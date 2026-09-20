"""收件中心用户偏好 service（GAP-5/6）。

- get_unread_count：服务端精确计算 "上次查看之后新产生多少个有报告的 decision_log"
- mark_reports_read：upsert 用户的 reports_last_viewed_at 时间戳

写路径全部走 PostgreSQL `ON CONFLICT DO UPDATE`，避免 SELECT-then-INSERT 在多端
登录用户并发场景下撞 PK 报 IntegrityError。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.audit import audit
from app.common.time_utils import isoformat_bjt, now_bjt
from app.execution.models import DecisionLog

from .models import UserInboxPreference
from .service import get_visible_skill_ids_cached


async def _read_pref(
    db: AsyncSession,
    user_id: str,
) -> UserInboxPreference | None:
    """只读 helper：拿到用户偏好行，不存在返回 None（不创建）。"""
    return await db.get(UserInboxPreference, user_id)


async def get_unread_count(
    db: AsyncSession,
    *,
    current_user: User,
) -> dict:
    """返回 `{unread: int, last_viewed_at: ISO8601 | None}`。

    语义：
    - last_viewed_at 为空（从未点开过）时 unread = 全部可见报告数
    - 否则 unread = 自 last_viewed_at 起新建的可见 decision_log 条数（只计 reports 非空的）
    - 跨可见性：admin / can_view_all 看全部；普通用户限 visible_skill_ids
    """
    pref = await _read_pref(db, current_user.id)
    last_viewed_at: datetime | None = pref.reports_last_viewed_at if pref else None

    visible = await get_visible_skill_ids_cached(db, current_user)
    if visible == []:  # 明确无可见 Skill
        return {
            "unread": 0,
            "last_viewed_at": isoformat_bjt(last_viewed_at),
        }

    # 只计 output_result.reports 非空的 DecisionLog
    stmt = select(func.count(DecisionLog.id)).where(
        DecisionLog.is_sandbox.is_(False),
        func.jsonb_typeof(DecisionLog.output_result["reports"]) == "array",
        func.jsonb_array_length(DecisionLog.output_result["reports"]) > 0,
    )
    if visible is not None:  # None 表示全局可见
        stmt = stmt.where(DecisionLog.skill_id.in_(visible))
    if last_viewed_at is not None:
        stmt = stmt.where(DecisionLog.created_at > last_viewed_at)

    count = (await db.execute(stmt)).scalar() or 0
    return {
        "unread": int(count),
        "last_viewed_at": isoformat_bjt(last_viewed_at),
    }


async def mark_reports_read(
    db: AsyncSession,
    *,
    current_user: User,
    until: datetime | None = None,
    ip_address: str | None = None,
) -> dict:
    """把用户的 reports_last_viewed_at 写为 `until`（缺省为当前 now）。

    允许前端传过去 `until` 用来 "标记到某个时刻止"（比如按报告 id 回溯其 created_at）。
    无 until 则用当前北京时间。

    并发安全：用 `INSERT ... ON CONFLICT (user_id) DO UPDATE` 在单条 SQL 内完成
    upsert，并用 `GREATEST(EXCLUDED.x, table.x)` 保证最终落库的是较晚的时间戳，
    避免多端登录用户两次并发 mark-read 撞 PK 抛 IntegrityError，
    同时也防止"老 mark 把新 mark 改回去"的回退问题。
    """
    when = until or now_bjt()
    now = now_bjt()

    # v2.0.16 H4：PG 专用路径 ON CONFLICT + GREATEST；其它方言（SQLite 测试夹具）
    # 走 SELECT-then-UPDATE/INSERT 降级，保持"不回退"语义但放弃单 SQL 原子性。
    # 原子性缺失仅影响并发 mark-read 的极端时序，生产环境始终是 PG。
    dialect_name = getattr(getattr(db.bind, "dialect", None), "name", "") or ""
    if dialect_name == "postgresql":
        stmt = pg_insert(UserInboxPreference).values(
            user_id=current_user.id,
            reports_last_viewed_at=when,
            updated_at=now,
        ).on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "reports_last_viewed_at": func.greatest(
                    pg_insert(UserInboxPreference).excluded.reports_last_viewed_at,
                    UserInboxPreference.reports_last_viewed_at,
                ),
                "updated_at": now,
            },
        ).returning(UserInboxPreference.reports_last_viewed_at)

        result = await db.execute(stmt)
        final_value = result.scalar_one()
    else:
        existing = await _read_pref(db, current_user.id)
        if existing is None:
            db.add(UserInboxPreference(
                user_id=current_user.id,
                reports_last_viewed_at=when,
                updated_at=now,
            ))
            final_value = when
        else:
            prev = existing.reports_last_viewed_at
            # 不回退：只有新时间戳 > 旧时间戳才更新
            if prev is None or when > prev:
                existing.reports_last_viewed_at = when
                final_value = when
            else:
                final_value = prev
            existing.updated_at = now
    await db.flush()

    await audit.log(
        current_user.id,
        "inbox.reports_mark_read",
        "user",
        current_user.id,
        detail={"until": isoformat_bjt(when)},
        ip_address=ip_address,
    )
    return {
        "ok": True,
        # 返回真正落库的值（取 GREATEST 后），调用方/前端就能感知到
        # 并发时另一端可能写了更晚的时间戳。
        "last_viewed_at": isoformat_bjt(final_value or when),
    }
