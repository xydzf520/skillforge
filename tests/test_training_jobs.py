import asyncio
import base64
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select


class _TrainingChatDummyWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code=1000, reason=''):
        return None


async def _wait_training_chat_sent(ws: _TrainingChatDummyWS, count: int = 1) -> list[dict]:
    for _ in range(100):
        if len(ws.sent) >= count:
            return ws.sent
        await asyncio.sleep(0.01)
    return ws.sent


def test_training_deployment_mapping_reads_nested_gateway_and_runtime_profile():
    from app.training.service import (
        _deployment_runtime_profile_from_mapping,
        _deployment_target_from_mapping,
    )

    spec = {
        "deployment": {
            "deployment_target_gateway_id": "platform-mac-238",
            "deployment_runtime_profile": "mlx-qwen3.6-35b-a3b-lora",
        }
    }

    assert _deployment_target_from_mapping(spec) == "platform-mac-238"
    assert _deployment_runtime_profile_from_mapping(spec) == "mlx-qwen3.6-35b-a3b-lora"


async def _seed_cli_session_token(raw_token: str, *, role: str = "admin", department: str = "AI小组") -> None:
    from app.auth.models import User
    from app.codex import service as codex_service
    from app.codex.models import CodexCliSession
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory

    async with async_session_factory() as db:
        user = await db.get(User, "admin")
        if user is None:
            db.add(User(
                id="admin",
                username="admin",
                name="管理员",
                role=role,
                department=department,
                can_view_all=True,
                is_active=True,
                state="active",
                permissions_rev=0,
            ))
        else:
            user.role = role
            user.department = department
            user.can_view_all = True
            user.is_active = True
            user.state = "active"
            user.permissions_rev = 0
        db.add(CodexCliSession(
            id=f"cli_{raw_token}",
            user_id="admin",
            token_hash=codex_service.token_hash(raw_token),
            device_name="pytest",
            scopes_json={"source": "pytest"},
            permissions_rev_snapshot=0,
            expires_at=now_bjt() + timedelta(days=30),
        ))
        await db.commit()


async def _seed_default_training_gateway(
    *,
    gateway_id: str = "node-1",
    supported_tasks: list[str] | None = None,
) -> None:
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    tasks = supported_tasks or ["lora", "eval"]
    async with async_session_factory() as db:
        existing = await db.get(OpenClawInstance, gateway_id)
        if existing is None:
            db.add(OpenClawInstance(
                id=gateway_id,
                name="训练网关 A",
                department="EC",
                gateway_url="http://127.0.0.1:18789",
                reload_hook_url="http://127.0.0.1:9000/reload",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="training",
                connection_mode="bridge",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": [
                        "training.submit_job",
                        "training.cancel_job",
                        "training.stream_logs",
                        "training.collect_result",
                        "training.download_artifact",
                        "training.inference",
                    ],
                    "training": {
                        "gateway": True,
                        "supported_tasks": tasks,
                        "gpu_count": 1,
                        "worker_count": 1,
                    },
                }),
                is_active=True,
            ))
        else:
            existing.agent_purpose = "training"
            existing.bridge_capabilities_json = json.dumps({
                "ops": [
                    "training.submit_job",
                    "training.cancel_job",
                    "training.stream_logs",
                    "training.collect_result",
                    "training.download_artifact",
                    "training.inference",
                ],
                "training": {
                    "gateway": True,
                    "supported_tasks": tasks,
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            })
        await db.commit()


async def _seed_m3_deployment_gateway(
    *,
    gateway_id: str = "inference-primary",
    name: str = "M3 Ultra 部署节点",
    is_active: bool = True,
) -> None:
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id=gateway_id,
            name=name,
            department="AI",
            gateway_url=f"ws://{gateway_id}",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "gateway_kind": "openclaw",
                "ops": ["run_skill_script", "training.inference", "training.artifact_status"],
                "training": {"gateway": False, "supported_tasks": ["eval"], "gpu_count": 0},
                "gpu": [{"name": "Apple M3 Ultra", "vram_total_mb": 524288, "vram_free_mb": 500000}],
            }),
            is_active=is_active,
        ))
        await db.commit()


