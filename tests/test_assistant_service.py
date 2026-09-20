"""assistant_service 模块单元测试。

测试 app/workbench/assistant_service.py 中的函数：
handle_chat（patch / question 两条路径）
handle_command（各命令分发 + 未知命令）
handle_chat_stream（异步生成器事件流）
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────


def _base_kwargs(intent=None, message="测试消息", context=None):
    """handle_chat 的基础参数"""
    return {
        "skill_id": "EC-投放-01",
        "session_id": "wb-test123",
        "message": message,
        "context": context or {"active_module": "params"},
        "intent": intent,
        "reference_ids": [],
        "user_id": "admin",
        "department": "EC",
    }


# ── handle_chat: patch intent 路径 ──────────────────────────────────


@pytest.mark.asyncio
@patch("app.workbench.assistant_service.context_builder")
async def test_handle_chat_patch_intent(mock_cb):
    """patch intent 应调用 create_patch 并返回 type=patch"""
    from app.workbench.assistant_service import handle_chat

    mock_cb.build_chat_context.return_value = {}
    patch_response = MagicMock()
    patch_response.summary = "已调整阈值"
    patch_response.model_dump.return_value = {"target_module": "params", "changes": []}

    create_patch = AsyncMock(return_value=patch_response)
    generate_answer = AsyncMock()

    result = await handle_chat(
        create_patch=create_patch,
        generate_answer=generate_answer,
        **_base_kwargs(intent="tune_threshold"),
    )
    assert result["type"] == "patch"
    assert "已调整阈值" in result["message"]
    create_patch.assert_awaited_once()
    generate_answer.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.workbench.assistant_service.context_builder")
async def test_handle_chat_patch_error(mock_cb):
    """create_patch 失败时应返回 type=error"""
    from app.workbench.assistant_service import handle_chat

    mock_cb.build_chat_context.return_value = {}
    create_patch = AsyncMock(side_effect=RuntimeError("LLM 超时"))
    generate_answer = AsyncMock()

    result = await handle_chat(
        create_patch=create_patch,
        generate_answer=generate_answer,
        **_base_kwargs(intent="add_rule"),
    )
    assert result["type"] == "error"
    assert "Patch 生成失败" in result["message"]


# ── handle_chat: question intent 路径 ────────────────────────────────


@pytest.mark.asyncio
@patch("app.workbench.assistant_service.detect_intent")
async def test_handle_chat_question_intent(mock_detect):
    """question intent 应调用 generate_answer 并返回 type=answer"""
    from app.workbench.assistant_service import handle_chat

    mock_detect.return_value = {"intent": "question", "target_module": "goal"}
    create_patch = AsyncMock()
    generate_answer = AsyncMock(return_value="ROI 阈值用来判断投放计划...")

    result = await handle_chat(
        create_patch=create_patch,
        generate_answer=generate_answer,
        **_base_kwargs(intent=None, message="ROI 阈值是什么意思？"),
    )
    assert result["type"] == "answer"
    assert "ROI" in result["message"]
    create_patch.assert_not_awaited()
    generate_answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_chat_explicit_question():
    """显式传 intent=question 时应走 answer 路径"""
    from app.workbench.assistant_service import handle_chat

    create_patch = AsyncMock()
    generate_answer = AsyncMock(return_value="答案")

    result = await handle_chat(
        create_patch=create_patch,
        generate_answer=generate_answer,
        **_base_kwargs(intent="question"),
    )
    assert result["type"] == "answer"
    assert result["patch"] is None


# ── handle_command ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_command_unknown():
    """未知命令应返回 type=error"""
    from app.workbench.assistant_service import handle_command

    result = await handle_command(
        skill_id="EC-投放-01",
        session_id="wb-test123",
        command="nonexistent-cmd",
        context={},
        user_id="admin",
    )
    assert result["type"] == "error"
    assert "未知命令" in result["message"]


@pytest.mark.asyncio
@patch("app.workbench.assistant_service._cmd_generate_tests")
async def test_handle_command_generate_tests(mock_cmd):
    """generate-tests 命令应调用对应 handler"""
    from app.workbench.assistant_service import handle_command

    mock_cmd.return_value = {
        "type": "answer",
        "message": "已生成 5 个测试用例。",
        "patch": None,
        "validation_hint": {"affected_modules": ["test_cases"]},
    }

    result = await handle_command(
        skill_id="EC-投放-01",
        session_id="wb-test123",
        command="generate-tests",
        context={},
        user_id="admin",
    )
    assert result["type"] == "answer"
    assert "测试用例" in result["message"]
    mock_cmd.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.workbench.assistant_service._cmd_drift_check")
async def test_handle_command_drift_check(mock_cmd):
    """drift-check 命令正常返回"""
    from app.workbench.assistant_service import handle_command

    mock_cmd.return_value = {
        "type": "answer",
        "message": "漂移检测完成。当前有 3 个参数。",
        "patch": None,
        "validation_hint": None,
    }

    result = await handle_command(
        skill_id="EC-投放-01",
        session_id="wb-test123",
        command="drift-check",
        context={},
        user_id="admin",
    )
    assert result["type"] == "answer"
    assert "漂移检测" in result["message"]


@pytest.mark.asyncio
@patch("app.workbench.assistant_service._cmd_suggest_branches")
async def test_handle_command_handler_error(mock_cmd):
    """命令 handler 抛异常时应返回 type=error"""
    from app.workbench.assistant_service import handle_command

    mock_cmd.side_effect = RuntimeError("内部错误")

    result = await handle_command(
        skill_id="EC-投放-01",
        session_id="wb-test123",
        command="suggest-branches",
        context={},
        user_id="admin",
    )
    assert result["type"] == "error"
    assert "命令执行失败" in result["message"]


# ── handle_chat_stream ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_chat_stream_patch_intent():
    """patch intent 的流式输出应产出 patch + done 事件"""
    from app.workbench.assistant_service import handle_chat_stream

    handle_chat_fn = AsyncMock(return_value={
        "type": "patch",
        "patch": {"target_module": "params"},
        "message": "已调整",
        "validation_hint": None,
    })

    events = []
    async for event in handle_chat_stream(
        handle_chat_fn=handle_chat_fn,
        build_answer_prompts_fn=MagicMock(),
        handle_run_skill_stream_fn=AsyncMock(),
        skill_id="EC-投放-01",
        session_id="wb-test123",
        message="调高 ROI",
        context={"active_module": "params"},
        intent="tune_threshold",
        reference_ids=[],
        user_id="admin",
    ):
        events.append(event)

    assert len(events) == 2
    assert events[0]["type"] == "patch"
    assert events[1]["type"] == "done"


@pytest.mark.asyncio
async def test_handle_chat_stream_patch_error():
    """patch handler 异常时流式输出应产出 error 事件"""
    from app.workbench.assistant_service import handle_chat_stream

    handle_chat_fn = AsyncMock(side_effect=RuntimeError("fail"))

    events = []
    async for event in handle_chat_stream(
        handle_chat_fn=handle_chat_fn,
        build_answer_prompts_fn=MagicMock(),
        handle_run_skill_stream_fn=AsyncMock(),
        skill_id="EC-投放-01",
        session_id="wb-test123",
        message="添加规则",
        context={},
        intent="add_rule",
        reference_ids=[],
        user_id="admin",
    ):
        events.append(event)

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["code"] == "PATCH_FAILED"


@pytest.mark.asyncio
async def test_handle_chat_stream_answer_path():
    """question intent 的流式输出应透传 LLM 事件"""
    from app.workbench.assistant_service import handle_chat_stream

    # 模拟 call_llm_stream 产出的事件序列
    async def fake_llm_stream(**kwargs):
        yield {"type": "delta", "content": "答案"}
        yield {"type": "done", "finish_reason": "stop"}

    build_prompts = MagicMock(return_value=("system prompt", "user msg", "hash123"))

    events = []
    with patch("app.workbench.assistant_service.call_llm_stream", side_effect=fake_llm_stream):
        async for event in handle_chat_stream(
            handle_chat_fn=AsyncMock(),
            build_answer_prompts_fn=build_prompts,
            handle_run_skill_stream_fn=AsyncMock(),
            skill_id="EC-投放-01",
            session_id="wb-test123",
            message="解释一下",
            context={},
            intent="question",
            reference_ids=[],
            user_id="admin",
        ):
            events.append(event)

    types = [e["type"] for e in events]
    assert "delta" in types
    assert "done" in types


@pytest.mark.asyncio
async def test_handle_chat_stream_build_prompt_error():
    """构建 prompt 失败时流式输出应产出 BUILD_PROMPT_FAILED 错误"""
    from app.workbench.assistant_service import handle_chat_stream

    build_prompts = MagicMock(side_effect=ValueError("prompt 模板损坏"))

    events = []
    async for event in handle_chat_stream(
        handle_chat_fn=AsyncMock(),
        build_answer_prompts_fn=build_prompts,
        handle_run_skill_stream_fn=AsyncMock(),
        skill_id="EC-投放-01",
        session_id="wb-test123",
        message="测试",
        context={},
        intent="question",
        reference_ids=[],
        user_id="admin",
    ):
        events.append(event)

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["code"] == "BUILD_PROMPT_FAILED"


@pytest.mark.asyncio
async def test_handle_chat_stream_run_skill():
    """run_skill intent 应委托给 handle_run_skill_stream_fn"""
    from app.workbench.assistant_service import handle_chat_stream

    async def fake_run_stream(**kwargs):
        yield {"type": "execution_start", "skill_id": kwargs["skill_id"]}
        yield {"type": "done", "output": {"result": "ok"}}

    events = []
    async for event in handle_chat_stream(
        handle_chat_fn=AsyncMock(),
        build_answer_prompts_fn=MagicMock(),
        handle_run_skill_stream_fn=fake_run_stream,
        skill_id="EC-投放-01",
        session_id="wb-test123",
        message="跑一下",
        context={},
        intent="run_skill",
        reference_ids=[],
        user_id="admin",
    ):
        events.append(event)

    assert len(events) == 2
    assert events[0]["type"] == "execution_start"
    assert events[1]["type"] == "done"
