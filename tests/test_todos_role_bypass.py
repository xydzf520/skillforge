"""H11：v2 角色 bypass 口径统一测试。

仅校验 ``is_global_inbox_reader`` 单元行为 + ``list_todos`` 是否对全局可见角色不打
部门 filter。无 DB seed 时只断言行为差异，不写大量数据。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

import app.database as db_mod
from app.auth.models import User
from app.common.exceptions import AppError
from app.org.models import OrgUnit, UserOrgMembership
from app.skills.core.models import Skill
from app.todos._acl import is_global_inbox_actor, is_global_inbox_reader
from app.todos.models import AITodo, DecisionRequest
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


# --- 单元：is_global_inbox_reader ---


@pytest.mark.parametrize(
    ("role", "can_view_all", "expected"),
    [
        ("system_admin", False, True),  # v2 主角色必须全局
        ("admin", False, True),  # legacy admin
        ("ai_engineer", False, True),  # legacy
        ("director", False, True),  # legacy
        ("biz_owner", False, False),
        ("dept_admin", False, False),
        ("observer", False, False),
        ("operator", False, False),
        ("biz_owner", True, True),  # can_view_all 一票通过
        ("observer", True, True),
    ],
)
def test_is_global_inbox_reader_matches_role_matrix_v2(role, can_view_all, expected):
    user = _u(f"u-{role}", role=role, dept="X", can_view_all=can_view_all)
    assert is_global_inbox_reader(user) is expected
    # actor 口径暂时与 reader 一致
    assert is_global_inbox_actor(user) is expected


def test_is_global_inbox_reader_handles_none_user():
    assert is_global_inbox_reader(None) is False
    assert is_global_inbox_actor(None) is False


# --- 集成：list_todos 全局角色不打部门 filter ---


async def _seed_two_dept_todos() -> dict[str, int]:
    """每个测试用户都被 assign 一条 Dept A + 一条 Dept B 的 todo，
    用来检验全局可见角色不会被部门 filter 漏掉跨部门的待办。
    """
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
        s.add_all(
            [
                Skill(
                    id="skill-A",
                    name="Skill A",
                    description="A",
                    department="Dept A",
                    org_unit_id="dept-a",
                    visibility="company",
                    status="active",
                ),
                Skill(
                    id="skill-B",
                    name="Skill B",
                    description="B",
                    department="Dept B",
                    org_unit_id="dept-b",
                    visibility="company",
                    status="active",
                ),
            ]
        )
        # 给 sa 分配 Dept A，让其可读，但 Dept B 必须依赖全局识别
        s.add_all(
            [
                _u("alice", role="biz_owner", dept="Dept A"),
                _u("bob", role="biz_owner", dept="Dept B"),
                _u("sa", role="system_admin", dept="Dept A"),
                _u("legacy_admin", role="admin", can_view_all=False, dept="Dept A"),
                _u("plain_observer", role="observer", dept="Dept A"),
            ]
        )
        s.add_all(
            [
                UserOrgMembership(user_id="alice", org_unit_id="dept-a"),
                UserOrgMembership(user_id="bob", org_unit_id="dept-b"),
                UserOrgMembership(user_id="sa", org_unit_id="dept-a"),
                UserOrgMembership(user_id="legacy_admin", org_unit_id="dept-a"),
                UserOrgMembership(
                    user_id="plain_observer", org_unit_id="dept-a"
                ),
            ]
        )
        # alice 的 todo 在 Dept A
        req_a = DecisionRequest(
            id="dr-a",
            source_type="t",
            source_id="s-a",
            skill_id="skill-A",
            run_id=None,
            decision_log_id=None,
            kind="review",
            title="Dept A todo",
            summary=None,
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 4, 25, 8, 0, 0),
            payload={},
        )
        # 注意：list_todos 用 assignee == current_user.id 过滤；要测"sa 是否能跨部门"
        # 必须把 todo 的 assignee 设成 sa，但 skill 跨在另一部门
        # → 让 alice 的 todo 在 Dept A，把 sa 设成 assignee 也在 Dept B（sa.department=Dept A）
        # 简化：直接给 sa 安排两条 assignee=sa 的 todo，分布两部门，看 sa 能不能两条都看到
        s.add(req_a)
        req_b = DecisionRequest(
            id="dr-b",
            source_type="t",
            source_id="s-b",
            skill_id="skill-B",
            run_id=None,
            decision_log_id=None,
            kind="review",
            title="Dept B todo",
            summary=None,
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 4, 25, 8, 0, 0),
            payload={},
        )
        s.add(req_b)
        await s.flush()
        # 给每个测试 actor 都建 (Dept A todo, Dept B todo)，
        # 这样 list_todos 的 assignee filter 不会成为副因素
        todos = []
        for assignee in ("sa", "legacy_admin", "plain_observer"):
            for req_id in ("dr-a", "dr-b"):
                todos.append(
                    AITodo(
                        request_id=req_id,
                        kind="review",
                        assignee=assignee,
                        status="pending",
                    )
                )
        s.add_all(todos)
        await s.commit()
    return {}


@pytest.mark.asyncio
async def test_list_todos_system_admin_sees_all_dept_todos(client):
    """v2 system_admin 默认看全局收件箱，不受部门和 assignee 限制。"""
    await _seed_two_dept_todos()
    async with db_mod.async_session_factory() as s:
        sa = await s.get(User, "sa")
        result = await todo_service.list_todos(s, current_user=sa)
    assert result["total"] == 6  # 3 个 assignee × Dept A/Dept B 都看到


@pytest.mark.asyncio
async def test_list_todos_system_admin_can_filter_to_assigned_to_me(client):
    """管理员点左侧「派给我」时，显式 assignee=me 仍只返回自己的待办。"""
    await _seed_two_dept_todos()
    async with db_mod.async_session_factory() as s:
        sa = await s.get(User, "sa")
        result = await todo_service.list_todos(s, current_user=sa, assignee="me")
    assert result["total"] == 2
    assert {item["assignee"] for item in result["items"]} == {"sa"}


@pytest.mark.asyncio
async def test_list_todos_system_admin_can_filter_any_assignee(client):
    """管理员可按具体 assignee 排查别人名下的来源待办。"""
    await _seed_two_dept_todos()
    async with db_mod.async_session_factory() as s:
        sa = await s.get(User, "sa")
        result = await todo_service.list_todos(s, current_user=sa, assignee="legacy_admin")
    assert result["total"] == 2
    assert {item["assignee"] for item in result["items"]} == {"legacy_admin"}


@pytest.mark.asyncio
async def test_list_todos_system_admin_source_skill_filter_matches_global_scope(client):
    """左侧来源 Skill 的全局计数和列表筛选必须同一口径。"""
    await _seed_two_dept_todos()
    async with db_mod.async_session_factory() as s:
        sa = await s.get(User, "sa")
        result = await todo_service.list_todos(
            s,
            current_user=sa,
            status="pending",
            skill_id="skill-B",
        )
    assert result["total"] == 3
    assert {item["title"] for item in result["items"]} == {"Dept B todo"}


@pytest.mark.asyncio
async def test_todo_stats_system_admin_uses_global_scope(client):
    await _seed_two_dept_todos()
    async with db_mod.async_session_factory() as s:
        sa = await s.get(User, "sa")
        result = await todo_service.get_stats(s, sa)
    assert result["pending"] == 6


@pytest.mark.asyncio
async def test_list_todos_legacy_admin_also_sees_all(client):
    await _seed_two_dept_todos()
    async with db_mod.async_session_factory() as s:
        admin = await s.get(User, "legacy_admin")
        result = await todo_service.list_todos(s, current_user=admin)
    # legacy admin 不带 can_view_all 也应当被全局识别（is_global_inbox_reader）
    assert result["total"] == 6


@pytest.mark.asyncio
async def test_list_todos_plain_observer_cannot_filter_other_assignee(client):
    await _seed_two_dept_todos()
    async with db_mod.async_session_factory() as s:
        observer = await s.get(User, "plain_observer")
        with pytest.raises(AppError) as exc:
            await todo_service.list_todos(s, current_user=observer, assignee="sa")
    assert getattr(exc.value, "code", None) == "AUTH_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_list_todos_plain_observer_filtered_to_own_dept(client):
    await _seed_two_dept_todos()
    async with db_mod.async_session_factory() as s:
        observer = await s.get(User, "plain_observer")
        result = await todo_service.list_todos(s, current_user=observer)
    # observer.department=Dept A → 只能看 Dept A 的 todo
    assert result["total"] == 1
    assert result["items"][0]["title"] == "Dept A todo"
