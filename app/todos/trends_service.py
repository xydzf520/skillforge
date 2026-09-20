"""GAP-10：`GET /api/todos/trends?days={7,30,90}` 待办趋势聚合。

GROUP BY DATE(ai_todos.created_at) 按日分桶，填补空日（count=0）使折线图不出现中断。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.skills.core.models import Skill
from app.todos._acl import is_global_inbox_reader
from app.todos.assignee_scope import visible_inbox_assignee_scope_for_user
from app.todos.models import AITodo, DecisionRequest
from app.common.time_utils import now_bjt

_SUPPORTED_DAYS: frozenset[int] = frozenset({7, 30, 90})


async def get_trends(
    db: AsyncSession,
    *,
    current_user: User,
    days: int = 7,
    now: datetime | None = None,
    tz_offset_minutes: int = 480,
) -> dict[str, Any]:
    """待办趋势聚合。

    业务日期固定按北京时间切日。`tz_offset_minutes` 仅为兼容旧前端参数保留，
    不再按浏览器所在时区重算。
    """
    if days not in _SUPPORTED_DAYS:
        days = 7
    now = now or now_bjt()
    _ = tz_offset_minutes
    window_start_date = (now - timedelta(days=days - 1)).date()
    window_start = datetime.combine(window_start_date, datetime.min.time())

    filters: list[Any] = [
        DecisionRequest.archived_at.is_(None),
        AITodo.created_at >= window_start,
    ]
    if not is_global_inbox_reader(current_user):
        visible_assignee_ids, identity_has_global_scope = (
            await visible_inbox_assignee_scope_for_user(db, current_user)
        )
        filters.append(AITodo.assignee.in_(visible_assignee_ids))
        if not identity_has_global_scope:
            filters.append(or_(Skill.id.is_(None), Skill.department == current_user.department))

    stmt = (
        select(
            func.date(AITodo.created_at).label("day"),
            AITodo.status,
            func.count(AITodo.id),
        )
        .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
        .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
        .where(*filters)
        .group_by(func.date(AITodo.created_at), AITodo.status)
        .order_by(func.date(AITodo.created_at))
    )

    raw: dict[date, dict[str, int]] = {}
    for day, status, count in (await db.execute(stmt)).all():
        bucket = raw.setdefault(day, {})
        bucket[status] = int(count)

    points: list[dict[str, Any]] = []
    cursor = window_start_date
    end_date = now.date()
    while cursor <= end_date:
        bucket = raw.get(cursor, {})
        total = sum(bucket.values())
        points.append(
            {
                "day": cursor.isoformat(),
                "pending": bucket.get("pending", 0),
                "approved": bucket.get("approved", 0),
                "rejected": bucket.get("rejected", 0),
                "expired": bucket.get("expired", 0),
                "resolved_by_peer": bucket.get("resolved_by_peer", 0),
                "total": total,
            }
        )
        cursor = cursor + timedelta(days=1)

    return {"days": days, "points": points}


__all__ = ["get_trends"]
