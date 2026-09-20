from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from smoke_stack import smoke_env


def seed_inbox_fixture() -> dict[str, int | str]:
    sys.path.insert(0, str(smoke_env.PROJECT_ROOT))

    from sqlalchemy import create_engine

    from app.auth.models import User
    from app.execution.models import DecisionLog, ExecutionRun
    from app.skills.core.models import Skill
    from app.todos.models import AITodo, DecisionRequest

    engine = create_engine(smoke_env.SYNC_DB_URL)
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session(engine) as session:
        smoke_user = session.execute(
            select(User).where(User.username == smoke_env.SMOKE_ADMIN_USERNAME)
        ).scalar_one()

        session.execute(delete(AITodo).where(AITodo.request_id == "smoke-inbox-dr-1"))
        session.execute(delete(DecisionRequest).where(DecisionRequest.id == "smoke-inbox-dr-1"))
        session.execute(delete(DecisionLog).where(DecisionLog.run_id == "smoke-inbox-run-1"))
        session.execute(delete(ExecutionRun).where(ExecutionRun.id == "smoke-inbox-run-1"))
        session.execute(delete(Skill).where(Skill.id == "smoke-inbox-skill"))
        session.flush()

        skill = Skill(
            id="smoke-inbox-skill",
            name="Smoke Inbox Skill",
            description="smoke inbox seed",
            department="AI",
            status="active",
            visibility="company",
            approval_level=1,
            usage_count=0,
        )
        run = ExecutionRun(
            id="smoke-inbox-run-1",
            trigger_type="manual",
            status="completed",
            started_at=now - timedelta(minutes=5),
            completed_at=now - timedelta(minutes=4),
            total_steps=1,
            completed_steps=1,
            summary="smoke inbox run",
        )
        report_title = "Smoke Inbox Report"
        todo_title = "Smoke Inbox Todo"
        decision_log = DecisionLog(
            run_id=run.id,
            skill_id=skill.id,
            input_snapshot={"region": "华南", "date": "2026-04-17"},
            output_result={
                "reports": [
                    {
                        "channel": "dingtalk_card",
                        "title": report_title,
                        "summary": "Smoke summary for inbox",
                        "content_markdown": "# Smoke Inbox Report\n\n- metric ok",
                        "metrics": [
                            {
                                "label": "ROI",
                                "value": "1.6",
                                "trend": "up",
                                "delta": "+8%",
                            }
                        ],
                        "tags": ["smoke", "inbox"],
                        "payload": {"region": "华南", "ok": True},
                    }
                ]
            },
            approval_level=1,
            created_at=now - timedelta(minutes=3),
            is_sandbox=False,
        )
        session.add_all([skill, run, decision_log])
        session.flush()

        request = DecisionRequest(
            id="smoke-inbox-dr-1",
            source_type="skill_execution_review",
            source_id=f"{run.id}:review",
            skill_id=skill.id,
            run_id=run.id,
            decision_log_id=decision_log.id,
            kind="review",
            title=todo_title,
            summary="Need manual review for smoke inbox",
            payload={
                "structured": {
                    "output_summary": "Smoke output",
                    "decision_reasoning": "Need approval",
                    "suggested_actions": ["approve"],
                }
            },
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=now + timedelta(days=1),
            created_at=now - timedelta(minutes=2),
            callback_status="skipped",
        )
        session.add(request)
        session.flush()

        todo = AITodo(
            request_id=request.id,
            kind="review",
            assignee=smoke_user.id,
            status="pending",
            created_at=now - timedelta(minutes=2),
            updated_at=now - timedelta(minutes=2),
        )
        session.add(todo)
        session.commit()
        fixture = {
            "skill_id": skill.id,
            "run_id": run.id,
            "decision_log_id": decision_log.id,
            "card_id": f"{decision_log.id}-0",
            "todo_id": int(todo.id),
            "report_title": report_title,
            "todo_title": todo_title,
        }

    engine.dispose()
    smoke_env.ok(f"收件中心 smoke 数据已准备: card={fixture['card_id']} todo={fixture['todo_id']}")
    return fixture


