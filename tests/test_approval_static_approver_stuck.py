"""M2：静态 approver_id 链路 + approver disable 后的 stuck 告警。

构造 chain_step=[{approver_id: alice}] 的实例，alice disable 后触发 reresolve
应：
- 写一条 audit.log action='approval.static_approver_disabled_stuck'
- DingTalkOutbox 增加一条 priority=2 告警
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as db_mod
from app.approval.models import ApprovalInstance, ApprovalRule, ApprovalStep
from app.approval.reresolve import reresolve_affected_decision_requests
from app.auth.models import User
from app.common.audit import AuditLog
from app.config import settings
from app.database import Base
from app.dingtalk.models import DingTalkOutbox
from app.todos.models import AITodo, DecisionRequest
from app.users.role_matrix import set_user_state


@pytest_asyncio.fixture
async def db():
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$",
        "/skillforge_test",
        str(settings.DATABASE_URL),
    )
    # 先 terminate 该 DB 上其他闲置连接，避免 DROP SCHEMA 死锁
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

    import app.approval.models  # noqa: F401
    import app.auth.models  # noqa: F401
    import app.common.audit  # noqa: F401
    import app.dingtalk.models  # noqa: F401
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
    _close = await asyncpg.connect(plain_url)
    try:
        await _close.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=current_database() AND pid <> pg_backend_pid() AND state='idle'"
        )
    finally:
        await _close.close()


async def _create_user(session: AsyncSession, *, user_id: str, role: str) -> User:
    user = User(
        id=user_id,
        username=user_id,
        name=user_id,
        role=role,
        is_active=True,
        must_change_password=False,
    )
    session.add(user)
    await session.flush()
    await set_user_state(session, user, "active")
    return user


@pytest.mark.asyncio
async def test_static_approver_disabled_emits_stuck_audit_and_alert(db: AsyncSession):
    alice = await _create_user(db, user_id="alice-static", role="aibp")
    requester = await _create_user(db, user_id="static-requester", role="aibp")
    await db.commit()

    rule = ApprovalRule(
        id="rule-static-stuck",
        business_type="skill_publish",
        condition_json=None,
        approval_chain=[{"approver_id": alice.id}],
        enabled=True,
        priority=1,
    )
    instance = ApprovalInstance(
        id="ai-static-stuck",
        rule_id=rule.id,
        business_type="skill_publish",
        business_id="skill-static-stuck",
        requester_id=requester.id,
        status="pending",
        current_step=1,
        payload={},
    )
    step = ApprovalStep(
        id="as-static-stuck",
        instance_id=instance.id,
        step_order=1,
        approver_id=alice.id,
        status="pending",
    )
    request = DecisionRequest(
        id=f"dr-static-{instance.id}",
        source_type="approval_step",
        source_id=f"{instance.id}:1",
        skill_id=instance.business_id,
        kind="review",
        title="静态审批",
        summary="待审批",
        payload={
            "instance_id": instance.id,
            "step_order": 1,
            "business_type": instance.business_type,
            "business_id": instance.business_id,
        },
        decision_mode="any_of",
        aggregate_status="pending",
        sla_at=datetime.utcnow() + timedelta(hours=24),
    )
    db.add_all([rule, instance, step, request])
    await db.flush()
    db.add(AITodo(request_id=request.id, kind="review", assignee=alice.id))
    await db.flush()

    await set_user_state(db, alice, "disabled")
    await db.commit()

    # 触发 reresolve（模拟 disable_user 主流程之后的 §6.5 调用）
    await reresolve_affected_decision_requests(db, alice.id)
    await db.commit()

    # 1) audit.log 必有 approval.static_approver_disabled_stuck
    audit_rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "approval.static_approver_disabled_stuck"
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 1, (
        f"应写入 1 条 static_approver_disabled_stuck，实际 {len(audit_rows)}"
    )
    detail = audit_rows[0].detail or {}
    assert detail.get("static_approver_id") == alice.id
    assert detail.get("instance_id") == instance.id
    assert detail.get("priority") == "high"

    # 2) DingTalkOutbox 必有一条 priority=2 alert
    outbox_rows = (
        await db.execute(
            select(DingTalkOutbox).where(
                DingTalkOutbox.message_type == "approval_stuck"
            )
        )
    ).scalars().all()
    assert len(outbox_rows) == 1, (
        f"应入队 1 条 stuck alert，实际 {len(outbox_rows)}"
    )
    assert outbox_rows[0].priority == 2
    assert outbox_rows[0].related_id == instance.id

    # 3) 关键：原来挂在 alice 名下的 AITodo 没有被错误改成 resolved_by_peer
    # （静态链路无法重分派，只能告警等待人工）
    todo_row = (
        await db.execute(
            select(AITodo).where(AITodo.request_id == request.id)
        )
    ).scalar_one()
    assert todo_row.assignee == alice.id
    # 仍 pending（待人工干预），不会被错误状态机翻转
    assert todo_row.status == "pending"


@pytest.mark.asyncio
async def test_static_approver_other_user_not_alerted(db: AsyncSession):
    """边界：static approver 不是被禁用的 user → 不写 stuck audit、不入队 alert。"""
    alice = await _create_user(db, user_id="alice-bystander", role="aibp")
    bob = await _create_user(db, user_id="bob-disabled", role="aibp")
    requester = await _create_user(db, user_id="bystander-requester", role="aibp")
    await db.commit()

    rule = ApprovalRule(
        id="rule-bystander",
        business_type="skill_publish",
        condition_json=None,
        approval_chain=[{"approver_id": alice.id}],
        enabled=True,
        priority=1,
    )
    instance = ApprovalInstance(
        id="ai-bystander",
        rule_id=rule.id,
        business_type="skill_publish",
        business_id="skill-bystander",
        requester_id=requester.id,
        status="pending",
        current_step=1,
        payload={},
    )
    step = ApprovalStep(
        id="as-bystander",
        instance_id=instance.id,
        step_order=1,
        approver_id=alice.id,
        status="pending",
    )
    request = DecisionRequest(
        id=f"dr-bystander-{instance.id}",
        source_type="approval_step",
        source_id=f"{instance.id}:1",
        skill_id=instance.business_id,
        kind="review",
        title="旁观审批",
        summary="待审批",
        payload={
            "instance_id": instance.id,
            "step_order": 1,
            "business_type": instance.business_type,
            "business_id": instance.business_id,
        },
        decision_mode="any_of",
        aggregate_status="pending",
        sla_at=datetime.utcnow() + timedelta(hours=24),
    )
    db.add_all([rule, instance, step, request])
    await db.flush()
    # 注意：todo 挂在 bob 名下（不是 static approver alice），bob 被禁后会进入主循环
    db.add(AITodo(request_id=request.id, kind="review", assignee=bob.id))
    await db.flush()

    await set_user_state(db, bob, "disabled")
    await db.commit()

    await reresolve_affected_decision_requests(db, bob.id)
    await db.commit()

    # bob 不是 static approver → 不应写 stuck audit
    audit_rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "approval.static_approver_disabled_stuck"
            )
        )
    ).scalars().all()
    assert audit_rows == [], f"bob 不是 static approver，不应触发 stuck 告警，实际 {audit_rows}"
