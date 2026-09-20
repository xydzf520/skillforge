import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.common.time_utils import now_bjt
from app.execution.models import DecisionLog, ExecutionRun, OpenClawInstance
from app.learning.models import LearningArtifact, LearningEvent, LearningIngestionJob
from app.projects.models import Project, ProjectCapabilityCall, ProjectIngressEvent, ProjectRun, ProjectRunAsset, ProjectSdkToken


PROJECT_EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "docs" / "examples" / "projects"


def test_project_run_assets_use_persistent_skill_workspace():
    from app.projects import service as project_service

    assert project_service.PROJECT_RUN_ASSETS_DIR.parent == project_service.PROJECT_ASSETS_DIR.parent
    assert project_service.PROJECT_RUN_ASSETS_DIR.name == "project-run-assets"


def test_project_run_asset_response_is_private_and_cacheable_by_hash():
    from app.projects import service as project_service

    headers = project_service.project_run_asset_response_headers(
        SimpleNamespace(sha256="a" * 64)
    )

    assert headers["Cache-Control"] == "private, max-age=43200, immutable"
    assert headers["ETag"] == f'"sha256-{"a" * 64}"'
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_department_project_is_visible_to_descendant_team_members(client):
    """A department project is readable by people assigned to child teams only."""

    from app.auth.models import User
    from app.org.models import OrgUnit, UserOrgMembership
    from app.projects import service as project_service
    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        db.add_all(
            [
                OrgUnit(id="project-dept-root", name="示例品牌内容电商运营部", path="/project-dept-root"),
                OrgUnit(
                    id="project-dept-child",
                    name="示例品牌短视频组",
                    parent_id="project-dept-root",
                    path="/project-dept-root/project-dept-child",
                ),
                OrgUnit(id="project-dept-other", name="其他业务部", path="/project-dept-other"),
            ]
        )
        child_user = User(
            id="project-child-user",
            username="project-child-user",
            name="下级团队成员",
            role="operator",
            department="示例品牌短视频组",
            state="active",
            is_active=True,
            can_view_all=False,
            must_change_password=False,
        )
        db.add(child_user)
        await db.flush()
        db.add(
            UserOrgMembership(
                user_id=child_user.id,
                org_unit_id="project-dept-child",
                membership_type="primary",
            )
        )
        db.add_all(
            [
                Project(
                    id="department-parent-project",
                    name="父部门素材工作台",
                    type="internal_tool",
                    department_id="project-dept-root",
                    department="示例品牌内容电商运营部",
                    visibility="department",
                    status="published",
                    entry_url="/project-assets/department-parent-project/index.html",
                ),
                Project(
                    id="unrelated-department-project",
                    name="无关部门项目",
                    type="internal_tool",
                    department_id="project-dept-other",
                    department="其他业务部",
                    visibility="department",
                    status="published",
                    entry_url="/project-assets/unrelated-department-project/index.html",
                ),
            ]
        )
        await db.commit()

        assert await project_service.can_read_project(
            db, child_user, await db.get(Project, "department-parent-project")
        ) is True
        assert await project_service.can_read_project(
            db, child_user, await db.get(Project, "unrelated-department-project")
        ) is False
        listed = await project_service.list_projects(db, child_user, include_playbooks=False)
        listed_ids = {item["id"] for item in listed["items"]}
        assert "department-parent-project" in listed_ids
        assert "unrelated-department-project" not in listed_ids


async def _seed_project_236_gateway(
    monkeypatch,
    *,
    chat_calls: list[dict] | None = None,
    chat_response: dict | None = None,
    context_measurements: dict | None = None,
) -> None:
    from app.projects import service as project_service
    from app.common.models import SystemConfig
    import app.database as db_mod

    resident_models = [
        {
            "model": "skillforge-base-test-236",
            "deployment_id": "deploy-test-236",
            "runtime_profile": "mlx-test",
            "status": "loaded",
            "loaded": True,
        }
    ]
    live_models = [
        {
            "id": "skillforge-base-test-236",
            "display_name": "SkillForge Base Test 236",
            "runtime_profile": "mlx-test",
            "deployment_id": "deploy-test-236",
        },
        {
            "id": "qwen-extra-test-236",
            "display_name": "Qwen Extra Test 236",
            "runtime_profile": "mlx-extra",
        },
    ]
    async with db_mod.async_session_factory() as db:
        db.add(OpenClawInstance(
            id="inference-primary",
            name="Mac 236 · M3 Ultra",
            department="AI小组",
            gateway_url="bridge://inference-primary",
            reload_hook_url="bridge://inference-primary/reload",
            reload_token="reload-token",
            auth_token="auth-token",
            is_active=True,
            agent_type="aiclaw",
            agent_purpose="mixed",
            bridge_gateway_kind="openclaw",
            bridge_platform="darwin",
            bridge_capabilities_json=json.dumps({
                "platform": "darwin",
                "resident_models": resident_models,
                "training": {"gateway": True, "resident_models": resident_models},
            }),
        ))
        if context_measurements is not None:
            db.add(SystemConfig(
                key=project_service.PROJECT_236_CONTEXT_CONFIG_KEY,
                value=context_measurements,
                updated_by="test",
            ))
        await db.commit()

    monkeypatch.setattr(project_service.bridge_registry, "is_online", lambda instance_id: instance_id == "inference-primary")

    async def fake_openwebui_status(self, payload=None, *, timeout=30):
        return {"ok": True, "reachable": True, "models": live_models}

    async def fake_openwebui_chat(self, payload, *, timeout=600):
        if chat_calls is not None:
            chat_calls.append(payload)
        if chat_response is not None:
            return dict(chat_response)
        return {
            "ok": True,
            "status": "succeeded",
            "text": f"236 says {payload['model_id']}",
            "finish_reason": "stop",
            "response_id": "chatcmpl-test-236",
            "metrics": {"prompt_tokens": 7, "generated_tokens": 5},
        }

    monkeypatch.setattr(project_service.AIClawClient, "get_openwebui_status", fake_openwebui_status)
    monkeypatch.setattr(project_service.AIClawClient, "test_openwebui_chat", fake_openwebui_chat)


def test_project_openai_text_tool_call_parser_accepts_common_formats():
    from app.projects import service as project_service

    tools = [{
        "type": "function",
        "function": {
            "name": "calculator",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
            },
        },
    }]
    examples = [
        'tool_use(name="calculator", arguments={"expression":"19 + 23"})',
        "tool_use(name='calculator', arguments={'expression': '19 + 23'})",
        'tool_use("calculator", {"expression": "19 + 23"})',
        '```json\n{"name":"calculator","arguments":{"expression":"19 + 23"}}\n```',
    ]
    for text in examples:
        calls = project_service._project_openai_parse_text_tool_calls(text, tools=tools)
        assert len(calls) == 1
        assert calls[0]["type"] == "function"
        assert calls[0]["function"]["name"] == "calculator"
        assert json.loads(calls[0]["function"]["arguments"]) == {"expression": "19 + 23"}

    assert project_service._project_openai_parse_text_tool_calls("I would add 19 and 23.", tools=tools) == []


def test_project_openai_messages_prompt_preserves_tool_loop_context():
    from app.projects import service as project_service

    prompt = project_service._project_openai_messages_to_prompt(
        [
            {"role": "user", "content": "Use calculator for 19 + 23."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "calculator", "arguments": '{"expression":"19 + 23"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "name": "calculator", "content": "42"},
        ],
        tools=[{"type": "function", "function": {"name": "calculator"}}],
        tool_choice="auto",
    )

    assert "assistant: tool_calls:" in prompt
    assert "tool(calculator): 42" in prompt
    assert 'tool_use(name="<tool_name>", arguments={<JSON object>})' in prompt


def _package_example_project(example_name: str) -> bytes:
    import io
    import tarfile

    root = PROJECT_EXAMPLES_DIR / example_name
    assert root.is_dir(), f"missing project example: {root}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            data = path.read_bytes()
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_project_trace_models_define_keyset_pagination_indexes():
    ingress_indexes = {index.name: tuple(column.name for column in index.columns) for index in ProjectIngressEvent.__table__.indexes}
    capability_indexes = {index.name: tuple(column.name for column in index.columns) for index in ProjectCapabilityCall.__table__.indexes}
    assert ingress_indexes["ix_project_ingress_run_created_id"] == ("project_run_id", "created_at", "id")
    assert capability_indexes["ix_project_capability_run_created_id"] == ("project_run_id", "created_at", "id")


@pytest.mark.asyncio
async def test_project_create_list_and_get(client):
    payload = {
        "id": "ec_tool",
        "name": "EC 小工具",
        "description": "部门小网页，无容器运行",
        "type": "external_web",
        "entry_url": "https://example.com/tool",
        "visibility": "department",
    }
    created = (await client.post("/api/projects/", json=payload)).json()
    assert created["id"] == "ec_tool"
    assert created["runtime"]["containerized"] is False
    assert created["runtime"]["sdk_path"] == "/project-gateway-sdk.js"
    assert created["runtime"]["gateway_limits"]["max_ingest_bytes"] >= 1

    listed = (await client.get("/api/projects/")).json()
    assert any(item["id"] == "ec_tool" for item in listed["items"])
    assert listed["runtime_policy"]["containerized"] is False
    assert listed["pagination"]["total"] >= 1
    assert listed["runtime_policy"]["gateway_limits"]["max_capability_calls_per_run"] >= 1

    detail = (await client.get("/api/projects/ec_tool")).json()
    assert detail["name"] == "EC 小工具"
    assert detail["permissions"]["launch"] is True


@pytest.mark.asyncio
async def test_project_sdk_token_starts_run_and_enqueues_gb10_training_sink(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "sdk_token_page",
            "name": "SDK Token Page",
            "type": "external_web",
            "entry_url": "https://example.com/sdk-token",
            "metadata": {"capabilities": ["ai.generate"]},
        },
    )
    created = (
        await client.post(
            "/api/projects/sdk_token_page/tokens",
            json={"name": "orders-api", "expires_in_days": 30},
        )
    ).json()
    raw_token = created["token"]
    assert raw_token.startswith("sfproj_")
    assert created["token_prefix"].startswith("sfproj_")
    assert set(created["scopes"]) == {"runs:create", "runs:capability", "runs:trace"}
    assert "ai.generate" not in raw_token

    listed = (await client.get("/api/projects/sdk_token_page/tokens")).json()
    assert listed["items"][0]["token_prefix"] == created["token_prefix"]
    assert "token" not in listed["items"][0]

    run = (
        await client.post(
            "/api/projects/sdk/runs",
            headers={"Authorization": f"Bearer {raw_token}"},
            json={
                "project_id": "sdk_token_page",
                "request_id": "sdk-token-run-1",
                "input": {"query": "daily", "api_key": "sdk-secret"},
                "metadata": {"caller": "orders-api"},
            },
        )
    ).json()
    assert run["projectRunId"] == run["id"]
    assert run["traceUrl"] == f"/projects/sdk_token_page?run_id={run['id']}"
    assert run["credential_location"] == "platform_only"
    assert run["trainingSink"]["status"] == "pending"
    assert run["trainingSink"]["target_gateway_id"] == "GB10 237"
    assert run["trainingSink"]["dataset_ref"].startswith("learning-artifacts://project/sdk_token_page/training/sdk/")

    missing = await client.post("/api/projects/sdk/runs", json={"project_id": "sdk_token_page"})
    assert missing.status_code == 401

    await client.post(
        "/api/projects/",
        json={
            "id": "sdk_other_page",
            "name": "Other",
            "type": "external_web",
            "entry_url": "https://example.com/other",
        },
    )
    denied = await client.post(
        "/api/projects/sdk/runs",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"project_id": "sdk_other_page", "request_id": "sdk-cross-project"},
    )
    assert denied.status_code == 403

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        token_rows = (await db.execute(select(ProjectSdkToken))).scalars().all()
        jobs = (await db.execute(select(LearningIngestionJob))).scalars().all()

    assert len(token_rows) == 1
    assert token_rows[0].token_hash != raw_token
    assert token_rows[0].last_used_at is not None
    assert any(job.sink_id == "GB10 237" and job.policy_json["source_type"] == "project_run" for job in jobs)


@pytest.mark.asyncio
async def test_project_sdk_token_rate_limit_returns_retryable_429(client, monkeypatch):
    from app.projects import service as project_service

    class AllowLimiter:
        async def check(self, identity: str):
            return True, {"remaining": 99, "retry_after": 0.0, "limit": 100}

    class DenyLimiter:
        async def check(self, identity: str):
            assert "sdk_rate_limit_page:" in identity
            return False, {"remaining": 0, "retry_after": 3.2, "limit": 1}

    monkeypatch.setattr(project_service, "PROJECT_SDK_QPS_LIMITER", DenyLimiter())
    monkeypatch.setattr(project_service, "PROJECT_SDK_DAILY_LIMITER", AllowLimiter())

    await client.post(
        "/api/projects/",
        json={
            "id": "sdk_rate_limit_page",
            "name": "SDK Rate Limit Page",
            "type": "external_web",
            "entry_url": "https://example.com/sdk-rate-limit",
        },
    )
    raw_token = (
        await client.post(
            "/api/projects/sdk_rate_limit_page/tokens",
            json={"name": "limited-client"},
        )
    ).json()["token"]

    resp = await client.post(
        "/api/projects/sdk/runs",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"project_id": "sdk_rate_limit_page", "request_id": "sdk-rate-limited"},
    )

    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "PROJECT_SDK_RATE_LIMITED"
    assert body["error"]["detail"]["error_category"] == "quota_error"
    assert body["error"]["detail"]["retry_after_seconds"] == 4
    assert body["error"]["detail"]["rate_limit"]["kind"] == "qps"


@pytest.mark.asyncio
async def test_project_sdk_ai_capability_records_training_sample_and_gb10_sink(client, monkeypatch):
    from app.projects import service as project_service

    async def fake_platform_ai(db, user, payload, *, effective_skill_id=None, effective_run_id=None):
        assert payload["context_pack"]["capability"]["credential_location"] == "platform_only"
        assert effective_run_id
        return {
            "output": {"summary": "SDK AI output", "recommendation": "keep"},
            "model": "deepseek-v4-pro",
            "model_profile": "default",
            "prompt_hash": "sdk-prompt-hash",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)

    await client.post(
        "/api/projects/",
        json={
            "id": "sdk_ai_page",
            "name": "SDK AI Page",
            "type": "external_web",
            "entry_url": "https://example.com/sdk-ai",
            "metadata": {"capabilities": ["ai.generate", "ai.analyze"]},
        },
    )
    token = (
        await client.post(
            "/api/projects/sdk_ai_page/tokens",
            json={"name": "ai-service"},
        )
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    run = (
        await client.post(
            "/api/projects/sdk/runs",
            headers=headers,
            json={"project_id": "sdk_ai_page", "request_id": "sdk-ai-run-1", "input": {"query": "daily"}},
        )
    ).json()
    capability = (
        await client.post(
            f"/api/projects/sdk/runs/{run['id']}/capability",
            headers=headers,
            json={
                "request_id": "sdk-ai-call-1",
                "capability": "ai.generate",
                "prompt": "summarize",
                "input": {"visible": "ok", "api_key": "sdk-secret-value"},
            },
        )
    ).json()

    assert capability["ok"] is True
    assert capability["call_id"]
    assert capability["trainingSink"]["status"] == "pending"
    assert capability["trainingSink"]["source_type"] == "project_capability_call"
    assert capability["trainingSink"]["target_gateway_id"] == "GB10 237"
    assert capability["traceUrl"] == f"/projects/sdk_ai_page?run_id={run['id']}"

    trace = (
        await client.get(
            f"/api/projects/sdk/runs/{run['id']}/trace",
            headers=headers,
        )
    ).json()
    trace_text = json.dumps(trace, ensure_ascii=False)
    assert "sdk-secret-value" not in trace_text
    assert "[REDACTED]" in trace_text

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        calls = (
            await db.execute(select(ProjectCapabilityCall).where(ProjectCapabilityCall.project_run_id == run["id"]))
        ).scalars().all()
        artifacts = (
            await db.execute(select(LearningArtifact).where(LearningArtifact.artifact_kind == "training_sample"))
        ).scalars().all()
        jobs = (await db.execute(select(LearningIngestionJob))).scalars().all()

    assert len(calls) == 1
    model_samples = [
        item for item in artifacts
        if item.content_json.get("source", {}).get("project_capability_call_id") == calls[0].id
    ]
    assert model_samples
    sample_text = json.dumps(model_samples[0].content_json, ensure_ascii=False)
    assert "sdk-secret-value" not in sample_text
    assert model_samples[0].sensitivity_level == "restricted"
    assert any(
        job.sink_id == "GB10 237"
        and job.policy_json["source_type"] == "project_capability_call"
        and job.policy_json["source_id"] == str(calls[0].id)
        for job in jobs
    )


@pytest.mark.asyncio
async def test_project_sdk_can_call_236_resident_model_and_records_gb10_sink(client, monkeypatch):
    chat_calls: list[dict] = []
    await _seed_project_236_gateway(monkeypatch, chat_calls=chat_calls)
    await client.post(
        "/api/projects/",
        json={
            "id": "sdk_236_page",
            "name": "SDK 236 Page",
            "type": "external_web",
            "entry_url": "https://example.com/sdk-236",
            "metadata": {"capabilities": ["ai.chat", "ai.generate"]},
        },
    )
    token = (
        await client.post(
            "/api/projects/sdk_236_page/tokens",
            json={"name": "resident-model-service"},
        )
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    run = (
        await client.post(
            "/api/projects/sdk/runs",
            headers=headers,
            json={"project_id": "sdk_236_page", "request_id": "sdk-236-run-1", "input": {"query": "daily"}},
        )
    ).json()
    capability = (
        await client.post(
            f"/api/projects/sdk/runs/{run['id']}/capability",
            headers=headers,
            json={
                "request_id": "sdk-236-call-1",
                "capability": "ai.chat",
                "target_gateway_id": "inference-primary",
                "model": "skillforge-base-test-236",
                "messages": [{"role": "user", "content": "hello 236"}],
                "input": {"customer": "A", "messages": [{"role": "user", "content": "hello 236"}]},
                "max_tokens": 64,
            },
        )
    ).json()

    assert capability["ok"] is True
    assert capability["result"]["model"] == "skillforge-base-test-236"
    assert capability["result"]["target_gateway_id"] == "inference-primary"
    assert capability["result"]["credential_location"] == "platform_only"
    assert capability["result"]["data_sink"] == "GB10 237"
    assert capability["trainingSink"]["target_gateway_id"] == "GB10 237"
    assert chat_calls[0]["model_id"] == "skillforge-base-test-236"
    assert chat_calls[0]["max_tokens"] == 64

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        calls = (
            await db.execute(select(ProjectCapabilityCall).where(ProjectCapabilityCall.project_run_id == run["id"]))
        ).scalars().all()
        artifacts = (
            await db.execute(select(LearningArtifact).where(LearningArtifact.artifact_kind == "training_sample"))
        ).scalars().all()
        jobs = (await db.execute(select(LearningIngestionJob))).scalars().all()

    assert len(calls) == 1
    assert calls[0].capability_type == "ai_resident"
    assert calls[0].input_summary["target_gateway_id"] == "inference-primary"
    assert calls[0].output_result["output"] == "236 says skillforge-base-test-236"
    assert any(
        item.content_json.get("source", {}).get("project_capability_call_id") == calls[0].id
        for item in artifacts
    )
    assert any(
        job.sink_id == "GB10 237"
        and job.policy_json["source_type"] == "project_capability_call"
        and job.policy_json["source_id"] == str(calls[0].id)
        for job in jobs
    )


@pytest.mark.asyncio
async def test_project_sdk_236_concurrency_limit_is_token_and_model_scoped(client, monkeypatch):
    from app.projects import service as project_service
    import app.database as db_mod

    chat_calls: list[dict] = []
    await _seed_project_236_gateway(monkeypatch, chat_calls=chat_calls)
    monkeypatch.setattr(project_service.settings, "PROJECT_SDK_CONCURRENT_236_CALLS_PER_TOKEN_MODEL", 1)
    await client.post(
        "/api/projects/",
        json={
            "id": "sdk_236_concurrency_page",
            "name": "SDK 236 Concurrency Page",
            "type": "external_web",
            "entry_url": "https://example.com/sdk-236-concurrency",
            "metadata": {"capabilities": ["ai.chat"]},
        },
    )
    token_payload = (
        await client.post(
            "/api/projects/sdk_236_concurrency_page/tokens",
            json={"name": "resident-model-service"},
        )
    ).json()
    headers = {"Authorization": f"Bearer {token_payload['token']}"}
    run = (
        await client.post(
            "/api/projects/sdk/runs",
            headers=headers,
            json={"project_id": "sdk_236_concurrency_page", "request_id": "sdk-236-concurrency-run-1"},
        )
    ).json()

    async with db_mod.async_session_factory() as db:
        db.add(ProjectCapabilityCall(
            project_run_id=run["id"],
            request_id="existing-running-236",
            capability_key="ai.chat",
            capability_type="ai_resident",
            status="running",
            input_summary={
                "sdk_token_id": token_payload["id"],
                "model": "skillforge-base-test-236",
            },
        ))
        await db.commit()

    resp = await client.post(
        f"/api/projects/sdk/runs/{run['id']}/capability",
        headers=headers,
        json={
            "request_id": "sdk-236-concurrency-call-1",
            "capability": "ai.chat",
            "target_gateway_id": "inference-primary",
            "model": "skillforge-base-test-236",
            "messages": [{"role": "user", "content": "hello 236"}],
        },
    )

    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "PROJECT_SDK_CONCURRENCY_LIMITED"
    assert body["error"]["detail"]["error_category"] == "quota_error"
    assert body["error"]["detail"]["rate_limit"]["kind"] == "concurrency"
    assert chat_calls == []


@pytest.mark.asyncio
async def test_project_openai_236_proxy_lists_models_and_sdk_chat_records_trace(client, monkeypatch):
    chat_calls: list[dict] = []
    await _seed_project_236_gateway(monkeypatch, chat_calls=chat_calls)
    await client.post(
        "/api/projects/",
        json={
            "id": "sdk_openai_236_page",
            "name": "SDK OpenAI 236 Page",
            "type": "external_web",
            "entry_url": "https://example.com/sdk-openai-236",
            "metadata": {"capabilities": ["ai.chat"]},
        },
    )
    catalog = (await client.get("/api/projects/models/236")).json()
    assert catalog["gateway"]["id"] == "inference-primary"
    assert catalog["total"] == 2
    assert catalog["callable_count"] == 2
    assert {item["id"] for item in catalog["data"]} == {"skillforge-base-test-236", "qwen-extra-test-236"}

    token = (
        await client.post(
            "/api/projects/sdk_openai_236_page/tokens",
            json={"name": "openai-compatible-client"},
        )
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    models = (await client.get("/api/projects/openai/236/v1/models", headers=headers)).json()
    assert models["object"] == "list"
    assert {item["id"] for item in models["data"]} == {"skillforge-base-test-236", "qwen-extra-test-236"}
    first_model = models["data"][0]
    assert first_model["context_window"] == 32768
    assert first_model["max_input_tokens"] == 16384
    assert first_model["recommended_input_tokens"] == 16384
    assert first_model["max_output_tokens"] == 4096
    assert first_model["context_status"] == "unverified"

    completion = (
        await client.post(
            "/api/projects/openai/236/v1/chat/completions",
            headers=headers,
            json={
                "request_id": "openai-236-call-1",
                "model": "qwen-extra-test-236",
                "messages": [{"role": "user", "content": "hello through openai"}],
                "max_tokens": 32,
            },
        )
    ).json()
    assert completion["object"] == "chat.completion"
    assert completion["model"] == "qwen-extra-test-236"
    assert completion["choices"][0]["message"]["content"] == "236 says qwen-extra-test-236"
    assert "tool_calls" not in completion["choices"][0]["message"]
    assert completion["skillforge"]["target_gateway_id"] == "inference-primary"
    assert completion["skillforge"]["credential_location"] == "platform_only"
    assert completion["skillforge"]["training_sink"]["target_gateway_id"] == "GB10 237"
    assert completion["skillforge"]["usage_reliable"] is True
    assert completion["skillforge"]["context"]["max_input_tokens"] == 16384
    assert completion["skillforge"]["context"]["truncated"] is False
    assert chat_calls[0]["model_id"] == "qwen-extra-test-236"
    assert chat_calls[0]["max_input_chars"] == len(chat_calls[0]["prompt"])
    assert chat_calls[0]["context_window"] == 32768

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        runs = (await db.execute(select(ProjectRun).where(ProjectRun.project_id == "sdk_openai_236_page"))).scalars().all()
        calls = (
            await db.execute(select(ProjectCapabilityCall).where(ProjectCapabilityCall.capability_type == "ai_resident"))
        ).scalars().all()
        jobs = (await db.execute(select(LearningIngestionJob))).scalars().all()
        executions = (await db.execute(select(ExecutionRun).where(ExecutionRun.skill_id == "project:sdk_openai_236_page"))).scalars().all()

    assert len(runs) == 1
    assert len(calls) == 1
    assert len(executions) == 1
    assert runs[0].status == "completed"
    assert runs[0].completed_at is not None
    assert runs[0].output_result["model"] == "qwen-extra-test-236"
    assert runs[0].output_result["data_sink"] == "GB10 237"
    assert executions[0].status == "completed"
    assert executions[0].completed_at is not None
    assert calls[0].project_run_id == runs[0].id
    assert calls[0].output_result["model"] == "qwen-extra-test-236"
    assert any(job.sink_id == "GB10 237" and job.policy_json["source_id"] == str(calls[0].id) for job in jobs)


@pytest.mark.asyncio
async def test_project_openai_236_proxy_converts_text_tool_use_to_openai_tool_calls(client, monkeypatch):
    chat_calls: list[dict] = []
    await _seed_project_236_gateway(
        monkeypatch,
        chat_calls=chat_calls,
        chat_response={
            "ok": True,
            "status": "succeeded",
            "text": 'tool_use(name="calculator", arguments={"expression":"19 + 23"})',
            "finish_reason": "stop",
            "response_id": "chatcmpl-tool-use-test",
        },
    )
    await client.post(
        "/api/projects/",
        json={
            "id": "sdk_openai_236_tools",
            "name": "SDK OpenAI 236 Tools",
            "type": "external_web",
            "entry_url": "https://example.com/sdk-openai-236-tools",
            "metadata": {"capabilities": ["ai.chat"]},
        },
    )
    token = (
        await client.post(
            "/api/projects/sdk_openai_236_tools/tokens",
            json={"name": "openai-tool-client"},
        )
    ).json()["token"]
    completion = (
        await client.post(
            "/api/projects/openai/236/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "request_id": "openai-236-tool-call-1",
                "model": "skillforge-base-test-236",
                "messages": [{"role": "user", "content": "Use calculator for 19 + 23."}],
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "description": "Evaluate arithmetic expressions.",
                        "parameters": {
                            "type": "object",
                            "properties": {"expression": {"type": "string"}},
                            "required": ["expression"],
                        },
                    },
                }],
                "tool_choice": "auto",
                "max_tokens": 32,
            },
        )
    ).json()

    choice = completion["choices"][0]
    message = choice["message"]
    assert choice["finish_reason"] == "tool_calls"
    assert message["content"] is None
    assert len(message["tool_calls"]) == 1
    tool_call = message["tool_calls"][0]
    assert tool_call["id"].startswith("call_sf_")
    assert tool_call["type"] == "function"
    assert tool_call["function"]["name"] == "calculator"
    assert json.loads(tool_call["function"]["arguments"]) == {"expression": "19 + 23"}
    assert completion["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert completion["skillforge"]["usage_reliable"] is False
    assert completion["skillforge"]["tool_call_compat"]["mode"] == "parsed_from_text"
    assert "tool_use" in completion["skillforge"]["tool_call_compat"]["raw_tool_text_preview"]
    assert "calculator" in chat_calls[0]["prompt"]
    assert 'tool_use(name="<tool_name>", arguments={<JSON object>})' in chat_calls[0]["prompt"]


@pytest.mark.asyncio
async def test_project_openai_236_proxy_rejects_context_overflow_without_auto_truncation(client, monkeypatch):
    chat_calls: list[dict] = []
    await _seed_project_236_gateway(
        monkeypatch,
        chat_calls=chat_calls,
        context_measurements={
            "models": {
                "skillforge-base-test-236": {
                    "context_status": "verified",
                    "context_window": 8192,
                    "max_input_tokens": 64,
                    "recommended_input_tokens": 48,
                    "context_verified_at": "2026-07-24T00:00:00Z",
                }
            }
        },
    )
    await client.post(
        "/api/projects/",
        json={
            "id": "sdk_openai_236_context_overflow",
            "name": "SDK OpenAI 236 Context Overflow",
            "type": "external_web",
            "entry_url": "https://example.com/sdk-openai-236-context-overflow",
            "metadata": {"capabilities": ["ai.chat"]},
        },
    )
    token = (
        await client.post(
            "/api/projects/sdk_openai_236_context_overflow/tokens",
            json={"name": "openai-context-client"},
        )
    ).json()["token"]

    resp = await client.post(
        "/api/projects/openai/236/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "request_id": "openai-236-context-overflow-1",
            "model": "skillforge-base-test-236",
            "messages": [{"role": "user", "content": "x" * 2000}],
            "max_tokens": 32,
        },
    )

    assert resp.status_code == 413
    body = resp.json()
    assert body["error"]["code"] == "PROJECT_OPENAI_CONTEXT_OVERFLOW"
    assert body["error"]["detail"]["error_category"] == "context_overflow"
    assert body["error"]["detail"]["max_input_tokens"] == 64
    assert chat_calls == []

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        calls = (
            await db.execute(select(ProjectCapabilityCall).where(ProjectCapabilityCall.capability_type == "ai_resident"))
        ).scalars().all()

    assert len(calls) == 1
    assert calls[0].status == "failed"
    assert calls[0].input_summary["reason"] == "context_overflow"


