"""role-matrix-v2 helpers shared by user/org services."""

from __future__ import annotations

from collections.abc import Iterable

import sqlalchemy as sa
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.exceptions import AppError
from app.org.models import UserOrgMembership

ROLE_ALIASES = {
    "admin": "system_admin",
    "biz_owner": "dept_admin",
    "ai_engineer": "aibp",
    "operator": "observer",
    "director": "observer",
}

ROLE_VARIANTS = {
    "system_admin": {"system_admin", "admin"},
    "dept_admin": {"dept_admin", "biz_owner"},
    "aibp": {"aibp", "ai_engineer"},
    "observer": {"observer", "operator", "director"},
}

VALID_V2_ROLES = {"system_admin", "dept_admin", "aibp", "observer"}
VALID_ALL_ROLES = set().union(*ROLE_VARIANTS.values())
VALID_STATES = {"pending", "active", "disabled"}


def normalize_role(role: str | None) -> str:
    if not role:
        return ""
    return ROLE_ALIASES.get(role, role)


def role_variants(role: str | None) -> set[str]:
    normalized = normalize_role(role)
    variants = set(ROLE_VARIANTS.get(normalized, {normalized}))
    if role:
        variants.add(role)
    return {item for item in variants if item}


def is_system_admin(user: User | None) -> bool:
    return normalize_role(getattr(user, "role", None)) == "system_admin"


def is_dept_admin(user: User | None) -> bool:
    return normalize_role(getattr(user, "role", None)) == "dept_admin"


def is_operator_manageable_role(role: str | None) -> bool:
    return normalize_role(role) in {"aibp", "observer"}


# role-matrix-v2 列（state / permissions_rev）由 migrations/versions/056_role_matrix_v2.py 建立。
# 本模块不再在请求路径里隐式 ALTER TABLE——启动时由 verify_role_matrix_schema() 显式 fail-fast。
_ROLE_MATRIX_COLUMNS: tuple[str, ...] = ("state", "permissions_rev")


async def ensure_role_matrix_columns(db: AsyncSession) -> None:
    """Lifespan 期调用：校验 users 表具备 role-matrix-v2 必需列。

    缺列即在启动时报错，避免生产请求路径隐式改 schema。per-session 缓存保留是为
    避免单元测试反复 introspect 代价。
    """
    if db.info.get("_role_matrix_v2_ready"):
        return

    conn = await db.connection()

    def _sync(sync_conn) -> list[str]:
        columns = {col["name"] for col in sa.inspect(sync_conn).get_columns("users")}
        return [name for name in _ROLE_MATRIX_COLUMNS if name not in columns]

    missing = await conn.run_sync(_sync)
    if missing:
        raise AppError(
            "ROLE_MATRIX_SCHEMA_MISSING",
            500,
            {
                "missing_columns": missing,
                "hint": "请先运行 alembic upgrade head 应用迁移 056_role_matrix_v2",
            },
        )
    db.info["_role_matrix_v2_ready"] = True


async def verify_role_matrix_schema(db: AsyncSession) -> None:
    """启动期调用：校验 schema 并提示运维。失败直接抛出，lifespan 阻断启动。"""
    await ensure_role_matrix_columns(db)
    logger.info("role-matrix-v2 schema 校验通过（users.state / users.permissions_rev 存在）")


async def attach_role_matrix_fields(db: AsyncSession, user: User | None) -> User | None:
    if user is None:
        return None
    await ensure_role_matrix_columns(db)
    row = (
        await db.execute(
            sa.text(
                "SELECT state, permissions_rev "
                "FROM users WHERE id = :user_id"
            ),
            {"user_id": user.id},
        )
    ).mappings().first()

    state = row["state"] if row and row.get("state") else ("disabled" if not user.is_active else "active")
    permissions_rev = int(row["permissions_rev"] if row and row.get("permissions_rev") is not None else 0)
    setattr(user, "state", state)
    setattr(user, "permissions_rev", permissions_rev)
    return user


