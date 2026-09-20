from __future__ import annotations

import fnmatch
import json
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.common.exceptions import AppError, app_error_handler
import app.database as db_mod
from app.execution.models import DecisionLog, ExecutionRun
from app.inbox.router import router as inbox_router
from app.inbox.service import inbox_service
from app.org.models import OrgUnit, UserOrgMembership
from app.skills.members import SkillMember
from app.skills.core.models import Skill
from app.todos.models import AITodo, DecisionRequest


def _make_user(
    user_id: str,
    *,
    role: str = "observer",
    can_view_all: bool = False,
    department: str | None = None,
) -> User:
    return User(
        id=user_id,
        username=user_id,
        name=user_id,
        role=role,
        state="active",
        permissions_rev=0,
        can_view_all=can_view_all,
        department=department,
        is_active=True,
        must_change_password=False,
    )


@pytest_asyncio.fixture
async def inbox_client(client):
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    state = {"current_user": _make_user("admin", role="admin", can_view_all=True)}
    app.dependency_overrides[get_current_user] = lambda: state["current_user"]
    app.include_router(inbox_router, prefix="/api/inbox")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, state


@pytest.fixture
def fake_cache(monkeypatch):
    store: dict[str, object] = {}

    async def fake_get(key: str):
        raw = store.get(key)
        if raw is None:
            return None
        return json.loads(json.dumps(raw))

    async def fake_set(key: str, value, ttl=None):
        store[key] = json.loads(json.dumps(value, default=str))

    async def fake_delete(key: str):
        store.pop(key, None)

    async def fake_delete_pattern(pattern: str):
        keys = [key for key in list(store) if fnmatch.fnmatch(key, pattern)]
        for key in keys:
            store.pop(key, None)
        return len(keys)

    monkeypatch.setattr("app.common.cache.cache_get", fake_get)
    monkeypatch.setattr("app.common.cache.cache_set", fake_set)
    monkeypatch.setattr("app.common.cache.cache_delete", fake_delete)
    monkeypatch.setattr("app.common.cache.cache_delete_pattern", fake_delete_pattern)
    return store


async def _add_users_and_orgs() -> dict[str, User]:
    async with db_mod.async_session_factory() as session:
        org_root = OrgUnit(id="root", name="Root", type="company", path="/root")
        org_a = OrgUnit(id="dept-a", name="Dept A", type="department", parent_id="root", path="/root/dept-a")
        org_a_child = OrgUnit(
            id="dept-a-team",
            name="Dept A Team",
            type="team",
            parent_id="dept-a",
            path="/root/dept-a/dept-a-team",
        )
        org_b = OrgUnit(id="dept-b", name="Dept B", type="department", parent_id="root", path="/root/dept-b")
        session.add_all([org_root, org_a, org_a_child, org_b])

        alice = _make_user("alice", department="Dept A")
        bob = _make_user("bob", department="Dept B")
        manager = _make_user("manager", role="dept_admin", department="Dept A")
        viewer = _make_user("viewer", can_view_all=True, department="Dept B")
        admin = _make_user("admin", role="admin", can_view_all=True, department="Dept A")
        session.add_all([alice, bob, manager, viewer, admin])
        session.add_all(
            [
                UserOrgMembership(user_id="alice", org_unit_id="dept-a-team"),
                UserOrgMembership(user_id="bob", org_unit_id="dept-b"),
                UserOrgMembership(user_id="manager", org_unit_id="dept-a", is_manager=True),
                UserOrgMembership(user_id="viewer", org_unit_id="dept-b"),
                UserOrgMembership(user_id="admin", org_unit_id="dept-a"),
            ]
        )
        await session.commit()
        return {
            "alice": alice,
            "bob": bob,
            "manager": manager,
            "viewer": viewer,
            "admin": admin,
        }


async def _add_skill(
    *,
    skill_id: str,
    name: str,
    visibility: str,
    department: str,
    org_unit_id: str,
) -> None:
    async with db_mod.async_session_factory() as session:
        session.add(
            Skill(
                id=skill_id,
                name=name,
                description=name,
                department=department,
                org_unit_id=org_unit_id,
                visibility=visibility,
                status="active",
            )
        )
        await session.commit()


async def _add_execution_run(run_id: str, *, trigger_type: str = "cron") -> None:
    async with db_mod.async_session_factory() as session:
        session.add(
            ExecutionRun(
                id=run_id,
                trigger_type=trigger_type,
                status="completed",
                started_at=datetime(2026, 4, 17, 8, 0, 0),
            )
        )
        await session.commit()