@pytest.mark.asyncio
async def test_training_approval_builds_redacted_learning_dataset_package(client):
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.learning.models import LearningArtifact, LearningEvent, LearningFlowEdge
    from app.learning.service import capture_training_job
    from app.training.models import TrainingJob

    raw_token = "test-training-dataset-package-token"
    await _seed_cli_session_token(raw_token, department="EC")
    client._transport.app.dependency_overrides.pop(get_current_user, None)
    headers = {"Authorization": f"Bearer {raw_token}"}

    await _seed_default_training_gateway(gateway_id="node-learning", supported_tasks=["qlora", "lora"])
    async with async_session_factory() as db:
        db.add(LearningEvent(
            id="le-training-package",
            event_type="decision.completed",
            source_type="decision_log",
            source_id="decision-training-package",
            source_hash="decision-training-package",
            department="EC",
            skill_id="skill-learning-package",
            redacted_summary="训练样本包验证",
            quality_score=0.9,
        ))
        for idx in range(4):
            db.add(LearningArtifact(
                id=f"la-training-package-{idx}",
                event_id="le-training-package",
                artifact_kind="training_sample",
                artifact_hash=f"la-training-package-{idx}",
                target_type="skill",
                target_id="skill-learning-package",
                department="EC",
                skill_id="skill-learning-package",
                title=f"训练样本 {idx}",
                summary="用于验证样本包只在 gateway payload 内部下发。",
                content_json={
                    "input": {"video_id": f"video-{idx}", "business_note": "payload-should-not-leak"},
                    "output": {"todo": "优化高消耗低转化视频"},
                    "user_action": "approved",
                    "source_type": "todo_feedback" if idx == 0 else "decision_log",
                    "source_channel": "dingtalk" if idx == 0 else "platform",
                    "feedback": {"status": "approved", "source_channel": "dingtalk"} if idx == 0 else {},
                    "human_feedback_input": {
                        "source_channel": "dingtalk",
                        "fields": {
                            "feedback_type": "确认有效",
                            "rating": 5,
                            "feedback": "用户在钉钉确认这个建议有效",
                        },
                    } if idx == 0 else {},
                    "decision": {
                        "decision_log_id": 1000 + idx,
                        "rating": 5,
                        "feedback_type": "确认有效",
                    } if idx == 0 else {},
                    "model_context": {
                        "model_deployment_id": "deploy-learning-package",
                        "model_family": "skill-learning-package:qwen3.5-4b-qlora",
                        "deployment_status": "active",
                        "artifact_sha256": "d" * 64,
                    } if idx == 0 else {},
                },
                labels_json=["training"],
                quality_score=0.9,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
            ))
        db.add(LearningArtifact(
            id="la-training-package-trace",
            event_id="le-training-package",
            artifact_kind="training_sample",
            artifact_hash="la-training-package-trace",
            target_type="skill",
            target_id="skill-learning-package",
            department="EC",
            skill_id="skill-learning-package",
            title="执行轨迹训练样本",
            summary="用于验证执行输入、输出和形成数据会进入训练数据包。",
            content_json={
                "instruction": "基于执行轨迹学习输入输出和形成数据。",
                "input": {"steps": [{"input": {"question": "链接下滑原因"}}]},
                "output": {"steps": [{"output": {"summary": "搜索访客下降"}}]},
                "formed_data": {"steps": [{"duration_ms": 321}]},
                "runtime_agent": {
                    "agent_id": "node-learning",
                    "contract": {
                        "complete": True,
                        "input_channels": ["node_schedule_snapshot", "skill_run_request"],
                        "output_channels": ["decision_log", "execution_artifact", "execution_run"],
                    },
                },
                "source_type": "execution_trace",
                "source_channel": "node_scheduler",
            },
            labels_json=["training", "execution_trace"],
            quality_score=0.82,
            confidence=0.74,
            status="materialized",
            sink_type="training_sample",
        ))
        await db.commit()

    learning_artifact_ids = [f"la-training-package-{idx}" for idx in range(4)] + ["la-training-package-trace"]
    create_resp = await client.post(
        "/api/training/jobs",
        headers=headers,
        json={
            "title": "学习样本包 QLoRA 验证",
            "department": "EC",
            "job_type": "qlora",
            "target_skill_id": "skill-learning-package",
            "target_gateway_id": "node-learning",
            "dataset_ref": "learning-artifacts://skill-learning-package/training/latest",
            "spec": {
                "base_model": "Qwen/Qwen3.5-4B",
                "model": {"profile": "qwen3.5-4b"},
                "dataset": {
                    "learning_artifact_ids": learning_artifact_ids,
                },
                "lineage": {
                    "source": "learning_artifacts",
                    "learning_artifact_ids": learning_artifact_ids,
                },
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()

    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve", headers=headers)
    assert approve_resp.status_code == 200, approve_resp.text
    approved = approve_resp.json()
    assert approved["gateway_payload"]["dataset_package"]["sample_count"] == 5
    assert approved["gateway_payload"]["dataset_package"]["samples"] == "[REDACTED]"
    assert approved["gateway_payload"]["dataset_package"]["input_contract"]["required_fields"] == [
        "instruction",
        "input",
        "output",
    ]
    assert approved["gateway_payload"]["dataset_package"]["output_contract"]["callback_required"] is True
    assert approved["gateway_payload"]["dataset_package"]["lineage"]["source"] == "learning_artifacts"
    assert approved["gateway_payload"]["dataset_package"]["lineage"]["learning_artifact_ids"] == learning_artifact_ids
    encoded_approved = json.dumps(approved, ensure_ascii=False)
    assert "payload-should-not-leak" not in encoded_approved

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        package = job.gateway_payload_json["dataset_package"]
        await capture_training_job(db, job)
        await db.commit()
    assert package["sample_count"] == 5
    assert package["input_contract"]["schema"] == "skillforge_sft_jsonl_v1"
    assert package["input_contract"]["lineage_field"] == "metadata"
    assert package["output_contract"]["requires_artifact_sha256"] is True
    assert package["lineage"]["training_job_id"] == created["id"]
    assert package["lineage"]["target_skill_id"] == "skill-learning-package"
    assert package["lineage"]["learning_artifact_ids"] == learning_artifact_ids
    assert package["lineage"]["manifest_hash"] == package["manifest_hash"]
    assert package["samples"][0]["id"] == "la-training-package-0"
    assert package["samples"][0]["metadata"]["learning_artifact_id"] == "la-training-package-0"
    assert package["samples"][0]["metadata"]["source_type"] == "todo_feedback"
    assert package["samples"][0]["metadata"]["source_channel"] == "dingtalk"
    assert package["samples"][0]["metadata"]["has_input"] is True
    assert package["samples"][0]["metadata"]["has_output"] is True
    assert package["samples"][0]["metadata"]["has_human_feedback"] is True
    assert package["samples"][0]["metadata"]["feedback_source"] == "dingtalk"
    assert package["samples"][0]["metadata"]["has_model_context"] is True
    assert package["samples"][0]["metadata"]["model_deployment_id"] == "deploy-learning-package"
    assert package["samples"][0]["metadata"]["input_sha256"]
    assert package["samples"][0]["metadata"]["output_sha256"]
    assert package["samples"][0]["metadata"]["sample_sha256"]
    assert "payload-should-not-leak" in package["samples"][0]["input"]
    assert '"source_type":"todo_feedback"' in package["samples"][0]["input"]
    assert '"source_channel":"dingtalk"' in package["samples"][0]["input"]
    assert '"channel":"dingtalk"' in package["samples"][0]["input"]
    assert '"structured":{"fields":{"feedback":"用户在钉钉确认这个建议有效","feedback_type":"确认有效","rating":5}' in package["samples"][0]["input"]
    assert '"decision_log_id":1000' in package["samples"][0]["input"]
    assert '"model_deployment_id":"deploy-learning-package"' in package["samples"][0]["input"]
    assert '"artifact_sha256":"' + "d" * 64 + '"' in package["samples"][0]["input"]
    trace_sample = next(item for item in package["samples"] if item["id"] == "la-training-package-trace")
    assert trace_sample["metadata"]["source_type"] == "execution_trace"
    assert trace_sample["metadata"]["source_channel"] == "node_scheduler"
    assert trace_sample["metadata"]["has_formed_data"] is True
    assert trace_sample["metadata"]["formed_data_sha256"]
    assert trace_sample["metadata"]["has_runtime_agent"] is True
    assert trace_sample["metadata"]["runtime_agent_id"] == "node-learning"
    assert trace_sample["metadata"]["runtime_agent_contract_complete"] is True
    assert '"formed_data":{"steps":[{"duration_ms":321}]}' in trace_sample["input"]
    assert '"previous_output":{"steps":[{"output":{"summary":"搜索访客下降"}}]}' in trace_sample["input"]
    assert package["manifest_hash"] != hashlib.sha256(
        json.dumps(
            {
                "job_id": created["id"],
                "dataset_ref": "learning-artifacts://skill-learning-package/training/latest",
                "artifact_ids": learning_artifact_ids,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    async with async_session_factory() as db:
        event = (
            await db.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "training_job")
                .where(LearningEvent.source_id == created["id"])
            )
        ).scalar_one()
        control_edge = (
            await db.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "agent")
                .where(LearningFlowEdge.from_id == "node-learning")
                .where(LearningFlowEdge.to_type == "training_job")
                .where(LearningFlowEdge.to_id == created["id"])
                .where(LearningFlowEdge.relation == "controlled_training")
            )
        ).scalar_one()
    assert event.metadata_json["dataset_package"]["manifest_hash"] == package["manifest_hash"]
    assert event.metadata_json["dataset_package"]["samples"] == "[REDACTED]"
    assert event.metadata_json["dataset_package"]["sample_count"] == 5
    assert event.metadata_json["dataset_package"]["sample_fingerprints"][0]["id"] == "la-training-package-0"
    assert event.metadata_json["dataset_package"]["sample_fingerprints"][0]["source_channel"] == "dingtalk"
    assert event.metadata_json["dataset_package"]["sample_fingerprints"][0]["has_human_feedback"] is True
    assert event.metadata_json["dataset_package"]["sample_fingerprints"][0]["feedback_source"] == "dingtalk"
    assert event.metadata_json["dataset_package"]["sample_fingerprints"][0]["has_model_context"] is True
    assert event.metadata_json["dataset_package"]["sample_fingerprints"][0]["sample_sha256"]
    trace_fingerprint = next(
        item
        for item in event.metadata_json["dataset_package"]["sample_fingerprints"]
        if item["id"] == "la-training-package-trace"
    )
    assert trace_fingerprint["source_type"] == "execution_trace"
    assert trace_fingerprint["source_channel"] == "node_scheduler"
    assert trace_fingerprint["has_formed_data"] is True
    assert trace_fingerprint["formed_data_sha256"] == trace_sample["metadata"]["formed_data_sha256"]
    assert trace_fingerprint["has_runtime_agent"] is True
    assert trace_fingerprint["runtime_agent_id"] == "node-learning"
    assert trace_fingerprint["runtime_agent_contract_complete"] is True
    assert control_edge.metadata_json["dataset_package"]["manifest_hash"] == package["manifest_hash"]
    assert control_edge.metadata_json["dataset_package"]["sample_fingerprints"][0]["source_type"] == "todo_feedback"
    assert control_edge.metadata_json["dataset_package"]["raw_payload_returned"] is False
    encoded_dataset_evidence = json.dumps(
        {
            "event": event.metadata_json["dataset_package"],
            "edge": control_edge.metadata_json["dataset_package"],
        },
        ensure_ascii=False,
    )
    assert "payload-should-not-leak" not in encoded_dataset_evidence
    assert "用户在钉钉确认这个建议有效" not in encoded_dataset_evidence


@pytest.mark.asyncio
async def test_training_approval_externalizes_learning_dataset_to_235_for_237(client, monkeypatch):
    monkeypatch.setattr("app.training.service.TRAINING_MODEL_TRANSFER_HOST_OVERRIDES", {"data-primary": "192.0.2.10"})
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training.models import TrainingJob

    raw_token = "test-training-external-dataset-token"
    await _seed_cli_session_token(raw_token, department="EC")
    client._transport.app.dependency_overrides.pop(get_current_user, None)
    headers = {"Authorization": f"Bearer {raw_token}"}

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="data-primary",
            name="AI 235 · RTX PRO 6000",
            department="EC",
            gateway_url="ws://data-primary",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": [
                    "training.dataset_write_chunk",
                    "training.dataset_commit",
                    "training.dataset_status",
                    "training.dataset_export_start",
                    "training.dataset_export_manifest",
                    "training.dataset_export_file_chunk",
                ],
                "training": {"gateway": True, "supported_tasks": ["eval"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        db.add(OpenClawInstance(
            id="training-primary",
            name="GB10 237 · NVIDIA GB10",
            department="EC",
            gateway_url="ws://training-primary",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": [
                    "training.submit_job",
                    "training.collect_result",
                    "training.inference",
                    "training.dataset_import_from_export",
                    "training.dataset_import_relay_file_status",
                    "training.dataset_import_relay_chunk",
                    "training.dataset_import_relay_commit",
                ],
                "training": {"gateway": True, "supported_tasks": ["lora", "qlora", "eval"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        db.add(LearningEvent(
            id="le-external-dataset",
            event_type="decision.completed",
            source_type="decision_log",
            source_id="decision-external-dataset",
            source_hash="decision-external-dataset",
            department="EC",
            skill_id="skill-external-dataset",
            redacted_summary="外置数据集验证",
            quality_score=0.9,
        ))
        for idx in range(5):
            db.add(LearningArtifact(
                id=f"la-external-dataset-{idx}",
                event_id="le-external-dataset",
                artifact_kind="training_sample",
                artifact_hash=f"la-external-dataset-{idx}",
                target_type="skill",
                target_id="skill-external-dataset",
                department="EC",
                skill_id="skill-external-dataset",
                title=f"外置训练样本 {idx}",
                summary="成人用品电商测试样本",
                content_json={
                    "input": {"keyword": "安全套", "scenario": f"case-{idx}", "payload": "do-not-inline"},
                    "output": {"category": "成人用品/情趣用品", "action": "合规表达并评估转化"},
                    "source_type": "ecommerce_fixture",
                    "source_channel": "sf_data",
                    "feedback": {"status": "approved"},
                },
                labels_json=["training", "ecommerce", "adult_products"],
                quality_score=0.9,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
            ))
        await db.commit()

    calls: list[tuple[str, str, dict]] = []

    async def fake_write_training_dataset_chunk(self, payload, *, timeout=120):
        calls.append(("write", self.instance_id, payload))
        return {"status": "ok", "dataset_id": payload["dataset_id"], "written_size_bytes": 1024}

    async def fake_commit_training_dataset(self, payload, *, timeout=300):
        calls.append(("commit", self.instance_id, payload))
        return {
            "status": "succeeded",
            "dataset_id": payload["dataset_id"],
            "dataset_dir": f"/srv/skillforge/datasets/{payload['dataset_id']}",
            "dataset_path": f"/srv/skillforge/datasets/{payload['dataset_id']}/dataset.json",
            "sha256": payload["sha256"],
            "size_bytes": 1024,
        }

    async def fake_start_training_dataset_export(self, payload, *, timeout=3600):
        calls.append(("export", self.instance_id, payload))
        return {
            "status": "ready",
            "token": "dataset-export-token",
            "export_url": "http://127.0.0.1:18789/training-model-export/dataset",
            "manifest_url": "http://127.0.0.1:18789/training-model-export/dataset/manifest",
            "manifest_sha256": "a" * 64,
        }

    async def fake_import_training_dataset_from_export(self, payload, *, timeout=21600):
        calls.append(("import", self.instance_id, payload))
        return {
            "status": "succeeded",
            "dataset_id": payload["dataset_id"],
            "dataset_dir": f"/home/skillforge/.skillforge_bridge/89cd6683/training_datasets/{payload['dataset_id']}/a",
            "dataset_path": (
                f"/home/skillforge/.skillforge_bridge/89cd6683/training_datasets/"
                f"{payload['dataset_id']}/a/dataset.json"
            ),
            "manifest_sha256": payload["manifest_sha256"],
            "sha256": "b" * 64,
        }

    monkeypatch.setattr(
        bridge_registry,
        "is_online",
        lambda instance_id: instance_id in {"data-primary", "training-primary"},
    )
    monkeypatch.setattr(AIClawClient, "write_training_dataset_chunk", fake_write_training_dataset_chunk)
    monkeypatch.setattr(AIClawClient, "commit_training_dataset", fake_commit_training_dataset)
    monkeypatch.setattr(AIClawClient, "start_training_dataset_export", fake_start_training_dataset_export)
    monkeypatch.setattr(AIClawClient, "import_training_dataset_from_export", fake_import_training_dataset_from_export)

    learning_artifact_ids = [f"la-external-dataset-{idx}" for idx in range(5)]
    create_resp = await client.post(
        "/api/training/jobs",
        headers=headers,
        json={
            "title": "学习样本外置到 235 后由 237 训练",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-external-dataset",
            "target_gateway_id": "training-primary",
            "dataset_ref": "learning-artifacts://skill-external-dataset/training/latest",
            "spec": {
                "model": {
                    "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                    "source": "internal",
                },
                "dataset": {
                    "learning_artifact_ids": learning_artifact_ids,
                    "bridge_dataset": {
                        "enabled": True,
                        "source_gateway_id": "data-primary",
                        "target_gateway_id": "training-primary",
                    },
                },
                "lineage": {
                    "source": "learning_artifacts",
                    "learning_artifact_ids": learning_artifact_ids,
                },
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()

    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve", headers=headers)
    assert approve_resp.status_code == 200, approve_resp.text
    approved = approve_resp.json()
    gateway_payload = approved["gateway_payload"]
    ref = gateway_payload["dataset_package_ref"]

    assert "dataset_package" not in gateway_payload
    assert ref["storage"] == "bridge_dataset"
    assert ref["source_gateway_id"] == "data-primary"
    assert ref["training_gateway_id"] == "training-primary"
    assert ref["sample_count"] == 5
    assert ref["dataset_path"].endswith("/dataset.json")
    assert ref["transfer_mode"] == "direct_http"
    assert gateway_payload["control"]["agent_id"] == "training-primary"
    assert ("write", "data-primary") in [(name, instance_id) for name, instance_id, _payload in calls]
    assert ("commit", "data-primary") in [(name, instance_id) for name, instance_id, _payload in calls]
    assert ("export", "data-primary") in [(name, instance_id) for name, instance_id, _payload in calls]
    assert ("import", "training-primary") in [(name, instance_id) for name, instance_id, _payload in calls]
    import_call = next(payload for name, _instance_id, payload in calls if name == "import")
    assert import_call["export_url"].startswith("http://192.0.2.10:")
    assert import_call["source_gateway_id"] == "data-primary"
    assert "do-not-inline" not in json.dumps(approved, ensure_ascii=False)

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        assert "dataset_package_ref" in job.gateway_payload_json
        assert "dataset_package" not in job.gateway_payload_json
        assert job.gateway_payload_json["dataset_package_ref"]["source_gateway_id"] == "data-primary"


@pytest.mark.asyncio
async def test_training_gateway_dataset_selector_packages_full_history_samples(client):
    from app.database import async_session_factory
    from app.learning.models import LearningArtifact, LearningEvent
    from app.common.time_utils import now_bjt
    from app.training.models import TrainingJob
    from app.training.service import _build_learning_gateway_dataset_package

    now = now_bjt()
    async with async_session_factory() as db:
        db.add(LearningEvent(
            id="le-training-selector-package",
            event_type="decision.completed",
            source_type="decision_log",
            source_id="decision-training-selector-package",
            source_hash="decision-training-selector-package",
            department="EC",
            skill_id="skill-selector-package",
            redacted_summary="全历史 selector 训练样本包验证",
            quality_score=0.9,
            created_at=now,
            updated_at=now,
        ))
        for idx in range(30):
            db.add(LearningArtifact(
                id=f"la-selector-package-{idx:02d}",
                event_id="le-training-selector-package",
                artifact_kind="eval_case" if idx in {5, 17} else "training_sample",
                artifact_hash=f"la-selector-package-{idx:02d}",
                target_type="skill",
                target_id="skill-selector-package",
                department="EC",
                skill_id="skill-selector-package",
                title=f"selector 样本 {idx}",
                summary="用于验证 selector 会完整打包全历史学习样本。",
                content_json={
                    "input": {"question": f"商品 {idx} 消耗异常原因"},
                    "output": {"summary": f"样本 {idx} 的诊断输出"},
                    "source_type": "execution_trace",
                    "formed_data": {"step": idx},
                },
                labels_json=["training"],
                quality_score=0.86,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=now + timedelta(seconds=idx),
                updated_at=now + timedelta(seconds=idx),
            ))
        job = TrainingJob(
            id="train_selector_package",
            title="selector 全历史训练",
            department="EC",
            created_by="learning_auto_flow",
            status="queued",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="skill-selector-package",
            dataset_ref="learning-artifacts://platform/training/full-history/test",
            spec_json={
                "dataset": {
                    "selector": {
                        "type": "learning_artifacts",
                        "created_at_start": now.isoformat(),
                        "created_at_end": (now + timedelta(minutes=1)).isoformat(),
                        "limit": 30,
                    }
                }
            },
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        await db.flush()

        package = await _build_learning_gateway_dataset_package(db, job)

    assert package is not None
    assert package["sample_count"] == 30
    assert package["train_count"] == 28
    assert package["eval_count"] == 2
    assert package["lineage"]["learning_artifact_ids"][0] == "la-selector-package-00"
    assert package["lineage"]["learning_artifact_ids"][-1] == "la-selector-package-29"
    assert len(package["samples"]) == 30


@pytest.mark.asyncio
async def test_training_gateway_dataset_selector_reserves_eval_samples(client):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training.models import TrainingJob
    from app.training.service import _build_learning_gateway_dataset_package

    now = now_bjt()
    async with async_session_factory() as db:
        db.add(LearningEvent(
            id="le-training-selector-eval-reserve",
            event_type="decision.completed",
            source_type="decision_log",
            source_id="decision-training-selector-eval-reserve",
            source_hash="decision-training-selector-eval-reserve",
            department="EC",
            skill_id="skill-selector-eval-reserve",
            redacted_summary="全历史 selector 评测预留验证",
            quality_score=0.9,
            created_at=now,
            updated_at=now,
        ))
        for idx in range(300):
            db.add(LearningArtifact(
                id=f"la-selector-eval-reserve-train-{idx:03d}",
                event_id="le-training-selector-eval-reserve",
                artifact_kind="training_sample",
                artifact_hash=f"la-selector-eval-reserve-train-{idx:03d}",
                target_type="skill",
                target_id="skill-selector-eval-reserve",
                department="EC",
                skill_id="skill-selector-eval-reserve",
                title=f"评测预留训练样本 {idx}",
                summary="用于验证前序训练样本很多时也会保留评测样本。",
                content_json={
                    "input": {"question": f"训练商品 {idx} 消耗异常原因"},
                    "output": {"summary": f"训练样本 {idx} 的诊断输出"},
                    "source_type": "execution_trace",
                },
                labels_json=["training"],
                quality_score=0.86,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=now + timedelta(seconds=idx),
                updated_at=now + timedelta(seconds=idx),
            ))
        for idx in range(20):
            db.add(LearningArtifact(
                id=f"la-selector-eval-reserve-eval-{idx:03d}",
                event_id="le-training-selector-eval-reserve",
                artifact_kind="eval_case",
                artifact_hash=f"la-selector-eval-reserve-eval-{idx:03d}",
                target_type="skill",
                target_id="skill-selector-eval-reserve",
                department="EC",
                skill_id="skill-selector-eval-reserve",
                title=f"评测预留样本 {idx}",
                summary="用于验证评测样本被独立预留。",
                content_json={
                    "input": {"question": f"评测商品 {idx} 消耗异常原因"},
                    "actual_output": {"summary": f"评测样本 {idx} 的模型输出"},
                    "expected_signal": "应该指出同主题参照和转化承接差异。",
                    "source_type": "decision_feedback",
                },
                labels_json=["eval"],
                quality_score=0.86,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=now + timedelta(days=1, seconds=idx),
                updated_at=now + timedelta(days=1, seconds=idx),
            ))
        job = TrainingJob(
            id="train_selector_eval_reserve",
            title="selector 评测预留训练",
            department="EC",
            created_by="learning_auto_flow",
            status="queued",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="skill-selector-eval-reserve",
            dataset_ref="learning-artifacts://platform/training/full-history/eval-reserve",
            spec_json={
                "dataset": {
                    "sample_total": 320,
                    "train_count": 300,
                    "eval_count": 20,
                    "selector": {
                        "type": "learning_artifacts",
                        "created_at_start": now.isoformat(),
                        "created_at_end": (now + timedelta(days=2)).isoformat(),
                        "limit": 320,
                        "inline_limit": 256,
                        "inline_eval_limit": 16,
                    },
                }
            },
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        await db.flush()

        package = await _build_learning_gateway_dataset_package(db, job)

    assert package is not None
    assert package["sample_count"] == 256
    assert package["train_count"] == 240
    assert package["eval_count"] == 16
    assert package["manifest_sample_count"] == 320
    assert package["inline_eval_limit"] == 16
    assert package["samples"][239]["id"] == "la-selector-eval-reserve-train-239"
    assert package["samples"][240]["id"] == "la-selector-eval-reserve-eval-000"
    assert package["samples"][-1]["id"] == "la-selector-eval-reserve-eval-015"


@pytest.mark.asyncio
async def test_training_gateway_dataset_selector_backfills_unused_eval_reserve(client):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training.models import TrainingJob
    from app.training.service import _build_learning_gateway_dataset_package

    now = now_bjt()
    async with async_session_factory() as db:
        db.add(LearningEvent(
            id="le-training-selector-eval-backfill",
            event_type="decision.completed",
            source_type="decision_log",
            source_id="decision-training-selector-eval-backfill",
            source_hash="decision-training-selector-eval-backfill",
            department="EC",
            skill_id="skill-selector-eval-backfill",
            redacted_summary="评测预留不足时回填训练样本验证",
            quality_score=0.9,
            created_at=now,
            updated_at=now,
        ))
        for idx in range(12):
            db.add(LearningArtifact(
                id=f"la-selector-eval-backfill-train-{idx:02d}",
                event_id="le-training-selector-eval-backfill",
                artifact_kind="training_sample",
                artifact_hash=f"la-selector-eval-backfill-train-{idx:02d}",
                target_type="skill",
                target_id="skill-selector-eval-backfill",
                department="EC",
                skill_id="skill-selector-eval-backfill",
                title=f"训练样本 {idx}",
                summary="用于验证 eval 预留未用完时不会减少总样本。",
                content_json={
                    "input": {"question": f"训练 {idx}"},
                    "output": {"summary": f"训练输出 {idx}"},
                    "source_type": "execution_trace",
                },
                labels_json=["training"],
                quality_score=0.86,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=now + timedelta(seconds=idx),
                updated_at=now + timedelta(seconds=idx),
            ))
        for idx in range(2):
            db.add(LearningArtifact(
                id=f"la-selector-eval-backfill-eval-{idx:02d}",
                event_id="le-training-selector-eval-backfill",
                artifact_kind="eval_case",
                artifact_hash=f"la-selector-eval-backfill-eval-{idx:02d}",
                target_type="skill",
                target_id="skill-selector-eval-backfill",
                department="EC",
                skill_id="skill-selector-eval-backfill",
                title=f"评测样本 {idx}",
                summary="用于验证 eval 样本不足时训练样本回填。",
                content_json={
                    "input": {"question": f"评测 {idx}"},
                    "output": {"summary": f"评测输出 {idx}"},
                    "expected_signal": "应该保留评测样本。",
                    "source_type": "decision_feedback",
                },
                labels_json=["eval"],
                quality_score=0.86,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=now + timedelta(minutes=1, seconds=idx),
                updated_at=now + timedelta(minutes=1, seconds=idx),
            ))
        job = TrainingJob(
            id="train_selector_eval_backfill",
            title="selector 评测回填训练",
            department="EC",
            created_by="learning_auto_flow",
            status="queued",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="skill-selector-eval-backfill",
            dataset_ref="learning-artifacts://platform/training/full-history/eval-backfill",
            spec_json={
                "dataset": {
                    "sample_total": 10,
                    "eval_count": 2,
                    "selector": {
                        "type": "learning_artifacts",
                        "target_skill_id": "skill-selector-eval-backfill",
                        "limit": 10,
                        "inline_limit": 10,
                        "inline_eval_limit": 4,
                    },
                }
            },
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        await db.flush()

        package = await _build_learning_gateway_dataset_package(db, job)

    assert package is not None
    assert package["sample_count"] == 10
    assert package["train_count"] == 8
    assert package["eval_count"] == 2
    assert package["samples"][-2]["id"] == "la-selector-eval-backfill-eval-00"
    assert package["samples"][-1]["id"] == "la-selector-eval-backfill-eval-01"


@pytest.mark.asyncio
async def test_training_gateway_dataset_selector_shrinks_large_package_by_budget(client, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training.models import TrainingJob
    from app.training import service as training_service

    now = now_bjt()
    async with async_session_factory() as db:
        db.add(LearningEvent(
            id="le-training-selector-budget",
            event_type="decision.completed",
            source_type="decision_log",
            source_id="decision-training-selector-budget",
            source_hash="decision-training-selector-budget",
            department="EC",
            skill_id="skill-selector-budget",
            redacted_summary="样本包预算缩减验证",
            quality_score=0.9,
            created_at=now,
            updated_at=now,
        ))
        for idx in range(40):
            db.add(LearningArtifact(
                id=f"la-selector-budget-{idx:02d}",
                event_id="le-training-selector-budget",
                artifact_kind="training_sample",
                artifact_hash=f"la-selector-budget-{idx:02d}",
                target_type="skill",
                target_id="skill-selector-budget",
                department="EC",
                skill_id="skill-selector-budget",
                title=f"预算样本 {idx}",
                summary="用于验证样本包会按预算缩减。",
                content_json={
                    "input": {"question": f"商品 {idx} 消耗异常原因", "body": "x" * 800},
                    "output": {"summary": f"样本 {idx} 的诊断输出", "body": "y" * 800},
                    "source_type": "execution_trace",
                },
                labels_json=["training"],
                quality_score=0.86,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=now + timedelta(seconds=idx),
                updated_at=now + timedelta(seconds=idx),
            ))
        job = TrainingJob(
            id="train_selector_budget",
            title="selector 样本包预算训练",
            department="EC",
            created_by="learning_auto_flow",
            status="queued",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="skill-selector-budget",
            dataset_ref="learning-artifacts://platform/training/full-history/budget",
            spec_json={"dataset": {"selector": {"type": "learning_artifacts", "limit": 40}}},
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        await db.flush()
        full_package = await training_service._build_learning_gateway_dataset_package(db, job)
        assert full_package is not None
        full_size = training_service._gateway_payload_size(full_package)

        monkeypatch.setattr(training_service, "TRAINING_GATEWAY_DATASET_PACKAGE_MAX_BYTES", max(5000, full_size // 3))
        shrunk_package = await training_service._build_learning_gateway_dataset_package(db, job)

    assert shrunk_package is not None
    assert 4 <= shrunk_package["sample_count"] < full_package["sample_count"]
    assert training_service._gateway_payload_size(shrunk_package) <= training_service.TRAINING_GATEWAY_DATASET_PACKAGE_MAX_BYTES


@pytest.mark.asyncio
async def test_training_gateway_payload_shrink_preserves_eval_samples(client, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training.models import TrainingJob
    from app.training import service as training_service

    now = now_bjt()
    async with async_session_factory() as db:
        db.add(LearningEvent(
            id="le-training-payload-eval-preserve",
            event_type="decision.completed",
            source_type="decision_log",
            source_id="decision-training-payload-eval-preserve",
            source_hash="decision-training-payload-eval-preserve",
            department="EC",
            skill_id="skill-payload-eval-preserve",
            redacted_summary="最终网关 payload 缩包也必须保留评测样本。",
            quality_score=0.9,
            created_at=now,
            updated_at=now,
        ))
        for idx in range(64):
            db.add(LearningArtifact(
                id=f"la-payload-eval-preserve-train-{idx:03d}",
                event_id="le-training-payload-eval-preserve",
                artifact_kind="training_sample",
                artifact_hash=f"la-payload-eval-preserve-train-{idx:03d}",
                target_type="skill",
                target_id="skill-payload-eval-preserve",
                department="EC",
                skill_id="skill-payload-eval-preserve",
                title=f"payload 训练样本 {idx}",
                summary="用于验证最终 payload 二次缩包不会截掉 eval。",
                content_json={
                    "input": {"question": f"商品 {idx} 消耗异常原因", "body": "x" * 900},
                    "output": {"summary": f"训练样本 {idx} 的诊断输出", "body": "y" * 900},
                    "source_type": "execution_trace",
                },
                labels_json=["training"],
                quality_score=0.86,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=now + timedelta(seconds=idx),
                updated_at=now + timedelta(seconds=idx),
            ))
        for idx in range(8):
            db.add(LearningArtifact(
                id=f"la-payload-eval-preserve-eval-{idx:03d}",
                event_id="le-training-payload-eval-preserve",
                artifact_kind="eval_case",
                artifact_hash=f"la-payload-eval-preserve-eval-{idx:03d}",
                target_type="skill",
                target_id="skill-payload-eval-preserve",
                department="EC",
                skill_id="skill-payload-eval-preserve",
                title=f"payload 评测样本 {idx}",
                summary="用于验证二次缩包保留 eval。",
                content_json={
                    "input": {"question": f"评测商品 {idx} 消耗异常原因", "body": "e" * 900},
                    "actual_output": {"summary": f"评测样本 {idx} 的模型输出", "body": "z" * 900},
                    "expected_signal": "应该指出同主题参照和转化承接差异。",
                    "source_type": "decision_feedback",
                },
                labels_json=["eval"],
                quality_score=0.86,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=now + timedelta(days=1, seconds=idx),
                updated_at=now + timedelta(days=1, seconds=idx),
            ))
        job = TrainingJob(
            id="train_payload_eval_preserve",
            title="payload 评测保留训练",
            department="EC",
            created_by="learning_auto_flow",
            status="queued",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="skill-payload-eval-preserve",
            dataset_ref="learning-artifacts://platform/training/full-history/payload-eval-preserve",
            spec_json={
                "dataset": {
                    "sample_total": 72,
                    "train_count": 64,
                    "eval_count": 8,
                    "selector": {
                        "type": "learning_artifacts",
                        "target_skill_id": "skill-payload-eval-preserve",
                        "limit": 72,
                        "inline_limit": 72,
                        "inline_eval_limit": 8,
                    },
                }
            },
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        await db.flush()
        full_payload = await training_service._build_gateway_payload_for_job(db, job)
        full_size = training_service._gateway_payload_size(full_payload)

        monkeypatch.setattr(training_service, "TRAINING_GATEWAY_PAYLOAD_MAX_BYTES", max(7000, full_size // 3))
        shrunk_payload = await training_service._build_gateway_payload_for_job(db, job)

    package = shrunk_payload["dataset_package"]
    assert 4 <= package["sample_count"] < full_payload["dataset_package"]["sample_count"]
    assert package["eval_count"] > 0
    assert package["samples"][-1]["split"] == "eval"
    assert training_service._gateway_payload_size(shrunk_payload) <= training_service.TRAINING_GATEWAY_PAYLOAD_MAX_BYTES


@pytest.mark.asyncio
async def test_training_gateway_dataset_selector_skips_oversized_inline_content(client):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training.models import TrainingJob
    from app.training.service import _build_learning_gateway_dataset_package

    now = now_bjt()
    async with async_session_factory() as db:
        db.add(LearningEvent(
            id="le-training-selector-oversized",
            event_type="decision.completed",
            source_type="decision_log",
            source_id="decision-training-selector-oversized",
            source_hash="decision-training-selector-oversized",
            department="EC",
            skill_id="skill-selector-oversized",
            redacted_summary="超大样本 inline 过滤验证",
            quality_score=0.9,
            created_at=now,
            updated_at=now,
        ))
        big_body = "|".join(f"token-{j}-{j * j}-{j % 97}" for j in range(4000))
        for idx in range(8):
            db.add(LearningArtifact(
                id=f"la-selector-oversized-{idx:02d}",
                event_id="le-training-selector-oversized",
                artifact_kind="training_sample",
                artifact_hash=f"la-selector-oversized-{idx:02d}",
                target_type="skill",
                target_id="skill-selector-oversized",
                department="EC",
                skill_id="skill-selector-oversized",
                title=f"超大过滤样本 {idx}",
                summary="用于验证超大 content_json 不进入 inline 训练包。",
                content_json={
                    "input": {"question": f"商品 {idx} 消耗异常原因", "body": big_body if idx < 2 else "x" * 20},
                    "output": {"summary": f"样本 {idx} 的诊断输出"},
                    "source_type": "execution_trace",
                },
                labels_json=["training"],
                quality_score=0.86,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=now + timedelta(seconds=idx),
                updated_at=now + timedelta(seconds=idx),
            ))
        job = TrainingJob(
            id="train_selector_oversized",
            title="selector 超大过滤训练",
            department="EC",
            created_by="learning_auto_flow",
            status="queued",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="skill-selector-oversized",
            dataset_ref="learning-artifacts://platform/training/full-history/oversized",
            spec_json={
                "dataset": {
                    "selector": {
                        "type": "learning_artifacts",
                        "limit": 8,
                        "inline_max_content_bytes": 1000,
                    }
                }
            },
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        await db.flush()
        package = await _build_learning_gateway_dataset_package(db, job)

    assert package is not None
    assert package["sample_count"] == 6
    assert {item["id"] for item in package["samples"]} == {
        "la-selector-oversized-02",
        "la-selector-oversized-03",
        "la-selector-oversized-04",
        "la-selector-oversized-05",
        "la-selector-oversized-06",
        "la-selector-oversized-07",
    }


def test_training_gateway_selector_datetime_strips_timezone():
    from app.training.service import _parse_optional_datetime

    parsed = _parse_optional_datetime("2026-06-24T18:10:13.568024+08:00")
    assert parsed is not None
    assert parsed.tzinfo is None
    assert parsed.isoformat() == "2026-06-24T18:10:13.568024"


@pytest.mark.asyncio
async def test_training_resource_bootstrap_env_marks_gateway_mixed_and_audits(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-bootstrap-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="runtime-gpu-bootstrap",
            name="EC 执行 Agent",
            department="EC",
            gateway_url="ws://runtime-gpu-bootstrap",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="skill_runtime",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": [
                    "training.submit_job",
                    "training.bootstrap_env",
                    "training.bootstrap_status",
                ],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora", "qlora", "eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
                "gpu": [{"name": "RTX 4060 Ti", "vram_total_mb": 8188, "vram_free_mb": 7652}],
            }),
            is_active=True,
        ))
        await db.commit()

    calls = []

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "runtime-gpu-bootstrap")

    async def fake_bootstrap_training_env(self, payload, *, timeout=1800):
        calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "status": "succeeded",
            "profile": payload["profile"],
            "cuda": payload["cuda"],
            "python": "/home/node/.skillforge_bridge/runtime/training_env/qlora_1b/venv/bin/python",
        }

    monkeypatch.setattr(AIClawClient, "bootstrap_training_env", fake_bootstrap_training_env)

    resp = await client.post(
        "/api/training/resources/runtime-gpu-bootstrap/bootstrap-env",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "profile": "qlora-1b",
            "cuda": "cu121",
            "timeout_seconds": 120,
            "agent_purpose": "mixed",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["bootstrap"]["status"] == "succeeded"
    assert body["agent_purpose_updated"] == "mixed"
    assert body["gateway"]["agent_purpose"] == "mixed"
    assert body["gateway"]["training"]["route_eligible"] is True
    assert calls == [{
        "instance_id": "runtime-gpu-bootstrap",
        "payload": {
            "profile": "qlora-1b",
            "cuda": "cu121",
            "force": False,
            "dry_run": False,
            "timeout_seconds": 120,
        },
        "timeout": 180,
    }]

    async with async_session_factory() as db:
        instance = await db.get(OpenClawInstance, "runtime-gpu-bootstrap")
        assert instance.agent_purpose == "mixed"
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == "runtime-gpu-bootstrap").order_by(AuditLog.id)
        )).scalars().all()
    assert actions == ["training_gateway.bootstrap_env"]


@pytest.mark.asyncio
async def test_training_resource_bootstrap_env_accepts_cli_token(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-bootstrap-dry-run-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="runtime-gpu-cli-bootstrap",
            name="EC CLI 训练 Agent",
            department="EC",
            gateway_url="ws://runtime-gpu-cli-bootstrap",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="skill_runtime",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.bootstrap_env", "training.bootstrap_status"],
                "training": {"gateway": True, "supported_tasks": ["qlora"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        await db.commit()

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "runtime-gpu-cli-bootstrap")

    async def fake_bootstrap_training_env(self, payload, *, timeout=1800):
        return {"status": "planned", "profile": payload["profile"], "cuda": payload["cuda"]}

    monkeypatch.setattr(AIClawClient, "bootstrap_training_env", fake_bootstrap_training_env)

    resp = await client.post(
        "/api/training/resources/runtime-gpu-cli-bootstrap/bootstrap-env",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "profile": "qlora-1b",
            "cuda": "cu121",
            "dry_run": True,
            "agent_purpose": "mixed",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["bootstrap"]["status"] == "planned"
    assert body["gateway"]["agent_purpose"] == "skill_runtime"


@pytest.mark.asyncio
async def test_training_resource_prepare_model_accepts_cli_token_and_audits(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-prepare-model-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="runtime-gpu-model",
            name="EC 模型 Agent",
            department="EC",
            gateway_url="ws://runtime-gpu-model",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.prepare_model", "training.model_status", "training.bootstrap_status"],
                "training": {"gateway": True, "supported_tasks": ["qlora", "inference"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        await db.commit()

    calls = []
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "runtime-gpu-model")

    async def fake_prepare_training_model(self, payload, *, timeout=7200):
        calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "status": "planned",
            "profile": payload["profile"],
            "model_id": "Qwen/Qwen3.5-4B",
            "validate_mode": payload["validate_mode"],
        }

    monkeypatch.setattr(AIClawClient, "prepare_training_model", fake_prepare_training_model)

    resp = await client.post(
        "/api/training/resources/runtime-gpu-model/model",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "profile": "qwen3.5-4b",
            "revision": "main",
            "modelscope_revision": "master",
            "bootstrap_profile": "qlora-1b",
            "validate_mode": "metadata",
            "source": "modelscope",
            "dry_run": True,
            "timeout_seconds": 120,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["model"]["status"] == "planned"
    assert body["model"]["model_id"] == "Qwen/Qwen3.5-4B"
    assert calls == [{
        "instance_id": "runtime-gpu-model",
        "payload": {
            "profile": "qwen3.5-4b",
            "revision": "main",
            "modelscope_revision": "master",
            "bootstrap_profile": "qlora-1b",
            "force": False,
            "dry_run": True,
            "timeout_seconds": 120,
            "validate_mode": "metadata",
            "source": "modelscope",
            "auto_discover": False,
        },
        "timeout": 180,
    }]

    async with async_session_factory() as db:
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == "runtime-gpu-model").order_by(AuditLog.id)
        )).scalars().all()
    assert actions == ["training_gateway.prepare_model"]


@pytest.mark.asyncio
async def test_training_resource_runtime_config_sets_qwen36_model_dir(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-runtime-config-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="runtime-gpu-config",
            name="EC Runtime Config Agent",
            department="EC",
            gateway_url="ws://runtime-gpu-config",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.configure_runtime", "training.model_status"],
                "training": {"gateway": True, "supported_tasks": ["qlora", "inference"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        await db.commit()

    calls = []
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "runtime-gpu-config")

    async def fake_configure_training_runtime(self, payload, *, timeout=30):
        calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "configured": True,
            "updated_keys": ["SKILLFORGE_QWEN36_35B_MODEL_DIR"],
            "env": {"SKILLFORGE_QWEN36_35B_MODEL_DIR": "/mnt/models/qwen36"},
        }

    async def fake_get_training_model_status(self, payload, *, timeout=30):
        return {
            "status": "not_downloaded",
            "profile": payload["profile"],
            "source_exists": True,
            "source_path": "/mnt/models/qwen36",
            "exists": False,
        }

    monkeypatch.setattr(AIClawClient, "configure_training_runtime", fake_configure_training_runtime)
    monkeypatch.setattr(AIClawClient, "get_training_model_status", fake_get_training_model_status)

    resp = await client.post(
        "/api/training/resources/runtime-gpu-config/runtime-config",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"qwen36_35b_model_dir": "/mnt/models/qwen36"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["runtime"]["configured"] is True
    assert body["model"]["source_exists"] is True
    assert calls == [{
        "instance_id": "runtime-gpu-config",
        "payload": {"env": {"SKILLFORGE_QWEN36_35B_MODEL_DIR": "/mnt/models/qwen36"}},
        "timeout": 30,
    }]
    async with async_session_factory() as db:
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == "runtime-gpu-config").order_by(AuditLog.id)
        )).scalars().all()
    assert actions == ["training_gateway.configure_runtime"]


@pytest.mark.asyncio
async def test_training_resource_model_discover_and_openwebui_status(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-discover-openwebui-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="runtime-gpu-observe",
            name="EC Runtime Observe Agent",
            department="EC",
            gateway_url="ws://runtime-gpu-observe",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.discover_models", "training.explore_model_paths", "training.openwebui_status"],
                "training": {"gateway": True, "supported_tasks": ["qlora", "inference"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        await db.commit()

    calls = []
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "runtime-gpu-observe")

    async def fake_discover_training_models(self, payload, *, timeout=60):
        calls.append(("discover", self.instance_id, payload, timeout))
        return {
            "profile": payload["profile"],
            "source": payload["source"],
            "scan_summary": {"candidate_count": 1},
            "candidates": [{"path": "/mnt/models/qwen36", "has_config": True, "has_weights": True}],
        }

    async def fake_get_openwebui_status(self, payload=None, *, timeout=30):
        calls.append(("openwebui_status", self.instance_id, payload or {}, timeout))
        return {
            "enabled": True,
            "base_url": "http://127.0.0.1:18080/v1",
            "model_count": 1,
            "models": [{"id": "skillforge-ft-qwen36"}],
            "server_reachable": True,
        }

    async def fake_explore_training_model_paths(self, payload=None, *, timeout=120):
        calls.append(("explore", self.instance_id, payload or {}, timeout))
        return {
            "profile": (payload or {}).get("profile"),
            "source": (payload or {}).get("source"),
            "read_policy": {"mode": "controlled_read_only", "remote_shell": False},
            "scan_summary": {"candidate_count": 1},
            "candidates": [{"path": "/mnt/models/qwen36", "score": 100, "has_config": True, "has_weights": True}],
        }

    monkeypatch.setattr(AIClawClient, "discover_training_models", fake_discover_training_models)
    monkeypatch.setattr(AIClawClient, "explore_training_model_paths", fake_explore_training_model_paths)
    monkeypatch.setattr(AIClawClient, "get_openwebui_status", fake_get_openwebui_status)

    discover_resp = await client.post(
        "/api/training/resources/runtime-gpu-observe/models/discover",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "auto_discover": True,
        },
    )
    assert discover_resp.status_code == 200, discover_resp.text
    discover_body = discover_resp.json()
    assert discover_body["discover"]["scan_summary"]["candidate_count"] == 1
    assert discover_body["discover"]["candidates"][0]["path"] == "/mnt/models/qwen36"

    openwebui_resp = await client.get(
        "/api/training/resources/runtime-gpu-observe/openwebui-status",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert openwebui_resp.status_code == 200, openwebui_resp.text
    assert openwebui_resp.json()["openwebui"]["models"][0]["id"] == "skillforge-ft-qwen36"
    assert calls[0] == (
        "discover",
        "runtime-gpu-observe",
        {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "revision": "main",
            "modelscope_revision": "master",
            "bootstrap_profile": "qlora-1b",
            "source": "internal",
            "auto_discover": True,
            "internal_model_ref": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        },
        60,
    )
    assert calls[1] == ("openwebui_status", "runtime-gpu-observe", {}, 30)

    discover_all_resp = await client.post(
        "/api/training/models/discover-all",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "training_gateway_id": "runtime-gpu-observe",
            "gateway_ids": ["runtime-gpu-observe"],
            "roots": ["/mnt/models"],
            "include_metadata": True,
        },
    )
    assert discover_all_resp.status_code == 200, discover_all_resp.text
    discover_all = discover_all_resp.json()
    assert discover_all["summary"]["train_ready_count"] == 1
    assert discover_all["train_ready_candidates"][0]["path"] == "/mnt/models/qwen36"
    assert calls[2][0] == "explore"
    assert calls[2][2]["roots"] == ["/mnt/models"]
    assert calls[2][2]["include_metadata"] is True


@pytest.mark.asyncio
async def test_training_resource_openwebui_base_model_registers_prepared_model(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-openwebui-base-model-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="runtime-gpu-openwebui-base",
            name="EC Runtime OpenWebUI Base Agent",
            department="EC",
            gateway_url="ws://runtime-gpu-openwebui-base",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": [
                    "training.model_status",
                    "training.register_openwebui_model",
                    "training.openwebui_status",
                ],
                "training": {"gateway": True, "supported_tasks": ["inference"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        await db.commit()

    calls = []
    profile = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    model_id = f"skillforge-base-{profile}"
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "runtime-gpu-openwebui-base")

    async def fake_get_training_model_status(self, payload, *, timeout=30):
        calls.append(("model_status", self.instance_id, payload, timeout))
        return {
            "status": "succeeded",
            "profile": payload["profile"],
            "source": payload["source"],
            "exists": True,
            "model_dir": "/Users/skillforge/.skillforge_bridge/state/training_models/qwen36/model",
        }

    async def fake_register_openwebui_model(self, payload, *, timeout=30):
        calls.append(("register", self.instance_id, payload, timeout))
        return {
            "registered": True,
            "model_id": payload["model_id"],
            "aliases": payload["aliases"],
            "base_url": "http://127.0.0.1:18080/v1",
        }

    async def fake_get_openwebui_status(self, payload=None, *, timeout=30):
        calls.append(("openwebui_status", self.instance_id, payload or {}, timeout))
        return {
            "enabled": True,
            "server_reachable": True,
            "registry_exists": True,
            "model_count": 2,
            "models": [{"id": model_id}, {"id": profile, "alias_of": model_id}],
        }

    monkeypatch.setattr(AIClawClient, "get_training_model_status", fake_get_training_model_status)
    monkeypatch.setattr(AIClawClient, "register_openwebui_model", fake_register_openwebui_model)
    monkeypatch.setattr(AIClawClient, "get_openwebui_status", fake_get_openwebui_status)

    resp = await client.post(
        "/api/training/resources/runtime-gpu-openwebui-base/openwebui-base-model",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model"]["exists"] is True
    assert body["registration"]["registered"] is True
    assert body["registration"]["model_id"] == model_id
    assert profile in body["registration"]["aliases"]
    assert body["openwebui"]["registry_exists"] is True
    assert calls == [
        (
            "model_status",
            "runtime-gpu-openwebui-base",
            {
                "profile": profile,
                "bootstrap_profile": "qlora-1b",
                "source": "internal",
                "internal_model_ref": profile,
            },
            30,
        ),
        (
            "register",
            "runtime-gpu-openwebui-base",
            {
                "model_id": model_id,
                "aliases": [profile, f"skillforge-{profile}"],
                "profile": profile,
                "runtime_profile": "mlx-qwen3.6-35b-a3b-lora",
                "base_model_only": True,
                "display_name": f"SkillForge Base {profile}",
                "source_type": "skillforge_base_model",
            },
            30,
        ),
        ("openwebui_status", "runtime-gpu-openwebui-base", {}, 30),
    ]

    async with async_session_factory() as db:
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == "runtime-gpu-openwebui-base").order_by(AuditLog.id)
        )).scalars().all()
    assert actions == ["training_gateway.openwebui_register_base_model"]


@pytest.mark.asyncio
async def test_training_resource_openwebui_chat_test_delegates_to_bridge(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-openwebui-chat-test-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="runtime-gpu-openwebui-chat",
            name="EC Runtime OpenWebUI Chat Agent",
            department="EC",
            gateway_url="ws://runtime-gpu-openwebui-chat",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.openwebui_chat_test"],
                "training": {"gateway": True, "supported_tasks": ["inference"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        await db.commit()

    calls = []
    profile = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    model_id = f"skillforge-base-{profile}"
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "runtime-gpu-openwebui-chat")

    async def fake_test_openwebui_chat(self, payload, *, timeout=600):
        calls.append((self.instance_id, payload, timeout))
        return {
            "status": "succeeded",
            "ok": True,
            "model": payload["model_id"],
            "text": "OpenWebUI chat works",
            "base_url": "http://127.0.0.1:18080/v1",
        }

    monkeypatch.setattr(AIClawClient, "test_openwebui_chat", fake_test_openwebui_chat)

    resp = await client.post(
        "/api/training/resources/runtime-gpu-openwebui-chat/openwebui-chat/test",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "model_id": model_id,
            "prompt": "ping",
            "max_tokens": 8,
            "timeout_seconds": 60,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model"]["model_id"] == model_id
    assert body["result"]["ok"] is True
    assert body["result"]["text"] == "OpenWebUI chat works"
    assert calls == [(
        "runtime-gpu-openwebui-chat",
        {
            "model_id": model_id,
            "prompt": "ping",
            "max_tokens": 8,
            "timeout_seconds": 60,
        },
        120,
    )]

    async with async_session_factory() as db:
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == "runtime-gpu-openwebui-chat").order_by(AuditLog.id)
        )).scalars().all()
    assert actions == ["training_gateway.openwebui_chat_test"]


@pytest.mark.asyncio
async def test_training_resource_openwebui_proxy_models_and_chat(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-openwebui-proxy-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="runtime-gpu-openwebui-proxy",
            name="EC Runtime OpenWebUI Proxy Agent",
            department="EC",
            gateway_url="ws://runtime-gpu-openwebui-proxy",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.openwebui_status", "training.openwebui_chat_test"],
                "training": {"gateway": True, "supported_tasks": ["inference"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        await db.commit()

    profile = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    model_id = f"skillforge-base-{profile}"
    calls = []
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "runtime-gpu-openwebui-proxy")

    async def fake_get_openwebui_status(self, payload=None, *, timeout=30):
        calls.append(("status", self.instance_id, payload or {}, timeout))
        return {
            "enabled": True,
            "server_reachable": True,
            "base_url": "http://192.0.2.11:18080/v1",
            "model_count": 2,
            "models": [{"id": model_id}, {"id": profile, "alias_of": model_id}],
        }

    async def fake_test_openwebui_chat(self, payload, *, timeout=600):
        calls.append(("chat", self.instance_id, payload, timeout))
        return {
            "status": "succeeded",
            "ok": True,
            "model": payload["model_id"],
            "text": "5",
            "response_id": "chatcmpl-test",
            "finish_reason": "stop",
        }

    monkeypatch.setattr(AIClawClient, "get_openwebui_status", fake_get_openwebui_status)
    monkeypatch.setattr(AIClawClient, "test_openwebui_chat", fake_test_openwebui_chat)

    models_resp = await client.get(
        "/api/training/resources/runtime-gpu-openwebui-proxy/openwebui-proxy/v1/models",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert models_resp.status_code == 200, models_resp.text
    assert [item["id"] for item in models_resp.json()["data"]] == [model_id, profile]

    chat_resp = await client.post(
        "/api/training/resources/runtime-gpu-openwebui-proxy/openwebui-proxy/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "model": profile,
            "messages": [{"role": "user", "content": "2+3=?"}],
            "max_tokens": 16,
        },
    )
    assert chat_resp.status_code == 200, chat_resp.text
    chat = chat_resp.json()
    assert chat["object"] == "chat.completion"
    assert chat["model"] == profile
    assert chat["choices"][0]["message"]["content"] == "5"

    stream_resp = await client.post(
        "/api/training/resources/runtime-gpu-openwebui-proxy/openwebui-proxy/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "model": profile,
            "messages": [{"role": "user", "content": "2+3=?"}],
            "stream": True,
        },
    )
    assert stream_resp.status_code == 200, stream_resp.text
    assert "text/event-stream" in stream_resp.headers["content-type"]
    assert "data: [DONE]" in stream_resp.text
    assert calls[0] == ("status", "runtime-gpu-openwebui-proxy", {}, 30)
    assert calls[1][0] == "chat"
    assert calls[1][2]["model_id"] == profile


@pytest.mark.asyncio
async def test_training_model_platform_relay_retries_transient_disconnects(monkeypatch):
    from app.training import service as training_service

    monkeypatch.setattr(training_service, "TRAINING_MODEL_TRANSFER_RELAY_RETRY_DELAYS", (0, 0))

    manifest = {
        "profile": "qwen3.5-4b",
        "model_id": "Qwen/Qwen3.5-4B",
        "manifest_sha256": "a" * 64,
        "total_size_bytes": 5,
        "files": [{"path": "model.safetensors", "size_bytes": 5}],
    }

    class SourceClient:
        def __init__(self):
            self.chunk_calls = 0

        async def export_training_model_manifest(self, payload, *, timeout=21600):
            return manifest

        async def export_training_model_file_chunk(self, payload, *, timeout=600):
            self.chunk_calls += 1
            if self.chunk_calls == 1:
                raise RuntimeError("disconnected")
            return {
                "status": "ok",
                "file_path": payload["file_path"],
                "offset": payload["offset"],
                "size_bytes": 5,
                "content_base64": base64.b64encode(b"model").decode("ascii"),
                "eof": True,
            }

    class TargetClient:
        def __init__(self):
            self.import_calls = 0

        async def get_training_model_relay_file_status(self, payload, *, timeout=30):
            return {"status": "ok", "exists": False, "size_bytes": 0}

        async def import_training_model_relay_chunk(self, payload, *, timeout=600):
            self.import_calls += 1
            if self.import_calls == 1:
                raise RuntimeError("connection closed")
            return {"status": "ok", "written_size_bytes": 5}

        async def commit_training_model_relay_import(self, payload, *, timeout=21600):
            return {
                "status": "succeeded",
                "internal_model_ref": "/tmp/imported-model",
                "model_dir": "/tmp/imported-model",
            }

    source = SourceClient()
    target = TargetClient()
    events = []

    result = await training_service._relay_training_model_via_platform(
        source_client=source,
        target_client=target,
        normalized={
            "profile": "qwen3.5-4b",
            "hash_files": False,
            "timeout_seconds": 21600,
        },
        source_gateway_id="source-1",
        source_path="/models/qwen35",
        progress_cb=lambda event: events.append(event),
    )

    assert result["status"] == "succeeded"
    assert result["internal_model_ref"] == "/tmp/imported-model"
    assert result["relay"] is True
    assert source.chunk_calls == 2
    assert target.import_calls == 2
    assert any(event.get("current_file") == "model.safetensors" for event in events)


@pytest.mark.asyncio
async def test_training_model_platform_relay_resumes_existing_target_files(monkeypatch):
    from app.training import service as training_service

    manifest = {
        "profile": "qwen3.5-4b",
        "model_id": "Qwen/Qwen3.5-4B",
        "manifest_sha256": "c" * 64,
        "total_size_bytes": 9,
        "files": [
            {"path": "complete.safetensors", "size_bytes": 4},
            {"path": "partial.safetensors", "size_bytes": 5},
        ],
    }

    class SourceClient:
        def __init__(self):
            self.read_offsets = []

        async def export_training_model_manifest(self, payload, *, timeout=21600):
            return manifest

        async def export_training_model_file_chunk(self, payload, *, timeout=600):
            self.read_offsets.append((payload["file_path"], payload["offset"]))
            assert payload["file_path"] == "partial.safetensors"
            assert payload["offset"] == 2
            return {
                "status": "ok",
                "file_path": payload["file_path"],
                "offset": payload["offset"],
                "size_bytes": 3,
                "content_base64": base64.b64encode(b"ial").decode("ascii"),
                "eof": True,
            }

    class TargetClient:
        def __init__(self):
            self.status_calls = []
            self.write_offsets = []

        async def get_training_model_relay_file_status(self, payload, *, timeout=30):
            self.status_calls.append(payload["file_path"])
            if payload["file_path"] == "complete.safetensors":
                return {"status": "ok", "exists": True, "size_bytes": 4}
            return {"status": "ok", "exists": True, "size_bytes": 2}

        async def import_training_model_relay_chunk(self, payload, *, timeout=600):
            self.write_offsets.append((payload["file_path"], payload["offset"]))
            return {"status": "ok", "written_size_bytes": 5}

        async def commit_training_model_relay_import(self, payload, *, timeout=21600):
            return {
                "status": "succeeded",
                "internal_model_ref": "/tmp/imported-model",
                "model_dir": "/tmp/imported-model",
            }

    source = SourceClient()
    target = TargetClient()

    result = await training_service._relay_training_model_via_platform(
        source_client=source,
        target_client=target,
        normalized={
            "profile": "qwen3.5-4b",
            "hash_files": False,
            "timeout_seconds": 21600,
            "resume_manifest_sha256": "c" * 64,
            "target_supports_relay_file_status": True,
        },
        source_gateway_id="source-1",
        source_path="/models/qwen35",
        progress_cb=lambda event: None,
    )

    assert result["status"] == "succeeded"
    assert target.status_calls == ["complete.safetensors", "partial.safetensors"]
    assert source.read_offsets == [("partial.safetensors", 2)]
    assert target.write_offsets == [("partial.safetensors", 2)]
    assert result["transferred_bytes"] == 9
    assert result["imported_files"] == 2


@pytest.mark.asyncio
async def test_training_model_platform_relay_resumes_from_recorded_bytes_without_file_status(monkeypatch):
    from app.training import service as training_service

    manifest = {
        "profile": "qwen3.5-4b",
        "model_id": "Qwen/Qwen3.5-4B",
        "manifest_sha256": "d" * 64,
        "total_size_bytes": 9,
        "files": [
            {"path": "complete.safetensors", "size_bytes": 4},
            {"path": "partial.safetensors", "size_bytes": 5},
        ],
    }

    class SourceClient:
        def __init__(self):
            self.read_offsets = []

        async def export_training_model_manifest(self, payload, *, timeout=21600):
            return manifest

        async def export_training_model_file_chunk(self, payload, *, timeout=600):
            self.read_offsets.append((payload["file_path"], payload["offset"]))
            assert payload["file_path"] == "partial.safetensors"
            assert payload["offset"] == 2
            return {
                "status": "ok",
                "file_path": payload["file_path"],
                "offset": payload["offset"],
                "size_bytes": 3,
                "content_base64": base64.b64encode(b"ial").decode("ascii"),
                "eof": True,
            }

    class TargetClient:
        def __init__(self):
            self.write_offsets = []

        async def import_training_model_relay_chunk(self, payload, *, timeout=600):
            self.write_offsets.append((payload["file_path"], payload["offset"]))
            return {"status": "ok", "written_size_bytes": 5}

        async def commit_training_model_relay_import(self, payload, *, timeout=21600):
            return {
                "status": "succeeded",
                "internal_model_ref": "/tmp/imported-model",
                "model_dir": "/tmp/imported-model",
            }

    source = SourceClient()
    target = TargetClient()

    result = await training_service._relay_training_model_via_platform(
        source_client=source,
        target_client=target,
        normalized={
            "profile": "qwen3.5-4b",
            "hash_files": False,
            "timeout_seconds": 21600,
            "resume_manifest_sha256": "d" * 64,
            "resume_transferred_bytes": 6,
            "target_supports_relay_file_status": False,
        },
        source_gateway_id="source-1",
        source_path="/models/qwen35",
        progress_cb=lambda event: None,
    )

    assert result["status"] == "succeeded"
    assert source.read_offsets == [("partial.safetensors", 2)]
    assert target.write_offsets == [("partial.safetensors", 2)]
    assert result["transferred_bytes"] == 9
    assert result["imported_files"] == 2


@pytest.mark.asyncio
async def test_training_model_transfer_to_training_gateway_orchestrates_bridge_ops(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-model-transfer-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add_all([
            OpenClawInstance(
                id="data-primary-transfer-test",
                name="AI 235 · RTX PRO 6000",
                department="AI小组",
                gateway_url="ws://data-primary-transfer-test",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                bridge_gateway_kind="bridge",
                bridge_capabilities_json=json.dumps({
                    "ops": ["training.explore_model_paths", "training.model_export_start"],
                    "training": {"gateway": True, "supported_tasks": ["inference"], "gpu_count": 1},
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="training-primary-transfer-test",
                name="GB10 237 · NVIDIA GB10",
                department="AI小组",
                gateway_url="ws://training-primary-transfer-test",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="training",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": [
                        "training.model_import_from_export",
                        "training.configure_runtime",
                        "training.prepare_model",
                    ],
                    "training": {"gateway": True, "supported_tasks": ["qlora"], "gpu_count": 1},
                }),
                is_active=True,
            ),
        ])
        await db.commit()

    monkeypatch.setattr(
        bridge_registry,
        "is_online",
        lambda instance_id: instance_id in {"data-primary-transfer-test", "training-primary-transfer-test"},
    )
    calls = []

    async def fake_start_training_model_export(self, payload, *, timeout=3600):
        calls.append(("export", self.instance_id, payload, timeout))
        return {
            "status": "ready",
            "session_id": "mx-test",
            "profile": payload["profile"],
            "source_path": payload["source_path"],
            "file_count": 3,
            "total_size_bytes": 123,
            "manifest_sha256": "a" * 64,
            "export_url": "http://192.0.2.10:39123/training-model-export/mx-test",
            "manifest_url": "http://192.0.2.10:39123/training-model-export/mx-test/manifest",
            "token": "temporary-transfer-token",
        }

    async def fake_import_training_model_from_export(self, payload, *, timeout=21600):
        calls.append(("import", self.instance_id, payload, timeout))
        assert payload["token"] == "temporary-transfer-token"
        return {
            "status": "succeeded",
            "profile": payload["profile"],
            "source_gateway_id": payload["source_gateway_id"],
            "source_path": payload["source_path"],
            "internal_model_ref": "/home/node/.skillforge_bridge/state/training_model_imports/qwen36/a",
        }

    async def fake_configure_training_runtime(self, payload, *, timeout=30):
        calls.append(("configure", self.instance_id, payload, timeout))
        return {"configured": True, "env": payload}

    async def fake_prepare_training_model(self, payload, *, timeout=7200):
        calls.append(("prepare", self.instance_id, payload, timeout))
        return {
            "status": "succeeded",
            "exists": True,
            "profile": payload["profile"],
            "source_path": payload["internal_model_ref"],
            "model_dir": "/home/node/.skillforge_bridge/state/training_models/qwen36/model",
        }

    monkeypatch.setattr(AIClawClient, "start_training_model_export", fake_start_training_model_export)
    monkeypatch.setattr(AIClawClient, "import_training_model_from_export", fake_import_training_model_from_export)
    monkeypatch.setattr(AIClawClient, "configure_training_runtime", fake_configure_training_runtime)
    monkeypatch.setattr(AIClawClient, "prepare_training_model", fake_prepare_training_model)

    resp = await client.post(
        "/api/training/models/transfer-to-training",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "training_gateway_id": "training-primary-transfer-test",
            "source_gateway_id": "data-primary-transfer-test",
            "source_path": "/srv/ai/models/hf/hub/models--Qwen--Qwen3.6-35B-A3B-FP8/snapshots/main",
            "validate_mode": "metadata",
            "run_inline": True,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "transferred"
    assert body["source_gateway_id"] == "data-primary-transfer-test"
    assert body["training_gateway_id"] == "training-primary-transfer-test"
    assert body["internal_model_ref"].endswith("/training_model_imports/qwen36/a")
    assert "token" not in body["export"]
    assert [item[0] for item in calls] == ["export", "import", "configure", "prepare"]
    assert calls[0][1] == "data-primary-transfer-test"
    assert calls[1][1] == "training-primary-transfer-test"
    assert calls[2][2]["env"]["SKILLFORGE_QWEN36_35B_MODEL_DIR"] == body["internal_model_ref"]
    assert calls[3][2]["internal_model_ref"] == body["internal_model_ref"]


@pytest.mark.asyncio
async def test_training_model_transfer_fails_when_target_prepare_fails(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-model-transfer-prepare-failed-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add_all([
            OpenClawInstance(
                id="data-primary-transfer-prepare-fail",
                name="AI 235 · RTX PRO 6000",
                department="AI小组",
                gateway_url="ws://data-primary-transfer-prepare-fail",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                bridge_gateway_kind="bridge",
                bridge_capabilities_json=json.dumps({
                    "ops": ["training.model_export_start"],
                    "training": {"gateway": True, "supported_tasks": ["inference"], "gpu_count": 1},
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="inference-primary-transfer-prepare-fail",
                name="Mac 236 · M3 Ultra",
                department="AI小组",
                gateway_url="ws://inference-primary-transfer-prepare-fail",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="deployment",
                bridge_gateway_kind="bridge",
                bridge_capabilities_json=json.dumps({
                    "ops": [
                        "training.model_import_from_export",
                        "training.configure_runtime",
                        "training.prepare_model",
                    ],
                    "training": {"gateway": True, "supported_tasks": ["inference"], "gpu_count": 0},
                }),
                is_active=True,
            ),
        ])
        await db.commit()

    monkeypatch.setattr(
        bridge_registry,
        "is_online",
        lambda instance_id: instance_id in {
            "data-primary-transfer-prepare-fail",
            "inference-primary-transfer-prepare-fail",
        },
    )

    async def fake_start_training_model_export(self, payload, *, timeout=3600):
        return {
            "status": "ready",
            "session_id": "mx-test",
            "profile": payload["profile"],
            "source_path": payload["source_path"],
            "file_count": 1,
            "total_size_bytes": 123,
            "manifest_sha256": "a" * 64,
            "export_url": "http://192.0.2.10:39123/training-model-export/mx-test",
            "manifest_url": "http://192.0.2.10:39123/training-model-export/mx-test/manifest",
            "token": "temporary-transfer-token",
        }

    async def fake_import_training_model_from_export(self, payload, *, timeout=21600):
        return {
            "status": "succeeded",
            "profile": payload["profile"],
            "source_gateway_id": payload["source_gateway_id"],
            "source_path": payload["source_path"],
            "internal_model_ref": "/Users/skillforge/.skillforge_bridge/state/training_model_imports/qwen36/a",
        }

    async def fake_configure_training_runtime(self, payload, *, timeout=30):
        return {"configured": True, "env": payload}

    async def fake_prepare_training_model(self, payload, *, timeout=7200):
        return {
            "status": "failed",
            "exists": False,
            "error": "training environment is not installed",
            "model_dir": "/Users/skillforge/.skillforge_bridge/state/training_models/qwen36/model",
        }

    monkeypatch.setattr(AIClawClient, "start_training_model_export", fake_start_training_model_export)
    monkeypatch.setattr(AIClawClient, "import_training_model_from_export", fake_import_training_model_from_export)
    monkeypatch.setattr(AIClawClient, "configure_training_runtime", fake_configure_training_runtime)
    monkeypatch.setattr(AIClawClient, "prepare_training_model", fake_prepare_training_model)

    resp = await client.post(
        "/api/training/models/transfer-to-training",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "training_gateway_id": "inference-primary-transfer-prepare-fail",
            "source_gateway_id": "data-primary-transfer-prepare-fail",
            "source_path": "/srv/ai/models/qwen36",
            "validate_mode": "metadata",
            "run_inline": True,
        },
    )

    assert resp.status_code == 502, resp.text
    assert resp.json()["error"]["code"] == "TRAINING_MODEL_TRANSFER_PREPARE_FAILED"


@pytest.mark.asyncio
async def test_training_model_transfer_defaults_to_durable_task(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingModelTransfer

    raw_token = "test-training-model-transfer-task-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add_all([
            OpenClawInstance(
                id="data-primary-transfer-task-test",
                name="AI 235 · RTX PRO 6000",
                department="AI小组",
                gateway_url="ws://data-primary-transfer-task-test",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                bridge_gateway_kind="bridge",
                bridge_capabilities_json=json.dumps({
                    "ops": ["training.model_export_start", "training.model_export_manifest", "training.model_export_file_chunk"],
                    "training": {"gateway": True, "supported_tasks": ["inference"], "gpu_count": 1},
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="training-primary-transfer-task-test",
                name="GB10 237 · NVIDIA GB10",
                department="AI小组",
                gateway_url="ws://training-primary-transfer-task-test",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="training",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": ["training.model_import_from_export", "training.model_import_relay_chunk", "training.model_import_relay_commit"],
                    "training": {"gateway": True, "supported_tasks": ["qlora"], "gpu_count": 1},
                }),
                is_active=True,
            ),
        ])
        await db.commit()

    monkeypatch.setattr(
        bridge_registry,
        "is_online",
        lambda instance_id: instance_id in {
            "data-primary-transfer-task-test",
            "training-primary-transfer-task-test",
        },
    )
    kicked: list[str] = []
    monkeypatch.setattr("app.training.router.kickoff_training_model_transfer", lambda transfer_id: kicked.append(transfer_id) or True)

    resp = await client.post(
        "/api/training/models/transfer-to-training",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "training_gateway_id": "training-primary-transfer-task-test",
            "source_gateway_id": "data-primary-transfer-task-test",
            "source_path": "/srv/ai/models/hf/hub/models--Qwen--Qwen3.6-35B-A3B-FP8/snapshots/main",
            "validate_mode": "metadata",
            "hash_files": False,
        },
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["transfer_id"].startswith("tmx_")
    assert body["source_gateway_id"] == "data-primary-transfer-task-test"
    assert body["training_gateway_id"] == "training-primary-transfer-task-test"
    assert kicked == [body["transfer_id"]]

    status_resp = await client.get(
        f"/api/training/model-transfers/{body['transfer_id']}",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert status_resp.status_code == 200, status_resp.text
    assert status_resp.json()["status"] == "queued"

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelTransfer, body["transfer_id"])
        assert stored is not None
        assert stored.status == "queued"
        assert stored.hash_files is False


@pytest.mark.asyncio
async def test_training_model_transfer_falls_back_to_platform_relay(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-model-transfer-relay-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add_all([
            OpenClawInstance(
                id="data-primary-relay-test",
                name="AI 235 · RTX PRO 6000",
                department="AI小组",
                gateway_url="ws://data-primary-relay-test",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                bridge_gateway_kind="bridge",
                bridge_capabilities_json=json.dumps({
                    "ops": [
                        "training.model_export_start",
                        "training.model_export_manifest",
                        "training.model_export_file_chunk",
                    ],
                    "training": {"gateway": True, "supported_tasks": ["inference"], "gpu_count": 1},
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="training-primary-relay-test",
                name="GB10 237 · NVIDIA GB10",
                department="AI小组",
                gateway_url="ws://training-primary-relay-test",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="training",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": [
                        "training.model_import_from_export",
                        "training.model_import_relay_chunk",
                        "training.model_import_relay_commit",
                        "training.configure_runtime",
                        "training.prepare_model",
                    ],
                    "training": {"gateway": True, "supported_tasks": ["qlora"], "gpu_count": 1},
                }),
                is_active=True,
            ),
        ])
        await db.commit()

    monkeypatch.setattr(
        bridge_registry,
        "is_online",
        lambda instance_id: instance_id in {"data-primary-relay-test", "training-primary-relay-test"},
    )
    calls = []
    file_bytes = b"abcdef"

    async def fake_start_training_model_export(self, payload, *, timeout=3600):
        calls.append(("export", self.instance_id))
        return {
            "status": "ready",
            "session_id": "mx-relay",
            "manifest_sha256": "b" * 64,
            "export_url": "http://192.0.2.10:39123/training-model-export/mx-relay",
            "manifest_url": "http://192.0.2.10:39123/training-model-export/mx-relay/manifest",
            "token": "relay-token",
        }

    async def fake_import_training_model_from_export(self, payload, *, timeout=21600):
        calls.append(("direct_import", self.instance_id))
        raise AppError("BRIDGE_OP_ERROR", 502, {"detail": {"message": "<urlopen error [Errno 113] No route to host>"}})

    async def fake_export_training_model_manifest(self, payload, *, timeout=3600):
        calls.append(("relay_manifest", self.instance_id))
        return {
            "status": "ready",
            "profile": payload["profile"],
            "manifest_sha256": "b" * 64,
            "total_size_bytes": len(file_bytes),
            "files": [{"path": "model.safetensors", "size_bytes": len(file_bytes), "sha256": hashlib.sha256(file_bytes).hexdigest()}],
        }

    async def fake_export_training_model_file_chunk(self, payload, *, timeout=120):
        calls.append(("relay_read", self.instance_id, payload["offset"]))
        offset = payload["offset"]
        raw = file_bytes[offset: offset + 3]
        return {
            "status": "ok",
            "file_path": payload["file_path"],
            "offset": offset,
            "size_bytes": len(raw),
            "eof": offset + len(raw) >= len(file_bytes),
            "content_base64": base64.b64encode(raw).decode("ascii"),
        }

    async def fake_import_training_model_relay_chunk(self, payload, *, timeout=120):
        calls.append(("relay_write", self.instance_id, payload["offset"]))
        return {"status": "ok"}

    async def fake_get_training_model_relay_file_status(self, payload, *, timeout=30):
        calls.append(("relay_file_status", self.instance_id, payload["file_path"]))
        return {"status": "ok", "exists": False, "size_bytes": 0}

    async def fake_commit_training_model_relay_import(self, payload, *, timeout=21600):
        calls.append(("relay_commit", self.instance_id))
        return {
            "status": "succeeded",
            "relay": True,
            "internal_model_ref": "/home/node/.skillforge_bridge/state/training_model_imports/qwen36/relay",
        }

    async def fake_configure_training_runtime(self, payload, *, timeout=30):
        calls.append(("configure", self.instance_id))
        return {"configured": True}

    async def fake_prepare_training_model(self, payload, *, timeout=7200):
        calls.append(("prepare", self.instance_id))
        return {"status": "succeeded", "exists": True, "model_dir": "/tmp/model"}

    monkeypatch.setattr(AIClawClient, "start_training_model_export", fake_start_training_model_export)
    monkeypatch.setattr(AIClawClient, "import_training_model_from_export", fake_import_training_model_from_export)
    monkeypatch.setattr(AIClawClient, "export_training_model_manifest", fake_export_training_model_manifest)
    monkeypatch.setattr(AIClawClient, "export_training_model_file_chunk", fake_export_training_model_file_chunk)
    monkeypatch.setattr(AIClawClient, "get_training_model_relay_file_status", fake_get_training_model_relay_file_status)
    monkeypatch.setattr(AIClawClient, "import_training_model_relay_chunk", fake_import_training_model_relay_chunk)
    monkeypatch.setattr(AIClawClient, "commit_training_model_relay_import", fake_commit_training_model_relay_import)
    monkeypatch.setattr(AIClawClient, "configure_training_runtime", fake_configure_training_runtime)
    monkeypatch.setattr(AIClawClient, "prepare_training_model", fake_prepare_training_model)

    resp = await client.post(
        "/api/training/models/transfer-to-training",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "training_gateway_id": "training-primary-relay-test",
            "source_gateway_id": "data-primary-relay-test",
            "source_path": "/srv/ai/models/hf/hub/models--Qwen--Qwen3.6-35B-A3B-FP8/snapshots/main",
            "validate_mode": "metadata",
            "run_inline": True,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "transferred"
    assert body["transfer_mode"] == "platform_relay"
    assert body["import"]["relay"] is True
    assert ("direct_import", "training-primary-relay-test") in calls
    assert ("relay_manifest", "data-primary-relay-test") in calls
    assert ("relay_commit", "training-primary-relay-test") in calls
    assert ("configure", "training-primary-relay-test") in calls
    assert ("prepare", "training-primary-relay-test") in calls


@pytest.mark.asyncio
async def test_training_resource_qwen36_35b_requires_internal_model_source(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-qwen36-internal-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="runtime-gpu-qwen36",
            name="EC Qwen36 Agent",
            department="EC",
            gateway_url="ws://runtime-gpu-qwen36",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.prepare_model", "training.model_status", "training.bootstrap_status"],
                "training": {"gateway": True, "supported_tasks": ["qlora", "inference"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        await db.commit()

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "runtime-gpu-qwen36")
    calls = []

    async def fake_prepare_training_model(self, payload, *, timeout=7200):
        calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "status": "planned",
            "profile": payload["profile"],
            "model_id": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": payload["source"],
            "internal_model_ref": payload["internal_model_ref"],
        }

    monkeypatch.setattr(AIClawClient, "prepare_training_model", fake_prepare_training_model)

    public_resp = await client.post(
        "/api/training/resources/runtime-gpu-qwen36/model",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "modelscope",
            "dry_run": True,
        },
    )
    assert public_resp.status_code == 422
    assert public_resp.json()["error"]["detail"]["detail"] == (
        "qwen3.6 35b aggressive profile must use internal, local_path or artifact source"
    )

    internal_resp = await client.post(
        "/api/training/resources/runtime-gpu-qwen36/model",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "internal_model_ref": "/mnt/internal-models/qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "dry_run": True,
            "validate_mode": "metadata",
            "timeout_seconds": 120,
            "auto_discover": True,
        },
    )
    assert internal_resp.status_code == 200, internal_resp.text
    assert internal_resp.json()["model"]["source"] == "internal"
    assert calls == [{
        "instance_id": "runtime-gpu-qwen36",
        "payload": {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "revision": "main",
            "modelscope_revision": "master",
            "bootstrap_profile": "qlora-1b",
            "force": False,
            "dry_run": True,
            "timeout_seconds": 120,
            "validate_mode": "metadata",
            "source": "internal",
            "auto_discover": True,
            "internal_model_ref": "/mnt/internal-models/qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        },
        "timeout": 180,
    }]


@pytest.mark.asyncio
async def test_training_resource_test_model_runs_base_model_only(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="runtime-gpu-model-test",
            name="EC 模型测试 Agent",
            department="EC",
            gateway_url="ws://runtime-gpu-model-test",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.model_status", "training.inference"],
                "training": {"gateway": True, "supported_tasks": ["inference"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        await db.commit()

    calls = []
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "runtime-gpu-model-test")

    async def fake_run_training_inference(self, payload, *, timeout=300):
        calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "status": "completed",
            "text": "原始模型输出",
            "metrics": {
                "generated_tokens": 12,
                "tokens_per_second": 6.0,
                "inference_backend": "bridge_local_base_inference",
            },
        }

    monkeypatch.setattr(AIClawClient, "run_training_inference", fake_run_training_inference)

    resp = await client.post(
        "/api/training/resources/runtime-gpu-model-test/model/test",
        json={
            "profile": "qwen3.5-4b",
            "prompt": "写一个短视频脚本",
            "max_new_tokens": 256,
            "timeout_seconds": 120,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["model"]["base_model_only"] is True
    assert body["result"]["text"] == "原始模型输出"
    assert body["result"]["metrics"]["tokens_per_second"] == 6.0
    assert body["result"]["metrics"]["generated_tokens"] == 12
    assert calls == [{
        "instance_id": "runtime-gpu-model-test",
        "payload": {
            "profile": "qwen3.5-4b",
            "bootstrap_profile": "qlora-1b",
            "prompt": "写一个短视频脚本",
            "max_new_tokens": 256,
            "timeout_seconds": 120,
            "base_model_only": True,
        },
        "timeout": 180,
    }]

    async with async_session_factory() as db:
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == "runtime-gpu-model-test").order_by(AuditLog.id)
        )).scalars().all()
    assert actions == ["training_gateway.test_model"]


@pytest.mark.asyncio
async def test_training_resources_accepts_cli_token(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    raw_token = "test-training-resources-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="cli-training-resource",
            name="CLI 训练 Agent",
            department="EC",
            gateway_url="ws://cli-training-resource",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.bootstrap_env"],
                "training": {"gateway": True, "supported_tasks": ["qlora"], "gpu_count": 1},
                "gpu": [{"name": "RTX 4060 Ti", "vram_total_mb": 8188, "vram_free_mb": 7600}],
            }),
            is_active=True,
        ))
        await db.commit()

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "cli-training-resource")

    resp = await client.get(
        "/api/training/resources",
        headers={"Authorization": f"Bearer {raw_token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    ids = [item["id"] for item in body["items"]]
    assert "cli-training-resource" in ids


@pytest.mark.asyncio
async def test_training_jobs_and_datasets_accept_cli_token(client):
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.skills.core.models import Skill
    from app.training.models import TrainingJob

    raw_token = "test-training-list-cli-token"
    await _seed_cli_session_token(raw_token)
    client._transport.app.dependency_overrides.pop(get_current_user, None)

    async with async_session_factory() as db:
        db.add(Skill(
            id="cli-dataset-skill",
            name="CLI 数据集 Skill",
            department="AI小组",
            status="active",
            visibility="department",
            owner="admin",
        ))
        db.add(TrainingJob(
            id="train-cli-visible",
            title="CLI 可见训练任务",
            department="AI小组",
            job_type="lora",
            status="awaiting_review",
            approval_required=True,
            target_skill_id="cli-dataset-skill",
            created_by="admin",
        ))
        await db.commit()

    headers = {"Authorization": f"Bearer {raw_token}"}
    datasets_resp = await client.get("/api/training/datasets", headers=headers)
    assert datasets_resp.status_code == 200, datasets_resp.text
    datasets = datasets_resp.json()
    assert datasets["total"] >= 1
    assert any(item["skill_id"] == "cli-dataset-skill" for item in datasets["items"])

    jobs_resp = await client.get("/api/training/jobs", headers=headers)
    assert jobs_resp.status_code == 200, jobs_resp.text
    jobs = jobs_resp.json()
    assert any(item["id"] == "train-cli-visible" for item in jobs["items"])


async def _create_evaluated_deployment_request(
    client,
    *,
    target_skill_id: str = "skill-recommend",
    model_family: str = "skill-recommend",
    rollout_percent: int = 0,
    sha256: str = "a" * 64,
):
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    await _seed_default_training_gateway(supported_tasks=["qlora", "lora", "eval"])

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "训练商品下滑动作推荐模型",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": target_skill_id,
            "target_gateway_id": "node-1",
            "spec": {"eval_gate": {"min_win_rate": 0.8}},
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve")
    assert approve_resp.status_code == 200

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {"win_rate": 0.81},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/model", "sha256": sha256}],
        },
    )
    assert result_resp.status_code == 200
    evaluate_resp = await client.post(f"/api/training/jobs/{created['id']}/evaluate")
    assert evaluate_resp.status_code == 200

    deploy_resp = await client.post(
        f"/api/training/jobs/{created['id']}/deploy-request",
        json={
            "target_skill_ids": [target_skill_id],
            "model_family": model_family,
            "reason": "评估通过，提交部署审批",
            "rollout_percent": rollout_percent,
        },
    )
    assert deploy_resp.status_code == 200
    payload = deploy_resp.json()
    return payload["deployment"], payload["job"]


def _patch_training_deployment_model_ready(
    monkeypatch,
    *,
    status: str = "ready",
    online: bool = True,
    gateway_id: str = "node-1",
):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: online and instance_id == gateway_id)

    async def fake_get_training_model_status(self, payload, *, timeout=30):
        current_status = status() if callable(status) else status
        return {
            "status": current_status,
            "profile": payload.get("profile") or "qwen3.5-4b",
            "exists": current_status in {"ready", "prepared", "available", "completed"},
            "bridge_instance_id": self.instance_id,
        }

    monkeypatch.setattr(AIClawClient, "get_training_model_status", fake_get_training_model_status)


@pytest.mark.asyncio
async def test_training_job_create_list_approve_cancel_roundtrip(client):
    from app.common.audit import AuditLog
    from app.database import async_session_factory

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "训练商品下滑动作推荐模型",
            "department": "EC",
            "job_type": "lora",
            "training_strategy": "lora_task_parallel",
            "target_gateway_id": "node-1",
            "dataset_ref": "dataset://ec/actions/v1",
            "objective": "提升商品下滑场景的动作推荐准确率",
            "spec": {"epochs": 2, "eval_gate": {"min_win_rate": 0.55}},
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["status"] == "awaiting_review"
    assert created["approval_required"] is True
    assert created["gateway_payload"] == {}
    assert created["gateway_routing"]["agent_contract"]["complete"] is True
    assert created["gateway_routing"]["agent_contract"]["submission_ready"] is True
    assert created["gateway_routing"]["agent_contract"]["lifecycle_ready"] is True
    assert created["gateway_routing"]["agent_contract"]["missing_ops"] == []

    list_resp = await client.get("/api/training/jobs")
    assert list_resp.status_code == 200
    listed = list_resp.json()
    assert listed["total"] == 1
    assert listed["stats"]["awaiting_review"] == 1
    assert listed["items"][0]["id"] == created["id"]

    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve")
    assert approve_resp.status_code == 200
    approved = approve_resp.json()
    assert approved["status"] == "queued"
    assert approved["gateway_payload"]["job_id"] == created["id"]
    assert approved["gateway_payload"]["dataset_ref"] == "dataset://ec/actions/v1"
    assert approved["gateway_payload"]["control"]["callback_token"] == "***"

    cancel_resp = await client.post(f"/api/training/jobs/{created['id']}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    async with async_session_factory() as db:
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert actions == ["training_job.create", "training_job.approve", "training_job.cancel"]


@pytest.mark.asyncio
async def test_training_automation_run_auto_approves_manual_jobs(client, monkeypatch):
    # This case explicitly exercises opt-in automation; public defaults remain off.
    for flag in ("TRAINING_AUTO_ENABLED", "TRAINING_AUTO_APPROVE_JOBS", "TRAINING_AUTO_REQUEST_DEPLOYMENT", "TRAINING_AUTO_APPROVE_DEPLOYMENT", "TRAINING_AUTO_ACTIVATE_DEPLOYMENT"):
        monkeypatch.setattr(f"app.training.service.{flag}", True)
    await _seed_default_training_gateway(gateway_id="training-primary", supported_tasks=["lora", "qlora", "eval"])

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "普通任务也进入全自动",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-auto-manual",
            "target_gateway_id": "training-primary",
            "dataset_ref": "dataset://ec/manual/v1",
            "spec": {"eval_gate": {"min_win_rate": 0.55}},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    assert created["status"] == "awaiting_review"
    assert created["spec"]["automation"]["auto_approve_job"] is True
    assert created["spec"]["deployment"]["auto_request"] is True
    assert created["deployment_target_gateway_id"] == "inference-primary"

    run_resp = await client.post(
        "/api/training/automation/run",
        json={"datasets": False, "jobs": True, "dispatch": False, "evaluate": True, "trigger": "pytest"},
    )
    assert run_resp.status_code == 200, run_resp.text
    run = run_resp.json()
    assert run["stats"]["jobs_seen"] >= 1
    assert run["stats"]["jobs_changed"] >= 1
    job_action = next(item for item in run["actions"] if item["target_id"] == created["id"])
    assert job_action["status"] == "completed"
    assert job_action["actions"][0]["action"] == "approve"
    assert any(item["field"] == "status" for item in job_action["changed_fields"])

    detail = (await client.get(f"/api/training/jobs/{created['id']}")).json()
    assert detail["status"] == "queued"
    assert detail["approved_by"] == "training_automation"

    automation = (await client.get("/api/training/automation/status")).json()
    assert automation["policy"]["include_manual_jobs"] is True
    assert automation["last_run"]["trigger"] == "pytest"
    assert automation["last_run"]["stats"]["jobs_changed"] >= 1


@pytest.mark.asyncio
async def test_training_automation_run_materializes_learning_entry_samples(client):
    from app.database import async_session_factory
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training.models import TrainingDatasetVersion, TrainingSample

    async with async_session_factory() as db:
        db.add(LearningEvent(
            id="le-training-entry-materialize",
            event_type="decision.feedback",
            source_type="decision_log",
            source_id="decision-entry-materialize",
            source_hash="decision-entry-materialize",
            department="EC",
            redacted_summary="学习闭环入口样本。",
        ))
        db.add(LearningArtifact(
            id="la-training-entry-ready",
            event_id="le-training-entry-materialize",
            artifact_kind="training_sample",
            artifact_hash="la-training-entry-ready",
            target_type="skill",
            target_id="skill-entry",
            department="EC",
            skill_id="skill-entry",
            title="入口 ready 样本",
            summary="自动化入口推进应物化该样本。",
            content_json={"input": {"question": "入口"}, "output": {"summary": "样本"}},
            labels_json=["training"],
            quality_score=0.9,
            confidence=0.9,
            status="ready",
            sink_type="training_sample",
        ))
        await db.commit()

    run_resp = await client.post(
        "/api/training/automation/run",
        json={"materialize_learning": True, "datasets": False, "jobs": False, "trigger": "pytest_entry"},
    )
    assert run_resp.status_code == 200, run_resp.text
    payload = run_resp.json()
    assert payload["stats"]["learning_checked"] >= 1
    assert payload["stats"]["learning_materialized"] >= 1
    assert payload["stats"]["learning_samples_promoted"] >= 1
    assert payload["stats"]["datasets_created"] >= 1
    materialize_action = next(item for item in payload["actions"] if item["action"] == "materialize_learning")
    promote_action = next(item for item in payload["actions"] if item["action"] == "promote_learning_samples")
    dataset_action = next(item for item in payload["actions"] if item["action"] == "create_dataset_manifest")
    assert materialize_action["status"] == "completed"
    assert promote_action["status"] == "completed"
    assert dataset_action["status"] == "completed"

    async with async_session_factory() as db:
        artifact = await db.get(LearningArtifact, "la-training-entry-ready")
        sample = await db.get(TrainingSample, "la-training-entry-ready")
        dataset = (
            await db.execute(
                select(TrainingDatasetVersion)
                .where(TrainingDatasetVersion.department == "EC")
                .where(TrainingDatasetVersion.dataset_profile == "text_sft_v1")
                .order_by(TrainingDatasetVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    assert artifact.status == "materialized"
    assert artifact.sink_type == "training_sample"
    assert sample is not None
    assert sample.status == "ready"
    assert sample.source_id
    assert sample.metadata_json["learning_artifact_id"] == "la-training-entry-ready"
    assert dataset is not None
    assert dataset.sample_count >= 1
    assert dataset.sync_status == "not_synced"
    assert "la-training-entry-ready" in dataset.manifest_json["sample_ids"]


@pytest.mark.asyncio
async def test_training_automation_creates_company_scope_dataset_for_unassigned_learning_samples(client):
    from app.database import async_session_factory
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training.models import TrainingDatasetVersion, TrainingSample

    async with async_session_factory() as db:
        db.add(LearningEvent(
            id="le-training-company-materialized",
            event_type="decision.feedback",
            source_type="decision_log",
            source_id="decision-company-materialized",
            source_hash="decision-company-materialized",
            department=None,
            redacted_summary="公司级学习闭环样本。",
        ))
        db.add(LearningArtifact(
            id="la-training-company-materialized",
            event_id="le-training-company-materialized",
            artifact_kind="training_sample",
            artifact_hash="la-training-company-materialized",
            target_type="skill",
            target_id="skill-company",
            department=None,
            skill_id="skill-company",
            title="公司级样本",
            summary="公司级样本应进入 company manifest。",
            content_json={"input": {"question": "company"}, "output": {"summary": "sample"}},
            labels_json=["training"],
            quality_score=0.9,
            confidence=0.9,
            status="materialized",
            sink_type="training_sample",
        ))
        await db.commit()

    run_resp = await client.post(
        "/api/training/automation/run",
        json={
            "materialize_learning": False,
            "promote_learning_samples": True,
            "create_datasets": True,
            "datasets": False,
            "jobs": False,
            "trigger": "pytest_company_scope",
        },
    )
    assert run_resp.status_code == 200, run_resp.text
    payload = run_resp.json()
    assert payload["stats"]["learning_samples_promoted"] >= 1
    assert payload["stats"]["datasets_created"] >= 1

    async with async_session_factory() as db:
        sample = await db.get(TrainingSample, "la-training-company-materialized")
        dataset = (
            await db.execute(
                select(TrainingDatasetVersion)
                .where(TrainingDatasetVersion.name == "learning-artifacts-company")
                .where(TrainingDatasetVersion.department.is_(None))
                .where(TrainingDatasetVersion.dataset_profile == "text_sft_v1")
                .order_by(TrainingDatasetVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    assert sample is not None
    assert sample.department is None
    assert dataset is not None
    assert dataset.department is None
    assert "la-training-company-materialized" in dataset.manifest_json["sample_ids"]


@pytest.mark.asyncio
async def test_training_dataset_sync_writes_manifest_to_online_gateway(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient
    from app.auth.models import User
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.learning.models import LearningIngestionJob
    from app.training.models import TrainingAssetSource, TrainingDatasetVersion, TrainingSample
    from app.training.service import create_training_dataset_version, sync_training_dataset_version

    async with async_session_factory() as db:
        db.add(User(
            id="admin",
            username="admin",
            name="管理员",
            role="admin",
            department="EC",
            can_view_all=True,
            is_active=True,
            state="active",
        ))
        db.add(OpenClawInstance(
            id="training-primary",
            name="GB10 237 · NVIDIA GB10",
            department="EC",
            gateway_url="ws://training-primary",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "gateway_kind": "openclaw",
                "ops": [
                    "training.dataset_write_chunk",
                    "training.dataset_commit",
                    "training.dataset_status",
                ],
                "training": {"gateway": True, "supported_tasks": ["lora", "qlora", "eval"], "gpu_count": 1},
            }),
            is_active=True,
        ))
        source = TrainingAssetSource(
            id="tas-dataset-sync-online",
            source_type="manual_upload",
            source_id="dataset-sync-online",
            title="dataset sync online",
            department="EC",
            modality="text",
            sample_count=1,
            metadata_json={},
            policy_result_json={},
        )
        db.add(source)
        db.add(TrainingSample(
            id="sample-dataset-sync-online",
            source_id=source.id,
            dataset_profile="text_sft_v1",
            modality="text",
            sample_hash="sample-dataset-sync-online-hash",
            content_json={"instruction": "同步", "input": {"q": "q"}, "output": {"a": "a"}},
            media_refs_json=[],
            labels_json=["training"],
            quality_score=0.9,
            department="EC",
            sensitivity_level="internal",
            status="ready",
        ))
        await db.commit()

    calls: list[tuple[str, str, dict]] = []

    async def fake_write_training_dataset_chunk(self, payload, *, timeout=120):
        calls.append(("write", self.instance_id, payload))
        return {"status": "ok", "dataset_id": payload["dataset_id"]}

    async def fake_commit_training_dataset(self, payload, *, timeout=300):
        calls.append(("commit", self.instance_id, payload))
        return {
            "status": "succeeded",
            "dataset_id": payload["dataset_id"],
            "dataset_dir": f"/srv/skillforge/datasets/{payload['dataset_id']}",
            "dataset_path": f"/srv/skillforge/datasets/{payload['dataset_id']}/dataset.json",
            "sha256": payload["sha256"],
            "size_bytes": 1024,
        }

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "training-primary")
    monkeypatch.setattr(AIClawClient, "write_training_dataset_chunk", fake_write_training_dataset_chunk)
    monkeypatch.setattr(AIClawClient, "commit_training_dataset", fake_commit_training_dataset)

    async with async_session_factory() as db:
        user = await db.get(User, "admin")
        created = await create_training_dataset_version(
            db,
            user,
            {
                "name": "dataset-sync-online",
                "dataset_profile": "text_sft_v1",
                "modality": "text",
                "department": "EC",
                "sample_ids": ["sample-dataset-sync-online"],
                "target_gateway_id": "training-primary",
                "auto_sync": False,
            },
        )
        dataset_id = created["dataset_version"]["id"]
        result = await sync_training_dataset_version(
            db,
            user,
            dataset_id,
            {"target_gateway_id": "training-primary"},
        )
        dataset = await db.get(TrainingDatasetVersion, dataset_id)
        sink_job = (
            await db.execute(
                select(LearningIngestionJob)
                .where(LearningIngestionJob.job_id == dataset.synced_sink_job_id)
                .limit(1)
            )
        ).scalar_one()

    assert result["dataset_version"]["sync_status"] == "completed"
    assert dataset.sync_status == "completed"
    assert sink_job.status == "completed"
    assert sink_job.attempts == 1
    assert sink_job.policy_json["gateway_dataset_path"].endswith("/dataset.json")
    assert ("commit", "training-primary") in [(name, instance_id) for name, instance_id, _payload in calls]


@pytest.mark.asyncio
async def test_training_job_simulate_result_writes_output_and_verifies_changes(client, monkeypatch):
    # This case explicitly exercises opt-in automation; public defaults remain off.
    for flag in ("TRAINING_AUTO_ENABLED", "TRAINING_AUTO_APPROVE_JOBS", "TRAINING_AUTO_REQUEST_DEPLOYMENT", "TRAINING_AUTO_APPROVE_DEPLOYMENT", "TRAINING_AUTO_ACTIVATE_DEPLOYMENT"):
        monkeypatch.setattr(f"app.training.service.{flag}", True)
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient
    from app.common.audit import AuditLog
    from app.database import async_session_factory

    await _seed_default_training_gateway(gateway_id="training-primary", supported_tasks=["lora", "qlora", "eval"])
    await _seed_m3_deployment_gateway()
    monkeypatch.setattr(
        bridge_registry,
        "is_online",
        lambda instance_id: instance_id in {"training-primary", "inference-primary"},
    )

    async def fake_get_training_model_status(self, payload, *, timeout=30):
        return {
            "status": "ready",
            "profile": payload.get("profile") or "qwen3.6-35b-a3b",
            "exists": True,
            "bridge_instance_id": self.instance_id,
        }

    monkeypatch.setattr(AIClawClient, "get_training_model_status", fake_get_training_model_status)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "模拟输出验证模型",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-sim-result",
            "target_gateway_id": "training-primary",
            "dataset_ref": "dataset://ec/sim/v1",
            "spec": {"eval_gate": {"min_win_rate": 0.8}},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()

    simulate_resp = await client.post(
        f"/api/training/jobs/{created['id']}/simulate-result",
        json={
            "metrics": {
                "win_rate": 0.93,
                "eval_requested_samples": 24,
                "eval_evaluated_samples": 24,
            }
        },
    )
    assert simulate_resp.status_code == 200, simulate_resp.text
    payload = simulate_resp.json()
    changed = {item["field"]: item for item in payload["changed_fields"]}

    assert payload["ok"] is True
    assert payload["before"]["status"] == "awaiting_review"
    assert payload["after"]["status"] == "completed"
    assert payload["after"]["approved_by"] == "training_automation"
    assert payload["after"]["evaluation_status"] == "completed"
    assert payload["after"]["deployment_status"] == "active"
    assert payload["after"]["deployment_target_gateway_id"] == "inference-primary"
    assert payload["after"]["artifact_uri"].startswith("artifact://training-sim/")
    assert len(payload["after"]["artifact_sha256"]) == 64
    assert changed["status"]["before"] == "awaiting_review"
    assert changed["status"]["after"] == "completed"
    assert "artifact_uri" in changed
    assert payload["simulated_request"]["gateway_id"] == "training-primary"
    assert payload["simulated_request"]["artifacts"][0]["deployment_target_gateway_id"] == "inference-primary"
    assert payload["job"]["latest_deployment"]["status"] == "active"
    assert payload["job"]["latest_deployment"]["approved_by"] == "training_automation"

    async with async_session_factory() as db:
        actions = (await db.execute(
            select(AuditLog.action)
            .where(AuditLog.target_id == created["id"])
            .order_by(AuditLog.id)
        )).scalars().all()
    assert "training_job.gateway_result" in actions
    assert "training_job.simulate_gateway_result" in actions


@pytest.mark.asyncio
async def test_training_job_auto_routes_to_department_training_gateway(client):
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-training-1",
            name="EC 训练 Agent",
            department="EC",
            gateway_url="ws://node-training",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora", "eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
            is_active=True,
        ))
        await db.commit()

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "自动路由训练",
            "department": "EC",
            "job_type": "lora",
            "dataset_ref": "dataset://ec/actions/v1",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["target_gateway_id"] == "node-training-1"
    assert created["gateway_routing"]["mode"] == "auto"
    assert created["gateway_routing"]["scope"] == "department"
    assert created["gateway_routing"]["selected_gateway_id"] == "node-training-1"
    assert created["gateway_routing"]["agent_contract"]["complete"] is True
    assert created["gateway_routing"]["agent_contract"]["input_channels"] == [
        "training_job.gateway_payload",
        "learning_artifacts.dataset_package",
    ]
    assert "training.submit_job" in created["gateway_routing"]["agent_contract"]["control_ops"]
    assert created["gateway_routing"]["agent_contract"]["advertised_ops"] == [
        "training.collect_result",
        "training.inference",
        "training.submit_job",
    ]
    assert created["gateway_routing"]["agent_contract"]["submission_ready"] is True
    assert created["gateway_routing"]["agent_contract"]["lifecycle_ready"] is True
    assert created["gateway_routing"]["agent_contract"]["missing"] == []
    assert created["gateway_routing"]["agent_contract"]["missing_ops"] == []
    assert created["gateway_routing"]["agent_contract"]["output_channels"] == [
        "training_job.gateway_result",
        "training_artifact_refs",
        "training_metrics",
    ]
    assert created["gateway_routing"]["agent_contract"]["flow_traceable"] is True

    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve")
    assert approve_resp.status_code == 200
    approved = approve_resp.json()
    assert approved["gateway_payload"]["target_gateway_id"] == "node-training-1"
    assert approved["gateway_payload"]["control"]["agent_id"] == "node-training-1"
    assert approved["gateway_payload"]["control"]["agent_purpose"] == "training"
    assert approved["gateway_payload"]["control"]["agent_contract"]["submission_ready"] is True
    assert approved["gateway_payload"]["control"]["agent_contract"]["lifecycle_ready"] is True
    assert approved["gateway_payload"]["control"]["agent_contract"]["missing_ops"] == []


@pytest.mark.asyncio
async def test_training_job_department_user_can_use_platform_training_gateway_fallback(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

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

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="platform-training-1",
            name="平台训练 Agent",
            department="AI",
            gateway_url="ws://platform-training",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            is_platform_default=True,
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora"],
                    "gpu_count": 2,
                    "worker_count": 2,
                },
            }),
            is_active=True,
        ))
        await db.commit()

    calls = []

    async def fake_submit_training_job(self, payload, *, timeout=30):
        calls.append({"instance_id": self.instance_id, "payload": payload})
        return {"accepted": True, "status": "running", "worker_id": "platform-worker"}

    monkeypatch.setattr(AIClawClient, "submit_training_job", fake_submit_training_job)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "部门借用平台训练",
            "department": "EC",
            "job_type": "lora",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["target_gateway_id"] == "platform-training-1"
    assert created["gateway_routing"]["scope"] == "platform_fallback"
    assert created["gateway_routing"]["platform_fallback"] is True

    assert (await client.post(f"/api/training/jobs/{created['id']}/approve")).status_code == 200
    dispatch_resp = await client.post(f"/api/training/jobs/{created['id']}/dispatch")
    assert dispatch_resp.status_code == 200
    dispatched = dispatch_resp.json()
    assert dispatched["status"] == "running"
    assert calls[0]["instance_id"] == "platform-training-1"
    assert calls[0]["payload"]["department"] == "EC"


@pytest.mark.asyncio
async def test_training_job_auto_route_skips_runtime_agent_with_training_capability(client):
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add_all([
            OpenClawInstance(
                id="runtime-gpu-1",
                name="EC 执行 Agent",
                department="EC",
                gateway_url="ws://runtime-gpu",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="skill_runtime",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                    "training": {"gateway": True, "supported_tasks": ["lora"]},
                    "gpu": [{"name": "RTX 4060", "vram_total_mb": 8192, "vram_free_mb": 4096}],
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="training-gpu-1",
                name="EC 训练 Agent",
                department="EC",
                gateway_url="ws://training-gpu",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="training",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                    "training": {"gateway": True, "supported_tasks": ["lora"]},
                }),
                is_active=True,
            ),
        ])
        await db.commit()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "只路由训练 Agent",
            "department": "EC",
            "job_type": "lora",
        },
    )

    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["target_gateway_id"] == "training-gpu-1"
    assert created["gateway_routing"]["selected_gateway_id"] == "training-gpu-1"


@pytest.mark.asyncio
async def test_training_job_auto_route_prefers_gb10_and_rejects_comfyui_reserved_rtx(client):
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    training_ops = ["training.submit_job", "training.collect_result", "training.inference"]
    async with async_session_factory() as db:
        db.add_all([
            OpenClawInstance(
                id="training-primary",
                name="GB10 237 · NVIDIA GB10",
                department="AI",
                gateway_url="ws://training-primary",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                is_platform_default=True,
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["lora", "qlora", "eval", "merge"]},
                    "gpu": [{"name": "NVIDIA GB10", "vram_total_mb": 124620, "vram_free_mb": 121000}],
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="data-primary",
                name="RTX Pro 6000 · ComfyUI",
                department="AI",
                gateway_url="ws://data-primary",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                is_platform_default=True,
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["lora", "qlora", "eval", "merge"]},
                    "gpu": [{"name": "NVIDIA RTX PRO 6000", "vram_total_mb": 97800, "vram_free_mb": 97800}],
                }),
                is_active=True,
            ),
        ])
        await db.commit()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "GB10 优先训练",
            "department": "EC",
            "job_type": "qlora",
            "target_skill_id": "skill-recommend",
        },
    )

    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    assert created["target_gateway_id"] == "training-primary"
    assert created["gateway_routing"]["selected_gateway_id"] == "training-primary"
    assert created["gateway_routing"]["agent_contract"]["route_policy"]["role"] == "primary_training"

    manual_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "不能占用 ComfyUI 节点",
            "department": "EC",
            "job_type": "qlora",
            "target_gateway_id": "data-primary",
        },
    )
    assert manual_resp.status_code == 422
    assert manual_resp.json()["error"]["detail"]["reserved_for"] == "comfyui"


