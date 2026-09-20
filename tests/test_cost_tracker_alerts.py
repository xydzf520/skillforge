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



def _setup_threshold(monkeypatch, tracker, threshold: Decimal):
    """注入固定阈值，避免依赖 SystemConfig DB 数据"""
    async def fake():
        return threshold
    monkeypatch.setattr(tracker, "_get_alert_threshold", fake)


def _setup_recipients(monkeypatch, tracker, recipients: list[str]):
    async def fake(department):
        return recipients
    monkeypatch.setattr(tracker, "_get_alert_recipients", fake)


async def _seed_usage(department: str, cost_total: Decimal, model: str = "gpt-4o"):
    """直接写 usage_logs 模拟既有累计成本

    通过 input_tokens 控制成本（gpt-4o input = 2.5 USD/M）
    """
    from app.common.models import UsageLog
    import app.database as db_mod
    # cost_total / 2.5 USD per 1M tokens
    tokens = int(float(cost_total) / 2.5 * 1_000_000)
    async with db_mod.async_session_factory() as session:
        session.add(UsageLog(
            department=department,
            model=model,
            input_tokens=tokens,
            output_tokens=0,
            cost_usd=cost_total,
            call_source="seed",
        ))
        await session.commit()


class TestCheckAlert:
    """成本告警 _check_alert 行为测试"""

    @pytest.mark.asyncio
    async def test_skip_when_department_none(self, client, monkeypatch):
        """部门为 None 时直接跳过，不查 DB"""
        tracker = CostTracker()

        called = {"threshold": False}
        async def fake_threshold():
            called["threshold"] = True
            return Decimal("100")
        monkeypatch.setattr(tracker, "_get_alert_threshold", fake_threshold)

        await tracker._check_alert(None, Decimal("9999"))
        assert called["threshold"] is False  # 应该提前 return，根本不查阈值

    @pytest.mark.asyncio
    async def test_skip_when_threshold_zero(self, client, monkeypatch):
        """阈值 = 0 视为禁用告警"""
        tracker = CostTracker()
        _setup_threshold(monkeypatch, tracker, Decimal("0"))

        # 即便派 outbox.enqueue 也不会被调用
        from app.dingtalk import outbox as outbox_mod
        called = {"enqueue": False}
        original_enqueue = outbox_mod.outbox.enqueue
        async def fake_enqueue(*a, **kw):
            called["enqueue"] = True
            return 1
        monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

        await tracker._check_alert("EC", Decimal("9999"))
        assert called["enqueue"] is False

    @pytest.mark.asyncio
    async def test_no_alert_when_total_below_threshold(self, client, monkeypatch):
        """累计未超阈值不告警"""
        tracker = CostTracker()
        _setup_threshold(monkeypatch, tracker, Decimal("100"))

        # 已存在 50 USD 累计
        await _seed_usage("EC", Decimal("50"))

        from app.dingtalk import outbox as outbox_mod
        called = {"enqueue": False}
        async def fake_enqueue(*a, **kw):
            called["enqueue"] = True
            return 1
        monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

        await tracker._check_alert("EC", Decimal("10"))
        assert called["enqueue"] is False

    @pytest.mark.asyncio
    async def test_alert_only_on_first_crossing(self, client, monkeypatch):
        """第一次跨过阈值才告警

        说明：_check_alert 在 record() 写完后调用，所以 DB 中已包含本次成本。
        测试通过 seed 模拟"DB 累计 = 104（含本次 5）"的状态。
        """
        tracker = CostTracker()
        _setup_threshold(monkeypatch, tracker, Decimal("100"))
        _setup_recipients(monkeypatch, tracker, ["admin"])

        from app.dingtalk import outbox as outbox_mod
        enqueue_calls = []
        async def fake_enqueue(message_type, recipient, payload, **kw):
            enqueue_calls.append({
                "message_type": message_type,
                "recipient": recipient,
                "payload": payload,
                "related_type": kw.get("related_type"),
                "related_id": kw.get("related_id"),
            })
            return len(enqueue_calls)
        monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

        # DB 累计 = 104（previous=99 ≤ 100, total=104 > 100 → 刚跨过）
        await _seed_usage("EC", Decimal("104"))
        await tracker._check_alert("EC", Decimal("5"))
        assert len(enqueue_calls) == 1
        assert enqueue_calls[0]["message_type"] == "work_notice"
        assert enqueue_calls[0]["related_type"] == "cost_alert"
        assert enqueue_calls[0]["related_id"] == "EC"
        assert enqueue_calls[0]["recipient"] == "admin"
        assert "EC" in enqueue_calls[0]["payload"]["title"]

    @pytest.mark.asyncio
    async def test_no_duplicate_when_already_crossed(self, client, monkeypatch):
        """已经超过阈值的后续调用不重复触发"""
        tracker = CostTracker()
        _setup_threshold(monkeypatch, tracker, Decimal("100"))
        _setup_recipients(monkeypatch, tracker, ["admin"])

        from app.dingtalk import outbox as outbox_mod
        enqueue_calls = []
        async def fake_enqueue(*a, **kw):
            enqueue_calls.append(kw)
            return len(enqueue_calls)
        monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

        # DB 累计 150（含本次 5），previous=145 已超阈值
        await _seed_usage("EC", Decimal("150"))
        await tracker._check_alert("EC", Decimal("5"))
        assert len(enqueue_calls) == 0

    @pytest.mark.asyncio
    async def test_daily_dedup(self, client, monkeypatch):
        """同一日历日同部门已发过告警时不再发（HIGH 修复后从 24h 改为日级去重）"""
        tracker = CostTracker()
        _setup_threshold(monkeypatch, tracker, Decimal("100"))
        _setup_recipients(monkeypatch, tracker, ["admin"])

        from app.dingtalk.outbox import outbox

        # 预先声明今日告警权（模拟 5 分钟前另一个请求已发过）
        await tracker._try_claim_alert(
            "EC", datetime.utcnow().date(), Decimal("100"), Decimal("100")
        )

        # DB 累计 104 → 满足"刚跨过"条件
        await _seed_usage("EC", Decimal("104"))

        enqueue_calls = []
        async def fake_enqueue(*a, **kw):
            enqueue_calls.append(kw)
            return 999
        monkeypatch.setattr(outbox, "enqueue", fake_enqueue)

        await tracker._check_alert("EC", Decimal("5"))
        # 已被认领，不应入队
        assert len(enqueue_calls) == 0

    @pytest.mark.asyncio
    async def test_alert_failure_does_not_affect_record(self, client, monkeypatch):
        """_check_alert 内部异常不应影响 record() 主流程"""
        tracker = CostTracker()

        async def boom(department, cost):
            raise RuntimeError("模拟告警失败")
        monkeypatch.setattr(tracker, "_check_alert", boom)

        event = UsageEvent(
            department="EC",
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
        )
        # 不应抛异常
        cost = await tracker.record(event)
        assert cost > 0

    @pytest.mark.asyncio
    async def test_record_triggers_check_alert(self, client, monkeypatch):
        """record() 应自动调用 _check_alert，传入正确的 department 与本次成本"""
        tracker = CostTracker()
        captured = []

        async def fake_check(dept, cost):
            captured.append((dept, cost))

        monkeypatch.setattr(tracker, "_check_alert", fake_check)

        event = UsageEvent(
            department="EC",
            model="claude-haiku-4-5",
            input_tokens=1000,
            output_tokens=500,
        )
        cost = await tracker.record(event)

        assert len(captured) == 1
        assert captured[0][0] == "EC"
        assert captured[0][1] == cost

    @pytest.mark.asyncio
    async def test_recipients_dict_with_default(self, client, monkeypatch):
        """system_config recipients 是 dict + default 时按部门匹配"""
        tracker = CostTracker()

        from app.common.models import SystemConfig
        import app.database as db_mod

        async with db_mod.async_session_factory() as session:
            session.add(SystemConfig(
                key="cost.alert.recipients",
                value={"EC": ["zhang_ec"], "default": ["admin"]},
            ))
            await session.commit()

        ec_recipients = await tracker._get_alert_recipients("EC")
        assert ec_recipients == ["zhang_ec"]

        other_recipients = await tracker._get_alert_recipients("UNKNOWN_DEPT")
        assert other_recipients == ["admin"]

    @pytest.mark.asyncio
    async def test_threshold_from_system_config(self, client):
        """system_config.cost.alert.daily_usd 应被读取"""
        tracker = CostTracker()

        from app.common.models import SystemConfig
        import app.database as db_mod

        async with db_mod.async_session_factory() as session:
            session.add(SystemConfig(
                key="cost.alert.daily_usd",
                value=250,
            ))
            await session.commit()

        threshold = await tracker._get_alert_threshold()
        assert threshold == Decimal("250")