async def attach_many_role_matrix_fields(db: AsyncSession, users: Iterable[User]) -> list[User]:
    items = list(users)
    if not items:
        return items
    await ensure_role_matrix_columns(db)
    user_ids = [user.id for user in items]
    stmt = sa.text(
        "SELECT id, state, permissions_rev FROM users WHERE id IN :user_ids"
    ).bindparams(sa.bindparam("user_ids", expanding=True))
    rows = (
        await db.execute(stmt, {"user_ids": user_ids})
    ).mappings().all()
    state_map = {
        row["id"]: (
            row["state"] or "active",
            int(row["permissions_rev"] if row["permissions_rev"] is not None else 0),
        )
        for row in rows
    }
    for user in items:
        state, permissions_rev = state_map.get(
            user.id,
            ("disabled" if not user.is_active else "active", 0),
        )
        setattr(user, "state", state)
        setattr(user, "permissions_rev", permissions_rev)
    return items


async def get_user_state(db: AsyncSession, user: User | str) -> str:
    await ensure_role_matrix_columns(db)
    user_id = user if isinstance(user, str) else user.id
    row = (
        await db.execute(
            sa.text("SELECT state FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
    ).mappings().first()
    if row and row.get("state"):
        return row["state"]
    if isinstance(user, str):
        return "active"
    return "disabled" if not user.is_active else "active"


async def set_user_state(
    db: AsyncSession,
    user: User,
    state: str,
) -> None:
    if state not in VALID_STATES:
        raise ValueError(f"invalid user state: {state}")
    await ensure_role_matrix_columns(db)
    await db.execute(
        sa.text(
            "UPDATE users "
            "SET state = :state, is_active = :is_active "
            "WHERE id = :user_id"
        ),
        {
            "state": state,
            "is_active": state != "disabled",
            "user_id": user.id,
        },
    )
    setattr(user, "state", state)
    user.is_active = state != "disabled"


async def bump_permissions_rev(db: AsyncSession, user_id: str) -> None:
    await ensure_role_matrix_columns(db)
    await db.execute(
        sa.text(
            "UPDATE users "
            "SET permissions_rev = COALESCE(permissions_rev, 0) + 1 "
            "WHERE id = :user_id"
        ),
        {"user_id": user_id},
    )


async def get_related_departments(db: AsyncSession, user: User | str) -> set[str]:
    user_id = user if isinstance(user, str) else user.id
    rows = (
        await db.execute(
            sa.select(UserOrgMembership.org_unit_id).where(
                UserOrgMembership.user_id == user_id,
            )
        )
    ).scalars().all()
    return set(rows)


async def get_managed_departments(db: AsyncSession, user: User) -> set[str]:
    if not is_dept_admin(user):
        return set()
    rows = (
        await db.execute(
            sa.select(UserOrgMembership.org_unit_id).where(
                UserOrgMembership.user_id == user.id,
                UserOrgMembership.is_manager == True,  # noqa: E712
            )
        )
    ).scalars().all()
    return set(rows)


async def can_manage_department(db: AsyncSession, user: User, org_unit_id: str) -> bool:
    if is_system_admin(user):
        return True
    if not is_dept_admin(user):
        return False
    return org_unit_id in await get_managed_departments(db, user)


async def count_active_system_admins(
    db: AsyncSession,
    *,
    exclude_user_id: str | None = None,
) -> int:
    await ensure_role_matrix_columns(db)
    params: dict[str, object] = {}
    extra = ""
    if exclude_user_id:
        extra = " AND id <> :exclude_user_id"
        params["exclude_user_id"] = exclude_user_id
    row = (
        await db.execute(
            sa.text(
                "SELECT COUNT(*) AS total "
                "FROM users "
                "WHERE role IN ('system_admin', 'admin') "
                "  AND state = 'active'"
                f"{extra}"
            ),
            params,
        )
    ).mappings().first()
    return int(row["total"] if row else 0)