@pytest.mark.asyncio
async def test_project_openai_236_proxy_auto_truncates_old_messages(client, monkeypatch):
    chat_calls: list[dict] = []
    await _seed_project_236_gateway(
        monkeypatch,
        chat_calls=chat_calls,
        context_measurements={
            "models": {
                "skillforge-base-test-236": {
                    "context_status": "verified",
                    "context_window": 8192,
                    "max_input_tokens": 96,
                    "recommended_input_tokens": 64,
                    "context_verified_at": "2026-07-24T00:00:00Z",
                }
            }
        },
    )
    await client.post(
        "/api/projects/",
        json={
            "id": "sdk_openai_236_context_auto",
            "name": "SDK OpenAI 236 Context Auto",
            "type": "external_web",
            "entry_url": "https://example.com/sdk-openai-236-context-auto",
            "metadata": {"capabilities": ["ai.chat"]},
        },
    )
    token = (
        await client.post(
            "/api/projects/sdk_openai_236_context_auto/tokens",
            json={"name": "openai-context-auto-client"},
        )
    ).json()["token"]

    completion = (
        await client.post(
            "/api/projects/openai/236/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "request_id": "openai-236-context-auto-1",
                "model": "skillforge-base-test-236",
                "messages": [
                    {"role": "system", "content": "answer briefly"},
                    {"role": "user", "content": "OLDER-CONTEXT " + ("x" * 2000)},
                    {"role": "user", "content": "LATEST-CONTEXT answer this"},
                ],
                "max_tokens": 32,
                "truncation": "auto",
            },
        )
    ).json()

    assert completion["object"] == "chat.completion"
    assert completion["skillforge"]["context"]["truncation"] == "auto"
    assert completion["skillforge"]["context"]["truncated"] is True
    assert completion["skillforge"]["context"]["estimated_input_tokens"] <= 96
    assert "LATEST-CONTEXT" in chat_calls[0]["prompt"]
    assert "OLDER-CONTEXT" not in chat_calls[0]["prompt"]


@pytest.mark.asyncio
async def test_project_sdk_check_project_creates_company_sdk_project(client):
    created = await client.post("/api/projects/sdk/check/project")
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["id"] == "skillforge-company-sdk"
    assert body["name"] == "SkillForge Company SDK"
    assert body["type"] == "internal_tool"
    assert body["visibility"] == "company"
    assert body["status"] == "published"
    assert body["department_id"] is None
    assert body["department"] is None
    assert body["permissions"]["edit"] is True
    assert body["runtime"]["sdk_path"] == "/project-gateway-sdk.js"
    assert set(body["metadata"]["capabilities"]) >= {"ai.chat", "ai.generate", "ai.analyze"}
    assert body["metadata"]["sdk_check"]["openai_236_models_url"] == "/api/projects/openai/236/v1/models"
    assert body["metadata"]["sdk_check"]["openai_236_chat_url"] == "/api/projects/openai/236/v1/chat/completions"
    assert body["metadata"]["training_sink"]["target_gateway_id"] == "GB10 237"

    fetched = (await client.get("/api/projects/skillforge-company-sdk")).json()
    assert fetched["id"] == "skillforge-company-sdk"
    assert fetched["visibility"] == "company"


@pytest.mark.asyncio
async def test_project_sdk_check_summary_verifies_236_token_call_and_gb10_storage(client, monkeypatch):
    chat_calls: list[dict] = []
    await _seed_project_236_gateway(monkeypatch, chat_calls=chat_calls)
    project = (await client.post("/api/projects/sdk/check/project")).json()
    token = (
        await client.post(
            f"/api/projects/{project['id']}/tokens",
            json={"name": "sdk-check-validation", "expires_in_days": 1},
        )
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    unauthenticated = await client.get("/api/projects/openai/236/v1/models")
    assert unauthenticated.status_code == 401

    models = (await client.get("/api/projects/openai/236/v1/models", headers=headers)).json()
    assert models["object"] == "list"
    assert models["total"] == 2
    assert {item["id"] for item in models["data"]} == {"skillforge-base-test-236", "qwen-extra-test-236"}

    completion = (
        await client.post(
            "/api/projects/openai/236/v1/chat/completions",
            headers=headers,
            json={
                "request_id": "sdk-check-openai-236-1",
                "model": "skillforge-base-test-236",
                "messages": [{"role": "user", "content": "只回复 ok"}],
                "max_tokens": 8,
            },
        )
    ).json()
    assert completion["choices"][0]["message"]["content"] == "236 says skillforge-base-test-236"
    assert completion["skillforge"]["project_id"] == "skillforge-company-sdk"
    assert completion["skillforge"]["training_sink"]["target_gateway_id"] == "GB10 237"

    summary = (
        await client.get(f"/api/projects/sdk/check/runs/{completion['skillforge']['project_run_id']}")
    ).json()
    assert summary["ok"] is True
    assert summary["project"]["id"] == "skillforge-company-sdk"
    assert summary["run"]["status"] == "completed"
    assert summary["run"]["output_model"] == "skillforge-base-test-236"
    assert summary["run"]["output_data_sink"] == "GB10 237"
    assert summary["counts"]["capability_calls"] == 1
    assert summary["counts"]["completed_capability_calls"] == 1
    assert summary["counts"]["gb10_237_jobs"] >= 1
    assert summary["capability_calls"][0]["status"] == "completed"
    assert summary["capability_calls"][0]["model"] == "skillforge-base-test-236"
    assert summary["capability_calls"][0]["data_sink"] == "GB10 237"
    assert summary["training_sink_jobs"][0]["target_gateway_id"] == "GB10 237"
    assert chat_calls[0]["model_id"] == "skillforge-base-test-236"
    assert token not in json.dumps(summary)


@pytest.mark.asyncio
async def test_project_rejects_unsafe_entry_url_schemes(client):
    created = await client.post(
        "/api/projects/",
        json={
            "id": "unsafe_url_project",
            "name": "Unsafe URL",
            "type": "external_web",
            "entry_url": "javascript:alert(1)",
            "visibility": "department",
        },
    )
    assert created.status_code == 400
    created_body = created.json()["error"]
    assert created_body["code"] == "PROJECT_INVALID"
    assert created_body["detail"]["field"] == "entry_url"
    assert "http/https" in created_body["detail"]["reason"]

    registered = await client.post(
        "/api/projects/auto-register",
        json={
            "manifest": {
                "project_id": "unsafe_auto_url",
                "name": "Unsafe Auto URL",
                "kind": "external_web",
                "entry_url": "data:text/html,<script>alert(1)</script>",
            },
        },
    )
    assert registered.status_code == 400
    registered_body = registered.json()["error"]
    assert registered_body["code"] == "PROJECT_INVALID"
    assert registered_body["detail"]["field"] == "entry_url"


@pytest.mark.asyncio
async def test_project_list_filters_by_department_for_large_directory(client):
    for project_id, department_id, department in [
        ("dept_filter_ai", "dept-ai", "AI小组"),
        ("dept_filter_ops", "dept-ops", "运营部"),
        ("dept_filter_sales", "dept-sales", "销售部"),
    ]:
        resp = await client.post(
            "/api/projects/",
            json={
                "id": project_id,
                "name": f"{department}项目",
                "type": "dashboard",
                "department_id": department_id,
                "department": department,
                "entry_url": f"https://example.com/{project_id}",
            },
        )
        assert resp.status_code == 200, resp.text

    by_department = (
        await client.get(
            "/api/projects/",
            params={"include_playbooks": "false", "department": "运营部", "page_size": 100},
        )
    ).json()
    assert by_department["pagination"]["total"] == 1
    assert [item["id"] for item in by_department["items"]] == ["dept_filter_ops"]
    assert by_department["stats"]["by_department"] == {"运营部": 1}

    by_department_id = (
        await client.get(
            "/api/projects/",
            params={"include_playbooks": "false", "department_id": "dept-sales", "page_size": 100},
        )
    ).json()
    assert by_department_id["pagination"]["total"] == 1
    assert by_department_id["items"][0]["department_id"] == "dept-sales"
    assert by_department_id["items"][0]["department"] == "销售部"


@pytest.mark.asyncio
async def test_project_run_ingest_records_execution_decision_and_learning(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "ops_page",
            "name": "运营页面",
            "type": "external_web",
            "entry_url": "https://example.com/ops",
        },
    )
    run = (await client.post("/api/projects/ops_page/runs", json={"params": {"q": "today"}})).json()
    assert run["status"] == "running"
    assert run["execution_run_id"]

    ingested = (
        await client.post(
            f"/api/projects/runs/{run['id']}/ingest",
            json={
                "auto_analyze": False,
                "input": {"q": "today"},
                "output": {"summary": "运营页面输出完成"},
                "reports": [{"title": "运营报告", "summary": "已发现一个可跟进机会"}],
            },
        )
    ).json()
    assert ingested["status"] == "completed"
    assert ingested["decision_log_id"]
    assert len(ingested["report_cards"]) == 1
    assert ingested["report_count"] == 1
    assert ingested["todo_count"] == 0

    listed = (
        await client.get(
            "/api/projects/",
            params={"include_playbooks": "false", "run_status": "ai_completed", "search": "运营页面"},
        )
    ).json()
    assert listed["pagination"]["total"] == 1
    assert listed["items"][0]["latest_run"]["report_count"] == 1
    assert listed["stats"]["report_count"] == 1

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project_run = await db.get(ProjectRun, run["id"])
        execution = await db.get(ExecutionRun, run["execution_run_id"])
        decision = await db.get(DecisionLog, ingested["decision_log_id"])
        events = (
            await db.execute(
                select(LearningEvent).where(LearningEvent.run_id == run["execution_run_id"])
            )
        ).scalars().all()
        artifacts = (
            await db.execute(
                select(LearningArtifact).where(LearningArtifact.run_id == run["execution_run_id"])
            )
        ).scalars().all()

    assert project_run is not None
    assert execution is not None and execution.status == "completed"
    assert execution.skill_id == "project:ops_page"
    assert decision is not None and decision.skill_id == "project:ops_page"
    assert any(event.source_type == "decision_log" for event in events)
    assert any(event.source_type == "execution_run" for event in events)
    assert any(event.source_type == "project_run" and event.event_type == "project.run.opened" for event in events)
    assert any(event.source_type == "project_ingress_event" and event.event_type == "project.ingress.received" for event in events)
    assert any(artifact.artifact_kind == "report_summary" and artifact.target_id == "ops_page" for artifact in artifacts)
    assert any(artifact.artifact_kind == "training_sample" and artifact.target_type == "project" for artifact in artifacts)


@pytest.mark.asyncio
async def test_project_run_input_event_records_department_trace_and_learning(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "input_page",
            "name": "输入页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/input",
            "department": "AI小组",
        },
    )
    run = (
        await client.post(
            "/api/projects/input_page/runs",
            json={"request_id": "open-input-001", "params": {"source": "project_page"}, "input": {"initial": "yes"}},
        )
    ).json()
    assert run["input_snapshot"] == {"source": "project_page", "initial": "yes"}

    first = (
        await client.post(
            f"/api/projects/runs/{run['id']}/input",
            json={
                "request_id": "input-001",
                "input": {"keyword": "今日运营", "api_key": "secret-key"},
                "metadata": {"source": "filter-form"},
            },
        )
    ).json()
    duplicate = (
        await client.post(
            f"/api/projects/runs/{run['id']}/input",
            json={
                "request_id": "input-001",
                "input": {"keyword": "今日运营", "api_key": "secret-key"},
                "metadata": {"source": "filter-form"},
            },
        )
    ).json()
    assert first["status"] == "running"
    assert first["input_event_id"]
    assert first["input_snapshot"]["source"] == "project_page"
    assert first["input_snapshot"]["initial"] == "yes"
    assert first["input_snapshot"]["keyword"] == "今日运营"
    assert duplicate["deduped"] is True
    assert duplicate["input_event_id"] == first["input_event_id"]

    trace = (await client.get(f"/api/projects/runs/{run['id']}/trace")).json()
    assert trace["counts"]["input_events"] == 1
    assert trace["counts"]["output_events"] == 0
    assert trace["timeline"][0]["kind"] == "input"
    assert trace["ingress_events"][0]["event_type"] == "input"
    assert trace["ingress_events"][0]["input_snapshot"]["api_key"] == "[REDACTED]"

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project_run = await db.get(ProjectRun, run["id"])
        execution = await db.get(ExecutionRun, run["execution_run_id"])
        input_events = (
            await db.execute(
                select(ProjectIngressEvent).where(ProjectIngressEvent.project_run_id == run["id"], ProjectIngressEvent.event_type == "input")
            )
        ).scalars().all()
        learning_events = (
            await db.execute(
                select(LearningEvent).where(LearningEvent.source_type == "project_ingress_event")
            )
        ).scalars().all()

    assert project_run is not None
    assert project_run.input_snapshot["keyword"] == "今日运营"
    assert project_run.department == "AI小组"
    assert execution is not None
    assert execution.metadata_json["department"] == "AI小组"
    assert execution.metadata_json["last_project_input_request_id"] == "input-001"
    assert len(input_events) == 1
    assert input_events[0].request_id == "input-001"
    assert any(event.source_id == str(input_events[0].id) and event.event_type == "project.input.received" for event in learning_events)