class TestConcurrentAlertSafety:
    """Codex HIGH 修复：并发场景下成本告警 daily 去重"""

    @pytest.mark.asyncio
    async def test_two_sessions_race_only_one_wins(self, client):
        """Codex 二轮强化测试：两个独立 session 显式争抢同一 (dept, date)，
        断言恰好一个 True 一个 False（PostgreSQL 唯一约束仲裁）"""
        from datetime import date
        import asyncio

        tracker = CostTracker()
        today = date.today()

        # 两个独立任务并发执行（asyncio.gather 在协程层并发，
        # asyncpg pool 会分配独立连接 → 真实 PostgreSQL race）
        results = await asyncio.gather(
            tracker._try_claim_alert("RACE_DEPT", today, Decimal("110"), Decimal("100")),
            tracker._try_claim_alert("RACE_DEPT", today, Decimal("110"), Decimal("100")),
        )
        # 恰好一个成功，一个失败
        assert sum(1 for r in results if r is True) == 1
        assert sum(1 for r in results if r is False) == 1

    @pytest.mark.asyncio
    async def test_concurrent_check_alert_only_once(self, client, monkeypatch):
        """同一部门在同一日历日内 N 个并发请求都跨过阈值，
        最终只应入队 1 条 cost_alert（不能双发）"""
        import asyncio

        tracker = CostTracker()
        _setup_threshold(monkeypatch, tracker, Decimal("100"))
        _setup_recipients(monkeypatch, tracker, ["admin"])

        # 模拟 DB 累计 110（总额持续超过阈值）
        await _seed_usage("EC", Decimal("110"))

        from app.dingtalk import outbox as outbox_mod
        enqueue_calls = []
        async def fake_enqueue(*a, **kw):
            enqueue_calls.append(kw)
            return len(enqueue_calls)
        monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

        # 并发触发 5 次告警检查（每次 cost_added=5，previous=105）
        # 注意：每次调用都满足 "刚跨过" 条件（因为 110-5=105 ≤ 100 是 False，
        # 所以这里要构造正确的数据。改造场景：）
        # 重新设计：先 seed 99，然后并发 5 次 _check_alert(EC, 5)，
        # 但 DB 里 sum 始终是 99（没真的写新记录）。让 _check_alert 内部查询的是 99
        # 这样 total=99，不会触发。
        # 正确测试：应该让每次调用看到的 total 都满足"刚跨过"条件。
        # 这里改用 mock _check_alert 内部 DB 查询返回 fix total。

        # 直接构造场景：seed 累计 105，调用时 cost=5（previous=100 ≤ 100，total=105 > 100 ✓）
        from app.common.models import UsageLog
        import app.database as db_mod
        async with db_mod.async_session_factory() as session:
            from sqlalchemy import delete
            await session.execute(delete(UsageLog))
            await session.commit()
        await _seed_usage("EC", Decimal("105"))

        # 5 个并发任务都会满足 "刚跨过" 条件
        results = await asyncio.gather(*[
            tracker._check_alert("EC", Decimal("5"))
            for _ in range(5)
        ])

        # 并发安全：只应有 1 条入队
        assert len(enqueue_calls) == 1, (
            f"并发场景下应只触发 1 次告警，实际 {len(enqueue_calls)} 次"
        )

    @pytest.mark.asyncio
    async def test_claim_alert_only_succeeds_once(self, client):
        """_try_claim_alert 在同一 (dept, date) 上只能成功一次"""
        from datetime import date
        tracker = CostTracker()

        today = date.today()
        first = await tracker._try_claim_alert("EC", today, Decimal("110"), Decimal("100"))
        second = await tracker._try_claim_alert("EC", today, Decimal("120"), Decimal("100"))
        third = await tracker._try_claim_alert("OPS", today, Decimal("110"), Decimal("100"))

        assert first is True   # 首次声明成功
        assert second is False  # 同一 dept+date 重复声明失败
        assert third is True   # 不同部门可以独立声明


