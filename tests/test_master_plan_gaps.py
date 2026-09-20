"""Master plan 缺口修复的单元测试。

覆盖：
- §3.2 infer_from_description 规则化推断
- §3.5 publish_readiness 历史回放 sanity
- §3.7 / §11 telemetry 埋点
- §4.2 ±20% 阈值变化检测
- §4.5 get_param_evidence
- §6.4 根因建议 3 类分桶
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ═══ §3.2 智能推断 ═══

class TestInferFromDescription:
    def test_block_action_has_high_cost(self):
        from app.workbench.architect import infer_from_description
        r = infer_from_description("异常订单应拦截并人工审核")
        assert r["action_type"] == "block"
        assert r["action_cost"] == "high"
        assert r["precision_preference"] == "high"
        assert "manual_review" in r["must_have_branches"]

    def test_recommend_action_is_recall_biased(self):
        from app.workbench.architect import infer_from_description
        r = infer_from_description("根据用户兴趣推荐商品展示排序")
        assert r["action_type"] == "recommend"
        assert r["precision_preference"] == "recall"

    def test_threshold_keyword_triggers_data_derived(self):
        from app.workbench.architect import infer_from_description
        r = infer_from_description("根据 ROI 阈值判断是否加预算")
        assert r["threshold_source"] == "data_derived"

    def test_finance_keyword_adds_data_missing_branch(self):
        from app.workbench.architect import infer_from_description
        r = infer_from_description("订单金额超限时退款")
        assert "data_missing" in r["must_have_branches"]
        assert "else_fallback" in r["must_have_branches"]

    def test_risk_domain_monitors_false_positive(self):
        from app.workbench.architect import infer_from_description
        r = infer_from_description("风控：异常账号拦截登录")
        assert "false_positive_rate" in r["monitoring_metrics"]

    def test_empty_description_returns_defaults(self):
        from app.workbench.architect import infer_from_description
        r = infer_from_description("")
        assert r["action_type"] == "judge"
        assert r["must_have_branches"] == ["else_fallback"]

    @pytest.mark.asyncio
    async def test_fallback_round_uses_inference(self, monkeypatch):
        """_fallback_round 现在是 async,且 inferred 走 LLM 主路径,
        失败时降级到 _infer_from_description_keywords。
        测试用 monkeypatch 把 LLM 调用打掉,验证关键词 fallback 仍然能命中 block。"""
        import app.workbench.architect as architect

        async def _fake_llm(*a, **kw):
            return None  # 触发降级到关键词版

        monkeypatch.setattr(architect, "call_llm", _fake_llm)

        from app.workbench.architect import _fallback_round
        r = await _fallback_round("round1_goal", "风控异常订单拦截")
        assert r.inferred.get("action_type") == "block"
        assert r.inferred.get("precision_preference") == "high"


# ═══ §3.5 publish_readiness 回放 sanity ═══

class TestPublishReadinessReplay:
    def test_report_has_replay_field(self):
        from app.skills.lifecycle.publish_readiness import PublishReadinessReport
        r = PublishReadinessReport(skill_id="x", can_publish=True, blocker_count=0, warning_count=0)
        d = r.to_dict()
        assert "replay" in d

    @pytest.mark.asyncio
    async def test_replay_sanity_skipped_on_exception(self):
        """DecisionLog 查询失败时应返回 skipped 而非抛异常。"""
        from app.skills.lifecycle.publish_readiness import _replay_sanity_check
        with patch("app.database.async_session_factory", side_effect=Exception("DB down")):
            result = await _replay_sanity_check("test-skill")
        assert result["status"] == "skipped"


# ═══ §3.7 / §11 Telemetry ═══

class TestTelemetry:
    def setup_method(self):
        from app.common.telemetry import reset_memory_metrics
        reset_memory_metrics()

    def test_record_ttfr(self):
        from app.common.telemetry import record_ttfr, get_memory_snapshot
        record_ttfr(45.5, source="swarm")
        snap = get_memory_snapshot()
        assert snap["histograms"]["ttfr_seconds:swarm"] == [45.5]

    def test_record_first_pass_quality(self):
        from app.common.telemetry import record_first_pass_quality, get_memory_snapshot
        record_first_pass_quality(True, source="architect")
        record_first_pass_quality(False, source="architect")
        record_first_pass_quality(True, source="architect")
        snap = get_memory_snapshot()
        assert snap["counters"]["first_pass_quality:pass:architect"] == 2
        assert snap["counters"]["first_pass_quality:fail:architect"] == 1

    def test_record_coach_suggestion_accepted(self):
        from app.common.telemetry import record_coach_suggestion, get_memory_snapshot
        record_coach_suggestion(accepted=True, action="complete_branches")
        record_coach_suggestion(accepted=False, action="extract_param")
        snap = get_memory_snapshot()
        assert snap["counters"]["coach_suggestion:yes:complete_branches"] == 1
        assert snap["counters"]["coach_suggestion:no:extract_param"] == 1

    def test_record_publish_blocked(self):
        from app.common.telemetry import record_publish_blocked, get_memory_snapshot
        record_publish_blocked(reason="quality_gate")
        record_publish_blocked(reason="quality_gate")
        snap = get_memory_snapshot()
        assert snap["counters"]["publish_blocked:quality_gate"] == 2

    def test_time_span_context_manager(self):
        from app.common.telemetry import time_span, record_ttfr, get_memory_snapshot
        import time
        with time_span(record_ttfr, source="test"):
            time.sleep(0.01)
        snap = get_memory_snapshot()
        assert len(snap["histograms"]["ttfr_seconds:test"]) == 1
        assert snap["histograms"]["ttfr_seconds:test"][0] >= 0.01


# ═══ §4.2 ±20% 阈值变化 ═══

class TestParamImpactThreshold:
    def _make_structured_with_param(self, param_name="roi_ratio"):
        from app.skills.core.parser import SkillStructured, DecisionStep, Branch
        return SkillStructured(
            frontmatter={"name": "t"}, purpose="test",
            steps=[DecisionStep(id="s1", name="s1", description="",
                                branches=[Branch(condition=f"ROI > {{{param_name}}}", conclusion="绿灯", action="", next_step=None)])],
            antipatterns=[], output_definition=[], data_inputs=[], test_cases=[],
            raw_sections={}, custom_sections={},
        )

    @pytest.mark.asyncio
    async def test_major_change_marked_high_severity(self):
        from app.workbench.coach import handle_event
        s = self._make_structured_with_param()
        resp = await handle_event("param_changed", s, context={
            "param_name": "roi_ratio",
            "old_value": 1.0,
            "new_value": 1.3,  # +30%
        })
        # 找到我们关心的建议
        impact_sug = next((sg for sg in resp.suggestions if "roi_ratio" in sg.id), None)
        assert impact_sug is not None
        assert impact_sug.severity == "high"
        assert impact_sug.evidence.get("is_major_change") is True

    @pytest.mark.asyncio
    async def test_minor_change_marked_medium(self):
        from app.workbench.coach import handle_event
        s = self._make_structured_with_param()
        resp = await handle_event("param_changed", s, context={
            "param_name": "roi_ratio",
            "old_value": 1.0,
            "new_value": 1.05,  # +5%
        })
        impact_sug = next((sg for sg in resp.suggestions if "roi_ratio" in sg.id), None)
        assert impact_sug is not None
        assert impact_sug.severity == "medium"
        assert impact_sug.evidence.get("is_major_change") is False

    @pytest.mark.asyncio
    async def test_non_numeric_values_fall_back_to_medium(self):
        from app.workbench.coach import handle_event
        s = self._make_structured_with_param()
        resp = await handle_event("param_changed", s, context={
            "param_name": "roi_ratio",
            "old_value": "old",
            "new_value": "new",
        })
        impact_sug = next((sg for sg in resp.suggestions if "roi_ratio" in sg.id), None)
        assert impact_sug is not None
        assert impact_sug.severity == "medium"  # 非数值 → 无法判断 ±20%


# ═══ §4.5 Param Evidence ═══

class TestParamEvidence:
    @pytest.mark.asyncio
    async def test_empty_logs_returns_not_enough(self):
        from app.skills.intelligence.ai_service import get_param_evidence
        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = []
        db = MagicMock()
        db.execute = AsyncMock(return_value=exec_result)
        with patch("app.skills.ai_service.git_service") as gs, \
             patch("app.skills.ai_service.validate_skill_id"):
            gs.read_file.return_value = "roi_ratio: 1.2"
            r = await get_param_evidence(db, "test", "roi_ratio", days=30)
        assert r["enough_data"] is False
        assert r["sample_size"] == 0
        assert r["current_value"] == 1.2

    @pytest.mark.asyncio
    async def test_rate_computation(self):
        from app.skills.intelligence.ai_service import get_param_evidence
        # 模拟 25 条日志：15 采纳 / 10 驳回
        mock_logs = []
        for i in range(15):
            l = MagicMock()
            l.user_action = "completed"
            l.input_snapshot = {"other": i}
            l.suggested_action = {}
            l.business_impact = None
            l.created_at = None
            mock_logs.append(l)
        for i in range(10):
            l = MagicMock()
            l.user_action = "rejected"
            l.input_snapshot = {"other": i}
            l.suggested_action = {}
            l.business_impact = None
            l.created_at = None
            mock_logs.append(l)
        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = mock_logs
        db = MagicMock()
        db.execute = AsyncMock(return_value=exec_result)

        with patch("app.skills.ai_service.git_service") as gs, \
             patch("app.skills.ai_service.validate_skill_id"), \
             patch("app.skills.ai_service.call_llm_cached", new=AsyncMock(return_value=None)):
            gs.read_file.return_value = "roi_ratio: 1.2"
            r = await get_param_evidence(db, "test", "roi_ratio", days=30)

        assert r["sample_size"] == 25
        assert r["enough_data"] is True
        assert r["distribution"]["completed"] == 15
        assert r["distribution"]["rejected"] == 10
        assert r["adoption_rate"] == 0.6
        assert r["rejection_rate"] == 0.4


# ═══ §6.4 根因 3 类分桶 ═══

class TestRootCauseCategories:
    @pytest.mark.asyncio
    async def test_branch_anomaly_creates_param_tunable_patch(self):
        from app.skills.guardian.root_cause import analyze_root_cause
        from app.skills.guardian.anomaly_detector import Anomaly
        a = Anomaly(
            skill_id="x", dimension="branch", segment="step_1",
            metric="hit_rate", baseline=0.1, current=0.5,
            z_score=4.0, severity="high", description="branch spike",
        )
        # git_service 在函数内部 import，patch 源模块
        with patch("app.skills.git_service.git_service.log", return_value=[]):
            report = await analyze_root_cause(MagicMock(), "x", a)
        cats = [p.get("category") for p in report.candidate_patches]
        assert "param_tunable" in cats
        assert "rule_missing_branch" in cats  # severity=high → emergency_stop

    @pytest.mark.asyncio
    async def test_overall_failure_anomaly_creates_data_source_patches(self):
        from app.skills.guardian.root_cause import analyze_root_cause
        from app.skills.guardian.anomaly_detector import Anomaly
        a = Anomaly(
            skill_id="x", dimension="overall", segment="全部",
            metric="failure_rate", baseline=0.05, current=0.2,
            z_score=3.0, severity="high", description="failure spike",
        )
        with patch("app.skills.git_service.git_service.log", return_value=[]):
            report = await analyze_root_cause(MagicMock(), "x", a)
        cats = [p.get("category") for p in report.candidate_patches]
        assert "data_source_issue" in cats
        assert "rule_missing_branch" in cats
