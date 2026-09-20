"""Phase 2-4 测试：Architect / Retrieval / Coach / Guardian / Reviewer / Motif"""

import pytest
from dataclasses import is_dataclass
from app.skills.core.parser import SkillStructured, DecisionStep, Branch, TestCase, Antipattern


def make_skill(**overrides) -> SkillStructured:
    defaults = {
        "frontmatter": {"name": "test", "department": "EC", "risk_level": "R2"},
        "purpose": "测试",
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


def make_step(id_, branches):
    return DecisionStep(id=id_, name=id_, description="", branches=branches)


def make_branch(cond, conc="通过", act="", nxt=None):
    return Branch(condition=cond, conclusion=conc, action=act, next_step=nxt)


# ═══ Architect ═══

class TestArchitect:
    def test_round_config_exists(self):
        from app.workbench.architect import ROUND_CONFIG
        assert "round1_goal" in ROUND_CONFIG
        assert "round2_signals" in ROUND_CONFIG
        assert "round3_boundaries" in ROUND_CONFIG
        assert "round4_deployment" in ROUND_CONFIG

    @pytest.mark.asyncio
    async def test_fallback_round_questions(self, monkeypatch):
        # _fallback_round 现在是 async,inferred 走 LLM 主路径
        # 测试时把 LLM 打掉,走关键词降级,验证问题集结构仍合法
        import app.workbench.architect as architect

        async def _fake_llm(*a, **kw):
            return None

        monkeypatch.setattr(architect, "call_llm", _fake_llm)

        from app.workbench.architect import _fallback_round
        r = await _fallback_round("round1_goal", "测试")
        assert len(r.questions) >= 2
        assert all(q.id and q.prompt for q in r.questions)

    @pytest.mark.asyncio
    async def test_fallback_all_rounds(self, monkeypatch):
        import app.workbench.architect as architect

        async def _fake_llm(*a, **kw):
            return None

        monkeypatch.setattr(architect, "call_llm", _fake_llm)

        from app.workbench.architect import _fallback_round
        for rid in ["round1_goal", "round2_signals", "round3_boundaries", "round4_deployment"]:
            r = await _fallback_round(rid, "test")
            assert r.id == rid
            assert r.title
            assert len(r.questions) > 0

    @pytest.mark.asyncio
    async def test_interview_round_to_dict(self, monkeypatch):
        import app.workbench.architect as architect

        async def _fake_llm(*a, **kw):
            return None

        monkeypatch.setattr(architect, "call_llm", _fake_llm)

        from app.workbench.architect import _fallback_round
        r = await _fallback_round("round1_goal", "test")
        d = r.to_dict()
        assert "id" in d
        assert "questions" in d


# ═══ Retrieval ═══

class TestRetrieval:
    def test_tokenize(self):
        from app.workbench.retrieval import _tokenize
        t = _tokenize("投放 ROI 判断")
        assert "roi" in t
        assert "投放" in t or "放_" in str(t)  # 中文 bigram

    def test_semantic_similarity(self):
        from app.workbench.retrieval import _semantic_similarity
        s1 = _semantic_similarity("投放 ROI 判断", "投放 ROI 规则")
        s2 = _semantic_similarity("投放 ROI", "域名 查询")
        assert s1 > s2
        assert 0 <= s1 <= 1

    def test_extract_motifs_ladder(self):
        from app.workbench.retrieval import _extract_motifs
        step = make_step("s1", [
            make_branch("ROI > 1.5", "绿灯"),
            make_branch("ROI > 1.0", "黄灯"),
            make_branch("ROI < 0.8", "红灯"),
        ])
        s = make_skill(steps=[step])
        motifs = _extract_motifs(s)
        assert "阶梯决策" in motifs

    def test_quality_score_active_with_tests(self):
        from app.workbench.retrieval import _quality_score
        class MockSkill:
            status = "active"
        s = make_skill(
            steps=[make_step("s1", [make_branch("A", "1"), make_branch("其他", "2")])],
            test_cases=[TestCase(name="t", input_data={}, expected_output={}, assert_rules=[])] * 3,
            antipatterns=[Antipattern(scenario="x", correct_action="y", source="")],
        )
        score = _quality_score(MockSkill(), s)
        assert score > 0.5

    def test_structural_similarity(self):
        from app.workbench.retrieval import _structural_similarity
        a = {"step_count": 3, "total_branches": 9, "has_tests": True, "has_antipatterns": True, "has_data_inputs": False}
        b = {"step_count": 3, "total_branches": 8, "has_tests": True, "has_antipatterns": True, "has_data_inputs": False}
        sim = _structural_similarity(a, b)
        assert sim > 0.8


# ═══ Coach ═══

class TestCoach:
    @pytest.mark.asyncio
    async def test_onboarding_empty_skill(self):
        from app.workbench.coach import handle_event
        s = make_skill()
        resp = await handle_event("open_editor", s)
        # 空 skill 应该推荐添加第一个规则
        assert any("添加" in s.title or "定义" in s.title for s in resp.suggestions)

    @pytest.mark.asyncio
    async def test_branch_changed_missing_else(self):
        from app.workbench.coach import handle_event
        step = make_step("s1", [make_branch("ROI > 1.2", "绿灯")])
        s = make_skill(steps=[step])
        resp = await handle_event("branch_changed", s)
        assert any("兜底" in sug.title for sug in resp.suggestions)

    @pytest.mark.asyncio
    async def test_coverage_gap_no_tests(self):
        from app.workbench.coach import handle_event
        step = make_step("s1", [make_branch("A", "1"), make_branch("其他", "2")])
        s = make_skill(steps=[step], test_cases=[])
        resp = await handle_event("coverage_gap", s)
        assert any("测试" in sug.title for sug in resp.suggestions)

    @pytest.mark.asyncio
    async def test_save_pending_runs_lint(self):
        from app.workbench.coach import handle_event
        step = make_step("s1", [make_branch("ROI > 1.2", "绿灯")])  # 缺 else
        s = make_skill(steps=[step])
        resp = await handle_event("save_pending", s)
        # save_pending 会调 lint
        assert len(resp.suggestions) > 0

    @pytest.mark.asyncio
    async def test_suggestions_limited_to_3(self):
        from app.workbench.coach import handle_event
        steps = [make_step(f"s{i}", [make_branch(f"cond{i}", "通过")]) for i in range(5)]
        s = make_skill(steps=steps)
        resp = await handle_event("branch_changed", s)
        assert len(resp.suggestions) <= 3


# ═══ Guardian ═══

class TestGuardian:
    def test_anomaly_dataclass(self):
        from app.skills.guardian.anomaly_detector import Anomaly
        assert is_dataclass(Anomaly)

    def test_root_cause_dataclass(self):
        from app.skills.guardian.root_cause import RootCause, RootCauseReport
        assert is_dataclass(RootCause)
        assert is_dataclass(RootCauseReport)

    def test_conflict_dataclass(self):
        from app.skills.guardian.conflict_detector import CrossSkillConflict
        assert is_dataclass(CrossSkillConflict)

    def test_action_pairs_conflicting(self):
        from app.skills.guardian.conflict_detector import ACTION_PAIRS_CONFLICTING
        assert ("加预算", "降价") in ACTION_PAIRS_CONFLICTING
        assert ("放行", "拒绝") in ACTION_PAIRS_CONFLICTING

    def test_extract_actions(self):
        from app.skills.guardian.conflict_detector import _extract_actions
        step = make_step("s1", [make_branch("ROI > 1.2", "绿灯", "加预算")])
        s = make_skill(steps=[step])
        actions = _extract_actions(s)
        assert "加预算" in actions

    def test_extract_thresholds_by_term(self):
        from app.skills.guardian.conflict_detector import _extract_thresholds_by_term
        step = make_step("s1", [make_branch("ROI > 1.5", "绿灯")])
        s = make_skill(steps=[step])
        result = _extract_thresholds_by_term(s)
        assert "ROI" in result
        assert 1.5 in result["ROI"]


# ═══ Reviewer ═══

class TestReviewer:
    def test_diff_modules(self):
        from app.workbench.reviewer import _diff_modules
        a = make_skill(purpose="旧目标")
        b = make_skill(purpose="新目标")
        changed = _diff_modules(a, b)
        assert "goal" in changed

    def test_reviewer_dataclasses(self):
        from app.workbench.reviewer import ChangeSummary, RiskAssessment, ReviewerReport
        assert is_dataclass(ChangeSummary)
        assert is_dataclass(RiskAssessment)
        assert is_dataclass(ReviewerReport)

    def test_risk_assessment_defaults(self):
        from app.workbench.reviewer import RiskAssessment
        r = RiskAssessment()
        assert r.behavior_expansion == "low"
        assert r.recommendation == "approve"


# ═══ Motif Library ═══

class TestMotifLibrary:
    def test_motif_templates_complete(self):
        from app.skills.intelligence.motif_library import MOTIF_TEMPLATES
        required = ["ladder_decision", "boundary_protection", "data_fallback", "stage_flow", "conflict_resolution"]
        for r in required:
            assert r in MOTIF_TEMPLATES
            assert "template" in MOTIF_TEMPLATES[r]

    def test_detect_ladder_decision(self):
        from app.skills.intelligence.motif_library import _is_ladder_decision
        step = make_step("s1", [
            make_branch("x > 10", "h"),
            make_branch("x > 5", "m"),
            make_branch("x < 5", "l"),
        ])
        s = make_skill(steps=[step])
        assert _is_ladder_decision(s)

    def test_detect_boundary_protection(self):
        from app.skills.intelligence.motif_library import _is_boundary_protection
        step = make_step("s1", [
            make_branch("x > 100", "异常", "人工复核"),
            make_branch("其他", "正常"),
        ])
        s = make_skill(steps=[step])
        assert _is_boundary_protection(s)

    def test_detect_data_fallback(self):
        from app.skills.intelligence.motif_library import _is_data_fallback
        step = make_step("s1", [
            make_branch("数据缺失", "异常"),
            make_branch("其他", "正常"),
        ])
        s = make_skill(steps=[step])
        assert _is_data_fallback(s)

    def test_detect_stage_flow(self):
        from app.skills.intelligence.motif_library import _is_stage_flow
        steps = [
            make_step(f"s{i}", [make_branch("A", "1"), make_branch("其他", "2")])
            for i in range(3)
        ]
        s = make_skill(steps=steps)
        assert _is_stage_flow(s)


# ═══ Skill Pipeline ═══

class TestSkillPipeline:
    def test_dict_to_structured(self):
        from app.workbench.skill_pipeline import _dict_to_structured
        data = {
            "meta": {"name": "test", "department": "EC"},
            "goal": "目标",
            "rules": [{"id": "s1", "name": "判断", "branches": [{"condition": "A", "conclusion": "1"}]}],
            "params": [],
            "output_table": [],
            "test_cases": [],
            "antipatterns": [],
        }
        s = _dict_to_structured(data)
        assert s.frontmatter["name"] == "test"
        assert s.purpose == "目标"
        assert len(s.steps) == 1

    def test_generated_skill_dataclass(self):
        from app.workbench.skill_pipeline import GeneratedSkill
        g = GeneratedSkill()
        d = g.to_dict()
        assert "skill" in d
        assert "can_publish" in d
