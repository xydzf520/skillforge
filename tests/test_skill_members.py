"""M1 Skill 成员表与成员权限测试。"""

from __future__ import annotations

import importlib.util
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.exceptions import AppError


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


if not _has_module("app.skills.members"):
    pytest.skip("skill members backend not present yet", allow_module_level=True)


from app.skills.core.access import require_skill_access  # noqa: E402
from app.skills.members import SkillMember, SkillTag  # noqa: E402
from app.skills.core.models import Skill  # noqa: E402


def _mock_user(*, user_id: str = "u1", role: str = "operator"):
    user = MagicMock()
    user.id = user_id
    user.role = role
    user.can_view_all = False
    user.department = "EC"
    return user


def _mock_skill(*, visibility: str = "private"):
    skill = MagicMock(spec=Skill)
    skill.id = "skill-1"
    skill.visibility = visibility
    skill.org_unit_id = "EC"
    skill.updated_at = datetime.utcnow()
    return skill


@pytest.mark.asyncio
async def test_skill_member_model_contract():
    assert SkillMember.__tablename__ == "skill_members"
    assert SkillTag.__tablename__ == "skill_tags"
    assert list(SkillMember.__table__.primary_key.columns.keys()) == ["skill_id", "user_id"]
    assert list(SkillTag.__table__.primary_key.columns.keys()) == ["skill_id", "tag"]


@pytest.mark.asyncio
async def test_add_member_grants_read_and_edit():
    read_db = AsyncMock()
    read_db.get = AsyncMock(side_effect=[
        _mock_skill(),
        MagicMock(spec=SkillMember, role="editor"),
    ])
    edit_db = AsyncMock()
    edit_db.get = AsyncMock(side_effect=[
        _mock_skill(),
        MagicMock(spec=SkillMember, role="editor"),
    ])

    read_skill = await require_skill_access(read_db, "skill-1", _mock_user(user_id="member-1"), "read")
    edit_skill = await require_skill_access(edit_db, "skill-1", _mock_user(user_id="member-1"), "edit")

    assert read_skill.id == "skill-1"
    assert edit_skill.id == "skill-1"


@pytest.mark.asyncio
async def test_remove_member_revokes_private_access():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[_mock_skill(), None])

    with pytest.raises(AppError) as exc_info:
        await require_skill_access(db, "skill-1", _mock_user(user_id="member-1"), "read")

    assert exc_info.value.code == "SKILL_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_reviewer_can_review():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        _mock_skill(),
        MagicMock(spec=SkillMember, role="reviewer"),
    ])

    skill = await require_skill_access(db, "skill-1", _mock_user(user_id="reviewer-1"), "review")

    assert skill.id == "skill-1"


@pytest.mark.asyncio
async def test_owner_can_manage_members():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        _mock_skill(),
        MagicMock(spec=SkillMember, role="owner"),
    ])

    skill = await require_skill_access(db, "skill-1", _mock_user(user_id="owner-1"), "manage_members")

    assert skill.id == "skill-1"


@pytest.mark.asyncio
async def test_owner_can_publish():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        _mock_skill(),
        MagicMock(spec=SkillMember, role="owner"),
    ])

    skill = await require_skill_access(db, "skill-1", _mock_user(user_id="owner-1"), "publish")

    assert skill.id == "skill-1"


@pytest.mark.asyncio
async def test_viewer_cannot_publish():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        _mock_skill(),
        MagicMock(spec=SkillMember, role="viewer"),
    ])

    with pytest.raises(AppError) as exc_info:
        await require_skill_access(db, "skill-1", _mock_user(user_id="viewer-1"), "publish")

    assert exc_info.value.code == "SKILL_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_skill_member_management_api(client):
    from app.auth.models import User
    from app.database import async_session_factory

    skill_id = "MEMBER-API-01"
    target_user_id = "member-api-user"
    async with async_session_factory() as session:
        session.add_all([
            Skill(
                id=skill_id,
                name="成员权限 API",
                department="AI小组",
                status="draft",
                visibility="department",
            ),
            User(
                id=target_user_id,
                username=target_user_id,
                name="成员测试用户",
                role="aibp",
                department="AI小组",
                state="active",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            ),
        ])
        await session.commit()

    list_resp = await client.get(f"/api/skills/{skill_id}/members")
    assert list_resp.status_code == 200
    assert list_resp.json()["permissions"]["manage_members"] is True

    add_resp = await client.post(
        f"/api/skills/{skill_id}/members",
        json={"user_id": target_user_id, "role": "editor"},
    )
    assert add_resp.status_code == 200, add_resp.text

    async with async_session_factory() as session:
        member = await session.get(SkillMember, (skill_id, target_user_id))
        user = await session.get(User, target_user_id)
        assert member is not None
        assert member.role == "editor"
        assert user.permissions_rev == 0

    visibility_resp = await client.put(
        f"/api/skills/{skill_id}/visibility",
        json={"visibility": "private"},
    )
    assert visibility_resp.status_code == 200, visibility_resp.text
    assert visibility_resp.json()["visibility"] == "private"

    delete_resp = await client.delete(f"/api/skills/{skill_id}/members/{target_user_id}")
    assert delete_resp.status_code == 200, delete_resp.text

    async with async_session_factory() as session:
        member = await session.get(SkillMember, (skill_id, target_user_id))
        user = await session.get(User, target_user_id)
        skill = await session.get(Skill, skill_id)
        assert member is None
        assert user.permissions_rev == 0
        assert skill.visibility == "private"


@pytest.mark.asyncio
async def test_skill_member_management_api_protects_last_owner(client):
    from app.auth.models import User
    from app.database import async_session_factory

    skill_id = "MEMBER-API-OWNER"
    owner_id = "skill-owner-user"
    async with async_session_factory() as session:
        session.add_all([
            Skill(
                id=skill_id,
                name="最后 Owner",
                department="AI小组",
                status="draft",
                visibility="private",
            ),
            User(
                id=owner_id,
                username=owner_id,
                name="Owner 用户",
                role="aibp",
                department="AI小组",
                state="active",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            ),
            SkillMember(skill_id=skill_id, user_id=owner_id, role="owner", granted_by="admin"),
        ])
        await session.commit()

    downgrade_resp = await client.post(
        f"/api/skills/{skill_id}/members",
        json={"user_id": owner_id, "role": "viewer"},
    )
    assert downgrade_resp.status_code == 400

    delete_resp = await client.delete(f"/api/skills/{skill_id}/members/{owner_id}")
    assert delete_resp.status_code == 400
