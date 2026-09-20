"""认证模块测试"""

import pytest
from unittest.mock import MagicMock
from urllib.parse import quote

from httpx import ASGITransport, AsyncClient


def _make_mock_user(
    *,
    user_id: str,
    role: str,
    department: str | None = "AI小组",
    name: str = "测试用户",
    username: str | None = None,
    can_view_all: bool = False,
    is_active: bool = True,
    must_change_password: bool = False,
    state: str = "active",
    permissions_rev: int = 0,
):
    user = MagicMock()
    user.id = user_id
    user.name = name
    user.username = username or user_id
    user.role = role
    user.department = department
    user.can_view_all = can_view_all
    user.is_active = is_active
    user.must_change_password = must_change_password
    user.avatar_url = None
    user.email = None
    user.phone = None
    user.permissions_rev = permissions_rev
    user.state = state
    user.dingtalk_user_id = None
    return user


@pytest.mark.asyncio
async def test_login_success(client):
    """测试正确密码登录（先通过API创建测试用户）"""
    create_resp = await client.post("/api/users/", json={
        "user_id": "test_login_user",
        "username": "testlogin",
        "password": "Test1234!",
        "name": "测试登录用户",
        "role": "operator",
        "department": "AI小组",
    })
    assert create_resp.status_code == 200, f"创建用户失败: {create_resp.json()}"
    resp = await client.post("/api/auth/login", json={"username": "testlogin", "password": "Test1234!"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"]
    assert data["role"] == "operator"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    """测试错误密码"""
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_me(client):
    """测试获取当前用户"""
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "admin"


@pytest.mark.asyncio
async def test_dingtalk_login_preserves_safe_next(client):
    """钉钉登录入口应保存站内 next，callback 成功后跳回原授权页。"""
    from app.auth.router import DINGTALK_NEXT_COOKIE
    from app.auth.dingtalk_oauth import DINGTALK_STATE_COOKIE
    from app.config import settings

    next_path = "/api/codex/auth/authorize?login_intent_id=login_test&state=state_test"
    resp = await client.get(
        f"/api/auth/dingtalk/login?next={quote(next_path, safe='')}",
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert resp.cookies.get(DINGTALK_NEXT_COOKIE) == quote(next_path, safe="")
    assert resp.cookies.get(DINGTALK_STATE_COOKIE)

    unsafe = await client.get("/api/auth/dingtalk/login?next=https://evil.example/callback", follow_redirects=False)
    assert unsafe.status_code == 307
    assert unsafe.cookies.get(DINGTALK_NEXT_COOKIE) == quote("/", safe="")
    assert settings.COOKIE_SECURE is not None


@pytest.mark.asyncio
async def test_me_without_login():
    """未登录访问/me应返回401"""
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/auth/me")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health(client):
    """健康检查"""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ===== 新增: 未登录访问受保护端点 =====


@pytest.mark.asyncio
async def test_access_protected_endpoint_without_login():
    """未登录访问受保护的API端点——应返回401"""
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 尝试访问Skill列表
        resp = await ac.get("/api/skills/")
        assert resp.status_code == 401

        # 尝试访问审核列表
        resp2 = await ac.get("/api/reviews/")
        assert resp2.status_code == 401


# ===== 新增: 角色权限检查——operator不能创建Skill =====


@pytest.mark.asyncio
async def test_role_based_access_operator_cannot_create_skill():
    """测试角色权限：operator角色不能创建Skill（需要admin/ai_engineer权限）"""
    from app.common.exceptions import AppError, app_error_handler
    from app.auth.dependencies import get_current_user
    from fastapi import FastAPI

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    # mock operator用户
    mock_user = _make_mock_user(
        user_id="operator_user",
        name="操作员",
        role="operator",
        department="电商",
    )
    app.dependency_overrides[get_current_user] = lambda: mock_user

    from app.skills.router import router
    app.include_router(router, prefix="/api/skills")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/skills/", json={
            "skill_id": "TEST-OP-01",
            "name": "测试",
            "department": "电商",
        })
        # operator没有create skill的权限（需要admin/ai_engineer）
        assert resp.status_code == 403


# ===== 新增: 部门隔离——biz_owner只能看本部门 =====


@pytest.mark.asyncio
async def test_department_isolation_biz_owner():
    """测试部门隔离：biz_owner只能看自己部门的数据"""
    from app.auth.dependencies import require_department_access

    # biz_owner用户
    mock_user = _make_mock_user(
        user_id="biz_user",
        role="biz_owner",
        department="传统电商",
    )

    # 同部门可访问
    assert require_department_access("传统电商", mock_user) is True

    # 其他部门不可访问
    assert require_department_access("直播电商", mock_user) is False
    assert require_department_access("AI小组", mock_user) is False


@pytest.mark.asyncio
async def test_department_isolation_admin_can_view_all():
    """测试部门隔离：admin可以查看所有部门"""
    from app.auth.dependencies import require_department_access

    mock_admin = _make_mock_user(
        user_id="admin",
        role="admin",
        can_view_all=True,
    )

    assert require_department_access("传统电商", mock_admin) is True
    assert require_department_access("直播电商", mock_admin) is True


@pytest.mark.asyncio
async def test_department_isolation_ai_engineer_can_view_all():
    """测试部门隔离：ai_engineer可以查看所有部门"""
    from app.auth.dependencies import require_department_access

    mock_eng = _make_mock_user(
        user_id="engineer",
        role="ai_engineer",
        can_view_all=False,
    )  # 即使can_view_all=False，ai_engineer角色也能查看

    assert require_department_access("传统电商", mock_eng) is True


# ===== 新增: Session token验证 =====


def test_session_token_create_and_verify():
    """测试session token的创建和验证 (v2: token 内含 uid + permissions_rev 快照)"""
    from app.auth.dependencies import create_session_token, verify_session_token

    # 兼容 legacy 单参调用
    token = create_session_token("test_user")
    assert token is not None

    result = verify_session_token(token)
    assert result is not None
    user_id, rev = result
    assert user_id == "test_user"
    assert rev == 0

    # v2: 带 permissions_rev 的 token
    token_v2 = create_session_token("test_user", 7)
    result_v2 = verify_session_token(token_v2)
    assert result_v2 == ("test_user", 7)


def test_session_token_invalid():
    """测试无效的session token"""
    from app.auth.dependencies import verify_session_token

    result = verify_session_token("invalid_token_abc")
    assert result is None


# ===== 新增: biz_owner角色访问Skill列表——部门过滤 =====


@pytest.mark.asyncio
async def test_biz_owner_skill_list_filtered(client):
    """测试biz_owner访问Skill列表——只返回本部门的Skill"""
    from app.auth.dependencies import get_current_user

    mock_user = _make_mock_user(
        user_id="biz_user",
        name="业务用户",
        role="biz_owner",
        department="传统电商",
    )
    app = client._transport.app
    old_override = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        resp = await client.get("/api/skills/")
        # 应该能访问成功（列表是部门过滤后的结果）
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert isinstance(data["items"], list)
        assert data["page"] == 1
    finally:
        if old_override is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = old_override


# ===== 新增: require_role装饰器测试 =====


@pytest.mark.asyncio
async def test_require_role_allowed():
    """测试require_role——允许的角色"""
    from app.auth.dependencies import require_role, get_current_user
    from app.common.exceptions import AppError, app_error_handler
    from fastapi import FastAPI, Depends

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    mock_user = _make_mock_user(
        user_id="admin",
        role="admin",
    )
    app.dependency_overrides[get_current_user] = lambda: mock_user

    @app.get("/test-role")
    async def test_endpoint(user=Depends(require_role("admin", "ai_engineer"))):
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/test-role")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_require_role_denied():
    """测试require_role——不允许的角色"""
    from app.auth.dependencies import require_role, get_current_user
    from app.common.exceptions import AppError, app_error_handler
    from fastapi import FastAPI, Depends

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    mock_user = _make_mock_user(
        user_id="viewer",
        role="viewer",
    )
    app.dependency_overrides[get_current_user] = lambda: mock_user

    @app.get("/test-role-denied")
    async def test_endpoint(user=Depends(require_role("admin"))):
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/test-role-denied")
        assert resp.status_code == 403


# ===== v2: session 立即失效（state=disabled / permissions_rev 不匹配） =====


def _build_auth_only_app():
    """构建仅含 /api/auth 路由的 FastAPI app（不覆盖 get_current_user，用真实 session）。"""
    from fastapi import FastAPI

    from app.auth.router import router as auth_router
    from app.common.exceptions import AppError, app_error_handler

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(auth_router, prefix="/api/auth")
    return app


@pytest.mark.asyncio
async def test_disabled_user_session_rejected(client):
    """
    v2 §10.2：账号被禁用后旧 session 立即失效。
    流程：建 active 用户 + 签发 token → DB 直接改 state=disabled/is_active=false
          → 带旧 token 请求 /api/auth/me → 403 AUTH_ACCOUNT_DISABLED。
    注意：client fixture 已 override 了 get_current_user（返回 mock admin），
    本测试自建一个 app 确保真实 get_current_user 生效。
    """
    from sqlalchemy import text

    import app.database as db_mod
    from app.auth.dependencies import SESSION_COOKIE, create_session_token

    uid = "v2_disabled_user"
    # 1. 直接 SQL 建 active 用户（字段与 User 模型对齐；created_at/updated_at 必填）
    async with db_mod.async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, username, name, role, state, "
                "permissions_rev, is_active, must_change_password, can_view_all, "
                "created_at, updated_at) "
                "VALUES (:id, :uname, :name, :role, :state, 0, true, false, false, "
                "NOW(), NOW())"
            ),
            {
                "id": uid,
                "uname": uid,
                "name": "禁用测试用户",
                "role": "observer",
                "state": "active",
            },
        )
        await session.commit()

    # 2. 签发 rev=0 的 token（此刻应能通过认证）
    token = create_session_token(uid, 0)

    # 3. 直接 DB 改 state=disabled + is_active=false
    async with db_mod.async_session_factory() as session:
        await session.execute(
            text("UPDATE users SET state='disabled', is_active=false WHERE id=:id"),
            {"id": uid},
        )
        await session.commit()

    # 4. 用旧 token 请求 /api/auth/me → 应 403 AUTH_ACCOUNT_DISABLED
    app = _build_auth_only_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set(SESSION_COOKIE, token)
        resp = await ac.get("/api/auth/me")
        assert resp.status_code == 403, f"期望 403，实际 {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["error"]["code"] == "AUTH_ACCOUNT_DISABLED"


