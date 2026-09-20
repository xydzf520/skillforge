"""认证API路由：登录/登出/获取当前用户/钉钉OAuth"""

import secrets
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import (
    get_accessible_departments,
    get_managed_departments,
    get_primary_department_id,
    get_user_state,
)
from app.auth.dependencies import (
    SESSION_COOKIE,
    create_session_token,
    get_current_user,
)
from app.auth.models import User
from app.auth.service import authenticate, change_password, verify_password
from app.common.exceptions import AppError
from app.config import settings
from app.database import get_db

router = APIRouter()
DINGTALK_NEXT_COOKIE = "skillforge_dingtalk_next"


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class UserInfo(BaseModel):
    user_id: str
    username: str
    name: str
    role: str
    department: str | None
    department_id: str | None = None
    state: str = "active"
    permissions_rev: int = 0
    managed_departments: list[str] = []
    accessible_departments: list[str] = []
    can_view_all: bool
    must_change_password: bool
    avatar_url: str | None = None
    email: str | None = None
    phone: str | None = None


def _safe_next_path(value: str | None) -> str:
    text = str(value or "").strip()
    if text.startswith("/") and not text.startswith("//"):
        return text
    return "/"


def _encode_next_cookie(value: str) -> str:
    return quote(_safe_next_path(value), safe="")


def _decode_next_cookie(value: str | None) -> str:
    return _safe_next_path(unquote(str(value or "")))


async def _build_user_info(db: AsyncSession, user: User) -> "UserInfo":
    """构建含 v2 字段的 UserInfo（state / permissions_rev / managed / accessible 部门集）。"""
    department_id = await get_primary_department_id(db, user)
    managed = sorted(await get_managed_departments(db, user))
    accessible = await get_accessible_departments(db, user)
    return UserInfo(
        user_id=user.id,
        username=user.username,
        name=user.name,
        role=user.role,
        department=user.department,
        department_id=department_id,
        state=get_user_state(user),
        permissions_rev=int(getattr(user, "permissions_rev", 0) or 0),
        managed_departments=managed,
        accessible_departments=[] if accessible is None else sorted(accessible),
        can_view_all=user.can_view_all,
        must_change_password=user.must_change_password,
        avatar_url=user.avatar_url,
        email=user.email,
        phone=user.phone,
    )


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """账号密码登录"""
    user = await authenticate(
        db,
        body.username,
        body.password,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # 设置session cookie（v2: token 携带 permissions_rev 快照，权限变更后旧 token 立即失效）
    token = create_session_token(user.id, getattr(user, "permissions_rev", 0))
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.SESSION_MAX_AGE,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
    )

    return await _build_user_info(db, user)


@router.post("/logout")
async def logout(response: Response):
    """登出，清除session cookie"""
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前登录用户信息"""
    return await _build_user_info(db, current_user)


@router.post("/change-password")
async def change_password_endpoint(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改密码（首次登录强制修改 / 主动修改）"""
    # 验证旧密码
    if not current_user.password_hash or not verify_password(body.old_password, current_user.password_hash):
        raise AppError("AUTH_INVALID_CREDENTIALS", 401)

    # 新密码不能与旧密码相同
    if body.old_password == body.new_password:
        raise AppError("AUTH_SAME_PASSWORD", 400)

    await change_password(db, current_user, body.new_password)
    await db.commit()

    return {"ok": True}


# ===== 登录页用：公开的提供方能力探测 =====

@router.get("/providers")
async def login_providers():
    """返回登录页可用的第三方登录方式，便于前端按需展示。"""
    return {
        "dingtalk": bool(settings.DINGTALK_APP_KEY and settings.DINGTALK_APP_SECRET),
    }


# ===== 钉钉OAuth =====

@router.get("/dingtalk/login")
async def dingtalk_login(next: str | None = Query(None), redirect: str | None = Query(None)):
    """生成钉钉扫码登录URL并重定向（带state防CSRF）"""
    from app.auth.dingtalk_oauth import DINGTALK_STATE_COOKIE, get_dingtalk_login_url

    url, state = get_dingtalk_login_url()
    next_path = _safe_next_path(next or redirect)
    redirect = RedirectResponse(url)
    redirect.set_cookie(
        key=DINGTALK_STATE_COOKIE,
        value=state,
        max_age=600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
    )
    redirect.set_cookie(
        key=DINGTALK_NEXT_COOKIE,
        value=_encode_next_cookie(next_path),
        max_age=600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
    )
    return redirect


@router.get("/dingtalk/callback")
async def dingtalk_callback(
    code: str = Query(..., alias="authCode"),
    state: str = Query(...),
    request: Request = None,
    response: Response = None,
    db: AsyncSession = Depends(get_db),
):
    """钉钉扫码回调：校验state + authCode换用户信息 → 创建session → 跳转首页"""
    from app.auth.dingtalk_oauth import DINGTALK_STATE_COOKIE, handle_dingtalk_callback, verify_state

    expected_state = request.cookies.get(DINGTALK_STATE_COOKIE, "") if request else ""
    if not expected_state or not secrets.compare_digest(expected_state, state):
        redirect = RedirectResponse("/login?error=invalid_state")
        redirect.delete_cookie(DINGTALK_STATE_COOKIE)
        redirect.delete_cookie(DINGTALK_NEXT_COOKIE)
        return redirect

    if not await verify_state(state):
        redirect = RedirectResponse("/login?error=invalid_state")
        redirect.delete_cookie(DINGTALK_STATE_COOKIE)
        redirect.delete_cookie(DINGTALK_NEXT_COOKIE)
        return redirect

    ip = request.client.host if request and request.client else None
    user = await handle_dingtalk_callback(db, code, ip_address=ip)

    if not user:
        redirect = RedirectResponse("/login?error=dingtalk_failed")
        redirect.delete_cookie(DINGTALK_STATE_COOKIE)
        redirect.delete_cookie(DINGTALK_NEXT_COOKIE)
        return redirect

    token = create_session_token(user.id, getattr(user, "permissions_rev", 0))
    next_path = _decode_next_cookie(request.cookies.get(DINGTALK_NEXT_COOKIE) if request else None)
    redirect = RedirectResponse(next_path, status_code=302)
    redirect.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.SESSION_MAX_AGE,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
    )
    redirect.delete_cookie(DINGTALK_STATE_COOKIE)
    redirect.delete_cookie(DINGTALK_NEXT_COOKIE)
    return redirect
