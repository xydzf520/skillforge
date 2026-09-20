"""
APScheduler定时调度：按Cron触发Skill执行。
启动时从数据库加载所有active+cron类型的Skill，创建定时任务。
多 worker 部署安全：通过 pg_advisory_xact_lock 防止重复执行。
"""

import hashlib
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy import func as sa_func

from app.config import settings
from app.execution.bridge_placement import (
    active_platform_fingerprints,
    bridge_has_platform_fingerprint_conflict,
)
from app.execution.execution_service import execution_service
from app.execution.models import ExecutionRun, NodeScheduleConfig, OpenClawInstance
from app.skills.core.models import Skill
from app.common.time_utils import isoformat_bjt, now_bjt


def _sf():
    from app.database import async_session_factory
    return async_session_factory


scheduler = AsyncIOScheduler(timezone=settings.SCHEDULER_TIMEZONE)
_NODE_BRIDGE_ONLINE_TTL = timedelta(seconds=90)


def _scheduler_timezone() -> ZoneInfo:
    return ZoneInfo(settings.SCHEDULER_TIMEZONE)


def _cron_from_crontab(expression: str) -> CronTrigger:
    return CronTrigger.from_crontab(expression, timezone=_scheduler_timezone())


def _as_bjt_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    return value


def _bridge_runtime_online(inst: OpenClawInstance, *, now: datetime | None = None) -> bool:
    """Return true only for a live in-process bridge connection with fresh DB heartbeat."""
    if not inst.is_active:
        return False
    try:
        from app.aiclaw.bridge_registry import bridge_registry
        if not bridge_registry.is_online(inst.id):
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("检查 bridge 在线注册表失败 instance={}: {}", inst.id, exc)
        return False

    current = _as_bjt_naive(now) or now_bjt()
    heartbeat = _as_bjt_naive(inst.last_heartbeat) or _as_bjt_naive(inst.bridge_connected_at)
    if heartbeat is None:
        return False
    age = (current - heartbeat).total_seconds()
    return 0 <= age <= _NODE_BRIDGE_ONLINE_TTL.total_seconds()


def _watchdog_expected_window(
    cron_expression: str,
    *,
    now: datetime | None = None,
    tolerance: timedelta = timedelta(minutes=5),
) -> tuple[datetime, datetime, datetime] | None:
    """Return expected local fire time and DB query window（BJT naive）。

    execution_service 写入 started_at 使用 now_bjt()（BJT naive），
    本函数返回的查询窗口必须同样使用 BJT naive，否则数字比较会错位。
    """
    scheduler_tz = _scheduler_timezone()
    current = now or datetime.now(scheduler_tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=scheduler_tz)
    else:
        current = current.astimezone(scheduler_tz)

    trigger = CronTrigger.from_crontab(cron_expression, timezone=scheduler_tz)
    expected = trigger.get_next_fire_time(None, current - timedelta(hours=1))
    if expected is None:
        return None
    if expected.tzinfo is None:
        expected = expected.replace(tzinfo=scheduler_tz)
    else:
        expected = expected.astimezone(scheduler_tz)
    if expected > current:
        return None
    if (current - expected) < tolerance:
        return None

    # 用 BJT naive 与 execution_service 的 now_bjt() 对齐
    expected_bjt = expected.replace(tzinfo=None)
    current_bjt = current.replace(tzinfo=None)
    return expected, expected_bjt - tolerance, current_bjt


async def _node_schedule_state(skill_id: str) -> tuple[dict | None, bool, bool, datetime | None]:
    """Return the preferred online node schedule and center-fallback policy."""
    async with _sf()() as session:
        rows = (
            await session.execute(
                select(NodeScheduleConfig, OpenClawInstance)
                .join(OpenClawInstance, OpenClawInstance.id == NodeScheduleConfig.instance_id)
                .where(NodeScheduleConfig.skill_id == skill_id)
                .where(NodeScheduleConfig.ack_ok.is_(True))
                .order_by(NodeScheduleConfig.pushed_at.desc())
            )
        ).all()
        platform_fingerprints = await active_platform_fingerprints(session)

    has_node_schedule = bool(rows)
    allow_center_fallback = False
    online_schedule: dict | None = None
    latest_pushed_at: datetime | None = None
    for cfg, inst in rows:
        if latest_pushed_at is None or cfg.pushed_at > latest_pushed_at:
            latest_pushed_at = cfg.pushed_at
        runtime = cfg.runtime_config if isinstance(cfg.runtime_config, dict) else {}
        allow_center_fallback = allow_center_fallback or bool(runtime.get("allow_center_fallback"))
        if bridge_has_platform_fingerprint_conflict(inst, platform_fingerprints):
            logger.warning(
                "跳过节点调度: {} instance={} 与平台主节点同机，需在真实节点机器重新安装 Bridge",
                skill_id,
                inst.id,
            )
            continue
        if online_schedule is None and _bridge_runtime_online(inst):
            online_schedule = {
                "instance_id": inst.id,
                "runtime": runtime,
                "runtime_backend": cfg.runtime_backend,
                "cron_expression": cfg.cron_expression,
                "config_version": cfg.config_version,
                "pushed_at": cfg.pushed_at,
            }
    return online_schedule, has_node_schedule, allow_center_fallback, latest_pushed_at


