"""Workbench 路由公共边界。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.skills.core.service_shared import ensure_skill_access


async def check_skill_read_access(skill_id: str, current_user: User, db: AsyncSession) -> None:
    """统一的 Workbench skill 读权限检查。"""
    await ensure_skill_access(db, skill_id, current_user, "read")


def db_session_for_check():
    """供 WS 端点复用的轻量 session 工厂。"""
    from app import database as _db_mod

    return _db_mod.async_session_factory()
