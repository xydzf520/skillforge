"""GAP-1：`GET /api/todos/` 列表富字段回归测试。

验证 list_todos 返回新增的 suggested_actions / metrics_preview /
approval_level / requester / requester_department / aggregate_progress。
复用 tests/test_inbox_reports.py 的 seed helpers。
"""

from __future__ import annotations

from datetime import datetime

import pytest

import app.database as db_mod
from app.execution.models import DecisionLog
from app.skills.core.models import Skill
from app.todos.models import AITodo, DecisionRequest, TodoDispatchTask
from app.todos.service import todo_service
from tests.test_inbox_reports import (
    _add_decision_log,
    _add_execution_run,
    _add_skill,
    _add_users_and_orgs,
)


def test_extract_payload_metrics_prioritizes_tmall_daily_metrics():
    payload = {
        "key_metrics": [
            {"label": "实时下滑", "value": "-20%", "status": "下降"},
            {"label": "全店访客排名", "value": "第 1"},
            {"label": "全店成交排名", "value": "第 2"},
        ],
        "metric_sections": [
            {
                "key": "daily_yesterday",
                "metrics": [
                    {"name": "免费搜索/免费访客环比", "value": "-6.67%", "status": "下降", "delta": "28 / 30"},
                    {"name": "搜索/免费转化环比", "value": "-64.29%", "status": "下降"},
                    {"name": "付费访客环比", "value": "+12.0%", "status": "上升"},
                    {"name": "评价证据", "value": "已采集", "status": "正常"},
                ],
            }
        ],
    }

    preview = todo_service._extract_payload_metrics(payload, limit=3)

    assert [row["label"] for row in preview] == [
        "免费搜索/免费访客环比",
        "搜索/免费转化环比",
        "付费访客环比",
    ]