async def _trigger_remote_schedule_fallback(
    *,
    skill_id: str,
    node_schedule: dict,
    expected_at: datetime,
) -> dict:
    runtime = dict(node_schedule.get("runtime") or {})
    backend = str(
        runtime.get("backend")
        or node_schedule.get("runtime_backend")
        or "bridge_script"
    ).strip().lower()
    if backend not in {"openclaw_agent", "hybrid", "bridge_script"}:
        backend = "bridge_script"

    script_entry = str(runtime.get("script_entry") or runtime.get("script_path") or "scripts/main.py")
    timeout = runtime.get("timeout") or 300
    params = {
        "_execution_backend": backend,
        "_aiclaw_instance_id": node_schedule["instance_id"],
        "_execution": {
            "backend": backend,
            "instance_id": node_schedule["instance_id"],
            "script_path": script_entry,
            "timeout": timeout,
            "scheduled_at": isoformat_bjt(expected_at),
            "fallback_reason": "node_schedule_missed",
        },
    }
    return await execution_service.execute_skill(
        skill_id=skill_id,
        params=params,
        triggered_by="scheduler:fallback",
    )


async def _recent_schedule_run_count(
    skill_id: str,
    *,
    since: datetime,
    trigger_types: list[str] | None = None,
    statuses: list[str] | None = None,
) -> int:
    async with _sf()() as session:
        stmt = (
            select(sa_func.count(ExecutionRun.id))
            .where(ExecutionRun.skill_id == skill_id)
            .where(ExecutionRun.started_at >= since)
        )
        if trigger_types:
            stmt = stmt.where(ExecutionRun.trigger_type.in_(trigger_types))
        if statuses:
            stmt = stmt.where(ExecutionRun.status.in_(statuses))
        return (await session.execute(stmt)).scalar() or 0


@asynccontextmanager
async def _pg_advisory_lock(job_key: str):
    """基于 PostgreSQL pg_try_advisory_lock 的分布式锁。
    非阻塞：拿不到锁直接 yield False，拿到锁 yield True。
    使用 session-level lock（非事务级），在 context 退出时显式释放。
    """
    lock_id = int.from_bytes(
        hashlib.sha256(job_key.encode()).digest()[:8], "big"
    ) & 0x7FFFFFFFFFFFFFFF
    async with _sf()() as session:
        result = await session.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
        acquired = result.scalar()
        if not acquired:
            yield False
        else:
            try:
                yield True
            finally:
                await session.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": lock_id},
                )


