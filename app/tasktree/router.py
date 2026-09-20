"""任务树 API 路由。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel
from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.audit import audit
from app.common.exceptions import AppError
from app.database import get_db
from app.skills.core.access import list_user_member_departments
from app.skills.core.models import Skill
from app.tasktree.metrics import ETAG_HIT, TREE_DURATION
from app.tasktree.schemas import (
    FailureDiagnosisResponse,
    NodeDetailResponse,
    NodeSchedulesResponse,
    RunChainResponse,
    SkillValueResponse,
    TaskTreeDashboardResponse,
    TaskTreeResponse,
    TaskTreeStatsResponse,
)
from app.tasktree.ai_diagnose import diagnose_failure
from app.tasktree.service import (
    ADMIN_ROLES,
    VIRTUAL_AI_ID,
    VIRTUAL_ORPHAN_ID,
    VIRTUAL_UNASSIGNED_ID,
    _load_org_indexes,
    _normalize_requested_department,
    _resolve_user_lv1,
    is_global_viewer,
    projection,
)
from app.tasktree.value_service import estimate_value

router = APIRouter()


# ── ABAC helpers ─────────────────────────────────────────────────
_ADMIN_ROLES = ADMIN_ROLES  # 保留别名以兼容模块外旧引用
_is_admin = is_global_viewer


async def _user_member_departments(db: AsyncSession, user: User) -> set[str]:
    """返回用户通过 skill_members 间接持有的部门集合（跨部门代管）。

    C5a：业务 owner 加入他部门 SkillMember 后，应能访问该部门 task-tree。
    本 helper 给 _enforce_department_abac 用，把 skill_members 关联部门归到 Lv1 名集合。
    """
    return await list_user_member_departments(db, user.id)


async def _enforce_department_abac(
    *,
    department: str | None,
    current_user: User,
    db: AsyncSession,
    request: Request | None = None,
    action: str,
) -> None:
    """P0-6：部门参数 ABAC 收口。

    - admin / ai_engineer / director / can_view_all → 允许跨部门，但必须审计
    - 其他用户：仅允许访问自己部门 + skill_members 关联部门
    - 不接受前端传入"假部门"绕过（require_department_access 二次校验）

    C5a / C5b 修复：
    - 业务 owner 加入他部门 SkillMember 时，可访问该部门 task-tree（恢复 _user_member_departments 通道）
    - AI 部门用户 user_lv1 == VIRTUAL_AI_ID → 允许访问 AI 虚拟组，未分配虚拟组同理
    """
    if not department:
        return

    if _is_admin(current_user):
        # admin 跨部门查询必须留审计痕迹（违规回溯依据）
        ip = request.client.host if request is not None and request.client else None
        try:
            await audit.log(
                current_user.id,
                action,
                target_type="tasktree",
                target_id=department,
                detail={"role": current_user.role, "cross_department": True},
                ip_address=ip,
            )
        except Exception:
            # 审计失败不阻断业务（已在 audit 内部有兜底）
            pass
        return

    lv1_by_id, _lv1_by_name, lv1_names, org_by_name = await _load_org_indexes(db)
    requested_department = _normalize_requested_department(
        department,
        lv1_by_id=lv1_by_id,
        lv1_names=lv1_names,
        org_by_name=org_by_name,
    )
    user_lv1 = _resolve_user_lv1(
        current_user.department,
        lv1_by_id=lv1_by_id,
        lv1_names=lv1_names,
        org_by_name=org_by_name,
    )

    # C5b：虚拟组只有"自己归属"该虚拟组的非 admin 才允许进入
    if requested_department in {VIRTUAL_AI_ID, VIRTUAL_UNASSIGNED_ID, VIRTUAL_ORPHAN_ID}:
        if user_lv1 == requested_department:
            return
        raise AppError("AUTH_DEPARTMENT_DENIED", 403)

    if user_lv1 is None:
        raise AppError("AUTH_DEPARTMENT_DENIED", 403)
    if requested_department == user_lv1:
        return

    # C5a：跨部门 SkillMember 通道——业务 owner 被授权代管他部门 Skill 时放行
    member_depts_raw = await _user_member_departments(db, current_user)
    if not member_depts_raw:
        raise AppError("AUTH_DEPARTMENT_DENIED", 403)
    # 把代管部门归并到 Lv1 名集合（与 user_lv1 同维度）
    member_lv1_names: set[str] = set()
    for dept in member_depts_raw:
        normalized = _normalize_requested_department(
            dept,
            lv1_by_id=lv1_by_id,
            lv1_names=lv1_names,
            org_by_name=org_by_name,
        )
        if normalized:
            member_lv1_names.add(normalized)
    if requested_department not in member_lv1_names:
        raise AppError("AUTH_DEPARTMENT_DENIED", 403)


@router.get("/task-tree", response_model=TaskTreeResponse)
async def get_task_tree(
    response: Response,
    request: Request,
    department: str | None = None,
    status: str | None = Query(None, pattern="^(online|maybe_offline|offline|all)$"),
    if_none_match: str | None = Header(None),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    started = time.perf_counter()
    try:
        is_admin = _is_admin(current_user)
        await _enforce_department_abac(
            department=department,
            current_user=current_user,
            db=db,
            request=request,
            action="tasktree.tree.read",
        )
        tree = await projection.get_tree(
            db,
            department=department,
            user_department=current_user.department,
            current_user=current_user,
            is_admin=is_admin,
            status_filter=status,
        )
        if if_none_match and if_none_match.strip('"') == tree.etag:
            ETAG_HIT.inc()
            return Response(status_code=304, headers={"ETag": tree.etag})

        if response is not None:
            response.headers["ETag"] = tree.etag
        return tree
    finally:
        TREE_DURATION.labels(endpoint="tree").observe(time.perf_counter() - started)


@router.get("/task-tree/stats", response_model=TaskTreeStatsResponse)
async def get_stats(
    request: Request,
    department: str | None = None,
    window: str = Query("24h", pattern="^(1h|24h|7d)$"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    started = time.perf_counter()
    try:
        is_admin = _is_admin(current_user)
        await _enforce_department_abac(
            department=department,
            current_user=current_user,
            db=db,
            request=request,
            action="tasktree.stats.read",
        )
        return await projection.get_stats(
            db,
            department=department,
            window=window,
            user_department=current_user.department,
            current_user=current_user,
            is_admin=is_admin,
        )
    finally:
        TREE_DURATION.labels(endpoint="stats").observe(time.perf_counter() - started)


@router.get("/task-tree/dashboard", response_model=TaskTreeDashboardResponse)
async def get_dashboard(
    request: Request,
    department: str | None = None,
    period_days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    started = time.perf_counter()
    try:
        is_admin = _is_admin(current_user)
        await _enforce_department_abac(
            department=department,
            current_user=current_user,
            db=db,
            request=request,
            action="tasktree.dashboard.read",
        )
        return await projection.get_dashboard(
            db,
            department=department,
            period_days=period_days,
            user_department=current_user.department,
            current_user=current_user,
            is_admin=is_admin,
        )
    finally:
        TREE_DURATION.labels(endpoint="dashboard").observe(time.perf_counter() - started)


@router.get("/task-tree/node/{instance_id}", response_model=NodeDetailResponse)
async def get_node_detail(
    instance_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    started = time.perf_counter()
    try:
        return await projection.get_node_detail(db, instance_id, current_user=current_user)
    finally:
        TREE_DURATION.labels(endpoint="node").observe(time.perf_counter() - started)


@router.get("/task-tree/node/{instance_id}/schedules", response_model=NodeSchedulesResponse)
async def get_node_schedules(
    instance_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取节点上的定时任务列表及执行统计。"""
    from app.tasktree.schedule_query import get_instance_schedules
    return await get_instance_schedules(db, instance_id)


