import json

import pytest


def _mock_trace_user(
    *,
    user_id: str = "u1",
    role: str = "operator",
    department: str = "EC",
):
    from unittest.mock import MagicMock

    user = MagicMock()
    user.id = user_id
    user.role = role
    user.department = department
    user.can_view_all = False
    user.is_active = True
    return user


@pytest.mark.asyncio
async def test_run_trace_includes_collection_analyze_usage_and_decision(client):
    from decimal import Decimal

    from app.collection.models import CollectionProof
    from app.common.models import IntelligenceAnalyzeCache, IntelligenceAnalyzeRun, UsageLog
    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun

    async with async_session_factory() as session:
        session.add(ExecutionRun(
            id="run-trace-1",
            skill_id="skill-1",
            trigger_type="manual",
            run_mode="manual_real",
            status="completed",
            metadata_json={"skill_git_commit_full": "abc123"},
        ))
        session.add(CollectionProof(
            proof_id="proof-1",
            run_id="run-trace-1",
            skill_id="skill-1",
            platform="sycm",
            shop_id="shop-1",
            data_scope="sycm.item_rank",
            endpoint_family="read_metrics",
            warning_group="sycm.report_read",
            credential_alias="sycm-shop-1-owner",
            status="success",
            response_hash="hash-1",
            data_keys=["rows"],
            row_count=1,
        ))
        cache = IntelligenceAnalyzeCache(
            cache_key="cache-1",
            model="deepseek-chat",
            prompt_hash="p" * 64,
            context_hash="c" * 64,
            output_hash="o" * 64,
            output_payload={"insights": []},
            first_seen_run_id="run-trace-1",
            hit_count=0,
        )
        session.add(cache)
        await session.flush()
        session.add(IntelligenceAnalyzeRun(
            cache_key="cache-1",
            cache_id=cache.id,
            cache_hit=False,
            skill_id="skill-1",
            skill_git_commit_full="abc123",
            prompt_git_ref="abc123:prompts/analysis_v1.md",
            run_id="run-trace-1",
            analysis_backend="agent",
            analysis_agent_id="analysis-node-1",
            analysis_delegate_route={
                "mode": "auto",
                "scope": "department",
                "agent_id": "analysis-node-1",
                "auth_token": "agent-secret-token",
            },
            model="deepseek-chat",
            prompt_version="analysis_v1",
            prompt_hash="p" * 64,
            context_hash="c" * 64,
            cost_usd=Decimal("0.010000"),
        ))
        session.add(UsageLog(
            skill_id="skill-1",
            department="EC",
            call_source="intelligence_analyze",
            model="deepseek-chat",
            input_tokens=10,
            output_tokens=5,
            metadata_json={"run_id": "run-trace-1"},
        ))
        session.add(DecisionLog(
            run_id="run-trace-1",
            skill_id="skill-1",
            input_snapshot={"date": "2026-05-21"},
            output_result={"reports": []},
            approval_status="auto",
        ))
        await session.commit()

    resp = await client.get("/api/admin/runs/run-trace-1/trace")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["execution_run"]["skill_id"] == "skill-1"
    assert body["collection_proofs"][0]["proof_id"] == "proof-1"
    assert "cookie_pool_id" not in body["collection_proofs"][0]
    assert body["intelligence_analyze_runs"][0]["cache_key"] == "cache-1"
    assert body["intelligence_analyze_runs"][0]["analysis_backend"] == "agent"
    assert body["intelligence_analyze_runs"][0]["analysis_agent_id"] == "analysis-node-1"
    assert body["intelligence_analyze_runs"][0]["analysis_delegate_route"]["scope"] == "department"
    assert "agent-secret-token" not in json.dumps(body["intelligence_analyze_runs"][0], ensure_ascii=False)
    assert body["usage_logs"][0]["call_source"] == "intelligence_analyze"
    assert body["decision_log"][0]["approval_status"] == "auto"
    assert body["decision_log"][0]["input_snapshot"]["date"] == "2026-05-21"