@pytest.mark.asyncio
async def test_training_automation_status_groups_department_process_flow(client):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training.models import (
        TrainingAssetSource,
        TrainingDatasetVersion,
        TrainingJob,
        TrainingJobTask,
        TrainingModelDeployment,
    )

    old_time = now_bjt() - timedelta(days=10)
    async with async_session_factory() as db:
        db.add(LearningEvent(
            id="le-training-flow-ec",
            event_type="execution.completed",
            source_type="execution_run",
            source_id="run-training-flow-ec",
            source_hash="run-training-flow-ec",
            department="Flow EC",
            skill_id="skill-flow",
            redacted_summary="部门过程数据进入训练流水。",
            quality_score=0.95,
        ))
        db.add(LearningEvent(
            id="le-training-flow-old",
            event_type="execution.completed",
            source_type="execution_run",
            source_id="run-training-flow-old",
            source_hash="run-training-flow-old",
            department="Flow Old",
            skill_id="skill-flow-old",
            redacted_summary="较早的部门过程数据。",
            quality_score=0.9,
            created_at=old_time,
            updated_at=old_time,
        ))
        db.add_all([
            LearningArtifact(
                id="la-training-flow-ec-train",
                event_id="le-training-flow-ec",
                artifact_kind="training_sample",
                artifact_hash="la-training-flow-ec-train",
                target_type="skill",
                target_id="skill-flow",
                department="Flow EC",
                skill_id="skill-flow",
                title="训练样本",
                summary="已清洗训练样本。",
                content_json={"input": {"question": "过程数据"}, "output": {"summary": "模型训练"}},
                labels_json=["training"],
                quality_score=0.9,
                confidence=0.9,
                status="materialized",
                sink_type="training_sample",
            ),
            LearningArtifact(
                id="la-training-flow-ec-eval",
                event_id="le-training-flow-ec",
                artifact_kind="eval_case",
                artifact_hash="la-training-flow-ec-eval",
                target_type="skill",
                target_id="skill-flow",
                department="Flow EC",
                skill_id="skill-flow",
                title="评估样本",
                summary="已清洗评估样本。",
                content_json={"input": {"question": "评估"}, "output": {"summary": "通过"}},
                labels_json=["eval"],
                quality_score=0.9,
                confidence=0.9,
                status="materialized",
                sink_type="training_sample",
            ),
        ])
        db.add_all([
            TrainingAssetSource(
                id="tas-training-flow-ec-sdk",
                source_type="sdk_upload",
                source_id="sdk-training-flow-ec",
                department="Flow EC",
                modality="text",
                sample_count=3,
                metadata_json={},
                policy_result_json={},
            ),
            TrainingAssetSource(
                id="tas-training-flow-ec-manual",
                source_type="manual_upload",
                source_id="manual-training-flow-ec",
                department="Flow EC",
                modality="image",
                sample_count=4,
                metadata_json={},
                policy_result_json={},
            ),
        ])
        db.add(TrainingDatasetVersion(
            id="tdv-training-flow-ec",
            name="flow-ec",
            version="20260809",
            dataset_profile="text_sft_v1",
            modality="image_text",
            department="Flow EC",
            status="ready",
            sample_count=2,
            sync_status="completed",
        ))
        db.add(TrainingJob(
            id="train-flow-ec",
            title="Flow EC 训练任务",
            department="Flow EC",
            created_by="admin",
            status="completed",
            job_type="lora",
            target_skill_id="skill-flow",
            target_gateway_id="training-primary",
            dataset_ref="dataset://flow-ec/20260809",
            spec_json={"model_name": "flow-ec-model"},
        ))
        db.add_all([
            TrainingJob(
                id="train-flow-status-awaiting",
                title="Flow Status 待审任务",
                department="Flow Status",
                created_by="admin",
                status="awaiting_review",
                job_type="lora",
                target_gateway_id="training-primary",
                dataset_ref="dataset://flow-status/awaiting",
                spec_json={"model_name": "flow-status-awaiting"},
            ),
            TrainingJob(
                id="train-flow-status-queued",
                title="Flow Status 队列任务",
                department="Flow Status",
                created_by="admin",
                status="queued",
                job_type="lora",
                target_gateway_id="training-primary",
                dataset_ref="dataset://flow-status/queued",
                spec_json={"model_name": "flow-status-queued"},
            ),
            TrainingJob(
                id="train-flow-status-running",
                title="Flow Status 训练中任务",
                department="Flow Status",
                created_by="admin",
                status="running",
                job_type="lora",
                target_gateway_id="training-primary",
                dataset_ref="dataset://flow-status/running",
                spec_json={"model_name": "flow-status-running"},
            ),
        ])
        db.add(TrainingJobTask(
            job_id="train-flow-ec",
            gateway_id="training-primary",
            status="completed",
            progress=1,
            metrics_json={"op": "training.evaluate", "passed": True},
        ))
        db.add(TrainingModelDeployment(
            id="deploy-flow-ec",
            job_id="train-flow-ec",
            department="Flow EC",
            model_family="flow-ec-model",
            artifact_id="artifact-flow-ec",
            status="active",
            rollout_percent=100,
            requested_by="admin",
            deployment_target_gateway_id="inference-primary",
        ))
        await db.commit()

    resp = await client.get("/api/training/automation/status")
    assert resp.status_code == 200, resp.text
    flow = resp.json()["department_flow"]
    assert [item["label"] for item in flow["stage_order"]] == ["入口", "样本", "数据集", "GB10 同步", "自动寻路", "训练", "评估", "Mac236 部署", "运行"]
    department = next(item for item in flow["departments"] if item["department"] == "Flow EC")
    assert department["metrics"]["raw_events"] == 1
    assert department["metrics"]["entry_sdk"] == 3
    assert department["metrics"]["entry_learning"] == 2
    assert department["metrics"]["entry_manual"] == 4
    assert department["metrics"]["entry_tasks"] == 1
    assert department["metrics"]["entry_datasets"] == 1
    assert department["metrics"]["sample_text"] == 3
    assert department["metrics"]["sample_image"] == 4
    assert department["metrics"]["sample_image_text"] == 0
    assert department["metrics"]["cleaned_samples"] == 2
    assert department["metrics"]["datasets_unsynced"] == 0
    dataset_stage = next(item for item in department["stages"] if item["key"] == "dataset")
    assert dataset_stage["count"] == 1
    assert dataset_stage["detail"] == "1 版本化 manifest / 0 待同步"
    gb10_sync_stage = next(item for item in department["stages"] if item["key"] == "gb10_sync")
    assert gb10_sync_stage["count"] == 1
    assert gb10_sync_stage["detail"] == "1 同步到 237 / 0 待同步"
    assert department["metrics"]["jobs_target_gb10"] == 1
    assert department["metrics"]["jobs_target_fallback"] == 0
    assert department["metrics"]["jobs_unrouted"] == 0
    assert department["metrics"]["jobs_completed"] == 1
    assert department["metrics"]["eval_completed"] >= 1
    assert department["metrics"]["deployments"] == 1
    assert department["metrics"]["deployments_active"] == 1
    deploy_stage = next(item for item in department["stages"] if item["key"] == "deploy")
    assert deploy_stage["count"] == 1
    assert deploy_stage["detail"] == "1 内网自动部署 / 0 灰度"
    assert department["current_stage"] == "run"
    status_department = next(item for item in flow["departments"] if item["department"] == "Flow Status")
    assert status_department["metrics"]["jobs_awaiting"] == 1
    assert status_department["metrics"]["jobs_queued"] == 1
    assert status_department["metrics"]["jobs_training"] == 1
    assert status_department["metrics"]["jobs_running"] == 2
    assert status_department["metrics"]["jobs_target_gb10"] == 3
    route_stage = next(item for item in status_department["stages"] if item["key"] == "route")
    assert route_stage["detail"] == "3 指向 GB10 / 0 兜底 GPU / 0 待寻路"
    train_stage = next(item for item in status_department["stages"] if item["key"] == "train")
    assert train_stage["detail"] == "1 自动审核 / 1 训练中"

    daily_resp = await client.get("/api/training/automation/status?window_days=1")
    assert daily_resp.status_code == 200, daily_resp.text
    daily_flow = daily_resp.json()["department_flow"]
    assert daily_flow["window_days"] == 1
    assert all(item["department"] != "Flow Old" for item in daily_flow["departments"])

    monthly_resp = await client.get("/api/training/automation/status?window_days=30")
    assert monthly_resp.status_code == 200, monthly_resp.text
    monthly_flow = monthly_resp.json()["department_flow"]
    assert monthly_flow["window_days"] == 30
    assert any(item["department"] == "Flow Old" for item in monthly_flow["departments"])


