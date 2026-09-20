"""service_runtime 模块单元测试。

测试 app/skills/service_runtime.py 中的 6 个函数：
get_skill_history, get_skill_diff, update_params,
deprecate_skill, delete_skill, rollback_skill。
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.exceptions import AppError


# ── helpers ──────────────────────────────────────────────────────────

def _mock_skill(skill_id="EC-投放-01", status="active", git_commit="aaa111"):
    """构造一个可被 SQLAlchemy result.scalar_one_or_none() 返回的假 Skill 对象"""
    skill = MagicMock()
    skill.id = skill_id
    skill.status = status
    skill.git_commit = git_commit
    skill.updated_at = datetime.utcnow()
    return skill


def _mock_db(skill=None):
    """构造 AsyncMock 的 db session，execute 返回 scalar_one_or_none = skill"""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = skill
    db.execute.return_value = result_mock
    return db


# ── get_skill_history ────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_runtime.git_service")
async def test_get_skill_history_normal(mock_git):
    """正常获取 Skill 提交历史"""
    from app.skills.lifecycle.service_runtime import get_skill_history

    mock_git.log.return_value = [
        {"sha": "abc123", "message": "初始化", "date": "2026-01-01"},
        {"sha": "def456", "message": "更新参数", "date": "2026-01-02"},
    ]
    result = await get_skill_history("EC-投放-01", max_count=10)
    assert len(result) == 2
    mock_git.log.assert_called_once_with(skill_id="EC-投放-01", max_count=10)


@pytest.mark.asyncio
async def test_get_skill_history_invalid_id():
    """非法 Skill ID 应该抛出 SKILL_ID_INVALID"""
    from app.skills.lifecycle.service_runtime import get_skill_history

    with pytest.raises(AppError) as exc_info:
        await get_skill_history("../../etc/passwd")
    assert exc_info.value.code == "SKILL_ID_INVALID"


# ── get_skill_diff ───────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_runtime.git_service")
async def test_get_skill_diff_normal(mock_git):
    """正常获取两次提交之间的 diff"""
    from app.skills.lifecycle.service_runtime import get_skill_diff

    mock_git.diff.return_value = "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-old\n+new"
    result = await get_skill_diff("EC-投放-01", "abc123", "def456")
    assert "+new" in result
    mock_git.diff.assert_called_once_with(skill_id="EC-投放-01", commit_a="abc123", commit_b="def456")


@pytest.mark.asyncio
@patch("app.skills.service_runtime.git_service")
async def test_get_skill_diff_default_commits(mock_git):
    """不传 commit 参数时使用默认 HEAD~1 和 HEAD"""
    from app.skills.lifecycle.service_runtime import get_skill_diff

    mock_git.diff.return_value = ""
    await get_skill_diff("EC-投放-01")
    mock_git.diff.assert_called_once_with(skill_id="EC-投放-01", commit_a="HEAD~1", commit_b="HEAD")


# ── update_params ────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_runtime.audit")
@patch("app.skills.service_runtime.git_service")
async def test_update_params_normal(mock_git, mock_audit):
    """正常更新参数：合并到 policy_pack.yaml 并 commit"""
    from app.skills.lifecycle.service_runtime import update_params

    skill = _mock_skill()
    db = _mock_db(skill)
    mock_git.read_file.return_value = "roi_green: 1.5\nroi_red: 0.8\n"
    mock_git.commit_all.return_value = "new_sha_123"
    mock_audit.log = AsyncMock()

    result = await update_params(db, "EC-投放-01", {"roi_green": 2.0}, user_id="admin")
    assert result["skill_id"] == "EC-投放-01"
    assert result["git_commit"] == "new_sha_123"
    assert result["params"]["roi_green"] == 2.0
    # 原有参数保留
    assert result["params"]["roi_red"] == 0.8
    mock_git.write_file.assert_called_once()
    mock_audit.log.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.skills.service_runtime.audit")
@patch("app.skills.service_runtime.git_service")
async def test_update_params_skill_not_found(mock_git, mock_audit):
    """Skill 不存在时应抛出 404"""
    from app.skills.lifecycle.service_runtime import update_params

    db = _mock_db(skill=None)
    with pytest.raises(AppError) as exc_info:
        await update_params(db, "NOT-EXIST", {"roi": 1.0})
    assert exc_info.value.code == "SKILL_NOT_FOUND"
    assert exc_info.value.status == 404


# ── deprecate_skill ──────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_runtime.audit")
async def test_deprecate_skill_normal(mock_audit):
    """正常废弃 Skill：status 变为 deprecated"""
    from app.skills.lifecycle.service_runtime import deprecate_skill

    skill = _mock_skill(status="active")
    db = _mock_db(skill)
    mock_audit.log = AsyncMock()

    result = await deprecate_skill(db, "EC-投放-01", "admin")
    assert result["status"] == "deprecated"
    assert skill.status == "deprecated"
    mock_audit.log.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.skills.service_runtime.audit")
async def test_deprecate_skill_not_found(mock_audit):
    """废弃不存在的 Skill 应抛出 404"""
    from app.skills.lifecycle.service_runtime import deprecate_skill

    db = _mock_db(skill=None)
    with pytest.raises(AppError) as exc_info:
        await deprecate_skill(db, "NOT-EXIST", "admin")
    assert exc_info.value.code == "SKILL_NOT_FOUND"


# ── delete_skill ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_runtime.audit")
@patch("app.skills.service_runtime.git_service")
async def test_delete_skill_normal(mock_git, mock_audit):
    """正常删除 Skill：级联清理关联表 + 删除 DB 记录"""
    from app.skills.lifecycle.service_runtime import delete_skill
    from pathlib import Path

    skill = _mock_skill()
    db = _mock_db(skill)
    mock_audit.log = AsyncMock()
    # skill_dir 返回一个不存在的路径（跳过 rename）
    mock_git.skill_dir.return_value = Path("/tmp/nonexistent-test-dir")

    result = await delete_skill(db, "EC-投放-01", "admin")
    assert result["deleted"] is True
    db.delete.assert_awaited_once_with(skill)


@pytest.mark.asyncio
@patch("app.skills.service_runtime.audit")
@patch("app.skills.service_runtime.git_service")
async def test_delete_skill_not_found(mock_git, mock_audit):
    """删除不存在的 Skill 应抛出 404"""
    from app.skills.lifecycle.service_runtime import delete_skill

    db = _mock_db(skill=None)
    with pytest.raises(AppError) as exc_info:
        await delete_skill(db, "NOT-EXIST", "admin")
    assert exc_info.value.code == "SKILL_NOT_FOUND"


@pytest.mark.asyncio
@patch("app.skills.service_runtime.audit")
@patch("app.skills.service_runtime.git_service")
async def test_delete_skill_cascade_cleanup(mock_git, mock_audit):
    """delete_skill 应级联清理关联表（通过 sa_delete 调用确认）"""
    from app.skills.lifecycle.service_runtime import delete_skill
    from pathlib import Path

    skill = _mock_skill()
    db = _mock_db(skill)
    mock_audit.log = AsyncMock()
    mock_git.skill_dir.return_value = Path("/tmp/nonexistent-test-dir")

    result = await delete_skill(db, "EC-投放-01", "admin")
    assert result["deleted"] is True
    # 确认 execute 被多次调用（级联清理多个关联表）
    assert db.execute.await_count >= 3


# ── rollback_skill ───────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_runtime.audit")
@patch("app.skills.service_runtime.git_service")
async def test_rollback_skill_normal(mock_git, mock_audit):
    """正常回滚 Skill 到指定提交"""
    from app.skills.lifecycle.service_runtime import rollback_skill

    skill = _mock_skill(status="active")
    db = _mock_db(skill)
    mock_git.get_file_at_commit.return_value = "# Old SKILL.md content"
    mock_git.list_files_at_commit.return_value = ["EC-投放-01/SKILL.md"]
    mock_git.repo = MagicMock()
    mock_git.commit_all.return_value = "rollback_sha"
    mock_audit.log = AsyncMock()

    result = await rollback_skill(db, "EC-投放-01", "abc12345", "admin")
    assert result["status"] == "draft"
    assert result["rolled_back_to"] == "abc12345"[:8]
    assert skill.status == "draft"


@pytest.mark.asyncio
@patch("app.skills.service_runtime.audit")
@patch("app.skills.service_runtime.git_service")
async def test_rollback_skill_not_found(mock_git, mock_audit):
    """回滚不存在的 Skill 应抛出 404"""
    from app.skills.lifecycle.service_runtime import rollback_skill

    db = _mock_db(skill=None)
    with pytest.raises(AppError) as exc_info:
        await rollback_skill(db, "NOT-EXIST", "abc123", "admin")
    assert exc_info.value.code == "SKILL_NOT_FOUND"


@pytest.mark.asyncio
@patch("app.skills.service_runtime.audit")
@patch("app.skills.service_runtime.git_service")
async def test_rollback_skill_target_missing(mock_git, mock_audit):
    """目标提交不包含 SKILL.md 时应抛出 GIT_OPERATION_FAILED"""
    from app.skills.lifecycle.service_runtime import rollback_skill

    skill = _mock_skill()
    db = _mock_db(skill)
    mock_git.get_file_at_commit.return_value = None

    with pytest.raises(AppError) as exc_info:
        await rollback_skill(db, "EC-投放-01", "bad_commit", "admin")
    assert exc_info.value.code == "GIT_OPERATION_FAILED"
    assert exc_info.value.status == 400