@pytest.mark.asyncio
async def test_project_run_assets_upload_list_download_and_trace(client, tmp_path, monkeypatch):
    from app.projects import service as project_service

    monkeypatch.setattr(project_service, "PROJECT_RUN_ASSETS_DIR", tmp_path / "project-run-assets")
    monkeypatch.setattr(project_service, "PROJECT_RUN_ASSET_STREAM_CHUNK_BYTES", 8)
    await client.post(
        "/api/projects/",
        json={
            "id": "asset_page",
            "name": "资产页面",
            "type": "dashboard",
            "entry_url": "https://example.com/assets",
        },
    )
    run = (await client.post("/api/projects/asset_page/runs", json={"params": {"source": "asset-test"}})).json()

    uploaded = (
        await client.post(
            f"/api/projects/runs/{run['id']}/assets",
            files={"file": ("spend.csv", b"video_name,owner,sku,spend\na.mp4,A,SKU,100\n", "text/csv")},
            data={"metadata_json": '{"source":"pytest"}'},
        )
    ).json()
    assert uploaded["ok"] is True
    asset = uploaded["asset"]
    assert asset["source_kind"] == "data"
    assert asset["sha256"]
    assert asset["metadata"]["upload_transport"] == "streamed_multipart"
    assert asset["metadata"]["upload_chunk_bytes"] == 8
    assert asset["metadata"]["upload_chunk_count"] > 1
    assert asset["download_url"].endswith(f"/assets/{asset['id']}/download")

    listed = (await client.get(f"/api/projects/runs/{run['id']}/assets")).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == asset["id"]

    downloaded = await client.get(f"/api/projects/runs/{run['id']}/assets/{asset['id']}/download")
    assert downloaded.status_code == 200
    assert b"video_name,owner" in downloaded.content

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        asset_model = await db.get(ProjectRunAsset, asset["id"])
        assert asset_model is not None
        signed_url = project_service._project_run_asset_signed_download_url(asset_model)  # noqa: SLF001 - focused service integration test
    signed_path = signed_url.split("http://localhost:8000", 1)[-1]
    signed = await client.get(signed_path)
    assert signed.status_code == 200
    assert b"video_name,owner" in signed.content

    rejected = await client.get(signed_path.replace("token=", "token=bad", 1))
    assert rejected.status_code == 403

    trace = (await client.get(f"/api/projects/runs/{run['id']}/trace")).json()
    asset_events = [row for row in trace["ingress_events"] if row.get("request_id") == f"asset:{asset['id']}"]
    assert asset_events
    assert asset_events[0]["event_type"] == "asset"
    assert trace["counts"]["asset_events_total"] == 1
    assert trace["counts"]["output_events_total"] == 0

    monkeypatch.setattr(project_service, "PROJECT_RUN_ASSET_MAX_BYTES", 10)
    too_large = await client.post(
        f"/api/projects/runs/{run['id']}/assets",
        files={"file": ("too-large.mp4", b"0123456789-too-large", "video/mp4")},
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "PROJECT_ASSET_TOO_LARGE"
    assert not list((tmp_path / "project-run-assets" / ".uploads").glob("*.part"))

    refreshed = (await client.get(f"/api/projects/runs/{run['id']}")).json()
    assert refreshed["input_snapshot"] == {"source": "asset-test"}


@pytest.mark.asyncio
async def test_project_ai_analyze_uses_visual_frame_assets(client, tmp_path, monkeypatch):
    from app.projects import service as project_service

    monkeypatch.setattr(project_service, "PROJECT_RUN_ASSETS_DIR", tmp_path / "project-run-assets")
    captured: dict[str, object] = {}

    async def fake_multimodal(system, content_parts, **kwargs):
        captured["vision_system"] = system
        captured["vision_parts"] = content_parts
        captured["vision_kwargs"] = kwargs
        return {
            "summary": "素材 A 首屏出现商品和价格，素材 B 首屏商品露出弱。",
            "materials": {
                "material_a": {"evidence": ["首帧商品居中", "价格文字清晰"]},
                "material_b": {"evidence": ["开头以场景为主"]},
            },
            "evidence": ["material_a opening frame 商品明显"],
            "risks": [],
            "confidence": 0.82,
        }

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        captured["final_args"] = args
        visual = args["context_pack"]["visual_analysis"]
        assert visual["status"] == "ok"
        assert visual["materials"]["material_a"]["result"]["summary"].startswith("素材 A")
        return {
            "ok": True,
            "model": "text-agent",
            "output": "已基于视觉证据完成分析",
            "prompt_hash": "visual-final-hash",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service, "call_llm_multimodal", fake_multimodal)
    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)
    await client.post(
        "/api/projects/",
        json={
            "id": "visual_ai_page",
            "name": "视觉分析页面",
            "type": "dashboard",
            "entry_url": "https://example.com/visual",
            "metadata": {"capabilities": ["ai.analyze"]},
        },
    )
    run = (await client.post("/api/projects/visual_ai_page/runs", json={"params": {"source": "visual-test"}})).json()
    uploaded = (
        await client.post(
            f"/api/projects/runs/{run['id']}/assets",
            files={"file": ("good-opening.jpg", b"\xff\xd8\xff\xe0visual-frame", "image/jpeg")},
            data={
                "metadata_json": (
                    '{"source":"short_video_frame_capture","role":"material_a",'
                    '"visual_frame":true,"frame_label":"opening","frame_time":0.8}'
                )
            },
        )
    ).json()
    assert uploaded["ok"] is True

    result = (
        await client.post(
            f"/api/projects/runs/{run['id']}/capability",
            json={
                "capability": "ai.analyze",
                "request_id": "visual-ai-001",
                "prompt": "请分析素材 A/B。",
                "input": {"videos": [{"role": "material_a", "label": "素材 A"}]},
            },
        )
    ).json()

    assert result["status"] == "ai_completed"
    assert result["visual_analysis"]["status"] == "ok"
    assert result["output"] == "已基于视觉证据完成分析"
    assert captured["vision_kwargs"]["model_profile"] == "vision"
    assert any(part.get("type") == "image_url" for part in captured["vision_parts"])
    assert "visual_analysis" in captured["final_args"]["context_pack"]


@pytest.mark.asyncio
async def test_project_ai_analyze_prefers_visual_video_assets(client, tmp_path, monkeypatch):
    from app.projects import service as project_service

    monkeypatch.setattr(project_service, "PROJECT_RUN_ASSETS_DIR", tmp_path / "project-run-assets")
    captured: dict[str, object] = {}

    async def fake_multimodal(system, content_parts, **kwargs):
        captured["vision_system"] = system
        captured["vision_parts"] = content_parts
        captured["vision_kwargs"] = kwargs
        return {
            "summary": "素材 A 原视频首屏商品和优惠明确。",
            "timeline": [{"stage": "opening", "evidence": "商品居中"}],
            "diagnosis": {
                "hook": "强",
                "product_exposure": "清晰",
                "pace_density": "紧凑",
                "proof": "有演示",
                "action_guidance": "明确",
                "consumer_takeaway": "能快速理解利益点",
            },
            "evidence": ["video opening 商品居中"],
            "risks": [],
            "confidence": 0.86,
        }

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        captured["final_args"] = args
        visual = args["context_pack"]["visual_analysis"]
        assert visual["status"] == "ok"
        assert visual["mode"] == "direct_video"
        assert visual["materials"]["material_a"]["result"]["summary"].startswith("素材 A 原视频")
        return {
            "ok": True,
            "model": "qwen3.6",
            "output": "已基于原视频视觉证据完成分析",
            "prompt_hash": "visual-video-final-hash",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service, "call_llm_multimodal", fake_multimodal)
    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)
    await client.post(
        "/api/projects/",
        json={
            "id": "visual_video_page",
            "name": "原视频视觉分析页面",
            "type": "dashboard",
            "entry_url": "https://example.com/visual-video",
            "metadata": {"capabilities": ["ai.analyze"]},
        },
    )
    run = (await client.post("/api/projects/visual_video_page/runs", json={"params": {"source": "visual-video-test"}})).json()
    uploaded = (
        await client.post(
            f"/api/projects/runs/{run['id']}/assets",
            files={"file": ("material-a.mp4", b"\x00\x00\x00\x18ftypmp42visual-video", "video/mp4")},
            data={
                "metadata_json": (
                    '{"source":"short_video_analysis_page","role":"material_a"}'
                )
            },
        )
    ).json()
    assert uploaded["ok"] is True

    result = (
        await client.post(
            f"/api/projects/runs/{run['id']}/capability",
            json={
                "capability": "ai.analyze",
                "request_id": "visual-video-ai-001",
                "prompt": "请分析素材 A/B。",
                "input": {"videos": [{"role": "material_a", "label": "素材 A"}]},
            },
        )
    ).json()

    assert result["status"] == "ai_completed"
    assert result["visual_analysis"]["mode"] == "direct_video"
    assert result["output"] == "已基于原视频视觉证据完成分析"
    assert captured["vision_kwargs"]["model_profile"] == "vision"
    assert any(part.get("type") == "video_url" for part in captured["vision_parts"])
    assert not any(part.get("type") == "image_url" for part in captured["vision_parts"])


@pytest.mark.asyncio
async def test_project_ai_analyze_merges_video_and_frame_fallback(client, tmp_path, monkeypatch):
    from app.projects import service as project_service

    monkeypatch.setattr(project_service, "PROJECT_RUN_ASSETS_DIR", tmp_path / "project-run-assets")
    calls: list[dict[str, object]] = []
    captured: dict[str, object] = {}

    async def fake_multimodal(system, content_parts, **kwargs):
        calls.append({"system": system, "parts": content_parts, "kwargs": kwargs})
        if any(part.get("type") == "video_url" for part in content_parts):
            return {
                "summary": "素材 A 原视频首屏商品露出明确。",
                "evidence": ["素材 A 原视频商品居中"],
                "confidence": 0.84,
            }
        return {
            "summary": "素材 B 关键帧首屏商品弱，证据不足。",
            "evidence": ["素材 B opening frame 商品不明显"],
            "confidence": 0.78,
        }

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        visual = args["context_pack"]["visual_analysis"]
        captured["visual"] = visual
        assert visual["status"] == "ok"
        assert visual["mode"] == "mixed_video_frame"
        assert visual["materials"]["material_a"]["result"]["summary"].startswith("素材 A 原视频")
        assert visual["materials"]["material_b"]["result"]["summary"].startswith("素材 B 关键帧")
        return {
            "ok": True,
            "model": "text-agent",
            "output": "已基于原视频和关键帧兜底完成分析",
            "prompt_hash": "mixed-visual-final-hash",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service, "call_llm_multimodal", fake_multimodal)
    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)
    await client.post(
        "/api/projects/",
        json={
            "id": "visual_mixed_page",
            "name": "混合视觉分析页面",
            "type": "dashboard",
            "entry_url": "https://example.com/visual-mixed",
            "metadata": {"capabilities": ["ai.analyze"]},
        },
    )
    run = (await client.post("/api/projects/visual_mixed_page/runs", json={"params": {"source": "visual-mixed-test"}})).json()
    video_upload = (
        await client.post(
            f"/api/projects/runs/{run['id']}/assets",
            files={"file": ("material-a.mp4", b"\x00\x00\x00\x18ftypmp42visual-video", "video/mp4")},
            data={"metadata_json": '{"source":"short_video_analysis_page","role":"material_a"}'},
        )
    ).json()
    assert video_upload["ok"] is True
    frame_upload = (
        await client.post(
            f"/api/projects/runs/{run['id']}/assets",
            files={"file": ("bad-opening-frame.jpg", b"\xff\xd8\xff\xe0visual-frame", "image/jpeg")},
            data={
                "metadata_json": (
                    '{"source":"short_video_frame_capture","role":"material_b",'
                    '"visual_frame":true,"frame_label":"opening","frame_time":0.8}'
                )
            },
        )
    ).json()
    assert frame_upload["ok"] is True

    result = (
        await client.post(
            f"/api/projects/runs/{run['id']}/capability",
            json={
                "capability": "ai.analyze",
                "request_id": "visual-mixed-ai-001",
                "prompt": "请分析素材 A/B。",
                "input": {
                    "videos": [
                        {"role": "material_a", "label": "素材 A"},
                        {"role": "material_b", "label": "素材 B"},
                    ]
                },
            },
        )
    ).json()

    assert result["status"] == "ai_completed"
    assert result["visual_analysis"]["mode"] == "mixed_video_frame"
    assert result["output"] == "已基于原视频和关键帧兜底完成分析"
    assert len(calls) == 2
    assert any(any(part.get("type") == "video_url" for part in call["parts"]) for call in calls)
    assert any(any(part.get("type") == "image_url" for part in call["parts"]) for call in calls)


@pytest.mark.asyncio
async def test_project_input_request_id_lock_dedupes_concurrent_events(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "input_lock_page",
            "name": "输入锁页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/input-lock",
        },
    )
    run = (await client.post("/api/projects/input_lock_page/runs", json={"request_id": "input-lock-open"})).json()

    async def record_input():
        return await client.post(
            f"/api/projects/runs/{run['id']}/input",
            json={"request_id": "input-lock-001", "input": {"keyword": "并发输入"}},
        )

    first, second = await asyncio.gather(record_input(), record_input())
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    bodies = [first.json(), second.json()]
    assert any(body.get("deduped") is True for body in bodies)
    assert len({body.get("input_event_id") for body in bodies}) == 1

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        events = (
            await db.execute(
                select(ProjectIngressEvent).where(ProjectIngressEvent.project_run_id == run["id"])
            )
        ).scalars().all()
        learning_events = (
            await db.execute(
                select(LearningEvent).where(LearningEvent.source_type == "project_ingress_event")
            )
        ).scalars().all()

    assert len(events) == 1
    assert events[0].request_id == "input-lock-001"
    assert len([event for event in learning_events if event.event_type == "project.input.received"]) == 1


@pytest.mark.asyncio
async def test_project_input_rejects_payload_over_limit_but_records_failed_trace(client, monkeypatch):
    from app.projects import service as project_service

    monkeypatch.setattr(project_service, "PROJECT_INPUT_MAX_BYTES", 96)
    await client.post(
        "/api/projects/",
        json={
            "id": "input_limit_page",
            "name": "输入限制页面",
            "type": "external_web",
            "entry_url": "https://example.com/input-limit",
        },
    )
    run = (await client.post("/api/projects/input_limit_page/runs", json={})).json()
    payload = {"request_id": "input-too-large", "input": {"keyword": "x" * 260}}
    first = await client.post(f"/api/projects/runs/{run['id']}/input", json=payload)
    replay = await client.post(f"/api/projects/runs/{run['id']}/input", json=payload)

    assert first.status_code == 413
    assert replay.status_code == 413
    first_body = first.json()["error"]
    replay_body = replay.json()["error"]
    assert first_body["code"] == "PROJECT_PAYLOAD_TOO_LARGE"
    assert first_body["detail"]["reason"] == "input_event_bytes"
    assert first_body["detail"]["ingress_event_status"] == "failed"
    assert replay_body["detail"]["event_id"] == first_body["detail"]["event_id"]
    assert replay_body["detail"]["deduped"] is True

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        events = (
            await db.execute(select(ProjectIngressEvent).where(ProjectIngressEvent.project_run_id == run["id"]))
        ).scalars().all()
        project_run = await db.get(ProjectRun, run["id"])
        learning_events = (
            await db.execute(select(LearningEvent).where(LearningEvent.source_type == "project_ingress_event"))
        ).scalars().all()

    assert project_run is not None
    assert project_run.input_snapshot == {}
    assert len(events) == 1
    assert events[0].event_type == "input"
    assert events[0].input_snapshot == {}
    assert events[0].output_result["status"] == "failed"
    assert events[0].metadata_json["payload_reason"] == "input_event_bytes"
    assert "x" * 50 not in str(events[0].output_result)
    assert any(
        event.source_id == str(events[0].id)
        and event.event_type == "project.input.received"
        and event.metadata_json["gateway_status"] == "failed"
        for event in learning_events
    )


@pytest.mark.asyncio
async def test_project_input_and_output_can_share_business_request_id(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "shared_request_page",
            "name": "共享请求 ID 页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/shared-request",
        },
    )
    run = (await client.post("/api/projects/shared_request_page/runs", json={"request_id": "shared-open"})).json()
    recorded_input = (
        await client.post(
            f"/api/projects/runs/{run['id']}/input",
            json={"request_id": "business-001", "input": {"query": "同一个业务请求"}},
        )
    ).json()
    ingested = (
        await client.post(
            f"/api/projects/runs/{run['id']}/ingest",
            json={
                "request_id": "business-001",
                "auto_analyze": False,
                "output": {"summary": "同一个业务请求已输出"},
            },
        )
    ).json()
    duplicate_output = (
        await client.post(
            f"/api/projects/runs/{run['id']}/ingest",
            json={
                "request_id": "business-001",
                "auto_analyze": False,
                "output": {"summary": "重复输出不应新增"},
            },
        )
    ).json()

    assert recorded_input["input_event_id"]
    assert ingested["status"] == "completed"
    assert duplicate_output["deduped"] is True

    trace = (await client.get(f"/api/projects/runs/{run['id']}/trace")).json()
    assert trace["counts"]["input_events"] == 1
    assert trace["counts"]["output_events"] == 1
    assert trace["counts"]["ingress_events_total"] == 2
    assert {event["event_type"] for event in trace["ingress_events"]} == {"input", "output"}
    assert [event["request_id"] for event in trace["ingress_events"]].count("business-001") == 2

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        events = (
            await db.execute(
                select(ProjectIngressEvent).where(ProjectIngressEvent.project_run_id == run["id"])
            )
        ).scalars().all()
        learning_events = (
            await db.execute(
                select(LearningEvent).where(LearningEvent.source_type == "project_ingress_event")
            )
        ).scalars().all()

    assert len(events) == 2
    assert {event.event_type for event in events} == {"input", "output"}
    assert len([event for event in learning_events if event.event_type == "project.input.received"]) == 1
    assert len([event for event in learning_events if event.event_type == "project.ingress.received"]) == 1


@pytest.mark.asyncio
async def test_project_run_trace_supports_gateway_pagination(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "trace_page",
            "name": "Trace 分页页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/trace",
        },
    )
    run = (await client.post("/api/projects/trace_page/runs", json={"request_id": "trace-open-001"})).json()
    for idx in range(5):
        resp = await client.post(
            f"/api/projects/runs/{run['id']}/input",
            json={"request_id": f"trace-input-{idx}", "input": {"idx": idx}},
        )
        assert resp.status_code == 200, resp.text

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        created_at = now_bjt()
        for idx in range(2):
            db.add(
                ProjectCapabilityCall(
                    project_run_id=run["id"],
                    request_id=f"trace-capability-{idx}",
                    capability_key="ai.generate",
                    capability_type="ai",
                    status="completed",
                    input_payload={"idx": idx},
                    output_result={"summary": f"capability {idx}"},
                    created_at=created_at + timedelta(seconds=idx),
                    completed_at=created_at + timedelta(seconds=idx),
                )
            )
        await db.commit()

    first_page = (await client.get(f"/api/projects/runs/{run['id']}/trace", params={"limit": 2, "offset": 0})).json()
    assert first_page["counts"]["ingress_events"] == 2
    assert first_page["counts"]["ingress_events_total"] == 5
    assert first_page["counts"]["input_events"] == 2
    assert first_page["counts"]["capability_calls"] == 2
    assert first_page["counts"]["capability_calls_total"] == 2
    assert first_page["pagination"]["limit"] == 2
    assert first_page["pagination"]["offset"] == 0
    assert first_page["pagination"]["ingress_events_total"] == 5
    assert first_page["pagination"]["input_events_total"] == 5
    assert first_page["pagination"]["output_events_total"] == 0
    assert first_page["pagination"]["capability_calls_total"] == 2
    assert first_page["pagination"]["has_more_ingress_events"] is True
    assert first_page["pagination"]["has_more_capability_calls"] is False
    assert first_page["pagination"]["next_offset"] == 2
    assert first_page["pagination"]["next_ingress_cursor"]
    assert first_page["pagination"]["next_capability_cursor"]
    first_page_requests = [row["request_id"] for row in first_page["ingress_events"]]

    await client.post(
        f"/api/projects/runs/{run['id']}/input",
        json={"request_id": "trace-input-newer", "input": {"idx": 99}},
    )
    cursor_page = (
        await client.get(
            f"/api/projects/runs/{run['id']}/trace",
            params={
                "limit": 2,
                "ingress_cursor": first_page["pagination"]["next_ingress_cursor"],
                "capability_cursor": first_page["pagination"]["next_capability_cursor"],
            },
        )
    ).json()
    cursor_requests = [row["request_id"] for row in cursor_page["ingress_events"]]
    assert "trace-input-newer" not in cursor_requests
    assert not set(first_page_requests) & set(cursor_requests)
    assert cursor_page["capability_calls"] == []
    assert cursor_page["pagination"]["has_more_capability_calls"] is False
    assert cursor_page["pagination"]["next_capability_cursor"] == first_page["pagination"]["next_capability_cursor"]
    assert cursor_page["pagination"]["cursor_mode"] is True

    bad_cursor = await client.get(f"/api/projects/runs/{run['id']}/trace", params={"ingress_cursor": "not-a-cursor"})
    assert bad_cursor.status_code == 400
    assert bad_cursor.json()["error"]["code"] == "PROJECT_INVALID"

    second_page = (await client.get(f"/api/projects/runs/{run['id']}/trace", params={"limit": 2, "offset": 5})).json()
    assert len(second_page["ingress_events"]) == 1
    assert second_page["pagination"]["has_more_ingress_events"] is False


@pytest.mark.asyncio
async def test_project_run_trace_and_mutation_are_isolated_by_run_owner(client):
    from types import SimpleNamespace

    from app.common.exceptions import AppError
    from app.projects import service as project_service

    await client.post(
        "/api/projects/",
        json={
            "id": "run_isolation_page",
            "name": "运行隔离页面",
            "type": "dashboard",
            "entry_url": "https://example.com/run-isolation",
            "visibility": "company",
        },
    )
    run = (
        await client.post(
            "/api/projects/run_isolation_page/runs",
            json={"request_id": "owner-run-001", "params": {"secret_filter": "owner-only"}},
        )
    ).json()

    other_user = SimpleNamespace(
        id="other_project_user",
        username="other_project_user",
        name="其他项目用户",
        role="operator",
        department="AI小组",
        can_view_all=False,
        state="active",
        is_active=True,
    )

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        detail = await project_service.get_project(db, other_user, "run_isolation_page")
        assert detail["permissions"]["launch"] is True
        assert detail["latest_run"] is None
        assert detail["runs"] == []
        listed = await project_service.list_projects(db, other_user, include_playbooks=False)
        listed_page = next(item for item in listed["items"] if item["id"] == "run_isolation_page")
        assert listed_page["latest_run"] is None

        with pytest.raises(AppError) as read_exc:
            await project_service.get_project_run(db, other_user, run["id"])
        assert read_exc.value.code == "PROJECT_ACCESS_DENIED"

        with pytest.raises(AppError) as trace_exc:
            await project_service.get_project_run_trace(db, other_user, run["id"])
        assert trace_exc.value.code == "PROJECT_ACCESS_DENIED"

        with pytest.raises(AppError) as input_exc:
            await project_service.record_project_input_event(
                db,
                other_user,
                run["id"],
                {"request_id": "other-input", "input": {"q": "should-not-write"}},
            )
        assert input_exc.value.code == "PROJECT_ACCESS_DENIED"

        with pytest.raises(AppError) as ingest_exc:
            await project_service.ingest_project_output(
                db,
                other_user,
                run["id"],
                {"request_id": "other-ingest", "auto_analyze": False, "output": {"summary": "越权输出"}},
                auto_analyze=False,
            )
        assert ingest_exc.value.code == "PROJECT_ACCESS_DENIED"

        with pytest.raises(AppError) as capability_exc:
            await project_service.call_project_capability(
                db,
                other_user,
                run["id"],
                {"request_id": "other-cap", "capability": "ai.generate", "prompt": "越权 AI"},
            )
        assert capability_exc.value.code == "PROJECT_ACCESS_DENIED"

        owner_user = SimpleNamespace(id="admin", role="admin", department="AI小组", can_view_all=True)
        owner_run = await project_service.get_project_run(db, owner_user, run["id"])
        assert owner_run["id"] == run["id"]
        assert owner_run["input_snapshot"]["secret_filter"] == "owner-only"


@pytest.mark.asyncio
async def test_project_list_click_resume_uses_current_users_latest_visible_run(client):
    from types import SimpleNamespace

    from app.projects import service as project_service

    await client.post(
        "/api/projects/",
        json={
            "id": "resume_visible_page",
            "name": "点击续用页面",
            "type": "dashboard",
            "entry_url": "https://example.com/resume-visible",
            "visibility": "company",
        },
    )
    own_run = (
        await client.post(
            "/api/projects/resume_visible_page/runs",
            json={"request_id": "resume-own-001", "params": {"filter": "mine"}},
        )
    ).json()

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project = await db.get(Project, "resume_visible_page")
        assert project is not None
        project.owner_user_id = "project_catalog_owner"
        newer = now_bjt() + timedelta(seconds=1)
        db.add(
            ProjectRun(
                id="prun_resume_other_newer",
                project_id="resume_visible_page",
                request_id="resume-other-001",
                user_id="other_project_user",
                department="AI小组",
                status="running",
                input_snapshot={"filter": "other"},
                output_result={},
                ai_summary={},
                report_count=0,
                todo_count=0,
                capability_call_count=0,
                created_at=newer,
                started_at=newer,
                last_heartbeat_at=newer,
                updated_at=newer,
            )
        )
        await db.commit()

    current_user = SimpleNamespace(
        id="admin",
        username="admin",
        name="Admin",
        role="operator",
        department="AI小组",
        can_view_all=False,
        state="active",
        is_active=True,
    )
    async with db_mod.async_session_factory() as db:
        listed = await project_service.list_projects(db, current_user, include_playbooks=False)
        listed_page = next(item for item in listed["items"] if item["id"] == "resume_visible_page")
        assert listed_page["latest_run"]["id"] == own_run["id"]
        assert listed_page["latest_run"]["request_id"] == "resume-own-001"
        listed_running = await project_service.list_projects(db, current_user, include_playbooks=False, run_status="running")
        running_page = next(item for item in listed_running["items"] if item["id"] == "resume_visible_page")
        assert running_page["latest_run"]["id"] == own_run["id"]


