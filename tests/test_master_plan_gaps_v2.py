"""第二轮 master plan 缺口修复的测试。

覆盖：
- §6.2 anomaly_detector 恢复的分支级 + 分群 + rejection 检测
- §6.2 drift_detector KL 散度
- §4.2 reverted_repeatedly 事件
- §5.3 reviewer result_diff
- §4.8 / §11 新增 5 项 KPI
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


# ═══ §6.2 Anomaly Detector — 新增维度 ═══

class TestAnomalyDimensions:
    def test_relative_severity_levels(self):
        from app.skills.guardian.anomaly_detector import _relative_severity
        assert _relative_severity(0.3, 0.05) == "critical"    # 6x
        assert _relative_severity(0.2, 0.1) == "high"         # 2x
        assert _relative_severity(0.16, 0.1) == "medium"      # 1.6x
        assert _relative_severity(0.11, 0.1) == "low"         # 1.1x
        assert _relative_severity(0.2, 0) == "high"           # base 0

    @pytest.mark.asyncio
    async def test_branch_hit_rate_detects_spike(self):
        """分支命中率突变从 10% 升到 50% 时应被检测。"""
        from app.skills.guardian.anomaly_detector import _detect_branch_hit_rate

        now = datetime.utcnow()
        window_start = now - timedelta(hours=24)
        baseline_start = now - timedelta(days=30)

        # 构造 mock 日志：基线时期分支 A 命中 10%，当前时期命中 50%
        logs = []
        # 基线：A 10 次 / B 90 次（A=10%）
        for _ in range(10):
            logs.append(({"step_id": "A"}, now - timedelta(days=15)))
        for _ in range(90):
            logs.append(({"step_id": "B"}, now - timedelta(days=15)))
        # 当前：A 50 次 / B 50 次（A=50%）
        for _ in range(50):
            logs.append(({"step_id": "A"}, now - timedelta(hours=1)))
        for _ in range(50):
            logs.append(({"step_id": "B"}, now - timedelta(hours=1)))

        exec_result = MagicMock()
        exec_result.all.return_value = logs
        db = MagicMock()
        db.execute = AsyncMock(return_value=exec_result)

        anomalies = await _detect_branch_hit_rate(db, "x", window_start, baseline_start)
        assert len(anomalies) >= 1
        branch_a = next((a for a in anomalies if "A" in a.segment), None)
        assert branch_a is not None
        assert branch_a.dimension == "branch"
        assert branch_a.severity in ("high", "medium")

    @pytest.mark.asyncio
    async def test_rejection_rate_detects_increase(self):
        from app.skills.guardian.anomaly_detector import _detect_rejection_rate

        now = datetime.utcnow()
        window_start = now - timedelta(hours=24)
        baseline_start = now - timedelta(days=30)

        # 基线：50 条 5 rejected (10%) / 当前：20 条 10 rejected (50%)
        logs = []
        for i in range(5):
            logs.append(("rejected", now - timedelta(days=15)))
        for i in range(45):
            logs.append(("completed", now - timedelta(days=15)))
        for i in range(10):
            logs.append(("rejected", now - timedelta(hours=1)))
        for i in range(10):
            logs.append(("completed", now - timedelta(hours=1)))

        exec_result = MagicMock()
        exec_result.all.return_value = logs
        db = MagicMock()
        db.execute = AsyncMock(return_value=exec_result)

        anomalies = await _detect_rejection_rate(db, "x", window_start, baseline_start)
        assert len(anomalies) >= 1
        assert anomalies[0].dimension == "rejection"
        assert anomalies[0].severity in ("high", "critical")

    @pytest.mark.asyncio
    async def test_segmented_failure_detects_by_category(self):
        from app.skills.guardian.anomaly_detector import _detect_segmented_failure_rate

        now = datetime.utcnow()
        window_start = now - timedelta(hours=24)
        baseline_start = now - timedelta(days=30)

        # 构造 category=A 的异常：基线 5% 驳回，当前 30% 驳回
        logs = []
        for _ in range(95):
            logs.append(({"category": "A"}, "completed", now - timedelta(days=15)))
        for _ in range(5):
            logs.append(({"category": "A"}, "rejected", now - timedelta(days=15)))
        for _ in range(7):
            logs.append(({"category": "A"}, "completed", now - timedelta(hours=1)))
        for _ in range(3):
            logs.append(({"category": "A"}, "rejected", now - timedelta(hours=1)))

        exec_result = MagicMock()
        exec_result.all.return_value = logs
        db = MagicMock()
        db.execute = AsyncMock(return_value=exec_result)

        anomalies = await _detect_segmented_failure_rate(db, "x", window_start, baseline_start)
        # 应该检测到 category=A 异常
        cat_a = next((a for a in anomalies if a.dimension == "segment:category" and a.segment == "A"), None)
        assert cat_a is not None


# ═══ §6.2 Drift Detector — KL 散度 ═══

class TestDriftDetector:
    def test_kl_divergence_identical(self):
        from app.skills.guardian.drift_detector import _kl_divergence
        assert _kl_divergence({"a": 10, "b": 10}, {"a": 10, "b": 10}) < 0.001

    def test_kl_divergence_different(self):
        from app.skills.guardian.drift_detector import _kl_divergence
        kl = _kl_divergence({"a": 100, "b": 0}, {"a": 0, "b": 100})
        assert kl > 1.0

    def test_kl_severity_classification(self):
        from app.skills.guardian.drift_detector import _kl_severity
        assert _kl_severity(0.6) == "high"
        assert _kl_severity(0.3) == "medium"
        assert _kl_severity(0.05) == "low"

    def test_bucket_numeric(self):
        from app.skills.guardian.drift_detector import _bucket_numeric
        assert _bucket_numeric(15, 10) == "[10,20)"
        assert _bucket_numeric(3.5, 5) == "[0,5)"
        assert _bucket_numeric("not_a_number", 10) == "invalid"

    @pytest.mark.asyncio
    async def test_detect_drift_reports_categorical_shift(self):
        from app.skills.guardian.drift_detector import detect_drift

        now = datetime.utcnow()
        logs = []
        # 基线：category=A 大多数
        for _ in range(80):
            logs.append(({"category": "A"}, now - timedelta(days=5)))
        for _ in range(20):
            logs.append(({"category": "B"}, now - timedelta(days=5)))
        # 当前：category=B 大多数（明显偏移）
        for _ in range(15):
            logs.append(({"category": "A"}, now - timedelta(hours=1)))
        for _ in range(85):
            logs.append(({"category": "B"}, now - timedelta(hours=1)))

        exec_result = MagicMock()
        exec_result.all.return_value = logs
        db = MagicMock()
        db.execute = AsyncMock(return_value=exec_result)

        reports = await detect_drift(db, "x")
        # 应该检测到 category 字段漂移
        cat_report = next((r for r in reports if r.field == "category"), None)
        assert cat_report is not None
        assert cat_report.kl_divergence > 0.1
        assert cat_report.field_type == "categorical"


# ═══ §4.2 reverted_repeatedly ═══

class TestRevertedRepeatedly:
    @pytest.mark.asyncio
    async def test_repeated_revert_generates_suggestion(self):
        from app.workbench.coach import handle_event
        from app.skills.core.parser import SkillStructured
        s = SkillStructured(
            frontmatter={}, purpose="", steps=[], antipatterns=[],
            output_definition=[], data_inputs=[], test_cases=[],
            raw_sections={}, custom_sections={},
        )
        resp = await handle_event("reverted_repeatedly", s, context={
            "location": "param:roi_ratio",
            "revert_count": 4,
            "last_values": ["1.0", "1.2", "1.0", "1.15"],
        })
        assert any("反复修改" in sg.title for sg in resp.suggestions)
        assert any(sg.kind == "evidence_card" for sg in resp.suggestions)

    @pytest.mark.asyncio
    async def test_single_change_does_not_trigger(self):
        from app.workbench.coach import handle_event
        from app.skills.core.parser import SkillStructured
        s = SkillStructured(
            frontmatter={}, purpose="", steps=[], antipatterns=[],
            output_definition=[], data_inputs=[], test_cases=[],
            raw_sections={}, custom_sections={},
        )
        # revert_count < 2 → 不触发
        resp = await handle_event("reverted_repeatedly", s, context={
            "location": "param:x",
            "revert_count": 1,
            "last_values": ["1.0"],
        })
        assert not any("反复修改" in sg.title for sg in resp.suggestions)


# ═══ §5.3 Reviewer result_diff ═══

class TestReviewerResultDiff:
    def test_report_has_result_diff_field(self):
        from app.workbench.reviewer import ReviewerReport
        r = ReviewerReport(skill_id="x")
        d = r.to_dict()
        assert "result_diff" in d
        assert d["result_diff"]["before"] == {}
        assert d["result_diff"]["after"] == {}

    def test_result_distribution_dataclass(self):
        from app.workbench.reviewer import ResultDistribution
        from dataclasses import is_dataclass
        assert is_dataclass(ResultDistribution)
        rd = ResultDistribution(
            before={"绿灯": 30, "红灯": 10},
            after={"绿灯": 40, "红灯": 8},
            sample_size_before=40,
            sample_size_after=48,
        )
        d = rd.to_dict()
        assert d["before"]["绿灯"] == 30
        assert d["sample_size_after"] == 48


# ═══ §4.8 / §11 新增 5 项 KPI ═══

class TestAdditionalKPIs:
    def setup_method(self):
        from app.common.telemetry import reset_memory_metrics
        reset_memory_metrics()

    def test_record_edit_duration(self):
        from app.common.telemetry import record_edit_duration, get_memory_snapshot
        record_edit_duration(120, skill_id="s1")
        record_edit_duration(240, skill_id="s1")
        snap = get_memory_snapshot()
        assert snap["histograms"]["edit_duration:s1"] == [120, 240]

    def test_record_coverage_delta(self):
        from app.common.telemetry import record_coverage_delta, get_memory_snapshot
        record_coverage_delta(0.5, 0.8, skill_id="s1")
        snap = get_memory_snapshot()
        assert abs(snap["histograms"]["coverage_delta:s1"][0] - 0.3) < 0.001

    def test_record_repeated_edit(self):
        from app.common.telemetry import record_repeated_edit, get_memory_snapshot
        record_repeated_edit("param:roi_ratio", 3)
        record_repeated_edit("param:roi_ratio", 2)
        snap = get_memory_snapshot()
        assert snap["counters"]["repeated_edit:param:roi_ratio"] == 5

    def test_record_review_decision_time(self):
        from app.common.telemetry import record_review_decision_time, get_memory_snapshot
        record_review_decision_time(3600, decision="approve")
        record_review_decision_time(7200, decision="reject")
        snap = get_memory_snapshot()
        assert snap["histograms"]["review_decision:approve"] == [3600]
        assert snap["histograms"]["review_decision:reject"] == [7200]

    def test_record_rollback_counter(self):
        from app.common.telemetry import record_rollback, get_memory_snapshot
        record_rollback("skill-01")
        record_rollback("skill-01")
        record_rollback("skill-02")
        snap = get_memory_snapshot()
        assert snap["counters"]["rollback:skill-01"] == 2
        assert snap["counters"]["rollback:skill-02"] == 1
