"""任务树写入器。

写入路径均做了失败捕获：若失败则通过 `enqueue_repair`
把 payload 写入 `tasktree_repair_queue`，交给 `repair_worker` 异步补偿，
避免 fire-and-forget 写入拖垮主流程。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.metrics import TASKTREE_WRITER_FAILURE_TOTAL
from app.tasktree.models import TaskNodeLight, TaskTreeRepairQueue
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime

if TYPE_CHECKING:
    from app.execution.models import OpenClawInstance


def _serialize_datetime(value: datetime | None) -> str | None:
    return isoformat_bjt(value)


def _deserialize_datetime(value: str | None) -> datetime | None:
    return parse_bjt_datetime(value) if value else None


class TaskTreeWriter:
    """维护 task_nodes_light 的轻量写入入口。"""

    async def create_execution_node(
        self,
        db: AsyncSession,
        *,
        run_id: str,
        department_id: str,
        title: str,
        source_instance_id: str | None = None,
        node_type: str = "execution",
        status: str = "running",
        parent_id: UUID | str | None = None,
        root_id: UUID | str | None = None,
        sort_key: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        last_heartbeat_at: datetime | None = None,
    ) -> TaskNodeLight:
        """插入一个执行节点。

        root_id 默认优先使用父节点的 root_id，其次使用 run_id 的 UUID 表示，
        最后退化为新 UUID，保证表约束始终满足。
        """
        parent_uuid = self._coerce_uuid(parent_id)
        root_uuid = self._coerce_uuid(root_id)
        if root_uuid is None and parent_uuid is not None:
            parent = await db.get(TaskNodeLight, parent_uuid)
            if parent is None:
                raise ValueError(f"parent task node not found: {parent_uuid}")
            root_uuid = parent.root_id
        if root_uuid is None:
            root_uuid = self._coerce_uuid(run_id) or uuid4()

        node = TaskNodeLight(
            root_id=root_uuid,
            parent_id=parent_uuid,
            source_run_id=run_id,
            source_instance_id=source_instance_id,
            department_id=department_id,
            node_type=node_type,
            title=title,
            status=status,
            sort_key=sort_key or title,
            started_at=started_at or now_bjt(),
            finished_at=finished_at,
            last_heartbeat_at=last_heartbeat_at,
        )
        db.add(node)
        await db.flush()
        return node

    async def finish_execution_node(
        self,
        db: AsyncSession,
        *,
        run_id: str,
        status: str,
        finished_at: datetime | None = None,
    ) -> TaskNodeLight | None:
        """更新执行节点状态与结束时间。"""
        result = await db.execute(
            select(TaskNodeLight)
            .where(TaskNodeLight.source_run_id == run_id)
            .order_by(TaskNodeLight.created_at.asc(), TaskNodeLight.id.asc())
            .limit(1)
        )
        node = result.scalar_one_or_none()
        if node is None:
            return None

        node.status = status
        node.finished_at = finished_at or now_bjt()
        node.updated_at = node.finished_at
        await db.flush()
        return node

    async def sync_instance_heartbeat(
        self,
        db: AsyncSession,
        *,
        instance_id: str,
        last_heartbeat_at: datetime | None = None,
        department_id: str | None = None,
        record_history: bool = False,
        guard_against_stale: bool = False,
    ) -> int:
        """批量刷新同一实例下所有节点的心跳时间。"""
        return await self.sync_instance_heartbeats(
            db,
            {instance_id: last_heartbeat_at or now_bjt()},
            department_id=department_id,
            record_history=record_history,
            guard_against_stale=guard_against_stale,
        )

    async def sync_instance_heartbeats(
        self,
        db: AsyncSession,
        heartbeats: dict[str, datetime],
        *,
        department_id: str | None = None,
        record_history: bool = False,
        guard_against_stale: bool = False,
    ) -> int:
        """批量刷新多个实例的心跳时间。

        [B3] guard_against_stale=True 时附加 `last_heartbeat_at IS NULL OR
        last_heartbeat_at <= :payload_hb` 条件，防止 replay 老 payload 把已经
        更新到新时间戳的节点回退（repair_worker 幂等回放路径专用）。
        """
        from sqlalchemy import or_, text

        touched_at = now_bjt()
        updated = 0

        for instance_id, heartbeat_at in heartbeats.items():
            # Heartbeats for one instance can arrive concurrently from the
            # bridge, scheduler, and repair worker. Keep one writer per
            # instance per transaction; a newer heartbeat will arrive on the
            # next cycle if this duplicate loses the non-blocking lock.
            lock_result = await db.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"tasktree-heartbeat:{instance_id}"},
            )
            if not bool(lock_result.scalar()):
                continue
            latest_heartbeat_id = (
                select(TaskNodeLight.id)
                .where(TaskNodeLight.source_instance_id == instance_id)
                .where(TaskNodeLight.node_type == "heartbeat")
                .order_by(TaskNodeLight.last_heartbeat_at.desc().nullslast(), TaskNodeLight.created_at.desc())
                .limit(1)
            )
            if department_id:
                latest_heartbeat_id = latest_heartbeat_id.where(TaskNodeLight.department_id == department_id)
            stmt = (
                update(TaskNodeLight)
                .where(TaskNodeLight.source_instance_id == instance_id)
                .where(
                    or_(
                        TaskNodeLight.status.in_(("queued", "running", "stale")),
                        TaskNodeLight.id == latest_heartbeat_id.scalar_subquery(),
                    )
                )
                .where(
                    or_(
                        TaskNodeLight.last_heartbeat_at.is_(None),
                        TaskNodeLight.last_heartbeat_at < heartbeat_at,
                    )
                )
                .values(last_heartbeat_at=heartbeat_at, updated_at=touched_at)
            )
            if department_id:
                stmt = stmt.where(TaskNodeLight.department_id == department_id)
            result = await db.execute(stmt)
            updated += result.rowcount or 0
            if record_history:
                await self._record_heartbeat_snapshot(
                    db,
                    instance_id=instance_id,
                    heartbeat_at=heartbeat_at,
                    department_id=department_id or "unknown",
                )

        return updated

    async def sync_heartbeat_for_instance(
        self,
        db: AsyncSession,
        *,
        instance: "OpenClawInstance",
        last_heartbeat_at: datetime | None = None,
        record_history: bool = True,
    ) -> int:
        """统一封装：根据 instance 对象刷新心跳，消除 aiclaw 与 scheduler
        两处的重复 sync_instance_heartbeat 调用模板。

        调用方不再需要手动从 instance 上拆 id / department，避免
        "漏传 department 导致跨部门更新" 之类 bug。
        """
        heartbeat_at = last_heartbeat_at or now_bjt()
        return await self.sync_instance_heartbeat(
            db,
            instance_id=instance.id,
            last_heartbeat_at=heartbeat_at,
            department_id=instance.department,
            record_history=record_history,
        )

    async def _record_heartbeat_snapshot(
        self,
        db: AsyncSession,
        *,
        instance_id: str,
        heartbeat_at: datetime,
        department_id: str,
    ) -> None:
        recent = (
            await db.execute(
                select(TaskNodeLight)
                .where(TaskNodeLight.source_instance_id == instance_id)
                .where(TaskNodeLight.node_type == "heartbeat")
                .order_by(TaskNodeLight.last_heartbeat_at.desc().nullslast(), TaskNodeLight.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if recent and recent.last_heartbeat_at:
            gap = abs((heartbeat_at - recent.last_heartbeat_at).total_seconds())
            if gap < 55:
                return

        db.add(
            TaskNodeLight(
                root_id=uuid4(),
                source_instance_id=instance_id,
                department_id=department_id,
                node_type="heartbeat",
                title=f"heartbeat:{instance_id}",
                status="online",
                sort_key=isoformat_bjt(heartbeat_at),
                last_heartbeat_at=heartbeat_at,
                started_at=heartbeat_at,
                finished_at=heartbeat_at,
            )
        )
        await db.flush()

    # ── 补偿队列 ──────────────────────────────────────────────

    async def enqueue_repair(
        self,
        *,
        source_type: str,
        source_ref: str,
        operation: str,
        payload: dict[str, Any],
        last_error: str | None = None,
    ) -> bool:
        """把失败的 writer 操作写入 tasktree_repair_queue。

        使用新的 session，不共用上层 session（上层可能已经 rollback 或
        commit 失败，状态不可再用）。

        [codex-2026-04-14 复审] 返回值明确表示入队是否成功，调用方（dispatcher /
        drift_scan）需要据此判断是否走 dead-letter 指标 / 是否可以推进游标。
        之前全吞异常只记日志，dispatcher 永远抓不到真正的"双失败"。
        """
        from app.database import async_session_factory

        TASKTREE_WRITER_FAILURE_TOTAL.labels(source=source_type).inc()

        try:
            async with async_session_factory() as session:
                entry = TaskTreeRepairQueue(
                    source_type=source_type,
                    source_ref=source_ref,
                    operation=operation,
                    payload=payload,
                    last_error=(last_error or "")[:4000] or None,
                    retries=0,
                    status="pending",
                    next_attempt_at=now_bjt(),
                )
                session.add(entry)
                await session.commit()
            return True
        except IntegrityError as e:
            # [codex-2026-04-14 复审] 仅当是 UNIQUE violation（SQLSTATE 23505，
            # 即撞 ix_trq_active_unique）视为良性。FK / check / not-null 等其他
            # 完整性错误都应返回 False，让调用方走 dead-letter。
            orig = getattr(e, "orig", None)
            sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
            if sqlstate == "23505":
                logger.debug(
                    "tasktree repair_queue 已存在活跃任务，跳过 source={} ref={} op={}",
                    source_type,
                    source_ref,
                    operation,
                )
                return True
            logger.error(
                "tasktree repair_queue IntegrityError（非唯一约束）source={} ref={} sqlstate={}: {}",
                source_type,
                source_ref,
                sqlstate,
                e,
                exc_info=True,
            )
            return False
        except Exception as e:
            # 兜底：连 repair_queue 都入不了，返回 False 让调用方走 dead-letter 通道
            logger.error(
                "tasktree repair_queue 入队失败 source={} ref={}: {}",
                source_type,
                source_ref,
                e,
                exc_info=True,
            )
            return False

    # ── 供 dispatcher / repair_worker 调用的幂等重写入口 ──────

    async def replay_create_execution_node(
        self,
        db: AsyncSession,
        payload: dict[str, Any],
    ) -> None:
        """根据 payload 重新创建执行节点。幂等：若已存在同 run_id 的节点则跳过。"""
        run_id = payload.get("run_id")
        if not run_id:
            return
        existing = await db.execute(
            select(TaskNodeLight).where(TaskNodeLight.source_run_id == run_id).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            return
        await self.create_execution_node(
            db,
            run_id=run_id,
            department_id=payload.get("department_id") or "unknown",
            title=payload.get("title") or run_id,
            source_instance_id=payload.get("source_instance_id"),
            node_type=payload.get("node_type") or "execution",
            status=payload.get("status") or "running",
            parent_id=payload.get("parent_id"),
            root_id=payload.get("root_id"),
            sort_key=payload.get("sort_key"),
            started_at=_deserialize_datetime(payload.get("started_at")),
            finished_at=_deserialize_datetime(payload.get("finished_at")),
            last_heartbeat_at=_deserialize_datetime(payload.get("last_heartbeat_at")),
        )

    async def replay_finish_execution_node(
        self,
        db: AsyncSession,
        payload: dict[str, Any],
    ) -> None:
        """根据 payload 重新标记执行节点完成。

        [codex-2026-04-14 复审] 节点不存在时**必须抛错**，让 repair_worker 走
        重试路径——大概率是 create_execution_node 的 replay 还在 backoff 或
        drift_scan 尚未入队 create。之前静默返回会让 finish repair 被标记
        `done`，create 侧补上时已经没有 finish 触发点，节点永远卡在 running。
        """
        run_id = payload.get("run_id")
        status = payload.get("status") or "completed"
        if not run_id:
            return
        node = await self.finish_execution_node(
            db,
            run_id=run_id,
            status=status,
            finished_at=_deserialize_datetime(payload.get("finished_at")),
        )
        if node is None:
            raise RuntimeError(
                f"replay_finish_execution_node: node for run_id={run_id} not found; "
                "racing with create_execution_node replay — let repair_worker retry"
            )

    async def replay_sync_heartbeat(
        self,
        db: AsyncSession,
        payload: dict[str, Any],
    ) -> None:
        """根据 payload 重新刷新实例心跳。

        [B3] 幂等防倒退：老的 repair_queue payload 不得把节点的 last_heartbeat_at
        回退到比当前更旧的时间（重复 replay 或乱序消费时都会保持最新）。
        """
        instance_id = payload.get("instance_id")
        if not instance_id:
            return
        heartbeat_at = _deserialize_datetime(payload.get("last_heartbeat_at")) or now_bjt()
        await self.sync_instance_heartbeat(
            db,
            instance_id=instance_id,
            last_heartbeat_at=heartbeat_at,
            department_id=payload.get("department_id"),
            record_history=bool(payload.get("record_history")),
            guard_against_stale=True,
        )

    @staticmethod
    def _coerce_uuid(value: UUID | str | None) -> UUID | None:
        if value is None:
            return None
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None


def serialize_datetime(value: datetime | None) -> str | None:
    """供 dispatcher 构造 payload 时序列化 datetime。"""
    return _serialize_datetime(value)


writer = TaskTreeWriter()
