"""service_files 模块单元测试。

测试 app/skills/service_files.py 中的函数：
list_scripts, run_script, save_file, create_file, delete_file,
rename_file, acquire_lock, release_lock, get_lock_status。
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

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
    db.add = MagicMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = skill
    db.execute.return_value = result_mock
    return db


# ── list_scripts ─────────────────────────────────────────────────────


@patch("app.skills.service_files.build_skill_manifest_from_dir")
@patch("app.skills.service_files.git_service")
def test_list_scripts_normal(mock_git, mock_manifest):
    """正常列出脚本"""
    from app.skills.service_files import list_scripts

    mock_git.skill_dir.return_value = Path("/repo/EC-投放-01")
    mock_manifest.return_value = {
        "scripts": [
            {"path": "scripts/main.py", "purpose": "分析投放数据"},
            {"path": "scripts/fetch.py", "purpose": "拉取API数据"},
        ]
    }
    result = list_scripts("EC-投放-01")
    assert len(result) == 2
    assert result[0]["path"] == "scripts/main.py"


@patch("app.skills.service_files.build_skill_manifest_from_dir")
@patch("app.skills.service_files.git_service")
def test_list_scripts_empty(mock_git, mock_manifest):
    """没有脚本时返回空列表"""
    from app.skills.service_files import list_scripts

    mock_git.skill_dir.return_value = Path("/repo/EC-投放-01")
    mock_manifest.return_value = {"scripts": []}
    result = list_scripts("EC-投放-01")
    assert result == []


# ── run_script ───────────────────────────────────────────────────────


@patch("app.sandbox.executor.run_script")
@patch("app.skills.service_files.git_service")
def test_run_script_normal(mock_git, mock_sandbox):
    """正常执行脚本返回结果"""
    from app.skills.service_files import run_script

    fake_path = MagicMock(spec=Path)
    fake_path.exists.return_value = True
    fake_path.__str__ = lambda self: "/repo/EC-投放-01/scripts/main.py"
    fake_path.endswith = lambda self, s: str(self).endswith(s)
    mock_git._safe_path.return_value = fake_path
    mock_sandbox.return_value = {
        "success": True, "output": {"ok": True}, "error": None, "duration_ms": 5,
    }
    result = run_script("EC-投放-01", "scripts/main.py", payload={"x": 1})
    assert result["success"] is True
    assert result["output"] == {"ok": True}


def test_run_script_invalid_path():
    """路径不以 scripts/ 开头时应抛出 PARAM_INVALID"""
    from app.skills.service_files import run_script

    with pytest.raises(AppError) as exc_info:
        run_script("EC-投放-01", "hacks/evil.py")
    assert exc_info.value.code == "PARAM_INVALID"


@patch("app.skills.service_files.git_service")
def test_run_script_file_not_found(mock_git):
    """脚本文件不存在时应抛出 404"""
    from app.skills.service_files import run_script

    fake_path = MagicMock(spec=Path)
    fake_path.exists.return_value = False
    mock_git._safe_path.return_value = fake_path
    with pytest.raises(AppError) as exc_info:
        run_script("EC-投放-01", "scripts/missing.py")
    assert exc_info.value.code == "SKILL_FILE_NOT_FOUND"


# ── save_file ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_files.audit")
@patch("app.skills.service_files.git_service")
async def test_save_file_normal(mock_git, mock_audit):
    """正常保存非 SKILL.md 文件"""
    from app.skills.service_files import save_file

    skill = _mock_skill()
    db = _mock_db(skill)
    mock_git.commit_all.return_value = "sha_save"
    mock_audit.log = AsyncMock()

    result = await save_file(db, "EC-投放-01", "policy_pack.yaml", "roi: 2.0\n", user_id="admin")
    assert result["file"] == "policy_pack.yaml"
    assert result["git_commit"] == "sha_save"
    mock_git.write_file.assert_called_once_with("EC-投放-01", "policy_pack.yaml", "roi: 2.0\n")


@pytest.mark.asyncio
@patch("app.skills.service_files.audit")
@patch("app.skills.service_files.git_service")
async def test_save_file_skill_not_found(mock_git, mock_audit):
    """Skill 不存在时应抛出 404"""
    from app.skills.service_files import save_file

    db = _mock_db(skill=None)
    with pytest.raises(AppError) as exc_info:
        await save_file(db, "NOT-EXIST", "test.yaml", "content")
    assert exc_info.value.code == "SKILL_NOT_FOUND"


@pytest.mark.asyncio
@patch("app.skills.service_files.audit")
@patch("app.skills.service_files.git_service")
@patch("app.skills.validators.quick_validate")
async def test_save_file_skill_md_validation_fail(mock_qv, mock_git, mock_audit):
    """保存 SKILL.md 时校验失败应抛出 422"""
    from app.skills.service_files import save_file

    skill = _mock_skill()
    db = _mock_db(skill)
    qv_result = MagicMock()
    qv_result.ok = False
    qv_result.to_detail.return_value = {"errors": ["缺少 name 字段"]}
    mock_qv.return_value = qv_result

    with pytest.raises(AppError) as exc_info:
        await save_file(db, "EC-投放-01", "SKILL.md", "invalid content")
    assert exc_info.value.code == "SKILL_VALIDATION_FAILED"
    assert exc_info.value.status == 422


# ── create_file ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_files.audit")
@patch("app.skills.service_files.git_service")
async def test_create_file_normal(mock_git, mock_audit):
    """正常创建新文件"""
    from app.skills.service_files import create_file

    skill = _mock_skill()
    db = _mock_db(skill)
    mock_git.commit_all.return_value = "sha_create"
    mock_audit.log = AsyncMock()

    result = await create_file(db, "EC-投放-01", "scripts/new.py", content="# new script")
    assert result["file"] == "scripts/new.py"
    mock_git.write_file.assert_called_once_with("EC-投放-01", "scripts/new.py", "# new script")


@pytest.mark.asyncio
@patch("app.skills.service_files.audit")
@patch("app.skills.service_files.git_service")
async def test_create_dir(mock_git, mock_audit):
    """创建目录时应写 .gitkeep"""
    from app.skills.service_files import create_file

    skill = _mock_skill()
    db = _mock_db(skill)
    mock_git.commit_all.return_value = "sha_dir"
    mock_audit.log = AsyncMock()

    result = await create_file(db, "EC-投放-01", "data", is_dir=True)
    mock_git.create_dir.assert_called_once_with("EC-投放-01", "data")
    mock_git.write_file.assert_called_once_with("EC-投放-01", "data/.gitkeep", "")


# ── delete_file ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_files.audit")
@patch("app.skills.service_files.git_service")
async def test_delete_file_normal(mock_git, mock_audit):
    """正常删除文件"""
    from app.skills.service_files import delete_file

    skill = _mock_skill()
    db = _mock_db(skill)
    mock_git.commit_all.return_value = "sha_del"
    mock_audit.log = AsyncMock()

    result = await delete_file(db, "EC-投放-01", "scripts/old.py", user_id="admin")
    assert result["deleted"] == "scripts/old.py"
    mock_git.delete_file.assert_called_once_with("EC-投放-01", "scripts/old.py")


# ── rename_file ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.skills.service_files.audit")
@patch("app.skills.service_files.git_service")
async def test_rename_file_normal(mock_git, mock_audit):
    """正常重命名文件"""
    from app.skills.service_files import rename_file

    skill = _mock_skill()
    db = _mock_db(skill)
    mock_git.commit_all.return_value = "sha_rename"
    mock_audit.log = AsyncMock()

    result = await rename_file(db, "EC-投放-01", "old.py", "new.py", user_id="admin")
    assert result["old_path"] == "old.py"
    assert result["new_path"] == "new.py"
    mock_git.rename_file.assert_called_once_with("EC-投放-01", "old.py", "new.py")


@pytest.mark.asyncio
@patch("app.skills.service_files.audit")
@patch("app.skills.service_files.git_service")
async def test_rename_file_skill_not_found(mock_git, mock_audit):
    """重命名不存在 Skill 的文件应抛出 404"""
    from app.skills.service_files import rename_file

    db = _mock_db(skill=None)
    with pytest.raises(AppError) as exc_info:
        await rename_file(db, "NOT-EXIST", "a.py", "b.py")
    assert exc_info.value.code == "SKILL_NOT_FOUND"


# ── acquire_lock / release_lock / get_lock_status ────────────────────


@pytest.mark.asyncio
async def test_acquire_lock_new():
    """无现有锁时应成功获取锁"""
    from app.skills.service_files import acquire_lock

    db = AsyncMock()
    db.add = MagicMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    result = await acquire_lock(db, "EC-投放-01", "user_a")
    assert result["locked_by"] == "user_a"
    db.add.assert_called_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_acquire_lock_conflict():
    """其他用户持有有效锁时应抛出 SKILL_LOCKED 423"""
    from app.skills.service_files import acquire_lock

    existing_lock = MagicMock()
    existing_lock.user_id = "user_b"
    existing_lock.locked_at = datetime.utcnow() - timedelta(minutes=5)  # 5分钟前，未过期

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing_lock
    db.execute.return_value = result_mock

    with pytest.raises(AppError) as exc_info:
        await acquire_lock(db, "EC-投放-01", "user_a")
    assert exc_info.value.code == "SKILL_LOCKED"
    assert exc_info.value.status == 423


@pytest.mark.asyncio
async def test_acquire_lock_expired():
    """锁已过期（超过30分钟）时应允许覆盖"""
    from app.skills.service_files import acquire_lock

    existing_lock = MagicMock()
    existing_lock.user_id = "user_b"
    existing_lock.locked_at = datetime.utcnow() - timedelta(minutes=40)  # 40分钟前，已过期

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing_lock
    db.execute.return_value = result_mock

    result = await acquire_lock(db, "EC-投放-01", "user_a")
    assert result["locked_by"] == "user_a"
    assert existing_lock.user_id == "user_a"  # 覆盖了原锁


@pytest.mark.asyncio
async def test_release_lock_own():
    """释放自己持有的锁"""
    from app.skills.service_files import release_lock

    existing_lock = MagicMock()
    existing_lock.user_id = "user_a"

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing_lock
    db.execute.return_value = result_mock

    result = await release_lock(db, "EC-投放-01", "user_a")
    assert result["unlocked"] is True
    db.delete.assert_awaited_once_with(existing_lock)


@pytest.mark.asyncio
async def test_release_lock_others():
    """不能释放其他用户的锁（静默忽略）"""
    from app.skills.service_files import release_lock

    existing_lock = MagicMock()
    existing_lock.user_id = "user_b"

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing_lock
    db.execute.return_value = result_mock

    result = await release_lock(db, "EC-投放-01", "user_a")
    assert result["unlocked"] is True
    db.delete.assert_not_awaited()  # 不应删除别人的锁


@pytest.mark.asyncio
async def test_get_lock_status_active():
    """查询活跃锁状态"""
    from app.skills.service_files import get_lock_status

    lock = MagicMock()
    lock.user_id = "user_a"
    lock.locked_at = datetime.utcnow() - timedelta(minutes=10)

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = lock
    db.execute.return_value = result_mock

    result = await get_lock_status(db, "EC-投放-01")
    assert result is not None
    assert result["locked_by"] == "user_a"
    assert result["expires_in_seconds"] > 0


@pytest.mark.asyncio
async def test_get_lock_status_expired():
    """过期锁应自动清除并返回 None"""
    from app.skills.service_files import get_lock_status

    lock = MagicMock()
    lock.user_id = "user_a"
    lock.locked_at = datetime.utcnow() - timedelta(minutes=40)  # 已过期

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = lock
    db.execute.return_value = result_mock

    result = await get_lock_status(db, "EC-投放-01")
    assert result is None
    db.delete.assert_awaited_once_with(lock)


@pytest.mark.asyncio
async def test_get_lock_status_none():
    """无锁时返回 None"""
    from app.skills.service_files import get_lock_status

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    result = await get_lock_status(db, "EC-投放-01")
    assert result is None
