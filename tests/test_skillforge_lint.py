"""skillforge-lint 静态规则引擎测试"""

import pytest
from app.skills.lint import lint_skill, LintReport
from app.skills.lint.rules import (
    check_metadata,
    check_branch_completeness,
    check_unreachable_branch,
    check_threshold_conflict,
    check_threshold_gap,
    check_test_coverage,
    check_param_usage,
)
from app.skills.core.parser import SkillStructured, DecisionStep, Branch, TestCase, Antipattern


def make_skill(**overrides) -> SkillStructured:
    """测试辅助：快速构造 SkillStructured。"""
    defaults = {
        "frontmatter": {"name": "test", "department": "EC", "risk_level": "R2"},
        "purpose": "测试目标",
        "steps": [],
        "antipatterns": [],
        "output_definition": [],
        "data_inputs": [],
        "test_cases": [],
        "raw_sections": {},
        "custom_sections": {},
    }
    defaults.update(overrides)
    return SkillStructured(**defaults)


def make_step(step_id, branches):
    return DecisionStep(id=step_id, name=step_id, description="", branches=branches)


def make_branch(condition, conclusion="通过", action="", next_step=None):
    return Branch(condition=condition, conclusion=conclusion, action=action, next_step=next_step)


# ═══ Metadata 检查 ═══

class TestMetadata:
    def test_missing_name_is_error(self):
        s = make_skill(frontmatter={})
        issues = check_metadata(s)
        errors = [i for i in issues if i.severity == "error"]
        assert any(i.rule == "metadata.name" for i in errors)

    def test_missing_description_is_error(self):
        s = make_skill(frontmatter={"name": "test"}, purpose="")
        issues = check_metadata(s)
        assert any(i.rule == "metadata.description" and i.severity == "error" for i in issues)

    def test_missing_risk_level_is_warning(self):
        s = make_skill(frontmatter={"name": "t", "department": "EC"})
        issues = check_metadata(s)
        assert any(i.rule == "metadata.risk_level" and i.severity == "warning" for i in issues)

    def test_complete_metadata_no_issues(self):
        s = make_skill()
        issues = check_metadata(s)
        assert len(issues) == 0


# ═══ 分支完备性（最核心）═══

class TestBranchCompleteness:
    def test_single_branch_is_error(self):
        step = make_step("step_1", [make_branch("ROI > 1.2", "绿灯")])
        s = make_skill(steps=[step])
        issues = check_branch_completeness(s)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 1
        assert "缺少兜底" in errors[0].message

    def test_missing_else_warning(self):
        step = make_step("step_1", [
            make_branch("ROI > 1.2", "绿灯"),
            make_branch("ROI < 0.8", "红灯"),
        ])
        s = make_skill(steps=[step])
        issues = check_branch_completeness(s)
        warnings = [i for i in issues if i.severity == "warning"]
        assert len(warnings) == 1

    def test_with_else_branch_passes(self):
        step = make_step("step_1", [
            make_branch("ROI > 1.2", "绿灯"),
            make_branch("其他情况", "默认"),
        ])
        s = make_skill(steps=[step])
        issues = check_branch_completeness(s)
        assert len(issues) == 0

    def test_multiple_else_keywords(self):
        for kw in ["其他", "else", "默认", "default", "不满足", "否则", "兜底"]:
            step = make_step("s1", [
                make_branch("ROI > 1.2", "绿灯"),
                make_branch(f"{kw}条件", "默认"),
            ])
            s = make_skill(steps=[step])
            issues = check_branch_completeness(s)
            assert len(issues) == 0, f"关键词 '{kw}' 应该被识别为兜底"

    def test_empty_branches_is_error(self):
        step = make_step("s1", [])
        s = make_skill(steps=[step])
        issues = check_branch_completeness(s)
        assert any(i.severity == "error" and "没有任何分支" in i.message for i in issues)


# ═══ 不可达分支 ═══

