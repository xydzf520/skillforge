"""
执行编排服务：调度 → 调OpenClaw → 收结果 → 写日志 → 写平台待办/报告。
SkillForge不执行Skill脚本，只负责编排。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta
from time import monotonic, time
from uuid import uuid4

from loguru import logger
from sqlalchemy import func, or_, select

from app.common.audit import audit
from app.common.contract_schema import data_proofs_sample_used, normalize_data_proofs
from app.common.exceptions import AppError
from app.common.models import IntelligenceAnalyzeRun
from app.common.time_utils import isoformat_bjt
from app.config import settings
from app.dingtalk.outbox import outbox
from app.execution.artifact_service import persist_execution_artifact
from app.execution.model_context import active_model_context_for_skill
from app.execution.models import (
    RUN_MODE_BATCH_CANDIDATE,
    RUN_MODE_MANUAL_REAL,
    RUN_MODE_SAMPLE_PREVIEW,
    RUN_MODE_SANDBOX_TEST,
    RUN_MODE_SCHEDULED_REAL,
    RUN_MODE_SDK_SUBMIT,
    RUN_MODES,
    SAMPLE_RUN_MODES,
    ContractDrift,
    DecisionLog,
    ExecutionArtifact,
    ExecutionRun,
    OpenClawInstance,
)
from app.execution.openclaw_client import default_client
from app.skills.core.models import Skill
from app.tasktree import dispatcher as tasktree_dispatcher
from app.tasktree.writer import serialize_datetime, writer as tasktree_writer
from app.common.time_utils import now_bjt

DATA_QUALITY_CHECK_TIMEOUT_SECONDS = 15.0
DEFAULT_RUN_SKILL_TIMEOUT_SECONDS = 180.0
BRIDGE_SCRIPT_TIMEOUT_GRACE_SECONDS = 15.0
CORE_EXECUTION_TIMEOUT_BUFFER_SECONDS = 20.0
PERSIST_RESULT_TIMEOUT_SECONDS = 15.0
CONTRACT_DRIFT_TIMEOUT_SECONDS = 10.0
TODO_CREATE_TIMEOUT_SECONDS = 15.0
EXECUTION_SUMMARY_TIMEOUT_SECONDS = 20.0
RUN_TOKEN_TTL_SECONDS = 12 * 60 * 60
MCP_RUNTIME_ALERT_CODES = {"MCP_RUNTIME_DRIFT", "MCP_DIRECT_COOKIE_BLOCKED"}
SKILL_RUNTIME_AGENT_PURPOSES = {"skill_runtime", "mixed"}
_operator_trigger_tasks: set[asyncio.Task] = set()
LINK_DECLINE_OPERATOR_SUCCESS_RECIPIENT_QUERY = settings.LINK_DECLINE_OPERATOR_RECIPIENT_QUERY.strip()
LINK_DECLINE_OPERATOR_SUCCESS_DINGTALK_USER_ID = settings.LINK_DECLINE_OPERATOR_DINGTALK_USER_ID.strip()
LINK_DECLINE_OPERATOR_SUCCESS_RELATED_TYPE = "link_decline_operator_success"
_TODO_PRIORITY_LEVELS = ("P0", "P1", "P2", "P3")
_LINK_DECLINE_COLLECTION_TIME_KEYS = (
    "snapshot_time",
    "快照时间",
    "schedule_time",
    "data_time",
    "采集时间",
    "执行时间",
)
_LINK_DECLINE_BASELINE_TOLERANCE_MINUTES = 30
_LINK_DECLINE_OPERATOR_TRANSIENT_RETRY_SECONDS = (300, 600, 1200, 2400)
_LINK_DECLINE_OPERATOR_MAX_RETRIES = len(_LINK_DECLINE_OPERATOR_TRANSIENT_RETRY_SECONDS) + 1
_LINK_DECLINE_OPERATOR_TRANSIENT_ERROR_CODES = {
    "BRIDGE_OFFLINE",
    "BRIDGE_DISCONNECTED",
    "EPOCH_MISMATCH",
    "REQUEST_TIMEOUT",
    "AICLAW_RATE_LIMITED",
}


def _agent_purpose(instance: OpenClawInstance | None) -> str:
    value = str(getattr(instance, "agent_purpose", "") or "skill_runtime").strip().lower()
    return value if value in {"skill_runtime", "analysis", "training", "media", "mixed"} else "skill_runtime"


def _is_skill_runtime_agent(instance: OpenClawInstance | None) -> bool:
    return _agent_purpose(instance) in SKILL_RUNTIME_AGENT_PURPOSES


def _decision_model_runtime_status_summary(runtime_status: object) -> dict | None:
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


def _sanitize_decision_model_context(row: IntelligenceAnalyzeRun) -> dict | None:
    route = row.analysis_delegate_route if isinstance(row.analysis_delegate_route, dict) else {}
    deployment = route.get("active_model_deployment") if isinstance(route.get("active_model_deployment"), dict) else {}
    inference = route.get("deployed_model_inference") if isinstance(route.get("deployed_model_inference"), dict) else {}
    deployment_id = str(deployment.get("id") or "").strip()
    if not deployment_id:
        return None
    context = {
        "model_deployment_id": deployment_id[:80],
        "model_family": str(deployment.get("model_family") or "")[:100] or None,
        "deployment_status": str(deployment.get("status") or "")[:30] or None,
        "rollout_percent": deployment.get("rollout_percent"),
        "artifact_id": str(deployment.get("artifact_id") or "")[:120] or None,
        "artifact_sha256": str(deployment.get("artifact_sha256") or "")[:64] or None,
        "model_runtime_status": _decision_model_runtime_status_summary(deployment.get("runtime_status")),
        "rollout_selected": deployment.get("rollout_selected"),
        "rollout_bucket": deployment.get("rollout_bucket"),
        "rollout_reason": str(deployment.get("rollout_reason") or "")[:50] or None,
        "target_skill_ids": [
            str(item)[:100]
            for item in (deployment.get("target_skill_ids") if isinstance(deployment.get("target_skill_ids"), list) else [])
            if str(item or "").strip()
        ][:20],
        "analysis_run_id": row.id,
        "analysis_backend": row.analysis_backend,
        "analysis_agent_id": row.analysis_agent_id,
        "analysis_model": row.model,
        "cache_key": row.cache_key,
    }
    if inference:
        context["inference_status"] = str(inference.get("status") or "")[:30] or None
        context["inference_backend"] = str(inference.get("backend") or "")[:50] or None
        context["inference_gateway_id"] = str(inference.get("gateway_id") or "")[:100] or None
        context["inference_profile"] = str(inference.get("profile") or "")[:50] or None
        context["inference_text_sha256"] = str(inference.get("text_sha256") or "")[:64] or None
        metrics = inference.get("metrics") if isinstance(inference.get("metrics"), dict) else {}
        context["inference_metrics"] = {
            key: metrics.get(key)
            for key in (
                "prompt_tokens",
                "generated_tokens",
                "duration_ms",
                "cuda_available",
                "cuda_device",
                "cuda_memory_allocated_mb",
                "cuda_memory_reserved_mb",
            )
            if metrics.get(key) is not None
        } or None
    return {key: value for key, value in context.items() if value not in (None, "", [], {})}


async def latest_decision_model_context(
    db,
    *,
    skill_id: str,
    run_id: str | None,
) -> dict | None:
    """Return governed model-deployment context used by platform analysis for a run."""
    if not run_id:
        return None
    rows = (
        await db.execute(
            select(IntelligenceAnalyzeRun)
            .where(IntelligenceAnalyzeRun.skill_id == skill_id)
            .where(IntelligenceAnalyzeRun.run_id == run_id)
            .order_by(IntelligenceAnalyzeRun.created_at.desc(), IntelligenceAnalyzeRun.id.desc())
            .limit(20)
        )
    ).scalars().all()
    for row in rows:
        context = _sanitize_decision_model_context(row)
        if context:
            return context
    return None


def attach_model_context_to_output(output: dict, model_context: dict | None) -> dict:
    if not isinstance(output, dict) or not model_context:
        return output
    result = dict(output)
    meta = result.get("_skillforge_meta")
    meta = dict(meta) if isinstance(meta, dict) else {}
    meta["model_context"] = model_context
    meta["active_model_deployment"] = {
        key: model_context.get(key)
        for key in (
            "model_deployment_id",
            "model_family",
            "deployment_status",
            "rollout_percent",
            "rollout_selected",
            "rollout_bucket",
            "rollout_reason",
            "artifact_id",
            "artifact_sha256",
            "target_skill_ids",
        )
        if model_context.get(key) is not None
    }
    result["_skillforge_meta"] = meta
    return result


def model_context_from_output(output: dict | None) -> dict | None:
    meta = output.get("_skillforge_meta") if isinstance(output, dict) else None
    if not isinstance(meta, dict):
        return None
    context = meta.get("model_context")
    if isinstance(context, dict) and context.get("model_deployment_id"):
        return context
    active_model = meta.get("active_model_deployment")
    if isinstance(active_model, dict) and active_model.get("model_deployment_id"):
        return {
            "model_deployment_id": str(active_model.get("model_deployment_id") or "")[:80],
            "model_family": str(active_model.get("model_family") or "")[:100] or None,
            "deployment_status": str(active_model.get("deployment_status") or "")[:30] or None,
            "rollout_percent": active_model.get("rollout_percent"),
            "artifact_id": str(active_model.get("artifact_id") or "")[:120] or None,
            "artifact_sha256": str(active_model.get("artifact_sha256") or "")[:64] or None,
            "target_skill_ids": [
                str(item)[:100]
                for item in (
                    active_model.get("target_skill_ids")
                    if isinstance(active_model.get("target_skill_ids"), list)
                    else []
                )
                if str(item or "").strip()
            ][:20],
        }
    return None


async def _capture_execution_artifact_refs(session, artifact_refs: list[dict], *, skill_id: str, run_id: str) -> None:
    if not artifact_refs:
        return
    try:
        from app.learning.service import capture_execution_artifact

        for ref in artifact_refs:
            artifact_id = ref.get("id") if isinstance(ref, dict) else None
            if artifact_id is None:
                continue
            artifact = await session.get(ExecutionArtifact, artifact_id)
            if artifact is not None:
                await capture_execution_artifact(session, artifact)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获执行原始数据失败 skill={} run={} err={}", skill_id, run_id, exc)


def _link_decline_time_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def _link_decline_collection_time(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in _LINK_DECLINE_COLLECTION_TIME_KEYS:
        parsed = _link_decline_time_label(payload.get(key))
        if parsed:
            return parsed
    for nested_key in ("数据采集快照", "0855快照对比", "_0855_snapshot_compare", "采集报告"):
        nested = payload.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in (*_LINK_DECLINE_COLLECTION_TIME_KEYS, "as_of_time", "captured_at", "snapshot_schedule"):
            parsed = _link_decline_time_label(nested.get(key))
            if parsed:
                return parsed
    return ""


def _link_decline_has_value(value: object) -> bool:
    return value is not None and str(value).strip() != ""


def _link_decline_num(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if text.startswith("¥"):
        text = text[1:]
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _link_decline_pct(curr: object, prev: object) -> float | None:
    if not _link_decline_has_value(prev):
        return None
    prev_num = _link_decline_num(prev)
    if prev_num <= 0:
        return None
    return (_link_decline_num(curr) - prev_num) / prev_num * 100


def _link_decline_copy_compare_from_baseline(
    current_basis: dict,
    baseline_basis: dict,
    mappings: tuple[tuple[str, str, str], ...],
) -> bool:
    applied = False
    for current_key, compare_key, pct_key in mappings:
        if not _link_decline_has_value(current_basis.get(current_key)):
            continue
        if not _link_decline_has_value(baseline_basis.get(current_key)):
            continue
        current_basis[compare_key] = baseline_basis.get(current_key)
        pct = _link_decline_pct(current_basis.get(current_key), current_basis.get(compare_key))
        if pct is not None:
            current_basis[pct_key] = round(pct, 2)
        applied = True
    if applied:
        current_basis["found"] = True
        current_basis["compare_found"] = True
        current_basis["compare_source"] = "baseline_collector_output"
    return applied


def _link_decline_with_baseline_compare(collector_output: dict, baseline_output: dict | None) -> tuple[dict, dict]:
    if not isinstance(baseline_output, dict):
        return collector_output, {"applied_item_count": 0}
    raw = collector_output.get("外部平台原始采集") if isinstance(collector_output.get("外部平台原始采集"), dict) else {}
    baseline_raw = baseline_output.get("外部平台原始采集") if isinstance(baseline_output.get("外部平台原始采集"), dict) else {}
    current_flow = raw.get("required_flow_by_item") if isinstance(raw.get("required_flow_by_item"), dict) else {}
    baseline_flow = (
        baseline_raw.get("required_flow_by_item")
        if isinstance(baseline_raw.get("required_flow_by_item"), dict)
        else {}
    )
    if not current_flow or not baseline_flow:
        return collector_output, {"applied_item_count": 0}

    free_mappings = (
        ("search_visitor", "search_visitor_compare", "search_visitor_change_pct"),
        ("search_pay_buyer_count", "search_pay_buyer_compare", "search_pay_buyer_change_pct"),
        ("search_conversion_rate", "search_conversion_compare_rate", "search_conversion_change_pct"),
        ("search_pay_amount", "search_pay_amount_compare", "search_pay_amount_change_pct"),
    )
    paid_mappings = (
        ("keyword_promotion_visitor", "keyword_promotion_visitor_compare", "keyword_promotion_visitor_change_pct"),
        (
            "keyword_promotion_pay_buyer_count",
            "keyword_promotion_pay_buyer_compare",
            "keyword_promotion_pay_buyer_change_pct",
        ),
        (
            "keyword_promotion_conversion_rate",
            "keyword_promotion_conversion_compare_rate",
            "keyword_promotion_conversion_change_pct",
        ),
        ("keyword_promotion_pay_amount", "keyword_promotion_pay_amount_compare", "keyword_promotion_pay_amount_change_pct"),
    )

    updated_flow = dict(current_flow)
    applied_item_ids: list[str] = []
    for item_id, current_entry in current_flow.items():
        if not isinstance(current_entry, dict) or current_entry.get("error"):
            continue
        if str(current_entry.get("source") or "") != "tmall_item_flow_required_metrics":
            continue
        baseline_entry = baseline_flow.get(item_id)
        if not isinstance(baseline_entry, dict) or baseline_entry.get("error"):
            continue
        if str(baseline_entry.get("source") or "") != "tmall_item_flow_required_metrics":
            continue

        current_free = dict(
            current_entry.get("free_flow_basis")
            if isinstance(current_entry.get("free_flow_basis"), dict)
            else {}
        )
        baseline_free = (
            baseline_entry.get("free_flow_basis")
            if isinstance(baseline_entry.get("free_flow_basis"), dict)
            else {}
        )
        current_paid = dict(
            current_entry.get("paid_flow_basis")
            if isinstance(current_entry.get("paid_flow_basis"), dict)
            else {}
        )
        baseline_paid = (
            baseline_entry.get("paid_flow_basis")
            if isinstance(baseline_entry.get("paid_flow_basis"), dict)
            else {}
        )
        free_applied = _link_decline_copy_compare_from_baseline(current_free, baseline_free, free_mappings)
        paid_applied = _link_decline_copy_compare_from_baseline(current_paid, baseline_paid, paid_mappings)
        if not free_applied and not paid_applied:
            continue

        patched_entry = dict(current_entry)
        patched_entry["compare_source"] = "baseline_collector_output"
        patched_entry["compareAsOfTime"] = baseline_entry.get("asOfTime") or baseline_entry.get("updateTime")
        if baseline_entry.get("dateRange"):
            patched_entry["compareDateRange"] = baseline_entry.get("dateRange")
        if free_applied:
            patched_entry["free_flow_basis"] = current_free
        if paid_applied:
            patched_entry["paid_flow_basis"] = current_paid
        updated_flow[item_id] = patched_entry
        applied_item_ids.append(str(item_id))

    if not applied_item_ids:
        return collector_output, {"applied_item_count": 0}
    patched_output = dict(collector_output)
    patched_raw = dict(raw)
    patched_raw["required_flow_by_item"] = updated_flow
    patched_output["外部平台原始采集"] = patched_raw
    patched_output["_baseline_collector_compare"] = {
        "source": "baseline_collector_output",
        "applied_item_count": len(applied_item_ids),
        "applied_item_ids": applied_item_ids[:100],
    }
    return patched_output, patched_output["_baseline_collector_compare"]


def _link_decline_collector_snapshot(output: dict | None) -> dict | None:
    if not isinstance(output, dict):
        return None
    snapshot = output.get("数据采集快照")
    if isinstance(snapshot, dict) and snapshot.get("items"):
        return snapshot
    return None


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _normalize_todo_priority(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if text in _TODO_PRIORITY_LEVELS:
        return text
    for level in _TODO_PRIORITY_LEVELS:
        if text.startswith(f"{level}｜") or text.startswith(f"{level}|") or text.startswith(f"{level} "):
            return level
    return None


def _priority_from_todo_payload(payload: dict | None, title: str | None = None) -> str | None:
    if not isinstance(payload, dict):
        payload = {}
    input_payload = payload.get("input") if isinstance(payload.get("input"), dict) else {}
    for candidate in (
        payload.get("priority"),
        input_payload.get("priority"),
        payload.get("todo_priority"),
        title,
    ):
        priority = _normalize_todo_priority(candidate)
        if priority:
            return priority
    return None


def _bounded_dingtalk_related_id(value: str) -> str:
    text = str(value or "").strip()
    if len(text) <= 50:
        return text
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"{text[:39]}:{digest}"


async def _operator_todo_priority_summary(
    session,
    *,
    run_id: str,
    output: dict,
    fallback_todo_count: int | None,
) -> dict:
    from app.todos.models import DecisionRequest

    counts = {level: 0 for level in _TODO_PRIORITY_LEVELS}
    unknown_count = 0
    db_rows = (
        await session.execute(
            select(DecisionRequest.title, DecisionRequest.payload)
            .where(DecisionRequest.run_id == run_id)
            .order_by(DecisionRequest.created_at.asc())
        )
    ).all()
    if db_rows:
        for title, payload in db_rows:
            priority = _priority_from_todo_payload(payload, title)
            if priority:
                counts[priority] += 1
            else:
                unknown_count += 1
        return {"total": len(db_rows), "priority_counts": counts, "unknown_count": unknown_count}

    todos = output.get("todos") if isinstance(output.get("todos"), list) else []
    for todo in todos:
        todo = todo if isinstance(todo, dict) else {}
        payload = todo.get("payload") if isinstance(todo.get("payload"), dict) else todo
        priority = _priority_from_todo_payload(payload, str(todo.get("title") or ""))
        if priority:
            counts[priority] += 1
        else:
            unknown_count += 1
    total = int(fallback_todo_count if fallback_todo_count is not None else len(todos))
    if total > len(todos):
        unknown_count += total - len(todos)
    return {"total": total, "priority_counts": counts, "unknown_count": unknown_count}


def issue_run_token(
    *,
    skill_id: str,
    run_id: str,
    instance_id: str | None = None,
    skill_git_commit_full: str | None = None,
    department: str | None = None,
    skill_department: str | None = None,
    allow_intelligence: bool = True,
    ttl_seconds: int = RUN_TOKEN_TTL_SECONDS,
) -> str:
    """签发执行期 run token。首期用 HMAC 签名 JSON，避免新增存储依赖。"""
    now = int(time())
    claims = {
        "skill_id": skill_id,
        "run_id": run_id,
        "instance_id": instance_id,
        "skill_git_commit_full": skill_git_commit_full,
        "department": department,
        "skill_department": skill_department or department,
        "allow_intelligence": bool(allow_intelligence),
        "iat": now,
        "exp": now + int(ttl_seconds or RUN_TOKEN_TTL_SECONDS),
    }
    payload = _b64url_encode(
        json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(settings.SECRET_KEY.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_b64url_encode(signature)}"


def verify_run_token(
    token: str | None,
    *,
    skill_id: str | None = None,
    run_id: str | None = None,
    instance_id: str | None = None,
    skill_git_commit_full: str | None = None,
    require_intelligence: bool = False,
) -> dict:
    """校验 run token 与请求声明是否一致。失败统一 RUN_TOKEN_INVALID。"""
    if not token:
        raise AppError("RUN_TOKEN_INVALID", 401)
    try:
        payload, signature = str(token).split(".", 1)
        expected = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        actual = _b64url_decode(signature)
        if not hmac.compare_digest(actual, expected):
            raise ValueError("signature mismatch")
        claims = json.loads(_b64url_decode(payload).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise AppError("RUN_TOKEN_INVALID", 401) from exc

    if int(claims.get("exp") or 0) < int(time()):
        raise AppError("RUN_TOKEN_INVALID", 401, {"reason": "expired"})
    checks = {
        "skill_id": skill_id,
        "run_id": run_id,
        "instance_id": instance_id,
        "skill_git_commit_full": skill_git_commit_full,
    }
    for key, expected_value in checks.items():
        if expected_value and str(claims.get(key) or "") != str(expected_value):
            raise AppError("RUN_TOKEN_INVALID", 401, {"reason": f"{key}_mismatch"})
    if require_intelligence and claims.get("allow_intelligence") is not True:
        raise AppError("RUN_TOKEN_INVALID", 401, {"reason": "intelligence_not_allowed"})
    return claims


def _sf():
    """延迟获取async_session_factory，避免测试中engine替换问题"""
    from app.database import async_session_factory
    return async_session_factory


def _walk_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def _extract_mcp_runtime_alert_codes(output: dict | None) -> set[str]:
    if not isinstance(output, dict):
        return set()
    codes: set[str] = set()
    for value in _walk_values(output):
        text = str(value or "")
        for code in MCP_RUNTIME_ALERT_CODES:
            if code in text:
                codes.add(code)
    return codes


async def _emit_mcp_runtime_alerts(
    *,
    run_id: str,
    skill_id: str,
    output: dict | None,
    decision_log_id: int | None,
) -> None:
    codes = _extract_mcp_runtime_alert_codes(output)
    if not codes:
        return

    from sqlalchemy.exc import IntegrityError

    from app.auth.models import User
    from app.dingtalk.recipients import enqueue_admin_work_notice
    from app.todos.models import AITodo, DecisionRequest

    async with _sf()() as session:
        skill = await session.get(Skill, skill_id)
        owner_id = getattr(skill, "owner", None) if skill else None
        assignee = None
        if owner_id:
            owner = await session.get(User, owner_id)
            if owner and owner.is_active and getattr(owner, "state", "active") == "active":
                assignee = owner.id
        if not assignee:
            assignee = await session.scalar(
                select(User.id)
                .where(User.is_active == True)  # noqa: E712
                .where(User.state == "active")
                .where(User.role.in_(["system_admin", "admin", "ai_engineer"]))
                .order_by(User.role.asc(), User.id.asc())
                .limit(1)
            )

        now = now_bjt()
        for code in sorted(codes):
            source_id = f"{run_id}:{code}"
            existing = await session.scalar(
                select(DecisionRequest.id).where(
                    DecisionRequest.source_type == "mcp_runtime_alert",
                    DecisionRequest.source_id == source_id,
                    DecisionRequest.kind == "review",
                    DecisionRequest.skill_id == skill_id,
                )
            )
            if not existing:
                request = DecisionRequest(
                    id=f"mcp-alert-{uuid4().hex[:20]}",
                    source_type="mcp_runtime_alert",
                    source_id=source_id,
                    skill_id=skill_id,
                    run_id=run_id,
                    decision_log_id=decision_log_id,
                    kind="review",
                    title=f"MCP 运行告警：{code}",
                    summary=f"Skill {skill_id} run {run_id} 触发 {code}，需要处理采集治理链路。",
                    payload={
                        "alert_type": code.lower(),
                        "code": code,
                        "skill_id": skill_id,
                        "run_id": run_id,
                        "decision_log_id": decision_log_id,
                    },
                    decision_mode="any_of",
                    aggregate_status="pending",
                    sla_at=now + timedelta(days=1),
                    created_at=now,
                )
                session.add(request)
                await session.flush()
                if assignee:
                    session.add(AITodo(request_id=request.id, kind="review", assignee=assignee, status="pending"))
            try:
                await enqueue_admin_work_notice(
                    {
                        "msgtype": "markdown",
                        "markdown": {
                            "title": f"MCP 运行告警：{code}",
                            "text": (
                                f"**Skill**: {skill_id}\n"
                                f"**run_id**: {run_id}\n"
                                f"**告警**: {code}\n"
                                "请检查 MCP runtime / 采集治理入口，避免告警静默。"
                            ),
                        },
                    },
                    priority=2,
                    related_type="mcp_runtime_alert",
                    related_id=source_id,
                    session=session,
                    dedupe_window_minutes=60,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP runtime 钉钉告警入队失败 skill={} run={} code={} err={}", skill_id, run_id, code, exc)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()


async def _invalidate_execution_cache() -> None:
    """清空 execution 列表缓存。在任何 ExecutionRun 状态变更(新增/更新)后调用,
    避免 /api/executions/runs 看到 stale items/total。失败不影响主流程。"""
    try:
        from app.common.cache import cache_delete_pattern
        await cache_delete_pattern("execution:*")
    except Exception as e:
        logger.warning("invalidate execution cache 失败: {}", e)


async def _invalidate_generated_data_cache_if_needed(output: dict | None) -> None:
    if not isinstance(output, dict):
        return
    has_reports = isinstance(output.get("reports"), list)
    has_todos = isinstance(output.get("todos"), list)
    if not has_reports and not has_todos:
        return
    try:
        from app.common.cache import invalidate_generated_data_cache

        await invalidate_generated_data_cache()
    except Exception as e:
        logger.warning("invalidate generated data cache 失败: {}", e)


# 向后兼容旧私有函数名，外部如仍引用则走统一生成数据缓存失效。
async def _invalidate_inbox_reports_cache_if_needed(output: dict | None) -> None:
    await _invalidate_generated_data_cache_if_needed(output)


def normalize_run_mode(
    run_mode: str | None = None,
    *,
    sandbox: bool = False,
    triggered_by: str | None = None,
) -> str:
    """SSOT for execution run modes; old callers can keep using sandbox/triggered_by."""
    if run_mode in RUN_MODES:
        return run_mode

    trigger = (triggered_by or "").lower()
    if any(marker in trigger for marker in ("sample", "fixture", "preview")):
        return RUN_MODE_SAMPLE_PREVIEW
    if trigger.startswith("batch:"):
        return RUN_MODE_BATCH_CANDIDATE
    if trigger in {"skill_sdk", "sdk", "sdk_submit"} or trigger.startswith("sdk:"):
        return RUN_MODE_SDK_SUBMIT
    if sandbox:
        return RUN_MODE_SANDBOX_TEST
    if trigger == "scheduler" or trigger.startswith("scheduler:"):
        return RUN_MODE_SCHEDULED_REAL
    if trigger == "node_scheduler" or trigger.startswith("node_scheduler:"):
        return RUN_MODE_SCHEDULED_REAL
    return RUN_MODE_MANUAL_REAL


def _execution_sample_used(run_mode: str, data_proofs: list[dict] | None, explicit: bool | None = None) -> bool:
    if explicit is not None:
        return bool(explicit)
    return run_mode in SAMPLE_RUN_MODES or data_proofs_sample_used(data_proofs)


def attach_execution_metadata(
    output: dict | None,
    *,
    run_id: str,
    skill_id: str,
    run_mode: str,
    parent_run_id: str | None = None,
    batch_id: str | None = None,
    data_proofs: list[dict] | None = None,
    sample_used: bool | None = None,
) -> dict:
    result = dict(output or {})
    proofs = normalize_data_proofs(data_proofs or result.get("data_provenance"))
    used_sample = _execution_sample_used(run_mode, proofs, sample_used)
    meta = result.get("_skillforge_meta")
    meta = dict(meta) if isinstance(meta, dict) else {}
    meta.update({
        "run_id": run_id,
        "skill_id": skill_id,
        "run_mode": run_mode,
        "data_proofs": proofs,
        "data_provenance": proofs,
        "sample_used": used_sample,
    })
    if parent_run_id:
        meta["parent_run_id"] = parent_run_id
    if batch_id:
        meta["batch_id"] = batch_id
    result["_skillforge_meta"] = meta
    return result


def output_declares_sample(output: dict | None) -> bool:
    """检测 Skill 输出是否自称样例/沙箱。

    非沙箱执行不能把 `metadata.sample_used=true` 或 `run_mode=sandbox_test`
    这类结果包装成 manual_real，否则会出现平台元数据与业务内容互相矛盾。
    """
    if not isinstance(output, dict):
        return False
    candidates = [output]
    for key in ("metadata", "_skillforge_meta"):
        value = output.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for item in candidates:
        if item.get("sample_used") is True:
            return True
        if item.get("run_mode") in SAMPLE_RUN_MODES:
            return True
    return False


_PRIMARY_INDICATOR_SEVERITY = {"critical", "high", "medium", "low"}
_PRIMARY_INDICATOR_TONE = {"danger", "warning", "success", "info", "neutral"}


def extract_primary_indicator(output: dict | None) -> dict | None:
    """从 `output.reports[0].primary_indicator` 抽取卡片主指标。

    契约：`docs/spec/skill-output-report-v1.md` 的 `primary_indicator` 字段。
    只取第一条报告的主指标，作为整张待办卡片的"一眼核心"。
    非法结构一律返回 None —— 调用方会 fallback 到 metrics_preview[0]。
    """
    if not isinstance(output, dict):
        return None
    reports = output.get("reports")
    if not isinstance(reports, list) or not reports:
        return None
    first = reports[0]
    if not isinstance(first, dict):
        return None
    indicator = first.get("primary_indicator")
    if not isinstance(indicator, dict):
        return None
    label = indicator.get("label")
    value = indicator.get("value")
    if not isinstance(label, str) or not label.strip():
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    result: dict = {"label": label.strip()[:40], "value": value.strip()[:80]}
    severity = indicator.get("severity")
    if isinstance(severity, str) and severity in _PRIMARY_INDICATOR_SEVERITY:
        result["severity"] = severity
    tone = indicator.get("tone")
    if isinstance(tone, str) and tone in _PRIMARY_INDICATOR_TONE:
        result["tone"] = tone
    return result


class _TimeoutBudget:
    def __init__(self, total_seconds: float):
        self.total_seconds = max(float(total_seconds), 0.001)
        self._deadline = monotonic() + self.total_seconds

    def remaining(self) -> float:
        return max(self._deadline - monotonic(), 0.0)


def _bridge_script_phase_timeout(timeout: int | float | None) -> float:
    base_timeout = max(float(timeout or 120), 1.0)
    return base_timeout + BRIDGE_SCRIPT_TIMEOUT_GRACE_SECONDS


def _core_execution_total_timeout(runtime_phase_timeout: float) -> float:
    return max(float(runtime_phase_timeout), 1.0) + CORE_EXECUTION_TIMEOUT_BUFFER_SECONDS


def _latest_skill_git_commit_full(skill_id: str, fallback: str | None = None) -> str | None:
    try:
        from app.skills.core.git_service import git_service

        logs = git_service.log(skill_id=skill_id, max_count=1)
        if logs:
            return str(logs[0].get("hash_full") or "").strip() or fallback
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 skill git commit 失败 skill={}: {}", skill_id, exc)
    return fallback


def _timeout_error(
    *,
    phase: str,
    timeout_seconds: float,
    skill_id: str,
    run_id: str | None = None,
    total_timeout_seconds: float | None = None,
) -> AppError:
    detail = {
        "phase": phase,
        "timeout_seconds": round(float(timeout_seconds), 3),
        "skill_id": skill_id,
    }
    if run_id:
        detail["run_id"] = run_id
    if total_timeout_seconds is not None:
        detail["total_timeout_seconds"] = round(float(total_timeout_seconds), 3)
    return AppError("EXECUTION_TIMEOUT", 504, detail)


def _coerce_int_setting(value: object, default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _notification_recipients(value: object) -> tuple[list[str], list[str]]:
    if not isinstance(value, dict):
        return [], []
    user_ids = _string_list(
        value.get("user_ids")
        or value.get("users")
        or value.get("recipient_user_ids")
    )
    dingtalk_user_ids = _string_list(
        value.get("dingtalk_user_ids")
        or value.get("dingtalkUserIds")
    )
    return user_ids, dingtalk_user_ids


def _notification_content(item: dict) -> tuple[str, str]:
    title = str(item.get("title") or "").strip()[:100]
    markdown = str(item.get("markdown") or item.get("content_markdown") or item.get("content") or "").strip()
    if not markdown and isinstance(item.get("payload"), dict):
        markdown = str(item["payload"].get("markdown") or "").strip()
    return title, markdown[:6000]


async def _enqueue_output_notifications(
    *,
    run_id: str,
    skill_id: str,
    decision_log_id: int | None,
    output: dict | None,
) -> dict:
    if not run_id or not isinstance(output, dict):
        return {"status": "skipped", "reason": "output_not_dict"}
    raw_items = output.get("notifications")
    if not isinstance(raw_items, list) or not raw_items:
        return {"status": "skipped", "reason": "no_notifications"}

    from app.auth.models import User
    from app.dingtalk.models import DingTalkOutbox
    from app.dingtalk.recipients import resolve_work_notice_user

    queued = 0
    skipped = 0
    async with _sf()() as session:
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                skipped += 1
                continue
            channel = str(raw.get("channel") or raw.get("type") or "").strip()
            if channel not in {"dingtalk_work_notice", "work_notice", "dingtalk_markdown"}:
                skipped += 1
                continue
            title, markdown = _notification_content(raw)
            recipients = raw.get("recipients") if isinstance(raw.get("recipients"), dict) else raw
            user_ids, dingtalk_user_ids = _notification_recipients(recipients)
            if not title or not markdown or (not user_ids and not dingtalk_user_ids):
                skipped += 1
                continue
            priority = 5
            try:
                priority = max(1, min(9, int(raw.get("priority", 5))))
            except (TypeError, ValueError):
                priority = 5
            related_id = _bounded_dingtalk_related_id(
                str(raw.get("idempotency_key") or raw.get("idempotencyKey") or f"{run_id}:{index}")
            )
            existing = await session.scalar(
                select(DingTalkOutbox.id)
                .where(DingTalkOutbox.message_type == "work_notice")
                .where(DingTalkOutbox.related_type == "skill_output_notification")
                .where(DingTalkOutbox.related_id == related_id)
                .where(DingTalkOutbox.status.in_(["pending", "sending", "sent"]))
                .limit(1)
            )
            if existing:
                skipped += 1
                continue

            recipient_dingtalk_ids: list[str] = []
            seen: set[str] = set()
            for user_id in user_ids:
                user = await session.get(User, user_id)
                user = await resolve_work_notice_user(session, user)
                dingtalk_user_id = str(getattr(user, "dingtalk_user_id", "") or "").strip() if user else ""
                if dingtalk_user_id and dingtalk_user_id not in seen:
                    seen.add(dingtalk_user_id)
                    recipient_dingtalk_ids.append(dingtalk_user_id)
            for dingtalk_user_id in dingtalk_user_ids:
                if dingtalk_user_id not in seen:
                    seen.add(dingtalk_user_id)
                    recipient_dingtalk_ids.append(dingtalk_user_id)

            if not recipient_dingtalk_ids:
                skipped += 1
                continue

            payload = {
                "title": title,
                "markdown": markdown,
                "source": {
                    "skill_id": skill_id,
                    "run_id": run_id,
                    "decision_log_id": decision_log_id,
                    "notification_index": index,
                },
            }
            buttons = raw.get("buttons")
            if isinstance(buttons, list):
                payload["buttons"] = [
                    {"title": str(button.get("title") or "")[:50], "url": str(button.get("url") or "")[:500]}
                    for button in buttons[:5]
                    if isinstance(button, dict) and button.get("title") and button.get("url")
                ]
            for recipient in recipient_dingtalk_ids:
                await outbox.enqueue(
                    "work_notice",
                    recipient,
                    payload,
                    priority=priority,
                    related_type="skill_output_notification",
                    related_id=related_id,
                    session=session,
                )
                queued += 1
        await session.commit()
    return {"status": "processed", "queued": queued, "skipped": skipped}


def _is_timeout_error(exc: Exception) -> bool:
    return isinstance(exc, AppError) and exc.code == "EXECUTION_TIMEOUT"


def _exception_text(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return f"{exc.code}: {exc.detail or exc.message}"
    return str(exc)


def _link_decline_operator_retry_delay_seconds(exc: Exception, task: dict) -> int | None:
    code = exc.code if isinstance(exc, AppError) else ""
    if code not in _LINK_DECLINE_OPERATOR_TRANSIENT_ERROR_CODES:
        return None
    retry_count = int(task.get("retry_count") or 0)
    index = min(max(retry_count, 0), len(_LINK_DECLINE_OPERATOR_TRANSIENT_RETRY_SECONDS) - 1)
    return _LINK_DECLINE_OPERATOR_TRANSIENT_RETRY_SECONDS[index]


def _schedule_operator_trigger(awaitable) -> None:
    task = asyncio.create_task(awaitable, name="link_decline_operator_trigger")
    _operator_trigger_tasks.add(task)

    def _done(done_task: asyncio.Task) -> None:
        _operator_trigger_tasks.discard(done_task)
        try:
            done_task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("link decline operator background trigger failed: {}", exc)

    task.add_done_callback(_done)


async def _notify_link_decline_operator_success(
    *,
    run_id: str,
    decision_log_id: int | None,
    output: dict | None,
    todo_count: int | None,
    recipient_query: str | None = None,
    fallback_dingtalk_user_id: str | None = None,
    related_id: str | None = None,
) -> dict:
    if not run_id:
        return {"status": "skipped", "reason": "missing_run_id"}
    if not isinstance(output, dict):
        return {"status": "skipped", "reason": "output_not_dict"}

    from app.auth.models import User
    from app.dingtalk.models import DingTalkOutbox
    from app.dingtalk.recipients import resolve_work_notice_user

    query = (recipient_query or LINK_DECLINE_OPERATOR_SUCCESS_RECIPIENT_QUERY).strip()
    fallback_dingtalk_user_id = (fallback_dingtalk_user_id or LINK_DECLINE_OPERATOR_SUCCESS_DINGTALK_USER_ID).strip()
    if not query and not fallback_dingtalk_user_id:
        return {"status": "skipped", "reason": "recipient_not_configured"}
    recipient_filters = []
    if query:
        recipient_filters.extend((
            User.name.ilike(f"%{query}%"),
            User.username.ilike(f"%{query}%"),
            User.id.ilike(f"%{query}%"),
            User.department.ilike(f"%{query}%"),
        ))
    if fallback_dingtalk_user_id:
        recipient_filters.append(User.dingtalk_user_id == fallback_dingtalk_user_id)
    dedupe_related_id = _bounded_dingtalk_related_id(related_id or run_id)
    async with _sf()() as session:
        existing = await session.scalar(
            select(DingTalkOutbox.id)
            .where(DingTalkOutbox.message_type == "work_notice")
            .where(DingTalkOutbox.related_type == LINK_DECLINE_OPERATOR_SUCCESS_RELATED_TYPE)
            .where(DingTalkOutbox.related_id == dedupe_related_id)
            .where(DingTalkOutbox.status.in_(["pending", "sending", "sent"]))
            .limit(1)
        )
        if existing:
            return {"status": "skipped", "reason": "duplicate", "outbox_id": existing}

        user = (
            await session.execute(
                select(User)
                .where(User.is_active == True)  # noqa: E712
                .where(User.state == "active")
                .where(User.dingtalk_user_id.is_not(None))
                .where(User.dingtalk_user_id != "")
                .where(or_(*recipient_filters))
                .order_by(User.name.asc(), User.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        user = await resolve_work_notice_user(session, user)
        recipient_dingtalk_user_id = user.dingtalk_user_id if user and user.dingtalk_user_id else fallback_dingtalk_user_id
        recipient_user_id = user.id if user else None
        if not recipient_dingtalk_user_id:
            return {"status": "skipped", "reason": "recipient_not_found"}
        if not user:
            logger.info(
                "Operator 成功通知使用固定钉钉收件人 userId={} query={}",
                fallback_dingtalk_user_id,
                query,
            )

        reports = output.get("reports") if isinstance(output.get("reports"), list) else []
        actions = output.get("actions") if isinstance(output.get("actions"), list) else []
        first_report = reports[0] if reports and isinstance(reports[0], dict) else {}
        title = str(first_report.get("title") or "天猫链接下滑诊断日报")
        summary = str(first_report.get("summary") or (output.get("诊断报告") or {}).get("store_main_cause") or "").strip()
        report_count = len(reports)
        todo_summary = await _operator_todo_priority_summary(
            session,
            run_id=run_id,
            output=output,
            fallback_todo_count=todo_count,
        )
        resolved_todo_count = int(todo_summary["total"])
        priority_counts = todo_summary["priority_counts"]
        action_count = len(actions)
        detail_path = f"/execution/{run_id}"
        priority_line = "、".join(
            f"{level} {int(priority_counts.get(level) or 0)}"
            for level in _TODO_PRIORITY_LEVELS
        )
        markdown = (
            f"### {title}\n\n"
            f"- run_id：{run_id}\n"
            f"- decision_log_id：{decision_log_id or '-'}\n"
            f"- 报告数：{report_count}\n"
            f"- 形成待办：{resolved_todo_count} 条\n"
            f"- 优先级分布：{priority_line}\n"
            f"- 动作数：{action_count}\n"
            + (f"- 摘要：{summary[:300]}\n" if summary else "")
        )
        msg_id = await outbox.enqueue(
            "work_notice",
            recipient_dingtalk_user_id,
            {
                "title": title,
                "markdown": markdown,
                "buttons": [{"title": "查看执行结果", "url": detail_path}],
            },
            priority=5,
            related_type=LINK_DECLINE_OPERATOR_SUCCESS_RELATED_TYPE,
            related_id=dedupe_related_id,
            session=session,
        )
        await session.commit()
        return {
            "status": "enqueued",
            "outbox_id": msg_id,
            "recipient_user_id": recipient_user_id,
            "recipient_dingtalk_user_id": recipient_dingtalk_user_id,
            "todo_count": resolved_todo_count,
            "priority_counts": priority_counts,
            "detail_path": detail_path,
        }


class ExecutionService:
    """Skill执行编排服务"""

    COLLECTOR_SKILL_ID = "tmall-link-decline-collector-v1"
    OPERATOR_SKILL_ID = "tmall-link-decline-operator-v1"
    OPERATOR_TRIGGER = "event:tmall-link-decline-collector-v1:completed"
    OPERATOR_TRIGGER_TASK_TYPE = "link_decline_operator_trigger"
    COLLECTION_SCHEMA = "tmall_link_decline_collection_v1"

    async def _apply_department_analysis_agent_control(
        self,
        session,
        *,
        skill: Skill,
        params: dict,
    ) -> tuple[dict, dict | None]:
        from app.agents.models import DepartmentAnalysisAgent
        from app.agents.router import DEFAULT_ANALYSIS_PARAMS

        explicit_agent = params.get("analysis_agent") if isinstance(params.get("analysis_agent"), dict) else {}
        explicit_agent = explicit_agent or (params.get("_analysis_agent") if isinstance(params.get("_analysis_agent"), dict) else {})
        requested_agent_id = str(
            params.get("_analysis_agent_id")
            or params.get("analysis_agent_id")
            or explicit_agent.get("id")
            or ""
        ).strip()
        stmt = (
            select(DepartmentAnalysisAgent)
            .where(DepartmentAnalysisAgent.skill_id == skill.id)
            .where(DepartmentAnalysisAgent.is_active == True)  # noqa: E712
            .where(DepartmentAnalysisAgent.status == "active")
            .order_by(DepartmentAnalysisAgent.updated_at.desc())
            .limit(1)
        )
        if requested_agent_id:
            stmt = stmt.where(DepartmentAnalysisAgent.id == requested_agent_id)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return params, None

        agent_defaults = dict(DEFAULT_ANALYSIS_PARAMS)
        if isinstance(row.default_params_json, dict):
            agent_defaults.update(row.default_params_json)
        if isinstance(explicit_agent.get("default_params"), dict):
            agent_defaults.update(explicit_agent["default_params"])

        merged = dict(agent_defaults)
        merged.update(params)

        dimensions = _string_list(explicit_agent.get("dimensions")) or _string_list(row.dimensions_json)
        if "dimensions" not in params and dimensions:
            merged["dimensions"] = dimensions
        prompt_version = str(
            params.get("prompt_version")
            or explicit_agent.get("prompt_version")
            or row.prompt_version
            or "analysis_v1"
        ).strip()
        if prompt_version:
            merged["prompt_version"] = prompt_version

        merged["top_n"] = _coerce_int_setting(merged.get("top_n"), 5, min_value=1, max_value=20)
        merged["video_max_pages"] = _coerce_int_setting(merged.get("video_max_pages"), 10, min_value=1, max_value=50)
        merged["audit_max_pages"] = _coerce_int_setting(merged.get("audit_max_pages"), 6, min_value=1, max_value=30)
        merged["audit_reject_detail_limit"] = _coerce_int_setting(
            merged.get("audit_reject_detail_limit"),
            8,
            min_value=0,
            max_value=100,
        )
        merged["window_days"] = _coerce_int_setting(merged.get("window_days"), 7, min_value=1, max_value=30)
        if "output_sections" in merged:
            merged["output_sections"] = _string_list(merged.get("output_sections"))
        if "todo_enabled" in merged:
            merged["todo_enabled"] = bool(merged.get("todo_enabled"))

        control_meta = {
            "id": row.id,
            "name": row.name,
            "department_id": row.department_id,
            "department": row.department,
            "skill_id": row.skill_id,
            "prompt_version": prompt_version,
            "dimensions": dimensions,
            "default_params": agent_defaults,
            "effective_params": {
                key: merged.get(key)
                for key in (
                    "window",
                    "window_days",
                    "timezone",
                    "same_topic_rule",
                    "top_n",
                    "video_max_pages",
                    "audit_max_pages",
                    "audit_reject_detail_limit",
                    "low_spend_rule",
                    "output_sections",
                    "todo_enabled",
                    "report_channel",
                    "model_profile",
                )
                if key in merged
            },
        }
        merged["analysis_agent"] = control_meta
        merged["_analysis_agent_id"] = row.id
        merged["_analysis_agent_controlled"] = True
        return merged, control_meta

    async def execute_skill(
        self,
        skill_id: str,
        params: dict | None = None,
        sandbox: bool = False,
        triggered_by: str = "scheduler",
        run_mode: str | None = None,
        parent_run_id: str | None = None,
        batch_id: str | None = None,
        data_proofs: list[dict] | None = None,
        sample_used: bool | None = None,
        run_metadata: dict | None = None,
    ) -> dict:
        """完整执行链路（含数据质量门禁）"""
        run_id = str(uuid4())
        params = params or {}
        resolved_run_mode = normalize_run_mode(run_mode, sandbox=sandbox, triggered_by=triggered_by)
        normalized_data_proofs = normalize_data_proofs(data_proofs)
        resolved_sample_used = _execution_sample_used(resolved_run_mode, normalized_data_proofs, sample_used)
        safe_run_metadata = run_metadata if isinstance(run_metadata, dict) else {}

        # 0. 数据质量门禁（非沙箱模式）
        if not sandbox:
            try:
                quality_check = await self._await_with_timeout(
                    self._pre_check_data_quality(skill_id),
                    phase="data_quality_check",
                    timeout_seconds=DATA_QUALITY_CHECK_TIMEOUT_SECONDS,
                    skill_id=skill_id,
                    run_id=run_id,
                )
            except AppError as exc:
                if _is_timeout_error(exc):
                    await self._persist_timeout_before_start(
                        run_id=run_id,
                        skill_id=skill_id,
                        triggered_by=triggered_by,
                        resolved_run_mode=resolved_run_mode,
                        parent_run_id=parent_run_id,
                        batch_id=batch_id,
                        normalized_data_proofs=normalized_data_proofs,
                        resolved_sample_used=resolved_sample_used,
                        detail=exc.detail or {},
                    )
                raise

            if not quality_check["passed"]:
                # 记录被阻止的执行
                blocked_at = now_bjt()
                async with _sf()() as session:
                    run = ExecutionRun(
                        id=run_id,
                        skill_id=skill_id,
                        trigger_type=triggered_by,
                        run_mode=resolved_run_mode,
                        parent_run_id=parent_run_id,
                        started_at=blocked_at,
                        completed_at=blocked_at,
                        status="blocked",
                        batch_id=batch_id,
                        metadata_json={
                            "data_proofs": normalized_data_proofs,
                            "sample_used": resolved_sample_used,
                            **safe_run_metadata,
                        },
                    )
                    session.add(run)
                    await session.commit()
                await _invalidate_execution_cache()

                # 任务树写入走 fire-and-forget，失败自动入 repair_queue
                await tasktree_dispatcher.schedule_writer(
                    _make_create_node_coro(
                        run_id=run_id,
                        department_id="unknown",
                        title=skill_id,
                        source_instance_id=None,
                        status="blocked",
                        started_at=blocked_at,
                        finished_at=blocked_at,
                    ),
                    source_type="execution_blocked",
                    source_ref=run_id,
                    operation="create_execution_node",
                    payload={
                        "run_id": run_id,
                        "department_id": "unknown",
                        "title": skill_id,
                        "status": "blocked",
                        "started_at": serialize_datetime(blocked_at),
                        "finished_at": serialize_datetime(blocked_at),
                    },
                )

                await audit.log("svc_execution", "execution.blocked", "skill", skill_id,
                                detail={"run_id": run_id, "reasons": quality_check["reasons"]})

                return {
                    "run_id": run_id,
                    "status": "blocked",
                    "reasons": quality_check["reasons"],
                    "run_mode": resolved_run_mode,
                    "data_proofs": normalized_data_proofs,
                    "sample_used": resolved_sample_used,
                }

        # 1. 获取Skill元信息 + 记录执行开始（单事务，避免不一致）
        async with _sf()() as session:
            result = await session.execute(select(Skill).where(Skill.id == skill_id))
            skill = result.scalar_one_or_none()

            if not skill:
                logger.error(f"Skill不存在: {skill_id}")
                raise AppError("SKILL_NOT_FOUND", 404)

            # 提取后续需要的属性（session关闭后ORM对象不可访问）
            skill_name = skill.name
            skill_approval_level = skill.approval_level
            skill_target_users = skill.target_users or []
            skill_owner = skill.owner
            skill_department = skill.department
            skill_git_commit_full = _latest_skill_git_commit_full(skill_id, skill.git_commit)
            params, analysis_agent_control = await self._apply_department_analysis_agent_control(
                session,
                skill=skill,
                params=params,
            )

            skill_frontmatter = {}
            try:
                from app.skills.core.git_service import git_service
                from app.skills.core.parser import skill_parser

                skill_md = git_service.read_file(skill_id, "SKILL.md")
                if skill_md:
                    skill_frontmatter = skill_parser.parse(skill_md).frontmatter or {}
            except Exception as e:
                logger.warning("读取 SKILL.md frontmatter 失败: {}", e)

            skill_meta = {
                "id": skill_id,
                "name": skill_name,
                "department": skill_department,
                "approval_level": skill_approval_level,
                "target_users": skill_target_users,
                "owner": skill_owner,
            }
            skill_meta.update(skill_frontmatter)
            from app.execution.sync_service import _normalize_runtime_config

            runtime_config = _normalize_runtime_config(skill_frontmatter)
            runtime_declared = isinstance(skill_frontmatter, dict) and skill_frontmatter.get("runtime") is not None
            execution_cfg = params.get("_execution") if isinstance(params.get("_execution"), dict) else {}
            execution_backend = str(
                params.get("_execution_backend")
                or execution_cfg.get("backend")
                or (runtime_config.get("backend") if runtime_declared else "")
                or ""
            ).lower()
            script_instance_id = (
                params.get("_aiclaw_instance_id")
                or execution_cfg.get("instance_id")
                or (skill_frontmatter.get("instance_id") if isinstance(skill_frontmatter, dict) else None)
                or (params.get("instance_id") if execution_backend in {"bridge_script", "openclaw_agent", "hybrid"} else None)
            )
            script_path = (
                params.get("_script_path")
                or execution_cfg.get("script_path")
                or runtime_config.get("script_entry")
                or "scripts/main.py"
            )
            script_timeout = (
                params.get("_script_timeout")
                or execution_cfg.get("timeout")
                or runtime_config.get("timeout")
                or 300
            )
            use_agent_runtime = execution_backend in {"openclaw_agent", "hybrid"}
            use_bridge_script = not use_agent_runtime and (execution_backend == "bridge_script" or bool(script_instance_id))
            source_instance_id = (
                script_instance_id
                if (use_agent_runtime or use_bridge_script)
                else (
                    params.get("_aiclaw_instance_id")
                    or execution_cfg.get("instance_id")
                    or (skill_frontmatter.get("instance_id") if isinstance(skill_frontmatter, dict) else None)
                    or params.get("instance_id")
                )
            )
            if use_bridge_script:
                if not script_instance_id:
                    script_instance_id = await self._pick_bridge_script_instance(
                        skill_department=skill_department
                    )
                    source_instance_id = script_instance_id
                await self._ensure_skill_runtime_instance(script_instance_id)
                await self._ensure_bridge_script_online(script_instance_id)
            run_token = issue_run_token(
                skill_id=skill_id,
                run_id=run_id,
                instance_id=source_instance_id,
                skill_git_commit_full=skill_git_commit_full,
                department=skill_department,
                skill_department=skill_department,
            )

            started_at = now_bjt()
            run = ExecutionRun(
                id=run_id,
                skill_id=skill_id,
                trigger_type=triggered_by,
                run_mode=resolved_run_mode,
                parent_run_id=parent_run_id,
                started_at=started_at,
                status="running",
                batch_id=batch_id,
                source_instance_id=source_instance_id,
                metadata_json={
                    "data_proofs": normalized_data_proofs,
                    "sample_used": resolved_sample_used,
                    **safe_run_metadata,
                    **({"analysis_agent_control": analysis_agent_control} if analysis_agent_control else {}),
                    "skill_git_commit_full": skill_git_commit_full,
                    "run_token_claim": {
                        "skill_id": skill_id,
                        "run_id": run_id,
                        "instance_id": source_instance_id,
                        "skill_git_commit_full": skill_git_commit_full,
                        "department": skill_department,
                    },
                },
            )
            session.add(run)
            await session.commit()

        # 任务树写入解耦：fire-and-forget，失败入 repair_queue
        await tasktree_dispatcher.schedule_writer(
            _make_create_node_coro(
                run_id=run_id,
                department_id=skill_department or "unknown",
                title=skill_name,
                source_instance_id=source_instance_id,
                status="running",
                started_at=started_at,
            ),
            source_type="execution_start",
            source_ref=run_id,
            operation="create_execution_node",
            payload={
                "run_id": run_id,
                "department_id": skill_department or "unknown",
                "title": skill_name,
                "source_instance_id": source_instance_id,
                "status": "running",
                "started_at": serialize_datetime(started_at),
            },
        )

        # 2. 执行 Skill。生产级执行优先走远端 runtime，避免 chat.send 让 LLM 生成样例结果。
        runtime_phase_timeout = (
            _bridge_script_phase_timeout(script_timeout)
            if use_bridge_script or use_agent_runtime
            else DEFAULT_RUN_SKILL_TIMEOUT_SECONDS
        )
        core_budget = _TimeoutBudget(_core_execution_total_timeout(runtime_phase_timeout))
        try:
            if use_agent_runtime:
                output = await self._await_with_timeout(
                    self._run_skill_via_agent_runtime(
                        skill_id=skill_id,
                        params=params,
                        run_id=run_id,
                        run_token=run_token,
                        run_mode=resolved_run_mode,
                        skill_git_commit_full=skill_git_commit_full,
                        instance_id=script_instance_id,
                        skill_department=skill_department,
                        runtime_config=runtime_config,
                        script_path=script_path,
                        timeout=script_timeout,
                    ),
                    phase="openclaw_agent",
                    timeout_seconds=runtime_phase_timeout,
                    skill_id=skill_id,
                    run_id=run_id,
                    budget=core_budget,
                )
            elif use_bridge_script:
                output = await self._await_with_timeout(
                    self._run_skill_via_bridge_script(
                        skill_id=skill_id,
                        params=params,
                        run_id=run_id,
                        run_token=run_token,
                        run_mode=resolved_run_mode,
                        skill_git_commit_full=skill_git_commit_full,
                        instance_id=script_instance_id,
                        skill_department=skill_department,
                        script_path=script_path,
                        timeout=script_timeout,
                    ),
                    phase="bridge_script",
                    timeout_seconds=runtime_phase_timeout,
                    skill_id=skill_id,
                    run_id=run_id,
                    budget=core_budget,
                )
            else:
                output = await self._await_with_timeout(
                    default_client.run_skill(
                        skill_id,
                        params,
                        sandbox=sandbox,
                    ),
                    phase="run_skill",
                    timeout_seconds=runtime_phase_timeout,
                    skill_id=skill_id,
                    run_id=run_id,
                    budget=core_budget,
                )
            if not sandbox and output_declares_sample(output):
                raise AppError(
                    "SAMPLE_OUTPUT_IN_REAL_RUN",
                    422,
                    {"detail": "非沙箱执行返回了 sample/sandbox 输出，已拒绝入库为真实结果"},
                )

            output, decision_log_id, completed_at, final_data_proofs, final_sample_used = await self._await_with_timeout(
                self._persist_execution_success(
                    run_id=run_id,
                    skill_id=skill_id,
                    params=params,
                    output=output,
                    resolved_run_mode=resolved_run_mode,
                    parent_run_id=parent_run_id,
                    batch_id=batch_id,
                    normalized_data_proofs=normalized_data_proofs,
                    resolved_sample_used=resolved_sample_used,
                    skill_approval_level=skill_approval_level,
                    sandbox=sandbox,
                ),
                phase="persist_execution_result",
                timeout_seconds=PERSIST_RESULT_TIMEOUT_SECONDS,
                skill_id=skill_id,
                run_id=run_id,
                budget=core_budget,
            )
        except Exception as e:
            await self._finalize_run_failure(
                run_id=run_id,
                skill_id=skill_id,
                skill_department=skill_department,
                error=e,
            )
            raise

        if not sandbox:
            try:
                await self._await_with_timeout(
                    _emit_mcp_runtime_alerts(
                        run_id=run_id,
                        skill_id=skill_id,
                        output=output,
                        decision_log_id=decision_log_id,
                    ),
                    phase="mcp_runtime_alert",
                    timeout_seconds=5.0,
                    skill_id=skill_id,
                    run_id=run_id,
                )
            except Exception as alert_err:
                logger.warning("MCP runtime 告警写入失败 skill={} run={} err={}", skill_id, run_id, alert_err)

        await _invalidate_generated_data_cache_if_needed(output)

        await tasktree_dispatcher.schedule_writer(
            _make_finish_node_coro(
                run_id=run_id,
                status="completed",
                finished_at=completed_at,
            ),
            source_type="execution_finish",
            source_ref=run_id,
            operation="finish_execution_node",
            payload={
                "run_id": run_id,
                "status": "completed",
                "finished_at": serialize_datetime(completed_at),
            },
        )

        await audit.log(
            "svc_execution",
            "execution.complete",
            "skill",
            skill_id,
            detail={"run_id": run_id, "sandbox": sandbox},
        )

        # 契约运行时校验（warn-only，不阻塞业务）
        try:
            await self._await_with_timeout(
                _check_contract_drift(skill_id, run_id, output),
                phase="contract_drift_check",
                timeout_seconds=CONTRACT_DRIFT_TIMEOUT_SECONDS,
                skill_id=skill_id,
                run_id=run_id,
            )
        except Exception as drift_err:
            logger.warning("contract drift check 失败 skill={} run={} err={}", skill_id, run_id, drift_err)

        todo_request_created = False
        todo_count = 0
        todo_error = None
        if not sandbox:
            try:
                todo_request_created, todo_count = await self._await_with_timeout(
                    self._create_todos_for_execution(
                        run_id=run_id,
                        skill_id=skill_id,
                        skill_meta=skill_meta,
                        params=params,
                        output=output,
                        decision_log_id=decision_log_id,
                    ),
                    phase="todo_create",
                    timeout_seconds=TODO_CREATE_TIMEOUT_SECONDS,
                    skill_id=skill_id,
                    run_id=run_id,
                )
            except Exception as e:
                if isinstance(e, AppError):
                    todo_error = {
                        "code": e.code,
                        "message": e.message,
                        "detail": e.detail or {},
                    }
                else:
                    todo_error = {
                        "code": "TODO_CREATE_FAILED",
                        "message": str(e),
                        "detail": {},
                    }
                logger.warning(
                    "创建 AI 待办失败 skill={} run={} error={}",
                    skill_id,
                    run_id,
                    e,
                )

        if not sandbox and skill_id == self.OPERATOR_SKILL_ID:
            try:
                await _notify_link_decline_operator_success(
                    run_id=run_id,
                    decision_log_id=decision_log_id,
                    output=output,
                    todo_count=todo_count,
                )
            except Exception as notify_err:  # noqa: BLE001
                logger.warning(
                    "Operator 成功通知入队失败 skill={} run={} error={}",
                    skill_id,
                    run_id,
                    notify_err,
                )

        if not sandbox:
            try:
                await self._await_with_timeout(
                    _enqueue_output_notifications(
                        run_id=run_id,
                        skill_id=skill_id,
                        decision_log_id=decision_log_id,
                        output=output,
                    ),
                    phase="output_notifications",
                    timeout_seconds=5.0,
                    skill_id=skill_id,
                    run_id=run_id,
                )
            except Exception as notify_err:  # noqa: BLE001
                logger.warning(
                    "Skill 输出通知入队失败 skill={} run={} error={}",
                    skill_id,
                    run_id,
                    notify_err,
                )

        # 4. 非沙箱模式也不直接由 Skill 脚本推钉钉。Skill 运行结果统一先进入平台；
        # 受控系统通知与待办派发均由平台 outbox 处理。

        # 5. B5: AI 生成执行摘要（非沙箱）
        if not sandbox:
            try:
                await self._await_with_timeout(
                    self._generate_execution_summary(
                        run_id=run_id,
                        skill_name=skill_name,
                        output=output,
                    ),
                    phase="execution_summary",
                    timeout_seconds=EXECUTION_SUMMARY_TIMEOUT_SECONDS,
                    skill_id=skill_id,
                    run_id=run_id,
                )
            except Exception as e:
                logger.warning("AI 摘要生成失败: {}", e)

        try:
            from app.tasktree.service import invalidate_tasktree
            await invalidate_tasktree(skill_department)
        except Exception as inv_err:
            logger.warning(
                "invalidate_tasktree 失败 (run_id={} dept={}): {}",
                run_id,
                skill_department,
                inv_err,
                exc_info=True,
            )

        if not sandbox:
            await self.enqueue_link_decline_operator_trigger_if_needed(
                collector_skill_id=skill_id,
                collector_run_id=run_id,
                collector_decision_log_id=decision_log_id,
                collector_output=output,
                created_by="execution.local_complete",
            )

        await _invalidate_execution_cache()
        return {
            "run_id": run_id,
            "output": output,
            "run_mode": resolved_run_mode,
            "data_proofs": final_data_proofs,
            "sample_used": final_sample_used,
            "todo_created": todo_request_created,
            "todo_count": todo_count,
            "todo_error": todo_error,
            "run_token": run_token,
            "skill_git_commit_full": skill_git_commit_full,
        }

    async def _trigger_link_decline_operator_if_needed(
        self,
        *,
        collector_skill_id: str,
        collector_run_id: str,
        collector_decision_log_id: int | None,
        collector_output: dict | None,
    ) -> None:
        """Backward-compatible direct trigger used by focused tests and repair code.

        Normal collector completion should call
        enqueue_link_decline_operator_trigger_if_needed() so the trigger is
        durable across web process restarts.
        """
        await self._run_link_decline_operator_trigger(
            collector_skill_id=collector_skill_id,
            collector_run_id=collector_run_id,
            collector_decision_log_id=collector_decision_log_id,
            collector_output=collector_output,
            trigger_source="direct",
        )

    async def enqueue_link_decline_operator_trigger_if_needed(
        self,
        *,
        collector_skill_id: str,
        collector_run_id: str,
        collector_decision_log_id: int | None,
        collector_output: dict | None,
        created_by: str = "system",
        process_now: bool = True,
        operator_triggered_by: str | None = None,
    ) -> dict:
        if collector_skill_id != self.COLLECTOR_SKILL_ID:
            return {"status": "skipped", "reason": "not_collector"}
        if not isinstance(collector_output, dict):
            return {"status": "skipped", "reason": "collector_output_not_dict"}
        if collector_output.get("collection_schema") != self.COLLECTION_SCHEMA:
            return {"status": "skipped", "reason": "schema_mismatch"}

        from app.execution.queue_models import ExecutionQueueTask
        from app.execution.task_queue import task_queue

        async with _sf()() as session:
            result = await session.execute(
                select(Skill.status).where(Skill.id == self.OPERATOR_SKILL_ID).limit(1)
            )
            operator_status = result.scalar_one_or_none()
            if operator_status is None:
                logger.warning(
                    "link decline operator skill not found, skip enqueue parent_run_id={}",
                    collector_run_id,
                )
                await self._audit_link_decline_operator_trigger(
                    "execution.link_decline_operator.skipped",
                    collector_run_id,
                    {"reason": "operator_not_found", "created_by": created_by},
                )
                return {"status": "skipped", "reason": "operator_not_found"}
            if operator_status != "active":
                logger.warning(
                    "link decline operator skill is not active, skip enqueue parent_run_id={} status={}",
                    collector_run_id,
                    operator_status,
                )
                await self._audit_link_decline_operator_trigger(
                    "execution.link_decline_operator.skipped",
                    collector_run_id,
                    {"reason": "operator_not_active", "operator_status": operator_status, "created_by": created_by},
                )
                return {"status": "skipped", "reason": "operator_not_active", "operator_status": operator_status}

            existing_child = await session.execute(
                select(ExecutionRun.id, ExecutionRun.status)
                .where(ExecutionRun.skill_id == self.OPERATOR_SKILL_ID)
                .where(ExecutionRun.parent_run_id == collector_run_id)
                .where(ExecutionRun.status.in_(["running", "completed"]))
                .order_by(ExecutionRun.started_at.desc())
                .limit(1)
            )
            child = existing_child.first()
            if child:
                return {
                    "status": "skipped",
                    "reason": "operator_child_exists",
                    "operator_run_id": child.id,
                    "operator_status": child.status,
                }

            existing_task = await session.execute(
                select(ExecutionQueueTask.id)
                .where(ExecutionQueueTask.task_type == self.OPERATOR_TRIGGER_TASK_TYPE)
                .where(ExecutionQueueTask.status.in_(["pending", "running"]))
                .where(ExecutionQueueTask.payload["collector_run_id"].as_string() == collector_run_id)
                .order_by(ExecutionQueueTask.id.asc())
                .limit(1)
            )
            existing_task_id = existing_task.scalar_one_or_none()
            if existing_task_id:
                return {"status": "queued", "task_id": existing_task_id, "idempotent": True}

        payload = {
            "collector_skill_id": collector_skill_id,
            "collector_run_id": collector_run_id,
            "collector_decision_log_id": collector_decision_log_id,
            "collection_schema": collector_output.get("collection_schema"),
        }
        if operator_triggered_by:
            payload["operator_triggered_by"] = operator_triggered_by
        task_id = await task_queue.enqueue(
            task_type=self.OPERATOR_TRIGGER_TASK_TYPE,
            payload=payload,
            skill_id=self.OPERATOR_SKILL_ID,
            priority=3,
            queue_name="default",
            timeout_seconds=2100,
            max_retries=_LINK_DECLINE_OPERATOR_MAX_RETRIES,
            created_by=created_by,
        )
        logger.info(
            "link decline operator trigger enqueued task_id={} parent_run_id={} decision_log_id={}",
            task_id,
            collector_run_id,
            collector_decision_log_id,
        )
        await self._audit_link_decline_operator_trigger(
            "execution.link_decline_operator.enqueued",
            collector_run_id,
            {
                "task_id": task_id,
                "collector_decision_log_id": collector_decision_log_id,
                "created_by": created_by,
            },
        )
        if process_now:
            _schedule_operator_trigger(
                self.process_link_decline_operator_queue_once(
                    worker_id=f"link-decline-trigger-{task_id}",
                )
            )
        return {"status": "enqueued", "task_id": task_id, "idempotent": False}

    async def trigger_link_decline_operator_from_decision_log(
        self,
        *,
        collector_run_id: str,
        collector_decision_log_id: int | None = None,
        trigger_source: str = "queue",
        operator_triggered_by: str | None = None,
    ) -> dict:
        decision = None
        async with _sf()() as session:
            stmt = select(DecisionLog).where(DecisionLog.skill_id == self.COLLECTOR_SKILL_ID)
            if collector_decision_log_id is not None:
                stmt = stmt.where(DecisionLog.id == collector_decision_log_id)
            else:
                stmt = stmt.where(DecisionLog.run_id == collector_run_id)
            stmt = stmt.order_by(DecisionLog.id.desc()).limit(1)
            decision = (await session.execute(stmt)).scalar_one_or_none()

        if decision is None:
            raise AppError(
                "EXECUTION_DATA_MISSING",
                404,
                {
                    "detail": "collector DecisionLog 不存在，无法补偿触发 operator",
                    "collector_run_id": collector_run_id,
                    "collector_decision_log_id": collector_decision_log_id,
                },
            )
        if decision.run_id and decision.run_id != collector_run_id:
            raise AppError(
                "PARAM_INVALID",
                400,
                {
                    "detail": "collector_decision_log_id 与 collector_run_id 不匹配",
                    "collector_run_id": collector_run_id,
                    "decision_log_run_id": decision.run_id,
                    "collector_decision_log_id": decision.id,
                },
            )

        return await self._run_link_decline_operator_trigger(
            collector_skill_id=self.COLLECTOR_SKILL_ID,
            collector_run_id=collector_run_id,
            collector_decision_log_id=decision.id,
            collector_output=decision.output_result,
            trigger_source=trigger_source,
            operator_triggered_by=operator_triggered_by,
        )

    async def process_link_decline_operator_queue_once(
        self,
        *,
        worker_id: str = "link-decline-operator-worker",
    ) -> dict | None:
        from app.execution.task_queue import task_queue

        task = await task_queue.claim(
            worker_id=worker_id,
            queue_name="default",
            task_types=[self.OPERATOR_TRIGGER_TASK_TYPE],
        )
        if task is None:
            return None

        payload = task.get("payload") or {}
        try:
            result = await self.trigger_link_decline_operator_from_decision_log(
                collector_run_id=str(payload.get("collector_run_id") or ""),
                collector_decision_log_id=payload.get("collector_decision_log_id"),
                trigger_source=f"queue:{task['id']}",
                operator_triggered_by=payload.get("operator_triggered_by") or self.OPERATOR_TRIGGER,
            )
            await task_queue.complete(task["id"], result=result, worker_id=worker_id)
            return {"task_id": task["id"], **result}
        except Exception as exc:  # noqa: BLE001
            error = _exception_text(exc)
            retry_delay_seconds = _link_decline_operator_retry_delay_seconds(exc, task)
            await task_queue.fail(
                task["id"],
                error=error[:4000],
                retry_delay_seconds=retry_delay_seconds,
            )
            await self._audit_link_decline_operator_trigger(
                "execution.link_decline_operator.failed",
                str(payload.get("collector_run_id") or ""),
                {
                    "task_id": task["id"],
                    "error": error[:1000],
                    "retry_delay_seconds": retry_delay_seconds,
                    "transient": retry_delay_seconds is not None,
                },
            )
            logger.warning(
                "process link decline operator trigger task failed task_id={} parent_run_id={} retry_delay={} error={}",
                task["id"],
                payload.get("collector_run_id"),
                retry_delay_seconds,
                error,
            )
            raise

    async def _find_link_decline_baseline_collector_output(
        self,
        session,
        *,
        collector_run_id: str,
        collector_output: dict,
    ) -> dict | None:
        current_run = await session.get(ExecutionRun, collector_run_id)
        if current_run is None or current_run.started_at is None:
            return None
        target_started_at = current_run.started_at - timedelta(days=1)
        window_start = target_started_at - timedelta(minutes=_LINK_DECLINE_BASELINE_TOLERANCE_MINUTES)
        window_end = target_started_at + timedelta(minutes=_LINK_DECLINE_BASELINE_TOLERANCE_MINUTES)
        current_time = _link_decline_collection_time(collector_output)
        rows = (
            await session.execute(
                select(ExecutionRun, DecisionLog)
                .join(DecisionLog, DecisionLog.run_id == ExecutionRun.id)
                .where(ExecutionRun.skill_id == self.COLLECTOR_SKILL_ID)
                .where(DecisionLog.skill_id == self.COLLECTOR_SKILL_ID)
                .where(ExecutionRun.id != collector_run_id)
                .where(ExecutionRun.status == "completed")
                .where(ExecutionRun.started_at >= window_start)
                .where(ExecutionRun.started_at <= window_end)
                .order_by(ExecutionRun.started_at.asc(), DecisionLog.id.desc())
            )
        ).all()
        candidates: list[tuple[float, ExecutionRun, DecisionLog, dict]] = []
        for baseline_run, decision in rows:
            output = decision.output_result if isinstance(decision.output_result, dict) else {}
            if output.get("collection_schema") != self.COLLECTION_SCHEMA:
                continue
            baseline_time = _link_decline_collection_time(output)
            if current_time and baseline_time and baseline_time != current_time:
                continue
            started_at = baseline_run.started_at
            distance = abs((started_at - target_started_at).total_seconds()) if started_at else 0.0
            candidates.append((distance, baseline_run, decision, output))
        if not candidates:
            return None
        _, baseline_run, decision, output = min(candidates, key=lambda row: row[0])
        return {
            "baseline_collector_run_id": baseline_run.id,
            "baseline_collector_decision_log_id": decision.id,
            "baseline_collector_output": output,
            "baseline_collector_started_at": isoformat_bjt(baseline_run.started_at),
            "baseline_collection_time": _link_decline_collection_time(output),
        }

    async def _run_link_decline_operator_trigger(
        self,
        *,
        collector_skill_id: str,
        collector_run_id: str,
        collector_decision_log_id: int | None,
        collector_output: dict | None,
        trigger_source: str,
        operator_triggered_by: str | None = None,
    ) -> dict:
        if collector_skill_id != self.COLLECTOR_SKILL_ID:
            return {"status": "skipped", "reason": "not_collector"}
        if not isinstance(collector_output, dict):
            return {"status": "skipped", "reason": "collector_output_not_dict"}
        if collector_output.get("collection_schema") != self.COLLECTION_SCHEMA:
            return {"status": "skipped", "reason": "schema_mismatch"}

        async with _sf()() as session:
            result = await session.execute(
                select(Skill.status).where(Skill.id == self.OPERATOR_SKILL_ID).limit(1)
            )
            operator_status = result.scalar_one_or_none()
            if operator_status is None:
                await self._audit_link_decline_operator_trigger(
                    "execution.link_decline_operator.skipped",
                    collector_run_id,
                    {"reason": "operator_not_found", "trigger_source": trigger_source},
                )
                return {"status": "skipped", "reason": "operator_not_found"}
            if operator_status != "active":
                await self._audit_link_decline_operator_trigger(
                    "execution.link_decline_operator.skipped",
                    collector_run_id,
                    {
                        "reason": "operator_not_active",
                        "operator_status": operator_status,
                        "trigger_source": trigger_source,
                    },
                )
                return {"status": "skipped", "reason": "operator_not_active", "operator_status": operator_status}

            existing_child = await session.execute(
                select(ExecutionRun.id, ExecutionRun.status)
                .where(ExecutionRun.skill_id == self.OPERATOR_SKILL_ID)
                .where(ExecutionRun.parent_run_id == collector_run_id)
                .where(ExecutionRun.status.in_(["running", "completed"]))
                .order_by(ExecutionRun.started_at.desc())
                .limit(1)
            )
            existing_child_row = existing_child.first()
            if existing_child_row:
                return {
                    "status": "skipped",
                    "reason": "operator_child_exists",
                    "operator_run_id": existing_child_row.id,
                    "operator_status": existing_child_row.status,
                }
            baseline_context = await self._find_link_decline_baseline_collector_output(
                session,
                collector_run_id=collector_run_id,
                collector_output=collector_output,
            )

        collection_time = _link_decline_collection_time(collector_output)
        operator_collector_output = collector_output
        if collection_time:
            operator_collector_output = dict(collector_output)
            operator_collector_output.setdefault("snapshot_time", collection_time)
            operator_collector_output.setdefault("schedule_time", collection_time)
        baseline_output = baseline_context.get("baseline_collector_output") if baseline_context else None
        baseline_snapshot = _link_decline_collector_snapshot(baseline_output)
        if baseline_snapshot:
            if operator_collector_output is collector_output:
                operator_collector_output = dict(operator_collector_output)
            operator_collector_output.setdefault("baseline_snapshot", baseline_snapshot)
        operator_collector_output, baseline_compare_meta = _link_decline_with_baseline_compare(
            operator_collector_output,
            baseline_output,
        )
        operator_params = {
            "collector_skill_id": collector_skill_id,
            "collector_run_id": collector_run_id,
            "collector_decision_log_id": collector_decision_log_id,
            "collector_output": operator_collector_output,
            "trigger_source": trigger_source,
        }
        if collection_time:
            operator_params["snapshot_time"] = collection_time
            operator_params["schedule_time"] = collection_time
            operator_params["snapshot_mode"] = "on"
        if baseline_context:
            operator_params.update({
                "baseline_collector_run_id": baseline_context.get("baseline_collector_run_id"),
                "baseline_collector_decision_log_id": baseline_context.get("baseline_collector_decision_log_id"),
                "baseline_collector_output": baseline_context.get("baseline_collector_output"),
            })
            if baseline_snapshot:
                operator_params["baseline_snapshot"] = baseline_snapshot
        if baseline_compare_meta.get("applied_item_count"):
            operator_params["baseline_collector_compare"] = baseline_compare_meta
        operator_result = await self.execute_skill(
            skill_id=self.OPERATOR_SKILL_ID,
            params=operator_params,
            sandbox=False,
            triggered_by=operator_triggered_by or self.OPERATOR_TRIGGER,
            parent_run_id=collector_run_id,
        )
        operator_run_id = operator_result.get("run_id") if isinstance(operator_result, dict) else None
        await self._audit_link_decline_operator_trigger(
            "execution.link_decline_operator.triggered",
            collector_run_id,
            {
                "operator_run_id": operator_run_id,
                "collector_decision_log_id": collector_decision_log_id,
                "trigger_source": trigger_source,
                "baseline_collector_run_id": baseline_context.get("baseline_collector_run_id") if baseline_context else None,
                "baseline_collector_decision_log_id": (
                    baseline_context.get("baseline_collector_decision_log_id") if baseline_context else None
                ),
            },
        )
        return {
            "status": "triggered",
            "operator_run_id": operator_run_id,
            "collector_run_id": collector_run_id,
            "collector_decision_log_id": collector_decision_log_id,
        }

    async def _audit_link_decline_operator_trigger(
        self,
        action: str,
        collector_run_id: str,
        detail: dict | None = None,
    ) -> None:
        try:
            await audit.log(
                "svc_execution",
                action,
                "execution",
                collector_run_id,
                detail=detail or {},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("link decline operator trigger audit failed action={} err={}", action, exc)

    async def _await_with_timeout(
        self,
        awaitable,
        *,
        phase: str,
        timeout_seconds: float,
        skill_id: str,
        run_id: str | None = None,
        budget: _TimeoutBudget | None = None,
    ):
        effective_timeout = max(float(timeout_seconds), 0.001)
        total_timeout_seconds = None
        if budget is not None:
            total_timeout_seconds = budget.total_seconds
            remaining = budget.remaining()
            if remaining <= 0:
                raise _timeout_error(
                    phase=phase,
                    timeout_seconds=0.001,
                    skill_id=skill_id,
                    run_id=run_id,
                    total_timeout_seconds=total_timeout_seconds,
                )
            effective_timeout = min(effective_timeout, remaining)

        try:
            return await asyncio.wait_for(awaitable, timeout=effective_timeout)
        except asyncio.TimeoutError as exc:
            raise _timeout_error(
                phase=phase,
                timeout_seconds=effective_timeout,
                skill_id=skill_id,
                run_id=run_id,
                total_timeout_seconds=total_timeout_seconds,
            ) from exc

    async def _persist_timeout_before_start(
        self,
        *,
        run_id: str,
        skill_id: str,
        triggered_by: str,
        resolved_run_mode: str,
        parent_run_id: str | None,
        batch_id: str | None,
        normalized_data_proofs: list[dict],
        resolved_sample_used: bool,
        detail: dict,
    ) -> None:
        timed_out_at = now_bjt()
        async with _sf()() as session:
            session.add(
                ExecutionRun(
                    id=run_id,
                    skill_id=skill_id,
                    trigger_type=triggered_by,
                    run_mode=resolved_run_mode,
                    parent_run_id=parent_run_id,
                    started_at=timed_out_at,
                    completed_at=timed_out_at,
                    status="timeout",
                    batch_id=batch_id,
                    metadata_json={
                        "data_proofs": normalized_data_proofs,
                        "sample_used": resolved_sample_used,
                        "timeout": detail,
                    },
                )
            )
            await session.commit()
        await audit.log(
            "svc_execution",
            "execution.timeout",
            "skill",
            skill_id,
            detail={"run_id": run_id, "status": "timeout", "detail": detail},
        )
        await _invalidate_execution_cache()

    async def _persist_execution_success(
        self,
        *,
        run_id: str,
        skill_id: str,
        params: dict,
        output: dict | None,
        resolved_run_mode: str,
        parent_run_id: str | None,
        batch_id: str | None,
        normalized_data_proofs: list[dict],
        resolved_sample_used: bool,
        skill_approval_level: int | None,
        sandbox: bool,
    ) -> tuple[dict, int, datetime, list[dict], bool]:
        completed_at = now_bjt()
        async with _sf()() as session:
            result = await session.execute(select(ExecutionRun).where(ExecutionRun.id == run_id))
            run = result.scalar_one()
            run.status = "completed"
            run.completed_at = completed_at

            output = attach_execution_metadata(
                output,
                run_id=run_id,
                skill_id=skill_id,
                run_mode=resolved_run_mode,
                parent_run_id=parent_run_id,
                batch_id=batch_id,
                data_proofs=normalized_data_proofs,
                sample_used=resolved_sample_used,
            )
            # 从 output.reports[0].primary_indicator 抽主指标，挂到 _skillforge_meta，让下游
            # `_create_todos_for_execution` 能沿用同一份数据塞到 DecisionRequest.payload。
            primary_indicator = extract_primary_indicator(output)
            if primary_indicator and isinstance(output, dict):
                meta_bag = output.get("_skillforge_meta")
                if isinstance(meta_bag, dict):
                    meta_bag["primary_indicator"] = primary_indicator
            output_meta = output.get("_skillforge_meta") if isinstance(output, dict) else {}
            final_data_proofs = normalize_data_proofs(
                output_meta.get("data_proofs") if isinstance(output_meta, dict) else []
            )
            final_sample_used = (
                bool(output_meta.get("sample_used"))
                if isinstance(output_meta, dict)
                else resolved_sample_used
            )
            runtime_meta = {}
            if isinstance(output_meta, dict):
                for key in (
                    "execution_backend",
                    "requested_backend",
                    "fallback_from",
                    "instance_id",
                    "remote_run_id",
                    "agent_runtime",
                    "script",
                    "duration_ms",
                    "script_duration_ms",
                    "returncode",
                ):
                    if output_meta.get(key) is not None:
                        runtime_meta[key] = output_meta.get(key)
                if not run.source_instance_id and output_meta.get("instance_id"):
                    run.source_instance_id = str(output_meta.get("instance_id"))[:50]

            previous_metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
            metadata_json = dict(previous_metadata)
            metadata_json["data_proofs"] = final_data_proofs
            metadata_json["sample_used"] = final_sample_used
            if runtime_meta:
                metadata_json["runtime"] = runtime_meta
            model_context = model_context_from_output(output)
            if not model_context:
                model_context = await latest_decision_model_context(
                    session,
                    skill_id=skill_id,
                    run_id=run_id,
                )
            if model_context:
                metadata_json["model_context"] = model_context
                output = attach_model_context_to_output(output, model_context)
            effective_params = dict(params or {})
            if model_context:
                effective_params["model_context"] = model_context
            run.metadata_json = metadata_json
            decision = DecisionLog(
                run_id=run_id,
                skill_id=skill_id,
                input_snapshot=effective_params,
                output_result=output,
                model_id=model_context.get("model_deployment_id") if model_context else None,
                approval_level=skill_approval_level,
                is_sandbox=sandbox,
                created_at=now_bjt(),
            )
            session.add(decision)
            await session.flush()
            decision_log_id = decision.id
            try:
                from app.inbox.service import sync_report_cards_for_decision_log

                await sync_report_cards_for_decision_log(session, decision)
            except Exception as exc:  # noqa: BLE001
                logger.warning("同步报告投影失败 skill={} run={} err={}", skill_id, run_id, exc)
            try:
                from app.learning.service import capture_decision_log, capture_execution_run

                await capture_execution_run(session, run)
                await capture_decision_log(session, decision)
            except Exception as exc:  # noqa: BLE001
                logger.debug("智能闭环捕获本地执行结果失败 skill={} run={} err={}", skill_id, run_id, exc)
            artifact_refs: list[dict] = []
            input_ref = await persist_execution_artifact(
                session,
                run_id=run_id,
                skill_id=skill_id,
                kind="raw-input",
                payload=effective_params,
                decision_log_id=decision_log_id,
            )
            if input_ref:
                artifact_refs.append(input_ref)
            output_ref = await persist_execution_artifact(
                session,
                run_id=run_id,
                skill_id=skill_id,
                kind="raw-output",
                payload=output,
                decision_log_id=decision_log_id,
            )
            if output_ref:
                artifact_refs.append(output_ref)
            if artifact_refs:
                run_metadata = dict(run.metadata_json or {})
                existing_artifacts = run_metadata.get("artifacts")
                by_key = {
                    (str(item.get("kind")), str(item.get("sha256"))): item
                    for item in (existing_artifacts if isinstance(existing_artifacts, list) else [])
                    if isinstance(item, dict)
                }
                for ref in artifact_refs:
                    by_key[(str(ref.get("kind")), str(ref.get("sha256")))] = ref
                run_metadata["artifacts"] = list(by_key.values())
                run.metadata_json = run_metadata
                await _capture_execution_artifact_refs(
                    session,
                    artifact_refs,
                    skill_id=skill_id,
                    run_id=run_id,
                )
            await session.commit()

        return output, decision_log_id, completed_at, final_data_proofs, final_sample_used

    async def _create_todos_for_execution(
        self,
        *,
        run_id: str,
        skill_id: str,
        skill_meta: dict,
        params: dict,
        output: dict,
        decision_log_id: int,
    ) -> tuple[bool, int]:
        from sqlalchemy import update as sa_update
        from app.todos.service import todo_service

        # 从 output.reports[0].primary_indicator 抽主指标，作为 decision 下放字段，让
        # todo_service 能透传到 DecisionRequest.payload['primary_indicator']。
        primary_indicator = extract_primary_indicator(output)

        async with _sf()() as session:
            todo_request = await todo_service.create_from_execution(
                session,
                run_id=run_id,
                skill_id=skill_id,
                skill_meta=skill_meta,
                decision={
                    "input_snapshot": params,
                    "output_result": output,
                    "suggested_action": output.get("suggestions") or output.get("recommended_actions"),
                    "decision_log_id": decision_log_id,
                    "primary_indicator": primary_indicator,
                },
            )
            if not todo_request:
                return False, 0

            await session.execute(
                sa_update(DecisionLog)
                .where(DecisionLog.run_id == run_id)
                .values(approval_status="pending")
            )
            await session.commit()
            return True, len(todo_request)

    async def _generate_execution_summary(
        self,
        *,
        run_id: str,
        skill_name: str,
        output: dict,
    ) -> None:
        from sqlalchemy import update as sa_update
        from app.skills.intelligence.ai_service import generate_execution_summary

        ai_summary = await generate_execution_summary(skill_name, output)
        if not ai_summary:
            return
        async with _sf()() as session:
            await session.execute(
                sa_update(ExecutionRun)
                .where(ExecutionRun.id == run_id)
                .values(summary=ai_summary)
            )
            await session.commit()

    async def _finalize_run_failure(
        self,
        *,
        run_id: str,
        skill_id: str,
        skill_department: str | None,
        error: Exception,
    ) -> None:
        failed_at = now_bjt()
        status = "timeout" if _is_timeout_error(error) else "failed"
        async with _sf()() as session:
            result = await session.execute(select(ExecutionRun).where(ExecutionRun.id == run_id))
            run = result.scalar_one_or_none()
            if run is not None and run.status != "completed":
                run.status = status
                run.completed_at = failed_at
                await session.commit()

        await tasktree_dispatcher.schedule_writer(
            _make_finish_node_coro(
                run_id=run_id,
                status=status,
                finished_at=failed_at,
            ),
            source_type="execution_finish",
            source_ref=run_id,
            operation="finish_execution_node",
            payload={
                "run_id": run_id,
                "status": status,
                "finished_at": serialize_datetime(failed_at),
            },
        )

        try:
            from app.tasktree.service import invalidate_tasktree
            await invalidate_tasktree(skill_department)
        except Exception as inv_err:
            logger.warning(
                "invalidate_tasktree 失败 (run_id={} dept={}): {}",
                run_id,
                skill_department,
                inv_err,
                exc_info=True,
            )

        detail = {
            "run_id": run_id,
            "status": status,
            "error": str(error),
        }
        if isinstance(error, AppError):
            detail["code"] = error.code
            if error.detail:
                detail["detail"] = error.detail
        await audit.log(
            "svc_execution",
            "execution.timeout" if status == "timeout" else "execution.fail",
            "skill",
            skill_id,
            detail=detail,
        )
        await _invalidate_execution_cache()

    async def _persist_runtime_log_tail(
        self,
        *,
        run_id: str | None,
        skill_id: str,
        source: str,
        stream: str,
        content: object,
    ) -> None:
        if not run_id or not content:
            return
        try:
            from app.execution.artifact_service import persist_execution_log

            async with _sf()() as session:
                await persist_execution_log(
                    session,
                    run_id=run_id,
                    skill_id=skill_id,
                    stream=stream,
                    source=source,
                    content=content,
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("persist runtime log failed skill={} run={} stream={} err={}", skill_id, run_id, stream, exc)

    async def _pick_bridge_script_instance(self, *, skill_department: str | None = None) -> str | None:
        """挑一个在线且有本地 skills 目录的 bridge 节点作为脚本执行目标。"""
        async with _sf()() as session:
            stmt = (
                select(OpenClawInstance)
                .where(OpenClawInstance.is_active == True)  # noqa: E712
                .where(OpenClawInstance.bridge_connected_at.is_not(None))
                .where(OpenClawInstance.bridge_skills_dir.is_not(None))
                .order_by(OpenClawInstance.is_platform_default.desc(), OpenClawInstance.last_heartbeat.desc().nullslast())
            )
            rows = (await session.execute(stmt)).scalars().all()
        rows = [inst for inst in rows if _is_skill_runtime_agent(inst)]
        try:
            from app.aiclaw.bridge_registry import bridge_registry

            rows = [inst for inst in rows if bridge_registry.is_online(inst.id)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("检查 Bridge 在线状态失败: {}", exc)
            return None
        if not rows:
            return None
        if skill_department:
            for inst in rows:
                if inst.department == skill_department:
                    return inst.id
        return rows[0].id

    async def _ensure_skill_runtime_instance(self, instance_id: str | None) -> None:
        if not instance_id:
            return
        async with _sf()() as session:
            instance = await session.get(OpenClawInstance, instance_id)
        if instance is not None and not _is_skill_runtime_agent(instance):
            raise AppError(
                "AICLAW_AGENT_PURPOSE_INVALID",
                400,
                {
                    "detail": "该 Agent 用途不承载 Skill Runtime，请选择部门执行或混合 Agent",
                    "instance_id": instance_id,
                    "agent_purpose": _agent_purpose(instance),
                },
            )

    async def _ensure_bridge_script_online(self, instance_id: str | None) -> None:
        if not instance_id:
            raise AppError(
                "BRIDGE_OFFLINE",
                503,
                {"detail": "没有在线 AIClaw Bridge 可执行 scripts/main.py"},
            )
        try:
            from app.aiclaw.bridge_registry import bridge_registry

            is_online = bridge_registry.is_online(instance_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("检查 Bridge 在线状态失败 instance={} err={}", instance_id, exc)
            is_online = False
        if not is_online:
            raise AppError(
                "BRIDGE_OFFLINE",
                503,
                {"detail": f"实例 {instance_id} 的 bridge 未连接", "instance_id": instance_id},
            )

    @staticmethod
    def _script_payload(params: dict | None) -> dict:
        payload = dict(params or {})
        for key in (
            "_execution_backend",
            "_execution",
            "_aiclaw_instance_id",
            "_script_path",
            "_script_timeout",
            "_target_dir",
            "model_context",
        ):
            payload.pop(key, None)
        return payload

    async def _script_payload_with_model_context(
        self,
        *,
        params: dict | None,
        skill_id: str,
        skill_department: str | None,
        target_instance_id: str | None,
        run_id: str | None,
    ) -> dict:
        payload = self._script_payload(params)
        if not target_instance_id:
            return payload
        try:
            async with _sf()() as session:
                instance = await session.get(OpenClawInstance, target_instance_id)
                model_context = await active_model_context_for_skill(
                    session,
                    skill_id=skill_id,
                    skill_department=skill_department,
                    target_instance=instance,
                    run_id=run_id,
                    include_artifact_uri=True,
                )
            if model_context:
                payload["model_context"] = model_context
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "解析 Skill 运行模型上下文失败 skill={} instance={} run={} err={}",
                skill_id,
                target_instance_id,
                run_id,
                exc,
            )
        return payload

    @staticmethod
    def _attach_runtime_input_model_context(output: dict, script_payload: dict) -> dict:
        model_context = script_payload.get("model_context") if isinstance(script_payload, dict) else None
        if not isinstance(model_context, dict):
            return output
        result = dict(output or {})
        meta = result.get("_skillforge_meta")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta["model_context"] = model_context
        active_model = (
            model_context.get("active_model_deployment")
            if isinstance(model_context.get("active_model_deployment"), dict)
            else {}
        )
        if active_model:
            meta["active_model_deployment"] = {
                key: active_model.get(key)
                for key in (
                    "model_deployment_id",
                    "model_family",
                    "deployment_status",
                    "rollout_percent",
                    "artifact_id",
                    "artifact_sha256",
                    "target_skill_ids",
                )
                if active_model.get(key) is not None
            }
        result["_skillforge_meta"] = meta
        return result

    async def _run_skill_via_bridge_script(
        self,
        *,
        skill_id: str,
        params: dict,
        run_id: str | None = None,
        run_token: str | None = None,
        run_mode: str | None = None,
        skill_git_commit_full: str | None = None,
        instance_id: str | None,
        skill_department: str | None,
        script_path: str,
        timeout: int,
    ) -> dict:
        from app.aiclaw.client import AIClawClient
        from app.common.exceptions import AppError

        target_instance_id = instance_id or await self._pick_bridge_script_instance(
            skill_department=skill_department
        )
        if not target_instance_id:
            raise AppError(
                "AICLAW_EXEC_FAILED",
                503,
                {"detail": "没有在线 AIClaw bridge 可执行 scripts/main.py"},
            )
        await self._ensure_skill_runtime_instance(target_instance_id)

        script_payload = await self._script_payload_with_model_context(
            params=params,
            skill_id=skill_id,
            skill_department=skill_department,
            target_instance_id=target_instance_id,
            run_id=run_id,
        )
        result = await AIClawClient(target_instance_id).run_skill_script(
            skill_id,
            script_payload,
            run_id=run_id,
            run_token=run_token,
            run_mode=run_mode,
            skill_git_commit_full=skill_git_commit_full,
            script_path=str(script_path or "scripts/main.py"),
            timeout=int(timeout or 300),
            target_dir=params.get("_target_dir"),
        )
        await self._persist_runtime_log_tail(
            run_id=run_id,
            skill_id=skill_id,
            source="bridge_script",
            stream="stdout",
            content=result.get("raw"),
        )
        await self._persist_runtime_log_tail(
            run_id=run_id,
            skill_id=skill_id,
            source="bridge_script",
            stream="stderr",
            content=result.get("stderr"),
        )
        if not result.get("success"):
            raise AppError("EXECUTION_SCRIPT_ERROR", 500, {"detail": result})

        output = result.get("output")
        if not isinstance(output, dict):
            output = {"output": output}
        output = self._attach_runtime_input_model_context(output, script_payload)
        meta = output.get("_skillforge_meta")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta.update({
            "execution_backend": "bridge_script",
            "instance_id": target_instance_id,
            "script": result.get("script"),
            "script_duration_ms": result.get("duration_ms"),
        })
        output["_skillforge_meta"] = meta
        return output

    async def _run_skill_via_agent_runtime(
        self,
        *,
        skill_id: str,
        params: dict,
        run_id: str | None = None,
        run_token: str | None = None,
        run_mode: str | None = None,
        skill_git_commit_full: str | None = None,
        instance_id: str | None,
        skill_department: str | None,
        runtime_config: dict,
        script_path: str,
        timeout: int,
    ) -> dict:
        from app.aiclaw.client import AIClawClient
        from app.common.exceptions import AppError

        target_instance_id = instance_id or await self._pick_bridge_script_instance(
            skill_department=skill_department
        )
        if not target_instance_id:
            raise AppError(
                "AICLAW_EXEC_FAILED",
                503,
                {"detail": "没有在线 AIClaw bridge 可执行 OpenClaw Agent runtime"},
            )
        await self._ensure_skill_runtime_instance(target_instance_id)

        try:
            script_payload = await self._script_payload_with_model_context(
                params=params,
                skill_id=skill_id,
                skill_department=skill_department,
                target_instance_id=target_instance_id,
                run_id=run_id,
            )
            result = await AIClawClient(target_instance_id).run_agent_skill(
                skill_id,
                script_payload,
                run_id=run_id,
                runtime=runtime_config,
                run_token=run_token,
                run_mode=run_mode,
                skill_git_commit_full=skill_git_commit_full,
                timeout=int(timeout or runtime_config.get("timeout") or 300),
                target_dir=params.get("_target_dir"),
            )
            await self._persist_runtime_log_tail(
                run_id=run_id,
                skill_id=skill_id,
                source="agent_runtime",
                stream="stdout",
                content=result.get("raw") or result.get("stdout") or result.get("log"),
            )
            await self._persist_runtime_log_tail(
                run_id=run_id,
                skill_id=skill_id,
                source="agent_runtime",
                stream="stderr",
                content=result.get("stderr") or result.get("error"),
            )
        except AppError:
            if runtime_config.get("fallback") != "bridge_script":
                raise
            output = await self._run_skill_via_bridge_script(
                skill_id=skill_id,
                params=params,
                run_id=run_id,
                run_token=run_token,
                run_mode=run_mode,
                skill_git_commit_full=skill_git_commit_full,
                instance_id=target_instance_id,
                skill_department=skill_department,
                script_path=script_path,
                timeout=timeout,
            )
            meta = output.get("_skillforge_meta")
            meta = dict(meta) if isinstance(meta, dict) else {}
            meta["fallback_from"] = runtime_config.get("backend") or "openclaw_agent"
            output["_skillforge_meta"] = meta
            return output

        if not result.get("success", True):
            if runtime_config.get("fallback") == "bridge_script":
                output = await self._run_skill_via_bridge_script(
                    skill_id=skill_id,
                    params=params,
                    run_id=run_id,
                    run_token=run_token,
                    run_mode=run_mode,
                    skill_git_commit_full=skill_git_commit_full,
                    instance_id=target_instance_id,
                    skill_department=skill_department,
                    script_path=script_path,
                    timeout=timeout,
                )
                meta = output.get("_skillforge_meta")
                meta = dict(meta) if isinstance(meta, dict) else {}
                meta["fallback_from"] = runtime_config.get("backend") or "openclaw_agent"
                output["_skillforge_meta"] = meta
                return output
            raise AppError("EXECUTION_AGENT_ERROR", 500, {"detail": result})

        output = result.get("output")
        if not isinstance(output, dict):
            output = {"output": output}
        output = self._attach_runtime_input_model_context(output, script_payload)
        meta = output.get("_skillforge_meta")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta.update({
            "execution_backend": runtime_config.get("backend") or "openclaw_agent",
            "instance_id": target_instance_id,
            "agent_runtime": "openclaw",
            "duration_ms": result.get("duration_ms"),
        })
        output["_skillforge_meta"] = meta
        return output

    async def _pre_check_data_quality(self, skill_id: str) -> dict:
        """
        执行前数据质量检查。
        检查项：
        1. 数据源是否存在且可用（is_active）
        2. 数据源新鲜度（最后更新时间 < stale_threshold_hours）
        3. 数据行数不为 0
        """
        from app.datasources.models import DataSource, DataIngestionLog
        from app.skills.core.parser import skill_parser
        from app.skills.core.git_service import git_service

        skill_md = git_service.read_file(skill_id, "SKILL.md")
        if not skill_md:
            # 无 SKILL.md 文件则跳过检查
            return {"passed": True, "reasons": []}

        parsed = skill_parser.parse(skill_md)
        if not parsed.data_inputs:
            return {"passed": True, "reasons": []}

        reasons = []

        async with _sf()() as session:
            for data_input in parsed.data_inputs:
                source_id = data_input.source
                if not source_id:
                    continue

                # 按 id 或 name 查找数据源
                result = await session.execute(
                    select(DataSource).where(DataSource.id == source_id)
                )
                source = result.scalar_one_or_none()
                if not source:
                    result = await session.execute(
                        select(DataSource).where(DataSource.name == source_id)
                    )
                    source = result.scalar_one_or_none()
                if not source:
                    reasons.append(f"数据源 '{source_id}' 不存在")
                    continue

                if not source.is_active:
                    reasons.append(f"数据源 '{source_id}' 已停用")
                    continue

                # 检查最后成功的导入记录
                log_result = await session.execute(
                    select(DataIngestionLog)
                    .where(DataIngestionLog.source_id == source_id)
                    .where(DataIngestionLog.status == "success")
                    .order_by(DataIngestionLog.created_at.desc())
                    .limit(1)
                )
                last_log = log_result.scalar_one_or_none()

                if not last_log:
                    reasons.append(f"数据源 '{source_id}' 从未成功导入过数据")
                    continue

                # 新鲜度检查
                if last_log.created_at:
                    hours_since = (now_bjt() - last_log.created_at).total_seconds() / 3600
                    threshold = source.stale_threshold_hours or 24
                    if hours_since > threshold:
                        reasons.append(
                            f"数据源 '{source_id}' 已过期: "
                            f"最后更新 {hours_since:.1f}h 前, 阈值 {threshold}h"
                        )

                # 行数检查
                if last_log.row_count is not None and last_log.row_count == 0:
                    reasons.append(f"数据源 '{source_id}' 最近一次导入行数为 0")

        return {
            "passed": len(reasons) == 0,
            "reasons": reasons,
        }

    async def get_last_production_result(self, skill_id: str) -> dict | None:
        """获取最近一次非沙箱执行的decision_log，用于沙箱结果对比"""
        async with _sf()() as session:
            stmt = (
                select(DecisionLog)
                .where(DecisionLog.skill_id == skill_id)
                .where(DecisionLog.is_sandbox == False)  # noqa: E712
                .order_by(DecisionLog.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            log = result.scalar_one_or_none()

            if not log:
                return None

            return {
                "run_id": log.run_id,
                "input_snapshot": log.input_snapshot,
                "output_result": log.output_result,
                "user_action": log.user_action,
                "created_at": isoformat_bjt(log.created_at),
            }

    @staticmethod
    def _build_execution_runs_stmt(
        *,
        skill_id: str | None = None,
        department: str | None = None,
        status: str | None = None,
    ):
        stmt = select(ExecutionRun)
        if status:
            stmt = stmt.where(ExecutionRun.status == status)
        if skill_id or department:
            decision_scope = select(1).select_from(DecisionLog).where(DecisionLog.run_id == ExecutionRun.id)
            if skill_id:
                decision_scope = decision_scope.where(DecisionLog.skill_id == skill_id)
            if department:
                decision_scope = decision_scope.join(Skill, Skill.id == DecisionLog.skill_id)
                decision_scope = decision_scope.where(Skill.department == department)
            stmt = stmt.where(decision_scope.exists())
        return stmt

    async def count_execution_runs(
        self,
        skill_id: str | None = None,
        *,
        department: str | None = None,
        status: str | None = None,
    ) -> int:
        """统计执行记录总数（支持与列表查询相同的过滤条件）。"""
        async with _sf()() as session:
            return await self._count_execution_runs_in_session(
                session,
                skill_id=skill_id,
                department=department,
                status=status,
            )

    async def _count_execution_runs_in_session(
        self,
        session,
        *,
        skill_id: str | None = None,
        department: str | None = None,
        status: str | None = None,
    ) -> int:
        scoped = self._build_execution_runs_stmt(
            skill_id=skill_id,
            department=department,
            status=status,
        ).subquery()
        total = await session.execute(select(func.count()).select_from(scoped))
        return total.scalar_one() or 0

    async def get_execution_runs(
        self,
        skill_id: str | None = None,
        limit: int = 20,
        *,
        offset: int = 0,
        department: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """查询执行记录（支持部门过滤）"""
        async with _sf()() as session:
            return await self._fetch_execution_runs_in_session(
                session,
                skill_id=skill_id,
                limit=limit,
                offset=offset,
                department=department,
                status=status,
            )

    async def _fetch_execution_runs_in_session(
        self,
        session,
        *,
        skill_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        department: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        stmt = (
            self._build_execution_runs_stmt(
                skill_id=skill_id,
                department=department,
                status=status,
            )
            .order_by(ExecutionRun.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await session.execute(stmt)
        runs = result.scalars().all()
        return [
            {"id": r.id, "trigger_type": r.trigger_type, "status": r.status,
             "skill_id": r.skill_id, "run_mode": r.run_mode,
             "parent_run_id": r.parent_run_id, "batch_id": r.batch_id,
             "sample_used": (r.metadata_json or {}).get("sample_used") if isinstance(r.metadata_json, dict) else None,
             "started_at": isoformat_bjt(r.started_at),
             "completed_at": isoformat_bjt(r.completed_at)}
            for r in runs
        ]

    async def list_execution_runs_paged(
        self,
        *,
        page: int,
        page_size: int,
        skill_id: str | None = None,
        department: str | None = None,
        status: str | None = None,
    ) -> dict:
        """在**同一 session**内完成 count + 列表, 避免两次查询之间数据写入造成不一致。"""
        async with _sf()() as session:
            total = await self._count_execution_runs_in_session(
                session,
                skill_id=skill_id,
                department=department,
                status=status,
            )
            offset = (page - 1) * page_size
            items = await self._fetch_execution_runs_in_session(
                session,
                skill_id=skill_id,
                limit=page_size,
                offset=offset,
                department=department,
                status=status,
            )
            return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def get_run_detail(self, run_id: str) -> dict:
        """获取单次执行详情"""
        async with _sf()() as session:
            result = await session.execute(
                select(ExecutionRun).where(ExecutionRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            if not run:
                return {"error": "执行记录不存在"}

            return {
                "id": run.id,
                "skill_id": run.skill_id,
                "playbook_id": run.playbook_id,
                "trigger_type": run.trigger_type,
                "run_mode": run.run_mode,
                "parent_run_id": run.parent_run_id,
                "batch_id": run.batch_id,
                "data_proofs": (run.metadata_json or {}).get("data_proofs") if isinstance(run.metadata_json, dict) else [],
                "sample_used": (run.metadata_json or {}).get("sample_used") if isinstance(run.metadata_json, dict) else None,
                "status": run.status,
                "total_steps": run.total_steps,
                "completed_steps": run.completed_steps,
                "started_at": isoformat_bjt(run.started_at),
                "completed_at": isoformat_bjt(run.completed_at),
            }

    async def get_run_steps(self, run_id: str) -> list[dict]:
        """获取执行步骤列表"""
        from app.execution.models import ExecutionStep
        async with _sf()() as session:
            result = await session.execute(
                select(ExecutionStep)
                .where(ExecutionStep.run_id == run_id)
                .order_by(ExecutionStep.step_order)
            )
            steps = result.scalars().all()

            return [
                {
                    "id": s.id,
                    "skill_id": s.skill_id,
                    "step_order": s.step_order,
                    "status": s.status,
                    "input_data": s.input_data,
                    "output_data": s.output_data,
                    "duration_ms": s.duration_ms,
                    "error_message": s.error_message,
                    "started_at": isoformat_bjt(s.started_at),
                    "completed_at": isoformat_bjt(s.completed_at),
                }
                for s in steps
            ]

    async def get_run_decisions(self, run_id: str) -> list[dict]:
        """决策追溯：获取某次执行的所有决策日志"""
        async with _sf()() as session:
            result = await session.execute(
                select(DecisionLog)
                .where(DecisionLog.run_id == run_id)
                .order_by(DecisionLog.created_at)
            )
            decisions = result.scalars().all()

            return [
                {
                    "id": d.id,
                    "skill_id": d.skill_id,
                    "input_snapshot": d.input_snapshot,
                    "output_result": d.output_result,
                    "suggested_action": d.suggested_action,
                    "approval_level": d.approval_level,
                    "approval_status": d.approval_status,
                    "user_action": d.user_action,
                    "user_feedback": d.user_feedback,
                    "rating": d.rating,
                    "feedback_type": d.feedback_type,
                    "business_impact": d.business_impact,
                    "is_sandbox": d.is_sandbox,
                    "created_at": isoformat_bjt(d.created_at),
                }
                for d in decisions
            ]


    async def compare_runs(self, run_id_1: str, run_id_2: str) -> dict:
        """对比两次执行的输入/输出"""
        async with _sf()() as session:
            d1 = await session.execute(
                select(DecisionLog).where(DecisionLog.run_id == run_id_1)
            )
            d2 = await session.execute(
                select(DecisionLog).where(DecisionLog.run_id == run_id_2)
            )
            log1 = d1.scalar_one_or_none()
            log2 = d2.scalar_one_or_none()

            if not log1 or not log2:
                from app.common.exceptions import AppError
                raise AppError("EXECUTION_NOT_FOUND", 404)

            input_diff = _compute_json_diff(log1.input_snapshot, log2.input_snapshot)
            output_diff = _compute_json_diff(log1.output_result, log2.output_result)

            return {
                "run1": {
                    "run_id": run_id_1,
                    "skill_id": log1.skill_id,
                    "created_at": isoformat_bjt(log1.created_at),
                    "input": log1.input_snapshot,
                    "output": log1.output_result,
                },
                "run2": {
                    "run_id": run_id_2,
                    "skill_id": log2.skill_id,
                    "created_at": isoformat_bjt(log2.created_at),
                    "input": log2.input_snapshot,
                    "output": log2.output_result,
                },
                "input_diff": input_diff,
                "output_diff": output_diff,
                "is_same_output": log1.output_result == log2.output_result,
            }


async def _check_contract_drift(skill_id: str, run_id: str, output: dict | None) -> None:
    """Runtime 契约校验（warn-only）。

    流程：
    1. 读该 skill 的 contract.json
    2. 取 output_schema；无则跳过（legacy）
    3. 用 jsonschema 校验 output；不符则写入 contract_drifts 表 + 提醒 owner
    """
    from app.common.contract_schema import (
        get_output_schema,
        validate_output,
        summarize_errors,
    )
    from app.skills.core.git_service import git_service
    import hashlib
    import json

    if not output or not isinstance(output, dict):
        return

    contract_raw = git_service.read_file(skill_id, "contract.json")
    if not contract_raw:
        return
    try:
        contract = json.loads(contract_raw)
    except json.JSONDecodeError as e:
        logger.warning("contract.json 解析失败 skill={} err={}", skill_id, e)
        return

    output_schema = get_output_schema(contract)
    if not output_schema:
        return  # legacy skill，跳过

    errors = validate_output(output, output_schema)
    if not errors:
        return  # 合规，啥也不做

    # 有 drift：写表 + 提醒 owner
    schema_hash = hashlib.sha256(
        json.dumps(output_schema, sort_keys=True).encode("utf-8")
    ).hexdigest()[:32]

    async with _sf()() as session:
        session.add(ContractDrift(
            skill_id=skill_id,
            run_id=run_id,
            schema_errors=errors[:50],
            schema_hash=schema_hash,
        ))
        await session.commit()

    logger.warning(
        "contract_drift detected skill={} run={} errors={}",
        skill_id, run_id, summarize_errors(errors),
    )

    # 异步提醒 owner（通过 outbox 限速）
    try:
        async with _sf()() as session:
            skill_row = (await session.execute(
                select(Skill).where(Skill.id == skill_id)
            )).scalar_one_or_none()
            owner = skill_row.owner if skill_row else None
        if owner:
            await outbox.enqueue(
                "work_notice",
                owner,
                {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": f"契约偏离提醒：{skill_id}",
                        "text": (
                            f"**skill**: {skill_id}\n"
                            f"**run_id**: {run_id}\n"
                            f"**schema 错误数**: {len(errors)}\n"
                            f"**首条错误**: {errors[0] if errors else 'N/A'}\n"
                            "请检查 SKILL 的 main.py 与 contract.output_schema 是否一致。"
                        ),
                    },
                },
                priority=5,
                related_type="contract_drift",
                related_id=run_id,
            )
    except Exception as e:
        logger.warning("drift 提醒发送失败 skill={} err={}", skill_id, e)


def _compute_json_diff(obj1: dict | None, obj2: dict | None) -> list[dict]:
    """对比两个 JSON 对象，返回差异列表"""
    obj1 = obj1 or {}
    obj2 = obj2 or {}
    diffs = []
    all_keys = set(obj1.keys()) | set(obj2.keys())
    for key in sorted(all_keys):
        v1 = obj1.get(key)
        v2 = obj2.get(key)
        if v1 != v2:
            diffs.append({
                "key": key,
                "old_value": v1,
                "new_value": v2,
                "type": "added" if v1 is None else "removed" if v2 is None else "changed",
            })
    return diffs


def _make_create_node_coro(**kwargs):
    """为 dispatcher 生成 create_execution_node 协程工厂。

    dispatcher 收到的是 "工厂"（无参 callable）而不是协程对象，
    这样失败时可以选择不 await，仅把 payload 入队由 repair_worker replay。
    """
    async def _run():
        async with _sf()() as session:
            await tasktree_writer.create_execution_node(session, **kwargs)
            await session.commit()

    return _run


def _make_finish_node_coro(**kwargs):
    """为 dispatcher 生成 finish_execution_node 协程工厂。"""
    async def _run():
        async with _sf()() as session:
            await tasktree_writer.finish_execution_node(session, **kwargs)
            await session.commit()

    return _run


def _make_sync_heartbeat_coro(**kwargs):
    """为 dispatcher 生成 sync_instance_heartbeat 协程工厂。"""
    async def _run():
        async with _sf()() as session:
            await tasktree_writer.sync_instance_heartbeat(session, **kwargs)
            await session.commit()

    return _run


# 全局实例
execution_service = ExecutionService()
