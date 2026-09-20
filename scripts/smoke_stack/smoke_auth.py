from __future__ import annotations

import os

import requests

from smoke_stack import smoke_env


def ensure_smoke_admin() -> None:
    os.environ.update(smoke_env.smoke_env())

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.auth.models import User
    from app.auth.service import hash_password

    engine = create_engine(smoke_env.SYNC_DB_URL)
    try:
        with Session(engine) as session:
            user = session.execute(select(User).where(User.username == smoke_env.SMOKE_ADMIN_USERNAME)).scalar_one_or_none()
            if user:
                user.password_hash = hash_password(smoke_env.SMOKE_ADMIN_PASSWORD)
                user.name = smoke_env.SMOKE_ADMIN_NAME
                user.role = "admin"
                user.can_view_all = True
                user.is_active = True
                user.must_change_password = False
                session.commit()
                smoke_env.ok(f"已更新 smoke admin: {smoke_env.SMOKE_ADMIN_USERNAME}")
                return

            session.add(User(
                id="smoke-admin",
                username=smoke_env.SMOKE_ADMIN_USERNAME,
                password_hash=hash_password(smoke_env.SMOKE_ADMIN_PASSWORD),
                name=smoke_env.SMOKE_ADMIN_NAME,
                role="admin",
                can_view_all=True,
                department="AI",
                is_active=True,
                must_change_password=False,
            ))
            session.commit()
            smoke_env.ok(f"已创建 smoke admin: {smoke_env.SMOKE_ADMIN_USERNAME}")
    finally:
        engine.dispose()


def login() -> requests.Session:
    session = requests.Session()
    resp = session.post(
        f"{smoke_env.BASE_URL}/api/auth/login",
        json={"username": smoke_env.SMOKE_ADMIN_USERNAME, "password": smoke_env.SMOKE_ADMIN_PASSWORD},
        timeout=10,
    )
    if resp.status_code != 200:
        ensure_smoke_admin()
        resp = session.post(
            f"{smoke_env.BASE_URL}/api/auth/login",
            json={"username": smoke_env.SMOKE_ADMIN_USERNAME, "password": smoke_env.SMOKE_ADMIN_PASSWORD},
            timeout=10,
        )
    if resp.status_code != 200:
        smoke_env.fail(f"登录失败: {resp.status_code} {resp.text[:200]}")
    smoke_env.ok("登录成功")
    return session


def verify_session(session: requests.Session) -> None:
    resp = session.get(f"{smoke_env.BASE_URL}/api/auth/me", timeout=10)
    if resp.status_code != 200:
        smoke_env.fail(f"/api/auth/me 失败: {resp.status_code}")
    body = resp.json()
    if body.get("username") != smoke_env.SMOKE_ADMIN_USERNAME:
        smoke_env.fail(f"/api/auth/me 返回用户异常: {body}")
    smoke_env.ok("/api/auth/me 返回当前 smoke 用户")
