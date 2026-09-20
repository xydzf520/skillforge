"""配额使用率告警。

当组织配额使用率达到 80% / 90% / 100% 时，
通过钉钉 outbox 和站内通知告警管理员。
使用 Redis 缓存避免重复告警（同一配额同一级别 24h 内只告警一次）。

注：本项目没有独立的 approval/quota_service 模块，配额由
cost_tracker 的日成本阈值体系管理。本模块在 cost_tracker 基础上
增加多级别渐进告警（80%/90%/100%），并通过后台定时任务定期检查。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from loguru import logger

from app.common.cache import cache_get, cache_set
from app.common.time_utils import isoformat_bjt, now_bjt


# 告警阈值级别：(使用率比例, 级别名称, 告警消息模板)
ALERT_THRESHOLDS: list[tuple[float, str, str]] = [
    (0.80, "warning", "配额使用率已达 80%，请注意控制用量"),
    (0.90, "critical", "配额使用率已达 90%，即将触发限流"),
    (1.00, "throttled", "配额已耗尽，已自动限流"),
]

# Redis 缓存 key 前缀，用于 24h 去重
_ALERT_CACHE_PREFIX = "quota_alert_sent"
# 去重过期时间（秒）
_ALERT_COOLDOWN_SECONDS = 86400  # 24小时


async def check_and_alert_quotas(db=None) -> list[dict]:
    """检查所有部门的配额使用率，对超阈值的发送告警。

    遍历 cost_tracker 的日成本阈值体系：
    - 从 system_config 读取每部门日成本限额
    - 计算当日用量占限额的比例
    - 对超过 80%/90%/100% 的级别分别告警（24h 去重）

    Returns:
        本次发送的告警列表 [{department, level, message, usage_ratio}]
    """
    from app.common.cost_tracker import cost_tracker

    alerts_sent: list[dict] = []

    # 获取全局告警阈值（日成本 USD）
    threshold = await cost_tracker._get_alert_threshold()
    if threshold is None or threshold <= 0:
        return alerts_sent

    # 获取所有有用量的部门（今日）
    departments = await _get_active_departments_today()
    if not departments:
        return alerts_sent

    for dept, daily_cost in departments:
        if daily_cost <= 0:
            continue

        # 计算使用率
        usage_ratio = float(daily_cost) / float(threshold)

        # 按从高到低检查阈值级别
        for ratio_threshold, level, message_template in reversed(ALERT_THRESHOLDS):
            if usage_ratio >= ratio_threshold:
                # 检查 24h 内是否已告警
                already_sent = await _is_alert_sent_recently(dept, level)
                if already_sent:
                    break  # 高级别已发过，低级别也不需要重发

                # 发送告警
                try:
                    await _send_quota_alert(
                        department=dept,
                        threshold_level=level,
                        message=message_template,
                        usage_ratio=usage_ratio,
                        daily_cost=daily_cost,
                        daily_limit=threshold,
                    )
                    alerts_sent.append({
                        "department": dept,
                        "level": level,
                        "message": message_template,
                        "usage_ratio": round(usage_ratio, 4),
                    })
                    logger.warning(
                        f"配额告警已发送: dept={dept} level={level} "
                        f"usage={usage_ratio:.1%} cost=${daily_cost:.2f}/{threshold:.2f}"
                    )
                except Exception as exc:
                    logger.error(f"配额告警发送失败: dept={dept} level={level} error={exc}")

                break  # 只发最高级别的告警

    return alerts_sent


async def _send_quota_alert(
    *,
    department: str,
    threshold_level: str,
    message: str,
    usage_ratio: float,
    daily_cost: Decimal,
    daily_limit: Decimal,
) -> None:
    """通过 outbox 发送配额告警钉钉消息。

    Args:
        department: 部门名
        threshold_level: 告警级别 (warning/critical/throttled)
        message: 告警消息
        usage_ratio: 使用率
        daily_cost: 当日已用成本
        daily_limit: 日成本限额
    """
    from app.common.cost_tracker import cost_tracker
    from app.dingtalk.outbox import outbox

    # 构建告警负载
    level_icons = {"warning": "⚠️", "critical": "🔴", "throttled": "🚫"}
    icon = level_icons.get(threshold_level, "⚠️")

    today_str = now_bjt().strftime("%Y-%m-%d")
    payload = {
        "msgtype": "text",
        "text": {
            "content": (
                f"{icon} 配额告警 [{threshold_level.upper()}]\n"
                f"部门: {department}\n"
                f"日期: {today_str}\n"
                f"状态: {message}\n"
                f"用量: ${float(daily_cost):.2f} / ${float(daily_limit):.2f} "
                f"({usage_ratio:.0%})"
            ),
        },
    }

    # 获取收件人
    recipients = await cost_tracker._get_alert_recipients(department)
    # 告警优先级：warning=5, critical=3, throttled=1
    priority_map = {"warning": 5, "critical": 3, "throttled": 1}
    priority = priority_map.get(threshold_level, 5)

    for recipient in recipients:
        await outbox.enqueue(
            message_type="work_notice",
            recipient=recipient,
            payload=payload,
            priority=priority,
            related_type="quota_alert",
            related_id=f"{department}:{threshold_level}",
        )

    # 标记已发送（Redis 缓存 24h 去重）
    await _mark_alert_sent(department, threshold_level)


async def _is_alert_sent_recently(department: str, level: str) -> bool:
    """检查 Redis 缓存，24h 内同级别是否已告警。

    Args:
        department: 部门名
        level: 告警级别

    Returns:
        True 表示 24h 内已发过，应跳过
    """
    today_str = now_bjt().strftime("%Y-%m-%d")
    cache_key = f"{_ALERT_CACHE_PREFIX}:{department}:{level}:{today_str}"
    try:
        cached = await cache_get(cache_key)
        return cached is not None
    except Exception as exc:
        # Redis 不可用时保守不发（避免重复告警）
        logger.warning(f"配额告警去重查询 Redis 失败: {exc}")
        return True


async def _mark_alert_sent(department: str, level: str) -> None:
    """在 Redis 中标记告警已发送。

    Args:
        department: 部门名
        level: 告警级别
    """
    today_str = now_bjt().strftime("%Y-%m-%d")
    cache_key = f"{_ALERT_CACHE_PREFIX}:{department}:{level}:{today_str}"
    try:
        await cache_set(cache_key, {"sent_at": isoformat_bjt(now_bjt())}, ttl=_ALERT_COOLDOWN_SECONDS)
    except Exception as exc:
        logger.warning(f"配额告警去重标记 Redis 失败: {exc}")


async def _get_active_departments_today() -> list[tuple[str, Decimal]]:
    """获取今日有用量的所有部门及其当日成本。

    Returns:
        [(department_name, daily_cost_usd), ...]
    """
    from sqlalchemy import func, select

    from app import database as _db_mod
    from app.common.models import UsageLog

    today = now_bjt().date()
    try:
        async with _db_mod.async_session_factory() as session:
            stmt = (
                select(
                    UsageLog.department,
                    func.coalesce(func.sum(UsageLog.cost_usd), 0),
                )
                .where(UsageLog.department.isnot(None))
                .where(func.date(UsageLog.ts) == today)
                .group_by(UsageLog.department)
            )
            rows = (await session.execute(stmt)).all()
            return [(r[0], Decimal(str(r[1] or 0))) for r in rows if r[0]]
    except Exception as exc:
        logger.warning(f"查询活跃部门失败: {exc}")
        return []
