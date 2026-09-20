"""Material Workbench 2.0 department production and review services.

This module deliberately remains a control plane.  Workflows are declarative
and are always reduced to the three Bridge allow-listed H3 templates.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
import statistics
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.ai import call_llm_multimodal, get_ai_profile_config
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime
from app.config import settings
from app.execution.models import OpenClawInstance
from app.projects.models import Project, ProjectRun, ProjectRunAsset
from app.projects import service as project_service

from .ad_material_parser import (
    AD_MATERIAL_POLICY_VERSION,
    adapt_source_roles_for_ad_contract,
    apply_ad_material_contract_to_plan,
    build_operator_brief_plan,
    normalize_frontdesk_script,
    parse_ad_material_brief,
)
from .h3_prompt_policy import (
    H3_PROMPT_MAX_CHARS,
    H3_PROMPT_POLICY_VERSION,
    H3PromptPolicyError,
    build_character_first_frame_prompt,
    compile_h3_plan,
    production_variant_for_candidate,
)
from .production_policy import (
    PRODUCTION_POLICY_VERSION,
    brief_explicitly_forbids_people,
    production_policy_for,
)
from .models import (
    CloudVideoSyncOutbox,
    MediaAssetGroup,
    MediaContinuationChain,
    MediaContinuationSegment,
    MediaGenerationAttempt,
    MediaGenerationJob,
    MediaCreativeStrategySession,
    MediaLibraryAsset,
    MediaProductionBatch,
    MediaReplayProject,
    MediaReplaySegment,
    MediaReviewAnnotation,
    MediaWorkflowDefinition,
    MediaWorkbenchStreamEvent,
)


BUSINESS_ASSET_ROLES = {
    "character_first_frame": ("image", "first_frame"),
    "product_packshot": ("image", "first_frame"),
    "product_detail": ("image", "reference_image"),
    "visual_reference": ("image", "reference_image"),
    "motion_reference": ("video", "reference_video"),
    "audio_reference": ("audio", "reference_audio"),
    "continuity_anchor": ("video", "reference_video"),
}


def _workbench_media_node_runtime(node: OpenClawInstance) -> dict[str, Any]:
    """Return worker-safe node liveness plus diagnostics for the workbench.

    Bridge websocket membership is process-local.  With multiple API workers a
    project snapshot can be served by a different process and previously showed
    ``0 online`` while the scheduler was actively dispatching to both GPUs.
    The persisted heartbeat is the cross-worker source of truth used by the
    task-tree as well, bounded by the same freshness window.
    """

    heartbeat = node.last_heartbeat or node.bridge_connected_at
    heartbeat_age_seconds = None
    if heartbeat is not None:
        heartbeat_age_seconds = max(0, round((now_bjt() - heartbeat).total_seconds()))
    if not bool(node.is_active):
        return {
            "online": False,
            "online_source": "inactive",
            "offline_reason": "inactive",
            "heartbeat_age_seconds": heartbeat_age_seconds,
        }
    if _core().bridge_registry.is_online(node.id):
        return {
            "online": True,
            "online_source": "bridge_session",
            "offline_reason": None,
            "heartbeat_age_seconds": heartbeat_age_seconds,
        }
    if heartbeat is None:
        return {
            "online": False,
            "online_source": "missing_heartbeat",
            "offline_reason": "heartbeat_missing",
            "heartbeat_age_seconds": None,
        }
    online = bool(heartbeat_age_seconds <= settings.TASKTREE_HEARTBEAT_ONLINE)
    return {
        "online": online,
        "online_source": "persisted_heartbeat",
        "offline_reason": None if online else "heartbeat_stale",
        "heartbeat_age_seconds": heartbeat_age_seconds,
    }


def _workbench_media_node_online(node: OpenClawInstance) -> bool:
    """Compatibility helper used by dispatch and existing focused tests."""

    return bool(_workbench_media_node_runtime(node)["online"])


def _normalized_library_metadata(existing: Any, incoming: Any, *, media_type: str) -> dict[str, Any]:
    incoming_metadata = _dict(incoming)
    metadata = {**_dict(existing), **incoming_metadata}
    defaults = {"image": "character_first_frame", "video": "motion_reference", "audio": "audio_reference"}
    # Older sf clients wrote ``business_role`` while the workbench UI reads
    # ``default_role``.  Prefer the current request so re-saving an old asset
    # can repair a previously normalized character-first-frame role.
    requested = _text(
        incoming_metadata.get("default_role")
        or incoming_metadata.get("business_role")
        or metadata.get("default_role"),
        40,
    )
    contract = BUSINESS_ASSET_ROLES.get(requested)
    if contract is None or contract[0] != media_type:
        if requested:
            metadata["default_role_normalized_from"] = requested
        metadata["default_role"] = defaults[media_type]
    else:
        metadata["default_role"] = requested
    return metadata


WORKFLOW_KINDS = {
    "text_to_video": ("text_to_video", "h3_t2v_v1"),
    "image_to_video": ("image_to_video", "h3_i2v_v1"),
    "reference_replay": ("reference_replay", "h3_r2v_v1"),
    "specified_asset": (None, None),
    "continuation": ("reference_replay", "h3_r2v_v1"),
}
BATCH_STATUSES = {"scheduled", "queued", "running", "paused", "completed", "partial_failed", "cancelled", "deadline_reached"}
CHAIN_STATUSES = {"planned", "queued", "running", "awaiting_review", "approved", "rejected", "failed", "cancelled"}
ANNOTATION_CATEGORIES = {
    "technical", "continuity", "composition", "motion", "audio", "brand", "text", "human_shape",
    "commercial", "compliance", "other",
}
ANNOTATION_SEVERITIES = {"note", "warning", "critical"}
PUBLISH_ROLES = {"system_admin", "dept_admin", "admin", "biz_owner"}

OUTPUT_PRESET_DEFINITIONS = {
    "quick_preview": {
        "label": "极速试片",
        "description": "已验证的稳定默认档，适合快速判断镜头和创意。",
        "params": {"width": 480, "height": 864, "frames": 124, "fps": 24, "steps": 20, "seed": -1, "batch_count": 1, "audio_enabled": True},
        "benchmark_required": False,
    },
    "balanced_vertical": {
        "label": "均衡画质",
        "description": "更清晰的输出档位，已开放选择；首次使用建议先小批量试片。",
        "params": {"width": 576, "height": 1024, "frames": 124, "fps": 24, "steps": 20, "seed": -1, "batch_count": 1, "audio_enabled": True},
        "benchmark_required": True,
    },
    "hd_vertical": {
        "label": "高清画质",
        "description": "高分辨率输出档位，已开放选择；耗时和显存占用高于极速档。",
        "params": {"width": 864, "height": 1536, "frames": 124, "fps": 24, "steps": 20, "seed": -1, "batch_count": 1, "audio_enabled": True},
        "benchmark_required": True,
    },
}
SUPPORTED_OUTPUT_RATIOS = {"9:16", "16:9"}
REQUIRED_BENCHMARK_NODE_FAMILIES = ("rtx_5080", "rtx_pro_6000")
CREATIVE_OPTIONS = {
    "smart": {"label": "智能推荐"},
    "direct_prompt": {"label": "直接 H3 提示词"},
    "reference_replay": {"label": "参考复刻"},
    "talking_product": {"label": "口播换品"},
    "product_frame": {"label": "商品首帧"},
    "dialogue": {"label": "双人对话"},
    "continuation": {"label": "自动续写"},
}
DIRECT_PROMPT_PLANNER_MODEL = "operator-direct"
OPERATOR_BRIEF_PLANNER_MODEL = "operator-input"
SCRIPT_SPEECH_RATE_CPS = 4.0
SCRIPT_LINE_PAUSE_SECONDS = 0.25
H3_AUTO_REFERENCE_CLIP_SECONDS = 14.0
SOURCE_UNDERSTANDING_POLICY_VERSION = "material-source-understanding-v3"
SOURCE_UNDERSTANDING_MAX_VISUALS = 12
SOURCE_UNDERSTANDING_MAX_IMAGE_BYTES = 1_500_000
PRODUCTION_DURATION_OPTIONS = (3, *range(5, 16), 30)
_QUOTED_SPEECH_RE = re.compile(r'[“"]([^”"]+)[”"]|[‘\']([^’\']+)[’\']')


def production_duration_contract(value: Any) -> dict[str, int | bool | None]:
    """Map a business-facing duration to a truthful executable contract."""

    try:
        requested = int(value or 5)
    except (TypeError, ValueError):
        requested = 0
    if requested not in PRODUCTION_DURATION_OPTIONS:
        raise AppError("MEDIA_DURATION_INVALID", 422, {"allowed": list(PRODUCTION_DURATION_OPTIONS)})
    if requested == 3:
        return {
            "requested_seconds": 3,
            "generation_seconds": 4,
            "delivery_seconds": 3,
            "target_seconds": None,
            "continuation": False,
        }
    if requested == 30:
        return {
            "requested_seconds": 30,
            "generation_seconds": 15,
            "delivery_seconds": None,
            "target_seconds": 30,
            "continuation": True,
        }
    return {
        "requested_seconds": requested,
        "generation_seconds": requested,
        "delivery_seconds": None,
        "target_seconds": None,
        "continuation": False,
    }


def production_frames_for_duration(generation_seconds: int, *, fps: int = 24) -> int:
    # Preserve the verified five-second H3 default; all other native durations
    # use an exact fps multiple and stay within the Bridge frame allow-list.
    return 124 if generation_seconds == 5 and fps == 24 else generation_seconds * fps


def _core():
    from . import service

    return service


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _estimate_script_seconds(value: Any) -> float:
    """Estimate spoken Mandarin duration for clip-fit guidance, not billing."""

    normalized = normalize_frontdesk_script(value)
    lines = [
        item.strip()
        for item in str(normalized.get("normalized_script") or value or "").splitlines()
        if item.strip()
    ]
    spoken = "\n".join(re.sub(r"^[^：:\n]{1,8}[：:]\s*", "", item) for item in lines)
    chinese_or_digits = len(re.findall(r"[\u3400-\u9fff0-9]", spoken))
    latin_words = len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)?", spoken))
    punctuation = len(re.findall(r"[，。！？!?；;、…]", spoken))
    seconds = (
        chinese_or_digits / SCRIPT_SPEECH_RATE_CPS
        + latin_words / 2.5
        + punctuation * 0.08
        + max(0, len(lines) - 1) * SCRIPT_LINE_PAUSE_SECONDS
    )
    return round(seconds, 1)


def _fit_script_excerpt(script: str, duration_seconds: int) -> str:
    normalized = normalize_frontdesk_script(script)
    lines = [
        item.strip()
        for item in str(normalized.get("normalized_script") or script or "").splitlines()
        if item.strip()
    ]
    if not lines:
        return ""
    budget = max(1.0, float(duration_seconds) * 0.86)
    selected: list[str] = []
    for line in lines:
        candidate = "\n".join([*selected, line])
        if _estimate_script_seconds(candidate) <= budget:
            selected.append(line)
            continue
        if selected:
            break
        # Speaker turns are semantic units.  A raw character slice can leave
        # malformed dialogue such as ``会更`` and is worse than a slightly
        # fast but complete first turn.  Keep the first turn intact and let
        # the timing warning recommend 8 seconds or continuation when needed.
        selected.append(line)
        break
    return "\n".join(selected)


def _script_timing_for_clip(script: Any, duration_seconds: int) -> dict[str, Any]:
    script_normalization = normalize_frontdesk_script(script)
    normalized = _text(script_normalization.get("normalized_script") or script, 6000)
    estimated = _estimate_script_seconds(normalized)
    fits = not normalized or estimated <= float(duration_seconds) * 0.9
    excerpt = normalized if fits else _fit_script_excerpt(normalized, duration_seconds)
    if fits:
        action = "fits"
    elif estimated <= 8 * 0.9 and duration_seconds < 8:
        action = "switch_to_8s_or_use_excerpt"
    else:
        action = "split_or_continuation"
    return {
        "estimated_seconds": estimated,
        "clip_duration_seconds": duration_seconds,
        "fits": fits,
        "shot_script": excerpt,
        "original_line_count": len([item for item in normalized.splitlines() if item.strip()]),
        "shot_line_count": len([item for item in excerpt.splitlines() if item.strip()]),
        "recommended_action": action,
        "script_normalization": {
            "changed": bool(script_normalization.get("changed")),
            "turn_count": int(script_normalization.get("turn_count") or 0),
            "speaker_count": int(script_normalization.get("speaker_count") or 0),
            "policy_version": AD_MATERIAL_POLICY_VERSION,
        },
    }


def _recommended_dialogue_duration(script: Any, selected_seconds: int) -> int:
    """Recommend the shortest native H3 duration that keeps speech safe.

    The estimate intentionally includes delivery headroom: real governed TTS
    adds breaths and natural pauses which are longer than raw character-rate
    arithmetic.  This helper is advisory only: the selected operator duration
    remains authoritative and the deterministic excerpt contract below keeps
    the selected clip feasible.
    """

    estimated = _estimate_script_seconds(script)
    if not _text(script, 6000) or selected_seconds != 5 or estimated <= 4.5:
        return selected_seconds
    required = int(math.ceil(estimated / 0.72))
    candidates = [seconds for seconds in range(6, 16) if seconds >= required]
    # Native H3 clips stop at 15 seconds.  When a pasted front-desk script is
    # longer, use the longest legal clip and let the semantic-turn fitter keep
    # a complete excerpt instead of silently falling back to five seconds.
    return candidates[0] if candidates else 15


def _speech_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", str(value or "").casefold())


def _action_fitted_to_script(action: Any, excerpt: str) -> str:
    """Remove quoted dialogue beats that the deterministic clip fit omitted."""

    original = _text(action, 1200)
    excerpt_token = _speech_token(excerpt)
    pieces: list[str] = []
    cursor = 0
    for match in _QUOTED_SPEECH_RE.finditer(original):
        pieces.append(original[cursor:match.start()])
        quoted = next((item for item in match.groups() if item), "")
        quoted_token = _speech_token(quoted)
        if quoted_token and quoted_token in excerpt_token:
            pieces.append(match.group(0))
        else:
            # The wording immediately before an omitted quote normally names
            # the associated action (for example ``女方接着说``). Remove that
            # dangling cue as well so H3 cannot act out dialogue that no longer
            # exists in the fitted audio direction.
            prefix = pieces[-1]
            pieces[-1] = re.sub(
                r"(?:[，,；;。]\s*)?(?:[^，,；;。]{0,12}?)(?:接着|继续|然后|再)?(?:说|回答|反问|问)\s*$",
                "",
                prefix,
            )
        cursor = match.end()
    pieces.append(original[cursor:])
    fitted = "".join(pieces)
    fitted = re.sub(r"([，,；;]\s*){2,}", "，", fitted)
    fitted = re.sub(r"(?:并|再|然后)?(?:接着|继续)(?:说|回答|反问)?\s*(?=$|[，,；;。])", "", fitted)
    fitted = fitted.strip(" ，,；;。")
    directive = f"本镜头只表演以下口播：{excerpt.replace(chr(10), ' / ')}"
    return f"{fitted}；{directive}" if fitted else directive


def _apply_script_timing_to_plan(plan: dict[str, Any], timing: dict[str, Any]) -> dict[str, Any]:
    adapted = dict(plan)
    adapted["script_timing"] = dict(timing)
    if timing.get("fits") is not False:
        return adapted
    excerpt = _text(timing.get("shot_script"), 1200)
    duration = int(timing.get("clip_duration_seconds") or 5)
    estimate = float(timing.get("estimated_seconds") or 0)
    safe_budget = round(float(duration) * 0.9, 1)
    warning = (
        f"为避免语速过快，完整台词预计约 {estimate:g} 秒，已达到或超过 {duration} 秒镜头的"
        f"自然语速安全预算约 {safe_budget:g} 秒；"
        f"本镜头已自动收敛为：{excerpt.replace(chr(10), ' / ')}。完整台词请切到更长镜头或自动续写。"
    )
    warnings = [_text(item, 500) for item in _list(adapted.get("warnings")) if _text(item, 500)]
    if warning not in warnings:
        warnings.append(warning)
    adapted["warnings"] = warnings
    adapted["audio_prompt"] = f"本镜头只使用以下口播，不得补说被省略内容：{excerpt}"
    shots = []
    for index, raw in enumerate(_list(adapted.get("shots"))):
        shot = dict(raw) if isinstance(raw, dict) else {}
        if index == 0:
            shot["action"] = _action_fitted_to_script(shot.get("action"), excerpt)
            shot["audio"] = excerpt
        else:
            shot["action"] = "保持同一构图和自然环境动作，不新增台词或表演被省略内容"
            shot["audio"] = "保持同一环境声，不新增台词"
        shots.append(shot)
    adapted["shots"] = shots
    negatives = [_text(item, 300) for item in _list(adapted.get("negative_constraints")) if _text(item, 300)]
    timing_guardrail = "不得为了容纳被省略的完整台词而新增切镜、商品特写、人物换位或额外展示动作"
    if timing_guardrail not in negatives:
        negatives.append(timing_guardrail)
    adapted["negative_constraints"] = negatives
    return adapted


def _new_id(prefix: str) -> str:
    return _core()._new_id(prefix)


def _percentile(values: list[float], percentile: float) -> float | None:
    ordered = sorted(value for value in values if value > 0)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * percentile) - 1))
    return round(ordered[index], 2)


def _node_family(node: OpenClawInstance) -> str | None:
    caps = _core()._bridge_caps(node)
    haystack = json.dumps({"name": node.name, "gpu": caps.get("gpu")}, ensure_ascii=False).lower()
    if "5080" in haystack:
        return "rtx_5080"
    if "pro 6000" in haystack or "pro_6000" in haystack or "rtx 6000" in haystack:
        return "rtx_pro_6000"
    return None


def preset_benchmark_gate(
    preset_id: str,
    samples_by_node: dict[str, list[dict[str, Any]]],
    quick_samples_by_node: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    if preset_id == "quick_preview":
        return {
            "enabled": True,
            "benchmark_passed": True,
            "reason": "已验证的生产默认档",
            "nodes": {},
        }
    node_results: dict[str, Any] = {}
    benchmark_passed = True
    reasons: list[str] = []
    for family in REQUIRED_BENCHMARK_NODE_FAMILIES:
        samples = samples_by_node.get(family, [])
        quick = quick_samples_by_node.get(family, [])
        successes = [item for item in samples if item.get("status") == "completed"]
        success_rate = len(successes) / len(samples) if samples else 0.0
        durations = [float(item.get("duration_seconds") or 0) for item in successes]
        quick_durations = [
            float(item.get("duration_seconds") or 0)
            for item in quick
            if item.get("status") == "completed"
        ]
        p95 = _percentile(durations, 0.95)
        quick_p95 = _percentile(quick_durations, 0.95)
        passed = (
            len(samples) >= 5
            and success_rate >= 0.95
            and p95 is not None
            and quick_p95 is not None
            and p95 <= quick_p95 * 2
        )
        if not passed:
            benchmark_passed = False
            reasons.append(f"{family} 尚未完成 5 轮稳定基准或 P95 超标")
        node_results[family] = {
            "samples": len(samples),
            "successes": len(successes),
            "success_rate": round(success_rate, 4),
            "p95_seconds": p95,
            "quick_p95_seconds": quick_p95,
            "passed": passed,
        }
    return {
        # Benchmarks remain visible as operational evidence, but no longer hide
        # valid H3 dimensions from operators. Bridge still performs the final
        # dimension/workflow validation before a job is accepted by a node.
        "enabled": True,
        "benchmark_passed": benchmark_passed,
        "reason": (
            "双节点基准已通过"
            if benchmark_passed
            else f"已开放手动选择；{'；'.join(reasons)}"
        ),
        "nodes": node_results,
    }


def infer_production_mode(source_roles: list[dict[str, Any]], *, continuation: bool = False) -> str:
    if continuation:
        return "continuation"
    roles = {_text(_dict(item).get("role"), 40) for item in source_roles}
    technical_roles = {_text(_dict(item).get("technical_role"), 40) for item in source_roles}
    roles.discard("")
    technical_roles.discard("")
    technical_reference_roles = {"reference_image", "reference_video", "reference_audio"}
    business_reference_roles = {"visual_reference", "motion_reference", "audio_reference", "continuity_anchor"}
    if technical_roles & technical_reference_roles or roles & (technical_reference_roles | business_reference_roles):
        return "reference_replay"
    if "first_frame" in technical_roles or (
        not technical_roles and roles & {"character_first_frame", "product_packshot", "first_frame"}
    ):
        return "image_to_video"
    return "text_to_video"


def normalize_output_ratio(value: Any) -> str:
    ratio = _text(value, 20) or "9:16"
    if ratio not in SUPPORTED_OUTPUT_RATIOS:
        raise AppError(
            "MEDIA_OUTPUT_RATIO_INVALID",
            422,
            {"ratio": ratio, "allowed": sorted(SUPPORTED_OUTPUT_RATIOS)},
        )
    return ratio


def output_params_for_ratio(params: dict[str, Any], ratio: Any) -> dict[str, Any]:
    """Orient a benchmarked preset without changing its pixel or memory class."""

    normalized = normalize_output_ratio(ratio)
    oriented = dict(params)
    width = int(oriented.get("width") or 480)
    height = int(oriented.get("height") or 864)
    if normalized == "16:9" and width < height:
        oriented["width"], oriented["height"] = height, width
    elif normalized == "9:16" and width > height:
        oriented["width"], oriented["height"] = height, width
    oriented["ratio"] = normalized
    return oriented


def _normalize_source_mention(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    if len(token) > 65 or re.fullmatch(r"@[\w-]{1,64}", token, re.UNICODE) is None:
        raise AppError(
            "MEDIA_REFERENCE_MENTION_INVALID",
            422,
            {"mention_token": token[:80], "detail": "素材引用必须是 @ 开头的中文、字母、数字、下划线或短横线"},
        )
    return token


def _bind_source_mentions(
    brief: dict[str, Any], source_roles: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Bind business-facing @names to governed source asset ids.

    The mention is metadata only: the asset id remains the authority and H3
    still receives an allow-listed <Picture N>/<Video N>/<Audio N> reference.
    """

    source_text = f"{_text(brief.get('request'), 6000)}\n{_text(brief.get('script'), 6000)}"
    bindings: list[dict[str, str]] = []
    seen_tokens: set[str] = set()
    bound: list[dict[str, Any]] = []
    for source in source_roles:
        item = dict(source)
        token = _normalize_source_mention(item.get("mention_token"))
        if token:
            if token in seen_tokens:
                raise AppError("MEDIA_REFERENCE_MENTION_DUPLICATE", 422, {"mention_token": token})
            seen_tokens.add(token)
            present = re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", source_text, re.UNICODE) is not None
            item["mention_token"] = token
            item["mentioned"] = present
            if present:
                marker = f"用户在需求中以 {token} 明确指定"
                purpose = _text(item.get("purpose"), 500)
                item["purpose"] = purpose if marker in purpose else _text(
                    f"{marker}；{purpose}" if purpose else marker, 500
                )
                bindings.append({
                    "mention_token": token,
                    "asset_id": item["asset_id"],
                    "business_role": item["role"],
                    "technical_role": item["technical_role"],
                })
        bound.append(item)
    return {**brief, "source_mentions": bindings}, bound


