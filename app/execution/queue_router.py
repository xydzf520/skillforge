"""执行队列 API 路由

提供队列统计、任务列表、取消任务、超时回收等管理端点。
"""

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import get_current_user, require_role
from app.auth.models import User
from app.common.exceptions import AppError
from app.execution.task_queue import task_queue
from app.execution.exec_scheduler import exec_scheduler

router = APIRouter()


@router.get("/queue/stats")
async def queue_stats(
    queue_name: str | None = Query(None, description="队列名称，为空返回全局统计"),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """队列统计：各状态任务数量"""
    if queue_name:
        return await task_queue.get_queue_stats(queue_name=queue_name)
    return await exec_scheduler.get_all_queue_stats()


@router.get("/queue/tasks")
async def queue_tasks(
    queue_name: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """任务列表（分页）"""
    return await task_queue.list_tasks(
        queue_name=queue_name,
        status=status,
        page=page,
        page_size=page_size,
    )


@router.post("/queue/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """取消指定任务"""
    from app.common.audit import audit
    await task_queue.cancel(task_id)
    await audit.log(current_user.id, "queue.task_cancel", "execution_queue", str(task_id))
    return {"status": "ok", "task_id": task_id}


@router.post("/queue/reclaim-stale")
async def reclaim_stale(
    timeout_margin: int = Query(60, ge=0, le=600, description="超时余量(秒)"),
    current_user: User = Depends(require_role("admin")),
):
    """手动回收超时任务"""
    from app.common.audit import audit
    count = await task_queue.reclaim_stale(timeout_margin=timeout_margin)
    await audit.log(current_user.id, "queue.reclaim_stale", detail={"reclaimed": count})
    return {"status": "ok", "reclaimed": count}
