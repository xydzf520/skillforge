import pytest


async def _reset_agent_core_singletons():
    import app.agent_core.checkpointer as ckpt
    from app.agent_core.graph import reset_graphs

    reset_graphs()
    pool = ckpt._pool
    ckpt._pool = None
    ckpt._saver = None
    ckpt._init_lock = None
    if pool is not None:
        try:
            await pool.close()
        except Exception:
            pass


@pytest.fixture(autouse=True)
async def _mock_agent_core_llm(monkeypatch):
    async def _fake_call_llm(*args, **kwargs):
        return None

    import app.agent_core.intent_extractor as ie
    import app.agent_core.nodes.skill_generate as sg

    monkeypatch.setattr(ie, "call_llm", _fake_call_llm)
    monkeypatch.setattr(sg, "call_llm", _fake_call_llm)
    await _reset_agent_core_singletons()
    yield
    await _reset_agent_core_singletons()


@pytest.mark.asyncio
async def test_dingtalk_daily_journey(client):
    resp = await client.post(
        "/api/skills/workbench/task-contract",
        json={"message": "每天 18:00 发昨日销售钉钉日报到销售运营群"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["contract"]["trigger"]["expression"] == "0 18 * * *"
    assert data["contract"]["output"]["adapter"] == "dingtalk_card"
    assert "昨日销售日报" in data["preview"]["rendered_output"]
    assert len(data["contract"]["test_cases"]) >= 3
