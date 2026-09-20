"""CostTracker 单元 + 集成测试。

覆盖：
- 成本计算公式（各种 model 定价 + cache）
- record() 写入 usage_logs
- summary() 多维度聚合
- top_skills / daily_costs 排序
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from app.common.cost_tracker import (
    CostTracker,
    UsageEvent,
    DEFAULT_PRICING,
    cost_tracker as global_tracker,
    _match_pricing,
)


# ===== 1. 定价匹配 =====

class TestPricingMatch:
    def test_exact_match(self):
        p = _match_pricing("claude-opus-4-6")
        assert p["input"] == 15.0

    def test_prefix_match(self):
        """带版本号后缀也能匹配到基础模型"""
        p = _match_pricing("claude-opus-4-6-20260101")
        assert p["input"] == 15.0

    def test_unknown_falls_back_to_default(self):
        p = _match_pricing("brand-new-model-xyz")
        assert p == DEFAULT_PRICING["default"]

    def test_empty_falls_back_to_default(self):
        assert _match_pricing("") == DEFAULT_PRICING["default"]


# ===== 2. 成本计算 =====

class TestCalcCost:
    def test_basic_input_output(self):
        tracker = CostTracker()
        event = UsageEvent(
            model="claude-haiku-4-5",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        cost = tracker.calc_cost(event)
        # haiku: input 0.8, output 4.0 → 0.8 + 4.0 = 4.8
        assert cost == Decimal("4.800000")

    def test_with_cache(self):
        tracker = CostTracker()
        event = UsageEvent(
            model="claude-opus-4-6",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=1_000_000,
            cache_write_tokens=1_000_000,
        )
        # opus: 15 + 75 + 1.5 + 18.75 = 110.25
        cost = tracker.calc_cost(event)
        assert cost == Decimal("110.250000")

    def test_zero_tokens(self):
        tracker = CostTracker()
        event = UsageEvent(model="gpt-4o")
        cost = tracker.calc_cost(event)
        assert cost == Decimal("0")

    def test_unknown_model_uses_default(self):
        tracker = CostTracker()
        event = UsageEvent(
            model="some-unknown-model",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        # default: input 1.0, output 3.0 → 4.0
        cost = tracker.calc_cost(event)
        assert cost == Decimal("4.000000")


# ===== 3. record() 写入 DB =====

class TestRecord:
    @pytest.mark.asyncio
    async def test_record_writes_to_db(self, client):
        from app.common.models import UsageLog
        from sqlalchemy import select
        import app.database as db_mod

        tracker = CostTracker()
        event = UsageEvent(
            user_id="test_user",
            department="EC",
            skill_id="TEST-SKILL",
            call_source="agent_chat",
            model="claude-haiku-4-5",
            input_tokens=1000,
            output_tokens=500,
            duration_ms=1234,
        )
        cost = await tracker.record(event)
        assert cost > 0

        # 验证 DB 有这条记录
        async with db_mod.async_session_factory() as session:
            result = await session.execute(
                select(UsageLog).where(UsageLog.user_id == "test_user")
            )
            rows = result.scalars().all()
            assert len(rows) == 1
            assert rows[0].skill_id == "TEST-SKILL"
            assert rows[0].input_tokens == 1000
            assert rows[0].output_tokens == 500
            assert rows[0].cost_usd == cost

    @pytest.mark.asyncio
    async def test_record_handles_db_error_gracefully(self, client, monkeypatch):
        """DB 写入失败时只 warning，不抛异常"""
        from app.common.cost_tracker import CostTracker
        import app.common.cost_tracker as ct_module

        # mock async_session_factory 抛异常
        class FakeFailing:
            async def __aenter__(self):
                raise RuntimeError("模拟 DB 失败")
            async def __aexit__(self, *args):
                return None

        monkeypatch.setattr(
            ct_module._db_mod, "async_session_factory",
            lambda: FakeFailing(),
        )

        tracker = CostTracker()
        event = UsageEvent(model="gpt-4o", input_tokens=100)
        cost = await tracker.record(event)
        # 不应抛异常，且仍返回计算成本
        assert cost > 0

    @pytest.mark.asyncio
    async def test_record_reads_system_config_pricing(self, client):
        from app.common.models import SystemConfig, UsageLog
        from sqlalchemy import select
        import app.database as db_mod

        async with db_mod.async_session_factory() as session:
            session.add(SystemConfig(
                key="ai.pricing.deepseek-chat",
                value={"input": 2.0, "output": 4.0, "cache_read": 0, "cache_write": 0},
            ))
            await session.commit()

        tracker = CostTracker()
        event = UsageEvent(
            model="deepseek-chat",
            call_source="intelligence_analyze",
            input_tokens=1_000_000,
            output_tokens=500_000,
            prompt_hash="a" * 64,
            metadata_json={"run_id": "run-1"},
        )
        cost = await tracker.record(event)

        assert cost == Decimal("4.000000")
        async with db_mod.async_session_factory() as session:
            row = (await session.execute(select(UsageLog))).scalars().one()
        assert row.prompt_hash == "a" * 64
        assert row.metadata_json["run_id"] == "run-1"


# ===== 4. summary() 聚合查询 =====

class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_aggregates_by_model(self, client):
        tracker = CostTracker()

        # 写入多条不同 model 的记录
        await tracker.record(UsageEvent(
            user_id="u1", model="claude-haiku-4-5",
            input_tokens=1000, output_tokens=500,
            call_source="agent_chat",
        ))
        await tracker.record(UsageEvent(
            user_id="u1", model="claude-haiku-4-5",
            input_tokens=2000, output_tokens=1000,
            call_source="agent_chat",
        ))
        await tracker.record(UsageEvent(
            user_id="u2", model="gpt-4o",
            input_tokens=500, output_tokens=200,
            call_source="reviewer",
        ))

        summary = await tracker.summary()
        assert summary["total_calls"] == 3
        assert summary["total_input_tokens"] == 3500
        assert summary["total_output_tokens"] == 1700
        assert summary["total_cost_usd"] > 0

        # by_model 应该按成本排序
        models = [m["model"] for m in summary["by_model"]]
        assert "claude-haiku-4-5" in models
        assert "gpt-4o" in models

        # by_source
        sources = [s["call_source"] for s in summary["by_source"]]
        assert "agent_chat" in sources
        assert "reviewer" in sources

    @pytest.mark.asyncio
    async def test_summary_filters_by_user(self, client):
        tracker = CostTracker()
        await tracker.record(UsageEvent(user_id="alice", model="gpt-4o", input_tokens=100))
        await tracker.record(UsageEvent(user_id="bob", model="gpt-4o", input_tokens=200))

        alice_summary = await tracker.summary(user_id="alice")
        assert alice_summary["total_calls"] == 1
        assert alice_summary["total_input_tokens"] == 100


# ===== 5. top_skills =====

class TestTopSkills:
    @pytest.mark.asyncio
    async def test_top_skills_orders_by_cost(self, client):
        tracker = CostTracker()
        await tracker.record(UsageEvent(
            skill_id="skill-A", model="claude-opus-4-6",
            input_tokens=10_000_000, output_tokens=5_000_000,
        ))
        await tracker.record(UsageEvent(
            skill_id="skill-B", model="claude-haiku-4-5",
            input_tokens=1000, output_tokens=500,
        ))

        top = await tracker.top_skills(limit=5)
        assert len(top) == 2
        # Opus 调用应该排在前面（成本更高）
        assert top[0]["skill_id"] == "skill-A"
        assert top[0]["cost_usd"] > top[1]["cost_usd"]


# ===== 5b. top_users =====


class TestListCallSources:
    """Codex MEDIUM-D4 修复：动态枚举 call_source"""

    @pytest.mark.asyncio
    async def test_list_call_sources_returns_unique(self, client):
        tracker = CostTracker()
        for source in ["agent_chat", "agent_chat", "workbench_chat", "optimizer"]:
            await tracker.record(UsageEvent(
                user_id="u1", model="gpt-4o", call_source=source,
                input_tokens=100,
            ))
        sources = await tracker.list_call_sources(days=30)
        assert sorted(sources) == ["agent_chat", "optimizer", "workbench_chat"]

    @pytest.mark.asyncio
    async def test_call_sources_endpoint(self, client):
        tracker = CostTracker()
        await tracker.record(UsageEvent(
            user_id="u1", model="gpt-4o", call_source="agent_chat",
            input_tokens=100,
        ))
        resp = await client.get("/api/dashboard/costs/call-sources?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert "agent_chat" in data["items"]


class TestAlertThresholdRobustness:
    """Codex MEDIUM-A4 修复：阈值配置宽容解析"""

    @pytest.mark.asyncio
    async def test_threshold_dict_with_alternative_keys(self, client):
        """支持多种 dict 结构 key"""
        tracker = CostTracker()
        from app.common.models import SystemConfig
        import app.database as db_mod

        # daily_usd key
        async with db_mod.async_session_factory() as session:
            session.add(SystemConfig(
                key="cost.alert.daily_usd",
                value={"daily_usd": 200},
            ))
            await session.commit()

        threshold = await tracker._get_alert_threshold()
        assert threshold == Decimal("200")

    @pytest.mark.asyncio
    async def test_threshold_invalid_format_falls_back(self, client):
        """非法格式（不能转 Decimal）回退到默认值，不抛异常"""
        tracker = CostTracker()
        from app.common.models import SystemConfig
        import app.database as db_mod

        async with db_mod.async_session_factory() as session:
            session.add(SystemConfig(
                key="cost.alert.daily_usd",
                value={"random_key": "abc_not_a_number"},
            ))
            await session.commit()

        from app.common.cost_tracker import DEFAULT_ALERT_THRESHOLD_USD
        threshold = await tracker._get_alert_threshold()
        # 应回退到默认值（不抛 InvalidOperation）
        assert threshold == DEFAULT_ALERT_THRESHOLD_USD


class TestTopUsers:
    @pytest.mark.asyncio
    async def test_top_users_orders_by_cost(self, client):
        tracker = CostTracker()
        # alice 用 opus 大开销
        await tracker.record(UsageEvent(
            user_id="alice", model="claude-opus-4-6",
            input_tokens=10_000_000, output_tokens=5_000_000,
        ))
        # bob 用 haiku 小开销
        await tracker.record(UsageEvent(
            user_id="bob", model="claude-haiku-4-5",
            input_tokens=1000, output_tokens=500,
        ))

        top = await tracker.top_users(limit=5)
        assert len(top) == 2
        assert top[0]["user_id"] == "alice"
        assert top[0]["cost_usd"] > top[1]["cost_usd"]
        assert top[0]["calls"] == 1
        assert top[0]["input_tokens"] == 10_000_000

    @pytest.mark.asyncio
    async def test_top_users_excludes_null_user(self, client):
        """user_id 为 None 的记录不应出现在 top_users"""
        tracker = CostTracker()
        await tracker.record(UsageEvent(
            user_id=None, model="claude-opus-4-6",
            input_tokens=10_000_000,
        ))
        await tracker.record(UsageEvent(
            user_id="alice", model="claude-haiku-4-5",
            input_tokens=1000,
        ))
        top = await tracker.top_users(limit=10)
        assert len(top) == 1
        assert top[0]["user_id"] == "alice"

    @pytest.mark.asyncio
    async def test_top_users_endpoint(self, client):
        """HTTP 端点 /dashboard/costs/top-users 可达"""
        tracker = CostTracker()
        await tracker.record(UsageEvent(
            user_id="alice", model="claude-haiku-4-5",
            input_tokens=10_000, output_tokens=5_000,
        ))
        resp = await client.get("/api/dashboard/costs/top-users?limit=5&days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["days"] == 7
        assert len(data["items"]) >= 1
        assert data["items"][0]["user_id"] == "alice"


# ===== 5c. report 详细报表 =====


class TestReport:
    @pytest.mark.asyncio
    async def test_report_groups_by_user(self, client):
        tracker = CostTracker()
        await tracker.record(UsageEvent(
            user_id="alice", department="EC", model="claude-haiku-4-5",
            input_tokens=10_000, output_tokens=5_000, call_source="agent_chat",
        ))
        await tracker.record(UsageEvent(
            user_id="bob", department="EC", model="claude-haiku-4-5",
            input_tokens=20_000, output_tokens=10_000, call_source="agent_chat",
        ))
        report = await tracker.report(group_by="user_id")
        assert report["group_by"] == "user_id"
        assert report["total_calls"] == 2
        keys = [item["key"] for item in report["items"]]
        assert "alice" in keys
        assert "bob" in keys

    @pytest.mark.asyncio
    async def test_report_filters_by_department(self, client):
        tracker = CostTracker()
        await tracker.record(UsageEvent(
            user_id="alice", department="EC", model="gpt-4o",
            input_tokens=10_000,
        ))
        await tracker.record(UsageEvent(
            user_id="bob", department="OPS", model="gpt-4o",
            input_tokens=10_000,
        ))
        report = await tracker.report(department="EC", group_by="user_id")
        assert report["total_calls"] == 1
        assert report["items"][0]["key"] == "alice"

    @pytest.mark.asyncio
    async def test_report_invalid_group_by_raises(self, client):
        """Codex MEDIUM-B1 修复：未知 group_by 应 raise ValueError，不再静默回退"""
        tracker = CostTracker()
        with pytest.raises(ValueError, match="group_by"):
            await tracker.report(group_by="'; DROP TABLE--")
        with pytest.raises(ValueError, match="group_by"):
            await tracker.report(group_by="unknown_column")

    @pytest.mark.asyncio
    async def test_report_endpoint(self, client):
        tracker = CostTracker()
        await tracker.record(UsageEvent(
            user_id="alice", department="EC", model="gpt-4o",
            input_tokens=10_000, output_tokens=5_000,
        ))
        resp = await client.get("/api/dashboard/costs/report?days=7&group_by=department")
        assert resp.status_code == 200
        data = resp.json()
        assert data["group_by"] == "department"
        assert data["total_calls"] >= 1
        # truncated 字段必须存在
        assert "truncated" in data
        assert "total_groups" in data

    @pytest.mark.asyncio
    async def test_report_endpoint_invalid_group_by_returns_422(self, client):
        """API 层返回 422 而非 200 包装错误数据"""
        resp = await client.get("/api/dashboard/costs/report?group_by=evil")
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "INVALID_GROUP_BY"

    @pytest.mark.asyncio
    async def test_report_truncated_flag(self, client):
        """Codex MEDIUM-D1 修复：返回结果数 ≥ limit 时 truncated=True"""
        tracker = CostTracker()
        # 写入 6 个不同 user
        for i in range(6):
            await tracker.record(UsageEvent(
                user_id=f"user{i}", model="gpt-4o", input_tokens=1000,
            ))
        report = await tracker.report(group_by="user_id", limit=3)
        assert report["truncated"] is True
        assert report["total_groups"] == 6
        assert len(report["items"]) == 3
        assert report["limit"] == 3


# ===== 6. daily_costs =====

class TestDailyCosts:
    @pytest.mark.asyncio
    async def test_daily_costs_returns_list(self, client):
        tracker = CostTracker()
        await tracker.record(UsageEvent(
            model="gpt-4o", input_tokens=1000, output_tokens=500,
        ))
        daily = await tracker.daily_costs(days=7)
        assert isinstance(daily, list)
        # 至少应有今天这一天
        assert len(daily) >= 1
        for entry in daily:
            assert "date" in entry
            assert "calls" in entry
            assert "cost_usd" in entry


# ===== 7. 全局单例 =====

class TestGlobalSingleton:
    def test_global_tracker_exists(self):
        assert global_tracker is not None
        assert isinstance(global_tracker, CostTracker)


# ===== 8. Codex 审计追加：cost_context 端到端传递断言 =====

class TestCostContextPropagation:
    """Codex 审计点：调用方传的 cost_context 必须真的传到 cost_tracker.record()
    而不是被 mock 静默吞掉。
    """

    @pytest.mark.asyncio
    async def test_call_llm_propagates_cost_context(self, client, monkeypatch):
        """call_llm 调用时传的 cost_context 应被 _record_usage 收到"""
        from app.common import ai as ai_module
        from app.common.cost_tracker import cost_tracker, UsageEvent

        # 捕获 record 的入参
        captured: list[UsageEvent] = []

        async def fake_record(event: UsageEvent):
            captured.append(event)
            from decimal import Decimal
            return Decimal("0.001")

        monkeypatch.setattr(cost_tracker, "record", fake_record)

        # mock httpx.AsyncClient.post 返回固定 200 + 假 usage
        class FakeResp:
            status_code = 200
            text = ""
            def json(self):
                return {
                    "choices": [{"message": {"content": '{"ok": 1}'}}],
                    "usage": {
                        "prompt_tokens": 123,
                        "completion_tokens": 45,
                        "total_tokens": 168,
                    },
                }

        class FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return None
            async def post(self, *a, **kw):
                return FakeResp()

        monkeypatch.setattr(ai_module.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(
            ai_module, "get_ai_config",
            lambda **kwargs: _make_fake_config(),
        )

        await ai_module.call_llm(
            system="s", user="u", json_mode=True,
            call_source="test_source",
            cost_context={
                "user_id": "alice",
                "department": "EC",
                "skill_id": "SKILL-A",
                "conversation_id": "conv-1",
                "prompt_hash": "abc123def456",
            },
        )

        assert len(captured) == 1
        ev = captured[0]
        assert ev.call_source == "test_source"
        assert ev.user_id == "alice"
        assert ev.department == "EC"
        assert ev.skill_id == "SKILL-A"
        assert ev.conversation_id == "conv-1"
        assert ev.prompt_hash == "abc123def456"
        assert ev.input_tokens == 123
        assert ev.output_tokens == 45

    @pytest.mark.asyncio
    async def test_call_llm_works_without_cost_context(self, client, monkeypatch):
        """没传 cost_context 时不应崩溃，归因字段为 None"""
        from app.common import ai as ai_module
        from app.common.cost_tracker import cost_tracker, UsageEvent

        captured: list[UsageEvent] = []

        async def fake_record(event):
            captured.append(event)
            from decimal import Decimal
            return Decimal("0.001")

        monkeypatch.setattr(cost_tracker, "record", fake_record)

        class FakeResp:
            status_code = 200
            text = ""
            def json(self):
                return {
                    "choices": [{"message": {"content": "hi"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }

        class FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, *a, **kw): return FakeResp()

        monkeypatch.setattr(ai_module.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(ai_module, "get_ai_config", lambda **kwargs: _make_fake_config())

        await ai_module.call_llm(
            system="s", user="u", json_mode=False,
            call_source="no_context_test",
        )

        assert len(captured) == 1
        ev = captured[0]
        assert ev.call_source == "no_context_test"
        assert ev.user_id is None
        assert ev.department is None
        assert ev.skill_id is None


async def _make_fake_config():
    """供 cost_context 测试用的 fake AI config"""
    return {
        "ai.api_base": "http://fake",
        "ai.api_key": "k",
        "ai.model": "test-model",
        "ai.max_tokens": 1000,
        "ai.temperature": 0.3,
        "ai.timeout": 30,
    }


# ===== 9. 成本告警 _check_alert =====
