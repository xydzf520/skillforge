"""GAP-2：`GET /api/inbox/overview` 概览聚合回归测试。

验证 pending / overdue / resolved_today / sla_hit_rate / avg_resolve_minutes /
backlog_by_age / approved_last_7d / rejected_last_7d 全部字段的边界逻辑。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import app.database as db_mod
from app.todos.models import AITodo, DecisionRequest
from app.todos.overview_service import get_overview
from tests.test_inbox_reports import _add_users_and_orgs


async def _seed(now: datetime) -> None:
    """造 5 条不同状态的 todo 给 alice。
    - pending in 1-6h bucket（2 小时前）
    - pending overdue, 48 小时前 → >24h bucket + overdue
    - approved on-time（今天早 1h 处理）
    - rejected late（decided_at > sla_at）
    - expired（终态 expired）
    """
    async with db_mod.async_session_factory() as s:
        reqs = [
            DecisionRequest(
                id=f"dr-ov-{i}",
                source_type="t",
                source_id=f"s-{i}",
                skill_id="skill-ov",  # 不存在的 skill，走 orphan 分支
                run_id=None,
                decision_log_id=None,
                kind="review",
                title=f"T{i}",
                summary=None,
                decision_mode="any_of",
                aggregate_status="pending",
                sla_at=now + timedelta(hours=2),
            )
            for i in range(5)
        ]
        # 调整每条 sla / aggregate_status
        reqs[1].sla_at = now - timedelta(hours=1)  # overdue
        reqs[2].sla_at = now + timedelta(hours=5)
        reqs[2].aggregate_status = "completed"
        reqs[2].aggregate_decision = "approved"
        reqs[3].sla_at = now - timedelta(hours=3)  # rejected late
        reqs[3].aggregate_status = "completed"
        reqs[3].aggregate_decision = "rejected"
        reqs[4].sla_at = now - timedelta(hours=10)
        reqs[4].aggregate_status = "completed"
        reqs[4].aggregate_decision = "expired"

        s.add_all(reqs)
        await s.flush()

        todos = [
            # 1 pending 2h 前
            AITodo(
                request_id="dr-ov-0",
                kind="review",
                assignee="alice",
                status="pending",
                created_at=now - timedelta(hours=2),
            ),
            # 2 pending overdue 48h 前
            AITodo(
                request_id="dr-ov-1",
                kind="review",
                assignee="alice",
                status="pending",
                created_at=now - timedelta(hours=48),
            ),
            # 3 approved on-time（3h 前建，1h 前决）
            AITodo(
                request_id="dr-ov-2",
                kind="review",
                assignee="alice",
                status="approved",
                created_at=now - timedelta(hours=3),
                decided_at=now - timedelta(hours=1),
                decided_by="alice",
            ),
            # 4 rejected late（25h 前建 2h 前决，但 sla_at 是 3h 前）
            AITodo(
                request_id="dr-ov-3",
                kind="review",
                assignee="alice",
                status="rejected",
                created_at=now - timedelta(hours=25),
                decided_at=now - timedelta(hours=2),
                decided_by="alice",
            ),
            # 5 expired
            AITodo(
                request_id="dr-ov-4",
                kind="review",
                assignee="alice",
                status="expired",
                created_at=now - timedelta(hours=20),
                decided_at=now - timedelta(hours=9),
            ),
        ]
        s.add_all(todos)
        await s.commit()


@pytest.mark.asyncio
async def test_overview_aggregates_all_kpi_fields(client):
    users = await _add_users_and_orgs()
    now = datetime(2026, 4, 18, 10, 0, 0)
    await _seed(now)

    async with db_mod.async_session_factory() as s:
        result = await get_overview(s, current_user=users["alice"], now=now)

    # pending / overdue
    assert result["pending"] == 2
    assert result["overdue"] == 1

    # resolved_today：3 条都在今天内决策（now 是 10:00，decided_at 都在今日 0:00 之后）
    assert result["resolved_today"] == 3

    # 近 7 天：1 approved + 1 rejected（expired 不计入 approved/rejected_last_7d，
    # 但计入 resolved_last_7d 当作被处理）
    assert result["approved_last_7d"] == 1
    assert result["rejected_last_7d"] == 1
    assert result["resolved_last_7d"] == 3

    # sla_hit_rate：approved on-time 算命中（1）；rejected late 不算命中（decided > sla）；
    # expired 必定不算命中 → 分子 1 / 分母 3 = 0.3333
    assert abs(result["sla_hit_rate"] - 1 / 3) < 0.01

    # avg_resolve_minutes：
    # approved：3h-1h = 2h = 120min；rejected：25h-2h = 23h = 1380min；
    # expired：20h-9h = 11h = 660min → 平均 (120+1380+660)/3 = 720min
    assert abs(result["avg_resolve_minutes"] - 720.0) < 0.01

    # backlog_by_age：只看 pending，1-6h=1 (2h)，>24h=1 (48h)，其余 0
    buckets = {b["bucket"]: b["count"] for b in result["backlog_by_age"]}
    assert buckets == {"<1h": 0, "1-6h": 1, "6-24h": 0, ">24h": 1}


@pytest.mark.asyncio
async def test_overview_empty_when_no_todos(client):
    users = await _add_users_and_orgs()
    now = datetime(2026, 4, 18, 10, 0, 0)

    async with db_mod.async_session_factory() as s:
        result = await get_overview(s, current_user=users["alice"], now=now)

    assert result["pending"] == 0
    assert result["overdue"] == 0
    assert result["resolved_today"] == 0
    assert result["sla_hit_rate"] == 1.0  # 无分母时约定 100%
    assert result["avg_resolve_minutes"] == 0.0
    assert all(b["count"] == 0 for b in result["backlog_by_age"])


@pytest.mark.asyncio
async def test_overview_bucket_boundaries(client):
    """测试 60min / 360min / 1440min 三个边界点：刚好在边界上应归入更大 bucket。"""
    users = await _add_users_and_orgs()
    now = datetime(2026, 4, 18, 12, 0, 0)

    async with db_mod.async_session_factory() as s:
        # 4 条 pending：59 分前（<1h）/ 60 分前（1-6h）/ 360 分前（6-24h）/ 1440 分前（>24h）
        reqs = [
            DecisionRequest(
                id=f"dr-b-{i}",
                source_type="t",
                source_id=f"s-{i}",
                skill_id="skill-b",
                run_id=None,
                decision_log_id=None,
                kind="review",
                title=f"B{i}",
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
                    request_id=f"dr-b-{i}",
                    kind="review",
                    assignee="alice",
                    status="pending",
                    created_at=now - delta,
                )
                for i, delta in enumerate(
                    [
                        timedelta(minutes=59),
                        timedelta(minutes=60),
                        timedelta(minutes=360),
                        timedelta(minutes=1440),
                    ]
                )
            ]
        )
        await s.commit()

    async with db_mod.async_session_factory() as s:
        result = await get_overview(s, current_user=users["alice"], now=now)

    buckets = {b["bucket"]: b["count"] for b in result["backlog_by_age"]}
    assert buckets == {"<1h": 1, "1-6h": 1, "6-24h": 1, ">24h": 1}
