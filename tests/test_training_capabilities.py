from types import SimpleNamespace
import json

from app.aiclaw.bridge_protocol import BridgeCapabilitiesFrame
from app.aiclaw.bridge_router import _sanitize_training_capability
from app.aiclaw.router import _training_summary_from_capabilities


def test_bridge_capabilities_frame_accepts_training_payload():
    frame = BridgeCapabilitiesFrame.model_validate({
        "type": "bridge_capabilities",
        "resident_models": [{
            "model": "qwen3.5-4b-lora",
            "deployment_id": "deploy-mac-active",
            "loaded": True,
        }],
        "training": {
            "gateway": True,
            "supported_tasks": ["lora", "qlora", "eval"],
            "gpu_count": 2,
            "worker_count": 2,
        },
    })

    assert frame.training["gateway"] is True
    assert frame.training["supported_tasks"] == ["lora", "qlora", "eval"]
    assert frame.resident_models[0]["model"] == "qwen3.5-4b-lora"


def test_training_capability_sanitizer_keeps_only_whitelisted_tasks():
    sanitized = _sanitize_training_capability({
        "gateway": True,
        "supported_tasks": ["lora", "shell", "qlora", "../escape", "eval"],
        "gpu_count": "2",
        "worker_count": "99999",
        "resident_models": [{
            "model": "qwen3.5-4b-lora",
            "deployment_id": "deploy-mac-active",
            "artifact_uri": "/Users/secret/models/adapter",
            "loaded": True,
        }],
    })

    assert sanitized["gateway"] is True
    assert sanitized["supported_tasks"] == ["lora", "qlora", "eval"]
    assert sanitized["gpu_count"] == 2
    assert sanitized["worker_count"] == 1024
    assert sanitized["resident_models"][0]["model"] == "qwen3.5-4b-lora"
    assert "secret" not in json.dumps(sanitized, ensure_ascii=False)


def test_bridge_capability_store_payload_can_include_sanitized_training():
    cap = SimpleNamespace(
        training={
            "gateway": True,
            "supported_tasks": ["lora", "bad-op", "inference"],
            "gpu_count": 1,
            "worker_count": 1,
        },
    )

    sanitized = _sanitize_training_capability(cap.training)

    assert sanitized == {
        "gateway": True,
        "supported_tasks": ["lora", "inference"],
        "gpu_count": 1,
        "worker_count": 1,
    }


def test_instance_training_summary_sanitizes_list_payload():
    summary = _training_summary_from_capabilities(json.dumps({
        "home": "/home/secret-user",
        "skills_dirs": ["/home/secret-user/.openclaw/skills"],
        "training": {
            "gateway": True,
            "supported_tasks": ["lora", "shell", "eval"],
            "gpu_count": "2",
            "worker_count": "3",
        },
        "gpu": [{
            "name": "RTX 4090",
            "vram_total_mb": 24576,
            "vram_free_mb": 20480,
        }],
    }))

    assert summary == {
        "gateway": True,
        "supported_tasks": ["lora", "eval"],
        "gpu_count": 2,
        "worker_count": 3,
        "vram_total_gb": 24.0,
        "vram_free_gb": 20.0,
    }
    assert "secret-user" not in json.dumps(summary, ensure_ascii=False)