@pytest.mark.asyncio
async def test_full_history_finetune_status_blocks_without_online_gpu(client):
    resp = await client.get("/api/training/full-history-finetune/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ready_to_run"] is False
    blocker_keys = {item["key"] for item in payload["blockers"]}
    assert "no_online_training_gateway" in blocker_keys
    assert "no_online_inference_gateway" in blocker_keys


@pytest.mark.asyncio
async def test_full_history_finetune_status_explains_active_deployment_without_chat_model(client, monkeypatch):
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    monkeypatch.setattr("app.training.service._deployment_openwebui_model_id", lambda deployment, job: "")

    async with async_session_factory() as db:
        db.add(TrainingJob(
            id="train-full-history-active-no-chat-model",
            title="全量历史 active 部署",
            department="AI平台",
            created_by="admin",
            status="completed",
            job_type="lora",
            target_gateway_id="training-primary",
            target_skill_id="skillforge-finetuned-model-chat",
            dataset_ref="learning-artifacts://platform/training/full-history/latest",
            spec_json={"governance": {"full_history_three_cycle": True}, "cycle_index": 3},
        ))
        db.add(TrainingModelDeployment(
            id="deploy-full-history-active-no-chat-model",
            job_id="train-full-history-active-no-chat-model",
            department="AI平台",
            model_family="skillforge-full-history-4b-chat-adapter",
            artifact_id="artifact-full-history-active-no-chat-model",
            artifact_ref_json={"uri": "file:///models/full-history/model.safetensors"},
            target_skill_ids_json=["skillforge-finetuned-model-chat"],
            status="active",
            rollout_percent=100,
            requested_by="admin",
            deployment_target_gateway_id="inference-primary",
        ))
        await db.commit()

    resp = await client.get("/api/training/full-history-finetune/status")
    assert resp.status_code == 200, resp.text
    chat = resp.json()["chat"]
    assert chat["ready"] is False
    assert chat["disabled_reason"] == "已存在 active 部署，但缺少大厅对话模型 ID"


