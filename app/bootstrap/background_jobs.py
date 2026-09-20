from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from types import SimpleNamespace

from loguru import logger

from app.config import settings


@dataclass
class BackgroundTasksState:
    is_scheduler_worker: bool
    outbox_task: asyncio.Task | None = None
    cost_alert_cleanup_task: asyncio.Task | None = None
    quota_alert_task: asyncio.Task | None = None
    todo_expire_task: asyncio.Task | None = None
    todo_callback_retry_task: asyncio.Task | None = None
    coding_agent_gc_task: asyncio.Task | None = None
    bridge_gc_task: asyncio.Task | None = None
    tasktree_repair_task: asyncio.Task | None = None
    tasktree_drift_scan_task: asyncio.Task | None = None
    cookie_heartbeat_task: asyncio.Task | None = None
    direct_capability_task: asyncio.Task | None = None
    training_collect_due_task: asyncio.Task | None = None
    training_model_transfer_task: asyncio.Task | None = None
    training_full_history_finetune_task: asyncio.Task | None = None
    learning_auto_flow_task: asyncio.Task | None = None
    link_decline_operator_task: asyncio.Task | None = None
    media_job_task: asyncio.Task | None = None
    media_quality_task: asyncio.Task | None = None


async def cost_alert_cleanup_loop():
    """每 24h 清理过期的 cost_alert_seen.* 声明记录。"""
    from app.common.cost_tracker import cost_tracker

    await asyncio.sleep(60)
    while True:
        try:
            deleted = await cost_tracker.cleanup_old_alert_claims(retention_days=90)
            if deleted > 0:
                logger.info(f"成本告警声明清理完成: 删除 {deleted} 条")
        except asyncio.CancelledError:
            logger.info("成本告警清理任务已取消")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"成本告警清理异常（继续）: {exc}")
        await asyncio.sleep(86400)


async def quota_alert_check_loop():
    """每 5 分钟检查配额使用率，超阈值时发送渐进告警（80%/90%/100%）。"""
    from app.common.quota_alerter import check_and_alert_quotas

    await asyncio.sleep(30)  # 启动后等 30 秒再首次检查
    while True:
        try:
            alerts = await check_and_alert_quotas()
            if alerts:
                logger.info(f"配额告警检查完成: 发送 {len(alerts)} 条告警")
        except asyncio.CancelledError:
            logger.info("配额告警检查任务已取消")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"配额告警检查异常（继续）: {exc}")
        await asyncio.sleep(300)  # 每 5 分钟检查一次


async def tasktree_drift_scan_loop():
    """[codex-2026-04-14] 定时跑 drift_scan 对账扫描，持久化游标保证跨重启追赶。

    间隔由 settings.TASKTREE_DRIFT_SCAN_INTERVAL 控制，0 视为禁用（仅手工触发）。
    """
    from app.tasktree.drift_scan import run_drift_scan

    interval = max(0, int(settings.TASKTREE_DRIFT_SCAN_INTERVAL))
    window_hours = max(1, int(settings.TASKTREE_DRIFT_SCAN_WINDOW_HOURS))
    if interval <= 0:
        logger.info("tasktree drift_scan 后台任务已禁用 (TASKTREE_DRIFT_SCAN_INTERVAL=0)")
        return
    logger.info(
        "tasktree drift_scan 后台任务启动：每 {}s 扫一次，窗口 {}h",
        interval,
        window_hours,
    )
    # 启动后先等一段时间，避开其他启动任务争抢
    await asyncio.sleep(60)
    while True:
        try:
            result = await run_drift_scan(window_hours=window_hours)
            if result.get("enqueued"):
                logger.info(
                    "tasktree drift_scan 新入队 {} 条 (drift={} scanned={})",
                    result["enqueued"],
                    result["drift"],
                    result["scanned"],
                )
        except asyncio.CancelledError:
            logger.info("tasktree drift_scan 任务已取消")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("tasktree drift_scan 异常（继续）: {}", exc, exc_info=True)
        await asyncio.sleep(interval)


