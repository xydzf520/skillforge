"""Post-training model evaluation for inbox todo outputs."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aiclaw.client import AIClawClient
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.execution.model_context import active_model_context_for_skill
from app.execution.models import OpenClawInstance
from app.skills.core.models import Skill
from app.training.models import TrainingJob, TrainingModelDeployment


POST_TRAINING_EVALUATION_KEY = "post_training_model_evaluation"
POST_TRAINING_EVALUATED_SKILL_IDS = frozenset({
    "tmall-link-decline-operator-v1",
    "samplebrand-video-low-consumption-operator-v1",
})
POST_TRAINING_EVALUATION_TIMEOUT_SECONDS = 45
POST_TRAINING_EVALUATION_MAX_NEW_TOKENS = 768
POST_TRAINING_EVALUATION_CONTEXT_LIMIT = 4200
POST_TRAINING_EVALUATION_MAX_PROMPT_CHARS = 5600
POST_TRAINING_SHARED_MODEL_MARKER = "platform-full-history"
_EVALUATION_SECTION_RE = re.compile(r"(?m)(?:^|\n)\s*(?:#+\s*)?(?:[一二三四五六七八九十0-9]+[.、]\s*)?(评估结论|关键依据|风险缺口|建议动作|审核建议)\s*[:：]?\s*")
_PAYLOAD_LIST_FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "analysis_basis": ("dimension", "basis", "data_gap", "confidence", "_confidence"),
    "analysis_pool_basis": ("dimension", "basis", "range", "ratio", "amount"),
    "data_quality_items": ("dimension", "issue", "severity", "source", "evidence", "confidence"),
    "data_audit_items": ("metric", "value", "status", "source", "evidence", "accuracy_status", "completeness_status"),
    "key_metrics": ("name", "value", "delta", "note", "source", "status", "period"),
    "key_evidence": ("dimension", "evidence", "value", "source", "issue"),
    "operation_actions": ("rank", "section", "action", "actions", "owner", "priority", "evidence", "scenario"),
    "top_actions": ("rank", "section", "action", "actions", "owner", "priority", "evidence", "scenario"),
    "root_cause_ranking": ("rank", "dimension", "role", "score", "evidence", "actions", "priority"),
    "risk_flags": ("code", "label", "reason", "severity", "evidence"),
    "forbidden_actions": ("action", "reason", "evidence"),
    "suggested": ("title", "action", "reason", "evidence"),
}


def should_evaluate_post_training_model(skill_id: str | None) -> bool:
    return str(skill_id or "").strip() in POST_TRAINING_EVALUATED_SKILL_IDS


def post_training_evaluation_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "status": value.get("status"),
        "model_deployment_id": value.get("model_deployment_id"),
        "model_family": value.get("model_family"),
        "evaluated_at": value.get("evaluated_at"),
        "reason": value.get("reason") or value.get("error"),
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_text(value: Any, *, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _truncate_json_value(
    value: Any,
    *,
    string_limit: int,
    list_limit: int,
    depth_limit: int,
    depth: int = 0,
) -> Any:
    if isinstance(value, str):
        return _clean_text(value, limit=string_limit)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= depth_limit:
        if isinstance(value, dict):
            return {"_truncated": True, "keys": list(value.keys())[:8]}
        if isinstance(value, list):
            return {"_truncated": True, "count": len(value)}
        return _clean_text(value, limit=string_limit)
    if isinstance(value, list):
        items = [
            _truncate_json_value(
                item,
                string_limit=max(80, int(string_limit * 0.75)),
                list_limit=list_limit,
                depth_limit=depth_limit,
                depth=depth + 1,
            )
            for item in value[:list_limit]
        ]
        if len(value) > list_limit:
            items.append({"_truncated": True, "remaining": len(value) - list_limit})
        return items
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 40:
                result["_truncated_keys"] = len(value) - index
                break
            result[str(key)] = _truncate_json_value(
                item,
                string_limit=max(80, int(string_limit * 0.8)),
                list_limit=list_limit,
                depth_limit=depth_limit,
                depth=depth + 1,
            )
        return result
    return _clean_text(value, limit=string_limit)


def _safe_json(value: Any, *, limit: int = POST_TRAINING_EVALUATION_CONTEXT_LIMIT) -> str:
    try:
        text = _json_dumps(value)
    except Exception:
        text = str(value)
    if len(text) <= limit:
        return text
    for string_limit, list_limit, depth_limit in (
        (700, 8, 5),
        (420, 6, 4),
        (240, 4, 4),
        (140, 3, 3),
        (90, 2, 3),
    ):
        try:
            compact = _truncate_json_value(
                value,
                string_limit=string_limit,
                list_limit=list_limit,
                depth_limit=depth_limit,
            )
            text = _json_dumps(compact)
        except Exception:
            continue
        if len(text) <= limit:
            return text
    return _json_dumps({
        "_truncated": True,
        "reason": "context_exceeds_prompt_limit",
        "preview": _clean_text(text, limit=max(80, limit - 100)),
    })


def _compact_structured_value(value: Any, *, limit: int = 220) -> Any:
    if isinstance(value, str):
        return _clean_text(value, limit=limit)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        return [_compact_structured_value(item, limit=max(80, int(limit * 0.7))) for item in value[:3]]
    if isinstance(value, dict):
        return {
            str(key): _compact_structured_value(item, limit=max(80, int(limit * 0.7)))
            for key, item in list(value.items())[:8]
            if item not in (None, "", [], {})
        }
    return _clean_text(value, limit=limit)


def _compact_dict_fields(item: Any, keys: tuple[str, ...], *, string_limit: int = 220) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"value": _compact_structured_value(item, limit=string_limit)}
    compact: dict[str, Any] = {}
    for key in keys:
        if key in item and item.get(key) not in (None, "", [], {}):
            compact[key] = _compact_structured_value(item.get(key), limit=string_limit)
    if not compact:
        for key, value in list(item.items())[:6]:
            if value not in (None, "", [], {}):
                compact[str(key)] = _compact_structured_value(value, limit=string_limit)
    return compact


def _compact_dict_list(
    value: Any,
    keys: tuple[str, ...],
    *,
    item_limit: int = 4,
    string_limit: int = 220,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items = [_compact_dict_fields(item, keys, string_limit=string_limit) for item in value[:item_limit]]
    if len(value) > item_limit:
        items.append({"_remaining": len(value) - item_limit})
    return items


def _compact_payload_value(key: str, value: Any) -> Any:
    if key in _PAYLOAD_LIST_FIELD_KEYS and isinstance(value, list):
        return _compact_dict_list(value, _PAYLOAD_LIST_FIELD_KEYS[key], item_limit=4, string_limit=260)
    if isinstance(value, dict):
        return _truncate_json_value(value, string_limit=260, list_limit=4, depth_limit=4)
    if isinstance(value, list):
        return [_compact_structured_value(item, limit=220) for item in value[:4]]
    return _compact_structured_value(value, limit=320)


def _compact_payload_for_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the evaluator context complete enough while fitting Bridge prompt limits."""
    if not isinstance(payload, dict):
        return {}
    excluded = {
        POST_TRAINING_EVALUATION_KEY,
        "detail_markdown",
        "chart_sections",
        "metric_sections",
        "analysis_logic",
        "operator_playbook_actions",
    }
    preferred = (
        "type",
        "card_type",
        "priority",
        "decision_type",
        "recommended_decision",
        "confidence",
        "business_action_allowed",
        "human_can_decide",
        "data_time",
        "analysis_date",
        "account_name",
        "person",
        "item_id",
        "object_id",
        "item_title",
        "object_title",
        "primary_indicator",
        "key_metrics",
        "key_evidence",
        "data_quality",
        "data_quality_items",
        "data_audit_summary",
        "data_audit_items",
        "risk_flags",
        "root_cause_ranking",
        "store_main_judgement",
        "analysis_basis",
        "analysis_pool_basis",
        "rank_basis",
        "priority_basis",
        "market_top300_status",
        "decline_amount_text",
        "decline_coef",
        "top_actions",
        "operation_actions",
        "forbidden_actions",
        "approval_question",
        "suggested",
    )
    compact: dict[str, Any] = {}
    for key in preferred:
        if key in payload and key not in excluded:
            compact[key] = _compact_payload_value(key, payload.get(key))
    output = payload.get("output")
    if isinstance(output, dict):
        compact["output"] = {
            key: _compact_payload_value(key, output.get(key))
            for key in (
                "summary",
                "decision",
                "recommended_decision",
                "confidence",
                "metrics",
                "actions",
                "todos",
                "risk_flags",
                "data_gaps",
            )
            if key in output
        }
    elif isinstance(output, str) and output.strip():
        compact["output"] = _clean_text(output, limit=900)
    for key, value in payload.items():
        if key in compact or key in excluded or key.startswith("_"):
            continue
        if len(compact) >= 34:
            break
        if isinstance(value, (str, int, float, bool)) or value is None:
            compact[key] = _compact_payload_value(key, value)
    return compact