async def _add_decision_log(
    *,
    skill_id: str,
    run_id: str,
    created_at: datetime,
    reports: list[dict] | None = None,
    is_sandbox: bool = False,
    output_extra: dict | None = None,
) -> int:
    output_result = dict(output_extra or {})
    if reports is not None:
        output_result["reports"] = reports
    async with db_mod.async_session_factory() as session:
        log = DecisionLog(
            skill_id=skill_id,
            run_id=run_id,
            input_snapshot={"date": "2026-04-17"},
            output_result=output_result,
            is_sandbox=is_sandbox,
            created_at=created_at,
        )
        session.add(log)
        await session.commit()
        return int(log.id)


async def _add_related_requests(decision_log_id: int) -> None:
    async with db_mod.async_session_factory() as session:
        req_pending = DecisionRequest(
            id="dr-pending",
            source_type="skill_execution_review",
            source_id="run-1:pending",
            skill_id="skill-reporting",
            run_id="run-1",
            decision_log_id=decision_log_id,
            kind="review",
            title="Check budget",
            summary="Budget needs approval",
            aggregate_status="pending",
            sla_at=datetime(2026, 4, 18, 8, 0, 0),
        )
        req_done = DecisionRequest(
            id="dr-approved",
            source_type="skill_execution_dispatch",
            source_id="run-1:approved",
            skill_id="skill-reporting",
            run_id="run-1",
            decision_log_id=decision_log_id,
            kind="dispatch",
            title="Adjust campaign",
            summary="Dispatch work item",
            aggregate_status="completed",
            aggregate_decision="approved",
            sla_at=datetime(2026, 4, 19, 8, 0, 0),
        )
        session.add_all([req_pending, req_done])
        await session.flush()
        session.add_all(
            [
                AITodo(request_id="dr-pending", kind="review", assignee="alice"),
                AITodo(request_id="dr-pending", kind="review", assignee="manager"),
                AITodo(request_id="dr-approved", kind="dispatch", assignee="bob"),
            ]
        )
        await session.commit()


