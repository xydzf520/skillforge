"""require_skill_access: department 字符串回退语义测试。

覆盖场景：Skill.visibility='department' 且 org_unit_id 为空（或用户缺 org membership）
时，退回到 skill.department == user.department 字符串匹配。
动机：AIClaw / 老 seed 导入的 Skill 只有 department 字符串没有 org_unit_id，
否则非 admin 的同部门成员会被 403。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.exceptions import AppError
from app.skills.core import access as access_mod
from app.skills.core.access import require_skill_access


def _user(uid: str, role: str = "aibp", department: str | None = "EC", can_view_all: bool = False):
    u = MagicMock()
    u.id = uid
    u.role = role
    u.department = department
    u.can_view_all = can_view_all
    return u


def _skill(
    sid: str,
    *,
    visibility: str = "department",
    department: str | None = "EC",
    org_unit_id: str | None = None,
):
    s = MagicMock()
    s.id = sid
    s.visibility = visibility
    s.department = department
    s.org_unit_id = org_unit_id
    return s


@pytest.fixture(autouse=True)
def _patch_cache(monkeypatch):
    async def _noop_get(_key):
        return None

    async def _noop_set(_key, _val, ttl=0):
        return None

    monkeypatch.setattr(access_mod, "_cache_get_safe", _noop_get)
    monkeypatch.setattr(access_mod, "_cache_set_safe", _noop_set)


async def _mk_db(skill, member=None):
    """构造 AsyncSession mock：db.get(Skill, id) / db.get(SkillMember, ...) / db.execute()."""
    db = AsyncMock()

    async def _get(model, key):
        name = getattr(model, "__name__", "")
        if name == "Skill":
            return skill
        if name == "SkillMember":
            return member
        if name == "OrgUnit":
            return None
        return None

    db.get = AsyncMock(side_effect=_get)
    result = MagicMock()
    result.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_department_string_fallback_allows_same_dept_when_org_unit_missing():
    """org_unit_id=None 的历史 Skill，同 department 字符串的非特权用户应能 read。"""
    skill = _skill("imported-1", org_unit_id=None, department="EC")
    user = _user("u-aibp", role="aibp", department="EC", can_view_all=False)
    db = await _mk_db(skill)

    result = await require_skill_access(db, "imported-1", user, "read")
    assert result is skill


@pytest.mark.asyncio
async def test_department_string_fallback_denies_cross_department():
    """即便 org_unit_id=None，跨 department 也不应回退放行。"""
    skill = _skill("imported-2", org_unit_id=None, department="EC")
    user = _user("u-bd", role="aibp", department="BD", can_view_all=False)
    db = await _mk_db(skill)

    with pytest.raises(AppError) as exc:
        await require_skill_access(db, "imported-2", user, "read")
    assert exc.value.code == "SKILL_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_department_string_fallback_denies_when_either_side_missing_dept():
    """skill 或 user 任一侧 department 为空 / None，回退不应放行（防空字符串误匹配）。"""
    skill = _skill("imported-3", org_unit_id=None, department=None)
    user = _user("u-x", role="aibp", department="EC", can_view_all=False)
    db = await _mk_db(skill)

    with pytest.raises(AppError) as exc:
        await require_skill_access(db, "imported-3", user, "read")
    assert exc.value.code == "SKILL_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_department_string_fallback_engages_when_user_missing_org_membership():
    """Skill 有 org_unit_id 但用户没 UserOrgMembership 时，回退到 department 字符串匹配。"""
    skill = _skill("sk-with-ou", org_unit_id="ou-999", department="EC")
    user = _user("u-orphan", role="aibp", department="EC", can_view_all=False)
    db = await _mk_db(skill)

    # db.execute 返回空 → user_in_org_tree 会判定 False
    result = await require_skill_access(db, "sk-with-ou", user, "read")
    assert result is skill


@pytest.mark.asyncio
async def test_admin_still_bypasses_all_checks():
    """系统管理员（can_view_all=True）无论 org_unit_id / department 如何都能访问。"""
    skill = _skill("imported-4", org_unit_id=None, department="OTHER")
    user = _user("admin", role="system_admin", department="EC", can_view_all=True)
    db = await _mk_db(skill)

    result = await require_skill_access(db, "imported-4", user, "read")
    assert result is skill


@pytest.mark.asyncio
async def test_company_visibility_unchanged():
    """visibility=company 的 Skill 任何人都能 read（与回退逻辑无关，回归保护）。"""
    skill = _skill("sk-public", visibility="company", org_unit_id=None, department=None)
    user = _user("u-any", role="aibp", department="BD", can_view_all=False)
    db = await _mk_db(skill)

    result = await require_skill_access(db, "sk-public", user, "read")
    assert result is skill


@pytest.mark.asyncio
async def test_private_visibility_unaffected_by_fallback():
    """visibility=private 的 Skill 不因 department 回退而放行，仍需 skill_member。"""
    skill = _skill("sk-private", visibility="private", org_unit_id=None, department="EC")
    user = _user("u-ec", role="aibp", department="EC", can_view_all=False)
    db = await _mk_db(skill, member=None)

    with pytest.raises(AppError) as exc:
        await require_skill_access(db, "sk-private", user, "read")
    assert exc.value.code == "SKILL_ACCESS_DENIED"
