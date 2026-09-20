import hashlib
import json
import warnings
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.database as db_mod
from app.common.models import (
    IntelligenceAnalyzeCache,
    IntelligenceAnalyzeRun,
    SystemConfig,
    UsageLog,
)
from app.common.time_utils import now_bjt
from app.execution.models import DecisionLog, ExecutionRun
from app.execution.execution_service import issue_run_token
from app.intelligence.service import LLMAnalyzeResult, _deployment_rollout_bucket
from app.learning.models import LearningEvent, LearningFlowEdge
from app.skills.core.models import Skill
from app.training.models import TrainingJob, TrainingModelDeployment


SKILL_ID = "tmall-store-weekly-insight-v1"
COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _ensure_router(client):
    app = client._transport.app
    if getattr(app.state, "intelligence_router_registered", False):
        return
    from app.intelligence.router import router

    app.include_router(router, prefix="/api/intelligence")
    app.state.intelligence_router_registered = True


async def _seed_skill_and_pricing(run_id: str = "run-1"):
    async with db_mod.async_session_factory() as session:
        session.add(
            Skill(
                id=SKILL_ID,
                name="天猫全店洞察",
                department="EC",
                status="active",
                git_commit=COMMIT,
                approval_level=0,
            )
        )
        session.add(
            SystemConfig(
                key="ai.pricing.deepseek-v4-pro",  # gitleaks:allow -- enum/config key or deliberate synthetic test fixture; not a credential
                value={"input": 1.0, "output": 2.0, "cache_read": 0, "cache_write": 0},
            )
        )
        await session.commit()


def _token(run_id: str = "run-1", commit: str = COMMIT, instance_id: str = "node-1") -> str:
    return issue_run_token(
        skill_id=SKILL_ID,
        run_id=run_id,
        instance_id=instance_id,
        skill_git_commit_full=commit,
        department="EC",
    )


def _body(run_id: str = "run-1", commit: str = COMMIT, cache_key: str | None = "store:demo"):
    body = {
        "skill_id": SKILL_ID,
        "skill_git_commit_full": commit,
        "run_id": run_id,
        "instance_id": "node-1",
        "prompt_version": "analysis_v1",
        "context_pack": {"facts": {"gmv": 100}, "proofs": [{"status": "success"}]},
    }
    if cache_key is not None:
        body["cache_key"] = cache_key
    return body


def _run_id_for_rollout(deployment_id: str, *, selected: bool, percent: int = 10) -> str:
    for index in range(1000):
        run_id = f"run-rollout-{deployment_id}-{index}"
        bucket = _deployment_rollout_bucket(SKILL_ID, run_id, deployment_id)
        if (bucket < percent) == selected:
            return run_id
    raise AssertionError(f"could not find rollout bucket selected={selected} percent={percent}")


async def _seed_canary_rollout_deployments(run_id: str) -> None:
    from app.execution.models import OpenClawInstance

    now = now_bjt()
    async with db_mod.async_session_factory() as session:
        session.add(
            OpenClawInstance(
                id="node-canary-decision",
                name="EC canary model node",
                department="EC",
                gateway_url="ws://node-canary-decision",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                bridge_gateway_kind="bridge",
                bridge_capabilities_json=json.dumps({"ops": ["training.inference"]}),
            )
        )
        session.add(
            ExecutionRun(
                id=run_id,
                skill_id=SKILL_ID,
                run_mode="manual_real",
                trigger_type="manual",
                status="running",
                metadata_json={},
            )
        )
        session.add(
            TrainingJob(
                id="train-active-rollout-decision",
                title="active rollout decision model",
                department="EC",
                created_by="admin",
                status="completed",
                job_type="qlora",
                target_skill_id=SKILL_ID,
                target_gateway_id="node-canary-decision",
            )
        )
        session.add(
            TrainingJob(
                id="train-canary-rollout-decision",
                title="canary rollout decision model",
                department="EC",
                created_by="admin",
                status="completed",
                job_type="qlora",
                target_skill_id=SKILL_ID,
                target_gateway_id="node-canary-decision",
            )
        )
        session.add(
            TrainingModelDeployment(
                id="deploy-active-rollout-decision",
                job_id="train-active-rollout-decision",
                department="EC",
                model_family=f"{SKILL_ID}:learning_loop_improvement",
                artifact_id="adapter-active-rollout-decision",
                artifact_ref_json={
                    "id": "adapter-active-rollout-decision",
                    "sha256": "b" * 64,
                    "uri": "/tmp/adapter-active-rollout-decision.tar.gz",
                },
                target_skill_ids_json=[SKILL_ID],
                status="active",
                rollout_percent=100,
                requested_by="admin",
                approved_by="admin",
                approved_at=now,
                activated_at=now,
            )
        )
        session.add(
            TrainingModelDeployment(
                id="deploy-canary-rollout-decision",
                job_id="train-canary-rollout-decision",
                department="EC",
                model_family=f"{SKILL_ID}:learning_loop_improvement",
                artifact_id="adapter-canary-rollout-decision",
                artifact_ref_json={
                    "id": "adapter-canary-rollout-decision",
                    "sha256": "c" * 64,
                    "uri": "/tmp/adapter-canary-rollout-decision.tar.gz",
                },
                target_skill_ids_json=[SKILL_ID],
                status="canary",
                rollout_percent=10,
                requested_by="admin",
                approved_by="admin",
                approved_at=now,
            )
        )
        await session.commit()


def _patch_deployment_model_status(
    monkeypatch,
    *,
    ready_ids: set[str] | None = None,
    statuses: dict[str, str] | None = None,
) -> None:
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient

    statuses = statuses or {}
    ready_ids = ready_ids or set(statuses.keys())

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id in ready_ids)

    async def fake_model_status(self, payload, *, timeout=30):
        status = statuses.get(self.instance_id, "ready")
        return {
            "status": status,
            "profile": payload.get("profile") or "qwen3.5-4b",
            "exists": status in {"ready", "prepared", "available", "completed"},
            "bridge_instance_id": self.instance_id,
        }

    monkeypatch.setattr(AIClawClient, "get_training_model_status", fake_model_status)


