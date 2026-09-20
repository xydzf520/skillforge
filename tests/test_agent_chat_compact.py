"""Agent对话上下文压缩与熔断的集成测试。

覆盖：
1. 多轮对话超过水位时自动压缩，boundary 正确插入
2. 失败计数达到阈值时立即触发 503 熔断
3. 单次巨量消息触发 413 CONTEXT_OVERFLOW
4. 压缩后预算回到 ok/warn 状态

测试策略：
- 用 monkeypatch 替换 agent_chat_service._call_llm 和 _build_system_prompt（避免真实 LLM 调用）
- 用 monkeypatch 替换 context_budget._call_summarizer 控制摘要返回值
- 直接操作 Conversation ORM 插入测试数据
- 必须依赖 client fixture 才能初始化测试 DB schema
"""

import pytest
from datetime import datetime, timedelta, timezone

from app.common.context_budget import BudgetConfig, context_budget
from app.testing.agent_chat import agent_chat_service
from app.testing.models import Conversation


# ===== 公共 fixture =====

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def small_budget(monkeypatch):
    """临时把全局 context_budget 调小，方便触发压缩。

    设计要点：
    - effective_window=8500，output_reserve=500
    - compact 阈值 = 8500 - 2000 = 6500
    - error 阈值 = 8500 * 0.95 = 8075
    - 想触发 compact，input_tokens 需要在 [6000, 7575] 之间
    """
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
    yield context_budget.config
    monkeypatch.setattr(context_budget, "config", original_config)


async def _create_test_conversation(
    skill_id: str = "TEST-SKILL",
    message_count: int = 0,
    msg_chars: int = 200,
    pre_failure_count: int = 0,
) -> str:
    """直接通过 DB 插入一条测试 Conversation。"""
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
            id=conv_id,
            skill_id=skill_id,
            user_id="admin",
            messages=messages,
            model_id="test-model",
            total_tokens=0,
            compact_boundary_idx=0,
            compact_failure_count=pre_failure_count,
            usage_snapshot=None,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(conv)
        await session.commit()

    return conv_id


async def _get_conversation(conversation_id: str) -> Conversation:
    import app.database as db_mod
    from sqlalchemy import select
    async with db_mod.async_session_factory() as session:
        result = await session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()


@pytest.fixture
def patched_llm(monkeypatch):
    """统一 mock 掉 LLM 调用与 system prompt 构建"""

    call_log = {"call_count": 0}

    # F4 接入后 _call_llm 新增了 cost_context 关键字参数，mock 需兼容 **kwargs
    async def fake_call_llm(messages, **kwargs):
        call_log["call_count"] += 1
        return "模拟 AI 回复", 100

    async def fake_system_prompt(skill_id, session):
        return "你是测试助手"

    monkeypatch.setattr(agent_chat_service, "_call_llm", fake_call_llm)
    monkeypatch.setattr(agent_chat_service, "_build_system_prompt", fake_system_prompt)
    return call_log


# ===== 1. 自动压缩 =====

@pytest.mark.asyncio
async def test_compress_triggers_when_above_threshold(client, small_budget, patched_llm, monkeypatch):
    """造一个超过 compact 阈值的会话，发送消息应触发压缩"""
    # 10 条消息 × 800 字符 = 8000 字符 → 约 2666 tokens
    # +500 reserve = 3166，远低于 compact 阈值 6500
    # 需要更大：30 条 × 800 = 24000 字符 → 8000 tokens → 8500 total
    # 8500 < error(8075)? 8500 > 8075 → error
    # 试试 25 条 × 800 = 20000 → 6666 tokens → 7166 total
    # 6500 ≤ 7166 < 8075 → compact ✓
    conv_id = await _create_test_conversation(message_count=25, msg_chars=800)

    # mock summarizer：返回成功摘要
    async def fake_summarizer(*args, **kwargs):
        return "前 N 轮的简短摘要"

    async def patched_call(self, user_input, summarizer=None):
        return await fake_summarizer()

    monkeypatch.setattr(
        type(context_budget), "_call_summarizer", patched_call
    )

    result = await agent_chat_service.send_message(conv_id, "新提问")

    # 应该返回 compact_event
    assert "compact_event" in result, f"未触发压缩，response={result}"
    assert result["compact_event"]["original_count"] > 0

    # DB 中的消息应包含 boundary 标记
    conv = await _get_conversation(conv_id)
    assert conv is not None
    assert conv.compact_boundary_idx == 0
    assert conv.compact_failure_count == 0
    boundary_msgs = [m for m in conv.messages if m.get("_boundary")]
    assert len(boundary_msgs) == 1
    assert "摘要" in boundary_msgs[0]["content"]
    # 持久化的消息数应明显小于初始 25 条 + 2 条新消息 = 27 条
    assert len(conv.messages) < 27


