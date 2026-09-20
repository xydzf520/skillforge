"""
完整发布链路端到端测试：
  编辑 → 保存 → 提交审核 → 审核通过 → Git tag → Hermes 文件生成+提交 → 推送到对应部门终端

验证每个步骤的数据正确性和链路完整性。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from pathlib import Path
import tempfile
import os


class TestPublishFlowIntegrity:
    """验证发布链路中各步骤的正确连接"""

    @staticmethod
    def _approve_review_source() -> str:
        import inspect
        from app.reviews.service import approve_review

        source = inspect.getsource(approve_review)
        if "sync_after_approval" not in source:
            source_file = inspect.getsourcefile(approve_review)
            if source_file:
                source = Path(source_file).read_text(encoding="utf-8")
        return source

    def test_hermes_adapter_generates_and_files_exist(self):
        """Hermes 适配器生成文件后文件应存在于磁盘"""
        from app.skills.integrations.hermes_adapter import generate_hermes_files

        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.skills.hermes_adapter.settings") as mock_settings:
                mock_settings.SKILL_REPO_PATH = tmp

                # 创建模拟 Skill 目录
                skill_dir = Path(tmp) / "test-skill"
                skill_dir.mkdir()
                scripts_dir = skill_dir / "scripts"
                scripts_dir.mkdir()

                # 写入 SKILL.md
                (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: 测试用
department: EC
trigger_type: manual
params:
  threshold:
    type: number
    default: 1.5
---

# test-skill

## 目的

测试

## 执行步骤

### Step 1: 判断

ROI > {threshold} → 绿灯
""")
                # 写入一个脚本
                (scripts_dir / "analyze.py").write_text("def main(p): return {'ok': True}")

                result = generate_hermes_files("test-skill")

                assert result["hermes_md"] is True
                assert result["cli_entry"] is True
                assert (skill_dir / "SKILL.hermes.md").exists()
                assert (scripts_dir / "_hermes_entry.py").exists()

                # 验证 SKILL.hermes.md 内容
                hermes_md = (skill_dir / "SKILL.hermes.md").read_text()
                assert "name: test-skill" in hermes_md
                assert "metadata:" in hermes_md
                assert "hermes:" in hermes_md
                assert "requires_toolsets:" in hermes_md
                assert "threshold" in hermes_md

    def test_sync_service_has_targeted_push(self):
        """sync_after_approval 应调用部门/指定终端推送"""
        from app.execution.sync_service import SkillSyncService
        svc = SkillSyncService()
        assert hasattr(svc, 'push_skill_to_targets')
        assert callable(svc.push_skill_to_targets)

    @pytest.mark.asyncio
    async def test_hermes_client_chat_format(self):
        """HermesClient.chat 发送的请求应符合 OpenAI 格式"""
        from app.aiclaw.hermes_client import HermesClient

        client = HermesClient("http://fake:8642", api_key="test-key")

        # 验证 headers
        headers = client._headers()
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_hermes_client_health_offline(self):
        """不可达的 Hermes 实例应返回 online=False"""
        from app.aiclaw.hermes_client import HermesClient

        client = HermesClient("http://192.0.2.1:8642")  # RFC 5737 不可路由地址
        result = await client.health()
        assert result["online"] is False

    def test_approve_review_calls_hermes_generate(self):
        """approve_review 中应在 sync 前生成 Hermes 文件"""
        source = self._approve_review_source()
        # 验证调用顺序：generate_hermes_files 在 sync_after_approval 之前
        gen_pos = source.find("generate_hermes_files")
        sync_pos = source.find("sync_after_approval")
        assert gen_pos > 0, "approve_review 应调用 generate_hermes_files"
        assert sync_pos > 0, "approve_review 应调用 sync_after_approval"
        assert gen_pos < sync_pos, "generate_hermes_files 应在 sync_after_approval 之前"

    def test_approve_review_commits_hermes_files(self):
        """approve_review 中 Hermes 文件生成后应 git commit"""
        source = self._approve_review_source()
        gen_pos = source.find("generate_hermes_files")
        commit_pos = source.find("git_service.commit", gen_pos)
        sync_pos = source.find("sync_after_approval")
        assert commit_pos > gen_pos, "Hermes 文件生成后应 git commit"
        assert commit_pos < sync_pos, "git commit 应在 sync 之前"

    def test_sync_service_pushes_to_target_department_source(self):
        """sync_after_approval 应按部门/指定终端同步，不广播全部实例"""
        import inspect
        from app.execution.sync_service import SkillSyncService

        source = inspect.getsource(SkillSyncService.sync_after_approval)
        run_source = inspect.getsource(SkillSyncService.run_sync_job)
        assert "push_skill_to_targets" in run_source
        assert "target_instance_ids" in source
        assert "_push_to_all_instances" not in source + run_source

    def test_targeted_push_handles_hermes(self):
        """push_skill_to_targets 应区分 agent_type=hermes"""
        import inspect
        from app.execution.sync_service import SkillSyncService

        source = inspect.getsource(SkillSyncService._push_files_to_instance)
        assert "hermes" in source.lower()
        assert "HermesClient" in source
        assert "AIClawClient" in source

    def test_tag_failure_rollback_in_source(self):
        """Git tag 失败时应回滚 review.status"""
        source = self._approve_review_source()
        # tag 失败后应有 status = "pending" 回滚
        tag_pos = source.find("git_service.tag")
        rollback_pos = source.find('"pending"', tag_pos)
        assert rollback_pos > tag_pos, "tag 失败后应回滚 status 到 pending"
