"""Helpers for resolving DingTalk recipients for system notifications."""

from __future__ import annotations

from datetime import timedelta

from loguru import logger
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.time_utils import now_bjt
from app.dingtalk.models import DingTalkOutbox


def _sf():
    from app.database import async_session_factory
    return async_session_factory


def _display_name_aliases(name: str | None) -> list[str]:
    raw = str(name or "").strip()
    if not raw:
        return []

    aliases = [raw]
    for sep in ("-", "—", "–", "－", "~", "～"):
        if sep in raw:
            head = raw.split(sep, 1)[0].strip()
            tail = raw.rsplit(sep, 1)[-1].strip()
            for item in (head, tail):
                if item and item not in aliases:
                    aliases.append(item)
    return aliases


def _looks_like_oauth_openid(value: str | None) -> bool:
    text = str(value or "").strip()
    return len(text) >= 20 and any(ch.isalpha() for ch in text)


async def resolve_work_notice_user(db: AsyncSession, user: User | None) -> User | None:
    """Return the canonical user whose DingTalk id can receive work notices.

    DingTalk OAuth may create an openId-based shadow user, while work notices
    require the org-sync userid. If a no-department OAuth user looks like a
    duplicate of an org-synced user, prefer the org-synced record.
    """
    if user is None:
        return None
    if not user.dingtalk_user_id:
        return user

    aliases = _display_name_aliases(user.name)
    if not aliases:
        return user

    dept_rank = case((User.department.is_(None), 1), else_=0)
    role_rank = case(
        (User.role == "admin", 0),
        (User.role == "system_admin", 1),
        (User.role == "operator", 2),
        (User.role == "ai_engineer", 3),
        else_=9,
    )
    stmt = (
        select(User)
        .where(User.id != user.id)
        .where(User.name.in_(aliases))
        .where(User.dingtalk_user_id.is_not(None))
        .where(User.dingtalk_user_id != "")
        .where(User.is_active.is_(True))
        .where(User.state == "active")
        .order_by(dept_rank.asc(), role_rank.asc(), User.id.asc())
        .limit(10)
    )
    candidates = list((await db.execute(stmt)).scalars().all())
    canonical = next(
        (
            item
            for item in candidates
            if item.department and not _looks_like_oauth_openid(item.dingtalk_user_id)
        ),
        None,
    ) or next((item for item in candidates if item.department), None)

    if canonical is None and _looks_like_oauth_openid(user.dingtalk_user_id):
        avatar_url = str(user.avatar_url or "").strip()
        if avatar_url:
            avatar_stmt = (
                select(User)
                .where(User.id != user.id)
                .where(User.avatar_url == avatar_url)
                .where(User.dingtalk_user_id.is_not(None))
                .where(User.dingtalk_user_id != "")
                .where(User.is_active.is_(True))
                .where(User.state == "active")
                .order_by(dept_rank.asc(), role_rank.asc(), User.id.asc())
                .limit(10)
            )
            avatar_candidates = list((await db.execute(avatar_stmt)).scalars().all())
            canonical = next(
                (
                    item
                    for item in avatar_candidates
                    if item.department and not _looks_like_oauth_openid(item.dingtalk_user_id)
                ),
                None,
            ) or next((item for item in avatar_candidates if item.department), None)

    if canonical and canonical.department:
        should_resolve = (
            not user.department
            or canonical.name != user.name
            or _looks_like_oauth_openid(user.dingtalk_user_id)
        )
        if not should_resolve:
            return user
        logger.info(
            "钉钉收件人归一: {}({}) -> {}({})",
            user.name,
            user.dingtalk_user_id,
            canonical.name,
            canonical.dingtalk_user_id,
        )
        return canonical
    return user


async def resolve_admin_alert_recipients(
    session: AsyncSession | None = None,
    *,
    limit: int = 1,
) -> list[str]:
    """Return bound admin DingTalk user IDs for system alerts.

    The local bootstrap user id "admin" is not a DingTalk user id. Keep the
    default limit at one to avoid turning repeated system alerts into fan-out.
    """
    safe_limit = max(1, int(limit))
    role_rank = case(
        (User.role == "system_admin", 0),
        (User.role == "admin", 1),
        (User.role == "ai_engineer", 2),
        else_=9,
    )
    stmt = (
        select(User.dingtalk_user_id)
        .where(User.dingtalk_user_id.is_not(None))
        .where(User.dingtalk_user_id != "")
        .where(User.is_active.is_(True))
        .where(User.state == "active")
        .where(User.role.in_(["system_admin", "admin", "ai_engineer"]))
        .order_by(role_rank.asc(), User.can_view_all.desc(), User.id.asc())
        .limit(safe_limit)
    )

    async def _query(db: AsyncSession) -> list[str]:
        result = await db.execute(stmt)
        return [str(item) for item in result.scalars().all() if item]

    if session is not None:
        return await _query(session)

    async with _sf()() as db:
        return await _query(db)


async def _has_recent_admin_alert(
    db: AsyncSession,
    *,
    recipient: str,
    related_type: str | None,
    related_id: str | None,
    window_minutes: int,
) -> bool:
    if not related_type or not related_id or window_minutes <= 0:
        return False

    cutoff = now_bjt() - timedelta(minutes=window_minutes)
    stmt = (
        select(DingTalkOutbox.id)
        .where(DingTalkOutbox.message_type == "work_notice")
        .where(DingTalkOutbox.recipient_user_id == recipient)
        .where(DingTalkOutbox.related_type == related_type)
        .where(DingTalkOutbox.related_id == related_id)
        .where(DingTalkOutbox.status.in_(["pending", "sending", "sent"]))
        .where(DingTalkOutbox.created_at >= cutoff)
        .limit(1)
    )
    return (await db.scalar(stmt)) is not None


async def enqueue_admin_work_notice(
    payload: dict,
    *,
    priority: int = 1,
    related_type: str | None = None,
    related_id: str | None = None,
    session: AsyncSession | None = None,
    limit: int = 1,
    dedupe_window_minutes: int = 60,
) -> list[int]:
    """Queue an admin alert to real DingTalk-bound admin users."""
    from app.dingtalk.outbox import outbox

    async def _enqueue_with_session(db: AsyncSession | None) -> list[int]:
        recipients = await resolve_admin_alert_recipients(db, limit=limit)
        if not recipients:
            logger.warning("钉钉管理员告警跳过：没有已绑定钉钉的活跃 admin/ai_engineer 用户")
            return []

        message_ids: list[int] = []
        for recipient in recipients:
            if db is not None and await _has_recent_admin_alert(
                db,
                recipient=recipient,
                related_type=related_type,
                related_id=related_id,
                window_minutes=dedupe_window_minutes,
            ):
                logger.info(
                    "钉钉管理员告警去重跳过: type={} id={} to={}",
                    related_type,
                    related_id,
                    recipient,
                )
                continue

            message_ids.append(
                await outbox.enqueue(
                    "work_notice",
                    recipient,
                    payload,
                    priority=priority,
                    related_type=related_type,
                    related_id=related_id,
                    session=db,
                )
            )
        return message_ids

    if session is not None:
        return await _enqueue_with_session(session)

    async with _sf()() as db:
        message_ids = await _enqueue_with_session(db)
        if message_ids:
            await db.commit()
        return message_ids
