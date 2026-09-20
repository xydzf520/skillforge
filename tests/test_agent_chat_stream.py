"""Agent 对话流式接口（send_message_stream）集成测试。

直接驱动 send_message_stream 异步生成器，验证事件序列正确：
- 正常对话：delta+ → done
- 触发压缩：compact → delta+ → done
- LLM 错误：error
- WebSocket 端点级测试见 tests/test_workbench_chat_stream.py（用 starlette TestClient）
"""

import pytest
from datetime import datetime, timezone

from app.common.context_budget import BudgetConfig, context_budget
from app.testing.agent_chat import agent_chat_service
from app.testing.models import Conversation


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def small_budget(monkeypatch):
    """复用同样的小预算配置触发压缩"""
    original_config = context_budget.config
    monkeypatch.setattr(
        context_budget,
        "config",
        BudgetConfig(
            effective_window=8500,
            output_reserve=500,
            autocompact_buffer=2000,
            warning_ratio=0.80,
            error_ratio=0.95,
            keep_recent_turns=3,
            max_consecutive_failures=3,
        ),
    )
    yield
    monkeypatch.setattr(context_budget, "config", original_config)


@pytest.fixture
def patched_stream_llm(monkeypatch):
    """mock call_llm_stream 返回固定的 SSE 事件序列"""

    async def fake_stream(*args, **kwargs):
        yield {"type": "delta", "content": "你好"}
        yield {"type": "delta", "content": "，我是"}
        yield {"type": "delta", "content": " AI"}
        yield {"type": "usage", "input_tokens": 50, "output_tokens": 30, "total_tokens": 80}
        yield {"type": "done", "finish_reason": "stop"}

    async def fake_system_prompt(skill_id, session):
        return "你是测试助手"

    monkeypatch.setattr("app.testing.agent_chat.call_llm_stream", fake_stream, raising=False)
    # call_llm_stream 是从 app.common.ai 导入到 send_message_stream 函数内部的，
    # 所以也要 patch app.common.ai
    monkeypatch.setattr("app.common.ai.call_llm_stream", fake_stream)
    monkeypatch.setattr(agent_chat_service, "_build_system_prompt", fake_system_prompt)
    return fake_stream


async def _create_conv(message_count: int = 0, msg_chars: int = 200) -> str:
    import app.database as db_mod
    import uuid

    conv_id = f"test-{uuid.uuid4().hex[:10]}"
    messages = []
    for i in range(message_count):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({
            "role": role,
            "content": f"{role}_{i}_" + ("内容" * (msg_chars // 2)),
            "timestamp": _utcnow().isoformat(),
        })
    async with db_mod.async_session_factory() as session:
        conv = Conversation(
            id=conv_id, skill_id="TEST-SKILL", user_id="admin",
            messages=messages, model_id="test", total_tokens=0,
            compact_boundary_idx=0, compact_failure_count=0,
            usage_snapshot=None,
            created_at=_utcnow(), updated_at=_utcnow(),
        )
        session.add(conv)
        await session.commit()
    return conv_id


# ===== 1. 正常流式 =====

@pytest.mark.asyncio
async def test_stream_normal_flow(client, patched_stream_llm):
    """正常对话：3 个 delta + done"""
    conv_id = await _create_conv(message_count=2)

    events = []
    async for ev in agent_chat_service.send_message_stream(conv_id, "你好"):
        events.append(ev)

    delta_events = [e for e in events if e["type"] == "delta"]
    done_events = [e for e in events if e["type"] == "done"]

    assert len(delta_events) == 3
    assert delta_events[0]["content"] == "你好"
    assert "AI" in delta_events[2]["content"]
    assert len(done_events) == 1
    assert done_events[0]["total_tokens"] == 80


# ===== 2. 触发压缩的流式 =====

@pytest.mark.asyncio
async def test_stream_with_compact(client, small_budget, patched_stream_llm, monkeypatch):
    """超过 compact 阈值时应先 yield compact，再 yield delta"""
    conv_id = await _create_conv(message_count=25, msg_chars=800)

    async def patched_call(self, user_input, summarizer=None):
        return "前 N 轮的简短摘要"

    monkeypatch.setattr(type(context_budget), "_call_summarizer", patched_call)

    events = []
    async for ev in agent_chat_service.send_message_stream(conv_id, "新提问"):
        events.append(ev)

    compact_events = [e for e in events if e["type"] == "compact"]
    delta_events = [e for e in events if e["type"] == "delta"]

    assert len(compact_events) == 1
    assert compact_events[0]["original_count"] > 0
    assert len(delta_events) == 3

    # compact 必须在所有 delta 之前
    compact_idx = next(i for i, e in enumerate(events) if e["type"] == "compact")
    first_delta_idx = next(i for i, e in enumerate(events) if e["type"] == "delta")
    assert compact_idx < first_delta_idx


# ===== 3. LLM 错误 =====

@pytest.mark.asyncio
async def test_stream_propagates_llm_error(client, monkeypatch):
    """call_llm_stream 返回 error 事件时，send_message_stream 应透传"""

    async def failing_stream(*args, **kwargs):
        yield {"type": "delta", "content": "开头"}
        yield {"type": "error", "error": "模拟 LLM 错误"}

    async def fake_system_prompt(skill_id, session):
        return "你是测试助手"

    monkeypatch.setattr("app.common.ai.call_llm_stream", failing_stream)
    monkeypatch.setattr(agent_chat_service, "_build_system_prompt", fake_system_prompt)

    conv_id = await _create_conv(message_count=2)

    events = []
    async for ev in agent_chat_service.send_message_stream(conv_id, "提问"):
        events.append(ev)

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert "模拟 LLM 错误" in error_events[0]["error"]


# ===== 4. 不存在的会话 =====

@pytest.mark.asyncio
async def test_stream_unknown_conversation(client, patched_stream_llm):
    """不存在的会话应 yield error，而非抛异常"""
    events = []
    async for ev in agent_chat_service.send_message_stream("nonexistent-id", "提问"):
        events.append(ev)

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["code"] == "SKILL_NOT_FOUND"


# ===== 5. 单次巨量消息 =====

@pytest.mark.asyncio
async def test_stream_huge_message_overflow(client, small_budget, patched_stream_llm):
    """单次巨量消息应 yield CONTEXT_OVERFLOW"""
    conv_id = await _create_conv(message_count=0)

    huge_msg = "巨量内容" * 6000
    events = []
    async for ev in agent_chat_service.send_message_stream(conv_id, huge_msg):
        events.append(ev)

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["code"] == "CONTEXT_OVERFLOW"


# ===== 6. 熔断 =====

@pytest.mark.asyncio
async def test_stream_circuit_breaker(client, small_budget, patched_stream_llm):
    """failure_count >= 3 时应立即 yield COMPACT_CIRCUIT_BREAKER"""
    import app.database as db_mod
    from sqlalchemy import update

    conv_id = await _create_conv(message_count=25, msg_chars=800)

    # 直接预置 failure_count = 3
    async with db_mod.async_session_factory() as session:
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conv_id)
            .values(compact_failure_count=3)
        )
        await session.commit()

    events = []
    async for ev in agent_chat_service.send_message_stream(conv_id, "提问"):
        events.append(ev)

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["code"] == "COMPACT_CIRCUIT_BREAKER"
