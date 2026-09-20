"""钉钉扫码用户默认直接激活为 AIBP，手动 pending 流程仍可激活。"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as db_mod
from app.auth.dingtalk_oauth import get_dingtalk_login_url, handle_dingtalk_callback
from app.auth.models import User
from app.config import settings
from app.database import Base
from app.org import service
from app.org.service import create_org_unit
from app.users import service as user_service
from app.users.role_matrix import get_user_state, set_user_state


@pytest_asyncio.fixture
async def db():
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$",
        "/skillforge_test",
        str(settings.DATABASE_URL),
    )
    import asyncpg
    plain_url = test_url.replace("+asyncpg", "")
    _kill = await asyncpg.connect(plain_url)
    try:
        await _kill.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=current_database() AND pid <> pg_backend_pid()"
        )
    finally:
        await _kill.close()

    engine = create_async_engine(test_url, echo=False, pool_size=5, max_overflow=0)

    import app.auth.models  # noqa: F401
    import app.common.audit  # noqa: F401
    import app.org.models  # noqa: F401
    import app.skills.members  # noqa: F401
    import app.skills.models  # noqa: F401
    import app.todos.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    original_engine, original_factory = db_mod.engine, db_mod.async_session_factory
    db_mod.engine, db_mod.async_session_factory = engine, factory

    async with factory() as session:
        yield session

    db_mod.engine, db_mod.async_session_factory = original_engine, original_factory
    await engine.dispose()
    _close = await asyncpg.connect(plain_url)
    try:
        await _close.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=current_database() AND pid <> pg_backend_pid() AND state='idle'"
        )
    finally:
        await _close.close()


@dataclass
class _FakeResponse:
    payload: dict

    def json(self):
        return self.payload


class _FakeHttpxClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        if url.endswith("/oauth2/userAccessToken"):
            return _FakeResponse({"accessToken": "tok-m1"})
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url, headers=None):
        if url.endswith("/contact/users/me"):
            return _FakeResponse(
                {
                    "userId": "ding-m1",
                    "unionId": "union-m1",
                    "nick": "M1 钉钉用户",
                    "avatarUrl": "https://example/m1.png",
                }
            )
        raise AssertionError(f"unexpected GET {url}")


class _FakeOpenIdOnlyHttpxClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        if url.endswith("/oauth2/userAccessToken"):
            return _FakeResponse({"accessToken": "tok-openid"})
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url, headers=None):
        if url.endswith("/contact/users/me"):
            return _FakeResponse(
                {
                    "openId": "openid-only",
                    "unionId": "union-openid",
                    "nick": "OpenId 钉钉用户",
                    "avatarUrl": "https://example/openid.png",
                }
            )
        raise AssertionError(f"unexpected GET {url}")


class _FakeNoUserIdHttpxClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        if url.endswith("/oauth2/userAccessToken"):
            return _FakeResponse({"accessToken": "tok-no-userid"})
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url, headers=None):
        if url.endswith("/contact/users/me"):
            return _FakeResponse(
                {
                    "unionId": "union-no-userid",
                    "nick": "NoUserId 钉钉用户",
                    "avatarUrl": "https://example/no-userid.png",
                }
            )
        raise AssertionError(f"unexpected GET {url}")


async def _corp_member(user_id: str):
    return {
        "ok": True,
        "data": {
            "user_id": user_id,
            "name": "企业钉钉用户",
            "avatar": "https://example/corp.png",
            "dept_id_list": ["dept-1"],
        },
    }


@pytest.mark.asyncio
async def test_dingtalk_login_url_keeps_openid_scope(monkeypatch):
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_LOGIN_REDIRECT", "https://example/callback")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_CORP_ID", "corp-id")

    url, _state = get_dingtalk_login_url()

    assert "scope=openid&" in url
    assert "corpid" not in url


@pytest.mark.asyncio
async def test_dingtalk_first_login_creates_active_aibp(
    monkeypatch, db: AsyncSession
):
    """钉钉首登创建 active AIBP，扫码后可直接进入平台使用能力大厅。"""
    monkeypatch.setattr("app.auth.dingtalk_oauth.httpx.AsyncClient", _FakeHttpxClient)
    monkeypatch.setattr("app.auth.dingtalk_oauth.dingtalk_client.get_user_detail", _corp_member)
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_SECRET", "appsecret")

    user = await handle_dingtalk_callback(db, "auth-code")
    await db.commit()

    assert user is not None
    assert user.role == "aibp"
    assert await get_user_state(db, user) == "active"
    assert user.is_active is True


@pytest.mark.asyncio
async def test_dingtalk_login_upgrades_legacy_default_user_to_active_aibp(
    monkeypatch, db: AsyncSession
):
    monkeypatch.setattr("app.auth.dingtalk_oauth.httpx.AsyncClient", _FakeHttpxClient)
    monkeypatch.setattr("app.auth.dingtalk_oauth.dingtalk_client.get_user_detail", _corp_member)
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_SECRET", "appsecret")
    db.add(
        User(
            id="dt_ding-m1",
            username="dingtalk_ding-m1",
            name="旧钉钉用户",
            role="operator",
            state="active",
            is_active=True,
            dingtalk_user_id="ding-m1",
            must_change_password=False,
            permissions_rev=3,
        )
    )
    await db.commit()

    user = await handle_dingtalk_callback(db, "auth-code")
    await db.commit()

    assert user.role == "aibp"
    assert await get_user_state(db, user) == "active"
    assert user.is_active is True
    assert user.permissions_rev == 4


@pytest.mark.asyncio
async def test_dingtalk_login_keeps_legacy_openid_fallback(
    monkeypatch, db: AsyncSession
):
    monkeypatch.setattr("app.auth.dingtalk_oauth.httpx.AsyncClient", _FakeOpenIdOnlyHttpxClient)
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_SECRET", "appsecret")

    user = await handle_dingtalk_callback(db, "auth-code")
    await db.commit()

    assert user is not None
    assert user.id == "dt_openid-only"
    assert user.username == "dingtalk_openid-only"
    assert user.dingtalk_user_id == "openid-only"
    assert user.dingtalk_union_id == "union-openid"
    assert user.name == "OpenId 钉钉用户"
    assert user.avatar_url == "https://example/openid.png"
    assert user.role == "aibp"
    assert await get_user_state(db, user) == "active"


@pytest.mark.asyncio
async def test_dingtalk_login_reuses_legacy_openid_shadow_user(
    monkeypatch, db: AsyncSession
):
    db.add(
        User(
            id="dt_openid-only",
            username="dingtalk_openid-only",
            name="旧影子用户",
            role="aibp",
            state="active",
            is_active=True,
            dingtalk_user_id="openid-only",
            dingtalk_union_id="union-openid",
            must_change_password=False,
        )
    )
    await db.commit()

    monkeypatch.setattr("app.auth.dingtalk_oauth.httpx.AsyncClient", _FakeOpenIdOnlyHttpxClient)
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_SECRET", "appsecret")

    user = await handle_dingtalk_callback(db, "auth-code")
    await db.commit()

    assert user is not None
    assert user.id == "dt_openid-only"
    assert user.dingtalk_user_id == "openid-only"
    assert user.dingtalk_union_id == "union-openid"
    assert user.name == "旧影子用户"
    assert user.avatar_url == "https://example/openid.png"


@pytest.mark.asyncio
async def test_dingtalk_login_prefers_auth_code_enterprise_userid_when_oauth_returns_openid(
    monkeypatch, db: AsyncSession
):
    async def _auth_code_lookup(auth_code: str):
        assert auth_code == "auth-code"
        return {
            "ok": True,
            "data": {
                "user_id": "ding-enterprise-user",
                "union_id": "union-enterprise-user",
                "name": "企业真实用户",
            },
        }

    async def _corp_member(user_id: str):
        assert user_id == "ding-enterprise-user"
        return {
            "ok": True,
            "data": {
                "user_id": user_id,
                "name": "企业真实用户",
                "dept_id_list": ["dept-auth"],
            },
        }

    await create_org_unit(db, unit_id="dept-auth", name="认证部门")
    monkeypatch.setattr("app.auth.dingtalk_oauth.httpx.AsyncClient", _FakeOpenIdOnlyHttpxClient)
    monkeypatch.setattr("app.auth.dingtalk_oauth.dingtalk_client.get_user_id_by_auth_code", _auth_code_lookup)
    monkeypatch.setattr("app.auth.dingtalk_oauth.dingtalk_client.get_user_detail", _corp_member)
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_SECRET", "appsecret")

    user = await handle_dingtalk_callback(db, "auth-code")
    await db.commit()

    assert user is not None
    assert user.id == "dt_ding-enterprise-user"
    assert user.username == "dingtalk_ding-enterprise-user"
    assert user.dingtalk_user_id == "ding-enterprise-user"
    assert user.dingtalk_union_id == "union-openid"
    assert user.department == "认证部门"


@pytest.mark.asyncio
async def test_dingtalk_login_resolves_union_id_when_auth_code_returns_openid(
    monkeypatch, db: AsyncSession
):
    async def _auth_code_lookup(auth_code: str):
        assert auth_code == "auth-code"
        return {
            "ok": True,
            "data": {
                "user_id": "oCAlBCxOPU5GiihCNiS30iSrQiEiE",
                "name": "千寻-童小琳",
            },
        }

    async def _union_lookup(union_id: str):
        assert union_id == "union-openid"
        return {
            "ok": True,
            "data": {"user_id": "16987446933259877", "union_id": union_id},
        }

    async def _corp_member(user_id: str):
        if user_id == "oCAlBCxOPU5GiihCNiS30iSrQiEiE":
            return {"ok": False, "error": "not_found"}
        assert user_id == "16987446933259877"
        return {
            "ok": True,
            "data": {
                "user_id": user_id,
                "name": "童小琳",
                "dept_id_list": ["dept-ops"],
            },
        }

    await create_org_unit(
        db,
        unit_id="dept-ai",
        name="AI组",
        dingtalk_dept_id="dept-ai",
    )
    await create_org_unit(
        db,
        unit_id="dept-ops",
        name="运营组",
        dingtalk_dept_id="dept-ops",
    )
    canonical = User(
        id="16987446933259877",
        username="16987446933259877",
        name="童小琳",
        role="observer",
        state="active",
        department="AI组",
        dingtalk_user_id="16987446933259877",
        is_active=True,
        must_change_password=False,
    )
    shadow = User(
        id="dt_oCAlBCxOPU5GiihCNiS30iSrQiEiE",
        username="dingtalk_oCAlBCxOPU5GiihCNiS30iSrQiEiE",
        name="千寻-童小琳",
        role="aibp",
        state="active",
        dingtalk_user_id="oCAlBCxOPU5GiihCNiS30iSrQiEiE",
        dingtalk_union_id="union-openid",
        is_active=True,
        must_change_password=False,
    )
    db.add_all([canonical, shadow])
    await db.flush()
    await service.add_membership(
        db,
        user_id=canonical.id,
        org_unit_id="dept-ai",
        membership_type="primary",
        actor_id="test-setup",
    )
    await db.commit()
    monkeypatch.setattr("app.auth.dingtalk_oauth.httpx.AsyncClient", _FakeOpenIdOnlyHttpxClient)
    monkeypatch.setattr(
        "app.auth.dingtalk_oauth.dingtalk_client.get_user_id_by_auth_code",
        _auth_code_lookup,
    )
    monkeypatch.setattr(
        "app.auth.dingtalk_oauth.dingtalk_client.get_user_id_by_union_id",
        _union_lookup,
    )
    monkeypatch.setattr(
        "app.auth.dingtalk_oauth.dingtalk_client.get_user_detail",
        _corp_member,
    )
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_SECRET", "appsecret")

    user = await handle_dingtalk_callback(db, "auth-code")
    await db.commit()

    assert user is not None
    assert user.id == "16987446933259877"
    assert user.dingtalk_user_id == "16987446933259877"
    assert user.dingtalk_union_id == "union-openid"
    assert user.role == "aibp"
    assert user.department == "运营组"
    memberships = (
        await db.execute(
            text("SELECT org_unit_id FROM user_org_memberships WHERE user_id = :user_id"),
            {"user_id": user.id},
        )
    ).all()
    assert memberships == [("dept-ops",)]
    assert user.permissions_rev == 4
    refreshed_shadow = await db.get(User, shadow.id)
    assert refreshed_shadow is not None
    assert refreshed_shadow.dingtalk_union_id is None
    assert refreshed_shadow.permissions_rev == 1


@pytest.mark.asyncio
async def test_dingtalk_login_keeps_explicit_observer_without_linked_aibp_shadow(
    monkeypatch, db: AsyncSession
):
    """A verified login alone must not override an intentional observer role."""
    monkeypatch.setattr("app.auth.dingtalk_oauth.httpx.AsyncClient", _FakeHttpxClient)
    monkeypatch.setattr("app.auth.dingtalk_oauth.dingtalk_client.get_user_detail", _corp_member)
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_SECRET", "appsecret")
    db.add(
        User(
            id="ding-m1",
            username="ding-m1",
            name="只读审片人",
            role="observer",
            state="active",
            is_active=True,
            dingtalk_user_id="ding-m1",
            must_change_password=False,
            permissions_rev=2,
        )
    )
    await db.commit()

    user = await handle_dingtalk_callback(db, "auth-code")
    await db.commit()

    assert user is not None
    assert user.role == "observer"
    assert user.permissions_rev == 2


@pytest.mark.asyncio
async def test_dingtalk_login_does_not_grant_org_to_unverified_openid(
    monkeypatch, db: AsyncSession
):
    async def _auth_code_lookup(_auth_code: str):
        return {"ok": True, "data": {"user_id": "openid-only"}}

    async def _union_lookup(_union_id: str):
        return {"ok": False, "error": "not_found"}

    async def _not_corp_member(_user_id: str):
        return {"ok": False, "error": "not_found"}

    await create_org_unit(db, unit_id="dept-ai", name="AI组")
    monkeypatch.setattr("app.auth.dingtalk_oauth.httpx.AsyncClient", _FakeOpenIdOnlyHttpxClient)
    monkeypatch.setattr(
        "app.auth.dingtalk_oauth.dingtalk_client.get_user_id_by_auth_code",
        _auth_code_lookup,
    )
    monkeypatch.setattr(
        "app.auth.dingtalk_oauth.dingtalk_client.get_user_id_by_union_id",
        _union_lookup,
    )
    monkeypatch.setattr(
        "app.auth.dingtalk_oauth.dingtalk_client.get_user_detail",
        _not_corp_member,
    )
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_SECRET", "appsecret")

    user = await handle_dingtalk_callback(db, "auth-code")
    await db.commit()

    assert user is not None
    assert user.id == "dt_openid-only"
    assert user.department is None
    memberships = (
        await db.execute(
            text("SELECT org_unit_id FROM user_org_memberships WHERE user_id = :user_id"),
            {"user_id": user.id},
        )
    ).all()
    assert memberships == []


@pytest.mark.asyncio
async def test_dingtalk_login_uses_auth_code_fallback_when_oauth_has_no_userid(
    monkeypatch, db: AsyncSession
):
    async def _auth_code_lookup(auth_code: str):
        assert auth_code == "auth-code"
        return {
            "ok": True,
            "data": {
                "user_id": "ding-real-user",
                "union_id": "union-real-user",
                "name": "真实钉钉用户",
            },
        }

    monkeypatch.setattr("app.auth.dingtalk_oauth.httpx.AsyncClient", _FakeNoUserIdHttpxClient)
    monkeypatch.setattr("app.auth.dingtalk_oauth.dingtalk_client.get_user_id_by_auth_code", _auth_code_lookup)
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_SECRET", "appsecret")

    user = await handle_dingtalk_callback(db, "auth-code")
    await db.commit()

    assert user is not None
    assert user.id == "dt_ding-real-user"
    assert user.username == "dingtalk_ding-real-user"
    assert user.dingtalk_user_id == "ding-real-user"
    assert user.dingtalk_union_id == "union-no-userid"
    assert user.name == "真实钉钉用户"
    assert user.avatar_url == "https://example/no-userid.png"
    assert user.role == "aibp"
    assert await get_user_state(db, user) == "active"


@pytest.mark.asyncio
async def test_dingtalk_login_does_not_require_corp_contact_permission(
    monkeypatch, db: AsyncSession
):
    async def _not_corp_member(user_id: str):
        return {"ok": False, "error": "not_in_corp"}

    monkeypatch.setattr("app.auth.dingtalk_oauth.httpx.AsyncClient", _FakeHttpxClient)
    monkeypatch.setattr("app.auth.dingtalk_oauth.dingtalk_client.get_user_detail", _not_corp_member)
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_SECRET", "appsecret")

    user = await handle_dingtalk_callback(db, "auth-code")
    await db.commit()

    assert user is not None
    assert user.dingtalk_user_id == "ding-m1"
    assert user.role == "aibp"
    assert await get_user_state(db, user) == "active"


@pytest.mark.asyncio
async def test_activate_pending_user_sets_is_active_true(
    db: AsyncSession
):
    """M1：activate_pending_user → state=active + is_active=True 一致。"""
    user = User(
        id="pending-ding-m1",
        username="pending-ding-m1",
        name="待激活钉钉用户",
        role="observer",
        state="pending",
        is_active=False,
        dingtalk_user_id="ding-m1",
        must_change_password=False,
    )
    db.add(user)

    sys_admin = User(
        id="sys-admin-m1",
        username="sys-admin-m1",
        name="系统管理员",
        role="system_admin",
        is_active=True,
        must_change_password=False,
    )
    db.add(sys_admin)
    await db.flush()
    await set_user_state(db, sys_admin, "active")
    dept = await create_org_unit(db, unit_id="dept-m1", name="M1 部门")
    await db.commit()

    await user_service.activate_pending_user(
        db,
        user_id=user.id,
        new_role="aibp",
        org_unit_id=dept["id"],
        operator=sys_admin,
    )
    await db.commit()

    refreshed = await db.get(User, user.id)
    assert await get_user_state(db, refreshed) == "active"
    assert refreshed.is_active is True, "激活后必须 is_active=True"
    assert refreshed.role == "aibp"
