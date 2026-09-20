"""tasktree ABAC 跨部门 SkillMember + AI 虚拟组测试（C5a / C5b）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.common.exceptions import AppError, app_error_handler
from app.database import get_db
from app.tasktree import service as svc_mod
from app.tasktree.router import router as tasktree_router
from app.tasktree.service import VIRTUAL_AI_ID, VIRTUAL_AI_NAME, VIRTUAL_UNASSIGNED_ID


# ── 通用 mock 工厂 ───────────────────────────────────────


def _make_user(*, user_id: str, role: str, department: str, can_view_all: bool = False):
    user = MagicMock()
    user.id = user_id
    user.username = user_id
    user.name = user_id
    user.role = role
    user.department = department
    user.can_view_all = can_view_all
    user.is_active = True
    user.must_change_password = False
    return user


class _Result:
    """轻量替代 sqlalchemy Result，支持 .all() / .scalars()."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


def _make_lv1_unit(unit_id: str, name: str):
    """构造 OrgUnit-like 对象，name 用普通属性而非 MagicMock 构造名。"""
    unit = MagicMock(spec=["id", "name", "parent_id", "path", "sort_order"])
    unit.id = unit_id
    unit.name = name
    unit.parent_id = "ROOT"
    unit.path = f"/ROOT/{unit_id}"
    unit.sort_order = 0
    return unit


def _make_org_indexes():
    sales = _make_lv1_unit("lv1-sales", "销售一部")
    rnd = _make_lv1_unit("lv1-rnd", "研发部")
    am = _make_lv1_unit("lv1-am", "AM")
    lv1_by_id = {sales.id: sales, rnd.id: rnd, am.id: am}
    lv1_by_name = {sales.name: sales, rnd.name: rnd, am.name: am}
    lv1_names = set(lv1_by_name)
    org_by_name = dict(lv1_by_name)
    return lv1_by_id, lv1_by_name, lv1_names, org_by_name


@pytest_asyncio.fixture
async def app_with_router(monkeypatch):
    """提供仅注册 tasktree 路由的 FastAPI app；自动 monkeypatch 索引/cache。"""
    from app.tasktree import router as router_mod

    fake_indexes = _make_org_indexes()
    fake_load = AsyncMock(return_value=fake_indexes)
    # 同时 patch service / router 两侧——router 是 from import，名字已绑死
    monkeypatch.setattr(svc_mod, "_load_org_indexes", fake_load)
    monkeypatch.setattr(router_mod, "_load_org_indexes", fake_load)
    # 顺便清掉 H13 进程级 cache，避免上一轮测试残留
    svc_mod._ORG_INDEXES_CACHE["value"] = None
    svc_mod._ORG_INDEXES_CACHE["expires_at"] = 0.0

    async def _noop_get(_key):
        return None

    async def _noop_set(_key, _value, **_kw):
        return None

    monkeypatch.setattr(svc_mod, "cache_get", _noop_get)
    monkeypatch.setattr(svc_mod, "cache_set", _noop_set)

    # 兜底 mock projection.get_tree 不真正访问 DB
    from app.tasktree.schemas import TaskTreeResponse
    from app.tasktree.service import projection

    async def _fake_get_tree(*args, **kwargs):
        return TaskTreeResponse(
            departments=[],
            projected_at="2026-04-18T00:00:00",
            etag="etag-fake",
            total_online=0,
            total_offline=0,
            total_running=0,
        )

    monkeypatch.setattr(projection, "get_tree", _fake_get_tree)

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(tasktree_router, prefix="/api")
    yield app


def _make_db_with_member_skills(member_dept_rows):
    """伪造 db.execute → SkillMember join Skill.department 的行集合。

    member_dept_rows: list of department strings (即 user 通过 SkillMember 间接代管的部门名)
    """
    db = MagicMock()

    async def _execute(*_args, **_kwargs):
        return _Result([(d,) for d in member_dept_rows])

    db.execute = _execute
    return db


# ── C5a：跨部门 SkillMember 通道 ──────────────────────────


