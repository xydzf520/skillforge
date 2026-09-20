"""GAP-10/11/12：trends / related-timeline / dispatch-calendar 回归测试。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

import app.database as db_mod
from app.todos.models import AITodo, DecisionRequest, TodoDispatchTask
from app.todos.service import todo_service
from app.todos.trends_service import get_trends
from tests.test_inbox_reports import _add_skill, _add_users_and_orgs


@pytest.mark.asyncio
async def test_trends_fills_empty_days_and_groups_by_status(client):
    users = await _add_users_and_orgs()
    now = datetime(2026, 4, 18, 12, 0, 0)

    # 造：Day 0 (今天): 1 pending / 1 approved；Day -2: 2 rejected；其他日空
    async with db_mod.async_session_factory() as s:
        reqs = [
            DecisionRequest(
                id=f"dr-tr-{i}",
                source_type="t",
                source_id=f"s-{i}",
                skill_id="skill-tr",
                run_id=None,
                decision_log_id=None,
                kind="review",
                title=f"T{i}",
                summary=None,
                decision_mode="any_of",
                aggregate_status="pending",
                sla_at=now + timedelta(days=1),
            )
            for i in range(4)
        ]
        s.add_all(reqs)
        await s.flush()
        s.add_all(
            [
                AITodo(
                    request_id="dr-tr-0",
                    kind="review",
                    assignee="alice",
                    status="pending",
                    created_at=now,
                ),
                AITodo(
                    request_id="dr-tr-1",
                    kind="review",
                    assignee="alice",
                    status="approved",
                    created_at=now,
                    decided_at=now,
                ),
                AITodo(
                    request_id="dr-tr-2",
                    kind="review",
                    assignee="alice",
                    status="rejected",
                    created_at=now - timedelta(days=2),
                    decided_at=now - timedelta(days=2),
                ),
                AITodo(
                    request_id="dr-tr-3",
                    kind="review",
                    assignee="alice",
                    status="rejected",
                    created_at=now - timedelta(days=2),
                    decided_at=now - timedelta(days=2),
                ),
            ]
        )
        await s.commit()

    async with db_mod.async_session_factory() as s:
        result = await get_trends(s, current_user=users["alice"], days=7, now=now)

    assert result["days"] == 7
    assert len(result["points"]) == 7  # 包含空日
    # 最后一天（今天）应有 pending=1, approved=1
    today = result["points"][-1]
    assert today["day"] == now.date().isoformat()
    assert today["pending"] == 1
    assert today["approved"] == 1
    # 倒数第 3 天（-2）应有 rejected=2
    day_minus_2 = result["points"][-3]
    assert day_minus_2["rejected"] == 2
    # 其他日 total=0
    for i in (0, 1, 2, 3, 5):
        assert result["points"][i]["total"] == 0


@pytest.mark.asyncio
async def test_trends_range_end_uses_beijing_date(client):
    users = await _add_users_and_orgs()
    now = datetime(2026, 4, 18, 18, 0, 0)

    async with db_mod.async_session_factory() as s:
        result = await get_trends(
            s,
            current_user=users["alice"],
            days=7,
            now=now,
            tz_offset_minutes=480,
        )

    assert len(result["points"]) == 7
    assert result["points"][-1]["day"] == "2026-04-18"


@pytest.mark.asyncio
async def test_related_timeline_returns_history_for_same_skill(client):
    users = await _add_users_and_orgs()
    await _add_skill(
        skill_id="skill-rt",
        name="RT Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    base = datetime(2026, 4, 18, 12, 0, 0)

    async with db_mod.async_session_factory() as s:
        # 过去 3 条已决（approved, rejected, approved）+ 当前 1 条 pending
        for i, status in enumerate(["approved", "rejected", "approved", "pending"]):
            req = DecisionRequest(
                id=f"dr-rt-{i}",
                source_type="t",
                source_id=f"s-{i}",
                skill_id="skill-rt",
                run_id=None,
                decision_log_id=None,
                kind="review",
                title=f"Title {i}",
                summary=f"summary {i}",
                decision_mode="any_of",
                aggregate_status="completed" if status != "pending" else "pending",
                sla_at=base + timedelta(days=1),
            )
            s.add(req)
            await s.flush()
            s.add(
                AITodo(
                    request_id=f"dr-rt-{i}",
                    kind="review",
                    assignee="alice",
                    status=status,
                    created_at=base - timedelta(hours=i),
                    decided_at=(base - timedelta(hours=i)) if status != "pending" else None,
                    decided_by="alice" if status != "pending" else None,
                    decision_reason=f"reason {i}" if status != "pending" else None,
                )
            )
        await s.commit()
        # 找到 pending 的 todo_id
        pending_id = (
            await s.execute(
                select(AITodo.id).where(AITodo.status == "pending").where(
                    AITodo.request_id == "dr-rt-3"
                )
            )
        ).scalar()

    async with db_mod.async_session_factory() as s:
        result = await todo_service.get_related_timeline(
            s, todo_id=pending_id, current_user=users["alice"]
        )

    assert result["skill_id"] == "skill-rt"
    # 应返回 3 条已决（approved/rejected），排除自身 pending
    assert len(result["items"]) == 3
    decisions = [it["decision"] for it in result["items"]]
    assert set(decisions) == {"approved", "rejected"}
    # 排序：按 decided_at DESC
    assert result["items"][0]["decided_at"] >= result["items"][1]["decided_at"]


@pytest.mark.asyncio
async def test_dispatch_calendar_groups_by_deadline(client):
    users = await _add_users_and_orgs()
    await _add_skill(
        skill_id="skill-cal",
        name="Cal Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    day_a = datetime(2026, 4, 20, 10, 0, 0)
    day_b = datetime(2026, 4, 22, 14, 0, 0)

    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id="dr-cal",
            source_type="t",
            source_id="s",
            skill_id="skill-cal",
            run_id=None,
            decision_log_id=None,
            kind="dispatch",
            title="Cal",
            summary=None,
            decision_mode="any_of",
            aggregate_status="completed",
            aggregate_decision="approved",
            sla_at=day_a,
        )
        s.add(req)
        await s.flush()
        s.add_all(
            [
                TodoDispatchTask(
                    request_id="dr-cal",
                    executor="alice",
                    content="A1",
                    deadline=day_a,
                    status="sent",
                ),
                TodoDispatchTask(
                    request_id="dr-cal",
                    executor="alice",
                    content="A2",
                    deadline=day_a + timedelta(hours=2),  # 同一天
                    status="in_progress",
                ),
                TodoDispatchTask(
                    request_id="dr-cal",
                    executor="alice",
                    content="B1",
                    deadline=day_b,
                    status="done",
                ),
            ]
        )
        await s.commit()

    async with db_mod.async_session_factory() as s:
        result = await todo_service.get_dispatch_calendar(
            s,
            current_user=users["alice"],
            date_from=datetime(2026, 4, 19),
            date_to=datetime(2026, 4, 23),
        )

    assert result["date_from"] == "2026-04-19"
    assert len(result["days"]) == 2
    day_map = {d["day"]: d for d in result["days"]}
    assert day_map["2026-04-20"]["count"] == 2
    assert set(day_map["2026-04-20"]["statuses"].keys()) == {"sent", "in_progress"}
    assert day_map["2026-04-22"]["count"] == 1
    assert day_map["2026-04-22"]["statuses"]["done"] == 1