def test_extract_payload_metrics_projects_low_consumption_video_findings():
    payload = {
        "person": "杨丰荧",
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

    metrics = todo_service._extract_payload_metrics(payload, limit=3)

    assert [item["label"] for item in metrics] == ["低消耗视频", "消耗", "ROI"]
    assert metrics[1]["value"] == "221.36"
    assert metrics[1]["delta"] == "对标 893.9"
    assert todo_service._extract_suggested_actions(payload) == ["补一版强购买理由，把核心卖点提前。"]
    assert "转化率低于" in todo_service._extract_finding_reasoning(payload)[0]
    assert "低消耗视频" in todo_service._extract_finding_summary(payload)


def test_format_number_preserves_integer_trailing_zeroes():
    assert todo_service._format_number(330.0) == "330"
    assert todo_service._format_number(100.0) == "100"
    assert todo_service._format_number(0.0) == "0"
    assert todo_service._format_number(12.3) == "12.3"


@pytest.mark.asyncio
async def test_todo_detail_builds_local_snapshot_compares(client):
    users = await _add_users_and_orgs()
    await _add_skill(
        skill_id="skill-local-snapshot",
        name="Local Snapshot Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_execution_run("run-baseline-snapshot", trigger_type="node_scheduler")
    await _add_execution_run("run-current-snapshot", trigger_type="node_scheduler")

    async with db_mod.async_session_factory() as s:
        baseline_log = DecisionLog(
            run_id="run-baseline-snapshot",
            skill_id="skill-local-snapshot",
            input_snapshot={},
            output_result={
                "数据采集快照": {
                    "as_of_time": "2026-05-25 07:52:31",
                    "captured_at": "2026-05-28T09:44:31+08:00",
                    "items": {
                        "800559674590": {
                            "paid": {
                                "charge": 1922.57,
                                "roi": 0.4685,
                                "ppc": 8.1811,
                                "paid_visitor_count": 197,
                                "paid_conversion_rate": 0.02030456852791878,
                            }
                        }
                    }
                }
            },
            suggested_action={},
            created_at=datetime(2026, 5, 25, 8, 47, 29),
            is_sandbox=False,
        )
        current_log = DecisionLog(
            run_id="run-current-snapshot",
            skill_id="skill-local-snapshot",
            input_snapshot={},
            output_result={
                "0855快照对比": {
                    "baseline_captured_at": "2026-05-25T08:47:29+08:00",
                    "items": [
                        {
                            "item_id": "800559674590",
                            "period": {
                                "current_label": "2026-05-25 07:50~2026-05-26 07:50",
                                "compare_label": "2026-05-24 07:50~2026-05-25 07:50",
                            },
                            "free_search_visitor_count": 34,
                            "free_search_visitor_prev": 39,
                            "free_search_visitor_change_pct": -12.82,
                            "free_search_conversion_rate": 0.058823529411764705,
                            "free_search_conversion_prev": 0.07692307692307693,
                            "free_search_conversion_change_pct": -23.53,
                            "paid_charge": 1596.14,
                            "paid_charge_prev": 1922.57,
                            "paid_charge_change_pct": -16.98,
                        }
                    ],
                },
                "数据采集快照": {
                    "as_of_time": "2026-05-26 08:49:24",
                    "items": {
                        "800559674590": {
                            "paid": {
                                "charge": 1596.14,
                                "roi": 1.019,
                                "ppc": 7.7483,
                                "paid_visitor_count": 187,
                                "paid_conversion_rate": 0.0427807486631016,
                            }
                        }
                    }
                },
            },
            suggested_action={},
            created_at=datetime(2026, 5, 26, 8, 49, 24),
            is_sandbox=False,
        )
        s.add_all([baseline_log, current_log])
        await s.flush()
        req = DecisionRequest(
            id="dr-local-snapshot",
            source_type="skill_execution_dispatch",
            source_id="run-current-snapshot:todo",
            skill_id="skill-local-snapshot",
            run_id="run-current-snapshot",
            decision_log_id=current_log.id,
            kind="dispatch",
            title="P1｜商品诊断卡｜800559674590",
            summary="本地快照对比",
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 5, 26, 18, 0, 0),
            payload={"item_id": "800559674590", "card_type": "product_decline_decision_card"},
        )
        s.add(req)
        await s.flush()
        todo = AITodo(request_id=req.id, kind="dispatch", assignee="alice", status="pending")
        s.add(todo)
        await s.commit()
        todo_id = todo.id

    async with db_mod.async_session_factory() as s:
        detail = await todo_service.get_todo_detail(
            s,
            todo_id=todo_id,
            current_user=users["alice"],
        )

    free_metrics = {
        row["key"]: row for row in detail["payload"]["free_search_compare"]["metrics"]
    }
    paid_metrics = {
        row["key"]: row for row in detail["payload"]["paid_realtime_compare"]["metrics"]
    }
    assert free_metrics["visitor"]["current_text"] == "34"
    assert free_metrics["visitor"]["previous_text"] == "39"
    assert free_metrics["visitor"]["change_text"] == "-12.82%"
    assert free_metrics["conversion"]["current_text"] == "5.88%"
    assert free_metrics["conversion"]["previous_text"] == "7.69%"
    assert paid_metrics["charge"]["current_text"] == "¥1596.14"
    assert paid_metrics["charge"]["previous_text"] == "¥1922.57"
    assert paid_metrics["roi"]["current_text"] == "1.019"
    assert paid_metrics["roi"]["previous_text"] == "0.4685"
    assert paid_metrics["cpc"]["current_text"] == "¥7.7483"
    assert paid_metrics["cpc"]["previous_text"] == "¥8.1811"


@pytest.mark.asyncio
async def test_todo_detail_uses_yesterday_same_time_paid_snapshot_within_ten_minutes(client):
    users = await _add_users_and_orgs()
    await _add_skill(
        skill_id="skill-paid-same-time",
        name="Paid Same Time Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_execution_run("run-paid-same-time-baseline", trigger_type="node_scheduler")
    await _add_execution_run("run-paid-same-time-current", trigger_type="node_scheduler")

    async with db_mod.async_session_factory() as s:
        same_time_log = DecisionLog(
            run_id="run-paid-same-time-baseline",
            skill_id="skill-paid-same-time",
            input_snapshot={},
            output_result={
                "数据采集快照": {
                    "as_of_time": "2026-05-28 07:52:31",
                    "captured_at": "2026-05-28T09:44:31+08:00",
                    "items": {
                        "800729240226": {
                            "paid": {
                                "charge": 837.66,
                                "roi": 0.9061,
                                "ppc": 7.5465,
                            },
                            "periods": {
                                "flow": {
                                    "current_label": "2026-05-27 07:50~2026-05-28 07:50",
                                }
                            },
                        }
                    },
                }
            },
            suggested_action={},
            created_at=datetime(2026, 5, 28, 9, 44, 31),
            is_sandbox=False,
        )
        outside_window_log = DecisionLog(
            run_id="run-paid-same-time-baseline",
            skill_id="skill-paid-same-time",
            input_snapshot={},
            output_result={
                "数据采集快照": {
                    "as_of_time": "2026-05-28 08:15:00",
                    "items": {
                        "800729240226": {
                            "paid": {
                                "charge": 9999,
                                "roi": 9,
                                "ppc": 99,
                            }
                        }
                    },
                }
            },
            suggested_action={},
            created_at=datetime(2026, 5, 28, 8, 15, 0),
            is_sandbox=False,
        )
        current_log = DecisionLog(
            run_id="run-paid-same-time-current",
            skill_id="skill-paid-same-time",
            input_snapshot={},
            output_result={
                "0855快照对比": {"items": []},
                "数据采集快照": {
                    "as_of_time": "2026-05-29 07:50:14",
                    "items": {
                        "800729240226": {
                            "paid": {
                                "charge": 325.38,
                                "roi": 1.1052,
                                "ppc": 5.8104,
                            },
                            "item360": {
                                "free_visitor_count": 98,
                                "free_visitor_prev": 104,
                            },
                            "periods": {
                                "flow": {
                                    "current_label": "2026-05-28 07:50~2026-05-29 07:50",
                                    "compare_label": "2026-05-27 07:50~2026-05-28 07:50",
                                }
                            },
                        }
                    },
                },
                "付费端诊断报告": [
                    {
                        "item_id": "800729240226",
                        "charge_value": 325.38,
                        "charge_prev": 2662.15,
                        "roi_value": 1.1052,
                        "roi_delta": 0.1772,
                        "ppc_value": 5.8104,
                        "ppc_delta": -2.3557,
                    }
                ],
            },
            suggested_action={},
            created_at=datetime(2026, 5, 29, 10, 24, 52),
            is_sandbox=False,
        )
        s.add_all([same_time_log, outside_window_log, current_log])
        await s.flush()
        req = DecisionRequest(
            id="dr-paid-same-time",
            source_type="skill_execution_dispatch",
            source_id="run-paid-same-time-current:todo",
            skill_id="skill-paid-same-time",
            run_id="run-paid-same-time-current",
            decision_log_id=current_log.id,
            kind="dispatch",
            title="P1｜商品诊断卡｜800729240226",
            summary="付费昨日同刻快照回填",
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 5, 29, 18, 0, 0),
            payload={"item_id": "800729240226", "card_type": "product_decline_decision_card"},
        )
        s.add(req)
        await s.flush()
        todo = AITodo(request_id=req.id, kind="dispatch", assignee="alice", status="pending")
        s.add(todo)
        await s.commit()
        todo_id = todo.id

    async with db_mod.async_session_factory() as s:
        detail = await todo_service.get_todo_detail(
            s,
            todo_id=todo_id,
            current_user=users["alice"],
        )

    paid_metrics = {
        row["key"]: row for row in detail["payload"]["paid_realtime_compare"]["metrics"]
    }
    assert paid_metrics["charge"]["previous_text"] == "¥837.66"
    assert paid_metrics["charge"]["change_text"] == "-61.16%"
    assert paid_metrics["roi"]["previous_text"] == "0.9061"
    assert paid_metrics["roi"]["change_text"] == "+21.97%"
    assert paid_metrics["cpc"]["previous_text"] == "¥7.5465"
    assert paid_metrics["cpc"]["change_text"] == "-23.01%"


@pytest.mark.asyncio
async def test_todo_detail_uses_paid_report_zero_values_when_snapshot_paid_is_null(client):
    users = await _add_users_and_orgs()
    await _add_skill(
        skill_id="skill-paid-zero-snapshot",
        name="Paid Zero Snapshot Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_execution_run("run-paid-zero-current", trigger_type="node_scheduler")

    async with db_mod.async_session_factory() as s:
        current_log = DecisionLog(
            run_id="run-paid-zero-current",
            skill_id="skill-paid-zero-snapshot",
            input_snapshot={},
            output_result={
                "0855快照对比": {
                    "baseline_captured_at": "2026-05-25T13:47:07+08:00",
                    "items": [
                        {
                            "item_id": "800685473349",
                            "period": {
                                "current_label": "2026-05-25 12:50~2026-05-26 12:50",
                                "compare_label": "2026-05-24 12:50~2026-05-25 12:50",
                            },
                            "paid_charge": None,
                            "paid_charge_prev": 5110.89,
                            "paid_charge_change_pct": -100.0,
                        }
                    ],
                },
                "数据采集快照": {
                    "items": {
                        "800685473349": {
                            "paid": {
                                "charge": None,
                                "roi": None,
                                "ppc": None,
                                "paid_visitor_count": 292,
                                "paid_conversion_rate": 0.08904109589041095,
                            }
                        }
                    }
                },
                "付费端诊断报告": [
                    {
                        "item_id": "800685473349",
                        "charge_value": 0.0,
                        "roi_value": 0.0,
                        "ppc_value": 0.0,
                        "charge_prev": 0.0,
                        "roi_prev": 0.0,
                        "ppc_prev": 0.0,
                    }
                ],
            },
            suggested_action={},
            created_at=datetime(2026, 5, 26, 13, 48, 53),
            is_sandbox=False,
        )
        s.add(current_log)
        await s.flush()
        req = DecisionRequest(
            id="dr-paid-zero-snapshot",
            source_type="skill_execution_dispatch",
            source_id="run-paid-zero-current:todo",
            skill_id="skill-paid-zero-snapshot",
            run_id="run-paid-zero-current",
            decision_log_id=current_log.id,
            kind="dispatch",
            title="P0｜商品诊断卡｜800685473349",
            summary="付费聚合值为零",
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 5, 26, 18, 0, 0),
            payload={"item_id": "800685473349", "card_type": "product_decline_decision_card"},
        )
        s.add(req)
        await s.flush()
        todo = AITodo(request_id=req.id, kind="dispatch", assignee="alice", status="pending")
        s.add(todo)
        await s.commit()
        todo_id = todo.id

    async with db_mod.async_session_factory() as s:
        detail = await todo_service.get_todo_detail(
            s,
            todo_id=todo_id,
            current_user=users["alice"],
        )

    paid_metrics = {
        row["key"]: row for row in detail["payload"]["paid_realtime_compare"]["metrics"]
    }
    assert paid_metrics["charge"]["current_text"] == "¥0"
    assert paid_metrics["charge"]["previous_text"] == "¥5110.89"
    assert paid_metrics["charge"]["change_text"] == "-100.00%"
    assert paid_metrics["roi"]["current_text"] == "0"
    assert paid_metrics["roi"]["previous_text"] == "0"
    assert paid_metrics["cpc"]["current_text"] == "¥0"
    assert paid_metrics["cpc"]["previous_text"] == "¥0"


@pytest.mark.asyncio
async def test_todo_detail_builds_compares_from_collection_snapshot_when_0855_items_empty(client):
    users = await _add_users_and_orgs()
    await _add_skill(
        skill_id="skill-collection-snapshot",
        name="Collection Snapshot Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_execution_run("run-collection-snapshot", trigger_type="node_scheduler")

    async with db_mod.async_session_factory() as s:
        current_log = DecisionLog(
            run_id="run-collection-snapshot",
            skill_id="skill-collection-snapshot",
            input_snapshot={},
            output_result={
                "0855快照对比": {
                    "items": [],
                },
                "数据采集快照": {
                    "items": {
                        "800729240226": {
                            "item360": {
                                "free_visitor_count": 98,
                                "free_visitor_prev": 104,
                                "free_conversion_rate": 0.05102040816326531,
                                "free_conversion_prev": 0.08653846153846154,
                            },
                            "paid": {
                                "charge": 325.38,
                                "roi": 1.1052,
                                "ppc": 5.8104,
                            },
                            "periods": {
                                "flow": {
                                    "current_label": "2026-05-28 07:50~2026-05-29 07:50",
                                    "compare_label": "2026-05-27 07:50~2026-05-28 07:50",
                                }
                            },
                        }
                    }
                },
                "免费流分析结果": [
                    {
                        "item_id": "800729240226",
                        "visitor_change_pct": -5.77,
                        "conversion_change_pct": -41.04,
                    }
                ],
                "付费端诊断报告": [
                    {
                        "item_id": "800729240226",
                        "charge_value": 325.38,
                        "charge_prev": 2662.15,
                        "charge_change_pct": -87.78,
                        "roi_value": 1.1052,
                        "roi_delta": 0.1772,
                        "roi_change_pct": 19.09,
                        "ppc_value": 5.8104,
                        "ppc_delta": -2.3557,
                        "ppc_change_pct": -28.85,
                    }
                ],
            },
            suggested_action={},
            created_at=datetime(2026, 5, 29, 7, 53, 31),
            is_sandbox=False,
        )
        s.add(current_log)
        await s.flush()
        req = DecisionRequest(
            id="dr-collection-snapshot",
            source_type="skill_execution_dispatch",
            source_id="run-collection-snapshot:todo",
            skill_id="skill-collection-snapshot",
            run_id="run-collection-snapshot",
            decision_log_id=current_log.id,
            kind="dispatch",
            title="P1｜商品诊断卡｜800729240226",
            summary="数据采集快照回填",
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 5, 29, 18, 0, 0),
            payload={"item_id": "800729240226", "card_type": "product_decline_decision_card"},
        )
        s.add(req)
        await s.flush()
        todo = AITodo(request_id=req.id, kind="dispatch", assignee="alice", status="pending")
        s.add(todo)
        await s.commit()
        todo_id = todo.id

    async with db_mod.async_session_factory() as s:
        detail = await todo_service.get_todo_detail(
            s,
            todo_id=todo_id,
            current_user=users["alice"],
        )

    assert detail["payload"]["paid_realtime_compare"]["period"] == {
        "current_label": "2026-05-28 07:50~2026-05-29 07:50",
        "compare_label": "2026-05-27 07:50~2026-05-28 07:50",
    }
    free_metrics = {
        row["key"]: row for row in detail["payload"]["free_search_compare"]["metrics"]
    }
    paid_metrics = {
        row["key"]: row for row in detail["payload"]["paid_realtime_compare"]["metrics"]
    }
    assert free_metrics["visitor"]["current_text"] == "98"
    assert free_metrics["visitor"]["previous_text"] == "104"
    assert free_metrics["visitor"]["change_text"] == "-5.77%"
    assert free_metrics["conversion"]["current_text"] == "5.10%"
    assert free_metrics["conversion"]["previous_text"] == "8.65%"
    assert free_metrics["conversion"]["change_text"] == "-41.04%"
    assert paid_metrics["charge"]["current_text"] == "¥325.38"
    assert paid_metrics["charge"]["previous_text"] == "¥2662.15"
    assert paid_metrics["charge"]["change_text"] == "-87.78%"
    assert paid_metrics["roi"]["current_text"] == "1.1052"
    assert paid_metrics["roi"]["previous_text"] == "0.928"
    assert paid_metrics["roi"]["change_text"] == "+19.09%"
    assert paid_metrics["cpc"]["current_text"] == "¥5.8104"
    assert paid_metrics["cpc"]["previous_text"] == "¥8.1661"
    assert paid_metrics["cpc"]["change_text"] == "-28.85%"


async def _seed_one_enriched_todo(*, approval_level: int = 2, requester_id: str = "bob") -> int:
    """造一条完整的 enriched 场景：Skill(approval_level) + DecisionLog(metrics+
    requester) + DecisionRequest(payload.suggested) + 两条 AITodo（会签）。
    返回 decision_log_id 便于断言。
    """
    await _add_skill(
        skill_id="skill-enrich",
        name="Enrich Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    async with db_mod.async_session_factory() as s:
        skill = await s.get(Skill, "skill-enrich")
        skill.approval_level = approval_level
        await s.commit()

    await _add_execution_run("run-enrich", trigger_type="manual")
    log_id = await _add_decision_log(
        skill_id="skill-enrich",
        run_id="run-enrich",
        created_at=datetime(2026, 4, 18, 8, 0, 0),
        reports=[
            {
                "channel": "dingtalk_card",
                "title": "Budget review",
                "summary": "ROI uplift alert",
                "metrics": [
                    {"label": "ROI", "value": "1.6", "trend": "up", "delta": "+8%"},
                    {"label": "GMV", "value": "120k", "trend": "down", "delta": "-3%"},
                    {"label": "Visitors", "value": "45k", "trend": "up"},
                    {"label": "CTR", "value": "4.2%"},  # 第 4 条不应出现在 preview
                ],
            }
        ],
    )
    # 注入 requester 候选（DecisionRequest 无此列，走 input_snapshot）
    async with db_mod.async_session_factory() as s:
        log = await s.get(DecisionLog, log_id)
        snapshot = dict(log.input_snapshot or {})
        snapshot["requester"] = requester_id
        log.input_snapshot = snapshot
        await s.commit()

    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id="dr-enrich",
            source_type="skill_execution_review",
            source_id="run-enrich:1",
            skill_id="skill-enrich",
            run_id="run-enrich",
            decision_log_id=log_id,
            kind="review",
            title="Enrich test",
            summary="Summary",
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 4, 20, 8, 0, 0),
            payload={
                "suggested": [
                    "下调预算 20%",
                    "同部门复核后再执行",
                    "补充同期竞品数据后下结论",
                ],
                "output": {"summary": "ok"},
            },
        )
        s.add(req)
        await s.flush()
        s.add_all(
            [
                AITodo(
                    request_id="dr-enrich",
                    kind="review",
                    assignee="alice",
                    status="pending",
                ),
                AITodo(
                    request_id="dr-enrich",
                    kind="review",
                    assignee="manager",
                    status="approved",
                    decided_at=datetime(2026, 4, 18, 9, 0, 0),
                    decided_by="manager",
                ),
            ]
        )
        await s.commit()
    return log_id


