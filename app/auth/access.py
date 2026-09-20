"""v2 权限工具：基于 UserOrgMembership 的访问判定与角色兼容。"""

from __future__ import annotations

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.org.models import OrgUnit, UserOrgMembership

ROLE_COMPAT_BY_USER_ROLE: dict[str, set[str]] = {
    "system_admin": {
        "system_admin",
        "dept_admin",
        "aibp",
        "observer",
        "admin",
        "ai_engineer",
        "biz_owner",
        "director",
        "operator",
    },
    "dept_admin": {"dept_admin", "biz_owner", "director", "observer", "operator"},
    "aibp": {"aibp", "ai_engineer", "observer", "operator"},
    # observer 需兼容 legacy "operator" / "director"（migration 056 已把 director→observer，
    # 但兼容表里保留 legacy key 防止未迁移环境漏判），与 users/role_matrix.ROLE_VARIANTS 对齐。
    "observer": {"observer", "operator", "director"},
    # legacy 角色兜底，便于渐进迁移
    "admin": {"system_admin", "admin", "ai_engineer", "biz_owner", "director", "operator"},
    "ai_engineer": {"aibp", "ai_engineer", "operator"},
    "biz_owner": {"dept_admin", "biz_owner", "director", "operator"},
    "director": {"dept_admin", "director", "biz_owner", "operator"},
    "operator": {"observer", "operator", "director"},
}


def get_user_state(user: User) -> str:
    state = getattr(user, "state", None)
    if isinstance(state, str) and state:
        return state
    return "active" if bool(getattr(user, "is_active", True)) else "disabled"


def role_matches_any(user: User, allowed_roles: tuple[str, ...] | list[str] | set[str]) -> bool:
    if not allowed_roles:
        return True
    compatible = ROLE_COMPAT_BY_USER_ROLE.get(user.role, {user.role})
    return any(role in compatible for role in allowed_roles)


async def get_primary_department_id(db: AsyncSession, user: User) -> str | None:
    rows = (
        await db.execute(
            select(UserOrgMembership.org_unit_id, UserOrgMembership.membership_type)
            .where(UserOrgMembership.user_id == user.id)
        )
    ).all()
    if not rows:
        return None
    for org_unit_id, membership_type in rows:
        if membership_type == "primary":
            return org_unit_id
    return rows[0][0]


async def get_managed_departments(db: AsyncSession, user: User) -> set[str]:
    if user.role != "dept_admin":
        return set()
    stmt = select(UserOrgMembership.org_unit_id).where(
        UserOrgMembership.user_id == user.id,
        UserOrgMembership.is_manager == True,  # noqa: E712
    )
    return set((await db.execute(stmt)).scalars().all())


async def get_related_departments(db: AsyncSession, user: User) -> set[str]:
    stmt = select(UserOrgMembership.org_unit_id).where(UserOrgMembership.user_id == user.id)
    return set((await db.execute(stmt)).scalars().all())


async def expand_department_subtree(
    db: AsyncSession, department_ids: set[str]
) -> set[str]:
    """将给定部门集合向下展开到整棵子树（基于 OrgUnit.path 前缀匹配）。

    SQL 批量化：先一次性拿到输入部门的 path，再用 or_(path == p, path like f"{p}/%")
    组合条件一次查询所有子树节点，避免 N+1。
    """
    if not department_ids:
        return set()

    orgs = (
        await db.execute(
            select(OrgUnit.id, OrgUnit.path).where(OrgUnit.id.in_(department_ids))
        )
    ).all()

    expanded: set[str] = {org_id for org_id, _ in orgs}
    path_conditions = [
        or_(OrgUnit.path == path, OrgUnit.path.like(f"{path}/%"))
        for _, path in orgs
        if path
    ]
    if path_conditions:
        stmt = select(OrgUnit.id).where(or_(*path_conditions))
        expanded.update((await db.execute(stmt)).scalars().all())
    return expanded


# 向后兼容的私有别名（已被外部误用，保留一段时间；新代码直接用 expand_department_subtree）
_expand_department_subtree = expand_department_subtree


