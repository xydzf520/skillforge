"""PromptRegistry 单元测试。

覆盖：注册、加载目录、build 渲染、版本切换、feature gate、hash 计算、错误处理。
"""

import pytest
from pathlib import Path

from app.common.prompt_registry import (
    PromptRegistry,
    PromptSection,
    prompt_registry as global_registry,
)


@pytest.fixture
def fresh_registry():
    """每个测试拿到一个干净的 PromptRegistry 实例"""
    return PromptRegistry()


# ===== PromptSection =====

class TestPromptSection:
    def test_hash_is_12_chars(self):
        s = PromptSection(name="foo", content="hello", version="v1")
        assert len(s.hash) == 12
        assert all(c in "0123456789abcdef" for c in s.hash)

    def test_hash_changes_with_content(self):
        a = PromptSection(name="foo", content="hello", version="v1")
        b = PromptSection(name="foo", content="world", version="v1")
        assert a.hash != b.hash

    def test_hash_changes_with_version(self):
        a = PromptSection(name="foo", content="hello", version="v1")
        b = PromptSection(name="foo", content="hello", version="v2")
        assert a.hash != b.hash

    def test_hash_changes_with_name(self):
        a = PromptSection(name="foo", content="hello", version="v1")
        b = PromptSection(name="bar", content="hello", version="v1")
        assert a.hash != b.hash

    def test_hash_is_stable(self):
        a = PromptSection(name="foo", content="hello", version="v1")
        b = PromptSection(name="foo", content="hello", version="v1")
        assert a.hash == b.hash


# ===== register =====

class TestRegister:
    def test_register_first_sets_default(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="x", version="v1"))
        assert fresh_registry._default_versions["foo"] == "v1"

    def test_register_second_version_keeps_default(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="x", version="v1"))
        fresh_registry.register(PromptSection(name="foo", content="y", version="v2"))
        assert fresh_registry._default_versions["foo"] == "v1"
        assert len(fresh_registry._sections["foo"]) == 2

    def test_register_same_version_overwrites(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="x", version="v1"))
        fresh_registry.register(PromptSection(name="foo", content="y", version="v1"))
        assert len(fresh_registry._sections["foo"]) == 1
        assert fresh_registry._sections["foo"][0].content == "y"


# ===== build =====

class TestBuild:
    def test_build_basic(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="hello {name}", version="v1"))
        rendered, h = fresh_registry.build("foo", {"name": "world"})
        assert rendered == "hello world"
        assert len(h) == 12

    def test_build_default_version(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="v1 content", version="v1"))
        fresh_registry.register(PromptSection(name="foo", content="v2 content", version="v2"))
        rendered, _ = fresh_registry.build("foo")
        assert rendered == "v1 content"  # 默认是 v1

    def test_build_explicit_version(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="v1 content", version="v1"))
        fresh_registry.register(PromptSection(name="foo", content="v2 content", version="v2"))
        rendered, _ = fresh_registry.build("foo", version="v2")
        assert rendered == "v2 content"

    def test_build_after_set_default(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="v1 content", version="v1"))
        fresh_registry.register(PromptSection(name="foo", content="v2 content", version="v2"))
        fresh_registry.set_default("foo", "v2")
        rendered, _ = fresh_registry.build("foo")
        assert rendered == "v2 content"

    def test_build_unknown_name_raises(self, fresh_registry):
        with pytest.raises(KeyError, match="未注册"):
            fresh_registry.build("nonexistent")

    def test_build_unknown_version_raises(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="x", version="v1"))
        with pytest.raises(KeyError, match="版本未找到"):
            fresh_registry.build("foo", version="v99")

    def test_build_missing_placeholder_falls_back_to_raw(self, fresh_registry):
        fresh_registry.register(PromptSection(
            name="foo", content="hello {missing}", version="v1"
        ))
        # 没传 missing context，应回退原文而非崩溃
        rendered, _ = fresh_registry.build("foo", {})
        assert "hello {missing}" in rendered or rendered == "hello {missing}"

    def test_build_returns_correct_hash(self, fresh_registry):
        section = PromptSection(name="foo", content="hello {name}", version="v1")
        fresh_registry.register(section)
        _, h = fresh_registry.build("foo", {"name": "x"})
        assert h == section.hash


# ===== feature gate =====

class TestFeatureGate:
    def test_gate_passes_when_returns_true(self, fresh_registry):
        fresh_registry.register(PromptSection(
            name="foo", content="v2 content", version="v2",
            gate=lambda ctx: ctx.get("flag") == "on",
        ))
        fresh_registry.register(PromptSection(
            name="foo", content="v1 content", version="v1",
        ))
        # 默认仍然是 v2（因为先注册）
        rendered, _ = fresh_registry.build("foo", {"flag": "on"})
        assert rendered == "v2 content"

    def test_gate_fallback_to_v1_when_returns_false(self, fresh_registry):
        fresh_registry.register(PromptSection(
            name="foo", content="v2 content", version="v2",
            gate=lambda ctx: ctx.get("flag") == "on",
        ))
        fresh_registry.register(PromptSection(
            name="foo", content="v1 content", version="v1",
        ))
        # gate 返回 False，应回退 v1
        rendered, _ = fresh_registry.build("foo", {"flag": "off"})
        assert rendered == "v1 content"


# ===== load_from_dir =====

