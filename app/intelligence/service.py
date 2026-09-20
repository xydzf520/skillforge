from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
from typing import Any

import httpx
from loguru import logger
from sqlalchemy import or_, select

from app import database as _db_mod
from app.common.ai import get_ai_config, redact_secret_text
from app.common.cost_tracker import UsageEvent, cost_tracker
from app.common.exceptions import AppError
from app.common.models import (
    IntelligenceAnalyzeCache,
    IntelligenceAnalyzeRun,
    SystemConfig,
)
from app.common.time_utils import now_bjt
from app.execution.model_context import (
    _deployment_rollout_bucket,
    _deployment_rollout_percent,
    deployment_candidates_for_skill,
)
from app.execution.execution_service import verify_run_token
from app.execution.models import ExecutionRun
from app.intelligence.schemas import AnalyzeRequest
from app.skills.core.models import Skill


DEFAULT_ALLOWED_MODELS = ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat"]
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_MAX_INPUT_TOKENS = 1_000_000
DEFAULT_MAX_OUTPUT_TOKENS = 16_384
DEFAULT_TIMEOUT_SECONDS = 120
ANALYSIS_AGENT_PURPOSES = {"analysis", "mixed"}
TRAINING_DEPLOYMENT_MODEL_READY_STATUSES = {"ready", "prepared", "available", "completed", "succeeded"}


@dataclass
class LLMAnalyzeResult:
    output: dict
    usage: dict
    raw_output: str | None = None
    backend: str = "platform"
    agent_id: str | None = None
    model_key: str | None = None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _strip_code_fences(text: str) -> str:
    text = str(text or "").strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl >= 0 else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = _strip_code_fences(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(cleaned[start:end + 1])
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _config_value(raw: Any, default: Any = None) -> Any:
    if isinstance(raw, dict) and "value" in raw:
        return raw.get("value")
    return default if raw is None else raw


async def _load_intelligence_config() -> dict:
    cfg: dict[str, Any] = {
        "allowed_models": DEFAULT_ALLOWED_MODELS,
        "default_model": DEFAULT_MODEL,
        "max_input_tokens_per_run": DEFAULT_MAX_INPUT_TOKENS,
        "max_output_tokens_per_run": DEFAULT_MAX_OUTPUT_TOKENS,
        "timeout": DEFAULT_TIMEOUT_SECONDS,
    }
    try:
        async with _db_mod.async_session_factory() as session:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key.like("intelligence.%"))
            )
            for row in result.scalars().all():
                key = row.key.removeprefix("intelligence.")
                value = _config_value(row.value)
                if key == "allowed_models":
                    if isinstance(value, list):
                        cfg["allowed_models"] = [str(item) for item in value]
                    elif isinstance(value, dict) and isinstance(value.get("models"), list):
                        cfg["allowed_models"] = [str(item) for item in value["models"]]
                elif key in {"default_model", "fallback_model"}:
                    cfg[key] = str(value)
                elif key in {"max_input_tokens_per_run", "max_output_tokens_per_run", "timeout"}:
                    cfg[key] = int(value)
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取 intelligence 配置失败，使用默认值: {}", exc)
    return cfg


def _estimate_tokens(prompt: str, context_json: str) -> int:
    # 粗略预算门禁：中文和 JSON 混排按约 4 chars/token 估算，保守加上 prompt。
    return max(1, (len(prompt) + len(context_json) + 3) // 4)


def _data_health_ratio(context_pack: dict) -> Decimal | None:
    proofs = context_pack.get("proofs")
    if not isinstance(proofs, list) or not proofs:
        return None
    healthy = 0
    for item in proofs:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or item.get("state") or "").lower()
        if status in {"ok", "success", "200", "mcp"} or item.get("success") is True:
            healthy += 1
    return Decimal(str(round(healthy / max(len(proofs), 1), 4)))


def _usage_dict(usage: dict) -> dict:
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    prompt_tokens = _safe_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion_tokens = _safe_int(usage.get("completion_tokens") or usage.get("output_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens")) or prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _prompt_path(prompt_version: str) -> str:
    return f"prompts/{prompt_version}.md"


def _agent_model_key(instance_id: str) -> str:
    safe = "".join(ch for ch in instance_id if ch.isalnum() or ch in {"-", "_"})[:32]
    if safe:
        return f"agent:{safe}"[:50]
    return f"agent:{_sha256_text(instance_id)[:16]}"


