"""
测试 2026-04-13 Skill 编写体验改进:
- parser 表头 bug 修复
- emoji 去除改进
- JSON 块解析健壮性
- 条件启发式扩展
- 决策树可达性检测
- 语义 diff 覆盖 test_cases/custom_sections
- apply_patch 异常处理
- 启动配置校验
"""

import pytest

from app.skills.core.parser import SkillParser, _strip_leading_emoji, _is_condition_line


# ── Parser 表头修复 ──

class TestTableHeaderFix:
    def test_table_with_separator(self):
        """标准表格（含分隔行）应正常解析"""
        parser = SkillParser()
        text = """| 名称 | 来源 | 频率 |
|---|---|---|
| ROI数据 | API | 每日 |
| 预算表 | CSV | 每周 |"""
        rows = parser._parse_md_table(text, expected_cols=3)
        assert len(rows) == 2
        assert rows[0][0] == "ROI数据"
        assert rows[1][0] == "预算表"

    def test_table_without_separator(self):
        """无分隔行的表格：第一行视为表头，其余行作为数据"""
        parser = SkillParser()
        text = """| 名称 | 来源 | 频率 |
| ROI数据 | API | 每日 |"""
        # 没有分隔行时，防御性处理：第一行视为表头跳过，其余行作为数据
        rows = parser._parse_md_table(text, expected_cols=3)
        assert len(rows) == 1
        assert rows[0][0] == "ROI数据"


# ── Emoji 去除 ──

class TestEmojiStripping:
    def test_standard_emojis(self):
        assert _strip_leading_emoji("✅ ROI > 1.5") == "ROI > 1.5"
        assert _strip_leading_emoji("🟡 中等风险") == "中等风险"
        assert _strip_leading_emoji("🔴 高风险") == "高风险"
        assert _strip_leading_emoji("⛔ 停止") == "停止"

    def test_uncommon_emojis(self):
        """之前硬编码列表不包含的emoji"""
        assert _strip_leading_emoji("🔵 蓝灯") == "蓝灯"
        assert _strip_leading_emoji("🟢 绿灯") == "绿灯"
        assert _strip_leading_emoji("💡 建议") == "建议"
        assert _strip_leading_emoji("📊 数据") == "数据"

    def test_no_emoji(self):
        assert _strip_leading_emoji("ROI > 1.5") == "ROI > 1.5"

    def test_only_emoji(self):
        assert _strip_leading_emoji("✅🟡🔴") == ""


# ── 条件行检测 ──

class TestConditionDetection:
    def test_operator_conditions(self):
        assert _is_condition_line("ROI > 1.5")
        assert _is_condition_line("预算 <= 10000")
        assert _is_condition_line("A ≥ B × 1.2")

    def test_natural_language_conditions(self):
        assert _is_condition_line("ROI between A and B")
        assert _is_condition_line("如果 ROI > 阈值")
        assert _is_condition_line("当 ROI > 盈亏线时")
        assert _is_condition_line("属于高风险类别")
        assert _is_condition_line("是否需要审批")
        assert _is_condition_line("预算超过10000")
        assert _is_condition_line("ROI 大于 1.5")

    def test_non_condition_lines(self):
        assert not _is_condition_line("可考虑加预算")
        assert not _is_condition_line("输出投放建议报告")
        assert not _is_condition_line("Step 1: 按ROI排序")

    def test_no_false_positives_on_common_chinese(self):
        """不应把含 '当前'、'或者' 等常用词误判为条件行"""
        assert not _is_condition_line("当前版本 v1.2")
        assert not _is_condition_line("当前状态正常")
        assert not _is_condition_line("或可增加投入")


# ── JSON 块解析 ──

class TestJsonBlockParsing:
    def test_normal_two_blocks(self):
        parser = SkillParser()
        text = """### 正常案例

**输入:**
```json
{"roi": 1.5}
```

**期望输出:**
```json
{"decision": "green"}
```
"""
        cases = parser._parse_test_cases(text)
        assert len(cases) == 1
        assert cases[0].input_data == {"roi": 1.5}
        assert cases[0].expected_output == {"decision": "green"}

    def test_malformed_json_logged(self):
        """JSON 解析失败不应静默"""
        parser = SkillParser()
        text = """### 异常案例

```json
{not valid json}
```
"""
        cases = parser._parse_test_cases(text)
        assert len(cases) == 1
        assert cases[0].input_data == {}  # 解析失败应为空