@pytest.mark.asyncio
async def test_raw_model_and_prompt_fields_rejected(client):
    await _ensure_router(client)
    body = _body()
    body["model"] = "deepseek-chat"
    body["json"] = True
    body["system"] = "raw prompt"

    resp = await client.post(
        "/api/intelligence/analyze",
        json=body,
        headers={"X-Run-Token": _token()},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MODEL_PARAM_NOT_ACCEPTED"
    async with db_mod.async_session_factory() as session:
        count = len((await session.execute(select(IntelligenceAnalyzeRun))).scalars().all())
        assert count == 0


@pytest.mark.asyncio
async def test_invalid_body_returns_param_invalid_not_500(client):
    await _ensure_router(client)
    body = _body(commit="")

    resp = await client.post(
        "/api/intelligence/analyze",
        json=body,
        headers={"X-Run-Token": _token()},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PARAM_INVALID"
    assert resp.json()["error"]["detail"]["errors"][0]["loc"] == ["skill_git_commit_full"]
    async with db_mod.async_session_factory() as session:
        count = len((await session.execute(select(IntelligenceAnalyzeRun))).scalars().all())
        assert count == 0


@pytest.mark.asyncio
async def test_commit_mismatch_is_run_token_invalid_before_prompt_load(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("prompt loader should not be called on commit mismatch")

    from app.skills.core.git_service import git_service

    monkeypatch.setattr(git_service, "get_file_at_commit", fail_if_called)

    resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(commit=OTHER_COMMIT),
        headers={"X-Run-Token": _token(commit=COMMIT)},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "RUN_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_prompt_missing_returns_prompt_version_not_found(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing()

    from app.skills.core.git_service import git_service

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: None)

    resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(),
        headers={"X-Run-Token": _token()},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PROMPT_VERSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_model_not_allowed_rejects_before_prompt_load(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing()

    async with db_mod.async_session_factory() as session:
        session.add(SystemConfig(key="intelligence.allowed_models", value=["deepseek-chat"]))
        session.add(SystemConfig(key="intelligence.default_model", value="forbidden-model"))
        await session.commit()

    from app.skills.core.git_service import git_service

    monkeypatch.setattr(
        git_service,
        "get_file_at_commit",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("prompt should not load")),
    )

    resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(),
        headers={"X-Run-Token": _token()},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MODEL_NOT_ALLOWED"
    async with db_mod.async_session_factory() as session:
        count = len((await session.execute(select(IntelligenceAnalyzeRun))).scalars().all())
        assert count == 0


@pytest.mark.asyncio
async def test_budget_exceeded_returns_402_without_run_row(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing()

    async with db_mod.async_session_factory() as session:
        session.add(SystemConfig(key="intelligence.max_input_tokens_per_run", value=1))
        await session.commit()

    from app.skills.core.git_service import git_service

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "prompt needs context")

    import app.intelligence.service as service_module

    async def fail_llm(**kwargs):
        raise AssertionError("LLM should not be called when budget is exceeded")

    monkeypatch.setattr(service_module, "_call_llm_json", fail_llm)

    resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(),
        headers={"X-Run-Token": _token()},
    )

    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "BUDGET_EXCEEDED"
    async with db_mod.async_session_factory() as session:
        count = len((await session.execute(select(IntelligenceAnalyzeRun))).scalars().all())
        assert count == 0


@pytest.mark.asyncio
async def test_analyze_success_cache_and_default_cache_key(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing()

    prompt = "请基于 context_pack 输出 JSON 洞察"
    from app.skills.core.git_service import git_service

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: prompt)
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return LLMAnalyzeResult(
            output={"summary": f"ok-{len(calls)}"},
            usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            raw_output='{"summary":"ok"}',
        )

    import app.intelligence.service as service_module

    monkeypatch.setattr(service_module, "_call_llm_json", fake_llm)

    first = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id="run-1", cache_key="store:2026-05-11"),
        headers={"X-Run-Token": _token(run_id="run-1")},
    )
    second = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id="run-2", cache_key="store:2026-05-11"),
        headers={"X-Run-Token": _token(run_id="run-2")},
    )
    third_body = _body(run_id="run-3", cache_key=None)
    third = await client.post(
        "/api/intelligence/analyze",
        json=third_body,
        headers={"X-Run-Token": _token(run_id="run-3")},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert third.status_code == 200, third.text
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert third.json()["cache_hit"] is False
    assert len(calls) == 2

    async with db_mod.async_session_factory() as session:
        from app.learning.models import LearningArtifact, LearningEvent, LearningFlowEdge

        cache_rows = (await session.execute(select(IntelligenceAnalyzeCache))).scalars().all()
        run_rows = (
            await session.execute(
                select(IntelligenceAnalyzeRun).order_by(IntelligenceAnalyzeRun.id.asc())
            )
        ).scalars().all()
        usage_rows = (await session.execute(select(UsageLog))).scalars().all()
        learning_events = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "intelligence_analyze_run")
                .order_by(LearningEvent.source_id.asc())
            )
        ).scalars().all()
        analysis_samples = (
            await session.execute(
                select(LearningArtifact)
                .where(LearningArtifact.artifact_kind == "training_sample")
                .where(LearningArtifact.target_id == SKILL_ID)
                .order_by(LearningArtifact.created_at.asc())
            )
        ).scalars().all()
        used_analysis_edges = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "run")
                .where(LearningFlowEdge.to_type == "intelligence_analyze")
                .where(LearningFlowEdge.relation == "used_analysis")
                .order_by(LearningFlowEdge.to_id.asc())
            )
        ).scalars().all()

    assert len(cache_rows) == 2
    assert len(run_rows) == 3
    assert len(learning_events) == 3
    assert {event.run_id for event in learning_events} == {"run-1", "run-2", "run-3"}
    assert learning_events[1].metadata_json["cache_hit"] is True
    assert learning_events[1].metadata_json["cache_hit_of_run_id"] == "run-1"
    assert len(analysis_samples) == 3
    assert all(sample.status == "materialized" for sample in analysis_samples)
    assert all(sample.sink_type == "training_sample" for sample in analysis_samples)
    assert all(sample.content_json["input"]["raw_context_returned"] is False for sample in analysis_samples)
    assert all(sample.content_json["output"]["summary"].startswith("ok-") for sample in analysis_samples)
    assert "gmv" not in json.dumps([sample.content_json["input"] for sample in analysis_samples], ensure_ascii=False)
    assert {edge.from_id for edge in used_analysis_edges} == {"run-1", "run-2", "run-3"}
    assert run_rows[0].prompt_git_ref == f"{COMMIT}:prompts/analysis_v1.md"
    assert run_rows[0].skill_git_commit_full == COMMIT
    assert run_rows[1].cache_hit is True
    assert run_rows[1].cache_hit_of_run_id == "run-1"
    assert run_rows[1].cost_usd == Decimal("0.000000")
    context_hash = _sha(
        '{"facts":{"gmv":100},"proofs":[{"status":"success"}]}'
    )
    prompt_hash = _sha(prompt)
    assert run_rows[2].cache_key == _sha(f"{SKILL_ID}|run-3|{prompt_hash}|{context_hash}")
    assert len(usage_rows) == 2
    assert usage_rows[0].call_source == "intelligence_analyze"
    assert len(usage_rows[0].prompt_hash) == 64
    assert usage_rows[0].metadata_json["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_call_llm_json_extracts_json_and_retries_parse_error(monkeypatch):
    import app.intelligence.service as service_module

    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, content: str, usage: dict):
            self._content = content
            self._usage = usage
            self.text = content

        def json(self):
            return {
                "choices": [{"message": {"content": self._content}}],
                "usage": self._usage,
            }

    class FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, json=None):
            calls.append(json)
            return FakeResponse(
                '先解释一下：{"summary":"ok"}',
                {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            )

    async def fake_config():
        return {"ai.api_base": "http://llm.local", "ai.api_key": "key"}

    monkeypatch.setattr(service_module, "get_ai_config", fake_config)
    monkeypatch.setattr(service_module.httpx, "AsyncClient", FakeClient)

    result = await service_module._call_llm_json(
        model="deepseek-v4-flash",
        system_prompt="return json",
        context_pack={"facts": {"gmv": 100}},
        max_output_tokens=100,
        timeout=10,
    )

    assert result.output == {"summary": "ok"}
    assert len(calls) == 1

    calls.clear()

    async def post_retry(self, url, headers=None, json=None):
        calls.append(json)
        if len(calls) == 1:
            return FakeResponse("not json", {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13})
        return FakeResponse(
            '```json\n{"summary":"retry-ok"}\n```',
            {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
        )

    monkeypatch.setattr(FakeClient, "post", post_retry)

    retried = await service_module._call_llm_json(
        model="deepseek-v4-flash",
        system_prompt="return json",
        context_pack={"facts": {"gmv": 100}},
        max_output_tokens=100,
        timeout=10,
    )

    assert retried.output == {"summary": "retry-ok"}
    assert retried.usage == {"prompt_tokens": 21, "completion_tokens": 7, "total_tokens": 28}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_analyze_delegates_to_agent_when_bridge_reports_intelligence_op(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing()

    from app.execution.models import OpenClawInstance
    from app.skills.core.git_service import git_service
    import app.intelligence.service as service_module

    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id="node-1",
            name="EC 分析 Agent",
            department="EC",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
            agent_purpose="analysis",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({"ops": ["intelligence.analyze"]}),
        ))
        await session.commit()

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "agent prompt")

    async def fail_platform_llm(**kwargs):
        raise AssertionError("platform LLM should not be called when Agent can analyze")

    async def fake_agent_analyze(self, payload, *, timeout=120):
        assert self.instance_id == "node-1"
        assert payload["prompt_version"] == "analysis_v1"
        assert payload["context_pack"]["facts"]["gmv"] == 100
        return {
            "output": {"summary": "agent-ok"},
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            "model": "local-agent",
        }

    monkeypatch.setattr(service_module, "_call_llm_json", fail_platform_llm)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_intelligence_analyze", fake_agent_analyze)

    resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id="run-agent", cache_key="agent-cache"),
        headers={"X-Run-Token": _token(run_id="run-agent")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["output"] == {"summary": "agent-ok"}
    assert body["analysis_backend"] == "agent"
    assert body["analysis_agent_id"] == "node-1"
    assert body["analysis_delegate_route"]["mode"] == "current_instance"
    assert body["analysis_delegate_route"]["scope"] == "department"
    assert body["model"] == "agent:node-1"
    assert body["cost_usd"] == 0.0

    async with db_mod.async_session_factory() as session:
        run_rows = (await session.execute(select(IntelligenceAnalyzeRun))).scalars().all()
        usage_rows = (await session.execute(select(UsageLog))).scalars().all()

    assert run_rows[0].model == "agent:node-1"
    assert run_rows[0].analysis_backend == "agent"
    assert run_rows[0].analysis_agent_id == "node-1"
    assert run_rows[0].analysis_delegate_route["scope"] == "department"
    assert run_rows[0].cost_usd == Decimal("0.000000")
    assert usage_rows == []


@pytest.mark.asyncio
async def test_active_model_deployment_is_attached_to_submitted_decision(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing(run_id="run-model-decision")

    from app.skills.core.git_service import git_service
    from app.execution.models import OpenClawInstance
    import app.intelligence.service as service_module

    async with db_mod.async_session_factory() as session:
        session.add(
            OpenClawInstance(
                id="node-active-decision",
                name="EC active model node",
                department="EC",
                gateway_url="ws://node-active-decision",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                agent_purpose="training",
                bridge_gateway_kind="bridge",
                bridge_capabilities_json=json.dumps({"ops": ["training.inference"]}),
            )
        )
        session.add(
            TrainingJob(
                id="train-active-decision",
                title="active decision model",
                department="EC",
                created_by="admin",
                status="completed",
                job_type="qlora",
                target_skill_id=SKILL_ID,
                target_gateway_id="node-active-decision",
            )
        )
        session.add(
            ExecutionRun(
                id="run-model-decision",
                skill_id=SKILL_ID,
                run_mode="manual_real",
                trigger_type="manual",
                status="running",
                metadata_json={},
            )
        )
        session.add(
            TrainingModelDeployment(
                id="deploy-active-decision",
                job_id="train-active-decision",
                department="EC",
                model_family=f"{SKILL_ID}:learning_loop_improvement",
                artifact_id="adapter-active-decision",
                artifact_ref_json={
                    "id": "adapter-active-decision",
                    "sha256": "a" * 64,
                    "uri": "/tmp/adapter-active-decision.tar.gz",
                },
                target_skill_ids_json=[SKILL_ID],
                status="active",
                rollout_percent=100,
                requested_by="admin",
                activated_at=now_bjt(),
            )
        )
        await session.commit()

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "model prompt")

    async def fake_inference(self, payload, *, timeout=300):
        return {"text": "active adapter output", "text_sha256": "g" * 64, "metrics": {"generated_tokens": 9}}

    async def fake_llm(**kwargs):
        assert (
            kwargs["context_pack"]["skillforge"]["active_model_deployment"]["id"]
            == "deploy-active-decision"
        )
        assert kwargs["context_pack"]["skillforge"]["active_model_deployment"]["runtime_status"]["inference_ready"] is True
        return LLMAnalyzeResult(
            output={"summary": "model-decision-ok"},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            raw_output='{"summary":"model-decision-ok"}',
        )

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_training_inference", fake_inference)
    monkeypatch.setattr(service_module, "_call_llm_json", fake_llm)
    from app.aiclaw.bridge_registry import bridge_registry

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "node-active-decision")

    async def fake_model_status(self, payload, *, timeout=30):
        return {
            "status": "ready",
            "profile": payload.get("profile") or "qwen3.5-4b",
            "exists": True,
            "bridge_instance_id": self.instance_id,
            "raw_model_probe": {
                "local_path": "/tmp/raw-adapter-path-should-not-leak",
                "debug": "node raw status payload should not leak",
            },
        }

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.get_training_model_status", fake_model_status)

    analyze_resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id="run-model-decision", cache_key="active-model-decision-cache"),
        headers={"X-Run-Token": _token(run_id="run-model-decision")},
    )
    assert analyze_resp.status_code == 200, analyze_resp.text
    assert analyze_resp.json()["active_model_deployment"]["id"] == "deploy-active-decision"
    assert analyze_resp.json()["active_model_deployment"]["runtime_status"] == {
        "artifact_uri_present": True,
        "target_gateway_id": "node-active-decision",
        "target_gateway_kind": "bridge",
        "model_profile": "qwen3.5-4b",
        "model_status": {
            "status": "ready",
            "profile": "qwen3.5-4b",
            "exists": True,
            "bridge_instance_id": "node-active-decision",
        },
        "raw_payload_returned": False,
        "model_ready": True,
        "inference_ready": True,
    }
    assert "raw_model_probe" not in json.dumps(analyze_resp.json(), ensure_ascii=False)
    assert analyze_resp.json()["deployed_model_inference"]["status"] == "used"

    submit_resp = await client.post(
        "/api/executions/submit-result",
        json={
            "skill_id": SKILL_ID,
            "run_id": "run-model-decision",
            "output": {"summary": "业务结论已由分析模型辅助生成"},
            "params": {"date": "2026-06-17"},
            "triggered_by": "skill_sdk",
        },
        headers={"X-Run-Token": _token(run_id="run-model-decision")},
    )
    assert submit_resp.status_code == 200, submit_resp.text
    submit_body = submit_resp.json()
    assert submit_body["model_context"]["model_deployment_id"] == "deploy-active-decision"
    assert submit_body["model_context"]["inference_status"] == "used"
    assert submit_body["model_context"]["inference_gateway_id"] == "node-active-decision"
    assert submit_body["model_context"]["model_runtime_status"]["inference_ready"] is True
    assert submit_body["model_context"]["model_runtime_status"]["raw_payload_returned"] is False
    assert "raw_model_probe" not in json.dumps(submit_body, ensure_ascii=False)

    async with db_mod.async_session_factory() as session:
        decision = (
            await session.execute(
                select(DecisionLog).where(DecisionLog.run_id == "run-model-decision")
            )
        ).scalar_one()
        run = await session.get(ExecutionRun, "run-model-decision")
        analyze_run = (
            await session.execute(
                select(IntelligenceAnalyzeRun)
                .where(IntelligenceAnalyzeRun.run_id == "run-model-decision")
                .order_by(IntelligenceAnalyzeRun.id.desc())
                .limit(1)
            )
        ).scalar_one()
        analyze_event = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "intelligence_analyze_run")
                .where(LearningEvent.source_id == str(analyze_run.id))
            )
        ).scalar_one()
        served_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "agent")
                .where(LearningFlowEdge.from_id == "node-active-decision")
                .where(LearningFlowEdge.to_type == "model_deployment")
                .where(LearningFlowEdge.to_id == "deploy-active-decision")
                .where(LearningFlowEdge.relation == "served_model_inference")
            )
        ).scalar_one()
        controlled_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "agent")
                .where(LearningFlowEdge.from_id == "node-active-decision")
                .where(LearningFlowEdge.to_type == "intelligence_analyze")
                .where(LearningFlowEdge.to_id == str(analyze_run.id))
                .where(LearningFlowEdge.relation == "controlled_inference")
            )
        ).scalar_one()

    assert decision.model_id == "deploy-active-decision"
    assert decision.output_result["_skillforge_meta"]["model_context"]["artifact_sha256"] == "a" * 64
    assert decision.output_result["_skillforge_meta"]["model_context"]["model_runtime_status"]["inference_ready"] is True
    assert decision.output_result["_skillforge_meta"]["model_context"]["model_runtime_status"]["raw_payload_returned"] is False
    assert run.metadata_json["model_context"]["model_deployment_id"] == "deploy-active-decision"
    assert "raw_model_probe" not in json.dumps(decision.output_result, ensure_ascii=False)
    assert "raw_model_probe" not in json.dumps(run.metadata_json, ensure_ascii=False)
    assert analyze_event.metadata_json["inference_agent"]["agent_id"] == "node-active-decision"
    assert analyze_event.metadata_json["inference_agent"]["contract"]["complete"] is True
    assert analyze_event.metadata_json["inference_agent"]["contract"]["control_ops"] == ["training.inference"]
    assert analyze_event.metadata_json["inference_agent"]["contract"]["input_channels"] == [
        "intelligence_context_pack",
        "model_deployment_context",
    ]
    assert analyze_event.metadata_json["inference_agent"]["contract"]["output_channels"] == [
        "analysis_result",
        "decision_model_context",
        "deployed_model_inference",
    ]
    assert served_edge.metadata_json["inference_status"] == "used"
    assert served_edge.metadata_json["contract"]["flow_traceable"] is True
    assert controlled_edge.metadata_json["inference_text_sha256"] == "g" * 64
    assert controlled_edge.metadata_json["contract"]["lifecycle_ready"] is True

    journeys = (
        await client.get(
            "/api/learning/flow-journeys",
            params={"days": 7, "skill_id": SKILL_ID, "source_type": "intelligence_analyze_run"},
        )
    ).json()
    analyze_journey = next(
        item
        for item in journeys["items"]
        if item["source_id"] == str(analyze_run.id)
    )
    journey_steps = {step["action"]: step for step in analyze_journey["steps"] if step.get("action")}
    assert journey_steps["served_model_inference"]["stage"] == "control"
    assert journey_steps["served_model_inference"]["payload"]["agent_id"] == "node-active-decision"
    assert journey_steps["controlled_inference"]["payload"]["contract"]["flow_traceable"] is True


