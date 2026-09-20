import asyncio
import time
from unittest.mock import AsyncMock
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.common.time_utils import now_bjt


async def _insert_outbox_rows(count: int, **overrides) -> None:
    from app.database import async_session_factory
    from app.dingtalk.models import DingTalkOutbox

    rows = [
        DingTalkOutbox(
            message_type=overrides.get("message_type", "work_notice"),
            priority=overrides.get("priority", 5),
            recipient_user_id=overrides.get("recipient_user_id", f"user-{idx}"),
            payload=overrides.get("payload", {"title": "t", "markdown": "m"}),
            status=overrides.get("status", "pending"),
            attempts=overrides.get("attempts", 0),
            max_attempts=overrides.get("max_attempts", 3),
            next_retry_at=overrides.get("next_retry_at"),
            error_message=overrides.get("error_message"),
            created_at=overrides.get("created_at", datetime.utcnow()),
        )
        for idx in range(count)
    ]
    async with async_session_factory() as session:
        session.add_all(rows)
        await session.commit()


async def _list_outbox_rows():
    from app.database import async_session_factory
    from app.dingtalk.models import DingTalkOutbox

    async with async_session_factory() as session:
        result = await session.execute(select(DingTalkOutbox).order_by(DingTalkOutbox.id.asc()))
        return list(result.scalars().all())


async def _wait_for(predicate, *, timeout: float = 1.0, interval: float = 0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("condition not met before timeout")


@pytest.mark.asyncio
async def test_outbox_process_loop_batches_with_concurrency_cap(client, monkeypatch):
    from app.dingtalk import outbox as outbox_mod

    await _insert_outbox_rows(5)

    service = outbox_mod.DingTalkOutboxService()
    service.MAX_CONCURRENCY = 3
    service.IDLE_POLL_INTERVAL_SECONDS = 0.01
    service.ACTIVE_POLL_INTERVAL_SECONDS = 0.01
    monkeypatch.setattr(service, "_acquire_send_slot", AsyncMock())

    current = 0
    max_seen = 0
    finished = 0
    done = asyncio.Event()

    async def fake_send(_msg):
        nonlocal current, max_seen, finished
        current += 1
        max_seen = max(max_seen, current)
        await asyncio.sleep(0.05)
        current -= 1
        finished += 1
        if finished == 5:
            done.set()
        return {"ok": True}

    monkeypatch.setattr(outbox_mod.dingtalk_client, "send", fake_send)

    task = asyncio.create_task(service.process_loop())
    await asyncio.wait_for(done.wait(), timeout=1.5)

    async def all_sent():
        rows = await _list_outbox_rows()
        return all(row.status == "sent" for row in rows)

    await _wait_for(all_sent)
    task.cancel()
    await task

    rows = await _list_outbox_rows()
    assert max_seen == 3
    assert all(row.status == "sent" for row in rows)


@pytest.mark.asyncio
async def test_outbox_acquire_send_slot_preserves_global_rate_budget():
    from app.dingtalk.outbox import DingTalkOutboxService

    service = DingTalkOutboxService()
    service.SEND_INTERVAL = 0.05

    timestamps = []

    async def acquire_once():
        await service._acquire_send_slot()
        timestamps.append(time.monotonic())

    await asyncio.gather(*(acquire_once() for _ in range(3)))

    assert len(timestamps) == 3
    deltas = [timestamps[idx + 1] - timestamps[idx] for idx in range(2)]
    assert all(delta >= 0.04 for delta in deltas), deltas


@pytest.mark.asyncio
async def test_outbox_failure_uses_exponential_backoff_with_jitter(client, monkeypatch):
    from app.dingtalk import outbox as outbox_mod

    await _insert_outbox_rows(1)

    service = outbox_mod.DingTalkOutboxService()
    monkeypatch.setattr(service, "_acquire_send_slot", AsyncMock())
    monkeypatch.setattr(outbox_mod.random, "uniform", lambda _a, _b: 1.25)
    monkeypatch.setattr(outbox_mod.dingtalk_client, "send", AsyncMock(return_value={"ok": False, "error": "boom"}))

    claimed = await service._claim_batch(1)
    await service._deliver_one(claimed[0])

    row = (await _list_outbox_rows())[0]
    retry_after = (row.next_retry_at - now_bjt()).total_seconds()

    assert row.status == "pending"
    assert row.attempts == 1
    assert row.error_message == "boom"
    assert 34 <= retry_after <= 38


@pytest.mark.asyncio
async def test_outbox_terminal_failure_clears_retry_schedule(client, monkeypatch):
    from app.dingtalk import outbox as outbox_mod

    await _insert_outbox_rows(1, attempts=2, max_attempts=3)

    service = outbox_mod.DingTalkOutboxService()
    monkeypatch.setattr(service, "_acquire_send_slot", AsyncMock())
    monkeypatch.setattr(outbox_mod.dingtalk_client, "send", AsyncMock(return_value={"ok": False, "error": "fatal"}))

    claimed = await service._claim_batch(1)
    await service._deliver_one(claimed[0])

    row = (await _list_outbox_rows())[0]
    assert row.status == "failed"
    assert row.attempts == 3
    assert row.next_retry_at is None
    assert row.error_message == "fatal"


@pytest.mark.asyncio
async def test_outbox_reclaim_stale_sending_and_ignore_late_success(client, monkeypatch):
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.dingtalk.models import DingTalkOutbox

    await _insert_outbox_rows(1)

    service = outbox_mod.DingTalkOutboxService()
    monkeypatch.setattr(service, "_acquire_send_slot", AsyncMock())
    monkeypatch.setattr(outbox_mod.dingtalk_client, "send", AsyncMock(return_value={"ok": True}))

    claimed = await service._claim_batch(1)

    async with async_session_factory() as session:
        await session.execute(
            select(DingTalkOutbox).where(DingTalkOutbox.id == claimed[0].id).with_for_update()
        )
        await session.execute(
            DingTalkOutbox.__table__.update()
            .where(DingTalkOutbox.id == claimed[0].id)
            .values(
                status="sending",
                next_retry_at=datetime.utcnow() - timedelta(seconds=1),
            )
        )
        await session.commit()

    reclaimed = await service._reclaim_stale_sending()
    await service._deliver_one(claimed[0])

    row = (await _list_outbox_rows())[0]
    assert reclaimed == 1
    assert row.status == "pending"
    assert row.next_retry_at is None
    assert row.error_message == "sending timeout reclaimed"