@pytest.mark.asyncio
async def test_project_runtime_views_do_not_reconcile_or_count_hidden_user_runs(client):
    from types import SimpleNamespace

    from app.projects import service as project_service

    await client.post(
        "/api/projects/",
        json={
            "id": "hidden_runtime_page",
            "name": "隐藏运行态页面",
            "type": "dashboard",
            "entry_url": "https://example.com/hidden-runtime",
            "visibility": "company",
        },
    )
    owner_run = (
        await client.post(
            "/api/projects/hidden_runtime_page/runs",
            json={"request_id": "hidden-owner-run", "params": {"secret": "owner"}},
        )
    ).json()

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project_run = await db.get(ProjectRun, owner_run["id"])
        assert project_run is not None
        project_run.last_heartbeat_at = now_bjt() - timedelta(seconds=600)
        project_run.updated_at = project_run.last_heartbeat_at
        await db.commit()

    other_user = SimpleNamespace(
        id="hidden_runtime_other",
        username="hidden_runtime_other",
        name="隐藏运行态其他用户",
        role="operator",
        department="AI小组",
        can_view_all=False,
        state="active",
        is_active=True,
    )

    async with db_mod.async_session_factory() as db:
        listed = await project_service.list_projects(db, other_user, include_playbooks=False)
        listed_page = next(item for item in listed["items"] if item["id"] == "hidden_runtime_page")
        assert listed_page["latest_run"] is None
        assert listed["stats"]["running"] == 0
        listed_running = await project_service.list_projects(db, other_user, include_playbooks=False, run_status="running")
        assert listed_running["items"] == []
        assert listed_running["pagination"]["total"] == 0
        project_run = await db.get(ProjectRun, owner_run["id"])
        assert project_run is not None and project_run.status == "running"

        runtime = await project_service.get_project_runtime_status(db, other_user)
        assert runtime["projects"]["total"] >= 1
        assert runtime["runs"]["active"] == 0
        assert runtime["runs"]["stale"] == 0
        assert runtime["runs"]["waiting_ai"] == 0
        assert all(item["project_run_id"] != owner_run["id"] for item in runtime["recent_errors"])


@pytest.mark.asyncio
async def test_project_run_create_request_id_is_idempotent(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "open_idem_page",
            "name": "打开幂等页面",
            "type": "external_web",
            "entry_url": "https://example.com/open-idem",
        },
    )
    payload = {"request_id": "open-req-001", "params": {"source": "project_page"}}
    first = (await client.post("/api/projects/open_idem_page/runs", json=payload)).json()
    second = (await client.post("/api/projects/open_idem_page/runs", json=payload)).json()
    assert first["id"] == second["id"]
    assert first["execution_run_id"] == second["execution_run_id"]
    assert first["request_id"] == "open-req-001"
    assert second["deduped"] is True

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project_runs = (
            await db.execute(select(ProjectRun).where(ProjectRun.project_id == "open_idem_page"))
        ).scalars().all()
        execution_runs = (
            await db.execute(select(ExecutionRun).where(ExecutionRun.business_ref_id == "open_idem_page"))
        ).scalars().all()
        learning_events = (
            await db.execute(
                select(LearningEvent).where(LearningEvent.source_type == "project_run", LearningEvent.source_id == first["id"])
            )
        ).scalars().all()

    assert len(project_runs) == 1
    assert len(execution_runs) == 1
    assert len(learning_events) == 1
    assert learning_events[0].event_type == "project.run.opened"


@pytest.mark.asyncio
async def test_project_run_heartbeat_records_liveness_and_preserves_terminal_status(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "heartbeat_page",
            "name": "心跳页面",
            "type": "external_web",
            "entry_url": "https://example.com/heartbeat",
        },
    )
    run = (await client.post("/api/projects/heartbeat_page/runs", json={})).json()
    assert run["status"] == "running"
    assert run["last_heartbeat_at"]

    heartbeat = (
        await client.post(
            f"/api/projects/runs/{run['id']}/heartbeat",
            json={"metadata": {"cookie": "secret-cookie", "visible": True}},
        )
    ).json()
    assert heartbeat["status"] == "running"
    assert heartbeat["last_heartbeat_at"]

    ingested = (
        await client.post(
            f"/api/projects/runs/{run['id']}/ingest",
            json={"auto_analyze": False, "output": {"summary": "已完成"}},
        )
    ).json()
    assert ingested["status"] == "completed"

    terminal_heartbeat = (
        await client.post(
            f"/api/projects/runs/{run['id']}/heartbeat",
            json={"status": "running", "metadata": {"token": "secret-token"}},
        )
    ).json()
    assert terminal_heartbeat["status"] == "completed"
    assert terminal_heartbeat["last_heartbeat_at"]

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        execution = await db.get(ExecutionRun, run["execution_run_id"])
        project_run = await db.get(ProjectRun, run["id"])

    assert project_run is not None and project_run.last_heartbeat_at is not None
    assert execution is not None
    assert execution.metadata_json["last_project_heartbeat_at"]
    assert execution.metadata_json["heartbeat_payload"]["token"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_project_run_close_releases_capacity_and_records_execution_learning(client, monkeypatch):
    from app.projects import service as project_service

    monkeypatch.setattr(project_service.settings, "PROJECT_TARGET_CONCURRENT_RUNS", 1)
    await client.post(
        "/api/projects/",
        json={
            "id": "close_capacity_page",
            "name": "关闭释放容量页面",
            "type": "external_web",
            "entry_url": "https://example.com/close-capacity",
        },
    )
    first = (
        await client.post(
            "/api/projects/close_capacity_page/runs",
            json={"request_id": "close-open-1"},
        )
    ).json()
    blocked = await client.post(
        "/api/projects/close_capacity_page/runs",
        json={"request_id": "close-open-blocked"},
    )
    assert blocked.status_code == 429

    closed = (
        await client.post(
            f"/api/projects/runs/{first['id']}/close",
            json={"metadata": {"source": "test_close", "token": "secret-token"}},
        )
    ).json()
    assert closed["id"] == first["id"]
    assert closed["status"] == "completed"
    assert closed["closed"] is True
    assert closed["previous_status"] == "running"
    assert closed["completed_at"]

    heartbeat = (
        await client.post(
            f"/api/projects/runs/{first['id']}/heartbeat",
            json={"status": "running", "metadata": {"source": "late_heartbeat"}},
        )
    ).json()
    assert heartbeat["status"] == "completed"

    second = (
        await client.post(
            "/api/projects/close_capacity_page/runs",
            json={"request_id": "close-open-2"},
        )
    ).json()
    assert second["status"] == "running"

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project_run = await db.get(ProjectRun, first["id"])
        execution = await db.get(ExecutionRun, first["execution_run_id"])
        events = (
            await db.execute(
                select(LearningEvent).where(
                    LearningEvent.source_type == "execution_run",
                    LearningEvent.source_id == first["execution_run_id"],
                    LearningEvent.event_type == "skill.run.completed",
                )
            )
        ).scalars().all()

    assert project_run is not None and project_run.status == "completed"
    assert execution is not None and execution.status == "completed"
    assert execution.metadata_json["last_project_close_released_capacity"] is True
    assert execution.metadata_json["close_payload"]["token"] == "[REDACTED]"
    assert len(events) == 1


@pytest.mark.asyncio
async def test_project_reconciles_stale_runs_and_heartbeat_restores(client):
    from datetime import timedelta

    from app.common.time_utils import now_bjt

    await client.post(
        "/api/projects/",
        json={
            "id": "stale_page",
            "name": "失活页面",
            "type": "external_web",
            "entry_url": "https://example.com/stale",
        },
    )
    run = (await client.post("/api/projects/stale_page/runs", json={})).json()

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project_run = await db.get(ProjectRun, run["id"])
        assert project_run is not None
        project_run.last_heartbeat_at = now_bjt() - timedelta(seconds=600)
        project_run.updated_at = project_run.last_heartbeat_at
        await db.commit()

    listed = (
        await client.get(
            "/api/projects/",
            params={"include_playbooks": "false", "run_status": "stale"},
        )
    ).json()
    assert listed["pagination"]["total"] == 1
    assert listed["items"][0]["latest_run"]["status"] == "stale"
    assert listed["items"][0]["latest_run"]["liveness"] == "stale"
    assert listed["stats"]["stale"] == 1

    async with db_mod.async_session_factory() as db:
        execution = await db.get(ExecutionRun, run["execution_run_id"])
        project_run = await db.get(ProjectRun, run["id"])

    assert project_run is not None and project_run.status == "stale"
    assert execution is not None and execution.status == "stale"
    assert execution.metadata_json["project_heartbeat_stale_after_seconds"] == 120

    restored = (
        await client.post(
            f"/api/projects/runs/{run['id']}/heartbeat",
            json={"status": "running", "metadata": {"tab": "restored"}},
        )
    ).json()
    assert restored["status"] == "running"
    assert restored["liveness"] == "online"

    async with db_mod.async_session_factory() as db:
        execution = await db.get(ExecutionRun, run["execution_run_id"])

    assert execution is not None and execution.status == "running"
    assert execution.metadata_json["last_project_heartbeat_restored"] is True


@pytest.mark.asyncio
async def test_project_ingest_request_id_is_idempotent(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "idem_page",
            "name": "幂等页面",
            "type": "external_web",
            "entry_url": "https://example.com/idem",
        },
    )
    run = (await client.post("/api/projects/idem_page/runs", json={})).json()
    payload = {
        "request_id": "ingest-001",
        "auto_analyze": False,
        "output": {"summary": "重复回传只应记录一次"},
        "reports": [{"title": "幂等报告", "summary": "一次"}],
        "todos": [{"title": "幂等待办"}],
    }
    first = (await client.post(f"/api/projects/runs/{run['id']}/ingest", json=payload)).json()
    second = (await client.post(f"/api/projects/runs/{run['id']}/ingest", json=payload)).json()
    assert first.get("deduped") is not True
    assert second["deduped"] is True

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        events = (
            await db.execute(
                select(ProjectIngressEvent).where(ProjectIngressEvent.project_run_id == run["id"])
            )
        ).scalars().all()
        project_run = await db.get(ProjectRun, run["id"])

    assert len(events) == 1
    assert events[0].request_id == "ingest-001"
    assert project_run is not None
    assert project_run.report_count == 1
    assert project_run.todo_count == 1


@pytest.mark.asyncio
async def test_project_ingest_auto_analyze_binds_to_ingress_event_and_dedupes(client, monkeypatch):
    from app.projects import service as project_service

    call_count = 0

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        nonlocal call_count
        call_count += 1
        assert args["context_pack"]["project"]["id"] == "ingress_ai_page"
        assert effective_run_id
        return {
            "ok": True,
            "model": "ingress-ai-model",
            "output": "项目输出已进入 AI 循环",
            "prompt_hash": "ingress-ai-hash",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)
    await client.post(
        "/api/projects/",
        json={
            "id": "ingress_ai_page",
            "name": "输出自动分析页面",
            "type": "external_web",
            "entry_url": "https://example.com/ingress-ai",
        },
    )
    run = (await client.post("/api/projects/ingress_ai_page/runs", json={})).json()
    payload = {
        "request_id": "ingress-ai-001",
        "output": {"summary": "需要进入 AI 循环"},
        "reports": [{"title": "AI 报告", "summary": "自动分析"}],
    }
    first = (await client.post(f"/api/projects/runs/{run['id']}/ingest", json=payload)).json()
    second = (await client.post(f"/api/projects/runs/{run['id']}/ingest", json=payload)).json()

    assert first["status"] == "ai_completed"
    assert first["analysis"]["output"] == "项目输出已进入 AI 循环"
    assert second["deduped"] is True
    assert second["analysis"]["deduped"] is True
    assert call_count == 1

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        events = (
            await db.execute(select(ProjectIngressEvent).where(ProjectIngressEvent.project_run_id == run["id"]))
        ).scalars().all()
        calls = (
            await db.execute(select(ProjectCapabilityCall).where(ProjectCapabilityCall.project_run_id == run["id"]))
        ).scalars().all()

    assert len(events) == 1
    assert len(calls) == 1
    assert calls[0].request_id == f"ingress:{events[0].id}:analyze"
    assert calls[0].capability_key == "ai.analyze"


@pytest.mark.asyncio
async def test_project_ingest_rejects_gateway_payload_over_limit(client, monkeypatch):
    from app.projects import service as project_service

    monkeypatch.setattr(project_service, "PROJECT_INGEST_MAX_BYTES", 128)
    await client.post(
        "/api/projects/",
        json={
            "id": "ingest_limit_page",
            "name": "载荷限制页面",
            "type": "external_web",
            "entry_url": "https://example.com/limit",
        },
    )
    run = (await client.post("/api/projects/ingest_limit_page/runs", json={})).json()
    resp = await client.post(
        f"/api/projects/runs/{run['id']}/ingest",
        json={"request_id": "ingest-too-large", "auto_analyze": False, "output": {"summary": "x" * 300}},
    )
    assert resp.status_code == 413
    body = resp.json()["error"]
    assert body["code"] == "PROJECT_PAYLOAD_TOO_LARGE"
    assert body["detail"]["reason"] == "ingest_bytes"
    assert body["detail"]["ingress_event_status"] == "failed"
    failed_event_id = body["detail"]["event_id"]
    replay = await client.post(
        f"/api/projects/runs/{run['id']}/ingest",
        json={"request_id": "ingest-too-large", "auto_analyze": False, "output": {"summary": "x" * 300}},
    )
    assert replay.status_code == 413
    replay_body = replay.json()["error"]
    assert replay_body["detail"]["event_id"] == failed_event_id
    assert replay_body["detail"]["deduped"] is True

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        events = (
            await db.execute(
                select(ProjectIngressEvent).where(ProjectIngressEvent.project_run_id == run["id"])
            )
        ).scalars().all()
        project_run = await db.get(ProjectRun, run["id"])
        learning_events = (
            await db.execute(
                select(LearningEvent).where(LearningEvent.source_type == "project_ingress_event")
            )
        ).scalars().all()

    assert len(events) == 1
    assert events[0].id == failed_event_id
    assert events[0].request_id == "ingest-too-large"
    assert events[0].output_result["status"] == "failed"
    assert events[0].metadata_json["status"] == "failed"
    assert events[0].metadata_json["payload_reason"] == "ingest_bytes"
    assert "x" * 50 not in str(events[0].output_result)
    assert any(
        event.source_id == str(failed_event_id)
        and event.event_type == "project.ingress.received"
        and event.metadata_json["gateway_status"] == "failed"
        for event in learning_events
    )
    assert project_run is not None and project_run.status == "running"
    trace = (await client.get(f"/api/projects/runs/{run['id']}/trace")).json()
    assert trace["counts"]["ingress_events_total"] == 1
    assert trace["ingress_events"][0]["metadata"]["status"] == "failed"
    assert trace["timeline"][0]["title"] == "输出失败 · ingest_bytes"


@pytest.mark.asyncio
async def test_project_ingest_rejects_report_todo_proof_count_over_limit(client, monkeypatch):
    from app.projects import service as project_service

    monkeypatch.setattr(project_service, "PROJECT_MAX_REPORTS_PER_INGEST", 1)
    await client.post(
        "/api/projects/",
        json={
            "id": "ingest_count_limit_page",
            "name": "数量限制页面",
            "type": "external_web",
            "entry_url": "https://example.com/count-limit",
        },
    )
    run = (await client.post("/api/projects/ingest_count_limit_page/runs", json={})).json()
    resp = await client.post(
        f"/api/projects/runs/{run['id']}/ingest",
        json={
            "auto_analyze": False,
            "output": {"summary": "ok"},
            "reports": [{"title": "r1"}, {"title": "r2"}],
        },
    )
    assert resp.status_code == 413
    body = resp.json()["error"]
    assert body["code"] == "PROJECT_PAYLOAD_TOO_LARGE"
    assert body["detail"]["reason"] == "reports_count"
    assert body["detail"]["max_count"] == 1


@pytest.mark.asyncio
async def test_project_capability_call_routes_platform_ai_and_records_gateway(client, monkeypatch):
    from app.projects import service as project_service

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        assert effective_skill_id is None
        assert effective_run_id
        assert args["context_pack"]["capability"]["credential_location"] == "platform_only"
        assert args["context_pack"]["project"]["id"] == "ai_page"
        return {
            "ok": True,
            "model": "test-platform-model",
            "output": "平台 AI 已接管生成",
            "prompt_hash": "hash-ai",
            "credential_location": "platform_only",
        }

    call_count = 0

    async def counted_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        nonlocal call_count
        call_count += 1
        return await fake_platform_ai(db, user, args, effective_skill_id=effective_skill_id, effective_run_id=effective_run_id)

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", counted_platform_ai)
    await client.post(
        "/api/projects/",
        json={
            "id": "ai_page",
            "name": "AI 页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/ai",
            "metadata": {"capabilities": ["ai.generate"]},
        },
    )
    run = (await client.post("/api/projects/ai_page/runs", json={"params": {"topic": "稳定性"}})).json()
    result = (
        await client.post(
            f"/api/projects/runs/{run['id']}/capability",
            json={"request_id": "cap-001", "capability": "ai.generate", "prompt": "生成稳定性建议", "input": {"tone": "direct"}},
        )
    ).json()
    duplicate = (
        await client.post(
            f"/api/projects/runs/{run['id']}/capability",
            json={"request_id": "cap-001", "capability": "ai.generate", "prompt": "生成稳定性建议", "input": {"tone": "direct"}},
        )
    ).json()
    assert result["ok"] is True
    assert result["capability"] == "ai.generate"
    assert result["result"]["credential_location"] == "platform_only"
    assert result["result"]["output"] == "平台 AI 已接管生成"
    assert duplicate["deduped"] is True
    assert duplicate["result"]["output"] == "平台 AI 已接管生成"
    assert call_count == 1

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project_run = await db.get(ProjectRun, run["id"])
        calls = (
            await db.execute(
                select(ProjectCapabilityCall).where(ProjectCapabilityCall.project_run_id == run["id"])
            )
        ).scalars().all()

    assert project_run is not None
    assert project_run.capability_call_count == 1
    assert len(calls) == 1
    assert calls[0].request_id == "cap-001"
    assert calls[0].capability_key == "ai.generate"
    assert calls[0].status == "completed"
    assert calls[0].input_payload["prompt"] == "生成稳定性建议"
    assert calls[0].input_payload["input"] == {"tone": "direct"}
    assert calls[0].output_result["output"] == "平台 AI 已接管生成"
    assert calls[0].output_summary["model"] == "test-platform-model"

    run_detail = (await client.get(f"/api/projects/runs/{run['id']}")).json()
    latest = run_detail["latest_capability_call"]
    assert latest["request_id"] == "cap-001"
    assert latest["capability_key"] == "ai.generate"
    assert latest["capability_type"] == "ai"
    assert latest["status"] == "completed"
    assert latest["output_summary"]["model"] == "test-platform-model"
    assert isinstance(latest["latency_ms"], int)
    assert "input_payload" not in latest
    assert "output_result" not in latest


@pytest.mark.asyncio
async def test_project_capability_routes_tmall_cached_data_from_project_gateway(client, monkeypatch):
    from app.projects import service as project_service

    captured: dict[str, tuple[dict, str | None]] = {}

    async def fake_latest(db, user, args, *, capability_key=None):
        captured["latest"] = (args, capability_key)
        return {
            "capability": capability_key,
            "platform": "tmall",
            "data_scope": "tmall.link_decline.cached_artifacts",
            "kind": "raw-output",
            "count": 1,
            "items": [{"run_id": "tmall-data-run-1", "kind": "raw-output", "summary": {"rows": 88}}],
        }

    async def fake_get(db, user, args, *, capability_key=None):
        captured["get"] = (args, capability_key)
        return {
            "capability": {"capability": capability_key, "platform": "tmall"},
            "artifact": {"run_id": "tmall-data-run-1", "kind": "raw-output"},
            "include_content": True,
            "content_omitted": False,
            "content_json": {"rows": [{"item_id": "item-1", "decline_rate": 0.31}]},
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_data_capability_latest", fake_latest)
    monkeypatch.setattr(project_service.codex_service, "_builtin_data_artifact_get", fake_get)
    await client.post(
        "/api/projects/",
        json={
            "id": "tmall_cached_data_page",
            "name": "天猫缓存数据页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/tmall",
            "metadata": {
                "capabilities": [
                    "tmall.link_decline.raw_collection",
                    "skillforge_tmall_link_decline_data_get",
                ]
            },
        },
    )
    run = (await client.post("/api/projects/tmall_cached_data_page/runs", json={})).json()

    latest_resp = await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={
            "request_id": "tmall-latest-001",
            "capability": "mcp://tmall.link_decline.raw_collection",
            "input": {"limit": 1},
        },
    )
    assert latest_resp.status_code == 200, latest_resp.text
    latest = latest_resp.json()
    assert latest["capability"] == "mcp://skillforge_tmall_link_decline_data_latest"
    assert latest["result"]["tool"] == "skillforge_tmall_link_decline_data_latest"
    assert latest["result"]["proof"]["gateway"] == "skillforge_project_gateway"
    assert latest["result"]["proof"]["data_scope"] == "tmall.link_decline.cached_artifacts"
    assert latest["result"]["data"]["items"][0]["run_id"] == "tmall-data-run-1"
    assert captured["latest"] == ({"limit": 1}, "tmall.link_decline.raw_collection")

    get_resp = await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={
            "request_id": "tmall-get-001",
            "capability": "skillforge_tmall_link_decline_data_get",
            "input": {"run_id": "tmall-data-run-1", "include_content": True},
        },
    )
    assert get_resp.status_code == 200, get_resp.text
    get_result = get_resp.json()
    assert get_result["capability"] == "mcp://skillforge_tmall_link_decline_data_get"
    assert get_result["result"]["tool"] == "skillforge_tmall_link_decline_data_get"
    assert get_result["result"]["data"]["content_json"]["rows"][0]["item_id"] == "item-1"
    assert captured["get"] == (
        {"run_id": "tmall-data-run-1", "include_content": True, "max_bytes": 5 * 1024 * 1024},
        "tmall.link_decline.raw_collection",
    )

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        calls = (
            await db.execute(
                select(ProjectCapabilityCall)
                .where(ProjectCapabilityCall.project_run_id == run["id"])
                .order_by(ProjectCapabilityCall.id.asc())
            )
        ).scalars().all()

    assert [call.capability_key for call in calls] == [
        "mcp://skillforge_tmall_link_decline_data_latest",
        "mcp://skillforge_tmall_link_decline_data_get",
    ]
    assert all(call.status == "completed" and call.capability_type == "mcp_read" for call in calls)