async def cookie_heartbeat_loop():
    """每 BROWSER_HEARTBEAT_INTERVAL 秒主动验证各平台 cookie 是否仍有效。

    结果写回 DataSource.config.{verify_status, verify_detail, last_verified_at}，
    供前端展示登态红绿灯，避免 cookie 静默过期导致 Skill 跑出错才发现。
    """
    if not settings.BROWSER_HEARTBEAT_ENABLED:
        logger.info("浏览器 cookie 心跳已禁用 (BROWSER_HEARTBEAT_ENABLED=false)")
        return

    from app.browser.service import PLATFORM_LOGIN_PROBE, verify_login
    from app.database import async_session_factory

    interval = max(60, int(settings.BROWSER_HEARTBEAT_INTERVAL))
    first_delay = max(0, int(settings.BROWSER_HEARTBEAT_FIRST_DELAY))
    logger.info(
        "浏览器 cookie 心跳启动：首跑 {}s 后，之后每 {}s 一次，监控 {} 个平台",
        first_delay, interval, len(PLATFORM_LOGIN_PROBE),
    )
    await asyncio.sleep(first_delay)

    while True:
        try:
            for source_id in list(PLATFORM_LOGIN_PROBE.keys()):
                try:
                    async with async_session_factory() as session:
                        result = await verify_login(
                            session, source_id=source_id, user_id="heartbeat",
                        )
                    logger.info(
                        "[heartbeat] {} → status={} verified={}",
                        source_id,
                        result.get("status"),
                        result.get("verified"),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[heartbeat] {} 验证异常（继续下一个）: {}",
                        source_id, exc,
                    )
        except asyncio.CancelledError:
            logger.info("浏览器 cookie 心跳任务已取消")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("浏览器 cookie 心跳整体异常（继续下一轮）: {}", exc)
        await asyncio.sleep(interval)


async def training_collect_due_loop():
    """Control-plane compensation for delayed or lost training gateway callbacks."""
    if not settings.TRAINING_COLLECT_DUE_ENABLED:
        logger.info("训练结果到期补偿已禁用 (TRAINING_COLLECT_DUE_ENABLED=false)")
        return

    from app.database import async_session_factory
    from app.training.service import collect_due_training_job_results

    interval = max(60, int(settings.TRAINING_COLLECT_DUE_INTERVAL))
    stale_seconds = max(60, int(settings.TRAINING_COLLECT_DUE_STALE_SECONDS))
    max_jobs = max(1, min(int(settings.TRAINING_COLLECT_DUE_MAX_JOBS), 100))
    actor = SimpleNamespace(
        id="training_reconciler",
        role="system_admin",
        department=None,
        can_view_all=True,
    )
    logger.info(
        "训练结果到期补偿启动：每 {}s 扫描一次，stale={}s，max_jobs={}",
        interval,
        stale_seconds,
        max_jobs,
    )
    await asyncio.sleep(min(120, interval))

    while True:
        try:
            async with async_session_factory() as session:
                result = await collect_due_training_job_results(
                    session,
                    actor,
                    stale_seconds=stale_seconds,
                    max_jobs=max_jobs,
                )
            stats = result.get("stats") or {}
            if stats.get("collected") or stats.get("failed") or stats.get("skipped"):
                logger.info(
                    "[training-collect-due] collected={} skipped={} failed={} scanned={}",
                    stats.get("collected", 0),
                    stats.get("skipped", 0),
                    stats.get("failed", 0),
                    result.get("total", 0),
                )
        except asyncio.CancelledError:
            logger.info("训练结果到期补偿任务已取消")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("[training-collect-due] 扫描失败（继续）: {}", exc)
        await asyncio.sleep(interval)


async def training_model_transfer_loop():
    """Resume durable model transfer tasks between internal Bridge nodes."""
    from app.database import async_session_factory
    from app.training.service import advance_training_model_transfers

    active_interval = 2
    idle_interval = 10
    max_idle_interval = 60
    current_idle_interval = idle_interval
    logger.info("训练模型传输后台推进启动：active={}s idle={}s", active_interval, idle_interval)
    await asyncio.sleep(5)

    while True:
        try:
            async with async_session_factory() as session:
                result = await advance_training_model_transfers(session, limit=1)
            has_activity = bool(result.get("started") or result.get("active"))
            if result.get("started"):
                logger.info(
                    "[training-model-transfer] scanned={} started={} active={}",
                    result.get("scanned", 0),
                    result.get("started", 0),
                    result.get("active", 0),
                )
        except asyncio.CancelledError:
            logger.info("训练模型传输后台推进已取消")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("[training-model-transfer] 推进失败（继续）: {}", exc, exc_info=True)
            has_activity = False
        if has_activity:
            current_idle_interval = idle_interval
            await asyncio.sleep(active_interval)
        else:
            await asyncio.sleep(current_idle_interval)
            current_idle_interval = min(max_idle_interval, current_idle_interval * 2)