@pytest.mark.asyncio
async def test_full_history_finetune_uses_online_gateway_fallback_and_creates_4b_cycle(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training.models import TrainingJob

    training_ops = ["training.submit_job", "training.collect_result", "training.inference"]
    async with async_session_factory() as db:
        db.add_all([
            OpenClawInstance(
                id="training-primary",
                name="GB10 237 · NVIDIA GB10",
                department="AI",
                gateway_url="ws://training-primary",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                is_platform_default=True,
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["qlora", "lora", "merge"]},
                    "gpu": [{"name": "NVIDIA GB10", "vram_total_mb": 124620, "vram_free_mb": 100000}],
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="inference-primary",
                name="Mac 236 · M3 Ultra",
                department="AI",
                gateway_url="ws://inference-primary",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                is_platform_default=True,
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["inference", "lora"]},
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="online-gpu-fallback",
                name="在线 GPU 兜底",
                department="AI",
                gateway_url="ws://online-gpu-fallback",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                is_platform_default=True,
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["qlora", "lora", "merge", "eval"]},
                    "gpu": [{"name": "NVIDIA L40S", "vram_total_mb": 49152, "vram_free_mb": 42000}],
                }),
                is_active=True,
            ),
            LearningEvent(
                id="le-full-history-4b",
                event_type="decision.feedback",
                source_type="decision_log",
                source_id="decision-full-history-4b",
                source_hash="decision-full-history-4b",
                department="AI小组",
                redacted_summary="全量 4B 微调样本。",
            ),
        ])
        for idx in range(5):
            db.add(LearningArtifact(
                id=f"la-full-history-4b-{idx}",
                event_id="le-full-history-4b",
                artifact_kind="eval_case" if idx == 4 else "training_sample",
                artifact_hash=f"la-full-history-4b-{idx}",
                target_type="skill",
                target_id="source-skill",
                department="AI小组",
                skill_id="source-skill",
                title=f"全量 4B 样本 {idx}",
                summary="用于验证在线 GPU 兜底微调。",
                content_json={
                    "input": {"question": f"输入 {idx}"},
                    "output": {"summary": f"输出 {idx}"},
                    "formed_data": {"trace": idx},
                    "source_type": "execution_trace",
                },
                labels_json=["training"],
                quality_score=0.9,
                confidence=0.9,
                status="materialized",
                sink_type="training_sample",
            ))
        await db.commit()

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "online-gpu-fallback")

    async def fake_submit_training_job(self, payload, *, timeout=30):
        return {"accepted": True, "status": "running", "worker_id": "fallback-worker"}

    monkeypatch.setattr(AIClawClient, "submit_training_job", fake_submit_training_job)
    monkeypatch.setattr("app.training.service.kickoff_full_history_finetune_automation", lambda: True)

    resp = await client.post(
        "/api/training/full-history-finetune/run",
        json={"cycles": 3, "capture_sources": False, "min_sample_count": 4},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "running_or_blocked"
    assert payload["completed_cycles"] == 0
    action_names = [item["action"] for item in payload["actions"]]
    assert "create_job" in action_names
    assert "schedule_background_auto_advance" in action_names
    assert payload["after"]["automation_run"]["status"] == "running"
    assert payload["after"]["automation_run"]["current_step"] == "approve_job"
    assert payload["after"]["current_job_id"] == payload["actions"][0]["job_id"]
    assert payload["after"]["selected"]["training_gateway"]["id"] == "online-gpu-fallback"
    assert payload["after"]["selected"]["deployment_gateway"]["id"] == "online-gpu-fallback"
    blocker_keys = {item["key"] for item in payload["after"]["blockers"]}
    assert "gb10_training_fallback" in blocker_keys
    assert "mac236_deployment_fallback" in blocker_keys

    async with async_session_factory() as db:
        job = (
            await db.execute(
                select(TrainingJob).where(TrainingJob.target_skill_id == "skillforge-finetuned-model-chat")
            )
        ).scalar_one()
    spec = job.spec_json or {}
    assert job.target_gateway_id == "online-gpu-fallback"
    assert spec["model"]["profile"] == "qwen3.5-4b"
    assert spec["model"]["source"] == "auto"
    assert spec["deployment"]["deployment_target_gateway_id"] == "online-gpu-fallback"
    assert spec["deployment"]["deployment_runtime_profile"] == "mlx-qwen3.5-4b-lora"
    assert spec["deployment"]["keyword_review"]["enabled"] is False
    assert spec["deployment"]["keyword_review"]["domain"] == "skillforge_full_history_chat"
    assert spec["governance"]["full_history_three_cycle"] is True
    assert spec["governance"]["cycle_index"] == 1


@pytest.mark.asyncio
async def test_full_history_background_automation_persists_run_and_step(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training import service as training_service
    from app.training.models import TrainingFullHistoryAutomationRun, TrainingJob, TrainingJobTask, TrainingModelDeployment

    training_ops = ["training.submit_job", "training.collect_result", "training.inference"]
    async with async_session_factory() as db:
        existing_jobs = (
            await db.execute(
                select(TrainingJob.id).where(TrainingJob.target_skill_id == "skillforge-finetuned-model-chat")
            )
        ).scalars().all()
        if existing_jobs:
            await db.execute(delete(TrainingJobTask).where(TrainingJobTask.job_id.in_(existing_jobs)))
            await db.execute(delete(TrainingModelDeployment).where(TrainingModelDeployment.job_id.in_(existing_jobs)))
            await db.execute(delete(TrainingJob).where(TrainingJob.id.in_(existing_jobs)))
        await db.execute(delete(TrainingFullHistoryAutomationRun))
        db.add_all([
            OpenClawInstance(
                id="training-primary",
                name="GB10 237 · NVIDIA GB10",
                department="AI",
                gateway_url="ws://training-primary",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                is_platform_default=True,
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["qlora", "lora", "merge", "eval"]},
                    "gpu": [{"name": "NVIDIA GB10", "vram_total_mb": 124620, "vram_free_mb": 100000}],
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="inference-primary",
                name="Mac 236 · M3 Ultra",
                department="AI",
                gateway_url="ws://inference-primary",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                is_platform_default=True,
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["inference", "lora"]},
                }),
                is_active=True,
            ),
            LearningEvent(
                id="le-full-history-bg",
                event_type="decision.feedback",
                source_type="decision_log",
                source_id="decision-full-history-bg",
                source_hash="decision-full-history-bg",
                department="AI小组",
                redacted_summary="后台全量 4B 微调样本。",
            ),
        ])
        for idx in range(4):
            db.add(LearningArtifact(
                id=f"la-full-history-bg-{idx}",
                event_id="le-full-history-bg",
                artifact_kind="training_sample",
                artifact_hash=f"la-full-history-bg-{idx}",
                target_type="skill",
                target_id="source-skill",
                department="AI小组",
                skill_id="source-skill",
                title=f"后台全量样本 {idx}",
                summary="用于验证后台自动微调。",
                content_json={"input": {"question": f"输入 {idx}"}, "output": {"summary": f"输出 {idx}"}},
                labels_json=["training"],
                quality_score=0.9,
                confidence=0.9,
                status="materialized",
                sink_type="training_sample",
            ))
        await db.commit()

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id in {"training-primary", "inference-primary"})

    async def fake_prepare(db, user, row, **kwargs):
        return {
            "status": "completed",
            "captured": {"skipped": True},
            "materialized": {"materialized": 0},
            "raw_payload_returned": False,
        }

    monkeypatch.setattr(training_service, "_prepare_full_history_training_data", fake_prepare)

    async with async_session_factory() as db:
        result = await training_service.advance_full_history_finetune_automation(db, limit=1)
        run = (
            await db.execute(
                select(TrainingFullHistoryAutomationRun).order_by(TrainingFullHistoryAutomationRun.created_at.desc())
            )
        ).scalar_one()
        job = (
            await db.execute(
                select(TrainingJob).where(TrainingJob.target_skill_id == "skillforge-finetuned-model-chat")
            )
        ).scalar_one()

    assert result["status"] == "advanced"
    assert run.trigger_type == "scheduled"
    assert run.status == "running"
    assert run.current_step == "approve_job"
    assert run.current_job_id == job.id
    assert run.training_gateway_id == "training-primary"
    assert run.deployment_gateway_id == "inference-primary"
    assert run.data_preparation_json["status"] == "completed"


@pytest.mark.asyncio
async def test_training_job_auto_route_uses_online_gb10_pool_before_legacy_fallback(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    training_ops = ["training.submit_job", "training.collect_result", "training.inference"]
    async with async_session_factory() as db:
        db.add_all([
            OpenClawInstance(
                id="training-primary",
                name="GB10 237 · NVIDIA GB10",
                department="AI",
                gateway_url="ws://training-primary",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                is_platform_default=True,
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["qlora", "lora", "merge"]},
                    "gpu": [{"name": "NVIDIA GB10", "vram_total_mb": 124620, "vram_free_mb": 100000}],
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="platform-gb10-238",
                name="GB10 238 · NVIDIA GB10",
                department="AI",
                gateway_url="ws://platform-gb10-238",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                is_platform_default=True,
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["qlora", "lora", "merge"]},
                    "gpu": [{"name": "NVIDIA GB10", "vram_total_mb": 124620, "vram_free_mb": 121000}],
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="legacy-training-4060",
                name="Legacy RTX 4060",
                department="EC",
                gateway_url="ws://legacy-training-4060",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="training",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["qlora", "lora"]},
                    "gpu": [{"name": "NVIDIA RTX 4060", "vram_total_mb": 12288, "vram_free_mb": 11000}],
                }),
                is_active=True,
            ),
        ])
        await db.commit()

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "platform-gb10-238")

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "GB10 池在线节点优先",
            "department": "EC",
            "job_type": "qlora",
            "target_skill_id": "skill-gb10-pool",
        },
    )

    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    assert created["target_gateway_id"] == "platform-gb10-238"
    assert created["gateway_routing"]["agent_contract"]["route_policy"]["gb10_training_pool"] is True


@pytest.mark.asyncio
async def test_training_automation_reroutes_invalid_gateway_to_gb10_without_approval(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="training-primary",
            name="GB10 237 · NVIDIA GB10",
            department="AI",
            gateway_url="ws://training-primary",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            is_platform_default=True,
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {"gateway": True, "supported_tasks": ["lora", "qlora", "eval"], "gpu_count": 1},
                "gpu": [{"name": "NVIDIA GB10", "vram_total_mb": 124620, "vram_free_mb": 100000}],
            }),
            is_active=True,
        ))
        db.add(TrainingJob(
            id="train-reroute-invalid-gateway",
            title="历史错误网关自动寻路",
            department="EC",
            created_by="pytest",
            status="awaiting_review",
            job_type="lora",
            target_skill_id="skill-reroute-invalid",
            target_gateway_id="内容电商",
            dataset_ref="dataset://ec/reroute",
            spec_json={"automation": {"auto_approve_job": True}},
        ))
        await db.commit()

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "training-primary")

    run_resp = await client.post(
        "/api/training/automation/run",
        json={
            "materialize_learning": False,
            "promote_learning_samples": False,
            "create_datasets": False,
            "datasets": False,
            "jobs": True,
            "approve": False,
            "dispatch": False,
            "evaluate": False,
            "max_jobs": 1,
            "trigger": "pytest_route_only",
        },
    )
    assert run_resp.status_code == 200, run_resp.text
    payload = run_resp.json()
    action = next(item for item in payload["actions"] if item["target_id"] == "train-reroute-invalid-gateway")
    assert action["actions"][0]["action"] == "route"
    assert action["actions"][0]["target_gateway_id"] == "training-primary"
    assert action["job"]["status"] == "awaiting_review"
    assert action["job"]["target_gateway_id"] == "training-primary"
    assert action["job"]["gateway_routing"]["reroute_reason"] == "target_gateway_not_found"


@pytest.mark.asyncio
async def test_training_automation_prefers_gb10_over_legacy_training_gateway(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob

    training_ops = ["training.submit_job", "training.collect_result", "training.inference"]
    async with async_session_factory() as db:
        db.add_all([
            OpenClawInstance(
                id="training-primary",
                name="GB10 237 · NVIDIA GB10",
                department="AI",
                gateway_url="ws://training-primary",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                is_platform_default=True,
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["lora", "qlora", "eval"], "gpu_count": 1},
                    "gpu": [{"name": "NVIDIA GB10", "vram_total_mb": 124620, "vram_free_mb": 100000}],
                }),
                is_active=True,
            ),
            OpenClawInstance(
                id="内容电商",
                name="传统电商",
                department="销售二部",
                gateway_url="ws://legacy-content-ecommerce",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="mixed",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": training_ops,
                    "training": {"gateway": True, "supported_tasks": ["lora", "qlora", "eval"], "gpu_count": 1},
                    "gpu": [{"name": "NVIDIA RTX 4060", "vram_total_mb": 12288, "vram_free_mb": 10000}],
                }),
                is_active=True,
            ),
        ])
        db.add(TrainingJob(
            id="train-reroute-legacy-content",
            title="历史内容电商网关重选 GB10",
            department="示例品牌内容电商运营部",
            created_by="pytest",
            status="awaiting_review",
            job_type="lora",
            target_skill_id="skill-reroute-legacy",
            target_gateway_id="内容电商",
            dataset_ref="dataset://ec/reroute-legacy",
            spec_json={
                "automation": {"auto_approve_job": True},
                "dataset": {
                    "bridge_transfer": {
                        "enabled": True,
                        "source_gateway_id": "training-primary",
                        "target_gateway_id": "内容电商",
                    }
                },
            },
        ))
        await db.commit()

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "training-primary")

    run_resp = await client.post(
        "/api/training/automation/run",
        json={
            "materialize_learning": False,
            "promote_learning_samples": False,
            "create_datasets": False,
            "datasets": False,
            "jobs": True,
            "approve": False,
            "dispatch": False,
            "evaluate": False,
            "max_jobs": 1,
            "trigger": "pytest_route_primary",
        },
    )
    assert run_resp.status_code == 200, run_resp.text
    payload = run_resp.json()
    action = next(item for item in payload["actions"] if item["target_id"] == "train-reroute-legacy-content")
    assert action["actions"][0]["action"] == "route"
    assert action["actions"][0]["target_gateway_id"] == "training-primary"
    assert action["job"]["target_gateway_id"] == "training-primary"
    assert action["job"]["gateway_routing"]["previous_gateway_id"] == "内容电商"
    assert action["job"]["gateway_routing"]["reroute_reason"] == "prefer_primary_training_gateway"


@pytest.mark.asyncio
async def test_training_job_rejects_manual_runtime_agent_even_if_capable(client):
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="runtime-training-capable",
            name="EC 执行 Agent",
            department="EC",
            gateway_url="ws://runtime-training-capable",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="skill_runtime",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {"gateway": True, "supported_tasks": ["lora"]},
            }),
            is_active=True,
        ))
        await db.commit()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "误选执行 Agent",
            "department": "EC",
            "job_type": "lora",
            "target_gateway_id": "runtime-training-capable",
        },
    )

    assert create_resp.status_code == 422
    assert "not marked as a training Agent" in create_resp.json()["error"]["detail"]["detail"]


@pytest.mark.asyncio
async def test_training_job_rejects_missing_manual_gateway(client):
    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "不存在节点不能作为训练目标",
            "department": "EC",
            "job_type": "lora",
            "target_gateway_id": "missing-training-agent",
        },
    )

    assert create_resp.status_code == 404
    assert create_resp.json()["error"]["detail"]["detail"] == "target training gateway not found"


@pytest.mark.asyncio
async def test_deployable_training_job_rejects_incomplete_lifecycle_agent(client):
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="submit-only-training",
            name="只会提交的训练 Agent",
            department="EC",
            gateway_url="ws://submit-only-training",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job"],
                "training": {"gateway": True, "supported_tasks": ["lora", "eval"]},
            }),
            is_active=True,
        ))
        await db.commit()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "不能闭环的微调任务",
            "department": "EC",
            "job_type": "lora",
            "target_gateway_id": "submit-only-training",
        },
    )

    assert create_resp.status_code == 422
    detail = create_resp.json()["error"]["detail"]
    assert detail["detail"] == "target gateway does not support deployable training lifecycle"
    assert detail["missing_ops"] == ["training.collect_result", "training.inference"]

    eval_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "纯评估任务允许提交型 Agent",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "submit-only-training",
        },
    )
    assert eval_resp.status_code == 200, eval_resp.text
    assert eval_resp.json()["target_gateway_id"] == "submit-only-training"


@pytest.mark.asyncio
async def test_training_skill_candidate_requires_data_readiness(client):
    from app.database import async_session_factory
    from app.skills.core.models import Skill

    async with async_session_factory() as db:
        db.add(Skill(
            id="skill-recommend",
            name="商品推荐",
            department="EC",
            status="active",
            owner="admin",
            risk_level="R2",
        ))
        await db.commit()

    readiness_resp = await client.get("/api/training/skills/skill-recommend/candidate-readiness")
    assert readiness_resp.status_code == 200
    readiness = readiness_resp.json()
    assert readiness["passed"] is False
    assert readiness["sample_counts"]["sft_samples"] == 0

    create_resp = await client.post("/api/training/skills/skill-recommend/candidate", json={})
    assert create_resp.status_code == 400
    assert create_resp.json()["error"]["code"] == "TRAINING_DATA_NOT_READY"


@pytest.mark.asyncio
async def test_training_skill_candidate_creates_reviewed_job_from_decision_logs(client):
    from app.common.audit import AuditLog
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.execution.models import DecisionLog
    from app.skills.core.models import Skill
    from app.training.models import TrainingJob

    await _seed_default_training_gateway()

    async with async_session_factory() as db:
        db.add(Skill(
            id="skill-recommend",
            name="商品推荐",
            department="EC",
            status="active",
            owner="admin",
            risk_level="R2",
        ))
        now = now_bjt()
        for index in range(120):
            db.add(DecisionLog(
                run_id=f"run-{index}",
                skill_id="skill-recommend",
                input_snapshot={"sku_id": index},
                output_result={"todos": [{"title": f"动作 {index}"}]},
                suggested_action={"type": "todo_dispatch", "rank": index % 3},
                user_action="rejected" if index < 10 else ("completed" if index < 60 else None),
                rating=4 if 10 <= index < 60 else None,
                is_sandbox=False,
                created_at=now,
            ))
        await db.commit()

    create_resp = await client.post(
        "/api/training/skills/skill-recommend/candidate",
        json={"target_gateway_id": "node-1", "model_family": "todo_action_ranker"},
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["status"] == "awaiting_review"
    assert created["target_skill_id"] == "skill-recommend"
    assert created["target_gateway_id"] == "node-1"
    assert created["dataset_ref"] == "decision-log://skill-recommend/action-outcome/latest"
    assert created["spec"]["source"] == "skillstudio_data_asset"
    assert created["spec"]["model_family"] == "todo_action_ranker"
    assert created["spec"]["dataset"]["sample_counts"]["sft_samples"] == 120
    assert created["spec"]["dataset"]["sample_counts"]["action_outcome_samples"] == 120

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        assert job is not None
        assert job.status == "awaiting_review"
        actions = (await db.execute(
            select(AuditLog).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert [entry.action for entry in actions] == ["training_job.create"]
    assert actions[0].detail["source"] == "skillstudio_data_asset"


@pytest.mark.asyncio
async def test_training_run_candidate_creates_reviewed_job_from_run_analysis(client):
    from app.common.audit import AuditLog
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun, ExecutionStep
    from app.skills.core.models import Skill
    from app.training.models import TrainingJob

    async with async_session_factory() as db:
        db.add(Skill(
            id="skill-recommend",
            name="商品推荐",
            department="EC",
            status="active",
            owner="admin",
            risk_level="R2",
        ))
        db.add(ExecutionRun(
            id="run-training-1",
            skill_id="skill-recommend",
            trigger_type="manual",
            run_mode="manual_real",
            status="failed",
            summary="运行失败，需要调优规则",
            started_at=now_bjt(),
            metadata_json={"department": "EC", "access_token": "secret-token-should-not-leak"},
        ))
        db.add(ExecutionStep(
            run_id="run-training-1",
            skill_id="skill-recommend",
            step_order=1,
            status="failed",
            input_data={"api_key": "step-secret-should-not-leak"},
            output_data={"todos": [{"title": "检查库存"}]},
            error_message="timeout",
        ))
        db.add(DecisionLog(
            run_id="run-training-1",
            skill_id="skill-recommend",
            input_snapshot={"cookie": "decision-secret-should-not-leak"},
            output_result={"todos": [{"title": "调整推荐"}]},
            suggested_action={"type": "todo_dispatch"},
            user_action="rejected",
            rating=2,
            is_sandbox=False,
            created_at=now_bjt(),
        ))
        await db.commit()

    create_resp = await client.post(
        "/api/training/runs/run-training-1/candidate",
        json={
            "analysis_model": "deepseek-v4-pro",
            "analysis_prompt_hash": "hash-run-training-1",
            "raw_counts": {"execution_steps": 1, "decision_logs": 1},
            "analysis_summary": "复盘建议：补齐库存检查并降低误派发。",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    assert created["status"] == "awaiting_review"
    assert created["target_skill_id"] == "skill-recommend"
    assert created["dataset_ref"] == "execution-run://run-training-1/redacted-analysis-context"
    assert created["spec"]["source"] == "run_trace_analysis"
    assert created["spec"]["dataset"]["sample_counts"]["execution_steps"] == 1
    assert created["spec"]["dataset"]["sample_counts"]["decision_logs"] == 1
    assert created["spec"]["lineage"]["run_id"] == "run-training-1"
    assert created["spec"]["analysis"]["prompt_hash"] == "hash-run-training-1"
    encoded = json.dumps(created, ensure_ascii=False)
    assert "secret-token-should-not-leak" not in encoded
    assert "step-secret-should-not-leak" not in encoded
    assert "decision-secret-should-not-leak" not in encoded

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        assert job is not None
        assert job.status == "awaiting_review"
        entries = (await db.execute(
            select(AuditLog).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert [entry.action for entry in entries] == ["training_job.create"]
    assert entries[0].detail["source"] == "run_trace_analysis"


@pytest.mark.asyncio
async def test_training_run_candidate_rejects_sandbox_run_without_override(client):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.execution.models import ExecutionRun, ExecutionStep
    from app.skills.core.models import Skill

    async with async_session_factory() as db:
        db.add(Skill(
            id="skill-recommend",
            name="商品推荐",
            department="EC",
            status="active",
            owner="admin",
            risk_level="R2",
        ))
        db.add(ExecutionRun(
            id="run-sandbox-1",
            skill_id="skill-recommend",
            trigger_type="manual",
            run_mode="sandbox_test",
            status="completed",
            started_at=now_bjt(),
        ))
        db.add(ExecutionStep(
            run_id="run-sandbox-1",
            skill_id="skill-recommend",
            step_order=1,
            status="completed",
            output_data={"ok": True},
        ))
        await db.commit()

    create_resp = await client.post("/api/training/runs/run-sandbox-1/candidate", json={})
    assert create_resp.status_code == 400
    assert create_resp.json()["error"]["code"] == "TRAINING_RUN_SANDBOX_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_training_job_dispatch_submits_to_bridge(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-1",
            name="训练网关 A",
            department="EC",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora", "eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
        ))
        await db.commit()

    calls = []

    async def fake_submit_training_job(self, payload, *, timeout=30):
        calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "accepted": True,
            "status": "running",
            "gateway_job_id": "node-1:train",
            "worker_id": "worker-1",
            "access_token": "dispatch-token-should-not-leak",
            "headers": {"Authorization": "Bearer dispatch-header-should-not-leak"},
        }

    monkeypatch.setattr(AIClawClient, "submit_training_job", fake_submit_training_job)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "训练商品下滑动作推荐模型",
            "department": "EC",
            "job_type": "lora",
            "target_gateway_id": "node-1",
            "dataset_ref": "dataset://ec/actions/v1",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve")
    assert approve_resp.status_code == 200

    dispatch_resp = await client.post(f"/api/training/jobs/{created['id']}/dispatch")
    assert dispatch_resp.status_code == 200
    dispatched = dispatch_resp.json()

    assert dispatched["status"] == "running"
    assert dispatched["failure_stage"] is None
    assert dispatched["tasks"][0]["status"] == "running"
    assert dispatched["tasks"][0]["worker_id"] == "worker-1"
    encoded_dispatch = json.dumps(dispatched, ensure_ascii=False)
    assert "dispatch-token-should-not-leak" not in encoded_dispatch
    assert "dispatch-header-should-not-leak" not in encoded_dispatch
    assert calls[0]["instance_id"] == "node-1"
    assert calls[0]["payload"]["job_id"] == created["id"]
    assert calls[0]["payload"]["control"]["callback_path"].endswith(f"/api/training/jobs/{created['id']}/gateway-result")
    assert calls[0]["payload"]["control"]["agent_id"] == "node-1"
    assert calls[0]["payload"]["control"]["agent_purpose"] == "training"
    assert calls[0]["payload"]["control"]["agent_contract"]["input_channels"] == [
        "training_job.gateway_payload",
        "learning_artifacts.dataset_package",
    ]
    assert calls[0]["payload"]["control"]["agent_contract"]["submission_ready"] is True
    assert calls[0]["payload"]["control"]["agent_contract"]["lifecycle_ready"] is True
    assert dispatched["tasks"][0]["metrics"]["agent_contract"]["missing_ops"] == []

    async with async_session_factory() as db:
        entries = (await db.execute(
            select(AuditLog).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    actions = [entry.action for entry in entries]
    assert actions == ["training_job.create", "training_job.approve", "training_job.dispatch"]
    encoded_audit = json.dumps([entry.detail for entry in entries], ensure_ascii=False)
    assert "dispatch-token-should-not-leak" not in encoded_audit
    assert "dispatch-header-should-not-leak" not in encoded_audit
    dispatch_audit = next(entry.detail for entry in entries if entry.action == "training_job.dispatch")
    assert dispatch_audit["agent_contract"]["submission_ready"] is True
    assert dispatch_audit["agent_contract"]["lifecycle_ready"] is True


@pytest.mark.asyncio
async def test_training_job_cancel_running_job_calls_bridge(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-1",
            name="训练网关 A",
            department="EC",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.cancel_job"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
        ))
        await db.commit()

    cancel_calls = []

    async def fake_submit_training_job(self, payload, *, timeout=30):
        return {
            "accepted": True,
            "status": "running",
            "gateway_job_id": "node-1:train",
            "worker_id": "worker-1",
    }

    async def fake_cancel_training_job(self, payload, *, timeout=15):
        cancel_calls.append({
            "instance_id": self.instance_id,
            "gateway_kind": self.gateway_kind,
            "payload": payload,
        })
        return {
            "cancelled": True,
            "job_id": payload["job_id"],
            "status": "cancelled",
            "access_token": "cancel-token-should-not-leak",
            "headers": {"Authorization": "Bearer cancel-header-should-not-leak"},
        }

    monkeypatch.setattr(AIClawClient, "submit_training_job", fake_submit_training_job)
    monkeypatch.setattr(AIClawClient, "cancel_training_job", fake_cancel_training_job)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")
    await client.post(f"/api/training/jobs/{created['id']}/dispatch")

    cancel_resp = await client.post(f"/api/training/jobs/{created['id']}/cancel")
    assert cancel_resp.status_code == 200
    cancelled = cancel_resp.json()

    assert cancelled["status"] == "cancelled"
    assert cancelled["failure_stage"] is None
    assert cancelled["tasks"][0]["status"] == "cancelled"
    assert cancelled["tasks"][0]["metrics"]["cancel"]["ok"] is True
    encoded_cancel = json.dumps(cancelled, ensure_ascii=False)
    assert "cancel-token-should-not-leak" not in encoded_cancel
    assert "cancel-header-should-not-leak" not in encoded_cancel
    assert cancel_calls == [{
        "instance_id": "node-1",
        "gateway_kind": "openclaw",
        "payload": {
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
        },
    }]

    async with async_session_factory() as db:
        entries = (await db.execute(
            select(AuditLog).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    actions = [entry.action for entry in entries]
    assert actions == [
        "training_job.create",
        "training_job.approve",
        "training_job.dispatch",
        "training_job.cancel",
    ]
    encoded_audit = json.dumps([entry.detail for entry in entries], ensure_ascii=False)
    assert "cancel-token-should-not-leak" not in encoded_audit
    assert "cancel-header-should-not-leak" not in encoded_audit


@pytest.mark.asyncio
async def test_training_job_cancel_running_job_keeps_running_on_bridge_failure(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-1",
            name="训练网关 A",
            department="EC",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.cancel_job"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
        ))
        await db.commit()

    async def fake_submit_training_job(self, payload, *, timeout=30):
        return {
            "accepted": True,
            "status": "running",
            "gateway_job_id": "node-1:train",
            "worker_id": "worker-1",
        }

    async def fake_cancel_training_job(self, payload, *, timeout=15):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": "bridge offline access_token=cancel-error-should-not-leak"})

    monkeypatch.setattr(AIClawClient, "submit_training_job", fake_submit_training_job)
    monkeypatch.setattr(AIClawClient, "cancel_training_job", fake_cancel_training_job)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")
    await client.post(f"/api/training/jobs/{created['id']}/dispatch")

    cancel_resp = await client.post(f"/api/training/jobs/{created['id']}/cancel")
    assert cancel_resp.status_code == 200
    result = cancel_resp.json()

    assert result["status"] == "running"
    assert result["failure_stage"] == "cancel"
    assert result["tasks"][0]["status"] == "running"
    assert result["tasks"][0]["metrics"]["cancel"]["ok"] is False
    assert "bridge offline" in result["tasks"][0]["error_message"]
    assert "cancel-error-should-not-leak" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_training_job_force_cancel_only_after_cancel_failure(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.common.audit import AuditLog
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-1",
            name="训练网关 A",
            department="EC",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.cancel_job"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
        ))
        await db.commit()

    async def fake_submit_training_job(self, payload, *, timeout=30):
        return {
            "accepted": True,
            "status": "running",
            "gateway_job_id": "node-1:train",
            "worker_id": "worker-1",
        }

    async def fake_cancel_training_job(self, payload, *, timeout=15):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": "bridge offline"})

    monkeypatch.setattr(AIClawClient, "submit_training_job", fake_submit_training_job)
    monkeypatch.setattr(AIClawClient, "cancel_training_job", fake_cancel_training_job)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")
    await client.post(f"/api/training/jobs/{created['id']}/dispatch")

    premature_resp = await client.post(
        f"/api/training/jobs/{created['id']}/force-cancel",
        json={"reason": "bridge 已离线，需要人工兜底"},
    )
    assert premature_resp.status_code == 400

    await client.post(f"/api/training/jobs/{created['id']}/cancel")
    force_resp = await client.post(
        f"/api/training/jobs/{created['id']}/force-cancel",
        json={"reason": "bridge 已离线，需要人工兜底取消训练"},
    )
    assert force_resp.status_code == 200
    forced = force_resp.json()

    assert forced["status"] == "cancelled"
    assert forced["failure_stage"] is None
    assert forced["tasks"][0]["status"] == "cancelled"
    assert forced["tasks"][0]["metrics"]["force_cancel"]["cleanup_pending"] is True

    async with async_session_factory() as db:
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert actions == [
        "training_job.create",
        "training_job.approve",
        "training_job.dispatch",
        "training_job.cancel",
        "training_job.force_cancel",
    ]


@pytest.mark.asyncio
async def test_training_job_logs_are_read_from_bridge_and_redacted(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-1",
            name="训练网关 A",
            department="EC",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.stream_logs"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
        ))
        await db.commit()

    log_calls = []

    async def fake_stream_training_logs(self, payload, *, timeout=15):
        log_calls.append({
            "instance_id": self.instance_id,
            "gateway_kind": self.gateway_kind,
            "payload": payload,
        })
        return {
            "job_id": payload["job_id"],
            "status": "running",
            "lines": [
                {"ts": "2026-05-21T10:00:00Z", "type": "accepted", "message": "accepted api_key=abcdefghijklmnop"},
                "bearer secret-token-value",
            ],
        }

    monkeypatch.setattr(AIClawClient, "stream_training_logs", fake_stream_training_logs)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()

    logs_resp = await client.get(f"/api/training/jobs/{created['id']}/logs")
    assert logs_resp.status_code == 200
    logs = logs_resp.json()

    assert logs["available"] is True
    assert logs["status"] == "running"
    assert log_calls == [{
        "instance_id": "node-1",
        "gateway_kind": "openclaw",
        "payload": {"job_id": created["id"]},
    }]
    assert logs["lines"][0]["type"] == "accepted"
    assert "[REDACTED]" in logs["lines"][0]["message"]
    assert "abcdefghijklmnop" not in json.dumps(logs, ensure_ascii=False)
    assert "secret-token-value" not in json.dumps(logs, ensure_ascii=False)


@pytest.mark.asyncio
async def test_training_job_logs_return_unavailable_when_bridge_fails(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-1",
            name="训练网关 A",
            department="EC",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.stream_logs"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
        ))
        await db.commit()

    async def fake_stream_training_logs(self, payload, *, timeout=15):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": "bridge offline"})

    monkeypatch.setattr(AIClawClient, "stream_training_logs", fake_stream_training_logs)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()

    logs_resp = await client.get(f"/api/training/jobs/{created['id']}/logs")
    assert logs_resp.status_code == 200
    logs = logs_resp.json()
    assert logs["available"] is False
    assert logs["lines"] == []
    assert "bridge offline" in logs["error"]


@pytest.mark.asyncio
async def test_training_gateway_result_updates_job_with_scoped_token(client):
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    await _seed_default_training_gateway(supported_tasks=["eval"])

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "训练商品下滑动作推荐模型",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
            "dataset_ref": "dataset://ec/actions/v1",
        },
    )
    created = create_resp.json()
    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["gateway_payload"]["control"]["callback_token"] == "***"

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {"eval_score": 0.91},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/model"}],
        },
    )
    assert result_resp.status_code == 200
    result = result_resp.json()
    assert result["status"] == "completed"
    assert result["failure_stage"] is None
    assert result["tasks"][0]["status"] == "completed"
    assert result["tasks"][0]["progress"] == 1
    assert result["tasks"][0]["metrics"]["gateway_result"]["metrics"]["eval_score"] == 0.91

    async with async_session_factory() as db:
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert actions == [
        "training_job.create",
        "training_job.approve",
        "training_job.gateway_result",
    ]