@pytest.mark.asyncio
async def test_project_service_invoke_returns_tmall_cached_data_for_browser_adapter(client, monkeypatch):
    from app.projects import service as project_service

    captured: dict[str, tuple[dict, str | None]] = {}

    async def fake_latest(db, user, args, *, capability_key=None):
        captured["latest"] = (args, capability_key)
        return {
            "capability": capability_key,
            "platform": "tmall",
            "data_scope": "tmall.link_decline.cached_artifacts",
            "kind": "raw-output",
            "count": 1,
            "items": [{"id": 376, "run_id": "tmall-data-run-1", "kind": "raw-output"}],
        }

    async def fake_get(db, user, args, *, capability_key=None):
        captured["get"] = (args, capability_key)
        return {
            "capability": {"capability": capability_key, "platform": "tmall"},
            "artifact": {"id": 376, "run_id": "tmall-data-run-1", "kind": "raw-output"},
            "include_content": True,
            "max_bytes": args.get("max_bytes"),
            "content_omitted": False,
            "content_json": {"collection_schema": "tmall_link_decline_collection_v1", "rows": [{"item_id": "item-1"}]},
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_data_capability_latest", fake_latest)
    monkeypatch.setattr(project_service.codex_service, "_builtin_data_artifact_get", fake_get)
    await client.post(
        "/api/projects/",
        json={
            "id": "tmall_service_data_page",
            "name": "天猫服务化数据页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/tmall-service",
            "metadata": {
                "capabilities": [
                    "skillforge_tmall_link_decline_data_latest",
                    "skillforge_tmall_link_decline_data_get",
                ]
            },
        },
    )

    latest_resp = await client.post(
        "/api/projects/tmall_service_data_page/service/invoke",
        json={
            "request_id": "svc-tmall-latest",
            "capability": "skillforge_tmall_link_decline_data_latest",
            "input": {},
            "auto_analyze": False,
        },
    )
    assert latest_resp.status_code == 200, latest_resp.text
    latest = latest_resp.json()
    assert latest["ok"] is True
    assert latest["capability"]["key"] == "mcp://skillforge_tmall_link_decline_data_latest"
    assert latest["data"]["tool"] == "skillforge_tmall_link_decline_data_latest"
    assert latest["data"]["data"]["items"][0]["run_id"] == "tmall-data-run-1"
    assert latest["data"]["proof"]["gateway"] == "skillforge_project_gateway"
    assert captured["latest"][1] == "tmall.link_decline.raw_collection"

    get_resp = await client.post(
        "/api/projects/tmall_service_data_page/service/invoke",
        json={
            "request_id": "svc-tmall-get",
            "capability": "skillforge_tmall_link_decline_data_get",
            "input": {"run_id": "tmall-data-run-1", "include_content": True},
            "auto_analyze": False,
        },
    )
    assert get_resp.status_code == 200, get_resp.text
    get_result = get_resp.json()
    assert get_result["ok"] is True
    assert get_result["data"]["tool"] == "skillforge_tmall_link_decline_data_get"
    assert get_result["data"]["data"]["content_json"]["collection_schema"] == "tmall_link_decline_collection_v1"
    get_args, get_capability = captured["get"]
    assert get_capability == "tmall.link_decline.raw_collection"
    assert get_args["run_id"] == "tmall-data-run-1"
    assert get_args["include_content"] is True
    assert get_args["max_bytes"] == 5 * 1024 * 1024


@pytest.mark.asyncio
async def test_project_asset_latest_analysis_uses_platform_tmall_cache(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.execution.artifact_service import persist_execution_artifact
    from app.execution.models import RUN_MODE_SCHEDULED_REAL
    from app.skills.models import Skill

    monkeypatch.setattr(settings, "EXECUTION_ARTIFACT_ROOT", str(tmp_path / "execution-artifacts"))
    created = await client.post(
        "/api/projects/",
        json={
            "id": "samplebrand-tmall-link-health-dashboard-v1",
            "name": "示例品牌天猫链接健康日报",
            "type": "web_static",
            "entry_url": "https://example.com/tmall",
            "metadata": {"capabilities": ["tmall.link_decline.raw_collection"]},
        },
    )
    assert created.status_code == 200, created.text

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        db.add(
            Skill(
                id="tmall-link-decline-collector-v1",
                name="天猫链接下滑采集",
                department="AI小组",
                visibility="company",
                status="active",
            )
        )
        db.add(
            ExecutionRun(
                id="tmall-cache-run-latest",
                skill_id="tmall-link-decline-collector-v1",
                run_mode=RUN_MODE_SCHEDULED_REAL,
                status="completed",
                completed_at=now_bjt(),
            )
        )
        await db.flush()
        await persist_execution_artifact(
            db,
            run_id="tmall-cache-run-latest",
            skill_id="tmall-link-decline-collector-v1",
            kind="raw-output",
            payload={
                "collection_schema": "tmall_link_decline_collection_v1",
                "异常商品清单": [
                        {
                            "item_id": "8001",
                            "title": "示例品牌测试链接",
                            "conversion_rate_change_pct": -0.42,
                            "pay_amount_pool": True,
                        }
                ],
            },
        )
        await db.commit()

    resp = await client.get("/api/projects/assets/samplebrand-tmall-link-health-dashboard-v1/testv/web/latest-analysis.json")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generated_by"] == "skillforge_project_gateway"
    assert body["source"]["run_id"] == "tmall-cache-run-latest"
    assert body["output"]["source_label"] == "SkillForge 平台最新采集缓存"
    assert body["output"]["findings"][0]["item_id"] == "8001"
    assert body["output"]["findings"][0]["severity"] == "red"
    assert "成交/转化异常波动" in body["output"]["findings"][0]["reason_labels"]


@pytest.mark.asyncio
async def test_project_capability_routes_tmall_link_health_latest_analysis(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.execution.artifact_service import persist_execution_artifact
    from app.execution.models import RUN_MODE_SCHEDULED_REAL
    from app.skills.models import Skill

    monkeypatch.setattr(settings, "EXECUTION_ARTIFACT_ROOT", str(tmp_path / "execution-artifacts"))
    await client.post(
        "/api/projects/",
        json={
            "id": "tmall_latest_analysis_page",
            "name": "天猫最新分析页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/tmall-analysis",
            "metadata": {"capabilities": ["tmall_link_health_latest_analysis"]},
        },
    )

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        db.add(
            Skill(
                id="tmall-link-decline-collector-v1",
                name="天猫链接下滑采集",
                department="AI小组",
                visibility="company",
                status="active",
            )
        )
        db.add(
            ExecutionRun(
                id="tmall-capability-cache-run",
                skill_id="tmall-link-decline-collector-v1",
                run_mode=RUN_MODE_SCHEDULED_REAL,
                status="completed",
                completed_at=now_bjt(),
            )
        )
        await db.flush()
        await persist_execution_artifact(
            db,
            run_id="tmall-capability-cache-run",
            skill_id="tmall-link-decline-collector-v1",
            kind="raw-output",
            payload={
                "collection_schema": "tmall_link_decline_collection_v1",
                "异常商品清单": [{"item_id": "9001", "title": "能力测试链接", "conversion_change_rate": -0.5}],
            },
        )
        await db.commit()

    run = (await client.post("/api/projects/tmall_latest_analysis_page/runs", json={})).json()
    resp = await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={"request_id": "latest-analysis-001", "capability": "tmall_link_health_latest_analysis", "input": {}},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["result"]["tool"] == "tmall_link_health_latest_analysis"
    assert result["result"]["data"]["source"]["run_id"] == "tmall-capability-cache-run"
    assert result["result"]["data"]["output"]["findings"][0]["item_id"] == "9001"


@pytest.mark.asyncio
async def test_project_capability_cheap_ai_uses_cheap_model_profile(client, monkeypatch):
    from app.projects import service as project_service

    captured: dict[str, str] = {}

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        captured["model_profile"] = args.get("model_profile")
        return {
            "ok": True,
            "model": "cheap-test-model",
            "model_profile": args.get("model_profile") or "default",
            "output": "低成本模型结果",
            "prompt_hash": "hash-cheap",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)
    await client.post(
        "/api/projects/",
        json={
            "id": "cheap_ai_page",
            "name": "便宜模型页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/cheap",
            "metadata": {"capabilities": ["ai.cheap.generate"]},
        },
    )
    run = (await client.post("/api/projects/cheap_ai_page/runs", json={})).json()
    resp = await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={"request_id": "cheap-001", "capability": "ai.cheap.generate", "prompt": "低成本总结"},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert captured["model_profile"] == "cheap"
    assert result["result"]["model"] == "cheap-test-model"
    assert result["result"]["model_profile"] == "cheap"


@pytest.mark.asyncio
async def test_project_ai_gateway_metadata_can_select_flash_model_profile(client, monkeypatch):
    from app.projects import service as project_service

    captured: list[dict] = []

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        context_pack = args["context_pack"]
        capability_input = context_pack.get("capability_input") or {}
        if context_pack["capability"]["key"] == "ai.analyze":
            assert "capability_input" in args["prompt"]
            assert "effective_depth" in args["prompt"]
            assert "good.mp4" in args["prompt"]
        captured.append(
            {
                "capability": context_pack["capability"]["key"],
                "model_profile": args.get("model_profile"),
                "analysis_depth": capability_input.get("analysis_depth"),
            }
        )
        return {
            "ok": True,
            "model": "deepseek-v4-flash",
            "model_profile": args.get("model_profile") or "default",
            "output": "flash 模型结果",
            "prompt_hash": f"hash-{len(captured)}",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)
    await client.post(
        "/api/projects/",
        json={
            "id": "flash_ai_page",
            "name": "Flash AI 页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/flash",
            "metadata": {
                "capabilities": ["ai.generate"],
                "ai_gateway": {"model_profile": "deepseek-v4-flash"},
            },
        },
    )
    run = (await client.post("/api/projects/flash_ai_page/runs", json={})).json()

    generate_resp = await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={"request_id": "flash-generate-001", "capability": "ai.generate", "prompt": "用 flash 生成"},
    )
    assert generate_resp.status_code == 200, generate_resp.text
    assert generate_resp.json()["result"]["model"] == "deepseek-v4-flash"
    assert generate_resp.json()["result"]["model_profile"] == "flash"

    analyze_resp = await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={
            "request_id": "flash-analyze-001",
            "capability": "ai.analyze",
            "prompt": "用 flash 分析",
            "input": {
                "analysis_depth": {"selected_mode": "auto", "effective_depth": "deep"},
                "videos": [{"role": "good_video", "file_name": "good.mp4"}],
            },
        },
    )
    assert analyze_resp.status_code == 200, analyze_resp.text
    assert analyze_resp.json()["ok"] is True, analyze_resp.json()
    assert analyze_resp.json()["model"] == "deepseek-v4-flash"
    assert analyze_resp.json()["model_profile"] == "flash"

    assert captured == [
        {"capability": "ai.generate", "model_profile": "flash", "analysis_depth": None},
        {
            "capability": "ai.analyze",
            "model_profile": "flash",
            "analysis_depth": {"selected_mode": "auto", "effective_depth": "deep"},
        },
    ]


@pytest.mark.asyncio
async def test_project_analyze_request_id_is_idempotent_and_records_single_ai_call(client, monkeypatch):
    from app.projects import service as project_service

    call_count = 0

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        nonlocal call_count
        call_count += 1
        assert args["context_pack"]["project"]["id"] == "analyze_idem_page"
        assert effective_run_id
        return {
            "ok": True,
            "model": "analyze-model",
            "output": "分析完成",
            "prompt_hash": "analyze-hash",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)
    await client.post(
        "/api/projects/",
        json={
            "id": "analyze_idem_page",
            "name": "分析幂等页面",
            "type": "dashboard",
            "entry_url": "https://example.com/analyze-idem",
        },
    )
    run = (await client.post("/api/projects/analyze_idem_page/runs", json={"params": {"q": "today"}})).json()
    first = (
        await client.post(
            f"/api/projects/runs/{run['id']}/analyze",
            json={"request_id": "analyze-001", "prompt": "分析项目输出"},
        )
    ).json()
    second = (
        await client.post(
            f"/api/projects/runs/{run['id']}/analyze",
            json={"request_id": "analyze-001", "prompt": "分析项目输出"},
        )
    ).json()
    assert first["ok"] is True
    assert first["output"] == "分析完成"
    assert second["deduped"] is True
    assert second["output"] == "分析完成"
    assert isinstance(first["call_id"], int)
    assert second["call_id"] == first["call_id"]
    assert call_count == 1

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project_run = await db.get(ProjectRun, run["id"])
        calls = (
            await db.execute(
                select(ProjectCapabilityCall).where(ProjectCapabilityCall.project_run_id == run["id"])
            )
        ).scalars().all()
        events = (
            await db.execute(
                select(LearningEvent).where(LearningEvent.source_type == "project_capability_call")
            )
        ).scalars().all()

    assert project_run is not None
    assert project_run.capability_call_count == 1
    assert project_run.ai_summary["output"] == "分析完成"
    assert len(calls) == 1
    assert calls[0].request_id == "analyze-001"
    assert calls[0].capability_key == "ai.analyze"
    assert calls[0].status == "completed"
    assert any(event.source_id == str(calls[0].id) and event.event_type == "project.capability.completed" for event in events)


@pytest.mark.asyncio
async def test_project_capability_request_id_lock_dedupes_concurrent_ai_calls(client, monkeypatch):
    from app.projects import service as project_service

    call_count = 0

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return {
            "ok": True,
            "model": "concurrent-model",
            "output": f"并发 AI 输出：{args['prompt']}",
            "prompt_hash": "concurrent-hash",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)
    await client.post(
        "/api/projects/",
        json={
            "id": "cap_lock_page",
            "name": "能力锁页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/cap-lock",
            "metadata": {"capabilities": ["ai.generate"]},
        },
    )
    run = (await client.post("/api/projects/cap_lock_page/runs", json={"request_id": "cap-lock-open"})).json()

    async def call_ai():
        return await client.post(
            f"/api/projects/runs/{run['id']}/capability",
            json={
                "request_id": "cap-lock-001",
                "capability": "ai.generate",
                "prompt": "只应调用一次模型",
            },
        )

    first, second = await asyncio.gather(call_ai(), call_ai())
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    bodies = [first.json(), second.json()]
    assert call_count == 1
    assert any(body.get("deduped") is True for body in bodies)
    assert len({body.get("call_id") for body in bodies}) == 1

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project_run = await db.get(ProjectRun, run["id"])
        calls = (
            await db.execute(
                select(ProjectCapabilityCall).where(ProjectCapabilityCall.project_run_id == run["id"])
            )
        ).scalars().all()

    assert project_run is not None
    assert project_run.capability_call_count == 1
    assert len(calls) == 1
    assert calls[0].request_id == "cap-lock-001"
    assert calls[0].status == "completed"


@pytest.mark.asyncio
async def test_project_capability_rejects_input_over_limit_and_quota(client, monkeypatch):
    from app.projects import service as project_service

    await client.post(
        "/api/projects/",
        json={
            "id": "cap_limit_page",
            "name": "能力限制页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/cap-limit",
            "metadata": {"capabilities": ["ai.generate"]},
        },
    )
    run = (await client.post("/api/projects/cap_limit_page/runs", json={})).json()

    monkeypatch.setattr(project_service, "PROJECT_CAPABILITY_MAX_INPUT_BYTES", 64)
    oversized = await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={
            "request_id": "cap-too-large",
            "capability": "ai.generate",
            "prompt": "ok",
            "input": {"blob": "x" * 200},
        },
    )
    assert oversized.status_code == 413
    oversized_body = oversized.json()["error"]
    assert oversized_body["code"] == "PROJECT_PAYLOAD_TOO_LARGE"
    assert oversized_body["detail"]["reason"] == "capability_input_bytes"
    assert oversized_body["detail"]["capability_call_status"] == "failed"
    oversized_call_id = oversized_body["detail"]["call_id"]
    oversized_replay = await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={
            "request_id": "cap-too-large",
            "capability": "ai.generate",
            "prompt": "ok",
            "input": {"blob": "x" * 200},
        },
    )
    assert oversized_replay.status_code == 413
    oversized_replay_body = oversized_replay.json()["error"]
    assert oversized_replay_body["code"] == "PROJECT_PAYLOAD_TOO_LARGE"
    assert oversized_replay_body["detail"]["call_id"] == oversized_call_id
    assert oversized_replay_body["detail"]["deduped"] is True

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project_run = await db.get(ProjectRun, run["id"])
        assert project_run is not None
        project_run.capability_call_count = 1
        await db.commit()

    monkeypatch.setattr(project_service, "PROJECT_CAPABILITY_MAX_INPUT_BYTES", 4096)
    monkeypatch.setattr(project_service, "PROJECT_CAPABILITY_MAX_CALLS_PER_RUN", 1)
    quota = await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={
            "request_id": "cap-quota",
            "capability": "ai.generate",
            "prompt": "quota",
            "input": {"ok": True},
        },
    )
    assert quota.status_code == 429
    quota_body = quota.json()["error"]
    assert quota_body["code"] == "PROJECT_CAPABILITY_DENIED"
    assert quota_body["detail"]["max_calls_per_run"] == 1
    quota_replay = await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={
            "request_id": "cap-quota",
            "capability": "ai.generate",
            "prompt": "quota",
            "input": {"ok": True},
        },
    )
    assert quota_replay.status_code == 429
    assert quota_replay.json()["error"]["detail"]["deduped"] is True

    async with db_mod.async_session_factory() as db:
        project_run = await db.get(ProjectRun, run["id"])
        calls = (
            await db.execute(
                select(ProjectCapabilityCall)
                .where(ProjectCapabilityCall.project_run_id == run["id"])
                .order_by(ProjectCapabilityCall.created_at.asc(), ProjectCapabilityCall.id.asc())
            )
        ).scalars().all()
        events = (
            await db.execute(
                select(LearningEvent).where(LearningEvent.source_type == "project_capability_call")
            )
        ).scalars().all()

    assert project_run is not None
    assert project_run.capability_call_count == 2
    assert len(calls) == 2
    over_limit_call = next(call for call in calls if call.request_id == "cap-too-large")
    quota_call = next(call for call in calls if call.request_id == "cap-quota")
    assert over_limit_call.id == oversized_call_id
    assert over_limit_call.status == "failed"
    assert over_limit_call.input_payload == {}
    assert over_limit_call.input_summary["reason"] == "capability_payload_too_large"
    assert over_limit_call.input_summary["payload_reason"] == "capability_input_bytes"
    assert quota_call.status == "failed"
    assert quota_call.input_summary["reason"] == "capability_quota_exceeded"
    assert any(event.source_id == str(over_limit_call.id) and event.event_type == "project.capability.failed" for event in events)
    assert any(event.source_id == str(quota_call.id) and event.event_type == "project.capability.failed" for event in events)


@pytest.mark.asyncio
async def test_project_declared_platform_data_capability_records_learning_loop(client):
    from app.auth.models import User

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        db.add(
            User(
                id="u_project_data",
                username="u_project_data",
                name="项目数据用户",
                role="operator",
                department="AI小组",
                state="active",
                is_active=True,
                must_change_password=False,
            )
        )
        await db.commit()

    await client.post(
        "/api/projects/",
        json={
            "id": "data_page",
            "name": "数据能力页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/data",
            "metadata": {"capabilities": ["mcp://skillforge_org_search_users"]},
        },
    )
    run = (await client.post("/api/projects/data_page/runs", json={})).json()
    result = (
        await client.post(
            f"/api/projects/runs/{run['id']}/capability",
            json={
                "request_id": "data-cap-001",
                "capability": "mcp://skillforge_org_search_users",
                "input": {"query": "项目数据用户", "limit": 5},
            },
        )
    ).json()
    duplicate = (
        await client.post(
            f"/api/projects/runs/{run['id']}/capability",
            json={
                "request_id": "data-cap-001",
                "capability": "mcp://skillforge_org_search_users",
                "input": {"query": "项目数据用户", "limit": 5},
            },
        )
    ).json()
    assert result["ok"] is True
    assert result["result"]["proof"]["credential_location"] == "platform_only"
    assert result["result"]["proof"]["data_scope"] == "org.users"
    assert result["result"]["data"]["count"] == 1
    assert result["result"]["data"]["items"][0]["user_id"] == "u_project_data"
    assert duplicate["deduped"] is True

    async with db_mod.async_session_factory() as db:
        calls = (
            await db.execute(
                select(ProjectCapabilityCall).where(ProjectCapabilityCall.project_run_id == run["id"])
            )
        ).scalars().all()
        events = (
            await db.execute(
                select(LearningEvent).where(LearningEvent.source_type == "project_capability_call")
            )
        ).scalars().all()
        artifacts = (
            await db.execute(
                select(LearningArtifact).where(LearningArtifact.target_type == "project_capability")
            )
        ).scalars().all()

    assert len(calls) == 1
    assert calls[0].capability_type == "mcp_read"
    assert calls[0].status == "completed"
    assert calls[0].output_summary["data_scope"] == "org.users"
    assert any(event.source_id == str(calls[0].id) and event.event_type == "project.capability.completed" for event in events)
    assert any(
        artifact.artifact_kind == "agent_memory"
        and artifact.target_id == "mcp://skillforge_org_search_users"
        and artifact.sensitivity_level == "restricted"
        for artifact in artifacts
    )


@pytest.mark.asyncio
async def test_project_declared_samplebrand_cloud_video_alias_routes_to_mcp_read(client, monkeypatch):
    from app.projects import service as project_service

    async def fake_daily_analysis_input(db, user, args):
        assert args["analysis_date"] == "2026-06-11"
        return {
            "ok": True,
            "analysis_date": args["analysis_date"],
            "daily_video_rows": [{"video_id": "v1", "statCost": 300}],
            "videos": [{"id": "v1", "title": "素材1"}],
            "source_counts": {"returned_daily_video_rows": 1},
        }

    monkeypatch.setattr(
        project_service.codex_service,
        "_builtin_samplebrand_daily_analysis_input",
        fake_daily_analysis_input,
    )
    await client.post(
        "/api/projects/",
        json={
            "id": "samplebrand_cloud_video_page",
            "name": "示例品牌云视频页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/samplebrand-cloud-video",
            "metadata": {
                "capabilities": [
                    "ai.analyze",
                    "skillforge_samplebrand_cloud_video_daily_analysis_input",
                ]
            },
        },
    )
    run = (await client.post("/api/projects/samplebrand_cloud_video_page/runs", json={})).json()

    result = (
        await client.post(
            f"/api/projects/runs/{run['id']}/capability",
            json={
                "request_id": "samplebrand-cloud-video-001",
                "capability": "skillforge_samplebrand_cloud_video_daily_analysis_input",
                "input": {"analysis_date": "2026-06-11"},
            },
        )
    ).json()

    assert result["ok"] is True
    assert result["capability"] == "mcp://skillforge_samplebrand_cloud_video_daily_analysis_input"
    assert result["result"]["proof"]["credential_location"] == "platform_only"
    assert result["result"]["proof"]["data_scope"] == "cloud_video.samplebrand_weekly.daily_analysis_input"
    assert result["result"]["data"]["daily_video_rows"][0]["video_id"] == "v1"

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        calls = (
            await db.execute(
                select(ProjectCapabilityCall).where(ProjectCapabilityCall.project_run_id == run["id"])
            )
        ).scalars().all()

    assert len(calls) == 1
    assert calls[0].capability_key == "mcp://skillforge_samplebrand_cloud_video_daily_analysis_input"
    assert calls[0].capability_type == "mcp_read"
    assert calls[0].status == "completed"