def _compact_dispatch_tasks(tasks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    compact_tasks: list[dict[str, Any]] = []
    if not isinstance(tasks, list):
        return compact_tasks
    for task in tasks[:4]:
        if not isinstance(task, dict):
            continue
        compact_tasks.append({
            key: _compact_payload_value(key, task.get(key))
            for key in ("executor", "content", "deadline", "status", "extra")
            if key in task
        })
    return compact_tasks


def _slim_payload_for_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
    """Last-resort compact shape for very large todo cards."""
    if not isinstance(payload, dict):
        return {}
    keys = (
        "type",
        "card_type",
        "priority",
        "decision_type",
        "recommended_decision",
        "confidence",
        "decision_confidence",
        "business_action_allowed",
        "human_can_decide",
        "data_time",
        "analysis_date",
        "account_name",
        "person",
        "item_id",
        "object_id",
        "item_title",
        "object_title",
        "decline_amount_text",
        "decline_coef",
        "data_quality",
        "data_audit_summary",
        "market_top300_status",
        "primary_indicator",
        "analysis_pool_basis",
        "approval_question",
    )
    slim = {
        key: _compact_payload_value(key, payload.get(key))
        for key in keys
        if key in payload and payload.get(key) not in (None, "", [], {})
    }
    for key, item_limit, string_limit in (
        ("key_metrics", 3, 130),
        ("key_evidence", 2, 140),
        ("analysis_basis", 2, 140),
        ("data_quality_items", 2, 150),
        ("root_cause_ranking", 1, 140),
        ("operation_actions", 1, 150),
        ("top_actions", 1, 150),
        ("forbidden_actions", 2, 120),
        ("suggested", 1, 150),
        ("risk_flags", 2, 120),
    ):
        value = payload.get(key)
        if isinstance(value, list) and value:
            keys_for_field = _PAYLOAD_LIST_FIELD_KEYS.get(key)
            if keys_for_field:
                slim[key] = _compact_dict_list(
                    value,
                    keys_for_field,
                    item_limit=item_limit,
                    string_limit=string_limit,
                )
            else:
                slim[key] = [_compact_structured_value(item, limit=string_limit) for item in value[:item_limit]]
    output = payload.get("output")
    if isinstance(output, dict):
        slim["output"] = {
            key: _compact_payload_value(key, output.get(key))
            for key in ("summary", "decision", "recommended_decision", "confidence")
            if output.get(key) not in (None, "", [], {})
        }
    elif isinstance(output, str) and output.strip():
        slim["output"] = _clean_text(output, limit=500)
    return slim


def _slim_dispatch_tasks(tasks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not isinstance(tasks, list):
        return []
    slim_tasks: list[dict[str, Any]] = []
    for task in tasks[:2]:
        if not isinstance(task, dict):
            continue
        slim_task = {
            key: _compact_payload_value(key, task.get(key))
            for key in ("executor", "content", "deadline", "status")
            if task.get(key) not in (None, "", [], {})
        }
        extra = task.get("extra")
        if isinstance(extra, dict):
            slim_task["extra"] = {
                key: _compact_payload_value(key, extra.get(key))
                for key in ("required_output", "account_name", "person", "item_id", "object_id", "priority")
                if extra.get(key) not in (None, "", [], {})
            }
        slim_tasks.append(slim_task)
    return slim_tasks


def _micro_payload_for_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    micro_keys = (
        "type",
        "card_type",
        "priority",
        "decision_type",
        "recommended_decision",
        "confidence",
        "business_action_allowed",
        "human_can_decide",
        "data_time",
        "item_id",
        "object_id",
        "item_title",
        "object_title",
        "decline_amount_text",
        "decline_coef",
        "data_quality",
        "data_audit_summary",
        "market_top300_status",
        "primary_indicator",
        "approval_question",
    )
    micro = {
        key: _compact_payload_value(key, payload.get(key))
        for key in micro_keys
        if key in payload and payload.get(key) not in (None, "", [], {})
    }
    for key, item_limit, string_limit in (
        ("key_metrics", 2, 90),
        ("key_evidence", 2, 90),
        ("analysis_basis", 1, 100),
        ("data_quality_items", 2, 100),
        ("root_cause_ranking", 1, 100),
        ("operation_actions", 1, 100),
        ("forbidden_actions", 1, 90),
    ):
        value = payload.get(key)
        keys_for_field = _PAYLOAD_LIST_FIELD_KEYS.get(key)
        if isinstance(value, list) and keys_for_field:
            micro[key] = _compact_dict_list(
                value,
                keys_for_field,
                item_limit=item_limit,
                string_limit=string_limit,
            )
    return micro


def _micro_dispatch_tasks(tasks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not isinstance(tasks, list):
        return []
    result: list[dict[str, Any]] = []
    for task in tasks[:1]:
        if not isinstance(task, dict):
            continue
        result.append({
            key: _clean_text(task.get(key), limit=160) if key == "content" else _compact_payload_value(key, task.get(key))
            for key in ("content", "deadline", "status")
            if task.get(key) not in (None, "", [], {})
        })
    return result


def _json_for_prompt_material(material: dict[str, Any], *, limit: int = POST_TRAINING_EVALUATION_CONTEXT_LIMIT) -> str:
    text = _safe_json(material, limit=limit)
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    text_looks_degraded = (
        isinstance(parsed, dict)
        and (
            parsed.get("reason") == "context_exceeds_prompt_limit"
            or _json_dumps(parsed).count('"keys"') >= 8
        )
    )
    if not text_looks_degraded:
        return text
    slim = {
        "skill_id": material.get("skill_id"),
        "run_id": material.get("run_id"),
        "todo": material.get("todo"),
        "payload": _slim_payload_for_evaluation(material.get("payload") if isinstance(material.get("payload"), dict) else {}),
        "dispatch_tasks": _slim_dispatch_tasks(
            material.get("dispatch_tasks") if isinstance(material.get("dispatch_tasks"), list) else None
        ),
    }
    slim_text = _json_dumps(slim)
    if len(slim_text) <= limit and slim_text.count('"keys"') < 8:
        return slim_text
    micro = {
        "skill_id": material.get("skill_id"),
        "run_id": material.get("run_id"),
        "todo": _truncate_json_value(material.get("todo") or {}, string_limit=180, list_limit=2, depth_limit=3),
        "payload": _micro_payload_for_evaluation(slim.get("payload") if isinstance(slim.get("payload"), dict) else {}),
        "dispatch_tasks": _micro_dispatch_tasks(
            slim.get("dispatch_tasks") if isinstance(slim.get("dispatch_tasks"), list) else None
        ),
    }
    micro_text = _json_dumps(micro)
    if len(micro_text) <= limit:
        return micro_text
    return _safe_json(micro, limit=limit)


def _artifact_uri(artifact_ref: dict[str, Any]) -> str:
    return str(
        artifact_ref.get("uri")
        or artifact_ref.get("artifact_uri")
        or artifact_ref.get("url")
        or artifact_ref.get("path")
        or ""
    ).strip()


def _deployment_target_skill_ids(deployment: TrainingModelDeployment) -> list[str]:
    values = deployment.target_skill_ids_json if isinstance(deployment.target_skill_ids_json, list) else []
    return [str(item or "").strip() for item in values if str(item or "").strip()]


def _artifact_sha256(artifact_ref: dict[str, Any]) -> str | None:
    value = str(artifact_ref.get("sha256") or "").strip()
    return value[:64] or None


async def _shared_platform_model_context(
    db: AsyncSession,
    *,
    skill_id: str,
    run_id: str | None,
) -> dict[str, Any] | None:
    """Evaluation-only fallback to the latest shared platform-history model.

    This is intentionally not used for Skill runtime injection. It only gives
    inbox review cards a post-training evaluator when a Skill has no dedicated
    target deployment yet.
    """
    rows = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.status.in_(["active", "canary"]))
            .where(TrainingModelDeployment.model_family.ilike(f"%{POST_TRAINING_SHARED_MODEL_MARKER}%"))
            .order_by(
                TrainingModelDeployment.activated_at.desc().nullslast(),
                TrainingModelDeployment.updated_at.desc(),
                TrainingModelDeployment.created_at.desc(),
                TrainingModelDeployment.id.desc(),
            )
            .limit(20)
        )
    ).scalars().all()
    for deployment in rows:
        artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
        if not _artifact_uri(artifact_ref):
            continue
        job = await db.get(TrainingJob, deployment.job_id)
        if job is None:
            continue
        target_gateway_id = str(job.target_gateway_id or "").strip()
        deployment_payload = {
            "model_deployment_id": deployment.id,
            "job_id": deployment.job_id,
            "department": deployment.department,
            "model_family": deployment.model_family,
            "deployment_status": deployment.status,
            "rollout_percent": deployment.rollout_percent,
            "rollout_selected": True,
            "rollout_reason": "shared_platform_history_fallback",
            "artifact_id": str(artifact_ref.get("id") or deployment.artifact_id or "").strip(),
            "artifact_sha256": _artifact_sha256(artifact_ref),
            "target_skill_ids": _deployment_target_skill_ids(deployment),
            "run_id": str(run_id or "")[:80] or None,
            "runtime_status": {"target_gateway_id": target_gateway_id} if target_gateway_id else {},
        }
        deployment_payload = {
            key: value
            for key, value in deployment_payload.items()
            if value not in (None, "", [], {})
        }
        return {
            "active_model_deployment": deployment_payload,
            "model_deployment_id": deployment.id,
            "model_family": deployment.model_family,
            "deployment_status": deployment.status,
            "artifact_id": deployment_payload.get("artifact_id"),
            "artifact_sha256": deployment_payload.get("artifact_sha256"),
            "target_skill_ids": deployment_payload.get("target_skill_ids") or [],
            "runtime_status": deployment_payload.get("runtime_status") or {},
            "model_scope": "shared_platform_history_fallback",
            "requested_skill_id": skill_id,
            "control": {
                "agent_contract": "post_training_todo_evaluation.v1",
                "raw_payload_returned": False,
            },
        }
    return None


