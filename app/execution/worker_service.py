"""Worker registry service for multi-worker execution."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppError
from app.execution.models import ExecutionWorker
from app.common.time_utils import isoformat_bjt, now_bjt


def _serialize(worker: ExecutionWorker) -> dict:
    return {
        "id": worker.id,
        "name": worker.name,
        "queue_name": worker.queue_name,
        "capacity": worker.capacity,
        "active_runs": worker.active_runs,
        "status": worker.status,
        "last_seen_at": isoformat_bjt(worker.last_seen_at),
        "metadata": worker.metadata_json or {},
        "created_at": isoformat_bjt(worker.created_at),
    }


async def register_worker(
    db: AsyncSession,
    *,
    worker_id: str,
    name: str,
    queue_name: str | None = None,
    capacity: int = 1,
    active_runs: int = 0,
    metadata: dict | None = None,
) -> dict:
    worker = await db.get(ExecutionWorker, worker_id)
    if worker is None:
        worker = ExecutionWorker(id=worker_id, name=name)
        db.add(worker)
    worker.name = name
    worker.queue_name = queue_name
    worker.capacity = capacity
    worker.active_runs = active_runs
    worker.status = "online"
    worker.last_seen_at = now_bjt()
    worker.metadata_json = metadata or {}
    await db.flush()
    return _serialize(worker)


async def heartbeat_worker(
    db: AsyncSession,
    *,
    worker_id: str,
    active_runs: int | None = None,
    status: str | None = None,
    metadata: dict | None = None,
) -> dict:
    worker = await db.get(ExecutionWorker, worker_id)
    if not worker:
        raise AppError("NOT_FOUND", 404, {"resource": "execution_worker", "id": worker_id})
    if active_runs is not None:
        worker.active_runs = active_runs
    if status is not None:
        worker.status = status
    if metadata is not None:
        worker.metadata_json = metadata
    worker.last_seen_at = now_bjt()
    await db.flush()
    return _serialize(worker)


async def list_workers(db: AsyncSession) -> dict:
    rows = (await db.execute(select(ExecutionWorker).order_by(ExecutionWorker.name.asc(), ExecutionWorker.id.asc()))).scalars().all()
    stale_after = now_bjt() - timedelta(minutes=5)
    items = []
    for row in rows:
        item = _serialize(row)
        item["is_stale"] = bool(row.last_seen_at and row.last_seen_at < stale_after)
        items.append(item)
    return {"items": items, "total": len(items)}
