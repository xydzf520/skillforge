"""M1 Skill 资源级权限测试。"""

from __future__ import annotations

import importlib.util
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.exceptions import AppError


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


if not _has_module("app.skills.access"):
    pytest.skip("skill access backend not present yet", allow_module_level=True)


from app.skills.core.access import build_skill_access_filter, get_skill_permissions, require_skill_access  # noqa: E402
from app.skills.members import SkillMember  # noqa: E402
from app.skills.core.models import Skill  # noqa: E402
from app.org.models import OrgUnit, UserOrgMembership  # noqa: E402
from app.skills.core.service_shared import ensure_skill_access, ensure_skill_department_access  # noqa: E402


def _mock_user(*, user_id: str = "u1", role: str = "operator", can_view_all: bool = False):
    user = MagicMock()
    user.id = user_id
    user.role = role
    user.can_view_all = can_view_all
    user.department = "EC"
    return user


def _mock_skill(*, visibility: str = "department", org_unit_id: str | None = "EC"):
    skill = MagicMock(spec=Skill)
    skill.id = "skill-1"
    skill.visibility = visibility
    skill.org_unit_id = org_unit_id
    skill.owner = "owner-1"
    skill.updated_at = datetime.utcnow()
    return skill


def _result(*, rows=None):
    result = MagicMock()
    result.all.return_value = rows or []
    return result


@pytest.mark.asyncio
async def test_admin_can_access_any_skill():
    db = AsyncMock()
    db.get = AsyncMock(return_value=_mock_skill(visibility="private", org_unit_id="OPS"))
    user = _mock_user(role="admin")

    skill = await require_skill_access(db, "skill-1", user, "edit")

    assert skill.id == "skill-1"
    db.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_can_edit():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        _mock_skill(visibility="private"),
        MagicMock(spec=SkillMember, role="owner"),
    ])

    skill = await require_skill_access(
        db,
        "skill-1",
        _mock_user(user_id="owner-1", role="member"),
        "edit",
    )

    assert skill.id == "skill-1"


@pytest.mark.asyncio
async def test_legacy_skill_owner_without_member_can_edit():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        _mock_skill(visibility="department"),
        None,
    ])
    db.execute = AsyncMock(return_value=_result(rows=[]))

    skill = await require_skill_access(
        db,
        "skill-1",
        _mock_user(user_id="owner-1", role="aibp"),
        "edit",
    )

    assert skill.id == "skill-1"


@pytest.mark.asyncio
async def test_editor_can_edit():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        _mock_skill(visibility="private"),
        MagicMock(spec=SkillMember, role="editor"),
    ])

    skill = await require_skill_access(
        db,
        "skill-1",
        _mock_user(user_id="editor-1", role="member"),
        "edit",
    )

    assert skill.id == "skill-1"


@pytest.mark.asyncio
async def test_viewer_cannot_edit():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        _mock_skill(visibility="private"),
        MagicMock(spec=SkillMember, role="viewer"),
    ])

    with pytest.raises(AppError) as exc_info:
        await require_skill_access(
            db,
            "skill-1",
            _mock_user(user_id="viewer-1", role="member"),
            "edit",
        )

    assert exc_info.value.code == "SKILL_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_company_visibility_allows_any_user():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[_mock_skill(visibility="company"), None])

    skill = await require_skill_access(db, "skill-1", _mock_user(), "read")

    assert skill.id == "skill-1"


@pytest.mark.asyncio
async def test_department_visibility_checks_org_tree():
    skill = _mock_skill(visibility="department", org_unit_id="EC")
    skill_org = MagicMock(spec=OrgUnit, path="/EC")
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        skill,
        None,
        skill_org,
    ])
    db.execute = AsyncMock(side_effect=[
        _result(rows=[("EC-OPS",)]),
        _result(rows=[("/EC/OPS",)]),
    ])

    user = _mock_user(user_id="u1", role="member")
    skill_result = await require_skill_access(db, "skill-1", user, "read")

    assert skill_result.id == "skill-1"
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_execute_action_uses_org_tree_via_shared_helper():
    skill = _mock_skill(visibility="department", org_unit_id="EC")
    skill_org = MagicMock(spec=OrgUnit, path="/EC")
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        skill,
        None,
        skill_org,
    ])
    db.execute = AsyncMock(side_effect=[
        _result(rows=[("EC-OPS",)]),
        _result(rows=[("/EC/OPS",)]),
    ])

    user = _mock_user(user_id="u1", role="member")
    skill_result = await ensure_skill_access(db, "skill-1", user, "execute")

    assert skill_result.id == "skill-1"


@pytest.mark.asyncio
async def test_compat_department_helper_delegates_to_read_access():
    skill = _mock_skill(visibility="department", org_unit_id="EC")
    skill_org = MagicMock(spec=OrgUnit, path="/EC")
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        skill,
        None,
        skill_org,
    ])
    db.execute = AsyncMock(side_effect=[
        _result(rows=[("EC-OPS",)]),
        _result(rows=[("/EC/OPS",)]),
    ])

    user = _mock_user(user_id="u1")
    skill_result = await ensure_skill_department_access(db, "skill-1", user)

    assert skill_result.id == "skill-1"


@pytest.mark.asyncio
async def test_private_visibility_requires_membership():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[_mock_skill(visibility="private"), None])

    with pytest.raises(AppError) as exc_info:
        await require_skill_access(db, "skill-1", _mock_user(), "read")

    assert exc_info.value.code == "SKILL_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_department_visibility_allows_explicit_member_read():
    skill = _mock_skill(visibility="department", org_unit_id="EC")
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        skill,
        MagicMock(spec=SkillMember, role="viewer"),
    ])
    db.execute = AsyncMock(return_value=_result(rows=[]))

    skill_result = await require_skill_access(db, "skill-1", _mock_user(user_id="viewer-1"), "read")

    assert skill_result.id == "skill-1"


@pytest.mark.asyncio
async def test_can_view_all_does_not_grant_skill_write_permissions():
    skill = _mock_skill(visibility="private", org_unit_id="OPS")
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    permissions = await get_skill_permissions(
        db,
        skill,
        _mock_user(role="observer", can_view_all=True),
    )

    assert permissions["read"] is True
    assert permissions["execute"] is False
    assert permissions["edit"] is False
    assert permissions["publish"] is False
    assert permissions["delete"] is False


@pytest.mark.asyncio
async def test_legacy_observer_alias_cannot_execute_visible_skill():
    skill = _mock_skill(visibility="company")
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    permissions = await get_skill_permissions(db, skill, _mock_user(role="operator"))

    assert permissions["read"] is True
    assert permissions["execute"] is False
    assert permissions["edit"] is False


@pytest.mark.asyncio
async def test_read_filter_includes_legacy_skill_owner():
    user = _mock_user(user_id="owner-1", role="aibp")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result(rows=[]))

    predicate = await build_skill_access_filter(db, user, "read")

    assert "skills.owner" in str(predicate)


@pytest.mark.asyncio
async def test_require_skill_access_cache_key_includes_permissions_rev():
    skill = _mock_skill(visibility="company")
    user = _mock_user(user_id="cached-user", role="operator")
    user.permissions_rev = 7
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[skill, None])

    with patch("app.skills.core.access.cache_get", new=AsyncMock(return_value=True)) as mock_cache_get:
        result = await require_skill_access(db, "skill-1", user, "read")

    assert result.id == "skill-1"
    cache_key = mock_cache_get.await_args.args[0]
    assert cache_key == "skill:access:skill-1:cached-user:7:read"
