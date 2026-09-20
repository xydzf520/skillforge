import gzip
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def configured_example_recipient(monkeypatch):
    monkeypatch.setattr("app.todos.service.TMALL_LINK_DECLINE_OPERATOR_INBOX_ASSIGNEE_QUERY", "示例成员甲")
    monkeypatch.setattr("app.todos.service.TMALL_LINK_DECLINE_OPERATOR_INBOX_DINGTALK_USER_ID", "900000000000000001")
    monkeypatch.setattr("app.execution.execution_service.LINK_DECLINE_OPERATOR_SUCCESS_RECIPIENT_QUERY", "示例成员甲")
    monkeypatch.setattr("app.execution.execution_service.LINK_DECLINE_OPERATOR_SUCCESS_DINGTALK_USER_ID", "900000000000000001")


SDK_PATH = Path(__file__).resolve().parents[1] / "app" / "skill_runtime_sdk" / "skillforge_sdk.py"


def test_skillforge_submit_merges_reports_and_todos(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("skillforge_sdk_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_post(url, json, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("SKILLFORGE_BASE_URL", "http://skillforge.test")
    monkeypatch.setenv("BROWSER_API_TOKEN", "token-1")
    monkeypatch.setenv("OPENCLAW_INSTANCE_ID", "inst-sdk-1")
    monkeypatch.setenv("SKILLFORGE_RUN_TOKEN", "run-token-1")
    monkeypatch.setenv("SKILL_VERSION", "v1.2.3")
    monkeypatch.setenv("OPENCLAW_AGENT_TYPE", "aiclaw")
    monkeypatch.setattr(sdk.requests, "post", fake_post)

    sf = sdk.SkillForge("skill-sdk")
    report = sf.report_card(
        "新增报告",
        "报告摘要",
        detail="明细内容",
        sections=[{"title": "分段", "content": "分段内容"}],
        recipients=["u1"],
        channel="dingtalk_card",
    )
    todo = sf.todo_review("新增待办", reviewers=["u1"])

    result = sf.submit(
        output={
            "summary": "ok",
            "reports": [{"title": "已有报告", "summary": "已有摘要"}],
            "todos": [{"kind": "review", "title": "已有待办"}],
        },
        reports=[report],
        todos=[todo],
        params={"date": "2026-04-21"},
        idempotency_key="idem-sdk-1",
        run_id="run-sdk-1",
    )

    payload = captured["json"]
    assert result == {"ok": True}
    assert captured["url"] == "http://skillforge.test/api/executions/submit-result?token=token-1"
    assert captured["headers"] == {"X-Run-Token": "run-token-1"}
    assert payload["idempotency_key"] == "idem-sdk-1"
    assert payload["run_id"] == "run-sdk-1"
    assert payload["instance_id"] == "inst-sdk-1"
    assert payload["skill_version"] == "v1.2.3"
    assert payload["agent_type"] == "aiclaw"
    assert payload["data_provenance"] == []
    assert payload["output"]["_skillforge_meta"]["data_proofs"] == []
    assert payload["output"]["reports"] == [
        {"title": "已有报告", "summary": "已有摘要"},
        report,
    ]
    assert payload["output"]["todos"] == [
        {"kind": "review", "title": "已有待办"},
        todo,
    ]
    assert report["content_markdown"] == "明细内容\n\n## 分段\n\n分段内容"
    assert report["channel"] == "dingtalk_card"
    assert report["recipients"] == {"users": ["u1"]}


def test_skillforge_submit_carries_injected_runtime_model_context(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("skillforge_sdk_model_context_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_post(url, json, headers=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    model_context = {
        "model_deployment_id": "deploy-submit-runtime",
        "model_family": "lora",
        "artifact_sha256": "f" * 64,
        "active_model_deployment": {
            "model_deployment_id": "deploy-submit-runtime",
            "model_family": "lora",
            "deployment_status": "active",
            "artifact_sha256": "f" * 64,
        },
        "control": {"agent_contract": "skill_runtime_model_context.v1"},
    }
    monkeypatch.setenv("SKILLFORGE_BASE_URL", "http://skillforge.test")
    monkeypatch.setenv("BROWSER_API_TOKEN", "token-1")
    monkeypatch.setenv("SKILLFORGE_MODEL_CONTEXT_JSON", json.dumps(model_context))
    monkeypatch.setattr(sdk.requests, "post", fake_post)

    sf = sdk.SkillForge("skill-sdk")
    result = sf.submit(output={"summary": "ok"}, params={"date": "2026-06-18"})

    assert result == {"ok": True}
    payload = captured["json"]
    assert payload["params"]["model_context"]["model_deployment_id"] == "deploy-submit-runtime"
    assert payload["output"]["_skillforge_meta"]["model_context"]["model_deployment_id"] == "deploy-submit-runtime"
    assert payload["output"]["_skillforge_meta"]["active_model_deployment"]["artifact_sha256"] == "f" * 64


def test_skillforge_submit_falls_back_to_urllib_without_requests(monkeypatch):
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location("skillforge_sdk_urllib_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)
    monkeypatch.setattr(sdk, "requests", None)

    captured = {}

    class FakeUrlopen:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeUrlopen()

    monkeypatch.setenv("SKILLFORGE_BASE_URL", "http://skillforge.test")
    monkeypatch.setenv("BROWSER_API_TOKEN", "token-1")
    monkeypatch.setenv("SKILLFORGE_RUN_TOKEN", "run-token-1")
    monkeypatch.setenv("SKILLFORGE_INSTANCE_ID", "node-1")
    monkeypatch.setattr(sdk.urllib.request, "urlopen", fake_urlopen)

    sf = sdk.SkillForge("skill-sdk")
    result = sf.submit(output={"summary": "ok"}, run_id="run-sdk-1")

    assert result == {"ok": True}
    assert captured["url"] == "http://skillforge.test/api/executions/submit-result?token=token-1"
    assert captured["method"] == "POST"
    assert captured["headers"]["X-run-token"] == "run-token-1"
    assert captured["body"]["run_id"] == "run-sdk-1"
    assert captured["body"]["instance_id"] == "node-1"


def test_skillforge_fetch_api_records_data_provenance(monkeypatch):
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location("skillforge_sdk_fetch_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)

    sf = sdk.SkillForge("skill-fetch")
    monkeypatch.setattr(
        sf,
        "browser_collect",
        lambda *_args, **_kwargs: {
            "data": json.dumps({
                "status": 200,
                "data": {"rows": [{"sku": "A", "gmv": 12}, {"sku": "B", "gmv": 5}]},
            })
        },
    )

    data = sf.fetch_api("https://example.test/api/rows", method="post", is_sample=True)

    proof = sf._data_proofs[0]
    assert data["rows"][0]["sku"] == "A"
    assert proof["url"] == "https://example.test/api/rows"
    assert proof["method"] == "POST"
    assert proof["status"] == 200
    assert proof["row_count"] == 2
    assert proof["is_sample"] is True
    assert set(proof["sample_keys"]) == {"rows"}


def test_skillforge_fetch_api_records_collection_proofs_from_mcp(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("skillforge_sdk_mcp_proof_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)

    sf = sdk.SkillForge("skill-fetch")
    monkeypatch.setattr(sf, "_run_token", lambda: "run-token-1")
    monkeypatch.setattr(
        sf,
        "_call_platform_mcp_tool",
        lambda *_args, **_kwargs: (
            {
                "ok": True,
                "_skillforge_meta": {
                    "collection_proofs": [
                        {
                            "proof_id": "proof-1",
                            "tool_name": "tmall_item_promotion_required_metrics",
                            "platform": "alimama",
                        }
                    ]
                },
            },
            {"mcp_runtime_source": "skillforge_gateway"},
        ),
    )

    data = sf.fetch_api("mcp://tmall_item_promotion_required_metrics", body={"itemId": "8001"})

    assert data["ok"] is True
    assert sf._collection_proofs == [
        {
            "proof_id": "proof-1",
            "tool_name": "tmall_item_promotion_required_metrics",
            "platform": "alimama",
        }
    ]
    assert sf._data_proofs[0]["proof_ids"] == ["proof-1"]


def test_skillforge_submit_carries_collection_proofs(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("skillforge_sdk_submit_collection_proof_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_post(url, json, headers=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setenv("SKILLFORGE_BASE_URL", "http://skillforge.test")
    monkeypatch.setattr(sdk.requests, "post", fake_post)

    sf = sdk.SkillForge("skill-fetch")
    sf._merge_collection_proofs([
        {"proof_id": "proof-1", "tool_name": "tmall_item_flow_required_metrics", "platform": "sycm"}
    ])

    sf.submit(output={"summary": "ok"})

    meta = captured["json"]["output"]["_skillforge_meta"]
    assert meta["collection_proofs"] == [
        {"proof_id": "proof-1", "tool_name": "tmall_item_flow_required_metrics", "platform": "sycm"}
    ]


def test_skillforge_fetch_api_uses_fetch_json_local_when_page_url_given(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("skillforge_sdk_fetch_page_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)

    sf = sdk.SkillForge("skill-fetch")
    sf._browser_cfg = {"url": "http://127.0.0.1:8000/api/browser/collect-local", "token": ""}
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "data": {
                    "status": 200,
                    "ok": True,
                    "data": {"rows": [{"sku": "A", "gmv": 12}]},
                },
            }

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(sdk.requests, "post", fake_post)

    data = sf.fetch_api(
        "https://example.test/api/rows",
        method="post",
        page_url="https://example.test/page",
        headers={"x-test": "1"},
        body={"q": "sku"},
    )

    assert captured["url"] == "http://127.0.0.1:8000/api/browser/fetch-json-local"
    assert captured["json"]["page_url"] == "https://example.test/page"
    assert captured["json"]["headers"] == {"x-test": "1"}
    assert captured["json"]["body"] == {"q": "sku"}
    assert data["rows"][0]["sku"] == "A"
    assert sf._data_proofs[0]["status"] == 200


def test_skillforge_fetch_api_passes_collection_cookie_context(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("skillforge_sdk_collection_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)

    sf = sdk.SkillForge("skill-fetch")
    sf._sf_cfg = {"base_url": "http://skillforge.test", "token": ""}
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "data": {
                    "status": 200,
                    "ok": True,
                    "data": {"rows": [{"sku": "A"}]},
                },
                "proof": {"proof_id": "proof-1"},
            }

    def fake_post(url, json, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setenv("SKILLFORGE_RUN_TOKEN", "run-token-1")
    monkeypatch.setenv("SKILLFORGE_RUN_ID", "run-1")
    monkeypatch.setenv("SKILLFORGE_INSTANCE_ID", "node-1")
    monkeypatch.setattr(sdk.requests, "post", fake_post)

    data = sf.fetch_api(
        "https://sycm.taobao.com/api.json",
        platform="sycm",
        shop_id="shop-1",
        source_id="platform-sycm-team-a",
        data_scope="sycm.item_rank",
        endpoint_family="read_metrics",
        warning_group="sycm.report_read",
        credential_scope="team-a",
        credential_plan_id="plan-1",
    )

    assert data["rows"][0]["sku"] == "A"
    assert captured["url"] == "http://skillforge.test/api/collection/fetch"
    assert captured["headers"] == {"X-Run-Token": "run-token-1"}
    assert captured["json"]["source_id"] == "platform-sycm-team-a"
    assert captured["json"]["credential_scope"] == "team-a"
    assert captured["json"]["credential_plan_id"] == "plan-1"
    assert captured["json"]["run_id"] == "run-1"
    assert captured["json"]["instance_id"] == "node-1"


def test_skillforge_mcp_fetch_requires_gateway_token_in_production(monkeypatch, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("skillforge_sdk_mcp_production_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)

    platform_dir = tmp_path / "platform_mcp"
    platform_dir.mkdir()
    (platform_dir / "tmall_mcp_server.py").write_text("# platform runtime\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SKILLFORGE_MCP_CANONICAL_DIR", str(platform_dir))

    sf = sdk.SkillForge("skill-mcp")
    with pytest.raises(RuntimeError, match="MCP_ENV_MISSING"):
        sf.fetch_api("mcp://tmall_sycm_item_rank_top", body={"limit": 1})


def test_skillforge_mcp_fetch_prefers_platform_shared_runtime_in_local(monkeypatch, tmp_path):
    import importlib.util
    import json
    import subprocess

    spec = importlib.util.spec_from_file_location("skillforge_sdk_mcp_shared_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)

    platform_dir = tmp_path / "platform_mcp"
    bundled_dir = tmp_path / "skill" / "scripts"
    platform_dir.mkdir()
    bundled_dir.mkdir(parents=True)
    platform_script = platform_dir / "tmall_mcp_server.py"
    bundled_script = bundled_dir / "tmall_mcp_server.py"
    platform_script.write_text("# platform runtime\n")
    bundled_script.write_text("# bundled runtime\n")

    captured = {}

    def fake_run(args, **kwargs):
        captured["script"] = args[1]
        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps({"ok": True})}]},
        }
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(response), stderr="")

    monkeypatch.chdir(tmp_path / "skill")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SKILLFORGE_MCP_CANONICAL_DIR", str(platform_dir))
    monkeypatch.setattr(sdk.subprocess, "run", fake_run)

    sf = sdk.SkillForge("skill-mcp")
    data = sf.fetch_api("mcp://tmall_sycm_item_rank_top", body={"limit": 1})

    assert data == {"ok": True}
    assert captured["script"] == str(platform_script)
    assert sf._data_proofs[0]["mcp_runtime_source"] == "platform_shared"


def test_skillforge_mcp_fetch_rejects_bundled_runtime_in_local_without_opt_in(monkeypatch, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("skillforge_sdk_mcp_drift_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)

    bundled_dir = tmp_path / "skill" / "scripts"
    bundled_dir.mkdir(parents=True)
    (bundled_dir / "tmall_mcp_server.py").write_text("# bundled runtime\n")

    monkeypatch.chdir(tmp_path / "skill")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(sdk.SkillForge, "_platform_mcp_roots", lambda self: [])

    sf = sdk.SkillForge("skill-mcp")
    with pytest.raises(RuntimeError, match="MCP_RUNTIME_DRIFT"):
        sf.fetch_api("mcp://tmall_sycm_item_rank_top", body={"limit": 1})

    proof = sf._data_proofs[0]
    assert proof["status"] == "mcp_rejected"
    assert proof["mcp_runtime_source"] == "skill_bundled"
    assert proof["mcp_runtime_warning"] == "MCP_RUNTIME_DRIFT"


def test_skillforge_mcp_fetch_allows_bundled_runtime_with_local_opt_in(monkeypatch, tmp_path):
    import importlib.util
    import json
    import subprocess

    spec = importlib.util.spec_from_file_location("skillforge_sdk_mcp_local_drift_under_test", SDK_PATH)
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)

    bundled_dir = tmp_path / "skill" / "scripts"
    bundled_dir.mkdir(parents=True)
    bundled_script = bundled_dir / "tmall_mcp_server.py"
    bundled_script.write_text("# bundled runtime\n")

    def fake_run(args, **kwargs):
        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps({"ok": True})}]},
        }
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(response), stderr="")

    monkeypatch.chdir(tmp_path / "skill")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SKILLFORGE_ALLOW_SKILL_BUNDLED_MCP", "1")
    monkeypatch.setattr(sdk.SkillForge, "_platform_mcp_roots", lambda self: [])
    monkeypatch.setattr(sdk.subprocess, "run", fake_run)

    sf = sdk.SkillForge("skill-mcp")
    data = sf.fetch_api("mcp://tmall_sycm_item_rank_top", body={"limit": 1})

    assert data["ok"] is True
    assert data["_skillforge_meta"]["mcp_runtime_source"] == "skill_bundled"
    assert data["_skillforge_meta"]["mcp_runtime_warning"] == "MCP_RUNTIME_DRIFT"
    assert sf._data_proofs[0]["mcp_runtime_path"] == str(bundled_script)


@pytest.mark.asyncio
async def test_submit_result_todos_link_decision_log(client):
    from sqlalchemy import select

    from app.auth.models import User
    import app.database as db_mod
    from app.skills.models import Skill
    from app.todos.models import DecisionRequest

    async with db_mod.async_session_factory() as session:
        session.add(User(
            id="remote-reviewer",
            username="remote-reviewer",
            name="远程待办处理人",
            role="operator",
            is_active=True,
        ))
        session.add(Skill(
            id="skill-submit-todos",
            name="远程待办 Skill",
            department="EC",
            status="active",
            approval_level=1,
        ))
        await session.commit()

    resp = await client.post("/api/executions/submit-result", json={
        "skill_id": "skill-submit-todos",
        "params": {"date": "2026-04-21"},
        "triggered_by": "skill_sdk",
        "output": {
            "summary": "需要确认",
            "reports": [
                {
                    "title": "远程回推报告",
                    "summary": "含关联待办",
                    "content_markdown": "# 远程回推报告",
                }
            ],
            "todos": [
                {
                    "kind": "review",
                    "title": "确认远程回推结果",
                    "summary": "核对报告口径",
                    "reviewers": ["remote-reviewer"],
                }
            ],
        },
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["todo_created"] is True
    assert data["todo_count"] == 1
    assert data["decision_log_id"]

    async with db_mod.async_session_factory() as session:
        request = (
            await session.execute(
                select(DecisionRequest)
                .where(DecisionRequest.skill_id == "skill-submit-todos")
            )
        ).scalar_one()
        assert request.decision_log_id == data["decision_log_id"]
        assert request.title == "确认远程回推结果"


@pytest.mark.asyncio
async def test_submit_result_invalid_explicit_todos_exposes_error_without_legacy_fallback(client):
    from sqlalchemy import func, select

    import app.database as db_mod
    from app.skills.models import Skill
    from app.todos.models import DecisionRequest

    async with db_mod.async_session_factory() as session:
        session.add(Skill(
            id="skill-submit-invalid-todos",
            name="非法待办 Skill",
            department="EC",
            owner="owner-1",
            target_users=["fallback-reviewer"],
            status="active",
            approval_level=1,
        ))
        await session.commit()

    resp = await client.post("/api/executions/submit-result", json={
        "skill_id": "skill-submit-invalid-todos",
        "params": {"date": "2026-04-21"},
        "triggered_by": "skill_sdk",
        "output": {
            "summary": "dispatch 缺少 tasks 时必须暴露错误",
            "todos": [
                {
                    "kind": "dispatch",
                    "title": "非法 dispatch 待办",
                    "reviewers": ["fallback-reviewer"],
                }
            ],
        },
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["todo_created"] is False
    assert data["todo_count"] == 0
    assert data["todo_error"]["code"] == "TODO_SPEC_INVALID"
    assert "kind=dispatch 必须包含至少一条 tasks" in data["todo_error"]["detail"]["detail"]

    async with db_mod.async_session_factory() as session:
        request_count = await session.scalar(
            select(func.count(DecisionRequest.id)).where(
                DecisionRequest.skill_id == "skill-submit-invalid-todos"
            )
        )
    assert request_count == 0


@pytest.mark.asyncio
async def test_submit_result_reports_invalidate_cache_and_visible(client, monkeypatch):
    from app.auth.models import User
    import app.database as db_mod
    from app.inbox.service import inbox_service
    from app.skills.models import Skill

    invalidations = []

    async def fake_invalidate():
        invalidations.append("reports")
        return 1

    monkeypatch.setattr("app.common.cache.invalidate_generated_data_cache", fake_invalidate)

    async with db_mod.async_session_factory() as session:
        session.add(
            Skill(
                id="skill-submit-reports",
                name="远程报告 Skill",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            )
        )
        await session.commit()

    resp = await client.post("/api/executions/submit-result", json={
        "skill_id": "skill-submit-reports",
        "params": {"date": "2026-04-21"},
        "triggered_by": "skill_sdk",
        "output": {
            "summary": "报告完成",
            "reports": [
                {
                    "title": "远程入库报告",
                    "summary": "提交后应立即可见",
                    "content_markdown": "# 远程入库报告",
                }
            ],
        },
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["report_count"] == 1
    assert invalidations == ["reports"]

    current_user = User(
        id="admin",
        username="admin",
        name="admin",
        role="admin",
        state="active",
        permissions_rev=0,
        can_view_all=True,
        department="EC",
        is_active=True,
        must_change_password=False,
    )
    async with db_mod.async_session_factory() as session:
        reports = await inbox_service.list_reports(
            session,
            current_user=current_user,
            page=1,
            page_size=20,
            skill_id="skill-submit-reports",
        )

    assert reports["total"] == 1
    assert reports["items"][0]["title"] == "远程入库报告"
    assert reports["items"][0]["run_id"] == data["run_id"]
    async with db_mod.async_session_factory() as session:
        from sqlalchemy import select
        from app.inbox.models import InboxReportCard

        projected = (
            await session.execute(
                select(InboxReportCard).where(InboxReportCard.decision_log_id == data["decision_log_id"])
            )
        ).scalar_one()
        assert projected.title == "远程入库报告"


@pytest.mark.asyncio
async def test_execute_skill_reports_invalidate_cache(client, monkeypatch):
    from app.database import async_session_factory
    from app.execution.execution_service import execution_service
    from app.execution.models import DecisionLog, ExecutionRun
    from app.skills.models import Skill
    from sqlalchemy import select

    invalidations = []

    async def fake_invalidate():
        invalidations.append("reports")
        return 1

    async def fake_schedule_writer(*args, **kwargs):
        return None

    async def fake_run_skill(*args, **kwargs):
        return {
            "summary": "执行完成",
            "reports": [{"title": "执行报告", "summary": "execute_skill 可见"}],
        }

    async def fake_pre_check(skill_id):
        return {"passed": True, "reasons": []}

    async def fake_invalidate_tasktree(department):
        return None

    dingtalk_calls = []

    async def fake_dingtalk_enqueue(*args, **kwargs):
        dingtalk_calls.append({"args": args, "kwargs": kwargs})
        return 1

    monkeypatch.setattr("app.common.cache.invalidate_generated_data_cache", fake_invalidate)
    monkeypatch.setattr(
        "app.execution.execution_service.tasktree_dispatcher.schedule_writer",
        fake_schedule_writer,
    )
    monkeypatch.setattr("app.execution.execution_service.default_client.run_skill", fake_run_skill)
    monkeypatch.setattr(execution_service, "_pre_check_data_quality", fake_pre_check)
    monkeypatch.setattr("app.tasktree.service.invalidate_tasktree", fake_invalidate_tasktree)
    monkeypatch.setattr("app.execution.execution_service.outbox.enqueue", fake_dingtalk_enqueue)

    async with async_session_factory() as session:
        session.add(
            Skill(
                id="skill-exec-reports",
                name="执行报告 Skill",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
                target_users=["ding-direct-should-not-send"],
            )
        )
        await session.commit()

    result = await execution_service.execute_skill(
        skill_id="skill-exec-reports",
        params={"date": "2026-04-21"},
        sandbox=False,
        triggered_by="test",
    )

    assert result["output"]["reports"][0]["title"] == "执行报告"
    assert result["run_mode"] == "manual_real"
    assert result["sample_used"] is False
    assert invalidations == ["reports"]
    assert dingtalk_calls == []
    async with async_session_factory() as session:
        log = (
            await session.execute(
                select(DecisionLog).where(DecisionLog.run_id == result["run_id"])
            )
        ).scalar_one_or_none()
        if log is None:
            log = (
                await session.execute(
                    select(DecisionLog).where(DecisionLog.skill_id == "skill-exec-reports")
                )
            ).scalar_one()
        run = (
            await session.execute(
                select(ExecutionRun).where(ExecutionRun.id == result["run_id"])
            )
        ).scalar_one_or_none()
        if run is None:
            run = (
                await session.execute(
                    select(ExecutionRun).where(ExecutionRun.skill_id == "skill-exec-reports")
                )
            ).scalar_one()
    assert log.output_result["reports"][0]["summary"] == "execute_skill 可见"
    assert log.output_result["_skillforge_meta"]["run_mode"] == "manual_real"
    assert run.skill_id == "skill-exec-reports"
    assert run.run_mode == "manual_real"


@pytest.mark.asyncio
async def test_execute_skill_invalid_todos_exposes_todo_error(client, monkeypatch):
    from sqlalchemy import func, select

    from app.database import async_session_factory
    from app.execution.execution_service import execution_service
    from app.skills.models import Skill
    from app.todos.models import DecisionRequest

    async def fake_schedule_writer(*args, **kwargs):
        return None

    async def fake_run_skill(*args, **kwargs):
        return {
            "summary": "dispatch 缺少 tasks 时必须暴露错误",
            "todos": [
                {
                    "kind": "dispatch",
                    "title": "非法 dispatch 待办",
                    "reviewers": ["admin"],
                }
            ],
        }

    async def fake_pre_check(skill_id):
        return {"passed": True, "reasons": []}

    async def fake_invalidate_tasktree(department):
        return None

    monkeypatch.setattr(
        "app.execution.execution_service.tasktree_dispatcher.schedule_writer",
        fake_schedule_writer,
    )
    monkeypatch.setattr("app.execution.execution_service.default_client.run_skill", fake_run_skill)
    monkeypatch.setattr(execution_service, "_pre_check_data_quality", fake_pre_check)
    monkeypatch.setattr("app.tasktree.service.invalidate_tasktree", fake_invalidate_tasktree)

    async with async_session_factory() as session:
        session.add(
            Skill(
                id="skill-exec-invalid-todos",
                name="非法执行待办 Skill",
                department="EC",
                visibility="company",
                status="active",
                approval_level=1,
                target_users=["admin"],
            )
        )
        await session.commit()

    result = await execution_service.execute_skill(
        skill_id="skill-exec-invalid-todos",
        params={"date": "2026-04-22"},
        sandbox=False,
        triggered_by="test",
    )

    assert result["todo_created"] is False
    assert result["todo_count"] == 0
    assert result["todo_error"]["code"] == "TODO_SPEC_INVALID"
    assert "kind=dispatch 必须包含至少一条 tasks" in result["todo_error"]["detail"]["detail"]

    async with async_session_factory() as session:
        request_count = await session.scalar(
            select(func.count(DecisionRequest.id)).where(
                DecisionRequest.skill_id == "skill-exec-invalid-todos"
            )
        )
    assert request_count == 0


@pytest.mark.asyncio
async def test_submit_result_idempotency_key_does_not_duplicate(client):
    from sqlalchemy import func, select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun
    from app.skills.models import Skill
    from app.todos.models import DecisionRequest

    async with async_session_factory() as session:
        session.add(User(
            id="idem-reviewer",
            username="idem-reviewer",
            name="幂等待办处理人",
            role="operator",
            is_active=True,
        ))
        session.add(
            Skill(
                id="skill-submit-idem",
                name="幂等 Skill",
                department="EC",
                visibility="company",
                status="active",
                approval_level=1,
            )
        )
        await session.commit()

    payload = {
        "skill_id": "skill-submit-idem",
        "params": {"date": "2026-04-21"},
        "triggered_by": "skill_sdk",
        "idempotency_key": "idem-submit-1",
        "output": {
            "summary": "需要确认",
            "reports": [{"title": "幂等报告", "summary": "只入库一次"}],
            "todos": [
                {
                    "kind": "review",
                    "title": "确认幂等结果",
                    "summary": "核对报告口径",
                    "reviewers": ["idem-reviewer"],
                }
            ],
        },
    }

    first = await client.post("/api/executions/submit-result", json=payload)
    second = await client.post("/api/executions/submit-result", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    first_data = first.json()
    second_data = second.json()
    assert first_data["idempotent"] is False
    assert second_data["idempotent"] is True
    assert second_data["run_id"] == first_data["run_id"]
    assert second_data["decision_log_id"] == first_data["decision_log_id"]

    async with async_session_factory() as session:
        run_count = await session.scalar(
            select(func.count(ExecutionRun.id)).where(ExecutionRun.skill_id == "skill-submit-idem")
        )
        log_count = await session.scalar(
            select(func.count(DecisionLog.id)).where(DecisionLog.skill_id == "skill-submit-idem")
        )
        request_count = await session.scalar(
            select(func.count(DecisionRequest.id)).where(DecisionRequest.skill_id == "skill-submit-idem")
        )

    assert run_count == 1
    assert log_count == 1
    assert request_count == 1


@pytest.mark.asyncio
async def test_submit_result_persists_run_mode_and_data_proofs(client):
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun
    from app.skills.models import Skill

    async with async_session_factory() as session:
        session.add(
            Skill(
                id="skill-proof-submit",
                name="DataProof Skill",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            )
        )
        await session.commit()

    proof = {
        "url": "https://example.test/api",
        "method": "GET",
        "status": 200,
        "fetched_at": "2026-04-21T00:00:00+00:00",
        "sample_keys": ["rows"],
        "row_count": 3,
        "is_sample": True,
    }
    resp = await client.post("/api/executions/submit-result", json={
        "skill_id": "skill-proof-submit",
        "run_id": "run-proof-submit",
        "params": {"date": "2026-04-21"},
        "triggered_by": "skill_sdk",
        "run_mode": "sample_preview",
        "parent_run_id": "parent-run-1",
        "batch_id": "batch-proof-1",
        "sample_used": True,
        "data_provenance": [proof],
        "output": {"summary": "proof"},
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["run_mode"] == "sample_preview"
    assert data["data_proofs"] == [proof]
    assert data["sample_used"] is True

    async with async_session_factory() as session:
        run = (
            await session.execute(
                select(ExecutionRun).where(ExecutionRun.skill_id == "skill-proof-submit")
            )
        ).scalar_one()
        log = (
            await session.execute(
                select(DecisionLog).where(DecisionLog.skill_id == "skill-proof-submit")
            )
        ).scalar_one()

    assert run.skill_id == "skill-proof-submit"
    assert run.run_mode == "sample_preview"
    assert run.parent_run_id == "parent-run-1"
    assert run.batch_id == "batch-proof-1"
    assert run.metadata_json["data_proofs"] == [proof]
    assert log.output_result["_skillforge_meta"]["sample_used"] is True


@pytest.mark.asyncio
async def test_submit_result_reads_data_proofs_from_output_meta(client):
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun
    from app.skills.models import Skill

    async with async_session_factory() as session:
        session.add(
            Skill(
                id="skill-output-proof-submit",
                name="Output Proof Skill",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            )
        )
        await session.commit()

    proof = {
        "url": "mcp://yuyidata_get_task_info",
        "method": "GET",
        "status": 200,
        "fetched_at": "2026-05-19T10:40:29+08:00",
        "sample_keys": ["ok", "data"],
        "row_count": 1,
        "is_sample": False,
    }
    resp = await client.post("/api/executions/submit-result", json={
        "skill_id": "skill-output-proof-submit",
        "run_id": "run-output-proof-submit",
        "triggered_by": "node_scheduler",
        "run_mode": "scheduled_real",
        "output": {
            "summary": "proof in output meta",
            "_skillforge_meta": {"data_proofs": [proof]},
        },
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["data_proofs"] == [proof]
    assert data["sample_used"] is False

    async with async_session_factory() as session:
        run = (
            await session.execute(
                select(ExecutionRun).where(ExecutionRun.skill_id == "skill-output-proof-submit")
            )
        ).scalar_one()
        log = (
            await session.execute(
                select(DecisionLog).where(DecisionLog.skill_id == "skill-output-proof-submit")
            )
        ).scalar_one()

    assert run.metadata_json["data_proofs"] == [proof]
    assert log.output_result["_skillforge_meta"]["data_proofs"] == [proof]
    assert log.output_result["_skillforge_meta"]["sample_used"] is False


@pytest.mark.asyncio
async def test_submit_result_remote_pair_idempotency_and_source_metadata(client):
    from sqlalchemy import func, select

    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun
    from app.skills.models import Skill

    async with async_session_factory() as session:
        session.add(
            Skill(
                id="skill-submit-source",
                name="来源绑定 Skill",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            )
        )
        await session.commit()

    payload = {
        "skill_id": "skill-submit-source",
        "params": {"date": "2026-04-21"},
        "triggered_by": "skill_sdk",
        "instance_id": "inst-a",
        "remote_run_id": "remote-001",
        "skill_version": "v2",
        "agent_type": "openclaw",
        "signature": "sig-1",
        "idempotency_key": "different-key-first",
        "run_id": "platform-run-first",
        "output": {
            "summary": "第一次",
            "_skillforge_meta": {
                "execution_backend": "openclaw_agent",
                "requested_backend": "hybrid",
                "fallback_from": None,
                "instance_id": "inst-a",
                "remote_run_id": "remote-001",
                "agent_runtime": "openclaw",
                "duration_ms": 1234,
            },
        },
    }
    first = await client.post("/api/executions/submit-result", json=payload)
    second = await client.post(
        "/api/executions/submit-result",
        json={
            **payload,
            "idempotency_key": "different-key-second",
            "run_id": "platform-run-second",
            "output": {"summary": "第二次不应写入"},
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_data = first.json()
    second_data = second.json()
    assert first_data["idempotent"] is False
    assert second_data["idempotent"] is True
    assert second_data["run_id"] == "platform-run-first"
    assert second_data["source"] == {
        "source_instance_id": "inst-a",
        "remote_run_id": "remote-001",
        "skill_version": "v2",
        "agent_type": "openclaw",
    }
    assert second_data["runtime"]["execution_backend"] == "openclaw_agent"
    assert second_data["runtime"]["requested_backend"] == "hybrid"

    async with async_session_factory() as session:
        run_count = await session.scalar(
            select(func.count(ExecutionRun.id)).where(ExecutionRun.skill_id == "skill-submit-source")
        )
        log_count = await session.scalar(
            select(func.count(DecisionLog.id)).where(DecisionLog.skill_id == "skill-submit-source")
        )
        run = await session.get(ExecutionRun, "platform-run-first")
        log = (
            await session.execute(
                select(DecisionLog).where(DecisionLog.run_id == "platform-run-first")
            )
        ).scalar_one()

    assert run_count == 1
    assert log_count == 1
    assert run.metadata_json["source_instance_id"] == "inst-a"
    assert run.metadata_json["remote_run_id"] == "remote-001"
    assert run.metadata_json["skill_version"] == "v2"
    assert run.metadata_json["agent_type"] == "openclaw"
    assert run.metadata_json["runtime"]["execution_backend"] == "openclaw_agent"
    assert run.metadata_json["runtime"]["requested_backend"] == "hybrid"
    assert run.metadata_json["runtime"]["duration_ms"] == 1234
    assert log.output_result["_skillforge_meta"]["source_instance_id"] == "inst-a"
    assert log.output_result["_skillforge_meta"]["remote_run_id"] == "remote-001"
    assert log.output_result["_skillforge_meta"]["signature"] == "sig-1"


@pytest.mark.asyncio
async def test_mcp_runtime_alert_creates_todo_and_dingtalk_outbox(client):
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk.models import DingTalkOutbox
    from app.execution.execution_service import _emit_mcp_runtime_alerts
    from app.skills.core.models import Skill
    from app.todos.models import AITodo, DecisionRequest

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="owner-alert",
                username="owner-alert",
                name="Owner",
                role="aibp",
                department="EC",
                state="active",
                is_active=True,
            ),
            User(
                id="admin-alert",
                username="admin-alert",
                name="Admin",
                role="system_admin",
                department="AI",
                dingtalk_user_id="ding-admin-alert",
                state="active",
                is_active=True,
            ),
            Skill(id="skill-alert", name="Alert Skill", department="EC", status="active", owner="owner-alert"),
        ])
        await session.commit()

    await _emit_mcp_runtime_alerts(
        run_id="run-alert",
        skill_id="skill-alert",
        output={
            "errors": [{"code": "MCP_RUNTIME_DRIFT"}],
            "warning": "MCP_DIRECT_COOKIE_BLOCKED",
        },
        decision_log_id=7,
    )

    async with async_session_factory() as session:
        requests = (
            await session.execute(select(DecisionRequest).order_by(DecisionRequest.source_id.asc()))
        ).scalars().all()
        todos = (await session.execute(select(AITodo).order_by(AITodo.id.asc()))).scalars().all()
        outbox_rows = (
            await session.execute(select(DingTalkOutbox).order_by(DingTalkOutbox.related_id.asc()))
        ).scalars().all()

    assert {row.payload["code"] for row in requests} == {"MCP_RUNTIME_DRIFT", "MCP_DIRECT_COOKIE_BLOCKED"}
    assert {todo.assignee for todo in todos} == {"owner-alert"}
    assert {row.related_type for row in outbox_rows} == {"mcp_runtime_alert"}
    assert {row.recipient_user_id for row in outbox_rows} == {"ding-admin-alert"}


@pytest.mark.asyncio
async def test_output_notifications_enqueue_dingtalk_outbox(client):
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk.models import DingTalkOutbox
    from app.execution.execution_service import _enqueue_output_notifications

    async with async_session_factory() as session:
        session.add(
            User(
                id="u-notify-output",
                username="notify-output",
                name="Notify Output",
                role="operator",
                department="示例品牌内容电商运营部",
                dingtalk_user_id="ding-notify-output",
                state="active",
                is_active=True,
            )
        )
        await session.commit()

    output = {
        "notifications": [
            {
                "channel": "dingtalk_work_notice",
                "title": "示例品牌低消耗视频日诊断",
                "markdown": "### 今日改进\n- 视频 A：补强首屏钩子",
                "recipients": {"user_ids": ["u-notify-output"]},
                "idempotency_key": "samplebrand-low-consumption:2026-06-09",
            }
        ]
    }
    result = await _enqueue_output_notifications(
        run_id="notify-output-run",
        skill_id="samplebrand-video-low-consumption-operator-v1",
        decision_log_id=123,
        output=output,
    )
    duplicate = await _enqueue_output_notifications(
        run_id="notify-output-run",
        skill_id="samplebrand-video-low-consumption-operator-v1",
        decision_log_id=123,
        output=output,
    )

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(DingTalkOutbox).where(DingTalkOutbox.related_type == "skill_output_notification")
            )
        ).scalars().all()

    assert result["queued"] == 1
    assert duplicate["queued"] == 0
    assert len(rows) == 1
    assert rows[0].recipient_user_id == "ding-notify-output"
    assert "补强首屏钩子" in rows[0].payload["markdown"]


@pytest.mark.asyncio
async def test_operator_success_notification_enqueues_examplerecipient_outbox(client):
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk.models import DingTalkOutbox
    from app.execution.execution_service import (
        LINK_DECLINE_OPERATOR_SUCCESS_RELATED_TYPE,
        _notify_link_decline_operator_success,
    )

    async with async_session_factory() as session:
        session.add(
            User(
                id="u-examplerecipient",
                username="examplerecipient",
                name="示例成员甲",
                role="operator",
                department="运营部",
                dingtalk_user_id="ding-examplerecipient",
                state="active",
                is_active=True,
            )
        )
        await session.commit()

    result = await _notify_link_decline_operator_success(
        run_id="operator-success-run",
        decision_log_id=230,
        output={
            "reports": [{"title": "2026-05-28 店铺链接下滑诊断日报", "summary": "含待办"}],
            "todos": [{"title": "todo"}],
            "actions": [{"action_id": "a1"}, {"action_id": "a2"}],
        },
        todo_count=34,
    )
    duplicate = await _notify_link_decline_operator_success(
        run_id="operator-success-run",
        decision_log_id=230,
        output={"reports": [], "todos": [], "actions": []},
        todo_count=0,
    )

    async with async_session_factory() as session:
        outbox_rows = (
            await session.execute(
                select(DingTalkOutbox).where(
                    DingTalkOutbox.related_type == LINK_DECLINE_OPERATOR_SUCCESS_RELATED_TYPE
                )
            )
        ).scalars().all()

    assert result["status"] == "enqueued"
    assert duplicate["reason"] == "duplicate"
    assert len(outbox_rows) == 1
    assert outbox_rows[0].recipient_user_id == "ding-examplerecipient"
    assert outbox_rows[0].related_id == "operator-success-run"
    assert "形成待办：34 条" in outbox_rows[0].payload["markdown"]
    assert "优先级分布：P0 0、P1 0、P2 0、P3 0" in outbox_rows[0].payload["markdown"]
    assert outbox_rows[0].payload["buttons"] == [
        {"title": "查看执行结果", "url": "/execution/operator-success-run"}
    ]


@pytest.mark.asyncio
async def test_operator_success_notification_uses_fixed_dingtalk_id_when_user_not_visible(client):
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.dingtalk.models import DingTalkOutbox
    from app.execution.execution_service import (
        LINK_DECLINE_OPERATOR_SUCCESS_DINGTALK_USER_ID,
        _notify_link_decline_operator_success,
    )

    result = await _notify_link_decline_operator_success(
        run_id="operator-success-fixed-recipient-run",
        decision_log_id=231,
        output={
            "reports": [{"title": "Operator 报告", "summary": "ok"}],
            "todos": [],
            "actions": [],
        },
        todo_count=0,
    )

    async with async_session_factory() as session:
        outbox_row = (
            await session.execute(
                select(DingTalkOutbox).where(
                    DingTalkOutbox.related_id == "operator-success-fixed-recipient-run"
                )
            )
        ).scalar_one()

    assert result["status"] == "enqueued"
    assert result["recipient_user_id"] is None
    assert result["recipient_dingtalk_user_id"] == LINK_DECLINE_OPERATOR_SUCCESS_DINGTALK_USER_ID
    assert outbox_row.recipient_user_id == LINK_DECLINE_OPERATOR_SUCCESS_DINGTALK_USER_ID


@pytest.mark.asyncio
async def test_operator_success_notification_summarizes_p0_p3_from_created_todos(client):
    from datetime import datetime
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk.models import DingTalkOutbox
    from app.execution.execution_service import _notify_link_decline_operator_success
    from app.todos.models import DecisionRequest

    async with async_session_factory() as session:
        session.add(
            User(
                id="u-dongmaintainer",
                username="dongmaintainer",
                name="示例成员4",
                role="admin",
                department="信息技术部",
                dingtalk_user_id="ding-dongmaintainer",
                state="active",
                is_active=True,
            )
        )
        session.add_all([
            DecisionRequest(
                id="dr-op-success-p0",
                source_type="skill_execution_dispatch",
                source_id="operator-success-priority-run:p0",
                skill_id="tmall-link-decline-operator-v1",
                run_id="operator-success-priority-run",
                kind="dispatch",
                title="P0｜商品诊断卡｜核心商品",
                summary="",
                payload={"priority": "P0"},
                sla_at=datetime(2026, 5, 28, 18, 0, 0),
            ),
            DecisionRequest(
                id="dr-op-success-p1",
                source_type="skill_execution_dispatch",
                source_id="operator-success-priority-run:p1",
                skill_id="tmall-link-decline-operator-v1",
                run_id="operator-success-priority-run",
                kind="dispatch",
                title="商品诊断卡",
                summary="",
                payload={"input": {"priority": "P1"}},
                sla_at=datetime(2026, 5, 28, 18, 0, 0),
            ),
            DecisionRequest(
                id="dr-op-success-p2",
                source_type="skill_execution_dispatch",
                source_id="operator-success-priority-run:p2",
                skill_id="tmall-link-decline-operator-v1",
                run_id="operator-success-priority-run",
                kind="dispatch",
                title="P2 商品诊断卡",
                summary="",
                payload={},
                sla_at=datetime(2026, 5, 28, 18, 0, 0),
            ),
            DecisionRequest(
                id="dr-op-success-p3",
                source_type="skill_execution_dispatch",
                source_id="operator-success-priority-run:p3",
                skill_id="tmall-link-decline-operator-v1",
                run_id="operator-success-priority-run",
                kind="dispatch",
                title="商品诊断卡",
                summary="",
                payload={"priority": "P3"},
                sla_at=datetime(2026, 5, 28, 18, 0, 0),
            ),
        ])
        await session.commit()

    result = await _notify_link_decline_operator_success(
        run_id="operator-success-priority-run",
        decision_log_id=232,
        output={
            "reports": [{"title": "Operator 报告", "summary": "ok"}],
            "todos": [],
            "actions": [],
        },
        todo_count=0,
        recipient_query="示例成员4",
        fallback_dingtalk_user_id="example-user-004",
        related_id="operator-success-priority-test-that-is-longer-than-fifty-characters",
    )

    async with async_session_factory() as session:
        outbox_row = (
            await session.execute(
                select(DingTalkOutbox).where(
                    DingTalkOutbox.related_type == "link_decline_operator_success",
                    DingTalkOutbox.related_id != "operator-success-priority-run",
                )
            )
        ).scalar_one()

    assert result["status"] == "enqueued"
    assert result["recipient_dingtalk_user_id"] == "ding-dongmaintainer"
    assert result["todo_count"] == 4
    assert result["priority_counts"] == {"P0": 1, "P1": 1, "P2": 1, "P3": 1}
    assert "形成待办：4 条" in outbox_row.payload["markdown"]
    assert "优先级分布：P0 1、P1 1、P2 1、P3 1" in outbox_row.payload["markdown"]
    assert outbox_row.payload["buttons"] == [
        {"title": "查看执行结果", "url": "/execution/operator-success-priority-run"}
    ]


@pytest.mark.asyncio
async def test_submit_result_operator_success_enqueues_notification(client):
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk.models import DingTalkOutbox
    from app.execution.execution_service import execution_service
    from app.skills.core.models import Skill

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="u-submit-examplerecipient",
                username="submit-examplerecipient",
                name="示例成员甲",
                role="operator",
                department="运营部",
                dingtalk_user_id="ding-submit-examplerecipient",
                state="active",
                is_active=True,
            ),
            Skill(
                id=execution_service.OPERATOR_SKILL_ID,
                name="Operator",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            ),
        ])
        await session.commit()

    resp = await client.post("/api/executions/submit-result", json={
        "skill_id": execution_service.OPERATOR_SKILL_ID,
        "run_id": "operator-submit-success-run",
        "triggered_by": "node_scheduler",
        "run_mode": "scheduled_real",
        "output": {
            "summary": "operator done",
            "reports": [{"title": "Operator 报告", "summary": "ok"}],
            "todos": [
                {
                    "kind": "review",
                    "title": "确认 Operator 结果",
                    "summary": "核对报告",
                    "reviewers": ["u-submit-examplerecipient"],
                }
            ],
            "actions": [{"action_id": "a1"}],
        },
    })

    assert resp.status_code == 200
    async with async_session_factory() as session:
        outbox_row = (
            await session.execute(
                select(DingTalkOutbox).where(DingTalkOutbox.related_id == "operator-submit-success-run")
            )
        ).scalar_one()

    assert outbox_row.related_type == "link_decline_operator_success"
    assert outbox_row.recipient_user_id == "ding-submit-examplerecipient"
    assert "动作数：1" in outbox_row.payload["markdown"]


@pytest.mark.asyncio
async def test_submit_result_completed_collector_enqueues_link_decline_operator(client, monkeypatch, tmp_path):
    from sqlalchemy import select

    from app.config import settings
    from app.database import async_session_factory
    from app.execution.artifact_service import artifact_abs_path
    from app.execution.models import ExecutionArtifact, ExecutionRun
    from app.execution.execution_service import execution_service
    from app.execution.queue_models import ExecutionQueueTask
    from app.skills.core.models import Skill

    monkeypatch.setattr(settings, "EXECUTION_ARTIFACT_ROOT", str(tmp_path / "execution-artifacts"))
    monkeypatch.setattr(
        execution_service,
        "process_link_decline_operator_queue_once",
        AsyncMock(return_value=None),
    )
    async with async_session_factory() as session:
        session.add_all([
            Skill(
                id=execution_service.COLLECTOR_SKILL_ID,
                name="Collector",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            ),
            Skill(
                id=execution_service.OPERATOR_SKILL_ID,
                name="Operator",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            ),
        ])
        await session.commit()

    resp = await client.post("/api/executions/submit-result", json={
        "skill_id": execution_service.COLLECTOR_SKILL_ID,
        "run_id": "collector-submit-run",
        "triggered_by": "node_scheduler",
        "run_mode": "scheduled_real",
        "output": {
            "collection_schema": "tmall_link_decline_collection_v1",
            "summary": "collector done",
        },
    })

    assert resp.status_code == 200
    data = resp.json()
    async with async_session_factory() as session:
        task = (
            await session.execute(
                select(ExecutionQueueTask).where(
                    ExecutionQueueTask.task_type == execution_service.OPERATOR_TRIGGER_TASK_TYPE
                )
            )
        ).scalar_one()
        artifacts = (
            await session.execute(
                select(ExecutionArtifact)
                .where(ExecutionArtifact.run_id == "collector-submit-run")
                .order_by(ExecutionArtifact.kind.asc())
            )
        ).scalars().all()
        run = await session.get(ExecutionRun, "collector-submit-run")

    assert task.status == "pending"
    assert task.skill_id == execution_service.OPERATOR_SKILL_ID
    assert task.created_by == "execution.submit_result"
    assert task.payload["collector_run_id"] == "collector-submit-run"
    assert task.payload["collector_decision_log_id"] == data["decision_log_id"]
    assert [artifact.kind for artifact in artifacts] == ["raw-input", "raw-output"]
    raw_output = next(artifact for artifact in artifacts if artifact.kind == "raw-output")
    assert raw_output.schema_name == "tmall_link_decline_collection_v1"
    assert raw_output.uncompressed_size_bytes > 0
    assert raw_output.size_bytes > 0
    assert raw_output.summary_json["collection_schema"] == "tmall_link_decline_collection_v1"
    raw_output_path = artifact_abs_path(raw_output.storage_path)
    assert raw_output_path.exists()
    restored = json.loads(gzip.decompress(raw_output_path.read_bytes()).decode("utf-8"))
    assert restored["collection_schema"] == "tmall_link_decline_collection_v1"
    assert {item["kind"] for item in (run.metadata_json or {}).get("artifacts", [])} == {"raw-input", "raw-output"}


@pytest.mark.asyncio
async def test_submit_result_idempotent_collector_repairs_missing_operator_trigger(client, monkeypatch, tmp_path):
    from sqlalchemy import func, select

    from app.config import settings
    from app.database import async_session_factory
    from app.execution.execution_service import execution_service
    from app.execution.models import ExecutionArtifact
    from app.execution.queue_models import ExecutionQueueTask
    from app.skills.core.models import Skill

    monkeypatch.setattr(settings, "EXECUTION_ARTIFACT_ROOT", str(tmp_path / "execution-artifacts"))
    monkeypatch.setattr(
        execution_service,
        "process_link_decline_operator_queue_once",
        AsyncMock(return_value=None),
    )
    async with async_session_factory() as session:
        session.add_all([
            Skill(
                id=execution_service.COLLECTOR_SKILL_ID,
                name="Collector",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            ),
            Skill(
                id=execution_service.OPERATOR_SKILL_ID,
                name="Operator",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            ),
        ])
        await session.commit()

    payload = {
        "skill_id": execution_service.COLLECTOR_SKILL_ID,
        "run_id": "collector-submit-idem-run",
        "triggered_by": "node_scheduler",
        "run_mode": "scheduled_real",
        "idempotency_key": "collector-idem-1",
        "output": {
            "collection_schema": "tmall_link_decline_collection_v1",
            "summary": "collector done",
        },
    }
    first = await client.post("/api/executions/submit-result", json=payload)
    assert first.status_code == 200
    async with async_session_factory() as session:
        first_artifact_count = int(
            await session.scalar(
                select(func.count(ExecutionArtifact.id)).where(
                    ExecutionArtifact.run_id == "collector-submit-idem-run"
                )
            )
            or 0
        )
    assert first_artifact_count == 2

    async with async_session_factory() as session:
        task = (
            await session.execute(
                select(ExecutionQueueTask).where(
                    ExecutionQueueTask.task_type == execution_service.OPERATOR_TRIGGER_TASK_TYPE
                )
            )
        ).scalar_one()
        await session.delete(task)
        await session.commit()

    second = await client.post("/api/executions/submit-result", json=payload)
    assert second.status_code == 200
    assert second.json()["idempotent"] is True

    async with async_session_factory() as session:
        repaired_task = (
            await session.execute(
                select(ExecutionQueueTask).where(
                    ExecutionQueueTask.task_type == execution_service.OPERATOR_TRIGGER_TASK_TYPE
                )
            )
        ).scalar_one()
        second_artifact_count = int(
            await session.scalar(
                select(func.count(ExecutionArtifact.id)).where(
                    ExecutionArtifact.run_id == "collector-submit-idem-run"
                )
            )
            or 0
        )

    assert repaired_task.created_by == "execution.submit_result.idempotent"
    assert repaired_task.payload["collector_run_id"] == "collector-submit-idem-run"
    assert repaired_task.payload["collector_decision_log_id"] == first.json()["decision_log_id"]
    assert second_artifact_count == first_artifact_count