@pytest.mark.asyncio
async def test_project_samplebrand_daily_input_ignores_page_action_kind(client, monkeypatch):
    from app.projects import service as project_service

    received_args = {}

    async def fake_daily_analysis_input(db, user, args):
        received_args.update(args)
        return {"ok": True, "analysis_date": "2026-06-11", "daily_video_rows": [], "videos": []}

    monkeypatch.setattr(
        project_service.codex_service,
        "_builtin_samplebrand_daily_analysis_input",
        fake_daily_analysis_input,
    )
    await client.post(
        "/api/projects/",
        json={
            "id": "samplebrand_cloud_video_page_kind",
            "name": "示例品牌云视频页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/samplebrand-cloud-video",
            "metadata": {"capabilities": ["skillforge_samplebrand_cloud_video_daily_analysis_input"]},
        },
    )
    run = (await client.post("/api/projects/samplebrand_cloud_video_page_kind/runs", json={})).json()

    result = (
        await client.post(
            f"/api/projects/runs/{run['id']}/capability",
            json={
                "request_id": "samplebrand-cloud-video-page-kind",
                "capability": "skillforge_samplebrand_cloud_video_daily_analysis_input",
                "input": {
                    "kind": "load_realtime_cloud_video_daily_input",
                    "source": "project_page",
                    "max_rows": 120,
                    "min_cost": 200,
                    "analysis_date": "",
                    "source_skill_id": "samplebrand-video-low-consumption-operator-v1",
                    "include_zero_cost": False,
                },
            },
        )
    ).json()

    assert result["ok"] is True
    assert "kind" not in received_args
    assert received_args["request_kind"] == "load_realtime_cloud_video_daily_input"
    assert received_args["source"] == "project_page"
    assert received_args["max_rows"] == 120

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        call = (
            await db.execute(
                select(ProjectCapabilityCall).where(ProjectCapabilityCall.project_run_id == run["id"])
            )
        ).scalar_one()

    assert call.status == "completed"
    assert call.input_payload["kind"] == "load_realtime_cloud_video_daily_input"


@pytest.mark.asyncio
async def test_project_examples_upload_run_call_capabilities_and_enter_learning_loop(client, monkeypatch, tmp_path):
    import hashlib

    from app.auth.models import User
    from app.projects import service as project_service

    import app.database as db_mod

    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    async with db_mod.async_session_factory() as db:
        if await db.get(User, "u_example_project_data") is None:
            db.add(
                User(
                    id="u_example_project_data",
                    username="u_example_project_data",
                    name="项目数据用户",
                    role="operator",
                    department="AI小组",
                    state="active",
                    is_active=True,
                    must_change_password=False,
                )
            )
        await db.commit()

    ai_calls: list[dict] = []

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        context = args["context_pack"]
        project = context["project"]
        capability = context.get("capability", {}).get("key") or "ai.analyze"
        ai_calls.append(
            {
                "project_id": project["id"],
                "capability": capability,
                "effective_run_id": effective_run_id,
            }
        )
        return {
            "ok": True,
            "model": "example-platform-ai",
            "output": f"{project['name']} 已由平台 AI 接管，建议优先跟进高风险项。",
            "prompt_hash": f"hash-{project['id']}-{capability}",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)

    ai_package = _package_example_project("ai-report-dashboard")
    ai_upload = await client.post(
        "/api/projects/upload",
        data={"package_hash": "sha256:" + hashlib.sha256(ai_package).hexdigest()},
        files={"package": ("ai-report-dashboard.tar.gz", ai_package, "application/gzip")},
    )
    assert ai_upload.status_code == 200, ai_upload.text
    ai_project = ai_upload.json()
    assert ai_project["id"] == "example_ai_report_dashboard"
    assert ai_project["metadata"]["capabilities"] == ["ai.generate"]
    assert ai_project["metadata"]["outputs"] == {"reports": True, "todos": True, "proofs": True}
    assert ai_project["runtime"]["containerized"] is False
    assert ai_project["entry_url"].startswith("/api/projects/assets/example_ai_report_dashboard/")
    ai_asset = await client.get(ai_project["entry_url"])
    assert ai_asset.status_code == 200
    assert "AI报告看板示例" in ai_asset.text
    assert "window.PlatformProjectGateway" in ai_asset.text

    ai_run = (
        await client.post(
            "/api/projects/example_ai_report_dashboard/runs",
            json={
                "request_id": "example-ai-open",
                "params": {"source": "project-example"},
                "input": {"period": "today"},
            },
        )
    ).json()
    await client.post(
        f"/api/projects/runs/{ai_run['id']}/input",
        json={
            "request_id": "example-ai-input",
            "input": {"metric": "conversion", "threshold": "down_10_percent"},
            "metadata": {"example": "ai-report-dashboard"},
        },
    )
    ai_capability = (
        await client.post(
            f"/api/projects/runs/{ai_run['id']}/capability",
            json={
                "request_id": "example-ai-generate",
                "capability": "ai.generate",
                "prompt": "生成部门运营看板分析",
                "input": {"rows": [{"channel": "search", "conversion_drop": 0.13}]},
            },
        )
    ).json()
    assert ai_capability["ok"] is True
    assert ai_capability["result"]["credential_location"] == "platform_only"
    assert "平台 AI 接管" in ai_capability["result"]["output"]

    ai_ingest = (
        await client.post(
            f"/api/projects/runs/{ai_run['id']}/ingest",
            json={
                "request_id": "example-ai-ingest",
                "output": {
                    "summary": ai_capability["result"]["output"],
                    "filters": {"period": "today", "metric": "conversion"},
                },
                "reports": [{"title": "AI运营报告示例", "summary": ai_capability["result"]["output"]}],
                "todos": [{"title": "跟进转化率异常", "priority": "high"}],
                "proofs": [{"source": "project-example", "metric": "conversion"}],
            },
        )
    ).json()
    assert ai_ingest["status"] == "ai_completed"
    assert ai_ingest["analysis"]["credential_location"] == "platform_only"
    assert ai_ingest["report_count"] == 1
    assert ai_ingest["todo_count"] == 1
    assert len(ai_calls) == 2
    assert {call["capability"] for call in ai_calls} == {"ai.generate", "ai.analyze"}

    org_package = _package_example_project("org-data-search")
    org_upload = await client.post(
        "/api/projects/upload",
        data={"package_hash": "sha256:" + hashlib.sha256(org_package).hexdigest()},
        files={"package": ("org-data-search.tar.gz", org_package, "application/gzip")},
    )
    assert org_upload.status_code == 200, org_upload.text
    org_project = org_upload.json()
    assert org_project["id"] == "example_org_data_search"
    assert org_project["metadata"]["capabilities"] == ["mcp://skillforge_org_search_users"]
    assert org_project["runtime"]["containerized"] is False
    org_asset = await client.get(org_project["entry_url"])
    assert org_asset.status_code == 200
    assert "组织数据查询示例" in org_asset.text
    assert "mcp://skillforge_org_search_users" in org_asset.text

    org_run = (
        await client.post(
            "/api/projects/example_org_data_search/runs",
            json={"request_id": "example-org-open", "input": {"query": "项目数据用户"}},
        )
    ).json()
    await client.post(
        f"/api/projects/runs/{org_run['id']}/input",
        json={
            "request_id": "example-org-input",
            "input": {"query": "项目数据用户", "limit": 5},
            "metadata": {"example": "org-data-search"},
        },
    )
    org_capability = (
        await client.post(
            f"/api/projects/runs/{org_run['id']}/capability",
            json={
                "request_id": "example-org-search",
                "capability": "mcp://skillforge_org_search_users",
                "input": {"query": "项目数据用户", "limit": 5},
            },
        )
    ).json()
    assert org_capability["ok"] is True
    assert org_capability["result"]["proof"]["credential_location"] == "platform_only"
    assert org_capability["result"]["proof"]["data_scope"] == "org.users"
    assert org_capability["result"]["data"]["count"] >= 1
    assert any(item["user_id"] == "u_example_project_data" for item in org_capability["result"]["data"]["items"])

    org_ingest = (
        await client.post(
            f"/api/projects/runs/{org_run['id']}/ingest",
            json={
                "request_id": "example-org-ingest",
                "auto_analyze": False,
                "output": {
                    "summary": "组织数据查询完成",
                    "count": org_capability["result"]["data"]["count"],
                },
                "reports": [{"title": "组织数据查询报告示例", "summary": "组织数据能力调用完成"}],
                "proofs": [org_capability["result"]["proof"]],
            },
        )
    ).json()
    assert org_ingest["status"] == "completed"
    assert org_ingest["report_count"] == 1

    ai_trace = (await client.get(f"/api/projects/runs/{ai_run['id']}/trace")).json()
    assert ai_trace["counts"]["input_events_total"] == 1
    assert ai_trace["counts"]["output_events_total"] == 1
    assert ai_trace["counts"]["capability_calls_total"] == 2
    assert {call["capability_key"] for call in ai_trace["capability_calls"]} == {"ai.generate", "ai.analyze"}

    org_trace = (await client.get(f"/api/projects/runs/{org_run['id']}/trace")).json()
    assert org_trace["counts"]["input_events_total"] == 1
    assert org_trace["counts"]["output_events_total"] == 1
    assert org_trace["counts"]["capability_calls_total"] == 1
    assert org_trace["capability_calls"][0]["capability_type"] == "mcp_read"

    await client.post(f"/api/projects/runs/{ai_run['id']}/close", json={"request_id": "example-ai-close"})
    await client.post(f"/api/projects/runs/{org_run['id']}/close", json={"request_id": "example-org-close"})

    async with db_mod.async_session_factory() as db:
        runs = (
            await db.execute(select(ProjectRun).where(ProjectRun.id.in_([ai_run["id"], org_run["id"]])))
        ).scalars().all()
        ingress_events = (
            await db.execute(
                select(ProjectIngressEvent).where(ProjectIngressEvent.project_run_id.in_([ai_run["id"], org_run["id"]]))
            )
        ).scalars().all()
        capability_calls = (
            await db.execute(
                select(ProjectCapabilityCall).where(ProjectCapabilityCall.project_run_id.in_([ai_run["id"], org_run["id"]]))
            )
        ).scalars().all()
        decisions = (
            await db.execute(
                select(DecisionLog).where(
                    DecisionLog.skill_id.in_(["project:example_ai_report_dashboard", "project:example_org_data_search"])
                )
            )
        ).scalars().all()
        learning_events = (await db.execute(select(LearningEvent))).scalars().all()
        learning_artifacts = (
            await db.execute(
                select(LearningArtifact).where(
                    LearningArtifact.skill_id.in_(
                        ["project:example_ai_report_dashboard", "project:example_org_data_search"]
                    )
                )
            )
        ).scalars().all()

    assert {run.project_id for run in runs} == {"example_ai_report_dashboard", "example_org_data_search"}
    assert {call.capability_key for call in capability_calls} == {
        "ai.generate",
        "ai.analyze",
        "mcp://skillforge_org_search_users",
    }
    assert any(call.capability_type == "mcp_read" and call.output_summary["data_scope"] == "org.users" for call in capability_calls)
    assert len([event for event in ingress_events if event.event_type == "input"]) == 2
    assert len([event for event in ingress_events if event.event_type == "output"]) == 2
    assert {decision.skill_id for decision in decisions} == {"project:example_ai_report_dashboard", "project:example_org_data_search"}

    run_ids = {ai_run["id"], org_run["id"]}
    execution_ids = {ai_run["execution_run_id"], org_run["execution_run_id"]}
    ingress_ids = {str(event.id) for event in ingress_events}
    call_ids = {str(call.id) for call in capability_calls}
    relevant_events = [
        event
        for event in learning_events
        if event.source_id in run_ids
        or event.source_id in execution_ids
        or event.source_id in ingress_ids
        or event.source_id in call_ids
    ]
    assert any(event.source_type == "project_run" and event.event_type == "project.run.opened" for event in relevant_events)
    assert any(event.source_type == "project_ingress_event" and event.event_type == "project.input.received" for event in relevant_events)
    assert any(event.source_type == "project_ingress_event" and event.event_type == "project.ingress.received" for event in relevant_events)
    assert any(event.source_type == "project_capability_call" and event.event_type == "project.capability.completed" for event in relevant_events)
    assert any(event.source_type == "execution_run" for event in relevant_events)
    assert any(artifact.artifact_kind == "report_summary" for artifact in learning_artifacts)
    assert any(artifact.artifact_kind == "training_sample" and artifact.target_type == "project" for artifact in learning_artifacts)
    assert len([artifact for artifact in learning_artifacts if artifact.artifact_kind == "agent_memory"]) >= 3
    assert any(
        artifact.artifact_kind == "report_summary"
        and artifact.sensitivity_level == "restricted"
        and artifact.review_status == "required"
        for artifact in learning_artifacts
    )


@pytest.mark.asyncio
async def test_project_can_create_100_concurrent_runs(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "concurrent_page",
            "name": "并发页面",
            "type": "external_web",
            "entry_url": "https://example.com/concurrent",
        },
    )

    async def create_run(idx: int):
        resp = await client.post("/api/projects/concurrent_page/runs", json={"params": {"idx": idx}})
        assert resp.status_code == 200, resp.text
        return resp.json()

    runs = await asyncio.gather(*(create_run(idx) for idx in range(100)))
    assert len({item["id"] for item in runs}) == 100
    assert len({item["execution_run_id"] for item in runs}) == 100

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        project_runs = (
            await db.execute(select(ProjectRun).where(ProjectRun.project_id == "concurrent_page"))
        ).scalars().all()
        execution_runs = (
            await db.execute(select(ExecutionRun).where(ExecutionRun.business_ref_id == "concurrent_page"))
        ).scalars().all()

    assert len(project_runs) == 100
    assert len(execution_runs) == 100


@pytest.mark.asyncio
async def test_project_run_capacity_limit_rejects_extra_open_but_keeps_idempotent_replay(client, monkeypatch):
    from app.projects import service as project_service

    monkeypatch.setattr(project_service.settings, "PROJECT_TARGET_CONCURRENT_RUNS", 2)
    await client.post(
        "/api/projects/",
        json={
            "id": "capacity_guard_page",
            "name": "容量保护页面",
            "type": "external_web",
            "entry_url": "https://example.com/capacity",
            "department": "AI小组",
        },
    )
    first = (
        await client.post(
            "/api/projects/capacity_guard_page/runs",
            json={"request_id": "open-cap-1", "params": {"source": "capacity-test"}},
        )
    ).json()
    second = (
        await client.post(
            "/api/projects/capacity_guard_page/runs",
            json={"request_id": "open-cap-2"},
        )
    ).json()
    rejected = await client.post(
        "/api/projects/capacity_guard_page/runs",
        json={"request_id": "open-cap-3"},
    )
    replay = (
        await client.post(
            "/api/projects/capacity_guard_page/runs",
            json={"request_id": "open-cap-1", "params": {"source": "capacity-test"}},
        )
    ).json()

    assert first["status"] == "running"
    assert second["status"] == "running"
    assert rejected.status_code == 429
    body = rejected.json()["error"]
    assert body["code"] == "PROJECT_CAPACITY_EXCEEDED"
    assert body["detail"]["target_concurrent_runs"] == 2
    assert body["detail"]["active_runs"] == 2
    assert replay["id"] == first["id"]
    assert replay["deduped"] is True

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        runs = (
            await db.execute(select(ProjectRun).where(ProjectRun.project_id == "capacity_guard_page"))
        ).scalars().all()
        execution = await db.get(ExecutionRun, first["execution_run_id"])

    assert len(runs) == 2
    assert execution is not None
    assert execution.metadata_json["department"] == "AI小组"
    assert execution.metadata_json["capacity_at_open"]["target_concurrent_runs"] == 2
    assert execution.metadata_json["capacity_at_open"]["active_runs_before_open"] == 0
    assert execution.metadata_json["capacity_at_open"]["lock"]["key"] == "project_run_capacity:global"


@pytest.mark.asyncio
async def test_project_run_capacity_reconciles_stale_runs_before_rejecting(client, monkeypatch):
    from datetime import timedelta

    from app.common.time_utils import now_bjt
    from app.projects import service as project_service

    monkeypatch.setattr(project_service.settings, "PROJECT_TARGET_CONCURRENT_RUNS", 1)
    await client.post(
        "/api/projects/",
        json={
            "id": "capacity_stale_page",
            "name": "容量失活修复页面",
            "type": "external_web",
            "entry_url": "https://example.com/capacity-stale",
        },
    )
    first = (await client.post("/api/projects/capacity_stale_page/runs", json={"request_id": "stale-cap-1"})).json()

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        stale_run = await db.get(ProjectRun, first["id"])
        assert stale_run is not None
        stale_run.last_heartbeat_at = now_bjt() - timedelta(seconds=600)
        stale_run.updated_at = stale_run.last_heartbeat_at
        await db.commit()

    second = (
        await client.post(
            "/api/projects/capacity_stale_page/runs",
            json={"request_id": "stale-cap-2"},
        )
    ).json()
    assert second["status"] == "running"

    async with db_mod.async_session_factory() as db:
        stale_run = await db.get(ProjectRun, first["id"])
        active_run = await db.get(ProjectRun, second["id"])

    assert stale_run is not None and stale_run.status == "stale"
    assert active_run is not None and active_run.status == "running"


@pytest.mark.asyncio
async def test_project_run_capacity_lock_serializes_concurrent_open_requests(client, monkeypatch):
    from app.projects import service as project_service

    monkeypatch.setattr(project_service.settings, "PROJECT_TARGET_CONCURRENT_RUNS", 1)
    await client.post(
        "/api/projects/",
        json={
            "id": "capacity_concurrent_page",
            "name": "并发容量锁页面",
            "type": "external_web",
            "entry_url": "https://example.com/capacity-concurrent",
        },
    )

    async def open_run(request_id: str):
        return await client.post("/api/projects/capacity_concurrent_page/runs", json={"request_id": request_id})

    first, second = await asyncio.gather(open_run("cap-lock-1"), open_run("cap-lock-2"))
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 429]
    rejected = first if first.status_code == 429 else second
    assert rejected.json()["error"]["code"] == "PROJECT_CAPACITY_EXCEEDED"
    assert rejected.json()["error"]["detail"]["capacity_lock"]["key"] == "project_run_capacity:global"

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        runs = (
            await db.execute(select(ProjectRun).where(ProjectRun.project_id == "capacity_concurrent_page"))
        ).scalars().all()

    assert len(runs) == 1
    assert runs[0].status == "running"


@pytest.mark.asyncio
async def test_project_runtime_status_reports_capacity_ai_and_stale_state(client):
    from datetime import timedelta

    from app.common.time_utils import now_bjt

    await client.post(
        "/api/projects/",
        json={
            "id": "runtime_status_page",
            "name": "运行状态页面",
            "type": "dashboard",
            "entry_url": "https://example.com/status",
        },
    )
    active = (await client.post("/api/projects/runtime_status_page/runs", json={})).json()
    stale = (await client.post("/api/projects/runtime_status_page/runs", json={})).json()
    waiting = (await client.post("/api/projects/runtime_status_page/runs", json={})).json()

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        stale_run = await db.get(ProjectRun, stale["id"])
        waiting_run = await db.get(ProjectRun, waiting["id"])
        assert stale_run is not None and waiting_run is not None
        stale_run.last_heartbeat_at = now_bjt() - timedelta(seconds=600)
        stale_run.updated_at = stale_run.last_heartbeat_at
        waiting_run.status = "waiting_ai"
        waiting_run.updated_at = now_bjt()
        await db.commit()

    resp = await client.get("/api/projects/runtime/status")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["capacity"]["target_concurrent_runs"] >= 100
    assert data["capacity"]["active_runs"] >= 1
    assert data["capacity"]["available_run_slots"] >= 0
    assert data["runs"]["stale"] >= 1
    assert data["runs"]["waiting_ai"] >= 1
    assert data["runs"]["stale_reconcile"]["updated"] >= 1
    assert data["ai"]["waiting"] >= 1
    assert data["projects"]["by_type"]["dashboard"] == 1
    assert data["gateway"]["containerized"] is False
    assert data["gateway"]["supports_100_concurrent_runs"] is True
    assert data["gateway"]["gateway_limits"]["max_ingest_bytes"] >= 1
    assert data["gateway"]["gateway_limits"]["max_asset_upload_bytes"] >= 1
    assert data["gateway"]["gateway_limits"]["asset_upload_streaming"] == 1
    assert data["gateway"]["gateway_limits"]["asset_upload_resumable"] == 0
    assert "learning_events" in data["gateway"]["records"]
    assert any(item["project_run_id"] == stale["id"] for item in data["recent_errors"])
    assert active["status"] == "running"


@pytest.mark.asyncio
async def test_project_runtime_status_scales_to_thousand_project_inventory(client):
    from app.common.time_utils import now_bjt

    import app.database as db_mod

    now = now_bjt()
    async with db_mod.async_session_factory() as db:
        db.add_all(
            [
                Project(
                    id=f"bulk_{idx:04d}",
                    name=f"千级项目 {idx:04d}",
                    type="dashboard" if idx % 2 else "external_web",
                    department="AI小组",
                    owner_user_id="admin",
                    visibility="department",
                    status="published",
                    entry_url=f"https://example.com/bulk/{idx}",
                    metadata_json={},
                    created_at=now,
                    updated_at=now,
                )
                for idx in range(1000)
            ]
        )
        await db.commit()

    data = (await client.get("/api/projects/runtime/status")).json()
    assert data["capacity"]["target_visible_projects"] >= 1000
    assert data["capacity"]["visible_projects"] == 1000
    assert data["projects"]["by_type"]["dashboard"] == 500
    assert data["projects"]["by_type"]["external_web"] == 500
    assert data["projects"]["by_department"]["AI小组"] == 1000
    assert data["gateway"]["supports_hundreds_to_thousands_projects"] is True


@pytest.mark.asyncio
async def test_project_capability_rejects_undeclared_non_ai_gateway(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "locked_page",
            "name": "受控页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/locked",
            "metadata": {"capabilities": ["ai.generate"]},
        },
    )
    run = (await client.post("/api/projects/locked_page/runs", json={})).json()
    resp = await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={"capability": "mcp://skillforge_org_search_users", "input": {"query": "张三"}},
    )
    assert resp.status_code == 403
    detail = resp.json()["error"]["detail"]
    assert detail["capability"] == "mcp://skillforge_org_search_users"
    assert detail["capability_call_status"] == "failed"
    assert isinstance(detail["call_id"], int)

    run_detail = (await client.get(f"/api/projects/runs/{run['id']}")).json()
    latest = run_detail["latest_capability_call"]
    assert latest["id"] == detail["call_id"]
    assert latest["capability_key"] == "mcp://skillforge_org_search_users"
    assert latest["status"] == "failed"
    assert latest["error"] == "项目包未声明该能力，平台拒绝代调用。"


