"""Inbox assignee identity helpers."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.dingtalk.recipients import (
    _display_name_aliases,
    resolve_work_notice_user,
)
from app.todos._acl import is_global_inbox_reader


async def visible_inbox_assignee_ids_for_user(
    db: AsyncSession,
    user: User | None,
) -> list[str]:
    """Return platform user ids that belong to the same inbox recipient.

    DingTalk OAuth can create an openId-based shadow user before org sync has
    the real userid. Todos should stay assigned to the org-synced account for
    work-notice delivery, while the logged-in shadow account must still see the
    same inbox items.
    """
    if user is None or not getattr(user, "id", None):
        return []

    ids: list[str] = []

    def add_id(value: str | None) -> None:
        text = str(value or "").strip()
        if text and text not in ids:
            ids.append(text)

    add_id(user.id)
    canonical = await resolve_work_notice_user(db, user)
    add_id(getattr(canonical, "id", None))

    aliases = _display_name_aliases(getattr(user, "name", None))
    predicates = []
    if aliases:
        predicates.append(User.name.in_(aliases))
    avatar_url = str(getattr(user, "avatar_url", None) or "").strip()
    if avatar_url:
        predicates.append(User.avatar_url == avatar_url)
    union_id = str(getattr(user, "dingtalk_union_id", None) or "").strip()
    if union_id:
        predicates.append(User.dingtalk_union_id == union_id)
    dingtalk_user_id = str(getattr(user, "dingtalk_user_id", None) or "").strip()
    if dingtalk_user_id:
        predicates.append(User.dingtalk_user_id == dingtalk_user_id)

    if not predicates:
        return ids

    candidates = list(
        (
            await db.execute(
                select(User)
                .where(User.id != user.id)
                .where(User.is_active.is_(True))
                .where(User.state == "active")
                .where(or_(*predicates))
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    for candidate in candidates:
        resolved = await resolve_work_notice_user(db, candidate)
        if resolved and resolved.id in ids:
            add_id(candidate.id)
            continue
        if candidate.id in ids:
            add_id(getattr(resolved, "id", None))

    return ids


async def visible_inbox_assignee_scope_for_user(
    db: AsyncSession,
    user: User | None,
) -> tuple[list[str], bool]:
    """Return visible assignee ids plus whether that identity has global scope."""
    ids = await visible_inbox_assignee_ids_for_user(db, user)
    if not ids:
        return [], False
    users = list(
        (
            await db.execute(
                select(User).where(User.id.in_(ids))
            )
        )
        .scalars()
        .all()
    )
    return ids, any(is_global_inbox_reader(item) for item in users)
