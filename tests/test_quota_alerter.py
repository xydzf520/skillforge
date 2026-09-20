"""配额告警模块测试。

覆盖：
1. 使用率未达阈值时不告警
2. 达到 80%/90%/100% 时分级告警
3. 24h 内同级别不重复告警（Redis 去重）
4. Redis 不可用时保守不发（避免重复告警）
5. 无活跃部门时直接返回空列表
"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.common.quota_alerter import (
    ALERT_THRESHOLDS,
    _ALERT_CACHE_PREFIX,
    _is_alert_sent_recently,
    check_and_alert_quotas,
)


# ===== 辅助 =====

def _patch_threshold(val):
    """mock cost_tracker._get_alert_threshold 返回指定值。"""
    return patch(
        "app.common.cost_tracker.CostTracker._get_alert_threshold",
        new_callable=AsyncMock,
        return_value=val,
    )


def _patch_departments(data: list[tuple[str, Decimal]]):
    """mock _get_active_departments_today 返回指定部门列表。"""
    return patch(
        "app.common.quota_alerter._get_active_departments_today",
        new_callable=AsyncMock,
        return_value=data,
    )


def _patch_alert_sent(is_sent: bool):
    """mock _is_alert_sent_recently 返回指定值。"""
    return patch(
        "app.common.quota_alerter._is_alert_sent_recently",
        new_callable=AsyncMock,
        return_value=is_sent,
    )


def _patch_send_alert():
    """mock _send_quota_alert。"""
    return patch(
        "app.common.quota_alerter._send_quota_alert",
        new_callable=AsyncMock,
    )


# ===== 测试 =====

class TestCheckAndAlertQuotas:
    """check_and_alert_quotas 集成测试。"""

    @pytest.mark.asyncio
    async def test_no_alert_below_threshold(self):
        """使用率低于 80% 时不发告警。"""
        with (
            _patch_threshold(Decimal("100")),
            _patch_departments([("EC", Decimal("50"))]),  # 50%
            _patch_send_alert() as mock_send,
        ):
            alerts = await check_and_alert_quotas()
            assert alerts == []
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_warning_at_80_percent(self):
        """使用率达到 80% 时发 warning 告警。"""
        with (
            _patch_threshold(Decimal("100")),
            _patch_departments([("EC", Decimal("82"))]),  # 82%
            _patch_alert_sent(False),
            _patch_send_alert() as mock_send,
        ):
            alerts = await check_and_alert_quotas()
            assert len(alerts) == 1
            assert alerts[0]["level"] == "warning"
            assert alerts[0]["department"] == "EC"
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_critical_at_90_percent(self):
        """使用率达到 90% 时发 critical 告警。"""
        with (
            _patch_threshold(Decimal("100")),
            _patch_departments([("EC", Decimal("95"))]),  # 95%
            _patch_alert_sent(False),
            _patch_send_alert() as mock_send,
        ):
            alerts = await check_and_alert_quotas()
            assert len(alerts) == 1
            assert alerts[0]["level"] == "critical"

    @pytest.mark.asyncio
    async def test_throttled_at_100_percent(self):
        """使用率达到 100% 时发 throttled 告警。"""
        with (
            _patch_threshold(Decimal("100")),
            _patch_departments([("EC", Decimal("105"))]),  # 105%
            _patch_alert_sent(False),
            _patch_send_alert() as mock_send,
        ):
            alerts = await check_and_alert_quotas()
            assert len(alerts) == 1
            assert alerts[0]["level"] == "throttled"

    @pytest.mark.asyncio
    async def test_no_duplicate_within_24h(self):
        """24h 内同级别不重复告警。"""
        with (
            _patch_threshold(Decimal("100")),
            _patch_departments([("EC", Decimal("85"))]),
            _patch_alert_sent(True),  # 已发过
            _patch_send_alert() as mock_send,
        ):
            alerts = await check_and_alert_quotas()
            assert alerts == []
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_active_departments(self):
        """无活跃部门时返回空列表。"""
        with (
            _patch_threshold(Decimal("100")),
            _patch_departments([]),
        ):
            alerts = await check_and_alert_quotas()
            assert alerts == []

    @pytest.mark.asyncio
    async def test_threshold_disabled_zero(self):
        """阈值为 0 时不检查。"""
        with _patch_threshold(Decimal("0")):
            alerts = await check_and_alert_quotas()
            assert alerts == []

    @pytest.mark.asyncio
    async def test_threshold_disabled_none(self):
        """阈值为 None 时不检查。"""
        with _patch_threshold(None):
            alerts = await check_and_alert_quotas()
            assert alerts == []

    @pytest.mark.asyncio
    async def test_multiple_departments(self):
        """多个部门同时检查。"""
        with (
            _patch_threshold(Decimal("100")),
            _patch_departments([
                ("EC", Decimal("85")),   # 85% -> warning
                ("AI小组", Decimal("50")),  # 50% -> 无告警
                ("运营", Decimal("95")),  # 95% -> critical
            ]),
            _patch_alert_sent(False),
            _patch_send_alert() as mock_send,
        ):
            alerts = await check_and_alert_quotas()
            assert len(alerts) == 2
            levels = {a["level"] for a in alerts}
            assert "warning" in levels
            assert "critical" in levels


class TestIsAlertSentRecently:
    """_is_alert_sent_recently 缓存去重测试。"""

    @pytest.mark.asyncio
    async def test_not_sent_when_cache_empty(self):
        """缓存为空时返回 False。"""
        with patch("app.common.quota_alerter.cache_get", new_callable=AsyncMock, return_value=None):
            result = await _is_alert_sent_recently("EC", "warning")
            assert result is False

    @pytest.mark.asyncio
    async def test_sent_when_cache_hit(self):
        """缓存命中时返回 True。"""
        with patch(
            "app.common.quota_alerter.cache_get",
            new_callable=AsyncMock,
            return_value={"sent_at": "2026-04-13T10:00:00"},
        ):
            result = await _is_alert_sent_recently("EC", "warning")
            assert result is True

    @pytest.mark.asyncio
    async def test_conservative_on_redis_failure(self):
        """Redis 不可用时保守返回 True（不发告警）。"""
        with patch(
            "app.common.quota_alerter.cache_get",
            new_callable=AsyncMock,
            side_effect=Exception("Redis 连接失败"),
        ):
            result = await _is_alert_sent_recently("EC", "warning")
            assert result is True