@pytest.mark.asyncio
async def test_list_todos_includes_rich_enrichment_fields(client):
    users = await _add_users_and_orgs()
    await _seed_one_enriched_todo(approval_level=2, requester_id="bob")

    async with db_mod.async_session_factory() as s:
        result = await todo_service.list_todos(
            s,
            current_user=users["alice"],
            status="pending",
            page=1,
            page_size=10,
        )

    assert result["total"] == 1, "alice 应该只看到自己的 pending todo"
    item = result["items"][0]

    # suggested_actions：三条，顺序一致，被截断成 ≤60 字
    assert item["suggested_actions"] == [
        "下调预算 20%",
        "同部门复核后再执行",
        "补充同期竞品数据后下结论",
    ]

    # metrics_preview：只取 report[0].metrics 的前 3 条
    assert len(item["metrics_preview"]) == 3
    assert item["metrics_preview"][0]["label"] == "ROI"
    assert item["metrics_preview"][0]["trend"] == "up"
    labels = [m["label"] for m in item["metrics_preview"]]
    assert "CTR" not in labels, "第 4 条 metric 应被裁掉"

    # approval_level：Skill.approval_level=2 映射成 L2
    assert item["approval_level"] == "L2"

    # requester：取自 DecisionLog.input_snapshot.requester
    assert item["requester"] == {"id": "bob", "name": "bob"}
    assert item["requester_department"] == "Dept B"

    # aggregate_progress：2 个 AITodo（alice pending + manager approved）
    assert item["aggregate_progress"] == {
        "total": 2,
        "done": 1,
        "waiting_on": ["alice"],
    }


