"""C2 / H9 / H10：todos 批量端点权限闸门 + savepoint 回归。

覆盖任务：
- batch-extend-sla：alice (biz_owner) 403；admin / dept_admin@DeptA 200；
  dept_admin@DeptB 对 DeptA 的 todo 403
- batch-reassign：alice 403；admin OK
- batch-ack-dispatch：alice (非 executor) 403；alice (executor) OK
- bulk-summary：alice 不可见 id 静默跳过、不抛 403
- partial failure：5 ids 中混 1 个不可见 → succeeded=4 + 不可见的为 failed
- 转派只更新平台待办，不直接重发钉钉卡片
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

import app.database as db_mod
from app.auth.models import User
from app.common.audit import AuditLog
from app.common.exceptions import AppError
from app.org.models import OrgUnit, UserOrgMembership
from app.skills.core.models import Skill
from app.todos.models import AITodo, DecisionRequest, TodoDispatchTask
from app.todos.service import todo_service


def _u(uid: str, *, role: str, dept: str | None = None, can_view_all: bool = False) -> User:
    return User(
        id=uid,
        username=uid,
        name=uid,
        role=role,
        state="active",
        permissions_rev=0,
        can_view_all=can_view_all,
        department=dept,
        is_active=True,
        must_change_password=False,
    )


async def _seed_world() -> dict[str, User]:
    """构造一组 actor + 一批 todo 用于权限测试。

    部门：Dept A（dept-a）/ Dept B（dept-b）
    用户：
      - alice：biz_owner @ Dept A（普通业务用户，无管理权）
      - bob：admin（system_admin 等价的 legacy admin + can_view_all）
      - carol：dept_admin @ Dept A
      - dave：dept_admin @ Dept B
      - sa：v2 system_admin（不带 can_view_all 也应当被全局识别）
    Skill：skill-A 属于 Dept A
    """
    alice = _u("alice", role="biz_owner", dept="Dept A")
    bob = _u("bob", role="admin", can_view_all=True, dept="Dept A")
    carol = _u("carol", role="dept_admin", dept="Dept A")
    dave = _u("dave", role="dept_admin", dept="Dept B")
    sa = _u("sa", role="system_admin", dept="Dept A")
    eve = _u("eve", role="biz_owner", dept="Dept A")
    async with db_mod.async_session_factory() as s:
        s.add_all(
            [
                OrgUnit(id="root", name="Root", type="company", path="/root"),
                OrgUnit(
                    id="dept-a",
                    name="Dept A",
                    type="department",
                    parent_id="root",
                    path="/root/dept-a",
                ),
                OrgUnit(
                    id="dept-b",
                    name="Dept B",
                    type="department",
                    parent_id="root",
                    path="/root/dept-b",
                ),
            ]
        )
        s.add_all([alice, bob, carol, dave, sa, eve])
        s.add_all(
            [
                UserOrgMembership(user_id="alice", org_unit_id="dept-a"),
                UserOrgMembership(user_id="bob", org_unit_id="dept-a"),
                UserOrgMembership(
                    user_id="carol", org_unit_id="dept-a", is_manager=True
                ),
                UserOrgMembership(
                    user_id="dave", org_unit_id="dept-b", is_manager=True
                ),
                UserOrgMembership(user_id="sa", org_unit_id="dept-a"),
                UserOrgMembership(user_id="eve", org_unit_id="dept-a"),
            ]
        )
        s.add(
            Skill(
                id="skill-A",
                name="Skill A",
                description="A",
                department="Dept A",
                org_unit_id="dept-a",
                visibility="company",
                status="active",
            )
        )
        await s.commit()
    return {
        "alice": alice,
        "bob": bob,
        "carol": carol,
        "dave": dave,
        "sa": sa,
        "eve": eve,
    }


async def _seed_todos_for(assignee: str, count: int = 3) -> list[int]:
    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id=f"dr-{assignee}",
            source_type="t",
            source_id=f"s-{assignee}",
            skill_id="skill-A",
            run_id=None,
            decision_log_id=None,
            kind="review",
            title="Batch authz test",
            summary=None,
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 4, 25, 8, 0, 0),
            payload={},
        )
        s.add(req)
        await s.flush()
        s.add_all(
            [
                AITodo(
                    request_id=req.id,
                    kind="review",
                    assignee=assignee,
                    status="pending",
                )
                for _ in range(count)
            ]
        )
        await s.commit()
        rows = (
            await s.execute(
                select(AITodo.id)
                .where(AITodo.request_id == req.id)
                .order_by(AITodo.id)
            )
        ).scalars().all()
        return [int(x) for x in rows]


# -------- batch-extend-sla --------


@pytest.mark.asyncio
async def test_batch_extend_sla_denies_normal_user(client):
    users = await _seed_world()
    tids = await _seed_todos_for("alice", count=2)

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_extend_sla(
            s, todo_ids=tids, hours=2, actor=users["alice"]
        )

    assert result["total"] == 2
    assert result["succeeded"] == 0
    assert result["failed"] == 2
    assert all(r["error"] == "AUTH_PERMISSION_DENIED" for r in result["results"])


@pytest.mark.asyncio
async def test_batch_extend_sla_allows_admin(client):
    users = await _seed_world()
    tids = await _seed_todos_for("alice", count=2)

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_extend_sla(
            s, todo_ids=tids, hours=2, actor=users["bob"]
        )
        await s.commit()

    assert result["succeeded"] == 2


@pytest.mark.asyncio
async def test_batch_extend_sla_allows_local_dept_admin(client):
    users = await _seed_world()
    tids = await _seed_todos_for("alice", count=2)

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_extend_sla(
            s, todo_ids=tids, hours=2, actor=users["carol"]
        )
        await s.commit()

    assert result["succeeded"] == 2


@pytest.mark.asyncio
async def test_batch_extend_sla_denies_other_dept_admin(client):
    users = await _seed_world()
    tids = await _seed_todos_for("alice", count=2)

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_extend_sla(
            s, todo_ids=tids, hours=2, actor=users["dave"]
        )

    assert result["succeeded"] == 0
    assert result["failed"] == 2
    assert all(r["error"] == "AUTH_PERMISSION_DENIED" for r in result["results"])


@pytest.mark.asyncio
async def test_batch_extend_sla_partial_savepoint(client):
    """5 ids 中混 1 个不可见 id：savepoint 让 4 条 commit、不可见的进 failed。"""
    users = await _seed_world()
    tids = await _seed_todos_for("alice", count=4)
    target = tids + [9_999_999]

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_extend_sla(
            s, todo_ids=target, hours=1, actor=users["bob"]
        )
        await s.commit()

    assert result["total"] == 5
    assert result["succeeded"] == 4
    assert result["failed"] == 1
    failed = [r for r in result["results"] if not r["ok"]]
    assert failed and failed[0]["error"] == "TODO_NOT_FOUND"


# -------- batch-reassign --------


@pytest.mark.asyncio
async def test_batch_reassign_denies_normal_user(client):
    users = await _seed_world()
    tids = await _seed_todos_for("alice", count=2)

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_reassign(
            s,
            todo_ids=tids,
            to_user_id="eve",
            actor=users["alice"],
            reason="不该让普通用户改",
        )

    assert result["succeeded"] == 0
    assert result["failed"] == 2
    assert all(r["error"] == "AUTH_PERMISSION_DENIED" for r in result["results"])


@pytest.mark.asyncio
async def test_batch_reassign_does_not_enqueue_dingtalk(client):
    """转派只改平台待办 assignee，不直接重发钉钉卡片。"""
    users = await _seed_world()
    # 即使目标用户绑定了钉钉，也不能在转派时直接推送。
    async with db_mod.async_session_factory() as s:
        eve = await s.get(User, "eve")
        eve.dingtalk_user_id = "ding-eve"
        await s.commit()
    tids = await _seed_todos_for("alice", count=1)

    enqueue_calls = []

    with patch(
        "app.todos.service.outbox.enqueue", new=AsyncMock(side_effect=lambda *a, **kw: enqueue_calls.append((a, kw)))
    ):
        async with db_mod.async_session_factory() as s:
            result = await todo_service.batch_reassign(
                s,
                todo_ids=tids,
                to_user_id="eve",
                actor=users["bob"],
                reason="模拟漏发",
            )
            await s.commit()

    assert result["succeeded"] == 1  # 转派 DB 落库成功
    assert result["card_failures"] == []
    assert enqueue_calls == []

    # audit：只记录转派，不记录/触发钉钉漏发
    async with db_mod.async_session_factory() as s:
        actions = (
            await s.execute(
                select(AuditLog.action).where(AuditLog.target_id == str(tids[0]))
            )
        ).scalars().all()
    assert "todo.reassigned" in actions
    assert "todo.reassign_card_failed" not in actions


# -------- batch-ack-dispatch --------


async def _seed_dispatch_for(executor: str) -> list[int]:
    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id=f"dr-dp-{executor}",
            source_type="t",
            source_id=f"s-dp-{executor}",
            skill_id="skill-A",
            run_id=None,
            decision_log_id=None,
            kind="dispatch",
            title="Dispatch authz",
            summary=None,
            decision_mode="any_of",
            aggregate_status="completed",
            aggregate_decision="approved",
            sla_at=datetime(2026, 4, 25, 8, 0, 0),
            payload={},
        )
        s.add(req)
        await s.flush()
        s.add_all(
            [
                TodoDispatchTask(
                    request_id=req.id,
                    manager_todo_id=None,
                    executor=executor,
                    content="任务 1",
                    status="sent",
                ),
                TodoDispatchTask(
                    request_id=req.id,
                    manager_todo_id=None,
                    executor=executor,
                    content="任务 2",
                    status="in_progress",
                ),
            ]
        )
        await s.commit()
        rows = (
            await s.execute(
                select(TodoDispatchTask.id)
                .where(TodoDispatchTask.request_id == req.id)
                .order_by(TodoDispatchTask.id)
            )
        ).scalars().all()
        return [int(x) for x in rows]


@pytest.mark.asyncio
async def test_batch_ack_dispatch_denies_non_executor(client):
    users = await _seed_world()
    tids = await _seed_dispatch_for("eve")  # eve 是 executor

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_ack_dispatch(
            s, task_ids=tids, actor_id=users["alice"].id, note=""
        )

    assert result["succeeded"] == 0
    assert all(r["error"] == "AUTH_PERMISSION_DENIED" for r in result["results"])


@pytest.mark.asyncio
async def test_batch_ack_dispatch_allows_own_executor(client):
    users = await _seed_world()
    tids = await _seed_dispatch_for("eve")

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_ack_dispatch(
            s, task_ids=tids, actor_id="eve", note="完成"
        )
        await s.commit()

    assert result["succeeded"] == 2


# -------- bulk-summary 静默过滤 --------


@pytest.mark.asyncio
async def test_bulk_summary_silently_filters_unauthorized_ids(client):
    """alice 调 bulk-summary，传入 [可见 id, 不可见 id]：只回可见，不暴露不可见的存在。"""
    users = await _seed_world()
    tids_alice = await _seed_todos_for("alice", count=1)
    # 给 bob 也建一条 todo（属于 alice 不可见的 request 即可：通过部门隔离）
    async with db_mod.async_session_factory() as s:
        # 新 skill 在 Dept B，alice 看不到
        s.add(
            Skill(
                id="skill-B",
                name="Skill B",
                description="B",
                department="Dept B",
                org_unit_id="dept-b",
                visibility="company",
                status="active",
            )
        )
        req = DecisionRequest(
            id="dr-other",
            source_type="t",
            source_id="s-other",
            skill_id="skill-B",
            run_id=None,
            decision_log_id=None,
            kind="review",
            title="other dept",
            summary=None,
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 4, 25, 8, 0, 0),
            payload={},
        )
        s.add(req)
        await s.flush()
        # bob 的 todo（alice 不可见，因为不是 alice 的 + 跨部门）
        s.add(
            AITodo(
                request_id="dr-other",
                kind="review",
                assignee="bob",
                status="pending",
            )
        )
        await s.commit()
        invisible_id = (
            await s.execute(
                select(AITodo.id).where(AITodo.request_id == "dr-other")
            )
        ).scalar_one()

    async with db_mod.async_session_factory() as s:
        result = await todo_service.bulk_summary(
            s,
            todo_ids=tids_alice + [int(invisible_id)],
            current_user=users["alice"],
        )

    # 只能看见自己的 1 条；不可见 id 被静默丢弃，不抛 403
    assert set(result["summaries"].keys()) == set(tids_alice)
