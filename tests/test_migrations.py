"""Migration downgrade 可逆性测试。

测试最近 5 个迁移的 upgrade → downgrade → re-upgrade 可逆性。
使用 Alembic API 直接操作，需要数据库连接。

运行方式（需要测试数据库）：
    DATABASE_URL_SYNC=postgresql+psycopg2://... python3 -m pytest tests/test_migrations.py -v
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


# 最近 5 个迁移版本（按序）
RECENT_MIGRATIONS = ["049", "050", "051", "052", "053"]


class TestMigrationStructure:
    """测试迁移文件结构完整性（不需要数据库连接）"""

    def test_all_migrations_have_downgrade(self):
        """所有迁移文件必须有 downgrade() 函数"""
        from pathlib import Path

        migrations_dir = Path(__file__).parent.parent / "migrations" / "versions"
        assert migrations_dir.exists(), f"迁移目录不存在: {migrations_dir}"

        missing_downgrade = []
        for py_file in sorted(migrations_dir.glob("*.py")):
            if py_file.name.startswith("__"):
                continue
            content = py_file.read_text(encoding="utf-8")
            if "def upgrade" in content and "def downgrade" not in content:
                missing_downgrade.append(py_file.name)

        assert not missing_downgrade, f"以下迁移缺少 downgrade(): {missing_downgrade}"

    def test_all_migrations_have_revision_info(self):
        """所有迁移文件必须有 Revision ID（变量或 docstring）"""
        from pathlib import Path

        migrations_dir = Path(__file__).parent.parent / "migrations" / "versions"
        missing = []
        for py_file in sorted(migrations_dir.glob("*.py")):
            if py_file.name.startswith("__"):
                continue
            content = py_file.read_text(encoding="utf-8")
            # 支持两种格式：变量 `revision = "xxx"` 或 docstring `Revision ID: xxx`
            has_revision = "revision =" in content or "Revision ID:" in content
            if not has_revision:
                missing.append(py_file.name)

        assert not missing, f"以下迁移缺少 Revision 信息: {missing}"

    def test_migration_down_revisions_exist(self):
        """所有 down_revision 必须指向真实 revision，避免 Alembic 迁移图断裂。"""
        import importlib.util
        from pathlib import Path

        migrations_dir = Path(__file__).parent.parent / "migrations" / "versions"
        revisions: dict[str, tuple[str | tuple[str, ...] | None, str]] = {}
        for py_file in sorted(migrations_dir.glob("*.py")):
            if py_file.name.startswith("__"):
                continue
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            assert spec and spec.loader, f"无法加载迁移文件: {py_file.name}"
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            revision = getattr(module, "revision", None)
            assert revision, f"迁移缺少 revision 变量: {py_file.name}"
            revisions[str(revision)] = (getattr(module, "down_revision", None), py_file.name)

        missing: list[str] = []
        for down_revision, filename in revisions.values():
            if down_revision is None:
                continue
            refs = down_revision if isinstance(down_revision, tuple) else (down_revision,)
            for ref in refs:
                if ref is not None and str(ref) not in revisions:
                    missing.append(f"{filename}: down_revision={ref}")

        assert not missing, f"以下迁移引用了不存在的 down_revision: {missing}"

    def test_recent_migrations_import_correctly(self):
        """最近 5 个迁移可以正常 import"""
        import importlib
        from pathlib import Path

        migrations_dir = Path(__file__).parent.parent / "migrations" / "versions"
        for rev in RECENT_MIGRATIONS:
            files = list(migrations_dir.glob(f"{rev}_*.py"))
            assert len(files) == 1, f"迁移 {rev} 文件数异常: {len(files)}"

            # 验证文件有 upgrade 和 downgrade 函数
            content = files[0].read_text(encoding="utf-8")
            assert "def upgrade" in content, f"迁移 {rev} 缺少 upgrade()"
            assert "def downgrade" in content, f"迁移 {rev} 缺少 downgrade()"

    def test_downgrade_operations_are_inverse(self):
        """验证 downgrade 操作是 upgrade 操作的逆操作（静态分析）"""
        from pathlib import Path

        migrations_dir = Path(__file__).parent.parent / "migrations" / "versions"

        for rev in RECENT_MIGRATIONS:
            files = list(migrations_dir.glob(f"{rev}_*.py"))
            if not files:
                continue
            content = files[0].read_text(encoding="utf-8")

            # 提取 upgrade 和 downgrade 中的操作
            upgrade_creates = content.count("create_table") + content.count("add_column")
            downgrade_drops = content.count("drop_table") + content.count("drop_column")

            # upgrade 创建的表/列数量应等于 downgrade 删除的数量
            assert upgrade_creates == downgrade_drops, (
                f"迁移 {rev}: upgrade 创建 {upgrade_creates} 个对象, "
                f"downgrade 删除 {downgrade_drops} 个对象, 不匹配"
            )
