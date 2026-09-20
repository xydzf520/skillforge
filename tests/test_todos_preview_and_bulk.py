"""GAP-3 / GAP-4：preview + bulk-summary 回归测试。"""

from __future__ import annotations

from datetime import datetime

import pytest

import app.database as db_mod
from app.skills.core.models import Skill
from app.todos.models import AITodo, DecisionRequest
from app.todos.service import todo_service
from tests.test_inbox_reports import (
    _add_decision_log,
    _add_execution_run,
    _add_skill,
    _add_users_and_orgs,
)


async def _seed_two_todos_alice_owns_both() -> tuple[int, int]:
    """alice 拥有 2 条 todo，bob 拥有 1 条；返回 alice 的两个 todo_id。"""
    await _add_skill(
        skill_id="skill-pv",
        name="Preview Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    async with db_mod.async_session_factory() as s:
        sk = await s.get(Skill, "skill-pv")
        sk.approval_level = 1
        await s.commit()
    await _add_execution_run("run-pv", trigger_type="manual")
    log_id = await _add_decision_log(
        skill_id="skill-pv",
        run_id="run-pv",
        created_at=datetime(2026, 4, 18, 8, 0, 0),
        reports=[
            {
                "channel": "dingtalk_card",
                "title": "Preview report",
                "summary": "Preview summary",
                "metrics": [
                    {"label": "ROI", "value": "1.2", "trend": "up"},
                    {"label": "GMV", "value": "100k", "trend": "flat"},
                ],
            }
        ],
    )
    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id="dr-pv",
            source_type="t",
            source_id="s",
            skill_id="skill-pv",
            run_id="run-pv",
            decision_log_id=log_id,
            kind="review",
            title="Preview test",
            summary="Sum",
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 4, 20, 8, 0, 0),
            payload={
                "suggested": ["建议 A", "建议 B"],
                "output": {"reasoning": ["第 1 步推理", "第 2 步推理"]},
            },
        )
        s.add(req)
        await s.flush()
        s.add_all(
            [
                AITodo(request_id="dr-pv", kind="review", assignee="alice", status="pending"),
                AITodo(request_id="dr-pv", kind="review", assignee="alice", status="pending"),
                AITodo(request_id="dr-pv", kind="review", assignee="bob", status="pending"),
            ]
        )
        await s.commit()
    async with db_mod.async_session_factory() as s:
        from sqlalchemy import select

        rows = (
            await s.execute(
                select(AITodo.id).where(AITodo.assignee == "alice").order_by(AITodo.id)
            )
        ).scalars().all()
    return int(rows[0]), int(rows[1])


@pytest.mark.asyncio
async def test_preview_returns_subset_of_detail(client):
    users = await _add_users_and_orgs()
    todo_a, _todo_b = await _seed_two_todos_alice_owns_both()

    async with db_mod.async_session_factory() as s:
        result = await todo_service.get_todo_preview(
            s, todo_id=todo_a, current_user=users["alice"]
        )

    assert result["id"] == todo_a
    assert result["title"] == "Preview test"
    assert result["status"] == "pending"
    assert result["suggested_actions"] == ["建议 A", "建议 B"]
    assert len(result["metrics"]) == 2
    assert result["metrics"][0]["label"] == "ROI"
    assert "第 1 步推理" in result["decision_reasoning"]
    assert result["skill_meta"]["id"] == "skill-pv"
    assert result["skill_meta"]["approval_level"] == 1
    # 不该出现 detail 才有的字段
    assert "payload" not in result
    assert "decision_chain" not in result
    assert "dispatch_tasks" not in result