# ===== 2. 熔断 =====

@pytest.mark.asyncio
async def test_circuit_breaker_when_failure_count_at_threshold(
    client, small_budget, patched_llm
):
    """预置 compact_failure_count=3 的会话，发送消息应立即抛 503"""
    from app.common.exceptions import AppError

    # 同样造 25 × 800 字符的会话，确保会触发 compact 状态
    conv_id = await _create_test_conversation(
        message_count=25, msg_chars=800, pre_failure_count=3
    )

    with pytest.raises(AppError) as exc_info:
        await agent_chat_service.send_message(conv_id, "提问")

    assert exc_info.value.code == "COMPACT_CIRCUIT_BREAKER"
    assert exc_info.value.status == 503


# ===== 3. 单次巨量消息 =====

@pytest.mark.asyncio
async def test_huge_single_message_triggers_overflow(client, small_budget, patched_llm):
    """单条用户消息巨大，触发 413 CONTEXT_OVERFLOW"""
    from app.common.exceptions import AppError

    conv_id = await _create_test_conversation(message_count=0)

    # 8500 effective, error 阈值 = 8075
    # 想 input_tokens > 7575 (8075 - 500 reserve)
    # 字符数 > 7575 * 3 = 22725
    huge_msg = "巨量内容" * 6000  # 4 × 6000 = 24000 chars → 8000 tokens

    with pytest.raises(AppError) as exc_info:
        await agent_chat_service.send_message(conv_id, huge_msg)

    assert exc_info.value.code == "CONTEXT_OVERFLOW"
    assert exc_info.value.status == 413


# ===== 4. 压缩后预算回到 ok =====

@pytest.mark.asyncio
async def test_budget_recovers_after_compression(
    client, small_budget, patched_llm, monkeypatch
):
    """压缩成功后再次查询会话，预算应回到较低水位"""

    conv_id = await _create_test_conversation(message_count=25, msg_chars=800)

    async def patched_call(self, user_input, summarizer=None):
        return "短摘要"

    monkeypatch.setattr(type(context_budget), "_call_summarizer", patched_call)

    # 触发压缩
    await agent_chat_service.send_message(conv_id, "新提问")

    # 查询会话
    info = await agent_chat_service.get_conversation(conv_id)
    assert "budget_status" in info
    assert "budget_used" in info
    assert "budget_limit" in info
    assert info["compact_boundary_idx"] == 0
    assert info["compact_failure_count"] == 0
    # 压缩后应回到 ok 或 warn（不可能再是 compact 或 error）
    assert info["budget_status"] in ("ok", "warn")


# ===== 5. 不需要压缩时跳过 =====

@pytest.mark.asyncio
async def test_no_compress_below_threshold(client, small_budget, patched_llm):
    """消息量未达到 compact 阈值，应正常对话不触发压缩"""
    conv_id = await _create_test_conversation(message_count=5, msg_chars=200)

    result = await agent_chat_service.send_message(conv_id, "提问")

    assert "compact_event" not in result
    conv = await _get_conversation(conv_id)
    assert conv.compact_boundary_idx == 0
    boundary_msgs = [m for m in (conv.messages or []) if m.get("_boundary")]
    assert len(boundary_msgs) == 0