async def training_full_history_finetune_loop():
    """Advance the full-history 4B finetune workflow without blocking web requests."""
    from app.common.cache import invalidate_training_cache
    from app.database import async_session_factory
    from app.tasktree.service import invalidate_tasktree
    from app.training.service import advance_full_history_finetune_automation

    active_interval = 2
    idle_interval = 10
    max_idle_interval = 60
    current_idle_interval = idle_interval
    logger.info("全量 4B 三轮微调后台推进启动：active={}s idle={}s", active_interval, idle_interval)
    await asyncio.sleep(5)

    while True:
        try:
            async with async_session_factory() as session:
                result = await advance_full_history_finetune_automation(session, limit=1)
            has_activity = bool(result.get("advanced") or result.get("active"))
            if result.get("advanced") or result.get("active"):
                await invalidate_training_cache()
                await invalidate_tasktree()
                logger.info(
                    "[full-history-finetune] status={} advanced={} active={} completed_cycles={}",
                    result.get("status"),
                    result.get("advanced", 0),
                    result.get("active", 0),
                    result.get("completed_cycles", 0),
                )
        except asyncio.CancelledError:
            logger.info("全量 4B 三轮微调后台推进已取消")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("[full-history-finetune] 推进失败（继续）: {}", exc, exc_info=True)
            has_activity = False
        if has_activity:
            current_idle_interval = idle_interval
            await asyncio.sleep(active_interval)
        else:
            await asyncio.sleep(current_idle_interval)
            current_idle_interval = min(max_idle_interval, current_idle_interval * 2)


async def direct_capability_task_loop():
    """Advance direct hall capability tasks even if the browser page is closed."""
    from app.database import async_session_factory
    from app.hall.direct_capability_service import advance_due_direct_capability_tasks

    active_interval = 1
    idle_interval = 3
    max_idle_interval = 30
    current_idle_interval = idle_interval
    logger.info(
        "大厅直接能力任务推进启动：有任务每 {}s 扫描，空闲退避 {}-{}s",
        active_interval,
        idle_interval,
        max_idle_interval,
    )
    await asyncio.sleep(3)

    while True:
        try:
            async with async_session_factory() as session:
                result = await advance_due_direct_capability_tasks(
                    session,
                    capability_ids=("gpt-imagegen", "gpt-imagegen-dialogue"),
                    limit=20,
                )
                await session.commit()
            has_activity = bool(result.get("advanced") or result.get("failed_stale"))
            if result.get("advanced") or result.get("failed_stale"):
                logger.info(
                    "[direct-capability] scanned={} advanced={} failed_stale={}",
                    result.get("scanned", 0),
                    result.get("advanced", 0),
                    result.get("failed_stale", 0),
                )
        except asyncio.CancelledError:
            logger.info("大厅直接能力任务推进已取消")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("[direct-capability] 任务推进失败（继续）: {}", exc, exc_info=True)
            has_activity = False
        if has_activity:
            current_idle_interval = idle_interval
            await asyncio.sleep(active_interval)
        else:
            await asyncio.sleep(current_idle_interval)
            current_idle_interval = min(max_idle_interval, current_idle_interval * 2)


async def media_job_task_loop():
    """Advance governed GPU media jobs even when the workbench is closed."""
    from app.database import async_session_factory
    from app.media.service import process_media_jobs_once

    active_interval = 2
    idle_interval = 5
    max_idle_interval = 30
    current_idle_interval = idle_interval
    logger.info("素材工作台媒体任务推进启动：active={}s idle={}-{}s", active_interval, idle_interval, max_idle_interval)
    await asyncio.sleep(5)
    while True:
        try:
            async with async_session_factory() as session:
                result = await process_media_jobs_once(session, limit=20)
            has_activity = bool(result.get("advanced"))
            if result.get("advanced") or result.get("failed"):
                logger.info(
                    "[media-job] scanned={} advanced={} failed={}",
                    result.get("scanned", 0),
                    result.get("advanced", 0),
                    result.get("failed", 0),
                )
        except asyncio.CancelledError:
            logger.info("素材工作台媒体任务推进已取消")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("素材工作台媒体任务推进失败（继续）: {}", exc, exc_info=True)
            has_activity = False
        if has_activity:
            current_idle_interval = idle_interval
            await asyncio.sleep(active_interval)
        else:
            await asyncio.sleep(current_idle_interval)
            current_idle_interval = min(max_idle_interval, current_idle_interval * 2)


