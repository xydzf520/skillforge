from __future__ import annotations

from datetime import timedelta

import pytest

import app.database as db_mod
from app.auth.models import User
from app.codex.models import CodexCliSession, CodexDebugRun, CodexMcpCallAudit
from app.common.audit import AuditLog
from app.common.time_utils import now_bjt
from app.execution.models import DecisionLog, ExecutionRun
from app.sf.service import build_sf_overview
from app.skills.core.models import Skill


@pytest.mark.asyncio
async def test_sf_overview_scopes_and_reports(client):
    now = now_bjt()
    async with db_mod.async_session_factory() as session:
        admin = User(
            id="admin",
            username="admin",
            name="Admin",
            role="admin",
            state="active",
            can_view_all=True,
            is_active=True,
            must_change_password=False,
        )
        engineer = User(
            id="eng1",
            username="eng1",
            name="Engineer One",
            role="ai_engineer",
            state="active",
            can_view_all=False,
            is_active=True,
            must_change_password=False,
        )
        other = User(
            id="other1",
            username="other1",
            name="Other User",
            role="aibp",
            state="active",
            can_view_all=False,
            is_active=True,
            must_change_password=False,
        )
        session.add_all([admin, engineer, other])
        session.add_all(
            [
                Skill(id="sf-skill", name="SF Skill", department="AI", status="active"),
                Skill(id="other-skill", name="Other Skill", department="AI", status="active"),
            ]
        )
        session.add_all(
            [
                CodexCliSession(
                    id="sess-eng",
                    user_id="eng1",
                    token_hash="hash-eng",
                    scopes_json={},
                    permissions_rev_snapshot=0,
                    expires_at=now + timedelta(days=3),
                    created_at=now - timedelta(days=1),
                ),
                CodexCliSession(
                    id="sess-other",
                    user_id="other1",
                    token_hash="hash-other",
                    scopes_json={},
                    permissions_rev_snapshot=0,
                    expires_at=now + timedelta(days=3),
                    created_at=now - timedelta(days=1),
                ),
            ]
        )
        session.add_all(
            [
                AuditLog(
                    user_id="eng1",
                    action="codex.capabilities",
                    target_type="skill",
                    target_id="sf-skill",
                    detail={"skill_id": "sf-skill", "run_mode": "local_debug"},
                    created_at=now - timedelta(hours=3),
                ),
                AuditLog(
                    user_id="other1",
                    action="codex.skill.submit",
                    target_type="skill",
                    target_id="other-skill",
                    detail={"skill_id": "other-skill"},
                    created_at=now - timedelta(hours=2),
                ),
            ]
        )
        session.add_all(
            [
                CodexMcpCallAudit(
                    request_id="req-1",
                    proof_id="proof-1",
                    user_id="eng1",
                    skill_id="sf-skill",
                    server="skillforge",
                    tool="skillforge_org_search_users",
                    run_mode="local_debug",
                    dry_run=True,
                    ok=True,
                    created_at=now - timedelta(hours=1),
                ),
                CodexMcpCallAudit(
                    request_id="req-2",
                    proof_id="proof-2",
                    user_id="eng1",
                    skill_id="sf-skill",
                    server="skillforge",
                    tool="skillforge_org_search_users",
                    run_mode="local_debug",
                    dry_run=True,
                    ok=False,
                    error_code="MOCK_FAIL",
                    created_at=now - timedelta(minutes=50),
                ),
                CodexMcpCallAudit(
                    request_id="req-3",
                    proof_id="proof-3",
                    user_id="other1",
                    skill_id="other-skill",
                    server="skillforge",
                    tool="tmall_store_weekly_snapshot",
                    run_mode="local_debug",
                    dry_run=False,
                    ok=True,
                    created_at=now - timedelta(minutes=40),
                ),
            ]
        )
        session.add_all(
            [
                CodexDebugRun(
                    id="debug-eng",
                    user_id="eng1",
                    skill_id="sf-skill",
                    requested_tools_json=[],
                    manifest_json={},
                    input_json={},
                    limits_json={},
                    expires_at=now + timedelta(hours=1),
                    created_at=now - timedelta(hours=4),
                ),
                CodexDebugRun(
                    id="debug-other",
                    user_id="other1",
                    skill_id="other-skill",
                    requested_tools_json=[],
                    manifest_json={},
                    input_json={},
                    limits_json={},
                    expires_at=now + timedelta(hours=1),
                    created_at=now - timedelta(hours=4),
                ),
            ]
        )
        session.add_all(
            [
                ExecutionRun(
                    id="run-eng",
                    skill_id="sf-skill",
                    trigger_type="codex:eng1",
                    run_mode="local_debug",
                    status="completed",
                    started_at=now - timedelta(hours=2),
                    completed_at=now - timedelta(hours=1, minutes=55),
                ),
                ExecutionRun(
                    id="run-other",
                    skill_id="other-skill",
                    trigger_type="codex:other1",
                    run_mode="local_debug",
                    status="completed",
                    started_at=now - timedelta(hours=2),
                    completed_at=now - timedelta(hours=1, minutes=55),
                ),
            ]
        )
        session.add_all(
            [
                DecisionLog(
                    run_id="run-eng",
                    skill_id="sf-skill",
                    output_result={
                        "reports": [
                            {
                                "title": "工程报告",
                                "summary": "SF 触发形成的工程报告",
                                "channel": "daily",
                                "tags": ["sf", "eng"],
                            }
                        ]
                    },
                    created_at=now - timedelta(hours=1, minutes=50),
                ),
                DecisionLog(
                    run_id="run-other",
                    skill_id="other-skill",
                    output_result={
                        "reports": [
                            {
                                "title": "业务报告",
                                "summary": "其他用户报告",
                                "channel": "weekly",
                            }
                        ]
                    },
                    created_at=now - timedelta(hours=1, minutes=45),
                ),
            ]
        )
        await session.commit()

    async with db_mod.async_session_factory() as session:
        engineer = await session.get(User, "eng1")
        personal = await build_sf_overview(session, engineer, days=30, page=1, page_size=20)
        assert personal["summary"]["scope"] == "personal"
        assert personal["summary"]["total_calls"] == 3
        assert personal["summary"]["mcp_calls"] == 2
        assert personal["summary"]["failed_mcp_calls"] == 1
        assert personal["summary"]["report_count"] == 1
        assert personal["reports"][0]["title"] == "工程报告"
        assert {item["tool"] for item in personal["top_tools"]} == {"skillforge_org_search_users"}
        assert {item["user_id"] for item in personal["recent_events"]["items"]} == {"eng1"}

        admin = await session.get(User, "admin")
        overview = await build_sf_overview(session, admin, days=30, page=1, page_size=20)
        assert overview["summary"]["scope"] == "global"
        assert overview["summary"]["total_calls"] == 5
        assert overview["summary"]["mcp_calls"] == 3
        assert overview["summary"]["report_count"] == 2
        assert sum(day["report_count"] for day in overview["daily_usage"]) == 2
        assert {item["tool"] for item in overview["top_tools"]} == {
            "skillforge_org_search_users",
            "tmall_store_weekly_snapshot",
        }

        filtered = await build_sf_overview(
            session,
            admin,
            days=30,
            page=1,
            page_size=20,
            kind="mcp",
            tool="skillforge_org_search_users",
            skill_id="sf-skill",
            q="MOCK_FAIL",
        )
        assert filtered["summary"]["mcp_calls"] == 2
        assert filtered["summary"]["report_count"] == 1
        assert filtered["recent_events"]["total"] == 1
        assert filtered["recent_events"]["items"][0]["tool"] == "skillforge_org_search_users"
        assert filtered["recent_events"]["items"][0]["ok"] is False

        scoped_override = await build_sf_overview(
            session,
            engineer,
            days=30,
            page=1,
            page_size=20,
            user_id="other1",
        )
        assert {item["user_id"] for item in scoped_override["recent_events"]["items"]} == {"eng1"}
        assert scoped_override["filters"]["user_id"] == "eng1"

    response = await client.get("/api/sf/overview", params={"days": 30, "page_size": 20, "kind": "mcp", "tool": "skillforge_org_search_users"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["scope"] == "global"
    assert payload["summary"]["total_calls"] == 5
    assert payload["recent_events"]["total"] == 2
    assert all(item["kind"] == "mcp" for item in payload["recent_events"]["items"])
    assert payload["daily_usage"]
    assert payload["health"]["latest_version"]
    assert payload["failure_summary"]["by_error_code"][0]["error_code"] == "MOCK_FAIL"
    assert payload["rankings"]["users"]
    assert payload["output_summary"]["report_count"] == 2
    assert payload["reports"][0]["id"]

    catalog_response = await client.get("/api/sf/catalog", params={"days": 30})
    assert catalog_response.status_code == 200
    catalog = catalog_response.json()
    assert catalog["command_groups"]
    assert any(tool["name"] == "skillforge_org_search_users" for tool in catalog["mcp_capabilities"]["tools"])

    event_id = payload["recent_events"]["items"][0]["id"]
    trace_response = await client.get("/api/sf/trace", params={"event_id": event_id})
    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["event"]["id"] == event_id
    assert trace["proof_id"]

    dry_run_response = await client.post(
        "/api/sf/mcp/dry-run",
        json={"tool": "skillforge_org_search_users", "arguments": {"query": "eng", "limit": 5}},
    )
    assert dry_run_response.status_code == 200
    assert dry_run_response.json()["proof"]["dry_run"] is True