async def expand_org_lineage(
    db: AsyncSession, org_ids: set[str]
) -> set[str]:
    """将 org 集合向上/向下扩展到完整 lineage（祖先 + 自身 + 子孙）。

    Department 可见性默认按整条继承链：用户在 `/root/dept-a/dept-a-team` 时，
    既能看 `dept-a` 级的 Skill（祖先方向），也能看更深的子团队（子孙方向）。
    该函数用于从 user 所属 org 计算出 SQL `IN` 候选集，避免 SELECT 全表。
    """
    if not org_ids:
        return set()

    rows = (
        await db.execute(
            select(OrgUnit.id, OrgUnit.path).where(OrgUnit.id.in_(org_ids))
        )
    ).all()
    paths = [path for _, path in rows if path]
    if not paths:
        return set(org_ids)

    # 祖先 path（纯字符串切片，不查 DB）
    ancestor_paths: set[str] = set()
    for raw in paths:
        clean = raw.rstrip("/")
        leading = "/" if clean.startswith("/") else ""
        parts = [part for part in clean.split("/") if part]
        for idx in range(1, len(parts)):
            ancestor_paths.add(leading + "/".join(parts[:idx]))

    # 一次 SQL 同时覆盖三种关系：自身 + 祖先（精确 in）+ 子孙（path LIKE 'user/%'）。
    # 注意 LIKE 对 `%` / `_` / `\` 敏感，虽然当前 org path 由系统维护、实践中不含
    # 特殊字符，仍用显式 escape 防御畸形数据污染查询。
    path_in_set = ancestor_paths | set(paths)
    conditions = [OrgUnit.path.in_(list(path_in_set))] if path_in_set else []
    for raw in paths:
        safe_prefix = (
            raw.rstrip("/")
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        conditions.append(OrgUnit.path.like(f"{safe_prefix}/%", escape="\\"))
    stmt = select(OrgUnit.id).where(or_(*conditions))
    expanded = set((await db.execute(stmt)).scalars().all())
    return expanded | set(org_ids)


async def expand_department_ancestors(
    db: AsyncSession, department_ids: set[str]
) -> set[str]:
    """Expand memberships upward so a child-team member can read parent-department resources.

    This is intentionally read-only scope expansion.  It must not be reused for
    edit, approval, publishing, or membership-management permissions.
    """
    if not department_ids:
        return set()
    rows = (
        await db.execute(
            select(OrgUnit.id, OrgUnit.path).where(OrgUnit.id.in_(department_ids))
        )
    ).all()
    ancestor_paths: set[str] = set()
    for _, raw_path in rows:
        if not raw_path:
            continue
        clean = raw_path.rstrip("/")
        leading = "/" if clean.startswith("/") else ""
        parts = [part for part in clean.split("/") if part]
        for index in range(1, len(parts) + 1):
            ancestor_paths.add(leading + "/".join(parts[:index]))
    if not ancestor_paths:
        return set(department_ids)
    ancestors = set(
        (
            await db.execute(
                select(OrgUnit.id).where(OrgUnit.path.in_(sorted(ancestor_paths)))
            )
        ).scalars().all()
    )
    return ancestors | set(department_ids)


async def get_accessible_departments(db: AsyncSession, user: User) -> set[str] | None:
    if user.role in ("system_admin", "admin") or bool(getattr(user, "can_view_all", False)):
        return None

    base = await get_related_departments(db, user)

    # dept_admin：关联部门直读 + 管辖部门子树展开
    # spec §3.2：读范围 = 关联部门直读 + 管辖部门（is_manager=True）整棵子树
    # 注意写操作 / 审批归属 / 成员管理仍只按直接管辖部门判定（见 can_manage_department）
    if user.role == "dept_admin":
        managed = await get_managed_departments(db, user)
        managed_subtree = await expand_department_subtree(db, managed)
        return base | managed_subtree

    # observer：关联部门全部向下展开到子树（spec §3.4）
    if user.role == "observer" and base:
        return await expand_department_subtree(db, base)

    return base


async def can_access_department(db: AsyncSession, user: User, department_id: str | None) -> bool:
    if user.role in ("system_admin", "admin") or bool(getattr(user, "can_view_all", False)):
        return True
    if not department_id:
        return False
    accessible = await get_accessible_departments(db, user)
    if accessible is None:
        return True
    return department_id in accessible


async def can_manage_department(db: AsyncSession, user: User, department_id: str | None) -> bool:
    if user.role in ("system_admin", "admin"):
        return True
    if user.role != "dept_admin" or not department_id:
        return False
    managed = await get_managed_departments(db, user)
    return department_id in managed


async def bump_permissions_rev(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(permissions_rev=User.permissions_rev + 1)
    )
    await db.flush()
