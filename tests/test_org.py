"""组织架构 API 回归测试。"""

import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.common.exceptions import AppError, app_error_handler
from app.config import settings
from app.dingtalk.client import dingtalk_client


def _mock_user(*, user_id: str, role: str = "operator", department: str | None = None, can_view_all: bool = False):
    user = MagicMock()
    user.id = user_id
    user.name = user_id
    user.username = user_id
    user.role = role
    user.department = department
    user.can_view_all = can_view_all
    user.is_active = True
    user.must_change_password = False
    user.dingtalk_user_id = None
    user.avatar_url = None
    return user


def _build_app(mock_user):
    from app.auth.dependencies import get_current_user
    from app.org.router import router

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.dependency_overrides[get_current_user] = lambda: mock_user

    app.include_router(router, prefix="/api/org")
    return app


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload, ensure_ascii=False)


class _FakeHttpxClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, params=None, json=None, headers=None):
        if url.endswith("/topapi/v2/department/listsub"):
            dept_id = json["dept_id"]
            if str(dept_id) == "1":
                return _FakeResponse(
                    {
                        "errcode": 0,
                        "result": [
                            {"dept_id": 10, "name": "电商部", "parent_dept_id": 1, "order": 1},
                            {"dept_id": 20, "name": "商务部", "parent_dept_id": 1, "order": 2},
                        ],
                    }
                )
            if str(dept_id) == "10":
                return _FakeResponse(
                    {
                        "errcode": 0,
                        "result": [
                            {"dept_id": 11, "name": "运营组", "parent_dept_id": 10, "order": 1},
                        ],
                    }
                )
            return _FakeResponse({"errcode": 0, "result": []})

        if url.endswith("/topapi/v2/user/list"):
            dept_id = str(json["dept_id"])
            cursor = str(json["cursor"])
            if dept_id == "10" and cursor == "0":
                return _FakeResponse(
                    {
                        "errcode": 0,
                        "result": {
                            "list": [{"userid": "u_ec", "name": "电商用户"}],
                            "has_more": True,
                            "next_cursor": "1",
                        },
                    }
                )
            if dept_id == "10" and cursor == "1":
                return _FakeResponse(
                    {
                        "errcode": 0,
                        "result": {
                            "list": [{"userid": "u_ec_2", "name": "电商用户2"}],
                            "has_more": False,
                            "next_cursor": "",
                        },
                    }
                )
            if dept_id == "11":
                return _FakeResponse(
                    {
                        "errcode": 0,
                        "result": {
                            "list": [{"userid": "u_ops", "name": "运营用户"}],
                            "has_more": False,
                            "next_cursor": "",
                        },
                    }
                )
            if dept_id == "20":
                return _FakeResponse(
                    {
                        "errcode": 0,
                        "result": {
                            "list": [{"userid": "u_bd", "name": "商务用户"}],
                            "has_more": False,
                            "next_cursor": "",
                        },
                    }
                )

        return _FakeResponse({"errcode": 0, "result": []})


@pytest.mark.asyncio
async def test_dingtalk_client_helpers_fetch_tree_and_users(monkeypatch):
    async def _token():
        return "token"

    monkeypatch.setattr(dingtalk_client, "get_access_token", _token)
    monkeypatch.setattr("app.dingtalk.client.httpx.AsyncClient", _FakeHttpxClient)

    tree = await dingtalk_client.get_department_tree(1)
    assert tree["ok"] is True
    assert [node["dept_id"] for node in tree["data"]] == ["10", "20"]
    assert tree["data"][0]["children"][0]["dept_id"] == "11"

    users = await dingtalk_client.list_department_users("10")
    assert users["ok"] is True
    assert [item["user_id"] for item in users["data"]] == ["u_ec", "u_ec_2"]


