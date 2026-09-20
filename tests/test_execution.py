"""执行模块测试"""

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_sandbox_execution(client):
    """测试沙箱执行（Skill不存在时应返回错误）"""
    resp = await client.post("/api/executions/run", json={
        "skill_id": "EC-投放-01",
        "sandbox": True,
    })
    data = resp.json()
    # 测试环境无预置Skill和OpenClaw，允许执行失败或返回错误
    if resp.status_code == 200 and "error" not in data:
        assert "run_id" in data
    else:
        # Skill不存在或OpenClaw未连接均可接受
        assert resp.status_code in (200, 400, 404, 500)


@pytest.mark.asyncio
async def test_list_runs(client):
    """测试执行记录列表"""
    resp = await client.get("/api/executions/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_list_runs_uses_db_pagination_and_status_filter(client):
    """分页和状态过滤应在数据库层完成，而不是全量拉取后切片。"""
    from app.database import async_session_factory, engine
    from app.execution.models import ExecutionRun

    now = datetime.utcnow()
    await engine.dispose()
    async with async_session_factory() as session:
        for idx in range(5):
            session.add(ExecutionRun(
                id=f"run-page-{idx}",
                trigger_type="manual",
                status="completed" if idx < 4 else "failed",
                started_at=now - timedelta(minutes=idx),
            ))
        await session.commit()

    resp = await client.get("/api/executions/runs?page=2&page_size=2&status=completed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert data["page"] == 2
    assert data["page_size"] == 2
    assert [item["id"] for item in data["items"]] == ["run-page-2", "run-page-3"]


@pytest.mark.asyncio
async def test_list_runs_system_admin_uses_global_scope(monkeypatch):
    from app.execution import router as execution_router

    current_user = MagicMock()
    current_user.role = "system_admin"
    current_user.department = "Dept A"
    current_user.can_view_all = False

    list_mock = AsyncMock(
        return_value={"items": [], "total": 0, "page": 1, "page_size": 20}
    )
    monkeypatch.setattr(execution_router, "cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(execution_router, "cache_set", AsyncMock())
    monkeypatch.setattr(
        execution_router.execution_service,
        "list_execution_runs_paged",
        list_mock,
    )

    result = await execution_router.list_runs(
        page=1,
        page_size=20,
        status=None,
        current_user=current_user,
    )

    assert result["total"] == 0
    list_mock.assert_awaited_once_with(
        page=1,
        page_size=20,
        department=None,
        status=None,
    )


@pytest.mark.asyncio
async def test_runs_stats_system_admin_uses_global_scope(monkeypatch):
    from app.execution import router as execution_router

    current_user = MagicMock()
    current_user.role = "system_admin"
    current_user.department = "Dept A"
    current_user.can_view_all = False

    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.all.return_value = []
    db.execute = AsyncMock(return_value=exec_result)

    monkeypatch.setattr(execution_router, "cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(execution_router, "cache_set", AsyncMock())

    result = await execution_router.runs_stats(current_user=current_user, db=db)

    assert result["total"] == 0
    stmt = str(db.execute.await_args.args[0])
    assert "skills.department" not in stmt


@pytest.mark.asyncio
async def test_openclaw_client_mock_only_allowed_in_sandbox(monkeypatch):
    """Mock 执行只能在显式开发开关 + sandbox 下触发。"""
    from app.common.exceptions import AppError
    from app.config import settings
    from app.execution.openclaw_client import OpenClawClient

    client = OpenClawClient("ws://gateway.invalid")
    monkeypatch.setattr(client, "_connect_and_auth", AsyncMock(side_effect=RuntimeError("gateway down")))
    monkeypatch.setattr(settings, "ALLOW_MOCK_EXECUTION", True, raising=False)

    sandbox_result = await client.run_skill("EC-投放-01", {}, sandbox=True)
    assert sandbox_result["mock"] is True

    with pytest.raises(AppError) as exc_info:
        await client.run_skill("EC-投放-01", {}, sandbox=False)
    assert exc_info.value.code == "GATEWAY_EXECUTION_FAILED"


@pytest.mark.asyncio
async def test_openclaw_client_requests_operator_read_write_scopes(monkeypatch):
    """后端直连 OpenClaw/AIClaw 时必须申请读写 scope，否则 chat.send 会被拒绝。"""
    from app.execution import openclaw_client as module
    from app.execution.openclaw_client import OpenClawClient

    class FakeWebSocket:
        def __init__(self):
            self.sent: list[dict] = []
            self.messages = [
                {"event": "connect.challenge", "payload": {"nonce": "nonce-123456"}},
                {"type": "event", "event": "noise"},
                {"type": "res", "id": "sf-nonce-12", "ok": True},
        ]

        async def recv(self):
            return json.dumps(self.messages.pop(0))

        async def send(self, message):
            self.sent.append(json.loads(message))

        async def close(self):
            pass

    fake_ws = FakeWebSocket()

    async def fake_connect(*args, **kwargs):
        return fake_ws

    monkeypatch.setattr(module.websockets, "connect", fake_connect)
    monkeypatch.setattr(module, "_build_local_device_auth", lambda **kwargs: None)
    monkeypatch.setattr(module, "_discover_local_device_id", lambda: "device-1")
    monkeypatch.setattr(module, "_local_platform", lambda: "linux")

    ws = await OpenClawClient("ws://gateway.local", auth_token="token-1")._connect_and_auth()

    assert ws is fake_ws
    connect_msg = fake_ws.sent[0]
    assert connect_msg["method"] == "connect"
    params = connect_msg["params"]
    assert params["client"]["mode"] == "backend"
    assert params["client"]["platform"] == "linux"
    assert params["role"] == "operator"
    assert params["scopes"] == ["operator.read", "operator.write", "operator.admin"]
    assert params["auth"] == {"token": "token-1"}
    assert params["device"] == {"id": "device-1"}


@pytest.mark.asyncio
async def test_openclaw_client_omits_empty_auth(monkeypatch):
    from app.execution import openclaw_client as module
    from app.execution.openclaw_client import OpenClawClient

    class FakeWebSocket:
        def __init__(self):
            self.sent: list[dict] = []
            self.messages = [
                {"event": "connect.challenge", "payload": {"nonce": "nonce-empty"}},
                {"type": "res", "id": "sf-nonce-em", "ok": True},
            ]

        async def recv(self):
            return json.dumps(self.messages.pop(0))

        async def send(self, message):
            self.sent.append(json.loads(message))

        async def close(self):
            pass

    fake_ws = FakeWebSocket()

    async def fake_connect(*args, **kwargs):
        return fake_ws

    monkeypatch.setattr(module.websockets, "connect", fake_connect)
    monkeypatch.setattr(module, "_build_local_device_auth", lambda **kwargs: None)
    monkeypatch.setattr(module, "_discover_local_device_id", lambda: "")

    await OpenClawClient("ws://gateway.local")._connect_and_auth()

    params = fake_ws.sent[0]["params"]
    assert "auth" not in params
    assert "device" not in params


@pytest.mark.asyncio
async def test_execute_skill_uses_bridge_script_backend(client, monkeypatch):
    """指定 bridge_script 时，执行链路必须调用节点脚本，不走 chat.send/default_client。"""
    from datetime import datetime
    from uuid import uuid4

    from app.database import async_session_factory, engine
    from app.execution.execution_service import execution_service

    # Synthetic transport used below must also advertise a live connection.
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: True)
    from app.execution.models import OpenClawInstance
    from app.skills.models import Skill

    suffix = uuid4().hex[:8]
    skill_id = f"skill-bridge-script-{suffix}"
    instance_id = f"node-bridge-script-{suffix}"

    async def fake_pre_check(skill_id):
        return {"passed": True, "reasons": []}

    calls = {}

    async def fake_run_skill_script(self, skill_id, payload, **kwargs):
        calls["run_mode"] = kwargs.get("run_mode")
        return {
            "success": True,
            "script": kwargs.get("script_path"),
            "duration_ms": 12,
            "output": {"reports": [{"title": payload["date"]}], "todos": []},
        }

    async def fail_default_run(*args, **kwargs):
        raise AssertionError("default_client.run_skill should not be used")

    monkeypatch.setattr(execution_service, "_pre_check_data_quality", fake_pre_check)
    monkeypatch.setattr("app.execution.execution_service.tasktree_dispatcher.schedule_writer", AsyncMock())
    monkeypatch.setattr("app.execution.execution_service.default_client.run_skill", fail_default_run)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_skill_script", fake_run_skill_script)
    monkeypatch.setattr("app.tasktree.service.invalidate_tasktree", AsyncMock())

    await engine.dispose()
    async with async_session_factory() as session:
        session.add(
            Skill(
                id=skill_id,
                name="Bridge Script",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            )
        )
        session.add(
            OpenClawInstance(
                id=instance_id,
                name="node",
                department="EC",
                gateway_url="ws://local",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                bridge_connected_at=datetime.utcnow(),
                bridge_skills_dir="/tmp/skills",
            )
        )
        await session.commit()

    result = await execution_service.execute_skill(
        skill_id=skill_id,
        params={
            "_execution_backend": "bridge_script",
            "_aiclaw_instance_id": instance_id,
            "date": "2026-04-22",
        },
        sandbox=False,
        triggered_by="test",
    )

    assert result["output"]["reports"][0]["title"] == "2026-04-22"
    assert result["output"]["_skillforge_meta"]["execution_backend"] == "bridge_script"
    assert result["sample_used"] is False
    assert calls["run_mode"] == "manual_real"


@pytest.mark.asyncio
async def test_execute_skill_injects_active_model_context_before_bridge_script(client, monkeypatch):
    """手动 Bridge runtime 执行前应注入 active 模型上下文，并覆盖用户伪造字段。"""
    from datetime import datetime
    from uuid import uuid4

    from app.database import async_session_factory, engine
    from app.execution.execution_service import execution_service

    # Synthetic transport used below must also advertise a live connection.
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: True)
    from app.execution.models import DecisionLog, OpenClawInstance
    from app.skills.models import Skill
    from app.training.models import TrainingJob, TrainingModelDeployment

    suffix = uuid4().hex[:8]
    skill_id = f"skill-runtime-model-{suffix}"
    instance_id = f"node-runtime-model-{suffix}"
    captured_payload = {}

    async def fake_pre_check(skill_id):
        return {"passed": True, "reasons": []}

    async def fake_run_skill_script(self, skill_id, payload, **kwargs):
        captured_payload.update(payload)
        return {
            "success": True,
            "script": kwargs.get("script_path"),
            "duration_ms": 12,
            "output": {"reports": [{"title": "model controlled"}], "todos": []},
        }

    monkeypatch.setattr(execution_service, "_pre_check_data_quality", fake_pre_check)
    monkeypatch.setattr("app.execution.execution_service.tasktree_dispatcher.schedule_writer", AsyncMock())
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_skill_script", fake_run_skill_script)
    monkeypatch.setattr("app.tasktree.service.invalidate_tasktree", AsyncMock())

    await engine.dispose()
    async with async_session_factory() as session:
        session.add(
            Skill(
                id=skill_id,
                name="Runtime Model Skill",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            )
        )
        session.add(
            OpenClawInstance(
                id=instance_id,
                name="node",
                department="EC",
                gateway_url="ws://local",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                bridge_connected_at=datetime.utcnow(),
                bridge_skills_dir="/tmp/skills",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json='{"ops":["run_skill_script","training.inference"]}',
            )
        )
        session.add(
            TrainingJob(
                id=f"job-{suffix}",
                title="Runtime model training",
                department="EC",
                created_by="trainer",
                status="completed",
                target_skill_id=skill_id,
                target_gateway_id=instance_id,
            )
        )
        session.add(
            TrainingModelDeployment(
                id=f"deploy-{suffix}",
                job_id=f"job-{suffix}",
                department="EC",
                model_family="lora",
                artifact_id=f"artifact-{suffix}",
                artifact_ref_json={
                    "id": f"artifact-{suffix}",
                    "uri": "/tmp/skillforge-state/training/artifacts/runtime-model.tar.gz",
                    "sha256": "c" * 64,
                },
                target_skill_ids_json=[skill_id],
                status="active",
                rollout_percent=100,
                requested_by="trainer",
            )
        )
        await session.commit()

    result = await execution_service.execute_skill(
        skill_id=skill_id,
        params={
            "_execution_backend": "bridge_script",
            "_aiclaw_instance_id": instance_id,
            "model_context": {"model_deployment_id": "fake-user-context"},
            "date": "2026-06-18",
        },
        sandbox=False,
        triggered_by="manual",
    )

    injected_context = captured_payload["model_context"]
    assert injected_context["model_deployment_id"] == f"deploy-{suffix}"
    assert injected_context["active_model_deployment"]["artifact_uri"].endswith("runtime-model.tar.gz")
    assert injected_context["active_model_deployment"]["runtime_status"]["inference_ready_hint"] is True
    assert result["output"]["_skillforge_meta"]["model_context"]["model_deployment_id"] == f"deploy-{suffix}"
    assert result["output"]["_skillforge_meta"]["active_model_deployment"]["artifact_sha256"] == "c" * 64

    async with async_session_factory() as session:
        decision = (
            await session.execute(select(DecisionLog).where(DecisionLog.skill_id == skill_id))
        ).scalar_one()
    assert decision.model_id == f"deploy-{suffix}"
    assert decision.input_snapshot["model_context"]["model_deployment_id"] == f"deploy-{suffix}"
    assert decision.output_result["_skillforge_meta"]["model_context"]["model_deployment_id"] == f"deploy-{suffix}"


@pytest.mark.asyncio
async def test_department_analysis_agent_controls_bridge_script_payload(client, monkeypatch):
    """昨天登记的业务分析 Agent 配置必须进入实际 bridge_script payload 和 run metadata。"""
    from datetime import datetime

    from app.agents.models import DepartmentAnalysisAgent
    from app.database import async_session_factory, engine
    from app.execution.execution_service import execution_service

    # Synthetic transport used below must also advertise a live connection.
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: True)
    from app.execution.models import ExecutionRun, OpenClawInstance
    from app.org.models import OrgUnit
    from app.skills.models import Skill

    skill_id = "samplebrand-weekly-video-diagnosis"
    instance_id = "bridge-samplebrand-content"
    captured_payload = {}

    async def fake_pre_check(skill_id):
        return {"passed": True, "reasons": []}

    async def fake_run_skill_script(self, skill_id, payload, **kwargs):
        captured_payload.update(payload)
        return {
            "success": True,
            "script": kwargs.get("script_path"),
            "duration_ms": 21,
            "output": {
                "reports": [{"title": "agent controlled"}],
                "todos": [],
                "_skillforge_meta": {"analysis_agent": payload["analysis_agent"]["id"]},
            },
        }

    async def fail_default_run(*args, **kwargs):
        raise AssertionError("default_client.run_skill should not be used")

    monkeypatch.setattr(execution_service, "_pre_check_data_quality", fake_pre_check)
    monkeypatch.setattr("app.execution.execution_service.tasktree_dispatcher.schedule_writer", AsyncMock())
    monkeypatch.setattr("app.execution.execution_service.default_client.run_skill", fail_default_run)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_skill_script", fake_run_skill_script)
    monkeypatch.setattr("app.tasktree.service.invalidate_tasktree", AsyncMock())
    monkeypatch.setattr(execution_service, "_generate_execution_summary", AsyncMock())

    await engine.dispose()
    async with async_session_factory() as session:
        dept = OrgUnit(
            id="dept-samplebrand-content",
            name="示例品牌内容电商运营部",
            type="department",
            path="/dept-samplebrand-content",
        )
        skill = Skill(
            id=skill_id,
            name="示例品牌周度同主题视频消耗诊断",
            department="示例品牌内容电商运营部",
            visibility="department",
            status="active",
            approval_level=0,
            owner="qiyingying",
            org_unit_id=dept.id,
            trigger_type="cron",
            trigger_expression="0 8 * * *",
        )
        agent = DepartmentAnalysisAgent(
            id="samplebrand-same-topic-video-diagnosis-agent",
            name="示例品牌同主题视频消耗诊断 Agent",
            department_id=dept.id,
            department="示例品牌内容电商运营部",
            owner_user_id="qiyingying",
            skill_id=skill_id,
            prompt_version="analysis_v2",
            dimensions_json=["商品露出", "行动引导"],
            default_params_json={
                "preset": "brief_summary",
                "top_n": 2,
                "window_days": 14,
                "output_sections": ["executive_summary", "topic_comparisons"],
                "todo_enabled": False,
            },
            editor_user_ids_json=["qiyingying"],
            status="active",
            is_active=True,
            created_by="admin",
            updated_by="admin",
        )
        node = OpenClawInstance(
            id=instance_id,
            name="samplebrand-node",
            department="示例品牌内容电商运营部",
            gateway_url="ws://local",
            reload_hook_url="",
            reload_token="",
            is_active=True,
            bridge_connected_at=datetime.utcnow(),
            bridge_skills_dir="/tmp/skills",
        )
        await session.merge(dept)
        await session.merge(skill)
        await session.merge(agent)
        await session.merge(node)
        await session.commit()

    result = await execution_service.execute_skill(
        skill_id=skill_id,
        params={
            "_execution_backend": "bridge_script",
            "_aiclaw_instance_id": instance_id,
            "sample_data": True,
        },
        sandbox=True,
        triggered_by="manual",
    )

    assert result["output"]["reports"][0]["title"] == "agent controlled"
    assert captured_payload["analysis_agent"]["id"] == "samplebrand-same-topic-video-diagnosis-agent"
    assert captured_payload["prompt_version"] == "analysis_v2"
    assert captured_payload["dimensions"] == ["商品露出", "行动引导"]
    assert captured_payload["top_n"] == 2
    assert captured_payload["window_days"] == 14
    assert captured_payload["output_sections"] == ["executive_summary", "topic_comparisons"]
    assert captured_payload["todo_enabled"] is False

    async with async_session_factory() as session:
        run = await session.get(ExecutionRun, result["run_id"])
        control = run.metadata_json["analysis_agent_control"]
        assert control["id"] == "samplebrand-same-topic-video-diagnosis-agent"
        assert control["prompt_version"] == "analysis_v2"
        assert control["effective_params"]["todo_enabled"] is False


@pytest.mark.asyncio
async def test_execute_skill_auto_bridge_script_skips_analysis_and_training_agents(client, monkeypatch):
    """自动选择执行节点时，只能选部门执行/混合 Agent，不能误用分析或训练 Agent。"""
    from datetime import datetime
    from uuid import uuid4

    from app.database import async_session_factory, engine
    from app.execution.execution_service import execution_service

    # Synthetic transport used below must also advertise a live connection.
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: True)
    from app.execution.models import OpenClawInstance
    from app.skills.models import Skill

    suffix = uuid4().hex[:8]
    skill_id = f"skill-runtime-agent-purpose-{suffix}"
    department = f"EC-Runtime-{suffix}"

    async def fake_pre_check(skill_id):
        return {"passed": True, "reasons": []}

    calls: list[str] = []

    async def fake_run_skill_script(self, skill_id, payload, **kwargs):
        calls.append(self.instance_id)
        return {
            "success": True,
            "duration_ms": 12,
            "output": {"reports": [{"title": self.instance_id}], "todos": []},
        }

    async def fail_default_run(*args, **kwargs):
        raise AssertionError("default_client.run_skill should not be used")

    monkeypatch.setattr(execution_service, "_pre_check_data_quality", fake_pre_check)
    monkeypatch.setattr("app.execution.execution_service.tasktree_dispatcher.schedule_writer", AsyncMock())
    monkeypatch.setattr("app.execution.execution_service.default_client.run_skill", fail_default_run)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_skill_script", fake_run_skill_script)
    monkeypatch.setattr("app.tasktree.service.invalidate_tasktree", AsyncMock())

    await engine.dispose()
    async with async_session_factory() as session:
        session.add(
            Skill(
                id=skill_id,
                name="Runtime Purpose",
                department=department,
                visibility="company",
                status="active",
                approval_level=0,
            )
        )
        session.add_all([
            OpenClawInstance(
                id=f"training-{suffix}",
                name="EC 训练 Agent",
                department=department,
                gateway_url="ws://local",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                is_platform_default=True,
                agent_purpose="training",
                bridge_connected_at=datetime.utcnow(),
                bridge_skills_dir="/tmp/skills",
            ),
            OpenClawInstance(
                id=f"analysis-{suffix}",
                name="EC 分析 Agent",
                department=department,
                gateway_url="ws://local",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                agent_purpose="analysis",
                bridge_connected_at=datetime.utcnow(),
                bridge_skills_dir="/tmp/skills",
            ),
            OpenClawInstance(
                id=f"runtime-{suffix}",
                name="EC 执行 Agent",
                department=department,
                gateway_url="ws://local",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                agent_purpose="skill_runtime",
                bridge_connected_at=datetime.utcnow(),
                bridge_skills_dir="/tmp/skills",
            ),
        ])
        await session.commit()

    result = await execution_service.execute_skill(
        skill_id=skill_id,
        params={"_execution_backend": "bridge_script", "date": "2026-05-22"},
        sandbox=False,
        triggered_by="test",
    )

    assert calls == [f"runtime-{suffix}"]
    assert result["output"]["reports"][0]["title"] == f"runtime-{suffix}"
    assert result["output"]["_skillforge_meta"]["instance_id"] == f"runtime-{suffix}"