@pytest.mark.asyncio
async def test_list_todos_handles_missing_decision_log_and_skill(client):
    """没有 decision_log / skill / payload.suggested 时新字段应给空值兜底。"""
    users = await _add_users_and_orgs()
    # 无 Skill；无 DecisionLog；无 payload.suggested
    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id="dr-bare",
            source_type="skill_execution_review",
            source_id="run-bare:1",
            skill_id="skill-not-exist",
            run_id=None,
            decision_log_id=None,
            kind="review",
            title="Bare",
            summary=None,
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 4, 20, 8, 0, 0),
            payload={},
        )
        s.add(req)
        await s.flush()
        s.add(AITodo(request_id="dr-bare", kind="review", assignee="alice", status="pending"))
        await s.commit()

    async with db_mod.async_session_factory() as s:
        result = await todo_service.list_todos(
            s,
            current_user=users["alice"],
            status="pending",
            page=1,
            page_size=10,
        )

    item = result["items"][0]
    assert item["suggested_actions"] == []
    assert item["metrics_preview"] == []
    assert item["approval_level"] is None
    assert item["requester"] is None
    assert item["requester_department"] is None
    assert item["aggregate_progress"] == {"total": 1, "done": 0, "waiting_on": ["alice"]}


@pytest.mark.asyncio
async def test_list_todos_suggested_truncates_long_strings(client):
    """suggested_actions 每条截断到 60 字符。"""
    users = await _add_users_and_orgs()
    long_action = "这是一个非常冗长的建议，超过六十字符上限。" + "x" * 100
    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id="dr-long",
            source_type="t",
            source_id="s",
            skill_id="skill-long",
            run_id=None,
            decision_log_id=None,
            kind="review",
            title="Long",
            summary=None,
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=datetime(2026, 4, 20, 8, 0, 0),
            payload={"suggested": [long_action, "短建议"]},
        )
        s.add(req)
        await s.flush()
        s.add(AITodo(request_id="dr-long", kind="review", assignee="alice", status="pending"))
        await s.commit()

    async with db_mod.async_session_factory() as s:
        result = await todo_service.list_todos(
            s, current_user=users["alice"], status="pending", page=1, page_size=10
        )
    item = result["items"][0]
    assert len(item["suggested_actions"]) == 2
    assert len(item["suggested_actions"][0]) == 60  # 截断到 60
    assert item["suggested_actions"][1] == "短建议"