@pytest.mark.asyncio
async def test_skillmember_can_access_managed_department(app_with_router):
    """alice = biz_owner of 销售一部，被加为 AM 部门某 skill 的 SkillMember
    → 调 GET /api/task-tree?department=AM 应返回 200。"""
    alice = _make_user(user_id="u_alice", role="biz_owner", department="销售一部")
    db = _make_db_with_member_skills(["AM"])  # alice 通过 skill_member 关联 AM

    async def _fake_db():
        yield db

    app_with_router.dependency_overrides[get_current_user] = lambda: alice
    app_with_router.dependency_overrides[get_db] = _fake_db

    transport = ASGITransport(app=app_with_router)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/task-tree", params={"department": "AM"})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_skillmember_denies_unrelated_department(app_with_router):
    """alice 无 Z 部门任何 SkillMember → GET ?department=研发部 应 403。"""
    alice = _make_user(user_id="u_alice", role="biz_owner", department="销售一部")
    db = _make_db_with_member_skills(["AM"])  # 只代管 AM，不代管研发部

    async def _fake_db():
        yield db

    app_with_router.dependency_overrides[get_current_user] = lambda: alice
    app_with_router.dependency_overrides[get_db] = _fake_db

    transport = ASGITransport(app=app_with_router)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/task-tree", params={"department": "研发部"})
    assert resp.status_code == 403
    code = (resp.json().get("error") or {}).get("code") or resp.json().get("code")
    assert code == "AUTH_DEPARTMENT_DENIED"


# ── C5b：AI 虚拟组用户访问自己部门 ──────────────────────────


@pytest.mark.asyncio
async def test_ai_user_can_access_ai_virtual_group(app_with_router):
    """ai_user = role=biz_owner, department='AI'
    → GET /api/task-tree?department=AI 应 200（C5b 之前 403）。"""
    ai_user = _make_user(user_id="u_ai", role="biz_owner", department="AI")
    db = _make_db_with_member_skills([])

    async def _fake_db():
        yield db

    app_with_router.dependency_overrides[get_current_user] = lambda: ai_user
    app_with_router.dependency_overrides[get_db] = _fake_db

    transport = ASGITransport(app=app_with_router)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/task-tree", params={"department": "AI"})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_non_admin_non_ai_user_denied_ai_virtual_group(app_with_router):
    """普通业务用户访问 AI 虚拟组 → 403。"""
    bob = _make_user(user_id="u_bob", role="biz_owner", department="销售一部")
    db = _make_db_with_member_skills([])

    async def _fake_db():
        yield db

    app_with_router.dependency_overrides[get_current_user] = lambda: bob
    app_with_router.dependency_overrides[get_db] = _fake_db

    transport = ASGITransport(app=app_with_router)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/task-tree", params={"department": "AI"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_access_ai_virtual_group(app_with_router):
    """admin 访问 AI 虚拟组 → 200。"""
    admin = _make_user(user_id="u_admin", role="admin", department="AI", can_view_all=True)
    db = _make_db_with_member_skills([])

    async def _fake_db():
        yield db

    app_with_router.dependency_overrides[get_current_user] = lambda: admin
    app_with_router.dependency_overrides[get_db] = _fake_db

    transport = ASGITransport(app=app_with_router)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/task-tree", params={"department": "AI"})
    assert resp.status_code == 200, resp.text


def test_resolve_user_lv1_ai_returns_virtual_id():
    """C5b 单元测试：raw='AI' → VIRTUAL_AI_ID。"""
    lv1_by_id, _by_name, lv1_names, org_by_name = _make_org_indexes()
    assert (
        svc_mod._resolve_user_lv1(
            "AI",
            lv1_by_id=lv1_by_id,
            lv1_names=lv1_names,
            org_by_name=org_by_name,
        )
        == VIRTUAL_AI_ID
    )


def test_resolve_user_lv1_real_lv1_unchanged():
    """C5b 回归：真实 Lv1 用户 → 原样返回 lv1 名。"""
    lv1_by_id, _by_name, lv1_names, org_by_name = _make_org_indexes()
    assert (
        svc_mod._resolve_user_lv1(
            "销售一部",
            lv1_by_id=lv1_by_id,
            lv1_names=lv1_names,
            org_by_name=org_by_name,
        )
        == "销售一部"
    )