def _model_profile(deployment: TrainingModelDeployment, job: TrainingJob | None) -> str:
    spec = job.spec_json if job is not None and isinstance(job.spec_json, dict) else {}
    deployment_spec = spec.get("deployment") if isinstance(spec.get("deployment"), dict) else {}
    model_spec = spec.get("model") if isinstance(spec.get("model"), dict) else {}
    for raw in (
        spec.get("model_profile"),
        spec.get("base_model_profile"),
        deployment_spec.get("model_profile"),
        model_spec.get("profile"),
    ):
        profile = str(raw or "").strip().lower().replace("_", "-")
        if profile:
            return profile[:80]
    family = str(getattr(deployment, "model_family", "") or "").lower()
    if "qwen3.5" in family and "4b" in family:
        return "qwen3.5-4b"
    return "qwen3.5-4b"


def _gateway_ops(instance: OpenClawInstance | None) -> set[str]:
    if instance is None:
        return set()
    try:
        capabilities = json.loads(getattr(instance, "bridge_capabilities_json", "") or "{}")
    except (TypeError, ValueError):
        capabilities = {}
    raw_ops = capabilities.get("ops") if isinstance(capabilities, dict) else []
    return {str(item or "").strip() for item in (raw_ops or []) if str(item or "").strip()}


