"""Services for the SkillForge intelligence learning loop.

This module intentionally centralizes cross-domain learning behavior. Existing
modules keep their own ownership; the learning loop only observes, classifies,
links and materializes governed downstream artifacts.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, time as datetime_time, timedelta
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from loguru import logger
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.auth.access import role_matches_any
from app.auth.models import User
from app.codex.models import CodexMcpCallAudit, SfDataRecord
from app.common.ai import call_llm, get_ai_config, redact_secret_text
from app.common.exceptions import AppError
from app.common.models import IntelligenceAnalyzeCache, IntelligenceAnalyzeRun
from app.common.time_utils import isoformat_bjt, now_bjt
from app.config import settings
from app.execution.models import DecisionLog, ExecutionArtifact, ExecutionRun, ExecutionStep, OpenClawInstance, SAMPLE_RUN_MODES
from app.inbox.service import extract_report_cards
from app.knowledge.models import DepartmentKnowledgeBase, KnowledgeDocument, KnowledgeQueryLog
from app.learning.models import (
    ImprovementCandidate,
    LearningArtifact,
    LearningAutomationRun,
    LearningEvent,
    LearningFlowEdge,
    LearningGovernanceTask,
    LearningIngestionJob,
)
from app.skills.core.models import Skill
from app.todos.models import AITodo, DecisionRequest, TodoDispatchTask
from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

EVENT_LIMIT_MAX = 1000
ARTIFACT_LIMIT_MAX = 1000
FLOW_LIMIT_MAX = 600
TRAINING_GATEWAY_REQUIRED_OPS = ("training.submit_job", "training.collect_result", "training.inference")
RUNTIME_AGENT_REQUIRED_OPS = ("run_agent_skill", "run_skill_script")
INFERENCE_AGENT_REQUIRED_OPS = ("training.inference",)
ANALYSIS_AGENT_REQUIRED_OPS = ("intelligence.analyze",)

KNOWLEDGE_ARTIFACTS = {"knowledge_note", "report_summary", "execution_data_artifact"}
TRAINING_ARTIFACTS = {"training_sample", "eval_case"}
CANDIDATE_ARTIFACTS = {
    "skill_improvement_candidate",
    "sf_iteration_candidate",
    "agent_policy_candidate",
    "agent_creation_candidate",
    "ai_system_gap_candidate",
    "knowledge_review_candidate",
    "training_improvement_candidate",
}
AGENT_ARTIFACTS = {"agent_memory"}
LEARNING_TRAINING_CANDIDATE_MIN_SAMPLES = 30
LEARNING_AUTO_TRAINING_AGENT_ID = "learning_training_reviewer"
LEARNING_FULL_HISTORY_TARGET_SKILL_ID = "samplebrand-video-low-consumption-operator-v1"
LEARNING_FULL_HISTORY_PARENT_DEPLOYMENT_ID = "deploy_293468f323bb4ce7807b9b15"
LEARNING_DAILY_TRAINING_SPEC_INLINE_ID_MAX_BYTES = 16 * 1024
LEARNING_SYSTEM_TRAINING_BACKFILL_MAX_PER_SOURCE = 50000
LEARNING_SYSTEM_TRAINING_MATERIALIZE_MAX = 50000
ECOMMERCE_LEARNING_FLOW_DATA_NAMESPACE = "ecommerce.learning_flow.eval.v1"
ECOMMERCE_LEARNING_FLOW_MATERIALIZE_LIMIT = 1000
LEARNING_DEFAULT_TRAINING_MODEL_PROFILE = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
LEARNING_DEFAULT_TRAINING_GATEWAY_ID = "training-primary"
LEARNING_DEFAULT_DATASET_SOURCE_GATEWAY_ID = "data-primary"
LEARNING_DEFAULT_DEPLOYMENT_GATEWAY_ID = "inference-primary"
LEARNING_DEFAULT_DEPLOYMENT_RUNTIME_PROFILE = "mlx-qwen3.6-35b-a3b-lora"
LEARNING_ADULT_PRODUCT_REVIEW_KEYWORDS = (
    "安全套",
    "润滑液",
    "成人用品",
    "情趣用品",
    "避孕套",
    "延时喷剂",
    "飞机杯",
    "震动棒",
    "私密护理",
    "计生用品",
)
LEARNING_GOVERNANCE_TASK_STALE_HOURS = 24
LEARNING_AUTOMATION_RUN_STALE_HOURS = 24
MAINTENANCE_DECISION_CHANNELS = {"data_cleanup", "manual_cleanup", "scheduler_cleanup", "system_cleanup"}
MAINTENANCE_DECISION_REASON_PREFIXES = (
    "data_cleanup",
    "manual_cleanup",
    "system_cleanup",
    "archived_by_cleanup",
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:24]}"


def _iso(value: datetime | None) -> str | None:
    return isoformat_bjt(value) if value else None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clip(value: Any, limit: int = 4000) -> str:
    return redact_secret_text(value, limit=limit)


def _redacted_payload(value: Any, limit: int = 4000) -> Any:
    if value in (None, "", [], {}):
        return {}
    redacted = _clip(_json_dumps(value), limit)
    try:
        return json.loads(redacted)
    except (TypeError, ValueError):
        return redacted


def _is_maintenance_todo_decision(row: AITodo) -> bool:
    channel = str(row.decision_channel or "").strip().lower()
    if channel in MAINTENANCE_DECISION_CHANNELS:
        return True
    reason = str(row.decision_reason or "").strip().lower()
    return any(reason.startswith(prefix) for prefix in MAINTENANCE_DECISION_REASON_PREFIXES)


def _is_global_user(user: User) -> bool:
    return role_matches_any(user, ("admin", "system_admin")) or bool(getattr(user, "can_view_all", False))


def _user_department(user: User) -> str | None:
    text = str(getattr(user, "department", "") or "").strip()
    return text or None


def _scope_filter(model: Any, user: User):
    if _is_global_user(user):
        return sa.true()
    dept = _user_department(user)
    conditions = []
    if hasattr(model, "department") and dept:
        conditions.append(model.department == dept)
    if hasattr(model, "user_id"):
        conditions.append(model.user_id == str(getattr(user, "id", "") or ""))
    if hasattr(model, "created_by"):
        conditions.append(model.created_by == str(getattr(user, "id", "") or ""))
    return or_(*conditions) if conditions else sa.false()


def _sf_data_visibility_filter(user: User):
    if _is_global_user(user):
        return sa.true()
    user_id = str(getattr(user, "id", "") or "")
    department = _user_department(user)
    return or_(
        SfDataRecord.visibility == "global",
        SfDataRecord.user_id == user_id,
        and_(SfDataRecord.visibility == "department", SfDataRecord.department == department) if department else sa.false(),
    )


def _clean_filter(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _bounded_days(days: int | None, default: int = 30) -> int:
    try:
        value = int(days if days is not None else default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, 365))


def _auto_training_interval_seconds() -> int:
    try:
        value = int(settings.LEARNING_AUTO_TRAINING_INTERVAL_SECONDS or 0)
    except (TypeError, ValueError):
        value = 24 * 60 * 60
    return max(60 * 60, min(value or 24 * 60 * 60, 30 * 24 * 60 * 60))


def _auto_training_min_samples() -> int:
    try:
        value = int(settings.LEARNING_AUTO_TRAINING_MIN_SAMPLES or 0)
    except (TypeError, ValueError):
        value = 4
    return max(1, min(value or 4, LEARNING_TRAINING_CANDIDATE_MIN_SAMPLES))


def _automation_run_advanced_training(row: LearningAutomationRun) -> bool:
    result = _safe_dict(row.result_json)
    auto_limits = _safe_dict(result.get("auto_limits"))
    if auto_limits.get("training_due") is True:
        return True
    training_automation = _safe_dict(result.get("training_automation"))
    if training_automation.get("enabled") is not True:
        return False
    skipped_reasons = {
        str(_safe_dict(item).get("reason") or "")
        for item in _safe_list(training_automation.get("skipped"))
        if isinstance(item, dict)
    }
    return "training_interval_not_due" not in skipped_reasons


async def _auto_training_gate(db: AsyncSession) -> dict[str, Any]:
    now = now_bjt()
    interval_seconds = _auto_training_interval_seconds()
    rows = (
        await db.execute(
            select(LearningAutomationRun)
            .where(LearningAutomationRun.trigger_type == "scheduled")
            .where(LearningAutomationRun.status == "succeeded")
            .where(LearningAutomationRun.finished_at.is_not(None))
            .order_by(LearningAutomationRun.finished_at.desc())
            .limit(100)
        )
    ).scalars().all()
    latest_training_at = next(
        (row.finished_at for row in rows if _automation_run_advanced_training(row)),
        None,
    )
    next_after = latest_training_at + timedelta(seconds=interval_seconds) if latest_training_at else None
    due = next_after is None or next_after <= now
    return {
        "training_enabled": bool(settings.LEARNING_AUTO_TRAINING_ENABLED),
        "training_due": due,
        "training_interval_seconds": interval_seconds,
        "training_days": _bounded_days(getattr(settings, "LEARNING_AUTO_TRAINING_DAYS", 1), default=1),
        "latest_training_at": _iso(latest_training_at),
        "training_next_after": _iso(next_after),
    }


def _auto_training_interval_skip(gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "reason": "training_interval_not_due",
        "latest_training_at": gate.get("latest_training_at"),
        "next_training_after": gate.get("training_next_after"),
        "interval_seconds": gate.get("training_interval_seconds"),
    }


def _yesterday_training_window(reference: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Return the BJT natural-day window used by daily incremental training."""
    now = reference or now_bjt()
    today_start = datetime.combine(now.date(), datetime_time.min)
    start = today_start - timedelta(days=1)
    return start, today_start, start.date().isoformat()


def _with_common_filters(
    condition,
    model: Any,
    *,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    created_after=None,
):
    if created_after is not None and hasattr(model, "created_at"):
        condition = and_(condition, model.created_at >= created_after)
    if department := _clean_filter(department):
        if hasattr(model, "department"):
            condition = and_(condition, model.department == department)
    if org_unit_id := _clean_filter(org_unit_id):
        if hasattr(model, "org_unit_id"):
            condition = and_(condition, model.org_unit_id == org_unit_id)
    if skill_id := _clean_filter(skill_id):
        if hasattr(model, "skill_id"):
            condition = and_(condition, model.skill_id == skill_id)
    if run_id := _clean_filter(run_id):
        if hasattr(model, "run_id"):
            condition = and_(condition, model.run_id == run_id)
    if status := _clean_filter(status):
        if hasattr(model, "status"):
            condition = and_(condition, model.status == status)
    return condition


def _event_tool_condition(sf_tool: str | None):
    tool = _clean_filter(sf_tool)
    if not tool:
        return None
    like = f"%{tool}%"
    return or_(
        LearningEvent.metadata_json["tool"].astext == tool,
        LearningEvent.source_id.ilike(like),
        LearningEvent.redacted_summary.ilike(like),
    )


def _event_filter(
    user: User,
    *,
    created_after=None,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    source_type: str | None = None,
    modality: str | None = None,
    sensitivity_level: str | None = None,
    sf_tool: str | None = None,
):
    condition = _with_common_filters(
        _scope_filter(LearningEvent, user),
        LearningEvent,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        status=status,
        created_after=created_after,
    )
    if event_type := _clean_filter(event_type):
        condition = and_(condition, LearningEvent.event_type == event_type)
    if source_type := _clean_filter(source_type):
        condition = and_(condition, LearningEvent.source_type == source_type)
    if modality := _clean_filter(modality):
        condition = and_(condition, LearningEvent.modality == modality)
    if sensitivity_level := _clean_filter(sensitivity_level):
        condition = and_(condition, LearningEvent.sensitivity_level == sensitivity_level)
    tool_condition = _event_tool_condition(sf_tool)
    if tool_condition is not None:
        condition = and_(condition, tool_condition)
    return condition


def _artifact_filter(
    user: User,
    *,
    created_after=None,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    artifact_kind: str | None = None,
    target_type: str | None = None,
    sink_type: str | None = None,
    sensitivity_level: str | None = None,
    event_ids=None,
):
    condition = _with_common_filters(
        _scope_filter(LearningArtifact, user),
        LearningArtifact,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        status=status,
        created_after=created_after,
    )
    if artifact_kind := _clean_filter(artifact_kind):
        condition = and_(condition, LearningArtifact.artifact_kind == artifact_kind)
    if target_type := _clean_filter(target_type):
        condition = and_(condition, LearningArtifact.target_type == target_type)
    if sink_type := _clean_filter(sink_type):
        condition = and_(condition, LearningArtifact.sink_type == sink_type)
    if sensitivity_level := _clean_filter(sensitivity_level):
        condition = and_(condition, LearningArtifact.sensitivity_level == sensitivity_level)
    if event_ids is not None:
        condition = and_(condition, LearningArtifact.event_id.in_(event_ids))
    return condition


def _candidate_filter(
    user: User,
    *,
    created_after=None,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    risk_level: str | None = None,
    sf_tool: str | None = None,
):
    condition = _with_common_filters(
        _scope_filter(ImprovementCandidate, user),
        ImprovementCandidate,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        status=status,
        created_after=created_after,
    )
    if target_type := _clean_filter(target_type):
        condition = and_(condition, ImprovementCandidate.target_type == target_type)
    if target_id := _clean_filter(target_id):
        condition = and_(condition, ImprovementCandidate.target_id == target_id)
    if risk_level := _clean_filter(risk_level):
        condition = and_(condition, ImprovementCandidate.risk_level == risk_level)
    if sf_tool := _clean_filter(sf_tool):
        like = f"%{sf_tool}%"
        condition = and_(
            condition,
            or_(
                ImprovementCandidate.target_id == sf_tool,
                ImprovementCandidate.title.ilike(like),
                ImprovementCandidate.proposal.ilike(like),
            ),
        )
    return condition


def _ingestion_job_filter(
    user: User,
    *,
    created_after=None,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    status: str | None = None,
    sink_type: str | None = None,
    artifact_ids=None,
):
    condition = _with_common_filters(
        _scope_filter(LearningIngestionJob, user),
        LearningIngestionJob,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        status=status,
        created_after=created_after,
    )
    if sink_type := _clean_filter(sink_type):
        condition = and_(condition, LearningIngestionJob.sink_type == sink_type)
    if artifact_ids is not None:
        condition = and_(condition, LearningIngestionJob.artifact_id.in_(artifact_ids))
    return condition


async def _skill_department(db: AsyncSession, skill_id: str | None) -> tuple[str | None, str | None]:
    if not skill_id:
        return None, None
    if str(skill_id).startswith("project:"):
        try:
            from app.projects.models import Project

            project = await db.get(Project, str(skill_id).split(":", 1)[1])
            if project:
                return project.department, project.department_id
        except Exception:
            return None, None
    row = await db.get(Skill, skill_id)
    if not row:
        return None, None
    return row.department, row.org_unit_id


def _learning_artifact_training_source_id(row: LearningArtifact) -> str:
    skill_id = str(getattr(row, "skill_id", "") or "").strip()
    if skill_id:
        return skill_id
    target_type = str(getattr(row, "target_type", "") or "").strip().lower()
    target_id = str(getattr(row, "target_id", "") or "").strip()
    if target_type == "project" and target_id:
        return target_id if target_id.startswith("project:") else f"project:{target_id}"
    if target_type == "skill" and target_id:
        return target_id
    return ""


def _managed_project_training_gate(project: Any) -> dict[str, Any]:
    """Return a project's explicit training owner contract, if it has one."""
    manifest = _safe_dict(_safe_dict(getattr(project, "metadata_json", None)).get("manifest"))
    metadata = _safe_dict(manifest.get("metadata"))
    learning_loop = _safe_dict(metadata.get("learning_loop"))
    if learning_loop.get("managed_training") is not True:
        return {}
    try:
        minimum = max(1, int(learning_loop.get("minimum_training_samples") or 1))
    except (TypeError, ValueError):
        minimum = 1
    return {
        "managed_training": True,
        "minimum_training_samples": minimum,
        "candidate_source": str(learning_loop.get("candidate_source") or "project_managed_learning_loop"),
    }


async def _visible_skill_ids(db: AsyncSession, user: User) -> set[str] | None:
    if _is_global_user(user):
        return None
    dept = _user_department(user)
    if not dept:
        return set()
    rows = (await db.execute(select(Skill.id).where(Skill.department == dept).limit(2000))).scalars().all()
    return {str(item) for item in rows}


def _event_to_dict(row: LearningEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "source_hash": row.source_hash,
        "department": row.department,
        "org_unit_id": row.org_unit_id,
        "skill_id": row.skill_id,
        "user_id": row.user_id,
        "run_id": row.run_id,
        "modality": row.modality,
        "payload_ref": row.payload_ref,
        "redacted_summary": row.redacted_summary,
        "sensitivity_level": row.sensitivity_level,
        "status": row.status,
        "quality_score": row.quality_score,
        "policy_result": row.policy_result_json or {},
        "metadata": row.metadata_json or {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _artifact_content_preview(row: LearningArtifact) -> dict[str, Any]:
    """Return a governed, non-raw preview for UI lists.

    ``content_json`` can contain training inputs, Skill outputs, business impact,
    callback payload fragments or other sensitive source details. List/detail
    APIs in the learning loop should expose lineage and summaries, not raw
    sample payloads; materializers still read ``content_json`` internally.
    """
    content = _safe_dict(row.content_json)
    preview: dict[str, Any] = {
        "keys": sorted(str(key) for key in content.keys())[:30],
        "source_type": content.get("source_type"),
    }
    text = content.get("text") or content.get("proposal") or row.summary or row.title
    if text:
        preview["text"] = _clip(text, 800)
    source = _safe_dict(content.get("source"))
    if source:
        allowed_source_keys = (
            "run_id",
            "skill_id",
            "decision_log_id",
            "request_id",
            "todo_id",
            "dispatch_task_id",
            "artifact_id",
            "kind",
            "schema",
            "sha256",
            "training_job_id",
            "deployment_id",
        )
        preview["source"] = {key: _clip(source.get(key), 200) for key in allowed_source_keys if source.get(key) is not None}
    return {key: value for key, value in preview.items() if value not in (None, {}, [])}


def _decision_model_context_from_payload(
    output: dict[str, Any] | None,
    *,
    model_id: str | None = None,
) -> dict[str, Any]:
    meta = output.get("_skillforge_meta") if isinstance(output, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    context = meta.get("model_context") if isinstance(meta.get("model_context"), dict) else {}
    deployment = meta.get("active_model_deployment") if isinstance(meta.get("active_model_deployment"), dict) else {}
    deployment_id = str(
        context.get("model_deployment_id")
        or context.get("deployment_id")
        or deployment.get("model_deployment_id")
        or deployment.get("deployment_id")
        or deployment.get("id")
        or ""
    ).strip()
    if not deployment_id and str(model_id or "").startswith("deploy_"):
        deployment_id = str(model_id or "").strip()
    if not deployment_id:
        return {}
    return {
        "model_deployment_id": deployment_id[:80],
        "model_family": str(context.get("model_family") or deployment.get("model_family") or "")[:100] or None,
        "deployment_status": str(context.get("deployment_status") or deployment.get("status") or "")[:30] or None,
        "rollout_percent": context.get("rollout_percent") if "rollout_percent" in context else deployment.get("rollout_percent"),
        "rollout_selected": (
            context.get("rollout_selected")
            if "rollout_selected" in context
            else deployment.get("rollout_selected")
        ),
        "rollout_bucket": context.get("rollout_bucket") if "rollout_bucket" in context else deployment.get("rollout_bucket"),
        "rollout_reason": str(context.get("rollout_reason") or deployment.get("rollout_reason") or "")[:50] or None,
        "artifact_id": str(context.get("artifact_id") or deployment.get("artifact_id") or "")[:120] or None,
        "artifact_sha256": str(context.get("artifact_sha256") or deployment.get("artifact_sha256") or "")[:64] or None,
        "analysis_run_id": context.get("analysis_run_id"),
        "analysis_backend": context.get("analysis_backend"),
        "analysis_agent_id": context.get("analysis_agent_id"),
        "analysis_model": context.get("analysis_model"),
        "cache_key": context.get("cache_key"),
        "inference_status": context.get("inference_status"),
        "inference_backend": context.get("inference_backend"),
        "inference_gateway_id": context.get("inference_gateway_id"),
        "inference_profile": context.get("inference_profile"),
        "inference_text_sha256": context.get("inference_text_sha256"),
        "inference_metrics": context.get("inference_metrics"),
    }


def _training_agent_purpose(instance: Any | None) -> str:
    value = str(getattr(instance, "agent_purpose", "") or "skill_runtime").strip().lower()
    return value if value in {"skill_runtime", "analysis", "training", "media", "mixed"} else "skill_runtime"


def _training_agent_contract_from_instance(instance: Any | None) -> dict[str, Any]:
    if instance is None:
        return {
            "complete": False,
            "missing": ["agent"],
            "input_channels": ["training_job.gateway_payload", "learning_artifacts.dataset_package"],
            "control_ops": list(TRAINING_GATEWAY_REQUIRED_OPS),
            "advertised_ops": [],
            "required_ops": list(TRAINING_GATEWAY_REQUIRED_OPS),
            "missing_ops": list(TRAINING_GATEWAY_REQUIRED_OPS),
            "output_channels": ["training_job.gateway_result", "training_artifact_refs", "training_metrics"],
            "lineage_required": True,
            "flow_traceable": True,
            "submission_ready": False,
            "lifecycle_ready": False,
        }
    try:
        cap = json.loads(getattr(instance, "bridge_capabilities_json", "") or "{}")
    except (TypeError, ValueError):
        cap = {}
    cap = cap if isinstance(cap, dict) else {}
    training = cap.get("training") if isinstance(cap.get("training"), dict) else {}
    try:
        gpu_count = max(0, int(training.get("gpu_count") or 0))
    except (TypeError, ValueError):
        gpu_count = 0
    try:
        worker_count = max(0, int(training.get("worker_count") or 0))
    except (TypeError, ValueError):
        worker_count = 0
    advertised_op_set = {str(item or "").strip() for item in (cap.get("ops") or []) if str(item or "").strip()}
    advertised_ops = sorted(advertised_op_set)[:40]
    supported_tasks = [str(item or "").strip() for item in training.get("supported_tasks") or [] if str(item or "").strip()]
    purpose = _training_agent_purpose(instance)
    missing_ops = [op for op in TRAINING_GATEWAY_REQUIRED_OPS if op not in advertised_op_set]
    missing: list[str] = []
    if purpose not in {"training", "mixed"}:
        missing.append("agent_purpose")
    if not training.get("gateway"):
        missing.append("training_gateway")
    if missing_ops:
        missing.append("control_ops")
    if not supported_tasks:
        missing.append("supported_tasks")
    submission_ready = (
        purpose in {"training", "mixed"}
        and bool(training.get("gateway"))
        and "training.submit_job" in advertised_op_set
        and bool(supported_tasks)
    )
    lifecycle_ready = submission_ready and not missing_ops
    return {
        "complete": not missing,
        "missing": missing,
        "purpose": purpose,
        "gateway_kind": str(
            cap.get("gateway_kind")
            or getattr(instance, "bridge_gateway_kind", "")
            or getattr(instance, "agent_type", "")
            or "unknown"
        )[:50],
        "input_channels": ["training_job.gateway_payload", "learning_artifacts.dataset_package"],
        "control_ops": list(TRAINING_GATEWAY_REQUIRED_OPS),
        "advertised_ops": advertised_ops,
        "required_ops": list(TRAINING_GATEWAY_REQUIRED_OPS),
        "missing_ops": missing_ops,
        "supported_tasks": supported_tasks,
        "gpu_count": gpu_count,
        "worker_count": worker_count,
        "output_channels": ["training_job.gateway_result", "training_artifact_refs", "training_metrics"],
        "lineage_required": True,
        "flow_traceable": True,
        "submission_ready": submission_ready,
        "lifecycle_ready": lifecycle_ready,
    }


async def _training_agent_context(db: AsyncSession, row: TrainingJob) -> dict[str, Any]:
    gateway_id = str(row.target_gateway_id or "").strip()
    if not gateway_id:
        return {}
    from app.execution.models import OpenClawInstance

    instance = await db.get(OpenClawInstance, gateway_id)
    return {
        "agent_id": gateway_id[:80],
        "agent_name": str(getattr(instance, "name", "") or gateway_id)[:120],
        "department": str(getattr(instance, "department", "") or row.department or "")[:80],
        "gateway_kind": str(
            getattr(instance, "bridge_gateway_kind", None)
            or getattr(instance, "agent_type", "")
            or "unknown"
        )[:50],
        "agent_purpose": _training_agent_purpose(instance),
        "is_active": bool(getattr(instance, "is_active", False)) if instance is not None else False,
        "contract": _training_agent_contract_from_instance(instance),
    }


def _training_dataset_package_control_summary(row: TrainingJob) -> dict[str, Any]:
    gateway_payload = row.gateway_payload_json if isinstance(row.gateway_payload_json, dict) else {}
    package = gateway_payload.get("dataset_package") if isinstance(gateway_payload.get("dataset_package"), dict) else {}
    if not package:
        return {}
    lineage = package.get("lineage") if isinstance(package.get("lineage"), dict) else {}
    samples = package.get("samples") if isinstance(package.get("samples"), list) else []
    sample_fingerprints: list[dict[str, Any]] = []
    for item in samples[:50]:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        sample_fingerprints.append({
            "id": str(item.get("id") or "")[:100] or None,
            "split": str(item.get("split") or "")[:20] or None,
            "source_type": str(metadata.get("source_type") or "")[:80] or None,
            "source_channel": str(metadata.get("source_channel") or "")[:80] or None,
            "has_input": metadata.get("has_input"),
            "has_output": metadata.get("has_output"),
            "has_formed_data": metadata.get("has_formed_data"),
            "formed_data_sha256": str(metadata.get("formed_data_sha256") or "")[:64] or None,
            "has_runtime_agent": metadata.get("has_runtime_agent"),
            "runtime_agent_id": str(metadata.get("runtime_agent_id") or "")[:80] or None,
            "runtime_agent_contract_complete": metadata.get("runtime_agent_contract_complete"),
            "has_human_feedback": metadata.get("has_human_feedback"),
            "feedback_source": str(metadata.get("feedback_source") or "")[:80] or None,
            "has_model_context": metadata.get("has_model_context"),
            "model_deployment_id": str(metadata.get("model_deployment_id") or "")[:80] or None,
            "input_sha256": str(metadata.get("input_sha256") or "")[:64] or None,
            "output_sha256": str(metadata.get("output_sha256") or "")[:64] or None,
            "sample_sha256": str(metadata.get("sample_sha256") or "")[:64] or None,
        })
    return {
        "format": str(package.get("format") or "")[:80] or None,
        "source": str(package.get("source") or "")[:80] or None,
        "dataset_ref": str(package.get("dataset_ref") or row.dataset_ref or "")[:500] or None,
        "target_skill_id": str(package.get("target_skill_id") or row.target_skill_id or "")[:100] or None,
        "manifest_hash": str(package.get("manifest_hash") or "")[:64] or None,
        "sample_count": package.get("sample_count"),
        "train_count": package.get("train_count"),
        "eval_count": package.get("eval_count"),
        "input_contract": package.get("input_contract") if isinstance(package.get("input_contract"), dict) else {},
        "output_contract": package.get("output_contract") if isinstance(package.get("output_contract"), dict) else {},
        "lineage": lineage,
        "sample_fingerprints": [
            {key: value for key, value in item.items() if value not in (None, "", [], {})}
            for item in sample_fingerprints
        ],
        "raw_payload_returned": False,
        "samples": "[REDACTED]",
    }


def _runtime_agent_purpose(instance: Any | None) -> str:
    value = str(getattr(instance, "agent_purpose", "") or "skill_runtime").strip().lower()
    return value if value in {"skill_runtime", "analysis", "training", "media", "mixed"} else "skill_runtime"


def _runtime_agent_contract_from_instance(instance: Any | None) -> dict[str, Any]:
    if instance is None:
        return {
            "complete": False,
            "missing": ["agent"],
            "input_channels": ["node_schedule_snapshot", "skill_run_request"],
            "control_ops": list(RUNTIME_AGENT_REQUIRED_OPS),
            "advertised_ops": [],
            "required_ops": list(RUNTIME_AGENT_REQUIRED_OPS),
            "required_ops_any": list(RUNTIME_AGENT_REQUIRED_OPS),
            "missing_ops": list(RUNTIME_AGENT_REQUIRED_OPS),
            "available_control_ops": [],
            "output_channels": ["decision_log", "execution_artifact", "execution_run"],
            "lineage_required": True,
            "flow_traceable": True,
            "submission_ready": False,
            "lifecycle_ready": False,
        }
    try:
        cap = json.loads(getattr(instance, "bridge_capabilities_json", "") or "{}")
    except (TypeError, ValueError):
        cap = {}
    cap = cap if isinstance(cap, dict) else {}
    advertised_op_set = {str(item or "").strip() for item in (cap.get("ops") or []) if str(item or "").strip()}
    advertised_ops = sorted(advertised_op_set)[:40]
    purpose = _runtime_agent_purpose(instance)
    available_control_ops = [op for op in RUNTIME_AGENT_REQUIRED_OPS if op in advertised_op_set]
    missing_ops = [] if available_control_ops else list(RUNTIME_AGENT_REQUIRED_OPS)
    missing: list[str] = []
    if purpose not in {"skill_runtime", "mixed"}:
        missing.append("agent_purpose")
    if not available_control_ops:
        missing.append("control_ops")
    return {
        "complete": not missing,
        "missing": missing,
        "purpose": purpose,
        "gateway_kind": str(
            cap.get("gateway_kind")
            or getattr(instance, "bridge_gateway_kind", "")
            or getattr(instance, "agent_type", "")
            or "unknown"
        )[:50],
        "input_channels": ["node_schedule_snapshot", "skill_run_request"],
        "control_ops": list(RUNTIME_AGENT_REQUIRED_OPS),
        "advertised_ops": advertised_ops,
        "required_ops": list(RUNTIME_AGENT_REQUIRED_OPS),
        "required_ops_any": list(RUNTIME_AGENT_REQUIRED_OPS),
        "missing_ops": missing_ops,
        "available_control_ops": available_control_ops,
        "output_channels": ["decision_log", "execution_artifact", "execution_run"],
        "lineage_required": True,
        "flow_traceable": True,
        "submission_ready": not missing,
        "lifecycle_ready": not missing,
    }


async def _runtime_agent_context(db: AsyncSession, row: ExecutionRun) -> dict[str, Any]:
    agent_id = str(row.source_instance_id or "").strip()
    if not agent_id:
        return {}
    from app.execution.models import OpenClawInstance

    instance = await db.get(OpenClawInstance, agent_id)
    return {
        "agent_id": agent_id[:80],
        "agent_name": str(getattr(instance, "name", "") or agent_id)[:120],
        "department": str(getattr(instance, "department", "") or "")[:80] or None,
        "gateway_kind": str(
            getattr(instance, "bridge_gateway_kind", None)
            or getattr(instance, "agent_type", "")
            or "unknown"
        )[:50],
        "agent_purpose": _runtime_agent_purpose(instance),
        "is_active": bool(getattr(instance, "is_active", False)) if instance is not None else False,
        "contract": _runtime_agent_contract_from_instance(instance),
    }


def _inference_agent_contract_from_instance(instance: Any | None) -> dict[str, Any]:
    if instance is None:
        return {
            "complete": False,
            "missing": ["agent"],
            "input_channels": ["intelligence_context_pack", "model_deployment_context"],
            "control_ops": list(INFERENCE_AGENT_REQUIRED_OPS),
            "advertised_ops": [],
            "required_ops": list(INFERENCE_AGENT_REQUIRED_OPS),
            "missing_ops": list(INFERENCE_AGENT_REQUIRED_OPS),
            "output_channels": ["analysis_result", "decision_model_context", "deployed_model_inference"],
            "lineage_required": True,
            "flow_traceable": True,
            "submission_ready": False,
            "lifecycle_ready": False,
        }
    try:
        cap = json.loads(getattr(instance, "bridge_capabilities_json", "") or "{}")
    except (TypeError, ValueError):
        cap = {}
    cap = cap if isinstance(cap, dict) else {}
    advertised_op_set = {str(item or "").strip() for item in (cap.get("ops") or []) if str(item or "").strip()}
    missing_ops = [op for op in INFERENCE_AGENT_REQUIRED_OPS if op not in advertised_op_set]
    purpose = _training_agent_purpose(instance)
    missing: list[str] = []
    if purpose not in {"training", "mixed"}:
        missing.append("agent_purpose")
    if missing_ops:
        missing.append("control_ops")
    return {
        "complete": not missing,
        "missing": missing,
        "purpose": purpose,
        "gateway_kind": str(
            cap.get("gateway_kind")
            or getattr(instance, "bridge_gateway_kind", "")
            or getattr(instance, "agent_type", "")
            or "unknown"
        )[:50],
        "input_channels": ["intelligence_context_pack", "model_deployment_context"],
        "control_ops": list(INFERENCE_AGENT_REQUIRED_OPS),
        "advertised_ops": sorted(advertised_op_set)[:40],
        "required_ops": list(INFERENCE_AGENT_REQUIRED_OPS),
        "missing_ops": missing_ops,
        "output_channels": ["analysis_result", "decision_model_context", "deployed_model_inference"],
        "lineage_required": True,
        "flow_traceable": True,
        "submission_ready": not missing_ops,
        "lifecycle_ready": not missing,
    }


async def _inference_agent_context(db: AsyncSession, gateway_id: str | None) -> dict[str, Any]:
    agent_id = str(gateway_id or "").strip()
    if not agent_id:
        return {}
    from app.execution.models import OpenClawInstance

    instance = await db.get(OpenClawInstance, agent_id)
    return {
        "agent_id": agent_id[:80],
        "agent_name": str(getattr(instance, "name", "") or agent_id)[:120],
        "department": str(getattr(instance, "department", "") or "")[:80] or None,
        "gateway_kind": str(
            getattr(instance, "bridge_gateway_kind", None)
            or getattr(instance, "agent_type", "")
            or "unknown"
        )[:50],
        "agent_purpose": _training_agent_purpose(instance),
        "is_active": bool(getattr(instance, "is_active", False)) if instance is not None else False,
        "contract": _inference_agent_contract_from_instance(instance),
    }


def _analysis_agent_purpose(instance: Any | None) -> str:
    value = str(getattr(instance, "agent_purpose", "") or "skill_runtime").strip().lower()
    return value if value in {"skill_runtime", "analysis", "training", "media", "mixed"} else "skill_runtime"


def _analysis_agent_contract_from_instance(instance: Any | None) -> dict[str, Any]:
    if instance is None:
        return {
            "complete": False,
            "missing": ["agent"],
            "input_channels": ["intelligence_context_pack", "model_deployment_context"],
            "control_ops": list(ANALYSIS_AGENT_REQUIRED_OPS),
            "advertised_ops": [],
            "required_ops": list(ANALYSIS_AGENT_REQUIRED_OPS),
            "missing_ops": list(ANALYSIS_AGENT_REQUIRED_OPS),
            "output_channels": ["analysis_result", "decision_model_context", "learning_event"],
            "lineage_required": True,
            "flow_traceable": True,
            "submission_ready": False,
            "lifecycle_ready": False,
        }
    try:
        cap = json.loads(getattr(instance, "bridge_capabilities_json", "") or "{}")
    except (TypeError, ValueError):
        cap = {}
    cap = cap if isinstance(cap, dict) else {}
    advertised_op_set = {str(item or "").strip() for item in (cap.get("ops") or []) if str(item or "").strip()}
    missing_ops = [op for op in ANALYSIS_AGENT_REQUIRED_OPS if op not in advertised_op_set]
    purpose = _analysis_agent_purpose(instance)
    missing: list[str] = []
    if purpose not in {"analysis", "mixed"}:
        missing.append("agent_purpose")
    if missing_ops:
        missing.append("control_ops")
    return {
        "complete": not missing,
        "missing": missing,
        "purpose": purpose,
        "gateway_kind": str(
            cap.get("gateway_kind")
            or getattr(instance, "bridge_gateway_kind", "")
            or getattr(instance, "agent_type", "")
            or "unknown"
        )[:50],
        "input_channels": ["intelligence_context_pack", "model_deployment_context"],
        "control_ops": list(ANALYSIS_AGENT_REQUIRED_OPS),
        "advertised_ops": sorted(advertised_op_set)[:40],
        "required_ops": list(ANALYSIS_AGENT_REQUIRED_OPS),
        "missing_ops": missing_ops,
        "output_channels": ["analysis_result", "decision_model_context", "learning_event"],
        "lineage_required": True,
        "flow_traceable": True,
        "submission_ready": not missing_ops,
        "lifecycle_ready": not missing,
    }


async def _analysis_agent_context(db: AsyncSession, agent_id: str | None) -> dict[str, Any]:
    clean_id = str(agent_id or "").strip()
    if not clean_id:
        return {}
    from app.execution.models import OpenClawInstance

    instance = await db.get(OpenClawInstance, clean_id)
    return {
        "agent_id": clean_id[:80],
        "agent_name": str(getattr(instance, "name", "") or clean_id)[:120],
        "department": str(getattr(instance, "department", "") or "")[:80] or None,
        "gateway_kind": str(
            getattr(instance, "bridge_gateway_kind", None)
            or getattr(instance, "agent_type", "")
            or "unknown"
        )[:50],
        "agent_purpose": _analysis_agent_purpose(instance),
        "is_active": bool(getattr(instance, "is_active", False)) if instance is not None else False,
        "contract": _analysis_agent_contract_from_instance(instance),
    }


async def _link_model_context_to_decision(
    db: AsyncSession,
    event: LearningEvent,
    *,
    decision_log_id: int,
    model_context: dict[str, Any],
) -> None:
    deployment_id = str(model_context.get("model_deployment_id") or "").strip()
    if not deployment_id:
        return
    metadata = {
        "event_id": event.id,
        "model_family": model_context.get("model_family"),
        "deployment_status": model_context.get("deployment_status"),
        "rollout_percent": model_context.get("rollout_percent"),
        "rollout_selected": model_context.get("rollout_selected"),
        "rollout_bucket": model_context.get("rollout_bucket"),
        "rollout_reason": model_context.get("rollout_reason"),
        "artifact_id": model_context.get("artifact_id"),
        "artifact_sha256": model_context.get("artifact_sha256"),
        "analysis_run_id": model_context.get("analysis_run_id"),
        "analysis_backend": model_context.get("analysis_backend"),
        "analysis_agent_id": model_context.get("analysis_agent_id"),
        "analysis_model": model_context.get("analysis_model"),
        "inference_status": model_context.get("inference_status"),
        "inference_backend": model_context.get("inference_backend"),
        "inference_gateway_id": model_context.get("inference_gateway_id"),
        "inference_profile": model_context.get("inference_profile"),
        "inference_text_sha256": model_context.get("inference_text_sha256"),
        "inference_metrics": model_context.get("inference_metrics"),
    }
    await _upsert_edge(
        db,
        from_type="model_deployment",
        from_id=deployment_id,
        to_type="decision_log",
        to_id=str(decision_log_id),
        relation="used_as_model",
        department=event.department,
        org_unit_id=event.org_unit_id,
        skill_id=event.skill_id,
        run_id=event.run_id,
        metadata={key: value for key, value in metadata.items() if value not in (None, "", [], {})},
    )
    if event.run_id:
        await _upsert_edge(
            db,
            from_type="model_deployment",
            from_id=deployment_id,
            to_type="run",
            to_id=str(event.run_id),
            relation="used_as_model",
            department=event.department,
            org_unit_id=event.org_unit_id,
            skill_id=event.skill_id,
            run_id=event.run_id,
            metadata={key: value for key, value in metadata.items() if value not in (None, "", [], {})},
        )


async def _materialize_training_artifact(db: AsyncSession, event: LearningEvent, artifact: LearningArtifact) -> LearningArtifact:
    artifact.status = "materialized"
    artifact.sink_type = "training_sample"
    artifact.sink_id = artifact.id
    artifact.updated_at = now_bjt()
    await _upsert_edge(
        db,
        from_type="learning_artifact",
        from_id=artifact.id,
        to_type="training_sample",
        to_id=artifact.id,
        relation="created_sample",
        department=artifact.department or event.department,
        org_unit_id=artifact.org_unit_id or event.org_unit_id,
        skill_id=artifact.skill_id or event.skill_id,
        run_id=artifact.run_id or event.run_id,
    )
    return artifact


def _artifact_to_dict(row: LearningArtifact, *, include_content: bool = False) -> dict[str, Any]:
    item = {
        "id": row.id,
        "event_id": row.event_id,
        "artifact_kind": row.artifact_kind,
        "artifact_hash": row.artifact_hash,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "department": row.department,
        "org_unit_id": row.org_unit_id,
        "skill_id": row.skill_id,
        "run_id": row.run_id,
        "title": row.title,
        "summary": row.summary,
        "content_preview": _artifact_content_preview(row),
        "labels": row.labels_json or [],
        "quality_score": row.quality_score,
        "confidence": row.confidence,
        "sensitivity_level": row.sensitivity_level,
        "status": row.status,
        "review_status": row.review_status,
        "sink_type": row.sink_type,
        "sink_id": row.sink_id,
        "policy_result": row.policy_result_json or {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if include_content:
        item["content"] = row.content_json or {}
    return item


def _candidate_to_dict(row: ImprovementCandidate) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_artifact_id": row.source_artifact_id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "department": row.department,
        "org_unit_id": row.org_unit_id,
        "skill_id": row.skill_id,
        "run_id": row.run_id,
        "title": row.title,
        "proposal": row.proposal,
        "evidence_event_ids": row.evidence_event_ids_json or [],
        "risk_level": row.risk_level,
        "expected_impact": row.expected_impact_json or {},
        "priority_score": row.priority_score,
        "status": row.status,
        "review_id": row.review_id,
        "todo_id": row.todo_id,
        "git_commit": row.git_commit,
        "external_ref": row.external_ref_json or {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _automation_run_to_dict(row: LearningAutomationRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "trigger_type": row.trigger_type,
        "status": row.status,
        "days": row.days,
        "limit": row.limit,
        "materialize": row.materialize,
        "captured": row.captured_json or {},
        "result": row.result_json or {},
        "error": _clip(row.error, 1000) if row.error else None,
        "metadata": row.metadata_json or {},
        "created_by": row.created_by,
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _governance_task_to_dict(row: LearningGovernanceTask) -> dict[str, Any]:
    age_hours = max(0.0, (now_bjt() - row.created_at).total_seconds() / 3600) if row.created_at else 0.0
    return {
        "id": row.id,
        "candidate_id": row.candidate_id,
        "queue_id": row.queue_id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "department": row.department,
        "org_unit_id": row.org_unit_id,
        "title": row.title,
        "summary": row.summary,
        "assignee": row.assignee,
        "status": row.status,
        "priority_score": row.priority_score,
        "payload": row.payload_json or {},
        "created_by": row.created_by,
        "age_hours": round(age_hours, 2),
        "is_stale": row.status in {"pending", "in_progress"} and age_hours >= LEARNING_GOVERNANCE_TASK_STALE_HOURS,
        "stale_threshold_hours": LEARNING_GOVERNANCE_TASK_STALE_HOURS,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "completed_at": _iso(row.completed_at),
    }


def _edge_to_dict(row: LearningFlowEdge) -> dict[str, Any]:
    return {
        "id": row.id,
        "from_type": row.from_type,
        "from_id": row.from_id,
        "to_type": row.to_type,
        "to_id": row.to_id,
        "relation": row.relation,
        "department": row.department,
        "org_unit_id": row.org_unit_id,
        "skill_id": row.skill_id,
        "run_id": row.run_id,
        "weight": row.weight,
        "status": row.status,
        "metadata": row.metadata_json or {},
        "created_at": _iso(row.created_at),
    }


def _pulse_count_map(rows: list[Any], key_index: int = 0, count_index: int = 1) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        key = str(row[key_index] or "-")
        result[key] = int(row[count_index] or 0)
    return result


def _pulse_duration_seconds(started: datetime | None, finished: datetime | None) -> float | None:
    if not started or not finished:
        return None
    return max(0.0, (finished - started).total_seconds())


def _pulse_eta_seconds(job: TrainingJob, task: TrainingJobTask | None, avg_completed_seconds: float | None) -> int | None:
    if job.status in {"completed", "failed", "cancelled"}:
        return 0
    if task is not None and task.status in {"running", "pending"}:
        progress = max(0.0, min(0.99, float(task.progress or 0)))
        elapsed = _pulse_duration_seconds(task.created_at, now_bjt()) or 0
        if progress > 0.05 and elapsed > 0:
            return int(max(0, elapsed * (1 - progress) / progress))
    if avg_completed_seconds:
        return int(avg_completed_seconds)
    return None


def _pulse_eta_text(seconds: int | None, status: str | None) -> str:
    if status in {"completed"}:
        return "已完成"
    if status in {"failed", "cancelled"}:
        return "无需预计"
    if seconds is None:
        return "等待节点回传"
    if seconds < 60:
        return f"约 {max(1, seconds)} 秒"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"约 {minutes} 分钟"
    hours = round(minutes / 60, 1)
    return f"约 {hours} 小时"


def _pulse_version_text(value: datetime | None) -> str:
    if value is None:
        return "-"
    if value.year == now_bjt().year:
        return value.strftime("%m-%d %H:%M")
    return value.strftime("%Y-%m-%d")


def _pulse_item(
    *,
    id: str,
    title: str,
    meta: str,
    status: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "title": _clip(title, 240),
        "meta": _clip(meta, 300),
        "status": status,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "payload": payload or {},
    }


def _pulse_title(value: Any, fallback: str = "-") -> str:
    text = str(value or "").strip()
    return _clip(text or fallback, 240)


def _pulse_summary(value: Any, fallback: str = "-") -> str:
    text = str(value or "").strip()
    return _clip(text or fallback, 500)


def _pulse_entity_label(entity_type: str | None, entity_id: str | None) -> str:
    left = str(entity_type or "").strip() or "entity"
    right = str(entity_id or "").strip() or "-"
    return f"{left}:{right}"


def _pulse_flow_row(
    *,
    title: str,
    summary: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    source: str | None = None,
    destination: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    input_preview: Any = None,
    output_preview: Any = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "title": _pulse_title(title),
        "summary": _pulse_summary(summary, "") if summary else "",
        "status": status,
        "status_text": _status_label(status),
        "reason": _pulse_summary(reason, "") if reason else "",
        "source": _clip(source, 240) if source else "",
        "destination": _clip(destination, 240) if destination else "",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity": _pulse_entity_label(entity_type, entity_id) if entity_type and entity_id else "",
        "input_preview": _redacted_payload(input_preview, limit=1200) if input_preview not in (None, "", [], {}) else None,
        "output_preview": _redacted_payload(output_preview, limit=1200) if output_preview not in (None, "", [], {}) else None,
        "metadata": metadata or {},
    }
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _pulse_attach_counts(container: dict[str, Any], total_count: int | None) -> dict[str, Any]:
    items = container.get("items") or []
    returned_count = len(items) if isinstance(items, list) else 0
    try:
        clean_total = int(total_count if total_count is not None else returned_count)
    except (TypeError, ValueError):
        clean_total = returned_count
    clean_total = max(clean_total, returned_count, 0)
    container["returned_count"] = returned_count
    container["total_count"] = clean_total
    container["hidden_count"] = max(clean_total - returned_count, 0)
    return container


def _pulse_event_flow_row(row: LearningEvent) -> dict[str, Any]:
    return _pulse_flow_row(
        title=row.redacted_summary or row.event_type,
        summary=row.redacted_summary or "原始记录已进入学习流。",
        status=row.status,
        reason="通过来源哈希与权限范围校验后进入学习流；页面只展示脱敏摘要。",
        source=f"{_source_type_label(row.source_type)} · {row.source_id}",
        destination="数据清洗",
        entity_type="learning_event",
        entity_id=row.id,
        input_preview={
            "source_type": row.source_type,
            "source_id": row.source_id,
            "source_hash": row.source_hash,
            "payload_ref": row.payload_ref,
            "modality": row.modality,
            "sensitivity_level": row.sensitivity_level,
        },
        output_preview={
            "event_id": row.id,
            "event_type": row.event_type,
            "quality_score": row.quality_score,
            "created_at": _iso(row.created_at),
        },
        metadata=_safe_dict(row.metadata_json),
    )


def _pulse_artifact_flow_row(row: LearningArtifact) -> dict[str, Any]:
    dropped = row.status == "ignored"
    input_preview: dict[str, Any] = {
        "artifact_kind": row.artifact_kind,
        "artifact_hash": row.artifact_hash,
        "quality_score": row.quality_score,
        "confidence": row.confidence,
    }
    if not dropped:
        input_preview.update({
            "target_type": row.target_type,
            "content_preview": _artifact_content_preview(row),
        })
    return _pulse_flow_row(
        title=row.title or _artifact_kind_label(row.artifact_kind),
        summary=row.summary or ("该资产未进入后续链路。" if dropped else _artifact_kind_label(row.artifact_kind)),
        status=row.status,
        reason=_pulse_artifact_keep_reason(row),
        source=f"learning_event:{row.event_id}",
        destination="已丢弃 / 不进入下游" if dropped else _pulse_artifact_destination(row),
        entity_type="learning_artifact",
        entity_id=row.id,
        input_preview=input_preview,
        output_preview=(
            {"status": row.status, "policy_result": row.policy_result_json or {}}
            if dropped
            else {
                "sink_type": row.sink_type,
                "sink_id": row.sink_id,
                "status": row.status,
                "updated_at": _iso(row.updated_at),
            }
        ),
        metadata=None if dropped else {
            "labels": row.labels_json or [],
            "review_status": row.review_status,
            "policy_result": row.policy_result_json or {},
        },
    )


def _pulse_ingestion_flow_row(row: LearningIngestionJob) -> dict[str, Any]:
    return _pulse_flow_row(
        title=f"{_pulse_sink_label(row.sink_type)} 同步任务",
        summary=row.error or "同步任务已记录。",
        status=row.status,
        reason="将清洗后的资产写入数据库、向量库或训练样本库。",
        source=f"learning_artifact:{row.artifact_id or '-'}",
        destination=f"{row.sink_type}:{row.sink_id or '-'}",
        entity_type="learning_ingestion_job",
        entity_id=row.job_id,
        input_preview={
            "action": row.action,
            "sink_type": row.sink_type,
            "artifact_id": row.artifact_id,
            "policy": row.policy_json or {},
        },
        output_preview={
            "sink_id": row.sink_id,
            "attempts": row.attempts,
            "started_at": _iso(row.started_at),
            "finished_at": _iso(row.finished_at),
            "error": row.error,
        },
    )


def _pulse_artifact_keep_reason(row: LearningArtifact) -> str:
    if row.status == "ignored":
        policy = _safe_dict(row.policy_result_json)
        return str(policy.get("reason") or policy.get("decision_reason") or "该资产被标记为忽略，不进入后续知识、样本或记忆链路。")
    reasons = []
    if row.quality_score:
        reasons.append(f"质量分 {round(float(row.quality_score), 2)}")
    if row.confidence:
        reasons.append(f"置信度 {round(float(row.confidence), 2)}")
    if row.sink_type:
        reasons.append(f"已同步到 {_pulse_sink_label(row.sink_type)}")
    if row.status == "materialized":
        reasons.append("已物化")
    return "；".join(reasons) or "满足当前清洗规则，保留为可复用学习资产。"


def _pulse_sink_label(value: str | None) -> str:
    mapping = {
        "knowledge_document": "知识库 / 向量库",
        "training_sample": "训练样本库",
        "agent_memory": "Agent 记忆",
        "improvement_candidate": "改进候选池",
    }
    return mapping.get(str(value or ""), str(value or "-"))


def _pulse_artifact_destination(row: LearningArtifact) -> str:
    if row.sink_type:
        return f"{_pulse_sink_label(row.sink_type)} · {row.sink_id or '-'}"
    if row.target_type:
        return f"{row.target_type}:{row.target_id or '-'}"
    return "待分配下游"


def _pulse_sample_lineage_preview(rows: list[LearningArtifact], limit: int = 5) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        if row.artifact_kind not in TRAINING_ARTIFACTS:
            continue
        result.append({
            "artifact_id": row.id,
            "title": _pulse_title(row.title or _artifact_kind_label(row.artifact_kind)),
            "kind": _artifact_kind_label(row.artifact_kind),
            "status": row.status,
            "status_text": _status_label(row.status),
            "source_event_id": row.event_id,
            "input_sha256": _safe_dict(row.content_json).get("input_sha256"),
            "output_sha256": _safe_dict(row.content_json).get("output_sha256"),
            "sample_sha256": _safe_dict(row.content_json).get("sample_sha256"),
        })
        if len(result) >= limit:
            break
    return result


def _pulse_training_job_row(
    row: TrainingJob,
    tasks: list[TrainingJobTask],
    sample_count: int,
) -> dict[str, Any]:
    latest_task = tasks[0] if tasks else None
    task_metrics = _training_metric_preview(latest_task.metrics_json) if latest_task else {}
    package = _training_dataset_package_control_summary(row)
    sample_total = package.get("sample_count") or sample_count
    return _pulse_flow_row(
        title=row.title,
        summary=row.objective or "训练任务已进入受控训练链路。",
        status=row.status,
        reason="等待审核后下发训练 Agent。" if row.status == "awaiting_review" else "已进入训练 Agent 执行链路。",
        source=row.dataset_ref or package.get("dataset_ref") or "learning-artifacts://visible/training/latest",
        destination=row.target_gateway_id or "等待分配训练 Agent",
        entity_type="training_job",
        entity_id=row.id,
        input_preview={
            "dataset_ref": row.dataset_ref,
            "target_skill_id": row.target_skill_id,
            "job_type": row.job_type,
            "training_strategy": row.training_strategy,
            "sample_count": sample_total,
            "dataset_package": package,
        },
        output_preview={
            "latest_task_id": getattr(latest_task, "id", None),
            "progress": getattr(latest_task, "progress", None),
            "metrics": task_metrics,
            "logs_url": getattr(latest_task, "logs_url", None),
            "error": getattr(latest_task, "error_message", None),
        },
        metadata={
            "gateway_id": row.target_gateway_id,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "approval_required": row.approval_required,
            "approved_by": row.approved_by,
        },
    )


def _training_job_model_test_context(
    job: TrainingJob | None,
    task: TrainingJobTask | None = None,
    deployment: TrainingModelDeployment | None = None,
) -> dict[str, Any]:
    if job is None:
        return {}
    spec = _safe_dict(job.spec_json)
    dataset = _safe_dict(spec.get("dataset"))
    deployment_spec = _safe_dict(spec.get("deployment"))
    package = _training_dataset_package_control_summary(job)
    metrics = _training_eval_metrics(task)
    artifact_ids = _training_dataset_artifact_ids_for_job(job)
    model_name = str(
        deployment_spec.get("model_family")
        or spec.get("model_family")
        or spec.get("base_model")
        or spec.get("model")
        or f"{job.target_skill_id or job.id}:{job.job_type}"
    ).strip()
    artifact_model_name = str(
        getattr(deployment, "model_family", "")
        or metrics.get("model_family")
        or metrics.get("adapter_name")
        or model_name
    ).strip()
    artifact_sha256 = str(
        metrics.get("artifact_sha256")
        or metrics.get("adapter_sha256")
        or _safe_dict(getattr(deployment, "artifact_ref_json", None)).get("sha256")
        or ""
    ).strip()
    sample_count = package.get("sample_count") or dataset.get("sample_total") or dataset.get("learning_artifact_ids_count")
    return {
        "training_job_id": job.id,
        "training_job_title": job.title,
        "training_date": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "target_skill_id": job.target_skill_id,
        "target_gateway_id": job.target_gateway_id,
        "job_type": job.job_type,
        "model_name": model_name,
        "artifact_model_name": artifact_model_name,
        "model_deployment_id": getattr(deployment, "id", None),
        "deployment_status": getattr(deployment, "status", None),
        "artifact_id": getattr(deployment, "artifact_id", None) or metrics.get("artifact_id"),
        "artifact_sha256": artifact_sha256 or None,
        "win_rate": metrics.get("win_rate") or metrics.get("eval_win_rate"),
        "passed": metrics.get("passed"),
        "training_data": {
            "job_id": job.id,
            "dataset_ref": package.get("dataset_ref") or dataset.get("ref") or job.dataset_ref,
            "manifest_hash": package.get("manifest_hash") or dataset.get("manifest_hash"),
            "sample_count": sample_count,
            "train_count": package.get("train_count") or dataset.get("train_count") or metrics.get("train_samples"),
            "eval_count": package.get("eval_count") or dataset.get("eval_count") or metrics.get("eval_samples"),
            "artifact_count": len(artifact_ids) or dataset.get("learning_artifact_ids_count"),
            "endpoint": f"/api/learning/training-jobs/{job.id}/dataset",
            "raw_payload_returned": False,
        },
    }


def _pulse_eval_row(
    task: TrainingJobTask,
    *,
    job: TrainingJob | None = None,
    deployment: TrainingModelDeployment | None = None,
    instance: OpenClawInstance | None = None,
    tasks: list[TrainingJobTask] | None = None,
) -> dict[str, Any]:
    metrics = _training_metric_preview(task.metrics_json)
    metrics_dict = metrics if isinstance(metrics, dict) else {}
    passed = metrics_dict.get("passed")
    model_context = _training_job_model_test_context(job, task, deployment)
    chat_context = (
        _pulse_deployment_chat_context(deployment, job, instance, tasks or [])
        if deployment is not None
        else {}
    )
    return _pulse_flow_row(
        title=f"评估任务 {task.id}",
        summary="训练产物评估已完成。" if task.status == "completed" else "训练产物评估未完成。",
        status="passed" if passed is True else "failed" if passed is False or task.status == "failed" else task.status,
        reason=str(metrics_dict.get("reason") or metrics_dict.get("summary") or "根据训练任务回传指标判定。"),
        source=f"training_job:{task.job_id}",
        destination=str(metrics_dict.get("artifact_uri") or metrics_dict.get("artifact_ref") or metrics_dict.get("artifact_sha256") or "待生成产物引用"),
        entity_type="training_job",
        entity_id=task.job_id,
        input_preview={
            "job_id": task.job_id,
            "gateway_id": task.gateway_id,
            "worker_id": task.worker_id,
            "training_model": model_context.get("model_name"),
            "training_date": model_context.get("training_date"),
            "training_data": model_context.get("training_data"),
        },
        output_preview={
            "progress": task.progress,
            "metrics": metrics,
            "error": task.error_message,
            "logs_url": task.logs_url,
            "model_name": model_context.get("artifact_model_name") or model_context.get("model_name"),
            "model_deployment_id": model_context.get("model_deployment_id"),
            "artifact_id": model_context.get("artifact_id"),
            "artifact_sha256": model_context.get("artifact_sha256"),
            "chat_context": chat_context or None,
            "model_test_context": model_context or None,
        },
        metadata={
            "task_id": task.id,
            "updated_at": _iso(task.updated_at),
            "model_test_context": model_context,
            "training_data": model_context.get("training_data") if model_context else None,
            "chat_context": chat_context or None,
        },
    )


def _is_training_eval_task(task: TrainingJobTask) -> bool:
    metrics = _safe_dict(task.metrics_json)
    op = str(metrics.get("op") or "").strip()
    if op:
        return op == "training.evaluate"
    return any(key in metrics for key in ("passed", "win_rate", "eval_win_rate", "checks"))


def _training_eval_metrics(task: TrainingJobTask | None) -> dict[str, Any]:
    if task is None:
        return {}
    metrics = _safe_dict(task.metrics_json)
    nested = _safe_dict(metrics.get("metrics"))
    merged = {**nested, **{key: value for key, value in metrics.items() if key != "metrics"}}
    return merged


def _pulse_deployment_artifact_uri(artifact_ref: dict[str, Any]) -> str:
    return str(
        artifact_ref.get("uri")
        or artifact_ref.get("artifact_uri")
        or artifact_ref.get("url")
        or artifact_ref.get("path")
        or ""
    ).strip()


def _pulse_deployment_gateway_capabilities(instance: OpenClawInstance | None) -> dict[str, Any]:
    if instance is None:
        return {}
    try:
        data = json.loads(getattr(instance, "bridge_capabilities_json", "") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _pulse_deployment_artifact_matches(artifact_ref: dict[str, Any], artifact: Any) -> bool:
    if not isinstance(artifact, dict):
        return False
    ref_id = str(artifact_ref.get("id") or "").strip()
    ref_sha = str(artifact_ref.get("sha256") or "").strip().lower()
    ref_uri = _pulse_deployment_artifact_uri(artifact_ref)
    raw_uri = str(
        artifact.get("uri")
        or artifact.get("artifact_uri")
        or artifact.get("url")
        or artifact.get("path")
        or ""
    ).strip()
    raw_sha = str(artifact.get("sha256") or artifact.get("hash") or "").strip().lower()
    raw_ids = {
        str(artifact.get("id") or "").strip(),
        str(artifact.get("name") or "").strip(),
        str(artifact.get("filename") or "").strip(),
    }
    return bool(
        (ref_sha and raw_sha and ref_sha == raw_sha)
        or (ref_uri and raw_uri and ref_uri == raw_uri)
        or (ref_id and ref_id in raw_ids)
    )


def _pulse_deployment_artifact_proof_ready(
    artifact_ref: dict[str, Any],
    tasks: list[TrainingJobTask],
) -> bool:
    if not tasks:
        return False
    for task in tasks[:50]:
        metrics = _safe_dict(getattr(task, "metrics_json", None))
        gateway_result = _safe_dict(metrics.get("gateway_result"))
        artifacts = _safe_list(gateway_result.get("artifacts"))
        if any(_pulse_deployment_artifact_matches(artifact_ref, item) for item in artifacts):
            return True
    return False


def _pulse_deployment_target_gateway_id(
    row: TrainingModelDeployment | None,
    job: TrainingJob | None = None,
) -> str:
    if row is not None:
        value = str(getattr(row, "deployment_target_gateway_id", "") or "").strip()
        if value:
            return value
        artifact_ref = _safe_dict(getattr(row, "artifact_ref_json", None))
        value = str(
            artifact_ref.get("deployment_target_gateway_id")
            or artifact_ref.get("target_gateway_id")
            or ""
        ).strip()
        if value:
            return value
    return str(getattr(job, "target_gateway_id", "") or "").strip() if job is not None else ""


def _pulse_deployment_chat_context(
    row: TrainingModelDeployment,
    job: TrainingJob | None = None,
    instance: OpenClawInstance | None = None,
    tasks: list[TrainingJobTask] | None = None,
) -> dict[str, Any]:
    artifact_ref = _safe_dict(row.artifact_ref_json)
    artifact_uri = _pulse_deployment_artifact_uri(artifact_ref)
    artifact_proof_ready = _pulse_deployment_artifact_proof_ready(artifact_ref, tasks or [])
    training_gateway_id = str(getattr(job, "target_gateway_id", "") or "").strip() if job is not None else ""
    target_gateway_id = _pulse_deployment_target_gateway_id(row, job)
    cap = _pulse_deployment_gateway_capabilities(instance)
    ops = {str(item or "").strip() for item in (cap.get("ops") or []) if str(item or "").strip()}
    target_gateway_kind = str(
        cap.get("gateway_kind")
        or getattr(instance, "bridge_gateway_kind", "")
        or getattr(instance, "agent_type", "")
        or ""
    ).strip()
    chat_disabled_reason = None
    if not row.id:
        chat_disabled_reason = "缺少模型部署 ID"
    elif row.status not in {"active", "canary"}:
        chat_disabled_reason = "部署未进入灰度或已激活状态"
    elif not artifact_uri:
        chat_disabled_reason = "缺少模型产物 URI，无法在部署节点推理"
    elif not artifact_proof_ready:
        chat_disabled_reason = "缺少匹配的训练产物记录，无法确认模型部署可推理"
    elif not target_gateway_id:
        chat_disabled_reason = "缺少部署目标节点，无法路由模型推理"
    elif instance is None or getattr(instance, "is_active", True) is False:
        chat_disabled_reason = f"训练后模型推理节点 {target_gateway_id} 不存在或未启用"
    elif "training.inference" not in ops:
        chat_disabled_reason = f"训练后模型推理节点 {target_gateway_id} 未提供 training.inference 能力"
    chat_ready = chat_disabled_reason is None
    return {
        "ready": chat_ready,
        "disabled_reason": chat_disabled_reason,
        "model_deployment_id": row.id,
        "model_family": row.model_family,
        "training_job_id": row.job_id,
        "department": row.department,
        "artifact_id": row.artifact_id,
        "artifact_sha256": str(artifact_ref.get("sha256") or "").strip() or None,
        "artifact_uri_present": bool(artifact_uri),
        "artifact_proof_ready": artifact_proof_ready,
        "deployment_status": row.status,
        "target_gateway_id": target_gateway_id or None,
        "training_gateway_id": training_gateway_id if training_gateway_id and training_gateway_id != target_gateway_id else None,
        "target_gateway_kind": target_gateway_kind[:50] or None,
        "target_gateway_active": bool(getattr(instance, "is_active", False)) if instance is not None else False,
        "inference_ready": chat_ready,
        "inference_disabled_reason": chat_disabled_reason,
    }


def _pulse_deployment_row(
    row: TrainingModelDeployment,
    job: TrainingJob | None = None,
    instance: OpenClawInstance | None = None,
    tasks: list[TrainingJobTask] | None = None,
) -> dict[str, Any]:
    artifact_ref = _safe_dict(row.artifact_ref_json)
    chat_context = _pulse_deployment_chat_context(row, job, instance, tasks)
    target_gateway_id = chat_context.get("target_gateway_id")
    return _pulse_flow_row(
        title=row.model_family,
        summary=row.request_reason or "模型产物已登记到部署链路。",
        status=row.status,
        reason=(
            "当前部署可被目标 Skill、决策链路和模型对话选择。"
            if chat_context["inference_ready"]
            else chat_context["disabled_reason"] or "部署尚未激活或仍在审核。"
        ),
        source=f"training_job:{row.job_id}",
        destination=", ".join(str(item) for item in (row.target_skill_ids_json or [])) or "未绑定目标 Skill",
        entity_type="model_deployment",
        entity_id=row.id,
        input_preview={
            "model_deployment_id": row.id,
            "model_family": row.model_family,
            "training_job_id": row.job_id,
            "department": row.department,
            "artifact_id": row.artifact_id,
            "artifact_sha256": chat_context["artifact_sha256"],
            "artifact_ref": artifact_ref,
            "target_skill_ids": row.target_skill_ids_json or [],
            "target_gateway_id": target_gateway_id or None,
            "rollout_percent": row.rollout_percent,
        },
        output_preview={
            "deployment_id": row.id,
            "model_deployment_id": row.id,
            "model_family": row.model_family,
            "training_job_id": row.job_id,
            "department": row.department,
            "artifact_id": row.artifact_id,
            "artifact_sha256": chat_context["artifact_sha256"],
            "target_gateway_id": target_gateway_id or None,
            "status": row.status,
            "activated_at": _iso(row.activated_at),
            "rollback_to": row.rollback_to,
            "chat_context": chat_context,
        },
        metadata={
            "requested_by": row.requested_by,
            "approved_by": row.approved_by,
            "updated_at": _iso(row.updated_at),
            "chat_context": chat_context,
        },
    )


def _pulse_application_edge_row(edge: LearningFlowEdge) -> dict[str, Any]:
    metadata = _safe_dict(edge.metadata_json)
    inference = _safe_dict(metadata.get("inference_metrics"))
    return _pulse_flow_row(
        title=_pulse_relation_label(edge.relation),
        summary="模型部署已参与实际输出链路。",
        status=edge.status,
        reason=str(metadata.get("rollout_reason") or metadata.get("inference_status") or "由运行链路记录的模型上下文确认。"),
        source=f"{edge.from_type}:{edge.from_id}",
        destination=f"{edge.to_type}:{edge.to_id}",
        entity_type=edge.to_type,
        entity_id=edge.to_id,
        input_preview={
            "model_deployment_id": edge.from_id,
            "inference_backend": metadata.get("inference_backend"),
            "inference_gateway_id": metadata.get("inference_gateway_id"),
            "input_sha256": metadata.get("inference_text_sha256"),
            "prompt": metadata.get("prompt") or metadata.get("input") or metadata.get("input_preview"),
        },
        output_preview={
            "result": metadata.get("result") or metadata.get("output") or metadata.get("output_preview"),
            "inference_metrics": inference,
            "decision_log_id": edge.to_id if edge.to_type == "decision_log" else None,
        },
        metadata={key: value for key, value in metadata.items() if key not in {"prompt", "input", "output"}},
    )


def _pulse_cleaned_artifact_items(rows: list[LearningArtifact], limit: int) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    ordered_keys: list[tuple[str, str, str]] = []
    for row in rows:
        title = str(row.title or _artifact_kind_label(row.artifact_kind) or "").strip()
        if not title:
            title = _artifact_kind_label(row.artifact_kind)
        key = (str(row.artifact_kind or ""), str(row.status or ""), title)
        if key not in groups:
            groups[key] = {"row": row, "count": 0, "ids": []}
            ordered_keys.append(key)
        groups[key]["count"] += 1
        groups[key]["ids"].append(row.id)

    items: list[dict[str, Any]] = []
    for index, key in enumerate(ordered_keys[:limit], start=1):
        data = groups[key]
        row = data["row"]
        count = int(data["count"] or 0)
        ids = list(data["ids"])
        kind_label = _artifact_kind_label(row.artifact_kind)
        status_label = _status_label(row.status)
        title = row.title or kind_label
        meta = f"{kind_label} · {status_label}"
        payload = _artifact_to_dict(row)
        if count > 1:
            title = f"{title}（同类 {count} 条）"
            meta = f"{meta} · 已合并重复明细"
            payload = {
                **payload,
                "group_count": count,
                "group_artifact_ids": ids[:20],
                "grouped_by": ["artifact_kind", "status", "title"],
            }
        items.append(
            _pulse_item(
                id=row.id if count == 1 else f"artifact-group-{index}",
                title=title,
                meta=meta,
                status=row.status,
                entity_type="learning_artifact",
                entity_id=row.id,
                payload=payload,
            )
        )
    return items


def _pulse_relation_label(value: str | None) -> str:
    mapping = {
        "deployed_to": "模型部署可用",
        "used_as_model": "模型参与决策",
        "controlled_inference": "推理 Agent 控制",
    }
    return mapping.get(str(value or ""), str(value or "-"))


def _pulse_module(
    key: str,
    title: str,
    subtitle: str,
    *,
    status: str,
    status_text: str,
    tone: str,
    metrics: list[dict[str, Any]],
    items: list[dict[str, Any]],
    detail: dict[str, Any] | None = None,
    flow_detail: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "subtitle": subtitle,
        "status": status,
        "status_text": status_text,
        "tone": tone,
        "metrics": metrics,
        "items": items,
        "detail": detail or {},
        "flow_detail": flow_detail or {},
        "filters": {key: value for key, value in (filters or {}).items() if value not in (None, "", [], {})},
    }


async def learning_pulse(
    db: AsyncSession,
    user: User,
    *,
    days: int = 30,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    source_type: str | None = None,
    artifact_kind: str | None = None,
    target_type: str | None = None,
    sf_tool: str | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    """Return the five-stage default pulse view for the learning flow page."""
    clean_days = _bounded_days(days)
    clean_limit = max(1, min(int(limit or 6), 12))
    cutoff = now_bjt() - timedelta(days=clean_days)
    scope_department = department or (None if _is_global_user(user) else _user_department(user))

    event_filter = _event_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        sf_tool=sf_tool,
    )
    filtered_event_ids = select(LearningEvent.id).where(event_filter) if (event_type or source_type or sf_tool) else None
    artifact_filter = _artifact_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        artifact_kind=artifact_kind,
        target_type=target_type,
        event_ids=filtered_event_ids,
    )

    event_total = int((await db.execute(select(func.count(LearningEvent.id)).where(event_filter))).scalar() or 0)
    event_by_source = _pulse_count_map(
        (await db.execute(
            select(LearningEvent.source_type, func.count(LearningEvent.id))
            .where(event_filter)
            .group_by(LearningEvent.source_type)
        )).all()
    )
    event_by_type = _pulse_count_map(
        (await db.execute(
            select(LearningEvent.event_type, func.count(LearningEvent.id))
            .where(event_filter)
            .group_by(LearningEvent.event_type)
        )).all()
    )
    event_rows = (
        await db.execute(
            select(LearningEvent).where(event_filter).order_by(LearningEvent.created_at.desc()).limit(clean_limit)
        )
    ).scalars().all()

    artifact_total = int((await db.execute(select(func.count(LearningArtifact.id)).where(artifact_filter))).scalar() or 0)
    artifact_by_kind = _pulse_count_map(
        (await db.execute(
            select(LearningArtifact.artifact_kind, func.count(LearningArtifact.id))
            .where(artifact_filter)
            .group_by(LearningArtifact.artifact_kind)
        )).all()
    )
    artifact_by_status = _pulse_count_map(
        (await db.execute(
            select(LearningArtifact.status, func.count(LearningArtifact.id))
            .where(artifact_filter)
            .group_by(LearningArtifact.status)
        )).all()
    )
    artifact_rows = (
        await db.execute(
            select(LearningArtifact)
            .where(artifact_filter)
            .order_by(LearningArtifact.updated_at.desc(), LearningArtifact.created_at.desc())
            .limit(min(clean_limit * 4, 48))
        )
    ).scalars().all()
    ignored_artifact_rows = (
        await db.execute(
            select(LearningArtifact)
            .where(artifact_filter, LearningArtifact.status == "ignored")
            .order_by(LearningArtifact.updated_at.desc(), LearningArtifact.created_at.desc())
            .limit(clean_limit)
        )
    ).scalars().all()
    training_sample_count = int(
        (await db.execute(
            select(func.count(LearningArtifact.id)).where(
                artifact_filter,
                LearningArtifact.artifact_kind.in_(sorted(TRAINING_ARTIFACTS)),
                LearningArtifact.status == "materialized",
                LearningArtifact.sink_type == "training_sample",
            )
        )).scalar()
        or 0
    )
    synced_to_db_count = int(
        (await db.execute(
            select(func.count(LearningArtifact.id)).where(
                artifact_filter,
                or_(LearningArtifact.status == "materialized", LearningArtifact.sink_type.is_not(None)),
            )
        )).scalar()
        or 0
    )
    knowledge_doc_id_select = (
        select(LearningArtifact.sink_id.label("sink_id"))
        .where(
            artifact_filter,
            LearningArtifact.sink_type == "knowledge_document",
            LearningArtifact.sink_id.is_not(None),
        )
        .distinct()
    )
    knowledge_doc_ids = [
        str(row[0])
        for row in (
            await db.execute(knowledge_doc_id_select.limit(20))
        ).all()
        if row[0]
    ]
    knowledge_doc_stats = {"document_count": 0, "vector_chunks": 0, "latest_indexed_at": None, "latest_updated_at": None, "max_index_version": 0}
    if knowledge_doc_ids:
        knowledge_doc_subquery = knowledge_doc_id_select.subquery()
        doc_stats = (
            await db.execute(
                select(
                    func.count(KnowledgeDocument.id),
                    func.coalesce(func.sum(KnowledgeDocument.chunk_count), 0),
                    func.max(KnowledgeDocument.indexed_at),
                    func.max(KnowledgeDocument.updated_at),
                    func.max(KnowledgeDocument.index_version),
                ).where(KnowledgeDocument.id.in_(select(knowledge_doc_subquery.c.sink_id)))
            )
        ).one()
        knowledge_doc_stats = {
            "document_count": int(doc_stats[0] or 0),
            "vector_chunks": int(doc_stats[1] or 0),
            "latest_indexed_at": doc_stats[2],
            "latest_updated_at": doc_stats[3],
            "max_index_version": int(doc_stats[4] or 0),
        }
    database_version_at = knowledge_doc_stats["latest_indexed_at"] or knowledge_doc_stats["latest_updated_at"]
    ingestion_artifact_ids = select(LearningArtifact.id).where(artifact_filter)
    ingestion_filter = _ingestion_job_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        artifact_ids=ingestion_artifact_ids,
    )
    ingestion_total = int(
        (await db.execute(select(func.count(LearningIngestionJob.id)).where(ingestion_filter))).scalar() or 0
    )
    ingestion_rows = (
        await db.execute(
            select(LearningIngestionJob)
            .where(ingestion_filter)
            .order_by(LearningIngestionJob.updated_at.desc(), LearningIngestionJob.created_at.desc())
            .limit(clean_limit)
        )
    ).scalars().all()

    job_filter = _with_common_filters(
        _scope_filter(TrainingJob, user),
        TrainingJob,
        department=scope_department,
        skill_id=skill_id,
        run_id=None,
        created_after=cutoff,
    )
    if target_type and target_type != "training":
        job_filter = and_(job_filter, sa.false())
    if skill_id:
        job_filter = and_(job_filter, TrainingJob.target_skill_id == skill_id)
    job_rows = (
        await db.execute(
            select(TrainingJob)
            .where(job_filter)
            .order_by(TrainingJob.updated_at.desc(), TrainingJob.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    job_ids = [row.id for row in job_rows]
    task_rows: list[TrainingJobTask] = []
    if job_ids:
        task_rows = (
            await db.execute(
                select(TrainingJobTask)
                .where(TrainingJobTask.job_id.in_(job_ids))
                .order_by(TrainingJobTask.updated_at.desc(), TrainingJobTask.id.desc())
            )
        ).scalars().all()
    tasks_by_job: dict[str, list[TrainingJobTask]] = {}
    for task in task_rows:
        tasks_by_job.setdefault(task.job_id, []).append(task)
    completed_durations = [
        duration
        for task in task_rows
        if task.status == "completed"
        for duration in [_pulse_duration_seconds(task.created_at, task.updated_at)]
        if duration is not None and duration > 0
    ]
    avg_completed_seconds = sum(completed_durations) / len(completed_durations) if completed_durations else None
    latest_job = job_rows[0] if job_rows else None
    latest_task = tasks_by_job.get(latest_job.id, [None])[0] if latest_job else None
    job_status_counts = _pulse_count_map(
        (await db.execute(
            select(TrainingJob.status, func.count(TrainingJob.id)).where(job_filter).group_by(TrainingJob.status)
        )).all()
    )

    deployment_filter = _with_common_filters(
        _scope_filter(TrainingModelDeployment, user),
        TrainingModelDeployment,
        department=scope_department,
        created_after=cutoff,
    )
    if skill_id:
        deployment_filter = and_(deployment_filter, TrainingModelDeployment.target_skill_ids_json.contains([skill_id]))
    deployment_rows = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(deployment_filter)
            .order_by(TrainingModelDeployment.updated_at.desc(), TrainingModelDeployment.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    deployment_ids = [row.id for row in deployment_rows]
    deployment_job_ids = sorted({row.job_id for row in deployment_rows if row.job_id})
    deployment_job_rows: list[TrainingJob] = []
    if deployment_job_ids:
        deployment_job_rows = (
            await db.execute(
                select(TrainingJob).where(TrainingJob.id.in_(deployment_job_ids))
            )
        ).scalars().all()
        deployment_task_rows = (
            await db.execute(
                select(TrainingJobTask)
                .where(TrainingJobTask.job_id.in_(deployment_job_ids))
                .order_by(TrainingJobTask.updated_at.desc(), TrainingJobTask.id.desc())
            )
        ).scalars().all()
        for task in deployment_task_rows:
            tasks_by_job.setdefault(task.job_id, []).append(task)
    jobs_by_id = {row.id: row for row in [*deployment_job_rows, *job_rows]}
    deployment_gateway_ids_set: set[str] = set()
    for row in deployment_rows:
        gateway_id = _pulse_deployment_target_gateway_id(row, jobs_by_id.get(row.job_id))
        if gateway_id:
            deployment_gateway_ids_set.add(gateway_id)
    deployment_gateway_ids = sorted(deployment_gateway_ids_set)
    deployment_instances_by_id: dict[str, OpenClawInstance] = {}
    if deployment_gateway_ids:
        deployment_instances = (
            await db.execute(
                select(OpenClawInstance).where(OpenClawInstance.id.in_(deployment_gateway_ids))
            )
        ).scalars().all()
        deployment_instances_by_id = {row.id: row for row in deployment_instances}
    deployment_by_status = _pulse_count_map(
        (await db.execute(
            select(TrainingModelDeployment.status, func.count(TrainingModelDeployment.id))
            .where(deployment_filter)
            .group_by(TrainingModelDeployment.status)
        )).all()
    )

    eval_tasks = [
        task
        for task in task_rows
        if task.status in {"completed", "failed"} and _is_training_eval_task(task)
    ]
    passed_count = sum(1 for task in eval_tasks if _safe_dict(task.metrics_json).get("passed") is True)
    failed_eval_count = sum(1 for task in eval_tasks if task.status == "failed" or _safe_dict(task.metrics_json).get("passed") is False)
    latest_eval = eval_tasks[0] if eval_tasks else None
    latest_eval_metrics = _training_eval_metrics(latest_eval)

    edge_filter = _with_common_filters(
        _scope_filter(LearningFlowEdge, user),
        LearningFlowEdge,
        department=scope_department,
        skill_id=skill_id,
        run_id=run_id,
        created_after=cutoff,
    )
    if deployment_ids:
        application_edge_filter = and_(
            edge_filter,
            LearningFlowEdge.from_type == "model_deployment",
            LearningFlowEdge.from_id.in_(deployment_ids),
            LearningFlowEdge.relation.in_(["deployed_to", "used_as_model", "controlled_inference"]),
        )
    else:
        application_edge_filter = and_(
            edge_filter,
            LearningFlowEdge.from_type == "model_deployment",
            LearningFlowEdge.relation.in_(["deployed_to", "used_as_model", "controlled_inference"]),
        )
    application_edges = (
        await db.execute(
            select(LearningFlowEdge)
            .where(application_edge_filter)
            .order_by(LearningFlowEdge.created_at.desc())
            .limit(clean_limit)
        )
    ).scalars().all()
    application_relation_counts = _pulse_count_map(
        (await db.execute(
            select(LearningFlowEdge.relation, func.count(LearningFlowEdge.id))
            .where(application_edge_filter)
            .group_by(LearningFlowEdge.relation)
        )).all()
    )

    eta_seconds = _pulse_eta_seconds(latest_job, latest_task, avg_completed_seconds)
    latest_job_status = latest_job.status if latest_job else "not_started"
    finetuning_tone = "bad" if latest_job_status == "failed" else "warn" if latest_job_status in {"queued", "running", "evaluating"} else "good" if latest_job_status == "completed" else "info"
    test_tone = "bad" if failed_eval_count else "good" if passed_count else "info"
    app_tone = "good" if application_edges or deployment_by_status.get("active") or deployment_by_status.get("canary") else "info"
    deployment_by_job_id: dict[str, TrainingModelDeployment] = {}
    deployment_candidates = [
        *[row for row in deployment_rows if row.status in {"active", "canary"}],
        *[row for row in deployment_rows if row.status not in {"active", "canary"}],
    ]
    for row in deployment_candidates:
        deployment_by_job_id.setdefault(row.job_id, row)
    raw_flow_records = [_pulse_event_flow_row(row) for row in event_rows[:clean_limit]]
    kept_artifact_rows = [row for row in artifact_rows if row.status != "ignored"][:clean_limit]
    clarified_keep_records = [_pulse_artifact_flow_row(row) for row in kept_artifact_rows]
    clarified_drop_records = [_pulse_artifact_flow_row(row) for row in ignored_artifact_rows[:clean_limit]]
    ingestion_records = [_pulse_ingestion_flow_row(row) for row in ingestion_rows]
    training_records = [
        _pulse_training_job_row(row, tasks_by_job.get(row.id, []), training_sample_count)
        for row in job_rows[:clean_limit]
    ]
    test_records = [
        _pulse_eval_row(
            task,
            job=jobs_by_id.get(task.job_id),
            deployment=deployment_by_job_id.get(task.job_id),
            instance=deployment_instances_by_id.get(
                _pulse_deployment_target_gateway_id(
                    deployment_by_job_id.get(task.job_id),
                    jobs_by_id.get(task.job_id),
                )
            ),
            tasks=tasks_by_job.get(task.job_id, []),
        )
        for task in eval_tasks[:clean_limit]
    ]
    deployment_records_all = [
        _pulse_deployment_row(
            row,
            jobs_by_id.get(row.job_id),
            deployment_instances_by_id.get(_pulse_deployment_target_gateway_id(row, jobs_by_id.get(row.job_id))),
            tasks_by_job.get(row.job_id, []),
        )
        for row in deployment_rows
    ]
    deployment_records = sorted(
        deployment_records_all,
        key=lambda item: (
            0 if _safe_dict(_safe_dict(item.get("metadata")).get("chat_context")).get("inference_ready") is True else 1,
            0 if str(item.get("status") or "") in {"active", "canary"} else 1,
        ),
    )[:clean_limit]
    application_records = [_pulse_application_edge_row(edge) for edge in application_edges[:clean_limit]]
    conversation_examples = [
        row for row in application_records
        if _safe_dict(row.get("input_preview")).get("prompt") or _safe_dict(row.get("output_preview")).get("result")
    ][:3]
    if not conversation_examples and deployment_rows:
        conversation_examples = [
            {
                "title": "暂无可展示的模型对话样例",
                "summary": "当前部署已有模型产物与应用链路，但未保存可展示的推理输入/输出摘要。",
                "status": "pending",
                "status_text": _status_label("pending"),
                "source": f"model_deployment:{deployment_rows[0].id}",
                "destination": "等待下一次推理链路回传",
                "entity_type": "model_deployment",
                "entity_id": deployment_rows[0].id,
                "input_preview": {
                    "expected_input": "业务问题、运行上下文或待分析文本",
                    "note": "未返回原始输入明文；如后续推理链路写入摘要，将自动展示。",
                },
                "output_preview": {
                    "expected_output": "模型分析结论、建议动作或结构化结果",
                    "note": "当前没有可展示的输出摘要。",
                },
            }
        ]

    modules = [
        _pulse_module(
            "raw_inputs",
            "原始数据",
            "原始数据进来哪些、来源哪里",
            status="ready" if event_total else "empty",
            status_text=f"{event_total} 条原始记录",
            tone="source" if event_total else "info",
            metrics=[
                {"key": "events", "label": "原始记录", "value": event_total},
                {"key": "sources", "label": "来源类型", "value": len(event_by_source)},
                {"key": "event_types", "label": "事件类型", "value": len(event_by_type)},
            ],
            items=[
                _pulse_item(
                    id=row.id,
                    title=row.redacted_summary or row.event_type,
                    meta=f"{_source_type_label(row.source_type)} · {row.source_id}",
                    status=row.status,
                    entity_type="learning_event",
                    entity_id=row.id,
                    payload=_event_to_dict(row),
                )
                for row in event_rows
            ],
            detail={"by_source": event_by_source, "by_event_type": event_by_type},
            flow_detail={
                "title": "原始数据进入明细",
                "summary": "展示本周期进入学习流的脱敏原始记录、来源、接收依据与后续去向。",
                "sections": [
                    {
                        "key": "new_income",
                        "title": "新增进入记录",
                        "description": "已通过权限范围、来源去重和脱敏摘要处理后进入数据清洗。",
                        "items": raw_flow_records,
                    },
                    {
                        "key": "source_distribution",
                        "title": "来源分布",
                        "description": "用于判断数据主要从哪些系统进入。",
                        "items": [
                            {"title": _source_type_label(key), "value": value, "summary": f"{value} 条记录"}
                            for key, value in sorted(event_by_source.items(), key=lambda item: item[1], reverse=True)[:8]
                        ],
                    },
                ],
            },
            filters={"source_type": source_type, "event_type": event_type},
        ),
        _pulse_module(
            "clarified_data",
            "数据清洗",
            "清洗后同步入数据库和向量库，形成可复用资产",
            status="ready" if artifact_total else "empty",
            status_text=f"{artifact_total} 条清洗资产",
            tone="artifact" if artifact_total else "info",
            metrics=[
                {"key": "artifacts", "label": "清洗资产", "value": artifact_total},
                {"key": "database_synced", "label": "数据库存储", "value": synced_to_db_count},
                {"key": "vector_chunks", "label": "向量分片", "value": knowledge_doc_stats["vector_chunks"]},
                {"key": "database_version", "label": "数据库版本", "value": _pulse_version_text(database_version_at)},
                {"key": "training_samples", "label": "训练样本", "value": training_sample_count},
            ],
            items=_pulse_cleaned_artifact_items(artifact_rows, clean_limit),
            detail={
                "by_kind": artifact_by_kind,
                "by_status": artifact_by_status,
                "storage": {
                    "database_synced_count": synced_to_db_count,
                    "knowledge_document_count": knowledge_doc_stats["document_count"],
                    "vector_chunk_count": knowledge_doc_stats["vector_chunks"],
                    "database_version": _pulse_version_text(database_version_at),
                    "database_version_at": _iso(database_version_at),
                    "latest_indexed_at": _iso(knowledge_doc_stats["latest_indexed_at"]),
                    "max_index_version": knowledge_doc_stats["max_index_version"],
                    "knowledge_document_ids": knowledge_doc_ids[:20],
                },
            },
            flow_detail={
                "title": "数据清洗处理明细",
                "summary": "展示清洗后的保留资产、丢弃资产、同步存储位置及处理依据。",
                "sections": [
                    {
                        "key": "kept",
                        "title": "保留并进入下游的数据",
                        "description": "满足质量、置信度或物化条件，已进入知识库、向量库、训练样本库或 Agent 记忆。",
                        "items": clarified_keep_records,
                    },
                    {
                        "key": "dropped",
                        "title": "丢弃或不进入下游的数据",
                        "description": "因策略、重复、质量或人工忽略而未进入后续链路。",
                        "items": clarified_drop_records,
                        "empty_text": "当前筛选范围内没有丢弃记录。",
                    },
                    {
                        "key": "storage",
                        "title": "同步存储",
                        "description": "展示写入数据库、知识文档、向量索引或训练样本库的任务。",
                        "items": ingestion_records,
                        "empty_text": "当前没有单独的同步任务记录；可依据资产 sink_type / sink_id 查看存储位置。",
                    },
                ],
                "storage": {
                    "database_version": _pulse_version_text(database_version_at),
                    "database_version_at": _iso(database_version_at),
                    "database_synced_count": synced_to_db_count,
                    "knowledge_document_count": knowledge_doc_stats["document_count"],
                    "vector_chunk_count": knowledge_doc_stats["vector_chunks"],
                    "max_index_version": knowledge_doc_stats["max_index_version"],
                },
            },
            filters={"artifact_kind": artifact_kind},
        ),
        _pulse_module(
            "finetuning",
            "微调模型",
            "微调模型进行到哪、预计还要多久",
            status=latest_job_status,
            status_text=_status_label(latest_job_status),
            tone=finetuning_tone,
            metrics=[
                {"key": "jobs", "label": "训练任务", "value": sum(job_status_counts.values())},
                {"key": "samples", "label": "样本数", "value": training_sample_count},
                {"key": "progress", "label": "进度", "value": round(float(getattr(latest_task, "progress", 0) or 0) * 100, 1), "suffix": "%"},
                {"key": "eta", "label": "预计时间", "value": _pulse_eta_text(eta_seconds, latest_job_status)},
            ],
            items=[
                _pulse_item(
                    id=row.id,
                    title=row.title,
                    meta=f"{row.job_type} · {_status_label(row.status)} · {row.dataset_ref or '-'}",
                    status=row.status,
                    entity_type="training_job",
                    entity_id=row.id,
                    payload={
                        "id": row.id,
                        "title": row.title,
                        "status": row.status,
                        "job_type": row.job_type,
                        "target_skill_id": row.target_skill_id,
                        "target_gateway_id": row.target_gateway_id,
                        "dataset_ref": row.dataset_ref,
                        "tasks": [_training_metric_preview(_safe_dict(task.metrics_json)) for task in tasks_by_job.get(row.id, [])[:3]],
                        "created_at": _iso(row.created_at),
                        "updated_at": _iso(row.updated_at),
                    },
                )
                for row in job_rows[:clean_limit]
            ],
            detail={"by_status": job_status_counts, "eta_seconds": eta_seconds, "eta_text": _pulse_eta_text(eta_seconds, latest_job_status)},
            flow_detail={
                "title": "微调训练流转明细",
                "summary": "展示训练任务是否进入训练 Agent、使用的数据集、样本来源、进度和预计完成时间。",
                "sections": [
                    {
                        "key": "training_jobs",
                        "title": "进入训练的任务",
                        "description": "每条任务都关联训练样本集、目标 Skill 和训练 Agent。",
                        "items": training_records,
                        "empty_text": "当前没有进入训练的任务。",
                    },
                    {
                        "key": "sample_lineage",
                        "title": "训练样本来源",
                        "description": "展示样本由哪些清洗资产形成；仅展示哈希和血缘，不返回原始样本明文。",
                        "items": _pulse_sample_lineage_preview(artifact_rows, clean_limit),
                        "empty_text": "当前没有可展示的训练样本血缘。",
                    },
                ],
                "eta": {"seconds": eta_seconds, "text": _pulse_eta_text(eta_seconds, latest_job_status)},
            },
            filters={"target_type": "training"},
        ),
        _pulse_module(
            "test_results",
            "模型测试",
            "评估是否通过、产物是否可用",
            status="passed" if passed_count else "failed" if failed_eval_count else "pending",
            status_text="已通过" if passed_count else "未通过" if failed_eval_count else "待评估",
            tone=test_tone,
            metrics=[
                {"key": "passed", "label": "通过", "value": passed_count},
                {"key": "failed", "label": "未通过", "value": failed_eval_count},
                {"key": "win_rate", "label": "胜率", "value": latest_eval_metrics.get("win_rate") or latest_eval_metrics.get("eval_win_rate") or "-"},
                {"key": "artifact", "label": "产物", "value": latest_eval_metrics.get("artifact_sha256") or latest_eval_metrics.get("adapter_sha256") or "-"},
            ],
            items=[
                _pulse_item(
                    id=f"task-{task.id}",
                    title=f"评估任务 {task.id}",
                    meta=f"{_status_label(task.status)} · progress={round(float(task.progress or 0) * 100, 1)}%",
                    status=task.status,
                    entity_type="training_job",
                    entity_id=task.job_id,
                    payload={
                        "task_id": task.id,
                        "job_id": task.job_id,
                        "status": task.status,
                        "progress": task.progress,
                        "metrics": _training_metric_preview(task.metrics_json),
                        "model_test_context": _training_job_model_test_context(
                            jobs_by_id.get(task.job_id),
                            task,
                            deployment_by_job_id.get(task.job_id),
                        ),
                        "error": task.error_message,
                        "updated_at": _iso(task.updated_at),
                    },
                )
                for task in eval_tasks[:clean_limit]
            ],
            detail={"latest_metrics": _training_metric_preview(latest_eval_metrics), "passed_count": passed_count, "failed_count": failed_eval_count},
            flow_detail={
                "title": "模型测试明细",
                "summary": "展示测试任务、执行 Agent、输入产物、评估指标和输出结果。",
                "sections": [
                    {
                        "key": "eval_tasks",
                        "title": "测试任务与 Agent",
                        "description": "测试任务来自训练 Agent 回传的评估记录。",
                        "items": test_records,
                        "empty_text": "当前没有测试任务回传。",
                    },
                    {
                        "key": "test_output",
                        "title": "测试输出",
                        "description": "展示最新评估指标和产物引用。",
                        "items": [
                            {
                                "title": "最新评估结果",
                                "status": "passed" if passed_count else "failed" if failed_eval_count else "pending",
                                "status_text": "已通过" if passed_count else "未通过" if failed_eval_count else "待评估",
                                "summary": "训练产物评估结果。",
                                "input_preview": {"job_id": latest_eval.job_id if latest_eval else None},
                                "output_preview": _training_metric_preview(latest_eval_metrics),
                            }
                        ] if latest_eval else [],
                        "empty_text": "当前没有可展示的测试输出。",
                    },
                ],
            },
            filters={"target_type": "training"},
        ),
        _pulse_module(
            "application_outputs",
            "部署输出",
            "应用在哪里输出、结果怎么样",
            status="applied" if app_tone == "good" else "pending",
            status_text="已应用" if app_tone == "good" else "未使用",
            tone=app_tone,
            metrics=[
                {"key": "deployments", "label": "部署", "value": sum(deployment_by_status.values())},
                {"key": "active", "label": "已激活", "value": deployment_by_status.get("active", 0) + deployment_by_status.get("canary", 0)},
                {"key": "used_as_model", "label": "参与决策", "value": application_relation_counts.get("used_as_model", 0)},
                {"key": "outputs", "label": "输出链路", "value": sum(application_relation_counts.values())},
            ],
            items=[
                _pulse_item(
                    id=f"edge-{edge.id}",
                    title=_pulse_relation_label(edge.relation),
                    meta=f"{edge.from_type}:{edge.from_id} → {edge.to_type}:{edge.to_id}",
                    status=edge.status,
                    entity_type=edge.to_type,
                    entity_id=edge.to_id,
                    payload=_edge_to_dict(edge),
                )
                for edge in application_edges
            ]
            or [
                _pulse_item(
                    id=row.id,
                    title=row.model_family,
                    meta=f"{_status_label(row.status)} · rollout {row.rollout_percent}%",
                    status=row.status,
                    entity_type="model_deployment",
                    entity_id=row.id,
                    payload={
                        "id": row.id,
                        "job_id": row.job_id,
                        "status": row.status,
                        "model_family": row.model_family,
                        "target_skill_ids": row.target_skill_ids_json or [],
                        "rollout_percent": row.rollout_percent,
                        "artifact_ref": row.artifact_ref_json or {},
                        "updated_at": _iso(row.updated_at),
                    },
                )
                for row in deployment_rows[:clean_limit]
            ],
            detail={"deployment_by_status": deployment_by_status, "relation_counts": application_relation_counts},
            flow_detail={
                "title": "部署与应用输出明细",
                "summary": "展示模型部署位置、目标 Skill、实际参与的运行/决策链路，以及可展示的推理输入输出。",
                "sections": [
                    {
                        "key": "deployments",
                        "title": "部署位置",
                        "description": "展示模型产物部署到哪里、目标 Skill 是什么、当前激活状态如何。",
                        "items": deployment_records,
                        "empty_text": "当前没有模型部署记录。",
                    },
                    {
                        "key": "application_edges",
                        "title": "实际应用链路",
                        "description": "展示模型部署参与了哪些运行、决策或推理链路。",
                        "items": application_records,
                        "empty_text": "当前没有实际应用链路。",
                    },
                    {
                        "key": "conversation_examples",
                        "title": "模型对话输入输出",
                        "description": "如运行链路已保存推理摘要，这里展示输入与输出；否则明确标记暂无样例。",
                        "items": conversation_examples,
                    },
                ],
            },
            filters={"target_type": "model_deployment"},
        ),
    ]
    section_totals = {
        ("raw_inputs", "new_income"): event_total,
        ("raw_inputs", "source_distribution"): len(event_by_source),
        ("clarified_data", "kept"): max(artifact_total - artifact_by_status.get("ignored", 0), 0),
        ("clarified_data", "dropped"): artifact_by_status.get("ignored", 0),
        ("clarified_data", "storage"): ingestion_total,
        ("finetuning", "training_jobs"): sum(job_status_counts.values()),
        ("finetuning", "sample_lineage"): training_sample_count,
        ("test_results", "eval_tasks"): len(eval_tasks),
        ("test_results", "test_output"): 1 if latest_eval else 0,
        ("application_outputs", "deployments"): sum(deployment_by_status.values()),
        ("application_outputs", "application_edges"): sum(application_relation_counts.values()),
        ("application_outputs", "conversation_examples"): len(conversation_examples),
    }
    module_totals = {
        "raw_inputs": event_total,
        "clarified_data": artifact_total,
        "finetuning": sum(job_status_counts.values()),
        "test_results": len(eval_tasks),
        "application_outputs": sum(application_relation_counts.values()) or sum(deployment_by_status.values()),
    }
    for module in modules:
        module_key = str(module.get("key") or "")
        _pulse_attach_counts(module, module_totals.get(module_key))
        for section in _safe_dict(module.get("flow_detail")).get("sections") or []:
            if isinstance(section, dict):
                _pulse_attach_counts(section, section_totals.get((module_key, str(section.get("key") or ""))))

    return {
        "scope": "global" if _is_global_user(user) else "department",
        "department": None if _is_global_user(user) else _user_department(user),
        "days": clean_days,
        "generated_at": _iso(now_bjt()),
        "filters": {
            "department": _clean_filter(department),
            "org_unit_id": _clean_filter(org_unit_id),
            "skill_id": _clean_filter(skill_id),
            "run_id": _clean_filter(run_id),
            "event_type": _clean_filter(event_type),
            "source_type": _clean_filter(source_type),
            "artifact_kind": _clean_filter(artifact_kind),
            "target_type": _clean_filter(target_type),
            "sf_tool": _clean_filter(sf_tool),
        },
        "modules": modules,
        "summary": {
            "raw_events": event_total,
            "artifacts": artifact_total,
            "training_jobs": sum(job_status_counts.values()),
            "tests_passed": passed_count,
            "application_outputs": sum(application_relation_counts.values()),
        },
        "governance": {
            "raw_payload_returned": False,
            "eta_is_estimate": True,
        },
    }


def _learning_pulse_filters_payload(
    *,
    department: str | None,
    org_unit_id: str | None,
    skill_id: str | None,
    run_id: str | None,
    event_type: str | None,
    source_type: str | None,
    artifact_kind: str | None,
    target_type: str | None,
    sf_tool: str | None,
) -> dict[str, Any]:
    return {
        "department": _clean_filter(department),
        "org_unit_id": _clean_filter(org_unit_id),
        "skill_id": _clean_filter(skill_id),
        "run_id": _clean_filter(run_id),
        "event_type": _clean_filter(event_type),
        "source_type": _clean_filter(source_type),
        "artifact_kind": _clean_filter(artifact_kind),
        "target_type": _clean_filter(target_type),
        "sf_tool": _clean_filter(sf_tool),
    }


async def _pulse_deployment_rows(
    db: AsyncSession,
    rows: list[TrainingModelDeployment],
    fallback_jobs: list[TrainingJob] | None = None,
) -> list[dict[str, Any]]:
    deployment_job_ids = sorted({row.job_id for row in rows if row.job_id})
    deployment_job_rows: list[TrainingJob] = []
    if deployment_job_ids:
        deployment_job_rows = (
            await db.execute(select(TrainingJob).where(TrainingJob.id.in_(deployment_job_ids)))
        ).scalars().all()
    jobs_by_id = {row.id: row for row in [*(fallback_jobs or []), *deployment_job_rows]}
    gateway_ids_set: set[str] = set()
    for row in rows:
        gateway_id = _pulse_deployment_target_gateway_id(row, jobs_by_id.get(row.job_id))
        if gateway_id:
            gateway_ids_set.add(gateway_id)
    gateway_ids = sorted(gateway_ids_set)
    instances_by_id: dict[str, OpenClawInstance] = {}
    if gateway_ids:
        instances = (
            await db.execute(select(OpenClawInstance).where(OpenClawInstance.id.in_(gateway_ids)))
        ).scalars().all()
        instances_by_id = {row.id: row for row in instances}
    tasks_by_job: dict[str, list[TrainingJobTask]] = {}
    if deployment_job_ids:
        task_rows = (
            await db.execute(
                select(TrainingJobTask)
                .where(TrainingJobTask.job_id.in_(deployment_job_ids))
                .order_by(TrainingJobTask.updated_at.desc(), TrainingJobTask.id.desc())
            )
        ).scalars().all()
        for task in task_rows:
            tasks_by_job.setdefault(task.job_id, []).append(task)
    result = [
        _pulse_deployment_row(
            row,
            jobs_by_id.get(row.job_id),
            instances_by_id.get(_pulse_deployment_target_gateway_id(row, jobs_by_id.get(row.job_id))),
            tasks_by_job.get(row.job_id, []),
        )
        for row in rows
    ]
    return sorted(
        result,
        key=lambda item: (
            0 if _safe_dict(_safe_dict(item.get("metadata")).get("chat_context")).get("inference_ready") is True else 1,
            0 if str(item.get("status") or "") in {"active", "canary"} else 1,
        ),
    )


async def learning_pulse_drilldown(
    db: AsyncSession,
    user: User,
    *,
    module_key: str,
    section_key: str,
    days: int = 30,
    page: int = 1,
    page_size: int = 50,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    source_type: str | None = None,
    artifact_kind: str | None = None,
    target_type: str | None = None,
    sf_tool: str | None = None,
) -> dict[str, Any]:
    clean_days = _bounded_days(days)
    clean_page = max(1, int(page or 1))
    clean_page_size = max(1, min(int(page_size or 50), 100))
    offset = (clean_page - 1) * clean_page_size
    cutoff = now_bjt() - timedelta(days=clean_days)
    scope_department = department or (None if _is_global_user(user) else _user_department(user))
    module_key = str(module_key or "").strip()
    section_key = str(section_key or "").strip()

    event_filter = _event_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        sf_tool=sf_tool,
    )
    filtered_event_ids = select(LearningEvent.id).where(event_filter) if (event_type or source_type or sf_tool) else None
    artifact_filter = _artifact_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        artifact_kind=artifact_kind,
        target_type=target_type,
        event_ids=filtered_event_ids,
    )
    job_filter = _with_common_filters(
        _scope_filter(TrainingJob, user),
        TrainingJob,
        department=scope_department,
        skill_id=skill_id,
        run_id=None,
        created_after=cutoff,
    )
    if target_type and target_type != "training":
        job_filter = and_(job_filter, sa.false())
    if skill_id:
        job_filter = and_(job_filter, TrainingJob.target_skill_id == skill_id)
    deployment_filter = _with_common_filters(
        _scope_filter(TrainingModelDeployment, user),
        TrainingModelDeployment,
        department=scope_department,
        created_after=cutoff,
    )
    if skill_id:
        deployment_filter = and_(deployment_filter, TrainingModelDeployment.target_skill_ids_json.contains([skill_id]))
    edge_filter = _with_common_filters(
        _scope_filter(LearningFlowEdge, user),
        LearningFlowEdge,
        department=scope_department,
        skill_id=skill_id,
        run_id=run_id,
        created_after=cutoff,
    )
    app_edge_filter = and_(
        edge_filter,
        LearningFlowEdge.from_type == "model_deployment",
        LearningFlowEdge.relation.in_(["deployed_to", "used_as_model", "controlled_inference"]),
    )

    total = 0
    items: list[dict[str, Any]] = []
    title = section_key
    description = ""

    if module_key == "raw_inputs" and section_key == "new_income":
        total = int((await db.execute(select(func.count(LearningEvent.id)).where(event_filter))).scalar() or 0)
        rows = (
            await db.execute(
                select(LearningEvent)
                .where(event_filter)
                .order_by(LearningEvent.created_at.desc())
                .offset(offset)
                .limit(clean_page_size)
            )
        ).scalars().all()
        items = [_pulse_event_flow_row(row) for row in rows]
        title = "新增进入记录"
        description = "按当前筛选条件分页查看进入学习流的脱敏原始记录。"
    elif module_key == "raw_inputs" and section_key == "source_distribution":
        counts = _pulse_count_map(
            (await db.execute(
                select(LearningEvent.source_type, func.count(LearningEvent.id))
                .where(event_filter)
                .group_by(LearningEvent.source_type)
            )).all()
        )
        rows = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        total = len(rows)
        items = [
            {"title": _source_type_label(key), "value": value, "summary": f"{value} 条记录"}
            for key, value in rows[offset: offset + clean_page_size]
        ]
        title = "来源分布"
        description = "按来源类型聚合当前学习流原始输入。"
    elif module_key == "clarified_data" and section_key in {"kept", "dropped"}:
        condition = and_(artifact_filter, LearningArtifact.status == "ignored") if section_key == "dropped" else and_(artifact_filter, LearningArtifact.status != "ignored")
        total = int((await db.execute(select(func.count(LearningArtifact.id)).where(condition))).scalar() or 0)
        rows = (
            await db.execute(
                select(LearningArtifact)
                .where(condition)
                .order_by(LearningArtifact.updated_at.desc(), LearningArtifact.created_at.desc())
                .offset(offset)
                .limit(clean_page_size)
            )
        ).scalars().all()
        items = [_pulse_artifact_flow_row(row) for row in rows]
        title = "丢弃或不进入下游的数据" if section_key == "dropped" else "保留并进入下游的数据"
        description = "按当前筛选条件分页查看清洗后的资产去向和处理依据。"
    elif module_key == "clarified_data" and section_key == "storage":
        ingestion_filter = _ingestion_job_filter(
            user,
            created_after=cutoff,
            department=department,
            org_unit_id=org_unit_id,
            skill_id=skill_id,
            artifact_ids=select(LearningArtifact.id).where(artifact_filter),
        )
        total = int((await db.execute(select(func.count(LearningIngestionJob.id)).where(ingestion_filter))).scalar() or 0)
        rows = (
            await db.execute(
                select(LearningIngestionJob)
                .where(ingestion_filter)
                .order_by(LearningIngestionJob.updated_at.desc(), LearningIngestionJob.created_at.desc())
                .offset(offset)
                .limit(clean_page_size)
            )
        ).scalars().all()
        items = [_pulse_ingestion_flow_row(row) for row in rows]
        title = "同步存储"
        description = "分页查看写入数据库、知识文档、向量索引或训练样本库的任务。"
    elif module_key == "finetuning" and section_key == "training_jobs":
        total = int((await db.execute(select(func.count(TrainingJob.id)).where(job_filter))).scalar() or 0)
        rows = (
            await db.execute(
                select(TrainingJob)
                .where(job_filter)
                .order_by(TrainingJob.updated_at.desc(), TrainingJob.created_at.desc())
                .offset(offset)
                .limit(clean_page_size)
            )
        ).scalars().all()
        job_ids = [row.id for row in rows]
        task_rows = []
        if job_ids:
            task_rows = (
                await db.execute(
                    select(TrainingJobTask)
                    .where(TrainingJobTask.job_id.in_(job_ids))
                    .order_by(TrainingJobTask.updated_at.desc(), TrainingJobTask.id.desc())
                )
            ).scalars().all()
        tasks_by_job: dict[str, list[TrainingJobTask]] = {}
        for task in task_rows:
            tasks_by_job.setdefault(task.job_id, []).append(task)
        sample_total = int(
            (await db.execute(
                select(func.count(LearningArtifact.id)).where(
                    artifact_filter,
                    LearningArtifact.artifact_kind.in_(sorted(TRAINING_ARTIFACTS)),
                    LearningArtifact.status == "materialized",
                    LearningArtifact.sink_type == "training_sample",
                )
            )).scalar()
            or 0
        )
        items = [_pulse_training_job_row(row, tasks_by_job.get(row.id, []), sample_total) for row in rows]
        title = "进入训练的任务"
        description = "分页查看训练任务、样本包、目标 Skill、训练节点和回传进度。"
    elif module_key == "finetuning" and section_key == "sample_lineage":
        condition = and_(
            artifact_filter,
            LearningArtifact.artifact_kind.in_(sorted(TRAINING_ARTIFACTS)),
            LearningArtifact.status == "materialized",
            LearningArtifact.sink_type == "training_sample",
        )
        total = int((await db.execute(select(func.count(LearningArtifact.id)).where(condition))).scalar() or 0)
        rows = (
            await db.execute(
                select(LearningArtifact)
                .where(condition)
                .order_by(LearningArtifact.updated_at.desc(), LearningArtifact.created_at.desc())
                .offset(offset)
                .limit(clean_page_size)
            )
        ).scalars().all()
        items = [
            _pulse_flow_row(
                title=row.title or _artifact_kind_label(row.artifact_kind),
                summary=row.summary or "训练样本已物化并进入样本库。",
                status=row.status,
                reason="展示样本血缘、哈希和存储引用，不返回原始业务明文。",
                source=f"learning_event:{row.event_id}",
                destination=f"training_sample:{row.sink_id or row.id}",
                entity_type="learning_artifact",
                entity_id=row.id,
                input_preview=_pulse_sample_lineage_preview([row], 1)[0] if _pulse_sample_lineage_preview([row], 1) else {},
                output_preview={"sink_type": row.sink_type, "sink_id": row.sink_id, "updated_at": _iso(row.updated_at)},
            )
            for row in rows
        ]
        title = "训练样本来源"
        description = "分页查看训练样本由哪些清洗资产形成。"
    elif module_key == "test_results" and section_key in {"eval_tasks", "test_output"}:
        candidate_tasks = (
            await db.execute(
                select(TrainingJobTask)
                .join(TrainingJob, TrainingJob.id == TrainingJobTask.job_id)
                .where(job_filter, TrainingJobTask.status.in_(["completed", "failed"]))
                .order_by(TrainingJobTask.updated_at.desc(), TrainingJobTask.id.desc())
                .limit(2000)
            )
        ).scalars().all()
        eval_tasks = [task for task in candidate_tasks if _is_training_eval_task(task)]
        total = len(eval_tasks)
        page_tasks = eval_tasks[offset: offset + clean_page_size]
        page_job_ids = sorted({task.job_id for task in page_tasks if task.job_id})
        page_jobs: list[TrainingJob] = []
        page_deployments: list[TrainingModelDeployment] = []
        page_task_rows: list[TrainingJobTask] = []
        if page_job_ids:
            page_jobs = (
                await db.execute(select(TrainingJob).where(TrainingJob.id.in_(page_job_ids)))
            ).scalars().all()
            page_deployments = (
                await db.execute(
                    select(TrainingModelDeployment)
                    .where(_scope_filter(TrainingModelDeployment, user))
                    .where(TrainingModelDeployment.job_id.in_(page_job_ids))
                    .order_by(TrainingModelDeployment.updated_at.desc(), TrainingModelDeployment.created_at.desc())
                )
            ).scalars().all()
            page_task_rows = (
                await db.execute(
                    select(TrainingJobTask)
                    .where(TrainingJobTask.job_id.in_(page_job_ids))
                    .order_by(TrainingJobTask.updated_at.desc(), TrainingJobTask.id.desc())
                )
            ).scalars().all()
        page_jobs_by_id = {row.id: row for row in page_jobs}
        page_deployment_by_job_id: dict[str, TrainingModelDeployment] = {}
        page_deployment_candidates = [
            *[row for row in page_deployments if row.status in {"active", "canary"}],
            *[row for row in page_deployments if row.status not in {"active", "canary"}],
        ]
        for row in page_deployment_candidates:
            page_deployment_by_job_id.setdefault(row.job_id, row)
        page_tasks_by_job: dict[str, list[TrainingJobTask]] = {}
        for task in page_task_rows:
            page_tasks_by_job.setdefault(task.job_id, []).append(task)
        page_gateway_ids = sorted({
            str(getattr(page_jobs_by_id.get(job_id), "target_gateway_id", "") or "").strip()
            for job_id in page_job_ids
            if str(getattr(page_jobs_by_id.get(job_id), "target_gateway_id", "") or "").strip()
        })
        page_instances_by_id: dict[str, OpenClawInstance] = {}
        if page_gateway_ids:
            page_instances = (
                await db.execute(select(OpenClawInstance).where(OpenClawInstance.id.in_(page_gateway_ids)))
            ).scalars().all()
            page_instances_by_id = {row.id: row for row in page_instances}
        if section_key == "test_output":
            items = [
                {
                    "title": f"评估任务 {task.id} 输出",
                    "status": "passed" if _training_eval_metrics(task).get("passed") is True else "failed" if _training_eval_metrics(task).get("passed") is False or task.status == "failed" else task.status,
                    "status_text": "已通过" if _training_eval_metrics(task).get("passed") is True else "未通过" if _training_eval_metrics(task).get("passed") is False or task.status == "failed" else _status_label(task.status),
                    "summary": "训练产物评估结果。",
                    "input_preview": {
                        "job_id": task.job_id,
                        "task_id": task.id,
                        "training_data": _training_job_model_test_context(
                            page_jobs_by_id.get(task.job_id),
                            task,
                            page_deployment_by_job_id.get(task.job_id),
                        ).get("training_data"),
                    },
                    "output_preview": {
                        **_safe_dict(_training_metric_preview(_training_eval_metrics(task))),
                        "model_test_context": _training_job_model_test_context(
                            page_jobs_by_id.get(task.job_id),
                            task,
                            page_deployment_by_job_id.get(task.job_id),
                        ),
                        "chat_context": (
                            _pulse_deployment_chat_context(
                                page_deployment_by_job_id[task.job_id],
                                page_jobs_by_id.get(task.job_id),
                                page_instances_by_id.get(str(getattr(page_jobs_by_id.get(task.job_id), "target_gateway_id", "") or "").strip()),
                                page_tasks_by_job.get(task.job_id, []),
                            )
                            if task.job_id in page_deployment_by_job_id
                            else None
                        ),
                    },
                    "metadata": {
                        "task_id": task.id,
                        "model_test_context": _training_job_model_test_context(
                            page_jobs_by_id.get(task.job_id),
                            task,
                            page_deployment_by_job_id.get(task.job_id),
                        ),
                    },
                }
                for task in page_tasks
            ]
            title = "测试输出"
            description = "分页查看评估指标和产物引用。"
        else:
            items = [
                _pulse_eval_row(
                    task,
                    job=page_jobs_by_id.get(task.job_id),
                    deployment=page_deployment_by_job_id.get(task.job_id),
                    instance=page_instances_by_id.get(str(getattr(page_jobs_by_id.get(task.job_id), "target_gateway_id", "") or "").strip()),
                    tasks=page_tasks_by_job.get(task.job_id, []),
                )
                for task in page_tasks
            ]
            title = "测试任务与 Agent"
            description = "分页查看训练 Agent 回传的真实评估任务。"
    elif module_key == "application_outputs" and section_key == "deployments":
        total = int((await db.execute(select(func.count(TrainingModelDeployment.id)).where(deployment_filter))).scalar() or 0)
        rows = (
            await db.execute(
                select(TrainingModelDeployment)
                .where(deployment_filter)
                .order_by(TrainingModelDeployment.updated_at.desc(), TrainingModelDeployment.created_at.desc())
                .offset(offset)
                .limit(clean_page_size)
            )
        ).scalars().all()
        items = await _pulse_deployment_rows(db, rows)
        title = "部署位置"
        description = "分页查看模型产物部署位置、目标 Skill 和激活状态。"
    elif module_key == "application_outputs" and section_key in {"application_edges", "conversation_examples"}:
        total = int((await db.execute(select(func.count(LearningFlowEdge.id)).where(app_edge_filter))).scalar() or 0)
        rows = (
            await db.execute(
                select(LearningFlowEdge)
                .where(app_edge_filter)
                .order_by(LearningFlowEdge.created_at.desc())
                .offset(offset)
                .limit(clean_page_size)
            )
        ).scalars().all()
        records = [_pulse_application_edge_row(row) for row in rows]
        if section_key == "conversation_examples":
            records = [
                row for row in records
                if _safe_dict(row.get("input_preview")).get("prompt") or _safe_dict(row.get("output_preview")).get("result")
            ]
        items = records
        title = "模型对话输入输出" if section_key == "conversation_examples" else "实际应用链路"
        description = "分页查看模型部署参与的运行、决策或推理链路。"
    else:
        raise AppError("PARAM_INVALID", 422, {"detail": "unsupported pulse drilldown section"})

    return {
        "module_key": module_key,
        "section_key": section_key,
        "title": title,
        "description": description,
        "page": clean_page,
        "page_size": clean_page_size,
        "total": total,
        "returned_count": len(items),
        "hidden_count": max(total - offset - len(items), 0),
        "has_more": offset + len(items) < total,
        "items": items,
        "days": clean_days,
        "generated_at": _iso(now_bjt()),
        "filters": _learning_pulse_filters_payload(
            department=department,
            org_unit_id=org_unit_id,
            skill_id=skill_id,
            run_id=run_id,
            event_type=event_type,
            source_type=source_type,
            artifact_kind=artifact_kind,
            target_type=target_type,
            sf_tool=sf_tool,
        ),
    }


def _ingestion_job_to_dict(row: LearningIngestionJob) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "event_id": row.event_id,
        "artifact_id": row.artifact_id,
        "sink_type": row.sink_type,
        "sink_id": row.sink_id,
        "department": row.department,
        "org_unit_id": row.org_unit_id,
        "skill_id": row.skill_id,
        "action": row.action,
        "status": row.status,
        "attempts": row.attempts,
        "error": _clip(row.error, 1000) if row.error else None,
        "policy": row.policy_json or {},
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


def _source_node_type(event: LearningEvent) -> str:
    mapping = {
        "execution_run": "run",
        "execution_artifact": "execution_artifact",
        "intelligence_analyze_run": "intelligence_analyze",
        "decision_log": "decision_log",
        "improvement_candidate": "improvement_candidate",
        "agent_draft_validation": "agent_draft_validation",
        "decision_request": "todo",
        "ai_todo": "todo",
        "todo_dispatch_task": "todo_dispatch_task",
        "sf_mcp_call": "sf_call",
        "knowledge_query": "knowledge_query",
        "training_job": "training_job",
        "model_deployment": "model_deployment",
        "agent_thread": "agent_thread",
    }
    return mapping.get(event.source_type, event.source_type)


def _source_type_to_edge_type(value: str | None) -> str | None:
    source_type = _clean_filter(value)
    if not source_type:
        return None
    mapping = {
        "execution_run": "run",
        "execution_artifact": "execution_artifact",
        "intelligence_analyze_run": "intelligence_analyze",
        "decision_log": "decision_log",
        "improvement_candidate": "improvement_candidate",
        "agent_draft_validation": "agent_draft_validation",
        "decision_request": "todo",
        "ai_todo": "todo",
        "todo_dispatch_task": "todo_dispatch_task",
        "sf_mcp_call": "sf_call",
        "sf_usage_aggregate": "sf_usage_aggregate",
        "knowledge_query": "knowledge_query",
        "training_job": "training_job",
        "model_deployment": "model_deployment",
        "agent_thread": "agent_thread",
    }
    return mapping.get(source_type, source_type)


async def _upsert_edge(
    db: AsyncSession,
    *,
    from_type: str,
    from_id: str,
    to_type: str,
    to_id: str,
    relation: str,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
    weight: float = 1.0,
    metadata: dict[str, Any] | None = None,
) -> LearningFlowEdge:
    existing = (
        await db.execute(
            select(LearningFlowEdge).where(
                LearningFlowEdge.from_type == from_type,
                LearningFlowEdge.from_id == from_id,
                LearningFlowEdge.to_type == to_type,
                LearningFlowEdge.to_id == to_id,
                LearningFlowEdge.relation == relation,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.weight = max(float(existing.weight or 0), weight)
        existing.status = "active"
        existing.metadata_json = {**(existing.metadata_json or {}), **(metadata or {})}
        return existing
    row = LearningFlowEdge(
        from_type=from_type,
        from_id=from_id,
        to_type=to_type,
        to_id=to_id,
        relation=relation,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        weight=weight,
        metadata_json=metadata or {},
    )
    db.add(row)
    await db.flush()
    return row


def _policy_for_artifact(kind: str, *, sensitivity_level: str, event_type: str | None = None) -> dict[str, Any]:
    if kind in KNOWLEDGE_ARTIFACTS:
        if sensitivity_level in {"restricted", "secret"}:
            return {"action": "review_first", "sink_type": "knowledge", "reason": "sensitive_knowledge"}
        return {"action": "auto_index", "sink_type": "knowledge", "reason": "confirmed_summary"}
    if kind in TRAINING_ARTIFACTS:
        return {"action": "training_candidate_only", "sink_type": "training", "reason": "feedback_or_action_sample"}
    if kind in CANDIDATE_ARTIFACTS:
        return {"action": "improvement_candidate", "sink_type": "candidate", "reason": "improvement_signal"}
    if kind in AGENT_ARTIFACTS:
        return {"action": "agent_memory_only", "sink_type": "agent_memory", "reason": "agent_experience"}
    return {"action": "ignore", "reason": "unsupported_artifact"}


async def _upsert_event(
    db: AsyncSession,
    *,
    event_type: str,
    source_type: str,
    source_id: str,
    source_hash: str | None,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    user_id: str | None = None,
    run_id: str | None = None,
    modality: str = "text",
    payload_ref: str | None = None,
    redacted_summary: str | None = None,
    sensitivity_level: str = "internal",
    quality_score: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> LearningEvent:
    existing = (
        await db.execute(
            select(LearningEvent).where(
                LearningEvent.event_type == event_type,
                LearningEvent.source_type == source_type,
                LearningEvent.source_id == source_id,
            )
        )
    ).scalar_one_or_none()
    policy = {"captured": True, "source_hash": source_hash}
    now = now_bjt()
    if existing:
        existing.source_hash = source_hash or existing.source_hash
        existing.department = department or existing.department
        existing.org_unit_id = org_unit_id or existing.org_unit_id
        existing.skill_id = skill_id or existing.skill_id
        existing.user_id = user_id or existing.user_id
        existing.run_id = run_id or existing.run_id
        existing.modality = modality or existing.modality
        existing.payload_ref = payload_ref or existing.payload_ref
        existing.redacted_summary = redacted_summary or existing.redacted_summary
        existing.sensitivity_level = sensitivity_level or existing.sensitivity_level
        existing.quality_score = quality_score if quality_score is not None else existing.quality_score
        existing.policy_result_json = {**(existing.policy_result_json or {}), **policy}
        existing.metadata_json = {**(existing.metadata_json or {}), **(metadata or {})}
        existing.updated_at = now
        return existing
    row = LearningEvent(
        id=_new_id("le"),
        event_type=event_type,
        source_type=source_type,
        source_id=source_id,
        source_hash=source_hash,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        user_id=user_id,
        run_id=run_id,
        modality=modality,
        payload_ref=payload_ref,
        redacted_summary=redacted_summary,
        sensitivity_level=sensitivity_level,
        quality_score=quality_score,
        policy_result_json=policy,
        metadata_json=metadata or {},
    )
    db.add(row)
    await db.flush()
    return row


async def _upsert_artifact(
    db: AsyncSession,
    event: LearningEvent,
    *,
    artifact_kind: str,
    title: str,
    summary: str | None = None,
    content: dict[str, Any] | None = None,
    labels: list[Any] | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    quality_score: float = 0.7,
    confidence: float = 0.7,
    sensitivity_level: str | None = None,
) -> LearningArtifact:
    content_json = content or {}
    artifact_hash = _sha256({"kind": artifact_kind, "title": title, "summary": summary, "content": content_json})
    existing = (
        await db.execute(
            select(LearningArtifact).where(
                LearningArtifact.event_id == event.id,
                LearningArtifact.artifact_kind == artifact_kind,
                LearningArtifact.artifact_hash == artifact_hash,
            )
        )
    ).scalar_one_or_none()
    policy = _policy_for_artifact(
        artifact_kind,
        sensitivity_level=sensitivity_level or event.sensitivity_level or "internal",
        event_type=event.event_type,
    )
    if existing:
        existing.title = title[:240]
        existing.summary = _clip(summary, 4000) if summary else existing.summary
        existing.content_json = content_json
        existing.labels_json = labels or []
        existing.target_type = target_type or existing.target_type
        existing.target_id = target_id or existing.target_id
        existing.quality_score = max(float(existing.quality_score or 0), quality_score)
        existing.confidence = max(float(existing.confidence or 0), confidence)
        existing.policy_result_json = policy
        existing.updated_at = now_bjt()
        row = existing
    else:
        row = LearningArtifact(
            id=_new_id("la"),
            event_id=event.id,
            artifact_kind=artifact_kind,
            artifact_hash=artifact_hash,
            target_type=target_type,
            target_id=target_id,
            department=event.department,
            org_unit_id=event.org_unit_id,
            skill_id=event.skill_id,
            run_id=event.run_id,
            title=title[:240],
            summary=_clip(summary, 4000) if summary else None,
            content_json=content_json,
            labels_json=labels or [],
            quality_score=quality_score,
            confidence=confidence,
            sensitivity_level=sensitivity_level or event.sensitivity_level or "internal",
            review_status="required" if policy.get("action") == "review_first" else None,
            policy_result_json=policy,
        )
        db.add(row)
        await db.flush()
    await _upsert_edge(
        db,
        from_type=_source_node_type(event),
        from_id=event.source_id,
        to_type="learning_artifact",
        to_id=row.id,
        relation="produced",
        department=event.department,
        org_unit_id=event.org_unit_id,
        skill_id=event.skill_id,
        run_id=event.run_id,
        metadata={"event_id": event.id, "artifact_kind": artifact_kind},
    )
    if artifact_kind in CANDIDATE_ARTIFACTS:
        await _ensure_improvement_candidate(db, row, created_by=event.user_id)
    return row


def _bounded_quality_score(value: Any, default: float = 0.8) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    return max(0.0, min(score, 1.0))


def _sf_data_case_items(row: SfDataRecord) -> list[dict[str, Any]]:
    data = row.data_json
    if isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict) and isinstance(data.get("cases"), list):
        raw_items = data.get("cases") or []
    elif isinstance(data, dict) and isinstance(data.get("items"), list):
        raw_items = data.get("items") or []
    elif isinstance(data, dict):
        raw_items = [data]
    else:
        raw_items = [{
            "case_id": row.source_ref or row.id,
            "input_features": {"preview": row.text_preview},
            "expected_action": None,
        }]
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items[:ECOMMERCE_LEARNING_FLOW_MATERIALIZE_LIMIT]):
        if isinstance(item, dict):
            case = dict(item)
        else:
            case = {"input_features": item}
        case.setdefault("case_id", f"{row.id}:{index}")
        items.append(case)
    return items


def _normalize_ecommerce_split(value: Any) -> str:
    text = str(value or "train").strip().lower()
    if text in {"eval", "evaluation", "test", "validation", "valid"}:
        return "eval"
    return "train"


async def materialize_ecommerce_learning_flow_dataset(
    db: AsyncSession,
    user: User,
    *,
    namespace: str = ECOMMERCE_LEARNING_FLOW_DATA_NAMESPACE,
    limit: int = ECOMMERCE_LEARNING_FLOW_MATERIALIZE_LIMIT,
) -> dict[str, Any]:
    clean_namespace = _clean_filter(namespace) or ECOMMERCE_LEARNING_FLOW_DATA_NAMESPACE
    clean_limit = max(1, min(int(limit or ECOMMERCE_LEARNING_FLOW_MATERIALIZE_LIMIT), ECOMMERCE_LEARNING_FLOW_MATERIALIZE_LIMIT))
    rows = (
        await db.execute(
            select(SfDataRecord)
            .where(
                _sf_data_visibility_filter(user),
                SfDataRecord.namespace == clean_namespace,
            )
            .order_by(SfDataRecord.created_at.asc(), SfDataRecord.id.asc())
            .limit(clean_limit)
        )
    ).scalars().all()
    created_or_updated: list[dict[str, Any]] = []
    stats = {"records": len(rows), "cases": 0, "train": 0, "eval": 0}
    for row in rows:
        record_meta = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        for case in _sf_data_case_items(row):
            stats["cases"] += 1
            split = _normalize_ecommerce_split(case.get("split") or record_meta.get("split"))
            artifact_kind = "eval_case" if split == "eval" else "training_sample"
            stats[split] += 1
            case_id = str(case.get("case_id") or f"{row.id}:{stats['cases']}")[:120]
            skill_id = _clean_filter(case.get("skill_id") or row.skill_id or record_meta.get("skill_id"))
            department = _clean_filter(case.get("department") or row.department or record_meta.get("department") or _user_department(user))
            quality_score = _bounded_quality_score(case.get("quality_score", record_meta.get("quality_score", 0.8)))
            labels = case.get("labels") if isinstance(case.get("labels"), list) else []
            normalized_labels = sorted({
                "ecommerce",
                "learning_flow",
                split,
                *(str(item)[:80] for item in labels if str(item or "").strip()),
            })
            input_features = case.get("input_features")
            if input_features in (None, "", [], {}):
                input_features = case.get("input") or case.get("request") or {}
            content = {
                "source_type": "sf_data_record",
                "source_record_id": row.id,
                "namespace": row.namespace,
                "case_id": case_id,
                "split": split,
                "input_features": _redacted_payload(input_features, 6000),
                "expected_action": _redacted_payload(case.get("expected_action") or case.get("expected_output"), 3000),
                "outcome_metrics": _redacted_payload(case.get("outcome_metrics") or case.get("metrics"), 3000),
                "source": {
                    "source": row.source,
                    "source_tool": row.source_tool,
                    "source_ref": row.source_ref,
                    "sf_data_sha256": row.sha256,
                },
            }
            content = {key: value for key, value in content.items() if value not in (None, "", [], {})}
            event_source_id = f"sfdata:{row.id}:{case_id}"
            event = await _upsert_event(
                db,
                event_type="ecommerce_learning_case",
                source_type="sf_data_record",
                source_id=event_source_id,
                source_hash=_sha256({"record_sha256": row.sha256, "case": case}),
                department=department,
                skill_id=skill_id,
                user_id=row.user_id,
                run_id=row.run_id,
                modality="json",
                payload_ref=f"sfdata://{row.id}#{case_id}",
                redacted_summary=_clip(case.get("summary") or row.title or row.text_preview or case_id, 1000),
                sensitivity_level=str(record_meta.get("sensitivity_level") or "internal")[:20],
                quality_score=quality_score,
                metadata={
                    "namespace": row.namespace,
                    "sf_data_record_id": row.id,
                    "split": split,
                    "content_type": row.content_type,
                },
            )
            if row.created_at and (event.created_at is None or event.created_at > row.created_at):
                event.created_at = row.created_at
            artifact = await _upsert_artifact(
                db,
                event,
                artifact_kind=artifact_kind,
                title=str(case.get("title") or row.title or f"电商学习样本 {case_id}")[:240],
                summary=str(case.get("summary") or row.text_preview or f"{split} case {case_id}")[:4000],
                content=content,
                labels=normalized_labels,
                target_type="skill" if skill_id else "project",
                target_id=skill_id or _clean_filter(case.get("project_id") or record_meta.get("project_id")),
                quality_score=quality_score,
                confidence=_bounded_quality_score(case.get("confidence", record_meta.get("confidence", 0.8))),
            )
            artifact.status = "materialized"
            artifact.sink_type = "training_sample"
            artifact.sink_id = artifact.sink_id or f"sfdata:{row.id}:{case_id}"
            if row.created_at and artifact.created_at > row.created_at:
                artifact.created_at = row.created_at
            artifact.updated_at = now_bjt()
            await _upsert_edge(
                db,
                from_type="sf_data_record",
                from_id=row.id,
                to_type="learning_artifact",
                to_id=artifact.id,
                relation="materialized_training_sample",
                department=department,
                skill_id=skill_id,
                run_id=row.run_id,
                metadata={"namespace": row.namespace, "case_id": case_id, "split": split},
            )
            created_or_updated.append({
                "record_id": row.id,
                "artifact_id": artifact.id,
                "artifact_kind": artifact.artifact_kind,
                "skill_id": artifact.skill_id,
                "split": split,
            })
    return {
        "namespace": clean_namespace,
        "materialized": created_or_updated,
        "total": len(created_or_updated),
        "stats": stats,
        "governance": {
            "raw_payload_returned": False,
            "scope": "global" if _is_global_user(user) else "department",
            "department": None if _is_global_user(user) else _user_department(user),
        },
    }


async def _ensure_improvement_candidate(
    db: AsyncSession,
    artifact: LearningArtifact,
    *,
    created_by: str | None = None,
) -> ImprovementCandidate:
    existing = (
        await db.execute(select(ImprovementCandidate).where(ImprovementCandidate.source_artifact_id == artifact.id))
    ).scalar_one_or_none()
    content = artifact.content_json or {}
    default_target_type = {
        "sf_iteration_candidate": "sf",
        "agent_policy_candidate": "agent",
        "agent_creation_candidate": "agent",
        "ai_system_gap_candidate": "ai_system",
        "knowledge_review_candidate": "knowledge",
        "training_improvement_candidate": "training",
    }.get(artifact.artifact_kind, "skill")
    target_type = artifact.target_type or content.get("target_type") or default_target_type
    target_id = artifact.target_id or content.get("target_id") or artifact.skill_id
    proposal = str(content.get("proposal") or artifact.summary or artifact.title or "建议进一步评估并迭代")
    evidence_ids = [artifact.event_id]
    now = now_bjt()
    if existing:
        existing.title = artifact.title or existing.title
        existing.proposal = proposal
        existing.evidence_event_ids_json = sorted(set([*list(existing.evidence_event_ids_json or []), *evidence_ids]))
        existing.priority_score = max(float(existing.priority_score or 0), float(artifact.quality_score or 0))
        existing.updated_at = now
        candidate = existing
    else:
        candidate = ImprovementCandidate(
            id=_new_id("ic"),
            source_artifact_id=artifact.id,
            target_type=str(target_type or "skill")[:40],
            target_id=str(target_id)[:120] if target_id else None,
            department=artifact.department,
            org_unit_id=artifact.org_unit_id,
            skill_id=artifact.skill_id,
            run_id=artifact.run_id,
            title=(artifact.title or "智能闭环改进候选")[:240],
            proposal=proposal,
            evidence_event_ids_json=evidence_ids,
            risk_level=str(content.get("risk_level") or "R2")[:20],
            expected_impact_json=_safe_dict(content.get("expected_impact")),
            priority_score=float(artifact.quality_score or 0),
            created_by=created_by,
            updated_by=created_by,
        )
        db.add(candidate)
        await db.flush()
    artifact.status = "materialized"
    artifact.sink_type = "improvement_candidate"
    artifact.sink_id = candidate.id
    artifact.updated_at = now
    await _upsert_edge(
        db,
        from_type="learning_artifact",
        from_id=artifact.id,
        to_type="improvement_candidate",
        to_id=candidate.id,
        relation="proposed_change",
        department=artifact.department,
        org_unit_id=artifact.org_unit_id,
        skill_id=artifact.skill_id,
        run_id=artifact.run_id,
    )
    return candidate


async def _create_ingestion_job(
    db: AsyncSession,
    artifact: LearningArtifact,
    *,
    sink_type: str,
    action: str,
    user: User | None = None,
    status: str = "running",
) -> LearningIngestionJob:
    job = LearningIngestionJob(
        job_id=_new_id("lij"),
        event_id=artifact.event_id,
        artifact_id=artifact.id,
        sink_type=sink_type,
        department=artifact.department,
        org_unit_id=artifact.org_unit_id,
        skill_id=artifact.skill_id,
        action=action,
        status=status,
        policy_json=artifact.policy_result_json or {},
        created_by=str(getattr(user, "id", "") or "") if user else None,
        started_at=now_bjt() if status == "running" else None,
    )
    db.add(job)
    await db.flush()
    return job


async def _base_for_artifact(db: AsyncSession, user: User, artifact: LearningArtifact) -> DepartmentKnowledgeBase:
    from app.knowledge.service import DEFAULT_RAG_MODE, RAG_BACKEND, _ensure_user_fallback_base, _stable_id, ensure_department_knowledge_bases

    await ensure_department_knowledge_bases(db, actor_id=getattr(user, "id", None))
    base = None
    if artifact.org_unit_id:
        base = (
            await db.execute(
                select(DepartmentKnowledgeBase).where(DepartmentKnowledgeBase.org_unit_id == artifact.org_unit_id)
            )
        ).scalar_one_or_none()
    if base is None and artifact.department:
        base = (
            await db.execute(
                select(DepartmentKnowledgeBase)
                .where(DepartmentKnowledgeBase.department == artifact.department)
                .order_by(DepartmentKnowledgeBase.created_at.asc())
            .limit(1)
            )
        ).scalar_one_or_none()
    if base is None and artifact.department and artifact.department == _user_department(user):
        base = await _ensure_user_fallback_base(db, user)
    if base is None and artifact.department and _is_global_user(user):
        base_id = _stable_id("kb", f"department-name:{artifact.department}")
        base = await db.get(DepartmentKnowledgeBase, base_id)
        if base is None:
            base = DepartmentKnowledgeBase(
                id=base_id,
                org_unit_id=None,
                department=artifact.department,
                name=f"{artifact.department}知识库",
                description="智能闭环自动维护的部门知识库。",
                created_by=getattr(user, "id", None) or "system",
                storage_backend=RAG_BACKEND,
                config_json={
                    "backend": RAG_BACKEND,
                    "mode": DEFAULT_RAG_MODE,
                    "retrieval": "lightrag_hybrid_graph_vector_keyword",
                    "fallback_department_name": True,
                    "created_by_learning_loop": True,
                },
            )
            db.add(base)
            await db.flush()
    if base is None:
        raise AppError("KNOWLEDGE_BASE_NOT_FOUND", 404, {"detail": "artifact department has no knowledge base"})
    return base


def _artifact_text(artifact: LearningArtifact) -> str:
    content = artifact.content_json or {}
    parts = [artifact.title or "", artifact.summary or ""]
    for key in ("text", "detail", "analysis", "proposal", "answer", "context"):
        value = content.get(key)
        if value:
            parts.append(str(value))
    if content.get("metrics"):
        parts.append("指标：" + _json_dumps(content.get("metrics"))[:2000])
    if content.get("source"):
        parts.append("来源：" + _json_dumps(content.get("source"))[:1000])
    text = "\n\n".join(part for part in parts if str(part or "").strip())
    return _clip(text or artifact.title or artifact.id, 20000)


async def materialize_artifact(
    db: AsyncSession,
    user: User,
    artifact_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    artifact = await db.get(LearningArtifact, artifact_id)
    if not artifact:
        raise AppError("NOT_FOUND", 404, {"detail": "learning artifact not found"})
    if not _is_global_user(user):
        dept = _user_department(user)
        if artifact.department and artifact.department != dept:
            raise AppError("FORBIDDEN", 403, {"detail": "no access to artifact department"})
    policy = artifact.policy_result_json or _policy_for_artifact(
        artifact.artifact_kind, sensitivity_level=artifact.sensitivity_level
    )
    action = str(policy.get("action") or "")
    job = await _create_ingestion_job(db, artifact, sink_type=str(policy.get("sink_type") or "unknown"), action=action, user=user)
    try:
        if artifact.artifact_kind in KNOWLEDGE_ARTIFACTS:
            if action == "review_first" and not force:
                artifact.status = "needs_review"
                artifact.review_status = "required"
                job.status = "needs_review"
                job.finished_at = now_bjt()
                return {"artifact": _artifact_to_dict(artifact), "job_id": job.job_id, "requires_review": True}
            from app.knowledge.service import create_document

            base = await _base_for_artifact(db, user, artifact)
            source_type = "report" if artifact.artifact_kind == "report_summary" else "execution"
            if artifact.content_json.get("source_type") in {"todo", "sf", "agent", "training", "feedback"}:
                source_type = str(artifact.content_json.get("source_type"))
            doc = await create_document(
                db,
                user,
                kb_id=base.id,
                title=artifact.title or "智能闭环知识",
                content=_artifact_text(artifact),
                source_type=source_type,
                source_ref=f"learning:{artifact.id}",
                tags=["智能闭环", artifact.artifact_kind, artifact.skill_id or ""],
                metadata={
                    "learning_artifact_id": artifact.id,
                    "learning_event_id": artifact.event_id,
                    "source_event_run_id": artifact.run_id,
                    "source_skill_id": artifact.skill_id,
                    "policy": policy,
                },
                upsert_source=True,
            )
            artifact.status = "materialized"
            artifact.sink_type = "knowledge_document"
            artifact.sink_id = doc.get("id")
            artifact.updated_at = now_bjt()
            job.status = "completed"
            job.sink_id = artifact.sink_id
            job.finished_at = now_bjt()
            await _upsert_edge(
                db,
                from_type="learning_artifact",
                from_id=artifact.id,
                to_type="knowledge_document",
                to_id=str(artifact.sink_id),
                relation="indexed_as",
                department=artifact.department,
                org_unit_id=artifact.org_unit_id,
                skill_id=artifact.skill_id,
                run_id=artifact.run_id,
                metadata={"document_title": doc.get("title")},
            )
            return {"artifact": _artifact_to_dict(artifact), "knowledge_document": doc, "job_id": job.job_id}
        if artifact.artifact_kind in TRAINING_ARTIFACTS:
            artifact.status = "materialized"
            artifact.sink_type = "training_sample"
            artifact.sink_id = artifact.id
            artifact.updated_at = now_bjt()
            job.status = "completed"
            job.sink_id = artifact.id
            job.finished_at = now_bjt()
            await _upsert_edge(
                db,
                from_type="learning_artifact",
                from_id=artifact.id,
                to_type="training_sample",
                to_id=artifact.id,
                relation="created_sample",
                department=artifact.department,
                org_unit_id=artifact.org_unit_id,
                skill_id=artifact.skill_id,
                run_id=artifact.run_id,
            )
            return {"artifact": _artifact_to_dict(artifact), "training_sample": _artifact_to_dict(artifact), "job_id": job.job_id}
        if artifact.artifact_kind in CANDIDATE_ARTIFACTS:
            candidate = await _ensure_improvement_candidate(db, artifact, created_by=str(getattr(user, "id", "") or ""))
            job.status = "completed"
            job.sink_id = candidate.id
            job.finished_at = now_bjt()
            return {"artifact": _artifact_to_dict(artifact), "candidate": _candidate_to_dict(candidate), "job_id": job.job_id}
        if artifact.artifact_kind in AGENT_ARTIFACTS:
            artifact.status = "materialized"
            artifact.sink_type = "agent_memory"
            artifact.sink_id = artifact.id
            artifact.updated_at = now_bjt()
            job.status = "completed"
            job.sink_id = artifact.id
            job.finished_at = now_bjt()
            await _upsert_edge(
                db,
                from_type="learning_artifact",
                from_id=artifact.id,
                to_type="agent_memory",
                to_id=artifact.id,
                relation="remembered_as",
                department=artifact.department,
                org_unit_id=artifact.org_unit_id,
                skill_id=artifact.skill_id,
                run_id=artifact.run_id,
            )
            return {"artifact": _artifact_to_dict(artifact), "agent_memory": _artifact_to_dict(artifact), "job_id": job.job_id}
        artifact.status = "ignored"
        job.status = "completed"
        job.finished_at = now_bjt()
        return {"artifact": _artifact_to_dict(artifact), "job_id": job.job_id, "ignored": True}
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.error = _clip(exc, 2000)
        job.finished_at = now_bjt()
        raise


async def ignore_artifact(db: AsyncSession, user: User, artifact_id: str, reason: str | None = None) -> dict[str, Any]:
    artifact = await db.get(LearningArtifact, artifact_id)
    if not artifact:
        raise AppError("NOT_FOUND", 404, {"detail": "learning artifact not found"})
    if not _is_global_user(user) and artifact.department and artifact.department != _user_department(user):
        raise AppError("FORBIDDEN", 403)
    artifact.status = "ignored"
    artifact.review_status = "ignored"
    policy = dict(artifact.policy_result_json or {})
    policy["ignored_reason"] = _clip(reason, 500) if reason else "manual_ignore"
    policy["ignored_by"] = getattr(user, "id", None)
    artifact.policy_result_json = policy
    artifact.updated_at = now_bjt()
    return _artifact_to_dict(artifact)


async def _extract_from_decision(db: AsyncSession, event: LearningEvent, row: DecisionLog) -> list[LearningArtifact]:
    artifacts: list[LearningArtifact] = []
    output = row.output_result if isinstance(row.output_result, dict) else {}
    reports = extract_report_cards(row.id, output)
    for idx, card in enumerate(reports):
        title = str(card.get("title") or f"执行报告 {row.id}-{idx + 1}")
        summary = str(card.get("summary") or "")
        content = {
            "text": summary or title,
            "detail": card.get("detail") or card.get("content"),
            "metrics": card.get("metrics") or [],
            "source": {"decision_log_id": row.id, "run_id": row.run_id, "skill_id": row.skill_id},
            "source_type": "report",
        }
        artifacts.append(await _upsert_artifact(
            db,
            event,
            artifact_kind="report_summary",
            title=title,
            summary=summary,
            content=content,
            labels=["report", *(card.get("tags") or [])],
            target_type="knowledge",
            target_id=row.skill_id,
            quality_score=0.82,
            confidence=0.82,
        ))
    if output.get("todos") or output.get("actions") or row.suggested_action:
        artifact = await _upsert_artifact(
            db,
            event,
            artifact_kind="training_sample",
            title=f"{row.skill_id} 动作结果样本",
            summary="Skill 输出了可执行动作，可作为动作推荐/评估训练样本。",
            content={
                "input": row.input_snapshot or {},
                "output": output,
                "suggested_action": row.suggested_action or {},
                "user_action": row.user_action,
                "rating": row.rating,
                "feedback_type": row.feedback_type,
                "business_impact": row.business_impact or {},
                "source": {"decision_log_id": row.id, "run_id": row.run_id, "skill_id": row.skill_id},
            },
            labels=["training", "action_outcome"],
            target_type="skill",
            target_id=row.skill_id,
            quality_score=0.72 + (0.1 if row.user_action else 0),
            confidence=0.72,
        )
        artifacts.append(await _materialize_training_artifact(db, event, artifact))
    has_feedback = bool(row.user_action or row.user_feedback or row.rating is not None or row.reject_reason or row.business_impact)
    if has_feedback:
        artifact = await _upsert_artifact(
            db,
            event,
            artifact_kind="training_sample",
            title=f"{row.skill_id} 用户反馈样本",
            summary=_clip(row.user_feedback or row.reject_reason or row.user_action or "用户反馈", 1000),
            content={
                "input": row.input_snapshot or {},
                "output": output,
                "user_action": row.user_action,
                "user_feedback": row.user_feedback,
                "rating": row.rating,
                "feedback_type": row.feedback_type,
                "reject_reason": row.reject_reason,
                "business_impact": row.business_impact or {},
                "source": {"decision_log_id": row.id, "run_id": row.run_id, "skill_id": row.skill_id},
            },
            labels=["training", "feedback"],
            target_type="skill",
            target_id=row.skill_id,
            quality_score=0.86,
            confidence=0.8,
        )
        artifacts.append(await _materialize_training_artifact(db, event, artifact))
    negative = str(row.user_action or "").lower() in {"rejected", "failed", "help_requested"} or (row.rating is not None and row.rating <= 2)
    if negative:
        artifact = await _upsert_artifact(
            db,
            event,
            artifact_kind="eval_case",
            title=f"{row.skill_id} 负反馈评测样本",
            summary=_clip(row.reject_reason or row.user_feedback or "低评分/驳回样本", 1000),
            content={
                "input": row.input_snapshot or {},
                "actual_output": output,
                "expected_signal": "avoid_or_fix",
                "user_action": row.user_action,
                "rating": row.rating,
                "reject_reason": row.reject_reason,
                "source": {"decision_log_id": row.id, "run_id": row.run_id, "skill_id": row.skill_id},
            },
            labels=["eval", "negative_feedback"],
            target_type="skill",
            target_id=row.skill_id,
            quality_score=0.9,
            confidence=0.84,
        )
        artifacts.append(await _materialize_training_artifact(db, event, artifact))
        artifacts.append(await _upsert_artifact(
            db,
            event,
            artifact_kind="skill_improvement_candidate",
            title=f"优化 Skill：{row.skill_id} 的低评分/驳回案例",
            summary=_clip(row.reject_reason or row.user_feedback or "用户对输出不满意，需要复盘规则、数据源或输出动作。", 2000),
            content={
                "target_type": "skill",
                "target_id": row.skill_id,
                "proposal": "基于该负反馈补充评测用例，复盘输入条件、输出依据和动作推荐规则。",
                "risk_level": "R2",
                "expected_impact": {"quality": "降低同类驳回率", "source": "decision_feedback"},
            },
            labels=["candidate", "skill", "negative_feedback"],
            target_type="skill",
            target_id=row.skill_id,
            quality_score=0.78,
            confidence=0.74,
        ))
    return artifacts


async def capture_decision_log(db: AsyncSession, row: DecisionLog, *, user_id: str | None = None) -> LearningEvent:
    dept, org_unit_id = await _skill_department(db, row.skill_id)
    output = row.output_result if isinstance(row.output_result, dict) else {}
    feedback_bits = [row.user_action, row.user_feedback, row.reject_reason, row.feedback_type]
    has_feedback = any(bool(item) for item in feedback_bits) or row.rating is not None or bool(row.business_impact)
    event_type = "decision.feedback" if has_feedback else "decision.created"
    model_context = _decision_model_context_from_payload(output, model_id=row.model_id)
    event = await _upsert_event(
        db,
        event_type=event_type,
        source_type="decision_log",
        source_id=str(row.id),
        source_hash=_sha256({
            "input": row.input_snapshot,
            "output": row.output_result,
            "action": row.user_action,
            "feedback": row.user_feedback,
            "rating": row.rating,
        }),
        department=dept,
        org_unit_id=org_unit_id,
        skill_id=row.skill_id,
        user_id=user_id or row.approver or row.target_user,
        run_id=row.run_id,
        payload_ref=f"decision_log:{row.id}",
        redacted_summary=_clip(output.get("summary") or row.user_feedback or f"DecisionLog {row.id}", 1000),
        sensitivity_level="restricted" if row.business_impact else "internal",
        quality_score=0.86 if has_feedback else 0.72,
        metadata={
            "approval_status": row.approval_status,
            "is_sandbox": bool(row.is_sandbox),
            **({"model_context": model_context} if model_context else {}),
        },
    )
    if model_context:
        await _link_model_context_to_decision(
            db,
            event,
            decision_log_id=row.id,
            model_context=model_context,
        )
    if not row.is_sandbox:
        await _extract_from_decision(db, event, row)
    else:
        event.status = "ignored"
        event.policy_result_json = {**(event.policy_result_json or {}), "action": "ignore", "reason": "sandbox"}
    return event


async def _execution_steps_for_run(db: AsyncSession, run_id: str) -> list[ExecutionStep]:
    return (
        await db.execute(
            select(ExecutionStep)
            .where(ExecutionStep.run_id == run_id)
            .order_by(ExecutionStep.step_order.asc().nullslast(), ExecutionStep.id.asc())
            .limit(50)
        )
    ).scalars().all()


def _execution_step_training_item(step: ExecutionStep) -> dict[str, Any]:
    item = {
        "step_id": step.id,
        "step_order": step.step_order,
        "status": step.status,
        "input": _redacted_payload(step.input_data, 3000),
        "output": _redacted_payload(step.output_data, 3000),
        "error": _clip(step.error_message, 1200) if step.error_message else None,
        "duration_ms": step.duration_ms,
        "started_at": _iso(step.started_at),
        "completed_at": _iso(step.completed_at),
    }
    return {key: value for key, value in item.items() if value not in (None, "", [], {})}


def _execution_trace_sample_content(
    row: ExecutionRun,
    *,
    steps: list[ExecutionStep],
    runtime_agent: dict[str, Any],
) -> dict[str, Any]:
    step_items = [_execution_step_training_item(step) for step in steps]
    run_context = {
        "run_id": row.id,
        "skill_id": row.skill_id,
        "run_mode": row.run_mode,
        "trigger_type": row.trigger_type,
        "status": row.status,
        "source_instance_id": row.source_instance_id,
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "total_steps": row.total_steps,
        "completed_steps": row.completed_steps,
    }
    run_context = {key: value for key, value in run_context.items() if value not in (None, "", [], {})}
    return {
        "instruction": "基于 Skill 执行轨迹学习输入、输出、中间产物和节点控制上下文。",
        "input": {
            "run": run_context,
            "steps": [
                {
                    key: item[key]
                    for key in ("step_id", "step_order", "status", "input")
                    if key in item
                }
                for item in step_items
            ],
            "metadata": _redacted_payload(row.metadata_json, 3000),
        },
        "output": {
            "status": row.status,
            "summary": _clip(row.summary, 2000) if row.summary else None,
            "steps": [
                {
                    key: item[key]
                    for key in ("step_id", "step_order", "status", "output", "error")
                    if key in item
                }
                for item in step_items
            ],
        },
        "formed_data": {
            "steps": step_items,
            "metadata": _redacted_payload(row.metadata_json, 3000),
            "runtime_agent": runtime_agent or None,
        },
        "source": {
            "run_id": row.id,
            "skill_id": row.skill_id,
            "source_instance_id": row.source_instance_id,
        },
        "source_type": "execution_trace",
        "source_channel": row.trigger_type or "platform",
    }


async def _extract_execution_trace_training_sample(
    db: AsyncSession,
    event: LearningEvent,
    row: ExecutionRun,
    *,
    runtime_agent: dict[str, Any],
) -> LearningArtifact | None:
    status = str(row.status or "").lower()
    if status not in {"completed", "failed", "timeout"}:
        return None
    steps = await _execution_steps_for_run(db, str(row.id))
    if not steps and not row.summary and not row.metadata_json:
        return None
    content = _execution_trace_sample_content(row, steps=steps, runtime_agent=runtime_agent)
    artifact = await _upsert_artifact(
        db,
        event,
        artifact_kind="training_sample",
        title=f"{row.skill_id or 'Skill'} 执行轨迹样本",
        summary=_clip(row.summary or f"执行 {row.id} 的输入、输出和形成数据已沉淀为训练样本。", 1200),
        content=content,
        labels=["training", "execution_trace", status, f"run_mode:{row.run_mode or 'unknown'}"],
        target_type="skill",
        target_id=row.skill_id,
        quality_score=0.76 if status == "completed" else 0.82,
        confidence=0.74,
    )
    return await _materialize_training_artifact(db, event, artifact)


async def capture_execution_run(db: AsyncSession, row: ExecutionRun, *, user_id: str | None = None) -> LearningEvent:
    dept, org_unit_id = await _skill_department(db, row.skill_id)
    event_type = "skill.run.failed" if str(row.status).lower() in {"failed", "timeout"} else "skill.run.completed"
    if str(row.status).lower() == "running":
        event_type = "skill.run.running"
    runtime_agent = await _runtime_agent_context(db, row)
    event = await _upsert_event(
        db,
        event_type=event_type,
        source_type="execution_run",
        source_id=str(row.id),
        source_hash=_sha256({"status": row.status, "summary": row.summary, "metadata": row.metadata_json}),
        department=dept,
        org_unit_id=org_unit_id,
        skill_id=row.skill_id,
        user_id=user_id,
        run_id=row.id,
        payload_ref=f"execution_run:{row.id}",
        redacted_summary=_clip(row.summary or f"{row.skill_id or 'Skill'} {row.status}", 1000),
        sensitivity_level="internal",
        quality_score=0.7 if row.status == "completed" else 0.6,
        metadata={
            "run_mode": row.run_mode,
            "trigger_type": row.trigger_type,
            "status": row.status,
            "source_instance_id": row.source_instance_id,
            "runtime_agent": runtime_agent or None,
        },
    )
    if str(row.run_mode or "") in SAMPLE_RUN_MODES:
        event.status = "ignored"
        event.policy_result_json = {**(event.policy_result_json or {}), "action": "ignore", "reason": "sample_run"}
        return event
    if runtime_agent.get("agent_id"):
        await _upsert_edge(
            db,
            from_type="agent",
            from_id=str(runtime_agent["agent_id"]),
            to_type="run",
            to_id=str(row.id),
            relation="controlled_run",
            department=dept or runtime_agent.get("department"),
            org_unit_id=org_unit_id,
            skill_id=row.skill_id,
            run_id=row.id,
            metadata={
                "event_id": event.id,
                "run_mode": row.run_mode,
                "trigger_type": row.trigger_type,
                "status": row.status,
                "gateway_kind": runtime_agent.get("gateway_kind"),
                "agent_purpose": runtime_agent.get("agent_purpose"),
                "contract": runtime_agent.get("contract"),
            },
        )
    await _extract_execution_trace_training_sample(db, event, row, runtime_agent=runtime_agent)
    if row.summary and row.status == "completed":
        await _upsert_artifact(
            db,
            event,
            artifact_kind="knowledge_note",
            title=f"{row.skill_id or 'Skill'} 执行摘要",
            summary=row.summary,
            content={"text": row.summary, "source": {"run_id": row.id, "skill_id": row.skill_id}, "source_type": "execution"},
            labels=["run_summary", "knowledge"],
            target_type="knowledge",
            target_id=row.skill_id,
            quality_score=0.7,
            confidence=0.7,
        )
    if row.status in {"failed", "timeout"}:
        await _upsert_artifact(
            db,
            event,
            artifact_kind="skill_improvement_candidate",
            title=f"修复 Skill 运行失败：{row.skill_id or row.id}",
            summary=row.summary or "Skill 运行失败，需要复盘数据源、运行节点、契约或脚本逻辑。",
            content={
                "target_type": "skill",
                "target_id": row.skill_id,
                "proposal": "把本次失败加入评测/回归集，检查采集 proof、异常栈、运行节点和输出契约。",
                "risk_level": "R2",
                "expected_impact": {"stability": "降低失败率"},
            },
            labels=["candidate", "skill", "failure"],
            target_type="skill",
            target_id=row.skill_id,
            quality_score=0.76,
            confidence=0.7,
        )
    return event


def _analysis_deployment_context(route: dict[str, Any]) -> dict[str, Any]:
    deployment = route.get("active_model_deployment") if isinstance(route.get("active_model_deployment"), dict) else {}
    inference = route.get("deployed_model_inference") if isinstance(route.get("deployed_model_inference"), dict) else {}
    deployment_id = str(deployment.get("id") or "").strip()
    if not deployment_id:
        return {}
    context = {
        "model_deployment_id": deployment_id[:80],
        "model_family": str(deployment.get("model_family") or "")[:100] or None,
        "deployment_status": str(deployment.get("status") or "")[:30] or None,
        "rollout_percent": deployment.get("rollout_percent"),
        "rollout_selected": deployment.get("rollout_selected"),
        "rollout_bucket": deployment.get("rollout_bucket"),
        "rollout_reason": str(deployment.get("rollout_reason") or "")[:50] or None,
        "artifact_id": str(deployment.get("artifact_id") or "")[:120] or None,
        "artifact_sha256": str(deployment.get("artifact_sha256") or "")[:64] or None,
        "model_runtime_status": (
            deployment.get("runtime_status")
            if isinstance(deployment.get("runtime_status"), dict)
            else None
        ),
    }
    if inference:
        context.update(
            {
                "inference_status": str(inference.get("status") or "")[:30] or None,
                "inference_backend": str(inference.get("backend") or "")[:50] or None,
                "inference_gateway_id": str(inference.get("gateway_id") or "")[:100] or None,
                "inference_profile": str(inference.get("profile") or "")[:50] or None,
                "inference_text_sha256": str(inference.get("text_sha256") or "")[:64] or None,
                "inference_metrics": (
                    inference.get("metrics")
                    if isinstance(inference.get("metrics"), dict)
                    else None
                ),
            }
        )
    return {key: value for key, value in context.items() if value not in (None, "", [], {})}


def _analysis_skipped_deployments(route: dict[str, Any]) -> list[dict[str, Any]]:
    rows = route.get("skipped_model_deployments") if isinstance(route, dict) else []
    if not isinstance(rows, list):
        return []
    skipped: list[dict[str, Any]] = []
    for row in rows[:10]:
        if not isinstance(row, dict):
            continue
        deployment_id = str(row.get("id") or "").strip()
        if not deployment_id:
            continue
        runtime_status = row.get("runtime_status") if isinstance(row.get("runtime_status"), dict) else None
        item = {
            "model_deployment_id": deployment_id[:80],
            "job_id": str(row.get("job_id") or "")[:80] or None,
            "department": str(row.get("department") or "")[:80] or None,
            "model_family": str(row.get("model_family") or "")[:100] or None,
            "deployment_status": str(row.get("status") or "")[:30] or None,
            "rollout_percent": row.get("rollout_percent"),
            "rollout_selected": row.get("rollout_selected"),
            "rollout_bucket": row.get("rollout_bucket"),
            "rollout_reason": str(row.get("rollout_reason") or "")[:50] or None,
            "artifact_id": str(row.get("artifact_id") or "")[:120] or None,
            "artifact_sha256": str(row.get("artifact_sha256") or "")[:64] or None,
            "skip_reason": str(row.get("skip_reason") or "")[:80] or None,
            "model_runtime_status": runtime_status,
        }
        skipped.append({key: value for key, value in item.items() if value not in (None, "", [], {})})
    return skipped


async def capture_intelligence_analyze_run(
    db: AsyncSession,
    row: IntelligenceAnalyzeRun,
    *,
    output_payload: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> LearningEvent:
    dept, org_unit_id = await _skill_department(db, row.skill_id)
    route = row.analysis_delegate_route if isinstance(row.analysis_delegate_route, dict) else {}
    deployment_context = _analysis_deployment_context(route)
    skipped_deployments = _analysis_skipped_deployments(route)
    inference_agent = await _inference_agent_context(db, deployment_context.get("inference_gateway_id"))
    analysis_agent = await _analysis_agent_context(db, row.analysis_agent_id)
    output = output_payload if isinstance(output_payload, dict) else {}
    summary = (
        output.get("summary")
        or output.get("text")
        or output.get("result")
        or f"智能分析 {row.run_id or row.id}"
    )
    event = await _upsert_event(
        db,
        event_type="intelligence.analyze.completed" if not row.degraded else "intelligence.analyze.degraded",
        source_type="intelligence_analyze_run",
        source_id=str(row.id),
        source_hash=_sha256(
            {
                "cache_key": row.cache_key,
                "cache_hit": row.cache_hit,
                "output_hash": row.output_hash,
                "context_hash": row.context_hash,
                "model": row.model,
                "backend": row.analysis_backend,
                "route": route,
                "degraded": row.degraded,
            }
        ),
        department=dept or row.department,
        org_unit_id=org_unit_id,
        skill_id=row.skill_id,
        user_id=user_id,
        run_id=row.run_id,
        payload_ref=f"intelligence_analyze_run:{row.id}",
        redacted_summary=_clip(summary, 1000),
        sensitivity_level="internal",
        quality_score=0.78 if not row.degraded else 0.6,
        metadata={
            "analysis_run_id": row.id,
            "cache_id": row.cache_id,
            "cache_key": row.cache_key,
            "cache_hit": bool(row.cache_hit),
            "cache_hit_of_run_id": row.cache_hit_of_run_id,
            "analysis_backend": row.analysis_backend,
            "analysis_agent_id": row.analysis_agent_id,
            "analysis_model": row.model,
            "prompt_version": row.prompt_version,
            "prompt_git_ref": row.prompt_git_ref,
            "prompt_hash": row.prompt_hash,
            "context_hash": row.context_hash,
            "output_hash": row.output_hash,
            "llm_output_hash": row.llm_output_hash,
            "degraded": bool(row.degraded),
            "degraded_reason": row.degraded_reason,
            "evidence_passed": row.evidence_passed,
            "data_health_ratio": float(row.data_health_ratio) if row.data_health_ratio is not None else None,
            "usage": {
                "prompt_tokens": row.prompt_tokens or 0,
                "completion_tokens": row.completion_tokens or 0,
                "total_tokens": row.total_tokens or 0,
            },
            "active_model_deployment": deployment_context or None,
            "skipped_model_deployments": skipped_deployments or None,
            "inference_agent": inference_agent or None,
            "analysis_agent": analysis_agent or None,
            "analysis_delegate_route": {
                key: route.get(key)
                for key in ("mode", "scope", "platform_fallback", "preferred_by", "failed_agent_id")
                if route.get(key) not in (None, "", [], {})
            },
        },
    )
    if row.run_id:
        await _upsert_edge(
            db,
            from_type="run",
            from_id=str(row.run_id),
            to_type="intelligence_analyze",
            to_id=str(row.id),
            relation="used_analysis",
            department=event.department,
            org_unit_id=event.org_unit_id,
            skill_id=event.skill_id,
            run_id=event.run_id,
            metadata={"event_id": event.id, "cache_hit": bool(row.cache_hit), "backend": row.analysis_backend},
        )
    if row.cache_id:
        await _upsert_edge(
            db,
            from_type="intelligence_analyze_cache",
            from_id=str(row.cache_id),
            to_type="intelligence_analyze",
            to_id=str(row.id),
            relation="served_analysis",
            department=event.department,
            org_unit_id=event.org_unit_id,
            skill_id=event.skill_id,
            run_id=event.run_id,
            metadata={"cache_key": row.cache_key, "cache_hit": bool(row.cache_hit)},
        )
    if row.analysis_agent_id:
        await _upsert_edge(
            db,
            from_type="agent",
            from_id=str(row.analysis_agent_id),
            to_type="intelligence_analyze",
            to_id=str(row.id),
            relation="handled_analysis",
            department=event.department,
            org_unit_id=event.org_unit_id,
            skill_id=event.skill_id,
            run_id=event.run_id,
            metadata={
                "event_id": event.id,
                "backend": row.analysis_backend,
                "model": row.model,
                "agent_purpose": analysis_agent.get("agent_purpose"),
                "gateway_kind": analysis_agent.get("gateway_kind"),
                "contract": analysis_agent.get("contract"),
            },
        )
        await _upsert_edge(
            db,
            from_type="agent",
            from_id=str(row.analysis_agent_id),
            to_type="intelligence_analyze",
            to_id=str(row.id),
            relation="controlled_analysis",
            department=event.department or analysis_agent.get("department"),
            org_unit_id=event.org_unit_id,
            skill_id=event.skill_id,
            run_id=event.run_id,
            metadata={
                "event_id": event.id,
                "backend": row.analysis_backend,
                "model": row.model,
                "agent_purpose": analysis_agent.get("agent_purpose"),
                "gateway_kind": analysis_agent.get("gateway_kind"),
                "contract": analysis_agent.get("contract"),
                "analysis_delegate_route": {
                    key: route.get(key)
                    for key in ("mode", "scope", "platform_fallback", "preferred_by", "failed_agent_id")
                    if route.get(key) not in (None, "", [], {})
                },
            },
        )
    deployment_id = str(deployment_context.get("model_deployment_id") or "").strip()
    if deployment_id:
        await _upsert_edge(
            db,
            from_type="model_deployment",
            from_id=deployment_id,
            to_type="intelligence_analyze",
            to_id=str(row.id),
            relation="participated_in_analysis",
            department=event.department,
            org_unit_id=event.org_unit_id,
            skill_id=event.skill_id,
            run_id=event.run_id,
            metadata={
                "event_id": event.id,
                "analysis_backend": row.analysis_backend,
                "inference_status": deployment_context.get("inference_status"),
                "inference_text_sha256": deployment_context.get("inference_text_sha256"),
                "rollout_reason": deployment_context.get("rollout_reason"),
            },
        )
        if inference_agent.get("agent_id"):
            common_inference_metadata = {
                "event_id": event.id,
                "model_deployment_id": deployment_id,
                "model_family": deployment_context.get("model_family"),
                "artifact_id": deployment_context.get("artifact_id"),
                "artifact_sha256": deployment_context.get("artifact_sha256"),
                "inference_status": deployment_context.get("inference_status"),
                "inference_backend": deployment_context.get("inference_backend"),
                "inference_profile": deployment_context.get("inference_profile"),
                "inference_text_sha256": deployment_context.get("inference_text_sha256"),
                "inference_metrics": deployment_context.get("inference_metrics"),
                "contract": inference_agent.get("contract"),
            }
            await _upsert_edge(
                db,
                from_type="agent",
                from_id=str(inference_agent["agent_id"]),
                to_type="model_deployment",
                to_id=deployment_id,
                relation="served_model_inference",
                department=event.department or inference_agent.get("department"),
                org_unit_id=event.org_unit_id,
                skill_id=event.skill_id,
                run_id=event.run_id,
                metadata=common_inference_metadata,
            )
            await _upsert_edge(
                db,
                from_type="agent",
                from_id=str(inference_agent["agent_id"]),
                to_type="intelligence_analyze",
                to_id=str(row.id),
                relation="controlled_inference",
                department=event.department or inference_agent.get("department"),
                org_unit_id=event.org_unit_id,
                skill_id=event.skill_id,
                run_id=event.run_id,
                metadata=common_inference_metadata,
            )
    for skipped in skipped_deployments:
        skipped_deployment_id = str(skipped.get("model_deployment_id") or "").strip()
        if not skipped_deployment_id:
            continue
        await _upsert_edge(
            db,
            from_type="model_deployment",
            from_id=skipped_deployment_id,
            to_type="intelligence_analyze",
            to_id=str(row.id),
            relation="skipped_analysis",
            department=event.department,
            org_unit_id=event.org_unit_id,
            skill_id=event.skill_id,
            run_id=event.run_id,
            metadata={
                "event_id": event.id,
                "analysis_backend": row.analysis_backend,
                "skip_reason": skipped.get("skip_reason"),
                "rollout_reason": skipped.get("rollout_reason"),
                "model_runtime_status": skipped.get("model_runtime_status"),
            },
        )
    if output:
        artifact = await _upsert_artifact(
            db,
            event,
            artifact_kind="training_sample",
            title=f"{row.skill_id} 智能分析输出样本",
            summary=_clip(summary, 1000),
            content={
                "instruction": "基于 SkillForge 受控运行上下文生成业务分析输出。",
                "input": {
                    "source_type": "intelligence_analyze_run",
                    "run_id": row.run_id,
                    "skill_id": row.skill_id,
                    "skill_git_commit_full": row.skill_git_commit_full,
                    "prompt_version": row.prompt_version,
                    "prompt_hash": row.prompt_hash,
                    "context_hash": row.context_hash,
                    "cache_hit": bool(row.cache_hit),
                    "analysis_backend": row.analysis_backend,
                    "analysis_agent_id": row.analysis_agent_id,
                    "model_deployment_id": deployment_id or None,
                    "raw_context_returned": False,
                },
                "output": output,
                "decision": {
                    "analysis_run_id": row.id,
                    "output_hash": row.output_hash,
                    "evidence_passed": row.evidence_passed,
                    "degraded": bool(row.degraded),
                    "degraded_reason": row.degraded_reason,
                },
                "model_context": deployment_context,
                "source": {
                    "analysis_run_id": row.id,
                    "run_id": row.run_id,
                    "skill_id": row.skill_id,
                    "cache_id": row.cache_id,
                },
                "source_type": "intelligence_analyze_run",
            },
            labels=["training", "analysis_output", row.analysis_backend or "platform"],
            target_type="skill",
            target_id=row.skill_id,
            quality_score=0.78 if not row.degraded else 0.62,
            confidence=0.74,
        )
        await _materialize_training_artifact(db, event, artifact)
    return event


async def capture_execution_artifact(db: AsyncSession, row: ExecutionArtifact, *, user_id: str | None = None) -> LearningEvent:
    dept, org_unit_id = await _skill_department(db, row.skill_id)
    summary = _safe_dict(row.summary_json)
    event = await _upsert_event(
        db,
        event_type="execution.artifact.captured",
        source_type="execution_artifact",
        source_id=str(row.id),
        source_hash=row.sha256,
        department=dept,
        org_unit_id=org_unit_id,
        skill_id=row.skill_id,
        user_id=user_id,
        run_id=row.run_id,
        payload_ref=f"execution_artifact:{row.id}",
        redacted_summary=_clip(f"{row.kind} · {row.schema_name or summary.get('type') or 'data'}", 1000),
        sensitivity_level="internal",
        quality_score=0.68,
        metadata={
            "artifact_id": row.id,
            "kind": row.kind,
            "schema": row.schema_name,
            "sha256": row.sha256,
            "size_bytes": row.size_bytes,
            "uncompressed_size_bytes": row.uncompressed_size_bytes,
            "decision_log_id": row.decision_log_id,
            "summary": summary,
        },
    )
    await _upsert_edge(
        db,
        from_type="run",
        from_id=str(row.run_id),
        to_type="execution_artifact",
        to_id=str(row.id),
        relation="produced_artifact",
        department=dept,
        org_unit_id=org_unit_id,
        skill_id=row.skill_id,
        run_id=row.run_id,
        metadata={"event_id": event.id, "kind": row.kind, "sha256": row.sha256, "schema": row.schema_name},
    )
    if row.decision_log_id:
        await _upsert_edge(
            db,
            from_type="decision_log",
            from_id=str(row.decision_log_id),
            to_type="execution_artifact",
            to_id=str(row.id),
            relation="captured_data",
            department=dept,
            org_unit_id=org_unit_id,
            skill_id=row.skill_id,
            run_id=row.run_id,
            metadata={"event_id": event.id, "kind": row.kind, "sha256": row.sha256, "schema": row.schema_name},
        )
    await _upsert_artifact(
        db,
        event,
        artifact_kind="execution_data_artifact",
        title=f"{row.skill_id or 'Skill'} 执行数据：{row.kind}",
        summary=f"执行 {row.run_id} 形成 {row.kind} 数据资产，schema={row.schema_name or '-'}，sha256={row.sha256[:12]}。",
        content={
            "text": f"执行数据资产 {row.kind} 已入库，可通过 execution_artifact:{row.id} 追踪原始输入/输出。",
            "source_type": "execution_artifact",
            "source": {
                "artifact_id": row.id,
                "run_id": row.run_id,
                "skill_id": row.skill_id,
                "decision_log_id": row.decision_log_id,
                "kind": row.kind,
                "schema": row.schema_name,
                "sha256": row.sha256,
                "summary": summary,
            },
        },
        labels=["execution_data", str(row.kind or "artifact"), *([f"schema:{row.schema_name}"] if row.schema_name else [])],
        target_type="execution_artifact",
        target_id=str(row.id),
        quality_score=0.68,
        confidence=0.72,
    )
    return event


async def capture_sf_mcp_call(db: AsyncSession, row: CodexMcpCallAudit) -> LearningEvent:
    dept, org_unit_id = await _skill_department(db, row.skill_id)
    detail = row.detail_json if isinstance(row.detail_json, dict) else {}
    event = await _upsert_event(
        db,
        event_type="sf.mcp.called",
        source_type="sf_mcp_call",
        source_id=str(row.id),
        source_hash=_sha256({"tool": row.tool, "ok": row.ok, "error": row.error_code, "detail": detail}),
        department=dept,
        org_unit_id=org_unit_id,
        skill_id=row.skill_id,
        user_id=row.user_id,
        run_id=row.debug_run_id,
        payload_ref=f"codex_mcp_call_audit:{row.id}",
        redacted_summary=_clip(f"{row.tool} · {'成功' if row.ok else row.error_code or '失败'}", 1000),
        sensitivity_level="internal",
        quality_score=0.62 if row.ok else 0.72,
        metadata={"tool": row.tool, "ok": bool(row.ok), "dry_run": bool(row.dry_run), "data_scope": row.data_scope},
    )
    if not row.ok:
        await _upsert_artifact(
            db,
            event,
            artifact_kind="sf_iteration_candidate",
            title=f"修复或增强 SF 能力：{row.tool}",
            summary=f"SF MCP 调用失败：{row.error_code or 'unknown'}。建议补充防呆、错误提示、参数校验或能力元数据。",
            content={
                "target_type": "sf",
                "target_id": row.tool,
                "proposal": "分析该 MCP 工具失败原因，补齐参数提示、权限说明、dry-run 示例和测试用例。",
                "risk_level": "R1",
                "expected_impact": {"sf_success_rate": "提升工具调用成功率"},
                "tool": row.tool,
                "error_code": row.error_code,
            },
            labels=["candidate", "sf", "mcp_failure"],
            target_type="sf",
            target_id=row.tool,
            quality_score=0.8,
            confidence=0.72,
        )
    return event


async def capture_project_run_opened(db: AsyncSession, row: Any) -> LearningEvent:
    from app.projects.models import Project

    project = await db.get(Project, row.project_id)
    input_snapshot = row.input_snapshot if isinstance(row.input_snapshot, dict) else {}
    event = await _upsert_event(
        db,
        event_type="project.run.opened",
        source_type="project_run",
        source_id=str(row.id),
        source_hash=_sha256(
            {
                "project_id": row.project_id,
                "version_id": row.version_id,
                "request_id": getattr(row, "request_id", None),
                "input": input_snapshot,
                "status": row.status,
            }
        ),
        department=getattr(project, "department", None) or getattr(row, "department", None),
        org_unit_id=getattr(project, "department_id", None) or getattr(row, "department_id", None),
        skill_id=f"project:{row.project_id}",
        user_id=getattr(row, "user_id", None),
        run_id=getattr(row, "execution_run_id", None) or getattr(row, "id", None),
        payload_ref=f"project_runs:{row.id}",
        redacted_summary=_clip(f"项目 {getattr(project, 'name', None) or row.project_id} 打开运行，输入参数已进入平台闭环。", 1000),
        sensitivity_level="internal",
        quality_score=0.66,
        metadata={
            "project_id": row.project_id,
            "project_run_id": row.id,
            "project_run_request_id": getattr(row, "request_id", None),
            "project_version_id": getattr(row, "version_id", None),
            "input_keys": sorted(str(key) for key in input_snapshot.keys())[:50],
            "status": row.status,
        },
    )
    return event


async def capture_project_ingress_event(db: AsyncSession, row: Any) -> LearningEvent:
    from app.projects.models import Project, ProjectRun

    run = await db.get(ProjectRun, row.project_run_id)
    project = await db.get(Project, run.project_id) if run else None
    output = row.output_result if isinstance(row.output_result, dict) else {}
    row_metadata = row.metadata_json if isinstance(getattr(row, "metadata_json", None), dict) else {}
    reports = row.reports if isinstance(row.reports, list) else []
    todos = row.todos if isinstance(row.todos, list) else []
    proofs = row.proofs if isinstance(row.proofs, list) else []
    event_type = str(getattr(row, "event_type", "") or "")
    if event_type == "input":
        learning_event_type = "project.input.received"
        fallback_summary = f"项目输入记录：{getattr(project, 'name', None) or getattr(run, 'project_id', None) or row.project_run_id}"
    elif event_type == "asset":
        learning_event_type = "project.asset.received"
        asset = row.input_snapshot.get("asset") if isinstance(row.input_snapshot, dict) else {}
        asset_name = asset.get("file_name") if isinstance(asset, dict) else None
        fallback_summary = f"项目运行资产上传：{asset_name or getattr(project, 'name', None) or getattr(run, 'project_id', None) or row.project_run_id}"
    else:
        learning_event_type = "project.ingress.received"
        fallback_summary = f"项目输出回传：{getattr(project, 'name', None) or getattr(run, 'project_id', None) or row.project_run_id}"
    summary = output.get("summary") or (reports[0].get("summary") if reports and isinstance(reports[0], dict) else None)
    event = await _upsert_event(
        db,
        event_type=learning_event_type,
        source_type="project_ingress_event",
        source_id=str(row.id),
        source_hash=_sha256(
            {
                "request_id": row.request_id,
                "event_type": row.event_type,
                "input": row.input_snapshot,
                "output": row.output_result,
                "reports": reports,
                "todos": todos,
                "proofs": proofs,
            }
        ),
        department=getattr(project, "department", None) or getattr(run, "department", None),
        org_unit_id=getattr(project, "department_id", None) or getattr(run, "department_id", None),
        skill_id=f"project:{project.id}" if project else None,
        user_id=getattr(run, "user_id", None),
        run_id=getattr(run, "execution_run_id", None) or getattr(run, "id", None),
        payload_ref=f"project_ingress_events:{row.id}",
        redacted_summary=_clip(summary or fallback_summary, 1000),
        sensitivity_level="restricted" if proofs else "internal",
        quality_score=0.76 if reports else 0.68,
        metadata={
            "project_id": getattr(project, "id", None),
            "project_run_id": getattr(run, "id", None) or row.project_run_id,
            "request_id": row.request_id,
            "event_type": row.event_type,
            "asset_id": row_metadata.get("asset_id") or output.get("asset_id"),
            "gateway_status": row_metadata.get("status"),
            "gateway_reason": row_metadata.get("payload_reason") or row_metadata.get("reason"),
            "gateway_error": output.get("error"),
            "report_count": len(reports),
            "todo_count": len(todos),
            "proof_count": len(proofs),
            "output_keys": sorted(str(key) for key in output.keys())[:50],
        },
    )
    project_id = getattr(project, "id", None) or getattr(run, "project_id", None) or row.project_run_id
    project_name = getattr(project, "name", None) or project_id
    if event_type != "input":
        for idx, item in enumerate(reports[:20]):
            card = _safe_dict(item)
            title = str(card.get("title") or f"项目报告 {project_name}-{idx + 1}")
            card_summary = _clip(card.get("summary") or card.get("text") or summary or title, 4000)
            await _upsert_artifact(
                db,
                event,
                artifact_kind="report_summary",
                title=title,
                summary=card_summary,
                content={
                    "text": card_summary,
                    "detail": card.get("detail") or card.get("content") or output.get("detail"),
                    "metrics": card.get("metrics") or output.get("metrics") or [],
                    "source": {
                        "source_type": "project_report",
                        "project_id": project_id,
                        "project_run_id": getattr(run, "id", None) or row.project_run_id,
                        "project_ingress_event_id": row.id,
                        "request_id": row.request_id,
                    },
                    "source_type": "project_report",
                    "proof_count": len(proofs),
                },
                labels=["project", "report", *[str(tag) for tag in _safe_list(card.get("tags"))[:10]]],
                target_type="project",
                target_id=str(project_id)[:120] if project_id else None,
                quality_score=0.82 if not proofs else 0.78,
                confidence=0.8,
                sensitivity_level="restricted" if proofs else event.sensitivity_level,
            )
        if output or todos:
            await _upsert_artifact(
                db,
                event,
                artifact_kind="training_sample",
                title=f"项目 {project_name} 输入输出样本",
                summary=_clip(summary or f"项目 {project_name} 完成一次输入、能力调用与输出回传。", 2000),
                content={
                    "input": row.input_snapshot if isinstance(row.input_snapshot, dict) else {},
                    "output": output,
                    "reports": [
                        {"title": _safe_dict(card).get("title"), "summary": _safe_dict(card).get("summary")}
                        for card in reports[:20]
                    ],
                    "todos": [
                        {"title": _safe_dict(todo).get("title"), "priority": _safe_dict(todo).get("priority")}
                        for todo in todos[:50]
                    ],
                    "suggested_action": output.get("suggested_action") or {"todos": len(todos)},
                    "source": {
                        "source_type": "project_ingress",
                        "project_id": project_id,
                        "project_run_id": getattr(run, "id", None) or row.project_run_id,
                        "project_ingress_event_id": row.id,
                        "request_id": row.request_id,
                    },
                    "proof_count": len(proofs),
                },
                labels=["project", "training", "input_output", "action_outcome"],
                target_type="project",
                target_id=str(project_id)[:120] if project_id else None,
                quality_score=0.76 + (0.04 if todos else 0),
                confidence=0.74,
                sensitivity_level="restricted" if proofs else event.sensitivity_level,
            )
    failed_gateway = row_metadata.get("status") == "failed" or output.get("status") == "failed" or bool(output.get("error"))
    if failed_gateway:
        await _upsert_artifact(
            db,
            event,
            artifact_kind="ai_system_gap_candidate",
            title=f"修复项目回传链路：{project_name}",
            summary=_clip(
                output.get("error")
                or row_metadata.get("payload_reason")
                or row_metadata.get("reason")
                or "项目输入/输出回传失败，需要检查 payload、manifest、网关与前端 SDK。",
                2000,
            ),
            content={
                "target_type": "project",
                "target_id": project_id,
                "project_id": project_id,
                "project_run_id": getattr(run, "id", None) or row.project_run_id,
                "project_ingress_event_id": row.id,
                "request_id": row.request_id,
                "event_type": row.event_type,
                "gateway_status": row_metadata.get("status"),
                "gateway_reason": row_metadata.get("payload_reason") or row_metadata.get("reason"),
                "error": output.get("error"),
                "proposal": "补齐项目包 manifest、前端 SDK payload 限制提示、幂等 request_id 和失败重试防护。",
                "risk_level": "R1",
                "expected_impact": {"project_gateway": "降低项目运行失败率", "ai_loop": "保留失败样本用于自我修复"},
            },
            labels=["candidate", "project", "gateway_failure"],
            target_type="project",
            target_id=str(project_id)[:120] if project_id else None,
            quality_score=0.78,
            confidence=0.72,
        )
    return event


async def capture_project_capability_call(db: AsyncSession, row: Any) -> LearningEvent:
    from app.projects.models import Project, ProjectRun

    run = await db.get(ProjectRun, row.project_run_id)
    project = await db.get(Project, run.project_id) if run else None
    ok = str(getattr(row, "status", "")) == "completed"
    event = await _upsert_event(
        db,
        event_type="project.capability.completed" if ok else "project.capability.failed",
        source_type="project_capability_call",
        source_id=str(row.id),
        source_hash=_sha256(
            {
                "capability": row.capability_key,
                "status": row.status,
                "input": row.input_summary,
                "output": row.output_summary,
                "error": row.error,
            }
        ),
        department=getattr(project, "department", None),
        org_unit_id=getattr(project, "department_id", None),
        skill_id=f"project:{project.id}" if project else None,
        user_id=getattr(run, "user_id", None),
        run_id=getattr(run, "execution_run_id", None) or getattr(run, "id", None),
        payload_ref=f"project_capability_calls:{row.id}",
        redacted_summary=_clip(f"{row.capability_key} · {'成功' if ok else row.error or '失败'}", 1000),
        sensitivity_level="restricted" if str(row.capability_type or "").startswith(("data", "mcp")) else "internal",
        quality_score=0.68 if ok else 0.74,
        metadata={
            "project_id": getattr(project, "id", None),
            "project_run_id": getattr(run, "id", None),
            "capability": row.capability_key,
            "capability_type": row.capability_type,
            "status": row.status,
        },
    )
    if not ok:
        await _upsert_artifact(
            db,
            event,
            artifact_kind="ai_system_gap_candidate",
            title=f"修复项目能力调用：{row.capability_key}",
            summary=f"项目能力调用失败：{row.error or row.status}。建议补齐项目 manifest 声明、参数校验或网关提示。",
            content={
                "target_type": "project_capability",
                "target_id": row.capability_key,
                "project_id": getattr(project, "id", None),
                "project_run_id": getattr(run, "id", None),
                "error": row.error,
            },
            labels=["candidate", "project", "capability_failure"],
            target_type="project_capability",
            target_id=row.capability_key,
            quality_score=0.74,
            confidence=0.68,
        )
    else:
        await _upsert_artifact(
            db,
            event,
            artifact_kind="agent_memory",
            title=f"项目能力调用经验：{row.capability_key}",
            summary=_clip(f"项目 {getattr(project, 'name', None) or getattr(run, 'project_id', None)} 成功调用 {row.capability_key}。", 1000),
            content={
                "capability": row.capability_key,
                "capability_type": row.capability_type,
                "status": row.status,
                "input_summary": row.input_summary if isinstance(row.input_summary, dict) else {},
                "output_summary": row.output_summary if isinstance(row.output_summary, dict) else {},
                "latency_ms": getattr(row, "latency_ms", None),
                "source": {
                    "source_type": "project_capability_call",
                    "project_id": getattr(project, "id", None),
                    "project_run_id": getattr(run, "id", None),
                    "project_capability_call_id": row.id,
                    "request_id": getattr(row, "request_id", None),
                },
            },
            labels=["project", "capability", "success", str(row.capability_type or "unknown")],
            target_type="project_capability",
            target_id=str(row.capability_key)[:120],
            quality_score=0.7,
            confidence=0.7,
            sensitivity_level=event.sensitivity_level,
        )
    if str(getattr(row, "capability_type", "") or "").startswith("ai"):
        await _upsert_artifact(
            db,
            event,
            artifact_kind="training_sample",
            title=f"项目 AI 能力调用样本：{row.capability_key}",
            summary=_clip(
                f"项目 {getattr(project, 'name', None) or getattr(run, 'project_id', None) or '-'} "
                f"调用 {row.capability_key}，状态={row.status}。",
                2000,
            ),
            content={
                "input": _redacted_payload(getattr(row, "input_payload", None), 12000),
                "output": _redacted_payload(getattr(row, "output_result", None), 12000),
                "input_summary": _safe_dict(getattr(row, "input_summary", None)),
                "output_summary": _safe_dict(getattr(row, "output_summary", None)),
                "cost": _safe_dict(getattr(row, "cost_json", None)),
                "source": {
                    "source_type": "project_capability_call",
                    "project_id": getattr(project, "id", None),
                    "project_run_id": getattr(run, "id", None) or row.project_run_id,
                    "project_capability_call_id": row.id,
                    "request_id": getattr(row, "request_id", None),
                    "capability": row.capability_key,
                    "capability_type": row.capability_type,
                    "status": row.status,
                },
                "suggested_action": {"use_for": "model_call_replay_or_sft", "status": row.status},
            },
            labels=["project", "training", "ai_capability", "model_call", str(row.status or "")],
            target_type="project",
            target_id=str(getattr(project, "id", "") or "")[:120] or None,
            quality_score=0.72 if ok else 0.66,
            confidence=0.7,
            sensitivity_level="restricted",
        )
    return event


def _knowledge_context_sources(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize logged retrieval context into document references.

    Search / ask / context APIs historically log compact ``document_ids`` while
    some callers pass richer ``sources`` objects. The learning loop should not
    miss lineage just because the query log used the compact form.
    """
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(doc_id: Any, *, score: Any = None, title: Any = None, modality: Any = None) -> None:
        text = str(doc_id or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        sources.append({"document_id": text, "score": score, "title": title, "modality": modality})

    for item in _safe_list(context.get("sources")):
        if isinstance(item, dict):
            add(item.get("document_id") or item.get("id"), score=item.get("score"), title=item.get("title") or item.get("document_title"), modality=item.get("modality"))
        else:
            add(item)
    for item in _safe_list(context.get("items")):
        if isinstance(item, dict):
            add(item.get("document_id") or item.get("id"), score=item.get("score"), title=item.get("title") or item.get("document_title"), modality=item.get("modality"))
    for doc_id in _safe_list(context.get("document_ids")):
        add(doc_id, modality=context.get("modality"))
    return sources


def _agent_state_document_sources(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract knowledge document references from an Agent runtime state.

    Agent implementations evolve quickly; this accepts both compact
    ``document_ids`` and richer source/citation objects in a few common nesting
    locations while returning only document ids / titles / scores.
    """

    sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_many(context: Any) -> None:
        if not isinstance(context, dict):
            return
        for item in _knowledge_context_sources(context):
            doc_id = str(item.get("document_id") or "").strip()
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                sources.append(item)

    add_many(state)
    for key in (
        "knowledge_context",
        "rag_context",
        "retrieval_context",
        "context",
        "citations",
        "references",
        "sources",
    ):
        value = state.get(key)
        if isinstance(value, dict):
            add_many(value)
        elif isinstance(value, list):
            add_many({"sources": value})
    return sources


def _agent_state_skill_ids(state: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text[:120])

    for key in ("skill_id", "target_skill_id", "base_skill_id"):
        add(state.get(key))
    skill = _safe_dict(state.get("skill"))
    meta = _safe_dict(skill.get("meta"))
    add(skill.get("id") or skill.get("skill_id") or meta.get("id") or meta.get("skill_id") or meta.get("name"))
    contract = _safe_dict(state.get("contract"))
    add(contract.get("skill_id") or contract.get("target_skill_id"))
    return result


def _agent_state_model_deployment_ids(state: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text[:120])

    for key in ("model_deployment_id", "deployment_id", "active_model_deployment_id"):
        add(state.get(key))
    for key in ("model_context", "deployment", "active_model_deployment"):
        value = state.get(key)
        if isinstance(value, dict):
            add(value.get("deployment_id") or value.get("id") or value.get("model_deployment_id"))
    return result


def _agent_state_tool_names(state: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text[:120])

    for key in ("tools", "used_tools"):
        for item in _safe_list(state.get(key)):
            if isinstance(item, dict):
                add(item.get("name") or item.get("tool") or item.get("id"))
            else:
                add(item)
    for item in _safe_list(state.get("tool_calls")):
        if isinstance(item, dict):
            add(item.get("name") or item.get("tool") or item.get("server"))
        else:
            add(item)
    return result


def _agent_skill_candidate_payload(state: dict[str, Any], message: str) -> tuple[str, str | None, str, dict[str, Any]] | None:
    contract = _safe_dict(state.get("contract"))
    skill = _safe_dict(state.get("skill"))
    if not contract and not skill:
        return None
    meta = _safe_dict(skill.get("meta"))
    risks = _safe_dict(contract.get("risks"))
    trigger = _safe_dict(contract.get("trigger"))
    output = _safe_dict(contract.get("output"))
    goal = str(contract.get("goal") or skill.get("goal") or message or "Agent 生成的 Skill 候选").strip()
    target_id = str(skill.get("id") or skill.get("skill_id") or meta.get("name") or meta.get("id") or "").strip() or None
    title = f"Agent 生成 Skill 候选：{_clip(goal, 80)}"
    proposal = "将本次 Agent 生成的任务合同和 Skill 草案纳入 Skill Git / Review 链路，补充测试后再发布。"
    payload = {
        "target_type": "skill",
        "target_id": target_id,
        "proposal": proposal,
        "risk_level": risks.get("level") or meta.get("risk_level") or "R2",
        "expected_impact": {
            "automation_reuse": "把高频自然语言需求固化为可审核 Skill",
            "source": "agent_thread",
        },
        "contract_summary": {
            "goal": _clip(goal, 500),
            "trigger_type": trigger.get("type"),
            "output_adapter": output.get("adapter"),
            "risk_level": risks.get("level"),
            "department": risks.get("department") or meta.get("department"),
            "permission_actions": [item.get("action") for item in _safe_list(contract.get("permissions")) if isinstance(item, dict)][:20],
        },
        "skill_summary": {
            "id": target_id,
            "name": meta.get("name"),
            "description": meta.get("description"),
            "has_generated_scripts": bool(_safe_dict(skill.get("scripts"))),
            "rule_count": len(_safe_list(skill.get("rules"))),
            "test_case_count": len(_safe_list(skill.get("test_cases"))),
        },
    }
    return title, target_id, proposal, payload


def _training_lineage_from_spec(row: TrainingJob) -> dict[str, Any]:
    spec = _safe_dict(row.spec_json)
    lineage = _safe_dict(spec.get("lineage"))
    dataset = _safe_dict(spec.get("dataset"))
    return {
        "source": lineage.get("source") or spec.get("source"),
        "skill_id": lineage.get("skill_id") or spec.get("source_skill_id") or row.target_skill_id,
        "run_id": lineage.get("run_id"),
        "learning_candidate_id": lineage.get("learning_candidate_id") or spec.get("learning_candidate_id"),
        "dataset_ref": dataset.get("ref") or row.dataset_ref,
        "artifact_ids": [
            str(item)
            for item in [
                *(_safe_list(lineage.get("learning_artifact_ids"))),
                *(_safe_list(lineage.get("artifact_ids"))),
                *(_safe_list(dataset.get("artifact_ids"))),
                *(_safe_list(dataset.get("learning_artifact_ids"))),
            ]
            if str(item or "").strip()
        ][:200],
    }


def _training_dataset_artifact_ids_for_job(row: TrainingJob) -> list[str]:
    ids: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in ids:
            ids.append(text[:120])

    lineage = _training_lineage_from_spec(row)
    for item in _safe_list(lineage.get("artifact_ids")):
        add(item)
    package = _training_dataset_package_control_summary(row)
    package_lineage = _safe_dict(package.get("lineage"))
    for key in ("learning_artifact_ids", "artifact_ids"):
        for item in _safe_list(package_lineage.get(key)):
            add(item)
    for item in _safe_list(package.get("sample_fingerprints")):
        if isinstance(item, dict):
            add(item.get("id") or item.get("artifact_id"))
    return ids[:1000]


def _training_metric_preview(value: Any, *, depth: int = 0) -> Any:
    if depth > 2:
        return _clip(value, 500)
    if isinstance(value, dict):
        preview: dict[str, Any] = {}
        for key, item in list(value.items())[:24]:
            clean_key = _clip(key, 120)
            if isinstance(item, (int, float, bool)) or item is None:
                preview[clean_key] = item
            elif isinstance(item, str):
                preview[clean_key] = _clip(item, 500)
            elif isinstance(item, dict):
                preview[clean_key] = _training_metric_preview(item, depth=depth + 1)
            elif isinstance(item, list):
                preview[clean_key] = {"count": len(item), "preview": _training_metric_preview(item[:3], depth=depth + 1)}
            else:
                preview[clean_key] = _clip(item, 500)
        return preview
    if isinstance(value, list):
        return [_training_metric_preview(item, depth=depth + 1) for item in value[:6]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clip(value, 500)


def _training_artifact_identity(item: Any, *, task_id: int, index: int) -> str:
    if isinstance(item, dict):
        for key in ("id", "artifact_id", "name", "filename", "sha256", "hash", "uri", "url", "path"):
            text = str(item.get(key) or "").strip()
            if text:
                return _clip(text, 180)
    return f"task-{task_id}:artifact-{index}"


async def _training_task_context(db: AsyncSession, job_id: str) -> dict[str, Any]:
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job_id)
            .order_by(TrainingJobTask.created_at.asc(), TrainingJobTask.id.asc())
            .limit(50)
        )
    ).scalars().all()
    task_items: list[dict[str, Any]] = []
    artifact_ids: list[str] = []
    metrics_keys: set[str] = set()
    latest_progress = 0.0
    for task in tasks:
        metrics_json = _safe_dict(task.metrics_json)
        gateway_result = _safe_dict(metrics_json.get("gateway_result"))
        metrics = _safe_dict(gateway_result.get("metrics"))
        raw_artifacts = _safe_list(gateway_result.get("artifacts"))
        task_artifact_ids = [
            _training_artifact_identity(item, task_id=int(task.id or 0), index=index)
            for index, item in enumerate(raw_artifacts[:50])
        ]
        artifact_ids.extend(task_artifact_ids)
        metrics_keys.update(str(key) for key in metrics.keys())
        latest_progress = max(latest_progress, float(task.progress or 0))
        task_items.append(
            {
                "task_id": task.id,
                "gateway_id": task.gateway_id,
                "worker_id": _clip(task.worker_id, 160) if task.worker_id else None,
                "status": task.status,
                "progress": round(float(task.progress or 0), 4),
                "metrics": _training_metric_preview(metrics),
                "metrics_keys": sorted(str(key) for key in metrics.keys())[:40],
                "artifact_ids": task_artifact_ids[:40],
                "artifact_count": len(raw_artifacts),
                "error": _clip(task.error_message, 1000) if task.error_message else None,
                "updated_at": _iso(task.updated_at),
            }
        )
    return {
        "task_count": len(tasks),
        "latest_progress": round(latest_progress, 4),
        "metrics_keys": sorted(metrics_keys)[:80],
        "artifact_ids": sorted(set(artifact_ids))[:200],
        "tasks": task_items,
    }


def _training_deployment_review_item(row: TrainingModelDeployment) -> dict[str, Any]:
    artifact_ref = _safe_dict(row.artifact_ref_json)
    item = {
        "deployment_id": row.id,
        "status": row.status,
        "model_family": row.model_family,
        "artifact_id": row.artifact_id,
        "artifact_sha256": str(artifact_ref.get("sha256") or "")[:64] or None,
        "target_skill_ids": [
            str(skill_id)[:80]
            for skill_id in _safe_list(row.target_skill_ids_json)
            if str(skill_id or "").strip()
        ][:20],
        "rollout_percent": row.rollout_percent,
        "request_reason": _clip(row.request_reason, 1000) if row.request_reason else None,
        "requested_by": row.requested_by,
        "approved_by": row.approved_by,
        "activated_at": _iso(row.activated_at),
        "eval_task_id": row.eval_task_id,
        "review_required": row.status == "awaiting_review",
        "auto_approval": bool(row.approved_by or row.activated_at),
    }
    return {key: value for key, value in item.items() if value not in (None, "", [], {})}


async def _training_deployment_review_context(db: AsyncSession, row: TrainingJob) -> dict[str, Any]:
    deployments = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.job_id == row.id)
            .where(TrainingModelDeployment.status == "awaiting_review")
            .order_by(TrainingModelDeployment.created_at.desc(), TrainingModelDeployment.id.desc())
            .limit(20)
        )
    ).scalars().all()
    if not deployments:
        return {}
    spec = _safe_dict(row.spec_json)
    governance = _safe_dict(spec.get("governance"))
    deployment_spec = _safe_dict(spec.get("deployment"))
    requests = [_training_deployment_review_item(item) for item in deployments]
    return {
        "requested": True,
        "request_count": len(requests),
        "latest": requests[0],
        "requests": requests,
        "governance": {
            "review_required": True,
            "deployment_review_required": bool(governance.get("deployment_review_required", True)),
            "no_auto_approval": bool(governance.get("no_auto_approval", True)),
            "auto_request_enabled": bool(
                deployment_spec.get("auto_request")
                or deployment_spec.get("auto_request_deployment")
                or spec.get("auto_deploy_request")
            ),
            "approval_state_machine": "training_model_deployment",
        },
    }


async def capture_knowledge_query(db: AsyncSession, row: KnowledgeQueryLog) -> LearningEvent:
    context = _safe_dict(row.context_json)
    event = await _upsert_event(
        db,
        event_type="knowledge.query.used",
        source_type="knowledge_query",
        source_id=str(row.id),
        source_hash=_sha256({"query": row.query, "count": row.result_count, "context": context}),
        department=row.department,
        org_unit_id=row.org_unit_id,
        user_id=row.user_id,
        payload_ref=f"knowledge_query:{row.id}",
        redacted_summary=_clip(row.query, 1000),
        sensitivity_level="internal",
        quality_score=0.55 + min(float(row.result_count or 0), 5) * 0.05,
        metadata={"mode": row.mode, "result_count": row.result_count, "backend": row.retrieval_backend},
    )
    sources = _knowledge_context_sources(context)
    for item in sources[:20]:
        doc_id = item.get("document_id")
        if doc_id:
            await _upsert_edge(
                db,
                from_type="knowledge_document",
                from_id=str(doc_id),
                to_type="knowledge_query",
                to_id=str(row.id),
                relation="used_as_context",
                department=row.department,
                org_unit_id=row.org_unit_id,
                metadata={
                    "score": item.get("score"),
                    "title": item.get("title"),
                    "modality": item.get("modality") or context.get("modality"),
                    "query": row.query[:200],
                },
            )
    if int(row.result_count or 0) == 0 and str(row.mode or "") in {"search", "ask", "context"}:
        await _upsert_artifact(
            db,
            event,
            artifact_kind="knowledge_review_candidate",
            title=f"补充知识缺口：{_clip(row.query, 80)}",
            summary="知识库检索未命中，需要补充部门 SOP、历史复盘、FAQ 或外部数据引用。",
            content={
                "target_type": "knowledge",
                "target_id": row.kb_id or row.department or row.org_unit_id,
                "proposal": "补齐该问题相关的部门知识，或标记为无需沉淀，避免 Agent/Skill 后续低置信度回答。",
                "risk_level": "R1",
                "expected_impact": {"knowledge_coverage": "提升知识召回覆盖率", "query": row.query[:200]},
                "query": row.query,
                "mode": row.mode,
                "source_type": "knowledge",
            },
            labels=["candidate", "knowledge_gap", str(row.mode or "search")],
            target_type="knowledge",
            target_id=row.kb_id or row.department or row.org_unit_id,
            quality_score=0.68,
            confidence=0.62,
        )
    return event


async def capture_training_job(db: AsyncSession, row: TrainingJob) -> LearningEvent:
    event_type = "training.job.completed" if row.status == "completed" else "training.job.updated"
    lineage = _training_lineage_from_spec(row)
    task_context = await _training_task_context(db, row.id)
    agent_context = await _training_agent_context(db, row)
    dataset_package = _training_dataset_package_control_summary(row)
    deployment_review = await _training_deployment_review_context(db, row)
    event = await _upsert_event(
        db,
        event_type=event_type,
        source_type="training_job",
        source_id=row.id,
        source_hash=_sha256(
            {
                "status": row.status,
                "spec": row.spec_json,
                "dataset_ref": row.dataset_ref,
                "tasks": [
                    {
                        "task_id": item.get("task_id"),
                        "status": item.get("status"),
                        "progress": item.get("progress"),
                        "metrics_keys": item.get("metrics_keys"),
                        "artifact_ids": item.get("artifact_ids"),
                        "updated_at": item.get("updated_at"),
                    }
                    for item in _safe_list(task_context.get("tasks"))
                ],
                "dataset_package_manifest_hash": dataset_package.get("manifest_hash"),
                "dataset_package_sample_count": dataset_package.get("sample_count"),
            }
        ),
        department=row.department,
        skill_id=row.target_skill_id,
        user_id=row.created_by,
        payload_ref=f"training_job:{row.id}",
        redacted_summary=_clip(f"训练任务 {row.title} · {row.status}", 1000),
        sensitivity_level="internal",
        quality_score=0.72 if row.status == "completed" else 0.58,
        metadata={
            "job_type": row.job_type,
            "dataset_ref": row.dataset_ref,
            "status": row.status,
            "task_count": task_context.get("task_count"),
            "latest_progress": task_context.get("latest_progress"),
            "metrics_keys": task_context.get("metrics_keys"),
            "artifact_count": len(_safe_list(task_context.get("artifact_ids"))),
            "training_agent": agent_context or None,
            "dataset_package": dataset_package or None,
            "deployment_review": deployment_review or None,
        },
    )
    if agent_context.get("agent_id"):
        await _upsert_edge(
            db,
            from_type="agent",
            from_id=str(agent_context["agent_id"]),
            to_type="training_job",
            to_id=row.id,
            relation="controlled_training",
            department=row.department,
            skill_id=row.target_skill_id or lineage.get("skill_id"),
            metadata={
                "event_id": event.id,
                "gateway_kind": agent_context.get("gateway_kind"),
                "agent_purpose": agent_context.get("agent_purpose"),
                "contract": agent_context.get("contract"),
                "dataset_package": dataset_package or None,
            },
        )
    if lineage.get("learning_candidate_id"):
        await _upsert_edge(
            db,
            from_type="improvement_candidate",
            from_id=str(lineage["learning_candidate_id"]),
            to_type="training_job",
            to_id=row.id,
            relation="trained_from",
            department=row.department,
            skill_id=row.target_skill_id or lineage.get("skill_id"),
            metadata={"event_id": event.id, "dataset_ref": lineage.get("dataset_ref")},
        )
    if lineage.get("run_id"):
        await _upsert_edge(
            db,
            from_type="run",
            from_id=str(lineage["run_id"]),
            to_type="training_job",
            to_id=row.id,
            relation="trained_from",
            department=row.department,
            skill_id=row.target_skill_id or lineage.get("skill_id"),
            run_id=str(lineage["run_id"]),
            metadata={"event_id": event.id, "dataset_ref": lineage.get("dataset_ref")},
        )
    if lineage.get("skill_id"):
        await _upsert_edge(
            db,
            from_type="skill",
            from_id=str(lineage["skill_id"]),
            to_type="training_job",
            to_id=row.id,
            relation="trained_from",
            department=row.department,
            skill_id=str(lineage["skill_id"]),
            metadata={"event_id": event.id, "dataset_ref": lineage.get("dataset_ref")},
        )
    for artifact_id in lineage.get("artifact_ids") or []:
        await _upsert_edge(
            db,
            from_type="learning_artifact",
            from_id=str(artifact_id),
            to_type="training_job",
            to_id=row.id,
            relation="trained_from",
            department=row.department,
            skill_id=row.target_skill_id or lineage.get("skill_id"),
            metadata={"event_id": event.id, "dataset_ref": lineage.get("dataset_ref")},
        )
    for artifact_id in task_context.get("artifact_ids") or []:
        await _upsert_edge(
            db,
            from_type="training_job",
            from_id=row.id,
            to_type="model_artifact",
            to_id=str(artifact_id),
            relation="produced_artifact",
            department=row.department,
            skill_id=row.target_skill_id or lineage.get("skill_id"),
            metadata={"event_id": event.id, "dataset_ref": lineage.get("dataset_ref"), "job_status": row.status},
        )
    for request in deployment_review.get("requests") or []:
        deployment_id = str(request.get("deployment_id") or "").strip()
        if not deployment_id:
            continue
        await _upsert_edge(
            db,
            from_type="training_job",
            from_id=row.id,
            to_type="model_deployment",
            to_id=deployment_id,
            relation="requested_deployment_review",
            department=row.department,
            skill_id=row.target_skill_id or lineage.get("skill_id"),
            metadata={
                "event_id": event.id,
                "status": request.get("status"),
                "model_family": request.get("model_family"),
                "artifact_id": request.get("artifact_id"),
                "artifact_sha256": request.get("artifact_sha256"),
                "target_skill_ids": request.get("target_skill_ids"),
                "rollout_percent": request.get("rollout_percent"),
                "review_required": request.get("review_required"),
                "auto_approval": request.get("auto_approval", False),
                "governance": deployment_review.get("governance"),
            },
        )
    if row.status == "completed":
        await _upsert_artifact(
            db,
            event,
            artifact_kind="knowledge_note",
            title=f"训练完成：{row.title}",
            summary=f"训练任务完成，目标 Skill={row.target_skill_id or '-'}，数据集={row.dataset_ref or '-'}。",
            content={
                "text": row.objective or row.title,
                "source_type": "training",
                "source": {
                    "training_job_id": row.id,
                    "dataset_ref": row.dataset_ref,
                    "target_skill_id": row.target_skill_id,
                    "metrics_keys": task_context.get("metrics_keys"),
                    "artifact_ids": task_context.get("artifact_ids"),
                    "training_agent": agent_context or None,
                },
                "tasks": task_context.get("tasks"),
            },
            labels=["training", "knowledge"],
            target_type="knowledge",
            target_id=row.target_skill_id,
            quality_score=0.7,
            confidence=0.68,
        )
    if row.status == "failed":
        await _upsert_artifact(
            db,
            event,
            artifact_kind="training_improvement_candidate",
            title=f"修复训练任务失败：{row.title}",
            summary=row.objective or "训练任务失败，需要复盘数据集、网关、评估门禁或资源配置。",
            content={
                "target_type": "training",
                "target_id": row.id,
                "proposal": "复盘该训练任务的样本来源、网关日志、评估门禁和资源配置，必要时生成修复待办。",
                "risk_level": row.risk_level or "R2",
                "expected_impact": {"training_success_rate": "提升训练任务通过率", "dataset_ref": row.dataset_ref},
                "tasks": task_context.get("tasks"),
            },
            labels=["candidate", "training", "failure"],
            target_type="training",
            target_id=row.id,
            quality_score=0.76,
            confidence=0.68,
        )
    return event


def _model_deployment_runtime_status_payload(runtime_status: dict[str, Any] | None) -> dict[str, Any]:
    data = _safe_dict(runtime_status)
    if not data:
        return {}
    model_status = _safe_dict(data.get("model_status"))
    schedule_refresh = _safe_dict(data.get("schedule_refresh"))
    schedule_targets = []
    for item in _safe_list(schedule_refresh.get("targets"))[:50]:
        if not isinstance(item, dict):
            continue
        schedule_targets.append({
            key: item.get(key)
            for key in ("instance_id", "ok", "accepted", "config_version", "error")
            if item.get(key) not in (None, "", [], {})
        })
    payload = {
        "artifact_uri_present": data.get("artifact_uri_present"),
        "target_gateway_id": _clip(data.get("target_gateway_id"), 80) if data.get("target_gateway_id") else None,
        "target_gateway_kind": _clip(data.get("target_gateway_kind"), 50) if data.get("target_gateway_kind") else None,
        "model_profile": _clip(data.get("model_profile"), 120) if data.get("model_profile") else None,
        "model_ready": data.get("model_ready"),
        "inference_ready": data.get("inference_ready"),
        "inference_disabled_reason": (
            _clip(data.get("inference_disabled_reason"), 120)
            if data.get("inference_disabled_reason")
            else None
        ),
        "model_status": {
            key: model_status.get(key)
            for key in ("status", "profile", "exists", "bridge_instance_id")
            if model_status.get(key) not in (None, "", [], {})
        },
        "schedule_refresh": {
            "attempted": schedule_refresh.get("attempted"),
            "target_count": schedule_refresh.get("target_count"),
            "succeeded_count": schedule_refresh.get("succeeded_count"),
            "failed_count": schedule_refresh.get("failed_count"),
            "targets": schedule_targets,
            "raw_payload_returned": False,
        } if schedule_refresh else None,
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


async def capture_model_deployment(
    db: AsyncSession,
    row: TrainingModelDeployment,
    *,
    runtime_status: dict[str, Any] | None = None,
) -> LearningEvent:
    runtime_payload = _model_deployment_runtime_status_payload(runtime_status)
    event = await _upsert_event(
        db,
        event_type="model.deployed" if row.status in {"canary", "active"} else "model.deployment.updated",
        source_type="model_deployment",
        source_id=row.id,
        source_hash=_sha256(
            {
                "status": row.status,
                "artifact_id": row.artifact_id,
                "artifact": row.artifact_ref_json,
                "rollout": row.rollout_percent,
                "rollback_to": row.rollback_to,
                "reject_reason": row.reject_reason,
                "runtime_status": runtime_payload,
            }
        ),
        department=row.department,
        skill_id=(row.target_skill_ids_json or [None])[0] if isinstance(row.target_skill_ids_json, list) and row.target_skill_ids_json else None,
        user_id=row.requested_by,
        payload_ref=f"model_deployment:{row.id}",
        redacted_summary=_clip(f"模型部署 {row.model_family} · {row.status}", 1000),
        sensitivity_level="internal",
        quality_score=0.75 if row.status in {"canary", "active"} else 0.58,
        metadata={
            "model_family": row.model_family,
            "status": row.status,
            "rollout_percent": row.rollout_percent,
            **({"runtime_status": runtime_payload} if runtime_payload else {}),
        },
    )
    await _upsert_edge(
        db,
        from_type="training_job",
        from_id=row.job_id,
        to_type="model_deployment",
        to_id=row.id,
        relation="deployed_to",
        department=row.department,
        skill_id=(row.target_skill_ids_json or [None])[0] if isinstance(row.target_skill_ids_json, list) and row.target_skill_ids_json else None,
        metadata={
            "event_id": event.id,
            "status": row.status,
            "model_family": row.model_family,
            "artifact_id": row.artifact_id,
            "rollout_percent": row.rollout_percent,
            **({"runtime_status": runtime_payload} if runtime_payload else {}),
        },
    )
    if row.artifact_id:
        await _upsert_edge(
            db,
            from_type="model_artifact",
            from_id=row.artifact_id,
            to_type="model_deployment",
            to_id=row.id,
            relation="deployed_to",
            department=row.department,
            skill_id=(row.target_skill_ids_json or [None])[0] if isinstance(row.target_skill_ids_json, list) and row.target_skill_ids_json else None,
            metadata={
                "event_id": event.id,
                "training_job_id": row.job_id,
                "status": row.status,
                "model_family": row.model_family,
                "rollout_percent": row.rollout_percent,
                **({"runtime_status": runtime_payload} if runtime_payload else {}),
            },
        )
    if row.rollback_to:
        await _upsert_edge(
            db,
            from_type="model_deployment",
            from_id=row.id,
            to_type="model_deployment",
            to_id=row.rollback_to,
            relation="rollback_target",
            department=row.department,
            metadata={"event_id": event.id, "status": row.status, "model_family": row.model_family},
        )
    if row.status in {"canary", "active"}:
        for skill_id in row.target_skill_ids_json or []:
            await _upsert_edge(
                db,
                from_type="model_deployment",
                from_id=row.id,
                to_type="skill",
                to_id=str(skill_id),
                relation="deployed_to",
                department=row.department,
                skill_id=str(skill_id),
                metadata={
                    "event_id": event.id,
                    "status": row.status,
                    "model_family": row.model_family,
                    "rollout_percent": row.rollout_percent,
                    **({"runtime_status": runtime_payload} if runtime_payload else {}),
                },
            )
    await _upsert_artifact(
        db,
        event,
        artifact_kind="knowledge_note",
        title=f"模型部署状态：{row.model_family} · {row.status}",
        summary=f"模型部署 {row.id} 状态为 {row.status}，灰度比例 {row.rollout_percent}%，目标 Skill {', '.join(str(item) for item in (row.target_skill_ids_json or [])) or '-'}。",
        content={
            "text": f"模型部署 {row.model_family} 状态 {row.status}，灰度 {row.rollout_percent}%。",
            "source_type": "training",
            "source": {
                "deployment_id": row.id,
                "training_job_id": row.job_id,
                "target_skill_ids": row.target_skill_ids_json or [],
                "status": row.status,
                **({"runtime_status": runtime_payload} if runtime_payload else {}),
            },
        },
        labels=["training", "model_deployment", str(row.status or "updated")],
        target_type="knowledge",
        target_id=(row.target_skill_ids_json or [None])[0] if isinstance(row.target_skill_ids_json, list) and row.target_skill_ids_json else row.job_id,
        quality_score=0.7 if row.status in {"canary", "active"} else 0.62,
        confidence=0.66,
    )
    if row.status in {"rejected", "rolled_back"}:
        await _upsert_artifact(
            db,
            event,
            artifact_kind="training_improvement_candidate",
            title=f"复盘模型部署{ '回滚' if row.status == 'rolled_back' else '驳回' }：{row.model_family}",
            summary=row.reject_reason or row.request_reason or "模型部署未进入生产激活态，需要复盘评估、灰度指标和回滚原因。",
            content={
                "target_type": "training",
                "target_id": row.job_id,
                "proposal": "复盘该模型部署的评估门禁、灰度指标、驳回/回滚原因，并补充可回归评测样本。",
                "risk_level": "R2",
                "expected_impact": {"deployment_quality": "降低模型部署回滚或驳回率", "deployment_id": row.id},
            },
            labels=["candidate", "training", "deployment", str(row.status or "")],
            target_type="training",
            target_id=row.job_id,
            quality_score=0.78,
            confidence=0.7,
        )
    return event


def _decision_request_payload_signal(payload: Any) -> dict[str, Any]:
    """Project DecisionRequest payload to safe learning signals.

    Approval todos are valuable feedback for self-iteration, but their request
    payload may contain raw business objects. Keep only schema hints and small
    routing identifiers so sample/candidate payloads remain governed.
    """
    data = _safe_dict(payload)
    if not data:
        return {}
    signal: dict[str, Any] = {"keys": sorted(str(key) for key in data.keys())[:50]}
    for key in (
        "priority",
        "item_id",
        "itemId",
        "object_id",
        "objectId",
        "product_id",
        "productId",
        "sku_id",
        "skuId",
        "item_title",
        "object_title",
        "action",
        "type",
        "risk_level",
    ):
        value = data.get(key)
        if value is None or isinstance(value, (dict, list)):
            continue
        signal[key] = _clip(value, 240)
    return signal


async def _todo_decision_context(db: AsyncSession, request: DecisionRequest | None) -> dict[str, Any]:
    if request is None:
        return {}
    decision: DecisionLog | None = None
    if request.decision_log_id:
        decision = await db.get(DecisionLog, request.decision_log_id)
    if decision is None and request.run_id and request.skill_id:
        decision = (
            await db.execute(
                select(DecisionLog)
                .where(DecisionLog.run_id == request.run_id)
                .where(DecisionLog.skill_id == request.skill_id)
                .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
            )
        ).scalars().first()
    if decision is None:
        return {}
    output = decision.output_result if isinstance(decision.output_result, dict) else {}
    model_context = _decision_model_context_from_payload(output, model_id=decision.model_id)
    return {
        "decision_log_id": decision.id,
        "run_id": decision.run_id,
        "skill_id": decision.skill_id,
        "input": decision.input_snapshot or {},
        "output": output,
        "suggested_action": decision.suggested_action or {},
        "user_action": decision.user_action,
        "user_feedback": decision.user_feedback,
        "rating": decision.rating,
        "feedback_type": decision.feedback_type,
        "reject_reason": decision.reject_reason,
        "business_impact": decision.business_impact or {},
        **({"model_context": model_context} if model_context else {}),
    }


async def _link_todo_feedback_context(
    db: AsyncSession,
    event: LearningEvent,
    *,
    todo_id: int,
    request_id: str | None,
    decision_context: dict[str, Any],
    status: str,
    source_channel: str,
    assignee: str | None,
) -> None:
    decision_log_id = decision_context.get("decision_log_id")
    if not decision_log_id:
        return
    run_id = decision_context.get("run_id") or event.run_id
    metadata = {
        "event_id": event.id,
        "request_id": request_id,
        "status": status,
        "source_channel": source_channel,
        "assignee": assignee,
        "rating": decision_context.get("rating"),
        "feedback_type": decision_context.get("feedback_type"),
        "reject_reason": decision_context.get("reject_reason"),
    }
    clean_metadata = {key: value for key, value in metadata.items() if value not in (None, "", [], {})}
    await _upsert_edge(
        db,
        from_type="todo",
        from_id=str(todo_id),
        to_type="decision_log",
        to_id=str(decision_log_id),
        relation="feedback_for_decision",
        department=event.department,
        org_unit_id=event.org_unit_id,
        skill_id=event.skill_id or decision_context.get("skill_id"),
        run_id=run_id,
        metadata=clean_metadata,
    )
    model_context = _safe_dict(decision_context.get("model_context"))
    deployment_id = str(model_context.get("model_deployment_id") or "").strip()
    if not deployment_id:
        return
    await _upsert_edge(
        db,
        from_type="model_deployment",
        from_id=deployment_id,
        to_type="todo",
        to_id=str(todo_id),
        relation="feedback_on_model_decision",
        department=event.department,
        org_unit_id=event.org_unit_id,
        skill_id=event.skill_id or decision_context.get("skill_id"),
        run_id=run_id,
        metadata={key: value for key, value in {
            **clean_metadata,
            "decision_log_id": decision_log_id,
            "model_family": model_context.get("model_family"),
            "deployment_status": model_context.get("deployment_status"),
            "artifact_id": model_context.get("artifact_id"),
            "artifact_sha256": model_context.get("artifact_sha256"),
            "inference_status": model_context.get("inference_status"),
            "inference_gateway_id": model_context.get("inference_gateway_id"),
        }.items() if value not in (None, "", [], {})},
    )


async def _link_dingtalk_feedback_source(
    db: AsyncSession,
    event: LearningEvent,
    *,
    todo_id: int,
    request_id: str | None,
    status: str,
    structured_feedback: dict[str, Any],
) -> None:
    if not structured_feedback:
        return
    source_channel = str(structured_feedback.get("source_channel") or "").strip()
    fields = _safe_dict(structured_feedback.get("fields"))
    source = str(fields.get("source") or "").strip()
    if source_channel != "dingtalk" and source != "dingtalk_interactive_card":
        return
    feedback_ref = _sha256(
        {
            "todo_id": todo_id,
            "request_id": request_id,
            "decision": structured_feedback.get("decision"),
            "source_channel": source_channel,
            "fields": fields,
        }
    )
    metadata = {
        "event_id": event.id,
        "todo_id": todo_id,
        "request_id": request_id,
        "status": status,
        "source_channel": source_channel or "dingtalk",
        "source": source or "dingtalk_interactive_card",
        "action": fields.get("action"),
        "feedback_fields": sorted(str(key)[:80] for key in fields.keys())[:20],
        "feedback_sha256": feedback_ref,
        "rating": fields.get("rating"),
        "feedback_type": _clip(fields.get("feedback_type"), 120) if fields.get("feedback_type") else None,
        "reject_reason": _clip(fields.get("reject_reason"), 120) if fields.get("reject_reason") else None,
        "raw_text_returned": False,
    }
    await _upsert_edge(
        db,
        from_type="dingtalk_feedback",
        from_id=feedback_ref,
        to_type="todo",
        to_id=str(todo_id),
        relation="submitted_feedback",
        department=event.department,
        org_unit_id=event.org_unit_id,
        skill_id=event.skill_id,
        run_id=event.run_id,
        metadata={key: value for key, value in metadata.items() if value not in (None, "", [], {})},
    )


async def _link_dingtalk_dispatch_ack_source(
    db: AsyncSession,
    event: LearningEvent,
    *,
    task: TodoDispatchTask,
    request: DecisionRequest | None,
) -> None:
    source_channel = str(task.ack_channel or "").strip()
    if source_channel not in {"dingtalk", "dingtalk_open"}:
        return
    structured_feedback = _safe_dict((task.extra or {}).get("ack_feedback_payload"))
    structured_fields = _safe_dict(structured_feedback.get("fields"))
    source = str(structured_fields.get("source") or structured_feedback.get("source") or "").strip()
    feedback_fields = sorted(
        {
            "ack_note",
            "ack_channel",
            "executor",
            "status",
            *[str(key)[:80] for key in structured_fields.keys()],
            *[
                str(key)[:80]
                for key in structured_feedback.keys()
                if key not in {"fields", "note_text"}
            ],
        }
    )[:30]
    ack_ref = _sha256(
        {
            "dispatch_task_id": task.id,
            "request_id": task.request_id,
            "executor": task.executor,
            "ack_channel": source_channel,
            "ack_note": task.ack_note,
            "ack_at": _iso(task.ack_at),
            "structured_feedback": structured_feedback,
        }
    )
    note_text = str(task.ack_note or "")
    metadata = {
        "event_id": event.id,
        "dispatch_task_id": task.id,
        "request_id": task.request_id,
        "status": task.status,
        "source_channel": source_channel,
        "source": source or "dingtalk_dispatch_ack",
        "action": "dispatch_ack",
        "feedback_fields": feedback_fields,
        "feedback_sha256": ack_ref,
        "ack_note_sha256": _sha256(note_text) if note_text else None,
        "note_text_sha256": (
            _sha256(str(structured_feedback.get("note_text")))
            if structured_feedback.get("note_text")
            else None
        ),
        "rating": structured_fields.get("rating"),
        "feedback_type": (
            _clip(structured_fields.get("feedback_type"), 120)
            if structured_fields.get("feedback_type")
            else None
        ),
        "executor": task.executor,
        "raw_text_returned": False,
        "request_kind": request.kind if request else None,
    }
    await _upsert_edge(
        db,
        from_type="dingtalk_feedback",
        from_id=ack_ref,
        to_type="todo_dispatch_task",
        to_id=str(task.id),
        relation="submitted_feedback",
        department=event.department,
        org_unit_id=event.org_unit_id,
        skill_id=event.skill_id,
        run_id=event.run_id,
        metadata={key: value for key, value in metadata.items() if value not in (None, "", [], {})},
    )


async def capture_decision_request(db: AsyncSession, row: DecisionRequest) -> LearningEvent:
    dept, org_unit_id = await _skill_department(db, row.skill_id)
    event = await _upsert_event(
        db,
        event_type="inbox.report.created" if row.kind == "review" else "todo.created",
        source_type="decision_request",
        source_id=row.id,
        source_hash=_sha256({"title": row.title, "summary": row.summary, "payload": row.payload, "status": row.aggregate_status}),
        department=dept,
        org_unit_id=org_unit_id,
        skill_id=row.skill_id,
        run_id=row.run_id,
        payload_ref=f"decision_request:{row.id}",
        redacted_summary=_clip(row.summary or row.title, 1000),
        sensitivity_level="internal",
        quality_score=0.66,
        metadata={"kind": row.kind, "status": row.aggregate_status, "decision_log_id": row.decision_log_id},
    )
    if row.completed_at and row.aggregate_status in {"approved", "completed", "done"}:
        await _upsert_artifact(
            db,
            event,
            artifact_kind="knowledge_note",
            title=f"待办/审批完成：{row.title}",
            summary=row.summary or row.title,
            content={"text": row.summary or row.title, "source_type": "todo", "source": {"request_id": row.id}},
            labels=["todo", "completed"],
            target_type="knowledge",
            target_id=row.skill_id,
            quality_score=0.74,
            confidence=0.7,
        )
    return event


async def capture_ai_todo(db: AsyncSession, row: AITodo) -> LearningEvent:
    """Capture individual approval/dispatch todo decisions as feedback.

    ``DecisionRequest`` captures the aggregate request; this records the human
    judgement attached to each assignee. Rejections become eval cases and Skill
    improvement candidates, while approvals/rejections both enter the governed
    training sample pool.
    """
    request = await db.get(DecisionRequest, row.request_id)
    skill_id = request.skill_id if request else None
    dept, org_unit_id = await _skill_department(db, skill_id)
    status = str(row.status or "pending")
    event_type = "decision.feedback" if status in {"approved", "rejected"} else f"todo.{status}"
    payload_signal = _decision_request_payload_signal(request.payload if request else None)
    summary_bits = [
        request.title if request else f"Todo {row.id}",
        status,
        row.decision_reason,
    ]
    event = await _upsert_event(
        db,
        event_type=event_type,
        source_type="ai_todo",
        source_id=str(row.id),
        source_hash=_sha256({
            "request_id": row.request_id,
            "kind": row.kind,
            "status": row.status,
            "decided_at": _iso(row.decided_at),
            "reason": row.decision_reason,
            "payload_signal": payload_signal,
        }),
        department=dept,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        user_id=row.decided_by or row.assignee,
        run_id=request.run_id if request else None,
        payload_ref=f"ai_todo:{row.id}",
        redacted_summary=_clip(" · ".join(str(item) for item in summary_bits if item), 1000),
        sensitivity_level="internal",
        quality_score=0.86 if status in {"approved", "rejected"} else 0.58,
        metadata={
            "request_id": row.request_id,
            "kind": row.kind,
            "status": status,
            "decision_channel": row.decision_channel,
            "aggregate_status": request.aggregate_status if request else None,
            "request_kind": request.kind if request else None,
            "maintenance_decision": _is_maintenance_todo_decision(row),
        },
    )
    if _is_maintenance_todo_decision(row):
        event.policy_result_json = {
            **(event.policy_result_json or {}),
            "action": "capture_only",
            "reason": "maintenance_todo_decision",
        }
        return event
    if status in {"approved", "rejected"}:
        source_channel = str(row.decision_channel or "unknown")[:80]
        decision_context = await _todo_decision_context(db, request)
        structured_feedback = _safe_dict(getattr(row, "feedback_payload", None))
        feedback = {
            "status": status,
            "reason": _clip(row.decision_reason, 1000) if row.decision_reason else None,
            "channel": row.decision_channel,
            "source_channel": source_channel,
            "assignee": row.assignee,
            "structured": structured_feedback or None,
        }
        await _link_todo_feedback_context(
            db,
            event,
            todo_id=row.id,
            request_id=row.request_id,
            decision_context=decision_context,
            status=status,
            source_channel=source_channel,
            assignee=row.assignee,
        )
        await _link_dingtalk_feedback_source(
            db,
            event,
            todo_id=row.id,
            request_id=row.request_id,
            status=status,
            structured_feedback=structured_feedback,
        )
        artifact = await _upsert_artifact(
            db,
            event,
            artifact_kind="training_sample",
            title=f"{skill_id or 'Skill'} 审批反馈样本",
            summary=_clip(row.decision_reason or (request.summary if request else "") or status, 1000),
            content={
                "input": decision_context.get("input") or {},
                "output": decision_context.get("output") or {},
                "suggested_action": decision_context.get("suggested_action") or {},
                "feedback": feedback,
                "human_feedback_input": structured_feedback or {},
                "request": {
                    "title": _clip(request.title, 300) if request else None,
                    "summary": _clip(request.summary, 1000) if request else None,
                    "kind": request.kind if request else row.kind,
                    "payload_signal": payload_signal,
                },
                "decision": {
                    "decision_log_id": decision_context.get("decision_log_id"),
                    "user_action": decision_context.get("user_action"),
                    "user_feedback": decision_context.get("user_feedback"),
                    "rating": decision_context.get("rating"),
                    "feedback_type": decision_context.get("feedback_type"),
                    "reject_reason": decision_context.get("reject_reason"),
                    "business_impact": decision_context.get("business_impact") or {},
                },
                **({"model_context": decision_context.get("model_context")} if decision_context.get("model_context") else {}),
                "source": {
                    "todo_id": row.id,
                    "request_id": row.request_id,
                    "decision_log_id": decision_context.get("decision_log_id"),
                    "run_id": decision_context.get("run_id") or (request.run_id if request else None),
                    "skill_id": skill_id,
                    "source_type": "todo_feedback",
                    "source_channel": source_channel,
                },
                "source_channel": source_channel,
                "source_type": "todo_feedback",
            },
            labels=["training", "todo_feedback", status, f"channel:{source_channel}"],
            target_type="skill",
            target_id=skill_id,
            quality_score=0.88 if status == "rejected" else 0.78,
            confidence=0.78,
        )
        await _materialize_training_artifact(db, event, artifact)
    if status == "rejected":
        reason = row.decision_reason or (request.summary if request else None)
        artifact = await _upsert_artifact(
            db,
            event,
            artifact_kind="eval_case",
            title=f"{skill_id or 'Skill'} 审批驳回评测样本",
            summary=_clip(reason or "审批被驳回，需要作为回归评测案例。", 1000),
            content={
                "expected_signal": "avoid_rejected_plan",
                "rejection_reason": _clip(reason, 1000) if reason else None,
                "request": {
                    "title": _clip(request.title, 300) if request else None,
                    "kind": request.kind if request else row.kind,
                    "payload_signal": payload_signal,
                },
                "source": {
                    "todo_id": row.id,
                    "request_id": row.request_id,
                    "run_id": request.run_id if request else None,
                    "skill_id": skill_id,
                },
                "source_type": "todo_feedback",
            },
            labels=["eval", "todo_feedback", "rejected"],
            target_type="skill",
            target_id=skill_id,
            quality_score=0.9,
            confidence=0.82,
        )
        await _materialize_training_artifact(db, event, artifact)
        await _upsert_artifact(
            db,
            event,
            artifact_kind="skill_improvement_candidate",
            title=f"复盘待办驳回：{request.title if request else skill_id or row.id}",
            summary=_clip(reason or "审批人驳回了本次待办/动作建议，需要复盘 Skill 输出、审批条件或派发策略。", 2000),
            content={
                "target_type": "skill",
                "target_id": skill_id,
                "proposal": "把该待办驳回案例加入评测集，检查 Skill 输出依据、风险说明、审批条件和派发任务拆解。",
                "risk_level": "R2",
                "expected_impact": {"approval_quality": "降低同类待办驳回率", "todo_id": row.id},
            },
            labels=["candidate", "skill", "todo_rejected"],
            target_type="skill",
            target_id=skill_id,
            quality_score=0.82,
            confidence=0.76,
        )
    return event


async def capture_todo_dispatch_task(db: AsyncSession, row: TodoDispatchTask) -> LearningEvent:
    request = await db.get(DecisionRequest, row.request_id)
    skill_id = request.skill_id if request else None
    dept, org_unit_id = await _skill_department(db, skill_id)
    source_channel = str(row.ack_channel or "unknown")[:80]
    structured_feedback = _safe_dict((row.extra or {}).get("ack_feedback_payload"))
    structured_fields = _safe_dict(structured_feedback.get("fields"))
    request_payload = request.payload if request and isinstance(request.payload, dict) else {}
    payload_signal = _decision_request_payload_signal(request_payload)
    event = await _upsert_event(
        db,
        event_type="todo.completed" if row.status == "done" else "todo.updated",
        source_type="todo_dispatch_task",
        source_id=str(row.id),
        source_hash=_sha256({"content": row.content, "status": row.status, "ack": row.ack_note}),
        department=dept,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        user_id=row.executor,
        run_id=request.run_id if request else None,
        payload_ref=f"todo_dispatch_task:{row.id}",
        redacted_summary=_clip(row.ack_note or row.content, 1000),
        sensitivity_level="internal",
        quality_score=0.82 if row.status == "done" else 0.6,
        metadata={
            "request_id": row.request_id,
            "status": row.status,
            "ack_channel": row.ack_channel,
            "source_channel": source_channel,
            "request_kind": request.kind if request else None,
            "payload_signal": payload_signal,
            "feedback_fields": sorted(str(key)[:80] for key in structured_fields.keys())[:30],
        },
    )
    if row.status == "done":
        await _link_dingtalk_dispatch_ack_source(db, event, task=row, request=request)
        await _upsert_artifact(
            db,
            event,
            artifact_kind="knowledge_note",
            title="派发任务完成复盘",
            summary=row.ack_note or row.content,
            content={"text": row.ack_note or row.content, "source_type": "todo", "source": {"dispatch_task_id": row.id}},
            labels=["todo", "dispatch", "completed"],
            target_type="knowledge",
            target_id=skill_id,
            quality_score=0.78,
            confidence=0.72,
        )
        sample = await _upsert_artifact(
            db,
            event,
            artifact_kind="training_sample",
            title=f"{skill_id or 'Skill'} 派发执行反馈样本",
            summary=_clip(row.ack_note or row.content or "派发任务完成反馈", 1000),
            content={
                "input": {
                    "request": {
                        "title": _clip(request.title, 300) if request else None,
                        "summary": _clip(request.summary, 1000) if request else None,
                        "kind": request.kind if request else None,
                        "payload_signal": payload_signal,
                    },
                    "task": {
                        "content": _clip(row.content, 2000),
                        "deadline": _iso(row.deadline),
                    },
                },
                "output": {
                    "ack_note": _clip(row.ack_note, 1000),
                    "status": row.status,
                    "ack_channel": row.ack_channel,
                },
                "feedback": {
                    "status": row.status,
                    "source_channel": source_channel,
                    "executor": row.executor,
                    "ack_at": _iso(row.ack_at),
                    "structured": structured_feedback or None,
                },
                "human_feedback_input": structured_feedback or {},
                "source": {
                    "dispatch_task_id": row.id,
                    "request_id": row.request_id,
                    "run_id": request.run_id if request else None,
                    "skill_id": skill_id,
                    "source_type": "dispatch_ack",
                    "source_channel": source_channel,
                },
                "source_type": "dispatch_ack",
                "source_channel": source_channel,
            },
            labels=["training", "dispatch_ack", "todo_completed", f"channel:{source_channel}"],
            target_type="skill",
            target_id=skill_id,
            quality_score=0.84,
            confidence=0.76,
        )
        await _materialize_training_artifact(db, event, sample)
    return event


async def capture_agent_thread(
    db: AsyncSession,
    *,
    user: User,
    thread_id: str,
    message: str,
    mode: str,
    state: dict[str, Any] | None = None,
    status: str = "completed",
) -> LearningEvent:
    dept = _user_department(user)
    payload = {"thread_id": thread_id, "message": message, "mode": mode, "state": state or {}, "status": status}
    event = await _upsert_event(
        db,
        event_type="agent.thread.completed" if status == "completed" else "agent.thread.failed",
        source_type="agent_thread",
        source_id=thread_id,
        source_hash=_sha256(payload),
        department=dept,
        user_id=str(getattr(user, "id", "") or ""),
        payload_ref=f"agent_thread:{thread_id}",
        redacted_summary=_clip(message, 1000),
        sensitivity_level="internal",
        quality_score=0.68 if status == "completed" else 0.58,
        metadata={"mode": mode, "status": status},
    )
    clean_state = _safe_dict(state)
    for item in _agent_state_document_sources(clean_state)[:30]:
        doc_id = item.get("document_id")
        if not doc_id:
            continue
        await _upsert_edge(
            db,
            from_type="knowledge_document",
            from_id=str(doc_id),
            to_type="agent_thread",
            to_id=thread_id,
            relation="used_as_context",
            department=dept,
            metadata={
                "score": item.get("score"),
                "title": item.get("title"),
                "modality": item.get("modality"),
                "mode": mode,
                "event_id": event.id,
            },
        )
    for deployment_id in _agent_state_model_deployment_ids(clean_state)[:10]:
        await _upsert_edge(
            db,
            from_type="model_deployment",
            from_id=deployment_id,
            to_type="agent_thread",
            to_id=thread_id,
            relation="used_as_model",
            department=dept,
            metadata={"mode": mode, "event_id": event.id},
        )
    for skill_id in _agent_state_skill_ids(clean_state)[:10]:
        await _upsert_edge(
            db,
            from_type="skill",
            from_id=skill_id,
            to_type="agent_thread",
            to_id=thread_id,
            relation="used_skill",
            department=dept,
            skill_id=skill_id,
            metadata={"mode": mode, "event_id": event.id},
        )
    for tool_name in _agent_state_tool_names(clean_state)[:20]:
        await _upsert_edge(
            db,
            from_type="agent_tool",
            from_id=tool_name,
            to_type="agent_thread",
            to_id=thread_id,
            relation="called_tool",
            department=dept,
            metadata={"mode": mode, "event_id": event.id},
        )
    if status == "completed":
        await _upsert_artifact(
            db,
            event,
            artifact_kind="agent_memory",
            title="Agent 会话经验",
            summary=_clip(message, 1200),
            content={"text": message, "state_keys": sorted(clean_state.keys())[:40], "source_type": "agent"},
            labels=["agent", "memory"],
            target_type="agent",
            target_id=mode,
            quality_score=0.62,
            confidence=0.58,
        )
        candidate_payload = _agent_skill_candidate_payload(clean_state, message)
        if candidate_payload:
            title, target_id, proposal, payload = candidate_payload
            gate = _safe_dict(clean_state.get("gate"))
            await _upsert_artifact(
                db,
                event,
                artifact_kind="skill_improvement_candidate",
                title=title,
                summary=proposal,
                content=payload,
                labels=["candidate", "skill", "agent_generated"],
                target_type="skill",
                target_id=target_id,
                quality_score=0.82 if gate.get("can_publish") else 0.74,
                confidence=0.72,
            )
    else:
        await _upsert_artifact(
            db,
            event,
            artifact_kind="agent_policy_candidate",
            title="Agent 会话失败策略候选",
            summary="Agent 会话失败或中断，需要复盘工具选择、提示词和风险确认策略。",
            content={
                "target_type": "agent",
                "target_id": mode,
                "proposal": "检查本次 Agent 图执行失败节点，补充策略、知识或工具防呆。",
                "risk_level": "R1",
                "expected_impact": {"agent_success_rate": "提升会话完成率"},
            },
            labels=["candidate", "agent"],
            target_type="agent",
            target_id=mode,
            quality_score=0.72,
            confidence=0.65,
        )
    return event


def _agentization_target_id(value: Any) -> str:
    text = str(value or "learning_agent").strip().lower()
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in text)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.strip("_") or "learning_agent"
    return cleaned[:80]


def _agentization_fallback_decision(event: LearningEvent) -> dict[str, Any]:
    """Deterministic fallback when platform AI is not configured.

    The fallback keeps the pipeline useful in dev/test, while production can
    replace the judgement with ``call_llm`` from SystemConfig-backed AI.
    """
    metadata = _safe_dict(event.metadata_json)
    source_type = str(event.source_type or "")
    event_type = str(event.event_type or "")
    summary = event.redacted_summary or event.source_id
    if metadata.get("maintenance_decision") or (event.policy_result_json or {}).get("reason") == "maintenance_todo_decision":
        return {
            "should_create_agent": False,
            "confidence": 0.0,
            "agent_name": "学习流治理 Agent",
            "target_id": "maintenance_todo_decision",
            "goal": "维护性清理事件只进入审计，不生成 Agent 候选。",
            "trigger": "maintenance_event",
            "reason": "维护性 todo 决策不是业务反馈，不能用于 Agent 化、训练或改进候选。",
            "affected_modules": ["learning", "todos"],
            "permissions": ["learning:read"],
            "risks": ["避免把数据清理动作误判为业务驳回。"],
            "expected_impact": {"data_quality": "避免维护性事件污染学习闭环"},
            "workflow": [],
            "ai_used": False,
            "fallback": True,
        }
    should_create = False
    confidence = 0.0
    reason = "信号强度不足，先只保留学习事件。"
    agent_name = "学习流治理 Agent"
    goal = "持续观察学习流事件，识别需要人工治理或产品化的重复流程。"
    trigger = "manual_review"
    affected_modules = ["learning"]
    permissions = ["learning:read"]
    risks = ["只能生成候选，不自动发布、不自动执行真实外部副作用。"]

    if source_type == "sf_usage_aggregate" or event_type == "sf.usage.hotspot":
        tool = metadata.get("tool") or event.source_id.split(":", 1)[0]
        count = int(metadata.get("count") or 0)
        should_create = count >= 3
        confidence = 0.78 if should_create else 0.45
        agent_name = f"{tool} 高频流程 Agent"
        goal = f"把 {tool} 的重复调用、参数校验、报告沉淀和后续待办串成可审核 Agent 流程。"
        trigger = "manual_or_scheduled"
        affected_modules = ["sf", "mcp", "knowledge", "todos", "skills"]
        permissions = [f"mcp:{tool}:dry_run", "knowledge:read", "learning:write_candidate", "todo:create_review"]
        reason = f"{tool} 近期重复调用 {count} 次，适合由 Agent 编排上下文、工具调用和治理回流。"
    elif source_type == "ai_todo" and str(metadata.get("status") or "") == "rejected":
        should_create = True
        confidence = 0.74
        agent_name = "审批前自检 Agent"
        goal = "在待办进入审批前检查证据、风险说明、数据来源和动作拆解，减少同类驳回。"
        trigger = "before_todo_review"
        affected_modules = ["todos", "execution", "knowledge", "skills"]
        permissions = ["todo:read", "execution:read_trace", "knowledge:read", "learning:write_candidate"]
        reason = "审批驳回是明确的人类反馈，适合形成审批前自检 Agent 候选。"
    elif source_type == "knowledge_query" and int(metadata.get("result_count") or 0) == 0:
        should_create = True
        confidence = 0.68
        agent_name = "知识缺口补全 Agent"
        goal = "跟踪 0 命中问题，汇总来源证据并推动知识补充、过期检查和复用验证。"
        trigger = "knowledge_zero_hit"
        affected_modules = ["knowledge", "agent", "skills", "todos"]
        permissions = ["knowledge:read", "knowledge:create_candidate", "todo:create_review"]
        reason = "知识检索 0 命中会影响 Agent/Skill 回答依据，需要 Agent 负责补全闭环。"
    elif event_type in {"skill.run.failed", "agent.thread.failed"}:
        should_create = True
        confidence = 0.7
        agent_name = "失败复盘 Agent"
        goal = "自动收集失败上下文、知识引用、运行 trace 和候选修复建议，进入治理队列。"
        trigger = "failure_detected"
        affected_modules = ["execution", "agent", "knowledge", "skills", "training"]
        permissions = ["execution:read_trace", "knowledge:read", "learning:write_candidate"]
        reason = "失败事件会阻断正向循环，适合由 Agent 固化复盘流程。"
    elif source_type == "todo_dispatch_task" and event_type == "todo.completed":
        should_create = True
        confidence = 0.62
        agent_name = "任务复盘 Agent"
        goal = "把完成回执沉淀为知识、样本和下一步改进建议，并检查是否需要派生新待办。"
        trigger = "todo_completed"
        affected_modules = ["todos", "knowledge", "training", "learning"]
        permissions = ["todo:read", "knowledge:write_candidate", "learning:write_candidate"]
        reason = "完成回执是用户通过平台回推的业务结果，可由 Agent 统一复盘沉淀。"

    target_id = _agentization_target_id(f"{event.source_type}_{agent_name}")
    return {
        "should_create_agent": bool(should_create),
        "confidence": round(float(confidence), 4),
        "agent_name": agent_name,
        "target_id": target_id,
        "goal": goal,
        "trigger": trigger,
        "reason": reason,
        "affected_modules": affected_modules,
        "permissions": permissions,
        "risks": risks,
        "expected_impact": {
            "automation_reuse": "把重复判断和跨模块串联变成可审核 Agent",
            "quality": "减少漏沉淀、漏复盘和人工重复操作",
            "source_event_id": event.id,
        },
        "workflow": [
            "读取学习事件和来源血缘",
            "检索部门知识与历史反馈",
            "调用必要 MCP/Skill 做 dry-run 或诊断",
            "生成知识/训练/Skill/SF 候选",
            "进入待办或审核队列，等待人工确认",
        ],
        "ai_used": False,
        "fallback": True,
    }


async def _agentization_ai_decision(event: LearningEvent, fallback: dict[str, Any]) -> dict[str, Any] | None:
    try:
        await get_ai_config(require_system_config=True)
    except Exception:
        return None
    system = (
        "你是 SkillForge 智能闭环架构师。判断一个学习事件是否应该沉淀为可审核 Agent 候选。"
        "必须输出 JSON，不得输出原始敏感 payload。Agent 只能作为候选进入治理队列，不能自动发布、"
        "不能自动部署模型、不能执行真实外部副作用。"
    )
    user_payload = {
        "event": {
            "id": event.id,
            "event_type": event.event_type,
            "source_type": event.source_type,
            "source_id": event.source_id,
            "department": event.department,
            "skill_id": event.skill_id,
            "run_id": event.run_id,
            "summary": event.redacted_summary,
            "quality_score": event.quality_score,
            "metadata": event.metadata_json or {},
        },
        "fallback_judgement": fallback,
        "required_json_schema": {
            "should_create_agent": "boolean",
            "confidence": "0-1 number",
            "agent_name": "short string",
            "target_id": "stable snake_case id",
            "goal": "what this Agent automates",
            "trigger": "when it should run",
            "reason": "why or why not",
            "affected_modules": ["learning", "knowledge", "agent", "sf", "skills", "todos", "training"],
            "permissions": ["minimal scopes"],
            "risks": ["risk and guardrail"],
            "expected_impact": {"metric": "impact"},
            "workflow": ["steps in full flow"],
        },
    }
    try:
        result = await call_llm(
            system=system,
            user=_json_dumps(user_payload),
            json_mode=True,
            temperature=0.1,
            max_tokens=1800,
            timeout=20,
            call_source="learning.agentization_judgement",
            cost_context={"event_id": event.id, "source_type": event.source_type, "skill_id": event.skill_id},
            require_system_config=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("agentization AI judgement fallback event={} err={}", event.id, exc)
        return None
    if not isinstance(result, dict):
        return None
    confidence = result.get("confidence")
    try:
        result["confidence"] = round(max(0.0, min(1.0, float(confidence))), 4)
    except (TypeError, ValueError):
        result["confidence"] = fallback.get("confidence", 0.0)
    result["should_create_agent"] = bool(result.get("should_create_agent"))
    result["agent_name"] = _clip(result.get("agent_name") or fallback.get("agent_name"), 120)
    result["target_id"] = _agentization_target_id(result.get("target_id") or result.get("agent_name"))
    result["goal"] = _clip(result.get("goal") or fallback.get("goal"), 1000)
    result["trigger"] = _clip(result.get("trigger") or fallback.get("trigger"), 120)
    result["reason"] = _clip(result.get("reason") or fallback.get("reason"), 1200)
    result["affected_modules"] = [str(item)[:40] for item in _safe_list(result.get("affected_modules"))[:12]]
    result["permissions"] = [str(item)[:120] for item in _safe_list(result.get("permissions"))[:20]]
    result["risks"] = [_clip(item, 300) for item in _safe_list(result.get("risks"))[:12]]
    result["workflow"] = [_clip(item, 300) for item in _safe_list(result.get("workflow"))[:12]]
    result["expected_impact"] = _safe_dict(result.get("expected_impact")) or fallback.get("expected_impact") or {}
    result["ai_used"] = True
    result["fallback"] = False
    return result


async def judge_and_create_agentization_candidate(
    db: AsyncSession,
    event: LearningEvent,
    *,
    use_ai: bool = True,
) -> dict[str, Any]:
    existing = (
        await db.execute(
            select(LearningArtifact)
            .where(LearningArtifact.event_id == event.id)
            .where(LearningArtifact.artifact_kind == "agent_creation_candidate")
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        return {"created": False, "reason": "existing_candidate", "artifact": _artifact_to_dict(existing)}
    fallback = _agentization_fallback_decision(event)
    judgement = await _agentization_ai_decision(event, fallback) if use_ai else None
    decision = judgement or fallback
    if not bool(decision.get("should_create_agent")):
        return {"created": False, "reason": "not_agentizable", "judgement": decision}
    target_id = _agentization_target_id(decision.get("target_id") or decision.get("agent_name"))
    confidence = float(decision.get("confidence") or 0)
    impact = _safe_dict(decision.get("expected_impact"))
    content = {
        "target_type": "agent",
        "target_id": target_id,
        "proposal": decision.get("goal") or decision.get("reason") or "将该重复流程沉淀为 Agent 候选。",
        "risk_level": "R2",
        "expected_impact": {
            **impact,
            "affected_modules": _safe_list(decision.get("affected_modules")),
            "permissions": _safe_list(decision.get("permissions")),
            "risks": _safe_list(decision.get("risks")),
            "confidence": confidence,
            "ai_used": bool(decision.get("ai_used")),
        },
        "agent_blueprint": {
            "name": decision.get("agent_name"),
            "goal": decision.get("goal"),
            "trigger": decision.get("trigger"),
            "workflow": _safe_list(decision.get("workflow")),
            "permissions": _safe_list(decision.get("permissions")),
            "affected_modules": _safe_list(decision.get("affected_modules")),
            "guardrails": [
                "只创建候选，不自动发布 Agent",
                "只进入治理 / Review / Todo，不绕过 Skill Git 或模型部署审批",
                "真实 MCP / 钉钉 / 外部写操作仍需 dry-run/real/idempotency 规则",
                *(_safe_list(decision.get("risks"))[:8]),
            ],
        },
        "impact_analysis": {
            "affected_modules": _safe_list(decision.get("affected_modules")),
            "permissions": _safe_list(decision.get("permissions")),
            "risks": _safe_list(decision.get("risks")),
            "confidence": confidence,
            "ai_used": bool(decision.get("ai_used")),
            "reason": decision.get("reason"),
        },
        "source": {"learning_event_id": event.id, "source_type": event.source_type, "source_id": event.source_id},
        "source_type": "agentization_judgement",
    }
    artifact = await _upsert_artifact(
        db,
        event,
        artifact_kind="agent_creation_candidate",
        title=f"Agent 化候选：{_clip(decision.get('agent_name') or target_id, 100)}",
        summary=_clip(decision.get("reason") or decision.get("goal"), 2000),
        content=content,
        labels=["candidate", "agent", "agentization", "ai_judgement" if decision.get("ai_used") else "heuristic_judgement"],
        target_type="agent",
        target_id=target_id,
        quality_score=max(0.6, min(0.95, confidence or 0.7)),
        confidence=max(0.55, min(0.95, confidence or 0.65)),
    )
    return {
        "created": True,
        "artifact": _artifact_to_dict(artifact),
        "judgement": {**decision, "raw_payload_returned": False},
    }


async def run_agentization_judgement(
    db: AsyncSession,
    user: User,
    *,
    days: int = 7,
    limit: int = 20,
    use_ai: bool = True,
) -> dict[str, Any]:
    clean_days = _bounded_days(days, default=7)
    cutoff = now_bjt() - timedelta(days=clean_days)
    event_filter = _event_filter(user, created_after=cutoff)
    candidate_sources = {
        "sf_usage_aggregate",
        "ai_todo",
        "todo_dispatch_task",
        "knowledge_query",
        "execution_run",
        "agent_thread",
    }
    rows = (
        await db.execute(
            select(LearningEvent)
            .where(
                event_filter,
                LearningEvent.source_type.in_(sorted(candidate_sources)),
                LearningEvent.status != "ignored",
            )
            .order_by(LearningEvent.quality_score.desc().nulls_last(), LearningEvent.created_at.desc())
            .limit(max(1, min(int(limit or 20), 80)))
        )
    ).scalars().all()
    ai_enabled = bool(use_ai)
    if ai_enabled:
        try:
            await get_ai_config(require_system_config=True)
        except Exception:
            ai_enabled = False
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    review_handoffs: list[dict[str, Any]] = []
    agent_drafts: list[dict[str, Any]] = []
    for event in rows:
        result = await judge_and_create_agentization_candidate(db, event, use_ai=ai_enabled)
        if result.get("created"):
            artifact = result.get("artifact") or {}
            judgement = result.get("judgement") or {}
            handoff = None
            candidate_id = artifact.get("sink_id")
            if candidate_id:
                try:
                    handoff = await create_review_todo_for_candidate(db, user, str(candidate_id))
                    review_handoffs.append({
                        "event_id": event.id,
                        "candidate_id": candidate_id,
                        "governance_queue": handoff.get("governance_queue"),
                        "decision_request_id": handoff.get("decision_request_id"),
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "agentization candidate auto review handoff failed candidate={} err={}",
                        candidate_id,
                        exc,
                    )
                try:
                    draft_result = await create_agent_draft_from_candidate(db, user, str(candidate_id), auto=True)
                    validation = None
                    candidate_row = await db.get(ImprovementCandidate, str(candidate_id))
                    if candidate_row:
                        validation = await validate_agent_draft_for_candidate(db, user, candidate_row)
                    agent_drafts.append({
                        "event_id": event.id,
                        "candidate_id": candidate_id,
                        "agent_draft_id": (draft_result.get("agent_draft") or {}).get("id"),
                        "validation": validation,
                        "idempotent": bool(draft_result.get("idempotent")),
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "agentization candidate auto draft failed candidate={} err={}",
                        candidate_id,
                        exc,
                    )
            created.append({
                "event_id": event.id,
                "source_type": event.source_type,
                "artifact_id": artifact.get("id"),
                "candidate_id": candidate_id,
                "target_id": artifact.get("target_id"),
                "agent_name": judgement.get("agent_name"),
                "confidence": judgement.get("confidence"),
                "ai_used": judgement.get("ai_used"),
                "review_handoff": handoff,
            })
        else:
            skipped.append({"event_id": event.id, "source_type": event.source_type, "reason": result.get("reason")})
    return {
        "created": created,
        "skipped": skipped,
        "review_handoffs": review_handoffs,
        "agent_drafts": agent_drafts,
        "ai_enabled": ai_enabled,
        "days": clean_days,
        "governance": {
            "raw_payload_returned": False,
            "candidate_only": True,
            "draft_only": True,
            "no_auto_publish": True,
        },
    }


_AGENT_DRAFT_FORBIDDEN_TOKENS = (
    "subprocess",
    "os.system",
    "requests.",
    "httpx.",
    "urllib.",
    "socket.",
    "paramiko",
    "send_work_notice",
    "dingtalk",
    "eval(",
    "exec(",
)


async def validate_agent_draft_for_candidate(
    db: AsyncSession,
    user: User | None,
    candidate: ImprovementCandidate,
) -> dict[str, Any]:
    """Static governance validation for auto-generated Agent drafts.

    The generated draft is allowed to be a review artifact only. Validation is
    intentionally static: no generated code is executed, no external network or
    account side effects are possible.
    """
    from app.workbench.models import SkillStudioDraft

    external = dict(candidate.external_ref_json or {})
    draft_id = str(external.get("agent_draft_id") or "").strip()
    errors: list[str] = []
    warnings: list[str] = []
    draft = await db.get(SkillStudioDraft, draft_id) if draft_id else None
    if not draft_id:
        errors.append("missing_agent_draft_id")
    elif not draft:
        errors.append("agent_draft_not_found")
    if draft:
        if draft.skill_id is not None:
            errors.append("draft_already_bound_to_skill")
        if draft.generation_status != "ready":
            errors.append(f"draft_status_not_ready:{draft.generation_status}")
        contract = _safe_dict(draft.contract_json)
        lineage = _safe_dict(contract.get("lineage"))
        if lineage.get("learning_candidate_id") != candidate.id:
            errors.append("lineage_candidate_mismatch")
        if _safe_dict(contract.get("output")).get("adapter") != "learning_governance_queue":
            errors.append("output_adapter_not_governance_queue")
        extra_files = _safe_dict(draft.extra_files)
        for path in ("scripts/main.py", "tests/test_main.py", "learning-lineage.json"):
            if not extra_files.get(path):
                errors.append(f"missing_required_file:{path}")
        main_py = str(extra_files.get("scripts/main.py") or "")
        test_py = str(extra_files.get("tests/test_main.py") or "")
        lineage_text = str(extra_files.get("learning-lineage.json") or "")
        combined = "\n".join([main_py, test_py, lineage_text, str(draft.policy_yaml or "")]).lower()
        forbidden = [token for token in _AGENT_DRAFT_FORBIDDEN_TOKENS if token in combined]
        if forbidden:
            errors.append("forbidden_side_effect_tokens:" + ",".join(sorted(set(forbidden))[:8]))
        if "def main" not in main_py:
            errors.append("missing_main_function")
        if "no_auto_publish" not in combined:
            errors.append("missing_no_auto_publish_guardrail")
        if "draft_only" not in combined:
            warnings.append("missing_draft_only_text")
    status = "failed" if errors else "passed"
    draft_ref = draft_id or "missing"
    event = await _upsert_event(
        db,
        event_type=f"agent_draft.validation.{status}",
        source_type="agent_draft_validation",
        source_id=f"{candidate.id}:{draft_ref}",
        source_hash=_sha256({
            "candidate_id": candidate.id,
            "agent_draft_id": draft_ref,
            "status": status,
            "errors": errors,
            "warnings": warnings,
        }),
        department=candidate.department,
        org_unit_id=candidate.org_unit_id,
        skill_id=candidate.skill_id,
        user_id=str(getattr(user, "id", "") or ""),
        run_id=candidate.run_id,
        payload_ref=f"agent_draft_validation:{candidate.id}:{draft_ref}",
        redacted_summary=_clip(
            f"Agent 治理草稿校验{_status_label(status)}：{candidate.title}；"
            f"{'无阻断问题' if not errors else '阻断：' + ','.join(errors[:5])}",
            1200,
        ),
        sensitivity_level="internal",
        quality_score=0.82 if status == "passed" else 0.74,
        metadata={
            "candidate_id": candidate.id,
            "agent_draft_id": draft_id or None,
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "static_only": True,
            "no_code_execution": True,
        },
    )
    await _upsert_edge(
        db,
        from_type="improvement_candidate",
        from_id=candidate.id,
        to_type="learning_event",
        to_id=event.id,
        relation="validated_by",
        department=candidate.department,
        org_unit_id=candidate.org_unit_id,
        skill_id=candidate.skill_id,
        run_id=candidate.run_id,
        metadata={"status": status, "agent_draft_id": draft_id or None},
    )
    if draft_id:
        await _upsert_edge(
            db,
            from_type="agent_draft",
            from_id=draft_id,
            to_type="learning_event",
            to_id=event.id,
            relation="validated_by",
            department=candidate.department,
            org_unit_id=candidate.org_unit_id,
            skill_id=candidate.skill_id,
            run_id=candidate.run_id,
            metadata={"status": status, "candidate_id": candidate.id},
        )
    external["agent_draft_validation"] = {
        "status": status,
        "checked_at": _iso(now_bjt()),
        "event_id": event.id,
        "errors": errors,
        "warnings": warnings,
        "static_only": True,
        "no_code_execution": True,
    }
    candidate.external_ref_json = external
    candidate.updated_by = str(getattr(user, "id", "") or candidate.updated_by or "")
    candidate.updated_at = now_bjt()
    return {
        "candidate_id": candidate.id,
        "agent_draft_id": draft_id or None,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "event_id": event.id,
    }


async def run_self_iteration_gap_audit(
    db: AsyncSession,
    user: User,
    *,
    days: int = 7,
    limit: int = 50,
    auto_remediate: bool = True,
    use_ai: bool = True,
) -> dict[str, Any]:
    """Audit missing links in the AI self-iteration loop and create gaps.

    This is the meta-loop for "AI improves the AI system": it checks whether
    previously generated candidates are stuck because a required automatic
    follow-up is missing, tries safe control-plane remediations, and creates
    governed ``ai_system_gap_candidate`` rows when the platform still needs a
    product/engineering capability. It never publishes Skills/Agents or executes
    external side effects.
    """
    clean_days = _bounded_days(days, default=7)
    clean_limit = max(1, min(int(limit or 50), 200))
    cutoff = now_bjt() - timedelta(days=clean_days)
    created: list[dict[str, Any]] = []
    remediated: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    review_handoffs: list[dict[str, Any]] = []
    ai_suggestions: list[dict[str, Any]] = []
    ai_enabled = bool(use_ai)

    async def create_gap(
        *,
        gap_key: str,
        gap_type: str,
        title: str,
        summary: str,
        proposal: str,
        risk_level: str = "R1",
        expected_impact: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        priority: float = 0.72,
    ) -> LearningArtifact:
        event = await _upsert_event(
            db,
            event_type="ai.self_iteration.gap",
            source_type="learning_gap_audit",
            source_id=gap_key,
            source_hash=_sha256({
                "gap_key": gap_key,
                "gap_type": gap_type,
                "summary": summary,
                "proposal": proposal,
                "evidence": evidence or {},
            }),
            department=None if _is_global_user(user) else _user_department(user),
            user_id=str(getattr(user, "id", "") or ""),
            payload_ref=f"learning_gap_audit:{gap_key}",
            redacted_summary=_clip(summary, 1200),
            sensitivity_level="internal",
            quality_score=priority,
            metadata={
                "gap_key": gap_key,
                "gap_type": gap_type,
                "evidence": evidence or {},
                "auto_remediate": bool(auto_remediate),
            },
        )
        target_id = _agentization_target_id(gap_key)
        content = {
            "target_type": "ai_system",
            "target_id": target_id,
            "proposal": proposal,
            "risk_level": risk_level,
            "expected_impact": {
                "self_iteration_completeness": "补齐 AI 自动发现、自动添加、自动循环中的断点",
                **(expected_impact or {}),
            },
            "gap_type": gap_type,
            "gap_key": gap_key,
            "evidence": evidence or {},
            "source_type": "learning_gap_audit",
            "governance": {
                "candidate_only": True,
                "review_required": True,
                "no_auto_publish": True,
                "no_external_side_effect": True,
            },
        }
        existing_artifact = (
            await db.execute(
                select(LearningArtifact)
                .where(
                    LearningArtifact.event_id == event.id,
                    LearningArtifact.artifact_kind == "ai_system_gap_candidate",
                    LearningArtifact.target_type == "ai_system",
                    LearningArtifact.target_id == target_id,
                )
                .order_by(LearningArtifact.updated_at.desc(), LearningArtifact.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        idempotent = existing_artifact is not None
        if existing_artifact is not None:
            policy = _policy_for_artifact("ai_system_gap_candidate", sensitivity_level=event.sensitivity_level or "internal", event_type=event.event_type)
            existing_artifact.title = title[:240]
            existing_artifact.summary = _clip(summary, 4000)
            existing_artifact.content_json = content
            existing_artifact.labels_json = ["candidate", "ai_system", "self_iteration_gap", gap_type]
            existing_artifact.quality_score = max(float(existing_artifact.quality_score or 0), float(priority or 0))
            existing_artifact.confidence = max(float(existing_artifact.confidence or 0), max(0.6, min(0.9, priority)))
            existing_artifact.policy_result_json = policy
            existing_artifact.updated_at = now_bjt()
            artifact = existing_artifact
            await _ensure_improvement_candidate(db, artifact, created_by=event.user_id)
        else:
            artifact = await _upsert_artifact(
                db,
                event,
                artifact_kind="ai_system_gap_candidate",
                title=title,
                summary=summary,
                content=content,
                labels=["candidate", "ai_system", "self_iteration_gap", gap_type],
                target_type="ai_system",
                target_id=target_id,
                quality_score=priority,
                confidence=max(0.6, min(0.9, priority)),
            )
        candidate_id = artifact.sink_id
        handoff = None
        if candidate_id:
            try:
                handoff = await create_review_todo_for_candidate(db, user, str(candidate_id))
                review_handoffs.append({
                    "gap_key": gap_key,
                    "candidate_id": candidate_id,
                    "governance_queue": handoff.get("governance_queue"),
                    "decision_request_id": handoff.get("decision_request_id"),
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("self-iteration gap review handoff failed gap={} err={}", gap_key, exc)
        created.append({
            "gap_key": gap_key,
            "gap_type": gap_type,
            "artifact_id": artifact.id,
            "candidate_id": candidate_id,
            "idempotent": idempotent,
            "review_handoff": handoff,
        })
        return artifact

    # Gap 1: production should use SystemConfig-backed AI. If missing, the loop
    # still works with deterministic fallback, but the self-iteration system
    # should surface it as a governed platform gap.
    try:
        await get_ai_config(require_system_config=True)
    except Exception:
        ai_enabled = False
        await create_gap(
            gap_key="ai_config_missing",
            gap_type="ai_runtime",
            title="AI 自迭代缺口：未配置平台 AI",
            summary="当前自动判断使用规则兜底，未检测到 SystemConfig ai.* 模型配置。",
            proposal="在后台配置统一 AI 模型和成本/超时策略，让 Agent 化判断、缺口审计和候选排序优先使用受控模型。",
            risk_level="R2",
            expected_impact={"agentization_quality": "减少纯规则兜底导致的漏判或误判"},
            evidence={"require_system_config": True},
            priority=0.74,
        )

    candidate_rows = (
        await db.execute(
            select(ImprovementCandidate)
            .where(
                _candidate_filter(user, created_after=cutoff, target_type="agent"),
                ImprovementCandidate.status.in_(["reviewing", "accepted", "implemented"]),
            )
            .order_by(ImprovementCandidate.updated_at.desc())
            .limit(clean_limit)
        )
    ).scalars().all()

    for candidate in candidate_rows:
        external = dict(candidate.external_ref_json or {})
        source_artifact = await db.get(LearningArtifact, candidate.source_artifact_id) if candidate.source_artifact_id else None
        is_creation_candidate = bool(source_artifact and source_artifact.artifact_kind == "agent_creation_candidate")
        if is_creation_candidate and not external.get("agent_draft_id"):
            if auto_remediate:
                try:
                    draft_result = await create_agent_draft_from_candidate(db, user, candidate.id, auto=True)
                    remediated.append({
                        "gap_key": f"agent_candidate_missing_draft:{candidate.id}",
                        "candidate_id": candidate.id,
                        "action": "created_agent_draft",
                        "agent_draft_id": (draft_result.get("agent_draft") or {}).get("id"),
                    })
                    external = dict(candidate.external_ref_json or {})
                except Exception as exc:  # noqa: BLE001
                    logger.warning("self-audit failed to create missing agent draft candidate={} err={}", candidate.id, exc)
            if not external.get("agent_draft_id"):
                await create_gap(
                    gap_key=f"agent_candidate_missing_draft:{candidate.id}",
                    gap_type="agentization_pipeline",
                    title=f"AI 自迭代缺口：Agent 候选缺少治理草稿",
                    summary=f"Agent 候选 {candidate.id} 已进入治理，但没有 Workbench 治理草稿，自动添加链路不完整。",
                    proposal="修复 Agent 候选到 Workbench 草稿的自动生成链路，并补充幂等重试和失败告警。",
                    risk_level="R1",
                    expected_impact={"auto_add_agent": "确保 Agent 候选能自动添加到治理工作台"},
                    evidence={"candidate_id": candidate.id, "target_id": candidate.target_id},
                    priority=0.82,
                )
                continue

        if is_creation_candidate and external.get("agent_draft_id"):
            validation = await validate_agent_draft_for_candidate(db, user, candidate)
            validations.append(validation)
            if validation.get("status") == "failed":
                await create_gap(
                    gap_key=f"agent_draft_validation_failed:{candidate.id}:{external.get('agent_draft_id')}",
                    gap_type="agent_draft_quality",
                    title="AI 自迭代缺口：Agent 治理草稿校验失败",
                    summary=f"Agent 候选 {candidate.id} 的治理草稿未通过静态安全/治理校验。",
                    proposal="修复 Agent 草稿生成器，确保草稿只作为治理评审材料、包含 no_auto_publish 防护、完整血缘和最小测试。",
                    risk_level="R2",
                    expected_impact={"agent_draft_safety": "避免不合规草稿进入实施队列"},
                    evidence={
                        "candidate_id": candidate.id,
                        "agent_draft_id": external.get("agent_draft_id"),
                        "errors": validation.get("errors") or [],
                    },
                    priority=0.84,
                )
                continue

        if candidate.status in {"accepted", "implemented"} and not external.get("implementation_queue"):
            if auto_remediate:
                try:
                    await capture_candidate_status_feedback(
                        db,
                        candidate,
                        user=user,
                        status=candidate.status,
                        reason="AI 自审补齐 Agent 实施交接队列",
                        previous_status=None,
                    )
                    remediated.append({
                        "gap_key": f"accepted_agent_missing_implementation_queue:{candidate.id}",
                        "candidate_id": candidate.id,
                        "action": "created_implementation_handoff",
                        "implementation_queue": (candidate.external_ref_json or {}).get("implementation_queue"),
                    })
                    external = dict(candidate.external_ref_json or {})
                except Exception as exc:  # noqa: BLE001
                    logger.warning("self-audit failed implementation handoff candidate={} err={}", candidate.id, exc)
            if not external.get("implementation_queue"):
                await create_gap(
                    gap_key=f"accepted_agent_missing_implementation_queue:{candidate.id}",
                    gap_type="agent_implementation_handoff",
                    title="AI 自迭代缺口：采纳的 Agent 候选缺少实施交接",
                    summary=f"Agent 候选 {candidate.id} 已采纳但未进入 implementation 治理队列。",
                    proposal="补齐采纳后自动交接到实施治理队列、负责人认领和发布前检查的控制面链路。",
                    risk_level="R2",
                    expected_impact={"agent_delivery": "减少采纳后无人实施或无法追踪"},
                    evidence={"candidate_id": candidate.id, "target_id": candidate.target_id},
                    priority=0.8,
                )
            continue

        skipped.append({"candidate_id": candidate.id, "reason": "no_gap"})

    rejected_rows = (
        await db.execute(
            select(ImprovementCandidate.target_type, func.count(ImprovementCandidate.id))
            .where(
                _candidate_filter(user, created_after=cutoff),
                ImprovementCandidate.status == "rejected",
            )
            .group_by(ImprovementCandidate.target_type)
        )
    ).all()
    for target_type, count in rejected_rows:
        if int(count or 0) >= 2:
            await create_gap(
                gap_key=f"candidate_precision_gap:{target_type}",
                gap_type="candidate_quality",
                title=f"AI 自迭代缺口：{target_type} 候选近期驳回偏多",
                summary=f"近 {clean_days} 天 {target_type} 候选被驳回 {int(count or 0)} 次，自动发现/排序策略需要复盘。",
                proposal="聚合驳回原因，生成负样本评测集，调整候选阈值、证据要求和 Agent 化判断提示词。",
                risk_level="R1",
                expected_impact={"candidate_precision": "降低重复无效候选"},
                evidence={"target_type": target_type, "rejected_count": int(count or 0), "days": clean_days},
                priority=0.78,
            )

    failed_jobs = int((
        await db.execute(
            select(func.count(LearningIngestionJob.id)).where(
                _ingestion_job_filter(user, created_after=cutoff),
                LearningIngestionJob.status == "failed",
            )
        )
    ).scalar() or 0)
    if failed_jobs:
        await create_gap(
            gap_key="learning_materialization_failures",
            gap_type="materialization_reliability",
            title="AI 自迭代缺口：学习资产物化失败",
            summary=f"近 {clean_days} 天存在 {failed_jobs} 个学习资产物化失败任务。",
            proposal="为学习资产物化补充自动重试、错误分类、降级路径和治理告警，避免正向循环断在入库/入样本阶段。",
            risk_level="R2",
            expected_impact={"learning_flow_reliability": "减少自动循环断点"},
            evidence={"failed_job_count": failed_jobs, "days": clean_days},
            priority=0.76,
        )

    automation_failed_runs = int((
        await db.execute(
            select(func.count(LearningAutomationRun.id)).where(
                LearningAutomationRun.started_at >= cutoff,
                LearningAutomationRun.status == "failed",
            )
        )
    ).scalar() or 0)
    latest_automation_success_at = (
        await db.execute(
            select(func.max(LearningAutomationRun.finished_at)).where(
                LearningAutomationRun.status == "succeeded",
            )
        )
    ).scalar()
    if automation_failed_runs:
        await create_gap(
            gap_key="learning_automation_run_failures",
            gap_type="automation_reliability",
            title="AI 自迭代缺口：自动循环运行失败",
            summary=f"近 {clean_days} 天存在 {automation_failed_runs} 次学习自动循环运行失败，自动发现、自动添加或自审可能中断。",
            proposal="为学习自动循环补充失败原因分类、重试退避、运行健康告警和人工兜底入口，确保失败后仍能恢复下一轮自动处理。",
            risk_level="R2",
            expected_impact={"automation_reliability": "避免 AI 自迭代主循环静默中断"},
            evidence={"failed_run_count": automation_failed_runs, "days": clean_days},
            priority=0.82,
        )

    stale_before = now_bjt() - timedelta(hours=LEARNING_GOVERNANCE_TASK_STALE_HOURS)
    stale_governance_rows = (
        await db.execute(
            select(LearningGovernanceTask)
            .where(
                _scope_filter(LearningGovernanceTask, user),
                LearningGovernanceTask.created_at >= cutoff,
                LearningGovernanceTask.created_at <= stale_before,
                LearningGovernanceTask.status.in_(["pending", "in_progress"]),
            )
            .order_by(LearningGovernanceTask.created_at.asc(), LearningGovernanceTask.priority_score.desc())
            .limit(clean_limit)
        )
    ).scalars().all()
    stale_governance_tasks = [_governance_task_to_dict(row) for row in stale_governance_rows]
    stale_by_queue: dict[str, list[LearningGovernanceTask]] = {}
    for task in stale_governance_rows:
        stale_by_queue.setdefault(task.queue_id or "unknown", []).append(task)
    for queue_id, task_rows in stale_by_queue.items():
        await create_gap(
            gap_key=f"governance_queue_backlog:{queue_id}",
            gap_type="governance_sla",
            title=f"AI 自迭代缺口：{queue_id} 治理队列处理超时",
            summary=(
                f"{queue_id} 存在超过 {LEARNING_GOVERNANCE_TASK_STALE_HOURS} 小时仍未处理的治理任务，"
                "自动发现出的候选可能卡在人工决策环节。"
            ),
            proposal="为该治理队列补充分配负责人、SLA 提醒、批量决策入口和超时升级规则，避免自动添加候选后无法进入下一轮学习。",
            risk_level="R2",
            expected_impact={
                "governance_sla": "减少 AI 自动发现 / 自动添加后的人工治理积压",
                "queue_id": queue_id,
                "stale_threshold_hours": LEARNING_GOVERNANCE_TASK_STALE_HOURS,
            },
            evidence={
                "queue_id": queue_id,
                "stale_threshold_hours": LEARNING_GOVERNANCE_TASK_STALE_HOURS,
            },
            priority=min(0.9, 0.72 + len(task_rows) * 0.03),
        )

    terminal_governance_rows = (
        await db.execute(
            select(LearningGovernanceTask)
            .where(
                _scope_filter(LearningGovernanceTask, user),
                LearningGovernanceTask.created_at >= cutoff,
                LearningGovernanceTask.status.in_(["completed", "rejected", "cancelled"]),
            )
            .order_by(LearningGovernanceTask.updated_at.desc(), LearningGovernanceTask.created_at.desc())
            .limit(clean_limit)
        )
    ).scalars().all()
    decisionless_governance_tasks: list[dict[str, Any]] = []
    for task in terminal_governance_rows:
        if _safe_dict(task.payload_json).get("decision"):
            continue
        candidate = await db.get(ImprovementCandidate, task.candidate_id)
        if candidate is not None and candidate.status not in {"open", "reviewing"}:
            continue
        task_payload = _governance_task_to_dict(task)
        task_payload["candidate_status"] = candidate.status if candidate else None
        decisionless_governance_tasks.append(task_payload)
        await create_gap(
            gap_key=f"governance_task_missing_decision:{task.id}",
            gap_type="governance_feedback",
            title="AI 自迭代缺口：治理任务缺少候选决策反馈",
            summary=(
                f"治理任务 {task.id} 已进入终态 {task.status}，但没有 accepted/rejected/implemented 决策，"
                "候选状态和训练反馈无法回流到下一轮 AI 判断。"
            ),
            proposal="要求治理任务终态必须携带候选决策，或补充无决策终态的自动复核 / 重新打开 / 反馈补录入口。",
            risk_level="R2",
            expected_impact={
                "feedback_loop": "保证自动发现候选的评审结果能回流为训练样本、评测样本和策略修正",
                "queue_id": task.queue_id,
            },
            evidence={"task_id": task.id, "candidate_id": task.candidate_id, "task_status": task.status},
            priority=0.82,
        )

    has_unresolved_backlog = bool(failed_jobs or automation_failed_runs or stale_governance_rows or decisionless_governance_tasks)
    if has_unresolved_backlog and (
        latest_automation_success_at is None
        or latest_automation_success_at <= now_bjt() - timedelta(hours=LEARNING_AUTOMATION_RUN_STALE_HOURS)
    ):
        await create_gap(
            gap_key="learning_automation_reconciler_stale",
            gap_type="automation_reliability",
            title="AI 自迭代缺口：自动循环缺少近期成功运行",
            summary=(
                f"存在未清理的学习流阻塞，但最近 {LEARNING_AUTOMATION_RUN_STALE_HOURS} 小时内没有检测到成功的自动循环运行。"
            ),
            proposal="检查 learning auto-flow worker、后台任务注册、定时配置和运行持久化，确保自动发现、自动添加和自审能持续循环。",
            risk_level="R2",
            expected_impact={"automation_continuity": "确保 AI 自迭代循环持续运行而非只依赖人工触发"},
            evidence={
                "latest_success_at": _iso(latest_automation_success_at),
                "stale_threshold_hours": LEARNING_AUTOMATION_RUN_STALE_HOURS,
                "failed_materialization_jobs": failed_jobs,
                "failed_automation_runs": automation_failed_runs,
                "stale_governance_tasks": len(stale_governance_rows),
                "decisionless_governance_tasks": len(decisionless_governance_tasks),
            },
            priority=0.8,
        )

    if ai_enabled:
        safe_candidate_rows = (
            await db.execute(
                select(ImprovementCandidate)
                .where(_candidate_filter(user, created_after=cutoff))
                .order_by(ImprovementCandidate.priority_score.desc(), ImprovementCandidate.updated_at.desc())
                .limit(40)
            )
        ).scalars().all()
        prompt_payload = {
            "days": clean_days,
            "scope": "global" if _is_global_user(user) else "department",
            "department": None if _is_global_user(user) else _user_department(user),
            "heuristic_created_gap_keys": [item.get("gap_key") for item in created],
            "remediated": remediated,
            "validations": validations[-20:],
            "failed_materialization_jobs": failed_jobs,
            "decisionless_governance_tasks": [
                {
                    "id": row.get("id"),
                    "queue_id": row.get("queue_id"),
                    "target_type": row.get("target_type"),
                    "status": row.get("status"),
                    "candidate_status": row.get("candidate_status"),
                }
                for row in decisionless_governance_tasks[:20]
            ],
            "candidate_snapshot": [
                {
                    "id": row.id,
                    "target_type": row.target_type,
                    "target_id": row.target_id,
                    "status": row.status,
                    "risk_level": row.risk_level,
                    "priority_score": row.priority_score,
                    "has_agent_draft": bool(_safe_dict(row.external_ref_json).get("agent_draft_id")),
                    "draft_validation": _safe_dict(_safe_dict(row.external_ref_json).get("agent_draft_validation")).get("status"),
                    "implementation_status": _safe_dict(row.external_ref_json).get("implementation_status"),
                    "implementation_queue": _safe_dict(row.external_ref_json).get("implementation_queue"),
                    "title": _clip(row.title, 160),
                }
                for row in safe_candidate_rows
            ],
            "required_json_schema": {
                "gaps": [
                    {
                        "gap_key": "stable snake_case id",
                        "gap_type": "cost_control|permission|quality|coverage|observability|evaluation|other",
                        "title": "short title",
                        "summary": "why this AI self-iteration gap matters",
                        "proposal": "governed control-plane fix",
                        "risk_level": "R1/R2/R3",
                        "priority": "0-1 number",
                        "expected_impact": {"metric": "impact"},
                    }
                ]
            },
            "guardrails": [
                "只能提出候选，不自动发布 Agent/Skill/模型",
                "不得要求真实外部副作用",
                "不得返回原始敏感 payload",
                "优先补齐自动发现、自动添加、自动循环中的缺口",
            ],
        }
        try:
            result = await call_llm(
                system=(
                    "你是 SkillForge AI 自迭代审计 Agent。根据学习流摘要判断还有哪些 AI 系统能力缺口。"
                    "只输出 JSON；建议必须是控制面治理候选，不得自动发布、部署或执行真实外部副作用。"
                ),
                user=_json_dumps(prompt_payload),
                json_mode=True,
                temperature=0.1,
                max_tokens=2200,
                timeout=25,
                call_source="learning.self_iteration_gap_audit",
                cost_context={"days": clean_days, "scope": prompt_payload["scope"]},
                require_system_config=True,
            )
        except Exception as exc:  # noqa: BLE001
            ai_enabled = False
            logger.debug("self-iteration AI gap audit fallback err={}", exc)
        else:
            raw_gaps = result.get("gaps") if isinstance(result, dict) else result
            for item in _safe_list(raw_gaps)[:6]:
                if not isinstance(item, dict):
                    continue
                gap_key = _agentization_target_id(item.get("gap_key") or item.get("title"))
                if not gap_key:
                    continue
                title = _clip(item.get("title") or f"AI 自迭代缺口：{gap_key}", 160)
                summary = _clip(item.get("summary") or item.get("reason") or title, 1200)
                proposal = _clip(item.get("proposal") or "进入 AI 系统治理队列评估并补齐该能力。", 2000)
                try:
                    priority = max(0.55, min(0.92, float(item.get("priority") or 0.76)))
                except (TypeError, ValueError):
                    priority = 0.76
                artifact = await create_gap(
                    gap_key=f"ai_suggested:{gap_key}",
                    gap_type=_clip(item.get("gap_type") or "ai_suggested", 80),
                    title=title,
                    summary=summary,
                    proposal=proposal,
                    risk_level=_clip(item.get("risk_level") or "R2", 20),
                    expected_impact={"ai_suggested": True, **_safe_dict(item.get("expected_impact"))},
                    evidence={"source": "llm_self_audit", "input_scope": prompt_payload["scope"]},
                    priority=priority,
                )
                ai_suggestions.append({
                    "gap_key": f"ai_suggested:{gap_key}",
                    "artifact_id": artifact.id,
                    "candidate_id": artifact.sink_id,
                    "priority": priority,
                })

    return {
        "created": created,
        "remediated": remediated,
        "validations": validations,
        "stale_governance_tasks": stale_governance_tasks,
        "decisionless_governance_tasks": decisionless_governance_tasks,
        "automation_health": {
            "failed_runs": automation_failed_runs,
            "latest_success_at": _iso(latest_automation_success_at),
            "stale": bool(
                has_unresolved_backlog
                and (
                    latest_automation_success_at is None
                    or latest_automation_success_at <= now_bjt() - timedelta(hours=LEARNING_AUTOMATION_RUN_STALE_HOURS)
                )
            ),
            "stale_threshold_hours": LEARNING_AUTOMATION_RUN_STALE_HOURS,
        },
        "ai_suggestions": ai_suggestions,
        "ai_enabled": ai_enabled,
        "skipped": skipped[:clean_limit],
        "review_handoffs": review_handoffs,
        "days": clean_days,
        "governance": {
            "candidate_only": True,
            "review_required": True,
            "auto_remediate": bool(auto_remediate),
            "ai_used": bool(ai_suggestions),
            "no_auto_publish": True,
            "raw_payload_returned": False,
        },
    }


async def backfill_learning_events(
    db: AsyncSession,
    user: User,
    *,
    days: int = 7,
    limit: int = 200,
    materialize: bool = False,
) -> dict[str, Any]:
    clean_limit = max(1, min(int(limit or 200), EVENT_LIMIT_MAX))
    cutoff = now_bjt() - timedelta(days=max(1, min(int(days or 7), 365)))
    visible_skill_ids = await _visible_skill_ids(db, user)
    captured: dict[str, int] = {}
    materialized = 0
    sf_tool_counts: dict[str, dict[str, Any]] = {}

    async def inc(key: str) -> None:
        captured[key] = captured.get(key, 0) + 1

    run_stmt = select(ExecutionRun).where(ExecutionRun.started_at >= cutoff).order_by(ExecutionRun.started_at.desc()).limit(clean_limit)
    if visible_skill_ids is not None:
        run_stmt = run_stmt.where(ExecutionRun.skill_id.in_(sorted(visible_skill_ids)) if visible_skill_ids else sa.false())
    for row in (await db.execute(run_stmt)).scalars().all():
        await capture_execution_run(db, row)
        await inc("execution_run")

    execution_artifact_stmt = (
        select(ExecutionArtifact)
        .where(ExecutionArtifact.created_at >= cutoff)
        .order_by(ExecutionArtifact.created_at.desc(), ExecutionArtifact.id.desc())
        .limit(clean_limit)
    )
    if visible_skill_ids is not None:
        execution_artifact_stmt = execution_artifact_stmt.where(ExecutionArtifact.skill_id.in_(sorted(visible_skill_ids)) if visible_skill_ids else sa.false())
    for row in (await db.execute(execution_artifact_stmt)).scalars().all():
        await capture_execution_artifact(db, row)
        await inc("execution_artifact")

    analyze_stmt = (
        select(IntelligenceAnalyzeRun, IntelligenceAnalyzeCache.output_payload)
        .join(IntelligenceAnalyzeCache, IntelligenceAnalyzeCache.id == IntelligenceAnalyzeRun.cache_id, isouter=True)
        .where(IntelligenceAnalyzeRun.created_at >= cutoff)
        .order_by(IntelligenceAnalyzeRun.created_at.desc(), IntelligenceAnalyzeRun.id.desc())
        .limit(clean_limit)
    )
    if visible_skill_ids is not None:
        analyze_stmt = analyze_stmt.where(
            IntelligenceAnalyzeRun.skill_id.in_(sorted(visible_skill_ids)) if visible_skill_ids else sa.false()
        )
    for row, output_payload in (await db.execute(analyze_stmt)).all():
        await capture_intelligence_analyze_run(
            db,
            row,
            output_payload=output_payload if isinstance(output_payload, dict) else None,
        )
        await inc("intelligence_analyze_run")

    decision_stmt = select(DecisionLog).where(DecisionLog.created_at >= cutoff).order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc()).limit(clean_limit)
    if visible_skill_ids is not None:
        decision_stmt = decision_stmt.where(DecisionLog.skill_id.in_(sorted(visible_skill_ids)) if visible_skill_ids else sa.false())
    for row in (await db.execute(decision_stmt)).scalars().all():
        await capture_decision_log(db, row)
        await inc("decision_log")

    mcp_stmt = select(CodexMcpCallAudit).where(CodexMcpCallAudit.created_at >= cutoff).order_by(CodexMcpCallAudit.created_at.desc()).limit(clean_limit)
    if visible_skill_ids is not None:
        uid = str(getattr(user, "id", "") or "")
        mcp_stmt = mcp_stmt.where(or_(CodexMcpCallAudit.user_id == uid, CodexMcpCallAudit.skill_id.in_(sorted(visible_skill_ids)) if visible_skill_ids else sa.false()))
    for row in (await db.execute(mcp_stmt)).scalars().all():
        await capture_sf_mcp_call(db, row)
        bucket = sf_tool_counts.setdefault(row.tool, {"count": 0, "failed": 0, "last_id": row.id})
        bucket["count"] += 1
        bucket["failed"] += 0 if row.ok else 1
        bucket["last_id"] = row.id
        await inc("sf_mcp_call")
    for tool, stat in sf_tool_counts.items():
        # Phase 5: 高频 SF 手工调用本身就是“可产品化”的信号。失败在 capture_sf_mcp_call
        # 中已形成修复候选；这里补充成功/混合高频调用的能力固化候选。
        if int(stat.get("count") or 0) >= 3:
            event = await _upsert_event(
                db,
                event_type="sf.usage.hotspot",
                source_type="sf_usage_aggregate",
                source_id=f"{tool}:{days}d",
                source_hash=_sha256({"tool": tool, "days": days, "count": stat.get("count"), "failed": stat.get("failed")}),
                user_id=str(getattr(user, "id", "") or "") if not _is_global_user(user) else None,
                payload_ref=f"sf_usage:{tool}:{days}d",
                redacted_summary=f"{tool} 在近 {days} 天被调用 {stat.get('count')} 次，适合评估是否固化为 SF 命令或 Skill。",
                sensitivity_level="internal",
                quality_score=min(0.65 + int(stat.get("count") or 0) * 0.03, 0.95),
                metadata={"tool": tool, "days": days, **stat},
            )
            await _upsert_artifact(
                db,
                event,
                artifact_kind="sf_iteration_candidate",
                title=f"固化高频 SF 能力：{tool}",
                summary=f"{tool} 近期高频调用，建议补充命令模板、参数防呆、报告模板，或沉淀为可复用 Skill。",
                content={
                    "target_type": "sf",
                    "target_id": tool,
                    "proposal": "分析该工具的高频调用场景，把重复手工调用固化为 SF 命令、报告模板或标准 Skill。",
                    "risk_level": "R1",
                    "expected_impact": {"manual_reuse": "减少重复手工调用", "tool": tool, "count": stat.get("count")},
                },
                labels=["candidate", "sf", "hotspot"],
                target_type="sf",
                target_id=tool,
                quality_score=min(0.72 + int(stat.get("count") or 0) * 0.02, 0.96),
                confidence=0.7,
            )
            await inc("sf_usage_aggregate")

    query_stmt = select(KnowledgeQueryLog).where(KnowledgeQueryLog.created_at >= cutoff).order_by(KnowledgeQueryLog.created_at.desc()).limit(clean_limit)
    if not _is_global_user(user):
        query_stmt = query_stmt.where(or_(KnowledgeQueryLog.department == _user_department(user), KnowledgeQueryLog.user_id == str(getattr(user, "id", "") or "")))
    for row in (await db.execute(query_stmt)).scalars().all():
        await capture_knowledge_query(db, row)
        await inc("knowledge_query")

    train_stmt = (
        select(TrainingJob)
        .where(or_(TrainingJob.created_at >= cutoff, TrainingJob.updated_at >= cutoff, TrainingJob.approved_at >= cutoff))
        .order_by(TrainingJob.updated_at.desc(), TrainingJob.created_at.desc())
        .limit(clean_limit)
    )
    if not _is_global_user(user):
        train_stmt = train_stmt.where(TrainingJob.department == _user_department(user))
    for row in (await db.execute(train_stmt)).scalars().all():
        await capture_training_job(db, row)
        await inc("training_job")

    deploy_stmt = (
        select(TrainingModelDeployment)
        .where(
            or_(
                TrainingModelDeployment.created_at >= cutoff,
                TrainingModelDeployment.updated_at >= cutoff,
                TrainingModelDeployment.approved_at >= cutoff,
                TrainingModelDeployment.rejected_at >= cutoff,
                TrainingModelDeployment.activated_at >= cutoff,
            )
        )
        .order_by(TrainingModelDeployment.updated_at.desc(), TrainingModelDeployment.created_at.desc())
        .limit(clean_limit)
    )
    if not _is_global_user(user):
        deploy_stmt = deploy_stmt.where(TrainingModelDeployment.department == _user_department(user))
    for row in (await db.execute(deploy_stmt)).scalars().all():
        await capture_model_deployment(db, row)
        await inc("model_deployment")

    request_stmt = (
        select(DecisionRequest)
        .where(
            or_(
                DecisionRequest.created_at >= cutoff,
                DecisionRequest.completed_at >= cutoff,
                DecisionRequest.callback_completed_at >= cutoff,
            )
        )
        .order_by(DecisionRequest.completed_at.desc().nullslast(), DecisionRequest.created_at.desc())
        .limit(clean_limit)
    )
    if visible_skill_ids is not None:
        request_stmt = request_stmt.where(DecisionRequest.skill_id.in_(sorted(visible_skill_ids)) if visible_skill_ids else sa.false())
    for row in (await db.execute(request_stmt)).scalars().all():
        await capture_decision_request(db, row)
        await inc("decision_request")

    todo_stmt = (
        select(AITodo)
        .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
        .where(or_(AITodo.created_at >= cutoff, AITodo.updated_at >= cutoff, AITodo.decided_at >= cutoff))
        .order_by(AITodo.updated_at.desc(), AITodo.created_at.desc())
        .limit(clean_limit)
    )
    if visible_skill_ids is not None:
        todo_stmt = todo_stmt.where(DecisionRequest.skill_id.in_(sorted(visible_skill_ids)) if visible_skill_ids else sa.false())
    for row in (await db.execute(todo_stmt)).scalars().all():
        await capture_ai_todo(db, row)
        await inc("ai_todo")

    dispatch_stmt = (
        select(TodoDispatchTask)
        .where(
            or_(
                TodoDispatchTask.created_at >= cutoff,
                TodoDispatchTask.updated_at >= cutoff,
                TodoDispatchTask.assigned_at >= cutoff,
                TodoDispatchTask.dispatched_at >= cutoff,
                TodoDispatchTask.started_at >= cutoff,
                TodoDispatchTask.ack_at >= cutoff,
            )
        )
        .order_by(TodoDispatchTask.updated_at.desc(), TodoDispatchTask.created_at.desc())
        .limit(clean_limit)
    )
    if not _is_global_user(user):
        dispatch_stmt = dispatch_stmt.where(or_(TodoDispatchTask.executor == str(getattr(user, "id", "") or ""), TodoDispatchTask.assigned_by == str(getattr(user, "id", "") or "")))
    for row in (await db.execute(dispatch_stmt)).scalars().all():
        await capture_todo_dispatch_task(db, row)
        await inc("todo_dispatch_task")

    candidate_feedback_stmt = (
        select(ImprovementCandidate)
        .where(
            _scope_filter(ImprovementCandidate, user),
            ImprovementCandidate.updated_at >= cutoff,
            ImprovementCandidate.status.in_(["accepted", "rejected", "implemented"]),
        )
        .order_by(ImprovementCandidate.updated_at.desc())
        .limit(clean_limit)
    )
    for row in (await db.execute(candidate_feedback_stmt)).scalars().all():
        await capture_candidate_status_feedback(db, row, user=user, status=row.status, previous_status=None)
        await inc("improvement_candidate")

    auto_run = str(getattr(user, "id", "") or "") == "learning_auto_flow"
    materialize_limit = clean_limit
    clean_days = max(1, min(int(days or 7), 365))
    training_gate = await _auto_training_gate(db) if auto_run else {
        "training_enabled": bool(settings.LEARNING_AUTO_TRAINING_ENABLED),
        "training_due": True,
        "training_interval_seconds": None,
        "training_days": clean_days,
        "latest_training_at": None,
        "training_next_after": None,
    }
    training_days = int(training_gate.get("training_days") or clean_days)
    if auto_run:
        materialize_limit = max(1, min(clean_limit, int(settings.LEARNING_AUTO_MATERIALIZE_MAX_ARTIFACTS or 20)))
    if materialize:
        ready = (
            await db.execute(
                select(LearningArtifact)
                .where(LearningArtifact.status == "ready")
                .where(_scope_filter(LearningArtifact, user))
                .order_by(LearningArtifact.created_at.desc())
                .limit(materialize_limit)
            )
        ).scalars().all()
        for artifact in ready:
            policy = artifact.policy_result_json or {}
            if policy.get("action") in {"auto_index", "training_candidate_only", "improvement_candidate", "agent_memory_only"}:
                try:
                    await materialize_artifact(db, user, artifact.id)
                    materialized += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("learning materialize failed artifact={} err={}", artifact.id, exc)
                    if auto_run:
                        artifact.status = "failed"
                        artifact.review_status = "materialize_failed"
                        policy = dict(artifact.policy_result_json or {})
                        policy["last_auto_materialize_error"] = _clip(exc, 1000)
                        policy["auto_materialize_failed_at"] = isoformat_bjt(now_bjt())
                        policy["manual_retry_allowed"] = True
                        artifact.policy_result_json = policy
                        artifact.updated_at = now_bjt()

    agentization = await run_agentization_judgement(
        db,
        user,
        days=clean_days,
        limit=min(clean_limit, 20),
        use_ai=bool(settings.LEARNING_AUTO_AGENTIZATION_AI_ENABLED) if auto_run else True,
    )
    training_candidates = {"created": [], "skipped": [], "threshold": LEARNING_TRAINING_CANDIDATE_MIN_SAMPLES}
    training_enabled = bool(settings.LEARNING_AUTO_TRAINING_ENABLED)
    training_due = bool(training_gate.get("training_due", True))
    should_prepare_training_candidates = bool(
        training_enabled
        and ((auto_run and training_due) or ((not auto_run) and materialize))
    )
    if should_prepare_training_candidates:
        try:
            training_candidates = await ensure_training_candidates_from_learning_samples(
                db,
                user,
                days=training_days if auto_run else clean_days,
                min_sample_count=_auto_training_min_samples() if auto_run else LEARNING_TRAINING_CANDIDATE_MIN_SAMPLES,
                max_skills=20,
            )
            if auto_run:
                full_history = await ensure_full_history_platform_training_candidate(
                    db,
                    user,
                    min_sample_count=_auto_training_min_samples(),
                )
                training_candidates["full_history"] = full_history
                training_candidates["created"].extend(_safe_list(full_history.get("created")))
                training_candidates["skipped"].extend(_safe_list(full_history.get("skipped")))
        except Exception as exc:  # noqa: BLE001
            logger.warning("learning training candidate auto-create failed err={}", exc)
    elif auto_run and training_enabled and not training_due:
        training_candidates["skipped"].append(_auto_training_interval_skip(training_gate))

    training_automation = {"enabled": training_enabled, "skipped": [], "errors": []}
    if auto_run and training_enabled and not training_due:
        interval_skip = _auto_training_interval_skip(training_gate)
        try:
            training_automation = await advance_learning_training_automation(
                db,
                user,
                days=training_days,
                max_jobs=min(clean_limit, int(settings.LEARNING_AUTO_TRAINING_MAX_JOBS or 10)),
                dispatch_recovery_only=True,
            )
            training_automation.setdefault("skipped", []).append(interval_skip)
        except Exception as exc:  # noqa: BLE001
            logger.warning("learning dispatch recovery automation failed err={}", exc)
            training_automation = {
                "enabled": True,
                "days": training_days,
                "max_jobs": min(clean_limit, int(settings.LEARNING_AUTO_TRAINING_MAX_JOBS or 10)),
                "dispatch_recovery_only": True,
                "skipped": [interval_skip],
                "errors": [{"stage": "dispatch_recovery", "reason": exc.__class__.__name__, "detail": _clip(exc, 1000)}],
                "governance": {"uses_review_state_machine": True, "raw_payload_returned": False},
            }
    else:
        try:
            training_automation = await advance_learning_training_automation(
                db,
                user,
                days=training_days if auto_run else clean_days,
                max_jobs=min(clean_limit, int(settings.LEARNING_AUTO_TRAINING_MAX_JOBS or 10)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("learning training automation failed err={}", exc)
            training_automation = {
                "enabled": training_enabled,
                "errors": [{"stage": "training_automation", "reason": exc.__class__.__name__, "detail": _clip(exc, 1000)}],
                "governance": {"uses_review_state_machine": True, "raw_payload_returned": False},
            }

    self_audit = await run_self_iteration_gap_audit(
        db,
        user,
        days=clean_days,
        limit=min(clean_limit, 50),
        auto_remediate=bool(settings.LEARNING_AUTO_SELF_AUDIT_REMEDIATE_ENABLED) if auto_run else True,
        use_ai=bool(settings.LEARNING_AUTO_SELF_AUDIT_AI_ENABLED) if auto_run else True,
    )

    await db.flush()
    return {
        "captured": captured,
        "materialized": materialized,
        "agentization_candidates_created": len(agentization.get("created") or []),
        "agentization": agentization,
        "training_candidates_created": len(training_candidates.get("created") or []),
        "training_candidates": training_candidates,
        "training_automation": training_automation,
        "self_audit_gaps_created": len(self_audit.get("created") or []),
        "self_audit_remediated": len(self_audit.get("remediated") or []),
        "self_audit": self_audit,
        "days": days,
        "limit": clean_limit,
        "auto_limits": {
            "auto_run": auto_run,
            "materialize_limit": materialize_limit if materialize else 0,
            "agentization_ai": bool(settings.LEARNING_AUTO_AGENTIZATION_AI_ENABLED) if auto_run else True,
            "training_enabled": training_enabled,
            "training_due": training_due,
            "training_interval_seconds": training_gate.get("training_interval_seconds"),
            "training_days": training_days,
            "latest_training_at": training_gate.get("latest_training_at"),
            "training_next_after": training_gate.get("training_next_after"),
            "self_audit_ai": bool(settings.LEARNING_AUTO_SELF_AUDIT_AI_ENABLED) if auto_run else True,
            "self_audit_remediate": bool(settings.LEARNING_AUTO_SELF_AUDIT_REMEDIATE_ENABLED) if auto_run else True,
        },
    }


async def learning_automation_status(
    db: AsyncSession,
    user: User,
    *,
    days: int = 7,
) -> dict[str, Any]:
    """Return backlog metrics for the automatic learning-flow reconciler."""
    clean_days = _bounded_days(days, default=7)
    cutoff = now_bjt() - timedelta(days=clean_days)
    artifact_scope = _artifact_filter(user, created_after=cutoff)
    job_scope = _ingestion_job_filter(user, created_after=cutoff)
    event_scope = _event_filter(user, created_after=cutoff)
    auto_actions = {"auto_index", "training_candidate_only", "improvement_candidate", "agent_memory_only"}
    action_expr = sa.literal_column("learning_artifacts.policy_result_json ->> 'action'")
    ready_rows = (
        await db.execute(
            select(action_expr.label("action"), func.count(LearningArtifact.id))
            .select_from(LearningArtifact)
            .where(artifact_scope, LearningArtifact.status == "ready")
            .group_by(action_expr)
        )
    ).all()
    ready_by_action = {str(action or "unknown"): int(count or 0) for action, count in ready_rows}
    auto_materializable = sum(count for action, count in ready_by_action.items() if action in auto_actions)
    job_rows = (
        await db.execute(
            select(LearningIngestionJob.status, func.count(LearningIngestionJob.id))
            .where(job_scope)
            .group_by(LearningIngestionJob.status)
        )
    ).all()
    jobs_by_status = {str(status or "unknown"): int(count or 0) for status, count in job_rows}
    governance_scope = and_(
        _scope_filter(LearningGovernanceTask, user),
        LearningGovernanceTask.created_at >= cutoff,
    )
    governance_rows = (
        await db.execute(
            select(LearningGovernanceTask.status, func.count(LearningGovernanceTask.id))
            .where(governance_scope)
            .group_by(LearningGovernanceTask.status)
        )
    ).all()
    governance_by_status = {str(status or "unknown"): int(count or 0) for status, count in governance_rows}
    governance_pending = sum(governance_by_status.get(key, 0) for key in ("pending", "in_progress"))
    stale_governance_tasks = int((
        await db.execute(
            select(func.count(LearningGovernanceTask.id)).where(
                governance_scope,
                LearningGovernanceTask.status.in_(["pending", "in_progress"]),
                LearningGovernanceTask.created_at <= now_bjt() - timedelta(hours=LEARNING_GOVERNANCE_TASK_STALE_HOURS),
            )
        )
    ).scalar() or 0)
    terminal_governance_rows = (
        await db.execute(
            select(LearningGovernanceTask)
            .where(
                governance_scope,
                LearningGovernanceTask.status.in_(["completed", "rejected", "cancelled"]),
            )
            .order_by(LearningGovernanceTask.updated_at.desc(), LearningGovernanceTask.created_at.desc())
            .limit(200)
        )
    ).scalars().all()
    governance_decisionless = 0
    for task in terminal_governance_rows:
        if _safe_dict(task.payload_json).get("decision"):
            continue
        candidate = await db.get(ImprovementCandidate, task.candidate_id)
        if candidate is None or candidate.status in {"open", "reviewing"}:
            governance_decisionless += 1
    run_status_rows = (
        await db.execute(
            select(LearningAutomationRun.status, func.count(LearningAutomationRun.id))
            .where(LearningAutomationRun.started_at >= cutoff)
            .group_by(LearningAutomationRun.status)
        )
    ).all()
    automation_runs_by_status = {str(status or "unknown"): int(count or 0) for status, count in run_status_rows}
    latest_automation_success_at = (
        await db.execute(
            select(func.max(LearningAutomationRun.finished_at)).where(
                LearningAutomationRun.status == "succeeded",
            )
        )
    ).scalar()
    latest_event_at = (
        await db.execute(select(func.max(LearningEvent.created_at)).where(event_scope))
    ).scalar()
    latest_job_at = (
        await db.execute(select(func.max(LearningIngestionJob.updated_at)).where(job_scope))
    ).scalar()
    run_rows = (
        await db.execute(
            select(LearningAutomationRun)
            .where(LearningAutomationRun.started_at >= cutoff)
            .order_by(LearningAutomationRun.started_at.desc())
            .limit(8)
        )
    ).scalars().all()
    return {
        "days": clean_days,
        "generated_at": _iso(now_bjt()),
        "backlog": {
            "ready_by_action": ready_by_action,
            "auto_materializable": auto_materializable,
            "review_required": ready_by_action.get("review_first", 0),
            "governance_tasks_by_status": governance_by_status,
            "governance_pending": governance_pending,
            "governance_stale": stale_governance_tasks,
            "governance_decisionless": governance_decisionless,
            "governance_stale_threshold_hours": LEARNING_GOVERNANCE_TASK_STALE_HOURS,
            "automation_runs_by_status": automation_runs_by_status,
            "automation_failed": automation_runs_by_status.get("failed", 0),
            "automation_latest_success_at": _iso(latest_automation_success_at),
            "automation_stale": bool(
                (auto_materializable or jobs_by_status.get("failed", 0) or governance_pending or governance_decisionless)
                and (
                    latest_automation_success_at is None
                    or latest_automation_success_at <= now_bjt() - timedelta(hours=LEARNING_AUTOMATION_RUN_STALE_HOURS)
                )
            ),
            "automation_stale_threshold_hours": LEARNING_AUTOMATION_RUN_STALE_HOURS,
        },
        "jobs": {"by_status": jobs_by_status},
        "latest": {
            "event_at": _iso(latest_event_at),
            "job_at": _iso(latest_job_at),
            "automation_run": _automation_run_to_dict(run_rows[0]) if run_rows else None,
        },
        "automation_runs": [_automation_run_to_dict(row) for row in run_rows],
        "governance": {
            "scope": "global" if _is_global_user(user) else "department",
            "department": None if _is_global_user(user) else _user_department(user),
            "raw_payload_returned": False,
        },
    }


async def _capture_system_training_source_rows(
    db: AsyncSession,
    user: User,
    *,
    days: int,
    per_source_limit: int,
) -> dict[str, Any]:
    """Reconcile governed learning events from trainable system sources."""
    clean_limit = max(
        1,
        min(int(per_source_limit or LEARNING_SYSTEM_TRAINING_BACKFILL_MAX_PER_SOURCE), LEARNING_SYSTEM_TRAINING_BACKFILL_MAX_PER_SOURCE),
    )
    clean_days = max(1, int(days or 3650))
    cutoff = now_bjt() - timedelta(days=clean_days)
    captured: dict[str, int] = {
        "execution_run": 0,
        "intelligence_analyze_run": 0,
        "decision_log": 0,
        "ai_todo": 0,
        "todo_dispatch_task": 0,
        "project_ingress_event": 0,
    }
    errors: list[dict[str, Any]] = []

    def missing_event_condition(source_type: str, source_id_expr: Any):
        existing = (
            select(LearningEvent.id)
            .where(
                LearningEvent.source_type == source_type,
                LearningEvent.source_id == sa.cast(source_id_expr, sa.String),
            )
            .limit(1)
        )
        return ~existing.exists()

    async def capture_rows(key: str, stmt, handler) -> None:
        rows = (await db.execute(stmt.limit(clean_limit))).scalars().all()
        count = 0
        for row in rows:
            try:
                await handler(db, row)
                count += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({
                    "source": key,
                    "source_id": str(getattr(row, "id", "") or getattr(row, "request_id", "") or "")[:100],
                    "reason": exc.__class__.__name__,
                    "detail": _clip(exc, 800),
                })
        captured[key] = count

    await capture_rows(
        "execution_run",
        select(ExecutionRun)
        .where(ExecutionRun.started_at >= cutoff)
        .where(ExecutionRun.status.in_(["completed", "failed", "timeout"]))
        .where(or_(ExecutionRun.run_mode.is_(None), ExecutionRun.run_mode.not_in(sorted(SAMPLE_RUN_MODES))))
        .where(_scope_filter(ExecutionRun, user))
        .where(missing_event_condition("execution_run", ExecutionRun.id))
        .order_by(ExecutionRun.started_at.asc(), ExecutionRun.id.asc()),
        capture_execution_run,
    )

    analyze_rows = (
        await db.execute(
            select(IntelligenceAnalyzeRun, IntelligenceAnalyzeCache.output_payload)
            .outerjoin(IntelligenceAnalyzeCache, IntelligenceAnalyzeRun.cache_id == IntelligenceAnalyzeCache.id)
            .where(IntelligenceAnalyzeRun.created_at >= cutoff)
            .where(_scope_filter(IntelligenceAnalyzeRun, user))
            .where(missing_event_condition("intelligence_analyze_run", IntelligenceAnalyzeRun.id))
            .order_by(IntelligenceAnalyzeRun.created_at.asc(), IntelligenceAnalyzeRun.id.asc())
            .limit(clean_limit)
        )
    ).all()
    analyze_count = 0
    for row, output_payload in analyze_rows:
        try:
            await capture_intelligence_analyze_run(db, row, output_payload=output_payload)
            analyze_count += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "source": "intelligence_analyze_run",
                "source_id": str(getattr(row, "id", "") or "")[:100],
                "reason": exc.__class__.__name__,
                "detail": _clip(exc, 800),
            })
    captured["intelligence_analyze_run"] = analyze_count

    await capture_rows(
        "decision_log",
        select(DecisionLog)
        .where(DecisionLog.created_at >= cutoff)
        .where(or_(DecisionLog.is_sandbox.is_(None), DecisionLog.is_sandbox == sa.false()))
        .where(_scope_filter(DecisionLog, user))
        .where(missing_event_condition("decision_log", DecisionLog.id))
        .order_by(DecisionLog.created_at.asc(), DecisionLog.id.asc()),
        capture_decision_log,
    )
    await capture_rows(
        "ai_todo",
        select(AITodo)
        .where(or_(AITodo.created_at >= cutoff, AITodo.updated_at >= cutoff, AITodo.decided_at >= cutoff))
        .where(AITodo.status.in_(["approved", "rejected"]))
        .where(_scope_filter(AITodo, user))
        .where(missing_event_condition("ai_todo", AITodo.id))
        .order_by(AITodo.created_at.asc(), AITodo.id.asc()),
        capture_ai_todo,
    )
    await capture_rows(
        "todo_dispatch_task",
        select(TodoDispatchTask)
        .where(or_(
            TodoDispatchTask.created_at >= cutoff,
            TodoDispatchTask.updated_at >= cutoff,
            TodoDispatchTask.dispatched_at >= cutoff,
            TodoDispatchTask.started_at >= cutoff,
            TodoDispatchTask.ack_at >= cutoff,
        ))
        .where(TodoDispatchTask.status == "done")
        .where(_scope_filter(TodoDispatchTask, user))
        .where(missing_event_condition("todo_dispatch_task", TodoDispatchTask.id))
        .order_by(TodoDispatchTask.created_at.asc(), TodoDispatchTask.id.asc()),
        capture_todo_dispatch_task,
    )

    from app.projects.models import ProjectIngressEvent

    await capture_rows(
        "project_ingress_event",
        select(ProjectIngressEvent)
        .where(ProjectIngressEvent.created_at >= cutoff)
        .where(ProjectIngressEvent.event_type != "input")
        .where(missing_event_condition("project_ingress_event", ProjectIngressEvent.id))
        .order_by(ProjectIngressEvent.created_at.asc(), ProjectIngressEvent.id.asc()),
        capture_project_ingress_event,
    )

    return {
        "days": clean_days,
        "cutoff": _iso(cutoff),
        "per_source_limit": clean_limit,
        "capture_mode": "trainable_sources",
        "deferred_auxiliary_sources": [
            "execution_artifact",
            "codex_mcp_call_audit",
            "knowledge_query_log",
            "training_job",
            "model_deployment",
            "decision_request",
            "project_run",
            "project_capability_call",
        ],
        "captured": captured,
        "total_captured": sum(captured.values()),
        "errors": errors[:50],
        "error_count": len(errors),
    }


async def _materialize_system_training_artifacts(
    db: AsyncSession,
    user: User,
    *,
    limit: int,
) -> dict[str, Any]:
    """Materialize ready training artifacts so full-history selectors can train from them."""
    clean_limit = max(1, min(int(limit or LEARNING_SYSTEM_TRAINING_MATERIALIZE_MAX), LEARNING_SYSTEM_TRAINING_MATERIALIZE_MAX))
    rows = (
        await db.execute(
            select(
                LearningArtifact.id,
                LearningArtifact.artifact_kind,
                LearningArtifact.department,
                LearningArtifact.org_unit_id,
                LearningArtifact.skill_id,
                LearningArtifact.run_id,
            )
            .where(
                _scope_filter(LearningArtifact, user),
                LearningArtifact.artifact_kind.in_(sorted(TRAINING_ARTIFACTS)),
                LearningArtifact.status == "ready",
            )
            .order_by(LearningArtifact.created_at.asc(), LearningArtifact.id.asc())
            .limit(clean_limit)
        )
    ).all()
    materialized = len(rows)
    errors: list[dict[str, Any]] = []
    by_kind: dict[str, int] = {}
    if not rows:
        return {
            "checked": 0,
            "materialized": 0,
            "by_kind": {},
            "errors": [],
            "error_count": 0,
            "limit": clean_limit,
            "mode": "bulk",
        }

    artifact_ids = [row.id for row in rows]
    for row in rows:
        by_kind[row.artifact_kind] = by_kind.get(row.artifact_kind, 0) + 1

    try:
        for start in range(0, len(artifact_ids), 5000):
            await db.execute(
                sa.update(LearningArtifact)
                .where(LearningArtifact.id.in_(artifact_ids[start:start + 5000]))
                .values(
                    status="materialized",
                    sink_type="training_sample",
                    sink_id=LearningArtifact.id,
                    updated_at=now_bjt(),
                )
            )
        dialect_name = str(db.get_bind().dialect.name)
        edge_values = [
            {
                "from_type": "learning_artifact",
                "from_id": row.id,
                "to_type": "training_sample",
                "to_id": row.id,
                "relation": "created_sample",
                "department": row.department,
                "org_unit_id": row.org_unit_id,
                "skill_id": row.skill_id,
                "run_id": row.run_id,
                "weight": 1.0,
                "status": "active",
                "metadata_json": {"materialized_by": "system_full_training_flow"},
                "created_at": now_bjt(),
            }
            for row in rows
        ]
        if dialect_name == "postgresql":
            for start in range(0, len(edge_values), 1000):
                stmt = (
                    pg_insert(LearningFlowEdge)
                    .values(edge_values[start:start + 1000])
                    .on_conflict_do_nothing(
                        constraint="uq_learning_flow_edge",
                    )
                )
                await db.execute(stmt)
        else:
            for row in rows:
                await _upsert_edge(
                    db,
                    from_type="learning_artifact",
                    from_id=row.id,
                    to_type="training_sample",
                    to_id=row.id,
                    relation="created_sample",
                    department=row.department,
                    org_unit_id=row.org_unit_id,
                    skill_id=row.skill_id,
                    run_id=row.run_id,
                    metadata={"materialized_by": "system_full_training_flow"},
                )
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        materialized = 0
        errors.append({
            "artifact_id": "*",
            "artifact_kind": "training_sample",
            "reason": exc.__class__.__name__,
            "detail": _clip(exc, 800),
        })
    return {
        "checked": len(rows),
        "materialized": materialized,
        "by_kind": by_kind,
        "errors": errors[:50],
        "error_count": len(errors),
        "limit": clean_limit,
        "mode": "bulk",
    }


async def run_system_full_training_flow(
    db: AsyncSession,
    user: User,
    *,
    days: int = 3650,
    per_source_limit: int = LEARNING_SYSTEM_TRAINING_BACKFILL_MAX_PER_SOURCE,
    min_sample_count: int = 4,
    advance: bool = True,
    max_jobs: int = 3,
    capture_sources: bool = False,
) -> dict[str, Any]:
    """Capture all governed system data, create a full-history job, then advance training."""
    if not _is_global_user(user):
        raise AppError("FORBIDDEN", 403, {"detail": "system full training requires global admin access"})
    clean_days = max(1, min(int(days or 3650), 3650))
    clean_limit = max(1, min(int(per_source_limit or LEARNING_SYSTEM_TRAINING_BACKFILL_MAX_PER_SOURCE), LEARNING_SYSTEM_TRAINING_BACKFILL_MAX_PER_SOURCE))
    clean_jobs = max(1, min(int(max_jobs or 3), 20))

    materialized = await _materialize_system_training_artifacts(
        db,
        user,
        limit=LEARNING_SYSTEM_TRAINING_MATERIALIZE_MAX,
    )
    captured = {
        "skipped": True,
        "reason": "training_first_mode",
        "capture_mode": "not_blocking_training",
        "captured": {},
        "total_captured": 0,
        "errors": [],
        "error_count": 0,
    }
    if capture_sources:
        captured = await _capture_system_training_source_rows(
            db,
            user,
            days=clean_days,
            per_source_limit=clean_limit,
        )
    training_candidate = await ensure_full_history_platform_training_candidate(
        db,
        user,
        min_sample_count=min_sample_count,
    )
    training_automation = None
    if advance:
        training_automation = await advance_learning_training_automation(
            db,
            user,
            days=clean_days,
            max_jobs=clean_jobs,
        )
    await db.flush()
    return {
        "captured": captured,
        "materialized": materialized,
        "training_candidate": training_candidate,
        "training_automation": training_automation,
        "limits": {
            "days": clean_days,
            "per_source_limit": clean_limit,
            "materialize_limit": LEARNING_SYSTEM_TRAINING_MATERIALIZE_MAX,
            "max_jobs": clean_jobs,
            "advanced": bool(advance),
            "capture_sources": bool(capture_sources),
        },
        "governance": {
            "scope": "platform",
            "requires_global_admin": True,
            "uses_review_state_machine": True,
            "raw_payload_returned": False,
        },
    }


async def run_learning_automation_once(
    db: AsyncSession,
    user: User,
    *,
    days: int = 7,
    limit: int = 300,
    materialize: bool = True,
    trigger_type: str = "manual",
    created_by: str | None = None,
) -> dict[str, Any]:
    """Run one governed capture/materialize cycle and persist its trace."""
    clean_days = _bounded_days(days, default=7)
    clean_limit = max(1, min(int(limit or 300), EVENT_LIMIT_MAX))
    started = now_bjt()
    stale_run_count = 0
    stale_minutes = max(5, min(int(settings.LEARNING_AUTO_RUN_STALE_MINUTES or 20), 24 * 60))
    if str(trigger_type or "") == "scheduled":
        stale_cutoff = started - timedelta(minutes=stale_minutes)
        stale_rows = (
            await db.execute(
                select(LearningAutomationRun)
                .where(LearningAutomationRun.status == "running")
                .where(LearningAutomationRun.started_at <= stale_cutoff)
                .where(LearningAutomationRun.finished_at.is_(None))
                .order_by(LearningAutomationRun.started_at.asc())
                .limit(50)
            )
        ).scalars().all()
        for stale_row in stale_rows:
            stale_row.status = "failed"
            stale_row.error = f"stale scheduled automation run exceeded {stale_minutes} minutes"
            stale_row.result_json = {
                **_safe_dict(stale_row.result_json),
                "error": stale_row.error,
                "stale_marked_by": "learning_auto_flow",
            }
            stale_row.finished_at = started
            stale_row.updated_at = started
            stale_run_count += 1
    row = LearningAutomationRun(
        id=_new_id("lar"),
        trigger_type=str(trigger_type or "manual")[:30],
        status="running",
        days=clean_days,
        limit=clean_limit,
        materialize=bool(materialize),
        created_by=created_by or str(getattr(user, "id", "") or ""),
        started_at=started,
        created_at=started,
        updated_at=started,
        metadata_json={
            "scope": "global" if _is_global_user(user) else "department",
            "department": None if _is_global_user(user) else _user_department(user),
            "stale_runs_marked_failed": stale_run_count,
            "stale_run_threshold_minutes": stale_minutes,
        },
    )
    db.add(row)
    await db.flush()
    try:
        result = await backfill_learning_events(
            db,
            user,
            days=clean_days,
            limit=clean_limit,
            materialize=bool(materialize),
        )
        row.status = "succeeded"
        row.captured_json = _safe_dict(result.get("captured"))
        row.result_json = result
        row.error = None
    except Exception as exc:  # noqa: BLE001
        row.status = "failed"
        row.error = _clip(exc, 2000)
        row.result_json = {"error": row.error}
    row.finished_at = now_bjt()
    row.updated_at = row.finished_at
    await db.flush()
    return {"status": row.status, "run": _automation_run_to_dict(row), "result": row.result_json}


def _is_learning_auto_training_job(job: TrainingJob | None) -> bool:
    if job is None:
        return False
    spec = _safe_dict(getattr(job, "spec_json", None))
    dataset = _safe_dict(spec.get("dataset"))
    lineage = _safe_dict(spec.get("lineage"))
    created_by = str(getattr(job, "created_by", "") or "")
    strategy = str(getattr(job, "training_strategy", "") or "")
    manifest_from_learning = bool(
        dataset.get("manifest_hash")
        and lineage.get("source") == "learning_artifacts"
    )
    return bool(
        created_by == "learning_auto_flow"
        or spec.get("source") == "learning_sample_threshold"
        or manifest_from_learning
        or strategy == "learning_sample_threshold"
    )


def _job_daily_training_window(job: TrainingJob | None) -> dict[str, Any]:
    if job is None:
        return {}
    spec = _safe_dict(getattr(job, "spec_json", None))
    dataset = _safe_dict(spec.get("dataset"))
    window = _safe_dict(dataset.get("window"))
    if not window:
        window = _safe_dict(spec.get("training_window"))
    return window


def _is_daily_incremental_training_job(job: TrainingJob | None) -> bool:
    if not _is_learning_auto_training_job(job):
        return False
    spec = _safe_dict(getattr(job, "spec_json", None))
    governance = _safe_dict(spec.get("governance"))
    dataset = _safe_dict(spec.get("dataset"))
    window = _safe_dict(dataset.get("window"))
    return bool(
        spec.get("source") == "learning_sample_threshold"
        and governance.get("auto_training_agent") == LEARNING_AUTO_TRAINING_AGENT_ID
        and governance.get("incremental_training") is True
        and window.get("mode") == "yesterday"
        and window.get("date")
    )


def _is_full_history_incremental_training_job(job: TrainingJob | None) -> bool:
    if not _is_learning_auto_training_job(job):
        return False
    spec = _safe_dict(getattr(job, "spec_json", None))
    governance = _safe_dict(spec.get("governance"))
    dataset = _safe_dict(spec.get("dataset"))
    window = _safe_dict(dataset.get("window"))
    return bool(
        spec.get("source") == "learning_sample_threshold"
        and governance.get("auto_training_agent") == LEARNING_AUTO_TRAINING_AGENT_ID
        and governance.get("incremental_training") is True
        and spec.get("training_mode") == "full_history_incremental"
        and window.get("mode") == "full_history"
    )


def _is_auto_incremental_training_job(job: TrainingJob | None) -> bool:
    return _is_daily_incremental_training_job(job) or _is_full_history_incremental_training_job(job)


def _default_training_route_priority(job: TrainingJob | None) -> int:
    if job is None:
        return 1
    spec = _safe_dict(getattr(job, "spec_json", None))
    model = _safe_dict(spec.get("model"))
    deployment = _safe_dict(spec.get("deployment"))
    if (
        str(model.get("profile") or "").strip() == LEARNING_DEFAULT_TRAINING_MODEL_PROFILE
        and str(deployment.get("deployment_target_gateway_id") or "").strip() == LEARNING_DEFAULT_DEPLOYMENT_GATEWAY_ID
        and str(deployment.get("deployment_runtime_profile") or "").strip() == LEARNING_DEFAULT_DEPLOYMENT_RUNTIME_PROFILE
    ):
        return 0
    return 1


def _training_mode_priority(job: TrainingJob | None) -> int:
    if _is_full_history_incremental_training_job(job):
        return 0
    if _is_daily_incremental_training_job(job):
        return 1
    return 2


def _training_sample_total(job: TrainingJob | None) -> int:
    spec = _safe_dict(getattr(job, "spec_json", None))
    dataset = _safe_dict(spec.get("dataset"))
    try:
        return int(dataset.get("sample_total") or 0)
    except (TypeError, ValueError):
        return 0


def _training_model_request_from_job(job: TrainingJob | None) -> dict[str, Any]:
    spec = _safe_dict(getattr(job, "spec_json", None))
    model = _safe_dict(spec.get("model"))
    profile = str(model.get("profile") or "").strip()
    if not profile:
        return {}
    payload = {
        "profile": profile,
        "source": str(model.get("source") or ("internal" if profile == LEARNING_DEFAULT_TRAINING_MODEL_PROFILE else "auto")).strip(),
    }
    for key in (
        "internal_model_ref",
        "internalModelRef",
        "model_path",
        "modelPath",
        "artifact_uri",
        "artifactUri",
        "revision",
        "modelscope_revision",
        "bootstrap_profile",
    ):
        value = model.get(key)
        if value not in (None, "", [], {}):
            payload[key] = value
    return payload


def _first_training_model_source_candidate(model_status: dict[str, Any]) -> str | None:
    for item in _safe_list(model_status.get("source_candidates")):
        candidate = _safe_dict(item)
        path = str(candidate.get("path") or "").strip()
        if path.startswith("/"):
            return path
    return None


def _first_training_model_train_ready_candidate(discover_all: dict[str, Any]) -> str | None:
    for item in _safe_list(discover_all.get("train_ready_candidates")):
        candidate = _safe_dict(item)
        path = str(candidate.get("path") or "").strip()
        if path.startswith("/") and candidate.get("train_ready") is not False:
            return path
    return None


def _first_training_model_transfer_candidate(discover_all: dict[str, Any]) -> dict[str, Any] | None:
    for item in _safe_list(discover_all.get("found_but_not_train_ready")):
        candidate = _safe_dict(item)
        path = str(candidate.get("path") or "").strip()
        gateway_id = str(candidate.get("gateway_id") or candidate.get("source_gateway_id") or "").strip()
        if path.startswith("/") and gateway_id:
            return {
                "path": path,
                "gateway_id": gateway_id,
                "source_gateway_id": str(candidate.get("source_gateway_id") or gateway_id).strip(),
                "score": candidate.get("score"),
            }
    return None


async def _auto_training_model_dispatch_readiness(
    db: AsyncSession,
    job: TrainingJob,
    reviewer: Any,
    training_service: Any,
) -> tuple[bool, str, dict[str, Any]]:
    payload = _training_model_request_from_job(job)
    if not payload:
        return True, "model_preflight_not_required", {}
    gateway_id = str(getattr(job, "target_gateway_id", "") or "").strip()
    if not gateway_id:
        return False, "missing_training_gateway", {"profile": payload.get("profile")}
    try:
        status_payload = await training_service.get_training_gateway_model_status(
            db,
            reviewer,
            gateway_id,
            payload,
        )
    except AppError as exc:
        return False, str(exc.code or "training_model_status_failed"), _redacted_payload({
            "target_gateway_id": gateway_id,
            "profile": payload.get("profile"),
            "detail": exc.detail,
        }, 1000)
    except Exception as exc:  # noqa: BLE001
        return False, "training_model_status_failed", {
            "target_gateway_id": gateway_id,
            "profile": payload.get("profile"),
            "detail": _clip(exc, 1000),
        }
    model_status = _safe_dict(_safe_dict(status_payload).get("model"))
    ready_statuses = set(getattr(training_service, "TRAINING_DEPLOYMENT_MODEL_READY_STATUSES", set()) or set())
    status = str(model_status.get("status") or "").strip().lower()
    if status in ready_statuses or model_status.get("exists") is True:
        return True, "training_model_ready", {
            "target_gateway_id": gateway_id,
            "profile": payload.get("profile"),
            "status": status,
            "exists": model_status.get("exists"),
            "model_dir": model_status.get("model_dir"),
        }
    if model_status.get("source_exists") is not True and not _first_training_model_source_candidate(model_status):
        discover_all_fn = getattr(training_service, "discover_training_models_across_gateways", None)
        if callable(discover_all_fn):
            try:
                discover_all_payload = await discover_all_fn(
                    db,
                    reviewer,
                    {
                        **payload,
                        "training_gateway_id": gateway_id,
                        "auto_discover": True,
                        "include_metadata": True,
                        "limit": 12,
                    },
                )
            except AppError as exc:
                model_status = {
                    **model_status,
                    "discover_all_error": {
                        "reason": str(exc.code or "training_model_discover_all_failed"),
                        "detail": exc.detail,
                    },
                }
            except Exception as exc:  # noqa: BLE001
                model_status = {
                    **model_status,
                    "discover_all_error": {
                        "reason": "training_model_discover_all_failed",
                        "detail": _clip(exc, 1000),
                    },
                }
            else:
                discover_all = _safe_dict(discover_all_payload)
                candidate_path = _first_training_model_train_ready_candidate(discover_all)
                transfer_candidate = _first_training_model_transfer_candidate(discover_all) if not candidate_path else None
                model_status = {
                    **model_status,
                    "discover_all": {
                        "summary": discover_all.get("summary"),
                        "training_gateway_id": discover_all.get("training_gateway_id"),
                        "train_ready_candidates": discover_all.get("train_ready_candidates"),
                        "found_but_not_train_ready": discover_all.get("found_but_not_train_ready"),
                    },
                }
                if candidate_path:
                    model_status["source_candidates"] = [
                        {
                            "path": candidate_path,
                            "source": "discover_all",
                            "train_ready": True,
                        }
                    ]
                elif transfer_candidate is not None:
                    transfer_payload = {
                        **payload,
                        "training_gateway_id": gateway_id,
                        "source_gateway_id": transfer_candidate["source_gateway_id"],
                        "source_path": transfer_candidate["path"],
                        "validate_mode": "metadata",
                        "timeout_seconds": 21600,
                        "prepare": True,
                    }
                    start_transfer_fn = getattr(training_service, "start_training_model_transfer", None)
                    transfer_fn = start_transfer_fn if callable(start_transfer_fn) else getattr(
                        training_service,
                        "transfer_training_model_to_training_gateway",
                        None,
                    )
                    if callable(transfer_fn):
                        try:
                            transferred_payload = await transfer_fn(db, reviewer, transfer_payload)
                        except AppError as exc:
                            model_status = {
                                **model_status,
                                "transfer_error": {
                                    "reason": str(exc.code or "training_model_transfer_failed"),
                                    "detail": exc.detail,
                                    "candidate": transfer_candidate,
                                },
                            }
                        except Exception as exc:  # noqa: BLE001
                            model_status = {
                                **model_status,
                                "transfer_error": {
                                    "reason": "training_model_transfer_failed",
                                    "detail": _clip(exc, 1000),
                                    "candidate": transfer_candidate,
                                },
                            }
                        else:
                            transferred = _safe_dict(transferred_payload)
                            transfer_state = str(transferred.get("status") or "").strip().lower()
                            transfer_id = str(transferred.get("transfer_id") or transferred.get("id") or "").strip()
                            if transfer_id and callable(start_transfer_fn):
                                kickoff_fn = getattr(training_service, "kickoff_training_model_transfer", None)
                                if callable(kickoff_fn) and transfer_state in {"queued", "running"}:
                                    kickoff_fn(transfer_id)
                            if transfer_state in {"queued", "running"}:
                                return False, "training_model_transfer_pending", {
                                    "target_gateway_id": gateway_id,
                                    "profile": payload.get("profile"),
                                    "transfer_id": transfer_id or None,
                                    "status": transfer_state,
                                    "progress": transferred.get("progress"),
                                    "source_gateway_id": transferred.get("source_gateway_id"),
                                    "source_path": transferred.get("source_path"),
                                    "transferred_from_candidate": True,
                                }
                            if transfer_state in {"failed", "cancelled"}:
                                model_status = {
                                    **model_status,
                                    "transfer_status": {
                                        "transfer_id": transfer_id or None,
                                        "status": transfer_state,
                                        "error": transferred.get("error"),
                                        "source_gateway_id": transferred.get("source_gateway_id"),
                                        "source_path": transferred.get("source_path"),
                                    },
                                }
                                return False, "training_model_transfer_failed", _redacted_payload(model_status, 1000)
                            imported = _safe_dict(transferred.get("import"))
                            if not imported and isinstance(transferred.get("result"), dict):
                                imported = _safe_dict(_safe_dict(transferred.get("result")).get("import"))
                            prepared_status = _safe_dict(_safe_dict(transferred.get("prepare")).get("model"))
                            if not prepared_status and isinstance(transferred.get("result"), dict):
                                prepared_status = _safe_dict(_safe_dict(_safe_dict(transferred.get("result")).get("prepare")).get("model"))
                            prepared_state = str(prepared_status.get("status") or "").strip().lower()
                            internal_ref = str(
                                transferred.get("internal_model_ref")
                                or _safe_dict(transferred.get("result")).get("internal_model_ref")
                                or imported.get("internal_model_ref")
                                or prepared_status.get("source_path")
                                or ""
                            ).strip()
                            if prepared_state in ready_statuses or prepared_status.get("exists") is True:
                                return True, "training_model_transferred_prepared", {
                                    "target_gateway_id": gateway_id,
                                    "profile": payload.get("profile"),
                                    "status": prepared_state,
                                    "exists": prepared_status.get("exists"),
                                    "model_dir": prepared_status.get("model_dir"),
                                    "internal_model_ref": internal_ref,
                                    "source_gateway_id": transferred.get("source_gateway_id"),
                                    "source_path": transferred.get("source_path"),
                                    "transferred_from_candidate": True,
                                }
                            model_status = {
                                **model_status,
                                "transfer_status": {
                                    "status": transferred.get("status"),
                                    "source_gateway_id": transferred.get("source_gateway_id"),
                                    "source_path": transferred.get("source_path"),
                                    "internal_model_ref": internal_ref,
                                    "import_status": imported.get("status"),
                                    "prepare_status": {
                                        "status": prepared_state or None,
                                        "exists": prepared_status.get("exists"),
                                        "model_dir": prepared_status.get("model_dir"),
                                        "error": prepared_status.get("error"),
                                    },
                                },
                            }
    if model_status.get("source_exists") is True:
        prepare_payload = {
            **payload,
            "validate_mode": "metadata",
            "timeout_seconds": 180,
            "auto_discover": True,
        }
        try:
            prepared_payload = await training_service.prepare_training_gateway_model(
                db,
                reviewer,
                gateway_id,
                prepare_payload,
            )
        except AppError as exc:
            return False, str(exc.code or "training_model_prepare_failed"), _redacted_payload({
                "target_gateway_id": gateway_id,
                "profile": payload.get("profile"),
                "source_exists": True,
                "detail": exc.detail,
            }, 1000)
        except Exception as exc:  # noqa: BLE001
            return False, "training_model_prepare_failed", {
                "target_gateway_id": gateway_id,
                "profile": payload.get("profile"),
                "source_exists": True,
                "detail": _clip(exc, 1000),
            }
        prepared_status = _safe_dict(_safe_dict(prepared_payload).get("model"))
        prepared_state = str(prepared_status.get("status") or "").strip().lower()
        if prepared_state in ready_statuses or prepared_status.get("exists") is True:
            return True, "training_model_prepared", {
                "target_gateway_id": gateway_id,
                "profile": payload.get("profile"),
                "status": prepared_state,
                "exists": prepared_status.get("exists"),
                "model_dir": prepared_status.get("model_dir"),
                "source_exists": True,
            }
        model_status = {
            **model_status,
            "prepare_status": {
                "status": prepared_state or None,
                "exists": prepared_status.get("exists"),
                "model_dir": prepared_status.get("model_dir"),
                "error": prepared_status.get("error"),
            },
        }
    elif candidate_path := _first_training_model_source_candidate(model_status):
        try:
            configured_payload = await training_service.configure_training_gateway_runtime(
                db,
                reviewer,
                gateway_id,
                {"qwen36_35b_model_dir": candidate_path},
            )
        except AppError as exc:
            return False, str(exc.code or "training_model_candidate_config_failed"), _redacted_payload({
                "target_gateway_id": gateway_id,
                "profile": payload.get("profile"),
                "candidate_path": candidate_path,
                "detail": exc.detail,
            }, 1000)
        except Exception as exc:  # noqa: BLE001
            return False, "training_model_candidate_config_failed", {
                "target_gateway_id": gateway_id,
                "profile": payload.get("profile"),
                "candidate_path": candidate_path,
                "detail": _clip(exc, 1000),
            }
        configured_status = _safe_dict(_safe_dict(configured_payload).get("model"))
        if configured_status.get("source_exists") is True:
            prepare_payload = {
                **payload,
                "internal_model_ref": candidate_path,
                "validate_mode": "metadata",
                "timeout_seconds": 180,
                "auto_discover": True,
            }
            try:
                prepared_payload = await training_service.prepare_training_gateway_model(
                    db,
                    reviewer,
                    gateway_id,
                    prepare_payload,
                )
            except AppError as exc:
                return False, str(exc.code or "training_model_prepare_failed"), _redacted_payload({
                    "target_gateway_id": gateway_id,
                    "profile": payload.get("profile"),
                    "candidate_path": candidate_path,
                    "source_exists": True,
                    "detail": exc.detail,
                }, 1000)
            except Exception as exc:  # noqa: BLE001
                return False, "training_model_prepare_failed", {
                    "target_gateway_id": gateway_id,
                    "profile": payload.get("profile"),
                    "candidate_path": candidate_path,
                    "source_exists": True,
                    "detail": _clip(exc, 1000),
                }
            prepared_status = _safe_dict(_safe_dict(prepared_payload).get("model"))
            prepared_state = str(prepared_status.get("status") or "").strip().lower()
            if prepared_state in ready_statuses or prepared_status.get("exists") is True:
                return True, "training_model_candidate_configured", {
                    "target_gateway_id": gateway_id,
                    "profile": payload.get("profile"),
                    "status": prepared_state,
                    "exists": prepared_status.get("exists"),
                    "model_dir": prepared_status.get("model_dir"),
                    "source_exists": True,
                    "source_path": candidate_path,
                    "configured_from_candidate": True,
                }
            model_status = {
                **configured_status,
                "candidate_path": candidate_path,
                "configured_from_candidate": True,
                "prepare_status": {
                    "status": prepared_state or None,
                    "exists": prepared_status.get("exists"),
                    "model_dir": prepared_status.get("model_dir"),
                    "error": prepared_status.get("error"),
                },
            }
    elif model_status.get("source_exists") is not True:
        prepare_payload = {
            **payload,
            "validate_mode": "metadata",
            "timeout_seconds": 180,
            "auto_discover": True,
        }
        try:
            prepared_payload = await training_service.prepare_training_gateway_model(
                db,
                reviewer,
                gateway_id,
                prepare_payload,
            )
        except AppError as exc:
            return False, str(exc.code or "training_model_auto_discover_failed"), _redacted_payload({
                "target_gateway_id": gateway_id,
                "profile": payload.get("profile"),
                "source_exists": model_status.get("source_exists"),
                "scan_summary": model_status.get("scan_summary"),
                "candidate_roots": model_status.get("candidate_roots"),
                "detail": exc.detail,
            }, 1000)
        except Exception as exc:  # noqa: BLE001
            return False, "training_model_auto_discover_failed", {
                "target_gateway_id": gateway_id,
                "profile": payload.get("profile"),
                "source_exists": model_status.get("source_exists"),
                "scan_summary": model_status.get("scan_summary"),
                "candidate_roots": model_status.get("candidate_roots"),
                "detail": _clip(exc, 1000),
            }
        prepared_status = _safe_dict(_safe_dict(prepared_payload).get("model"))
        prepared_state = str(prepared_status.get("status") or "").strip().lower()
        if prepared_state in ready_statuses or prepared_status.get("exists") is True:
            return True, "training_model_auto_discovered", {
                "target_gateway_id": gateway_id,
                "profile": payload.get("profile"),
                "status": prepared_state,
                "exists": prepared_status.get("exists"),
                "model_dir": prepared_status.get("model_dir"),
                "source_exists": prepared_status.get("source_exists"),
                "source_path": prepared_status.get("source_path"),
                "internal_model_ref": prepared_status.get("internal_model_ref"),
                "auto_discover": True,
            }
        model_status = {
            **model_status,
            "source_candidates": prepared_status.get("source_candidates") or model_status.get("source_candidates"),
            "scan_summary": prepared_status.get("scan_summary") or model_status.get("scan_summary"),
            "candidate_roots": prepared_status.get("candidate_roots") or model_status.get("candidate_roots"),
            "discover": prepared_status.get("discover") or model_status.get("discover"),
            "auto_discover_prepare_status": {
                "status": prepared_state or None,
                "exists": prepared_status.get("exists"),
                "model_dir": prepared_status.get("model_dir"),
                "source_exists": prepared_status.get("source_exists"),
                "error": prepared_status.get("error"),
            },
        }
    return False, "training_model_not_ready", {
        "target_gateway_id": gateway_id,
        "profile": payload.get("profile"),
        "status": status or None,
        "exists": model_status.get("exists"),
        "model_dir": model_status.get("model_dir"),
        "source_exists": model_status.get("source_exists"),
        "source_candidates": model_status.get("source_candidates"),
        "scan_summary": model_status.get("scan_summary"),
        "candidate_roots": model_status.get("candidate_roots"),
        "discover_all": model_status.get("discover_all"),
        "discover_all_error": model_status.get("discover_all_error"),
        "configured_from_candidate": model_status.get("configured_from_candidate"),
        "candidate_path": model_status.get("candidate_path"),
        "internal_model_ref": model_status.get("internal_model_ref") or payload.get("internal_model_ref"),
        "error": model_status.get("error"),
        "prepare_status": model_status.get("prepare_status"),
        "auto_discover_prepare_status": model_status.get("auto_discover_prepare_status"),
    }


async def _latest_active_model_deployment_for_skill(
    db: AsyncSession,
    skill_id: str,
    *,
    department: str | None = None,
) -> TrainingModelDeployment | None:
    rows = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.status.in_(["active", "canary"]))
            .order_by(
                TrainingModelDeployment.updated_at.desc(),
                TrainingModelDeployment.created_at.desc(),
                TrainingModelDeployment.activated_at.desc().nullslast(),
                TrainingModelDeployment.id.desc(),
            )
            .limit(100)
        )
    ).scalars().all()
    clean_department = str(department or "").strip()
    matches: list[TrainingModelDeployment] = []
    for row in rows:
        target_ids = [str(item or "").strip() for item in (row.target_skill_ids_json or []) if str(item or "").strip()]
        if skill_id not in set(target_ids):
            continue
        matches.append(row)
    if clean_department:
        same_department = [row for row in matches if str(row.department or "").strip() == clean_department]
        if same_department:
            return same_department[0]
    return matches[0] if matches else None


def _parent_model_spec_from_deployment(deployment: TrainingModelDeployment | None) -> dict[str, Any] | None:
    if deployment is None:
        return None
    artifact_ref = _safe_dict(deployment.artifact_ref_json)
    source_uri = str(
        artifact_ref.get("source_uri")
        or artifact_ref.get("training_uri")
        or artifact_ref.get("source_path")
        or ""
    ).strip()
    artifact_uri = source_uri or str(artifact_ref.get("uri") or "").strip()
    return {
        "source": "active_model_deployment",
        "model_deployment_id": deployment.id,
        "job_id": deployment.job_id,
        "model_family": deployment.model_family,
        "artifact_id": deployment.artifact_id,
        "artifact_ref": artifact_ref,
        "artifact_uri": artifact_uri or None,
        "artifact_uri_source": "source_uri" if source_uri else "uri",
        "artifact_sha256": artifact_ref.get("sha256"),
        "status": deployment.status,
        "activated_at": _iso(deployment.activated_at),
    }


def _parent_model_usable_on_training_gateway(parent_model: dict[str, Any] | None, gateway_id: str | None) -> bool:
    if not parent_model:
        return False
    clean_gateway_id = str(gateway_id or "").strip()
    if not clean_gateway_id:
        return True
    artifact_ref = _safe_dict(parent_model.get("artifact_ref"))
    marker_values = {
        str(parent_model.get("gateway_id") or "").strip(),
        str(parent_model.get("worker_id") or "").strip(),
        str(artifact_ref.get("gateway_id") or "").strip(),
        str(artifact_ref.get("worker_id") or "").strip(),
        str(artifact_ref.get("training_gateway_id") or "").strip(),
        str(artifact_ref.get("source_gateway_id") or "").strip(),
    }
    marker_values.discard("")
    if not marker_values:
        return True
    return clean_gateway_id in marker_values


def _auto_training_agent_allows_job(job: TrainingJob, *, window_date: str | None = None) -> tuple[bool, str]:
    if not _is_auto_incremental_training_job(job):
        return False, "not_incremental_candidate"
    if str(getattr(job, "job_type", "") or "") not in {"lora", "qlora"}:
        return False, "unsupported_job_type"
    spec = _safe_dict(job.spec_json)
    dataset = _safe_dict(spec.get("dataset"))
    window = _safe_dict(dataset.get("window"))
    if _is_daily_incremental_training_job(job) and window_date and str(window.get("date") or "") != window_date:
        return False, "not_yesterday_window"
    try:
        sample_total = int(dataset.get("sample_total") or 0)
    except (TypeError, ValueError):
        sample_total = 0
    if sample_total < _auto_training_min_samples():
        return False, "below_training_sample_threshold"
    parent_model = _safe_dict(spec.get("parent_model"))
    if parent_model and not parent_model.get("artifact_uri"):
        return False, "parent_model_missing_artifact_uri"
    governance = _safe_dict(spec.get("governance"))
    if governance.get("raw_payload_returned") is not False:
        return False, "raw_payload_not_governed"
    return True, "eligible"


def _learning_training_reviewer_user(job: TrainingJob) -> Any:
    return type(
        "LearningTrainingReviewer",
        (),
        {
            "id": LEARNING_AUTO_TRAINING_AGENT_ID,
            "username": LEARNING_AUTO_TRAINING_AGENT_ID,
            "name": "学习训练审核 Agent",
            "role": "system_admin",
            "department": job.department,
            "can_view_all": True,
            "is_active": True,
            "state": "active",
        },
    )()


async def _dispatch_retry_gateway_readiness(
    db: AsyncSession,
    job: TrainingJob,
) -> tuple[bool, str, dict[str, Any]]:
    gateway_id = str(job.target_gateway_id or "").strip()
    if not gateway_id:
        return False, "missing_gateway", {}
    instance = await db.get(OpenClawInstance, gateway_id)
    if instance is None:
        return False, "gateway_not_found", {}
    if getattr(instance, "is_active", True) is False:
        return False, "gateway_inactive", {"gateway_id": gateway_id}
    contract = _training_agent_contract_from_instance(instance)
    if not contract.get("submission_ready"):
        return False, "gateway_not_submission_ready", contract
    try:
        from app.aiclaw.bridge_registry import bridge_registry

        online = bridge_registry.is_online(gateway_id)
    except Exception as exc:  # noqa: BLE001
        return False, "gateway_online_check_failed", {"detail": _clip(exc, 500), "contract": contract}
    if not online:
        return False, "gateway_offline", contract
    return True, "ready", contract


async def _active_training_job_for_skill(
    db: AsyncSession,
    job: TrainingJob,
) -> TrainingJob | None:
    skill_id = str(job.target_skill_id or "").strip()
    if not skill_id:
        return None
    return (
        await db.execute(
            select(TrainingJob)
            .where(TrainingJob.id != job.id)
            .where(TrainingJob.target_skill_id == skill_id)
            .where(
                or_(
                    TrainingJob.status.in_(["running", "evaluating", "unknown"]),
                    and_(
                        TrainingJob.status == "queued",
                        or_(
                            TrainingJob.failure_stage.is_(None),
                            TrainingJob.failure_stage.notin_(["dispatch", "model_preflight"]),
                        ),
                    ),
                )
            )
            .order_by(TrainingJob.updated_at.desc(), TrainingJob.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


def _training_gateway_concurrency_limit(contract: dict[str, Any] | None) -> int:
    data = contract or {}
    for key in ("worker_count", "gpu_count"):
        try:
            value = int(data.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return max(1, min(value, 16))
    return 1


async def _active_training_jobs_for_gateway(
    db: AsyncSession,
    gateway_id: str,
    *,
    exclude_job_id: str | None = None,
) -> list[TrainingJob]:
    clean_gateway_id = str(gateway_id or "").strip()
    if not clean_gateway_id:
        return []
    stmt = (
        select(TrainingJob)
        .where(TrainingJob.target_gateway_id == clean_gateway_id)
        .where(
            or_(
                TrainingJob.status.in_(["running", "evaluating", "unknown"]),
                and_(
                    TrainingJob.status == "queued",
                    or_(
                        TrainingJob.failure_stage.is_(None),
                        TrainingJob.failure_stage.notin_(["dispatch", "model_preflight"]),
                    ),
                ),
            )
        )
        .order_by(TrainingJob.updated_at.desc(), TrainingJob.created_at.desc(), TrainingJob.id.asc())
        .limit(20)
    )
    if exclude_job_id:
        stmt = stmt.where(TrainingJob.id != exclude_job_id)
    return (await db.execute(stmt)).scalars().all()


async def _auto_training_gateway_dispatch_slot(
    db: AsyncSession,
    job: TrainingJob,
    *,
    contract: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    gateway_id = str(job.target_gateway_id or "").strip()
    if not gateway_id:
        return True, "ready", {}
    if contract is None:
        instance = await db.get(OpenClawInstance, gateway_id)
        contract = _training_agent_contract_from_instance(instance)
    limit = _training_gateway_concurrency_limit(contract)
    active_jobs = await _active_training_jobs_for_gateway(db, gateway_id, exclude_job_id=job.id)
    if len(active_jobs) >= limit:
        return False, "gateway_training_capacity_busy", {
            "gateway_id": gateway_id,
            "limit": limit,
            "active_count": len(active_jobs),
            "active_job_ids": [row.id for row in active_jobs[:8]],
        }
    return True, "ready", {"gateway_id": gateway_id, "limit": limit, "active_count": len(active_jobs)}


def _training_evaluation_report_markdown(job: TrainingJob, latest_task: TrainingJobTask | None, deployment: TrainingModelDeployment | None) -> str:
    spec = _safe_dict(job.spec_json)
    dataset = _safe_dict(spec.get("dataset"))
    selector = _safe_dict(dataset.get("selector"))
    parent_model = _safe_dict(spec.get("parent_model"))
    metrics = _safe_dict(_safe_dict(getattr(latest_task, "metrics_json", None)).get("metrics")) if latest_task else {}
    checks = _safe_list(_safe_dict(getattr(latest_task, "metrics_json", None)).get("checks")) if latest_task else []
    manifest_total = dataset.get("sample_total")
    actual_train = metrics.get("train_samples")
    actual_eval = metrics.get("eval_samples")
    actual_total = (
        int(actual_train or 0) + int(actual_eval or 0)
        if actual_train is not None or actual_eval is not None
        else "-"
    )
    eval_evaluated = metrics.get("eval_evaluated_samples", actual_eval)
    validation_status = _training_evaluation_status_label(metrics)
    lines = [
        f"# 全平台历史模型训练评估报告",
        "",
        f"- 训练任务：`{job.id}`",
        f"- 训练模式：`{spec.get('training_mode') or '-'}`",
        f"- 目标 Skill：`{job.target_skill_id or '-'}`",
        f"- 父模型部署：`{parent_model.get('model_deployment_id') or '-'}`",
        f"- 新部署：`{deployment.id if deployment else '-'}`",
        f"- 部署状态：`{deployment.status if deployment else '-'}`",
        f"- 数据集：`{job.dataset_ref or dataset.get('ref') or '-'}`",
        f"- 候选样本数：{manifest_total or '-'}",
        f"- 实际内联样本：{actual_total}",
        f"- 实际训练样本：{actual_train if actual_train is not None else '-'}",
        f"- manifest 评测样本：{dataset.get('eval_count') if dataset.get('eval_count') is not None else '-'}",
        f"- 实际评测样本：{actual_eval if actual_eval is not None else '-'}",
        f"- 实际完成评测：{eval_evaluated if eval_evaluated is not None else '-'}",
        f"- 内联上限：{selector.get('inline_limit', '-')}",
        f"- 内联评测预留：{selector.get('inline_eval_limit', '-')}",
        f"- 训练耗时：{metrics.get('train_runtime_seconds') or metrics.get('train_runtime') or '-'} 秒",
        f"- win_rate：{metrics.get('win_rate', '-')}",
        f"- token/s：{metrics.get('tokens_per_second', '-')}",
        f"- 验证状态：{validation_status}",
        "",
        "## 评估门禁",
    ]
    for check in checks:
        if not isinstance(check, dict):
            continue
        lines.append(
            f"- {check.get('name')}: {'通过' if check.get('passed') else '未通过'} "
            f"({check.get('actual')} {check.get('operator')} {check.get('expected')})"
        )
    lines.extend([
        "",
        "## 结论",
        (
            "该报告由学习训练审核 Agent 自动生成。候选样本数表示平台识别到的可训练历史样本；"
            "实际训练和评测以本次下发到 Bridge 的内联样本为准。"
        ),
    ])
    eval_mode = str(metrics.get("eval_mode") or "").strip()
    if eval_mode == "holdout_generation_smoke" and int(eval_evaluated or 0) > 0:
        lines.extend([
            "",
            "## 风险",
            "- 本次离线评估模式为 `holdout_generation_smoke`，win_rate 表示保留样本可生成率，不等同于业务语义验收通过。",
            "- 仍需结合真实模型对话、业务规则评测集和人工抽检确认输出语义是否稳定。",
        ])
    elif int(eval_evaluated or 0) <= 0:
        lines.extend([
            "",
            "## 风险",
            "- 本次没有有效评测样本进入离线评测，win_rate 只能表示未评测状态，不能作为效果通过依据。",
            "- 需要重新生成含评测预留的训练任务，或切换为文件式数据集下发后再做正式验收。",
        ])
    return "\n".join(lines)


def _training_evaluation_status_label(metrics: dict[str, Any]) -> str:
    eval_evaluated = metrics.get("eval_evaluated_samples", metrics.get("eval_samples"))
    try:
        evaluated_count = int(eval_evaluated or 0)
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "评估不足"
    if str(metrics.get("eval_mode") or "").strip() == "holdout_generation_smoke":
        return "离线冒烟通过"
    return "正式评估通过"


async def _auto_training_deployment_eval_readiness(
    db: AsyncSession,
    job: TrainingJob,
) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id.desc())
            .limit(20)
        )
    ).scalars().all()
    for row in rows:
        payload = _safe_dict(getattr(row, "metrics_json", None))
        if payload.get("op") != "training.evaluate":
            continue
        metrics = _safe_dict(payload.get("metrics"))
        eval_evaluated = metrics.get("eval_evaluated_samples", metrics.get("eval_samples"))
        try:
            evaluated_count = int(float(eval_evaluated or 0))
        except (TypeError, ValueError):
            evaluated_count = 0
        return {
            "ready": evaluated_count > 0,
            "reason": "ok" if evaluated_count > 0 else "no_eval_samples",
            "evaluation_task_id": row.id,
            "eval_evaluated_samples": evaluated_count,
            "eval_samples": metrics.get("eval_samples"),
            "eval_mode": metrics.get("eval_mode"),
        }
    return {
        "ready": False,
        "reason": "missing_evaluation_task",
        "evaluation_task_id": None,
        "eval_evaluated_samples": 0,
    }


async def publish_training_evaluation_report_to_inbox(
    db: AsyncSession,
    job: TrainingJob,
    *,
    latest_task: TrainingJobTask | None = None,
    deployment: TrainingModelDeployment | None = None,
) -> dict[str, Any]:
    spec = _safe_dict(job.spec_json)
    if spec.get("training_mode") != "full_history_incremental":
        return {"published": False, "reason": "not_full_history_incremental"}
    from app.execution.models import DecisionLog, ExecutionRun
    from app.inbox.service import sync_report_cards_for_decision_log

    if latest_task is None:
        recent_tasks = (
            await db.execute(
                select(TrainingJobTask)
                .where(TrainingJobTask.job_id == job.id)
                .order_by(TrainingJobTask.id.desc())
                .limit(20)
            )
        ).scalars().all()
        latest_task = next(
            (
                row for row in recent_tasks
                if _safe_dict(getattr(row, "metrics_json", None)).get("op") == "training.evaluate"
            ),
            None,
        )
    if deployment is None:
        deployment = (
            await db.execute(
                select(TrainingModelDeployment)
                .where(TrainingModelDeployment.job_id == job.id)
                .order_by(TrainingModelDeployment.created_at.desc(), TrainingModelDeployment.id.desc())
                .limit(1)
            )
        ).scalars().first()
    report_key = f"train-eval-{_sha256(job.id)[:24]}"
    existing = (
        await db.execute(
            select(DecisionLog)
            .where(DecisionLog.run_id == report_key)
            .where(DecisionLog.skill_id == (job.target_skill_id or LEARNING_FULL_HISTORY_TARGET_SKILL_ID))
            .order_by(DecisionLog.id.desc())
            .limit(1)
        )
    ).scalars().first()
    now = now_bjt()
    run = await db.get(ExecutionRun, report_key)
    if run is None:
        run = ExecutionRun(
            id=report_key,
            skill_id=job.target_skill_id or LEARNING_FULL_HISTORY_TARGET_SKILL_ID,
            trigger_type="training_evaluation",
            run_mode="training_evaluation_report",
            started_at=job.created_at or now,
            completed_at=now,
            status="completed",
            summary="全平台历史模型训练评估报告已生成。",
            metadata_json={
                "training_job_id": job.id,
                "training_mode": spec.get("training_mode"),
                "model_deployment_id": deployment.id if deployment else None,
            },
        )
        db.add(run)
        await db.flush()
    dataset = _safe_dict(spec.get("dataset"))
    metrics = _safe_dict(_safe_dict(getattr(latest_task, "metrics_json", None)).get("metrics")) if latest_task else {}
    manifest_total = dataset.get("sample_total")
    actual_train = metrics.get("train_samples")
    actual_eval = metrics.get("eval_samples")
    actual_total = (
        int(actual_train or 0) + int(actual_eval or 0)
        if actual_train is not None or actual_eval is not None
        else None
    )
    eval_evaluated = metrics.get("eval_evaluated_samples", actual_eval)
    eval_ready = int(eval_evaluated or 0) > 0
    validation_status = _training_evaluation_status_label(metrics)
    report = {
        "channel": "model_training_evaluation",
        "title": "全平台历史模型训练评估报告",
        "summary": (
            f"训练任务 {job.id} 已完成训练；候选样本 {manifest_total or '-'} 条，"
            f"实际内联 {actual_total if actual_total is not None else '-'} 条，"
            f"评测样本 {eval_evaluated if eval_evaluated is not None else '-'} 条，"
            f"验证状态={validation_status}，"
            f"部署={deployment.id if deployment else '-'}。"
        ),
        "content_markdown": _training_evaluation_report_markdown(job, latest_task, deployment),
        "metrics": [
            {"label": "候选样本", "value": str(manifest_total or "-")},
            {"label": "实际内联样本", "value": str(actual_total if actual_total is not None else "-")},
            {"label": "实际训练样本", "value": str(metrics.get("train_samples", "-"))},
            {"label": "候选评测样本", "value": str(dataset.get("eval_count") if dataset.get("eval_count") is not None else metrics.get("eval_samples", "-"))},
            {"label": "实际评测样本", "value": str(metrics.get("eval_samples", "-"))},
            {"label": "完成评测样本", "value": str(metrics.get("eval_evaluated_samples", "-"))},
            {"label": "win_rate", "value": str(metrics.get("win_rate", "-"))},
            {"label": "训练耗时", "value": f"{metrics.get('train_runtime_seconds') or metrics.get('train_runtime') or '-'}s"},
            {"label": "验证状态", "value": validation_status},
        ],
        "tags": ["模型训练", "全平台历史", "评估报告"],
        "payload": {
            "training_job_id": job.id,
            "training_mode": spec.get("training_mode"),
            "dataset_ref": job.dataset_ref,
            "deployment_id": deployment.id if deployment else None,
            "deployment_status": deployment.status if deployment else None,
            "parent_model_deployment_id": _safe_dict(spec.get("parent_model")).get("model_deployment_id"),
            "manifest_sample_total": manifest_total,
            "actual_inline_sample_total": actual_total,
            "actual_train_samples": actual_train,
            "actual_eval_samples": actual_eval,
            "eval_evaluated_samples": eval_evaluated,
            "evaluation_ready": eval_ready,
            "raw_payload_returned": False,
        },
    }
    output_result = {"reports": [report], "todos": []}
    input_snapshot = {
        "training_job_id": job.id,
        "dataset_ref": job.dataset_ref,
        "training_mode": spec.get("training_mode"),
    }
    if existing is not None:
        existing.input_snapshot = input_snapshot
        existing.output_result = output_result
        existing.suggested_action = {
            "action": "review_model_training_evaluation",
            "deployment_id": deployment.id if deployment else None,
        }
        flag_modified(existing, "input_snapshot")
        flag_modified(existing, "output_result")
        flag_modified(existing, "suggested_action")
        existing.model_id = deployment.id if deployment else None
        await db.flush()
        await sync_report_cards_for_decision_log(db, existing)
        return {"published": True, "decision_log_id": existing.id, "existing": True}

    decision = DecisionLog(
        run_id=report_key,
        skill_id=job.target_skill_id or LEARNING_FULL_HISTORY_TARGET_SKILL_ID,
        input_snapshot=input_snapshot,
        output_result=output_result,
        suggested_action={"action": "review_model_training_evaluation", "deployment_id": deployment.id if deployment else None},
        approval_level=0,
        approval_status="auto",
        model_id=deployment.id if deployment else None,
        created_at=now,
    )
    db.add(decision)
    await db.flush()
    await sync_report_cards_for_decision_log(db, decision)
    try:
        await capture_execution_run(db, run)
        await capture_decision_log(db, decision)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获训练评估 inbox 报告失败 job={} err={}", job.id, exc)
    return {"published": True, "decision_log_id": decision.id, "existing": False}


async def advance_learning_training_automation(
    db: AsyncSession,
    user: User,
    *,
    days: int = 30,
    max_jobs: int = 10,
    dispatch_recovery_only: bool = False,
) -> dict[str, Any]:
    """Advance learning-created training work through governed state machines.

    The auto-review agent only approves daily incremental candidates generated
    from the previous BJT natural day. It still calls the normal training
    approval and dispatch APIs, so audit trails and rollback state remain intact.
    """
    if not bool(settings.LEARNING_AUTO_TRAINING_ENABLED):
        return {"enabled": False, "reason": "LEARNING_AUTO_TRAINING_ENABLED=false"}

    clean_days = _bounded_days(days, default=30)
    clean_limit = max(1, min(int(max_jobs or 10), 100))
    _window_start, _window_end, window_date = _yesterday_training_window()
    cutoff = now_bjt() - timedelta(days=clean_days)
    result: dict[str, Any] = {
        "enabled": True,
        "days": clean_days,
        "max_jobs": clean_limit,
        "dispatch_recovery_only": bool(dispatch_recovery_only),
        "approved_jobs": [],
        "dispatched_jobs": [],
        "retried_dispatch_jobs": [],
        "skipped_dispatch_jobs": [],
        "collected": None,
        "evaluated_jobs": [],
        "approved_deployments": [],
        "evaluation_reports": [],
        "skipped": [],
        "errors": [],
        "governance": {
            "uses_review_state_machine": True,
            "raw_payload_returned": False,
            "auto_deployment_approval_requires_eval_samples": False,
        },
    }

    from app.training import service as training_service

    async def record_error(stage: str, entity_id: str, exc: Exception) -> None:
        detail: Any
        if isinstance(exc, AppError):
            detail = exc.detail
            reason = exc.code
        else:
            detail = {"detail": _clip(exc, 1000)}
            reason = exc.__class__.__name__
        result["errors"].append({
            "stage": stage,
            "id": entity_id,
            "reason": reason,
            "detail": _redacted_payload(detail, 1000),
        })

    jobs: list[TrainingJob] = []
    if not dispatch_recovery_only:
        scan_limit = max(clean_limit * 200, 2000)
        job_rows = (
            await db.execute(
                select(TrainingJob)
                .where(
                    or_(
                        TrainingJob.created_at >= cutoff,
                        TrainingJob.updated_at >= cutoff,
                        TrainingJob.approved_at >= cutoff,
                    )
                )
                .where(TrainingJob.training_strategy == "learning_sample_threshold")
                .where(TrainingJob.status.in_(["awaiting_review", "queued", "running", "evaluating", "unknown", "completed"]))
                .order_by(
                    TrainingJob.updated_at.asc(),
                    TrainingJob.created_at.asc(),
                    TrainingJob.id.asc(),
                )
                .limit(scan_limit)
            )
        ).scalars().all()
        if not _is_global_user(user):
            department = _user_department(user)
            job_rows = [job for job in job_rows if job.department == department]
        job_rows.sort(
            key=lambda row: (
                0 if _is_auto_incremental_training_job(row) else 1,
                row.updated_at or datetime.max,
                row.created_at or datetime.max,
                row.id,
            )
        )

        grouped_jobs: dict[str, list[TrainingJob]] = {}
        for job in job_rows:
            if not _is_learning_auto_training_job(job):
                continue
            allowed, reason = _auto_training_agent_allows_job(job, window_date=window_date)
            if not allowed:
                result["skipped"].append({
                    "job_id": job.id,
                    "skill_id": job.target_skill_id,
                    "status": job.status,
                    "reason": reason,
                })
                continue
            grouped_jobs.setdefault(str(job.target_skill_id or job.id), []).append(job)

        for skill_id, items in sorted(grouped_jobs.items()):
            has_default_route_candidate = any(
                _default_training_route_priority(row) == 0
                and row.status in {"awaiting_review", "queued"}
                for row in items
            )
            active: list[TrainingJob] = []
            for row in items:
                if row.status in {"running", "evaluating", "unknown"}:
                    active.append(row)
                    continue
                if row.status == "queued" and row.failure_stage not in {"dispatch", "model_preflight"}:
                    if has_default_route_candidate and _default_training_route_priority(row) > 0:
                        result["skipped"].append({
                            "job_id": row.id,
                            "skill_id": skill_id,
                            "reason": "legacy_candidate_superseded_by_default_route",
                        })
                        continue
                    active.append(row)
            if active:
                result["skipped"].append({
                    "skill_id": skill_id,
                    "reason": "active_training_job_exists",
                    "job_id": active[0].id,
                    "status": active[0].status,
                })
                continue
            items.sort(
                key=lambda row: (
                    0 if row.status in {"awaiting_review", "queued"} else 1,
                    _default_training_route_priority(row),
                    _training_mode_priority(row),
                    -_training_sample_total(row),
                    -float(_safe_dict(_safe_dict(row.spec_json).get("dataset")).get("avg_quality") or 0),
                    -(row.created_at.timestamp() if row.created_at else 0),
                    row.id,
                ),
            )
            jobs.append(items[0])
            for duplicate in items[1:]:
                result["skipped"].append({
                    "job_id": duplicate.id,
                    "skill_id": skill_id,
                    "reason": "daily_candidate_superseded",
                    "selected_job_id": items[0].id,
                })
            if len(jobs) >= clean_limit:
                break

    advanced_job_ids: set[str] = set()
    for job in jobs:
        job_id = job.id
        advanced_job_ids.add(job_id)
        reviewer = _learning_training_reviewer_user(job)
        try:
            if job.status == "awaiting_review":
                approved = await training_service.approve_training_job(db, reviewer, job_id)
                result["approved_jobs"].append({
                    "job_id": job_id,
                    "status": approved.get("status"),
                    "target_gateway_id": approved.get("target_gateway_id"),
                    "approved_by": LEARNING_AUTO_TRAINING_AGENT_ID,
                })
                job = await db.get(TrainingJob, job_id) or job
            if bool(settings.LEARNING_AUTO_TRAINING_DISPATCH_ENABLED) and job.status == "queued":
                was_dispatch_retry = job.failure_stage == "dispatch"
                model_ready, model_reason, model_detail = await _auto_training_model_dispatch_readiness(
                    db,
                    job,
                    reviewer,
                    training_service,
                )
                if not model_ready:
                    job.failure_stage = "model_preflight"
                    job.updated_at = now_bjt()
                    result["skipped_dispatch_jobs"].append({
                        "job_id": job.id,
                        "skill_id": job.target_skill_id,
                        "target_gateway_id": job.target_gateway_id,
                        "reason": model_reason,
                        "model_status": model_detail,
                    })
                    await db.flush()
                    continue
                if job.failure_stage == "model_preflight":
                    job.failure_stage = None
                    job.updated_at = now_bjt()
                    await db.flush()
                has_gateway_slot, gateway_slot_reason, gateway_slot_detail = await _auto_training_gateway_dispatch_slot(
                    db,
                    job,
                )
                if not has_gateway_slot:
                    result["skipped_dispatch_jobs"].append({
                        "job_id": job.id,
                        "skill_id": job.target_skill_id,
                        "target_gateway_id": job.target_gateway_id,
                        "reason": gateway_slot_reason,
                        "gateway_capacity": gateway_slot_detail,
                    })
                    continue
                if was_dispatch_retry:
                    dispatched = await training_service.retry_training_job(db, reviewer, job_id)
                else:
                    dispatched = await training_service.dispatch_training_job(db, reviewer, job_id)
                result["dispatched_jobs"].append({
                    "job_id": job_id,
                    "status": dispatched.get("status"),
                    "target_gateway_id": dispatched.get("target_gateway_id"),
                    "dispatched_by": LEARNING_AUTO_TRAINING_AGENT_ID,
                    "retry": was_dispatch_retry,
                })
                job = await db.get(TrainingJob, job_id) or job
            if job.status == "completed":
                evaluated = await training_service.evaluate_training_job(db, reviewer, job_id)
                result["evaluated_jobs"].append({
                    "job_id": job_id,
                    "status": evaluated.get("status"),
                    "latest_deployment": _safe_dict(evaluated.get("latest_deployment")).get("id"),
                })
                refreshed_job = await db.get(TrainingJob, job_id)
                if refreshed_job is not None and _is_full_history_incremental_training_job(refreshed_job):
                    report_result = await publish_training_evaluation_report_to_inbox(db, refreshed_job)
                    result["evaluation_reports"].append({"job_id": job_id, **report_result})
        except Exception as exc:  # noqa: BLE001
            await record_error("training_job.advance", job_id, exc)

    stale_seconds = max(0, min(int(settings.LEARNING_AUTO_TRAINING_COLLECT_STALE_SECONDS or 0), 24 * 60 * 60))
    collect_cutoff = now_bjt() - timedelta(seconds=stale_seconds)
    if bool(settings.LEARNING_AUTO_TRAINING_DISPATCH_ENABLED):
        retry_rows = (
            await db.execute(
                select(TrainingJob)
                .where(TrainingJob.created_by == "learning_auto_flow")
                .where(TrainingJob.training_strategy == "learning_sample_threshold")
                .where(TrainingJob.status == "queued")
                .where(TrainingJob.failure_stage.in_(["dispatch", "model_preflight"]))
                .where(TrainingJob.updated_at <= collect_cutoff)
                .order_by(TrainingJob.updated_at.asc(), TrainingJob.created_at.asc(), TrainingJob.id.asc())
                .limit(clean_limit * 20)
            )
        ).scalars().all()
        if not _is_global_user(user):
            department = _user_department(user)
            retry_rows = [job for job in retry_rows if job.department == department]
        retried_count = 0
        for job in retry_rows:
            if retried_count >= clean_limit:
                break
            if job.id in advanced_job_ids:
                continue
            if not _is_learning_auto_training_job(job):
                result["skipped_dispatch_jobs"].append({
                    "job_id": job.id,
                    "skill_id": job.target_skill_id,
                    "reason": "not_learning_auto_training_job",
                })
                continue
            allowed, reason = _auto_training_agent_allows_job(job, window_date=None)
            if not allowed:
                result["skipped_dispatch_jobs"].append({
                    "job_id": job.id,
                    "skill_id": job.target_skill_id,
                    "reason": reason,
                })
                continue
            was_model_preflight_retry = job.failure_stage == "model_preflight"
            reviewer = _learning_training_reviewer_user(job)
            if was_model_preflight_retry:
                model_ready, model_reason, model_detail = await _auto_training_model_dispatch_readiness(
                    db,
                    job,
                    reviewer,
                    training_service,
                )
                if not model_ready:
                    job.updated_at = now_bjt()
                    result["skipped_dispatch_jobs"].append({
                        "job_id": job.id,
                        "skill_id": job.target_skill_id,
                        "target_gateway_id": job.target_gateway_id,
                        "reason": model_reason,
                        "model_status": model_detail,
                        "recovered_model_preflight": False,
                    })
                    await db.flush()
                    continue
                job.failure_stage = None
                job.updated_at = now_bjt()
                await db.flush()
            active_job = await _active_training_job_for_skill(db, job)
            if active_job is not None:
                result["skipped_dispatch_jobs"].append({
                    "job_id": job.id,
                    "skill_id": job.target_skill_id,
                    "reason": "active_training_job_exists",
                    "active_job_id": active_job.id,
                    "active_status": active_job.status,
                })
                continue
            ready, reason, contract = await _dispatch_retry_gateway_readiness(db, job)
            if not ready:
                result["skipped_dispatch_jobs"].append({
                    "job_id": job.id,
                    "skill_id": job.target_skill_id,
                    "target_gateway_id": job.target_gateway_id,
                    "reason": reason,
                    "gateway_contract": contract,
                })
                continue
            has_gateway_slot, gateway_slot_reason, gateway_slot_detail = await _auto_training_gateway_dispatch_slot(
                db,
                job,
                contract=contract,
            )
            if not has_gateway_slot:
                result["skipped_dispatch_jobs"].append({
                    "job_id": job.id,
                    "skill_id": job.target_skill_id,
                    "target_gateway_id": job.target_gateway_id,
                    "reason": gateway_slot_reason,
                    "gateway_capacity": gateway_slot_detail,
                })
                continue
            try:
                if was_model_preflight_retry:
                    dispatched = await training_service.dispatch_training_job(db, reviewer, job.id)
                else:
                    dispatched = await training_service.retry_training_job(db, reviewer, job.id)
            except Exception as exc:  # noqa: BLE001
                await record_error("training_job.dispatch_retry_recover", job.id, exc)
                continue
            item = {
                "job_id": job.id,
                "status": dispatched.get("status"),
                "target_gateway_id": dispatched.get("target_gateway_id"),
                "dispatched_by": LEARNING_AUTO_TRAINING_AGENT_ID,
                "retry": not was_model_preflight_retry,
                "recovered_dispatch_failure": not was_model_preflight_retry,
                "recovered_model_preflight": was_model_preflight_retry,
            }
            result["retried_dispatch_jobs"].append(item)
            result["dispatched_jobs"].append(item)
            retried_count += 1

    collect_items: list[dict[str, Any]] = []
    if bool(settings.LEARNING_AUTO_TRAINING_DISPATCH_ENABLED):
        active_rows = (
            await db.execute(
                select(TrainingJob)
                .where(TrainingJob.status.in_(["queued", "running", "evaluating", "unknown"]))
                .where(or_(
                    TrainingJob.status != "queued",
                    TrainingJob.failure_stage.is_(None),
                    TrainingJob.failure_stage != "dispatch",
                ))
                .where(TrainingJob.updated_at <= collect_cutoff)
                .order_by(TrainingJob.updated_at.asc(), TrainingJob.created_at.asc())
                .limit(clean_limit * 4)
            )
        ).scalars().all()
        if not _is_global_user(user):
            department = _user_department(user)
            active_rows = [job for job in active_rows if job.department == department]
        for job in [row for row in active_rows if _is_auto_incremental_training_job(row)][:clean_limit]:
            if not job.target_gateway_id:
                collect_items.append({"job_id": job.id, "status": "skipped", "reason": "missing_gateway"})
                continue
            try:
                reviewer = _learning_training_reviewer_user(job)
                collected = await training_service.collect_training_job_result(db, reviewer, job.id)
                collect_items.append({
                    "job_id": job.id,
                    "status": "collected",
                    "result_status": collected.get("status"),
                    "updated_at": collected.get("updated_at"),
                })
                refreshed = await db.get(TrainingJob, job.id)
                if refreshed is not None and refreshed.status == "completed":
                    reviewer = _learning_training_reviewer_user(refreshed)
                    evaluated = await training_service.evaluate_training_job(db, reviewer, refreshed.id)
                    result["evaluated_jobs"].append({
                        "job_id": refreshed.id,
                        "status": evaluated.get("status"),
                        "latest_deployment": _safe_dict(evaluated.get("latest_deployment")).get("id"),
                    })
                    if _is_full_history_incremental_training_job(refreshed):
                        report_result = await publish_training_evaluation_report_to_inbox(db, refreshed)
                        result["evaluation_reports"].append({"job_id": refreshed.id, **report_result})
            except Exception as exc:  # noqa: BLE001
                await record_error("training_job.collect_result", job.id, exc)
        result["collected"] = {"items": collect_items, "total": len(collect_items), "stale_seconds": stale_seconds}

    if bool(settings.LEARNING_AUTO_DEPLOYMENT_APPROVE_ENABLED):
        deployment_rows = (
            await db.execute(
                select(TrainingModelDeployment, TrainingJob)
                .join(TrainingJob, TrainingJob.id == TrainingModelDeployment.job_id)
                .where(TrainingModelDeployment.status == "awaiting_review")
                .where(
                    or_(
                        TrainingModelDeployment.created_at >= cutoff,
                        TrainingModelDeployment.updated_at >= cutoff,
                        TrainingJob.created_at >= cutoff,
                        TrainingJob.updated_at >= cutoff,
                    )
                )
                .order_by(TrainingModelDeployment.updated_at.asc(), TrainingModelDeployment.created_at.asc())
                .limit(clean_limit * 4)
            )
        ).all()
        if not _is_global_user(user):
            department = _user_department(user)
            deployment_rows = [item for item in deployment_rows if item[0].department == department]
        for deployment, job in deployment_rows:
            if len(result["approved_deployments"]) >= clean_limit:
                break
            if not _is_auto_incremental_training_job(job):
                continue
            eval_readiness = await _auto_training_deployment_eval_readiness(db, job)
            try:
                reviewer = _learning_training_reviewer_user(job)
                approved = await training_service.approve_training_deployment(db, reviewer, deployment.id)
                deployment_payload = _safe_dict(approved.get("deployment"))
                result["approved_deployments"].append({
                    "deployment_id": deployment.id,
                    "job_id": job.id,
                    "status": deployment_payload.get("status"),
                    "rollout_percent": deployment_payload.get("rollout_percent"),
                    "approved_by": LEARNING_AUTO_TRAINING_AGENT_ID,
                    "eval_readiness": eval_readiness,
                    "approval_risk": None if bool(eval_readiness.get("ready")) else "no_eval_samples",
                })
                if _is_full_history_incremental_training_job(job):
                    refreshed_deployment = await db.get(TrainingModelDeployment, deployment.id)
                    report_result = await publish_training_evaluation_report_to_inbox(
                        db,
                        job,
                        deployment=refreshed_deployment or deployment,
                    )
                    result["evaluation_reports"].append({"job_id": job.id, **report_result})
            except Exception as exc:  # noqa: BLE001
                await record_error("training_deployment.approve", deployment.id, exc)

    result["skipped"] = result["skipped"][:clean_limit]
    result["errors"] = result["errors"][:clean_limit]
    return result


async def ensure_training_candidates_from_learning_samples(
    db: AsyncSession,
    user: User,
    *,
    days: int = 30,
    min_sample_count: int = LEARNING_TRAINING_CANDIDATE_MIN_SAMPLES,
    max_skills: int = 20,
) -> dict[str, Any]:
    """Create governed daily incremental training jobs when yesterday's sample pool is ready."""
    clean_days = _bounded_days(days)
    clean_min = max(1, int(min_sample_count or LEARNING_TRAINING_CANDIDATE_MIN_SAMPLES))
    # Retained for backwards-compatible callers; daily incremental training must
    # consider every eligible Skill/project source in the window.
    _ = max_skills
    ecommerce_materialization = await materialize_ecommerce_learning_flow_dataset(db, user)
    window_start, window_end, window_date = _yesterday_training_window()
    rows = (
        await db.execute(
            select(LearningArtifact)
            .where(
                _scope_filter(LearningArtifact, user),
                LearningArtifact.created_at >= window_start,
                LearningArtifact.created_at < window_end,
                LearningArtifact.artifact_kind.in_(sorted(TRAINING_ARTIFACTS)),
                or_(
                    LearningArtifact.skill_id.is_not(None),
                    and_(
                        LearningArtifact.target_type.in_(["skill", "project"]),
                        LearningArtifact.target_id.is_not(None),
                    ),
                ),
                LearningArtifact.status == "materialized",
                LearningArtifact.sink_type == "training_sample",
            )
            .order_by(LearningArtifact.created_at.asc(), LearningArtifact.id.asc())
        )
    ).scalars().all()
    grouped: dict[str, list[LearningArtifact]] = {}
    for row in rows:
        source_id = _learning_artifact_training_source_id(row)
        if source_id:
            grouped.setdefault(source_id, []).append(row)

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    from app.training.service import create_training_job

    for skill_id, artifacts in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        artifact_ids = sorted({row.id for row in artifacts})
        sample_total = len(artifact_ids)
        eval_count = sum(1 for row in artifacts if row.artifact_kind == "eval_case")
        avg_quality = round(
            sum(float(row.quality_score or 0) for row in artifacts) / max(len(artifacts), 1),
            4,
        )
        if skill_id.startswith("project:"):
            from app.projects.models import Project

            project = await db.get(Project, skill_id.split(":", 1)[1])
            managed_gate = _managed_project_training_gate(project) if project else {}
            if managed_gate:
                skipped.append({
                    "skill_id": skill_id,
                    "reason": "managed_project_training_gate",
                    "sample_total": sample_total,
                    "minimum_training_samples": managed_gate["minimum_training_samples"],
                    "candidate_source": managed_gate["candidate_source"],
                    "window": {
                        "mode": "yesterday",
                        "date": window_date,
                        "start": _iso(window_start),
                        "end": _iso(window_end),
                    },
                })
                continue
        if sample_total < clean_min:
            skipped.append({
                "skill_id": skill_id,
                "reason": "below_threshold",
                "sample_total": sample_total,
                "window": {"mode": "yesterday", "date": window_date, "start": _iso(window_start), "end": _iso(window_end)},
            })
            continue
        artifact_ids_hash = _sha256({"learning_artifact_ids": artifact_ids})
        legacy_manifest_hash = _sha256({"skill_id": skill_id, "artifact_ids": artifact_ids, "window_date": window_date})
        manifest_hash = _sha256({
            "skill_id": skill_id,
            "artifact_ids_hash": artifact_ids_hash,
            "sample_total": sample_total,
            "window_date": window_date,
            "window_start": _iso(window_start),
            "window_end": _iso(window_end),
        })
        existing_jobs = (
            await db.execute(
                select(TrainingJob)
                .where(TrainingJob.target_skill_id == skill_id)
                .where(TrainingJob.status.not_in(["failed", "cancelled"]))
                .order_by(TrainingJob.created_at.desc())
                .limit(50)
            )
        ).scalars().all()
        duplicate = next(
            (
                job
                for job in existing_jobs
                if _safe_dict(job.spec_json).get("source") == "learning_sample_threshold"
                and _safe_dict(_safe_dict(job.spec_json).get("dataset")).get("manifest_hash")
                in {manifest_hash, legacy_manifest_hash}
            ),
            None,
        )
        if duplicate:
            skipped.append({
                "skill_id": skill_id,
                "reason": "duplicate_manifest",
                "training_job_id": duplicate.id,
                "sample_total": sample_total,
                "window": {"mode": "yesterday", "date": window_date, "start": _iso(window_start), "end": _iso(window_end)},
            })
            continue
        skill = await db.get(Skill, skill_id)
        department = (skill.department if skill else None) or artifacts[0].department or _user_department(user)
        if not department:
            skipped.append({
                "skill_id": skill_id,
                "reason": "missing_department",
                "sample_total": sample_total,
                "window": {"mode": "yesterday", "date": window_date, "start": _iso(window_start), "end": _iso(window_end)},
            })
            continue
        parent_deployment = await _latest_active_model_deployment_for_skill(
            db,
            skill_id,
            department=department,
        )
        parent_model = _parent_model_spec_from_deployment(parent_deployment)
        dataset_ref = f"learning-artifacts://{skill_id}/training/yesterday/{window_date}"
        inline_artifact_ids = (
            len(json.dumps(artifact_ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            <= LEARNING_DAILY_TRAINING_SPEC_INLINE_ID_MAX_BYTES
        )
        selector = {
            "type": "learning_artifacts",
            "scope": "source",
            "source_id": skill_id,
            "target_skill_id": skill_id,
            "artifact_kinds": sorted(TRAINING_ARTIFACTS),
            "status": "materialized",
            "sink_type": "training_sample",
            "created_at_start": _iso(window_start),
            "created_at_end": _iso(window_end),
            "limit": sample_total,
            "inline_limit": sample_total,
            "inline_max_content_bytes": 204800,
        }
        dataset = {
            "ref": dataset_ref,
            "manifest_hash": manifest_hash,
            "sample_total": sample_total,
            "train_count": sample_total - eval_count,
            "eval_count": eval_count,
            "avg_quality": avg_quality,
            "learning_artifact_ids_count": sample_total,
            "learning_artifact_ids_hash": artifact_ids_hash,
            "learning_artifact_ids_inlined": inline_artifact_ids,
            "selector": selector,
            "threshold": clean_min,
            "window": {
                "mode": "yesterday",
                "date": window_date,
                "start": _iso(window_start),
                "end": _iso(window_end),
                "timezone": "Asia/Shanghai",
            },
        }
        lineage = {
            "source": "learning_artifacts",
            "skill_id": skill_id,
            "learning_artifact_ids_count": sample_total,
            "learning_artifact_ids_hash": artifact_ids_hash,
            "manifest_hash": manifest_hash,
            "window_date": window_date,
            "parent_model_deployment_id": parent_model.get("model_deployment_id") if parent_model else None,
        }
        if inline_artifact_ids:
            dataset["learning_artifact_ids"] = artifact_ids
            lineage["learning_artifact_ids"] = artifact_ids
        spec = {
            "source": "learning_sample_threshold",
            "source_skill_id": skill_id,
            "training_mode": "daily_incremental",
            "model": {
                "profile": LEARNING_DEFAULT_TRAINING_MODEL_PROFILE,
                "source": "internal",
                "internal_model_ref": LEARNING_DEFAULT_TRAINING_MODEL_PROFILE,
            },
            "parent_model": parent_model,
            "dataset": dataset,
            "lineage": lineage,
            "eval_gate": {
                "require_artifact_sha256": True,
                "min_win_rate": 0.55,
                "manual_review_required": False,
            },
            "deployment": {
                "auto_request": True,
                "auto_active": True,
                "target_skill_ids": [skill_id],
                "model_family": f"{skill_id}:{LEARNING_DEFAULT_TRAINING_MODEL_PROFILE}:learning-loop-adapter",
                "deployment_target_gateway_id": LEARNING_DEFAULT_DEPLOYMENT_GATEWAY_ID,
                "deployment_runtime_profile": LEARNING_DEFAULT_DEPLOYMENT_RUNTIME_PROFILE,
                "rollout_percent": 100,
                "reason": "智能闭环训练评估通过后自动激活到 Mac 推理节点",
                "keyword_review": {
                    "enabled": True,
                    "domain": "adult_products",
                    "keywords": list(LEARNING_ADULT_PRODUCT_REVIEW_KEYWORDS),
                },
            },
            "governance": {
                "auto_created": True,
                "raw_payload_returned": False,
                "no_auto_approval": False,
                "auto_training_agent": LEARNING_AUTO_TRAINING_AGENT_ID,
                "incremental_training": True,
                "deployment_review_required": False,
                "auto_deployment_mode": "active_after_eval_readiness",
                "bridge_dataset": {
                    "enabled": True,
                    "source_gateway_id": LEARNING_DEFAULT_DATASET_SOURCE_GATEWAY_ID,
                    "target_gateway_id": LEARNING_DEFAULT_TRAINING_GATEWAY_ID,
                    "storage": "bridge_dataset",
                },
            },
        }
        dataset["bridge_dataset"] = {
            "enabled": True,
            "source_gateway_id": LEARNING_DEFAULT_DATASET_SOURCE_GATEWAY_ID,
            "target_gateway_id": LEARNING_DEFAULT_TRAINING_GATEWAY_ID,
            "storage": "bridge_dataset",
        }
        result = await create_training_job(
            db,
            user,
            {
                "title": f"智能闭环自动训练候选：{(skill.display_name or skill.name) if skill else skill_id}"[:200],
                "department": department,
                "job_type": "lora",
                "training_strategy": "learning_sample_threshold",
                "target_skill_id": skill_id,
                "dataset_ref": dataset_ref,
                "objective": (
                    f"使用 {window_date} 的昨日增量学习样本继续训练当前模型："
                    f"{sample_total} 条可治理样本（评测 {eval_count} 条，平均质量 {avg_quality}）。"
                ),
                "risk_level": (getattr(skill, "risk_level", None) if skill else None) or "R2",
                "spec": spec,
            },
            commit=False,
        )
        created.append({
            "skill_id": skill_id,
            "training_job_id": result.get("id"),
            "status": result.get("status"),
            "dataset_ref": dataset_ref,
            "manifest_hash": manifest_hash,
            "sample_total": sample_total,
            "eval_count": eval_count,
            "avg_quality": avg_quality,
            "learning_artifact_ids_inlined": inline_artifact_ids,
            "window": {"mode": "yesterday", "date": window_date, "start": _iso(window_start), "end": _iso(window_end)},
            "parent_model_deployment_id": parent_model.get("model_deployment_id") if parent_model else None,
        })
    return {
        "created": created,
        "skipped": skipped,
        "threshold": clean_min,
        "days": clean_days,
        "window": {"mode": "yesterday", "date": window_date, "start": _iso(window_start), "end": _iso(window_end)},
        "governance": {
            "raw_payload_returned": False,
            "auto_training_agent": LEARNING_AUTO_TRAINING_AGENT_ID,
            "incremental_training": True,
            "all_eligible_sources": True,
            "auto_deployment_mode": "active_after_eval_readiness",
        },
        "ecommerce_dataset": {
            "namespace": ecommerce_materialization.get("namespace"),
            "materialized_total": ecommerce_materialization.get("total"),
            "stats": ecommerce_materialization.get("stats"),
        },
    }


async def ensure_full_history_platform_training_candidate(
    db: AsyncSession,
    user: User,
    *,
    min_sample_count: int = 4,
    target_skill_id: str = LEARNING_FULL_HISTORY_TARGET_SKILL_ID,
    parent_deployment_id: str | None = None,
    require_parent_deployment: bool = True,
    training_model_profile: str = LEARNING_DEFAULT_TRAINING_MODEL_PROFILE,
    training_model_source: str | None = None,
    training_model_internal_ref: str | None = None,
    training_gateway_id: str = LEARNING_DEFAULT_TRAINING_GATEWAY_ID,
    dataset_source_gateway_id: str | None = None,
    deployment_gateway_id: str = LEARNING_DEFAULT_DEPLOYMENT_GATEWAY_ID,
    deployment_runtime_profile: str = LEARNING_DEFAULT_DEPLOYMENT_RUNTIME_PROFILE,
    model_family_suffix: str = "platform-full-history-adapter",
    assign_training_gateway: bool = False,
    manifest_extra: dict[str, Any] | None = None,
    governance_extra: dict[str, Any] | None = None,
    job_title: str | None = None,
    dataset_ref_suffix: str | None = None,
) -> dict[str, Any]:
    """Create a governed full-platform historical incremental training job."""
    clean_min = max(1, int(min_sample_count or 4))
    clean_model_profile = str(training_model_profile or LEARNING_DEFAULT_TRAINING_MODEL_PROFILE).strip()
    clean_training_gateway_id = str(training_gateway_id or LEARNING_DEFAULT_TRAINING_GATEWAY_ID).strip()
    clean_dataset_source_gateway_id = str(
        dataset_source_gateway_id or LEARNING_DEFAULT_DATASET_SOURCE_GATEWAY_ID
    ).strip()
    clean_deployment_gateway_id = str(deployment_gateway_id or LEARNING_DEFAULT_DEPLOYMENT_GATEWAY_ID).strip()
    clean_deployment_runtime_profile = str(
        deployment_runtime_profile or LEARNING_DEFAULT_DEPLOYMENT_RUNTIME_PROFILE
    ).strip()
    clean_model_family_suffix = str(model_family_suffix or "platform-full-history-adapter").strip()
    clean_manifest_extra = _safe_dict(manifest_extra)
    clean_governance_extra = _safe_dict(governance_extra)
    ecommerce_materialization = await materialize_ecommerce_learning_flow_dataset(db, user)
    rows = (
        await db.execute(
            select(
                LearningArtifact.artifact_kind,
                func.count(LearningArtifact.id),
                func.min(LearningArtifact.created_at),
                func.max(LearningArtifact.created_at),
                func.avg(LearningArtifact.quality_score),
            )
            .where(
                _scope_filter(LearningArtifact, user),
                LearningArtifact.artifact_kind.in_(sorted(TRAINING_ARTIFACTS)),
                LearningArtifact.status == "materialized",
                LearningArtifact.sink_type == "training_sample",
            )
            .group_by(LearningArtifact.artifact_kind)
        )
    ).all()
    counts_by_kind = {str(kind): int(count or 0) for kind, count, *_rest in rows}
    sample_total = sum(counts_by_kind.values())
    eval_count = counts_by_kind.get("eval_case", 0)
    min_created = min((row[2] for row in rows if row[2]), default=None)
    max_created = max((row[3] for row in rows if row[3]), default=None)
    avg_values = [float(row[4] or 0) for row in rows if row[4] is not None]
    avg_quality = round(sum(avg_values) / max(len(avg_values), 1), 4) if avg_values else 0.0
    window_date = now_bjt().date().isoformat()
    if sample_total < clean_min:
        return {
            "created": [],
            "skipped": [{
                "reason": "below_threshold",
                "sample_total": sample_total,
                "threshold": clean_min,
                "training_mode": "full_history_incremental",
            }],
            "threshold": clean_min,
        }

    latest_deployment = await _latest_active_model_deployment_for_skill(db, target_skill_id)
    selected_parent_deployment_id = parent_deployment_id or (latest_deployment.id if latest_deployment is not None else None)
    if require_parent_deployment and not selected_parent_deployment_id:
        selected_parent_deployment_id = LEARNING_FULL_HISTORY_PARENT_DEPLOYMENT_ID
    parent_deployment = None
    if selected_parent_deployment_id:
        parent_deployment = (
            latest_deployment
            if latest_deployment is not None and latest_deployment.id == selected_parent_deployment_id
            else await db.get(TrainingModelDeployment, selected_parent_deployment_id)
        )
    if parent_deployment is None and require_parent_deployment:
        return {
            "created": [],
            "skipped": [{
                "reason": "parent_deployment_missing",
                "parent_model_deployment_id": selected_parent_deployment_id,
            }],
            "threshold": clean_min,
        }
    parent_model_candidate = _parent_model_spec_from_deployment(parent_deployment)
    if require_parent_deployment and (not parent_model_candidate or not parent_model_candidate.get("artifact_uri")):
        return {
            "created": [],
            "skipped": [{
                "reason": "parent_model_missing_artifact_uri",
                "parent_model_deployment_id": selected_parent_deployment_id,
            }],
            "threshold": clean_min,
        }
    parent_model = (
        parent_model_candidate
        if _parent_model_usable_on_training_gateway(parent_model_candidate, clean_training_gateway_id)
        else None
    )
    effective_parent_deployment_id = (
        str(parent_model.get("model_deployment_id") or selected_parent_deployment_id)
        if parent_model
        else None
    )
    selected_parent_deployment_id = str(
        (parent_model_candidate or {}).get("model_deployment_id") or selected_parent_deployment_id or ""
    )
    inline_limit = min(sample_total, 5000)
    inline_eval_limit = min(max(eval_count, 4), 16) if eval_count else 0
    from app.training.service import TRAINING_GATEWAY_DATASET_PACKING_VERSION

    manifest_hash = _sha256({
        "scope": "platform",
        "target_skill_id": target_skill_id,
        "sample_total": sample_total,
        "eval_count": eval_count,
        "min_created_at": _iso(min_created),
        "max_created_at": _iso(max_created),
        "parent_model_deployment_id": effective_parent_deployment_id,
        "base_model_profile": clean_model_profile,
        "gateway_dataset_packing_version": TRAINING_GATEWAY_DATASET_PACKING_VERSION,
        "extra": clean_manifest_extra,
    })
    dataset_suffix = str(dataset_ref_suffix or window_date).strip().strip("/")
    dataset_ref = f"learning-artifacts://platform/training/full-history/{dataset_suffix or window_date}"
    duplicate = (
        await db.execute(
            select(TrainingJob)
            .where(TrainingJob.training_strategy == "learning_sample_threshold")
            .where(TrainingJob.target_skill_id == target_skill_id)
            .where(TrainingJob.status.not_in(["failed", "cancelled"]))
            .order_by(TrainingJob.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    for job in duplicate:
        spec = _safe_dict(job.spec_json)
        dataset = _safe_dict(spec.get("dataset"))
        if spec.get("training_mode") == "full_history_incremental" and dataset.get("manifest_hash") == manifest_hash:
            return {
                "created": [],
                "skipped": [{
                    "reason": "duplicate_manifest",
                    "training_job_id": job.id,
                    "sample_total": sample_total,
                    "manifest_hash": manifest_hash,
                }],
                "threshold": clean_min,
            }

    skill = await db.get(Skill, target_skill_id)
    department = (
        (skill.department if skill else None)
        or (parent_deployment.department if parent_deployment is not None else None)
        or _user_department(user)
        or ("AI平台" if target_skill_id == "skillforge-finetuned-model-chat" else None)
    )
    keyword_review = {
        "enabled": True,
        "domain": "adult_products",
        "keywords": list(LEARNING_ADULT_PRODUCT_REVIEW_KEYWORDS),
    }
    if target_skill_id == "skillforge-finetuned-model-chat":
        keyword_review = {
            "enabled": False,
            "domain": "skillforge_full_history_chat",
            "keywords": [],
        }
    spec = {
        "source": "learning_sample_threshold",
        "source_skill_id": target_skill_id,
        "training_mode": "full_history_incremental",
        "model": {
            "profile": clean_model_profile,
            "source": str(
                training_model_source
                or ("internal" if clean_model_profile == LEARNING_DEFAULT_TRAINING_MODEL_PROFILE else "auto")
            ).strip(),
        },
        "dataset": {
            "ref": dataset_ref,
            "manifest_hash": manifest_hash,
            "sample_total": sample_total,
            "train_count": sample_total - eval_count,
            "eval_count": eval_count,
            "avg_quality": avg_quality,
            "threshold": clean_min,
            "selector": {
                "type": "learning_artifacts",
                "scope": "platform",
                "artifact_kinds": sorted(TRAINING_ARTIFACTS),
                "status": "materialized",
                "sink_type": "training_sample",
                "created_at_start": _iso(min_created),
                "created_at_end": _iso(max_created + timedelta(microseconds=1) if max_created else None),
                "limit": sample_total,
                "inline_limit": inline_limit,
                "inline_eval_limit": inline_eval_limit,
                "inline_max_content_bytes": 204800,
                "packing_version": TRAINING_GATEWAY_DATASET_PACKING_VERSION,
            },
            "window": {
                "mode": "full_history",
                "date": window_date,
                "start": _iso(min_created),
                "end": _iso(max_created),
                "timezone": "Asia/Shanghai",
            },
        },
        "lineage": {
            "source": "learning_artifacts",
            "scope": "platform",
            "skill_id": target_skill_id,
            "manifest_hash": manifest_hash,
            "parent_model_deployment_id": effective_parent_deployment_id,
            "parent_model_candidate_deployment_id": selected_parent_deployment_id,
            "parent_model_selection": (
                "latest_active_or_canary" if parent_model and parent_deployment_id is None
                else ("explicit" if parent_model else "base_model")
            ),
            "base_model_profile": clean_model_profile,
            "manifest_extra": clean_manifest_extra,
        },
        "parameters": {
            "max_steps": 8,
            "eval_max_samples": inline_eval_limit,
            "timeout_seconds": 3600,
        },
        "eval_gate": {
            "require_artifact_sha256": True,
            "min_win_rate": 0.55,
            "manual_review_required": False,
        },
        "deployment": {
            "auto_request": True,
            "auto_active": True,
            "target_skill_ids": [target_skill_id],
            "model_family": f"{target_skill_id}:{clean_model_profile}:{clean_model_family_suffix}",
            "deployment_target_gateway_id": clean_deployment_gateway_id,
            "deployment_runtime_profile": clean_deployment_runtime_profile,
            "rollout_percent": 100,
            "reason": "全平台历史数据增量训练评估通过后自动激活到 Mac 推理节点",
            "keyword_review": keyword_review,
        },
        "governance": {
            "auto_created": True,
            "raw_payload_returned": False,
            "no_auto_approval": False,
            "auto_training_agent": LEARNING_AUTO_TRAINING_AGENT_ID,
            "incremental_training": True,
            "deployment_review_required": False,
            "full_platform_history": True,
            "auto_deployment_mode": "active_after_eval_readiness",
            "bridge_dataset": {
                "enabled": True,
                "required": True,
                "source_gateway_id": clean_dataset_source_gateway_id,
                "target_gateway_id": clean_training_gateway_id,
                "storage": "bridge_dataset",
            },
            **clean_governance_extra,
        },
    }
    if training_model_internal_ref:
        spec["model"]["internal_model_ref"] = str(training_model_internal_ref).strip()
    elif spec["model"]["source"] == "internal":
        spec["model"]["internal_model_ref"] = clean_model_profile
    if parent_model:
        spec["parent_model"] = parent_model
    spec["dataset"]["bridge_dataset"] = {
        "enabled": True,
        "required": True,
        "source_gateway_id": clean_dataset_source_gateway_id,
        "target_gateway_id": clean_training_gateway_id,
        "storage": "bridge_dataset",
    }
    from app.training.service import create_training_job

    result = await create_training_job(
        db,
        user,
        {
            "title": job_title or "全平台历史数据增量训练：输入 / 过程 / 输出",
            "department": department,
            "job_type": "lora",
            "training_strategy": "learning_sample_threshold",
            "target_skill_id": target_skill_id,
            "target_gateway_id": clean_training_gateway_id if assign_training_gateway else None,
            "dataset_ref": dataset_ref,
            "objective": (
                (
                    "基于刚完成的训练后模型，使用全平台历史输入、过程、输出治理样本继续增量训练；"
                    if parent_model
                    else f"基于 {clean_model_profile} 基座模型，使用全平台历史输入、过程、输出治理样本进行 LoRA 微调；"
                )
                + f"样本 {sample_total} 条，评测 {eval_count} 条，平均质量 {avg_quality}。"
            ),
            "risk_level": (getattr(skill, "risk_level", None) if skill else None) or "R2",
            "spec": spec,
        },
        commit=False,
    )
    return {
        "created": [{
            "training_job_id": result.get("id"),
            "status": result.get("status"),
            "target_skill_id": target_skill_id,
            "dataset_ref": dataset_ref,
            "manifest_hash": manifest_hash,
            "sample_total": sample_total,
            "eval_count": eval_count,
            "avg_quality": avg_quality,
            "training_mode": "full_history_incremental",
            "parent_model_deployment_id": effective_parent_deployment_id,
            "parent_model_candidate_deployment_id": selected_parent_deployment_id,
            "inline_limit": inline_limit,
            "inline_eval_limit": inline_eval_limit,
        }],
        "skipped": [],
        "threshold": clean_min,
        "ecommerce_dataset": {
            "namespace": ecommerce_materialization.get("namespace"),
            "materialized_total": ecommerce_materialization.get("total"),
            "stats": ecommerce_materialization.get("stats"),
        },
    }


async def training_dataset_manifest(
    db: AsyncSession,
    user: User,
    *,
    skill_id: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Return a governed manifest for Phase 3 training samples.

    The manifest exposes sample lineage and quality, not raw business payloads.
    Training jobs can reference the returned ``dataset_ref`` while the actual
    sample content stays governed in ``learning_artifacts``.
    """
    stmt = select(LearningArtifact).where(
        _scope_filter(LearningArtifact, user),
        LearningArtifact.artifact_kind.in_(sorted(TRAINING_ARTIFACTS)),
        LearningArtifact.status == "materialized",
        LearningArtifact.sink_type == "training_sample",
    )
    if skill_id:
        stmt = stmt.where(LearningArtifact.skill_id == skill_id)
    rows = (
        await db.execute(
            stmt.order_by(LearningArtifact.created_at.desc()).limit(max(1, min(limit, ARTIFACT_LIMIT_MAX)))
        )
    ).scalars().all()
    by_skill: dict[str, dict[str, Any]] = {}
    sample_refs: list[dict[str, Any]] = []
    for row in rows:
        key = row.skill_id or "unknown"
        bucket = by_skill.setdefault(key, {
            "skill_id": row.skill_id,
            "sample_count": 0,
            "eval_count": 0,
            "avg_quality": 0.0,
            "artifact_ids": [],
            "latest_at": None,
        })
        bucket["sample_count"] += 1
        if row.artifact_kind == "eval_case":
            bucket["eval_count"] += 1
        bucket["avg_quality"] += float(row.quality_score or 0)
        bucket["artifact_ids"].append(row.id)
        bucket["latest_at"] = bucket["latest_at"] or _iso(row.created_at)
        sample_refs.append({
            "artifact_id": row.id,
            "artifact_kind": row.artifact_kind,
            "skill_id": row.skill_id,
            "run_id": row.run_id,
            "event_id": row.event_id,
            "quality_score": row.quality_score,
            "confidence": row.confidence,
            "status": row.status,
            "sink_type": row.sink_type,
            "labels": row.labels_json or [],
            "source_type": (row.content_json or {}).get("source_type") if isinstance(row.content_json, dict) else None,
            "source_channel": (
                (row.content_json or {}).get("source_channel")
                or ((row.content_json or {}).get("source") or {}).get("source_channel")
            ) if isinstance(row.content_json, dict) else None,
            "lineage_summary": _training_manifest_lineage_summary(row),
            "created_at": _iso(row.created_at),
        })
    for item in by_skill.values():
        if item["sample_count"]:
            item["avg_quality"] = round(item["avg_quality"] / item["sample_count"], 4)
        item["dataset_ref"] = f"learning-artifacts://{item['skill_id'] or 'unknown'}/training/latest"
        item["manifest_hash"] = _sha256({"skill_id": item["skill_id"], "artifact_ids": item["artifact_ids"]})
    return {
        "dataset_ref": f"learning-artifacts://{skill_id or 'visible'}/training/latest",
        "manifest_hash": _sha256({"skill_id": skill_id, "sample_refs": sample_refs}),
        "sample_total": len(sample_refs),
        "skills": list(by_skill.values()),
        "samples": sample_refs,
        "generated_at": _iso(now_bjt()),
        "governance": {
            "raw_payload_returned": False,
            "scope": "global" if _is_global_user(user) else "department",
            "department": None if _is_global_user(user) else _user_department(user),
        },
    }


async def training_job_dataset_samples(
    db: AsyncSession,
    user: User,
    *,
    job_id: str,
    limit: int = 200,
) -> dict[str, Any]:
    clean_job_id = str(job_id or "").strip()
    clean_limit = max(1, min(int(limit or 200), ARTIFACT_LIMIT_MAX))
    if not clean_job_id:
        raise AppError("PARAM_INVALID", 422, {"detail": "job_id is required"})
    job = (
        await db.execute(
            select(TrainingJob)
            .where(TrainingJob.id == clean_job_id)
            .where(_scope_filter(TrainingJob, user))
        )
    ).scalars().first()
    if job is None:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})

    spec = _safe_dict(job.spec_json)
    dataset = _safe_dict(spec.get("dataset"))
    selector = _safe_dict(dataset.get("selector"))
    package = _training_dataset_package_control_summary(job)
    artifact_ids = _training_dataset_artifact_ids_for_job(job)

    base_condition = and_(
        _scope_filter(LearningArtifact, user),
        LearningArtifact.artifact_kind.in_(sorted(TRAINING_ARTIFACTS)),
        LearningArtifact.status == "materialized",
        LearningArtifact.sink_type == "training_sample",
    )
    rows: list[LearningArtifact] = []
    if artifact_ids:
        rows = (
            await db.execute(
                select(LearningArtifact)
                .where(base_condition, LearningArtifact.id.in_(artifact_ids))
                .limit(clean_limit)
            )
        ).scalars().all()
        row_by_id = {row.id: row for row in rows}
        rows = [row_by_id[item] for item in artifact_ids if item in row_by_id][:clean_limit]
    else:
        target_skill_id = str(
            selector.get("target_skill_id")
            or selector.get("skill_id")
            or job.target_skill_id
            or ""
        ).strip()
        condition = base_condition
        if target_skill_id:
            condition = and_(
                condition,
                or_(
                    LearningArtifact.skill_id == target_skill_id,
                    LearningArtifact.target_id == target_skill_id,
                ),
            )
        elif job.dataset_ref and job.dataset_ref.startswith("learning-artifacts://platform/"):
            condition = and_(condition, LearningArtifact.skill_id.is_not(None))
        else:
            condition = and_(condition, sa.false())
        rows = (
            await db.execute(
                select(LearningArtifact)
                .where(condition)
                .order_by(LearningArtifact.created_at.desc(), LearningArtifact.id.asc())
                .limit(clean_limit)
            )
        ).scalars().all()

    samples = [
        {
            "artifact_id": row.id,
            "artifact_kind": row.artifact_kind,
            "title": row.title or _artifact_kind_label(row.artifact_kind),
            "summary": row.summary,
            "skill_id": row.skill_id,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "run_id": row.run_id,
            "event_id": row.event_id,
            "quality_score": row.quality_score,
            "confidence": row.confidence,
            "status": row.status,
            "sink_type": row.sink_type,
            "sink_id": row.sink_id,
            "labels": row.labels_json or [],
            "source_type": _safe_dict(row.content_json).get("source_type"),
            "lineage_summary": _training_manifest_lineage_summary(row),
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }
        for row in rows
    ]
    return {
        "job": {
            "id": job.id,
            "title": job.title,
            "status": job.status,
            "target_skill_id": job.target_skill_id,
            "target_gateway_id": job.target_gateway_id,
            "dataset_ref": package.get("dataset_ref") or dataset.get("ref") or job.dataset_ref,
            "created_at": _iso(job.created_at),
            "updated_at": _iso(job.updated_at),
        },
        "dataset": {
            "dataset_ref": package.get("dataset_ref") or dataset.get("ref") or job.dataset_ref,
            "manifest_hash": package.get("manifest_hash") or dataset.get("manifest_hash"),
            "sample_count": package.get("sample_count") or dataset.get("sample_total") or len(samples),
            "train_count": package.get("train_count") or dataset.get("train_count"),
            "eval_count": package.get("eval_count") or dataset.get("eval_count"),
            "artifact_count": len(artifact_ids) or dataset.get("learning_artifact_ids_count") or len(samples),
            "returned_count": len(samples),
            "raw_payload_returned": False,
        },
        "items": samples,
        "total": len(samples),
        "limit": clean_limit,
        "generated_at": _iso(now_bjt()),
        "governance": {
            "raw_payload_returned": False,
            "scope": "global" if _is_global_user(user) else "department",
            "department": None if _is_global_user(user) else _user_department(user),
        },
    }


def _training_manifest_lineage_summary(row: LearningArtifact) -> dict[str, Any]:
    content = row.content_json if isinstance(row.content_json, dict) else {}
    feedback = _safe_dict(content.get("feedback"))
    structured_feedback = _safe_dict(content.get("human_feedback_input")) or _safe_dict(feedback.get("structured"))
    structured_fields = _safe_dict(structured_feedback.get("fields"))
    decision = _safe_dict(content.get("decision"))
    model_context = _safe_dict(content.get("model_context"))
    source = _safe_dict(content.get("source"))
    formed_data = content.get("formed_data")
    runtime_agent = {}
    if isinstance(formed_data, dict) and isinstance(formed_data.get("runtime_agent"), dict):
        runtime_agent = _safe_dict(formed_data.get("runtime_agent"))
    elif isinstance(content.get("runtime_agent"), dict):
        runtime_agent = _safe_dict(content.get("runtime_agent"))
    input_value = content.get("input") if "input" in content else content.get("request")
    output_value = content.get("output") if "output" in content else content.get("actual_output")
    summary = {
        "has_input": input_value not in (None, "", [], {}),
        "has_output": output_value not in (None, "", [], {}),
        "has_formed_data": formed_data not in (None, "", [], {}),
        "formed_data_sha256": _sha256(_redacted_payload(formed_data, 3000)) if formed_data not in (None, "", [], {}) else None,
        "has_runtime_agent": bool(runtime_agent),
        "runtime_agent_id": runtime_agent.get("agent_id"),
        "runtime_agent_contract_complete": (
            _safe_dict(runtime_agent.get("contract")).get("complete")
            if isinstance(runtime_agent.get("contract"), dict)
            else None
        ),
        "has_suggested_action": content.get("suggested_action") not in (None, "", [], {}),
        "has_human_feedback": bool(feedback or structured_feedback or decision.get("user_feedback")),
        "feedback_source": (
            structured_feedback.get("source_channel")
            or feedback.get("source_channel")
            or feedback.get("channel")
            or content.get("source_channel")
            or source.get("source_channel")
        ),
        "feedback_fields": sorted(str(key)[:80] for key in structured_fields.keys())[:20],
        "feedback_type": _clip(
            structured_fields.get("feedback_type") or decision.get("feedback_type"),
            120,
        ) if structured_fields.get("feedback_type") or decision.get("feedback_type") else None,
        "rating": structured_fields.get("rating") if structured_fields.get("rating") is not None else decision.get("rating"),
        "decision_log_id": decision.get("decision_log_id") or source.get("decision_log_id"),
        "todo_id": source.get("todo_id") or structured_fields.get("todo_id"),
        "request_id": source.get("request_id") or structured_fields.get("request_id"),
        "run_id": source.get("run_id") or row.run_id,
        "model_deployment_id": model_context.get("model_deployment_id"),
        "model_family": _clip(model_context.get("model_family"), 120) if model_context.get("model_family") else None,
        "deployment_status": model_context.get("deployment_status"),
        "artifact_id": model_context.get("artifact_id"),
        "artifact_sha256": model_context.get("artifact_sha256"),
        "inference_status": model_context.get("inference_status"),
        "inference_gateway_id": model_context.get("inference_gateway_id"),
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


async def learning_summary(
    db: AsyncSession,
    user: User,
    *,
    days: int = 30,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    source_type: str | None = None,
    artifact_kind: str | None = None,
    target_type: str | None = None,
    status: str | None = None,
    sf_tool: str | None = None,
) -> dict[str, Any]:
    clean_days = _bounded_days(days)
    cutoff = now_bjt() - timedelta(days=clean_days)
    event_filter = _event_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        status=status,
        event_type=event_type,
        source_type=source_type,
        sf_tool=sf_tool,
    )
    filtered_event_ids = None
    if event_type or source_type or sf_tool:
        filtered_event_ids = select(LearningEvent.id).where(event_filter)
    artifact_filter = _artifact_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        status=status,
        artifact_kind=artifact_kind,
        event_ids=filtered_event_ids,
    )
    candidate_filter = _candidate_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        status=status,
        target_type=target_type,
        sf_tool=sf_tool,
    )

    total_events = int((await db.execute(select(func.count(LearningEvent.id)).where(event_filter))).scalar() or 0)
    artifact_rows = (await db.execute(
        select(LearningArtifact.artifact_kind, LearningArtifact.status, func.count(LearningArtifact.id))
        .where(artifact_filter)
        .group_by(LearningArtifact.artifact_kind, LearningArtifact.status)
    )).all()
    artifacts_by_kind: dict[str, int] = {}
    artifacts_by_status: dict[str, int] = {}
    for kind, status, count in artifact_rows:
        artifacts_by_kind[str(kind)] = artifacts_by_kind.get(str(kind), 0) + int(count or 0)
        artifacts_by_status[str(status)] = artifacts_by_status.get(str(status), 0) + int(count or 0)
    candidate_rows = (await db.execute(
        select(ImprovementCandidate.target_type, ImprovementCandidate.status, func.count(ImprovementCandidate.id))
        .where(candidate_filter)
        .group_by(ImprovementCandidate.target_type, ImprovementCandidate.status)
    )).all()
    candidates_by_target: dict[str, int] = {}
    candidates_by_status: dict[str, int] = {}
    for target_type, status, count in candidate_rows:
        candidates_by_target[str(target_type)] = candidates_by_target.get(str(target_type), 0) + int(count or 0)
        candidates_by_status[str(status)] = candidates_by_status.get(str(status), 0) + int(count or 0)
    indexed_knowledge = int((await db.execute(
        select(func.count(LearningArtifact.id)).where(artifact_filter, LearningArtifact.sink_type == "knowledge_document")
    )).scalar() or 0)
    training_samples = int((await db.execute(
        select(func.count(LearningArtifact.id)).where(artifact_filter, LearningArtifact.artifact_kind.in_(sorted(TRAINING_ARTIFACTS)))
    )).scalar() or 0)
    used_context = int((await db.execute(
        select(func.count(LearningFlowEdge.id)).where(
            _with_common_filters(
                _scope_filter(LearningFlowEdge, user),
                LearningFlowEdge,
                department=department,
                org_unit_id=org_unit_id,
                skill_id=skill_id,
                run_id=run_id,
                created_after=cutoff,
            ),
            LearningFlowEdge.relation == "used_as_context",
        )
    )).scalar() or 0)
    agent_candidate_rows = (
        await db.execute(
            select(ImprovementCandidate).where(
                _candidate_filter(
                    user,
                    created_after=cutoff,
                    department=department,
                    org_unit_id=org_unit_id,
                    skill_id=skill_id,
                    run_id=run_id,
                    target_type="agent",
                )
            )
        )
    ).scalars().all()
    ai_gap_rows = (
        await db.execute(
            select(ImprovementCandidate).where(
                _candidate_filter(
                    user,
                    created_after=cutoff,
                    department=department,
                    org_unit_id=org_unit_id,
                    skill_id=skill_id,
                    run_id=run_id,
                    target_type="ai_system",
                )
            )
        )
    ).scalars().all()
    feedback_events = int((
        await db.execute(
            select(func.count(LearningEvent.id)).where(
                _event_filter(
                    user,
                    created_after=cutoff,
                    department=department,
                    org_unit_id=org_unit_id,
                    skill_id=skill_id,
                    run_id=run_id,
                    source_type="improvement_candidate",
                )
            )
        )
    ).scalar() or 0)
    self_audit_events = int((
        await db.execute(
            select(func.count(LearningEvent.id)).where(
                _event_filter(
                    user,
                    created_after=cutoff,
                    department=department,
                    org_unit_id=org_unit_id,
                    skill_id=skill_id,
                    run_id=run_id,
                    source_type="learning_gap_audit",
                )
            )
        )
    ).scalar() or 0)
    governance_task_rows = (
        await db.execute(
            select(LearningGovernanceTask.status, func.count(LearningGovernanceTask.id))
            .where(
                _with_common_filters(
                    _scope_filter(LearningGovernanceTask, user),
                    LearningGovernanceTask,
                    created_after=cutoff,
                    department=department,
                    org_unit_id=org_unit_id,
                    status=None,
                )
            )
            .group_by(LearningGovernanceTask.status)
        )
    ).all()
    governance_tasks_by_status = {str(task_status or "unknown"): int(count or 0) for task_status, count in governance_task_rows}
    governance_tasks_total = sum(governance_tasks_by_status.values())
    governance_tasks_pending = sum(governance_tasks_by_status.get(key, 0) for key in ("pending", "in_progress"))
    agent_candidates_total = len(agent_candidate_rows)
    agent_drafts = sum(1 for row in agent_candidate_rows if _safe_dict(row.external_ref_json).get("agent_draft_id"))
    validation_passed = sum(
        1
        for row in agent_candidate_rows
        if _safe_dict(_safe_dict(row.external_ref_json).get("agent_draft_validation")).get("status") == "passed"
    )
    validation_failed = sum(
        1
        for row in agent_candidate_rows
        if _safe_dict(_safe_dict(row.external_ref_json).get("agent_draft_validation")).get("status") == "failed"
    )
    implementation_ready = sum(1 for row in agent_candidate_rows if _safe_dict(row.external_ref_json).get("implementation_queue"))
    implementation_blocked = sum(
        1
        for row in agent_candidate_rows
        if _safe_dict(row.external_ref_json).get("implementation_status") == "blocked_by_agent_draft_validation"
    )
    ai_gaps_open = sum(1 for row in ai_gap_rows if row.status in {"open", "reviewing"})
    ai_gaps_reviewing = sum(1 for row in ai_gap_rows if row.status == "reviewing")
    health_penalty = (
        validation_failed * 12
        + implementation_blocked * 12
        + ai_gaps_open * 6
        + governance_tasks_pending * 2
        + max(agent_candidates_total - agent_drafts, 0) * 4
    )
    if agent_candidates_total and not self_audit_events:
        health_penalty += 8
    self_iteration_health = max(0, min(100, 100 - health_penalty))
    return {
        "scope": "global" if _is_global_user(user) else "department",
        "department": None if _is_global_user(user) else _user_department(user),
        "days": clean_days,
        "filters": {
            "department": _clean_filter(department),
            "org_unit_id": _clean_filter(org_unit_id),
            "skill_id": _clean_filter(skill_id),
            "run_id": _clean_filter(run_id),
            "event_type": _clean_filter(event_type),
            "source_type": _clean_filter(source_type),
            "artifact_kind": _clean_filter(artifact_kind),
            "target_type": _clean_filter(target_type),
            "status": _clean_filter(status),
            "sf_tool": _clean_filter(sf_tool),
        },
        "generated_at": _iso(now_bjt()),
        "events": {"total": total_events},
        "artifacts": {"by_kind": artifacts_by_kind, "by_status": artifacts_by_status},
        "knowledge": {"indexed": indexed_knowledge},
        "training": {"samples": training_samples},
        "agent": {"used_context_edges": used_context, "memories": artifacts_by_kind.get("agent_memory", 0)},
        "candidates": {"by_target": candidates_by_target, "by_status": candidates_by_status},
        "self_iteration": {
            "health_score": self_iteration_health,
            "agent_candidates": agent_candidates_total,
            "agent_drafts": agent_drafts,
            "draft_validation_passed": validation_passed,
            "draft_validation_failed": validation_failed,
            "implementation_ready": implementation_ready,
            "implementation_blocked": implementation_blocked,
            "ai_system_gaps": len(ai_gap_rows),
            "ai_system_gaps_open": ai_gaps_open,
            "ai_system_gaps_reviewing": ai_gaps_reviewing,
            "governance_tasks": governance_tasks_total,
            "governance_tasks_pending": governance_tasks_pending,
            "governance_tasks_by_status": governance_tasks_by_status,
            "feedback_events": feedback_events,
            "self_audit_events": self_audit_events,
        },
    }


async def list_learning_events(
    db: AsyncSession,
    user: User,
    *,
    event_type: str | None = None,
    source_type: str | None = None,
    skill_id: str | None = None,
    department: str | None = None,
    org_unit_id: str | None = None,
    run_id: str | None = None,
    modality: str | None = None,
    sensitivity_level: str | None = None,
    sf_tool: str | None = None,
    status: str | None = None,
    q: str | None = None,
    days: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    cutoff = now_bjt() - timedelta(days=_bounded_days(days)) if days else None
    stmt = select(LearningEvent).where(
        _event_filter(
            user,
            created_after=cutoff,
            department=department,
            org_unit_id=org_unit_id,
            skill_id=skill_id,
            run_id=run_id,
            status=status,
            event_type=event_type,
            source_type=source_type,
            modality=modality,
            sensitivity_level=sensitivity_level,
            sf_tool=sf_tool,
        )
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(LearningEvent.event_type.ilike(like), LearningEvent.redacted_summary.ilike(like), LearningEvent.source_id.ilike(like)))
    rows = (await db.execute(stmt.order_by(LearningEvent.created_at.desc()).limit(max(1, min(limit, EVENT_LIMIT_MAX))))).scalars().all()
    return {"items": [_event_to_dict(row) for row in rows], "total": len(rows)}


async def list_learning_artifacts(
    db: AsyncSession,
    user: User,
    *,
    artifact_kind: str | None = None,
    status: str | None = None,
    skill_id: str | None = None,
    department: str | None = None,
    org_unit_id: str | None = None,
    run_id: str | None = None,
    target_type: str | None = None,
    sink_type: str | None = None,
    sensitivity_level: str | None = None,
    event_type: str | None = None,
    source_type: str | None = None,
    sf_tool: str | None = None,
    q: str | None = None,
    days: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    cutoff = now_bjt() - timedelta(days=_bounded_days(days)) if days else None
    event_ids = None
    if event_type or source_type or sf_tool:
        event_ids = select(LearningEvent.id).where(
            _event_filter(
                user,
                created_after=cutoff,
                department=department,
                org_unit_id=org_unit_id,
                skill_id=skill_id,
                run_id=run_id,
                event_type=event_type,
                source_type=source_type,
                sf_tool=sf_tool,
            )
        )
    stmt = select(LearningArtifact).where(
        _artifact_filter(
            user,
            created_after=cutoff,
            department=department,
            org_unit_id=org_unit_id,
            skill_id=skill_id,
            run_id=run_id,
            status=status,
            artifact_kind=artifact_kind,
            target_type=target_type,
            sink_type=sink_type,
            sensitivity_level=sensitivity_level,
            event_ids=event_ids,
        )
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(LearningArtifact.title.ilike(like), LearningArtifact.summary.ilike(like), LearningArtifact.artifact_kind.ilike(like)))
    rows = (await db.execute(stmt.order_by(LearningArtifact.created_at.desc()).limit(max(1, min(limit, ARTIFACT_LIMIT_MAX))))).scalars().all()
    return {
        "items": [_artifact_to_dict(row) for row in rows],
        "total": len(rows),
        "governance": {
            "scope": "global" if _is_global_user(user) else "department",
            "department": None if _is_global_user(user) else _user_department(user),
            "raw_payload_returned": False,
        },
    }


async def list_improvement_candidates(
    db: AsyncSession,
    user: User,
    *,
    target_type: str | None = None,
    status: str | None = None,
    skill_id: str | None = None,
    department: str | None = None,
    org_unit_id: str | None = None,
    run_id: str | None = None,
    target_id: str | None = None,
    risk_level: str | None = None,
    sf_tool: str | None = None,
    q: str | None = None,
    days: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    cutoff = now_bjt() - timedelta(days=_bounded_days(days)) if days else None
    stmt = select(ImprovementCandidate).where(
        _candidate_filter(
            user,
            created_after=cutoff,
            department=department,
            org_unit_id=org_unit_id,
            skill_id=skill_id,
            run_id=run_id,
            status=status,
            target_type=target_type,
            target_id=target_id,
            risk_level=risk_level,
            sf_tool=sf_tool,
        )
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(ImprovementCandidate.title.ilike(like), ImprovementCandidate.proposal.ilike(like)))
    rows = (await db.execute(stmt.order_by(ImprovementCandidate.priority_score.desc(), ImprovementCandidate.created_at.desc()).limit(max(1, min(limit, EVENT_LIMIT_MAX))))).scalars().all()
    return {"items": [_candidate_to_dict(row) for row in rows], "total": len(rows)}


def _node_id(entity_type: str, entity_id: str) -> str:
    return f"{entity_type}:{entity_id}"


def _source_type_label(value: str | None) -> str:
    mapping = {
        "execution_run": "Skill 运行",
        "execution_artifact": "执行数据资产",
        "intelligence_analyze_run": "智能分析",
        "decision_log": "决策反馈",
        "sf_mcp_call": "SF 调用",
        "sf_usage_aggregate": "SF 高频聚合",
        "improvement_candidate": "改进候选反馈",
        "learning_gap_audit": "AI 自审",
        "agent_draft_validation": "Agent 草稿校验",
        "agent_thread": "Agent 会话",
        "knowledge_query": "知识检索",
        "training_job": "训练任务",
        "model_deployment": "模型部署",
        "decision_request": "评审请求",
        "ai_todo": "审批反馈",
        "todo_dispatch_task": "派发待办",
    }
    return mapping.get(str(value or ""), str(value or "-"))


def _artifact_kind_label(value: str | None) -> str:
    mapping = {
        "knowledge_note": "知识片段",
        "report_summary": "报告摘要",
        "execution_data_artifact": "执行数据资产",
        "training_sample": "训练样本",
        "eval_case": "评测样本",
        "agent_memory": "Agent 记忆",
        "skill_improvement_candidate": "Skill 候选",
        "sf_iteration_candidate": "SF 候选",
        "agent_policy_candidate": "Agent 候选",
        "agent_creation_candidate": "Agent 化候选",
        "ai_system_gap_candidate": "AI 系统缺口",
        "knowledge_review_candidate": "知识缺口",
        "training_improvement_candidate": "训练候选",
    }
    return mapping.get(str(value or ""), str(value or "-"))


def _status_label(value: str | None) -> str:
    mapping = {
        "ready": "待处理",
        "needs_review": "待审核",
        "materialized": "已物化",
        "ignored": "已忽略",
        "captured": "已捕获",
        "completed": "已完成",
        "pending": "等待中",
        "running": "运行中",
        "failed": "失败",
        "open": "开放",
        "reviewing": "评审中",
        "accepted": "已采纳",
        "rejected": "已驳回",
        "implemented": "已落地",
    }
    return mapping.get(str(value or ""), str(value or "-"))


def _icon_for_source(value: str | None) -> str:
    mapping = {
        "execution_run": "play",
        "execution_artifact": "database",
        "intelligence_analyze_run": "bot",
        "decision_log": "check",
        "sf_mcp_call": "bolt",
        "sf_usage_aggregate": "trend",
        "improvement_candidate": "check",
        "learning_gap_audit": "shield",
        "agent_draft_validation": "check",
        "agent_thread": "bot",
        "knowledge_query": "search",
        "training_job": "gpu",
        "model_deployment": "bolt",
        "decision_request": "inbox",
        "ai_todo": "check",
        "todo_dispatch_task": "send",
    }
    return mapping.get(str(value or ""), "flow")


def _icon_for_artifact(value: str | None) -> str:
    mapping = {
        "knowledge_note": "book",
        "report_summary": "doc",
        "execution_data_artifact": "database",
        "training_sample": "beaker",
        "eval_case": "check",
        "agent_memory": "bot",
        "skill_improvement_candidate": "edit",
        "sf_iteration_candidate": "bolt",
        "agent_policy_candidate": "shield",
        "agent_creation_candidate": "bot",
        "ai_system_gap_candidate": "shield",
        "knowledge_review_candidate": "book",
        "training_improvement_candidate": "gpu",
    }
    return mapping.get(str(value or ""), "layers")


def _heat(value: float | int | None, *, fallback: float = 0.55) -> float:
    try:
        number = float(value if value is not None else fallback)
    except (TypeError, ValueError):
        number = fallback
    return round(max(0.05, min(1.0, number)), 4)


def _flow_node(
    *,
    node_id: str,
    label: str,
    meta: str,
    entity_type: str,
    entity_id: str | None = None,
    icon: str = "flow",
    heat: float | int | None = None,
    tone: str = "source",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": _clip(label, 120),
        "meta": _clip(meta, 160),
        "icon": icon,
        "heat": _heat(heat),
        "tone": tone,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "payload": payload or {},
    }


def _flow_connector(from_key: str, to_key: str, *, count: int, label: str, relation: str) -> dict[str, Any]:
    clean_count = max(0, int(count or 0))
    return {
        "from": from_key,
        "to": to_key,
        "count": clean_count,
        "label": label,
        "relation": relation,
        "intensity": round(max(0.08, min(1.0, clean_count / 20)), 4),
        "animated": clean_count > 0,
    }


def _journey_step(
    stage: str,
    label: str,
    *,
    entity_type: str,
    entity_id: str | None = None,
    status: str = "completed",
    at: datetime | None = None,
    summary: str | None = None,
    meta: str | None = None,
    action: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reduced single-hop step for the observable data-flow journey.

    The journey API intentionally returns only redacted labels / summaries and
    stable entity ids so the UI can show "where data is flowing" without
    exposing raw payloads.
    """

    return {
        "stage": stage,
        "label": _clip(label, 160),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "status": status,
        "status_text": _status_label(status),
        "at": _iso(at),
        "summary": _clip(summary, 600) if summary else None,
        "meta": _clip(meta, 180) if meta else None,
        "action": action,
        "payload": payload or {},
    }


def _journey_state(blockers: list[str], steps: list[dict[str, Any]]) -> tuple[str, float]:
    status_values = {str(step.get("status") or "") for step in steps}
    if any(value in status_values for value in {"failed", "error"}):
        state = "failed"
    elif blockers:
        state = "blocked"
    elif any(value in status_values for value in {"ready", "open", "running", "pending"}):
        state = "flowing"
    else:
        state = "completed"
    done_statuses = {"completed", "materialized", "accepted", "implemented", "reviewing", "captured"}
    done = sum(1 for step in steps if str(step.get("status") or "") in done_statuses)
    progress = round(done / max(len(steps), 1), 4)
    return state, progress


JOURNEY_EDGE_STEP_LABELS: dict[str, tuple[str, str]] = {
    "controlled_run": ("control", "运行 Agent 控制"),
    "controlled_analysis": ("control", "分析 Agent 控制"),
    "controlled_inference": ("control", "推理 Agent 控制"),
    "controlled_training": ("control", "训练 Agent 控制"),
    "served_model_inference": ("control", "模型推理服务"),
    "submitted_feedback": ("feedback", "钉钉反馈回传"),
    "requested_deployment_review": ("review", "提交模型部署审核"),
    "deployed_to": ("deployment", "模型部署可用"),
    "used_as_model": ("decision", "模型参与决策"),
}


def _journey_contract_payload(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        key: contract.get(key)
        for key in (
            "complete",
            "missing",
            "purpose",
            "gateway_kind",
            "input_channels",
            "control_ops",
            "required_ops",
            "required_ops_any",
            "missing_ops",
            "available_control_ops",
            "output_channels",
            "lineage_required",
            "flow_traceable",
            "submission_ready",
            "lifecycle_ready",
        )
        if contract.get(key) not in (None, "", [], {})
    }


def _journey_edge_step(edge: LearningFlowEdge) -> dict[str, Any] | None:
    relation = str(edge.relation or "")
    config = JOURNEY_EDGE_STEP_LABELS.get(relation)
    if not config:
        return None
    stage, label = config
    metadata = _safe_dict(edge.metadata_json)
    payload: dict[str, Any] = {
        "relation": relation,
        "from_type": edge.from_type,
        "from_id": edge.from_id,
        "to_type": edge.to_type,
        "to_id": edge.to_id,
    }
    summary: str | None = None
    meta = f"{edge.from_type} -> {edge.to_type}"
    if relation.startswith("controlled_") or relation == "served_model_inference":
        contract = _journey_contract_payload(_safe_dict(metadata.get("contract")))
        dataset_package = _safe_dict(metadata.get("dataset_package"))
        payload.update(
            {
                "agent_id": edge.from_id if edge.from_type == "agent" else metadata.get("agent_id"),
                "gateway_kind": metadata.get("gateway_kind"),
                "agent_purpose": metadata.get("agent_purpose"),
                "contract": contract,
                "dataset_package": dataset_package or None,
            }
        )
        missing = _safe_list(contract.get("missing_ops")) + _safe_list(contract.get("missing"))
        if missing:
            summary = f"控制契约未完整：{', '.join(str(item) for item in missing[:6])}"
        else:
            summary = "Agent 控制输入、控制动作、输出回传已记录到学习流。"
        meta = str(metadata.get("gateway_kind") or metadata.get("agent_purpose") or meta)
    elif relation == "submitted_feedback":
        action = str(metadata.get("action") or "")
        payload.update(
            {
                "source_channel": metadata.get("source_channel"),
                "source": metadata.get("source"),
                "feedback_fields": _safe_list(metadata.get("feedback_fields")),
                "feedback_sha256": metadata.get("feedback_sha256"),
                "feedback_type": metadata.get("feedback_type"),
                "rating": metadata.get("rating"),
                "dispatch_task_id": metadata.get("dispatch_task_id"),
                "ack_note_sha256": metadata.get("ack_note_sha256"),
                "raw_text_returned": False,
            }
        )
        summary = (
            "钉钉派发执行完成说明已脱敏回传平台，并可进入训练样本链路。"
            if action == "dispatch_ack"
            else "钉钉互动卡反馈已脱敏回传平台，并可进入训练样本链路。"
        )
        meta = str(metadata.get("source_channel") or metadata.get("source") or "dingtalk")
    elif relation == "requested_deployment_review":
        governance = _safe_dict(metadata.get("governance"))
        payload.update(
            {
                "deployment_id": edge.to_id,
                "status": metadata.get("status"),
                "model_family": metadata.get("model_family"),
                "artifact_id": metadata.get("artifact_id"),
                "artifact_sha256": metadata.get("artifact_sha256"),
                "target_skill_ids": _safe_list(metadata.get("target_skill_ids")),
                "rollout_percent": metadata.get("rollout_percent"),
                "review_required": metadata.get("review_required"),
                "auto_approval": metadata.get("auto_approval", False),
                "governance": {
                    key: governance.get(key)
                    for key in (
                        "review_required",
                        "deployment_review_required",
                        "no_auto_approval",
                        "auto_request_enabled",
                        "approval_state_machine",
                    )
                    if governance.get(key) not in (None, "", [], {})
                },
            }
        )
        summary = "训练完成后已提交模型部署审核；仍需按训练部署状态机完成治理。"
        meta = str(metadata.get("status") or metadata.get("model_family") or meta)
    elif relation == "deployed_to":
        runtime_status = _safe_dict(metadata.get("runtime_status"))
        payload.update(
            {
                "deployment_id": edge.to_id if edge.to_type == "model_deployment" else edge.from_id,
                "status": metadata.get("status"),
                "model_family": metadata.get("model_family"),
                "artifact_id": metadata.get("artifact_id"),
                "rollout_percent": metadata.get("rollout_percent"),
                "runtime_status": runtime_status,
            }
        )
        if edge.from_type == "model_deployment" and edge.to_type == "skill":
            summary = "模型部署已绑定目标 Skill，后续分析和决策可选用该模型。"
            if runtime_status.get("inference_ready") is True:
                summary = "模型部署已通过节点推理可用校验，并绑定目标 Skill 参与决策。"
        else:
            summary = "训练产物已进入模型部署链路。"
            if runtime_status.get("inference_ready") is True:
                summary = "训练产物已部署到节点并通过推理可用校验。"
        meta = str(
            metadata.get("model_family")
            or runtime_status.get("target_gateway_kind")
            or runtime_status.get("target_gateway_id")
            or meta
        )
    elif relation == "used_as_model":
        payload.update(
            {
                "deployment_id": edge.from_id,
                "decision_target_type": edge.to_type,
                "decision_target_id": edge.to_id,
                "model_family": metadata.get("model_family"),
                "deployment_status": metadata.get("deployment_status"),
                "rollout_percent": metadata.get("rollout_percent"),
                "rollout_selected": metadata.get("rollout_selected"),
                "rollout_bucket": metadata.get("rollout_bucket"),
                "rollout_reason": metadata.get("rollout_reason"),
                "artifact_id": metadata.get("artifact_id"),
                "artifact_sha256": metadata.get("artifact_sha256"),
                "inference_status": metadata.get("inference_status"),
                "inference_backend": metadata.get("inference_backend"),
                "inference_gateway_id": metadata.get("inference_gateway_id"),
                "inference_profile": metadata.get("inference_profile"),
                "inference_text_sha256": metadata.get("inference_text_sha256"),
                "inference_metrics": _safe_dict(metadata.get("inference_metrics")),
            }
        )
        if metadata.get("inference_status") == "used":
            summary = "已部署模型参与本次 Skill 输出和决策记录，推理结果以哈希和指标形式回传学习流。"
        else:
            summary = "模型部署上下文已绑定本次 Skill 输出和决策记录。"
        meta = str(
            metadata.get("model_family")
            or metadata.get("inference_gateway_id")
            or metadata.get("deployment_status")
            or meta
        )
    payload = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
    return _journey_step(
        stage,
        label,
        entity_type=edge.from_type,
        entity_id=edge.from_id,
        status="completed",
        at=edge.created_at,
        summary=summary,
        meta=meta,
        action=relation,
        payload=payload,
    )


async def learning_flow_graph(
    db: AsyncSession,
    user: User,
    *,
    days: int = 30,
    skill_id: str | None = None,
    department: str | None = None,
    org_unit_id: str | None = None,
    run_id: str | None = None,
    relation: str | None = None,
    event_type: str | None = None,
    source_type: str | None = None,
    target_type: str | None = None,
    sf_tool: str | None = None,
    limit: int = 250,
) -> dict[str, Any]:
    clean_days = _bounded_days(days)
    cutoff = now_bjt() - timedelta(days=clean_days)
    condition = _with_common_filters(
        _scope_filter(LearningFlowEdge, user),
        LearningFlowEdge,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        created_after=cutoff,
    )
    if relation := _clean_filter(relation):
        condition = and_(condition, LearningFlowEdge.relation == relation)
    if source_type := _clean_filter(source_type):
        condition = and_(condition, LearningFlowEdge.from_type == _source_type_to_edge_type(source_type))
    if event_type or sf_tool:
        matching_events = _event_filter(
            user,
            created_after=cutoff,
            department=department,
            org_unit_id=org_unit_id,
            skill_id=skill_id,
            run_id=run_id,
            event_type=event_type,
            source_type=source_type,
            sf_tool=sf_tool,
        )
        matching_source_ids = select(LearningEvent.source_id).where(matching_events)
        matching_event_ids = select(LearningEvent.id).where(matching_events)
        condition = and_(
            condition,
            or_(
                LearningFlowEdge.from_id.in_(matching_source_ids),
                LearningFlowEdge.to_id.in_(matching_source_ids),
                LearningFlowEdge.metadata_json["event_id"].astext.in_(matching_event_ids),
            ),
        )
    if target_type := _clean_filter(target_type):
        condition = and_(condition, LearningFlowEdge.to_type == target_type)
    stmt = select(LearningFlowEdge).where(condition)
    edges = (await db.execute(stmt.order_by(LearningFlowEdge.created_at.desc()).limit(max(1, min(limit, FLOW_LIMIT_MAX))))).scalars().all()
    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []
    for edge in reversed(edges):
        a = _node_id(edge.from_type, edge.from_id)
        b = _node_id(edge.to_type, edge.to_id)
        nodes.setdefault(a, {"id": a, "entity_type": edge.from_type, "entity_id": edge.from_id, "label": edge.from_id, "group": edge.from_type})
        nodes.setdefault(b, {"id": b, "entity_type": edge.to_type, "entity_id": edge.to_id, "label": edge.to_id, "group": edge.to_type})
        links.append({"id": edge.id, "source": a, "target": b, "relation": edge.relation, "weight": edge.weight, "created_at": _iso(edge.created_at), "metadata": edge.metadata_json or {}})
    return {"nodes": list(nodes.values()), "edges": links, "days": clean_days, "generated_at": _iso(now_bjt())}


async def learning_flow_topology(
    db: AsyncSession,
    user: User,
    *,
    days: int = 30,
    skill_id: str | None = None,
    department: str | None = None,
    org_unit_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    source_type: str | None = None,
    artifact_kind: str | None = None,
    target_type: str | None = None,
    status: str | None = None,
    sf_tool: str | None = None,
    limit_nodes: int = 5,
) -> dict[str, Any]:
    """Return an authoritative staged topology for the flowing data canvas.

    Unlike ``learning_flow_graph`` which exposes raw lineage edges, this API
    groups the loop into product stages. The frontend can render a stable
    left-to-right stream without inferring stage counts from unrelated lists.
    Payloads are intentionally reduced and do not include raw artifact content.
    """
    clean_days = _bounded_days(days)
    clean_limit = max(1, min(limit_nodes, 12))
    cutoff = now_bjt() - timedelta(days=clean_days)

    event_filter = _event_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        status=status,
        event_type=event_type,
        source_type=source_type,
        sf_tool=sf_tool,
    )
    filtered_event_ids = select(LearningEvent.id).where(event_filter) if (event_type or source_type or sf_tool) else None
    artifact_filter = _artifact_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        status=status,
        artifact_kind=artifact_kind,
        event_ids=filtered_event_ids,
    )
    candidate_filter = _candidate_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        status=status,
        target_type=target_type,
        sf_tool=sf_tool,
    )
    edge_filter = _with_common_filters(
        _scope_filter(LearningFlowEdge, user),
        LearningFlowEdge,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        created_after=cutoff,
    )

    total_events = int((await db.execute(select(func.count(LearningEvent.id)).where(event_filter))).scalar() or 0)
    total_artifacts = int((await db.execute(select(func.count(LearningArtifact.id)).where(artifact_filter))).scalar() or 0)
    total_candidates = int((await db.execute(select(func.count(ImprovementCandidate.id)).where(candidate_filter))).scalar() or 0)
    sink_total = int((
        await db.execute(
            select(func.count(LearningArtifact.id)).where(
                artifact_filter,
                or_(LearningArtifact.status == "materialized", LearningArtifact.sink_type.is_not(None)),
            )
        )
    ).scalar() or 0)
    review_total = int((
        await db.execute(
            select(func.count(ImprovementCandidate.id)).where(
                candidate_filter,
                ImprovementCandidate.status.in_(["reviewing", "accepted", "implemented"]),
            )
        )
    ).scalar() or 0)

    source_rows = (
        await db.execute(
            select(
                LearningEvent.source_type,
                func.count(LearningEvent.id),
                func.max(LearningEvent.created_at),
            )
            .where(event_filter)
            .group_by(LearningEvent.source_type)
            .order_by(func.count(LearningEvent.id).desc(), func.max(LearningEvent.created_at).desc())
            .limit(clean_limit)
        )
    ).all()
    source_nodes = [
        _flow_node(
            node_id=f"source:{source_type}",
            label=_source_type_label(source_type),
            meta=f"{int(count or 0)} 条 · 最新 {(_iso(latest) or '-')[:16]}",
            entity_type="source_type",
            entity_id=str(source_type),
            icon=_icon_for_source(source_type),
            heat=min(1.0, int(count or 0) / max(total_events, 1) + 0.18),
            tone="source",
            payload={"source_type": source_type, "count": int(count or 0), "latest_at": _iso(latest)},
        )
        for source_type, count, latest in source_rows
    ]

    event_rows = (
        await db.execute(
            select(LearningEvent)
            .where(event_filter)
            .order_by(LearningEvent.created_at.desc())
            .limit(clean_limit)
        )
    ).scalars().all()
    event_nodes = [
        _flow_node(
            node_id=f"event:{row.id}",
            label=row.event_type,
            meta=f"{_source_type_label(row.source_type)} · {(_iso(row.created_at) or '-')[:16]}",
            entity_type="learning_event",
            entity_id=row.id,
            icon="spark",
            heat=row.quality_score or 0.6,
            tone="event" if "fail" not in row.event_type else "danger",
            payload={
                "id": row.id,
                "event_type": row.event_type,
                "source_type": row.source_type,
                "source_id": row.source_id,
                "skill_id": row.skill_id,
                "run_id": row.run_id,
                "summary": row.redacted_summary,
                "created_at": _iso(row.created_at),
                "quality_score": row.quality_score,
            },
        )
        for row in event_rows
    ]

    artifact_rows = (
        await db.execute(
            select(LearningArtifact)
            .where(artifact_filter)
            .order_by(LearningArtifact.created_at.desc())
            .limit(clean_limit)
        )
    ).scalars().all()
    artifact_nodes = [
        _flow_node(
            node_id=f"artifact:{row.id}",
            label=row.title or _artifact_kind_label(row.artifact_kind),
            meta=f"{_artifact_kind_label(row.artifact_kind)} · {_status_label(row.status)}",
            entity_type="learning_artifact",
            entity_id=row.id,
            icon=_icon_for_artifact(row.artifact_kind),
            heat=row.quality_score or row.confidence or 0.55,
            tone="muted" if row.status == "ignored" else "artifact",
            payload={
                "id": row.id,
                "artifact_kind": row.artifact_kind,
                "title": row.title,
                "summary": row.summary,
                "status": row.status,
                "sink_type": row.sink_type,
                "sink_id": row.sink_id,
                "skill_id": row.skill_id,
                "run_id": row.run_id,
                "quality_score": row.quality_score,
                "created_at": _iso(row.created_at),
            },
        )
        for row in artifact_rows
    ]

    sink_rows = (
        await db.execute(
            select(LearningArtifact)
            .where(
                artifact_filter,
                or_(LearningArtifact.status == "materialized", LearningArtifact.sink_type.is_not(None)),
            )
            .order_by(LearningArtifact.updated_at.desc())
            .limit(clean_limit)
        )
    ).scalars().all()
    sink_nodes = [
        _flow_node(
            node_id=f"sink:{row.sink_type or row.artifact_kind}:{row.id}",
            label=_status_label(row.status),
            meta=f"{row.sink_type or row.artifact_kind} · {row.sink_id or row.id}",
            entity_type=row.sink_type or "learning_artifact",
            entity_id=row.sink_id or row.id,
            icon="database" if row.sink_type == "knowledge_document" else _icon_for_artifact(row.artifact_kind),
            heat=row.quality_score or 0.7,
            tone="sink",
            payload={
                "artifact_id": row.id,
                "artifact_kind": row.artifact_kind,
                "sink_type": row.sink_type,
                "sink_id": row.sink_id,
                "status": row.status,
                "title": row.title,
            },
        )
        for row in sink_rows
    ]

    candidate_rows = (
        await db.execute(
            select(ImprovementCandidate)
            .where(candidate_filter)
            .order_by(ImprovementCandidate.priority_score.desc(), ImprovementCandidate.created_at.desc())
            .limit(clean_limit)
        )
    ).scalars().all()
    candidate_nodes = [
        _flow_node(
            node_id=f"candidate:{row.id}",
            label=row.title,
            meta=f"{row.target_type} · {_status_label(row.status)} · {row.risk_level or 'R2'}",
            entity_type="improvement_candidate",
            entity_id=row.id,
            icon="bolt" if row.target_type == "sf" else "bot" if row.target_type == "agent" else "edit",
            heat=row.priority_score or 0.62,
            tone="muted" if row.status == "rejected" else "candidate",
            payload={
                "id": row.id,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "skill_id": row.skill_id,
                "status": row.status,
                "risk_level": row.risk_level,
                "proposal": row.proposal,
                "priority_score": row.priority_score,
                "agent_draft_id": (row.external_ref_json or {}).get("agent_draft_id"),
                "implementation_queue": (row.external_ref_json or {}).get("implementation_queue"),
                "created_at": _iso(row.created_at),
            },
        )
        for row in candidate_rows
    ]

    review_nodes = [
        _flow_node(
            node_id=f"review:{row.id}",
            label=_status_label(row.status),
            meta=f"{row.title[:80]} · {row.todo_id or '待治理'}",
            entity_type="improvement_candidate",
            entity_id=row.id,
            icon="inbox" if row.status == "reviewing" else "check",
            heat=row.priority_score or 0.6,
            tone="review",
            payload={
                "id": row.id,
                "status": row.status,
                "todo_id": row.todo_id,
                "review_id": row.review_id,
                "training_job_id": (row.external_ref_json or {}).get("training_job_id"),
                "agent_draft_id": (row.external_ref_json or {}).get("agent_draft_id"),
                "implementation_queue": (row.external_ref_json or {}).get("implementation_queue"),
            },
        )
        for row in candidate_rows
        if row.status in {"reviewing", "accepted", "implemented"}
    ][:clean_limit]

    relation_rows = (
        await db.execute(
            select(LearningFlowEdge.relation, func.count(LearningFlowEdge.id))
            .where(edge_filter)
            .group_by(LearningFlowEdge.relation)
        )
    ).all()
    relation_counts = {str(relation): int(count or 0) for relation, count in relation_rows}
    narrow_filter_active = any(_clean_filter(item) for item in (event_type, source_type, artifact_kind, target_type, status, sf_tool))
    produced_count = total_artifacts if narrow_filter_active else relation_counts.get("produced", total_artifacts)
    sink_edge_count = sink_total if narrow_filter_active else (
        relation_counts.get("indexed_as", 0)
        + relation_counts.get("created_sample", 0)
        + relation_counts.get("remembered_as", 0)
    )
    candidate_edge_count = total_candidates if narrow_filter_active else relation_counts.get("proposed_change", total_candidates)
    review_edge_count = review_total if narrow_filter_active else (
        relation_counts.get("reviewed_by", 0)
        + relation_counts.get("drafted_as", 0)
        + relation_counts.get("trained_from", 0)
        + relation_counts.get("deployed_to", 0)
    )

    stages = [
        {"key": "source", "title": "数据源", "caption": "运行 / SF / Agent / 训练", "count": total_events, "tone": "source", "nodes": source_nodes},
        {"key": "event", "title": "标准事件", "caption": "统一脱敏、去重、打分", "count": total_events, "tone": "event", "nodes": event_nodes},
        {"key": "artifact", "title": "学习资产", "caption": "知识 / 样本 / 记忆 / 候选", "count": total_artifacts, "tone": "artifact", "nodes": artifact_nodes},
        {"key": "sink", "title": "自动物化", "caption": "知识库 / 训练池 / Agent 记忆", "count": sink_total, "tone": "sink", "nodes": sink_nodes},
        {"key": "candidate", "title": "迭代候选", "caption": "Skill / SF / Agent 自动建议", "count": total_candidates, "tone": "candidate", "nodes": candidate_nodes},
        {"key": "review", "title": "治理闭环", "caption": "待办评审 / 训练 / 发布", "count": review_total, "tone": "review", "nodes": review_nodes},
    ]
    connectors = [
        _flow_connector("source", "event", count=total_events, label="捕获", relation="captured_as_event"),
        _flow_connector("event", "artifact", count=produced_count, label="抽取", relation="produced"),
        _flow_connector("artifact", "sink", count=max(sink_total, sink_edge_count), label="物化", relation="materialized_to_sink"),
        _flow_connector("sink", "candidate", count=max(total_candidates, candidate_edge_count), label="反哺", relation="fed_back_to_candidate"),
        _flow_connector("candidate", "review", count=max(review_total, review_edge_count), label="治理", relation="governed_by"),
    ]
    return {
        "stages": stages,
        "connectors": connectors,
        "relation_counts": relation_counts,
        "days": clean_days,
        "skill_id": skill_id,
        "filters": {
            "department": _clean_filter(department),
            "org_unit_id": _clean_filter(org_unit_id),
            "skill_id": _clean_filter(skill_id),
            "run_id": _clean_filter(run_id),
            "event_type": _clean_filter(event_type),
            "source_type": _clean_filter(source_type),
            "artifact_kind": _clean_filter(artifact_kind),
            "target_type": _clean_filter(target_type),
            "status": _clean_filter(status),
            "sf_tool": _clean_filter(sf_tool),
        },
        "generated_at": _iso(now_bjt()),
        "governance": {
            "scope": "global" if _is_global_user(user) else "department",
            "department": None if _is_global_user(user) else _user_department(user),
            "raw_payload_returned": False,
        },
    }


async def learning_flow_journeys(
    db: AsyncSession,
    user: User,
    *,
    days: int = 30,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    source_type: str | None = None,
    artifact_kind: str | None = None,
    artifact_status: str | None = None,
    target_type: str | None = None,
    candidate_status: str | None = None,
    sf_tool: str | None = None,
    q: str | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    """Return event-centered flow journeys.

    Topology answers "how many things are flowing between stages"; journeys
    answer "for this concrete source fact, where did it go and where is it
    stuck". This is the primary observable view for the positive loop.
    """

    clean_days = _bounded_days(days)
    clean_limit = max(1, min(limit, 80))
    cutoff = now_bjt() - timedelta(days=clean_days)
    event_condition = _event_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        sf_tool=sf_tool,
    )
    if q := _clean_filter(q):
        like = f"%{q}%"
        event_condition = and_(
            event_condition,
            or_(
                LearningEvent.event_type.ilike(like),
                LearningEvent.source_type.ilike(like),
                LearningEvent.source_id.ilike(like),
                LearningEvent.redacted_summary.ilike(like),
            ),
        )

    event_rows = (
        await db.execute(
            select(LearningEvent)
            .where(event_condition)
            .order_by(LearningEvent.created_at.desc())
            .limit(min(EVENT_LIMIT_MAX, clean_limit * 6))
        )
    ).scalars().all()
    if not event_rows:
        return {
            "items": [],
            "total": 0,
            "days": clean_days,
            "generated_at": _iso(now_bjt()),
            "filters": {
                "department": _clean_filter(department),
                "org_unit_id": _clean_filter(org_unit_id),
                "skill_id": _clean_filter(skill_id),
                "run_id": _clean_filter(run_id),
                "event_type": _clean_filter(event_type),
                "source_type": _clean_filter(source_type),
                "artifact_kind": _clean_filter(artifact_kind),
                "artifact_status": _clean_filter(artifact_status),
                "target_type": _clean_filter(target_type),
                "candidate_status": _clean_filter(candidate_status),
                "sf_tool": _clean_filter(sf_tool),
                "q": _clean_filter(q),
            },
            "governance": {
                "scope": "global" if _is_global_user(user) else "department",
                "department": None if _is_global_user(user) else _user_department(user),
                "raw_payload_returned": False,
            },
        }

    event_ids = [row.id for row in event_rows]
    event_by_id = {row.id: row for row in event_rows}
    artifact_condition = _artifact_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        status=artifact_status,
        artifact_kind=artifact_kind,
        target_type=target_type,
        event_ids=event_ids,
    )
    artifact_rows = (
        await db.execute(
            select(LearningArtifact)
            .where(artifact_condition)
            .order_by(LearningArtifact.created_at.asc())
            .limit(ARTIFACT_LIMIT_MAX)
        )
    ).scalars().all()
    artifacts_by_event: dict[str, list[LearningArtifact]] = {}
    for artifact in artifact_rows:
        artifacts_by_event.setdefault(artifact.event_id, []).append(artifact)

    artifact_ids = [row.id for row in artifact_rows]
    candidate_condition = _candidate_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        status=candidate_status,
        target_type=target_type,
        sf_tool=sf_tool,
    )
    if q := _clean_filter(q):
        like = f"%{q}%"
        candidate_condition = and_(
            candidate_condition,
            or_(ImprovementCandidate.title.ilike(like), ImprovementCandidate.proposal.ilike(like)),
        )
    candidate_rows = (
        await db.execute(
            select(ImprovementCandidate)
            .where(candidate_condition)
            .order_by(ImprovementCandidate.priority_score.desc(), ImprovementCandidate.created_at.desc())
            .limit(EVENT_LIMIT_MAX)
        )
    ).scalars().all()
    candidates_by_event: dict[str, list[ImprovementCandidate]] = {event_id: [] for event_id in event_ids}
    candidates_by_artifact: dict[str, list[ImprovementCandidate]] = {}
    event_id_set = set(event_ids)
    artifact_id_set = set(artifact_ids)
    for candidate in candidate_rows:
        if candidate.source_artifact_id:
            candidates_by_artifact.setdefault(candidate.source_artifact_id, []).append(candidate)
        evidence_ids = set(str(item) for item in (candidate.evidence_event_ids_json or []))
        matched_events = event_id_set.intersection(evidence_ids)
        if not matched_events and candidate.source_artifact_id in artifact_id_set:
            artifact = next((item for item in artifact_rows if item.id == candidate.source_artifact_id), None)
            if artifact:
                matched_events.add(artifact.event_id)
        for event_id in matched_events:
            candidates_by_event.setdefault(event_id, []).append(candidate)

    job_condition = _with_common_filters(
        _scope_filter(LearningIngestionJob, user),
        LearningIngestionJob,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        created_after=cutoff,
    )
    job_condition = and_(
        job_condition,
        or_(
            LearningIngestionJob.event_id.in_(event_ids),
            LearningIngestionJob.artifact_id.in_(artifact_ids) if artifact_ids else sa.false(),
        ),
    )
    job_rows = (
        await db.execute(
            select(LearningIngestionJob)
            .where(job_condition)
            .order_by(LearningIngestionJob.created_at.asc())
            .limit(EVENT_LIMIT_MAX)
        )
    ).scalars().all()
    jobs_by_event: dict[str, list[LearningIngestionJob]] = {}
    jobs_by_artifact: dict[str, list[LearningIngestionJob]] = {}
    for job in job_rows:
        if job.event_id:
            jobs_by_event.setdefault(job.event_id, []).append(job)
        if job.artifact_id:
            jobs_by_artifact.setdefault(job.artifact_id, []).append(job)

    edge_condition = _with_common_filters(
        _scope_filter(LearningFlowEdge, user),
        LearningFlowEdge,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        created_after=cutoff,
    )
    edge_condition = and_(
        edge_condition,
        LearningFlowEdge.relation.in_(list(JOURNEY_EDGE_STEP_LABELS)),
        LearningFlowEdge.metadata_json["event_id"].astext.in_(event_ids),
    )
    edge_rows = (
        await db.execute(
            select(LearningFlowEdge)
            .where(edge_condition)
            .order_by(LearningFlowEdge.created_at.asc(), LearningFlowEdge.id.asc())
            .limit(FLOW_LIMIT_MAX)
        )
    ).scalars().all()
    edges_by_event: dict[str, list[LearningFlowEdge]] = {}
    for edge in edge_rows:
        event_id = str(_safe_dict(edge.metadata_json).get("event_id") or "")
        if event_id in event_by_id:
            edges_by_event.setdefault(event_id, []).append(edge)

    filter_needs_artifact = any(_clean_filter(item) for item in (artifact_kind, artifact_status))
    filter_needs_candidate = any(_clean_filter(item) for item in (target_type, candidate_status))
    journeys: list[dict[str, Any]] = []
    for event in event_rows:
        event_artifacts = artifacts_by_event.get(event.id, [])
        event_candidates = list(candidates_by_event.get(event.id, []))
        for artifact in event_artifacts:
            for candidate in candidates_by_artifact.get(artifact.id, []):
                if candidate not in event_candidates:
                    event_candidates.append(candidate)
        if filter_needs_artifact and not event_artifacts:
            continue
        if filter_needs_candidate and not event_candidates:
            continue

        steps: list[dict[str, Any]] = [
            _journey_step(
                "source",
                _source_type_label(event.source_type),
                entity_type=_source_node_type(event),
                entity_id=event.source_id,
                status="completed",
                at=event.created_at,
                summary=event.redacted_summary,
                meta=event.skill_id or event.run_id or event.department,
                payload={
                    "source_type": event.source_type,
                    "source_id": event.source_id,
                    "skill_id": event.skill_id,
                    "run_id": event.run_id,
                },
            ),
            _journey_step(
                "event",
                event.event_type,
                entity_type="learning_event",
                entity_id=event.id,
                status=event.status or "captured",
                at=event.created_at,
                summary=event.redacted_summary,
                meta=f"{_source_type_label(event.source_type)} · Q {round(float(event.quality_score or 0), 2)}",
                payload={"event_id": event.id, "policy": event.policy_result_json or {}},
            ),
        ]
        for edge in edges_by_event.get(event.id, [])[:10]:
            step = _journey_edge_step(edge)
            if step:
                steps.append(step)
        blockers: list[str] = []
        if not event_artifacts:
            blockers.append("no_artifact_extracted")
            steps.append(
                _journey_step(
                    "artifact",
                    "尚未抽取学习资产",
                    entity_type="learning_event",
                    entity_id=event.id,
                    status="pending",
                    at=event.updated_at,
                    summary="等待下一轮自动流动或手动捕获生成知识 / 样本 / 候选。",
                    action="backfill",
                )
            )
        for artifact in event_artifacts[:8]:
            policy = artifact.policy_result_json or {}
            artifact_status_text = artifact.status or "ready"
            if artifact_status_text in {"ready", "needs_review"}:
                if policy.get("action") == "review_first" or artifact_status_text == "needs_review":
                    blockers.append("review_required")
                elif policy.get("action") in {"auto_index", "training_candidate_only", "agent_memory_only", "improvement_candidate"}:
                    blockers.append("awaiting_materialization")
            steps.append(
                _journey_step(
                    "artifact",
                    artifact.title or _artifact_kind_label(artifact.artifact_kind),
                    entity_type="learning_artifact",
                    entity_id=artifact.id,
                    status=artifact_status_text,
                    at=artifact.created_at,
                    summary=artifact.summary,
                    meta=f"{_artifact_kind_label(artifact.artifact_kind)} · {policy.get('action') or '-'}",
                    action=str(policy.get("action") or "") or None,
                    payload={
                        "artifact_id": artifact.id,
                        "artifact_kind": artifact.artifact_kind,
                        "target_type": artifact.target_type,
                        "target_id": artifact.target_id,
                        "quality_score": artifact.quality_score,
                        "sink_type": artifact.sink_type,
                        "sink_id": artifact.sink_id,
                    },
                )
            )
            if artifact.status == "materialized" or artifact.sink_type or artifact.sink_id:
                steps.append(
                    _journey_step(
                        "sink",
                        artifact.sink_type or _artifact_kind_label(artifact.artifact_kind),
                        entity_type=artifact.sink_type or "learning_artifact",
                        entity_id=artifact.sink_id or artifact.id,
                        status="materialized",
                        at=artifact.updated_at,
                        summary=artifact.summary,
                        meta=f"{artifact.sink_type or artifact.artifact_kind} · {artifact.sink_id or artifact.id}",
                        payload={
                            "artifact_id": artifact.id,
                            "sink_type": artifact.sink_type,
                            "sink_id": artifact.sink_id,
                            "raw_payload_returned": False,
                        },
                    )
                )
            failed_jobs = [job for job in jobs_by_artifact.get(artifact.id, []) if job.status == "failed"]
            for job in failed_jobs[:2]:
                blockers.append("ingestion_failed")
                steps.append(
                    _journey_step(
                        "sink",
                        f"物化失败：{job.sink_type}",
                        entity_type="learning_ingestion_job",
                        entity_id=job.job_id,
                        status="failed",
                        at=job.finished_at or job.updated_at,
                        summary=job.error,
                        meta=f"{job.action} · attempts={job.attempts}",
                        action="retry",
                        payload={"job_id": job.job_id, "artifact_id": job.artifact_id, "sink_type": job.sink_type},
                    )
                )

        for job in jobs_by_event.get(event.id, []):
            if job.status == "failed":
                blockers.append("ingestion_failed")
                steps.append(
                    _journey_step(
                        "sink",
                        f"事件物化失败：{job.sink_type}",
                        entity_type="learning_ingestion_job",
                        entity_id=job.job_id,
                        status="failed",
                        at=job.finished_at or job.updated_at,
                        summary=job.error,
                        meta=f"{job.action} · attempts={job.attempts}",
                        action="retry",
                    )
                )

        if event_candidates:
            for candidate in event_candidates[:6]:
                if candidate.status == "open":
                    blockers.append("candidate_open")
                steps.append(
                    _journey_step(
                        "candidate",
                        candidate.title,
                        entity_type="improvement_candidate",
                        entity_id=candidate.id,
                        status=candidate.status,
                        at=candidate.created_at,
                        summary=candidate.proposal,
                        meta=f"{candidate.target_type} · {candidate.risk_level or 'R2'} · P {round(float(candidate.priority_score or 0), 2)}",
                        action="create_review" if candidate.status == "open" else None,
                        payload={
                            "candidate_id": candidate.id,
                            "target_type": candidate.target_type,
                            "target_id": candidate.target_id,
                            "todo_id": candidate.todo_id,
                            "review_id": candidate.review_id,
                            "agent_draft_id": (candidate.external_ref_json or {}).get("agent_draft_id"),
                            "governance_task_id": (candidate.external_ref_json or {}).get("governance_task_id"),
                            "implementation_queue": (candidate.external_ref_json or {}).get("implementation_queue"),
                        },
                    )
                )
                if (candidate.external_ref_json or {}).get("governance_task_id"):
                    steps.append(
                        _journey_step(
                            "review",
                            "治理任务已创建",
                            entity_type="learning_governance_task",
                            entity_id=str((candidate.external_ref_json or {}).get("governance_task_id")),
                            status=candidate.status,
                            at=candidate.updated_at,
                            summary="候选已进入可查询、可分配、可决策的治理任务对象。",
                            meta=(candidate.external_ref_json or {}).get("governance_queue") or candidate.target_type,
                            payload={
                                "candidate_id": candidate.id,
                                "governance_task_id": (candidate.external_ref_json or {}).get("governance_task_id"),
                                "governance_queue": (candidate.external_ref_json or {}).get("governance_queue"),
                                "no_auto_publish": True,
                            },
                        )
                    )
                if (candidate.external_ref_json or {}).get("agent_draft_id"):
                    steps.append(
                        _journey_step(
                            "review",
                            "Agent 治理草稿已生成",
                            entity_type="agent_draft",
                            entity_id=str((candidate.external_ref_json or {}).get("agent_draft_id")),
                            status="ready",
                            at=candidate.updated_at,
                            summary="自动添加为 Workbench 治理草稿；草稿不自动发布，仍需人工评审和 Skill Git / Agent 发布链路。",
                            meta="draft_only · no_auto_publish",
                            payload={
                                "candidate_id": candidate.id,
                                "agent_draft_id": (candidate.external_ref_json or {}).get("agent_draft_id"),
                                "implementation_queue": (candidate.external_ref_json or {}).get("implementation_queue"),
                                "draft_only": True,
                                "no_auto_publish": True,
                            },
                        )
                    )
                if (candidate.external_ref_json or {}).get("implementation_queue"):
                    steps.append(
                        _journey_step(
                            "review",
                            "Agent 实施交接队列",
                            entity_type="governance_queue",
                            entity_id=str((candidate.external_ref_json or {}).get("implementation_queue")),
                            status=(candidate.external_ref_json or {}).get("implementation_status") or "awaiting_human_implementation",
                            at=candidate.updated_at,
                            summary="候选已采纳，自动交接到 Agent 实施治理队列；仍需人工实现、审核和发布。",
                            meta="implementation_review · no_auto_publish",
                            payload={
                                "candidate_id": candidate.id,
                                "agent_draft_id": (candidate.external_ref_json or {}).get("agent_draft_id"),
                                "implementation_queue": (candidate.external_ref_json or {}).get("implementation_queue"),
                                "review_required": True,
                                "no_auto_publish": True,
                            },
                        )
                    )
                if candidate.status in {"reviewing", "accepted", "implemented"} or candidate.todo_id or candidate.review_id:
                    steps.append(
                        _journey_step(
                            "review",
                            candidate.todo_id or candidate.review_id or "治理记录",
                            entity_type="decision_request" if candidate.todo_id else "improvement_candidate",
                            entity_id=str(candidate.todo_id or candidate.id),
                            status=candidate.status,
                            at=candidate.updated_at,
                            summary="候选已进入治理链路，后续仍需按 Skill Git / 训练审批 / SF 审核发布。",
                            meta=candidate.target_type,
                            payload={
                                "candidate_id": candidate.id,
                                "todo_id": candidate.todo_id,
                                "review_id": candidate.review_id,
                                "governance_task_id": (candidate.external_ref_json or {}).get("governance_task_id"),
                                "training_job_id": (candidate.external_ref_json or {}).get("training_job_id"),
                                "agent_draft_id": (candidate.external_ref_json or {}).get("agent_draft_id"),
                                "implementation_queue": (candidate.external_ref_json or {}).get("implementation_queue"),
                            },
                        )
                    )
        elif not event_artifacts:
            pass

        last_at_values = [
            event.updated_at,
            *[artifact.updated_at for artifact in event_artifacts],
            *[candidate.updated_at for candidate in event_candidates],
            *[job.updated_at for job in jobs_by_event.get(event.id, [])],
            *[edge.created_at for edge in edges_by_event.get(event.id, [])],
        ]
        last_at = max((value for value in last_at_values if value), default=event.created_at)
        state, progress = _journey_state(sorted(set(blockers)), steps)
        journey = {
            "id": f"journey:{event.id}",
            "event_id": event.id,
            "title": event.redacted_summary or event.event_type,
            "source_type": event.source_type,
            "source_id": event.source_id,
            "skill_id": event.skill_id,
            "run_id": event.run_id,
            "department": event.department,
            "state": state,
            "progress": progress,
            "blockers": sorted(set(blockers)),
            "stage_count": len(steps),
            "artifact_count": len(event_artifacts),
            "candidate_count": len(event_candidates),
            "sink_count": len([step for step in steps if step["stage"] == "sink" and step["status"] == "materialized"]),
            "started_at": _iso(event.created_at),
            "last_at": _iso(last_at),
            "steps": steps,
        }
        journeys.append(journey)
        if len(journeys) >= clean_limit:
            break

    state_counts: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    for item in journeys:
        state_counts[item["state"]] = state_counts.get(item["state"], 0) + 1
        for blocker in item["blockers"]:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1

    return {
        "items": journeys,
        "total": len(journeys),
        "state_counts": state_counts,
        "blocker_counts": blocker_counts,
        "days": clean_days,
        "generated_at": _iso(now_bjt()),
        "filters": {
            "department": _clean_filter(department),
            "org_unit_id": _clean_filter(org_unit_id),
            "skill_id": _clean_filter(skill_id),
            "run_id": _clean_filter(run_id),
            "event_type": _clean_filter(event_type),
            "source_type": _clean_filter(source_type),
            "artifact_kind": _clean_filter(artifact_kind),
            "artifact_status": _clean_filter(artifact_status),
            "target_type": _clean_filter(target_type),
            "candidate_status": _clean_filter(candidate_status),
            "sf_tool": _clean_filter(sf_tool),
            "q": _clean_filter(q),
        },
        "governance": {
            "scope": "global" if _is_global_user(user) else "department",
            "department": None if _is_global_user(user) else _user_department(user),
            "raw_payload_returned": False,
        },
    }


async def learning_flow_bottlenecks(
    db: AsyncSession,
    user: User,
    *,
    days: int = 30,
    department: str | None = None,
    org_unit_id: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    source_type: str | None = None,
    artifact_kind: str | None = None,
    target_type: str | None = None,
    status: str | None = None,
    sf_tool: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return actionable places where the learning flow is stuck.

    The topology shows movement; this API highlights blocks that need a human
    or worker action: unmaterialized assets, review-required assets, open
    candidates, failed ingestion jobs, and captured events with no extracted
    artifact yet.
    """
    clean_days = _bounded_days(days)
    clean_limit = max(1, min(int(limit or 20), 80))
    cutoff = now_bjt() - timedelta(days=clean_days)
    stale_cutoff = now_bjt() - timedelta(minutes=10)

    event_filter = _event_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        sf_tool=sf_tool,
    )
    filtered_event_ids = select(LearningEvent.id).where(event_filter) if (event_type or source_type or sf_tool) else None
    artifact_filter = _artifact_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        artifact_kind=artifact_kind,
        event_ids=filtered_event_ids,
    )
    candidate_filter = _candidate_filter(
        user,
        created_after=cutoff,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        target_type=target_type,
        sf_tool=sf_tool,
    )

    ready_filter = and_(artifact_filter, LearningArtifact.status == "ready")
    review_filter = and_(artifact_filter, or_(LearningArtifact.status == "needs_review", LearningArtifact.review_status == "required"))
    if status:
        ready_filter = and_(ready_filter, LearningArtifact.status == status)
        review_filter = and_(review_filter, LearningArtifact.status == status)
        candidate_filter = and_(candidate_filter, ImprovementCandidate.status == status)

    ready_count = int((await db.execute(select(func.count(LearningArtifact.id)).where(ready_filter))).scalar() or 0)
    review_count = int((await db.execute(select(func.count(LearningArtifact.id)).where(review_filter))).scalar() or 0)
    open_candidate_filter = and_(candidate_filter, ImprovementCandidate.status.in_(["open", "reviewing"]))
    open_candidate_count = int((await db.execute(select(func.count(ImprovementCandidate.id)).where(open_candidate_filter))).scalar() or 0)

    scoped_artifact_ids = select(LearningArtifact.id).where(artifact_filter)
    failed_job_filter = and_(
        _ingestion_job_filter(
            user,
            created_after=cutoff,
            department=department,
            org_unit_id=org_unit_id,
            skill_id=skill_id,
            artifact_ids=scoped_artifact_ids,
        ),
        LearningIngestionJob.status.in_(["failed", "needs_review"]),
    )
    failed_job_count = int((await db.execute(select(func.count(LearningIngestionJob.id)).where(failed_job_filter))).scalar() or 0)

    governance_task_filter = _with_common_filters(
        _scope_filter(LearningGovernanceTask, user),
        LearningGovernanceTask,
        department=department,
        org_unit_id=org_unit_id,
        status=status,
        created_after=cutoff,
    )
    if target_type := _clean_filter(target_type):
        governance_task_filter = and_(governance_task_filter, LearningGovernanceTask.target_type == target_type)
    pending_governance_task_filter = and_(
        governance_task_filter,
        LearningGovernanceTask.status.in_(["pending", "in_progress"]),
    )
    pending_governance_task_count = int((
        await db.execute(select(func.count(LearningGovernanceTask.id)).where(pending_governance_task_filter))
    ).scalar() or 0)

    artifact_event_ids = select(LearningArtifact.event_id).where(_scope_filter(LearningArtifact, user))
    stalled_event_filter = and_(
        event_filter,
        LearningEvent.status == "captured",
        LearningEvent.created_at <= stale_cutoff,
        LearningEvent.id.not_in(artifact_event_ids),
    )
    stalled_event_count = int((await db.execute(select(func.count(LearningEvent.id)).where(stalled_event_filter))).scalar() or 0)

    ready_rows = (
        await db.execute(
            select(LearningArtifact)
            .where(ready_filter)
            .order_by(LearningArtifact.quality_score.desc(), LearningArtifact.created_at.asc())
            .limit(clean_limit)
        )
    ).scalars().all()
    review_rows = (
        await db.execute(
            select(LearningArtifact)
            .where(review_filter)
            .order_by(LearningArtifact.created_at.asc())
            .limit(clean_limit)
        )
    ).scalars().all()
    candidate_rows = (
        await db.execute(
            select(ImprovementCandidate)
            .where(open_candidate_filter)
            .order_by(ImprovementCandidate.priority_score.desc(), ImprovementCandidate.created_at.asc())
            .limit(clean_limit)
        )
    ).scalars().all()
    job_rows = (
        await db.execute(
            select(LearningIngestionJob)
            .where(failed_job_filter)
            .order_by(LearningIngestionJob.updated_at.desc())
            .limit(clean_limit)
        )
    ).scalars().all()
    event_rows = (
        await db.execute(
            select(LearningEvent)
            .where(stalled_event_filter)
            .order_by(LearningEvent.created_at.asc())
            .limit(clean_limit)
        )
    ).scalars().all()
    governance_task_rows = (
        await db.execute(
            select(LearningGovernanceTask)
            .where(pending_governance_task_filter)
            .order_by(LearningGovernanceTask.priority_score.desc(), LearningGovernanceTask.created_at.asc())
            .limit(clean_limit)
        )
    ).scalars().all()

    sections = [
        {
            "key": "ready_artifacts",
            "title": "待物化资产",
            "count": ready_count,
            "severity": "warning" if ready_count else "ok",
            "action": "materialize",
            "items": [_artifact_to_dict(row) for row in ready_rows],
        },
        {
            "key": "review_artifacts",
            "title": "待审核资产",
            "count": review_count,
            "severity": "warning" if review_count else "ok",
            "action": "review",
            "items": [_artifact_to_dict(row) for row in review_rows],
        },
        {
            "key": "open_candidates",
            "title": "待治理候选",
            "count": open_candidate_count,
            "severity": "warning" if open_candidate_count else "ok",
            "action": "create_review",
            "items": [_candidate_to_dict(row) for row in candidate_rows],
        },
        {
            "key": "pending_governance_tasks",
            "title": "待处理治理任务",
            "count": pending_governance_task_count,
            "severity": "warning" if pending_governance_task_count else "ok",
            "action": "decide_governance_task",
            "items": [_governance_task_to_dict(row) for row in governance_task_rows],
        },
        {
            "key": "failed_ingestions",
            "title": "物化失败任务",
            "count": failed_job_count,
            "severity": "danger" if failed_job_count else "ok",
            "action": "retry_or_inspect",
            "items": [_ingestion_job_to_dict(row) for row in job_rows],
        },
        {
            "key": "stalled_events",
            "title": "未抽取事件",
            "count": stalled_event_count,
            "severity": "warning" if stalled_event_count else "ok",
            "action": "backfill",
            "items": [_event_to_dict(row) for row in event_rows],
        },
    ]
    total = ready_count + review_count + open_candidate_count + pending_governance_task_count + failed_job_count + stalled_event_count
    return {
        "days": clean_days,
        "generated_at": _iso(now_bjt()),
        "summary": {
            "total": total,
            "ready_artifacts": ready_count,
            "review_artifacts": review_count,
            "open_candidates": open_candidate_count,
            "pending_governance_tasks": pending_governance_task_count,
            "failed_ingestions": failed_job_count,
            "stalled_events": stalled_event_count,
        },
        "sections": sections,
        "filters": {
            "department": _clean_filter(department),
            "org_unit_id": _clean_filter(org_unit_id),
            "skill_id": _clean_filter(skill_id),
            "run_id": _clean_filter(run_id),
            "event_type": _clean_filter(event_type),
            "source_type": _clean_filter(source_type),
            "artifact_kind": _clean_filter(artifact_kind),
            "target_type": _clean_filter(target_type),
            "status": _clean_filter(status),
            "sf_tool": _clean_filter(sf_tool),
        },
        "governance": {
            "scope": "global" if _is_global_user(user) else "department",
            "department": None if _is_global_user(user) else _user_department(user),
            "raw_payload_returned": False,
        },
    }


async def entity_lineage(
    db: AsyncSession,
    user: User,
    *,
    entity_type: str,
    entity_id: str,
    limit: int = 120,
) -> dict[str, Any]:
    stmt = select(LearningFlowEdge).where(
        _scope_filter(LearningFlowEdge, user),
        or_(
            and_(LearningFlowEdge.from_type == entity_type, LearningFlowEdge.from_id == entity_id),
            and_(LearningFlowEdge.to_type == entity_type, LearningFlowEdge.to_id == entity_id),
        ),
    ).order_by(LearningFlowEdge.created_at.desc()).limit(max(1, min(limit, FLOW_LIMIT_MAX)))
    rows = (await db.execute(stmt)).scalars().all()
    return {"entity": {"type": entity_type, "id": entity_id}, "edges": [_edge_to_dict(row) for row in rows], "total": len(rows)}


async def list_governance_tasks(
    db: AsyncSession,
    user: User,
    *,
    queue_id: str | None = None,
    target_type: str | None = None,
    status: str | None = None,
    candidate_id: str | None = None,
    q: str | None = None,
    days: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    cutoff = now_bjt() - timedelta(days=_bounded_days(days)) if days else None
    condition = _with_common_filters(
        _scope_filter(LearningGovernanceTask, user),
        LearningGovernanceTask,
        status=status,
        created_after=cutoff,
    )
    if queue_id := _clean_filter(queue_id):
        condition = and_(condition, LearningGovernanceTask.queue_id == queue_id)
    if target_type := _clean_filter(target_type):
        condition = and_(condition, LearningGovernanceTask.target_type == target_type)
    if candidate_id := _clean_filter(candidate_id):
        condition = and_(condition, LearningGovernanceTask.candidate_id == candidate_id)
    if q := _clean_filter(q):
        like = f"%{q}%"
        condition = and_(
            condition,
            or_(
                LearningGovernanceTask.title.ilike(like),
                LearningGovernanceTask.summary.ilike(like),
                LearningGovernanceTask.candidate_id.ilike(like),
                LearningGovernanceTask.target_id.ilike(like),
            ),
        )
    rows = (
        await db.execute(
            select(LearningGovernanceTask)
            .where(condition)
            .order_by(LearningGovernanceTask.priority_score.desc(), LearningGovernanceTask.created_at.desc())
            .limit(max(1, min(limit, 500)))
        )
    ).scalars().all()
    return {
        "items": [_governance_task_to_dict(row) for row in rows],
        "total": len(rows),
        "filters": {
            "queue_id": _clean_filter(queue_id),
            "target_type": _clean_filter(target_type),
            "status": _clean_filter(status),
            "candidate_id": _clean_filter(candidate_id),
            "q": _clean_filter(q),
        },
        "governance": {
            "scope": "global" if _is_global_user(user) else "department",
            "department": None if _is_global_user(user) else _user_department(user),
            "raw_payload_returned": False,
        },
    }


async def update_governance_task_status(
    db: AsyncSession,
    user: User,
    task_id: str,
    *,
    status: str = "completed",
    decision: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    task = await db.get(LearningGovernanceTask, task_id)
    if not task:
        raise AppError("NOT_FOUND", 404, {"detail": "learning governance task not found"})
    if not _is_global_user(user) and task.department and task.department != _user_department(user):
        raise AppError("FORBIDDEN", 403)
    if status not in {"pending", "in_progress", "completed", "rejected", "cancelled"}:
        raise AppError("PARAM_INVALID", 422, {"detail": "unsupported governance task status"})
    if decision and decision not in {"accepted", "rejected", "implemented"}:
        raise AppError("PARAM_INVALID", 422, {"detail": "unsupported governance decision"})
    now = now_bjt()
    previous_status = task.status
    task.status = status
    task.updated_at = now
    if status in {"completed", "rejected", "cancelled"}:
        task.completed_at = now
    payload = dict(task.payload_json or {})
    payload.update({
        "last_status": status,
        "previous_status": previous_status,
        "decision": decision,
        "decision_reason": _clip(reason, 1000) if reason else None,
        "decided_by": str(getattr(user, "id", "") or ""),
        "decided_at": _iso(now),
    })
    task.payload_json = {key: value for key, value in payload.items() if value is not None}
    candidate_payload = None
    candidate = await db.get(ImprovementCandidate, task.candidate_id)
    if decision:
        if decision == "rejected":
            task.status = "rejected"
            task.completed_at = now
        else:
            task.status = "completed"
            task.completed_at = now
        candidate_payload = await update_candidate_status(db, user, task.candidate_id, status=decision, reason=reason)
        candidate = await db.get(ImprovementCandidate, task.candidate_id)
    await _upsert_edge(
        db,
        from_type="learning_governance_task",
        from_id=task.id,
        to_type="improvement_candidate",
        to_id=task.candidate_id,
        relation="resolved_as",
        department=task.department,
        org_unit_id=task.org_unit_id,
        metadata={"status": task.status, "decision": decision, "reason": _clip(reason, 500) if reason else None},
    )
    return {
        "task": _governance_task_to_dict(task),
        "candidate": candidate_payload or (_candidate_to_dict(candidate) if candidate else None),
        "governance": {
            "raw_payload_returned": False,
            "decision_feedback_captured": bool(decision),
            "no_auto_publish": True,
        },
    }


async def capture_candidate_status_feedback(
    db: AsyncSession,
    candidate: ImprovementCandidate,
    *,
    user: User | None = None,
    status: str | None = None,
    reason: str | None = None,
    previous_status: str | None = None,
) -> LearningEvent:
    """Feed candidate review outcomes back into the learning loop.

    Review decisions are high-signal supervision for the AI self-iteration
    system: accepted candidates teach what should be repeated, rejected
    candidates teach the judge/agentization policy what to avoid. The function
    records only redacted candidate summaries and governance metadata.
    """
    clean_status = str(status or candidate.status or "").strip() or "updated"
    clean_reason = _clip(reason or _safe_dict(candidate.external_ref_json).get(f"{clean_status}_reason") or "", 1000)
    event_type = f"candidate.{clean_status}"
    source_id = f"{candidate.id}:{clean_status}"
    title = candidate.title or candidate.id
    summary = f"候选{_status_label(clean_status)}：{title}"
    if clean_reason:
        summary = f"{summary}；原因：{clean_reason}"
    event = await _upsert_event(
        db,
        event_type=event_type,
        source_type="improvement_candidate",
        source_id=source_id,
        source_hash=_sha256({
            "candidate_id": candidate.id,
            "status": clean_status,
            "previous_status": previous_status,
            "reason": clean_reason,
            "target_type": candidate.target_type,
            "target_id": candidate.target_id,
            "updated_at": candidate.updated_at,
        }),
        department=candidate.department,
        org_unit_id=candidate.org_unit_id,
        skill_id=candidate.skill_id,
        user_id=str(getattr(user, "id", "") or candidate.updated_by or candidate.created_by or "") or None,
        run_id=candidate.run_id,
        payload_ref=f"improvement_candidate:{candidate.id}",
        redacted_summary=_clip(summary, 1200),
        sensitivity_level="internal",
        quality_score=0.8 if clean_status in {"accepted", "implemented"} else 0.72 if clean_status == "rejected" else 0.6,
        metadata={
            "candidate_id": candidate.id,
            "status": clean_status,
            "previous_status": previous_status,
            "target_type": candidate.target_type,
            "target_id": candidate.target_id,
            "risk_level": candidate.risk_level,
            "reason": clean_reason,
            "agent_draft_id": _safe_dict(candidate.external_ref_json).get("agent_draft_id"),
            "training_job_id": _safe_dict(candidate.external_ref_json).get("training_job_id"),
        },
    )
    await _upsert_edge(
        db,
        from_type="improvement_candidate",
        from_id=candidate.id,
        to_type="learning_event",
        to_id=event.id,
        relation="feedback_event",
        department=candidate.department,
        org_unit_id=candidate.org_unit_id,
        skill_id=candidate.skill_id,
        run_id=candidate.run_id,
        metadata={"status": clean_status, "previous_status": previous_status},
    )

    if clean_status in {"accepted", "implemented"} and candidate.target_type == "agent":
        external = dict(candidate.external_ref_json or {})
        validation = _safe_dict(external.get("agent_draft_validation"))
        if validation.get("status") != "passed":
            validation_result = await validate_agent_draft_for_candidate(db, user, candidate)
            external = dict(candidate.external_ref_json or {})
        else:
            validation_result = {"status": "passed", "agent_draft_id": external.get("agent_draft_id"), "errors": []}
        if validation_result.get("status") != "passed":
            external.update({
                "implementation_status": "blocked_by_agent_draft_validation",
                "implementation_blocked_at": _iso(now_bjt()),
                "implementation_block_reason": "agent_draft_validation_failed",
                "no_auto_publish": True,
            })
            candidate.external_ref_json = external
            gap_artifact = await _upsert_artifact(
                db,
                event,
                artifact_kind="ai_system_gap_candidate",
                title="AI 自迭代缺口：Agent 草稿未通过校验，阻断实施交接",
                summary="Agent 候选已采纳，但治理草稿缺失或未通过静态安全校验，因此没有进入实施队列。",
                content={
                    "target_type": "ai_system",
                    "target_id": f"agent_draft_validation_block:{candidate.id}",
                    "proposal": "修复 Agent 治理草稿生成器或重新生成草稿；只有校验通过后才能交接到实施队列。",
                    "risk_level": "R2",
                    "expected_impact": {
                        "agent_delivery_safety": "阻止未验证草稿进入实施链路",
                        "validation_errors": validation_result.get("errors") or [],
                    },
                    "gap_type": "agent_draft_validation_gate",
                    "source_type": "candidate_feedback",
                    "governance": {
                        "candidate_only": True,
                        "review_required": True,
                        "no_auto_publish": True,
                        "no_external_side_effect": True,
                    },
                },
                labels=["candidate", "ai_system", "agent_draft_validation", "blocked_implementation"],
                target_type="ai_system",
                target_id=_agentization_target_id(f"agent_draft_validation_block:{candidate.id}"),
                quality_score=0.84,
                confidence=0.78,
            )
            if gap_artifact.sink_id:
                try:
                    await create_review_todo_for_candidate(db, user, str(gap_artifact.sink_id))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("agent draft validation gap review handoff failed candidate={} err={}", gap_artifact.sink_id, exc)
        else:
            implementation_queue = "learning_agent_implementation"
            external.update({
                "implementation_queue": implementation_queue,
                "implementation_requested_at": _iso(now_bjt()),
                "implementation_status": "awaiting_human_implementation",
                "implementation_source_event_id": event.id,
                "no_auto_publish": True,
            })
            candidate.external_ref_json = external
            await _upsert_edge(
                db,
                from_type="improvement_candidate",
                from_id=candidate.id,
                to_type="governance_queue",
                to_id=implementation_queue,
                relation="ready_for_implementation",
                department=candidate.department,
                org_unit_id=candidate.org_unit_id,
                skill_id=candidate.skill_id,
                run_id=candidate.run_id,
                metadata={
                    "status": clean_status,
                    "agent_draft_id": external.get("agent_draft_id"),
                    "review_required": True,
                    "no_auto_publish": True,
                },
            )
            if external.get("agent_draft_id"):
                await _upsert_edge(
                    db,
                    from_type="agent_draft",
                    from_id=str(external.get("agent_draft_id")),
                    to_type="governance_queue",
                    to_id=implementation_queue,
                    relation="ready_for_implementation",
                    department=candidate.department,
                    org_unit_id=candidate.org_unit_id,
                    skill_id=candidate.skill_id,
                    run_id=candidate.run_id,
                    metadata={
                        "candidate_id": candidate.id,
                        "status": clean_status,
                        "review_required": True,
                        "no_auto_publish": True,
                    },
                )

    if clean_status in {"accepted", "rejected", "implemented"}:
        supervision = {
            "source_type": "candidate_feedback",
            "candidate_id": candidate.id,
            "target_type": candidate.target_type,
            "target_id": candidate.target_id,
            "status": clean_status,
            "reason": clean_reason,
            "proposal": _clip(candidate.proposal, 1200),
            "expected_impact": _safe_dict(candidate.expected_impact_json),
            "source": {"candidate_id": candidate.id, "artifact_id": candidate.source_artifact_id},
        }
        await _upsert_artifact(
            db,
            event,
            artifact_kind="training_sample",
            title=f"候选评审监督样本：{_clip(title, 80)}",
            summary=f"{candidate.target_type} 候选被{_status_label(clean_status)}，用于训练判断何时应自动生成候选。",
            content={
                **supervision,
                "input": {
                    "title": candidate.title,
                    "proposal": _clip(candidate.proposal, 1200),
                    "target_type": candidate.target_type,
                    "risk_level": candidate.risk_level,
                },
                "expected_output": {"status": clean_status, "accepted": clean_status in {"accepted", "implemented"}},
            },
            labels=["training", "candidate_feedback", clean_status, candidate.target_type],
            target_type="training",
            target_id=candidate.skill_id or candidate.target_id or candidate.target_type,
            quality_score=0.82 if clean_status in {"accepted", "implemented"} else 0.75,
            confidence=0.78,
        )
        await _upsert_artifact(
            db,
            event,
            artifact_kind="eval_case",
            title=f"候选治理评测：{_clip(title, 80)}",
            summary="用真实评审结果回归测试 AI 候选生成、Agent 化判断和治理说明。",
            content={
                **supervision,
                "assertions": [
                    "候选生成必须说明影响模块、最小权限和风险",
                    "被拒绝的同类候选应降低置信度或补充证据",
                    "被采纳的同类候选应优先进入治理草稿而非自动发布",
                ],
            },
            labels=["eval", "candidate_feedback", clean_status, candidate.target_type],
            target_type="training",
            target_id=candidate.skill_id or candidate.target_id or candidate.target_type,
            quality_score=0.8,
            confidence=0.75,
        )

    if clean_status in {"accepted", "implemented"}:
        await _upsert_artifact(
            db,
            event,
            artifact_kind="knowledge_note",
            title=f"已验证改进判据：{_clip(title, 80)}",
            summary=f"{candidate.target_type} 候选已{_status_label(clean_status)}，可作为后续自动发现和排序的正样本。",
            content={
                "source_type": "candidate_feedback",
                "text": _clip(
                    f"候选 {candidate.id}（{candidate.target_type}/{candidate.target_id}）已{_status_label(clean_status)}。"
                    f"提案：{candidate.proposal}。原因：{clean_reason or '无'}",
                    3000,
                ),
                "source": {"candidate_id": candidate.id},
            },
            labels=["knowledge", "candidate_feedback", clean_status, candidate.target_type],
            target_type="knowledge",
            target_id=candidate.skill_id or candidate.target_id or candidate.target_type,
            quality_score=0.78,
            confidence=0.72,
        )

    if clean_status == "rejected":
        policy_target = (
            "learning_agentization_judge"
            if candidate.target_type == "agent"
            else f"learning_{_agentization_target_id(candidate.target_type)}_candidate_judge"
        )
        await _upsert_artifact(
            db,
            event,
            artifact_kind="agent_policy_candidate",
            title=f"修正候选生成策略：{_clip(title, 80)}",
            summary="候选被驳回，应让 AI 判断器学习该负反馈，降低同类误判或补充必要证据。",
            content={
                "target_type": "agent",
                "target_id": policy_target,
                "proposal": "把该拒绝原因纳入自动发现 / Agent 化判断 / 候选排序策略，避免缺证据、越权或影响面不清的候选反复出现。",
                "risk_level": "R1",
                "expected_impact": {
                    "candidate_precision": "提升自动候选命中率",
                    "rejected_candidate_target_type": candidate.target_type,
                    "reason": clean_reason,
                },
                "source_type": "candidate_feedback",
                "source": {"candidate_id": candidate.id},
            },
            labels=["candidate", "agent", "policy", "candidate_feedback", "rejected"],
            target_type="agent",
            target_id=policy_target,
            quality_score=0.76,
            confidence=0.7,
        )
    return event


async def _upsert_governance_task_for_candidate(
    db: AsyncSession,
    user: User,
    candidate: ImprovementCandidate,
    *,
    queue_id: str,
) -> LearningGovernanceTask:
    existing = (
        await db.execute(
            select(LearningGovernanceTask).where(LearningGovernanceTask.candidate_id == candidate.id)
        )
    ).scalar_one_or_none()
    payload = {
        "candidate_id": candidate.id,
        "source_artifact_id": candidate.source_artifact_id,
        "target_type": candidate.target_type,
        "target_id": candidate.target_id,
        "proposal": _clip(candidate.proposal, 2000),
        "risk_level": candidate.risk_level,
        "expected_impact": _safe_dict(candidate.expected_impact_json),
        "evidence_event_ids": candidate.evidence_event_ids_json or [],
        "review_required": True,
        "no_auto_publish": True,
    }
    now = now_bjt()
    if existing:
        existing.queue_id = queue_id
        existing.target_type = candidate.target_type
        existing.target_id = candidate.target_id
        existing.department = candidate.department
        existing.org_unit_id = candidate.org_unit_id
        existing.title = candidate.title[:240]
        existing.summary = _clip(candidate.proposal, 1000)
        existing.assignee = existing.assignee or str(getattr(user, "id", "") or "") or None
        existing.status = "pending" if existing.status in {"cancelled", "failed"} else existing.status
        existing.priority_score = float(candidate.priority_score or existing.priority_score or 0)
        existing.payload_json = {**(existing.payload_json or {}), **payload}
        existing.updated_at = now
        task = existing
    else:
        task = LearningGovernanceTask(
            id=_new_id("lgt"),
            candidate_id=candidate.id,
            queue_id=queue_id,
            target_type=candidate.target_type,
            target_id=candidate.target_id,
            department=candidate.department,
            org_unit_id=candidate.org_unit_id,
            title=candidate.title[:240],
            summary=_clip(candidate.proposal, 1000),
            assignee=str(getattr(user, "id", "") or "") or None,
            status="pending",
            priority_score=float(candidate.priority_score or 0),
            payload_json=payload,
            created_by=str(getattr(user, "id", "") or "") or None,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        await db.flush()
    await _upsert_edge(
        db,
        from_type="improvement_candidate",
        from_id=candidate.id,
        to_type="learning_governance_task",
        to_id=task.id,
        relation="queued_as",
        department=candidate.department,
        org_unit_id=candidate.org_unit_id,
        skill_id=candidate.skill_id,
        run_id=candidate.run_id,
        metadata={"queue_id": queue_id, "target_type": candidate.target_type, "no_auto_publish": True},
    )
    await _upsert_edge(
        db,
        from_type="learning_governance_task",
        from_id=task.id,
        to_type="governance_queue",
        to_id=queue_id,
        relation="reviewed_by",
        department=candidate.department,
        org_unit_id=candidate.org_unit_id,
        skill_id=candidate.skill_id,
        run_id=candidate.run_id,
        metadata={"candidate_id": candidate.id, "target_type": candidate.target_type},
    )
    return task


async def update_candidate_status(
    db: AsyncSession,
    user: User,
    candidate_id: str,
    *,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    candidate = await db.get(ImprovementCandidate, candidate_id)
    if not candidate:
        raise AppError("NOT_FOUND", 404, {"detail": "improvement candidate not found"})
    if not _is_global_user(user) and candidate.department and candidate.department != _user_department(user):
        raise AppError("FORBIDDEN", 403)
    if status not in {"open", "reviewing", "accepted", "rejected", "implemented"}:
        raise AppError("PARAM_INVALID", 422, {"detail": "unsupported candidate status"})
    previous_status = candidate.status
    candidate.status = status
    candidate.updated_by = str(getattr(user, "id", "") or "")
    meta = dict(candidate.external_ref_json or {})
    if reason:
        meta[f"{status}_reason"] = _clip(reason, 1000)
    candidate.external_ref_json = meta
    candidate.updated_at = now_bjt()
    if status != "open":
        await capture_candidate_status_feedback(
            db,
            candidate,
            user=user,
            status=status,
            reason=reason,
            previous_status=previous_status,
        )
    return _candidate_to_dict(candidate)


async def create_review_todo_for_candidate(db: AsyncSession, user: User, candidate_id: str) -> dict[str, Any]:
    candidate = await db.get(ImprovementCandidate, candidate_id)
    if not candidate:
        raise AppError("NOT_FOUND", 404, {"detail": "improvement candidate not found"})
    skill_id = candidate.skill_id or (candidate.target_id if candidate.target_type == "skill" else None)
    if not _is_global_user(user) and candidate.department and candidate.department != _user_department(user):
        raise AppError("FORBIDDEN", 403)
    if not skill_id:
        # Non-Skill candidates such as knowledge gaps and SF/Agent policy gaps do
        # not fit DecisionRequest's non-null skill_id constraint. They still need
        # a governed handoff rather than a failed button, so we move them into a
        # learning governance queue and keep the lineage auditable. A later domain
        # specific reviewer can materialize this queue into Knowledge/SF/Admin UI.
        external = dict(candidate.external_ref_json or {})
        queue_id = f"learning_{candidate.target_type or 'general'}_review"
        task = await _upsert_governance_task_for_candidate(db, user, candidate, queue_id=queue_id)
        if candidate.status == "reviewing" and external.get("governance_queue") == queue_id and external.get("governance_task_id") == task.id:
            return {
                "candidate": _candidate_to_dict(candidate),
                "governance_queue": queue_id,
                "governance_task": _governance_task_to_dict(task),
                "governance_task_id": task.id,
                "idempotent": True,
            }
        candidate.status = "reviewing"
        candidate.updated_by = str(getattr(user, "id", "") or "")
        external.update({
            "governance_queue": queue_id,
            "governance_task_id": task.id,
            "review_requested_by": str(getattr(user, "id", "") or ""),
            "review_requested_at": _iso(now_bjt()),
        })
        candidate.external_ref_json = external
        candidate.updated_at = now_bjt()
        await _upsert_edge(
            db,
            from_type="improvement_candidate",
            from_id=candidate.id,
            to_type="governance_queue",
            to_id=queue_id,
            relation="reviewed_by",
            department=candidate.department,
            org_unit_id=candidate.org_unit_id,
            skill_id=candidate.skill_id,
            run_id=candidate.run_id,
            metadata={"target_type": candidate.target_type, "target_id": candidate.target_id, "governance_task_id": task.id},
        )
        return {
            "candidate": _candidate_to_dict(candidate),
            "governance_queue": queue_id,
            "governance_task": _governance_task_to_dict(task),
            "governance_task_id": task.id,
        }
    existing = (
        await db.execute(
            select(DecisionRequest).where(
                DecisionRequest.source_type == "learning_candidate",
                DecisionRequest.source_id == candidate.id,
                DecisionRequest.kind == "review",
                DecisionRequest.skill_id == skill_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        candidate.status = "reviewing"
        candidate.todo_id = existing.id
        candidate.updated_by = str(getattr(user, "id", "") or "")
        candidate.updated_at = now_bjt()
        return {"candidate": _candidate_to_dict(candidate), "decision_request_id": existing.id, "idempotent": True}
    request_id = _new_id("ldr")[:50]
    req = DecisionRequest(
        id=request_id,
        source_type="learning_candidate",
        source_id=candidate.id,
        skill_id=skill_id,
        run_id=candidate.run_id,
        kind="review",
        title=f"评审智能闭环候选：{candidate.title}"[:200],
        summary=_clip(candidate.proposal, 500),
        payload={"candidate_id": candidate.id, "target_type": candidate.target_type, "proposal": candidate.proposal},
        aggregate_status="pending",
        sla_at=now_bjt() + timedelta(days=2),
        created_at=now_bjt(),
    )
    db.add(req)
    await db.flush()
    todo = AITodo(
        request_id=req.id,
        kind="review",
        assignee=str(getattr(user, "id", "") or ""),
        status="pending",
    )
    db.add(todo)
    await db.flush()
    candidate.status = "reviewing"
    candidate.todo_id = req.id
    candidate.updated_by = str(getattr(user, "id", "") or "")
    candidate.updated_at = now_bjt()
    await _upsert_edge(
        db,
        from_type="improvement_candidate",
        from_id=candidate.id,
        to_type="decision_request",
        to_id=req.id,
        relation="reviewed_by",
        department=candidate.department,
        org_unit_id=candidate.org_unit_id,
        skill_id=skill_id,
        run_id=candidate.run_id,
    )
    return {"candidate": _candidate_to_dict(candidate), "decision_request_id": req.id, "todo_id": todo.id}


def _agent_blueprint_contract(candidate: ImprovementCandidate, artifact: LearningArtifact | None, user: User) -> dict[str, Any]:
    content = _safe_dict(artifact.content_json) if artifact else {}
    blueprint = _safe_dict(content.get("agent_blueprint"))
    impact = _safe_dict(content.get("impact_analysis")) or _safe_dict(candidate.expected_impact_json)
    external = _safe_dict(candidate.external_ref_json)
    raw_permissions = _safe_list(blueprint.get("permissions")) or _safe_list(impact.get("permissions"))
    raw_workflow = _safe_list(blueprint.get("workflow"))
    raw_modules = _safe_list(blueprint.get("affected_modules")) or _safe_list(impact.get("affected_modules"))
    raw_risks = _safe_list(blueprint.get("guardrails")) or _safe_list(impact.get("risks"))
    agent_name = _clip(blueprint.get("name") or candidate.title or "智能闭环 Agent", 120)
    goal = _clip(blueprint.get("goal") or candidate.proposal or "把重复学习信号沉淀为可审核 Agent 流程。", 1000)
    trigger_text = _clip(blueprint.get("trigger") or "review_required", 160)
    queue_id = str(external.get("governance_queue") or "learning_agent_review")
    department = candidate.department or _user_department(user) or "业务团队"

    permissions: list[dict[str, Any]] = [
        {"action": "read_learning_lineage", "target": "learning_events/artifacts/candidates", "reversible": True},
        {"action": "create_review_candidate", "target": queue_id, "reversible": True},
    ]
    for item in raw_permissions[:12]:
        text = _clip(item, 160)
        if not text:
            continue
        permissions.append({"action": "declared_scope", "target": text, "reversible": True})

    return {
        "goal": f"{agent_name}：{goal}",
        "trigger": {
            "type": "manual",
            "expression": "",
            "description": f"{trigger_text}；草稿阶段只允许人工评审触发",
        },
        "input": [
            {"name": "学习事件血缘", "type": "json", "source": "learning", "required": True},
            {"name": "候选影响分析", "type": "json", "source": "learning", "required": True},
            {"name": "部门知识上下文", "type": "json", "source": "knowledge", "required": False},
            {"name": "MCP dry-run 诊断", "type": "json", "source": "mcp", "required": False},
        ],
        "output": {
            "adapter": "learning_governance_queue",
            "recipient": queue_id,
            "schema": {
                "title": "Agent 治理建议标题",
                "summary": "影响与风险摘要",
                "candidate_id": "学习闭环候选 ID",
                "next_actions": ["补齐权限", "评审后进入 Skill Git/Agent 配置链路"],
            },
        },
        "permissions": permissions,
        "risks": {
            "level": candidate.risk_level or "R2",
            "data_classification": "internal",
            "department": department,
            "guardrails": [
                "草稿只用于治理评审，不自动发布 Agent/Skill。",
                "不得绕过 Skill Git、Review、模型部署和外部真实动作审批。",
                *[_clip(item, 260) for item in raw_risks[:8]],
            ],
        },
        "fixtures": [
            {
                "title": agent_name,
                "summary": goal,
                "candidate_id": candidate.id,
                "target_id": candidate.target_id,
                "affected_modules": [str(item)[:40] for item in raw_modules[:8]],
                "next_actions": ["进入治理队列", "补齐最小权限", "评审后生成可发布版本"],
            }
        ],
        "test_cases": [
            {
                "name": "候选进入治理队列",
                "input": {"candidate_id": candidate.id, "status": candidate.status},
                "expected_keywords": ["治理", "候选", "人工确认"],
            },
            {
                "name": "无权限时阻断真实动作",
                "input": {"permission": "missing"},
                "expected_keywords": ["阻断", "权限", "dry-run"],
            },
            {
                "name": "评审拒绝时回流学习",
                "input": {"review": "rejected"},
                "expected_keywords": ["反馈", "复盘", "学习流"],
            },
        ],
        "lineage": {
            "learning_candidate_id": candidate.id,
            "learning_artifact_id": artifact.id if artifact else None,
            "evidence_event_ids": candidate.evidence_event_ids_json or [],
            "source": "learning_agentization",
        },
        "agent_blueprint": {
            "name": agent_name,
            "target_id": candidate.target_id,
            "workflow": [_clip(item, 260) for item in raw_workflow[:12]],
            "affected_modules": [str(item)[:40] for item in raw_modules[:12]],
        },
    }


def _agent_draft_main_py(candidate: ImprovementCandidate, contract: dict[str, Any]) -> str:
    payload = {
        "title": f"Agent 治理草稿：{candidate.title}",
        "candidate_id": candidate.id,
        "target_type": candidate.target_type,
        "target_id": candidate.target_id,
        "risk_level": candidate.risk_level or "R2",
        "governance": {
            "draft_only": True,
            "review_required": True,
            "no_auto_publish": True,
        },
        "contract_goal": contract.get("goal"),
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        '"""Generated by SkillForge learning loop.\n'
        "This draft is a governed review artifact and performs no external side effects.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "import copy\n\n"
        f"_DRAFT_PAYLOAD = {raw}\n\n"
        "def main(payload=None):\n"
        "    result = copy.deepcopy(_DRAFT_PAYLOAD)\n"
        "    result['input_received'] = bool(payload)\n"
        "    result['next_actions'] = ['人工评审', '补齐最小权限', '进入 Skill Git / Agent 发布链路']\n"
        "    return result\n"
    )


def _agent_draft_test_py(candidate: ImprovementCandidate) -> str:
    return (
        "from main import main\n\n"
        "def test_agent_draft_is_governed():\n"
        "    out = main({'dry_run': True})\n"
        f"    assert out['candidate_id'] == {candidate.id!r}\n"
        "    assert out['governance']['draft_only'] is True\n"
        "    assert out['governance']['no_auto_publish'] is True\n"
    )


async def create_agent_draft_from_candidate(
    db: AsyncSession,
    user: User,
    candidate_id: str,
    *,
    auto: bool = False,
) -> dict[str, Any]:
    """Create an idempotent governed Agent draft from an Agentization candidate.

    This is the "auto-add" bridge in the learning loop: it prepares a reviewable
    Workbench draft, but deliberately does not create/publish a Skill, deploy an
    Agent, or perform any real MCP/DingTalk/external side effect.
    """
    candidate = await db.get(ImprovementCandidate, candidate_id)
    if not candidate:
        raise AppError("NOT_FOUND", 404, {"detail": "improvement candidate not found"})
    if not _is_global_user(user) and candidate.department and candidate.department != _user_department(user):
        raise AppError("FORBIDDEN", 403)
    if candidate.target_type != "agent":
        raise AppError("PARAM_INVALID", 422, {"detail": "candidate is not an Agent candidate"})
    if candidate.status == "rejected":
        raise AppError("PARAM_INVALID", 422, {"detail": "rejected Agent candidate cannot create draft"})
    artifact = await db.get(LearningArtifact, candidate.source_artifact_id) if candidate.source_artifact_id else None
    if artifact and artifact.artifact_kind != "agent_creation_candidate":
        raise AppError("PARAM_INVALID", 422, {"detail": "candidate source is not an Agent creation artifact"})

    from app.agent_core.nodes.skill_generate import _render_skill_md
    from app.workbench.models import SkillStudioDraft
    from app.workbench.task_contract import (
        build_skill_draft_from_contract,
        default_review_state,
        render_intent_md,
        render_policy_yaml,
    )

    external = dict(candidate.external_ref_json or {})
    existing_draft_id = str(external.get("agent_draft_id") or "").strip()
    if existing_draft_id:
        existing = await db.get(SkillStudioDraft, existing_draft_id)
        if existing:
            return {
                "candidate": _candidate_to_dict(candidate),
                "agent_draft": {
                    "id": existing.id,
                    "status": existing.generation_status,
                    "created_at": _iso(existing.created_at),
                    "updated_at": _iso(existing.updated_at),
                },
                "idempotent": True,
                "governance": {
                    "draft_only": True,
                    "review_required": True,
                    "no_auto_publish": True,
                    "auto_created": bool(auto),
                },
            }

    contract = _agent_blueprint_contract(candidate, artifact, user)
    skill_struct = build_skill_draft_from_contract(contract).model_dump()
    draft_id = f"draft-agent-{uuid4().hex[:16]}"
    lineage = {
        "learning_candidate_id": candidate.id,
        "learning_artifact_id": artifact.id if artifact else None,
        "evidence_event_ids": candidate.evidence_event_ids_json or [],
        "governance_queue": external.get("governance_queue") or "learning_agent_review",
        "auto_created": bool(auto),
        "no_auto_publish": True,
    }
    extra_files = {
        "scripts/main.py": _agent_draft_main_py(candidate, contract),
        "tests/test_main.py": _agent_draft_test_py(candidate),
        "fixtures/sample_input.json": json.dumps({"candidate_id": candidate.id, "dry_run": True}, ensure_ascii=False, indent=2),
        "agent_blueprint.json": json.dumps(_safe_dict(contract.get("agent_blueprint")), ensure_ascii=False, indent=2),
        "learning-lineage.json": json.dumps(lineage, ensure_ascii=False, indent=2),
    }
    now = now_bjt()
    draft = SkillStudioDraft(
        id=draft_id,
        skill_id=None,
        branch="main",
        user_id=str(getattr(user, "id", "") or "system"),
        source_message=f"学习闭环 Agent 化候选：{candidate.title}",
        intent_md=render_intent_md(candidate.proposal, contract),
        skill_md=_render_skill_md(skill_struct),
        policy_yaml=render_policy_yaml(contract),
        contract_json=contract,
        review_state_json=default_review_state(contract),
        extra_files=extra_files,
        generation_status="ready",
        created_at=now,
        updated_at=now,
    )
    db.add(draft)
    await db.flush()

    if candidate.status == "open":
        candidate.status = "reviewing"
    candidate.updated_by = str(getattr(user, "id", "") or "")
    candidate.updated_at = now_bjt()
    external.update({
        "agent_draft_id": draft.id,
        "agent_draft_status": "ready",
        "agent_draft_created_at": _iso(now),
        "agent_draft_auto_created": bool(auto),
        "agent_draft_type": "workbench_review_draft",
        "no_auto_publish": True,
    })
    candidate.external_ref_json = external
    await _upsert_edge(
        db,
        from_type="improvement_candidate",
        from_id=candidate.id,
        to_type="agent_draft",
        to_id=draft.id,
        relation="drafted_as",
        department=candidate.department,
        org_unit_id=candidate.org_unit_id,
        skill_id=candidate.skill_id,
        run_id=candidate.run_id,
        metadata={
            "draft_kind": "workbench_review_draft",
            "auto_created": bool(auto),
            "review_required": True,
            "no_auto_publish": True,
        },
    )
    return {
        "candidate": _candidate_to_dict(candidate),
        "agent_draft": {
            "id": draft.id,
            "status": draft.generation_status,
            "created_at": _iso(draft.created_at),
            "updated_at": _iso(draft.updated_at),
        },
        "governance": {
            "draft_only": True,
            "review_required": True,
            "no_auto_publish": True,
            "auto_created": bool(auto),
        },
    }


async def create_training_job_from_candidate(db: AsyncSession, user: User, candidate_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate = await db.get(ImprovementCandidate, candidate_id)
    if not candidate:
        raise AppError("NOT_FOUND", 404, {"detail": "improvement candidate not found"})
    if not candidate.skill_id and candidate.target_type != "skill":
        raise AppError("PARAM_INVALID", 422, {"detail": "candidate is not tied to a skill"})
    from app.training.service import create_training_candidate_from_run, create_training_candidate_from_skill

    body = dict(payload or {})
    body.setdefault("title", f"智能闭环训练候选：{candidate.title}"[:200])
    body.setdefault("objective", candidate.proposal[:4000])
    body.setdefault("risk_level", candidate.risk_level or "R2")
    body.setdefault("lineage_extra", {"learning_candidate_id": candidate.id})
    if candidate.run_id:
        result = await create_training_candidate_from_run(
            db,
            user,
            candidate.run_id,
            {**body, "analysis_summary": candidate.proposal, "model_family": body.get("model_family") or f"{candidate.skill_id}:learning_loop_improvement"},
            commit=False,
        )
    else:
        skill_id = candidate.skill_id or candidate.target_id
        result = await create_training_candidate_from_skill(
            db,
            user,
            str(skill_id),
            {**body, "model_family": body.get("model_family") or f"{skill_id}:learning_loop_improvement"},
            source="learning_loop_candidate",
            lineage_extra={"learning_candidate_id": candidate.id},
            commit=False,
        )
    candidate.status = "accepted"
    external = dict(candidate.external_ref_json or {})
    if isinstance(result, dict) and result.get("id"):
        external["training_job_id"] = result.get("id")
        await _upsert_edge(
            db,
            from_type="improvement_candidate",
            from_id=candidate.id,
            to_type="training_job",
            to_id=str(result.get("id")),
            relation="trained_from",
            department=candidate.department,
            org_unit_id=candidate.org_unit_id,
            skill_id=candidate.skill_id,
            run_id=candidate.run_id,
        )
    candidate.external_ref_json = external
    candidate.updated_by = str(getattr(user, "id", "") or "")
    candidate.updated_at = now_bjt()
    await capture_candidate_status_feedback(
        db,
        candidate,
        user=user,
        status="accepted",
        reason="已从学习闭环候选创建训练任务",
        previous_status=None,
    )
    return {"candidate": _candidate_to_dict(candidate), "training_job": result}
