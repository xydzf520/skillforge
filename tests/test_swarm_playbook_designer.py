"""Phase 4 长期项：Swarm 4-Agent + Playbook Designer 测试"""

import pytest
from dataclasses import is_dataclass
from unittest.mock import AsyncMock, MagicMock, patch


# ═══ Swarm 4-Agent ═══

class TestSwarmDataclasses:
    def test_swarm_result_dataclass(self):
        from app.workbench.swarm import SwarmResult
        assert is_dataclass(SwarmResult)
        r = SwarmResult()
        d = r.to_dict()
        assert "skill" in d
        assert "explorer_findings" in d
        assert "rules_draft" in d
        assert "tests_notes" in d
        assert "verifier_issues" in d
        assert "lint_report" in d
        assert "can_publish" in d
        assert "leader_summary" in d


class TestSwarmTestsAgent:
    def test_tests_agent_empty(self):
        from app.workbench.swarm import _tests_agent
        assert _tests_agent({}) == []

    def test_tests_agent_no_branches(self):
        from app.workbench.swarm import _tests_agent
        # 没有任何分支 → 不产生建议
        notes = _tests_agent({"rules": [], "test_cases": []})
        assert notes == []

    def test_tests_agent_missing_tests(self):
        from app.workbench.swarm import _tests_agent
        skill = {
            "rules": [
                {"branches": [{"condition": "A"}, {"condition": "B"}, {"condition": "C"}]},
            ],
            "test_cases": [{"name": "t1"}],
            "antipatterns": [],
        }
        notes = _tests_agent(skill)
        # 有 3 分支 1 测试 → 覆盖率 33% → 有建议
        assert any("33%" in n or "测试覆盖" in n for n in notes)
        # 无反例 → 有建议
        assert any("反例" in n for n in notes)

    def test_tests_agent_full_coverage(self):
        from app.workbench.swarm import _tests_agent
        skill = {
            "rules": [{"branches": [{"condition": "A"}]}],
            "test_cases": [{"name": "t1"}],
            "antipatterns": [{"scenario": "x"}],
        }
        notes = _tests_agent(skill)
        assert notes == []


class TestSwarmLeaderMerge:
    def test_merge_empty_rules(self):
        from app.workbench.swarm import _leader_merge
        skill, summary = _leader_merge("任务", {}, {}, [], [])
        assert skill == {}
        assert "放弃" in summary

    def test_merge_writes_verifier_issues_as_antipatterns(self):
        from app.workbench.swarm import _leader_merge
        rules = {
            "meta": {"name": "test"},
            "rules": [{"id": "s1", "branches": []}],
            "antipatterns": [{"scenario": "原有反例"}],
        }
        verifier_issues = [
            {"severity": "critical", "title": "未处理 null", "suggestion": "加兜底", "category": "boundary"},
            {"severity": "low", "title": "小问题"},  # 不应该被写入
        ]
        skill, summary = _leader_merge("测试目标", {}, rules, [], verifier_issues)
        aps = skill.get("antipatterns", [])
        assert len(aps) == 2  # 原有 1 + critical 1
        assert any("未处理 null" in ap.get("scenario", "") for ap in aps)
        assert any("Verifier" in ap.get("source", "") for ap in aps)

    def test_merge_writes_references_to_meta(self):
        from app.workbench.swarm import _leader_merge
        rules = {"meta": {"name": "test"}, "rules": []}
        explorer = {
            "similar_skills": [
                {"skill_id": "ec-pricing-01", "name": "定价"},
                {"skill_id": "ec-budget-02", "name": "预算"},
            ],
            "motifs": [{"id": "ladder_decision", "name": "阶梯决策"}],
        }
        skill, _ = _leader_merge("目标", explorer, rules, [], [])
        meta = skill.get("meta", {})
        refs = meta.get("references", [])
        assert "ec-pricing-01" in refs
        assert "ec-budget-02" in refs
        assert any(r.startswith("motif:") for r in refs)