# ===== 6. 回归：boundary 必须回注给 LLM =====

@pytest.mark.asyncio
async def test_boundary_is_reinjected_into_llm_call(client, small_budget, patched_llm):
    """
    回归测试（来自 Codex 审计发现的 bug）：
    含有 boundary 消息的会话发送下一条时，boundary 内容必须以 system role 出现在
    传给 LLM 的 messages 列表里，否则历史摘要丢失。
    """
    import app.database as db_mod

    captured_messages = []

    async def capturing_call_llm(messages, **kwargs):
        captured_messages.extend(messages)
        return "好的", 50

    # 替换默认 patched_llm 的 _call_llm
    import pytest
    from unittest.mock import patch

    # 直接 monkey patch 一次
    original = agent_chat_service._call_llm
    agent_chat_service._call_llm = capturing_call_llm
    try:
        # 造一个含 boundary 的会话
        conv_id = f"test-{__import__('uuid').uuid4().hex[:10]}"
        boundary_content = "[历史摘要] 前 8 条消息已由 AI 自动压缩：\n用户问 ROI 阈值，AI 解释了规则"
        async with db_mod.async_session_factory() as session:
            conv = Conversation(
                id=conv_id,
                skill_id="TEST-SKILL",
                user_id="admin",
                messages=[
                    {
                        "role": "system",
                        "content": boundary_content,
                        "_boundary": True,
                        "_original_count": 8,
                        "timestamp": _utcnow().isoformat(),
                    },
                    {
                        "role": "user",
                        "content": "继续提问",
                        "timestamp": _utcnow().isoformat(),
                    },
                    {
                        "role": "assistant",
                        "content": "之前的回答",
                        "timestamp": _utcnow().isoformat(),
                    },
                ],
                model_id="test",
                total_tokens=0,
                compact_boundary_idx=0,
                compact_failure_count=0,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(conv)
            await session.commit()

        await agent_chat_service.send_message(conv_id, "新提问")

        # 验证：传给 LLM 的 messages 中应包含 boundary 内容
        system_msgs = [m for m in captured_messages if m.get("role") == "system"]
        # 至少应有 2 条 system: 主 system_prompt + boundary
        assert len(system_msgs) >= 2, f"system 消息数不对: {len(system_msgs)}"
        boundary_in_llm = any(
            "前 8 条消息" in m.get("content", "") or "历史摘要" in m.get("content", "")
            for m in system_msgs
        )
        assert boundary_in_llm, "boundary 内容未被注入到 LLM messages"
    finally:
        agent_chat_service._call_llm = original


# ===== 7. F6: 微压缩集成 =====

@pytest.fixture
def tiny_budget_for_warn(monkeypatch):
    """更窄的预算配置，使较少消息就能触发 warn 档位。

    设计：
    - effective_window=8500，output_reserve=500
    - warn 阈值 = 8500 * 0.80 = 6800
    - compact 阈值 = 8500 - 2000 = 6500（< warn）
    - 所以落在 [6500, 6800) 的 total 会是 compact，[6800, ?) 视情况
    微压缩的触发条件是 status in ("warn","compact")，所以只要进入任一档位即可。
    """
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
            micro_compact_enabled=True,
            micro_compact_threshold_minutes=10,
        ),
    )
    yield context_budget.config
    monkeypatch.setattr(context_budget, "config", original_config)