async def _execute_skill_job(skill_id: str):
    """定时任务回调：执行Skill（加分布式锁）"""
    async with _pg_advisory_lock(f"skill_job:{skill_id}") as acquired:
        if not acquired:
            logger.debug(f"跳过重复执行: {skill_id}（另一实例持有锁）")
            return
        recent_node = await _recent_schedule_run_count(
            skill_id,
            since=now_bjt() - timedelta(minutes=10),
            trigger_types=["node_scheduler"],
            statuses=["running", "completed"],
        )
        if recent_node > 0:
            logger.debug(f"跳过集中执行: {skill_id}（节点已上报或正在执行）")
            return

        node_schedule, has_node_schedule, allow_center_fallback, _ = await _node_schedule_state(skill_id)
        if node_schedule is not None:
            logger.debug(
                "跳过集中执行: {}（节点调度已下发且在线 instance={}）",
                skill_id,
                node_schedule["instance_id"],
            )
            return
        if has_node_schedule and not allow_center_fallback:
            logger.warning(
                "跳过集中执行: {}（已存在节点调度配置，但没有可用的合规在线节点）",
                skill_id,
            )
            return

        logger.info(f"定时触发Skill: {skill_id}")
        try:
            result = await execution_service.execute_skill(
                skill_id=skill_id,
                triggered_by="scheduler",
            )
            logger.info(f"Skill执行完成: {skill_id} run_id={result['run_id']}")
        except Exception as e:
            logger.error(f"定时执行失败: {skill_id} error={e}")
            # bridge 断连时退化为远程触发，让节点在下次重连时补执行
            if "bridge" in str(e).lower() or "断开" in str(e):
                try:
                    node_schedule, _, _, _ = await _node_schedule_state(skill_id)
                    if node_schedule:
                        logger.warning("中心直接执行失败，尝试远程补触发: {}", skill_id)
                        await _trigger_remote_schedule_fallback(
                            skill_id=skill_id,
                            node_schedule=node_schedule,
                            expected_at=now_bjt(),
                        )
                    else:
                        logger.warning("无可用节点，放弃补触发: {}", skill_id)
                except Exception as fallback_err:
                    logger.error("补触发也失败: {} error={}", skill_id, fallback_err)


async def load_skill_jobs():
    """从数据库加载所有需要定时执行的Skill"""
    async with _sf()() as session:
        result = await session.execute(
            select(Skill)
            .where(Skill.status == "active")
            .where(Skill.trigger_type == "cron")
            .where(Skill.trigger_expression.isnot(None))
        )
        skills = result.scalars().all()

    for skill in skills:
        try:
            trigger = _cron_from_crontab(skill.trigger_expression)
            scheduler.add_job(
                _execute_skill_job,
                trigger=trigger,
                args=[skill.id],
                id=f"skill_{skill.id}",
                replace_existing=True,
                name=f"Skill: {skill.name}",
            )
            logger.info(f"注册定时任务: {skill.id} cron={skill.trigger_expression}")
        except Exception as e:
            logger.error(f"注册定时任务失败: {skill.id} error={e}")

    logger.info(f"共加载 {len(skills)} 个定时Skill")


async def _execute_data_pull_job(source_id: str):
    """定时任务回调：拉取API数据源"""
    logger.info(f"定时触发API数据拉取: {source_id}")
    try:
        from app.datasources import service as ds_service
        async with _sf()() as session:
            result = await ds_service.pull_api_source(
                session,
                source_id=source_id,
                user_id="scheduler",
            )
            await session.commit()
        logger.info(f"API数据拉取完成: {source_id} rows={result['row_count']} status={result['status']}")
    except Exception as e:
        logger.error(f"定时API拉取失败: {source_id} error={e}")


async def load_data_source_jobs():
    """从数据库加载所有需要定时拉取的API数据源"""
    from app.datasources.models import DataSource

    async with _sf()() as session:
        result = await session.execute(
            select(DataSource)
            .where(DataSource.source_type == "api_pull")
            .where(DataSource.is_active == True)  # noqa: E712
            .where(DataSource.schedule.isnot(None))
        )
        sources = result.scalars().all()

    for src in sources:
        try:
            trigger = _cron_from_crontab(src.schedule)
            scheduler.add_job(
                _execute_data_pull_job,
                trigger=trigger,
                args=[src.id],
                id=f"datasource_{src.id}",
                replace_existing=True,
                name=f"数据拉取: {src.name}",
            )
            logger.info(f"注册数据拉取任务: {src.id} cron={src.schedule}")
        except Exception as e:
            logger.error(f"注册数据拉取任务失败: {src.id} error={e}")

    logger.info(f"共加载 {len(sources)} 个定时数据拉取任务")


async def start_scheduler():
    """启动调度器（在应用startup时调用）"""
    await load_skill_jobs()
    await load_data_source_jobs()
    _register_system_jobs()
    scheduler.start()
    logger.info("APScheduler已启动")