@pytest.mark.asyncio
async def test_list_reports_flattens_cards_and_applies_card_filters(inbox_client, fake_cache):
    client, state = inbox_client
    users = await _add_users_and_orgs()
    state["current_user"] = users["admin"]

    await _add_skill(
        skill_id="skill-reporting",
        name="Reporting",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_execution_run("run-1", trigger_type="cron")
    await _add_execution_run("run-2", trigger_type="manual")
    await _add_execution_run("run-3", trigger_type="event")
    first_log_id = await _add_decision_log(
        skill_id="skill-reporting",
        run_id="run-1",
        created_at=datetime(2026, 4, 17, 9, 0, 0),
        reports=[
            {
                "channel": "dingtalk_card",
                "title": "North weekly summary",
                "summary": "North is healthy",
                "tags": ["north", "weekly"],
                "metrics": [{"label": "ROI", "value": "1.8"}],
            },
            {
                "channel": "email",
                "title": "North alert",
                "summary": "Spend is rising",
                "tags": ["alert"],
            },
            {
                "channel": "email",
                "summary": "missing title should be skipped",
            },
        ],
    )
    await _add_related_requests(first_log_id)
    second_log_id = await _add_decision_log(
        skill_id="skill-reporting",
        run_id="run-2",
        created_at=datetime(2026, 4, 16, 12, 0, 0),
        reports=[
            {
                "channel": "dingtalk_card",
                "title": "South manual summary",
                "summary": "Manual trigger output",
                "tags": ["south", "manual"],
            }
        ],
    )
    await _add_decision_log(
        skill_id="skill-reporting",
        run_id="run-3",
        created_at=datetime(2026, 4, 15, 12, 0, 0),
        reports=[{"channel": "email", "title": "Sandbox", "summary": "ignore"}],
        is_sandbox=True,
    )
    await _add_decision_log(
        skill_id="skill-reporting",
        run_id="run-3",
        created_at=datetime(2026, 4, 14, 12, 0, 0),
        reports=[],
    )
    await _add_decision_log(
        skill_id="skill-reporting",
        run_id="run-3",
        created_at=datetime(2026, 4, 13, 12, 0, 0),
        output_extra={"summary": "missing reports array"},
    )

    resp = await client.get("/api/inbox/reports?page=1&page_size=1")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 3
    assert payload["items"][0]["id"] == f"{first_log_id}-0"
    assert payload["items"][0]["related_todo_count"] == 2
    assert payload["items"][0]["related_pending_request_count"] == 1
    assert payload["items"][0]["metrics"] == [{"label": "ROI", "value": "1.8", "trend": None, "delta": None}]

    resp = await client.get("/api/inbox/reports?page=2&page_size=1")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["id"] == f"{first_log_id}-1"

    resp = await client.get("/api/inbox/reports?tag=alert")
    assert resp.status_code == 200
    tag_payload = resp.json()
    assert tag_payload["total"] == 1
    assert tag_payload["items"][0]["id"] == f"{first_log_id}-1"

    resp = await client.get("/api/inbox/reports?channel=dingtalk_card&trigger_type=manual")
    assert resp.status_code == 200
    trigger_payload = resp.json()
    assert trigger_payload["total"] == 1
    assert trigger_payload["items"][0]["id"] == f"{second_log_id}-0"

    resp = await client.get("/api/inbox/reports?date_from=2026-04-17")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_list_reports_reads_synced_projection_without_decision_json(inbox_client, fake_cache):
    client, state = inbox_client
    users = await _add_users_and_orgs()
    state["current_user"] = users["admin"]

    await _add_skill(
        skill_id="skill-projected",
        name="Projected",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_execution_run("run-projected", trigger_type="cron")
    log_id = await _add_decision_log(
        skill_id="skill-projected",
        run_id="run-projected",
        created_at=datetime(2026, 4, 17, 9, 0, 0),
        reports=[
            {
                "channel": "dingtalk_card",
                "title": "Projected report",
                "summary": "Projection keeps list light",
                "tags": ["projected"],
            }
        ],
    )

    from app.inbox.service import sync_report_cards_for_decision_log

    async with db_mod.async_session_factory() as session:
        decision = await session.get(DecisionLog, log_id)
        await sync_report_cards_for_decision_log(session, decision)
        decision.output_result = {"reports": []}
        await session.commit()

    resp = await client.get("/api/inbox/reports?date_from=2026-04-17&date_to=2026-04-17")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == f"{log_id}-0"
    assert payload["items"][0]["title"] == "Projected report"
    assert payload["items"][0]["tags"] == ["projected"]


@pytest.mark.asyncio
async def test_list_reports_repairs_missing_projection_for_new_decision_log(inbox_client, fake_cache):
    client, state = inbox_client
    users = await _add_users_and_orgs()
    state["current_user"] = users["admin"]
    fake_cache.clear()
    import app.inbox.service as inbox_service_mod

    inbox_service_mod._REPORT_CARD_TABLE_EXISTS = None

    await _add_skill(
        skill_id="skill-repair-projection",
        name="Repair Projection",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_execution_run("run-repair-projection", trigger_type="manual")
    log_id = await _add_decision_log(
        skill_id="skill-repair-projection",
        run_id="run-repair-projection",
        created_at=datetime(2026, 4, 17, 10, 0, 0),
        reports=[
            {
                "channel": "email",
                "title": "Fresh report without projection",
                "summary": "List repairs a missing projection row",
            }
        ],
    )

    resp = await client.get("/api/inbox/reports?date_from=2026-04-17&date_to=2026-04-17")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == f"{log_id}-0"

    from app.inbox.models import InboxReportCard

    async with db_mod.async_session_factory() as session:
        projected = (
            await session.execute(
                select(InboxReportCard).where(InboxReportCard.decision_log_id == log_id)
            )
        ).scalar_one()
        assert projected.title == "Fresh report without projection"


@pytest.mark.asyncio
async def test_report_detail_returns_related_todos_and_debug_context_by_acl(inbox_client, fake_cache):
    client, state = inbox_client
    users = await _add_users_and_orgs()
    await _add_skill(
        skill_id="skill-reporting",
        name="Reporting",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_execution_run("run-1", trigger_type="cron")
    log_id = await _add_decision_log(
        skill_id="skill-reporting",
        run_id="run-1",
        created_at=datetime(2026, 4, 17, 10, 0, 0),
        reports=[
            {
                "channel": "dingtalk_card",
                "title": "Executive summary",
                "summary": "Business summary",
                "content_markdown": "## Analysis",
                "payload": {"score": 98},
                "tags": ["exec"],
                "metrics": [{"label": "Score", "value": "98"}],
            }
        ],
    )
    await _add_related_requests(log_id)

    state["current_user"] = users["alice"]
    resp = await client.get(f"/api/inbox/reports/{log_id}-0?include_debug=true")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["content_markdown"] == "## Analysis"
    assert payload["payload"] == {"score": 98}
    assert payload["debug_context"] is None
    assert payload["related_todo_count"] == 2
    assert payload["related_pending_request_count"] == 1
    assert len(payload["related_todos"]) == 2
    first_related = next(item for item in payload["related_todos"] if item["request_id"] == "dr-pending")
    assert first_related["assignees"] == ["alice", "manager"]

    state["current_user"] = users["admin"]
    resp = await client.get(f"/api/inbox/reports/{log_id}-0?include_debug=true")
    assert resp.status_code == 200
    debug_payload = resp.json()
    assert debug_payload["debug_context"] == {
        "raw_output_result": {
            "reports": [
                {
                    "channel": "dingtalk_card",
                    "title": "Executive summary",
                    "summary": "Business summary",
                    "content_markdown": "## Analysis",
                    "payload": {"score": 98},
                    "tags": ["exec"],
                    "metrics": [{"label": "Score", "value": "98"}],
                }
            ]
        },
        "input_snapshot": {"date": "2026-04-17"},
    }


@pytest.mark.asyncio
async def test_report_acl_filters_follow_skill_access_semantics(inbox_client, fake_cache):
    client, state = inbox_client
    users = await _add_users_and_orgs()

    await _add_skill(
        skill_id="skill-company",
        name="Company",
        visibility="company",
        department="Dept B",
        org_unit_id="dept-b",
    )
    await _add_skill(
        skill_id="skill-dept",
        name="Department",
        visibility="department",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_skill(
        skill_id="skill-private-member",
        name="PrivateMember",
        visibility="private",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_skill(
        skill_id="skill-private-hidden",
        name="PrivateHidden",
        visibility="private",
        department="Dept B",
        org_unit_id="dept-b",
    )
    async with db_mod.async_session_factory() as session:
        session.add(SkillMember(skill_id="skill-private-member", user_id="alice", role="reviewer"))
        await session.commit()

    card_ids: dict[str, str] = {}
    for idx, skill_id in enumerate(
        ["skill-company", "skill-dept", "skill-private-member", "skill-private-hidden"],
        start=1,
    ):
        await _add_execution_run(f"run-acl-{idx}", trigger_type="cron")
        log_id = await _add_decision_log(
            skill_id=skill_id,
            run_id=f"run-acl-{idx}",
            created_at=datetime(2026, 4, 17, 9, 0, 0) - timedelta(minutes=idx),
            reports=[{"channel": "dingtalk_card", "title": skill_id, "summary": skill_id}],
        )
        card_ids[skill_id] = f"{log_id}-0"

    state["current_user"] = users["alice"]
    resp = await client.get("/api/inbox/reports")
    assert resp.status_code == 200
    assert {item["skill_id"] for item in resp.json()["items"]} == {
        "skill-company",
        "skill-dept",
        "skill-private-member",
    }

    state["current_user"] = users["bob"]
    resp = await client.get("/api/inbox/reports")
    assert resp.status_code == 200
    assert {item["skill_id"] for item in resp.json()["items"]} == {"skill-company"}

    state["current_user"] = users["manager"]
    resp = await client.get("/api/inbox/reports")
    assert resp.status_code == 200
    assert {item["skill_id"] for item in resp.json()["items"]} == {
        "skill-company",
        "skill-dept",
        "skill-private-member",
    }

    state["current_user"] = users["viewer"]
    resp = await client.get("/api/inbox/reports")
    assert resp.status_code == 200
    assert {item["skill_id"] for item in resp.json()["items"]} == {
        "skill-company",
        "skill-dept",
        "skill-private-member",
        "skill-private-hidden",
    }

    state["current_user"] = users["bob"]
    resp = await client.get(f"/api/inbox/reports/{card_ids['skill-private-hidden']}")
    assert resp.status_code == 403

    state["current_user"] = users["alice"]
    resp = await client.get(f"/api/inbox/reports/{card_ids['skill-private-member']}")
    assert resp.status_code == 200
    assert resp.json()["skill_id"] == "skill-private-member"


@pytest.mark.asyncio
async def test_report_list_includes_department_skill_member_with_legacy_department_fallback(
    inbox_client,
    fake_cache,
):
    client, state = inbox_client
    users = await _add_users_and_orgs()

    await _add_skill(
        skill_id="skill-legacy-dept-member",
        name="Legacy Dept Member",
        visibility="department",
        department="Dept A",
        org_unit_id="",
    )
    async with db_mod.async_session_factory() as session:
        session.add(SkillMember(skill_id="skill-legacy-dept-member", user_id="alice", role="editor"))
        await session.commit()

    await _add_execution_run("run-legacy-dept-member", trigger_type="cron")
    log_id = await _add_decision_log(
        skill_id="skill-legacy-dept-member",
        run_id="run-legacy-dept-member",
        created_at=datetime(2026, 4, 17, 9, 0, 0),
        reports=[
            {
                "channel": "dingtalk_card",
                "title": "Legacy department member report",
                "summary": "Visible through member and department fallback",
            }
        ],
    )

    state["current_user"] = users["alice"]
    resp = await client.get("/api/inbox/reports?skill_id=skill-legacy-dept-member")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == f"{log_id}-0"

    state["current_user"] = users["bob"]
    resp = await client.get("/api/inbox/reports?skill_id=skill-legacy-dept-member")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_dept_admin_sees_own_dept_reports(inbox_client, fake_cache):
    """spec §7 权限 case: dept_admin 能看本部门（含 is_manager 子树）所有 skill 的报告（含 private）,
    跨部门的 private skill 看不到,详情返 403。

    结构:
      root → dept-a → dept-a-team  (manager 管辖 dept-a, 按子树展开应覆盖 dept-a-team)
      root → dept-b                 (manager 无关联)

    三个 private skill:
      - skill_A_private   (org_unit_id=dept-a)       — manager 直接管辖,必须可见
      - skill_A1_private  (org_unit_id=dept-a-team)  — manager 管辖子树,依赖 access.py 子树展开
      - skill_B_private   (org_unit_id=dept-b)       — manager 管不着,必须不可见,详情 403
    """
    client, state = inbox_client
    users = await _add_users_and_orgs()

    await _add_skill(
        skill_id="skill_A_private",
        name="A Private",
        visibility="private",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_skill(
        skill_id="skill_A1_private",
        name="A1 Private",
        visibility="private",
        department="Dept A",
        org_unit_id="dept-a-team",
    )
    await _add_skill(
        skill_id="skill_B_private",
        name="B Private",
        visibility="private",
        department="Dept B",
        org_unit_id="dept-b",
    )

    card_ids: dict[str, str] = {}
    for idx, skill_id in enumerate(
        ["skill_A_private", "skill_A1_private", "skill_B_private"], start=1
    ):
        await _add_execution_run(f"run-deptadm-{idx}", trigger_type="cron")
        log_id = await _add_decision_log(
            skill_id=skill_id,
            run_id=f"run-deptadm-{idx}",
            created_at=datetime(2026, 4, 17, 9, 0, 0) - timedelta(minutes=idx),
            reports=[{"channel": "dingtalk_card", "title": skill_id, "summary": skill_id}],
        )
        card_ids[skill_id] = f"{log_id}-0"

    state["current_user"] = users["manager"]
    resp = await client.get("/api/inbox/reports")
    assert resp.status_code == 200
    visible = {item["skill_id"] for item in resp.json()["items"]}
    # dept_admin 能看本部门 + 管辖子树的 private skill,但看不到跨部门 private
    assert "skill_A_private" in visible
    assert "skill_A1_private" in visible
    assert "skill_B_private" not in visible

    # Dept B 的 private skill 详情访问必须 403
    resp = await client.get(f"/api/inbox/reports/{card_ids['skill_B_private']}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_router_cache_is_scoped_by_user_id(inbox_client, fake_cache, monkeypatch):
    client, state = inbox_client
    users = await _add_users_and_orgs()
    state["current_user"] = users["alice"]

    await _add_skill(
        skill_id="skill-company",
        name="Company",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_execution_run("run-cache", trigger_type="cron")
    await _add_decision_log(
        skill_id="skill-company",
        run_id="run-cache",
        created_at=datetime(2026, 4, 17, 11, 0, 0),
        reports=[{"channel": "dingtalk_card", "title": "Cached", "summary": "Cached"}],
    )

    original = inbox_service.list_reports
    calls: list[str] = []

    async def counted(*args, **kwargs):
        calls.append(kwargs["current_user"].id)
        return await original(*args, **kwargs)

    monkeypatch.setattr(inbox_service, "list_reports", counted)

    resp = await client.get("/api/inbox/reports")
    assert resp.status_code == 200
    resp = await client.get("/api/inbox/reports")
    assert resp.status_code == 200

    state["current_user"] = users["bob"]
    resp = await client.get("/api/inbox/reports")
    assert resp.status_code == 200

    assert calls == ["alice", "bob"]
    assert any("current_user.id=alice" in key for key in fake_cache)
    assert any("current_user.id=bob" in key for key in fake_cache)
