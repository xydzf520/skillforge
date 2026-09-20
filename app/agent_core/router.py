"""FastAPI endpoints for the v7 LangGraph runtime."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, WebSocket
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_core.graph import get_publish_graph, get_save_graph
from app.agent_core.progress_translator import translate_event
from app.agent_core.runtime import resume_skill_graph, run_skill_graph
from app.auth.dependencies import get_current_user_ws, require_state_active
from app.auth.models import User
from app.common.exceptions import AppError
from app.database import async_session_factory, get_db


router = APIRouter()


class AgentRunRequest(BaseModel):
    # 与 WorkbenchTaskContractRequest 同源（用户在 Blueprint 输入框粘贴 SOP / API 文档）
    message: str = Field(..., min_length=1, max_length=10000)
    mode: str = "publish"
    thread_id: str | None = None


class AgentResumeRequest(BaseModel):
    thread_id: str
    mode: str = "publish"
    resume_value: dict | None = None


def _user_thread_prefix(user: User) -> str:
    """生成 thread_id 用户前缀，用于校验 resume 时的归属。"""
    return f"u{user.id}-"


def _ensure_thread_owner(thread_id: str, user: User) -> None:
    """确保 thread_id 归属当前用户，否则抛 403。"""
    if not thread_id.startswith(_user_thread_prefix(user)):
        raise AppError("AGENT_THREAD_FORBIDDEN", 403)


@router.post("/run")
async def run_agent_flow(
    body: AgentRunRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    # thread_id 必须绑定当前 user，防止跨用户复用 graph 状态
    thread_id = body.thread_id or f"{_user_thread_prefix(current_user)}{uuid4().hex[:8]}"
    _ensure_thread_owner(thread_id, current_user)
    state = await run_skill_graph(body.message, mode=body.mode, thread_id=thread_id)
    try:
        from app.learning.service import capture_agent_thread

        await capture_agent_thread(
            db,
            user=current_user,
            thread_id=thread_id,
            message=body.message,
            mode=body.mode,
            state=state if isinstance(state, dict) else {"value": state},
            status="completed",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("learning capture agent run failed thread={} err={}", thread_id, exc)
    return state


@router.post("/resume")
async def resume_agent_flow(
    body: AgentResumeRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    _ensure_thread_owner(body.thread_id, current_user)
    state = await resume_skill_graph(body.thread_id, mode=body.mode, resume_value=body.resume_value)
    try:
        from app.learning.service import capture_agent_thread

        await capture_agent_thread(
            db,
            user=current_user,
            thread_id=body.thread_id,
            message=str(body.resume_value or ""),
            mode=body.mode,
            state=state if isinstance(state, dict) else {"value": state},
            status="completed",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("learning capture agent resume failed thread={} err={}", body.thread_id, exc)
    return state


@router.websocket("/stream")
async def stream_agent_flow(websocket: WebSocket):
    """流式跑 graph，推送 progress / node_result / interrupt / done 事件给前端。"""
    await websocket.accept()
    # P0-3: WS 必须鉴权，未登录立即关闭
    current_user = await get_current_user_ws(websocket)
    if current_user is None:
        return

    try:
        payload = await websocket.receive_json()
    except Exception:
        await websocket.close(code=1003, reason="invalid payload")
        return

    message = payload.get("message", "")
    mode = payload.get("mode", "publish")
    requested_thread_id = payload.get("thread_id")
    if requested_thread_id:
        # P0-3 部门/用户隔离：thread_id 必须以当前用户前缀开头，禁止跨用户 resume
        if not requested_thread_id.startswith(_user_thread_prefix(current_user)):
            await websocket.close(code=4403, reason="AGENT_THREAD_FORBIDDEN")
            return
        thread_id = requested_thread_id
    else:
        thread_id = f"{_user_thread_prefix(current_user)}{uuid4().hex[:8]}"

    graph = await (get_save_graph() if mode == "save" else get_publish_graph())
    config = {"configurable": {"thread_id": thread_id}}

    await websocket.send_json({"type": "thread_id", "thread_id": thread_id})

    try:
        # astream 输出每个节点完成后的 state diff
        async for chunk in graph.astream({"user_input": message}, config=config):
            for node_name, node_state in chunk.items():
                if node_name == "__interrupt__":
                    await websocket.send_json({
                        "type": "interrupt",
                        "interrupt_data": node_state,
                        "message": "等你确认后继续",
                    })
                    return  # 等前端调 /resume
                await websocket.send_json({
                    "type": "progress",
                    "node": node_name,
                    "message": translate_event(node_name),
                })
                await websocket.send_json({
                    "type": "node_result",
                    "node": node_name,
                    "data": node_state,
                })

        # 拿最终 state
        final_state = await graph.aget_state(config)
        final_values = final_state.values if final_state else {}
        try:
            from app.learning.service import capture_agent_thread

            async with async_session_factory() as session:
                await capture_agent_thread(
                    session,
                    user=current_user,
                    thread_id=thread_id,
                    message=message,
                    mode=mode,
                    state=final_values if isinstance(final_values, dict) else {"value": final_values},
                    status="completed",
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("learning capture agent stream failed thread={} err={}", thread_id, exc)
        await websocket.send_json({
            "type": "done",
            "state": final_values,
        })
    except Exception as exc:  # noqa: BLE001
        # [M2] 双层脱敏:
        #   - 主日志: 只记 error_id + user + thread + 异常类名, 不带堆栈
        #   - 敏感 channel: 带堆栈, 通过 logger.bind(channel="sensitive") 路由到独立 sink
        #     (运维可配置 sensitive sink → /var/log/skillforge/sensitive.log + chmod 600,
        #     或路由到 audit DB)
        # - 响应: 仅 error_code + error_id, 让用户上报时可追溯
        import uuid
        error_id = uuid.uuid4().hex[:12]
        exc_type = type(exc).__name__
        logger.error(
            "agent_core stream failed: error_id={} user={} thread={} type={}",
            error_id, current_user.id, thread_id, exc_type,
        )
        # 完整堆栈走 sensitive channel — 默认仍写 stdout, 但 channel 标签让 ops 能配置过滤
        logger.bind(channel="sensitive").exception(
            "agent_core stream stack trace: error_id={}", error_id,
        )
        try:
            from app.learning.service import capture_agent_thread

            async with async_session_factory() as session:
                await capture_agent_thread(
                    session,
                    user=current_user,
                    thread_id=thread_id,
                    message=message,
                    mode=mode,
                    state={"error_id": error_id, "error_type": exc_type},
                    status="failed",
                )
                await session.commit()
        except Exception as capture_exc:  # noqa: BLE001
            logger.debug("learning capture agent error failed thread={} err={}", thread_id, capture_exc)
        await websocket.send_json({
            "type": "error",
            "error_code": "AGENT_EXECUTION_FAILED",
            "error_id": error_id,
            "message": "graph 执行失败，请稍后重试",
        })
    finally:
        try:
            await websocket.close()
        except Exception as e:
            logger.debug("agent_core WS close 失败(可能已断): {}", e)
