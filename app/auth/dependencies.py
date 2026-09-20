"""
认证依赖：get_current_user / require_role / require_department_access
所有需要登录的路由加 Depends(get_current_user)。
"""

from fastapi import Depends, Request, WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.exceptions import AppError
from app.config import settings
from app.database import async_session_factory, get_db

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="session")

SESSION_COOKIE = "skillforge_session"


def create_session_token(user_id: str, permissions_rev: int = 0) -> str:
    """创建签名 session token。

    v2（docs/spec/role-matrix-v2.md §10.1）：token 同时携带 user_id 与当时的
    permissions_rev 快照；任何 role/state/can_view_all/membership 变更都会 bump
    User.permissions_rev，rev 一旦不匹配旧 token 立即失效，实现“改完立即登出”。

    permissions_rev 默认 0，兼容 legacy 调用点渐进迁移；新代码应显式传入
    user.permissions_rev。
    """
    return _signer.dumps({"uid": user_id, "rev": int(permissions_rev or 0)})


def verify_session_token(token: str) -> tuple[str, int] | None:
    """验证 session token，返回 (user_id, permissions_rev) 元组；失败返回 None。

    兼容 pre-v2 token（payload 直接是 user_id 字符串），rev 默认 0，
    该分支仅在切流窗口期保留。
    """
    try:
        payload = _signer.loads(token, max_age=settings.SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if isinstance(payload, dict):
        uid = payload.get("uid")
        if not isinstance(uid, str):
            return None
        rev = int(payload.get("rev") or 0)
        return (uid, rev)
    # legacy: payload 就是 user_id 字符串
    if isinstance(payload, str):
        return (payload, 0)
    return None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """从 cookie 中获取当前登录用户，未登录/失效/禁用/权限改变均抛异常。

    v2（docs/spec/role-matrix-v2.md §10.2）补充：
    - token 携带 (uid, rev) 元组；rev != 用户当前 permissions_rev → 抛
      PERMISSIONS_REV_MISMATCH(401)，强制重新登录；
    - state == "disabled" 或 is_active 为假 → 抛 AUTH_ACCOUNT_DISABLED(403)；
    - state == "pending" 允许通过，由业务层/路由层决定是否跳 /pending。
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AppError("AUTH_REQUIRED", 401)

    result_tuple = verify_session_token(token)
    if not result_tuple:
        raise AppError("AUTH_SESSION_EXPIRED", 401)
    user_id, token_rev = result_tuple

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise AppError("AUTH_SESSION_EXPIRED", 401)
    # v2: 禁用账号立即拒绝（state 优先；pending 状态 is_active=False 不算禁用）
    # M1：pending 用户钉钉首登 is_active=False 是合法状态，不能在此被 disabled 分支拦截，
    # 否则用户连 /api/auth/me、/pending 页都进不去。state 是真源——只有 disabled 才禁。
    user_state = getattr(user, "state", None)
    if user_state == "disabled":
        raise AppError("AUTH_ACCOUNT_DISABLED", 403)
    # is_active=False 兜底：仅在 state 缺失时才作为 disabled 信号
    if user_state is None and not user.is_active:
        raise AppError("AUTH_ACCOUNT_DISABLED", 403)
    # v2: permissions_rev 比对，不一致说明权限已变更，旧 token 失效
    user_rev = int(getattr(user, "permissions_rev", 0) or 0)
    if token_rev != user_rev:
        raise AppError("PERMISSIONS_REV_MISMATCH", 401)

    return user


def require_role(*allowed_roles: str):
    """路由级角色权限检查依赖，用法：current_user: User = Depends(require_role("admin"))

    v2 兼容：自动通过 ROLE_COMPAT_BY_USER_ROLE 映射匹配老/新角色。
    例如 allowed_roles=("admin",) 时，user.role="system_admin" 也能放行
    （因 v2 `system_admin` 兼容集含 "admin"）。
    """
    from app.auth.access import get_user_state, role_matches_any  # 避免循环 import

    async def _check_role(current_user: User = Depends(get_current_user)) -> User:
        state = get_user_state(current_user)
        if state == "pending":
            raise AppError("AUTH_ACCOUNT_NOT_ACTIVE", 403)
        if state == "disabled" or not current_user.is_active:
            raise AppError("AUTH_ACCOUNT_DISABLED", 403)
        if not role_matches_any(current_user, allowed_roles):
            raise AppError("AUTH_PERMISSION_DENIED", 403)
        return current_user
    return _check_role


async def require_state_active(
    current_user: User = Depends(get_current_user),
) -> User:
    """业务接口默认依赖：pending 用户不能进入业务流程。

    spec role-matrix-v2 §10.2.1：pending 用户只能访问 /api/auth/* 等白名单接口。
    其他需登录的接口都应依赖本函数而不是 get_current_user。

    拒绝规则：
    - state == "pending" → 403 AUTH_ACCOUNT_NOT_ACTIVE（未激活）
    - state == "disabled" 或 is_active=False → 403 AUTH_ACCOUNT_DISABLED（被禁用）
    - 其他（active）→ 通过

    注意：get_current_user 已经拦截了 disabled，本函数额外拦截 pending。
    兜底保留 disabled 判定以防 get_current_user 行为调整。
    """
    state = getattr(current_user, "state", None) or ("active" if current_user.is_active else "disabled")
    if state == "pending":
        raise AppError("AUTH_ACCOUNT_NOT_ACTIVE", 403)
    if state == "disabled" or not current_user.is_active:
        raise AppError("AUTH_ACCOUNT_DISABLED", 403)
    return current_user


async def require_state_active_web_or_cli(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Accept a normal web session or a Codex CLI bearer token.

    Control-plane APIs used by `sf` need to share the same active-user checks as
    web APIs while allowing the CLI session token issued by SkillForge.
    """
    try:
        user = await get_current_user(request, db)
        return await require_state_active(user)
    except AppError as exc:
        if exc.code not in {"AUTH_REQUIRED", "AUTH_SESSION_EXPIRED", "PERMISSIONS_REV_MISMATCH"}:
            raise
    authorization = str(request.headers.get("authorization") or "").strip()
    if not authorization.lower().startswith("bearer "):
        raise AppError("AUTH_REQUIRED", 401)
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise AppError("AUTH_REQUIRED", 401)

    from app.codex import service as codex_service

    principal = await codex_service.authenticate_cli_session(db, token)
    return principal.user


async def get_current_user_ws(websocket: WebSocket) -> User | None:
    """WebSocket 端点鉴权 helper。

    必须在 `await websocket.accept()` 之后调用。鉴权失败会自行 close 连接并返回 None，
    调用方收到 None 应立即 return。

    成功返回 User 对象（已含 department / role）。

    用法示例：
        @router.websocket("/foo")
        async def foo(ws: WebSocket):
            await ws.accept()
            user = await get_current_user_ws(ws)
            if user is None:
                return
            ...
    """
    token = websocket.cookies.get(SESSION_COOKIE)
    if not token:
        await websocket.close(code=4401, reason="AUTH_REQUIRED")
        return None
    result_tuple = verify_session_token(token)
    if not result_tuple:
        await websocket.close(code=4401, reason="AUTH_SESSION_EXPIRED")
        return None
    user_id, token_rev = result_tuple
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if not user:
        await websocket.close(code=4401, reason="AUTH_SESSION_EXPIRED")
        return None
    # v2: 禁用账号立即拒绝
    if getattr(user, "state", None) == "disabled" or not user.is_active:
        await websocket.close(code=4403, reason="AUTH_ACCOUNT_DISABLED")
        return None
    # v2: permissions_rev 不一致 → 视同 session 失效
    user_rev = int(getattr(user, "permissions_rev", 0) or 0)
    if token_rev != user_rev:
        await websocket.close(code=4401, reason="AUTH_SESSION_EXPIRED")
        return None
    return user


def require_department_access(skill_department: str, user: User) -> bool:
    """同步的部门权限快速检查（仅比较 user.department 展示名，不走 org_tree）。

    真正的 v2 读权判断应走 app.auth.access.can_access_department（async，走 OrgUnit.path
    子树 + ABAC ACL）。这个 sync helper 作为兜底，用于路由层便捷过滤（如 list 接口
    组装 where 条件前快速跳过全局可见用户）。

    v2 角色兼容（docs/spec/role-matrix-v2.md §4.1）：
    - system_admin / admin(legacy) → 全局可见
    - can_view_all=True → 全局可见
    - legacy ai_engineer / director → 全局可见（数据迁移期兼容）
    - 其他角色（dept_admin / aibp / observer / legacy biz_owner / operator）
      → 仅 user.department == skill_department 放行
    """
    # v2: system_admin / admin (legacy) 跨部门；can_view_all 跨部门
    if user.role in ("system_admin", "admin") or bool(getattr(user, "can_view_all", False)):
        return True
    # v2 兼容：legacy ai_engineer / director 保留全局读（数据迁移期）
    if user.role in ("ai_engineer", "director"):
        return True
    # 其他角色（dept_admin / aibp / observer / legacy biz_owner / operator）只能访问自己 department
    return user.department == skill_department