@pytest.mark.asyncio
async def test_sync_dingtalk_uses_dingtalk_api_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "DINGTALK_APP_KEY", "configured-key")
    monkeypatch.setattr(settings, "DINGTALK_APP_SECRET", "configured-secret")

    async def _tree(root_dept_id=1):
        assert str(root_dept_id) == "1"
        return {
            "ok": True,
            "data": [
                {
                    "dept_id": "10",
                    "name": "电商部",
                    "parent_dept_id": None,
                    "order": 1,
                    "children": [
                        {
                            "dept_id": "11",
                            "name": "运营组",
                            "parent_dept_id": "10",
                            "order": 1,
                            "children": [],
                        }
                    ],
                },
                {
                    "dept_id": "20",
                    "name": "商务部",
                    "parent_dept_id": None,
                    "order": 2,
                    "children": [],
                },
            ],
        }

    async def _users(dept_id, size=100):
        mapping = {
            "10": [{"user_id": "u_ec", "name": "电商用户", "is_manager": True}],
            "11": [{"user_id": "u_ops", "name": "运营用户"}],
            "20": [{"user_id": "u_bd", "name": "商务用户"}],
        }
        return {"ok": True, "data": mapping[str(dept_id)]}

    monkeypatch.setattr(dingtalk_client, "get_department_tree", _tree)
    monkeypatch.setattr(dingtalk_client, "list_department_users", _users)

    from app.auth.models import User
    from app.database import async_session_factory

    async with async_session_factory() as session:
        session.add_all(
            [
                User(id="u_ec", username="u_ec", name="旧电商名", role="operator", department="Legacy A", dingtalk_user_id="u_ec", is_active=True),
                User(id="u_ops", username="u_ops", name="运营用户", role="operator", department="Legacy B", dingtalk_user_id="u_ops", is_active=True),
                User(id="u_bd", username="u_bd", name="商务用户", role="operator", department="Legacy C", dingtalk_user_id="u_bd", is_active=True),
            ]
        )
        await session.commit()

    resp = await client.post("/api/org/sync-dingtalk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["synced_units"] == 3
    assert body["synced_users"] == 3
    assert body["errors"] == []

    tree = (await client.get("/api/org/tree")).json()
    assert [node["id"] for node in tree] == ["10", "20"]
    assert tree[0]["children"][0]["id"] == "11"

    members = (await client.get("/api/org/units/10/members")).json()["items"]
    assert members[0]["user_id"] == "u_ec"
    assert members[0]["membership_type"] == "primary"
    assert members[0]["is_manager"] is True


@pytest.mark.asyncio
async def test_sync_dingtalk_auto_creates_missing_users(client, monkeypatch):
    """钉钉同步来的 userid 在 users 表不存在时，应自动创建用户（id=dt_{钉钉userid}）。

    之前 bug：sync_dingtalk_org 直接把钉钉 userid 当 users.id 传给 add_membership，
    触发 FK 约束冲突。修复后按 dingtalk_user_id 查、找不到就按 dingtalk_oauth 的规则
    新建 User(id=dt_...)。
    """
    monkeypatch.setattr(settings, "DINGTALK_APP_KEY", "configured-key")
    monkeypatch.setattr(settings, "DINGTALK_APP_SECRET", "configured-secret")

    async def _tree(root_dept_id=1):
        return {
            "ok": True,
            "data": [
                {"dept_id": "30", "name": "新部门", "parent_dept_id": None, "order": 1, "children": []},
            ],
        }

    # 钉钉返回的 userid 是 OpenID 风格（长字符），users 表中**不存在**对应记录
    async def _users(dept_id, size=100):
        return {
            "ok": True,
            "data": [
                {"user_id": "openid-abc-12345", "name": "新员工甲", "email": "alice@example.com", "is_manager": True},
                {"user_id": "openid-def-67890", "name": "新员工乙", "mobile": "13800138000"},
            ],
        }

    monkeypatch.setattr(dingtalk_client, "get_department_tree", _tree)
    monkeypatch.setattr(dingtalk_client, "list_department_users", _users)

    resp = await client.post("/api/org/sync-dingtalk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["synced_units"] == 1
    assert body["synced_users"] == 2
    assert body["errors"] == []

    # 验证用户被按 dingtalk_oauth 规则自动创建
    from app.auth.models import User
    from app.database import async_session_factory
    async with async_session_factory() as session:
        from sqlalchemy import select
        users = (await session.execute(select(User).where(User.id.like("dt_%")))).scalars().all()
        user_ids = {u.id for u in users}
        assert user_ids == {"dt_openid-abc-12345", "dt_openid-def-67890"}
        by_id = {u.id: u for u in users}
        assert by_id["dt_openid-abc-12345"].role == "aibp"
        assert by_id["dt_openid-abc-12345"].state == "active"
        assert by_id["dt_openid-abc-12345"].is_active is True
        assert by_id["dt_openid-abc-12345"].name == "新员工甲"
        assert by_id["dt_openid-abc-12345"].email == "alice@example.com"
        assert by_id["dt_openid-abc-12345"].dingtalk_user_id == "openid-abc-12345"
        assert by_id["dt_openid-abc-12345"].password_hash is None  # 只能钉钉扫码登录
        assert by_id["dt_openid-def-67890"].phone == "13800138000"

    # 验证 membership 用的是 SkillForge 内部 id（dt_前缀），不是钉钉原始 userid
    members = (await client.get("/api/org/units/30/members")).json()["items"]
    member_ids = {m["user_id"] for m in members}
    assert member_ids == {"dt_openid-abc-12345", "dt_openid-def-67890"}


@pytest.mark.asyncio
async def test_sync_dingtalk_reuses_oauth_shadow_by_union_id(client, monkeypatch):
    """Directory sync must link the OAuth namespace instead of creating a duplicate user."""
    monkeypatch.setattr(settings, "DINGTALK_APP_KEY", "configured-key")
    monkeypatch.setattr(settings, "DINGTALK_APP_SECRET", "configured-secret")

    async def _tree(root_dept_id=1):
        return {
            "ok": True,
            "data": [
                {
                    "dept_id": "40",
                    "name": "运营组",
                    "parent_dept_id": None,
                    "order": 1,
                    "children": [],
                }
            ],
        }

    async def _users(dept_id, size=100):
        assert str(dept_id) == "40"
        return {
            "ok": True,
            "data": [
                {
                    "user_id": "16987446933259877",
                    "union_id": "union-real-user",
                    "name": "童小琳",
                }
            ],
        }

    monkeypatch.setattr(dingtalk_client, "get_department_tree", _tree)
    monkeypatch.setattr(dingtalk_client, "list_department_users", _users)

    from app.auth.models import User
    from app.database import async_session_factory
    from app.org.models import UserOrgMembership

    async with async_session_factory() as session:
        session.add(
            User(
                id="dt_oauth-shadow",
                username="dingtalk_oauth-shadow",
                name="千寻-童小琳",
                role="aibp",
                state="active",
                dingtalk_user_id="oauth-open-id",
                dingtalk_union_id="union-real-user",
                is_active=True,
                must_change_password=False,
            )
        )
        await session.commit()

    resp = await client.post("/api/org/sync-dingtalk")
    assert resp.status_code == 200
    assert resp.json()["errors"] == []

    async with async_session_factory() as session:
        shadow = await session.get(User, "dt_oauth-shadow")
        duplicate = await session.get(User, "dt_16987446933259877")
        memberships = list(
            (
                await session.execute(
                    select(UserOrgMembership).where(
                        UserOrgMembership.user_id == "dt_oauth-shadow"
                    )
                )
            ).scalars().all()
        )
        assert shadow is not None
        assert shadow.dingtalk_user_id == "16987446933259877"
        assert shadow.department == "运营组"
        assert duplicate is None
        assert [item.org_unit_id for item in memberships] == ["40"]


@pytest.mark.asyncio
async def test_sync_dingtalk_reuses_user_by_dingtalk_id(client, monkeypatch):
    """用户已通过钉钉 OAuth 登录存在时（id=dt_xxx + dingtalk_user_id=xxx），
    同步不应重复创建，只更新 department/name/联系方式等基础字段。"""
    monkeypatch.setattr(settings, "DINGTALK_APP_KEY", "configured-key")
    monkeypatch.setattr(settings, "DINGTALK_APP_SECRET", "configured-secret")

    async def _tree(root_dept_id=1):
        return {"ok": True, "data": [
            {"dept_id": "40", "name": "新命名部门", "parent_dept_id": None, "children": []},
        ]}

    async def _users(dept_id, size=100):
        return {"ok": True, "data": [
            {"user_id": "oauth-user-1", "name": "新姓名", "email": "new@example.com"},
        ]}

    monkeypatch.setattr(dingtalk_client, "get_department_tree", _tree)
    monkeypatch.setattr(dingtalk_client, "list_department_users", _users)

    from app.auth.models import User
    from app.database import async_session_factory
    async with async_session_factory() as session:
        session.add(User(
            id="dt_oauth-user-1",
            username="dingtalk_oauth-user-1",
            name="旧姓名",
            role="biz_owner",  # 已被 admin 提升过角色，同步不能覆盖
            department="旧部门",
            dingtalk_user_id="oauth-user-1",
            is_active=True,
        ))
        await session.commit()

    resp = await client.post("/api/org/sync-dingtalk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["synced_users"] == 1
    assert body["errors"] == []

    async with async_session_factory() as session:
        from sqlalchemy import select, func
        count = (await session.execute(
            select(func.count(User.id)).where(User.dingtalk_user_id == "oauth-user-1")
        )).scalar_one()
        assert count == 1  # 未重复创建
        user = (await session.execute(
            select(User).where(User.id == "dt_oauth-user-1")
        )).scalar_one()
        assert user.name == "新姓名"           # 更新了
        assert user.email == "new@example.com"   # 更新了
        assert user.department == "新命名部门"    # 更新了
        assert user.role == "biz_owner"         # 未被覆盖（敏感字段保护）


@pytest.mark.asyncio
async def test_sync_dingtalk_falls_back_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(settings, "DINGTALK_APP_KEY", "")
    monkeypatch.setattr(settings, "DINGTALK_APP_SECRET", "")

    async def _should_not_call(*args, **kwargs):
        pytest.fail("DingTalk client should not be called when unconfigured")

    monkeypatch.setattr(dingtalk_client, "get_department_tree", _should_not_call)
    monkeypatch.setattr(dingtalk_client, "list_department_users", _should_not_call)

    from app.auth.models import User
    from app.database import async_session_factory

    async with async_session_factory() as session:
        session.add_all(
            [
                User(id="u_ec", username="u_ec", name="电商用户", role="operator", department="电商部", is_active=True),
                User(id="u_bd", username="u_bd", name="商务用户", role="operator", department="商务部", is_active=True),
            ]
        )
        await session.commit()

    resp = await client.post("/api/org/sync-dingtalk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["synced_units"] == 2
    assert body["synced_users"] == 2

    tree = (await client.get("/api/org/tree")).json()
    assert {node["id"] for node in tree} == {"商务部", "电商部"}


@pytest.mark.asyncio
async def test_create_unit_memberships_and_members_list(client):
    from app.auth.models import User
    from app.database import async_session_factory

    async with async_session_factory() as session:
        session.add(
            User(id="member-1", username="member-1", name="成员一号", role="operator", department="电商部", is_active=True)
        )
        await session.commit()

    unit = (await client.post(
        "/api/org/units",
        json={"name": "618项目组", "type": "project"},
    )).json()
    unit_id = unit["id"]

    add_resp = await client.post(
        "/api/org/memberships",
        json={"user_id": "member-1", "org_unit_id": unit_id, "membership_type": "secondary", "is_manager": True},
    )
    assert add_resp.status_code == 200
    assert add_resp.json()["is_manager"] is True

    members = (await client.get(f"/api/org/units/{unit_id}/members")).json()["items"]
    assert len(members) == 1
    assert members[0]["user_id"] == "member-1"
    assert members[0]["membership_type"] == "secondary"

    del_resp = await client.delete(f"/api/org/memberships/member-1/{unit_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    members_after = (await client.get(f"/api/org/units/{unit_id}/members")).json()["items"]
    assert members_after == []


@pytest.mark.asyncio
async def test_tree_filters_to_member_scope(client):
    from app.auth.dependencies import get_db
    from app.auth.models import User
    from app.database import async_session_factory
    from app.org.models import UserOrgMembership
    from app.org.service import create_org_unit

    async with async_session_factory() as session:
        session.add(User(id="scope-user", username="scope-user", name="范围用户", role="operator", department="运营组", is_active=True))
        root = await create_org_unit(session, unit_id="ROOT", name="总部", unit_type="department")
        child = await create_org_unit(session, unit_id="ROOT-OPS", name="运营组", unit_type="team", parent_id=root["id"])
        session.add(
            UserOrgMembership(user_id="scope-user", org_unit_id=child["id"], membership_type="primary", is_manager=False)
        )
        await session.commit()

    mock_user = _mock_user(user_id="scope-user", role="operator", department="运营组")
    app = _build_app(mock_user)

    async def _db_override():
        async with async_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db_override

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/org/tree")

    assert resp.status_code == 200
    tree = resp.json()
    assert len(tree) == 1
    assert tree[0]["id"] == "ROOT"
    assert tree[0]["children"][0]["id"] == "ROOT-OPS"
