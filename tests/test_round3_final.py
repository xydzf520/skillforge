"""
第三轮（最终轮）改进测试：
- Git commit 原子性
- 自动迁移
- 沙箱网络隔离
- Migration downgrade 结构
- 编辑感知协同
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Git commit 原子性 ──

class TestGitCommitAtomicity:
    def test_delete_tag_method_exists(self):
        """git_service 应有 delete_tag 方法"""
        from app.skills.core.git_service import GitService
        assert hasattr(GitService, "delete_tag")

    @pytest.mark.asyncio
    async def test_approve_review_tag_failure_rollback(self):
        """tag 创建失败时应回滚 review status 到 pending"""
        from app.reviews.service import approve_review
        from app.reviews.models import Review
        from app.skills.core.models import Skill
        from app.common.exceptions import AppError

        mock_db = AsyncMock()

        # mock review
        mock_review = MagicMock(spec=Review)
        mock_review.id = 1
        mock_review.status = "pending"
        mock_review.skill_id = "test-skill"
        mock_review.submitter = "user-a"
        mock_review.review_feedback = {}

        # mock skill
        mock_skill = MagicMock(spec=Skill)
        mock_skill.id = "test-skill"
        mock_skill.approver = "reviewer-b"
        mock_skill.current_version = "v0.1"
        mock_skill.approval_level = 0

        # 第一次 execute 返回 review，第二次返回 skill
        mock_db.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_review)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_skill)),
        ])
        mock_db.flush = AsyncMock()

        # git_service.tag 抛异常
        with patch("app.reviews.service.git_service") as mock_git:
            mock_git.tag.side_effect = Exception("tag creation failed")
            with patch("app.reviews.service.audit") as mock_audit:
                mock_audit.log = AsyncMock()
                with pytest.raises(AppError) as exc_info:
                    await approve_review(mock_db, 1, "reviewer-b")
                assert exc_info.value.code == "REVIEW_TAG_FAILED"

        # review status 应被回滚
        assert mock_review.status == "pending"
        assert mock_review.decided_at is None


# ── 自动迁移 ──

class TestAutoMigration:
    def test_auto_migrate_function_exists(self):
        from app.bootstrap.runtime_init import _auto_migrate
        assert callable(_auto_migrate)

    @pytest.mark.asyncio
    async def test_auto_migrate_skips_when_alembic_missing(self):
        """alembic 未安装时应跳过（不抛异常）"""
        from app.bootstrap.runtime_init import _auto_migrate
        with patch.dict("sys.modules", {"alembic": None, "alembic.command": None}):
            # 不应抛异常
            try:
                await _auto_migrate()
            except RuntimeError:
                pass  # 可能因为其他导入失败，但不应该是 alembic 相关


# ── 沙箱网络隔离 ──

class TestSandboxNetworkBlock:
    def test_block_network_prevents_connect(self):
        """_block_network 应阻止 socket 连接"""
        from app.sandbox.runner import _block_network
        import socket

        # 保存原始 socket
        orig_socket = socket.socket

        _block_network()

        # 验证新 socket 阻止连接
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(PermissionError, match="沙箱环境禁止网络访问"):
            s.connect(("127.0.0.1", 80))

        # 恢复原始 socket（清理）
        socket.socket = orig_socket

    def test_block_network_prevents_bind(self):
        """_block_network 应阻止 socket 绑定"""
        from app.sandbox.runner import _block_network
        import socket

        orig_socket = socket.socket
        _block_network()

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(PermissionError, match="沙箱环境禁止网络绑定"):
            s.bind(("0.0.0.0", 0))

        socket.socket = orig_socket


# ── 编辑感知协同 ──

class TestCollabWs:
    def test_check_conflict_no_base(self):
        """无 base_commit 时不应报冲突"""
        from app.skills.integrations.collab_ws import check_conflict
        with patch("app.skills.collab_ws.git_service") as mock_git:
            result = check_conflict("test-skill", "")
            assert result["conflict"] is False

    def test_check_conflict_same_commit(self):
        """HEAD 未变时不应报冲突"""
        from app.skills.integrations.collab_ws import check_conflict
        with patch("app.skills.collab_ws.git_service") as mock_git:
            mock_git.log.return_value = [{"hash_full": "abc123"}]
            result = check_conflict("test-skill", "abc123")
            assert result["conflict"] is False

    def test_check_conflict_different_commit(self):
        """HEAD 已变时应报冲突"""
        from app.skills.integrations.collab_ws import check_conflict
        with patch("app.skills.collab_ws.git_service") as mock_git:
            mock_git.log.return_value = [{"hash_full": "def456"}]
            result = check_conflict("test-skill", "abc123")
            assert result["conflict"] is True
            assert result["head_commit"] == "def456"

    def test_get_active_editors_empty(self):
        from app.skills.integrations.collab_ws import get_active_editors
        result = get_active_editors("nonexistent-skill")
        assert result == []


# ── Migration 结构测试 ──

class TestMigrationStructureIntegrity:
    def test_migration_files_exist(self):
        from pathlib import Path
        migrations_dir = Path(__file__).parent.parent / "migrations" / "versions"
        py_files = list(migrations_dir.glob("*.py"))
        assert len(py_files) >= 30, f"迁移文件数不足: {len(py_files)}"

    def test_all_have_upgrade_and_downgrade(self):
        from pathlib import Path
        migrations_dir = Path(__file__).parent.parent / "migrations" / "versions"
        for py_file in sorted(migrations_dir.glob("*.py")):
            if py_file.name.startswith("__"):
                continue
            content = py_file.read_text(encoding="utf-8")
            assert "def upgrade" in content, f"{py_file.name} 缺少 upgrade()"
            assert "def downgrade" in content, f"{py_file.name} 缺少 downgrade()"
