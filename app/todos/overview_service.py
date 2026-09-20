"""GAP-2：`GET /api/inbox/overview`。

汇总审批人的待办盘面：pending / overdue / resolved_today / SLA 达成率 /
平均处理时长 / 积压分桶 / 近 7 天聚合。用于 InboxHeader KPI 卡取代前端拼凑。

实现原则：
- 一次 SELECT 拿近 30 天的 (status, created_at, decided_at, sla_at) 四元组；
- Python 端做聚合，比分多条 SQL 简单且便于边界调试；
- 结果由 router 层包一层 6 小时生成数据缓存（@cached，scope_by=current_user.id），写入/状态变更时主动失效。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.skills.core.models import Skill
from app.todos._acl import is_global_inbox_reader
from app.todos.assignee_scope import visible_inbox_assignee_scope_for_user
from app.todos.models import AITodo, DecisionRequest
from app.common.time_utils import now_bjt

# 积压桶（以 now - created_at 分钟数为键），边界左闭右开：
# "<1h": [0, 60) / "1-6h": [60, 360) / "6-24h": [360, 1440) / ">24h": [1440, inf)
_BACKLOG_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("<1h", 0, 60),
    ("1-6h", 60, 360),
    ("6-24h", 360, 1440),
    (">24h", 1440, None),
)


def _bucket_for_age(minutes: float) -> str:
    for label, low, high in _BACKLOG_BUCKETS:
        if high is None:
            if minutes >= low:
                return label
        elif low <= minutes < high:
            return label
    return ">24h"


async def get_overview(
    db: AsyncSession,
    *,
    current_user: User,
    now: datetime | None = None,
) -> dict[str, Any]:
    """生成收件中心概览。now 注入便于测试确定边界。

    访问范围对齐 list_todos：全局角色看全公司；普通用户只看派给自己的本部门待办。
    """
    now = now or now_bjt()
    today_start = datetime(now.year, now.month, now.day)
    window_start = now - timedelta(days=30)

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
            AITodo.status,
            AITodo.created_at,
            AITodo.decided_at,
            DecisionRequest.sla_at,
        )
        .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
        .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
        .where(*filters)
    )
    rows = (await db.execute(stmt)).all()

    pending = 0
    overdue = 0
    resolved_today = 0
    resolved_last_7d = 0
    approved_last_7d = 0
    rejected_last_7d = 0
    sla_success = 0
    sla_total = 0  # approved + rejected + expired 三种终态计 SLA 分母
    resolve_durations_minutes: list[float] = []
    backlog_counts: dict[str, int] = {label: 0 for label, _, _ in _BACKLOG_BUCKETS}

    week_start = now - timedelta(days=7)

    for status, created_at, decided_at, sla_at in rows:
        if status == "pending":
            pending += 1
            if sla_at and sla_at < now:
                overdue += 1
            age_minutes = (now - created_at).total_seconds() / 60.0 if created_at else 0
            backlog_counts[_bucket_for_age(age_minutes)] += 1
        else:
            # 终态：纳入 SLA 统计
            if status in ("approved", "rejected", "expired"):
                sla_total += 1
                if status != "expired" and sla_at and decided_at and decided_at <= sla_at:
                    sla_success += 1
            if decided_at and decided_at >= today_start:
                resolved_today += 1
            if decided_at and decided_at >= week_start:
                resolved_last_7d += 1
                if status == "approved":
                    approved_last_7d += 1
                elif status == "rejected":
                    rejected_last_7d += 1
            if decided_at and created_at:
                resolve_durations_minutes.append(
                    (decided_at - created_at).total_seconds() / 60.0
                )

    avg_resolve_minutes = (
        round(sum(resolve_durations_minutes) / len(resolve_durations_minutes), 2)
        if resolve_durations_minutes
        else 0.0
    )
    sla_hit_rate = round(sla_success / sla_total, 4) if sla_total > 0 else 1.0

    return {
        "pending": pending,
        "overdue": overdue,
        "resolved_today": resolved_today,
        "sla_hit_rate": sla_hit_rate,
        "avg_resolve_minutes": avg_resolve_minutes,
        "backlog_by_age": [
            {"bucket": label, "count": backlog_counts[label]}
            for label, _, _ in _BACKLOG_BUCKETS
        ],
        "resolved_last_7d": resolved_last_7d,
        "approved_last_7d": approved_last_7d,
        "rejected_last_7d": rejected_last_7d,
    }


__all__ = ["get_overview"]
