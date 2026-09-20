"""任务树写入 fire-and-forget 调度器。

Group A P0-2：把 ExecutionService / aiclaw bridge 里大量
`try: writer.xxx(...) except: pass` 的直接调用，统一收敛到
`schedule_writer(coro_factory, source_type=..., source_ref=..., payload=...)`。

调度器职责：
- 以 asyncio.create_task 立即启动协程（调用方无需 await）；
- 记录 task 到模块级 set，完成后移除，防止 GC 在任务完成前回收；
- 协程内部任何异常 → 写入 `tasktree_repair_queue`，由 repair_worker 重试；
- 不把异常抛给调用方（保留最小耦合）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable

from loguru import logger

from app.common.metrics import TASKTREE_DISPATCHER_DEAD_LETTER_TOTAL
from app.tasktree.writer import writer

# 保留引用防 GC：asyncio 文档明确要求在 create_task 返回值生命期内持有引用，
# 否则 task 可能被垃圾回收（Python 3.11+ 有 warning 但不保证运行）。
_active_tasks: set[asyncio.Task[Any]] = set()


async def schedule_writer(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    source_type: str,
    source_ref: str,
    operation: str,
    payload: dict[str, Any],
) -> asyncio.Task[Any]:
    """fire-and-forget 调度 writer 协程。

    Args:
        coro_factory: 返回待执行协程的工厂函数（每次调用都产生一个新协程）。
            用工厂而不是直接传协程，便于失败时仅入队 payload 不重放协程。
        source_type: 失败补偿来源（如 "execution_start"）。
        source_ref: 业务记录引用（如 run_id / instance_id）。
        operation: writer 方法名（如 "create_execution_node"），repair_worker
            据此决定 replay 路径。
        payload: 幂等 replay 所需参数（datetime 请先 isoformat 序列化）。

    Returns:
        已调度的 asyncio.Task。调用方可选择保留引用查状态，或直接丢弃。
    """

    async def _runner() -> None:
        try:
            await coro_factory()
        except Exception as e:
            logger.warning(
                "tasktree writer 调度失败 → 入 repair_queue "
                "(source={} ref={} op={}): {}",
                source_type,
                source_ref,
                operation,
                e,
                exc_info=True,
            )
            # [codex-2026-04-14] enqueue_repair 返回 bool 明示入队结果：
            # True = 已入队（含 IntegrityError 撞唯一约束的良性情况）
            # False = 数据真的丢失 → 走 dead-letter 指标 + logger.error
            enqueued_ok = False
            enqueue_exc: Exception | None = None
            try:
                enqueued_ok = await writer.enqueue_repair(
                    source_type=source_type,
                    source_ref=source_ref,
                    operation=operation,
                    payload=payload,
                    last_error=f"{type(e).__name__}: {e}",
                )
            except Exception as ee:  # noqa: BLE001
                enqueue_exc = ee

            if not enqueued_ok:
                try:
                    TASKTREE_DISPATCHER_DEAD_LETTER_TOTAL.labels(source=source_type).inc()
                except Exception as metric_err:
                    logger.debug("tasktree dispatcher 死信指标上报失败: {}", metric_err)
                logger.error(
                    "tasktree dispatcher 死信：writer + enqueue_repair 都失败 "
                    "(source={} ref={} op={}): writer_err={} enqueue_err={}",
                    source_type,
                    source_ref,
                    operation,
                    e,
                    enqueue_exc,
                    exc_info=True,
                )

    task = asyncio.create_task(_runner(), name=f"tasktree_writer:{operation}:{source_ref}")
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
    return task


def active_task_count() -> int:
    """暴露给监控/测试用的当前在飞 task 数。"""
    return len(_active_tasks)


async def wait_all_active(timeout: float | None = 5.0) -> None:
    """测试/关闭时刷清在飞任务。生产关闭路径也应该调它，避免任务被强杀。"""
    if not _active_tasks:
        return
    pending = list(_active_tasks)
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "wait_all_active 超时 timeout={}s 未完成={}",
            timeout,
            len(_active_tasks),
        )


def make_heartbeat_coro(
    *,
    instance_id: str,
    last_heartbeat_at: datetime,
    department_id: str | None,
    record_history: bool = True,
) -> Callable[[], Awaitable[None]]:
    """[m5] 共用心跳同步协程工厂：bridge_router 和 execution_service 之前各有一份
    重复的 `async def _sync_heartbeat` 闭包，集中到这里避免漂移。

    使用方式：
        await tasktree_dispatcher.schedule_writer(
            make_heartbeat_coro(instance_id=..., last_heartbeat_at=..., department_id=...),
            source_type="heartbeat",
            source_ref=instance_id,
            operation="sync_heartbeat",
            payload={...},
        )
    """
    async def _run() -> None:
        from app.database import async_session_factory
        from app.tasktree.writer import writer as tasktree_writer

        async with async_session_factory() as session:
            await tasktree_writer.sync_instance_heartbeat(
                session,
                instance_id=instance_id,
                last_heartbeat_at=last_heartbeat_at,
                department_id=department_id,
                record_history=record_history,
            )
            await session.commit()

    return _run
