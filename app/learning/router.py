"""Intelligence learning loop API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_state_active_web_or_cli
from app.auth.models import User
from app.common.cache import cached, invalidate_page_cache
from app.database import get_db
from app.learning.service import (
    backfill_learning_events,
    ensure_full_history_platform_training_candidate,
    create_agent_draft_from_candidate,
    create_review_todo_for_candidate,
    create_training_job_from_candidate,
    entity_lineage,
    ignore_artifact,
    learning_flow_bottlenecks,
    learning_flow_graph,
    learning_flow_journeys,
    learning_flow_topology,
    learning_pulse,
    learning_pulse_drilldown,
    learning_summary,
    learning_automation_status,
    list_governance_tasks,
    list_improvement_candidates,
    list_learning_artifacts,
    list_learning_events,
    materialize_ecommerce_learning_flow_dataset,
    materialize_artifact,
    run_learning_automation_once,
    run_agentization_judgement,
    run_system_full_training_flow,
    run_self_iteration_gap_audit,
    training_dataset_manifest,
    training_job_dataset_samples,
    update_candidate_status,
    update_governance_task_status,
)

router = APIRouter()
require_learning_web_or_cli_user = require_state_active_web_or_cli
_CACHE_KEY_EXCLUDES = frozenset({"db", "session", "request", "background_tasks", "current_user"})
_CACHE_SCOPE = ("current_user.id", "current_user.role", "current_user.department", "current_user.can_view_all")

class LearningBackfillRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=200, ge=1, le=1000)
    materialize: bool = False


class LearningAutomationRunRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=300, ge=1, le=1000)
    materialize: bool = True


class FullHistoryTrainingRequest(BaseModel):
    min_sample_count: int = Field(default=4, ge=1, le=1000)


class EcommerceDatasetMaterializeRequest(BaseModel):
    namespace: str = Field(default="ecommerce.learning_flow.eval.v1", max_length=160)
    limit: int = Field(default=1000, ge=1, le=1000)


class SystemFullTrainingRequest(BaseModel):
    days: int = Field(default=3650, ge=1, le=3650)
    per_source_limit: int = Field(default=50000, ge=1, le=50000)
    min_sample_count: int = Field(default=4, ge=1, le=1000)
    advance: bool = True
    max_jobs: int = Field(default=3, ge=1, le=20)
    capture_sources: bool = False


class LearningAgentizationRunRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=20, ge=1, le=80)
    use_ai: bool = True


class LearningSelfAuditRunRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=50, ge=1, le=200)
    auto_remediate: bool = True
    use_ai: bool = True


class ArtifactMaterializeRequest(BaseModel):
    force: bool = False


class ArtifactIgnoreRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class CandidateStatusRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class GovernanceTaskStatusRequest(BaseModel):
    status: str = Field(default="completed", max_length=30)
    decision: str | None = Field(default=None, max_length=30)
    reason: str | None = Field(default=None, max_length=1000)


class CandidateTrainingRequest(BaseModel):
    model_family: str | None = Field(default=None, max_length=100)
    job_type: str | None = Field(default=None, max_length=30)
    training_strategy: str | None = Field(default=None, max_length=80)
    dataset_ref: str | None = Field(default=None, max_length=200)
    target_gateway_id: str | None = Field(default=None, max_length=50)
    base_model: str | None = Field(default=None, max_length=160)
    target_metric: str | None = Field(default=None, max_length=120)
    estimated_gpu_hours: float | None = None
    risk_notes: str | None = Field(default=None, max_length=1000)


def _parse_automation_ts(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _merge_latest_automation_run(automation: dict, latest_run: dict | None) -> dict:
    """Align visible AI-loop status with persisted automation history."""
    if not latest_run or automation.get("status") == "running":
        return automation
    if latest_run.get("id") and latest_run.get("id") == automation.get("run_id"):
        current_result = automation.get("result")
        if current_result is None or current_result == {}:
            automation["result"] = latest_run.get("result")
        if not automation.get("started_at"):
            automation["started_at"] = latest_run.get("started_at")
        if not automation.get("finished_at"):
            automation["finished_at"] = latest_run.get("finished_at")
        if not automation.get("error"):
            automation["error"] = latest_run.get("error")
        return automation
    current_ts = _parse_automation_ts(automation.get("finished_at") or automation.get("started_at"))
    latest_ts = _parse_automation_ts(latest_run.get("finished_at") or latest_run.get("started_at"))
    should_merge = automation.get("status") in {None, "never_run"}
    if not should_merge and latest_run.get("id") and latest_run.get("id") != automation.get("run_id"):
        should_merge = current_ts is None or latest_ts is None or latest_ts >= current_ts
    if not should_merge and automation.get("status") == "failed" and latest_run.get("status") == "succeeded":
        should_merge = current_ts is None or latest_ts is None or latest_ts >= current_ts
    if should_merge:
        automation.update({
            "status": latest_run.get("status"),
            "run_id": latest_run.get("id"),
            "started_at": latest_run.get("started_at"),
            "finished_at": latest_run.get("finished_at"),
            "error": latest_run.get("error"),
            "result": latest_run.get("result"),
        })
    return automation


async def _invalidate_learning_related() -> None:
    await invalidate_page_cache("learning", "knowledge", "training", "sf", "agent")


@router.get("/automation-status")
async def get_learning_automation_status(
    days: int = Query(7, ge=1, le=365),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    from app.learning.worker import get_learning_auto_flow_state

    backlog = await learning_automation_status(db, current_user, days=days)
    automation = get_learning_auto_flow_state()
    latest_run = (backlog.get("latest") or {}).get("automation_run")
    automation = _merge_latest_automation_run(automation, latest_run)
    return {"automation": automation, **backlog}


@router.post("/automation/run")
async def post_learning_automation_run(
    body: LearningAutomationRunRequest,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await run_learning_automation_once(
        db,
        current_user,
        days=body.days,
        limit=body.limit,
        materialize=body.materialize,
        trigger_type="manual",
        created_by=str(getattr(current_user, "id", "") or ""),
    )
    await _invalidate_learning_related()
    return result


@router.post("/agentization/run")
async def post_learning_agentization_run(
    body: LearningAgentizationRunRequest,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    """AI/heuristic judgement: should a learning signal become an Agent candidate."""
    result = await run_agentization_judgement(
        db,
        current_user,
        days=body.days,
        limit=body.limit,
        use_ai=body.use_ai,
    )
    await _invalidate_learning_related()
    return result


@router.post("/self-audit/run")
async def post_learning_self_audit_run(
    body: LearningSelfAuditRunRequest,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await run_self_iteration_gap_audit(
        db,
        current_user,
        days=body.days,
        limit=body.limit,
        auto_remediate=body.auto_remediate,
        use_ai=body.use_ai,
    )
    await _invalidate_learning_related()
    return result


@router.get("/summary")
async def get_learning_summary(
    days: int = Query(30, ge=1, le=365),
    department: str | None = Query(None, max_length=100),
    org_unit_id: str | None = Query(None, max_length=50),
    skill_id: str | None = Query(None, max_length=100),
    run_id: str | None = Query(None, max_length=100),
    event_type: str | None = Query(None, max_length=80),
    source_type: str | None = Query(None, max_length=50),
    artifact_kind: str | None = Query(None, max_length=50),
    target_type: str | None = Query(None, max_length=40),
    status: str | None = Query(None, max_length=30),
    sf_tool: str | None = Query(None, max_length=120),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await learning_summary(
        db,
        current_user,
        days=days,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        artifact_kind=artifact_kind,
        target_type=target_type,
        status=status,
        sf_tool=sf_tool,
    )


@router.get("/home")
@cached(
    "learning:home",
    ttl=60,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_learning_home(
    days: int = Query(30, ge=1, le=365),
    department: str | None = Query(None, max_length=100),
    org_unit_id: str | None = Query(None, max_length=50),
    skill_id: str | None = Query(None, max_length=100),
    run_id: str | None = Query(None, max_length=100),
    event_type: str | None = Query(None, max_length=80),
    source_type: str | None = Query(None, max_length=50),
    artifact_kind: str | None = Query(None, max_length=50),
    target_type: str | None = Query(None, max_length=40),
    status: str | None = Query(None, max_length=30),
    sf_tool: str | None = Query(None, max_length=120),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    from app.learning.worker import get_learning_auto_flow_state

    summary = await learning_summary(
        db,
        current_user,
        days=days,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        artifact_kind=artifact_kind,
        target_type=target_type,
        status=status,
        sf_tool=sf_tool,
    )
    topology = await learning_flow_topology(
        db,
        current_user,
        days=days,
        skill_id=skill_id,
        department=department,
        org_unit_id=org_unit_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        artifact_kind=artifact_kind,
        target_type=target_type,
        status=status,
        sf_tool=sf_tool,
        limit_nodes=5,
    )
    backlog = await learning_automation_status(db, current_user, days=days)
    automation = _merge_latest_automation_run(
        get_learning_auto_flow_state(),
        (backlog.get("latest") or {}).get("automation_run"),
    )
    return {
        "summary": summary,
        "topology": topology,
        "automation": {"automation": automation, **backlog},
    }


@router.get("/pulse")
async def get_learning_pulse(
    days: int = Query(30, ge=1, le=365),
    department: str | None = Query(None, max_length=100),
    org_unit_id: str | None = Query(None, max_length=50),
    skill_id: str | None = Query(None, max_length=100),
    run_id: str | None = Query(None, max_length=100),
    event_type: str | None = Query(None, max_length=80),
    source_type: str | None = Query(None, max_length=50),
    artifact_kind: str | None = Query(None, max_length=50),
    target_type: str | None = Query(None, max_length=40),
    sf_tool: str | None = Query(None, max_length=120),
    limit: int = Query(6, ge=1, le=12),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await learning_pulse(
        db,
        current_user,
        days=days,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        artifact_kind=artifact_kind,
        target_type=target_type,
        sf_tool=sf_tool,
        limit=limit,
    )


@router.get("/pulse/drilldown")
async def get_learning_pulse_drilldown(
    module_key: str = Query(..., max_length=80),
    section_key: str = Query(..., max_length=80),
    days: int = Query(30, ge=1, le=365),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    department: str | None = Query(None, max_length=100),
    org_unit_id: str | None = Query(None, max_length=50),
    skill_id: str | None = Query(None, max_length=100),
    run_id: str | None = Query(None, max_length=100),
    event_type: str | None = Query(None, max_length=80),
    source_type: str | None = Query(None, max_length=50),
    artifact_kind: str | None = Query(None, max_length=50),
    target_type: str | None = Query(None, max_length=40),
    sf_tool: str | None = Query(None, max_length=120),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await learning_pulse_drilldown(
        db,
        current_user,
        module_key=module_key,
        section_key=section_key,
        days=days,
        page=page,
        page_size=page_size,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        artifact_kind=artifact_kind,
        target_type=target_type,
        sf_tool=sf_tool,
    )


@router.get("/events")
async def get_learning_events(
    days: int | None = Query(None, ge=1, le=365),
    event_type: str | None = Query(None, max_length=80),
    source_type: str | None = Query(None, max_length=50),
    skill_id: str | None = Query(None, max_length=100),
    department: str | None = Query(None, max_length=100),
    org_unit_id: str | None = Query(None, max_length=50),
    run_id: str | None = Query(None, max_length=100),
    modality: str | None = Query(None, max_length=20),
    sensitivity_level: str | None = Query(None, max_length=20),
    sf_tool: str | None = Query(None, max_length=120),
    status: str | None = Query(None, max_length=30),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_learning_events(
        db,
        current_user,
        event_type=event_type,
        source_type=source_type,
        skill_id=skill_id,
        department=department,
        org_unit_id=org_unit_id,
        run_id=run_id,
        modality=modality,
        sensitivity_level=sensitivity_level,
        sf_tool=sf_tool,
        status=status,
        q=q,
        days=days,
        limit=limit,
    )


@router.get("/artifacts")
async def get_learning_artifacts(
    days: int | None = Query(None, ge=1, le=365),
    artifact_kind: str | None = Query(None, max_length=50),
    skill_id: str | None = Query(None, max_length=100),
    department: str | None = Query(None, max_length=100),
    org_unit_id: str | None = Query(None, max_length=50),
    run_id: str | None = Query(None, max_length=100),
    target_type: str | None = Query(None, max_length=40),
    sink_type: str | None = Query(None, max_length=40),
    sensitivity_level: str | None = Query(None, max_length=20),
    event_type: str | None = Query(None, max_length=80),
    source_type: str | None = Query(None, max_length=50),
    sf_tool: str | None = Query(None, max_length=120),
    status: str | None = Query(None, max_length=30),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_learning_artifacts(
        db,
        current_user,
        artifact_kind=artifact_kind,
        skill_id=skill_id,
        department=department,
        org_unit_id=org_unit_id,
        run_id=run_id,
        target_type=target_type,
        sink_type=sink_type,
        sensitivity_level=sensitivity_level,
        event_type=event_type,
        source_type=source_type,
        sf_tool=sf_tool,
        status=status,
        q=q,
        days=days,
        limit=limit,
    )


@router.get("/candidates")
async def get_learning_candidates(
    days: int | None = Query(None, ge=1, le=365),
    target_type: str | None = Query(None, max_length=40),
    skill_id: str | None = Query(None, max_length=100),
    department: str | None = Query(None, max_length=100),
    org_unit_id: str | None = Query(None, max_length=50),
    run_id: str | None = Query(None, max_length=100),
    target_id: str | None = Query(None, max_length=120),
    risk_level: str | None = Query(None, max_length=20),
    sf_tool: str | None = Query(None, max_length=120),
    status: str | None = Query(None, max_length=30),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_improvement_candidates(
        db,
        current_user,
        target_type=target_type,
        skill_id=skill_id,
        department=department,
        org_unit_id=org_unit_id,
        run_id=run_id,
        target_id=target_id,
        risk_level=risk_level,
        sf_tool=sf_tool,
        status=status,
        q=q,
        days=days,
        limit=limit,
    )


@router.get("/governance-tasks")
async def get_learning_governance_tasks(
    days: int | None = Query(None, ge=1, le=365),
    queue_id: str | None = Query(None, max_length=80),
    target_type: str | None = Query(None, max_length=40),
    status: str | None = Query(None, max_length=30),
    candidate_id: str | None = Query(None, max_length=50),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_governance_tasks(
        db,
        current_user,
        queue_id=queue_id,
        target_type=target_type,
        status=status,
        candidate_id=candidate_id,
        q=q,
        days=days,
        limit=limit,
    )


@router.post("/governance-tasks/{task_id}/status")
async def post_learning_governance_task_status(
    task_id: str,
    body: GovernanceTaskStatusRequest | None = None,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    request = body or GovernanceTaskStatusRequest()
    result = await update_governance_task_status(
        db,
        current_user,
        task_id,
        status=request.status,
        decision=request.decision,
        reason=request.reason,
    )
    await _invalidate_learning_related()
    return result


@router.get("/flow-graph")
async def get_learning_flow_graph(
    days: int = Query(30, ge=1, le=365),
    skill_id: str | None = Query(None, max_length=100),
    department: str | None = Query(None, max_length=100),
    org_unit_id: str | None = Query(None, max_length=50),
    run_id: str | None = Query(None, max_length=100),
    relation: str | None = Query(None, max_length=50),
    event_type: str | None = Query(None, max_length=80),
    source_type: str | None = Query(None, max_length=50),
    target_type: str | None = Query(None, max_length=50),
    sf_tool: str | None = Query(None, max_length=120),
    limit: int = Query(250, ge=1, le=600),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await learning_flow_graph(
        db,
        current_user,
        days=days,
        skill_id=skill_id,
        department=department,
        org_unit_id=org_unit_id,
        run_id=run_id,
        relation=relation,
        event_type=event_type,
        source_type=source_type,
        target_type=target_type,
        sf_tool=sf_tool,
        limit=limit,
    )


@router.get("/flow-topology")
async def get_learning_flow_topology(
    days: int = Query(30, ge=1, le=365),
    skill_id: str | None = Query(None, max_length=100),
    department: str | None = Query(None, max_length=100),
    org_unit_id: str | None = Query(None, max_length=50),
    run_id: str | None = Query(None, max_length=100),
    event_type: str | None = Query(None, max_length=80),
    source_type: str | None = Query(None, max_length=50),
    artifact_kind: str | None = Query(None, max_length=50),
    target_type: str | None = Query(None, max_length=40),
    status: str | None = Query(None, max_length=30),
    sf_tool: str | None = Query(None, max_length=120),
    limit_nodes: int = Query(5, ge=1, le=12),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await learning_flow_topology(
        db,
        current_user,
        days=days,
        skill_id=skill_id,
        department=department,
        org_unit_id=org_unit_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        artifact_kind=artifact_kind,
        target_type=target_type,
        status=status,
        sf_tool=sf_tool,
        limit_nodes=limit_nodes,
    )


@router.get("/flow-journeys")
async def get_learning_flow_journeys(
    days: int = Query(30, ge=1, le=365),
    department: str | None = Query(None, max_length=100),
    org_unit_id: str | None = Query(None, max_length=50),
    skill_id: str | None = Query(None, max_length=100),
    run_id: str | None = Query(None, max_length=100),
    event_type: str | None = Query(None, max_length=80),
    source_type: str | None = Query(None, max_length=50),
    artifact_kind: str | None = Query(None, max_length=50),
    artifact_status: str | None = Query(None, max_length=30),
    target_type: str | None = Query(None, max_length=40),
    candidate_status: str | None = Query(None, max_length=30),
    sf_tool: str | None = Query(None, max_length=120),
    q: str | None = Query(None, max_length=160),
    limit: int = Query(12, ge=1, le=80),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await learning_flow_journeys(
        db,
        current_user,
        days=days,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        artifact_kind=artifact_kind,
        artifact_status=artifact_status,
        target_type=target_type,
        candidate_status=candidate_status,
        sf_tool=sf_tool,
        q=q,
        limit=limit,
    )


@router.get("/bottlenecks")
async def get_learning_flow_bottlenecks(
    days: int = Query(30, ge=1, le=365),
    department: str | None = Query(None, max_length=100),
    org_unit_id: str | None = Query(None, max_length=50),
    skill_id: str | None = Query(None, max_length=100),
    run_id: str | None = Query(None, max_length=100),
    event_type: str | None = Query(None, max_length=80),
    source_type: str | None = Query(None, max_length=50),
    artifact_kind: str | None = Query(None, max_length=50),
    target_type: str | None = Query(None, max_length=40),
    status: str | None = Query(None, max_length=30),
    sf_tool: str | None = Query(None, max_length=120),
    limit: int = Query(20, ge=1, le=80),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await learning_flow_bottlenecks(
        db,
        current_user,
        days=days,
        department=department,
        org_unit_id=org_unit_id,
        skill_id=skill_id,
        run_id=run_id,
        event_type=event_type,
        source_type=source_type,
        artifact_kind=artifact_kind,
        target_type=target_type,
        status=status,
        sf_tool=sf_tool,
        limit=limit,
    )


@router.get("/entities/{entity_type}/{entity_id}/lineage")
async def get_entity_lineage(
    entity_type: str = Path(..., max_length=50),
    entity_id: str = Path(..., max_length=120),
    limit: int = Query(120, ge=1, le=600),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await entity_lineage(db, current_user, entity_type=entity_type, entity_id=entity_id, limit=limit)


@router.get("/training-manifest")
async def get_training_manifest(
    skill_id: str | None = Query(None, max_length=100),
    limit: int = Query(500, ge=1, le=1000),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await training_dataset_manifest(db, current_user, skill_id=skill_id, limit=limit)


@router.get("/training-jobs/{job_id}/dataset")
async def get_training_job_dataset(
    job_id: str = Path(..., max_length=50),
    limit: int = Query(200, ge=1, le=1000),
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await training_job_dataset_samples(db, current_user, job_id=job_id, limit=limit)


@router.post("/training/ecommerce-dataset/materialize")
async def post_ecommerce_dataset_materialize(
    body: EcommerceDatasetMaterializeRequest | None = None,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    request = body or EcommerceDatasetMaterializeRequest()
    result = await materialize_ecommerce_learning_flow_dataset(
        db,
        current_user,
        namespace=request.namespace,
        limit=request.limit,
    )
    await db.commit()
    await _invalidate_learning_related()
    return result


@router.post("/events/backfill")
async def post_learning_backfill(
    body: LearningBackfillRequest,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await backfill_learning_events(
        db,
        current_user,
        days=body.days,
        limit=body.limit,
        materialize=body.materialize,
    )
    await _invalidate_learning_related()
    return result


@router.post("/training/full-history")
async def post_full_history_training_candidate(
    body: FullHistoryTrainingRequest | None = None,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    request = body or FullHistoryTrainingRequest()
    result = await ensure_full_history_platform_training_candidate(
        db,
        current_user,
        min_sample_count=request.min_sample_count,
    )
    await db.commit()
    await _invalidate_learning_related()
    return result


@router.post("/training/system-full")
async def post_system_full_training_flow(
    body: SystemFullTrainingRequest | None = None,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    request = body or SystemFullTrainingRequest()
    result = await run_system_full_training_flow(
        db,
        current_user,
        days=request.days,
        per_source_limit=request.per_source_limit,
        min_sample_count=request.min_sample_count,
        advance=request.advance,
        max_jobs=request.max_jobs,
        capture_sources=request.capture_sources,
    )
    await db.commit()
    await _invalidate_learning_related()
    return result


@router.post("/artifacts/{artifact_id}/materialize")
async def post_materialize_artifact(
    artifact_id: str,
    body: ArtifactMaterializeRequest | None = None,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    request = body or ArtifactMaterializeRequest()
    result = await materialize_artifact(db, current_user, artifact_id, force=request.force)
    await _invalidate_learning_related()
    return result


@router.post("/artifacts/{artifact_id}/ignore")
async def post_ignore_artifact(
    artifact_id: str,
    body: ArtifactIgnoreRequest | None = None,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await ignore_artifact(db, current_user, artifact_id, reason=(body.reason if body else None))
    await _invalidate_learning_related()
    return result


@router.post("/candidates/{candidate_id}/accept")
async def post_accept_candidate(
    candidate_id: str,
    body: CandidateStatusRequest | None = None,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await update_candidate_status(db, current_user, candidate_id, status="accepted", reason=(body.reason if body else None))
    await _invalidate_learning_related()
    return result


@router.post("/candidates/{candidate_id}/reject")
async def post_reject_candidate(
    candidate_id: str,
    body: CandidateStatusRequest | None = None,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await update_candidate_status(db, current_user, candidate_id, status="rejected", reason=(body.reason if body else None))
    await _invalidate_learning_related()
    return result


@router.post("/candidates/{candidate_id}/create-review")
async def post_candidate_review(
    candidate_id: str,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await create_review_todo_for_candidate(db, current_user, candidate_id)
    await _invalidate_learning_related()
    return result


@router.post("/candidates/{candidate_id}/create-agent-draft")
async def post_candidate_agent_draft(
    candidate_id: str,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await create_agent_draft_from_candidate(db, current_user, candidate_id, auto=False)
    await _invalidate_learning_related()
    return result


@router.post("/candidates/{candidate_id}/create-training-job")
async def post_candidate_training_job(
    candidate_id: str,
    body: CandidateTrainingRequest | None = None,
    current_user: User = Depends(require_learning_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await create_training_job_from_candidate(
        db,
        current_user,
        candidate_id,
        payload=body.model_dump(exclude_none=True) if body else {},
    )
    await _invalidate_learning_related()
    return result
