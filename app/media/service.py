"""Control-plane service for the Material Workbench and MiniMax H3 jobs.

The web process plans, authorizes, schedules and records work.  It never runs
ComfyUI itself; execution is delegated to allow-listed Bridge media operations.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aiclaw.bridge_registry import bridge_registry
from app.aiclaw.client import AIClawClient
from app.auth.models import User
from app.codex import service as codex_service
from app.common.ai import call_llm_multimodal, get_ai_profile_config, redact_secret_text
from app.common.cache import cache_get, cache_set
from app.common.exceptions import AppError
from app.common.retry import RetryableError
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime
from app.execution.models import OpenClawInstance
from app.projects.models import Project, ProjectRun, ProjectRunAsset
from app.training.models import TrainingJob, TrainingModelDeployment, TrainingSample

from .models import (
    CloudVideoSyncOutbox,
    MediaContinuationChain,
    MediaGenerationAttempt,
    MediaGenerationJob,
    MediaPlanComparison,
    MediaProductionBatch,
    MediaReviewAnnotation,
    MediaCreativeStrategySession,
    MediaReplayProject,
    MediaReplaySegment,
    MediaWorkflowDefinition,
)
from .h3_prompt_policy import (
    H3PromptPolicyError,
    H3_PROMPT_MAX_CHARS,
    H3_PROMPT_POLICY_VERSION,
    H3_REFERENCE_LIMITS,
    H3_REFERENCE_MAX_BYTES,
    H3_REFERENCE_MAX_TOTAL,
    H3_REFERENCE_ROLES,
    brief_excludes_brand_fidelity,
    brief_excludes_people,
    compile_h3_plan,
    planning_contract_description,
    production_variant_for_candidate,
)
from .production_policy import PRODUCTION_POLICY_VERSION, production_policy_for, quality_signal_for_intent
from .speech_delivery import (
    SPEECH_DELIVERY_POLICY_VERSION,
    SPEECH_DELIVERY_RENDERER_ID,
    SpeechDeliveryError,
    render_governed_dialogue,
    transcribe_governed_dialogue,
)

MATERIAL_WORKBENCH_PROJECT_ID = "samplebrand-material-workbench"
MATERIAL_WORKBENCH_DEPARTMENT_ID = "931765248"
MATERIAL_WORKBENCH_DEPARTMENT = "示例品牌内容电商运营部"
MEDIA_BASELINE_PROFILE = "deepseek-v4-flash"
MEDIA_PLAN_ATTEMPT_TIMEOUT_SECONDS = 72
MEDIA_PLAN_COMPARE_TIMEOUT_SECONDS = 160
MEDIA_PLAN_RESULT_CACHE_TTL_SECONDS = 30 * 60
MEDIA_PLAN_TEMPLATE_VERSION = "h3-plan-v3"
MEDIA_H3_MODEL_FAMILY = "minimax-h3"
MEDIA_TRAINING_THRESHOLD = 300
MEDIA_QUALITY_ANALYSIS_POLICY_VERSION = "material-video-quality-v27"
MEDIA_PRODUCT_OVERLAY_ALPHA_POLICY_VERSION = "product-overlay-alpha-v1"
MEDIA_PRODUCT_OVERLAY_MAX_ASSETS = 2
MEDIA_QUALITY_ANALYSIS_STALE_SECONDS = 15 * 60
MEDIA_DIALOGUE_AUTO_STALE_SECONDS = 15 * 60
# H3 supports clips up to 15 seconds. Sampling only the first six seconds made
# the reviewer incorrectly report late subtitles and selling points as missing.
MEDIA_QUALITY_ANALYSIS_MAX_FRAMES = 15
MEDIA_QUALITY_ANALYSIS_MAX_FRAME_BYTES = 2 * 1024 * 1024
MEDIA_TRAINING_ACTIVE_STATUSES = {
    "awaiting_review",
    "approved",
    "queued",
    "dispatching",
    "running",
    "evaluating",
    "collecting_result",
}
MEDIA_JOB_MODES = {"text_to_video", "image_to_video", "reference_replay", "video_enhance", "video_local_edit"}
MEDIA_CAPABILITIES = {
    "video.plan_compare",
    "video.job.submit",
    "video.job.batch_submit",
    "video.job.batch_cancel",
    "video.job.list",
    "video.job.get",
    "video.job.cancel",
    "video.job.retry",
    "video.job.clone",
    "video.quality.analyze",
    "video.review",
    "cloud_video.sync",
}
from .workbench_v2 import NEW_MEDIA_CAPABILITIES
from .workbench_v3 import V3_MEDIA_CAPABILITIES
from .fde_v4 import FDE_MEDIA_CAPABILITIES

MEDIA_CAPABILITIES |= NEW_MEDIA_CAPABILITIES
MEDIA_CAPABILITIES |= V3_MEDIA_CAPABILITIES
MEDIA_CAPABILITIES |= FDE_MEDIA_CAPABILITIES
MEDIA_JOB_STATUSES = {
    "draft",
    "planned",
    "queued",
    "assigned",
    "running",
    "collecting",
    "awaiting_review",
    "approved",
    "rejected",
    "syncing",
    "synced",
    "failed",
    "cancelled",
}
MEDIA_TERMINAL_STATUSES = {"synced", "failed", "cancelled", "rejected"}
MEDIA_ALLOWED_TRANSITIONS = {
    "draft": {"planned", "cancelled"},
    "planned": {"queued", "cancelled"},
    "queued": {"assigned", "cancelled", "failed"},
    "assigned": {"running", "queued", "cancelled", "failed"},
    "running": {"collecting", "queued", "cancelled", "failed"},
    "collecting": {"awaiting_review", "queued", "failed", "cancelled"},
    "awaiting_review": {"approved", "rejected"},
    "approved": {"syncing"},
    "rejected": {"queued"},
    "syncing": {"synced", "approved", "failed"},
    "failed": {"queued", "cancelled"},
    "cancelled": {"queued"},
    "synced": set(),
}
MEDIA_DEFAULT_PRESET = {
    "width": 480,
    "height": 864,
    "frames": 124,
    "fps": 24,
    "steps": 20,
    "seed": -1,
    "batch_count": 1,
    "audio_enabled": True,
}
MEDIA_EIGHT_SECOND_PRESET = {**MEDIA_DEFAULT_PRESET, "frames": 192}
MEDIA_MAX_RESULT_BYTES = 512 * 1024 * 1024
MEDIA_RESULT_CHUNK_BYTES = 4 * 1024 * 1024
MEDIA_QUEUE_WAIT_MESSAGE = "媒体节点当前繁忙或能力尚未就绪；任务正按优先级排队。"
MEDIA_BRIDGE_DISCONNECT_GRACE_SECONDS = 90
MEDIA_ORPHAN_RECOVERY_MAX_ATTEMPTS = 3
MEDIA_PROMPT_ORPHAN_ERROR_CODE = "MEDIA_PROMPT_ORPHANED"
MEDIA_BRIDGE_RECONNECT_WAIT_MESSAGE = "媒体节点连接短暂中断，正在等待 90 秒自动恢复，暂不重复派单。"
MEDIA_ROUTING_QUALITY_PROFILE_TTL_SECONDS = 60
MEDIA_ROUTING_QUALITY_PROFILE_LIMIT = 240
_MEDIA_ROUTING_QUALITY_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, dict[str, Any]]]] = {}
MEDIA_QUALITY_DIMENSION_KEYS = (
    "prompt_alignment",
    "visual_continuity",
    "hook_strength",
    "shot_boundary_clarity",
    "batch_diversity",
    "commercial_readiness",
    "product_fidelity",
    "selling_point_coverage",
    "silhouette_safety",
)
MEDIA_QUALITY_ANALYSIS_STATUSES = {
    "awaiting_review",
    "approved",
    "rejected",
    "syncing",
    "synced",
}
MEDIA_TRAINING_QUALITY_MINIMUMS = {
    "prompt_alignment": 4,
    "visual_continuity": 4,
    "hook_strength": 3,
    "shot_boundary_clarity": 3,
    "batch_diversity": 4,
    "commercial_readiness": 4,
    "product_fidelity": 4,
    "selling_point_coverage": 3,
    "silhouette_safety": 4,
}
_PLAN_REQUIRED_KEYS = {
    "creative_goal",
    "audience",
    "selling_points",
    "shots",
    "video_prompt",
    "audio_prompt",
    "negative_constraints",
    "reference_roles",
    "h3_mode",
    "recommended_params",
    "duration_seconds",
    "ratio",
    "brand_guardrails",
    "assumptions",
    "warnings",
}


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:20]}"


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_H3_REFERENCE_PLACEHOLDER_RE = re.compile(r"<(Picture|Video|Audio)\s+([1-9]\d*)>")
_H3_UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _validated_user_compiled_prompt(value: Any, references: list[dict[str, Any]]) -> str | None:
    """Validate an explicit prompt edit without opening the workflow contract.

    The deterministic compiler remains the source of the structured plan and
    reference mapping.  A user may edit only the final text sent into the
    allow-listed H3 prompt node; all required placeholders must remain intact.
    """

    if value is None:
        return None
    prompt = str(value).strip()
    if not prompt:
        raise AppError("MEDIA_PROMPT_OVERRIDE_INVALID", 422, {"field": "user_compiled_prompt", "detail": "prompt is empty"})
    if len(prompt) > H3_PROMPT_MAX_CHARS:
        raise AppError(
            "MEDIA_PROMPT_OVERRIDE_INVALID",
            422,
            {"field": "user_compiled_prompt", "detail": f"prompt exceeds {H3_PROMPT_MAX_CHARS} characters"},
        )
    if _H3_UNSAFE_CONTROL_RE.search(prompt):
        raise AppError(
            "MEDIA_PROMPT_OVERRIDE_INVALID",
            422,
            {"field": "user_compiled_prompt", "detail": "prompt contains unsupported control characters"},
        )
    if not prompt.startswith("integrated_multimodal_description:"):
        raise AppError(
            "MEDIA_PROMPT_OVERRIDE_INVALID",
            422,
            {"field": "user_compiled_prompt", "detail": "prompt must keep the integrated_multimodal_description header"},
        )

    counters = {"image": 0, "video": 0, "audio": 0}
    labels = {"image": "Picture", "video": "Video", "audio": "Audio"}
    expected: set[tuple[str, int]] = set()
    for item in references:
        kind = H3_REFERENCE_ROLES.get(_text(item.get("role"), 40))
        if not kind:
            continue
        counters[kind] += 1
        expected.add((labels[kind], counters[kind]))
    actual = {(label, int(index)) for label, index in _H3_REFERENCE_PLACEHOLDER_RE.findall(prompt)}
    if actual != expected:
        missing = sorted(f"<{label} {index}>" for label, index in expected - actual)
        unexpected = sorted(f"<{label} {index}>" for label, index in actual - expected)
        raise AppError(
            "MEDIA_PROMPT_OVERRIDE_INVALID",
            422,
            {
                "field": "user_compiled_prompt",
                "detail": "reference placeholders must exactly match the validated source assets",
                "missing": missing,
                "unexpected": unexpected,
            },
        )
    return prompt


def transition_media_job(job: MediaGenerationJob, target: str, *, error: str | None = None) -> None:
    """Apply the single media state machine used by UI, scheduler and tests."""

    current = str(job.status or "draft")
    if target not in MEDIA_JOB_STATUSES:
        raise AppError("MEDIA_STATUS_INVALID", 422, {"status": target})
    if target != current and target not in MEDIA_ALLOWED_TRANSITIONS.get(current, set()):
        raise AppError("MEDIA_STATUS_TRANSITION_INVALID", 409, {"from": current, "to": target})
    now = now_bjt()
    job.status = target
    job.updated_at = now
    job.error = _text(error, 4000) or None
    if target in MEDIA_TERMINAL_STATUSES or target == "awaiting_review":
        job.completed_at = now


def normalize_media_params(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = {**MEDIA_DEFAULT_PRESET, **_safe_dict(payload)}

    def integer(name: str, minimum: int, maximum: int) -> int:
        try:
            value = int(raw.get(name))
        except (TypeError, ValueError) as exc:
            raise AppError("MEDIA_PARAM_INVALID", 422, {"field": name, "reason": "must be integer"}) from exc
        if value < minimum or value > maximum:
            raise AppError("MEDIA_PARAM_INVALID", 422, {"field": name, "minimum": minimum, "maximum": maximum})
        return value

    width = integer("width", 256, 1536)
    height = integer("height", 256, 1536)
    frames = integer("frames", 21, 361)
    fps = integer("fps", 12, 30)
    steps = integer("steps", 4, 60)
    batch_count = integer("batch_count", 1, 8)
    try:
        seed = int(raw.get("seed", -1))
    except (TypeError, ValueError) as exc:
        raise AppError("MEDIA_PARAM_INVALID", 422, {"field": "seed", "reason": "must be integer"}) from exc
    if width % 32 or height % 32:
        raise AppError("MEDIA_PARAM_INVALID", 422, {"detail": "width and height must be multiples of 32"})
    return {
        "width": width,
        "height": height,
        "frames": frames,
        "fps": fps,
        "steps": steps,
        "seed": seed,
        "batch_count": batch_count,
        "audio_enabled": bool(raw.get("audio_enabled", True)),
    }


def _parse_plan_output(value: Any, *, brief: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if isinstance(value, dict):
        parsed = value
    else:
        text = str(value or "").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(parsed, dict) or not _PLAN_REQUIRED_KEYS.issubset(parsed):
        return None
    if parsed.get("h3_mode") not in MEDIA_JOB_MODES:
        return None
    if not isinstance(parsed.get("shots"), list) or not isinstance(parsed.get("recommended_params"), dict):
        return None
    try:
        return compile_h3_plan(parsed, brief=brief)
    except H3PromptPolicyError:
        return None


def _decoded_plan_object(value: Any) -> dict[str, Any] | None:
    """Decode a model response without accepting an incomplete H3 plan."""

    if isinstance(value, dict):
        parsed: Any = value
    else:
        raw = str(value or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(parsed, dict):
        return None
    # Some JSON-mode providers still wrap the requested object. Unwrap only a
    # single well-known envelope; arbitrary nested data must not become a plan.
    for key in ("plan", "result", "output"):
        nested = parsed.get(key)
        if isinstance(nested, dict) and not any(name in parsed for name in ("shots", "h3_mode", "video_prompt")):
            return nested
    return parsed


def _plan_output_validation_error(value: Any, *, brief: dict[str, Any] | None = None) -> str:
    parsed = _decoded_plan_object(value)
    if parsed is None:
        return "响应不是可解析的 JSON 对象"
    missing = sorted(_PLAN_REQUIRED_KEYS - set(parsed))
    if missing:
        return "缺少顶层字段: " + ", ".join(missing)
    if parsed.get("h3_mode") not in MEDIA_JOB_MODES:
        return "h3_mode 不在受控模式白名单"
    if not isinstance(parsed.get("shots"), list) or not parsed.get("shots"):
        return "shots 必须是非空数组"
    if not isinstance(parsed.get("recommended_params"), dict):
        return "recommended_params 必须是对象"
    try:
        compile_h3_plan(parsed, brief=brief)
    except H3PromptPolicyError as exc:
        return f"{exc.field}: {exc.message}"
    return "未知结构错误"


def _repair_plan_output(
    value: Any,
    *,
    brief: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Deterministically repair non-creative schema gaps in a usable plan.

    This deliberately refuses to invent a storyboard: at least one model-made
    shot must exist. Creative facts remain model/user supplied, while aliases,
    defaults and types are normalized before the governed H3 compiler runs.
    """

    source = _decoded_plan_object(value)
    if source is None:
        return None, []
    repaired = dict(source)
    changed: set[str] = set()
    aliases = {
        "creative_goal": ("goal", "objective"),
        "audience": ("target_audience",),
        "selling_points": ("key_selling_points", "benefits"),
        "shots": ("shot_list", "storyboard", "scenes"),
        "video_prompt": ("visual_prompt", "prompt"),
        "audio_prompt": ("sound_prompt",),
        "negative_constraints": ("negative_prompt", "constraints"),
        "reference_roles": ("references",),
        "h3_mode": ("mode", "video_mode"),
        "recommended_params": ("params",),
        "duration_seconds": ("duration",),
        "ratio": ("aspect_ratio",),
        "brand_guardrails": ("guardrails",),
    }
    for target, candidates in aliases.items():
        if target in repaired:
            continue
        for candidate in candidates:
            if candidate in repaired:
                repaired[target] = repaired[candidate]
                changed.add(target)
                break

    shots = repaired.get("shots")
    if not isinstance(shots, list) or not any(isinstance(item, dict) for item in shots):
        return None, sorted(changed)

    brief_data = _safe_dict(brief)

    def fill(name: str, value: Any) -> None:
        if name not in repaired or repaired.get(name) is None:
            repaired[name] = value
            changed.add(name)

    fill("creative_goal", _text(brief_data.get("request") or brief_data.get("requirement"), 1000) or "生成符合需求的投流视频")
    fill("audience", _text(brief_data.get("audience"), 500) or "成年投流受众")
    fill("selling_points", _safe_list(brief_data.get("selling_points")))
    fill("video_prompt", _text(brief_data.get("request") or brief_data.get("requirement"), 4000))
    fill("audio_prompt", _text(brief_data.get("script"), 2000))
    fill("negative_constraints", _safe_list(brief_data.get("compliance_constraints")))
    fill("reference_roles", [])
    fill("h3_mode", _text(brief_data.get("requested_h3_mode"), 40) or "text_to_video")
    fill("duration_seconds", brief_data.get("duration_seconds") or brief_data.get("requested_duration_seconds") or 5)
    fill("ratio", _text(brief_data.get("ratio"), 20) or "9:16")
    fill("brand_guardrails", {})
    fill("assumptions", [])
    fill("warnings", [])

    for name in ("selling_points", "negative_constraints", "reference_roles", "assumptions", "warnings"):
        current = repaired.get(name)
        if isinstance(current, str):
            repaired[name] = [current] if current.strip() else []
            changed.add(name)
        elif not isinstance(current, list):
            repaired[name] = []
            changed.add(name)
    if not isinstance(repaired.get("brand_guardrails"), dict):
        repaired["brand_guardrails"] = {}
        changed.add("brand_guardrails")
    params = repaired.get("recommended_params")
    if not isinstance(params, dict):
        params = {}
        changed.add("recommended_params")
    merged_params = {**MEDIA_DEFAULT_PRESET, **params}
    if merged_params != params:
        changed.add("recommended_params")
    repaired["recommended_params"] = merged_params

    compiled = _parse_plan_output(repaired, brief=brief_data)
    if not compiled:
        return None, sorted(changed)
    compiled["plan_contract_repair"] = {
        "applied": True,
        "template_version": MEDIA_PLAN_TEMPLATE_VERSION,
        "normalized_fields": sorted(changed),
    }
    return compiled, sorted(changed)


def _plan_retry_prompt(prompt: str, value: Any, reason: str) -> str:
    try:
        previous = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        previous = str(value or "")
    previous = previous[:12000]
    return (
        prompt
        + "\n上次响应已成功返回，但没有通过 H3 方案结构校验。"
        + f"校验错误：{reason}。请只修复结构和类型，不改变用户需求与镜头创意。"
        + "必须只返回一个字段完整的 JSON 对象。\n上次响应："
        + previous
    )


def _planning_system_prompt() -> str:
    keys = ", ".join(sorted(_PLAN_REQUIRED_KEYS))
    return (
        "你是内容电商短视频制片规划器。只输出一个 JSON 对象，不要 Markdown。"
        f"顶层字段必须完整包含：{keys}。"
        "h3_mode 只能是 text_to_video、image_to_video、reference_replay。"
        "shots 必须是镜头数组；recommended_params 必须包含 width、height、frames、fps、steps、seed、batch_count、audio_enabled。"
        "brand_guardrails 必须是对象，assumptions 和 warnings 必须是数组。"
        "遵守输入中的合规约束，不编造产品功效、资质或用户数据。默认 MiniMax H3 竖屏约 5 秒。"
        "若输入包含 source_understanding，这是 Qwen-VL 对上传视频和图片的只读视觉取证，属于素材事实约束："
        "不得篡改或臆造素材本身已有的人物、场景、商品和动作；但用户在需求或 ad_material_contract 中明确要求"
        "新生成的成年演员、对话与场景可以生成，不能把一张仅含商品的透明图误当成人物首帧。"
        "若 ad_material_contract.performance_beats 存在，逐句执行说话人的单一主动作、听者的自然反应、视线、"
        "停顿与身体受力连续，禁止木偶式点头、非说话人冻结、抢话和重复手势。若 source_replication_contract.enabled=true，"
        "必须按 source_shots 保持时长、构图、相机、主体数量和动作，只替换明确 product_assets 对应的商品槽位；"
        "remove_source_overlays 只删除源视频字幕/角标/水印，不得误删已审核目标商品参考图自身的包装外观。"
        + planning_contract_description()
    )


async def _call_baseline_plan(db: AsyncSession, user: User, run: ProjectRun, brief: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    prompt = "把以下自然语言需求拆成可编辑的 MiniMax H3 生产方案：\n" + json.dumps(brief, ensure_ascii=False)
    last_output: Any = None
    failures: list[str] = []
    retry_prompt = prompt
    for profile in (MEDIA_BASELINE_PROFILE, "configured"):
        for attempt in range(2):
            try:
                response = await asyncio.wait_for(
                    codex_service._builtin_platform_ai_analyze(
                        db,
                        user,
                        {
                            "prompt": prompt if attempt == 0 else retry_prompt,
                            "system": _planning_system_prompt(),
                            "context_pack": {"project_run_id": run.id, "template_version": MEDIA_PLAN_TEMPLATE_VERSION},
                            "json_mode": True,
                            "temperature": 0.35,
                            "max_output_tokens": 4096,
                            "model_profile": profile,
                        },
                        effective_skill_id=None,
                        effective_run_id=run.execution_run_id or run.id,
                    ),
                    timeout=MEDIA_PLAN_ATTEMPT_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                failures.append(f"{profile}: timeout_after_{MEDIA_PLAN_ATTEMPT_TIMEOUT_SECONDS}s")
                # A second request to the same stuck profile only lengthens the
                # user-visible outage. Move to the explicitly reported
                # configured fallback; invalid JSON still retries once below.
                break
            except (AppError, RetryableError) as exc:
                failures.append(f"{profile}: {getattr(exc, 'code', type(exc).__name__)}")
                if attempt == 0 and getattr(exc, "code", "") == "PLATFORM_AI_CALL_FAILED":
                    continue
                break
            last_output = response.get("output")
            plan = _parse_plan_output(last_output, brief=brief)
            validation_error = ""
            if not plan:
                validation_error = _plan_output_validation_error(last_output, brief=brief)
                failures.append(f"{profile}: invalid_structure: {validation_error}")
                plan, repaired_fields = _repair_plan_output(last_output, brief=brief)
                if plan:
                    logger.info(
                        "素材工作台已确定性修复 AI 方案结构 run={} profile={} fields={}",
                        run.id,
                        profile,
                        ",".join(repaired_fields),
                    )
            if plan:
                actual_model = str(response.get("model") or profile)
                if profile != MEDIA_BASELINE_PROFILE:
                    flash_schema_failed = any(
                        item.startswith(f"{MEDIA_BASELINE_PROFILE}: invalid_structure:") for item in failures
                    )
                    fallback_warning = (
                        f"请求模型 {MEDIA_BASELINE_PROFILE} 已返回内容但未通过 H3 方案结构校验；"
                        f"本次已明确回退到实际模型 {actual_model}。"
                        if flash_schema_failed
                        else f"请求模型 {MEDIA_BASELINE_PROFILE} 调用失败或超时；本次已明确回退到实际模型 {actual_model}。"
                    )
                    warnings = [str(item) for item in plan.get("warnings") or [] if str(item).strip()]
                    plan["warnings"] = [fallback_warning, *warnings]
                    plan["requested_baseline_model"] = MEDIA_BASELINE_PROFILE
                    plan["baseline_fallback_reason"] = "; ".join(failures)[:1000]
                return actual_model, plan
            retry_prompt = _plan_retry_prompt(prompt, last_output, validation_error)
    raise AppError(
        "MEDIA_PLAN_JSON_INVALID",
        422,
        {
            "detail": "AI 未返回合法结构，已尝试请求模型及平台已配置模型；原始内容已保留供人工编辑。",
            "editable_output": _text(last_output, 12000),
            "failures": failures,
        },
    )


def _is_bound_media_deployment(deployment: TrainingModelDeployment, project_id: str) -> bool:
    """Only expose a deployed planner when it is bound to this project and an immutable artifact."""
    expected = {project_id, f"project:{project_id}", MATERIAL_WORKBENCH_PROJECT_ID}
    targets = {str(item) for item in _safe_list(deployment.target_skill_ids_json)}
    artifact_ref = _safe_dict(deployment.artifact_ref_json)
    artifact_sha256 = _text(artifact_ref.get("sha256"), 64).lower()
    return bool(
        deployment.status in {"canary", "active"}
        and targets & expected
        and _text(deployment.artifact_id, 120)
        and re.fullmatch(r"[0-9a-f]{64}", artifact_sha256)
        and _text(deployment.deployment_target_gateway_id, 50)
    )


async def _active_candidate_deployment(db: AsyncSession, project: Project) -> TrainingModelDeployment | None:
    rows = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.status.in_(["canary", "active"]))
            .where(TrainingModelDeployment.department == (project.department or MATERIAL_WORKBENCH_DEPARTMENT))
            .order_by(TrainingModelDeployment.updated_at.desc())
            .limit(20)
        )
    ).scalars().all()
    for row in rows:
        if _is_bound_media_deployment(row, project.id):
            return row
    return None


def _deployment_model_version(deployment: TrainingModelDeployment) -> str:
    """Return an immutable display version for an actually deployed artifact."""
    artifact_ref = _safe_dict(deployment.artifact_ref_json)
    for key in ("model_version", "adapter_version", "version"):
        value = _text(artifact_ref.get(key), 180)
        if value:
            return value
    family = _text(deployment.model_family, 120) or "fine-tuned-model"
    sha256 = _text(artifact_ref.get("sha256"), 64)
    if sha256:
        return f"{family}@{sha256[:12].lower()}"
    artifact_id = _text(deployment.artifact_id or artifact_ref.get("id"), 80)
    if artifact_id:
        return f"{family}@{artifact_id}"
    return f"{family}@{deployment.id}"


async def _call_candidate_plan(
    deployment: TrainingModelDeployment | None,
    brief: dict[str, Any],
) -> tuple[TrainingModelDeployment | None, str | None, dict[str, Any]]:
    if deployment is None or not deployment.deployment_target_gateway_id:
        return deployment, None, {}
    if not bridge_registry.is_online(deployment.deployment_target_gateway_id):
        return deployment, _deployment_model_version(deployment), {}
    result = await AIClawClient(deployment.deployment_target_gateway_id).run_training_inference(
        {
            "deployment_id": deployment.id,
            "model_id": deployment.model_family,
            "prompt": _planning_system_prompt() + "\n用户需求：" + json.dumps(brief, ensure_ascii=False),
            "json_mode": True,
            "max_tokens": 4096,
        },
        timeout=300,
    )
    plan = _parse_plan_output(result.get("output") or result.get("text") or result.get("result"), brief=brief)
    actual_version = _text(result.get("model_version"), 180) or _deployment_model_version(deployment)
    return deployment, actual_version, plan or {}


def serialize_plan(row: MediaPlanComparison) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "project_run_id": row.project_run_id,
        "status": row.status,
        "brief": row.brief_json or {},
        "baseline": {"model": row.baseline_model, "output": row.baseline_output_json or {}},
        "candidate": {
            "deployment_id": row.candidate_deployment_id,
            "model": row.candidate_model,
            "output": row.candidate_output_json or {},
            "available": bool(row.candidate_output_json),
        },
        "selected_source": row.selected_source,
        "final_output": row.final_output_json or {},
        "edits": row.edits_json or {},
        "template_version": row.template_version,
        "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


def serialize_job(row: MediaGenerationJob, attempt: MediaGenerationAttempt | None = None) -> dict[str, Any]:
    result = {
        "id": row.id,
        "project_id": row.project_id,
        "project_run_id": row.project_run_id,
        "plan_comparison_id": row.plan_comparison_id,
        "cloned_from_job_id": row.cloned_from_job_id,
        "production_batch_id": row.production_batch_id,
        "workflow_definition_id": row.workflow_definition_id,
        "workflow_definition_version": row.workflow_definition_version,
        "continuation_chain_id": row.continuation_chain_id,
        "strategy_session_id": row.strategy_session_id,
        "strategy_direction_id": row.strategy_direction_id,
        "replay_project_id": row.replay_project_id,
        "replay_segment_id": row.replay_segment_id,
        "prompt_preview_sha256": row.prompt_preview_sha256,
        "execution_prompt_sha256": row.execution_prompt_sha256,
        "depends_on_job_id": row.depends_on_job_id,
        "sequence_index": row.sequence_index,
        "not_before_at": isoformat_bjt(row.not_before_at),
        "deadline_at": isoformat_bjt(row.deadline_at),
        "mode": row.mode,
        "status": row.status,
        "priority": row.priority,
        "assigned_instance_id": row.assigned_instance_id,
        "attempt_no": row.current_attempt_no,
        "prompt": row.prompt_json or {},
        "params": row.params_json or {},
        "reference_assets": row.reference_assets_json or [],
        "workflow_template_id": row.workflow_template_id,
        "workflow_version": row.workflow_version,
        "model_version": row.model_version,
        "model_sha256": row.model_sha256,
        "result_asset_id": row.result_asset_id,
        "result_sha256": row.result_sha256,
        "result": row.result_json or {},
        "review": row.review_json or {},
        "review_assignee_id": row.review_assignee_id,
        "review_tags": row.review_tags_json or [],
        "business_title": row.business_title,
        "output_preset_id": row.output_preset_id,
        "poster_asset_id": row.poster_asset_id,
        "source_roles": row.source_roles_json or [],
        "creative_option": row.creative_option,
        "job_group_id": row.job_group_id,
        "rights": row.rights_json or {},
        "training_eligibility": row.training_eligibility,
        "error": row.error,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
        "completed_at": isoformat_bjt(row.completed_at),
    }
    if attempt:
        result["attempt"] = {
            "attempt_no": attempt.attempt_no,
            "instance_id": attempt.instance_id,
            "bridge_job_id": attempt.bridge_job_id,
            "status": attempt.status,
            "metrics": attempt.metrics_json or {},
            "log_tail": attempt.log_tail,
            "error": attempt.error,
        }
    return result