@pytest.mark.asyncio
async def test_project_run_trace_lists_gateway_events_and_redacts_sensitive_payload(client, monkeypatch):
    from app.projects import service as project_service

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        return {
            "ok": True,
            "model": "trace-model",
            "output": "trace ok",
            "prompt_hash": "trace-hash",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)
    await client.post(
        "/api/projects/",
        json={
            "id": "trace_page",
            "name": "轨迹页面",
            "type": "internal_tool",
            "entry_url": "https://example.com/trace",
            "metadata": {"capabilities": ["ai.generate"]},
        },
    )
    run = (await client.post("/api/projects/trace_page/runs", json={})).json()
    await client.post(
        f"/api/projects/runs/{run['id']}/ingest",
        json={
            "request_id": "trace-ingest",
            "auto_analyze": False,
            "input": {"cookie": "secret-cookie", "q": "safe"},
            "output": {"summary": "trace output", "api_token": "secret-token"},
        },
    )
    await client.post(
        f"/api/projects/runs/{run['id']}/capability",
        json={
            "request_id": "trace-cap",
            "capability": "ai.generate",
            "prompt": "trace",
            "input": {"password": "secret-password", "visible": "ok"},
        },
    )

    trace = (await client.get(f"/api/projects/runs/{run['id']}/trace")).json()
    assert trace["counts"]["ingress_events"] == 1
    assert trace["counts"]["capability_calls"] == 1
    assert trace["timeline"][0]["kind"] in {"ingress", "capability"}
    event = trace["ingress_events"][0]
    assert event["request_id"] == "trace-ingest"
    assert event["input_snapshot"]["cookie"] == "[REDACTED]"
    assert event["output_result"]["api_token"] == "[REDACTED]"
    call = trace["capability_calls"][0]
    assert call["request_id"] == "trace-cap"
    assert call["status"] == "completed"
    assert call["input_payload"]["input"]["password"] == "[REDACTED]"
    assert call["input_payload"]["input"]["visible"] == "ok"
    assert call["output_result"]["output"] == "trace ok"


@pytest.mark.asyncio
async def test_project_list_paginates_and_searches_large_project_set(client):
    for idx in range(105):
        resp = await client.post(
            "/api/projects/",
            json={
                "id": f"scale_{idx:03d}",
                "name": f"规模项目 {idx:03d}",
                "type": "dashboard" if idx % 2 else "external_web",
                "entry_url": f"https://example.com/scale/{idx}",
            },
        )
        assert resp.status_code == 200, resp.text

    first_page = (
        await client.get(
            "/api/projects/",
            params={"include_playbooks": "false", "page": 1, "page_size": 100},
        )
    ).json()
    assert first_page["pagination"] == {"page": 1, "page_size": 100, "total": 105, "has_more": True}
    assert first_page["runtime_policy"]["query_strategy"] == "server_side_latest_run_page"
    assert len(first_page["items"]) == 100
    assert first_page["stats"]["by_type"]["dashboard"] == 52
    assert first_page["stats"]["by_type"]["external_web"] == 53

    second_page = (
        await client.get(
            "/api/projects/",
            params={"include_playbooks": "false", "page": 2, "page_size": 100},
        )
    ).json()
    assert second_page["pagination"]["has_more"] is False
    assert len(second_page["items"]) == 5

    searched = (
        await client.get(
            "/api/projects/",
            params={"include_playbooks": "false", "page_size": 100, "search": "规模项目 104"},
        )
    ).json()
    assert searched["pagination"]["total"] == 1
    assert searched["items"][0]["id"] == "scale_104"


@pytest.mark.asyncio
async def test_project_list_includes_playbooks_as_project_items(client, monkeypatch):
    from app.projects import service as project_service

    async def fake_list_playbooks():
        return [
            {
                "file_name": "daily_ops",
                "name": "每日运营 Playbook",
                "description": "已有 Playbook 兼容到项目分类",
                "department": "AI小组",
                "steps_count": 2,
                "modified_at": "2026-06-03T10:00:00+08:00",
            }
        ]

    monkeypatch.setattr(project_service.playbook_service, "list_playbooks", fake_list_playbooks)
    data = (await client.get("/api/projects/?scope=playbook")).json()
    assert data["items"][0]["id"] == "playbook:daily_ops"
    assert data["items"][0]["type"] == "playbook"
    assert data["items"][0]["runtime"]["containerized"] is False


@pytest.mark.asyncio
async def test_project_list_with_playbooks_uses_bounded_mixed_page(client, monkeypatch):
    from app.projects import service as project_service

    async def fake_visible_playbooks(user):
        return [
            {
                "id": "playbook:scale-playbook",
                "name": "规模 Playbook",
                "description": "",
                "type": "playbook",
                "department_id": None,
                "department": "AI小组",
                "owner_user_id": None,
                "visibility": "department",
                "status": "published",
                "entry_url": "/playbook/scale-playbook",
                "current_version_id": None,
                "metadata": {"source": "playbook"},
                "runtime": {"mode": "playbook_workbench", "containerized": False},
                "latest_run": None,
                "permissions": {"read": True, "launch": True},
                "created_at": None,
                "updated_at": "2999-01-01T00:00:00+08:00",
            }
        ]

    monkeypatch.setattr(project_service, "_visible_playbook_projects", fake_visible_playbooks)
    for idx in range(30):
        resp = await client.post(
            "/api/projects/",
            json={
                "id": f"mixed_scale_{idx:03d}",
                "name": f"混合规模项目 {idx:03d}",
                "type": "dashboard",
                "entry_url": f"https://example.com/mixed-scale/{idx}",
            },
        )
        assert resp.status_code == 200, resp.text

    mixed_page = (
        await client.get(
            "/api/projects/",
            params={"page": 2, "page_size": 10},
        )
    ).json()
    assert mixed_page["pagination"] == {"page": 2, "page_size": 10, "total": 31, "has_more": True}
    assert mixed_page["runtime_policy"]["query_strategy"] == "bounded_mixed_playbook_project_page"
    assert mixed_page["runtime_policy"]["project_page_fetch_limit"] == 20
    assert mixed_page["stats"]["by_type"]["playbook"] == 1
    assert mixed_page["stats"]["by_type"]["dashboard"] == 30
    assert len(mixed_page["items"]) == 10


