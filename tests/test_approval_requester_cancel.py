"""role-matrix-v2 §6.5 第二条：requester 被禁用后，其发起的未决审批应作废。

本文件只覆盖 requester 侧取消逻辑。approver 侧由 test_approval_reresolve.py 覆盖。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as db_mod
from app.approval.models import ApprovalInstance, ApprovalRule, ApprovalStep
from app.approval.reresolve import (
    cancel_requester_approvals,
    reresolve_affected_decision_requests,
)
from app.auth.models import User
from app.common.audit import AuditLog
from app.config import settings
from app.database import Base
from app.todos.models import AITodo, DecisionRequest


@pytest_asyncio.fixture
async def db():
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$",
        "/skillforge_test",
        str(settings.DATABASE_URL),
    )
    engine = create_async_engine(test_url, echo=False)

    # 保证所有相关表都建起来（audit_log / approval / todos / auth）
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


async def _seed_instance(
    db: AsyncSession,
    *,
    instance_id: str,
    requester_id: str,
    approver_id: str,
    status: str = "pending",
    with_todo: bool = True,
) -> tuple[ApprovalInstance, DecisionRequest | None, AITodo | None]:
    """构造一个审批实例 + 审批链 + DR + AITodo（可选）。

    只用到 approval_chain 里的 approver_id 字段，避免触发 resolver 依赖。
    """
    rule_id = f"rule-{instance_id}"
    rule = await db.get(ApprovalRule, rule_id)
    if rule is None:
        rule = ApprovalRule(
            id=rule_id,
            business_type="skill_publish",
            condition_json=None,
            approval_chain=[{"approver_id": approver_id}],
            enabled=True,
            priority=1,
        )
        db.add(rule)

    instance = ApprovalInstance(
        id=instance_id,
        rule_id=rule.id,
        business_type="skill_publish",
        business_id=f"skill-{instance_id}",
        requester_id=requester_id,
        status=status,
        current_step=1,
        payload={"hint": "seed"},
    )
    step = ApprovalStep(
        id=f"as-{instance_id}",
        instance_id=instance.id,
        step_order=1,
        approver_id=approver_id,
        status="pending",
    )
    db.add_all([instance, step])
    await db.flush()

    dr: DecisionRequest | None = None
    todo: AITodo | None = None
    if with_todo:
        dr = DecisionRequest(
            id=f"dr-{instance_id}",
            source_type="approval_step",
            source_id=f"{instance.id}:1",
            skill_id=instance.business_id,
            kind="review",
            title="skill_publish 审批",
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
        db.add(dr)
        await db.flush()
        todo = AITodo(
            request_id=dr.id,
            kind="review",
            assignee=approver_id,
            status="pending",
        )
        db.add(todo)
        await db.flush()
    return instance, dr, todo


@pytest.mark.asyncio
async def test_disable_requester_cancels_pending_approvals(db: AsyncSession):
    """requester 被禁用 → 其发起的未决审批级联 cancelled；无关审批保持不变。"""
    # 目标 requester（要被禁用）
    target_requester = User(
        id="req-target",
        username="req-target",
        name="Target Requester",
        role="biz_owner",
        is_active=True,
        must_change_password=False,
    )
    # 无关 requester（作对照，不受影响）
    other_requester = User(
        id="req-other",
        username="req-other",
        name="Other Requester",
        role="biz_owner",
        is_active=True,
        must_change_password=False,
    )
    db.add_all([target_requester, other_requester])
    await db.flush()

    # 1) target 的 pending 审批 —— 应被 cancel
    i1, dr1, todo1 = await _seed_instance(
        db,
        instance_id="ai-req-target-1",
        requester_id=target_requester.id,
        approver_id="approver-1",
        status="pending",
    )
    # 2) target 的 in_progress 审批（spec 提到但目前代码没落；防御覆盖）
    i2, dr2, todo2 = await _seed_instance(
        db,
        instance_id="ai-req-target-2",
        requester_id=target_requester.id,
        approver_id="approver-2",
        status="in_progress",
    )
    # 3) target 的已通过审批 —— 终态，不应被动
    i3, dr3, _ = await _seed_instance(
        db,
        instance_id="ai-req-target-3",
        requester_id=target_requester.id,
        approver_id="approver-3",
        status="approved",
        with_todo=False,
    )
    # 4) 无关用户的 pending 审批 —— 不应被动
    i4, dr4, todo4 = await _seed_instance(
        db,
        instance_id="ai-req-other-1",
        requester_id=other_requester.id,
        approver_id="approver-4",
        status="pending",
    )
    await db.commit()

    cancelled = await cancel_requester_approvals(db, user_id=target_requester.id)
    await db.commit()

    # 返回值：包含 target 的 pending + in_progress 两个实例
    assert set(cancelled) == {i1.id, i2.id}
    assert len(cancelled) == 2

    # 实例层面
    refreshed_i1 = await db.get(ApprovalInstance, i1.id)
    refreshed_i2 = await db.get(ApprovalInstance, i2.id)
    refreshed_i3 = await db.get(ApprovalInstance, i3.id)
    refreshed_i4 = await db.get(ApprovalInstance, i4.id)
    assert refreshed_i1.status == "cancelled"
    assert refreshed_i1.completed_at is not None
    # 专属列（真源）
    assert refreshed_i1.cancelled_reason == "requester_disabled"
    assert isinstance(refreshed_i1.cancelled_at, datetime)
    # payload 兼容层
    assert refreshed_i1.payload.get("cancelled_reason") == "requester_disabled"
    assert refreshed_i1.payload.get("cancelled_at")
    assert refreshed_i2.status == "cancelled"
    assert refreshed_i2.cancelled_reason == "requester_disabled"
    assert isinstance(refreshed_i2.cancelled_at, datetime)
    # 终态实例保持不动
    assert refreshed_i3.status == "approved"
    # 无关实例保持 pending
    assert refreshed_i4.status == "pending"
    assert refreshed_i4.completed_at is None

    # DR 级联
    refreshed_dr1 = await db.get(DecisionRequest, dr1.id)
    refreshed_dr2 = await db.get(DecisionRequest, dr2.id)
    refreshed_dr4 = await db.get(DecisionRequest, dr4.id)
    assert refreshed_dr1.aggregate_status == "cancelled"
    assert refreshed_dr1.completed_at is not None
    assert refreshed_dr2.aggregate_status == "cancelled"
    # 对照组 DR 仍 pending
    assert refreshed_dr4.aggregate_status == "pending"

    # AITodo 级联
    refreshed_todo1 = await db.get(AITodo, todo1.id)
    refreshed_todo2 = await db.get(AITodo, todo2.id)
    refreshed_todo4 = await db.get(AITodo, todo4.id)
    assert refreshed_todo1.status == "cancelled"
    assert refreshed_todo1.decision_channel == "reresolve"
    assert refreshed_todo1.decision_reason == "requester_disabled"
    assert refreshed_todo1.decided_at is not None
    assert refreshed_todo2.status == "cancelled"
    # 对照组 todo 仍 pending
    assert refreshed_todo4.status == "pending"

    # audit 日志
    audit_rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "approval.cancel_on_requester_disabled",
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 2
    target_ids = {row.target_id for row in audit_rows}
    assert target_ids == {i1.id, i2.id}
    # detail 里带 requester_id 和被取消的 DR/todo 数
    for row in audit_rows:
        assert row.target_type == "approval_instance"
        assert row.detail.get("requester_id") == target_requester.id
        assert row.detail.get("cancelled_requests") == 1
        assert row.detail.get("cancelled_todos") == 1


@pytest.mark.asyncio
async def test_reresolve_entry_also_cancels_requester_approvals(db: AsyncSession):
    """`reresolve_affected_decision_requests` 作为统一入口，应同时触发 requester 侧取消。

    这验证 disable_user 通过原有 `_trigger_reresolve` hook 就能覆盖 §6.5 第二条，
    无需在 users/service.py 另加调用。
    """
    requester = User(
        id="req-entry",
        username="req-entry",
        name="Entry Requester",
        role="biz_owner",
        is_active=True,
        must_change_password=False,
    )
    db.add(requester)
    await db.flush()

    instance, dr, todo = await _seed_instance(
        db,
        instance_id="ai-req-entry-1",
        requester_id=requester.id,
        approver_id="approver-entry",
        status="pending",
    )
    await db.commit()

    summary = await reresolve_affected_decision_requests(db, requester.id)
    await db.commit()

    assert summary["cancelled_as_requester"] == 1
    # approver 侧没有命中（requester != approver）
    assert summary["scanned"] == 0

    refreshed_instance = await db.get(ApprovalInstance, instance.id)
    refreshed_dr = await db.get(DecisionRequest, dr.id)
    refreshed_todo = await db.get(AITodo, todo.id)
    assert refreshed_instance.status == "cancelled"
    assert refreshed_dr.aggregate_status == "cancelled"
    assert refreshed_todo.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_requester_approvals_noop_when_user_has_none(db: AsyncSession):
    """user_id 未发起过任何审批 → 返回空列表，不写 audit。"""
    cancelled = await cancel_requester_approvals(db, user_id="ghost-user")
    await db.commit()
    assert cancelled == []

    audit_rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "approval.cancel_on_requester_disabled",
            )
        )
    ).scalars().all()
    assert audit_rows == []