async def media_quality_task_loop():
    """Analyze completed H3 assets without blocking GPU collection or review."""
    from app.database import async_session_factory
    from app.media.service import process_dialogue_deliveries_once, process_media_quality_analyses_once
    from app.media.workbench_v2 import backfill_review_manifests_once

    active_interval = 2
    idle_interval = 15
    max_idle_interval = 60
    current_idle_interval = idle_interval
    logger.info("素材工作台 AI 成片质检启动：单任务串行，人工审核门禁保持不变")
    await asyncio.sleep(12)
    while True:
        try:
            async with async_session_factory() as session:
                result = await process_media_quality_analyses_once(session, limit=1)
                dialogue_result = await process_dialogue_deliveries_once(session, limit=1)
                poster_result = await backfill_review_manifests_once(session, limit=1)
            has_activity = bool(
                result.get("completed")
                or result.get("failed")
                or dialogue_result.get("completed")
                or dialogue_result.get("failed")
                or poster_result.get("completed")
                or poster_result.get("failed")
            )
            if has_activity:
                logger.info(
                    "[media-quality] scanned={} completed={} failed={} skipped={} dialogue_completed={} dialogue_failed={} dialogue_waiting={} posters_completed={} posters_failed={}",
                    result.get("scanned", 0),
                    result.get("completed", 0),
                    result.get("failed", 0),
                    result.get("skipped", 0),
                    dialogue_result.get("completed", 0),
                    dialogue_result.get("failed", 0),
                    dialogue_result.get("waiting", 0),
                    poster_result.get("completed", 0),
                    poster_result.get("failed", 0),
                )
        except asyncio.CancelledError:
            logger.info("素材工作台 AI 成片质检已取消")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("素材工作台 AI 成片质检失败（继续）: {}", exc, exc_info=True)
            has_activity = False
        if has_activity:
            current_idle_interval = idle_interval
            await asyncio.sleep(active_interval)
        else:
            await asyncio.sleep(current_idle_interval)
            current_idle_interval = min(max_idle_interval, current_idle_interval * 2)


async def link_decline_operator_trigger_loop():
    """Consume durable tmall collector -> operator trigger tasks."""
    from app.execution.execution_service import execution_service

    await asyncio.sleep(5)
    idle_interval = 10
    max_idle_interval = 30
    current_idle_interval = idle_interval
    while True:
        try:
            processed = await execution_service.process_link_decline_operator_queue_once(
                worker_id="link-decline-operator-loop",
            )
            if processed:
                current_idle_interval = idle_interval
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(current_idle_interval)
                current_idle_interval = min(max_idle_interval, current_idle_interval * 2)
        except asyncio.CancelledError:
            logger.info("链接下滑 operator 触发任务已取消")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("链接下滑 operator 触发任务异常（继续）: {}", exc, exc_info=True)
            await asyncio.sleep(current_idle_interval)
            current_idle_interval = min(max_idle_interval, current_idle_interval * 2)


