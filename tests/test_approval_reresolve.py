"""role-matrix-v2 §6.5 审批链重新解析测试。

验证：
- approver_resolver._resolve_dept_admin 排除 state=disabled 的 dept_admin
- disable_user / update_user 触发 reresolve，未决审批 todo 被重分配或标为 resolved_by_peer
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as db_mod
from app.approval.approver_resolver import resolve_approvers
from app.approval.models import ApprovalInstance, ApprovalRule, ApprovalStep
from app.approval.reresolve import reresolve_affected_decision_requests
from app.auth.models import User
from app.common.exceptions import AppError
from app.config import settings
from app.database import Base
from app.org.models import UserOrgMembership
from app.org.service import create_org_unit
from app.todos.models import AITodo, DecisionRequest
from app.users import service as user_service
from app.users.role_matrix import set_user_state


@pytest_asyncio.fixture
async def db():
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$",
        "/skillforge_test",
        str(settings.DATABASE_URL),
    )
    engine = create_async_engine(test_url, echo=False)

    import app.approval.models  # noqa: F401
    import app.auth.models  # noqa: F401
    import app.common.audit  # noqa: F401
    import app.execution.models  # noqa: F401
    import app.notifications.models  # noqa: F401
    import app.optimizer.models  # noqa: F401
    import app.org.models  # noqa: F401
    import app.skills.asset_models  # noqa: F401
    import app.skills.members  # noqa: F401
    import app.skills.models  # noqa: F401
    import app.testing.models  # noqa: F401
    import app.todos.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    original_engine, original_factory = db_mod.engine, db_mod.async_session_factory
    db_mod.engine, db_mod.async_session_factory = engine, factory

    async with factory() as session:
        yield session

    db_mod.engine, db_mod.async_session_factory = original_engine, original_factory
    await engine.dispose()


async def _fetch_permissions_rev(session: AsyncSession, user_id: str) -> int:
    """绕过 ORM 缓存直接读 users.permissions_rev（bump 走 raw SQL，object 会 stale）。"""
    from sqlalchemy import text as sql_text

    row = (
        await session.execute(
            sql_text("SELECT permissions_rev FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
    ).first()
    return int(row[0]) if row else 0


async def _create_user(
    session: AsyncSession,
    *,
    user_id: str,
    role: str,
    state: str = "active",
    department: str | None = None,
) -> User:
    user = User(
        id=user_id,
        username=user_id,
        name=user_id,
        role=role,
        department=department,
        is_active=True,
        must_change_password=False,
    )
    session.add(user)
    await session.flush()
    await set_user_state(session, user, state)
    return user


@pytest.mark.asyncio
async def test_disabled_approver_excluded_from_resolution(db: AsyncSession):
    """state=disabled 的 dept_admin 不应被 approver_resolver 返回。"""
    dept = await create_org_unit(db, unit_id="dept-resolve", name="解析部门")

    disabled_admin = await _create_user(
        db, user_id="disabled-admin", role="dept_admin", department="解析部门"
    )
    active_admin = await _create_user(
        db, user_id="active-admin", role="dept_admin", department="解析部门"
    )
    db.add_all(
        [
            UserOrgMembership(
                user_id=disabled_admin.id,
                org_unit_id=dept["id"],
                membership_type="primary",
                is_manager=True,
            ),
            UserOrgMembership(
                user_id=active_admin.id,
                org_unit_id=dept["id"],
                membership_type="primary",
                is_manager=True,
            ),
        ]
    )
    await db.flush()

    # 禁用掉 disabled_admin
    await set_user_state(db, disabled_admin, "disabled")
    await db.commit()

    # resolver 只应返回 active_admin
    result = await resolve_approvers(
        db,
        {"type": "dept_admin", "department": dept["id"]},
        {},
    )
    assert result == ["active-admin"]

    # 如果把最后一个 active dept_admin 也禁了，resolver 应报错（而不是返回 disabled 账号）
    await set_user_state(db, active_admin, "disabled")
    await db.commit()
    with pytest.raises(AppError) as exc:
        await resolve_approvers(
            db,
            {"type": "dept_admin", "department": dept["id"]},
            {},
        )
    assert exc.value.code == "APPROVER_NOT_FOUND"


@pytest.mark.asyncio
async def test_pending_approver_excluded_from_resolution(db: AsyncSession):
    """state=pending（钉钉首登未激活）的 dept_admin 不应被 approver_resolver 返回。

    回归保护 approver_resolver._active_user_filter：该过滤器曾经只校验
    is_active=True，pending 用户（is_active=True, state='pending'）会漏过被选为审批人。
    """
    dept = await create_org_unit(db, unit_id="dept-pending", name="Pending 部门")

    pending_admin = await _create_user(
        db,
        user_id="pending-admin",
        role="dept_admin",
        state="pending",
        department="Pending 部门",
    )
    active_admin = await _create_user(
        db, user_id="active-admin-pending", role="dept_admin", department="Pending 部门"
    )
    db.add_all(
        [
            UserOrgMembership(
                user_id=pending_admin.id,
                org_unit_id=dept["id"],
                membership_type="primary",
                is_manager=True,
            ),
            UserOrgMembership(
                user_id=active_admin.id,
                org_unit_id=dept["id"],
                membership_type="primary",
                is_manager=True,
            ),
        ]
    )
    await db.flush()
    await db.commit()

    # resolver 只应返回 active_admin，不应把 pending_admin 列为候选
    result = await resolve_approvers(
        db,
        {"type": "dept_admin", "department": dept["id"]},
        {},
    )
    assert result == ["active-admin-pending"]


@pytest.mark.asyncio
async def test_update_user_role_change_bumps_permissions_rev(db: AsyncSession):
    """update_user 修改 role 后必须 bump permissions_rev（§10.2）。

    回归保护：旧版本只触发 reresolve，漏了 bump，导致旧 session 不失效。
    """
    await create_org_unit(db, unit_id="dept-bump", name="Bump 部门")
    system_admin = await _create_user(
        db, user_id="sys-admin-bump", role="system_admin"
    )
    target = await _create_user(
        db, user_id="bump-target", role="dept_admin", department="Bump 部门"
    )
    await db.commit()

    rev_before = int(getattr(target, "permissions_rev", 0) or 0)
    await user_service.update_user(
        db,
        user_id=target.id,
        role="aibp",
        operator=system_admin,
    )
    await db.commit()

    rev_after = await _fetch_permissions_rev(db, target.id)
    assert rev_after == rev_before + 1, (
        f"role 变更后 permissions_rev 应 +1，实际 {rev_before} -> {rev_after}"
    )


@pytest.mark.asyncio
async def test_disable_user_bumps_permissions_rev(db: AsyncSession):
    """disable_user 必须 bump permissions_rev，让被禁用者的旧 session 立即失效。"""
    await create_org_unit(db, unit_id="dept-disable-bump", name="禁用 Bump 部门")
    system_admin = await _create_user(
        db, user_id="sys-admin-disable-bump", role="system_admin"
    )
    target = await _create_user(
        db,
        user_id="disable-bump-target",
        role="aibp",
        department="禁用 Bump 部门",
    )
    await db.commit()

    rev_before = int(getattr(target, "permissions_rev", 0) or 0)
    await user_service.disable_user(
        db,
        user_id=target.id,
        operator=system_admin,
    )
    await db.commit()

    rev_after = await _fetch_permissions_rev(db, target.id)
    assert rev_after == rev_before + 1, (
        f"disable 后 permissions_rev 应 +1，实际 {rev_before} -> {rev_after}"
    )


async def _seed_pending_approval(
    db: AsyncSession,
    *,
    dept_id: str,
    dept_name: str,
    assignee_id: str,
) -> tuple[ApprovalInstance, DecisionRequest, AITodo, ApprovalStep]:
    rule = ApprovalRule(
        id="rule-reresolve",
        business_type="skill_publish",
        condition_json=None,
        approval_chain=[{"type": "dept_admin", "department": dept_id}],
        enabled=True,
        priority=1,
    )
    instance = ApprovalInstance(
        id="ai-reresolve-1",
        rule_id=rule.id,
        business_type="skill_publish",
        business_id="skill-xyz",
        requester_id="requester-xyz",
        status="pending",
        current_step=1,
        payload={"skill_department": dept_id},
    )
    step = ApprovalStep(
        id="as-reresolve-1",
        instance_id=instance.id,
        step_order=1,
        approver_id=assignee_id,
        status="pending",
    )
    request = DecisionRequest(
        id=f"dr-approval-{instance.id}-1",
        source_type="approval_step",
        source_id=f"{instance.id}:1",
        skill_id="skill-xyz",
        kind="review",
        title="skill_publish 审批",
        summary="待审批",
        payload={
            "instance_id": instance.id,
            "step_order": 1,
            "business_type": "skill_publish",
            "business_id": "skill-xyz",
        },
        decision_mode="any_of",
        aggregate_status="pending",
        sla_at=datetime.utcnow() + timedelta(hours=24),
    )
    db.add_all([rule, instance, step, request])
    await db.flush()
    todo = AITodo(request_id=request.id, kind="review", assignee=assignee_id)
    db.add(todo)
    await db.flush()
    return instance, request, todo, step


@pytest.mark.asyncio
async def test_reresolve_on_user_disable_flips_pending_todos(db: AsyncSession):
    """disable_user 后 approval 待办不再挂在该用户下。"""
    dept = await create_org_unit(db, unit_id="dept-flip", name="切换部门")
    system_admin = await _create_user(
        db, user_id="sys-admin-flip", role="system_admin"
    )
    target_admin = await _create_user(
        db, user_id="target-admin", role="dept_admin", department="切换部门"
    )
    peer_admin = await _create_user(
        db, user_id="peer-admin", role="dept_admin", department="切换部门"
    )
    successor = await _create_user(
        db, user_id="successor-admin", role="dept_admin", department="切换部门"
    )
    db.add_all(
        [
            UserOrgMembership(
                user_id=target_admin.id,
                org_unit_id=dept["id"],
                membership_type="primary",
                is_manager=True,
            ),
            UserOrgMembership(
                user_id=peer_admin.id,
                org_unit_id=dept["id"],
                membership_type="primary",
                is_manager=True,
            ),
            UserOrgMembership(
                user_id=successor.id,
                org_unit_id=dept["id"],
                membership_type="primary",
                is_manager=True,
            ),
        ]
    )
    await db.flush()

    instance, request, target_todo, step = await _seed_pending_approval(
        db,
        dept_id=dept["id"],
        dept_name=dept["name"],
        assignee_id=target_admin.id,
    )
    await db.commit()

    # 禁用 target_admin
    await user_service.disable_user(
        db,
        user_id=target_admin.id,
        successor_user_id=successor.id,
        operator=system_admin,
    )
    await db.commit()

    # target_admin 的 todo 应被标记 resolved_by_peer
    refreshed_target_todo = await db.get(AITodo, target_todo.id)
    assert refreshed_target_todo.status == "resolved_by_peer"
    assert refreshed_target_todo.decision_channel == "reresolve"

    # 该审批请求下应该有新候选（peer_admin / successor）作为 assignee 的 pending todo
    rows = (
        await db.execute(
            select(AITodo).where(
                AITodo.request_id == request.id,
                AITodo.status == "pending",
            )
        )
    ).scalars().all()
    new_assignees = {row.assignee for row in rows}
    assert target_admin.id not in new_assignees
    # 至少包含 peer_admin，successor 视 resolver 输出而定
    assert peer_admin.id in new_assignees

    # request 仍保持 pending（没被提前关闭）
    refreshed_request = await db.get(DecisionRequest, request.id)
    assert refreshed_request.aggregate_status == "pending"


@pytest.mark.asyncio
async def test_reresolve_on_role_downgrade_flips_pending_todos(db: AsyncSession):
    """update_user 把 dept_admin 降权为 aibp → reresolve 触发。"""
    dept = await create_org_unit(db, unit_id="dept-down", name="降级部门")
    system_admin = await _create_user(
        db, user_id="sys-admin-down", role="system_admin"
    )
    target = await _create_user(
        db, user_id="downgrade-target", role="dept_admin", department="降级部门"
    )
    peer = await _create_user(
        db, user_id="downgrade-peer", role="dept_admin", department="降级部门"
    )
    db.add_all(
        [
            UserOrgMembership(
                user_id=target.id,
                org_unit_id=dept["id"],
                membership_type="primary",
                is_manager=True,
            ),
            UserOrgMembership(
                user_id=peer.id,
                org_unit_id=dept["id"],
                membership_type="primary",
                is_manager=True,
            ),
        ]
    )
    await db.flush()

    instance, request, target_todo, _ = await _seed_pending_approval(
        db,
        dept_id=dept["id"],
        dept_name=dept["name"],
        assignee_id=target.id,
    )
    await db.commit()

    # 降权为 aibp
    await user_service.update_user(
        db,
        user_id=target.id,
        role="aibp",
        operator=system_admin,
    )
    await db.commit()

    refreshed_target_todo = await db.get(AITodo, target_todo.id)
    assert refreshed_target_todo.status == "resolved_by_peer"

    pending = (
        await db.execute(
            select(AITodo).where(
                AITodo.request_id == request.id,
                AITodo.status == "pending",
            )
        )
    ).scalars().all()
    new_assignees = {row.assignee for row in pending}
    assert target.id not in new_assignees
    assert peer.id in new_assignees