class TestLoadFromDir:
    def test_load_from_dir(self, fresh_registry, tmp_path: Path):
        (tmp_path / "agent_chat@v1.md").write_text("agent v1 content {x}", encoding="utf-8")
        (tmp_path / "reviewer@v2.md").write_text("reviewer v2", encoding="utf-8")
        (tmp_path / "no_version.md").write_text("default v1", encoding="utf-8")
        (tmp_path / "README.md").write_text("# 不应被加载", encoding="utf-8")

        count = fresh_registry.load_from_dir(tmp_path)
        assert count == 3  # README 跳过

        # 验证加载结果
        rendered, _ = fresh_registry.build("agent_chat", {"x": "hi"})
        assert rendered == "agent v1 content hi"

        rendered, _ = fresh_registry.build("reviewer")
        assert rendered == "reviewer v2"

        # no_version.md → version=v1
        rendered, _ = fresh_registry.build("no_version")
        assert rendered == "default v1"

    def test_load_from_nonexistent_dir(self, fresh_registry, tmp_path: Path):
        count = fresh_registry.load_from_dir(tmp_path / "nonexistent")
        assert count == 0


# ===== set_default =====

class TestSetDefault:
    def test_set_default_unknown_name(self, fresh_registry):
        with pytest.raises(KeyError, match="未注册"):
            fresh_registry.set_default("foo", "v1")

    def test_set_default_unknown_version(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="x", version="v1"))
        with pytest.raises(KeyError, match="版本不存在"):
            fresh_registry.set_default("foo", "v99")

    def test_set_default_works(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="x", version="v1"))
        fresh_registry.register(PromptSection(name="foo", content="y", version="v2"))
        fresh_registry.set_default("foo", "v2")
        assert fresh_registry._default_versions["foo"] == "v2"


# ===== list_all =====

class TestListAll:
    def test_list_all_empty(self, fresh_registry):
        assert fresh_registry.list_all() == []

    def test_list_all_returns_metadata(self, fresh_registry):
        fresh_registry.register(PromptSection(
            name="foo", content="content 1", version="v1", description="第一个"
        ))
        fresh_registry.register(PromptSection(
            name="foo", content="content 2", version="v2", description="第二个"
        ))
        fresh_registry.register(PromptSection(
            name="bar", content="bar content", version="v1"
        ))

        all_items = fresh_registry.list_all()
        assert len(all_items) == 2

        # 按 name 排序
        assert all_items[0]["name"] == "bar"
        assert all_items[1]["name"] == "foo"

        foo = all_items[1]
        assert foo["default_version"] == "v1"
        assert len(foo["versions"]) == 2
        assert foo["versions"][0]["version"] == "v1"
        assert foo["versions"][0]["description"] == "第一个"
        assert "hash" in foo["versions"][0]


# ===== get_section =====

class TestGetSection:
    def test_get_section_default(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="x", version="v1"))
        s = fresh_registry.get_section("foo")
        assert s is not None
        assert s.version == "v1"

    def test_get_section_specific_version(self, fresh_registry):
        fresh_registry.register(PromptSection(name="foo", content="x", version="v1"))
        fresh_registry.register(PromptSection(name="foo", content="y", version="v2"))
        s = fresh_registry.get_section("foo", "v2")
        assert s is not None
        assert s.content == "y"

    def test_get_section_unknown_returns_none(self, fresh_registry):
        assert fresh_registry.get_section("nonexistent") is None


# ===== 全局单例 =====

class TestGlobalSingleton:
    def test_global_registry_exists(self):
        assert global_registry is not None
        assert isinstance(global_registry, PromptRegistry)


# ===== F3 验收：实际加载的 prompt 数量与覆盖范围 =====

class TestRealRegistryCoverage:
    """验证 init_registry() 加载的真实 prompt 数量满足 ≥ 8 的方案验收要求"""

    def test_init_registry_loads_at_least_8_prompts(self):
        from app.common.prompt_registry import init_registry, prompt_registry

        # 重新加载（覆盖式注册不影响）
        init_registry()
        all_prompts = prompt_registry.list_all()
        names = [p["name"] for p in all_prompts]

        assert len(names) >= 8, (
            f"PromptRegistry 应加载 ≥ 8 个 prompt，"
            f"当前 {len(names)}: {names}"
        )

    def test_optimizer_prompts_registered(self):
        """F3 后续：optimizer 三个 prompt 必须已注册"""
        from app.common.prompt_registry import init_registry, prompt_registry

        init_registry()
        all_names = {p["name"] for p in prompt_registry.list_all()}

        for required in (
            "optimizer_failure_analysis",
            "optimizer_generate_candidate",
            "optimizer_pairwise_judge",
        ):
            assert required in all_names, f"必须注册的 prompt 缺失: {required}"

    def test_optimizer_generate_candidate_renders(self):
        """渲染时占位符替换正确，且 JSON 示例不受影响"""
        from app.common.prompt_registry import init_registry, prompt_registry

        init_registry()
        rendered, prompt_hash = prompt_registry.build(
            "optimizer_generate_candidate",
            context={
                "skill_md": "# Test Skill\nbody",
                "goal": "提高准确率",
                "editable_zones": "branch_conditions, thresholds",
                "frozen_zones": "frontmatter",
                "failures": "case-1: 失败",
                "decision_logs": "(无)",
            },
        )
        # 占位符已替换
        assert "Test Skill" in rendered
        assert "提高准确率" in rendered
        # JSON 示例的 {} 不被破坏
        assert '"rationale"' in rendered
        assert '"changes"' in rendered
        # hash 12 字符
        assert len(prompt_hash) == 12

    def test_optimizer_failure_analysis_renders(self):
        from app.common.prompt_registry import init_registry, prompt_registry

        init_registry()
        rendered, prompt_hash = prompt_registry.build(
            "optimizer_failure_analysis",
            context={
                "skill_md": "test skill",
                "failures": "case 1\ncase 2",
            },
        )
        assert "test skill" in rendered
        assert "case 1" in rendered
        assert len(prompt_hash) == 12