async def start_background_jobs(*, is_scheduler_worker: bool) -> BackgroundTasksState:
    from app.aiclaw.bridge_registry import bridge_registry
    from app.dingtalk.outbox import outbox
    from app.execution.scheduler import start_scheduler
    from app.todos.scheduler import callback_retry_loop, expire_old_todos_loop

    state = BackgroundTasksState(is_scheduler_worker=is_scheduler_worker)
    state.bridge_gc_task = bridge_registry.start_gc_loop()

    if settings.CODING_AGENT_ENABLED:
        from app.coding_agent.session_pool import coding_agent_pool

        state.coding_agent_gc_task = asyncio.create_task(coding_agent_pool.gc_loop())

    if is_scheduler_worker:
        state.outbox_task = asyncio.create_task(outbox.process_loop())
        await start_scheduler()
        state.todo_expire_task = asyncio.create_task(expire_old_todos_loop())
        state.todo_callback_retry_task = asyncio.create_task(callback_retry_loop())
        state.cost_alert_cleanup_task = asyncio.create_task(cost_alert_cleanup_loop())
        state.quota_alert_task = asyncio.create_task(quota_alert_check_loop())

        # Group A：tasktree 双写补偿 worker（扫 repair_queue 做幂等 replay）
        from app.tasktree.repair_worker import run_repair_loop

        state.tasktree_repair_task = asyncio.create_task(run_repair_loop())
        # [codex-2026-04-14] drift_scan 对账扫描：持久化游标跨重启追赶
        state.tasktree_drift_scan_task = asyncio.create_task(tasktree_drift_scan_loop())
        # 浏览器 cookie 心跳：定时调 verify_login 主动验证各平台登录态
        state.cookie_heartbeat_task = asyncio.create_task(cookie_heartbeat_loop())
        # 大厅 GPT ImageGen 等直接能力任务：服务端兜底推进，不依赖前端页面持续打开。
        state.direct_capability_task = asyncio.create_task(direct_capability_task_loop())
        # 训练结果补偿：只做回调丢失后的控制面对账，不替代训练网关主回调。
        state.training_collect_due_task = asyncio.create_task(training_collect_due_loop())
        # 大模型跨 Bridge 传输：持久化任务后台推进，避免单个 API 请求承载 35B relay。
        state.training_model_transfer_task = asyncio.create_task(training_model_transfer_loop())
        # 全量 4B 三轮微调：真实 GB10/Mac 链路后台推进，HTTP 请求只触发和观测。
        state.training_full_history_finetune_task = asyncio.create_task(training_full_history_finetune_loop())
        # H3 媒体队列：平台只做编排和断线补偿，实际生成仍由 Bridge/ComfyUI 执行。
        state.media_job_task = asyncio.create_task(media_job_task_loop())
        # 成片视觉分析独立串行，不能阻塞 Bridge 结果收集或替代人工审核。
        state.media_quality_task = asyncio.create_task(media_quality_task_loop())
        if settings.LEARNING_AUTO_FLOW_IN_WEB_WORKER_ENABLED:
            # 智能闭环自动流动需要和 Bridge registry 同进程，训练/部署预检才能看到在线节点。
            from app.learning.worker import learning_auto_flow_loop

            state.learning_auto_flow_task = asyncio.create_task(learning_auto_flow_loop())
            learning_auto_flow_text = " + learning auto-flow"
        else:
            logger.info(
                "智能闭环自动流动跳过 web worker 启动；由独立 worker 服务承接 "
                "(LEARNING_AUTO_FLOW_IN_WEB_WORKER_ENABLED=false)"
            )
            learning_auto_flow_text = ""
        # tmall collector 完成后的 operator 分析触发补偿 worker
        state.link_decline_operator_task = asyncio.create_task(link_decline_operator_trigger_loop())
        logger.info(
            "当前 Worker 已获取调度锁，启动 Scheduler + Outbox consumer + todo expire loop "
            "+ callback retry loop + cost alert cleanup + quota alert + tasktree repair worker "
            "+ tasktree drift_scan + cookie heartbeat + direct capability task loop "
            "+ training collect-due + full-history finetune + media job loop + media quality loop{} "
            "+ link decline operator trigger loop".format(learning_auto_flow_text)
        )
    else:
        logger.info("当前 Worker 未获取调度锁，跳过 Scheduler + Outbox consumer")

    return state


async def stop_background_jobs(state: BackgroundTasksState) -> None:
    from app.execution.scheduler import stop_scheduler

    # [B2] 关机前先等 dispatcher 在飞的 writer 协程刷清，
    # 避免 execution_runs 已 commit 但 task_nodes_light 未写 / 也未入 repair_queue。
    # 必须在 scheduler / repair_worker 被 cancel 之前，否则 repair_worker 也来不及消费 enqueue_repair 的兜底。
    try:
        from app.tasktree import dispatcher as tasktree_dispatcher

        await tasktree_dispatcher.wait_all_active(timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"tasktree dispatcher 刷清失败（继续关机流程）: {exc}")

    if state.is_scheduler_worker:
        stop_scheduler()
        for task in (
            state.outbox_task,
            state.cost_alert_cleanup_task,
            state.quota_alert_task,
            state.todo_expire_task,
            state.todo_callback_retry_task,
            state.tasktree_repair_task,
            state.tasktree_drift_scan_task,
            state.cookie_heartbeat_task,
            state.direct_capability_task,
            state.training_collect_due_task,
            state.training_model_transfer_task,
            state.training_full_history_finetune_task,
            state.learning_auto_flow_task,
            state.link_decline_operator_task,
            state.media_job_task,
            state.media_quality_task,
        ):
            if task:
                task.cancel()

    if state.bridge_gc_task:
        state.bridge_gc_task.cancel()

    if state.coding_agent_gc_task:
        state.coding_agent_gc_task.cancel()
        with suppress(BaseException):
            await state.coding_agent_gc_task
        try:
            from app.coding_agent.session_pool import coding_agent_pool

            await coding_agent_pool.close_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"关闭 coding_agent pool 失败: {exc}")