@pytest.mark.asyncio
async def test_active_model_deployment_skipped_when_node_model_not_ready(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing(run_id="run-model-not-ready-decision")

    from app.execution.models import OpenClawInstance
    from app.skills.core.git_service import git_service
    import app.intelligence.service as service_module

    async with db_mod.async_session_factory() as session:
        session.add(
            OpenClawInstance(
                id="node-not-ready-decision",
                name="EC not ready model node",
                department="EC",
                gateway_url="ws://node-not-ready-decision",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                bridge_gateway_kind="bridge",
                bridge_capabilities_json=json.dumps({"ops": ["training.inference", "training.model_status"]}),
            )
        )
        session.add(
            TrainingJob(
                id="train-not-ready-decision",
                title="not ready decision model",
                department="EC",
                created_by="admin",
                status="completed",
                job_type="qlora",
                target_skill_id=SKILL_ID,
                target_gateway_id="node-not-ready-decision",
            )
        )
        session.add(
            ExecutionRun(
                id="run-model-not-ready-decision",
                skill_id=SKILL_ID,
                run_mode="manual_real",
                trigger_type="manual",
                status="running",
                metadata_json={},
            )
        )
        session.add(
            TrainingModelDeployment(
                id="deploy-not-ready-decision",
                job_id="train-not-ready-decision",
                department="EC",
                model_family=f"{SKILL_ID}:qwen3.5-4b-qlora",
                artifact_id="adapter-not-ready-decision",
                artifact_ref_json={
                    "id": "adapter-not-ready-decision",
                    "sha256": "a" * 64,
                    "uri": "/tmp/adapter-not-ready-decision.tar.gz",
                },
                target_skill_ids_json=[SKILL_ID],
                status="active",
                rollout_percent=100,
                requested_by="admin",
                activated_at=now_bjt(),
            )
        )
        await session.commit()

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "model prompt")
    _patch_deployment_model_status(
        monkeypatch,
        ready_ids={"node-not-ready-decision"},
        statuses={"node-not-ready-decision": "not_downloaded"},
    )

    async def fail_inference(self, payload, *, timeout=300):
        raise AssertionError("not-ready deployment must not run training inference")

    async def fake_llm(**kwargs):
        skillforge = kwargs["context_pack"].get("skillforge") or {}
        assert skillforge.get("active_model_deployment") is None
        assert skillforge.get("deployed_model_inference") is None
        return LLMAnalyzeResult(
            output={"summary": "platform-fallback-for-not-ready-model"},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            raw_output='{"summary":"platform-fallback-for-not-ready-model"}',
        )

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_training_inference", fail_inference)
    monkeypatch.setattr(service_module, "_call_llm_json", fake_llm)

    analyze_resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id="run-model-not-ready-decision", cache_key="active-model-not-ready-cache"),
        headers={"X-Run-Token": _token(run_id="run-model-not-ready-decision")},
    )

    assert analyze_resp.status_code == 200, analyze_resp.text
    body = analyze_resp.json()
    assert body["active_model_deployment"] is None
    assert body["deployed_model_inference"] is None
    skipped = body["skipped_model_deployments"]
    assert skipped == body["analysis_delegate_route"]["skipped_model_deployments"]
    assert skipped[0]["id"] == "deploy-not-ready-decision"
    assert skipped[0]["skip_reason"] == "training_model_not_ready"
    assert skipped[0]["runtime_status"]["model_status"]["status"] == "not_downloaded"
    assert skipped[0]["runtime_status"]["inference_ready"] is False
    assert "artifact_uri" not in skipped[0]
    assert body["output"]["summary"] == "platform-fallback-for-not-ready-model"

    async with db_mod.async_session_factory() as session:
        analyze_run = (
            await session.execute(
                select(IntelligenceAnalyzeRun)
                .where(IntelligenceAnalyzeRun.run_id == "run-model-not-ready-decision")
                .order_by(IntelligenceAnalyzeRun.id.desc())
                .limit(1)
            )
        ).scalar_one()
        event = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "intelligence_analyze_run")
                .where(LearningEvent.source_id == str(analyze_run.id))
            )
        ).scalar_one()
        skipped_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "model_deployment")
                .where(LearningFlowEdge.from_id == "deploy-not-ready-decision")
                .where(LearningFlowEdge.to_type == "intelligence_analyze")
                .where(LearningFlowEdge.to_id == str(analyze_run.id))
                .where(LearningFlowEdge.relation == "skipped_analysis")
            )
        ).scalar_one()

    route_skipped = analyze_run.analysis_delegate_route["skipped_model_deployments"][0]
    assert route_skipped["skip_reason"] == "training_model_not_ready"
    assert "artifact_uri" not in route_skipped
    event_skipped = event.metadata_json["skipped_model_deployments"][0]
    assert event_skipped["model_deployment_id"] == "deploy-not-ready-decision"
    assert event_skipped["skip_reason"] == "training_model_not_ready"
    assert "artifact_uri" not in event_skipped
    assert skipped_edge.metadata_json["skip_reason"] == "training_model_not_ready"


