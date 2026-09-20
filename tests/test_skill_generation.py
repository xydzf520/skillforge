"""测试 Skill 生成相关功能"""
import pytest
import yaml


def test_render_skill_md():
    """测试确定性渲染。

    P0-8 之后 frontmatter 由 yaml.safe_dump 生成，
    quote 风格由 PyYAML 决定（中文常用单引号），所以断言改为
    "解析后的语义比对"，不再依赖具体的引号字符。
    """
    from app.skills.lifecycle.service import _render_skill_md
    md = _render_skill_md(
        slug="test-skill", description="测试。Use when: 测试时。",
        compatibility="Requires curl", department="AI", risk_level="R1",
        body="# 测试\n\n## 步骤\n\n```bash\ncurl http://example.com\n```",
    )
    assert md.startswith("---")
    # YAML 解析后的语义校验（不依赖具体引号风格）
    fm = yaml.safe_load(md.split("---", 2)[1])
    assert fm["name"] == "test-skill"
    assert fm["description"] == "测试。Use when: 测试时。"
    assert fm["compatibility"] == "Requires curl"
    assert fm["metadata"]["department"] == "AI"
    assert fm["metadata"]["risk-level"] == "R1"
    # body 内容保留
    assert "# 测试" in md
    assert "curl http://example.com" in md


def test_render_skill_md_escapes_quotes():
    """测试 description 含引号时的转义"""
    from app.skills.lifecycle.service import _render_skill_md
    md = _render_skill_md(
        slug="q-test", description='含"双引号"的描述。Use when: 测试。',
        compatibility="", department="EC", risk_level="R2",
        body="# test",
    )
    fm = yaml.safe_load(md.split("---", 2)[1])
    assert '"' not in fm["name"]  # name 不含引号
    assert "双引号" in fm["description"]


def test_render_skill_md_strips_body_frontmatter():
    """测试 body 中含 frontmatter 时的清理"""
    from app.skills.lifecycle.service import _render_skill_md
    body_with_fm = "---\nname: bad\n---\n# Real Body\ncontent"
    md = _render_skill_md(
        slug="strip-test", description="test。Use when: test。",
        compatibility="", department="AI", risk_level="R1", body=body_with_fm,
    )
    # 应该只有一个 frontmatter
    assert md.count("name: strip-test") == 1
    assert "name: bad" not in md
    assert "Real Body" in md


def test_validate_generated_md():
    """测试校验函数"""
    from app.skills.lifecycle.service import _validate_generated_md
    good = """---
name: valid-skill
description: "Good skill。Use when: always。"
metadata:
  department: AI
---

# Valid Skill

Some content here that is long enough to pass validation with more text."""
    assert _validate_generated_md(good, "valid-skill") is True

    # name 不匹配
    assert _validate_generated_md(good, "wrong-name") is False

    # 无 description
    bad_no_desc = """---
name: bad
---

# Bad"""
    assert _validate_generated_md(bad_no_desc, "bad") is False

    # 太短
    too_short = """---
name: short
description: "x"
---

# S"""
    assert _validate_generated_md(too_short, "short") is False


def test_acceptance_policy():
    """测试 AcceptancePolicy"""
    from app.optimizer.acceptance_policy import AcceptancePolicy
    p = AcceptancePolicy()
    cfg = {"acceptance": {"min_accuracy_gain": 0.02, "max_regression_count": 0}}

    ok, _ = p.pass_offline({"accuracy": 0.85, "regression_count": 0}, {"accuracy": 0.80, "regression_count": 0}, cfg)
    assert ok

    ok, _ = p.pass_offline({"accuracy": 0.81, "regression_count": 0}, {"accuracy": 0.80, "regression_count": 0}, cfg)
    assert not ok

    # 缺字段
    ok, msg = p.pass_offline({"regression_count": 0}, {"accuracy": 0.8, "regression_count": 0}, cfg)
    assert not ok
    assert "缺少" in msg