def verify_inbox_api(session: requests.Session, fixture: dict[str, int | str]) -> None:
    reports_resp = session.get(
        f"{smoke_env.BASE_URL}/api/inbox/reports",
        params={"page": 1, "page_size": 10},
        timeout=10,
    )
    if reports_resp.status_code != 200:
        smoke_env.fail(f"inbox reports API 失败: {reports_resp.status_code} {reports_resp.text[:200]}")
    items = reports_resp.json().get("items") or []
    report_item = next((item for item in items if item.get("id") == fixture["card_id"]), None)
    if not report_item:
        smoke_env.fail("收件中心报告列表缺少 smoke 卡片")

    detail_resp = session.get(
        f"{smoke_env.BASE_URL}/api/inbox/reports/{fixture['card_id']}",
        params={"include_debug": 1},
        timeout=10,
    )
    if detail_resp.status_code != 200:
        smoke_env.fail(f"inbox report detail 失败: {detail_resp.status_code} {detail_resp.text[:200]}")
    detail = detail_resp.json()
    if detail.get("title") != fixture["report_title"]:
        smoke_env.fail(f"报告详情标题异常: {detail.get('title')!r}")
    if not detail.get("debug_context"):
        smoke_env.fail("报告详情缺少 debug_context")

    todo_list_resp = session.get(
        f"{smoke_env.BASE_URL}/api/todos/",
        params={"status": "pending"},
        timeout=10,
    )
    if todo_list_resp.status_code != 200:
        smoke_env.fail(f"todo list 失败: {todo_list_resp.status_code}")
    todo_items = todo_list_resp.json().get("items") or []
    todo_item = next((item for item in todo_items if item.get("id") == fixture["todo_id"]), None)
    if not todo_item:
        smoke_env.fail("待办列表缺少 smoke todo")

    todo_detail_resp = session.get(
        f"{smoke_env.BASE_URL}/api/todos/{fixture['todo_id']}",
        timeout=10,
    )
    if todo_detail_resp.status_code != 200:
        smoke_env.fail(f"todo detail 失败: {todo_detail_resp.status_code}")
    related_report = todo_detail_resp.json().get("related_report") or {}
    if related_report.get("id") != fixture["card_id"]:
        smoke_env.fail(f"待办详情 related_report 异常: {related_report}")

    smoke_env.ok("收件中心 API 返回正常")