class TestSwarmOrchestration:
    @pytest.mark.asyncio
    async def test_run_swarm_smoke(self):
        """run_swarm 能把 mock 的各 agent 串起来并产出非空结果。"""
        from app.workbench.swarm import run_swarm

        mock_rules = {
            "meta": {"name": "test"},
            "rules": [{"id": "s1", "branches": [{"condition": "A"}, {"condition": "其他"}]}],
            "test_cases": [{"name": "t1"}],
            "antipatterns": [],
        }

        with patch("app.workbench.swarm._rules_agent", new=AsyncMock(return_value=mock_rules)), \
             patch("app.workbench.swarm._explorer_agent", new=AsyncMock(return_value={"similar_skills": [], "motifs": []})), \
             patch("app.workbench.swarm._verifier_agent", new=AsyncMock(return_value=[])):
            result = await run_swarm(db=MagicMock(), description="test goal")
        assert result.skill.get("meta", {}).get("name") == "test"
        assert "Swarm" in result.leader_summary


# ═══ Playbook Designer ═══

class TestPlaybookDesignDataclasses:
    def test_playbook_design_dataclass(self):
        from app.playbooks.designer import PlaybookDesign, PlaybookStep
        assert is_dataclass(PlaybookDesign)
        assert is_dataclass(PlaybookStep)
        d = PlaybookDesign(goal="test")
        assert "steps" in d.to_dict()
        assert "reasoning" in d.to_dict()
        assert "candidate_skills" in d.to_dict()

    def test_playbook_step_to_dict(self):
        from app.playbooks.designer import PlaybookStep
        s = PlaybookStep(id="step_1", skill_id="s1", skill_name="测试", purpose="第一步", depends_on=["step_0"])
        d = s.to_dict()
        assert d["id"] == "step_1"
        assert d["skill_id"] == "s1"
        assert d["depends_on"] == ["step_0"]
        assert d["on_failure"] == "terminate"


