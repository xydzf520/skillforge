import json
import re

import pytest


async def _seed_gb10_training_gateway() -> None:
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance

    async with async_session_factory() as db:
        db.add(OpenClawInstance(
            id="training-primary",
            name="GB10 237 · NVIDIA GB10",
            department="AI小组",
            gateway_url="bridge://training-primary",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            is_active=True,
            is_platform_default=True,
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["qlora", "eval", "inference"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
                "gpu": [{"name": "NVIDIA GB10", "vram_total_mb": 131072, "vram_free_mb": 120000}],
            }),
        ))
        await db.commit()


@pytest.mark.asyncio
async def test_training_asset_registry_builds_vision_dataset_and_job(client):
    await _seed_gb10_training_gateway()

    source = (
        await client.post(
            "/api/training/assets/sources",
            json={
                "source_type": "knowledge_media",
                "source_id": "km-image-1",
                "title": "商品主图",
                "department": "AI小组",
                "modality": "image_text",
                "storage_backend": "knowledge_media",
                "storage_ref": "data/knowledge_media/km-image-1.png",
                "mime_type": "image/png",
                "byte_size": 128,
                "sha256": "a" * 64,
            },
        )
    ).json()["asset_source"]

    sample = (
        await client.post(
            "/api/training/assets/samples",
            json={
                "source_id": source["id"],
                "dataset_profile": "vision_instruction_v1",
                "content": {
                    "messages": [
                        {"role": "user", "content": "判断图片中的商品是否适合投放。"},
                        {"role": "assistant", "content": "适合投放，主图清晰且卖点明确。"},
                    ],
                    "expected_output": "适合投放",
                },
                "media_refs": [{
                    "source_type": "knowledge_media",
                    "asset_id": "km-image-1",
                    "storage_ref": "data/knowledge_media/km-image-1.png",
                    "mime_type": "image/png",
                    "sha256": "a" * 64,
                }],
                "labels": ["vision", "ad_review"],
                "quality_score": 0.93,
            },
        )
    ).json()["sample"]

    dataset_response = (
        await client.post(
            "/api/training/dataset-versions",
            json={
                "name": "ad-vision",
                "version": "v1",
                "dataset_profile": "vision_instruction_v1",
                "sample_ids": [sample["id"]],
            },
        )
    ).json()
    dataset = dataset_response["dataset_version"]

    manifest = (await client.get(f"/api/training/dataset-versions/{dataset['id']}/manifest")).json()
    assert dataset["modality"] == "image_text"
    assert dataset["sample_count"] == 1
    assert dataset["media_count"] == 1
    assert dataset["sync_status"] == "pending"
    assert dataset["synced_sink_job_id"].startswith("tdsync_")
    assert dataset_response["auto_sync"]["training_sink"]["target_gateway_id"] == "training-primary"
    assert dataset["automation_status"]["steps"][-1]["key"] == "gb10_sync"
    assert manifest["media_policy"] == "reference_only"
    assert "data/knowledge_media/km-image-1.png" in manifest["jsonl"]

    job = (
        await client.post(
            "/api/training/jobs",
            json={
                "title": "图文理解微调",
                "dataset_version_id": dataset["id"],
                "objective": "学习图片和文字联合判断投放质量。",
            },
        )
    ).json()

    assert job["job_type"] == "multimodal_sft"
    assert job["target_gateway_id"] == "training-primary"
    assert job["dataset_ref"] == dataset["manifest_uri"]
    assert job["spec"]["dataset"]["dataset_profile"] == "vision_instruction_v1"
    assert job["spec"]["dataset"]["media_policy"] == "reference_only"
    assert job["spec"]["dataset"]["sync_status"] == "pending"
    assert job["spec"]["automation"]["auto_evaluate_on_result"] is True
    assert job["spec"]["deployment"]["auto_request"] is True
    assert job["spec"]["deployment"]["auto_active"] is True
    assert job["deployment_target_gateway_id"] == "inference-primary"
    assert job["deployment_runtime_profile"] == "mlx-qwen3.6-35b-a3b-lora"
    assert re.search(r":20\d{6}$", job["training_plan"]["model_name"])
    assert job["automation_status"]["steps"][-1]["key"] == "deploy"

    automation = (await client.get("/api/training/automation/status")).json()
    assert automation["policy"]["training_gateway_id"] == "training-primary"
    assert automation["policy"]["deployment_gateway_id"] == "inference-primary"


@pytest.mark.asyncio
async def test_project_sdk_token_records_training_sample_and_syncs_dataset(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "sdk_training_project",
            "name": "SDK Training Project",
            "type": "external_web",
            "department": "AI小组",
            "entry_url": "https://example.com/sdk-training",
            "metadata": {"capabilities": ["ai.chat"]},
        },
    )
    token = (
        await client.post(
            "/api/projects/sdk_training_project/tokens",
            json={
                "name": "training-writer",
                "scopes": [
                    "runs:create",
                    "runs:assets",
                    "training:assets",
                    "training:samples",
                    "training:datasets:create",
                    "training:datasets:sync",
                    "training:sync",
                ],
            },
        )
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    run = (
        await client.post(
            "/api/projects/sdk/runs",
            headers=headers,
            json={"project_id": "sdk_training_project", "request_id": "sdk-training-run"},
        )
    ).json()

    upload = (
        await client.post(
            f"/api/projects/sdk/runs/{run['id']}/assets",
            headers=headers,
            files={"file": ("frame.png", b"fake-image-bytes", "image/png")},
            data={"metadata_json": json.dumps({"purpose": "training"}, ensure_ascii=False)},
        )
    ).json()
    asset_id = upload["asset"]["id"]
    assert upload["trainingAsset"]["asset_source"]["source_type"] == "project_run_asset"

    sample = (
        await client.post(
            f"/api/projects/sdk/runs/{run['id']}/training-samples",
            headers=headers,
            json={
                "asset_id": asset_id,
                "dataset_profile": "vision_instruction_v1",
                "content": {
                    "messages": [
                        {"role": "user", "content": "这张图片有什么风险？"},
                        {"role": "assistant", "content": "没有明显风险。"},
                    ]
                },
                "labels": ["sdk", "vision"],
                "quality_score": 0.8,
            },
        )
    ).json()
    assert sample["trainingSample"]["dataset_profile"] == "vision_instruction_v1"
    assert sample["trainingSink"]["target_gateway_id"] == "GB10 237"

    assets = (await client.get("/api/projects/sdk/training/assets", headers=headers)).json()
    assert any(item["source_id"] == asset_id for item in assets["items"])

    dataset = (
        await client.post(
            "/api/projects/sdk/training/datasets",
            headers=headers,
            json={"name": "sdk-vision", "dataset_profile": "vision_instruction_v1", "limit": 20},
        )
    ).json()["datasetVersion"]
    assert dataset["sample_count"] == 1
    assert dataset["manifest_sha256"]
    assert dataset["sync_status"] == "pending"
    assert dataset["synced_sink_job_id"].startswith("tdsync_")

    sync = (
        await client.post(
            f"/api/projects/sdk/training/datasets/{dataset['id']}/sync",
            headers=headers,
            json={"target_gateway_id": "training-primary"},
        )
    ).json()
    assert sync["training_sink"]["status"] == "pending"
    assert sync["training_sink"]["target_gateway_id"] == "training-primary"
