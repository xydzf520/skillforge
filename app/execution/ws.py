"""执行进度 WebSocket 端点：编辑器内沙箱执行，实时推送进度到前端"""

import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from app.common.ws_auth import (
    WS_CODE_AUTH_REQUIRED,
    WS_CODE_OVERFLOW,
    WS_CODE_PERMISSION_DENIED,
    get_current_user_ws,
)
from app.common.ws_session import (
    spawn_json_heartbeat,
    spawn_ping_pong_listener,
    user_ws_session_limiter,
)

ws_router = APIRouter()


async def _receive_execute_params(websocket: WebSocket) -> dict:
    """首帧接收执行参数，兼容 ping/pong 与旧版裸 params JSON。"""
    deadline = asyncio.get_running_loop().time() + 10
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        msg = await asyncio.wait_for(websocket.receive_json(), timeout=remaining)
        if not isinstance(msg, dict):
            raise ValueError("invalid payload")

        msg_type = str(msg.get("type") or "").lower()
        if msg_type == "ping":
            await websocket.send_json({"type": "pong"})
            continue
        if msg_type == "params":
            params = msg.get("params")
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            return params
        if not msg_type:
            return msg
        raise ValueError(f"unsupported message type: {msg_type}")


@ws_router.websocket("/ws/execute")
async def ws_execute(
    websocket: WebSocket,
    skill_id: str = Query(...),
):
    """
    WebSocket 执行端点：
    1. 验证认证 + 角色（admin/ai_engineer）
    2. 客户端连接后发送 params JSON
    3. 服务端启动沙箱执行
    4. 执行完成后推送 result 事件并关闭
    """
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.skills.core.service_shared import ensure_skill_access

    await websocket.accept()

    user = await get_current_user_ws(websocket)
    if user is None:
        await websocket.close(code=WS_CODE_AUTH_REQUIRED, reason="unauthorized")
        return

    # 角色检查：与 HTTP 执行接口一致（走兼容映射，system_admin 等同 admin）
    from app.auth.access import role_matches_any
    if not role_matches_any(user, ("admin", "ai_engineer")):
        await websocket.close(code=WS_CODE_PERMISSION_DENIED, reason="permission denied")
        return

    try:
        async with async_session_factory() as session:
            await ensure_skill_access(session, skill_id, user, "execute")
    except AppError:
        await websocket.close(code=WS_CODE_PERMISSION_DENIED, reason="无权执行该 Skill")
        return

    lease = await user_ws_session_limiter.acquire("ws-execute", user.id)
    if not lease.acquired:
        await websocket.send_json({
            "type": "error",
            "message": f"该账号已有 {lease.count} 个执行流正在运行，请先关闭旧连接",
        })
        await websocket.close(code=WS_CODE_OVERFLOW, reason="too many execute streams")
        return

    heartbeat_task = None
    control_task = None
    try:
        # 等待客户端发送参数
        params = await _receive_execute_params(websocket)

        await websocket.send_json({"type": "started", "skill_id": skill_id})
        heartbeat_task = spawn_json_heartbeat(websocket, lease=lease)
        control_task = spawn_ping_pong_listener(websocket)

        # 执行沙箱
        from app.execution.execution_service import execution_service
        result = await execution_service.execute_skill(
            skill_id=skill_id,
            params=params,
            sandbox=True,
            triggered_by=f"editor:{user.id}",
        )

        await websocket.send_json({
            "type": "result",
            "data": result,
        })

    except WebSocketDisconnect:
        logger.info(f"编辑器执行 WebSocket 断开: skill={skill_id}")
    except asyncio.TimeoutError:
        try:
            await websocket.send_json({"type": "error", "message": "参数接收超时"})
        except Exception as send_err:
            logger.debug(f"执行 WS 超时错误回发失败 skill={skill_id}: {send_err}")
    except ValueError as e:
        logger.info(f"编辑器执行参数非法: skill={skill_id} err={e}")
        try:
            await websocket.send_json({"type": "error", "message": "参数格式非法"})
        except Exception as send_err:
            logger.debug(f"执行 WS 参数错误回发失败 skill={skill_id}: {send_err}")
    except Exception as e:
        logger.error(f"编辑器执行异常: skill={skill_id} error={e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception as send_err:
            logger.debug(f"执行 WS 错误回发失败 skill={skill_id}: {send_err}")
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        if control_task:
            control_task.cancel()
        if heartbeat_task:
            try:
                await heartbeat_task
            except BaseException:
                pass
        if control_task:
            try:
                await control_task
            except BaseException:
                pass
        await lease.release()
        try:
            await websocket.close()
        except Exception as close_err:
            logger.debug(f"执行 WS close 失败 skill={skill_id}: {close_err}")
