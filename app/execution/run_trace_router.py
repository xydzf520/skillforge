"""Run trace API for governed collection and platform intelligence calls."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import can_access_department, role_matches_any
from app.auth.dependencies import require_state_active
from app.auth.models import User
from app.codex import service as codex_service
from app.collection.models import CollectionProof
from app.common.exceptions import AppError
from app.common.models import IntelligenceAnalyzeCache, IntelligenceAnalyzeRun, UsageLog
from app.common.time_utils import isoformat_bjt, now_bjt
from app.database import get_db
from app.datasources.models import PlatformCookieAudit
from app.execution.models import DecisionLog, ExecutionRun
from app.portal.ui_service import redact_sensitive_ui_response_value
from app.skills.models import Skill


router = APIRouter()


class RunTraceAnalyzeRequest(BaseModel):
    prompt: str = ""
    include_raw: bool = False
    json_mode: bool = False
    max_output_tokens: int = Field(default=4096, ge=1, le=codex_service.PLATFORM_AI_MCP_MAX_OUTPUT_TOKENS)
    temperature: float | None = Field(default=None, ge=0, le=2)
    step_limit: int = Field(default=20, ge=1, le=codex_service.RAW_DATA_QUERY_LIMIT)
    decision_limit: int = Field(default=50, ge=1, le=codex_service.RAW_DATA_QUERY_LIMIT)
    proof_limit: int = Field(default=50, ge=1, le=codex_service.RAW_DATA_QUERY_LIMIT)
    snapshot_limit: int = Field(default=20, ge=1, le=codex_service.RAW_DATA_QUERY_LIMIT)


def _dt(value) -> str | None:
    return isoformat_bjt(value) if value else None


def _execution_metadata(row: ExecutionRun) -> dict[str, Any]:
    return redact_sensitive_ui_response_value(row.metadata_json or {})


def _execution_run(row: ExecutionRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "skill_id": row.skill_id,
        "trigger_type": row.trigger_type,
        "run_mode": row.run_mode,
        "parent_run_id": row.parent_run_id,
        "status": row.status,
        "started_at": _dt(row.started_at),
        "completed_at": _dt(row.completed_at),
        "source_instance_id": row.source_instance_id,
        "metadata_json": _execution_metadata(row),
    }


def _collection_proof(row: CollectionProof) -> dict[str, Any]:
    return {
        "proof_id": row.proof_id,
        "tool_name": getattr(row, "mcp_tool_name", None),
        "run_id": row.run_id,
        "skill_id": row.skill_id,
        "platform": row.platform,
        "shop_id": row.shop_id,
        "data_scope": row.data_scope,
        "endpoint_family": row.endpoint_family,
        "warning_group": row.warning_group,
        "credential_scope": row.credential_scope,
        "credential_plan_id": row.credential_plan_id,
        "credential_alias": row.credential_alias,
        "browser_slot_id": row.browser_slot_id,
        "slot_id": row.browser_slot_id,
        "egress_group": None,
        "retry_of_proof_id": row.retry_of_proof_id,
        "status": row.status,
        "http_status": row.http_status,
        "error_code": row.error_code,
        "response_hash": row.response_hash,
        "data_keys": row.data_keys or [],
        "row_count": row.row_count,
        "warning_signal": row.warning_signal or {},
        "created_at": _dt(row.created_at),
    }


def _analyze_run(row: IntelligenceAnalyzeRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "cache_key": row.cache_key,
        "cache_id": row.cache_id,
        "cache_hit": row.cache_hit,
        "cache_hit_of_run_id": row.cache_hit_of_run_id,
        "skill_id": row.skill_id,
        "skill_git_commit_full": row.skill_git_commit_full,
        "prompt_git_ref": row.prompt_git_ref,
        "run_id": row.run_id,
        "instance_id": row.instance_id,
        "department": row.department,
        "model": row.model,
        "analysis_backend": row.analysis_backend,
        "analysis_agent_id": row.analysis_agent_id,
        "analysis_delegate_route": redact_sensitive_ui_response_value(row.analysis_delegate_route or {}),
        "prompt_version": row.prompt_version,
        "prompt_hash": row.prompt_hash,
        "context_hash": row.context_hash,
        "data_health_ratio": float(row.data_health_ratio) if row.data_health_ratio is not None else None,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "total_tokens": row.total_tokens,
        "cost_usd": float(row.cost_usd) if row.cost_usd is not None else None,
        "duration_ms": row.duration_ms,
        "degraded": row.degraded,
        "degraded_reason": row.degraded_reason,
        "output_hash": row.output_hash,
        "llm_output_hash": row.llm_output_hash,
        "evidence_passed": row.evidence_passed,
        "error_code": row.error_code,
        "created_at": _dt(row.created_at),
    }


def _usage_log(row: UsageLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "ts": _dt(row.ts),
        "department": row.department,
        "skill_id": row.skill_id,
        "conversation_id": row.conversation_id,
        "call_source": row.call_source,
        "model": row.model,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cache_read_tokens": row.cache_read_tokens,
        "cache_write_tokens": row.cache_write_tokens,
        "cost_usd": float(row.cost_usd) if row.cost_usd is not None else None,
        "prompt_hash": row.prompt_hash,
        "duration_ms": row.duration_ms,
        "metadata_json": redact_sensitive_ui_response_value(row.metadata_json or {}),
    }


def _cookie_audit(row: PlatformCookieAudit) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_id": row.source_id,
        "platform": row.platform,
        "shop_id": row.shop_id,
        "action": row.action,
        "auth_source": row.auth_source,
        "actor_user_id": row.actor_user_id,
        "detail": redact_sensitive_ui_response_value(row.detail or {}),
        "created_at": _dt(row.created_at),
    }


def _decision_log(row: DecisionLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "skill_id": row.skill_id,
        "input_snapshot": redact_sensitive_ui_response_value(row.input_snapshot),
        "output_result": redact_sensitive_ui_response_value(row.output_result),
        "suggested_action": row.suggested_action,
        "approval_status": row.approval_status,
        "user_action": row.user_action,
        "rating": row.rating,
        "model_id": row.model_id,
        "token_count": row.token_count,
        "is_sandbox": row.is_sandbox,
        "created_at": _dt(row.created_at),
    }


def _analyze_cache(row: IntelligenceAnalyzeCache) -> dict[str, Any]:
    return {
        "id": row.id,
        "cache_key": row.cache_key,
        "model": row.model,
        "prompt_hash": row.prompt_hash,
        "context_hash": row.context_hash,
        "output_hash": row.output_hash,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "total_tokens": row.total_tokens,
        "evidence_passed": row.evidence_passed,
        "first_seen_run_id": row.first_seen_run_id,
        "hit_count": row.hit_count,
        "last_hit_at": _dt(row.last_hit_at),
        "created_at": _dt(row.created_at),
    }


def _run_owner_id(run: ExecutionRun) -> str | None:
    metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
    owner = (
        metadata.get("user_id")
        or metadata.get("requester_id")
        or metadata.get("created_by")
        or metadata.get("owner")
        or metadata.get("submitter")
    )
    if owner:
        return str(owner)
    trigger_type = str(run.trigger_type or "")
    if trigger_type.startswith("portal:"):
        return trigger_type.split(":", 1)[1] or None
    return None


def _is_portal_run(run: ExecutionRun) -> bool:
    metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
    return bool(metadata.get("portal_submission_id")) or str(run.trigger_type or "").startswith("portal:")


async def _assert_trace_access(db: AsyncSession, user: User, run: ExecutionRun) -> None:
    metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
    run_owner = _run_owner_id(run)
    portal_submission_id = metadata.get("portal_submission_id")
    if not run_owner and portal_submission_id:
        from app.portal.models import SkillSubmission

        submission = await db.get(SkillSubmission, str(portal_submission_id))
        if submission and getattr(submission, "requester_id", None):
            run_owner = str(submission.requester_id)
    if run_owner and run_owner == user.id:
        return
    if _is_portal_run(run):
        if role_matches_any(user, ("admin", "system_admin")) or bool(getattr(user, "can_view_all", False)):
            return
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    if role_matches_any(user, ("admin", "system_admin", "ai_engineer", "aibp")):
        return
    if bool(getattr(user, "can_view_all", False)):
        return

    skill = None
    if run.skill_id:
        skill = await db.get(Skill, run.skill_id)
        if skill and getattr(skill, "owner", None) == user.id:
            return

    department = (
        metadata.get("department")
        or metadata.get("skill_department")
        or getattr(skill, "org_unit_id", None)
        or getattr(skill, "department", None)
    )
    if department and department == getattr(user, "department", None):
        return
    if await can_access_department(db, user, department):
        return

    raise AppError("AUTH_PERMISSION_DENIED", 403)


@router.get("/runs/{run_id}/trace")
async def get_run_trace(
    run_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(ExecutionRun, run_id)
    if not run:
        raise AppError("NOT_FOUND", 404, {"detail": "execution run 不存在"})
    await _assert_trace_access(db, current_user, run)

    collection_rows = (await db.execute(
        select(CollectionProof)
        .where(CollectionProof.run_id == run_id)
        .order_by(CollectionProof.created_at.asc(), CollectionProof.id.asc())
    )).scalars().all()
    analyze_rows = (await db.execute(
        select(IntelligenceAnalyzeRun)
        .where(IntelligenceAnalyzeRun.run_id == run_id)
        .order_by(IntelligenceAnalyzeRun.created_at.asc(), IntelligenceAnalyzeRun.id.asc())
    )).scalars().all()
    usage_rows = (await db.execute(
        select(UsageLog)
        .where(UsageLog.metadata_json["run_id"].as_string() == run_id)
        .order_by(UsageLog.ts.asc(), UsageLog.id.asc())
    )).scalars().all()
    decision_rows = (await db.execute(
        select(DecisionLog)
        .where(DecisionLog.run_id == run_id)
        .order_by(DecisionLog.created_at.asc(), DecisionLog.id.asc())
    )).scalars().all()
    cache_ids = sorted({row.cache_id for row in analyze_rows if row.cache_id})
    cache_rows = []
    if cache_ids:
        cache_rows = (await db.execute(
            select(IntelligenceAnalyzeCache)
            .where(IntelligenceAnalyzeCache.id.in_(cache_ids))
            .order_by(IntelligenceAnalyzeCache.id.asc())
        )).scalars().all()

    platform_shop_pairs = sorted({
        (row.platform, row.shop_id)
        for row in collection_rows
        if row.platform and row.shop_id
    })
    cookie_rows = []
    if platform_shop_pairs:
        stmt = select(PlatformCookieAudit).where(or_(*[
            and_(
                PlatformCookieAudit.platform == platform,
                PlatformCookieAudit.shop_id == shop_id,
            )
            for platform, shop_id in platform_shop_pairs
        ]))
        started_at = run.started_at
        completed_at = run.completed_at or now_bjt()
        if started_at and completed_at:
            stmt = stmt.where(PlatformCookieAudit.created_at >= started_at)
            stmt = stmt.where(PlatformCookieAudit.created_at <= completed_at)
        cookie_rows = (await db.execute(
            stmt.order_by(PlatformCookieAudit.created_at.desc()).limit(100)
        )).scalars().all()

    return {
        "run_id": run_id,
        "execution_run": _execution_run(run),
        "collection_proofs": [_collection_proof(row) for row in collection_rows],
        "intelligence_analyze_runs": [_analyze_run(row) for row in analyze_rows],
        "intelligence_analyze_cache": [_analyze_cache(row) for row in cache_rows],
        "usage_logs": [_usage_log(row) for row in usage_rows],
        "platform_cookie_audit": [_cookie_audit(row) for row in cookie_rows],
        "decision_log": [_decision_log(row) for row in decision_rows],
        "_skillforge_meta": {
            "status": run.status,
            "skill_id": run.skill_id,
            "trace_sections": [
                "execution_run",
                "collection_proofs",
                "intelligence_analyze_runs",
                "intelligence_analyze_cache",
                "usage_logs",
                "platform_cookie_audit",
                "decision_log",
            ],
        },
    }


@router.post("/runs/{run_id}/trace/analyze")
async def analyze_run_trace(
    run_id: str,
    body: RunTraceAnalyzeRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(ExecutionRun, run_id)
    if not run:
        raise AppError("NOT_FOUND", 404, {"detail": "execution run 不存在"})
    await _assert_trace_access(db, current_user, run)
    return await codex_service.analyze_execution_run_with_platform_ai(
        db,
        current_user,
        run_id=run_id,
        skill_id=run.skill_id,
        prompt=body.prompt,
        include_raw=body.include_raw,
        json_mode=body.json_mode,
        max_output_tokens=body.max_output_tokens,
        temperature=body.temperature,
        step_limit=body.step_limit,
        decision_limit=body.decision_limit,
        proof_limit=body.proof_limit,
        snapshot_limit=body.snapshot_limit,
    )