async def _job_schedule_watchdog():
    """检测节点漏执行的定时任务并触发 fallback。"""
    async with _pg_advisory_lock("system:schedule_watchdog") as acquired:
        if not acquired:
            return
        try:
            async with _sf()() as session:
                skills = (await session.execute(
                    select(Skill)
                    .where(Skill.status == "active")
                    .where(Skill.trigger_type == "cron")
                    .where(Skill.trigger_expression.isnot(None))
                )).scalars().all()

            tolerance = timedelta(minutes=5)
            now = datetime.now(_scheduler_timezone())

            for skill in skills:
                try:
                    window = _watchdog_expected_window(
                        skill.trigger_expression,
                        now=now,
                        tolerance=tolerance,
                    )
                    if window is None:
                        continue
                    expected, started_from, started_to = window

                    async with _sf()() as session:
                        count = (await session.execute(
                            select(sa_func.count(ExecutionRun.id))
                            .where(ExecutionRun.skill_id == skill.id)
                            .where(ExecutionRun.started_at >= started_from)
                            .where(ExecutionRun.started_at <= started_to)
                            .where(ExecutionRun.trigger_type.in_(["scheduler", "node_scheduler", "scheduler:fallback"]))
                        )).scalar() or 0

                    if count == 0:
                        node_schedule, has_node_schedule, allow_center_fallback, latest_pushed_at = await _node_schedule_state(skill.id)
                        if latest_pushed_at is not None and expected.replace(tzinfo=None) <= latest_pushed_at:
                            logger.debug(
                                "watchdog: 跳过变更前预期时间 {} expected={} pushed_at={}",
                                skill.id,
                                expected,
                                latest_pushed_at,
                            )
                            continue
                        if node_schedule is not None:
                            # 节点在线且已确认调度 → 检查节点近期是否已执行
                            recent_node = await _recent_schedule_run_count(
                                skill.id,
                                since=now_bjt() - timedelta(minutes=15),
                                trigger_types=["node_scheduler", "scheduler:fallback"],
                                statuses=["running", "completed"],
                            )
                            if recent_node > 0:
                                logger.debug(
                                    "watchdog: 节点已执行或正在执行 {}，跳过 fallback instance={}",
                                    skill.id,
                                    node_schedule["instance_id"],
                                )
                                continue
                            # 节点在线但近期未执行 → 远程补触发同一节点 runtime
                            logger.warning(
                                "watchdog: 节点在线但无近期执行记录 {}，预期 {}，远程补触发 instance={}",
                                skill.id,
                                expected,
                                node_schedule["instance_id"],
                            )
                            await _trigger_remote_schedule_fallback(
                                skill_id=skill.id,
                                node_schedule=node_schedule,
                                expected_at=expected,
                            )
                            continue
                        if has_node_schedule and not allow_center_fallback:
                            logger.warning(
                                "watchdog: 漏执行 {}，预期 {}，但无在线节点且未允许中心 fallback",
                                skill.id,
                                expected,
                            )
                            continue
                        logger.warning(f"watchdog: 漏执行 {skill.id}，预期 {expected}，触发中心 fallback")
                        await _execute_skill_job(skill.id)
                except Exception as e:
                    logger.error(f"watchdog 检查失败 {skill.id}: {e}")
        except Exception as e:
            logger.error(f"schedule watchdog 异常: {e}")


def _register_system_jobs():
    """注册平台自身的定时任务"""

    # 1. T+1效果回收（每日10:00）
    scheduler.add_job(
        _job_t1_effect_collection,
        CronTrigger(hour=10, minute=0),
        id="system_t1_effect",
        replace_existing=True,
        name="T+1效果回收",
    )

    # 2. 董事长周报（每周五17:00）
    scheduler.add_job(
        _job_weekly_report,
        CronTrigger(day_of_week="fri", hour=17, minute=0),
        id="system_weekly_report",
        replace_existing=True,
        name="董事长周报",
    )

    # 3. 执行超时清理（每小时）
    scheduler.add_job(
        _job_timeout_cleanup,
        CronTrigger(minute=0),
        id="system_timeout_cleanup",
        replace_existing=True,
        name="执行超时清理",
    )

    # 4. 数据源过期检查（每小时）
    scheduler.add_job(
        _job_data_staleness_check,
        CronTrigger(minute=30),
        id="system_data_staleness",
        replace_existing=True,
        name="数据源过期检查",
    )

    # 5. 审计日志清理（每日03:00）
    scheduler.add_job(
        _job_audit_cleanup,
        CronTrigger(hour=3, minute=0),
        id="system_audit_cleanup",
        replace_existing=True,
        name="审计日志清理",
    )

    # 6. 审核 SLA 催办（每30分钟）
    from app.reviews.sla_checker import check_review_sla
    scheduler.add_job(
        check_review_sla,
        CronTrigger(minute="*/30"),
        id="review_sla_check",
        replace_existing=True,
        name="审核SLA催办检查",
    )

    # 7. OpenClaw 心跳检测（每分钟）
    scheduler.add_job(
        _job_openclaw_heartbeat,
        CronTrigger(second=0),  # 每分钟
        id="system_openclaw_heartbeat",
        replace_existing=True,
        name="OpenClaw心跳检测",
    )

    # 8. Nightly Skill 自动优化（每日02:00）
    scheduler.add_job(
        _job_nightly_optimize,
        CronTrigger(hour=2, minute=0),
        id="system_nightly_optimize",
        replace_existing=True,
        name="Nightly自动优化",
    )

    # 9. 节点调度看门狗（每5分钟）
    scheduler.add_job(
        _job_schedule_watchdog,
        CronTrigger(minute="*/5"),
        id="system_schedule_watchdog",
        replace_existing=True,
        name="节点调度看门狗",
    )

    logger.info("注册9个系统定时任务")