@pytest.mark.asyncio
async def test_runtime_and_decision_paths_choose_same_rollout_model(client, monkeypatch):
    await _ensure_router(client)
    run_id = _run_id_for_rollout("deploy-canary-rollout-decision", selected=True, percent=10)
    expected_bucket = _deployment_rollout_bucket(SKILL_ID, run_id, "deploy-canary-rollout-decision")
    await _seed_skill_and_pricing(run_id=run_id)
    await _seed_canary_rollout_deployments(run_id)
    _patch_deployment_model_status(monkeypatch, ready_ids={"node-canary-decision"})

    from app.execution.model_context import active_model_context_for_skill
    from app.execution.models import OpenClawInstance
    from app.intelligence.service import _resolve_active_model_deployment

    async with db_mod.async_session_factory() as session:
        skill = await session.get(Skill, SKILL_ID)
        instance = await session.get(OpenClawInstance, "node-canary-decision")
        runtime_context = await active_model_context_for_skill(
            session,
            skill_id=SKILL_ID,
            skill_department="EC",
            target_instance=instance,
            run_id=run_id,
            include_artifact_uri=True,
        )
        decision_deployment = await _resolve_active_model_deployment(
            session,
            skill,
            run_id=run_id,
            skipped=[],
        )

    runtime_deployment = runtime_context["active_model_deployment"]
    assert runtime_deployment["model_deployment_id"] == "deploy-canary-rollout-decision"
    assert decision_deployment["id"] == runtime_deployment["model_deployment_id"]
    assert runtime_deployment["rollout_selected"] is True
    assert runtime_deployment["rollout_bucket"] == expected_bucket
    assert decision_deployment["rollout_bucket"] == expected_bucket
    assert runtime_deployment["rollout_reason"] == decision_deployment["rollout_reason"] == "canary_selected"
    assert runtime_deployment["artifact_uri"] == decision_deployment["artifact_uri"]
    assert runtime_deployment["artifact_sha256"] == decision_deployment["artifact_sha256"]


@pytest.mark.asyncio
async def test_canary_model_deployment_participates_in_decision_when_rollout_selected(client, monkeypatch):
    await _ensure_router(client)
    run_id = _run_id_for_rollout("deploy-canary-rollout-decision", selected=True, percent=10)
    expected_bucket = _deployment_rollout_bucket(SKILL_ID, run_id, "deploy-canary-rollout-decision")
    await _seed_skill_and_pricing(run_id=run_id)
    await _seed_canary_rollout_deployments(run_id)

    from app.skills.core.git_service import git_service
    import app.intelligence.service as service_module

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "model prompt")
    inference_calls = []

    async def fake_inference(self, payload, *, timeout=300):
        inference_calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "text": "canary adapter output",
            "text_sha256": "c" * 64,
            "metrics": {"generated_tokens": 13},
        }

    async def fake_llm(**kwargs):
        deployment = kwargs["context_pack"]["skillforge"]["active_model_deployment"]
        assert deployment["id"] == "deploy-canary-rollout-decision"
        assert deployment["status"] == "canary"
        assert deployment["rollout_percent"] == 10
        assert deployment["rollout_selected"] is True
        assert deployment["rollout_bucket"] == expected_bucket
        assert deployment["rollout_reason"] == "canary_selected"
        inference = kwargs["context_pack"]["skillforge"]["deployed_model_inference"]
        assert inference["model_deployment_id"] == "deploy-canary-rollout-decision"
        assert inference["text"] == "canary adapter output"
        return LLMAnalyzeResult(
            output={"summary": "canary-model-decision-ok"},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            raw_output='{"summary":"canary-model-decision-ok"}',
        )

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_training_inference", fake_inference)
    monkeypatch.setattr(service_module, "_call_llm_json", fake_llm)
    _patch_deployment_model_status(monkeypatch, ready_ids={"node-canary-decision"})

    analyze_resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id=run_id, cache_key="canary-model-decision-cache"),
        headers={"X-Run-Token": _token(run_id=run_id)},
    )

    assert analyze_resp.status_code == 200, analyze_resp.text
    body = analyze_resp.json()
    assert body["active_model_deployment"]["id"] == "deploy-canary-rollout-decision"
    assert body["active_model_deployment"]["status"] == "canary"
    assert body["active_model_deployment"]["rollout_selected"] is True
    assert body["active_model_deployment"]["rollout_bucket"] == expected_bucket
    assert body["active_model_deployment"]["runtime_status"]["inference_ready"] is True
    assert body["deployed_model_inference"]["status"] == "used"
    assert inference_calls[0]["payload"]["deployment_id"] == "deploy-canary-rollout-decision"
    assert inference_calls[0]["payload"]["artifact_uri"] == "/tmp/adapter-canary-rollout-decision.tar.gz"

    submit_resp = await client.post(
        "/api/executions/submit-result",
        json={
            "skill_id": SKILL_ID,
            "run_id": run_id,
            "output": {"summary": "灰度模型参与后的业务结论"},
            "params": {"date": "2026-06-17"},
            "triggered_by": "skill_sdk",
        },
        headers={"X-Run-Token": _token(run_id=run_id)},
    )
    assert submit_resp.status_code == 200, submit_resp.text
    model_context = submit_resp.json()["model_context"]
    assert model_context["model_deployment_id"] == "deploy-canary-rollout-decision"
    assert model_context["deployment_status"] == "canary"
    assert model_context["rollout_percent"] == 10
    assert model_context["rollout_selected"] is True
    assert model_context["rollout_bucket"] == expected_bucket
    assert model_context["rollout_reason"] == "canary_selected"
    assert model_context["inference_status"] == "used"

    async with db_mod.async_session_factory() as session:
        decision = (await session.execute(select(DecisionLog).where(DecisionLog.run_id == run_id))).scalar_one()
        run = await session.get(ExecutionRun, run_id)

    assert decision.model_id == "deploy-canary-rollout-decision"
    meta_context = decision.output_result["_skillforge_meta"]["model_context"]
    assert meta_context["model_deployment_id"] == "deploy-canary-rollout-decision"
    assert meta_context["rollout_bucket"] == expected_bucket
    assert run.metadata_json["model_context"]["deployment_status"] == "canary"
    assert run.metadata_json["model_context"]["rollout_reason"] == "canary_selected"


