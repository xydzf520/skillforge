"""
端到端验证：2026-04-13 改进项在完整链路中的效果。
覆盖 创建→编辑→校验→审核 链路中的每个改进点。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.skills.core.parser import SkillParser


# ── E2E-1: Parser 完整 roundtrip（含改进后的表格、emoji、条件检测）──

class TestParserFullRoundtrip:
    """验证 parse → 修改 → render → re-parse 保持数据完整性"""

    def test_roundtrip_with_table_and_emoji_branches(self):
        parser = SkillParser()
        skill_md = """---
name: EC-投放-01
description: 投放决策Skill
department: EC
trigger_type: cron
risk_level: R2
---

# EC-投放-01

## 目的

根据ROI决定投放策略

## 执行步骤

### Step 1: 评估ROI

  ├─ 🟢 ROI > 盈亏线 × 1.2 → 绿灯
      动作: 可考虑加预算
  └─ 🔴 ROI < 盈亏线 × 0.8 → 红灯
      动作: 建议暂停
      → 进入 Step 2

### Step 2: 深度分析

  ├─ 预算超过10000 → 需审批
  └─ 预算不超过阈值 → 自动通过

## 反例

1. **误判场景**: 新品首周ROI低就停投
   **正确做法**: 新品有3天保护期

## 输出

| 输出项 | 格式 | 接收人 | 审批级别 |
|---|---|---|---|
| 投放建议 | 钉钉卡片 | 投手 | L1 |
| 预算调整 | JSON | 系统 | L2 |

## 数据输入

| 数据名称 | 来源 | 刷新频率 |
|---|---|---|
| ROI数据 | API拉取 | 每日 |

## 测试用例

### 正常绿灯

```json
{"roi": 1.8, "threshold": 1.5}
```

```json
{"decision": "green"}
```

## 参考资料

详细说明文档
"""
        # parse
        parsed = parser.parse(skill_md)

        # 验证各模块正确解析
        assert parsed.frontmatter["name"] == "EC-投放-01"
        assert parsed.purpose == "根据ROI决定投放策略"
        assert len(parsed.steps) == 2

        # Step 1: emoji 应被去除
        step1 = parsed.steps[0]
        assert step1.id == "1"
        assert len(step1.branches) >= 2
        assert "🟢" not in step1.branches[0].condition
        assert "ROI" in step1.branches[0].condition

        # Step 2: 自然语言条件应被识别
        step2 = parsed.steps[1]
        assert len(step2.branches) >= 1
        assert "超过" in step2.branches[0].condition or "10000" in step2.branches[0].condition

        # 表格解析
        assert len(parsed.output_definition) == 2
        assert parsed.output_definition[0].name == "投放建议"
        assert len(parsed.data_inputs) == 1
        assert parsed.data_inputs[0].name == "ROI数据"

        # 测试用例
        assert len(parsed.test_cases) == 1
        assert parsed.test_cases[0].input_data == {"roi": 1.8, "threshold": 1.5}
        assert parsed.test_cases[0].expected_output == {"decision": "green"}

        # 自定义章节
        assert "参考资料" in parsed.custom_sections

        # render 后 re-parse
        rendered = parser.render(parsed)
        re_parsed = parser.parse(rendered)

        assert re_parsed.frontmatter["name"] == "EC-投放-01"
        assert re_parsed.purpose == "根据ROI决定投放策略"
        assert len(re_parsed.steps) == 2
        assert len(re_parsed.output_definition) == 2
        assert len(re_parsed.data_inputs) == 1
        assert len(re_parsed.test_cases) == 1
        assert "参考资料" in re_parsed.custom_sections


# ── E2E-2: 结构校验器含决策树检测 ──

class TestStructuralValidatorE2E:
    def test_valid_skill_passes(self):
        """正确的 Skill 应通过所有校验"""
        import tempfile, os

        skill_md = """---
name: test-valid
description: Valid test skill
department: EC
trigger_type: manual
---

# test-valid

## 目的

测试用

## 执行步骤

### Step 1: 判断

ROI > 1.5 → 绿灯
      → 进入 Step 2

### Step 2: 确认

预算 > 0 → 执行
"""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = os.path.join(tmp, "test-valid")
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
                f.write(skill_md)

            from app.skills.validators.structural_validator import structural_validate
            report = structural_validate(skill_dir)
            # 不应有孤立节点警告
            orphan_warnings = [w for w in report.warnings if "孤立节点" in w]
            assert len(orphan_warnings) == 0

    def test_duplicate_step_ids(self):
        """重复 step ID 应报错"""
        import tempfile, os

        skill_md = """---
name: test-dup
description: Duplicate step IDs
department: EC
trigger_type: manual
---

# test-dup

## 执行步骤

### Step 1: 第一步

ROI > 1

### Step 1: 也是第一步