@pytest.mark.asyncio
async def test_list_todos_searches_item_id_and_sorts_business_rank_fields(client):
    users = await _add_users_and_orgs()
    async with db_mod.async_session_factory() as s:
        for idx, payload in enumerate([
            {
                "item_id": "8001",
                "item_title": "超薄组合装",
                "object_id": "8001",
                "object_title": "超薄组合装",
                "ranking_visitors": 53,
                "ranking_orders": 35,
                "decline_coef": 0.3202,
            },
            {
                "item_id": "9002",
                "item_title": "家庭装",
                "object_id": "9002",
                "object_title": "家庭装",
                "ranking_visitors": 10,
                "ranking_orders": 80,
                "decline_coef": 0.1201,
            },
        ]):
            req = DecisionRequest(
                id=f"dr-rank-{idx}",
                source_type="skill_execution_dispatch",
                source_id=f"run-rank:{idx}",
                skill_id="skill-rank",
                run_id=None,
                decision_log_id=None,
                kind="dispatch",
                title=f"商品诊断 {payload['item_title']}",
                summary=None,
                decision_mode="any_of",
                aggregate_status="pending",
                sla_at=datetime(2026, 4, 20, 8, 0, 0),
                payload=payload,
            )
            s.add(req)
            await s.flush()
            s.add(AITodo(request_id=req.id, kind="dispatch", assignee="alice", status="pending"))
        await s.commit()

    async with db_mod.async_session_factory() as s:
        search_result = await todo_service.list_todos(
            s,
            current_user=users["alice"],
            status="pending",
            search="8001",
            page=1,
            page_size=10,
        )
        visitor_sorted = await todo_service.list_todos(
            s,
            current_user=users["alice"],
            status="pending",
            sort_by="ranking_visitors",
            sort_order="asc",
            page=1,
            page_size=10,
        )
        decline_sorted = await todo_service.list_todos(
            s,
            current_user=users["alice"],
            status="pending",
            sort_by="decline_coef",
            sort_order="desc",
            page=1,
            page_size=10,
        )

    assert search_result["total"] == 1
    assert search_result["items"][0]["object_id"] == "8001"
    assert [item["object_id"] for item in visitor_sorted["items"]] == ["9002", "8001"]
    assert [item["object_id"] for item in decline_sorted["items"]] == ["8001", "9002"]