class TestCleanupAlertClaims:
    """Codex 二轮 MEDIUM-2 修复：定期清理成本告警声明记录"""

    @pytest.mark.asyncio
    async def test_cleanup_removes_old_records(self, client):
        """老于 retention_days 的 cost_alert_seen.* 记录应被删除"""
        from app.common.models import SystemConfig
        from datetime import date, timedelta as _td
        import app.database as db_mod

        tracker = CostTracker()
        old_date = date.today() - _td(days=120)
        recent_date = date.today() - _td(days=30)

        # 写入 1 条老的 + 1 条新的（value 必须含 date 字段，cleanup 用此字段判断）
        async with db_mod.async_session_factory() as session:
            session.add_all([
                SystemConfig(
                    key=f"cost_alert_seen.EC.{old_date.isoformat()}",
                    value={
                        "alerted_at": "2020-01-01T00:00:00",
                        "date": old_date.isoformat(),
                        "department": "EC",
                    },
                ),
                SystemConfig(
                    key=f"cost_alert_seen.EC.{recent_date.isoformat()}",
                    value={
                        "alerted_at": "2026-04-01T00:00:00",
                        "date": recent_date.isoformat(),
                        "department": "EC",
                    },
                ),
                # 不相关的配置（应保留）
                SystemConfig(key="ai.api_base", value="http://test"),
            ])
            await session.commit()

        deleted = await tracker.cleanup_old_alert_claims(retention_days=90)
        assert deleted == 1

        # 验证：只剩 recent + ai.api_base
        async with db_mod.async_session_factory() as session:
            from sqlalchemy import select as _select
            rows = (await session.execute(_select(SystemConfig))).scalars().all()
            keys = sorted(r.key for r in rows)
            assert "ai.api_base" in keys
            assert f"cost_alert_seen.EC.{recent_date.isoformat()}" in keys
            assert f"cost_alert_seen.EC.{old_date.isoformat()}" not in keys