def _normalize_source_roles(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        data = _dict(item)
        asset_id = _text(data.get("asset_id") or data.get("source_asset_id"), 50)
        role = _text(data.get("role"), 40)
        if not asset_id or not role:
            continue
        if role in BUSINESS_ASSET_ROLES:
            technical_role = BUSINESS_ASSET_ROLES[role][1]
            requested_technical_role = _text(data.get("technical_role"), 40)
            allowed_technical_roles = {
                "character_first_frame": {"first_frame", "reference_image"},
                "product_packshot": {"first_frame", "reference_image", "overlay_image"},
                "product_detail": {"reference_image", "overlay_image"},
                "visual_reference": {"reference_image"},
                "motion_reference": {"reference_video"},
                "audio_reference": {"reference_audio"},
                "continuity_anchor": {"reference_video"},
            }[role]
            if requested_technical_role in allowed_technical_roles:
                # The server prepare step may safely demote a business
                # packshot from first_frame to reference_image/overlay_image
                # when actors are generated independently. Preserve only that allow-listed
                # decision across prepare -> submit; never accept arbitrary
                # Bridge roles from the browser.
                technical_role = requested_technical_role
        elif role in {"first_frame", "reference_image", "reference_video", "reference_audio", "overlay_image"}:
            technical_role = role
        else:
            raise AppError("MEDIA_REFERENCE_ROLE_INVALID", 422, {"role": role})
        mention_token = _normalize_source_mention(data.get("mention_token"))
        normalized.append({
            "asset_id": asset_id,
            "role": role,
            "technical_role": technical_role,
            "purpose": _text(data.get("purpose"), 500),
            **({"role_locked": True} if data.get("role_locked") is True else {}),
            **({"mention_token": mention_token} if mention_token else {}),
        })
    if len({item["asset_id"] for item in normalized}) != len(normalized):
        raise AppError("MEDIA_REFERENCE_DUPLICATE", 422)
    # A product packshot is a first frame only for the simple I2V path.  Once
    # another reference is present the job becomes R2V and the same product
    # image is compiled as a reference image.  This keeps the business role in
    # lineage without sending the mutually-exclusive first_frame + references
    # combination to Bridge.
    reference_present = any(item["technical_role"] in {"reference_image", "reference_video", "reference_audio"} for item in normalized)
    if reference_present:
        for item in normalized:
            if item["role"] in {"character_first_frame", "product_packshot"}:
                item["technical_role"] = "reference_image"
    if any(item["technical_role"] == "first_frame" for item in normalized) and reference_present:
        raise AppError("MEDIA_REFERENCE_MODE_CONFLICT", 422, {"detail": "图生视频首帧不能与参考复刻素材混用"})
    return normalized


def _strict_reference_replay_requested(brief: dict[str, Any], source_roles: list[dict[str, Any]]) -> bool:
    if not any(item.get("role") in {"motion_reference", "continuity_anchor"} for item in source_roles):
        return False
    text = " ".join(
        _text(brief.get(key), 6000)
        for key in ("request", "requirement", "script", "creative_angle")
    ).casefold()
    return any(term in text for term in ("一比一", "1:1", "复刻", "只换产品", "替换产品", "更换产品", "换产品"))


def _normalize_source_understanding(value: Any, source_roles: list[dict[str, Any]]) -> dict[str, Any] | None:
    raw = _dict(value)
    raw_assets = {
        _text(item.get("asset_id"), 50): item
        for item in _list(raw.get("assets"))
        if isinstance(item, dict) and _text(item.get("asset_id"), 50)
    }
    allowed_semantics = {
        "source_video", "product_package", "product_unit", "person_scene", "scene_reference", "other",
    }
    normalized_assets: list[dict[str, Any]] = []
    for source in source_roles:
        asset_id = source["asset_id"]
        item = _dict(raw_assets.get(asset_id))
        evidence_status = "verified" if asset_id in raw_assets else "missing"
        semantic_role = _text(item.get("semantic_role"), 40)
        if source.get("role") in {"motion_reference", "continuity_anchor"}:
            semantic_role = "source_video"
        if semantic_role not in allowed_semantics:
            semantic_role = "source_video" if source.get("role") in {"motion_reference", "continuity_anchor"} else "other"
        presence = _text(item.get("people_presence"), 30)
        if presence not in {"none", "partial_hands", "full_person", "multiple_people", "unknown"}:
            presence = "unknown"
        try:
            people_count = max(0, min(int(item.get("people_count") or 0), 20))
        except (TypeError, ValueError):
            people_count = 0
        suggested = {
            "source_video": source.get("role") if source.get("role") in {"motion_reference", "continuity_anchor"} else "motion_reference",
            "product_package": "product_packshot",
            "product_unit": "product_detail",
            "person_scene": "character_first_frame",
            "scene_reference": "visual_reference",
        }.get(semantic_role, source.get("role"))
        shots = []
        for shot in _list(item.get("shots"))[:12]:
            if not isinstance(shot, dict):
                continue
            try:
                start = max(0.0, float(shot.get("start_seconds") or 0))
                end = max(start, float(shot.get("end_seconds") or start))
            except (TypeError, ValueError):
                continue
            shots.append({
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "framing": _text(shot.get("framing"), 160),
                "subject": _text(shot.get("subject"), 500),
                "action": _text(shot.get("action"), 500),
                "scene": _text(shot.get("scene"), 500),
                "camera_command": _text(shot.get("camera_command"), 80) or "[Static shot]",
                "product_slot": _text(shot.get("product_slot"), 300),
                "source_text": _text(shot.get("source_text"), 300),
            })
        normalized_assets.append({
            "asset_id": asset_id,
            "evidence_status": evidence_status,
            "semantic_role": semantic_role,
            "suggested_business_role": suggested,
            "people_presence": presence,
            "people_count": people_count,
            "contains_product": item.get("contains_product") is True,
            "contains_source_text": item.get("contains_source_text") is True,
            "framing": _text(item.get("framing"), 120),
            "visible_body_region": _text(item.get("visible_body_region"), 160),
            "both_shoulder_lines_visible": (
                item.get("both_shoulder_lines_visible")
                if isinstance(item.get("both_shoulder_lines_visible"), bool)
                else None
            ),
            "both_elbows_visible": (
                item.get("both_elbows_visible")
                if isinstance(item.get("both_elbows_visible"), bool)
                else None
            ),
            "waist_support_visible": (
                item.get("waist_support_visible")
                if isinstance(item.get("waist_support_visible"), bool)
                else None
            ),
            "inward_partner_gaze": (
                item.get("inward_partner_gaze")
                if isinstance(item.get("inward_partner_gaze"), bool)
                else None
            ),
            "faces_visibly_distinct": (
                item.get("faces_visibly_distinct")
                if isinstance(item.get("faces_visibly_distinct"), bool)
                else None
            ),
            "visual_summary": _text(item.get("visual_summary"), 1000),
            "product_kind": _text(item.get("product_kind"), 120),
            "shots": shots,
        })
    if not normalized_assets:
        return None
    return {
        "summary": _text(raw.get("summary"), 1600),
        "assets": normalized_assets,
        "warnings": [_text(item, 500) for item in _list(raw.get("warnings"))[:12] if _text(item, 500)],
    }


def _source_understanding_complete(
    understanding: dict[str, Any] | None,
    source_roles: list[dict[str, Any]],
) -> bool:
    """Require evidence for every visual input before planning may continue."""

    facts = {
        _text(item.get("asset_id"), 50): item
        for item in _list(_dict(understanding).get("assets"))
        if isinstance(item, dict) and _text(item.get("asset_id"), 50)
    }
    for source in source_roles:
        fact = _dict(facts.get(source["asset_id"]))
        if fact.get("evidence_status") != "verified":
            return False
        if source.get("role") in {"motion_reference", "continuity_anchor"}:
            if fact.get("semantic_role") != "source_video":
                return False
            if fact.get("people_presence") == "unknown" or not _list(fact.get("shots")):
                return False
            continue
        if fact.get("semantic_role") not in {
            "product_package", "product_unit", "person_scene", "scene_reference",
        }:
            return False
    return True


def _source_understanding_fingerprint(
    assets: list[ProjectRunAsset], source_roles: list[dict[str, Any]]
) -> str:
    asset_by_id = {asset.id: asset for asset in assets}
    rows = [
        f"{item['asset_id']}:{getattr(asset_by_id.get(item['asset_id']), 'sha256', '')}:{item.get('role')}"
        for item in source_roles
    ]
    material = SOURCE_UNDERSTANDING_POLICY_VERSION + "|" + "|".join(sorted(rows))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _render_source_understanding_visuals(
    assets: list[ProjectRunAsset], durations: dict[str, float | None]
) -> list[tuple[str, bytes]]:
    ffmpeg = _core()._media_quality_ffmpeg_executable()
    visuals: list[tuple[str, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="sf-source-understanding-") as temp_dir:
        root = Path(temp_dir)
        for asset in assets:
            if len(visuals) >= SOURCE_UNDERSTANDING_MAX_VISUALS:
                break
            mime = str(asset.mime_type or "").lower()
            source_path = project_service._project_run_asset_abs_path(asset)
            if mime.startswith("image/"):
                output = root / f"{asset.id}-image.jpg"
                proc = subprocess.run(
                    [
                        ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source_path),
                        "-vf", "scale='min(720,iw)':-2:flags=lanczos", "-frames:v", "1", "-q:v", "3", "-y", str(output),
                    ],
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                if proc.returncode == 0 and output.is_file():
                    content = output.read_bytes()
                    if 0 < len(content) <= SOURCE_UNDERSTANDING_MAX_IMAGE_BYTES:
                        visuals.append((f"asset={asset.id}; kind=image; file={asset.file_name}", content))
                continue
            if not mime.startswith("video/"):
                continue
            duration = max(float(durations.get(asset.id) or 0), 0.1)
            frame_count = min(6, max(2, int(math.ceil(duration))))
            for index in range(frame_count):
                if len(visuals) >= SOURCE_UNDERSTANDING_MAX_VISUALS:
                    break
                at = min(max(0.0, duration - 0.05), (duration * index / max(frame_count - 1, 1)))
                output = root / f"{asset.id}-frame-{index + 1:02d}.jpg"
                proc = subprocess.run(
                    [
                        ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(source_path),
                        "-vf", "scale='min(640,iw)':-2:flags=lanczos", "-frames:v", "1", "-q:v", "3", "-y", str(output),
                    ],
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                if proc.returncode == 0 and output.is_file():
                    content = output.read_bytes()
                    if 0 < len(content) <= SOURCE_UNDERSTANDING_MAX_IMAGE_BYTES:
                        visuals.append((f"asset={asset.id}; kind=video_frame; time={at:.3f}s; file={asset.file_name}", content))
    return visuals


async def _understand_source_assets(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    source_roles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    visual_role_items = [
        item for item in source_roles
        if BUSINESS_ASSET_ROLES.get(item.get("role"), ("", ""))[0] in {"image", "video"}
        or item.get("technical_role") in {"first_frame", "reference_image", "reference_video"}
    ]
    if not visual_role_items:
        return None
    assets: list[ProjectRunAsset] = []
    durations: dict[str, float | None] = {}
    for item in visual_role_items:
        asset = await db.get(ProjectRunAsset, item["asset_id"])
        same_run = bool(asset and asset.project_run_id == run.id)
        same_project_department = bool(
            asset
            and asset.project_id == run.project_id
            and not (asset.department_id and run.department_id and asset.department_id != run.department_id)
        )
        if asset is None or not (same_run or same_project_department):
            raise AppError("PROJECT_ASSET_NOT_FOUND", 404, {"asset_id": item["asset_id"]})
        assets.append(asset)
        if str(asset.mime_type or "").lower().startswith("video/"):
            durations[asset.id] = await _reference_asset_duration(db, asset)
    fingerprint = _source_understanding_fingerprint(assets, visual_role_items)
    anchor = assets[0]
    cached = _dict(_dict(anchor.metadata_json).get("source_understanding_cache"))
    if (
        cached.get("fingerprint") == fingerprint
        and cached.get("policy_version") == SOURCE_UNDERSTANDING_POLICY_VERSION
    ):
        result = _normalize_source_understanding(cached.get("result"), visual_role_items)
        if result and _source_understanding_complete(result, visual_role_items):
            return {**result, "model": _text(cached.get("model"), 180), "cache_hit": True, "fingerprint": fingerprint}

    visuals = await asyncio.to_thread(_render_source_understanding_visuals, assets, durations)
    if not visuals:
        return None
    parts: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            "分析以下生产素材。文件名和已有角色只是定位信息，不是视觉结论。逐资产返回事实，"
            "尤其区分完整人物、仅局部手部、商品包装、单片/散片、场景和画面文字。"
        ),
    }]
    for label, content in visuals:
        parts.extend([
            {"type": "text", "text": label},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(content).decode("ascii")}},
        ])
    system = (
        "你是投流视频生产前的素材取证分析器，只描述可见事实，不做创意、不补剧情、不猜测画外人物。"
        "严格输出 JSON：summary、assets、warnings。assets 必须逐个覆盖输入 asset_id。"
        "每个资产字段：asset_id、semantic_role、people_presence、people_count、contains_product、"
        "contains_source_text、framing、visible_body_region、both_shoulder_lines_visible、both_elbows_visible、"
        "waist_support_visible、inward_partner_gaze、faces_visibly_distinct、visual_summary、product_kind、shots。"
        "布尔字段看不清时返回 null，禁止猜测。framing 使用 close_up、medium_close、waist_up_two_shot、"
        "medium_two_shot、wide 或 other。semantic_role 只能是 source_video、"
        "product_package、product_unit、person_scene、scene_reference、other；people_presence 只能是 none、"
        "partial_hands、full_person、multiple_people、unknown。people_presence 与 people_count 必须分开判断："
        "full_person 表示画面中的人物身体完整可见，不代表只有一人；multiple_people 表示多人存在但身体完整性不确定。"
        "视频 shots 按时间返回 start_seconds、end_seconds、"
        "framing、subject、action、scene、camera_command、product_slot、source_text。局部手指或手掌不得算完整人物，"
        "看不到脸和身体时禁止声称存在男/女演员。包装盒与独立单片必须分开标注。"
    )
    config = await get_ai_profile_config(model_profile="vision", require_system_config=True)
    model = _text(config.get("ai.model"), 180) or "vision-profile"
    raw = None
    for attempt in (1, 2):
        raw = await call_llm_multimodal(
            system,
            parts,
            max_tokens=3200,
            temperature=0.1,
            timeout=180,
            json_mode=True,
            call_source="material_workbench_source_understanding",
            cost_context={
                "user_id": _user_id(user),
                "project_run_id": run.id,
                "attempt": attempt,
                "policy_version": SOURCE_UNDERSTANDING_POLICY_VERSION,
                "source_fingerprint": fingerprint,
            },
            model_profile="vision",
            require_system_config=True,
        )
        normalized = _normalize_source_understanding(raw, visual_role_items)
        if normalized and _source_understanding_complete(normalized, visual_role_items):
            metadata = _dict(anchor.metadata_json)
            anchor.metadata_json = {
                **metadata,
                "source_understanding_cache": {
                    "policy_version": SOURCE_UNDERSTANDING_POLICY_VERSION,
                    "fingerprint": fingerprint,
                    "model": model,
                    "result": normalized,
                    "analyzed_at": isoformat_bjt(now_bjt()),
                },
            }
            await db.flush()
            return {**normalized, "model": model, "cache_hit": False, "fingerprint": fingerprint}
    return None


