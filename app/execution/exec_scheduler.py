"""执行调度器。

决定任务应该分配到哪个 queue（基于 task_type 和 worker 能力），
是 task_queue 的上层封装。
"""

from loguru import logger
from sqlalchemy import select, func

from app.execution.task_queue import task_queue
from app.execution.models import OpenClawInstance


def _sf():
    """延迟获取 async_session_factory，避免测试中 engine 替换问题"""
    from app.database import async_session_factory
    return async_session_factory


class ExecutionScheduler:
    """执行调度器：任务路由 + worker 选择。

    负责将不同类型的任务路由到合适的队列，
    并提供 worker 能力查询。
    """

    # 队列路由规则：task_type → queue_name
    QUEUE_RULES: dict[str, str] = {
        "skill_run": "default",
        "link_decline_operator_trigger": "default",
        "playbook_run": "default",
        "test_run": "test",
        "browser_task": "browser",
    }

    async def schedule(
        self,
        task_type: str,
        payload: dict,
        *,
        skill_id: str | None = None,
        priority: int = 5,
        timeout_seconds: int = 300,
        max_retries: int = 2,
        created_by: str = "system",
    ) -> int:
        """智能调度：选择队列 -> 入队 -> 返回 task_id。

        1. 根据 task_type 确定目标 queue_name
        2. 检查是否有可用 worker（仅记录警告，不阻塞入队）
        3. 调用 task_queue.enqueue 入队
        """
        queue_name = self.QUEUE_RULES.get(task_type, "default")

        # 检查 worker 可用性（非阻塞，仅日志警告）
        worker = await self.get_worker_for_queue(queue_name)
        if not worker:
            logger.warning("队列 {} 暂无可用 worker，任务将排队等待", queue_name)

        task_id = await task_queue.enqueue(
            task_type=task_type,
            payload=payload,
            skill_id=skill_id,
            priority=priority,
            queue_name=queue_name,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            created_by=created_by,
        )

        logger.info("调度完成: task_id={} type={} -> queue={}",
                     task_id, task_type, queue_name)
        return task_id

    async def get_worker_for_queue(self, queue_name: str) -> dict | None:
        """查找指定队列的可用 worker（基于 OpenClaw 实例状态）。

        当前实现：检查 active 的 OpenClaw 实例。
        未来可扩展为 worker 注册表 + 容量检测。
        """
        async with _sf()() as session:
            stmt = (
                select(OpenClawInstance)
                .where(OpenClawInstance.is_active == True)  # noqa: E712
            )
            result = await session.execute(stmt)
            instances = result.scalars().all()

        if not instances:
            return None

        # 简单策略：返回第一个活跃实例
        # 未来可增加负载均衡策略（轮询/最少连接/容量感知）
        inst = instances[0]
        return {
            "id": inst.id,
            "name": inst.name,
            "gateway_url": inst.gateway_url,
            "department": inst.department,
        }

    async def get_all_queue_stats(self) -> dict:
        """获取所有队列的统计信息。"""
        # 列举所有已知的队列名
        queue_names = set(self.QUEUE_RULES.values())

        stats = {}
        for qn in sorted(queue_names):
            stats[qn] = await task_queue.get_queue_stats(queue_name=qn)

        # 加上全局统计
        stats["_global"] = await task_queue.get_queue_stats()
        return stats


# 全局实例
exec_scheduler = ExecutionScheduler()
