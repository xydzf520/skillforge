import asyncio

import pytest


@pytest.mark.asyncio
async def test_creation_task_manager_limits_running_tasks_and_queues(monkeypatch):
    import app.coding_agent.creation_task_manager as mod

    manager = mod.CreationTaskManager()
    started: list[str] = []
    gates = {draft_id: asyncio.Event() for draft_id in ("d1", "d2", "d3")}

    async def fake_run_skill_creation(*, message: str, draft_id: str, user_id: str | None = None):
        assert user_id == "u1"
        started.append(draft_id)
        await gates[draft_id].wait()
        if False:
            yield None

    async def fake_mark_running(_draft_id: str) -> bool:
        return True

    monkeypatch.setattr(mod, "run_skill_creation", fake_run_skill_creation)
    monkeypatch.setattr(mod.CreationTaskManager, "_mark_running", staticmethod(fake_mark_running))

    manager.start("d1", "msg-1", "u1")
    manager.start("d2", "msg-2", "u1")
    manager.start("d3", "msg-3", "u1")

    await asyncio.sleep(0.05)

    assert mod.MAX_CONCURRENT_TASKS == 2
    assert started == ["d1", "d2"]
    assert manager.is_running("d3") is True

    queued = await asyncio.wait_for(manager._queues["d3"].get(), timeout=0.2)
    assert queued == {
        "type": "queued",
        "position": 1,
        "max_concurrent_tasks": 2,
    }

    gates["d1"].set()
    await asyncio.sleep(0.05)
    assert started == ["d1", "d2", "d3"]

    gates["d2"].set()
    gates["d3"].set()
    await asyncio.sleep(0.05)
    assert manager._running_count() == 0