class UpdateScheduleRequest(BaseModel):
    action: str  # "start" / "stop" / "update"
    cron_expression: str | None = None


@router.put("/task-tree/skill/{skill_id}/schedule")
async def update_skill_schedule(
    skill_id: str,
    body: UpdateScheduleRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """控制 Skill 定时任务：启动 / 停止 / 修改 cron 表达式。
    同步更新 Skill 记录 + APScheduler 任务。
    """
    from app.tasktree.schedule_query import update_skill_schedule
    result = await update_skill_schedule(db, skill_id, body.action, body.cron_expression, current_user.id)
    await audit.log(current_user.id, "skill.schedule", "skill", skill_id, {
        "action": body.action, "cron": body.cron_expression,
    })
    return result


@router.get("/task-tree/run/{run_id}/chain", response_model=RunChainResponse)
async def get_run_chain(
    run_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    started = time.perf_counter()
    try:
        return await projection.get_run_chain(db, run_id, current_user=current_user)
    finally:
        TREE_DURATION.labels(endpoint="chain").observe(time.perf_counter() - started)


@router.get("/task-tree/run/{run_id}/diagnose", response_model=FailureDiagnosisResponse)
async def get_run_diagnosis(
    run_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    started = time.perf_counter()
    try:
        await projection.get_run_chain(db, run_id, current_user=current_user)
        diag = await diagnose_failure(
            db,
            run_id,
            user_id=current_user.id,
            department=current_user.department,
        )
        # 兼容旧接口：允许 diagnose_failure 返回 str 或 dict
        if isinstance(diag, dict):
            return FailureDiagnosisResponse(
                run_id=run_id,
                diagnosis=str(diag.get("text") or ""),
                ai_available=bool(diag.get("ai_available", True)),
                source=str(diag.get("source") or "llm"),
            )
        return FailureDiagnosisResponse(run_id=run_id, diagnosis=str(diag))
    finally:
        TREE_DURATION.labels(endpoint="diagnose").observe(time.perf_counter() - started)


@router.get("/task-tree/skill/{skill_id}/value", response_model=SkillValueResponse)
async def get_skill_value(
    skill_id: str,
    period_days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    started = time.perf_counter()
    try:
        skill_department = await db.scalar(select(Skill.department).where(Skill.id == skill_id))
        is_admin = _is_admin(current_user)
        if skill_department and not is_admin and skill_department != current_user.department:
            raise AppError("AUTH_DEPARTMENT_DENIED", 403)
        department = skill_department or current_user.department
        result = await estimate_value(
            db,
            skill_id,
            period_days=period_days,
            user_id=current_user.id,
            department=department,
        )
        return SkillValueResponse(
            skill_id=skill_id,
            period_days=period_days,
            saved_hours=result.get("saved_hours", 0.0),
            estimated_cost_saving=result.get("estimated_cost_saving", 0.0),
            risk_events_prevented=result.get("risk_events_prevented", 0),
            recommendation=result.get("recommendation", ""),
        )
    finally:
        TREE_DURATION.labels(endpoint="value").observe(time.perf_counter() - started)