# ===== 系统定时任务实现 =====

async def _job_t1_effect_collection():
    """T+1效果回收：查昨日执行记录，拉T+1业务数据，回填business_impact"""
    async with _pg_advisory_lock("system:t1_effect") as acquired:
        if not acquired:
            logger.debug("跳过T+1回收（另一实例执行中）")
            return

        from datetime import datetime, timedelta
        from sqlalchemy import select
        from app.execution.models import DecisionLog

        logger.info("开始T+1效果回收")
        now = now_bjt()
        yesterday_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        yesterday_end = yesterday_start + timedelta(days=1)

        async with _sf()() as session:
            result = await session.execute(
                select(DecisionLog)
                .where(DecisionLog.created_at >= yesterday_start)
                .where(DecisionLog.created_at < yesterday_end)
                .where(DecisionLog.is_sandbox == False)  # noqa: E712
                .where(DecisionLog.business_impact.is_(None))
            )
            logs = result.scalars().all()

            count = 0
            for log in logs:
                impact = await _collect_impact_for_decision(session, log, yesterday_start)
                log.business_impact = impact
                count += 1

            await session.commit()

        # T+1 卡片回更：为已回收的决策更新钉钉卡片（best-effort）
        updated_cards = 0
        try:
            from app.dingtalk.client import dingtalk_client
            from app.dingtalk.card_templates import build_t1_impact_update
            for log in logs:
                if not log.business_impact or not log.business_impact.get("collected"):
                    continue
                run_id = log.run_id
                if not run_id:
                    continue
                try:
                    card_data = build_t1_impact_update(log.business_impact)
                    await dingtalk_client.update_interactive_card(f"exec_{run_id}", card_data)
                    updated_cards += 1
                except Exception as e:
                    logger.debug(f"T+1卡片更新跳过 run_id={run_id}: {e}")
        except Exception as e:
            logger.warning(f"T+1卡片批量更新异常: {e}")

        logger.info(f"T+1效果回收完成: {count}条, 卡片更新: {updated_cards}张")

    logger.info(f"T+1效果回收完成: {count}条, 卡片更新: {updated_cards}张")


async def _collect_impact_for_decision(session, decision_log, date):
    """
    为单条决策日志采集 T+1 业务影响。
    通过 skill_id 找到关联的数据源，查询最新数据，与决策输出对比。
    """
    from sqlalchemy import select
    from app.datasources.models import DataSource, DataIngestionLog
    from app.skills.core.models import Skill

    date_str = date.strftime("%Y-%m-%d")
    skill_id = decision_log.skill_id
    output = decision_log.output_result or {}
    suggested = decision_log.suggested_action or {}
    user_action = decision_log.user_action

    # 查找 Skill 关联的数据源
    ds_result = await session.execute(
        select(DataSource).where(DataSource.related_skills.contains([skill_id]))
    )
    sources = ds_result.scalars().all()

    if not sources:
        return {"collected": True, "date": date_str, "source": "no_datasource"}

    # 取第一个数据源的最新成功上传
    source = sources[0]
    ingestion_result = await session.execute(
        select(DataIngestionLog)
        .where(DataIngestionLog.source_id == source.id)
        .where(DataIngestionLog.status == "success")
        .order_by(DataIngestionLog.created_at.desc())
        .limit(1)
    )
    latest = ingestion_result.scalar_one_or_none()

    impact = {
        "collected": True,
        "date": date_str,
        "datasource": source.name,
        "datasource_id": source.id,
        "latest_data_rows": latest.row_count if latest else 0,
        "decision_conclusion": output.get("conclusion", ""),
        "suggested_action": suggested.get("action", ""),
        "user_action": user_action,
        "adopted": user_action in ("completed", "approved", None),
    }

    # 提取金额影响
    for key in ("amount", "budget", "cost", "revenue", "impact_amount"):
        val = output.get(key) or suggested.get(key)
        if val is not None:
            try:
                impact["amount"] = float(val)
            except (ValueError, TypeError):
                pass
            break

    return impact