@pytest.mark.asyncio
async def test_training_gateway_result_redacts_metrics_artifacts_and_logs_url(client):
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    await _seed_default_training_gateway(supported_tasks=["eval"])

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {
                "eval_score": 0.91,
                "access_token": "metric-token-should-not-leak",
                "nested": {"authorization": "Bearer nested-secret-should-not-leak"},
            },
            "artifacts": [
                {
                    "type": "adapter",
                    "uri": "https://object-store/model.bin?token=artifact-token-should-not-leak",
                    "headers": {"Authorization": "Bearer artifact-header-should-not-leak"},
                }
            ],
            "logs_url": "https://logs.local/job.log?access_token=log-token-should-not-leak",
            "error_message": "failed with api_key=error-key-should-not-leak",
        },
    )
    assert result_resp.status_code == 200
    result = result_resp.json()
    encoded = json.dumps(result, ensure_ascii=False)

    assert result["tasks"][0]["metrics"]["gateway_result"]["metrics"]["eval_score"] == 0.91
    assert result["tasks"][0]["metrics"]["gateway_result"]["metrics"]["access_token"] == "[REDACTED]"
    assert "[REDACTED]" in encoded
    assert "metric-token-should-not-leak" not in encoded
    assert "nested-secret-should-not-leak" not in encoded
    assert "artifact-token-should-not-leak" not in encoded
    assert "artifact-header-should-not-leak" not in encoded
    assert "log-token-should-not-leak" not in encoded
    assert "error-key-should-not-leak" not in encoded


@pytest.mark.asyncio
async def test_training_job_artifacts_returns_safe_manifest_without_download_tokens(client):
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    await _seed_default_training_gateway(supported_tasks=["eval"])

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {},
            "artifacts": [
                {
                    "type": "adapter",
                    "uri": "https://object-store/artifacts/model.bin?X-Amz-Signature=artifact-signature-should-not-leak",
                    "sha256": "a" * 64,
                    "size_bytes": 123,
                    "headers": {"Authorization": "Bearer artifact-header-should-not-leak"},
                }
            ],
        },
    )

    artifacts_resp = await client.get(f"/api/training/jobs/{created['id']}/artifacts")
    assert artifacts_resp.status_code == 200
    payload = artifacts_resp.json()
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["download_proxy_available"] is True
    assert payload["total"] == 1
    assert payload["items"][0]["type"] == "adapter"
    assert payload["items"][0]["name"] == "model.bin"
    assert payload["items"][0]["uri"] == "https://object-store/artifacts/model.bin"
    assert payload["items"][0]["sha256"] == "a" * 64
    artifact = payload["items"][0]
    assert artifact["downloadable"] is True
    assert artifact["download_url"].endswith(
        f"/api/training/jobs/{created['id']}/artifacts/{artifact['id']}/download"
    )
    assert "uri_query_stripped" in payload["items"][0]["warnings"]
    assert "artifact-signature-should-not-leak" not in encoded
    assert "artifact-header-should-not-leak" not in encoded


@pytest.mark.asyncio
async def test_training_job_artifact_download_proxies_through_bridge(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob

    content = b"model"
    sha = hashlib.sha256(content).hexdigest()
    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-1",
            name="训练节点",
            department="EC",
            gateway_url="ws://node-1",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            bridge_gateway_kind="aiclaw",
            bridge_capabilities_json=json.dumps({
                "ops": [
                    "training.submit_job",
                    "training.collect_result",
                    "training.inference",
                    "training.download_artifact",
                ],
                "training": {"gateway": True, "supported_tasks": ["lora"]},
            }),
            is_active=True,
        ))
        await db.commit()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "下载模型",
            "department": "EC",
            "job_type": "lora",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {},
            "artifacts": [{
                "type": "adapter",
                "name": "model.bin",
                "uri": "artifact://train/model.bin",
                "sha256": sha,
                "size_bytes": len(content),
            }],
        },
    )

    calls = []

    async def fake_download_artifact(self, payload, *, timeout=60):
        calls.append({"payload": payload, "timeout": timeout})
        return {
            "filename": "model.bin",
            "content_type": "application/octet-stream",
            "content_base64": "bW9kZWw=",
            "sha256": sha,
        }

    monkeypatch.setattr(AIClawClient, "download_training_artifact", fake_download_artifact)

    artifacts_resp = await client.get(f"/api/training/jobs/{created['id']}/artifacts")
    artifact_id = artifacts_resp.json()["items"][0]["id"]
    resp = await client.get(f"/api/training/jobs/{created['id']}/artifacts/{artifact_id}/download")

    assert resp.status_code == 200
    assert resp.content == content
    assert resp.headers["content-type"] == "application/octet-stream"
    assert "model.bin" in resp.headers["content-disposition"]
    assert calls == [{
        "payload": {
            "job_id": created["id"],
            "artifact_id": artifact_id,
            "artifact_name": "model.bin",
            "sha256": sha,
        },
        "timeout": 60,
    }]


@pytest.mark.asyncio
async def test_training_job_evaluate_completed_job_records_gate_result(client):
    from app.auth.dependencies import get_current_user
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    raw_token = "test-training-evaluate-pass-token"
    await _seed_cli_session_token(raw_token, department="EC")
    client._transport.app.dependency_overrides.pop(get_current_user, None)
    headers = {"Authorization": f"Bearer {raw_token}"}

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        headers=headers,
        json={
            "title": "训练商品下滑动作推荐模型",
            "department": "EC",
            "job_type": "lora",
            "target_gateway_id": "node-1",
            "spec": {"eval_gate": {"min_win_rate": 0.55}},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve", headers=headers)
    assert approve_resp.status_code == 200, approve_resp.text

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {"win_rate": 0.61},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/model", "sha256": "a" * 64}],
        },
    )
    assert result_resp.status_code == 200

    evaluate_resp = await client.post(f"/api/training/jobs/{created['id']}/evaluate", headers=headers)
    assert evaluate_resp.status_code == 200
    evaluated = evaluate_resp.json()
    evaluation_task = evaluated["tasks"][-1]

    assert evaluated["status"] == "completed"
    assert evaluated["failure_stage"] is None
    assert evaluation_task["status"] == "completed"
    assert evaluation_task["metrics"]["op"] == "training.evaluate"
    assert evaluation_task["metrics"]["passed"] is True
    assert evaluation_task["metrics"]["checks"][0]["name"] == "min_win_rate"
    assert evaluation_task["metrics"]["checks"][0]["actual"] == 0.61

    async with async_session_factory() as db:
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert actions == [
        "training_job.create",
        "training_job.approve",
        "training_job.gateway_result",
        "training_job.evaluate",
    ]


@pytest.mark.asyncio
async def test_training_job_evaluate_manual_review_gate_passes_but_deployment_stays_review(client):
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    raw_token = "test-training-manual-review-gate-token"
    await _seed_cli_session_token(raw_token, department="EC")
    client._transport.app.dependency_overrides.pop(get_current_user, None)
    headers = {"Authorization": f"Bearer {raw_token}"}

    await _seed_default_training_gateway(supported_tasks=["qlora", "lora", "eval"])

    create_resp = await client.post(
        "/api/training/jobs",
        headers=headers,
        json={
            "title": "训练低消耗视频诊断模型",
            "department": "EC",
            "job_type": "qlora",
            "target_gateway_id": "node-1",
            "spec": {
                "model_family": "samplebrand-video-low-consumption-operator-v1:qwen3.5-4b-qlora",
                "eval_gate": {
                    "min_win_rate": 0.55,
                    "manual_review_required": True,
                    "require_artifact_sha256": True,
                },
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve", headers=headers)
    assert approve_resp.status_code == 200, approve_resp.text

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {"win_rate": 0.67, "eval_evaluated_samples": 4},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/model", "sha256": "d" * 64}],
        },
    )
    assert result_resp.status_code == 200

    evaluate_resp = await client.post(f"/api/training/jobs/{created['id']}/evaluate", headers=headers)
    assert evaluate_resp.status_code == 200
    evaluated = evaluate_resp.json()
    checks = {item["name"]: item for item in evaluated["tasks"][-1]["metrics"]["checks"]}

    assert evaluated["status"] == "completed"
    assert checks["min_win_rate"]["passed"] is True
    assert checks["manual_review_required"] == {
        "name": "manual_review_required",
        "metric": "deployment.review",
        "operator": "requires_review",
        "expected": True,
        "actual": True,
        "passed": True,
    }
    assert checks["artifact_sha256"]["passed"] is True

    deploy_resp = await client.post(
        f"/api/training/jobs/{created['id']}/deploy-request",
        headers=headers,
        json={
            "target_skill_ids": ["samplebrand-video-low-consumption-operator-v1"],
            "model_family": "samplebrand-video-low-consumption-operator-v1:qwen3.5-4b-qlora",
            "rollout_percent": 10,
            "reason": "真实评估通过后提交人工灰度审批",
        },
    )
    assert deploy_resp.status_code == 200
    deployment = deploy_resp.json()["deployment"]
    assert deployment["status"] == "awaiting_review"
    assert deployment["rollout_percent"] == 10


@pytest.mark.asyncio
async def test_training_job_evaluate_skips_win_rate_gate_without_eval_samples(client):
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    raw_token = "test-training-no-eval-samples-token"
    await _seed_cli_session_token(raw_token, department="EC")
    client._transport.app.dependency_overrides.pop(get_current_user, None)
    headers = {"Authorization": f"Bearer {raw_token}"}

    await _seed_default_training_gateway(supported_tasks=["qlora", "lora", "eval"])

    create_resp = await client.post(
        "/api/training/jobs",
        headers=headers,
        json={
            "title": "训练低消耗视频诊断模型",
            "department": "EC",
            "job_type": "qlora",
            "target_gateway_id": "node-1",
            "spec": {
                "eval_gate": {
                    "min_win_rate": 0.55,
                    "manual_review_required": True,
                    "require_artifact_sha256": True,
                },
                "deployment": {
                    "auto_request": True,
                    "target_skill_ids": ["samplebrand-video-low-consumption-operator-v1"],
                    "model_family": "samplebrand-video-low-consumption-operator-v1:learning-loop-adapter",
                    "rollout_percent": 10,
                },
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve", headers=headers)
    assert approve_resp.status_code == 200, approve_resp.text

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {
                "win_rate": 0.0,
                "eval_samples": 0,
                "eval_requested_samples": 0,
                "eval_evaluated_samples": 0,
                "eval_generation_success_rate": 0.0,
            },
            "artifacts": [{"type": "adapter", "uri": "artifact://train/model", "sha256": "e" * 64}],
        },
    )
    assert result_resp.status_code == 200

    evaluate_resp = await client.post(f"/api/training/jobs/{created['id']}/evaluate", headers=headers)
    assert evaluate_resp.status_code == 200
    evaluated = evaluate_resp.json()
    evaluation_task = evaluated["tasks"][-1]
    checks = {item["name"]: item for item in evaluation_task["metrics"]["checks"]}

    assert evaluated["status"] == "completed"
    assert evaluated["failure_stage"] is None
    assert evaluation_task["status"] == "completed"
    assert evaluation_task["metrics"]["passed"] is True
    assert checks["min_win_rate"]["passed"] is True
    assert checks["min_win_rate"]["skipped"] is True
    assert checks["min_win_rate"]["operator"] == "skipped_no_eval_samples"
    assert checks["artifact_sha256"]["passed"] is True
    assert evaluation_task["metrics"]["auto_deployment_request"]["status"] == "created"
    assert evaluated["deployments"][0]["status"] == "awaiting_review"


@pytest.mark.asyncio
async def test_training_job_evaluate_marks_job_failed_when_gate_fails(client):
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    raw_token = "test-training-evaluate-fail-token"
    await _seed_cli_session_token(raw_token, department="EC")
    client._transport.app.dependency_overrides.pop(get_current_user, None)
    headers = {"Authorization": f"Bearer {raw_token}"}

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        headers=headers,
        json={
            "title": "训练商品下滑动作推荐模型",
            "department": "EC",
            "job_type": "lora",
            "target_gateway_id": "node-1",
            "spec": {"eval_gate": {"min_win_rate": 0.8}},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve", headers=headers)
    assert approve_resp.status_code == 200, approve_resp.text

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {"win_rate": 0.51},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/model", "sha256": "b" * 64}],
        },
    )

    evaluate_resp = await client.post(f"/api/training/jobs/{created['id']}/evaluate", headers=headers)
    assert evaluate_resp.status_code == 200
    evaluated = evaluate_resp.json()
    evaluation_task = evaluated["tasks"][-1]

    assert evaluated["status"] == "failed"
    assert evaluated["failure_stage"] == "eval"
    assert evaluation_task["status"] == "failed"
    assert evaluation_task["error_message"] == "evaluation gate failed: min_win_rate"
    assert evaluation_task["metrics"]["passed"] is False


@pytest.mark.asyncio
async def test_training_job_evaluate_can_retry_eval_stage_failure(client):
    from app.auth.dependencies import get_current_user
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingJobTask

    raw_token = "test-training-retry-eval-failure-token"
    await _seed_cli_session_token(raw_token, department="EC")
    client._transport.app.dependency_overrides.pop(get_current_user, None)
    headers = {"Authorization": f"Bearer {raw_token}"}

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        headers=headers,
        json={
            "title": "训练商品下滑动作推荐模型",
            "department": "EC",
            "job_type": "lora",
            "target_gateway_id": "node-1",
            "spec": {"eval_gate": {"min_win_rate": 0.8}},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve", headers=headers)

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {
                "win_rate": 0.0,
                "eval_samples": 0,
                "eval_requested_samples": 0,
                "eval_evaluated_samples": 0,
            },
            "artifacts": [{"type": "adapter", "uri": "artifact://train/model", "sha256": "f" * 64}],
        },
    )

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        job.status = "failed"
        job.failure_stage = "eval"
        db.add(TrainingJobTask(
            job_id=created["id"],
            gateway_id="node-1",
            status="failed",
            progress=1,
            metrics_json={
                "op": "training.evaluate",
                "passed": False,
                "checks": [
                    {
                        "name": "min_win_rate",
                        "metric": "win_rate",
                        "operator": ">=",
                        "expected": 0.55,
                        "actual": 0.0,
                        "passed": False,
                    }
                ],
            },
            error_message="evaluation gate failed: min_win_rate",
        ))
        await db.commit()

    retry_eval = await client.post(f"/api/training/jobs/{created['id']}/evaluate", headers=headers)
    assert retry_eval.status_code == 200
    evaluated = retry_eval.json()
    latest_eval = evaluated["tasks"][-1]

    assert evaluated["status"] == "completed"
    assert evaluated["failure_stage"] is None
    assert latest_eval["status"] == "completed"
    assert latest_eval["metrics"]["checks"][0]["operator"] == "skipped_no_eval_samples"


@pytest.mark.asyncio
async def test_training_job_evaluate_requires_completed_job(client):
    await _seed_default_training_gateway(supported_tasks=["eval"])

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    evaluate_resp = await client.post(f"/api/training/jobs/{created['id']}/evaluate")
    assert evaluate_resp.status_code == 400
    assert evaluate_resp.json()["error"]["code"] == "INVALID_STATUS"


@pytest.mark.asyncio
async def test_training_job_deploy_request_creates_awaiting_review_deployment(client):
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.learning.models import LearningFlowEdge
    from app.training.models import TrainingJob, TrainingModelDeployment

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "训练商品下滑动作推荐模型",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-recommend",
            "target_gateway_id": "node-1",
            "spec": {"eval_gate": {"min_win_rate": 0.55}},
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {"win_rate": 0.61},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/model", "sha256": "a" * 64}],
        },
    )
    await client.post(f"/api/training/jobs/{created['id']}/evaluate")

    deploy_resp = await client.post(
        f"/api/training/jobs/{created['id']}/deploy-request",
        json={"reason": "评估通过，提交部署审批", "rollout_percent": 10},
    )
    assert deploy_resp.status_code == 200
    payload = deploy_resp.json()
    deployment = payload["deployment"]

    assert deployment["status"] == "awaiting_review"
    assert deployment["target_skill_ids"] == ["skill-recommend"]
    assert deployment["rollout_percent"] == 10
    assert deployment["artifact_ref"]["sha256"] == "a" * 64
    assert payload["job"]["status"] == "completed"
    assert payload["job"]["failure_stage"] is None
    assert payload["job"]["latest_deployment"]["id"] == deployment["id"]

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        assert stored is not None
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == deployment["id"]).order_by(AuditLog.id)
        )).scalars().all()
        review_edge = (
            await db.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "training_job")
                .where(LearningFlowEdge.from_id == created["id"])
                .where(LearningFlowEdge.to_type == "model_deployment")
                .where(LearningFlowEdge.to_id == deployment["id"])
                .where(LearningFlowEdge.relation == "requested_deployment_review")
            )
        ).scalar_one()
    assert actions == ["training_deployment.request"]
    assert review_edge.metadata_json["review_required"] is True
    assert review_edge.metadata_json["auto_approval"] is False

    journeys = (
        await client.get(
            "/api/learning/flow-journeys",
            params={"days": 7, "skill_id": "skill-recommend", "source_type": "training_job"},
        )
    ).json()
    training_journey = next(item for item in journeys["items"] if item["source_id"] == created["id"])
    review_step = next(
        step for step in training_journey["steps"] if step["action"] == "requested_deployment_review"
    )
    assert review_step["stage"] == "review"
    assert review_step["payload"]["deployment_id"] == deployment["id"]
    assert review_step["payload"]["governance"]["approval_state_machine"] == "training_model_deployment"


@pytest.mark.asyncio
async def test_training_job_evaluate_auto_requests_deployment_review_when_opted_in(client):
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.learning.models import LearningEvent
    from app.training.models import TrainingJob, TrainingModelDeployment

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "自动提交部署审批的训练任务",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-auto",
            "target_gateway_id": "node-1",
            "spec": {
                "eval_gate": {"min_win_rate": 0.55, "manual_review_required": True},
                "deployment": {
                    "auto_request": True,
                    "target_skill_ids": ["skill-auto"],
                    "model_family": "skill-auto:qwen3.5-4b-qlora",
                    "rollout_percent": 10,
                    "reason": "评估通过后自动提交灰度审批",
                },
            },
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {"win_rate": 0.68},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/auto", "sha256": "e" * 64}],
        },
    )
    assert result_resp.status_code == 200

    evaluate_resp = await client.post(f"/api/training/jobs/{created['id']}/evaluate")
    assert evaluate_resp.status_code == 200
    evaluated = evaluate_resp.json()
    deployment = evaluated["latest_deployment"]
    auto_request = evaluated["tasks"][-1]["metrics"]["auto_deployment_request"]

    assert evaluated["status"] == "completed"
    assert deployment["status"] == "awaiting_review"
    assert deployment["target_skill_ids"] == ["skill-auto"]
    assert deployment["model_family"] == "skill-auto:qwen3.5-4b-qlora"
    assert deployment["rollout_percent"] == 10
    assert deployment["approved_by"] is None
    assert deployment["activated_at"] is None
    assert auto_request == {
        "status": "created",
        "deployment_id": deployment["id"],
        "deployment_status": "awaiting_review",
        "rollout_percent": 10,
        "review_required": True,
    }

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        assert stored is not None
        assert stored.status == "awaiting_review"
        assert stored.approved_by is None
        assert stored.activated_at is None
        deployment_audit = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.target_id == deployment["id"])
                .where(AuditLog.action == "training_deployment.request")
            )
        ).scalars().one()
        evaluate_audit = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.target_id == created["id"])
                .where(AuditLog.action == "training_job.evaluate")
            )
        ).scalars().one()
        learning_event = (
            await db.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "model_deployment")
                .where(LearningEvent.source_id == deployment["id"])
            )
        ).scalars().one()

    assert deployment_audit.detail["auto_requested"] is True
    assert deployment_audit.detail["status"] == "awaiting_review"
    assert evaluate_audit.detail["auto_deployment_request"]["deployment_id"] == deployment["id"]
    assert learning_event.event_type == "model.deployment.updated"


@pytest.mark.asyncio
async def test_training_gateway_completed_result_auto_evaluates_and_requests_deployment_review(client):
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "训练完成回传后自动进入部署审核",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-callback-auto",
            "target_gateway_id": "node-1",
            "spec": {
                "eval_gate": {"min_win_rate": 0.55, "manual_review_required": True},
                "deployment": {
                    "auto_request": True,
                    "target_skill_ids": ["skill-callback-auto"],
                    "model_family": "skill-callback-auto:learning-loop-adapter",
                    "rollout_percent": 10,
                },
                "governance": {
                    "no_auto_approval": True,
                    "deployment_review_required": True,
                },
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-callback-auto",
            "status": "completed",
            "metrics": {"win_rate": 0.72},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/callback-auto", "sha256": "7" * 64}],
        },
    )
    assert result_resp.status_code == 200, result_resp.text
    payload = result_resp.json()
    deployment = payload["latest_deployment"]
    evaluation_task = payload["tasks"][-1]

    assert payload["status"] == "completed"
    assert payload["auto_evaluation"]["status"] == "evaluated"
    assert payload["auto_evaluation"]["auto_deployment_request"]["review_required"] is True
    assert evaluation_task["metrics"]["op"] == "training.evaluate"
    assert evaluation_task["metrics"]["trigger"] == "gateway_result"
    assert evaluation_task["metrics"]["auto_deployment_request"]["deployment_id"] == deployment["id"]
    assert deployment["status"] == "awaiting_review"
    assert deployment["target_skill_ids"] == ["skill-callback-auto"]
    assert deployment["approved_by"] is None
    assert deployment["activated_at"] is None

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert stored is not None
    assert stored.status == "awaiting_review"
    assert actions == [
        "training_job.create",
        "training_job.approve",
        "training_job.gateway_result",
        "training_job.evaluate",
    ]


@pytest.mark.asyncio
async def test_training_gateway_completed_result_auto_activates_to_m3_when_opted_in(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient
    from app.common.audit import AuditLog
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    await _seed_default_training_gateway()
    await _seed_m3_deployment_gateway()
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "inference-primary")

    status_calls: list[dict] = []

    async def fake_get_training_model_status(self, payload, *, timeout=30):
        status_calls.append({"instance_id": self.instance_id, "profile": payload.get("profile")})
        return {
            "status": "ready",
            "profile": payload.get("profile") or "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "exists": True,
            "bridge_instance_id": self.instance_id,
        }

    monkeypatch.setattr(AIClawClient, "get_training_model_status", fake_get_training_model_status)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "训练完成后自动激活到 M3",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-auto-active",
            "target_gateway_id": "node-1",
            "spec": {
                "eval_gate": {"min_win_rate": 0.55},
                "model": {
                    "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                    "source": "internal",
                },
                "deployment": {
                    "auto_request": True,
                    "auto_active": True,
                    "target_skill_ids": ["skill-auto-active"],
                    "model_family": "skill-auto-active:qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive:qlora",
                    "rollout_percent": 100,
                },
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]
        previous = TrainingModelDeployment(
            id="deploy_previous_auto_active",
            job_id="train_previous_auto_active",
            department="EC",
            model_family="skill-auto-active:qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive:qlora",
            artifact_id="previous-auto-active",
            artifact_ref_json={"uri": "artifact://train/previous", "sha256": "7" * 64},
            target_skill_ids_json=["skill-auto-active"],
            deployment_target_gateway_id="inference-primary",
            deployment_runtime_profile="mlx-qwen3.6-35b-a3b-lora",
            status="active",
            rollout_percent=100,
            requested_by="admin",
            approved_by="admin",
            approved_at=now_bjt(),
            activated_at=now_bjt(),
        )
        db.add(previous)
        await db.commit()

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-auto-active",
            "status": "completed",
            "metrics": {"win_rate": 0.72},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/auto-active", "sha256": "8" * 64}],
        },
    )
    assert result_resp.status_code == 200, result_resp.text
    payload = result_resp.json()
    auto_request = payload["auto_evaluation"]["auto_deployment_request"]
    deployment = payload["latest_deployment"]

    assert status_calls == [{
        "instance_id": "inference-primary",
        "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
    }]
    assert auto_request["status"] == "active"
    assert auto_request["deployment_status"] == "active"
    assert auto_request["deployment_target_gateway_id"] == "inference-primary"
    assert auto_request["review_required"] is False
    assert auto_request["runtime_status"]["target_gateway_id"] == "inference-primary"
    assert auto_request["runtime_status"]["training_gateway_id"] == "node-1"
    assert auto_request["schedule_refresh_pending"] is True
    assert deployment["status"] == "active"
    assert deployment["deployment_target_gateway_id"] == "inference-primary"
    assert deployment["deployment_runtime_profile"] == "mlx-qwen3.6-35b-a3b-lora"
    assert deployment["approved_by"] == "training_automation"
    assert deployment["activated_at"] is not None

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        previous = await db.get(TrainingModelDeployment, "deploy_previous_auto_active")
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == deployment["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert stored.status == "active"
    assert stored.deployment_target_gateway_id == "inference-primary"
    assert previous.status == "rolled_back"
    assert previous.rollback_to == deployment["id"]
    assert actions == ["training_deployment.request", "training_deployment.auto_activate"]


@pytest.mark.asyncio
async def test_training_evaluate_retries_failed_auto_deployment_activation(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    await _seed_default_training_gateway()
    await _seed_m3_deployment_gateway()
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "inference-primary")

    status_calls = 0

    async def fake_get_training_model_status(self, payload, *, timeout=30):
        nonlocal status_calls
        status_calls += 1
        if status_calls == 1:
            return {"status": "missing", "profile": payload.get("profile"), "exists": False}
        return {
            "status": "ready",
            "profile": payload.get("profile") or "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "exists": True,
            "bridge_instance_id": self.instance_id,
        }

    monkeypatch.setattr(AIClawClient, "get_training_model_status", fake_get_training_model_status)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "自动部署失败后重试激活",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-auto-retry",
            "target_gateway_id": "node-1",
            "spec": {
                "eval_gate": {"min_win_rate": 0.55},
                "model": {
                    "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                    "source": "internal",
                },
                "deployment": {
                    "auto_request": True,
                    "auto_active": True,
                    "target_skill_ids": ["skill-auto-retry"],
                    "model_family": "skill-auto-retry:qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive:qlora",
                    "rollout_percent": 100,
                },
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-auto-retry",
            "status": "completed",
            "metrics": {"win_rate": 0.72},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/auto-retry", "sha256": "8" * 64}],
        },
    )
    assert result_resp.status_code == 200, result_resp.text
    first = result_resp.json()
    failed_request = first["auto_evaluation"]["auto_deployment_request"]
    deployment = first["latest_deployment"]
    assert failed_request["status"] == "failed"
    assert failed_request["runtime_status"]["inference_disabled_reason"] == "training_model_not_ready"
    assert deployment["status"] == "awaiting_review"

    retry_resp = await client.post(f"/api/training/jobs/{created['id']}/evaluate")
    assert retry_resp.status_code == 200, retry_resp.text
    retried = retry_resp.json()
    retry_request = retried["tasks"][-1]["metrics"]["auto_deployment_request"]
    deployment = retried["latest_deployment"]
    assert retry_request["retry"] is True
    assert retry_request["status"] == "active"
    assert retry_request["deployment_id"] == deployment["id"]
    assert deployment["status"] == "active"
    assert deployment["approved_by"] == "training_automation"
    assert status_calls == 2

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
    assert stored.status == "active"