class TestUnreachableBranch:
    def test_else_in_middle_is_error(self):
        step = make_step("s1", [
            make_branch("ROI > 1.2", "绿灯"),
            make_branch("其他", "默认"),  # else 在中间
            make_branch("ROI > 2.0", "高绿灯"),  # 不可达
        ])
        s = make_skill(steps=[step])
        issues = check_unreachable_branch(s)
        assert len(issues) >= 1
        assert any("永远不会执行" in i.message for i in issues)

    def test_else_at_end_is_ok(self):
        step = make_step("s1", [
            make_branch("ROI > 1.2", "绿灯"),
            make_branch("ROI > 2.0", "高绿灯"),
            make_branch("其他", "默认"),
        ])
        s = make_skill(steps=[step])
        issues = check_unreachable_branch(s)
        assert len(issues) == 0


# ═══ 阈值检查 ═══

class TestThresholdChecks:
    def test_extract_threshold(self):
        from app.skills.lint.rules import _extract_threshold
        assert _extract_threshold("ROI > 1.5") == (">", 1.5)
        assert _extract_threshold("预算 <= 0.8") == ("<=", 0.8)
        assert _extract_threshold("其他情况") is None

    def test_threshold_gap_detected(self):
        step = make_step("s1", [
            make_branch("ROI > 1.5", "绿灯"),
            make_branch("ROI < 0.8", "红灯"),
            # 缺 [0.8, 1.5] 区间
        ])
        s = make_skill(steps=[step])
        issues = check_threshold_gap(s)
        assert any("未被覆盖" in i.message for i in issues)

    def test_threshold_gap_with_else_no_issue(self):
        step = make_step("s1", [
            make_branch("ROI > 1.5", "绿灯"),
            make_branch("ROI < 0.8", "红灯"),
            make_branch("其他", "黄灯"),
        ])
        s = make_skill(steps=[step])
        issues = check_threshold_gap(s)
        assert len(issues) == 0


# ═══ 测试覆盖 ═══

class TestTestCoverage:
    def test_no_tests_with_branches_error(self):
        step = make_step("s1", [
            make_branch("ROI > 1.2", "绿灯"),
            make_branch("其他", "黄灯"),
        ])
        s = make_skill(steps=[step], test_cases=[])
        issues = check_test_coverage(s)
        errors = [i for i in issues if i.severity == "error"]
        assert any("没有任何测试用例" in i.message for i in errors)

    def test_insufficient_tests_warning(self):
        step = make_step("s1", [
            make_branch("A", "1"), make_branch("B", "2"), make_branch("C", "3"),
        ])
        tests = [TestCase(name="t1", input_data={}, expected_output={}, assert_rules=[])]
        s = make_skill(steps=[step], test_cases=tests)
        issues = check_test_coverage(s)
        assert any("测试用例数" in i.message for i in issues)

    def test_missing_antipatterns_warning(self):
        # 需要有 steps 才会触发检查
        step = make_step("s1", [make_branch("A", "1"), make_branch("其他", "2")])
        s = make_skill(
            steps=[step],
            test_cases=[TestCase(name="t1", input_data={}, expected_output={}, assert_rules=[])],
            antipatterns=[],
        )
        issues = check_test_coverage(s)
        assert any("反例" in i.message for i in issues)


# ═══ Magic Number ═══

class TestParamUsage:
    def test_many_magic_numbers_info(self):
        step = make_step("s1", [
            make_branch("A > 1.5 AND B > 2.3 AND C > 4.7 AND D > 5.9", "1"),
            make_branch("其他", "2"),
        ])
        s = make_skill(steps=[step])
        issues = check_param_usage(s)
        assert any(i.rule == "param_usage" for i in issues)


# ═══ 整体 lint_skill ═══

