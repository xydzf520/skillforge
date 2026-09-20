"""v7 agent_core runtime 集成测试。

两个关键 fixture：
- _reset_agent_core_state：每个 case 前后清空 graph / checkpointer 进程级单例，
  避免 LangGraph AsyncPostgresSaver 的 asyncio.Lock 绑定到上一个 event loop。
- _mock_llm：把 agent_core 里所有 call_llm 入口 mock 成返回 None，
  触发 intent_extractor / skill_generate / cross_model_check 的正则/模板 fallback，
  让测试不依赖真实 LLM 网络。
"""

import pytest
import pytest_asyncio

from app.agent_core.progress_translator import translate_event
from app.agent_core.runtime import run_skill_graph


async def _reset_agent_core_singletons():
    """清空 graph / checkpointer 进程级单例,并 await 关掉底层 pool。"""
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


@pytest_asyncio.fixture(autouse=True)
async def _reset_agent_core_state():
    """每个 async 用例前后清掉 agent_core 的进程级单例,避免 LangGraph
    AsyncPostgresSaver 的 asyncio.Lock 绑到上一个 event loop。"""
    await _reset_agent_core_singletons()
    yield
    await _reset_agent_core_singletons()


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    async def _fake_call_llm(*args, **kwargs):
        return None

    import app.agent_core.intent_extractor as ie
    import app.agent_core.nodes.cross_model_check as cmc
    import app.agent_core.nodes.skill_generate as sg
    monkeypatch.setattr(ie, "call_llm", _fake_call_llm)
    monkeypatch.setattr(cmc, "call_llm", _fake_call_llm)
    monkeypatch.setattr(sg, "call_llm", _fake_call_llm)


async def test_agent_core_runtime_save_flow():
    """save 模式只跑 intent_extract + skill_generate，不会触发 interrupt。"""
    state = await run_skill_graph(
        "每天 18:00 发昨日销售钉钉日报到销售运营群",
        mode="save",
    )
    assert state["contract"]["output"]["adapter"] == "dingtalk_card"
    assert state.get("skill_md")
    assert state.get("skill")


def test_progress_translator():
    msg = translate_event("sandbox_run")
    assert "预演" in msg


async def test_agent_core_http_run(client):
    resp = await client.post(
        "/api/agent-core/run",
        json={"message": "每天 18:00 发昨日销售钉钉日报到销售运营群", "mode": "save"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "contract" in data
    assert "skill_md" in data


async def test_intent_extractor_invokes_llm_with_loaded_prompt(monkeypatch):
    """LLM 链路 smoke 测试:验证 extract_contract 真的 await call_llm,
    且传入的 system prompt 来自 prompt_loader 加载的 intent_extract.txt。
    防止 _mock_llm fixture 把测试假绿。"""
    from unittest.mock import AsyncMock

    import app.agent_core.intent_extractor as ie
    from app.agent_core.prompt_loader import load_prompt

    expected_prompt = load_prompt("intent_extract")
    assert expected_prompt and len(expected_prompt) > 100  # prompt 文件存在且非空

    fake_llm = AsyncMock(return_value={
        "goal": "测试目标",
        "trigger": {"type": "cron", "expression": "0 18 * * *"},
        "input": [{"name": "x", "type": "string", "source": "user"}],
        "output": {"adapter": "dingtalk_card", "schema": {}},
        "permissions": [{"action": "send_message", "reversible": False}],
        "risks": {
            "level": "R1",
            "data_classification": "internal",
            "department": "测试",
        },
        "test_cases": [
            {"name": f"case{i}", "input": {}, "expected_keywords": []}
            for i in range(3)
        ],
    })
    monkeypatch.setattr(ie, "call_llm", fake_llm)

    contract = await ie.extract_contract("每天18点发钉钉日报")

    # LLM 链路真被走过
    assert fake_llm.await_count == 1, "extract_contract 应该 await 一次 call_llm"
    call_kwargs = fake_llm.await_args.kwargs
    assert call_kwargs.get("system") == expected_prompt, (
        "传给 call_llm 的 system 必须是 prompt_loader 加载的 intent_extract.txt"
    )
    assert call_kwargs.get("json_mode") is True
    assert contract["output"]["adapter"] == "dingtalk_card"
