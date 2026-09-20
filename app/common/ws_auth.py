"""
WebSocket 认证辅助。

FastAPI 的 Depends 在 WebSocket 路由上工作有限（不能用 HTTPException），
本模块提供从 WebSocket headers 中提取 session cookie 并验证用户的工具函数。

用法：
    @router.websocket("/foo")
    async def foo(websocket: WebSocket):
        user = await get_current_user_ws(websocket)
        if not user:
            await websocket.close(code=4401, reason="未登录")
            return
        # ... 业务逻辑
"""

from fastapi import WebSocket
from sqlalchemy import select

from app import database as _db_mod
from app.auth.dependencies import SESSION_COOKIE, verify_session_token
from app.auth.models import User


async def get_current_user_ws(websocket: WebSocket) -> User | None:
    """
    从 WebSocket 的 cookie 头中提取并验证 session token，返回 User 或 None。

    与 HTTP 的 get_current_user 等价，但不抛 HTTPException
    （WebSocket 应该用 close(code) 而非异常处理）。

    Returns:
        User: 验证通过的用户对象
        None: 未登录、token 失效、用户不存在或被禁用
    """
    token = websocket.cookies.get(SESSION_COOKIE)
    if not token:
        return None

    result_tuple = verify_session_token(token)
    if not result_tuple:
        return None
    user_id, token_rev = result_tuple

    async with _db_mod.async_session_factory() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if not user or not user.is_active:
        return None
    # v2: disabled / rev 不匹配均视同未登录
    if getattr(user, "state", None) == "disabled":
        return None
    user_rev = int(getattr(user, "permissions_rev", 0) or 0)
    if token_rev != user_rev:
        return None

    return user


# WebSocket 关闭码（参考 RFC 6455 + 自定义 4xxx 区间）
WS_CODE_AUTH_REQUIRED = 4401
WS_CODE_PERMISSION_DENIED = 4403
WS_CODE_NOT_FOUND = 4404
WS_CODE_INTERNAL_ERROR = 4500
WS_CODE_OVERFLOW = 4413
WS_CODE_CIRCUIT_BREAKER = 4503
