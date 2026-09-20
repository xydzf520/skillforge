"""天猫店铺链接下滑分析 10 轮新建 Skill 闭环回归。"""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest
from sqlalchemy import select


def _load_tmall_skill_main():
    root = Path(__file__).resolve().parents[1]
    main_path = (
        root
        / "skills-repo"
        / "tian-mao-dian-pu-lian-jie-xia-hua-fen-xi-0fdf8d"
        / "scripts"
        / "main.py"
    )
    spec = importlib.util.spec_from_file_location("tmall_decline_skill_main_10_rounds", main_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.main


FOCUS_ID = "800737120171"
EXECUTOR_ID = "tmall-ding-exec"
EXECUTOR_DING_ID = "ding-tmall-exec"


def _normal_item(item_id: str, title: str) -> dict:
    return {
        "item_id": item_id,
        "title": title,
        "visitor_count": 1000,
        "visitor_count_prev": 1000,
        "conversion_rate": 0.05,
        "conversion_rate_prev": 0.05,
        "pay_amt": 10000,
        "pay_amt_prev": 10000,
    }


def _items(focus: dict) -> list[dict]:
    others = [
        _normal_item("800737120172", "对照商品B"),
        _normal_item("800737120173", "对照商品C"),
        _normal_item("800737120174", "对照商品D"),
        _normal_item("800737120175", "对照商品E"),
        _normal_item("800737120176", "对照商品F"),
    ]
    return [focus, *others]


def _normal_activity(items: list[dict]) -> dict:
    return {
        str(item["item_id"]): {
            "activity_status": "正常",
            "price_status": "正常",
            "price_star_status": "正常",
            "activity_detail": "活动正常",
            "price_detail": "",
        }
        for item in items
    }


def _neutral_market(items: list[dict]) -> dict:
    data = {
        "category_summary": {
            "trend": "下滑",
            "avg_decline_coefficient": 10,
            "top_competitor_keywords": "",
            "total_items_analyzed": len(items),
        }
    }
    for item in items:
        data[str(item["item_id"])] = {
            "category_decline_status": "市场整体下滑",
            "competitor_rise_reason": "",
            "competitor_growth_channel": "",
        }
    return data


def _expected_decline(item: dict) -> float:
    visitor_change = (
        (item["visitor_count"] - item["visitor_count_prev"]) / item["visitor_count_prev"]
        if item["visitor_count_prev"] > 0
        else 0
    )
    conv_change = (
        (item["conversion_rate"] - item["conversion_rate_prev"]) / item["conversion_rate_prev"]
        if item["conversion_rate_prev"] > 0
        else 0
    )
    revenue_change = (
        (item["pay_amt"] - item["pay_amt_prev"]) / item["pay_amt_prev"]
        if item["pay_amt_prev"] > 0
        else 0
    )
    return round(-(visitor_change * 0.4 + conv_change * 0.3 + revenue_change * 0.3), 4)


def _expected_top5(items: list[dict]) -> list[dict]:
    ranked = [
        {
            "item_id": str(item["item_id"]),
            "decline_coefficient": _expected_decline(item),
        }
        for item in items
    ]
    ranked.sort(key=lambda row: row["decline_coefficient"], reverse=True)
    return ranked[:5]


def _case_payload(case: dict) -> dict:
    items = _items(case["focus"])
    activity = _normal_activity(items)
    market = _neutral_market(items)
    extra = case.get("extra") or {}
    if "活动价格数据" in extra:
        activity.update(extra["活动价格数据"])
    if "生意参谋_市场排行" in extra:
        market.update(extra["生意参谋_市场排行"])

    payload = {
        "date": "2026-04-22",
        "default_executor": EXECUTOR_ID,
        "reviewers": ["admin"],
        "生意参谋_商品排行榜": items,
        "生意参谋_市场排行": market,
        "活动价格数据": activity,
    }
    for key, value in extra.items():
        if key not in {"活动价格数据", "生意参谋_市场排行"}:
            payload[key] = value
    return payload


def _cases() -> list[dict]:
    base = _normal_item(FOCUS_ID, "天猫测试商品A")
    return [
        {
            "suffix": "回归01",
            "focus": {**base, "visitor_count": 500, "pay_amt": 9000},
            "expected_dimensions": {"免费流-访客": "高"},
            "expected_free": {"visitor_change_pct": -50.0, "conversion_change_pct": 0.0},
            "expected_todo_count": 1,
        },
        {
            "suffix": "回归02",
            "focus": {**base, "conversion_rate": 0.025, "pay_amt": 9000},
            "expected_dimensions": {"免费流-转化率": "高"},
            "expected_free": {"visitor_change_pct": 0.0, "conversion_change_pct": -50.0},
            "expected_todo_count": 1,
        },
        {
            "suffix": "回归03",
            "focus": dict(base),
            "extra": {
                "评价数据": {
                    FOCUS_ID: {
                        "has_new_negative_pinned": True,
                        "negative_review_summary": "质量问题-材质不符",
                        "pinned_review_content": "实际材质与描述不符",
                    }
                }
            },
            "expected_dimensions": {"评价": "高"},
            "expected_review_pinned": True,
            "expected_todo_count": 2,
        },
        {
            "suffix": "回归04",
            "focus": {**base, "pay_amt": 6000},
            "extra": {
                "生意参谋_市场排行": {
                    "category_summary": {
                        "trend": "上涨",
                        "avg_decline_coefficient": -0.1,
                        "top_competitor_keywords": "竞品关键词: 低价活动",
                        "total_items_analyzed": 6,
                    },
                    FOCUS_ID: {
                        "category_decline_status": "市场整体上涨",
                        "competitor_rise_reason": "报入活动+低价策略",
                        "competitor_growth_channel": "活动+付费",
                    },
                }
            },
            "expected_dimensions": {"市场": "高"},
            "expected_market_contains": "市场上涨但自身下滑",
            "expected_todo_count": 1,
        },
        {
            "suffix": "回归05",
            "focus": dict(base),
            "extra": {
                "活动价格数据": {
                    FOCUS_ID: {
                        "activity_status": "已掉线",
                        "price_status": "正常",
                        "price_star_status": "正常",
                        "activity_detail": "活动到期未续报",
                        "price_detail": "",
                    }
                }
            },
            "expected_dimensions": {"活动": "高"},
            "expected_activity": {"activity_status": "已掉线"},
            "expected_todo_count": 1,
        },
        {
            "suffix": "回归06",
            "focus": dict(base),
            "extra": {
                "活动价格数据": {
                    FOCUS_ID: {
                        "activity_status": "正常",
                        "price_status": "价格偏高",
                        "price_star_status": "正常",
                        "activity_detail": "活动正常",
                        "price_detail": "当前成交价高于同类商品15%",
                    }
                }
            },
            "expected_dimensions": {"价格": "高"},
            "expected_activity": {"price_status": "价格偏高"},
            "expected_todo_count": 1,
        },
        {
            "suffix": "回归07",
            "focus": dict(base),
            "extra": {
                "活动价格数据": {
                    FOCUS_ID: {
                        "activity_status": "正常",
                        "price_status": "正常",
                        "price_star_status": "高价限流",
                        "activity_detail": "活动正常",
                        "price_detail": "价格力星级低于竞品",
                    }
                }
            },
            "expected_dimensions": {"价格力星级": "中"},
            "expected_activity": {"price_star_status": "高价限流"},
            "expected_todo_count": 1,
        },
        {
            "suffix": "回归08",
            "focus": dict(base),
            "extra": {
                "千牛后台_关键词推广": {
                    FOCUS_ID: {
                        "paid_visitor_count": 300,
                        "paid_visitor_prev": 300,
                        "paid_conversion_rate": 0.04,
                        "paid_conversion_prev": 0.04,
                        "roi": 1.5,
                        "roi_prev": 3.0,
                        "ppc": 1.1,
                        "ppc_prev": 1.1,
                        "plan_status": "正常",
                    }
                }
            },
            "expected_dimensions": {"付费-ROI": "高"},
            "expected_paid": {"roi_status": "下滑", "ppc_status": "正常"},
            "expected_todo_count": 1,
        },
        {
            "suffix": "回归09",
            "focus": dict(base),
            "extra": {
                "千牛后台_关键词推广": {
                    FOCUS_ID: {
                        "paid_visitor_count": 300,
                        "paid_visitor_prev": 300,
                        "paid_conversion_rate": 0.04,
                        "paid_conversion_prev": 0.04,
                        "roi": 3.2,
                        "roi_prev": 3.0,
                        "ppc": 2.0,
                        "ppc_prev": 1.4,
                        "plan_status": "正常",
                    }
                }
            },
            "expected_dimensions": {"付费-PPC": "中"},
            "expected_paid": {"roi_status": "正常", "ppc_status": "升高"},
            "expected_todo_count": 1,
        },
        {
            "suffix": "回归10",
            "focus": {**base, "visitor_count": 500, "pay_amt": 9000},
            "extra": {
                "上次整改状态": [
                    {
                        "item_id": FOCUS_ID,
                        "dimension": "免费流-访客",
                        "previous_issue": "免费访客环比下降50%",
                    },
                    {
                        "item_id": FOCUS_ID,
                        "dimension": "价格",
                        "previous_issue": "价格偏高",
                    },
                ]
            },
            "expected_dimensions": {"免费流-访客": "高"},
            "expected_closed_loop": {
                "免费流-访客": False,
                "价格": True,
            },
            "expected_todo_count": 1,
        },
    ]


def _assert_output_matches_expected(output: dict, payload: dict, case: dict) -> None:
    expected_rank = _expected_top5(payload["生意参谋_商品排行榜"])
    actual_rank = output["下滑系数排名"]
    assert [row["item_id"] for row in actual_rank] == [row["item_id"] for row in expected_rank]
    assert [row["decline_coefficient"] for row in actual_rank] == [
        row["decline_coefficient"] for row in expected_rank
    ]

    focus_free = next(row for row in output["免费流分析结果"] if row["item_id"] == FOCUS_ID)
    if "expected_free" in case:
        assert focus_free["visitor_change_pct"] == case["expected_free"]["visitor_change_pct"]
        assert focus_free["conversion_change_pct"] == case["expected_free"]["conversion_change_pct"]

    focus_suggestions = [
        row for row in output["整改建议清单"] if row["item_id"] == FOCUS_ID
    ]
    assert {
        row["dimension"]: row["priority"] for row in focus_suggestions
    } == case["expected_dimensions"]

    if case.get("expected_review_pinned") is not None:
        review = next(row for row in output["评价检查结果"] if row["item_id"] == FOCUS_ID)
        assert review["has_new_negative_pinned"] is case["expected_review_pinned"]

    if case.get("expected_market_contains"):
        market = next(row for row in output["市场对比结果"] if row["item_id"] == FOCUS_ID)
        assert case["expected_market_contains"] in market["our_vs_market_coefficient"]

    if case.get("expected_activity"):
        activity = next(row for row in output["活动价格检查结果"] if row["item_id"] == FOCUS_ID)
        for key, value in case["expected_activity"].items():
            assert activity[key] == value

    if case.get("expected_paid"):
        paid = next(row for row in output["付费端诊断报告"] if row["item_id"] == FOCUS_ID)
        for key, value in case["expected_paid"].items():
            assert paid[key] == value

    if case.get("expected_closed_loop"):
        closed_loop = {
            row["dimension"]: row["resolved"]
            for row in output["整改闭环状态"]
            if row["item_id"] == FOCUS_ID
        }
        assert closed_loop == case["expected_closed_loop"]

    assert len(output["todos"]) == case["expected_todo_count"]
    assert output["reports"][0]["title"] == "2026-04-22 店铺链接下滑诊断日报"
    for todo in output["todos"]:
        assert todo["kind"] == "dispatch"
        assert todo["reviewers"] == ["admin"]
        assert todo["tasks"], "dispatch 待办必须包含 tasks"
        for task in todo["tasks"]:
            assert task["executor"] == EXECUTOR_ID
            assert task["content"]


@pytest.mark.asyncio
async def test_tmall_skill_10_round_new_skill_inbox_dispatch_outbox(client):
    import app.database as db_mod
    from app.auth.models import User
    from app.auth.service import hash_password
    from app.dingtalk.models import DingTalkOutbox
    from app.skills.models import Skill
    from app.todos.models import TodoDispatchTask
    from app.todos.service import todo_service

    skill_main = _load_tmall_skill_main()

    async with db_mod.async_session_factory() as session:
        session.add_all([
            User(
                id="admin",
                username="admin",
                name="管理员",
                role="admin",
                can_view_all=True,
                department="EC",
                password_hash=hash_password("admin123"),
                is_active=True,
                state="active",
                must_change_password=False,
                dingtalk_user_id="ding-admin",
            ),
            User(
                id=EXECUTOR_ID,
                username=EXECUTOR_ID,
                name="天猫测试执行人",
                role="operator",
                department="EC",
                password_hash=hash_password("admin123"),
                is_active=True,
                state="active",
                must_change_password=False,
                dingtalk_user_id=EXECUTOR_DING_ID,
            ),
        ])
        await session.commit()

    login = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert login.status_code == 200
    assert login.json()["username"] == "admin"

    rng = random.Random(20260422)
    for index, case in enumerate(_cases(), start=1):
        skill_id = f"tmall-decline-regression-{index:02d}"
        skill_name = f"天猫店铺链接下滑分析-{case['suffix']}"
        payload = _case_payload(case)
        output = skill_main(payload)
        _assert_output_matches_expected(output, payload, case)

        async with db_mod.async_session_factory() as session:
            session.add(
                Skill(
                    id=skill_id,
                    name=skill_name,
                    display_name=skill_name,
                    description="天猫店铺链接下滑分析 10 轮回归新建 Skill",
                    department="EC",
                    owner="tmall-owner",
                    visibility="company",
                    status="active",
                    approval_level=1,
                    target_users=[],
                    current_version="v1",
                )
            )
            await session.commit()

        run_id = f"run-tmall-regression-{index:02d}"
        submitted = await client.post(
            "/api/executions/submit-result",
            json={
                "skill_id": skill_id,
                "run_id": run_id,
                "params": payload,
                "triggered_by": "regression",
                "output": output,
            },
        )
        assert submitted.status_code == 200
        submitted_json = submitted.json()
        assert submitted_json["todo_error"] is None
        assert submitted_json["todo_created"] is True
        assert submitted_json["todo_count"] == case["expected_todo_count"]

        async with db_mod.async_session_factory() as session:
            admin = await session.get(User, "admin")
            inbox = await todo_service.list_todos(
                session,
                current_user=admin,
                skill_id=skill_id,
                status="pending",
                kind="dispatch",
                page_size=50,
            )
            assert inbox["total"] == case["expected_todo_count"]
            chosen = rng.choice(inbox["items"])

            decision_result = await todo_service.decide(
                session,
                todo_id=chosen["id"],
                decision="approved",
                decided_by="admin",
                channel="web",
                reason=f"10轮回归随机同意 {case['suffix']}",
            )
            await session.commit()
            assert decision_result["dispatch_fanout"]["sent"] >= 1
            assert decision_result["dispatch_fanout"]["degraded"] == 0

            tasks = (
                await session.execute(
                    select(TodoDispatchTask).where(
                        TodoDispatchTask.request_id == chosen["request_id"]
                    )
                )
            ).scalars().all()
            assert tasks
            assert all(task.status in {"sent", "pushed_no_dingtalk"} for task in tasks)
            assert any(task.status == "sent" for task in tasks)

            sent_task_ids = [str(task.id) for task in tasks if task.status == "sent"]
            outbox_rows = (
                await session.execute(
                    select(DingTalkOutbox).where(
                        DingTalkOutbox.related_type == "dispatch_task",
                        DingTalkOutbox.related_id.in_(sent_task_ids),
                    )
                )
            ).scalars().all()
            assert len(outbox_rows) == len(sent_task_ids)
            assert all(row.recipient_user_id == EXECUTOR_DING_ID for row in outbox_rows)
            assert all(row.status == "pending" for row in outbox_rows)