def _reconcile_source_roles_with_understanding(
    source_roles: list[dict[str, Any]], understanding: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    facts = {
        item.get("asset_id"): item
        for item in _list(_dict(understanding).get("assets"))
        if isinstance(item, dict)
    }
    corrected: list[dict[str, Any]] = []
    changes: list[dict[str, str]] = []
    for source in source_roles:
        item = dict(source)
        fact = _dict(facts.get(item["asset_id"]))
        suggested = _text(fact.get("suggested_business_role"), 40)
        original = item.get("role")
        may_correct = not item.get("role_locked") and original in {"character_first_frame", "visual_reference"}
        if may_correct and suggested in {"product_packshot", "product_detail"}:
            item["role"] = suggested
            item["technical_role"] = BUSINESS_ASSET_ROLES[suggested][1]
            item["purpose"] = "Qwen-VL 视觉确认的真实商品包装" if suggested == "product_packshot" else "Qwen-VL 视觉确认的真实商品单片或细节"
            changes.append({"asset_id": item["asset_id"], "from": str(original), "to": suggested})
        corrected.append(item)
    return _normalize_source_roles(corrected), changes


def _source_replication_contract(
    brief: dict[str, Any], source_roles: list[dict[str, Any]], understanding: dict[str, Any] | None
) -> dict[str, Any]:
    strict = _strict_reference_replay_requested(brief, source_roles)
    assets = _list(_dict(understanding).get("assets"))
    source_videos = [item for item in assets if isinstance(item, dict) and item.get("semantic_role") == "source_video"]
    product_assets = [
        item for item in assets
        if isinstance(item, dict) and item.get("semantic_role") in {"product_package", "product_unit"}
    ]
    people_presence = "unknown"
    if source_videos:
        presences = {_text(item.get("people_presence"), 30) for item in source_videos}
        if presences <= {"none", "partial_hands"}:
            people_presence = "partial_hands" if "partial_hands" in presences else "none"
        elif "multiple_people" in presences:
            people_presence = "multiple_people"
        elif "full_person" in presences:
            people_presence = "full_person"
    return {
        "enabled": bool(strict and source_videos),
        "mode": "structure_replay_product_replace" if strict and product_assets else "structure_replay",
        "preserve_shot_timing": True,
        "preserve_composition": True,
        "preserve_camera": True,
        "replace_only_product_slots": bool(product_assets),
        "remove_source_overlays": brief.get("strip_reference_text") is True,
        "allow_approved_product_surface_text": bool(product_assets),
        "source_people_presence": people_presence,
        "forbid_invented_people": people_presence in {"none", "partial_hands"},
        "source_shots": [shot for item in source_videos for shot in _list(item.get("shots"))][:12],
        "product_assets": [
            {
                "asset_id": item.get("asset_id"),
                "semantic_role": item.get("semantic_role"),
                "product_kind": item.get("product_kind"),
                "visual_summary": item.get("visual_summary"),
            }
            for item in product_assets
        ],
    }


def _apply_replication_facts_to_brief(
    brief: dict[str, Any], contract: dict[str, Any] | None
) -> dict[str, Any]:
    """Let verified source facts override stale browser content checkboxes."""

    updated = dict(brief)
    data = _dict(contract)
    if (
        data.get("enabled") is True
        and data.get("source_people_presence") in {"none", "partial_hands"}
    ):
        updated["contains_person"] = False
        updated["source_people_presence"] = data.get("source_people_presence")
    return updated


def _apply_source_understanding_to_plan(plan: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    contract = _dict(brief.get("source_replication_contract"))
    if not contract.get("enabled"):
        return plan
    updated = dict(plan)
    source_shots = [item for item in _list(contract.get("source_shots")) if isinstance(item, dict)]
    product_names = [
        _text(item.get("visual_summary") or item.get("product_kind"), 300)
        for item in _list(contract.get("product_assets"))
        if isinstance(item, dict)
    ]
    product_directive = "；替换为已上传的目标商品参考（" + "、".join(filter(None, product_names)) + "）" if product_names else ""
    if source_shots:
        shots = []
        for raw in source_shots:
            shot = dict(raw)
            subject = _text(shot.get("subject"), 500) or "源视频中可见主体"
            action = _text(shot.get("action"), 500) or "严格保持源视频动作和时间关系"
            if contract.get("replace_only_product_slots"):
                action += product_directive + "；只替换源视频中的商品槽位，其他结构不变"
            shots.append({
                "start_seconds": shot.get("start_seconds", 0),
                "end_seconds": shot.get("end_seconds", 0),
                "framing": _text(shot.get("framing"), 160) or "保持源视频景别",
                "subject": subject,
                "action": action,
                "scene": _text(shot.get("scene"), 500) or "保持源视频场景",
                "lighting": "保持源视频光线、色温和曝光关系",
                "mood": "保持源视频节奏",
                "camera_command": _text(shot.get("camera_command"), 80) or "[Static shot]",
                "audio": "无同步口播；不重建源视频文字",
                "product_slot": _text(shot.get("product_slot"), 300),
            })
        updated["shots"] = shots
    updated["creative_goal"] = (
        "严格复刻源视频的镜头时长、构图、相机、主体数量与动作，只替换已识别的商品槽位；"
        "删除源字幕、角标和底部声明，保留目标商品参考图自身经授权的包装外观。"
    )
    negatives = [_text(item, 300) for item in _list(updated.get("negative_constraints")) if _text(item, 300)]
    negatives.extend([
        "不得新增源视频中不存在的人物、脸、身体、对白、场景、镜头或道具",
        "不得复制源视频字幕、角标、水印、底部声明、价格、促销字样或伪文字",
    ])
    updated["negative_constraints"] = list(dict.fromkeys(negatives))
    updated["source_replication_contract"] = contract
    return updated


async def _reference_asset_duration(db: AsyncSession, asset: ProjectRunAsset) -> float | None:
    duration = _core()._reference_duration_seconds(asset)
    if duration is not None:
        return duration
    probe = await asyncio.to_thread(
        project_service._probe_project_media_file,
        project_service._project_run_asset_abs_path(asset),
        asset.mime_type,
    )
    if probe:
        asset.metadata_json = {**_dict(asset.metadata_json), "media_probe": probe}
        await db.flush()
    return _core()._reference_duration_seconds(asset)


async def _ensure_h3_reference_clip(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    asset: ProjectRunAsset,
    *,
    kind: str,
    source_duration_seconds: float,
    suppress_text: bool = False,
    clip_strategy: str = "opening",
) -> ProjectRunAsset:
    """Create one deterministic, traceable H3-safe clip for a long source.

    Motion references use the opening of the source.  Continuation anchors use
    the tail so the next generated clip sees the last identity, wardrobe,
    product, colour-grade and audio state rather than an unrelated opening.
    """

    if clip_strategy not in {"opening", "tail"}:
        raise AppError(
            "MEDIA_REFERENCE_PREPROCESS_FAILED",
            422,
            {"asset_id": asset.id, "detail": "unsupported H3 reference clip strategy"},
        )

    extension = ".mp4" if kind == "video" else ".m4a"
    policy_suffix = "-motion-only-no-text-v2" if kind == "video" and suppress_text else ""
    clip_start_seconds = (
        max(0.0, float(source_duration_seconds) - H3_AUTO_REFERENCE_CLIP_SECONDS)
        if clip_strategy == "tail"
        else 0.0
    )
    derived_name = (
        f"h3-reference-{asset.id}-{clip_strategy}-{int(H3_AUTO_REFERENCE_CLIP_SECONDS)}s"
        f"{policy_suffix}{extension}"
    )
    existing = (
        await db.execute(
            select(ProjectRunAsset)
            .where(
                ProjectRunAsset.project_run_id == run.id,
                ProjectRunAsset.file_name == derived_name,
            )
            .order_by(ProjectRunAsset.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing_duration = await _reference_asset_duration(db, existing)
        if existing_duration is not None and 2 <= existing_duration <= 15:
            return existing

    ffmpeg = _core()._media_quality_ffmpeg_executable()
    source_path = project_service._project_run_asset_abs_path(asset)
    with tempfile.TemporaryDirectory(prefix="sf-h3-reference-") as temp_dir:
        output_path = Path(temp_dir) / f"reference{extension}"
        if kind == "video":
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(clip_start_seconds), "-i", str(source_path),
                "-t", str(H3_AUTO_REFERENCE_CLIP_SECONDS), "-map", "0:v:0",
            ]
            if suppress_text:
                # Reference replay otherwise learns burned-in subtitles, lower thirds and
                # compliance copy as visual content.  The governed no-text path keeps only
                # low-frequency pose, composition and motion.  Source speech is also removed:
                # H3 can otherwise regenerate captions from the embedded dialogue even after
                # the glyph pixels themselves have been blurred.
                command.extend([
                    "-vf", "scale=32:-2:flags=area,scale=576:-2:flags=bicubic,gblur=sigma=8",
                    "-an",
                ])
            else:
                command.extend(["-map", "0:a?"])
            command.extend([
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            ])
            if not suppress_text:
                command.extend(["-c:a", "aac", "-b:a", "128k"])
            command.extend(["-movflags", "+faststart", "-y", str(output_path)])
            mime_type = "video/mp4"
        else:
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(clip_start_seconds), "-i", str(source_path),
                "-t", str(H3_AUTO_REFERENCE_CLIP_SECONDS), "-map", "0:a:0",
                "-c:a", "aac", "-b:a", "192k", "-y", str(output_path),
            ]
            mime_type = "audio/mp4"

        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            timeout=300,
        )
        if completed.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
            raise AppError(
                "MEDIA_REFERENCE_PREPROCESS_FAILED",
                422,
                {
                    "asset_id": asset.id,
                    "detail": _text(completed.stderr.decode("utf-8", errors="replace"), 1000)
                    or "ffmpeg did not create the H3 reference clip",
                },
            )
        uploaded = await project_service.upload_project_run_asset(
            db,
            user,
            run.id,
            file_name=derived_name,
            mime_type=mime_type,
            content=output_path.read_bytes(),
            metadata={
                "source": "h3_auto_reference_clip",
                "source_asset_id": asset.id,
                "source_sha256": asset.sha256,
                "source_duration_seconds": round(source_duration_seconds, 3),
                "clip_start_seconds": round(clip_start_seconds, 3),
                "clip_duration_seconds": H3_AUTO_REFERENCE_CLIP_SECONDS,
                "clip_strategy": clip_strategy,
                "strip_reference_text": bool(kind == "video" and suppress_text),
                "reference_usage": "motion_composition_timing_only" if kind == "video" and suppress_text else "full_reference",
                "audio_policy": "stripped_to_prevent_caption_leakage" if kind == "video" and suppress_text else "preserve_if_present",
            },
        )
    derived = await db.get(ProjectRunAsset, _text(_dict(uploaded.get("asset")).get("id"), 50))
    if derived is None:
        raise AppError("MEDIA_REFERENCE_PREPROCESS_FAILED", 422, {"asset_id": asset.id})
    duration = await _reference_asset_duration(db, derived)
    if duration is None or duration < 2 or duration > 15:
        raise AppError(
            "MEDIA_REFERENCE_PREPROCESS_FAILED",
            422,
            {"asset_id": asset.id, "derived_asset_id": derived.id, "duration_seconds": duration},
        )
    return derived


async def _ensure_h3_identity_anchor(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    asset: ProjectRunAsset,
) -> ProjectRunAsset:
    """Extract a governed center-person anchor without the usual caption/footer bands."""

    derived_name = f"h3-reference-{asset.id}-identity-anchor-v1.jpg"
    existing = (
        await db.execute(
            select(ProjectRunAsset)
            .where(
                ProjectRunAsset.project_run_id == run.id,
                ProjectRunAsset.file_name == derived_name,
            )
            .order_by(ProjectRunAsset.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    ffmpeg = _core()._media_quality_ffmpeg_executable()
    source_path = project_service._project_run_asset_abs_path(asset)
    with tempfile.TemporaryDirectory(prefix="sf-h3-identity-anchor-") as temp_dir:
        output_path = Path(temp_dir) / "identity-anchor.jpg"
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.2", "-i", str(source_path),
            "-frames:v", "1",
            "-vf", "crop=trunc(iw*0.70/2)*2:trunc(ih*0.60/2)*2:trunc(iw*0.15/2)*2:0,scale=576:-2:flags=lanczos",
            "-q:v", "2", "-y", str(output_path),
        ]
        completed = await asyncio.to_thread(subprocess.run, command, capture_output=True, timeout=120)
        if completed.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
            raise AppError(
                "MEDIA_REFERENCE_PREPROCESS_FAILED",
                422,
                {
                    "asset_id": asset.id,
                    "detail": _text(completed.stderr.decode("utf-8", errors="replace"), 1000)
                    or "ffmpeg did not create the source identity anchor",
                },
            )
        uploaded = await project_service.upload_project_run_asset(
            db,
            user,
            run.id,
            file_name=derived_name,
            mime_type="image/jpeg",
            content=output_path.read_bytes(),
            metadata={
                "source": "h3_reference_identity_anchor",
                "source_asset_id": asset.id,
                "source_sha256": asset.sha256,
                "frame_seconds": 0.2,
                "crop_policy": "center_person_top_60_v1",
            },
        )
    derived = await db.get(ProjectRunAsset, _text(_dict(uploaded.get("asset")).get("id"), 50))
    if derived is None:
        raise AppError("MEDIA_REFERENCE_PREPROCESS_FAILED", 422, {"asset_id": asset.id})
    return derived


async def _materialize_h3_source_roles(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    source_roles: list[dict[str, Any]],
    *,
    suppress_reference_text: bool = False,
    reference_identity_policy: str = "replace_actor",
    preserve_identity_anchor: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replace long video/audio business sources with governed H3-safe derivatives."""

    materialized: list[dict[str, Any]] = []
    preprocessing: list[dict[str, Any]] = []
    for item in source_roles:
        if item["technical_role"] not in {"reference_video", "reference_audio"}:
            materialized.append(item)
            continue
        asset = await db.get(ProjectRunAsset, item["asset_id"])
        same_run = bool(asset and asset.project_run_id == run.id)
        same_project_department = bool(
            asset
            and asset.project_id == run.project_id
            and not (asset.department_id and run.department_id and asset.department_id != run.department_id)
        )
        if asset is None or not (same_run or same_project_department):
            raise AppError("PROJECT_ASSET_NOT_FOUND", 404, {"asset_id": item["asset_id"]})
        kind = _core()._reference_asset_kind(asset)
        if kind not in {"video", "audio"}:
            materialized.append(item)
            continue
        duration = await _reference_asset_duration(db, asset)
        suppress_text = bool(suppress_reference_text and kind == "video")
        if duration is None or (duration <= 15 and not suppress_text):
            materialized.append(item)
            continue
        clip_strategy = "tail" if item.get("role") == "continuity_anchor" else "opening"
        derived = await _ensure_h3_reference_clip(
            db,
            user,
            run,
            asset,
            kind=kind,
            source_duration_seconds=duration,
            suppress_text=suppress_text,
            clip_strategy=clip_strategy,
        )
        identity_anchor = None
        if suppress_text and reference_identity_policy == "preserve_source" and preserve_identity_anchor:
            identity_anchor = await _ensure_h3_identity_anchor(db, user, run, asset)
            materialized.append({
                "asset_id": identity_anchor.id,
                "original_asset_id": asset.id,
                "role": "visual_reference",
                "technical_role": "reference_image",
                "purpose": "原人物身份锚点；只用于近似保持成年人物脸型、发型和稳定身份，不得复制任何画面文字",
                "preprocessing": "source_identity_anchor",
            })
        purpose = item["purpose"]
        if suppress_text:
            purpose = (
                "仅参考源视频可见主体（包括商品或局部手部）的粗粒度姿态、动作、构图、镜头节奏和时间关系；"
                "输入已低通清除文字，"
                "不得复制或重建原视频中的字幕、角标、贴纸、商品卡、Logo、水印、声明、资质号、字母、数字或伪文字"
            )
        materialized.append({
            **item,
            "asset_id": derived.id,
            "original_asset_id": asset.id,
            "purpose": purpose,
            "preprocessing": (
                f"{clip_strategy}_clip_motion_only_no_text" if suppress_text else f"{clip_strategy}_clip"
            ),
        })
        preprocessing.append({
            "source_asset_id": asset.id,
            "derived_asset_id": derived.id,
            "file_name": asset.file_name,
            "source_duration_seconds": round(duration, 3),
            "clip_start_seconds": round(
                max(0.0, float(duration) - H3_AUTO_REFERENCE_CLIP_SECONDS)
                if clip_strategy == "tail"
                else 0.0,
                3,
            ),
            "clip_duration_seconds": H3_AUTO_REFERENCE_CLIP_SECONDS,
            "strategy": (
                f"{clip_strategy}_clip_motion_only_no_text" if suppress_text else f"{clip_strategy}_clip"
            ),
            **({"strip_reference_text": True} if suppress_text else {}),
            **({"identity_anchor_asset_id": identity_anchor.id} if identity_anchor is not None else {}),
            **({"reference_identity_policy": reference_identity_policy} if suppress_text else {}),
        })
    return materialized, preprocessing


def _product_from_material_hints(hints: list[str]) -> str | None:
    text = " ".join(_text(item, 500) for item in hints).lower()
    rules = (
        (("魔力玻玻", "玻尿酸", "水感"), "示例品牌 魔力玻玻"),
        (("超快感", "快感套", "酥麻"), "示例品牌 超快感"),
        (("持久", "延时"), "示例品牌 持久"),
        (("001", "隐形套"), "示例品牌 001 隐形系列"),
        (("air", "空气套", "铂金"), "示例品牌 AIR / 铂金"),
    )
    return next((product for keywords, product in rules if any(keyword in text for keyword in keywords)), None)


def _default_request_for_mode(
    mode: str,
    *,
    has_assets: bool,
    script: str = "",
    contains_person: bool = False,
) -> str:
    common = "9:16 竖屏投流镜头，前 5 秒建立明确视觉钩子；不编造价格、活动、功效、包装文字或 Logo。"
    if _text(script, 6000):
        dialogue = int(normalize_frontdesk_script(script).get("speaker_count") or 0) >= 2
        people = "同一对明确成年人物" if dialogue else "同一位明确成年人物"
        reference = "保持所选首帧的人物身份、服装、场景和构图连续；" if mode == "image_to_video" else ""
        return (
            f"把完整台词按当前镜头时长自动拆段，本镜头只使用完整语义回合；{reference}"
            f"{people}在同一场景自然表演，默认一镜到底，不为容纳更多台词新增切镜或人物换位。"
            f"没有正确商品图时不生成包装、Logo 或文字，商品画面留给独立商品镜头或后期合成。{common}"
        )
    if mode == "image_to_video":
        subject = "人物身份、服装、场景连续" if contains_person else "商品外观和包装真实性"
        return f"以已选图片作为正确首帧，保持{subject}，生成自然连贯的动态展示。{common}"
    if mode == "reference_replay":
        return f"参考已选素材的镜头结构、人物状态、动作和节奏；商品、文字和声音只采用素材中可验证的信息。{common}"
    if mode == "continuation":
        return f"从已选源视频自然续写，保持主体、构图、光线、运动方向和声音氛围连续。{common}"
    if has_assets:
        return f"根据已选素材生成一条结构清晰、节奏紧凑的投流镜头。{common}"
    return "生成轻薄、舒适、安心的无品牌抽象 B-roll，供后期与已授权真实产品图组合；无包装、Logo、文字、人物或价格活动信息。9:16 竖屏，前 5 秒建立视觉钩子。"


def _resolve_audio_brief_conflicts(brief: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Make the explicit audio switch authoritative over stale prose defaults."""

    updated = dict(brief)
    script = _text(updated.get("script"), 6000)
    if not script or updated.get("audio_enabled") is not True:
        return updated, []
    request = _text(updated.get("request") or updated.get("requirement"), 6000)
    if not request:
        return updated, []
    patterns = (
        r"(?:^|[；;。])\s*(?:\d+(?:\.\d+)?秒)?(?:本镜头)?(?:全程)?(?:保持)?无声(?:音)?(?:输出)?\s*(?=$|[；;。])",
        r"(?:^|[；;。])\s*不得有(?:任何)?音频(?:内容)?\s*(?=$|[；;。])",
        r"(?:^|[；;。])\s*关闭(?:同步)?音频\s*(?=$|[；;。])",
    )
    normalized = request
    replacements = 0
    for pattern in patterns:
        normalized, count = re.subn(pattern, "。", normalized, flags=re.IGNORECASE)
        replacements += count
    if replacements == 0:
        return updated, []
    normalized = re.sub(r"[。；;]{2,}", "。", normalized).strip("。；; ")
    updated["request"] = normalized
    updated.pop("requirement", None)
    return updated, ["audio_requirement_conflict_resolved"]


def _infer_brief_content_flags(
    brief: dict[str, Any],
    source_roles: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Derive person/voice facts before planning, routing and compilation.

    These are content facts, not model tuning knobs.  A character first frame
    and speaker-labelled dialogue must not depend on an operator discovering
    advanced checkboxes before the system applies identity/audio guardrails.
    """

    updated = dict(brief)
    applied: list[str] = []
    script = _text(updated.get("script"), 6000)
    script_contract = normalize_frontdesk_script(script)
    visible_speaker_turns = [
        item
        for item in _list(script_contract.get("turns"))
        if _text(_dict(item).get("speaker"), 24) not in {"旁白", "画外音"}
    ]
    has_character_frame = any(
        _text(item.get("role"), 40) == "character_first_frame"
        for item in source_roles
        if isinstance(item, dict)
    )
    replication_contract = _dict(updated.get("source_replication_contract"))
    has_verified_source_people = replication_contract.get("source_people_presence") in {
        "full_person",
        "multiple_people",
    }
    if (
        brief_explicitly_forbids_people(updated)
        and not has_character_frame
        and not visible_speaker_turns
        and not has_verified_source_people
    ):
        if updated.get("contains_person") is True:
            applied.append("contains_person_cleared_by_explicit_constraint")
        updated["contains_person"] = False
    elif updated.get("contains_person") is not True and (has_character_frame or bool(visible_speaker_turns)):
        updated["contains_person"] = True
        applied.append("contains_person")
    if (
        updated.get("contains_voice") is not True
        and updated.get("audio_enabled") is True
        and bool(script)
    ):
        updated["contains_voice"] = True
        applied.append("contains_voice")
    return updated, applied


async def _apply_production_brief_defaults(
    db: AsyncSession,
    run: ProjectRun,
    brief: dict[str, Any],
    source_roles: list[dict[str, Any]],
    *,
    mode: str,
) -> tuple[dict[str, Any], list[str]]:
    updated = dict(brief)
    applied: list[str] = []
    asset_ids = [item["asset_id"] for item in source_roles]
    assets = (
        await db.execute(select(ProjectRunAsset).where(ProjectRunAsset.id.in_(asset_ids)))
    ).scalars().all() if asset_ids else []
    hints = []
    for asset in assets:
        metadata = _dict(asset.metadata_json)
        hints.extend((asset.file_name, metadata.get("cloud_video_title"), metadata.get("cloud_video_category")))
    if not _text(updated.get("product"), 300):
        updated["product"] = _product_from_material_hints([
            *hints,
            _text(updated.get("script"), 6000),
            _text(updated.get("request") or updated.get("requirement"), 6000),
        ]) or (
            "示例品牌（以已选素材为准）"
            if source_roles
            else "示例品牌（根据需求确认具体 SKU）"
            if _text(updated.get("script") or updated.get("request") or updated.get("requirement"), 6000)
            else "无品牌抽象 B-roll"
        )
        applied.append("product")
    if not _text(updated.get("request") or updated.get("requirement"), 6000):
        updated["request"] = _default_request_for_mode(
            mode,
            has_assets=bool(source_roles),
            script=_text(updated.get("script"), 6000),
            contains_person=bool(updated.get("contains_person")),
        )
        applied.append("request")
    updated["defaults_applied"] = applied
    updated["default_source"] = "selected_materials" if source_roles else "safe_text_to_video"
    return updated, applied


def _department_id(run: ProjectRun, project: Project | None = None) -> str:
    return _text(run.department_id or getattr(project, "department_id", None), 50) or _core().MATERIAL_WORKBENCH_DEPARTMENT_ID


def _user_id(user: User) -> str | None:
    return _text(getattr(user, "id", None), 50) or None


def _require_publisher(user: User) -> None:
    if _text(getattr(user, "role", None), 30).lower() not in PUBLISH_ROLES and not bool(getattr(user, "can_view_all", False)):
        raise AppError("MEDIA_WORKFLOW_PUBLISH_FORBIDDEN", 403)


def _parse_time(value: Any, *, field: str, required: bool = False) -> datetime | None:
    raw = _text(value, 50)
    if not raw:
        if required:
            raise AppError("MEDIA_SCHEDULE_INVALID", 422, {"field": field})
        return None
    try:
        return parse_bjt_datetime(raw)
    except (TypeError, ValueError) as exc:
        raise AppError("MEDIA_SCHEDULE_INVALID", 422, {"field": field}) from exc


def _asset_kind(asset: ProjectRunAsset) -> str:
    return _core()._reference_asset_kind(asset)


def continuation_segment_durations(source_duration: float, target_duration: int, preferred_duration: int) -> list[int]:
    minimum_target = max(8, math.ceil(source_duration) + 4)
    if target_duration < minimum_target or target_duration > 60:
        raise AppError("MEDIA_CONTINUATION_DURATION_INVALID", 422, {"minimum": minimum_target, "maximum": 60})
    generated_duration = max(4, round(target_duration - source_duration))
    preferred = min(max(int(preferred_duration), 4), 15)
    segment_count = max(math.ceil(generated_duration / 15), min(12, math.ceil(generated_duration / preferred)))
    while segment_count > 1 and generated_duration / segment_count < 4:
        segment_count -= 1
    if segment_count > 12:
        raise AppError("MEDIA_CONTINUATION_DURATION_INVALID", 422, {"maximum_segments": 12})
    base_duration, extra_seconds = divmod(generated_duration, segment_count)
    durations = [base_duration + (1 if index < extra_seconds else 0) for index in range(segment_count)]
    if any(duration < 4 or duration > 15 for duration in durations):
        raise AppError("MEDIA_CONTINUATION_DURATION_INVALID", 422, {"detail": "segments must each be 4-15 seconds"})
    return durations


def _continuation_retry_revision(locks: dict[str, Any] | None = None) -> int:
    try:
        return max(0, int(_dict(locks).get("retry_revision") or 0))
    except (TypeError, ValueError):
        return 0


def continuation_segment_idempotency_key(chain_id: str, segment_index: int, locks: dict[str, Any] | None = None) -> str:
    revision = _continuation_retry_revision(locks)
    suffix = f":r{revision}" if revision else ""
    return f"continuation:{chain_id}:{segment_index}{suffix}"


def _serialize_library_asset(row: MediaLibraryAsset, source: ProjectRunAsset | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "department_id": row.department_id,
        "source_asset_id": row.source_asset_id,
        "name": row.name,
        "media_type": row.media_type,
        "sha256": row.sha256,
        "product_key": row.product_key,
        "view_type": row.view_type,
        "package_count": row.package_count,
        "aliases": row.aliases_json or [],
        "thumbnail_asset_id": row.thumbnail_asset_id,
        "auto_reference_eligible": bool(row.auto_reference_eligible),
        "import_batch": row.import_batch,
        "status": row.status,
        "tags": row.tags_json or [],
        "rights": row.rights_json or {},
        "scan": row.scan_json or {},
        "metadata": row.metadata_json or {},
        "source": project_service.serialize_run_asset(source, include_download_url=True) if source else None,
        "created_by": row.created_by,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


def _serialize_group(row: MediaAssetGroup) -> dict[str, Any]:
    return {
        "id": row.id, "department_id": row.department_id, "name": row.name, "purpose": row.purpose,
        "status": row.status, "version": row.version, "items": row.items_json or [], "history": row.history_json or [],
        "created_by": row.created_by, "published_by": row.published_by,
        "created_at": isoformat_bjt(row.created_at), "updated_at": isoformat_bjt(row.updated_at),
    }


def _serialize_workflow(row: MediaWorkflowDefinition) -> dict[str, Any]:
    return {
        "id": row.id, "department_id": row.department_id, "name": row.name, "purpose": row.purpose,
        "platform": row.platform, "status": row.status, "version": row.version,
        "workflow_kind": row.workflow_kind, "h3_mode": row.h3_mode,
        "bridge_template_id": row.bridge_template_id, "required_roles": row.required_roles_json or [],
        "config": row.config_json or {}, "history": row.history_json or [], "created_by": row.created_by, "published_by": row.published_by,
        "created_at": isoformat_bjt(row.created_at), "updated_at": isoformat_bjt(row.updated_at),
    }


async def _batch_counts(db: AsyncSession, batch_id: str) -> dict[str, int]:
    rows = (
        await db.execute(
            select(MediaGenerationJob.status, func.count(MediaGenerationJob.id))
            .where(MediaGenerationJob.production_batch_id == batch_id)
            .group_by(MediaGenerationJob.status)
        )
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    counts["total"] = sum(counts.values())
    return counts


async def _serialize_batch(db: AsyncSession, row: MediaProductionBatch) -> dict[str, Any]:
    counts = await _batch_counts(db, row.id)
    row.counters_json = counts
    previous_status = row.status
    current_time = now_bjt()
    if row.status not in {"paused", "cancelled", "deadline_reached"}:
        active = sum(counts.get(key, 0) for key in ("assigned", "running", "collecting", "syncing"))
        waiting = counts.get("queued", 0)
        successes = sum(counts.get(key, 0) for key in ("awaiting_review", "approved", "syncing", "synced"))
        unsuccessful = counts.get("failed", 0) + counts.get("cancelled", 0)
        total = counts.get("total", 0)
        if row.not_before_at and row.not_before_at > current_time and waiting:
            row.status = "scheduled"
        elif active:
            row.status = "running"
        elif waiting:
            row.status = "queued"
        elif total and successes + unsuccessful >= total:
            row.status = "partial_failed" if unsuccessful else "completed"
    if row.status != previous_status:
        row.updated_at = current_time
    return {
        "id": row.id, "project_id": row.project_id, "project_run_id": row.project_run_id,
        "department_id": row.department_id, "title": row.title, "theme": row.theme, "status": row.status,
        "not_before_at": isoformat_bjt(row.not_before_at), "deadline_at": isoformat_bjt(row.deadline_at),
        "allowed_nodes": row.allowed_nodes_json or [], "resource_policy": row.resource_policy_json or {},
        "plan": row.plan_json or {}, "counters": counts, "requested_by": row.requested_by,
        "idempotency_key": row.idempotency_key,
        "created_at": isoformat_bjt(row.created_at), "updated_at": isoformat_bjt(row.updated_at),
    }


def _serialize_annotation(row: MediaReviewAnnotation) -> dict[str, Any]:
    return {
        "id": row.id, "media_job_id": row.media_job_id, "start_seconds": row.start_seconds,
        "end_seconds": row.end_seconds, "category": row.category, "severity": row.severity,
        "status": row.status, "note": row.note, "evidence": row.evidence_json or [],
        "created_by": row.created_by, "resolved_by": row.resolved_by,
        "created_at": isoformat_bjt(row.created_at), "updated_at": isoformat_bjt(row.updated_at),
    }


async def list_library_assets(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    department_id = _department_id(run)
    conditions = [MediaLibraryAsset.department_id == department_id]
    if not bool(payload.get("include_archived")):
        conditions.append(MediaLibraryAsset.status == "active")
    kind = _text(payload.get("media_type"), 30)
    if kind:
        conditions.append(MediaLibraryAsset.media_type == kind)
    rows = (
        await db.execute(select(MediaLibraryAsset).where(*conditions).order_by(MediaLibraryAsset.updated_at.desc()).limit(500))
    ).scalars().all()
    sources = {asset.id: asset for asset in (
        await db.execute(select(ProjectRunAsset).where(ProjectRunAsset.id.in_([row.source_asset_id for row in rows])))
    ).scalars().all()} if rows else {}
    return {"items": [_serialize_library_asset(row, sources.get(row.source_asset_id)) for row in rows], "total": len(rows)}


async def upsert_library_asset(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    department_id = _department_id(run)
    row_id = _text(payload.get("id"), 50)
    row = await db.get(MediaLibraryAsset, row_id) if row_id else None
    if row and row.department_id != department_id:
        raise AppError("MEDIA_LIBRARY_ASSET_NOT_FOUND", 404)
    source_id = _text(payload.get("source_asset_id") or getattr(row, "source_asset_id", None), 50)
    source = await db.get(ProjectRunAsset, source_id)
    if not source or source.project_id != run.project_id or (source.department_id and source.department_id != department_id):
        raise AppError("PROJECT_ASSET_NOT_FOUND", 404, {"asset_id": source_id})
    kind = _asset_kind(source)
    if kind not in {"image", "video", "audio"}:
        raise AppError("MEDIA_REFERENCE_TYPE_INVALID", 422, {"mime_type": source.mime_type})
    existing = (
        await db.execute(select(MediaLibraryAsset).where(
            MediaLibraryAsset.department_id == department_id, MediaLibraryAsset.source_asset_id == source.id
        ).limit(1))
    ).scalar_one_or_none()
    row = row or existing
    now = now_bjt()
    if row is None:
        row = MediaLibraryAsset(
            id=_new_id("mla"), department_id=department_id, source_asset_id=source.id,
            name=_text(payload.get("name"), 240) or source.file_name, media_type=kind, sha256=source.sha256,
            created_by=_user_id(user), created_at=now, updated_at=now,
        )
        db.add(row)
    row.name = _text(payload.get("name"), 240) or row.name
    if "product_key" in payload:
        row.product_key = _text(payload.get("product_key"), 120) or None
    if "view_type" in payload:
        view_type = _text(payload.get("view_type"), 40) or None
        if view_type not in {None, "front", "side", "back", "unit", "open_pack", "detail", "logo", "composite"}:
            raise AppError("MEDIA_LIBRARY_ASSET_INVALID", 422, {"field": "view_type"})
        row.view_type = view_type
    if "package_count" in payload:
        count = payload.get("package_count")
        row.package_count = max(1, min(int(count), 999)) if count not in {None, ""} else None
    if "aliases" in payload:
        row.aliases_json = list(dict.fromkeys(
            _text(item, 120) for item in _list(payload.get("aliases")) if _text(item, 120)
        ))[:30]
    if "auto_reference_eligible" in payload:
        row.auto_reference_eligible = bool(payload.get("auto_reference_eligible"))
    if "import_batch" in payload:
        row.import_batch = _text(payload.get("import_batch"), 120) or None
    row.tags_json = [_text(item, 80) for item in _list(payload.get("tags")) if _text(item, 80)][:30]
    row.rights_json = {**_dict(row.rights_json), **_dict(payload.get("rights"))}
    row.scan_json = {**_dict(row.scan_json), **_dict(payload.get("scan"))}
    row.metadata_json = _normalized_library_metadata(row.metadata_json, payload.get("metadata"), media_type=kind)
    row.status = "active"
    row.updated_at = now
    await db.flush()
    return {"asset": _serialize_library_asset(row, source), "deduped": bool(existing and not row_id)}


async def archive_library_asset(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    row = await db.get(MediaLibraryAsset, _text(payload.get("id"), 50))
    if not row or row.department_id != _department_id(run):
        raise AppError("MEDIA_LIBRARY_ASSET_NOT_FOUND", 404)
    if row.created_by != _user_id(user):
        _require_publisher(user)
    row.status = "archived"
    row.updated_at = now_bjt()
    return {"asset": _serialize_library_asset(row)}


async def _normalize_group_items(db: AsyncSession, department_id: str, items: list[Any]) -> list[dict[str, Any]]:
    if not items or len(items) > 12:
        raise AppError("MEDIA_ASSET_GROUP_INVALID", 422, {"field": "items", "maximum": 12})
    normalized: list[dict[str, Any]] = []
    counts = {"image": 0, "video": 0, "audio": 0}
    seen: set[str] = set()
    for item in items:
        data = _dict(item)
        asset_id = _text(data.get("library_asset_id") or data.get("asset_id"), 50)
        role = _text(data.get("role"), 40)
        if asset_id in seen or role not in BUSINESS_ASSET_ROLES:
            raise AppError("MEDIA_ASSET_GROUP_INVALID", 422, {"asset_id": asset_id, "role": role})
        row = await db.get(MediaLibraryAsset, asset_id)
        expected_kind, technical_role = BUSINESS_ASSET_ROLES[role]
        if not row or row.department_id != department_id or row.status != "active" or row.media_type != expected_kind:
            raise AppError("MEDIA_ASSET_GROUP_INVALID", 422, {"asset_id": asset_id, "role": role})
        seen.add(asset_id)
        counts[expected_kind] += 1
        normalized.append({
            "library_asset_id": row.id, "source_asset_id": row.source_asset_id, "role": role,
            "technical_role": technical_role, "purpose": _text(data.get("purpose"), 500), "sha256": row.sha256,
        })
    if counts["image"] > 9 or counts["video"] > 3 or counts["audio"] > 3:
        raise AppError("MEDIA_REFERENCE_LIMIT", 422, {"counts": counts})
    if any(item["role"] == "product_packshot" for item in normalized) and len(normalized) > 1:
        raise AppError("MEDIA_ASSET_GROUP_INVALID", 422, {"detail": "product_packshot 首帧图不能与参考复刻素材混用"})
    return normalized


async def manage_asset_group(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any], action: str) -> dict[str, Any]:
    department_id = _department_id(run)
    if action == "list":
        rows = (await db.execute(select(MediaAssetGroup).where(MediaAssetGroup.department_id == department_id).order_by(MediaAssetGroup.updated_at.desc()).limit(200))).scalars().all()
        return {"items": [_serialize_group(row) for row in rows], "total": len(rows)}
    row = await db.get(MediaAssetGroup, _text(payload.get("id"), 50)) if payload.get("id") else None
    if row and row.department_id != department_id:
        raise AppError("MEDIA_ASSET_GROUP_NOT_FOUND", 404)
    if action == "get":
        if not row:
            raise AppError("MEDIA_ASSET_GROUP_NOT_FOUND", 404)
        return {"group": _serialize_group(row)}
    if action == "archive":
        if not row:
            raise AppError("MEDIA_ASSET_GROUP_NOT_FOUND", 404)
        if row.created_by != _user_id(user):
            _require_publisher(user)
        row.history_json = [*_list(row.history_json), {
            "version": row.version, "status": row.status, "items": row.items_json or [],
            "action": "archive", "changed_by": _user_id(user), "changed_at": isoformat_bjt(now_bjt()),
        }][-50:]
        row.status = "archived"
        row.updated_at = now_bjt()
        return {"group": _serialize_group(row)}
    items = await _normalize_group_items(db, department_id, _list(payload.get("items")))
    now = now_bjt()
    if row is None:
        row = MediaAssetGroup(id=_new_id("mag"), department_id=department_id, name="", created_by=_user_id(user), created_at=now, updated_at=now)
        db.add(row)
    else:
        row.history_json = [*_list(row.history_json), {
            "version": row.version, "status": row.status, "name": row.name,
            "purpose": row.purpose, "items": row.items_json or [],
            "action": "new_version", "changed_by": _user_id(user), "changed_at": isoformat_bjt(now),
        }][-50:]
        row.version = int(row.version or 1) + 1
    row.name = _text(payload.get("name"), 180) or row.name or "未命名素材组"
    row.purpose = _text(payload.get("purpose"), 500) or None
    row.items_json = items
    row.status = "draft"
    row.updated_at = now
    await db.flush()
    return {"group": _serialize_group(row)}


def _workflow_mode(kind: str, payload: dict[str, Any]) -> tuple[str, str]:
    if kind not in WORKFLOW_KINDS:
        raise AppError("MEDIA_WORKFLOW_INVALID", 422, {"field": "workflow_kind"})
    mode, template = WORKFLOW_KINDS[kind]
    if kind == "specified_asset":
        mode = _text(payload.get("h3_mode"), 30)
        if mode not in {"image_to_video", "reference_replay"}:
            raise AppError("MEDIA_WORKFLOW_INVALID", 422, {"field": "h3_mode"})
        template = "h3_i2v_v1" if mode == "image_to_video" else "h3_r2v_v1"
    return mode or "text_to_video", template or "h3_t2v_v1"


async def manage_workflow(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any], action: str) -> dict[str, Any]:
    department_id = _department_id(run)
    if action == "list":
        rows = (await db.execute(select(MediaWorkflowDefinition).where(MediaWorkflowDefinition.department_id == department_id).order_by(MediaWorkflowDefinition.updated_at.desc()).limit(200))).scalars().all()
        return {"items": [_serialize_workflow(row) for row in rows], "total": len(rows)}
    row = await db.get(MediaWorkflowDefinition, _text(payload.get("id"), 50)) if payload.get("id") else None
    if row and row.department_id != department_id:
        raise AppError("MEDIA_WORKFLOW_NOT_FOUND", 404)
    if action == "get":
        if not row:
            raise AppError("MEDIA_WORKFLOW_NOT_FOUND", 404)
        return {"workflow": _serialize_workflow(row)}
    if action in {"publish", "archive"}:
        _require_publisher(user)
        if not row:
            raise AppError("MEDIA_WORKFLOW_NOT_FOUND", 404)
        row.history_json = [*_list(row.history_json), {
            "version": row.version, "status": row.status, "workflow_kind": row.workflow_kind,
            "h3_mode": row.h3_mode, "bridge_template_id": row.bridge_template_id,
            "required_roles": row.required_roles_json or [], "config": row.config_json or {},
            "action": action, "changed_by": _user_id(user), "changed_at": isoformat_bjt(now_bjt()),
        }][-50:]
        row.status = "published" if action == "publish" else "archived"
        row.published_by = _user_id(user) if action == "publish" else row.published_by
        row.updated_at = now_bjt()
        return {"workflow": _serialize_workflow(row)}
    kind = _text(payload.get("workflow_kind"), 40) or "text_to_video"
    mode, template = _workflow_mode(kind, payload)
    required_roles = [_text(item, 40) for item in _list(payload.get("required_roles"))]
    if any(role not in BUSINESS_ASSET_ROLES for role in required_roles):
        raise AppError("MEDIA_WORKFLOW_INVALID", 422, {"field": "required_roles"})
    now = now_bjt()
    if row is None:
        row = MediaWorkflowDefinition(id=_new_id("mwf"), department_id=department_id, name="", workflow_kind=kind, h3_mode=mode, bridge_template_id=template, created_by=_user_id(user), created_at=now, updated_at=now)
        db.add(row)
    else:
        row.history_json = [*_list(row.history_json), {
            "version": row.version, "status": row.status, "name": row.name, "purpose": row.purpose,
            "platform": row.platform, "workflow_kind": row.workflow_kind, "h3_mode": row.h3_mode,
            "bridge_template_id": row.bridge_template_id, "required_roles": row.required_roles_json or [],
            "config": row.config_json or {}, "action": "new_version", "changed_by": _user_id(user),
            "changed_at": isoformat_bjt(now),
        }][-50:]
        row.version = int(row.version or 1) + 1
    row.name = _text(payload.get("name"), 180) or row.name or "未命名工作流"
    row.purpose = _text(payload.get("purpose"), 500) or None
    row.platform = _text(payload.get("platform"), 80) or "douyin"
    row.status = "draft"
    row.workflow_kind = kind
    row.h3_mode = mode
    row.bridge_template_id = template
    row.required_roles_json = required_roles
    row.config_json = _dict(payload.get("config"))
    row.updated_at = now
    await db.flush()
    return {"workflow": _serialize_workflow(row)}


def _preset_id_from_job(job: MediaGenerationJob) -> str | None:
    if job.output_preset_id in OUTPUT_PRESET_DEFINITIONS:
        return job.output_preset_id
    params = _dict(job.params_json)
    for preset_id, definition in OUTPUT_PRESET_DEFINITIONS.items():
        expected = _dict(definition.get("params"))
        if all(params.get(key) == expected.get(key) for key in ("width", "height", "frames", "fps", "steps")):
            return preset_id
    return None


async def output_presets(db: AsyncSession, project: Project) -> dict[str, Any]:
    nodes = (
        await db.execute(
            select(OpenClawInstance).where(
                OpenClawInstance.is_active.is_(True),
                OpenClawInstance.agent_purpose.in_(["media", "mixed"]),
            )
        )
    ).scalars().all()
    node_families = {node.id: _node_family(node) for node in nodes}
    rows = (
        await db.execute(
            select(MediaGenerationAttempt, MediaGenerationJob)
            .join(MediaGenerationJob, MediaGenerationJob.id == MediaGenerationAttempt.job_id)
            .where(
                MediaGenerationJob.project_id == project.id,
                MediaGenerationAttempt.status.in_(["completed", "failed"]),
            )
            .order_by(MediaGenerationAttempt.completed_at.desc().nullslast())
            .limit(1000)
        )
    ).all()
    samples: dict[str, dict[str, list[dict[str, Any]]]] = {
        preset_id: {family: [] for family in REQUIRED_BENCHMARK_NODE_FAMILIES}
        for preset_id in OUTPUT_PRESET_DEFINITIONS
    }
    for attempt, job in rows:
        family = node_families.get(attempt.instance_id)
        preset_id = _preset_id_from_job(job)
        if family not in REQUIRED_BENCHMARK_NODE_FAMILIES or preset_id not in samples:
            continue
        metrics = _dict(attempt.metrics_json)
        try:
            duration = float(metrics.get("total_seconds") or float(metrics.get("duration_ms") or 0) / 1000)
        except (TypeError, ValueError):
            duration = 0.0
        samples[preset_id][family].append({"status": attempt.status, "duration_seconds": duration})
    items = []
    quick_samples = samples["quick_preview"]
    for preset_id, definition in OUTPUT_PRESET_DEFINITIONS.items():
        gate = preset_benchmark_gate(preset_id, samples[preset_id], quick_samples)
        items.append({
            "id": preset_id,
            "label": definition["label"],
            "description": definition["description"],
            "params": definition["params"],
            "benchmark_required": bool(definition["benchmark_required"]),
            "enabled": bool(gate["enabled"]),
            "gate": gate,
        })
    return {"recommended_id": "quick_preview", "items": items}


async def _resolve_output_preset(
    db: AsyncSession,
    project: Project,
    requested: Any,
    *,
    allow_locked_benchmark: bool = False,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    preset_id = _text(requested, 50) or "quick_preview"
    if preset_id == "recommended":
        preset_id = "quick_preview"
    if preset_id not in OUTPUT_PRESET_DEFINITIONS:
        raise AppError("MEDIA_OUTPUT_PRESET_INVALID", 422, {"preset_id": preset_id})
    registry = await output_presets(db, project)
    selected = next(item for item in registry["items"] if item["id"] == preset_id)
    if not selected["enabled"] and not allow_locked_benchmark:
        raise AppError(
            "MEDIA_OUTPUT_PRESET_NOT_READY",
            409,
            {"preset_id": preset_id, "reason": selected["gate"]["reason"], "benchmark": selected["gate"]},
        )
    return preset_id, dict(selected["params"]), selected


async def theme_diverge(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    count = int(payload.get("direction_count") or 6)
    if count < 3 or count > 12:
        raise AppError("MEDIA_THEME_DIVERGE_INVALID", 422, {"field": "direction_count", "minimum": 3, "maximum": 12})
    brief = _dict(payload.get("brief")) or payload
    system = (
        "你是抖音内容电商投流素材总监。只输出 JSON 对象，顶层是 directions 数组。"
        "每项必须有 title、angle、hook、audience、workflow_kind、variant_count、asset_roles、brief。"
        f"严格输出 {count} 个互不重复方向，不编造价格、活动或功效。"
    )
    prompt = "围绕以下需求发散投流方向：" + json.dumps(brief, ensure_ascii=False)
    last_output: Any = None
    for attempt in range(2):
        response = await _core().codex_service._builtin_platform_ai_analyze(
            db, user,
            {
                "prompt": prompt if attempt == 0 else prompt + "\n上次 JSON 不合法，请只返回字段完整的 JSON。",
                "system": system, "context_pack": {"project_run_id": run.id, "feature": "theme_diverge"},
                "json_mode": True, "temperature": 0.65, "max_output_tokens": 4096,
                "model_profile": _core().MEDIA_BASELINE_PROFILE,
            },
            effective_skill_id=None, effective_run_id=run.execution_run_id or run.id,
        )
        last_output = response.get("output")
        value = last_output
        if isinstance(value, str):
            try:
                value = json.loads(value.strip().removeprefix("```json").removesuffix("```").strip())
            except json.JSONDecodeError:
                value = None
        directions = _list(_dict(value).get("directions"))
        if len(directions) == count and all(_text(_dict(item).get("title"), 120) for item in directions):
            normalized = []
            for index, item in enumerate(directions, 1):
                data = _dict(item)
                normalized.append({
                    "id": f"direction-{index}", "title": _text(data.get("title"), 120),
                    "angle": _text(data.get("angle"), 500), "hook": _text(data.get("hook"), 500),
                    "audience": _text(data.get("audience"), 300),
                    "workflow_kind": _text(data.get("workflow_kind"), 40) or "text_to_video",
                    "variant_count": min(max(int(data.get("variant_count") or 1), 1), 20),
                    "asset_roles": [role for role in _list(data.get("asset_roles")) if role in BUSINESS_ASSET_ROLES],
                    "brief": {**brief, **_dict(data.get("brief")), "creative_angle": _text(data.get("angle"), 500)},
                })
            return {"model": response.get("model") or _core().MEDIA_BASELINE_PROFILE, "directions": normalized, "direction_count": count}
    raise AppError("MEDIA_THEME_DIVERGE_JSON_INVALID", 422, {"editable_output": _text(last_output, 12000)})


def _character_first_frame_quality_gate(
    understanding: dict[str, Any] | None,
    *,
    asset_id: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed before a generated still can steer an H3 video task."""

    fact = next(
        (
            _dict(item)
            for item in _list(_dict(understanding).get("assets"))
            if _text(_dict(item).get("asset_id"), 50) == asset_id
        ),
        {},
    )
    try:
        actor_count = max(1, int(contract.get("actor_count") or contract.get("speaker_count") or 1))
    except (TypeError, ValueError):
        actor_count = 1
    dialogue = _text(contract.get("content_format"), 40) == "dialogue" and actor_count >= 2
    issues: list[str] = []
    if fact.get("evidence_status") != "verified":
        issues.append("视觉模型没有返回可验证的首帧事实")
    if fact.get("semantic_role") != "person_scene":
        issues.append("首帧未被识别为人物/场景画面")
    if dialogue:
        if (
            int(fact.get("people_count") or 0) != 2
            or fact.get("people_presence") not in {"multiple_people", "full_person"}
        ):
            issues.append("双人对话首帧必须且只能清楚显示两位成年人")
        if fact.get("framing") not in {"waist_up_two_shot", "medium_two_shot"}:
            issues.append("双人首帧未保持腰部以上中景，可能继续造成动作被裁切")
        for field, message in (
            ("both_shoulder_lines_visible", "两人的完整肩线没有同时清楚可见"),
            ("both_elbows_visible", "两人的肘部没有同时进入画面"),
            ("waist_support_visible", "腰部或身体受力支撑没有清楚进入画面"),
            ("inward_partner_gaze", "两人的视线没有稳定朝向彼此"),
            ("faces_visibly_distinct", "两张脸的年龄、轮廓或发型区分不足"),
        ):
            if fact.get(field) is not True:
                issues.append(message)
    else:
        if fact.get("people_presence") != "full_person" or int(fact.get("people_count") or 0) != 1:
            issues.append("单人口播首帧必须且只能清楚显示一位成年人")
    if fact.get("contains_product") is True:
        issues.append("人物干净首帧不应包含模型生成的商品或包装")
    if fact.get("contains_source_text") is True:
        issues.append("人物干净首帧不应包含文字、字幕、Logo 或水印")
    return {
        "passed": not issues,
        "issues": issues,
        "fact": fact,
        "vision_model": _text(_dict(understanding).get("model"), 180),
        "policy_version": SOURCE_UNDERSTANDING_POLICY_VERSION,
    }


async def _existing_character_first_frame(
    db: AsyncSession,
    run: ProjectRun,
    idempotency_key: str,
) -> ProjectRunAsset | None:
    rows = (
        await db.execute(
            select(ProjectRunAsset)
            .where(ProjectRunAsset.project_run_id == run.id)
            .order_by(ProjectRunAsset.created_at.desc())
            .limit(100)
        )
    ).scalars().all()
    return next(
        (
            item
            for item in rows
            if _text(_dict(item.metadata_json).get("first_frame_idempotency_key"), 128) == idempotency_key
        ),
        None,
    )


async def generate_character_first_frame(
    db: AsyncSession,
    user: User,
    project: Project,
    run: ProjectRun,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Generate, persist and visually gate a clean character/scene anchor."""

    plan = _dict(payload.get("prepared_plan") or payload.get("final_plan"))
    brief = _dict(payload.get("brief") or payload.get("normalized_brief"))
    if not plan:
        raise AppError("MEDIA_PLAN_REQUIRED", 422, {"field": "prepared_plan"})
    contract = _dict(plan.get("ad_material_contract") or brief.get("ad_material_contract"))
    if contract.get("people_requested") is not True or _text(contract.get("content_format"), 40) not in {
        "dialogue", "talking_head",
    }:
        raise AppError(
            "MEDIA_FIRST_FRAME_NOT_APPLICABLE",
            422,
            {"detail": "人物首帧只用于双人对话或单人口播任务。"},
        )
    idempotency_key = _text(payload.get("idempotency_key"), 128)
    if len(idempotency_key) < 8:
        raise AppError("MEDIA_IDEMPOTENCY_KEY_REQUIRED", 422)
    existing = await _existing_character_first_frame(db, run, idempotency_key)
    if existing is not None:
        metadata = _dict(existing.metadata_json)
        gate = _dict(metadata.get("quality_gate"))
        source_role = {
            "asset_id": existing.id,
            "role": "character_first_frame",
            "technical_role": "first_frame",
            "purpose": "受控生成并通过视觉门禁的人物/场景首帧",
            "role_locked": True,
        }
        if "passed" not in gate or gate.get("policy_version") != SOURCE_UNDERSTANDING_POLICY_VERSION:
            understanding = await _understand_source_assets(db, user, run, [source_role])
            gate = _character_first_frame_quality_gate(
                understanding,
                asset_id=existing.id,
                contract=contract,
            )
            existing.metadata_json = {
                **metadata,
                "quality_gate": gate,
                "source_understanding": understanding or {},
            }
            await db.flush()
        return {
            "status": "completed" if gate.get("passed") is True else "needs_review",
            "deduped": True,
            "asset": project_service.serialize_run_asset(existing, include_download_url=True),
            "source_role": source_role,
            "quality_gate": gate,
            "prompt_sha256": _text(metadata.get("generation_prompt_sha256"), 64),
        }

    prompt = build_character_first_frame_prompt(plan)
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    try:
        from app.hall import direct_capability_service

        response = await direct_capability_service.run_direct_capability(
            "gpt-imagegen",
            {
                "params": {
                    "prompt": prompt,
                    "size": "9:16",
                    "resolution": "2K",
                    "n": 1,
                    "wait": True,
                    "download": False,
                    "workspace_id": f"material-first-frame-{run.id}",
                    "workspace_name": "素材工作台 · 人物首帧",
                    "timeout": 300,
                }
            },
            db=db,
            user_id=_user_id(user),
            current_user=user,
        )
        image_urls = direct_capability_service.direct_capability_result_image_urls(response)
        if not image_urls:
            raise AppError(
                "MEDIA_FIRST_FRAME_GENERATION_PENDING",
                503,
                {"detail": "人物首帧生成尚未返回图片；本次没有创建 H3 视频任务，请稍后重试。"},
            )
        image_response = await direct_capability_service.download_direct_capability_image(
            "gpt-imagegen",
            image_urls[0],
            filename=f"character-first-frame-{prompt_sha256[:10]}.png",
            current_user=user,
        )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - convert provider/HTTP details at the project boundary
        detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
        raise AppError(
            "MEDIA_FIRST_FRAME_GENERATION_FAILED",
            502,
            {"detail": f"人物首帧生成失败：{_text(detail, 500)}；本次没有创建 H3 视频任务。"},
        ) from exc

    content = bytes(getattr(image_response, "body", b"") or b"")
    if not content:
        raise AppError("MEDIA_FIRST_FRAME_GENERATION_FAILED", 502, {"detail": "人物首帧返回了空图片。"})
    root = _dict(response.get("result"))
    image_mime = _text(getattr(image_response, "media_type", None), 120) or "image/png"
    image_suffix = {"image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(
        image_mime.casefold(), ".png"
    )
    uploaded = await project_service.upload_project_run_asset(
        db,
        user,
        run.id,
        file_name=f"人物场景首帧-{prompt_sha256[:10]}{image_suffix}",
        mime_type=image_mime,
        content=content,
        metadata={
            "source": "material_workbench_character_first_frame",
            "default_role": "character_first_frame",
            "business_role": "character_first_frame",
            "technical_role": "first_frame",
            "first_frame_idempotency_key": idempotency_key,
            "generation_capability": "gpt-imagegen",
            "generation_model": _text(root.get("model"), 180) or "gpt-image-2",
            "generation_prompt": prompt,
            "generation_prompt_sha256": prompt_sha256,
            "ad_material_policy_version": AD_MATERIAL_POLICY_VERSION,
            "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
            "rights": {"ownership": "department_generated", "training_candidate": False},
        },
    )
    asset_id = _text(_dict(uploaded.get("asset")).get("id"), 50)
    source_role = {
        "asset_id": asset_id,
        "role": "character_first_frame",
        "technical_role": "first_frame",
        "purpose": "受控生成并通过视觉门禁的人物/场景首帧",
        "role_locked": True,
    }
    understanding = await _understand_source_assets(db, user, run, [source_role])
    gate = _character_first_frame_quality_gate(
        understanding,
        asset_id=asset_id,
        contract=contract,
    )
    asset_row = await db.get(ProjectRunAsset, asset_id)
    if asset_row is not None:
        asset_row.metadata_json = {
            **_dict(asset_row.metadata_json),
            "quality_gate": gate,
            "source_understanding": understanding or {},
        }
        await db.flush()
        serialized_asset = project_service.serialize_run_asset(asset_row, include_download_url=True)
    else:
        serialized_asset = _dict(uploaded.get("asset"))
    return {
        "status": "completed" if gate["passed"] else "needs_review",
        "deduped": False,
        "asset": serialized_asset,
        "source_role": source_role,
        "quality_gate": gate,
        "source_understanding": understanding,
        "prompt": prompt,
        "prompt_sha256": prompt_sha256,
        "generation_model": _text(root.get("model"), 180) or "gpt-image-2",
        "next_h3_mode": "image_to_video" if gate["passed"] else None,
    }


async def prepare_production(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    brief = _dict(payload.get("brief")) or {
        "product": _text(payload.get("product"), 300),
        "script": _text(payload.get("script"), 6000),
        "request": _text(payload.get("request") or payload.get("requirement"), 6000),
        "platform": _text(payload.get("platform"), 80) or "douyin",
    }
    # Keep the operator's exact business inputs as immutable lineage.  The
    # normalized fields below are allowed to add speaker labels, fitted timing
    # and safe defaults, but they must never erase what the operator actually
    # typed before optional AI optimization.
    operator_input = {
        "product": _text(brief.get("product"), 300),
        "script": _text(brief.get("script"), 6000),
        "request": _text(
            brief.get("request") or brief.get("requirements") or brief.get("generation_requirements"),
            6000,
        ),
        "platform": _text(brief.get("platform"), 80) or "douyin",
        "duration_seconds": brief.get("duration_seconds") or payload.get("duration_seconds") or 5,
        "ratio": _text(brief.get("ratio"), 20) or "9:16",
    }
    script_normalization = normalize_frontdesk_script(brief.get("script"))
    if _text(brief.get("script"), 6000):
        brief = {
            **brief,
            "script": _text(script_normalization.get("normalized_script"), 6000),
            "script_normalization": {
                "changed": bool(script_normalization.get("changed")),
                "turn_count": int(script_normalization.get("turn_count") or 0),
                "speaker_count": int(script_normalization.get("speaker_count") or 0),
                "speakers": _list(script_normalization.get("speakers")),
                "policy_version": AD_MATERIAL_POLICY_VERSION,
            },
        }
    duration_contract = production_duration_contract(
        brief.get("duration_seconds") or payload.get("duration_seconds") or 5
    )
    requested_duration_seconds = int(duration_contract["requested_seconds"])
    recommended_duration_seconds = _recommended_dialogue_duration(
        brief.get("script"), requested_duration_seconds
    )
    # Duration is an operator-owned production choice.  A longer dialogue can
    # produce a recommendation and a fitted shot excerpt, but must never
    # silently change the selected duration at prepare time.
    duration_auto_adjusted = False
    generation_duration_seconds = int(duration_contract["generation_seconds"])
    script_timing = _script_timing_for_clip(brief.get("script"), requested_duration_seconds)
    brief = {
        **brief,
        "duration_seconds": generation_duration_seconds,
        "requested_duration_seconds": requested_duration_seconds,
        "delivery_duration_seconds": duration_contract["delivery_seconds"],
        "target_duration_seconds": duration_contract["target_seconds"],
        "script_timing": script_timing,
        "duration_auto_adjusted": duration_auto_adjusted,
        "duration_recommendation_seconds": recommended_duration_seconds,
        "shot_audio_instruction": (
            "只使用 script_timing.shot_script 作为本镜头口播；不得为了容纳完整台词新增切镜、"
            "商品特写、人物换位或额外展示动作。"
            if script_timing.get("fits") is False
            else "完整台词适合当前镜头时长。"
        ),
    }
    creative_option = _text(payload.get("creative_option"), 50) or "smart"
    if creative_option not in CREATIVE_OPTIONS:
        raise AppError("MEDIA_CREATIVE_OPTION_INVALID", 422, {"creative_option": creative_option})
    direct_prompt_mode = creative_option == "direct_prompt"
    requested_planning_mode = _text(payload.get("planning_mode"), 40).casefold()
    if requested_planning_mode in {"", "operator", "operator_brief", "manual"}:
        planning_mode = "operator_brief"
    elif requested_planning_mode in {"ai", "ai_planned", "ai_optimize"}:
        planning_mode = "ai_planned"
    elif requested_planning_mode == "direct_h3_prompt":
        planning_mode = "direct_h3_prompt"
    else:
        raise AppError(
            "MEDIA_PLANNING_MODE_INVALID",
            422,
            {"planning_mode": requested_planning_mode},
        )
    if direct_prompt_mode:
        planning_mode = "direct_h3_prompt"
    elif planning_mode == "direct_h3_prompt":
        raise AppError(
            "MEDIA_PLANNING_MODE_INVALID",
            422,
            {"detail": "直接 H3 提示词必须选择 direct_prompt 创作模板。"},
        )
    operator_brief_mode = planning_mode == "operator_brief"
    direct_h3_prompt = str(payload.get("direct_h3_prompt") or "").strip()
    if direct_prompt_mode:
        if not direct_h3_prompt:
            raise AppError(
                "MEDIA_DIRECT_H3_PROMPT_REQUIRED",
                422,
                {"field": "direct_h3_prompt", "detail": "直接 H3 提示词不能为空"},
            )
        if len(direct_h3_prompt) > H3_PROMPT_MAX_CHARS:
            raise AppError(
                "MEDIA_DIRECT_H3_PROMPT_INVALID",
                422,
                {
                    "field": "direct_h3_prompt",
                    "detail": f"提示词不能超过 {H3_PROMPT_MAX_CHARS} 个字符",
                },
            )
        if _dict(payload.get("theme_divergence")).get("enabled") is True:
            raise AppError(
                "MEDIA_DIRECT_H3_PROMPT_THEME_UNSUPPORTED",
                422,
                {"detail": "直接 H3 提示词不会调用 AI，不能同时使用主题发散"},
            )
        direct_prompt_people_terms = re.compile(
            r"(?:人物|女人|女性|女生|男人|男性|男生|男女|情侣|夫妻|医生|主播|演员|"
            r"\b(?:woman|women|man|men|person|people|couple|doctor|presenter|actor|actress)\b)",
            re.IGNORECASE,
        )
        if brief.get("contains_person") is not True and direct_prompt_people_terms.search(direct_h3_prompt):
            brief = {**brief, "contains_person": True}
    source_roles = _normalize_source_roles(_list(payload.get("source_roles")) or _list(payload.get("reference_assets")))
    reference_identity_policy = _text(
        brief.get("reference_identity_policy") or payload.get("reference_identity_policy"), 40
    ) or "replace_actor"
    if reference_identity_policy not in {"preserve_source", "replace_actor"}:
        raise AppError("MEDIA_REFERENCE_IDENTITY_POLICY_INVALID", 422, {"value": reference_identity_policy})
    suppress_reference_text = brief.get("strip_reference_text") is True or payload.get("strip_reference_text") is True
    if reference_identity_policy == "replace_actor" and not suppress_reference_text:
        # Keeping source overlays while replacing the person is not a stable H3
        # contract.  Commercial copy belongs to deterministic post-production.
        suppress_reference_text = True
    brief = {
        **brief,
        "strip_reference_text": suppress_reference_text,
        "reference_identity_policy": reference_identity_policy,
    }
    continuation = (
        creative_option == "continuation"
        or bool(payload.get("continuation"))
        or bool(duration_contract["continuation"])
    )
    strict_reference_replay = _strict_reference_replay_requested(brief, source_roles)
    source_understanding = None
    source_role_changes: list[dict[str, str]] = []
    if source_roles and not direct_prompt_mode:
        try:
            source_understanding = await _understand_source_assets(db, user, run, source_roles)
        except Exception as exc:  # noqa: BLE001 - strict replay must fail closed on any vision outage
            if strict_reference_replay:
                raise AppError(
                    "MEDIA_SOURCE_UNDERSTANDING_FAILED",
                    422,
                    {
                        "detail": "严格复刻必须先完成 Qwen-VL 素材取证；视觉分析暂不可用，本次没有让 DeepSeek 猜测画面。",
                        "retryable": True,
                    },
                ) from exc
        if strict_reference_replay and not source_understanding:
            raise AppError(
                "MEDIA_SOURCE_UNDERSTANDING_FAILED",
                422,
                {"detail": "严格复刻没有取得可验证的素材视觉清单，本次没有创建生成任务。", "retryable": True},
            )
        source_roles, source_role_changes = _reconcile_source_roles_with_understanding(
            source_roles, source_understanding
        )
    provisional_mode = infer_production_mode(source_roles, continuation=continuation)
    ad_material_contract = parse_ad_material_brief(
        brief,
        source_roles,
        source_understanding,
        inferred_mode=provisional_mode,
    )
    source_roles = adapt_source_roles_for_ad_contract(source_roles, ad_material_contract)
    brief = {
        **brief,
        "ad_material_contract": ad_material_contract,
        "ad_material_policy_version": AD_MATERIAL_POLICY_VERSION,
        "requested_h3_mode": ad_material_contract.get("recommended_h3_mode"),
        "cast_market": ad_material_contract.get("cast_market"),
        "face_style": ad_material_contract.get("face_style"),
        "actor_count": ad_material_contract.get("actor_count"),
    }
    replication_contract = _source_replication_contract(brief, source_roles, source_understanding)
    brief = _apply_replication_facts_to_brief({
        **brief,
        "source_understanding": source_understanding or {},
        "source_understanding_policy_version": SOURCE_UNDERSTANDING_POLICY_VERSION,
        "source_replication_contract": replication_contract,
    }, replication_contract)
    source_roles, reference_preprocessing = await _materialize_h3_source_roles(
        db,
        user,
        run,
        source_roles,
        suppress_reference_text=suppress_reference_text,
        reference_identity_policy=reference_identity_policy,
        preserve_identity_anchor=replication_contract.get("source_people_presence") in {"full_person", "multiple_people"},
    )
    brief, source_roles = _bind_source_mentions(brief, source_roles)
    if continuation and not any(
        item["role"] in {"continuity_anchor", "motion_reference"} for item in source_roles
    ):
        raise AppError(
            "MEDIA_CONTINUATION_SOURCE_INVALID",
            422,
            {"detail": "30-second production requires a verified 2-15 second source video"},
        )
    inferred_mode = infer_production_mode(source_roles, continuation=continuation)
    has_reference_video = any(item.get("technical_role") == "reference_video" for item in source_roles)
    reference_source_audio_isolated = bool(suppress_reference_text and has_reference_video)
    if reference_source_audio_isolated:
        brief = {**brief, "reference_source_audio_isolated": True}
    brief, content_flags_applied = _infer_brief_content_flags(brief, source_roles)
    brief, defaults_applied = await _apply_production_brief_defaults(
        db, run, brief, source_roles, mode=inferred_mode
    )
    defaults_applied = [*content_flags_applied, *defaults_applied]
    if reference_source_audio_isolated:
        defaults_applied.append("reference_source_audio_stripped_for_no_text_replay")
    brief, conflict_resolutions = _resolve_audio_brief_conflicts(brief)
    defaults_applied.extend(conflict_resolutions)
    production_policy = production_policy_for(brief, source_roles, mode=inferred_mode)
    brief = {
        **brief,
        "production_intent": production_policy["production_intent"],
        "production_policy_version": PRODUCTION_POLICY_VERSION,
        # The creative model can use the business intent, but GPU selection
        # remains a deterministic server-side decision.
        "system_routing_policy": production_policy["routing_policy"],
    }
    reference_requests = [
        {
            "asset_id": item["asset_id"], "role": item["technical_role"],
            "business_role": item["role"], "purpose": item["purpose"],
            **({"mention_token": item["mention_token"]} if item.get("mention_token") else {}),
        }
        for item in source_roles
    ]
    await _core()._validated_reference_requests(
        db,
        run,
        reference_requests,
        mode="reference_replay" if continuation else inferred_mode,
    )
    if creative_option == "reference_replay" and inferred_mode != "reference_replay":
        raise AppError("MEDIA_REFERENCE_REQUIRED", 422, {"mode": "reference_replay"})
    if creative_option == "product_frame" and not any(item["role"] == "product_packshot" for item in source_roles):
        raise AppError("MEDIA_REFERENCE_REQUIRED", 422, {"role": "product_packshot"})
    brief = {
        **brief,
        "creative_option": creative_option,
        "creative_option_label": CREATIVE_OPTIONS[creative_option]["label"],
        "source_roles": source_roles,
        "source_mentions": _list(brief.get("source_mentions")),
        "planning_mode": planning_mode,
        "prompt_source": (
            "operator_direct"
            if direct_prompt_mode
            else "operator_input" if operator_brief_mode else "planning_model"
        ),
    }
    if direct_prompt_mode:
        baseline = {
            "creative_goal": "操作员直接控制 H3 画面生成；文案用于受控配音，生成要求用于业务与审片约束。",
            "h3_mode": inferred_mode,
            "duration_seconds": generation_duration_seconds,
            "ratio": _text(brief.get("ratio"), 20) or "9:16",
            "shots": [{
                "start_seconds": 0,
                "end_seconds": generation_duration_seconds,
                "framing": "operator defined in direct H3 prompt",
                "camera_command": "[Static shot]",
                "subject": "operator defined in direct H3 prompt",
                "action": "operator defined in direct H3 prompt",
                "scene": "operator defined in direct H3 prompt",
                "lighting": "operator defined in direct H3 prompt",
                "mood": "operator defined in direct H3 prompt",
                "audio": "",
            }],
            "direct_h3_prompt": direct_h3_prompt,
            "planning_mode": "direct_h3_prompt",
            "planning_model": None,
            "prompt_source": "operator_direct",
            "recommended_params": {
                "frames": production_frames_for_duration(generation_duration_seconds),
                "fps": 24,
                "steps": 20,
                "seed": -1,
                "audio_enabled": bool(brief.get("audio_enabled")),
            },
            "warnings": ["已完全跳过 DeepSeek 规划；最终画面提示词由操作员直接提供。"],
        }
        comparison = {
            "baseline": {
                "model": DIRECT_PROMPT_PLANNER_MODEL,
                "model_version": None,
                "output": baseline,
            },
            "candidate": None,
            "planning_mode": "direct_h3_prompt",
        }
    elif operator_brief_mode:
        baseline = build_operator_brief_plan(
            brief,
            ad_material_contract,
            h3_mode=inferred_mode,
        )
        comparison = {
            "baseline": {
                "model": OPERATOR_BRIEF_PLANNER_MODEL,
                "model_version": AD_MATERIAL_POLICY_VERSION,
                "output": baseline,
            },
            "candidate": None,
            "planning_mode": "operator_brief",
        }
    else:
        comparison_result = await _core()._plan_compare(db, user, project, run, {"brief": brief})
        comparison = _dict(comparison_result.get("plan_comparison"))
        baseline = _dict(_dict(comparison.get("baseline")).get("output"))
    if inferred_mode != "continuation":
        baseline = _apply_script_timing_to_plan(baseline, script_timing)
        baseline = _apply_source_understanding_to_plan(baseline, brief)
        baseline = apply_ad_material_contract_to_plan(baseline, ad_material_contract)
        baseline = compile_h3_plan(
            {
                **baseline,
                "h3_mode": inferred_mode,
                "reference_roles": [
                    {
                        "asset_id": item["asset_id"], "role": item["technical_role"],
                        "business_role": item["role"], "purpose": item["purpose"],
                    }
                    for item in source_roles
                ],
            },
            brief=brief,
        )
        if isinstance(comparison.get("baseline"), dict):
            comparison["baseline"] = {**_dict(comparison.get("baseline")), "output": baseline}
    theme_config = _dict(payload.get("theme_divergence"))
    directions: list[dict[str, Any]] = []
    theme_model = None
    if theme_config.get("enabled") is True and operator_brief_mode:
        raise AppError(
            "MEDIA_THEME_REQUIRES_AI_OPTIMIZATION",
            422,
            {"detail": "主题发散会调用 AI；请显式选择 AI 优化后再生成多个方向。"},
        )
    if theme_config.get("enabled") is True and not direct_prompt_mode:
        diverged = await theme_diverge(
            db,
            user,
            run,
            {"brief": brief, "direction_count": theme_config.get("direction_count") or 6},
        )
        directions = []
        for direction_value in _list(diverged.get("directions")):
            direction = _dict(direction_value)
            direction_brief = {
                **brief,
                **_dict(direction.get("brief")),
                "creative_angle": _text(direction.get("angle"), 500),
                "hook": _text(direction.get("hook"), 500),
                "theme_direction": _text(direction.get("title"), 120),
            }
            direction_model, direction_plan = await _core()._call_baseline_plan(db, user, run, direction_brief)
            direction_plan = _apply_script_timing_to_plan(direction_plan, script_timing)
            direction_plan = _apply_source_understanding_to_plan(direction_plan, direction_brief)
            direction_plan = apply_ad_material_contract_to_plan(direction_plan, ad_material_contract)
            direction_plan = compile_h3_plan(
                {
                    **direction_plan,
                    "h3_mode": inferred_mode,
                    "reference_roles": [
                        {
                            "asset_id": item["asset_id"], "role": item["technical_role"],
                            "business_role": item["role"], "purpose": item["purpose"],
                        }
                        for item in source_roles
                    ],
                },
                brief=direction_brief,
            )
            directions.append({
                **direction,
                "brief": direction_brief,
                "final_plan": direction_plan,
                "planning_model": direction_model,
            })
        theme_model = diverged.get("model")
    registry = await output_presets(db, project)
    brief = {**brief, "operator_input": operator_input}
    return {
        "plan_comparison": comparison,
        "prepared_plan": baseline,
        "normalized_brief": brief,
        "source_roles": source_roles,
        "source_mentions": _list(brief.get("source_mentions")),
        "source_role_changes": source_role_changes,
        "source_understanding": source_understanding,
        "source_understanding_pipeline": {
            "vision_model": _text(_dict(source_understanding).get("model"), 180),
            "vision_policy_version": SOURCE_UNDERSTANDING_POLICY_VERSION,
            "planner_model": (
                None
                if direct_prompt_mode or operator_brief_mode
                else _core().MEDIA_BASELINE_PROFILE
            ),
            "planner_role": (
                "已跳过规划模型；有台词时仅执行确定性无字静音底片编译"
                if direct_prompt_mode
                else "用户原始提示词直接进入确定性结构化与 H3 编译；AI 优化未启用"
                if operator_brief_mode
                else "只基于视觉证据规划，不承担看图或看视频"
            ),
            "cache_hit": bool(_dict(source_understanding).get("cache_hit")),
        },
        "planning_mode": planning_mode,
        "prompt_source": (
            "operator_direct"
            if direct_prompt_mode
            else "operator_input" if operator_brief_mode else "planning_model"
        ),
        "reference_preprocessing": reference_preprocessing,
        "inferred_mode": inferred_mode,
        "mode_label": {
            "text_to_video": "文生视频",
            "image_to_video": "首帧生成",
            "reference_replay": "参考复刻",
            "continuation": "自动续写",
        }[inferred_mode],
        "creative_option": creative_option,
        "theme_directions": directions,
        "theme_model": theme_model,
        "output_presets": registry,
        "recommended_preset_id": registry["recommended_id"],
        "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
        "defaults_applied": defaults_applied,
        "script_timing": script_timing,
        "script_normalization": _dict(brief.get("script_normalization")),
        "duration_auto_adjusted": duration_auto_adjusted,
        "duration_recommendation_seconds": recommended_duration_seconds,
        "production_policy": production_policy,
        "ad_material_contract": ad_material_contract,
        "ad_material_policy_version": AD_MATERIAL_POLICY_VERSION,
        "duration_contract": duration_contract,
    }


async def compile_production_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate edited storyboard fields and deterministically rebuild H3 text.

    This is deliberately a local control-plane operation: it neither invokes
    the planning model nor submits a GPU task.  The durable submit boundary
    still recompiles the returned plan, so browser edits cannot bypass policy.
    """

    plan = _dict(payload.get("final_plan")) or _dict(payload.get("prepared_plan"))
    if not plan:
        raise AppError("MEDIA_PLAN_REQUIRED", 422, {"field": "final_plan"})
    if plan.get("planning_mode") == "direct_h3_prompt" or "direct_h3_prompt" in plan:
        raise AppError(
            "MEDIA_SHOT_EDIT_DIRECT_PROMPT_UNSUPPORTED",
            422,
            {"detail": "直接 H3 提示词模式请修改原始提示词后重新校验。"},
        )
    edited_shots = _list(payload.get("shots"))
    if not edited_shots or any(not isinstance(item, dict) for item in edited_shots):
        raise AppError(
            "MEDIA_SHOTS_REQUIRED",
            422,
            {"field": "shots", "detail": "至少保留一个可编辑分镜。"},
        )
    if len(edited_shots) > 12:
        raise AppError("MEDIA_SHOTS_INVALID", 422, {"field": "shots", "max_items": 12})

    original_shots = _list(plan.get("shots"))
    candidate = {**plan, "shots": edited_shots}
    candidate["operator_edited_action_indices"] = [
        index
        for index, edited in enumerate(edited_shots, 1)
        if index <= len(original_shots)
        if _text(edited.get("action"), 900)
        != _text(_dict(original_shots[index - 1]).get("action"), 900)
    ]
    if len(edited_shots) > len(original_shots):
        candidate["operator_edited_action_indices"].extend(
            range(len(original_shots) + 1, len(edited_shots) + 1)
        )
    # Executable text is derived output. Never allow an older compiled prompt
    # to survive after its structured storyboard has changed.
    candidate.pop("integrated_multimodal_description", None)
    candidate.pop("video_prompt", None)
    candidate.pop("prompt_sha256", None)
    try:
        compiled = compile_h3_plan(candidate, brief=_dict(payload.get("brief")))
    except H3PromptPolicyError as exc:
        raise AppError(
            "MEDIA_PLAN_INVALID",
            422,
            {"field": exc.field, "detail": exc.message},
        ) from exc

    serialized_original = json.dumps(original_shots, ensure_ascii=False, sort_keys=True, default=str)
    serialized_edited = json.dumps(compiled.get("shots") or [], ensure_ascii=False, sort_keys=True, default=str)
    compiled_prompt = _text(
        compiled.get("integrated_multimodal_description") or compiled.get("video_prompt"),
        H3_PROMPT_MAX_CHARS,
    )
    compiled["user_shot_override"] = {
        "applied": True,
        "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
        "original_shots_sha256": hashlib.sha256(serialized_original.encode("utf-8")).hexdigest(),
        "edited_shots_sha256": hashlib.sha256(serialized_edited.encode("utf-8")).hexdigest(),
        "compiled_prompt_sha256": hashlib.sha256(compiled_prompt.encode("utf-8")).hexdigest(),
        "edited_shot_count": len(compiled.get("shots") or []),
        "edited_action_indices": _list(compiled.get("operator_edited_action_indices")),
    }
    return {
        "prepared_plan": compiled,
        "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
        "warnings": _list(compiled.get("warnings")),
        "edited_shot_count": len(compiled.get("shots") or []),
    }


def _night_schedule() -> tuple[datetime, datetime]:
    current = now_bjt()
    start = current.replace(hour=20, minute=0, second=0, microsecond=0)
    if current.hour < 9:
        start = current
        end = current.replace(hour=9, minute=0, second=0, microsecond=0)
    elif current < start:
        end = (current + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        start = current
        end = (current + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return start, end


async def submit_production(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    idempotency_key = _text(payload.get("idempotency_key"), 128)
    if len(idempotency_key) < 8:
        raise AppError("MEDIA_IDEMPOTENCY_KEY_REQUIRED", 422)
    brief = _dict(payload.get("brief")) or _dict(payload.get("normalized_brief"))
    operator_input = _dict(brief.get("operator_input")) or {
        "product": _text(brief.get("product"), 300),
        "script": _text(brief.get("script"), 6000),
        "request": _text(
            brief.get("request") or brief.get("requirements") or brief.get("generation_requirements"),
            6000,
        ),
        "platform": _text(brief.get("platform"), 80) or "douyin",
        "duration_seconds": payload.get("duration_seconds") or brief.get("duration_seconds") or 5,
        "ratio": _text(payload.get("ratio") or brief.get("ratio"), 20) or "9:16",
        "audio_enabled": brief.get("audio_enabled") is not False,
    }
    script_normalization = normalize_frontdesk_script(brief.get("script"))
    if _text(brief.get("script"), 6000):
        brief = {
            **brief,
            "script": _text(script_normalization.get("normalized_script"), 6000),
            "script_normalization": {
                "changed": bool(script_normalization.get("changed")),
                "turn_count": int(script_normalization.get("turn_count") or 0),
                "speaker_count": int(script_normalization.get("speaker_count") or 0),
                "speakers": _list(script_normalization.get("speakers")),
                "policy_version": AD_MATERIAL_POLICY_VERSION,
            },
        }
    final_plan = _dict(payload.get("final_plan")) or _dict(payload.get("prepared_plan"))
    source_roles = _normalize_source_roles(_list(payload.get("source_roles")) or _list(payload.get("reference_assets")))
    brief, source_roles = _bind_source_mentions(brief, source_roles)
    references = [
        {
            "asset_id": item["asset_id"], "role": item["technical_role"],
            "business_role": item["role"], "purpose": item["purpose"],
            **({"mention_token": item["mention_token"]} if item.get("mention_token") else {}),
        }
        for item in source_roles
    ]
    creative_option = _text(payload.get("creative_option"), 50) or "smart"
    if creative_option not in CREATIVE_OPTIONS:
        raise AppError("MEDIA_CREATIVE_OPTION_INVALID", 422, {"creative_option": creative_option})
    duration_contract = production_duration_contract(payload.get("duration_seconds") or 5)
    requested_duration_seconds = int(duration_contract["requested_seconds"])
    generation_duration_seconds = int(duration_contract["generation_seconds"])
    # Never trust an older browser draft to preserve the prepare-time fitted
    # script. Rebuild the exact clip contract at the durable submit boundary
    # and overwrite both the business brief and final plan before H3 compile.
    submit_timing = _script_timing_for_clip(brief.get("script"), requested_duration_seconds)
    brief = {
        **brief,
        "operator_input": operator_input,
        "duration_seconds": generation_duration_seconds,
        "requested_duration_seconds": requested_duration_seconds,
        "delivery_duration_seconds": duration_contract["delivery_seconds"],
        "target_duration_seconds": duration_contract["target_seconds"],
        "script_timing": submit_timing,
    }
    final_plan = _apply_script_timing_to_plan(final_plan, submit_timing)
    inferred_mode = infer_production_mode(
        source_roles,
        continuation=creative_option == "continuation" or bool(duration_contract["continuation"]),
    )
    benchmark_override = payload.get("benchmark_override") is True
    if benchmark_override:
        _require_publisher(user)
    preset_id, params, preset = await _resolve_output_preset(
        db,
        project,
        payload.get("output_preset_id"),
        allow_locked_benchmark=benchmark_override,
    )
    output_ratio = normalize_output_ratio(payload.get("ratio") or brief.get("ratio"))
    params = output_params_for_ratio(params, output_ratio)
    brief = {**brief, "ratio": output_ratio}
    final_plan = {**final_plan, "ratio": output_ratio}
    fps = int(params.get("fps") or 24)
    params["frames"] = production_frames_for_duration(generation_duration_seconds, fps=fps)
    if duration_contract["delivery_seconds"]:
        params["delivery_duration_seconds"] = int(duration_contract["delivery_seconds"])
    if "audio_enabled" in payload:
        params["audio_enabled"] = bool(payload.get("audio_enabled"))
    elif "audio_enabled" in brief:
        params["audio_enabled"] = bool(brief.get("audio_enabled"))
    has_reference_video = any(item.get("technical_role") == "reference_video" for item in source_roles)
    if has_reference_video and brief.get("strip_reference_text") is True:
        # Source-video audio is removed by governed preprocessing.  A new,
        # explicitly supplied dialogue may still be synthesized; removing
        # source overlays must not silently turn a talking ad into a mute clip.
        brief = {**brief, "reference_source_audio_isolated": True}
    supplied_rights = _dict(payload.get("rights"))
    rights = {
        **supplied_rights,
        # The workbench is department-scoped and its source library contains
        # organization-owned material. Record that policy on the server so an
        # operator does not have to repeat a legal checkbox for every job.
        "copyright_authorized": True,
        "portrait_authorized": True,
        "voice_authorized": True,
        "authorization_basis": "department_owned_material_policy",
        "authorization_scope": "department",
        "authorization_department_id": run.department_id or project.department_id,
        "authorization_recorded_at": isoformat_bjt(now_bjt()),
        "benchmark_run": benchmark_override,
    }
    common = {
        "plan_comparison_id": _text(payload.get("plan_comparison_id"), 50) or None,
        "selected_source": (
            "manual"
            if (
                creative_option == "direct_prompt"
                or payload.get("user_compiled_prompt") is not None
                or _text(final_plan.get("planning_mode"), 40) == "operator_brief"
                or _text(brief.get("planning_mode"), 40) == "operator_brief"
            )
            else "baseline"
        ),
        "final_plan": final_plan,
        "brief": brief,
        "mode": inferred_mode,
        "params": params,
        "reference_assets": references,
        "source_roles": source_roles,
        "rights": rights,
        "output_preset_id": preset_id,
        "creative_option": creative_option,
        "business_title": _text(payload.get("business_title"), 240),
        "acknowledge_brand_text_risk": payload.get("acknowledge_brand_text_risk"),
        "user_compiled_prompt": payload.get("user_compiled_prompt"),
    }
    if inferred_mode == "continuation":
        source = next((item for item in source_roles if item["role"] in {"continuity_anchor", "motion_reference", "reference_video"}), None)
        if source is None:
            raise AppError("MEDIA_CONTINUATION_SOURCE_INVALID", 422)
        planned = await plan_continuation(db, user, project, run, {
            "source_asset_id": source["asset_id"],
            "target_duration_seconds": int(duration_contract["target_seconds"] or payload.get("target_duration_seconds") or 20),
            "segment_duration_seconds": generation_duration_seconds,
            "generated_only": requested_duration_seconds == 30,
            "brief": brief,
            "locks": _dict(payload.get("continuation_locks")),
            "audio_continuity": bool(payload.get("audio_continuity", True)),
        })
        submitted = await submit_continuation(db, user, project, run, {
            "chain_id": _dict(planned.get("chain")).get("id"), "params": params, "rights": rights,
        })
        return {**submitted, "submission_kind": "continuation", "output_preset": preset}

    schedule_mode = _text(payload.get("schedule_mode"), 30) or "immediate"
    not_before = None
    deadline = None
    if schedule_mode == "night":
        not_before, deadline = _night_schedule()
    elif schedule_mode == "custom":
        not_before = _parse_time(payload.get("not_before_at"), field="not_before_at", required=True)
        deadline = _parse_time(payload.get("deadline_at"), field="deadline_at", required=True)
    elif schedule_mode != "immediate":
        raise AppError("MEDIA_SCHEDULE_INVALID", 422, {"field": "schedule_mode"})
    directions = _list(payload.get("theme_directions"))
    quantity = min(max(int(payload.get("quantity") or 1), 1), 600)
    if directions or quantity > 1 or schedule_mode != "immediate":
        if payload.get("user_compiled_prompt") is not None:
            raise AppError(
                "MEDIA_PROMPT_OVERRIDE_BATCH_UNSUPPORTED",
                422,
                {"detail": "手工修改的最终提示词只适用于单条立即生成；主题发散或批量任务请分别修改候选方案。"},
            )
        if directions:
            normalized_directions = []
            for direction in directions:
                item = _dict(direction)
                normalized_directions.append({
                    **item,
                    "variant_count": min(max(int(item.get("variant_count") or 1), 1), 100),
                    "output_preset_id": preset_id,
                    "creative_option": creative_option,
                    "source_roles": source_roles,
                })
        else:
            normalized_directions = [{
                "id": "direction-1", "title": _text(brief.get("creative_angle"), 120) or "主方向",
                "final_plan": final_plan, "brief": brief, "variant_count": quantity,
                "output_preset_id": preset_id, "creative_option": creative_option,
                "source_roles": source_roles,
            }]
        result = await create_production_batch(db, user, project, run, {
            **common,
            "idempotency_key": idempotency_key,
            "directions": normalized_directions,
            # Quantity means independent seed variants of the plan the user
            # just reviewed. Theme divergence already supplies a distinct
            # final_plan per direction. The legacy layout campaign variants
            # are intended for non-human abstract B-roll benchmarks and can
            # otherwise overwrite people, dialogue and reference framing.
            "variation_policy": "seed_only",
            "title": _text(payload.get("batch_title"), 240) or _text(payload.get("business_title"), 240) or "投流素材批次",
            "theme": _text(brief.get("request") or brief.get("requirement"), 4000),
            "not_before_at": isoformat_bjt(not_before),
            "deadline_at": isoformat_bjt(deadline),
            "allowed_nodes": _list(payload.get("allowed_nodes")),
            "resource_policy": {"only_when_idle": schedule_mode != "immediate", "max_parallel_per_node": 1},
            "seed": payload.get("seed"),
        })
        return {**result, "submission_kind": "batch", "output_preset": preset}
    result = await _core()._submit_job(db, user, project, run, {**common, "idempotency_key": idempotency_key})
    return {**result, "submission_kind": "single", "output_preset": preset}


async def cloud_reference_search(
    db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    from app.codex import cloud_video as cloud_video_service
    from app.codex import service as codex_service

    query = _text(payload.get("search"), 120)
    limit = min(max(int(payload.get("limit") or 24), 1), 60)
    # The cloud-video API sorts by newest upload first. Fresh rows frequently
    # still have canPlay=0 while their transcode/audit is pending, so fetching
    # only the first UI-sized page can incorrectly make an entire library look
    # unavailable. Scan a bounded window server-side, keep upstream URLs
    # redacted, and present importable rows first without hiding metadata rows.
    scan_page_size = 60
    scan_max_pages = 5
    scan_max_items = scan_page_size * scan_max_pages
    try:
        live = await cloud_video_service.builtin_cloud_video_videos({
            "search": query,
            "page_size": scan_page_size,
            "auto_page": True,
            "max_pages": scan_max_pages,
            "max_items": scan_max_items,
        })
        normalized = [
            _normalize_cloud_reference_item(item, allow_import=True)
            for item in _list(live.get("items"))
        ]
        importable = [item for item in normalized if item["selectable"]]
        unavailable = [item for item in normalized if not item["selectable"]]
        visible_items = (importable + unavailable)[:limit]
        if importable:
            notice = (
                f"已扫描 {len(normalized)} 条匹配素材，其中 {len(importable)} 条可安全导入；"
                "结果已按可播放优先排列，原始地址仍由平台保管。"
            )
        else:
            notice = (
                f"已扫描最近 {len(normalized)} 条匹配素材，当前均未完成上游转码或审核；"
                "可查看元数据，但暂不能导入。"
            )
        return {
            "source": "live_connector",
            "as_of": isoformat_bjt(now_bjt()),
            "items": visible_items,
            "total": live.get("total"),
            "scanned_count": len(normalized),
            "importable_count": len(importable),
            "connector_status": "online",
            "notice": notice,
        }
    except AppError as exc:
        if exc.code != "MCP_ENV_MISSING":
            raise
    cached = await codex_service._builtin_samplebrand_daily_analysis_input(
        db, user, {"max_rows": 500, "include_zero_cost": False}
    )
    items = []
    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in _list(cached.get("daily_video_rows")):
        video_id = _text(_dict(row).get("video_id"), 50)
        if video_id and video_id not in rows_by_id:
            rows_by_id[video_id] = _dict(row)
    for item in _list(cached.get("videos")):
        video = _dict(item)
        searchable = " ".join((_text(video.get("title"), 240), _text(video.get("category_name"), 120))).lower()
        if query and query.lower() not in searchable:
            continue
        metrics = _dict(_dict(rows_by_id.get(_text(video.get("id"), 50))).get("metrics"))
        items.append(_normalize_cloud_reference_item({
            "video_id": video.get("id"), "title": video.get("title"), "category_name": video.get("category_name"),
            "duration_seconds": video.get("duration_seconds"), "has_cover": _dict(video.get("media")).get("has_cover"),
            "has_video": _dict(video.get("media")).get("has_video"),
            "can_play": video.get("can_play"), "state_label": video.get("state_label"),
            "metrics": {key: metrics.get(key) for key in ("statCost", "roi", "clickRate", "convertRate")},
        }, allow_import=False))
        if len(items) >= limit:
            break
    return {
        "source": "weekly_cache", "as_of": cached.get("analysis_date"), "items": items,
        "total": len(items), "connector_status": "missing_configuration",
        "notice": "实时云视频连接器未配置；当前仅展示带采集日期的缓存元数据，不能直接用作生成素材。",
    }


def _normalise_metric(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return int(value) if float(value).is_integer() else round(float(value), 6)


def _normalize_cloud_reference_item(item: Any, *, allow_import: bool) -> dict[str, Any]:
    """Return one stable, URL-free contract for live and cached cloud video rows."""

    row = _dict(item)
    media = _dict(row.get("media"))
    source_metrics = _dict(row.get("metrics"))
    video_id = _text(row.get("video_id") or row.get("id"), 80)
    title = _text(row.get("title") or row.get("video_name") or row.get("name"), 300) or (
        f"云视频 {video_id}" if video_id else "未命名云视频"
    )
    has_video = bool(media.get("has_video") or row.get("has_video"))
    can_play = bool(row.get("can_play"))
    metrics: dict[str, int | float] = {}
    metric_aliases = {
        "roi": (source_metrics.get("roi"), row.get("roi")),
        "stat_cost": (source_metrics.get("stat_cost"), source_metrics.get("statCost"), row.get("stat_cost")),
        "click_rate": (source_metrics.get("click_rate"), source_metrics.get("clickRate"), row.get("click_rate")),
        "convert_rate": (source_metrics.get("convert_rate"), source_metrics.get("convertRate"), row.get("convert_rate")),
        "views": (source_metrics.get("views"), row.get("views")),
        "downloads": (source_metrics.get("downloads"), row.get("downloads")),
        "collects": (source_metrics.get("collects"), row.get("collects")),
        "pushes": (source_metrics.get("pushes"), row.get("pushes")),
    }
    for key, candidates in metric_aliases.items():
        for candidate in candidates:
            value = _normalise_metric(candidate)
            if value is not None:
                metrics[key] = value
                break
    selectable = bool(allow_import and video_id and has_video and can_play)
    return {
        "video_id": video_id,
        "title": title,
        "category_name": _text(row.get("category_name"), 120) or None,
        "duration_seconds": _normalise_metric(row.get("duration_seconds") or row.get("duration")),
        "size_bytes": _normalise_metric(row.get("size_bytes")),
        "created_at": _text(row.get("created_at"), 80) or None,
        "state_label": _text(row.get("state_label"), 80) or None,
        "has_cover": bool(media.get("has_cover") or row.get("has_cover")),
        "has_video": has_video,
        "can_play": can_play,
        "metrics": metrics,
        "selectable": selectable,
        "import_status": "ready" if selectable else "metadata_only" if not allow_import else "media_unavailable",
    }


async def cloud_reference_import(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    """Import a cloud video through the server without exposing its upstream URL or credential."""

    from app.codex import cloud_video as cloud_video_service

    video_id = _text(payload.get("video_id"), 80)
    role = _text(payload.get("role"), 40) or "motion_reference"
    if not video_id:
        raise AppError("MEDIA_CLOUD_VIDEO_NOT_FOUND", 404, {"reason": "请选择要导入的云视频"})
    if role not in {"motion_reference", "continuity_anchor"}:
        raise AppError("MEDIA_ASSET_ROLE_INVALID", 422, {"field": "role"})

    raw = await cloud_video_service._cloud_video_raw_by_id(video_id)
    if not raw:
        raise AppError("MEDIA_CLOUD_VIDEO_NOT_FOUND", 404, {"video_id": video_id})
    mapped = cloud_video_service._map_video(raw, include_labels=True, include_raw=False)
    media = cloud_video_service._raw_video_media(raw)
    if not mapped.get("can_play") or not media.get("video_url"):
        raise AppError(
            "MEDIA_CLOUD_VIDEO_UNAVAILABLE",
            409,
            {"video_id": video_id, "reason": "该云视频当前没有可导入的视频文件"},
        )

    max_bytes = int(getattr(project_service, "PROJECT_RUN_ASSET_MAX_BYTES", 50 * 1024 * 1024))
    try:
        data_url, byte_size, mime_type = await asyncio.to_thread(
            cloud_video_service._download_media_data_url,
            str(media["video_url"]),
            default_mime="video/mp4",
            max_bytes=max_bytes,
        )
        if not str(mime_type or "").lower().startswith("video/"):
            raise ValueError("upstream_media_is_not_video")
        encoded = data_url.split(",", 1)[1]
        content = base64.b64decode(encoded, validate=True)
        if len(content) != byte_size:
            raise ValueError("downloaded_media_size_mismatch")
    except ValueError as exc:
        too_large = "media_too_large" in str(exc)
        reason = "视频超过平台单素材大小限制" if too_large else "云视频文件校验失败"
        code = "MEDIA_CLOUD_VIDEO_TOO_LARGE" if too_large else "MEDIA_CLOUD_VIDEO_IMPORT_FAILED"
        raise AppError(code, 413 if too_large else 502, {"video_id": video_id, "reason": reason, "max_bytes": max_bytes}) from exc
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            "MEDIA_CLOUD_VIDEO_IMPORT_FAILED",
            502,
            {"video_id": video_id, "reason": "云视频文件下载失败，请稍后重试"},
        ) from exc

    uploaded = await project_service.upload_project_run_asset(
        db,
        user,
        run.id,
        file_name=f"cloud-video-{video_id}.mp4",
        mime_type=str(mime_type or "video/mp4"),
        content=content,
        metadata={
            "source": "cloud_video_controlled_import",
            "cloud_video_id": video_id,
            "cloud_video_title": mapped.get("title"),
            "cloud_video_category": mapped.get("category_name"),
            "material_role": role,
            "authorization_basis": "department_owned_material_policy",
            "department_id": _department_id(run, project),
            "imported_by": _user_id(user),
            "imported_at": isoformat_bjt(now_bjt()),
            "upstream_url_stored": False,
        },
    )
    asset = _dict(uploaded.get("asset"))
    return {
        "asset": asset,
        "source_role": {"asset_id": asset.get("id"), "role": role, "purpose": BUSINESS_ASSET_ROLES[role][1]},
        "cloud_video": _normalize_cloud_reference_item(mapped, allow_import=True),
        "deduped": bool(uploaded.get("deduped")),
        "credential_location": "platform_only",
        "raw_url_returned": False,
    }


async def _references_from_group(db: AsyncSession, group_id: str | None) -> tuple[str | None, list[dict[str, Any]], dict[str, Any]]:
    if not group_id:
        return None, [], {}
    group = await db.get(MediaAssetGroup, group_id)
    if not group or group.status == "archived":
        raise AppError("MEDIA_ASSET_GROUP_NOT_FOUND", 404)
    items = _list(group.items_json)
    references = [
        {"asset_id": item["source_asset_id"], "role": item["technical_role"], "purpose": item.get("purpose")}
        for item in items
    ]
    mode = "image_to_video" if len(items) == 1 and items[0].get("role") == "product_packshot" else "reference_replay"
    return mode, references, {"id": group.id, "version": group.version, "name": group.name}


async def create_production_batch(db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    idempotency_key = _text(payload.get("idempotency_key"), 128) or None
    if idempotency_key:
        existing = (
            await db.execute(
                select(MediaProductionBatch).where(
                    MediaProductionBatch.project_id == project.id,
                    MediaProductionBatch.idempotency_key == idempotency_key,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"batch": await _serialize_batch(db, existing), "jobs": [], "jobs_truncated": True, "deduped": True}
    directions = _list(payload.get("directions"))
    if not directions:
        directions = [{"id": "direction-1", "title": _text(payload.get("title"), 120) or "默认方向", "final_plan": _dict(payload.get("final_plan")), "variant_count": int(payload.get("candidate_count") or 1)}]
    total = sum(min(max(int(_dict(item).get("variant_count") or 1), 1), 100) for item in directions)
    if total < 1 or total > 600:
        raise AppError("MEDIA_BATCH_INVALID", 422, {"field": "candidate_count", "maximum": 600})
    not_before = _parse_time(payload.get("not_before_at"), field="not_before_at")
    deadline = _parse_time(payload.get("deadline_at") or payload.get("target_end_at"), field="deadline_at")
    now = now_bjt()
    if not_before and not_before > now + timedelta(days=2):
        raise AppError("MEDIA_SCHEDULE_INVALID", 422, {"field": "not_before_at", "detail": "must be within 48 hours"})
    if deadline and (deadline <= (not_before or now) or deadline > now + timedelta(days=2)):
        raise AppError("MEDIA_SCHEDULE_INVALID", 422, {"field": "deadline_at", "detail": "must be after start and within 48 hours"})
    allowed_nodes = list(dict.fromkeys(_text(item, 50) for item in _list(payload.get("allowed_nodes")) if _text(item, 50)))
    resource_policy = {"only_when_idle": True, "max_parallel_per_node": 1, **_dict(payload.get("resource_policy"))}
    resource_policy["max_parallel_per_node"] = 1
    batch = MediaProductionBatch(
        id=_new_id("mpb"), project_id=project.id, project_run_id=run.id, department_id=_department_id(run, project),
        title=_text(payload.get("title"), 240) or "夜间投流素材批次", theme=_text(payload.get("theme"), 4000) or None,
        status="scheduled" if not_before and not_before > now else "queued", not_before_at=not_before,
        deadline_at=deadline, allowed_nodes_json=allowed_nodes, resource_policy_json=resource_policy,
        plan_json={"directions": directions, "prompt_policy_version": H3_PROMPT_POLICY_VERSION}, counters_json={"total": total},
        requested_by=_user_id(user), idempotency_key=idempotency_key, created_at=now, updated_at=now,
    )
    db.add(batch)
    await db.flush()
    jobs = []
    seed_base = int(payload.get("seed") or int(batch.id[-8:], 16))
    global_index = 0
    for direction in directions:
        direction = _dict(direction)
        variants = min(max(int(direction.get("variant_count") or 1), 1), 100)
        base_plan = _dict(direction.get("final_plan")) or _dict(payload.get("final_plan"))
        if not base_plan:
            brief = _dict(direction.get("brief")) or _dict(payload.get("brief"))
            _, base_plan = await _core()._call_baseline_plan(db, user, run, brief)
        group_id = _text(direction.get("asset_group_id") or payload.get("asset_group_id"), 50)
        group_mode, references, group_lineage = await _references_from_group(db, group_id or None)
        workflow_kind = _text(direction.get("workflow_kind"), 40)
        workflow_mode = WORKFLOW_KINDS.get(workflow_kind, (None, None))[0] if workflow_kind else None
        variation_policy = _text(direction.get("variation_policy") or payload.get("variation_policy"), 40) or "layout_v1"
        if variation_policy not in {"layout_v1", "seed_only"}:
            raise AppError(
                "MEDIA_BATCH_INVALID",
                422,
                {"field": "variation_policy", "allowed": ["layout_v1", "seed_only"]},
            )
        for variant_index in range(variants):
            global_index += 1
            plan_base = {
                key: value
                for key, value in base_plan.items()
                if variation_policy != "seed_only"
                or key not in {"production_variant", "production_variant_application", "prompt_policy_upgrade"}
            }
            plan = {
                **plan_base,
                **(
                    {"production_variant": production_variant_for_candidate(global_index, batch.id)}
                    if variation_policy == "layout_v1"
                    else {}
                ),
                "production_campaign": {
                    "id": batch.id, "title": batch.title, "direction_id": direction.get("id"),
                    "direction_title": direction.get("title"), "candidate_index": global_index,
                    "candidate_count": total, "target_end_at": isoformat_bjt(deadline),
                    "variation_policy": variation_policy,
                    "asset_group": group_lineage or None,
                },
            }
            submit_payload = {
                "final_plan": plan, "brief": _dict(direction.get("brief")) or _dict(payload.get("brief")),
                "plan_comparison_id": payload.get("plan_comparison_id"),
                "mode": group_mode or direction.get("mode") or workflow_mode or payload.get("mode") or plan.get("h3_mode"),
                "params": {**_dict(payload.get("params")), **_dict(direction.get("params")), "seed": seed_base + global_index, "batch_count": 1},
                "reference_assets": references or _list(direction.get("reference_assets")) or _list(payload.get("reference_assets")),
                "rights": _dict(payload.get("rights")), "acknowledge_brand_text_risk": payload.get("acknowledge_brand_text_risk"),
                "idempotency_key": f"{batch.id}:{global_index}", "production_batch_id": batch.id,
                "job_group_id": batch.id,
                "business_title": direction.get("business_title") or payload.get("business_title"),
                "output_preset_id": direction.get("output_preset_id") or payload.get("output_preset_id"),
                "creative_option": direction.get("creative_option") or payload.get("creative_option") or workflow_kind or "smart",
                "source_roles": direction.get("source_roles") or payload.get("source_roles") or [],
                "workflow_definition_id": direction.get("workflow_definition_id") or payload.get("workflow_definition_id"),
                "not_before_at": isoformat_bjt(not_before), "deadline_at": isoformat_bjt(deadline),
            }
            result = await _core()._submit_job(db, user, project, run, submit_payload)
            if len(jobs) < 20:
                jobs.append(result["job"])
    batch.counters_json = await _batch_counts(db, batch.id)
    return {"batch": await _serialize_batch(db, batch), "jobs": jobs, "jobs_truncated": total > len(jobs)}


async def manage_production_batch(db: AsyncSession, run: ProjectRun, payload: dict[str, Any], action: str) -> dict[str, Any]:
    if action == "list":
        rows = (await db.execute(select(MediaProductionBatch).where(MediaProductionBatch.project_id == run.project_id).order_by(MediaProductionBatch.created_at.desc()).limit(100))).scalars().all()
        return {"items": [await _serialize_batch(db, row) for row in rows], "total": len(rows)}
    row = await db.get(MediaProductionBatch, _text(payload.get("batch_id") or payload.get("id"), 50))
    if not row or row.project_id != run.project_id:
        raise AppError("MEDIA_BATCH_NOT_FOUND", 404)
    if action == "get":
        return {"batch": await _serialize_batch(db, row)}
    if action == "cancel":
        row.status = "cancelled"
        jobs = (await db.execute(select(MediaGenerationJob).where(MediaGenerationJob.production_batch_id == row.id, MediaGenerationJob.status == "queued"))).scalars().all()
        for job in jobs:
            _core().transition_media_job(job, "cancelled", error="生产批次已取消，未开始任务不再派单。")
    else:
        requested = _text(payload.get("status"), 30)
        if requested not in {"paused", "queued"}:
            raise AppError("MEDIA_BATCH_STATUS_INVALID", 422)
        row.status = requested
        if payload.get("allowed_nodes") is not None:
            row.allowed_nodes_json = list(dict.fromkeys(_text(item, 50) for item in _list(payload.get("allowed_nodes")) if _text(item, 50)))
        if payload.get("priority") is not None:
            priority = min(max(int(payload.get("priority")), 1), 1000)
            jobs = (await db.execute(select(MediaGenerationJob).where(MediaGenerationJob.production_batch_id == row.id, MediaGenerationJob.status == "queued"))).scalars().all()
            for job in jobs:
                job.priority = priority
    row.updated_at = now_bjt()
    return {"batch": await _serialize_batch(db, row)}


async def plan_continuation(db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    source = await db.get(ProjectRunAsset, _text(payload.get("source_asset_id"), 50))
    if not source or source.project_id != project.id or _asset_kind(source) != "video":
        raise AppError("MEDIA_CONTINUATION_SOURCE_INVALID", 422)
    source_duration = _core()._reference_duration_seconds(source)
    if source_duration is None:
        probe = await asyncio.to_thread(project_service._probe_project_media_file, project_service._project_run_asset_abs_path(source), source.mime_type)
        if probe:
            source.metadata_json = {**_dict(source.metadata_json), "media_probe": probe}
            source_duration = _core()._reference_duration_seconds(source)
    if source_duration is None or source_duration < 2 or source_duration > 15:
        raise AppError("MEDIA_CONTINUATION_SOURCE_INVALID", 422, {"detail": "source video must be verified and 2-15 seconds"})
    target = int(payload.get("target_duration_seconds") or 20)
    segment_duration = min(max(int(payload.get("segment_duration_seconds") or 5), 4), 15)
    generated_only = payload.get("generated_only") is True
    # The 30-second business preset generates two new 15-second clips.  The
    # uploaded source guides the first clip but is not copied into the final
    # preview; clip two then uses clip one's result as its real anchor.
    segment_durations = continuation_segment_durations(0 if generated_only else source_duration, target, segment_duration)
    generated_duration = sum(segment_durations)
    segment_count = len(segment_durations)
    continuity_locks = {
        "identity": True,
        "face": True,
        "wardrobe": True,
        "scene": True,
        "camera": True,
        "lighting": True,
        "color": True,
        "motion_direction": True,
        "dialogue_turns": True,
        **_dict(payload.get("locks")),
    }
    brief = {
        **_dict(payload.get("brief")),
        "request": _text(payload.get("request"), 3000) or (
            "生成连续的双段叙事：人物身份、脸型、发型、服装、场景、机位、光线、色彩和运动方向保持一致；"
            "对话轮次自然承接，第二段从第一段结束动作与情绪继续，不重复开场。"
        ),
        "duration_seconds": segment_duration,
        "reference_requirement": (
            "使用前序视频和尾帧作为连续性参考；保持人物、场景和声音状态，"
            "从上一段未完成的动作与对话自然继续，不重复开场，不突然跳切或改变主体。"
        ),
        "continuity_locks": continuity_locks,
    }
    model, base_plan = await _core()._call_baseline_plan(db, user, run, brief)
    now = now_bjt()
    chain = MediaContinuationChain(
        id=_new_id("mcc"), project_id=project.id, project_run_id=run.id, department_id=_department_id(run, project),
        source_asset_id=source.id, status="planned", target_duration_seconds=target,
        config_json={
            "source_duration_seconds": source_duration, "segment_duration_seconds": segment_duration,
            "planned_generated_duration_seconds": generated_duration,
            "planned_total_duration_seconds": round(generated_duration if generated_only else source_duration + generated_duration, 3),
            "generated_only": generated_only,
            "preview_includes_source": not generated_only,
            "locks": continuity_locks, "audio_continuity": bool(payload.get("audio_continuity", True)),
            "model": model, "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
        },
        requested_by=_user_id(user), created_at=now, updated_at=now,
    )
    db.add(chain)
    await db.flush()
    segments = []
    for index, duration in enumerate(segment_durations, 1):
        plan = compile_h3_plan({
            **base_plan, "duration_seconds": duration, "h3_mode": "reference_replay",
            "production_variant": production_variant_for_candidate(index, chain.id),
        }, brief=brief)
        segment = MediaContinuationSegment(
            id=_new_id("mcs"), chain_id=chain.id, segment_index=index, status="planned",
            duration_seconds=duration, prompt_json=plan, locks_json=continuity_locks,
            created_at=now, updated_at=now,
        )
        db.add(segment)
        segments.append(segment)
    await db.flush()
    return {"chain": await serialize_continuation(db, chain), "segments": [serialize_segment(row) for row in segments]}


def serialize_segment(row: MediaContinuationSegment) -> dict[str, Any]:
    return {
        "id": row.id, "chain_id": row.chain_id, "segment_index": row.segment_index, "status": row.status,
        "duration_seconds": row.duration_seconds, "prompt": row.prompt_json or {}, "locks": row.locks_json or {},
        "anchor_asset_id": row.anchor_asset_id, "media_job_id": row.media_job_id,
        "seam_analysis": row.seam_analysis_json or {}, "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


def continuation_timeline_handoff(
    row: MediaContinuationChain,
    source: ProjectRunAsset | None,
    segments: list[MediaContinuationSegment],
    jobs_by_id: dict[str, MediaGenerationJob],
    assets_by_id: dict[str, ProjectRunAsset],
    preview: ProjectRunAsset | None,
) -> dict[str, Any]:
    """Build a bounded editor handoff without pretending to be an OTIO file.

    Continuation clips are independently generated and reviewed, while the
    preview is only a convenience render.  Editors and downstream automation
    therefore need immutable asset hashes, exact timeline ranges and seam
    gates instead of reverse engineering the concatenated MP4.  This native
    contract can later be converted by a validated OpenTimelineIO adapter; the
    service does not emit an unvalidated ``.otio`` document today.
    """

    config = _dict(row.config_json)
    try:
        source_duration = max(0.0, float(config.get("source_duration_seconds") or 0))
    except (TypeError, ValueError):
        source_duration = 0.0
    generated_only = config.get("preview_includes_source") is False or config.get("generated_only") is True
    fps = 24
    for segment in segments:
        job = jobs_by_id.get(segment.media_job_id or "")
        if not job:
            continue
        try:
            candidate_fps = int(_dict(job.params_json).get("fps") or 24)
        except (TypeError, ValueError):
            candidate_fps = 24
        if candidate_fps > 0:
            fps = candidate_fps
            break

    def asset_ref(asset: ProjectRunAsset | None) -> dict[str, Any] | None:
        if asset is None:
            return None
        return {
            "id": asset.id,
            "sha256": asset.sha256,
            "mime_type": asset.mime_type,
            "file_name": asset.file_name,
            "byte_size": asset.byte_size,
            "download_url": f"/api/projects/runs/{asset.project_run_id}/assets/{asset.id}/download",
        }

    clips: list[dict[str, Any]] = []
    cursor = 0.0
    if not generated_only:
        clips.append({
            "clip_id": f"source:{row.source_asset_id}",
            "kind": "source",
            "sequence_index": 0,
            "status": "ready" if source else "missing",
            "timeline_range": {"start_seconds": 0.0, "duration_seconds": round(source_duration, 3)},
            "source_range": {"start_seconds": 0.0, "duration_seconds": round(source_duration, 3)},
            "asset": asset_ref(source),
            "generator": None,
            "seam_from_previous": None,
        })
        cursor = source_duration

    for segment in segments:
        job = jobs_by_id.get(segment.media_job_id or "")
        asset = assets_by_id.get(job.result_asset_id or "") if job else None
        duration = max(0.0, float(segment.duration_seconds or 0))
        prompt = _dict(job.prompt_json) if job else _dict(segment.prompt_json)
        params = _dict(job.params_json) if job else {}
        clips.append({
            "clip_id": segment.id,
            "kind": "generated",
            "sequence_index": segment.segment_index,
            "status": "ready" if asset else (job.status if job else segment.status),
            "timeline_range": {"start_seconds": round(cursor, 3), "duration_seconds": round(duration, 3)},
            "source_range": {"start_seconds": 0.0, "duration_seconds": round(duration, 3)},
            "asset": asset_ref(asset),
            "anchor_asset_id": segment.anchor_asset_id,
            "media_job_id": segment.media_job_id,
            "generator": {
                "prompt_policy_version": _text(prompt.get("prompt_policy_version"), 80),
                "workflow_template_id": getattr(job, "workflow_template_id", None),
                "workflow_version": getattr(job, "workflow_version", None),
                "model_version": getattr(job, "model_version", None),
                "model_sha256": getattr(job, "model_sha256", None),
                "seed": params.get("seed"),
                "width": params.get("width"),
                "height": params.get("height"),
                "frames": params.get("frames"),
                "fps": params.get("fps"),
            },
            "seam_from_previous": _dict(segment.seam_analysis_json),
        })
        cursor += duration

    body = {
        "schema_version": "skillforge.media.timeline_handoff.v1",
        "chain_id": row.id,
        "project_id": row.project_id,
        "project_run_id": row.project_run_id,
        "status": row.status,
        "output_mode": "generated_only" if generated_only else "source_plus_generated",
        "target_duration_seconds": row.target_duration_seconds,
        "timeline_duration_seconds": round(cursor, 3),
        "timebase": {"rate": fps, "scale": 1},
        "tracks": [{
            "id": "video-audio-1",
            "kind": "video_with_linked_audio",
            "transition_policy": "hard_cut_no_overlap",
            "clips": clips,
        }],
        "render_preview": asset_ref(preview),
        "continuity": {
            "locks": _dict(config.get("locks")),
            "audio_continuity": bool(config.get("audio_continuity", True)),
            "human_audio_review_required": any(
                bool(_dict(segment.seam_analysis_json).get("human_audio_review_required"))
                for segment in segments
            ),
        },
        "interchange": {
            "native_format": "skillforge_json",
            "opentimelineio_export": {
                "status": "not_exposed",
                "reason": "A validated OpenTimelineIO adapter is not implemented; this JSON is not an .otio document.",
            },
        },
    }
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**body, "fingerprint_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


async def serialize_continuations(
    db: AsyncSession,
    rows: list[MediaContinuationChain],
) -> list[dict[str, Any]]:
    """Serialize continuation chains with three bounded bulk queries.

    The workbench snapshot includes up to twenty recent chains.  Keeping this
    path bulked prevents the timeline handoff from turning one page open into
    dozens of segment/job/asset round trips.
    """

    if not rows:
        return []
    chain_ids = [row.id for row in rows]
    segments = (
        await db.execute(
            select(MediaContinuationSegment)
            .where(MediaContinuationSegment.chain_id.in_(chain_ids))
            .order_by(MediaContinuationSegment.chain_id, MediaContinuationSegment.segment_index)
        )
    ).scalars().all()
    segments_by_chain: dict[str, list[MediaContinuationSegment]] = {chain_id: [] for chain_id in chain_ids}
    for segment in segments:
        segments_by_chain.setdefault(segment.chain_id, []).append(segment)
    job_ids = [segment.media_job_id for segment in segments if segment.media_job_id]
    jobs = (
        (await db.execute(select(MediaGenerationJob).where(MediaGenerationJob.id.in_(job_ids)))).scalars().all()
        if job_ids else []
    )
    jobs_by_id = {job.id: job for job in jobs}
    asset_ids = {
        *(row.source_asset_id for row in rows),
        *(row.preview_asset_id for row in rows if row.preview_asset_id),
        *(job.result_asset_id for job in jobs if job.result_asset_id),
    } - {None, ""}
    assets = (
        (
            await db.execute(
                select(ProjectRunAsset).where(
                    ProjectRunAsset.project_id.in_({row.project_id for row in rows}),
                    ProjectRunAsset.id.in_(asset_ids),
                )
            )
        ).scalars().all()
        if asset_ids else []
    )
    assets_by_id = {asset.id: asset for asset in assets}
    serialized = []
    for row in rows:
        chain_segments = segments_by_chain.get(row.id, [])
        source = assets_by_id.get(row.source_asset_id)
        preview = assets_by_id.get(row.preview_asset_id or "")
        timeline_handoff = continuation_timeline_handoff(
            row, source, chain_segments, jobs_by_id, assets_by_id, preview,
        )
        serialized.append({
            "id": row.id, "project_id": row.project_id, "project_run_id": row.project_run_id,
            "department_id": row.department_id, "source_asset_id": row.source_asset_id, "status": row.status,
            "target_duration_seconds": row.target_duration_seconds, "config": row.config_json or {},
            "preview_asset": project_service.serialize_run_asset(preview, include_download_url=True) if preview else None,
            "segments": [serialize_segment(segment) for segment in chain_segments],
            "timeline_handoff": timeline_handoff,
            "created_at": isoformat_bjt(row.created_at), "updated_at": isoformat_bjt(row.updated_at),
        })
    return serialized


async def serialize_continuation(db: AsyncSession, row: MediaContinuationChain) -> dict[str, Any]:
    return (await serialize_continuations(db, [row]))[0]


async def submit_continuation(db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    chain = await db.get(MediaContinuationChain, _text(payload.get("chain_id"), 50))
    if not chain or chain.project_id != project.id:
        raise AppError("MEDIA_CONTINUATION_NOT_FOUND", 404)
    if chain.status not in {"planned", "failed"}:
        return {"chain": await serialize_continuation(db, chain), "deduped": True}
    chain.config_json = {**_dict(chain.config_json), "rights": _dict(payload.get("rights"))}
    segment = (await db.execute(select(MediaContinuationSegment).where(MediaContinuationSegment.chain_id == chain.id, MediaContinuationSegment.segment_index == 1))).scalar_one()
    params = {**_core().MEDIA_DEFAULT_PRESET, **_dict(payload.get("params")), "frames": int(segment.duration_seconds * int(_dict(payload.get("params")).get("fps") or 24))}
    result = await _core()._submit_job(db, user, project, run, {
        "final_plan": segment.prompt_json, "mode": "reference_replay", "params": params,
        "reference_assets": [{"asset_id": chain.source_asset_id, "role": "reference_video", "purpose": "续时源视频"}],
        "rights": _dict(payload.get("rights")),
        "idempotency_key": continuation_segment_idempotency_key(chain.id, 1, segment.locks_json),
        "continuation_chain_id": chain.id, "sequence_index": 1,
    })
    segment.media_job_id = result["job"]["id"]
    segment.anchor_asset_id = chain.source_asset_id
    segment.status = "queued" if result["job"]["status"] == "queued" else "running"
    segment.updated_at = now_bjt()
    chain.status = "running"
    chain.updated_at = now_bjt()
    return {"chain": await serialize_continuation(db, chain), "job": result["job"], "deduped": False}


async def cancel_continuation(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    chain = await db.get(MediaContinuationChain, _text(payload.get("chain_id"), 50))
    if not chain or chain.project_id != run.project_id:
        raise AppError("MEDIA_CONTINUATION_NOT_FOUND", 404)
    jobs = (await db.execute(select(MediaGenerationJob).where(MediaGenerationJob.continuation_chain_id == chain.id, MediaGenerationJob.status.in_(["queued", "assigned", "running", "collecting"])))).scalars().all()
    for job in jobs:
        await _core()._cancel_job_row(db, job)
    segments = (await db.execute(select(MediaContinuationSegment).where(MediaContinuationSegment.chain_id == chain.id, MediaContinuationSegment.status.in_(["planned", "queued", "running"])))).scalars().all()
    for segment in segments:
        segment.status = "cancelled"
        segment.updated_at = now_bjt()
    chain.status = "cancelled"
    chain.updated_at = now_bjt()
    return {"chain": await serialize_continuation(db, chain)}


async def retry_continuation_segment(db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    segment = await db.get(MediaContinuationSegment, _text(payload.get("segment_id"), 50))
    chain = await db.get(MediaContinuationChain, segment.chain_id) if segment else None
    if not segment or not chain or chain.project_id != project.id:
        raise AppError("MEDIA_CONTINUATION_NOT_FOUND", 404)
    downstream = (await db.execute(select(MediaContinuationSegment).where(MediaContinuationSegment.chain_id == chain.id, MediaContinuationSegment.segment_index > segment.segment_index))).scalars().all()
    if any(item.media_job_id for item in downstream) and payload.get("confirm_invalidate_downstream") is not True:
        raise AppError(
            "MEDIA_CONTINUATION_DOWNSTREAM_CONFIRM_REQUIRED",
            409,
            {"downstream_segment_ids": [item.id for item in downstream if item.media_job_id]},
        )
    for item in downstream:
        item.status = "invalidated"
        item.media_job_id = None
        item.anchor_asset_id = None
        item.updated_at = now_bjt()
    if chain.preview_asset_id:
        previous_previews = _list(_dict(chain.config_json).get("previous_preview_asset_ids"))
        chain.config_json = {
            **_dict(chain.config_json),
            "previous_preview_asset_ids": [*previous_previews, chain.preview_asset_id][-12:],
        }
        chain.preview_asset_id = None
    retry_revision = _continuation_retry_revision(segment.locks_json) + 1
    segment.locks_json = {**_dict(segment.locks_json), "retry_revision": retry_revision}
    segment.seam_analysis_json = {"status": "pending", "hard_gate": True, "retry_revision": retry_revision}
    segment.updated_at = now_bjt()
    if segment.media_job_id:
        job = await db.get(MediaGenerationJob, segment.media_job_id)
        if job and job.status in {"failed", "cancelled", "rejected"}:
            await _core()._retry_job(db, run, {"job_id": job.id})
            segment.status = "queued"
            chain.status = "running"
            return {"chain": await serialize_continuation(db, chain)}
    segment.media_job_id = None
    segment.status = "planned"
    chain.status = "running"
    await advance_continuations_once(db, chain_ids=[chain.id])
    return {"chain": await serialize_continuation(db, chain)}


async def _create_continuation_preview(db: AsyncSession, user: User, run: ProjectRun, chain: MediaContinuationChain, segments: list[MediaContinuationSegment]) -> ProjectRunAsset | None:
    assets = []
    if _dict(chain.config_json).get("preview_includes_source") is not False:
        assets.append(await db.get(ProjectRunAsset, chain.source_asset_id))
    for segment in segments:
        job = await db.get(MediaGenerationJob, segment.media_job_id) if segment.media_job_id else None
        assets.append(await db.get(ProjectRunAsset, job.result_asset_id) if job and job.result_asset_id else None)
    if any(asset is None for asset in assets):
        return None
    ffmpeg = _core()._media_quality_ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="sf-continuation-") as temp_dir:
        concat_file = Path(temp_dir) / "inputs.txt"
        lines = ["file '" + str(project_service._project_run_asset_abs_path(asset)).replace("'", "'\\''") + "'" for asset in assets if asset]
        concat_file.write_text("\n".join(lines), encoding="utf-8")
        output = Path(temp_dir) / f"{chain.id}-preview.mp4"
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", "-y", str(output)]
        completed = await asyncio.to_thread(subprocess.run, command, capture_output=True, timeout=600)
        if completed.returncode != 0 or not output.exists():
            chain.config_json = {**_dict(chain.config_json), "preview_error": completed.stderr.decode("utf-8", errors="ignore")[:1000]}
            return None
        uploaded = await project_service.upload_project_run_asset(
            db, user, run.id, file_name=output.name, mime_type="video/mp4", content=output.read_bytes(),
            metadata={"source": "media_continuation_preview", "chain_id": chain.id, "preview_only": True},
        )
        return await db.get(ProjectRunAsset, _dict(uploaded.get("asset")).get("id"))


async def _continuation_seam_analysis(previous: ProjectRunAsset, current: ProjectRunAsset, technical: dict[str, Any]) -> dict[str, Any]:
    if technical.get("status") != "passed":
        return {"status": "failed", "hard_gate": True, "issues": ["current_segment_technical_validation_failed"]}
    ffmpeg = _core()._media_quality_ffmpeg_executable()

    def frame_bytes(path: Path, *, tail: bool) -> bytes:
        command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
        if tail:
            command += ["-sseof", "-0.12"]
        command += ["-i", str(path), "-frames:v", "1", "-vf", "scale=64:64", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
        result = subprocess.run(command, capture_output=True, timeout=90)
        return result.stdout if result.returncode == 0 else b""

    def audio_bytes(path: Path, *, tail: bool) -> bytes:
        command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
        if tail:
            command += ["-sseof", "-0.35"]
        command += ["-i", str(path), "-t", "0.35", "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"]
        result = subprocess.run(command, capture_output=True, timeout=90)
        return result.stdout if result.returncode == 0 else b""

    previous_frame, current_frame, previous_audio, current_audio = await asyncio.gather(
        asyncio.to_thread(frame_bytes, project_service._project_run_asset_abs_path(previous), tail=True),
        asyncio.to_thread(frame_bytes, project_service._project_run_asset_abs_path(current), tail=False),
        asyncio.to_thread(audio_bytes, project_service._project_run_asset_abs_path(previous), tail=True),
        asyncio.to_thread(audio_bytes, project_service._project_run_asset_abs_path(current), tail=False),
    )
    if not previous_frame or not current_frame or len(previous_frame) != len(current_frame):
        return {"status": "failed", "hard_gate": True, "issues": ["seam_frame_extraction_failed"]}
    previous_brightness = sum(previous_frame) / len(previous_frame)
    current_brightness = sum(current_frame) / len(current_frame)
    brightness_delta = abs(previous_brightness - current_brightness)
    pixel_delta = sum(abs(left - right) for left, right in zip(previous_frame, current_frame)) / len(previous_frame)
    issues = []
    if brightness_delta > 45:
        issues.append("brightness_jump")
    if pixel_delta > 90:
        issues.append("visual_discontinuity")
    audio_analysis: dict[str, Any]
    if bool(previous_audio) != bool(current_audio):
        issues.append("audio_stream_break")
        audio_analysis = {"status": "failed", "issue": "audio_stream_break"}
    elif not previous_audio:
        audio_analysis = {"status": "no_audio"}
    else:
        def rms(raw: bytes) -> float:
            samples = [int.from_bytes(raw[index:index + 2], "little", signed=True) for index in range(0, len(raw) - 1, 2)]
            return math.sqrt(sum(sample * sample for sample in samples) / max(1, len(samples))) / 32768

        previous_rms, current_rms = rms(previous_audio), rms(current_audio)
        loudness_delta = abs(previous_rms - current_rms)
        audio_analysis = {
            "status": "failed" if loudness_delta > 0.35 else "passed",
            "previous_rms": round(previous_rms, 4), "current_rms": round(current_rms, 4),
            "loudness_delta": round(loudness_delta, 4), "threshold": 0.35,
        }
        if loudness_delta > 0.35:
            issues.append("audio_loudness_jump")
    return {
        "status": "failed" if issues else "passed",
        "hard_gate": True,
        "brightness_delta": round(brightness_delta, 3),
        "mean_pixel_delta": round(pixel_delta, 3),
        "thresholds": {"brightness_delta_max": 45, "mean_pixel_delta_max": 90},
        "audio_continuity": audio_analysis,
        "human_audio_review_required": bool(previous_audio or current_audio),
        "issues": issues,
    }


async def _ensure_continuation_preview_job(
    db: AsyncSession,
    chain: MediaContinuationChain,
    asset: ProjectRunAsset,
    segments: list[MediaContinuationSegment],
) -> MediaGenerationJob:
    idempotency_key = f"continuation:{chain.id}:preview"
    existing = (
        await db.execute(
            select(MediaGenerationJob).where(
                MediaGenerationJob.project_id == chain.project_id,
                MediaGenerationJob.idempotency_key == idempotency_key,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    segment_jobs = [
        await db.get(MediaGenerationJob, segment.media_job_id)
        for segment in segments if segment.media_job_id
    ]
    last_job = next((job for job in reversed(segment_jobs) if job is not None), None)
    technical = await asyncio.to_thread(
        project_service._probe_project_media_file,
        project_service._project_run_asset_abs_path(asset),
        asset.mime_type,
    )
    now = now_bjt()
    row = MediaGenerationJob(
        id=_new_id("mvj"), project_id=chain.project_id, project_run_id=chain.project_run_id,
        continuation_chain_id=chain.id, sequence_index=0, idempotency_key=idempotency_key,
        department_id=chain.department_id, requested_by=chain.requested_by,
        mode="reference_replay", status="awaiting_review", priority=100,
        prompt_json={
            "creative_goal": "自动续时完整拼接预览",
            "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
            "continuation_chain_id": chain.id,
            "segment_jobs": [job.id for job in segment_jobs if job is not None],
        },
        params_json={"target_duration_seconds": chain.target_duration_seconds, "preview_only": True},
        reference_assets_json=[{"asset_id": chain.source_asset_id, "role": "reference_video", "purpose": "续时源视频"}],
        workflow_template_id="h3_r2v_v1",
        workflow_version=getattr(last_job, "workflow_version", None),
        model_version=getattr(last_job, "model_version", None),
        model_sha256=getattr(last_job, "model_sha256", None),
        result_asset_id=asset.id, result_sha256=asset.sha256,
        result_json={
            "assets": [{"id": asset.id, "sha256": asset.sha256, "download_url": f"/api/projects/runs/{chain.project_run_id}/assets/{asset.id}/download"}],
            "technical_validation": technical,
            "continuation_preview": {"chain_id": chain.id, "segment_count": len(segments), "formal_asset": False},
        },
        review_json={}, rights_json=_dict(_dict(chain.config_json).get("rights")),
        training_eligibility="pending", completed_at=now, created_at=now, updated_at=now,
    )
    db.add(row)
    await db.flush()
    chain.config_json = {**_dict(chain.config_json), "preview_job_id": row.id}
    return row


async def advance_continuations_once(db: AsyncSession, *, chain_ids: list[str] | None = None, limit: int = 20) -> dict[str, int]:
    conditions = [MediaContinuationChain.status == "running"]
    if chain_ids:
        conditions.append(MediaContinuationChain.id.in_(chain_ids))
    chains = (await db.execute(select(MediaContinuationChain).where(*conditions).order_by(MediaContinuationChain.updated_at).limit(limit))).scalars().all()
    stats = {"scanned": len(chains), "advanced": 0, "failed": 0, "previewed": 0, "diagnostic_previewed": 0}
    for chain in chains:
        segments = (await db.execute(select(MediaContinuationSegment).where(MediaContinuationSegment.chain_id == chain.id).order_by(MediaContinuationSegment.segment_index))).scalars().all()
        run = await db.get(ProjectRun, chain.project_run_id)
        project = await db.get(Project, chain.project_id)
        user = await db.get(User, chain.requested_by) if chain.requested_by else None
        if not run or not project or not user:
            chain.status = "failed"
            stats["failed"] += 1
            continue
        blocked = False
        for segment in segments:
            if segment.media_job_id:
                job = await db.get(MediaGenerationJob, segment.media_job_id)
                if not job:
                    segment.status = "failed"
                    chain.status = "failed"
                    blocked = True
                    break
                segment.status = job.status
                segment.updated_at = now_bjt()
                if job.status in {"failed", "cancelled", "rejected"}:
                    chain.status = "failed"
                    blocked = True
                    break
                if job.status not in {"awaiting_review", "approved", "syncing", "synced"}:
                    blocked = True
                    break
                seam = _dict(segment.seam_analysis_json)
                if seam.get("status") in {None, "", "pending"} and job.result_asset_id:
                    previous_asset = await db.get(ProjectRunAsset, segment.anchor_asset_id or chain.source_asset_id)
                    current_asset = await db.get(ProjectRunAsset, job.result_asset_id)
                    if previous_asset and current_asset:
                        seam = await _continuation_seam_analysis(
                            previous_asset, current_asset, _dict(_dict(job.result_json).get("technical_validation"))
                        )
                        segment.seam_analysis_json = seam
                if seam.get("status") == "failed":
                    segment.status = "failed"
                    chain.status = "failed"
                    diagnostic_preview = await _create_continuation_preview(db, user, run, chain, segments)
                    if diagnostic_preview:
                        chain.preview_asset_id = diagnostic_preview.id
                        chain.config_json = {
                            **_dict(chain.config_json),
                            "preview_gate": {
                                "status": "blocked",
                                "formal_asset": False,
                                "reason": "continuation_seam_hard_gate_failed",
                                "segment_id": segment.id,
                                "issues": _list(seam.get("issues")),
                            },
                        }
                        stats["diagnostic_previewed"] += 1
                    blocked = True
                    stats["failed"] += 1
                    break
                continue
            previous_asset_id = chain.source_asset_id
            previous_job_id = None
            if segment.segment_index > 1:
                previous = segments[segment.segment_index - 2]
                previous_job = await db.get(MediaGenerationJob, previous.media_job_id) if previous.media_job_id else None
                if not previous_job or not previous_job.result_asset_id:
                    blocked = True
                    break
                previous_asset_id = previous_job.result_asset_id
                previous_job_id = previous_job.id
            params = {**_core().MEDIA_DEFAULT_PRESET, "frames": segment.duration_seconds * 24}
            result = await _core()._submit_job(db, user, project, run, {
                "final_plan": segment.prompt_json, "mode": "reference_replay", "params": params,
                "reference_assets": [{"asset_id": previous_asset_id, "role": "reference_video", "purpose": "前序续时片段"}],
                "rights": _dict(_dict(chain.config_json).get("rights")),
                "idempotency_key": continuation_segment_idempotency_key(chain.id, segment.segment_index, segment.locks_json),
                "continuation_chain_id": chain.id, "depends_on_job_id": previous_job_id,
                "sequence_index": segment.segment_index,
            })
            segment.media_job_id = result["job"]["id"]
            segment.anchor_asset_id = previous_asset_id
            segment.status = result["job"]["status"]
            segment.seam_analysis_json = {"status": "pending", "hard_gate": True}
            segment.updated_at = now_bjt()
            stats["advanced"] += 1
            blocked = True
            break
        if not blocked and segments and not chain.preview_asset_id:
            preview = await _create_continuation_preview(db, user, run, chain, segments)
            if preview:
                chain.preview_asset_id = preview.id
                await _ensure_continuation_preview_job(db, chain, preview, segments)
                chain.status = "awaiting_review"
                stats["previewed"] += 1
            else:
                chain.status = "failed"
                stats["failed"] += 1
        chain.updated_at = now_bjt()
        await db.commit()
    return stats


async def list_annotations(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    job = await _core()._get_job_for_run(db, run, payload.get("job_id"))
    rows = (await db.execute(select(MediaReviewAnnotation).where(MediaReviewAnnotation.media_job_id == job.id).order_by(MediaReviewAnnotation.start_seconds, MediaReviewAnnotation.created_at))).scalars().all()
    return {"items": [_serialize_annotation(row) for row in rows], "total": len(rows)}


async def save_annotation(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    job = await _core()._get_job_for_run(db, run, payload.get("job_id"))
    category = _text(payload.get("category"), 60)
    severity = _text(payload.get("severity"), 20) or "warning"
    note = _text(payload.get("note"), 4000)
    try:
        start = max(0.0, float(payload.get("start_seconds") or 0))
        end = float(payload.get("end_seconds")) if payload.get("end_seconds") is not None else None
    except (TypeError, ValueError) as exc:
        raise AppError("MEDIA_REVIEW_ANNOTATION_INVALID", 422) from exc
    if category not in ANNOTATION_CATEGORIES or severity not in ANNOTATION_SEVERITIES or not note or (end is not None and end < start):
        raise AppError("MEDIA_REVIEW_ANNOTATION_INVALID", 422)
    now = now_bjt()
    row = MediaReviewAnnotation(
        id=_new_id("mra"), media_job_id=job.id, department_id=job.department_id or _department_id(run),
        start_seconds=start, end_seconds=end, category=category, severity=severity, status="open",
        note=note, evidence_json=_list(payload.get("evidence"))[:20], created_by=_user_id(user), created_at=now, updated_at=now,
    )
    db.add(row)
    await db.flush()
    return {"annotation": _serialize_annotation(row)}


async def resolve_annotation(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    row = await db.get(MediaReviewAnnotation, _text(payload.get("annotation_id") or payload.get("id"), 50))
    job = await _core()._get_job_for_run(db, run, row.media_job_id if row else "")
    if not row or row.media_job_id != job.id:
        raise AppError("MEDIA_REVIEW_ANNOTATION_NOT_FOUND", 404)
    row.status = "resolved"
    row.resolved_by = _user_id(user)
    row.updated_at = now_bjt()
    return {"annotation": _serialize_annotation(row)}


def _visual_policy_gate_state(row: MediaGenerationJob) -> dict[str, Any]:
    """Expose the service-owned visual gate to review and approval flows."""

    return _core()._dialogue_visual_policy_gate_state(row)


def dialogue_delivery_gate(row: MediaGenerationJob) -> dict[str, Any]:
    """Prove that requested dialogue reached the collected final asset.

    H3 is deliberately asked for a silent clean plate when the brief contains
    dialogue: letting the video model render speech repeatedly produced burned
    caption pixels.  A later governed renderer must therefore record its
    immutable delivery result and the collected asset must contain an audio
    stream before a reviewer can approve it.
    """

    prompt = _dict(row.prompt_json)
    prompt_delivery = _dict(prompt.get("dialogue_delivery"))
    # v20 writes an explicit delivery contract.  Older production jobs still
    # carry the fitted business line in script_timing/requested_audio_prompt;
    # infer the same hard gate so a historical H3 AAC stream cannot be
    # mistaken for a governed, correctly delivered voiceover.
    legacy_requested_dialogue = _text(
        prompt.get("requested_audio_prompt")
        or _dict(prompt.get("script_timing")).get("shot_script"),
        6000,
    )
    requested = (
        prompt_delivery.get("requested") is True
        if "requested" in prompt_delivery
        else bool(legacy_requested_dialogue)
    )
    if not requested:
        return {
            "required": False,
            "passed": True,
            "status": "not_required",
            "label": "无需对白交付",
        }
    result = _dict(row.result_json)
    result_delivery = _dict(result.get("dialogue_delivery"))
    delivery = {**prompt_delivery, **result_delivery}
    clean_plate_asset_id = _text(_dict(result.get("clean_plate_asset")).get("id"), 50)
    clean_plate_available = bool(prompt_delivery.get("requested") is True and clean_plate_asset_id)
    visual_gate = _visual_policy_gate_state(row)
    auto_finalize = bool(
        prompt_delivery.get("auto_finalize") is True
        and prompt_delivery.get("delivery_mode") == "auto_after_visual_gate"
    )
    technical = _dict(result.get("technical_validation"))
    status = _text(delivery.get("status"), 60).lower()
    auto_pending = bool(
        auto_finalize
        and status in {"voiceover_renderer_required", "awaiting_visual_gate", "auto_rendering", "rendering"}
    )
    retryable = bool(
        clean_plate_available
        and visual_gate.get("passed") is True
        and (not auto_pending or status in {"failed", "configuration_required"})
    )
    transcription = _dict(delivery.get("transcription"))
    transcription_passed = bool(
        delivery.get("policy_version") == _core().SPEECH_DELIVERY_POLICY_VERSION
        and transcription.get("passed") is True
    )
    renderer = _text(delivery.get("renderer") or delivery.get("renderer_id"), 180)
    artifact_id = _text(
        delivery.get("artifact_id") or delivery.get("audio_asset_id") or delivery.get("final_asset_id"),
        180,
    )
    has_audio_stream = bool(_text(technical.get("audio_codec"), 60))
    passed = bool(
        status in {"completed", "delivered"}
        and renderer
        and artifact_id
        and has_audio_stream
        and transcription_passed
    )
    gate_status = status or "voiceover_renderer_required"
    if status in {"completed", "delivered"} and has_audio_stream and not transcription_passed:
        gate_status = "transcription_required"
    transcription_reason = _text(transcription.get("error"), 500)
    if not transcription_reason and transcription.get("status") == "failed":
        transcription_reason = (
            f"对白转写与原台词未通过对齐（相似度 {float(transcription.get('similarity') or 0):.0%}，"
            f"覆盖率 {float(transcription.get('coverage') or 0):.0%}）。"
        )
    return {
        "required": True,
        "passed": passed,
        "status": gate_status,
        "label": (
            "对白已核验"
            if passed
            else "对白待台词校验"
            if gate_status == "transcription_required"
            else "对白自动处理中"
            if auto_pending
            else "对白待独立配音"
        ),
        "renderer": renderer or None,
        "artifact_id": artifact_id or None,
        "has_audio_stream": has_audio_stream,
        "transcription": {
            "passed": transcription_passed,
            "status": _text(transcription.get("status"), 60) or "not_run",
            "provider": _text(transcription.get("provider"), 80) or None,
            "model": _text(transcription.get("model"), 180) or None,
            "similarity": transcription.get("similarity"),
            "coverage": transcription.get("coverage"),
            "transcript": _text(transcription.get("transcript"), 6000) or None,
            "trace_id": _text(transcription.get("trace_id"), 180) or None,
        },
        "retryable": retryable,
        "reason": None if passed else (
            (
                (transcription_reason + " ") if transcription_reason else ""
            )
            + "成片已有音轨，但尚未通过受控语音识别与原台词对齐；校验只复用现有音频，不重复生成 H3 视频。"
            if gate_status == "transcription_required"
            else "画面门禁通过后系统将自动生成受控配音、合成音轨并校验台词，无需审片人手动启动。"
            if auto_pending and visual_gate.get("passed") is True
            else "对白任务必须由受控配音执行器完成，并在最终成片中验证到音频流。"
            if retryable
            else "无字静音底片检测到烧字或其它画面硬门禁问题，必须先返回生产重生成，禁止在坏底片上继续配音。"
            if clean_plate_available and visual_gate.get("status") == "failed"
            else "无字静音底片仍在完成画面硬门禁检测，检测通过后才能执行独立配音。"
            if clean_plate_available and visual_gate.get("status") == "pending"
            else "历史任务没有 v20 无字静音底片血缘，必须返回生产重新生成，不能直接把旧 H3 成片冒充配音底片。"
        ),
        "visual_policy_gate": visual_gate,
    }


async def retry_dialogue_delivery(
    db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    """Retry only the governed voice/mux stage, never the H3 clean plate."""

    candidate = await _core()._get_job_for_run(db, run, payload.get("job_id"))
    # The provider call is billable. Serialize retries at the durable job row
    # so double-clicks, multiple review tabs and automatic collection cannot
    # render the same dialogue twice concurrently.
    job = (
        await db.execute(
            select(MediaGenerationJob)
            .where(MediaGenerationJob.id == candidate.id)
            .with_for_update()
        )
    ).scalar_one()
    if job.status not in {"awaiting_review", "rejected"} or not job.result_asset_id:
        raise AppError("MEDIA_DIALOGUE_DELIVERY_NOT_READY", 422, {"job_id": job.id})
    gate = dialogue_delivery_gate(job)
    if not gate.get("required"):
        raise AppError("MEDIA_DIALOGUE_DELIVERY_NOT_REQUIRED", 422, {"job_id": job.id})
    if gate.get("passed"):
        return {"job": _core().serialize_job(job, await _core()._latest_attempt(db, job.id)), "dialogue_delivery_gate": gate}
    if not gate.get("retryable"):
        raise AppError(
            "MEDIA_DIALOGUE_CLEAN_PLATE_REQUIRED",
            422,
            {"job_id": job.id, "detail": gate.get("reason")},
        )
    result = _dict(job.result_json)
    clean_plate_asset_id = _text(_dict(result.get("clean_plate_asset")).get("id"), 50)
    clean_plate_asset = await db.get(ProjectRunAsset, clean_plate_asset_id)
    if clean_plate_asset is None or clean_plate_asset.project_id != job.project_id:
        raise AppError("MEDIA_DIALOGUE_CLEAN_PLATE_MISSING", 422, {"job_id": job.id})
    asset_run = await db.get(ProjectRun, job.project_run_id)
    if asset_run is None or asset_run.project_id != job.project_id:
        raise AppError("MEDIA_DIALOGUE_DELIVERY_RUN_MISSING", 422, {"job_id": job.id})
    try:
        completed = await _core()._finalize_governed_dialogue(db, user, asset_run, job, clean_plate_asset)
    except Exception as exc:  # noqa: BLE001
        _core()._record_dialogue_delivery_failure(job, exc)
        code = getattr(exc, "code", "SPEECH_DELIVERY_FAILED")
        raise AppError(
            code,
            503 if code in {"SPEECH_PROVIDER_FAILED", "SPEECH_RENDERER_DISABLED", "SPEECH_CONFIG_MISSING"} else 422,
            {"detail": str(exc)[:800], "job_id": job.id, "clean_plate_reused": True},
        ) from exc
    completed_gate = dialogue_delivery_gate(job)
    if not completed_gate.get("passed"):
        raise AppError(
            "MEDIA_DIALOGUE_DELIVERY_INCOMPLETE",
            422,
            {"job_id": job.id, "detail": completed_gate.get("reason")},
        )
    job.result_json, _ = _core()._queue_media_quality_analysis(
        job.result_json,
        requested_by=job.requested_by,
        automatic=True,
        force=True,
    )
    asset = await db.get(ProjectRunAsset, job.result_asset_id) if job.result_asset_id else None
    await _ensure_review_media_manifest(db, user, asset_run, job, asset)
    await db.flush()
    return {
        "job": _core().serialize_job(job, await _core()._latest_attempt(db, job.id)),
        "dialogue_delivery": completed,
        "dialogue_delivery_gate": completed_gate,
        "clean_plate_reused": True,
    }


async def hard_review_gate(db: AsyncSession, row: MediaGenerationJob, payload: dict[str, Any]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    result = _dict(row.result_json)
    technical = _dict(result.get("technical_validation"))
    if technical.get("status") not in {"passed", "ok", "valid"} and technical.get("passed") is not True:
        missing.append("technical_validation.passed")
    rights = {**_dict(row.rights_json), **_dict(payload.get("rights"))}
    for key, expected in {
        "copyright_authorized": True, "malware_scan": "passed", "sensitive_data_scan": "passed", "redaction": "passed",
    }.items():
        if rights.get(key) != expected:
            missing.append(f"rights.{key}")
    has_voice = bool(rights.get("contains_voice")) or any(item.get("kind") == "audio" for item in _list(row.reference_assets_json))
    if has_voice and rights.get("voice_authorized") is not True:
        missing.append("rights.voice_authorized")
    if bool(rights.get("contains_person")) and rights.get("portrait_authorized") is not True:
        missing.append("rights.portrait_authorized")
    compliance = _dict(payload.get("compliance"))
    if compliance.get("adult_audience_confirmed") is not True:
        missing.append("compliance.adult_audience_confirmed")
    brief_text = json.dumps(row.prompt_json or {}, ensure_ascii=False)
    guardrails = _dict(_dict(row.prompt_json).get("brand_guardrails"))
    commercial_claim_present = bool(guardrails.get("commercial_claim_verification_required")) or any(
        token in brief_text for token in ("两位数到手", "活动价", "折扣价", "到手价", "优惠券", "￥", "¥")
    )
    if commercial_claim_present and compliance.get("commercial_claim_verified") is not True:
        missing.append("compliance.commercial_claim_verified")
    if not dialogue_delivery_gate(row)["passed"]:
        missing.append("dialogue_delivery.completed")
    visual_gate = _visual_policy_gate_state(row)
    if visual_gate.get("required") is True:
        if visual_gate.get("status") == "pending":
            missing.append("visual_policy_gate.completed")
        elif visual_gate.get("passed") is not True:
            missing.append("visual_policy_gate.passed")
            failed_codes = list(dict.fromkeys(
                _text(finding.get("code"), 80)
                for finding in _list(visual_gate.get("findings"))
                if isinstance(finding, dict) and _text(finding.get("code"), 80)
            ))
            missing.extend(f"visual_policy_gate.{code}" for code in failed_codes)
    critical = 0
    try:
        critical = int((await db.execute(select(func.count(MediaReviewAnnotation.id)).where(
            MediaReviewAnnotation.media_job_id == row.id,
            MediaReviewAnnotation.status == "open",
            MediaReviewAnnotation.severity == "critical",
        ))).scalar() or 0)
    except AttributeError:
        critical = 0
    if critical:
        missing.append("review_annotations.critical_resolved")
    return not missing, missing


async def batch_reject(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    job_ids = list(dict.fromkeys(_text(item, 50) for item in _list(payload.get("job_ids")) if _text(item, 50)))
    if not job_ids or len(job_ids) > 100:
        raise AppError("MEDIA_REVIEW_BATCH_INVALID", 422)
    reason = _text(payload.get("reason"), 2000)
    if not reason:
        raise AppError("MEDIA_REVIEW_BATCH_INVALID", 422, {"field": "reason"})
    jobs = []
    for job_id in job_ids:
        job = await _core()._get_job_for_run(db, run, job_id)
        if job.status != "awaiting_review":
            continue
        result = await _core()._review_job(db, user, run, {"job_id": job.id, "decision": "rejected", "reason": reason})
        jobs.append(result["job"])
    return {"jobs": jobs, "rejected_count": len(jobs), "batch_approve_supported": False}


async def batch_update_review(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    """Assign or tag review work without changing an approval decision."""

    job_ids = list(dict.fromkeys(_text(item, 50) for item in _list(payload.get("job_ids")) if _text(item, 50)))
    if not job_ids or len(job_ids) > 100:
        raise AppError("MEDIA_REVIEW_BATCH_INVALID", 422)
    assignee_supplied = "assignee_id" in payload
    assignee_id = _text(payload.get("assignee_id"), 50) or None
    tags_supplied = "tags" in payload
    tags = list(dict.fromkeys(_text(item, 60) for item in _list(payload.get("tags")) if _text(item, 60)))[:20]
    if not assignee_supplied and not tags_supplied:
        raise AppError("MEDIA_REVIEW_BATCH_INVALID", 422, {"detail": "assignee_id or tags is required"})
    updated = []
    for job_id in job_ids:
        job = await _core()._get_job_for_run(db, run, job_id)
        if job.status not in {"awaiting_review", "rejected"}:
            continue
        if assignee_supplied:
            job.review_assignee_id = assignee_id
        if tags_supplied:
            job.review_tags_json = tags
        job.review_json = {
            **_dict(job.review_json),
            "review_queue_updated_by": _user_id(user),
            "review_queue_updated_at": isoformat_bjt(now_bjt()),
        }
        job.updated_at = now_bjt()
        updated.append(_core().serialize_job(job, await _core()._latest_attempt(db, job.id)))
    return {"jobs": updated, "updated_count": len(updated), "approval_changed": False}


async def _review_asset(db: AsyncSession, job: MediaGenerationJob) -> ProjectRunAsset | None:
    return await db.get(ProjectRunAsset, job.result_asset_id) if job.result_asset_id else None


def _serialize_review_asset(asset: ProjectRunAsset | None) -> dict[str, Any] | None:
    """Deliver a project-wide review asset without widening ProjectRun access.

    Review queues are intentionally shared inside the project department, but
    the immutable media may belong to another user's ProjectRun.  Returning the
    normal authenticated run download URL makes read-only reviewers see the
    queue metadata while every poster/video request is rejected by the stricter
    ProjectRun boundary.  A short-lived signed URL preserves that boundary and
    grants only the exact immutable asset already exposed by an authorized
    review capability call.
    """

    if asset is None:
        return None
    row = project_service.serialize_run_asset(asset)
    row["download_url"] = project_service._project_run_asset_signed_download_url(asset)  # noqa: SLF001
    row["delivery"] = "signed_project_review"
    return row


def _review_manifest_delivery_entry(
    value: Any, assets: dict[str, ProjectRunAsset], *, project_id: str
) -> dict[str, Any]:
    row = _dict(value)
    # Never keep the historical owner-scoped /download URL in a department
    # review response.  It is unusable for observers and can also survive in
    # the browser session cache after permissions change.
    row.pop("download_url", None)
    asset_id = _text(row.get("asset_id") or row.get("id"), 50)
    asset = assets.get(asset_id or "")
    if asset is None or asset.project_id != project_id:
        return row
    delivered = _serialize_review_asset(asset) or {}
    return {
        **row,
        "asset_id": asset.id,
        "download_url": delivered.get("download_url"),
        "mime_type": asset.mime_type,
        "sha256": asset.sha256,
        "delivery": delivered.get("delivery"),
    }


async def _ensure_review_preview_proxy(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    job: MediaGenerationJob,
    asset: ProjectRunAsset,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one immutable 720p fast-start proxy for wall hover and review."""

    current = _dict(existing)
    proxy_id = _text(current.get("asset_id"), 50)
    if proxy_id:
        proxy_asset = await db.get(ProjectRunAsset, proxy_id)
        if proxy_asset is not None and proxy_asset.sha256 == current.get("sha256"):
            return _serialize_review_asset(proxy_asset) or {}
    ffmpeg = _core()._media_quality_ffmpeg_executable()
    source_path = project_service._project_run_asset_abs_path(asset)
    with tempfile.TemporaryDirectory(prefix="sf-review-proxy-") as temp_dir:
        output = Path(temp_dir) / "review-proxy.mp4"

        def transcode() -> tuple[int, str]:
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source_path),
                "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-y", str(output),
            ]
            completed = subprocess.run(command, capture_output=True, timeout=360)
            return completed.returncode, completed.stderr.decode("utf-8", errors="replace")

        return_code, error = await asyncio.to_thread(transcode)
        if return_code != 0 or not output.exists() or output.stat().st_size <= 0:
            raise RuntimeError(f"review proxy transcode failed: {error[-800:]}")
        uploaded = await project_service.upload_project_run_asset(
            db,
            user,
            run.id,
            file_name=f"{job.id}-review-720p.mp4",
            mime_type="video/mp4",
            content=output.read_bytes(),
            metadata={
                "source": "media_review_proxy",
                "media_job_id": job.id,
                "source_asset_id": asset.id,
                "source_sha256": asset.sha256,
                "profile": "h264-aac-yuv420p-faststart-720p-v1",
            },
        )
    proxy_asset = await db.get(ProjectRunAsset, _text(_dict(uploaded.get("asset")).get("id"), 50))
    if proxy_asset is None:
        raise RuntimeError("review proxy upload returned no asset")
    return _serialize_review_asset(proxy_asset) or {}


def _media_provenance_sidecar(
    row: MediaGenerationJob,
    asset: ProjectRunAsset | None,
    reference_rows: list[Any],
    reference_assets: dict[str, ProjectRunAsset],
    attempt: MediaGenerationAttempt | None,
) -> dict[str, Any]:
    """Build a deterministic, unsigned production sidecar for asset handoff.

    The structure follows the creation/edit/ingredient vocabulary used by
    Content Credentials, but it is deliberately not presented as an embedded
    or cryptographically signed C2PA manifest. A future signer can consume this
    bounded record without reconstructing provenance from UI text or logs.
    """

    prompt = _dict(row.prompt_json)
    result = _dict(row.result_json)
    params = _dict(row.params_json)
    asset_metadata = _dict(getattr(asset, "metadata_json", None))
    postprocess_provenance = _dict(asset_metadata.get("postprocess_provenance"))
    ingredients = []
    for raw in reference_rows:
        reference = _dict(raw)
        asset_id = _text(reference.get("asset_id"), 50)
        source = reference_assets.get(asset_id or "")
        ingredients.append({
            "asset_id": asset_id,
            "sha256": getattr(source, "sha256", None),
            "mime_type": getattr(source, "mime_type", None),
            "role": _text(reference.get("role"), 40),
            "purpose": _text(reference.get("purpose"), 120),
        })
    compiled_prompt = _text(
        prompt.get("integrated_multimodal_description") or prompt.get("video_prompt"),
        H3_PROMPT_MAX_CHARS,
    )
    is_edit = bool(
        ingredients
        or row.cloned_from_job_id
        or row.depends_on_job_id
        or row.continuation_chain_id
        or row.mode == "video_enhance"
    )
    body = {
        "schema_version": "skillforge.media.provenance.v1",
        "intent": "edit" if is_edit else "create",
        "content_credentials": {
            "status": "unsigned_sidecar",
            "embedded": False,
            "reason": "C2PA signer is not configured; verify the SHA256-bound SkillForge audit trail",
        },
        "job": {
            "id": row.id,
            "project_id": row.project_id,
            "department_id": row.department_id,
            "mode": row.mode,
            "created_at": isoformat_bjt(row.created_at),
            "completed_at": isoformat_bjt(row.completed_at),
        },
        "output": {
            "asset_id": getattr(asset, "id", None),
            "sha256": getattr(asset, "sha256", None) or row.result_sha256,
            "mime_type": getattr(asset, "mime_type", None),
        },
        "generator": {
            "prompt_policy_version": _text(prompt.get("prompt_policy_version"), 80),
            "compiled_prompt_sha256": _text(
                _dict(result.get("training_lineage")).get("compiled_prompt_sha256"), 64
            ) or (hashlib.sha256(compiled_prompt.encode("utf-8")).hexdigest() if compiled_prompt else None),
            "workflow_template_id": row.workflow_template_id,
            "workflow_version": row.workflow_version,
            "model_version": row.model_version,
            "model_sha256": row.model_sha256,
            "seed": params.get("seed"),
            "width": params.get("width"),
            "height": params.get("height"),
            "frames": params.get("frames"),
            "fps": params.get("fps"),
            "steps": params.get("steps"),
        },
        "execution": {
            "instance_id": getattr(attempt, "instance_id", None),
            "attempt_no": getattr(attempt, "attempt_no", None),
            "started_at": isoformat_bjt(getattr(attempt, "started_at", None)),
            "completed_at": isoformat_bjt(getattr(attempt, "completed_at", None)),
        },
        "postprocess": postprocess_provenance,
        "ingredients": ingredients,
        "lineage": {
            "cloned_from_job_id": row.cloned_from_job_id,
            "depends_on_job_id": row.depends_on_job_id,
            "continuation_chain_id": row.continuation_chain_id,
            "production_batch_id": row.production_batch_id,
            "strategy_session_id": getattr(row, "strategy_session_id", None),
            "strategy_direction_id": getattr(row, "strategy_direction_id", None),
            "replay_project_id": getattr(row, "replay_project_id", None),
            "replay_segment_id": getattr(row, "replay_segment_id", None),
            "prompt_preview_sha256": getattr(row, "prompt_preview_sha256", None),
            "execution_prompt_sha256": getattr(row, "execution_prompt_sha256", None),
            "prompt_hash_match": bool(
                getattr(row, "prompt_preview_sha256", None)
                and getattr(row, "execution_prompt_sha256", None)
                and getattr(row, "prompt_preview_sha256", None) == getattr(row, "execution_prompt_sha256", None)
            ),
        },
    }
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**body, "fingerprint_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


async def _ensure_review_media_manifest(
    db: AsyncSession, user: User, run: ProjectRun, job: MediaGenerationJob, asset: ProjectRunAsset | None
) -> dict[str, Any]:
    existing = _dict(_dict(job.result_json).get("review_manifest"))
    if existing.get("source_sha256") == getattr(asset, "sha256", None) and existing.get("status") == "ready":
        poster = _dict(existing.get("poster")) or _dict(next(iter(_list(existing.get("keyframes"))), {}))
        if poster.get("asset_id") and not job.poster_asset_id:
            job.poster_asset_id = _text(poster.get("asset_id"), 50) or None
            existing = {**existing, "poster": poster, "poster_strategy": existing.get("poster_strategy") or "first_existing_keyframe"}
            job.result_json = {**_dict(job.result_json), "review_manifest": existing}
        if poster.get("asset_id"):
            if asset is not None and not _dict(existing.get("preview")).get("asset_id"):
                try:
                    preview = await _ensure_review_preview_proxy(db, user, run, job, asset)
                    existing = {**existing, "preview": preview, "preview_status": "ready", "preview_error": None}
                except Exception as exc:  # noqa: BLE001
                    existing = {**existing, "preview_status": "failed", "preview_error": _text(exc, 800)}
                job.result_json = {**_dict(job.result_json), "review_manifest": existing}
                job.updated_at = now_bjt()
                await db.flush()
            return existing
    if asset is None:
        return {"status": "missing_result"}
    ffmpeg = _core()._media_quality_ffmpeg_executable()
    source_path = project_service._project_run_asset_abs_path(asset)
    media_probe = _dict(_dict(asset.metadata_json).get("media_probe"))
    try:
        duration_seconds = max(1.0, float(media_probe.get("duration_seconds") or media_probe.get("duration") or 5))
    except (TypeError, ValueError):
        duration_seconds = 5.0
    frame_interval = max(0.5, duration_seconds / 5)
    with tempfile.TemporaryDirectory(prefix="sf-review-manifest-") as temp_dir:
        frame_pattern = Path(temp_dir) / "frame-%02d.jpg"

        def extract_review_media() -> tuple[list[Path], list[float]]:
            frame_result = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.2", "-i", str(source_path),
                    "-vf", f"blackframe=amount=95:threshold=32,metadata=select:key=lavfi.blackframe.pblack:value=95:function=less,fps=1/{frame_interval:.4f},scale=320:-2",
                    "-frames:v", "5", "-q:v", "4", "-y", str(frame_pattern),
                ],
                capture_output=True, timeout=180,
            )
            if frame_result.returncode != 0 or not list(Path(temp_dir).glob("frame-*.jpg")):
                frame_result = subprocess.run(
                    [
                        ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.2", "-i", str(source_path),
                        "-vf", f"fps=1/{frame_interval:.4f},scale=320:-2", "-frames:v", "5", "-q:v", "4", "-y", str(frame_pattern),
                    ],
                    capture_output=True, timeout=180,
                )
            frames = sorted(Path(temp_dir).glob("frame-*.jpg")) if frame_result.returncode == 0 else []
            audio_result = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source_path), "-vn", "-ac", "1", "-ar", "1000", "-f", "s16le", "pipe:1"],
                capture_output=True, timeout=180,
            )
            waveform: list[float] = []
            raw = audio_result.stdout if audio_result.returncode == 0 else b""
            if raw:
                values = [int.from_bytes(raw[index:index + 2], "little", signed=True) for index in range(0, len(raw) - 1, 2)]
                block = max(1, len(values) // 240)
                waveform = [round(max(abs(item) for item in values[index:index + block]) / 32768, 4) for index in range(0, len(values), block)][:240]
            return frames, waveform

        frames, waveform = await asyncio.to_thread(extract_review_media)
        keyframes = []
        for index, frame in enumerate(frames):
            uploaded = await project_service.upload_project_run_asset(
                db, user, run.id, file_name=f"{job.id}-review-{index + 1:02d}.jpg", mime_type="image/jpeg",
                content=frame.read_bytes(), metadata={"source": "media_review_manifest", "media_job_id": job.id, "frame_time_seconds": index},
            )
            asset_data = _dict(uploaded.get("asset"))
            # The workbench event stream is project/department scoped, while
            # upload responses are deliberately bound to the producer's
            # ProjectRun. Persist a short-lived, asset-specific review URL so
            # the completion event can paint the cover immediately for every
            # authorized reviewer. review.list/manifest will refresh expired
            # signatures; the browser caches the immutable image by SHA256.
            frame_asset = await db.get(ProjectRunAsset, _text(asset_data.get("id"), 50))
            delivered = _serialize_review_asset(frame_asset) if frame_asset else asset_data
            keyframes.append({
                "asset_id": asset_data.get("id"), "download_url": _dict(delivered).get("download_url"),
                "time_seconds": round(min(duration_seconds, 0.2 + index * frame_interval), 3), "sha256": asset_data.get("sha256"),
                "delivery": _dict(delivered).get("delivery"),
            })
    poster = keyframes[0] if keyframes else {}
    preview: dict[str, Any] = {}
    preview_status = "missing"
    preview_error = None
    try:
        preview = await _ensure_review_preview_proxy(db, user, run, job, asset)
        preview_status = "ready"
    except Exception as exc:  # noqa: BLE001
        preview_status = "failed"
        preview_error = _text(exc, 800)
    manifest = {
        "status": "ready", "source_sha256": asset.sha256, "poster": poster, "keyframes": keyframes, "waveform": waveform,
        "preview": preview, "preview_status": preview_status, "preview_error": preview_error,
        "poster_strategy": "first_non_black_representative_frame",
        "waveform_source": "ffmpeg_pcm_peak" if waveform else "audio_stream_unavailable",
        "automatic_asr": False, "generated_at": isoformat_bjt(now_bjt()),
    }
    job.poster_asset_id = _text(poster.get("asset_id"), 50) or None
    job.result_json = {**_dict(job.result_json), "review_manifest": manifest}
    job.updated_at = now_bjt()
    await db.flush()
    return manifest


async def review_manifest(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    job = await _core()._get_job_for_run(db, run, payload.get("job_id"))
    compare = await _core()._get_job_for_run(db, run, payload.get("compare_job_id")) if payload.get("compare_job_id") else None
    async def one(row: MediaGenerationJob) -> dict[str, Any]:
        asset = await _review_asset(db, row)
        latest_attempt = await _core()._latest_attempt(db, row.id)
        media_manifest = await _ensure_review_media_manifest(db, user, run, row, asset)
        media_asset_ids = {
            asset_id
            for item in [
                _dict(media_manifest.get("poster")), _dict(media_manifest.get("preview")),
                *[_dict(value) for value in _list(media_manifest.get("keyframes"))],
            ]
            if (asset_id := _text(item.get("asset_id") or item.get("id"), 50))
        }
        media_assets = {
            item.id: item
            for item in (
                await db.execute(
                    select(ProjectRunAsset).where(
                        ProjectRunAsset.id.in_(media_asset_ids),
                        ProjectRunAsset.project_id == row.project_id,
                    )
                )
            ).scalars().all()
        } if media_asset_ids else {}
        poster = _review_manifest_delivery_entry(
            media_manifest.get("poster"), media_assets, project_id=row.project_id
        )
        preview = _review_manifest_delivery_entry(
            media_manifest.get("preview"), media_assets, project_id=row.project_id
        )
        keyframes = [
            _review_manifest_delivery_entry(item, media_assets, project_id=row.project_id)
            for item in _list(media_manifest.get("keyframes"))
        ]
        annotations = (await db.execute(select(MediaReviewAnnotation).where(MediaReviewAnnotation.media_job_id == row.id).order_by(MediaReviewAnnotation.start_seconds))).scalars().all()
        reference_rows = _list(row.reference_assets_json)
        reference_ids = [_text(_dict(item).get("asset_id"), 50) for item in reference_rows if _text(_dict(item).get("asset_id"), 50)]
        reference_assets = {
            item.id: item for item in (
                await db.execute(select(ProjectRunAsset).where(ProjectRunAsset.id.in_(reference_ids)))
            ).scalars().all()
        } if reference_ids else {}
        dialogue_gate = dialogue_delivery_gate(row)
        strategy_lineage = None
        if row.strategy_session_id:
            strategy_row = await db.get(MediaCreativeStrategySession, row.strategy_session_id)
            if strategy_row is not None:
                from .workbench_v3 import _serialize_strategy

                strategy_lineage = _serialize_strategy(strategy_row)
        replay_lineage = None
        if row.replay_project_id:
            replay_row = await db.get(MediaReplayProject, row.replay_project_id)
            if replay_row is not None:
                replay_segments = (
                    await db.execute(
                        select(MediaReplaySegment)
                        .where(MediaReplaySegment.replay_project_id == replay_row.id)
                        .order_by(MediaReplaySegment.segment_index.asc())
                    )
                ).scalars().all()
                from .workbench_v3 import _serialize_replay

                replay_lineage = _serialize_replay(replay_row, list(replay_segments))
        return {
            "job": _core().serialize_job(row, latest_attempt),
            "asset": _serialize_review_asset(asset),
            "preview": preview,
            "poster": poster,
            "keyframes": keyframes,
            "waveform": _list(media_manifest.get("waveform")),
            "quality": _dict(_dict(row.result_json).get("quality_analysis")),
            "technical_validation": _dict(_dict(row.result_json).get("technical_validation")),
            "dialogue_delivery_gate": dialogue_gate,
            "annotations": [_serialize_annotation(item) for item in annotations],
            "sources": [
                {
                    **_dict(item),
                    "asset": _serialize_review_asset(reference_assets.get(_text(_dict(item).get("asset_id"), 50)))
                    if reference_assets.get(_text(_dict(item).get("asset_id"), 50)) else None,
                }
                for item in reference_rows
            ],
            "provenance": _media_provenance_sidecar(
                row, asset, reference_rows, reference_assets, latest_attempt
            ),
            "lineage": {
                "cloned_from_job_id": row.cloned_from_job_id,
                "plan_comparison_id": row.plan_comparison_id, "production_batch_id": row.production_batch_id,
                "workflow_definition_id": row.workflow_definition_id,
                "workflow_definition_version": row.workflow_definition_version,
                "continuation_chain_id": row.continuation_chain_id, "depends_on_job_id": row.depends_on_job_id,
                "model_version": row.model_version, "model_sha256": row.model_sha256,
                "workflow_version": row.workflow_version,
                "material_origin": (
                    "original_text" if row.creative_option == "direct_original"
                    else "ai_strategy" if row.creative_option == "ai_strategy"
                    else "full_structure_replay" if row.creative_option == "full_structure_replay"
                    else "local_replacement" if row.creative_option == "local_replacement"
                    else row.creative_option
                ),
                "strategy_session_id": row.strategy_session_id,
                "strategy_direction_id": row.strategy_direction_id,
                "strategy": strategy_lineage,
                "replay_project_id": row.replay_project_id,
                "replay_segment_id": row.replay_segment_id,
                "replay": replay_lineage,
                "prompt_preview_sha256": row.prompt_preview_sha256,
                "execution_prompt_sha256": row.execution_prompt_sha256,
                "prompt_hash_match": bool(
                    row.prompt_preview_sha256
                    and row.execution_prompt_sha256
                    and row.prompt_preview_sha256 == row.execution_prompt_sha256
                ),
                "production_policy": _dict(_dict(row.result_json).get("production_policy")),
                "routing_decision": _dict(_dict(row.result_json).get("routing_decision")),
            },
        }
    primary = await one(job)
    compare_payload = await one(compare) if compare else None
    transcription = _dict(_dict(primary.get("dialogue_delivery_gate")).get("transcription"))
    return {
        "primary": primary,
        "compare": compare_payload,
        "audio_analysis": {
            "waveform": True,
            "automatic_asr": transcription.get("passed") is True,
            "transcription": transcription,
            "requires_human_listening": True,
            "human_review_scope": "音色、情绪、停顿自然度与人物口型观感",
        },
    }


def _sha256_bound_bridge_probe(result_json: Any, asset_sha256: str) -> dict[str, Any]:
    result = _dict(result_json)
    for candidate in [result, *[_dict(item) for item in _list(result.get("results"))]]:
        if _text(candidate.get("sha256"), 64) != _text(asset_sha256, 64):
            continue
        candidate_probe = _dict(candidate.get("media_probe"))
        candidate_streams = [item for item in _list(candidate_probe.get("streams")) if isinstance(item, dict)]
        if candidate_probe.get("status") == "ok" and any(
            item.get("codec_type") == "video" and item.get("width") and item.get("height")
            for item in candidate_streams
        ):
            return candidate_probe
    return {}


async def submit_video_enhancement(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    """Create an auditable 1080p derivative that returns to the normal review queue."""

    source_job = await _core()._get_job_for_run(db, run, payload.get("source_job_id"))
    if not source_job.result_asset_id:
        raise AppError("MEDIA_RESULT_NOT_READY", 422, {"job_id": source_job.id})
    source_asset = await db.get(ProjectRunAsset, source_job.result_asset_id)
    if source_asset is None or not str(source_asset.mime_type or "").lower().startswith("video/"):
        raise AppError("MEDIA_RESULT_NOT_READY", 422, {"job_id": source_job.id})
    if _text(payload.get("target"), 30).lower() not in {"", "1080p", "full_hd"}:
        raise AppError("MEDIA_ENHANCEMENT_TARGET_INVALID", 422, {"target": payload.get("target")})

    metadata = _dict(source_asset.metadata_json)
    probe = _dict(metadata.get("media_probe"))
    if probe.get("status") != "ok":
        probe = await asyncio.to_thread(
            project_service._probe_project_media_file,
            project_service._project_run_asset_abs_path(source_asset),
            source_asset.mime_type,
        )
        if probe:
            source_asset.metadata_json = {**metadata, "media_probe": probe}
            await db.flush()
    streams = [item for item in _list(probe.get("streams")) if isinstance(item, dict)]
    if not any(item.get("codec_type") == "video" and item.get("width") and item.get("height") for item in streams):
        # Historical assets can have a server MP4-parser probe with a verified
        # duration but no stream dimensions.  The original Bridge result keeps
        # the full probe and is bound to the same output SHA256, so it is a
        # stronger source than guessing from UI labels or accepting client data.
        candidate_probe = _sha256_bound_bridge_probe(source_job.result_json, source_asset.sha256)
        if candidate_probe:
            probe = candidate_probe
            streams = [item for item in _list(probe.get("streams")) if isinstance(item, dict)]
            source_asset.metadata_json = {
                **_dict(source_asset.metadata_json),
                "media_probe": probe,
                "media_probe_recovered_from": "sha256_bound_bridge_result",
            }
            await db.flush()
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio_enabled = any(item.get("codec_type") == "audio" for item in streams)
    source_width = int(video_stream.get("width") or 0)
    source_height = int(video_stream.get("height") or 0)
    try:
        duration = float(probe.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    raw_fps = _text(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"), 30)
    try:
        numerator, denominator = (raw_fps.split("/", 1) + ["1"])[:2]
        fps = float(numerator) / max(float(denominator), 1.0)
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 24.0
    fps = max(12, min(30, round(fps or 24)))
    if not source_width or not source_height or duration < 2 or duration > 300:
        raise AppError("MEDIA_RESULT_INVALID", 422, {"detail": "源视频缺少可验证的尺寸或时长"})
    width, height = (1080, 1920) if source_height >= source_width else (1920, 1080)
    frames = max(21, min(9000, round(duration * fps)))
    idempotency_key = _text(payload.get("idempotency_key"), 128) or (
        f"enhance:realesrgan-x4-1080p-v1:{source_job.id}:{source_asset.sha256}"
    )[:128]
    existing = (
        await db.execute(
            select(MediaGenerationJob)
            .where(MediaGenerationJob.project_id == project.id, MediaGenerationJob.idempotency_key == idempotency_key)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        return {"deduped": True, "job": _core().serialize_job(existing, await _core()._latest_attempt(db, existing.id))}

    params = {
        "width": width, "height": height, "frames": frames, "fps": fps, "steps": 4,
        "batch_count": 1, "seed": 0, "audio_enabled": audio_enabled,
        "enhancement_profile": "realesrgan_x4_then_lanczos_1080p_v1",
    }
    title = _text(f"{source_job.business_title or source_job.id} · 1080p 高清版", 240)
    now = now_bjt()
    row = MediaGenerationJob(
        id=_new_id("mvj"), project_id=project.id, project_run_id=run.id,
        cloned_from_job_id=source_job.id, idempotency_key=idempotency_key,
        department_id=run.department_id or project.department_id,
        department=run.department or project.department,
        requested_by=_user_id(user), mode="video_enhance", status="queued",
        priority=_core()._media_job_priority(
            department_id=run.department_id or project.department_id, mode="video_enhance", params=params,
        ),
        prompt_json={
            "creative_goal": "在不改变原片内容、时序、人物、字幕和声音的前提下提升到 1080p，供线上审片 A/B 对比。",
            "enhancement_only": True,
            "source_job_id": source_job.id,
            "source_asset_sha256": source_asset.sha256,
        },
        params_json=params,
        reference_assets_json=[{
            "asset_id": source_asset.id, "role": "reference_video", "business_role": "enhancement_source",
            "purpose": "只做逐帧清晰化和 1080p 缩放；保留原时序、画面内容和音频。",
        }],
        workflow_template_id="video_upscale_realesrgan_v1",
        result_json={
            "enhancement": {
                "source_job_id": source_job.id, "source_asset_id": source_asset.id,
                "source_sha256": source_asset.sha256, "source_resolution": [source_width, source_height],
                "target_resolution": [width, height], "profile": "realesrgan-x4-1080p-v1",
            }
        },
        business_title=title, output_preset_id="full_hd_1080p",
        source_roles_json=[{"asset_id": source_asset.id, "role": "enhancement_source", "technical_role": "reference_video"}],
        creative_option="enhance_1080p", job_group_id=source_job.job_group_id or source_job.id,
        rights_json=dict(_dict(source_job.rights_json)), training_eligibility="pending",
        created_at=now, updated_at=now,
    )
    db.add(row)
    await db.flush()
    await _core()._dispatch_job(db, run, row)
    return {"deduped": False, "job": _core().serialize_job(row, await _core()._latest_attempt(db, row.id))}


async def backfill_review_manifests_once(db: AsyncSession, *, limit: int = 1) -> dict[str, int]:
    """Generate historical review posters in the background, never in list latency."""

    manifest_status = MediaGenerationJob.result_json["review_manifest"]["status"].astext
    preview_asset_id = MediaGenerationJob.result_json["review_manifest"]["preview"]["asset_id"].astext
    rows = (
        await db.execute(
            select(MediaGenerationJob)
            .where(
                MediaGenerationJob.status.in_(["awaiting_review", "approved", "rejected", "syncing", "synced"]),
                MediaGenerationJob.result_asset_id.is_not(None),
                or_(MediaGenerationJob.poster_asset_id.is_(None), preview_asset_id.is_(None)),
                or_(manifest_status.is_(None), manifest_status.in_(["pending", "ready"])),
            )
            .order_by(MediaGenerationJob.updated_at.desc())
            .with_for_update(skip_locked=True)
            .limit(max(1, min(int(limit), 2)))
        )
    ).scalars().all()
    stats = {"scanned": len(rows), "completed": 0, "failed": 0}
    for job in rows:
        try:
            run = await db.get(ProjectRun, job.project_run_id)
            user = await db.get(User, job.requested_by) if job.requested_by else None
            asset = await db.get(ProjectRunAsset, job.result_asset_id) if job.result_asset_id else None
            if run is None or user is None or asset is None:
                raise RuntimeError("审片封面缺少可追溯的项目运行、提交人或结果资产")
            await _ensure_review_media_manifest(db, user, run, job, asset)
            stats["completed"] += 1
        except Exception as exc:  # noqa: BLE001
            job.result_json = {
                **_dict(job.result_json),
                "review_manifest": {
                    "status": "failed",
                    "error": _text(exc, 1000),
                    "retry": "opening video.review.manifest retries generation",
                    "failed_at": isoformat_bjt(now_bjt()),
                },
            }
            job.updated_at = now_bjt()
            stats["failed"] += 1
        await db.commit()
    return stats


def _review_sort_time(row: MediaGenerationJob) -> datetime:
    """Use the moment a result entered review, not when its request was created."""

    return row.completed_at or row.created_at


def _encode_review_cursor(row: MediaGenerationJob) -> str:
    raw = json.dumps(
        {"review_at": isoformat_bjt(_review_sort_time(row)), "id": row.id},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_review_cursor(value: Any) -> tuple[datetime, str] | None:
    text = _text(value, 500)
    if not text:
        return None
    try:
        padding = "=" * ((4 - len(text) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode((text + padding).encode("ascii")).decode("utf-8"))
        # Accept the earlier created_at cursor during a rolling deployment.
        review_at = parse_bjt_datetime(payload.get("review_at") or payload.get("created_at"))
        row_id = _text(payload.get("id"), 50)
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppError("MEDIA_REVIEW_FILTER_INVALID", 422, {"field": "cursor"}) from exc
    if not row_id:
        raise AppError("MEDIA_REVIEW_FILTER_INVALID", 422, {"field": "cursor"})
    return review_at, row_id


async def review_list(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    status = _text(payload.get("status"), 30) or "awaiting_review"
    allowed_statuses = {"awaiting_review", "approved", "rejected", "syncing", "synced", "all"}
    if status not in allowed_statuses:
        raise AppError("MEDIA_REVIEW_FILTER_INVALID", 422, {"field": "status"})
    conditions = [MediaGenerationJob.project_id == run.project_id]
    if status != "all":
        conditions.append(MediaGenerationJob.status == status)
    for field, column in (
        ("batch_id", MediaGenerationJob.production_batch_id),
        ("node_id", MediaGenerationJob.assigned_instance_id),
        ("workflow_id", MediaGenerationJob.workflow_definition_id),
    ):
        value = _text(payload.get(field), 80)
        if value:
            conditions.append(column == value)
    search = _text(payload.get("search"), 120)
    if search:
        escaped_search = search.replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped_search}%"
        conditions.append(or_(MediaGenerationJob.business_title.ilike(pattern, escape="\\"), MediaGenerationJob.id.ilike(pattern, escape="\\")))
    total = int((await db.execute(select(func.count(MediaGenerationJob.id)).where(*conditions))).scalar() or 0)
    review_at = func.coalesce(MediaGenerationJob.completed_at, MediaGenerationJob.created_at)
    cursor = _decode_review_cursor(payload.get("cursor") or payload.get("after_cursor"))
    if cursor:
        cursor_review_at, cursor_id = cursor
        conditions.append(or_(
            review_at < cursor_review_at,
            and_(review_at == cursor_review_at, MediaGenerationJob.id < cursor_id),
        ))
    page_limit = min(max(int(payload.get("limit") or 30), 1), 60)
    rows = (
        await db.execute(
            select(MediaGenerationJob)
            .where(*conditions)
            .order_by(review_at.desc(), MediaGenerationJob.id.desc())
            .limit(page_limit)
        )
    ).scalars().all()
    poster_ids = list({
        asset_id
        for row in rows
        if (
            asset_id := _text(
                row.poster_asset_id
                or _dict(_dict(row.result_json).get("review_manifest")).get("poster", {}).get("asset_id"),
                50,
            )
        )
    })
    posters = {
        asset.id: asset for asset in (
            await db.execute(select(ProjectRunAsset).where(ProjectRunAsset.id.in_(poster_ids)))
        ).scalars().all()
    } if poster_ids else {}
    outboxes = {
        item.media_job_id: item for item in (
            await db.execute(select(CloudVideoSyncOutbox).where(CloudVideoSyncOutbox.media_job_id.in_([row.id for row in rows])))
        ).scalars().all()
    } if rows else {}
    items = []
    quality_filter = _text(payload.get("quality"), 20)
    for row in rows:
        quality = _dict(_dict(row.result_json).get("quality_analysis"))
        try:
            score = float(
                quality.get("applicable_overall_score")
                or quality.get("overall_score")
                or _dict(quality.get("scores")).get("overall_score")
                or 0
            )
        except (TypeError, ValueError):
            score = 0.0
        # Automated and manual quality pipelines both persist overall_score as
        # a normalized 0..1 value; individual dimensions remain 1..5.
        quality_level = "high" if score >= 0.8 else "medium" if score >= 0.6 else "low" if score else "pending"
        if quality_filter and quality_filter != "all" and quality_level != quality_filter:
            continue
        manifest_poster = _dict(_dict(row.result_json).get("review_manifest")).get("poster")
        manifest_poster_id = _text(_dict(manifest_poster).get("asset_id") or _dict(manifest_poster).get("id"), 50)
        poster = posters.get(row.poster_asset_id or manifest_poster_id)
        prompt = _dict(row.prompt_json)
        outbox = outboxes.get(row.id)
        items.append({
            "job": _core().serialize_job(row, None),
            "review_ready_at": isoformat_bjt(_review_sort_time(row)),
            "business_title": row.business_title or _core()._job_business_title({}, {}, prompt),
            "prompt_summary": _text(prompt.get("creative_goal") or prompt.get("video_prompt"), 240),
            "poster": (
                _review_manifest_delivery_entry(manifest_poster, {poster.id: poster}, project_id=row.project_id)
                if poster
                else _review_manifest_delivery_entry(manifest_poster, {}, project_id=row.project_id)
            ),
            "quality": {"level": quality_level, "score": score, "summary": _text(quality.get("summary") or quality.get("notes"), 500)},
            "technical_validation": _dict(_dict(row.result_json).get("technical_validation")),
            "cloud_sync": {"status": outbox.status, "remote_video_id": outbox.remote_video_id, "last_error": outbox.last_error} if outbox else None,
        })
    return {
        "items": items,
        "total": total,
        "cursor": _text(payload.get("cursor") or payload.get("after_cursor"), 500) or None,
        "next_cursor": _encode_review_cursor(rows[-1]) if len(rows) == page_limit else None,
        "has_more": len(rows) == page_limit,
        "sort": "review_ready_desc",
        "poster_backfilled": 0,
        "poster_backfill_mode": "background",
        "poster_backfill_pending": sum(1 for row in rows if row.result_asset_id and not row.poster_asset_id),
    }


async def _record_workbench_snapshot(
    db: AsyncSession, project: Project, run: ProjectRun, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Persist one monotonic, replayable event for each observable state transition."""

    fingerprint = _core()._json_hash(snapshot)
    latest = (
        await db.execute(
            select(MediaWorkbenchStreamEvent)
            .where(MediaWorkbenchStreamEvent.project_run_id == run.id)
            .order_by(MediaWorkbenchStreamEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest and latest.fingerprint == fingerprint:
        return {**snapshot, "cursor": str(latest.id), "state_fingerprint": fingerprint}

    previous_snapshot = _dict(latest.snapshot_json) if latest else {}
    event_fields = {
        "job.updated": "jobs",
        "batch.updated": "batches",
        "continuation.updated": "continuations",
        "strategy.updated": "strategies",
        "replay.updated": "replays",
        "node.updated": "nodes",
        "review.updated": "review_counts",
        "training.updated": "training_status",
        "request.updated": "request_counts",
        "candidate.updated": "candidate_counts",
        "decision.updated": "candidate_counts",
        "delivery.updated": "candidate_counts",
        "performance.updated": "north_star",
    }
    changed_events = [
        event_name for event_name, field in event_fields.items()
        if not latest or previous_snapshot.get(field) != snapshot.get(field)
    ]
    previous_cursor = int(latest.id) if latest else 0
    inserted_cursor = (
        await db.execute(
            pg_insert(MediaWorkbenchStreamEvent)
            .values(
                project_id=project.id,
                project_run_id=run.id,
                previous_cursor=previous_cursor,
                fingerprint=fingerprint,
                changed_events_json=changed_events,
                snapshot_json=snapshot,
                created_at=now_bjt(),
            )
            .on_conflict_do_nothing(
                constraint="uq_media_workbench_stream_transition"
            )
            .returning(MediaWorkbenchStreamEvent.id)
        )
    ).scalar_one_or_none()
    if inserted_cursor is None:
        inserted_cursor = (
            await db.execute(
                select(MediaWorkbenchStreamEvent.id)
                .where(
                    MediaWorkbenchStreamEvent.project_run_id == run.id,
                    MediaWorkbenchStreamEvent.previous_cursor == previous_cursor,
                    MediaWorkbenchStreamEvent.fingerprint == fingerprint,
                )
                .limit(1)
            )
        ).scalar_one()
    persisted_snapshot = {**snapshot, "cursor": str(inserted_cursor), "state_fingerprint": fingerprint}
    await db.execute(
        sql_update(MediaWorkbenchStreamEvent)
        .where(MediaWorkbenchStreamEvent.id == inserted_cursor)
        .values(snapshot_json=persisted_snapshot)
    )
    return persisted_snapshot


async def workbench_snapshot(db: AsyncSession, project: Project, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    include_recent_jobs = bool(payload.get("include_recent_jobs"))
    limit = min(max(int(payload.get("limit") or 40), 1), 60)
    jobs = (await db.execute(select(MediaGenerationJob).where(MediaGenerationJob.project_id == project.id).order_by(MediaGenerationJob.updated_at.desc()).limit(limit))).scalars().all()
    attempts = {}
    for job in jobs:
        attempts[job.id] = await _core()._latest_attempt(db, job.id)
    batches = (await db.execute(select(MediaProductionBatch).where(MediaProductionBatch.project_id == project.id).order_by(MediaProductionBatch.created_at.desc()).limit(30))).scalars().all()
    serialized_batches = [await _serialize_batch(db, batch) for batch in batches]
    continuations = (
        await db.execute(
            select(MediaContinuationChain)
            .where(MediaContinuationChain.project_id == project.id)
            .order_by(MediaContinuationChain.updated_at.desc())
            .limit(20)
        )
    ).scalars().all()
    serialized_continuations = await serialize_continuations(db, continuations)
    strategy_rows = (
        await db.execute(
            select(MediaCreativeStrategySession)
            .where(MediaCreativeStrategySession.project_run_id == run.id)
            .order_by(MediaCreativeStrategySession.updated_at.desc())
            .limit(3)
        )
    ).scalars().all()
    replay_rows = (
        await db.execute(
            select(MediaReplayProject)
            .where(MediaReplayProject.project_run_id == run.id)
            .order_by(MediaReplayProject.updated_at.desc())
            .limit(3)
        )
    ).scalars().all()
    from .workbench_v3 import _serialize_replay, _serialize_strategy

    serialized_replays = []
    for replay in replay_rows:
        replay_segments = (
            await db.execute(
                select(MediaReplaySegment)
                .where(MediaReplaySegment.replay_project_id == replay.id)
                .order_by(MediaReplaySegment.segment_index.asc())
            )
        ).scalars().all()
        serialized_replays.append(_serialize_replay(replay, list(replay_segments)))
    nodes = (await db.execute(select(OpenClawInstance).where(OpenClawInstance.is_active.is_(True), OpenClawInstance.agent_purpose.in_(["media", "mixed"])))).scalars().all()
    node_items = []
    for node in nodes:
        caps = _core()._bridge_caps(node)
        media = _dict(caps.get("media"))
        runtime = _workbench_media_node_runtime(node)
        reported_online = bool(media.get("online"))
        roles = {
            str(item)
            for item in _list(caps.get("workload_roles")) + _list(media.get("workload_roles"))
        }
        # Exposing media operation names is not enough: only show nodes that
        # completed the governed H3 bootstrap and explicitly accept video jobs.
        if not media.get("configured") or "video_generation" not in roles:
            continue
        durations = []
        rows = (await db.execute(select(MediaGenerationAttempt).where(MediaGenerationAttempt.instance_id == node.id, MediaGenerationAttempt.status == "completed").order_by(MediaGenerationAttempt.completed_at.desc()).limit(20))).scalars().all()
        for attempt in rows:
            metrics = _dict(attempt.metrics_json)
            try:
                value = float(metrics.get("total_seconds") or float(metrics.get("duration_ms")) / 1000)
            except (TypeError, ValueError):
                continue
            if value > 0:
                durations.append(value)
        media.update({
            "reported_online": reported_online,
            "online": runtime["online"],
            "offline_reason": runtime["offline_reason"],
            "heartbeat_age_seconds": runtime["heartbeat_age_seconds"],
        })
        node_items.append({
            "id": node.id, "name": node.name, **runtime,
            "last_heartbeat_at": isoformat_bjt(node.last_heartbeat),
            "purpose": node.agent_purpose, "platform_default": bool(node.is_platform_default),
            "gpu": _list(caps.get("gpu")), "media": media, "bridge_version": caps.get("bridge_version"),
            "rolling_eta_seconds": round(statistics.median(durations)) if durations else None,
            "sample_count": len(durations),
        })
    review_counts = {
        str(status): int(count) for status, count in (
            await db.execute(select(MediaGenerationJob.status, func.count(MediaGenerationJob.id)).where(MediaGenerationJob.project_id == project.id).group_by(MediaGenerationJob.status))
        ).all()
    }
    training_status = await _core()._media_training_progress(db, project)
    preset_registry = await output_presets(db, project)
    snapshot = {
        "server_time": isoformat_bjt(now_bjt()),
        "jobs": [_core().serialize_job(job, attempts[job.id]) for job in jobs],
        "batches": serialized_batches,
        "continuations": serialized_continuations,
        "strategies": [_serialize_strategy(item) for item in strategy_rows],
        "replays": serialized_replays,
        "nodes": node_items, "status_counts": review_counts,
        "review_counts": {"awaiting_review": review_counts.get("awaiting_review", 0), "rejected": review_counts.get("rejected", 0)},
        "training_status": training_status,
        "output_presets": preset_registry,
        "recommended_preset_id": preset_registry["recommended_id"],
        "recent_job_count": len(jobs),
        "refresh": {"transport": "project_media_event_stream", "polling_required": False},
    }
    # server_time is operational metadata, not an observable state transition.
    fingerprint_source = {key: value for key, value in snapshot.items() if key != "server_time"}
    if payload.get("_skip_record"):
        result = {**fingerprint_source, "server_time": snapshot["server_time"]}
    else:
        persisted = await _record_workbench_snapshot(db, project, run, fingerprint_source)
        result = {**persisted, "server_time": snapshot["server_time"]}
    if include_recent_jobs:
        return result
    return {
        key: value for key, value in result.items()
        if key not in {"jobs", "batches", "continuations"}
    }


NEW_MEDIA_CAPABILITIES = {
    "video.workbench.snapshot",
    "video.production.prepare", "video.production.compile", "video.production.submit", "video.first_frame.generate",
    "video.review.list",
    "video.cloud_reference.search", "video.cloud_reference.import",
    "video.theme.diverge",
    "video.asset.library.list", "video.asset.library.upsert", "video.asset.library.archive",
    "video.asset.group.list", "video.asset.group.get", "video.asset.group.upsert", "video.asset.group.archive",
    "video.workflow.list", "video.workflow.get", "video.workflow.upsert", "video.workflow.publish", "video.workflow.archive",
    "video.production_batch.create", "video.production_batch.list", "video.production_batch.get", "video.production_batch.update", "video.production_batch.cancel",
    "video.continuation.plan", "video.continuation.submit", "video.continuation.get", "video.continuation.cancel", "video.continuation.retry_segment",
    "video.review.manifest", "video.review.annotation.list", "video.review.annotation.save", "video.review.annotation.resolve",
    "video.dialogue_delivery.retry",
    "video.review.batch_reject", "video.review.batch_update",
    "video.enhance.submit",
}


async def dispatch_v2_capability(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, capability: str, payload: dict[str, Any]
) -> dict[str, Any]:
    if capability == "video.workbench.snapshot":
        return await workbench_snapshot(db, project, run, payload)
    if capability == "video.production.prepare":
        return await prepare_production(db, user, project, run, payload)
    if capability == "video.production.compile":
        return await compile_production_plan(payload)
    if capability == "video.production.submit":
        return await submit_production(db, user, project, run, payload)
    if capability == "video.first_frame.generate":
        return await generate_character_first_frame(db, user, project, run, payload)
    if capability == "video.review.list":
        return await review_list(db, user, run, payload)
    if capability == "video.cloud_reference.search":
        return await cloud_reference_search(db, user, run, payload)
    if capability == "video.cloud_reference.import":
        return await cloud_reference_import(db, user, project, run, payload)
    if capability == "video.theme.diverge":
        return await theme_diverge(db, user, run, payload)
    if capability.startswith("video.asset.library."):
        action = capability.rsplit(".", 1)[-1]
        if action == "list": return await list_library_assets(db, run, payload)
        if action == "upsert": return await upsert_library_asset(db, user, run, payload)
        return await archive_library_asset(db, user, run, payload)
    if capability.startswith("video.asset.group."):
        return await manage_asset_group(db, user, run, payload, capability.rsplit(".", 1)[-1])
    if capability.startswith("video.workflow."):
        return await manage_workflow(db, user, run, payload, capability.rsplit(".", 1)[-1])
    if capability == "video.production_batch.create":
        return await create_production_batch(db, user, project, run, payload)
    if capability.startswith("video.production_batch."):
        return await manage_production_batch(db, run, payload, capability.rsplit(".", 1)[-1])
    if capability == "video.continuation.plan":
        return await plan_continuation(db, user, project, run, payload)
    if capability == "video.continuation.submit":
        return await submit_continuation(db, user, project, run, payload)
    if capability == "video.continuation.get":
        chain = await db.get(MediaContinuationChain, _text(payload.get("chain_id"), 50))
        if not chain or chain.project_id != project.id: raise AppError("MEDIA_CONTINUATION_NOT_FOUND", 404)
        return {"chain": await serialize_continuation(db, chain)}
    if capability == "video.continuation.cancel":
        return await cancel_continuation(db, run, payload)
    if capability == "video.continuation.retry_segment":
        return await retry_continuation_segment(db, user, project, run, payload)
    if capability == "video.review.manifest":
        return await review_manifest(db, user, run, payload)
    if capability == "video.dialogue_delivery.retry":
        return await retry_dialogue_delivery(db, user, run, payload)
    if capability == "video.enhance.submit":
        return await submit_video_enhancement(db, user, project, run, payload)
    if capability == "video.review.annotation.list":
        return await list_annotations(db, run, payload)
    if capability == "video.review.annotation.save":
        return await save_annotation(db, user, run, payload)
    if capability == "video.review.annotation.resolve":
        return await resolve_annotation(db, user, run, payload)
    if capability == "video.review.batch_reject":
        return await batch_reject(db, user, run, payload)
    if capability == "video.review.batch_update":
        return await batch_update_review(db, user, run, payload)
    raise AppError("PROJECT_CAPABILITY_DENIED", 400, {"capability": capability})