class TestLintSkill:
    def test_perfect_skill_passes(self):
        step = make_step("s1", [
            make_branch("ROI > 1.2", "绿灯"),
            make_branch("ROI < 0.8", "红灯"),
            make_branch("其他", "黄灯"),
        ])
        s = make_skill(
            steps=[step],
            test_cases=[
                TestCase(name="t1", input_data={"roi": 1.5}, expected_output={"r": "green"}, assert_rules=[]),
                TestCase(name="t2", input_data={"roi": 0.5}, expected_output={"r": "red"}, assert_rules=[]),
                TestCase(name="t3", input_data={"roi": 1.0}, expected_output={"r": "yellow"}, assert_rules=[]),
            ],
            antipatterns=[Antipattern(scenario="测试边界", correct_action="按规则", source="")],
        )
        report = lint_skill("test", s)
        assert report.passed, f"应该通过但有错误: {[e.message for e in report.errors]}"
        assert len(report.errors) == 0

    def test_broken_skill_fails(self):
        """waoowaoo-studio 类型：5 个 step 都只有 1 个分支且无测试"""
        steps = [
            make_step(f"s{i}", [make_branch(f"条件{i}", "通过")])
            for i in range(1, 6)
        ]
        s = make_skill(steps=steps, test_cases=[])
        report = lint_skill("broken", s)
        assert not report.passed
        assert len(report.errors) >= 5  # 5 个缺兜底 error
        # 还会有 test_coverage error

    def test_report_structure(self):
        s = make_skill()
        report = lint_skill("test", s)
        d = report.to_dict()
        assert "skill_id" in d
        assert "passed" in d
        assert "errors" in d
        assert "warnings" in d
        assert "infos" in d


class TestStaticLLMDetection:
    def test_detects_direct_llm_sdk_import(self):
        from app.reviews.static_checks import scan_static_text

        report = scan_static_text("demo/scripts/main.py", "from openai import OpenAI\n")

        assert not report.passed
        assert report.findings[0].rule == "llm_sdk_import"

    def test_detects_direct_llm_api_http_call(self):
        from app.reviews.static_checks import scan_static_text

        report = scan_static_text(
            "demo/scripts/main.py",
            "import requests\nrequests.post('https://api.openai.com/v1/chat/completions')\n",
        )

        assert not report.passed
        assert any(item.rule == "llm_api_http_call" for item in report.findings)

    def test_detects_js_llm_sdk_import(self):
        from app.reviews.static_checks import scan_static_text

        report = scan_static_text("demo/scripts/worker.ts", 'import OpenAI from "openai";\n')

        assert not report.passed
        assert report.findings[0].rule == "llm_sdk_import"

    def test_scans_platform_mcp_server_entrypoints(self, tmp_path):
        from app.reviews.static_checks import scan_platform_mcp_static_checks

        (tmp_path / "demo_mcp_server.py").write_text("import anthropic\n", encoding="utf-8")
        (tmp_path / "helper.py").write_text("import openai\n", encoding="utf-8")

        report = scan_platform_mcp_static_checks(tmp_path)

        assert not report.passed
        assert [item.path for item in report.findings] == ["scripts/demo_mcp_server.py"]

    def test_publish_gate_static_detection_blocks_skill_code(self, monkeypatch):
        from app.skills import publish_readiness as pr

        monkeypatch.setattr(pr.git_service, "list_skill_files", lambda skill_id: ["scripts/main.py"])
        monkeypatch.setattr(
            pr.git_service,
            "read_file",
            lambda skill_id, path, **kwargs: "import openai\n" if path == "scripts/main.py" else None,
        )

        result = pr._static_detection_gate_check("demo")

        assert result["status"] == "blocked"
        assert result["findings"][0]["rule"] == "llm_sdk_import"

    def test_publish_gate_schema_validation_blocks_invalid_schema(self, monkeypatch):
        from app.skills import publish_readiness as pr

        monkeypatch.setattr(
            pr.git_service,
            "read_file",
            lambda skill_id, path, **kwargs: '{"output_schema":{"type":"string"}}' if path == "contract.json" else None,
        )

        result = pr._schema_validation_gate_check("demo")

        assert result["status"] == "blocked"
        assert any("type 必须是 'object'" in error for error in result["errors"])
