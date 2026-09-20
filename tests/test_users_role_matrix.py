"""role-matrix-v2 用户切片测试。"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as db_mod
from app.auth.access import get_accessible_departments
from app.auth.models import User
from app.auth.dingtalk_oauth import handle_dingtalk_callback
from app.common.exceptions import AppError
from app.config import settings
from app.database import Base
from app.org.models import UserOrgMembership
from app.org.service import create_org_unit
from app.skills.members import SkillMember
from app.todos.models import AITodo, DecisionRequest
from app.users import service
from app.users.role_matrix import get_user_state, set_user_state


@pytest_asyncio.fixture
async def db():
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$",
        "/skillforge_test",
        str(settings.DATABASE_URL),
    )
    engine = create_async_engine(test_url, echo=False)

    # 注册所有与 skills/users FK 相关的模型，避免 drop_all 顺序错乱
    # （skill_instances / skill_releases / optimizer_sessions 等对 skills.id 有 FK）
    import app.auth.models  # noqa: F401
    import app.common.audit  # noqa: F401
    import app.execution.models  # noqa: F401
    import app.optimizer.models  # noqa: F401
    import app.org.models  # noqa: F401
    import app.skills.asset_models  # noqa: F401
    import app.skills.members  # noqa: F401
    import app.skills.models  # noqa: F401
    import app.todos.models  # noqa: F401

    from sqlalchemy import text as _sql_text
    async with engine.begin() as conn:
        # 强制清空 public schema，绕过历史遗留表 / ORM metadata 未覆盖表的 FK 依赖问题
        await conn.execute(_sql_text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(_sql_text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    original_engine, original_factory = db_mod.engine, db_mod.async_session_factory
    db_mod.engine, db_mod.async_session_factory = engine, factory

    async with factory() as session:
        yield session

    db_mod.engine, db_mod.async_session_factory = original_engine, original_factory
    await engine.dispose()


async def _create_user(
    session,
    *,
    user_id: str,
    role: str,
    state: str = "active",
    department: str | None = None,
    dingtalk_user_id: str | None = None,
) -> User:
    user = User(
        id=user_id,
        username=user_id,
        name=user_id,
        role=role,
        department=department,
        dingtalk_user_id=dingtalk_user_id,
        is_active=True,
        must_change_password=False,
    )
    session.add(user)
    await session.flush()
    await set_user_state(session, user, state)
    return user


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
            return _FakeResponse({"accessToken": "token-1"})
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url, headers=None):
        if url.endswith("/contact/users/me"):
            return _FakeResponse(
                {
                    "userId": "ding-user-1",
                    "unionId": "union-1",
                    "nick": "钉钉新人",
                    "avatarUrl": "https://example/avatar.png",
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
async def test_activate_pending_user_rejects_dept_admin_out_of_scope(db: AsyncSession):
    dept_a = await create_org_unit(db, unit_id="dept-a", name="部门A")
    dept_b = await create_org_unit(db, unit_id="dept-b", name="部门B")
    operator = await _create_user(db, user_id="dept-admin", role="dept_admin")
    pending_user = await _create_user(db, user_id="pending-1", role="observer", state="pending")
    db.add(UserOrgMembership(user_id=operator.id, org_unit_id=dept_a["id"], membership_type="primary", is_manager=True))
    db.add(UserOrgMembership(user_id=pending_user.id, org_unit_id=dept_b["id"], membership_type="primary", is_manager=False))
    await db.commit()

    with pytest.raises(AppError) as exc:
        await service.activate_pending_user(
            db,
            user_id=pending_user.id,
            new_role="aibp",
            org_unit_id=dept_a["id"],
            operator=operator,
        )

    assert exc.value.code == "AUTH_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_activate_pending_user_succeeds_and_bumps_permissions_rev(db: AsyncSession):
    dept = await create_org_unit(db, unit_id="dept-main", name="主部门")
    operator = await _create_user(db, user_id="sys-admin", role="system_admin")
    pending_user = await _create_user(db, user_id="pending-2", role="observer", state="pending")
    await db.commit()

    result = await service.activate_pending_user(
        db,
        user_id=pending_user.id,
        new_role="aibp",
        org_unit_id=dept["id"],
        is_manager=True,
        operator=operator,
    )
    await db.commit()

    assert result == {"id": "pending-2", "state": "active", "role": "aibp"}
    refreshed = await db.get(User, "pending-2")
    assert refreshed.role == "aibp"
    assert refreshed.department == "主部门"
    assert await get_user_state(db, refreshed) == "active"
    membership = (
        await db.execute(
            select(UserOrgMembership).where(
                UserOrgMembership.user_id == "pending-2",
                UserOrgMembership.org_unit_id == dept["id"],
            )
        )
    ).scalar_one()
    assert membership.is_manager is True
    row = (
        await db.execute(
            text("SELECT permissions_rev FROM users WHERE id = 'pending-2'")
        )
    ).first()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_system_admin_can_activate_pending_user_as_dept_admin(db: AsyncSession):
    dept = await create_org_unit(db, unit_id="dept-admin-activate", name="可管部门")
    operator = await _create_user(db, user_id="sys-admin-activator", role="system_admin")
    pending_user = await _create_user(db, user_id="pending-dept-admin", role="observer", state="pending")
    await db.commit()

    result = await service.activate_pending_user(
        db,
        user_id=pending_user.id,
        new_role="dept_admin",
        org_unit_id=dept["id"],
        operator=operator,
    )
    await db.commit()

    refreshed = await db.get(User, pending_user.id)
    assert result == {"id": "pending-dept-admin", "state": "active", "role": "dept_admin"}
    assert refreshed.role == "dept_admin"
    assert await get_user_state(db, refreshed) == "active"
    membership = (
        await db.execute(
            select(UserOrgMembership).where(
                UserOrgMembership.user_id == pending_user.id,
                UserOrgMembership.org_unit_id == dept["id"],
            )
        )
    ).scalar_one()
    assert membership.is_manager is True


@pytest.mark.asyncio
async def test_dept_admin_cannot_activate_pending_user_as_dept_admin(db: AsyncSession):
    dept = await create_org_unit(db, unit_id="dept-admin-denied", name="管辖部门")
    operator = await _create_user(db, user_id="dept-admin-activator", role="dept_admin")
    pending_user = await _create_user(db, user_id="pending-promote-denied", role="observer", state="pending")
    db.add(UserOrgMembership(user_id=operator.id, org_unit_id=dept["id"], membership_type="primary", is_manager=True))
    db.add(UserOrgMembership(user_id=pending_user.id, org_unit_id=dept["id"], membership_type="primary", is_manager=False))
    await db.commit()

    with pytest.raises(AppError) as exc:
        await service.activate_pending_user(
            db,
            user_id=pending_user.id,
            new_role="dept_admin",
            org_unit_id=dept["id"],
            operator=operator,
        )

    assert exc.value.code == "DEPT_ADMIN_CANNOT_PROMOTE"


@pytest.mark.asyncio
async def test_create_user_accepts_role_matrix_roles(db: AsyncSession):
    result = await service.create_user(
        db,
        user_id="created-system-admin",
        username="created-system-admin",
        name="Created Admin",
        role="system_admin",
        password="SkillForge@2026",
        operator_id="seed-admin",
    )
    await db.commit()

    refreshed = await db.get(User, "created-system-admin")
    assert result["id"] == "created-system-admin"
    assert refreshed.role == "system_admin"
    assert refreshed.is_active is True


@pytest.mark.asyncio
async def test_update_user_state_enables_disabled_user_and_bumps_permissions_rev(db: AsyncSession):
    operator = await _create_user(db, user_id="state-admin", role="system_admin")
    target = await _create_user(db, user_id="state-target", role="observer", state="disabled")
    await db.commit()

    result = await service.update_user_state(
        db,
        user_id=target.id,
        state="active",
        operator=operator,
    )
    await db.commit()

    refreshed = await db.get(User, target.id)
    assert result == {"id": "state-target", "state": "active", "is_active": True}
    assert await get_user_state(db, refreshed) == "active"
    assert refreshed.is_active is True
    row = (
        await db.execute(
            text("SELECT permissions_rev FROM users WHERE id = 'state-target'")
        )
    ).first()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_update_user_state_rejects_disabling_last_system_admin(db: AsyncSession):
    operator = await _create_user(db, user_id="only-admin", role="system_admin")
    await db.commit()

    with pytest.raises(AppError) as exc:
        await service.update_user_state(
            db,
            user_id=operator.id,
            state="disabled",
            operator=operator,
        )

    assert exc.value.code == "LAST_SYSTEM_ADMIN"


@pytest.mark.asyncio
async def test_update_user_rejects_demoting_last_system_admin(db: AsyncSession):
    operator = await _create_user(db, user_id="demote-only-admin", role="system_admin")
    await db.commit()

    with pytest.raises(AppError) as exc:
        await service.update_user(
            db,
            user_id=operator.id,
            role="observer",
            operator=operator,
        )

    assert exc.value.code == "LAST_SYSTEM_ADMIN"
    refreshed = await db.get(User, operator.id)
    assert refreshed.role == "system_admin"


@pytest.mark.asyncio
async def test_update_user_syncs_state_when_disabling_regular_user(db: AsyncSession):
    operator = await _create_user(db, user_id="state-sync-admin", role="system_admin")
    target = await _create_user(db, user_id="state-sync-target", role="observer")
    await db.commit()

    result = await service.update_user(
        db,
        user_id=target.id,
        is_active=False,
        operator=operator,
    )
    await db.commit()

    refreshed = await db.get(User, target.id)
    assert result["id"] == target.id
    assert refreshed.is_active is False
    assert await get_user_state(db, refreshed) == "disabled"


@pytest.mark.asyncio
async def test_list_users_scopes_dept_admin_to_managed_departments(db: AsyncSession):
    dept_a = await create_org_unit(db, unit_id="dept-users-a", name="用户部A")
    dept_b = await create_org_unit(db, unit_id="dept-users-b", name="用户部B")
    operator = await _create_user(db, user_id="dept-admin-users", role="dept_admin")
    target_a = await _create_user(db, user_id="managed-observer", role="observer")
    target_b = await _create_user(db, user_id="cross-observer-list", role="observer")
    db.add_all(
        [
            UserOrgMembership(user_id=operator.id, org_unit_id=dept_a["id"], membership_type="primary", is_manager=True),
            UserOrgMembership(user_id=target_a.id, org_unit_id=dept_a["id"], membership_type="primary", is_manager=False),
            UserOrgMembership(user_id=target_b.id, org_unit_id=dept_b["id"], membership_type="primary", is_manager=False),
        ]
    )
    await db.commit()

    result = await service.list_users(db, operator=operator, page_size=100)

    ids = {item["id"] for item in result["items"]}
    assert result["total"] == 2
    assert ids == {"dept-admin-users", "managed-observer"}


@pytest.mark.asyncio
async def test_dept_admin_can_update_managed_member_role_between_aibp_observer(db: AsyncSession):
    dept = await create_org_unit(db, unit_id="dept-update-managed", name="可管用户部")
    operator = await _create_user(db, user_id="dept-admin-update", role="dept_admin")
    target = await _create_user(db, user_id="managed-role-target", role="observer", department="可管用户部")
    db.add_all(
        [
            UserOrgMembership(user_id=operator.id, org_unit_id=dept["id"], membership_type="primary", is_manager=True),
            UserOrgMembership(user_id=target.id, org_unit_id=dept["id"], membership_type="primary", is_manager=False),
        ]
    )
    await db.commit()

    result = await service.update_user(
        db,
        user_id=target.id,
        role="aibp",
        department="可管用户部",
        operator=operator,
    )
    await db.commit()

    refreshed = await db.get(User, target.id)
    assert result["role"] == "aibp"
    assert refreshed.role == "aibp"


@pytest.mark.asyncio
async def test_dept_admin_cannot_update_cross_department_user(db: AsyncSession):
    dept_a = await create_org_unit(db, unit_id="dept-update-a", name="更新A")
    dept_b = await create_org_unit(db, unit_id="dept-update-b", name="更新B")
    operator = await _create_user(db, user_id="dept-admin-update-denied", role="dept_admin")
    target = await _create_user(db, user_id="cross-role-target", role="observer")
    db.add_all(
        [
            UserOrgMembership(user_id=operator.id, org_unit_id=dept_a["id"], membership_type="primary", is_manager=True),
            UserOrgMembership(user_id=target.id, org_unit_id=dept_b["id"], membership_type="primary", is_manager=False),
        ]
    )
    await db.commit()

    with pytest.raises(AppError) as exc:
        await service.update_user(db, user_id=target.id, role="aibp", operator=operator)

    assert exc.value.code == "AUTH_PERMISSION_DENIED"
    refreshed = await db.get(User, target.id)
    assert refreshed.role == "observer"


@pytest.mark.asyncio
async def test_dept_admin_cannot_promote_user_to_dept_admin(db: AsyncSession):
    dept = await create_org_unit(db, unit_id="dept-promote-denied", name="禁止提权部")
    operator = await _create_user(db, user_id="dept-admin-promote-denied", role="dept_admin")
    target = await _create_user(db, user_id="promote-target", role="observer")
    db.add_all(
        [
            UserOrgMembership(user_id=operator.id, org_unit_id=dept["id"], membership_type="primary", is_manager=True),
            UserOrgMembership(user_id=target.id, org_unit_id=dept["id"], membership_type="primary", is_manager=False),
        ]
    )
    await db.commit()

    with pytest.raises(AppError) as exc:
        await service.update_user(db, user_id=target.id, role="dept_admin", operator=operator)

    assert exc.value.code == "DEPT_ADMIN_CANNOT_PROMOTE"


@pytest.mark.asyncio
async def test_dept_admin_can_reset_password_for_managed_member(db: AsyncSession):
    dept = await create_org_unit(db, unit_id="dept-reset-managed", name="重置密码部")
    operator = await _create_user(db, user_id="dept-admin-reset", role="dept_admin")
    target = await _create_user(db, user_id="reset-target", role="aibp")
    db.add_all(
        [
            UserOrgMembership(user_id=operator.id, org_unit_id=dept["id"], membership_type="primary", is_manager=True),
            UserOrgMembership(user_id=target.id, org_unit_id=dept["id"], membership_type="primary", is_manager=False),
        ]
    )
    await db.commit()

    result = await service.reset_password(db, target.id, "SkillForge@2026", operator=operator)
    await db.commit()

    refreshed = await db.get(User, target.id)
    assert result == {"id": "reset-target", "must_change_password": True}
    assert refreshed.password_hash
    assert refreshed.must_change_password is True


@pytest.mark.asyncio
async def test_dept_admin_can_disable_managed_observer(db: AsyncSession):
    dept = await create_org_unit(db, unit_id="dept-disable-managed", name="停用用户部")
    operator = await _create_user(db, user_id="dept-admin-disable", role="dept_admin")
    target = await _create_user(db, user_id="disable-target", role="observer")
    db.add_all(
        [
            UserOrgMembership(user_id=operator.id, org_unit_id=dept["id"], membership_type="primary", is_manager=True),
            UserOrgMembership(user_id=target.id, org_unit_id=dept["id"], membership_type="primary", is_manager=False),
        ]
    )
    await db.commit()

    result = await service.disable_user(db, target.id, operator=operator)
    await db.commit()

    refreshed = await db.get(User, target.id)
    assert result == {"id": "disable-target", "state": "disabled"}
    assert await get_user_state(db, refreshed) == "disabled"


@pytest.mark.asyncio
async def test_dept_admin_cannot_disable_peer_dept_admin(db: AsyncSession):
    dept = await create_org_unit(db, unit_id="dept-disable-peer", name="同级管理员部")
    operator = await _create_user(db, user_id="dept-admin-disable-peer", role="dept_admin")
    peer = await _create_user(db, user_id="peer-dept-admin", role="dept_admin")
    db.add_all(
        [
            UserOrgMembership(user_id=operator.id, org_unit_id=dept["id"], membership_type="primary", is_manager=True),
            UserOrgMembership(user_id=peer.id, org_unit_id=dept["id"], membership_type="primary", is_manager=True),
        ]
    )
    await db.commit()

    with pytest.raises(AppError) as exc:
        await service.disable_user(db, peer.id, operator=operator)

    assert exc.value.code == "AUTH_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_unlink_dingtalk_clears_binding_and_bumps_permissions_rev(db: AsyncSession):
    operator = await _create_user(db, user_id="unlink-admin", role="system_admin")
    target = await _create_user(
        db,
        user_id="ding-bound",
        role="observer",
        dingtalk_user_id="ding-user-1",
    )
    target.dingtalk_union_id = "union-user-1"
    await db.commit()

    result = await service.unlink_dingtalk(
        db,
        user_id=target.id,
        operator=operator,
    )
    await db.commit()

    refreshed = await db.get(User, target.id)
    assert result == {"id": "ding-bound", "dingtalk_user_id": None}
    assert refreshed.dingtalk_user_id is None
    assert refreshed.dingtalk_union_id is None
    row = (
        await db.execute(
            text("SELECT permissions_rev FROM users WHERE id = 'ding-bound'")
        )
    ).first()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_disable_last_system_admin_is_hard_locked(db: AsyncSession):
    admin = await _create_user(db, user_id="last-admin", role="system_admin")
    successor = await _create_user(db, user_id="successor", role="observer")
    await db.commit()

    with pytest.raises(AppError) as exc:
        await service.disable_user(
            db,
            user_id=admin.id,
            successor_user_id=successor.id,
            operator=admin,
        )

    assert exc.value.code == "LAST_SYSTEM_ADMIN"


@pytest.mark.asyncio
async def test_disable_user_transfers_skill_owner_and_pending_todos(db: AsyncSession):
    dept = await create_org_unit(db, unit_id="dept-transfer", name="转交部门")
    operator = await _create_user(db, user_id="dept-admin-2", role="dept_admin")
    target = await _create_user(db, user_id="target-user", role="aibp", department="转交部门")
    successor = await _create_user(db, user_id="successor-user", role="aibp", department="转交部门")
    db.add_all(
        [
            UserOrgMembership(user_id=operator.id, org_unit_id=dept["id"], membership_type="primary", is_manager=True),
            UserOrgMembership(user_id=target.id, org_unit_id=dept["id"], membership_type="primary", is_manager=False),
            UserOrgMembership(user_id=successor.id, org_unit_id=dept["id"], membership_type="primary", is_manager=False),
            SkillMember(skill_id="skill-transfer", user_id=target.id, role="owner"),
            DecisionRequest(
                id="req-transfer",
                source_type="skill",
                source_id="skill-transfer",
                skill_id="skill-transfer",
                title="审批",
                sla_at=target.created_at,
            ),
        ]
    )
    await db.flush()
    db.add(AITodo(request_id="req-transfer", assignee=target.id, kind="review"))
    await db.commit()

    result = await service.disable_user(
        db,
        user_id=target.id,
        successor_user_id=successor.id,
        operator=operator,
    )
    await db.commit()

    assert result == {"id": "target-user", "state": "disabled"}
    disabled_user = await db.get(User, target.id)
    assert await get_user_state(db, disabled_user) == "disabled"
    moved_owner = (
        await db.execute(
            select(SkillMember).where(SkillMember.skill_id == "skill-transfer")
        )
    ).scalar_one()
    assert moved_owner.user_id == successor.id
    todo = (
        await db.execute(select(AITodo).where(AITodo.request_id == "req-transfer"))
    ).scalar_one()
    assert todo.assignee == successor.id


@pytest.mark.asyncio
async def test_list_pending_users_is_scoped_to_managed_departments(db: AsyncSession):
    dept_a = await create_org_unit(db, unit_id="dept-p1", name="部门P1")
    dept_b = await create_org_unit(db, unit_id="dept-p2", name="部门P2")
    operator = await _create_user(db, user_id="dept-admin-3", role="dept_admin")
    pending_a = await _create_user(db, user_id="pending-a", role="observer", state="pending")
    pending_b = await _create_user(db, user_id="pending-b", role="observer", state="pending")
    db.add_all(
        [
            UserOrgMembership(user_id=operator.id, org_unit_id=dept_a["id"], membership_type="primary", is_manager=True),
            UserOrgMembership(user_id=pending_a.id, org_unit_id=dept_a["id"], membership_type="primary", is_manager=False),
            UserOrgMembership(user_id=pending_b.id, org_unit_id=dept_b["id"], membership_type="primary", is_manager=False),
        ]
    )
    await db.commit()

    result = await service.list_pending_users(db, operator=operator)

    assert result["total"] == 1
    assert result["items"][0]["id"] == "pending-a"
    assert result["items"][0]["dingtalk_department_id"] == dept_a["id"]


@pytest.mark.asyncio
async def test_dingtalk_first_login_creates_active_aibp(monkeypatch, db: AsyncSession):
    monkeypatch.setattr("app.auth.dingtalk_oauth.httpx.AsyncClient", _FakeHttpxClient)
    monkeypatch.setattr("app.auth.dingtalk_oauth.dingtalk_client.get_user_detail", _corp_member)
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_KEY", "app-key")
    monkeypatch.setattr("app.auth.dingtalk_oauth.settings.DINGTALK_APP_SECRET", "app-secret")

    user = await handle_dingtalk_callback(db, "auth-code")
    await db.commit()

    assert user is not None
    assert user.role == "aibp"
    assert user.dingtalk_user_id == "ding-user-1"
    assert await get_user_state(db, user) == "active"


@pytest.mark.asyncio
async def test_dept_admin_subtree_expansion_in_read_scope(db: AsyncSession):
    """spec §3.2: dept_admin 读范围 = 关联部门直读 + 管辖部门子树展开。

    构造树：
      root → deptA → deptA1 → deptA2
                   (A 同级) deptB → deptB1

    manager：
      - deptA (is_manager=True) — 管辖，应展开到 deptA/A1/A2
      - deptB (is_manager=False) — 关联（直读），**不**展开到 deptB1
    """
    root = await create_org_unit(db, unit_id="org-root", name="root")
    dept_a = await create_org_unit(db, unit_id="org-a", name="A", parent_id=root["id"])
    dept_a1 = await create_org_unit(db, unit_id="org-a1", name="A1", parent_id=dept_a["id"])
    dept_a2 = await create_org_unit(db, unit_id="org-a2", name="A2", parent_id=dept_a1["id"])
    dept_b = await create_org_unit(db, unit_id="org-b", name="B", parent_id=root["id"])
    dept_b1 = await create_org_unit(db, unit_id="org-b1", name="B1", parent_id=dept_b["id"])

    manager = await _create_user(db, user_id="dept-admin-subtree", role="dept_admin")
    db.add_all(
        [
            UserOrgMembership(
                user_id=manager.id,
                org_unit_id=dept_a["id"],
                membership_type="primary",
                is_manager=True,
            ),
            UserOrgMembership(
                user_id=manager.id,
                org_unit_id=dept_b["id"],
                membership_type="secondary",
                is_manager=False,
            ),
        ]
    )
    await db.commit()

    scope = await get_accessible_departments(db, manager)

    # 管辖部门 deptA 及其子树 A1 / A2 应在读范围内
    assert dept_a["id"] in scope
    assert dept_a1["id"] in scope
    assert dept_a2["id"] in scope
    # 关联但非管辖部门 deptB 直读
    assert dept_b["id"] in scope
    # 关键断言：deptB 的子部门 B1 **不**在读范围内（非管辖 → 不穿透子树）
    assert dept_b1["id"] not in scope
    # root 不应被误包含（既非关联也非管辖子树）
    assert root["id"] not in scope

    assert scope == {dept_a["id"], dept_a1["id"], dept_a2["id"], dept_b["id"]}


@pytest.mark.asyncio
async def test_get_accessible_departments_system_admin_and_can_view_all_bypass(db: AsyncSession):
    """system_admin / can_view_all=True 跳过过滤，返回 None（全部可见）。"""
    await create_org_unit(db, unit_id="org-bypass", name="X")
    sys_admin = await _create_user(db, user_id="sys-bypass", role="system_admin")
    cross_observer = await _create_user(db, user_id="cross-observer", role="observer")
    cross_observer.can_view_all = True
    await db.flush()
    await db.commit()

    assert await get_accessible_departments(db, sys_admin) is None
    assert await get_accessible_departments(db, cross_observer) is None


@pytest.mark.asyncio
async def test_get_accessible_departments_observer_still_expands_all_related(
    db: AsyncSession,
):
    """spec §3.4: observer 的所有关联部门（不限 is_manager）均向下展开到子树。

    与 dept_admin 分支的区别：observer 不区分 is_manager，全部展开。
    """
    root = await create_org_unit(db, unit_id="obs-root", name="root")
    dept_x = await create_org_unit(db, unit_id="obs-x", name="X", parent_id=root["id"])
    dept_x1 = await create_org_unit(db, unit_id="obs-x1", name="X1", parent_id=dept_x["id"])
    dept_y = await create_org_unit(db, unit_id="obs-y", name="Y", parent_id=root["id"])
    dept_y1 = await create_org_unit(db, unit_id="obs-y1", name="Y1", parent_id=dept_y["id"])

    observer = await _create_user(db, user_id="observer-subtree", role="observer")
    db.add_all(
        [
            UserOrgMembership(
                user_id=observer.id,
                org_unit_id=dept_x["id"],
                membership_type="primary",
                is_manager=False,
            ),
            UserOrgMembership(
                user_id=observer.id,
                org_unit_id=dept_y["id"],
                membership_type="secondary",
                is_manager=False,
            ),
        ]
    )
    await db.commit()

    scope = await get_accessible_departments(db, observer)

    # observer：关联的 X / Y 都展开到 X1 / Y1
    assert dept_x["id"] in scope
    assert dept_x1["id"] in scope
    assert dept_y["id"] in scope
    assert dept_y1["id"] in scope
    # root 不在关联范围内
    assert root["id"] not in scope