@pytest.mark.asyncio
async def test_preview_and_list_project_low_consumption_video_findings(client):
    users = await _add_users_and_orgs()
    await _add_skill(
        skill_id="skill-samplebrand-low",
        name="SampleBrand Low Consumption",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    payload = {
        "person": "杨丰荧",
        "priority": "P2",
        "findings": [
            {
                "topic_key": "001经典｜美女口播（卖点）",
                "low_video": {
                    "video_id": "100659309",
                    "video_name": "示例企业四组_20260608-WLL-YFY_001BB",
                    "cost": 221.36,
                    "roi": 0.45,
                    "pay_amount": 99,
                    "click_rate": 2.5,
                    "convert_rate": 2.63,
                },
                "gap": {
                    "strongest_benchmark": {
                        "video_name": "高消耗样本",
                        "cost": 893.9,
                        "roi": 1.89,
                        "pay_amount": 1662,
                    }
                },
                "conclusions": ["转化率低于同主题高质量均值，需要强化下单理由。"],
                "improvement_actions": ["补一版强购买理由，把核心卖点提前。"],
            }
        ],
    }
    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id="dr-samplebrand-low",
            source_type="skill_execution_dispatch",
            source_id="run-samplebrand:P2",
            skill_id="skill-samplebrand-low",
            run_id="run-samplebrand",
            decision_log_id=None,
            kind="dispatch",
            title="P2｜杨丰荧 同主题低消耗视频改进",
            summary="需要按对标样本改版复测",
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 4, 20, 8, 0, 0),
            payload=payload,
        )
        s.add(req)
        await s.flush()
        todo = AITodo(request_id=req.id, kind="dispatch", assignee="alice", status="pending")
        s.add(todo)
        await s.flush()
        todo_id = todo.id
        await s.commit()

    async with db_mod.async_session_factory() as s:
        preview = await todo_service.get_todo_preview(
            s,
            todo_id=todo_id,
            current_user=users["alice"],
        )
        listing = await todo_service.list_todos(
            s,
            current_user=users["alice"],
            skill_id="skill-samplebrand-low",
        )

    assert preview["suggested_actions"] == ["补一版强购买理由，把核心卖点提前。"]
    assert preview["metrics"][0]["label"] == "低消耗视频"
    assert "转化率低于" in preview["decision_reasoning"][0]
    item = listing["items"][0]
    assert item["object_id"] == "100659309"
    assert item["object_title"] == "示例企业四组_20260608-WLL-YFY_001BB"
    assert item["metrics_preview"][1]["label"] == "消耗"
    assert item["primary_indicator"]["label"] == "ROI"


@pytest.mark.asyncio
async def test_preview_denies_cross_department_user(client):
    """非 assignee + 非本部门 + 非全局可见 → 403。"""
    users = await _add_users_and_orgs()
    todo_a, _ = await _seed_two_todos_alice_owns_both()

    from app.common.exceptions import AppError

    async with db_mod.async_session_factory() as s:
        with pytest.raises(AppError) as exc:
            await todo_service.get_todo_preview(
                s, todo_id=todo_a, current_user=users["bob"]  # bob 在 Dept B
            )
        assert exc.value.code == "AUTH_DEPARTMENT_DENIED"


@pytest.mark.asyncio
async def test_bulk_summary_silently_skips_unauthorized(client):
    """bulk-summary 把 bob 不可见的 alice 的 todo 静默跳过，不抛 403。"""
    users = await _add_users_and_orgs()
    todo_a, todo_b = await _seed_two_todos_alice_owns_both()
    # alice 视角：能看到自己的两条
    async with db_mod.async_session_factory() as s:
        result_alice = await todo_service.bulk_summary(
            s, todo_ids=[todo_a, todo_b], current_user=users["alice"]
        )
    assert set(result_alice["summaries"].keys()) == {todo_a, todo_b}

    # bob 视角（Dept B）：alice 的 todo 不可见 → summaries 为空
    async with db_mod.async_session_factory() as s:
        result_bob = await todo_service.bulk_summary(
            s, todo_ids=[todo_a, todo_b], current_user=users["bob"]
        )
    assert result_bob["summaries"] == {}


@pytest.mark.asyncio
async def test_bulk_summary_admin_sees_everything(client):
    users = await _add_users_and_orgs()
    todo_a, todo_b = await _seed_two_todos_alice_owns_both()

    async with db_mod.async_session_factory() as s:
        result = await todo_service.bulk_summary(
            s, todo_ids=[todo_a, todo_b], current_user=users["admin"]
        )
    assert set(result["summaries"].keys()) == {todo_a, todo_b}
    # schema 与 preview 对齐
    sample = result["summaries"][todo_a]
    assert {"id", "title", "status", "suggested_actions", "metrics", "skill_meta"}.issubset(sample.keys())


@pytest.mark.asyncio
async def test_bulk_summary_handles_empty_input(client):
    users = await _add_users_and_orgs()
    async with db_mod.async_session_factory() as s:
        result = await todo_service.bulk_summary(s, todo_ids=[], current_user=users["alice"])
    assert result == {"summaries": {}}
