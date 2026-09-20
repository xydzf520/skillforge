import json
from types import SimpleNamespace

import pytest

from app.training.service import _training_resource_from_instance


def test_training_resources_route_is_not_cached():
    from app.training.router import get_training_resources

    assert not hasattr(get_training_resources, "_cache_prefix")


def test_training_resource_aggregates_gpu_and_training_capability():
    instance = SimpleNamespace(
        id="node-1",
        name="训练网关 A",
        department="EC",
        bridge_gateway_kind="openclaw",
        bridge_gateway_version="1.2.3",
        bridge_version="2.5.0",
        agent_type="aiclaw",
        agent_purpose="training",
        bridge_capabilities_json=json.dumps({
            "gateway_kind": "openclaw",
            "platform": "darwin",
            "bridge_version": "2.5.0",
            "runtimes": ["bridge_script", "openclaw_agent"],
            "ops": ["run_agent_skill", "training.submit_job"],
            "resident_models": [{
                "model": "qwen3.5-4b-lora",
                "deployment_id": "deploy-mac-active",
                "runtime_profile": "mlx-qwen3.5-4b-lora",
                "status": "loaded",
                "loaded": True,
                "model_path": "/Users/secret/models/qwen",
            }],
            "training": {
                "gateway": True,
                "supported_tasks": ["lora", "bad-op", "eval"],
                "gpu_count": 1,
                "worker_count": 1,
            },
            "gpu": [{
                "name": "RTX 4060",
                "vram_total_mb": 8192,
                "vram_used_mb": 2048,
                "vram_free_mb": 6144,
                "gpu_util_pct": 5,
            }],
        }),
    )

    row = _training_resource_from_instance(instance, online=True)

    assert row["id"] == "node-1"
    assert row["online"] is True
    assert row["gateway_kind"] == "openclaw"
    assert row["bridge_platform"] == "darwin"
    assert row["gpu_count"] == 1
    assert row["idle_gpu_count"] == 1
    assert row["vram_total_gb"] == 8.0
    assert row["vram_free_gb"] == 6.0
    assert row["training"]["gateway"] is True
    assert row["training"]["supported_tasks"] == ["lora", "eval"]
    assert row["ops"] == ["run_agent_skill", "training.submit_job"]
    assert row["resident_models"][0]["model"] == "qwen3.5-4b-lora"
    assert row["resident_models"][0]["deployment_id"] == "deploy-mac-active"
    assert row["resident_models"][0]["loaded"] is True
    assert "secret" not in json.dumps(row["resident_models"], ensure_ascii=False)


def test_training_resource_does_not_echo_paths_or_unknown_training_tasks():
    instance = SimpleNamespace(
        id="node-2",
        name="节点 B",
        department="EC",
        bridge_gateway_kind="openclaw",
        bridge_gateway_version="",
        bridge_version="",
        agent_type="aiclaw",
        agent_purpose="training",
        bridge_capabilities_json=json.dumps({
            "home": "/home/secret-user",
            "skills_dirs": ["/home/secret-user/.openclaw/skills"],
            "training": {
                "gateway": True,
                "supported_tasks": ["shell", "../escape"],
            },
        }),
    )

    row = _training_resource_from_instance(instance, online=False)
    row_text = json.dumps(row, ensure_ascii=False)

    assert "secret-user" not in row_text
    assert row["training"]["supported_tasks"] == ["eval"]


def test_training_resource_runtime_agent_is_not_training_ready_even_with_gpu():
    instance = SimpleNamespace(
        id="node-runtime",
        name="执行 Agent",
        department="EC",
        bridge_gateway_kind="openclaw",
        bridge_gateway_version="",
        bridge_version="",
        agent_type="aiclaw",
        agent_purpose="skill_runtime",
        bridge_capabilities_json=json.dumps({
            "ops": ["training.submit_job"],
            "training": {"gateway": True, "supported_tasks": ["lora"]},
            "gpu": [{"name": "RTX 4060", "vram_total_mb": 8192, "vram_free_mb": 4096}],
        }),
    )

    row = _training_resource_from_instance(instance, online=True)

    assert row["training"]["route_eligible"] is False
    assert row["training"]["gateway"] is False
    assert row["training"]["supported_tasks"] == []
    assert row["gpu_count"] == 1


