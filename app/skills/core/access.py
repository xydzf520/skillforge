"""Skill 成员模型 + 资源级权限检查。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, and_, false, or_, select, true
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.access import (
    can_access_department,
    can_manage_department,
    get_accessible_departments,
    get_managed_departments,
    role_matches_any,
)
from app.common.cache import cache_delete_pattern, cache_get, cache_set
from app.common.exceptions import AppError
from app.common.time_utils import now_bjt
from app.database import Base
from app.org.models import OrgUnit, UserOrgMembership
from app.skills.core.models import Skill
from app.users.role_matrix import normalize_role

_policy_evaluators = []


class SkillMember(Base):
    __tablename__ = "skill_members"

    skill_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    granted_by: Mapped[str | None] = mapped_column(String(50))
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)


class SkillTag(Base):
    __tablename__ = "skill_tags"

    skill_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    tag: Mapped[str] = mapped_column(String(50), primary_key=True)


def register_skill_policy_evaluator(evaluator) -> None:
    """注册额外策略扩展点。evaluator(db, skill, user, action) -> True/False/None."""
    if evaluator not in _policy_evaluators:
        _policy_evaluators.append(evaluator)


def _department_strings_match(user: User, skill: Skill) -> bool:
    user_dept = (getattr(user, "department", None) or "").strip()
    skill_dept = (getattr(skill, "department", None) or "").strip()
    return bool(user_dept) and bool(skill_dept) and user_dept == skill_dept


def _is_system_admin(user: User) -> bool:
    return role_matches_any(user, ("admin",))


def _has_read_all(user: User) -> bool:
    return _is_system_admin(user) or bool(getattr(user, "can_view_all", False))


def _role_is(user: User, role: str) -> bool:
    return role_matches_any(user, (role,))


def clear_skill_policy_evaluators() -> None:
    _policy_evaluators.clear()


async def _cache_get_safe(key: str):
    try:
        return await cache_get(key)
    except RuntimeError:
        return None


async def _cache_set_safe(key: str, value, ttl: int) -> None:
    try:
        await cache_set(key, value, ttl=ttl)
    except RuntimeError:
        return


async def _cache_delete_pattern_safe(pattern: str) -> None:
    try:
        await cache_delete_pattern(pattern)
    except RuntimeError:
        return


async def get_skill_member(
    db: AsyncSession,
    *,
    skill_id: str,
    user_id: str,
) -> SkillMember | None:
    return await db.get(SkillMember, (skill_id, user_id))


async def list_user_member_skill_ids(db: AsyncSession, user_id: str) -> list[str]:
    rows = await db.execute(
        select(SkillMember.skill_id).where(SkillMember.user_id == user_id)
    )
    return [row[0] for row in rows.all()]


async def list_user_member_scope(
    db: AsyncSession,
    user_id: str,
) -> tuple[set[str], set[str]]:
    rows = (
        await db.execute(
            select(SkillMember.skill_id, Skill.department)
            .join(Skill, Skill.id == SkillMember.skill_id)
            .where(SkillMember.user_id == user_id)
        )
    ).all()
    member_skill_ids = {row[0] for row in rows if row[0]}
    member_departments = {row[1] for row in rows if row[1]}
    return member_skill_ids, member_departments


async def list_user_member_departments(db: AsyncSession, user_id: str) -> set[str]:
    rows = (
        await db.execute(
            select(Skill.department)
            .join(SkillMember, SkillMember.skill_id == Skill.id)
            .where(SkillMember.user_id == user_id)
        )
    ).all()
    return {row[0] for row in rows if row[0]}


async def _org_names(db: AsyncSession, org_ids: set[str]) -> set[str]:
    if not org_ids:
        return set()
    rows = await db.execute(select(OrgUnit.name).where(OrgUnit.id.in_(org_ids)))
    return {name for name in rows.scalars().all() if isinstance(name, str) and name}


async def _accessible_department_scope(
    db: AsyncSession,
    user: User,
) -> tuple[set[str], set[str], bool]:
    """Return (org_ids, department_names, read_all) for Skill read filtering."""
    if _has_read_all(user):
        return set(), set(), True

    accessible = await get_accessible_departments(db, user)
    if accessible is None:
        return set(), set(), True
    org_ids = set(accessible)
    names = await _org_names(db, org_ids)
    user_dept = (getattr(user, "department", None) or "").strip()
    if isinstance(user_dept, str) and user_dept:
        names.add(user_dept)
    return org_ids, names, False


async def build_skill_access_filter(
    db: AsyncSession,
    user: User,
    action: str = "read",
):
    """Build the SQLAlchemy WHERE predicate for Skill access.

    P0 supports read-scope filtering for all list/aggregation endpoints. Other
    actions stay single-object gated by ``require_skill_access`` because their
    rules depend on SkillMember controlled mode and write ownership.
    """
    if action != "read":
        # No endpoint should use this as a write gate. Return no rows rather
        # than accidentally over-granting if a caller misuses the helper.
        return false()

    org_ids, dept_names, read_all = await _accessible_department_scope(db, user)
    if read_all:
        return true()

    member_skill_ids = [
        sid for sid in await list_user_member_skill_ids(db, user.id)
        if isinstance(sid, str) and sid
    ]
    conditions = [Skill.visibility == "company"]
    dept_conditions = []
    if org_ids:
        dept_conditions.append(Skill.org_unit_id.in_(sorted(org_ids)))
    if dept_names:
        dept_conditions.append(Skill.department.in_(sorted(dept_names)))
    if dept_conditions:
        conditions.append(
            and_(Skill.visibility == "department", or_(*dept_conditions))
        )
    if member_skill_ids:
        conditions.append(Skill.id.in_(member_skill_ids))
    conditions.append(Skill.owner == user.id)
    return or_(*conditions) if conditions else false()


async def _skill_has_members(db: AsyncSession, skill_id: str) -> bool:
    rows = await db.execute(
        select(SkillMember.user_id).where(SkillMember.skill_id == skill_id).limit(1)
    )
    return rows.first() is not None


async def _can_access_skill_department(db: AsyncSession, user: User, skill: Skill) -> bool:
    if _has_read_all(user):
        return True
    if skill.org_unit_id and await user_in_org_tree(db, user.id, skill.org_unit_id):
        return True
    if skill.org_unit_id and await can_access_department(db, user, skill.org_unit_id):
        return True
    org_ids, dept_names, read_all = await _accessible_department_scope(db, user)
    if read_all:
        return True
    skill_dept = (getattr(skill, "department", None) or "").strip()
    if skill_dept and skill_dept in dept_names:
        return True
    return _department_strings_match(user, skill)


async def _can_manage_skill_department(db: AsyncSession, user: User, skill: Skill) -> bool:
    if _is_system_admin(user):
        return True
    if not _role_is(user, "dept_admin"):
        return False
    if skill.org_unit_id and await can_manage_department(db, user, skill.org_unit_id):
        return True
    managed_ids = await get_managed_departments(db, user)
    managed_names = await _org_names(db, managed_ids)
    skill_dept = (getattr(skill, "department", None) or "").strip()
    if skill_dept and skill_dept in managed_names:
        return True
    # Compatibility for legacy/test data that has no UserOrgMembership rows.
    return _department_strings_match(user, skill)


async def get_skill_permissions(
    db: AsyncSession,
    skill: Skill,
    user: User,
) -> dict[str, bool]:
    """Compute object-level Skill permissions for the current user."""
    permissions = {
        "read": False,
        "execute": False,
        "edit": False,
        "review": False,
        "publish": False,
        "delete": False,
        "manage_members": False,
    }

    if _is_system_admin(user):
        return {key: True for key in permissions}

    member = await get_skill_member(db, skill_id=skill.id, user_id=user.id)
    raw_member_role = getattr(member, "role", None) if member else None
    member_role = raw_member_role if isinstance(raw_member_role, str) else None
    is_owner = bool(getattr(skill, "owner", None) and skill.owner == user.id)
    if is_owner and member_role is None:
        member_role = "owner"

    if bool(getattr(user, "can_view_all", False)):
        permissions["read"] = True
    elif skill.visibility == "company":
        permissions["read"] = True
    elif skill.visibility == "department":
        permissions["read"] = await _can_access_skill_department(db, user, skill)
    elif skill.visibility == "private":
        permissions["read"] = bool(member_role)
    if member_role or is_owner:
        permissions["read"] = True

    if not permissions["read"]:
        return permissions

    can_manage_dept = await _can_manage_skill_department(db, user, skill)
    is_aibp = _role_is(user, "aibp")
    is_observer = normalize_role(getattr(user, "role", None)) == "observer"

    if can_manage_dept:
        permissions.update({
            "execute": True,
            "edit": True,
            "review": True,
            "publish": True,
            "delete": True,
            "manage_members": True,
        })
        return permissions

    if member_role:
        permissions["execute"] = permissions["execute"] or member_role in {
            "owner", "editor", "viewer", "reviewer"
        }
        permissions["edit"] = permissions["edit"] or member_role in {"owner", "editor"}
        permissions["review"] = permissions["review"] or member_role in {"owner", "reviewer"}
        permissions["publish"] = permissions["publish"] or member_role == "owner"
        permissions["delete"] = permissions["delete"] or member_role == "owner"
        permissions["manage_members"] = permissions["manage_members"] or member_role == "owner"

    if is_observer:
        return permissions

    # Backward-compatible portal/manual execution: any non-observer who can
    # read a company/department/member Skill can execute it unless a stricter
    # route-level role check applies before this helper.
    permissions["execute"] = True

    if is_aibp and await _can_access_skill_department(db, user, skill):
        has_members = bool(member_role) or await _skill_has_members(db, skill.id)
        if has_members:
            permissions["edit"] = member_role in {"owner", "editor"}
            permissions["publish"] = member_role == "owner"
            permissions["delete"] = member_role == "owner"
            permissions["manage_members"] = member_role == "owner"
            permissions["review"] = member_role in {"owner", "reviewer"}
        else:
            permissions["edit"] = True

    return permissions


async def require_skill_access(
    db: AsyncSession,
    skill_id: str,
    user: User,
    action: str,
) -> Skill:
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    user_rev = int(getattr(user, "permissions_rev", 0) or 0)
    cache_key = f"skill:access:{skill_id}:{user.id}:{user_rev}:{action}"
    cached = await _cache_get_safe(cache_key)
    if cached is True:
        return skill

    permissions = await get_skill_permissions(db, skill, user)
    allowed = bool(permissions.get(action))

    for evaluator in list(_policy_evaluators):
        decision = await evaluator(db, skill, user, action)
        if decision is False:
            raise AppError("SKILL_ACCESS_DENIED", 403)
        if decision is True:
            allowed = True
            break

    if not allowed:
        raise AppError("SKILL_ACCESS_DENIED", 403)

    await _cache_set_safe(cache_key, True, ttl=60)
    return skill


async def user_in_org_tree(db: AsyncSession, user_id: str, org_unit_id: str | None) -> bool:
    if not org_unit_id:
        return False

    result = await db.execute(
        select(UserOrgMembership.org_unit_id).where(UserOrgMembership.user_id == user_id)
    )
    user_orgs = {row[0] for row in result.all()}
    if org_unit_id in user_orgs:
        return True
    if not user_orgs:
        return False

    target_org = await db.get(OrgUnit, org_unit_id)
    if not target_org or not target_org.path:
        return False

    path_rows = await db.execute(select(OrgUnit.path).where(OrgUnit.id.in_(user_orgs)))
    for (path,) in path_rows.all():
        if path and (path.startswith(target_org.path) or target_org.path.startswith(path)):
            return True
    return False


async def invalidate_skill_access_cache(skill_id: str) -> None:
    await _cache_delete_pattern_safe(f"skill:access:{skill_id}:*")
    await _cache_delete_pattern_safe("portal:skills:*")