@pytest.mark.asyncio
async def test_run_trace_analyze_endpoint_collects_raw_data_and_calls_platform_ai(client, monkeypatch):
    import json

    from app.codex import service as codex_service
    from app.collection.models import CollectionProof
    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun, ExecutionStep

    async def fake_call_llm(system, user, **kwargs):
        payload = json.loads(user)
        assert payload["context_pack"]["run"]["id"] == "run-trace-ai-review"
        assert payload["context_pack"]["decision_logs"][0]["input_snapshot"]["token"] == "[REDACTED]"
        assert kwargs["model_override"] == codex_service.PLATFORM_AI_MCP_MODEL
        assert kwargs["require_system_config"] is True
        assert kwargs["cost_context"]["conversation_id"] == "run-trace-ai-review"
        return "本次运行完成，建议补充 proof 异常解释。"

    monkeypatch.setattr(codex_service, "call_llm", fake_call_llm)

    async with async_session_factory() as session:
        session.add(ExecutionRun(
            id="run-trace-ai-review",
            skill_id="skill-ai-review-missing",
            trigger_type="manual",
            run_mode="manual_real",
            status="completed",
            metadata_json={"token": "run-secret"},
        ))
        session.add(ExecutionStep(
            run_id="run-trace-ai-review",
            skill_id="skill-ai-review-missing",
            step_order=1,
            status="completed",
            input_data={"authorization": "Bearer raw-step-token"},
            output_data={"summary": "ok"},
        ))
        session.add(DecisionLog(
            run_id="run-trace-ai-review",
            skill_id="skill-ai-review-missing",
            input_snapshot={"token": "raw-decision-token", "date": "2026-05-22"},
            output_result={"reports": [{"title": "日报"}]},
            approval_status="auto",
        ))
        session.add(CollectionProof(
            proof_id="proof-ai-review",
            run_id="run-trace-ai-review",
            skill_id="skill-ai-review-missing",
            platform="tmall",
            shop_id="shop-1",
            data_scope="reviews",
            endpoint_family="item_reviews",
            warning_group="read",
            status="success",
            response_hash="hash",
            row_count=1,
        ))
        await session.commit()

    resp = await client.post(
        "/api/admin/runs/run-trace-ai-review/trace/analyze",
        json={"include_raw": True, "max_output_tokens": 512},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == "run-trace-ai-review"
    assert body["analysis"] == "本次运行完成，建议补充 proof 异常解释。"
    assert body["model"] == codex_service.PLATFORM_AI_MCP_MODEL
    assert body["raw_counts"]["execution_steps"] == 1
    assert body["raw_counts"]["decision_logs"] == 1
    assert body["raw_counts"]["collection_proofs"] == 1
    body_text = json.dumps(body, ensure_ascii=False)
    assert "raw-decision-token" not in body_text
    assert "raw-step-token" not in body_text
    assert "[REDACTED]" in body_text


@pytest.mark.asyncio
async def test_run_trace_redacts_historical_portal_ui_snapshot_secrets(client):
    import json

    from app.common.models import UsageLog
    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun

    async with async_session_factory() as session:
        session.add(ExecutionRun(
            id="run-trace-ui-secret",
            skill_id="skill-ui",
            trigger_type="portal:u1",
            run_mode="manual_real",
            status="completed",
            metadata_json={
                "portal_submission_id": "sub-secret",
                "user_id": "u1",
                "failure": {
                    "message": "provider failed api_key=sk-metadata-secret Authorization: Bearer raw-metadata-token",
                },
                "ui": {
                    "overlay": {
                        "components": [{
                            "id": "field-api-key",
                            "type": "field",
                            "title": "API Key",
                            "binding": "params.api_key",
                        }],
                    },
                    "actual_params": {
                        "date": "2026-05-21",
                        "api_key": "sk-run-trace-secret",
                    },
                },
            },
        ))
        session.add(DecisionLog(
            run_id="run-trace-ui-secret",
            skill_id="skill-ui",
            input_snapshot={
                "date": "2026-05-21",
                "api_key": "sk-decision-secret",
            },
            output_result={
                "reports": [{
                    "title": "日报",
                    "api_key": "sk-output-secret",
                    "token": "raw-output-token",
                }],
            },
            approval_status="auto",
        ))
        session.add(UsageLog(
            skill_id="skill-ui",
            department="EC",
            call_source="portal_ui_ai",
            model="deepseek-chat",
            input_tokens=10,
            output_tokens=5,
            metadata_json={
                "run_id": "run-trace-ui-secret",
                "api_key": "sk-usage-metadata-secret",
            },
        ))
        await session.commit()

    resp = await client.get("/api/admin/runs/run-trace-ui-secret/trace")

    assert resp.status_code == 200
    body_text = json.dumps(resp.json(), ensure_ascii=False)
    assert "2026-05-21" in body_text
    assert "sk-run-trace-secret" not in body_text
    assert "sk-decision-secret" not in body_text
    assert "sk-output-secret" not in body_text
    assert "raw-output-token" not in body_text
    assert "sk-metadata-secret" not in body_text
    assert "raw-metadata-token" not in body_text
    assert "sk-usage-metadata-secret" not in body_text
    assert "params.api_key" not in body_text
    assert "API Key" not in body_text
    assert "[REDACTED]" in body_text


@pytest.mark.asyncio
async def test_execution_success_preserves_portal_ui_metadata(client):
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.execution.execution_service import ExecutionService
    from app.execution.models import DecisionLog, ExecutionRun

    ui_snapshot = {
        "ui_pref_id": "uipref-1",
        "ui_pref_version": 2,
        "base_skill_commit": "abc123",
        "merged_ui_schema_hash": "sha256:ui",
    }
    async with async_session_factory() as session:
        session.add(ExecutionRun(
            id="run-ui-success",
            skill_id="skill-ui",
            trigger_type="portal:u1",
            run_mode="manual_real",
            status="running",
            metadata_json={
                "portal_submission_id": "sub-1",
                "ui": ui_snapshot,
                "skill_git_commit_full": "abc123",
                "run_token_claim": {"skill_id": "skill-ui"},
            },
        ))
        await session.commit()

    await ExecutionService()._persist_execution_success(
        run_id="run-ui-success",
        skill_id="skill-ui",
        params={"date": "2026-05-21"},
        output={"data": []},
        resolved_run_mode="manual_real",
        parent_run_id=None,
        batch_id=None,
        normalized_data_proofs=[],
        resolved_sample_used=False,
        skill_approval_level=None,
        sandbox=False,
    )

    async with async_session_factory() as session:
        run = await session.get(ExecutionRun, "run-ui-success")
        log = (
            await session.execute(
                select(DecisionLog).where(DecisionLog.run_id == "run-ui-success")
            )
        ).scalar_one()

    assert run.metadata_json["portal_submission_id"] == "sub-1"
    assert run.metadata_json["ui"] == ui_snapshot
    assert log.input_snapshot["date"] == "2026-05-21"


@pytest.mark.asyncio
async def test_run_trace_cookie_audit_uses_exact_platform_shop_pairs(client):
    from app.collection.models import CollectionProof
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.datasources.models import PlatformCookieAudit
    from app.execution.models import ExecutionRun

    started = now_bjt()
    async with async_session_factory() as session:
        session.add(ExecutionRun(
            id="run-trace-pairs",
            skill_id="skill-pairs",
            trigger_type="manual",
            run_mode="manual_real",
            status="completed",
            started_at=started,
            completed_at=started,
        ))
        session.add_all([
            CollectionProof(
                proof_id="proof-a",
                run_id="run-trace-pairs",
                skill_id="skill-pairs",
                platform="taobao",
                shop_id="shop-a",
                data_scope="scope",
                endpoint_family="family",
                warning_group="group",
                credential_alias="alias-a",
                status="success",
            ),
            CollectionProof(
                proof_id="proof-b",
                run_id="run-trace-pairs",
                skill_id="skill-pairs",
                platform="jd",
                shop_id="shop-b",
                data_scope="scope",
                endpoint_family="family",
                warning_group="group",
                credential_alias="alias-b",
                status="success",
            ),
        ])
        for idx, (platform, shop_id) in enumerate([
            ("taobao", "shop-a"),
            ("jd", "shop-b"),
            ("taobao", "shop-b"),
            ("jd", "shop-a"),
        ], start=1):
            session.add(PlatformCookieAudit(
                cookie_pool_id=idx,
                source_id=f"source-{idx}",
                platform=platform,
                shop_id=shop_id,
                owner_user_id="owner",
                action="push",
                auth_source="connector_api_key",
                detail={
                    "message": "Cookie: session_id=raw-cookie-secret",
                    "api_key": "sk-cookie-audit-secret",
                },
                created_at=started,
            ))
        await session.commit()

    resp = await client.get("/api/admin/runs/run-trace-pairs/trace")

    assert resp.status_code == 200
    body = resp.json()
    returned_pairs = {(item["platform"], item["shop_id"]) for item in body["platform_cookie_audit"]}
    assert returned_pairs == {("taobao", "shop-a"), ("jd", "shop-b")}
    body_text = str(body)
    assert "raw-cookie-secret" not in body_text
    assert "sk-cookie-audit-secret" not in body_text


@pytest.mark.asyncio
async def test_portal_run_trace_access_allows_requester_from_trigger():
    from unittest.mock import AsyncMock

    from app.execution.models import ExecutionRun
    from app.execution.run_trace_router import _assert_trace_access

    db = AsyncMock()
    user = _mock_trace_user(user_id="u1", department="EC")
    run = ExecutionRun(
        id="run-portal-owner",
        skill_id="skill-ui",
        trigger_type="portal:u1",
        run_mode="manual_real",
        status="completed",
        metadata_json={"portal_submission_id": "sub-1"},
    )

    await _assert_trace_access(db, user, run)


@pytest.mark.asyncio
async def test_portal_run_trace_access_allows_requester_from_submission_id():
    from unittest.mock import AsyncMock, MagicMock

    from app.execution.models import ExecutionRun
    from app.execution.run_trace_router import _assert_trace_access

    db = AsyncMock()
    db.get = AsyncMock(return_value=MagicMock(requester_id="u1"))
    user = _mock_trace_user(user_id="u1", department="EC")
    run = ExecutionRun(
        id="run-portal-submission-owner",
        skill_id="skill-ui",
        trigger_type="manual",
        run_mode="manual_real",
        status="completed",
        metadata_json={"portal_submission_id": "sub-1"},
    )

    await _assert_trace_access(db, user, run)
    db.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_portal_run_trace_access_denies_same_department_non_requester():
    from unittest.mock import AsyncMock, MagicMock

    from app.common.exceptions import AppError
    from app.execution.models import ExecutionRun
    from app.execution.run_trace_router import _assert_trace_access

    db = AsyncMock()
    db.get = AsyncMock(return_value=MagicMock(owner="owner-1", department="EC", org_unit_id="EC"))
    user = _mock_trace_user(user_id="u2", department="EC")
    run = ExecutionRun(
        id="run-portal-other-user",
        skill_id="skill-ui",
        trigger_type="portal:u1",
        run_mode="manual_real",
        status="completed",
        metadata_json={"portal_submission_id": "sub-1"},
    )

    with pytest.raises(AppError) as exc_info:
        await _assert_trace_access(db, user, run)

    assert exc_info.value.code == "AUTH_PERMISSION_DENIED"
    db.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_portal_run_trace_access_denies_ai_engineer_without_global_view():
    from unittest.mock import AsyncMock

    from app.common.exceptions import AppError
    from app.execution.models import ExecutionRun
    from app.execution.run_trace_router import _assert_trace_access

    db = AsyncMock()
    user = _mock_trace_user(user_id="u2", role="ai_engineer", department="EC")
    run = ExecutionRun(
        id="run-portal-ai-engineer",
        skill_id="skill-ui",
        trigger_type="portal:u1",
        run_mode="manual_real",
        status="completed",
        metadata_json={"portal_submission_id": "sub-1"},
    )

    with pytest.raises(AppError) as exc_info:
        await _assert_trace_access(db, user, run)

    assert exc_info.value.code == "AUTH_PERMISSION_DENIED"
    db.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_portal_run_trace_access_allows_global_viewer():
    from unittest.mock import AsyncMock

    from app.execution.models import ExecutionRun
    from app.execution.run_trace_router import _assert_trace_access

    db = AsyncMock()
    user = _mock_trace_user(user_id="auditor", role="operator", department="EC")
    user.can_view_all = True
    run = ExecutionRun(
        id="run-portal-auditor",
        skill_id="skill-ui",
        trigger_type="portal:u1",
        run_mode="manual_real",
        status="completed",
        metadata_json={"portal_submission_id": "sub-1"},
    )

    await _assert_trace_access(db, user, run)