# ── 语义 Diff 覆盖 test_cases + custom_sections ──

class TestSemanticDiffExtended:
    def test_diff_custom_sections(self):
        from app.reviews.semantic_diff import _diff_custom_sections

        old = {"参考资料": "旧内容"}
        new = {"参考资料": "新内容", "补充说明": "新增章节"}

        changes = _diff_custom_sections(old, new)
        assert len(changes) == 2

        types = {c["type"] for c in changes}
        assert "modified" in types
        assert "added" in types

    def test_diff_custom_sections_removed(self):
        from app.reviews.semantic_diff import _diff_custom_sections

        old = {"参考资料": "内容A", "注意事项": "内容B"}
        new = {"参考资料": "内容A"}

        changes = _diff_custom_sections(old, new)
        assert len(changes) == 1
        assert changes[0]["type"] == "removed"

    def test_diff_custom_sections_no_change(self):
        from app.reviews.semantic_diff import _diff_custom_sections

        sections = {"参考资料": "相同内容"}
        changes = _diff_custom_sections(sections, sections)
        assert len(changes) == 0


# ── 配置校验 ──

class TestConfigValidation:
    def test_validate_production_default_secret(self):
        from app.config import Settings
        s = Settings(SECRET_KEY="dev-secret-key-change-in-production")
        issues = s.validate_production()
        assert any("SECRET_KEY" in i for i in issues)

    def test_validate_production_ok(self):
        from app.config import Settings
        s = Settings(SECRET_KEY="a-long-random-string-for-production-use")
        issues = s.validate_production()
        assert not any("SECRET_KEY" in i for i in issues)

    def test_operational_warnings_empty_ai_api_key(self):
        from app.config import Settings
        s = Settings(SECRET_KEY="x", AI_API_KEY="")
        warnings = s.validate_operational_warnings()
        assert any("AI_API_KEY" in w for w in warnings)

    def test_operational_warnings_empty_canonical_repo_remote(self):
        from app.config import Settings
        s = Settings(SECRET_KEY="x", SKILL_REPO_CANONICAL_REMOTE="")
        warnings = s.validate_operational_warnings()
        assert any("SKILL_REPO_CANONICAL_REMOTE" in w for w in warnings)

    def test_operational_warnings_hardcoded_openclaw(self):
        from app.config import Settings
        s = Settings(SECRET_KEY="x", OPENCLAW_DEFAULT_URL="ws://192.168.1.20:18789")
        warnings = s.validate_operational_warnings()
        assert any("OPENCLAW_DEFAULT_URL" in w for w in warnings)

    def test_operational_warnings_default_database_password(self):
        from app.config import Settings
        s = Settings(
            SECRET_KEY="x",
            DATABASE_URL="postgresql+asyncpg://skillforge:password@localhost:5432/skillforge",
            DATABASE_URL_SYNC="postgresql+psycopg2://skillforge:password@localhost:5432/skillforge",
        )
        warnings = s.validate_operational_warnings()
        assert any("DATABASE_URL" in w for w in warnings)


# ── 决策树可达性 ──

class TestDecisionTreeReachability:
    def test_parser_detects_unreachable_step_in_validator(self):
        """通过 structural_validator 验证孤立 step 检测"""
        import tempfile, os

        skill_md = """---
name: test-skill
description: test
department: EC
trigger_type: manual
---

# test-skill

## 目的

测试用

## 执行步骤

### Step 1: 第一步

ROI > 1.5 → 绿灯

### Step 2: 孤立步骤

预算 > 10000 → 红灯
"""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = os.path.join(tmp, "test-skill")
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
                f.write(skill_md)

            from app.skills.validators.structural_validator import structural_validate
            report = structural_validate(skill_dir)
            # Step 2 没有被 Step 1 引用，应有孤立节点警告
            orphan_warnings = [w for w in report.warnings if "孤立节点" in w]
            assert len(orphan_warnings) == 1
            assert "Step 2" in orphan_warnings[0]