@pytest.mark.asyncio
async def test_training_auto_active_imports_adapter_to_mac_and_registers_openwebui(client, monkeypatch, tmp_path):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    await _seed_default_training_gateway()
    await _seed_m3_deployment_gateway()
    monkeypatch.setattr(
        bridge_registry,
        "is_online",
        lambda instance_id: instance_id in {"node-1", "inference-primary"},
    )

    source_adapter = tmp_path / "adapter.tar.gz"
    source_adapter.write_bytes(b"adapter")
    imported_uri = "/Users/skillforge/.skillforge_bridge/mac/training_imports/train/adapter.tar.gz"
    calls: dict[str, list[dict]] = {"download": [], "import": [], "register": [], "chat": []}

    async def fake_get_training_model_status(self, payload, *, timeout=30):
        return {
            "status": "ready",
            "profile": payload.get("profile") or "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "exists": True,
            "bridge_instance_id": self.instance_id,
        }

    async def fake_get_training_artifact_status(self, payload, *, timeout=30):
        return {"exists": False, "job_id": payload.get("job_id"), "artifact_id": payload.get("artifact_id")}

    async def fake_download_training_artifact(self, payload, *, timeout=60):
        calls["download"].append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "filename": "adapter.tar.gz",
            "sha256": "9" * 64,
            "content_base64": "YWRhcHRlcg==",
            "size_bytes": 7,
        }

    async def fake_import_training_artifact(self, payload, *, timeout=300):
        calls["import"].append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "uri": imported_uri,
            "sha256": payload["sha256"],
            "size_bytes": 7,
            "exists": True,
        }

    async def fake_register_openwebui_model(self, payload, *, timeout=30):
        calls["register"].append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "registered": True,
            "model_id": payload["model_id"],
            "base_url": "http://127.0.0.1:18080/v1",
        }

    async def fake_test_openwebui_chat(self, payload, *, timeout=600):
        calls["chat"].append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "status": "succeeded",
            "ok": True,
            "model": payload["model_id"],
            "text": f"{payload['prompt']}：可用于成人用品电商测试。",
            "http_status": 200,
        }

    monkeypatch.setattr(AIClawClient, "get_training_model_status", fake_get_training_model_status)
    monkeypatch.setattr(AIClawClient, "get_training_artifact_status", fake_get_training_artifact_status)
    monkeypatch.setattr(AIClawClient, "download_training_artifact", fake_download_training_artifact)
    monkeypatch.setattr(AIClawClient, "import_training_artifact", fake_import_training_artifact)
    monkeypatch.setattr(AIClawClient, "register_openwebui_model", fake_register_openwebui_model)
    monkeypatch.setattr(AIClawClient, "test_openwebui_chat", fake_test_openwebui_chat)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "训练完成后注册 OpenWebUI",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-openwebui",
            "target_gateway_id": "node-1",
            "spec": {
                "eval_gate": {"min_win_rate": 0.55},
                "model": {
                    "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                    "source": "internal",
                },
                "deployment": {
                    "auto_request": True,
                    "auto_active": True,
                    "target_skill_ids": ["skill-openwebui"],
                    "model_family": "skill-openwebui:qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive:learning-loop-adapter",
                    "rollout_percent": 100,
                    "keyword_review": {
                        "enabled": True,
                        "domain": "adult_products",
                        "keywords": ["安全套", "润滑液"],
                    },
                },
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-openwebui",
            "status": "completed",
            "metrics": {"win_rate": 0.72},
            "artifacts": [{
                "id": "train-openwebui:adapter",
                "type": "adapter",
                "name": "adapter.tar.gz",
                "uri": str(source_adapter),
                "sha256": "9" * 64,
            }],
        },
    )

    assert result_resp.status_code == 200, result_resp.text
    payload = result_resp.json()
    runtime_status = payload["auto_evaluation"]["auto_deployment_request"]["runtime_status"]
    deployment = payload["latest_deployment"]
    assert runtime_status["artifact_sync"]["status"] == "imported"
    assert runtime_status["openwebui"]["registered"] is True
    assert runtime_status["keyword_review"]["status"] == "passed"
    assert runtime_status["keyword_review"]["passed_count"] == 2
    assert calls["download"][0]["instance_id"] == "node-1"
    assert calls["import"][0]["instance_id"] == "inference-primary"
    assert calls["register"][0]["instance_id"] == "inference-primary"
    assert calls["register"][0]["payload"]["artifact_uri"] == imported_uri
    assert calls["register"][0]["payload"]["runtime_profile"] == "mlx-qwen3.6-35b-a3b-lora"
    deployment_date = deployment["created_at"][:10].replace("-", "")
    assert calls["register"][0]["payload"]["model_id"] == (
        f"skillforge-ft-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive-{deployment_date}-{deployment['id']}"
    )
    assert calls["register"][0]["payload"]["aliases"] == [
        "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive-finetuned"
    ]
    assert calls["register"][0]["payload"]["source_type"] == "skillforge_finetuned_adapter"
    assert [item["payload"]["model_id"] for item in calls["chat"]] == [calls["register"][0]["payload"]["model_id"]] * 2

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
    assert stored.artifact_ref_json["uri"] == imported_uri
    assert stored.artifact_ref_json["source_uri"] == str(source_adapter)
    assert stored.artifact_ref_json["deployment_target_gateway_id"] == "inference-primary"


@pytest.mark.asyncio
async def test_training_deployment_sync_openwebui_registers_existing_active(client, monkeypatch, tmp_path):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient
    from app.common.audit import AuditLog
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    await _seed_default_training_gateway()
    await _seed_m3_deployment_gateway()
    monkeypatch.setattr(
        bridge_registry,
        "is_online",
        lambda instance_id: instance_id in {"node-1", "inference-primary"},
    )

    source_adapter = tmp_path / "existing-adapter.tar.gz"
    source_adapter.write_bytes(b"adapter")
    imported_uri = "/Users/skillforge/.skillforge_bridge/mac/training_imports/train-existing/existing-adapter.tar.gz"
    calls: dict[str, list[dict]] = {"download": [], "import": [], "register": []}

    async def fake_get_training_model_status(self, payload, *, timeout=30):
        return {
            "status": "ready",
            "profile": payload.get("profile") or "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "exists": True,
            "bridge_instance_id": self.instance_id,
        }

    async def fake_get_training_artifact_status(self, payload, *, timeout=30):
        return {"exists": False, "job_id": payload.get("job_id"), "artifact_id": payload.get("artifact_id")}

    async def fake_download_training_artifact(self, payload, *, timeout=60):
        calls["download"].append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "filename": "existing-adapter.tar.gz",
            "sha256": "9" * 64,
            "content_base64": "YWRhcHRlcg==",
            "size_bytes": 7,
        }

    async def fake_import_training_artifact(self, payload, *, timeout=300):
        calls["import"].append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "uri": imported_uri,
            "sha256": payload["sha256"],
            "size_bytes": 7,
            "exists": True,
        }

    async def fake_register_openwebui_model(self, payload, *, timeout=30):
        calls["register"].append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "registered": True,
            "model_id": payload["model_id"],
            "base_url": "http://127.0.0.1:18080/v1",
        }

    monkeypatch.setattr(AIClawClient, "get_training_model_status", fake_get_training_model_status)
    monkeypatch.setattr(AIClawClient, "get_training_artifact_status", fake_get_training_artifact_status)
    monkeypatch.setattr(AIClawClient, "download_training_artifact", fake_download_training_artifact)
    monkeypatch.setattr(AIClawClient, "import_training_artifact", fake_import_training_artifact)
    monkeypatch.setattr(AIClawClient, "register_openwebui_model", fake_register_openwebui_model)

    now = now_bjt()
    async with async_session_factory() as db:
        db.add(TrainingJob(
            id="train_existing_openwebui",
            title="已有部署补注册 OpenWebUI",
            department="EC",
            created_by="admin",
            status="completed",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="skill-existing",
            target_gateway_id="node-1",
            dataset_ref="dataset://learning-flow/skillforge/all",
            objective="使用 SkillForge 全量学习数据微调。",
            risk_level="R2",
            spec_json={
                "model": {
                    "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                    "source": "internal",
                },
                "deployment": {
                    "target_gateway_id": "inference-primary",
                    "runtime_profile": "mlx-qwen3.6-35b-a3b-lora",
                },
            },
            approval_required=True,
            approved_by="admin",
            approved_at=now,
            created_at=now,
            updated_at=now,
        ))
        db.add(TrainingModelDeployment(
            id="deploy_existing_openwebui",
            job_id="train_existing_openwebui",
            department="EC",
            model_family="skill-existing:qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive:learning-loop-adapter",
            artifact_id="train-existing:adapter",
            artifact_ref_json={
                "id": "train-existing:adapter",
                "name": "existing-adapter.tar.gz",
                "uri": str(source_adapter),
                "sha256": "9" * 64,
            },
            target_skill_ids_json=["skill-existing"],
            deployment_target_gateway_id="inference-primary",
            deployment_runtime_profile="mlx-qwen3.6-35b-a3b-lora",
            status="active",
            rollout_percent=100,
            requested_by="admin",
            approved_by="admin",
            approved_at=now,
            activated_at=now,
            created_at=now,
            updated_at=now,
        ))
        await db.commit()

    resp = await client.post("/api/training/deployments/deploy_existing_openwebui/sync-openwebui")

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    runtime_status = payload["runtime_status"]
    assert runtime_status["artifact_sync"]["status"] == "imported"
    assert payload["openwebui"]["registered"] is True
    assert calls["download"][0]["instance_id"] == "node-1"
    assert calls["import"][0]["instance_id"] == "inference-primary"
    assert calls["register"][0]["instance_id"] == "inference-primary"
    assert calls["register"][0]["payload"]["model_id"] == (
        f"skillforge-ft-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive-{now.strftime('%Y%m%d')}-deploy_existing_openwebui"
    )
    assert calls["register"][0]["payload"]["aliases"] == [
        "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive-finetuned"
    ]
    assert calls["register"][0]["payload"]["artifact_uri"] == imported_uri
    assert calls["register"][0]["payload"]["source_type"] == "skillforge_finetuned_adapter"

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, "deploy_existing_openwebui")
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == "deploy_existing_openwebui").order_by(AuditLog.id)
        )).scalars().all()
    assert stored.artifact_ref_json["uri"] == imported_uri
    assert stored.artifact_ref_json["source_uri"] == str(source_adapter)
    assert stored.artifact_ref_json["deployment_target_gateway_id"] == "inference-primary"
    assert actions == ["training_deployment.sync_openwebui"]


@pytest.mark.asyncio
async def test_training_deployment_defaults_to_online_secondary_m3_when_236_offline(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    await _seed_default_training_gateway()
    await _seed_m3_deployment_gateway(gateway_id="inference-primary", name="M3 Ultra 236")
    await _seed_m3_deployment_gateway(gateway_id="platform-mac-238", name="M3 Ultra 238")
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "platform-mac-238")

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "M3 部署池 fallback",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-m3-pool",
            "target_gateway_id": "node-1",
            "spec": {"eval_gate": {"min_win_rate": 0.55}},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-m3-pool",
            "status": "completed",
            "metrics": {"win_rate": 0.7},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/m3-pool", "sha256": "4" * 64}],
        },
    )
    assert result_resp.status_code == 200, result_resp.text
    evaluate_resp = await client.post(f"/api/training/jobs/{created['id']}/evaluate")
    assert evaluate_resp.status_code == 200, evaluate_resp.text

    deploy_resp = await client.post(
        f"/api/training/jobs/{created['id']}/deploy-request",
        json={"reason": "评估通过，提交部署审批", "rollout_percent": 10},
    )
    assert deploy_resp.status_code == 200, deploy_resp.text
    deployment = deploy_resp.json()["deployment"]
    assert deployment["deployment_target_gateway_id"] == "platform-mac-238"
    assert deployment["deployment_runtime_profile"] == "mlx-qwen3.5-4b-lora"

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        assert stored.deployment_target_gateway_id == "platform-mac-238"


@pytest.mark.asyncio
async def test_learning_sample_training_candidate_evaluate_requests_deployment_review(client):
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "学习样本池自动训练候选",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-learning-loop",
            "target_gateway_id": "node-1",
            "dataset_ref": "learning-artifacts://skill-learning-loop/training/latest",
            "spec": {
                "source": "learning_sample_threshold",
                "eval_gate": {
                    "min_win_rate": 0.55,
                    "require_artifact_sha256": True,
                    "manual_review_required": True,
                },
                "deployment": {
                    "auto_request": True,
                    "target_skill_ids": ["skill-learning-loop"],
                    "model_family": "skill-learning-loop:learning-loop-adapter",
                    "rollout_percent": 10,
                    "reason": "智能闭环训练评估通过后自动提交部署审核",
                },
                "governance": {
                    "auto_created": True,
                    "no_auto_approval": True,
                    "deployment_review_required": True,
                },
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    assert created["status"] == "awaiting_review"
    assert created["spec"]["governance"]["no_auto_approval"] is True
    assert "no_auto_deploy" not in created["spec"]["governance"]

    approve_resp = await client.post(f"/api/training/jobs/{created['id']}/approve")
    assert approve_resp.status_code == 200, approve_resp.text

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-learning-loop",
            "status": "completed",
            "metrics": {"win_rate": 0.7},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/learning-loop", "sha256": "9" * 64}],
        },
    )
    assert result_resp.status_code == 200, result_resp.text

    evaluate_resp = await client.post(f"/api/training/jobs/{created['id']}/evaluate")
    assert evaluate_resp.status_code == 200, evaluate_resp.text
    evaluated = evaluate_resp.json()
    deployment = evaluated["latest_deployment"]

    assert evaluated["status"] == "completed"
    assert deployment["status"] == "awaiting_review"
    assert deployment["target_skill_ids"] == ["skill-learning-loop"]
    assert deployment["model_family"] == "skill-learning-loop:learning-loop-adapter"
    assert deployment["approved_by"] is None
    assert deployment["activated_at"] is None
    assert evaluated["tasks"][-1]["metrics"]["auto_deployment_request"]["review_required"] is True

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        assert stored is not None
        assert stored.status == "awaiting_review"
        assert stored.approved_by is None
        assert stored.activated_at is None


@pytest.mark.asyncio
async def test_learning_sample_training_candidate_requires_artifact_uri_for_deployment_review(client):
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "学习样本池自动训练候选缺少 artifact uri",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-learning-missing-uri",
            "target_gateway_id": "node-1",
            "dataset_ref": "learning-artifacts://skill-learning-missing-uri/training/latest",
            "spec": {
                "source": "learning_sample_threshold",
                "eval_gate": {
                    "min_win_rate": 0.55,
                    "require_artifact_sha256": True,
                    "manual_review_required": True,
                },
                "deployment": {
                    "auto_request": True,
                    "target_skill_ids": ["skill-learning-missing-uri"],
                    "model_family": "skill-learning-missing-uri:learning-loop-adapter",
                    "rollout_percent": 10,
                },
                "governance": {
                    "auto_created": True,
                    "no_auto_approval": True,
                    "deployment_review_required": True,
                },
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-learning-loop",
            "status": "completed",
            "metrics": {"win_rate": 0.7},
            "artifacts": [{"type": "adapter", "sha256": "8" * 64}],
        },
    )
    assert result_resp.status_code == 422, result_resp.text
    error = result_resp.json()["error"]
    assert error["code"] == "TRAINING_RESULT_ARTIFACT_REQUIRED"
    assert error["detail"]["required_artifact_fields"] == ["uri", "sha256"]

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        deployments = (
            await db.execute(
                select(TrainingModelDeployment).where(TrainingModelDeployment.job_id == created["id"])
            )
        ).scalars().all()
    assert job.status == "queued"
    assert deployments == []


@pytest.mark.asyncio
async def test_training_job_evaluate_respects_no_auto_deploy_governance(client):
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "禁止自动部署的训练任务",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-governed",
            "target_gateway_id": "node-1",
            "spec": {
                "eval_gate": {"min_win_rate": 0.55},
                "governance": {"no_auto_deploy": True},
                "deployment": {"auto_request": True, "rollout_percent": 10},
            },
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {"win_rate": 0.68},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/governed", "sha256": "f" * 64}],
        },
    )
    assert result_resp.status_code == 200

    evaluate_resp = await client.post(f"/api/training/jobs/{created['id']}/evaluate")
    assert evaluate_resp.status_code == 200
    evaluated = evaluate_resp.json()

    assert evaluated["status"] == "completed"
    assert evaluated["latest_deployment"] is None
    assert "auto_deployment_request" not in evaluated["tasks"][-1]["metrics"]

    async with async_session_factory() as db:
        deployments = (
            await db.execute(
                select(TrainingModelDeployment).where(TrainingModelDeployment.job_id == created["id"])
            )
        ).scalars().all()
    assert deployments == []


@pytest.mark.asyncio
async def test_training_deployments_list_returns_registry_rows_with_job_summary(client):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    now = now_bjt()
    async with async_session_factory() as db:
        db.add(TrainingJob(
            id="train_registry_1",
            title="动作推荐训练",
            department="EC",
            created_by="admin",
            status="completed",
            job_type="lora",
            target_skill_id="skill-recommend",
            target_gateway_id="node-1",
            dataset_ref="dataset://ec/actions/v1",
            approval_required=True,
            created_at=now,
            updated_at=now,
        ))
        db.add(TrainingModelDeployment(
            id="deploy_registry_1",
            job_id="train_registry_1",
            department="EC",
            model_family="item_decline_ranker",
            artifact_id="artifact-1",
            artifact_ref_json={"sha256": "a" * 64},
            target_skill_ids_json=["skill-recommend"],
            status="awaiting_review",
            rollout_percent=0,
            requested_by="admin",
            created_at=now,
            updated_at=now,
        ))
        await db.commit()

    resp = await client.get("/api/training/deployments")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["total"] == 1
    assert payload["stats"]["awaiting_review"] == 1
    assert payload["items"][0]["id"] == "deploy_registry_1"
    assert payload["items"][0]["artifact_ref"]["sha256"] == "a" * 64
    assert payload["items"][0]["job"] == {
        "id": "train_registry_1",
        "title": "动作推荐训练",
        "department": "EC",
        "status": "completed",
        "failure_stage": None,
        "job_type": "lora",
        "target_skill_id": "skill-recommend",
        "target_gateway_id": "node-1",
        "deployment_target_gateway_id": None,
        "deployment_runtime_profile": None,
        "dataset_ref": "dataset://ec/actions/v1",
        "updated_at": payload["items"][0]["job"]["updated_at"],
    }


@pytest.mark.asyncio
async def test_training_deployment_detail_returns_job_tasks_and_related_deployments(client):
    deployment, job = await _create_evaluated_deployment_request(
        client,
        target_skill_id="skill-recommend",
        model_family="item_decline_ranker",
        rollout_percent=10,
        sha256="c" * 64,
    )

    detail_resp = await client.get(f"/api/training/deployments/{deployment['id']}")
    assert detail_resp.status_code == 200
    payload = detail_resp.json()
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["deployment"]["id"] == deployment["id"]
    assert payload["deployment"]["artifact_ref"]["sha256"] == "c" * 64
    assert payload["job"]["id"] == job["id"]
    assert payload["job"]["status"] == "completed"
    assert payload["job"]["latest_deployment"]["id"] == deployment["id"]
    assert any(task["metrics"].get("op") == "training.evaluate" for task in payload["job"]["tasks"])
    assert "sftrain." not in encoded


@pytest.mark.asyncio
async def test_training_job_detail_exposes_agent_chat_target_readiness(client, monkeypatch):
    model_status = {"status": "ready"}
    _patch_training_deployment_model_ready(monkeypatch, status=lambda: model_status["status"])
    deployment, job = await _create_evaluated_deployment_request(
        client,
        target_skill_id="skill-recommend",
        model_family="item_decline_ranker",
        rollout_percent=10,
        sha256="e" * 64,
    )

    pending_detail = (await client.get(f"/api/training/jobs/{job['id']}")).json()
    pending_target = pending_detail["training_plan"]["chat_target"]
    assert pending_target["ready"] is False
    assert pending_target["disabled_reason"] == "部署状态为 awaiting_review，需进入灰度或已部署"
    assert pending_target["artifact_id"] == deployment["artifact_id"]
    assert pending_target["artifact_uri_present"] is True
    assert pending_target["inference_ready"] is False
    assert pending_target["inference_disabled_reason"] == "部署状态为 awaiting_review，需进入灰度或已部署"

    approve_resp = await client.post(f"/api/training/deployments/{deployment['id']}/approve")
    assert approve_resp.status_code == 200
    ready_detail = (await client.get(f"/api/training/jobs/{job['id']}")).json()
    chat_target = ready_detail["training_plan"]["chat_target"]
    assert chat_target == {
        "ready": True,
        "disabled_reason": None,
        "model_deployment_id": deployment["id"],
        "model_family": "item_decline_ranker",
        "training_job_id": job["id"],
        "department": "EC",
        "artifact_id": deployment["artifact_id"],
        "artifact_uri_present": True,
        "target_skill_ids": ["skill-recommend"],
        "target_gateway_id": "node-1",
        "deployment_status": "canary",
        "inference_ready": True,
        "inference_disabled_reason": None,
    }


@pytest.mark.asyncio
async def test_training_deployment_defaults_to_m3_target_and_approval_checks_m3(client, monkeypatch):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient

    await _seed_m3_deployment_gateway()
    deployment, _ = await _create_evaluated_deployment_request(
        client,
        target_skill_id="skill-recommend",
        model_family="item_decline_ranker:qwen3.5-4b",
        rollout_percent=10,
        sha256="9" * 64,
    )

    assert deployment["deployment_target_gateway_id"] == "inference-primary"
    assert deployment["deployment_runtime_profile"] == "mlx-qwen3.5-4b-lora"

    status_calls: list[dict] = []
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "inference-primary")

    async def fake_get_training_model_status(self, payload, *, timeout=30):
        status_calls.append({"instance_id": self.instance_id, "profile": payload.get("profile")})
        return {
            "status": "ready",
            "profile": payload.get("profile") or "qwen3.5-4b",
            "exists": True,
            "bridge_instance_id": self.instance_id,
        }

    monkeypatch.setattr(AIClawClient, "get_training_model_status", fake_get_training_model_status)

    approve_resp = await client.post(f"/api/training/deployments/{deployment['id']}/approve")
    assert approve_resp.status_code == 200, approve_resp.text
    payload = approve_resp.json()

    assert status_calls == [{"instance_id": "inference-primary", "profile": "qwen3.5-4b"}]
    assert payload["deployment"]["status"] == "canary"
    assert payload["deployment"]["deployment_target_gateway_id"] == "inference-primary"
    chat_target = payload["job"]["training_plan"]["chat_target"]
    assert chat_target["target_gateway_id"] == "inference-primary"
    assert chat_target["training_gateway_id"] == "node-1"
    assert chat_target["inference_ready"] is True


@pytest.mark.asyncio
async def test_training_deployment_reject_closes_request_and_keeps_job_completed(client):
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.training.models import TrainingModelDeployment

    deployment, job = await _create_evaluated_deployment_request(
        client,
        target_skill_id="skill-recommend",
        model_family="item_decline_ranker",
        rollout_percent=10,
        sha256="d" * 64,
    )

    reject_resp = await client.post(
        f"/api/training/deployments/{deployment['id']}/reject",
        json={"reason": "评估窗口业务指标不稳定，暂不进入灰度"},
    )
    assert reject_resp.status_code == 200
    payload = reject_resp.json()

    assert payload["deployment"]["status"] == "rejected"
    assert payload["deployment"]["reject_reason"] == "评估窗口业务指标不稳定，暂不进入灰度"
    assert payload["job"]["id"] == job["id"]
    assert payload["job"]["status"] == "completed"
    assert payload["job"]["failure_stage"] is None

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        assert stored.status == "rejected"
        assert stored.reject_reason == "评估窗口业务指标不稳定，暂不进入灰度"
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == deployment["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert actions == ["training_deployment.request", "training_deployment.reject"]


@pytest.mark.asyncio
async def test_training_job_deploy_request_requires_passed_evaluation(client):
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    await _seed_default_training_gateway()

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "训练商品下滑动作推荐模型",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-recommend",
            "target_gateway_id": "node-1",
            "spec": {"eval_gate": {"min_win_rate": 0.8}},
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "worker_id": "worker-1",
            "status": "completed",
            "metrics": {"win_rate": 0.51},
            "artifacts": [{"type": "adapter", "uri": "artifact://train/model", "sha256": "b" * 64}],
        },
    )
    await client.post(f"/api/training/jobs/{created['id']}/evaluate")

    deploy_resp = await client.post(
        f"/api/training/jobs/{created['id']}/deploy-request",
        json={"target_skill_ids": ["skill-recommend"], "reason": "尝试提交部署"},
    )
    assert deploy_resp.status_code == 400
    assert deploy_resp.json()["error"]["code"] == "INVALID_STATUS"


@pytest.mark.asyncio
async def test_training_deployment_approve_and_activate_rolls_forward_single_active(client, monkeypatch):
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.learning.models import LearningEvent, LearningFlowEdge
    from app.training.models import TrainingModelDeployment

    model_status = {"status": "ready"}
    _patch_training_deployment_model_ready(monkeypatch, status=lambda: model_status["status"])
    first, _ = await _create_evaluated_deployment_request(
        client,
        target_skill_id="skill-recommend",
        model_family="item_decline_ranker",
        rollout_percent=100,
        sha256="a" * 64,
    )
    first_approve = await client.post(f"/api/training/deployments/{first['id']}/approve")
    assert first_approve.status_code == 200
    assert first_approve.json()["deployment"]["status"] == "active"

    second, _ = await _create_evaluated_deployment_request(
        client,
        target_skill_id="skill-recommend",
        model_family="item_decline_ranker",
        rollout_percent=10,
        sha256="b" * 64,
    )
    second_approve = await client.post(f"/api/training/deployments/{second['id']}/approve")
    assert second_approve.status_code == 200
    approved = second_approve.json()["deployment"]

    assert approved["status"] == "canary"
    assert approved["rollback_to"] == first["id"]
    async with async_session_factory() as db:
        stored_first = await db.get(TrainingModelDeployment, first["id"])
        assert stored_first.status == "active"
        approved_event = (
            await db.execute(
                select(LearningEvent)
                .where(LearningEvent.event_type == "model.deployed")
                .where(LearningEvent.source_type == "model_deployment")
                .where(LearningEvent.source_id == second["id"])
            )
        ).scalar_one()
        approved_edge = (
            await db.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "training_job")
                .where(LearningFlowEdge.to_type == "model_deployment")
                .where(LearningFlowEdge.to_id == second["id"])
                .where(LearningFlowEdge.relation == "deployed_to")
            )
        ).scalar_one()
    assert approved_event.metadata_json["runtime_status"]["inference_ready"] is True
    assert approved_event.metadata_json["runtime_status"]["model_ready"] is True
    assert approved_event.metadata_json["runtime_status"]["target_gateway_id"] == "node-1"
    assert approved_edge.metadata_json["runtime_status"]["inference_ready"] is True

    model_status["status"] = "prepared"
    activate_resp = await client.post(f"/api/training/deployments/{second['id']}/activate")
    assert activate_resp.status_code == 200
    activated = activate_resp.json()["deployment"]

    assert activated["status"] == "active"
    assert activated["rollout_percent"] == 100
    async with async_session_factory() as db:
        stored_first = await db.get(TrainingModelDeployment, first["id"])
        stored_second = await db.get(TrainingModelDeployment, second["id"])
        assert stored_first.status == "rolled_back"
        assert stored_first.rollback_to == second["id"]
        assert stored_second.status == "active"
        activated_event = (
            await db.execute(
                select(LearningEvent)
                .where(LearningEvent.event_type == "model.deployed")
                .where(LearningEvent.source_type == "model_deployment")
                .where(LearningEvent.source_id == second["id"])
            )
        ).scalar_one()
        skill_edge = (
            await db.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "model_deployment")
                .where(LearningFlowEdge.from_id == second["id"])
                .where(LearningFlowEdge.to_type == "skill")
                .where(LearningFlowEdge.to_id == "skill-recommend")
                .where(LearningFlowEdge.relation == "deployed_to")
            )
        ).scalar_one()
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == second["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert activated_event.metadata_json["runtime_status"]["inference_ready"] is True
    assert activated_event.metadata_json["runtime_status"]["model_status"]["status"] == "prepared"
    assert skill_edge.metadata_json["event_id"] == activated_event.id
    assert skill_edge.metadata_json["runtime_status"]["inference_ready"] is True
    assert actions == [
        "training_deployment.request",
        "training_deployment.approve",
        "training_deployment.activate",
    ]

    journeys = (
        await client.get(
            "/api/learning/flow-journeys",
            params={"days": 7, "skill_id": "skill-recommend", "source_type": "model_deployment"},
        )
    ).json()
    deployment_journey = next(item for item in journeys["items"] if item["source_id"] == second["id"])
    deployed_steps = [step for step in deployment_journey["steps"] if step.get("action") == "deployed_to"]
    assert any(step["stage"] == "deployment" for step in deployed_steps)
    skill_step = next(
        step
        for step in deployed_steps
        if step["payload"]["from_type"] == "model_deployment" and step["payload"]["to_type"] == "skill"
    )
    assert skill_step["payload"]["runtime_status"]["inference_ready"] is True
    assert skill_step["payload"]["runtime_status"]["model_status"]["status"] == "prepared"
    assert "参与决策" in skill_step["summary"]


@pytest.mark.asyncio
async def test_training_deployment_active_approval_refreshes_node_schedule_model_context(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.database import async_session_factory
    from app.execution.models import NodeScheduleConfig, OpenClawInstance
    from app.learning.models import LearningEvent, LearningFlowEdge
    from app.skills.core.models import Skill

    target_skill_id = "skill-runtime-refresh"
    _patch_training_deployment_model_ready(monkeypatch)
    deployment, _ = await _create_evaluated_deployment_request(
        client,
        target_skill_id=target_skill_id,
        model_family="runtime_refresh_ranker",
        rollout_percent=100,
        sha256="f" * 64,
    )

    async with async_session_factory() as db:
        gateway = await db.get(OpenClawInstance, "node-1")
        gateway.agent_purpose = "mixed"
        db.add(Skill(
            id=target_skill_id,
            name="运行态模型上下文刷新",
            department="EC",
            owner="admin",
            status="active",
            trigger_type="cron",
            trigger_expression="*/5 * * * *",
        ))
        db.add(NodeScheduleConfig(
            instance_id="node-1",
            skill_id=target_skill_id,
            cron_expression="*/5 * * * *",
            config_version=1,
            ack_ok=True,
            runtime_backend="bridge_script",
            runtime_config={
                "backend": "bridge_script",
                "_model_context": {
                    "model_deployment_id": "old-deployment",
                    "sha256": "old-context",
                },
            },
        ))
        await db.commit()

    sync_calls: list[dict] = []

    async def fake_list_local_skills(self, target_dir=None):
        return {"skills": [target_skill_id], "dir": "/tmp/skills"}

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        sync_calls.append({
            "instance_id": self.instance_id,
            "schedules": schedules,
            "config_version": config_version,
        })
        return {"accepted": len(schedules), "config_version": config_version}

    monkeypatch.setattr(AIClawClient, "list_local_skills", fake_list_local_skills)
    monkeypatch.setattr(AIClawClient, "sync_schedules", fake_sync_schedules)

    approve_resp = await client.post(f"/api/training/deployments/{deployment['id']}/approve")
    assert approve_resp.status_code == 200, approve_resp.text
    assert approve_resp.json()["deployment"]["status"] == "active"

    assert [call["instance_id"] for call in sync_calls] == ["node-1"]
    schedule = sync_calls[0]["schedules"][0]
    assert schedule["skill_id"] == target_skill_id
    assert schedule["model_context"]["model_deployment_id"] == deployment["id"]
    active_model = schedule["model_context"]["active_model_deployment"]
    assert active_model["deployment_status"] == "active"
    assert active_model["artifact_sha256"] == "f" * 64
    assert active_model["artifact_uri"] == "artifact://train/model"
    assert active_model["runtime_status"]["inference_ready_hint"] is True

    async with async_session_factory() as db:
        config = (
            await db.execute(
                select(NodeScheduleConfig)
                .where(NodeScheduleConfig.instance_id == "node-1")
                .where(NodeScheduleConfig.skill_id == target_skill_id)
            )
        ).scalar_one()
        deployment_event = (
            await db.execute(
                select(LearningEvent)
                .where(LearningEvent.event_type == "model.deployed")
                .where(LearningEvent.source_type == "model_deployment")
                .where(LearningEvent.source_id == deployment["id"])
            )
        ).scalar_one()
        skill_edge = (
            await db.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "model_deployment")
                .where(LearningFlowEdge.from_id == deployment["id"])
                .where(LearningFlowEdge.to_type == "skill")
                .where(LearningFlowEdge.to_id == target_skill_id)
                .where(LearningFlowEdge.relation == "deployed_to")
            )
        ).scalar_one()
    marker = config.runtime_config["_model_context"]
    assert marker["model_deployment_id"] == deployment["id"]
    assert marker["sha256"] != "old-context"
    assert config.ack_ok is True
    schedule_refresh = deployment_event.metadata_json["runtime_status"]["schedule_refresh"]
    assert schedule_refresh == {
        "attempted": True,
        "target_count": 1,
        "succeeded_count": 1,
        "failed_count": 0,
        "targets": [{"instance_id": "node-1", "ok": True, "accepted": 1, "config_version": sync_calls[0]["config_version"]}],
        "raw_payload_returned": False,
    }
    assert skill_edge.metadata_json["runtime_status"]["schedule_refresh"]["succeeded_count"] == 1
    assert "model_context" not in json.dumps(schedule_refresh, ensure_ascii=False)