@pytest.mark.asyncio
async def test_resolved_by_peer_list_includes_dispatch_completion_note(client):
    users = await _add_users_and_orgs()
    await _add_skill(
        skill_id="skill-dispatch-completion",
        name="Dispatch Completion Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )

    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id="dr-dispatch-completion",
            source_type="skill_execution_dispatch",
            source_id="run-dispatch-completion:todo",
            skill_id="skill-dispatch-completion",
            run_id="run-dispatch-completion",
            decision_log_id=None,
            kind="dispatch",
            title="商品诊断执行",
            summary="主管通过后派发运营执行",
            decision_mode="any_of",
            aggregate_status="completed",
            aggregate_decision="approved",
            sla_at=datetime(2026, 4, 23, 18, 0, 0),
            payload={},
        )
        s.add(req)
        await s.flush()
        s.add_all([
            AITodo(
                request_id=req.id,
                kind="dispatch",
                assignee="alice",
                status="resolved_by_peer",
                decided_at=datetime(2026, 4, 23, 9, 0, 0),
                decided_by="manager",
            ),
            AITodo(
                request_id=req.id,
                kind="dispatch",
                assignee="manager",
                status="approved",
                decided_at=datetime(2026, 4, 23, 9, 0, 0),
                decided_by="manager",
            ),
            TodoDispatchTask(
                request_id=req.id,
                executor="bob",
                content="复核活动是否掉线并截图确认",
                status="done",
                ack_at=datetime(2026, 4, 23, 10, 15, 0),
                ack_note="已核对，活动在线，主图已提交鹿班测图",
                ack_channel="dingtalk_open",
            ),
        ])
        await s.commit()

    async with db_mod.async_session_factory() as s:
        result = await todo_service.list_todos(
            s,
            current_user=users["alice"],
            status="resolved_by_peer",
            page=1,
            page_size=10,
        )

    assert result["total"] == 1
    item = result["items"][0]
    assert item["dispatch_done_count"] == 1
    assert item["latest_dispatch_completion"]["executor"] == "bob"
    assert item["latest_dispatch_completion"]["executor_name"] == "bob"
    assert item["latest_dispatch_completion"]["ack_note"] == "已核对，活动在线，主图已提交鹿班测图"
    assert item["dispatch_completions"][0]["content"] == "复核活动是否掉线并截图确认"