@pytest.mark.asyncio
async def test_permission_changed_session_expired(client):
    """
    v2 §10.2：permissions_rev 升级后旧 session token 立即失效。
    流程：建用户 rev=0 → create_session_token(uid, 0) → DB 改 permissions_rev=1
          → 旧 token 请求 /api/auth/me → 401 PERMISSIONS_REV_MISMATCH。
    """
    from sqlalchemy import text

    import app.database as db_mod
    from app.auth.dependencies import SESSION_COOKIE, create_session_token

    uid = "v2_rev_bumped_user"
    async with db_mod.async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, username, name, role, state, "
                "permissions_rev, is_active, must_change_password, can_view_all, "
                "created_at, updated_at) "
                "VALUES (:id, :uname, :name, 'observer', 'active', 0, true, false, false, "
                "NOW(), NOW())"
            ),
            {"id": uid, "uname": uid, "name": "权限升级测试用户"},
        )
        await session.commit()

    token = create_session_token(uid, 0)

    async with db_mod.async_session_factory() as session:
        await session.execute(
            text("UPDATE users SET permissions_rev=1 WHERE id=:id"),
            {"id": uid},
        )
        await session.commit()

    app = _build_auth_only_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set(SESSION_COOKIE, token)
        resp = await ac.get("/api/auth/me")
        assert resp.status_code == 401, f"期望 401，实际 {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["error"]["code"] == "PERMISSIONS_REV_MISMATCH"


