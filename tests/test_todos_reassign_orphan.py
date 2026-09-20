"""Wave 1 C1：batch_reassign 的 orphan todo 与 pending 用户防护回归测试。

- orphan todo（skill 已被删除）在防御路径下仍遵循同部门规则（非全局角色），
  且全局角色可越部门但不能越过 "目标用户 active" 的门槛。
- pending 目标用户（钉钉首登未激活）不能被指派工作。
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

import app.database as db_mod
from app.auth.models import User
from app.common.exceptions import AppError
from app.todos.models import AITodo, DecisionRequest
from app.todos.service import todo_service
from tests.test_inbox_reports import _add_users_and_orgs


async def _make_orphan_todo(assignee_id: str, request_id: str = "dr-orphan") -> int:
    """建一条 request.skill_id 指向不存在 Skill 的 orphan todo，返回 tid。"""
    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id=request_id,
            source_type="t",
            source_id="s",
            skill_id="skill-ghost",  # ← 不存在
            run_id=None,
            decision_log_id=None,
            kind="review",
            title="Orphan",
            summary=None,
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 4, 20, 8, 0, 0),
            payload={},
        )
        s.add(req)
        await s.flush()
        s.add(AITodo(request_id=request_id, kind="review", assignee=assignee_id, status="pending"))
        await s.commit()
        tid = (
            await s.execute(
                select(AITodo.id).where(AITodo.request_id == request_id)
            )
        ).scalar_one()
    return int(tid)


@pytest.mark.asyncio
async def test_orphan_reassign_allowed_for_admin_cross_dept(client):
    """admin（全局角色）可以把 orphan todo 跨部门转派。"""
    users = await _add_users_and_orgs()
    tid = await _make_orphan_todo("alice")

    async with db_mod.async_session_factory() as s:
        result = await todo_service.batch_reassign(
            s,
            todo_ids=[tid],
            to_user_id="bob",  # Dept B
            actor=users["admin"],  # 全局
            reason="admin 处理 orphan",
        )
        await s.commit()

    assert result["succeeded"] == 1, result
    async with db_mod.async_session_factory() as s:
        todo = await s.get(AITodo, tid)
    assert todo.assignee == "bob"


@pytest.mark.asyncio
async def test_orphan_reassign_denied_for_non_global_cross_dept(client):
    """防御性：若 _acl 放宽让非全局角色走到 orphan 分支，仍拒绝跨部门。

    手动构造一个 role=dept_admin 但 can_view_all=False 的 actor，且让
    assert_can_modify_todo 放行（通过 orphan 场景——目前版本会拦，测试仅验证
    代码路径 defense-in-depth）。实现方式：用 monkeypatch 短暂让
    assert_can_modify_todo 变成 no-op。
    """
    import app.todos.service as todo_svc_mod

    users = await _add_users_and_orgs()
    tid = await _make_orphan_todo("alice", request_id="dr-orphan-2")

    # 构造"dept_admin-in-Dept-A"演员，尝试转派给 Dept B 的 bob
    actor = users["manager"]  # role=dept_admin, department=Dept A

    original = todo_svc_mod.assert_can_modify_todo

    def _bypass(**kwargs):  # noqa: ANN001 – bypass defense 以测试下一层
        return None

    todo_svc_mod.assert_can_modify_todo = _bypass
    try:
        async with db_mod.async_session_factory() as s:
            result = await todo_service.batch_reassign(
                s,
                todo_ids=[tid],
                to_user_id="bob",  # Dept B
                actor=actor,
                reason="防御跨部门",
            )
    finally:
        todo_svc_mod.assert_can_modify_todo = original

    assert result["succeeded"] == 0, result
    assert result["results"][0]["error"] == "AUTH_DEPARTMENT_DENIED"

    async with db_mod.async_session_factory() as s:
        todo = await s.get(AITodo, tid)
    assert todo.assignee == "alice"  # 未改


@pytest.mark.asyncio
async def test_reassign_rejects_pending_target_user(client):
    """pending 用户（钉钉首登未激活）不能被指派任何 todo。"""
    users = await _add_users_and_orgs()
    tid = await _make_orphan_todo("alice", request_id="dr-orphan-3")

    async with db_mod.async_session_factory() as s:
        bob = await s.get(User, "bob")
        bob.state = "pending"  # is_active 仍 True，但 state 是 pending
        await s.commit()

    async with db_mod.async_session_factory() as s:
        with pytest.raises(AppError) as exc_info:
            await todo_service.batch_reassign(
                s,
                todo_ids=[tid],
                to_user_id="bob",
                actor=users["admin"],
                reason="试试 pending",
            )

    assert exc_info.value.code == "AUTH_ACCOUNT_DISABLED"  # 复用旧错误码
    detail_obj = getattr(exc_info.value, "detail", None) or {}
    assert "pending" in str(detail_obj.get("detail", "")), (
        f"detail should mention pending state, got {detail_obj!r}"
    )
