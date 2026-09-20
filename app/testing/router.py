"""Skill测试API路由：测试用例执行 + Agent对话 + 历史回放"""

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.audit import audit
from app.common.ws_auth import (
    WS_CODE_AUTH_REQUIRED,
    WS_CODE_INTERNAL_ERROR,
    get_current_user_ws,
)
from app.testing.agent_chat import agent_chat_service
from app.testing.replay import replay_service
from app.testing.test_runner import run_test_cases

router = APIRouter()


# ===== 测试用例 =====

class RunTestRequest(BaseModel):
    test_case_ids: list[int] | None = None
    run_all: bool = False


@router.post("/{skill_id}/test")
async def test_skill(
    skill_id: str,
    body: RunTestRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """跑Skill测试用例"""
    case_ids = None if body.run_all else body.test_case_ids
    result = await run_test_cases(skill_id, case_ids)
    try:
        await audit.log(
            current_user.id,
            "testing.run",
            "skill",
            skill_id,
            detail={
                "total": result.get("total", 0),
                "passed": result.get("passed", 0),
                "failed": result.get("failed", 0),
                "run_all": body.run_all,
                "case_ids": case_ids,
                "error": result.get("error"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("测试运行审计写入失败 skill={} err={}", skill_id, exc)
    return result


# ===== Agent对话 =====

class StartChatRequest(BaseModel):
    """开始对话请求（无需额外参数，skill_id从路径获取）"""
    pass


class SendMessageRequest(BaseModel):
    """发送消息请求"""
    message: str


@router.post("/{skill_id}/chat/start")
async def start_chat(
    skill_id: str,
    current_user: User = Depends(require_state_active),
):
    """创建新的Agent对话会话"""
    return await agent_chat_service.start_conversation(
        skill_id=skill_id,
        user_id=current_user.id,
    )


@router.post("/chat/{conversation_id}/message")
async def send_chat_message(
    conversation_id: str,
    body: SendMessageRequest,
    current_user: User = Depends(require_state_active),
):
    """向对话发送消息并获取AI回复"""
    return await agent_chat_service.send_message(
        conversation_id=conversation_id,
        user_message=body.message,
    )


@router.get("/chat/{conversation_id}")
async def get_chat_conversation(
    conversation_id: str,
    current_user: User = Depends(require_state_active),
):
    """获取对话完整历史"""
    return await agent_chat_service.get_conversation(conversation_id)


@router.get("/{skill_id}/chat/list")
async def list_chat_conversations(
    skill_id: str,
    current_user: User = Depends(require_state_active),
):
    """列出某Skill的所有对话会话（当前用户的）"""
    return await agent_chat_service.list_conversations(
        skill_id=skill_id,
        user_id=current_user.id,
    )


@router.delete("/chat/{conversation_id}")
async def delete_chat_conversation(
    conversation_id: str,
    current_user: User = Depends(require_state_active),
):
    """删除Agent对话"""
    return await agent_chat_service.delete_conversation(
        conversation_id=conversation_id,
        user_id=current_user.id,
    )


# ===== F2: 流式对话 WebSocket 端点 =====

@router.websocket("/chat/{conversation_id}/stream")
async def stream_chat_conversation(
    websocket: WebSocket,
    conversation_id: str,
):
    """
    Agent 对话流式 WebSocket。

    协议：
        客户端 → {"type": "user_message", "content": "..."}
        服务端 → {"type": "compact", "original_count": N}    (可选，触发了压缩)
        服务端 → {"type": "delta", "content": "片段"}        (多次)
        服务端 → {"type": "done", "usage": {...}, "total_tokens": X}
        服务端 → {"type": "error", "code": "...", "error": "..."}

        客户端 → {"type": "stop"}     (中断生成)
        客户端 → {"type": "ping"}     → 服务端 {"type": "pong"}

    认证：从 cookie 中提取 session token，未登录直接 close(4401)。
    """
    await websocket.accept()

    # 认证
    user = await get_current_user_ws(websocket)
    if not user:
        await websocket.close(code=WS_CODE_AUTH_REQUIRED, reason="unauthorized")
        return

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type == "stop":
                # 当前实现：stop 只是个提示，前端会自己停止渲染
                # 后端流如果还在跑也会很快结束（下一个 await）
                # 后续可改为：取消生成器、发 cancel 给上游 LLM
                continue

            if msg_type != "user_message":
                await websocket.send_json({
                    "type": "error",
                    "code": "INVALID_MESSAGE_TYPE",
                    "error": f"未知消息类型: {msg_type}",
                })
                continue

            content = (msg.get("content") or "").strip()
            if not content:
                await websocket.send_json({
                    "type": "error",
                    "code": "EMPTY_MESSAGE",
                    "error": "消息内容不能为空",
                })
                continue

            # 流式发送
            try:
                async for event in agent_chat_service.send_message_stream(
                    conversation_id=conversation_id,
                    user_message=content,
                ):
                    await websocket.send_json(event)
                    if event.get("type") == "error":
                        # error 已经发送，让客户端决定是否继续
                        break
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.exception(f"流式对话异常 conv={conversation_id}: {e}")
                try:
                    await websocket.send_json({
                        "type": "error",
                        "code": "INTERNAL_ERROR",
                        "error": str(e)[:300],
                    })
                except Exception as send_err:
                    logger.debug(f"testing 流式对话错误回发失败 conv={conversation_id}: {send_err}")

    except WebSocketDisconnect:
        logger.info(f"流式对话 WS 断开 conv={conversation_id}")
    except Exception as e:
        logger.exception(f"流式对话 WS 致命异常 conv={conversation_id}: {e}")
        try:
            await websocket.close(code=WS_CODE_INTERNAL_ERROR, reason=str(e)[:100])
        except Exception as close_err:
            logger.debug(f"testing 流式对话 WS close 失败 conv={conversation_id}: {close_err}")


# ===== 测试用例流式执行 WebSocket =====

@router.websocket("/{skill_id}/test/stream")
async def stream_test_execution(
    websocket: WebSocket,
    skill_id: str,
):
    """
    测试用例流式执行 WebSocket。

    协议：
        客户端 → {"type": "run", "case_ids": [1,2] | null, "run_all": true}
        服务端 → {"type": "case_start", "case_id": N, "name": "..."}
        服务端 → {"type": "case_result", "case_id": N, "passed": bool, "result": {...}}
        服务端 → {"type": "summary", "total": N, "passed": N, "failed": N}
        服务端 → {"type": "error", "error": "..."}
    """
    await websocket.accept()

    user = await get_current_user_ws(websocket)
    if not user:
        await websocket.close(code=WS_CODE_AUTH_REQUIRED, reason="unauthorized")
        return

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type != "run":
                await websocket.send_json({"type": "error", "error": f"未知消息类型: {msg_type}"})
                continue

            case_ids = None if msg.get("run_all") else msg.get("case_ids")
            try:
                async for event in _stream_test_cases(skill_id, case_ids):
                    await websocket.send_json(event)
            except Exception as e:
                logger.exception(f"流式测试异常 skill={skill_id}: {e}")
                await websocket.send_json({"type": "error", "error": str(e)[:300]})

    except WebSocketDisconnect:
        logger.info(f"测试流式 WS 断开 skill={skill_id}")
    except Exception as e:
        logger.exception(f"测试流式 WS 致命异常 skill={skill_id}: {e}")
        try:
            await websocket.close(code=WS_CODE_INTERNAL_ERROR, reason=str(e)[:100])
        except Exception as close_err:
            logger.debug(f"测试流式 WS close 失败 skill={skill_id}: {close_err}")


async def _stream_test_cases(skill_id: str, case_ids: list[int] | None = None):
    """逐用例流式产生测试事件。"""
    import yaml, time
    from app.skills.core.git_service import git_service
    from app.execution.openclaw_client import default_client
    from app.testing.test_runner import _check_assertions

    test_yaml = git_service.read_file(skill_id, "tests/test_cases.yaml")
    if not test_yaml:
        yield {"type": "error", "error": "未找到 tests/test_cases.yaml"}
        return

    try:
        data = yaml.safe_load(test_yaml)
        cases = data.get("test_cases", [])
    except yaml.YAMLError as e:
        yield {"type": "error", "error": f"YAML解析失败: {e}"}
        return

    if case_ids:
        cases = [c for i, c in enumerate(cases) if (i + 1) in case_ids]

    passed = 0
    total = len(cases)

    for i, case in enumerate(cases):
        case_name = case.get("name", f"用例{i + 1}")
        case_id = i + 1
        input_data = case.get("input", {})
        expected = case.get("expected_output", {})
        assert_rules = case.get("assert_rules", [])

        yield {"type": "case_start", "case_id": case_id, "name": case_name, "total": total}

        start_time = time.time()
        try:
            output = await default_client.run_skill(skill_id, params=input_data, sandbox=True)
            duration_ms = int((time.time() - start_time) * 1000)
            case_passed, failures = _check_assertions(output, expected, assert_rules)
            if case_passed:
                passed += 1
            yield {
                "type": "case_result", "case_id": case_id, "name": case_name,
                "passed": case_passed, "input": input_data, "expected": expected,
                "actual": output, "failures": failures, "duration_ms": duration_ms,
            }
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            yield {
                "type": "case_result", "case_id": case_id, "name": case_name,
                "passed": False, "failures": [f"执行异常: {e}"], "duration_ms": duration_ms,
            }

    yield {"type": "summary", "total": total, "passed": passed, "failed": total - passed}


# ===== 历史回放 =====

class ReplayRequest(BaseModel):
    """历史回放请求"""
    date_from: str  # "2026-03-25"
    date_to: str    # "2026-04-01"
    new_params: dict | None = None


@router.post("/{skill_id}/replay")
async def replay_skill(
    skill_id: str,
    body: ReplayRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """历史回放：用新参数重跑历史决策，对比差异"""
    return await replay_service.run_replay(
        skill_id=skill_id,
        date_from=body.date_from,
        date_to=body.date_to,
        new_params=body.new_params,
    )
