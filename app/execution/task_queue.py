"""PostgreSQL-backed 执行任务队列。

使用 SELECT ... FOR UPDATE SKIP LOCKED 实现无锁任务领取，
支持优先级、超时、重试和死信处理。
"""

from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select, update, func, text

from app.execution.queue_models import ExecutionQueueTask
from app.common.time_utils import isoformat_bjt, now_bjt


def _sf():
    """延迟获取 async_session_factory，避免测试中 engine 替换问题"""
    from app.database import async_session_factory
    return async_session_factory


class TaskQueue:
    """基于 PostgreSQL 的轻量执行任务队列。

    核心设计：
    - 入队：INSERT 一行 pending 记录
    - 领取：SELECT ... FOR UPDATE SKIP LOCKED（原子性，多 worker 安全）
    - 完成/失败：UPDATE status + result/error
    - 超时回收：定期扫描 claimed 超时的任务，重置为 pending 或标记 failed
    """

    async def enqueue(
        self,
        task_type: str,
        payload: dict,
        *,
        skill_id: str | None = None,
        priority: int = 5,
        queue_name: str = "default",
        timeout_seconds: int = 300,
        max_retries: int = 2,
        created_by: str = "system",
    ) -> int:
        """入队一个任务，返回 task_id。"""
        task = ExecutionQueueTask(
            task_type=task_type,
            skill_id=skill_id,
            payload=payload,
            priority=priority,
            status="pending",
            queue_name=queue_name,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            created_by=created_by,
            created_at=now_bjt(),
        )
        async with _sf()() as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)
            task_id = task.id

        logger.info("任务入队: id={} type={} queue={} priority={}",
                     task_id, task_type, queue_name, priority)
        return task_id

    async def claim(
        self,
        worker_id: str,
        queue_name: str = "default",
        task_types: list[str] | None = None,
    ) -> dict | None:
        """领取一个任务（原子性，SKIP LOCKED）。返回 None 如果无可用任务。

        使用 FOR UPDATE SKIP LOCKED 保证多 worker 并发安全：
        - 每个 worker 只能领取未被锁定的任务
        - 跳过已被其他事务锁定的行，不会阻塞
        """
        now = now_bjt()

        async with _sf()() as session:
            # 构建查询：pending 任务按优先级 + 创建时间排序
            stmt = (
                select(ExecutionQueueTask)
                .where(ExecutionQueueTask.status == "pending")
                .where(ExecutionQueueTask.queue_name == queue_name)
                .where(ExecutionQueueTask.next_attempt_at <= now)
            )
            if task_types:
                stmt = stmt.where(ExecutionQueueTask.task_type.in_(task_types))

            stmt = (
                stmt
                .order_by(
                    ExecutionQueueTask.priority.asc(),
                    ExecutionQueueTask.next_attempt_at.asc(),
                    ExecutionQueueTask.created_at.asc(),
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )

            result = await session.execute(stmt)
            task = result.scalar_one_or_none()

            if task is None:
                return None

            # 原子更新：claimed
            task.status = "running"
            task.claimed_by = worker_id
            task.claimed_at = now
            task.started_at = now
            await session.commit()

            logger.info("任务领取: id={} worker={} type={}",
                        task.id, worker_id, task.task_type)

            return {
                "id": task.id,
                "task_type": task.task_type,
                "skill_id": task.skill_id,
                "payload": task.payload,
                "priority": task.priority,
                "queue_name": task.queue_name,
                "timeout_seconds": task.timeout_seconds,
                "retry_count": task.retry_count,
                "max_retries": task.max_retries,
                "created_by": task.created_by,
            }

    async def complete(self, task_id: int, result: dict, worker_id: str | None = None) -> None:
        """标记任务完成。

        `worker_id` 为抢到任务的 worker 标识，UPDATE WHERE 会校验 claimed_by 必须匹配，
        防止"任一 worker 可标记他人正在跑的 task 为完成"的越权。传 None 兼容老调用方
        （测试/单 worker 场景），但生产多 worker 强烈建议传入。
        """
        async with _sf()() as session:
            stmt = (
                update(ExecutionQueueTask)
                .where(ExecutionQueueTask.id == task_id)
                .where(ExecutionQueueTask.status == "running")
            )
            if worker_id is not None:
                stmt = stmt.where(ExecutionQueueTask.claimed_by == worker_id)
            stmt = stmt.values(
                status="completed",
                result=result,
                error_message=None,
                completed_at=now_bjt(),
            )
            cr = await session.execute(stmt)
            await session.commit()
            if worker_id is not None and cr.rowcount == 0:
                logger.warning(
                    "任务完成被拒：id={} worker={} 非原抢占者或状态不匹配",
                    task_id, worker_id,
                )
                raise ValueError(f"task {task_id} not owned by worker {worker_id}")
        logger.info("任务完成: id={} worker={}", task_id, worker_id or "(legacy)")

    async def fail(self, task_id: int, error: str, *, retry_delay_seconds: int | float | None = None) -> None:
        """标记任务失败。如果未达 max_retries，重置为 pending 等待重试。"""
        async with _sf()() as session:
            # 先查任务当前状态
            result = await session.execute(
                select(ExecutionQueueTask)
                .where(ExecutionQueueTask.id == task_id)
                .with_for_update()
            )
            task = result.scalar_one_or_none()
            if not task:
                logger.warning("任务不存在: id={}", task_id)
                return

            task.retry_count += 1

            if task.retry_count < task.max_retries:
                # 未达重试上限：重置为 pending，等待重新领取
                retry_delay = max(float(retry_delay_seconds or 0), 0.0)
                task.status = "pending"
                task.claimed_by = None
                task.claimed_at = None
                task.started_at = None
                task.next_attempt_at = now_bjt() + timedelta(seconds=retry_delay)
                task.error_message = error
                logger.info(
                    "任务重试: id={} retry={}/{} next_attempt_at={}",
                    task_id,
                    task.retry_count,
                    task.max_retries,
                    isoformat_bjt(task.next_attempt_at),
                )
            else:
                # 达到重试上限：标记为最终失败
                task.status = "failed"
                task.error_message = error
                task.completed_at = now_bjt()
                logger.warning("任务最终失败: id={} error={}", task_id, error)

            await session.commit()

    async def cancel(self, task_id: int) -> None:
        """取消任务。只能取消 pending 状态的任务。"""
        async with _sf()() as session:
            result = await session.execute(
                update(ExecutionQueueTask)
                .where(ExecutionQueueTask.id == task_id)
                .where(ExecutionQueueTask.status.in_(["pending", "running"]))
                .values(
                    status="cancelled",
                    completed_at=now_bjt(),
                )
            )
            await session.commit()

            if result.rowcount == 0:
                logger.warning("取消任务失败（任务不存在或状态不允许）: id={}", task_id)
            else:
                logger.info("任务已取消: id={}", task_id)

    async def reclaim_stale(self, timeout_margin: int = 60) -> int:
        """回收超时未完成的任务（running 但超过 timeout_seconds + margin）。

        将 status 改为 pending 或 failed（如果已达 max_retries）。
        返回回收数量。
        """
        now = now_bjt()
        reclaimed = 0

        async with _sf()() as session:
            # 查找所有超时的 running 任务
            # 条件：started_at + timeout_seconds + margin < now
            stmt = (
                select(ExecutionQueueTask)
                .where(ExecutionQueueTask.status == "running")
                .where(ExecutionQueueTask.started_at.isnot(None))
                .where(
                    text(
                        "started_at + make_interval(secs => timeout_seconds + :margin) < :now"
                    ).bindparams(margin=timeout_margin, now=now)
                )
                .with_for_update(skip_locked=True)
            )

            result = await session.execute(stmt)
            stale_tasks = result.scalars().all()

            for task in stale_tasks:
                task.retry_count += 1
                if task.retry_count < task.max_retries:
                    # 重试：重置为 pending
                    task.status = "pending"
                    task.claimed_by = None
                    task.claimed_at = None
                    task.started_at = None
                    task.next_attempt_at = now
                    task.error_message = f"超时回收 (timeout={task.timeout_seconds}s)"
                    logger.info("超时回收(重试): id={} retry={}/{}", task.id, task.retry_count, task.max_retries)
                else:
                    # 达到重试上限：最终失败
                    task.status = "failed"
                    task.error_message = f"超时且重试耗尽 (timeout={task.timeout_seconds}s)"
                    task.completed_at = now
                    logger.warning("超时回收(失败): id={}", task.id)
                reclaimed += 1

            await session.commit()

        if reclaimed:
            logger.info("超时回收完成: {} 个任务", reclaimed)
        return reclaimed

    async def get_queue_stats(self, queue_name: str | None = None) -> dict:
        """队列统计：各状态任务数量。"""
        async with _sf()() as session:
            stmt = select(
                ExecutionQueueTask.status,
                func.count(ExecutionQueueTask.id).label("count"),
            )
            if queue_name:
                stmt = stmt.where(ExecutionQueueTask.queue_name == queue_name)
            stmt = stmt.group_by(ExecutionQueueTask.status)

            result = await session.execute(stmt)
            rows = result.all()

        stats = {
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for status, count in rows:
            stats[status] = count

        stats["total"] = sum(stats.values())
        if queue_name:
            stats["queue_name"] = queue_name
        return stats

    async def list_tasks(
        self,
        *,
        queue_name: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """分页查询任务列表。"""
        async with _sf()() as session:
            stmt = select(ExecutionQueueTask)

            if queue_name:
                stmt = stmt.where(ExecutionQueueTask.queue_name == queue_name)
            if status:
                stmt = stmt.where(ExecutionQueueTask.status == status)

            # 统计总数
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0

            # 分页查询
            stmt = (
                stmt
                .order_by(ExecutionQueueTask.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await session.execute(stmt)
            tasks = result.scalars().all()

        return {
            "items": [
                {
                    "id": t.id,
                    "task_type": t.task_type,
                    "skill_id": t.skill_id,
                    "priority": t.priority,
                    "status": t.status,
                    "queue_name": t.queue_name,
                    "claimed_by": t.claimed_by,
                    "retry_count": t.retry_count,
                    "max_retries": t.max_retries,
                    "timeout_seconds": t.timeout_seconds,
                    "error_message": t.error_message,
                    "created_by": t.created_by,
                    "created_at": isoformat_bjt(t.created_at),
                    "started_at": isoformat_bjt(t.started_at),
                    "completed_at": isoformat_bjt(t.completed_at),
                }
                for t in tasks
            ],
            "total": total,
            "page": page,
        }


# 全局实例
task_queue = TaskQueue()
