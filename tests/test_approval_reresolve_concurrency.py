"""H1 + H2 回归：reresolve 并发安全 + _trigger_reresolve 异常告警。

H1：同一 user 的并发 reresolve 调用必须串行（PostgreSQL advisory xact lock）。
H2：reresolve 抛 SQLAlchemyError 时，_trigger_reresolve 不静默吞，必须 audit 告警。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as db_mod
from app.approval.models import ApprovalInstance, ApprovalRule, ApprovalStep
from app.approval.reresolve import reresolve_affected_decision_requests
from app.auth.models import User
from app.common.audit import AuditLog
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
    # 先用裸 asyncpg 把 skillforge_test 上其他连接（前一轮 test 的 pool 残留）
    # 全部 terminate，避免 DROP SCHEMA 拿不到 AccessExclusiveLock 卡死。
    import asyncpg
    plain_url = test_url.replace("+asyncpg", "")
    _kill_conn = await asyncpg.connect(plain_url)
    try:
        await _kill_conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=current_database() AND pid <> pg_backend_pid()"
        )
    finally:
        await _kill_conn.close()

    engine = create_async_engine(test_url, echo=False, pool_size=5, max_overflow=0)

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
    # 拍死可能残留的连接，避免下个 fixture 的 DROP SCHEMA 拿不到 lock
    _close_conn = await asyncpg.connect(plain_url)
    try:
        await _close_conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=current_database() AND pid <> pg_backend_pid() AND state='idle'"
        )
    finally:
        await _close_conn.close()


@pytest_asyncio.fixture
async def session_factory(db):
    """需要并发跑两路 reresolve 时复用同一 engine 的 session 工厂。"""
    return db_mod.async_session_factory


async def _create_user(
    session: AsyncSession,
    *,
    user_id: str,
    role: str,
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
    await set_user_state(session, user, "active")
    return user


async def _seed_pending_approval(
    db: AsyncSession,
    *,
    dept_id: str,
    assignee_id: str,
    instance_id: str = "ai-conc-1",
) -> tuple[ApprovalInstance, DecisionRequest, AITodo, ApprovalStep]:
    rule = ApprovalRule(
        id=f"rule-{instance_id}",
        business_type="skill_publish",
        condition_json=None,
        approval_chain=[{"type": "dept_admin", "department": dept_id}],
        enabled=True,
        priority=1,
    )
    instance = ApprovalInstance(
        id=instance_id,
        rule_id=rule.id,
        business_type="skill_publish",
        business_id=f"skill-{instance_id}",
        requester_id="requester-conc",
        status="pending",
        current_step=1,
        payload={"skill_department": dept_id},
    )
    step = ApprovalStep(
        id=f"as-{instance_id}",
        instance_id=instance.id,
        step_order=1,
        approver_id=assignee_id,
        status="pending",
    )
    request = DecisionRequest(
        id=f"dr-approval-{instance.id}-1",
        source_type="approval_step",
        source_id=f"{instance.id}:1",
        skill_id=f"skill-{instance_id}",
        kind="review",
        title="并发审批",
        summary="待审批",
        payload={
            "instance_id": instance.id,
            "step_order": 1,
            "business_type": "skill_publish",
            "business_id": f"skill-{instance_id}",
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
async def test_concurrent_reresolve_serializes_via_advisory_lock(db: AsyncSession, session_factory):
    """H1：同一 user 的两个并发 reresolve 不应抛异常、最终状态一致。

    用 asyncio.gather 并发 reresolve_affected_decision_requests，
    advisory xact lock 应让两个事务串行通过临界区。
    """
    dept = await create_org_unit(db, unit_id="dept-conc", name="并发部门")
    target = await _create_user(
        db, user_id="conc-target", role="dept_admin", department="并发部门"
    )
    peer = await _create_user(
        db, user_id="conc-peer", role="dept_admin", department="并发部门"
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
    await _seed_pending_approval(db, dept_id=dept["id"], assignee_id=target.id)
    await set_user_state(db, target, "disabled")
    await db.commit()

    # 各自一个 session 并发跑 reresolve（共享同一 PG，advisory lock 跨 session 生效）
    async def _run():
        async with session_factory() as s:
            return await reresolve_affected_decision_requests(s, target.id)

    # 两路并发，必然两次都成功（lock 串行化）
    r1, r2 = await asyncio.gather(_run(), _run(), return_exceptions=False)
    assert isinstance(r1, dict) and isinstance(r2, dict)
    # 至少一次扫到了 1 条 todo（取决于哪路先拿到 lock）
    assert (r1.get("scanned", 0) + r2.get("scanned", 0)) >= 1


@pytest.mark.asyncio
async def test_trigger_reresolve_audits_on_sqlalchemy_error(db: AsyncSession):
    """H2：reresolve 抛 SQLAlchemyError → _trigger_reresolve 不抛，audit 写一条 reresolve_failed。"""
    dept = await create_org_unit(db, unit_id="dept-h2", name="H2 部门")
    sys_admin = await _create_user(db, user_id="sys-admin-h2", role="system_admin")
    target = await _create_user(
        db, user_id="h2-target", role="dept_admin", department="H2 部门"
    )
    db.add(
        UserOrgMembership(
            user_id=target.id,
            org_unit_id=dept["id"],
            membership_type="primary",
            is_manager=True,
        )
    )
    await db.commit()

    # mock reresolve 抛 OperationalError（SQLAlchemyError 子类）
    fake_exc = OperationalError("mock", None, Exception("simulated"))
    with patch(
        "app.approval.reresolve.reresolve_affected_decision_requests",
        new=AsyncMock(side_effect=fake_exc),
    ):
        # update_user 内部调 _trigger_reresolve；不应抛
        await user_service.update_user(
            db,
            user_id=target.id,
            role="aibp",
            operator=sys_admin,
        )
        await db.commit()

    # audit_log 应有一条 approval.reresolve_failed
    rows = (
        await db.execute(
            select(AuditLog).where(AuditLog.action == "approval.reresolve_failed")
        )
    ).scalars().all()
    assert len(rows) == 1, f"应写入 1 条 approval.reresolve_failed，实际 {len(rows)}"
    assert rows[0].target_id == target.id
    assert rows[0].detail.get("priority") == "high"
    assert "OperationalError" in rows[0].detail.get("error_type", "")


@pytest.mark.asyncio
async def test_trigger_reresolve_propagates_cancellation(db: AsyncSession):
    """H2：CancelledError 必须 re-raise，不能被吞。"""
    dept = await create_org_unit(db, unit_id="dept-cancel", name="取消部门")
    sys_admin = await _create_user(db, user_id="sys-admin-cancel", role="system_admin")
    target = await _create_user(
        db, user_id="cancel-target", role="dept_admin", department="取消部门"
    )
    db.add(
        UserOrgMembership(
            user_id=target.id,
            org_unit_id=dept["id"],
            membership_type="primary",
            is_manager=True,
        )
    )
    await db.commit()

    with patch(
        "app.approval.reresolve.reresolve_affected_decision_requests",
        new=AsyncMock(side_effect=asyncio.CancelledError()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await user_service.update_user(
                db,
                user_id=target.id,
                role="aibp",
                operator=sys_admin,
            )