def test_training_resource_marks_rtx_pro_6000_node_reserved_for_comfyui():
    instance = SimpleNamespace(
        id="data-primary",
        name="RTX Pro 6000 · ComfyUI",
        department="AI",
        bridge_gateway_kind="openclaw",
        bridge_gateway_version="",
        bridge_version="",
        agent_type="aiclaw",
        agent_purpose="mixed",
        is_platform_default=True,
        bridge_capabilities_json=json.dumps({
            "ops": ["training.submit_job", "training.collect_result", "training.inference"],
            "training": {"gateway": True, "supported_tasks": ["lora", "qlora", "merge"]},
            "gpu": [{"name": "NVIDIA RTX PRO 6000", "vram_total_mb": 97800, "vram_free_mb": 90000}],
        }),
    )

    row = _training_resource_from_instance(instance, online=True)

    assert row["reserved_for"] == "comfyui"
    assert row["resource_role"] == "reserved_comfyui"
    assert row["training"]["reserved_for"] == "comfyui"
    assert row["training"]["route_eligible"] is False
    assert row["training"]["gateway"] is False
    assert row["training"]["supported_tasks"] == []


@pytest.mark.asyncio
async def test_training_resources_include_active_jobs_without_paths(client):
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-active",
            name="训练网关 Active",
            department="EC",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            connection_mode="bridge",
            agent_purpose="training",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "home": "/home/secret-user",
                "training": {"gateway": True, "supported_tasks": ["lora"]},
                "gpu": [{"name": "RTX 4060", "vram_total_mb": 8192, "vram_free_mb": 4096}],
            }),
        ))
        db.add_all([
            TrainingJob(
                id="train_active",
                title="当前训练任务",
                department="EC",
                created_by="admin",
                status="running",
                job_type="lora",
                target_skill_id="skill-recommend",
                target_gateway_id="node-active",
                approval_required=True,
            ),
            TrainingJob(
                id="train_done",
                title="已完成任务",
                department="EC",
                created_by="admin",
                status="completed",
                job_type="lora",
                target_gateway_id="node-active",
                approval_required=True,
            ),
        ])
        await db.commit()

    resp = await client.get("/api/training/resources")
    assert resp.status_code == 200
    payload = resp.json()
    row = next(item for item in payload["items"] if item["id"] == "node-active")
    encoded = json.dumps(row, ensure_ascii=False)

    assert row["active_training_jobs_count"] == 1
    assert row["active_training_jobs"][0]["id"] == "train_active"
    assert row["active_training_jobs"][0]["title"] == "当前训练任务"
    assert row["active_training_jobs"][0]["status"] == "running"
    assert payload["stats"]["active_training_jobs"] >= 1
    assert "secret-user" not in encoded


@pytest.mark.asyncio
async def test_department_training_resources_include_platform_fallback_without_secrets(client):
    from app.auth.dependencies import get_current_user, require_state_active_web_or_cli
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob

    dept_user = SimpleNamespace(
        id="ec-manager",
        name="EC 负责人",
        username="ec-manager",
        role="dept_admin",
        department="EC",
        can_view_all=False,
        is_active=True,
        state="active",
    )
    client._transport.app.dependency_overrides[get_current_user] = lambda: dept_user
    client._transport.app.dependency_overrides[require_state_active_web_or_cli] = lambda: dept_user

    async with async_session_factory() as db:
        db.add_all([
            OpenClawInstance(
                id="platform-training-fallback",
                name="平台训练 Agent",
                department="AI",
                gateway_url="ws://platform-secret-gateway",
                reload_hook_url="http://platform-secret-hook",
                reload_token="platform-secret-token",
                auth_token="platform-secret-auth",
                agent_type="aiclaw",
                connection_mode="bridge",
                agent_purpose="training",
                is_platform_default=True,
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "home": "/home/platform-secret-user",
                    "ops": ["training.submit_job"],
                    "training": {"gateway": True, "supported_tasks": ["lora"]},
                    "gpu": [{"name": "RTX 4090", "vram_total_mb": 24576, "vram_free_mb": 20480}],
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="external-training-hidden",
                name="其他部门训练 Agent",
                department="Sales",
                gateway_url="ws://external-secret-gateway",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                connection_mode="bridge",
                agent_purpose="training",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": ["training.submit_job"],
                    "training": {"gateway": True, "supported_tasks": ["lora"]},
                }),
                is_active=True,
            ),
        ])
        db.add(TrainingJob(
            id="train_platform_fallback",
            title="平台兜底训练任务",
            department="EC",
            created_by="ec-manager",
            status="running",
            job_type="lora",
            target_gateway_id="platform-training-fallback",
            approval_required=True,
        ))
        await db.commit()

    resp = await client.get("/api/training/resources")
    assert resp.status_code == 200
    payload = resp.json()
    rows = {item["id"]: item for item in payload["items"]}
    assert "platform-training-fallback" in rows
    assert "external-training-hidden" not in rows

    platform = rows["platform-training-fallback"]
    assert platform["route_scope"] == "platform_fallback"
    assert platform["training"]["gateway"] is True
    assert platform["active_training_jobs_count"] == 1
    assert platform["active_training_jobs"][0]["id"] == "train_platform_fallback"
    encoded = json.dumps(platform, ensure_ascii=False)
    assert "platform-secret" not in encoded
    assert "secret-user" not in encoded
