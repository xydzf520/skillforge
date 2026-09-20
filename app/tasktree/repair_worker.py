"""任务树补偿 worker：扫 tasktree_repair_queue 并按指数退避重试。

算法（[codex-2026-04-14] 原子 claim + replay + mark_done）：
- 每 30s 打开一个 session 开 tx
- `SELECT ... WHERE status IN (pending, retrying) AND next_attempt_at <= now
  ORDER BY next_attempt_at LIMIT N FOR UPDATE SKIP LOCKED`
  原子抢占 N 条；skip_locked 保证多 worker 不会重复消费同一行
- 每条用 savepoint 包裹：
  - 成功路径：`replay_*` 与 `UPDATE status='done'` 在同一 session，同一 commit
  - 失败路径：rollback 当前 savepoint → 新 savepoint 写 retries / last_error / 状态迁移
- 最后一次 commit 释放行锁

幂等由两层兜底：
- `writer.replay_*` 自己做存在性检查 / guard_against_stale
- migration 050 / 052 的 partial UNIQUE INDEX（source_run_id 唯一；活跃 repair 唯一）
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.metrics import (
    TASKTREE_REPAIR_LAG_SECONDS,
    TASKTREE_REPAIR_QUEUE_SIZE,
)
from app.tasktree.models import TaskTreeRepairQueue
from app.tasktree.writer import writer
from app.common.time_utils import isoformat_bjt, now_bjt

SCAN_INTERVAL_SECONDS = 30
BATCH_LIMIT = 50
BASE_BACKOFF_SECONDS = 10
MAX_BACKOFF_SECONDS = 600
# [m2] tasktree_repair_queue.status='done' 保留天数；超过则清理，避免表无限膨胀
REPAIR_DONE_RETENTION_DAYS = 90
# [m2] 每 N 轮扫描后跑一次老记录清理；30s * 120 = 60 分钟
REPAIR_CLEANUP_EVERY_N_LOOPS = 120


def _sf():
    """延迟获取 async_session_factory，避免测试中 fixture 替换后的引用失效。"""
    from app.database import async_session_factory

    return async_session_factory


async def _dispatch_in_session(session: AsyncSession, entry: TaskTreeRepairQueue) -> None:
    """[codex-2026-04-14] 在给定 session 里执行 replay。不 commit，由调用方同事务 commit。

    这是生产主路径：replay 的 INSERT/UPDATE 与 repair 行的 status 更新在同一 tx，
    atomically 一起提交；中间崩溃会整体 rollback，不会出现 replay 已落但 status
    还是 pending 的裂脑状态。

    幂等仍由 writer.replay_* 的 WHERE 存在性检查兜底 + DB unique 约束保护。
    """
    if entry.operation == "create_execution_node":
        await writer.replay_create_execution_node(session, entry.payload or {})
    elif entry.operation == "finish_execution_node":
        await writer.replay_finish_execution_node(session, entry.payload or {})
    elif entry.operation == "sync_heartbeat":
        await writer.replay_sync_heartbeat(session, entry.payload or {})
    else:
        raise ValueError(f"unknown repair operation: {entry.operation}")


async def _dispatch(entry: TaskTreeRepairQueue) -> None:
    """[legacy] 兼容老测试 / 外部手工调用：自己开 session + commit。

    生产 _scan_once 主路径 **不再** 走这里（不是同 tx），只保留做 compat。
    """
    async with _sf()() as session:
        try:
            await _dispatch_in_session(session, entry)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _backoff(retries: int) -> timedelta:
    seconds = min(BASE_BACKOFF_SECONDS * (2 ** retries), MAX_BACKOFF_SECONDS)
    return timedelta(seconds=seconds)


async def _update_metrics() -> None:
    """每轮扫描后更新 repair_queue 相关 Gauge 指标。"""
    try:
        async with _sf()() as session:
            size = await session.scalar(
                select(func.count()).select_from(TaskTreeRepairQueue).where(
                    TaskTreeRepairQueue.status.in_(("pending", "retrying"))
                )
            )
            TASKTREE_REPAIR_QUEUE_SIZE.set(int(size or 0))

            oldest = await session.scalar(
                select(func.min(TaskTreeRepairQueue.created_at)).where(
                    TaskTreeRepairQueue.status.in_(("pending", "retrying"))
                )
            )
            if oldest:
                lag = (now_bjt() - oldest).total_seconds()
                TASKTREE_REPAIR_LAG_SECONDS.set(max(lag, 0.0))
            else:
                TASKTREE_REPAIR_LAG_SECONDS.set(0.0)
    except Exception as e:
        logger.warning("repair_queue 指标更新失败: {}", e, exc_info=True)


async def _handle_failure(
    session: AsyncSession,
    entry_id,
    operation: str,
    source_type: str,
    source_ref: str,
    exc: Exception,
) -> None:
    """[codex-2026-04-14] 失败分支：在 fresh savepoint 中写 retries / status 迁移。

    调用方已经 rollback 了 replay 的 savepoint；这里再开一个 savepoint 专门
    写状态机。若写状态机本身也失败，抛出让上层 savepoint rollback；调用方会
    把异常吞掉并让下次 scan 再处理（因为此时 row lock 已随 session.commit 释放）。
    """
    fresh = await session.get(TaskTreeRepairQueue, entry_id)
    if fresh is None:
        return
    fresh.retries = (fresh.retries or 0) + 1
    fresh.last_error = f"{type(exc).__name__}: {exc}"[:4000]
    fresh.updated_at = now_bjt()
    if fresh.retries >= (fresh.max_retries or 5):
        fresh.status = "failed"
        logger.error(
            "tasktree repair 彻底失败 id={} op={} source={}/{} retries={}: {}",
            fresh.id,
            operation,
            source_type,
            source_ref,
            fresh.retries,
            exc,
        )
    else:
        fresh.status = "retrying"
        fresh.next_attempt_at = now_bjt() + _backoff(fresh.retries)
        logger.warning(
            "tasktree repair 重试调度 id={} op={} retries={} next_at={}: {}",
            fresh.id,
            operation,
            fresh.retries,
            isoformat_bjt(fresh.next_attempt_at),
            exc,
        )
    await session.flush()


async def _scan_once() -> int:
    """[codex-2026-04-14] 原子 claim + replay + mark_done。返回处理条数。

    FOR UPDATE SKIP LOCKED 保证双 worker / scan 重入不会重复消费同一行。
    replay 与 status 更新在同一 session、同一事务内：
    - 成功：savepoint commit（`done`）
    - 失败：savepoint rollback，fresh savepoint 写 `retrying|failed`
    - 整批最后一次 session.commit 释放行锁

    兼容测试：用例如果 patch `_dispatch_in_session` 抛异常，就会走失败分支；
    旧测试 patch `_dispatch`（legacy 单独 session 路径）不会被本函数调用到，
    需要迁移到新 hook。
    """
    now = now_bjt()
    processed = 0

    async with _sf()() as session:
        # [codex-2026-04-14] 原子 claim：FOR UPDATE SKIP LOCKED 抢占一批
        claim_stmt = (
            select(TaskTreeRepairQueue)
            .where(TaskTreeRepairQueue.status.in_(("pending", "retrying")))
            .where(TaskTreeRepairQueue.next_attempt_at <= now)
            .order_by(TaskTreeRepairQueue.next_attempt_at.asc())
            .limit(BATCH_LIMIT)
            .with_for_update(skip_locked=True)
        )
        entries = list((await session.execute(claim_stmt)).scalars().all())

        if not entries:
            await session.commit()
            await _update_metrics()
            return 0

        for entry in entries:
            entry_id = entry.id
            operation = entry.operation
            source_type = entry.source_type
            source_ref = entry.source_ref
            replay_exc: Exception | None = None

            # ── 成功路径：replay + mark done 在同一 savepoint ──
            sp = await session.begin_nested()
            try:
                await _dispatch_in_session(session, entry)
                entry.status = "done"
                entry.last_error = None
                entry.updated_at = now_bjt()
                await session.flush()
                await sp.commit()
            except Exception as e:
                replay_exc = e
                # savepoint 里写的 replay / status 更新全部 rollback
                await sp.rollback()

            if replay_exc is None:
                processed += 1
                continue

            # ── 失败路径：fresh savepoint 写 retries / status 迁移 ──
            sp2 = await session.begin_nested()
            try:
                await _handle_failure(
                    session,
                    entry_id,
                    operation,
                    source_type,
                    source_ref,
                    replay_exc,
                )
                await sp2.commit()
            except Exception as e2:
                # 状态机写失败：放弃这条，等下次扫描（row lock 随 session.commit 释放）
                await sp2.rollback()
                logger.error(
                    "tasktree repair 状态机写入失败 id={} op={}: {}",
                    entry_id,
                    operation,
                    e2,
                    exc_info=True,
                )
            processed += 1

        await session.commit()

    await _update_metrics()
    return processed


async def _cleanup_done_items(older_than_days: int = REPAIR_DONE_RETENTION_DAYS) -> int:
    """[m2] 清理 status='done' 且 updated_at 超过保留天数的记录。

    不独立起 job，由 repair_loop 按 REPAIR_CLEANUP_EVERY_N_LOOPS 周期触发。
    返回删除条数（便于测试断言）。
    """
    from sqlalchemy import delete as sa_delete

    cutoff = now_bjt() - timedelta(days=max(1, int(older_than_days)))
    async with _sf()() as session:
        result = await session.execute(
            sa_delete(TaskTreeRepairQueue)
            .where(TaskTreeRepairQueue.status == "done")
            .where(TaskTreeRepairQueue.updated_at < cutoff)
        )
        deleted = int(result.rowcount or 0)
        await session.commit()
    if deleted:
        logger.info(
            "tasktree repair_queue 清理完成：删除 {} 条 done 记录 (older_than_days={})",
            deleted,
            older_than_days,
        )
    return deleted


async def run_repair_loop() -> None:
    """永久循环。应用启动时 asyncio.create_task(run_repair_loop())。"""
    logger.info("tasktree repair_worker 启动，每 {}s 扫描一次", SCAN_INTERVAL_SECONDS)
    # 启动后先等一小段时间，避免和其他启动任务争抢资源
    await asyncio.sleep(5)
    loop_counter = 0
    while True:
        try:
            processed = await _scan_once()
            if processed:
                logger.info("tasktree repair_worker 处理 {} 条补偿记录", processed)
            loop_counter += 1
            # [m2] 每 N 轮扫描后做一次 done 记录清理，避免 repair_queue 无限膨胀
            if loop_counter % REPAIR_CLEANUP_EVERY_N_LOOPS == 0:
                try:
                    await _cleanup_done_items()
                except Exception as e:
                    logger.warning(
                        "tasktree repair_queue 清理异常（继续）: {}", e, exc_info=True
                    )
        except asyncio.CancelledError:
            logger.info("tasktree repair_worker 收到 cancel，退出")
            raise
        except Exception as e:
            logger.error("tasktree repair_worker 主循环异常（继续）: {}", e, exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)