@pytest.mark.asyncio
async def test_feedback_done_filter_and_search_dispatch_feedback(client):
    users = await _add_users_and_orgs()
    await _add_skill(
        skill_id="skill-feedback-search",
        name="Feedback Search Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )

    async with db_mod.async_session_factory() as s:
        req = DecisionRequest(
            id="dr-feedback-search",
            source_type="skill_execution_dispatch",
            source_id="run-feedback-search:todo",
            skill_id="skill-feedback-search",
            run_id="run-feedback-search",
            decision_log_id=None,
            kind="dispatch",
            title="P1｜商品诊断卡｜800559674590",
            summary="主管通过后派发运营执行",
            decision_mode="any_of",
            aggregate_status="completed",
            aggregate_decision="approved",
            sla_at=datetime(2026, 5, 26, 18, 0, 0),
            payload={
                "priority": "P1",
                "input": {"item_id": "800559674590", "item_title": "黑金001三合一"},
            },
        )
        s.add(req)
        await s.flush()
        s.add_all([
            AITodo(request_id=req.id, kind="dispatch", assignee="alice", status="approved"),
            TodoDispatchTask(
                request_id=req.id,
                executor="bob",
                content="复核黑金001搜索承接",
                extra={"item_id": "800559674590", "priority": "P1"},
                status="done",
                ack_at=datetime(2026, 5, 26, 10, 15, 0),
                ack_note="已反馈：补充关键词排名截图",
                ack_channel="web",
            ),
        ])
        await s.commit()

    async with db_mod.async_session_factory() as s:
        feedback_result = await todo_service.list_todos(
            s,
            current_user=users["alice"],
            status="feedback_done",
            search="关键词排名",
            page=1,
            page_size=10,
        )
        stats = await todo_service.get_stats(s, users["alice"])

    assert feedback_result["total"] == 1
    item = feedback_result["items"][0]
    assert item["object_id"] is None
    assert item["dispatch_done_count"] == 1
    assert item["latest_dispatch_completion"]["ack_note"] == "已反馈：补充关键词排名截图"
    assert stats["feedback_done"] >= 1