class TestCostAlertCard:
    """build_cost_alert 卡片模板测试"""

    def test_card_contains_key_fields(self):
        from app.dingtalk.card_templates import build_cost_alert

        card = build_cost_alert(
            department="EC",
            total_cost=120.50,
            threshold=100.0,
            date_str="2026-04-07",
        )
        assert "title" in card
        assert "markdown" in card
        assert "EC" in card["title"]
        assert "120.5" in card["markdown"] or "120.50" in card["markdown"]
        assert "100" in card["markdown"]
        assert "2026-04-07" in card["markdown"]

    def test_severe_when_overflow_50pct(self):
        from app.dingtalk.card_templates import build_cost_alert

        card = build_cost_alert(
            department="EC",
            total_cost=200.0,
            threshold=100.0,
            date_str="2026-04-07",
        )
        assert "严重" in card["title"]

    def test_warning_when_small_overflow(self):
        from app.dingtalk.card_templates import build_cost_alert

        card = build_cost_alert(
            department="EC",
            total_cost=110.0,
            threshold=100.0,
            date_str="2026-04-07",
        )
        assert "警告" in card["title"]

    def test_includes_top_call_sources(self):
        from app.dingtalk.card_templates import build_cost_alert

        card = build_cost_alert(
            department="EC",
            total_cost=120.0,
            threshold=100.0,
            date_str="2026-04-07",
            top_call_sources=[("agent_chat", 80.5), ("workbench_chat", 30.0)],
        )
        assert "agent_chat" in card["markdown"]
        assert "workbench_chat" in card["markdown"]