def verify_inbox_gap_endpoints(session: requests.Session, fixture: dict[str, int | str]) -> None:
    """GAP-1/2/5/6/7/10/11/12 的 HTTP 层存活性断言。

    只校验「端点可达 + 返回 schema 字段齐全」；业务语义细节交由 pytest 兜底。
    """
    todo_id = int(fixture["todo_id"])

    # GAP-1: /api/todos/ 含新字段
    r = session.get(f"{smoke_env.BASE_URL}/api/todos/", params={"status": "pending"}, timeout=10)
    if r.status_code != 200:
        smoke_env.fail(f"GAP-1 /todos/ 列表异常: {r.status_code}")
    items = r.json().get("items") or []
    target = next((it for it in items if it.get("id") == todo_id), None)
    if target is None:
        smoke_env.fail("GAP-1 找不到 smoke todo")
    required_keys = {
        "suggested_actions",
        "metrics_preview",
        "approval_level",
        "requester",
        "requester_department",
        "aggregate_progress",
    }
    missing = required_keys - set(target.keys())
    if missing:
        smoke_env.fail(f"GAP-1 列表项缺字段: {missing}")

    # GAP-2: /api/inbox/overview
    r = session.get(f"{smoke_env.BASE_URL}/api/inbox/overview", timeout=10)
    if r.status_code != 200:
        smoke_env.fail(f"GAP-2 /inbox/overview 异常: {r.status_code} {r.text[:200]}")
    ov = r.json()
    for key in ("pending", "overdue", "sla_hit_rate", "backlog_by_age"):
        if key not in ov:
            smoke_env.fail(f"GAP-2 overview 缺字段 {key}")

    # GAP-3: /api/todos/{id}/preview
    r = session.get(f"{smoke_env.BASE_URL}/api/todos/{todo_id}/preview", timeout=10)
    if r.status_code != 200:
        smoke_env.fail(f"GAP-3 /todos/{todo_id}/preview 异常: {r.status_code}")

    # GAP-4: /api/todos/bulk-summary
    r = session.post(
        f"{smoke_env.BASE_URL}/api/todos/bulk-summary",
        json={"ids": [todo_id]},
        timeout=10,
    )
    if r.status_code != 200:
        smoke_env.fail(f"GAP-4 /todos/bulk-summary 异常: {r.status_code}")
    if "summaries" not in r.json():
        smoke_env.fail("GAP-4 响应缺 summaries")

    # GAP-5: /api/inbox/reports/unread-count
    r = session.get(f"{smoke_env.BASE_URL}/api/inbox/reports/unread-count", timeout=10)
    if r.status_code != 200:
        smoke_env.fail(f"GAP-5 /inbox/reports/unread-count 异常: {r.status_code}")
    if "unread" not in r.json():
        smoke_env.fail("GAP-5 响应缺 unread")

    # GAP-6: /api/inbox/reports/mark-read
    r = session.post(
        f"{smoke_env.BASE_URL}/api/inbox/reports/mark-read", json={}, timeout=10
    )
    if r.status_code != 200:
        smoke_env.fail(f"GAP-6 /inbox/reports/mark-read 异常: {r.status_code}")

    # GAP-10: /api/todos/trends?days=7
    r = session.get(f"{smoke_env.BASE_URL}/api/todos/trends", params={"days": 7}, timeout=10)
    if r.status_code != 200:
        smoke_env.fail(f"GAP-10 /todos/trends 异常: {r.status_code}")
    trends = r.json()
    if len(trends.get("points", [])) != 7:
        smoke_env.fail(f"GAP-10 应返回 7 个 point，实际 {len(trends.get('points', []))}")

    # GAP-11: /api/todos/{id}/related-timeline
    r = session.get(
        f"{smoke_env.BASE_URL}/api/todos/{todo_id}/related-timeline", timeout=10
    )
    if r.status_code != 200:
        smoke_env.fail(f"GAP-11 related-timeline 异常: {r.status_code}")

    # GAP-12: /api/todos/dispatch/calendar
    r = session.get(
        f"{smoke_env.BASE_URL}/api/todos/dispatch/calendar",
        params={"date_from": "2026-04-01", "date_to": "2026-04-30"},
        timeout=10,
    )
    if r.status_code != 200:
        smoke_env.fail(f"GAP-12 dispatch/calendar 异常: {r.status_code}")

    smoke_env.ok("GAP-1..12 端点 HTTP 存活性通过")


def verify_inbox_browser(fixture: dict[str, int | str]) -> None:
    env = smoke_env.smoke_env()
    env.update(
        {
            "SMOKE_INBOX_CARD_ID": str(fixture["card_id"]),
            "SMOKE_INBOX_TODO_ID": str(fixture["todo_id"]),
            "SMOKE_INBOX_REPORT_TITLE": str(fixture["report_title"]),
            "SMOKE_INBOX_TODO_TITLE": str(fixture["todo_title"]),
            "SMOKE_ADMIN_USERNAME": smoke_env.SMOKE_ADMIN_USERNAME,
            "SMOKE_ADMIN_PASSWORD": smoke_env.SMOKE_ADMIN_PASSWORD,
            "SMOKE_BROWSER_EXECUTABLE": os.environ.get(
                "SMOKE_BROWSER_EXECUTABLE", "/usr/bin/chromium-browser"
            ),
        }
    )
    script_path = Path(__file__).with_name("smoke_inbox_browser.cjs")
    result = subprocess.run(
        ["node", str(script_path)],
        cwd=smoke_env.PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        smoke_env.fail("收件中心浏览器 smoke 失败")
    smoke_env.ok("收件中心浏览器 smoke 通过")
