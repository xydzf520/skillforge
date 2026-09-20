"""4 个 Skill 编写体验迭代项的单元测试

覆盖：
  - 测试 __TODO__ 自动回填（test_generation.auto_fill_test_inputs）
  - 改 Skill 后的回归 diff（regression_diff.diff_structured）
  - 改阈值的下游影响（param_index._walk_skill）
  - 跨 Skill 规则冲突（cross_skill_conflict）
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.skills.core.parser import (
    Antipattern,
    Branch,
    DecisionStep,
    OutputItem,
    SkillStructured,
    TestCase,
)


# ═══════════════════════════════════════════════════════
# 工厂
# ═══════════════════════════════════════════════════════

def _make_branch(cond, concl="", action="", nxt=None):
    return Branch(condition=cond, conclusion=concl, action=action, next_step=nxt)


def _make_step(sid, name, branches):
    return DecisionStep(id=sid, name=name, description="", branches=branches)


def _make_skill(steps=None, test_cases=None, frontmatter=None, output=None):
    return SkillStructured(
        frontmatter=frontmatter or {"name": "t", "department": "EC", "risk_level": "R2"},
        purpose="测试目的",
        steps=steps or [],
        antipatterns=[],
        output_definition=output or [],
        data_inputs=[],
        test_cases=test_cases or [],
        raw_sections={},
        custom_sections={},
    )


# ═══════════════════════════════════════════════════════
# Task #1: auto_fill_test_inputs
# ═══════════════════════════════════════════════════════

class TestAutoFillTestInputs:

    @pytest.mark.asyncio
    async def test_fills_todo_from_history(self):
        """__TODO__ 占位符应被历史样本中同名字段的最近值替换。"""
        from app.skills.tooling.test_generation import auto_fill_test_inputs

        cases = [
            {"name": "case1", "input": {"ROI": "__TODO__", "曝光": "__TODO__"}},
        ]

        # 模拟 db.execute 返回 (snapshot,) 元组列表
        db = MagicMock()
        rows = [
            (({"ROI": 1.8, "曝光": 5000}),),
            (({"ROI": 1.2, "曝光": 3000}),),
        ]
        result_obj = MagicMock()
        result_obj.all.return_value = rows
        db.execute = AsyncMock(return_value=result_obj)

        out = await auto_fill_test_inputs(db, "skill-1", cases)

        assert out["filled_count"] == 2
        assert out["samples_used"] == 2
        assert cases[0]["input"]["ROI"] == 1.8  # 取最新（rows[0]）
        assert cases[0]["input"]["曝光"] == 5000
        assert out["unfilled_keys"] == []

    @pytest.mark.asyncio
    async def test_unfilled_when_no_match(self):
        """字段名不匹配时保留 __TODO__ 并记录到 unfilled_keys。"""
        from app.skills.tooling.test_generation import auto_fill_test_inputs

        cases = [{"name": "c", "input": {"unknown_field": "__TODO__"}}]
        db = MagicMock()
        result_obj = MagicMock()
        result_obj.all.return_value = [(({"another_field": 1.0}),)]
        db.execute = AsyncMock(return_value=result_obj)

        out = await auto_fill_test_inputs(db, "skill-1", cases)
        assert out["filled_count"] == 0
        assert "unknown_field" in out["unfilled_keys"]
        assert cases[0]["input"]["unknown_field"] == "__TODO__"

    @pytest.mark.asyncio
    async def test_skips_concrete_values(self):
        """已经有具体值的字段不应被覆盖。"""
        from app.skills.tooling.test_generation import auto_fill_test_inputs

        cases = [{"name": "c", "input": {"ROI": 2.5, "曝光": "__TODO__"}}]
        db = MagicMock()
        result_obj = MagicMock()
        result_obj.all.return_value = [(({"ROI": 1.8, "曝光": 5000}),)]
        db.execute = AsyncMock(return_value=result_obj)

        out = await auto_fill_test_inputs(db, "skill-1", cases)
        assert cases[0]["input"]["ROI"] == 2.5  # 没被覆盖
        assert cases[0]["input"]["曝光"] == 5000
        assert out["filled_count"] == 1

    @pytest.mark.asyncio
    async def test_handles_empty_cases(self):
        """空 cases 列表应安全返回零计数。"""
        from app.skills.tooling.test_generation import auto_fill_test_inputs

        db = MagicMock()
        out = await auto_fill_test_inputs(db, "skill-1", [])
        assert out["filled_count"] == 0
        assert out["samples_used"] == 0

    @pytest.mark.asyncio
    async def test_snapshot_as_json_string(self):
        """[H4] PostgreSQL 驱动可能返回 snapshot 为 JSON 字符串而非 dict, 应自动 json.loads。"""
        from app.skills.tooling.test_generation import auto_fill_test_inputs
        import json

        cases = [{"name": "c", "input": {"ROI": "__TODO__"}}]
        db = MagicMock()
        result_obj = MagicMock()
        # snapshot 是字符串而不是 dict
        result_obj.all.return_value = [(json.dumps({"ROI": 2.5}),)]
        db.execute = AsyncMock(return_value=result_obj)

        out = await auto_fill_test_inputs(db, "skill-1", cases)
        assert out["filled_count"] == 1
        assert cases[0]["input"]["ROI"] == 2.5

    @pytest.mark.asyncio
    async def test_skips_none_and_complex_values(self):
        """None / dict / list 等复杂类型应跳过, 不作为回填值。"""
        from app.skills.tooling.test_generation import auto_fill_test_inputs

        cases = [{"name": "c", "input": {"ROI": "__TODO__", "tags": "__TODO__"}}]
        db = MagicMock()
        result_obj = MagicMock()
        result_obj.all.return_value = [
            (({"ROI": None, "tags": ["a", "b"]}),),  # None / list 都跳过
            (({"ROI": 1.5}),),  # 第二条才有真实值
        ]
        db.execute = AsyncMock(return_value=result_obj)

        out = await auto_fill_test_inputs(db, "skill-1", cases)
        assert cases[0]["input"]["ROI"] == 1.5
        assert cases[0]["input"]["tags"] == "__TODO__"  # tags list 被跳过, 仍是占位符
        assert "tags" in out["unfilled_keys"]

    @pytest.mark.asyncio
    async def test_sql_failure_degrades_gracefully(self):
        """[H4] DB 查询失败时应降级返回, 不抛异常 (前端能拿 unfilled_keys)。"""
        from app.skills.tooling.test_generation import auto_fill_test_inputs

        cases = [{"name": "c", "input": {"ROI": "__TODO__"}}]
        db = MagicMock()
        db.execute = AsyncMock(side_effect=Exception("DB connection lost"))

        out = await auto_fill_test_inputs(db, "skill-1", cases)
        assert out["filled_count"] == 0
        assert out["samples_used"] == 0
        assert out.get("error") == "history_query_failed"
        # 原 cases 不被破坏
        assert cases[0]["input"]["ROI"] == "__TODO__"

    @pytest.mark.asyncio
    async def test_autofill_source_per_case_isolation(self):
        """[H4] _autofill_source 标记应仅打在真实回填的 case 上, 不污染后续未回填的 case。"""
        from app.skills.tooling.test_generation import auto_fill_test_inputs

        cases = [
            {"name": "c1", "input": {"ROI": "__TODO__"}},          # 能回填
            {"name": "c2", "input": {"unmatched": "__TODO__"}},    # 字段名不匹配, 无法回填
            {"name": "c3", "input": {"ROI": 5.0}},                  # 已有具体值, 不回填
        ]
        db = MagicMock()
        result_obj = MagicMock()
        result_obj.all.return_value = [(({"ROI": 1.5}),)]
        db.execute = AsyncMock(return_value=result_obj)

        out = await auto_fill_test_inputs(db, "skill-1", cases)
        assert out["filled_count"] == 1
        # 仅 c1 被标记为 _autofill_source
        assert cases[0].get("_autofill_source") == "decision_log"
        assert "_autofill_source" not in cases[1]
        assert "_autofill_source" not in cases[2]


# ═══════════════════════════════════════════════════════
# Task #2: regression_diff
# ═══════════════════════════════════════════════════════

class TestRegressionDiff:

    def _to_md(self, structured: SkillStructured) -> str:
        from app.skills.core.parser import skill_parser
        return skill_parser.render(structured)

    def test_no_changes(self):
        """两份相同的 Skill diff 应为空。"""
        from app.skills.lifecycle.regression_diff import diff_structured

        s = _make_skill(steps=[
            _make_step("1", "判断ROI", [
                _make_branch("ROI > 1.5", "绿灯", "加预算"),
                _make_branch("其他情况", "红灯", "停投"),
            ]),
        ])
        md = self._to_md(s)
        diff = diff_structured("skill-1", md, md)
        assert diff.has_changes is False
        assert diff.summary == "无规则层面变化"

    def test_branch_modified_detected(self):
        """branch.condition 改动应被检测到并归到 modified。"""
        from app.skills.lifecycle.regression_diff import diff_structured

        old = _make_skill(steps=[
            _make_step("1", "判断ROI", [
                _make_branch("ROI > 1.5", "绿灯", "加预算"),
                _make_branch("其他情况", "红灯", "停投"),
            ]),
        ])
        new = _make_skill(steps=[
            _make_step("1", "判断ROI", [
                _make_branch("ROI > 2.0", "绿灯", "加预算"),  # 阈值变了
                _make_branch("其他情况", "红灯", "停投"),
            ]),
        ])
        diff = diff_structured("skill-1", self._to_md(old), self._to_md(new))
        assert diff.has_changes is True
        assert len(diff.steps) == 1
        sd = diff.steps[0]
        assert sd.change_type == "modified"
        assert len(sd.branches_modified) == 1
        assert "condition" in sd.branches_modified[0].fields_changed

    def test_branch_added_and_removed(self):
        """新增/删除分支正确分类。"""
        from app.skills.lifecycle.regression_diff import diff_structured

        old = _make_skill(steps=[
            _make_step("1", "判断", [
                _make_branch("X > 1", "通过"),
            ]),
        ])
        new = _make_skill(steps=[
            _make_step("1", "判断", [
                _make_branch("X > 1", "通过"),
                _make_branch("X > 5", "黄灯"),
                _make_branch("其他", "红灯"),
            ]),
        ])
        diff = diff_structured("skill-1", self._to_md(old), self._to_md(new))
        assert diff.has_changes
        sd = diff.steps[0]
        assert len(sd.branches_added) == 2

    def test_test_case_affected_via_input_field(self):
        """测试用例 input 引用了改动 branch 的字段时，应被标记为受影响。"""
        from app.skills.lifecycle.regression_diff import diff_structured

        old = _make_skill(
            steps=[_make_step("1", "判断", [
                _make_branch("ROI > 1.5", "绿灯", "加预算"),
                _make_branch("其他", "红灯", "停"),
            ])],
            test_cases=[TestCase(name="case-A", input_data={"ROI": 1.8}, expected_output={"conclusion": "绿灯"})],
        )
        new = _make_skill(
            steps=[_make_step("1", "判断", [
                _make_branch("ROI > 2.0", "绿灯", "加预算"),  # 阈值改了
                _make_branch("其他", "红灯", "停"),
            ])],
            test_cases=[TestCase(name="case-A", input_data={"ROI": 1.8}, expected_output={"conclusion": "绿灯"})],
        )
        diff = diff_structured("skill-1", self._to_md(old), self._to_md(new))
        assert "case-A" in diff.test_cases_affected

    def test_affected_tests_estimation_note(self):
        """[H5] test_cases_affected 输出时, notes 必须附带过度估计提示。"""
        from app.skills.lifecycle.regression_diff import diff_structured

        old = _make_skill(
            steps=[_make_step("1", "判断", [
                _make_branch("ROI > 1.5", "绿灯", "加预算"),
            ])],
            test_cases=[TestCase(name="t1", input_data={"ROI": 1.8}, expected_output={"conclusion": "绿灯"})],
        )
        new = _make_skill(
            steps=[_make_step("1", "判断", [
                _make_branch("ROI > 2.0", "绿灯", "加预算"),
            ])],
            test_cases=[TestCase(name="t1", input_data={"ROI": 1.8}, expected_output={"conclusion": "绿灯"})],
        )
        diff = diff_structured("skill-1", self._to_md(old), self._to_md(new))
        assert diff.test_cases_affected
        assert any("过度估计" in n or "false positive" in n.lower() for n in diff.notes)

    def test_no_affected_tests_no_estimation_note(self):
        """无 affected 时不应附 estimation 提示, 避免噪音。"""
        from app.skills.lifecycle.regression_diff import diff_structured

        old = _make_skill(steps=[_make_step("1", "ok", [_make_branch("X > 1", "通过")])])
        new = _make_skill(steps=[_make_step("1", "ok", [_make_branch("X > 2", "通过")])])
        diff = diff_structured("skill-1", self._to_md(old), self._to_md(new))
        assert not diff.test_cases_affected
        assert not any("过度估计" in n for n in diff.notes)


# ═══════════════════════════════════════════════════════
# Task #3: param_index
# ═══════════════════════════════════════════════════════

class TestParamIndex:

    def test_extract_placeholder_and_metric(self):
        """同一条 condition 应同时识别 placeholder 和 metric。"""
        from app.skills.intelligence.param_index import _extract_param_refs

        placeholders, metrics = _extract_param_refs("ROI > {roi_green_ratio}")
        assert placeholders == {"roi_green_ratio"}
        assert "ROI" in metrics

    def test_walk_skill_yields_per_param_buckets(self):
        """walk_skill 应按参数名聚合所有引用位置。"""
        from app.skills.intelligence.param_index import _walk_skill

        structured = _make_skill(steps=[
            _make_step("step_1", "ROI判断", [
                _make_branch("ROI > {roi_green_ratio}", "绿灯", "加预算"),
                _make_branch("其他", "红灯", "停"),
            ]),
            _make_step("step_2", "曝光判断", [
                _make_branch("曝光 > 5000", "通过"),
            ]),
        ])
        skill = SimpleNamespace(id="EC-投放-01", name="EC投放", department="EC")
        bucket = _walk_skill(skill, structured)

        assert "roi_green_ratio" in bucket
        assert bucket["roi_green_ratio"][0].kind == "placeholder"
        assert bucket["roi_green_ratio"][0].step_id == "step_1"
        assert "ROI" in bucket
        assert bucket["ROI"][0].kind == "metric"
        assert "曝光" in bucket

    def test_walk_skill_skips_else_branch(self):
        """兜底分支不会贡献参数引用（"其他" 不是字段名）。"""
        from app.skills.intelligence.param_index import _walk_skill

        structured = _make_skill(steps=[
            _make_step("step_1", "判断", [
                _make_branch("其他情况", "默认"),
            ]),
        ])
        skill = SimpleNamespace(id="t", name="t", department="EC")
        bucket = _walk_skill(skill, structured)
        assert bucket == {}


# ═══════════════════════════════════════════════════════
# Task #4: cross_skill_conflict
# ═══════════════════════════════════════════════════════

class TestCrossSkillConflict:

    def test_polarity_recognition(self):
        from app.skills.lifecycle.cross_skill_conflict import _polarity

        assert _polarity("绿灯", "加预算") == "positive"
        assert _polarity("红灯", "停投") == "negative"
        assert _polarity("黄灯", "人工复核") == "neutral"
        assert _polarity("待定", "") == "neutral"
        assert _polarity("", "") == "unknown"

    def test_half_lines_overlap_half_lines(self):
        """双半射线 (>, <, >=, <=) 区间相交。"""
        from app.skills.lifecycle.cross_skill_conflict import _half_lines_overlap

        # 两个 > 总有交集
        assert _half_lines_overlap(">", 1.5, ">", 1.2) is True
        assert _half_lines_overlap("<", 1.0, "<", 2.0) is True
        # 一个 > 一个 <：> 阈值 < < 阈值 才有交集
        assert _half_lines_overlap(">", 1.0, "<", 2.0) is True
        assert _half_lines_overlap(">", 2.0, "<", 1.0) is False

    def test_half_lines_overlap_strict_boundary(self):
        """半射线临界点边界严格性 — 旧实现把 >1.0 vs <1.0 当成相交。"""
        from app.skills.lifecycle.cross_skill_conflict import _half_lines_overlap

        assert _half_lines_overlap(">", 1.0, "<", 1.0) is False     # (1,+∞) ∩ (-∞,1) = ∅
        assert _half_lines_overlap(">=", 1.0, "<=", 1.0) is True    # [1,+∞) ∩ (-∞,1] = {1}
        assert _half_lines_overlap(">", 1.0, "<=", 1.0) is False    # (1,+∞) ∩ (-∞,1] = ∅
        assert _half_lines_overlap(">=", 1.0, "<", 1.0) is False    # [1,+∞) ∩ (-∞,1) = ∅

    def test_half_lines_overlap_equal(self):
        """== (单点集) 与其他算子的相交。"""
        from app.skills.lifecycle.cross_skill_conflict import _half_lines_overlap

        assert _half_lines_overlap("==", 1.0, "==", 1.0) is True    # 同点
        assert _half_lines_overlap("==", 1.0, "==", 2.0) is False   # 异点
        assert _half_lines_overlap("==", 1.0, ">", 0.5) is True     # 1.0 ∈ (0.5,+∞)
        assert _half_lines_overlap("==", 1.0, ">", 2.0) is False    # 1.0 ∉ (2.0,+∞)
        assert _half_lines_overlap("==", 1.0, ">=", 1.0) is True    # 1.0 ∈ [1.0,+∞)
        assert _half_lines_overlap("==", 1.0, ">", 1.0) is False    # 1.0 ∉ (1.0,+∞)

    def test_half_lines_overlap_not_equal_critical(self):
        """[C1] != 算子精确建模 — 旧实现保守视为全集导致大量误报。"""
        from app.skills.lifecycle.cross_skill_conflict import _half_lines_overlap

        # != 与 ==: 仅当点重合时不相交
        assert _half_lines_overlap("!=", 1.0, "==", 1.0) is False   # ℝ\{1} ∩ {1} = ∅
        assert _half_lines_overlap("!=", 1.0, "==", 2.0) is True    # ℝ\{1} ∩ {2} = {2}
        assert _half_lines_overlap("==", 1.0, "!=", 1.0) is False   # 反向也对
        assert _half_lines_overlap("==", 1.0, "!=", 2.0) is True

        # != 与 !=: 全集除最多两点 → 仍非空
        assert _half_lines_overlap("!=", 1.0, "!=", 1.0) is True
        assert _half_lines_overlap("!=", 1.0, "!=", 2.0) is True

        # != 与半射线: 半射线含无穷多点, 去掉一点仍非空
        assert _half_lines_overlap("!=", 1.0, ">", 1.5) is True
        assert _half_lines_overlap("!=", 1.0, "<", 0.5) is True
        assert _half_lines_overlap("!=", 1.0, ">=", 0.5) is True
        assert _half_lines_overlap("!=", 1.0, "<=", 0.5) is True

    def test_extract_rules_from_skill(self):
        from app.skills.lifecycle.cross_skill_conflict import _extract_rules_from_skill

        structured = _make_skill(steps=[
            _make_step("1", "判断", [
                _make_branch("ROI > 1.5", "绿灯", "加预算"),
                _make_branch("ROI < 0.8", "红灯", "停投"),
            ]),
        ])
        skill = SimpleNamespace(id="A", name="SkillA", department="EC")
        rules = _extract_rules_from_skill(skill, structured)
        assert len(rules) == 2
        assert {r.metric for r in rules} == {"ROI"}
        assert {r.polarity for r in rules} == {"positive", "negative"}

    @pytest.mark.asyncio
    async def test_build_conflict_report_finds_opposite_intervals(self, monkeypatch):
        """两个 Skill 对同一 metric 的相交区间给出相反结论 → 冲突。"""
        from app.skills import cross_skill_conflict as csc
        from app.skills.core.parser import skill_parser

        skill_a = SimpleNamespace(id="A", name="SkillA", department="EC", status="active")
        skill_b = SimpleNamespace(id="B", name="SkillB", department="EC", status="active")

        struct_a = _make_skill(steps=[
            _make_step("1", "ROI判断", [
                _make_branch("ROI > 1.5", "绿灯", "加预算"),
                _make_branch("其他情况", "红灯", "停"),
            ]),
        ], frontmatter={"name": "SkillA", "department": "EC"})
        struct_b = _make_skill(steps=[
            _make_step("1", "ROI判断", [
                _make_branch("ROI > 1.2", "红灯", "停投"),
                _make_branch("其他情况", "绿灯", "继续"),
            ]),
        ], frontmatter={"name": "SkillB", "department": "EC"})

        md_a = skill_parser.render(struct_a)
        md_b = skill_parser.render(struct_b)

        # mock db.execute 返回两个 skill
        result_obj = MagicMock()
        scalars = MagicMock()
        scalars.all.return_value = [skill_a, skill_b]
        result_obj.scalars.return_value = scalars
        db = MagicMock()
        db.execute = AsyncMock(return_value=result_obj)

        def fake_read(skill_id, _path):
            return md_a if skill_id == "A" else md_b
        monkeypatch.setattr(csc.git_service, "read_file", fake_read)

        conflicts = await csc.build_conflict_report(db)
        assert len(conflicts) >= 1
        c = conflicts[0]
        assert c.metric == "ROI"
        assert {c.rule_a["skill_id"], c.rule_b["skill_id"]} == {"A", "B"}
        assert c.rule_a["polarity"] != c.rule_b["polarity"]

    @pytest.mark.asyncio
    async def test_no_conflict_when_same_polarity(self, monkeypatch):
        """同极性不算冲突。"""
        from app.skills import cross_skill_conflict as csc
        from app.skills.core.parser import skill_parser

        struct = _make_skill(steps=[
            _make_step("1", "ROI判断", [
                _make_branch("ROI > 1.5", "绿灯", "加预算"),
            ]),
        ])
        md = skill_parser.render(struct)

        s1 = SimpleNamespace(id="A", name="A", department="EC", status="active")
        s2 = SimpleNamespace(id="B", name="B", department="EC", status="active")
        result_obj = MagicMock()
        scalars = MagicMock()
        scalars.all.return_value = [s1, s2]
        result_obj.scalars.return_value = scalars
        db = MagicMock()
        db.execute = AsyncMock(return_value=result_obj)
        monkeypatch.setattr(csc.git_service, "read_file", lambda *a, **k: md)

        conflicts = await csc.build_conflict_report(db)
        # 两个 Skill 都说"绿灯加预算"，应无冲突
        assert conflicts == []

    @pytest.mark.asyncio
    async def test_no_conflict_when_neutral_polarity(self, monkeypatch):
        """[M9] neutral 极性 (黄灯/复核) 不应触发冲突 — 只有 positive vs negative 才算。"""
        from app.skills import cross_skill_conflict as csc
        from app.skills.core.parser import skill_parser

        struct_pos = _make_skill(steps=[_make_step("1", "j", [
            _make_branch("ROI > 1.5", "绿灯", "加预算"),
        ])], frontmatter={"name": "P", "department": "EC"})
        struct_neutral = _make_skill(steps=[_make_step("1", "j", [
            _make_branch("ROI > 1.2", "黄灯", "人工复核"),
        ])], frontmatter={"name": "N", "department": "EC"})

        md_p = skill_parser.render(struct_pos)
        md_n = skill_parser.render(struct_neutral)

        s_p = SimpleNamespace(id="P", name="P", department="EC", status="active")
        s_n = SimpleNamespace(id="N", name="N", department="EC", status="active")
        result_obj = MagicMock()
        result_obj.scalars.return_value = MagicMock(all=lambda: [s_p, s_n])
        db = MagicMock()
        db.execute = AsyncMock(return_value=result_obj)

        def fake_read(skill_id, _path):
            return md_p if skill_id == "P" else md_n
        monkeypatch.setattr(csc.git_service, "read_file", fake_read)

        conflicts = await csc.build_conflict_report(db)
        assert conflicts == []  # positive vs neutral 不算冲突

    @pytest.mark.asyncio
    async def test_three_skills_pairwise_conflicts(self, monkeypatch):
        """[M9] 3 个 Skill 应产生 n*(n-1)/2 = 3 对潜在比较, 实际命中由极性 + 区间决定。"""
        from app.skills import cross_skill_conflict as csc
        from app.skills.core.parser import skill_parser

        # A: positive, B: negative, C: negative — A vs B 冲突, A vs C 冲突, B vs C 同极性不算
        struct_a = _make_skill(steps=[_make_step("1", "j", [
            _make_branch("ROI > 1.5", "绿灯", "加预算"),
        ])], frontmatter={"name": "A", "department": "EC"})
        struct_b = _make_skill(steps=[_make_step("1", "j", [
            _make_branch("ROI > 1.0", "红灯", "停投"),
        ])], frontmatter={"name": "B", "department": "EC"})
        struct_c = _make_skill(steps=[_make_step("1", "j", [
            _make_branch("ROI > 1.2", "红灯", "停投"),
        ])], frontmatter={"name": "C", "department": "EC"})

        skills = [
            (SimpleNamespace(id="A", name="A", department="EC", status="active"), struct_a),
            (SimpleNamespace(id="B", name="B", department="EC", status="active"), struct_b),
            (SimpleNamespace(id="C", name="C", department="EC", status="active"), struct_c),
        ]
        rendered = {s[0].id: skill_parser.render(s[1]) for s in skills}

        result_obj = MagicMock()
        result_obj.scalars.return_value = MagicMock(all=lambda: [s[0] for s in skills])
        db = MagicMock()
        db.execute = AsyncMock(return_value=result_obj)
        monkeypatch.setattr(csc.git_service, "read_file", lambda sid, _p: rendered[sid])

        conflicts = await csc.build_conflict_report(db)
        # A(positive) vs B(negative) 相交 → 1 个冲突
        # A(positive) vs C(negative) 相交 → 1 个冲突
        # B(negative) vs C(negative) 同极性 → 不算
        assert len(conflicts) == 2
        # 验证 A 出现在两个冲突里
        a_appearances = sum(1 for c in conflicts
                            if "A" in {c.rule_a["skill_id"], c.rule_b["skill_id"]})
        assert a_appearances == 2

    @pytest.mark.asyncio
    async def test_broken_skill_md_does_not_block_scan(self, monkeypatch):
        """[M8/M9] 单个坏 SKILL.md 不应阻断全库扫描, 应记 warning 后跳过。"""
        from app.skills import cross_skill_conflict as csc
        from app.skills.core.parser import skill_parser

        # 一个正常 skill, 一个 read 时抛异常的坏 skill
        struct_good = _make_skill(steps=[_make_step("1", "j", [
            _make_branch("ROI > 1.5", "绿灯", "加预算"),
        ])], frontmatter={"name": "good", "department": "EC"})
        good_md = skill_parser.render(struct_good)

        s_good = SimpleNamespace(id="good", name="good", department="EC", status="active")
        s_bad = SimpleNamespace(id="bad", name="bad", department="EC", status="active")
        result_obj = MagicMock()
        result_obj.scalars.return_value = MagicMock(all=lambda: [s_good, s_bad])
        db = MagicMock()
        db.execute = AsyncMock(return_value=result_obj)

        def fake_read(skill_id, _path):
            if skill_id == "bad":
                raise IOError("disk error simulating broken file")
            return good_md
        monkeypatch.setattr(csc.git_service, "read_file", fake_read)

        # 不应抛异常 — bad skill 被跳过, good skill 仍参与扫描
        conflicts = await csc.build_conflict_report(db)
        assert isinstance(conflicts, list)  # 至少返回了, 没崩