def _inference_text(result: Any) -> str:
    raw_text = ""
    if isinstance(result, dict):
        for key in ("text", "output_text", "answer", "response", "content"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                raw_text = value.strip()
                break
        if not raw_text:
            message = result.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    raw_text = content.strip()
                elif isinstance(content, list):
                    raw_text = "\n\n".join(
                        str(block.get("text") or block.get("content") or block).strip()
                        if isinstance(block, dict)
                        else str(block).strip()
                        for block in content
                        if str(block or "").strip()
                    )
        if not raw_text and not any(key in result for key in ("text", "output_text", "answer", "response", "content", "message")):
            raw_text = json.dumps(result, ensure_ascii=False, default=str)[:4000]
    else:
        raw_text = str(result or "").strip()
    return _clean_inference_output(raw_text)


_TRAILING_MODEL_SIGNATURE_RE = re.compile(
    "(?:(?<=[\u3400-\u9fff#）)\\]】。！？!?\"'”’])(?:Mistral|ng)|\\n\\s*Mistral)\\s*$"
)
_PROMPT_TEMPLATE_LEAK_RE = re.compile(
    r"(?is)(?:system\s*\n\s*(?:You are|你是)|<\|\s*(?:model_context|system|user|assistant)\b)"
)
_ROLE_BLOCK_LEAK_RE = re.compile(
    r"(?im)(?:\n|(?<=[。！？!?]))\s*(?:system|user|assistant)\s*(?:\n|#)"
)
_TOOL_TEMPLATE_LEAK_RE = re.compile(r"(?is)(?:\n|(?<=[。！？!?]))\s*#\s*Tools\b")


def _clean_inference_output(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    leak_candidates = [
        match.start()
        for pattern in (_ROLE_BLOCK_LEAK_RE, _TOOL_TEMPLATE_LEAK_RE)
        if (match := pattern.search(cleaned)) is not None and match.start() > 0
    ]
    if leak_candidates:
        cleaned = cleaned[: min(leak_candidates)].strip()
    leak_match = _PROMPT_TEMPLATE_LEAK_RE.search(cleaned)
    if leak_match:
        cleaned = cleaned[:leak_match.start()].strip()
    cleaned = re.sub(r"(?im)^\s*(assistant|user|system)\s*:?\s*$", "", cleaned)
    cleaned = re.sub(r"(?i)\b(assistant|user|system)\s*$", "", cleaned).strip()
    lines = [line.rstrip() for line in cleaned.splitlines()]
    cleaned = "\n".join(line for line in lines if line.strip()).strip()
    cleaned = _TRAILING_MODEL_SIGNATURE_RE.sub("", cleaned).strip()
    section_match = _EVALUATION_SECTION_RE.search(cleaned)
    if section_match and section_match.start() > 0:
        cleaned = cleaned[section_match.start():].strip()
    return cleaned


def _usable_evaluation_text(text: str) -> bool:
    cleaned = str(text or "").strip()
    if len(cleaned) < 40:
        return False
    section_hits = len(_EVALUATION_SECTION_RE.findall(cleaned))
    if section_hits >= 2:
        return True
    if "评估结论" in cleaned and ("建议动作" in cleaned or "审核建议" in cleaned):
        return True
    return False


def _inference_metrics(
    result: Any,
    *,
    gateway_id: str,
    gateway_kind: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    metrics = dict(result.get("metrics") or {}) if isinstance(result, dict) and isinstance(result.get("metrics"), dict) else {}
    try:
        generated_tokens = int(metrics.get("generated_tokens") or 0)
        duration_ms = int(metrics.get("duration_ms") or 0)
        if generated_tokens > 0 and duration_ms > 0 and not metrics.get("tokens_per_second"):
            metrics["tokens_per_second"] = round(generated_tokens / (duration_ms / 1000), 2)
    except (TypeError, ValueError):
        pass
    metrics.setdefault("inference_backend", "bridge_local_lora_inference")
    metrics.setdefault("inference_runtime", "local_bridge")
    metrics.setdefault("gateway_id", gateway_id)
    if gateway_kind:
        metrics.setdefault("gateway_kind", gateway_kind)
    metrics.setdefault("model_deployment_id", payload.get("deployment_id"))
    metrics.setdefault("model_profile", payload.get("profile"))
    metrics.setdefault("requested_max_new_tokens", payload.get("max_new_tokens"))
    return metrics


def _base_result(
    *,
    status: str,
    skill_id: str,
    run_id: str | None,
    reason: str | None = None,
    model_context: dict[str, Any] | None = None,
    model_scope: str | None = None,
) -> dict[str, Any]:
    deployment = (model_context or {}).get("active_model_deployment")
    deployment = deployment if isinstance(deployment, dict) else {}
    result = {
        "schema": "post_training_todo_evaluation.v1",
        "status": status,
        "skill_id": skill_id,
        "run_id": run_id,
        "evaluated_at": isoformat_bjt(now_bjt()),
        "model_deployment_id": (model_context or {}).get("model_deployment_id") or deployment.get("model_deployment_id"),
        "model_family": (model_context or {}).get("model_family") or deployment.get("model_family"),
        "deployment_status": (model_context or {}).get("deployment_status") or deployment.get("deployment_status"),
        "artifact_id": (model_context or {}).get("artifact_id") or deployment.get("artifact_id"),
        "artifact_sha256": (model_context or {}).get("artifact_sha256") or deployment.get("artifact_sha256"),
        "model_scope": model_scope or (model_context or {}).get("model_scope") or "target_skill_deployment",
    }
    if reason:
        result["reason"] = reason
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _prompt_for_todo(
    *,
    skill_id: str,
    run_id: str | None,
    title: str,
    summary: str,
    kind: str,
    payload: dict[str, Any],
    dispatch_tasks: list[dict[str, Any]] | None,
) -> str:
    material = {
        "skill_id": skill_id,
        "run_id": run_id,
        "todo": {
            "kind": kind,
            "title": title,
            "summary": summary,
        },
        "payload": _compact_payload_for_evaluation(payload),
        "dispatch_tasks": _compact_dispatch_tasks(dispatch_tasks),
    }
    prompt = "\n".join([
        "<|system|>",
        (
            "你是 SkillForge 后训练模型评估器。请只基于下面这次 Skill 输出和待办内容进行评估，"
            "不要编造外部数据，不要复述系统提示。"
        ),
        "用中文输出完整评估，结构固定为：",
        "1. 评估结论：可用/需复核/不可用，并给出一句话原因。",
        "2. 关键依据：列出 3-6 条直接来自输出的数据或事实。",
        "3. 风险缺口：指出数据、逻辑或执行风险。",
        "4. 建议动作：给管理员或执行人的下一步处理建议。",
        "5. 审核建议：建议通过、驳回或补数据。",
        "<|user|>",
        _json_for_prompt_material(material),
        "<|assistant|>",
    ])
    if len(prompt) <= POST_TRAINING_EVALUATION_MAX_PROMPT_CHARS:
        return prompt
    material["payload"] = _compact_payload_for_evaluation(material["payload"])
    material["dispatch_tasks"] = _compact_dispatch_tasks(material["dispatch_tasks"])
    prompt = "\n".join([
        "<|system|>",
        "你是 SkillForge 后训练模型评估器。只基于待办摘要和关键字段评估，不复述输入。",
        "输出：1. 评估结论 2. 关键依据 3. 风险缺口 4. 建议动作 5. 审核建议。",
        "<|user|>",
        _json_for_prompt_material(material, limit=3000),
        "<|assistant|>",
    ])
    return prompt[: POST_TRAINING_EVALUATION_MAX_PROMPT_CHARS - len("\n<|assistant|>")] + "\n<|assistant|>" if "<|assistant|>" not in prompt[:POST_TRAINING_EVALUATION_MAX_PROMPT_CHARS] else prompt[:POST_TRAINING_EVALUATION_MAX_PROMPT_CHARS]


async def evaluate_todo_with_post_training_model(
    db: AsyncSession,
    *,
    skill_id: str,
    run_id: str | None,
    title: str,
    summary: str,
    kind: str,
    payload: dict[str, Any],
    dispatch_tasks: list[dict[str, Any]] | None = None,
    timeout_seconds: int = POST_TRAINING_EVALUATION_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    if not should_evaluate_post_training_model(skill_id):
        return None
    if not isinstance(payload, dict):
        return None
    existing = payload.get(POST_TRAINING_EVALUATION_KEY)
    if isinstance(existing, dict) and existing.get("status"):
        return existing

    skill = await db.get(Skill, skill_id)
    model_context = await active_model_context_for_skill(
        db,
        skill_id=skill_id,
        skill_department=getattr(skill, "department", None),
        run_id=run_id,
        include_artifact_uri=True,
    )
    model_scope = "target_skill_deployment"
    if not model_context:
        model_context = await _shared_platform_model_context(
            db,
            skill_id=skill_id,
            run_id=run_id,
        )
        if model_context:
            model_scope = "shared_platform_history_fallback"
    if not model_context:
        return _base_result(
            status="skipped",
            skill_id=skill_id,
            run_id=run_id,
            reason="未找到该 Skill 当前可用的 active/canary 后训练模型部署",
        )

    deployment_id = str(model_context.get("model_deployment_id") or "").strip()
    deployment = await db.get(TrainingModelDeployment, deployment_id) if deployment_id else None
    if deployment is None:
        return _base_result(
            status="skipped",
            skill_id=skill_id,
            run_id=run_id,
            model_context=model_context,
            model_scope=model_scope,
            reason=f"训练后模型部署 {deployment_id or '-'} 不存在",
        )
    job = await db.get(TrainingJob, deployment.job_id)
    if job is None:
        return _base_result(
            status="skipped",
            skill_id=skill_id,
            run_id=run_id,
            model_context=model_context,
            model_scope=model_scope,
            reason=f"训练任务 {deployment.job_id or '-'} 不存在",
        )

    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    artifact_uri = _artifact_uri(artifact_ref)
    if not artifact_uri:
        return _base_result(
            status="skipped",
            skill_id=skill_id,
            run_id=run_id,
            model_context=model_context,
            model_scope=model_scope,
            reason="后训练模型缺少可推理产物 URI",
        )

    gateway_id = str(getattr(job, "target_gateway_id", "") or "").strip()
    instance = await db.get(OpenClawInstance, gateway_id) if gateway_id else None
    if not gateway_id or instance is None or getattr(instance, "is_active", True) is False:
        return _base_result(
            status="skipped",
            skill_id=skill_id,
            run_id=run_id,
            model_context=model_context,
            model_scope=model_scope,
            reason=f"训练后模型推理节点 {gateway_id or '-'} 不存在或未启用",
        )
    if "training.inference" not in _gateway_ops(instance):
        return _base_result(
            status="skipped",
            skill_id=skill_id,
            run_id=run_id,
            model_context=model_context,
            model_scope=model_scope,
            reason=f"训练后模型推理节点 {gateway_id} 未提供 training.inference 能力",
        )

    prompt = _prompt_for_todo(
        skill_id=skill_id,
        run_id=run_id,
        title=title,
        summary=summary,
        kind=kind,
        payload=payload,
        dispatch_tasks=dispatch_tasks,
    )
    model_profile = _model_profile(deployment, job)
    inference_payload = {
        "deployment_id": deployment.id,
        "profile": model_profile,
        "artifact_uri": artifact_uri,
        "artifact_sha256": str(artifact_ref.get("sha256") or model_context.get("artifact_sha256") or "").strip() or None,
        "prompt": prompt,
        "max_new_tokens": POST_TRAINING_EVALUATION_MAX_NEW_TOKENS,
        "timeout_seconds": timeout_seconds,
        "max_prompt_chars": POST_TRAINING_EVALUATION_MAX_PROMPT_CHARS,
        "model_context": model_context,
        "generation_policy": {
            "mode": "todo_post_training_evaluation",
            "max_new_tokens": POST_TRAINING_EVALUATION_MAX_NEW_TOKENS,
        },
    }

    try:
        result = await AIClawClient(
            gateway_id,
            gateway_kind=getattr(instance, "bridge_gateway_kind", None),
        ).run_training_inference(
            inference_payload,
            timeout=timeout_seconds,
        )
    except AppError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        reason = str(detail.get("detail") or detail.get("bridge_error") or exc.message or exc.code)
        logger.warning(
            "后训练模型评估失败 skill={} run={} deployment={} code={} reason={}",
            skill_id,
            run_id,
            deployment_id,
            exc.code,
            reason,
        )
        failed = _base_result(
            status="failed",
            skill_id=skill_id,
            run_id=run_id,
            model_context=model_context,
            model_scope=model_scope,
            reason=reason,
        )
        failed["error_code"] = exc.code
        return failed
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "后训练模型评估异常 skill={} run={} deployment={} err={}",
            skill_id,
            run_id,
            deployment_id,
            exc,
        )
        failed = _base_result(
            status="failed",
            skill_id=skill_id,
            run_id=run_id,
            model_context=model_context,
            model_scope=model_scope,
            reason=str(exc),
        )
        failed["error_code"] = "POST_TRAINING_EVALUATION_FAILED"
        return failed

    text = _inference_text(result)
    if not _usable_evaluation_text(text):
        failed = _base_result(
            status="failed",
            skill_id=skill_id,
            run_id=run_id,
            model_context=model_context,
            model_scope=model_scope,
            reason="模型未返回可用评估文本，可能回显了输入片段",
        )
        failed["error_code"] = "POST_TRAINING_EVALUATION_UNUSABLE_OUTPUT"
        if text:
            failed["raw_text_preview"] = _clean_text(text, limit=500)
            failed["text_sha256"] = _sha256_text(text)
        return failed
    metrics = _inference_metrics(
        result,
        gateway_id=gateway_id,
        gateway_kind=getattr(instance, "bridge_gateway_kind", None),
        payload=inference_payload,
    )
    used = _base_result(
        status="used",
        skill_id=skill_id,
        run_id=run_id,
        model_context=model_context,
        model_scope=model_scope,
    )
    used.update({
        "text": text,
        "text_sha256": _sha256_text(text),
        "gateway_id": gateway_id,
        "gateway_kind": getattr(instance, "bridge_gateway_kind", None),
        "model_profile": model_profile,
        "metrics": metrics,
        "prompt_sha256": _sha256_text(prompt),
    })
    if isinstance(result, dict) and result.get("finish_reason"):
        used["finish_reason"] = result.get("finish_reason")
    return used


async def backfill_post_training_evaluations(
    db: AsyncSession,
    *,
    skill_ids: set[str] | None = None,
    limit: int = 5,
    since: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
    request_ids: list[str] | None = None,
) -> dict[str, Any]:
    from app.todos.models import DecisionRequest

    target_skill_ids = skill_ids or set(POST_TRAINING_EVALUATED_SKILL_IDS)
    clean_request_ids = [str(item or "").strip() for item in (request_ids or []) if str(item or "").strip()]
    stmt = select(DecisionRequest).where(DecisionRequest.skill_id.in_(sorted(target_skill_ids)))
    if clean_request_ids:
        stmt = stmt.where(DecisionRequest.id.in_(clean_request_ids))
    else:
        stmt = (
            stmt
            .where(DecisionRequest.archived_at.is_(None))
            .where(DecisionRequest.aggregate_status == "pending")
        )
        if since is not None:
            stmt = stmt.where(DecisionRequest.created_at >= since)
    stmt = (
        stmt
        .order_by(DecisionRequest.created_at.desc())
        .limit(max(1, min(int(limit or 100), 500)))
    )
    rows = (await db.execute(stmt)).scalars().all()
    updated = 0
    skipped_existing = 0
    candidates = 0
    evaluated: list[str] = []
    for request in rows:
        payload = request.payload if isinstance(request.payload, dict) else {}
        if isinstance(payload.get(POST_TRAINING_EVALUATION_KEY), dict) and not force:
            skipped_existing += 1
            continue
        candidates += 1
        if dry_run:
            evaluated.append(request.id)
            continue
        evaluation = await evaluate_todo_with_post_training_model(
            db,
            skill_id=request.skill_id,
            run_id=request.run_id,
            title=request.title,
            summary=request.summary or "",
            kind=request.kind,
            payload={key: value for key, value in payload.items() if key != POST_TRAINING_EVALUATION_KEY} if force else payload,
            dispatch_tasks=payload.get("dispatch_tasks") if isinstance(payload.get("dispatch_tasks"), list) else None,
        )
        if evaluation:
            request.payload = {**payload, POST_TRAINING_EVALUATION_KEY: evaluation}
            updated += 1
            evaluated.append(request.id)
    if updated:
        await db.flush()
    return {
        "checked": len(rows),
        "candidates": candidates,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "request_ids": evaluated,
    }