@pytest.mark.asyncio
async def test_canary_model_deployment_falls_back_to_active_when_rollout_not_selected(client, monkeypatch):
    await _ensure_router(client)
    run_id = _run_id_for_rollout("deploy-canary-rollout-decision", selected=False, percent=10)
    await _seed_skill_and_pricing(run_id=run_id)
    await _seed_canary_rollout_deployments(run_id)

    from app.skills.core.git_service import git_service
    import app.intelligence.service as service_module

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "model prompt")
    inference_calls = []

    async def fake_inference(self, payload, *, timeout=300):
        inference_calls.append(payload)
        return {"text": "active adapter output", "text_sha256": "b" * 64, "metrics": {"generated_tokens": 7}}

    async def fake_llm(**kwargs):
        deployment = kwargs["context_pack"]["skillforge"]["active_model_deployment"]
        assert deployment["id"] == "deploy-active-rollout-decision"
        assert deployment["status"] == "active"
        assert deployment["rollout_selected"] is True
        assert deployment["rollout_reason"] == "active"
        assert deployment["rollout_bucket"] is None
        return LLMAnalyzeResult(
            output={"summary": "active-fallback-decision-ok"},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            raw_output='{"summary":"active-fallback-decision-ok"}',
        )

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_training_inference", fake_inference)
    monkeypatch.setattr(service_module, "_call_llm_json", fake_llm)
    _patch_deployment_model_status(monkeypatch, ready_ids={"node-canary-decision"})

    analyze_resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id=run_id, cache_key="canary-model-fallback-cache"),
        headers={"X-Run-Token": _token(run_id=run_id)},
    )

    assert analyze_resp.status_code == 200, analyze_resp.text
    body = analyze_resp.json()
    assert body["active_model_deployment"]["id"] == "deploy-active-rollout-decision"
    assert body["active_model_deployment"]["status"] == "active"
    assert body["active_model_deployment"]["rollout_selected"] is True
    assert body["active_model_deployment"]["rollout_reason"] == "active"
    assert inference_calls[0]["deployment_id"] == "deploy-active-rollout-decision"


@pytest.mark.asyncio
async def test_unready_canary_model_deployment_falls_back_to_active_decision_model(client, monkeypatch):
    await _ensure_router(client)
    run_id = _run_id_for_rollout("deploy-canary-rollout-decision", selected=True, percent=10)
    await _seed_skill_and_pricing(run_id=run_id)
    await _seed_canary_rollout_deployments(run_id)

    async with db_mod.async_session_factory() as session:
        canary = await session.get(TrainingModelDeployment, "deploy-canary-rollout-decision")
        canary.artifact_ref_json = {
            "id": "adapter-canary-rollout-decision",
            "sha256": "c" * 64,
        }
        await session.commit()

    from app.skills.core.git_service import git_service
    import app.intelligence.service as service_module

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "model prompt")
    inference_calls = []

    async def fake_inference(self, payload, *, timeout=300):
        inference_calls.append(payload)
        assert payload["deployment_id"] == "deploy-active-rollout-decision"
        assert payload["artifact_uri"] == "/tmp/adapter-active-rollout-decision.tar.gz"
        return {"text": "active adapter output", "text_sha256": "b" * 64, "metrics": {"generated_tokens": 7}}

    async def fake_llm(**kwargs):
        deployment = kwargs["context_pack"]["skillforge"]["active_model_deployment"]
        assert deployment["id"] == "deploy-active-rollout-decision"
        assert deployment["status"] == "active"
        assert deployment["runtime_status"]["inference_ready"] is True
        inference = kwargs["context_pack"]["skillforge"]["deployed_model_inference"]
        assert inference["model_deployment_id"] == "deploy-active-rollout-decision"
        return LLMAnalyzeResult(
            output={"summary": "active-fallback-for-unready-canary-ok"},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            raw_output='{"summary":"active-fallback-for-unready-canary-ok"}',
        )

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_training_inference", fake_inference)
    monkeypatch.setattr(service_module, "_call_llm_json", fake_llm)
    _patch_deployment_model_status(monkeypatch, ready_ids={"node-canary-decision"})

    analyze_resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id=run_id, cache_key="canary-model-unready-fallback-cache"),
        headers={"X-Run-Token": _token(run_id=run_id)},
    )

    assert analyze_resp.status_code == 200, analyze_resp.text
    body = analyze_resp.json()
    assert body["active_model_deployment"]["id"] == "deploy-active-rollout-decision"
    assert body["active_model_deployment"]["status"] == "active"
    assert body["active_model_deployment"]["rollout_selected"] is True
    assert body["active_model_deployment"]["rollout_reason"] == "active"
    assert body["active_model_deployment"]["runtime_status"]["inference_ready"] is True
    assert body["deployed_model_inference"]["status"] == "used"
    assert inference_calls[0]["deployment_id"] == "deploy-active-rollout-decision"


@pytest.mark.asyncio
async def test_active_model_deployment_can_target_skill_across_department(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing(run_id="run-cross-dept-model-decision")

    from app.skills.core.git_service import git_service
    from app.execution.models import OpenClawInstance
    import app.intelligence.service as service_module

    async with db_mod.async_session_factory() as session:
        session.add(
            OpenClawInstance(
                id="node-1",
                name="EC inference node",
                department="EC",
                gateway_url="ws://127.0.0.1:18789",
                reload_hook_url="http://127.0.0.1:9000/reload",
                reload_token="",
                is_active=True,
                bridge_gateway_kind="bridge",
                bridge_capabilities_json=json.dumps({"ops": ["training.inference"]}),
            )
        )
        session.add(
            ExecutionRun(
                id="run-cross-dept-model-decision",
                skill_id=SKILL_ID,
                run_mode="manual_real",
                trigger_type="manual",
                status="running",
                metadata_json={},
            )
        )
        session.add(
            TrainingJob(
                id="train-cross-dept-decision",
                title="cross dept model",
                department="Sales-2",
                created_by="admin",
                status="completed",
                job_type="qlora",
                target_skill_id=SKILL_ID,
                target_gateway_id="node-1",
            )
        )
        session.add(
            TrainingModelDeployment(
                id="deploy-cross-dept-decision",
                job_id="train-cross-dept-decision",
                department="Sales-2",
                model_family=f"{SKILL_ID}:learning_loop_improvement",
                artifact_id="adapter-cross-dept-decision",
                artifact_ref_json={
                    "id": "adapter-cross-dept-decision",
                    "sha256": "b" * 64,
                    "uri": "/tmp/adapter-cross-dept-decision.tar.gz",
                },
                target_skill_ids_json=[SKILL_ID],
                status="active",
                rollout_percent=100,
                requested_by="admin",
                activated_at=now_bjt(),
            )
        )
        await session.commit()

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "model prompt")
    inference_calls = []

    async def fake_inference(self, payload, *, timeout=300):
        inference_calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "text": "adapter says focus on hook and audience coverage",
            "text_sha256": "c" * 64,
            "metrics": {"generated_tokens": 9, "cuda_available": True},
        }

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_training_inference", fake_inference)
    _patch_deployment_model_status(monkeypatch, ready_ids={"node-1"})

    async def fake_llm(**kwargs):
        deployment = kwargs["context_pack"]["skillforge"]["active_model_deployment"]
        assert deployment["id"] == "deploy-cross-dept-decision"
        assert deployment["department"] == "Sales-2"
        inference = kwargs["context_pack"]["skillforge"]["deployed_model_inference"]
        assert inference["text"] == "adapter says focus on hook and audience coverage"
        return LLMAnalyzeResult(
            output={"summary": "cross-dept-model-decision-ok"},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            raw_output='{"summary":"cross-dept-model-decision-ok"}',
        )

    monkeypatch.setattr(service_module, "_call_llm_json", fake_llm)

    analyze_resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(
            run_id="run-cross-dept-model-decision",
            cache_key="cross-dept-active-model-decision-cache",
        ),
        headers={"X-Run-Token": _token(run_id="run-cross-dept-model-decision")},
    )

    assert analyze_resp.status_code == 200, analyze_resp.text
    body = analyze_resp.json()
    assert body["active_model_deployment"]["id"] == "deploy-cross-dept-decision"
    assert body["analysis_delegate_route"]["active_model_deployment"]["department"] == "Sales-2"
    assert body["analysis_delegate_route"]["deployed_model_inference"]["status"] == "used"
    assert body["analysis_delegate_route"]["deployed_model_inference"]["text_sha256"] == "c" * 64
    assert inference_calls[0]["instance_id"] == "node-1"
    assert inference_calls[0]["payload"]["artifact_uri"] == "/tmp/adapter-cross-dept-decision.tar.gz"