async def _job_weekly_report():
    """董事长周报：调用 generate_weekly_report 获取丰富数据 → 钉钉推送"""
    from sqlalchemy import select
    from app.dashboard.service import generate_weekly_report
    from app.dingtalk.outbox import outbox
    from app.dingtalk.card_templates import build_weekly_report
    from app.auth.models import User

    logger.info("开始生成董事长周报")

    async with _sf()() as session:
        # 生成完整周报数据（全局视角，不限部门）
        report = await generate_weekly_report(session, department=None)

        # 找董事长/高管（can_view_all=True的用户）
        directors = (await session.execute(
            select(User).where(User.can_view_all == True).where(User.is_active == True)  # noqa: E712
        )).scalars().all()

    # 构造 build_weekly_report 所需的 stats 格式
    # top_skills 转为名称列表，与卡片模板的排行展示格式匹配
    top_skill_names = [
        f'{s["skill_id"]}（{s["executions"]}次, 采纳{s["adoption_rate"]}%）'
        for s in report.get("top_skills", [])
    ]

    # 亮点拼接为业务影响说明
    highlights = report.get("highlights", [])
    business_impact = "\n".join(f"- {h}" for h in highlights) if highlights else ""

    card = build_weekly_report({
        "period": report.get("period", "本周"),
        "total_executions": report.get("total_executions", 0),
        "adoption_rate": report.get("adoption_rate", 0),
        "active_skills": report.get("active_skills", 0),
        "top_skills": top_skill_names,
        "business_impact": business_impact,
    })

    for d in directors:
        if d.dingtalk_user_id:
            await outbox.enqueue("work_notice", d.dingtalk_user_id, card, priority=5)

    logger.info(
        f"周报生成完成: 执行{report.get('total_executions', 0)}次 "
        f"采纳率{report.get('adoption_rate', 0)}% "
        f"趋势: {report.get('trend_summary', '')}"
    )


async def _job_timeout_cleanup():
    """执行超时清理：标记超时的执行记录"""
    from datetime import timedelta
    from sqlalchemy import update
    from app.execution.models import ExecutionRun
    from app.tasktree.models import TaskNodeLight

    cleanup_at = now_bjt()
    platform_threshold = cleanup_at - timedelta(minutes=30)
    node_threshold = cleanup_at - timedelta(hours=4)
    timed_out: list[str] = []
    async with _sf()() as session:
        result = await session.execute(
            update(ExecutionRun)
            .where(ExecutionRun.status == "running")
            .where(ExecutionRun.source_instance_id.is_(None))
            .where(ExecutionRun.started_at < platform_threshold)
            .values(status="timeout", completed_at=cleanup_at)
            .returning(ExecutionRun.id)
        )
        timed_out.extend(row[0] for row in result.all())
        result = await session.execute(
            update(ExecutionRun)
            .where(ExecutionRun.status == "running")
            .where(ExecutionRun.source_instance_id.is_not(None))
            .where(ExecutionRun.started_at < node_threshold)
            .values(status="timeout", completed_at=cleanup_at)
            .returning(ExecutionRun.id)
        )
        timed_out.extend(row[0] for row in result.all())
        if timed_out:
            await session.execute(
                update(TaskNodeLight)
                .where(TaskNodeLight.source_run_id.in_(timed_out))
                .where(TaskNodeLight.node_type.in_(("execution", "run")))
                .values(status="timeout", finished_at=cleanup_at, updated_at=cleanup_at)
            )
        await session.commit()

    if timed_out:
        logger.warning(f"超时清理: {len(timed_out)}条执行记录标记为timeout")


