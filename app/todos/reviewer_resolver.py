"""待办 reviewer 解析。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.exceptions import AppError


async def resolve_reviewers_from_spec(
    db: AsyncSession,
    *,
    reviewers: list[str],
    reviewer_role: str | None,
) -> list[str]:
    """从 TodoSpec 提供的 reviewers + reviewer_role 解析最终接收人。"""
    if reviewers:
        result = await db.execute(
            select(User.id)
            .where(User.id.in_(reviewers))
            .where(User.is_active == True)  # noqa: E712
        )
        active_ids = {row[0] for row in result.all()}
        resolved = [uid for uid in reviewers if uid in active_ids]
        if len(resolved) != len(reviewers):
            raise AppError(
                "INVALID_REVIEWER_CONFIG",
                400,
                {"detail": {"requested": reviewers, "resolved": resolved}},
            )
        return resolved

    if reviewer_role:
        result = await db.execute(
            select(User.id)
            .where(User.role == reviewer_role)
            .where(User.is_active == True)  # noqa: E712
        )
        resolved = [row[0] for row in result.all()]
        if not resolved:
            raise AppError(
                "INVALID_REVIEWER_CONFIG",
                400,
                {"detail": {"reviewer_role": reviewer_role}},
            )
        return resolved

    return []


async def resolve_reviewers(db: AsyncSession, skill_meta: dict) -> list[str]:
    reviewers: list[str] = []
    explicit_config = False

    if skill_meta.get("reviewer"):
        reviewer_value = skill_meta.get("reviewer")
        reviewers = reviewer_value if isinstance(reviewer_value, list) else [reviewer_value]
        explicit_config = True
    elif skill_meta.get("reviewer_role"):
        result = await db.execute(
            select(User.id)
            .where(User.role == skill_meta["reviewer_role"])
            .where(User.is_active == True)  # noqa: E712
        )
        reviewers = [row[0] for row in result.all()]
        explicit_config = True
    elif skill_meta.get("target_users"):
        reviewers = list(skill_meta.get("target_users") or [])
    elif skill_meta.get("owner"):
        reviewers = [skill_meta["owner"]]

    if not reviewers:
        if explicit_config:
            raise AppError("INVALID_REVIEWER_CONFIG", 400)
        return []

    normalized = [str(item).strip() for item in reviewers if str(item).strip()]
    if not normalized:
        return []

    result = await db.execute(
        select(User.id)
        .where(User.id.in_(normalized))
        .where(User.is_active == True)  # noqa: E712
    )
    active_ids = {row[0] for row in result.all()}
    resolved = [user_id for user_id in normalized if user_id in active_ids]
    if explicit_config and len(resolved) != len(normalized):
        raise AppError("INVALID_REVIEWER_CONFIG", 400, {"detail": {"requested": normalized, "resolved": resolved}})
    return resolved
