"""训练控制面资源聚合。

Training 仍是 SkillForge 控制面：这里只汇总 Agent/Bridge 上报的训练资源，
不在 Web 进程内执行训练，也不读取训练节点文件系统。
"""

from __future__ import annotations

import asyncio
import json
import base64
import hashlib
import hmac
import re
from datetime import datetime, timedelta
from time import time
from types import SimpleNamespace
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse, urlunparse

from loguru import logger
from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aiclaw.client import AIClawClient
from app.aiclaw.bridge_registry import bridge_registry
from app.auth.access import role_matches_any
from app.auth.models import User
from app.common.ai import redact_secret_text
from app.common.audit import AuditLog
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.config import settings
from app.execution.models import DecisionLog, ExecutionRun, ExecutionStep, OpenClawInstance, SAMPLE_RUN_MODES
from app.skills.core.access import build_skill_access_filter, get_skill_permissions, require_skill_access
from app.skills.core.models import Skill
from app.training.models import (
    TrainingAssetSource,
    TrainingDatasetVersion,
    TrainingFullHistoryAutomationRun,
    TrainingJob,
    TrainingJobTask,
    TrainingModelDeployment,
    TrainingModelTransfer,
    TrainingSample,
    TrainingStorageLocation,
)
from uuid import uuid4

TRAINING_TASKS_ALLOWED = {"lora", "qlora", "eval", "merge", "inference"}
TRAINING_JOB_TYPES = {"lora", "qlora", "eval", "merge", "inference", "text_sft", "multimodal_sft", "vlm_lora"}
TRAINING_JOB_GATEWAY_TASK_ALIASES = {
    "text_sft": "lora",
    "multimodal_sft": "qlora",
    "vlm_lora": "qlora",
}
TRAINING_MODALITIES = {"text", "image", "image_text", "video_frame", "mixed"}
TRAINING_DATASET_PROFILES = {
    "text_sft_v1",
    "vision_instruction_v1",
    "image_caption_v1",
    "vision_eval_case_v1",
    "preference_v1",
}
TRAINING_DATASET_PROFILE_MODALITY = {
    "text_sft_v1": "text",
    "vision_instruction_v1": "image_text",
    "image_caption_v1": "image_text",
    "vision_eval_case_v1": "image_text",
    "preference_v1": "text",
}
TRAINING_ASSET_SOURCE_TYPES = {
    "project_run",
    "project_run_asset",
    "project_ingress_event",
    "knowledge_media",
    "learning_artifact",
    "execution_artifact",
    "manual_upload",
    "sdk_upload",
}
TRAINING_ASSET_STATUSES = {"active", "archived", "blocked"}
TRAINING_SAMPLE_STATUSES = {"ready", "draft", "excluded", "blocked"}
TRAINING_DATASET_VERSION_STATUSES = {"draft", "ready", "approved", "archived"}
TRAINING_JOB_STATUSES = {
    "awaiting_review",
    "queued",
    "running",
    "evaluating",
    "completed",
    "failed",
    "cancelled",
    "unknown",
}
FINAL_TRAINING_JOB_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_TRAINING_TASK_STATUSES = {"pending", "dispatching", "queued", "running", "evaluating"}
TRAINING_GATEWAY_RESULT_STATUSES = {
    "queued",
    "running",
    "evaluating",
    "completed",
    "failed",
    "cancelled",
    "unknown",
}
TRAINING_GATEWAY_STATUS_TRANSITIONS = {
    "queued": {"queued", "running", "evaluating", "completed", "failed", "cancelled", "unknown"},
    "running": {"running", "evaluating", "completed", "failed", "cancelled", "unknown"},
    "evaluating": {"evaluating", "completed", "failed", "cancelled", "unknown"},
    "unknown": {"running", "evaluating", "completed", "failed", "cancelled", "unknown"},
    "completed": {"completed"},
    "failed": {"failed"},
    "cancelled": {"cancelled"},
}
TRAINING_JOB_CREATE_ROLES = {"system_admin", "admin", "dept_admin", "aibp", "ai_engineer", "biz_owner"}
TRAINING_JOB_APPROVE_ROLES = {"system_admin", "admin", "dept_admin"}
TRAINING_DEPLOYMENT_OPEN_STATUSES = {"candidate", "evaluated", "awaiting_review", "canary", "active"}
TRAINING_DEPLOYMENT_ACTIVE_STATUSES = {"canary", "active"}
TRAINING_DEPLOYMENT_STATUSES = {
    "candidate",
    "evaluated",
    "awaiting_review",
    "canary",
    "active",
    "rejected",
    "failed",
    "rolled_back",
}
TRAINING_COLLECT_DUE_STATUSES = {"queued", "running", "evaluating", "unknown"}
TRAINING_RESOURCE_ACTIVE_JOB_STATUSES = {"queued", "running", "evaluating", "unknown"}
TRAINING_MODEL_TRANSFER_OPEN_STATUSES = {"queued", "running"}
TRAINING_MODEL_TRANSFER_FINAL_STATUSES = {"succeeded", "failed", "cancelled"}
MAX_TRAINING_SPEC_BYTES = 64 * 1024
MAX_TRAINING_RESULT_BYTES = 128 * 1024
MAX_TRAINING_RESULT_SANITIZE_DEPTH = 6
MAX_TRAINING_RESULT_LIST_ITEMS = 200
MAX_TRAINING_ARTIFACT_DOWNLOAD_BYTES = 64 * 1024 * 1024
TRAINING_CALLBACK_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60
TRAINING_CALLBACK_TOKEN_AUDIENCE = "training_gateway_result"
TRAINING_CANDIDATE_MAX_LOGS = 1000
TRAINING_DATASET_LIST_LIMIT = 200
TRAINING_CANDIDATE_THRESHOLDS = {
    "sft_samples": 100,
    "preference_samples": 50,
    "action_outcome_samples": 100,
    "eval_samples": 30,
    "max_recent_failure_rate": 0.30,
}
TRAINING_GATEWAY_PAYLOAD_MAX_BYTES = 8 * 1024 * 1024
TRAINING_GATEWAY_DATASET_PACKAGE_MAX_BYTES = 7 * 1024 * 1024
TRAINING_GATEWAY_DATASET_MAX_SAMPLE_IDS = 5000
TRAINING_GATEWAY_DATASET_MAX_INLINE_SAMPLES = 256
TRAINING_GATEWAY_DATASET_FIELD_CHARS = 900
TRAINING_GATEWAY_DATASET_COMPACT_DEPTH = 6
TRAINING_GATEWAY_DATASET_COMPACT_DICT_ITEMS = 24
TRAINING_GATEWAY_DATASET_COMPACT_LIST_ITEMS = 12
TRAINING_GATEWAY_DATASET_COMPACT_STRING_CHARS = 700
TRAINING_GATEWAY_DATASET_INLINE_MAX_CONTENT_BYTES = 200 * 1024
TRAINING_GATEWAY_DATASET_DEFAULT_EVAL_INLINE_SAMPLES = 16
TRAINING_GATEWAY_DATASET_PACKING_VERSION = "eval-reserve-v2"
TRAINING_GATEWAY_REQUIRED_OPS = ("training.submit_job", "training.collect_result", "training.inference")
TRAINING_DEPLOYABLE_JOB_TYPES = {"lora", "qlora", "merge", "text_sft", "multimodal_sft", "vlm_lora"}
TRAINING_DEPLOYMENT_MODEL_READY_STATUSES = {"ready", "prepared", "available", "completed", "succeeded"}
TRAINING_GATEWAY_DATASET_MIN_INLINE_SAMPLES = 4
TRAINING_BOOTSTRAP_PROFILES_ALLOWED = {"qlora-1b"}
TRAINING_BOOTSTRAP_CUDA_ALLOWED = {"cu121", "cu130"}
TRAINING_BOOTSTRAP_AGENT_PURPOSE_ALLOWED = {"training", "mixed"}
TRAINING_QWEN35_4B_PROFILE = "qwen3.5-4b"
TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
TRAINING_DEFAULT_LEARNING_MODEL_PROFILE = TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE
TRAINING_MODEL_PROFILES_ALLOWED = {TRAINING_QWEN35_4B_PROFILE, TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE}
TRAINING_MODEL_VALIDATE_MODES_ALLOWED = {"none", "metadata", "load_4bit"}
TRAINING_MODEL_INTERNAL_SOURCES = {"internal", "local_path", "artifact"}
TRAINING_MODEL_PUBLIC_SOURCES = {"auto", "huggingface", "modelscope"}
TRAINING_MODEL_SOURCES_ALLOWED = TRAINING_MODEL_PUBLIC_SOURCES | TRAINING_MODEL_INTERNAL_SOURCES
TRAINING_RUNTIME_ENV_ALLOWED = {
    "SKILLFORGE_QWEN36_35B_MODEL_DIR",
    "SKILLFORGE_OPENWEBUI_ENABLED",
    "SKILLFORGE_OPENWEBUI_HOST",
    "SKILLFORGE_OPENWEBUI_PUBLIC_HOST",
    "SKILLFORGE_OPENWEBUI_PORT",
}
TRAINING_PRIMARY_GATEWAY_IDS = ("training-primary",)
TRAINING_GB10_SEED_GATEWAY_IDS = {"training-primary"}
TRAINING_COMFYUI_RESERVED_GATEWAY_IDS = {"data-primary"}
TRAINING_DEFAULT_DATASET_SOURCE_GATEWAY_ID = "data-primary"
TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID = "inference-primary"
TRAINING_MAC_DEPLOYMENT_SEED_GATEWAY_IDS = {"inference-primary"}
TRAINING_AUTO_ENABLED = settings.TRAINING_AUTOMATION_ENABLED
TRAINING_AUTO_DATASET_SYNC_ENABLED = True
TRAINING_AUTO_APPROVE_JOBS = settings.TRAINING_AUTOMATION_ENABLED
TRAINING_AUTO_EVALUATE_ON_RESULT = True
TRAINING_AUTO_REQUEST_DEPLOYMENT = settings.TRAINING_AUTOMATION_ENABLED
TRAINING_AUTO_APPROVE_DEPLOYMENT = settings.TRAINING_AUTOMATION_ENABLED
TRAINING_AUTO_ACTIVATE_DEPLOYMENT = settings.TRAINING_AUTOMATION_ENABLED
TRAINING_AUTO_INCLUDE_MANUAL_JOBS = True
TRAINING_AUTO_MODEL_DATE_FORMAT = "%Y%m%d"
TRAINING_AUTOMATION_LAST_RUN: dict[str, Any] | None = None
TRAINING_DATASET_BRIDGE_REQUIRED_SOURCE_OPS = (
    "training.dataset_write_chunk",
    "training.dataset_commit",
    "training.dataset_export_start",
    "training.dataset_export_manifest",
    "training.dataset_export_file_chunk",
)
TRAINING_DATASET_BRIDGE_REQUIRED_TARGET_OPS = (
    "training.dataset_import_from_export",
    "training.dataset_import_relay_chunk",
    "training.dataset_import_relay_commit",
)
TRAINING_ADULT_PRODUCT_REVIEW_KEYWORDS = (
    "安全套",
    "润滑液",
    "成人用品",
    "情趣用品",
    "避孕套",
    "延时喷剂",
    "飞机杯",
    "震动棒",
    "私密护理",
    "计生用品",
)
TRAINING_MODEL_TRANSFER_HOST_OVERRIDES = settings.TRAINING_MODEL_TRANSFER_HOSTS
TRAINING_MODEL_TRANSFER_RELAY_CHUNK_BYTES = 8 * 1024 * 1024
TRAINING_DATASET_TRANSFER_CHUNK_BYTES = 64 * 1024
TRAINING_MODEL_TRANSFER_RELAY_RETRY_DELAYS = (3, 5, 10, 20, 30, 60, 60, 60, 60, 60, 60, 60)
TRAINING_DEFAULT_DEPLOYMENT_RUNTIME_PROFILE = "mlx-qwen3.5-4b-lora"
TRAINING_QWEN36_DEPLOYMENT_RUNTIME_PROFILE = "mlx-qwen3.6-35b-a3b-lora"
TRAINING_FULL_HISTORY_CHAT_SKILL_ID = "skillforge-finetuned-model-chat"
TRAINING_FULL_HISTORY_CHAT_SKILL_NAME = "全量微调大模型对话"
TRAINING_FULL_HISTORY_CYCLE_COUNT = 3
TRAINING_FULL_HISTORY_MODEL_PROFILE = TRAINING_QWEN35_4B_PROFILE
TRAINING_FULL_HISTORY_MODEL_SOURCE = "auto"
TRAINING_FULL_HISTORY_MODEL_FAMILY_SUFFIX = "full-history-4b-chat-adapter"
TRAINING_FULL_HISTORY_CHAT_DEFAULT_PROMPT = "请用一句话说明你当前使用的是 SkillForge 全量数据微调模型。"
TRAINING_FULL_HISTORY_EXTERNAL_SAMPLE_LIMIT = 5000
TRAINING_FULL_HISTORY_BACKGROUND_OPEN_STATUSES = {"awaiting_review", "queued", "completed"}
TRAINING_FULL_HISTORY_BACKGROUND_ACTIVE_STATUSES = {"running", "evaluating", "unknown"}
TRAINING_FULL_HISTORY_BACKGROUND_FINAL_STATUSES = {"failed", "cancelled"}
TRAINING_FULL_HISTORY_COMPLETED_DEPLOYMENT_STATUSES = TRAINING_DEPLOYMENT_ACTIVE_STATUSES | {"rolled_back"}
TRAINING_FULL_HISTORY_AUTOMATION_OPEN_STATUSES = {"queued", "running", "blocked"}
TRAINING_FULL_HISTORY_AUTOMATION_FINAL_STATUSES = {"completed", "failed", "cancelled"}
TRAINING_FULL_HISTORY_AUTO_DAYS = 3650
TRAINING_FULL_HISTORY_AUTO_PER_SOURCE_LIMIT = 2000
TRAINING_FULL_HISTORY_AUTO_MATERIALIZE_LIMIT = 50000
TRAINING_MODEL_DISCOVER_ALL_DEFAULT_GATEWAY_IDS = (
    "training-primary",
    "inference-primary",
    "data-primary",
)
TRAINING_MODEL_DISCOVER_ALL_MAX_ROOTS = 32
TRAINING_MODEL_DISCOVER_ALL_MAX_TERMS = 20
TRAINING_MODEL_DISCOVER_ALL_MAX_DIRS = 50000
TRAINING_MODEL_DISCOVER_ALL_MAX_DEPTH = 12
TRAINING_MODEL_DISCOVER_ALL_MAX_LIMIT = 50
TRAINING_DEFAULT_STORAGE_LOCATION_ID = "gb10-237-training-assets"
TRAINING_DEFAULT_STORAGE_LOCATION_NAME = "GB10 237 · NVIDIA GB10 训练资产"
TRAINING_DEFAULT_STORAGE_BACKEND = "gb10"
TRAINING_ASSET_LIST_LIMIT = 200
TRAINING_SAMPLE_LIST_LIMIT = 500
TRAINING_DATASET_VERSION_MAX_SAMPLES = 5000
TRAINING_DATASET_MANIFEST_SCHEMA_VERSION = "training-dataset-manifest-v1"
_TRAINING_MODEL_TRANSFER_TASKS: set[str] = set()
_FULL_HISTORY_FINETUNE_AUTOMATION_TASK: asyncio.Task | None = None
TRAINING_MODEL_PROFILE_CONFIG = {
    TRAINING_QWEN35_4B_PROFILE: {
        "model_id": "Qwen/Qwen3.5-4B",
        "revision": "main",
        "modelscope_revision": "master",
        "bootstrap_profile": "qlora-1b",
        "default_source": "auto",
    },
    TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE: {
        "model_id": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        "revision": "main",
        "modelscope_revision": "master",
        "bootstrap_profile": "qlora-1b",
        "default_source": "internal",
        "internal_model_ref": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        "deployment_runtime_profile": TRAINING_QWEN36_DEPLOYMENT_RUNTIME_PROFILE,
    }
}
TRAINING_RESULT_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|private[_-]?key|ssh[_-]?key|signing[_-]?key|"
    r"secret[_-]?key|service[_-]?account[_-]?key|session[_-]?id|"
    r"token|secret|password|credential|cookie|authorization)"
)
TRAINING_RESULT_SAFE_TOKEN_KEYS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "generated_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "requested_max_new_tokens",
    "effective_max_new_tokens",
    "max_new_tokens",
    "tokens_per_second",
}


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _safe_number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and number != float("inf") and number != float("-inf") else 0.0


def _safe_int(value: Any) -> int:
    return max(0, int(_safe_number(value)))


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _round_gb(mb: float) -> float:
    return round(mb / 1024, 1)


def _safe_capabilities(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_gpu_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value[:64]:
        if not isinstance(item, dict):
            continue
        items.append({
            "name": str(item.get("name") or "")[:120],
            "vram_total_mb": _safe_int(item.get("vram_total_mb")),
            "vram_used_mb": _safe_int(item.get("vram_used_mb")),
            "vram_free_mb": _safe_int(item.get("vram_free_mb")),
            "gpu_util_pct": max(0, min(_safe_int(item.get("gpu_util_pct")), 100)),
        })
    return items


def _normalize_training_capability(value: Any, *, gpu_count: int) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    tasks = [
        task
        for task in (str(item or "").strip().lower() for item in raw.get("supported_tasks") or [])
        if task in TRAINING_TASKS_ALLOWED
    ][:10]
    if not tasks and gpu_count:
        tasks = ["lora", "qlora", "eval", "merge", "inference"]
    elif not tasks:
        tasks = ["eval"]
    return {
        "gateway": bool(raw.get("gateway") or gpu_count),
        "supported_tasks": tasks,
        "gpu_count": max(_safe_int(raw.get("gpu_count")), gpu_count),
        "worker_count": max(_safe_int(raw.get("worker_count")), gpu_count),
    }


def _normalize_resident_models(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in value[:20]:
        if not isinstance(raw, dict):
            continue
        model = str(raw.get("model") or raw.get("model_name") or raw.get("model_id") or "").strip()[:160]
        deployment_id = str(raw.get("deployment_id") or raw.get("deploymentId") or "").strip()[:80]
        runtime_profile = str(raw.get("runtime_profile") or raw.get("runtimeProfile") or "").strip()[:80]
        status = str(raw.get("status") or ("loaded" if raw.get("loaded") else "unknown")).strip().lower()[:40]
        if not model and not deployment_id:
            continue
        items.append({
            "model": model or deployment_id,
            "deployment_id": deployment_id or None,
            "runtime_profile": runtime_profile or None,
            "status": status or "unknown",
            "loaded": bool(raw.get("loaded") or status in {"active", "loaded", "ready", "running"}),
            "last_heartbeat_at": str(raw.get("last_heartbeat_at") or raw.get("lastHeartbeatAt") or "")[:40] or None,
        })
    return items


def _training_resource_from_instance(instance: OpenClawInstance, *, online: bool) -> dict[str, Any]:
    cap = _safe_capabilities(getattr(instance, "bridge_capabilities_json", None))
    gpu_items = _normalize_gpu_items(cap.get("gpu"))
    total_mb = sum(_safe_number(item.get("vram_total_mb")) for item in gpu_items)
    free_mb = sum(_safe_number(item.get("vram_free_mb")) for item in gpu_items)
    busy_count = 0
    for item in gpu_items:
        total = _safe_number(item.get("vram_total_mb"))
        free = _safe_number(item.get("vram_free_mb"))
        util = _safe_number(item.get("gpu_util_pct"))
        if util >= 10 or (total > 0 and free / total < 0.2):
            busy_count += 1

    training = _normalize_training_capability(cap.get("training"), gpu_count=len(gpu_items))
    training["route_eligible"] = _is_training_agent(instance)
    reserved_for = _training_gateway_reserved_for(instance)
    if reserved_for:
        training["route_eligible"] = False
        training["reserved_for"] = reserved_for
    if not training["route_eligible"]:
        training["gateway"] = False
        training["supported_tasks"] = []
    route_policy = _training_gateway_route_policy(instance)
    gateway_kind = (
        cap.get("gateway_kind")
        or getattr(instance, "bridge_gateway_kind", None)
        or getattr(instance, "agent_type", None)
        or "unknown"
    )
    bridge_platform = (
        cap.get("platform")
        or getattr(instance, "bridge_platform", None)
        or ""
    )
    resident_models = _normalize_resident_models(cap.get("resident_models"))
    if not resident_models:
        training_cap = _safe_dict(cap.get("training"))
        resident_models = _normalize_resident_models(
            training_cap.get("resident_models")
            or _safe_dict(training_cap.get("inference")).get("resident_models")
        )
    return {
        "id": instance.id,
        "name": instance.name or instance.id,
        "department": instance.department,
        "agent_purpose": _agent_purpose(instance),
        "is_platform_default": bool(getattr(instance, "is_platform_default", False)),
        "online": online,
        "gateway_kind": str(gateway_kind or "unknown")[:50],
        "bridge_platform": str(bridge_platform or "")[:40],
        "resident_models": resident_models,
        "gateway_version": str(cap.get("gateway_version") or getattr(instance, "bridge_gateway_version", "") or "")[:80],
        "bridge_version": str(cap.get("bridge_version") or getattr(instance, "bridge_version", "") or "")[:80],
        "runtimes": [str(item)[:50] for item in (cap.get("runtimes") or []) if str(item).strip()][:20],
        "ops": [str(item)[:80] for item in (cap.get("ops") or []) if str(item).strip()][:60],
        "gpu": gpu_items,
        "training": training,
        "route_policy": route_policy,
        "resource_role": route_policy["role"],
        "reserved_for": reserved_for,
        "deployment_target_default": route_policy["deployment_target_default"],
        "gpu_count": len(gpu_items),
        "idle_gpu_count": max(len(gpu_items) - busy_count, 0),
        "busy_gpu_count": busy_count,
        "vram_total_gb": _round_gb(total_mb),
        "vram_free_gb": _round_gb(free_mb),
    }


def _can_view_all_training_resources(user: User) -> bool:
    return role_matches_any(user, ("admin", "system_admin")) or bool(getattr(user, "can_view_all", False))


def _can_create_training_job(user: User) -> bool:
    return str(getattr(user, "role", "") or "") in TRAINING_JOB_CREATE_ROLES or bool(getattr(user, "can_view_all", False))


def _can_approve_training_job(user: User) -> bool:
    return str(getattr(user, "role", "") or "") in TRAINING_JOB_APPROVE_ROLES or bool(getattr(user, "can_view_all", False))


def _user_department(user: User) -> str:
    return str(getattr(user, "department", "") or "").strip()


def _can_access_training_department(user: User, department: str) -> bool:
    if _can_view_all_training_resources(user):
        return True
    return bool(department and department == _user_department(user))


def _require_training_department_access(user: User, department: str) -> None:
    if not _can_access_training_department(user, department):
        raise AppError("FORBIDDEN", 403, {"detail": "no access to training department"})


def _normalize_job_type(value: Any) -> str:
    job_type = str(value or "lora").strip().lower()
    if job_type not in TRAINING_JOB_TYPES:
        raise AppError("PARAM_INVALID", 422, {"detail": "unsupported training job type"})
    return job_type


def _training_gateway_task_for_job_type(job_type: str) -> str:
    return TRAINING_JOB_GATEWAY_TASK_ALIASES.get(str(job_type or "").strip().lower(), str(job_type or "").strip().lower())


def _normalize_job_status(value: str | None) -> str | None:
    if not value:
        return None
    status = str(value).strip().lower()
    if status not in TRAINING_JOB_STATUSES:
        raise AppError("PARAM_INVALID", 422, {"detail": "unsupported training job status"})
    return status


def _normalize_deployment_status(value: str | None) -> str | None:
    if not value:
        return None
    status = str(value).strip().lower()
    if status not in TRAINING_DEPLOYMENT_STATUSES:
        raise AppError("PARAM_INVALID", 422, {"detail": "unsupported training deployment status"})
    return status


def _clean_text(value: Any, *, limit: int, required: bool = False) -> str | None:
    text = str(value or "").strip()
    if required and not text:
        raise AppError("PARAM_INVALID", 422, {"detail": "required field missing"})
    if not text:
        return None
    return text[:limit]


def _truthy_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _validate_spec_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AppError("PARAM_INVALID", 422, {"detail": "training spec must be an object"})
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_TRAINING_SPEC_BYTES:
        raise AppError("PARAM_INVALID", 422, {"detail": "training spec too large"})
    return value


def _decision_has_feedback(log: DecisionLog) -> bool:
    return bool(
        log.user_action
        or log.user_feedback
        or log.rating is not None
        or log.feedback_type
        or log.reject_reason
        or log.business_impact
    )


def _decision_has_action_outcome(log: DecisionLog) -> bool:
    output = log.output_result if isinstance(log.output_result, dict) else {}
    return bool(
        isinstance(log.suggested_action, dict)
        or output.get("todos")
        or output.get("actions")
        or output.get("action")
    )


def _build_training_dataset_readiness(skill: Skill, logs: list[DecisionLog]) -> dict[str, Any]:
    now = now_bjt()
    recent_cutoff = now - timedelta(days=7)
    recent_logs = [log for log in logs if log.created_at and log.created_at >= recent_cutoff]
    recent_failed = sum(1 for log in recent_logs if str(log.user_action or "").lower() in {"rejected", "failed"})
    recent_failure_rate = (recent_failed / len(recent_logs)) if recent_logs else 0.0
    sample_counts = {
        "sft_samples": sum(1 for log in logs if isinstance(log.output_result, dict) or isinstance(log.input_snapshot, dict)),
        "preference_samples": sum(1 for log in logs if _decision_has_feedback(log)),
        "action_outcome_samples": sum(1 for log in logs if _decision_has_action_outcome(log)),
        "eval_samples": sum(1 for log in logs if _decision_has_feedback(log)),
        "recent_total": len(recent_logs),
        "recent_failed": recent_failed,
    }
    checks = [
        {
            "key": "sft_samples",
            "label": "SFT 样本",
            "actual": sample_counts["sft_samples"],
            "threshold": TRAINING_CANDIDATE_THRESHOLDS["sft_samples"],
            "passed": sample_counts["sft_samples"] >= TRAINING_CANDIDATE_THRESHOLDS["sft_samples"],
        },
        {
            "key": "preference_samples",
            "label": "偏好样本",
            "actual": sample_counts["preference_samples"],
            "threshold": TRAINING_CANDIDATE_THRESHOLDS["preference_samples"],
            "passed": sample_counts["preference_samples"] >= TRAINING_CANDIDATE_THRESHOLDS["preference_samples"],
        },
        {
            "key": "action_outcome_samples",
            "label": "动作结果样本",
            "actual": sample_counts["action_outcome_samples"],
            "threshold": TRAINING_CANDIDATE_THRESHOLDS["action_outcome_samples"],
            "passed": sample_counts["action_outcome_samples"] >= TRAINING_CANDIDATE_THRESHOLDS["action_outcome_samples"],
        },
        {
            "key": "eval_samples",
            "label": "固定评估集",
            "actual": sample_counts["eval_samples"],
            "threshold": TRAINING_CANDIDATE_THRESHOLDS["eval_samples"],
            "passed": sample_counts["eval_samples"] >= TRAINING_CANDIDATE_THRESHOLDS["eval_samples"],
        },
        {
            "key": "recent_failure_rate",
            "label": "最近 7 天失败率",
            "actual": round(recent_failure_rate, 4),
            "threshold": TRAINING_CANDIDATE_THRESHOLDS["max_recent_failure_rate"],
            "passed": recent_failure_rate <= TRAINING_CANDIDATE_THRESHOLDS["max_recent_failure_rate"],
        },
    ]
    passed = all(bool(item["passed"]) for item in checks)
    return {
        "skill_id": skill.id,
        "skill_name": skill.name,
        "department": skill.department,
        "status": skill.status,
        "current_version": skill.current_version,
        "git_commit": skill.git_commit,
        "passed": passed,
        "sample_counts": sample_counts,
        "checks": checks,
        "thresholds": TRAINING_CANDIDATE_THRESHOLDS,
        "dataset_ref": f"decision-log://{skill.id}/action-outcome/latest",
        "last_log_at": isoformat_bjt(logs[0].created_at) if logs else None,
        "recent_failure_rate": round(recent_failure_rate, 4),
    }


async def get_training_candidate_readiness(
    db: AsyncSession,
    user: User,
    skill_id: str,
) -> dict[str, Any]:
    skill = await require_skill_access(db, skill_id, user, "read")
    logs = (
        await db.execute(
            select(DecisionLog)
            .where(DecisionLog.skill_id == skill_id)
            .where(DecisionLog.is_sandbox == False)  # noqa: E712
            .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
            .limit(TRAINING_CANDIDATE_MAX_LOGS)
        )
    ).scalars().all()

    return _build_training_dataset_readiness(skill, list(logs))


def _training_job_id() -> str:
    return f"train_{uuid4().hex[:24]}"


def _training_deployment_id() -> str:
    return f"deploy_{uuid4().hex[:24]}"


def issue_training_callback_token(job: TrainingJob) -> str:
    now = int(time())
    claims = {
        "aud": TRAINING_CALLBACK_TOKEN_AUDIENCE,
        "job_id": job.id,
        "target_gateway_id": job.target_gateway_id,
        "department": job.department,
        "iat": now,
        "exp": now + TRAINING_CALLBACK_TOKEN_TTL_SECONDS,
    }
    payload = _b64url_encode(
        json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(settings.SECRET_KEY.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return f"sftrain.{payload}.{_b64url_encode(signature)}"


def verify_training_callback_token(token: str | None, *, job: TrainingJob) -> dict[str, Any]:
    if not token:
        raise AppError("RUN_TOKEN_INVALID", 401, {"reason": "missing_training_callback_token"})
    try:
        prefix, payload, signature = str(token).split(".", 2)
        if prefix != "sftrain":
            raise ValueError("bad prefix")
        expected = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        actual = _b64url_decode(signature)
        if not hmac.compare_digest(actual, expected):
            raise ValueError("signature mismatch")
        claims = json.loads(_b64url_decode(payload).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise AppError("RUN_TOKEN_INVALID", 401, {"reason": "invalid_training_callback_token"}) from exc
    if claims.get("aud") != TRAINING_CALLBACK_TOKEN_AUDIENCE:
        raise AppError("RUN_TOKEN_INVALID", 401, {"reason": "aud_mismatch"})
    if int(claims.get("exp") or 0) < int(time()):
        raise AppError("RUN_TOKEN_INVALID", 401, {"reason": "expired"})
    checks = {
        "job_id": job.id,
        "target_gateway_id": job.target_gateway_id,
        "department": job.department,
    }
    for key, expected_value in checks.items():
        if expected_value and str(claims.get(key) or "") != str(expected_value):
            raise AppError("RUN_TOKEN_INVALID", 401, {"reason": f"{key}_mismatch"})
    return claims


def _build_gateway_payload(job: TrainingJob) -> dict[str, Any]:
    spec = dict(job.spec_json or {})
    job_type = str(job.job_type or "")
    gateway_task = _training_gateway_task_for_job_type(job_type)
    if gateway_task and gateway_task != job_type:
        spec["gateway_task"] = gateway_task
        spec["requested_job_type"] = job_type
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "title": job.title,
        "department": job.department,
        "target_skill_id": job.target_skill_id,
        "target_gateway_id": job.target_gateway_id,
        "dataset_ref": job.dataset_ref,
        "training_strategy": job.training_strategy,
        "objective": job.objective,
        "risk_level": job.risk_level,
        "spec": spec,
        "control": {
            "result_mode": "bridge_op_callback",
            "callback_path": f"/api/training/jobs/{job.id}/gateway-result",
            "callback_token": issue_training_callback_token(job),
        },
    }


def _gateway_payload_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _compact_training_dataset_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= TRAINING_GATEWAY_DATASET_COMPACT_STRING_CHARS:
            return value
        return value[:TRAINING_GATEWAY_DATASET_COMPACT_STRING_CHARS] + "...[truncated]"
    if depth >= TRAINING_GATEWAY_DATASET_COMPACT_DEPTH:
        return _compact_training_dataset_value(str(value), depth=depth)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        total = len(value)
        for index, (key, item) in enumerate(value.items()):
            if index >= TRAINING_GATEWAY_DATASET_COMPACT_DICT_ITEMS:
                out["__truncated_keys__"] = total - index
                break
            out[str(key)[:120]] = _compact_training_dataset_value(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        total = len(value)
        out = [
            _compact_training_dataset_value(item, depth=depth + 1)
            for item in value[:TRAINING_GATEWAY_DATASET_COMPACT_LIST_ITEMS]
        ]
        if total > TRAINING_GATEWAY_DATASET_COMPACT_LIST_ITEMS:
            out.append({"__truncated_items__": total - TRAINING_GATEWAY_DATASET_COMPACT_LIST_ITEMS})
        return out
    return _compact_training_dataset_value(str(value), depth=depth)


def _clip_training_dataset_text(value: Any, *, limit: int = TRAINING_GATEWAY_DATASET_FIELD_CHARS) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(
                _compact_training_dataset_value(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except Exception:
            text = str(value or "")
    return redact_secret_text(text, limit=limit)


def _training_dataset_sha256(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _gateway_dataset_artifact_ids(
    job: TrainingJob,
    *,
    max_ids: int | None = TRAINING_GATEWAY_DATASET_MAX_SAMPLE_IDS,
) -> list[str]:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    raw_ids: list[Any] = []
    dataset = spec.get("dataset") if isinstance(spec.get("dataset"), dict) else {}
    lineage = spec.get("lineage") if isinstance(spec.get("lineage"), dict) else {}
    for raw in (dataset.get("learning_artifact_ids"), lineage.get("learning_artifact_ids")):
        if isinstance(raw, list):
            raw_ids.extend(raw)
    ids: list[str] = []
    for raw_id in raw_ids:
        item = str(raw_id or "").strip()
        if item and item not in ids:
            ids.append(item[:50])
        if max_ids is not None and len(ids) >= max(1, max_ids):
            break
    return ids


async def _gateway_dataset_artifact_ids_from_selector(
    db: AsyncSession,
    job: TrainingJob,
    *,
    external_storage: bool = False,
    max_ids: int | None = TRAINING_GATEWAY_DATASET_MAX_SAMPLE_IDS,
) -> list[str]:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    dataset = spec.get("dataset") if isinstance(spec.get("dataset"), dict) else {}
    selector = dataset.get("selector") if isinstance(dataset.get("selector"), dict) else {}
    if not selector:
        return []
    if str(selector.get("type") or "") != "learning_artifacts":
        return []
    from app.learning.models import LearningArtifact

    stmt = (
        select(LearningArtifact.id)
        .where(LearningArtifact.artifact_kind.in_(["training_sample", "eval_case"]))
        .where(LearningArtifact.status == "materialized")
        .where(LearningArtifact.sink_type == "training_sample")
        .order_by(LearningArtifact.created_at.asc(), LearningArtifact.id.asc())
    )
    skill_ids = selector.get("skill_ids")
    if isinstance(skill_ids, list):
        cleaned_skill_ids = [str(item or "").strip() for item in skill_ids if str(item or "").strip()]
        if cleaned_skill_ids:
            stmt = stmt.where(LearningArtifact.skill_id.in_(cleaned_skill_ids))
    target_skill_id = str(selector.get("target_skill_id") or selector.get("source_id") or "").strip()
    if target_skill_id:
        if target_skill_id.startswith("project:"):
            project_id = target_skill_id.split(":", 1)[1]
            stmt = stmt.where(
                or_(
                    LearningArtifact.skill_id == target_skill_id,
                    and_(
                        LearningArtifact.target_type == "project",
                        LearningArtifact.target_id.in_([target_skill_id, project_id]),
                    ),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    LearningArtifact.skill_id == target_skill_id,
                    and_(LearningArtifact.target_type == "skill", LearningArtifact.target_id == target_skill_id),
                )
            )
    department = str(selector.get("department") or "").strip()
    if department:
        stmt = stmt.where(LearningArtifact.department == department)
    start = _parse_optional_datetime(selector.get("created_at_start"))
    end = _parse_optional_datetime(selector.get("created_at_end"))
    if start is not None:
        stmt = stmt.where(LearningArtifact.created_at >= start)
    if end is not None:
        stmt = stmt.where(LearningArtifact.created_at < end)
    max_content_bytes = selector.get("inline_max_content_bytes")
    if max_content_bytes is None:
        max_content_bytes = TRAINING_GATEWAY_DATASET_INLINE_MAX_CONTENT_BYTES
    try:
        max_content_bytes = int(max_content_bytes)
    except (TypeError, ValueError):
        max_content_bytes = TRAINING_GATEWAY_DATASET_INLINE_MAX_CONTENT_BYTES
    if max_content_bytes > 0:
        stmt = stmt.where(func.pg_column_size(LearningArtifact.content_json) <= max_content_bytes)
    if external_storage:
        raw_limit = selector.get("limit") or selector.get("inline_limit")
    else:
        raw_limit = selector.get("inline_limit") or selector.get("limit")
    try:
        limit = int(raw_limit or TRAINING_GATEWAY_DATASET_MAX_SAMPLE_IDS)
    except (TypeError, ValueError):
        limit = TRAINING_GATEWAY_DATASET_MAX_SAMPLE_IDS
    limit = max(1, limit)
    if max_ids is not None:
        limit = min(limit, max(1, max_ids))

    raw_eval_limit = None if external_storage else selector.get("inline_eval_limit")
    if raw_eval_limit is None:
        rows = (await db.execute(stmt.limit(limit))).scalars().all()
    else:
        try:
            eval_limit = int(raw_eval_limit)
        except (TypeError, ValueError):
            eval_limit = TRAINING_GATEWAY_DATASET_DEFAULT_EVAL_INLINE_SAMPLES
        eval_limit = max(0, min(eval_limit, limit))
        train_rows = []
        eval_rows = []
        if eval_limit:
            eval_rows = (
                await db.execute(
                    stmt.where(LearningArtifact.artifact_kind == "eval_case").limit(eval_limit)
                )
            ).scalars().all()
        train_limit = max(0, limit - len(eval_rows))
        if train_limit:
            train_rows = (
                await db.execute(
                    stmt.where(LearningArtifact.artifact_kind == "training_sample").limit(train_limit)
                )
            ).scalars().all()
        rows = list(train_rows) + list(eval_rows)
    ids: list[str] = []
    for raw_id in rows:
        item = str(raw_id or "").strip()
        if item and item not in ids:
            ids.append(item[:50])
    return ids


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _sft_sample_from_learning_artifact(row: Any) -> dict[str, Any] | None:
    content = row.content_json if isinstance(getattr(row, "content_json", None), dict) else {}
    labels = row.labels_json if isinstance(getattr(row, "labels_json", None), list) else []
    kind = str(getattr(row, "artifact_kind", "") or "")
    split = "eval" if kind == "eval_case" else "train"
    source = content.get("source") if isinstance(content.get("source"), dict) else {}
    source_type = str(content.get("source_type") or source.get("source_type") or kind or "learning_artifact")[:80]
    source_channel = str(content.get("source_channel") or source.get("source_channel") or "")[:80]
    decision = content.get("decision") if isinstance(content.get("decision"), dict) else {}
    model_context = content.get("model_context") if isinstance(content.get("model_context"), dict) else {}
    formed_data = content.get("formed_data") if isinstance(content.get("formed_data"), (dict, list)) else {}
    runtime_agent = {}
    if isinstance(formed_data, dict) and isinstance(formed_data.get("runtime_agent"), dict):
        runtime_agent = formed_data.get("runtime_agent") or {}
    elif isinstance(content.get("runtime_agent"), dict):
        runtime_agent = content.get("runtime_agent") or {}

    if kind == "eval_case":
        instruction = (
            "你是 SkillForge 的业务复盘评估模型。请根据输入和真实反馈，输出可执行的评估结论，"
            "指出应该避免或修正的判断点，并保留管理者和消费者视角。"
        )
        expected = {
            "expected_signal": content.get("expected_signal"),
            "rejection_reason": content.get("rejection_reason") or content.get("reject_reason"),
            "assertions": content.get("assertions"),
            "summary": getattr(row, "summary", None),
        }
        sample_input = {
            "artifact_kind": kind,
            "source_type": source_type,
            "source_channel": source_channel or None,
            "input": content.get("input") or content.get("request") or content.get("actual_output") or {},
            "actual_output": content.get("actual_output") or content.get("output") or {},
            "feedback": {
                "user_action": content.get("user_action"),
                "rating": content.get("rating"),
                "reject_reason": content.get("reject_reason") or content.get("rejection_reason"),
            },
            "formed_data": formed_data or None,
            "decision": decision,
            "model_context": model_context or None,
            "labels": labels,
        }
        sample_output = expected
    else:
        instruction = (
            "你是 SkillForge 的业务分析微调样本生成器。请学习输入、原始输出和人工反馈之间的关系，"
            "输出更有依据、更可执行的分析结论，重点解释同主题低消耗与高消耗差异。"
        )
        feedback = content.get("feedback") if isinstance(content.get("feedback"), dict) else {}
        structured_feedback = (
            content.get("human_feedback_input")
            if isinstance(content.get("human_feedback_input"), dict)
            else feedback.get("structured") if isinstance(feedback.get("structured"), dict) else {}
        )
        sample_input = {
            "artifact_kind": kind,
            "source_type": source_type,
            "source_channel": source_channel or None,
            "input": content.get("input") or content.get("request") or {},
            "previous_output": content.get("output") or content.get("actual_output") or {},
            "formed_data": formed_data or None,
            "suggested_action": content.get("suggested_action") or {},
            "feedback": {
                "status": feedback.get("status") or content.get("user_action"),
                "reason": (
                    feedback.get("reason")
                    or content.get("user_feedback")
                    or decision.get("user_feedback")
                    or content.get("reject_reason")
                    or decision.get("reject_reason")
                ),
                "channel": feedback.get("source_channel") or feedback.get("channel") or source_channel or None,
                "rating": content.get("rating") if content.get("rating") is not None else decision.get("rating"),
                "feedback_type": content.get("feedback_type") or decision.get("feedback_type"),
                "reject_reason": content.get("reject_reason") or decision.get("reject_reason"),
                "business_impact": content.get("business_impact") or decision.get("business_impact") or {},
                "structured": structured_feedback or None,
            },
            "decision": decision,
            "model_context": model_context or None,
            "labels": labels,
        }
        sample_output = (
            content.get("expected_output")
            or content.get("output")
            or {
                "summary": getattr(row, "summary", None),
                "recommended_action": content.get("suggested_action") or content.get("feedback") or {},
            }
        )

    rendered_input = _clip_training_dataset_text(sample_input)
    rendered_output = _clip_training_dataset_text(sample_output)
    if not rendered_input.strip() or not rendered_output.strip():
        return None
    artifact_id = str(getattr(row, "id", "") or "")[:80]
    input_sha256 = _training_dataset_sha256(rendered_input)
    output_sha256 = _training_dataset_sha256(rendered_output)
    metadata = {
        "learning_artifact_id": artifact_id,
        "event_id": str(getattr(row, "event_id", "") or "")[:80] or None,
        "skill_id": str(getattr(row, "skill_id", "") or "")[:80] or None,
        "run_id": str(getattr(row, "run_id", "") or "")[:80] or None,
        "source_type": source_type or None,
        "source_channel": source_channel or None,
        "artifact_kind": kind[:50],
        "quality_score": round(_safe_number(getattr(row, "quality_score", None)), 4),
        "confidence": round(_safe_number(getattr(row, "confidence", None)), 4),
        "has_input": sample_input.get("input") not in (None, "", [], {}),
        "has_output": sample_output not in (None, "", [], {}),
        "has_formed_data": formed_data not in (None, "", [], {}),
        "formed_data_sha256": (
            _training_dataset_sha256(_clip_training_dataset_text(formed_data, limit=TRAINING_GATEWAY_DATASET_FIELD_CHARS))
            if formed_data not in (None, "", [], {})
            else None
        ),
        "has_runtime_agent": bool(runtime_agent),
        "runtime_agent_id": str(runtime_agent.get("agent_id") or "")[:80] or None,
        "runtime_agent_contract_complete": (
            runtime_agent.get("contract", {}).get("complete")
            if isinstance(runtime_agent.get("contract"), dict)
            else None
        ),
        "has_human_feedback": bool(
            sample_input.get("feedback")
            and any(value not in (None, "", [], {}) for value in sample_input["feedback"].values())
        ),
        "feedback_source": (
            sample_input.get("feedback", {}).get("channel")
            if isinstance(sample_input.get("feedback"), dict)
            else None
        ),
        "has_model_context": bool(model_context),
        "model_deployment_id": str(model_context.get("model_deployment_id") or "")[:80] or None,
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
        "sample_sha256": _training_dataset_sha256(
            json.dumps(
                {
                    "instruction": instruction,
                    "input_sha256": input_sha256,
                    "output_sha256": output_sha256,
                    "artifact_id": artifact_id,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    }
    return {
        "id": artifact_id,
        "split": split,
        "artifact_kind": kind[:50],
        "quality_score": round(_safe_number(getattr(row, "quality_score", None)), 4),
        "instruction": instruction,
        "input": rendered_input,
        "output": rendered_output,
        "metadata": {key: value for key, value in metadata.items() if value not in (None, "", [], {})},
    }


def _compact_gateway_spec(spec: dict[str, Any]) -> dict[str, Any]:
    compact = _sanitize_training_result_value(spec or {})
    if not isinstance(compact, dict):
        return {}
    for key in ("dataset", "lineage"):
        section = compact.get(key)
        if not isinstance(section, dict):
            continue
        ids = section.get("learning_artifact_ids")
        if isinstance(ids, list):
            section["learning_artifact_ids_count"] = len(ids)
            section["learning_artifact_ids"] = ids[:10]
            section["learning_artifact_ids_truncated"] = len(ids) > 10
    return compact


def _gateway_dataset_manifest_hash(job: TrainingJob, dataset_ref: str, samples: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "job_id": job.id,
                "dataset_ref": dataset_ref,
                "sample_fingerprints": [
                    {
                        "id": item.get("id"),
                        "split": item.get("split"),
                        "input_sha256": ((item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}).get("input_sha256"),
                        "output_sha256": ((item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}).get("output_sha256"),
                        "sample_sha256": ((item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}).get("sample_sha256"),
                    }
                    for item in samples
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _gateway_dataset_package_for_samples(
    job: TrainingJob,
    *,
    dataset_ref: str,
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    dataset = spec.get("dataset") if isinstance(spec.get("dataset"), dict) else {}
    selector = dataset.get("selector") if isinstance(dataset.get("selector"), dict) else {}
    artifact_ids = [str(item.get("id") or "") for item in samples if str(item.get("id") or "").strip()]
    manifest_hash = _gateway_dataset_manifest_hash(job, dataset_ref, samples)
    return {
        "format": "skillforge_sft_jsonl_v1",
        "source": "learning_artifacts",
        "packing_version": TRAINING_GATEWAY_DATASET_PACKING_VERSION,
        "dataset_ref": dataset_ref[:500],
        "target_skill_id": str(job.target_skill_id or "")[:80],
        "manifest_hash": manifest_hash,
        "sample_count": len(samples),
        "train_count": sum(1 for item in samples if item.get("split") == "train"),
        "eval_count": sum(1 for item in samples if item.get("split") == "eval"),
        "manifest_sample_count": dataset.get("sample_total"),
        "manifest_train_count": dataset.get("train_count"),
        "manifest_eval_count": dataset.get("eval_count"),
        "inline_limit": selector.get("inline_limit"),
        "inline_eval_limit": selector.get("inline_eval_limit"),
        "inline_max_content_bytes": selector.get("inline_max_content_bytes"),
        "input_contract": {
            "schema": "skillforge_sft_jsonl_v1",
            "required_fields": ["instruction", "input", "output"],
            "split_field": "split",
            "lineage_field": "metadata",
            "sample_id_field": "id",
        },
        "output_contract": {
            "result_mode": "bridge_op_callback",
            "callback_required": True,
            "artifact_types": ["adapter"],
            "metrics": ["train_loss", "eval_loss", "win_rate"],
            "requires_artifact_sha256": True,
        },
        "lineage": {
            "source": "learning_artifacts",
            "training_job_id": job.id,
            "target_skill_id": job.target_skill_id,
            "learning_artifact_ids": artifact_ids,
            "manifest_hash": manifest_hash,
            "packing_version": TRAINING_GATEWAY_DATASET_PACKING_VERSION,
        },
        "raw_payload_returned": False,
        "samples": samples,
    }


def _dataset_samples_with_eval_reserve(samples: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    clean_limit = max(0, int(limit or 0))
    if clean_limit <= 0:
        return []
    if len(samples) <= clean_limit:
        return list(samples)

    eval_samples = [item for item in samples if item.get("split") == "eval"]
    if not eval_samples:
        return samples[:clean_limit]

    reserve = min(len(eval_samples), TRAINING_GATEWAY_DATASET_DEFAULT_EVAL_INLINE_SAMPLES, clean_limit)
    train_limit = clean_limit - reserve
    selected = [item for item in samples if item.get("split") != "eval"][:train_limit]
    selected_ids = {id(item) for item in selected}
    for item in eval_samples:
        if len(selected) >= clean_limit:
            break
        if id(item) not in selected_ids:
            selected.append(item)
            selected_ids.add(id(item))
    if len(selected) < clean_limit:
        for item in samples:
            if id(item) in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(id(item))
            if len(selected) >= clean_limit:
                break
    return selected[:clean_limit]


async def _build_learning_gateway_dataset_package(
    db: AsyncSession,
    job: TrainingJob,
    *,
    external_storage: bool = False,
) -> dict[str, Any] | None:
    dataset_ref = str(job.dataset_ref or "").strip()
    if not dataset_ref.startswith("learning-artifacts://"):
        return None
    max_ids = None if external_storage else TRAINING_GATEWAY_DATASET_MAX_SAMPLE_IDS
    artifact_ids = _gateway_dataset_artifact_ids(job, max_ids=max_ids)
    if not artifact_ids:
        artifact_ids = await _gateway_dataset_artifact_ids_from_selector(
            db,
            job,
            external_storage=external_storage,
            max_ids=max_ids,
        )
    if not artifact_ids:
        return None
    from app.learning.models import LearningArtifact

    rows = []
    for start in range(0, len(artifact_ids), 5000):
        chunk_ids = artifact_ids[start:start + 5000]
        if not chunk_ids:
            continue
        rows.extend(
            (
                await db.execute(
                    select(LearningArtifact)
                    .where(LearningArtifact.id.in_(chunk_ids))
                    .where(LearningArtifact.artifact_kind.in_(["training_sample", "eval_case"]))
                    .where(LearningArtifact.status == "materialized")
                    .where(LearningArtifact.sink_type == "training_sample")
                )
            ).scalars().all()
        )
    row_by_id = {row.id: row for row in rows}
    ordered = [row_by_id[item] for item in artifact_ids if item in row_by_id]
    samples: list[dict[str, Any]] = []
    for index, row in enumerate(ordered, start=1):
        sample = _sft_sample_from_learning_artifact(row)
        if sample is not None:
            samples.append(sample)
        if max_ids is not None and len(samples) >= max(1, max_ids):
            break
        if index % 1000 == 0:
            await asyncio.sleep(0)
    if len(samples) < TRAINING_GATEWAY_DATASET_MIN_INLINE_SAMPLES:
        return None

    package = await asyncio.to_thread(
        _gateway_dataset_package_for_samples,
        job,
        dataset_ref=dataset_ref,
        samples=samples,
    )
    if external_storage:
        return package
    if _gateway_payload_size(package) <= TRAINING_GATEWAY_DATASET_PACKAGE_MAX_BYTES:
        return package

    best: dict[str, Any] | None = None
    low = TRAINING_GATEWAY_DATASET_MIN_INLINE_SAMPLES
    high = len(samples) - 1
    while low <= high:
        mid = (low + high) // 2
        package = await asyncio.to_thread(
            _gateway_dataset_package_for_samples,
            job,
            dataset_ref=dataset_ref,
            samples=_dataset_samples_with_eval_reserve(samples, mid),
        )
        if _gateway_payload_size(package) <= TRAINING_GATEWAY_DATASET_PACKAGE_MAX_BYTES:
            best = package
            low = mid + 1
        else:
            high = mid - 1
    return best


def _training_dataset_bridge_config(job: TrainingJob) -> dict[str, Any] | None:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    dataset = spec.get("dataset") if isinstance(spec.get("dataset"), dict) else {}
    governance = spec.get("governance") if isinstance(spec.get("governance"), dict) else {}
    raw = None
    for item in (
        dataset.get("bridge_dataset"),
        dataset.get("bridge_transfer"),
        dataset.get("external_bridge_transfer"),
        governance.get("bridge_dataset"),
    ):
        if isinstance(item, dict):
            raw = item
            break
    enabled = bool(raw and raw.get("enabled") is not False)
    if not enabled:
        return None
    target_gateway_id = _clean_text(
        job.target_gateway_id
        or raw.get("target_gateway_id")
        or raw.get("training_gateway_id")
        or next(iter(TRAINING_PRIMARY_GATEWAY_IDS), ""),
        limit=50,
    )
    source_gateway_id = _clean_text(
        raw.get("source_gateway_id") or TRAINING_DEFAULT_DATASET_SOURCE_GATEWAY_ID,
        limit=50,
    )
    if not source_gateway_id or not target_gateway_id:
        return None
    timeout_seconds = _safe_int(raw.get("timeout_seconds") or raw.get("transfer_timeout_seconds") or 21600)
    ttl_seconds = _safe_int(raw.get("ttl_seconds") or 3600)
    return {
        "enabled": True,
        "source_gateway_id": source_gateway_id,
        "target_gateway_id": target_gateway_id,
        "timeout_seconds": max(60, min(timeout_seconds or 21600, 86400)),
        "ttl_seconds": max(60, min(ttl_seconds or 3600, 24 * 60 * 60)),
        "force": raw.get("force") is not False,
        "hash_files": raw.get("hash_files") is not False,
        "required": raw.get("required") is True,
    }


def _training_dataset_id(job: TrainingJob, package: dict[str, Any]) -> str:
    manifest_hash = str(package.get("manifest_hash") or "").strip().lower()
    date_segment = now_bjt().strftime("%Y%m%d")
    skill_segment = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(job.target_skill_id or "platform").strip())[:40].strip("-._:")
    raw = f"sfds-{skill_segment or 'platform'}-{date_segment}-{job.id}-{manifest_hash[:12]}"
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-._:")[:120] or f"sfds-{job.id}"


def _training_dataset_package_metadata(package: dict[str, Any]) -> dict[str, Any]:
    lineage = package.get("lineage") if isinstance(package.get("lineage"), dict) else {}
    sample_ids = lineage.get("learning_artifact_ids") if isinstance(lineage.get("learning_artifact_ids"), list) else []
    return {
        "format": str(package.get("format") or "")[:80],
        "source": str(package.get("source") or "")[:80],
        "dataset_ref": str(package.get("dataset_ref") or "")[:500],
        "target_skill_id": str(package.get("target_skill_id") or "")[:80],
        "manifest_hash": str(package.get("manifest_hash") or "")[:80],
        "sample_count": _safe_int(package.get("sample_count")),
        "train_count": _safe_int(package.get("train_count")),
        "eval_count": _safe_int(package.get("eval_count")),
        "learning_artifact_ids_count": len(sample_ids),
        "packing_version": str(package.get("packing_version") or "")[:80],
    }


async def _write_training_dataset_package_to_source(
    source_client: AIClawClient,
    *,
    dataset_id: str,
    package: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    async def _dataset_bridge_call(label: str, factory: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt, delay in enumerate((0, 1, 2, 5, 10, 20, 30), start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await factory()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _training_model_transfer_is_transient_error(exc):
                    raise
                logger.warning(
                    "[training-dataset] source {} transient error attempt={} retry_in={}s error={}",
                    label,
                    attempt,
                    delay,
                    _extract_error_message(exc),
                )
        if last_exc is not None:
            raise last_exc
        raise AppError("TRAINING_DATASET_SOURCE_WRITE_FAILED", 502, {"detail": f"{label} failed"})

    raw_text = await asyncio.to_thread(
        json.dumps,
        package,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    raw = raw_text.encode("utf-8")
    dataset_sha = hashlib.sha256(raw).hexdigest()
    offset = 0
    while offset < len(raw) or (len(raw) == 0 and offset == 0):
        chunk = raw[offset: offset + TRAINING_DATASET_TRANSFER_CHUNK_BYTES]
        await _dataset_bridge_call(
            f"write_chunk:{offset}",
            lambda chunk=chunk, offset=offset: source_client.write_training_dataset_chunk(
                {
                    "dataset_id": dataset_id,
                    "offset": offset,
                    "content_base64": base64.b64encode(chunk).decode("ascii"),
                },
                timeout=min(max(timeout_seconds, 120), 600),
            ),
        )
        if not chunk:
            break
        offset += len(chunk)
    committed = await _dataset_bridge_call(
        "commit",
        lambda: source_client.commit_training_dataset(
            {
                "dataset_id": dataset_id,
                "sha256": dataset_sha,
                "metadata": _training_dataset_package_metadata(package),
                "force": True,
            },
            timeout=timeout_seconds,
        ),
    )
    if str(committed.get("status") or "").lower() not in {"succeeded", "ready", "ok"}:
        raise AppError(
            "TRAINING_DATASET_SOURCE_COMMIT_FAILED",
            502,
            {"detail": committed.get("error") or "source dataset commit failed"},
        )
    return {
        **committed,
        "dataset_id": dataset_id,
        "sha256": committed.get("sha256") or dataset_sha,
        "size_bytes": committed.get("size_bytes") or len(raw),
    }


async def _relay_training_dataset_via_platform(
    *,
    source_client: AIClawClient,
    target_client: AIClawClient,
    dataset_id: str,
    source_gateway_id: str,
    source_path: str,
    timeout_seconds: int,
    hash_files: bool,
    target_supports_file_status: bool,
) -> dict[str, Any]:
    manifest = await source_client.export_training_dataset_manifest(
        {
            "dataset_id": dataset_id,
            "source_path": source_path,
            "hash_files": hash_files,
        },
        timeout=timeout_seconds,
    )
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    manifest_sha = str(manifest.get("manifest_sha256") or "").strip().lower()
    if not files or not manifest_sha:
        raise AppError(
            "TRAINING_DATASET_TRANSFER_RELAY_FAILED",
            502,
            {"detail": "source dataset relay manifest is empty"},
        )
    total_bytes = _safe_int(manifest.get("total_size_bytes"))
    transferred_bytes = 0
    imported_files = 0
    for entry in files:
        if not isinstance(entry, dict):
            continue
        relpath = str(entry.get("path") or "").strip()
        size = _safe_int(entry.get("size_bytes"))
        if not relpath:
            continue
        offset = 0
        if target_supports_file_status:
            status = await target_client.get_training_dataset_relay_file_status(
                {
                    "dataset_id": dataset_id,
                    "manifest_sha256": manifest_sha,
                    "file_path": relpath,
                    "hash_file": bool(entry.get("sha256")),
                },
                timeout=30,
            )
            expected_sha = str(entry.get("sha256") or "").strip().lower()
            actual_sha = str(_safe_dict(status).get("sha256") or "").strip().lower()
            existing_size = _safe_int(_safe_dict(status).get("size_bytes"))
            if status.get("exists") is True and existing_size == size and (not expected_sha or actual_sha == expected_sha):
                transferred_bytes += size
                imported_files += 1
                continue
            if 0 < existing_size < size:
                offset = existing_size
                transferred_bytes += existing_size
        while offset < size or (size == 0 and offset == 0):
            chunk = await source_client.export_training_dataset_file_chunk(
                {
                    "dataset_id": dataset_id,
                    "source_path": source_path,
                    "file_path": relpath,
                    "offset": offset,
                    "max_bytes": TRAINING_DATASET_TRANSFER_CHUNK_BYTES,
                },
                timeout=600,
            )
            content_b64 = str(chunk.get("content_base64") or "")
            chunk_size = _safe_int(chunk.get("size_bytes"))
            if not content_b64 and chunk_size:
                raise AppError(
                    "TRAINING_DATASET_TRANSFER_RELAY_FAILED",
                    502,
                    {"detail": f"source relay chunk missing content: {relpath}"},
                )
            await target_client.import_training_dataset_relay_chunk(
                {
                    "dataset_id": dataset_id,
                    "manifest_sha256": manifest_sha,
                    "file_path": relpath,
                    "offset": offset,
                    "content_base64": content_b64,
                },
                timeout=600,
            )
            transferred_bytes += chunk_size
            if chunk.get("eof") is True or chunk_size == 0:
                break
            offset += chunk_size
        imported_files += 1
    committed = await target_client.commit_training_dataset_relay_import(
        {
            "dataset_id": dataset_id,
            "manifest": manifest,
            "manifest_sha256": manifest_sha,
        },
        timeout=timeout_seconds,
    )
    if str(committed.get("status") or "").lower() not in {"succeeded", "ready", "ok"}:
        raise AppError(
            "TRAINING_DATASET_TRANSFER_RELAY_FAILED",
            502,
            {"detail": committed.get("error") or "target relay dataset commit failed"},
        )
    return {
        **committed,
        "relay": True,
        "source_gateway_id": source_gateway_id,
        "source_path": source_path,
        "transfer_mode": "platform_relay",
        "imported_files": imported_files,
        "transferred_bytes": transferred_bytes,
        "total_bytes": total_bytes,
    }


async def _transfer_training_dataset_between_gateways(
    *,
    source_instance: OpenClawInstance,
    target_instance: OpenClawInstance,
    dataset_id: str,
    source_path: str,
    timeout_seconds: int,
    ttl_seconds: int,
    force: bool,
    hash_files: bool,
) -> dict[str, Any]:
    if source_instance.id == target_instance.id:
        status = await AIClawClient(
            source_instance.id,
            gateway_kind=source_instance.bridge_gateway_kind,
        ).get_training_dataset_status({"dataset_id": dataset_id}, timeout=30)
        return {
            **status,
            "status": "already_on_training_gateway",
            "transfer_mode": "same_gateway",
            "source_gateway_id": source_instance.id,
            "target_gateway_id": target_instance.id,
            "source_path": source_path,
        }

    source_ops = _training_resource_ops(source_instance)
    target_ops = _training_resource_ops(target_instance)
    missing_source = [op for op in TRAINING_DATASET_BRIDGE_REQUIRED_SOURCE_OPS if op not in source_ops]
    missing_target = [op for op in TRAINING_DATASET_BRIDGE_REQUIRED_TARGET_OPS if op not in target_ops]
    if missing_source or missing_target:
        raise AppError(
            "TRAINING_DATASET_TRANSFER_UNAVAILABLE",
            409,
            {
                "detail": "bridge dataset transfer ops unavailable",
                "source_gateway_id": source_instance.id,
                "target_gateway_id": target_instance.id,
                "missing_source_ops": missing_source,
                "missing_target_ops": missing_target,
            },
        )
    source_client = AIClawClient(source_instance.id, gateway_kind=source_instance.bridge_gateway_kind)
    target_client = AIClawClient(target_instance.id, gateway_kind=target_instance.bridge_gateway_kind)
    exported = await source_client.start_training_dataset_export(
        {
            "dataset_id": dataset_id,
            "source_path": source_path,
            "ttl_seconds": ttl_seconds,
            "hash_files": hash_files,
        },
        timeout=timeout_seconds,
    )
    token = str(exported.get("token") or "").strip()
    if not token:
        raise AppError("TRAINING_DATASET_EXPORT_FAILED", 502, {"detail": "source dataset export did not return token"})
    source_public_host = _training_model_transfer_source_host(source_instance.id)
    import_export_url = _training_model_transfer_rewrite_url_host(exported.get("export_url"), source_public_host)
    import_manifest_url = _training_model_transfer_rewrite_url_host(exported.get("manifest_url"), source_public_host)
    transfer_mode = "direct_http"
    try:
        imported = await target_client.import_training_dataset_from_export(
            {
                "dataset_id": dataset_id,
                "source_gateway_id": source_instance.id,
                "source_path": source_path,
                "export_url": import_export_url,
                "manifest_url": import_manifest_url,
                "manifest_sha256": exported.get("manifest_sha256"),
                "token": token,
                "force": force,
                "timeout_seconds": timeout_seconds,
            },
            timeout=timeout_seconds,
        )
    except Exception as exc:
        if not _training_model_transfer_should_relay(exc):
            raise
        transfer_mode = "platform_relay"
        imported = await _relay_training_dataset_via_platform(
            source_client=source_client,
            target_client=target_client,
            dataset_id=dataset_id,
            source_gateway_id=source_instance.id,
            source_path=source_path,
            timeout_seconds=timeout_seconds,
            hash_files=hash_files,
            target_supports_file_status="training.dataset_import_relay_file_status" in target_ops,
        )
    if str(imported.get("status") or "").lower() not in {"succeeded", "ready", "ok"}:
        raise AppError(
            "TRAINING_DATASET_IMPORT_FAILED",
            502,
            {"detail": imported.get("error") or "target dataset import failed"},
        )
    dataset_path = str(imported.get("dataset_path") or "").strip()
    if not dataset_path:
        raise AppError("TRAINING_DATASET_IMPORT_FAILED", 502, {"detail": "target import did not return dataset_path"})
    return {
        **imported,
        "transfer_mode": transfer_mode,
        "source_gateway_id": source_instance.id,
        "target_gateway_id": target_instance.id,
        "source_path": source_path,
        "export": _training_transfer_safe_export_payload(exported),
    }


async def _externalize_gateway_dataset_package(
    db: AsyncSession,
    job: TrainingJob,
    package: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    source_instance = await _get_training_gateway_for_bootstrap(
        db,
        _training_model_transfer_worker_user(),
        config["source_gateway_id"],
    )
    target_instance = await _get_training_gateway_for_bootstrap(
        db,
        _training_model_transfer_worker_user(),
        config["target_gateway_id"],
    )
    if not bridge_registry.is_online(source_instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"数据源节点 {source_instance.id} 的 bridge 未连接"})
    if not bridge_registry.is_online(target_instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"训练节点 {target_instance.id} 的 bridge 未连接"})
    dataset_id = _training_dataset_id(job, package)
    source_client = AIClawClient(source_instance.id, gateway_kind=source_instance.bridge_gateway_kind)
    source_status = await _write_training_dataset_package_to_source(
        source_client,
        dataset_id=dataset_id,
        package=package,
        timeout_seconds=config["timeout_seconds"],
    )
    source_path = str(source_status.get("dataset_dir") or "").strip()
    if not source_path:
        raise AppError("TRAINING_DATASET_SOURCE_COMMIT_FAILED", 502, {"detail": "source dataset commit did not return dataset_dir"})
    transfer = await _transfer_training_dataset_between_gateways(
        source_instance=source_instance,
        target_instance=target_instance,
        dataset_id=dataset_id,
        source_path=source_path,
        timeout_seconds=config["timeout_seconds"],
        ttl_seconds=config["ttl_seconds"],
        force=config["force"],
        hash_files=config["hash_files"],
    )
    dataset_ref = str(package.get("dataset_ref") or job.dataset_ref or "")[:500]
    dataset_path = str(transfer.get("dataset_path") or "").strip()
    ref = {
        "storage": "bridge_dataset",
        "format": str(package.get("format") or "skillforge_sft_jsonl_v1")[:80],
        "dataset_id": dataset_id,
        "dataset_ref": dataset_ref,
        "manifest_hash": str(package.get("manifest_hash") or "")[:80],
        "sample_count": _safe_int(package.get("sample_count")),
        "train_count": _safe_int(package.get("train_count")),
        "eval_count": _safe_int(package.get("eval_count")),
        "source_gateway_id": source_instance.id,
        "source_dataset_path": source_status.get("dataset_path"),
        "source_dataset_dir": source_path,
        "training_gateway_id": target_instance.id,
        "dataset_path": dataset_path,
        "dataset_dir": transfer.get("dataset_dir"),
        "sha256": transfer.get("sha256") or source_status.get("sha256"),
        "manifest_sha256": transfer.get("manifest_sha256") or transfer.get("export", {}).get("manifest_sha256"),
        "transfer_mode": transfer.get("transfer_mode"),
        "created_at": isoformat_bjt(now_bjt()),
    }
    await _record_training_system_audit(
        db,
        "training_job.dataset_externalized",
        job,
        {
            "dataset_ref": dataset_ref,
            "dataset_id": dataset_id,
            "sample_count": ref["sample_count"],
            "source_gateway_id": source_instance.id,
            "training_gateway_id": target_instance.id,
            "transfer_mode": transfer.get("transfer_mode"),
            "dataset_path": dataset_path,
        },
    )
    return ref


async def _build_gateway_payload_for_job(db: AsyncSession, job: TrainingJob) -> dict[str, Any]:
    payload = _build_gateway_payload(job)
    if job.target_gateway_id:
        instance = await db.get(OpenClawInstance, job.target_gateway_id)
        if instance is not None:
            control = payload.get("control") if isinstance(payload.get("control"), dict) else {}
            payload["control"] = {
                **control,
                "agent_id": instance.id,
                "agent_purpose": _agent_purpose(instance),
                "agent_contract": _training_agent_contract_summary(instance),
            }
    bridge_dataset_config = _training_dataset_bridge_config(job)
    dataset_package = await _build_learning_gateway_dataset_package(
        db,
        job,
        external_storage=bridge_dataset_config is not None,
    )
    if dataset_package:
        payload["spec"] = _compact_gateway_spec(payload.get("spec") if isinstance(payload.get("spec"), dict) else {})
        if bridge_dataset_config is not None:
            try:
                payload["dataset_package_ref"] = await _externalize_gateway_dataset_package(
                    db,
                    job,
                    dataset_package,
                    bridge_dataset_config,
                )
                payload["spec"]["dataset_package_ref"] = {
                    key: value
                    for key, value in payload["dataset_package_ref"].items()
                    if key not in {"dataset_path", "dataset_dir"}
                }
            except Exception as exc:  # noqa: BLE001
                if bridge_dataset_config.get("required"):
                    externalization_failure = {
                        "status": "failed",
                        "source_gateway_id": bridge_dataset_config.get("source_gateway_id"),
                        "target_gateway_id": bridge_dataset_config.get("target_gateway_id"),
                        "reason": _extract_error_message(exc)[:1000],
                    }
                    logger.exception(
                        "[training-dataset] required bridge dataset externalization failed job={} source={} target={} error={}",
                        job.id,
                        bridge_dataset_config.get("source_gateway_id"),
                        bridge_dataset_config.get("target_gateway_id"),
                        _extract_error_message(exc),
                    )
                    spec_json = dict(job.spec_json or {}) if isinstance(job.spec_json, dict) else {}
                    job.spec_json = {**spec_json, "dataset_externalization": externalization_failure}
                    job.failure_stage = "dataset"
                    await _record_training_system_audit(
                        db,
                        "training_job.dataset_externalize_failed",
                        job,
                        externalization_failure,
                    )
                    await db.flush()
                    raise
                logger.warning(
                    "[training-dataset] bridge dataset externalization fallback job={} source={} target={} error={}",
                    job.id,
                    bridge_dataset_config.get("source_gateway_id"),
                    bridge_dataset_config.get("target_gateway_id"),
                    _extract_error_message(exc),
                )
                inline_package = await _build_learning_gateway_dataset_package(db, job, external_storage=False)
                if not inline_package:
                    raise
                dataset_package = inline_package
                payload["dataset_package"] = inline_package
                payload["spec"]["dataset_externalization"] = {
                    "status": "fallback_inline",
                    "source_gateway_id": bridge_dataset_config.get("source_gateway_id"),
                    "target_gateway_id": bridge_dataset_config.get("target_gateway_id"),
                    "reason": _extract_error_message(exc)[:500],
                }
        else:
            payload["dataset_package"] = dataset_package
    if _gateway_payload_size(payload) > TRAINING_GATEWAY_PAYLOAD_MAX_BYTES and dataset_package and bridge_dataset_config is None:
        samples = list(dataset_package.get("samples") or [])
        best_payload: dict[str, Any] | None = None
        low = TRAINING_GATEWAY_DATASET_MIN_INLINE_SAMPLES
        high = max(low - 1, len(samples) - 1)
        while low <= high:
            mid = (low + high) // 2
            next_package = _gateway_dataset_package_for_samples(
                job,
                dataset_ref=str(dataset_package.get("dataset_ref") or job.dataset_ref or ""),
                samples=_dataset_samples_with_eval_reserve(samples, mid),
            )
            payload["dataset_package"] = next_package
            if _gateway_payload_size(payload) <= TRAINING_GATEWAY_PAYLOAD_MAX_BYTES:
                best_payload = dict(payload)
                low = mid + 1
            else:
                high = mid - 1
        if best_payload is not None:
            return best_payload
        payload.pop("dataset_package", None)
    return payload


def _redact_gateway_payload(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    redacted = dict(payload)
    control = dict(redacted.get("control") or {})
    if control.get("callback_token"):
        control["callback_token"] = "***"
    redacted["control"] = control
    dataset_package = redacted.get("dataset_package")
    if isinstance(dataset_package, dict):
        redacted["dataset_package"] = {
            "format": dataset_package.get("format"),
            "source": dataset_package.get("source"),
            "dataset_ref": dataset_package.get("dataset_ref"),
            "target_skill_id": dataset_package.get("target_skill_id"),
            "manifest_hash": dataset_package.get("manifest_hash"),
            "manifest_sample_count": dataset_package.get("manifest_sample_count"),
            "manifest_train_count": dataset_package.get("manifest_train_count"),
            "manifest_eval_count": dataset_package.get("manifest_eval_count"),
            "inline_limit": dataset_package.get("inline_limit"),
            "inline_eval_limit": dataset_package.get("inline_eval_limit"),
            "inline_max_content_bytes": dataset_package.get("inline_max_content_bytes"),
            "sample_count": dataset_package.get("sample_count"),
            "train_count": dataset_package.get("train_count"),
            "eval_count": dataset_package.get("eval_count"),
            "input_contract": (
                dataset_package.get("input_contract")
                if isinstance(dataset_package.get("input_contract"), dict)
                else {}
            ),
            "output_contract": (
                dataset_package.get("output_contract")
                if isinstance(dataset_package.get("output_contract"), dict)
                else {}
            ),
            "lineage": dataset_package.get("lineage") if isinstance(dataset_package.get("lineage"), dict) else {},
            "raw_payload_returned": False,
            "samples": "[REDACTED]",
        }
    return redacted


def _extract_error_message(exc: Exception) -> str:
    if isinstance(exc, AppError):
        detail = exc.detail or {}
        if isinstance(detail, dict):
            raw = detail.get("detail") or detail.get("message") or detail.get("raw")
            if isinstance(raw, dict):
                raw = raw.get("message") or raw.get("detail") or raw
            if raw:
                return redact_secret_text(raw, limit=12000)
        return redact_secret_text(f"{exc.code}: {exc.message}", limit=12000)
    return redact_secret_text(exc, limit=12000)


def _sanitize_training_result_value(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_TRAINING_RESULT_SANITIZE_DEPTH:
        return redact_secret_text(value, limit=1000)
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_TRAINING_RESULT_LIST_ITEMS]:
            safe_key = redact_secret_text(key, limit=120)
            if str(key or "").lower() in TRAINING_RESULT_SAFE_TOKEN_KEYS:
                safe[safe_key] = _sanitize_training_result_value(item, depth=depth + 1)
            elif TRAINING_RESULT_SECRET_KEY_RE.search(str(key or "")):
                safe[safe_key] = "[REDACTED]"
            else:
                safe[safe_key] = _sanitize_training_result_value(item, depth=depth + 1)
        return safe
    if isinstance(value, list):
        return [
            _sanitize_training_result_value(item, depth=depth + 1)
            for item in value[:MAX_TRAINING_RESULT_LIST_ITEMS]
        ]
    if isinstance(value, str):
        return redact_secret_text(value, limit=12000)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_secret_text(value, limit=1000)


def _serialize_task(task: TrainingJobTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "job_id": task.job_id,
        "gateway_id": task.gateway_id,
        "worker_id": task.worker_id,
        "status": task.status,
        "progress": round(float(task.progress or 0), 4),
        "metrics": task.metrics_json or {},
        "logs_url": task.logs_url,
        "error_message": task.error_message,
        "created_at": isoformat_bjt(task.created_at),
        "updated_at": isoformat_bjt(task.updated_at),
    }


def _serialize_deployment(deployment: TrainingModelDeployment) -> dict[str, Any]:
    return {
        "id": deployment.id,
        "job_id": deployment.job_id,
        "department": deployment.department,
        "model_family": deployment.model_family,
        "artifact_id": deployment.artifact_id,
        "artifact_ref": deployment.artifact_ref_json or {},
        "eval_task_id": deployment.eval_task_id,
        "target_skill_ids": deployment.target_skill_ids_json or [],
        "deployment_target_gateway_id": deployment.deployment_target_gateway_id,
        "deployment_runtime_profile": deployment.deployment_runtime_profile,
        "status": deployment.status,
        "rollout_percent": int(deployment.rollout_percent or 0),
        "rollback_to": deployment.rollback_to,
        "requested_by": deployment.requested_by,
        "request_reason": deployment.request_reason,
        "approved_by": deployment.approved_by,
        "approved_at": isoformat_bjt(deployment.approved_at),
        "rejected_by": deployment.rejected_by,
        "rejected_at": isoformat_bjt(deployment.rejected_at),
        "reject_reason": deployment.reject_reason,
        "activated_at": isoformat_bjt(deployment.activated_at),
        "created_at": isoformat_bjt(deployment.created_at),
        "updated_at": isoformat_bjt(deployment.updated_at),
    }


def _serialize_deployment_job_summary(job: TrainingJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    return {
        "id": job.id,
        "title": job.title,
        "department": job.department,
        "status": job.status,
        "failure_stage": job.failure_stage,
        "job_type": job.job_type,
        "target_skill_id": job.target_skill_id,
        "target_gateway_id": job.target_gateway_id,
        "deployment_target_gateway_id": _deployment_target_from_mapping(spec),
        "deployment_runtime_profile": _deployment_runtime_profile_from_mapping(spec),
        "dataset_ref": job.dataset_ref,
        "updated_at": isoformat_bjt(job.updated_at),
    }


def _serialize_resource_job_summary(job: TrainingJob) -> dict[str, Any]:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    return {
        "id": job.id,
        "title": job.title,
        "status": job.status,
        "job_type": job.job_type,
        "target_skill_id": job.target_skill_id,
        "target_gateway_id": job.target_gateway_id,
        "deployment_target_gateway_id": _deployment_target_from_mapping(spec),
        "deployment_runtime_profile": _deployment_runtime_profile_from_mapping(spec),
        "model_name": _training_model_name(job, None),
        "estimated_duration_seconds": _training_estimated_duration_seconds(job),
        "updated_at": isoformat_bjt(job.updated_at),
    }


def _nested_value(source: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = source
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = None
                break
        if current not in (None, ""):
            return current
    return None


def _training_model_name(job: TrainingJob, deployment: dict[str, Any] | None) -> str:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    model = _nested_value(
        spec,
        "model_name",
        "output_model_name",
        "model_family",
        "output.model_name",
        "model.name",
    )
    if not model and deployment:
        model = deployment.get("model_family")
    return _clean_text(model or job.target_skill_id or job.title, limit=120) or job.id


def _training_model_date_segment(value: datetime | None = None) -> str:
    return (value or now_bjt()).strftime(TRAINING_AUTO_MODEL_DATE_FORMAT)


def _training_model_slug(value: Any, *, fallback: str) -> str:
    raw = str(value or fallback or "skillforge-model").strip().lower()
    slug = re.sub(r"[^a-z0-9_.:-]+", "-", raw).strip("-._:")
    return slug[:80] or fallback


def _training_model_family_with_date(value: Any, *, fallback: str) -> str:
    base = _training_model_slug(value, fallback=fallback)
    date_segment = _training_model_date_segment()
    if re.search(r"(?<!\d)20\d{6}(?!\d)", base):
        return base[:100]
    return f"{base}:{date_segment}"[:100]


def _training_auto_policy_payload() -> dict[str, Any]:
    return {
        "enabled": TRAINING_AUTO_ENABLED,
        "dataset_sync_enabled": TRAINING_AUTO_DATASET_SYNC_ENABLED,
        "approve_jobs": TRAINING_AUTO_APPROVE_JOBS,
        "evaluate_on_gateway_result": TRAINING_AUTO_EVALUATE_ON_RESULT,
        "deployment_request": TRAINING_AUTO_REQUEST_DEPLOYMENT,
        "approve_deployment": TRAINING_AUTO_APPROVE_DEPLOYMENT,
        "activate_deployment": TRAINING_AUTO_ACTIVATE_DEPLOYMENT,
        "include_manual_jobs": TRAINING_AUTO_INCLUDE_MANUAL_JOBS,
        "training_gateway_id": TRAINING_PRIMARY_GATEWAY_IDS[0],
        "deployment_gateway_id": TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID,
        "deployment_runtime_profile": TRAINING_QWEN36_DEPLOYMENT_RUNTIME_PROFILE,
        "model_name_date_format": "YYYYMMDD",
    }


def _apply_training_auto_defaults(
    spec: dict[str, Any],
    *,
    title: str,
    target_skill_id: str | None = None,
    full_auto: bool = False,
    force_model_date: bool = False,
) -> dict[str, Any]:
    next_spec = dict(spec or {})
    governance = next_spec.get("governance") if isinstance(next_spec.get("governance"), dict) else {}
    automation = next_spec.get("automation") if isinstance(next_spec.get("automation"), dict) else {}
    deployment = next_spec.get("deployment") if isinstance(next_spec.get("deployment"), dict) else {}
    raw_model_family = (
        deployment.get("model_family")
        or next_spec.get("model_family")
        or next_spec.get("model_name")
        or next_spec.get("output_model_name")
        or target_skill_id
        or title
    )
    model_family = (
        _training_model_family_with_date(raw_model_family, fallback=target_skill_id or title or "skillforge-model")
        if force_model_date
        else _clean_text(raw_model_family, limit=100)
    ) or _training_model_slug(target_skill_id or title, fallback="skillforge-model")
    if force_model_date or next_spec.get("model_family"):
        next_spec["model_family"] = model_family
    if force_model_date or next_spec.get("model_name"):
        next_spec["model_name"] = _clean_text(next_spec.get("model_name") or model_family, limit=120)
    next_spec["automation"] = {
        **automation,
        "enabled": automation.get("enabled", TRAINING_AUTO_ENABLED if full_auto else automation.get("enabled", TRAINING_AUTO_ENABLED)),
        "auto_dataset_sync": automation.get("auto_dataset_sync", TRAINING_AUTO_DATASET_SYNC_ENABLED),
        "auto_approve_job": automation.get("auto_approve_job", TRAINING_AUTO_APPROVE_JOBS if full_auto else automation.get("auto_approve_job", False)),
        "auto_evaluate_on_result": automation.get("auto_evaluate_on_result", TRAINING_AUTO_EVALUATE_ON_RESULT if full_auto else automation.get("auto_evaluate_on_result")),
        "training_gateway_id": automation.get("training_gateway_id") or TRAINING_PRIMARY_GATEWAY_IDS[0],
        "deployment_gateway_id": automation.get("deployment_gateway_id") or TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID,
        "model_name_date": automation.get("model_name_date") or (_training_model_date_segment() if force_model_date else None),
    }
    if full_auto or deployment:
        next_spec["deployment"] = {
            **deployment,
            "model_family": deployment.get("model_family") or model_family,
            "auto_request": deployment.get("auto_request", TRAINING_AUTO_REQUEST_DEPLOYMENT if full_auto else deployment.get("auto_request")),
            "auto_approve_canary": deployment.get("auto_approve_canary", TRAINING_AUTO_APPROVE_DEPLOYMENT if full_auto else deployment.get("auto_approve_canary")),
            "auto_active": deployment.get("auto_active", TRAINING_AUTO_ACTIVATE_DEPLOYMENT if full_auto else deployment.get("auto_active")),
            "rollout_percent": deployment.get("rollout_percent", 100 if full_auto else deployment.get("rollout_percent", 10)),
            "deployment_target_gateway_id": (
                _deployment_target_from_mapping(deployment)
                or _deployment_target_from_mapping(next_spec)
                or (TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID if full_auto else None)
            ),
            "deployment_runtime_profile": (
                _deployment_runtime_profile_from_mapping(deployment)
                or _deployment_runtime_profile_from_mapping(next_spec)
                or (TRAINING_QWEN36_DEPLOYMENT_RUNTIME_PROFILE if full_auto else None)
            ),
            "reason": deployment.get("reason") or deployment.get("request_reason") or ("training.auto policy" if full_auto else None),
        }
    next_spec["governance"] = {
        **governance,
        "auto_approval_reason": governance.get("auto_approval_reason") or ("training.auto policy" if full_auto else None),
    }
    return next_spec


def _training_auto_approval_enabled(job: TrainingJob) -> bool:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    governance = spec.get("governance") if isinstance(spec.get("governance"), dict) else {}
    automation = spec.get("automation") if isinstance(spec.get("automation"), dict) else {}
    if governance.get("no_auto_approval") is True:
        return False
    if automation.get("enabled") is False:
        return False
    return automation.get("auto_approve_job", TRAINING_AUTO_APPROVE_JOBS) is not False


def _automation_step(key: str, label: str, status: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    payload = {"key": key, "label": label, "status": status, "detail": detail}
    payload.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    return payload


def _training_dataset_automation_status(row: TrainingDatasetVersion) -> dict[str, Any]:
    sync_status = str(row.sync_status or "not_synced")
    sync_step_status = {
        "completed": "completed",
        "running": "running",
        "pending": "pending",
        "failed": "blocked",
    }.get(sync_status, "pending")
    blockers: list[dict[str, Any]] = []
    if sync_status == "failed":
        blockers.append({"key": "gb10_sync_failed", "label": "GB10 同步失败", "severity": "error"})
    if row.target_gateway_id and row.target_gateway_id != TRAINING_PRIMARY_GATEWAY_IDS[0]:
        blockers.append({
            "key": "non_gb10_target",
            "label": "数据集目标不是 GB10 237",
            "severity": "warn",
            "target_gateway_id": row.target_gateway_id,
        })
    return {
        "status": "blocked" if blockers else ("running" if sync_step_status in {"pending", "running"} else "ready"),
        "target_gateway_id": row.target_gateway_id or TRAINING_PRIMARY_GATEWAY_IDS[0],
        "policy": _training_auto_policy_payload(),
        "blockers": blockers,
        "steps": [
            _automation_step("entry", "入口", "completed", row.name or row.id),
            _automation_step("dataset_version", "数据集版本", "completed" if row.status in {"ready", "approved"} else "pending", row.version),
            _automation_step(
                "gb10_sync",
                "GB10 同步",
                sync_step_status,
                sync_status,
                target_gateway_id=row.target_gateway_id,
                sink_job_id=row.synced_sink_job_id,
            ),
        ],
    }


def _latest_task_by_op(tasks: list[TrainingJobTask], op: str) -> TrainingJobTask | None:
    for task in reversed(tasks):
        metrics = task.metrics_json if isinstance(task.metrics_json, dict) else {}
        if metrics.get("op") == op:
            return task
    return None


def _training_job_automation_status(
    job: TrainingJob,
    tasks: list[TrainingJobTask],
    deployments: list[TrainingModelDeployment],
) -> dict[str, Any]:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    governance = spec.get("governance") if isinstance(spec.get("governance"), dict) else {}
    automation = spec.get("automation") if isinstance(spec.get("automation"), dict) else {}
    dataset_spec = spec.get("dataset") if isinstance(spec.get("dataset"), dict) else {}
    routing = spec.get("routing") if isinstance(spec.get("routing"), dict) else {}
    sync_status = str(dataset_spec.get("sync_status") or "")
    submit_task = _latest_task_by_op(tasks, "training.submit_job")
    eval_task = _latest_task_by_op(tasks, "training.evaluate")
    latest_deployment = deployments[0] if deployments else None
    blockers: list[dict[str, Any]] = []
    if governance.get("no_auto_approval") is True:
        blockers.append({"key": "no_auto_approval", "label": "治理禁止自动审核", "severity": "warn"})
    if governance.get("no_auto_deploy") is True:
        blockers.append({"key": "no_auto_deploy", "label": "治理禁止自动部署", "severity": "warn"})
    if routing.get("status") == "missing_gateway" or not job.target_gateway_id:
        blockers.append({"key": "missing_training_gateway", "label": "缺少可用训练网关", "severity": "error"})
    if job.target_gateway_id and job.target_gateway_id != TRAINING_PRIMARY_GATEWAY_IDS[0]:
        blockers.append({
            "key": "non_gb10_training_gateway",
            "label": "当前训练目标不是 GB10 237",
            "severity": "warn",
            "target_gateway_id": job.target_gateway_id,
        })
    if sync_status == "failed":
        blockers.append({"key": "dataset_sync_failed", "label": "训练数据同步失败", "severity": "error"})
    if latest_deployment and latest_deployment.deployment_target_gateway_id and latest_deployment.deployment_target_gateway_id != TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID:
        blockers.append({
            "key": "non_mac236_deployment",
            "label": "部署目标不是 Mac236",
            "severity": "warn",
            "deployment_target_gateway_id": latest_deployment.deployment_target_gateway_id,
        })
    train_status = "pending"
    if job.status in {"running", "evaluating"}:
        train_status = "running"
    elif job.status in {"completed", "failed", "cancelled"}:
        train_status = "completed" if job.status == "completed" else "blocked"
    elif job.status == "queued":
        train_status = "pending"
    approval_status = "completed" if job.approved_by else ("blocked" if governance.get("no_auto_approval") is True else "pending")
    eval_status = "completed" if eval_task and eval_task.status == "completed" else (
        "blocked" if eval_task and eval_task.status == "failed" else ("pending" if job.status == "completed" else "pending")
    )
    deployment_status = "pending"
    if latest_deployment:
        deployment_status = "completed" if latest_deployment.status == "active" else (
            "running" if latest_deployment.status == "canary" else "pending"
        )
    if job.status == "failed":
        deployment_status = "blocked"
    sync_step_status = "completed" if sync_status == "completed" else (
        "blocked" if sync_status == "failed" else ("pending" if sync_status else "pending")
    )
    route_status = "completed" if job.target_gateway_id else "blocked"
    status = "blocked" if any(item.get("severity") == "error" for item in blockers) or job.status == "failed" else (
        "completed" if latest_deployment and latest_deployment.status == "active" else "running"
    )
    return {
        "status": status,
        "policy": _training_auto_policy_payload(),
        "model_name_date": automation.get("model_name_date") or _training_model_date_segment(),
        "auto_approval": {
            "enabled": _training_auto_approval_enabled(job),
            "approved_by": job.approved_by,
            "approved_at": isoformat_bjt(job.approved_at),
            "reason": governance.get("auto_approval_reason") or "training.auto policy",
        },
        "blockers": blockers,
        "steps": [
            _automation_step("entry", "入口", "completed", spec.get("source") or "manual"),
            _automation_step("dataset", "数据集版本", "completed" if job.dataset_ref else "pending", job.dataset_ref),
            _automation_step("gb10_sync", "GB10 同步", sync_step_status, sync_status or "等待数据集", sink_job_id=dataset_spec.get("synced_sink_job_id")),
            _automation_step("route", "GB10 自动寻路", route_status, routing.get("reason") or "create", route_plan=routing),
            _automation_step("approve", "自动审核", approval_status, job.approved_by or "training.auto"),
            _automation_step("train", "训练", train_status, job.status, task_id=getattr(submit_task, "id", None)),
            _automation_step("evaluate", "评估", eval_status, getattr(eval_task, "status", None) or "等待训练完成"),
            _automation_step(
                "deploy",
                "Mac236 内网部署",
                deployment_status,
                getattr(latest_deployment, "status", None) or "等待评估",
                deployment_id=getattr(latest_deployment, "id", None),
                deployment_target_gateway_id=getattr(latest_deployment, "deployment_target_gateway_id", None),
            ),
        ],
    }


def _training_base_model(job: TrainingJob) -> str | None:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    return _clean_text(
        _nested_value(spec, "base_model", "model.base_model", "model.base", "foundation_model"),
        limit=120,
    )


def _training_estimated_duration_seconds(job: TrainingJob) -> int:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    raw_seconds = _nested_value(spec, "estimated_duration_seconds", "estimate.duration_seconds")
    if raw_seconds is not None:
        return _safe_int(raw_seconds)
    raw_minutes = _nested_value(spec, "estimated_minutes", "estimate.minutes", "duration_minutes")
    if raw_minutes is not None:
        return _safe_int(raw_minutes) * 60
    raw_hours = _nested_value(spec, "estimated_gpu_hours", "gpu_estimate.hours", "estimate.gpu_hours")
    if raw_hours is not None:
        return _safe_int(_safe_number(raw_hours) * 3600)
    return 0


def _training_parameter_summary(job: TrainingJob) -> dict[str, Any]:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    params = _nested_value(spec, "parameters", "training_args", "hyperparameters")
    if not isinstance(params, dict):
        params = {}
    summary: dict[str, Any] = {}
    for key in (
        "epochs",
        "learning_rate",
        "lr",
        "batch_size",
        "micro_batch_size",
        "rank",
        "lora_rank",
        "max_steps",
        "optimizer",
    ):
        value = params.get(key) if isinstance(params, dict) else None
        if value not in (None, ""):
            summary[key] = value
    return summary


def _seconds_between(start: Any, end: Any) -> int:
    try:
        if not start or not end:
            return 0
        return max(0, int((end - start).total_seconds()))
    except Exception:  # noqa: BLE001
        return 0


def _format_eta(remaining_seconds: int) -> str | None:
    if remaining_seconds <= 0:
        return None
    hours = remaining_seconds // 3600
    minutes = (remaining_seconds % 3600) // 60
    if hours:
        return f"约 {hours} 小时 {minutes} 分钟"
    return f"约 {max(minutes, 1)} 分钟"


def _training_runtime_summary(job: TrainingJob, tasks: list[TrainingJobTask]) -> dict[str, Any]:
    latest_task = tasks[-1] if tasks else None
    progress = round(float(latest_task.progress or 0), 4) if latest_task else 0.0
    now = now_bjt()
    start_at = job.approved_at or job.created_at
    elapsed_seconds = _seconds_between(start_at, now)
    estimated_seconds = _training_estimated_duration_seconds(job)
    remaining_seconds = 0
    if job.status in {"queued", "running", "evaluating", "unknown"}:
        if progress > 0 and elapsed_seconds:
            remaining_seconds = max(0, int(elapsed_seconds / progress - elapsed_seconds))
        elif estimated_seconds:
            remaining_seconds = estimated_seconds
    return {
        "progress": progress,
        "latest_task_status": latest_task.status if latest_task else None,
        "latest_task_id": latest_task.id if latest_task else None,
        "elapsed_seconds": elapsed_seconds,
        "estimated_duration_seconds": estimated_seconds,
        "remaining_seconds": remaining_seconds,
        "eta_text": _format_eta(remaining_seconds),
        "updated_at": isoformat_bjt(latest_task.updated_at if latest_task else job.updated_at),
    }


def _training_chat_target(
    job: TrainingJob,
    latest_deployment: dict[str, Any] | None,
    primary_artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    deployment_status = str((latest_deployment or {}).get("status") or "")
    deployment_id = str((latest_deployment or {}).get("id") or "")
    model_family = str(
        (latest_deployment or {}).get("model_family")
        or _training_model_name(job, latest_deployment)
        or ""
    )
    artifact_ref = (latest_deployment or {}).get("artifact_ref")
    if not isinstance(artifact_ref, dict):
        artifact_ref = {}
    artifact_id = str(
        artifact_ref.get("id")
        or (latest_deployment or {}).get("artifact_id")
        or (primary_artifact or {}).get("id")
        or ""
    )
    artifact_uri = str(artifact_ref.get("uri") or "")
    target_skill_ids = (latest_deployment or {}).get("target_skill_ids")
    if not isinstance(target_skill_ids, list):
        target_skill_ids = [job.target_skill_id] if job.target_skill_id else []
    deployment_target_gateway_id = _clean_text(
        (latest_deployment or {}).get("deployment_target_gateway_id"),
        limit=50,
    ) or _clean_text(job.target_gateway_id, limit=50)

    disabled_reason = None
    if not latest_deployment:
        disabled_reason = "模型尚未提交部署审批"
    elif deployment_status not in TRAINING_DEPLOYMENT_ACTIVE_STATUSES:
        disabled_reason = f"部署状态为 {deployment_status or 'unknown'}，需进入灰度或已部署"
    elif not deployment_id:
        disabled_reason = "缺少模型部署 ID"
    elif not model_family:
        disabled_reason = "缺少模型名称"
    elif not job.department:
        disabled_reason = "缺少部门路由"
    elif not artifact_id:
        disabled_reason = "缺少模型产物"

    inference_disabled_reason = disabled_reason
    if inference_disabled_reason is None and not artifact_uri:
        inference_disabled_reason = "缺少模型产物 URI，无法在部署节点推理"
    if inference_disabled_reason is None and not deployment_target_gateway_id:
        inference_disabled_reason = "缺少部署目标节点，无法路由模型推理"

    payload = {
        "ready": disabled_reason is None,
        "disabled_reason": disabled_reason,
        "model_deployment_id": deployment_id or None,
        "model_family": model_family or None,
        "training_job_id": job.id,
        "department": job.department,
        "artifact_id": artifact_id or None,
        "artifact_uri_present": bool(artifact_uri),
        "target_skill_ids": [str(item) for item in target_skill_ids if str(item or "").strip()],
        "target_gateway_id": deployment_target_gateway_id,
        "deployment_status": deployment_status or None,
        "inference_ready": inference_disabled_reason is None,
        "inference_disabled_reason": inference_disabled_reason,
    }
    if job.target_gateway_id and job.target_gateway_id != deployment_target_gateway_id:
        payload["training_gateway_id"] = job.target_gateway_id
    return payload


def _serialize_training_plan(
    job: TrainingJob,
    tasks: list[TrainingJobTask],
    deployments: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_deployment = deployments[0] if deployments else None
    artifacts = _collect_training_artifacts(tasks) if tasks else []
    primary_artifact = artifacts[0] if artifacts else None
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    routing = spec.get("routing") if isinstance(spec.get("routing"), dict) else {}
    chat_target = _training_chat_target(job, latest_deployment, primary_artifact)
    return {
        "model_name": _training_model_name(job, latest_deployment),
        "base_model": _training_base_model(job),
        "job_type": job.job_type,
        "dataset_ref": job.dataset_ref,
        "parameters": _training_parameter_summary(job),
        "runtime": _training_runtime_summary(job, tasks),
        "routing": routing,
        "artifact": primary_artifact,
        "artifacts_count": len(artifacts),
        "deployment_status": latest_deployment.get("status") if latest_deployment else None,
        "deployment_id": latest_deployment.get("id") if latest_deployment else None,
        "chat_target": chat_target,
    }


def _serialize_job(
    job: TrainingJob,
    tasks: list[TrainingJobTask] | None = None,
    deployments: list[TrainingModelDeployment] | None = None,
) -> dict[str, Any]:
    task_items = tasks or []
    deployment_items = deployments or []
    serialized_deployments = [_serialize_deployment(item) for item in deployment_items]
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    automation_status = _training_job_automation_status(job, task_items, deployment_items)
    return {
        "id": job.id,
        "title": job.title,
        "department": job.department,
        "created_by": job.created_by,
        "status": job.status,
        "job_type": job.job_type,
        "training_strategy": job.training_strategy,
        "target_skill_id": job.target_skill_id,
        "target_gateway_id": job.target_gateway_id,
        "deployment_target_gateway_id": _deployment_target_from_mapping(spec),
        "deployment_runtime_profile": _deployment_runtime_profile_from_mapping(spec),
        "dataset_ref": job.dataset_ref,
        "objective": job.objective,
        "risk_level": job.risk_level,
        "failure_stage": job.failure_stage,
        "spec": spec,
        "gateway_routing": spec.get("routing"),
        "route_plan": spec.get("routing"),
        "automation_status": automation_status,
        "automation_blockers": automation_status.get("blockers", []),
        "gateway_payload": _redact_gateway_payload(job.gateway_payload_json),
        "approval_required": bool(job.approval_required),
        "approved_by": job.approved_by,
        "approved_at": isoformat_bjt(job.approved_at),
        "cancelled_by": job.cancelled_by,
        "cancelled_at": isoformat_bjt(job.cancelled_at),
        "created_at": isoformat_bjt(job.created_at),
        "updated_at": isoformat_bjt(job.updated_at),
        "training_plan": _serialize_training_plan(job, task_items, serialized_deployments),
        "tasks": [_serialize_task(task) for task in task_items],
        "deployments": serialized_deployments,
        "latest_deployment": serialized_deployments[0] if serialized_deployments else None,
    }


async def _record_training_audit(db: AsyncSession, user: User, action: str, job: TrainingJob, detail: dict | None = None) -> None:
    safe_detail = _sanitize_training_result_value(detail or {})
    if not isinstance(safe_detail, dict):
        safe_detail = {}
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action=action,
        target_type="training_job",
        target_id=job.id,
        detail={
            "department": job.department,
            "status": job.status,
            **safe_detail,
        },
    ))


async def _record_training_system_audit(db: AsyncSession, action: str, job: TrainingJob, detail: dict | None = None) -> None:
    safe_detail = _sanitize_training_result_value(detail or {})
    if not isinstance(safe_detail, dict):
        safe_detail = {}
    db.add(AuditLog(
        user_id="training_gateway",
        action=action,
        target_type="training_job",
        target_id=job.id,
        detail={
            "department": job.department,
            "status": job.status,
            **safe_detail,
        },
    ))


async def _record_training_deployment_audit(
    db: AsyncSession,
    user: User,
    action: str,
    deployment: TrainingModelDeployment,
    detail: dict | None = None,
) -> None:
    safe_detail = _sanitize_training_result_value(detail or {})
    if not isinstance(safe_detail, dict):
        safe_detail = {}
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action=action,
        target_type="training_deployment",
        target_id=deployment.id,
        detail={
            "job_id": deployment.job_id,
            "department": deployment.department,
            "model_family": deployment.model_family,
            "target_skill_ids": deployment.target_skill_ids_json or [],
            "status": deployment.status,
            **safe_detail,
        },
    ))


def _agent_purpose(instance: OpenClawInstance) -> str:
    value = str(getattr(instance, "agent_purpose", "") or "skill_runtime").strip().lower()
    return value if value in {"skill_runtime", "analysis", "training", "media", "mixed"} else "skill_runtime"


def _is_training_agent(instance: OpenClawInstance) -> bool:
    return _agent_purpose(instance) in {"training", "mixed"}


def _is_platform_training_gateway(instance: OpenClawInstance) -> bool:
    return _is_training_agent(instance) and (
        bool(getattr(instance, "is_platform_default", False))
        or _is_gb10_training_gateway(instance)
    )


def _training_gateway_reserved_for(instance: OpenClawInstance) -> str | None:
    instance_id = str(getattr(instance, "id", "") or "").strip()
    if instance_id in TRAINING_COMFYUI_RESERVED_GATEWAY_IDS:
        return "comfyui"
    return None


def _instance_hardware_blob(instance: OpenClawInstance, cap: dict[str, Any] | None = None) -> str:
    capabilities = cap if isinstance(cap, dict) else _safe_capabilities(getattr(instance, "bridge_capabilities_json", None))
    parts: list[str] = [
        str(getattr(instance, "id", "") or ""),
        str(getattr(instance, "name", "") or ""),
        str(getattr(instance, "bridge_gateway_kind", "") or ""),
        str(getattr(instance, "agent_type", "") or ""),
    ]
    for key in ("hardware", "device", "device_class", "accelerator", "platform", "arch", "runtime"):
        value = capabilities.get(key)
        if isinstance(value, (str, int, float, bool)):
            parts.append(str(value))
        elif isinstance(value, dict):
            parts.extend(str(item) for item in value.values() if isinstance(item, (str, int, float, bool)))
    for runtime in capabilities.get("runtimes") or []:
        if isinstance(runtime, (str, int, float, bool)):
            parts.append(str(runtime))
    for item in _normalize_gpu_items(capabilities.get("gpu")):
        parts.append(str(item.get("name") or ""))
    return " ".join(parts).lower()


def _is_gb10_training_gateway(instance: OpenClawInstance, cap: dict[str, Any] | None = None) -> bool:
    instance_id = str(getattr(instance, "id", "") or "").strip()
    if instance_id in TRAINING_GB10_SEED_GATEWAY_IDS:
        return True
    blob = _instance_hardware_blob(instance, cap)
    return "gb10" in blob and "nvidia" in blob


def _is_mac_deployment_gateway(instance: OpenClawInstance, cap: dict[str, Any] | None = None) -> bool:
    instance_id = str(getattr(instance, "id", "") or "").strip()
    if instance_id in TRAINING_MAC_DEPLOYMENT_SEED_GATEWAY_IDS:
        return True
    blob = _instance_hardware_blob(instance, cap)
    return "mac" in blob or "apple m" in blob or "m3 ultra" in blob


def _is_m3_deployment_gateway(instance: OpenClawInstance, cap: dict[str, Any] | None = None) -> bool:
    instance_id = str(getattr(instance, "id", "") or "").strip()
    if instance_id in TRAINING_MAC_DEPLOYMENT_SEED_GATEWAY_IDS:
        return True
    blob = _instance_hardware_blob(instance, cap)
    return ("m3" in blob and ("ultra" in blob or "apple" in blob or "mac" in blob)) or "m3 ultra" in blob


def _training_gateway_route_policy(instance: OpenClawInstance) -> dict[str, Any]:
    instance_id = str(getattr(instance, "id", "") or "").strip()
    reserved_for = _training_gateway_reserved_for(instance)
    cap = _safe_capabilities(getattr(instance, "bridge_capabilities_json", None))
    gb10_training = _is_gb10_training_gateway(instance, cap)
    mac_deployment = _is_mac_deployment_gateway(instance, cap)
    if reserved_for:
        role = "reserved_comfyui"
    elif gb10_training:
        role = "primary_training"
    elif mac_deployment:
        role = "deployment_inference"
    elif _is_training_agent(instance):
        role = "training_fallback"
    else:
        role = "runtime"
    return {
        "role": role,
        "preferred_for_training": gb10_training,
        "gb10_training_pool": gb10_training,
        "mac_deployment_pool": mac_deployment,
        "reserved_for": reserved_for,
        "deployment_target_default": mac_deployment,
        "train_route_eligible": _is_training_agent(instance) and reserved_for is None,
    }


def _training_gateway_scope(job_department: str, instance: OpenClawInstance) -> str:
    if str(getattr(instance, "department", "") or "") == job_department:
        return "department"
    if _is_platform_training_gateway(instance):
        return "platform_fallback"
    return "external"


def _can_use_training_gateway_for_job(user: User, job_department: str, instance: OpenClawInstance) -> bool:
    if _can_view_all_training_resources(user):
        return True
    if str(getattr(instance, "department", "") or "") == job_department:
        return True
    return _is_platform_training_gateway(instance)


def _training_gateway_capability(instance: OpenClawInstance) -> tuple[dict[str, Any], dict[str, Any]]:
    cap = _safe_capabilities(getattr(instance, "bridge_capabilities_json", None))
    gpu_items = _normalize_gpu_items(cap.get("gpu"))
    training = _normalize_training_capability(cap.get("training"), gpu_count=len(gpu_items))
    advertised_ops = {str(item or "").strip() for item in (cap.get("ops") or []) if str(item or "").strip()}
    # Older stored capability snapshots capped ops before late training ops were
    # persisted. If the bridge still declares inference as a supported task,
    # recover the lifecycle op so deployable training routing is not blocked.
    if "inference" in set(training.get("supported_tasks") or []) and "training.inference" not in advertised_ops:
        cap = {**cap, "ops": [*(cap.get("ops") or []), "training.inference"]}
    training["route_eligible"] = _is_training_agent(instance)
    reserved_for = _training_gateway_reserved_for(instance)
    if reserved_for:
        training["route_eligible"] = False
        training["reserved_for"] = reserved_for
    if not training["route_eligible"]:
        training["gateway"] = False
        training["supported_tasks"] = []
    return cap, training


def _training_agent_contract_summary(instance: OpenClawInstance) -> dict[str, Any]:
    cap, training = _training_gateway_capability(instance)
    advertised_op_set = {str(item or "").strip() for item in (cap.get("ops") or []) if str(item or "").strip()}
    advertised_ops = sorted(advertised_op_set)[:40]
    control_ops = list(TRAINING_GATEWAY_REQUIRED_OPS)
    missing_ops = [op for op in TRAINING_GATEWAY_REQUIRED_OPS if op not in advertised_op_set]
    supported_tasks = [str(item or "").strip() for item in training.get("supported_tasks") or [] if str(item or "").strip()]
    missing: list[str] = []
    reserved_for = _training_gateway_reserved_for(instance)
    if not _is_training_agent(instance):
        missing.append("agent_purpose")
    if reserved_for:
        missing.append("resource_reservation")
    if not training.get("gateway"):
        missing.append("training_gateway")
    if missing_ops:
        missing.append("control_ops")
    if not supported_tasks:
        missing.append("supported_tasks")
    submission_ready = (
        _is_training_agent(instance)
        and bool(training.get("gateway"))
        and "training.submit_job" in advertised_op_set
        and bool(supported_tasks)
    )
    lifecycle_ready = submission_ready and not missing_ops
    return {
        "complete": not missing,
        "missing": missing,
        "purpose": _agent_purpose(instance),
        "gateway_kind": str(
            cap.get("gateway_kind")
            or getattr(instance, "bridge_gateway_kind", "")
            or getattr(instance, "agent_type", "")
            or "unknown"
        )[:50],
        "input_channels": ["training_job.gateway_payload", "learning_artifacts.dataset_package"],
        "control_ops": control_ops,
        "advertised_ops": advertised_ops,
        "required_ops": control_ops,
        "missing_ops": missing_ops,
        "supported_tasks": supported_tasks,
        "route_policy": _training_gateway_route_policy(instance),
        "output_channels": ["training_job.gateway_result", "training_artifact_refs", "training_metrics"],
        "lineage_required": True,
        "flow_traceable": True,
        "submission_ready": submission_ready,
        "lifecycle_ready": lifecycle_ready,
    }


def _training_gateway_required_ops_for_job_type(job_type: str) -> tuple[str, ...]:
    if job_type in TRAINING_DEPLOYABLE_JOB_TYPES:
        return TRAINING_GATEWAY_REQUIRED_OPS
    return ("training.submit_job",)


def _training_gateway_job_support_error(instance: OpenClawInstance, job_type: str) -> dict[str, Any] | None:
    reserved_for = _training_gateway_reserved_for(instance)
    if reserved_for:
        return {
            "detail": "target gateway is reserved and cannot run SkillForge training jobs",
            "reserved_for": reserved_for,
        }
    cap, training = _training_gateway_capability(instance)
    advertised_ops = {str(item or "").strip() for item in (cap.get("ops") or []) if str(item or "").strip()}
    required_ops = _training_gateway_required_ops_for_job_type(job_type)
    missing_ops = [op for op in required_ops if op not in advertised_ops]
    if missing_ops:
        detail = (
            "target gateway does not support deployable training lifecycle"
            if job_type in TRAINING_DEPLOYABLE_JOB_TYPES
            else "target gateway does not support training.submit_job"
        )
        return {
            "detail": detail,
            "missing_ops": missing_ops,
            "required_ops": list(required_ops),
        }
    if not training.get("gateway"):
        return {"detail": "target gateway is not a training gateway"}
    gateway_task = _training_gateway_task_for_job_type(job_type)
    if gateway_task not in training.get("supported_tasks", []):
        return {
            "detail": "target gateway does not support this training job type",
            "job_type": job_type,
            "required_task": gateway_task,
        }
    return None


def _training_gateway_supports_job_type(instance: OpenClawInstance, job_type: str) -> bool:
    if not _is_training_agent(instance):
        return False
    return _training_gateway_job_support_error(instance, job_type) is None


async def _get_training_gateway_for_bootstrap(
    db: AsyncSession,
    user: User,
    gateway_id: str,
) -> OpenClawInstance:
    if not _can_approve_training_job(user):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    gateway_id = str(gateway_id or "").strip()
    if not gateway_id:
        raise AppError("TRAINING_GATEWAY_INVALID", 422, {"detail": "gateway_id is required"})
    instance = await db.get(OpenClawInstance, gateway_id)
    if instance is None or not bool(getattr(instance, "is_active", True)):
        raise AppError("AICLAW_INSTANCE_NOT_FOUND", 404, {"detail": f"training gateway {gateway_id} not found"})
    if not _can_use_training_gateway_for_job(user, _user_department(user), instance):
        raise AppError("AUTH_DEPARTMENT_DENIED", 403)
    return instance


def _normalize_training_bootstrap_request(payload: dict | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    profile = str(raw.get("profile") or "qlora-1b").strip().lower()
    if profile not in TRAINING_BOOTSTRAP_PROFILES_ALLOWED:
        raise AppError("TRAINING_BOOTSTRAP_INVALID", 422, {"detail": "unsupported training bootstrap profile"})
    cuda = str(raw.get("cuda") or "cu130").strip().lower()
    if cuda not in TRAINING_BOOTSTRAP_CUDA_ALLOWED:
        raise AppError("TRAINING_BOOTSTRAP_INVALID", 422, {"detail": "unsupported training bootstrap cuda target"})
    try:
        timeout_seconds = int(raw.get("timeout_seconds") or 3600)
    except (TypeError, ValueError):
        timeout_seconds = 3600
    agent_purpose = raw.get("agent_purpose")
    if agent_purpose is not None:
        agent_purpose = str(agent_purpose or "").strip().lower()
        if agent_purpose == "":
            agent_purpose = None
        elif agent_purpose not in TRAINING_BOOTSTRAP_AGENT_PURPOSE_ALLOWED:
            raise AppError("TRAINING_BOOTSTRAP_INVALID", 422, {"detail": "agent_purpose must be training or mixed"})
    return {
        "profile": profile,
        "cuda": cuda,
        "force": bool(raw.get("force")),
        "dry_run": bool(raw.get("dry_run")),
        "timeout_seconds": max(60, min(timeout_seconds, 7200)),
        "agent_purpose": agent_purpose,
    }


def _normalize_training_model_request(payload: dict | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    profile = str(raw.get("profile") or TRAINING_QWEN35_4B_PROFILE).strip().lower()
    if profile not in TRAINING_MODEL_PROFILES_ALLOWED:
        raise AppError("TRAINING_MODEL_INVALID", 422, {"detail": "unsupported training model profile"})
    profile_cfg = TRAINING_MODEL_PROFILE_CONFIG[profile]
    bootstrap_profile = str(raw.get("bootstrap_profile") or profile_cfg["bootstrap_profile"]).strip().lower()
    if bootstrap_profile not in TRAINING_BOOTSTRAP_PROFILES_ALLOWED:
        raise AppError("TRAINING_MODEL_INVALID", 422, {"detail": "unsupported training bootstrap profile"})
    validate_mode = str(raw.get("validate_mode") or "metadata").strip().lower()
    if validate_mode not in TRAINING_MODEL_VALIDATE_MODES_ALLOWED:
        raise AppError("TRAINING_MODEL_INVALID", 422, {"detail": "unsupported training model validate_mode"})
    source = str(raw.get("source") or profile_cfg.get("default_source") or "auto").strip().lower()
    if source not in TRAINING_MODEL_SOURCES_ALLOWED:
        raise AppError("TRAINING_MODEL_INVALID", 422, {"detail": "unsupported training model source"})
    if profile == TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE and source not in TRAINING_MODEL_INTERNAL_SOURCES:
        raise AppError(
            "TRAINING_MODEL_INVALID",
            422,
            {"detail": "qwen3.6 35b aggressive profile must use internal, local_path or artifact source"},
        )
    internal_model_ref = _clean_text(
        raw.get("internal_model_ref")
        or raw.get("internalModelRef")
        or raw.get("model_path")
        or raw.get("modelPath")
        or raw.get("artifact_uri")
        or raw.get("artifactUri")
        or profile_cfg.get("internal_model_ref"),
        limit=500,
    )
    revision = str(raw.get("revision") or profile_cfg.get("revision") or "main").strip() or "main"
    if not re.match(r"^[A-Za-z0-9._/-]{1,120}$", revision):
        raise AppError("TRAINING_MODEL_INVALID", 422, {"detail": "invalid training model revision"})
    modelscope_revision = str(raw.get("modelscope_revision") or profile_cfg.get("modelscope_revision") or "master").strip() or "master"
    if not re.match(r"^[A-Za-z0-9._/-]{1,120}$", modelscope_revision):
        raise AppError("TRAINING_MODEL_INVALID", 422, {"detail": "invalid modelscope model revision"})
    try:
        timeout_seconds = int(raw.get("timeout_seconds") or 7200)
    except (TypeError, ValueError):
        timeout_seconds = 7200
    return {
        "profile": profile,
        "model_id": profile_cfg["model_id"],
        "revision": revision,
        "modelscope_revision": modelscope_revision,
        "bootstrap_profile": bootstrap_profile,
        "internal_model_ref": internal_model_ref,
        "force": _truthy_value(raw.get("force")),
        "dry_run": _truthy_value(raw.get("dry_run")),
        "auto_discover": _truthy_value(raw.get("auto_discover") if "auto_discover" in raw else raw.get("autoDiscover")),
        "timeout_seconds": max(60, min(timeout_seconds, 21600)),
        "validate_mode": validate_mode,
        "source": source,
    }


def _normalize_training_model_discover_all_request(payload: dict | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    model = _normalize_training_model_request(raw)
    roots = []
    raw_roots = raw.get("roots") if isinstance(raw.get("roots"), list) else []
    for item in raw_roots:
        text = _clean_text(item, limit=500)
        if not text:
            continue
        if text.startswith("file://"):
            text = text[7:]
        if not (text.startswith("/") or text.startswith("~")):
            continue
        if text not in roots:
            roots.append(text)
        if len(roots) >= TRAINING_MODEL_DISCOVER_ALL_MAX_ROOTS:
            break
    terms = []
    raw_terms = raw.get("terms") if isinstance(raw.get("terms"), list) else []
    for item in raw_terms:
        text = _clean_text(item, limit=160)
        if not text:
            continue
        text = re.sub(r"[^A-Za-z0-9._:/ -]+", "", text).strip().lower()
        if text and text not in terms:
            terms.append(text)
        if len(terms) >= TRAINING_MODEL_DISCOVER_ALL_MAX_TERMS:
            break
    raw_target_ids = raw.get("gateway_ids") or raw.get("gatewayIds") or raw.get("target_gateway_ids") or raw.get("targetGatewayIds")
    target_gateway_ids = []
    if isinstance(raw_target_ids, list):
        for item in raw_target_ids:
            text = _clean_text(item, limit=80)
            if text and text not in target_gateway_ids:
                target_gateway_ids.append(text)
    training_gateway_id = (
        _clean_text(raw.get("training_gateway_id") or raw.get("trainingGatewayId"), limit=80)
        or TRAINING_PRIMARY_GATEWAY_IDS[0]
    )
    try:
        max_dirs = int(raw.get("max_dirs") or raw.get("maxDirs") or 12000)
    except (TypeError, ValueError):
        max_dirs = 12000
    try:
        max_depth = int(raw.get("max_depth") or raw.get("maxDepth") or 9)
    except (TypeError, ValueError):
        max_depth = 9
    try:
        limit = int(raw.get("limit") or 12)
    except (TypeError, ValueError):
        limit = 12
    model.update({
        "roots": roots,
        "terms": terms,
        "target_gateway_ids": target_gateway_ids,
        "training_gateway_id": training_gateway_id,
        "max_dirs": max(1, min(max_dirs, TRAINING_MODEL_DISCOVER_ALL_MAX_DIRS)),
        "max_depth": max(1, min(max_depth, TRAINING_MODEL_DISCOVER_ALL_MAX_DEPTH)),
        "limit": max(1, min(limit, TRAINING_MODEL_DISCOVER_ALL_MAX_LIMIT)),
        "include_metadata": _truthy_value(raw.get("include_metadata") if "include_metadata" in raw else raw.get("includeMetadata")),
    })
    return model


def _normalize_training_model_transfer_request(payload: dict | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    model = _normalize_training_model_discover_all_request(raw)
    source_gateway_id = _clean_text(raw.get("source_gateway_id") or raw.get("sourceGatewayId"), limit=80)
    source_path = _clean_text(raw.get("source_path") or raw.get("sourcePath") or raw.get("path"), limit=1000)
    source_public_host = _clean_text(
        raw.get("source_public_host")
        or raw.get("sourcePublicHost")
        or raw.get("source_host")
        or raw.get("sourceHost"),
        limit=120,
    )
    if source_path and source_path.startswith("file://"):
        source_path = source_path[7:]
    try:
        timeout_seconds = int(raw.get("timeout_seconds") or raw.get("timeoutSeconds") or 21600)
    except (TypeError, ValueError):
        timeout_seconds = 21600
    try:
        ttl_seconds = int(raw.get("ttl_seconds") or raw.get("ttlSeconds") or 3600)
    except (TypeError, ValueError):
        ttl_seconds = 3600
    resume_manifest_sha256 = _clean_text(
        raw.get("resume_manifest_sha256") or raw.get("resumeManifestSha256"),
        limit=64,
    )
    if resume_manifest_sha256 and not re.fullmatch(r"[a-fA-F0-9]{24,64}", resume_manifest_sha256):
        resume_manifest_sha256 = None
    resume_transferred_bytes = max(
        0,
        _safe_int(raw.get("resume_transferred_bytes") or raw.get("resumeTransferredBytes")),
    )
    resume_imported_files = max(
        0,
        _safe_int(raw.get("resume_imported_files") or raw.get("resumeImportedFiles")),
    )
    resume_current_file = _clean_text(
        raw.get("resume_current_file") or raw.get("resumeCurrentFile"),
        limit=500,
    )
    model.update({
        "source_gateway_id": source_gateway_id,
        "source_path": source_path,
        "source_public_host": source_public_host,
        "force": _truthy_value(raw.get("force")),
        "prepare": False if raw.get("prepare") is False else True,
        "hash_files": False if raw.get("hash_files") is False or raw.get("hashFiles") is False else True,
        "timeout_seconds": max(60, min(timeout_seconds, 86400)),
        "ttl_seconds": max(60, min(ttl_seconds, 24 * 60 * 60)),
        "resume_manifest_sha256": str(resume_manifest_sha256 or "").lower() or None,
        "resume_transferred_bytes": resume_transferred_bytes,
        "resume_imported_files": resume_imported_files,
        "resume_current_file": resume_current_file,
    })
    return model


def _normalize_training_runtime_config_request(payload: dict | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    env: dict[str, str] = {}
    raw_env = raw.get("env") if isinstance(raw.get("env"), dict) else {}
    for key, value in raw_env.items():
        clean_key = str(key or "").strip()
        if clean_key not in TRAINING_RUNTIME_ENV_ALLOWED:
            raise AppError("TRAINING_RUNTIME_CONFIG_INVALID", 422, {"detail": f"unsupported runtime env key: {clean_key}"})
        clean_value = _clean_text(value, limit=500, required=True)
        if clean_value is not None:
            env[clean_key] = clean_value
    model_dir = _clean_text(
        raw.get("qwen36_35b_model_dir")
        or raw.get("qwen3_6_35b_model_dir")
        or raw.get("model_dir")
        or raw.get("model_path"),
        limit=500,
    )
    if model_dir:
        env["SKILLFORGE_QWEN36_35B_MODEL_DIR"] = model_dir
    openwebui_enabled = raw.get("openwebui_enabled")
    if openwebui_enabled is not None:
        env["SKILLFORGE_OPENWEBUI_ENABLED"] = "1" if bool(openwebui_enabled) else "0"
    openwebui_host = _clean_text(raw.get("openwebui_host"), limit=120)
    if openwebui_host:
        env["SKILLFORGE_OPENWEBUI_HOST"] = openwebui_host
    openwebui_public_host = _clean_text(raw.get("openwebui_public_host") or raw.get("openwebuiPublicHost"), limit=120)
    if openwebui_public_host:
        env["SKILLFORGE_OPENWEBUI_PUBLIC_HOST"] = openwebui_public_host
    openwebui_port = raw.get("openwebui_port")
    if openwebui_port not in (None, ""):
        try:
            port = int(openwebui_port)
        except (TypeError, ValueError):
            raise AppError("TRAINING_RUNTIME_CONFIG_INVALID", 422, {"detail": "openwebui_port must be an integer"})
        if port < 1 or port > 65535:
            raise AppError("TRAINING_RUNTIME_CONFIG_INVALID", 422, {"detail": "openwebui_port must be between 1 and 65535"})
        env["SKILLFORGE_OPENWEBUI_PORT"] = str(port)
    if not env:
        raise AppError("TRAINING_RUNTIME_CONFIG_INVALID", 422, {"detail": "no runtime config provided"})
    model_path = env.get("SKILLFORGE_QWEN36_35B_MODEL_DIR")
    if model_path and not model_path.startswith(("/", "file://")):
        raise AppError("TRAINING_RUNTIME_CONFIG_INVALID", 422, {"detail": "qwen36_35b_model_dir must be an absolute path"})
    return {"env": env}


def _normalize_training_model_test_request(payload: dict | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    model = _normalize_training_model_request(raw)
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise AppError("TRAINING_MODEL_INVALID", 422, {"detail": "prompt is required"})
    max_new_tokens = max(1, min(_safe_int(raw.get("max_new_tokens") or 512), 4096))
    timeout_seconds = max(30, min(_safe_int(raw.get("timeout_seconds") or 600), 1800))
    max_prompt_chars = max(100, min(_safe_int(raw.get("max_prompt_chars") or 6000), 20000))
    model.update({
        "prompt": prompt[:max_prompt_chars],
        "max_new_tokens": max_new_tokens,
        "timeout_seconds": timeout_seconds,
        "max_prompt_chars": max_prompt_chars,
        "base_model_only": bool(raw.get("base_model_only", True)),
    })
    return model


def _normalize_training_openwebui_base_model_request(payload: dict | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    model = _normalize_training_model_request({
        **raw,
        "profile": raw.get("profile") or TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE,
        "source": raw.get("source") or raw.get("model_source") or "internal",
    })
    default_model_id = f"skillforge-base-{model['profile']}"
    model_id = _openwebui_model_segment(
        raw.get("model_id") or raw.get("openwebui_model_id") or default_model_id,
        fallback=default_model_id,
        limit=180,
    )
    aliases: list[str] = []
    raw_aliases = raw.get("aliases") if isinstance(raw.get("aliases"), list) else []
    default_aliases = [
        model["profile"],
        f"skillforge-{model['profile']}",
    ]
    for item in [*raw_aliases, *default_aliases]:
        alias = _openwebui_model_segment(item, fallback="", limit=180)
        if alias and alias != model_id and alias not in aliases:
            aliases.append(alias)
        if len(aliases) >= 10:
            break
    runtime_profile = _clean_text(
        raw.get("runtime_profile") or raw.get("deployment_runtime_profile"),
        limit=80,
    )
    if not runtime_profile and model["profile"] == TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE:
        runtime_profile = TRAINING_QWEN36_DEPLOYMENT_RUNTIME_PROFILE
    display_name = _clean_text(raw.get("display_name"), limit=180) or f"SkillForge Base {model['profile']}"
    model.update({
        "openwebui_model_id": model_id,
        "openwebui_aliases": aliases,
        "runtime_profile": runtime_profile or TRAINING_DEFAULT_DEPLOYMENT_RUNTIME_PROFILE,
        "display_name": display_name,
    })
    return model


def _normalize_training_openwebui_chat_test_request(payload: dict | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    profile = str(raw.get("profile") or TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE).strip().lower()
    if profile not in TRAINING_MODEL_PROFILES_ALLOWED:
        raise AppError("TRAINING_MODEL_INVALID", 422, {"detail": "unsupported training model profile"})
    default_model_id = f"skillforge-base-{profile}"
    model_id = _openwebui_model_segment(
        raw.get("model_id") or raw.get("openwebui_model_id") or raw.get("model") or default_model_id,
        fallback=default_model_id,
        limit=180,
    )
    prompt = str(raw.get("prompt") or "用一句话回答：你是 SkillForge 上哪个 OpenWebUI 模型？").strip()
    if not prompt:
        raise AppError("TRAINING_MODEL_INVALID", 422, {"detail": "prompt is required"})
    max_tokens = max(1, min(_safe_int(raw.get("max_tokens") or raw.get("max_new_tokens") or 32), 4096))
    timeout_seconds = max(30, min(_safe_int(raw.get("timeout_seconds") or 600), 3600))
    return {
        "profile": profile,
        "model_id": model_id,
        "prompt": prompt[:20000],
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
    }


def _gateway_routing_payload(
    instance: OpenClawInstance | None,
    *,
    mode: str,
    scope: str | None = None,
    status: str = "assigned",
    reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": mode,
        "status": status,
        "selected_at": isoformat_bjt(now_bjt()),
    }
    if instance is not None:
        payload.update({
            "selected_gateway_id": instance.id,
            "selected_gateway_name": instance.name or instance.id,
            "selected_gateway_department": instance.department,
            "scope": scope or _training_gateway_scope(str(getattr(instance, "department", "") or ""), instance),
            "platform_fallback": False,
            "agent_contract": _training_agent_contract_summary(instance),
        })
    if scope:
        payload["scope"] = scope
        payload["platform_fallback"] = scope == "platform_fallback"
    if reason:
        payload["reason"] = reason
    return payload


def _with_gateway_routing(spec: dict[str, Any], routing: dict[str, Any]) -> dict[str, Any]:
    next_spec = dict(spec or {})
    next_spec["routing"] = _sanitize_training_result_value(routing)
    return next_spec


async def _select_training_gateway_for_job(
    db: AsyncSession,
    user: User,
    *,
    department: str,
    job_type: str,
    preferred_gateway_id: str | None = None,
) -> tuple[OpenClawInstance | None, dict[str, Any] | None]:
    preferred_id = _clean_text(preferred_gateway_id, limit=50)
    if preferred_id:
        instance = await db.get(OpenClawInstance, preferred_id)
        if instance is None:
            raise AppError("NOT_FOUND", 404, {"detail": "target training gateway not found"})
        if not getattr(instance, "is_active", True):
            raise AppError("PARAM_INVALID", 422, {"detail": "target training gateway is disabled"})
        if not _can_use_training_gateway_for_job(user, department, instance):
            raise AppError("FORBIDDEN", 403, {"detail": "no access to target training gateway"})
        if not _is_training_agent(instance):
            raise AppError("PARAM_INVALID", 422, {"detail": "target gateway is not marked as a training Agent"})
        support_error = _training_gateway_job_support_error(instance, job_type)
        if support_error:
            raise AppError("PARAM_INVALID", 422, support_error)
        scope = _training_gateway_scope(department, instance)
        return instance, _gateway_routing_payload(instance, mode="manual", scope=scope)

    stmt = select(OpenClawInstance).where(OpenClawInstance.is_active.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    candidates: list[tuple[tuple[Any, ...], OpenClawInstance, str]] = []
    for instance in rows:
        if not _can_use_training_gateway_for_job(user, department, instance):
            continue
        if not _training_gateway_supports_job_type(instance, job_type):
            continue
        scope = _training_gateway_scope(department, instance)
        if scope not in {"department", "platform_fallback"} and not _can_view_all_training_resources(user):
            continue
        resource = _training_resource_from_instance(instance, online=bridge_registry.is_online(instance.id))
        route_policy = resource.get("route_policy") if isinstance(resource.get("route_policy"), dict) else {}
        gb10_rank = 0 if route_policy.get("gb10_training_pool") is True else 1
        seed_rank = 0 if str(instance.id or "") in TRAINING_PRIMARY_GATEWAY_IDS else 1
        scope_rank = 0 if scope == "department" else (1 if scope == "platform_fallback" else 2)
        purpose_rank = 0 if _agent_purpose(instance) in {"training", "mixed"} else 1
        candidates.append((
            (
                0 if resource["online"] else 1,
                gb10_rank,
                seed_rank,
                scope_rank,
                purpose_rank,
                resource["busy_gpu_count"],
                -resource["idle_gpu_count"],
                -resource["vram_free_gb"],
                str(instance.name or instance.id),
            ),
            instance,
            scope,
        ))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[0])
    selected = candidates[0][1]
    scope = candidates[0][2]
    return selected, _gateway_routing_payload(selected, mode="auto", scope=scope)


async def _assign_training_gateway_if_missing(
    db: AsyncSession,
    user: User,
    job: TrainingJob,
    *,
    reason: str,
    prefer_primary: bool = False,
) -> dict[str, Any] | None:
    previous_gateway_id = _clean_text(job.target_gateway_id, limit=50)
    reroute_reason: str | None = None
    if previous_gateway_id:
        existing = await db.get(OpenClawInstance, previous_gateway_id)
        if existing is None:
            reroute_reason = "target_gateway_not_found"
        elif not getattr(existing, "is_active", True):
            reroute_reason = "target_gateway_disabled"
        elif not _can_use_training_gateway_for_job(user, job.department, existing):
            reroute_reason = "target_gateway_forbidden"
        elif not _is_training_agent(existing):
            reroute_reason = "target_gateway_not_training_agent"
        else:
            support_error = _training_gateway_job_support_error(existing, job.job_type)
            if support_error:
                reroute_reason = _clean_text(support_error.get("detail"), limit=120) or "target_gateway_unsupported"
        if reroute_reason is None and prefer_primary and previous_gateway_id not in TRAINING_PRIMARY_GATEWAY_IDS:
            reroute_reason = "prefer_primary_training_gateway"
        if reroute_reason is None:
            return None
    preferred_gateway_id = None
    bridge_dataset_config = _training_dataset_bridge_config(job)
    if bridge_dataset_config is not None:
        bridge_preferred_gateway_id = _clean_text(bridge_dataset_config.get("target_gateway_id"), limit=50)
        if not (
            prefer_primary
            and reroute_reason == "prefer_primary_training_gateway"
            and bridge_preferred_gateway_id not in TRAINING_PRIMARY_GATEWAY_IDS
        ):
            preferred_gateway_id = bridge_preferred_gateway_id
    selected, routing = await _select_training_gateway_for_job(
        db,
        user,
        department=job.department,
        job_type=job.job_type,
        preferred_gateway_id=preferred_gateway_id,
    )
    if selected is None or routing is None:
        spec = job.spec_json if isinstance(job.spec_json, dict) else {}
        job.spec_json = _with_gateway_routing(
            spec,
            _gateway_routing_payload(None, mode="auto", status="missing_gateway", reason=reason),
        )
        return None
    if selected.id == previous_gateway_id and reroute_reason == "prefer_primary_training_gateway":
        return None
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    job.target_gateway_id = selected.id
    job.spec_json = _with_gateway_routing(
        spec,
        {
            **routing,
            "reason": reason,
            "previous_gateway_id": previous_gateway_id,
            "reroute_reason": reroute_reason,
        },
    )
    return routing


async def _get_training_gateway(db: AsyncSession, user: User, job: TrainingJob) -> OpenClawInstance:
    instance = await _get_training_gateway_instance(db, user, job)
    if not _is_training_agent(instance):
        raise AppError("PARAM_INVALID", 422, {"detail": "target gateway is not marked as a training Agent"})
    support_error = _training_gateway_job_support_error(instance, job.job_type)
    if support_error:
        raise AppError("PARAM_INVALID", 422, support_error)
    return instance


async def _get_training_gateway_instance(db: AsyncSession, user: User, job: TrainingJob) -> OpenClawInstance:
    gateway_id = _clean_text(job.target_gateway_id, limit=50, required=True) or ""
    instance = await db.get(OpenClawInstance, gateway_id)
    if not instance:
        raise AppError("NOT_FOUND", 404, {"detail": "training gateway not found"})
    if not _can_use_training_gateway_for_job(user, job.department, instance):
        raise AppError("FORBIDDEN", 403, {"detail": "no access to target training gateway"})
    return instance


def _validate_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AppError("PARAM_INVALID", 422, {"detail": "training result must be an object"})
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_TRAINING_RESULT_BYTES:
        raise AppError("PARAM_INVALID", 422, {"detail": "training result too large"})
    status = str(payload.get("status") or "").strip().lower()
    if status not in TRAINING_GATEWAY_RESULT_STATUSES:
        raise AppError("PARAM_INVALID", 422, {"detail": "unsupported training job status"})
    metrics = payload.get("metrics") or {}
    if not isinstance(metrics, dict):
        raise AppError("PARAM_INVALID", 422, {"detail": "metrics must be an object"})
    artifacts = payload.get("artifacts") or []
    if not isinstance(artifacts, list):
        raise AppError("PARAM_INVALID", 422, {"detail": "artifacts must be a list"})
    progress = max(0.0, min(_safe_number(payload.get("progress")), 1.0))
    if status == "completed" and payload.get("progress") is None:
        progress = 1.0
    return {
        "job_id": _clean_text(payload.get("job_id"), limit=50),
        "gateway_id": _clean_text(payload.get("gateway_id"), limit=50),
        "worker_id": _clean_text(payload.get("worker_id"), limit=100),
        "status": status,
        "progress": progress,
        "metrics": _sanitize_training_result_value(metrics),
        "artifacts": _sanitize_training_result_value(artifacts),
        "logs_url": _clean_text(redact_secret_text(payload.get("logs_url"), limit=500), limit=500),
        "error": _clean_text(redact_secret_text(payload.get("error") or payload.get("error_message"), limit=4000), limit=4000),
        "failure_stage": _clean_text(payload.get("failure_stage"), limit=30),
    }


def _result_artifact_value(artifact: Any, *keys: str) -> str:
    if not isinstance(artifact, dict):
        return ""
    for key in keys:
        value = str(artifact.get(key) or "").strip()
        if value:
            return value
    return ""


def _validate_completed_result_contract(job: TrainingJob, result: dict[str, Any]) -> None:
    if result.get("status") != "completed" or job.job_type not in TRAINING_DEPLOYABLE_JOB_TYPES:
        return
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
    usable_artifacts = [
        item
        for item in artifacts
        if _result_artifact_value(item, "uri", "url", "artifact_uri", "path")
        and _result_artifact_value(item, "sha256", "hash")
    ]
    if usable_artifacts:
        return
    raise AppError(
        "TRAINING_RESULT_ARTIFACT_REQUIRED",
        422,
        {
            "detail": "completed deployable training result requires artifact uri and sha256",
            "required_artifact_fields": ["uri", "sha256"],
            "job_type": job.job_type,
        },
    )


def _validate_gateway_result_transition(job: TrainingJob, result_status: str) -> None:
    allowed = TRAINING_GATEWAY_STATUS_TRANSITIONS.get(str(job.status or ""))
    if not allowed or result_status not in allowed:
        raise AppError(
            "INVALID_STATUS",
            400,
            {
                "detail": "training gateway result status transition is not allowed",
                "current_status": job.status,
                "result_status": result_status,
            },
        )


def _sanitize_log_line(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        message = value.get("message") or value.get("text") or value.get("line") or json.dumps(value, ensure_ascii=False)
        return {
            "ts": redact_secret_text(value.get("ts") or value.get("time") or "", limit=80),
            "type": redact_secret_text(value.get("type") or value.get("level") or "log", limit=40),
            "message": redact_secret_text(message, limit=1000),
        }
    return {
        "ts": "",
        "type": "log",
        "message": redact_secret_text(value, limit=1000),
    }


def _safe_artifact_uri_display(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        text = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return redact_secret_text(text, limit=500)


def _artifact_name_from_uri(value: str) -> str:
    parsed = urlparse(value or "")
    path = parsed.path or value or ""
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return name[:160] if name else ""


def _serialize_training_artifact(task: TrainingJobTask, artifact: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(artifact, dict):
        return None
    safe_artifact = _sanitize_training_result_value(artifact)
    if not isinstance(safe_artifact, dict):
        return None
    raw_uri = artifact.get("uri") or artifact.get("url") or artifact.get("artifact_uri") or artifact.get("path") or ""
    uri = _safe_artifact_uri_display(raw_uri)
    artifact_type = _clean_text(
        safe_artifact.get("type") or safe_artifact.get("artifact_type") or "artifact",
        limit=80,
    ) or "artifact"
    name = _clean_text(
        safe_artifact.get("name") or safe_artifact.get("filename") or _artifact_name_from_uri(uri) or artifact_type,
        limit=160,
    ) or artifact_type
    sha256 = _clean_text(safe_artifact.get("sha256") or safe_artifact.get("hash"), limit=128)
    size_bytes = _safe_int(safe_artifact.get("size_bytes") or safe_artifact.get("bytes"))
    warnings = []
    if not sha256:
        warnings.append("missing_sha256")
    if raw_uri and str(raw_uri).strip() != uri:
        warnings.append("uri_query_stripped")
    return {
        "id": f"{task.id}:{index}",
        "job_id": task.job_id,
        "task_id": task.id,
        "gateway_id": task.gateway_id,
        "worker_id": task.worker_id,
        "type": artifact_type,
        "name": name,
        "uri": uri,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "downloadable": False,
        "download_url": None,
        "warnings": warnings,
    }


def _latest_gateway_result(tasks: list[TrainingJobTask]) -> dict[str, Any] | None:
    for task in reversed(tasks):
        metrics = task.metrics_json if isinstance(task.metrics_json, dict) else {}
        gateway_result = metrics.get("gateway_result")
        if isinstance(gateway_result, dict):
            return gateway_result
    return None


def _collect_training_artifacts(tasks: list[TrainingJobTask]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for task in tasks:
        metrics = task.metrics_json if isinstance(task.metrics_json, dict) else {}
        gateway_result = metrics.get("gateway_result") if isinstance(metrics.get("gateway_result"), dict) else {}
        raw_artifacts = gateway_result.get("artifacts") if isinstance(gateway_result.get("artifacts"), list) else []
        for index, artifact in enumerate(raw_artifacts[:MAX_TRAINING_RESULT_LIST_ITEMS]):
            item = _serialize_training_artifact(task, artifact, index)
            if item is not None:
                artifacts.append(item)
    return artifacts


def _coerce_metric_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number != float("inf") and number != float("-inf") else None


def _evaluate_training_gate(
    job: TrainingJob,
    gateway_result: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> tuple[bool, list[dict[str, Any]]]:
    metrics = gateway_result.get("metrics") if isinstance(gateway_result.get("metrics"), dict) else {}
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    gate = spec.get("eval_gate") if isinstance(spec.get("eval_gate"), dict) else {}
    checks: list[dict[str, Any]] = []
    eval_requested = _coerce_metric_number(metrics.get("eval_requested_samples"))
    eval_evaluated = _coerce_metric_number(metrics.get("eval_evaluated_samples"))
    eval_sample_count = _coerce_metric_number(metrics.get("eval_samples"))
    explicit_eval_sample_counts = [
        value
        for name, value in (
            ("eval_evaluated_samples", eval_evaluated),
            ("eval_requested_samples", eval_requested),
            ("eval_samples", eval_sample_count),
        )
        if name in metrics and value is not None
    ]
    has_eval_samples = any(
        value is not None and value > 0
        for value in (eval_evaluated, eval_requested, eval_sample_count)
    )
    explicit_no_eval_samples = bool(explicit_eval_sample_counts) and not has_eval_samples

    for raw_key, raw_threshold in gate.items():
        key = str(raw_key or "").strip()
        if key in {"require_artifact_sha256", "require_artifact"}:
            continue
        if key == "manual_review_required":
            checks.append({
                "name": key,
                "metric": "deployment.review",
                "operator": "requires_review",
                "expected": bool(raw_threshold),
                "actual": True,
                "passed": True,
            })
            continue
        if key.startswith("min_"):
            metric_name = key[4:]
            expected = _coerce_metric_number(raw_threshold)
            actual = _coerce_metric_number(metrics.get(metric_name))
            if metric_name == "win_rate" and explicit_no_eval_samples:
                checks.append({
                    "name": key,
                    "metric": metric_name,
                    "operator": "skipped_no_eval_samples",
                    "expected": expected,
                    "actual": actual,
                    "passed": True,
                    "skipped": True,
                    "reason": "no_eval_samples",
                })
                continue
            passed = expected is not None and actual is not None and actual >= expected
            checks.append({
                "name": key,
                "metric": metric_name,
                "operator": ">=",
                "expected": expected,
                "actual": actual,
                "passed": passed,
            })
        elif key.startswith("max_"):
            metric_name = key[4:]
            expected = _coerce_metric_number(raw_threshold)
            actual = _coerce_metric_number(metrics.get(metric_name))
            passed = expected is not None and actual is not None and actual <= expected
            checks.append({
                "name": key,
                "metric": metric_name,
                "operator": "<=",
                "expected": expected,
                "actual": actual,
                "passed": passed,
            })
        else:
            checks.append({
                "name": key,
                "metric": key,
                "operator": "unsupported",
                "expected": _sanitize_training_result_value(raw_threshold),
                "actual": None,
                "passed": False,
            })

    requires_artifact_hash = bool(gate.get("require_artifact_sha256", job.job_type in {"lora", "qlora", "merge"}))
    if requires_artifact_hash:
        checks.append({
            "name": "artifact_sha256",
            "metric": "artifacts.sha256",
            "operator": "exists",
            "expected": True,
            "actual": any(bool(item.get("sha256")) for item in artifacts),
            "passed": any(bool(item.get("sha256")) for item in artifacts),
        })
    requires_artifact_uri = bool(gate.get("require_artifact_uri", job.job_type in {"lora", "qlora", "merge"}))
    if requires_artifact_uri:
        checks.append({
            "name": "artifact_uri",
            "metric": "artifacts.uri",
            "operator": "exists",
            "expected": True,
            "actual": any(bool(str(item.get("uri") or "").strip()) for item in artifacts),
            "passed": any(bool(str(item.get("uri") or "").strip()) for item in artifacts),
        })

    return all(bool(item.get("passed")) for item in checks), checks


def _latest_training_evaluation_task(tasks: list[TrainingJobTask]) -> TrainingJobTask | None:
    for task in reversed(tasks):
        metrics = task.metrics_json if isinstance(task.metrics_json, dict) else {}
        if metrics.get("op") == "training.evaluate":
            return task
    return None


def _normalize_target_skill_ids(value: Any, fallback: str | None) -> list[str]:
    raw_items = value if isinstance(value, list) else []
    if not raw_items and fallback:
        raw_items = [fallback]
    target_ids: list[str] = []
    for item in raw_items[:20]:
        cleaned = _clean_text(item, limit=50)
        if cleaned and cleaned not in target_ids:
            target_ids.append(cleaned)
    if not target_ids:
        raise AppError("PARAM_INVALID", 422, {"detail": "deployment request requires target_skill_ids"})
    return target_ids


def _deployment_target_from_mapping(source: dict[str, Any]) -> str | None:
    return _clean_text(
        _nested_value(
            source,
            "deployment_target_gateway_id",
            "deployment.deployment_target_gateway_id",
            "deployment.target_gateway_id",
            "deployment.gateway_id",
            "inference.target_gateway_id",
            "target_gateway_id",
        ),
        limit=50,
    )


def _deployment_runtime_profile_from_mapping(source: dict[str, Any]) -> str | None:
    return _clean_text(
        _nested_value(
            source,
            "deployment_runtime_profile",
            "deployment.deployment_runtime_profile",
            "deployment.runtime_profile",
            "inference.runtime_profile",
        ),
        limit=80,
    )


def _training_model_profile_from_mapping(source: dict[str, Any]) -> str | None:
    profile = str(
        _nested_value(
            source,
            "model.profile",
            "model_profile",
            "base_model_profile",
            "deployment.model_profile",
        )
        or ""
    ).strip().lower().replace("_", "-")
    return profile if profile in TRAINING_MODEL_PROFILES_ALLOWED else None


def _training_model_profile_from_family(model_family: str | None) -> str | None:
    family = str(model_family or "").lower()
    if "qwen3.6" in family and "35b" in family:
        return TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE
    if "qwen3.5" in family and "4b" in family:
        return TRAINING_QWEN35_4B_PROFILE
    return None


def _deployment_runtime_profile_for_model_profile(profile: str | None) -> str | None:
    if profile == TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE:
        return TRAINING_QWEN36_DEPLOYMENT_RUNTIME_PROFILE
    if profile == TRAINING_QWEN35_4B_PROFILE:
        return TRAINING_DEFAULT_DEPLOYMENT_RUNTIME_PROFILE
    return None


def _deployment_gateway_supports_inference(instance: OpenClawInstance) -> bool:
    if _training_gateway_reserved_for(instance):
        return False
    cap = _safe_capabilities(getattr(instance, "bridge_capabilities_json", None))
    ops = {str(item or "").strip() for item in (cap.get("ops") or []) if str(item or "").strip()}
    return "training.inference" in ops


async def _select_default_deployment_target_gateway_id(db: AsyncSession) -> str | None:
    rows = (
        await db.execute(
            select(OpenClawInstance)
            .where(OpenClawInstance.is_active.is_(True))
        )
    ).scalars().all()
    candidates: list[tuple[tuple[Any, ...], OpenClawInstance]] = []
    for instance in rows:
        if not _deployment_gateway_supports_inference(instance):
            continue
        cap = _safe_capabilities(getattr(instance, "bridge_capabilities_json", None))
        if not _is_mac_deployment_gateway(instance, cap):
            continue
        online = bridge_registry.is_online(instance.id)
        m3_rank = 0 if _is_m3_deployment_gateway(instance, cap) else 1
        mac_rank = 0 if _is_mac_deployment_gateway(instance, cap) else 1
        seed_rank = 0 if str(instance.id or "") == TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID else 1
        candidates.append((
            (
                0 if online else 1,
                m3_rank,
                mac_rank,
                seed_rank,
                str(instance.name or instance.id),
            ),
            instance,
        ))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return str(candidates[0][1].id)


async def _resolve_deployment_target_gateway_id(
    db: AsyncSession,
    *,
    job: TrainingJob,
    payload: dict[str, Any],
) -> str | None:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    requested_id = _deployment_target_from_mapping(payload) or _deployment_target_from_mapping(spec)
    if requested_id:
        instance = await db.get(OpenClawInstance, requested_id)
        if instance is None or getattr(instance, "is_active", True) is False:
            raise AppError("PARAM_INVALID", 422, {"detail": "deployment target gateway not found or disabled"})
        return requested_id

    selected_id = await _select_default_deployment_target_gateway_id(db)
    if selected_id:
        return selected_id
    return _clean_text(job.target_gateway_id, limit=50)


def _resolve_deployment_runtime_profile(
    *,
    job: TrainingJob,
    payload: dict[str, Any],
    deployment_target_gateway_id: str | None,
) -> str | None:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    explicit = _deployment_runtime_profile_from_mapping(payload) or _deployment_runtime_profile_from_mapping(spec)
    if explicit:
        return explicit
    if deployment_target_gateway_id == TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID or str(deployment_target_gateway_id or "").startswith("platform-mac-"):
        profile = (
            _training_model_profile_from_mapping(payload)
            or _training_model_profile_from_mapping(spec)
            or _training_model_profile_from_family(str(payload.get("model_family") or ""))
        )
        runtime_profile = _deployment_runtime_profile_for_model_profile(profile)
        if runtime_profile:
            return runtime_profile
        return TRAINING_DEFAULT_DEPLOYMENT_RUNTIME_PROFILE
    return None


def _deployment_target_gateway_id(deployment: TrainingModelDeployment, job: TrainingJob | None = None) -> str | None:
    gateway_id = _clean_text(getattr(deployment, "deployment_target_gateway_id", None), limit=50)
    if gateway_id:
        return gateway_id
    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    gateway_id = _clean_text(
        artifact_ref.get("deployment_target_gateway_id") or artifact_ref.get("target_gateway_id"),
        limit=50,
    )
    if gateway_id:
        return gateway_id
    if job is not None:
        return _clean_text(getattr(job, "target_gateway_id", None), limit=50)
    return None


def _auto_deployment_request_payload(job: TrainingJob) -> dict[str, Any] | None:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    governance = spec.get("governance") if isinstance(spec.get("governance"), dict) else {}
    if governance.get("no_auto_deploy") is True:
        return None
    deployment = spec.get("deployment") if isinstance(spec.get("deployment"), dict) else {}
    enabled = any(
        item is True
        for item in (
            deployment.get("auto_request"),
            deployment.get("auto_request_deployment"),
            spec.get("auto_deploy_request"),
        )
    )
    if not enabled:
        return None

    target_skill_ids = deployment.get("target_skill_ids") or spec.get("target_skill_ids")
    if isinstance(target_skill_ids, str):
        target_skill_ids = [target_skill_ids]
    model_family = (
        deployment.get("model_family")
        or spec.get("model_family")
        or spec.get("model_name")
        or spec.get("output_model_name")
    )
    rollout_percent = deployment.get("rollout_percent")
    if rollout_percent is None:
        rollout_percent = spec.get("rollout_percent", 10)
    auto_canary = any(
        item is True
        for item in (
            deployment.get("auto_canary"),
            deployment.get("auto_approve_canary"),
            spec.get("auto_canary"),
            spec.get("auto_approve_canary"),
        )
    )
    auto_active = any(
        item is True
        for item in (
            deployment.get("auto_active"),
            deployment.get("auto_approve_active"),
            deployment.get("auto_activate"),
            spec.get("auto_active"),
            spec.get("auto_approve_active"),
            spec.get("auto_activate"),
        )
    )
    if governance.get("no_auto_approval") is True:
        auto_canary = False
        auto_active = False
    if auto_active and (rollout_percent is None or _safe_int(rollout_percent) < 100):
        rollout_percent = 100
    return {
        "target_skill_ids": target_skill_ids,
        "model_family": model_family,
        "rollout_percent": rollout_percent,
        "deployment_target_gateway_id": _deployment_target_from_mapping(deployment) or _deployment_target_from_mapping(spec),
        "deployment_runtime_profile": _deployment_runtime_profile_from_mapping(deployment) or _deployment_runtime_profile_from_mapping(spec),
        "auto_approve_canary": auto_canary,
        "auto_active": auto_active,
        "reason": (
            deployment.get("reason")
            or deployment.get("request_reason")
            or "评估通过后自动提交部署审批"
        ),
    }


async def _create_training_deployment_request_uncommitted(
    db: AsyncSession,
    user: User,
    job: TrainingJob,
    payload: dict[str, Any],
    tasks: list[TrainingJobTask],
    *,
    auto_requested: bool = False,
    allow_existing: bool = False,
) -> tuple[TrainingModelDeployment, bool]:
    evaluation_task = _latest_training_evaluation_task(tasks)
    evaluation_metrics = evaluation_task.metrics_json if evaluation_task and isinstance(evaluation_task.metrics_json, dict) else {}
    if not evaluation_task or evaluation_task.status != "completed" or evaluation_metrics.get("passed") is not True:
        raise AppError("INVALID_STATUS", 400, {"detail": "training job must pass evaluation before deployment request"})

    artifacts = _collect_training_artifacts(tasks)
    artifact_ref = next(
        (
            item
            for item in artifacts
            if item.get("sha256") and str(item.get("uri") or "").strip()
        ),
        None,
    )
    if not artifact_ref:
        raise AppError("INVALID_STATUS", 400, {"detail": "deployment request requires an artifact with sha256 and uri"})

    existing = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.job_id == job.id)
            .where(TrainingModelDeployment.status.in_(TRAINING_DEPLOYMENT_OPEN_STATUSES))
            .order_by(TrainingModelDeployment.created_at.desc())
        )
    ).scalars().first()
    if existing:
        if allow_existing:
            return existing, False
        raise AppError(
            "TRAINING_DEPLOYMENT_EXISTS",
            409,
            {"detail": "training job already has an open deployment request", "deployment_id": existing.id},
        )

    target_skill_ids = _normalize_target_skill_ids(payload.get("target_skill_ids"), job.target_skill_id)
    model_family = _clean_text(payload.get("model_family") or target_skill_ids[0], limit=100, required=True) or target_skill_ids[0]
    rollout_percent = max(0, min(_safe_int(payload.get("rollout_percent")), 100))
    request_reason = _clean_text(payload.get("reason") or payload.get("request_reason"), limit=1000)
    deployment_target_gateway_id = await _resolve_deployment_target_gateway_id(db, job=job, payload=payload)
    deployment_runtime_profile = _resolve_deployment_runtime_profile(
        job=job,
        payload=payload,
        deployment_target_gateway_id=deployment_target_gateway_id,
    )
    now = now_bjt()
    deployment = TrainingModelDeployment(
        id=_training_deployment_id(),
        job_id=job.id,
        department=job.department,
        model_family=model_family,
        artifact_id=str(artifact_ref.get("id") or artifact_ref.get("sha256") or "")[:120],
        artifact_ref_json=artifact_ref,
        eval_task_id=evaluation_task.id,
        target_skill_ids_json=target_skill_ids,
        deployment_target_gateway_id=deployment_target_gateway_id,
        deployment_runtime_profile=deployment_runtime_profile,
        status="awaiting_review",
        rollout_percent=rollout_percent,
        requested_by=str(getattr(user, "id", "") or ""),
        request_reason=request_reason,
        created_at=now,
        updated_at=now,
    )
    db.add(deployment)
    await db.flush()
    await _record_training_deployment_audit(
        db,
        user,
        "training_deployment.request",
        deployment,
        {
            "artifact_id": deployment.artifact_id,
            "eval_task_id": evaluation_task.id,
            "rollout_percent": rollout_percent,
            "deployment_target_gateway_id": deployment_target_gateway_id,
            "deployment_runtime_profile": deployment_runtime_profile,
            "auto_requested": auto_requested,
        },
    )
    job.updated_at = now
    try:
        from app.learning.service import capture_model_deployment

        await capture_model_deployment(db, deployment)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获模型部署请求失败 deployment={} err={}", deployment.id, exc)
    return deployment, True


async def _auto_approve_training_deployment_canary_uncommitted(
    db: AsyncSession,
    user: User,
    deployment: TrainingModelDeployment,
    job: TrainingJob,
) -> dict[str, Any]:
    if deployment.status != "awaiting_review":
        raise AppError("INVALID_STATUS", 400, {"detail": "only awaiting_review deployments can enter auto canary"})
    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    if not artifact_ref.get("sha256"):
        raise AppError("INVALID_STATUS", 400, {"detail": "deployment approval requires artifact sha256"})
    runtime_status = await _require_deployment_inference_ready(db, deployment, job)

    now = now_bjt()
    previous_active = await _active_deployments_for_target(db, deployment)
    previous_ids = [item.id for item in previous_active]
    deployment.rollback_to = previous_ids[0] if previous_ids else deployment.rollback_to
    deployment.approved_by = str(getattr(user, "id", "") or "")
    deployment.approved_at = now
    deployment.status = "canary"
    if int(deployment.rollout_percent or 0) <= 0 or int(deployment.rollout_percent or 0) >= 100:
        deployment.rollout_percent = 10
    deployment.updated_at = now
    await _record_training_deployment_audit(
        db,
        user,
        "training_deployment.auto_approve_canary",
        deployment,
        {
            "previous_active_deployment_ids": previous_ids,
            "rollout_percent": int(deployment.rollout_percent or 0),
            "runtime_status": runtime_status,
        },
    )
    job.updated_at = now
    try:
        from app.learning.service import capture_model_deployment

        await capture_model_deployment(db, deployment, runtime_status=runtime_status)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获模型自动灰度失败 deployment={} err={}", deployment.id, exc)
    return runtime_status


async def _auto_activate_training_deployment_uncommitted(
    db: AsyncSession,
    user: User,
    deployment: TrainingModelDeployment,
    job: TrainingJob,
) -> dict[str, Any]:
    if deployment.status not in {"awaiting_review", "canary"}:
        raise AppError("INVALID_STATUS", 400, {"detail": "only awaiting_review or canary deployments can enter auto active"})
    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    if not artifact_ref.get("sha256"):
        raise AppError("INVALID_STATUS", 400, {"detail": "deployment approval requires artifact sha256"})
    runtime_status = await _require_deployment_inference_ready(db, deployment, job)

    now = now_bjt()
    previous_active = await _active_deployments_for_target(db, deployment)
    previous_ids = [item.id for item in previous_active]
    if previous_ids and not deployment.rollback_to:
        deployment.rollback_to = previous_ids[0]
    for item in previous_active:
        item.status = "rolled_back"
        item.rollback_to = deployment.id
        item.updated_at = now
    if deployment.status == "awaiting_review" or not deployment.approved_by:
        deployment.approved_by = str(getattr(user, "id", "") or "")
        deployment.approved_at = now
    deployment.status = "active"
    deployment.rollout_percent = 100
    deployment.activated_at = now
    deployment.updated_at = now
    await _record_training_deployment_audit(
        db,
        user,
        "training_deployment.auto_activate",
        deployment,
        {
            "previous_active_deployment_ids": previous_ids,
            "rollout_percent": 100,
            "runtime_status": runtime_status,
        },
    )
    job.updated_at = now
    try:
        from app.learning.service import capture_model_deployment

        await capture_model_deployment(db, deployment, runtime_status=runtime_status)
        for item in previous_active:
            await capture_model_deployment(db, item)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获模型自动激活失败 deployment={} err={}", deployment.id, exc)
    return runtime_status


def _training_automation_user(job: TrainingJob) -> Any:
    return SimpleNamespace(
        id="training_automation",
        username="training_automation",
        name="训练自动化",
        role="system_admin",
        department=job.department,
        can_view_all=True,
        is_active=True,
        state="active",
    )


def _auto_deployment_failure_payload(exc: AppError) -> dict[str, Any]:
    detail_payload = exc.detail if isinstance(exc.detail, dict) else {}
    payload = {
        "status": "failed",
        "error_code": exc.code,
        "detail": _sanitize_training_result_value(
            detail_payload.get("detail") if isinstance(detail_payload, dict) else exc.detail
        ),
    }
    if isinstance(detail_payload, dict) and isinstance(detail_payload.get("runtime_status"), dict):
        payload["runtime_status"] = _sanitize_training_result_value(detail_payload["runtime_status"])
    return payload


async def _retry_auto_deployment_for_existing_evaluation_uncommitted(
    db: AsyncSession,
    user: User,
    job: TrainingJob,
    tasks: list[TrainingJobTask],
    evaluation_task: TrainingJobTask,
    *,
    trigger: str,
) -> dict[str, Any] | None:
    metrics = evaluation_task.metrics_json if isinstance(evaluation_task.metrics_json, dict) else {}
    if metrics.get("passed") is not True:
        return None
    auto_payload = _auto_deployment_request_payload(job)
    if auto_payload is None:
        return None
    previous_request = metrics.get("auto_deployment_request") if isinstance(metrics.get("auto_deployment_request"), dict) else {}
    if previous_request.get("status") in {"active", "canary"} or previous_request.get("deployment_status") in {"active", "canary"}:
        return None
    if previous_request.get("deployment_id") and previous_request.get("status") in {"created", "existing"}:
        return None

    now = now_bjt()
    try:
        deployment, created = await _create_training_deployment_request_uncommitted(
            db,
            user,
            job,
            auto_payload,
            tasks,
            auto_requested=True,
            allow_existing=True,
        )
        auto_deployment_request = {
            "status": "created" if created else "existing",
            "deployment_id": deployment.id,
            "deployment_status": deployment.status,
            "rollout_percent": int(deployment.rollout_percent or 0),
            "review_required": deployment.status == "awaiting_review",
            "retry": True,
        }
        if deployment.deployment_target_gateway_id and deployment.deployment_target_gateway_id != job.target_gateway_id:
            auto_deployment_request["deployment_target_gateway_id"] = deployment.deployment_target_gateway_id
        if auto_payload.get("auto_active") is True and deployment.status in {"awaiting_review", "canary"}:
            runtime_status = await _auto_activate_training_deployment_uncommitted(
                db,
                user,
                deployment,
                job,
            )
            auto_deployment_request = {
                **auto_deployment_request,
                "status": "active",
                "deployment_status": deployment.status,
                "rollout_percent": int(deployment.rollout_percent or 0),
                "review_required": False,
                "runtime_status": runtime_status,
                "schedule_refresh_pending": True,
            }
        elif auto_payload.get("auto_approve_canary") is True and deployment.status == "awaiting_review":
            runtime_status = await _auto_approve_training_deployment_canary_uncommitted(
                db,
                user,
                deployment,
                job,
            )
            auto_deployment_request = {
                **auto_deployment_request,
                "status": "canary",
                "deployment_status": deployment.status,
                "rollout_percent": int(deployment.rollout_percent or 0),
                "review_required": False,
                "runtime_status": runtime_status,
            }
    except AppError as exc:
        auto_deployment_request = {
            **_auto_deployment_failure_payload(exc),
            "retry": True,
        }

    evaluation_task.metrics_json = {
        **metrics,
        "auto_deployment_request": auto_deployment_request,
        "auto_deployment_retry": {
            "trigger": trigger,
            "retried_at": isoformat_bjt(now),
        },
    }
    job.updated_at = now
    await _record_training_audit(
        db,
        user,
        "training_job.auto_deployment_retry",
        job,
        {
            "evaluation_task_id": evaluation_task.id,
            "auto_deployment_request": auto_deployment_request,
            "trigger": trigger,
        },
    )
    return {
        "status": "existing",
        "evaluation_task_id": evaluation_task.id,
        "passed": True,
        "auto_deployment_request": auto_deployment_request,
    }


def _should_auto_evaluate_after_gateway_result(job: TrainingJob) -> bool:
    if job.status != "completed":
        return False
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    automation = spec.get("automation") if isinstance(spec.get("automation"), dict) else {}
    explicit = automation.get("auto_evaluate_on_result", spec.get("auto_evaluate_on_result"))
    if explicit is False:
        return False
    if explicit is True:
        return True
    return _auto_deployment_request_payload(job) is not None


async def _evaluate_training_job_uncommitted(
    db: AsyncSession,
    user: User,
    job: TrainingJob,
    tasks: list[TrainingJobTask],
    *,
    trigger: str,
) -> dict[str, Any]:
    existing_evaluation = _latest_training_evaluation_task(tasks)
    if existing_evaluation is not None and existing_evaluation.status == "completed":
        metrics = existing_evaluation.metrics_json if isinstance(existing_evaluation.metrics_json, dict) else {}
        return {
            "status": "existing",
            "evaluation_task_id": existing_evaluation.id,
            "passed": metrics.get("passed"),
            "auto_deployment_request": metrics.get("auto_deployment_request"),
        }

    gateway_result = _latest_gateway_result(tasks)
    if not gateway_result:
        raise AppError("INVALID_STATUS", 400, {"detail": "training job has no completed gateway result"})

    artifacts = _collect_training_artifacts(tasks)
    passed, checks = _evaluate_training_gate(job, gateway_result, artifacts)
    metrics = gateway_result.get("metrics") if isinstance(gateway_result.get("metrics"), dict) else {}
    now = now_bjt()
    failed_checks = [str(item.get("name") or "check") for item in checks if not bool(item.get("passed"))]
    evaluation_task = TrainingJobTask(
        job_id=job.id,
        gateway_id=job.target_gateway_id,
        status="completed" if passed else "failed",
        progress=1,
        metrics_json={
            "op": "training.evaluate",
            "passed": passed,
            "checks": checks,
            "metrics": _sanitize_training_result_value(metrics),
            "artifacts_total": len(artifacts),
            "evaluated_at": isoformat_bjt(now),
            "trigger": trigger,
        },
        error_message=None if passed else f"evaluation gate failed: {', '.join(failed_checks)}",
    )
    db.add(evaluation_task)
    job.status = "completed" if passed else "failed"
    job.failure_stage = None if passed else "eval"
    job.updated_at = now
    auto_deployment_request: dict[str, Any] | None = None
    if passed:
        auto_payload = _auto_deployment_request_payload(job)
        if auto_payload is not None:
            await db.flush()
            try:
                deployment, created = await _create_training_deployment_request_uncommitted(
                    db,
                    user,
                    job,
                    auto_payload,
                    [*tasks, evaluation_task],
                    auto_requested=True,
                    allow_existing=True,
                )
                auto_deployment_request = {
                    "status": "created" if created else "existing",
                    "deployment_id": deployment.id,
                    "deployment_status": deployment.status,
                    "rollout_percent": int(deployment.rollout_percent or 0),
                    "review_required": deployment.status == "awaiting_review",
                }
                if deployment.deployment_target_gateway_id and deployment.deployment_target_gateway_id != job.target_gateway_id:
                    auto_deployment_request["deployment_target_gateway_id"] = deployment.deployment_target_gateway_id
                if auto_payload.get("auto_active") is True and deployment.status in {"awaiting_review", "canary"}:
                    runtime_status = await _auto_activate_training_deployment_uncommitted(
                        db,
                        user,
                        deployment,
                        job,
                    )
                    auto_deployment_request = {
                        **auto_deployment_request,
                        "status": "active",
                        "deployment_status": deployment.status,
                        "rollout_percent": int(deployment.rollout_percent or 0),
                        "review_required": False,
                        "runtime_status": runtime_status,
                        "schedule_refresh_pending": True,
                    }
                elif auto_payload.get("auto_approve_canary") is True and deployment.status == "awaiting_review":
                    runtime_status = await _auto_approve_training_deployment_canary_uncommitted(
                        db,
                        user,
                        deployment,
                        job,
                    )
                    auto_deployment_request = {
                        **auto_deployment_request,
                        "status": "canary",
                        "deployment_status": deployment.status,
                        "rollout_percent": int(deployment.rollout_percent or 0),
                        "review_required": False,
                        "runtime_status": runtime_status,
                    }
            except AppError as exc:
                auto_deployment_request = _auto_deployment_failure_payload(exc)
            evaluation_task.metrics_json = {
                **(evaluation_task.metrics_json or {}),
                "auto_deployment_request": auto_deployment_request,
            }
    await _record_training_audit(
        db,
        user,
        "training_job.evaluate",
        job,
        {
            "ok": passed,
            "checks": checks,
            "artifacts_total": len(artifacts),
            "auto_deployment_request": auto_deployment_request,
            "trigger": trigger,
        },
    )
    try:
        from app.learning.service import capture_training_job

        await capture_training_job(db, job)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获训练评估失败 job={} err={}", job.id, exc)
    return {
        "status": "evaluated",
        "evaluation_task_id": evaluation_task.id,
        "passed": passed,
        "auto_deployment_request": auto_deployment_request,
    }


async def _maybe_auto_evaluate_completed_training_job(
    db: AsyncSession,
    job: TrainingJob,
    *,
    trigger: str,
) -> dict[str, Any] | None:
    if not _should_auto_evaluate_after_gateway_result(job):
        return None
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    return await _evaluate_training_job_uncommitted(
        db,
        _training_automation_user(job),
        job,
        list(tasks),
        trigger=trigger,
    )


def _deployment_target_set(deployment: TrainingModelDeployment) -> set[str]:
    return {
        str(item or "").strip()
        for item in (deployment.target_skill_ids_json or [])
        if str(item or "").strip()
    }


def _deployment_schedule_skill_ids(*deployments: TrainingModelDeployment | None) -> list[str]:
    skill_ids: set[str] = set()
    for deployment in deployments:
        if deployment is None:
            continue
        skill_ids.update(_deployment_target_set(deployment))
    return sorted(skill_ids)


async def _deployment_schedule_target_instance_ids(
    db: AsyncSession,
    skill_ids: list[str],
) -> list[str]:
    if not skill_ids:
        return []
    from app.execution.models import NodeScheduleConfig, SkillSyncAttempt

    configured_ids = (
        await db.execute(
            select(NodeScheduleConfig.instance_id)
            .join(OpenClawInstance, OpenClawInstance.id == NodeScheduleConfig.instance_id)
            .where(OpenClawInstance.is_active == True)  # noqa: E712
            .where(NodeScheduleConfig.skill_id.in_(skill_ids))
            .group_by(NodeScheduleConfig.instance_id)
        )
    ).scalars().all()
    synced_ids = (
        await db.execute(
            select(SkillSyncAttempt.instance_id)
            .join(OpenClawInstance, OpenClawInstance.id == SkillSyncAttempt.instance_id)
            .where(OpenClawInstance.is_active == True)  # noqa: E712
            .where(SkillSyncAttempt.status == "succeeded")
            .where(SkillSyncAttempt.skill_id.in_(skill_ids))
            .where(SkillSyncAttempt.instance_id.isnot(None))
            .group_by(SkillSyncAttempt.instance_id)
        )
    ).scalars().all()
    return sorted({str(item) for item in [*configured_ids, *synced_ids] if item})


async def _refresh_deployment_runtime_schedules(
    *,
    deployment_id: str,
    skill_ids: list[str],
    target_instance_ids: list[str],
) -> list[dict[str, Any]]:
    if not skill_ids or not target_instance_ids:
        return []
    try:
        from app.execution.sync_service import sync_service

        results = await sync_service.push_schedules_to_targets(
            skill_ids=skill_ids,
            target_instance_ids=target_instance_ids,
        )
        failed = [item for item in results if not item.get("ok")]
        if failed:
            logger.warning(
                "模型部署刷新节点定时上下文部分失败 deployment={} skills={} failed={}",
                deployment_id,
                skill_ids,
                failed,
            )
        return results
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "模型部署刷新节点定时上下文失败 deployment={} skills={} targets={} err={}",
            deployment_id,
            skill_ids,
            target_instance_ids,
            exc,
        )
        return []


def _deployment_schedule_refresh_payload(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {
            "attempted": False,
            "target_count": 0,
            "succeeded_count": 0,
            "failed_count": 0,
            "targets": [],
            "raw_payload_returned": False,
        }
    targets: list[dict[str, Any]] = []
    for item in results[:50]:
        if not isinstance(item, dict):
            continue
        instance_id = str(item.get("instance_id") or item.get("id") or "")[:80]
        ok = bool(item.get("ok"))
        target = {
            "instance_id": instance_id or None,
            "ok": ok,
            "accepted": item.get("accepted"),
            "config_version": item.get("config_version"),
            "error": _clean_text(item.get("error"), limit=200) if item.get("error") else None,
        }
        targets.append({key: value for key, value in target.items() if value not in (None, "", [], {})})
    succeeded_count = sum(1 for item in targets if item.get("ok") is True)
    failed_count = sum(1 for item in targets if item.get("ok") is False)
    return {
        "attempted": True,
        "target_count": len(targets),
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
        "targets": targets,
        "raw_payload_returned": False,
    }


def _auto_active_deployment_id_from_evaluation(evaluation: dict[str, Any] | None) -> str | None:
    if not isinstance(evaluation, dict):
        return None
    request = evaluation.get("auto_deployment_request")
    if not isinstance(request, dict):
        return None
    if request.get("status") != "active" and request.get("deployment_status") != "active":
        return None
    return _clean_text(request.get("deployment_id"), limit=50)


async def _refresh_auto_active_deployment_schedules_after_commit(
    db: AsyncSession,
    evaluation: dict[str, Any] | None,
) -> None:
    deployment_id = _auto_active_deployment_id_from_evaluation(evaluation)
    if not deployment_id:
        return
    request = evaluation.get("auto_deployment_request") if isinstance(evaluation, dict) else {}
    runtime_status = request.get("runtime_status") if isinstance(request, dict) else {}
    if not isinstance(runtime_status, dict):
        runtime_status = {}
    async with db.begin():
        deployment = await db.get(TrainingModelDeployment, deployment_id)
        if deployment is None:
            return
        previous_active = (
            await db.execute(
                select(TrainingModelDeployment)
                .where(TrainingModelDeployment.rollback_to == deployment.id)
                .where(TrainingModelDeployment.status == "rolled_back")
            )
        ).scalars().all()
        schedule_skill_ids = _deployment_schedule_skill_ids(deployment, *previous_active)
        schedule_target_ids = await _deployment_schedule_target_instance_ids(db, schedule_skill_ids)
    schedule_refresh_results = await _refresh_deployment_runtime_schedules(
        deployment_id=deployment_id,
        skill_ids=schedule_skill_ids,
        target_instance_ids=schedule_target_ids,
    )
    try:
        from app.learning.service import capture_model_deployment

        async with db.begin():
            refreshed = await db.get(TrainingModelDeployment, deployment_id)
            if refreshed is not None:
                await capture_model_deployment(
                    db,
                    refreshed,
                    runtime_status={
                        **runtime_status,
                        "schedule_refresh": _deployment_schedule_refresh_payload(schedule_refresh_results),
                    },
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获自动激活节点刷新结果失败 deployment={} err={}", deployment_id, exc)


def _deployments_target_overlap(left: TrainingModelDeployment, right: TrainingModelDeployment) -> bool:
    left_targets = _deployment_target_set(left)
    right_targets = _deployment_target_set(right)
    return bool(left_targets and right_targets and left_targets.intersection(right_targets))


async def _active_deployments_for_target(
    db: AsyncSession,
    deployment: TrainingModelDeployment,
) -> list[TrainingModelDeployment]:
    rows = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.department == deployment.department)
            .where(TrainingModelDeployment.model_family == deployment.model_family)
            .where(TrainingModelDeployment.status == "active")
            .where(TrainingModelDeployment.id != deployment.id)
            .order_by(TrainingModelDeployment.activated_at.desc(), TrainingModelDeployment.created_at.desc())
        )
    ).scalars().all()
    return [item for item in rows if _deployments_target_overlap(item, deployment)]


async def _get_deployment_and_job(
    db: AsyncSession,
    user: User,
    deployment_id: str,
) -> tuple[TrainingModelDeployment, TrainingJob]:
    deployment = await db.get(TrainingModelDeployment, deployment_id)
    if not deployment:
        raise AppError("NOT_FOUND", 404, {"detail": "training deployment not found"})
    _require_training_department_access(user, deployment.department)
    job = await db.get(TrainingJob, deployment.job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    return deployment, job


def _deployment_model_profile(deployment: TrainingModelDeployment, job: TrainingJob) -> str:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    explicit = _nested_value(
        spec,
        "deployment.model_profile",
        "model.profile",
        "model_profile",
        "base_model_profile",
    )
    profile = str(explicit or "").strip().lower().replace("_", "-")
    if profile in TRAINING_MODEL_PROFILES_ALLOWED:
        return profile
    family_profile = _training_model_profile_from_family(getattr(deployment, "model_family", None))
    if family_profile:
        return family_profile
    if deployment.deployment_runtime_profile == TRAINING_QWEN36_DEPLOYMENT_RUNTIME_PROFILE:
        return TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE
    return TRAINING_QWEN35_4B_PROFILE


def _deployment_artifact_uri(artifact_ref: dict[str, Any]) -> str:
    return str(
        artifact_ref.get("target_uri")
        or artifact_ref.get("uri")
        or artifact_ref.get("artifact_uri")
        or artifact_ref.get("url")
        or artifact_ref.get("path")
        or ""
    ).strip()


def _artifact_uri_is_local_file(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    return parsed.scheme in {"", "file"}


def _deployment_artifact_payload(job: TrainingJob, deployment: TrainingModelDeployment) -> dict[str, Any]:
    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    return {
        "job_id": job.id,
        "artifact_id": str(
            artifact_ref.get("id")
            or deployment.artifact_id
            or artifact_ref.get("sha256")
            or artifact_ref.get("name")
            or ""
        )[:160],
        "artifact_name": str(artifact_ref.get("name") or artifact_ref.get("filename") or "adapter.tar.gz")[:160],
        "sha256": str(artifact_ref.get("sha256") or "")[:64],
    }


def _openwebui_model_segment(value: Any, *, fallback: str, limit: int = 80) -> str:
    text = str(value or fallback or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    return (text or fallback)[:limit]


def _deployment_openwebui_model_id(deployment: TrainingModelDeployment, job: TrainingJob) -> str:
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    explicit = _nested_value(
        spec,
        "deployment.openwebui_model_id",
        "deployment.openwebui.model_id",
        "openwebui_model_id",
    )
    if explicit:
        return _openwebui_model_segment(explicit, fallback="skillforge-ft-custom", limit=180)
    target_skill_ids = deployment.target_skill_ids_json if isinstance(deployment.target_skill_ids_json, list) else []
    skill_id = next((str(item or "").strip() for item in target_skill_ids if str(item or "").strip()), "")
    skill_id = skill_id or str(job.target_skill_id or deployment.model_family or "skill").strip()
    model_profile = _deployment_model_profile(deployment, job)
    profile_segment = _openwebui_model_segment(model_profile, fallback=TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE, limit=80)
    if model_profile == TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE:
        created_at = deployment.created_at if isinstance(deployment.created_at, datetime) else now_bjt()
        date_segment = created_at.strftime("%Y%m%d")
        deployment_segment = _openwebui_model_segment(deployment.id, fallback="deployment", limit=48)
        return f"skillforge-ft-{profile_segment}-{date_segment}-{deployment_segment}"[:180]
    skill_segment = _openwebui_model_segment(skill_id, fallback="skill", limit=48)
    deployment_segment = _openwebui_model_segment(deployment.id, fallback="deployment", limit=40)
    return f"skillforge-ft-{profile_segment}-{skill_segment}-{deployment_segment}"[:180]


async def _ensure_deployment_artifact_on_target(
    db: AsyncSession,
    deployment: TrainingModelDeployment,
    job: TrainingJob,
    gateway_id: str | None,
    gateway_kind: str | None,
) -> dict[str, Any]:
    artifact_ref = dict(deployment.artifact_ref_json or {}) if isinstance(deployment.artifact_ref_json, dict) else {}
    artifact_uri = _deployment_artifact_uri(artifact_ref)
    training_gateway_id = _clean_text(job.target_gateway_id, limit=50)
    if not gateway_id or not training_gateway_id or gateway_id == training_gateway_id:
        return {"status": "same_node" if gateway_id else "target_gateway_missing", "artifact_uri": artifact_uri}
    if not _artifact_uri_is_local_file(artifact_uri):
        return {"status": "skipped", "reason": "non_local_artifact_uri", "artifact_uri": artifact_uri}
    if not bridge_registry.is_online(gateway_id):
        return {"status": "skipped", "reason": "target_bridge_offline", "artifact_uri": artifact_uri}
    if not bridge_registry.is_online(training_gateway_id):
        return {"status": "failed", "reason": "training_bridge_offline", "artifact_uri": artifact_uri}

    payload = _deployment_artifact_payload(job, deployment)
    target_client = AIClawClient(gateway_id, gateway_kind=gateway_kind)
    try:
        status = await target_client.get_training_artifact_status(payload, timeout=30)
    except Exception:
        status = {}
    if isinstance(status, dict) and status.get("exists") is True and status.get("uri"):
        target_uri = str(status.get("uri") or "")
        artifact_ref.update({
            "source_uri": artifact_ref.get("source_uri") or artifact_uri,
            "uri": target_uri,
            "target_uri": target_uri,
            "deployment_target_gateway_id": gateway_id,
            "training_gateway_id": training_gateway_id,
            "target_synced_at": isoformat_bjt(now_bjt()),
        })
        deployment.artifact_ref_json = artifact_ref
        await db.flush()
        return {"status": "already_present", "artifact_uri": target_uri}

    source_instance = await db.get(OpenClawInstance, training_gateway_id)
    source_kind = getattr(source_instance, "bridge_gateway_kind", None) if source_instance is not None else None
    source_client = AIClawClient(training_gateway_id, gateway_kind=source_kind)
    downloaded = await source_client.download_training_artifact(payload, timeout=300)
    content_b64 = downloaded.get("content_base64") if isinstance(downloaded, dict) else None
    if not content_b64:
        raise AppError(
            "TRAINING_DEPLOYMENT_ARTIFACT_SYNC_FAILED",
            502,
            {"detail": "training artifact download did not return content_base64"},
        )
    imported = await target_client.import_training_artifact(
        {
            "job_id": job.id,
            "artifact_id": payload["artifact_id"],
            "filename": downloaded.get("filename") or payload["artifact_name"],
            "sha256": downloaded.get("sha256") or payload["sha256"],
            "content_base64": content_b64,
        },
        timeout=300,
    )
    target_uri = str(imported.get("uri") or "")
    if not target_uri:
        raise AppError(
            "TRAINING_DEPLOYMENT_ARTIFACT_SYNC_FAILED",
            502,
            {"detail": "training artifact import did not return target uri"},
        )
    artifact_ref.update({
        "source_uri": artifact_ref.get("source_uri") or artifact_uri,
        "uri": target_uri,
        "target_uri": target_uri,
        "deployment_target_gateway_id": gateway_id,
        "training_gateway_id": training_gateway_id,
        "target_synced_at": isoformat_bjt(now_bjt()),
    })
    if imported.get("sha256"):
        artifact_ref["sha256"] = str(imported.get("sha256") or "")[:64]
    deployment.artifact_ref_json = artifact_ref
    await db.flush()
    return {"status": "imported", "artifact_uri": target_uri, "size_bytes": imported.get("size_bytes")}


async def _register_deployment_openwebui_model(
    deployment: TrainingModelDeployment,
    job: TrainingJob,
    gateway_id: str | None,
    gateway_kind: str | None,
) -> dict[str, Any]:
    if not gateway_id or not bridge_registry.is_online(gateway_id):
        return {"registered": False, "reason": "target_bridge_offline"}
    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    artifact_uri = _deployment_artifact_uri(artifact_ref)
    if not _artifact_uri_is_local_file(artifact_uri):
        return {"registered": False, "reason": "non_local_artifact_uri"}
    model_profile = _deployment_model_profile(deployment, job)
    aliases = []
    if model_profile == TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE:
        aliases.append(f"{TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE}-finetuned")
    payload = {
        "model_id": _deployment_openwebui_model_id(deployment, job),
        "aliases": aliases,
        "profile": model_profile,
        "runtime_profile": deployment.deployment_runtime_profile,
        "deployment_id": deployment.id,
        "job_id": job.id,
        "model_family": deployment.model_family,
        "display_name": f"SkillForge FT {model_profile} · {deployment.id}",
        "source_type": "skillforge_finetuned_adapter",
        "artifact_uri": artifact_uri,
        "artifact_sha256": artifact_ref.get("sha256"),
    }
    try:
        return await AIClawClient(gateway_id, gateway_kind=gateway_kind).register_openwebui_model(payload, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"registered": False, "reason": _extract_error_message(exc)}


def _deployment_keyword_review_config(job: TrainingJob) -> dict[str, Any] | None:
    if str(job.target_skill_id or "").strip() == TRAINING_FULL_HISTORY_CHAT_SKILL_ID:
        return None
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    governance = _safe_dict(spec.get("governance"))
    if governance.get("skillforge_full_history_chat") is True:
        return None
    raw = None
    for section_name in ("deployment", "evaluation", "eval_gate"):
        section = spec.get(section_name) if isinstance(spec.get(section_name), dict) else {}
        candidate = section.get("keyword_review")
        if isinstance(candidate, dict):
            raw = candidate
            break
    if not raw or raw.get("enabled") is not True:
        return None
    keywords = [
        str(item or "").strip()
        for item in (raw.get("keywords") if isinstance(raw.get("keywords"), list) else [])
        if str(item or "").strip()
    ]
    if not keywords:
        keywords = list(TRAINING_ADULT_PRODUCT_REVIEW_KEYWORDS)
    max_keywords = max(1, min(_safe_int(raw.get("max_keywords") or len(keywords)), 20))
    return {
        "enabled": True,
        "domain": str(raw.get("domain") or "adult_products")[:80],
        "keywords": keywords[:max_keywords],
        "max_tokens": max(16, min(_safe_int(raw.get("max_tokens") or 96), 512)),
        "timeout_seconds": max(30, min(_safe_int(raw.get("timeout_seconds") or 600), 3600)),
    }


async def _run_deployment_keyword_review(
    *,
    gateway_id: str,
    gateway_kind: str | None,
    model_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    keywords = [str(item or "").strip() for item in config.get("keywords") or [] if str(item or "").strip()]
    client = AIClawClient(gateway_id, gateway_kind=gateway_kind)
    items: list[dict[str, Any]] = []
    passed_count = 0
    for keyword in keywords:
        prompt = (
            f"你是成人用品电商运营评审助手。请用一句话说明「{keyword}」"
            "在商品类目、合规表达或选品测试中的注意点。"
        )
        try:
            result = await client.test_openwebui_chat(
                {
                    "model_id": model_id,
                    "prompt": prompt,
                    "max_tokens": config["max_tokens"],
                    "timeout_seconds": config["timeout_seconds"],
                },
                timeout=config["timeout_seconds"] + 60,
            )
            text = str(_safe_dict(result).get("text") or "").strip()
            ok = bool(_safe_dict(result).get("ok")) and bool(text)
            if ok:
                passed_count += 1
            items.append({
                "keyword": keyword,
                "status": "passed" if ok else "failed",
                "ok": ok,
                "text_preview": text[:240],
                "http_status": _safe_dict(result).get("http_status"),
                "finish_reason": _safe_dict(result).get("finish_reason"),
            })
        except Exception as exc:  # noqa: BLE001
            items.append({
                "keyword": keyword,
                "status": "failed",
                "ok": False,
                "error": _extract_error_message(exc)[:1000],
            })
    status = "passed" if keywords and passed_count == len(keywords) else "failed"
    return {
        "status": status,
        "domain": str(config.get("domain") or "adult_products")[:80],
        "model_id": model_id,
        "keyword_count": len(keywords),
        "passed_count": passed_count,
        "items": items,
    }


async def _deployment_node_model_status(
    db: AsyncSession,
    gateway_id: str | None,
    gateway_kind: str | None,
    profile: str,
) -> dict[str, Any]:
    if not gateway_id:
        return {"status": "unavailable", "reason": "target_gateway_missing", "profile": profile}
    if not bridge_registry.is_online(gateway_id):
        return {"status": "unavailable", "reason": "bridge_offline", "profile": profile}
    try:
        result = await AIClawClient(gateway_id, gateway_kind=gateway_kind).get_training_model_status(
            {"profile": profile},
            timeout=30,
        )
    except AppError as exc:
        return {
            "status": "unavailable",
            "reason": exc.code,
            "profile": profile,
            "detail": _sanitize_training_result_value(exc.detail or {}),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unavailable",
            "reason": "model_status_failed",
            "profile": profile,
            "detail": _extract_error_message(exc),
        }
    safe = _sanitize_training_result_value(result if isinstance(result, dict) else {})
    if not isinstance(safe, dict):
        safe = {"status": "unknown"}
    safe["profile"] = str(safe.get("profile") or profile)[:80]
    return safe


async def _deployment_inference_readiness(
    db: AsyncSession,
    deployment: TrainingModelDeployment,
    job: TrainingJob,
) -> dict[str, Any]:
    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    artifact_uri = _deployment_artifact_uri(artifact_ref)
    training_gateway_id = _clean_text(job.target_gateway_id, limit=50)
    gateway_id = _deployment_target_gateway_id(deployment, job)
    gateway_kind = None
    inference_ready = True
    disabled_reason = None
    if not artifact_uri:
        inference_ready = False
        disabled_reason = "artifact_uri_missing"
    elif not gateway_id:
        inference_ready = False
        disabled_reason = "target_gateway_missing"
    else:
        instance = await db.get(OpenClawInstance, gateway_id)
        if instance is None or getattr(instance, "is_active", True) is False:
            inference_ready = False
            disabled_reason = "target_gateway_missing"
        else:
            cap = _safe_capabilities(getattr(instance, "bridge_capabilities_json", None))
            ops = {str(item or "").strip() for item in (cap.get("ops") or []) if str(item or "").strip()}
            gateway_kind = str(
                cap.get("gateway_kind")
                or getattr(instance, "bridge_gateway_kind", "")
                or getattr(instance, "agent_type", "")
                or ""
            )[:50] or None
            if "training.inference" not in ops:
                inference_ready = False
                disabled_reason = "training_inference_op_unavailable"
    model_profile = _deployment_model_profile(deployment, job)
    model_status: dict[str, Any] | None = None
    model_ready = False
    if inference_ready and gateway_id:
        model_status = await _deployment_node_model_status(db, gateway_id, gateway_kind, model_profile)
        model_ready = str(model_status.get("status") or "").lower() in TRAINING_DEPLOYMENT_MODEL_READY_STATUSES
        if not model_ready:
            inference_ready = False
            disabled_reason = "training_model_not_ready"
    artifact_sync: dict[str, Any] | None = None
    if inference_ready and gateway_id:
        try:
            artifact_sync = await _ensure_deployment_artifact_on_target(
                db,
                deployment,
                job,
                gateway_id,
                gateway_kind,
            )
        except AppError as exc:
            artifact_sync = {
                "status": "failed",
                "reason": exc.code,
                "detail": _sanitize_training_result_value(exc.detail or {}),
            }
        if artifact_sync.get("status") == "failed":
            inference_ready = False
            disabled_reason = "artifact_sync_failed"
        artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
        artifact_uri = _deployment_artifact_uri(artifact_ref)
    openwebui_status: dict[str, Any] | None = None
    if inference_ready and gateway_id:
        openwebui_status = await _register_deployment_openwebui_model(
            deployment,
            job,
            gateway_id,
            gateway_kind,
        )
    keyword_review: dict[str, Any] | None = None
    keyword_review_config = _deployment_keyword_review_config(job)
    if inference_ready and keyword_review_config:
        model_id = str(
            _safe_dict(openwebui_status).get("model_id")
            or _deployment_openwebui_model_id(deployment, job)
        ).strip()
        if _safe_dict(openwebui_status).get("registered") is not True:
            inference_ready = False
            disabled_reason = "openwebui_registration_failed"
            keyword_review = {
                "status": "skipped",
                "reason": "openwebui_registration_failed",
                "model_id": model_id,
            }
        elif gateway_id:
            keyword_review = await _run_deployment_keyword_review(
                gateway_id=gateway_id,
                gateway_kind=gateway_kind,
                model_id=model_id,
                config=keyword_review_config,
            )
            if keyword_review.get("status") != "passed":
                inference_ready = False
                disabled_reason = "keyword_review_failed"
    payload = {
        "artifact_uri_present": bool(artifact_uri),
        "target_gateway_id": gateway_id,
        "target_gateway_kind": gateway_kind,
        "model_profile": model_profile,
        "model_status": model_status,
        "model_ready": model_ready,
        "inference_ready": inference_ready,
        "inference_disabled_reason": disabled_reason,
    }
    if artifact_sync:
        payload["artifact_sync"] = artifact_sync
    if openwebui_status:
        payload["openwebui"] = openwebui_status
    if keyword_review:
        payload["keyword_review"] = keyword_review
    if training_gateway_id and training_gateway_id != gateway_id:
        payload["training_gateway_id"] = training_gateway_id
    if deployment.deployment_runtime_profile:
        payload["deployment_runtime_profile"] = deployment.deployment_runtime_profile
    return payload


async def _require_deployment_inference_ready(
    db: AsyncSession,
    deployment: TrainingModelDeployment,
    job: TrainingJob,
) -> dict[str, Any]:
    readiness = await _deployment_inference_readiness(db, deployment, job)
    if not readiness["inference_ready"]:
        raise AppError(
            "TRAINING_DEPLOYMENT_NOT_READY",
            400,
            {
                "detail": "deployment artifact is not ready for inference",
                "runtime_status": readiness,
            },
        )
    return readiness


async def _deployment_response(
    db: AsyncSession,
    deployment: TrainingModelDeployment,
    job: TrainingJob,
) -> dict[str, Any]:
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    deployments = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.job_id == job.id)
            .order_by(TrainingModelDeployment.created_at.desc(), TrainingModelDeployment.id.desc())
        )
    ).scalars().all()
    return {
        "deployment": _serialize_deployment(deployment),
        "job": _serialize_job(job, list(tasks), list(deployments)),
    }


async def get_training_job_artifacts(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job_id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    artifacts = _collect_training_artifacts(list(tasks))
    download_proxy_available = bool(job.target_gateway_id)
    for artifact in artifacts:
        artifact["downloadable"] = download_proxy_available
        artifact["download_url"] = (
            f"/api/training/jobs/{job.id}/artifacts/{artifact['id']}/download"
            if download_proxy_available else None
        )
    return {
        "job_id": job.id,
        "status": job.status,
        "items": artifacts,
        "total": len(artifacts),
        "download_proxy_available": download_proxy_available,
    }


def _safe_artifact_download_filename(value: Any, fallback: str) -> str:
    name = _clean_text(value, limit=180) or fallback
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return safe[:180] or fallback


async def download_training_job_artifact(
    db: AsyncSession,
    user: User,
    job_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    artifact_key = _clean_text(artifact_id, limit=160, required=True) or ""
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job_id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    artifacts = _collect_training_artifacts(list(tasks))
    artifact = next((item for item in artifacts if str(item.get("id") or "") == artifact_key), None)
    if not artifact:
        raise AppError("NOT_FOUND", 404, {"detail": "training artifact not found"})
    if not job.target_gateway_id:
        raise AppError("ARTIFACT_DOWNLOAD_UNAVAILABLE", 409, {"detail": "training job has no target gateway"})
    instance = await _get_training_gateway_instance(db, user, job)
    try:
        payload = await AIClawClient(
            instance.id,
            gateway_kind=instance.bridge_gateway_kind,
        ).download_training_artifact(
            {
                "job_id": job.id,
                "artifact_id": artifact_key,
                "artifact_name": artifact.get("name"),
                "sha256": artifact.get("sha256"),
            },
            timeout=60,
        )
    except AppError as exc:
        if exc.status in {401, 403}:
            raise
        raise AppError("ARTIFACT_DOWNLOAD_UNAVAILABLE", 409, {"detail": _extract_error_message(exc)}) from exc
    content_b64 = payload.get("content_base64") if isinstance(payload, dict) else None
    if not isinstance(content_b64, str) or not content_b64:
        raise AppError("ARTIFACT_DOWNLOAD_UNAVAILABLE", 409, {"detail": "training gateway did not return artifact content"})
    try:
        content = base64.b64decode(content_b64, validate=True)
    except Exception as exc:
        raise AppError("ARTIFACT_DOWNLOAD_INVALID", 502, {"detail": "training gateway returned invalid base64"}) from exc
    if len(content) > MAX_TRAINING_ARTIFACT_DOWNLOAD_BYTES:
        raise AppError("ARTIFACT_DOWNLOAD_TOO_LARGE", 413, {"detail": "training artifact is too large"})
    expected_sha = str(payload.get("sha256") or artifact.get("sha256") or "").strip().lower()
    if expected_sha and len(expected_sha) == 64:
        actual_sha = hashlib.sha256(content).hexdigest()
        if actual_sha != expected_sha:
            raise AppError("ARTIFACT_DOWNLOAD_HASH_MISMATCH", 502, {"detail": "training artifact sha256 mismatch"})
    filename = _safe_artifact_download_filename(payload.get("filename") or artifact.get("name"), f"{artifact_key}.bin")
    return {
        "filename": filename,
        "content_type": _clean_text(payload.get("content_type"), limit=120) or "application/octet-stream",
        "content": content,
        "size_bytes": len(content),
        "sha256": expected_sha,
    }


async def list_training_deployments(
    db: AsyncSession,
    user: User,
    *,
    status: str | None = None,
) -> dict[str, Any]:
    resolved_status = _normalize_deployment_status(status)
    stmt = select(TrainingModelDeployment).order_by(
        TrainingModelDeployment.created_at.desc(),
        TrainingModelDeployment.id.desc(),
    )
    if resolved_status:
        stmt = stmt.where(TrainingModelDeployment.status == resolved_status)
    if not _can_view_all_training_resources(user):
        department = _user_department(user)
        stmt = stmt.where(TrainingModelDeployment.department == department if department else false())
    deployments = (await db.execute(stmt.limit(200))).scalars().all()
    job_map: dict[str, TrainingJob] = {}
    if deployments:
        jobs = (
            await db.execute(
                select(TrainingJob).where(TrainingJob.id.in_([item.job_id for item in deployments]))
            )
        ).scalars().all()
        job_map = {job.id: job for job in jobs}
    items = []
    for deployment in deployments:
        row = _serialize_deployment(deployment)
        row["job"] = _serialize_deployment_job_summary(job_map.get(deployment.job_id))
        items.append(row)
    return {
        "items": items,
        "total": len(items),
        "stats": {
            "awaiting_review": sum(1 for item in items if item["status"] == "awaiting_review"),
            "canary": sum(1 for item in items if item["status"] == "canary"),
            "active": sum(1 for item in items if item["status"] == "active"),
            "rejected": sum(1 for item in items if item["status"] == "rejected"),
            "rolled_back": sum(1 for item in items if item["status"] == "rolled_back"),
            "failed": sum(1 for item in items if item["status"] == "failed"),
        },
    }


async def get_training_deployment(
    db: AsyncSession,
    user: User,
    deployment_id: str,
) -> dict[str, Any]:
    deployment, job = await _get_deployment_and_job(db, user, deployment_id)
    return await _deployment_response(db, deployment, job)


async def sync_training_deployment_openwebui(
    db: AsyncSession,
    user: User,
    deployment_id: str,
) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to sync training deployment"})
    deployment, job = await _get_deployment_and_job(db, user, deployment_id)
    if deployment.status not in TRAINING_DEPLOYMENT_ACTIVE_STATUSES:
        raise AppError(
            "INVALID_STATUS",
            400,
            {"detail": "only canary or active deployments can be synced to OpenWebUI"},
        )
    runtime_status = await _require_deployment_inference_ready(db, deployment, job)
    openwebui_status = runtime_status.get("openwebui") if isinstance(runtime_status.get("openwebui"), dict) else {}
    if openwebui_status.get("registered") is not True:
        raise AppError(
            "TRAINING_OPENWEBUI_SYNC_FAILED",
            409,
            {
                "detail": openwebui_status.get("reason") or openwebui_status.get("error") or "OpenWebUI model registration failed",
                "runtime_status": _sanitize_training_result_value(runtime_status),
            },
        )
    deployment.updated_at = now_bjt()
    await _record_training_deployment_audit(
        db,
        user,
        "training_deployment.sync_openwebui",
        deployment,
        {"runtime_status": runtime_status},
    )
    try:
        from app.learning.service import capture_model_deployment

        await capture_model_deployment(db, deployment, runtime_status=runtime_status)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获 OpenWebUI 同步结果失败 deployment={} err={}", deployment.id, exc)
    await db.commit()
    await db.refresh(job)
    await db.refresh(deployment)
    payload = await _deployment_response(db, deployment, job)
    payload["runtime_status"] = runtime_status
    payload["openwebui"] = openwebui_status
    return payload


async def list_training_datasets(db: AsyncSession, user: User) -> dict[str, Any]:
    skill_filter = await build_skill_access_filter(db, user, "read")
    skills = (
        await db.execute(
            select(Skill)
            .where(skill_filter)
            .order_by(Skill.updated_at.desc(), Skill.id)
            .limit(TRAINING_DATASET_LIST_LIMIT)
        )
    ).scalars().all()
    skill_ids = [skill.id for skill in skills]
    logs_by_skill: dict[str, list[DecisionLog]] = {skill_id: [] for skill_id in skill_ids}
    if skill_ids:
        ranked_logs = (
            select(
                DecisionLog.id.label("decision_log_id"),
                func.row_number()
                .over(
                    partition_by=DecisionLog.skill_id,
                    order_by=(DecisionLog.created_at.desc(), DecisionLog.id.desc()),
                )
                .label("row_number"),
            )
            .where(DecisionLog.skill_id.in_(skill_ids))
            .where(DecisionLog.is_sandbox == False)  # noqa: E712
            .subquery()
        )
        logs = (
            await db.execute(
                select(DecisionLog)
                .join(ranked_logs, DecisionLog.id == ranked_logs.c.decision_log_id)
                .where(ranked_logs.c.row_number <= TRAINING_CANDIDATE_MAX_LOGS)
                .order_by(DecisionLog.skill_id, DecisionLog.created_at.desc(), DecisionLog.id.desc())
            )
        ).scalars().all()
        for log in logs:
            logs_by_skill.setdefault(log.skill_id, []).append(log)

    items: list[dict[str, Any]] = []
    can_create_jobs = _can_create_training_job(user)
    for skill in skills:
        item = _build_training_dataset_readiness(skill, logs_by_skill.get(skill.id, []))
        permissions = await get_skill_permissions(db, skill, user)
        item["can_create_candidate"] = bool(can_create_jobs and permissions.get("edit") and item["passed"])
        item["candidate_endpoint"] = f"/api/training/skills/{skill.id}/candidate"
        items.append(item)

    return {
        "items": items,
        "total": len(items),
        "stats": {
            "ready": sum(1 for item in items if item["passed"]),
            "with_samples": sum(1 for item in items if item["sample_counts"]["sft_samples"] > 0),
            "can_create_candidate": sum(1 for item in items if item["can_create_candidate"]),
            "sft_samples": sum(item["sample_counts"]["sft_samples"] for item in items),
            "preference_samples": sum(item["sample_counts"]["preference_samples"] for item in items),
            "action_outcome_samples": sum(item["sample_counts"]["action_outcome_samples"] for item in items),
            "eval_samples": sum(item["sample_counts"]["eval_samples"] for item in items),
        },
        "thresholds": TRAINING_CANDIDATE_THRESHOLDS,
    }


def _training_storage_location_id() -> str:
    return f"tsloc_{uuid4().hex[:24]}"


def _training_asset_source_id() -> str:
    return f"tas_{uuid4().hex[:24]}"


def _training_sample_id() -> str:
    return f"tsmp_{uuid4().hex[:24]}"


def _training_dataset_version_id() -> str:
    return f"tdv_{uuid4().hex[:24]}"


def _normalize_training_modality(value: Any, *, dataset_profile: str | None = None, mime_type: str | None = None) -> str:
    raw = str(value or "").strip().lower()
    if not raw and dataset_profile:
        raw = TRAINING_DATASET_PROFILE_MODALITY.get(dataset_profile, "")
    if not raw and str(mime_type or "").lower().startswith("image/"):
        raw = "image"
    if not raw:
        raw = "text"
    if raw in {"image+text", "image-text", "vision", "vlm"}:
        raw = "image_text"
    if raw not in TRAINING_MODALITIES:
        raise AppError("TRAINING_MODALITY_INVALID", 422, {"modality": raw})
    return raw


def _normalize_training_dataset_profile(value: Any) -> str:
    profile = str(value or "text_sft_v1").strip().lower()
    if profile not in TRAINING_DATASET_PROFILES:
        raise AppError("TRAINING_DATASET_PROFILE_INVALID", 422, {"dataset_profile": profile})
    return profile


def _normalize_training_asset_source_type(value: Any) -> str:
    source_type = str(value or "").strip().lower()
    if source_type not in TRAINING_ASSET_SOURCE_TYPES:
        raise AppError("TRAINING_ASSET_SOURCE_TYPE_INVALID", 422, {"source_type": source_type})
    return source_type


def _normalize_training_source_status(value: Any, allowed: set[str], default: str) -> str:
    status = str(value or default).strip().lower()
    if status not in allowed:
        raise AppError("TRAINING_STATUS_INVALID", 422, {"status": status})
    return status


def _json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _training_manifest_uri(dataset_id: str) -> str:
    return f"skillforge://training-datasets/{dataset_id}/manifest.jsonl"


def _can_read_training_department(user: User, department: str | None) -> bool:
    if _can_view_all_training_resources(user):
        return True
    return bool(department and department == _user_department(user))


def _require_training_asset_read_access(user: User, department: str | None) -> None:
    if not _can_read_training_department(user, department):
        raise AppError("FORBIDDEN", 403, {"detail": "no access to training asset department"})


def _serialize_training_storage_location(row: TrainingStorageLocation) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "backend": row.backend,
        "uri": row.uri,
        "gateway_id": row.gateway_id,
        "department": row.department,
        "org_unit_id": row.org_unit_id,
        "status": row.status,
        "capabilities": row.capabilities_json or {},
        "retention_policy": row.retention_policy_json or {},
        "encryption": row.encryption_json or {},
        "created_by": row.created_by,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


def _serialize_training_asset_source(row: TrainingAssetSource) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "title": row.title,
        "department": row.department,
        "org_unit_id": row.org_unit_id,
        "owner_user_id": row.owner_user_id,
        "skill_id": row.skill_id,
        "project_id": row.project_id,
        "project_run_id": row.project_run_id,
        "modality": row.modality,
        "storage_location_id": row.storage_location_id,
        "storage_backend": row.storage_backend,
        "storage_ref": row.storage_ref,
        "mime_type": row.mime_type,
        "byte_size": row.byte_size,
        "sha256": row.sha256,
        "sample_count": row.sample_count,
        "sensitivity_level": row.sensitivity_level,
        "status": row.status,
        "policy_result": row.policy_result_json or {},
        "metadata": row.metadata_json or {},
        "created_by": row.created_by,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


def _serialize_training_sample(row: TrainingSample, *, include_content: bool = True) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "source_id": row.source_id,
        "dataset_profile": row.dataset_profile,
        "modality": row.modality,
        "sample_hash": row.sample_hash,
        "media_refs": row.media_refs_json or [],
        "labels": row.labels_json or [],
        "quality_score": row.quality_score,
        "department": row.department,
        "org_unit_id": row.org_unit_id,
        "skill_id": row.skill_id,
        "project_id": row.project_id,
        "project_run_id": row.project_run_id,
        "sensitivity_level": row.sensitivity_level,
        "status": row.status,
        "metadata": row.metadata_json or {},
        "created_by": row.created_by,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }
    if include_content:
        payload["content"] = row.content_json or {}
    return payload


def _serialize_training_dataset_version(row: TrainingDatasetVersion, *, include_manifest: bool = False) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        "dataset_profile": row.dataset_profile,
        "modality": row.modality,
        "target_model_family": row.target_model_family,
        "department": row.department,
        "org_unit_id": row.org_unit_id,
        "status": row.status,
        "sample_count": row.sample_count,
        "media_count": row.media_count,
        "byte_size": row.byte_size,
        "manifest_sha256": row.manifest_sha256,
        "manifest_uri": _training_manifest_uri(row.id),
        "storage_location_id": row.storage_location_id,
        "target_gateway_id": row.target_gateway_id,
        "sync_status": row.sync_status,
        "synced_sink_job_id": row.synced_sink_job_id,
        "created_by": row.created_by,
        "approved_by": row.approved_by,
        "approved_at": isoformat_bjt(row.approved_at),
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
        "automation_status": _training_dataset_automation_status(row),
    }
    if include_manifest:
        payload["manifest"] = row.manifest_json or {}
    return payload


async def ensure_training_default_storage_location(
    db: AsyncSession,
    user: User,
    *,
    commit: bool = False,
) -> TrainingStorageLocation:
    row = await db.get(TrainingStorageLocation, TRAINING_DEFAULT_STORAGE_LOCATION_ID)
    now = now_bjt()
    if row:
        return row
    row = TrainingStorageLocation(
        id=TRAINING_DEFAULT_STORAGE_LOCATION_ID,
        name=TRAINING_DEFAULT_STORAGE_LOCATION_NAME,
        backend=TRAINING_DEFAULT_STORAGE_BACKEND,
        uri="gb10://training-primary/training-assets",
        gateway_id="training-primary",
        department=None,
        org_unit_id=None,
        status="active",
        capabilities_json={
            "dataset_manifest": True,
            "media_refs": True,
            "large_files": True,
            "model_training_sink": True,
        },
        retention_policy_json={"default": "company_training_asset_policy"},
        encryption_json={"credential_policy": "platform_only"},
        created_by=str(getattr(user, "id", "") or "") or None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    if commit:
        await db.commit()
        await db.refresh(row)
    return row


async def register_training_asset_source(
    db: AsyncSession,
    user: User,
    payload: dict[str, Any],
    *,
    commit: bool = True,
) -> dict[str, Any]:
    source_type = _normalize_training_asset_source_type(payload.get("source_type"))
    source_id = _clean_text(payload.get("source_id"), limit=120, required=True) or ""
    company_scope = payload.get("company_scope") is True and _can_view_all_training_resources(user)
    department = None if company_scope else _clean_text(payload.get("department") or _user_department(user), limit=100)
    if department:
        _require_training_department_access(user, department)
    storage_location_id = _clean_text(payload.get("storage_location_id"), limit=50)
    if not storage_location_id:
        storage_location_id = (await ensure_training_default_storage_location(db, user, commit=False)).id
    mime_type = _clean_text(payload.get("mime_type"), limit=120)
    modality = _normalize_training_modality(payload.get("modality"), mime_type=mime_type)
    status = _normalize_training_source_status(payload.get("status"), TRAINING_ASSET_STATUSES, "active")
    now = now_bjt()
    row = (
        await db.execute(
            select(TrainingAssetSource)
            .where(TrainingAssetSource.source_type == source_type, TrainingAssetSource.source_id == source_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    metadata = _sanitize_training_result_value(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {})
    policy = _sanitize_training_result_value(
        payload.get("policy_result") if isinstance(payload.get("policy_result"), dict) else {}
    )
    if row is None:
        row = TrainingAssetSource(
            id=_training_asset_source_id(),
            source_type=source_type,
            source_id=source_id,
            title=_clean_text(payload.get("title"), limit=240),
            department=department,
            org_unit_id=_clean_text(payload.get("org_unit_id"), limit=50),
            owner_user_id=_clean_text(payload.get("owner_user_id") or getattr(user, "id", None), limit=50),
            skill_id=_clean_text(payload.get("skill_id"), limit=100),
            project_id=_clean_text(payload.get("project_id"), limit=42),
            project_run_id=_clean_text(payload.get("project_run_id"), limit=50),
            modality=modality,
            storage_location_id=storage_location_id,
            storage_backend=_clean_text(payload.get("storage_backend"), limit=40),
            storage_ref=_clean_text(payload.get("storage_ref"), limit=1000),
            mime_type=mime_type,
            byte_size=_safe_int(payload.get("byte_size")),
            sha256=_clean_text(payload.get("sha256"), limit=64),
            sensitivity_level=_clean_text(payload.get("sensitivity_level") or "internal", limit=20) or "internal",
            status=status,
            policy_result_json=policy if isinstance(policy, dict) else {},
            metadata_json=metadata if isinstance(metadata, dict) else {},
            created_by=str(getattr(user, "id", "") or "") or None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        deduped = False
    else:
        _require_training_asset_read_access(user, row.department)
        row.title = _clean_text(payload.get("title"), limit=240) or row.title
        row.department = department or row.department
        row.org_unit_id = _clean_text(payload.get("org_unit_id"), limit=50) or row.org_unit_id
        row.owner_user_id = _clean_text(payload.get("owner_user_id") or getattr(user, "id", None), limit=50) or row.owner_user_id
        row.skill_id = _clean_text(payload.get("skill_id"), limit=100) or row.skill_id
        row.project_id = _clean_text(payload.get("project_id"), limit=42) or row.project_id
        row.project_run_id = _clean_text(payload.get("project_run_id"), limit=50) or row.project_run_id
        row.modality = modality or row.modality
        row.storage_location_id = storage_location_id or row.storage_location_id
        row.storage_backend = _clean_text(payload.get("storage_backend"), limit=40) or row.storage_backend
        row.storage_ref = _clean_text(payload.get("storage_ref"), limit=1000) or row.storage_ref
        row.mime_type = mime_type or row.mime_type
        row.byte_size = _safe_int(payload.get("byte_size")) or row.byte_size
        row.sha256 = _clean_text(payload.get("sha256"), limit=64) or row.sha256
        row.sensitivity_level = _clean_text(payload.get("sensitivity_level"), limit=20) or row.sensitivity_level
        row.status = status or row.status
        row.policy_result_json = {**(row.policy_result_json or {}), **(policy if isinstance(policy, dict) else {})}
        row.metadata_json = {**(row.metadata_json or {}), **(metadata if isinstance(metadata, dict) else {})}
        row.updated_at = now
        deduped = True
    await db.flush()
    if commit:
        await db.commit()
        await db.refresh(row)
    return {"ok": True, "deduped": deduped, "asset_source": _serialize_training_asset_source(row)}


async def register_training_asset_from_project_run_asset(
    db: AsyncSession,
    user: User,
    asset_id: str,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    from app.projects.models import Project, ProjectRun, ProjectRunAsset

    asset = await db.get(ProjectRunAsset, asset_id)
    if asset is None:
        raise AppError("PROJECT_ASSET_NOT_FOUND", 404)
    run = await db.get(ProjectRun, asset.project_run_id)
    project = await db.get(Project, asset.project_id)
    department = (run.department if run else None) or (project.department if project else None) or _user_department(user)
    if department:
        _require_training_department_access(user, department)
    return await register_training_asset_source(
        db,
        user,
        {
            "source_type": "project_run_asset",
            "source_id": asset.id,
            "title": asset.file_name,
            "department": department,
            "org_unit_id": (run.department_id if run else None) or (project.department_id if project else None),
            "owner_user_id": asset.owner_user_id,
            "project_id": asset.project_id,
            "project_run_id": asset.project_run_id,
            "modality": "image_text" if str(asset.mime_type or "").lower().startswith("image/") else None,
            "storage_backend": asset.storage_backend,
            "storage_ref": asset.storage_path,
            "mime_type": asset.mime_type,
            "byte_size": asset.byte_size,
            "sha256": asset.sha256,
            "sensitivity_level": "internal",
            "metadata": {
                "source": "project_run_asset",
                "asset_metadata": asset.metadata_json or {},
                "file_name": asset.file_name,
            },
        },
        commit=commit,
    )


async def record_training_sample(
    db: AsyncSession,
    user: User,
    payload: dict[str, Any],
    *,
    commit: bool = True,
) -> dict[str, Any]:
    dataset_profile = _normalize_training_dataset_profile(payload.get("dataset_profile"))
    content = payload.get("content")
    if not isinstance(content, dict):
        raise AppError("TRAINING_SAMPLE_INVALID", 422, {"detail": "content must be an object"})
    source_id = _clean_text(payload.get("source_id"), limit=50)
    if not source_id and isinstance(payload.get("source"), dict):
        source_result = await register_training_asset_source(db, user, payload["source"], commit=False)
        source_id = _clean_text(_safe_dict(source_result.get("asset_source")).get("id"), limit=50)
    source = await db.get(TrainingAssetSource, source_id) if source_id else None
    if source is None:
        raise AppError("TRAINING_ASSET_SOURCE_NOT_FOUND", 404)
    _require_training_asset_read_access(user, source.department)
    media_refs = payload.get("media_refs")
    if media_refs is None:
        media_refs = []
    if not isinstance(media_refs, list):
        raise AppError("TRAINING_SAMPLE_INVALID", 422, {"detail": "media_refs must be a list"})
    labels = payload.get("labels")
    if labels is None:
        labels = []
    if not isinstance(labels, list):
        raise AppError("TRAINING_SAMPLE_INVALID", 422, {"detail": "labels must be a list"})
    safe_content = _sanitize_training_result_value(content)
    safe_media_refs = _sanitize_training_result_value(media_refs)
    safe_labels = _sanitize_training_result_value(labels)
    modality = _normalize_training_modality(
        payload.get("modality") or source.modality,
        dataset_profile=dataset_profile,
        mime_type=source.mime_type,
    )
    sample_hash = _json_hash({
        "dataset_profile": dataset_profile,
        "content": safe_content,
        "media_refs": safe_media_refs,
        "labels": safe_labels,
    })
    existing = (
        await db.execute(
            select(TrainingSample)
            .where(
                TrainingSample.source_id == source.id,
                TrainingSample.dataset_profile == dataset_profile,
                TrainingSample.sample_hash == sample_hash,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        return {"ok": True, "deduped": True, "sample": _serialize_training_sample(existing)}
    now = now_bjt()
    row = TrainingSample(
        id=_training_sample_id(),
        source_id=source.id,
        dataset_profile=dataset_profile,
        modality=modality,
        sample_hash=sample_hash,
        content_json=safe_content if isinstance(safe_content, dict) else {},
        media_refs_json=safe_media_refs if isinstance(safe_media_refs, list) else [],
        labels_json=safe_labels if isinstance(safe_labels, list) else [],
        quality_score=max(0.0, min(float(_safe_number(payload.get("quality_score"))), 1.0)),
        department=source.department,
        org_unit_id=source.org_unit_id,
        skill_id=source.skill_id,
        project_id=source.project_id,
        project_run_id=source.project_run_id,
        sensitivity_level=_clean_text(payload.get("sensitivity_level") or source.sensitivity_level, limit=20) or "internal",
        status=_normalize_training_source_status(payload.get("status"), TRAINING_SAMPLE_STATUSES, "ready"),
        metadata_json=_sanitize_training_result_value(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
        created_by=str(getattr(user, "id", "") or "") or None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    source.sample_count = (source.sample_count or 0) + 1
    source.updated_at = now
    await db.flush()
    if commit:
        await db.commit()
        await db.refresh(row)
    return {"ok": True, "deduped": False, "sample": _serialize_training_sample(row)}


def _training_dataset_record(row: TrainingSample) -> dict[str, Any]:
    return {
        "sample_id": row.id,
        "profile": row.dataset_profile,
        "modality": row.modality,
        "content": row.content_json or {},
        "media_refs": row.media_refs_json or [],
        "labels": row.labels_json or [],
        "quality_score": row.quality_score,
        "source": {
            "source_id": row.source_id,
            "department": row.department,
            "org_unit_id": row.org_unit_id,
            "skill_id": row.skill_id,
            "project_id": row.project_id,
            "project_run_id": row.project_run_id,
        },
        "policy": {
            "sensitivity_level": row.sensitivity_level,
            "credential_policy": "platform_only",
        },
        "hash": row.sample_hash,
    }


async def _select_training_samples_for_dataset(
    db: AsyncSession,
    user: User,
    payload: dict[str, Any],
    *,
    dataset_profile: str,
    department: str | None,
    modality: str,
) -> list[TrainingSample]:
    sample_ids = payload.get("sample_ids")
    limit = min(max(_safe_int(payload.get("limit")) or TRAINING_DATASET_VERSION_MAX_SAMPLES, 1), TRAINING_DATASET_VERSION_MAX_SAMPLES)
    stmt = (
        select(TrainingSample)
        .where(TrainingSample.dataset_profile == dataset_profile)
        .where(TrainingSample.status == "ready")
        .order_by(TrainingSample.created_at.asc(), TrainingSample.id.asc())
    )
    if isinstance(sample_ids, list) and sample_ids:
        cleaned_ids = [str(item or "").strip()[:50] for item in sample_ids if str(item or "").strip()]
        if not cleaned_ids:
            raise AppError("TRAINING_DATASET_EMPTY", 422, {"detail": "sample_ids is empty"})
        stmt = stmt.where(TrainingSample.id.in_(cleaned_ids)).limit(min(len(cleaned_ids), limit))
    else:
        stmt = stmt.limit(limit)
    if department:
        _require_training_asset_read_access(user, department)
        stmt = stmt.where(TrainingSample.department == department)
    elif not _can_view_all_training_resources(user):
        user_department = _user_department(user)
        stmt = stmt.where(TrainingSample.department == user_department if user_department else false())
    if modality != "mixed":
        stmt = stmt.where(or_(TrainingSample.modality == modality, TrainingSample.modality == "mixed"))
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        raise AppError("TRAINING_DATASET_EMPTY", 422, {"detail": "no ready samples matched dataset selector"})
    for row in rows:
        _require_training_asset_read_access(user, row.department)
    return rows


async def create_training_dataset_version(
    db: AsyncSession,
    user: User,
    payload: dict[str, Any],
    *,
    commit: bool = True,
) -> dict[str, Any]:
    if not _can_create_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to create training dataset"})
    dataset_profile = _normalize_training_dataset_profile(payload.get("dataset_profile"))
    modality = _normalize_training_modality(payload.get("modality"), dataset_profile=dataset_profile)
    company_scope = payload.get("company_scope") is True and _can_view_all_training_resources(user)
    department = None if company_scope else _clean_text(payload.get("department") or _user_department(user), limit=100)
    if department:
        _require_training_department_access(user, department)
    rows = await _select_training_samples_for_dataset(
        db,
        user,
        payload,
        dataset_profile=dataset_profile,
        department=department,
        modality=modality,
    )
    storage_location_id = _clean_text(payload.get("storage_location_id"), limit=50)
    if not storage_location_id:
        storage_location_id = (await ensure_training_default_storage_location(db, user, commit=False)).id
    now = now_bjt()
    name = _clean_text(payload.get("name"), limit=160) or f"{dataset_profile}-{department or 'company'}"
    version = _clean_text(payload.get("version"), limit=60) or now.strftime("%Y%m%d%H%M%S")
    dataset_id = _training_dataset_version_id()
    records = [_training_dataset_record(row) for row in rows]
    manifest = {
        "schema_version": TRAINING_DATASET_MANIFEST_SCHEMA_VERSION,
        "dataset_version_id": dataset_id,
        "name": name,
        "version": version,
        "dataset_profile": dataset_profile,
        "modality": modality,
        "target_model_family": _clean_text(payload.get("target_model_family"), limit=120),
        "department": department,
        "org_unit_id": _clean_text(payload.get("org_unit_id"), limit=50),
        "storage": {
            "storage_location_id": storage_location_id,
            "manifest_uri": _training_manifest_uri(dataset_id),
            "media_policy": "reference_only",
        },
        "sample_ids": [row.id for row in rows],
        "sample_count": len(rows),
        "media_count": sum(len(row.media_refs_json or []) for row in rows),
        "records": records,
        "lineage": {
            "source": "training_asset_registry",
            "sample_hashes": [row.sample_hash for row in rows],
        },
    }
    raw = _json_bytes(manifest)
    manifest_sha = hashlib.sha256(raw).hexdigest()
    row = TrainingDatasetVersion(
        id=dataset_id,
        name=name,
        version=version,
        dataset_profile=dataset_profile,
        modality=modality,
        target_model_family=_clean_text(payload.get("target_model_family"), limit=120),
        department=department,
        org_unit_id=_clean_text(payload.get("org_unit_id"), limit=50),
        status=_normalize_training_source_status(payload.get("status"), TRAINING_DATASET_VERSION_STATUSES, "ready"),
        sample_count=len(rows),
        media_count=int(manifest["media_count"]),
        byte_size=len(raw),
        manifest_sha256=manifest_sha,
        manifest_json={**manifest, "manifest_sha256": manifest_sha},
        storage_location_id=storage_location_id,
        target_gateway_id=_clean_text(payload.get("target_gateway_id") or "training-primary", limit=80),
        sync_status="not_synced",
        created_by=str(getattr(user, "id", "") or "") or None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    auto_sync_result: dict[str, Any] | None = None
    auto_enabled = TRAINING_AUTO_ENABLED and TRAINING_AUTO_DATASET_SYNC_ENABLED and payload.get("auto_sync", True) is not False
    if auto_enabled and row.status in {"ready", "approved"}:
        auto_sync_result = await sync_training_dataset_version(
            db,
            user,
            row.id,
            {
                "target_gateway_id": row.target_gateway_id or TRAINING_PRIMARY_GATEWAY_IDS[0],
                "metadata": {"trigger": "dataset_version.auto_sync"},
            },
            commit=False,
        )
    if commit:
        await db.commit()
        await db.refresh(row)
    return {
        "ok": True,
        "dataset_version": _serialize_training_dataset_version(row, include_manifest=True),
        "auto_sync": auto_sync_result,
    }


async def list_training_asset_sources(
    db: AsyncSession,
    user: User,
    *,
    source_type: str | None = None,
    modality: str | None = None,
    department: str | None = None,
    limit: int = TRAINING_ASSET_LIST_LIMIT,
) -> dict[str, Any]:
    stmt = select(TrainingAssetSource).order_by(TrainingAssetSource.created_at.desc(), TrainingAssetSource.id.desc())
    if source_type:
        stmt = stmt.where(TrainingAssetSource.source_type == _normalize_training_asset_source_type(source_type))
    if modality:
        stmt = stmt.where(TrainingAssetSource.modality == _normalize_training_modality(modality))
    if department:
        _require_training_asset_read_access(user, department)
        stmt = stmt.where(TrainingAssetSource.department == department)
    elif not _can_view_all_training_resources(user):
        user_department = _user_department(user)
        stmt = stmt.where(TrainingAssetSource.department == user_department if user_department else false())
    rows = list((await db.execute(stmt.limit(max(1, min(limit, TRAINING_ASSET_LIST_LIMIT))))).scalars().all())
    return {
        "items": [_serialize_training_asset_source(row) for row in rows],
        "total": len(rows),
        "profiles": sorted(TRAINING_DATASET_PROFILES),
        "modalities": sorted(TRAINING_MODALITIES),
        "default_storage_location_id": TRAINING_DEFAULT_STORAGE_LOCATION_ID,
    }


async def list_training_samples(
    db: AsyncSession,
    user: User,
    *,
    dataset_profile: str | None = None,
    source_id: str | None = None,
    department: str | None = None,
    limit: int = TRAINING_SAMPLE_LIST_LIMIT,
) -> dict[str, Any]:
    stmt = select(TrainingSample).order_by(TrainingSample.created_at.desc(), TrainingSample.id.desc())
    if dataset_profile:
        stmt = stmt.where(TrainingSample.dataset_profile == _normalize_training_dataset_profile(dataset_profile))
    if source_id:
        stmt = stmt.where(TrainingSample.source_id == source_id)
    if department:
        _require_training_asset_read_access(user, department)
        stmt = stmt.where(TrainingSample.department == department)
    elif not _can_view_all_training_resources(user):
        user_department = _user_department(user)
        stmt = stmt.where(TrainingSample.department == user_department if user_department else false())
    rows = list((await db.execute(stmt.limit(max(1, min(limit, TRAINING_SAMPLE_LIST_LIMIT))))).scalars().all())
    return {"items": [_serialize_training_sample(row) for row in rows], "total": len(rows)}


async def list_training_dataset_versions(
    db: AsyncSession,
    user: User,
    *,
    dataset_profile: str | None = None,
    department: str | None = None,
    status: str | None = None,
    limit: int = TRAINING_DATASET_LIST_LIMIT,
) -> dict[str, Any]:
    stmt = select(TrainingDatasetVersion).order_by(TrainingDatasetVersion.created_at.desc(), TrainingDatasetVersion.id.desc())
    if dataset_profile:
        stmt = stmt.where(TrainingDatasetVersion.dataset_profile == _normalize_training_dataset_profile(dataset_profile))
    if status:
        stmt = stmt.where(TrainingDatasetVersion.status == _normalize_training_source_status(status, TRAINING_DATASET_VERSION_STATUSES, "ready"))
    if department:
        _require_training_asset_read_access(user, department)
        stmt = stmt.where(TrainingDatasetVersion.department == department)
    elif not _can_view_all_training_resources(user):
        user_department = _user_department(user)
        stmt = stmt.where(TrainingDatasetVersion.department == user_department if user_department else false())
    rows = list((await db.execute(stmt.limit(max(1, min(limit, TRAINING_DATASET_LIST_LIMIT))))).scalars().all())
    return {"items": [_serialize_training_dataset_version(row) for row in rows], "total": len(rows)}


async def get_training_dataset_version(
    db: AsyncSession,
    user: User,
    dataset_version_id: str,
    *,
    include_manifest: bool = False,
) -> dict[str, Any]:
    row = await db.get(TrainingDatasetVersion, dataset_version_id)
    if row is None:
        raise AppError("TRAINING_DATASET_VERSION_NOT_FOUND", 404)
    _require_training_asset_read_access(user, row.department)
    return _serialize_training_dataset_version(row, include_manifest=include_manifest)


async def get_training_dataset_version_automation(
    db: AsyncSession,
    user: User,
    dataset_version_id: str,
) -> dict[str, Any]:
    row = await db.get(TrainingDatasetVersion, dataset_version_id)
    if row is None:
        raise AppError("TRAINING_DATASET_VERSION_NOT_FOUND", 404)
    _require_training_asset_read_access(user, row.department)
    return {
        "dataset_version_id": row.id,
        "automation_status": _training_dataset_automation_status(row),
        "dataset_version": _serialize_training_dataset_version(row),
    }


async def training_dataset_version_manifest(
    db: AsyncSession,
    user: User,
    dataset_version_id: str,
) -> dict[str, Any]:
    row = await db.get(TrainingDatasetVersion, dataset_version_id)
    if row is None:
        raise AppError("TRAINING_DATASET_VERSION_NOT_FOUND", 404)
    _require_training_asset_read_access(user, row.department)
    manifest = row.manifest_json or {}
    records = manifest.get("records") if isinstance(manifest.get("records"), list) else []
    jsonl = "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for record in records)
    return {
        "dataset_version": _serialize_training_dataset_version(row),
        "manifest": manifest,
        "jsonl": jsonl,
        "jsonl_sha256": hashlib.sha256(jsonl.encode("utf-8")).hexdigest(),
        "media_policy": "reference_only",
        "credential_policy": "platform_only",
    }


def _training_dataset_version_gateway_dataset_id(row: TrainingDatasetVersion) -> str:
    name_segment = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(row.name or "training-dataset").strip())[:40].strip("-._:")
    sha_segment = str(row.manifest_sha256 or row.id or "").strip().lower()[:12]
    raw = f"sfds-{name_segment or 'training-dataset'}-{row.version or 'latest'}-{sha_segment}"
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-._:")[:120] or f"sfds-{row.id}"


def _training_dataset_version_gateway_package(row: TrainingDatasetVersion) -> dict[str, Any]:
    manifest = row.manifest_json if isinstance(row.manifest_json, dict) else {}
    records = manifest.get("records") if isinstance(manifest.get("records"), list) else []
    sample_ids = manifest.get("sample_ids") if isinstance(manifest.get("sample_ids"), list) else []
    return {
        "format": "training_dataset_manifest_reference",
        "source": "training_asset_registry",
        "dataset_ref": _training_manifest_uri(row.id),
        "dataset_version_id": row.id,
        "dataset_profile": row.dataset_profile,
        "modality": row.modality,
        "name": row.name,
        "version": row.version,
        "department": row.department,
        "org_unit_id": row.org_unit_id,
        "target_model_family": row.target_model_family,
        "manifest_hash": row.manifest_sha256,
        "manifest_sha256": row.manifest_sha256,
        "sample_count": int(row.sample_count or len(records)),
        "train_count": int(row.sample_count or len(records)),
        "eval_count": 0,
        "media_count": int(row.media_count or 0),
        "manifest": {
            key: value
            for key, value in manifest.items()
            if key not in {"records"}
        },
        "records": records,
        "lineage": {
            "source": "training_dataset_version",
            "training_dataset_version_id": row.id,
            "sample_ids": sample_ids[:TRAINING_DATASET_VERSION_MAX_SAMPLES],
        },
        "packing_version": TRAINING_DATASET_MANIFEST_SCHEMA_VERSION,
    }


async def _write_training_dataset_version_to_gateway(
    row: TrainingDatasetVersion,
    target_gateway_id: str,
    *,
    gateway_kind: str | None = None,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    package = _training_dataset_version_gateway_package(row)
    dataset_id = _training_dataset_version_gateway_dataset_id(row)
    client = AIClawClient(target_gateway_id, gateway_kind=gateway_kind)
    return await _write_training_dataset_package_to_source(
        client,
        dataset_id=dataset_id,
        package=package,
        timeout_seconds=timeout_seconds,
    )


async def sync_training_dataset_version(
    db: AsyncSession,
    user: User,
    dataset_version_id: str,
    payload: dict[str, Any] | None = None,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    if not _can_create_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to sync training dataset"})
    payload = payload or {}
    row = await db.get(TrainingDatasetVersion, dataset_version_id)
    if row is None:
        raise AppError("TRAINING_DATASET_VERSION_NOT_FOUND", 404)
    _require_training_department_access(user, row.department or _user_department(user))
    target_gateway_id = _clean_text(payload.get("target_gateway_id") or row.target_gateway_id or "training-primary", limit=80)
    from app.learning.models import LearningIngestionJob

    now = now_bjt()
    job_hash = hashlib.sha256(f"{row.id}:{row.manifest_sha256}:{target_gateway_id}".encode("utf-8")).hexdigest()[:32]
    job_id = f"tdsync_{job_hash}"
    existing = (
        await db.execute(select(LearningIngestionJob).where(LearningIngestionJob.job_id == job_id).limit(1))
    ).scalar_one_or_none()
    if existing is None:
        existing = LearningIngestionJob(
            job_id=job_id,
            event_id=None,
            artifact_id=None,
            sink_type="training_gateway_dataset",
            sink_id=target_gateway_id,
            department=row.department,
            org_unit_id=row.org_unit_id,
            skill_id=None,
            action="training_dataset_version_sync",
            status="pending",
            attempts=0,
            policy_json={
                "source": "training_asset_registry",
                "dataset_version_id": row.id,
                "dataset_profile": row.dataset_profile,
                "modality": row.modality,
                "manifest_uri": _training_manifest_uri(row.id),
                "manifest_sha256": row.manifest_sha256,
                "sample_count": row.sample_count,
                "media_count": row.media_count,
                "target_gateway_id": target_gateway_id,
                "format": "jsonl_manifest_reference",
                "media_policy": "reference_only",
                "credential_policy": "platform_only",
                "gb10_target": {
                    "gateway_id": target_gateway_id,
                    "label": "GB10 237 · NVIDIA GB10" if target_gateway_id == "training-primary" else target_gateway_id,
                },
            },
            created_by=str(getattr(user, "id", "") or "") or None,
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
        await db.flush()
    row.synced_sink_job_id = existing.job_id
    row.target_gateway_id = target_gateway_id
    timeout_seconds = max(60, min(_safe_int(payload.get("timeout_seconds")) or 1800, 3600))
    gateway_result: dict[str, Any] | None = None
    force = payload.get("force") is True
    target_instance = await db.get(OpenClawInstance, target_gateway_id)
    gateway_kind = str(getattr(target_instance, "bridge_gateway_kind", "") or "") or None
    if existing.status == "completed" and row.sync_status == "completed" and not force:
        gateway_result = {
            "dataset_id": (existing.policy_json or {}).get("gateway_dataset_id"),
            "dataset_dir": (existing.policy_json or {}).get("gateway_dataset_dir"),
            "dataset_path": (existing.policy_json or {}).get("gateway_dataset_path"),
            "sha256": (existing.policy_json or {}).get("gateway_sha256"),
            "size_bytes": (existing.policy_json or {}).get("gateway_size_bytes"),
            "cached": True,
        }
    elif bridge_registry.is_online(target_gateway_id):
        existing.status = "running"
        existing.attempts = int(existing.attempts or 0) + 1
        existing.started_at = existing.started_at or now
        existing.error = None
        row.sync_status = "running"
        row.updated_at = now
        await db.flush()
        try:
            gateway_result = await _write_training_dataset_version_to_gateway(
                row,
                target_gateway_id,
                gateway_kind=gateway_kind,
                timeout_seconds=timeout_seconds,
            )
            finished_at = now_bjt()
            existing.status = "completed"
            existing.finished_at = finished_at
            existing.sink_id = target_gateway_id
            existing.error = None
            existing.policy_json = {
                **(existing.policy_json or {}),
                "gateway_dataset_id": gateway_result.get("dataset_id"),
                "gateway_dataset_dir": gateway_result.get("dataset_dir"),
                "gateway_dataset_path": gateway_result.get("dataset_path"),
                "gateway_sha256": gateway_result.get("sha256"),
                "gateway_size_bytes": gateway_result.get("size_bytes"),
                "completed_at": isoformat_bjt(finished_at),
            }
            row.sync_status = "completed"
            row.updated_at = finished_at
        except Exception as exc:  # noqa: BLE001
            failed_at = now_bjt()
            existing.status = "failed"
            existing.error = _extract_error_message(exc)[:2000]
            existing.finished_at = failed_at
            row.sync_status = "failed"
            row.updated_at = failed_at
            await db.flush()
            if commit:
                await db.commit()
            if isinstance(exc, AppError):
                raise
            raise AppError(
                "TRAINING_DATASET_SYNC_FAILED",
                502,
                {"detail": _extract_error_message(exc), "target_gateway_id": target_gateway_id},
            ) from exc
    else:
        row.sync_status = existing.status if existing.status != "pending" else "pending"
        existing.error = f"bridge offline: {target_gateway_id}"
        row.updated_at = now
    await db.flush()
    if commit:
        await db.commit()
        await db.refresh(row)
    return {
        "ok": True,
        "dataset_version": _serialize_training_dataset_version(row),
        "training_sink": {
            "status": existing.status,
            "job_id": existing.job_id,
            "target_gateway_id": existing.sink_id,
            "dataset_version_id": row.id,
            "manifest_sha256": row.manifest_sha256,
            "gateway_result": gateway_result,
        },
    }


async def auto_run_training_dataset_version(
    db: AsyncSession,
    user: User,
    dataset_version_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    sync_result = await sync_training_dataset_version(
        db,
        user,
        dataset_version_id,
        {
            "target_gateway_id": payload.get("target_gateway_id") or TRAINING_PRIMARY_GATEWAY_IDS[0],
            "metadata": {"trigger": "dataset_version.auto_run"},
        },
    )
    return {
        "ok": True,
        "action": "dataset_version.auto_run",
        "sync": sync_result,
    }


def _learning_artifact_training_source_id(department: str | None, org_unit_id: str | None) -> str:
    scope = json.dumps(
        {"department": department or "", "org_unit_id": org_unit_id or ""},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"learning-artifacts:{hashlib.sha256(scope.encode('utf-8')).hexdigest()[:24]}"


def _compact_learning_artifact_training_content(artifact: Any) -> dict[str, Any]:
    summary = _clean_text(getattr(artifact, "summary", None), limit=4000)
    title = _clean_text(getattr(artifact, "title", None), limit=500)
    artifact_kind = _clean_text(getattr(artifact, "artifact_kind", None), limit=50)
    target_type = _clean_text(getattr(artifact, "target_type", None), limit=40)
    target_id = _clean_text(getattr(artifact, "target_id", None), limit=120)
    run_id = _clean_text(getattr(artifact, "run_id", None), limit=120)
    skill_id = _clean_text(getattr(artifact, "skill_id", None), limit=100)
    return {
        "instruction": "根据部门过程数据、动作结果和反馈，学习可复用的决策或回复模式。",
        "input": {
            "title": title,
            "summary": summary,
            "artifact_kind": artifact_kind,
            "target_type": target_type,
            "target_id": target_id,
            "skill_id": skill_id,
            "run_id": run_id,
        },
        "output": {
            "summary": summary or title or "",
            "training_signal": artifact_kind or "training_sample",
        },
        "lineage": {
            "source": "learning_artifact",
            "learning_artifact_id": getattr(artifact, "id", None),
            "learning_event_id": getattr(artifact, "event_id", None),
            "artifact_hash": getattr(artifact, "artifact_hash", None),
        },
    }


async def _promote_learning_artifacts_to_training_samples(
    db: AsyncSession,
    user: User,
    *,
    limit: int,
) -> dict[str, Any]:
    from app.learning.models import LearningArtifact

    clean_limit = max(1, min(_safe_int(limit) or 50000, 50000))
    batch_size = 1000
    base_stmt = (
        select(
            LearningArtifact.id,
            LearningArtifact.event_id,
            LearningArtifact.artifact_kind,
            LearningArtifact.artifact_hash,
            LearningArtifact.target_type,
            LearningArtifact.target_id,
            LearningArtifact.department,
            LearningArtifact.org_unit_id,
            LearningArtifact.skill_id,
            LearningArtifact.run_id,
            LearningArtifact.title,
            LearningArtifact.summary,
            LearningArtifact.labels_json,
            LearningArtifact.quality_score,
            LearningArtifact.sensitivity_level,
            LearningArtifact.updated_at,
        )
        .where(LearningArtifact.artifact_kind.in_(["training_sample", "eval_case"]))
        .where(LearningArtifact.status == "materialized")
        .where(LearningArtifact.sink_type == "training_sample")
        .where(~select(TrainingSample.id).where(TrainingSample.id == LearningArtifact.id).exists())
        .order_by(LearningArtifact.updated_at.asc(), LearningArtifact.id.asc())
    )
    if not _can_view_all_training_resources(user):
        department = _user_department(user)
        base_stmt = base_stmt.where(LearningArtifact.department == department if department else false())

    checked = 0
    created = 0
    deduped = 0
    source_ids_seen: set[str] = set()
    storage_location_id: str | None = None
    while checked < clean_limit:
        current_limit = min(batch_size, clean_limit - checked)
        artifacts = [
            SimpleNamespace(**row)
            for row in (await db.execute(base_stmt.limit(current_limit))).mappings().all()
        ]
        if not artifacts:
            break
        checked += len(artifacts)

        artifact_ids = [row.id for row in artifacts]
        existing_ids = {
            str(item)
            for item in (
                await db.execute(select(TrainingSample.id).where(TrainingSample.id.in_(artifact_ids)))
            ).scalars().all()
        }

        source_specs: dict[str, dict[str, str | None]] = {}
        for artifact in artifacts:
            department = _clean_text(
                artifact.department or (_user_department(user) if not _can_view_all_training_resources(user) else None),
                limit=100,
            )
            org_unit_id = _clean_text(artifact.org_unit_id, limit=50)
            source_id = _learning_artifact_training_source_id(department, org_unit_id)
            source_specs[source_id] = {"department": department, "org_unit_id": org_unit_id}

        source_rows = (
            await db.execute(
                select(TrainingAssetSource)
                .where(TrainingAssetSource.source_type == "learning_artifact")
                .where(TrainingAssetSource.source_id.in_(list(source_specs)))
            )
        ).scalars().all()
        sources_by_source_id = {row.source_id: row for row in source_rows}
        now = now_bjt()
        if len(sources_by_source_id) < len(source_specs):
            if storage_location_id is None:
                storage_location_id = (await ensure_training_default_storage_location(db, user, commit=False)).id
            for source_id, spec in source_specs.items():
                if source_id in sources_by_source_id:
                    continue
                source = TrainingAssetSource(
                    id=_training_asset_source_id(),
                    source_type="learning_artifact",
                    source_id=source_id,
                    title=f"学习闭环训练样本 · {spec.get('department') or '全局'}",
                    department=spec.get("department"),
                    org_unit_id=spec.get("org_unit_id"),
                    owner_user_id=str(getattr(user, "id", "") or "") or None,
                    modality="text",
                    storage_location_id=storage_location_id,
                    storage_backend=TRAINING_DEFAULT_STORAGE_BACKEND,
                    storage_ref=f"learning://artifacts/{source_id}",
                    sample_count=0,
                    sensitivity_level="internal",
                    status="active",
                    policy_result_json={"credential_policy": "platform_only"},
                    metadata_json={"source": "learning_artifact", "auto_promoted": True},
                    created_by=str(getattr(user, "id", "") or "") or None,
                    created_at=now,
                    updated_at=now,
                )
                db.add(source)
                sources_by_source_id[source_id] = source
        await db.flush()
        source_ids_seen.update(sources_by_source_id)

        sample_values: list[dict[str, Any]] = []
        hash_values: set[str] = set()
        source_db_ids: set[str] = set()
        candidates: list[tuple[LearningArtifact, TrainingAssetSource, dict[str, Any], list[Any], str]] = []
        for artifact in artifacts:
            department = _clean_text(
                artifact.department or (_user_department(user) if not _can_view_all_training_resources(user) else None),
                limit=100,
            )
            org_unit_id = _clean_text(artifact.org_unit_id, limit=50)
            source = sources_by_source_id[_learning_artifact_training_source_id(department, org_unit_id)]
            labels = _sanitize_training_result_value(artifact.labels_json if isinstance(artifact.labels_json, list) else [])
            safe_content = _compact_learning_artifact_training_content(artifact)
            safe_labels = labels if isinstance(labels, list) else []
            sample_hash = _json_hash({
                "dataset_profile": "text_sft_v1",
                "content": safe_content,
                "media_refs": [],
                "labels": safe_labels,
            })
            candidates.append((artifact, source, safe_content, safe_labels, sample_hash))
            hash_values.add(sample_hash)
            source_db_ids.add(source.id)

        existing_hash_keys: set[tuple[str, str]] = set()
        if source_db_ids and hash_values:
            existing_rows = (
                await db.execute(
                    select(TrainingSample.source_id, TrainingSample.sample_hash)
                    .where(TrainingSample.source_id.in_(list(source_db_ids)))
                    .where(TrainingSample.dataset_profile == "text_sft_v1")
                    .where(TrainingSample.sample_hash.in_(list(hash_values)))
                )
            ).all()
            existing_hash_keys = {(str(source_id), str(sample_hash)) for source_id, sample_hash in existing_rows}

        created_by_source: dict[str, int] = {}
        for artifact, source, safe_content, safe_labels, sample_hash in candidates:
            if artifact.id in existing_ids or (source.id, sample_hash) in existing_hash_keys:
                deduped += 1
                continue
            quality_score = max(0.0, min(float(_safe_number(artifact.quality_score)), 1.0))
            sample_values.append({
                "id": artifact.id,
                "source_id": source.id,
                "dataset_profile": "text_sft_v1",
                "modality": "text",
                "sample_hash": sample_hash,
                "content_json": safe_content,
                "media_refs_json": [],
                "labels_json": safe_labels,
                "quality_score": quality_score,
                "department": source.department,
                "org_unit_id": source.org_unit_id,
                "skill_id": _clean_text(artifact.skill_id, limit=100),
                "project_id": None,
                "project_run_id": None,
                "sensitivity_level": _clean_text(artifact.sensitivity_level or source.sensitivity_level, limit=20) or "internal",
                "status": "ready",
                "metadata_json": {
                    "source": "learning_artifact",
                    "learning_artifact_id": artifact.id,
                    "learning_event_id": artifact.event_id,
                    "artifact_kind": artifact.artifact_kind,
                    "artifact_hash": artifact.artifact_hash,
                    "target_type": artifact.target_type,
                    "target_id": artifact.target_id,
                    "run_id": artifact.run_id,
                    "title": artifact.title,
                    "summary": artifact.summary,
                },
                "created_by": str(getattr(user, "id", "") or "") or None,
                "created_at": now,
                "updated_at": now,
            })
            created += 1
            created_by_source[source.id] = created_by_source.get(source.id, 0) + 1
            existing_hash_keys.add((source.id, sample_hash))
            existing_ids.add(artifact.id)

        if sample_values:
            await db.execute(TrainingSample.__table__.insert(), sample_values)
        for source in sources_by_source_id.values():
            increment = created_by_source.get(source.id, 0)
            if increment <= 0:
                continue
            source.sample_count = (source.sample_count or 0) + increment
            source.updated_at = now
        await db.commit()
        await asyncio.sleep(0)

    return {
        "checked": checked,
        "promoted": created,
        "deduped": deduped,
        "source_count": len(source_ids_seen),
        "limit": clean_limit,
    }


async def _auto_create_training_datasets_from_samples(
    db: AsyncSession,
    user: User,
    *,
    max_datasets: int,
    sample_limit: int,
) -> dict[str, Any]:
    clean_max = max(0, min(_safe_int(max_datasets) or 20, 100))
    clean_sample_limit = max(1, min(_safe_int(sample_limit) or TRAINING_DATASET_VERSION_MAX_SAMPLES, TRAINING_DATASET_VERSION_MAX_SAMPLES))
    if clean_max <= 0:
        return {"seen_departments": 0, "created": 0, "skipped": 0, "limit": clean_sample_limit}

    sample_stmt = (
        select(
            TrainingSample.department,
            func.count(TrainingSample.id),
            func.max(TrainingSample.updated_at),
        )
        .where(TrainingSample.dataset_profile == "text_sft_v1")
        .where(TrainingSample.modality.in_(["text", "mixed"]))
        .where(TrainingSample.status == "ready")
        .group_by(TrainingSample.department)
        .order_by(func.max(TrainingSample.updated_at).desc())
        .limit(clean_max)
    )
    if not _can_view_all_training_resources(user):
        department = _user_department(user)
        sample_stmt = sample_stmt.where(TrainingSample.department == department if department else false())
    sample_groups = list((await db.execute(sample_stmt)).all())
    if not sample_groups:
        return {"seen_departments": 0, "created": 0, "skipped": 0, "limit": clean_sample_limit}

    created = 0
    skipped = 0
    created_items: list[dict[str, Any]] = []
    for department, sample_count, latest_sample_at in sample_groups:
        department_value = _clean_text(department, limit=100)
        existing_stmt = (
            select(TrainingDatasetVersion)
            .where(TrainingDatasetVersion.dataset_profile == "text_sft_v1")
            .where(TrainingDatasetVersion.status.in_(["ready", "approved"]))
            .order_by(TrainingDatasetVersion.created_at.desc(), TrainingDatasetVersion.id.desc())
            .limit(1)
        )
        if department_value:
            existing_stmt = existing_stmt.where(TrainingDatasetVersion.department == department_value)
        else:
            existing_stmt = existing_stmt.where(TrainingDatasetVersion.department.is_(None))
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        expected_count = min(int(sample_count or 0), clean_sample_limit)
        if existing and int(existing.sample_count or 0) >= expected_count:
            skipped += 1
            continue
        sample_ids: list[str] | None = None
        if department_value is None:
            sample_ids = list(
                (
                    await db.execute(
                        select(TrainingSample.id)
                        .where(TrainingSample.dataset_profile == "text_sft_v1")
                        .where(TrainingSample.modality.in_(["text", "mixed"]))
                        .where(TrainingSample.status == "ready")
                        .where(TrainingSample.department.is_(None))
                        .order_by(TrainingSample.created_at.asc(), TrainingSample.id.asc())
                        .limit(clean_sample_limit)
                    )
                ).scalars().all()
            )
            if not sample_ids:
                skipped += 1
                continue
        result = await create_training_dataset_version(
            db,
            user,
            {
                "name": f"learning-artifacts-{department_value or 'company'}",
                "dataset_profile": "text_sft_v1",
                "modality": "text",
                "department": department_value,
                "sample_ids": sample_ids,
                "limit": clean_sample_limit,
                "status": "ready",
                "target_gateway_id": TRAINING_PRIMARY_GATEWAY_IDS[0],
                "auto_sync": False,
                "company_scope": department_value is None,
            },
            commit=True,
        )
        created += 1
        created_items.append({
            "dataset_version_id": _safe_dict(result.get("dataset_version")).get("id"),
            "department": department_value,
            "sample_count": expected_count,
            "latest_sample_at": isoformat_bjt(latest_sample_at),
        })
    return {
        "seen_departments": len(sample_groups),
        "created": created,
        "skipped": skipped,
        "limit": clean_sample_limit,
        "items": created_items[:20],
    }


TRAINING_DEPARTMENT_FLOW_STAGES = (
    ("data", "入口"),
    ("clean", "样本"),
    ("dataset", "数据集"),
    ("gb10_sync", "GB10 同步"),
    ("route", "自动寻路"),
    ("train", "训练"),
    ("eval", "评估"),
    ("deploy", "Mac236 部署"),
    ("run", "运行"),
)


def _training_flow_stage_status(
    *,
    count: int,
    running: int = 0,
    failed: int = 0,
    upstream_ready: bool = False,
) -> str:
    if running > 0:
        return "running"
    if failed > 0 and count <= 0:
        return "blocked"
    if count > 0:
        return "completed"
    return "pending" if upstream_ready else "empty"


def _training_flow_stage(
    key: str,
    label: str,
    *,
    count: int,
    detail: str,
    status: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "count": max(0, int(count or 0)),
        "status": status,
        "detail": detail,
    }


def _training_entry_source_bucket(source_type: Any) -> str:
    value = str(source_type or "").strip().lower()
    if value in {"sdk_upload", "project_run", "project_run_asset", "project_ingress_event"}:
        return "sdk"
    if value in {"learning_artifact", "execution_artifact"}:
        return "learning"
    return "manual"


def _training_sample_modality_bucket(modality: Any) -> str:
    value = str(modality or "text").strip().lower()
    if value == "image":
        return "image"
    if value in {"image_text", "image+text", "image-text", "vision", "vlm", "video_frame", "mixed"}:
        return "image_text"
    return "text"


def _training_department_flow_add_sample_modality(item: dict[str, Any], modality: Any, count: int) -> None:
    count_int = max(0, int(count or 0))
    if count_int <= 0:
        return
    bucket = _training_sample_modality_bucket(modality)
    item["metrics"][f"sample_{bucket}"] += count_int


def _training_department_flow_item(department: str) -> dict[str, Any]:
    clean_department = _clean_text(department, limit=100) or "未归属"
    return {
        "id": f"dept:{clean_department}",
        "department": clean_department,
        "metrics": {
            "raw_events": 0,
            "artifact_total": 0,
            "asset_sources": 0,
            "asset_samples": 0,
            "entry_sdk": 0,
            "entry_learning": 0,
            "entry_learning_sources": 0,
            "entry_manual": 0,
            "entry_tasks": 0,
            "entry_datasets": 0,
            "sample_text": 0,
            "sample_image": 0,
            "sample_image_text": 0,
            "cleaned_samples": 0,
            "training_samples": 0,
            "eval_samples": 0,
            "dataset_versions": 0,
            "dataset_samples": 0,
            "datasets_synced": 0,
            "datasets_unsynced": 0,
            "jobs_total": 0,
            "jobs_target_gb10": 0,
            "jobs_target_fallback": 0,
            "jobs_unrouted": 0,
            "jobs_awaiting": 0,
            "jobs_queued": 0,
            "jobs_training": 0,
            "jobs_evaluating": 0,
            "jobs_running": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "models": 0,
            "eval_completed": 0,
            "eval_failed": 0,
            "deployments": 0,
            "deployments_canary": 0,
            "deployments_active": 0,
            "deployments_failed": 0,
            "online_gateways": 0,
        },
        "latest_at": None,
    }


def _training_department_flow_touch(item: dict[str, Any], value: datetime | None) -> None:
    if value is None:
        return
    current_raw = item.get("_latest_at_raw")
    if current_raw is None or value > current_raw:
        item["_latest_at_raw"] = value
        item["latest_at"] = isoformat_bjt(value)


def _training_department_flow_stage_payload(item: dict[str, Any]) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    metrics = _safe_dict(item.get("metrics"))
    raw_events = _safe_int(metrics.get("raw_events"))
    artifact_total = _safe_int(metrics.get("artifact_total"))
    entry_sdk = _safe_int(metrics.get("entry_sdk"))
    entry_learning = max(_safe_int(metrics.get("entry_learning")), _safe_int(metrics.get("entry_learning_sources")))
    entry_manual = _safe_int(metrics.get("entry_manual"))
    sample_text = _safe_int(metrics.get("sample_text"))
    sample_image = _safe_int(metrics.get("sample_image"))
    sample_image_text = _safe_int(metrics.get("sample_image_text"))
    cleaned_samples = _safe_int(metrics.get("cleaned_samples"))
    training_samples = _safe_int(metrics.get("training_samples"))
    eval_samples = _safe_int(metrics.get("eval_samples"))
    dataset_versions = _safe_int(metrics.get("dataset_versions"))
    dataset_samples = _safe_int(metrics.get("dataset_samples"))
    datasets_synced = _safe_int(metrics.get("datasets_synced"))
    datasets_unsynced = _safe_int(metrics.get("datasets_unsynced"))
    jobs_total = _safe_int(metrics.get("jobs_total"))
    jobs_target_gb10 = _safe_int(metrics.get("jobs_target_gb10"))
    jobs_target_fallback = _safe_int(metrics.get("jobs_target_fallback"))
    jobs_unrouted = _safe_int(metrics.get("jobs_unrouted"))
    jobs_awaiting = _safe_int(metrics.get("jobs_awaiting"))
    jobs_queued = _safe_int(metrics.get("jobs_queued"))
    jobs_training = _safe_int(metrics.get("jobs_training"))
    jobs_evaluating = _safe_int(metrics.get("jobs_evaluating"))
    jobs_running = _safe_int(metrics.get("jobs_running"))
    jobs_completed = _safe_int(metrics.get("jobs_completed"))
    jobs_failed = _safe_int(metrics.get("jobs_failed"))
    eval_completed = _safe_int(metrics.get("eval_completed"))
    eval_failed = _safe_int(metrics.get("eval_failed"))
    deployments = _safe_int(metrics.get("deployments"))
    deployments_canary = _safe_int(metrics.get("deployments_canary"))
    deployments_active = _safe_int(metrics.get("deployments_active"))
    deployments_failed = _safe_int(metrics.get("deployments_failed"))

    entry_total = entry_sdk + entry_learning + entry_manual
    sample_modality_total = sample_text + sample_image + sample_image_text
    sample_text_detail = sample_text if sample_modality_total > 0 else cleaned_samples
    data_count = max(raw_events, artifact_total, dataset_samples, entry_total)
    clean_count = max(cleaned_samples, sample_modality_total)
    dataset_count = dataset_versions
    gb10_sync_count = datasets_synced
    route_count = jobs_target_gb10
    training_active = max(jobs_training + jobs_evaluating, jobs_running - jobs_queued)
    training_inflight = jobs_awaiting + jobs_queued + training_active
    deploy_count = deployments
    eval_count = eval_completed
    run_count = deployments_active
    stages = [
        _training_flow_stage(
            "data",
            "入口",
            count=data_count,
            detail=f"{entry_sdk} SDK / {entry_learning} 学习闭环 / {entry_manual} 人工",
            status=_training_flow_stage_status(count=data_count),
        ),
        _training_flow_stage(
            "clean",
            "样本",
            count=clean_count,
            detail=f"{sample_text_detail} 文字 / {sample_image} 图片 / {sample_image_text} 图文",
            status=_training_flow_stage_status(count=clean_count, upstream_ready=data_count > 0),
        ),
        _training_flow_stage(
            "dataset",
            "数据集",
            count=dataset_count,
            detail=f"{dataset_versions} 版本化 manifest / {datasets_unsynced} 待同步",
            status=_training_flow_stage_status(count=dataset_count, upstream_ready=clean_count > 0),
        ),
        _training_flow_stage(
            "gb10_sync",
            "GB10 同步",
            count=gb10_sync_count,
            detail=f"{datasets_synced} 同步到 237 / {datasets_unsynced} 待同步",
            status=_training_flow_stage_status(count=gb10_sync_count, upstream_ready=dataset_count > 0),
        ),
        _training_flow_stage(
            "route",
            "自动寻路",
            count=route_count,
            detail=f"{jobs_target_gb10} 指向 GB10 / {jobs_target_fallback} 兜底 GPU / {jobs_unrouted} 待寻路",
            status=_training_flow_stage_status(
                count=route_count,
                upstream_ready=gb10_sync_count > 0,
            ),
        ),
        _training_flow_stage(
            "train",
            "训练",
            count=training_inflight,
            detail=f"{jobs_awaiting} 自动审核 / {training_active} 训练中",
            status=_training_flow_stage_status(
                count=jobs_completed,
                running=jobs_running,
                failed=jobs_failed,
                upstream_ready=route_count > 0,
            ),
        ),
        _training_flow_stage(
            "eval",
            "评估",
            count=eval_count,
            detail=f"{eval_completed} 通过 / {eval_failed} 失败",
            status=_training_flow_stage_status(
                count=eval_completed,
                failed=eval_failed,
                upstream_ready=jobs_completed > 0 or training_inflight > 0,
            ),
        ),
        _training_flow_stage(
            "deploy",
            "Mac236 部署",
            count=deploy_count,
            detail=f"{deployments} 内网自动部署 / {deployments_canary} 灰度",
            status=_training_flow_stage_status(
                count=deploy_count,
                running=deployments_canary,
                failed=deployments_failed,
                upstream_ready=eval_completed > 0 or jobs_completed > 0,
            ),
        ),
        _training_flow_stage(
            "run",
            "运行",
            count=run_count,
            detail=f"{deployments_active} active / {deployments_canary} 灰度",
            status=_training_flow_stage_status(
                count=deployments_active,
                running=deployments_canary,
                failed=deployments_failed,
                upstream_ready=eval_completed > 0 or deploy_count > 0,
            ),
        ),
    ]
    current = next((stage for stage in stages if stage["status"] not in {"completed"}), stages[-1])
    blockers: list[dict[str, Any]] = []
    if data_count <= 0:
        blockers.append({"key": "no_process_data", "label": "没有可训练过程数据", "severity": "warn"})
    if jobs_failed > 0:
        blockers.append({"key": "training_failed", "label": "存在失败训练任务", "severity": "error", "count": jobs_failed})
    if eval_failed > 0:
        blockers.append({"key": "evaluation_failed", "label": "存在未通过评估", "severity": "error", "count": eval_failed})
    if deployments_failed > 0:
        blockers.append({"key": "deployment_failed", "label": "存在部署失败/驳回", "severity": "error", "count": deployments_failed})
    if dataset_versions > 0 and datasets_synced <= 0:
        blockers.append({"key": "dataset_not_synced", "label": "数据集尚未同步到训练网关", "severity": "warn"})
    if jobs_total > 0 and jobs_target_gb10 <= 0:
        blockers.append({"key": "no_gb10_route", "label": "没有训练任务指向 GB10 237", "severity": "warn"})
    if jobs_target_fallback > 0:
        blockers.append({
            "key": "fallback_training_route",
            "label": "存在训练任务走兜底 GPU",
            "severity": "warn",
            "count": jobs_target_fallback,
        })
    return stages, str(current.get("key") or "data"), blockers


async def _training_department_process_flow(
    db: AsyncSession,
    user: User,
    *,
    resources: list[dict[str, Any]],
    window_days: int = 7,
) -> dict[str, Any]:
    """Return department-level counters for 入口→样本→数据集→GB10同步→自动寻路→训练→评估→Mac236部署→运行.

    The payload is intentionally aggregate-only; raw process records and sample
    content stay in Learning/Training storage.
    """
    from app.learning.models import LearningArtifact, LearningEvent

    bounded_window_days = max(1, min(window_days, 3650))
    cutoff = now_bjt() - timedelta(days=bounded_window_days)
    scoped_department = _user_department(user)
    can_view_all = _can_view_all_training_resources(user)
    departments: dict[str, dict[str, Any]] = {}

    def ensure(department: Any) -> dict[str, Any]:
        name = _clean_text(department, limit=100) or "未归属"
        if name not in departments:
            departments[name] = _training_department_flow_item(name)
        return departments[name]

    def scope_condition(column: Any) -> Any | None:
        if can_view_all:
            return None
        return column == scoped_department if scoped_department else false()

    event_conditions: list[Any] = [LearningEvent.created_at >= cutoff]
    event_scope = scope_condition(LearningEvent.department)
    if event_scope is not None:
        event_conditions.append(event_scope)
    event_rows = (
        await db.execute(
            select(
                LearningEvent.department,
                func.count(LearningEvent.id),
                func.max(LearningEvent.created_at),
            )
            .where(*event_conditions)
            .group_by(LearningEvent.department)
        )
    ).all()
    for department, count, latest_at in event_rows:
        item = ensure(department)
        item["metrics"]["raw_events"] += int(count or 0)
        _training_department_flow_touch(item, latest_at)

    artifact_conditions: list[Any] = [
        LearningArtifact.artifact_kind.in_(["training_sample", "eval_case"]),
        LearningArtifact.sink_type == "training_sample",
        LearningArtifact.updated_at >= cutoff,
    ]
    artifact_scope = scope_condition(LearningArtifact.department)
    if artifact_scope is not None:
        artifact_conditions.append(artifact_scope)
    artifact_rows = (
        await db.execute(
            select(
                LearningArtifact.department,
                LearningArtifact.artifact_kind,
                LearningArtifact.status,
                func.count(LearningArtifact.id),
                func.max(LearningArtifact.updated_at),
            )
            .where(*artifact_conditions)
            .group_by(LearningArtifact.department, LearningArtifact.artifact_kind, LearningArtifact.status)
        )
    ).all()
    for department, artifact_kind, status, count, latest_at in artifact_rows:
        item = ensure(department)
        count_int = int(count or 0)
        item["metrics"]["artifact_total"] += count_int
        item["metrics"]["entry_learning"] += count_int
        if status == "materialized":
            item["metrics"]["cleaned_samples"] += count_int
            if artifact_kind == "eval_case":
                item["metrics"]["eval_samples"] += count_int
            else:
                item["metrics"]["training_samples"] += count_int
        _training_department_flow_touch(item, latest_at)

    source_conditions: list[Any] = [
        TrainingAssetSource.updated_at >= cutoff,
        TrainingAssetSource.status == "active",
    ]
    source_scope = scope_condition(TrainingAssetSource.department)
    if source_scope is not None:
        source_conditions.append(source_scope)
    source_rows = (
        await db.execute(
            select(
                TrainingAssetSource.department,
                TrainingAssetSource.source_type,
                TrainingAssetSource.modality,
                func.count(TrainingAssetSource.id),
                func.sum(TrainingAssetSource.sample_count),
                func.max(TrainingAssetSource.updated_at),
            )
            .where(*source_conditions)
            .group_by(TrainingAssetSource.department, TrainingAssetSource.source_type, TrainingAssetSource.modality)
        )
    ).all()
    for department, source_type, modality, count, sample_count, latest_at in source_rows:
        item = ensure(department)
        count_int = int(count or 0)
        sample_int = int(sample_count or 0)
        entry_count = max(count_int, sample_int)
        bucket = _training_entry_source_bucket(source_type)
        item["metrics"]["asset_sources"] += count_int
        item["metrics"]["asset_samples"] += sample_int
        if bucket != "learning":
            _training_department_flow_add_sample_modality(item, modality, entry_count)
        if bucket == "learning":
            item["metrics"]["entry_learning_sources"] += entry_count
        elif bucket == "sdk":
            item["metrics"]["entry_sdk"] += entry_count
        else:
            item["metrics"]["entry_manual"] += entry_count
        _training_department_flow_touch(item, latest_at)

    dataset_conditions: list[Any] = [TrainingDatasetVersion.updated_at >= cutoff]
    dataset_scope = scope_condition(TrainingDatasetVersion.department)
    if dataset_scope is not None:
        dataset_conditions.append(dataset_scope)
    dataset_rows = (
        await db.execute(
            select(
                TrainingDatasetVersion.department,
                TrainingDatasetVersion.sync_status,
                TrainingDatasetVersion.modality,
                func.count(TrainingDatasetVersion.id),
                func.sum(TrainingDatasetVersion.sample_count),
                func.max(TrainingDatasetVersion.updated_at),
            )
            .where(*dataset_conditions)
            .group_by(TrainingDatasetVersion.department, TrainingDatasetVersion.sync_status, TrainingDatasetVersion.modality)
        )
    ).all()
    for department, sync_status, modality, count, sample_count, latest_at in dataset_rows:
        item = ensure(department)
        count_int = int(count or 0)
        sample_int = int(sample_count or 0)
        item["metrics"]["dataset_versions"] += count_int
        item["metrics"]["entry_datasets"] += count_int
        item["metrics"]["dataset_samples"] += sample_int
        if sync_status == "completed":
            item["metrics"]["datasets_synced"] += count_int
        else:
            item["metrics"]["datasets_unsynced"] += count_int
        _training_department_flow_touch(item, latest_at)

    sample_conditions: list[Any] = [
        TrainingSample.updated_at >= cutoff,
        TrainingSample.status == "ready",
    ]
    sample_scope = scope_condition(TrainingSample.department)
    if sample_scope is not None:
        sample_conditions.append(sample_scope)
    sample_rows = (
        await db.execute(
            select(
                TrainingSample.department,
                TrainingSample.modality,
                func.count(TrainingSample.id),
                func.max(TrainingSample.updated_at),
            )
            .where(*sample_conditions)
            .group_by(TrainingSample.department, TrainingSample.modality)
        )
    ).all()
    for department, modality, count, latest_at in sample_rows:
        item = ensure(department)
        _training_department_flow_add_sample_modality(item, modality, int(count or 0))
        _training_department_flow_touch(item, latest_at)

    job_conditions: list[Any] = [TrainingJob.updated_at >= cutoff]
    job_scope = scope_condition(TrainingJob.department)
    if job_scope is not None:
        job_conditions.append(job_scope)
    job_rows = (
        await db.execute(
            select(
                TrainingJob.department,
                TrainingJob.status,
                TrainingJob.target_gateway_id,
                func.count(TrainingJob.id),
                func.max(TrainingJob.updated_at),
            )
            .where(*job_conditions)
            .group_by(TrainingJob.department, TrainingJob.status, TrainingJob.target_gateway_id)
        )
    ).all()
    for department, status, target_gateway_id, count, latest_at in job_rows:
        item = ensure(department)
        count_int = int(count or 0)
        item["metrics"]["jobs_total"] += count_int
        item["metrics"]["entry_tasks"] += count_int
        gateway_id = str(target_gateway_id or "").strip()
        if gateway_id in TRAINING_PRIMARY_GATEWAY_IDS:
            item["metrics"]["jobs_target_gb10"] += count_int
        elif gateway_id:
            item["metrics"]["jobs_target_fallback"] += count_int
        else:
            item["metrics"]["jobs_unrouted"] += count_int
        if status == "awaiting_review":
            item["metrics"]["jobs_awaiting"] += count_int
        elif status == "queued":
            item["metrics"]["jobs_queued"] += count_int
        elif status == "running":
            item["metrics"]["jobs_training"] += count_int
        elif status == "evaluating":
            item["metrics"]["jobs_evaluating"] += count_int
        elif status == "unknown":
            item["metrics"]["jobs_training"] += count_int
        if status in {"queued", "running", "evaluating", "unknown"}:
            item["metrics"]["jobs_running"] += count_int
        if status == "completed":
            item["metrics"]["jobs_completed"] += count_int
            item["metrics"]["models"] += count_int
        if status == "failed":
            item["metrics"]["jobs_failed"] += count_int
        _training_department_flow_touch(item, latest_at)

    eval_task_conditions: list[Any] = [TrainingJobTask.updated_at >= cutoff]
    eval_task_scope = scope_condition(TrainingJob.department)
    if eval_task_scope is not None:
        eval_task_conditions.append(eval_task_scope)
    eval_task_rows = (
        await db.execute(
            select(
                TrainingJob.department,
                TrainingJobTask.status,
                TrainingJobTask.metrics_json,
                TrainingJobTask.updated_at,
            )
            .join(TrainingJob, TrainingJob.id == TrainingJobTask.job_id)
            .where(*eval_task_conditions)
            .order_by(TrainingJobTask.updated_at.desc())
            .limit(5000)
        )
    ).all()
    for department, status, metrics_json, latest_at in eval_task_rows:
        metrics = metrics_json if isinstance(metrics_json, dict) else {}
        if metrics.get("op") != "training.evaluate":
            continue
        item = ensure(department)
        if status == "completed" and metrics.get("passed") is not False:
            item["metrics"]["eval_completed"] += 1
        elif status == "failed" or metrics.get("passed") is False:
            item["metrics"]["eval_failed"] += 1
        _training_department_flow_touch(item, latest_at)

    deployment_conditions: list[Any] = [TrainingModelDeployment.updated_at >= cutoff]
    deployment_scope = scope_condition(TrainingModelDeployment.department)
    if deployment_scope is not None:
        deployment_conditions.append(deployment_scope)
    deployment_rows = (
        await db.execute(
            select(
                TrainingModelDeployment.department,
                TrainingModelDeployment.status,
                func.count(TrainingModelDeployment.id),
                func.max(TrainingModelDeployment.updated_at),
            )
            .where(*deployment_conditions)
            .group_by(TrainingModelDeployment.department, TrainingModelDeployment.status)
        )
    ).all()
    for department, status, count, latest_at in deployment_rows:
        item = ensure(department)
        count_int = int(count or 0)
        item["metrics"]["deployments"] += count_int
        item["metrics"]["models"] = max(
            _safe_int(item["metrics"].get("models")),
            _safe_int(item["metrics"].get("jobs_completed")),
            _safe_int(item["metrics"].get("deployments")),
        )
        if status == "active":
            item["metrics"]["deployments_active"] += count_int
        elif status == "canary":
            item["metrics"]["deployments_canary"] += count_int
        elif status in {"failed", "rejected"}:
            item["metrics"]["deployments_failed"] += count_int
        if status in {"evaluated", "canary", "active"}:
            item["metrics"]["eval_completed"] += count_int
        _training_department_flow_touch(item, latest_at)

    for resource in resources:
        if not isinstance(resource, dict) or not resource.get("online"):
            continue
        item = ensure(resource.get("department"))
        item["metrics"]["online_gateways"] += 1
        active_count = _safe_int(resource.get("active_training_jobs_count"))
        if active_count > 0:
            item["metrics"]["jobs_running"] += active_count

    flow_items: list[dict[str, Any]] = []
    for item in departments.values():
        metrics = _safe_dict(item.get("metrics"))
        metrics["entry_learning"] = max(
            _safe_int(metrics.get("entry_learning")),
            _safe_int(metrics.get("entry_learning_sources")),
        )
        stages, current_stage, blockers = _training_department_flow_stage_payload(item)
        completed_weight = sum(
            1.0 if stage["status"] == "completed" else (0.5 if stage["status"] == "running" else 0.0)
            for stage in stages
        )
        item["stages"] = stages
        item["current_stage"] = current_stage
        item["current_stage_label"] = next((stage["label"] for stage in stages if stage["key"] == current_stage), "入口")
        item["coverage"] = round(max(0.0, min(100.0, completed_weight / max(len(stages), 1) * 100)), 1)
        item["blockers"] = blockers
        item.pop("_latest_at_raw", None)
        flow_items.append(item)

    flow_items.sort(key=lambda item: (
        -_safe_int(_safe_dict(item.get("metrics")).get("cleaned_samples")),
        str(item.get("department") or ""),
    ))
    summary_metrics: dict[str, int] = {}
    for item in flow_items:
        for key, value in _safe_dict(item.get("metrics")).items():
            summary_metrics[key] = summary_metrics.get(key, 0) + _safe_int(value)
    return {
        "window_days": bounded_window_days,
        "stage_order": [
            {"key": key, "label": label}
            for key, label in TRAINING_DEPARTMENT_FLOW_STAGES
        ],
        "summary": {
            **summary_metrics,
            "department_count": len(flow_items),
            "stage_counts": {
                "data": max(
                    summary_metrics.get("raw_events", 0),
                    summary_metrics.get("artifact_total", 0),
                    summary_metrics.get("dataset_samples", 0),
                    summary_metrics.get("asset_samples", 0),
                    summary_metrics.get("entry_sdk", 0)
                    + summary_metrics.get("entry_learning", 0)
                    + summary_metrics.get("entry_manual", 0),
                ),
                "clean": max(
                    summary_metrics.get("cleaned_samples", 0),
                    summary_metrics.get("sample_text", 0)
                    + summary_metrics.get("sample_image", 0)
                    + summary_metrics.get("sample_image_text", 0),
                ),
                "dataset": summary_metrics.get("dataset_versions", 0),
                "gb10_sync": summary_metrics.get("datasets_synced", 0),
                "route": summary_metrics.get("jobs_target_gb10", 0),
                "train": (
                    summary_metrics.get("jobs_awaiting", 0)
                    + summary_metrics.get("jobs_queued", 0)
                    + max(
                        summary_metrics.get("jobs_training", 0) + summary_metrics.get("jobs_evaluating", 0),
                        summary_metrics.get("jobs_running", 0) - summary_metrics.get("jobs_queued", 0),
                    )
                ),
                "eval": summary_metrics.get("eval_completed", 0),
                "deploy": summary_metrics.get("deployments", 0),
                "run": summary_metrics.get("deployments_active", 0),
            },
        },
        "departments": flow_items[:50],
        "raw_payload_returned": False,
    }


async def get_training_automation_status(db: AsyncSession, user: User, *, window_days: int = 7) -> dict[str, Any]:
    resources = await list_training_resources(db, user)
    items = resources.get("items") if isinstance(resources.get("items"), list) else []
    by_id = {str(item.get("id") or ""): item for item in items if isinstance(item, dict)}
    gb10 = by_id.get(TRAINING_PRIMARY_GATEWAY_IDS[0])
    mac236 = by_id.get(TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID)
    dataset_versions = await list_training_dataset_versions(db, user, limit=200)
    dataset_items = dataset_versions.get("items") if isinstance(dataset_versions.get("items"), list) else []
    jobs = await list_training_jobs(db, user)
    job_items = jobs.get("items") if isinstance(jobs.get("items"), list) else []
    department_flow = await _training_department_process_flow(db, user, resources=items, window_days=window_days)
    blockers: list[dict[str, Any]] = []
    if not gb10:
        blockers.append({"key": "gb10_missing", "label": "GB10 237 未登记", "severity": "error"})
    elif not gb10.get("online"):
        blockers.append({"key": "gb10_offline", "label": "GB10 237 离线", "severity": "error"})
    if gb10:
        training = gb10.get("training") if isinstance(gb10.get("training"), dict) else {}
        tasks = training.get("supported_tasks") if isinstance(training.get("supported_tasks"), list) else []
        if "qlora" not in {str(item).lower() for item in tasks}:
            blockers.append({"key": "gb10_training_capability", "label": "GB10 237 未上报 QLoRA 能力", "severity": "warn"})
    if not mac236:
        blockers.append({"key": "mac236_missing", "label": "Mac236 未登记", "severity": "error"})
    elif not mac236.get("online"):
        blockers.append({"key": "mac236_offline", "label": "Mac236 离线", "severity": "error"})
    unsynced = [
        item
        for item in dataset_items
        if isinstance(item, dict) and item.get("sync_status") not in {"pending", "running", "completed"}
    ]
    failed_sync = [
        item
        for item in dataset_items
        if isinstance(item, dict) and item.get("sync_status") == "failed"
    ]
    if failed_sync:
        blockers.append({"key": "dataset_sync_failed", "label": "存在同步失败的数据集", "severity": "error", "count": len(failed_sync)})
    return {
        "ok": not any(item.get("severity") == "error" for item in blockers),
        "policy": _training_auto_policy_payload(),
        "nodes": {
            "training": gb10,
            "deployment": mac236,
        },
        "factors": [
            {"key": "gb10_online", "label": "GB10 在线", "ok": bool(gb10 and gb10.get("online")), "node_id": TRAINING_PRIMARY_GATEWAY_IDS[0]},
            {"key": "gb10_capability", "label": "GB10 训练能力", "ok": not any(item.get("key") == "gb10_training_capability" for item in blockers)},  # gitleaks:allow -- enum/config key or deliberate synthetic test fixture; not a credential
            {"key": "dataset_sync", "label": "训练数据同步", "ok": not failed_sync, "pending": len(unsynced)},
            {"key": "mac236_online", "label": "Mac236 在线", "ok": bool(mac236 and mac236.get("online")), "node_id": TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID},
            {"key": "auto_approval", "label": "自动审核", "ok": TRAINING_AUTO_APPROVE_JOBS},
            {"key": "auto_deploy", "label": "自动部署", "ok": TRAINING_AUTO_REQUEST_DEPLOYMENT and TRAINING_AUTO_ACTIVATE_DEPLOYMENT},
        ],
        "summary": {
            "dataset_versions": len(dataset_items),
            "dataset_unsynced": len(unsynced),
            "dataset_sync_failed": len(failed_sync),
            "training_jobs": len(job_items),
            "job_blockers": sum(len(item.get("automation_blockers") or []) for item in job_items if isinstance(item, dict)),
        },
        "department_flow": department_flow,
        "blockers": blockers,
        "last_run": TRAINING_AUTOMATION_LAST_RUN,
    }


async def list_training_resources(db: AsyncSession, user: User) -> dict[str, Any]:
    stmt = select(OpenClawInstance).order_by(
        OpenClawInstance.is_platform_default.desc(),
        OpenClawInstance.name,
    )
    if not _can_view_all_training_resources(user):
        department = str(getattr(user, "department", "") or "").strip()
        stmt = stmt.where(OpenClawInstance.is_active.is_(True))
        stmt = stmt.where(
            or_(
                OpenClawInstance.department == department,
                OpenClawInstance.is_platform_default.is_(True),
            )
            if department else OpenClawInstance.is_platform_default.is_(True)
        )

    rows = (await db.execute(stmt)).scalars().all()
    resources = [
        _training_resource_from_instance(
            item,
            online=bridge_registry.is_online(item.id),
        )
        for item in rows
    ]
    resource_ids = [str(item["id"]) for item in resources if str(item.get("id") or "")]
    active_job_map: dict[str, list[dict[str, Any]]] = {resource_id: [] for resource_id in resource_ids}
    if resource_ids:
        job_stmt = (
            select(TrainingJob)
            .where(TrainingJob.target_gateway_id.in_(resource_ids))
            .where(TrainingJob.status.in_(TRAINING_RESOURCE_ACTIVE_JOB_STATUSES))
            .where(or_(TrainingJob.status != "queued", TrainingJob.failure_stage.is_distinct_from("dispatch")))
            .order_by(TrainingJob.updated_at.desc(), TrainingJob.created_at.desc())
        )
        if not _can_view_all_training_resources(user):
            department = _user_department(user)
            job_stmt = job_stmt.where(TrainingJob.department == department if department else false())
        active_jobs = (await db.execute(job_stmt.limit(500))).scalars().all()
        for job in active_jobs:
            gateway_id = str(job.target_gateway_id or "")
            if gateway_id in active_job_map and len(active_job_map[gateway_id]) < 8:
                active_job_map[gateway_id].append(_serialize_resource_job_summary(job))
    for item in resources:
        active_jobs = active_job_map.get(str(item.get("id") or ""), [])
        item["active_training_jobs"] = active_jobs
        item["active_training_jobs_count"] = len(active_jobs)
        department = str(item.get("department") or "")
        if item.get("reserved_for"):
            item["route_scope"] = "reserved"
        elif department and department == _user_department(user):
            item["route_scope"] = "department"
        elif bool(item.get("is_platform_default")) and str(item.get("agent_purpose") or "") in {"training", "mixed"}:
            item["route_scope"] = "platform_fallback"
        else:
            item["route_scope"] = "external"
    total_gpu = sum(item["gpu_count"] for item in resources)
    idle_gpu = sum(item["idle_gpu_count"] for item in resources)
    return {
        "items": resources,
        "total": len(resources),
        "context": {
            "department": _user_department(user),
            "can_view_all": _can_view_all_training_resources(user),
        },
        "stats": {
            "training_gateways": sum(1 for item in resources if item["training"]["gateway"]),
            "gpu_count": total_gpu,
            "idle_gpu_count": idle_gpu,
            "busy_gpu_count": max(total_gpu - idle_gpu, 0),
            "vram_total_gb": round(sum(item["vram_total_gb"] for item in resources), 1),
            "vram_free_gb": round(sum(item["vram_free_gb"] for item in resources), 1),
            "active_training_jobs": sum(item["active_training_jobs_count"] for item in resources),
        },
    }


def _full_history_resource_training_ready(resource: dict[str, Any]) -> bool:
    training = resource.get("training") if isinstance(resource.get("training"), dict) else {}
    tasks = {str(item or "").strip().lower() for item in (training.get("supported_tasks") or [])}
    return bool(
        resource.get("online")
        and training.get("gateway") is True
        and not resource.get("reserved_for")
        and ({"lora", "qlora"} & tasks)
    )


def _full_history_resource_inference_ready(resource: dict[str, Any]) -> bool:
    ops = {str(item or "").strip() for item in (resource.get("ops") or [])}
    return bool(resource.get("online") and "training.inference" in ops and not resource.get("reserved_for"))


def _full_history_resource_rank(resource: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if resource.get("id") in TRAINING_PRIMARY_GATEWAY_IDS else 1,
        0 if str(resource.get("resource_role") or "") == "primary_training" else 1,
        -_safe_int(resource.get("idle_gpu_count")),
        -_safe_number(resource.get("vram_free_gb")),
        str(resource.get("name") or resource.get("id") or ""),
    )


def _select_full_history_training_resource(resources: list[dict[str, Any]]) -> dict[str, Any] | None:
    ready = [item for item in resources if _full_history_resource_training_ready(item)]
    if not ready:
        return None
    ready.sort(key=_full_history_resource_rank)
    return ready[0]


def _select_full_history_deployment_resource(
    resources: list[dict[str, Any]],
    *,
    training_resource: dict[str, Any] | None,
) -> dict[str, Any] | None:
    ready = [item for item in resources if _full_history_resource_inference_ready(item)]
    if not ready:
        return None
    by_id = {str(item.get("id") or ""): item for item in ready}
    if TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID in by_id:
        return by_id[TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID]
    training_id = str((training_resource or {}).get("id") or "")
    if training_id in by_id:
        return by_id[training_id]
    ready.sort(key=lambda item: (
        0 if str(item.get("resource_role") or "") == "deployment_inference" else 1,
        -_safe_int(item.get("idle_gpu_count")),
        -_safe_number(item.get("vram_free_gb")),
        str(item.get("name") or item.get("id") or ""),
    ))
    return ready[0]


async def _latest_full_history_deployment(
    db: AsyncSession,
    *,
    target_skill_id: str = TRAINING_FULL_HISTORY_CHAT_SKILL_ID,
    cycle_index: int | None = None,
    statuses: set[str] | None = None,
) -> TrainingModelDeployment | None:
    allowed_statuses = statuses or TRAINING_DEPLOYMENT_ACTIVE_STATUSES
    rows = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.status.in_(sorted(allowed_statuses)))
            .order_by(
                TrainingModelDeployment.updated_at.desc(),
                TrainingModelDeployment.created_at.desc(),
                TrainingModelDeployment.id.desc(),
            )
            .limit(200)
        )
    ).scalars().all()
    job_ids = [row.job_id for row in rows if row.job_id]
    job_map: dict[str, TrainingJob] = {}
    if job_ids:
        jobs = (await db.execute(select(TrainingJob).where(TrainingJob.id.in_(job_ids)))).scalars().all()
        job_map = {job.id: job for job in jobs}
    for row in rows:
        targets = {str(item or "").strip() for item in (row.target_skill_ids_json or [])}
        if target_skill_id not in targets:
            continue
        if cycle_index is not None:
            job = job_map.get(row.job_id)
            if _full_history_job_cycle_index(job) != cycle_index:
                continue
        return row
    return None


def _full_history_job_cycle_index(job: TrainingJob | None) -> int:
    spec = _safe_dict(getattr(job, "spec_json", None))
    governance = _safe_dict(spec.get("governance"))
    if governance.get("full_history_three_cycle") is not True and governance.get("skillforge_full_history_chat") is not True:
        return 0
    return max(0, _safe_int(governance.get("cycle_index") or spec.get("cycle_index")))


def _normalize_full_history_job_bridge_dataset(job: TrainingJob) -> bool:
    if str(job.target_skill_id or "") != TRAINING_FULL_HISTORY_CHAT_SKILL_ID:
        return False
    cycle_index = _full_history_job_cycle_index(job)
    if cycle_index <= 0:
        return False
    target_gateway_id = str(job.target_gateway_id or "").strip()
    if not target_gateway_id:
        return False
    spec = dict(job.spec_json or {}) if isinstance(job.spec_json, dict) else {}
    changed = False
    for section_key in ("dataset", "governance"):
        section = dict(spec.get(section_key) or {}) if isinstance(spec.get(section_key), dict) else {}
        bridge_dataset = (
            dict(section.get("bridge_dataset") or {})
            if isinstance(section.get("bridge_dataset"), dict)
            else {}
        )
        if bridge_dataset.get("source_gateway_id") != target_gateway_id:
            bridge_dataset["source_gateway_id"] = target_gateway_id
            changed = True
        if bridge_dataset.get("target_gateway_id") != target_gateway_id:
            bridge_dataset["target_gateway_id"] = target_gateway_id
            changed = True
        if bridge_dataset.get("enabled") is not True:
            bridge_dataset["enabled"] = True
            changed = True
        if bridge_dataset.get("required") is not True:
            bridge_dataset["required"] = True
            changed = True
        if bridge_dataset.get("storage") != "bridge_dataset":
            bridge_dataset["storage"] = "bridge_dataset"
            changed = True
        if changed or section_key in spec:
            section["bridge_dataset"] = bridge_dataset
            spec[section_key] = section
    dataset = dict(spec.get("dataset") or {}) if isinstance(spec.get("dataset"), dict) else {}
    selector = dict(dataset.get("selector") or {}) if isinstance(dataset.get("selector"), dict) else {}
    if selector:
        current_limit = _safe_int(selector.get("limit"))
        if current_limit <= 0 or current_limit > TRAINING_FULL_HISTORY_EXTERNAL_SAMPLE_LIMIT:
            selector["limit"] = TRAINING_FULL_HISTORY_EXTERNAL_SAMPLE_LIMIT
            selector["selected_sample_limit"] = TRAINING_FULL_HISTORY_EXTERNAL_SAMPLE_LIMIT
            selector["selection_policy"] = "stable_full_history_training_batch"
            selector["full_history_sample_total"] = dataset.get("sample_total")
            dataset["selector"] = selector
            spec["dataset"] = dataset
            changed = True
    if changed:
        job.spec_json = spec
        job.updated_at = now_bjt()
    return changed


async def _full_history_cycle_jobs(
    db: AsyncSession,
    user: User,
    *,
    target_skill_id: str = TRAINING_FULL_HISTORY_CHAT_SKILL_ID,
) -> list[TrainingJob]:
    stmt = (
        select(TrainingJob)
        .where(TrainingJob.target_skill_id == target_skill_id)
        .order_by(TrainingJob.created_at.desc(), TrainingJob.id.desc())
        .limit(200)
    )
    if not _can_view_all_training_resources(user):
        department = _user_department(user)
        stmt = stmt.where(TrainingJob.department == department if department else false())
    rows = (await db.execute(stmt)).scalars().all()
    return [job for job in rows if _full_history_job_cycle_index(job) > 0]


async def _full_history_job_payload(
    db: AsyncSession,
    job: TrainingJob | None,
) -> dict[str, Any] | None:
    if job is None:
        return None
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    deployments = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.job_id == job.id)
            .order_by(TrainingModelDeployment.created_at.desc(), TrainingModelDeployment.id.desc())
        )
    ).scalars().all()
    return _serialize_job(job, list(tasks), list(deployments))


def _serialize_full_history_automation_run(row: TrainingFullHistoryAutomationRun | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "status": row.status,
        "trigger_type": row.trigger_type,
        "current_step": row.current_step,
        "current_cycle": row.current_cycle,
        "current_job_id": row.current_job_id,
        "target_skill_id": row.target_skill_id,
        "model_profile": row.model_profile,
        "training_gateway_id": row.training_gateway_id,
        "deployment_gateway_id": row.deployment_gateway_id,
        "created_by": row.created_by,
        "request": row.request_json or {},
        "result": row.result_json or {},
        "data_preparation": row.data_preparation_json or {},
        "actions": row.actions_json or [],
        "error": row.error,
        "started_at": isoformat_bjt(row.started_at) if row.started_at else None,
        "finished_at": isoformat_bjt(row.finished_at) if row.finished_at else None,
        "last_heartbeat_at": isoformat_bjt(row.last_heartbeat_at) if row.last_heartbeat_at else None,
        "created_at": isoformat_bjt(row.created_at) if row.created_at else None,
        "updated_at": isoformat_bjt(row.updated_at) if row.updated_at else None,
    }


async def _latest_full_history_automation_run(
    db: AsyncSession,
    *,
    include_open_only: bool = False,
) -> TrainingFullHistoryAutomationRun | None:
    stmt = (
        select(TrainingFullHistoryAutomationRun)
        .where(TrainingFullHistoryAutomationRun.target_skill_id == TRAINING_FULL_HISTORY_CHAT_SKILL_ID)
        .order_by(
            TrainingFullHistoryAutomationRun.updated_at.desc(),
            TrainingFullHistoryAutomationRun.started_at.desc(),
            TrainingFullHistoryAutomationRun.id.desc(),
        )
        .limit(1)
    )
    if include_open_only:
        stmt = stmt.where(TrainingFullHistoryAutomationRun.status.in_(sorted(TRAINING_FULL_HISTORY_AUTOMATION_OPEN_STATUSES)))
    return (await db.execute(stmt)).scalar_one_or_none()


async def _ensure_full_history_automation_run(
    db: AsyncSession,
    user: User,
    *,
    trigger_type: str,
    request: dict[str, Any] | None = None,
    training_gateway_id: str | None = None,
    deployment_gateway_id: str | None = None,
) -> TrainingFullHistoryAutomationRun:
    row = await _latest_full_history_automation_run(db, include_open_only=True)
    now = now_bjt()
    clean_request = _safe_dict(request)
    if row is not None:
        row.last_heartbeat_at = now
        row.updated_at = now
        if training_gateway_id:
            row.training_gateway_id = training_gateway_id
        if deployment_gateway_id:
            row.deployment_gateway_id = deployment_gateway_id
        if clean_request:
            row.request_json = {
                **_safe_dict(row.request_json),
                **clean_request,
                "latest_trigger_type": trigger_type,
                "latest_triggered_at": isoformat_bjt(now),
            }
        return row
    row = TrainingFullHistoryAutomationRun(
        id="fhauto_" + uuid4().hex[:24],
        status="queued",
        trigger_type=_clean_text(trigger_type, limit=40) or "scheduled",
        current_step="queued",
        current_cycle=1,
        target_skill_id=TRAINING_FULL_HISTORY_CHAT_SKILL_ID,
        model_profile=TRAINING_FULL_HISTORY_MODEL_PROFILE,
        training_gateway_id=training_gateway_id,
        deployment_gateway_id=deployment_gateway_id,
        created_by=str(getattr(user, "id", "") or "full_history_finetune_automation")[:50],
        request_json=clean_request,
        result_json={},
        data_preparation_json={},
        actions_json=[],
        started_at=now,
        last_heartbeat_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    return row


def _append_full_history_run_actions(
    row: TrainingFullHistoryAutomationRun,
    actions: list[dict[str, Any]],
) -> None:
    if not actions:
        return
    existing = _safe_list(row.actions_json)
    row.actions_json = [*existing, *actions][-200:]


def _derive_full_history_progress(status: dict[str, Any]) -> dict[str, Any]:
    blockers = _safe_list(status.get("blockers"))
    error_blockers = [item for item in blockers if _safe_dict(item).get("severity") == "error"]
    if error_blockers:
        first = _safe_dict(error_blockers[0])
        return {
            "current_step": "waiting_gateway",
            "current_cycle": 1,
            "current_job_id": None,
            "next_action": first.get("label") or "等待训练/推理网关在线",
            "last_error": None,
        }
    if _safe_int(status.get("completed_cycles")) >= TRAINING_FULL_HISTORY_CYCLE_COUNT:
        latest_deployment = _safe_dict(status.get("latest_deployment"))
        return {
            "current_step": "completed",
            "current_cycle": TRAINING_FULL_HISTORY_CYCLE_COUNT,
            "current_job_id": latest_deployment.get("job_id"),
            "next_action": "大厅对话 Skill 已可用",
            "last_error": None,
        }
    cycles = _safe_list(status.get("cycles"))
    for item in cycles:
        cycle = _safe_int(_safe_dict(item).get("cycle_index")) or 1
        if str(_safe_dict(item).get("status") or "") == "completed":
            continue
        job = _safe_dict(_safe_dict(item).get("job"))
        deployment = _safe_dict(_safe_dict(item).get("deployment"))
        job_id = str(job.get("id") or "") or None
        job_status = str(job.get("status") or _safe_dict(item).get("status") or "pending")
        failure_stage = str(job.get("failure_stage") or "").strip()
        if not job:
            step = "create_cycle_job" if cycle == 1 else "wait_parent_deployment"
            next_action = f"创建第 {cycle} 轮 4B 训练任务" if cycle == 1 else f"等待第 {cycle - 1} 轮部署后创建第 {cycle} 轮"
        elif job_status == "awaiting_review":
            step = "approve_job"
            next_action = f"自动审核第 {cycle} 轮训练任务"
        elif job_status == "queued" and failure_stage in {"dispatch", "model_preflight"}:
            step = "recover_dispatch"
            next_action = "恢复模型预检或重试训练下发"
        elif job_status == "queued":
            step = "dispatch_job"
            next_action = "下发到训练网关"
        elif job_status in {"running", "evaluating", "unknown"}:
            step = "wait_gateway_result"
            next_action = "等待训练网关完成并回传结果"
        elif job_status == "completed" and not deployment:
            step = "evaluate_deploy"
            next_action = "自动评估并创建部署"
        elif job_status in TRAINING_FULL_HISTORY_BACKGROUND_FINAL_STATUSES:
            step = "retry_or_blocked"
            next_action = "自动重试失败轮次或等待人工处理"
        else:
            step = "advance_cycle"
            next_action = "继续推进训练状态机"
        return {
            "current_step": step,
            "current_cycle": cycle,
            "current_job_id": job_id,
            "next_action": next_action,
            "last_error": job.get("error_message") or job.get("failure_stage") or None,
        }
    return {
        "current_step": "queued",
        "current_cycle": 1,
        "current_job_id": None,
        "next_action": "准备全量数据训练任务",
        "last_error": None,
    }


def _full_history_run_status_from_progress(
    status: dict[str, Any],
    progress: dict[str, Any],
    actions: list[dict[str, Any]],
) -> str:
    if _safe_int(status.get("completed_cycles")) >= TRAINING_FULL_HISTORY_CYCLE_COUNT:
        return "completed"
    if any(_safe_dict(item).get("severity") == "error" for item in _safe_list(status.get("blockers"))):
        return "blocked"
    if any(_safe_dict(item).get("status") == "failed" for item in actions):
        return "blocked"
    if str(progress.get("current_step") or "") in {"retry_or_blocked", "wait_parent_deployment"}:
        return "blocked"
    return "running"


def _compact_full_history_run_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": result.get("ok"),
        "status": result.get("status"),
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
        "cycles_requested": result.get("cycles_requested"),
        "completed_cycles": result.get("completed_cycles"),
        "blockers": _safe_list(result.get("blockers"))[:20],
        "actions": _safe_list(result.get("actions"))[-50:],
    }


async def _update_full_history_automation_run(
    db: AsyncSession,
    row: TrainingFullHistoryAutomationRun | None,
    *,
    status_payload: dict[str, Any],
    actions: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = None,
    data_preparation: dict[str, Any] | None = None,
    force_status: str | None = None,
    error: str | None = None,
) -> TrainingFullHistoryAutomationRun | None:
    if row is None:
        return None
    progress = _derive_full_history_progress(status_payload)
    now = now_bjt()
    clean_actions = [item for item in (actions or []) if isinstance(item, dict)]
    next_status = force_status or _full_history_run_status_from_progress(status_payload, progress, clean_actions)
    row.status = next_status
    row.current_step = str(progress.get("current_step") or "queued")[:80]
    row.current_cycle = max(1, min(_safe_int(progress.get("current_cycle")) or 1, TRAINING_FULL_HISTORY_CYCLE_COUNT))
    row.current_job_id = _clean_text(progress.get("current_job_id"), limit=50)
    selected = _safe_dict(status_payload.get("selected"))
    training_gateway_id = _clean_text(_safe_dict(selected.get("training_gateway")).get("id"), limit=80)
    deployment_gateway_id = _clean_text(_safe_dict(selected.get("deployment_gateway")).get("id"), limit=80)
    if training_gateway_id:
        row.training_gateway_id = training_gateway_id
    if deployment_gateway_id:
        row.deployment_gateway_id = deployment_gateway_id
    if data_preparation is not None:
        row.data_preparation_json = data_preparation
    if result is not None:
        row.result_json = _compact_full_history_run_result(result)
    _append_full_history_run_actions(row, clean_actions)
    row.error = (error or progress.get("last_error") or None)
    row.last_heartbeat_at = now
    row.updated_at = now
    if next_status in TRAINING_FULL_HISTORY_AUTOMATION_FINAL_STATUSES:
        row.finished_at = row.finished_at or now
    elif next_status in TRAINING_FULL_HISTORY_AUTOMATION_OPEN_STATUSES:
        row.finished_at = None
    await db.flush()
    return row


async def _prepare_full_history_training_data(
    db: AsyncSession,
    user: User,
    row: TrainingFullHistoryAutomationRun | None,
    *,
    capture_sources: bool,
    days: int = TRAINING_FULL_HISTORY_AUTO_DAYS,
    per_source_limit: int = TRAINING_FULL_HISTORY_AUTO_PER_SOURCE_LIMIT,
    materialize_limit: int = TRAINING_FULL_HISTORY_AUTO_MATERIALIZE_LIMIT,
) -> dict[str, Any]:
    existing = _safe_dict(getattr(row, "data_preparation_json", None))
    if existing.get("status") == "completed":
        return existing
    from app.learning.service import _capture_system_training_source_rows, _materialize_system_training_artifacts

    started_at = now_bjt()
    captured: dict[str, Any]
    if capture_sources:
        captured = await _capture_system_training_source_rows(
            db,
            user,
            days=days,
            per_source_limit=per_source_limit,
        )
    else:
        captured = {
            "skipped": True,
            "reason": "capture_sources_disabled",
            "capture_mode": "materialize_existing_learning_artifacts",
            "days": days,
            "per_source_limit": per_source_limit,
        }
    materialized = await _materialize_system_training_artifacts(db, user, limit=materialize_limit)
    prepared = {
        "status": "completed",
        "started_at": isoformat_bjt(started_at),
        "finished_at": isoformat_bjt(now_bjt()),
        "capture_sources": bool(capture_sources),
        "captured": captured,
        "materialized": materialized,
        "source_sync_mode": "bounded_batch_background" if capture_sources else "existing_artifacts_only",
        "days": days,
        "per_source_limit": per_source_limit,
        "materialize_limit": materialize_limit,
        "raw_payload_returned": False,
    }
    if row is not None:
        row.data_preparation_json = prepared
        row.updated_at = now_bjt()
        await db.flush()
    return prepared


async def _full_history_training_sample_summary(
    db: AsyncSession,
    user: User,
    jobs: list[TrainingJob],
) -> dict[str, Any]:
    """Summarize the governed learning samples used by the full-history selector."""
    from app.learning.models import LearningArtifact

    conditions = [
        LearningArtifact.artifact_kind.in_(["training_sample", "eval_case"]),
        LearningArtifact.status == "materialized",
        LearningArtifact.sink_type == "training_sample",
    ]
    if not _can_view_all_training_resources(user):
        department = _user_department(user)
        conditions.append(LearningArtifact.department == department if department else false())

    rows = (
        await db.execute(
            select(
                LearningArtifact.artifact_kind,
                func.count(LearningArtifact.id),
                func.min(LearningArtifact.created_at),
                func.max(LearningArtifact.created_at),
            )
            .where(*conditions)
            .group_by(LearningArtifact.artifact_kind)
        )
    ).all()
    counts_by_kind = {str(kind): int(count or 0) for kind, count, *_rest in rows}
    first_seen = min((row[2] for row in rows if row[2]), default=None)
    last_seen = max((row[3] for row in rows if row[3]), default=None)
    department_rows = (
        await db.execute(
            select(
                LearningArtifact.department,
                func.count(LearningArtifact.id),
            )
            .where(*conditions)
            .group_by(LearningArtifact.department)
            .order_by(func.count(LearningArtifact.id).desc())
            .limit(12)
        )
    ).all()
    latest_cycle_job = max(
        (job for job in jobs if _full_history_job_cycle_index(job) > 0),
        key=lambda item: _full_history_job_cycle_index(item),
        default=None,
    )
    latest_dataset = _safe_dict(_safe_dict(getattr(latest_cycle_job, "spec_json", None)).get("dataset"))
    return {
        "source": "learning_artifacts",
        "status": "ready" if rows else "empty",
        "sample_total": sum(counts_by_kind.values()),
        "training_sample_count": counts_by_kind.get("training_sample", 0),
        "eval_count": counts_by_kind.get("eval_case", 0),
        "latest_cycle_index": _full_history_job_cycle_index(latest_cycle_job),
        "latest_cycle_sample_total": _safe_int(latest_dataset.get("sample_total")),
        "latest_cycle_eval_count": _safe_int(latest_dataset.get("eval_count")),
        "created_at_start": isoformat_bjt(first_seen) if first_seen else None,
        "created_at_end": isoformat_bjt(last_seen) if last_seen else None,
        "departments": [
            {"department": str(department or "未归属"), "sample_count": int(count or 0)}
            for department, count in department_rows
        ],
        "raw_payload_returned": False,
    }


async def get_full_history_finetune_status(db: AsyncSession, user: User) -> dict[str, Any]:
    resources_payload = await list_training_resources(db, user)
    resources = [item for item in _safe_list(resources_payload.get("items")) if isinstance(item, dict)]
    by_id = {str(item.get("id") or ""): item for item in resources}
    gb10 = by_id.get(TRAINING_PRIMARY_GATEWAY_IDS[0])
    mac236 = by_id.get(TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID)
    training_resource = _select_full_history_training_resource(resources)
    deployment_resource = _select_full_history_deployment_resource(resources, training_resource=training_resource)
    blockers: list[dict[str, Any]] = []
    if training_resource is None:
        blockers.append({
            "key": "no_online_training_gateway",
            "label": "没有在线且支持 LoRA/QLoRA 的训练网关",
            "severity": "error",
        })
    elif training_resource.get("id") != TRAINING_PRIMARY_GATEWAY_IDS[0]:
        blockers.append({
            "key": "gb10_training_fallback",
            "label": "GB10 237 不在线或不可训练，已选择在线 GPU 网关兜底",
            "severity": "warn",
            "selected_gateway_id": training_resource.get("id"),
        })
    if deployment_resource is None:
        blockers.append({
            "key": "no_online_inference_gateway",
            "label": "没有在线且支持 training.inference 的部署网关",
            "severity": "error",
        })
    elif deployment_resource.get("id") != TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID:
        blockers.append({
            "key": "mac236_deployment_fallback",
            "label": "Mac 236 不在线或不可推理，已选择在线推理网关兜底",
            "severity": "warn",
            "selected_gateway_id": deployment_resource.get("id"),
        })
    jobs = await _full_history_cycle_jobs(db, user)
    latest_by_cycle: dict[int, TrainingJob] = {}
    for job in jobs:
        cycle = _full_history_job_cycle_index(job)
        if cycle and cycle not in latest_by_cycle:
            latest_by_cycle[cycle] = job
    cycle_items: list[dict[str, Any]] = []
    completed_cycles = 0
    for cycle in range(1, TRAINING_FULL_HISTORY_CYCLE_COUNT + 1):
        job = latest_by_cycle.get(cycle)
        deployment = await _latest_full_history_deployment(
            db,
            cycle_index=cycle,
            statuses=TRAINING_FULL_HISTORY_COMPLETED_DEPLOYMENT_STATUSES,
        )
        deployment_payload = _serialize_deployment(deployment) if deployment is not None else None
        job_payload = await _full_history_job_payload(db, job)
        is_complete = bool(deployment and deployment.status in TRAINING_FULL_HISTORY_COMPLETED_DEPLOYMENT_STATUSES)
        if is_complete:
            completed_cycles += 1
        cycle_items.append({
            "cycle_index": cycle,
            "status": "completed" if is_complete else (str(job.status) if job else "pending"),
            "job": job_payload,
            "deployment": deployment_payload,
        })
    sample_summary = await _full_history_training_sample_summary(db, user, jobs)
    latest_deployment = await _latest_full_history_deployment(db)
    latest_job = None
    if latest_deployment is not None:
        latest_job = await db.get(TrainingJob, latest_deployment.job_id)
    chat_model_id = (
        _deployment_openwebui_model_id(latest_deployment, latest_job)
        if latest_deployment is not None and latest_job is not None
        else None
    )
    chat_target_gateway_id = (
        _deployment_target_gateway_id(latest_deployment, latest_job)
        if latest_deployment is not None
        else None
    )
    chat_disabled_reason = None
    if latest_deployment is None:
        chat_disabled_reason = "还没有已激活的三轮微调部署"
    elif not chat_model_id:
        chat_disabled_reason = "已存在 active 部署，但缺少大厅对话模型 ID"
    elif not chat_target_gateway_id:
        chat_disabled_reason = "已存在 active 部署，但缺少推理网关"
    payload = {
        "ok": completed_cycles >= TRAINING_FULL_HISTORY_CYCLE_COUNT and not any(item["severity"] == "error" for item in blockers),
        "ready_to_run": not any(item["severity"] == "error" for item in blockers),
        "target_skill": {
            "id": TRAINING_FULL_HISTORY_CHAT_SKILL_ID,
            "name": TRAINING_FULL_HISTORY_CHAT_SKILL_NAME,
            "route_path": "/hall/finetuned-model-chat",
            "category": "用户对话",
            "department": "AI平台",
        },
        "model_profile": TRAINING_FULL_HISTORY_MODEL_PROFILE,
        "cycle_count": TRAINING_FULL_HISTORY_CYCLE_COUNT,
        "completed_cycles": completed_cycles,
        "cycles": cycle_items,
        "selected": {
            "training_gateway": training_resource,
            "deployment_gateway": deployment_resource,
        },
        "preferred": {
            "training_gateway": gb10,
            "deployment_gateway": mac236,
        },
        "latest_deployment": _serialize_deployment(latest_deployment) if latest_deployment is not None else None,
        "sample_summary": sample_summary,
        "chat": {
            "ready": chat_disabled_reason is None,
            "model_id": chat_model_id,
            "gateway_id": chat_target_gateway_id,
            "endpoint": "/api/training/full-history-finetune/chat",
            "disabled_reason": chat_disabled_reason,
        },
        "blockers": blockers,
        "policy": {
            "raw_payload_returned": False,
            "auto_review": TRAINING_AUTO_APPROVE_JOBS,
            "auto_deployment": TRAINING_AUTO_REQUEST_DEPLOYMENT and TRAINING_AUTO_ACTIVATE_DEPLOYMENT,
            "gb10_preferred": TRAINING_PRIMARY_GATEWAY_IDS[0],
            "mac_preferred": TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID,
            "fallback_online_gpu": True,
        },
    }
    latest_run = await _latest_full_history_automation_run(db)
    progress = _derive_full_history_progress(payload)
    run_payload = _serialize_full_history_automation_run(latest_run)
    if run_payload:
        run_payload["current_step"] = run_payload.get("current_step") or progress.get("current_step")
    payload.update({
        "automation_run": run_payload,
        "background_running": bool(latest_run and latest_run.status in {"queued", "running"}),
        "current_step": progress.get("current_step"),
        "current_cycle": progress.get("current_cycle"),
        "current_job_id": progress.get("current_job_id"),
        "next_action": progress.get("next_action"),
        "last_error": progress.get("last_error") or (latest_run.error if latest_run else None),
        "data_preparation": _safe_dict(getattr(latest_run, "data_preparation_json", None)),
    })
    return payload


async def _ensure_full_history_cycle_job(
    db: AsyncSession,
    user: User,
    *,
    cycle: int,
    cycles: int,
    min_sample_count: int,
    training_gateway_id: str,
    deployment_gateway_id: str,
    parent_deployment: TrainingModelDeployment | None = None,
) -> tuple[str, dict[str, Any]]:
    date_segment = now_bjt().strftime("%Y%m%d")
    from app.learning.service import ensure_full_history_platform_training_candidate

    candidate = await ensure_full_history_platform_training_candidate(
        db,
        user,
        min_sample_count=min_sample_count,
        target_skill_id=TRAINING_FULL_HISTORY_CHAT_SKILL_ID,
        parent_deployment_id=parent_deployment.id if parent_deployment is not None else None,
        require_parent_deployment=False,
        training_model_profile=TRAINING_FULL_HISTORY_MODEL_PROFILE,
        training_model_source=TRAINING_FULL_HISTORY_MODEL_SOURCE,
        training_gateway_id=training_gateway_id,
        dataset_source_gateway_id=training_gateway_id,
        deployment_gateway_id=deployment_gateway_id,
        deployment_runtime_profile=TRAINING_DEFAULT_DEPLOYMENT_RUNTIME_PROFILE,
        model_family_suffix=TRAINING_FULL_HISTORY_MODEL_FAMILY_SUFFIX,
        assign_training_gateway=True,
        manifest_extra={
            "flow": "skillforge_full_history_three_cycle",
            "cycle_index": cycle,
            "date": date_segment,
        },
        governance_extra={
            "skillforge_full_history_chat": True,
            "full_history_three_cycle": True,
            "cycle_index": cycle,
            "cycle_count": cycles,
            "target_chat_skill_id": TRAINING_FULL_HISTORY_CHAT_SKILL_ID,
            "fallback_online_gpu_allowed": True,
            "selected_training_gateway_id": training_gateway_id,
            "selected_deployment_gateway_id": deployment_gateway_id,
        },
        job_title=f"SkillForge 全量数据 4B 微调 · 第 {cycle} 轮 · {date_segment}",
        dataset_ref_suffix=f"{date_segment}/cycle-{cycle}",
    )
    await db.commit()
    created = _safe_list(candidate.get("created"))
    skipped = _safe_list(candidate.get("skipped"))
    if created:
        job_id = str(_safe_dict(created[0]).get("training_job_id") or "")
        return job_id, {
            "cycle_index": cycle,
            "action": "create_job",
            "status": "completed",
            "job_id": job_id,
            "candidate": candidate,
        }
    duplicate = next((item for item in skipped if _safe_dict(item).get("training_job_id")), None)
    job_id = str(_safe_dict(duplicate).get("training_job_id") or "")
    if job_id:
        return job_id, {
            "cycle_index": cycle,
            "action": "create_job",
            "status": "skipped",
            "job_id": job_id,
            "candidate": candidate,
        }
    return "", {
        "cycle_index": cycle,
        "action": "create_job",
        "status": "blocked",
        "candidate": candidate,
    }


async def run_full_history_finetune_cycles(
    db: AsyncSession,
    user: User,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not (_can_create_training_job(user) and _can_approve_training_job(user)):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to run full-history finetune automation"})
    if not _can_view_all_training_resources(user):
        raise AppError("FORBIDDEN", 403, {"detail": "full-history finetune requires global training access"})
    raw = payload if isinstance(payload, dict) else {}
    cycles = max(1, min(_safe_int(raw.get("cycles")) or TRAINING_FULL_HISTORY_CYCLE_COUNT, TRAINING_FULL_HISTORY_CYCLE_COUNT))
    min_sample_count = max(1, min(_safe_int(raw.get("min_sample_count")) or 4, 100000))
    capture_sources = raw.get("capture_sources", True) is not False
    blocking_source_sync = raw.get("blocking_source_sync") is True
    days = max(1, min(_safe_int(raw.get("days")) or 3650, 3650))
    per_source_limit = max(1, min(_safe_int(raw.get("per_source_limit")) or 2000, 50000))
    materialize_limit = max(1, min(_safe_int(raw.get("materialize_limit")) or 50000, 50000))
    background_dispatch = raw.get("background_dispatch", True) is not False
    started_at = now_bjt()
    before = await get_full_history_finetune_status(db, user)
    selected = _safe_dict(before.get("selected"))
    training_gateway_id = str(_safe_dict(selected.get("training_gateway")).get("id") or "")
    deployment_gateway_id = str(_safe_dict(selected.get("deployment_gateway")).get("id") or "")
    run_row = await _ensure_full_history_automation_run(
        db,
        user,
        trigger_type=str(raw.get("trigger") or "manual"),
        request={
            "cycles": cycles,
            "min_sample_count": min_sample_count,
            "capture_sources": capture_sources,
            "blocking_source_sync": blocking_source_sync,
            "days": days,
            "per_source_limit": per_source_limit,
            "materialize_limit": materialize_limit,
            "background_dispatch": background_dispatch,
        },
        training_gateway_id=training_gateway_id or None,
        deployment_gateway_id=deployment_gateway_id or None,
    )
    if before.get("ok"):
        result = {
            "ok": True,
            "started_at": isoformat_bjt(started_at),
            "finished_at": isoformat_bjt(now_bjt()),
            "status": "completed",
            "cycles_requested": cycles,
            "completed_cycles": before.get("completed_cycles", 0),
            "preparation": _safe_dict(run_row.data_preparation_json),
            "actions": [],
            "before": before,
            "after": before,
            "blockers": before.get("blockers") or [],
        }
        await _update_full_history_automation_run(
            db,
            run_row,
            status_payload=before,
            result=result,
            force_status="completed",
        )
        await db.commit()
        final_status = await get_full_history_finetune_status(db, user)
        result["after"] = final_status
        result["automation_run"] = final_status.get("automation_run")
        return result
    if not before.get("ready_to_run"):
        blocked_result = {
            "ok": False,
            "started_at": isoformat_bjt(started_at),
            "finished_at": isoformat_bjt(now_bjt()),
            "status": "blocked",
            "blockers": before.get("blockers") or [],
            "before": before,
            "after": before,
            "actions": [],
        }
        await _update_full_history_automation_run(
            db,
            run_row,
            status_payload=before,
            result=blocked_result,
            force_status="blocked",
        )
        await db.commit()
        final_status = await get_full_history_finetune_status(db, user)
        blocked_result["after"] = final_status
        blocked_result["automation_run"] = final_status.get("automation_run")
        return blocked_result
    preparation: dict[str, Any] = {}
    if capture_sources:
        preparation = await _prepare_full_history_training_data(
            db,
            user,
            run_row,
            capture_sources=blocking_source_sync,
            days=days,
            per_source_limit=per_source_limit,
            materialize_limit=materialize_limit,
        )
        if not blocking_source_sync:
            preparation = {
                **preparation,
                "status": "deferred_capture",
                "captured": {
                    "skipped": True,
                    "reason": "deferred_to_full_history_background_loop",
                    "capture_mode": "non_blocking_training_first",
                    "days": days,
                    "per_source_limit": per_source_limit,
                },
                "source_sync_mode": "deferred_non_blocking",
            }
            run_row.data_preparation_json = preparation
            await db.flush()
    actions: list[dict[str, Any]] = []
    current_status = before
    for cycle in range(1, cycles + 1):
        cycle_rows = [item for item in _safe_list(current_status.get("cycles")) if _safe_int(_safe_dict(item).get("cycle_index")) == cycle]
        current_cycle = _safe_dict(cycle_rows[0]) if cycle_rows else {}
        if current_cycle.get("status") == "completed":
            continue
        existing_job = current_cycle.get("job")
        job_id = str(_safe_dict(existing_job).get("id") or "")
        if str(_safe_dict(existing_job).get("status") or "") in TRAINING_FULL_HISTORY_BACKGROUND_FINAL_STATUSES:
            job_id = ""
        parent_deployment = None
        if cycle > 1:
            parent_deployment = await _latest_full_history_deployment(db, cycle_index=cycle - 1)
            if parent_deployment is None:
                actions.append({
                    "cycle_index": cycle,
                    "action": "wait_parent_deployment",
                    "status": "blocked",
                    "reason": "previous_cycle_not_deployed",
                })
                break
        if not job_id:
            job_id, create_action = await _ensure_full_history_cycle_job(
                db,
                user,
                cycle=cycle,
                cycles=cycles,
                min_sample_count=min_sample_count,
                training_gateway_id=training_gateway_id,
                deployment_gateway_id=deployment_gateway_id,
                parent_deployment=parent_deployment,
            )
            actions.append(create_action)
            if not job_id:
                break
        if job_id:
            if background_dispatch:
                job = await db.get(TrainingJob, job_id)
                if job is not None and _normalize_full_history_job_bridge_dataset(job):
                    await db.commit()
                kickoff_full_history_finetune_automation()
                actions.append({
                    "cycle_index": cycle,
                    "action": "schedule_background_auto_advance",
                    "status": "queued",
                    "job_id": job_id,
                })
                break
            try:
                advanced = await auto_advance_training_job(
                    db,
                    user,
                    job_id,
                    {"dispatch": True, "evaluate": True, "metadata": {"trigger": "full_history_three_cycle", "cycle_index": cycle}},
                )
                actions.append({
                    "cycle_index": cycle,
                    "action": "auto_advance",
                    "status": "completed",
                    "job_id": job_id,
                    "actions": advanced.get("actions") or [],
                    "job_status": _safe_dict(advanced.get("job")).get("status"),
                })
            except AppError as exc:
                await db.rollback()
                actions.append({
                    "cycle_index": cycle,
                    "action": "auto_advance",
                    "status": "failed",
                    "job_id": job_id,
                    "error_code": exc.code,
                    "detail": exc.detail,
                })
                break
        current_status = await get_full_history_finetune_status(db, user)
        current_cycle = next(
            (_safe_dict(item) for item in _safe_list(current_status.get("cycles")) if _safe_int(_safe_dict(item).get("cycle_index")) == cycle),
            {},
        )
        if current_cycle.get("status") != "completed":
            actions.append({
                "cycle_index": cycle,
                "action": "wait_cycle_completion",
                "status": "pending",
                "job_status": _safe_dict(current_cycle.get("job")).get("status"),
            })
            break
    after = await get_full_history_finetune_status(db, user)
    result = {
        "ok": bool(after.get("completed_cycles", 0) >= cycles),
        "status": "completed" if after.get("completed_cycles", 0) >= cycles else "running_or_blocked",
        "started_at": isoformat_bjt(started_at),
        "finished_at": isoformat_bjt(now_bjt()),
        "cycles_requested": cycles,
        "completed_cycles": after.get("completed_cycles", 0),
        "preparation": preparation,
        "actions": actions,
        "before": before,
        "after": after,
        "blockers": after.get("blockers") or [],
    }
    await _update_full_history_automation_run(
        db,
        run_row,
        status_payload=after,
        actions=actions,
        result=result,
        data_preparation=preparation or _safe_dict(run_row.data_preparation_json),
        force_status="completed" if result["ok"] else None,
    )
    await db.commit()
    final_status = await get_full_history_finetune_status(db, user)
    result["after"] = final_status
    result["automation_run"] = final_status.get("automation_run")
    return result


def _full_history_automation_user() -> Any:
    return SimpleNamespace(
        id="full_history_finetune_automation",
        username="full_history_finetune_automation",
        role="system_admin",
        department=None,
        can_view_all=True,
    )


async def advance_full_history_finetune_automation(
    db: AsyncSession,
    *,
    limit: int = 1,
) -> dict[str, Any]:
    actor = _full_history_automation_user()
    clean_limit = max(1, min(int(limit or 1), TRAINING_FULL_HISTORY_CYCLE_COUNT))
    started_at = now_bjt()
    actions: list[dict[str, Any]] = []
    advanced = 0
    active = 0
    status = await get_full_history_finetune_status(db, actor)
    selected = _safe_dict(status.get("selected"))
    training_gateway_id = str(_safe_dict(selected.get("training_gateway")).get("id") or "")
    deployment_gateway_id = str(_safe_dict(selected.get("deployment_gateway")).get("id") or "")
    latest_run = await _latest_full_history_automation_run(db, include_open_only=True)
    if status.get("ok"):
        if latest_run is not None:
            await _update_full_history_automation_run(
                db,
                latest_run,
                status_payload=status,
                result={"status": "completed", "completed_cycles": status.get("completed_cycles", 0)},
                force_status="completed",
            )
            await db.commit()
        return {
            "ok": True,
            "status": "idle",
            "started_at": isoformat_bjt(started_at),
            "finished_at": isoformat_bjt(now_bjt()),
            "advanced": 0,
            "active": 0,
            "actions": [],
            "completed_cycles": status.get("completed_cycles", 0),
            "blockers": status.get("blockers") or [],
        }
    run_row = await _ensure_full_history_automation_run(
        db,
        actor,
        trigger_type="scheduled",
        request={
            "cycles": TRAINING_FULL_HISTORY_CYCLE_COUNT,
            "min_sample_count": 4,
            "capture_sources": True,
            "days": TRAINING_FULL_HISTORY_AUTO_DAYS,
            "per_source_limit": TRAINING_FULL_HISTORY_AUTO_PER_SOURCE_LIMIT,
            "materialize_limit": TRAINING_FULL_HISTORY_AUTO_MATERIALIZE_LIMIT,
            "background_dispatch": True,
        },
        training_gateway_id=training_gateway_id or None,
        deployment_gateway_id=deployment_gateway_id or None,
    )
    if not status.get("ready_to_run"):
        await _update_full_history_automation_run(
            db,
            run_row,
            status_payload=status,
            result={"status": "blocked", "blockers": status.get("blockers") or []},
            force_status="blocked",
        )
        await db.commit()
        return {
            "ok": False,
            "status": "blocked",
            "started_at": isoformat_bjt(started_at),
            "finished_at": isoformat_bjt(now_bjt()),
            "advanced": 0,
            "active": 0,
            "actions": [],
            "blockers": status.get("blockers") or [],
        }

    data_preparation = await _prepare_full_history_training_data(
        db,
        actor,
        run_row,
        capture_sources=True,
        days=TRAINING_FULL_HISTORY_AUTO_DAYS,
        per_source_limit=TRAINING_FULL_HISTORY_AUTO_PER_SOURCE_LIMIT,
        materialize_limit=TRAINING_FULL_HISTORY_AUTO_MATERIALIZE_LIMIT,
    )
    jobs = await _full_history_cycle_jobs(db, actor)
    latest_by_cycle: dict[int, TrainingJob] = {}
    for job in jobs:
        cycle = _full_history_job_cycle_index(job)
        if cycle and cycle not in latest_by_cycle:
            latest_by_cycle[cycle] = job

    for cycle in range(1, TRAINING_FULL_HISTORY_CYCLE_COUNT + 1):
        deployment = await _latest_full_history_deployment(
            db,
            cycle_index=cycle,
            statuses=TRAINING_FULL_HISTORY_COMPLETED_DEPLOYMENT_STATUSES,
        )
        if deployment is not None and deployment.status in TRAINING_FULL_HISTORY_COMPLETED_DEPLOYMENT_STATUSES:
            continue

        job = latest_by_cycle.get(cycle)
        if job is not None and job.status in TRAINING_FULL_HISTORY_BACKGROUND_FINAL_STATUSES:
            failed_attempts = sum(
                1
                for item in jobs
                if _full_history_job_cycle_index(item) == cycle
                and item.status in TRAINING_FULL_HISTORY_BACKGROUND_FINAL_STATUSES
            )
            if failed_attempts < 3:
                actions.append({
                    "cycle_index": cycle,
                    "action": "retry_failed_cycle",
                    "status": "queued",
                    "previous_job_id": job.id,
                    "previous_job_status": job.status,
                    "attempt": failed_attempts + 1,
                })
                job = None
        if job is None:
            parent_deployment = None
            if cycle > 1:
                parent_deployment = await _latest_full_history_deployment(db, cycle_index=cycle - 1)
                if parent_deployment is None:
                    actions.append({
                        "cycle_index": cycle,
                        "action": "wait_parent_deployment",
                        "status": "blocked",
                        "reason": "previous_cycle_not_deployed",
                    })
                    break
            job_id, create_action = await _ensure_full_history_cycle_job(
                db,
                actor,
                cycle=cycle,
                cycles=TRAINING_FULL_HISTORY_CYCLE_COUNT,
                min_sample_count=4,
                training_gateway_id=training_gateway_id,
                deployment_gateway_id=deployment_gateway_id,
                parent_deployment=parent_deployment,
            )
            actions.append(create_action)
            advanced += 1
            if not job_id or advanced >= clean_limit:
                break
            job = await db.get(TrainingJob, job_id)
            if job is None:
                break

        if _normalize_full_history_job_bridge_dataset(job):
            await db.commit()
            await db.refresh(job)

        if job.status in TRAINING_FULL_HISTORY_BACKGROUND_OPEN_STATUSES:
            try:
                result = await auto_advance_training_job(
                    db,
                    actor,
                    job.id,
                    {
                        "dispatch": True,
                        "evaluate": True,
                        "metadata": {
                            "trigger": "full_history_background_loop",
                            "cycle_index": cycle,
                        },
                    },
                )
            except AppError as exc:
                await db.rollback()
                actions.append({
                    "cycle_index": cycle,
                    "action": "auto_advance",
                    "status": "failed",
                    "job_id": job.id,
                    "error_code": exc.code,
                    "detail": exc.detail,
                })
                break
            actions.append({
                "cycle_index": cycle,
                "action": "auto_advance",
                "status": "completed",
                "job_id": job.id,
                "actions": result.get("actions") or [],
                "job_status": _safe_dict(result.get("job")).get("status"),
            })
            advanced += 1
            break

        if job.status in TRAINING_FULL_HISTORY_BACKGROUND_ACTIVE_STATUSES:
            active += 1
            actions.append({
                "cycle_index": cycle,
                "action": "wait_cycle_completion",
                "status": "running",
                "job_id": job.id,
                "job_status": job.status,
            })
            break

        if job.status in TRAINING_FULL_HISTORY_BACKGROUND_FINAL_STATUSES:
            actions.append({
                "cycle_index": cycle,
                "action": "wait_cycle_completion",
                "status": "blocked",
                "job_id": job.id,
                "job_status": job.status,
                "failure_stage": job.failure_stage,
            })
            break

    after = await get_full_history_finetune_status(db, actor)
    result = {
        "ok": True,
        "status": "active" if active else ("advanced" if advanced else "idle"),
        "started_at": isoformat_bjt(started_at),
        "finished_at": isoformat_bjt(now_bjt()),
        "advanced": advanced,
        "active": active,
        "actions": actions,
        "completed_cycles": after.get("completed_cycles", 0),
        "blockers": after.get("blockers") or [],
    }
    await _update_full_history_automation_run(
        db,
        run_row,
        status_payload=after,
        actions=actions,
        result=result,
        data_preparation=data_preparation,
        force_status="completed" if after.get("completed_cycles", 0) >= TRAINING_FULL_HISTORY_CYCLE_COUNT else None,
    )
    await db.commit()
    return result


async def _run_full_history_finetune_automation_background() -> None:
    from app.database import async_session_factory

    async with async_session_factory() as db:
        await advance_full_history_finetune_automation(db, limit=1)


def kickoff_full_history_finetune_automation() -> bool:
    global _FULL_HISTORY_FINETUNE_AUTOMATION_TASK
    task = _FULL_HISTORY_FINETUNE_AUTOMATION_TASK
    if task is not None and not task.done():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    task = loop.create_task(_run_full_history_finetune_automation_background())
    _FULL_HISTORY_FINETUNE_AUTOMATION_TASK = task

    def _done(done_task: asyncio.Task) -> None:
        global _FULL_HISTORY_FINETUNE_AUTOMATION_TASK
        if _FULL_HISTORY_FINETUNE_AUTOMATION_TASK is done_task:
            _FULL_HISTORY_FINETUNE_AUTOMATION_TASK = None
        try:
            done_task.result()
        except asyncio.CancelledError:
            logger.info("[full-history-finetune] background kickoff cancelled")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[full-history-finetune] background kickoff failed: {}", exc, exc_info=True)

    task.add_done_callback(_done)
    return True


async def chat_full_history_finetuned_model(
    db: AsyncSession,
    user: User,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    status = await get_full_history_finetune_status(db, user)
    chat = _safe_dict(status.get("chat"))
    if chat.get("ready") is not True:
        raise AppError("FULL_HISTORY_MODEL_NOT_READY", 409, {"detail": chat.get("disabled_reason") or "finetuned model is not ready", "status": status})
    messages = raw.get("messages")
    prompt = str(raw.get("prompt") or "").strip()
    if not messages and prompt:
        messages = [{"role": "user", "content": prompt}]
    if not messages:
        messages = [{"role": "user", "content": TRAINING_FULL_HISTORY_CHAT_DEFAULT_PROMPT}]
    max_tokens = max(1, min(_safe_int(raw.get("max_tokens") or raw.get("max_completion_tokens") or 512), 4096))
    timeout_seconds = max(60, min(_safe_int(raw.get("timeout_seconds") or 600), 3600))
    openwebui_error: AppError | None = None
    try:
        result = await proxy_training_gateway_openwebui_chat_completion(
            db,
            user,
            str(chat.get("gateway_id") or ""),
            {
                "model": chat.get("model_id"),
                "profile": TRAINING_FULL_HISTORY_MODEL_PROFILE,
                "messages": messages,
                "max_tokens": max_tokens,
                "timeout_seconds": timeout_seconds,
            },
        )
        return {
            **result,
            "skill_id": TRAINING_FULL_HISTORY_CHAT_SKILL_ID,
            "deployment": status.get("latest_deployment"),
            "gateway_id": chat.get("gateway_id"),
            "skillforge_backend": "mac_openwebui",
        }
    except AppError as exc:
        openwebui_error = exc

    latest_deployment = await _latest_full_history_deployment(db)
    latest_job = await db.get(TrainingJob, latest_deployment.job_id) if latest_deployment is not None else None
    artifact_ref = _safe_dict(getattr(latest_deployment, "artifact_ref_json", None))
    source_uri = str(
        artifact_ref.get("source_uri")
        or artifact_ref.get("training_uri")
        or artifact_ref.get("source_path")
        or ""
    ).strip()
    training_gateway_id = str(
        artifact_ref.get("training_gateway_id")
        or getattr(latest_job, "target_gateway_id", None)
        or ""
    ).strip()
    if not source_uri or not training_gateway_id:
        raise AppError(
            "FULL_HISTORY_CHAT_FALLBACK_UNAVAILABLE",
            502,
            {
                "detail": "Mac OpenWebUI failed and no GB10 source adapter is available for fallback inference",
                "openwebui_error": {
                    "code": openwebui_error.code,
                    "detail": _sanitize_training_result_value(openwebui_error.detail or {}),
                },
            },
        ) from openwebui_error
    instance = await db.get(OpenClawInstance, training_gateway_id)
    if instance is None or not bridge_registry.is_online(training_gateway_id):
        raise AppError(
            "FULL_HISTORY_CHAT_FALLBACK_UNAVAILABLE",
            502,
            {
                "detail": "Mac OpenWebUI failed and the training gateway fallback is offline",
                "fallback_gateway_id": training_gateway_id,
                "openwebui_error": {
                    "code": openwebui_error.code,
                    "detail": _sanitize_training_result_value(openwebui_error.detail or {}),
                },
            },
        ) from openwebui_error
    prompt_text = _openai_proxy_messages_to_prompt(messages)
    inference = await AIClawClient(
        training_gateway_id,
        gateway_kind=instance.bridge_gateway_kind,
    ).run_training_inference(
        {
            "profile": TRAINING_FULL_HISTORY_MODEL_PROFILE,
            "runtime_profile": raw.get("fallback_runtime_profile") or "cuda-qwen3.5-4b-lora",
            "artifact_uri": source_uri,
            "artifact_sha256": artifact_ref.get("sha256"),
            "prompt": prompt_text,
            "max_new_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
            "deployment_id": getattr(latest_deployment, "id", None),
        },
        timeout=timeout_seconds + 60,
    )
    safe_inference = _safe_dict(_sanitize_training_result_value(inference))
    if safe_inference.get("ok") is not True:
        raise AppError(
            "FULL_HISTORY_CHAT_FALLBACK_FAILED",
            502,
            {
                "detail": safe_inference.get("error") or "GB10 training inference fallback failed",
                "fallback_gateway_id": training_gateway_id,
                "result": safe_inference,
                "openwebui_error": {
                    "code": openwebui_error.code,
                    "detail": _sanitize_training_result_value(openwebui_error.detail or {}),
                },
            },
        ) from openwebui_error
    text = str(safe_inference.get("text") or "")
    metrics = _safe_dict(safe_inference.get("metrics"))
    completion_tokens = _safe_int(metrics.get("generated_tokens")) or (max(1, len(text) // 4) if text else 0)
    prompt_tokens = _safe_int(metrics.get("prompt_tokens"))
    response_id = "chatcmpl-sf-full-history-" + hashlib.sha256(
        f"{time()}:{getattr(latest_deployment, 'id', '')}:{training_gateway_id}".encode()
    ).hexdigest()[:16]
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training.full_history_chat_fallback_inference",
        target_type="model_deployment",
        target_id=str(getattr(latest_deployment, "id", "") or ""),
        detail={
            "skill_id": TRAINING_FULL_HISTORY_CHAT_SKILL_ID,
            "job_id": getattr(latest_job, "id", None),
            "mac_gateway_id": chat.get("gateway_id"),
            "fallback_gateway_id": training_gateway_id,
            "openwebui_error_code": openwebui_error.code,
            "metrics": metrics,
        },
    ))
    await db.commit()
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": int(time()),
        "model": str(chat.get("model_id") or TRAINING_FULL_HISTORY_MODEL_PROFILE),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": str(safe_inference.get("finish_reason") or "stop"),
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "skill_id": TRAINING_FULL_HISTORY_CHAT_SKILL_ID,
        "deployment": status.get("latest_deployment"),
        "deployment_id": getattr(latest_deployment, "id", None),
        "gateway_id": chat.get("gateway_id"),
        "skillforge_backend": "gb10_training_inference_fallback",
        "fallback_gateway_id": training_gateway_id,
        "mac_openwebui_error_code": openwebui_error.code,
        "metrics": metrics,
    }


async def chat_training_deployment_model(
    db: AsyncSession,
    user: User,
    deployment_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.aiclaw.router import _chat_inference_metrics, _chat_inference_text, _prepare_chat_model_inference

    raw = payload if isinstance(payload, dict) else {}
    messages = raw.get("messages")
    prompt = str(raw.get("prompt") or "").strip()
    message_text = _openai_proxy_messages_to_prompt(messages) if messages else ""
    if not message_text:
        message_text = prompt
    if not message_text:
        raise AppError(
            "TRAINING_DEPLOYMENT_CHAT_INVALID_REQUEST",
            422,
            {"detail": "messages or prompt is required"},
        )

    requested_max_tokens = max(1, min(_safe_int(raw.get("max_tokens") or raw.get("max_completion_tokens") or 512), 4096))
    timeout_seconds = max(60, min(_safe_int(raw.get("timeout_seconds") or 300), 1800))
    gateway_id, gateway_kind, inference_payload = await _prepare_chat_model_inference(
        db,
        user,
        message=message_text,
        model_context={"model_deployment_id": deployment_id},
    )
    prepared_max_tokens = _safe_int(inference_payload.get("max_new_tokens")) or requested_max_tokens
    effective_max_tokens = max(1, min(requested_max_tokens, prepared_max_tokens, 4096))
    inference_payload["max_new_tokens"] = effective_max_tokens
    inference_payload["timeout_seconds"] = timeout_seconds
    generation_policy = _safe_dict(inference_payload.get("generation_policy"))
    generation_policy.update({
        "requested_max_tokens": requested_max_tokens,
        "max_new_tokens": effective_max_tokens,
    })
    inference_payload["generation_policy"] = generation_policy

    result = await AIClawClient(gateway_id, gateway_kind=gateway_kind).run_training_inference(
        inference_payload,
        timeout=timeout_seconds + 60,
    )
    safe_result = _safe_dict(_sanitize_training_result_value(result))
    if safe_result.get("ok") is False or str(safe_result.get("status") or "").lower() in {"failed", "error"}:
        raise AppError(
            "TRAINING_DEPLOYMENT_CHAT_FAILED",
            502,
            {
                "detail": safe_result.get("error") or "training deployment inference failed",
                "gateway_id": gateway_id,
                "deployment_id": deployment_id,
                "result": safe_result,
            },
        )

    text = _chat_inference_text(safe_result)
    metrics = _chat_inference_metrics(
        safe_result,
        gateway_id=gateway_id,
        gateway_kind=gateway_kind,
        payload=inference_payload,
    )
    completion_tokens = _safe_int(metrics.get("generated_tokens")) or (max(1, len(text) // 4) if text else 0)
    prompt_tokens = _safe_int(metrics.get("prompt_tokens"))
    finish_reason = str(safe_result.get("finish_reason") or safe_result.get("finishReason") or "stop")
    model_context = _safe_dict(inference_payload.get("model_context"))
    response_id = "chatcmpl-sf-deployment-" + hashlib.sha256(
        f"{time()}:{deployment_id}:{gateway_id}".encode()
    ).hexdigest()[:16]

    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training.deployment_chat_inference",
        target_type="model_deployment",
        target_id=deployment_id,
        detail={
            "gateway_id": gateway_id,
            "gateway_kind": gateway_kind,
            "model_profile": inference_payload.get("profile"),
            "runtime_profile": inference_payload.get("runtime_profile"),
            "max_tokens": effective_max_tokens,
            "fallback_reason": model_context.get("inference_gateway_fallback_reason"),
            "metrics": metrics,
        },
    ))
    await db.commit()
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": int(time()),
        "model": str(inference_payload.get("profile") or deployment_id),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "deployment_id": deployment_id,
        "model_deployment_id": deployment_id,
        "gateway_id": gateway_id,
        "skillforge_backend": "bridge_local_lora_inference",
        "fallback_reason": model_context.get("inference_gateway_fallback_reason") or "",
        "metrics": metrics,
        "model_context": model_context,
        "finish_reason": finish_reason,
    }


async def get_training_gateway_bootstrap_status(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    instance = await _get_training_gateway_for_bootstrap(db, user, gateway_id)
    normalized = _normalize_training_bootstrap_request(payload or {})
    if not bridge_registry.is_online(instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"实例 {instance.id} 的 bridge 未连接"})
    result = await AIClawClient(
        instance.id,
        gateway_kind=instance.bridge_gateway_kind,
    ).get_training_env_status(
        {"profile": normalized["profile"], "cuda": normalized["cuda"]},
        timeout=30,
    )
    return {
        "gateway": _training_resource_from_instance(instance, online=True),
        "bootstrap": _sanitize_training_result_value(result),
    }


async def bootstrap_training_gateway_env(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    instance = await _get_training_gateway_for_bootstrap(db, user, gateway_id)
    normalized = _normalize_training_bootstrap_request(payload or {})
    if not bridge_registry.is_online(instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"实例 {instance.id} 的 bridge 未连接"})
    bridge_payload = {
        "profile": normalized["profile"],
        "cuda": normalized["cuda"],
        "force": normalized["force"],
        "dry_run": normalized["dry_run"],
        "timeout_seconds": normalized["timeout_seconds"],
    }
    result = await AIClawClient(
        instance.id,
        gateway_kind=instance.bridge_gateway_kind,
    ).bootstrap_training_env(
        bridge_payload,
        timeout=normalized["timeout_seconds"] + 60,
    )
    previous_agent_purpose = _agent_purpose(instance)
    requested_agent_purpose = normalized.get("agent_purpose")
    if requested_agent_purpose and not normalized["dry_run"]:
        instance.agent_purpose = requested_agent_purpose
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training_gateway.bootstrap_env",
        target_type="training_gateway",
        target_id=instance.id,
        detail={
            "department": instance.department,
            "profile": normalized["profile"],
            "cuda": normalized["cuda"],
            "dry_run": normalized["dry_run"],
            "force": normalized["force"],
            "previous_agent_purpose": previous_agent_purpose,
            "agent_purpose": _agent_purpose(instance),
            "bootstrap_status": result.get("status") if isinstance(result, dict) else None,
        },
    ))
    try:
        await db.commit()
        await db.refresh(instance)
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.warning(
            "training model test audit write failed gateway={} profile={}: {}",
            instance.id,
            normalized["profile"],
            _extract_error_message(exc),
        )
    return {
        "gateway": _training_resource_from_instance(instance, online=True),
        "bootstrap": _sanitize_training_result_value(result),
        "agent_purpose_updated": (
            requested_agent_purpose
            if requested_agent_purpose and requested_agent_purpose != previous_agent_purpose and not normalized["dry_run"]
            else None
        ),
    }


async def discover_training_gateway_python_envs(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    instance = await _get_training_gateway_for_bootstrap(db, user, gateway_id)
    raw = payload if isinstance(payload, dict) else {}
    try:
        timeout_seconds = max(15, min(int(raw.get("timeout_seconds") or 120), 600))
    except Exception:
        timeout_seconds = 120
    bridge_payload: dict[str, Any] = {
        "max_candidates": raw.get("max_candidates") or raw.get("maxCandidates") or 30,
        "timeout_seconds": raw.get("probe_timeout_seconds") or raw.get("probeTimeoutSeconds") or 12,
    }
    if isinstance(raw.get("candidates"), list):
        bridge_payload["candidates"] = raw["candidates"]
    if not bridge_registry.is_online(instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"实例 {instance.id} 的 bridge 未连接"})
    result = await AIClawClient(
        instance.id,
        gateway_kind=instance.bridge_gateway_kind,
    ).discover_training_python_envs(
        bridge_payload,
        timeout=timeout_seconds,
    )
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training_gateway.discover_python_envs",
        target_type="training_gateway",
        target_id=instance.id,
        detail={
            "department": instance.department,
            "cuda_ready_count": result.get("cuda_ready_count") if isinstance(result, dict) else None,
            "probed_count": result.get("probed_count") if isinstance(result, dict) else None,
        },
    ))
    await db.commit()
    return {
        "gateway": _training_resource_from_instance(instance, online=True),
        "python_envs": _sanitize_training_result_value(result),
    }


async def get_training_gateway_model_status(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    instance = await _get_training_gateway_for_bootstrap(db, user, gateway_id)
    normalized = _normalize_training_model_request(payload or {})
    if not bridge_registry.is_online(instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"实例 {instance.id} 的 bridge 未连接"})
    bridge_payload = {
        "profile": normalized["profile"],
        "revision": normalized["revision"],
        "modelscope_revision": normalized["modelscope_revision"],
        "bootstrap_profile": normalized["bootstrap_profile"],
        "source": normalized["source"],
        "auto_discover": normalized["auto_discover"],
    }
    if normalized.get("internal_model_ref"):
        bridge_payload["internal_model_ref"] = normalized["internal_model_ref"]
    result = await AIClawClient(
        instance.id,
        gateway_kind=instance.bridge_gateway_kind,
    ).get_training_model_status(bridge_payload, timeout=30)
    return {
        "gateway": _training_resource_from_instance(instance, online=True),
        "model": _sanitize_training_result_value(result),
    }


async def discover_training_gateway_models(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    instance = await _get_training_gateway_for_bootstrap(db, user, gateway_id)
    normalized = _normalize_training_model_request(payload or {})
    if not bridge_registry.is_online(instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"实例 {instance.id} 的 bridge 未连接"})
    bridge_payload = {
        "profile": normalized["profile"],
        "revision": normalized["revision"],
        "modelscope_revision": normalized["modelscope_revision"],
        "bootstrap_profile": normalized["bootstrap_profile"],
        "source": normalized["source"],
        "auto_discover": normalized["auto_discover"],
    }
    if normalized.get("internal_model_ref"):
        bridge_payload["internal_model_ref"] = normalized["internal_model_ref"]
    result = await AIClawClient(
        instance.id,
        gateway_kind=instance.bridge_gateway_kind,
    ).discover_training_models(bridge_payload, timeout=60)
    return {
        "gateway": _training_resource_from_instance(instance, online=True),
        "discover": _sanitize_training_result_value(result),
    }


def _training_model_discover_payload(normalized: dict[str, Any], *, roots: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
    payload = {
        "profile": normalized["profile"],
        "revision": normalized["revision"],
        "modelscope_revision": normalized["modelscope_revision"],
        "bootstrap_profile": normalized["bootstrap_profile"],
        "source": normalized["source"],
        "auto_discover": normalized["auto_discover"],
        "roots": roots if roots is not None else normalized.get("roots") or [],
        "terms": normalized.get("terms") or [],
        "max_dirs": normalized.get("max_dirs") or 12000,
        "max_depth": normalized.get("max_depth") or 9,
        "limit": limit or normalized.get("limit") or 12,
        "include_metadata": normalized.get("include_metadata") is True,
    }
    if normalized.get("internal_model_ref"):
        payload["internal_model_ref"] = normalized["internal_model_ref"]
    return payload


def _training_model_discover_gateway_priority(instance: OpenClawInstance, training_gateway_id: str) -> tuple[int, str]:
    instance_id = str(getattr(instance, "id", "") or "")
    if instance_id == training_gateway_id:
        return (0, instance_id)
    if instance_id in TRAINING_PRIMARY_GATEWAY_IDS:
        return (1, instance_id)
    if instance_id in TRAINING_MAC_DEPLOYMENT_SEED_GATEWAY_IDS:
        return (2, instance_id)
    if instance_id in TRAINING_COMFYUI_RESERVED_GATEWAY_IDS:
        return (3, instance_id)
    policy = _training_gateway_route_policy(instance)
    if policy.get("gb10_training_pool"):
        return (10, instance_id)
    if policy.get("mac_deployment_pool"):
        return (20, instance_id)
    if policy.get("reserved_for"):
        return (30, instance_id)
    return (40, instance_id)


def _training_model_candidate_rows(gateway_id: str, discover: dict[str, Any], *, train_ready: bool = False, verified_on_training_gateway: bool = False) -> list[dict[str, Any]]:
    rows = []
    for item in discover.get("candidates") if isinstance(discover.get("candidates"), list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path.startswith("/"):
            continue
        rows.append({
            **item,
            "path": path,
            "gateway_id": gateway_id,
            "source_gateway_id": item.get("source_gateway_id") or gateway_id,
            "train_ready": train_ready,
            "verified_on_training_gateway": verified_on_training_gateway,
        })
    return rows


async def _training_explore_gateway(
    instance: OpenClawInstance,
    normalized: dict[str, Any],
    *,
    roots: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    resource = _training_resource_from_instance(instance, online=bridge_registry.is_online(instance.id))
    if not resource["online"]:
        return {"gateway": resource, "discover": {"status": "skipped", "reason": "bridge_offline", "candidates": []}}
    ops = set(resource.get("ops") or [])
    payload = _training_model_discover_payload(normalized, roots=roots, limit=limit)
    client = AIClawClient(instance.id, gateway_kind=instance.bridge_gateway_kind)
    try:
        if "training.explore_model_paths" in ops:
            result = await client.explore_training_model_paths(payload, timeout=120)
        elif "training.discover_models" in ops and not payload.get("roots") and not payload.get("terms"):
            result = await client.discover_training_models(payload, timeout=60)
        else:
            result = {"status": "skipped", "reason": "explore_op_unavailable", "candidates": []}
    except Exception as exc:  # noqa: BLE001
        result = {"status": "failed", "reason": "explore_failed", "error": _extract_error_message(exc), "candidates": []}
    return {
        "gateway": resource,
        "discover": _sanitize_training_result_value(result if isinstance(result, dict) else {}),
    }


async def discover_training_models_across_gateways(
    db: AsyncSession,
    user: User,
    payload: dict | None = None,
) -> dict[str, Any]:
    normalized = _normalize_training_model_discover_all_request(payload or {})
    target_gateway_ids = set(normalized.get("target_gateway_ids") or [])
    if not target_gateway_ids:
        target_gateway_ids.update(TRAINING_MODEL_DISCOVER_ALL_DEFAULT_GATEWAY_IDS)
    target_gateway_ids.add(str(normalized["training_gateway_id"]))

    stmt = select(OpenClawInstance).where(OpenClawInstance.is_active.is_(True))
    if not _can_view_all_training_resources(user):
        department = _user_department(user)
        stmt = stmt.where(
            or_(
                OpenClawInstance.department == department,
                OpenClawInstance.is_platform_default.is_(True),
            )
            if department else OpenClawInstance.is_platform_default.is_(True)
        )
    rows = (await db.execute(stmt)).scalars().all()
    selected = [
        row for row in rows
        if (
            str(getattr(row, "id", "") or "") in target_gateway_ids
            or bridge_registry.is_online(str(getattr(row, "id", "") or ""))
        )
    ]
    selected.sort(key=lambda item: _training_model_discover_gateway_priority(item, normalized["training_gateway_id"]))
    selected = selected[:12]
    instance_by_id = {str(getattr(item, "id", "") or ""): item for item in selected}

    items = await asyncio.gather(*[
        _training_explore_gateway(instance, normalized)
        for instance in selected
    ]) if selected else []

    flattened: list[dict[str, Any]] = []
    for item in items:
        gateway_id = str(_safe_dict(item.get("gateway")).get("id") or "")
        train_ready = gateway_id == normalized["training_gateway_id"]
        flattened.extend(_training_model_candidate_rows(gateway_id, _safe_dict(item.get("discover")), train_ready=train_ready))

    training_instance = instance_by_id.get(str(normalized["training_gateway_id"]))
    train_ready_candidates = [dict(item) for item in flattened if item.get("train_ready")]
    probed_paths = {str(item.get("path") or "") for item in train_ready_candidates}
    if training_instance is not None and bridge_registry.is_online(training_instance.id):
        for candidate in flattened:
            path = str(candidate.get("path") or "")
            if not path or path in probed_paths or candidate.get("gateway_id") == normalized["training_gateway_id"]:
                continue
            probe_normalized = {
                **normalized,
                "max_dirs": min(int(normalized.get("max_dirs") or 12000), 500),
                "max_depth": 2,
                "limit": 3,
                "include_metadata": False,
            }
            probe = await _training_explore_gateway(training_instance, probe_normalized, roots=[path], limit=3)
            probe_candidates = _training_model_candidate_rows(
                normalized["training_gateway_id"],
                _safe_dict(probe.get("discover")),
                train_ready=True,
                verified_on_training_gateway=True,
            )
            for probe_candidate in probe_candidates:
                probe_candidate["source_gateway_id"] = candidate.get("source_gateway_id") or candidate.get("gateway_id")
                probe_candidate["source_path"] = candidate.get("path")
                train_ready_candidates.append(probe_candidate)
                probed_paths.add(str(probe_candidate.get("path") or ""))
            if len(train_ready_candidates) >= normalized["limit"]:
                break

    train_ready_candidates.sort(key=lambda item: (-_safe_int(item.get("score")), str(item.get("path") or "")))
    train_ready_paths = {str(item.get("path") or "") for item in train_ready_candidates}
    found_but_not_train_ready = [
        item for item in flattened
        if str(item.get("path") or "") not in train_ready_paths
    ]
    return {
        "profile": normalized["profile"],
        "model_id": normalized["model_id"],
        "training_gateway_id": normalized["training_gateway_id"],
        "items": items,
        "train_ready_candidates": _sanitize_training_result_value(train_ready_candidates[: normalized["limit"]]),
        "found_but_not_train_ready": _sanitize_training_result_value(found_but_not_train_ready[: normalized["limit"]]),
        "summary": {
            "gateway_count": len(items),
            "candidate_count": len(flattened),
            "train_ready_count": len(train_ready_candidates),
            "found_but_not_train_ready_count": len(found_but_not_train_ready),
        },
    }


def _training_model_transfer_candidate(discover_all: dict[str, Any], source_gateway_id: str | None = None) -> dict[str, Any] | None:
    preferred_source = str(source_gateway_id or "").strip()
    rows = _safe_list(discover_all.get("found_but_not_train_ready"))
    if preferred_source:
        rows = [
            item for item in rows
            if str(_safe_dict(item).get("gateway_id") or _safe_dict(item).get("source_gateway_id") or "").strip() == preferred_source
        ] or rows
    for item in rows:
        candidate = _safe_dict(item)
        path = str(candidate.get("path") or "").strip()
        gateway_id = str(candidate.get("gateway_id") or candidate.get("source_gateway_id") or "").strip()
        if path.startswith("/") and gateway_id:
            return candidate
    return None


def _training_instance_transfer_allowed(instance: OpenClawInstance, user: User) -> bool:
    if _can_view_all_training_resources(user):
        return True
    department = _user_department(user)
    if department and getattr(instance, "department", None) == department:
        return True
    return bool(getattr(instance, "is_platform_default", False))


def _training_resource_ops(instance: OpenClawInstance) -> set[str]:
    cap = _safe_capabilities(getattr(instance, "bridge_capabilities_json", None))
    return {str(item or "").strip() for item in (cap.get("ops") or []) if str(item or "").strip()}


def _training_transfer_safe_export_payload(exported: dict[str, Any]) -> dict[str, Any]:
    safe = dict(exported if isinstance(exported, dict) else {})
    safe.pop("token", None)
    safe.pop("export_token", None)
    return _sanitize_training_result_value(safe)


async def _resolve_training_model_transfer_context(
    db: AsyncSession,
    user: User,
    normalized: dict[str, Any],
) -> dict[str, Any]:
    target_instance = await _get_training_gateway_for_bootstrap(db, user, normalized["training_gateway_id"])
    if not bridge_registry.is_online(target_instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"训练节点 {target_instance.id} 的 bridge 未连接"})

    source_gateway_id = str(normalized.get("source_gateway_id") or "").strip()
    source_path = str(normalized.get("source_path") or "").strip()
    discover_all: dict[str, Any] | None = None
    if not source_gateway_id or not source_path:
        discover_all = await discover_training_models_across_gateways(db, user, normalized)
        candidate = _training_model_transfer_candidate(discover_all, source_gateway_id)
        if candidate is None:
            raise AppError(
                "TRAINING_MODEL_TRANSFER_SOURCE_NOT_FOUND",
                404,
                {
                    "detail": "no transferable model candidate found",
                    "summary": discover_all.get("summary") if isinstance(discover_all, dict) else None,
                },
            )
        source_gateway_id = str(candidate.get("gateway_id") or candidate.get("source_gateway_id") or "").strip()
        source_path = str(candidate.get("path") or "").strip()

    if not source_gateway_id or not source_path:
        raise AppError("TRAINING_MODEL_TRANSFER_INVALID", 422, {"detail": "source_gateway_id and source_path are required"})
    if not source_path.startswith("/"):
        raise AppError("TRAINING_MODEL_TRANSFER_INVALID", 422, {"detail": "source_path must be absolute"})

    source_instance = await db.get(OpenClawInstance, source_gateway_id)
    if source_instance is None or getattr(source_instance, "is_active", True) is False:
        raise AppError("TRAINING_MODEL_TRANSFER_SOURCE_NOT_FOUND", 404, {"detail": f"源节点 {source_gateway_id} 不存在或未启用"})
    if not _training_instance_transfer_allowed(source_instance, user):
        raise AppError("TRAINING_MODEL_TRANSFER_FORBIDDEN", 403, {"detail": "no access to source gateway"})
    if not bridge_registry.is_online(source_instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"源节点 {source_instance.id} 的 bridge 未连接"})

    return {
        "target_instance": target_instance,
        "source_instance": source_instance,
        "source_path": source_path,
        "discover_all": discover_all,
    }


def _training_model_transfer_source_host(source_gateway_id: str, explicit_host: str | None = None) -> str:
    explicit = str(explicit_host or "").strip()
    if explicit:
        return explicit
    override = TRAINING_MODEL_TRANSFER_HOST_OVERRIDES.get(str(source_gateway_id or "").strip())
    if override:
        return override
    return ""


def _training_model_transfer_rewrite_url_host(value: Any, host: str) -> str:
    text = str(value or "").strip()
    host = str(host or "").strip()
    if not text or not host:
        return text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return text
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _training_model_transfer_should_relay(exc: Exception) -> bool:
    text = _extract_error_message(exc).lower()
    return any(
        marker in text
        for marker in (
            "urlopen error",
            "no route to host",
            "connection timed out",
            "timed out",
            "connection refused",
            "network is unreachable",
        )
    )


def _training_model_transfer_is_transient_error(exc: Exception) -> bool:
    text = _extract_error_message(exc).lower()
    return any(
        marker in text
        for marker in (
            "disconnected",
            "connection closed",
            "close message has been sent",
            "connection reset",
            "connection aborted",
            "connection timed out",
            "timed out",
            "timeout",
            "bridge offline",
            "bridge not connected",
            "bridge 未连接",
            "aiclaw bridge 未连接",
            "websocket",
            "websocketdisconnect",
            "broken pipe",
            "temporarily unavailable",
            "replaced",
        )
    )


async def _relay_training_model_via_platform(
    *,
    source_client: AIClawClient,
    target_client: AIClawClient,
    normalized: dict[str, Any],
    source_gateway_id: str,
    source_path: str,
    progress_cb: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
) -> dict[str, Any]:
    async def _relay_call(label: str, factory: Callable[[], Awaitable[dict[str, Any]]], event: dict[str, Any] | None = None) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt, delay in enumerate((0, *TRAINING_MODEL_TRANSFER_RELAY_RETRY_DELAYS), start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await factory()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _training_model_transfer_is_transient_error(exc):
                    raise
                if attempt > len(TRAINING_MODEL_TRANSFER_RELAY_RETRY_DELAYS):
                    break
                if progress_cb and event:
                    maybe_awaitable = progress_cb({
                        **event,
                        "status": "running",
                        "transfer_mode": "platform_relay",
                    })
                    if maybe_awaitable:
                        await maybe_awaitable
                logger.warning(
                    "[training-model-transfer] relay {} transient error attempt={} retry_in={}s error={}",
                    label,
                    attempt,
                    delay if delay else TRAINING_MODEL_TRANSFER_RELAY_RETRY_DELAYS[min(attempt - 1, len(TRAINING_MODEL_TRANSFER_RELAY_RETRY_DELAYS) - 1)],
                    _extract_error_message(exc),
                )
        if last_exc is not None:
            raise last_exc
        raise AppError("TRAINING_MODEL_TRANSFER_RELAY_FAILED", 502, {"detail": f"{label} failed"})

    manifest = await _relay_call(
        "manifest",
        lambda: source_client.export_training_model_manifest(
            {
                "profile": normalized["profile"],
                "source": "internal",
                "source_path": source_path,
                "internal_model_ref": source_path,
                "validate_mode": "metadata",
                "hash_files": normalized["hash_files"],
            },
            timeout=normalized["timeout_seconds"],
        ),
    )
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    manifest_sha = str(manifest.get("manifest_sha256") or "").strip()
    resume_manifest_sha = str(normalized.get("resume_manifest_sha256") or "").strip().lower()
    if resume_manifest_sha and re.fullmatch(r"[a-f0-9]{24,64}", resume_manifest_sha):
        manifest = {**manifest, "manifest_sha256": resume_manifest_sha}
        manifest_sha = resume_manifest_sha
    resume_remaining_bytes = max(0, _safe_int(normalized.get("resume_transferred_bytes"))) if resume_manifest_sha else 0
    target_supports_file_status = bool(normalized.get("target_supports_relay_file_status"))
    if resume_manifest_sha or resume_remaining_bytes:
        logger.info(
            "[training-model-transfer] relay resume manifest_sha={} transferred_bytes={} target_file_status={}",
            resume_manifest_sha or None,
            resume_remaining_bytes,
            target_supports_file_status,
        )
    if not files or not manifest_sha:
        raise AppError(
            "TRAINING_MODEL_TRANSFER_RELAY_FAILED",
            502,
            {"detail": "source relay manifest is empty"},
        )
    total_bytes = _safe_int(manifest.get("total_size_bytes"))
    if total_bytes <= 0:
        total_bytes = sum(_safe_int(_safe_dict(item).get("size_bytes")) for item in files)
    initial_transferred_bytes = min(resume_remaining_bytes, total_bytes) if total_bytes > 0 else resume_remaining_bytes
    initial_imported_files = max(0, _safe_int(normalized.get("resume_imported_files"))) if initial_transferred_bytes else 0
    if progress_cb:
        initial_progress = (initial_transferred_bytes / total_bytes) if total_bytes > 0 else 0.0
        maybe_awaitable = progress_cb({
            "status": "running",
            "transfer_mode": "platform_relay",
            "manifest_sha256": manifest_sha,
            "total_bytes": total_bytes,
            "file_count": len(files),
            "transferred_bytes": initial_transferred_bytes,
            "imported_files": initial_imported_files,
            "current_file": normalized.get("resume_current_file") or None,
            "progress": max(0.0, min(initial_progress, 0.99)),
        })
        if maybe_awaitable:
            await maybe_awaitable
    imported_files = 0
    transferred_bytes = 0
    for entry in files:
        if not isinstance(entry, dict):
            continue
        relpath = str(entry.get("path") or "").strip()
        size = _safe_int(entry.get("size_bytes"))
        if not relpath:
            continue
        status_event = {
            "manifest_sha256": manifest_sha,
            "total_bytes": total_bytes,
            "file_count": len(files),
            "transferred_bytes": transferred_bytes,
            "imported_files": imported_files,
            "current_file": relpath,
            "progress": max(0.0, min((transferred_bytes / total_bytes) if total_bytes > 0 else 0.0, 0.99)),
        }
        file_status: dict[str, Any] = {}
        existing_size = 0
        if target_supports_file_status:
            file_status = await _relay_call(
                f"file_status:{relpath}",
                lambda: target_client.get_training_model_relay_file_status(
                    {
                        "profile": normalized["profile"],
                        "source": "internal",
                        "manifest_sha256": manifest_sha,
                        "file_path": relpath,
                        "hash_file": bool(entry.get("sha256")),
                    },
                    timeout=30,
                ),
                status_event,
            )
            existing_size = _safe_int(file_status.get("size_bytes")) if isinstance(file_status, dict) else 0
        elif resume_remaining_bytes > 0:
            existing_size = min(size, resume_remaining_bytes)
            resume_remaining_bytes = max(0, resume_remaining_bytes - existing_size)
        expected_sha = str(entry.get("sha256") or "").strip().lower()
        actual_sha = str(_safe_dict(file_status).get("sha256") or "").strip().lower()
        resume_file_exists = bool(file_status.get("exists") is True or (not target_supports_file_status and existing_size > 0))
        if resume_file_exists and size >= 0 and existing_size == size:
            if not expected_sha or actual_sha == expected_sha:
                transferred_bytes += size
                imported_files += 1
                if progress_cb:
                    progress = (transferred_bytes / total_bytes) if total_bytes > 0 else 0.0
                    maybe_awaitable = progress_cb({
                        "status": "running",
                        "transfer_mode": "platform_relay",
                        "manifest_sha256": manifest_sha,
                        "total_bytes": total_bytes,
                        "file_count": len(files),
                        "transferred_bytes": transferred_bytes,
                        "imported_files": imported_files,
                        "current_file": relpath,
                        "progress": max(0.0, min(progress, 0.99)),
                    })
                    if maybe_awaitable:
                        await maybe_awaitable
                continue
        offset = 0
        if 0 < existing_size < size:
            offset = existing_size
            transferred_bytes += existing_size
        while offset < size or (size == 0 and offset == 0):
            progress_event = {
                "manifest_sha256": manifest_sha,
                "total_bytes": total_bytes,
                "file_count": len(files),
                "transferred_bytes": transferred_bytes,
                "imported_files": imported_files,
                "current_file": relpath,
                "progress": max(0.0, min((transferred_bytes / total_bytes) if total_bytes > 0 else 0.0, 0.99)),
            }
            chunk = await _relay_call(
                f"export_chunk:{relpath}:{offset}",
                lambda: source_client.export_training_model_file_chunk(
                    {
                        "profile": normalized["profile"],
                        "source": "internal",
                        "source_path": source_path,
                        "file_path": relpath,
                        "offset": offset,
                        "max_bytes": TRAINING_MODEL_TRANSFER_RELAY_CHUNK_BYTES,
                    },
                    timeout=600,
                ),
                progress_event,
            )
            content_b64 = str(chunk.get("content_base64") or "")
            chunk_size = _safe_int(chunk.get("size_bytes"))
            if not content_b64 and chunk_size:
                raise AppError(
                    "TRAINING_MODEL_TRANSFER_RELAY_FAILED",
                    502,
                    {"detail": f"source relay chunk missing content: {relpath}"},
                )
            await _relay_call(
                f"import_chunk:{relpath}:{offset}",
                lambda: target_client.import_training_model_relay_chunk(
                    {
                        "profile": normalized["profile"],
                        "source": "internal",
                        "manifest_sha256": manifest_sha,
                        "file_path": relpath,
                        "offset": offset,
                        "content_base64": content_b64,
                    },
                    timeout=600,
                ),
                progress_event,
            )
            transferred_bytes += chunk_size
            if progress_cb:
                progress = (transferred_bytes / total_bytes) if total_bytes > 0 else 0.0
                maybe_awaitable = progress_cb({
                    "status": "running",
                    "transfer_mode": "platform_relay",
                    "manifest_sha256": manifest_sha,
                    "total_bytes": total_bytes,
                    "file_count": len(files),
                    "transferred_bytes": transferred_bytes,
                    "imported_files": imported_files,
                    "current_file": relpath,
                    "progress": max(0.0, min(progress, 0.99)),
                })
                if maybe_awaitable:
                    await maybe_awaitable
            if chunk.get("eof") is True or chunk_size == 0:
                break
            offset += chunk_size
        imported_files += 1
        if progress_cb:
            progress = (transferred_bytes / total_bytes) if total_bytes > 0 else 0.0
            maybe_awaitable = progress_cb({
                "status": "running",
                "transfer_mode": "platform_relay",
                "manifest_sha256": manifest_sha,
                "total_bytes": total_bytes,
                "file_count": len(files),
                "transferred_bytes": transferred_bytes,
                "imported_files": imported_files,
                "current_file": relpath,
                "progress": max(0.0, min(progress, 0.99)),
            })
            if maybe_awaitable:
                await maybe_awaitable
    committed = await _relay_call(
        "commit",
        lambda: target_client.commit_training_model_relay_import(
            {
                "profile": normalized["profile"],
                "source": "internal",
                "manifest": manifest,
                "manifest_sha256": manifest_sha,
            },
            timeout=normalized["timeout_seconds"],
        ),
        {
            "manifest_sha256": manifest_sha,
            "total_bytes": total_bytes,
            "file_count": len(files),
            "transferred_bytes": transferred_bytes,
            "imported_files": imported_files,
            "progress": 0.99,
        },
    )
    committed["relay"] = True
    committed["source_gateway_id"] = source_gateway_id
    committed["source_path"] = source_path
    committed["imported_files"] = imported_files
    committed["transferred_bytes"] = transferred_bytes
    return committed


async def transfer_training_model_to_training_gateway(
    db: AsyncSession,
    user: User,
    payload: dict | None = None,
    *,
    progress_cb: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_training_model_transfer_request(payload or {})
    context = await _resolve_training_model_transfer_context(db, user, normalized)
    target_instance = context["target_instance"]
    source_instance = context["source_instance"]
    source_path = context["source_path"]
    discover_all = context["discover_all"]

    if source_instance.id == target_instance.id:
        prepared = await prepare_training_gateway_model(
            db,
            user,
            target_instance.id,
            {
                **normalized,
                "source": "internal",
                "internal_model_ref": source_path,
                "validate_mode": normalized.get("validate_mode") or "metadata",
                "auto_discover": False,
            },
        )
        return {
            "status": "already_on_training_gateway",
            "profile": normalized["profile"],
            "training_gateway_id": target_instance.id,
            "source_gateway_id": source_instance.id,
            "source_path": source_path,
            "internal_model_ref": source_path,
            "prepare": prepared,
            "discover_all": _sanitize_training_result_value(discover_all) if discover_all else None,
        }

    source_ops = _training_resource_ops(source_instance)
    target_ops = _training_resource_ops(target_instance)
    if "training.model_export_start" not in source_ops:
        raise AppError(
            "TRAINING_MODEL_TRANSFER_UNAVAILABLE",
            409,
            {"detail": f"源节点 {source_instance.id} bridge does not support training.model_export_start"},
        )
    if "training.model_import_from_export" not in target_ops:
        raise AppError(
            "TRAINING_MODEL_TRANSFER_UNAVAILABLE",
            409,
            {"detail": f"训练节点 {target_instance.id} bridge does not support training.model_import_from_export"},
        )
    normalized["target_supports_relay_file_status"] = "training.model_import_relay_file_status" in target_ops

    source_client = AIClawClient(source_instance.id, gateway_kind=source_instance.bridge_gateway_kind)
    target_client = AIClawClient(target_instance.id, gateway_kind=target_instance.bridge_gateway_kind)
    export_payload = {
        "profile": normalized["profile"],
        "source": "internal",
        "source_path": source_path,
        "internal_model_ref": source_path,
        "validate_mode": "metadata",
        "ttl_seconds": normalized["ttl_seconds"],
        "hash_files": normalized["hash_files"],
    }
    exported = await source_client.start_training_model_export(
        export_payload,
        timeout=normalized["timeout_seconds"],
    )
    token = str(exported.get("token") or "").strip()
    if not token:
        raise AppError("TRAINING_MODEL_TRANSFER_EXPORT_FAILED", 502, {"detail": "source export did not return token"})
    source_public_host = _training_model_transfer_source_host(
        source_instance.id,
        normalized.get("source_public_host"),
    )
    import_export_url = _training_model_transfer_rewrite_url_host(exported.get("export_url"), source_public_host)
    import_manifest_url = _training_model_transfer_rewrite_url_host(exported.get("manifest_url"), source_public_host)

    transfer_mode = "direct_http"
    try:
        imported = await target_client.import_training_model_from_export(
            {
                "profile": normalized["profile"],
                "source": "internal",
                "source_gateway_id": source_instance.id,
                "source_path": source_path,
                "export_url": import_export_url,
                "manifest_url": import_manifest_url,
                "manifest_sha256": exported.get("manifest_sha256"),
                "token": token,
                "force": normalized["force"],
                "timeout_seconds": normalized["timeout_seconds"],
            },
            timeout=normalized["timeout_seconds"],
        )
    except Exception as exc:
        if not _training_model_transfer_should_relay(exc):
            raise
        transfer_mode = "platform_relay"
        imported = await _relay_training_model_via_platform(
            source_client=source_client,
            target_client=target_client,
            normalized=normalized,
            source_gateway_id=source_instance.id,
            source_path=source_path,
            progress_cb=progress_cb,
        )
    internal_model_ref = str(imported.get("internal_model_ref") or imported.get("model_dir") or "").strip()
    if not internal_model_ref:
        raise AppError("TRAINING_MODEL_TRANSFER_IMPORT_FAILED", 502, {"detail": "target import did not return internal_model_ref"})

    runtime_result: dict[str, Any] | None = None
    if normalized["profile"] == TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE:
        runtime_result = await target_client.configure_training_runtime(
            {"env": {"SKILLFORGE_QWEN36_35B_MODEL_DIR": internal_model_ref}},
            timeout=30,
        )

    prepared_result: dict[str, Any] | None = None
    if normalized.get("prepare"):
        prepared_result = await target_client.prepare_training_model(
            {
                "profile": normalized["profile"],
                "revision": normalized["revision"],
                "modelscope_revision": normalized["modelscope_revision"],
                "bootstrap_profile": normalized["bootstrap_profile"],
                "force": normalized["force"],
                "dry_run": normalized["dry_run"],
                "timeout_seconds": normalized["timeout_seconds"],
                "validate_mode": normalized["validate_mode"] or "metadata",
                "source": "internal",
                "internal_model_ref": internal_model_ref,
                "auto_discover": False,
            },
            timeout=normalized["timeout_seconds"] + 60,
        )
        safe_prepared = _sanitize_training_result_value(prepared_result)
        if not isinstance(safe_prepared, dict):
            safe_prepared = {"status": "failed", "error": "invalid bridge response"}
        prepared_status = str(safe_prepared.get("status") or "").strip().lower()
        if prepared_status in {"failed", "unsupported"} or safe_prepared.get("exists") is False:
            raise AppError(
                "TRAINING_MODEL_TRANSFER_PREPARE_FAILED",
                502,
                {
                    "detail": safe_prepared.get("error") or "target model preparation failed",
                    "profile": normalized["profile"],
                    "target_gateway_id": target_instance.id,
                    "internal_model_ref": internal_model_ref,
                    "prepare": safe_prepared,
                },
            )

    safe_export = _training_transfer_safe_export_payload(exported)
    if import_export_url:
        safe_export["import_export_url"] = import_export_url
    if import_manifest_url:
        safe_export["import_manifest_url"] = import_manifest_url
    if source_public_host:
        safe_export["source_public_host"] = source_public_host
    result = {
        "status": "transferred",
        "profile": normalized["profile"],
        "training_gateway_id": target_instance.id,
        "source_gateway_id": source_instance.id,
        "source_path": source_path,
        "source_public_host": source_public_host or None,
        "transfer_mode": transfer_mode,
        "internal_model_ref": internal_model_ref,
        "export": safe_export,
        "import": _sanitize_training_result_value(imported),
        "runtime": _sanitize_training_result_value(runtime_result) if runtime_result else None,
        "prepare": {"model": _sanitize_training_result_value(prepared_result)} if prepared_result else None,
        "discover_all": _sanitize_training_result_value(discover_all) if discover_all else None,
    }
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training_model.transfer_to_training_gateway",
        target_type="training_gateway",
        target_id=target_instance.id,
        detail={
            "profile": normalized["profile"],
            "source_gateway_id": source_instance.id,
            "source_path": source_path,
            "transfer_mode": transfer_mode,
            "internal_model_ref": internal_model_ref,
            "export": safe_export,
            "import_status": imported.get("status") if isinstance(imported, dict) else None,
            "prepare_status": prepared_result.get("status") if isinstance(prepared_result, dict) else None,
        },
    ))
    await db.commit()
    await db.refresh(target_instance)
    return result


def _serialize_training_model_transfer(row: TrainingModelTransfer, *, include_result: bool = True) -> dict[str, Any]:
    result = {
        "id": row.id,
        "transfer_id": row.id,
        "status": row.status,
        "profile": row.profile,
        "source_gateway_id": row.source_gateway_id,
        "target_gateway_id": row.target_gateway_id,
        "training_gateway_id": row.target_gateway_id,
        "source_path": row.source_path,
        "source_public_host": row.source_public_host,
        "internal_model_ref": row.internal_model_ref,
        "transfer_mode": row.transfer_mode,
        "progress": float(row.progress or 0),
        "transferred_bytes": int(row.transferred_bytes or 0),
        "total_bytes": int(row.total_bytes or 0),
        "file_count": int(row.file_count or 0),
        "imported_files": int(row.imported_files or 0),
        "current_file": row.current_file,
        "manifest_sha256": row.manifest_sha256,
        "hash_files": bool(row.hash_files),
        "prepare": bool(row.prepare),
        "force": bool(row.force),
        "timeout_seconds": int(row.timeout_seconds or 21600),
        "error": row.error,
        "created_by": row.created_by,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
        "started_at": isoformat_bjt(row.started_at),
        "completed_at": isoformat_bjt(row.completed_at),
        "cancelled_at": isoformat_bjt(row.cancelled_at),
    }
    if include_result and row.result_json:
        result["result"] = _sanitize_training_result_value(row.result_json)
    return result


def _training_model_transfer_visible(row: TrainingModelTransfer, user: User) -> bool:
    if _can_view_all_training_resources(user):
        return True
    if str(getattr(user, "id", "") or "") == str(row.created_by or ""):
        return True
    return False


def _training_model_transfer_worker_user(row: TrainingModelTransfer | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(getattr(row, "created_by", "") or "training_model_transfer_worker"),
        role="system_admin",
        department=None,
        can_view_all=True,
        is_active=True,
        state="active",
    )


async def start_training_model_transfer(
    db: AsyncSession,
    user: User,
    payload: dict | None = None,
) -> dict[str, Any]:
    normalized = _normalize_training_model_transfer_request(payload or {})
    context = await _resolve_training_model_transfer_context(db, user, normalized)
    target_instance: OpenClawInstance = context["target_instance"]
    source_instance: OpenClawInstance = context["source_instance"]
    source_path = str(context["source_path"] or "").strip()
    source_public_host = _training_model_transfer_source_host(
        source_instance.id,
        normalized.get("source_public_host"),
    ) or None

    if not normalized["force"]:
        stmt = (
            select(TrainingModelTransfer)
            .where(TrainingModelTransfer.profile == normalized["profile"])
            .where(TrainingModelTransfer.source_gateway_id == source_instance.id)
            .where(TrainingModelTransfer.target_gateway_id == target_instance.id)
            .where(TrainingModelTransfer.source_path == source_path)
            .where(TrainingModelTransfer.status.in_(list(TRAINING_MODEL_TRANSFER_OPEN_STATUSES | {"succeeded"})))
            .order_by(TrainingModelTransfer.updated_at.desc(), TrainingModelTransfer.created_at.desc())
            .limit(1)
        )
        existing = (await db.execute(stmt)).scalars().first()
        if existing is not None:
            return _serialize_training_model_transfer(existing)

    transfer_id = f"tmx_{uuid4().hex[:24]}"
    request_json = _sanitize_training_result_value({
        **normalized,
        "training_gateway_id": target_instance.id,
        "source_gateway_id": source_instance.id,
        "source_path": source_path,
        "source_public_host": source_public_host,
        "discover_all": {
            "summary": _safe_dict(context.get("discover_all")).get("summary"),
            "training_gateway_id": _safe_dict(context.get("discover_all")).get("training_gateway_id"),
        } if context.get("discover_all") else None,
    })
    now = now_bjt()
    resume_manifest_sha = str(normalized.get("resume_manifest_sha256") or "").strip().lower() or None
    resume_transferred_bytes = max(0, _safe_int(normalized.get("resume_transferred_bytes"))) if resume_manifest_sha else 0
    resume_imported_files = max(0, _safe_int(normalized.get("resume_imported_files"))) if resume_manifest_sha else 0
    resume_current_file = _clean_text(normalized.get("resume_current_file"), limit=500) if resume_manifest_sha else None
    row = TrainingModelTransfer(
        id=transfer_id,
        status="queued",
        profile=normalized["profile"],
        source_gateway_id=source_instance.id,
        target_gateway_id=target_instance.id,
        source_path=source_path,
        source_public_host=source_public_host,
        progress=0,
        transferred_bytes=resume_transferred_bytes,
        total_bytes=0,
        file_count=0,
        imported_files=resume_imported_files,
        current_file=resume_current_file,
        manifest_sha256=resume_manifest_sha,
        hash_files=bool(normalized["hash_files"]),
        prepare=bool(normalized["prepare"]),
        force=bool(normalized["force"]),
        timeout_seconds=int(normalized["timeout_seconds"]),
        request_json=request_json,
        created_by=str(getattr(user, "id", "") or "unknown"),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training_model_transfer.create",
        target_type="training_model_transfer",
        target_id=row.id,
        detail={
            "profile": row.profile,
            "source_gateway_id": row.source_gateway_id,
            "target_gateway_id": row.target_gateway_id,
            "source_path": row.source_path,
        },
    ))
    await db.commit()
    await db.refresh(row)
    return _serialize_training_model_transfer(row)


async def get_training_model_transfer_status(
    db: AsyncSession,
    user: User,
    transfer_id: str,
) -> dict[str, Any]:
    row = await db.get(TrainingModelTransfer, transfer_id)
    if row is None:
        raise AppError("TRAINING_MODEL_TRANSFER_NOT_FOUND", 404, {"detail": "model transfer not found"})
    if not _training_model_transfer_visible(row, user):
        raise AppError("FORBIDDEN", 403, {"detail": "no access to model transfer"})
    return _serialize_training_model_transfer(row)


async def cancel_training_model_transfer(
    db: AsyncSession,
    user: User,
    transfer_id: str,
) -> dict[str, Any]:
    row = await db.get(TrainingModelTransfer, transfer_id)
    if row is None:
        raise AppError("TRAINING_MODEL_TRANSFER_NOT_FOUND", 404, {"detail": "model transfer not found"})
    if not _training_model_transfer_visible(row, user):
        raise AppError("FORBIDDEN", 403, {"detail": "no access to model transfer"})
    if row.status in TRAINING_MODEL_TRANSFER_FINAL_STATUSES:
        return _serialize_training_model_transfer(row)
    row.status = "cancelled"
    row.cancelled_at = now_bjt()
    row.completed_at = row.cancelled_at
    row.error = "cancelled by user"
    row.updated_at = row.cancelled_at
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training_model_transfer.cancel",
        target_type="training_model_transfer",
        target_id=row.id,
        detail={"status": row.status},
    ))
    await db.commit()
    await db.refresh(row)
    return _serialize_training_model_transfer(row)


def kickoff_training_model_transfer(transfer_id: str) -> bool:
    transfer_id = str(transfer_id or "").strip()
    if not transfer_id or transfer_id in _TRAINING_MODEL_TRANSFER_TASKS:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _TRAINING_MODEL_TRANSFER_TASKS.add(transfer_id)
    task = loop.create_task(run_training_model_transfer_by_id(transfer_id))

    def _done(done_task: asyncio.Task) -> None:
        _TRAINING_MODEL_TRANSFER_TASKS.discard(transfer_id)
        try:
            done_task.result()
        except asyncio.CancelledError:
            logger.info("[training-model-transfer] task cancelled transfer_id={}", transfer_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[training-model-transfer] task crashed transfer_id={} error={}",
                transfer_id,
                exc,
                exc_info=True,
            )

    task.add_done_callback(_done)
    return True


async def _update_training_model_transfer_progress(
    db: AsyncSession,
    transfer_id: str,
    event: dict[str, Any],
) -> None:
    row = await db.get(TrainingModelTransfer, transfer_id)
    if row is None:
        raise AppError("TRAINING_MODEL_TRANSFER_NOT_FOUND", 404, {"detail": "model transfer not found"})
    if row.status == "cancelled":
        raise AppError("TRAINING_MODEL_TRANSFER_CANCELLED", 499, {"detail": "model transfer cancelled"})
    row.status = "running"
    row.transfer_mode = str(event.get("transfer_mode") or row.transfer_mode or "").strip() or row.transfer_mode
    row.manifest_sha256 = str(event.get("manifest_sha256") or row.manifest_sha256 or "").strip() or row.manifest_sha256
    if "total_bytes" in event:
        row.total_bytes = max(0, _safe_int(event.get("total_bytes")))
    if "file_count" in event:
        row.file_count = max(0, _safe_int(event.get("file_count")))
    if "transferred_bytes" in event:
        row.transferred_bytes = max(0, _safe_int(event.get("transferred_bytes")))
    if "imported_files" in event:
        row.imported_files = max(0, _safe_int(event.get("imported_files")))
    current_file = str(event.get("current_file") or "").strip()
    if current_file:
        row.current_file = current_file[:500]
    if "progress" in event:
        try:
            progress = float(event.get("progress") or 0)
        except (TypeError, ValueError):
            progress = 0.0
        row.progress = max(0.0, min(progress, 0.99))
    elif row.total_bytes > 0:
        row.progress = max(0.0, min(float(row.transferred_bytes) / float(row.total_bytes), 0.99))
    row.updated_at = now_bjt()
    await db.commit()


async def run_training_model_transfer_by_id(transfer_id: str) -> dict[str, Any] | None:
    from app.database import async_session_factory

    async with async_session_factory() as db:
        row = await db.get(TrainingModelTransfer, transfer_id)
        if row is None:
            logger.warning("[training-model-transfer] missing transfer_id={}", transfer_id)
            return None
        return await _execute_training_model_transfer_row(db, row)


async def _execute_training_model_transfer_row(
    db: AsyncSession,
    row: TrainingModelTransfer,
) -> dict[str, Any]:
    if row.status in TRAINING_MODEL_TRANSFER_FINAL_STATUSES:
        return _serialize_training_model_transfer(row)
    now = now_bjt()
    row.status = "running"
    row.started_at = row.started_at or now
    row.updated_at = now
    row.error = None
    await db.commit()
    await db.refresh(row)

    actor = _training_model_transfer_worker_user(row)
    request_payload = _safe_dict(row.request_json)
    payload = {
        **request_payload,
        "profile": row.profile,
        "training_gateway_id": row.target_gateway_id,
        "source_gateway_id": row.source_gateway_id,
        "source_path": row.source_path,
        "source_public_host": row.source_public_host,
        "hash_files": bool(row.hash_files),
        "prepare": bool(row.prepare),
        "force": bool(row.force),
        "timeout_seconds": int(row.timeout_seconds or 21600),
        "resume_manifest_sha256": row.manifest_sha256 or request_payload.get("resume_manifest_sha256"),
        "resume_transferred_bytes": int(row.transferred_bytes or request_payload.get("resume_transferred_bytes") or 0),
        "resume_imported_files": int(row.imported_files or request_payload.get("resume_imported_files") or 0),
        "resume_current_file": row.current_file or request_payload.get("resume_current_file"),
    }

    last_progress_commit = 0.0

    async def progress_cb(event: dict[str, Any]) -> None:
        nonlocal last_progress_commit
        current = time()
        force_commit = bool(event.get("progress", 0) >= 0.99)
        if not force_commit and current - last_progress_commit < 1.0:
            return
        last_progress_commit = current
        await _update_training_model_transfer_progress(db, row.id, event)

    try:
        result = await transfer_training_model_to_training_gateway(
            db,
            actor,
            payload,
            progress_cb=progress_cb,
        )
    except AppError as exc:
        await db.rollback()
        refreshed = await db.get(TrainingModelTransfer, row.id)
        if refreshed is None:
            raise
        if refreshed.status == "cancelled" or exc.code == "TRAINING_MODEL_TRANSFER_CANCELLED":
            refreshed.status = "cancelled"
            refreshed.cancelled_at = refreshed.cancelled_at or now_bjt()
            refreshed.completed_at = refreshed.cancelled_at
        else:
            refreshed.status = "failed"
            refreshed.completed_at = now_bjt()
            refreshed.error = _extract_error_message(exc)[:2000]
        refreshed.updated_at = now_bjt()
        await db.commit()
        await db.refresh(refreshed)
        return _serialize_training_model_transfer(refreshed)
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        refreshed = await db.get(TrainingModelTransfer, row.id)
        if refreshed is None:
            raise
        refreshed.status = "failed"
        refreshed.completed_at = now_bjt()
        refreshed.updated_at = refreshed.completed_at
        refreshed.error = _extract_error_message(exc)[:2000]
        await db.commit()
        await db.refresh(refreshed)
        logger.warning("[training-model-transfer] failed transfer_id={} error={}", row.id, exc, exc_info=True)
        return _serialize_training_model_transfer(refreshed)

    refreshed = await db.get(TrainingModelTransfer, row.id)
    if refreshed is None:
        return None
    refreshed.status = "succeeded"
    refreshed.progress = 1.0
    refreshed.completed_at = now_bjt()
    refreshed.updated_at = refreshed.completed_at
    refreshed.current_file = None
    refreshed.transfer_mode = str(result.get("transfer_mode") or refreshed.transfer_mode or "").strip() or refreshed.transfer_mode
    refreshed.internal_model_ref = str(result.get("internal_model_ref") or refreshed.internal_model_ref or "").strip() or refreshed.internal_model_ref
    imported = _safe_dict(result.get("import"))
    if imported.get("transferred_bytes") is not None:
        refreshed.transferred_bytes = max(refreshed.transferred_bytes or 0, _safe_int(imported.get("transferred_bytes")))
    if imported.get("imported_files") is not None:
        refreshed.imported_files = max(refreshed.imported_files or 0, _safe_int(imported.get("imported_files")))
    if refreshed.total_bytes <= 0 and refreshed.transferred_bytes > 0:
        refreshed.total_bytes = refreshed.transferred_bytes
    refreshed.result_json = _sanitize_training_result_value(result)
    await db.commit()
    await db.refresh(refreshed)
    return _serialize_training_model_transfer(refreshed)


async def advance_training_model_transfers(
    db: AsyncSession,
    *,
    limit: int = 1,
) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit or 1), 4))
    stmt = (
        select(TrainingModelTransfer)
        .where(TrainingModelTransfer.status.in_(list(TRAINING_MODEL_TRANSFER_OPEN_STATUSES)))
        .order_by(TrainingModelTransfer.created_at.asc(), TrainingModelTransfer.id.asc())
        .limit(bounded_limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    started = 0
    for row in rows:
        if kickoff_training_model_transfer(row.id):
            started += 1
    return {
        "scanned": len(rows),
        "started": started,
        "active": len(_TRAINING_MODEL_TRANSFER_TASKS),
    }


async def configure_training_gateway_runtime(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    instance = await _get_training_gateway_for_bootstrap(db, user, gateway_id)
    normalized = _normalize_training_runtime_config_request(payload or {})
    if not bridge_registry.is_online(instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"实例 {instance.id} 的 bridge 未连接"})
    result = await AIClawClient(
        instance.id,
        gateway_kind=instance.bridge_gateway_kind,
    ).configure_training_runtime(normalized, timeout=30)
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training_gateway.configure_runtime",
        target_type="training_gateway",
        target_id=instance.id,
        detail={
            "department": instance.department,
            "updated_keys": sorted(normalized["env"].keys()),
            "result": _sanitize_training_result_value(result),
        },
    ))
    await db.commit()
    await db.refresh(instance)
    model_payload = {
        "profile": TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE,
        "source": "internal",
    }
    try:
        model_status = await AIClawClient(
            instance.id,
            gateway_kind=instance.bridge_gateway_kind,
        ).get_training_model_status(model_payload, timeout=30)
    except Exception as exc:  # noqa: BLE001
        model_status = {"status": "unavailable", "error": _extract_error_message(exc)}
    return {
        "gateway": _training_resource_from_instance(instance, online=True),
        "runtime": _sanitize_training_result_value(result),
        "model": _sanitize_training_result_value(model_status),
    }


async def prepare_training_gateway_model(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    instance = await _get_training_gateway_for_bootstrap(db, user, gateway_id)
    normalized = _normalize_training_model_request(payload or {})
    if not bridge_registry.is_online(instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"实例 {instance.id} 的 bridge 未连接"})
    bridge_payload = {
        "profile": normalized["profile"],
        "revision": normalized["revision"],
        "modelscope_revision": normalized["modelscope_revision"],
        "bootstrap_profile": normalized["bootstrap_profile"],
        "force": normalized["force"],
        "dry_run": normalized["dry_run"],
        "timeout_seconds": normalized["timeout_seconds"],
        "validate_mode": normalized["validate_mode"],
        "source": normalized["source"],
        "auto_discover": normalized["auto_discover"],
    }
    if normalized.get("internal_model_ref"):
        bridge_payload["internal_model_ref"] = normalized["internal_model_ref"]
    result = await AIClawClient(
        instance.id,
        gateway_kind=instance.bridge_gateway_kind,
    ).prepare_training_model(
        bridge_payload,
        timeout=normalized["timeout_seconds"] + 60,
    )
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training_gateway.prepare_model",
        target_type="training_gateway",
        target_id=instance.id,
        detail={
            "department": instance.department,
            "profile": normalized["profile"],
            "model_id": normalized["model_id"],
            "revision": normalized["revision"],
            "modelscope_revision": normalized["modelscope_revision"],
            "bootstrap_profile": normalized["bootstrap_profile"],
            "validate_mode": normalized["validate_mode"],
            "source": normalized["source"],
            "internal_model_ref": normalized["internal_model_ref"],
            "auto_discover": normalized["auto_discover"],
            "dry_run": normalized["dry_run"],
            "force": normalized["force"],
            "model_status": result.get("status") if isinstance(result, dict) else None,
        },
    ))
    await db.commit()
    await db.refresh(instance)
    return {
        "gateway": _training_resource_from_instance(instance, online=True),
        "model": _sanitize_training_result_value(result),
    }


async def get_training_gateway_openwebui_status(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    instance = await _get_training_gateway_for_bootstrap(db, user, gateway_id)
    if not bridge_registry.is_online(instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"实例 {instance.id} 的 bridge 未连接"})
    result = await AIClawClient(
        instance.id,
        gateway_kind=instance.bridge_gateway_kind,
    ).get_openwebui_status(payload or {}, timeout=30)
    return {
        "gateway": _training_resource_from_instance(instance, online=True),
        "openwebui": _sanitize_training_result_value(result),
    }


def _openai_proxy_messages_to_prompt(messages: Any) -> str:
    if not isinstance(messages, list):
        return str(messages or "").strip()
    parts: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").strip() or "user"
        content = item.get("content")
        if isinstance(content, list):
            chunks = []
            for chunk in content:
                if isinstance(chunk, dict):
                    if chunk.get("type") in {None, "text"}:
                        chunks.append(str(chunk.get("text") or ""))
                else:
                    chunks.append(str(chunk or ""))
            text = "\n".join(part for part in chunks if part)
        else:
            text = str(content or "")
        text = text.strip()
        if text:
            parts.append(f"{role}: {text}")
    return "\n".join(parts).strip()


async def proxy_training_gateway_openwebui_models(
    db: AsyncSession,
    user: User,
    gateway_id: str,
) -> dict[str, Any]:
    status = await get_training_gateway_openwebui_status(db, user, gateway_id, {})
    openwebui = status.get("openwebui") if isinstance(status.get("openwebui"), dict) else {}
    models = openwebui.get("models") if isinstance(openwebui.get("models"), list) else []
    return {
        "object": "list",
        "data": [
            {
                "id": str(item.get("id") or ""),
                "object": "model",
                "created": int(time()),
                "owned_by": "skillforge",
            }
            for item in models
            if isinstance(item, dict) and item.get("id")
        ],
    }


async def proxy_training_gateway_openwebui_chat_completion(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    model_id = str(raw.get("model") or raw.get("model_id") or "").strip()
    if not model_id:
        raise AppError("OPENWEBUI_PROXY_INVALID_REQUEST", 422, {"detail": "model is required"})
    prompt = _openai_proxy_messages_to_prompt(raw.get("messages"))
    if not prompt:
        prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise AppError("OPENWEBUI_PROXY_INVALID_REQUEST", 422, {"detail": "messages are required"})
    max_tokens = _safe_int(raw.get("max_tokens") or raw.get("max_completion_tokens") or 512)
    max_tokens = max(1, min(max_tokens, 4096))
    timeout_seconds = max(60, min(_safe_int(raw.get("timeout_seconds") or 600), 3600))
    result = await test_training_gateway_openwebui_chat(
        db,
        user,
        gateway_id,
        {
            "profile": raw.get("profile") or TRAINING_QWEN36_35B_AGGRESSIVE_PROFILE,
            "model_id": model_id,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
        },
    )
    bridge_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    if bridge_result.get("ok") is not True and bridge_result.get("status") != "succeeded":
        raise AppError(
            "OPENWEBUI_PROXY_CHAT_FAILED",
            502,
            {
                "detail": bridge_result.get("error") or "OpenWebUI relay chat failed",
                "result": bridge_result,
            },
        )
    text = str(bridge_result.get("text") or "")
    response_id = str(bridge_result.get("response_id") or ("chatcmpl-sf-proxy-" + hashlib.sha256(f"{time()}:{model_id}".encode()).hexdigest()[:16]))
    created = int(time())
    finish_reason = str(bridge_result.get("finish_reason") or "stop")
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": max(1, len(text) // 4) if text else 0,
            "total_tokens": max(1, len(text) // 4) if text else 0,
        },
    }


async def register_training_gateway_openwebui_base_model(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    instance = await _get_training_gateway_for_bootstrap(db, user, gateway_id)
    normalized = _normalize_training_openwebui_base_model_request(payload or {})
    if not bridge_registry.is_online(instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"实例 {instance.id} 的 bridge 未连接"})

    client = AIClawClient(instance.id, gateway_kind=instance.bridge_gateway_kind)
    model_payload = {
        "profile": normalized["profile"],
        "bootstrap_profile": normalized["bootstrap_profile"],
        "source": normalized["source"],
        "internal_model_ref": normalized["internal_model_ref"],
    }
    model_status = await client.get_training_model_status(model_payload, timeout=30)
    safe_model_status = _sanitize_training_result_value(model_status)
    if not isinstance(safe_model_status, dict):
        safe_model_status = {"status": "unknown"}
    model_state = str(safe_model_status.get("status") or "").strip().lower()
    if safe_model_status.get("exists") is not True or model_state in {"failed", "unsupported"}:
        raise AppError(
            "TRAINING_MODEL_NOT_READY",
            409,
            {
                "detail": "training model must exist on the target gateway before OpenWebUI registration",
                "profile": normalized["profile"],
                "gateway_id": instance.id,
                "model_status": safe_model_status,
            },
        )

    register_payload = {
        "model_id": normalized["openwebui_model_id"],
        "aliases": normalized["openwebui_aliases"],
        "profile": normalized["profile"],
        "runtime_profile": normalized["runtime_profile"],
        "base_model_only": True,
        "display_name": normalized["display_name"],
        "source_type": "skillforge_base_model",
    }
    registration = await client.register_openwebui_model(register_payload, timeout=30)
    safe_registration = _sanitize_training_result_value(registration)
    if not isinstance(safe_registration, dict):
        safe_registration = {"registered": False, "error": "invalid bridge response"}
    if safe_registration.get("registered") is not True:
        raise AppError(
            "OPENWEBUI_MODEL_REGISTER_FAILED",
            502,
            {
                "detail": safe_registration.get("error") or safe_registration.get("reason") or "OpenWebUI registration failed",
                "registration": safe_registration,
            },
        )
    openwebui_status = await client.get_openwebui_status({}, timeout=30)
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training_gateway.openwebui_register_base_model",
        target_type="training_gateway",
        target_id=instance.id,
        detail={
            "department": instance.department,
            "profile": normalized["profile"],
            "model_id": normalized["openwebui_model_id"],
            "aliases": normalized["openwebui_aliases"],
            "runtime_profile": normalized["runtime_profile"],
            "source": normalized["source"],
            "model_status": model_state or safe_model_status.get("status"),
        },
    ))
    await db.commit()
    await db.refresh(instance)
    return {
        "gateway": _training_resource_from_instance(instance, online=True),
        "model": safe_model_status,
        "registration": safe_registration,
        "openwebui": _sanitize_training_result_value(openwebui_status),
    }


async def test_training_gateway_openwebui_chat(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    instance = await _get_training_gateway_for_bootstrap(db, user, gateway_id)
    normalized = _normalize_training_openwebui_chat_test_request(payload or {})
    if not bridge_registry.is_online(instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"实例 {instance.id} 的 bridge 未连接"})
    result = await AIClawClient(
        instance.id,
        gateway_kind=instance.bridge_gateway_kind,
    ).test_openwebui_chat(
        {
            "model_id": normalized["model_id"],
            "prompt": normalized["prompt"],
            "max_tokens": normalized["max_tokens"],
            "timeout_seconds": normalized["timeout_seconds"],
        },
        timeout=normalized["timeout_seconds"] + 60,
    )
    safe_result = _sanitize_training_result_value(result)
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training_gateway.openwebui_chat_test",
        target_type="training_gateway",
        target_id=instance.id,
        detail={
            "department": instance.department,
            "profile": normalized["profile"],
            "model_id": normalized["model_id"],
            "max_tokens": normalized["max_tokens"],
            "status": safe_result.get("status") if isinstance(safe_result, dict) else None,
        },
    ))
    await db.commit()
    await db.refresh(instance)
    return {
        "gateway": _training_resource_from_instance(instance, online=True),
        "model": {
            "profile": normalized["profile"],
            "model_id": normalized["model_id"],
        },
        "result": safe_result,
    }


async def test_training_gateway_model(
    db: AsyncSession,
    user: User,
    gateway_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    instance = await _get_training_gateway_for_bootstrap(db, user, gateway_id)
    normalized = _normalize_training_model_test_request(payload or {})
    if not bridge_registry.is_online(instance.id):
        raise AppError("BRIDGE_OFFLINE", 503, {"detail": f"实例 {instance.id} 的 bridge 未连接"})
    bridge_payload = {
        "profile": normalized["profile"],
        "bootstrap_profile": normalized["bootstrap_profile"],
        "prompt": normalized["prompt"],
        "max_new_tokens": normalized["max_new_tokens"],
        "timeout_seconds": normalized["timeout_seconds"],
        "base_model_only": normalized["base_model_only"],
    }
    result = await AIClawClient(
        instance.id,
        gateway_kind=instance.bridge_gateway_kind,
    ).run_training_inference(
        bridge_payload,
        timeout=normalized["timeout_seconds"] + 60,
    )
    db.add(AuditLog(
        user_id=str(getattr(user, "id", "") or ""),
        action="training_gateway.test_model",
        target_type="training_gateway",
        target_id=instance.id,
        detail={
            "department": instance.department,
            "profile": normalized["profile"],
            "model_id": normalized["model_id"],
            "base_model_only": normalized["base_model_only"],
            "max_new_tokens": normalized["max_new_tokens"],
            "status": result.get("status") if isinstance(result, dict) else None,
        },
    ))
    await db.commit()
    await db.refresh(instance)
    return {
        "gateway": _training_resource_from_instance(instance, online=True),
        "model": {
            "profile": normalized["profile"],
            "model_id": normalized["model_id"],
            "base_model_only": normalized["base_model_only"],
        },
        "result": _sanitize_training_result_value(result),
    }


async def list_training_jobs(
    db: AsyncSession,
    user: User,
    *,
    status: str | None = None,
    target_gateway_id: str | None = None,
) -> dict[str, Any]:
    resolved_status = _normalize_job_status(status)
    gateway_id = _clean_text(target_gateway_id, limit=50)
    stmt = select(TrainingJob).order_by(TrainingJob.created_at.desc())
    if resolved_status:
        stmt = stmt.where(TrainingJob.status == resolved_status)
    if gateway_id:
        stmt = stmt.where(TrainingJob.target_gateway_id == gateway_id)
    if not _can_view_all_training_resources(user):
        department = _user_department(user)
        stmt = stmt.where(TrainingJob.department == department if department else false())
    jobs = (await db.execute(stmt.limit(200))).scalars().all()
    deployment_map: dict[str, list[TrainingModelDeployment]] = {job.id: [] for job in jobs}
    if jobs:
        deployments = (
            await db.execute(
                select(TrainingModelDeployment)
                .where(TrainingModelDeployment.job_id.in_([job.id for job in jobs]))
                .order_by(TrainingModelDeployment.created_at.desc(), TrainingModelDeployment.id.desc())
            )
        ).scalars().all()
        for deployment in deployments:
            deployment_map.setdefault(deployment.job_id, []).append(deployment)
    task_map: dict[str, list[TrainingJobTask]] = {job.id: [] for job in jobs}
    if jobs:
        tasks = (
            await db.execute(
                select(TrainingJobTask)
                .where(TrainingJobTask.job_id.in_([job.id for job in jobs]))
                .order_by(TrainingJobTask.id)
            )
        ).scalars().all()
        for task in tasks:
            task_map.setdefault(task.job_id, []).append(task)
    items = [
        _serialize_job(job, tasks=task_map.get(job.id, []), deployments=deployment_map.get(job.id, []))
        for job in jobs
    ]
    return {
        "items": items,
        "total": len(items),
        "stats": {
            "awaiting_review": sum(1 for item in items if item["status"] == "awaiting_review"),
            "queued": sum(1 for item in items if item["status"] == "queued"),
            "running": sum(1 for item in items if item["status"] == "running"),
            "completed": sum(1 for item in items if item["status"] == "completed"),
            "failed": sum(1 for item in items if item["status"] == "failed"),
            "cancelled": sum(1 for item in items if item["status"] == "cancelled"),
        },
    }


async def get_training_job(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job_id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    deployments = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.job_id == job_id)
            .order_by(TrainingModelDeployment.created_at.desc(), TrainingModelDeployment.id.desc())
        )
    ).scalars().all()
    return _serialize_job(job, list(tasks), list(deployments))


async def get_training_job_logs(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    if not job.target_gateway_id:
        return {
            "job_id": job.id,
            "available": False,
            "status": job.status,
            "lines": [],
            "error": "training job has no target gateway",
        }
    try:
        instance = await _get_training_gateway_instance(db, user, job)
        result = await AIClawClient(
            instance.id,
            gateway_kind=instance.bridge_gateway_kind,
        ).stream_training_logs(
            {"job_id": job.id},
        )
    except AppError as exc:
        if exc.status in {401, 403}:
            raise
        return {
            "job_id": job.id,
            "available": False,
            "status": job.status,
            "lines": [],
            "error": _extract_error_message(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "job_id": job.id,
            "available": False,
            "status": job.status,
            "lines": [],
            "error": _extract_error_message(exc),
        }
    payload = result if isinstance(result, dict) else {}
    raw_lines = payload.get("lines") if isinstance(payload.get("lines"), list) else []
    return {
        "job_id": job.id,
        "available": True,
        "status": str(payload.get("status") or job.status or "unknown")[:30],
        "lines": [_sanitize_log_line(item) for item in raw_lines[-100:]],
        "error": None,
    }


async def create_training_job(
    db: AsyncSession,
    user: User,
    payload: dict[str, Any],
    *,
    commit: bool = True,
) -> dict[str, Any]:
    if not _can_create_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to create training job"})
    department = _clean_text(payload.get("department") or _user_department(user), limit=50, required=True) or ""
    _require_training_department_access(user, department)
    title = _clean_text(payload.get("title"), limit=200, required=True) or ""
    job_type = _normalize_job_type(payload.get("job_type"))
    target_gateway_id = _clean_text(payload.get("target_gateway_id"), limit=50)
    target_skill_id = _clean_text(payload.get("target_skill_id"), limit=50)
    spec = _validate_spec_json(payload.get("spec") or payload.get("spec_json"))
    dataset_ref = _clean_text(payload.get("dataset_ref"), limit=200)
    dataset_spec = spec.get("dataset") if isinstance(spec.get("dataset"), dict) else {}
    dataset_version_id = _clean_text(
        payload.get("dataset_version_id") or spec.get("dataset_version_id") or dataset_spec.get("dataset_version_id"),
        limit=50,
    )
    if dataset_version_id:
        dataset_version = await db.get(TrainingDatasetVersion, dataset_version_id)
        if dataset_version is None:
            raise AppError("TRAINING_DATASET_VERSION_NOT_FOUND", 404)
        _require_training_asset_read_access(user, dataset_version.department)
        if dataset_version.status not in {"ready", "approved"}:
            raise AppError(
                "TRAINING_DATASET_VERSION_NOT_READY",
                422,
                {"dataset_version_id": dataset_version.id, "status": dataset_version.status},
            )
        if TRAINING_AUTO_ENABLED and TRAINING_AUTO_DATASET_SYNC_ENABLED and dataset_version.sync_status in {"", None, "not_synced", "failed"}:
            await sync_training_dataset_version(
                db,
                user,
                dataset_version.id,
                {
                    "target_gateway_id": dataset_version.target_gateway_id or TRAINING_PRIMARY_GATEWAY_IDS[0],
                    "metadata": {"trigger": "training_job.create"},
                },
                commit=False,
            )
        if dataset_version.modality in {"image", "image_text", "video_frame", "mixed"} and job_type in {"lora", "qlora", "text_sft"}:
            job_type = "multimodal_sft"
        dataset_ref = dataset_ref or _training_manifest_uri(dataset_version.id)
        spec = {
            **spec,
            "dataset_version_id": dataset_version.id,
            "dataset": {
                **dataset_spec,
                "dataset_version_id": dataset_version.id,
                "dataset_profile": dataset_version.dataset_profile,
                "modality": dataset_version.modality,
                "manifest_uri": _training_manifest_uri(dataset_version.id),
                "manifest_sha256": dataset_version.manifest_sha256,
                "sample_count": dataset_version.sample_count,
                "media_count": dataset_version.media_count,
                "media_policy": "reference_only",
                "sync_status": dataset_version.sync_status,
                "synced_sink_job_id": dataset_version.synced_sink_job_id,
                "target_gateway_id": dataset_version.target_gateway_id,
            },
        }
        if not target_gateway_id and dataset_version.target_gateway_id:
            target_gateway_id = dataset_version.target_gateway_id
        if not payload.get("training_strategy") and dataset_version.modality in {"image", "image_text", "video_frame", "mixed"}:
            payload = {**payload, "training_strategy": "qlora_vision_instruction"}
    spec_automation = spec.get("automation") if isinstance(spec.get("automation"), dict) else {}
    full_auto_requested = (
        bool(dataset_version_id)
        or target_gateway_id in TRAINING_PRIMARY_GATEWAY_IDS
        or payload.get("full_auto") is True
        or spec_automation.get("full_auto") is True
    )
    spec = _apply_training_auto_defaults(
        spec,
        title=title,
        target_skill_id=target_skill_id,
        full_auto=full_auto_requested,
        force_model_date=full_auto_requested,
    )
    if target_gateway_id:
        selected_gateway, routing = await _select_training_gateway_for_job(
            db,
            user,
            department=department,
            job_type=job_type,
            preferred_gateway_id=target_gateway_id,
        )
        if selected_gateway is not None and routing is not None:
            target_gateway_id = selected_gateway.id
            spec = _with_gateway_routing(spec, {**routing, "reason": "create"})
    else:
        selected_gateway, routing = await _select_training_gateway_for_job(
            db,
            user,
            department=department,
            job_type=job_type,
        )
        if selected_gateway is not None and routing is not None:
            target_gateway_id = selected_gateway.id
            spec = _with_gateway_routing(spec, {**routing, "reason": "create"})
        else:
            spec = _with_gateway_routing(
                spec,
                _gateway_routing_payload(None, mode="auto", status="missing_gateway", reason="create"),
            )
    job = TrainingJob(
        id=_training_job_id(),
        title=title,
        department=department,
        created_by=str(getattr(user, "id", "") or ""),
        status="awaiting_review",
        job_type=job_type,
        training_strategy=_clean_text(payload.get("training_strategy"), limit=80),
        target_skill_id=target_skill_id,
        target_gateway_id=target_gateway_id,
        dataset_ref=dataset_ref,
        objective=_clean_text(payload.get("objective"), limit=4000),
        risk_level=_clean_text(payload.get("risk_level"), limit=20),
        spec_json=spec,
        approval_required=True,
    )
    db.add(job)
    await db.flush()
    spec = job.spec_json if isinstance(job.spec_json, dict) else {}
    await _record_training_audit(
        db,
        user,
        "training_job.create",
        job,
        {
            "source": spec.get("source") or "manual",
            "target_skill_id": job.target_skill_id,
            "target_gateway_id": job.target_gateway_id,
            "dataset_ref": job.dataset_ref,
        },
    )
    try:
        from app.learning.service import capture_training_job

        await capture_training_job(db, job)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获训练任务创建失败 job={} err={}", job.id, exc)
    if commit:
        await db.commit()
        await db.refresh(job)
    else:
        await db.flush()
    return _serialize_job(job)


async def create_training_candidate_from_skill(
    db: AsyncSession,
    user: User,
    skill_id: str,
    payload: dict[str, Any],
    *,
    access_action: str = "edit",
    source: str = "skillstudio_data_asset",
    lineage_extra: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    if not _can_create_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to create training job"})
    skill = await require_skill_access(db, skill_id, user, access_action)
    readiness = await get_training_candidate_readiness(db, user, skill_id)
    if not readiness["passed"]:
        raise AppError("TRAINING_DATA_NOT_READY", 400, readiness)

    job_type = str(payload.get("job_type") or "lora").strip().lower()
    model_family = _clean_text(payload.get("model_family") or f"{skill.id}:action_ranker", limit=100)
    eval_gate = payload.get("eval_gate") if isinstance(payload.get("eval_gate"), dict) else {}
    if not eval_gate:
        eval_gate = {
            "require_artifact_sha256": True,
            "min_win_rate": 0.55,
        }
    candidate_spec = {
        "source": source,
        "source_skill_id": skill.id,
        "model_family": model_family,
        "dataset": {
            "ref": payload.get("dataset_ref") or readiness["dataset_ref"],
            "sample_counts": readiness["sample_counts"],
            "readiness_checks": readiness["checks"],
        },
        "eval_gate": eval_gate,
        "lineage": {
            "source": "decision_log",
            "skill_id": skill.id,
            "max_logs": TRAINING_CANDIDATE_MAX_LOGS,
        },
    }
    if lineage_extra:
        safe_lineage_extra = _sanitize_training_result_value(lineage_extra)
        if isinstance(safe_lineage_extra, dict):
            candidate_spec["lineage"].update(safe_lineage_extra)
    for source_key, target_key in (
        ("base_model", "base_model"),
        ("target_metric", "target_metric"),
        ("estimated_gpu_hours", "estimated_gpu_hours"),
        ("gpu_estimate", "gpu_estimate"),
        ("forbidden_actions", "forbidden_actions"),
        ("risk_notes", "risk_notes"),
    ):
        if source_key in payload:
            candidate_spec[target_key] = _sanitize_training_result_value(payload.get(source_key))
    return await create_training_job(
        db,
        user,
        {
            "title": payload.get("title") or f"训练 {skill.name} 动作推荐模型",
            "department": skill.department,
            "job_type": job_type,
            "training_strategy": payload.get("training_strategy") or "lora_task_parallel",
            "target_skill_id": skill.id,
            "target_gateway_id": payload.get("target_gateway_id"),
            "dataset_ref": payload.get("dataset_ref") or readiness["dataset_ref"],
            "objective": payload.get("objective") or f"基于 {skill.name} 的历史执行、反馈和动作结果样本生成训练候选",
            "risk_level": payload.get("risk_level") or skill.risk_level or "R2",
            "spec": candidate_spec,
        },
        commit=commit,
    )


def _run_training_sample_counts(
    *,
    run: ExecutionRun,
    step_count: int,
    decision_logs: list[DecisionLog],
    decision_total: int,
) -> dict[str, Any]:
    recent_failed = 1 if str(run.status or "").lower() in {"failed", "timeout"} else 0
    return {
        "execution_runs": 1,
        "execution_steps": step_count,
        "decision_logs": decision_total,
        "decision_logs_sampled": len(decision_logs),
        "sft_samples": sum(1 for log in decision_logs if isinstance(log.output_result, dict) or isinstance(log.input_snapshot, dict)),
        "preference_samples": sum(1 for log in decision_logs if _decision_has_feedback(log)),
        "action_outcome_samples": sum(1 for log in decision_logs if _decision_has_action_outcome(log)),
        "eval_samples": sum(1 for log in decision_logs if _decision_has_feedback(log)),
        "recent_total": 1,
        "recent_failed": recent_failed,
    }


async def create_training_candidate_from_run(
    db: AsyncSession,
    user: User,
    run_id: str,
    payload: dict[str, Any],
    *,
    commit: bool = True,
) -> dict[str, Any]:
    if not _can_create_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to create training job"})
    run = await db.get(ExecutionRun, run_id)
    if not run:
        raise AppError("NOT_FOUND", 404, {"detail": "execution run not found"})
    if not run.skill_id:
        raise AppError("TRAINING_RUN_SKILL_REQUIRED", 400, {"detail": "execution run has no skill_id"})
    if str(run.run_mode or "") in SAMPLE_RUN_MODES and not bool(payload.get("allow_sandbox")):
        raise AppError("TRAINING_RUN_SANDBOX_NOT_ALLOWED", 400, {"detail": "sandbox/sample runs cannot seed training candidates"})

    skill = await require_skill_access(db, run.skill_id, user, "edit")
    step_count = int((await db.execute(
        select(func.count(ExecutionStep.id)).where(ExecutionStep.run_id == run.id)
    )).scalar() or 0)
    step_status_rows = (await db.execute(
        select(ExecutionStep.status, func.count(ExecutionStep.id))
        .where(ExecutionStep.run_id == run.id)
        .group_by(ExecutionStep.status)
    )).all()
    step_status_counts = {
        str(status or "unknown"): int(count or 0)
        for status, count in step_status_rows
    }
    decision_total = int((await db.execute(
        select(func.count(DecisionLog.id)).where(DecisionLog.run_id == run.id)
    )).scalar() or 0)
    decision_logs = (await db.execute(
        select(DecisionLog)
        .where(DecisionLog.run_id == run.id)
        .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
        .limit(TRAINING_CANDIDATE_MAX_LOGS)
    )).scalars().all()
    if step_count <= 0 and decision_total <= 0 and not str(run.summary or "").strip():
        raise AppError("TRAINING_RUN_DATA_EMPTY", 400, {"detail": "execution run has no usable training context"})

    dataset_ref = _clean_text(
        payload.get("dataset_ref") or f"execution-run://{run.id}/redacted-analysis-context",
        limit=200,
    )
    model_family = _clean_text(
        payload.get("model_family") or f"{skill.id}:run_trace_improvement",
        limit=100,
    )
    eval_gate = payload.get("eval_gate") if isinstance(payload.get("eval_gate"), dict) else {}
    if not eval_gate:
        eval_gate = {
            "require_artifact_sha256": True,
            "manual_review_required": True,
            "min_win_rate": 0.55,
        }
    analysis_summary = _clean_text(payload.get("analysis_summary") or payload.get("analysis"), limit=4000)
    analysis_meta = {
        "model": _clean_text(payload.get("analysis_model") or payload.get("model"), limit=80),
        "prompt_hash": _clean_text(payload.get("analysis_prompt_hash") or payload.get("prompt_hash"), limit=120),
        "raw_counts": _sanitize_training_result_value(payload.get("raw_counts") if isinstance(payload.get("raw_counts"), dict) else {}),
    }
    if analysis_summary:
        analysis_meta["summary"] = redact_secret_text(analysis_summary, limit=4000)

    sample_counts = _run_training_sample_counts(
        run=run,
        step_count=step_count,
        decision_logs=list(decision_logs),
        decision_total=decision_total,
    )
    candidate_spec = {
        "source": "run_trace_analysis",
        "source_skill_id": skill.id,
        "model_family": model_family,
        "dataset": {
            "ref": dataset_ref,
            "sample_counts": sample_counts,
            "step_status_counts": step_status_counts,
            "readiness_checks": [
                {
                    "key": "has_run_context",
                    "label": "运行上下文",
                    "actual": step_count + decision_total,
                    "threshold": 1,
                    "passed": step_count + decision_total >= 1,
                },
                {
                    "key": "requires_human_review",
                    "label": "人工审批",
                    "actual": "awaiting_review",
                    "threshold": "awaiting_review",
                    "passed": True,
                },
            ],
        },
        "eval_gate": eval_gate,
        "lineage": {
            "source": "execution_run",
            "skill_id": skill.id,
            "run_id": run.id,
            "run_status": run.status,
            "run_mode": run.run_mode,
            "trigger_type": run.trigger_type,
            "source_instance_id": run.source_instance_id,
            "started_at": isoformat_bjt(run.started_at),
            "completed_at": isoformat_bjt(run.completed_at),
            "decision_logs_truncated": decision_total > len(decision_logs),
        },
        "analysis": _sanitize_training_result_value(analysis_meta),
    }
    for source_key, target_key in (
        ("base_model", "base_model"),
        ("target_metric", "target_metric"),
        ("estimated_gpu_hours", "estimated_gpu_hours"),
        ("gpu_estimate", "gpu_estimate"),
        ("forbidden_actions", "forbidden_actions"),
        ("risk_notes", "risk_notes"),
    ):
        if source_key in payload:
            candidate_spec[target_key] = _sanitize_training_result_value(payload.get(source_key))

    return await create_training_job(
        db,
        user,
        {
            "title": payload.get("title") or f"训练 {skill.name} 运行复盘候选",
            "department": skill.department,
            "job_type": payload.get("job_type") or "lora",
            "training_strategy": payload.get("training_strategy") or "lora_task_parallel",
            "target_skill_id": skill.id,
            "target_gateway_id": payload.get("target_gateway_id"),
            "dataset_ref": dataset_ref,
            "objective": payload.get("objective") or f"基于运行 {run.id} 的 AI 复盘和脱敏过程数据生成训练候选",
            "risk_level": payload.get("risk_level") or skill.risk_level or "R2",
            "spec": candidate_spec,
        },
        commit=commit,
    )


async def approve_training_job(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to approve training job"})
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    if job.status != "awaiting_review":
        raise AppError("INVALID_STATUS", 400, {"detail": "only awaiting_review training jobs can be approved"})
    routing = await _assign_training_gateway_if_missing(db, user, job, reason="approve")
    now = now_bjt()
    job.status = "queued"
    job.approved_by = str(getattr(user, "id", "") or "")
    job.approved_at = now
    job.updated_at = now
    job.gateway_payload_json = await _build_gateway_payload_for_job(db, job)
    audit_detail = {"routing": routing} if routing else None
    await _record_training_audit(db, user, "training_job.approve", job, audit_detail)
    await db.commit()
    await db.refresh(job)
    return _serialize_job(job)


async def _dispatch_training_job(
    db: AsyncSession,
    user: User,
    job_id: str,
    *,
    audit_action: str,
    require_dispatch_failure: bool = False,
) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to dispatch training job"})
    job = await db.get(TrainingJob, job_id, with_for_update=True)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    if job.status != "queued":
        raise AppError("INVALID_STATUS", 400, {"detail": "only queued training jobs can be dispatched"})
    if require_dispatch_failure and job.failure_stage != "dispatch":
        raise AppError(
            "INVALID_STATUS",
            400,
            {"detail": "only queued training jobs with dispatch failure can be retried"},
        )
    routing = await _assign_training_gateway_if_missing(db, user, job, reason="dispatch")
    instance = await _get_training_gateway(db, user, job)
    if not job.gateway_payload_json:
        job.gateway_payload_json = await _build_gateway_payload_for_job(db, job)
    control_payload = job.gateway_payload_json.get("control") if isinstance(job.gateway_payload_json, dict) else {}
    control_payload = control_payload if isinstance(control_payload, dict) else {}
    agent_contract = control_payload.get("agent_contract")
    if not isinstance(agent_contract, dict):
        agent_contract = _training_agent_contract_summary(instance)

    now = now_bjt()
    active_task = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .where(TrainingJobTask.status.in_(sorted(ACTIVE_TRAINING_TASK_STATUSES)))
            .order_by(TrainingJobTask.id.desc())
            .with_for_update()
        )
    ).scalars().first()
    if active_task is not None:
        if job.status != "running":
            job.status = "running"
            job.failure_stage = None
            job.updated_at = now
            await db.commit()
            await db.refresh(job)
        tasks = (
            await db.execute(
                select(TrainingJobTask)
                .where(TrainingJobTask.job_id == job.id)
                .order_by(TrainingJobTask.id)
            )
        ).scalars().all()
        return _serialize_job(job, list(tasks))

    task = TrainingJobTask(
        job_id=job.id,
        gateway_id=instance.id,
        status="dispatching",
        progress=0,
        metrics_json={"op": "training.submit_job", "agent_contract": agent_contract},
    )
    db.add(task)
    await db.flush()

    try:
        result = await AIClawClient(instance.id, gateway_kind=instance.bridge_gateway_kind).submit_training_job(
            dict(job.gateway_payload_json or {}),
        )
    except Exception as exc:  # noqa: BLE001
        message = _extract_error_message(exc)
        task.status = "failed"
        task.error_message = message
        task.metrics_json = {
            "op": "training.submit_job",
            "accepted": False,
            "error": message,
            "agent_contract": agent_contract,
        }
        task.updated_at = now
        job.failure_stage = "dispatch"
        job.updated_at = now
        audit_detail = {"ok": False, "gateway_id": instance.id, "error": message, "agent_contract": agent_contract}
        if routing:
            audit_detail["routing"] = routing
        if require_dispatch_failure:
            audit_detail["retry_stage"] = "dispatch"
        await _record_training_audit(
            db,
            user,
            audit_action,
            job,
            audit_detail,
        )
        await db.commit()
        await db.refresh(job)
        tasks = (
            await db.execute(
                select(TrainingJobTask)
                .where(TrainingJobTask.job_id == job.id)
                .order_by(TrainingJobTask.id)
            )
        ).scalars().all()
        return _serialize_job(job, list(tasks))

    result_payload = result if isinstance(result, dict) else {}
    safe_result_payload = _sanitize_training_result_value(result_payload)
    accepted = bool(result_payload.get("accepted", True))
    if accepted:
        task.status = "running"
        task.worker_id = _clean_text(result_payload.get("worker_id") or result_payload.get("gateway_job_id"), limit=100)
        task.progress = max(0, min(float(result_payload.get("progress") or 0), 1))
        task.metrics_json = {
            "op": "training.submit_job",
            "accepted": True,
            "result": safe_result_payload,
            "agent_contract": agent_contract,
        }
        job.status = "running"
        job.failure_stage = None
    else:
        message = _clean_text(
            redact_secret_text(result_payload.get("message") or "training gateway rejected job", limit=2000),
            limit=2000,
        ) or "training gateway rejected job"
        task.status = "failed"
        task.error_message = message
        task.metrics_json = {
            "op": "training.submit_job",
            "accepted": False,
            "result": safe_result_payload,
            "agent_contract": agent_contract,
        }
        job.failure_stage = "dispatch"
    task.updated_at = now
    job.updated_at = now
    audit_detail = {
        "ok": accepted,
        "gateway_id": instance.id,
        "result": safe_result_payload,
        "agent_contract": agent_contract,
    }
    if routing:
        audit_detail["routing"] = routing
    if require_dispatch_failure:
        audit_detail["retry_stage"] = "dispatch"
    await _record_training_audit(
        db,
        user,
        audit_action,
        job,
        audit_detail,
    )
    await db.commit()
    await db.refresh(job)
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    return _serialize_job(job, list(tasks))


async def _close_superseded_training_tasks(
    db: AsyncSession,
    *,
    job_id: str,
    task_id: int,
    result_status: str,
) -> None:
    if result_status not in FINAL_TRAINING_JOB_STATUSES:
        return
    rows = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job_id)
            .where(TrainingJobTask.id != task_id)
            .where(TrainingJobTask.status.in_(sorted(ACTIVE_TRAINING_TASK_STATUSES)))
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    if not rows:
        return
    now = now_bjt()
    for row in rows:
        row.status = "cancelled"
        row.error_message = row.error_message or f"superseded by task {task_id} {result_status} result"
        row.metrics_json = {
            **(row.metrics_json or {}),
            "superseded_by_task_id": task_id,
            "superseded_result_status": result_status,
        }
        row.updated_at = now


async def dispatch_training_job(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    return await _dispatch_training_job(db, user, job_id, audit_action="training_job.dispatch")


async def retry_training_job(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    return await _dispatch_training_job(
        db,
        user,
        job_id,
        audit_action="training_job.retry",
        require_dispatch_failure=True,
    )


async def auto_advance_training_job(
    db: AsyncSession,
    user: User,
    job_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to advance training automation"})
    payload = payload or {}
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    actions: list[dict[str, Any]] = []
    automation_user = _training_automation_user(job)
    if job.status in {"awaiting_review", "queued"}:
        routing = await _assign_training_gateway_if_missing(
            db,
            automation_user,
            job,
            reason="auto_advance",
            prefer_primary=payload.get("prefer_primary", True) is not False,
        )
        if routing:
            actions.append({
                "action": "route",
                "status": "completed",
                "target_gateway_id": job.target_gateway_id,
                "routing": routing,
            })
            job = await db.get(TrainingJob, job_id) or job
    if job.status == "awaiting_review":
        if payload.get("approve", True) is False:
            pass
        elif not _training_auto_approval_enabled(job):
            actions.append({"action": "approve", "status": "blocked", "reason": "auto approval disabled"})
        else:
            approved = await approve_training_job(db, automation_user, job_id)
            actions.append({"action": "approve", "status": "completed", "approved_by": approved.get("approved_by")})
            job = await db.get(TrainingJob, job_id) or job
    dispatch_enabled = payload.get("dispatch", True) is not False
    if dispatch_enabled and job.status == "queued":
        dispatched = await dispatch_training_job(db, automation_user, job_id)
        actions.append({"action": "dispatch", "status": dispatched.get("status"), "target_gateway_id": dispatched.get("target_gateway_id")})
        job = await db.get(TrainingJob, job_id) or job
    evaluate_enabled = payload.get("evaluate", True) is not False
    if evaluate_enabled and job.status == "completed" and _should_auto_evaluate_after_gateway_result(job):
        evaluated = await evaluate_training_job(db, automation_user, job_id)
        actions.append({"action": "evaluate", "status": evaluated.get("status")})
        job = await db.get(TrainingJob, job_id) or job
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job_id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    deployments = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.job_id == job_id)
            .order_by(TrainingModelDeployment.created_at.desc(), TrainingModelDeployment.id.desc())
        )
    ).scalars().all()
    return {
        "ok": True,
        "actions": actions,
        "job": _serialize_job(job, list(tasks), list(deployments)),
    }


def _training_job_verification_snapshot(job_payload: dict[str, Any]) -> dict[str, Any]:
    latest_deployment = job_payload.get("latest_deployment") if isinstance(job_payload.get("latest_deployment"), dict) else {}
    training_plan = job_payload.get("training_plan") if isinstance(job_payload.get("training_plan"), dict) else {}
    artifact = training_plan.get("artifact") if isinstance(training_plan.get("artifact"), dict) else {}
    automation_status = job_payload.get("automation_status") if isinstance(job_payload.get("automation_status"), dict) else {}
    tasks = job_payload.get("tasks") if isinstance(job_payload.get("tasks"), list) else []
    evaluation_task: dict[str, Any] = {}
    for item in reversed(tasks):
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        if metrics.get("op") == "training.evaluate":
            evaluation_task = item
            break
    steps = automation_status.get("steps") if isinstance(automation_status.get("steps"), list) else []
    return {
        "status": job_payload.get("status"),
        "failure_stage": job_payload.get("failure_stage"),
        "approved_by": job_payload.get("approved_by"),
        "target_gateway_id": job_payload.get("target_gateway_id"),
        "model_name": training_plan.get("model_name"),
        "artifact_uri": artifact.get("uri"),
        "artifact_sha256": artifact.get("sha256"),
        "evaluation_status": evaluation_task.get("status"),
        "deployment_id": latest_deployment.get("id"),
        "deployment_status": latest_deployment.get("status"),
        "deployment_target_gateway_id": latest_deployment.get("deployment_target_gateway_id"),
        "automation_steps": [
            f"{str(item.get('key') or '')}:{str(item.get('status') or '')}"
            for item in steps
            if isinstance(item, dict) and item.get("key")
        ],
    }


def _training_snapshot_changed_fields(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[dict[str, Any]]:
    keys = sorted(set(before.keys()) | set(after.keys()))
    return [
        {
            "field": key,
            "before": before.get(key),
            "after": after.get(key),
        }
        for key in keys
        if before.get(key) != after.get(key)
    ]


def _simulated_training_gateway_result_payload(
    job: TrainingJob,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    now = now_bjt()
    model_name = _training_model_name(job, None)
    date_segment = _training_model_date_segment(now)
    artifact_slug = _training_model_slug(model_name, fallback=job.id)
    artifact_name = f"{artifact_slug}-{date_segment}-adapter.safetensors"[:180]
    artifact_sha = hashlib.sha256(f"{job.id}:{model_name}:{date_segment}".encode("utf-8")).hexdigest()
    artifact_uri = f"artifact://training-sim/{job.id}/{artifact_name}"
    default_metrics = {
        "training_loss": 0.18,
        "eval_loss": 0.16,
        "win_rate": 0.92,
        "eval_requested_samples": 24,
        "eval_evaluated_samples": 24,
        "completed_at": isoformat_bjt(now),
        "simulated": True,
    }
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else None
    return {
        "job_id": job.id,
        "gateway_id": _clean_text(payload.get("gateway_id"), limit=50) or job.target_gateway_id or TRAINING_PRIMARY_GATEWAY_IDS[0],
        "worker_id": _clean_text(payload.get("worker_id"), limit=100) or f"training-sim-{job.id[-8:]}",
        "status": _clean_text(payload.get("status"), limit=30) or "completed",
        "progress": payload.get("progress", 1),
        "metrics": {
            **default_metrics,
            **metrics,
        },
        "artifacts": artifacts or [
            {
                "id": f"sim-{job.id}-{date_segment}",
                "type": "lora_adapter",
                "name": artifact_name,
                "uri": artifact_uri,
                "sha256": artifact_sha,
                "size_bytes": _safe_int(payload.get("size_bytes")) or 73400320,
                "target_gateway_id": job.target_gateway_id or TRAINING_PRIMARY_GATEWAY_IDS[0],
                "deployment_target_gateway_id": TRAINING_DEFAULT_DEPLOYMENT_GATEWAY_ID,
                "simulated": True,
            }
        ],
        "logs_url": _clean_text(payload.get("logs_url"), limit=500) or f"artifact://training-sim/{job.id}/logs.jsonl",
    }


async def simulate_training_job_result(
    db: AsyncSession,
    user: User,
    job_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to simulate training result"})
    payload = payload or {}
    before_job = await get_training_job(db, user, job_id)
    before_snapshot = _training_job_verification_snapshot(before_job)
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    advance_result: dict[str, Any] | None = None
    if payload.get("auto_advance_first", True) is not False and job.status == "awaiting_review":
        advance_result = await auto_advance_training_job(
            db,
            user,
            job_id,
            {
                "dispatch": False,
                "evaluate": False,
                "metadata": {"trigger": "training_job.simulate_result"},
            },
        )
        job = await db.get(TrainingJob, job_id) or job
    if job.status not in {"queued", "running", "evaluating", "unknown", "completed"}:
        raise AppError(
            "INVALID_STATUS",
            400,
            {
                "detail": "only queued, running, evaluating, unknown or completed jobs can receive simulated result",
                "status": job.status,
            },
        )
    result_payload = _simulated_training_gateway_result_payload(job, payload)
    token = issue_training_callback_token(job)
    gateway_result = await apply_training_gateway_result(
        db,
        job_id=job.id,
        token=token,
        payload=result_payload,
    )
    refreshed = await db.get(TrainingJob, job_id)
    if refreshed is not None:
        await _record_training_audit(
            db,
            user,
            "training_job.simulate_gateway_result",
            refreshed,
            {
                "gateway_id": result_payload.get("gateway_id"),
                "worker_id": result_payload.get("worker_id"),
                "result_status": result_payload.get("status"),
                "artifact_count": len(result_payload.get("artifacts") or []),
                "auto_advance_first": advance_result is not None,
            },
        )
        await db.commit()
    after_job = await get_training_job(db, user, job_id)
    after_snapshot = _training_job_verification_snapshot(after_job)
    return {
        "ok": True,
        "action": "training_job.simulate_gateway_result",
        "job_id": job_id,
        "advance": advance_result,
        "simulated_request": {
            "gateway_id": result_payload.get("gateway_id"),
            "worker_id": result_payload.get("worker_id"),
            "status": result_payload.get("status"),
            "progress": result_payload.get("progress"),
            "metrics": result_payload.get("metrics"),
            "artifacts": result_payload.get("artifacts"),
        },
        "gateway_result": gateway_result,
        "before": before_snapshot,
        "after": after_snapshot,
        "changed_fields": _training_snapshot_changed_fields(before_snapshot, after_snapshot),
        "job": after_job,
    }


async def run_training_automation_once(
    db: AsyncSession,
    user: User,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to run training automation"})
    payload = payload or {}
    started_at = now_bjt()
    max_datasets = max(0, min(_safe_int(payload.get("max_datasets")) or 20, 100))
    max_jobs = max(1, min(_safe_int(payload.get("max_jobs")) or 50, 200))
    materialize_learning = payload.get("materialize_learning", True) is not False
    materialize_limit = max(1, min(_safe_int(payload.get("materialize_limit")) or 50000, 50000))
    promote_learning_samples = payload.get("promote_learning_samples", True) is not False
    create_datasets = payload.get("create_datasets", True) is not False
    max_dataset_samples = max(1, min(_safe_int(payload.get("max_dataset_samples")) or 5000, TRAINING_DATASET_VERSION_MAX_SAMPLES))
    include_datasets = payload.get("datasets", True) is not False
    include_jobs = payload.get("jobs", True) is not False
    actions: list[dict[str, Any]] = []
    stats = {
        "learning_checked": 0,
        "learning_materialized": 0,
        "learning_samples_checked": 0,
        "learning_samples_promoted": 0,
        "learning_samples_deduped": 0,
        "datasets_created": 0,
        "datasets_seen": 0,
        "datasets_synced": 0,
        "jobs_seen": 0,
        "jobs_changed": 0,
        "failed": 0,
    }
    if materialize_learning:
        try:
            from app.learning.service import _materialize_system_training_artifacts

            materialized = await _materialize_system_training_artifacts(db, user, limit=materialize_limit)
            stats["learning_checked"] = _safe_int(materialized.get("checked"))
            stats["learning_materialized"] = _safe_int(materialized.get("materialized"))
            if materialized.get("error_count"):
                stats["failed"] += _safe_int(materialized.get("error_count"))
            if stats["learning_checked"] or stats["learning_materialized"] or materialized.get("error_count"):
                actions.append({
                    "target_type": "learning_artifact",
                    "target_id": "training_sample",
                    "action": "materialize_learning",
                    "status": "failed" if materialized.get("error_count") else "completed",
                    "result": materialized,
                })
            if stats["learning_materialized"] > 0:
                await db.commit()
        except AppError as exc:
            await db.rollback()
            stats["failed"] += 1
            actions.append({
                "target_type": "learning_artifact",
                "target_id": "training_sample",
                "action": "materialize_learning",
                "status": "failed",
                "error_code": exc.code,
                "detail": exc.detail,
            })
    if promote_learning_samples:
        try:
            promoted = await _promote_learning_artifacts_to_training_samples(db, user, limit=materialize_limit)
            stats["learning_samples_checked"] = _safe_int(promoted.get("checked"))
            stats["learning_samples_promoted"] = _safe_int(promoted.get("promoted"))
            stats["learning_samples_deduped"] = _safe_int(promoted.get("deduped"))
            if (
                stats["learning_samples_checked"]
                or stats["learning_samples_promoted"]
                or stats["learning_samples_deduped"]
            ):
                actions.append({
                    "target_type": "training_sample",
                    "target_id": "learning_artifact",
                    "action": "promote_learning_samples",
                    "status": "completed",
                    "result": promoted,
                })
            if stats["learning_samples_promoted"] > 0:
                await db.commit()
        except AppError as exc:
            await db.rollback()
            stats["failed"] += 1
            actions.append({
                "target_type": "training_sample",
                "target_id": "learning_artifact",
                "action": "promote_learning_samples",
                "status": "failed",
                "error_code": exc.code,
                "detail": exc.detail,
            })
    if create_datasets:
        try:
            created_datasets = await _auto_create_training_datasets_from_samples(
                db,
                user,
                max_datasets=max_datasets,
                sample_limit=max_dataset_samples,
            )
            stats["datasets_created"] = _safe_int(created_datasets.get("created"))
            if (
                created_datasets.get("seen_departments")
                or created_datasets.get("created")
                or created_datasets.get("skipped")
            ):
                actions.append({
                    "target_type": "training_dataset_version",
                    "target_id": "learning_artifact",
                    "action": "create_dataset_manifest",
                    "status": "completed",
                    "result": created_datasets,
                })
        except AppError as exc:
            await db.rollback()
            stats["failed"] += 1
            actions.append({
                "target_type": "training_dataset_version",
                "target_id": "learning_artifact",
                "action": "create_dataset_manifest",
                "status": "failed",
                "error_code": exc.code,
                "detail": exc.detail,
            })
    if include_datasets and TRAINING_AUTO_DATASET_SYNC_ENABLED and max_datasets:
        dataset_stmt = (
            select(TrainingDatasetVersion)
            .where(TrainingDatasetVersion.status.in_(["ready", "approved"]))
            .where(
                or_(
                    TrainingDatasetVersion.sync_status.is_(None),
                    TrainingDatasetVersion.sync_status == "",
                    TrainingDatasetVersion.sync_status != "completed",
                )
            )
            .order_by(TrainingDatasetVersion.created_at.desc())
            .limit(max_datasets)
        )
        if not _can_view_all_training_resources(user):
            department = _user_department(user)
            dataset_stmt = dataset_stmt.where(TrainingDatasetVersion.department == department if department else false())
        dataset_rows = (await db.execute(dataset_stmt)).scalars().all()
        stats["datasets_seen"] = len(dataset_rows)
        for row in dataset_rows:
            try:
                result = await auto_run_training_dataset_version(
                    db,
                    user,
                    row.id,
                    {
                        "target_gateway_id": row.target_gateway_id or TRAINING_PRIMARY_GATEWAY_IDS[0],
                        "metadata": {"trigger": "training_automation.run"},
                    },
                )
                sync_payload = _safe_dict(result.get("sync"))
                synced_dataset = _safe_dict(sync_payload.get("dataset_version"))
                training_sink = _safe_dict(sync_payload.get("training_sink"))
                sync_status = str(
                    synced_dataset.get("sync_status")
                    or training_sink.get("status")
                    or ""
                ).strip()
                if sync_status == "completed":
                    stats["datasets_synced"] += 1
                actions.append({
                    "target_type": "training_dataset_version",
                    "target_id": row.id,
                    "action": "auto_run",
                    "status": "completed" if sync_status == "completed" else "pending",
                    "result": result,
                })
            except AppError as exc:
                await db.rollback()
                stats["failed"] += 1
                actions.append({
                    "target_type": "training_dataset_version",
                    "target_id": row.id,
                    "action": "auto_run",
                    "status": "failed",
                    "error_code": exc.code,
                    "detail": exc.detail,
                })
    if include_jobs:
        route_priority = case(
            (
                or_(
                    TrainingJob.target_gateway_id.is_(None),
                    TrainingJob.target_gateway_id == "",
                    TrainingJob.target_gateway_id == "内容电商",
                ),
                0,
            ),
            (TrainingJob.failure_stage == "dispatch", 1),
            else_=2,
        )
        job_stmt = (
            select(TrainingJob)
            .where(TrainingJob.status.in_(["awaiting_review", "queued", "completed"]))
            .order_by(route_priority.asc(), TrainingJob.created_at.asc())
            .limit(max_jobs)
        )
        if not _can_view_all_training_resources(user):
            department = _user_department(user)
            job_stmt = job_stmt.where(TrainingJob.department == department if department else false())
        job_rows = (await db.execute(job_stmt)).scalars().all()
        stats["jobs_seen"] = len(job_rows)
        for job in job_rows:
            try:
                before = await get_training_job(db, user, job.id)
                result = await auto_advance_training_job(
                    db,
                    user,
                    job.id,
                    {
                        "approve": payload.get("approve", True),
                        "dispatch": payload.get("dispatch", True),
                        "evaluate": payload.get("evaluate", True),
                        "metadata": {"trigger": "training_automation.run"},
                    },
                )
                after = result.get("job") if isinstance(result.get("job"), dict) else await get_training_job(db, user, job.id)
                changed_fields = _training_snapshot_changed_fields(
                    _training_job_verification_snapshot(before),
                    _training_job_verification_snapshot(after),
                )
                if result.get("actions") or changed_fields:
                    stats["jobs_changed"] += 1
                actions.append({
                    "target_type": "training_job",
                    "target_id": job.id,
                    "action": "auto_advance",
                    "status": "completed",
                    "actions": result.get("actions") or [],
                    "changed_fields": changed_fields,
                    "job": after,
                })
            except AppError as exc:
                await db.rollback()
                stats["failed"] += 1
                actions.append({
                    "target_type": "training_job",
                    "target_id": job.id,
                    "action": "auto_advance",
                    "status": "failed",
                    "error_code": exc.code,
                    "detail": exc.detail,
                })
    finished_at = now_bjt()
    result = {
        "ok": stats["failed"] == 0,
        "trigger": _clean_text(payload.get("trigger"), limit=40) or "manual",
        "started_at": isoformat_bjt(started_at),
        "finished_at": isoformat_bjt(finished_at),
        "policy": _training_auto_policy_payload(),
        "stats": stats,
        "actions": actions,
    }
    global TRAINING_AUTOMATION_LAST_RUN
    TRAINING_AUTOMATION_LAST_RUN = result
    return result


async def evaluate_training_job(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to evaluate training job"})
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    task_list = list(tasks)
    existing_evaluation = _latest_training_evaluation_task(task_list)
    can_retry_eval_failure = (
        job.status == "failed"
        and job.failure_stage == "eval"
        and _latest_gateway_result(task_list) is not None
    )
    if job.status != "completed" and not can_retry_eval_failure:
        raise AppError("INVALID_STATUS", 400, {"detail": "only completed training jobs can be evaluated"})

    evaluation_result: dict[str, Any] | None = None
    if existing_evaluation is None or can_retry_eval_failure:
        evaluation_result = await _evaluate_training_job_uncommitted(
            db,
            user,
            job,
            task_list,
            trigger="manual",
        )
        await db.commit()
        await _refresh_auto_active_deployment_schedules_after_commit(db, evaluation_result)
    elif existing_evaluation.status == "completed":
        evaluation_result = await _retry_auto_deployment_for_existing_evaluation_uncommitted(
            db,
            _training_automation_user(job),
            job,
            task_list,
            existing_evaluation,
            trigger="manual_retry",
        )
        if evaluation_result is not None:
            await db.commit()
            await _refresh_auto_active_deployment_schedules_after_commit(db, evaluation_result)
    await db.refresh(job)
    updated_tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    deployments = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.job_id == job.id)
            .order_by(TrainingModelDeployment.created_at.desc(), TrainingModelDeployment.id.desc())
        )
    ).scalars().all()
    return _serialize_job(job, list(updated_tasks), list(deployments))


async def request_training_deployment(
    db: AsyncSession,
    user: User,
    job_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not _can_create_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to request training deployment"})
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    if job.status != "completed" or job.failure_stage:
        raise AppError("INVALID_STATUS", 400, {"detail": "only successfully evaluated training jobs can request deployment"})

    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    deployment, _ = await _create_training_deployment_request_uncommitted(
        db,
        user,
        job,
        payload,
        list(tasks),
    )
    try:
        from app.learning.service import capture_training_job

        await capture_training_job(db, job)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获手动训练部署申请失败 job={} deployment={} err={}", job.id, deployment.id, exc)
    await db.commit()
    await db.refresh(job)
    await db.refresh(deployment)
    deployments = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.job_id == job.id)
            .order_by(TrainingModelDeployment.created_at.desc(), TrainingModelDeployment.id.desc())
        )
    ).scalars().all()
    return {
        "deployment": _serialize_deployment(deployment),
        "job": _serialize_job(job, list(tasks), list(deployments)),
    }


async def approve_training_deployment(
    db: AsyncSession,
    user: User,
    deployment_id: str,
) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to approve training deployment"})
    deployment, job = await _get_deployment_and_job(db, user, deployment_id)
    if deployment.status != "awaiting_review":
        raise AppError("INVALID_STATUS", 400, {"detail": "only awaiting_review deployments can be approved"})
    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    if not artifact_ref.get("sha256"):
        raise AppError("INVALID_STATUS", 400, {"detail": "deployment approval requires artifact sha256"})
    runtime_status = await _require_deployment_inference_ready(db, deployment, job)

    now = now_bjt()
    previous_active = await _active_deployments_for_target(db, deployment)
    previous_ids = [item.id for item in previous_active]
    deployment.rollback_to = previous_ids[0] if previous_ids else deployment.rollback_to
    deployment.approved_by = str(getattr(user, "id", "") or "")
    deployment.approved_at = now
    deployment.status = "active" if int(deployment.rollout_percent or 0) >= 100 else "canary"
    deployment.updated_at = now
    if deployment.status == "active":
        deployment.activated_at = now
        for item in previous_active:
            item.status = "rolled_back"
            item.rollback_to = deployment.id
            item.updated_at = now
    schedule_skill_ids = _deployment_schedule_skill_ids(deployment, *previous_active)
    schedule_target_ids = await _deployment_schedule_target_instance_ids(db, schedule_skill_ids)

    await _record_training_deployment_audit(
        db,
        user,
        "training_deployment.approve",
        deployment,
        {
            "previous_active_deployment_ids": previous_ids,
            "rollout_percent": int(deployment.rollout_percent or 0),
            "activated": deployment.status == "active",
            "runtime_status": runtime_status,
        },
    )
    job.updated_at = now
    try:
        from app.learning.service import capture_model_deployment

        await capture_model_deployment(db, deployment, runtime_status=runtime_status)
        if deployment.status == "active":
            for item in previous_active:
                await capture_model_deployment(db, item)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获模型部署审批失败 deployment={} err={}", deployment.id, exc)
    await db.commit()
    if deployment.status == "active":
        schedule_refresh_results = await _refresh_deployment_runtime_schedules(
            deployment_id=deployment.id,
            skill_ids=schedule_skill_ids,
            target_instance_ids=schedule_target_ids,
        )
        try:
            from app.learning.service import capture_model_deployment

            async with db.begin():
                refreshed = await db.get(TrainingModelDeployment, deployment.id)
                if refreshed is not None:
                    await capture_model_deployment(
                        db,
                        refreshed,
                        runtime_status={
                            **runtime_status,
                            "schedule_refresh": _deployment_schedule_refresh_payload(schedule_refresh_results),
                        },
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug("智能闭环捕获模型部署节点刷新结果失败 deployment={} err={}", deployment.id, exc)
    await db.refresh(job)
    await db.refresh(deployment)
    return await _deployment_response(db, deployment, job)


async def reject_training_deployment(
    db: AsyncSession,
    user: User,
    deployment_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to reject training deployment"})
    deployment, job = await _get_deployment_and_job(db, user, deployment_id)
    if deployment.status != "awaiting_review":
        raise AppError("INVALID_STATUS", 400, {"detail": "only awaiting_review deployments can be rejected"})
    cleaned_reason = _clean_text(reason, limit=1000, required=True) or ""
    if len(cleaned_reason) < 10:
        raise AppError("PARAM_INVALID", 422, {"detail": "reject reason must be at least 10 characters"})

    now = now_bjt()
    deployment.status = "rejected"
    deployment.rejected_by = str(getattr(user, "id", "") or "")
    deployment.rejected_at = now
    deployment.reject_reason = cleaned_reason
    deployment.updated_at = now
    await _record_training_deployment_audit(
        db,
        user,
        "training_deployment.reject",
        deployment,
        {"reason": cleaned_reason},
    )
    job.updated_at = now
    try:
        from app.learning.service import capture_model_deployment

        await capture_model_deployment(db, deployment)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获模型部署驳回失败 deployment={} err={}", deployment.id, exc)
    await db.commit()
    await db.refresh(job)
    await db.refresh(deployment)
    return await _deployment_response(db, deployment, job)


async def activate_training_deployment(
    db: AsyncSession,
    user: User,
    deployment_id: str,
) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to activate training deployment"})
    deployment, job = await _get_deployment_and_job(db, user, deployment_id)
    if deployment.status != "canary":
        raise AppError("INVALID_STATUS", 400, {"detail": "only canary deployments can be activated"})
    runtime_status = await _require_deployment_inference_ready(db, deployment, job)

    now = now_bjt()
    previous_active = await _active_deployments_for_target(db, deployment)
    previous_ids = [item.id for item in previous_active]
    if previous_ids and not deployment.rollback_to:
        deployment.rollback_to = previous_ids[0]
    for item in previous_active:
        item.status = "rolled_back"
        item.rollback_to = deployment.id
        item.updated_at = now
    deployment.status = "active"
    deployment.rollout_percent = 100
    deployment.activated_at = now
    deployment.updated_at = now
    schedule_skill_ids = _deployment_schedule_skill_ids(deployment, *previous_active)
    schedule_target_ids = await _deployment_schedule_target_instance_ids(db, schedule_skill_ids)
    await _record_training_deployment_audit(
        db,
        user,
        "training_deployment.activate",
        deployment,
        {
            "previous_active_deployment_ids": previous_ids,
            "rollout_percent": 100,
            "runtime_status": runtime_status,
        },
    )
    job.updated_at = now
    try:
        from app.learning.service import capture_model_deployment

        await capture_model_deployment(db, deployment, runtime_status=runtime_status)
        for item in previous_active:
            await capture_model_deployment(db, item)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获模型部署激活失败 deployment={} err={}", deployment.id, exc)
    await db.commit()
    schedule_refresh_results = await _refresh_deployment_runtime_schedules(
        deployment_id=deployment.id,
        skill_ids=schedule_skill_ids,
        target_instance_ids=schedule_target_ids,
    )
    try:
        from app.learning.service import capture_model_deployment

        async with db.begin():
            refreshed = await db.get(TrainingModelDeployment, deployment.id)
            if refreshed is not None:
                await capture_model_deployment(
                    db,
                    refreshed,
                    runtime_status={
                        **runtime_status,
                        "schedule_refresh": _deployment_schedule_refresh_payload(schedule_refresh_results),
                    },
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获模型部署节点刷新结果失败 deployment={} err={}", deployment.id, exc)
    await db.refresh(job)
    await db.refresh(deployment)
    return await _deployment_response(db, deployment, job)


async def rollback_training_deployment(
    db: AsyncSession,
    user: User,
    deployment_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to rollback training deployment"})
    deployment, job = await _get_deployment_and_job(db, user, deployment_id)
    if deployment.status not in TRAINING_DEPLOYMENT_ACTIVE_STATUSES:
        raise AppError("INVALID_STATUS", 400, {"detail": "only canary or active deployments can be rolled back"})
    cleaned_reason = _clean_text(reason, limit=1000, required=True) or ""
    if len(cleaned_reason) < 10:
        raise AppError("PARAM_INVALID", 422, {"detail": "rollback reason must be at least 10 characters"})

    now = now_bjt()
    restored_deployment_id: str | None = None
    if deployment.status == "active" and deployment.rollback_to:
        previous = await db.get(TrainingModelDeployment, deployment.rollback_to)
        if (
            previous
            and previous.department == deployment.department
            and previous.model_family == deployment.model_family
            and _deployments_target_overlap(previous, deployment)
        ):
            previous.status = "active"
            previous.activated_at = previous.activated_at or now
            previous.updated_at = now
            restored_deployment_id = previous.id

    deployment.status = "rolled_back"
    deployment.updated_at = now
    restored_deployment: TrainingModelDeployment | None = None
    if restored_deployment_id:
        restored_deployment = await db.get(TrainingModelDeployment, restored_deployment_id)
    schedule_skill_ids = _deployment_schedule_skill_ids(deployment, restored_deployment)
    schedule_target_ids = await _deployment_schedule_target_instance_ids(db, schedule_skill_ids)
    await _record_training_deployment_audit(
        db,
        user,
        "training_deployment.rollback",
        deployment,
        {
            "reason": cleaned_reason,
            "restored_deployment_id": restored_deployment_id,
        },
    )
    job.updated_at = now
    try:
        from app.learning.service import capture_model_deployment

        await capture_model_deployment(db, deployment)
        if restored_deployment:
            await capture_model_deployment(db, restored_deployment)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获模型部署回滚失败 deployment={} err={}", deployment.id, exc)
    await db.commit()
    await _refresh_deployment_runtime_schedules(
        deployment_id=deployment.id,
        skill_ids=schedule_skill_ids,
        target_instance_ids=schedule_target_ids,
    )
    await db.refresh(job)
    await db.refresh(deployment)
    return await _deployment_response(db, deployment, job)


async def apply_training_gateway_result(
    db: AsyncSession,
    *,
    job_id: str,
    token: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    verify_training_callback_token(token, job=job)
    result = _validate_result_payload(payload)
    if result["job_id"] and result["job_id"] != job.id:
        raise AppError("PARAM_INVALID", 422, {"detail": "training result job_id mismatch"})
    gateway_id = result["gateway_id"] or job.target_gateway_id
    if job.target_gateway_id and gateway_id and gateway_id != job.target_gateway_id:
        raise AppError("RUN_TOKEN_INVALID", 401, {"reason": "gateway_id_mismatch"})
    _validate_gateway_result_transition(job, result["status"])
    _validate_completed_result_contract(job, result)

    now = now_bjt()
    task_stmt = select(TrainingJobTask).where(TrainingJobTask.job_id == job.id)
    if gateway_id:
        task_stmt = task_stmt.where(TrainingJobTask.gateway_id == gateway_id)
    task = (await db.execute(task_stmt.order_by(TrainingJobTask.id.desc()))).scalars().first()
    if task is None:
        task = TrainingJobTask(
            job_id=job.id,
            gateway_id=gateway_id,
            status=result["status"],
            progress=result["progress"],
        )
        db.add(task)
        await db.flush()
    task.status = result["status"]
    task.progress = result["progress"]
    task.worker_id = result["worker_id"] or task.worker_id
    task.logs_url = result["logs_url"] or task.logs_url
    task.error_message = result["error"]
    task.metrics_json = {
        **(task.metrics_json or {}),
        "gateway_result": {
            "status": result["status"],
            "metrics": result["metrics"],
            "artifacts": result["artifacts"],
        },
    }
    task.updated_at = now
    await _close_superseded_training_tasks(
        db,
        job_id=job.id,
        task_id=task.id,
        result_status=result["status"],
    )

    job.status = result["status"]
    if result["status"] == "failed":
        job.failure_stage = result["failure_stage"] or "gateway"
    elif result["status"] in {"completed", "running", "evaluating", "queued"}:
        job.failure_stage = None
    job.updated_at = now
    await _record_training_system_audit(
        db,
        "training_job.gateway_result",
        job,
        {
            "gateway_id": gateway_id,
            "worker_id": result["worker_id"],
            "result_status": result["status"],
        },
    )
    auto_evaluation = await _maybe_auto_evaluate_completed_training_job(
        db,
        job,
        trigger="gateway_result",
    )
    try:
        from app.learning.service import capture_training_job

        await capture_training_job(db, job)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获训练网关结果失败 job={} err={}", job.id, exc)
    await db.commit()
    await _refresh_auto_active_deployment_schedules_after_commit(db, auto_evaluation)
    await db.refresh(job)
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    deployments = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.job_id == job.id)
            .order_by(TrainingModelDeployment.created_at.desc(), TrainingModelDeployment.id.desc())
        )
    ).scalars().all()
    payload = _serialize_job(job, list(tasks), list(deployments))
    if auto_evaluation is not None:
        payload["auto_evaluation"] = auto_evaluation
    return payload


async def collect_training_job_result(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to collect training result"})
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    if job.status in FINAL_TRAINING_JOB_STATUSES:
        raise AppError("INVALID_STATUS", 400, {"detail": "final training job cannot collect gateway result"})
    instance = await _get_training_gateway_instance(db, user, job)
    try:
        payload = await AIClawClient(
            instance.id,
            gateway_kind=instance.bridge_gateway_kind,
        ).collect_training_result(
            {"job_id": job.id},
        )
    except AppError as exc:
        if exc.status in {401, 403}:
            raise
        raise AppError(
            "TRAINING_COLLECT_FAILED",
            502,
            {"detail": _extract_error_message(exc), "gateway_id": instance.id},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            "TRAINING_COLLECT_FAILED",
            502,
            {"detail": _extract_error_message(exc), "gateway_id": instance.id},
        ) from exc

    raw_payload = payload if isinstance(payload, dict) else {}
    result = _validate_result_payload({**raw_payload, "gateway_id": raw_payload.get("gateway_id") or instance.id})
    if result["job_id"] and result["job_id"] != job.id:
        raise AppError("PARAM_INVALID", 422, {"detail": "training result job_id mismatch"})
    gateway_id = result["gateway_id"] or job.target_gateway_id
    if job.target_gateway_id and gateway_id and gateway_id != job.target_gateway_id:
        raise AppError("RUN_TOKEN_INVALID", 401, {"reason": "gateway_id_mismatch"})
    _validate_gateway_result_transition(job, result["status"])
    _validate_completed_result_contract(job, result)

    now = now_bjt()
    task_stmt = select(TrainingJobTask).where(TrainingJobTask.job_id == job.id)
    if gateway_id:
        task_stmt = task_stmt.where(TrainingJobTask.gateway_id == gateway_id)
    task = (await db.execute(task_stmt.order_by(TrainingJobTask.id.desc()))).scalars().first()
    if task is None:
        task = TrainingJobTask(
            job_id=job.id,
            gateway_id=gateway_id,
            status=result["status"],
            progress=result["progress"],
        )
        db.add(task)
        await db.flush()
    task.status = result["status"]
    task.progress = result["progress"]
    task.worker_id = result["worker_id"] or task.worker_id
    task.logs_url = result["logs_url"] or task.logs_url
    task.error_message = result["error"]
    task.metrics_json = {
        **(task.metrics_json or {}),
        "gateway_result": {
            "status": result["status"],
            "metrics": result["metrics"],
            "artifacts": result["artifacts"],
            "collected": True,
        },
    }
    task.updated_at = now
    await _close_superseded_training_tasks(
        db,
        job_id=job.id,
        task_id=task.id,
        result_status=result["status"],
    )

    job.status = result["status"]
    if result["status"] == "failed":
        job.failure_stage = result["failure_stage"] or "gateway"
    elif result["status"] in {"completed", "running", "evaluating", "queued"}:
        job.failure_stage = None
    job.updated_at = now
    await _record_training_audit(
        db,
        user,
        "training_job.collect_result",
        job,
        {
            "gateway_id": gateway_id,
            "worker_id": result["worker_id"],
            "result_status": result["status"],
        },
    )
    auto_evaluation = await _maybe_auto_evaluate_completed_training_job(
        db,
        job,
        trigger="collect_result",
    )
    try:
        from app.learning.service import capture_training_job

        await capture_training_job(db, job)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获训练结果拉取失败 job={} err={}", job.id, exc)
    await db.commit()
    await _refresh_auto_active_deployment_schedules_after_commit(db, auto_evaluation)
    await db.refresh(job)
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id)
        )
    ).scalars().all()
    deployments = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.job_id == job.id)
            .order_by(TrainingModelDeployment.created_at.desc(), TrainingModelDeployment.id.desc())
        )
    ).scalars().all()
    payload = _serialize_job(job, list(tasks), list(deployments))
    if auto_evaluation is not None:
        payload["auto_evaluation"] = auto_evaluation
    return payload


async def collect_due_training_job_results(
    db: AsyncSession,
    user: User,
    *,
    stale_seconds: int = 300,
    max_jobs: int = 20,
    target_gateway_id: str | None = None,
) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to collect training results"})
    stale_seconds = max(0, min(int(stale_seconds or 0), 24 * 60 * 60))
    max_jobs = max(1, min(int(max_jobs or 20), 100))
    gateway_id = _clean_text(target_gateway_id, limit=50)
    cutoff = now_bjt() - timedelta(seconds=stale_seconds)
    stmt = (
        select(TrainingJob.id)
        .where(TrainingJob.status.in_(TRAINING_COLLECT_DUE_STATUSES))
        .where(TrainingJob.target_gateway_id.isnot(None))
        .where(TrainingJob.updated_at <= cutoff)
        .order_by(TrainingJob.updated_at.asc(), TrainingJob.created_at.asc())
        .limit(max_jobs)
    )
    if gateway_id:
        stmt = stmt.where(TrainingJob.target_gateway_id == gateway_id)
    if not _can_view_all_training_resources(user):
        department = _user_department(user)
        stmt = stmt.where(TrainingJob.department == department if department else false())
    job_ids = (await db.execute(stmt)).scalars().all()

    items: list[dict[str, Any]] = []
    for job_id in job_ids:
        job = await db.get(TrainingJob, job_id)
        if not job:
            items.append({"job_id": job_id, "status": "skipped", "reason": "not_found"})
            continue
        if job.status == "queued":
            task_exists = (
                await db.execute(
                    select(TrainingJobTask.id)
                    .where(TrainingJobTask.job_id == job.id)
                    .limit(1)
                )
            ).scalars().first()
            if not task_exists:
                items.append({"job_id": job.id, "status": "skipped", "reason": "not_dispatched"})
                continue
        if job.failure_stage == "dispatch":
            items.append({"job_id": job.id, "status": "skipped", "reason": "dispatch_failed"})
            continue
        try:
            collected = await collect_training_job_result(db, user, job.id)
        except AppError as exc:
            await db.rollback()
            items.append({
                "job_id": job.id,
                "status": "failed",
                "reason": exc.code,
                "detail": _sanitize_training_result_value(exc.detail or {}),
            })
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            items.append({
                "job_id": job.id,
                "status": "failed",
                "reason": "TRAINING_COLLECT_FAILED",
                "detail": {"detail": _extract_error_message(exc)},
            })
        else:
            items.append({
                "job_id": job.id,
                "status": "collected",
                "result_status": collected.get("status"),
                "updated_at": collected.get("updated_at"),
            })
    return {
        "items": items,
        "total": len(items),
        "stale_seconds": stale_seconds,
        "max_jobs": max_jobs,
        "target_gateway_id": gateway_id,
        "stats": {
            "collected": sum(1 for item in items if item["status"] == "collected"),
            "skipped": sum(1 for item in items if item["status"] == "skipped"),
            "failed": sum(1 for item in items if item["status"] == "failed"),
        },
    }


async def cancel_training_job(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    is_creator = str(getattr(user, "id", "") or "") == job.created_by
    if not (is_creator or _can_approve_training_job(user)):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to cancel training job"})
    if job.status in FINAL_TRAINING_JOB_STATUSES:
        raise AppError("INVALID_STATUS", 400, {"detail": "final training job cannot be cancelled"})
    now = now_bjt()
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id.desc())
        )
    ).scalars().all()
    latest_task = tasks[0] if tasks else None
    cancel_result: dict[str, Any] | None = None
    cancel_error: str | None = None
    should_cancel_bridge = job.status in {"running", "evaluating"} and bool(job.target_gateway_id)
    if should_cancel_bridge:
        try:
            instance = await _get_training_gateway_instance(db, user, job)
            result = await AIClawClient(
                instance.id,
                gateway_kind=instance.bridge_gateway_kind,
            ).cancel_training_job(
                {
                    "job_id": job.id,
                    "gateway_id": instance.id,
                    "worker_id": latest_task.worker_id if latest_task else None,
                },
            )
            cancel_result = _sanitize_training_result_value(result if isinstance(result, dict) else {})
            if not isinstance(cancel_result, dict):
                cancel_result = {}
        except AppError as exc:
            if exc.status in {401, 403}:
                raise
            cancel_error = _extract_error_message(exc)
            if latest_task is not None:
                latest_task.error_message = cancel_error
                latest_task.metrics_json = {
                    **(latest_task.metrics_json or {}),
                    "cancel": {
                        "ok": False,
                        "error": cancel_error,
                    },
                }
                latest_task.updated_at = now
            job.failure_stage = "cancel"
            job.updated_at = now
            await _record_training_audit(
                db,
                user,
                "training_job.cancel",
                job,
                {"ok": False, "gateway_id": job.target_gateway_id, "error": cancel_error},
            )
            await db.commit()
            await db.refresh(job)
            ordered_tasks = list(reversed(tasks))
            return _serialize_job(job, ordered_tasks)
        except Exception as exc:  # noqa: BLE001
            cancel_error = _extract_error_message(exc)
            if latest_task is not None:
                latest_task.error_message = cancel_error
                latest_task.metrics_json = {
                    **(latest_task.metrics_json or {}),
                    "cancel": {
                        "ok": False,
                        "error": cancel_error,
                    },
                }
                latest_task.updated_at = now
            job.failure_stage = "cancel"
            job.updated_at = now
            await _record_training_audit(
                db,
                user,
                "training_job.cancel",
                job,
                {"ok": False, "gateway_id": job.target_gateway_id, "error": cancel_error},
            )
            await db.commit()
            await db.refresh(job)
            ordered_tasks = list(reversed(tasks))
            return _serialize_job(job, ordered_tasks)

    for task in tasks:
        if task.status not in {"completed", "failed", "cancelled"}:
            task.status = "cancelled"
            task.progress = min(float(task.progress or 0), 1)
            task.metrics_json = {
                **(task.metrics_json or {}),
                "cancel": {
                    "ok": True,
                    "result": cancel_result or {},
                },
            }
            task.updated_at = now
    job.status = "cancelled"
    job.cancelled_by = str(getattr(user, "id", "") or "")
    job.cancelled_at = now
    job.failure_stage = None
    job.updated_at = now
    await _record_training_audit(
        db,
        user,
        "training_job.cancel",
        job,
        {
            "ok": True,
            "gateway_id": job.target_gateway_id,
            "bridge_cancelled": bool(should_cancel_bridge),
            "result": cancel_result or {},
        },
    )
    await db.commit()
    await db.refresh(job)
    ordered_tasks = list(reversed(tasks))
    return _serialize_job(job, ordered_tasks)


async def force_cancel_training_job(
    db: AsyncSession,
    user: User,
    job_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    if not _can_approve_training_job(user):
        raise AppError("FORBIDDEN", 403, {"detail": "no permission to force cancel training job"})
    job = await db.get(TrainingJob, job_id)
    if not job:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    _require_training_department_access(user, job.department)
    if job.status in FINAL_TRAINING_JOB_STATUSES:
        raise AppError("INVALID_STATUS", 400, {"detail": "final training job cannot be force cancelled"})
    if job.failure_stage != "cancel" or job.status not in {"running", "evaluating", "unknown"}:
        raise AppError("INVALID_STATUS", 400, {"detail": "only stuck cancel training jobs can be force cancelled"})
    cleaned_reason = _clean_text(reason, limit=1000, required=True) or ""
    if len(cleaned_reason) < 10:
        raise AppError("PARAM_INVALID", 422, {"detail": "force cancel reason must be at least 10 characters"})

    now = now_bjt()
    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job.id)
            .order_by(TrainingJobTask.id.desc())
        )
    ).scalars().all()
    for task in tasks:
        if task.status not in {"completed", "failed", "cancelled"}:
            task.status = "cancelled"
            task.metrics_json = {
                **(task.metrics_json or {}),
                "force_cancel": {
                    "ok": True,
                    "reason": cleaned_reason,
                    "cleanup_pending": True,
                },
            }
            task.updated_at = now
    job.status = "cancelled"
    job.cancelled_by = str(getattr(user, "id", "") or "")
    job.cancelled_at = now
    job.failure_stage = None
    job.updated_at = now
    await _record_training_audit(
        db,
        user,
        "training_job.force_cancel",
        job,
        {
            "reason": cleaned_reason,
            "gateway_id": job.target_gateway_id,
            "cleanup_pending": True,
        },
    )
    await db.commit()
    await db.refresh(job)
    ordered_tasks = list(reversed(tasks))
    return _serialize_job(job, ordered_tasks)
