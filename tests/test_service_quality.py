"""service_quality 模块单元测试。

测试 app/skills/service_quality.py 中的 5 个函数：
save_skill_structured, user_can_view_all_departments,
get_param_usages_grouped, get_cross_skill_conflicts_scoped,
get_skill_conflicts_with_access。
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.exceptions import AppError


# ── helpers ──────────────────────────────────────────────────────────


def _mock_skill(skill_id="EC-投放-01", status="active", git_commit="aaa111"):
    skill = MagicMock()
    skill.id = skill_id
    skill.status = status
    skill.git_commit = git_commit
    skill.updated_at = datetime.utcnow()
    return skill


def _mock_db(skill=None):
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = skill
    db.execute.return_value = result_mock
    return db


def _mock_user(role="admin", department="EC", can_view_all=True):
    user = MagicMock()
    user.role = role
    user.department = department
    user.can_view_all = can_view_all
    return user


def _mock_parsed():
    """构造 skill_parser.parse 的返回值"""
    parsed = MagicMock()
    parsed.frontmatter = {"name": "投放决策", "department": "EC"}
    parsed.purpose = "测试目的"
    parsed.steps = []
    parsed.antipatterns = []
    parsed.output_definition = []
    parsed.data_inputs = []
    parsed.test_cases = []
    parsed.custom_sections = {}
    return parsed


# ── user_can_view_all_departments ────────────────────────────────────


def test_can_view_all_admin():
    """admin 角色可以查看全部部门"""
    from app.skills.tooling.service_quality import user_can_view_all_departments

    user = _mock_user(role="admin", can_view_all=False)
    assert user_can_view_all_departments(user) is True


def test_can_view_all_with_flag():
    """can_view_all=True 的用户可以查看全部部门"""
    from app.skills.tooling.service_quality import user_can_view_all_departments

    user = _mock_user(role="operator", can_view_all=True)
    assert user_can_view_all_departments(user) is True


def test_cannot_view_all_normal_user():
    """普通 operator 不能查看全部部门"""
    from app.skills.tooling.service_quality import user_can_view_all_departments

    user = _mock_user(role="operator", can_view_all=False)
    assert user_can_view_all_departments(user) is False


def test_can_view_all_none_user():
    """None 用户返回 False"""
    from app.skills.tooling.service_quality import user_can_view_all_departments

    assert user_can_view_all_departments(None) is False


def test_can_view_all_ai_engineer():
    """ai_engineer 角色可以查看全部部门"""
    from app.skills.tooling.service_quality import user_can_view_all_departments

    user = _mock_user(role="ai_engineer", can_view_all=False)
    assert user_can_view_all_departments(user) is True


# ── save_skill_structured ────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_quality.audit")
@patch("app.skills.service_quality.skill_parser")
@patch("app.skills.service_quality.git_service")
async def test_save_structured_with_changes(mock_git, mock_parser, mock_audit):
    """有 SKILL.md 变更时应写文件并 commit"""
    from app.skills.tooling.service_quality import save_skill_structured

    skill = _mock_skill()
    db = _mock_db(skill)
    parsed = _mock_parsed()
    mock_git.read_file.return_value = "# old content"
    mock_parser.parse.return_value = parsed
    mock_parser.render.return_value = "# new content"
    mock_git.commit_all.return_value = "new_sha_456"
    mock_audit.log = AsyncMock()

    result = await save_skill_structured(db, "EC-投放-01", purpose="新目的", user_id="admin")
    assert result["skill_md_changed"] is True
    assert result["noop"] is False
    assert result["git_commit"] == "new_sha_456"
    mock_git.write_file.assert_called_once()
    mock_audit.log.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.skills.service_quality.audit")
@patch("app.skills.service_quality.skill_parser")
@patch("app.skills.service_quality.git_service")
async def test_save_structured_noop(mock_git, mock_parser, mock_audit):
    """无变更时返回 noop=True，不写 Git"""
    from app.skills.tooling.service_quality import save_skill_structured

    skill = _mock_skill()
    db = _mock_db(skill)
    parsed = _mock_parsed()
    old_content = "# unchanged"
    mock_git.read_file.return_value = old_content
    mock_parser.parse.return_value = parsed
    mock_parser.render.return_value = old_content  # 渲染后与原文相同
    mock_audit.log = AsyncMock()

    result = await save_skill_structured(db, "EC-投放-01", user_id="admin")
    assert result["noop"] is True
    assert result["skill_md_changed"] is False
    mock_git.commit_all.assert_not_called()


@pytest.mark.asyncio
@patch("app.skills.service_quality.audit")
@patch("app.skills.service_quality.skill_parser")
@patch("app.skills.service_quality.git_service")
async def test_save_structured_skill_not_found(mock_git, mock_parser, mock_audit):
    """Skill 不存在时应抛出 404"""
    from app.skills.tooling.service_quality import save_skill_structured

    db = _mock_db(skill=None)
    with pytest.raises(AppError) as exc_info:
        await save_skill_structured(db, "NOT-EXIST", user_id="admin")
    assert exc_info.value.code == "SKILL_NOT_FOUND"


@pytest.mark.asyncio
@patch("app.skills.service_quality.audit")
@patch("app.skills.service_quality.skill_parser")
@patch("app.skills.service_quality.git_service")
async def test_save_structured_with_policy_pack(mock_git, mock_parser, mock_audit):
    """同时更新 policy_pack.yaml 和 SKILL.md"""
    from app.skills.tooling.service_quality import save_skill_structured

    skill = _mock_skill()
    db = _mock_db(skill)
    parsed = _mock_parsed()
    mock_git.read_file.side_effect = lambda sid, path: (
        "# old content" if path == "SKILL.md" else "roi: 1.5\n"
    )
    mock_parser.parse.return_value = parsed
    mock_parser.render.return_value = "# new content"
    mock_git.commit_all.return_value = "sha_789"
    mock_audit.log = AsyncMock()

    result = await save_skill_structured(
        db, "EC-投放-01",
        purpose="新目的",
        policy_pack={"roi": 2.0},
        user_id="admin",
    )
    assert result["skill_md_changed"] is True
    assert result["policy_pack_changed"] is True
    # write_file 应被调用两次：SKILL.md + policy_pack.yaml
    assert mock_git.write_file.call_count == 2


# ── get_param_usages_grouped ─────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_quality.user_can_view_all_departments", return_value=True)
@patch("app.skills.param_index.find_param_usages")
async def test_param_usages_grouped_admin(mock_find, mock_view_all):
    """admin 可以看到所有部门的参数使用"""
    from app.skills.tooling.service_quality import get_param_usages_grouped

    mock_find.return_value = [
        {
            "skill_id": "EC-投放-01", "skill_name": "投放", "department": "EC",
            "step_id": "step_1", "step_name": "判断ROI", "branch_index": 0,
            "condition": "roi > {roi_green}", "conclusion": "绿灯",
            "action": "加预算", "kind": "condition",
        },
        {
            "skill_id": "EC-投放-02", "skill_name": "推广", "department": "MKT",
            "step_id": "step_1", "step_name": "判断CPA", "branch_index": 0,
            "condition": "cpa < {roi_green}", "conclusion": "通过",
            "action": "继续", "kind": "condition",
        },
    ]
    db = AsyncMock()
    user = _mock_user(role="admin")
    result = await get_param_usages_grouped(db, "roi_green", current_user=user)
    assert result["total_usages"] == 2
    assert result["skills_count"] == 2


@pytest.mark.asyncio
@patch("app.skills.service_quality.user_can_view_all_departments", return_value=False)
@patch("app.skills.param_index.find_param_usages")
async def test_param_usages_grouped_filtered(mock_find, mock_view_all):
    """普通用户只能看到本部门的参数使用"""
    from app.skills.tooling.service_quality import get_param_usages_grouped

    mock_find.return_value = [
        {
            "skill_id": "EC-投放-01", "skill_name": "投放", "department": "EC",
            "step_id": "step_1", "step_name": "判断ROI", "branch_index": 0,
            "condition": "roi > {roi_green}", "conclusion": "绿灯",
            "action": "加预算", "kind": "condition",
        },
        {
            "skill_id": "MKT-推广-01", "skill_name": "推广", "department": "MKT",
            "step_id": "step_1", "step_name": "判断CPA", "branch_index": 0,
            "condition": "cpa < {roi_green}", "conclusion": "通过",
            "action": "继续", "kind": "condition",
        },
    ]
    db = AsyncMock()
    user = _mock_user(role="operator", department="EC", can_view_all=False)
    result = await get_param_usages_grouped(db, "roi_green", current_user=user)
    # 应该过滤掉 MKT 部门
    assert result["total_usages"] == 1
    assert result["skills_count"] == 1


# ── get_cross_skill_conflicts_scoped ─────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_quality.user_can_view_all_departments", return_value=True)
@patch("app.skills.cross_skill_conflict.get_conflicts")
async def test_cross_skill_conflicts_admin(mock_conflicts, mock_view_all):
    """admin 查看跨 Skill 冲突（不限部门）"""
    from app.skills.tooling.service_quality import get_cross_skill_conflicts_scoped

    mock_conflicts.return_value = [{"type": "threshold_conflict", "skills": ["A", "B"]}]
    db = AsyncMock()
    user = _mock_user(role="admin")
    result = await get_cross_skill_conflicts_scoped(db, current_user=user)
    assert result["total"] == 1


@pytest.mark.asyncio
@patch("app.skills.service_quality.user_can_view_all_departments", return_value=False)
@patch("app.skills.cross_skill_conflict.get_conflicts")
async def test_cross_skill_conflicts_scoped_to_dept(mock_conflicts, mock_view_all):
    """普通用户自动限定到本部门"""
    from app.skills.tooling.service_quality import get_cross_skill_conflicts_scoped

    mock_conflicts.return_value = []
    db = AsyncMock()
    user = _mock_user(role="operator", department="EC", can_view_all=False)
    result = await get_cross_skill_conflicts_scoped(db, current_user=user)
    assert result["department"] == "EC"
    mock_conflicts.assert_awaited_once_with(db, department="EC")


# ── get_skill_conflicts_with_access ──────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.cross_skill_conflict.conflicts_for_skill")
@patch("app.skills.service.get_skill")
@patch("app.auth.dependencies.require_department_access", return_value=True)
async def test_skill_conflicts_with_access_ok(mock_dept, mock_get_skill, mock_conflicts):
    """有部门访问权限时正常返回冲突列表"""
    from app.skills.tooling.service_quality import get_skill_conflicts_with_access

    mock_get_skill.return_value = {"department": "EC"}
    mock_conflicts.return_value = [{"conflict": "test"}]
    db = AsyncMock()
    user = _mock_user(role="admin")
    result = await get_skill_conflicts_with_access(db, "EC-投放-01", current_user=user)
    assert len(result) == 1


@pytest.mark.asyncio
@patch("app.skills.service_shared.ensure_skill_access", new_callable=AsyncMock,
       side_effect=AppError("SKILL_ACCESS_DENIED", 403))
async def test_skill_conflicts_access_denied(mock_access):
    """无部门访问权限时应抛出 403"""
    from app.skills.tooling.service_quality import get_skill_conflicts_with_access

    db = AsyncMock()
    user = _mock_user(role="operator", department="EC", can_view_all=False)
    with pytest.raises(AppError) as exc_info:
        await get_skill_conflicts_with_access(db, "MKT-Skill-01", current_user=user)
    assert exc_info.value.code == "SKILL_ACCESS_DENIED"
    assert exc_info.value.status == 403
