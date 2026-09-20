"""AI 待办 API。"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.audit import audit
from app.common.cache import cached, invalidate_generated_data_cache, invalidate_page_cache
from app.common.time_utils import parse_bjt_datetime
from app.config import settings
from app.database import get_db

from .service import todo_service
from .todo_spec import build_dispatch_task, build_dispatch_todo, build_review_todo

router = APIRouter()

_CACHE_KEY_EXCLUDES = frozenset({"db", "session", "request", "background_tasks", "current_user"})


def _todo_id_cache_key(*args, **kwargs) -> str:
    todo_id = kwargs.get("todo_id")
    if todo_id is None and args:
        todo_id = args[0]
    return str(todo_id)


def _todo_detail_cache_key(todo_id: int, include_debug: bool = False, **_kwargs) -> str:
    return f"{todo_id}:debug={int(bool(include_debug))}"


def _skip_todo_detail_cache(*_args, include_debug: bool = False, **_kwargs) -> bool:
    return bool(include_debug)


def _related_timeline_cache_key(*args, **kwargs) -> str:
    return f"{kwargs.get('todo_id')}:{kwargs.get('limit', 10)}"


def _bulk_summary_cache_key(*args, **kwargs) -> str:
    body = kwargs.get("body")
    ids = sorted(set(getattr(body, "ids", []) or []))
    raw = json.dumps(ids, separators=(",", ":"))
    return hashlib.md5(raw.encode()).hexdigest()[:16]


async def _invalidate_generated_cache() -> None:
    await invalidate_generated_data_cache()
    await invalidate_page_cache("learning")


class DecideTodoRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    reason: str = Field("", max_length=2000)


class ExtendSLARequest(BaseModel):
    hours: int = Field(..., ge=1, le=168)


class AckDispatchRequest(BaseModel):
    note: str = Field("", max_length=1000)


class AssignDispatchRequest(BaseModel):
    executor_id: str = Field(..., min_length=1, max_length=50)


class UpdateDispatchTaskStatusRequest(BaseModel):
    status: str = Field(..., pattern="^(in_progress|blocked)$")
    note: str = Field("", max_length=2000)


class DraftDispatchTaskUpdate(BaseModel):
    id: int
    executor: str | None = Field(None, max_length=50)
    content: str | None = Field(None, max_length=2000)
    deadline: str | None = None


class UpdateTodoDraftRequest(BaseModel):
    title: str | None = Field(None, max_length=200)
    summary: str | None = Field(None, max_length=500)
    recommended_decision: str | None = Field(None, max_length=1000)
    approval_question: str | None = Field(None, max_length=500)
    suggested_actions: list[str] | None = None
    forbidden_actions: list[str] | None = None
    dispatch_tasks: list[DraftDispatchTaskUpdate] | None = None


class SourceCheckRequest(BaseModel):
    source_key: str | None = Field(None, max_length=100)
    index: int | None = Field(None, ge=0, le=50)


class BatchDecideRequest(BaseModel):
    todo_ids: list[int] = Field(..., min_length=1, max_length=50)
    decision: str = Field(..., pattern="^(approved|rejected)$")
    reason: str = Field("", max_length=2000)


class BulkSummaryRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=50)


class BackfillPostTrainingEvaluationsRequest(BaseModel):
    skill_ids: list[str] | None = Field(None, max_length=2)
    request_ids: list[str] | None = Field(None, max_length=50)
    limit: int = Field(5, ge=1, le=50)
    since_days: int = Field(30, ge=1, le=365)
    dry_run: bool = True
    force: bool = False


class BatchExtendSLARequest(BaseModel):
    todo_ids: list[int] = Field(..., min_length=1, max_length=50)
    hours: int = Field(..., ge=1, le=168)


class BatchReassignRequest(BaseModel):
    todo_ids: list[int] = Field(..., min_length=1, max_length=50)
    to_user_id: str = Field(..., min_length=1, max_length=50)
    reason: str = Field("", max_length=500)


class BatchAckDispatchRequest(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, max_length=50)
    note: str = Field("", max_length=1000)


class BuildDispatchTaskRequest(BaseModel):
    executor: str | None = None
    content: str = Field(..., min_length=1, max_length=2000)
    deadline: str | None = None
    extra: dict | None = None


class BuildDispatchTodoRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    summary: str | None = Field(None, max_length=500)
    payload: dict | None = None
    decision_mode: str = Field("any_of", pattern="^(any_of|all_of|independent)$")
    sla_hours: int | None = Field(None, ge=1, le=168)
    reviewers: list[str] = Field(default_factory=list)
    reviewer_role: str | None = None
    tasks: list[BuildDispatchTaskRequest] = Field(..., min_length=1)
    callback: dict | None = None


class BuildReviewTodoRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    summary: str | None = Field(None, max_length=500)
    payload: dict | None = None
    decision_mode: str = Field("any_of", pattern="^(any_of|all_of|independent)$")
    sla_hours: int | None = Field(None, ge=1, le=168)
    reviewers: list[str] = Field(default_factory=list)
    reviewer_role: str | None = None
    callback: dict | None = None


@router.post("/spec/build-dispatch")
async def build_dispatch_spec(
    body: BuildDispatchTodoRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    tasks = [
        build_dispatch_task(
            task.content,
            executor=task.executor,
            deadline=task.deadline,
            extra=task.extra,
        )
        for task in body.tasks
    ]
    return {
        "todo": build_dispatch_todo(
            body.title,
            tasks=tasks,
            summary=body.summary,
            payload=body.payload,
            decision_mode=body.decision_mode,
            sla_hours=body.sla_hours,
            reviewers=body.reviewers,
            reviewer_role=body.reviewer_role,
            callback=body.callback,
        )
    }


@router.post("/spec/build-review")
async def build_review_spec(
    body: BuildReviewTodoRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    return {
        "todo": build_review_todo(
            body.title,
            summary=body.summary,
            payload=body.payload,
            decision_mode=body.decision_mode,
            sla_hours=body.sla_hours,
            reviewers=body.reviewers,
            reviewer_role=body.reviewer_role,
            callback=body.callback,
        )
    }


@router.get("/")
@cached(
    "todos:list",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def list_todos(
    status: str | None = Query(None),
    kind: str | None = Query(None, pattern="^(review|dispatch)$"),
    skill_id: str | None = Query(None),
    decision_log_id: int | None = Query(None, ge=1),
    run_id: str | None = Query(None, max_length=50),
    priority: str | None = Query(None, pattern="^P[0-3]$"),
    assignee: str | None = Query(None, max_length=50),
    sla_state: str | None = Query(None, pattern="^(due_soon|due_24h|overdue)$"),
    q: str | None = Query(None, max_length=100),
    search: str | None = Query(None, max_length=100),
    sort_by: str = Query("created_at", pattern="^(created_at|sla_at|ranking_visitors|ranking_orders|decline_coef)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await todo_service.list_todos(
        db,
        current_user=current_user,
        status=status,
        kind=kind,
        skill_id=skill_id,
        decision_log_id=decision_log_id,
        run_id=run_id,
        priority=priority,
        assignee=assignee,
        sla_state=sla_state,
        search=q or search,
        sort_by=sort_by,
        sort_order=sort_order,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@router.get("/stats")
@cached(
    "todos:stats",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_todo_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await todo_service.get_stats(db, current_user)


# GAP-10：趋势（按日分组）—— 必须在 /{todo_id} 之前注册，否则 "trends" 会被当成 todo_id 解析
@router.get("/trends")
@cached(
    "todos:trends",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_todo_trends(
    days: int = Query(7, ge=1, le=90),
    # 兼容旧前端参数；服务端业务日期固定按北京时间切日。
    tz_offset_minutes: int = Query(480, ge=-720, le=720),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    from app.todos.trends_service import get_trends

    return await get_trends(
        db,
        current_user=current_user,
        days=days,
        tz_offset_minutes=tz_offset_minutes,
    )


# Top-N source skills for Inbox sidebar — 必须在 /{todo_id} 之前注册
@router.get("/skills/top")
@cached(
    "todos:skills:top",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_top_source_skills(
    limit: int = Query(6, ge=1, le=20),
    status: str = Query("pending", pattern="^(pending|all)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """按用户可见 todo 的 skill_id 聚合返回 top-N，附 skill 名称。

    ACL 与 service.list_todos 对齐：
    - 非 global_inbox_reader：限定当前用户同一钉钉人的 assignee 账号组，并按部门匹配可见 skill
    - global_inbox_reader：全部 todo 可见
    用于 InboxCenter 左侧 sidebar 的「来源 Skill」分组替换 stub 数据。
    """
    from sqlalchemy import func, or_, select
    from app.todos.models import AITodo, DecisionRequest
    from app.skills.core.models import Skill
    from app.todos._acl import is_global_inbox_reader
    from app.todos.assignee_scope import visible_inbox_assignee_scope_for_user

    filters = [
        DecisionRequest.archived_at.is_(None),
        DecisionRequest.skill_id.isnot(None),
    ]
    if status == "pending":
        filters.append(AITodo.status == "pending")
    if not is_global_inbox_reader(current_user):
        visible_assignee_ids, identity_has_global_scope = (
            await visible_inbox_assignee_scope_for_user(db, current_user)
        )
        filters.append(AITodo.assignee.in_(visible_assignee_ids))
        if not identity_has_global_scope:
            filters.append(or_(Skill.id.is_(None), Skill.department == current_user.department))

    stmt = (
        select(DecisionRequest.skill_id, func.count(AITodo.id).label("cnt"))
        .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
        .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
        .where(*filters)
        .group_by(DecisionRequest.skill_id)
        .order_by(func.count(AITodo.id).desc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    skill_ids = [r[0] for r in rows if r[0]]
    name_map: dict[str, str] = {}
    if skill_ids:
        name_rows = await db.execute(
            select(Skill.id, Skill.name).where(Skill.id.in_(skill_ids))
        )
        name_map = {sid: name for sid, name in name_rows.all()}

    return {
        "items": [
            {"id": sid, "name": name_map.get(sid) or sid, "count": int(cnt)}
            for sid, cnt in rows
        ]
    }


@router.post("/post-training-evaluations/backfill")
async def backfill_post_training_todo_evaluations(
    body: BackfillPostTrainingEvaluationsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "system_admin", "ai_engineer")),
):
    from datetime import timedelta

    from app.common.time_utils import now_bjt
    from app.todos.post_training_evaluation import (
        POST_TRAINING_EVALUATED_SKILL_IDS,
        backfill_post_training_evaluations,
    )

    requested_skill_ids = set(body.skill_ids or POST_TRAINING_EVALUATED_SKILL_IDS)
    unsupported = requested_skill_ids - set(POST_TRAINING_EVALUATED_SKILL_IDS)
    if unsupported:
        return {
            "ok": False,
            "code": "UNSUPPORTED_SKILL",
            "unsupported_skill_ids": sorted(unsupported),
            "supported_skill_ids": sorted(POST_TRAINING_EVALUATED_SKILL_IDS),
        }
    result = await backfill_post_training_evaluations(
        db,
        skill_ids=requested_skill_ids,
        limit=body.limit,
        since=now_bjt() - timedelta(days=body.since_days),
        dry_run=body.dry_run,
        force=body.force,
        request_ids=body.request_ids,
    )
    if body.dry_run:
        await db.rollback()
    else:
        await db.commit()
        await _invalidate_generated_cache()
        await audit.log(
            current_user.id,
            "todo.post_training_evaluation_backfill",
            "todo",
            "post_training_model_evaluation",
            detail={
                "skill_ids": sorted(requested_skill_ids),
                "limit": body.limit,
                "since_days": body.since_days,
                "force": body.force,
                "request_ids": body.request_ids or [],
                "updated": result.get("updated"),
                "checked": result.get("checked"),
            },
        )
    return {"ok": True, **result, "dry_run": body.dry_run}


@router.get("/{todo_id}")
@cached(
    "todos:detail",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_builder=_todo_detail_cache_key,
    key_excludes=_CACHE_KEY_EXCLUDES,
    skip_cache_if=_skip_todo_detail_cache,
)
async def get_todo_detail(
    todo_id: int,
    include_debug: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await todo_service.get_todo_detail(
        db,
        todo_id=todo_id,
        current_user=current_user,
        include_debug=include_debug,
    )


# GAP-3：/todos/{todo_id}/preview — 裁过的轻量版详情
@router.get("/{todo_id}/preview")
@cached(
    "todos:preview",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_builder=_todo_id_cache_key,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_todo_preview(
    todo_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await todo_service.get_todo_preview(
        db,
        todo_id=todo_id,
        current_user=current_user,
    )


@router.patch("/{todo_id}/draft")
async def update_todo_draft(
    todo_id: int,
    body: UpdateTodoDraftRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    result = await todo_service.update_todo_draft(
        db,
        todo_id=todo_id,
        actor=current_user,
        title=body.title,
        summary=body.summary,
        recommended_decision=body.recommended_decision,
        approval_question=body.approval_question,
        suggested_actions=body.suggested_actions,
        forbidden_actions=body.forbidden_actions,
        dispatch_tasks=[task.model_dump() for task in body.dispatch_tasks] if body.dispatch_tasks is not None else None,
    )
    await _invalidate_generated_cache()
    await audit.log(
        current_user.id,
        "todo.draft_update",
        "todo",
        str(todo_id),
        detail={"request_id": result.get("request", {}).get("id")},
    )
    return result


@router.post("/{todo_id}/source-check")
async def check_todo_source(
    todo_id: int,
    body: SourceCheckRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await todo_service.check_data_source(
        db,
        todo_id=todo_id,
        current_user=current_user,
        source_key=body.source_key,
        index=body.index,
    )


# GAP-4：/todos/bulk-summary — 一次批量摘要，非可见 id 静默过滤
@router.post("/bulk-summary")
@cached(
    "todos:bulk_summary",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_builder=_bulk_summary_cache_key,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def bulk_todo_summary(
    body: BulkSummaryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await todo_service.bulk_summary(
        db,
        todo_ids=body.ids,
        current_user=current_user,
    )


@router.post("/{todo_id}/decide")
async def decide_todo(
    todo_id: int,
    body: DecideTodoRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    result = await todo_service.decide(
        db,
        todo_id=todo_id,
        decision=body.decision,
        decided_by=current_user.id,
        channel="web",
        reason=body.reason,
    )
    await _invalidate_generated_cache()
    return result


@router.post("/{todo_id}/extend-sla")
async def extend_sla(
    todo_id: int,
    body: ExtendSLARequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """单条 SLA 延期。C2：权限在 service 层 ``assert_can_modify_todo`` 闸门，
    支持 dept_admin 在自己部门内延期，不再卡死在 ``require_role("admin")``。"""
    result = await todo_service.extend_sla(
        db, todo_id=todo_id, hours=body.hours, actor=current_user
    )
    await _invalidate_generated_cache()
    return result


# ──────────────────────────────────────────────
# 派发子任务（kind=dispatch 的待办在审批通过后 fan-out 到这里）
# ──────────────────────────────────────────────


@router.get("/dispatch/mine")
@cached(
    "todos:dispatch:mine",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def list_my_dispatch_tasks(
    status: str | None = Query(None, pattern="^(sent|pushed_no_dingtalk|in_progress|blocked|done|cancelled)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await todo_service.list_my_dispatch_tasks(
        db,
        executor_id=current_user.id,
        executor_user=current_user,
        status=status,
        page=page,
        page_size=page_size,
    )


@router.get("/dispatch/assignable-users")
async def list_dispatch_assignable_users(
    request_id: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await todo_service.list_dispatch_assignable_users(
        db,
        current_user=current_user,
        request_id=request_id,
    )


@router.post("/dispatch/{task_id}/assign")
async def assign_dispatch_task(
    task_id: int,
    body: AssignDispatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    result = await todo_service.assign_dispatch_task(
        db,
        task_id=task_id,
        executor_id=body.executor_id,
        actor_id=current_user.id,
    )
    await _invalidate_generated_cache()
    await audit.log(
        current_user.id,
        "dispatch.assign",
        "dispatch_task",
        str(task_id),
        detail={"executor_id": body.executor_id},
    )
    return result


@router.post("/dispatch/{task_id}/status")
async def update_dispatch_task_status(
    task_id: int,
    body: UpdateDispatchTaskStatusRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    result = await todo_service.update_dispatch_task_status(
        db,
        task_id=task_id,
        actor_id=current_user.id,
        status=body.status,
        note=body.note,
    )
    await _invalidate_generated_cache()
    await audit.log(
        current_user.id,
        f"dispatch.{body.status}",
        "dispatch_task",
        str(task_id),
        detail={"channel": "web"},
    )
    return result


@router.post("/dispatch/{task_id}/ack")
async def ack_dispatch_task(
    task_id: int,
    body: AckDispatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    result = await todo_service.ack_dispatch_task(
        db,
        task_id=task_id,
        actor_id=current_user.id,
        channel="web",
        note=body.note,
    )
    await _invalidate_generated_cache()
    await audit.log(
        current_user.id,
        "dispatch.ack",
        "dispatch_task",
        str(task_id),
        detail={"channel": "web"},
    )
    return result


@router.post("/batch-decide")
async def batch_decide_todos(
    body: BatchDecideRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """批量审批：一次审批多个 Todo（approve/reject）"""
    results = []
    for todo_id in body.todo_ids:
        try:
            r = await todo_service.decide(
                db,
                todo_id=todo_id,
                decision=body.decision,
                decided_by=current_user.id,
                channel="web",
                reason=body.reason,
            )
            results.append({"todo_id": todo_id, "ok": True, "status": r.get("status")})
        except Exception as e:
            results.append({"todo_id": todo_id, "ok": False, "error": str(e)[:200]})

    succeeded = sum(1 for r in results if r["ok"])
    await _invalidate_generated_cache()
    await audit.log(
        current_user.id,
        "todo.batch_decide",
        detail={"decision": body.decision, "total": len(body.todo_ids), "succeeded": succeeded},
    )
    return {"total": len(results), "succeeded": succeeded, "results": results}


# GAP-7：批量延期
@router.post("/batch-extend-sla")
async def batch_extend_sla(
    body: BatchExtendSLARequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """C2：service 层 ``assert_can_modify_todo`` 逐条校验权限；router 仅
    require_state_active，复用同一接口给 admin / dept_admin / 漏掉的角色按预期返回 403。
    H9：service 层每条 SAVEPOINT；外层事务 commit 由 ``get_db`` dependency 兜底。
    """
    result = await todo_service.batch_extend_sla(
        db,
        todo_ids=body.todo_ids,
        hours=body.hours,
        actor=current_user,
    )
    await _invalidate_generated_cache()
    await audit.log(
        current_user.id,
        "todo.batch_extend_sla",
        detail={
            "hours": body.hours,
            "total": result["total"],
            "succeeded": result["succeeded"],
        },
    )
    return result


# GAP-8：批量转派
@router.post("/batch-reassign")
async def batch_reassign_todos(
    body: BatchReassignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    result = await todo_service.batch_reassign(
        db,
        todo_ids=body.todo_ids,
        to_user_id=body.to_user_id,
        actor=current_user,
        reason=body.reason,
    )
    await _invalidate_generated_cache()
    await audit.log(
        current_user.id,
        "todo.batch_reassign",
        detail={
            "to_user_id": body.to_user_id,
            "total": result["total"],
            "succeeded": result["succeeded"],
        },
    )
    return result


# GAP-11：同 Skill 近 N 条历史决策（approved/rejected）
@router.get("/{todo_id}/related-timeline")
@cached(
    "todos:related_timeline",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_builder=_related_timeline_cache_key,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_related_timeline(
    todo_id: int,
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await todo_service.get_related_timeline(
        db,
        todo_id=todo_id,
        current_user=current_user,
        limit=limit,
    )


# GAP-12：派发日历
@router.get("/dispatch/calendar")
@cached(
    "todos:dispatch:calendar",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_dispatch_calendar(
    date_from: str = Query(...),
    date_to: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    try:
        df = parse_bjt_datetime(date_from)
        dt = parse_bjt_datetime(date_to)
        if "T" not in date_to and " " not in date_to:
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    except ValueError:
        from app.common.exceptions import AppError

        raise AppError("PARAM_INVALID", 400, {"detail": "date_from / date_to 需为 ISO 日期"})
    return await todo_service.get_dispatch_calendar(
        db,
        current_user=current_user,
        date_from=df,
        date_to=dt,
    )


# GAP-9：批量派发确认
@router.post("/dispatch/batch-ack")
async def batch_ack_dispatch(
    body: BatchAckDispatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    result = await todo_service.batch_ack_dispatch(
        db,
        task_ids=body.task_ids,
        actor_id=current_user.id,
        note=body.note,
    )
    await _invalidate_generated_cache()
    await audit.log(
        current_user.id,
        "dispatch.batch_ack",
        detail={"total": result["total"], "succeeded": result["succeeded"]},
    )
    return result