@pytest.mark.asyncio
async def test_submitted_decision_model_context_includes_adapter_inference_metadata(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing(run_id="run-adapter-inference-model-context")

    from app.skills.core.git_service import git_service
    from app.execution.models import OpenClawInstance
    import app.intelligence.service as service_module

    async with db_mod.async_session_factory() as session:
        session.add(
            OpenClawInstance(
                id="node-1",
                name="EC inference node",
                department="EC",
                gateway_url="ws://127.0.0.1:18789",
                reload_hook_url="http://127.0.0.1:9000/reload",
                reload_token="",
                is_active=True,
                bridge_gateway_kind="bridge",
                bridge_capabilities_json=json.dumps({"ops": ["training.inference"]}),
            )
        )
        session.add(
            TrainingJob(
                id="train-adapter-inference",
                title="adapter inference model",
                department="EC",
                created_by="admin",
                status="completed",
                job_type="qlora",
                target_skill_id=SKILL_ID,
                target_gateway_id="node-1",
            )
        )
        session.add(
            ExecutionRun(
                id="run-adapter-inference-model-context",
                skill_id=SKILL_ID,
                run_mode="manual_real",
                trigger_type="manual",
                status="running",
                metadata_json={},
            )
        )
        session.add(
            TrainingModelDeployment(
                id="deploy-adapter-inference",
                job_id="train-adapter-inference",
                department="EC",
                model_family=f"{SKILL_ID}:qwen3.5-4b-qlora",
                artifact_id="adapter-inference",
                artifact_ref_json={
                    "id": "adapter-inference",
                    "sha256": "d" * 64,
                    "uri": "/tmp/adapter-inference.tar.gz",
                },
                target_skill_ids_json=[SKILL_ID],
                status="active",
                rollout_percent=100,
                requested_by="admin",
                activated_at=now_bjt(),
            )
        )
        await session.commit()

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "model prompt")

    async def fake_inference(self, payload, *, timeout=300):
        return {
            "text": "adapter output",
            "text_sha256": "e" * 64,
            "metrics": {"generated_tokens": 11, "cuda_available": True, "cuda_device": "RTX"},
        }

    async def fake_llm(**kwargs):
        return LLMAnalyzeResult(
            output={"summary": "adapter inference context ok"},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            raw_output='{"summary":"adapter inference context ok"}',
        )

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_training_inference", fake_inference)
    monkeypatch.setattr(service_module, "_call_llm_json", fake_llm)
    _patch_deployment_model_status(monkeypatch, ready_ids={"node-1"})

    analyze_resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(
            run_id="run-adapter-inference-model-context",
            cache_key="adapter-inference-model-context-cache",
        ),
        headers={"X-Run-Token": _token(run_id="run-adapter-inference-model-context")},
    )
    assert analyze_resp.status_code == 200, analyze_resp.text

    submit_resp = await client.post(
        "/api/executions/submit-result",
        json={
            "skill_id": SKILL_ID,
            "run_id": "run-adapter-inference-model-context",
            "output": {"summary": "adapter inference decision"},
            "params": {"date": "2026-06-17"},
            "triggered_by": "skill_sdk",
        },
        headers={"X-Run-Token": _token(run_id="run-adapter-inference-model-context")},
    )
    assert submit_resp.status_code == 200, submit_resp.text
    model_context = submit_resp.json()["model_context"]
    assert model_context["model_deployment_id"] == "deploy-adapter-inference"
    assert model_context["inference_status"] == "used"
    assert model_context["inference_backend"] == "bridge_local_lora_inference"
    assert model_context["inference_gateway_id"] == "node-1"
    assert model_context["inference_text_sha256"] == "e" * 64
    assert model_context["inference_metrics"]["generated_tokens"] == 11


@pytest.mark.asyncio
async def test_analysis_agent_blueprint_can_select_flash_model_profile(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing(run_id="run-agent-flash")

    from app.agents.models import DepartmentAnalysisAgent
    from app.execution.models import ExecutionRun
    from app.skills.core.git_service import git_service
    import app.intelligence.service as service_module

    run_id = "run-agent-flash"
    agent_id = "tmall-weekly-insight-agent"
    async with db_mod.async_session_factory() as session:
        session.add(
            SystemConfig(
                key="ai.pricing.deepseek-v4-flash",
                value={"input": 0.1, "output": 0.2, "cache_read": 0, "cache_write": 0},
            )
        )
        session.add(
            ExecutionRun(
                id=run_id,
                skill_id=SKILL_ID,
                run_mode="manual_real",
                trigger_type="manual",
                status="running",
                metadata_json={"analysis_agent_control": {"id": agent_id}},
            )
        )
        session.add(
            DepartmentAnalysisAgent(
                id=agent_id,
                name="天猫全店洞察 Agent",
                department="EC",
                skill_id=SKILL_ID,
                prompt_version="analysis_v1",
                default_params_json={"model_profile": "deepseek-v4-flash"},
                status="active",
                is_active=True,
            )
        )
        await session.commit()

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "flash prompt")
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return LLMAnalyzeResult(
            output={"summary": "flash-ok"},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            raw_output='{"summary":"flash-ok"}',
        )

    monkeypatch.setattr(service_module, "_call_llm_json", fake_llm)

    resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id=run_id, cache_key="agent-flash-cache"),
        headers={"X-Run-Token": _token(run_id=run_id)},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model"] == "deepseek-v4-flash"
    assert calls[0]["model"] == "deepseek-v4-flash"
    assert body["analysis_delegate_route"]["analysis_agent_model"]["id"] == agent_id
    assert body["analysis_delegate_route"]["analysis_agent_model"]["model_profile_resolved"] is True

    async with db_mod.async_session_factory() as session:
        row = (
            await session.execute(
                select(IntelligenceAnalyzeRun).where(IntelligenceAnalyzeRun.run_id == run_id)
            )
        ).scalar_one()
        assert row.model == "deepseek-v4-flash"
        assert row.analysis_delegate_route["analysis_agent_model"]["model"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_analyze_auto_routes_to_department_analysis_agent(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing()

    from app.execution.models import OpenClawInstance
    from app.skills.core.git_service import git_service
    import app.intelligence.service as service_module

    async with db_mod.async_session_factory() as session:
        session.add_all(
            [
                OpenClawInstance(
                    id="node-1",
                    name="EC 执行 Agent",
                    department="EC",
                    gateway_url="ws://127.0.0.1:18780",
                    reload_hook_url="http://127.0.0.1:9000/reload",
                    reload_token="",
                    is_active=True,
                    agent_purpose="skill_runtime",
                    bridge_gateway_kind="openclaw",
                    bridge_capabilities_json=json.dumps({"ops": ["run_agent_skill"]}),
                ),
                OpenClawInstance(
                    id="analysis-node",
                    name="EC 分析 Agent",
                    department="EC",
                    gateway_url="ws://127.0.0.1:18781",
                    reload_hook_url="http://127.0.0.1:9001/reload",
                    reload_token="",
                    is_active=True,
                    agent_purpose="analysis",
                    bridge_gateway_kind="openclaw",
                    bridge_capabilities_json=json.dumps({"ops": ["intelligence.analyze"]}),
                ),
            ]
        )
        await session.commit()

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "agent prompt")

    async def fail_platform_llm(**kwargs):
        raise AssertionError("platform LLM should not be called when department Agent can analyze")

    async def fake_agent_analyze(self, payload, *, timeout=120):
        assert self.instance_id == "analysis-node"
        assert payload["instance_id"] == "node-1"
        assert payload["analysis_delegate_route"]["mode"] == "auto"
        assert payload["analysis_delegate_route"]["scope"] == "department"
        assert payload["analysis_delegate_route"]["agent_id"] == "analysis-node"
        return {
            "output": {"summary": "department-agent-ok"},
            "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
        }

    monkeypatch.setattr(service_module, "_call_llm_json", fail_platform_llm)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_intelligence_analyze", fake_agent_analyze)

    resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id="run-dept-agent", cache_key="dept-agent-cache"),
        headers={"X-Run-Token": _token(run_id="run-dept-agent")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["output"] == {"summary": "department-agent-ok"}
    assert body["analysis_backend"] == "agent"
    assert body["analysis_agent_id"] == "analysis-node"
    assert body["analysis_delegate_route"]["mode"] == "auto"
    assert body["analysis_delegate_route"]["scope"] == "department"
    assert body["model"] == "agent:analysis-node"
    assert body["cost_usd"] == 0.0

    async with db_mod.async_session_factory() as session:
        run_rows = (await session.execute(select(IntelligenceAnalyzeRun))).scalars().all()
        analyze_run = run_rows[0]
        analyze_event = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "intelligence_analyze_run")
                .where(LearningEvent.source_id == str(analyze_run.id))
            )
        ).scalar_one()
        controlled_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "agent")
                .where(LearningFlowEdge.from_id == "analysis-node")
                .where(LearningFlowEdge.to_type == "intelligence_analyze")
                .where(LearningFlowEdge.to_id == str(analyze_run.id))
                .where(LearningFlowEdge.relation == "controlled_analysis")
            )
        ).scalar_one()
        handled_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "agent")
                .where(LearningFlowEdge.from_id == "analysis-node")
                .where(LearningFlowEdge.to_type == "intelligence_analyze")
                .where(LearningFlowEdge.to_id == str(analyze_run.id))
                .where(LearningFlowEdge.relation == "handled_analysis")
            )
        ).scalar_one()

    assert analyze_run.analysis_backend == "agent"
    assert analyze_run.analysis_agent_id == "analysis-node"
    assert analyze_run.analysis_delegate_route["mode"] == "auto"
    assert analyze_run.analysis_delegate_route["scope"] == "department"
    assert analyze_event.metadata_json["analysis_agent"]["agent_id"] == "analysis-node"
    assert analyze_event.metadata_json["analysis_agent"]["contract"]["complete"] is True
    assert analyze_event.metadata_json["analysis_agent"]["contract"]["control_ops"] == ["intelligence.analyze"]
    assert analyze_event.metadata_json["analysis_agent"]["contract"]["input_channels"] == [
        "intelligence_context_pack",
        "model_deployment_context",
    ]
    assert analyze_event.metadata_json["analysis_agent"]["contract"]["output_channels"] == [
        "analysis_result",
        "decision_model_context",
        "learning_event",
    ]
    assert controlled_edge.metadata_json["contract"]["flow_traceable"] is True
    assert controlled_edge.metadata_json["analysis_delegate_route"]["scope"] == "department"
    assert handled_edge.metadata_json["contract"]["complete"] is True

    journeys = (
        await client.get(
            "/api/learning/flow-journeys",
            params={"days": 7, "skill_id": SKILL_ID, "source_type": "intelligence_analyze_run"},
        )
    ).json()
    analyze_journey = next(
        item
        for item in journeys["items"]
        if item["source_id"] == str(analyze_run.id)
    )
    control_step = next(step for step in analyze_journey["steps"] if step["action"] == "controlled_analysis")
    assert control_step["stage"] == "control"
    assert control_step["payload"]["agent_id"] == "analysis-node"
    assert control_step["payload"]["contract"]["control_ops"] == ["intelligence.analyze"]