def _serialize_active_model_deployment(
    deployment: Any,
    *,
    rollout_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_ref = getattr(deployment, "artifact_ref_json", None)
    artifact_ref = artifact_ref if isinstance(artifact_ref, dict) else {}
    payload = {
        "id": getattr(deployment, "id", None),
        "job_id": getattr(deployment, "job_id", None),
        "department": getattr(deployment, "department", None),
        "model_family": getattr(deployment, "model_family", None),
        "artifact_id": str(artifact_ref.get("id") or getattr(deployment, "artifact_id", "") or ""),
        "artifact_uri": str(artifact_ref.get("uri") or ""),
        "artifact_sha256": str(artifact_ref.get("sha256") or ""),
        "deployment_target_gateway_id": str(getattr(deployment, "deployment_target_gateway_id", "") or "") or None,
        "deployment_runtime_profile": str(getattr(deployment, "deployment_runtime_profile", "") or "") or None,
        "status": getattr(deployment, "status", None),
        "rollout_percent": _deployment_rollout_percent(deployment),
        "target_skill_ids": list(getattr(deployment, "target_skill_ids_json", None) or []),
        "activated_at": getattr(deployment, "activated_at", None).isoformat()
        if getattr(deployment, "activated_at", None)
        else None,
    }
    if rollout_decision:
        payload.update(
            {
                "rollout_selected": bool(rollout_decision.get("rollout_selected")),
                "rollout_bucket": rollout_decision.get("rollout_bucket"),
                "rollout_reason": rollout_decision.get("rollout_reason"),
            }
        )
    return payload


def _skipped_model_deployment_summary(
    deployment: dict[str, Any],
    *,
    runtime_status: dict[str, Any],
) -> dict[str, Any]:
    summary = {
        "id": deployment.get("id"),
        "job_id": deployment.get("job_id"),
        "department": deployment.get("department"),
        "model_family": deployment.get("model_family"),
        "artifact_id": deployment.get("artifact_id"),
        "artifact_sha256": deployment.get("artifact_sha256"),
        "status": deployment.get("status"),
        "rollout_percent": deployment.get("rollout_percent"),
        "rollout_selected": deployment.get("rollout_selected"),
        "rollout_bucket": deployment.get("rollout_bucket"),
        "rollout_reason": deployment.get("rollout_reason"),
        "runtime_status": runtime_status,
        "skip_reason": runtime_status.get("inference_disabled_reason") or "inference_not_ready",
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


def _model_runtime_status_summary(runtime_status: Any) -> dict[str, Any] | None:
    if not isinstance(runtime_status, dict):
        return None
    model_status = runtime_status.get("model_status") if isinstance(runtime_status.get("model_status"), dict) else {}
    summary = {
        "artifact_uri_present": runtime_status.get("artifact_uri_present"),
        "target_gateway_id": str(runtime_status.get("target_gateway_id") or "")[:100] or None,
        "target_gateway_kind": str(runtime_status.get("target_gateway_kind") or "")[:50] or None,
        "model_profile": str(runtime_status.get("model_profile") or "")[:80] or None,
        "model_ready": runtime_status.get("model_ready"),
        "inference_ready": runtime_status.get("inference_ready"),
        "inference_disabled_reason": (
            str(runtime_status.get("inference_disabled_reason") or "")[:120]
            if runtime_status.get("inference_disabled_reason")
            else None
        ),
        "model_status": {
            key: model_status.get(key)
            for key in ("status", "profile", "exists", "bridge_instance_id")
            if model_status.get(key) not in (None, "", [], {})
        },
        "raw_payload_returned": False,
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


async def _deployment_runtime_status(session: Any, deployment: dict[str, Any]) -> dict[str, Any]:
    gateway_id, gateway_kind = await _resolve_deployment_gateway(session, deployment)
    artifact_uri_present = bool(str(deployment.get("artifact_uri") or "").strip())
    disabled_reason = None
    if not artifact_uri_present:
        disabled_reason = "artifact_uri_missing"
    elif not gateway_id:
        disabled_reason = "target_gateway_missing"
    elif not gateway_kind:
        disabled_reason = "training_inference_op_unavailable"
    model_profile = _inference_profile_from_deployment(deployment)
    model_status: dict[str, Any] | None = None
    model_ready = False
    if disabled_reason is None:
        model_status = await _deployment_node_model_status(gateway_id, gateway_kind, model_profile)
        model_ready = str(model_status.get("status") or "").lower() in TRAINING_DEPLOYMENT_MODEL_READY_STATUSES
        if not model_ready:
            disabled_reason = "training_model_not_ready"
    return {
        "artifact_uri_present": artifact_uri_present,
        "target_gateway_id": gateway_id,
        "target_gateway_kind": gateway_kind,
        "model_profile": model_profile,
        "model_status": model_status,
        "model_ready": model_ready,
        "inference_ready": disabled_reason is None,
        "inference_disabled_reason": disabled_reason,
    }


async def _resolve_active_model_deployment(
    session: Any,
    skill: Skill,
    *,
    run_id: str | None = None,
    skipped: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    skill_department = str(skill.department or "")
    candidates = await deployment_candidates_for_skill(
        session,
        skill_id=skill.id,
        skill_department=skill_department,
        run_id=run_id,
    )
    if not candidates:
        return None
    for deployment, rollout_decision in candidates:
        serialized = _serialize_active_model_deployment(deployment, rollout_decision=rollout_decision)
        runtime_status = await _deployment_runtime_status(session, serialized)
        runtime_summary = _model_runtime_status_summary(runtime_status) or {}
        serialized["runtime_status"] = runtime_summary
        if runtime_status.get("inference_ready") is True:
            return serialized
        if skipped is not None:
            skipped.append(
                _skipped_model_deployment_summary(
                    serialized,
                    runtime_status=runtime_summary,
                )
            )
    return None


def _model_from_profile(profile: Any, cfg: dict[str, Any]) -> str | None:
    value = str(profile or "").strip().lower().replace("_", "-")
    if not value or value == "default":
        return None
    allowed = {str(item) for item in (cfg.get("allowed_models") or [])}
    if value in {"flash", "v4f", "deepseek-v4-flash"}:
        model = "deepseek-v4-flash"
        return model if model in allowed else None
    if value in {"pro", "v4-pro", "deepseek-v4-pro"}:
        model = "deepseek-v4-pro"
        return model if model in allowed else None
    if value in {"cheap", "deepseek-chat"}:
        model = "deepseek-chat"
        return model if model in allowed else None
    return value if value in allowed else None


async def _resolve_analysis_agent_model(
    session: Any,
    *,
    body: AnalyzeRequest,
    skill: Skill,
    cfg: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    from app.agents.models import DepartmentAnalysisAgent

    agent_id = ""
    run = await session.get(ExecutionRun, body.run_id)
    metadata = run.metadata_json if run and isinstance(run.metadata_json, dict) else {}
    control = metadata.get("analysis_agent_control") if isinstance(metadata.get("analysis_agent_control"), dict) else {}
    if control:
        agent_id = str(control.get("id") or "").strip()
    if not agent_id:
        skillforge_meta = body.context_pack.get("skillforge") if isinstance(body.context_pack.get("skillforge"), dict) else {}
        context_agent = skillforge_meta.get("analysis_agent") if isinstance(skillforge_meta.get("analysis_agent"), dict) else {}
        agent_id = str(context_agent.get("id") or "").strip()
    if not agent_id:
        return None, None

    row = await session.get(DepartmentAnalysisAgent, agent_id)
    if not row or not row.is_active or row.status != "active" or row.skill_id != skill.id:
        return None, None
    params = row.default_params_json if isinstance(row.default_params_json, dict) else {}
    model = _model_from_profile(params.get("model_profile"), cfg)
    if not model:
        return None, {
            "id": row.id,
            "name": row.name,
            "model_profile": params.get("model_profile") or "default",
            "model_profile_resolved": False,
        }
    return model, {
        "id": row.id,
        "name": row.name,
        "model_profile": params.get("model_profile") or "default",
        "model": model,
        "model_profile_resolved": True,
    }


def _with_active_model_deployment(context_pack: dict, deployment: dict[str, Any] | None) -> dict:
    if not deployment:
        return context_pack
    enriched = dict(context_pack or {})
    skillforge_meta = enriched.get("skillforge")
    skillforge_meta = dict(skillforge_meta) if isinstance(skillforge_meta, dict) else {}
    skillforge_meta["active_model_deployment"] = deployment
    enriched["skillforge"] = skillforge_meta
    return enriched


def _with_deployed_model_inference(context_pack: dict, inference: dict[str, Any] | None) -> dict:
    if not inference:
        return context_pack
    enriched = dict(context_pack or {})
    skillforge_meta = enriched.get("skillforge")
    skillforge_meta = dict(skillforge_meta) if isinstance(skillforge_meta, dict) else {}
    skillforge_meta["deployed_model_inference"] = inference
    enriched["skillforge"] = skillforge_meta
    return enriched


def _inference_profile_from_deployment(deployment: dict[str, Any]) -> str:
    model_family = str(deployment.get("model_family") or "").lower()
    runtime_profile = str(deployment.get("deployment_runtime_profile") or "").lower()
    if ("qwen3.6" in model_family or "qwen3.6" in runtime_profile) and (
        "35b" in model_family or "35b" in runtime_profile
    ):
        return "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    if "qwen3.5" in model_family and "4b" in model_family:
        return "qwen3.5-4b"
    return "qwen3.5-4b"


async def _deployment_node_model_status(
    gateway_id: str | None,
    gateway_kind: str | None,
    profile: str,
) -> dict[str, Any]:
    if not gateway_id:
        return {"status": "unavailable", "reason": "target_gateway_missing", "profile": profile}
    from app.aiclaw.bridge_registry import bridge_registry

    if not bridge_registry.is_online(gateway_id):
        return {"status": "unavailable", "reason": "bridge_offline", "profile": profile}
    from app.aiclaw.client import AIClawClient

    try:
        result = await AIClawClient(gateway_id, gateway_kind=gateway_kind).get_training_model_status(
            {"profile": profile},
            timeout=30,
        )
    except AppError as exc:
        return {
            "status": "unavailable",
            "reason": exc.code,
            "profile": profile,
            "detail": exc.detail or {},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unavailable",
            "reason": "model_status_failed",
            "profile": profile,
            "detail": redact_secret_text(exc, limit=1000),
        }
    if not isinstance(result, dict):
        return {"status": "unknown", "profile": profile}
    safe = dict(result)
    safe["profile"] = str(safe.get("profile") or profile)[:80]
    return safe


async def _resolve_deployment_gateway(session: Any, deployment: dict[str, Any]) -> tuple[str | None, str | None]:
    from app.training.models import TrainingJob
    from app.execution.models import OpenClawInstance

    job_id = str(deployment.get("job_id") or "").strip()
    if not job_id:
        return None, None
    job = await session.get(TrainingJob, job_id)
    gateway_id = str(deployment.get("deployment_target_gateway_id") or "").strip()
    if not gateway_id:
        gateway_id = str(getattr(job, "target_gateway_id", "") or "").strip()
    if not gateway_id:
        return None, None
    instance = await session.get(OpenClawInstance, gateway_id)
    if not instance or getattr(instance, "is_active", True) is False:
        return gateway_id, None
    try:
        cap = json.loads(getattr(instance, "bridge_capabilities_json", "") or "{}")
    except (TypeError, ValueError):
        cap = {}
    ops = {str(item or "").strip() for item in (cap.get("ops") or [])} if isinstance(cap, dict) else set()
    if "training.inference" not in ops:
        return gateway_id, None
    return gateway_id, getattr(instance, "bridge_gateway_kind", None)


async def _run_deployed_model_inference(
    session: Any,
    *,
    deployment: dict[str, Any] | None,
    body: AnalyzeRequest,
    prompt: str,
    context_pack: dict,
    timeout: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not deployment:
        return None, None
    artifact_uri = str((deployment.get("artifact_uri") or "")).strip()
    if not artifact_uri:
        return None, {"status": "skipped", "reason": "artifact_uri_missing"}
    gateway_id, gateway_kind = await _resolve_deployment_gateway(session, deployment)
    if not gateway_id:
        return None, {"status": "skipped", "reason": "target_gateway_missing"}
    if not gateway_kind:
        return None, {"status": "skipped", "reason": "training_inference_op_unavailable", "gateway_id": gateway_id}

    from app.aiclaw.client import AIClawClient

    prompt_text = (
        "请基于下面 SkillForge 分析任务给出用于业务判断的简短中文诊断，"
        "重点回答低消耗视频相比可用高消耗参照的差距、证据缺口和下一步优化动作。\n\n"
        f"### 系统提示\n{prompt[:4000]}\n\n"
        f"### 上下文\n{_json_dumps({'context_pack': context_pack})[:8000]}\n\n"
        "### 输出\n"
    )
    payload = {
        "deployment_id": deployment.get("id"),
        "profile": _inference_profile_from_deployment(deployment),
        "runtime_profile": deployment.get("deployment_runtime_profile"),
        "artifact_uri": artifact_uri,
        "artifact_sha256": deployment.get("artifact_sha256"),
        "prompt": prompt_text,
        "max_new_tokens": 128,
        "timeout_seconds": min(max(int(timeout or DEFAULT_TIMEOUT_SECONDS), 60), 1800),
    }
    result = await AIClawClient(gateway_id, gateway_kind=gateway_kind).run_training_inference(
        payload,
        timeout=int(payload["timeout_seconds"]),
    )
    text = str(result.get("text") or "").strip() if isinstance(result, dict) else ""
    inference = {
        "model_deployment_id": deployment.get("id"),
        "model_family": deployment.get("model_family"),
        "artifact_id": deployment.get("artifact_id"),
        "artifact_sha256": deployment.get("artifact_sha256"),
        "gateway_id": gateway_id,
        "backend": "bridge_local_lora_inference",
        "text": text[:4000],
        "text_sha256": result.get("text_sha256") if isinstance(result, dict) else _sha256_text(text),
        "metrics": result.get("metrics") if isinstance(result, dict) else {},
    }
    route = {
        "status": "used",
        "gateway_id": gateway_id,
        "backend": "bridge_local_lora_inference",
        "profile": payload["profile"],
        "text_sha256": inference["text_sha256"],
        "metrics": inference["metrics"],
    }
    return inference, route


async def _load_prompt(skill_id: str, commit: str, prompt_version: str) -> tuple[str, str]:
    from app.skills.core.git_service import git_service

    path = _prompt_path(prompt_version)
    prompt = git_service.get_file_at_commit(skill_id, path, commit)
    if not prompt:
        raise AppError(
            "PROMPT_VERSION_NOT_FOUND",
            404,
            {"skill_id": skill_id, "prompt_version": prompt_version, "commit": commit},
        )
    return prompt, f"{commit}:{path}"


async def _require_platform_model_pricing(model: str, allowed: set[str]) -> Any:
    if model not in allowed:
        raise AppError("MODEL_NOT_ALLOWED", 400, {"model": model, "allowed_models": sorted(allowed)})
    pricing = await cost_tracker.pricing_for_model(model, require_config=True)
    if pricing is None:
        raise AppError("MODEL_NOT_ALLOWED", 400, {"model": model, "reason": "pricing_not_configured"})
    return pricing


async def _call_llm_json(
    *,
    model: str,
    system_prompt: str,
    context_pack: dict,
    max_output_tokens: int,
    timeout: int,
) -> LLMAnalyzeResult:
    config = await get_ai_config()
    api_base = str(config["ai.api_base"]).rstrip("/")
    api_key = str(config.get("ai.api_key", ""))
    url = f"{api_base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    user_content = _json_dumps({"context_pack": context_pack})
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_output_tokens,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        raise AppError("LLM_API_ERROR", 502, {"status_code": resp.status_code, "body": resp.text[:500]})
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    try:
        output = _extract_json_object(content)
    except json.JSONDecodeError as exc:
        retry_payload = dict(payload)
        retry_payload["messages"] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
            {
                "role": "user",
                "content": "上一次返回不是合法 JSON。请只返回一个 JSON object，不要 Markdown，不要代码围栏，不要解释。",
            },
        ]
        async with httpx.AsyncClient(timeout=timeout) as client:
            retry_resp = await client.post(url, headers=headers, json=retry_payload)
        if retry_resp.status_code != 200:
            raise AppError("LLM_API_ERROR", 502, {"status_code": retry_resp.status_code, "body": retry_resp.text[:500]}) from exc
        retry_data = retry_resp.json()
        retry_content = retry_data["choices"][0]["message"]["content"]
        try:
            output = _extract_json_object(retry_content)
        except json.JSONDecodeError as retry_exc:
            raise AppError("LLM_API_ERROR", 502, {"detail": "LLM 返回非 JSON"}) from retry_exc
        usage = _usage_dict(data.get("usage") or {})
        retry_usage = _usage_dict(retry_data.get("usage") or {})
        return LLMAnalyzeResult(
            output=output,
            usage={
                "prompt_tokens": usage["prompt_tokens"] + retry_usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"] + retry_usage["completion_tokens"],
                "total_tokens": usage["total_tokens"] + retry_usage["total_tokens"],
            },
            raw_output=retry_content,
        )
    return LLMAnalyzeResult(output=output, usage=_usage_dict(data.get("usage") or {}), raw_output=content)


def _agent_can_analyze(instance: Any | None) -> bool:
    if instance is None:
        return False
    if getattr(instance, "is_active", True) is False:
        return False
    try:
        cap = json.loads(getattr(instance, "bridge_capabilities_json", "") or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(cap, dict):
        return False
    ops = {str(item or "").strip() for item in (cap.get("ops") or [])}
    return "intelligence.analyze" in ops


def _agent_purpose(instance: Any | None) -> str:
    value = str(getattr(instance, "agent_purpose", "") or "skill_runtime").strip().lower()
    return value if value in {"skill_runtime", "analysis", "training", "media", "mixed"} else "skill_runtime"


def _is_platform_analysis_agent(instance: Any | None) -> bool:
    if instance is None:
        return False
    return bool(getattr(instance, "is_platform_default", False)) and _agent_purpose(instance) in ANALYSIS_AGENT_PURPOSES


def _analysis_delegate_route(instance: Any, *, mode: str, scope: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "scope": scope,
        "agent_id": getattr(instance, "id", None),
        "agent_name": getattr(instance, "name", None) or getattr(instance, "id", None),
        "agent_department": getattr(instance, "department", None),
        "agent_purpose": _agent_purpose(instance),
        "platform_fallback": scope == "platform_fallback",
    }


def _agent_error_payload(exc: Exception) -> dict[str, Any]:
    detail = getattr(exc, "detail", None)
    payload: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": redact_secret_text(str(exc), limit=500),
    }
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    if code:
        payload["code"] = str(code)[:80]
    if status:
        payload["status"] = int(status)
    if detail is not None:
        payload["detail"] = redact_secret_text(_json_dumps(detail), limit=1000)
    return payload


def _analysis_agent_rank(instance: Any, *, department: str | None) -> tuple[Any, ...]:
    try:
        from app.aiclaw.bridge_registry import bridge_registry

        online = bridge_registry.is_online(getattr(instance, "id", ""))
    except Exception:  # noqa: BLE001
        online = False
    instance_department = str(getattr(instance, "department", "") or "")
    scope_rank = (
        0
        if department and instance_department == department
        else (1 if _is_platform_analysis_agent(instance) else 2)
    )
    purpose = _agent_purpose(instance)
    purpose_rank = 0 if purpose == "analysis" else 1
    heartbeat = getattr(instance, "last_heartbeat", None)
    heartbeat_rank = -int(heartbeat.timestamp()) if heartbeat and hasattr(heartbeat, "timestamp") else 0
    return (
        scope_rank,
        0 if online else 1,
        purpose_rank,
        heartbeat_rank,
        str(getattr(instance, "name", "") or getattr(instance, "id", "")),
    )


async def _resolve_agent_delegate(
    instance_id: str | None,
    *,
    department: str | None = None,
    preferred_instance_id: str | None = None,
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    from app.execution.models import OpenClawInstance

    async with _db_mod.async_session_factory() as session:
        if instance_id:
            instance = await session.get(OpenClawInstance, instance_id)
            if _agent_can_analyze(instance) and _agent_purpose(instance) in ANALYSIS_AGENT_PURPOSES:
                scope = (
                    "department"
                    if department and getattr(instance, "department", None) == department
                    else "execution_instance"
                )
                return (
                    instance.id,
                    getattr(instance, "bridge_gateway_kind", None),
                    _analysis_delegate_route(instance, mode="current_instance", scope=scope),
                )

        if preferred_instance_id and preferred_instance_id != instance_id:
            instance = await session.get(OpenClawInstance, preferred_instance_id)
            if _agent_can_analyze(instance):
                route = _analysis_delegate_route(
                    instance,
                    mode="active_model_deployment_gateway",
                    scope="model_deployment_gateway",
                )
                route["preferred_by"] = "active_model_deployment"
                route["skill_department"] = department
                return (
                    instance.id,
                    getattr(instance, "bridge_gateway_kind", None),
                    route,
                )

        stmt = (
            select(OpenClawInstance)
            .where(OpenClawInstance.is_active.is_(True))
            .where(
                or_(
                    OpenClawInstance.department == department,
                    OpenClawInstance.is_platform_default.is_(True),
                )
            )
            .order_by(OpenClawInstance.is_platform_default.asc(), OpenClawInstance.name.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        candidates = []
        for item in rows:
            if not _agent_can_analyze(item):
                continue
            same_department = bool(department and getattr(item, "department", None) == department)
            if not same_department and not _is_platform_analysis_agent(item):
                continue
            purpose = _agent_purpose(item)
            if same_department and purpose not in ANALYSIS_AGENT_PURPOSES:
                continue
            scope = "department" if same_department else "platform_fallback"
            candidates.append((_analysis_agent_rank(item, department=department), item, scope))
        if not candidates:
            return None, None, None
        candidates.sort(key=lambda item: item[0])
        selected = candidates[0][1]
        scope = candidates[0][2]
        return (
            selected.id,
            getattr(selected, "bridge_gateway_kind", None),
            _analysis_delegate_route(selected, mode="auto", scope=scope),
        )


async def _call_agent_analyze(
    *,
    instance_id: str,
    gateway_kind: str | None,
    model: str,
    prompt: str,
    prompt_git_ref: str,
    body: AnalyzeRequest,
    max_output_tokens: int,
    timeout: int,
    delegate_route: dict[str, Any] | None = None,
) -> LLMAnalyzeResult:
    from app.aiclaw.client import AIClawClient

    result = await AIClawClient(instance_id, gateway_kind=gateway_kind).run_intelligence_analyze(
        {
            "skill_id": body.skill_id,
            "run_id": body.run_id,
            "instance_id": body.instance_id,
            "skill_git_commit_full": body.skill_git_commit_full,
            "prompt_version": body.prompt_version,
            "prompt_git_ref": prompt_git_ref,
            "system_prompt": prompt,
            "context_pack": body.context_pack,
            "model": model,
            "max_output_tokens": max_output_tokens,
            "timeout": timeout,
            "analysis_delegate_route": delegate_route or {},
        },
        timeout=timeout,
    )
    output = result.get("output") if isinstance(result, dict) else None
    if not isinstance(output, dict):
        output = {"value": output} if output is not None else {}
    usage = _usage_dict(result.get("usage") or {}) if isinstance(result, dict) else _usage_dict({})
    raw_output = result.get("raw_output") if isinstance(result, dict) else None
    return LLMAnalyzeResult(
        output=output,
        usage=usage,
        raw_output=str(raw_output) if raw_output is not None else None,
        backend="agent",
        agent_id=instance_id,
        model_key=_agent_model_key(instance_id),
    )


async def analyze_context(body: AnalyzeRequest, *, run_token: str) -> dict:
    claims = verify_run_token(
        run_token,
        skill_id=body.skill_id,
        run_id=body.run_id,
        instance_id=body.instance_id,
        skill_git_commit_full=body.skill_git_commit_full,
        require_intelligence=True,
    )

    cfg = await _load_intelligence_config()
    model = str(cfg.get("default_model") or DEFAULT_MODEL)
    allowed = set(str(item) for item in (cfg.get("allowed_models") or []))
    pricing: Any | None = None

    async with _db_mod.async_session_factory() as session:
        skill = await session.get(Skill, body.skill_id)
        if not skill:
            raise AppError("SKILL_NOT_FOUND", 404)
        department = skill.department or claims.get("skill_department") or claims.get("department")
        skipped_model_deployments: list[dict[str, Any]] = []
        active_model_deployment = await _resolve_active_model_deployment(
            session,
            skill,
            run_id=body.run_id,
            skipped=skipped_model_deployments,
        )
        analysis_agent_model, analysis_agent_model_meta = await _resolve_analysis_agent_model(
            session,
            body=body,
            skill=skill,
            cfg=cfg,
        )
        if analysis_agent_model:
            model = analysis_agent_model

    body = body.model_copy(
        update={
            "context_pack": _with_active_model_deployment(
                body.context_pack,
                active_model_deployment,
            )
        }
    )

    deployed_model_inference: dict[str, Any] | None = None
    deployed_model_inference_route: dict[str, Any] | None = None
    preferred_agent_id: str | None = None
    if active_model_deployment:
        try:
            async with _db_mod.async_session_factory() as session:
                preferred_agent_id, _preferred_gateway_kind = await _resolve_deployment_gateway(
                    session,
                    active_model_deployment,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Resolve deployment analysis gateway failed deployment={} run={}: {}",
                active_model_deployment.get("id"),
                body.run_id,
                exc,
            )

    agent_id, agent_gateway_kind, agent_route = await _resolve_agent_delegate(
        body.instance_id,
        department=str(department) if department else None,
        preferred_instance_id=preferred_agent_id,
    )
    if not agent_route:
        agent_route = {"mode": "platform", "scope": "platform_llm", "platform_fallback": False}
    if active_model_deployment:
        agent_route = dict(agent_route)
        agent_route["active_model_deployment"] = active_model_deployment
    if skipped_model_deployments:
        agent_route = dict(agent_route)
        agent_route["skipped_model_deployments"] = skipped_model_deployments
    if deployed_model_inference_route:
        agent_route = dict(agent_route)
        agent_route["deployed_model_inference"] = deployed_model_inference_route
    if analysis_agent_model_meta:
        agent_route = dict(agent_route)
        agent_route["analysis_agent_model"] = analysis_agent_model_meta
    if not agent_id:
        pricing = await _require_platform_model_pricing(model, allowed)

    prompt, prompt_git_ref = await _load_prompt(
        body.skill_id,
        body.skill_git_commit_full,
        body.prompt_version,
    )
    if active_model_deployment:
        try:
            async with _db_mod.async_session_factory() as session:
                deployed_model_inference, deployed_model_inference_route = await _run_deployed_model_inference(
                    session,
                    deployment=active_model_deployment,
                    body=body,
                    prompt=prompt,
                    context_pack=body.context_pack,
                    timeout=int(cfg.get("timeout") or DEFAULT_TIMEOUT_SECONDS),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Deployed model inference failed deployment={} run={}: {}",
                active_model_deployment.get("id"),
                body.run_id,
                exc,
            )
            deployed_model_inference_route = {
                "status": "failed",
                "reason": "deployed_model_inference_failed",
                "error": str(exc)[:500],
            }
        if deployed_model_inference:
            body = body.model_copy(
                update={
                    "context_pack": _with_deployed_model_inference(
                        body.context_pack,
                        deployed_model_inference,
                    )
                }
            )
        if deployed_model_inference_route:
            agent_route = dict(agent_route)
            agent_route["deployed_model_inference"] = deployed_model_inference_route

    context_json = _json_dumps(body.context_pack)
    prompt_hash = _sha256_text(prompt)
    context_hash = _sha256_text(context_json)
    cache_key = body.cache_key or _sha256_text(
        f"{body.skill_id}|{body.run_id}|{prompt_hash}|{context_hash}"
    )
    cache_model = _agent_model_key(agent_id) if agent_id else model
    input_tokens_estimate = _estimate_tokens(prompt, context_json)
    if input_tokens_estimate > int(cfg.get("max_input_tokens_per_run") or DEFAULT_MAX_INPUT_TOKENS):
        raise AppError(
            "BUDGET_EXCEEDED",
            402,
            {"estimated_input_tokens": input_tokens_estimate, "limit": cfg.get("max_input_tokens_per_run")},
        )

    started = monotonic()
    async with _db_mod.async_session_factory() as session:
        cached = (
            await session.execute(
                select(IntelligenceAnalyzeCache)
                .where(IntelligenceAnalyzeCache.cache_key == cache_key)
                .where(IntelligenceAnalyzeCache.model == cache_model)
                .where(IntelligenceAnalyzeCache.prompt_hash == prompt_hash)
                .where(IntelligenceAnalyzeCache.context_hash == context_hash)
                .limit(1)
            )
        ).scalar_one_or_none()
        if cached:
            cached.hit_count = int(cached.hit_count or 0) + 1
            cached.last_hit_at = now_bjt()
            usage = _usage_dict(
                {
                    "prompt_tokens": cached.prompt_tokens or 0,
                    "completion_tokens": cached.completion_tokens or 0,
                    "total_tokens": cached.total_tokens or 0,
                }
            )
            run = IntelligenceAnalyzeRun(
                cache_key=cache_key,
                cache_id=cached.id,
                cache_hit=True,
                cache_hit_of_run_id=cached.first_seen_run_id,
                skill_id=body.skill_id,
                skill_git_commit_full=body.skill_git_commit_full,
                prompt_git_ref=prompt_git_ref,
                run_id=body.run_id,
                instance_id=body.instance_id,
                department=department,
                model=cache_model,
                analysis_backend="agent" if agent_id else "platform",
                analysis_agent_id=agent_id,
                analysis_delegate_route=agent_route,
                prompt_version=body.prompt_version,
                prompt_hash=prompt_hash,
                context_hash=context_hash,
                data_health_ratio=_data_health_ratio(body.context_pack),
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                total_tokens=usage["total_tokens"],
                cost_usd=Decimal("0"),
                duration_ms=int((monotonic() - started) * 1000),
                degraded=False,
                output_hash=cached.output_hash,
                evidence_passed=cached.evidence_passed,
            )
            session.add(run)
            await session.flush()
            from app.learning.service import capture_intelligence_analyze_run

            await capture_intelligence_analyze_run(
                session,
                run,
                output_payload=cached.output_payload if isinstance(cached.output_payload, dict) else None,
            )
            await session.commit()
            return {
                "output": cached.output_payload,
                "model": cache_model,
                "usage": usage,
                "cache_hit": True,
                "cache_id": cached.id,
                "cache_hit_of_run_id": cached.first_seen_run_id,
                "cost_usd": 0.0,
                "degraded": False,
                "analysis_backend": "agent" if agent_id else "platform",
                "analysis_agent_id": agent_id,
                "analysis_delegate_route": agent_route,
                "active_model_deployment": active_model_deployment,
                "skipped_model_deployments": skipped_model_deployments,
                "deployed_model_inference": deployed_model_inference_route,
                "analysis_agent_model": analysis_agent_model_meta,
            }

    degraded = False
    degraded_reason: str | None = None
    if agent_id:
        try:
            llm = await _call_agent_analyze(
                instance_id=agent_id,
                gateway_kind=agent_gateway_kind,
                model=model,
                prompt=prompt,
                prompt_git_ref=prompt_git_ref,
                body=body,
                max_output_tokens=int(cfg.get("max_output_tokens_per_run") or DEFAULT_MAX_OUTPUT_TOKENS),
                timeout=int(cfg.get("timeout") or DEFAULT_TIMEOUT_SECONDS),
                delegate_route=agent_route,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Agent intelligence analyze failed instance={}: {}", agent_id, exc)
            degraded = True
            degraded_reason = "agent_delegate_failed"
            failed_agent_id = agent_id
            failed_agent_route = agent_route
            agent_id = None
            cache_model = model
            agent_route = {
                "mode": "platform_fallback_after_agent_failure",
                "scope": "platform_llm",
                "platform_fallback": True,
                "failed_agent_id": failed_agent_id,
                "failed_route": failed_agent_route,
                "agent_error": _agent_error_payload(exc),
            }
            if active_model_deployment:
                agent_route["active_model_deployment"] = active_model_deployment
            if skipped_model_deployments:
                agent_route["skipped_model_deployments"] = skipped_model_deployments
            if deployed_model_inference_route:
                agent_route["deployed_model_inference"] = deployed_model_inference_route
            if analysis_agent_model_meta:
                agent_route["analysis_agent_model"] = analysis_agent_model_meta
            pricing = pricing or await _require_platform_model_pricing(model, allowed)
            llm = await _call_llm_json(
                model=model,
                system_prompt=prompt,
                context_pack=body.context_pack,
                max_output_tokens=int(cfg.get("max_output_tokens_per_run") or DEFAULT_MAX_OUTPUT_TOKENS),
                timeout=int(cfg.get("timeout") or DEFAULT_TIMEOUT_SECONDS),
            )
    else:
        llm = await _call_llm_json(
            model=model,
            system_prompt=prompt,
            context_pack=body.context_pack,
            max_output_tokens=int(cfg.get("max_output_tokens_per_run") or DEFAULT_MAX_OUTPUT_TOKENS),
            timeout=int(cfg.get("timeout") or DEFAULT_TIMEOUT_SECONDS),
        )
    usage = _usage_dict(llm.usage)
    output_hash = _sha256_text(_json_dumps(llm.output))
    llm_output_hash = output_hash
    response_output = llm.output
    event = UsageEvent(
        skill_id=body.skill_id,
        department=department,
        call_source="intelligence_analyze_agent" if agent_id else "intelligence_analyze",
        model=cache_model if agent_id else model,
        input_tokens=usage["prompt_tokens"],
        output_tokens=usage["completion_tokens"],
        prompt_hash=prompt_hash,
        duration_ms=int((monotonic() - started) * 1000),
        metadata_json={
            "run_id": body.run_id,
            "instance_id": body.instance_id,
            "cache_key": cache_key,
            "prompt_version": body.prompt_version,
            "skill_git_commit_full": body.skill_git_commit_full,
            "cache_discount_usd": "0",
            "analysis_backend": "agent" if agent_id else "platform",
            "analysis_agent_id": agent_id,
            "analysis_delegate_route": agent_route,
            "active_model_deployment": active_model_deployment,
            "skipped_model_deployments": skipped_model_deployments,
            "deployed_model_inference": deployed_model_inference_route,
            "analysis_agent_model": analysis_agent_model_meta,
        },
    )
    cost = Decimal("0") if agent_id else cost_tracker.calc_cost_with_pricing(event, pricing)

    async with _db_mod.async_session_factory() as session:
        cache = IntelligenceAnalyzeCache(
            cache_key=cache_key,
            model=cache_model,
            prompt_hash=prompt_hash,
            context_hash=context_hash,
            output_hash=output_hash,
            output_payload=llm.output,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
            evidence_passed=True,
            first_seen_run_id=body.run_id,
            hit_count=0,
            last_hit_at=now_bjt(),
        )
        session.add(cache)
        try:
            await session.flush()
        except Exception:
            await session.rollback()
            async with _db_mod.async_session_factory() as lookup_session:
                cached = (
                    await lookup_session.execute(
                        select(IntelligenceAnalyzeCache)
                        .where(IntelligenceAnalyzeCache.cache_key == cache_key)
                        .where(IntelligenceAnalyzeCache.model == cache_model)
                        .where(IntelligenceAnalyzeCache.prompt_hash == prompt_hash)
                        .where(IntelligenceAnalyzeCache.context_hash == context_hash)
                        .limit(1)
                    )
                ).scalar_one()
                cache = cached
                output_hash = cached.output_hash
                response_output = cached.output_payload
                if llm_output_hash == cached.output_hash:
                    degraded_reason = "concurrent_cache_race_same_output"
                else:
                    degraded = True
                    degraded_reason = "concurrent_cache_race_diff_output"
        run = IntelligenceAnalyzeRun(
            cache_key=cache_key,
            cache_id=cache.id,
            cache_hit=False,
            skill_id=body.skill_id,
            skill_git_commit_full=body.skill_git_commit_full,
            prompt_git_ref=prompt_git_ref,
            run_id=body.run_id,
            instance_id=body.instance_id,
            department=department,
            model=cache_model,
            analysis_backend="agent" if agent_id else "platform",
            analysis_agent_id=agent_id,
            analysis_delegate_route=agent_route,
            prompt_version=body.prompt_version,
            prompt_hash=prompt_hash,
            context_hash=context_hash,
            data_health_ratio=_data_health_ratio(body.context_pack),
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
            cost_usd=cost,
            duration_ms=int((monotonic() - started) * 1000),
            degraded=degraded,
            degraded_reason=degraded_reason,
            output_hash=output_hash,
            llm_output_hash=llm_output_hash,
            evidence_passed=True,
        )
        session.add(run)
        await session.flush()
        from app.learning.service import capture_intelligence_analyze_run

        await capture_intelligence_analyze_run(
            session,
            run,
            output_payload=response_output if isinstance(response_output, dict) else None,
        )
        await session.commit()

    if not agent_id:
        await cost_tracker.record(event)
    return {
        "output": response_output,
        "model": cache_model,
        "usage": usage,
        "cache_hit": False,
        "cache_id": cache.id,
        "cache_hit_of_run_id": None,
        "cost_usd": float(cost),
        "degraded": degraded,
        "degraded_reason": degraded_reason,
        "analysis_backend": "agent" if agent_id else "platform",
        "analysis_agent_id": agent_id,
        "analysis_delegate_route": agent_route,
        "active_model_deployment": active_model_deployment,
        "skipped_model_deployments": skipped_model_deployments,
        "deployed_model_inference": deployed_model_inference_route,
        "analysis_agent_model": analysis_agent_model_meta,
    }