async def _job_data_staleness_check():
    """数据源过期检查：超过阈值未更新的数据源发钉钉告警（N+1优化）"""
    from datetime import datetime
    from sqlalchemy import select, func
    from app.datasources.models import DataSource, DataIngestionLog
    from app.dingtalk.outbox import outbox
    from app.dingtalk.card_templates import build_data_alert
    from app.auth.models import User

    logger.info("开始数据源过期检查")
    stale_count = 0

    async with _sf()() as session:
        # 优化1：LEFT JOIN 一次性获取所有数据源 + 最后成功导入时间
        result = await session.execute(
            select(
                DataSource,
                func.max(DataIngestionLog.created_at).label("last_ingestion_at"),
            )
            .outerjoin(
                DataIngestionLog,
                (DataIngestionLog.source_id == DataSource.id)
                & (DataIngestionLog.status == "success"),
            )
            .where(DataSource.is_active == True)  # noqa: E712
            .group_by(DataSource.id)
        )
        sources_with_times = result.all()

        # 优化2：预加载所有可能的 creator 用户
        creator_ids = {row[0].created_by for row in sources_with_times if row[0].created_by}
        creators = {}
        if creator_ids:
            creators_result = await session.execute(
                select(User).where(User.id.in_(creator_ids))
            )
            creators = {u.id: u for u in creators_result.scalars().all()}

        # 优化3：只查一次 admin/ai_engineer 列表
        admins_result = await session.execute(
            select(User).where(
                User.role.in_(["admin", "ai_engineer"]),
                User.is_active == True,  # noqa: E712
                User.dingtalk_user_id.isnot(None),
            )
        )
        admins = admins_result.scalars().all()

        for src, last_time in sources_with_times:
            if last_time:
                hours_since = (now_bjt() - last_time).total_seconds() / 3600
            else:
                hours_since = (now_bjt() - src.created_at).total_seconds() / 3600

            if hours_since > src.stale_threshold_hours:
                stale_count += 1
                hours_int = int(hours_since)
                related_skills = src.related_skills or []
                logger.warning(f"数据源过期: {src.name} ({hours_int}h未更新)")

                card = build_data_alert(
                    source_name=src.name,
                    hours_since_update=hours_int,
                    affected_skills=related_skills,
                )

                notified_ids = set()

                # 通知创建者（从预加载 dict 获取）
                if src.created_by and src.created_by in creators:
                    creator_user = creators[src.created_by]
                    if creator_user.dingtalk_user_id:
                        notified_ids.add(creator_user.id)
                        await outbox.enqueue(
                            "work_notice",
                            creator_user.dingtalk_user_id,
                            card,
                            priority=5,
                            related_type="data_stale",
                            related_id=src.id,
                            session=session,
                        )

                # 通知 admin/ai_engineer（已预加载）
                for admin in admins:
                    if admin.id not in notified_ids:
                        notified_ids.add(admin.id)
                        await outbox.enqueue(
                            "work_notice",
                            admin.dingtalk_user_id,
                            card,
                            priority=5,
                            related_type="data_stale",
                            related_id=src.id,
                            session=session,
                        )

    logger.info(f"数据源过期检查完成: {stale_count}个数据源过期")


async def _job_audit_cleanup():
    """审计日志清理：删除90天前的普通日志，180天前的安全日志"""
    from datetime import datetime, timedelta
    from sqlalchemy import delete, and_, not_
    from app.common.audit import AuditLog

    logger.info("开始审计日志清理")
    async with _sf()() as session:
        # 普通日志90天
        cutoff_normal = now_bjt() - timedelta(days=90)
        result = await session.execute(
            delete(AuditLog)
            .where(AuditLog.created_at < cutoff_normal)
            .where(not_(AuditLog.action.like("%_invalid")))
            .where(AuditLog.action != "user.login_failed")
        )
        normal_deleted = result.rowcount

        # 安全日志180天
        cutoff_security = now_bjt() - timedelta(days=180)
        result2 = await session.execute(
            delete(AuditLog).where(AuditLog.created_at < cutoff_security)
        )
        security_deleted = result2.rowcount

        await session.commit()
    logger.info(f"审计清理完成: 普通{normal_deleted}条 安全{security_deleted}条")


