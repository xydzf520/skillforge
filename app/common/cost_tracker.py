"""
LLM 调用成本追踪。

参考 aiclawcode/src/cost-tracker.ts + modelCost.ts，针对 SkillForge 简化：
- 单层定价表（DEFAULT_PRICING），支持常见模型
- 每次调用写一条 usage_logs
- 提供 summary 聚合查询用于 Dashboard
- 部门日成本超阈值时通过钉钉 outbox 告警（24h 冷却，基于 outbox 记录去重）

定价单位：USD per 1M tokens
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from loguru import logger
from sqlalchemy import delete as sa_delete, func, select

from app import database as _db_mod
from app.common.models import UsageLog
from app.common.time_utils import isoformat_bjt, now_bjt


# ===== 成本告警 =====
# 默认阈值（USD/天/部门），可在 system_config.cost.alert.daily_usd 覆盖
DEFAULT_ALERT_THRESHOLD_USD = Decimal("100")

# 日级去重：每个部门每日历日最多发一次 cost_alert，跨进程并发安全。
# 实现见 cost_tracker._try_claim_alert（用 SystemConfig 表 ON CONFLICT 原子声明）。


# ===== 定价表 =====
# 单位: USD per 1M tokens
# 注：实际定价以官方为准，此处为参考值。可在 system_config.cost.pricing 覆盖
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {
        "input": 15.0, "output": 75.0,
        "cache_read": 1.5, "cache_write": 18.75,
    },
    "claude-sonnet-4-6": {
        "input": 3.0, "output": 15.0,
        "cache_read": 0.3, "cache_write": 3.75,
    },
    "claude-haiku-4-5": {
        "input": 0.8, "output": 4.0,
        "cache_read": 0.08, "cache_write": 1.0,
    },
    "gpt-4o": {
        "input": 2.5, "output": 10.0,
        "cache_read": 1.25, "cache_write": 2.5,
    },
    "gpt-4o-mini": {
        "input": 0.15, "output": 0.6,
        "cache_read": 0.075, "cache_write": 0.15,
    },
    "deepseek-v4-pro": {
        "input": 0.435, "output": 0.87,
        "cache_read": 0.003625, "cache_write": 0.0,
    },
    "deepseek-v4-flash": {
        "input": 0.14, "output": 0.28,
        "cache_read": 0.0028, "cache_write": 0.0,
    },
    "default": {
        "input": 1.0, "output": 3.0,
        "cache_read": 0.1, "cache_write": 1.25,
    },
}


def _match_pricing(model: str) -> dict[str, float]:
    """根据模型名称匹配定价。优先精确匹配，否则按前缀匹配，最后 fallback default。"""
    if not model:
        return DEFAULT_PRICING["default"]
    if model in DEFAULT_PRICING:
        return DEFAULT_PRICING[model]
    # 前缀匹配（兼容 claude-opus-4-6-20260101 这类带日期的版本）
    for key in DEFAULT_PRICING:
        if key != "default" and model.startswith(key):
            return DEFAULT_PRICING[key]
    return DEFAULT_PRICING["default"]


def _normalize_pricing(raw) -> dict[str, float] | None:
    """把 system_config 中的多种定价格式归一成 input/output/cache_read/cache_write。"""
    if not isinstance(raw, dict):
        return None
    source = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else raw
    aliases = {
        "input": ("input", "input_usd_per_1m", "prompt", "prompt_tokens"),
        "output": ("output", "output_usd_per_1m", "completion", "completion_tokens"),
        "cache_read": ("cache_read", "cache_read_usd_per_1m"),
        "cache_write": ("cache_write", "cache_write_usd_per_1m"),
    }
    normalized: dict[str, float | None] = {}
    for target, keys in aliases.items():
        value = None
        for key in keys:
            if key in source:
                value = source.get(key)
                break
        if value is None:
            normalized[target] = 0.0 if target.startswith("cache_") else None
            continue
        try:
            normalized[target] = float(value)
        except (TypeError, ValueError):
            return None
    if normalized.get("input") is None or normalized.get("output") is None:
        return None
    normalized.setdefault("cache_read", 0.0)
    normalized.setdefault("cache_write", 0.0)
    return {key: float(value or 0) for key, value in normalized.items()}


async def _match_pricing_from_config(model: str) -> tuple[dict[str, float] | None, bool]:
    """从 system_config 读取模型定价。返回 (pricing, configured)。"""
    try:
        from app.common.models import SystemConfig

        async with _db_mod.async_session_factory() as session:
            # 精确模型 key 优先：ai.pricing.<model>
            row = await session.execute(
                select(SystemConfig).where(SystemConfig.key == f"ai.pricing.{model}")
            )
            cfg = row.scalar_one_or_none()
            pricing = _normalize_pricing(cfg.value) if cfg and cfg.value is not None else None
            if pricing:
                return pricing, True

            # 集中表：cost.pricing / ai.pricing / intelligence.pricing
            result = await session.execute(
                select(SystemConfig).where(
                    SystemConfig.key.in_(["cost.pricing", "ai.pricing", "intelligence.pricing"])
                )
            )
            for item in result.scalars().all():
                raw = item.value
                if not isinstance(raw, dict):
                    continue
                model_pricing = raw.get(model)
                pricing = _normalize_pricing(model_pricing)
                if pricing:
                    return pricing, True
                for key, value in raw.items():
                    if key != "default" and model.startswith(str(key)):
                        pricing = _normalize_pricing(value)
                        if pricing:
                            return pricing, True
                pricing = _normalize_pricing(raw.get("default"))
                if pricing:
                    return pricing, True
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取模型定价配置失败 model={}: {}", model, exc)
    return None, False


# ===== 数据结构 =====

@dataclass
class UsageEvent:
    """单次 LLM 调用的用量事件。所有字段都可选（空时存默认值）"""
    user_id: str | None = None
    department: str | None = None
    skill_id: str | None = None
    conversation_id: str | None = None
    call_source: str = "unknown"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    prompt_hash: str | None = None
    duration_ms: int = 0
    metadata_json: dict | None = None


# ===== CostTracker =====

class CostTracker:
    """全局成本追踪器。单例：从模块底部 cost_tracker 访问"""

    def calc_cost(self, event: UsageEvent) -> Decimal:
        """根据 event.model 查定价表，计算成本（USD）"""
        pricing = _match_pricing(event.model)
        return self.calc_cost_with_pricing(event, pricing)

    def calc_cost_with_pricing(self, event: UsageEvent, pricing: dict[str, float]) -> Decimal:
        """根据给定定价表计算成本（USD）。"""
        cost = (
            event.input_tokens * pricing["input"]
            + event.output_tokens * pricing["output"]
            + event.cache_read_tokens * pricing["cache_read"]
            + event.cache_write_tokens * pricing["cache_write"]
        ) / 1_000_000
        return Decimal(str(round(cost, 6)))

    async def pricing_for_model(self, model: str, *, require_config: bool = False) -> dict[str, float] | None:
        """读取模型定价；DB 配置优先，未配置时可 fallback 到内置表。"""
        pricing, configured = await _match_pricing_from_config(model)
        if configured and pricing:
            return pricing
        if require_config and model not in DEFAULT_PRICING:
            return None
        return _match_pricing(model)

    async def calc_cost_async(self, event: UsageEvent, *, require_config: bool = False) -> Decimal | None:
        pricing = await self.pricing_for_model(event.model, require_config=require_config)
        if pricing is None:
            return None
        return self.calc_cost_with_pricing(event, pricing)

    async def record(self, event: UsageEvent) -> Decimal:
        """计算成本并写入 usage_logs。失败时只 warning，不中断主流程"""
        cost = await self.calc_cost_async(event) or self.calc_cost(event)
        try:
            async with _db_mod.async_session_factory() as session:
                row = UsageLog(
                    ts=now_bjt(),
                    user_id=event.user_id,
                    department=event.department,
                    skill_id=event.skill_id,
                    conversation_id=event.conversation_id,
                    call_source=event.call_source,
                    model=event.model or "unknown",
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    cache_read_tokens=event.cache_read_tokens,
                    cache_write_tokens=event.cache_write_tokens,
                    cost_usd=cost,
                    prompt_hash=event.prompt_hash,
                    duration_ms=event.duration_ms,
                    metadata_json=event.metadata_json or {},
                )
                session.add(row)
                await session.commit()
        except Exception as e:
            logger.warning(f"成本追踪写入失败 (event={event.call_source}): {e}")

        # F4: 成本告警检查（独立 try/except，绝不影响主调用）
        try:
            await self._check_alert(event.department, cost)
        except Exception as e:
            logger.warning(f"成本告警检查异常 (dept={event.department}): {e}")

        return cost

    async def _check_alert(self, department: str | None, cost_added: Decimal) -> None:
        """
        部门日成本超阈值告警。

        触发条件（同时满足）：
          1. department 非空
          2. 当日累计成本 > 阈值
          3. 加上本次 cost_added 之前 ≤ 阈值（即"刚跨过"）
          4. 当日尚未为该部门发送过 cost_alert（按日历日去重，跨进程并发安全）

        并发安全（Codex 复核 HIGH 修复）：
          原方案 "查 outbox 是否有 cost_alert + 入队" 存在 check-then-act race，
          两个并发请求可能都查到没有从而双发。
          新方案：用 SystemConfig 表 + ON CONFLICT DO NOTHING 原子声明 daily lock。
          首次声明成功的请求才会 enqueue，其他请求 rowcount=0 跳过。

        阈值来源：system_config.cost.alert.daily_usd（默认 100）
        收件人来源：system_config.cost.alert.recipients（默认 ["admin"]）
        发送通道：钉钉 outbox（priority=5），related_type=cost_alert，related_id=部门名
        """
        if not department:
            return

        threshold = await self._get_alert_threshold()
        if threshold is None or threshold <= 0:
            return  # 阈值未配置或被禁用

        # 查今日累计成本
        today = now_bjt().date()
        try:
            async with _db_mod.async_session_factory() as session:
                stmt = (
                    select(func.coalesce(func.sum(UsageLog.cost_usd), 0))
                    .where(UsageLog.department == department)
                    .where(func.date(UsageLog.ts) == today)
                )
                result = await session.execute(stmt)
                total = Decimal(str(result.scalar() or 0))
        except Exception as e:
            logger.warning(f"查询部门日成本失败 dept={department}: {e}")
            return

        # 判断是否"刚跨过阈值"
        cost_decimal = Decimal(str(cost_added))
        previous_total = total - cost_decimal
        if total <= threshold:
            return  # 未达阈值
        if previous_total > threshold:
            return  # 之前已超过，本次不算"刚跨过"

        # 原子声明今日告警权（HIGH 修复：并发安全的 daily 去重）
        # claim 失败 = 已被其他请求抢先发出 = 跳过
        claimed = await self._try_claim_alert(department, today, total, threshold)
        if not claimed:
            logger.info(
                f"成本告警已被其他请求声明 dept={department} date={today} (跳过)"
            )
            return

        # 触发告警 → enqueue 钉钉消息
        from app.dingtalk.outbox import outbox
        from app.dingtalk.card_templates import build_cost_alert

        # 取 top call_source（用于卡片正文）
        top_sources = await self._top_call_sources_today(department)

        payload = build_cost_alert(
            department=department,
            total_cost=float(total),
            threshold=float(threshold),
            date_str=today.isoformat(),
            top_call_sources=top_sources,
        )

        recipients = await self._get_alert_recipients(department)
        # P2-1 入队失败重试：DB 瞬时故障 / 死锁应允许重试，3 次仍失败才放弃
        # 失败的 recipient 收集起来，用于决定是否回滚 alert_seen 标记，
        # 避免"alert_seen 已写入但消息没投递"导致整天再也收不到告警
        failed_recipients: list[str] = []
        for recipient in recipients:
            ok = False
            last_exc: Exception | None = None
            for attempt in range(3):
                try:
                    await outbox.enqueue(
                        message_type="work_notice",
                        recipient=recipient,
                        payload=payload,
                        priority=5,
                        related_type="cost_alert",
                        related_id=department,
                    )
                    ok = True
                    break
                except Exception as e:
                    last_exc = e
                    logger.warning(
                        f"成本告警入队失败 dept={department} recipient={recipient} attempt={attempt+1}: {e}"
                    )
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (2 ** attempt))  # 0.5s, 1s
            if not ok:
                failed_recipients.append(recipient)
                logger.error(
                    f"成本告警入队最终失败 dept={department} recipient={recipient}: {last_exc}"
                )

        # 全部 recipient 都失败 → 回滚去重声明，让下一次触发能重试
        if failed_recipients and len(failed_recipients) == len(recipients):
            try:
                await self._release_alert_claim(department, today)
                logger.warning(
                    f"成本告警全部 recipient 入队失败，已回滚 alert_seen 标记 dept={department}"
                )
            except Exception as rollback_exc:
                logger.error(
                    f"成本告警回滚 alert_seen 失败 dept={department}: {rollback_exc}"
                )

        logger.warning(
            f"成本告警触发 dept={department} total=${total:.4f} threshold=${threshold:.2f} "
            f"recipients={recipients}"
        )

    async def cleanup_old_alert_claims(self, retention_days: int = 90) -> int:
        """清理旧的成本告警声明记录（每日后台任务调用）。

        Codex 二轮 MEDIUM-2 修复：避免 SystemConfig 表无限累积 cost_alert_seen.*。
        默认保留 90 天，老于此的记录可安全删除（业务上不会回查）。

        实现说明（Codex 三轮：split_part 脆弱性修复）：
        不再用 `split_part(key, '.', 3)` 解析 key 中的日期段，
        改用 JSONB 字段 `value->>'date'` 直接比较，避免依赖 department 不含 '.'。

        Returns:
            清理的记录数
        """
        from app.common.models import SystemConfig
        from sqlalchemy import delete
        cutoff_date = (now_bjt() - timedelta(days=retention_days)).date()
        cutoff_iso = cutoff_date.isoformat()
        try:
            async with _db_mod.async_session_factory() as session:
                # 删除 cost_alert_seen.* 且 value->>'date' < cutoff 的记录
                # value 是 JSONB（写入时统一含 "date" 字段，见 _try_claim_alert）
                stmt = delete(SystemConfig).where(
                    SystemConfig.key.like("cost_alert_seen.%"),
                    SystemConfig.value["date"].astext < cutoff_iso,
                )
                result = await session.execute(stmt)
                await session.commit()
                count = result.rowcount or 0
                if count > 0:
                    logger.info(
                        f"成本告警声明清理: 删除 {count} 条 < {cutoff_iso} 的记录"
                    )
                return count
        except Exception as e:
            logger.warning(f"清理告警声明失败: {e}")
            return 0

    async def _release_alert_claim(self, department: str, today) -> None:
        """P2-1 回滚 alert_seen 声明。

        当告警入队全部失败时调用，使下一次成本检查能重试投递，
        而不是被 _try_claim_alert 的去重逻辑挡住。
        """
        from app.common.models import SystemConfig

        key = f"cost_alert_seen.{department}.{today.isoformat()}"
        async with _db_mod.async_session_factory() as session:
            await session.execute(
                sa_delete(SystemConfig).where(SystemConfig.key == key)
            )
            await session.commit()

    async def _try_claim_alert(
        self,
        department: str,
        today,  # date
        total: Decimal,
        threshold: Decimal,
    ) -> bool:
        """原子地认领今日的告警权（按日历日去重）。

        实现：用 SystemConfig 表的主键约束 + ON CONFLICT DO NOTHING。
        - 首次插入 → rowcount=1 → 返回 True（应发）
        - 已存在 → rowcount=0 → 返回 False（跳过）

        这是并发安全的：PostgreSQL 保证多个并发 INSERT 同一 PK 时只有一个成功。

        Args:
            department: 部门名
            today: datetime.date
            total / threshold: 写入 value 用于审计追溯

        Returns:
            True 表示首次声明成功（应继续发送告警），False 表示已被认领
        """
        from app.common.models import SystemConfig
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        key = f"cost_alert_seen.{department}.{today.isoformat()}"
        try:
            async with _db_mod.async_session_factory() as session:
                stmt = pg_insert(SystemConfig).values(
                    key=key,
                    value={
                        "alerted_at": isoformat_bjt(now_bjt()),
                        "department": department,
                        "date": today.isoformat(),
                        "total_usd": str(total),
                        "threshold_usd": str(threshold),
                    },
                    updated_at=now_bjt(),
                ).on_conflict_do_nothing(index_elements=["key"])
                result = await session.execute(stmt)
                await session.commit()
                # rowcount > 0 → 首次插入成功
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.warning(
                f"声明告警权失败 dept={department} date={today}: {e}"
            )
            # 失败时保守不发，避免重复告警
            return False

    async def _get_alert_threshold(self) -> Decimal | None:
        """从 system_config.cost.alert.daily_usd 读取阈值（USD）。

        宽容解析（Codex MEDIUM 修复）：
        支持多种 JSONB value 结构：
        - 直接数字：100
        - dict 多种 key：{"value": 100} / {"daily_usd": 100} / {"threshold": 100}
        - 字符串可解析为 Decimal："100" / "100.5"

        非法格式时打 warning（不只是 debug），并 fallback 到默认值。
        """
        try:
            from app.common.models import SystemConfig
            async with _db_mod.async_session_factory() as session:
                row = await session.execute(
                    select(SystemConfig).where(SystemConfig.key == "cost.alert.daily_usd")
                )
                cfg = row.scalar_one_or_none()
                if cfg and cfg.value is not None:
                    raw = cfg.value
                    # dict 形式：尝试常见 key
                    if isinstance(raw, dict):
                        for k in ("value", "daily_usd", "threshold", "amount"):
                            if k in raw and raw[k] is not None:
                                raw = raw[k]
                                break
                        else:
                            logger.warning(
                                f"cost.alert.daily_usd 配置格式无法识别 (dict 缺少 value/daily_usd/threshold key): "
                                f"{cfg.value}，使用默认值 ${DEFAULT_ALERT_THRESHOLD_USD}"
                            )
                            return DEFAULT_ALERT_THRESHOLD_USD
                    if raw is not None:
                        try:
                            return Decimal(str(raw))
                        except (InvalidOperation, ValueError) as e:
                            logger.warning(
                                f"cost.alert.daily_usd 配置无法转 Decimal: {raw} ({e})，"
                                f"使用默认值 ${DEFAULT_ALERT_THRESHOLD_USD}"
                            )
                            return DEFAULT_ALERT_THRESHOLD_USD
        except Exception as e:
            logger.warning(f"读取告警阈值配置失败，使用默认值: {e}")
        return DEFAULT_ALERT_THRESHOLD_USD

    async def _get_alert_recipients(self, department: str) -> list[str]:
        """
        从 system_config.cost.alert.recipients 读取收件人。

        支持两种结构：
          1. {"default": ["admin"], "EC": ["zhang_ec"]}  # 按部门映射
          2. ["admin", "ops"]                              # 全局列表
        """
        try:
            from app.common.models import SystemConfig
            async with _db_mod.async_session_factory() as session:
                row = await session.execute(
                    select(SystemConfig).where(SystemConfig.key == "cost.alert.recipients")
                )
                cfg = row.scalar_one_or_none()
                if cfg and cfg.value is not None:
                    raw = cfg.value
                    if isinstance(raw, dict):
                        if department in raw:
                            v = raw[department]
                            return v if isinstance(v, list) else [v]
                        if "default" in raw:
                            v = raw["default"]
                            return v if isinstance(v, list) else [v]
                    elif isinstance(raw, list):
                        return [str(x) for x in raw]
        except Exception as e:
            logger.debug(f"读取告警收件人配置失败，使用默认值: {e}")
        return ["admin"]

    async def _top_call_sources_today(
        self, department: str, limit: int = 5
    ) -> list[tuple[str, float]]:
        """取今日该部门 top N 调用来源（用于告警卡片正文）"""
        today = now_bjt().date()
        try:
            async with _db_mod.async_session_factory() as session:
                stmt = (
                    select(
                        UsageLog.call_source,
                        func.coalesce(func.sum(UsageLog.cost_usd), 0),
                    )
                    .where(UsageLog.department == department)
                    .where(func.date(UsageLog.ts) == today)
                    .group_by(UsageLog.call_source)
                    .order_by(func.sum(UsageLog.cost_usd).desc())
                    .limit(limit)
                )
                rows = (await session.execute(stmt)).all()
                return [(r[0] or "unknown", float(r[1] or 0)) for r in rows]
        except Exception:
            return []

    async def summary(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        department: str | None = None,
        skill_id: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        """按维度聚合成本与 token 用量。

        Returns:
            {
                "total_cost_usd": Decimal,
                "total_calls": int,
                "total_input_tokens": int,
                "total_output_tokens": int,
                "by_model": [{"model": str, "calls": int, "cost_usd": Decimal}, ...],
                "by_source": [{"call_source": str, "calls": int, "cost_usd": Decimal}, ...],
            }
        """
        async with _db_mod.async_session_factory() as session:
            conditions = []
            if date_from:
                conditions.append(UsageLog.ts >= date_from)
            if date_to:
                conditions.append(UsageLog.ts < date_to)
            if department:
                conditions.append(UsageLog.department == department)
            if skill_id:
                conditions.append(UsageLog.skill_id == skill_id)
            if user_id:
                conditions.append(UsageLog.user_id == user_id)

            # 总览
            total_stmt = select(
                func.count(UsageLog.id),
                func.coalesce(func.sum(UsageLog.cost_usd), 0),
                func.coalesce(func.sum(UsageLog.input_tokens), 0),
                func.coalesce(func.sum(UsageLog.output_tokens), 0),
            )
            for c in conditions:
                total_stmt = total_stmt.where(c)
            total_row = (await session.execute(total_stmt)).one()

            # 按模型分桶
            by_model_stmt = (
                select(
                    UsageLog.model,
                    func.count(UsageLog.id),
                    func.coalesce(func.sum(UsageLog.cost_usd), 0),
                )
                .group_by(UsageLog.model)
                .order_by(func.sum(UsageLog.cost_usd).desc())
                .limit(10)
            )
            for c in conditions:
                by_model_stmt = by_model_stmt.where(c)
            by_model_rows = (await session.execute(by_model_stmt)).all()

            # 按 call_source 分桶
            by_source_stmt = (
                select(
                    UsageLog.call_source,
                    func.count(UsageLog.id),
                    func.coalesce(func.sum(UsageLog.cost_usd), 0),
                )
                .group_by(UsageLog.call_source)
                .order_by(func.sum(UsageLog.cost_usd).desc())
                .limit(10)
            )
            for c in conditions:
                by_source_stmt = by_source_stmt.where(c)
            by_source_rows = (await session.execute(by_source_stmt)).all()

            return {
                "total_calls": total_row[0] or 0,
                "total_cost_usd": float(total_row[1] or 0),
                "total_input_tokens": int(total_row[2] or 0),
                "total_output_tokens": int(total_row[3] or 0),
                "by_model": [
                    {"model": r[0] or "unknown", "calls": r[1], "cost_usd": float(r[2] or 0)}
                    for r in by_model_rows
                ],
                "by_source": [
                    {"call_source": r[0] or "unknown", "calls": r[1], "cost_usd": float(r[2] or 0)}
                    for r in by_source_rows
                ],
            }

    async def top_skills(self, limit: int = 10, days: int = 7) -> list[dict]:
        """按总成本排序的 Top N Skill"""
        cutoff = now_bjt() - timedelta(days=days)
        async with _db_mod.async_session_factory() as session:
            stmt = (
                select(
                    UsageLog.skill_id,
                    func.count(UsageLog.id),
                    func.coalesce(func.sum(UsageLog.cost_usd), 0),
                    func.coalesce(func.sum(UsageLog.input_tokens), 0),
                    func.coalesce(func.sum(UsageLog.output_tokens), 0),
                )
                .where(UsageLog.ts >= cutoff)
                .where(UsageLog.skill_id.isnot(None))
                .group_by(UsageLog.skill_id)
                .order_by(func.sum(UsageLog.cost_usd).desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).all()
            return [
                {
                    "skill_id": r[0],
                    "calls": r[1],
                    "cost_usd": float(r[2] or 0),
                    "input_tokens": int(r[3] or 0),
                    "output_tokens": int(r[4] or 0),
                }
                for r in rows
            ]

    async def top_users(self, limit: int = 10, days: int = 7) -> list[dict]:
        """按总成本排序的 Top N 用户

        Returns:
            [{user_id, calls, cost_usd, input_tokens, output_tokens, last_used}]
        """
        cutoff = now_bjt() - timedelta(days=days)
        async with _db_mod.async_session_factory() as session:
            stmt = (
                select(
                    UsageLog.user_id,
                    func.count(UsageLog.id),
                    func.coalesce(func.sum(UsageLog.cost_usd), 0),
                    func.coalesce(func.sum(UsageLog.input_tokens), 0),
                    func.coalesce(func.sum(UsageLog.output_tokens), 0),
                    func.max(UsageLog.ts),
                )
                .where(UsageLog.ts >= cutoff)
                .where(UsageLog.user_id.isnot(None))
                .group_by(UsageLog.user_id)
                .order_by(func.sum(UsageLog.cost_usd).desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).all()
            return [
                {
                    "user_id": r[0],
                    "calls": r[1],
                    "cost_usd": float(r[2] or 0),
                    "input_tokens": int(r[3] or 0),
                    "output_tokens": int(r[4] or 0),
                    "last_used": isoformat_bjt(r[5]) if r[5] else None,
                }
                for r in rows
            ]

    # 允许的分组维度（白名单），avoid SQL injection
    REPORT_GROUP_BY_FIELDS = (
        "user_id", "department", "skill_id", "model", "call_source",
    )

    async def report(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        department: str | None = None,
        model: str | None = None,
        call_source: str | None = None,
        group_by: str = "user_id",
        limit: int = 100,
    ) -> dict:
        """详细成本报表查询。

        Args:
            date_from / date_to: 时间范围（半开区间）
            department / model / call_source: 可选过滤
            group_by: 分组维度，必须在 REPORT_GROUP_BY_FIELDS 白名单内，
                      传入未知值会 raise ValueError（router 层应转 422）
            limit: 每个分组返回的最大条目数

        Returns:
            {
                "total_cost_usd": float,
                "total_calls": int,
                "total_input_tokens": int,
                "total_output_tokens": int,
                "group_by": str,
                "items": [{key, calls, cost_usd, input_tokens, output_tokens}, ...],
                "truncated": bool,  # 是否被 limit 截断（>= limit 行）
            }

        Raises:
            ValueError: group_by 非白名单值（Codex MEDIUM 修复：fail fast，
                        避免静默降级到 user_id 让客户端拿到错误数据）
        """
        group_columns = {
            "user_id": UsageLog.user_id,
            "department": UsageLog.department,
            "skill_id": UsageLog.skill_id,
            "model": UsageLog.model,
            "call_source": UsageLog.call_source,
        }
        if group_by not in group_columns:
            raise ValueError(
                f"group_by 必须是 {sorted(group_columns)} 之一，收到: {group_by!r}"
            )
        group_col = group_columns[group_by]

        async with _db_mod.async_session_factory() as session:
            conditions = []
            if date_from:
                conditions.append(UsageLog.ts >= date_from)
            if date_to:
                conditions.append(UsageLog.ts < date_to)
            if department:
                conditions.append(UsageLog.department == department)
            if model:
                conditions.append(UsageLog.model == model)
            if call_source:
                conditions.append(UsageLog.call_source == call_source)

            # 总览
            total_stmt = select(
                func.count(UsageLog.id),
                func.coalesce(func.sum(UsageLog.cost_usd), 0),
                func.coalesce(func.sum(UsageLog.input_tokens), 0),
                func.coalesce(func.sum(UsageLog.output_tokens), 0),
            )
            for c in conditions:
                total_stmt = total_stmt.where(c)
            total_row = (await session.execute(total_stmt)).one()

            # 按指定维度分组
            grouped_stmt = (
                select(
                    group_col,
                    func.count(UsageLog.id),
                    func.coalesce(func.sum(UsageLog.cost_usd), 0),
                    func.coalesce(func.sum(UsageLog.input_tokens), 0),
                    func.coalesce(func.sum(UsageLog.output_tokens), 0),
                )
                .group_by(group_col)
                .order_by(func.sum(UsageLog.cost_usd).desc())
                .limit(limit)
            )
            for c in conditions:
                grouped_stmt = grouped_stmt.where(c)
            grouped_rows = (await session.execute(grouped_stmt)).all()

            # 查询不同分组键的总数（用于判断是否截断）
            distinct_stmt = select(func.count(func.distinct(group_col)))
            for c in conditions:
                distinct_stmt = distinct_stmt.where(c)
            distinct_groups = (await session.execute(distinct_stmt)).scalar() or 0

            return {
                "group_by": group_by,
                "total_calls": int(total_row[0] or 0),
                "total_cost_usd": float(total_row[1] or 0),
                "total_input_tokens": int(total_row[2] or 0),
                "total_output_tokens": int(total_row[3] or 0),
                "items": [
                    {
                        "key": r[0] or "(unknown)",
                        "calls": int(r[1] or 0),
                        "cost_usd": float(r[2] or 0),
                        "input_tokens": int(r[3] or 0),
                        "output_tokens": int(r[4] or 0),
                    }
                    for r in grouped_rows
                ],
                # Codex MEDIUM 修复：明示是否被 limit 截断
                "limit": limit,
                "total_groups": int(distinct_groups),
                "truncated": int(distinct_groups) > limit,
            }

    async def list_call_sources(self, days: int = 30) -> list[str]:
        """列出最近 N 天出现过的所有 call_source（用于前端筛选下拉动态加载）

        Codex MEDIUM-D4 修复：避免前端硬编码 call_source 列表落后于真实埋点。
        """
        cutoff = now_bjt() - timedelta(days=days)
        try:
            async with _db_mod.async_session_factory() as session:
                stmt = (
                    select(UsageLog.call_source)
                    .where(UsageLog.ts >= cutoff)
                    .where(UsageLog.call_source.isnot(None))
                    .distinct()
                    .order_by(UsageLog.call_source)
                )
                rows = (await session.execute(stmt)).all()
                return [r[0] for r in rows if r[0]]
        except Exception as e:
            logger.warning(f"列举 call_sources 失败: {e}")
            return []

    async def daily_costs(self, days: int = 14) -> list[dict]:
        """最近 N 天的每日成本曲线"""
        cutoff = now_bjt() - timedelta(days=days)
        async with _db_mod.async_session_factory() as session:
            stmt = (
                select(
                    func.date(UsageLog.ts).label("day"),
                    func.count(UsageLog.id),
                    func.coalesce(func.sum(UsageLog.cost_usd), 0),
                )
                .where(UsageLog.ts >= cutoff)
                .group_by(func.date(UsageLog.ts))
                .order_by(func.date(UsageLog.ts))
            )
            rows = (await session.execute(stmt)).all()
            return [
                {
                    "date": r[0].isoformat() if r[0] else None,
                    "calls": r[1],
                    "cost_usd": float(r[2] or 0),
                }
                for r in rows
            ]


# 全局单例
cost_tracker = CostTracker()
