"""AIClaw bridge chat queue 反压测试：chunk vs control 双层策略。"""

from __future__ import annotations

import asyncio

import pytest


class DummyWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code=1000, reason=""):
        return None


@pytest.mark.asyncio
async def test_chunk_event_drops_oldest_when_full(monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.config import settings

    monkeypatch.setattr(settings, "BRIDGE_CHAT_QUEUE_MAX", 3)
    ws = DummyWS()
    conn = await bridge_registry.register("inst-bp-1", ws)
    try:
        run_id = "sf-test-chunk"
        queue = conn.open_chat_stream(run_id)
        for i in range(5):
            await conn.handle_forward_event(
                {
                    "epoch": conn.epoch,
                    "event": "chat",
                    "payload": {"runId": run_id, "state": "delta", "text": str(i)},
                }
            )
        # 满 3 条，前 2 条已被 drop
        assert queue.qsize() <= 3
        items = []
        while not queue.empty():
            items.append(queue.get_nowait())
        # 最后一条 chunk 必须保留
        assert items[-1]["payload"]["text"] == "4"
    finally:
        await bridge_registry.unregister("inst-bp-1")


@pytest.mark.asyncio
async def test_control_event_never_dropped_when_full(monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.config import settings

    monkeypatch.setattr(settings, "BRIDGE_CHAT_QUEUE_MAX", 2)
    ws = DummyWS()
    conn = await bridge_registry.register("inst-bp-2", ws)
    try:
        run_id = "sf-test-ctrl"
        queue = conn.open_chat_stream(run_id)
        # 先塞满 chunk
        for i in range(2):
            await conn.handle_forward_event(
                {
                    "epoch": conn.epoch,
                    "event": "chat",
                    "payload": {"runId": run_id, "state": "delta", "text": str(i)},
                }
            )
        assert queue.full()
        # 再塞 final，必须把 chunk 挤掉而不是丢自己
        await conn.handle_forward_event(
            {
                "epoch": conn.epoch,
                "event": "chat",
                "payload": {"runId": run_id, "state": "final", "text": "done"},
            }
        )
        items = []
        while not queue.empty():
            items.append(queue.get_nowait())
        # final 必须出现在 items 中
        assert any(item["payload"].get("state") == "final" for item in items)
    finally:
        await bridge_registry.unregister("inst-bp-2")


@pytest.mark.asyncio
async def test_inactive_run_event_rejected(monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry

    ws = DummyWS()
    conn = await bridge_registry.register("inst-bp-3", ws)
    try:
        # 不调 open_chat_stream → 没有 active run
        await conn.handle_forward_event(
            {
                "epoch": conn.epoch,
                "event": "chat",
                "payload": {"runId": "ghost-run", "state": "delta"},
            }
        )
        # 没有任何 queue，事件被静默丢弃，不抛异常即可
        assert "ghost-run" not in conn._chat_queues
    finally:
        await bridge_registry.unregister("inst-bp-3")


@pytest.mark.asyncio
async def test_run_id_alias_resolves_to_sf_run(monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry

    ws = DummyWS()
    conn = await bridge_registry.register("inst-bp-4", ws)
    try:
        sf_run_id = "sf-bp-4"
        aiclaw_run_id = "aiclaw-xyz"
        queue = conn.open_chat_stream(sf_run_id)
        conn.register_run_alias(sf_run_id, aiclaw_run_id)

        await conn.handle_forward_event(
            {
                "epoch": conn.epoch,
                "event": "chat",
                "payload": {"runId": aiclaw_run_id, "state": "delta", "text": "hi"},
            }
        )
        assert queue.qsize() == 1
    finally:
        await bridge_registry.unregister("inst-bp-4")