@pytest.mark.asyncio
async def test_project_upload_static_package(client, monkeypatch, tmp_path):
    import io
    import json
    import tarfile
    import hashlib
    from app.projects import service as project_service

    manifest = {
        "project_id": "static_tool",
        "name": "静态项目",
        "kind": "web_static",
        "entry": "web/index.html",
        "visibility": "department",
        "outputs": {"reports": True},
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        html = b"<html><body>ok</body></html>"
        info = tarfile.TarInfo("web/index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
    data = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    resp = await client.post(
        "/api/projects/upload",
        data={"manifest_json": json.dumps(manifest, ensure_ascii=False), "package_hash": "sha256:" + hashlib.sha256(data).hexdigest()},
        files={"package": ("project.tar.gz", data, "application/gzip")},
    )
    assert resp.status_code == 200, resp.text
    uploaded = resp.json()
    assert uploaded["id"] == "static_tool"
    assert uploaded["entry_url"].startswith("/api/projects/assets/static_tool/")
    assert uploaded["runtime"]["containerized"] is False
    assert (tmp_path / "project-assets" / "static_tool" / uploaded["asset_version"] / "web" / "index.html").is_file()
    asset = await client.get(uploaded["entry_url"])
    assert asset.status_code == 200
    assert "ok" in asset.text
    assert asset.headers["cache-control"].startswith("private")
    assert "sandbox" in asset.headers["content-security-policy"]
    assert "connect-src 'self'" in asset.headers["content-security-policy"]
    assert "connect-src 'self' https:" not in asset.headers["content-security-policy"]
    assert "allow-same-origin" not in asset.headers["content-security-policy"]
    assert asset.headers["x-content-type-options"] == "nosniff"
    from types import SimpleNamespace

    from app.common.exceptions import AppError
    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        other_dept_user = SimpleNamespace(id="other-user", role="operator", department="其它部门", can_view_all=False)
        with pytest.raises(AppError) as exc:
            await project_service.project_asset_path(db, other_dept_user, uploaded["id"], uploaded["asset_version"], "web/index.html")
    assert exc.value.code == "PROJECT_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_project_upload_generated_webpage_rewrites_root_assets_and_injects_gateway(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import json
    import tarfile

    from app.projects import service as project_service

    manifest = {
        "project_id": "generated_vite",
        "name": "Codex 生成网页",
        "kind": "web_static",
        "entry": "dist/index.html",
        "visibility": "department",
    }
    html = b"""<!doctype html>
<html><head>
  <link rel="stylesheet" href="/assets/index.css">
  <link rel=preload href=/assets/preload.js>
  <script type="module" src="/assets/index.js"></script>
</head><body><img src="/assets/logo.png" srcset="/assets/logo.png 1x, /assets/logo@2x.png 2x"><div id="root"></div></body></html>"""
    css = b"body{background:url('/assets/bg.png')}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        for name, content in {
            "dist/index.html": html,
            "dist/assets/index.css": css,
            "dist/assets/index.js": b"console.log('generated')",
            "dist/assets/preload.js": b"console.log('preload')",
            "dist/assets/logo.png": b"png",
            "dist/assets/logo@2x.png": b"png",
            "dist/assets/bg.png": b"png",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    data = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    resp = await client.post(
        "/api/projects/upload",
        data={
            "manifest_json": json.dumps(manifest, ensure_ascii=False),
            "package_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
        },
        files={"package": ("generated.tar.gz", data, "application/gzip")},
    )

    assert resp.status_code == 200, resp.text
    uploaded = resp.json()
    version = uploaded["asset_version"]
    prefix = f"/api/projects/assets/generated_vite/{version}/dist/assets/"
    asset_root = tmp_path / "project-assets" / "generated_vite" / version
    rewritten_html = (asset_root / "dist" / "index.html").read_text(encoding="utf-8")
    rewritten_css = (asset_root / "dist" / "assets" / "index.css").read_text(encoding="utf-8")
    assert f'href="{prefix}index.css"' in rewritten_html
    assert f'src="{prefix}index.js"' in rewritten_html
    assert f'src="{prefix}logo.png"' in rewritten_html
    assert f'href={prefix}preload.js' in rewritten_html
    assert f'srcset="{prefix}logo.png 1x, {prefix}logo@2x.png 2x"' in rewritten_html
    assert "/project-gateway-sdk.js" in rewritten_html
    assert "/project-autowire.js" in rewritten_html
    assert f"url('{prefix}bg.png')" in rewritten_css
    assert uploaded["metadata"]["asset_rewrites"]["replacement_count"] >= 4
    assert uploaded["metadata"]["gateway_bootstrap"]["injected"] is True
    assert uploaded["metadata"]["service_conversion"]["status"] == "converted"
    assert uploaded["metadata"]["service_conversion"]["model_profile"] == "ai.cheap"
    assert uploaded["metadata"]["service_conversion"]["api_service"]["contract_url"] == "/api/projects/generated_vite/service"

    asset = await client.get(uploaded["entry_url"])
    assert asset.status_code == 200
    assert "/project-autowire.js" in asset.text
    js_asset = await client.get(f"{prefix}index.js")
    assert js_asset.status_code == 200
    assert "generated" in js_asset.text
    contract = await client.get("/api/projects/generated_vite/service")
    assert contract.status_code == 200
    service = contract.json()["service"]
    assert service["runtime_contract"]["output"]["auto_analyze"] is True
    assert service["api_service"]["bridge_global"] == "window.SkillForgeProjectBridge"
    assert service["api_service"]["invoke_url"] == "/api/projects/generated_vite/service/invoke"


@pytest.mark.asyncio
async def test_uploaded_project_service_invoke_records_io_capability_and_ai_loop(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import json
    import tarfile

    from sqlalchemy import func, select

    from app.execution.models import DecisionLog, ExecutionRun
    from app.learning.models import LearningEvent
    from app.projects import service as project_service
    from app.projects.models import ProjectCapabilityCall, ProjectIngressEvent, ProjectRun

    ai_calls: list[dict] = []

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        ai_calls.append({"run_id": effective_run_id, "model_profile": args.get("model_profile"), "prompt": args.get("prompt")})
        return {
            "ok": True,
            "model": "cheap-service-model" if args.get("model_profile") == "cheap" else "analysis-model",
            "model_profile": args.get("model_profile") or "default",
            "output": {
                "summary": "订单 123 已完成服务化执行",
                "reports": [{"title": "订单服务报告", "summary": "服务输出已进入平台"}],
                "todos": [{"title": "跟进订单 123", "assignee": "运营"}],
            }
            if args.get("model_profile") == "cheap"
            else "AI 循环分析完成",
            "prompt_hash": f"svc-hash-{len(ai_calls)}",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    manifest = {
        "project_id": "service_invoke_page",
        "name": "上传后 API 服务页面",
        "kind": "web_static",
        "entry": "index.html",
        "visibility": "department",
        "capabilities": ["ai.cheap.generate"],
        "outputs": {"reports": True, "todos": True, "proofs": True},
    }
    html = b"""<!doctype html>
<html><head><title>Service Invoke</title></head>
<body>
  <form><input name="order_id" placeholder="Order ID"><button>Search</button></form>
</body></html>"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        info = tarfile.TarInfo("index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
    data = buf.getvalue()

    uploaded = await client.post(
        "/api/projects/upload",
        data={
            "manifest_json": json.dumps(manifest, ensure_ascii=False),
            "package_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
        },
        files={"package": ("service-invoke.tar.gz", data, "application/gzip")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["metadata"]["service_conversion"]["api_service"]["invoke_url"] == "/api/projects/service_invoke_page/service/invoke"

    invoked = await client.post(
        "/api/projects/service_invoke_page/service/invoke",
        json={
            "request_id": "order-123",
            "input": {"order_id": "123", "token": "should-redact"},
            "prompt": "查询订单 123 并给出报告和待办",
        },
    )
    assert invoked.status_code == 200, invoked.text
    payload = invoked.json()
    assert payload["ok"] is True
    assert payload["service"]["credential_location"] == "platform_only"
    assert payload["capability"]["key"] == "ai.cheap.generate"
    assert payload["capability"]["ok"] is True
    assert payload["run"]["status"] == "ai_completed"
    assert payload["run"]["report_count"] == 1
    assert payload["run"]["todo_count"] == 1
    assert len(ai_calls) == 2
    assert ai_calls[0]["model_profile"] == "cheap"
    assert ai_calls[1]["model_profile"] is None

    repeated = await client.post(
        "/api/projects/service_invoke_page/service/invoke",
        json={"request_id": "order-123", "input": {"order_id": "123"}, "prompt": "重复调用"},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["deduped"] is True
    assert repeated.json()["project_run_id"] == payload["project_run_id"]
    assert len(ai_calls) == 2

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        run = await db.get(ProjectRun, payload["project_run_id"])
        assert run is not None
        assert run.input_snapshot["order_id"] == "123"
        events = (
            await db.execute(
                select(ProjectIngressEvent)
                .where(ProjectIngressEvent.project_run_id == run.id)
                .order_by(ProjectIngressEvent.created_at.asc(), ProjectIngressEvent.id.asc())
            )
        ).scalars().all()
        assert [event.event_type for event in events] == ["input", "output"]
        assert events[0].metadata_json["source"] == "project_api_service"
        assert events[1].metadata_json["source"] == "project_api_service"
        calls = (
            await db.execute(
                select(ProjectCapabilityCall)
                .where(ProjectCapabilityCall.project_run_id == run.id)
                .order_by(ProjectCapabilityCall.created_at.asc(), ProjectCapabilityCall.id.asc())
            )
        ).scalars().all()
        assert [call.capability_key for call in calls] == ["ai.cheap.generate", "ai.analyze"]
        decision_count = (
            await db.execute(
                select(func.count(DecisionLog.id))
                .join(ExecutionRun, ExecutionRun.id == DecisionLog.run_id)
                .where(ExecutionRun.business_ref_id == "service_invoke_page")
            )
        ).scalar()
        learning_counts = dict(
            (
                await db.execute(
                    select(LearningEvent.source_type, func.count(LearningEvent.id))
                    .where(
                        LearningEvent.source_type.in_(
                            ["project_run", "project_ingress_event", "project_capability_call", "decision_log", "execution_run"]
                        )
                    )
                    .group_by(LearningEvent.source_type)
                )
            ).all()
        )
    assert decision_count == 1
    assert learning_counts["project_run"] >= 1
    assert learning_counts["project_ingress_event"] >= 2
    assert learning_counts["project_capability_call"] >= 2
    assert learning_counts["decision_log"] >= 1
    assert learning_counts["execution_run"] >= 1


@pytest.mark.asyncio
async def test_project_upload_service_conversion_can_use_cheap_only_ai_config(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import json
    import tarfile

    from sqlalchemy import delete

    import app.common.ai as ai_module
    from app.common.models import SystemConfig
    from app.database import async_session_factory
    from app.projects import service as project_service

    ai_module.invalidate_ai_config_cache()
    async with async_session_factory() as session:
        await session.execute(delete(SystemConfig).where(SystemConfig.key.like("ai.%")))
        await session.execute(delete(SystemConfig).where(SystemConfig.key == "project.service_conversion.ai_enabled"))
        session.add(SystemConfig(key="ai.cheap.api_base", value="https://cheap-only.example/v1", updated_by="admin"))
        session.add(SystemConfig(key="ai.cheap.api_key", value="sk-cheap-only", updated_by="admin"))
        session.add(SystemConfig(key="ai.cheap.model", value="cheap-only-model", updated_by="admin"))
        session.add(SystemConfig(key="project.service_conversion.ai_enabled", value=True, updated_by="admin"))
        await session.commit()

    captured: dict[str, object] = {}

    async def fake_call_llm(system, user, **kwargs):
        captured["system"] = system
        captured["user"] = user
        captured["kwargs"] = kwargs
        return {
            "service_name": "订单查询服务",
            "api_shape": {"input": {"order_id": "string"}, "output": {"reports": True}},
        }

    monkeypatch.setattr(ai_module, "call_llm", fake_call_llm)
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    manifest = {
        "project_id": "cheap_only_service_page",
        "name": "便宜模型转服务页面",
        "kind": "web_static",
        "entry": "index.html",
        "visibility": "department",
    }
    html = b"""<!doctype html>
<html><head><title>Order Tool</title></head>
<body>
  <form><input name="order_id" placeholder="Order ID"><button>Search</button></form>
</body></html>"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        info = tarfile.TarInfo("index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
    data = buf.getvalue()

    resp = await client.post(
        "/api/projects/upload",
        data={
            "manifest_json": json.dumps(manifest, ensure_ascii=False),
            "package_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
        },
        files={"package": ("cheap-only-service.tar.gz", data, "application/gzip")},
    )

    assert resp.status_code == 200, resp.text
    uploaded = resp.json()
    service_conversion = uploaded["metadata"]["service_conversion"]
    assert captured["kwargs"]["model_profile"] == "cheap"
    assert service_conversion["ai_refinement"]["status"] == "completed"
    assert service_conversion["ai_refinement"]["model"] == "cheap-only-model"
    assert service_conversion["ai_service_suggestion"]["service_name"] == "订单查询服务"

    contract = await client.get("/api/projects/cheap_only_service_page/service")
    assert contract.status_code == 200
    assert contract.json()["service"]["ai_refinement"]["model"] == "cheap-only-model"
    ai_module.invalidate_ai_config_cache()


@pytest.mark.asyncio
async def test_project_upload_same_manifest_version_creates_hash_suffixed_version(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import tarfile

    from app.projects import service as project_service

    def make_package(html: bytes) -> bytes:
        manifest = {
            "project_id": "same_version_tool",
            "name": "同版本重复上传项目",
            "version": "1.0.0",
            "kind": "web_static",
            "entry": "web/index.html",
            "visibility": "department",
        }
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
            info = tarfile.TarInfo("projectforge.yaml")
            info.size = len(manifest_bytes)
            tar.addfile(info, io.BytesIO(manifest_bytes))
            info = tarfile.TarInfo("web/index.html")
            info.size = len(html)
            tar.addfile(info, io.BytesIO(html))
        return buf.getvalue()

    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")
    first_package = make_package(b"<html><body>v1</body></html>")
    second_package = make_package(b"<html><body>v2</body></html>")

    first = await client.post(
        "/api/projects/upload",
        data={"package_hash": "sha256:" + hashlib.sha256(first_package).hexdigest()},
        files={"package": ("project-v1.tar.gz", first_package, "application/gzip")},
    )
    second = await client.post(
        "/api/projects/upload",
        data={"package_hash": "sha256:" + hashlib.sha256(second_package).hexdigest()},
        files={"package": ("project-v2.tar.gz", second_package, "application/gzip")},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_project = first.json()
    second_project = second.json()
    assert first_project["current_version_id"] != second_project["current_version_id"]
    assert second_project["metadata"]["manifest_version"] == "1.0.0"
    assert second_project["metadata"]["effective_version"].startswith("1.0.0+")
    assert second_project["metadata"]["effective_version"] != "1.0.0"
    assert second_project["entry_url"].endswith("/web/index.html")

    first_asset = await client.get(first_project["entry_url"])
    second_asset = await client.get(second_project["entry_url"])
    assert first_asset.status_code == 200
    assert second_asset.status_code == 200
    assert "v1" in first_asset.text
    assert "v2" in second_asset.text


@pytest.mark.asyncio
async def test_project_upload_rejects_frontend_ai_secret_or_direct_model_endpoint(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import json
    import tarfile

    from app.projects import service as project_service

    manifest = {
        "project_id": "unsafe_static_tool",
        "name": "不安全静态项目",
        "kind": "web_static",
        "entry": "web/index.html",
        "visibility": "department",
        "capabilities": ["ai.generate"],
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        html = (
            b"<html><script>"
            b"fetch('https://api.openai.com/v1/chat/completions',"
            b"{headers:{Authorization:'Bearer sk-testunsafeprojectsecret000000000000'}})"  # public-scan: synthetic-fixture; deliberate redaction/rejection test
            b"</script></html>"
        )
        info = tarfile.TarInfo("web/index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
    data = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    resp = await client.post(
        "/api/projects/upload",
        data={"manifest_json": json.dumps(manifest, ensure_ascii=False), "package_hash": "sha256:" + hashlib.sha256(data).hexdigest()},
        files={"package": ("project.tar.gz", data, "application/gzip")},
    )

    assert resp.status_code == 400
    body = resp.json()["error"]
    assert body["code"] == "PROJECT_INVALID"
    assert body["detail"]["field"] == "package"
    assert body["detail"]["file"] == "web/index.html"
    assert "Project Gateway" in body["detail"]["reason"]
    unsafe_asset_dir = tmp_path / "project-assets" / "unsafe_static_tool"
    assert not any(path.is_file() for path in unsafe_asset_dir.rglob("*"))


@pytest.mark.asyncio
async def test_project_upload_allows_provider_endpoint_when_gateway_proxy_can_take_over_ai(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import json
    import tarfile

    from app.projects import service as project_service

    manifest = {
        "project_id": "provider_proxy_static_tool",
        "name": "Provider Proxy Static Tool",
        "kind": "web_static",
        "entry": "web/index.html",
        "visibility": "department",
        "capabilities": ["ai.cheap.chat", "ai.generate"],
    }
    html = b"""<!doctype html><html><body><button id="ask">Ask</button><script>
      async function ask() {
        const res = await fetch('https://api.openai.com/v1/chat/completions', {
          method: 'POST',
          body: JSON.stringify({messages:[{role:'user',content:'evaluate project'}]})
        })
        document.body.dataset.status = res.ok ? 'ok' : 'failed'
      }
      document.getElementById('ask').addEventListener('click', ask)
    </script></body></html>"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        info = tarfile.TarInfo("web/index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
    data = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    resp = await client.post(
        "/api/projects/upload",
        data={"manifest_json": json.dumps(manifest, ensure_ascii=False), "package_hash": "sha256:" + hashlib.sha256(data).hexdigest()},
        files={"package": ("provider-proxy.tar.gz", data, "application/gzip")},
    )

    assert resp.status_code == 200, resp.text
    item = resp.json()
    security = item["metadata"]["package_security"]
    assert security["frontend_secret_scan"] == "pass"
    assert security["direct_ai_endpoint_count"] == 1
    assert security["direct_ai_endpoints"][0]["match"] == "openai_endpoint"
    eval_checks = {check["key"]: check for check in item["metadata"]["runtime_evaluation"]["checks"]}
    assert eval_checks["gateway_autowire"]["status"] == "pass"
    assert eval_checks["ai_provider_gateway_proxy"]["status"] == "pass"
    assert "Project Autowire" in eval_checks["ai_provider_gateway_proxy"]["detail"]


@pytest.mark.asyncio
async def test_project_upload_rejects_unbuilt_frontend_source_entry(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import json
    import tarfile

    from app.projects import service as project_service

    manifest = {
        "project_id": "unbuilt_vite_tool",
        "name": "未构建 Vite 项目",
        "kind": "web_static",
        "entry": "index.html",
        "visibility": "department",
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        html = b'<html><body><div id="app"></div><script type="module" src="/src/main.ts"></script></body></html>'
        info = tarfile.TarInfo("index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
        source = b"console.log('needs build')"
        info = tarfile.TarInfo("src/main.ts")
        info.size = len(source)
        tar.addfile(info, io.BytesIO(source))
    data = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    resp = await client.post(
        "/api/projects/upload",
        data={"manifest_json": json.dumps(manifest, ensure_ascii=False), "package_hash": "sha256:" + hashlib.sha256(data).hexdigest()},
        files={"package": ("project.tar.gz", data, "application/gzip")},
    )

    assert resp.status_code == 400
    body = resp.json()["error"]
    assert body["code"] == "PROJECT_INVALID"
    assert body["detail"]["field"] == "entry"
    assert body["detail"]["file"] == "index.html"
    assert body["detail"]["dependency"] == "/src/main.ts"
    assert "dist/build/out" in body["detail"]["reason"]


@pytest.mark.asyncio
async def test_project_auto_register_external_manifest_without_manual_fields(client):
    resp = await client.post(
        "/api/projects/auto-register",
        json={
            "source": "sf_auto_register",
            "manifest": {
                "project_id": "auto_external",
                "name": "自动外部项目",
                "kind": "external_web",
                "entry_url": "https://example.com/auto",
                "visibility": "department",
                "capabilities": ["ai.generate"],
                "outputs": {"reports": True},
            },
        },
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()
    assert item["id"] == "auto_external"
    assert item["entry_url"] == "https://example.com/auto"
    assert item["metadata"]["auto_registered"] is True
    assert item["metadata"]["source"] == "sf_auto_register"
    assert item["metadata"]["capabilities"] == ["ai.generate"]
    assert item["permissions"]["launch"] is True

    detail = (await client.get("/api/projects/auto_external")).json()
    assert detail["current_version_id"]
    assert detail["runtime"]["containerized"] is False


@pytest.mark.asyncio
async def test_project_upload_static_zip_package_auto_reads_manifest(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import zipfile

    from app.projects import service as project_service

    manifest = {
        "project_id": "zip_tool",
        "name": "Zip 静态项目",
        "kind": "web_static",
        "entry": "web/index.html",
        "visibility": "department",
        "capabilities": ["ai.generate"],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as archive:
        archive.writestr("projectforge.yaml", __import__("yaml").safe_dump(manifest, allow_unicode=True))
        archive.writestr("web/index.html", "<html><body>zip ok</body></html>")
    data = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    resp = await client.post(
        "/api/projects/upload",
        data={"package_hash": "sha256:" + hashlib.sha256(data).hexdigest()},
        files={"package": ("project.zip", data, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    uploaded = resp.json()
    assert uploaded["id"] == "zip_tool"
    assert uploaded["entry_url"].startswith("/api/projects/assets/zip_tool/")
    assert uploaded["metadata"]["package_format"] == "zip"
    assert uploaded["metadata"]["capabilities"] == ["ai.generate"]
    assert (tmp_path / "project-assets" / "zip_tool" / uploaded["asset_version"] / "web" / "index.html").is_file()
    asset = await client.get(uploaded["entry_url"])
    assert asset.status_code == 200
    assert "zip ok" in asset.text
    assert "sandbox" in asset.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_project_upload_auto_detects_common_static_entry_when_manifest_omits_entry(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import tarfile

    from app.projects import service as project_service

    manifest = {
        "project_id": "dist_tool",
        "name": "Dist 静态项目",
        "kind": "web_static",
        "visibility": "department",
        "capabilities": ["ai.generate"],
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        html = b"<html><body>dist ok</body></html>"
        info = tarfile.TarInfo("dist/index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
    data = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    resp = await client.post(
        "/api/projects/upload",
        data={"package_hash": "sha256:" + hashlib.sha256(data).hexdigest()},
        files={"package": ("project.tar.gz", data, "application/gzip")},
    )
    assert resp.status_code == 200, resp.text
    uploaded = resp.json()
    assert uploaded["id"] == "dist_tool"
    assert uploaded["entry_url"].endswith("/dist/index.html")
    assert uploaded["metadata"]["local_entry"] == "dist/index.html"
    asset = await client.get(uploaded["entry_url"])
    assert asset.status_code == 200
    assert "dist ok" in asset.text
    assert "sandbox" in asset.headers["content-security-policy"]
    spa_route = await client.get(uploaded["entry_url"].replace("dist/index.html", "orders/42"))
    assert spa_route.status_code == 200
    assert "dist ok" in spa_route.text
    assert "sandbox" in spa_route.headers["content-security-policy"]
    missing_asset = await client.get(uploaded["entry_url"].replace("dist/index.html", "missing/app.js"))
    assert missing_asset.status_code == 404


@pytest.mark.asyncio
async def test_project_upload_auto_detects_github_corpus_nested_frontend_entries(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import tarfile

    from app.projects import service as project_service

    for project_id, entry in [
        ("github_frontend_tool", "frontend/dist/index.html"),
        ("github_client_tool", "client/dist/index.html"),
        ("github_apps_web_tool", "apps/web/out/index.html"),
    ]:
        manifest = {
            "project_id": project_id,
            "name": f"GitHub Corpus {project_id}",
            "kind": "web_static",
            "visibility": "department",
            "capabilities": ["ai.cheap.generate"],
            "metadata": {"github_repo": f"sample/{project_id}"},
        }
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
            info = tarfile.TarInfo("projectforge.yaml")
            info.size = len(manifest_bytes)
            tar.addfile(info, io.BytesIO(manifest_bytes))
            html = f'<html><body>{project_id}<script src="/assets/app.js"></script></body></html>'.encode("utf-8")
            info = tarfile.TarInfo(entry)
            info.size = len(html)
            tar.addfile(info, io.BytesIO(html))
            asset_path = str(__import__("pathlib").PurePosixPath(entry).parent / "assets/app.js")
            asset = b"console.log('nested github corpus')"
            info = tarfile.TarInfo(asset_path)
            info.size = len(asset)
            tar.addfile(info, io.BytesIO(asset))
        data = buf.getvalue()
        monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

        resp = await client.post(
            "/api/projects/upload",
            data={"package_hash": "sha256:" + hashlib.sha256(data).hexdigest()},
            files={"package": (f"{project_id}.tar.gz", data, "application/gzip")},
        )
        assert resp.status_code == 200, resp.text
        uploaded = resp.json()
        assert uploaded["metadata"]["local_entry"] == entry
        assert uploaded["entry_url"].endswith(f"/{entry}")
        assert uploaded["metadata"]["manifest_metadata"]["github_repo"] == f"sample/{project_id}"
        asset = await client.get(uploaded["entry_url"])
        assert "/api/projects/assets/" in asset.text
        entry_parent = str(__import__("pathlib").PurePosixPath(entry).parent)
        assert 'src="/assets/app.js"' not in asset.text
        assert f"/{entry_parent}/assets/app.js" in asset.text


@pytest.mark.asyncio
async def test_project_upload_records_runtime_evaluation_and_endpoint(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import tarfile

    from app.projects import service as project_service

    manifest = {
        "project_id": "runtime_eval_tool",
        "name": "运行评估工具",
        "kind": "web_static",
        "entry": "web/index.html",
        "visibility": "department",
        "capabilities": ["ai.cheap.generate"],
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        html = b'<html><head></head><body><script src="/assets/app.js"></script></body></html>'
        info = tarfile.TarInfo("web/index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
        js = b"console.log('normal run ready')"
        info = tarfile.TarInfo("web/assets/app.js")
        info.size = len(js)
        tar.addfile(info, io.BytesIO(js))
    data = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    resp = await client.post(
        "/api/projects/upload",
        data={"package_hash": "sha256:" + hashlib.sha256(data).hexdigest()},
        files={"package": ("runtime-eval.tar.gz", data, "application/gzip")},
    )
    assert resp.status_code == 200, resp.text
    uploaded = resp.json()
    evaluation = uploaded["metadata"]["runtime_evaluation"]
    assert evaluation["status"] == "pass"
    assert evaluation["normal_run_ready"] is True
    assert {item["key"] for item in evaluation["checks"]} >= {
        "entry_exists",
        "asset_rewrites",
        "referenced_assets_exist",
        "gateway_autowire",
        "service_conversion",
    }

    endpoint = (await client.get("/api/projects/runtime_eval_tool/runtime-evaluation")).json()
    assert endpoint["ok"] is True
    assert endpoint["runtime_evaluation"]["summary"] == "可正常打开运行"
    assert endpoint["runtime_evaluation"]["entry_url"] == uploaded["entry_url"]


@pytest.mark.asyncio
async def test_project_ingest_records_result_evaluation_for_normal_run(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "result_eval_tool",
            "name": "结果评估工具",
            "type": "external_web",
            "entry_url": "https://example.com/result-eval",
            "visibility": "department",
        },
    )
    run = (await client.post("/api/projects/result_eval_tool/runs", json={"request_id": "result-eval-open"})).json()
    ingested = (
        await client.post(
            f"/api/projects/runs/{run['id']}/ingest",
            json={
                "request_id": "result-eval-output",
                "auto_analyze": False,
                "input": {"prompt": "评估运行"},
                "output": {"summary": "已正常运行"},
                "reports": [{"title": "运行报告", "summary": "正常"}],
                "todos": [{"kind": "review", "title": "复核结果", "reviewers": ["admin"]}],
                "proofs": [{"type": "http_probe", "status": 200}],
            },
        )
    ).json()
    evaluation = ingested["ai_summary"]["result_evaluation"]
    assert evaluation["status"] == "pass"
    assert evaluation["normal_result_ready"] is True
    checks = {item["key"]: item for item in evaluation["checks"]}
    assert checks["input_recorded"]["status"] == "pass"
    assert checks["output_recorded"]["status"] == "pass"
    assert checks["reports"]["count"] == 1
    assert checks["todos"]["count"] == 1
    assert checks["proofs"]["count"] == 1


@pytest.mark.asyncio
async def test_project_upload_auto_detects_build_static_entry_when_manifest_omits_entry(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import tarfile

    from app.projects import service as project_service

    manifest = {
        "project_id": "build_tool",
        "name": "Build 静态项目",
        "kind": "web_static",
        "visibility": "department",
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        html = b"<html><body>build ok</body></html>"
        info = tarfile.TarInfo("build/index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
    data = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    resp = await client.post(
        "/api/projects/upload",
        data={"package_hash": "sha256:" + hashlib.sha256(data).hexdigest()},
        files={"package": ("project.tar.gz", data, "application/gzip")},
    )
    assert resp.status_code == 200, resp.text
    uploaded = resp.json()
    assert uploaded["id"] == "build_tool"
    assert uploaded["entry_url"].endswith("/build/index.html")
    assert uploaded["metadata"]["local_entry"] == "build/index.html"


@pytest.mark.asyncio
async def test_codex_project_submit_status_and_logs_use_cli_token(client, monkeypatch, tmp_path):
    import hashlib
    import io
    import json
    import tarfile

    from app.auth.models import User
    from app.codex import service as codex_service
    from app.codex.models import CodexCliSession
    from app.projects import service as project_service

    import app.database as db_mod

    raw_token = "test-codex-project-token"
    async with db_mod.async_session_factory() as db:
        user = User(
            id="codex_project_user",
            username="codex_project_user",
            name="Codex Project 用户",
            role="aibp",
            department="项目部",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        cli_session = CodexCliSession(
            id="cli_codex_project_user",
            user_id=user.id,
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "pytest"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        db.add_all([user, cli_session])
        await db.commit()

    manifest = {
        "project_id": "codex_upload_project",
        "name": "Codex 上传项目",
        "kind": "web_static",
        "entry": "web/index.html",
        "visibility": "department",
        "capabilities": ["ai.generate"],
        "outputs": {"reports": True, "todos": True},
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = __import__("yaml").safe_dump(manifest, allow_unicode=True).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        html = b"<html><body>codex project</body></html>"
        info = tarfile.TarInfo("web/index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
    package = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    headers = {"Authorization": f"Bearer {raw_token}"}
    sf_auto_build = {
        "ok": True,
        "command": "npm run build",
        "original_entry": "index.html",
        "entry": "web/index.html",
    }
    uploaded_resp = await client.post(
        "/api/codex/projects/upload",
        headers=headers,
        data={
            "manifest_json": json.dumps(manifest, ensure_ascii=False),
            "package_hash": "sha256:" + hashlib.sha256(package).hexdigest(),
            "sf_auto_build_json": json.dumps(sf_auto_build, ensure_ascii=False),
        },
        files={"package": ("project.tar.gz", package, "application/gzip")},
    )
    assert uploaded_resp.status_code == 200, uploaded_resp.text
    uploaded = uploaded_resp.json()
    assert uploaded["ok"] is True
    assert uploaded["project_id"] == "codex_upload_project"
    assert uploaded["project"]["entry_url"].startswith("/api/projects/assets/codex_upload_project/")
    assert uploaded["project"]["metadata"]["sf_auto_build"]["command"] == "npm run build"
    assert uploaded["runtime"]["containerized"] is False
    assert uploaded["app_url"].endswith("/project-run/codex_upload_project")
    assert uploaded["trace_url"].endswith("/projects/codex_upload_project")
    assert uploaded["relative_urls"]["app"] == "/project-run/codex_upload_project"

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        return {
            "ok": True,
            "model": "codex-cli-service-model",
            "model_profile": args.get("model_profile") or "default",
            "output": {"summary": "Codex CLI 项目服务调用完成"},
            "prompt_hash": "codex-cli-service-hash",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)
    service_resp = await client.post(
        "/api/codex/projects/codex_upload_project/service/invoke",
        headers=headers,
        json={
            "request_id": "codex-service-001",
            "input": {"query": "cli invoke", "token": "secret"},
            "prompt": "通过 Codex CLI 调用项目服务",
            "auto_analyze": False,
        },
    )
    assert service_resp.status_code == 200, service_resp.text
    service_payload = service_resp.json()
    assert service_payload["ok"] is True
    assert service_payload["project_id"] == "codex_upload_project"
    assert service_payload["capability"]["key"] == "ai.generate"
    assert service_payload["project_run_id"]
    assert service_payload["app_url"].endswith(f"/project-run/codex_upload_project?run_id={service_payload['project_run_id']}")
    assert service_payload["trace_url"].endswith(f"/projects/codex_upload_project?run_id={service_payload['project_run_id']}")

    service_logs = (await client.get(f"/api/codex/projects/runs/{service_payload['project_run_id']}/logs", headers=headers)).json()
    assert service_logs["ok"] is True
    assert service_logs["counts"]["input_events_total"] == 1
    assert service_logs["counts"]["output_events_total"] == 1
    assert service_logs["counts"]["capability_calls_total"] == 1
    service_input_event = next(event for event in service_logs["ingress_events"] if event["event_type"] == "input")
    assert service_input_event["input_snapshot"]["token"] == "[REDACTED]"

    run = (await client.post("/api/projects/codex_upload_project/runs", json={"request_id": "codex-open-001"})).json()
    await client.post(
        f"/api/projects/runs/{run['id']}/ingest",
        json={
            "request_id": "codex-ingest-001",
            "auto_analyze": False,
            "input": {"api_key": "secret-key", "query": "visible"},
            "output": {"summary": "Codex 项目输出", "token": "secret-token"},
            "reports": [{"title": "Codex 报告", "summary": "已进入平台"}],
        },
    )

    status = (await client.get("/api/codex/projects/codex_upload_project/status", headers=headers)).json()
    assert status["ok"] is True
    assert status["project_id"] == "codex_upload_project"
    assert status["current_version_id"]
    assert status["runs"][0]["id"] == run["id"]
    assert status["runs"][0]["request_id"] == "codex-open-001"
    assert status["app_url"].endswith(f"/project-run/codex_upload_project?run_id={run['id']}")
    assert status["trace_url"].endswith(f"/projects/codex_upload_project?run_id={run['id']}")

    logs = (await client.get(f"/api/codex/projects/runs/{run['id']}/logs", headers=headers)).json()
    assert logs["ok"] is True
    assert logs["counts"]["ingress_events"] == 1
    assert logs["ingress_events"][0]["request_id"] == "codex-ingest-001"
    assert logs["ingress_events"][0]["input_snapshot"]["api_key"] == "[REDACTED]"
    assert logs["ingress_events"][0]["output_result"]["token"] == "[REDACTED]"
    assert logs["app_url"].endswith(f"/project-run/codex_upload_project?run_id={run['id']}")
    assert logs["trace_url"].endswith(f"/projects/codex_upload_project?run_id={run['id']}")

    paged_logs = (
        await client.get(f"/api/codex/projects/runs/{run['id']}/logs", params={"limit": 1, "offset": 0}, headers=headers)
    ).json()
    assert paged_logs["pagination"]["limit"] == 1
    assert paged_logs["counts"]["ingress_events_total"] == 1
    assert paged_logs["counts"]["output_events_total"] == 1


@pytest.mark.asyncio
async def test_project_upload_rejects_package_larger_than_platform_limit(client, monkeypatch, tmp_path):
    import io
    import tarfile

    from app.projects import service as project_service

    monkeypatch.setattr(project_service, "PROJECT_PACKAGE_MAX_BYTES", 128)
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")
    buf = io.BytesIO()
    manifest = b"project_id: too_big_package\nname: Too Big\nkind: web_static\nentry: web/index.html\n"
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
        html = b"<html>" + b"x" * 4096 + b"</html>"
        info = tarfile.TarInfo("web/index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
    data = buf.getvalue()
    assert len(data) > 128

    resp = await client.post(
        "/api/projects/upload",
        files={"package": ("project.tar.gz", data, "application/gzip")},
    )
    assert resp.status_code == 413
    body = resp.json()["error"]
    assert body["code"] == "PROJECT_PACKAGE_TOO_LARGE"
    assert body["detail"]["reason"] == "package_bytes"
    assert not (tmp_path / "project-assets" / "too_big_package").exists()


@pytest.mark.asyncio
async def test_project_upload_rejects_zip_with_excessive_extracted_size(client, monkeypatch, tmp_path):
    import io
    import json
    import zipfile

    from app.projects import service as project_service

    manifest = {
        "project_id": "zip_bomb_project",
        "name": "Zip Bomb Project",
        "kind": "web_static",
        "entry": "web/index.html",
        "visibility": "department",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("projectforge.yaml", "project_id: zip_bomb_project\nname: Zip Bomb Project\n")
        archive.writestr("web/index.html", "<html><body>ok</body></html>")
        archive.writestr("web/large.txt", "x" * 4096)
    data = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_PACKAGE_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(project_service, "PROJECT_PACKAGE_MAX_EXTRACTED_BYTES", 512)
    monkeypatch.setattr(project_service, "PROJECT_PACKAGE_MAX_FILES", 20)
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    resp = await client.post(
        "/api/projects/upload",
        data={"manifest_json": json.dumps(manifest, ensure_ascii=False)},
        files={"package": ("project.zip", data, "application/zip")},
    )
    assert resp.status_code == 413
    body = resp.json()["error"]
    assert body["code"] == "PROJECT_PACKAGE_TOO_LARGE"
    assert body["detail"]["reason"] == "extracted_bytes"
    asset_dir = tmp_path / "project-assets" / "zip_bomb_project"
    assert not asset_dir.exists() or not any(asset_dir.rglob("*"))


@pytest.mark.asyncio
async def test_project_upload_rejects_package_with_too_many_files(client, monkeypatch, tmp_path):
    import io
    import json
    import zipfile

    from app.projects import service as project_service

    manifest = {
        "project_id": "too_many_files_project",
        "name": "Too Many Files Project",
        "kind": "web_static",
        "entry": "web/index.html",
        "visibility": "department",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as archive:
        archive.writestr("projectforge.yaml", "project_id: too_many_files_project\nname: Too Many Files Project\n")
        archive.writestr("web/index.html", "<html><body>ok</body></html>")
        archive.writestr("web/a.js", "a")
        archive.writestr("web/b.js", "b")
    data = buf.getvalue()
    monkeypatch.setattr(project_service, "PROJECT_PACKAGE_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(project_service, "PROJECT_PACKAGE_MAX_EXTRACTED_BYTES", 1024 * 1024)
    monkeypatch.setattr(project_service, "PROJECT_PACKAGE_MAX_FILES", 3)
    monkeypatch.setattr(project_service, "PROJECT_ASSETS_DIR", tmp_path / "project-assets")

    resp = await client.post(
        "/api/projects/upload",
        data={"manifest_json": json.dumps(manifest, ensure_ascii=False)},
        files={"package": ("project.zip", data, "application/zip")},
    )
    assert resp.status_code == 413
    body = resp.json()["error"]
    assert body["code"] == "PROJECT_PACKAGE_TOO_LARGE"
    assert body["detail"]["reason"] == "file_count"
    asset_dir = tmp_path / "project-assets" / "too_many_files_project"
    assert not asset_dir.exists() or not any(asset_dir.rglob("*"))