@pytest.mark.asyncio
async def test_micro_compact_triggered_and_shortens_messages(
    client, tiny_budget_for_warn, patched_llm
):
    """
    F6 集成：会话中混有大量带 [执行结果] 前缀的旧工具消息，
    send_message 触发 micro_compact，消息列表变短且 DB 也被更新。
    """
    import app.database as db_mod
    import uuid as _uuid

    conv_id = f"test-{_uuid.uuid4().hex[:10]}"
    old_ts = (_utcnow() - timedelta(minutes=60)).isoformat()
    fresh_ts = _utcnow().isoformat()

    # 构造：以 [执行结果] 开头的旧工具消息 + 3 条新鲜对话
    # tiny_budget 的阈值（tokens）：warn≈6800，compact≈6500，error≈8075
    # input_tokens ≈ chars/3，所以想进 compact 档位需要 chars ∈ [18000, 22725]
    # 用 10 条 × 2000 字符 = 20000 字符 → 约 6666 tokens → 进 compact 档位
    messages = []
    for i in range(10):
        messages.append({
            "role": "assistant",
            "content": f"[执行结果] 第{i}次执行：" + "数据" * 1000,  # 约 2000 字符
            "timestamp": old_ts,
        })
    # 3 条新鲜的普通对话（保护区，放在列表尾部）
    messages.append({"role": "user", "content": "最近提问1", "timestamp": fresh_ts})
    messages.append({"role": "assistant", "content": "最近回答1", "timestamp": fresh_ts})
    messages.append({"role": "user", "content": "最近提问2", "timestamp": fresh_ts})

    async with db_mod.async_session_factory() as session:
        conv = Conversation(
            id=conv_id,
            skill_id="TEST-SKILL",
            user_id="admin",
            messages=messages,
            model_id="test-model",
            total_tokens=0,
            compact_boundary_idx=0,
            compact_failure_count=0,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(conv)
        await session.commit()

    initial_count = len(messages)

    result = await agent_chat_service.send_message(conv_id, "新提问")

    # 应该返回 micro_compact_event
    assert "micro_compact_event" in result, (
        f"未触发微压缩，response keys={list(result.keys())}"
    )
    assert result["micro_compact_event"]["removed_count"] > 0

    # DB 中的消息应明显减少（原 13 条 - N 个删除 + 2 条新消息 < 15）
    conv = await _get_conversation(conv_id)
    assert conv is not None
    # 删除后的持久化长度应小于 initial_count + 2（user+assistant 追加）
    assert len(conv.messages) < initial_count + 2
    # 至少有相当一部分过期的 [执行结果] 消息被删除
    # （因 keep_recent_turns=3 会保留最近 6 条对话槽，可能有少量漏网）
    remaining_old_results = [
        m for m in conv.messages
        if isinstance(m.get("content"), str)
        and m["content"].startswith("[执行结果]")
    ]
    assert len(remaining_old_results) < 10, (
        f"过期执行结果清理力度不够，还剩 {len(remaining_old_results)} 条"
    )
    # removed_count 应与实际删除数量吻合
    assert result["micro_compact_event"]["removed_count"] == (10 - len(remaining_old_results))


@pytest.mark.asyncio
async def test_micro_compact_skipped_when_status_ok(
    client, tiny_budget_for_warn, patched_llm
):
    """
    F6 集成：水位在 ok 档位时，不应触发微压缩，
    即使消息里有过期的 [执行结果] 消息。
    """
    import app.database as db_mod
    import uuid as _uuid

    conv_id = f"test-{_uuid.uuid4().hex[:10]}"
    old_ts = (_utcnow() - timedelta(minutes=60)).isoformat()

    # 少量过期的 tool 结果 + 少量普通对话，token 总量远低于 warn 阈值
    messages = [
        {
            "role": "assistant",
            "content": "[执行结果] 旧结果",
            "timestamp": old_ts,
        },
        {
            "role": "user",
            "content": "你好",
            "timestamp": _utcnow().isoformat(),
        },
    ]

    async with db_mod.async_session_factory() as session:
        conv = Conversation(
            id=conv_id,
            skill_id="TEST-SKILL",
            user_id="admin",
            messages=messages,
            model_id="test-model",
            total_tokens=0,
            compact_boundary_idx=0,
            compact_failure_count=0,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(conv)
        await session.commit()

    result = await agent_chat_service.send_message(conv_id, "提问")

    # 水位是 ok，应该完全跳过 F6
    assert "micro_compact_event" not in result
    assert "compact_event" not in result
