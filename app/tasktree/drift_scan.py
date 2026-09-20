"""任务树对账扫描。

查最近 N 小时的 execution_runs，对照 task_nodes_light 找出缺口：
- execution_runs 存在但 task_nodes_light 里没有对应 source_run_id
→ 视为写入丢失，入 tasktree_repair_queue（source_type=drift_scan，
   operation=create_execution_node），由 repair_worker 补写。

[codex-2026-04-14] 持久化游标 + 追赶模式：
- 持久化 drift_scan_cursor.last_scanned_at，重启/停摆后能继续追赶老缺口
- 每次扫描 since = min(cursor.last_scanned_at - SAFETY_OVERLAP, now - window_hours)
  保底至少覆盖 window_hours，同时允许追赶比窗口更早的积压
- 非 done 的 repair 记录中只跳过 pending/retrying；failed 不再永久排除，
  允许运维修完底层 bug 后由下一次扫描自动再入队

可单独跑：python -m app.tasktree.drift_scan [--window-hours 24]
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import and_, exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.common.metrics import TASKTREE_DRIFT_COUNT
from app.common.time_utils import isoformat_bjt
from app.execution.models import DecisionLog, ExecutionRun
from app.skills.core.models import Skill
from app.tasktree.models import (
    DriftScanCursor,
    TaskNodeLight,
    TaskTreeRepairQueue,
)
from app.tasktree.writer import serialize_datetime, writer
from app.common.time_utils import now_bjt

# 覆盖重叠时间（防止 late-arriving 行在 cursor 边界处被错过）
CURSOR_SAFETY_OVERLAP = timedelta(minutes=15)
# [codex-2026-04-14] failed 记录冷却期：最近 N 小时内失败过的不再重复入队，
# 超过冷却期才允许 drift_scan 自动回补，避免尚未修复的死信反复堆表
FAILED_RETRY_COOLDOWN = timedelta(hours=24)
# [codex-2026-04-14 复审 x3] 单次扫描强行回补的 stale_expired 上限。
# 避免 stale 集合无限增长后把 IN (...) 撑爆 PG bind 参数上限（约 32K），
# 剩下的会在下一轮扫描继续处理。按 updated_at 最老的优先捞回。
MAX_STALE_EXPIRED_PER_SCAN = 500


def _sf():
    """延迟获取 async_session_factory，避免测试中 fixture 替换后的引用失效。"""
    from app.database import async_session_factory

    return async_session_factory


async def _pending_source_refs() -> tuple[set[str], set[str], set[str]]:
    """drift_scan source_ref 三元分类：`(active, cooldown_blocked, stale_expired)`。

    [codex-2026-04-14 复审 x2]：
    - `active`：pending / retrying — 仍在处理中，跳过，不影响 cursor
    - `cooldown_blocked`：failed 且在冷却期内 — 跳过 + 阻塞 cursor（过了 cooldown
      它的 run 仍可能跌出 window_hours，必须保留窗口）
    - `stale_expired`：failed 且冷却期已过 — **必须被重新扫描**。即使 run.started_at
      已超出 window_hours，也要作为"显式 run_id 白名单"强制纳入扫描，防止
      cooldown 期间过渡后 run 永久跌出扫描面（第二轮 codex 复审新发现）
    done 不纳入：节点后来又丢了的二次缺口由正常 window 扫描覆盖。
    """
    cutoff = now_bjt() - FAILED_RETRY_COOLDOWN
    async with _sf()() as session:
        active_stmt = (
            select(TaskTreeRepairQueue.source_ref)
            .where(TaskTreeRepairQueue.source_type == "drift_scan")
            .where(TaskTreeRepairQueue.status.in_(("pending", "retrying")))
        )
        active = set((await session.execute(active_stmt)).scalars().all())

        blocked_stmt = (
            select(TaskTreeRepairQueue.source_ref)
            .where(TaskTreeRepairQueue.source_type == "drift_scan")
            .where(TaskTreeRepairQueue.status == "failed")
            .where(TaskTreeRepairQueue.updated_at >= cutoff)
        )
        cooldown_blocked = set((await session.execute(blocked_stmt)).scalars().all())

        # [codex-2026-04-14 复审 x3] stale_expired 有上限且按最老优先，避免
        # 集合膨胀撑爆 IN (...) 的 bind 参数
        stale_stmt = (
            select(TaskTreeRepairQueue.source_ref)
            .where(TaskTreeRepairQueue.source_type == "drift_scan")
            .where(TaskTreeRepairQueue.status == "failed")
            .where(TaskTreeRepairQueue.updated_at < cutoff)
            .order_by(TaskTreeRepairQueue.updated_at.asc())
            .limit(MAX_STALE_EXPIRED_PER_SCAN)
        )
        stale_expired = set((await session.execute(stale_stmt)).scalars().all())
    return active, cooldown_blocked, stale_expired


async def _cleanup_resolved_failed() -> int:
    """[codex-2026-04-14 复审 x4] 清理 drift_scan 中「节点已补回」的 failed 记录。

    只删对应 source_ref 在 task_nodes_light 里已经有 execution/run 节点的
    failed 记录（drift 已经被其它路径补上了，但 repair 行没及时 mark done）。
    **不按时间删**：老的、节点仍缺失的 failed 必须保留，否则老 run 的
    `force_include_ids` 凭据丢失，永远无法再被 drift_scan 重新扫到。
    """
    from sqlalchemy import delete as sa_delete

    try:
        async with _sf()() as session:
            # 子查询：已有 light 节点的 source_run_id
            resolved = (
                select(TaskNodeLight.source_run_id)
                .where(TaskNodeLight.source_run_id.is_not(None))
                .where(TaskNodeLight.node_type.in_(["execution", "run"]))
                .scalar_subquery()
            )
            result = await session.execute(
                sa_delete(TaskTreeRepairQueue)
                .where(TaskTreeRepairQueue.source_type == "drift_scan")
                .where(TaskTreeRepairQueue.status == "failed")
                .where(TaskTreeRepairQueue.source_ref.in_(resolved))
            )
            deleted = int(result.rowcount or 0)
            await session.commit()
        return deleted
    except Exception as exc:  # noqa: BLE001
        logger.warning("drift_scan 清理已解决 failed 异常（继续）: {}", exc, exc_info=True)
        return 0


async def _load_cursor() -> datetime | None:
    """读取 drift_scan_cursor.last_scanned_at；若表不存在（测试未建表）返回 None。"""
    try:
        async with _sf()() as session:
            row = await session.get(DriftScanCursor, 1)
            return row.last_scanned_at if row else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("drift_scan_cursor 读取失败（首次部署或测试环境）: {}", exc)
        return None


async def _advance_cursor(new_ts: datetime) -> None:
    """原子地把 last_scanned_at 推进到 new_ts；单调，不会被更早的扫描回拨。

    用 `ON CONFLICT DO UPDATE SET last_scanned_at = GREATEST(existing, new_ts)`
    保证并发扫描/慢实例不会把 cursor 拨回老时间（codex 二次评审漏洞）。
    表不存在时静默跳过。
    """
    from sqlalchemy import func

    try:
        async with _sf()() as session:
            now = now_bjt()
            stmt = pg_insert(DriftScanCursor).values(
                id=1,
                last_scanned_at=new_ts,
                last_run_at=now,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    # [codex-2026-04-14 复审] 单调推进：取 max(existing, new_ts)
                    "last_scanned_at": func.greatest(
                        DriftScanCursor.last_scanned_at, stmt.excluded.last_scanned_at
                    ),
                    "last_run_at": now,
                    "updated_at": now,
                },
            )
            await session.execute(stmt)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("drift_scan_cursor 推进失败（继续）: {}", exc, exc_info=True)


async def run_drift_scan(window_hours: int = 24) -> dict:
    """扫描 execution_runs 找缺失的 light 节点。

    扫描窗口 = [since, now]，since = min(cursor.last_scanned_at - overlap,
    now - window_hours)。

    Returns: {"scanned": N, "drift": M, "enqueued": K, "since": iso}
    """
    now = now_bjt()
    window_floor = now - timedelta(hours=window_hours)
    cursor_ts = await _load_cursor()
    if cursor_ts is None:
        since = window_floor
    else:
        # 取更早的那个时间点：保证至少覆盖窗口，同时能追赶 cursor 之前的积压
        since = min(cursor_ts - CURSOR_SAFETY_OVERLAP, window_floor)

    # [codex-2026-04-14 复审 x4] 清理 drift_scan failed 中「节点已补回」的僵尸记录。
    # 不按时间删，删了等于把老 run 的 force_include 凭据丢掉 → 永久脱离扫描面。
    # 只删 source_ref 对应 light 节点已存在的 failed（drift 其实已经解决）。
    resolved_deleted = await _cleanup_resolved_failed()
    if resolved_deleted:
        logger.info("drift_scan 清理已解决的 failed 记录 {} 条", resolved_deleted)

    active, cooldown_blocked, stale_expired = await _pending_source_refs()
    # active 在下面用来直接跳过；cooldown_blocked + stale_expired 要强制进入扫描面
    force_include_ids = list(cooldown_blocked | stale_expired)

    async with _sf()() as session:
        exists_light = exists().where(
            and_(
                TaskNodeLight.source_run_id == ExecutionRun.id,
                TaskNodeLight.node_type == "execution",
            )
        )
        # [m10] 一个 run 可能挂多条 DecisionLog，原查询会展开多行导致同一 run 反复入队；
        # 用 DISTINCT ON (er.id) 每个 run 只保留一行（任意 skill_id，补写只用 department/title）
        # [codex-2026-04-14 复审 x2] 把 cooldown_blocked + stale_expired 无条件 OR 进来，
        # 防止老 run 跌出 window_hours 后永远不再进入扫描面
        started_at_or_force = ExecutionRun.started_at >= since
        if force_include_ids:
            from sqlalchemy import or_

            started_at_or_force = or_(
                ExecutionRun.started_at >= since,
                ExecutionRun.id.in_(force_include_ids),
            )
        stmt = (
            select(
                ExecutionRun.id,
                ExecutionRun.status,
                ExecutionRun.started_at,
                ExecutionRun.completed_at,
                DecisionLog.skill_id,
                Skill.department,
                Skill.name,
            )
            .select_from(ExecutionRun)
            .join(DecisionLog, DecisionLog.run_id == ExecutionRun.id, isouter=True)
            .join(Skill, Skill.id == DecisionLog.skill_id, isouter=True)
            .where(started_at_or_force)
            .where(~exists_light)
            .distinct(ExecutionRun.id)
            .order_by(ExecutionRun.id.asc(), ExecutionRun.started_at.asc())
        )
        rows = (await session.execute(stmt)).all()

    scanned_total = 0
    drift_total = 0
    enqueued = 0
    enqueue_failures = 0
    cooldown_hits = 0  # 本次扫描命中"冷却期 failed"的条数

    for row in rows:
        scanned_total += 1
        run_id = row.id
        if not run_id:
            continue
        drift_total += 1
        run_id_str = str(run_id)
        # 优先级：cooldown_blocked > active > stale_expired（允许重新入队）
        if run_id_str in cooldown_blocked:
            cooldown_hits += 1
            continue
        if run_id_str in active:
            # 已有 pending/retrying，不重复入队（ix_trq_active_unique 也兜着）
            continue
        # 尽力恢复当时的 department / title；实在缺失就退化成 "unknown"
        department = row.department or "unknown"
        title = row.name or row.skill_id or run_id

        # [codex-2026-04-14 复审] 必须检查 enqueue_repair 返回值：
        # False 表示入队真的失败（DB 出问题），不能算 enqueued 也不能推进 cursor
        ok = await writer.enqueue_repair(
            source_type="drift_scan",
            source_ref=str(run_id),
            operation="create_execution_node",
            payload={
                "run_id": run_id,
                "department_id": department,
                "title": title,
                "node_type": "execution",
                "status": row.status or "running",
                "started_at": serialize_datetime(row.started_at),
                "finished_at": serialize_datetime(row.completed_at),
            },
            last_error="drift detected by drift_scan",
        )
        if ok:
            enqueued += 1
        else:
            enqueue_failures += 1

    TASKTREE_DRIFT_COUNT.set(drift_total)
    # [codex-2026-04-14 复审 High] 只有"全部入队成功 且 本轮没有冷却期命中"才推进 cursor。
    # 否则必须保留 cursor，让下次扫描覆盖相同窗口；特别是 cooldown_hits 场景：
    # 如果 cursor 前移，该 run 可能在冷却期结束前就跌出 window_hours，永久丢失。
    can_advance = (enqueue_failures == 0) and (cooldown_hits == 0)
    if can_advance:
        await _advance_cursor(now)
    else:
        logger.warning(
            "drift_scan cursor 不推进：enqueue_failures={} cooldown_hits={}（保留窗口以便下次重试）",
            enqueue_failures,
            cooldown_hits,
        )
    logger.info(
        "drift_scan 完成 since={} window={}h scanned={} drift={} enqueued={} "
        "failures={} cooldown_hits={}",
        isoformat_bjt(since),
        window_hours,
        scanned_total,
        drift_total,
        enqueued,
        enqueue_failures,
        cooldown_hits,
    )
    return {
        "scanned": scanned_total,
        "drift": drift_total,
        "enqueued": enqueued,
        "enqueue_failures": enqueue_failures,
        "cooldown_hits": cooldown_hits,
        "since": isoformat_bjt(since),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="task_nodes_light 对账扫描")
    parser.add_argument("--window-hours", type=int, default=24, help="扫描回溯窗口（小时）")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    result = await run_drift_scan(window_hours=args.window_hours)
    print(f"drift_scan 结果: {result}")


if __name__ == "__main__":
    asyncio.run(_main())