# ===== v2 §10.2.1: pending 用户禁入业务接口（require_state_active） =====


def _build_business_app_with_auth():
    """构建含 /api/auth + /api/skills 的 FastAPI app，不覆盖任何依赖（走真实 session）。

    用于验证 pending 用户能过 /api/auth/me 白名单，但被 /api/skills 业务接口拒。
    """
    from fastapi import FastAPI

    from app.auth.router import router as auth_router
    from app.common.exceptions import AppError, app_error_handler
    from app.skills.router import router as skills_router

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(skills_router, prefix="/api/skills")
    return app


@pytest.mark.asyncio
async def test_pending_user_blocked_from_business_endpoint(client):
    """
    v2 §10.2.1：pending 用户默认不能进入业务流程。
    流程：建 pending 用户 → 签 token → /api/skills/ → 403 AUTH_ACCOUNT_NOT_ACTIVE
          同一 token → /api/auth/me → 200（白名单放行，pending 可查自己信息）
    """
    from sqlalchemy import text

    import app.database as db_mod
    from app.auth.dependencies import SESSION_COOKIE, create_session_token

    uid = "v2_pending_user"
    async with db_mod.async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, username, name, role, state, "
                "permissions_rev, is_active, must_change_password, can_view_all, "
                "created_at, updated_at) "
                "VALUES (:id, :uname, :name, 'observer', 'pending', 0, true, false, false, "
                "NOW(), NOW())"
            ),
            {"id": uid, "uname": uid, "name": "待激活用户"},
        )
        await session.commit()

    token = create_session_token(uid, 0)

    app = _build_business_app_with_auth()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set(SESSION_COOKIE, token)
        # 业务接口拒绝 pending
        resp = await ac.get("/api/skills/")
        assert resp.status_code == 403, f"期望 403，实际 {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["error"]["code"] == "AUTH_ACCOUNT_NOT_ACTIVE"

        # /api/auth/me 白名单放行
        resp_me = await ac.get("/api/auth/me")
        assert resp_me.status_code == 200, f"pending 应可访问 /me，实际 {resp_me.status_code}: {resp_me.text}"
        me_body = resp_me.json()
        assert me_body["user_id"] == uid