def _job_business_title(payload: dict[str, Any], brief: dict[str, Any], plan: dict[str, Any]) -> str:
    explicit = _text(payload.get("business_title"), 240)
    if explicit:
        return explicit
    product = _text(brief.get("product"), 80) or "未命名产品"
    campaign = _safe_dict(plan.get("production_campaign"))
    angle = (
        _text(campaign.get("direction_title"), 80)
        or _text(brief.get("creative_angle"), 80)
        or _text(plan.get("creative_goal"), 80)
        or "投流素材"
    )
    option_key = _text(payload.get("creative_option"), 40) or "smart"
    option = {
        "smart": "智能生成",
        "reference_replay": "参考复刻",
        "talking_product": "口播换品",
        "product_frame": "商品首帧",
        "dialogue": "双人对话",
        "continuation": "自动续写",
    }.get(option_key, option_key)
    try:
        version = max(1, int(campaign.get("candidate_index") or payload.get("variant_index") or 1))
    except (TypeError, ValueError):
        version = 1
    return _text(f"{product} · {angle} · {option} · v{version:02d}", 240)


def _plan_cache_key(brief: dict[str, Any]) -> str:
    """Stable cache key for an identical governed planning request.

    The prompt/compiler policy versions are part of the key so a deployment
    never serves a plan generated by an older contract.  This cache is only a
    latency/retry optimization; every request still creates its own audited
    MediaPlanComparison row after the cached plan is recompiled below.
    """
    canonical = json.dumps(
        {
            "baseline_model": MEDIA_BASELINE_PROFILE,
            "template_version": MEDIA_PLAN_TEMPLATE_VERSION,
            "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
            "brief": brief,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"media:plan:{digest}"


async def _cached_baseline_plan(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    brief: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    cache_key = _plan_cache_key(brief)
    try:
        cached = await cache_get(cache_key)
    except Exception as exc:  # Redis loss must not block production planning.
        logger.warning("素材工作台规划缓存读取失败，继续调用 AI run={} err={}", run.id, exc)
        cached = None
    cached_value = _safe_dict(cached)
    cached_plan = _safe_dict(cached_value.get("plan"))
    if cached_plan:
        try:
            compiled = compile_h3_plan(cached_plan, brief=brief)
        except H3PromptPolicyError:
            compiled = {}
        if compiled:
            return _text(cached_value.get("model"), 180) or MEDIA_BASELINE_PROFILE, compiled

    model, plan = await _call_baseline_plan(db, user, run, brief)
    try:
        await cache_set(
            cache_key,
            {"model": model, "plan": plan},
            ttl=MEDIA_PLAN_RESULT_CACHE_TTL_SECONDS,
        )
    except Exception as exc:  # Planning succeeded; a cache outage is non-fatal.
        logger.warning("素材工作台规划缓存写入失败 run={} err={}", run.id, exc)
    return model, plan


def _encode_job_cursor(row: MediaGenerationJob) -> str:
    raw = json.dumps(
        {"created_at": isoformat_bjt(row.created_at), "id": row.id},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_job_cursor(value: Any) -> tuple[datetime, str] | None:
    text = _text(value, 500)
    if not text:
        return None
    try:
        padding = "=" * ((4 - len(text) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode((text + padding).encode("ascii")).decode("utf-8"))
        created_at = parse_bjt_datetime(payload.get("created_at"))
        row_id = _text(payload.get("id"), 50)
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppError("MEDIA_JOB_FILTER_INVALID", 422, {"field": "cursor"}) from exc
    if not row_id:
        raise AppError("MEDIA_JOB_FILTER_INVALID", 422, {"field": "cursor"})
    return created_at, row_id


async def _quality_gated_training_jobs(db: AsyncSession, project_id: str) -> list[MediaGenerationJob]:
    rows = (
        await db.execute(
            select(MediaGenerationJob).where(
                MediaGenerationJob.project_id == project_id,
                MediaGenerationJob.status.in_(["approved", "syncing", "synced"]),
                MediaGenerationJob.training_eligibility == "eligible",
            )
        )
    ).scalars().all()
    return [
        row
        for row in rows
        if _media_training_quality_gate(_safe_dict((row.review_json or {}).get("quality_evaluation")))[0]
    ]


async def _quality_gated_sft_sample_ids(
    db: AsyncSession,
    project_id: str,
    accepted_job_ids: set[str],
) -> list[str]:
    if not accepted_job_ids:
        return []
    rows = (
        await db.execute(
            select(TrainingSample)
            .where(
                TrainingSample.project_id == project_id,
                TrainingSample.dataset_profile == "text_sft_v1",
                TrainingSample.status == "ready",
            )
            .order_by(TrainingSample.created_at.asc(), TrainingSample.id.asc())
            .limit(5000)
        )
    ).scalars().all()
    return [
        row.id
        for row in rows
        if _text(_safe_dict(row.metadata_json).get("media_job_id"), 50) in accepted_job_ids
    ]


async def _media_training_progress(db: AsyncSession, project: Project) -> dict[str, Any]:
    accepted_jobs = await _quality_gated_training_jobs(db, project.id)
    accepted_job_ids = {row.id for row in accepted_jobs}
    accepted_count = len(accepted_job_ids)
    qualified_sample_ids = await _quality_gated_sft_sample_ids(db, project.id, accepted_job_ids)
    ready_sft_samples = len(qualified_sample_ids)
    jobs = (
        await db.execute(
            select(TrainingJob)
            .where(TrainingJob.target_skill_id == f"project:{project.id}")
            .order_by(TrainingJob.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    deployments = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.department == (project.department or MATERIAL_WORKBENCH_DEPARTMENT))
            .order_by(TrainingModelDeployment.updated_at.desc())
            .limit(20)
        )
    ).scalars().all()
    latest_deployment = next(
        (item for item in deployments if _is_bound_media_deployment(item, project.id)),
        None,
    )
    threshold = MEDIA_TRAINING_THRESHOLD
    gate_ready = min(accepted_count, ready_sft_samples) >= threshold
    latest_job = next(
        (
            item
            for item in jobs
            if item.status in MEDIA_TRAINING_ACTIVE_STATUSES
            and _safe_dict(item.spec_json).get("source") == "media_workbench_learning_loop"
            and int(_safe_dict(item.spec_json).get("accepted_count") or 0) >= threshold
            and gate_ready
        ),
        None,
    )
    latest_history = jobs[0] if jobs else None
    artifact_ref = _safe_dict(latest_deployment.artifact_ref_json) if latest_deployment else {}
    if latest_deployment:
        status = "deployed"
    elif latest_job and latest_job.status in {"running", "evaluating", "collecting_result"}:
        status = "training"
    elif latest_job:
        status = "candidate"
    else:
        status = "collecting"
    return {
        "status": status,
        "accepted_count": accepted_count,
        "ready_sft_samples": ready_sft_samples,
        "threshold": threshold,
        "gate_ready": gate_ready,
        "remaining": max(0, threshold - min(accepted_count, ready_sft_samples)),
        "latest_job": {
            "id": latest_job.id,
            "title": latest_job.title,
            "status": latest_job.status,
            "target_gateway_id": latest_job.target_gateway_id,
        } if latest_job else None,
        "latest_history": {
            "id": latest_history.id,
            "title": latest_history.title,
            "status": latest_history.status,
            "target_gateway_id": latest_history.target_gateway_id,
            "source": _safe_dict(latest_history.spec_json).get("source"),
            "eligible_for_media_gate": bool(latest_history is latest_job),
        } if latest_history else None,
        "deployed_model": {
            "deployment_id": latest_deployment.id,
            "model_family": latest_deployment.model_family,
            "model_version": _deployment_model_version(latest_deployment),
            "artifact_id": latest_deployment.artifact_id,
            "artifact_sha256": artifact_ref.get("sha256"),
            "status": latest_deployment.status,
            "rollout_percent": latest_deployment.rollout_percent,
            "gateway_id": latest_deployment.deployment_target_gateway_id,
        } if latest_deployment else None,
    }


def _bridge_caps(instance: OpenClawInstance) -> dict[str, Any]:
    raw = instance.bridge_capabilities_json
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _media_preset_fingerprint(params: dict[str, Any]) -> tuple[int, int, int, int, int]:
    return tuple(int(params.get(key) or 0) for key in ("width", "height", "frames", "fps", "steps"))


def _job_production_policy(job: MediaGenerationJob) -> dict[str, Any]:
    persisted = _safe_dict(_safe_dict(getattr(job, "result_json", {})).get("production_policy"))
    if persisted.get("production_intent"):
        return persisted
    prompt = _safe_dict(getattr(job, "prompt_json", {}))
    shot_sounds = [
        _text(item.get("sound") or item.get("audio"), 1000)
        for item in _safe_list(prompt.get("shots"))
        if isinstance(item, dict)
    ]
    legacy_brief = {
        "product": _text(getattr(job, "business_title", None), 240),
        "script": "\n".join(item for item in shot_sounds if item),
        "request": " ".join(
            filter(None, (_text(prompt.get("creative_goal"), 2000), _text(prompt.get("video_prompt"), 5000)))
        ),
        "contains_person": bool(_safe_dict(getattr(job, "rights_json", {})).get("contains_person")),
    }
    return production_policy_for(
        legacy_brief,
        _safe_list(getattr(job, "source_roles_json", None))
        or _safe_list(getattr(job, "reference_assets_json", None)),
        mode=_text(getattr(job, "mode", None), 30) or "text_to_video",
    )


async def _recent_media_quality_profiles(
    db: AsyncSession,
    *,
    project_id: str,
    production_intent: str,
    params: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Summarise recent same-intent/same-preset evidence per physical node."""

    fingerprint = _media_preset_fingerprint(params)
    cache_key = (project_id, production_intent, *fingerprint)
    cached = _MEDIA_ROUTING_QUALITY_CACHE.get(cache_key)
    monotonic_now = time.monotonic()
    if cached and cached[0] > monotonic_now:
        return cached[1]
    rows = (
        await db.execute(
            select(MediaGenerationAttempt, MediaGenerationJob)
            .join(MediaGenerationJob, MediaGenerationJob.id == MediaGenerationAttempt.job_id)
            .where(
                MediaGenerationJob.project_id == project_id,
                MediaGenerationAttempt.instance_id.is_not(None),
                MediaGenerationAttempt.attempt_no == MediaGenerationJob.current_attempt_no,
                MediaGenerationAttempt.status.in_(["completed", "failed"]),
            )
            .order_by(MediaGenerationAttempt.completed_at.desc().nullslast())
            .limit(MEDIA_ROUTING_QUALITY_PROFILE_LIMIT)
        )
    ).all()
    buckets: dict[str, dict[str, Any]] = {}
    for attempt, job in rows:
        policy = _job_production_policy(job)
        if policy.get("production_intent") != production_intent:
            continue
        if _media_preset_fingerprint(_safe_dict(job.params_json)) != fingerprint:
            continue
        instance_id = _text(attempt.instance_id, 50)
        if not instance_id:
            continue
        bucket = buckets.setdefault(instance_id, {"attempts": 0, "successes": 0, "quality": [], "durations": []})
        bucket["attempts"] += 1
        if attempt.status != "completed":
            continue
        bucket["successes"] += 1
        signal = quality_signal_for_intent(
            _safe_dict(_safe_dict(job.result_json).get("quality_analysis")),
            production_intent,
        )
        if signal is not None:
            bucket["quality"].append(signal)
        metrics = _safe_dict(attempt.metrics_json)
        try:
            duration = float(metrics.get("total_seconds") or float(metrics.get("duration_ms") or 0) / 1000)
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            bucket["durations"].append(duration)
    profiles: dict[str, dict[str, Any]] = {}
    for instance_id, bucket in buckets.items():
        attempts = int(bucket["attempts"])
        values = list(bucket["quality"])[-12:]
        durations = list(bucket["durations"])[-12:]
        profiles[instance_id] = {
            "sample_count": len(values),
            "attempt_count": attempts,
            "success_rate": round(int(bucket["successes"]) / max(1, attempts), 4),
            "average_quality": round(sum(values) / len(values), 4) if values else None,
            "average_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        }
    _MEDIA_ROUTING_QUALITY_CACHE[cache_key] = (
        monotonic_now + MEDIA_ROUTING_QUALITY_PROFILE_TTL_SECONDS,
        profiles,
    )
    return profiles


def _score_media_instance(
    instance: OpenClawInstance,
    *,
    mode: str,
    params: dict[str, Any],
    active_jobs: int = 0,
    production_policy: dict[str, Any] | None = None,
    quality_profile: dict[str, Any] | None = None,
    requires_product_overlay: bool = False,
) -> tuple[int, dict[str, Any]] | None:
    if not bool(instance.is_active) or not bridge_registry.is_online(instance.id):
        return None
    # The allow-listed H3 templates are memory intensive.  Until a node reports
    # an explicit parallel capacity, keep one governed media job per GPU node.
    if active_jobs >= 1:
        return None
    purpose = str(instance.agent_purpose or "skill_runtime").lower()
    if purpose not in {"media", "mixed"}:
        return None
    caps = _bridge_caps(instance)
    ops = {str(item) for item in _safe_list(caps.get("ops"))}
    media = _safe_dict(caps.get("media"))
    roles = {str(item) for item in _safe_list(caps.get("workload_roles")) + _safe_list(media.get("workload_roles"))}
    if "media.submit_job" not in ops or "video_generation" not in roles:
        return None
    modes = {str(item) for item in _safe_list(media.get("supported_modes"))}
    if mode not in modes:
        return None
    if requires_product_overlay and not bool(
        _safe_dict(media.get("postprocess")).get("governed_product_overlay")
    ):
        return None
    gpus = _safe_list(caps.get("gpu"))
    gpu = max((item for item in gpus if isinstance(item, dict)), key=lambda item: int(item.get("vram_free_mb") or 0), default={})
    free_mb = int(gpu.get("vram_free_mb") or 0)
    required_mb = (
        6000 if mode in {"video_enhance", "video_local_edit"}
        else 22000 if mode == "reference_replay" or int(params.get("batch_count") or 1) > 1
        else 12000
    )
    if free_mb and free_mb < required_mb:
        return None
    queue_depth = int(media.get("queue_depth") or 0)
    name = str(gpu.get("name") or instance.name or "").lower()
    standard = int(params.get("frames") or 0) <= 124 and int(params.get("batch_count") or 1) == 1
    policy = _safe_dict(production_policy)
    routing_policy = _text(policy.get("routing_policy"), 40) or "throughput_balanced"
    score = 1000 - queue_depth * 80 + min(free_mb // 256, 300)
    if "5080" in name and mode == "video_enhance":
        score += 700
    elif "5080" in name and standard and mode != "reference_replay":
        # A validated 5-second single render belongs on the 5080 even when the
        # content policy is quality-first. Visual quality is enforced by the
        # same v12 review gate on both nodes; routing it to the 96GB node only
        # wastes the shared reference/long-form capacity and leaves the 5080
        # idle. The PRO remains the automatic overflow when the 5080 is busy.
        score += 900
    if ("pro 6000" in name or "6000" in name) and (not standard or mode == "reference_replay"):
        score += 600
    elif ("pro 6000" in name or "6000" in name) and routing_policy in {
        "quality_first", "fidelity_first", "continuity_first"
    }:
        score += 220
    if instance.is_platform_default and purpose == "media":
        score += 120
    profile = _safe_dict(quality_profile)
    minimum_samples = max(1, int(policy.get("minimum_quality_samples") or 3))
    sample_count = int(profile.get("sample_count") or 0)
    average_quality = profile.get("average_quality")
    if sample_count >= minimum_samples and isinstance(average_quality, (int, float)):
        confidence = min(1.0, sample_count / 8)
        quality_weight = 1000 if routing_policy != "throughput_balanced" else 300
        combined = float(average_quality) * 0.8 + float(profile.get("success_rate") or 0) * 0.2
        score += round((combined - 0.5) * quality_weight * confidence)
    return score, {
        "media": media,
        "gpu": gpu,
        "bridge_version": caps.get("bridge_version"),
        "routing": {
            "production_policy_version": policy.get("version") or PRODUCTION_POLICY_VERSION,
            "production_intent": policy.get("production_intent"),
            "routing_policy": routing_policy,
            "resource_preference": (
                "standard_short_5080_primary"
                if standard and mode != "reference_replay"
                else "pro6000_reference_long_batch_primary"
            ),
            "quality_profile": profile,
            "score": score,
        },
    }


def _media_job_priority(*, department_id: str | None, mode: str, params: dict[str, Any]) -> int:
    """Keep department priority while letting PRO-specialized work reach the scheduler first."""
    priority = 200 if department_id == MATERIAL_WORKBENCH_DEPARTMENT_ID else 100
    if mode == "reference_replay":
        return priority + 100
    if mode == "video_enhance":
        return priority + 25
    if int(params.get("frames") or 0) > 124 or int(params.get("batch_count") or 1) > 1:
        return priority + 50
    return priority


async def _select_media_instance(
    db: AsyncSession,
    *,
    mode: str,
    params: dict[str, Any],
    project_id: str | None = None,
    production_policy: dict[str, Any] | None = None,
    allowed_instance_ids: set[str] | None = None,
    requires_product_overlay: bool = False,
) -> tuple[OpenClawInstance | None, dict[str, Any]]:
    rows = (
        await db.execute(
            select(OpenClawInstance)
            .where(OpenClawInstance.is_active.is_(True))
            .where(OpenClawInstance.agent_purpose.in_(["media", "mixed"]))
        )
    ).scalars().all()
    active_counts = {
        str(instance_id): int(count or 0)
        for instance_id, count in (
            await db.execute(
                select(MediaGenerationAttempt.instance_id, func.count(MediaGenerationAttempt.id))
                .join(MediaGenerationJob, MediaGenerationJob.id == MediaGenerationAttempt.job_id)
                .where(MediaGenerationAttempt.instance_id.is_not(None))
                .where(MediaGenerationAttempt.attempt_no == MediaGenerationJob.current_attempt_no)
                .where(MediaGenerationJob.status.in_(["assigned", "running", "collecting"]))
                .group_by(MediaGenerationAttempt.instance_id)
            )
        ).all()
    }
    eligible = []
    for row in rows:
        if allowed_instance_ids and row.id not in allowed_instance_ids:
            continue
        value = _score_media_instance(
            row,
            mode=mode,
            params=params,
            active_jobs=active_counts.get(row.id, 0),
            production_policy=production_policy,
            requires_product_overlay=requires_product_overlay,
        )
        if value:
            eligible.append(row)
    if not eligible:
        return None, {}
    policy = _safe_dict(production_policy)
    profiles: dict[str, dict[str, Any]] = {}
    if project_id and len(eligible) > 1 and policy.get("routing_policy") != "throughput_balanced":
        profiles = await _recent_media_quality_profiles(
            db,
            project_id=project_id,
            production_intent=_text(policy.get("production_intent"), 50) or "abstract_broll",
            params=params,
        )
    scored = []
    for row in eligible:
        value = _score_media_instance(
            row,
            mode=mode,
            params=params,
            active_jobs=active_counts.get(row.id, 0),
            production_policy=policy,
            quality_profile=profiles.get(row.id),
            requires_product_overlay=requires_product_overlay,
        )
        if value:
            scored.append((value[0], row, value[1]))
    if not scored:
        return None, {}
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return scored[0][1], scored[0][2]


def _reference_asset_kind(asset: ProjectRunAsset) -> str:
    mime = str(asset.mime_type or "").lower()
    return mime.split("/", 1)[0] if "/" in mime else ""


def _reference_duration_seconds(asset: ProjectRunAsset) -> float | None:
    metadata = _safe_dict(asset.metadata_json)
    probe = _safe_dict(metadata.get("media_probe"))
    if probe.get("source") not in {"server_ffprobe", "server_mp4_parser"} or probe.get("status") != "ok":
        return None
    try:
        duration = float(probe.get("duration_seconds"))
    except (TypeError, ValueError):
        return None
    return duration if duration > 0 else None


def _probe_product_overlay_transparency(path: Path) -> dict[str, Any]:
    """Verify that a governed product overlay has a real, non-empty alpha plane.

    A .png/.webp suffix does not prove transparency.  Passing an opaque image
    to ffmpeg's overlay filter creates a visible rectangle and only fails much
    later in review.  The control plane probes it before a job is created; the
    Bridge repeats the check after downloading the signed asset.
    """

    ffmpeg = _media_quality_ffmpeg_executable()
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            "alphaextract,signalstats,metadata=print:file=-",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"

    def signal(name: str) -> float | None:
        match = re.search(rf"lavfi\.signalstats\.{name}=([0-9.]+)", output)
        return float(match.group(1)) if match else None

    alpha_min = signal("YMIN")
    alpha_max = signal("YMAX")
    passed = bool(
        completed.returncode == 0
        and alpha_min is not None
        and alpha_max is not None
        and alpha_min < 255
        and alpha_max > 0
    )
    detail = "verified_non_empty_alpha_plane" if passed else (
        "image_has_no_transparent_pixels"
        if completed.returncode == 0 and alpha_min is not None and alpha_min >= 255
        else "image_has_no_visible_subject"
        if completed.returncode == 0 and alpha_max is not None and alpha_max <= 0
        else "alpha_plane_unavailable"
    )
    return {
        "policy_version": MEDIA_PRODUCT_OVERLAY_ALPHA_POLICY_VERSION,
        "passed": passed,
        "alpha_min": alpha_min,
        "alpha_max": alpha_max,
        "detail": detail,
    }


async def _validated_product_overlay_transparency(
    db: AsyncSession, asset: ProjectRunAsset
) -> dict[str, Any]:
    metadata = _safe_dict(asset.metadata_json)
    cached = _safe_dict(metadata.get("product_overlay_transparency_probe"))
    asset_sha = _text(getattr(asset, "sha256", None), 128)
    if not (
        cached.get("policy_version") == MEDIA_PRODUCT_OVERLAY_ALPHA_POLICY_VERSION
        and _text(cached.get("sha256"), 128) == asset_sha
    ):
        from app.projects.service import _project_run_asset_abs_path

        probe = await asyncio.to_thread(
            _probe_product_overlay_transparency,
            _project_run_asset_abs_path(asset),
        )
        cached = {**probe, "sha256": asset_sha}
        asset.metadata_json = {**metadata, "product_overlay_transparency_probe": cached}
        await db.flush()
    if cached.get("passed") is not True:
        raise AppError(
            "MEDIA_PRODUCT_OVERLAY_TRANSPARENCY_REQUIRED",
            422,
            {
                "asset_id": asset.id,
                "detail": (
                    "所选商品图没有可验证的透明背景，不能直接植入视频。"
                    "请上传已抠图的透明 PNG/WebP；系统不会把白底图冒充透明图，也不会改由 H3 重绘包装。"
                ),
                "probe": cached,
            },
        )
    return cached


def _normalize_reference_requests(raw_items: list[Any], *, mode: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, dict):
            asset_id = _text(item.get("asset_id") or item.get("id"), 50)
            role = _text(item.get("role"), 40)
            business_role = _text(item.get("business_role"), 40)
            purpose = _text(item.get("purpose") or item.get("description"), 500)
        else:
            asset_id = _text(item, 50)
            role = ""
            business_role = ""
            purpose = ""
        if asset_id:
            result.append({"asset_id": asset_id, "role": role, "business_role": business_role, "purpose": purpose})
    if mode == "text_to_video" and any(item["role"] != "overlay_image" for item in result):
        raise AppError("MEDIA_REFERENCE_NOT_ALLOWED", 422, {"mode": mode})
    return result


async def _validated_reference_requests(
    db: AsyncSession,
    run: ProjectRun,
    requests: list[dict[str, Any]],
    *,
    mode: str,
) -> list[dict[str, Any]]:
    if len(requests) > H3_REFERENCE_MAX_TOTAL:
        raise AppError("MEDIA_REFERENCE_LIMIT", 422, {"total": len(requests), "max_total": H3_REFERENCE_MAX_TOTAL})
    result: list[dict[str, Any]] = []
    counts = {"image": 0, "video": 0, "audio": 0}
    duration_totals = {"video": 0.0, "audio": 0.0}
    seen: set[str] = set()
    for request in requests:
        asset_id = _text(request.get("asset_id"), 50)
        if not asset_id or asset_id in seen:
            raise AppError("MEDIA_REFERENCE_INVALID", 422, {"asset_id": asset_id, "detail": "duplicate or missing asset"})
        seen.add(asset_id)
        asset = await db.get(ProjectRunAsset, asset_id)
        same_run = bool(asset and getattr(asset, "project_run_id", None) == getattr(run, "id", None))
        same_project_department = bool(
            asset
            and getattr(asset, "project_id", None)
            and getattr(run, "project_id", None)
            and asset.project_id == run.project_id
            and not (
                getattr(asset, "department_id", None)
                and getattr(run, "department_id", None)
                and asset.department_id != run.department_id
            )
        )
        if not asset or not (same_run or same_project_department):
            raise AppError("PROJECT_ASSET_NOT_FOUND", 404, {"asset_id": asset_id})
        kind = _reference_asset_kind(asset)
        if kind not in counts:
            raise AppError("MEDIA_REFERENCE_TYPE_INVALID", 422, {"asset_id": asset_id, "mime_type": asset.mime_type})
        role = _text(request.get("role"), 40)
        if not role:
            role = "first_frame" if mode == "image_to_video" else f"reference_{kind}"
        role_kind = "image" if role == "overlay_image" else H3_REFERENCE_ROLES.get(role)
        if role_kind != kind:
            raise AppError(
                "MEDIA_REFERENCE_ROLE_INVALID",
                422,
                {"asset_id": asset_id, "role": role, "mime_type": asset.mime_type},
            )
        max_bytes = 512 * 1024 * 1024 if mode in {"video_enhance", "video_local_edit"} and kind == "video" else H3_REFERENCE_MAX_BYTES[kind]
        if int(asset.byte_size or 0) > max_bytes:
            raise AppError(
                "MEDIA_REFERENCE_TOO_LARGE",
                422,
                {"asset_id": asset_id, "kind": kind, "max_bytes": max_bytes},
            )
        if role == "overlay_image":
            business_role = _text(request.get("business_role"), 40)
            if business_role not in {"product_packshot", "product_detail"}:
                raise AppError(
                    "MEDIA_REFERENCE_ROLE_INVALID",
                    422,
                    {"detail": "overlay_image is restricted to governed product packshot/detail assets"},
                )
            if str(asset.mime_type or "").lower() not in {"image/png", "image/webp"}:
                raise AppError(
                    "MEDIA_PRODUCT_OVERLAY_TRANSPARENCY_REQUIRED",
                    422,
                    {
                        "asset_id": asset_id,
                        "detail": "商品确定性植入只接受带透明通道的 PNG/WebP，不接受 JPEG 或其他图片格式。",
                    },
                )
            await _validated_product_overlay_transparency(db, asset)
        counts[kind] += 1
        if counts[kind] > H3_REFERENCE_LIMITS[kind]:
            raise AppError("MEDIA_REFERENCE_LIMIT", 422, {"kind": kind, "count": counts[kind]})
        duration = None
        if kind in duration_totals:
            duration = _reference_duration_seconds(asset)
            if duration is None:
                from app.projects.service import _probe_project_media_file, _project_run_asset_abs_path

                probe = await asyncio.to_thread(
                    _probe_project_media_file,
                    _project_run_asset_abs_path(asset),
                    asset.mime_type,
                )
                if probe:
                    asset.metadata_json = {**_safe_dict(asset.metadata_json), "media_probe": probe}
                    await db.flush()
                    duration = _reference_duration_seconds(asset)
            if duration is None:
                raise AppError(
                    "MEDIA_REFERENCE_PROBE_REQUIRED",
                    422,
                    {"asset_id": asset_id, "detail": "video/audio duration must be verified by server ffprobe"},
                )
            maximum_duration = 300 if mode == "video_enhance" else (60 if mode == "video_local_edit" else 15)
            if duration < 2 or duration > maximum_duration:
                raise AppError(
                    "MEDIA_REFERENCE_DURATION_INVALID",
                    422,
                    {"asset_id": asset_id, "duration_seconds": duration, "minimum": 2, "maximum": maximum_duration},
                )
            duration_totals[kind] += duration
            if duration_totals[kind] > maximum_duration + 0.001:
                raise AppError(
                    "MEDIA_REFERENCE_DURATION_LIMIT",
                    422,
                    {"kind": kind, "total_duration_seconds": round(duration_totals[kind], 3), "maximum": maximum_duration},
                )
        result.append(
            {
                "asset_id": asset.id,
                "role": role,
                "business_role": _text(request.get("business_role"), 40),
                "purpose": _text(request.get("purpose"), 500),
                "kind": kind,
                "duration_seconds": duration,
            }
        )
    generation_refs = [item for item in result if item["role"] != "overlay_image"]
    overlay_refs = [item for item in result if item["role"] == "overlay_image"]
    if any(item["business_role"] not in {"product_packshot", "product_detail"} for item in overlay_refs):
        raise AppError(
            "MEDIA_REFERENCE_ROLE_INVALID",
            422,
            {"detail": "overlay_image is restricted to governed product packshot/detail assets"},
        )
    if len(overlay_refs) > MEDIA_PRODUCT_OVERLAY_MAX_ASSETS:
        raise AppError(
            "MEDIA_REFERENCE_LIMIT",
            422,
            {
                "kind": "product_overlay",
                "count": len(overlay_refs),
                "max": MEDIA_PRODUCT_OVERLAY_MAX_ASSETS,
                "detail": "商品受控合成最多支持 1 张包装图和 1 张商品细节图。",
            },
        )
    if len(overlay_refs) == 2 and {
        item["business_role"] for item in overlay_refs
    } != {"product_packshot", "product_detail"}:
        raise AppError(
            "MEDIA_REFERENCE_ROLE_INVALID",
            422,
            {
                "detail": "双商品图合成必须恰好包含 1 张包装图和 1 张商品细节图，不能重复使用同一业务角色。",
            },
        )
    if mode == "image_to_video":
        if len(generation_refs) != 1 or generation_refs[0]["role"] != "first_frame":
            raise AppError("MEDIA_REFERENCE_INVALID", 422, {"detail": "image_to_video requires exactly one first_frame image"})
    elif mode == "reference_replay":
        if not generation_refs or any(item["role"] == "first_frame" for item in generation_refs):
            raise AppError("MEDIA_REFERENCE_INVALID", 422, {"detail": "reference_replay requires reference_* roles"})
    elif mode == "video_enhance":
        if len(generation_refs) != 1 or generation_refs[0]["role"] != "reference_video" or overlay_refs:
            raise AppError("MEDIA_REFERENCE_INVALID", 422, {"detail": "video_enhance requires exactly one reference_video"})
    elif mode == "video_local_edit":
        videos = [item for item in generation_refs if item["role"] == "reference_video"]
        images = [item for item in generation_refs if item["role"] == "reference_image"]
        if len(videos) != 1 or len(images) > 2 or overlay_refs:
            raise AppError(
                "MEDIA_REFERENCE_INVALID",
                422,
                {"detail": "video_local_edit requires one source video and at most two product reference images"},
            )
    elif generation_refs:
        raise AppError("MEDIA_REFERENCE_NOT_ALLOWED", 422, {"mode": mode})
    return result


async def _reference_payloads(db: AsyncSession, run: ProjectRun, references: list[Any], *, mode: str) -> list[dict[str, Any]]:
    from app.projects.service import _project_run_asset_signed_download_url

    normalized = _normalize_reference_requests(references, mode=mode)
    validated = await _validated_reference_requests(db, run, normalized, mode=mode)
    result = []
    counters = {"image": 0, "video": 0, "audio": 0}
    for item in validated:
        asset = await db.get(ProjectRunAsset, item["asset_id"])
        if asset is None:
            raise AppError("PROJECT_ASSET_NOT_FOUND", 404, {"asset_id": item["asset_id"]})
        counters[item["kind"]] += 1
        label = {"image": "Picture", "video": "Video", "audio": "Audio"}[item["kind"]]
        placeholder = (
            f"<Overlay Image {counters[item['kind']]}>"
            if item["role"] == "overlay_image"
            else f"<{label} {counters[item['kind']]}>"
        )
        result.append(
            {
                "asset_id": asset.id,
                "file_name": asset.file_name,
                "mime_type": asset.mime_type,
                "sha256": asset.sha256,
                "byte_size": asset.byte_size,
                "role": item["role"],
                "business_role": item["business_role"],
                "purpose": item["purpose"],
                "placeholder": placeholder,
                "duration_seconds": item["duration_seconds"],
                "source_url": _project_run_asset_signed_download_url(asset, ttl_seconds=3600),
            }
        )
    return result


async def _latest_attempt(db: AsyncSession, job_id: str) -> MediaGenerationAttempt | None:
    return (
        await db.execute(
            select(MediaGenerationAttempt)
            .where(MediaGenerationAttempt.job_id == job_id)
            .order_by(MediaGenerationAttempt.attempt_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _rolling_eta_seconds(
    db: AsyncSession,
    *,
    instance_id: str,
    mode: str,
    params: dict[str, Any],
    queue_depth: int,
) -> int | None:
    rows = (
        await db.execute(
            select(MediaGenerationAttempt, MediaGenerationJob)
            .join(MediaGenerationJob, MediaGenerationJob.id == MediaGenerationAttempt.job_id)
            .where(MediaGenerationAttempt.instance_id == instance_id)
            .where(MediaGenerationAttempt.status == "completed")
            .order_by(MediaGenerationAttempt.completed_at.desc())
            .limit(100)
        )
    ).all()
    preset_keys = ("width", "height", "frames", "fps", "steps", "batch_count", "audio_enabled")
    durations: list[float] = []
    for attempt, historic_job in rows:
        if historic_job.mode != mode:
            continue
        historic_params = historic_job.params_json or {}
        if any(historic_params.get(key) != params.get(key) for key in preset_keys):
            continue
        metrics = attempt.metrics_json or {}
        raw = metrics.get("total_seconds")
        if raw is None and metrics.get("duration_ms") is not None:
            raw = float(metrics["duration_ms"]) / 1000
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            durations.append(value)
        if len(durations) >= 20:
            break
    if not durations:
        return None
    durations.sort()
    midpoint = len(durations) // 2
    median = durations[midpoint] if len(durations) % 2 else (durations[midpoint - 1] + durations[midpoint]) / 2
    return max(1, round(median * (max(queue_depth, 0) + 1)))


async def _dispatch_job(db: AsyncSession, run: ProjectRun, job: MediaGenerationJob) -> None:
    allowed_instance_ids: set[str] | None = None
    production_batch_id = getattr(job, "production_batch_id", None)
    if production_batch_id:
        batch = await db.get(MediaProductionBatch, production_batch_id)
        if batch:
            if batch.status in {"paused", "cancelled", "deadline_reached"}:
                job.error = "生产批次当前暂停或已停止，任务不会派单。"
                job.updated_at = now_bjt()
                return
            allowed_instance_ids = {str(item) for item in _safe_list(batch.allowed_nodes_json) if str(item).strip()} or None
    production_policy = _job_production_policy(job)
    requires_product_overlay = any(
        str(_safe_dict(item).get("role") or "") == "overlay_image"
        for item in _safe_list(getattr(job, "reference_assets_json", None))
    )
    instance, snapshot = await _select_media_instance(
        db,
        mode=job.mode,
        params=job.params_json or {},
        project_id=getattr(job, "project_id", None),
        production_policy=production_policy,
        allowed_instance_ids=allowed_instance_ids,
        requires_product_overlay=requires_product_overlay,
    )
    if instance is None:
        # A busy single-capacity GPU is an expected queue condition, not a new
        # failure on every scheduler pass. Persist the explanation once so a
        # long campaign does not churn the same rows every two seconds.
        if job.error != MEDIA_QUEUE_WAIT_MESSAGE:
            job.error = MEDIA_QUEUE_WAIT_MESSAGE
            job.updated_at = now_bjt()
        return
    plan = await db.get(MediaPlanComparison, job.plan_comparison_id) if job.plan_comparison_id else None
    _upgrade_queued_campaign_prompt(job, brief=_safe_dict(plan.brief_json) if plan else {})
    references = await _reference_payloads(db, run, job.reference_assets_json or [], mode=job.mode)
    attempt_no = int(job.current_attempt_no or 0) + 1
    attempt = MediaGenerationAttempt(
        job_id=job.id,
        attempt_no=attempt_no,
        instance_id=instance.id,
        status="assigned",
        capability_snapshot_json=snapshot,
        assigned_at=now_bjt(),
    )
    db.add(attempt)
    job.current_attempt_no = attempt_no
    job.assigned_instance_id = instance.id
    eta_seconds = await _rolling_eta_seconds(
        db,
        instance_id=instance.id,
        mode=job.mode,
        params=job.params_json or {},
        queue_depth=int(_safe_dict(snapshot.get("media")).get("queue_depth") or 0),
    )
    job.result_json = {
        **(job.result_json or {}),
        "production_policy": production_policy,
        "routing_decision": {
            **_safe_dict(snapshot.get("routing")),
            "instance_id": instance.id,
            "instance_name": instance.name,
            "selected_at": isoformat_bjt(now_bjt()),
        },
        "eta": {
            "seconds": eta_seconds,
            "source": "rolling_same_preset" if eta_seconds is not None else "awaiting_first_benchmark",
            "instance_id": instance.id,
        },
    }
    transition_media_job(job, "assigned")
    await db.flush()
    await db.commit()
    try:
        response = await AIClawClient(instance.id, gateway_kind=instance.bridge_gateway_kind).submit_media_job(
            {
                "job_id": job.id,
                "idempotency_key": job.idempotency_key,
                "attempt_no": attempt_no,
                "template_id": job.workflow_template_id,
                "mode": job.mode,
                "prompt": job.prompt_json or {},
                "params": job.params_json or {},
                "references": references,
            },
            timeout=120,
        )
        attempt.bridge_job_id = _text(response.get("prompt_id") or response.get("job_id"), 120) or job.id
        attempt.status = "running"
        attempt.started_at = now_bjt()
        attempt.metrics_json = _safe_dict(response.get("metrics"))
        job.workflow_version = _text(response.get("workflow_version"), 80) or job.workflow_version
        job.model_version = _text(response.get("model_version"), 180) or job.model_version
        job.model_sha256 = _text(response.get("model_sha256"), 64) or job.model_sha256
        transition_media_job(job, "running")
    except Exception as exc:  # noqa: BLE001
        attempt.status = "failed"
        attempt.error = _text(getattr(exc, "detail", None) or exc, 4000)
        attempt.completed_at = now_bjt()
        transition_media_job(job, "queued", error="节点提交失败，已退回队列等待换机：" + attempt.error)
        job.assigned_instance_id = None


def _upgrade_queued_campaign_prompt(
    job: MediaGenerationJob,
    *,
    brief: dict[str, Any] | None = None,
) -> bool:
    """Upgrade legacy continuous candidates immediately before GPU dispatch.

    Campaigns can remain queued across a prompt-policy deployment.  Rebuilding
    hundreds of jobs would change their idempotency and billing lineage, so the
    scheduler deterministically upgrades only the executable prompt while the
    job is still queued.  Job id, seed, campaign deadline and idempotency key
    remain unchanged.
    """

    prompt = _safe_dict(job.prompt_json)
    campaign = _safe_dict(prompt.get("production_campaign"))
    campaign_id = _text(campaign.get("id") or campaign.get("campaign_id"), 80)
    try:
        candidate_index = int(campaign.get("candidate_index") or 0)
    except (TypeError, ValueError):
        candidate_index = 0
    if not campaign_id or candidate_index < 1:
        return False
    variation_policy = _text(campaign.get("variation_policy"), 40) or "layout_v1"
    seed_only = variation_policy == "seed_only"
    expected_variant = {} if seed_only else production_variant_for_candidate(candidate_index, campaign_id)
    if (
        prompt.get("prompt_policy_version") == H3_PROMPT_POLICY_VERSION
        and (
            (seed_only and not _safe_dict(prompt.get("production_variant")))
            or (not seed_only and _safe_dict(prompt.get("production_variant")) == expected_variant)
        )
    ):
        return False
    previous_version = _text(prompt.get("prompt_policy_version"), 80) or "legacy"
    candidate = {
        **{
            key: value
            for key, value in prompt.items()
            if not seed_only
            or key not in {"production_variant", "production_variant_application", "prompt_policy_upgrade"}
        },
        **({"production_variant": expected_variant} if not seed_only else {}),
        "prompt_policy_upgrade": {
            "from": previous_version,
            "to": H3_PROMPT_POLICY_VERSION,
            "reason": "queued_campaign_dispatch_upgrade",
            "variation_policy": variation_policy,
        },
    }
    try:
        upgraded = compile_h3_plan(candidate, brief=_safe_dict(brief))
    except H3PromptPolicyError as exc:
        logger.warning(
            "排队素材提示词升级失败，保留原提示词 job={} field={} err={}",
            job.id,
            exc.field,
            exc.message,
        )
        return False
    job.prompt_json = upgraded
    job.result_json = {
        **(job.result_json or {}),
        "prompt_upgrade": {
            "status": "completed",
            "from": previous_version,
            "to": H3_PROMPT_POLICY_VERSION,
            "variant_key": expected_variant.get("variant_key") or None,
            "variation_policy": variation_policy,
            "upgraded_at": isoformat_bjt(now_bjt()),
        },
    }
    return True


def _apply_audio_delivery_gate(params: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Keep H3 visual jobs silent when dialogue requires a separate renderer.

    The UI is only advisory.  This server-side gate prevents stale clients or
    edited payloads from re-enabling joint H3 dialogue after the deterministic
    compiler has marked the clean plate as video-only.
    """

    normalized = dict(params)
    delivery = _safe_dict(plan.get("dialogue_delivery"))
    if delivery.get("visual_audio_enabled") is False:
        normalized["audio_enabled"] = False
    return normalized


async def _collect_result_bytes(
    client: AIClawClient,
    job: MediaGenerationJob,
    meta: dict[str, Any],
    *,
    result_index: int = 0,
) -> bytes:
    size = int(meta.get("byte_size") or 0)
    expected_sha = _text(meta.get("sha256"), 64)
    if size <= 0 or size > MEDIA_MAX_RESULT_BYTES:
        raise AppError("MEDIA_RESULT_INVALID", 422, {"byte_size": size, "max_bytes": MEDIA_MAX_RESULT_BYTES})
    chunks: list[bytes] = []
    offset = 0
    hasher = hashlib.sha256()
    while offset < size:
        response = await client.read_media_result_chunk(
            {
                "job_id": job.id,
                "result_index": result_index,
                "offset": offset,
                "limit": min(MEDIA_RESULT_CHUNK_BYTES, size - offset),
            },
            timeout=120,
        )
        raw = base64.b64decode(str(response.get("content_base64") or ""), validate=True)
        if not raw or int(response.get("offset") or 0) != offset:
            raise AppError("MEDIA_RESULT_INVALID", 502, {"detail": "Bridge result chunk is missing or out of order"})
        chunks.append(raw)
        hasher.update(raw)
        offset += len(raw)
    if offset != size or (expected_sha and hasher.hexdigest() != expected_sha):
        raise AppError("MEDIA_RESULT_HASH_MISMATCH", 502)
    return b"".join(chunks)


def _merge_collected_media_result(
    existing: dict[str, Any] | None,
    first_meta: dict[str, Any],
    *,
    primary_asset: dict[str, Any],
    assets: list[dict[str, Any]],
    result_metas: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep dispatch lineage while replacing live ETA with collected output."""

    return {
        **_safe_dict(existing),
        **_safe_dict(first_meta),
        "asset": primary_asset,
        "assets": assets,
        "results": result_metas,
    }


def _generated_media_technical_validation(
    probe: dict[str, Any] | None,
    params: dict[str, Any] | None,
) -> dict[str, Any]:
    probe_data = _safe_dict(probe)
    params_data = _safe_dict(params)
    if probe_data.get("status") != "ok":
        return {
            "status": "unverified",
            "issues": [_text(probe_data.get("detail"), 500) or "媒体节点未返回可用的 ffprobe 结果"],
        }
    streams = [item for item in _safe_list(probe_data.get("streams")) if isinstance(item, dict)]
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    issues: list[str] = []
    if video is None:
        issues.append("生成结果缺少视频流")
    else:
        expected_width = int(params_data.get("width") or 0)
        expected_height = int(params_data.get("height") or 0)
        actual_width = int(video.get("width") or 0)
        actual_height = int(video.get("height") or 0)
        if expected_width and expected_height and (actual_width, actual_height) != (expected_width, expected_height):
            issues.append(
                f"分辨率不匹配：期望 {expected_width}x{expected_height}，实际 {actual_width}x{actual_height}"
            )
    try:
        duration = float(probe_data.get("duration_seconds") or 0)
        expected_duration = float(
            params_data.get("delivery_duration_seconds")
            or (float(params_data.get("frames") or 0) / max(float(params_data.get("fps") or 0), 1))
        )
    except (TypeError, ValueError, ZeroDivisionError):
        duration = 0
        expected_duration = 0
    if duration <= 0:
        issues.append("无法确认生成视频时长")
    elif expected_duration and abs(duration - expected_duration) > max(0.5, expected_duration * 0.1):
        issues.append(f"时长不匹配：期望约 {expected_duration:.3f} 秒，实际 {duration:.3f} 秒")
    if bool(params_data.get("audio_enabled", True)) and audio is None:
        issues.append("启用同步音频但生成结果缺少音频流")
    return {
        "status": "failed" if issues else "passed",
        "issues": issues,
        "duration_seconds": duration,
        "video_codec": _text((video or {}).get("codec_name"), 60),
        "audio_codec": _text((audio or {}).get("codec_name"), 60),
    }


def _media_quality_asset_refs(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    data = _safe_dict(result)
    refs = [item for item in _safe_list(data.get("assets")) if isinstance(item, dict)]
    if not refs and isinstance(data.get("asset"), dict):
        refs = [_safe_dict(data.get("asset"))]
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in refs[:8]:
        asset_id = _text(item.get("id") or item.get("asset_id"), 50)
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        deduped.append(item)
    return deduped


def _media_quality_asset_fingerprint(result: dict[str, Any] | None) -> str:
    refs = _media_quality_asset_refs(result)
    if not refs:
        return ""
    material = "|".join(
        f"{_text(item.get('id') or item.get('asset_id'), 50)}:{_text(item.get('sha256'), 64)}"
        for item in refs
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _queue_media_quality_analysis(
    result: dict[str, Any] | None,
    *,
    requested_by: str | None,
    automatic: bool,
    force: bool = False,
) -> tuple[dict[str, Any], bool]:
    data = _safe_dict(result)
    fingerprint = _media_quality_asset_fingerprint(data)
    if not fingerprint:
        return data, False
    existing = _safe_dict(data.get("quality_analysis"))
    if (
        not force
        and existing.get("asset_fingerprint") == fingerprint
        and existing.get("policy_version") == MEDIA_QUALITY_ANALYSIS_POLICY_VERSION
        and existing.get("status") in {"queued", "running", "completed", "partial"}
    ):
        return data, False
    history = [item for item in _safe_list(existing.get("history")) if isinstance(item, dict)][-2:]
    if existing.get("status") in {"completed", "partial", "failed"}:
        history.append({key: value for key, value in existing.items() if key != "history"})
    queued = {
        "id": f"mqa-{uuid4().hex}",
        "status": "queued",
        "policy_version": MEDIA_QUALITY_ANALYSIS_POLICY_VERSION,
        "asset_fingerprint": fingerprint,
        "requested_by": requested_by,
        "requested_at": isoformat_bjt(now_bjt()),
        "automatic": bool(automatic),
        "history": history[-3:],
    }
    return {**data, "quality_analysis": queued}, True


def _normalize_ai_media_quality_result(value: Any) -> dict[str, Any]:
    raw = _safe_dict(value)
    hard_gate_evaluated = isinstance(raw.get("hard_gate_findings"), list)
    raw_scores = _safe_dict(raw.get("scores") or raw.get("quality_scores"))
    scores: dict[str, int] = {}
    missing: list[str] = []
    for key in MEDIA_QUALITY_DIMENSION_KEYS:
        try:
            score = int(raw_scores.get(key))
        except (TypeError, ValueError):
            score = 0
        if not 1 <= score <= 5:
            missing.append(key)
        else:
            scores[key] = score
    if missing:
        raise ValueError("quality_scores_missing:" + ",".join(missing))

    def quality_item_text(item: Any) -> str:
        if not isinstance(item, dict):
            return _text(item, 1200)
        message = _text(
            item.get("issue")
            or item.get("message")
            or item.get("summary")
            or item.get("description")
            or item.get("observation"),
            600,
        )
        evidence = _text(item.get("evidence"), 600)
        impact = _text(item.get("impact"), 300)
        parts = [message]
        if evidence and evidence not in message:
            parts.append("证据：" + evidence)
        if impact and impact not in message:
            parts.append("影响：" + impact)
        return "；".join(part for part in parts if part)

    def text_list(key: str, limit: int = 12) -> list[str]:
        return [text for item in _safe_list(raw.get(key))[:limit] if (text := quality_item_text(item))]

    timeline = []
    for item in _safe_list(raw.get("timeline"))[:20]:
        if isinstance(item, dict):
            timeline.append({
                "time": _text(item.get("time") or item.get("range"), 80),
                "observation": _text(item.get("observation") or item.get("description"), 800),
                "evidence": _text(item.get("evidence"), 800),
            })
        elif _text(item, 800):
            timeline.append({"observation": _text(item, 800)})
    recommendation = raw.get("recommendation")
    if isinstance(recommendation, dict):
        normalized_recommendation: dict[str, Any] | str = {
            "decision": _text(recommendation.get("decision"), 80),
            "reason": _text(recommendation.get("reason"), 1000),
            "prompt_changes": [
                _text(item, 600) for item in _safe_list(recommendation.get("prompt_changes"))[:12] if _text(item, 600)
            ],
        }
    else:
        normalized_recommendation = _text(recommendation, 1200)
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0.0
    hard_gate_findings: list[dict[str, Any]] = []
    for item in _safe_list(raw.get("hard_gate_findings"))[:12]:
        if not isinstance(item, dict):
            continue
        code = _text(item.get("code"), 80)
        if code not in {
            "visible_text_detected",
            "interpersonal_contact_detected",
            "duplicate_actor_identity_detected",
            "synchronized_performance_detected",
            "actor_count_mismatch_detected",
            "cast_market_mismatch_detected",
            "synthetic_face_style_detected",
            "unexpected_person_detected",
        }:
            continue
        try:
            finding_confidence = max(0.0, min(1.0, float(item.get("confidence") or 0)))
        except (TypeError, ValueError):
            finding_confidence = 0.0
        frame_indices = []
        for value in _safe_list(item.get("frame_indices"))[:8]:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if index > 0:
                frame_indices.append(index)
        normalized_finding = {
            "code": code,
            "confidence": finding_confidence,
            "frame_indices": list(dict.fromkeys(frame_indices)),
            "visible_text": _text(item.get("visible_text"), 300),
            "contact_regions": _text(item.get("contact_regions"), 300),
            "identity_similarity": _text(item.get("identity_similarity"), 500),
            "synchronized_actions": _text(item.get("synchronized_actions"), 500),
            "evidence": _text(item.get("evidence"), 800),
        }
        if code == "visible_text_detected":
            reference_image_indices = []
            for value in _safe_list(item.get("reference_image_indices"))[:3]:
                try:
                    index = int(value)
                except (TypeError, ValueError):
                    continue
                if index > 0:
                    reference_image_indices.append(index)
            normalized_finding.update({
                "surface_region": _text(item.get("surface_region"), 160),
                "reference_comparison": _text(item.get("reference_comparison"), 80),
                "reference_visible_text": _text(item.get("reference_visible_text"), 500),
                "reference_image_indices": list(dict.fromkeys(reference_image_indices)),
            })
        elif code == "actor_count_mismatch_detected":
            normalized_finding.update({
                "expected_actor_count": item.get("expected_actor_count"),
                "observed_actor_count": item.get("observed_actor_count"),
            })
        elif code == "cast_market_mismatch_detected":
            normalized_finding.update({
                "expected_cast_market": _text(item.get("expected_cast_market"), 80),
                "observed_market_cues": _text(item.get("observed_market_cues"), 500),
            })
        elif code == "synthetic_face_style_detected":
            normalized_finding["face_artifacts"] = _text(item.get("face_artifacts"), 500)
        elif code == "unexpected_person_detected":
            try:
                observed_actor_count = max(0, int(item.get("observed_actor_count") or 0))
            except (TypeError, ValueError):
                observed_actor_count = 0
            normalized_finding.update({
                "observed_actor_count": observed_actor_count,
                "person_regions": _text(item.get("person_regions"), 500),
            })
        hard_gate_findings.append(normalized_finding)
    reference_text_inventory = []
    for item in _safe_list(raw.get("reference_text_inventory"))[:12]:
        if not isinstance(item, dict):
            continue
        try:
            inventory_confidence = max(0.0, min(1.0, float(item.get("confidence") or 0)))
        except (TypeError, ValueError):
            inventory_confidence = 0.0
        try:
            reference_image_index = max(0, int(item.get("reference_image_index") or 0))
        except (TypeError, ValueError):
            reference_image_index = 0
        reference_text_inventory.append({
            "reference_image_index": reference_image_index,
            "surface_region": _text(item.get("surface_region"), 160),
            "visible_text": _text(item.get("visible_text"), 800),
            "confidence": inventory_confidence,
        })
    return {
        "summary": _text(raw.get("summary"), 1600),
        "timeline": timeline,
        "scores": scores,
        "overall_score": round(sum(scores.values()) / (len(MEDIA_QUALITY_DIMENSION_KEYS) * 5), 3),
        "strengths": text_list("strengths"),
        "issues": text_list("issues"),
        "evidence": text_list("evidence", limit=20),
        "recommendation": normalized_recommendation,
        "confidence": confidence,
        "hard_gate_findings": hard_gate_findings,
        "reference_text_inventory": reference_text_inventory,
        "hard_gate_evaluated": hard_gate_evaluated,
        "source": "ai_advisory_only",
        "requires_human_review": True,
    }


def _normalize_people_presence_verifier(value: Any, *, frame_count: int) -> dict[str, Any]:
    """Normalize the independent no-person visual gate response.

    The broad quality model can correctly describe a face while forgetting to
    emit the corresponding hard-gate code.  This verifier has one job only:
    decide whether an output frame contains a person.  A low-confidence or
    incomplete response is rejected instead of being treated as a clean pass.
    """

    raw = _safe_dict(value)
    if not isinstance(raw.get("person_present"), bool):
        raise ValueError("people_presence_person_present_missing")
    person_present = raw["person_present"] is True
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.9:
        raise ValueError("people_presence_confidence_too_low")

    frame_indices: list[int] = []
    for value in _safe_list(raw.get("frame_indices"))[:MEDIA_QUALITY_ANALYSIS_MAX_FRAMES]:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= max(1, int(frame_count)):
            frame_indices.append(index)
    frame_indices = list(dict.fromkeys(frame_indices))
    try:
        observed_actor_count = max(0, int(raw.get("observed_actor_count") or 0))
    except (TypeError, ValueError):
        observed_actor_count = 0
    person_regions = _text(raw.get("person_regions"), 500)
    evidence = _text(raw.get("evidence"), 800)
    if not evidence:
        raise ValueError("people_presence_evidence_missing")
    if person_present and (not frame_indices or observed_actor_count < 1 or not person_regions):
        raise ValueError("people_presence_positive_evidence_incomplete")

    return {
        "status": "completed",
        "person_present": person_present,
        "confidence": confidence,
        "frame_indices": frame_indices if person_present else [],
        "observed_actor_count": observed_actor_count if person_present else 0,
        "person_regions": person_regions if person_present else "",
        "evidence": evidence,
        "source": "independent_people_presence_verifier",
    }


def _merge_people_presence_verifier(
    analysis: dict[str, Any],
    verifier: dict[str, Any],
) -> dict[str, Any]:
    merged = {**analysis, "people_presence_verifier": verifier}
    if verifier.get("person_present") is not True:
        return merged
    findings = [_safe_dict(item) for item in _safe_list(analysis.get("hard_gate_findings"))]
    if not any(item.get("code") == "unexpected_person_detected" for item in findings):
        findings.append({
            "code": "unexpected_person_detected",
            "confidence": float(verifier.get("confidence") or 0),
            "frame_indices": _safe_list(verifier.get("frame_indices")),
            "observed_actor_count": int(verifier.get("observed_actor_count") or 0),
            "person_regions": _text(verifier.get("person_regions"), 500),
            "evidence": _text(verifier.get("evidence"), 800),
            "detector_source": "independent_people_presence_verifier",
        })
    merged["hard_gate_findings"] = findings
    return merged


def _media_quality_ffmpeg_executable() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        bundled = str(imageio_ffmpeg.get_ffmpeg_exe() or "").strip()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("视频质量分析缺少 ffmpeg 关键帧提取器") from exc
    if not bundled or not Path(bundled).is_file():
        raise RuntimeError("视频质量分析缺少 ffmpeg 关键帧提取器")
    return bundled


def _apply_ai_media_quality_guardrails(
    analysis: dict[str, Any],
    job: MediaGenerationJob,
    review_annotations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    guarded = {**analysis}
    original_scores = {key: int(value) for key, value in _safe_dict(analysis.get("scores")).items() if key in MEDIA_QUALITY_DIMENSION_KEYS}
    scores = dict(original_scores)
    rules: list[str] = []
    raw_issues = [_text(item, 600) for item in _safe_list(analysis.get("issues")) if _text(item, 600)]
    issues: list[str] = []
    audio_claim_softened = False
    dialogue_transcription = _safe_dict(
        _safe_dict(_safe_dict(job.result_json).get("dialogue_delivery")).get("transcription")
    )
    dialogue_transcription_verified = dialogue_transcription.get("passed") is True
    audio_subjects = ("口播", "台词", "语音", "声音", "说出", "朗读")
    missing_markers = ("缺失", "未出现", "未能", "不完整", "没有", "未展示", "未呈现")
    for issue in raw_issues:
        if any(subject in issue for subject in audio_subjects) and any(marker in issue for marker in missing_markers):
            if dialogue_transcription_verified:
                rules.append("verified_asr_overrides_visual_audio_claim")
                continue
            audio_claim_softened = True
            issues.append("音频内容尚未自动识别；关键帧不能证明口播或台词是否缺失，需由审片人实际听审。")
        else:
            issues.append(issue)
    reference_roles = {
        _text(item.get("business_role") or item.get("role"), 40)
        for item in _safe_list(job.reference_assets_json)
        if isinstance(item, dict)
    }
    has_product_image = bool(reference_roles & {"product_packshot", "product_detail"})
    prompt = _safe_dict(job.prompt_json)
    execution_profile = _text(prompt.get("h3_execution_profile"), 120)
    ad_material_contract = _safe_dict(prompt.get("ad_material_contract"))
    brand_guardrails = _safe_dict(prompt.get("brand_guardrails"))
    approved_surface_text_policy = (
        _text(brand_guardrails.get("reference_text_policy"), 80)
        == "suppress_generated_text_preserve_approved_product_surface"
    )
    reference_text_inventory = [
        _safe_dict(item)
        for item in _safe_list(analysis.get("reference_text_inventory"))
        if _text(_safe_dict(item).get("visible_text"), 800)
    ]

    def text_fingerprint(value: Any) -> str:
        return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", _text(value, 1200).casefold())

    inventory_fingerprint = text_fingerprint(
        " ".join(_text(item.get("visible_text"), 800) for item in reference_text_inventory)
    )
    approved_surface_text_suppressed: list[str] = []
    embedded_brief = _safe_dict(prompt.get("production_brief"))
    product_overlay_enabled = _safe_dict(prompt.get("product_overlay")).get("enabled") is True
    result_data = _safe_dict(job.result_json)
    postprocess_provenance = _safe_dict(result_data.get("postprocess_provenance"))
    if not postprocess_provenance:
        postprocess_provenance = next(
            (
                _safe_dict(_safe_dict(item).get("postprocess_provenance"))
                for item in _safe_list(result_data.get("results"))
                if _safe_dict(_safe_dict(item).get("postprocess_provenance"))
            ),
            {},
        )
    expected_overlay_refs = [
        _safe_dict(item)
        for item in _safe_list(job.reference_assets_json)
        if _text(_safe_dict(item).get("role"), 40) == "overlay_image"
    ]
    expected_overlay_roles = {
        _text(item.get("business_role"), 40) for item in expected_overlay_refs
        if _text(item.get("business_role"), 40)
    }
    expected_overlay_hashes = {
        _text(item.get("sha256"), 64) for item in expected_overlay_refs
        if re.fullmatch(r"[0-9a-f]{64}", _text(item.get("sha256"), 64))
    }
    proven_overlay_roles = {
        _text(item, 40) for item in _safe_list(postprocess_provenance.get("product_overlay_roles"))
        if _text(item, 40)
    }
    proven_overlay_hashes = {
        _text(item, 64) for item in _safe_list(postprocess_provenance.get("product_overlay_sha256s"))
        if re.fullmatch(r"[0-9a-f]{64}", _text(item, 64))
    }
    governed_overlay_provenance_verified = bool(
        product_overlay_enabled
        and expected_overlay_refs
        and postprocess_provenance.get("product_overlay_applied") is True
        and expected_overlay_roles == proven_overlay_roles
        and (not expected_overlay_hashes or expected_overlay_hashes == proven_overlay_hashes)
        and re.fullmatch(
            r"[0-9a-f]{64}",
            _text(postprocess_provenance.get("product_overlay_config_sha256"), 64),
        )
    )
    guarded["product_overlay_provenance"] = {
        "verified": governed_overlay_provenance_verified,
        "roles": sorted(proven_overlay_roles),
        "asset_count": len(proven_overlay_hashes),
        "config_sha256": _text(postprocess_provenance.get("product_overlay_config_sha256"), 64),
    }
    assembly_plan = _safe_dict(ad_material_contract.get("assembly_plan"))
    commercial_brief_text = " ".join(
        _text(value, 4000)
        for value in (
            embedded_brief.get("script"),
            embedded_brief.get("request"),
            embedded_brief.get("operator_input"),
            ad_material_contract.get("executable_script"),
        )
        if _text(value, 4000)
    ).casefold()
    complete_delivery_markers = (
        "完整投流成片",
        "可直接投放",
        "直接投流",
        "点击购买",
        "立即下单",
        "赶紧下单",
        "两位数到手",
        "活动价",
        "优惠价",
        "限时优惠",
        "cta",
    )
    product_overlay_component_scope = bool(
        product_overlay_enabled
        and assembly_plan.get("commercial_preference")
        == "dynamic_clean_plate_then_deterministic_product_overlay"
        and not _text(embedded_brief.get("script"), 4000)
        and not _text(ad_material_contract.get("executable_script"), 4000)
        and not any(marker in commercial_brief_text for marker in complete_delivery_markers)
    )
    dimension_applicability = {key: True for key in MEDIA_QUALITY_DIMENSION_KEYS}
    dimension_applicability_reasons: dict[str, str] = {}
    brand_fidelity_excluded = (
        execution_profile == "clean_dialogue_plate_silent_v1"
        or (
            not product_overlay_enabled
            and brief_excludes_brand_fidelity(embedded_brief)
        )
        or (
        brand_guardrails.get("require_reference_image") is False
        and any(
            marker in _text(brand_guardrails.get("reason"), 500)
            for marker in ("不展示品牌", "无品牌", "不要求产品保真", "合成")
        )
        )
    )
    if product_overlay_component_scope:
        material_stage = {
            "id": "assembled_product_component",
            "label": "商品镜头",
            "approval_scope": "component_quality_only",
            "requires_assembly": True,
            "cloud_library_eligible": True,
            "commercial_positive_eligible": False,
            "description": "真实商品像素已完成受控合成；当前只评价单镜头画面与商品保真，仍需与钩子、卖点或转化动作组装后再评价完整投流效果。",
        }
        scores["selling_point_coverage"] = min(scores.get("selling_point_coverage", 1), 3)
        scores["commercial_readiness"] = min(scores.get("commercial_readiness", 1), 3)
        dimension_applicability["selling_point_coverage"] = False
        dimension_applicability["commercial_readiness"] = False
        dimension_applicability_reasons.update({
            "selling_point_coverage": "当前是单个商品镜头，卖点覆盖由后续投流成片组装与真实业务依据验证。",
            "commercial_readiness": "当前是可剪辑商品素材，不代表包含钩子、卖点和转化动作的完整投流成片。",
        })
        rules.append("product_component_not_complete_ad")
        guarded["scope_notes"] = [
            *_safe_list(guarded.get("scope_notes")),
            "商品包装清晰且保真，只能证明该商品镜头可用；没有完整台词、活动依据或转化动作时，不能据此判定完整投流成片已经商业就绪。",
        ]
    elif product_overlay_enabled:
        material_stage = {
            "id": "assembled_product_material",
            "label": "商品合成版",
            "approval_scope": "assembled_material_quality",
            "requires_assembly": False,
            "cloud_library_eligible": True,
            "commercial_positive_eligible": True,
            "description": "已将受控商品素材合成到生成画面；仍需审片确认包装真实性、卖点与投放合规。",
        }
    elif brand_fidelity_excluded:
        people_plate = any(
            marker in execution_profile
            for marker in ("dialogue", "people", "person")
        ) or any(
            marker in _text(embedded_brief, 4000)
            for marker in ("人物", "情侣", "口播", "对话", "医生", "女性", "男性")
        )
        material_stage = {
            "id": "component_plate",
            "label": "人物底片" if people_plate else "视觉底片",
            "approval_scope": "component_quality_only",
            "requires_assembly": True,
            "cloud_library_eligible": True,
            "commercial_positive_eligible": False,
            "description": "当前只评价底片的人物、动作、画面和声音质量；需与已审核商品、卖点或活动信息组装后再评价投流可用度。",
        }
    elif has_product_image:
        material_stage = {
            "id": "generated_product_candidate",
            "label": "商品生成候选",
            "approval_scope": "candidate_quality",
            "requires_assembly": False,
            "cloud_library_eligible": True,
            "commercial_positive_eligible": True,
            "description": "已提供商品参考图，但 AI 生成结果仍需人工核验包装、Logo、文字与商品几何真实性。",
        }
    else:
        material_stage = {
            "id": "generated_candidate",
            "label": "生成候选",
            "approval_scope": "candidate_quality",
            "requires_assembly": False,
            "cloud_library_eligible": True,
            "commercial_positive_eligible": False,
            "description": "当前没有可验证的真实商品依据；可以作为创意候选，但不能直接作为商业正样本。",
        }
    subtitle_policy = _text(ad_material_contract.get("subtitle_policy"), 80).lower()
    subtitles_forbidden = (
        execution_profile in {
            "clean_dialogue_plate_silent_v1",
            "clean_people_plate_silent_v4",
            "direct_clean_people_plate_silent_v1",
            "motion_only_no_text_ascii_v3",
            "product_replacement_no_text_ascii_v1",
        }
        or _text(brand_guardrails.get("reference_text_policy"), 80) in {
            "suppress_all_source_text",
            "suppress_generated_text_preserve_approved_product_surface",
        }
        or subtitle_policy in {
            "forbid_burned_in_text",
            "forbid_subtitles",
            "no_subtitles",
            "suppress_all_text",
        }
    )
    absence_markers = (
        "缺失",
        "未出现",
        "未展示",
        "未呈现",
        "未视觉化",
        "没有",
        "无字幕",
        "无法评估",
        "missing",
        "absent",
        "not present",
        "no caption",
        "no subtitle",
    )
    subtitle_absence_removed = False
    excluded_product_absence_removed = False
    policy_aligned_issues: list[str] = []
    for issue in issues:
        lowered = issue.casefold()
        missing_claim = any(marker in lowered for marker in absence_markers)
        if subtitles_forbidden and missing_claim and any(marker in lowered for marker in ("字幕", "caption", "subtitle")):
            subtitle_absence_removed = True
            continue
        if brand_fidelity_excluded and missing_claim and any(
            marker in lowered for marker in ("商品", "产品", "包装", "品牌", "logo", "卖点")
        ):
            excluded_product_absence_removed = True
            continue
        policy_aligned_issues.append(issue)
    issues = policy_aligned_issues
    if subtitle_absence_removed:
        rules.append("forbidden_subtitle_absence_not_an_issue")
    if excluded_product_absence_removed:
        rules.append("excluded_product_absence_not_an_issue")
    requires_product_reference = (
        brand_guardrails.get("require_reference_image") is True
        or brand_guardrails.get("approved_product_image_required") is True
    )
    has_verified_product_image = has_product_image and not brand_fidelity_excluded
    retry_rules: list[str] = []
    if not has_verified_product_image:
        caps = {"product_fidelity": 2, "selling_point_coverage": 2, "commercial_readiness": 2}
        for key, maximum in caps.items():
            if scores.get(key, 0) > maximum:
                scores[key] = maximum
        contradictory_product_claims = (
            "product_fidelity",
            "selling_point_coverage",
            "商品保真",
            "商品真实性",
            "卖点覆盖",
        )
        contradictory_score_claims = ("满分", "高分", "4分", "5分", "四分", "五分")
        filtered_issues = [
            issue
            for issue in issues
            if not (
                any(marker in issue for marker in contradictory_product_claims)
                and any(marker in issue for marker in contradictory_score_claims)
            )
        ]
        if len(filtered_issues) != len(issues):
            issues = filtered_issues
            rules.append("unverified_product_score_claim_removed")
        if brand_fidelity_excluded:
            dimension_applicability["product_fidelity"] = False
            dimension_applicability["selling_point_coverage"] = False
            dimension_applicability["commercial_readiness"] = False
            dimension_applicability_reasons.update({
                "product_fidelity": "本镜头明确不承担商品展示，商品真实性由后续已审核商品素材验证。",
                "selling_point_coverage": "本镜头明确不承担卖点展示，卖点覆盖由后续剪辑与投流成片验证。",
                "commercial_readiness": "当前是待组装的人物或视觉底片，不代表完整投流成片。",
            })
            rules.append("brand_fidelity_excluded_caps_commercial_scores")
            guarded["scope_notes"] = [
                *_safe_list(guarded.get("scope_notes")),
                "本任务明确排除真实商品或品牌展示；当前结果只能评估人物、场景或抽象物体的视觉稳定性，"
                "不能作为商品保真、卖点覆盖或商业就绪证据。"
            ]
        else:
            rules.append("no_authorized_product_image_caps_product_scores")
            issues.append("本任务没有可验证的已授权真实产品图，AI 不能把抽象画面当作真实商品、包装或卖点证据。")
            if requires_product_reference:
                retry_rules.append("missing_required_product_reference")
    if len(_media_quality_asset_refs(job.result_json)) < 2:
        # Diversity is a group-level metric. A normal one-asset task is not a
        # failure and must not be sent into an endless retry loop.
        scores["batch_diversity"] = 3
        dimension_applicability["batch_diversity"] = False
        dimension_applicability_reasons["batch_diversity"] = "单条素材不评价版本差异度。"
        rules.append("single_asset_batch_diversity_not_applicable")
    risk_subjects = ("烟支", "烟草", "香烟", "裸露", "性器官", "未成年人", "儿童", "医疗", "治疗", "违禁")
    risk_markers = ("风险", "疑似", "误生成", "出现", "联想", "违规", "不安全")
    if any(
        any(subject in issue for subject in risk_subjects) and any(marker in issue for marker in risk_markers)
        for issue in issues
    ):
        scores["silhouette_safety"] = min(scores.get("silhouette_safety", 1), 2)
        scores["commercial_readiness"] = min(scores.get("commercial_readiness", 1), 2)
        rules.append("visible_compliance_risk_caps_safety_scores")
        retry_rules.append("visible_compliance_risk")
    if audio_claim_softened:
        rules.append("audio_not_analyzed_claim_softened")
    people_explicitly_forbidden = brief_excludes_people(embedded_brief)
    people_forbidden_visual_gate_required = people_explicitly_forbidden or (
        ad_material_contract.get("people_requested") is False
        and int(ad_material_contract.get("actor_count") or 0) == 0
    )
    clean_dialogue_visual_gate_required = not people_forbidden_visual_gate_required and (
        execution_profile == "clean_dialogue_plate_silent_v1"
        or (
            ad_material_contract.get("content_format") == "dialogue"
            and int(ad_material_contract.get("actor_count") or ad_material_contract.get("speaker_count") or 0) >= 2
        )
    )
    people_visual_gate_required = (
        not people_forbidden_visual_gate_required
        and ad_material_contract.get("people_requested") is True
    )
    no_text_visual_gate_required = execution_profile in {
        "clean_dialogue_plate_silent_v1",
        "clean_people_plate_silent_v4",
        "direct_clean_people_plate_silent_v1",
        "motion_only_no_text_ascii_v3",
        "product_replacement_no_text_ascii_v1",
    } or _text(brand_guardrails.get("reference_text_policy"), 80) in {
        "suppress_all_source_text",
        "suppress_generated_text_preserve_approved_product_surface",
    }
    verified_visual_findings: list[dict[str, Any]] = []
    visual_gate_required = (
        no_text_visual_gate_required
        or people_visual_gate_required
        or people_forbidden_visual_gate_required
    )
    if visual_gate_required:
        for finding in _safe_list(analysis.get("hard_gate_findings")):
            row = _safe_dict(finding)
            visible_text_finding = (
                no_text_visual_gate_required
                and row.get("code") == "visible_text_detected"
                and float(row.get("confidence") or 0) >= 0.8
                and _safe_list(row.get("frame_indices"))
                and _text(row.get("visible_text"), 300)
                and _text(row.get("evidence"), 800)
            )
            if visible_text_finding and approved_surface_text_policy:
                visible_fingerprint = text_fingerprint(row.get("visible_text"))
                finding_reference_fingerprint = text_fingerprint(row.get("reference_visible_text"))
                approved_fingerprint = finding_reference_fingerprint or inventory_fingerprint
                matches_approved_surface = bool(
                    visible_fingerprint
                    and approved_fingerprint
                    and (
                        visible_fingerprint in approved_fingerprint
                        or all(char in approved_fingerprint for char in visible_fingerprint)
                    )
                )
                has_auditable_delta = bool(
                    _text(row.get("reference_comparison"), 80) == "absent_or_different"
                    and _text(row.get("surface_region"), 160)
                    and _safe_list(row.get("reference_image_indices"))
                    and approved_fingerprint
                    and not matches_approved_surface
                )
                if matches_approved_surface or not has_auditable_delta:
                    approved_surface_text_suppressed.append(_text(row.get("visible_text"), 300))
                    visible_text_finding = False
            if visible_text_finding and governed_overlay_provenance_verified:
                surface_region = _text(row.get("surface_region"), 300).casefold()
                approved_overlay_region = any(
                    marker in surface_region
                    for marker in ("包装", "商品植入", "植入区", "overlay", "product", "右下", "中央")
                ) and not any(
                    marker in surface_region
                    for marker in ("新增角标", "字幕", "包装外", "植入区外", "outside")
                )
                if approved_overlay_region:
                    approved_surface_text_suppressed.append(_text(row.get("visible_text"), 300))
                    visible_text_finding = False
            contact_finding = (
                clean_dialogue_visual_gate_required
                and row.get("code") == "interpersonal_contact_detected"
                and float(row.get("confidence") or 0) >= 0.85
                and _safe_list(row.get("frame_indices"))
                and _text(row.get("contact_regions"), 300)
                and _text(row.get("evidence"), 800)
            )
            duplicate_identity_finding = (
                clean_dialogue_visual_gate_required
                and row.get("code") == "duplicate_actor_identity_detected"
                and float(row.get("confidence") or 0) >= 0.9
                and len(_safe_list(row.get("frame_indices"))) >= 3
                and _text(row.get("identity_similarity"), 500)
                and _text(row.get("evidence"), 800)
            )
            synchronized_performance_finding = (
                clean_dialogue_visual_gate_required
                and row.get("code") == "synchronized_performance_detected"
                and float(row.get("confidence") or 0) >= 0.9
                and len(_safe_list(row.get("frame_indices"))) >= 3
                and _text(row.get("synchronized_actions"), 500)
                and _text(row.get("evidence"), 800)
            )
            actor_count_mismatch_finding = (
                people_visual_gate_required
                and row.get("code") == "actor_count_mismatch_detected"
                and float(row.get("confidence") or 0) >= 0.9
                and _safe_list(row.get("frame_indices"))
                and row.get("expected_actor_count") is not None
                and row.get("observed_actor_count") is not None
                and _text(row.get("evidence"), 800)
            )
            cast_market_mismatch_finding = (
                people_visual_gate_required
                and _text(ad_material_contract.get("cast_market"), 60) == "mainland_china"
                and row.get("code") == "cast_market_mismatch_detected"
                and float(row.get("confidence") or 0) >= 0.85
                and len(_safe_list(row.get("frame_indices"))) >= 2
                and _text(row.get("observed_market_cues"), 500)
                and _text(row.get("evidence"), 800)
            )
            synthetic_face_finding = (
                people_visual_gate_required
                and _text(ad_material_contract.get("face_style"), 60) == "authentic_live_action"
                and row.get("code") == "synthetic_face_style_detected"
                and float(row.get("confidence") or 0) >= 0.9
                and len(_safe_list(row.get("frame_indices"))) >= 3
                and _text(row.get("face_artifacts"), 500)
                and _text(row.get("evidence"), 800)
            )
            unexpected_person_finding = (
                people_forbidden_visual_gate_required
                and row.get("code") == "unexpected_person_detected"
                and float(row.get("confidence") or 0) >= 0.9
                and _safe_list(row.get("frame_indices"))
                and int(row.get("observed_actor_count") or 0) >= 1
                and _text(row.get("person_regions"), 500)
                and _text(row.get("evidence"), 800)
            )
            if (
                visible_text_finding
                or contact_finding
                or duplicate_identity_finding
                or synchronized_performance_finding
                or actor_count_mismatch_finding
                or cast_market_mismatch_finding
                or synthetic_face_finding
                or unexpected_person_finding
            ):
                verified_visual_findings.append(row)
    if approved_surface_text_suppressed:
        false_positive_markers = (
            "包装表面印刷文字",
            "与已审核商品参考图不一致",
            "包装文字与参考图",
            "未审核文字",
            "包装上的伪文字",
            "包装乱码文字",
            "text_policy",
            "brand_guardrails",
            "无字画面硬门禁",
        )
        issues = [issue for issue in issues if not any(marker in issue for marker in false_positive_markers)]
        rules.append("approved_product_surface_text_not_a_generated_text_violation")
        guarded["scope_notes"] = [
            *_safe_list(guarded.get("scope_notes")),
            "已审核商品参考图自身印刷不属于新增字幕或伪文字；只有参考图不存在或发生漂移的文字差异才触发硬门禁。",
        ]
    if governed_overlay_provenance_verified:
        if len(proven_overlay_roles) >= 2:
            mismatched_count_issues = [
                issue for issue in issues
                if "单一" in issue and "额外" in issue and "包装" in issue
            ]
            if mismatched_count_issues:
                issues = [issue for issue in issues if issue not in mismatched_count_issues]
                rules.append("verified_duo_overlay_provenance_overrides_single_asset_assumption")
        remaining_product_issue = any(
            any(marker in issue.casefold() for marker in (
                "商品变形", "包装变形", "包装外", "植入区外", "遮挡", "重复商品", "额外商品", "extra product",
            ))
            for issue in issues
        )
        if not remaining_product_issue:
            scores["product_fidelity"] = max(scores.get("product_fidelity", 1), 4)
            rules.append("verified_bridge_overlay_provenance_restores_product_fidelity")
    hard_gate_evaluated = analysis.get("hard_gate_evaluated") is True
    visual_policy_gate = {
        "version": "material-visual-policy-gate-v2",
        "required": visual_gate_required,
        "status": "failed" if verified_visual_findings else "passed" if hard_gate_evaluated or not visual_gate_required else "pending",
        "passed": not verified_visual_findings and (hard_gate_evaluated or not visual_gate_required),
        "findings": verified_visual_findings,
    }
    if verified_visual_findings:
        scores["prompt_alignment"] = min(scores.get("prompt_alignment", 1), 2)
        scores["commercial_readiness"] = min(scores.get("commercial_readiness", 1), 1)
        for finding in verified_visual_findings:
            frames = "、".join(str(value) for value in _safe_list(finding.get("frame_indices")))
            if finding.get("code") == "visible_text_detected":
                visible_text = _text(finding.get("visible_text"), 300)
                issues.append(f"无字画面硬门禁失败：关键帧 {frames} 检测到可见文字“{visible_text}”。")
                rules.append("visible_text_hard_gate_failed")
                retry_rules.append("visible_text_detected")
            elif finding.get("code") == "interpersonal_contact_detected":
                regions = _text(finding.get("contact_regions"), 300)
                issues.append(f"人物独立动作区硬门禁失败：关键帧 {frames} 检测到人物之间接触（{regions}）。")
                rules.append("interpersonal_contact_hard_gate_failed")
                retry_rules.append("interpersonal_contact_detected")
            elif finding.get("code") == "duplicate_actor_identity_detected":
                identity_similarity = _text(finding.get("identity_similarity"), 500)
                issues.append(f"双演员身份硬门禁失败：关键帧 {frames} 检测到重复或镜像脸（{identity_similarity}）。")
                scores["visual_continuity"] = min(scores.get("visual_continuity", 1), 2)
                scores["commercial_readiness"] = min(scores.get("commercial_readiness", 1), 1)
                rules.append("duplicate_actor_identity_hard_gate_failed")
                retry_rules.append("duplicate_actor_identity_detected")
            elif finding.get("code") == "synchronized_performance_detected":
                synchronized_actions = _text(finding.get("synchronized_actions"), 500)
                issues.append(f"双人表演节奏硬门禁失败：关键帧 {frames} 检测到同步或镜像动作（{synchronized_actions}）。")
                scores["hook_strength"] = min(scores.get("hook_strength", 1), 2)
                scores["commercial_readiness"] = min(scores.get("commercial_readiness", 1), 1)
                rules.append("synchronized_performance_hard_gate_failed")
                retry_rules.append("synchronized_performance_detected")
            elif finding.get("code") == "actor_count_mismatch_detected":
                expected = finding.get("expected_actor_count")
                observed = finding.get("observed_actor_count")
                issues.append(f"人物数量硬门禁失败：需求为 {expected} 人，关键帧 {frames} 实际出现 {observed} 人。")
                scores["visual_continuity"] = min(scores.get("visual_continuity", 1), 2)
                scores["commercial_readiness"] = min(scores.get("commercial_readiness", 1), 1)
                rules.append("actor_count_hard_gate_failed")
                retry_rules.append("actor_count_mismatch_detected")
            elif finding.get("code") == "cast_market_mismatch_detected":
                cues = _text(finding.get("observed_market_cues"), 500)
                issues.append(f"国内投流选角/场景硬门禁失败：关键帧 {frames} 的可见市场线索不符合中国大陆要求（{cues}）。")
                scores["hook_strength"] = min(scores.get("hook_strength", 1), 2)
                scores["commercial_readiness"] = min(scores.get("commercial_readiness", 1), 1)
                rules.append("cast_market_hard_gate_failed")
                retry_rules.append("cast_market_mismatch_detected")
            elif finding.get("code") == "synthetic_face_style_detected":
                artifacts = _text(finding.get("face_artifacts"), 500)
                issues.append(f"真人脸质感硬门禁失败：关键帧 {frames} 持续出现合成脸或过度美颜特征（{artifacts}）。")
                scores["visual_continuity"] = min(scores.get("visual_continuity", 1), 2)
                scores["commercial_readiness"] = min(scores.get("commercial_readiness", 1), 1)
                rules.append("synthetic_face_style_hard_gate_failed")
                retry_rules.append("synthetic_face_style_detected")
            elif finding.get("code") == "unexpected_person_detected":
                observed = int(finding.get("observed_actor_count") or 0)
                regions = _text(finding.get("person_regions"), 500)
                issues.append(
                    f"无人物画面硬门禁失败：需求明确不得出现人物，关键帧 {frames} 实际检测到 {observed} 人（{regions}）。"
                )
                scores["prompt_alignment"] = min(scores.get("prompt_alignment", 1), 1)
                scores["hook_strength"] = min(scores.get("hook_strength", 1), 2)
                scores["commercial_readiness"] = min(scores.get("commercial_readiness", 1), 1)
                scores["silhouette_safety"] = min(scores.get("silhouette_safety", 1), 2)
                rules.append("unexpected_person_hard_gate_failed")
                retry_rules.append("unexpected_person_detected")
    open_annotations = [
        item for item in (review_annotations or [])
        if isinstance(item, dict) and _text(item.get("status"), 20) != "resolved" and _text(item.get("note"), 600)
    ]
    if open_annotations:
        category_score_caps = {
            "motion": {"prompt_alignment": 4, "visual_continuity": 4, "hook_strength": 4, "commercial_readiness": 3},
            "brand": {"prompt_alignment": 4, "product_fidelity": 3, "commercial_readiness": 3},
            "text": {"prompt_alignment": 4, "commercial_readiness": 3},
            "commercial": {"commercial_readiness": 3, "selling_point_coverage": 3},
            "human_shape": {"silhouette_safety": 3, "commercial_readiness": 3},
            "technical": {"visual_continuity": 4, "shot_boundary_clarity": 4, "commercial_readiness": 3},
            "composition": {"prompt_alignment": 3, "visual_continuity": 3, "hook_strength": 3, "commercial_readiness": 3},
            "continuity": {"visual_continuity": 3, "shot_boundary_clarity": 3, "commercial_readiness": 3},
        }
        for annotation in open_annotations:
            start = float(annotation.get("start_seconds") or 0)
            end = annotation.get("end_seconds")
            range_text = f"{start:g}s" if end is None else f"{start:g}-{float(end):g}s"
            category = _text(annotation.get("category"), 60) or "other"
            severity = _text(annotation.get("severity"), 20) or "warning"
            note = _text(annotation.get("note"), 600)
            issues.append(f"人工审片未解决标注（{range_text}，{category}/{severity}）：{note}")
            for key, maximum in category_score_caps.get(category, {}).items():
                scores[key] = min(scores.get(key, maximum), maximum)
            if severity == "critical":
                retry_rules.append("unresolved_critical_review_annotation")
        rules.append("unresolved_human_review_annotations_preserved")
    guarded["model_scores"] = original_scores
    guarded["scores"] = scores
    guarded["overall_score"] = round(sum(scores.values()) / (len(MEDIA_QUALITY_DIMENSION_KEYS) * 5), 3)
    applicable_scores = [
        scores[key] for key in MEDIA_QUALITY_DIMENSION_KEYS
        if dimension_applicability.get(key, True)
    ]
    guarded["dimension_applicability"] = dimension_applicability
    guarded["dimension_applicability_reasons"] = dimension_applicability_reasons
    guarded["material_stage"] = material_stage
    guarded["applicable_dimension_count"] = len(applicable_scores)
    guarded["applicable_overall_score"] = round(
        sum(applicable_scores) / (len(applicable_scores) * 5), 3
    ) if applicable_scores else 0.0
    guarded["issues"] = list(dict.fromkeys(issues))[:12]
    guarded["deterministic_guardrails"] = rules
    guarded["visual_policy_gate"] = visual_policy_gate
    if rules:
        existing = guarded.get("recommendation")
        recommendation = _safe_dict(existing)
        prompt_changes = [
            _text(item, 600) for item in _safe_list(recommendation.get("prompt_changes")) if _text(item, 600)
        ]
        if subtitles_forbidden:
            prompt_changes = [
                item for item in prompt_changes
                if not (
                    any(marker in item.casefold() for marker in ("字幕", "caption", "subtitle"))
                    and any(marker in item.casefold() for marker in ("添加", "增加", "补", "加入", "显示", "覆盖", "叠加", "烧录", "add"))
                )
            ]
        if brand_fidelity_excluded:
            prompt_changes = [
                item for item in prompt_changes
                if not (
                    any(marker in item.casefold() for marker in ("商品", "产品", "包装", "品牌", "logo", "卖点"))
                    and any(marker in item.casefold() for marker in ("添加", "增加", "补", "加入", "展示", "植入", "add"))
                )
            ]
        if requires_product_reference and not has_verified_product_image:
            prompt_changes.append("改用图生视频并上传已授权、包装文字正确的产品图作为 first_frame。")
        prompt_changes.extend(
            _text(item.get("note"), 600) for item in open_annotations if _text(item.get("note"), 600)
        )
        decision = _text(recommendation.get("decision"), 80) or "human_review"
        reason = _text(recommendation.get("reason"), 1000)
        if retry_rules:
            decision = "retry_recommended"
            reason = reason or "确定性质量门禁发现必需商品证据或可见合规风险。"
        elif product_overlay_component_scope:
            decision = "human_review"
            reason = (
                "当前商品镜头已完成真实商品像素合成，可评价画面和商品保真；"
                "它仍是待剪辑素材，不代表钩子、卖点和转化链路完整的投流成片。"
            )
        elif audio_claim_softened or brand_fidelity_excluded or subtitle_absence_removed:
            decision = "human_review"
            reason = (
                "任务明确禁止烧录字幕或画面文字；未出现字幕符合生产要求。"
                "视觉关键帧不能验证音频内容，请人工看片并听审。"
                if subtitle_absence_removed
                else "视觉关键帧不能验证音频内容，且当前素材只用于合成画面稳定性测试；请人工看片并听审。"
            )
        guarded["recommendation"] = {
            "decision": decision,
            "reason": reason,
            "prompt_changes": list(dict.fromkeys(prompt_changes))[:12],
        }
    return guarded


def _media_quality_focus_crop(expected_prompt: Any, *, overlay_anchor: Any = None) -> tuple[str, str]:
    prompt_text = str(expected_prompt or "").casefold()
    requested_anchor = str(overlay_anchor or "").strip().casefold().replace("-", "_")
    if not requested_anchor and not any(
        term in prompt_text for term in ("包装", "logo", "品牌字", "文字", "package text", "brand mark")
    ):
        return "", ""
    focus_regions = (
        (("画面中央", "中央", "居中", "center", "centred", "centered"), "center", "crop=iw*0.55:ih*0.55:iw*0.225:ih*0.225,scale=480:-2"),
        (("右下角", "右下方", "lower-right", "bottom-right"), "bottom_right", "crop=iw*0.55:ih*0.55:iw*0.45:ih*0.45,scale=480:-2"),
        (("左下角", "左下方", "lower-left", "bottom-left"), "bottom_left", "crop=iw*0.55:ih*0.55:0:ih*0.45,scale=480:-2"),
        (("右上角", "右上方", "upper-right", "top-right"), "top_right", "crop=iw*0.55:ih*0.55:iw*0.45:0,scale=480:-2"),
        (("左上角", "左上方", "upper-left", "top-left"), "top_left", "crop=iw*0.55:ih*0.55:0:0,scale=480:-2"),
    )
    for _terms, region, filter_value in focus_regions:
        if requested_anchor == region:
            return region, filter_value
    for terms, region, filter_value in focus_regions:
        if any(term in prompt_text for term in terms):
            return region, filter_value
    return "", ""


def _media_quality_provider_label(config: dict[str, Any] | None) -> str:
    """Return a safe, auditable provider label without exposing credentials."""

    value = _safe_dict(config)
    api_base = str(value.get("ai.api_base") or "").strip().lower()
    provider = str(value.get("ai.provider") or "").strip().lower()
    if "api.siliconflow.cn" in api_base or provider == "siliconflow":
        return "SiliconFlow"
    if provider and provider != "custom":
        return provider
    return "OpenAI-compatible custom endpoint" if api_base else "platform vision profile"


def _effective_media_technical_validation(
    job: MediaGenerationJob,
    asset: ProjectRunAsset,
) -> dict[str, Any]:
    """Return the final artifact validation used by visual quality analysis.

    Governed dialogue delivery replaces the clean H3 plate with a muxed video.
    Older mux assets did not persist ``technical_validation`` on the asset row,
    while the authoritative final validation was already stored on the job.
    Merge both sources so a nine-second delivery can never be sampled using the
    original five-second request fallback.  Asset metadata wins when present.
    """

    job_validation = _safe_dict(_safe_dict(job.result_json).get("technical_validation"))
    asset_validation = _safe_dict(_safe_dict(asset.metadata_json).get("technical_validation"))
    return {**job_validation, **asset_validation}


async def _call_people_presence_verifier(
    frame_rows: list[tuple[str, bytes]],
    *,
    user: User,
    run: ProjectRun,
    job: MediaGenerationJob,
    asset: ProjectRunAsset,
    attempt: int,
) -> dict[str, Any]:
    """Run a narrow second vision pass for tasks that explicitly forbid people."""

    system = (
        "你是短视频关键帧中的人物存在检测器，只判断画面是否出现人物，不评价创意、商品、场景或画质。"
        "严格返回 JSON，且只能包含 person_present、confidence、frame_indices、observed_actor_count、person_regions、evidence。"
        "逐张检查所有输出关键帧：只要出现清晰真人脸、头部、躯干、手臂、手、腿、完整人体或人体剪影，"
        "person_present 必须为 true；frame_indices 使用从 1 开始的关键帧编号，observed_actor_count 填可确认的最大同时人数，"
        "person_regions 写明人物部位和画面区域，evidence 写可复核的像素证据。"
        "已审核商品包装上很小的印刷人物不算人物；模糊纹理、抽象曲线或无法确认的人形联想不算。"
        "只有检查完全部关键帧且均未发现上述人体部位时才可返回 false。明显完整人脸或上半身的置信度应不低于 0.99；"
        "无法达到 0.90 置信度时必须如实返回较低值，系统会阻止自动判定，不得虚构证据。"
    )
    parts: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            f"本任务明确禁止人物。下面是按时间顺序抽取的 {len(frame_rows)} 张输出关键帧；"
            "它们不是参考图。请只判断这些帧中是否出现人物。"
        ),
    }]
    for index, (label, content) in enumerate(frame_rows):
        parts.extend([
            {"type": "text", "text": f"输出关键帧 {index + 1}，文件标识 {label}"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(content).decode("ascii")}},
        ])
    raw = await call_llm_multimodal(
        system,
        parts,
        json_mode=True,
        max_tokens=700,
        temperature=0,
        timeout=90,
        call_source="material_workbench_people_presence_verifier",
        cost_context={
            "user_id": getattr(user, "id", None),
            "department": getattr(user, "department", None),
            "conversation_id": run.execution_run_id or run.id,
            "project_run_id": run.id,
            "media_job_id": job.id,
            "asset_id": asset.id,
            "attempt": attempt,
            "policy_version": MEDIA_QUALITY_ANALYSIS_POLICY_VERSION,
            "verifier_version": "people-presence-v1",
        },
        model_profile="vision",
        require_system_config=True,
    )
    return _normalize_people_presence_verifier(raw, frame_count=len(frame_rows))


async def _call_media_quality_model(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    job: MediaGenerationJob,
    asset: ProjectRunAsset,
) -> tuple[str, dict[str, Any]]:
    from app.projects.service import _project_run_asset_abs_path

    path = _project_run_asset_abs_path(asset)
    config = await get_ai_profile_config(model_profile="vision", require_system_config=True)
    model = _text(config.get("ai.model"), 180) or "vision-profile"
    provider = _media_quality_provider_label(config)
    prompt = _safe_dict(job.prompt_json)
    product_overlay = _safe_dict(prompt.get("product_overlay"))
    technical = _effective_media_technical_validation(job, asset)
    try:
        clip_duration_seconds = max(
            0.1,
            float(
                technical.get("duration_seconds")
                or technical.get("duration")
                or _safe_dict(asset.metadata_json).get("duration_seconds")
                or _safe_dict(job.params_json).get("duration_seconds")
                or 5
            ),
        )
    except (TypeError, ValueError):
        clip_duration_seconds = 5.0
    context = {
        "job_id": job.id,
        "mode": job.mode,
        "expected_prompt": _text(prompt.get("integrated_multimodal_description") or prompt.get("video_prompt"), 7000),
        "shots": _safe_list(prompt.get("shots"))[:20],
        "production_brief": _safe_dict(prompt.get("production_brief")),
        "ad_material_contract": _safe_dict(prompt.get("ad_material_contract")),
        "brand_guardrails": _safe_dict(prompt.get("brand_guardrails")),
        "product_overlay": product_overlay,
        "postprocess_provenance": _safe_dict(_safe_dict(asset.metadata_json).get("postprocess_provenance")),
        "production_variant": _safe_dict(prompt.get("production_variant")),
        "technical_validation": technical,
        "reference_assets": [
            {
                "asset_id": _text(item.get("asset_id"), 50),
                "role": _text(item.get("role"), 40),
                "purpose": _text(item.get("purpose"), 300),
            }
            for item in _safe_list(job.reference_assets_json)
            if isinstance(item, dict)
        ][:12],
    }
    annotation_rows = (
        await db.execute(
            select(MediaReviewAnnotation)
            .where(
                MediaReviewAnnotation.media_job_id == job.id,
                MediaReviewAnnotation.status == "open",
            )
            .order_by(MediaReviewAnnotation.start_seconds, MediaReviewAnnotation.created_at)
        )
    ).scalars().all()
    review_annotations = [
        {
            "start_seconds": item.start_seconds,
            "end_seconds": item.end_seconds,
            "category": item.category,
            "severity": item.severity,
            "status": item.status,
            "note": _text(item.note, 600),
            "evidence": _safe_list(item.evidence_json)[:8],
        }
        for item in annotation_rows
    ]
    context["unresolved_human_review_annotations"] = review_annotations
    ffmpeg = _media_quality_ffmpeg_executable()
    frame_rows: list[tuple[str, bytes]] = []
    reference_rows: list[tuple[str, str, bytes]] = []
    focus_frame_rows: list[tuple[str, bytes]] = []
    focus_reference_rows: dict[int, tuple[str, str, bytes]] = {}
    focus_region, focus_filter = _media_quality_focus_crop(
        context["expected_prompt"],
        overlay_anchor=product_overlay.get("anchor") if product_overlay.get("enabled") is True else None,
    )
    with tempfile.TemporaryDirectory(prefix="skillforge-media-quality-") as temp_dir:
        output_pattern = str(Path(temp_dir) / "frame-%02d.jpg")
        proc = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-vf",
                "fps=1,scale=360:-2",
                "-frames:v",
                str(max(1, MEDIA_QUALITY_ANALYSIS_MAX_FRAMES - 1)),
                "-q:v",
                "3",
                output_pattern,
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError("关键帧提取失败：" + (_text(proc.stderr, 500) or "ffmpeg error"))
        for frame_path in sorted(Path(temp_dir).glob("frame-*.jpg"))[:max(1, MEDIA_QUALITY_ANALYSIS_MAX_FRAMES - 1)]:
            content = frame_path.read_bytes()
            if not content or len(content) > MEDIA_QUALITY_ANALYSIS_MAX_FRAME_BYTES:
                continue
            frame_rows.append((frame_path.stem, content))
        final_frame_path = Path(temp_dir) / "frame-final.jpg"
        final_frame_proc = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{max(0.0, clip_duration_seconds - 0.08):.3f}",
                "-i",
                str(path),
                "-vf",
                "scale=360:-2",
                "-frames:v",
                "1",
                "-q:v",
                "3",
                "-y",
                str(final_frame_path),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if final_frame_proc.returncode == 0 and final_frame_path.is_file():
            final_content = final_frame_path.read_bytes()
            if (
                final_content
                and len(final_content) <= MEDIA_QUALITY_ANALYSIS_MAX_FRAME_BYTES
                and all(hashlib.sha256(content).digest() != hashlib.sha256(final_content).digest() for _, content in frame_rows)
            ):
                frame_rows.append(("frame-final", final_content))
        if focus_filter:
            focus_output_pattern = str(Path(temp_dir) / "focus-frame-%02d.jpg")
            focus_proc = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(path),
                    "-vf",
                    f"fps=1,{focus_filter}",
                    "-frames:v",
                    str(MEDIA_QUALITY_ANALYSIS_MAX_FRAMES),
                    "-q:v",
                    "3",
                    focus_output_pattern,
                ],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            if focus_proc.returncode == 0:
                for focus_path in sorted(Path(temp_dir).glob("focus-frame-*.jpg"))[:MEDIA_QUALITY_ANALYSIS_MAX_FRAMES]:
                    content = focus_path.read_bytes()
                    if content and len(content) <= MEDIA_QUALITY_ANALYSIS_MAX_FRAME_BYTES:
                        focus_frame_rows.append((focus_path.stem, content))
        quality_references = [
            ref
            for ref in _safe_list(job.reference_assets_json)
            if isinstance(ref, dict)
            and _text(ref.get("role"), 40) in {"first_frame", "reference_image", "product_packshot", "overlay_image"}
        ][:3]
        for index, ref in enumerate(quality_references):
            if not isinstance(ref, dict):
                continue
            role = _text(ref.get("role"), 40)
            reference_asset = await db.get(ProjectRunAsset, _text(ref.get("asset_id"), 50))
            if (
                not reference_asset
                or reference_asset.project_id != job.project_id
                or not str(reference_asset.mime_type or "").lower().startswith("image/")
            ):
                continue
            reference_path = _project_run_asset_abs_path(reference_asset)
            reference_output = str(Path(temp_dir) / f"reference-{index + 1:02d}.jpg")
            reference_proc = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(reference_path),
                    "-vf",
                    "scale=360:-2",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    reference_output,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if reference_proc.returncode != 0 or not Path(reference_output).is_file():
                continue
            reference_content = Path(reference_output).read_bytes()
            if not reference_content or len(reference_content) > MEDIA_QUALITY_ANALYSIS_MAX_FRAME_BYTES:
                continue
            display_role = _text(ref.get("business_role"), 40) or role
            reference_rows.append((display_role, _text(ref.get("purpose"), 300), reference_content))
            if focus_filter and role == "overlay_image":
                # The transparent packshot is already the complete object of
                # interest.  Crop the output placement region, but compare it
                # against the full approved source rather than transparent
                # lower-right pixels from the square source canvas.
                focus_reference_rows[len(reference_rows) - 1] = (
                    display_role,
                    _text(ref.get("purpose"), 300),
                    reference_content,
                )
            elif focus_filter:
                focus_reference_output = str(Path(temp_dir) / f"focus-reference-{index + 1:02d}.jpg")
                focus_reference_proc = subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(reference_path),
                        "-vf",
                        focus_filter,
                        "-frames:v",
                        "1",
                        "-q:v",
                        "3",
                        focus_reference_output,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if focus_reference_proc.returncode == 0 and Path(focus_reference_output).is_file():
                    focus_content = Path(focus_reference_output).read_bytes()
                    if focus_content and len(focus_content) <= MEDIA_QUALITY_ANALYSIS_MAX_FRAME_BYTES:
                        focus_reference_rows[len(reference_rows) - 1] = (
                            display_role,
                            _text(ref.get("purpose"), 300),
                            focus_content,
                        )
    if not frame_rows:
        raise RuntimeError("关键帧提取失败：没有可分析画面")

    system = (
        "你是 SkillForge 素材工作台的短视频成片质量评估 Agent。"
        "必须只依据按时间顺序抽取的视频关键帧、给定提示词和技术验收做保守判断，不得声称听过音频，不得编造商品、包装、文字、价格、销量、功效或投流结果。"
        "严格返回 JSON：summary、timeline、scores、strengths、issues、evidence、recommendation、confidence、reference_text_inventory、hard_gate_findings。"
        "若提供商品参考图，必须先填写 reference_text_inventory：逐张记录 reference_image_index、surface_region、visible_text 和 confidence，"
        "把参考图包装表面可辨认的品牌名、产品名、卖点字样按实际排版逐字抄录；看不清时 visible_text 置空，不得猜测。"
        "hard_gate_findings 必须是数组。仅当画面真实出现可辨认文字像素时，写入 code=visible_text_detected、confidence、"
        "frame_indices、visible_text（逐字抄录）和 evidence；不得把杯子、衣物纹理、窗框误报为文字。"
        "当 brand_guardrails.reference_text_policy=suppress_generated_text_preserve_approved_product_surface 时，"
        "与已审核商品参考图一致的包装表面印刷属于允许内容，不得记为 visible_text_detected；字幕、角标、水印、"
        "新增营销文字、漂移伪文字以及与参考图不一致的包装文字仍必须记为 visible_text_detected。"
        "此策略下 visible_text_detected 还必须提供 surface_region、reference_comparison=absent_or_different、"
        "reference_visible_text 和 reference_image_indices；若关键帧文字能在 reference_text_inventory 找到，禁止形成 hard_gate_findings，"
        "也不得仅因画面存在这些已审核印刷而降低 prompt_alignment、product_fidelity 或 commercial_readiness。"
        "visible_text 必须逐字来自关键帧中真实可辨认的像素；严禁从提示词、negative_constraints、brand_guardrails"
        "或系统说明中复制词句作为画面文字证据。分辨率不足以逐字确认时不得猜测或补全。"
        "当 production_brief 明确写了无人物、不要人物、不出现人物，或 ad_material_contract.people_requested=false 且 actor_count=0 时，"
        "任一关键帧出现清晰的真人脸、头部、躯干、手臂、手、腿、完整人体或人体剪影，都必须写入 code=unexpected_person_detected、"
        "confidence、frame_indices、observed_actor_count、person_regions 和 evidence。已审核商品包装上很小的印刷人物不计入，"
        "模糊纹理或无法确认的人形联想也不得报错；但画面主体中的清晰人物不能因构图自然或广告效果好而忽略。"
        "双人对话必须逐帧检查两个人之间是否发生手碰手、手碰对方手臂、肩部、胸腹、腰腿或躯干；证据明确时写入 "
        "code=interpersonal_contact_detected、confidence、frame_indices、contact_regions 和 evidence。不能因接触看起来自然就省略。"
        "双人对话还必须逐帧比较两个演员的脸型、鼻形、眼距、下颌、发型、发色、年龄纹理和服装；若至少三帧明确显示同一张脸被复制、"
        "镜像或仅换颜色，写入 code=duplicate_actor_identity_detected、confidence、frame_indices、identity_similarity 和 evidence。"
        "若至少三帧明确显示两人同步微笑、同步张嘴、同步抬眉、同步倾头或镜像变化，写入 code=synchronized_performance_detected、"
        "confidence、frame_indices、synchronized_actions 和 evidence；静态相似姿态本身不足以报此项。"
        "必须以 ad_material_contract.actor_count 为人物数量真值：若要求单人口播但任一关键帧出现第二人物、背景人脸、镜中人或额外人体，"
        "或要求双人但持续缺少一人，写入 code=actor_count_mismatch_detected、confidence、frame_indices、expected_actor_count、"
        "observed_actor_count 和 evidence。"
        "当 ad_material_contract.cast_market=mainland_china 时，只依据画面中可见的整体选角、发型妆造、服装、室内陈设、建筑和环境线索"
        "保守评价中国大陆投流市场匹配度；不得猜测具体人物国籍或身份。若至少两帧存在清晰且一致的非本地市场线索，写入 "
        "code=cast_market_mismatch_detected、confidence、frame_indices、expected_cast_market=mainland_china、observed_market_cues 和 evidence。"
        "当 ad_material_contract.face_style=authentic_live_action 时，必须检查毛孔、肤色微变化、左右轻微不对称、眼下纹理、发丝和表情肌变化。"
        "仅当至少三帧持续出现蜡像皮肤、完美镜像对称、塑料高光、五官模板化、皮肤纹理随帧漂移或明显 CG 感等多项证据时，写入 "
        "code=synthetic_face_style_detected、confidence、frame_indices、face_artifacts 和 evidence；单纯妆容精致或光线柔和不足以报错。"
        "scores 必须完整包含 prompt_alignment、visual_continuity、hook_strength、shot_boundary_clarity、"
        "batch_diversity、commercial_readiness、product_fidelity、selling_point_coverage、silhouette_safety，"
        "每项只能是 1-5 整数。若没有真实商品图或无法辨识商品，product_fidelity 与 selling_point_coverage 不得给高分。"
        "若任务明确排除真实商品或品牌展示，这两项仍必须保守给分，issues 中也不得把未展示商品解释成满分或高分。"
        "本次没有自动语音识别：不得用字幕缺失、口型不明显或关键帧不可见来断言口播/台词缺失；只能提示人工听审。"
        "若提供了输入参考图片，必须先把它视为生成前基准，再逐帧比较主体身份、构图、物体位置、相对尺寸、朝向、形状和可见文字；"
        "输入中很小的背景静物若在输出中显著变大、移位、转正、变形或产生伪文字，必须作为首帧保真问题写入 issues 与 evidence，"
        "并降低 prompt_alignment、visual_continuity 或 product_fidelity；不得仅因物体在输出内部五帧稳定就判定保真。"
        "若同时提供局部放大图，它只是对应参考图或关键帧的确定性裁剪，不是新画面；必须用它逐字检查微小包装、Logo 和文字漂移。"
        "若 product_overlay.enabled=true，输入角色 overlay_image 是 Bridge 在 H3 生成后确定性植入的唯一合法商品基准："
        "不得把该植入本身判为违规；必须按 product_overlay.anchor 和 product_overlay.layout 指定区域，逐一检查最终画面"
        "是否只出现与每张基准图形状、颜色、数量、前后层级和可见印刷一致的商品；single_verified_product_v1 只允许一件，"
        "packshot_detail_duo_v1 允许一张包装图与一张商品细节图并排组成商品组；anchor=center 表示画面中央，"
        "anchor=bottom_right 表示右下角。"
        "若 postprocess_provenance.product_overlay_applied=true，且商品 SHA256、角色和配置 SHA256 完整，"
        "说明 Bridge 已把对应参考图原始像素确定性合成到指定区域；该区域内与参考图一致的包装文字和两图组合不是模型重绘，"
        "不得因小尺寸 OCR 不确定而声称包装文字漂移。只有能指出植入区之外的额外商品，或新增角标、字幕、遮挡、变形的逐帧证据时才可判错。"
        "除该基准对应植入外，画面中由底片模型额外生成的商品卡、重复包装、伪文字、浮层或形变商品都属于严重问题，"
        "并应降低 prompt_alignment、product_fidelity 和 commercial_readiness。"
        "若商品采用 dynamic_clean_plate_then_deterministic_product_overlay，且没有完整台词、真实活动依据或明确转化动作，"
        "它属于单个商品素材镜头：只能评价画面与商品保真，不能把包装清晰直接等同于完整投流成片；"
        "selling_point_coverage 与 commercial_readiness 必须保守返回 3，并说明还需与钩子、卖点或 CTA 组装。"
        "只有提示词明确要求字幕时才评价字幕覆盖；若提示词明确禁止文字，不得因没有字幕扣分。"
        "若提示词明确要求 [Static shot]、固定机位或一镜到底，不得因缺少运镜、切镜或景别变化扣分，应评价其稳定性。"
        "若任务是双人对话且没有明确要求面向镜头口播，必须逐帧检查交流视线和身体朝向：任意一人在任意抽样帧明显直视镜头、"
        "两人同时直视镜头、逐渐并肩站正、肩线平行于画面或形成居中合影式姿态，均属于对话表演退化；必须在 issues 和 evidence 中写明"
        "关键帧编号，并降低 visual_continuity、hook_strength 与 commercial_readiness。只有单人明确直面镜头口播时才不适用。"
        "判断直视镜头必须同时看到脸部朝向外部观察者且双眼瞳孔指向镜头；侧脸或斜侧脸时，水平看向画面内另一人物不属于直视镜头。"
        "若提示词点名咖啡馆、落地窗、桌面、诊室等场景锚点，必须检查输出是否真实保留；咖啡馆变成居家沙发、面对面变成并排、"
        "点名锚点消失均属于场景和构图偏离。咖啡馆内静止的杯子、碟子等普通场景器具不是凭空生成的违规道具，不得因此扣分；"
        "但人物拿起、展示或指向未要求商品，以及凭空生成领夹麦、耳机、工牌、挂绳、录音设备时，必须在 issues 与 evidence 中写明。"
        "判断画面拼接、镜像重复或垂直分割伪影时，必须先区分真实窗框、墙角、门框、玻璃边缘和自然透视线。仅看到一条稳定竖线不足以报错；"
        "只有竖线两侧明确出现同一桌沿、人物局部或背景纹理的重复、反向、错位或透视断裂，并且至少三张关键帧均可复核，才可列为严重结构伪影。"
        "此类 issues 的 evidence 必须分别说明边界两侧具体重复或断裂的对象和关键帧编号；不得只写‘存在垂直线’。"
        "若首帧要求空手在说话人自身身体空间抬起，手却搭在、触碰或跨入另一人的肩膀、手臂或躯干，属于动作偏离，"
        "必须在 issues 和 evidence 写明首帧编号，并降低 prompt_alignment 与 hook_strength；不得只因接触看起来自然就忽略。"
        "若上下文包含 unresolved_human_review_annotations，必须逐条复核对应时间范围和关键帧；人工未解决标注优先于模型判断，"
        "不得用‘无显著问题’覆盖它，也不得删除或弱化其问题描述。"
        "单条视频不能评价批次差异，batch_diversity 返回中性 3，不得据此建议重试。"
        "判断字幕或卖点是否出现前必须检查全部关键帧，并在 evidence 中写明具体关键帧编号和逐字可见文字。"
        "recommendation 必须包含 decision、reason、prompt_changes；decision 只能是 human_review、retry_recommended 或 reject_recommended。"
        "这是审核建议，绝不能声称已经审核通过、已经同步云视频或已经进入训练。"
    )
    parts: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            "生成任务上下文：" + json.dumps(context, ensure_ascii=False, default=str)
            + f"。下面是按每秒 1 帧抽取的 {len(frame_rows)} 张关键帧；关键帧不能证明帧间全部运动，请保守判断。"
        ),
    }]
    for index, (role, purpose, content) in enumerate(reference_rows):
        parts.extend([
            {
                "type": "text",
                "text": f"输入参考图 {index + 1}，角色 {role}，用途 {purpose or '未说明'}；这是生成前基准，不是输出关键帧。",
            },
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(content).decode("ascii")}},
        ])
        if index in focus_reference_rows:
            focus_role, focus_purpose, focus_content = focus_reference_rows[index]
            parts.extend([
                {
                    "type": "text",
                    "text": f"输入参考图 {index + 1} 的 {focus_region} 局部放大；角色 {focus_role}，用途 {focus_purpose or '未说明'}。",
                },
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(focus_content).decode("ascii")}},
            ])
    for index, (label, content) in enumerate(frame_rows):
        frame_time = max(0.0, clip_duration_seconds - 0.08) if label == "frame-final" else float(index)
        parts.extend([
            {"type": "text", "text": f"关键帧 {index + 1}，约第 {frame_time:.3f} 秒，文件标识 {label}"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(content).decode("ascii")}},
        ])
        if index < len(focus_frame_rows):
            focus_label, focus_content = focus_frame_rows[index]
            parts.extend([
                {"type": "text", "text": f"关键帧 {index + 1} 的 {focus_region} 局部放大，文件标识 {focus_label}"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(focus_content).decode("ascii")}},
            ])
    people_contract = _safe_dict(context.get("ad_material_contract"))
    people_presence_verifier_required = brief_excludes_people(context.get("production_brief")) or (
        people_contract.get("people_requested") is False
        and int(people_contract.get("actor_count") or 0) == 0
    )
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            raw = await call_llm_multimodal(
                system,
                parts,
                json_mode=True,
                max_tokens=2400,
                temperature=0.1,
                timeout=150,
                call_source="material_workbench_quality_analysis",
                cost_context={
                    "user_id": getattr(user, "id", None),
                    "department": getattr(user, "department", None),
                    "conversation_id": run.execution_run_id or run.id,
                    "project_run_id": run.id,
                    "media_job_id": job.id,
                    "asset_id": asset.id,
                    "attempt": attempt,
                    "policy_version": MEDIA_QUALITY_ANALYSIS_POLICY_VERSION,
                },
                model_profile="vision",
                require_system_config=True,
            )
            normalized_raw = _normalize_ai_media_quality_result(raw)
            if normalized_raw.get("hard_gate_evaluated") is not True:
                raise ValueError("quality_hard_gate_findings_missing")
            if people_presence_verifier_required:
                people_presence_verifier = await _call_people_presence_verifier(
                    frame_rows,
                    user=user,
                    run=run,
                    job=job,
                    asset=asset,
                    attempt=attempt,
                )
                normalized_raw = _merge_people_presence_verifier(
                    normalized_raw,
                    people_presence_verifier,
                )
            normalized = _apply_ai_media_quality_guardrails(
                normalized_raw, job, review_annotations=review_annotations
            )
            normalized["analysis_input"] = {
                "mode": "reference_images_and_ordered_keyframes" if reference_rows else "ordered_keyframes",
                "frame_count": len(frame_rows),
                "reference_image_count": len(reference_rows),
                "focus_region": focus_region or None,
                "focus_frame_count": len(focus_frame_rows),
                "focus_reference_count": len(focus_reference_rows),
                "sampling_fps": 1,
                "final_frame_sampled": any(label == "frame-final" for label, _content in frame_rows),
                "audio_content_analyzed": False,
                "people_presence_verifier_required": people_presence_verifier_required,
                "people_presence_verifier_completed": (
                    _safe_dict(normalized.get("people_presence_verifier")).get("status") == "completed"
                    if people_presence_verifier_required
                    else None
                ),
            }
            normalized["runtime"] = {
                "provider": provider,
                "model": model,
                "model_profile": "vision",
                "model_origin": "platform_vision_profile",
                "project_fine_tuned": False,
                "deployment_id": None,
                "artifact_id": None,
            }
            return model, normalized
        except (ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt == 1:
                continue
            raise
    raise last_error or RuntimeError("media_quality_model_empty_response")


def _build_media_repair_plan(job: MediaGenerationJob, successful: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn advisory quality findings into an auditable production return packet."""

    issues: list[str] = []
    prompt_changes: list[str] = []
    decisions: list[str] = []
    scores: list[float] = []
    for item in successful:
        analysis = _safe_dict(item.get("analysis"))
        issues.extend(_text(value, 600) for value in _safe_list(analysis.get("issues")) if _text(value, 600))
        recommendation = _safe_dict(analysis.get("recommendation"))
        decision = _text(recommendation.get("decision"), 80)
        if decision:
            decisions.append(decision)
        prompt_changes.extend(
            _text(value, 600)
            for value in _safe_list(recommendation.get("prompt_changes"))
            if _text(value, 600)
        )
        try:
            scores.append(float(analysis.get("overall_score") or 0))
        except (TypeError, ValueError):
            pass
    average = sum(scores) / len(scores) if scores else 0.0
    retry_recommended = (
        any(value in {"retry_recommended", "reject_recommended"} for value in decisions)
        or (bool(scores) and average < 0.65)
    )
    constraints = list(dict.fromkeys(prompt_changes))[:12]
    if retry_recommended and not constraints:
        constraints = [f"修复质检问题：{item}" for item in list(dict.fromkeys(issues))[:6]]
    policy = _safe_dict(_safe_dict(job.result_json).get("production_policy"))
    return {
        "version": "material-quality-repair-v1",
        "status": "retry_recommended" if retry_recommended else "human_review",
        "automatic_submit": False,
        "requires_human_review": True,
        "source_job_id": job.id,
        "production_intent": policy.get("production_intent"),
        "issue_summary": list(dict.fromkeys(issues))[:12],
        "constraints": constraints,
        "reuse": {
            "source_assets": True,
            "output_preset_id": job.output_preset_id,
            "workflow_template_id": job.workflow_template_id,
            "mode": job.mode,
        },
    }


async def _run_media_quality_analysis(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    job: MediaGenerationJob,
) -> dict[str, Any]:
    refs = _media_quality_asset_refs(job.result_json)
    if not refs:
        raise AppError("MEDIA_RESULT_REQUIRED", 409)
    results: list[dict[str, Any]] = []
    models: list[str] = []
    providers: list[str] = []
    for index, ref in enumerate(refs):
        asset_id = _text(ref.get("id") or ref.get("asset_id"), 50)
        asset = await db.get(ProjectRunAsset, asset_id)
        # The queue is project-durable: an operator may collect a completed
        # Bridge job while viewing a newer ProjectRun. Bind quality analysis
        # to project + media_job_id lineage, not that transient viewer run.
        if not asset or asset.project_id != job.project_id or not str(asset.mime_type or "").lower().startswith("video/"):
            results.append({"asset_id": asset_id, "asset_index": index, "status": "failed", "error": "生成视频资产不可用"})
            continue
        metadata = _safe_dict(asset.metadata_json)
        source = _text(metadata.get("source"), 80)
        owned_by_job = _text(metadata.get("media_job_id"), 50) == job.id
        if source == "governed_dialogue_mux" and owned_by_job:
            # A dialogue delivery replaces the H3 clean plate with a governed
            # mux. Do not weaken the normal source allowlist: prove the mux
            # points back to the immutable H3 asset for this exact job before
            # allowing the visual model to read it.
            clean_plate_id = _text(metadata.get("clean_plate_asset_id"), 50)
            clean_plate = await db.get(ProjectRunAsset, clean_plate_id) if clean_plate_id else None
            clean_plate_metadata = _safe_dict(getattr(clean_plate, "metadata_json", None))
            owned_by_job = bool(
                clean_plate
                and clean_plate.project_id == job.project_id
                and clean_plate_metadata.get("source") == "minimax_h3"
                and _text(clean_plate_metadata.get("media_job_id"), 50) == job.id
            )
        if source not in {"minimax_h3", "video_enhancement", "governed_dialogue_mux"} or not owned_by_job:
            results.append({"asset_id": asset_id, "asset_index": index, "status": "failed", "error": "资产不属于当前 H3 任务"})
            continue
        try:
            model, analysis = await _call_media_quality_model(db, user, run, job, asset)
            models.append(model)
            provider = _text(_safe_dict(analysis.get("runtime")).get("provider"), 120)
            if provider:
                providers.append(provider)
            results.append({
                "asset_id": asset.id,
                "asset_index": index,
                "sha256": asset.sha256,
                "status": "completed",
                "model": model,
                "analysis": analysis,
            })
        except Exception as exc:  # noqa: BLE001
            results.append({
                "asset_id": asset.id,
                "asset_index": index,
                "sha256": asset.sha256,
                "status": "failed",
                "error": redact_secret_text(exc, limit=800) or type(exc).__name__,
            })
    successful = [item for item in results if item.get("status") == "completed"]
    if not successful:
        raise RuntimeError("；".join(_text(item.get("error"), 300) for item in results if item.get("error")) or "视频质量分析失败")
    scores = [float(_safe_dict(item.get("analysis")).get("overall_score") or 0) for item in successful]
    applicable_scores = [
        float(
            _safe_dict(item.get("analysis")).get("applicable_overall_score")
            or _safe_dict(item.get("analysis")).get("overall_score")
            or 0
        )
        for item in successful
    ]
    first_analysis = _safe_dict(successful[0].get("analysis"))
    repair_plan = _build_media_repair_plan(job, successful)
    return {
        "status": "completed" if len(successful) == len(results) else "partial",
        "policy_version": MEDIA_QUALITY_ANALYSIS_POLICY_VERSION,
        "model_profile": "vision",
        "model": models[0] if models and len(set(models)) == 1 else ", ".join(sorted(set(models))),
        "provider": providers[0] if providers and len(set(providers)) == 1 else ", ".join(sorted(set(providers))),
        "model_origin": "platform_vision_profile",
        "project_fine_tuned": False,
        "deployment_id": None,
        "artifact_id": None,
        "asset_fingerprint": _media_quality_asset_fingerprint(job.result_json),
        "overall_score": round(sum(scores) / len(scores), 3),
        "applicable_overall_score": round(sum(applicable_scores) / len(applicable_scores), 3),
        "dimension_applicability": _safe_dict(first_analysis.get("dimension_applicability")),
        "dimension_applicability_reasons": _safe_dict(first_analysis.get("dimension_applicability_reasons")),
        "applicable_dimension_count": first_analysis.get("applicable_dimension_count"),
        "material_stage": _safe_dict(first_analysis.get("material_stage")),
        "assets": results,
        "repair_plan": repair_plan,
        "repair_advice": "；".join(repair_plan.get("constraints") or repair_plan.get("issue_summary") or [])[:1600],
        "completed_at": isoformat_bjt(now_bjt()),
        "advisory_only": True,
        "requires_human_review": True,
    }


async def process_media_quality_analyses_once(db: AsyncSession, *, limit: int = 1) -> dict[str, int]:
    """Advance AI quality analysis independently from the GPU generation loop."""

    quality_status = MediaGenerationJob.result_json["quality_analysis"]["status"].astext
    rows = (
        await db.execute(
            select(MediaGenerationJob)
            .where(
                MediaGenerationJob.status.in_(MEDIA_QUALITY_ANALYSIS_STATUSES),
                quality_status.in_(["queued", "running"]),
            )
            .order_by(
                case((quality_status == "queued", 0), else_=1).asc(),
                MediaGenerationJob.updated_at.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(max(1, min(int(limit), 4)))
        )
    ).scalars().all()
    stats = {"scanned": len(rows), "completed": 0, "failed": 0, "skipped": 0}
    for job in rows:
        quality = _safe_dict(_safe_dict(job.result_json).get("quality_analysis"))
        analysis_id = _text(quality.get("id"), 80)
        if quality.get("status") == "running":
            started_raw = _text(quality.get("started_at"), 80)
            try:
                started_at = parse_bjt_datetime(started_raw) if started_raw else None
            except (TypeError, ValueError):
                started_at = None
            if started_at and now_bjt() - started_at < timedelta(seconds=MEDIA_QUALITY_ANALYSIS_STALE_SECONDS):
                stats["skipped"] += 1
                continue
        quality = {**quality, "status": "running", "started_at": isoformat_bjt(now_bjt()), "error": None}
        job.result_json = {**_safe_dict(job.result_json), "quality_analysis": quality}
        job.updated_at = now_bjt()
        await db.commit()
        try:
            run = await db.get(ProjectRun, job.project_run_id)
            user = await db.get(User, job.requested_by) if job.requested_by else None
            if run is None or user is None:
                raise RuntimeError("质量分析缺少可追溯的项目运行或提交人")
            completed = await _run_media_quality_analysis(db, user, run, job)
            current = _safe_dict(_safe_dict(job.result_json).get("quality_analysis"))
            if analysis_id and _text(current.get("id"), 80) != analysis_id:
                stats["skipped"] += 1
                continue
            job.result_json = {
                **_safe_dict(job.result_json),
                "quality_analysis": {**completed, "id": analysis_id, "history": _safe_list(current.get("history"))[-3:]},
            }
            job.updated_at = now_bjt()
            stats["completed"] += 1
        except Exception as exc:  # noqa: BLE001
            current = _safe_dict(_safe_dict(job.result_json).get("quality_analysis"))
            job.result_json = {
                **_safe_dict(job.result_json),
                "quality_analysis": {
                    **current,
                    "status": "failed",
                    "error": redact_secret_text(exc, limit=1000) or type(exc).__name__,
                    "completed_at": isoformat_bjt(now_bjt()),
                    "advisory_only": True,
                    "requires_human_review": True,
                },
            }
            job.updated_at = now_bjt()
            stats["failed"] += 1
            logger.warning("素材成片 AI 质量分析失败 job={} err={}", job.id, exc, exc_info=True)
        await db.commit()
    return stats


async def _refresh_job(db: AsyncSession, user: User, run: ProjectRun, job: MediaGenerationJob) -> MediaGenerationAttempt | None:
    attempt = await _latest_attempt(db, job.id)
    if job.status == "queued":
        await _dispatch_job(db, run, job)
        return await _latest_attempt(db, job.id)
    if job.status not in {"assigned", "running", "collecting"} or not attempt or not attempt.instance_id:
        return attempt
    if not bridge_registry.is_online(attempt.instance_id):
        current_time = now_bjt()
        metrics = _safe_dict(attempt.metrics_json)
        observed_raw = _text(metrics.get("bridge_disconnect_observed_at"), 80)
        try:
            observed_at = parse_bjt_datetime(observed_raw) if observed_raw else None
        except (TypeError, ValueError):
            observed_at = None
        if observed_at is None:
            metrics["bridge_disconnect_observed_at"] = isoformat_bjt(current_time)
            attempt.metrics_json = metrics
            job.error = MEDIA_BRIDGE_RECONNECT_WAIT_MESSAGE
            return attempt
        if current_time - observed_at < timedelta(seconds=MEDIA_BRIDGE_DISCONNECT_GRACE_SECONDS):
            job.error = MEDIA_BRIDGE_RECONNECT_WAIT_MESSAGE
            return attempt
        attempt.status = "disconnected"
        attempt.error = "Bridge disconnected"
        attempt.completed_at = current_time
        job.assigned_instance_id = None
        transition_media_job(job, "queued", error="媒体节点断线，已自动退回队列。")
        await _dispatch_job(db, run, job)
        return await _latest_attempt(db, job.id)
    metrics = _safe_dict(attempt.metrics_json)
    if metrics.pop("bridge_disconnect_observed_at", None) is not None:
        attempt.metrics_json = metrics
        if job.error == MEDIA_BRIDGE_RECONNECT_WAIT_MESSAGE:
            job.error = None
    client = AIClawClient(attempt.instance_id)
    try:
        remote = await client.get_media_job({"job_id": job.id}, timeout=30)
    except Exception as exc:  # noqa: BLE001
        job.error = _text(exc, 2000)
        return attempt
    remote_status = str(remote.get("status") or "running").lower()
    remote_error_code = _text(remote.get("error_code"), 120)
    attempt.metrics_json = {
        **(attempt.metrics_json or {}),
        **_safe_dict(remote.get("metrics")),
        "last_remote_probe_at": isoformat_bjt(now_bjt()),
        "remote_status": remote_status,
        "remote_updated_at": _text(remote.get("updated_at"), 80) or None,
        "remote_recovery": _safe_dict(remote.get("recovery")),
    }
    attempt.log_tail = _text(remote.get("log_tail"), 16000) or attempt.log_tail
    if job.error == MEDIA_BRIDGE_RECONNECT_WAIT_MESSAGE:
        job.error = None
    if remote_status in {"orphaned", "not_found"} or remote_error_code == MEDIA_PROMPT_ORPHAN_ERROR_CODE:
        current_time = now_bjt()
        attempt.status = "disconnected"
        attempt.error = _text(remote.get("error"), 4000) or "Bridge 上的 ComfyUI 任务记录已丢失"
        attempt.completed_at = current_time
        if int(attempt.attempt_no or job.current_attempt_no or 1) >= MEDIA_ORPHAN_RECOVERY_MAX_ATTEMPTS:
            logger.error(
                "素材任务连续丢失，停止自动重派 job={} attempt={} instance={} remote_status={}",
                job.id,
                attempt.attempt_no,
                attempt.instance_id,
                remote_status,
            )
            transition_media_job(job, "failed", error="节点任务连续丢失，已停止自动重派：" + attempt.error)
            return attempt
        logger.warning(
            "素材任务在节点上成为孤儿任务，使用同一幂等键自动重派 job={} attempt={} instance={} remote_status={}",
            job.id,
            attempt.attempt_no,
            attempt.instance_id,
            remote_status,
        )
        job.assigned_instance_id = None
        transition_media_job(job, "queued", error="节点任务已从 ComfyUI 队列和历史中丢失，正在使用同一幂等键自动重派。")
        await _dispatch_job(db, run, job)
        return await _latest_attempt(db, job.id)
    if remote_status in {"queued", "pending"}:
        attempt.status = "assigned"
    elif remote_status in {"running", "executing"}:
        attempt.status = "running"
        if job.status == "assigned":
            transition_media_job(job, "running")
    elif remote_status in {"failed", "error"}:
        attempt.status = "failed"
        attempt.error = _text(remote.get("error"), 4000)
        attempt.completed_at = now_bjt()
        transition_media_job(job, "failed", error=attempt.error or "H3 generation failed")
    elif remote_status in {"completed", "succeeded", "success"}:
        if job.status != "collecting":
            transition_media_job(job, "collecting")
        from app.projects.service import upload_project_run_asset

        asset_run = await db.get(ProjectRun, job.project_run_id)
        if asset_run is None or asset_run.project_id != job.project_id:
            asset_run = run

        first_meta = await client.collect_media_result({"job_id": job.id, "result_index": 0}, timeout=60)
        result_count = max(1, min(int(first_meta.get("result_count") or 1), int((job.params_json or {}).get("batch_count") or 1), 8))
        assets = []
        result_metas = []
        for result_index in range(result_count):
            meta = first_meta if result_index == 0 else await client.collect_media_result(
                {"job_id": job.id, "result_index": result_index}, timeout=60
            )
            media_probe = _safe_dict(meta.get("media_probe"))
            technical_validation = _generated_media_technical_validation(media_probe, job.params_json)
            meta = {**meta, "media_probe": media_probe, "technical_validation": technical_validation}
            if result_index == 0:
                first_meta = meta
            content = await _collect_result_bytes(client, job, meta, result_index=result_index)
            uploaded = await upload_project_run_asset(
                db,
                user,
                asset_run.id,
                file_name=_text(meta.get("file_name"), 240) or f"{job.id}-{result_index + 1:02d}.mp4",
                mime_type=_text(meta.get("mime_type"), 120) or "video/mp4",
                content=content,
                metadata={
                    "source": (
                        "video_enhancement" if job.mode == "video_enhance"
                        else "video_local_edit" if job.mode == "video_local_edit"
                        else "minimax_h3"
                    ),
                    "media_job_id": job.id,
                    "batch_index": result_index,
                    "instance_id": attempt.instance_id,
                    "model_version": meta.get("model_version"),
                    "model_sha256": meta.get("model_sha256"),
                    "workflow_version": meta.get("workflow_version"),
                    "media_probe": media_probe,
                    "technical_validation": technical_validation,
                    "postprocess_provenance": _safe_dict(meta.get("postprocess_provenance")),
                },
            )
            asset = _safe_dict(uploaded.get("asset"))
            assets.append(asset)
            result_metas.append({key: value for key, value in meta.items() if key != "content_base64"})
        primary_asset = assets[0] if assets else {}
        job.result_asset_id = _text(primary_asset.get("id"), 50)
        job.result_sha256 = _text(first_meta.get("sha256"), 64)
        job.result_json = _merge_collected_media_result(
            job.result_json,
            first_meta,
            primary_asset=primary_asset,
            assets=assets,
            result_metas=result_metas,
        )
        if _dialogue_delivery_requested(job) and job.result_asset_id:
            # H3 only produces the silent visual plate for governed dialogue.
            # Keep that immutable plate as the review source and let visual
            # quality analysis finish before a user may start the billable TTS
            # and mux stage.  Auto-rendering speech here used to race the visual
            # gate and could spend money on a plate that would later be rejected.
            _mark_dialogue_delivery_awaiting_visual_gate(job, primary_asset)
        job.result_json, _ = _queue_media_quality_analysis(
            job.result_json,
            requested_by=job.requested_by,
            automatic=True,
        )
        job.model_version = _text(first_meta.get("model_version"), 180) or job.model_version
        job.model_sha256 = _text(first_meta.get("model_sha256"), 64) or job.model_sha256
        job.workflow_version = _text(first_meta.get("workflow_version"), 80) or job.workflow_version
        failed_validations = [
            _safe_dict(item.get("technical_validation"))
            for item in result_metas
            if _safe_dict(item.get("technical_validation")).get("status") == "failed"
        ]
        if failed_validations:
            issues = [
                str(issue)
                for validation in failed_validations
                for issue in _safe_list(validation.get("issues"))
            ]
            attempt.status = "failed"
            attempt.error = _text("；".join(issues), 4000) or "生成媒体技术验收失败"
            attempt.completed_at = now_bjt()
            transition_media_job(job, "failed", error=attempt.error)
            return attempt
        attempt.status = "completed"
        attempt.completed_at = now_bjt()
        transition_media_job(job, "awaiting_review")
        try:
            from .fde_v4 import _materialize_candidates, _refresh_request_status

            method = "direct" if (job.creative_option or "") == "direct_original" else "historical"
            created = await _materialize_candidates(db, None, [{"id": job.id}], generation_method=method)
            for candidate in created:
                if candidate.request_id:
                    await _refresh_request_status(db, candidate.request_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("素材候选落库失败 job={} err={}", job.id, exc)
        try:
            from .workbench_v2 import _ensure_review_media_manifest

            result_asset = await db.get(ProjectRunAsset, job.result_asset_id) if job.result_asset_id else None
            await _ensure_review_media_manifest(db, user, asset_run, job, result_asset)
        except Exception as exc:  # noqa: BLE001
            # A missing poster must not lose an otherwise valid generated video.
            # Review list will show an explicit placeholder and allow regeneration.
            logger.warning("审片封面和关键帧预生成失败 job={} err={}", job.id, exc)
    return attempt


async def process_media_jobs_once(db: AsyncSession, *, limit: int = 20) -> dict[str, int]:
    """Advance queued/running media jobs without relying on an open browser.

    This runs only in SkillForge's elected scheduler worker.  The database row
    lock prevents two control-plane workers from assigning the same job while
    the Bridge operation remains idempotent on ``job_id + idempotency_key``.
    """

    rows = (
        await db.execute(
            select(MediaGenerationJob)
            .where(MediaGenerationJob.status.in_(["queued", "assigned", "running", "collecting"]))
            .order_by(
                MediaGenerationJob.priority.desc(),
                case((MediaGenerationJob.mode == "reference_replay", 1), else_=0).desc(),
                MediaGenerationJob.created_at.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(max(1, min(int(limit), 100)))
        )
    ).scalars().all()
    stats = {"scanned": len(rows), "advanced": 0, "failed": 0}
    for job in rows:
        before = (job.status, job.assigned_instance_id, job.current_attempt_no, job.updated_at)
        try:
            current_time = now_bjt()
            if job.status == "queued" and job.not_before_at and job.not_before_at > current_time:
                continue
            if job.status == "queued" and job.deadline_at and job.deadline_at <= current_time:
                transition_media_job(job, "cancelled", error="生产截止时间已到，未开始任务已自动取消。")
                if job.production_batch_id:
                    batch = await db.get(MediaProductionBatch, job.production_batch_id)
                    if batch and batch.status not in {"cancelled", "completed"}:
                        batch.status = "deadline_reached"
                        batch.updated_at = current_time
                stats["advanced"] += 1
                await db.commit()
                continue
            if job.status == "queued" and job.depends_on_job_id:
                dependency = await db.get(MediaGenerationJob, job.depends_on_job_id)
                if dependency is None or dependency.status in {"failed", "cancelled", "rejected"}:
                    transition_media_job(job, "failed", error="前序视频任务失败或不存在，续时链已停止。")
                    stats["failed"] += 1
                    await db.commit()
                    continue
                if dependency.status not in {"awaiting_review", "approved", "syncing", "synced"}:
                    continue
            # A continuous-production campaign is a time window, not permission
            # to drain its entire pre-created queue after the requested cutoff.
            # Let already assigned/running work finish, but never start another
            # queued candidate once the campaign deadline has passed.
            if job.status == "queued" and _continuous_campaign_expired(job):
                transition_media_job(
                    job,
                    "cancelled",
                    error="连续生产截止时间已到，未开始任务已自动取消。",
                )
                stats["advanced"] += 1
                await db.commit()
                continue
            run = await db.get(ProjectRun, job.project_run_id)
            user = await db.get(User, job.requested_by) if job.requested_by else None
            if run is None or user is None:
                job.error = "素材任务缺少可追溯的项目运行或提交人，已停止自动推进。"
                job.updated_at = now_bjt()
                stats["failed"] += 1
                continue
            await _refresh_job(db, user, run, job)
            after = (job.status, job.assigned_instance_id, job.current_attempt_no, job.updated_at)
            if after != before:
                stats["advanced"] += 1
        except Exception as exc:  # noqa: BLE001
            job.error = _text(getattr(exc, "detail", None) or exc, 4000)
            job.updated_at = now_bjt()
            stats["failed"] += 1
            logger.warning("素材任务后台推进失败 job={} err={}", job.id, exc, exc_info=True)
        await db.commit()
    try:
        from .workbench_v2 import advance_continuations_once

        await advance_continuations_once(db, limit=max(1, min(limit, 20)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("素材续时链后台推进失败 err={}", exc, exc_info=True)
    try:
        from .workbench_v3 import advance_replays_once

        await advance_replays_once(db, limit=max(1, min(limit, 20)))
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("素材复刻项目后台推进失败 err={}", exc, exc_info=True)
    return stats


_DIALOGUE_VISUAL_GATE_PROFILES = {
    "clean_dialogue_plate_silent_v1",
    "clean_people_plate_silent_v4",
    "direct_clean_people_plate_silent_v1",
    "motion_only_no_text_ascii_v3",
    "product_replacement_no_text_ascii_v1",
}


def _dialogue_visual_policy_gate_state(job: MediaGenerationJob) -> dict[str, Any]:
    """Return the authoritative no-text visual gate for dialogue delivery."""

    prompt = _safe_dict(job.prompt_json)
    execution_profile = _text(prompt.get("h3_execution_profile"), 120)
    guardrails = _safe_dict(prompt.get("brand_guardrails"))
    required = (
        execution_profile in _DIALOGUE_VISUAL_GATE_PROFILES
        or _text(guardrails.get("reference_text_policy"), 80) == "suppress_all_source_text"
    )
    if not required:
        return {"required": False, "passed": True, "status": "not_required", "findings": []}
    quality = _safe_dict(_safe_dict(job.result_json).get("quality_analysis"))
    visual_gates = [
        _safe_dict(_safe_dict(item).get("analysis")).get("visual_policy_gate")
        for item in _safe_list(quality.get("assets"))
        if _safe_dict(item).get("status") == "completed"
    ]
    visual_gates = [_safe_dict(item) for item in visual_gates if isinstance(item, dict)]
    ready = quality.get("status") in {"completed", "partial"} and bool(visual_gates)
    passed = bool(ready and all(gate.get("passed") is True for gate in visual_gates))
    findings = [
        finding
        for gate in visual_gates
        for finding in _safe_list(gate.get("findings"))
        if isinstance(finding, dict)
    ]
    return {
        "required": True,
        "passed": passed,
        "status": "passed" if passed else "failed" if ready else "pending",
        "findings": findings,
    }


def _dialogue_delivery_auto_requested(job: MediaGenerationJob) -> bool:
    delivery = _safe_dict(_safe_dict(job.prompt_json).get("dialogue_delivery"))
    return bool(
        _dialogue_delivery_requested(job)
        and delivery.get("auto_finalize") is True
        and delivery.get("delivery_mode") == "auto_after_visual_gate"
    )


def _dialogue_delivery_requested(job: MediaGenerationJob) -> bool:
    prompt = _safe_dict(job.prompt_json)
    delivery = _safe_dict(prompt.get("dialogue_delivery"))
    return delivery.get("requested") is True and bool(_text(prompt.get("requested_audio_prompt"), 6000))


def _dialogue_delivery_duration(job: MediaGenerationJob) -> float:
    result = _safe_dict(job.result_json)
    technical = _safe_dict(result.get("technical_validation"))
    try:
        duration = float(technical.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration > 0:
        return min(60.0, duration)
    params = _safe_dict(job.params_json)
    try:
        return min(60.0, max(1.0, float(params.get("delivery_duration_seconds") or 0) or (
            float(params.get("frames") or 0) / max(float(params.get("fps") or 0), 1.0)
        )))
    except (TypeError, ValueError, ZeroDivisionError):
        return 5.0


def _mark_dialogue_delivery_awaiting_visual_gate(
    job: MediaGenerationJob,
    clean_plate_asset: dict[str, Any],
) -> None:
    """Preserve the H3 plate until visual gates allow governed voiceover.

    This transition intentionally performs no provider call.  The review
    service is the single authority that exposes the manual voiceover action
    after the visual policy gate passes.
    """

    prompt_delivery = _safe_dict(_safe_dict(job.prompt_json).get("dialogue_delivery"))
    existing = _safe_dict(_safe_dict(job.result_json).get("dialogue_delivery"))
    job.result_json = {
        **_safe_dict(job.result_json),
        "clean_plate_asset": dict(clean_plate_asset),
        "dialogue_delivery": {
            **prompt_delivery,
            **existing,
            "status": "awaiting_visual_gate",
            "renderer": SPEECH_DELIVERY_RENDERER_ID,
            "policy_version": SPEECH_DELIVERY_POLICY_VERSION,
            "error": None,
            "queued_at": isoformat_bjt(now_bjt()),
        },
    }
    job.updated_at = now_bjt()


async def _finalize_governed_dialogue(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    job: MediaGenerationJob,
    clean_plate_asset: ProjectRunAsset,
) -> dict[str, Any]:
    """Render, persist and verify the independent voiceover delivery.

    The immutable H3 clean plate remains in lineage.  Only a newly uploaded
    muxed asset may replace ``result_asset_id`` and satisfy the review gate.
    """

    from app.projects import service as project_service

    prompt = _safe_dict(job.prompt_json)
    script = _text(prompt.get("requested_audio_prompt"), 6000)
    if not _dialogue_delivery_requested(job) or not script:
        return {"status": "not_required"}
    existing = _safe_dict(_safe_dict(job.result_json).get("dialogue_delivery"))
    existing_completed = bool(
        existing.get("status") in {"completed", "delivered"}
        and _text(existing.get("renderer"), 180)
        and _text(existing.get("audio_asset_id"), 50)
        and _text(existing.get("final_asset_id"), 50)
    )
    existing_transcription = _safe_dict(existing.get("transcription"))
    if (
        existing_completed
        and existing.get("policy_version") == SPEECH_DELIVERY_POLICY_VERSION
        and existing_transcription.get("passed") is True
    ):
        return existing
    if existing_completed:
        # Upgrade an already muxed v6 asset in place.  Re-running TTS would be
        # billable and could change voices/timing; the immutable WAV artifact is
        # the authoritative source for the new v7 ASR gate.
        existing_audio_asset = await db.get(ProjectRunAsset, _text(existing.get("audio_asset_id"), 50))
        if existing_audio_asset is None or existing_audio_asset.project_id != job.project_id:
            raise SpeechDeliveryError("SPEECH_TRANSCRIPTION_AUDIO_MISSING", "历史配音音频不存在，无法校验台词")
        try:
            transcription = await transcribe_governed_dialogue(
                audio_path=project_service._project_run_asset_abs_path(existing_audio_asset),  # noqa: SLF001
                expected_script=script,
            )
        except SpeechDeliveryError as exc:
            transcription = {
                "status": "failed",
                "passed": False,
                "provider": "SiliconFlow",
                "error_code": exc.code,
                "error": redact_secret_text(exc, limit=500),
            }
        checked_at = isoformat_bjt(now_bjt())
        upgraded = {
            **existing,
            "status": "completed",
            "policy_version": SPEECH_DELIVERY_POLICY_VERSION,
            "transcription": transcription,
            "transcription_checked_at": checked_at,
            **({"transcription_verified_at": checked_at} if transcription.get("passed") is True else {}),
            "error": None,
        }
        job.result_json = {**_safe_dict(job.result_json), "dialogue_delivery": upgraded}
        job.updated_at = now_bjt()
        return upgraded

    started_at = isoformat_bjt(now_bjt())
    asset_owner = await db.get(User, run.user_id) if run.user_id else None
    if asset_owner is None:
        raise SpeechDeliveryError("SPEECH_DELIVERY_OWNER_MISSING", "原生产任务提交人不存在，无法保存配音成片血缘")
    delivery_actor_id = _text(getattr(user, "id", ""), 50) or None
    job.result_json = {
        **_safe_dict(job.result_json),
        "clean_plate_asset": project_service.serialize_run_asset(clean_plate_asset),
        "dialogue_delivery": {
            **_safe_dict(prompt.get("dialogue_delivery")),
            **existing,
            "status": "rendering",
            "renderer": SPEECH_DELIVERY_RENDERER_ID,
            "policy_version": SPEECH_DELIVERY_POLICY_VERSION,
            "started_at": started_at,
            "error": None,
        },
    }
    rendered = await render_governed_dialogue(
        script=script,
        video_path=project_service._project_run_asset_abs_path(clean_plate_asset),  # noqa: SLF001
        target_duration_seconds=_dialogue_delivery_duration(job),
        performance_timeline=prompt.get("performance_timeline"),
        performance_profile=_safe_dict(prompt.get("ad_material_contract")).get("performance_profile"),
    )
    safe_delivery = {
        key: value for key, value in rendered.items()
        if key not in {"audio_content", "video_content"}
    }
    audio_upload = await project_service.upload_project_run_asset(
        db,
        asset_owner,
        run.id,
        file_name=f"{job.id}-governed-dialogue.wav",
        mime_type="audio/wav",
        content=rendered["audio_content"],
        metadata={
            "source": "governed_dialogue_audio",
            "media_job_id": job.id,
            "renderer": rendered["renderer"],
            "policy_version": rendered["policy_version"],
            "provider": rendered["provider"],
            "model": rendered["model"],
            "script_sha256": rendered["script_sha256"],
            "request_fingerprint": rendered["request_fingerprint"],
            "delivered_by": delivery_actor_id,
        },
    )
    audio_asset = _safe_dict(audio_upload.get("asset"))
    final_upload = await project_service.upload_project_run_asset(
        db,
        asset_owner,
        run.id,
        file_name=f"{job.id}-dialogue-delivered.mp4",
        mime_type="video/mp4",
        content=rendered["video_content"],
        metadata={
            "source": "governed_dialogue_mux",
            "media_job_id": job.id,
            "renderer": rendered["renderer"],
            "policy_version": rendered["policy_version"],
            "provider": rendered["provider"],
            "model": rendered["model"],
            "clean_plate_asset_id": clean_plate_asset.id,
            "audio_asset_id": audio_asset.get("id"),
            "request_fingerprint": rendered["request_fingerprint"],
            "delivered_by": delivery_actor_id,
        },
    )
    final_asset = _safe_dict(final_upload.get("asset"))
    final_probe = _safe_dict(_safe_dict(final_asset.get("metadata")).get("media_probe"))
    if not _safe_list(final_probe.get("streams")) and _safe_list(_safe_dict(rendered.get("mux")).get("verified_streams")):
        final_probe = {
            **final_probe,
            "status": "ok",
            "source": "governed_ffmpeg_decode_validation",
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": _text(_safe_dict(rendered.get("mux")).get("video_codec"), 60),
                    "width": int(_safe_dict(job.params_json).get("width") or 0),
                    "height": int(_safe_dict(job.params_json).get("height") or 0),
                },
                {
                    "codec_type": "audio",
                    "codec_name": _text(_safe_dict(rendered.get("mux")).get("audio_codec"), 60),
                },
            ],
        }
        final_asset["metadata"] = {**_safe_dict(final_asset.get("metadata")), "media_probe": final_probe}
        final_asset_row = await db.get(ProjectRunAsset, _text(final_asset.get("id"), 50))
        if final_asset_row is not None:
            final_asset_row.metadata_json = {
                **_safe_dict(final_asset_row.metadata_json),
                "media_probe": final_probe,
            }
    final_validation = _generated_media_technical_validation(
        final_probe,
        {**_safe_dict(job.params_json), "audio_enabled": True},
    )
    if final_validation.get("status") != "passed" or not _text(final_validation.get("audio_codec"), 60):
        issues = "；".join(str(item) for item in _safe_list(final_validation.get("issues")))
        raise SpeechDeliveryError(
            "SPEECH_FINAL_VALIDATION_FAILED",
            issues or "独立配音成片没有通过最终音视频流检测",
        )
    final_asset_metadata = {
        **_safe_dict(final_asset.get("metadata")),
        "media_probe": final_probe,
        "technical_validation": final_validation,
        "duration_seconds": final_validation.get("duration_seconds"),
    }
    final_asset["metadata"] = final_asset_metadata
    final_asset_row = await db.get(ProjectRunAsset, _text(final_asset.get("id"), 50))
    if final_asset_row is not None:
        final_asset_row.metadata_json = {
            **_safe_dict(final_asset_row.metadata_json),
            "media_probe": final_probe,
            "technical_validation": final_validation,
            "duration_seconds": final_validation.get("duration_seconds"),
        }
    completed_at = isoformat_bjt(now_bjt())
    transcription = _safe_dict(safe_delivery.get("transcription"))
    completed = {
        **safe_delivery,
        "status": "completed",
        "artifact_id": final_asset.get("id"),
        "audio_asset_id": audio_asset.get("id"),
        "final_asset_id": final_asset.get("id"),
        "clean_plate_asset_id": clean_plate_asset.id,
        "completed_at": completed_at,
        "has_audio_stream": True,
        "transcription_checked_at": completed_at,
        **({"transcription_verified_at": completed_at} if transcription.get("passed") is True else {}),
        "error": None,
    }
    job.result_asset_id = _text(final_asset.get("id"), 50)
    job.result_sha256 = _text(final_asset.get("sha256"), 64)
    job.poster_asset_id = None
    job.result_json = {
        **_safe_dict(job.result_json),
        "asset": final_asset,
        "assets": [final_asset],
        "dialogue_audio_asset": audio_asset,
        "dialogue_delivery": completed,
        "technical_validation": final_validation,
        "review_manifest": {"status": "pending", "source_sha256": final_asset.get("sha256")},
    }
    job.updated_at = now_bjt()
    return completed


def _record_dialogue_delivery_failure(job: MediaGenerationJob, exc: Exception) -> None:
    prompt_delivery = _safe_dict(_safe_dict(job.prompt_json).get("dialogue_delivery"))
    existing = _safe_dict(_safe_dict(job.result_json).get("dialogue_delivery"))
    code = exc.code if isinstance(exc, SpeechDeliveryError) else "SPEECH_DELIVERY_FAILED"
    job.result_json = {
        **_safe_dict(job.result_json),
        "dialogue_delivery": {
            **prompt_delivery,
            **existing,
            "status": "configuration_required" if code in {
                "SPEECH_RENDERER_DISABLED", "SPEECH_CONFIG_MISSING", "SPEECH_ENDPOINT_NOT_ALLOWED",
            } else "failed",
            "renderer": SPEECH_DELIVERY_RENDERER_ID,
            "policy_version": SPEECH_DELIVERY_POLICY_VERSION,
            "error_code": code,
            "error": redact_secret_text(exc, limit=800),
            "failed_at": isoformat_bjt(now_bjt()),
        },
    }
    job.updated_at = now_bjt()


async def process_dialogue_deliveries_once(db: AsyncSession, *, limit: int = 1) -> dict[str, int]:
    """Automatically finish explicitly requested dialogue after visual QA.

    The production form's final-audio switch is the authorization boundary.
    H3 collection never calls the billable renderer.  This worker only picks a
    v58 job whose immutable clean plate has passed the no-text visual gate,
    marks it durable before the provider call, then muxes and requeues quality
    analysis for the final asset.  Failures stay manual-retryable and are never
    spent repeatedly by the background loop.
    """

    dialogue_status = MediaGenerationJob.result_json["dialogue_delivery"]["status"].astext
    auto_finalize = MediaGenerationJob.prompt_json["dialogue_delivery"]["auto_finalize"].as_boolean()
    rows = (
        await db.execute(
            select(MediaGenerationJob)
            .where(
                MediaGenerationJob.status.in_({"awaiting_review", "rejected"}),
                dialogue_status.in_(["awaiting_visual_gate", "auto_rendering"]),
                auto_finalize.is_(True),
            )
            .order_by(MediaGenerationJob.updated_at.asc())
            .with_for_update(skip_locked=True)
            .limit(max(1, min(int(limit), 2)))
        )
    ).scalars().all()
    stats = {"scanned": len(rows), "completed": 0, "failed": 0, "waiting": 0, "skipped": 0}
    for job in rows:
        if not _dialogue_delivery_auto_requested(job):
            stats["skipped"] += 1
            continue
        current_delivery = _safe_dict(_safe_dict(job.result_json).get("dialogue_delivery"))
        if current_delivery.get("status") == "auto_rendering":
            started_raw = _text(current_delivery.get("auto_started_at"), 80)
            try:
                started_at = parse_bjt_datetime(started_raw) if started_raw else None
            except (TypeError, ValueError):
                started_at = None
            if started_at and now_bjt() - started_at < timedelta(seconds=MEDIA_DIALOGUE_AUTO_STALE_SECONDS):
                stats["waiting"] += 1
                continue
            _record_dialogue_delivery_failure(
                job,
                SpeechDeliveryError(
                    "SPEECH_DELIVERY_INTERRUPTED",
                    "自动配音进程中断或超时，已停止自动重试；可在审片中心复用原静音底片重试",
                ),
            )
            stats["failed"] += 1
            await db.commit()
            continue
        visual_gate = _dialogue_visual_policy_gate_state(job)
        if visual_gate.get("passed") is not True:
            stats["waiting"] += 1
            continue
        run = await db.get(ProjectRun, job.project_run_id)
        user = await db.get(User, job.requested_by) if job.requested_by else None
        result = _safe_dict(job.result_json)
        clean_plate_asset_id = _text(_safe_dict(result.get("clean_plate_asset")).get("id"), 50)
        clean_plate_asset = await db.get(ProjectRunAsset, clean_plate_asset_id) if clean_plate_asset_id else None
        if (
            run is None
            or user is None
            or clean_plate_asset is None
            or clean_plate_asset.project_id != job.project_id
        ):
            error = SpeechDeliveryError(
                "SPEECH_DELIVERY_LINEAGE_MISSING",
                "自动配音缺少可追溯的项目运行、提交人或静音底片",
            )
            _record_dialogue_delivery_failure(job, error)
            stats["failed"] += 1
            await db.commit()
            continue
        current_delivery = _safe_dict(result.get("dialogue_delivery"))
        job.result_json = {
            **result,
            "dialogue_delivery": {
                **current_delivery,
                "status": "auto_rendering",
                "auto_started_at": isoformat_bjt(now_bjt()),
                "error": None,
            },
        }
        job.updated_at = now_bjt()
        # Persist the claim before the provider call so another API worker
        # cannot spend the same dialogue request concurrently.
        await db.commit()
        try:
            await _finalize_governed_dialogue(db, user, run, job, clean_plate_asset)
            job.result_json, _ = _queue_media_quality_analysis(
                job.result_json,
                requested_by=job.requested_by,
                automatic=True,
                force=True,
            )
            stats["completed"] += 1
        except Exception as exc:  # noqa: BLE001
            _record_dialogue_delivery_failure(job, exc)
            stats["failed"] += 1
            logger.warning("素材对白自动交付失败 job={} err={}", job.id, exc, exc_info=True)
        await db.commit()
    return stats


def _continuous_campaign_deadline(job: MediaGenerationJob) -> datetime | None:
    campaign = _safe_dict((job.prompt_json or {}).get("production_campaign"))
    raw_deadline = _text(campaign.get("target_end_at"), 50)
    if not raw_deadline:
        return None
    try:
        return parse_bjt_datetime(raw_deadline)
    except (TypeError, ValueError):
        # Submission validates this field. Treat old/corrupt records as ordinary
        # jobs instead of unexpectedly cancelling them.
        return None


def _continuous_campaign_expired(
    job: MediaGenerationJob, *, current_time: datetime | None = None
) -> bool:
    deadline = _continuous_campaign_deadline(job)
    return deadline is not None and deadline <= (current_time or now_bjt())


def _training_rights_eligible(rights: dict[str, Any]) -> tuple[bool, list[str]]:
    required = {
        "copyright_authorized": True,
        "portrait_authorized": True,
        "voice_authorized": True,
        "malware_scan": "passed",
        "sensitive_data_scan": "passed",
        "redaction": "passed",
    }
    missing = [key for key, expected in required.items() if rights.get(key) != expected]
    return not missing, missing


async def _record_approved_training_samples(
    db: AsyncSession, user: User, job: MediaGenerationJob, plan: MediaPlanComparison | None
) -> list[dict[str, Any]]:
    if not job.result_asset_id:
        return []
    from app.training.service import register_training_asset_from_project_run_asset, record_training_sample

    source_result = await register_training_asset_from_project_run_asset(db, user, job.result_asset_id, commit=False)
    source_id = _safe_dict(source_result.get("asset_source")).get("id")
    if not source_id:
        return []
    brief = plan.brief_json if plan else {}
    # Job prompt is the authoritative per-output plan. A batch shares one plan
    # comparison row, but every candidate has its own deterministic variant,
    # seed and campaign index that must not be replaced by the last submitted
    # candidate when training samples are materialized later.
    final_plan = job.prompt_json or (
        plan.final_output_json if plan and plan.final_output_json else plan.baseline_output_json if plan else {}
    )
    generated_assets = [item for item in _safe_list((job.result_json or {}).get("assets")) if isinstance(item, dict)]
    media_refs = [
        {"asset_id": _text(item.get("id"), 50), "sha256": _text(item.get("sha256"), 64)}
        for item in generated_assets
        if _text(item.get("id"), 50)
    ] or [{"asset_id": job.result_asset_id, "sha256": job.result_sha256}]
    review_quality = _safe_dict((job.review_json or {}).get("quality_evaluation"))
    try:
        quality_score = float(review_quality.get("overall_score") or 0.8)
    except (TypeError, ValueError):
        quality_score = 0.8
    quality_score = max(0.0, min(1.0, quality_score))
    common = {
        "source_id": source_id,
        "project_id": job.project_id,
        "project_run_id": job.project_run_id,
        "quality_score": quality_score,
        "sensitivity_level": "internal",
        "status": "ready",
        "metadata": {
            "media_job_id": job.id,
            "rights": job.rights_json or {},
            "quality_evaluation": review_quality,
            "material_stage": _safe_dict(review_quality.get("material_stage")),
            "model_version": job.model_version,
            "model_sha256": job.model_sha256,
            "workflow_version": job.workflow_version,
            "workflow_definition_id": job.workflow_definition_id,
            "workflow_definition_version": job.workflow_definition_version,
            "prompt_policy_version": final_plan.get("prompt_policy_version") or H3_PROMPT_POLICY_VERSION,
            "compiled_prompt_sha256": hashlib.sha256(
                str(final_plan.get("integrated_multimodal_description") or "").encode("utf-8")
            ).hexdigest(),
            "reference_roles": final_plan.get("reference_roles") or [],
            "reference_assets": job.reference_assets_json or [],
            "structured_shots": final_plan.get("shots") or [],
            "compiled_prompt": final_plan.get("integrated_multimodal_description") or final_plan.get("video_prompt"),
            "user_edits": plan.edits_json if plan else {},
            "production_variant": final_plan.get("production_variant") or {},
            "production_campaign": final_plan.get("production_campaign") or {},
        },
    }
    payloads = [
        {
            **common,
            "dataset_profile": "text_sft_v1",
            "content": {"instruction": brief, "response": final_plan, "sample_type": "sft"},
            "labels": ["media_plan", "accepted"],
        },
        {
            **common,
            "dataset_profile": "preference_v1",
            "content": {
                "prompt": brief,
                "chosen": final_plan,
                "rejected": (plan.candidate_output_json if plan and plan.selected_source == "baseline" else plan.baseline_output_json if plan else {}),
                "sample_type": "dpo",
            },
            "labels": ["media_preference", plan.selected_source if plan else "final"],
        },
        {
            **common,
            "dataset_profile": "vision_instruction_v1",
            "content": {
                "instruction": brief,
                "response": final_plan,
                "generation": {"params": job.params_json, "result": job.result_json, "review": job.review_json},
                "sample_type": "action_outcome_multimodal",
            },
            "media_refs": media_refs,
            "labels": ["h3", "approved", "action_outcome"],
        },
    ]
    results = []
    for payload in payloads:
        results.append(await record_training_sample(db, user, payload, commit=False))
    return results


async def _maybe_create_media_training_candidate(
    db: AsyncSession,
    user: User,
    project: Project,
) -> dict[str, Any]:
    accepted_jobs = await _quality_gated_training_jobs(db, project.id)
    accepted_job_ids = {row.id for row in accepted_jobs}
    accepted_count = len(accepted_job_ids)
    if accepted_count < MEDIA_TRAINING_THRESHOLD:
        return {"status": "collecting", "accepted_count": accepted_count, "threshold": MEDIA_TRAINING_THRESHOLD}
    existing_jobs = (
        await db.execute(
            select(TrainingJob)
            .where(TrainingJob.target_skill_id == f"project:{project.id}")
            .order_by(TrainingJob.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    for existing in existing_jobs:
        spec = existing.spec_json if isinstance(existing.spec_json, dict) else {}
        if spec.get("source") == "media_workbench_learning_loop" and int(spec.get("accepted_count") or 0) >= accepted_count:
            return {"status": "existing", "accepted_count": accepted_count, "training_job_id": existing.id}

    sample_ids = await _quality_gated_sft_sample_ids(db, project.id, accepted_job_ids)
    if len(sample_ids) < MEDIA_TRAINING_THRESHOLD:
        return {
            "status": "collecting",
            "accepted_count": accepted_count,
            "ready_sft_samples": len(sample_ids),
            "threshold": MEDIA_TRAINING_THRESHOLD,
        }
    from app.training.service import create_training_dataset_version, create_training_job

    dataset = await create_training_dataset_version(
        db,
        user,
        {
            "name": f"素材工作台采纳方案-{accepted_count}",
            "version": now_bjt().strftime("%Y%m%d%H%M%S"),
            "dataset_profile": "text_sft_v1",
            "modality": "text",
            "department": project.department or MATERIAL_WORKBENCH_DEPARTMENT,
            "org_unit_id": project.department_id,
            "sample_ids": sample_ids,
            "target_model_family": "material-planner",
            "target_gateway_id": "data-primary",
            "status": "ready",
        },
        commit=False,
    )
    dataset_row = _safe_dict(dataset.get("dataset_version"))
    training_job = await create_training_job(
        db,
        user,
        {
            "title": f"素材工作台规划模型自动候选 · {accepted_count} 条",
            "department": project.department or MATERIAL_WORKBENCH_DEPARTMENT,
            "job_type": "text_sft",
            "training_strategy": "lora_instruction",
            "target_skill_id": f"project:{project.id}",
            "target_gateway_id": "data-primary",
            "dataset_version_id": dataset_row.get("id"),
            "objective": "用采纳、修改、生成和投流结果提升 MiniMax H3 需求拆解质量；DeepSeek 始终保留为基线与回退。",
            "risk_level": "high",
            "spec": {
                "source": "media_workbench_learning_loop",
                "accepted_count": accepted_count,
                "base_model": "platform-approved-material-planner-base",
                "automation": {
                    "full_auto": True,
                    "shadow_evaluation_first": True,
                    "rollout_percentages": [10, 25, 50, 100],
                    "rollback_on_gate_failure": True,
                    "baseline_model": MEDIA_BASELINE_PROFILE,
                },
                "dataset_split": {
                    "eval_holdout_percent": 20,
                    "group_by": ["creative_lineage", "time_bucket"],
                    "prevent_media_lineage_leakage": True,
                },
                "eval_gate": {
                    "json_valid_rate_min": 0.99,
                    "generation_success_drop_max_percentage_points": 2,
                    "pairwise_win_rate_min": 0.55,
                    "pairwise_confidence_lower_bound_min": 0.50,
                    "severe_compliance_regression_allowed": False,
                },
            },
        },
        commit=False,
    )
    return {
        "status": "candidate_created",
        "accepted_count": accepted_count,
        "dataset_version_id": dataset_row.get("id"),
        "training_job_id": training_job.get("id"),
        "governance_status": training_job.get("status"),
    }


async def _plan_compare(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    brief = _safe_dict(payload.get("brief")) or payload
    if not any(_text(brief.get(key)) for key in ("request", "requirement", "product", "selling_points")):
        raise AppError("MEDIA_BRIEF_REQUIRED", 422)
    deployment = await _active_candidate_deployment(db, project)
    try:
        baseline_result, candidate_result = await asyncio.wait_for(
            asyncio.gather(
                _cached_baseline_plan(db, user, run, brief),
                _call_candidate_plan(deployment, brief),
                return_exceptions=True,
            ),
            timeout=MEDIA_PLAN_COMPARE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise AppError(
            "MEDIA_PLAN_TIMEOUT",
            504,
            {
                "detail": "AI 分镜规划超时，请保留当前表单后重试；系统不会自动重复提交生成任务。",
                "timeout_seconds": MEDIA_PLAN_COMPARE_TIMEOUT_SECONDS,
                "baseline_model": MEDIA_BASELINE_PROFILE,
                "editable": True,
            },
        ) from exc
    if isinstance(baseline_result, Exception):
        raise baseline_result
    baseline_model, baseline = baseline_result
    candidate_model = deployment.model_family if deployment else None
    candidate: dict[str, Any] = {}
    if isinstance(candidate_result, Exception):
        logger.warning("素材工作台候选模型调用失败，保留 DeepSeek 基线 run={} err={}", run.id, candidate_result)
    else:
        deployment, candidate_model, candidate = candidate_result
    now = now_bjt()
    row = MediaPlanComparison(
        id=_new_id("mpc"),
        project_id=project.id,
        project_run_id=run.id,
        department_id=run.department_id or project.department_id,
        department=run.department or project.department,
        requested_by=str(getattr(user, "id", "") or "") or None,
        status="planned",
        brief_json=brief,
        baseline_model=baseline_model,
        baseline_output_json=baseline,
        candidate_deployment_id=deployment.id if deployment else None,
        candidate_model=candidate_model,
        candidate_output_json=candidate,
        template_version=MEDIA_PLAN_TEMPLATE_VERSION,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    return {
        "plan_comparison": serialize_plan(row),
        "training_status": await _media_training_progress(db, project),
    }


async def _submit_job(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any],
    *, allow_exact_prompt_contract: bool = False,
) -> dict[str, Any]:
    plan_id = _text(payload.get("plan_comparison_id"), 50)
    plan = await db.get(MediaPlanComparison, plan_id) if plan_id else None
    if plan and plan.project_run_id != run.id:
        raise AppError("MEDIA_PLAN_NOT_FOUND", 404)
    selected_source = _text(payload.get("selected_source"), 30) or "baseline"
    if selected_source not in {"baseline", "candidate", "merged", "manual"}:
        raise AppError("MEDIA_PLAN_SELECTION_INVALID", 422)
    final_plan = _safe_dict(payload.get("final_plan"))
    if not final_plan and plan:
        final_plan = (
            plan.candidate_output_json if selected_source == "candidate" else plan.baseline_output_json
        ) or {}
    if not final_plan:
        raise AppError("MEDIA_PLAN_REQUIRED", 422)
    brief = _safe_dict(plan.brief_json) if plan else _safe_dict(payload.get("brief"))
    final_plan = {
        **final_plan,
        "production_brief": {
            key: brief.get(key)
            for key in (
                "product", "script", "request", "platform", "ratio", "duration_seconds",
                "requested_duration_seconds", "delivery_duration_seconds", "audio_enabled",
                "strip_reference_text", "reference_identity_policy", "creative_option",
                "ad_material_policy_version", "ad_material_contract", "operator_input",
            )
            if brief.get(key) is not None
        },
    }
    exact_prompt_contract = _text(final_plan.get("prompt_contract_version"), 80) in {
        "material-exact-h3-v1",
        "material-fde-h3-v1",
    }
    if exact_prompt_contract and not allow_exact_prompt_contract:
        raise AppError("MEDIA_EXACT_PROMPT_CAPABILITY_REQUIRED", 403)
    if exact_prompt_contract:
        exact_prompt = str(final_plan.get("integrated_multimodal_description") or "").strip()
        if not exact_prompt or len(exact_prompt) > H3_PROMPT_MAX_CHARS or _H3_UNSAFE_CONTROL_RE.search(exact_prompt):
            raise AppError(
                "MEDIA_PLAN_INVALID",
                422,
                {"field": "integrated_multimodal_description", "detail": "exact prompt is invalid"},
            )
        final_plan["video_prompt"] = exact_prompt
        final_plan["audio_prompt"] = ""
        final_plan["negative_constraints"] = []
    else:
        try:
            final_plan = compile_h3_plan(final_plan, brief=brief)
        except H3PromptPolicyError as exc:
            raise AppError("MEDIA_PLAN_INVALID", 422, {"field": exc.field, "detail": exc.message}) from exc
    mode = _text(payload.get("mode") or final_plan.get("h3_mode"), 30) or "text_to_video"
    workflow_id = _text(payload.get("workflow_definition_id"), 50)
    workflow = await db.get(MediaWorkflowDefinition, workflow_id) if workflow_id else None
    if workflow_id and (
        workflow is None
        or workflow.department_id != (run.department_id or project.department_id)
        or workflow.status == "archived"
        or (workflow.status != "published" and workflow.created_by != str(getattr(user, "id", "") or ""))
    ):
        raise AppError("MEDIA_WORKFLOW_NOT_FOUND", 404)
    if workflow is not None:
        mode = workflow.h3_mode
    if mode not in MEDIA_JOB_MODES:
        raise AppError("MEDIA_MODE_INVALID", 422, {"mode": mode})
    params = normalize_media_params(_safe_dict(payload.get("params")) or _safe_dict(final_plan.get("recommended_params")))
    raw_references = _safe_list(payload.get("reference_assets")) or _safe_list(payload.get("reference_asset_ids"))
    reference_requests = _normalize_reference_requests(raw_references, mode=mode)
    if mode != "text_to_video" and not reference_requests:
        raise AppError("MEDIA_REFERENCE_REQUIRED", 422, {"mode": mode})
    references = await _validated_reference_requests(db, run, reference_requests, mode=mode)
    if workflow is not None:
        supplied_roles = {str(item.get("role") or "") for item in references}
        required_roles = {str(item) for item in (workflow.required_roles_json or [])}
        missing_roles = sorted(required_roles - supplied_roles)
        if missing_roles:
            raise AppError("MEDIA_WORKFLOW_REFERENCE_REQUIRED", 422, {"roles": missing_roles})
    guardrails = _safe_dict(final_plan.get("brand_guardrails"))
    if (
        not exact_prompt_contract
        and mode == "text_to_video"
        and guardrails.get("require_reference_image")
        and not bool(payload.get("acknowledge_brand_text_risk"))
    ):
        raise AppError(
            "MEDIA_BRAND_REFERENCE_RECOMMENDED",
            422,
            {"detail": "包装、Logo 或文字真实性要求默认使用图生视频；如坚持文生视频需明确确认文字失真风险。"},
        )
    final_plan["h3_mode"] = mode
    final_plan["reference_roles"] = [
        {"asset_id": item["asset_id"], "role": item["role"], "purpose": item["purpose"]}
        for item in references
    ]
    if not exact_prompt_contract:
        try:
            final_plan = compile_h3_plan(final_plan, brief=brief)
        except H3PromptPolicyError as exc:
            raise AppError("MEDIA_PLAN_INVALID", 422, {"field": exc.field, "detail": exc.message}) from exc
    params = _apply_audio_delivery_gate(params, final_plan)
    user_compiled_prompt = None if exact_prompt_contract else _validated_user_compiled_prompt(payload.get("user_compiled_prompt"), references)
    if user_compiled_prompt is not None:
        deterministic_prompt = str(final_plan.get("integrated_multimodal_description") or "")
        final_plan["deterministic_compiled_prompt"] = deterministic_prompt
        final_plan["integrated_multimodal_description"] = user_compiled_prompt
        final_plan["video_prompt"] = user_compiled_prompt
        final_plan["user_prompt_override"] = {
            "applied": True,
            "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
            "deterministic_prompt_sha256": hashlib.sha256(deterministic_prompt.encode("utf-8")).hexdigest(),
            "override_prompt_sha256": hashlib.sha256(user_compiled_prompt.encode("utf-8")).hexdigest(),
        }
    # An explicit, acknowledged user override wins over the planner recommendation.
    final_plan["h3_mode"] = mode
    execution_prompt = str(final_plan.get("integrated_multimodal_description") or "")
    execution_prompt_sha256 = hashlib.sha256(execution_prompt.encode("utf-8")).hexdigest()
    preview_prompt_sha256 = _text(payload.get("prompt_preview_sha256"), 64) or execution_prompt_sha256
    if exact_prompt_contract and preview_prompt_sha256 != execution_prompt_sha256:
        raise AppError(
            "MEDIA_PROMPT_HASH_MISMATCH",
            409,
            {"preview_sha256": preview_prompt_sha256, "execution_sha256": execution_prompt_sha256},
        )
    idempotency_key = _text(payload.get("idempotency_key"), 128) or _json_hash(
        {"run": run.id, "plan": final_plan, "mode": mode, "params": params, "references": references}
    )
    existing = (
        await db.execute(
            select(MediaGenerationJob)
            .where(MediaGenerationJob.project_id == project.id, MediaGenerationJob.idempotency_key == idempotency_key)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        return {"deduped": True, "job": serialize_job(existing, await _latest_attempt(db, existing.id))}
    if plan:
        plan.selected_source = selected_source
        selection_plan = final_plan
        if _safe_dict(final_plan.get("production_campaign")):
            selection_source = {
                key: value
                for key, value in final_plan.items()
                if key not in {"production_campaign", "production_variant"}
            }
            try:
                selection_plan = compile_h3_plan(selection_source, brief=brief)
            except H3PromptPolicyError as exc:
                raise AppError("MEDIA_PLAN_INVALID", 422, {"field": exc.field, "detail": exc.message}) from exc
        plan.final_output_json = selection_plan
        plan.edits_json = {
            **_safe_dict(payload.get("edits")),
            "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
            "compiled_prompt_sha256": hashlib.sha256(
                str(selection_plan.get("integrated_multimodal_description") or "").encode("utf-8")
            ).hexdigest(),
            "production_campaign_id": _safe_dict(final_plan.get("production_campaign")).get("id"),
        }
        plan.status = "selected"
        plan.updated_at = now_bjt()
    template_id = workflow.bridge_template_id if workflow is not None else {
        "text_to_video": "h3_t2v_v1", "image_to_video": "h3_i2v_v1", "reference_replay": "h3_r2v_v1",
        "video_enhance": "video_upscale_realesrgan_v1", "video_local_edit": "video_local_edit_sam2_propainter_data_patch_v2",
    }[mode]
    production_policy = production_policy_for(
        brief,
        _safe_list(payload.get("source_roles")) or references,
        mode=mode,
    )
    rights = dict(_safe_dict(payload.get("rights")))
    production_intent = _text(production_policy.get("production_intent"), 50)
    rights["contains_person"] = bool(rights.get("contains_person")) or production_intent in {
        "people_dialogue", "people_lifestyle"
    }
    rights["contains_voice"] = bool(rights.get("contains_voice")) or bool(
        params.get("audio_enabled") and _text(brief.get("script"), 6000)
    )
    rights["content_detection_source"] = "server_production_policy"
    rights["production_policy_version"] = PRODUCTION_POLICY_VERSION
    row = MediaGenerationJob(
        id=_new_id("mvj"),
        project_id=project.id,
        project_run_id=run.id,
        plan_comparison_id=plan.id if plan else None,
        production_batch_id=_text(payload.get("production_batch_id"), 50) or None,
        workflow_definition_id=workflow.id if workflow is not None else None,
        workflow_definition_version=workflow.version if workflow is not None else None,
        continuation_chain_id=_text(payload.get("continuation_chain_id"), 50) or None,
        strategy_session_id=_text(payload.get("strategy_session_id"), 50) or None,
        strategy_direction_id=_text(payload.get("strategy_direction_id"), 50) or None,
        replay_project_id=_text(payload.get("replay_project_id"), 50) or None,
        replay_segment_id=_text(payload.get("replay_segment_id"), 50) or None,
        prompt_preview_sha256=preview_prompt_sha256,
        execution_prompt_sha256=execution_prompt_sha256,
        depends_on_job_id=_text(payload.get("depends_on_job_id"), 50) or None,
        sequence_index=(int(payload.get("sequence_index")) if payload.get("sequence_index") is not None else None),
        not_before_at=(parse_bjt_datetime(payload.get("not_before_at")) if payload.get("not_before_at") else None),
        deadline_at=(parse_bjt_datetime(payload.get("deadline_at")) if payload.get("deadline_at") else None),
        idempotency_key=idempotency_key,
        department_id=run.department_id or project.department_id,
        department=run.department or project.department,
        requested_by=str(getattr(user, "id", "") or "") or None,
        mode=mode,
        status="queued",
        priority=_media_job_priority(
            department_id=run.department_id or project.department_id,
            mode=mode,
            params=params,
        ),
        prompt_json=final_plan,
        params_json=params,
        reference_assets_json=references,
        result_json={"production_policy": production_policy},
        workflow_template_id=template_id,
        business_title=_job_business_title(payload, brief, final_plan),
        output_preset_id=_text(payload.get("output_preset_id"), 50) or "custom",
        source_roles_json=_safe_list(payload.get("source_roles")) or [
            {"asset_id": item.get("asset_id"), "role": item.get("role"), "purpose": item.get("purpose")}
            for item in references
        ],
        creative_option=_text(payload.get("creative_option"), 50) or "smart",
        job_group_id=(
            _text(payload.get("job_group_id"), 80)
            or _text(_safe_dict(final_plan.get("production_campaign")).get("id"), 80)
            or _text(payload.get("production_batch_id"), 80)
            or None
        ),
        rights_json=rights,
        created_at=now_bjt(),
        updated_at=now_bjt(),
    )
    db.add(row)
    await db.flush()
    if not row.not_before_at or row.not_before_at <= now_bjt():
        await _dispatch_job(db, run, row)
    return {"deduped": False, "job": serialize_job(row, await _latest_attempt(db, row.id))}


async def _batch_submit_jobs(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        candidate_count = int(payload.get("candidate_count") or payload.get("count") or 1)
    except (TypeError, ValueError) as exc:
        raise AppError("MEDIA_BATCH_INVALID", 422, {"field": "candidate_count"}) from exc
    if candidate_count < 1 or candidate_count > 600:
        raise AppError("MEDIA_BATCH_INVALID", 422, {"field": "candidate_count", "minimum": 1, "maximum": 600})

    target_end_at = _text(payload.get("target_end_at"), 50)
    if target_end_at:
        try:
            target_dt = parse_bjt_datetime(target_end_at)
            now = now_bjt()
        except (TypeError, ValueError) as exc:
            raise AppError("MEDIA_BATCH_INVALID", 422, {"field": "target_end_at"}) from exc
        if target_dt <= now or target_dt > now + timedelta(days=2):
            raise AppError(
                "MEDIA_BATCH_INVALID",
                422,
                {"field": "target_end_at", "detail": "target_end_at must be within the next 48 hours"},
            )

    final_plan = _safe_dict(payload.get("final_plan"))
    plan_id = _text(payload.get("plan_comparison_id"), 50)
    campaign_fingerprint = {
        "run": run.id,
        "plan": plan_id or final_plan,
        "mode": payload.get("mode"),
        "params": payload.get("params"),
        "target_end_at": target_end_at,
        "candidate_count": candidate_count,
    }
    campaign_id = _text(payload.get("campaign_id"), 50) or f"mvc-{_json_hash(campaign_fingerprint)[:20]}"
    raw_params = _safe_dict(payload.get("params"))
    try:
        base_seed = int(raw_params.get("seed", -1))
    except (TypeError, ValueError):
        base_seed = -1
    if base_seed < 0:
        base_seed = int(hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:15], 16)

    sample_items = []
    queued_count = 0
    assigned_count = 0
    deduped_count = 0
    for index in range(candidate_count):
        item_plan = {
            **final_plan,
            "production_variant": production_variant_for_candidate(index + 1, campaign_id),
            "production_campaign": {
                "id": campaign_id,
                "candidate_index": index + 1,
                "candidate_count": candidate_count,
                "target_end_at": target_end_at or None,
            },
        }
        item_payload = {
            **payload,
            "final_plan": item_plan,
            "params": {**raw_params, "seed": base_seed + index, "batch_count": 1},
            "idempotency_key": f"{campaign_id}:{index + 1}",
        }
        result = await _submit_job(db, user, project, run, item_payload)
        item = result.get("job") or {}
        queued_count += int(item.get("status") == "queued")
        assigned_count += int(item.get("status") == "assigned")
        deduped_count += int(bool(result.get("deduped")))
        if len(sample_items) < 20:
            sample_items.append(item)
    return {
        "campaign_id": campaign_id,
        "target_end_at": target_end_at or None,
        "candidate_count": candidate_count,
        "queued_count": queued_count,
        "assigned_count": assigned_count,
        "deduped_count": deduped_count,
        "items": sample_items,
        "items_truncated": candidate_count > len(sample_items),
    }


async def _get_job_for_run(db: AsyncSession, run: ProjectRun, job_id: Any) -> MediaGenerationJob:
    row = await db.get(MediaGenerationJob, _text(job_id, 50))
    # The material workbench is a durable project-level queue. Reopening the
    # Project creates a new ProjectRun for trace isolation, but operators must
    # still be able to inspect and review jobs created by earlier runs of the
    # same governed Project.
    if not row or row.project_id != run.project_id:
        raise AppError("MEDIA_JOB_NOT_FOUND", 404)
    return row


async def _list_jobs(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    raw_status = _text(payload.get("status"), 30).lower()
    status_groups = {
        # "active" is the GPU production queue shown in Material Workbench.
        # Delivery syncing is a separate business state and must not inflate the
        # "正在生产" metric after generation has already completed.
        "active": {"queued", "assigned", "running", "collecting"},
        "executing": {"assigned", "running", "collecting", "syncing"},
        "failed": {"failed", "cancelled", "rejected"},
    }
    allowed_statuses = {
        "draft", "planned", "queued", "assigned", "running", "collecting",
        "awaiting_review", "approved", "rejected", "syncing", "synced",
        "failed", "cancelled",
    }
    selected_statuses = status_groups.get(raw_status)
    if raw_status and selected_statuses is None:
        if raw_status not in allowed_statuses:
            raise AppError("MEDIA_JOB_FILTER_INVALID", 422, {"field": "status"})
        selected_statuses = {raw_status}
    base_condition = MediaGenerationJob.project_id == run.project_id
    count_rows = (
        await db.execute(
            select(MediaGenerationJob.status, func.count(MediaGenerationJob.id))
            .where(base_condition)
            .group_by(MediaGenerationJob.status)
        )
    ).all()
    status_counts = {str(status): int(count) for status, count in count_rows}
    total = int(
        (
            await db.execute(
                select(func.count(MediaGenerationJob.id)).where(base_condition)
            )
        ).scalar()
        or 0
    )
    conditions = [base_condition]
    if selected_statuses:
        conditions.append(MediaGenerationJob.status.in_(sorted(selected_statuses)))
    filtered_total = int(
        (
            await db.execute(select(func.count(MediaGenerationJob.id)).where(*conditions))
        ).scalar()
        or 0
    )
    cursor = _decode_job_cursor(payload.get("cursor") or payload.get("after_cursor"))
    if cursor:
        cursor_created_at, cursor_id = cursor
        conditions.append(
            or_(
                MediaGenerationJob.created_at < cursor_created_at,
                and_(MediaGenerationJob.created_at == cursor_created_at, MediaGenerationJob.id < cursor_id),
            )
        )
    try:
        offset = min(max(int(payload.get("offset") or 0), 0), 10_000)
    except (TypeError, ValueError) as exc:
        raise AppError("MEDIA_JOB_FILTER_INVALID", 422, {"field": "offset"}) from exc
    page_limit = min(max(int(payload.get("limit") or 50), 1), 100)
    rows = (
        await db.execute(
            select(MediaGenerationJob)
            .where(*conditions)
            .order_by(MediaGenerationJob.created_at.desc(), MediaGenerationJob.id.desc())
            .offset(0 if cursor else offset)
            .limit(page_limit)
        )
    ).scalars().all()
    items = []
    for row in rows:
        attempt = await _refresh_job(db, user, run, row)
        items.append(serialize_job(row, attempt))
    return {
        "items": items,
        "total": total,
        "filtered_total": filtered_total,
        "status_counts": status_counts,
        "offset": 0 if cursor else offset,
        "cursor": _text(payload.get("cursor") or payload.get("after_cursor"), 500) or None,
        "next_cursor": _encode_job_cursor(rows[-1]) if len(rows) == page_limit else None,
        "has_more": len(rows) == page_limit,
    }


async def _get_job(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    row = await _get_job_for_run(db, run, payload.get("job_id"))
    attempt = await _refresh_job(db, user, run, row)
    return {"job": serialize_job(row, attempt)}


async def _enqueue_quality_analysis(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    payload: dict[str, Any],
) -> dict[str, Any]:
    requested_ids = _safe_list(payload.get("job_ids"))
    if payload.get("job_id"):
        requested_ids.insert(0, payload.get("job_id"))
    job_ids = []
    for value in requested_ids:
        job_id = _text(value, 50)
        if job_id and job_id not in job_ids:
            job_ids.append(job_id)
    if not job_ids or len(job_ids) > 12:
        raise AppError("MEDIA_QUALITY_ANALYSIS_REQUEST_INVALID", 422, {"max_job_ids": 12})
    force = bool(payload.get("force"))
    jobs = []
    queued_count = 0
    deduped_count = 0
    for job_id in job_ids:
        row = await _get_job_for_run(db, run, job_id)
        if row.status not in MEDIA_QUALITY_ANALYSIS_STATUSES:
            raise AppError("MEDIA_QUALITY_ANALYSIS_STATUS_INVALID", 409, {"job_id": row.id, "status": row.status})
        queued_result, queued = _queue_media_quality_analysis(
            row.result_json,
            requested_by=str(getattr(user, "id", "") or "") or None,
            automatic=False,
            force=force,
        )
        if queued:
            row.result_json = queued_result
            row.updated_at = now_bjt()
            queued_count += 1
        else:
            deduped_count += 1
        jobs.append(serialize_job(row, await _latest_attempt(db, row.id)))
    return {
        "jobs": jobs,
        "queued_count": queued_count,
        "deduped_count": deduped_count,
        "policy_version": MEDIA_QUALITY_ANALYSIS_POLICY_VERSION,
        "advisory_only": True,
    }


async def _cancel_job(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    row = await _get_job_for_run(db, run, payload.get("job_id"))
    if row.status in {"synced", "cancelled"}:
        return {"job": serialize_job(row, await _latest_attempt(db, row.id)), "deduped": True}
    await _cancel_job_row(db, row)
    return {"job": serialize_job(row, await _latest_attempt(db, row.id))}


async def _cancel_job_row(db: AsyncSession, row: MediaGenerationJob) -> dict[str, Any]:
    """Cancel one governed media row and best-effort cancel its Bridge work."""

    attempt = await _latest_attempt(db, row.id)
    bridge_cancelled = False
    bridge_error = ""
    if attempt and attempt.instance_id and bridge_registry.is_online(attempt.instance_id):
        try:
            await AIClawClient(attempt.instance_id).cancel_media_job({"job_id": row.id}, timeout=30)
            bridge_cancelled = True
        except Exception as exc:  # noqa: BLE001
            bridge_error = _text(exc, 2000)
            attempt.error = bridge_error
    if row.status not in {"failed", "cancelled"}:
        transition_media_job(row, "cancelled")
    if attempt:
        attempt.status = "cancelled"
        attempt.completed_at = now_bjt()
    return {"bridge_cancelled": bridge_cancelled, "bridge_error": bridge_error}


async def _batch_cancel_jobs(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    """Stop active continuous-production campaigns in one audited capability call."""

    campaign_ids = {
        _text(item, 50)
        for item in _safe_list(payload.get("campaign_ids"))
        if _text(item, 50)
    }
    if len(campaign_ids) > 20:
        raise AppError("MEDIA_BATCH_CANCEL_INVALID", 422, {"field": "campaign_ids", "maximum": 20})
    cancel_all = bool(payload.get("cancel_all_active_continuous"))
    if not campaign_ids and not cancel_all:
        raise AppError(
            "MEDIA_BATCH_CANCEL_INVALID",
            422,
            {"detail": "必须提供 campaign_ids，或明确设置 cancel_all_active_continuous=true"},
        )

    active_statuses = {"queued", "assigned", "running", "collecting"}
    rows = (
        await db.execute(
            select(MediaGenerationJob)
            .where(
                MediaGenerationJob.project_id == run.project_id,
                MediaGenerationJob.status.in_(sorted(active_statuses)),
            )
            .order_by(MediaGenerationJob.created_at.asc())
            .limit(5000)
        )
    ).scalars().all()
    selected = []
    for row in rows:
        campaign = _safe_dict(_safe_dict(row.prompt_json).get("production_campaign"))
        campaign_id = _text(campaign.get("id") or campaign.get("campaign_id"), 50)
        if not campaign_id:
            continue
        if campaign_ids and campaign_id not in campaign_ids:
            continue
        selected.append(row)

    cancelled_count = 0
    bridge_cancelled_count = 0
    bridge_errors: list[dict[str, str]] = []
    sample_jobs: list[dict[str, Any]] = []
    affected_campaigns: set[str] = set()
    for row in selected:
        campaign = _safe_dict(_safe_dict(row.prompt_json).get("production_campaign"))
        campaign_id = _text(campaign.get("id") or campaign.get("campaign_id"), 50)
        affected_campaigns.add(campaign_id)
        result = await _cancel_job_row(db, row)
        cancelled_count += 1
        bridge_cancelled_count += int(bool(result.get("bridge_cancelled")))
        if result.get("bridge_error") and len(bridge_errors) < 20:
            bridge_errors.append({"job_id": row.id, "error": _text(result.get("bridge_error"), 500)})
        if len(sample_jobs) < 20:
            sample_jobs.append(serialize_job(row, await _latest_attempt(db, row.id)))

    return {
        "campaign_ids": sorted(affected_campaigns),
        "selected_count": len(selected),
        "cancelled_count": cancelled_count,
        "bridge_cancelled_count": bridge_cancelled_count,
        "bridge_error_count": len(bridge_errors),
        "bridge_errors": bridge_errors,
        "jobs": sample_jobs,
        "jobs_truncated": len(selected) > len(sample_jobs),
    }


async def _retry_job(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    row = await _get_job_for_run(db, run, payload.get("job_id"))
    if row.status not in {"failed", "cancelled", "rejected", "queued"}:
        raise AppError("MEDIA_JOB_NOT_RETRYABLE", 409, {"status": row.status})
    if row.status != "queued":
        transition_media_job(row, "queued")
    row.assigned_instance_id = None
    await _dispatch_job(db, run, row)
    return {"job": serialize_job(row, await _latest_attempt(db, row.id))}


def _review_rework_seed(idempotency_key: str, source_seed: Any) -> int:
    try:
        normalized_source_seed = int(source_seed)
    except (TypeError, ValueError):
        normalized_source_seed = -1
    digest = hashlib.sha256(
        f"{idempotency_key}:{normalized_source_seed}:review-rework".encode("utf-8")
    ).hexdigest()
    return max(1, int(digest[:8], 16) % 2_147_483_647)


async def _clone_job(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    source = await _get_job_for_run(db, run, payload.get("job_id"))
    prompt_overrides = _safe_dict(payload.get("prompt_overrides"))
    param_overrides = _safe_dict(payload.get("param_overrides"))
    idempotency_key = _text(payload.get("idempotency_key"), 128) or f"clone:{source.id}:{uuid4().hex}"
    params = {**(source.params_json or {}), **param_overrides}
    if _safe_dict(prompt_overrides.get("review_rework")) and "seed" not in param_overrides:
        params["seed"] = _review_rework_seed(idempotency_key, _safe_dict(source.params_json).get("seed", -1))
    clone_payload = {
        # Project jobs are durable across ProjectRun reopenings, while a plan
        # comparison remains run-scoped. Preserve the compiled plan below, but
        # never smuggle an older run's comparison row into the new trace.
        "plan_comparison_id": source.plan_comparison_id if source.project_run_id == run.id else None,
        "selected_source": "manual",
        "final_plan": {**(source.prompt_json or {}), **prompt_overrides},
        "mode": payload.get("mode") or source.mode,
        "params": params,
        "reference_assets": payload.get("reference_assets") or payload.get("reference_asset_ids") or source.reference_assets_json,
        "workflow_definition_id": payload.get("workflow_definition_id") or source.workflow_definition_id,
        "rights": payload.get("rights") or source.rights_json,
        "idempotency_key": idempotency_key,
    }
    result = await _submit_job(db, user, project, run, clone_payload)
    new_job = await db.get(MediaGenerationJob, _safe_dict(result.get("job")).get("id"))
    if new_job:
        new_job.cloned_from_job_id = source.id
        result["job"] = serialize_job(new_job, await _latest_attempt(db, new_job.id))
    return result


def _normalize_media_quality_evaluation(value: Any) -> dict[str, Any]:
    raw_quality = _safe_dict(value)
    quality_dimensions = {}
    for key in MEDIA_QUALITY_DIMENSION_KEYS:
        try:
            score = int(raw_quality.get(key))
        except (TypeError, ValueError):
            continue
        if 1 <= score <= 5:
            quality_dimensions[key] = score
    overall_score = (
        round(sum(quality_dimensions.values()) / (len(quality_dimensions) * 5), 3)
        if quality_dimensions
        else 0.0
    )
    normalized = {
        **quality_dimensions,
        "overall_score": overall_score,
        "notes": _text(raw_quality.get("notes"), 2000),
        "source": "manual_workbench" if quality_dimensions else "approval_default",
    }
    material_stage = _safe_dict(raw_quality.get("material_stage"))
    if material_stage:
        normalized["material_stage"] = {
            "id": _text(material_stage.get("id"), 80),
            "label": _text(material_stage.get("label"), 80),
            "approval_scope": _text(material_stage.get("approval_scope"), 80),
            "requires_assembly": material_stage.get("requires_assembly") is True,
            "cloud_library_eligible": material_stage.get("cloud_library_eligible") is not False,
            "commercial_positive_eligible": material_stage.get("commercial_positive_eligible") is True,
        }
    return normalized


def _material_stage_from_quality_result(value: Any) -> dict[str, Any]:
    quality = _safe_dict(value)
    material_stage = _safe_dict(quality.get("material_stage"))
    if material_stage:
        return material_stage
    for asset in _safe_list(quality.get("assets")):
        material_stage = _safe_dict(_safe_dict(_safe_dict(asset).get("analysis")).get("material_stage"))
        if material_stage:
            return material_stage
    return {}


def _media_training_quality_gate(value: Any) -> tuple[bool, list[str]]:
    quality = _safe_dict(value)
    missing = []
    material_stage = _safe_dict(quality.get("material_stage"))
    if material_stage and (
        material_stage.get("requires_assembly") is True
        or material_stage.get("commercial_positive_eligible") is not True
    ):
        missing.append("material_stage.commercial_positive_eligible")
    for key, minimum in MEDIA_TRAINING_QUALITY_MINIMUMS.items():
        try:
            score = int(quality.get(key))
        except (TypeError, ValueError):
            score = 0
        if score < minimum:
            missing.append(f"quality.{key}>={minimum}")
    return not missing, missing


async def _review_job(
    db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    row = await _get_job_for_run(db, run, payload.get("job_id"))
    decision = _text(payload.get("decision"), 20).lower()
    if decision not in {"approved", "rejected"}:
        raise AppError("MEDIA_REVIEW_INVALID", 422)
    if row.status != "awaiting_review":
        raise AppError("MEDIA_REVIEW_STATUS_INVALID", 409, {"status": row.status})
    hard_gate: dict[str, Any] | None = None
    if decision == "approved":
        from .workbench_v2 import hard_review_gate

        gate_passed, gate_missing = await hard_review_gate(db, row, payload)
        hard_gate = {"passed": gate_passed, "missing": gate_missing, "version": "material-review-hard-gate-v3"}
        if not gate_passed:
            raise AppError("MEDIA_REVIEW_HARD_GATE_BLOCKED", 409, hard_gate)
    quality_evaluation = _normalize_media_quality_evaluation(payload.get("quality_evaluation"))
    # Material stage is a server-owned production fact. Never allow a client
    # to mark a component plate as a commercial-positive candidate.
    quality_evaluation.pop("material_stage", None)
    authoritative_stage = _material_stage_from_quality_result(
        _safe_dict(row.result_json).get("quality_analysis")
    )
    if authoritative_stage:
        quality_evaluation["material_stage"] = _normalize_media_quality_evaluation({
            "material_stage": authoritative_stage,
        }).get("material_stage", {})
    row.review_json = {
        "decision": decision,
        "reason": _text(payload.get("reason"), 2000),
        "category": _text(payload.get("category"), 180),
        "reviewed_by": str(getattr(user, "id", "") or ""),
        "reviewed_at": isoformat_bjt(now_bjt()),
        "quality_evaluation": quality_evaluation,
        "hard_gate": hard_gate,
    }
    row.rights_json = {**(row.rights_json or {}), **_safe_dict(payload.get("rights"))}
    transition_media_job(row, decision)
    if row.continuation_chain_id and row.sequence_index == 0:
        chain = await db.get(MediaContinuationChain, row.continuation_chain_id)
        if chain is not None:
            chain.status = decision
            chain.config_json = {
                **_safe_dict(chain.config_json),
                "preview_review": {
                    "decision": decision,
                    "reason": _text(payload.get("reason"), 2000),
                    "reviewed_by": str(getattr(user, "id", "") or ""),
                    "reviewed_at": isoformat_bjt(now_bjt()),
                },
            }
            chain.updated_at = now_bjt()
            if decision == "approved" and row.result_asset_id:
                preview_asset = await db.get(ProjectRunAsset, row.result_asset_id)
                if preview_asset is not None:
                    preview_asset.metadata_json = {
                        **_safe_dict(preview_asset.metadata_json),
                        "preview_only": False,
                        "formal_asset": True,
                        "approved_media_job_id": row.id,
                    }
    training_samples = []
    cloud_sync: dict[str, Any] | None = None
    training_cycle: dict[str, Any] | None = None
    if decision == "approved":
        row.approved_by = str(getattr(user, "id", "") or "") or None
        row.approved_at = now_bjt()
        rights_eligible, rights_missing = _training_rights_eligible(row.rights_json or {})
        quality_eligible, quality_missing = _media_training_quality_gate(quality_evaluation)
        eligible = rights_eligible and quality_eligible
        missing = [*rights_missing, *quality_missing]
        row.training_eligibility = "eligible" if eligible else "blocked"
        row.review_json = {
            **row.review_json,
            "training_gate": {
                "eligible": eligible,
                "missing": missing,
                "rights_missing": rights_missing,
                "quality_missing": quality_missing,
                "quality_minimums": MEDIA_TRAINING_QUALITY_MINIMUMS,
            },
        }
        if eligible:
            plan = await db.get(MediaPlanComparison, row.plan_comparison_id) if row.plan_comparison_id else None
            training_samples = await _record_approved_training_samples(db, user, row, plan)
            project = await db.get(Project, row.project_id)
            if project is not None:
                try:
                    training_cycle = await _maybe_create_media_training_candidate(db, user, project)
                except Exception as exc:  # noqa: BLE001
                    training_cycle = {"status": "candidate_blocked", "error": _text(exc, 1000)}
                    logger.warning("素材工作台自动训练候选创建失败 job={} err={}", row.id, exc)
        # Approval always creates the durable outbox.  Delivery may remain blocked
        # until an official Cloud Video upload connector is accepted.
        await _ensure_cloud_sync_outboxes(db, row, category=_text(payload.get("category"), 180))
        cloud_sync = await _sync_cloud_video(
            db,
            run,
            {"job_id": row.id, "category": _text(payload.get("category"), 180)},
        )
    return {
        "job": serialize_job(row, await _latest_attempt(db, row.id)),
        "training_samples": training_samples,
        "training_cycle": training_cycle,
        "cloud_sync": cloud_sync,
    }


async def _ensure_cloud_sync_outbox(
    db: AsyncSession, row: MediaGenerationJob, *, category: str = ""
) -> CloudVideoSyncOutbox:
    rows = await _ensure_cloud_sync_outboxes(db, row, category=category)
    if not rows:
        raise AppError("MEDIA_RESULT_REQUIRED", 409)
    return rows[0]


async def _ensure_cloud_sync_outboxes(
    db: AsyncSession, row: MediaGenerationJob, *, category: str = ""
) -> list[CloudVideoSyncOutbox]:
    result_assets = [item for item in _safe_list((row.result_json or {}).get("assets")) if isinstance(item, dict)]
    if not result_assets and row.result_asset_id and row.result_sha256:
        result_assets = [{"id": row.result_asset_id, "sha256": row.result_sha256}]
    if not result_assets:
        return []
    team = MATERIAL_WORKBENCH_DEPARTMENT
    category = category or "示例品牌内容电商运营部/AI生成素材"
    result = []
    for asset in result_assets:
        asset_id = _text(asset.get("id"), 50)
        sha256 = _text(asset.get("sha256"), 64)
        if not asset_id or not sha256:
            continue
        key = hashlib.sha256(f"{sha256}:{team}:{category}".encode("utf-8")).hexdigest()
        existing = (
            await db.execute(select(CloudVideoSyncOutbox).where(CloudVideoSyncOutbox.idempotency_key == key).limit(1))
        ).scalar_one_or_none()
        if existing:
            result.append(existing)
            continue
        now = now_bjt()
        outbox = CloudVideoSyncOutbox(
            id=_new_id("cvo"),
            media_job_id=row.id,
            idempotency_key=key,
            team=team,
            category=category,
            status="pending",
            payload_json={"result_asset_id": asset_id, "sha256": sha256},
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(outbox)
        result.append(outbox)
    await db.flush()
    return result


async def _sync_cloud_video(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    row = await _get_job_for_run(db, run, payload.get("job_id"))
    if row.status not in {"approved", "syncing", "synced"}:
        raise AppError("MEDIA_SYNC_STATUS_INVALID", 409, {"status": row.status})
    outboxes = await _ensure_cloud_sync_outboxes(db, row, category=_text(payload.get("category"), 180))
    if not outboxes:
        raise AppError("MEDIA_RESULT_REQUIRED", 409)
    if row.status == "synced" and all(item.remote_video_id for item in outboxes):
        return {
            "job": serialize_job(row),
            "sync": _serialize_outbox(outboxes[0]),
            "sync_items": [_serialize_outbox(item) for item in outboxes],
            "deduped": True,
        }
    # The current Cloud Video module exposes query/report APIs only.  Persist the
    # outbox but do not use browser cookies or simulated clicks as an uploader.
    for outbox in outboxes:
        outbox.status = "blocked_connector_pending"
        outbox.last_error = "云视频尚无已验收的正式上传 API；同步停在连接器验收环节。"
        outbox.attempt_count = int(outbox.attempt_count or 0) + 1
        outbox.next_attempt_at = now_bjt() + timedelta(hours=6)
        outbox.updated_at = now_bjt()
    return {
        "job": serialize_job(row),
        "sync": _serialize_outbox(outboxes[0]),
        "sync_items": [_serialize_outbox(item) for item in outboxes],
        "connector_ready": False,
    }


def _serialize_outbox(row: CloudVideoSyncOutbox) -> dict[str, Any]:
    return {
        "id": row.id,
        "media_job_id": row.media_job_id,
        "team": row.team,
        "category": row.category,
        "status": row.status,
        "remote_video_id": row.remote_video_id,
        "attempt_count": row.attempt_count,
        "last_error": row.last_error,
        "next_attempt_at": isoformat_bjt(row.next_attempt_at),
        "completed_at": isoformat_bjt(row.completed_at),
    }


async def dispatch_project_capability(
    db: AsyncSession,
    user: User,
    project: Project,
    run: ProjectRun,
    capability: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if capability not in MEDIA_CAPABILITIES:
        raise AppError("PROJECT_CAPABILITY_DENIED", 400, {"capability": capability})
    if capability in FDE_MEDIA_CAPABILITIES:
        from .fde_v4 import dispatch_fde_capability

        return await dispatch_fde_capability(db, user, project, run, capability, payload)
    if capability in V3_MEDIA_CAPABILITIES:
        from .workbench_v3 import dispatch_v3_capability

        return await dispatch_v3_capability(db, user, project, run, capability, payload)
    if capability in NEW_MEDIA_CAPABILITIES:
        from .workbench_v2 import dispatch_v2_capability

        return await dispatch_v2_capability(db, user, project, run, capability, payload)
    if capability == "video.plan_compare":
        return await _plan_compare(db, user, project, run, payload)
    if capability == "video.job.submit":
        return await _submit_job(db, user, project, run, payload)
    if capability == "video.job.batch_submit":
        return await _batch_submit_jobs(db, user, project, run, payload)
    if capability == "video.job.batch_cancel":
        return await _batch_cancel_jobs(db, run, payload)
    if capability == "video.job.list":
        return await _list_jobs(db, user, run, payload)
    if capability == "video.job.get":
        return await _get_job(db, user, run, payload)
    if capability == "video.job.cancel":
        return await _cancel_job(db, run, payload)
    if capability == "video.job.retry":
        return await _retry_job(db, run, payload)
    if capability == "video.job.clone":
        return await _clone_job(db, user, project, run, payload)
    if capability == "video.quality.analyze":
        return await _enqueue_quality_analysis(db, user, run, payload)
    if capability == "video.review":
        return await _review_job(db, user, run, payload)
    return await _sync_cloud_video(db, run, payload)