预算 > 0
"""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = os.path.join(tmp, "test-dup")
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
                f.write(skill_md)

            from app.skills.validators.structural_validator import structural_validate
            report = structural_validate(skill_dir)
            dup_errors = [e for e in report.errors if "重复 step ID" in e]
            assert len(dup_errors) == 1

    def test_dangling_reference(self):
        """引用不存在的 step 应报警告"""
        import tempfile, os

        skill_md = """---
name: test-dangle
description: Dangling reference
department: EC
trigger_type: manual
---

# test-dangle

## 执行步骤

### Step 1: 判断

ROI > 1 → 绿灯
      → 进入 Step 99
"""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = os.path.join(tmp, "test-dangle")
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
                f.write(skill_md)

            from app.skills.validators.structural_validator import structural_validate
            report = structural_validate(skill_dir)
            dangle_warnings = [w for w in report.warnings if "引用不存在" in w]
            assert len(dangle_warnings) == 1
            assert "Step 99" in dangle_warnings[0]


# ── E2E-3: 语义 Diff 完整覆盖 ──

class TestSemanticDiffE2E:
    def test_full_diff_with_all_categories(self):
        """语义 diff 应覆盖所有 8 个类别"""
        from app.reviews.semantic_diff import _diff_frontmatter, _diff_steps, _diff_list, _diff_custom_sections, _generate_summary
        from app.skills.core.parser import Branch, DecisionStep, Antipattern, OutputItem, DataInput, TestCase

        changes = []

        # 1. frontmatter
        changes.extend(_diff_frontmatter({"name": "old"}, {"name": "new"}))
        # 2. steps
        changes.extend(_diff_steps(
            [DecisionStep(id="1", name="旧步骤", branches=[Branch(condition="A>1")])],
            [DecisionStep(id="1", name="新步骤", branches=[Branch(condition="A>2")])],
        ))
        # 3. antipatterns
        changes.extend(_diff_list("antipattern",
            [("旧场景", Antipattern(scenario="旧场景", correct_action="旧做法"))],
            [("新场景", Antipattern(scenario="新场景", correct_action="新做法"))],
            lambda a: f"{a.scenario} → {a.correct_action}",
        ))
        # 4. output
        changes.extend(_diff_list("output",
            [], [("新输出", OutputItem(name="新输出", format="JSON"))],
            lambda o: f"{o.name}({o.format})",
        ))
        # 5. data_input
        changes.extend(_diff_list("data_input",
            [("旧数据", DataInput(name="旧数据", source="API"))], [],
            lambda d: f"{d.name} ← {d.source}",
        ))
        # 6. test_case
        changes.extend(_diff_list("test_case",
            [], [("新测试", TestCase(name="新测试", assert_rules=["rule1"]))],
            lambda t: f"{t.name}（{len(t.assert_rules)}条断言）",
        ))
        # 7. custom_section
        changes.extend(_diff_custom_sections({"旧章节": "A"}, {"新章节": "B"}))

        categories = {c["category"] for c in changes}
        assert "frontmatter" in categories
        assert "step" in categories or "branch" in categories
        assert "antipattern" in categories
        assert "output" in categories
        assert "data_input" in categories
        assert "test_case" in categories
        assert "custom_section" in categories

        summary = _generate_summary(changes)
        assert "决策逻辑" in summary
        assert "配置" in summary
        assert "测试用例" in summary
        assert "自定义章节" in summary


# ── E2E-4: 配置校验完整流程 ──

class TestConfigE2EValidation:
    def test_full_validation_chain(self):
        """完整配置校验链：生产错误 + 运营警告"""
        from app.config import Settings

        # 模拟生产环境配置
        s = Settings(
            SECRET_KEY="synthetic-production-secret-key-long-enough",
            ENROLLMENT_PEPPER="synthetic-production-enrollment-pepper-long-enough",
            DATABASE_URL="postgresql+asyncpg://skillforge:real-secret@localhost:5432/skillforge",
            DATABASE_URL_SYNC="postgresql+psycopg2://skillforge:real-secret@localhost:5432/skillforge",
            AI_API_KEY="",
            SKILL_REPO_REMOTE="",
            SKILL_REPO_CANONICAL_REMOTE="",
            DINGTALK_APP_KEY="",
            OPENCLAW_DEFAULT_URL="ws://192.168.1.20:18789",
        )

        # 生产错误：应为空（正确配置了 secret key 和 pepper）
        prod_issues = s.validate_production()
        assert not any("SECRET_KEY" in i for i in prod_issues)
        assert not any("ENROLLMENT_PEPPER" in i for i in prod_issues)

        # 运营警告：至少包含核心 4 类；钉钉登录/回调等可追加更细告警
        op_warnings = s.validate_operational_warnings()
        assert len(op_warnings) >= 4
        assert any("AI_API_KEY" in w for w in op_warnings)
        assert any("SKILL_REPO" in w for w in op_warnings)
        assert any("DINGTALK" in w for w in op_warnings)
        assert any("OPENCLAW" in w for w in op_warnings)
