"""认证服务：登录验证、密码哈希、暴力破解防护"""

from datetime import datetime, timedelta

import bcrypt
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User, LoginLog
from app.common.audit import audit
from app.common.exceptions import AppError
from app.config import settings
from app.common.time_utils import now_bjt


def hash_password(password: str) -> str:
    """bcrypt哈希密码"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(password.encode(), hashed.encode())


def validate_password_strength(password: str) -> None:
    """密码复杂度校验 — 在 change_password / 创建用户前调用。

    要求：长度 ≥ 10，至少包含 3 类字符（小写 / 大写 / 数字 / 符号）。
    不够严苛（没上已泄露库比对）但能挡简单弱密码如 "password" / "12345678"。
    """
    if not password or len(password) < 8:
        raise AppError("PASSWORD_TOO_SHORT", 400, {"detail": "密码至少需要 8 位"})


async def authenticate(
    db: AsyncSession,
    username: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> User:
    """
    账号密码登录验证。
    成功返回User对象，失败抛AppError。
    """
    # 暴力破解防护：同时按用户名和IP检查15分钟内失败次数
    cutoff = now_bjt() - timedelta(minutes=15)

    # 按用户名限速
    user_fail_count = (await db.execute(
        select(func.count()).select_from(LoginLog)
        .where(LoginLog.user_id == username)
        .where(LoginLog.success == False)  # noqa: E712
        .where(LoginLog.created_at >= cutoff)
    )).scalar() or 0

    # 按IP限速（防止用不同用户名暴力破解）
    ip_fail_count = 0
    if ip_address:
        ip_fail_count = (await db.execute(
            select(func.count()).select_from(LoginLog)
            .where(LoginLog.ip_address == ip_address)
            .where(LoginLog.success == False)  # noqa: E712
            .where(LoginLog.created_at >= cutoff)
        )).scalar() or 0

    if user_fail_count >= settings.LOGIN_MAX_ATTEMPTS or ip_fail_count >= settings.LOGIN_MAX_ATTEMPTS * 3:
        await audit.log("unknown", "user.login_blocked",
                        detail={"username": username, "user_attempts": user_fail_count,
                                "ip_attempts": ip_fail_count},
                        ip_address=ip_address)
        raise AppError("AUTH_TOO_MANY_ATTEMPTS", 429)

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    # 用户不存在或密码错误
    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        # 记录登录失败
        log_entry = LoginLog(
            user_id=username,
            login_method="password",
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            failure_reason="wrong_password",
        )
        db.add(log_entry)
        await db.flush()
        await audit.log("unknown", "user.login_failed",
                        detail={"username": username, "reason": "密码错误"},
                        ip_address=ip_address)
        raise AppError("AUTH_INVALID_CREDENTIALS", 401)

    # 账号禁用
    if not user.is_active:
        await audit.log(user.id, "user.login_failed",
                        detail={"reason": "账号禁用"},
                        ip_address=ip_address)
        raise AppError("AUTH_ACCOUNT_DISABLED", 403)

    # 登录成功：更新最后登录时间
    user.last_login_at = now_bjt()
    log_entry = LoginLog(
        user_id=user.id,
        login_method="password",
        ip_address=ip_address,
        user_agent=user_agent,
        success=True,
    )
    db.add(log_entry)
    await db.flush()

    await audit.log(user.id, "user.login", ip_address=ip_address)

    return user


async def change_password(
    db: AsyncSession,
    user: User,
    new_password: str,
) -> None:
    """修改密码 — 同时递增 permissions_rev 使所有旧 session 立即失效，强制重新登录"""
    validate_password_strength(new_password)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.permissions_rev = (int(getattr(user, "permissions_rev", 0) or 0) + 1)
    await db.flush()
    await audit.log(user.id, "user.password_change")