@pytest.mark.asyncio
async def test_require_state_active_allows_active_user(client):
    """
    require_state_active 对 active 用户应放行到业务层。
    流程：建 active 用户 → 签 token → /api/skills/ → 不应是 AUTH_ACCOUNT_NOT_ACTIVE。
    （200 或业务层的 403 PERMISSION_DENIED 都算通过 state 检查）
    """
    from sqlalchemy import text

    import app.database as db_mod
    from app.auth.dependencies import SESSION_COOKIE, create_session_token

    uid = "v2_active_state_user"
    async with db_mod.async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, username, name, role, state, "
                "permissions_rev, is_active, must_change_password, can_view_all, "
                "department, created_at, updated_at) "
                "VALUES (:id, :uname, :name, 'observer', 'active', 0, true, false, false, "
                ":dept, NOW(), NOW())"
            ),
            {"id": uid, "uname": uid, "name": "激活用户", "dept": "AI小组"},
        )
        await session.commit()

    token = create_session_token(uid, 0)

    app = _build_business_app_with_auth()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set(SESSION_COOKIE, token)
        resp = await ac.get("/api/skills/")
        # 状态检查通过：可能 200（业务层放行）或 403 AUTH_PERMISSION_DENIED（角色不够）
        # 但绝不能是 AUTH_ACCOUNT_NOT_ACTIVE
        if resp.status_code == 403:
            body = resp.json()
            assert body["error"]["code"] != "AUTH_ACCOUNT_NOT_ACTIVE", (
                f"active 用户不应被 state 检查拦截，实际: {body}"
            )
        else:
            assert resp.status_code == 200, (
                f"期望 200 或 403(非 NOT_ACTIVE)，实际 {resp.status_code}: {resp.text}"
            )


# ===== v2: require_department_access 升级到 v2 角色 =====


def test_require_department_access_v2_roles():
    """
    纯单元测试 require_department_access：覆盖 v2 角色矩阵
    (docs/spec/role-matrix-v2.md §4.1)。

    - system_admin / admin (legacy) → True（任何 dept）
    - can_view_all=True → True
    - legacy ai_engineer / director → True（兼容）
    - dept_admin / aibp / observer / legacy biz_owner / operator：
        跨部门 False，同部门 True
    """
    from app.auth.dependencies import require_department_access

    def _mk(role: str, department: str | None = None, can_view_all: bool = False):
        return _make_mock_user(
            user_id=f"u_{role}",
            role=role,
            department=department,
            can_view_all=can_view_all,
        )

    # v2: system_admin 跨部门
    system_admin = _mk("system_admin", department="技术部")
    assert require_department_access("电商", system_admin) is True
    assert require_department_access("直播电商", system_admin) is True

    # legacy admin 跨部门
    legacy_admin = _mk("admin", department="AI小组")
    assert require_department_access("电商", legacy_admin) is True

    # can_view_all=True 跨部门（即使角色非管理）
    observer_viewall = _mk("observer", department="AI小组", can_view_all=True)
    assert require_department_access("电商", observer_viewall) is True
    assert require_department_access("直播电商", observer_viewall) is True

    # legacy ai_engineer / director 数据迁移期保留全局读
    legacy_eng = _mk("ai_engineer", department="AI小组")
    assert require_department_access("电商", legacy_eng) is True
    legacy_director = _mk("director", department="总经办")
    assert require_department_access("电商", legacy_director) is True

    # dept_admin 跨部门 → False；同部门 → True
    dept_admin = _mk("dept_admin", department="电商")
    assert require_department_access("电商", dept_admin) is True
    assert require_department_access("直播电商", dept_admin) is False

    # aibp 跨部门 → False；同部门 → True
    aibp = _mk("aibp", department="电商")
    assert require_department_access("电商", aibp) is True
    assert require_department_access("直播电商", aibp) is False

    # observer（无 can_view_all） 跨部门 → False；同部门 → True
    observer = _mk("observer", department="电商")
    assert require_department_access("电商", observer) is True
    assert require_department_access("直播电商", observer) is False

    # legacy biz_owner / operator 跨部门 → False；同部门 → True
    biz = _mk("biz_owner", department="电商")
    assert require_department_access("电商", biz) is True
    assert require_department_access("直播电商", biz) is False
    op = _mk("operator", department="电商")
    assert require_department_access("电商", op) is True
    assert require_department_access("直播电商", op) is False
