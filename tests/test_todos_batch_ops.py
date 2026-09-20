"""GAP-7/8/9：批量延期 / 转派 / 派发确认回归测试。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

import app.database as db_mod
from app.auth.models import User
from app.common.audit import AuditLog
from app.common.exceptions import AppError
from app.todos.models import AITodo, DecisionRequest, TodoDispatchTask
from app.todos.service import todo_service
from tests.test_inbox_reports import _add_skill, _add_users_and_orgs


async def _make_two_pending_todos_for(assignee_id: str, *, skill_department: str = "Dept A") -> tuple[int, int, datetime]:
    """建 skill-br + 2 条 pending AITodo 给 assignee_id，返回 (tid1, tid2, sla_at)。"""
    await _add_skill(
        skill_id="skill-br",
        name="Batch Skill",
        visibility="company",
        department=skill_department,
        org_unit_id="dept-a",
    )
    sla = datetime(2026, 4, 20, 8, 0, 0)
    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id="dr-br",
            source_type="t",
            source_id="s",
            skill_id="skill-br",
            run_id=None,
            decision_log_id=None,
            kind="review",
            title="Batch",
            summary=None,
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=sla,
            payload={},
        )
        s.add(req)
        await s.flush()
        s.add_all(
            [
                AITodo(request_id="dr-br", kind="review", assignee=assignee_id, status="pending"),
                AITodo(request_id="dr-br", kind="review", assignee=assignee_id, status="pending"),
            ]
        )
        await s.commit()
    async with db_mod.async_session_factory() as s:
        rows = (
            await s.execute(
                select(AITodo.id).where(AITodo.assignee == assignee_id).order_by(AITodo.id)
            )
        ).scalars().all()
    return int(rows[0]), int(rows[1]), sla


@pytest.mark.asyncio
async def test_batch_extend_sla_shifts_each_request(client):
    users = await _add_users_and_orgs()
    tid1, tid2, sla = await _make_two_pending_todos_for("alice")

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_extend_sla(
            s, todo_ids=[tid1, tid2], hours=3, actor=users["admin"]
        )
        await s.commit()

    assert result["total"] == 2
    assert result["succeeded"] == 2
    assert result["failed"] == 0

    async with db_mod.async_session_factory() as s:
        req = await s.get(DecisionRequest, "dr-br")
    # 两条 AITodo 共享一条 DR，延期会被调用两次，累加 6h（extend_sla 语义）
    assert req.sla_at == sla + timedelta(hours=6)


@pytest.mark.asyncio
async def test_batch_extend_sla_handles_missing_ids(client):
    users = await _add_users_and_orgs()
    tid1, _, _ = await _make_two_pending_todos_for("alice")

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_extend_sla(
            s, todo_ids=[tid1, 999999], hours=2, actor=users["admin"]
        )
    assert result["total"] == 2
    assert result["succeeded"] == 1
    assert result["failed"] == 1


@pytest.mark.asyncio
async def test_batch_reassign_updates_assignee_and_writes_audit(client):
    users = await _add_users_and_orgs()
    tid1, tid2, _ = await _make_two_pending_todos_for("alice")

    # manager 是 dept_admin 于 Dept A；转派给 manager 应成功
    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_reassign(
            s,
            todo_ids=[tid1, tid2],
            to_user_id="manager",
            actor=users["admin"],
            reason="代办",
        )
        await s.commit()

    assert result["succeeded"] == 2

    async with db_mod.async_session_factory() as s:
        todos = (
            await s.execute(select(AITodo).where(AITodo.id.in_([tid1, tid2])))
        ).scalars().all()
    assert all(t.assignee == "manager" for t in todos)

    # 审计落库
    async with db_mod.async_session_factory() as s:
        rows = (
            await s.execute(select(AuditLog).where(AuditLog.action == "todo.reassigned"))
        ).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_batch_reassign_denies_cross_department(client):
    users = await _add_users_and_orgs()
    tid1, _, _ = await _make_two_pending_todos_for("alice", skill_department="Dept A")

    # bob 在 Dept B，不是 Dept A，且不是 admin / can_view_all → 应当拒绝
    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_reassign(
            s,
            todo_ids=[tid1],
            to_user_id="bob",
            actor=users["admin"],
            reason="测试跨部门",
        )
        await s.commit()

    assert result["succeeded"] == 0
    assert result["failed"] == 1
    assert result["results"][0]["error"] == "AUTH_DEPARTMENT_DENIED"

    async with db_mod.async_session_factory() as s:
        todo = await s.get(AITodo, tid1)
    assert todo.assignee == "alice"  # 未被修改


@pytest.mark.asyncio
async def test_batch_reassign_rejects_disabled_target_user(client):
    users = await _add_users_and_orgs()
    tid1, _, _ = await _make_two_pending_todos_for("alice")
    # 把 bob 禁用
    async with db_mod.async_session_factory() as s:
        bob = await s.get(User, "bob")
        bob.is_active = False
        bob.state = "disabled"
        await s.commit()

    async with db_mod.async_session_factory() as s:
        with pytest.raises(AppError) as exc:
            await todo_service.batch_reassign(
                s,
                todo_ids=[tid1],
                to_user_id="bob",
                actor=users["admin"],
                reason="禁用目标",
            )
    assert exc.value.code == "AUTH_ACCOUNT_DISABLED"


@pytest.mark.asyncio
async def test_batch_ack_dispatch_marks_done(client):
    users = await _add_users_and_orgs()
    await _add_skill(
        skill_id="skill-dp",
        name="Dispatch Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id="dr-dp",
            source_type="t",
            source_id="s",
            skill_id="skill-dp",
            run_id=None,
            decision_log_id=None,
            kind="dispatch",
            title="Dispatch",
            summary=None,
            decision_mode="any_of",
            aggregate_status="completed",
            aggregate_decision="approved",
            sla_at=datetime(2026, 4, 20, 8, 0, 0),
            payload={},
        )
        s.add(req)
        await s.flush()
        s.add_all(
            [
                TodoDispatchTask(
                    request_id="dr-dp",
                    manager_todo_id=None,
                    executor="alice",
                    content="任务 1",
                    status="sent",
                ),
                TodoDispatchTask(
                    request_id="dr-dp",
                    manager_todo_id=None,
                    executor="alice",
                    content="任务 2",
                    status="in_progress",
                ),
            ]
        )
        await s.commit()
        rows = (
            await s.execute(select(TodoDispatchTask.id).order_by(TodoDispatchTask.id))
        ).scalars().all()

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_ack_dispatch(
            s, task_ids=list(rows), actor_id="alice", note="批量完成"
        )
        await s.commit()

    assert result["total"] == 2
    assert result["succeeded"] == 2

    async with db_mod.async_session_factory() as s:
        tasks = (await s.execute(select(TodoDispatchTask))).scalars().all()
    assert all(t.status == "done" for t in tasks)
