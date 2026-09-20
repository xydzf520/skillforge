"""M3 动态权限扩展点测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.exceptions import AppError
from app.skills.core.access import (
    clear_skill_policy_evaluators,
    register_skill_policy_evaluator,
    require_skill_access,
)
from app.skills.members import SkillMember
from app.skills.core.models import Skill


def _mock_user(*, user_id: str = "u1", role: str = "operator"):
    user = MagicMock()
    user.id = user_id
    user.role = role
    user.can_view_all = False
    user.department = "总部"
    return user


def _mock_skill(*, visibility: str = "private"):
    skill = MagicMock(spec=Skill)
    skill.id = "skill-1"
    skill.visibility = visibility
    skill.org_unit_id = "总部"
    return skill


@pytest.mark.asyncio
async def test_policy_evaluator_can_deny_after_rbac():
    clear_skill_policy_evaluators()

    async def deny_policy(_db, _skill, _user, _action):
        return False

    register_skill_policy_evaluator(deny_policy)
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[_mock_skill(), MagicMock(spec=SkillMember, role="owner")])

    with pytest.raises(AppError) as exc_info:
        await require_skill_access(db, "skill-1", _mock_user(user_id="owner-1"), "publish")

    assert exc_info.value.code == "SKILL_ACCESS_DENIED"
    clear_skill_policy_evaluators()


@pytest.mark.asyncio
async def test_policy_evaluator_can_allow_without_breaking_access():
    clear_skill_policy_evaluators()

    async def allow_policy(_db, _skill, _user, _action):
        return True

    register_skill_policy_evaluator(allow_policy)
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[_mock_skill(), MagicMock(spec=SkillMember, role="owner")])

    skill = await require_skill_access(db, "skill-1", _mock_user(user_id="owner-1"), "publish")
    assert skill.id == "skill-1"
    clear_skill_policy_evaluators()