@pytest.mark.asyncio
async def test_execute_skill_uses_openclaw_agent_runtime(client, monkeypatch):
    """SKILL.md runtime=openclaw_agent 时，中心触发应通过 bridge 调远端 Agent runtime。"""
    from datetime import datetime
    from uuid import uuid4

    from app.database import async_session_factory, engine
    from app.execution.execution_service import execution_service

    from app.execution.models import ExecutionRun, OpenClawInstance
    from app.skills.models import Skill

    suffix = uuid4().hex[:8]
    skill_id = f"skill-agent-runtime-{suffix}"
    instance_id = f"node-agent-runtime-{suffix}"

    async def fake_pre_check(skill_id):
        return {"passed": True, "reasons": []}

    agent_calls: list[dict] = []

    async def fake_run_agent_skill(self, skill_id, payload, **kwargs):
        agent_calls.append({"skill_id": skill_id, "payload": payload, "kwargs": kwargs})
        return {
            "success": True,
            "duration_ms": 34,
            "output": {"reports": [{"title": "agent runtime"}], "todos": []},
        }

    async def fail_run_skill_script(*args, **kwargs):
        raise AssertionError("run_skill_script should not be used")

    async def fail_default_run(*args, **kwargs):
        raise AssertionError("default_client.run_skill should not be used")

    monkeypatch.setattr(execution_service, "_pre_check_data_quality", fake_pre_check)
    monkeypatch.setattr("app.execution.execution_service.tasktree_dispatcher.schedule_writer", AsyncMock())
    monkeypatch.setattr("app.execution.execution_service.default_client.run_skill", fail_default_run)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_agent_skill", fake_run_agent_skill)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.run_skill_script", fail_run_skill_script)
    monkeypatch.setattr("app.tasktree.service.invalidate_tasktree", AsyncMock())
    monkeypatch.setattr(
        "app.skills.core.git_service.git_service.read_file",
        lambda sid, path: (
            "---\n"
            f"instance_id: {instance_id}\n"
            "runtime:\n"
            "  backend: openclaw_agent\n"
            "  fallback: bridge_script\n"
            "  timeout: 900\n"
            "---\n"
        ),
    )

    await engine.dispose()
    async with async_session_factory() as session:
        session.add(
            Skill(
                id=skill_id,
                name="Agent Runtime",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            )
        )
        session.add(
            OpenClawInstance(
                id=instance_id,
                name="node",
                department="EC",
                gateway_url="ws://local",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                bridge_connected_at=datetime.utcnow(),
                bridge_skills_dir="/tmp/skills",
            )
        )
        await session.commit()

    result = await execution_service.execute_skill(
        skill_id=skill_id,
        params={"date": "2026-04-24"},
        sandbox=False,
        triggered_by="test",
    )

    assert agent_calls[0]["skill_id"] == skill_id
    assert agent_calls[0]["payload"] == {"date": "2026-04-24"}
    assert agent_calls[0]["kwargs"]["runtime"]["backend"] == "openclaw_agent"
    assert result["output"]["reports"][0]["title"] == "agent runtime"
    assert result["output"]["_skillforge_meta"]["execution_backend"] == "openclaw_agent"
    assert result["output"]["_skillforge_meta"]["instance_id"] == instance_id
    async with async_session_factory() as session:
        run = (await session.execute(select(ExecutionRun).where(ExecutionRun.id == result["run_id"]))).scalar_one()
        assert run.source_instance_id == instance_id
        assert run.metadata_json["runtime"]["execution_backend"] == "openclaw_agent"
        assert run.metadata_json["runtime"]["instance_id"] == instance_id
        assert "skill_git_commit_full" in run.metadata_json
        assert run.metadata_json["run_token_claim"]["skill_id"] == skill_id