class TestPlaybookDesignerLogic:
    @pytest.mark.asyncio
    async def test_no_candidates_returns_warning(self):
        """没有候选 Skill 时返回警告而不是报错。"""
        from app.playbooks.designer import design_playbook
        with patch("app.playbooks.designer._fetch_candidate_skills", new=AsyncMock(return_value=[])):
            design = await design_playbook(MagicMock(), "高 ROI 投放自动化")
        assert design.steps == []
        assert any("未找到" in w for w in design.warnings)

    @pytest.mark.asyncio
    async def test_fallback_when_llm_unavailable(self):
        """LLM 不可用时按相关性降级串行编排（每步依赖前一步）。"""
        from app.playbooks.designer import design_playbook
        candidates = [
            {"skill_id": "s1", "name": "初筛", "description": "", "department": "EC", "score": 0.9},
            {"skill_id": "s2", "name": "校验", "description": "", "department": "EC", "score": 0.8},
            {"skill_id": "s3", "name": "执行", "description": "", "department": "EC", "score": 0.7},
        ]
        with patch("app.playbooks.designer._fetch_candidate_skills", new=AsyncMock(return_value=candidates)), \
             patch("app.playbooks.designer.call_llm", new=AsyncMock(side_effect=Exception("LLM 挂了"))):
            design = await design_playbook(MagicMock(), "test")
        assert len(design.steps) == 3
        assert design.steps[0].id == "step_1"
        assert design.steps[0].skill_id == "s1"
        assert design.steps[0].depends_on == []
        assert design.steps[1].depends_on == ["step_1"]
        assert design.steps[2].depends_on == ["step_2"]
        assert all(s.on_failure == "terminate" for s in design.steps)
        assert any("降级" in w for w in design.warnings)

    @pytest.mark.asyncio
    async def test_rejects_hallucinated_skill_ids(self):
        """LLM 编造不存在的 skill_id 时应被过滤并加警告。"""
        from app.playbooks.designer import design_playbook
        candidates = [
            {"skill_id": "real-skill-01", "name": "真实", "description": "", "department": "EC", "score": 1},
        ]
        fake_llm_resp = {
            "steps": [
                {"id": "step_1", "skill_id": "real-skill-01", "purpose": "真正一步", "depends_on": [], "on_failure": "terminate"},
                {"id": "step_2", "skill_id": "fake-skill-99", "purpose": "伪造一步", "depends_on": ["step_1"], "on_failure": "terminate"},
            ],
            "reasoning": "test",
            "warnings": [],
        }
        with patch("app.playbooks.designer._fetch_candidate_skills", new=AsyncMock(return_value=candidates)), \
             patch("app.playbooks.designer.call_llm", new=AsyncMock(return_value=fake_llm_resp)):
            design = await design_playbook(MagicMock(), "test")
        assert len(design.steps) == 1
        assert design.steps[0].skill_id == "real-skill-01"
        assert any("编造" in w and "fake-skill-99" in w for w in design.warnings)

    @pytest.mark.asyncio
    async def test_valid_llm_response(self):
        """LLM 返回合法编排时完整保留。"""
        from app.playbooks.designer import design_playbook
        candidates = [
            {"skill_id": "ec-filter-01", "name": "初筛", "description": "", "department": "EC", "score": 0.9},
            {"skill_id": "ec-budget-02", "name": "预算", "description": "", "department": "EC", "score": 0.8},
        ]
        llm_resp = {
            "steps": [
                {"id": "step_1", "skill_id": "ec-filter-01", "purpose": "先初筛", "depends_on": [], "on_failure": "terminate"},
                {"id": "step_2", "skill_id": "ec-budget-02", "purpose": "再校预算", "depends_on": ["step_1"], "on_failure": "terminate"},
            ],
            "reasoning": "低成本在前高成本在后",
            "warnings": ["注意预算溢出"],
        }
        with patch("app.playbooks.designer._fetch_candidate_skills", new=AsyncMock(return_value=candidates)), \
             patch("app.playbooks.designer.call_llm", new=AsyncMock(return_value=llm_resp)):
            design = await design_playbook(MagicMock(), "test")
        assert len(design.steps) == 2
        assert design.steps[0].depends_on == []
        assert design.steps[1].depends_on == ["step_1"]
        assert all(s.on_failure == "terminate" for s in design.steps)
        assert "低成本" in design.reasoning
        assert "注意预算溢出" in design.warnings

    @pytest.mark.asyncio
    async def test_invalid_depends_on_dropped(self):
        """依赖非前序步骤时应被过滤。"""
        from app.playbooks.designer import design_playbook
        candidates = [
            {"skill_id": "s1", "name": "一", "description": "", "department": "EC", "score": 1},
            {"skill_id": "s2", "name": "二", "description": "", "department": "EC", "score": 1},
        ]
        llm_resp = {
            "steps": [
                # step_1 依赖尚未出现的 step_2 —— 应被丢弃
                {"id": "step_1", "skill_id": "s1", "purpose": "a", "depends_on": ["step_2"], "on_failure": "terminate"},
                {"id": "step_2", "skill_id": "s2", "purpose": "b", "depends_on": ["step_1"], "on_failure": "terminate"},
            ],
            "reasoning": "",
            "warnings": [],
        }
        with patch("app.playbooks.designer._fetch_candidate_skills", new=AsyncMock(return_value=candidates)), \
             patch("app.playbooks.designer.call_llm", new=AsyncMock(return_value=llm_resp)):
            design = await design_playbook(MagicMock(), "test")
        assert design.steps[0].depends_on == []  # 前置依赖被丢弃
        assert design.steps[1].depends_on == ["step_1"]
        assert any("前序" in w or "丢弃" in w for w in design.warnings)

    @pytest.mark.asyncio
    async def test_invalid_on_failure_normalized(self):
        """on_failure 非白名单值时应规范化为 terminate。"""
        from app.playbooks.designer import design_playbook
        candidates = [{"skill_id": "s1", "name": "一", "description": "", "department": "EC", "score": 1}]
        llm_resp = {
            "steps": [
                {"id": "step_1", "skill_id": "s1", "purpose": "x", "depends_on": [], "on_failure": "skip"},
            ],
            "reasoning": "",
            "warnings": [],
        }
        with patch("app.playbooks.designer._fetch_candidate_skills", new=AsyncMock(return_value=candidates)), \
             patch("app.playbooks.designer.call_llm", new=AsyncMock(return_value=llm_resp)):
            design = await design_playbook(MagicMock(), "test")
        assert design.steps[0].on_failure == "terminate"