async def _job_openclaw_heartbeat():
    """OpenClaw 心跳检测：检查所有活跃实例的连通性。

    bridge 模式实例已经通过 SkillForge bridge_router 的 ws ping/pong 维护
    bridge_connected_at，scheduler 不需要再走 reload_hook 探测；只对 direct
    模式的老 OpenClaw 实例做 HTTP /health 探测。
    """
    from datetime import datetime
    from sqlalchemy import select
    from app.execution.models import OpenClawInstance
    from app.dingtalk.recipients import enqueue_admin_work_notice

    async with _sf()() as session:
        instances = (await session.execute(
            select(OpenClawInstance).where(OpenClawInstance.is_active == True)  # noqa: E712
        )).scalars().all()

        for inst in instances:
            # bridge 模式跳过：bridge 自己维护 connected_at + 心跳
            if (inst.connection_mode or "bridge") == "bridge":
                continue
            try:
                import httpx
                url = inst.reload_hook_url
                if not url.startswith("http"):
                    url = f"http://{url}"
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{url}/health")
                    data = resp.json()
                inst.last_heartbeat = now_bjt()
                try:
                    from app.tasktree.writer import writer as tasktree_writer

                    await tasktree_writer.sync_heartbeat_for_instance(
                        session,
                        instance=inst,
                        last_heartbeat_at=inst.last_heartbeat,
                    )
                except Exception as hb_err:
                    logger.warning(
                        "tasktree sync heartbeat 失败 (scheduler, instance={}): {}",
                        inst.id,
                        hb_err,
                        exc_info=True,
                    )
                if not data.get("ok", data.get("status") == "ok"):
                    logger.warning(f"OpenClaw实例 {inst.id} 心跳异常: {data}")
            except Exception as e:
                logger.error(f"OpenClaw实例 {inst.id} 心跳失败: {e}")
                if inst.last_heartbeat:
                    gap = (now_bjt() - inst.last_heartbeat).total_seconds()
                    if gap > 300:
                        # [H1] 共享 session: 心跳更新和离线告警同一事务
                        await enqueue_admin_work_notice(
                            {
                                "title": "OpenClaw 实例离线告警",
                                "markdown": f"**实例离线**\n\n- 实例: {inst.name} ({inst.id})\n- 最后心跳: {gap/60:.0f}分钟前\n- 错误: {e}",
                            },
                            priority=1,
                            related_type="openclaw_instance",
                            related_id=str(inst.id),
                            session=session,
                        )

        await session.commit()

    # 也检测默认实例（配置级别）
    try:
        from app.execution.openclaw_client import default_client
        status = await default_client.get_status()
        if not status.get("ok", status.get("online", False)):
            logger.warning(f"默认OpenClaw实例状态异常: {status}")
    except Exception as e:
        logger.warning(f"默认OpenClaw心跳检测失败: {e}")


async def _job_nightly_optimize():
    """Nightly 自动优化：为配置了 auto_optimize 的 Skill 创建并运行优化会话"""
    from app.database import async_session_factory
    from app.optimizer.controller import optimizer_controller
    from app.optimizer.models import OptimizerSession
    from app.skills.core.models import Skill
    from app.common.models import SystemConfig

    try:
        async with async_session_factory() as db:
            # 检查全局开关
            from sqlalchemy import select
            config_result = await db.execute(
                select(SystemConfig).where(SystemConfig.key == "optimizer.nightly_enabled")
            )
            config_row = config_result.scalar_one_or_none()
            if not config_row or not config_row.value:
                return

            # 获取所有 active 状态的 Skill
            skill_result = await db.execute(
                select(Skill).where(Skill.status == "active")
            )
            skills = skill_result.scalars().all()

            for skill in skills:
                # 检查是否已有运行中的优化会话
                running = await db.execute(
                    select(OptimizerSession)
                    .where(OptimizerSession.skill_id == skill.id)
                    .where(OptimizerSession.status == "running")
                )
                if running.scalar_one_or_none():
                    continue

                # 创建并启动优化会话
                try:
                    session = await optimizer_controller.create_session(
                        skill_id=skill.id,
                        name=f"nightly-{skill.id}",
                        goal="提高任务正确率",
                        config={
                            "editable_zones": ["branch_conditions", "thresholds"],
                            "frozen_zones": ["frontmatter", "output_definition"],
                            "budget": {"max_iterations": 10, "max_tokens": 200000, "max_sandbox_runs": 50},
                        },
                        benchmark_pack_id=None,
                        user_id="system",
                        db=db,
                    )
                    await db.commit()
                    await optimizer_controller.start(session_id=session["session_id"], db=db)
                    await db.commit()
                    logger.info(f"Nightly 优化已启动: {skill.id}")
                except Exception as e:
                    logger.warning(f"Nightly 优化 {skill.id} 失败: {e}")

    except Exception as e:
        logger.error(f"Nightly 优化任务异常: {e}")


def stop_scheduler():
    """停止调度器"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler已停止")