@pytest.mark.asyncio
async def test_training_deployment_approve_requires_inference_ready_artifact(client):
    from app.database import async_session_factory
    from app.training.models import TrainingModelDeployment

    deployment, _ = await _create_evaluated_deployment_request(
        client,
        target_skill_id="skill-recommend",
        model_family="item_decline_ranker",
        rollout_percent=10,
        sha256="c" * 64,
    )
    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        stored.artifact_ref_json = {"sha256": "c" * 64}
        await db.commit()

    approve_resp = await client.post(f"/api/training/deployments/{deployment['id']}/approve")
    assert approve_resp.status_code == 400
    body = approve_resp.json()
    assert body["error"]["code"] == "TRAINING_DEPLOYMENT_NOT_READY"
    assert body["error"]["detail"]["runtime_status"]["inference_disabled_reason"] == "artifact_uri_missing"

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        assert stored.status == "awaiting_review"
        assert stored.approved_by is None


@pytest.mark.asyncio
async def test_training_deployment_approve_requires_inference_capable_gateway(client):
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingModelDeployment

    deployment, job = await _create_evaluated_deployment_request(
        client,
        target_skill_id="skill-recommend",
        model_family="item_decline_ranker",
        rollout_percent=10,
        sha256="d" * 64,
    )
    async with async_session_factory() as db:
        gateway = await db.get(OpenClawInstance, "node-1")
        gateway.bridge_capabilities_json = json.dumps({
            "ops": ["training.submit_job", "training.collect_result"],
            "training": {"gateway": True, "supported_tasks": ["lora", "eval"], "gpu_count": 1},
        })
        await db.commit()

    approve_resp = await client.post(f"/api/training/deployments/{deployment['id']}/approve")
    assert approve_resp.status_code == 400
    body = approve_resp.json()
    assert body["error"]["code"] == "TRAINING_DEPLOYMENT_NOT_READY"
    assert body["error"]["detail"]["runtime_status"] == {
        "artifact_uri_present": True,
        "target_gateway_id": "node-1",
        "target_gateway_kind": "openclaw",
        "model_profile": "qwen3.5-4b",
        "model_status": None,
        "model_ready": False,
        "inference_ready": False,
        "inference_disabled_reason": "training_inference_op_unavailable",
    }

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        assert stored.status == "awaiting_review"
        assert stored.approved_by is None


@pytest.mark.asyncio
async def test_training_deployment_approve_requires_ready_node_model(client, monkeypatch):
    from app.database import async_session_factory
    from app.training.models import TrainingModelDeployment

    _patch_training_deployment_model_ready(monkeypatch, status="not_downloaded")
    deployment, _ = await _create_evaluated_deployment_request(
        client,
        target_skill_id="skill-recommend",
        model_family="item_decline_ranker:qwen3.5-4b",
        rollout_percent=10,
        sha256="e" * 64,
    )

    approve_resp = await client.post(f"/api/training/deployments/{deployment['id']}/approve")
    assert approve_resp.status_code == 400
    body = approve_resp.json()
    runtime_status = body["error"]["detail"]["runtime_status"]
    assert body["error"]["code"] == "TRAINING_DEPLOYMENT_NOT_READY"
    assert runtime_status["artifact_uri_present"] is True
    assert runtime_status["target_gateway_id"] == "node-1"
    assert runtime_status["model_profile"] == "qwen3.5-4b"
    assert runtime_status["model_status"]["status"] == "not_downloaded"
    assert runtime_status["model_ready"] is False
    assert runtime_status["inference_ready"] is False
    assert runtime_status["inference_disabled_reason"] == "training_model_not_ready"

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        assert stored.status == "awaiting_review"
        assert stored.approved_by is None


@pytest.mark.asyncio
async def test_training_deployment_rollback_restores_previous_active(client, monkeypatch):
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.training.models import TrainingModelDeployment

    _patch_training_deployment_model_ready(monkeypatch)
    first, _ = await _create_evaluated_deployment_request(
        client,
        target_skill_id="skill-recommend",
        model_family="item_decline_ranker",
        rollout_percent=100,
        sha256="a" * 64,
    )
    assert (await client.post(f"/api/training/deployments/{first['id']}/approve")).status_code == 200

    second, _ = await _create_evaluated_deployment_request(
        client,
        target_skill_id="skill-recommend",
        model_family="item_decline_ranker",
        rollout_percent=10,
        sha256="b" * 64,
    )
    assert (await client.post(f"/api/training/deployments/{second['id']}/approve")).status_code == 200
    assert (await client.post(f"/api/training/deployments/{second['id']}/activate")).status_code == 200

    rollback_resp = await client.post(
        f"/api/training/deployments/{second['id']}/rollback",
        json={"reason": "灰度指标回归，恢复上一版部署"},
    )
    assert rollback_resp.status_code == 200
    rolled_back = rollback_resp.json()["deployment"]

    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["rollback_to"] == first["id"]
    async with async_session_factory() as db:
        stored_first = await db.get(TrainingModelDeployment, first["id"])
        stored_second = await db.get(TrainingModelDeployment, second["id"])
        assert stored_first.status == "active"
        assert stored_second.status == "rolled_back"
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == second["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert actions == [
        "training_deployment.request",
        "training_deployment.approve",
        "training_deployment.activate",
        "training_deployment.rollback",
    ]


@pytest.mark.asyncio
async def test_training_gateway_result_rejects_bad_token(client):
    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": "bad-token"},
        json={
            "job_id": created["id"],
            "status": "completed",
            "metrics": {},
        },
    )
    assert result_resp.status_code == 401


@pytest.mark.asyncio
async def test_training_gateway_result_rejects_control_plane_status(client):
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    await _seed_default_training_gateway(supported_tasks=["eval"])

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    result_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "status": "awaiting_review",
            "metrics": {},
        },
    )
    assert result_resp.status_code == 422


@pytest.mark.asyncio
async def test_training_gateway_result_rejects_status_regression(client):
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    await _seed_default_training_gateway(supported_tasks=["eval"])

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    running_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "status": "running",
            "progress": 0.2,
            "metrics": {},
        },
    )
    assert running_resp.status_code == 200
    assert running_resp.json()["status"] == "running"

    queued_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "gateway_id": "node-1",
            "status": "queued",
            "metrics": {},
        },
    )
    assert queued_resp.status_code == 400

    detail_resp = await client.get(f"/api/training/jobs/{created['id']}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["status"] == "running"


@pytest.mark.asyncio
async def test_training_gateway_result_cannot_change_final_cancelled_job(client):
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
        callback_token = job.gateway_payload_json["control"]["callback_token"]

    cancel_resp = await client.post(f"/api/training/jobs/{created['id']}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    stale_resp = await client.post(
        f"/api/training/jobs/{created['id']}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": created["id"],
            "status": "completed",
            "metrics": {},
        },
    )
    assert stale_resp.status_code == 400


@pytest.mark.asyncio
async def test_training_job_dispatch_failure_keeps_job_queued(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-1",
            name="训练网关 A",
            department="EC",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["eval"],
                    "gpu_count": 0,
                    "worker_count": 1,
                },
            }),
        ))
        await db.commit()

    async def fake_submit_training_job(self, payload, *, timeout=30):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": "bridge offline"})

    monkeypatch.setattr(AIClawClient, "submit_training_job", fake_submit_training_job)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    dispatch_resp = await client.post(f"/api/training/jobs/{created['id']}/dispatch")
    assert dispatch_resp.status_code == 200
    dispatched = dispatch_resp.json()

    assert dispatched["status"] == "queued"
    assert dispatched["failure_stage"] == "dispatch"
    assert dispatched["tasks"][0]["status"] == "failed"
    assert "bridge offline" in dispatched["tasks"][0]["error_message"]


@pytest.mark.asyncio
async def test_training_job_retry_dispatch_failure_resubmits_to_bridge(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.common.audit import AuditLog
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-1",
            name="训练网关 A",
            department="EC",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["eval"],
                    "gpu_count": 0,
                    "worker_count": 1,
                },
            }),
        ))
        await db.commit()

    calls = []

    async def fake_submit_training_job(self, payload, *, timeout=30):
        calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        if len(calls) == 1:
            raise AppError("BRIDGE_OFFLINE", 503, {"detail": "bridge offline"})
        return {"accepted": True, "status": "running", "gateway_job_id": "node-1:retry", "worker_id": "worker-retry"}

    monkeypatch.setattr(AIClawClient, "submit_training_job", fake_submit_training_job)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    dispatch_resp = await client.post(f"/api/training/jobs/{created['id']}/dispatch")
    assert dispatch_resp.status_code == 200
    assert dispatch_resp.json()["failure_stage"] == "dispatch"

    retry_resp = await client.post(f"/api/training/jobs/{created['id']}/retry")
    assert retry_resp.status_code == 200
    retried = retry_resp.json()

    assert len(calls) == 2
    assert retried["status"] == "running"
    assert retried["failure_stage"] is None
    assert [task["status"] for task in retried["tasks"]] == ["failed", "running"]
    assert retried["tasks"][1]["worker_id"] == "worker-retry"

    async with async_session_factory() as db:
        entries = (await db.execute(
            select(AuditLog).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert [entry.action for entry in entries] == [
        "training_job.create",
        "training_job.approve",
        "training_job.dispatch",
        "training_job.retry",
    ]


@pytest.mark.asyncio
async def test_training_job_retry_requires_dispatch_failure(client):
    await _seed_default_training_gateway(supported_tasks=["eval"])

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    retry_resp = await client.post(f"/api/training/jobs/{created['id']}/retry")
    assert retry_resp.status_code == 400
    assert retry_resp.json()["error"]["code"] == "INVALID_STATUS"


@pytest.mark.asyncio
async def test_training_job_collect_result_pulls_from_bridge_when_callback_missing(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-1",
            name="训练网关 A",
            department="EC",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["eval"],
                    "gpu_count": 0,
                    "worker_count": 1,
                },
            }),
        ))
        await db.commit()

    collect_calls = []

    async def fake_collect_training_result(self, payload, *, timeout=15):
        collect_calls.append({
            "instance_id": self.instance_id,
            "gateway_kind": self.gateway_kind,
            "payload": payload,
            "timeout": timeout,
        })
        return {
            "job_id": payload["job_id"],
            "gateway_id": "node-1",
            "worker_id": "worker-collect",
            "status": "completed",
            "metrics": {
                "eval_score": 0.93,
                "access_token": "metric-token-should-not-leak",
            },
            "artifacts": [
                {
                    "type": "report",
                    "uri": "https://object-store/report.json?token=artifact-token-should-not-leak",
                    "sha256": "c" * 64,
                }
            ],
        }

    monkeypatch.setattr(AIClawClient, "collect_training_result", fake_collect_training_result)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "评估任务",
            "department": "EC",
            "job_type": "eval",
            "target_gateway_id": "node-1",
        },
    )
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    collect_resp = await client.post(f"/api/training/jobs/{created['id']}/collect-result")
    assert collect_resp.status_code == 200
    collected = collect_resp.json()
    encoded = json.dumps(collected, ensure_ascii=False)

    assert collect_calls == [{
        "instance_id": "node-1",
        "gateway_kind": "openclaw",
        "payload": {"job_id": created["id"]},
        "timeout": 15,
    }]
    assert collected["status"] == "completed"
    assert collected["tasks"][0]["worker_id"] == "worker-collect"
    assert collected["tasks"][0]["metrics"]["gateway_result"]["metrics"]["eval_score"] == 0.93
    assert collected["tasks"][0]["metrics"]["gateway_result"]["metrics"]["access_token"] == "[REDACTED]"
    assert collected["tasks"][0]["metrics"]["gateway_result"]["collected"] is True
    assert "metric-token-should-not-leak" not in encoded
    assert "artifact-token-should-not-leak" not in encoded

    async with async_session_factory() as db:
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert actions == [
        "training_job.create",
        "training_job.approve",
        "training_job.collect_result",
    ]


@pytest.mark.asyncio
async def test_training_job_collect_result_auto_evaluates_and_requests_deployment_review(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.training.models import TrainingModelDeployment

    await _seed_default_training_gateway()

    async def fake_collect_training_result(self, payload, *, timeout=15):
        return {
            "job_id": payload["job_id"],
            "gateway_id": "node-1",
            "worker_id": "worker-collect-auto",
            "status": "completed",
            "metrics": {"win_rate": 0.73},
            "artifacts": [
                {
                    "type": "adapter",
                    "uri": "artifact://train/collect-auto",
                    "sha256": "6" * 64,
                }
            ],
        }

    monkeypatch.setattr(AIClawClient, "collect_training_result", fake_collect_training_result)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "主动拉取训练结果后自动进入部署审核",
            "department": "EC",
            "job_type": "lora",
            "target_skill_id": "skill-collect-auto",
            "target_gateway_id": "node-1",
            "spec": {
                "eval_gate": {"min_win_rate": 0.55, "manual_review_required": True},
                "deployment": {
                    "auto_request": True,
                    "target_skill_ids": ["skill-collect-auto"],
                    "model_family": "skill-collect-auto:learning-loop-adapter",
                    "rollout_percent": 10,
                },
                "governance": {
                    "no_auto_approval": True,
                    "deployment_review_required": True,
                },
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    collect_resp = await client.post(f"/api/training/jobs/{created['id']}/collect-result")
    assert collect_resp.status_code == 200, collect_resp.text
    payload = collect_resp.json()
    deployment = payload["latest_deployment"]
    evaluation_task = payload["tasks"][-1]

    assert payload["status"] == "completed"
    assert payload["auto_evaluation"]["status"] == "evaluated"
    assert evaluation_task["metrics"]["op"] == "training.evaluate"
    assert evaluation_task["metrics"]["trigger"] == "collect_result"
    assert evaluation_task["metrics"]["auto_deployment_request"]["review_required"] is True
    assert deployment["status"] == "awaiting_review"
    assert deployment["target_skill_ids"] == ["skill-collect-auto"]
    assert deployment["approved_by"] is None
    assert deployment["activated_at"] is None

    async with async_session_factory() as db:
        stored = await db.get(TrainingModelDeployment, deployment["id"])
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == created["id"]).order_by(AuditLog.id)
        )).scalars().all()
    assert stored is not None
    assert stored.status == "awaiting_review"
    assert actions == [
        "training_job.create",
        "training_job.approve",
        "training_job.collect_result",
        "training_job.evaluate",
    ]


@pytest.mark.asyncio
async def test_training_job_collect_result_rejects_deployable_completion_without_artifact_contract(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    await _seed_default_training_gateway()

    async def fake_collect_training_result(self, payload, *, timeout=15):
        return {
            "job_id": payload["job_id"],
            "gateway_id": "node-1",
            "worker_id": "worker-missing-artifact",
            "status": "completed",
            "metrics": {"win_rate": 0.8},
            "artifacts": [{"type": "adapter", "sha256": "1" * 64}],
        }

    monkeypatch.setattr(AIClawClient, "collect_training_result", fake_collect_training_result)

    create_resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "主动拉取缺少产物 URI",
            "department": "EC",
            "job_type": "lora",
            "target_gateway_id": "node-1",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    await client.post(f"/api/training/jobs/{created['id']}/approve")

    collect_resp = await client.post(f"/api/training/jobs/{created['id']}/collect-result")
    assert collect_resp.status_code == 422
    assert collect_resp.json()["error"]["code"] == "TRAINING_RESULT_ARTIFACT_REQUIRED"

    async with async_session_factory() as db:
        job = await db.get(TrainingJob, created["id"])
    assert job.status == "queued"


@pytest.mark.asyncio
async def test_training_job_collect_due_only_pulls_stale_active_jobs(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.common.audit import AuditLog
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob

    old_time = now_bjt() - timedelta(minutes=10)
    fresh_time = now_bjt()
    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-1",
            name="训练网关 A",
            department="EC",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.collect_result"],
                "training": {"gateway": True, "supported_tasks": ["eval"]},
            }),
        ))
        db.add_all([
            TrainingJob(
                id="train_old_running",
                title="旧运行任务",
                department="EC",
                created_by="admin",
                status="running",
                job_type="eval",
                target_gateway_id="node-1",
                approval_required=True,
                created_at=old_time,
                updated_at=old_time,
            ),
            TrainingJob(
                id="train_fresh_running",
                title="新运行任务",
                department="EC",
                created_by="admin",
                status="running",
                job_type="eval",
                target_gateway_id="node-1",
                approval_required=True,
                created_at=fresh_time,
                updated_at=fresh_time,
            ),
        ])
        await db.commit()

    collect_calls = []

    async def fake_collect_training_result(self, payload, *, timeout=15):
        collect_calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "job_id": payload["job_id"],
            "gateway_id": "node-1",
            "status": "completed",
            "metrics": {"eval_score": 0.91},
        }

    monkeypatch.setattr(AIClawClient, "collect_training_result", fake_collect_training_result)

    collect_resp = await client.post(
        "/api/training/jobs/collect-due",
        json={"stale_seconds": 300, "max_jobs": 10},
    )
    assert collect_resp.status_code == 200
    payload = collect_resp.json()

    assert payload["stats"] == {"collected": 1, "skipped": 0, "failed": 0}
    assert payload["items"][0]["updated_at"]
    assert payload["items"][0] == {
        "job_id": "train_old_running",
        "status": "collected",
        "result_status": "completed",
        "updated_at": payload["items"][0]["updated_at"],
    }
    assert collect_calls == [{
        "instance_id": "node-1",
        "payload": {"job_id": "train_old_running"},
        "timeout": 15,
    }]

    async with async_session_factory() as db:
        old_job = await db.get(TrainingJob, "train_old_running")
        fresh_job = await db.get(TrainingJob, "train_fresh_running")
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.target_id == "train_old_running").order_by(AuditLog.id)
        )).scalars().all()
    assert old_job.status == "completed"
    assert fresh_job.status == "running"
    assert actions == ["training_job.collect_result"]


@pytest.mark.asyncio
async def test_training_job_list_is_scoped_by_department(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.training.models import TrainingJob
    from app.training.service import list_training_jobs

    async with async_session_factory() as db:
        db.add_all([
            TrainingJob(
                id="train_ec",
                title="EC 训练",
                department="EC",
                created_by="admin",
                status="awaiting_review",
                job_type="lora",
            ),
            TrainingJob(
                id="train_hr",
                title="HR 训练",
                department="HR",
                created_by="admin",
                status="awaiting_review",
                job_type="lora",
            ),
        ])
        await db.commit()

        user = User(
            id="u-ec",
            username="u-ec",
            name="EC 用户",
            role="operator",
            department="EC",
            state="active",
            is_active=True,
        )
        result = await list_training_jobs(db, user)

    assert [item["id"] for item in result["items"]] == ["train_ec"]


@pytest.mark.asyncio
async def test_training_job_list_can_filter_by_gateway_context(client):
    from app.database import async_session_factory
    from app.training.models import TrainingJob

    async with async_session_factory() as db:
        db.add_all([
            TrainingJob(
                id="train_gateway_a",
                title="网关 A 训练",
                department="EC",
                created_by="admin",
                status="running",
                job_type="lora",
                target_gateway_id="node-a",
            ),
            TrainingJob(
                id="train_gateway_b",
                title="网关 B 训练",
                department="EC",
                created_by="admin",
                status="running",
                job_type="lora",
                target_gateway_id="node-b",
            ),
        ])
        await db.commit()

    resp = await client.get("/api/training/jobs", params={"gateway": "node-a"})

    assert resp.status_code == 200
    payload = resp.json()
    assert [item["id"] for item in payload["items"]] == ["train_gateway_a"]
    assert payload["stats"]["running"] == 1


@pytest.mark.asyncio
async def test_training_collect_due_can_filter_by_gateway_context(client, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.training import service as training_service
    from app.training.models import TrainingJob

    updated_at = now_bjt() - timedelta(minutes=10)
    async with async_session_factory() as db:
        db.add_all([
            TrainingJob(
                id="train_due_a",
                title="网关 A 待同步",
                department="EC",
                created_by="admin",
                status="running",
                job_type="lora",
                target_gateway_id="node-a",
                updated_at=updated_at,
            ),
            TrainingJob(
                id="train_due_b",
                title="网关 B 待同步",
                department="EC",
                created_by="admin",
                status="running",
                job_type="lora",
                target_gateway_id="node-b",
                updated_at=updated_at,
            ),
        ])
        await db.commit()

    collected_ids = []

    async def fake_collect_training_job_result(db, user, job_id):
        collected_ids.append(job_id)
        return {"id": job_id, "status": "completed", "updated_at": "2026-05-22T10:00:00+08:00"}

    monkeypatch.setattr(training_service, "collect_training_job_result", fake_collect_training_job_result)

    resp = await client.post(
        "/api/training/jobs/collect-due",
        json={"stale_seconds": 0, "max_jobs": 20, "target_gateway_id": "node-a"},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert collected_ids == ["train_due_a"]
    assert [item["job_id"] for item in payload["items"]] == ["train_due_a"]
    assert payload["target_gateway_id"] == "node-a"
    assert payload["stats"]["collected"] == 1


@pytest.mark.asyncio
async def test_training_deployment_list_is_scoped_by_department(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment
    from app.training.service import list_training_deployments

    async with async_session_factory() as db:
        db.add_all([
            TrainingJob(
                id="train_ec_deploy",
                title="EC 训练",
                department="EC",
                created_by="admin",
                status="completed",
                job_type="lora",
            ),
            TrainingJob(
                id="train_hr_deploy",
                title="HR 训练",
                department="HR",
                created_by="admin",
                status="completed",
                job_type="lora",
            ),
            TrainingModelDeployment(
                id="deploy_ec",
                job_id="train_ec_deploy",
                department="EC",
                model_family="ec_ranker",
                artifact_id="artifact-ec",
                target_skill_ids_json=["skill-ec"],
                status="active",
                requested_by="admin",
            ),
            TrainingModelDeployment(
                id="deploy_hr",
                job_id="train_hr_deploy",
                department="HR",
                model_family="hr_ranker",
                artifact_id="artifact-hr",
                target_skill_ids_json=["skill-hr"],
                status="active",
                requested_by="admin",
            ),
        ])
        await db.commit()

        user = User(
            id="u-ec",
            username="u-ec",
            name="EC 用户",
            role="operator",
            department="EC",
            state="active",
            is_active=True,
        )
        result = await list_training_deployments(db, user)

    assert [item["id"] for item in result["items"]] == ["deploy_ec"]
    assert result["items"][0]["job"]["id"] == "train_ec_deploy"


@pytest.mark.asyncio
async def test_training_deployment_chat_endpoint_runs_selected_model(client):
    from app.aiclaw.bridge_registry import bridge_registry
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="node-training-chat",
            name="训练后模型推理节点",
            department="AI小组",
            gateway_url="ws://node-training-chat",
            reload_hook_url="",
            reload_token="",
            is_active=True,
            agent_purpose="training",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.inference", "training.artifact_status"],
                "gpu": [{"name": "RTX 4090", "vram_total_mb": 24564, "vram_free_mb": 24000}],
            }),
        ))
        db.add(TrainingJob(
            id="train-deployment-chat",
            title="大厅模型直聊",
            department="AI小组",
            created_by="admin",
            status="completed",
            job_type="lora",
            target_gateway_id="node-training-chat",
            target_skill_id="skill-chat",
            spec_json={"model": {"profile": "qwen3.5-4b"}},
        ))
        db.add(TrainingModelDeployment(
            id="deploy-deployment-chat",
            job_id="train-deployment-chat",
            department="AI小组",
            model_family="hall_chat_adapter",
            artifact_id="artifact-chat",
            artifact_ref_json={"id": "artifact-chat", "uri": "file:///models/hall_chat", "sha256": "a" * 64},
            target_skill_ids_json=["skill-chat"],
            status="active",
            rollout_percent=100,
            requested_by="admin",
            deployment_target_gateway_id="node-training-chat",
        ))
        db.add(TrainingJobTask(
            job_id="train-deployment-chat",
            gateway_id="node-training-chat",
            status="completed",
            progress=100,
            metrics_json={
                "gateway_result": {
                    "status": "completed",
                    "artifacts": [
                        {"id": "artifact-chat", "uri": "file:///models/hall_chat", "sha256": "a" * 64},
                    ],
                },
            },
        ))
        await db.commit()

    ws = _TrainingChatDummyWS()
    conn = await bridge_registry.register("node-training-chat", ws)
    try:
        resp_task = asyncio.create_task(client.post(
            "/api/training/deployments/deploy-deployment-chat/chat",
            json={"prompt": "请说明模型是否可用", "max_tokens": 96, "timeout_seconds": 120},
        ))
        await _wait_training_chat_sent(ws)
        status_request = ws.sent[0]
        assert status_request["type"] == "bridge_op"
        assert status_request["op"] == "training.artifact_status"
        await conn.handle_bridge_op_response({
            "request_id": status_request["request_id"],
            "epoch": status_request["epoch"],
            "ok": True,
            "result": {"exists": True, "sha256": "a" * 64, "size_bytes": 123},
        })

        await _wait_training_chat_sent(ws, 2)
        inference_request = ws.sent[1]
        assert inference_request["type"] == "bridge_op"
        assert inference_request["op"] == "training.inference"
        assert inference_request["payload"]["deployment_id"] == "deploy-deployment-chat"
        assert inference_request["payload"]["max_new_tokens"] == 96
        assert inference_request["payload"]["timeout_seconds"] == 120
        assert "请说明模型是否可用" in inference_request["payload"]["prompt"]

        await conn.handle_bridge_op_response({
            "request_id": inference_request["request_id"],
            "epoch": inference_request["epoch"],
            "ok": True,
            "result": {
                "text": "<think>内部推理</think>\n\n模型已可用",
                "metrics": {"generated_tokens": 6, "duration_ms": 1500},
                "finish_reason": "stop",
            },
        })
        resp = await resp_task
    finally:
        await bridge_registry.unregister("node-training-chat")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_deployment_id"] == "deploy-deployment-chat"
    assert body["gateway_id"] == "node-training-chat"
    assert body["skillforge_backend"] == "bridge_local_lora_inference"
    assert body["choices"][0]["message"]["content"] == "模型已可用"
    assert body["metrics"]["tokens_per_second"] == 4


@pytest.mark.asyncio
async def test_training_deployment_chat_endpoint_rejects_unavailable_status(client):
    from app.database import async_session_factory
    from app.training.models import TrainingJob, TrainingModelDeployment

    async with async_session_factory() as db:
        db.add(TrainingJob(
            id="train-deployment-chat-rolled-back",
            title="已回滚模型",
            department="AI小组",
            created_by="admin",
            status="completed",
            job_type="lora",
        ))
        db.add(TrainingModelDeployment(
            id="deploy-deployment-chat-rolled-back",
            job_id="train-deployment-chat-rolled-back",
            department="AI小组",
            model_family="rolled_back_chat_adapter",
            artifact_id="artifact-rolled-back",
            artifact_ref_json={"id": "artifact-rolled-back", "uri": "file:///models/rolled_back"},
            target_skill_ids_json=["skill-chat"],
            status="rolled_back",
            rollout_percent=0,
            requested_by="admin",
        ))
        await db.commit()

    resp = await client.post(
        "/api/training/deployments/deploy-deployment-chat-rolled-back/chat",
        json={"prompt": "还能对话吗？"},
    )

    assert resp.status_code == 400
    assert "灰度或激活" in resp.text


@pytest.mark.asyncio
async def test_training_job_create_rejects_unsupported_job_type(client):
    resp = await client.post(
        "/api/training/jobs",
        json={
            "title": "危险训练",
            "department": "EC",
            "job_type": "shell",
        },
    )
    assert resp.status_code == 422