@pytest.mark.asyncio
async def test_analyze_accepts_agent_usage_redacted_by_old_bridge(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing()

    from app.execution.models import OpenClawInstance
    from app.skills.core.git_service import git_service
    import app.intelligence.service as service_module

    async with db_mod.async_session_factory() as session:
        session.add(
            OpenClawInstance(
                id="node-1",
                name="EC 分析 Agent",
                department="EC",
                gateway_url="ws://127.0.0.1:18789",
                reload_hook_url="http://127.0.0.1:9000/reload",
                reload_token="",
                is_active=True,
                agent_purpose="analysis",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({"ops": ["intelligence.analyze"]}),
            )
        )
        await session.commit()

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "agent prompt")

    async def fail_platform_llm(**kwargs):
        raise AssertionError("platform LLM should not be called when Agent output is valid")

    async def fake_agent_analyze(self, payload, *, timeout=120):
        return {
            "output": {"summary": "agent-ok-redacted-usage"},
            "usage": {
                "prompt_tokens": "[REDACTED]",
                "completion_tokens": "[REDACTED]",
                "total_tokens": "[REDACTED]",
            },
        }

    monkeypatch.setattr(service_module, "_call_llm_json", fail_platform_llm)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_intelligence_analyze", fake_agent_analyze)

    resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id="run-agent-redacted-usage", cache_key="agent-redacted-usage-cache"),
        headers={"X-Run-Token": _token(run_id="run-agent-redacted-usage")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["analysis_backend"] == "agent"
    assert body["output"] == {"summary": "agent-ok-redacted-usage"}
    assert body["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    async with db_mod.async_session_factory() as session:
        run_row = (
            await session.execute(
                select(IntelligenceAnalyzeRun).where(IntelligenceAnalyzeRun.run_id == "run-agent-redacted-usage")
            )
        ).scalar_one()

    assert run_row.analysis_backend == "agent"
    assert run_row.degraded is False


@pytest.mark.asyncio
async def test_analyze_routes_to_active_model_gateway_when_department_agent_missing(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing(run_id="run-model-gateway-agent")

    from app.execution.models import OpenClawInstance
    from app.skills.core.git_service import git_service
    import app.intelligence.service as service_module

    async with db_mod.async_session_factory() as session:
        session.add_all(
            [
                OpenClawInstance(
                    id="node-1",
                    name="EC 执行 Agent",
                    department="EC",
                    gateway_url="ws://127.0.0.1:18780",
                    reload_hook_url="http://127.0.0.1:9000/reload",
                    reload_token="",
                    is_active=True,
                    agent_purpose="skill_runtime",
                    bridge_gateway_kind="openclaw",
                    bridge_capabilities_json=json.dumps({"ops": ["run_agent_skill"]}),
                ),
                OpenClawInstance(
                    id="model-gateway",
                    name="模型训练分析 Agent",
                    department="Sales-2",
                    gateway_url="ws://127.0.0.1:18783",
                    reload_hook_url="http://127.0.0.1:9003/reload",
                    reload_token="",
                    is_active=True,
                    agent_purpose="mixed",
                    bridge_gateway_kind="openclaw",
                    bridge_capabilities_json=json.dumps(
                        {"ops": ["training.inference", "intelligence.analyze"]}
                    ),
                ),
            ]
        )
        session.add(
            TrainingJob(
                id="train-model-gateway-agent",
                title="model gateway agent",
                department="Sales-2",
                created_by="admin",
                status="completed",
                job_type="qlora",
                target_skill_id=SKILL_ID,
                target_gateway_id="model-gateway",
            )
        )
        session.add(
            TrainingModelDeployment(
                id="deploy-model-gateway-agent",
                job_id="train-model-gateway-agent",
                department="Sales-2",
                model_family=f"{SKILL_ID}:qwen3.5-4b-qlora",
                artifact_id="adapter-model-gateway-agent",
                artifact_ref_json={
                    "id": "adapter-model-gateway-agent",
                    "sha256": "f" * 64,
                    "uri": "/tmp/adapter-model-gateway-agent.tar.gz",
                },
                target_skill_ids_json=[SKILL_ID],
                status="active",
                rollout_percent=100,
                requested_by="admin",
                activated_at=now_bjt(),
            )
        )
        await session.commit()

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "agent prompt")

    async def fake_inference(self, payload, *, timeout=300):
        assert self.instance_id == "model-gateway"
        return {"text": "adapter says compare high spend", "text_sha256": "f" * 64, "metrics": {}}

    async def fake_agent_analyze(self, payload, *, timeout=120):
        assert self.instance_id == "model-gateway"
        route = payload["analysis_delegate_route"]
        assert route["mode"] == "active_model_deployment_gateway"
        assert route["scope"] == "model_deployment_gateway"
        assert route["preferred_by"] == "active_model_deployment"
        return {
            "output": {"summary": "model-gateway-agent-ok"},
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        }

    async def fail_platform_llm(**kwargs):
        raise AssertionError("platform LLM should not be called when model gateway Agent can analyze")

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_training_inference", fake_inference)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_intelligence_analyze", fake_agent_analyze)
    monkeypatch.setattr(service_module, "_call_llm_json", fail_platform_llm)
    _patch_deployment_model_status(monkeypatch, ready_ids={"model-gateway"})

    resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id="run-model-gateway-agent", cache_key="model-gateway-agent-cache"),
        headers={"X-Run-Token": _token(run_id="run-model-gateway-agent")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["analysis_backend"] == "agent"
    assert body["analysis_agent_id"] == "model-gateway"
    assert body["analysis_delegate_route"]["scope"] == "model_deployment_gateway"
    assert body["analysis_delegate_route"]["active_model_deployment"]["id"] == "deploy-model-gateway-agent"


@pytest.mark.asyncio
async def test_analyze_auto_routes_to_platform_analysis_agent_fallback(client, monkeypatch):
    await _ensure_router(client)
    await _seed_skill_and_pricing()

    from app.execution.models import OpenClawInstance
    from app.skills.core.git_service import git_service
    import app.intelligence.service as service_module

    async with db_mod.async_session_factory() as session:
        session.add_all(
            [
                OpenClawInstance(
                    id="node-1",
                    name="EC 执行 Agent",
                    department="EC",
                    gateway_url="ws://127.0.0.1:18780",
                    reload_hook_url="http://127.0.0.1:9000/reload",
                    reload_token="",
                    is_active=True,
                    agent_purpose="skill_runtime",
                    bridge_gateway_kind="openclaw",
                    bridge_capabilities_json=json.dumps({"ops": ["run_agent_skill"]}),
                ),
                OpenClawInstance(
                    id="platform-analysis",
                    name="平台分析 Agent",
                    department="AI",
                    gateway_url="ws://127.0.0.1:18782",
                    reload_hook_url="http://127.0.0.1:9002/reload",
                    reload_token="",
                    is_active=True,
                    is_platform_default=True,
                    agent_purpose="analysis",
                    bridge_gateway_kind="openclaw",
                    bridge_capabilities_json=json.dumps({"ops": ["intelligence.analyze"]}),
                ),
            ]
        )
        await session.commit()

    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "agent prompt")

    async def fail_platform_llm(**kwargs):
        raise AssertionError("platform LLM should not be called when platform Agent can analyze")

    async def fake_agent_analyze(self, payload, *, timeout=120):
        assert self.instance_id == "platform-analysis"
        route = payload["analysis_delegate_route"]
        assert route["mode"] == "auto"
        assert route["scope"] == "platform_fallback"
        assert route["platform_fallback"] is True
        return {
            "output": {"summary": "platform-agent-ok"},
            "usage": {"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 14},
        }

    monkeypatch.setattr(service_module, "_call_llm_json", fail_platform_llm)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_intelligence_analyze", fake_agent_analyze)

    resp = await client.post(
        "/api/intelligence/analyze",
        json=_body(run_id="run-platform-agent", cache_key="platform-agent-cache"),
        headers={"X-Run-Token": _token(run_id="run-platform-agent")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["output"] == {"summary": "platform-agent-ok"}
    assert body["analysis_backend"] == "agent"
    assert body["analysis_agent_id"] == "platform-analysis"
    assert body["analysis_delegate_route"]["scope"] == "platform_fallback"
    assert body["analysis_delegate_route"]["platform_fallback"] is True
    assert body["model"] == "agent:platform-analysis"
    assert body["cost_usd"] == 0.0

    async with db_mod.async_session_factory() as session:
        run_rows = (await session.execute(select(IntelligenceAnalyzeRun))).scalars().all()

    assert run_rows[0].analysis_backend == "agent"
    assert run_rows[0].analysis_agent_id == "platform-analysis"
    assert run_rows[0].analysis_delegate_route["scope"] == "platform_fallback"


def test_sdk_analyze_strips_deprecated_kwargs(monkeypatch):
    from app.skill_runtime_sdk.skillforge_sdk import SkillForge

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output": {"ok": True}}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setenv("SKILLFORGE_PLATFORM_URL", "http://skillforge.test")
    monkeypatch.setenv("SKILLFORGE_INTELLIGENCE_RUN_TOKEN", "analysis-token")
    monkeypatch.setenv("SKILLFORGE_RUN_TOKEN", "token-1")
    monkeypatch.setenv("SKILLFORGE_RUN_ID", "run-1")
    monkeypatch.setenv("SKILLFORGE_INSTANCE_ID", "node-1")
    monkeypatch.setenv("SKILLFORGE_SKILL_GIT_COMMIT_FULL", COMMIT)
    monkeypatch.setattr("app.skill_runtime_sdk.skillforge_sdk.requests.post", fake_post)

    sf = SkillForge(SKILL_ID)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = sf.analyze(context_pack={"facts": {}}, model="ignored", max_tokens=1)

    assert result == {"output": {"ok": True}}
    assert len(caught) == 2
    assert captured["url"] == "http://skillforge.test/api/intelligence/analyze"
    assert captured["headers"]["X-Run-Token"] == "analysis-token"
    assert "model" not in captured["json"]
    assert "max_tokens" not in captured["json"]
    assert captured["json"]["skill_git_commit_full"] == COMMIT


def test_sdk_analyze_and_submit_carry_runtime_model_context(monkeypatch):
    from app.skill_runtime_sdk.skillforge_sdk import SkillForge

    captured: list[dict] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if url.endswith("/api/intelligence/analyze"):
            return FakeResponse({
                "output": {"summary": "analysis ok"},
                "active_model_deployment": {
                    "id": "deploy-sdk-runtime",
                    "model_family": "lora",
                    "status": "active",
                    "rollout_percent": 100,
                    "artifact_id": "artifact-sdk-runtime",
                    "artifact_sha256": "d" * 64,
                    "target_skill_ids": [SKILL_ID],
                    "runtime_status": {"inference_ready": True},
                },
                "deployed_model_inference": {
                    "status": "used",
                    "gateway_id": "node-sdk-runtime",
                    "backend": "bridge_local_lora_inference",
                    "profile": "qwen3.5-4b",
                    "text_sha256": "e" * 64,
                    "metrics": {"generated_tokens": 8},
                },
            })
        return FakeResponse({"ok": True})

    runtime_context = {
        "model_deployment_id": "deploy-runtime-input",
        "active_model_deployment": {
            "model_deployment_id": "deploy-runtime-input",
            "model_family": "lora",
            "deployment_status": "active",
        },
        "control": {"agent_contract": "skill_runtime_model_context.v1"},
    }
    monkeypatch.setenv("SKILLFORGE_PLATFORM_URL", "http://skillforge.test")
    monkeypatch.setenv("SKILLFORGE_BASE_URL", "http://skillforge.test")
    monkeypatch.setenv("BROWSER_API_TOKEN", "submit-token")
    monkeypatch.setenv("SKILLFORGE_RUN_TOKEN", "run-token-1")
    monkeypatch.setenv("SKILLFORGE_RUN_ID", "run-sdk-model")
    monkeypatch.setenv("SKILLFORGE_INSTANCE_ID", "node-sdk-runtime")
    monkeypatch.setenv("SKILLFORGE_SKILL_GIT_COMMIT_FULL", COMMIT)
    monkeypatch.setenv("SKILLFORGE_MODEL_CONTEXT_JSON", json.dumps(runtime_context))
    monkeypatch.setattr("app.skill_runtime_sdk.skillforge_sdk.requests.post", fake_post)

    sf = SkillForge(SKILL_ID)
    assert sf.model_context()["model_deployment_id"] == "deploy-runtime-input"

    analyze_result = sf.analyze(context_pack={"facts": {"gmv": 12}})
    assert analyze_result["output"]["summary"] == "analysis ok"
    analyze_payload = captured[0]["json"]
    assert analyze_payload["context_pack"]["skillforge"]["runtime_model_context"]["model_deployment_id"] == "deploy-runtime-input"
    assert analyze_payload["context_pack"]["skillforge"]["active_model_deployment"]["model_deployment_id"] == "deploy-runtime-input"
    assert sf.model_context()["model_deployment_id"] == "deploy-sdk-runtime"

    submit_result = sf.submit(
        output={"summary": "final output"},
        params={"date": "2026-06-18"},
        run_id="run-sdk-model",
    )

    assert submit_result == {"ok": True}
    submit_payload = captured[1]["json"]
    model_context = submit_payload["output"]["_skillforge_meta"]["model_context"]
    assert model_context["model_deployment_id"] == "deploy-sdk-runtime"
    assert model_context["inference_status"] == "used"
    assert model_context["inference_text_sha256"] == "e" * 64
    assert submit_payload["params"]["model_context"]["model_deployment_id"] == "deploy-sdk-runtime"
    assert submit_payload["output"]["_skillforge_meta"]["active_model_deployment"]["artifact_sha256"] == "d" * 64


def test_sdk_analyze_omits_platform_pseudo_instance(monkeypatch):
    from app.skill_runtime_sdk.skillforge_sdk import SkillForge

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output": {"ok": True}}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setenv("SKILLFORGE_PLATFORM_URL", "http://skillforge.test")
    monkeypatch.setenv("SKILLFORGE_RUN_TOKEN", "token-1")
    monkeypatch.setenv("SKILLFORGE_RUN_ID", "run-1")
    monkeypatch.setenv("SKILLFORGE_INSTANCE_ID", "platform")
    monkeypatch.setenv("SKILLFORGE_SKILL_GIT_COMMIT_FULL", COMMIT)
    monkeypatch.setattr("app.skill_runtime_sdk.skillforge_sdk.requests.post", fake_post)

    sf = SkillForge(SKILL_ID)
    result = sf.analyze(context_pack={"facts": {}})

    assert result == {"output": {"ok": True}}
    assert captured["json"]["instance_id"] is None


def test_sdk_analyze_uses_skillforge_yaml_base_commit_when_env_missing(monkeypatch, tmp_path):
    from app.skill_runtime_sdk.skillforge_sdk import SkillForge

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output": {"ok": True}}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.chdir(tmp_path)
    (tmp_path / "skillforge.yaml").write_text(
        f"skill_id: {SKILL_ID}\nbase_commit: {COMMIT}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SKILLFORGE_PLATFORM_URL", "http://skillforge.test")
    monkeypatch.delenv("SKILLFORGE_SKILL_GIT_COMMIT_FULL", raising=False)
    monkeypatch.delenv("SKILL_GIT_COMMIT_FULL", raising=False)
    monkeypatch.delenv("OPENCLAW_SKILL_GIT_COMMIT_FULL", raising=False)
    monkeypatch.setattr("app.skill_runtime_sdk.skillforge_sdk.requests.post", fake_post)

    sf = SkillForge(SKILL_ID)
    result = sf.analyze(context_pack={"facts": {}})

    assert result == {"output": {"ok": True}}
    assert captured["json"]["skill_git_commit_full"] == COMMIT


def test_sdk_analyze_includes_platform_error_body(monkeypatch):
    from app.skill_runtime_sdk.skillforge_sdk import SkillForge

    class FakeResponse:
        text = '{"error":{"code":"PARAM_INVALID","message":"参数无效","detail":{"field":"skill_git_commit_full"}}}'

        def raise_for_status(self):
            raise RuntimeError("400 Client Error")

        def json(self):
            return {
                "error": {
                    "code": "PARAM_INVALID",
                    "message": "参数无效",
                    "detail": {"field": "skill_git_commit_full"},
                }
            }

    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeResponse()

    monkeypatch.setenv("SKILLFORGE_PLATFORM_URL", "http://skillforge.test")
    monkeypatch.setenv("SKILLFORGE_RUN_TOKEN", "token-1")
    monkeypatch.setenv("SKILLFORGE_RUN_ID", "run-1")
    monkeypatch.setattr("app.skill_runtime_sdk.skillforge_sdk.requests.post", fake_post)

    sf = SkillForge(SKILL_ID)
    with pytest.raises(RuntimeError) as exc:
        sf.analyze(context_pack={"facts": {}})

    message = str(exc.value)
    assert "PARAM_INVALID" in message
    assert "skill_git_commit_full" in message