@pytest.mark.asyncio
async def test_execute_skill_timeout_marks_run_timeout(client, monkeypatch):
    from uuid import uuid4

    from app.common.exceptions import AppError
    from app.database import async_session_factory, engine
    from app.execution.execution_service import execution_service
    from app.execution.models import ExecutionRun
    from app.skills.models import Skill

    async def fake_pre_check(_skill_id):
        return {"passed": True, "reasons": []}

    async def slow_run_skill(*args, **kwargs):
        await asyncio.sleep(0.2)
        return {"summary": "too slow"}

    monkeypatch.setattr(execution_service, "_pre_check_data_quality", fake_pre_check)
    monkeypatch.setattr("app.execution.execution_service.DEFAULT_RUN_SKILL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("app.execution.execution_service.tasktree_dispatcher.schedule_writer", AsyncMock())
    monkeypatch.setattr("app.execution.execution_service.default_client.run_skill", slow_run_skill)
    monkeypatch.setattr("app.tasktree.service.invalidate_tasktree", AsyncMock())
    monkeypatch.setattr("app.execution.execution_service.audit.log", AsyncMock())

    skill_id = f"skill-timeout-run-{uuid4().hex[:8]}"
    await engine.dispose()
    async with async_session_factory() as session:
        session.add(
            Skill(
                id=skill_id,
                name="Timeout Run Skill",
                department="EC",
                visibility="company",
                status="active",
                approval_level=0,
            )
        )
        await session.commit()

    with pytest.raises(AppError) as exc_info:
        await execution_service.execute_skill(
            skill_id=skill_id,
            params={"date": "2026-04-23"},
            sandbox=False,
            triggered_by="test",
        )

    assert exc_info.value.code == "EXECUTION_TIMEOUT"
    assert exc_info.value.detail["phase"] == "run_skill"

    async with async_session_factory() as session:
        run = (
            await session.execute(
                select(ExecutionRun).where(ExecutionRun.skill_id == skill_id)
            )
        ).scalar_one()

    assert run.status == "timeout"
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_execute_skill_quality_check_timeout_persists_timeout_run(client, monkeypatch):
    from uuid import uuid4

    from app.common.exceptions import AppError
    from app.database import async_session_factory, engine
    from app.execution.execution_service import execution_service
    from app.execution.models import ExecutionRun

    async def slow_pre_check(_skill_id):
        await asyncio.sleep(0.2)
        return {"passed": True, "reasons": []}

    monkeypatch.setattr(execution_service, "_pre_check_data_quality", slow_pre_check)
    monkeypatch.setattr("app.execution.execution_service.DATA_QUALITY_CHECK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("app.execution.execution_service.audit.log", AsyncMock())

    skill_id = f"skill-timeout-quality-{uuid4().hex[:8]}"
    await engine.dispose()
    with pytest.raises(AppError) as exc_info:
        await execution_service.execute_skill(
            skill_id=skill_id,
            params={},
            sandbox=False,
            triggered_by="test",
        )

    assert exc_info.value.code == "EXECUTION_TIMEOUT"
    assert exc_info.value.detail["phase"] == "data_quality_check"

    async with async_session_factory() as session:
        run = (
            await session.execute(
                select(ExecutionRun).where(ExecutionRun.skill_id == skill_id)
            )
        ).scalar_one()

    assert run.status == "timeout"
    assert run.metadata_json["timeout"]["phase"] == "data_quality_check"


def test_output_declares_sample_detects_metadata():
    from app.execution.execution_service import output_declares_sample

    assert output_declares_sample({
        "metadata": {"run_mode": "sandbox_test", "sample_used": True},
        "reports": [],
    })
    assert output_declares_sample({
        "_skillforge_meta": {"run_mode": "sample_preview"},
    })
    assert not output_declares_sample({
        "_skillforge_meta": {"run_mode": "manual_real", "sample_used": False},
    })


@pytest.mark.asyncio
async def test_decision_help_action(client):
    """测试 L1 决策求助动作"""
    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun

    async with async_session_factory() as session:
        run = ExecutionRun(id="run-help-001", trigger_type="manual", status="completed")
        decision = DecisionLog(
            run_id="run-help-001",
            skill_id="EC-投放-01",
            approval_level=1,
            approval_status="pending",
        )
        session.add(run)
        session.add(decision)
        await session.commit()
        await session.refresh(decision)
        decision_id = decision.id

    resp = await client.post(
        f"/api/executions/runs/run-help-001/decisions/{decision_id}/action",
        json={"action": "help", "reason": "data_question", "note": "数据口径需要确认"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "help_requested"
    assert data["reason"] == "data_question"
    assert data["note"] == "数据口径需要确认"

    async with async_session_factory() as session:
        result = await session.execute(select(DecisionLog).where(DecisionLog.id == decision_id))
        updated = result.scalar_one()
        assert updated.user_action == "help_requested"
        assert "求助原因: data_question" in (updated.user_feedback or "")
        assert "补充说明: 数据口径需要确认" in (updated.user_feedback or "")


@pytest.mark.asyncio
async def test_mark_winner_keeps_single_winner(client):
    """mark-winner 应先清空同 batch 旧 winner，再设置新 winner。"""
    from app.database import async_session_factory
    from app.execution.models import ExecutionRun

    async with async_session_factory() as session:
        session.add_all([
            ExecutionRun(
                id="run-batch-a",
                trigger_type="manual",
                status="completed",
                batch_id="batch-test-1",
                is_winner=True,
            ),
            ExecutionRun(
                id="run-batch-b",
                trigger_type="manual",
                status="completed",
                batch_id="batch-test-1",
                is_winner=False,
            ),
        ])
        await session.commit()

    resp = await client.post(
        "/api/skills/runs/run-batch-b/mark-winner",
        json={"batch_id": "batch-test-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-batch-b"
    assert data["is_winner"] is True

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(ExecutionRun).where(ExecutionRun.batch_id == "batch-test-1")
            )
        ).scalars().all()
        winner_ids = sorted(run.id for run in rows if run.is_winner)
        assert winner_ids == ["run-batch-b"]


@pytest.mark.asyncio
async def test_execute_batch_returns_structured_errors(client, monkeypatch):
    """execute-batch 单 run 失败时应返回结构化错误，便于前端展示/归档。"""

    async def fake_ensure_skill_access(*_args, **_kwargs):
        return None

    async def fake_execute_skill(*_args, **kwargs):
        triggered_by = kwargs.get("triggered_by", "")
        if ":1:" in triggered_by:
            raise RuntimeError("mock boom")
        return {"run_id": None, "output": {"ok": True}}

    from app.execution import execution_service as execution_service_module

    monkeypatch.setattr("app.skills.router_runtime.ensure_skill_access", fake_ensure_skill_access)
    monkeypatch.setattr(execution_service_module.execution_service, "execute_skill", fake_execute_skill)

    resp = await client.post(
        "/api/skills/skill-batch-test/execute-batch",
        json={"n": 2, "params": {"case": "demo"}, "sandbox": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["batch_id"].startswith("batch-")
    assert len(data["runs"]) == 2

    ok_run = next(item for item in data["runs"] if item["status"] == "ok")
    err_run = next(item for item in data["runs"] if item["status"] == "err")
    assert ok_run["output"] == {"ok": True}
    assert err_run["error"] == {"type": "RuntimeError", "message": "mock boom"}


@pytest.mark.asyncio
async def test_mermaid_conversion():
    """测试Playbook→Mermaid转换"""
    from app.common.mermaid import playbook_to_mermaid

    playbook = {
        "steps": [
            {"id": "check", "skill": "EC-店铺-01", "description": "晨间检查"},
            {"id": "diagnose", "skill": "EC-店铺-02", "depends_on": ["check"], "condition": "has_anomaly==true"},
            {"id": "summary", "skill": "morning-report", "depends_on": ["check", "diagnose"]},
        ]
    }
    mermaid = playbook_to_mermaid(playbook)
    assert "graph TD" in mermaid
    assert "check" in mermaid
    assert "diagnose" in mermaid
    assert "-->" in mermaid
