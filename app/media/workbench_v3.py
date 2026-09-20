"""Material Workbench 3.0: exact prompts, optional AI strategy and full-source replay.

The module is deliberately a control plane.  It assembles auditable payloads
and delegates execution to the existing allow-listed Bridge media operations.
It never accepts a workflow, command or download URL from a project client.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.execution.models import OpenClawInstance
from app.projects import service as project_service
from app.projects.models import Project, ProjectRun, ProjectRunAsset

from .models import (
    MediaCreativeStrategySession,
    MediaGenerationJob,
    MediaReplayProject,
    MediaReplaySegment,
    MediaWorkbenchAgentProfile,
)
from .workbench_v2 import (
    _department_id,
    _dict,
    _list,
    _materialize_h3_source_roles,
    _normalize_source_roles,
    _reference_asset_duration,
    _resolve_output_preset,
    _text,
    _understand_source_assets,
    _user_id,
    output_params_for_ratio,
    production_frames_for_duration,
)


EXACT_PROMPT_POLICY_VERSION = "material-exact-h3-v1"
STRATEGY_POLICY_VERSION = "material-strategy-v1"
PROMPT_OPTIMIZATION_POLICY_VERSION = "minimax-h3-prompt-optimizer-v1"
REPLAY_POLICY_VERSION = "material-full-replay-v2"
LOCAL_REPLACE_PROFILE = "video_local_edit_sam2_propainter_data_patch_v2"
AGENT_MODEL_PROFILE = "deepseek-v4-flash"
MAX_AGENT_PROMPT_CHARS = 60000
MAX_STRATEGY_ROUNDS = 5
MAX_PROMPT_CHARS = 7000
_UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

V3_MEDIA_CAPABILITIES = {
    "video.create.direct_submit",
    "video.prompt.optimize",
    "video.strategy.start",
    "video.strategy.answer",
    "video.strategy.compile",
    "video.strategy.submit",
    "video.replay.analyze",
    "video.replay.update",
    "video.replay.submit",
    "video.replay.get",
    "video.agent.list",
    "video.agent.get",
    "video.agent.save",
    "video.agent.restore",
    "video.agent.invoke",
}

WORKBENCH_AGENT_SPECS: dict[str, dict[str, str]] = {
    "video_prompt_h3": {
        "display_name": "视频提示词 Agent",
        "source_file": "video_prompt_h3_agent.md",
        "description": "把当前创意、台词和已确认素材整理为可执行的 MiniMax H3 提示词。",
    },
    "video_replay_h3": {
        "display_name": "视频复刻 Agent",
        "source_file": "video_replay_h3_agent.md",
        "description": "基于完整源视频事实拆节拍、分段并生成 MiniMax H3 复刻方案。",
    },
}

_REFERENCE_TOKEN_RE = re.compile(r"<(?:Picture|Video|Audio)\s+[1-9][0-9]*>")
_QUOTED_DIALOGUE_RE = re.compile(r"[“\"]([^”\"\r\n]{1,1000})[”\"]")
_SPEAKER_DIALOGUE_RE = re.compile(
    r"(?:^|[\s；;])"
    r"(?:男|女|男生|女生|左女|右女|左边女生|右边女生|丈夫|妻子|男医生|女医生|医生|顾客|主播|旁白)"
    r"[：:]\s*(.+?)"
    r"(?=(?:\s+|[；;])(?:男|女|男生|女生|左女|右女|左边女生|右边女生|丈夫|妻子|男医生|女医生|医生|顾客|主播|旁白)[：:]|$)",
    re.DOTALL,
)
_PRESERVE_TERM_RE = re.compile(
    r"([\u3400-\u9fffA-Za-z0-9·/_-]{1,20}?)(?:需要|必须|仍需|要)?(?:保留|保持(?:原样|不变)?|不变)"
)
_PROMPT_OPTIMIZATION_FIDELITY_CODES = {
    "MEDIA_PROMPT_OPTIMIZATION_REFERENCE_INVALID",
    "MEDIA_PROMPT_OPTIMIZATION_DIALOGUE_CHANGED",
    "MEDIA_PROMPT_OPTIMIZATION_PRESERVE_CHANGED",
}


def _core():
    from . import service

    return service


def _new_id(prefix: str) -> str:
    return _core()._new_id(prefix)


def _json_hash(value: Any) -> str:
    return _core()._json_hash(value)


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _agent_spec(agent_key: Any) -> tuple[str, dict[str, str]]:
    key = _text(agent_key, 60)
    spec = WORKBENCH_AGENT_SPECS.get(key)
    if spec is None:
        raise AppError(
            "MEDIA_WORKBENCH_AGENT_NOT_FOUND",
            404,
            {"agent_key": key, "available": list(WORKBENCH_AGENT_SPECS)},
        )
    return key, spec


@lru_cache(maxsize=len(WORKBENCH_AGENT_SPECS))
def _default_agent_prompt(agent_key: str) -> str:
    key, spec = _agent_spec(agent_key)
    path = Path(__file__).with_name(spec["source_file"])
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AppError(
            "MEDIA_WORKBENCH_AGENT_DEFAULT_MISSING",
            500,
            {"agent_key": key, "file": path.name},
        ) from exc
    if not prompt:
        raise AppError("MEDIA_WORKBENCH_AGENT_DEFAULT_EMPTY", 500, {"agent_key": key})
    return prompt


def _clean_agent_prompt(value: Any) -> str:
    prompt = str(value or "").strip()
    if not prompt:
        raise AppError("MEDIA_WORKBENCH_AGENT_PROMPT_REQUIRED", 422)
    if len(prompt) > MAX_AGENT_PROMPT_CHARS:
        raise AppError(
            "MEDIA_WORKBENCH_AGENT_PROMPT_TOO_LONG",
            422,
            {"maximum": MAX_AGENT_PROMPT_CHARS, "actual": len(prompt)},
        )
    if _UNSAFE_CONTROL_RE.search(prompt):
        raise AppError("MEDIA_WORKBENCH_AGENT_PROMPT_INVALID", 422)
    return prompt


async def _agent_profile_row(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    agent_key: str,
) -> MediaWorkbenchAgentProfile | None:
    return (
        await db.execute(
            select(MediaWorkbenchAgentProfile)
            .where(
                MediaWorkbenchAgentProfile.project_id == project_id,
                MediaWorkbenchAgentProfile.user_id == user_id,
                MediaWorkbenchAgentProfile.agent_key == agent_key,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _agent_runtime(
    db: AsyncSession,
    user: User,
    project: Project,
    agent_key: Any,
) -> dict[str, Any]:
    key, spec = _agent_spec(agent_key)
    user_id = _user_id(user)
    row = await _agent_profile_row(db, project_id=project.id, user_id=user_id, agent_key=key)
    default_prompt = _default_agent_prompt(key)
    default_hash = _prompt_hash(default_prompt)
    prompt = _clean_agent_prompt(row.system_prompt if row is not None else default_prompt)
    # A profile restored to the then-current default is not a user-authored
    # customization.  Roll it forward when the shipped Agent instructions gain
    # a safety or quality fix, while preserving genuinely edited personal
    # prompts byte-for-byte.
    if (
        row is not None
        and _prompt_hash(prompt) == row.default_prompt_sha256
        and row.default_prompt_sha256 != default_hash
    ):
        row.system_prompt = default_prompt
        row.default_prompt_sha256 = default_hash
        row.version = int(row.version or 0) + 1
        row.updated_by = user_id
        row.updated_at = now_bjt()
        await db.flush()
        prompt = default_prompt
    return {
        "agent_key": key,
        "display_name": spec["display_name"],
        "description": spec["description"],
        "model": AGENT_MODEL_PROFILE,
        "version": int(row.version if row is not None else 1),
        "system_prompt": prompt,
        "prompt_sha256": _prompt_hash(prompt),
        "default_prompt_sha256": default_hash,
        "is_custom": _prompt_hash(prompt) != default_hash,
        "updated_at": isoformat_bjt(row.updated_at) if row is not None else None,
    }


def _public_agent(runtime: dict[str, Any], *, include_prompt: bool = True) -> dict[str, Any]:
    result = {
        key: value
        for key, value in runtime.items()
        if key != "system_prompt"
    }
    if include_prompt:
        result["system_prompt"] = runtime["system_prompt"]
    return result


def _bridge_capabilities(node: OpenClawInstance) -> dict[str, Any]:
    raw = getattr(node, "bridge_capabilities_json", None)
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _node_supports_local_replace(node: OpenClawInstance) -> bool:
    media = _dict(_bridge_capabilities(node).get("media"))
    return LOCAL_REPLACE_PROFILE in _list(media.get("profiles"))


def _clean_prompt_text(value: Any, *, field: str, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise AppError("MEDIA_DIRECT_INPUT_REQUIRED", 422, {"field": field})
    if _UNSAFE_CONTROL_RE.search(text):
        raise AppError("MEDIA_PROMPT_INVALID", 422, {"field": field, "detail": "contains control characters"})
    return text


def _generation_duration(value: Any, *, minimum: int = 3, maximum: int = 15) -> int:
    try:
        duration = int(value or 5)
    except (TypeError, ValueError) as exc:
        raise AppError("MEDIA_DURATION_INVALID", 422, {"minimum": minimum, "maximum": maximum}) from exc
    if duration < minimum or duration > maximum:
        raise AppError("MEDIA_DURATION_INVALID", 422, {"minimum": minimum, "maximum": maximum})
    return duration


def _normalize_explicit_sources(items: list[Any]) -> list[dict[str, Any]]:
    """Normalize only roles that the user explicitly confirmed.

    No file-name, SKU or visual inference is performed here.  A caller must
    send a business role plus role_locked=true (the UI does this after the
    user confirms the recommendation).
    """

    normalized: list[dict[str, Any]] = []
    for raw in items:
        item = _dict(raw)
        asset_id = _text(item.get("asset_id") or item.get("id"), 50)
        if not asset_id:
            continue
        if item.get("role_locked") is not True:
            raise AppError("MEDIA_SOURCE_ROLE_CONFIRMATION_REQUIRED", 422, {"asset_id": asset_id})
        role = _text(item.get("role"), 40)
        technical_role = _text(item.get("technical_role"), 40)
        if role in {
            "character_first_frame", "product_packshot", "product_detail", "visual_reference",
            "motion_reference", "audio_reference", "continuity_anchor",
        }:
            mapped = _normalize_source_roles([{**item, "role_locked": True}])
            if mapped:
                normalized.append(mapped[0])
                continue
        if technical_role not in {
            "first_frame", "reference_image", "reference_video", "reference_audio", "overlay_image"
        }:
            raise AppError("MEDIA_SOURCE_ROLE_CONFIRMATION_REQUIRED", 422, {"asset_id": asset_id})
        normalized.append({
            "asset_id": asset_id,
            "role": role or technical_role,
            "technical_role": technical_role,
            "purpose": _text(item.get("purpose"), 500),
            "role_locked": True,
        })
    return normalized


def _execution_mode(sources: list[dict[str, Any]]) -> str:
    roles = [str(item.get("technical_role") or "") for item in sources]
    if not roles or set(roles) <= {"overlay_image"}:
        return "text_to_video"
    if len(roles) == 1 and roles[0] == "first_frame":
        return "image_to_video"
    return "reference_replay"


def _reference_requests(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "asset_id": item["asset_id"],
            "role": item["technical_role"],
            "business_role": item.get("role"),
            "purpose": item.get("purpose") or "",
        }
        for item in sources
    ]


def _replay_control_prompt(config: dict[str, Any]) -> str:
    product_mode = str(config.get("product_mode") or "keep")
    audio_mode = str(config.get("audio_mode") or "redub")
    lines = [
        "更换人物" if config.get("replace_people") else "保留原人物",
        "去除画面文字" if config.get("remove_text") else "保留画面文字",
        {"keep": "保留原商品", "replace": "替换为所选商品", "remove": "不展示商品"}[product_mode],
        {
            "original": "使用原音",
            "redub": "使用用户填写的新台词重新配音",
            "partial_redub": "只替换用户指定的口播片段，其他原音保持不变",
            "silent": "静音",
        }[audio_mode],
        "保持原场景" if config.get("keep_scene") is not False else "不强制保持原场景",
        "保持原镜头运动" if config.get("keep_camera_motion") is not False else "不强制保持原镜头运动",
    ]
    return "复刻控制（用户已选择）：" + "；".join(lines) + "。"


def _replay_product_sources(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only product images the user explicitly selected for replacement."""

    if str(config.get("product_mode") or "keep") != "replace":
        return []
    return [
        {
            "asset_id": str(asset_id),
            "technical_role": "reference_image",
            "role": "product_packshot",
            "purpose": "用户确认用于替换的正确商品素材",
        }
        for asset_id in _list(config.get("product_asset_ids"))
        if str(asset_id).strip()
    ]


def _replay_preview_sources(segment: MediaReplaySegment, config: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = [{
        "asset_id": segment.source_clip_asset_id or f"pending-source-{segment.segment_index}",
        "technical_role": "reference_video",
        "role": "motion_reference",
        "purpose": f"源视频 {segment.start_seconds:.2f}–{segment.end_seconds:.2f} 秒完整片段",
    }]
    if segment.segment_index > 0:
        sources.append({
            "asset_id": f"pending-anchor-{segment.segment_index}",
            "technical_role": "reference_image",
            "role": "continuity_anchor",
            "purpose": "上一生成片段的尾帧锚点，用于保持人物、场景、动作方向和光线连续",
        })
    sources.extend(_replay_product_sources(config))
    return sources


def _normalize_local_replacement_items(value: Any, duration_seconds: float) -> list[dict[str, Any]]:
    items = _list(value)
    if len(items) > 12:
        raise AppError("MEDIA_LOCAL_REPLACEMENT_LIMIT", 422, {"maximum": 12})
    duration = max(0.01, float(duration_seconds or 0))
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        item = _dict(raw)
        original = _clean_prompt_text(item.get("original_text"), field="original_text")
        replacement = _clean_prompt_text(item.get("replacement_text"), field="replacement_text")
        if not original and not replacement:
            continue
        if not original or not replacement:
            raise AppError(
                "MEDIA_LOCAL_REPLACEMENT_INCOMPLETE",
                422,
                {"index": index, "detail": "原内容和替换内容必须同时填写"},
            )
        if original == replacement:
            raise AppError("MEDIA_LOCAL_REPLACEMENT_UNCHANGED", 422, {"index": index})
        scope = _text(item.get("scope"), 30) or "visual_audio"
        if scope not in {"visual_audio", "visual_only", "audio_only"}:
            raise AppError("MEDIA_LOCAL_REPLACEMENT_SCOPE_INVALID", 422, {"index": index})
        try:
            start_seconds = float(item.get("start_seconds") or 0)
            end_raw = item.get("end_seconds")
            end_seconds = duration if end_raw is None or end_raw == "" else float(end_raw)
        except (TypeError, ValueError):
            raise AppError("MEDIA_LOCAL_REPLACEMENT_RANGE_INVALID", 422, {"index": index}) from None
        if start_seconds < 0 or end_seconds <= start_seconds or end_seconds > duration + 0.05:
            raise AppError(
                "MEDIA_LOCAL_REPLACEMENT_RANGE_INVALID",
                422,
                {"index": index, "start_seconds": start_seconds, "end_seconds": end_seconds, "duration_seconds": duration},
            )
        normalized.append({
            "id": _text(item.get("id"), 80) or f"replace-{index + 1}",
            "scope": scope,
            "original_text": original,
            "replacement_text": replacement,
            "start_seconds": round(start_seconds, 3),
            "end_seconds": round(min(end_seconds, duration), 3),
            "region_hint": _text(item.get("region_hint"), 200),
            "preserve_visual_style": item.get("preserve_visual_style") is not False,
            "preserve_voice": item.get("preserve_voice") is not False,
        })
    return normalized


def _local_replace_instruction(config: dict[str, Any]) -> str:
    parts = ["原片局部替换：遮罩区域之外的像素、人物、动作、场景、节奏和音频保持不变。"]
    if config.get("remove_text"):
        parts.append("去除用户标记的画面文字区域并修复背景。")
    parts.append({
        "keep": "保留原商品。",
        "replace": "用所选正确商品素材替换原商品。",
        "remove": "去除原商品并修复背景。",
    }[str(config.get("product_mode") or "keep")])
    for index, item in enumerate(_list(config.get("replacement_items")), start=1):
        replacement = _dict(item)
        scope = str(replacement.get("scope") or "visual_audio")
        scope_text = {
            "visual_audio": "同步替换画面文字和对应口播",
            "visual_only": "只替换画面文字，原音保持不变",
            "audio_only": "只替换对应口播，画面像素保持不变",
        }[scope]
        timing = f"{float(replacement.get('start_seconds') or 0):.2f}–{float(replacement.get('end_seconds') or 0):.2f} 秒"
        region = _text(replacement.get("region_hint"), 200)
        parts.append(
            f"替换项 {index}：在 {timing}{'、' + region if region else ''}，{scope_text}；"
            f"将「{replacement.get('original_text')}」准确替换为「{replacement.get('replacement_text')}」。"
        )
        if scope in {"visual_audio", "visual_only"} and replacement.get("preserve_visual_style") is not False:
            parts.append("新画面内容沿用原字号、颜色、透视、位置和运动轨迹。")
        if scope in {"visual_audio", "audio_only"} and replacement.get("preserve_voice") is not False:
            parts.append("新口播沿用原说话人的声线、语气、语速、节奏和环境声。")
    return "".join(parts)


def compose_exact_h3_prompt(
    visual_prompt: Any,
    script: Any = "",
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compose the exact, visible prompt without adding creative semantics."""

    visual = _clean_prompt_text(visual_prompt, field="visual_prompt", required=True)
    dialogue = _clean_prompt_text(script, field="script")
    source_items = list(sources or [])
    counters = {"Picture": 0, "Video": 0, "Audio": 0}
    bindings: list[dict[str, Any]] = []
    visible_refs: list[str] = []
    for item in source_items:
        role = str(item.get("technical_role") or "")
        kind = "Picture" if role in {"first_frame", "reference_image", "overlay_image"} else (
            "Video" if role == "reference_video" else "Audio"
        )
        counters[kind] += 1
        token = f"<{kind} {counters[kind]}>"
        purpose = _text(item.get("purpose"), 500)
        bindings.append({
            "asset_id": item.get("asset_id"),
            "role": role,
            "token": token,
            "purpose": purpose,
        })
        visible_refs.append(f"{token}" + (f"：{purpose}" if purpose else ""))
    parts = [visual]
    if dialogue:
        parts.append("台词（原文，逐字保持）：\n" + dialogue)
    if visible_refs:
        parts.append("素材引用：\n" + "\n".join(visible_refs))
    prompt = "\n\n".join(parts)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise AppError(
            "MEDIA_PROMPT_TOO_LONG",
            422,
            {"field": "final_prompt", "length": len(prompt), "maximum": MAX_PROMPT_CHARS},
        )
    return {"prompt": prompt, "sha256": _prompt_hash(prompt), "bindings": bindings}


def normalize_prompt_optimization(
    raw: Any,
    *,
    original_visual_prompt: str,
    script: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate an AI suggestion without allowing it to become an execution side effect."""

    value = _dict(raw)
    optimized_visual = _clean_prompt_text(
        value.get("optimized_visual_prompt"),
        field="optimized_visual_prompt",
        required=True,
    )
    if _REFERENCE_TOKEN_RE.search(optimized_visual):
        raise AppError(
            "MEDIA_PROMPT_OPTIMIZATION_REFERENCE_INVALID",
            422,
            {"detail": "素材占位符只能由服务端按用户确认的素材用途装配"},
        )

    # Quoted dialogue embedded directly in the visual prompt is operator-owned
    # content just like the dedicated script field.  The optimizer may improve
    # actions around it, but may not silently rewrite or drop the spoken words.
    quoted_dialogue = _QUOTED_DIALOGUE_RE.findall(original_visual_prompt)
    protected_dialogue = quoted_dialogue or [
        fragment.strip().strip('“”"') for fragment in _SPEAKER_DIALOGUE_RE.findall(original_visual_prompt)
    ]
    missing_dialogue = [fragment.strip() for fragment in protected_dialogue if fragment.strip() not in optimized_visual]
    if missing_dialogue:
        raise AppError(
            "MEDIA_PROMPT_OPTIMIZATION_DIALOGUE_CHANGED",
            422,
            {
                "detail": "优化稿改动或遗漏了原台词，系统已阻止采用并将重新保真优化",
                "missing_dialogue": missing_dialogue[:20],
            },
        )
    required_terms = [term.rstrip("要需") for term in _PRESERVE_TERM_RE.findall(original_visual_prompt)]
    missing_terms = [term for term in required_terms if term and term not in optimized_visual]
    if missing_terms:
        raise AppError(
            "MEDIA_PROMPT_OPTIMIZATION_PRESERVE_CHANGED",
            422,
            {
                "detail": "优化稿遗漏了用户明确要求保留的内容，系统已阻止采用并将重新保真优化",
                "missing_preserved_terms": missing_terms[:20],
            },
        )

    original = compose_exact_h3_prompt(original_visual_prompt, script, sources)
    optimized = compose_exact_h3_prompt(optimized_visual, script, sources)
    modifications = []
    for item in _list(value.get("modifications"))[:20]:
        if isinstance(item, dict):
            normalized = {
                "field": _text(item.get("field"), 80) or "提示词表达",
                "before": _text(item.get("before"), 800),
                "after": _text(item.get("after"), 800),
                "reason": _text(item.get("reason"), 400),
            }
        else:
            normalized = {"field": "提示词表达", "before": "", "after": "", "reason": _text(item, 400)}
        if any(normalized.values()):
            modifications.append(normalized)

    return {
        "policy_version": PROMPT_OPTIMIZATION_POLICY_VERSION,
        "original_visual_prompt": original_visual_prompt,
        "optimized_visual_prompt": optimized_visual,
        "original_final_prompt": original["prompt"],
        "optimized_final_prompt": optimized["prompt"],
        "original_visual_prompt_sha256": _prompt_hash(original_visual_prompt),
        "optimized_visual_prompt_sha256": _prompt_hash(optimized_visual),
        "original_final_prompt_sha256": original["sha256"],
        "optimized_final_prompt_sha256": optimized["sha256"],
        "source_bindings": optimized["bindings"],
        "summary": _text(value.get("summary"), 800) or "已按 MiniMax H3 的时间线结构整理提示词。",
        "modifications": modifications,
        "preserved_items": [_text(item, 300) for item in _list(value.get("preserved_items"))[:30] if _text(item, 300)],
        "assumptions": [_text(item, 300) for item in _list(value.get("assumptions"))[:20] if _text(item, 300)],
        "warnings": [_text(item, 500) for item in _list(value.get("warnings"))[:20] if _text(item, 500)],
    }


def _safe_prompt_optimization_candidate(
    *,
    original_visual_prompt: str,
    duration_seconds: int,
    ratio: str,
) -> dict[str, Any]:
    """Create a visible, lossless fallback after AI fidelity checks fail twice."""

    structured = (
        f"输出规格：{duration_seconds} 秒，{ratio}。\n\n"
        "用户确认的画面、人物、动作、场景、台词、语调、保留项和可调整项（以下原文逐字执行）：\n"
        f"{original_visual_prompt}\n\n"
        "按原文出现顺序执行；镜头和动作只做自然衔接，不改写上述事实与台词。"
    )
    if len(structured) > MAX_PROMPT_CHARS:
        structured = original_visual_prompt
    return {
        "optimized_visual_prompt": structured,
        "summary": "AI 候选未通过原文保真校验，已生成逐字保留原文的安全结构化版本。",
        "modifications": [{
            "field": "执行结构",
            "before": "操作员原文",
            "after": "补充输出规格和执行顺序，原文逐字嵌入",
            "reason": "避免长台词被模型改写或遗漏",
        }],
        "preserved_items": ["操作员原文逐字保留", "台词说话人、轮次和顺序不变"],
        "assumptions": [],
        "warnings": ["AI 优化稿连续两次未通过保真门禁，本候选由服务端安全结构化生成。"],
    }


def _validate_reference_tokens(prompt: str, bindings: list[dict[str, Any]]) -> None:
    expected = [str(item.get("token") or "") for item in bindings if item.get("token")]
    actual = re.findall(r"<(?:Picture|Video|Audio)\s+[1-9][0-9]*>", prompt)
    expected_tokens = set(expected)
    actual_tokens = set(actual)
    missing = sorted(expected_tokens - actual_tokens)
    unexpected = sorted(actual_tokens - expected_tokens)
    if not missing and not unexpected:
        return

    reasons: list[str] = []
    if unexpected:
        reasons.append(
            f"提示词引用了 {', '.join(unexpected)}，但没有上传并确认对应素材；"
            "请上传素材并确认用途，或从提示词中删除这些占位符"
        )
    if missing:
        reasons.append(
            f"已上传并确认的素材没有出现在最终提示词中：{', '.join(missing)}；"
            "请恢复对应占位符，或移除该素材"
        )
    raise AppError(
        "MEDIA_REFERENCE_TOKEN_MISMATCH",
        422,
        {
            "expected": expected,
            "actual": actual,
            "missing": missing,
            "unexpected": unexpected,
            "reason": "；".join(reasons),
        },
    )


def _exact_plan(
    prompt: str,
    *,
    mode: str,
    params: dict[str, Any],
    origin: str,
    contract_version: str = EXACT_PROMPT_POLICY_VERSION,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "prompt_contract_version": contract_version,
        "prompt_origin": origin,
        "prompt_policy_version": contract_version,
        "integrated_multimodal_description": prompt,
        "overall_soundscape": "",
        "non_diegetic_music": "",
        "video_prompt": prompt,
        "audio_prompt": "",
        "negative_constraints": [],
        "reference_roles": [],
        "h3_mode": mode,
        "recommended_params": params,
        "user_visible_prompt": prompt,
        "user_visible_prompt_sha256": _prompt_hash(prompt),
        **dict(metadata or {}),
    }


async def _direct_submit(
    db: AsyncSession,
    user: User,
    project: Project,
    run: ProjectRun,
    payload: dict[str, Any],
) -> dict[str, Any]:
    original_sources = _normalize_explicit_sources(_list(payload.get("source_roles")))
    sources, reference_preprocessing = await _materialize_h3_source_roles(
        db,
        user,
        run,
        original_sources,
    )
    mode = _execution_mode(sources)
    preset_id, preset_params, _ = await _resolve_output_preset(db, project, payload.get("output_preset_id"))
    params = output_params_for_ratio(
        {**preset_params, **_dict(payload.get("params"))},
        payload.get("ratio") or "9:16",
    )
    duration = _generation_duration(payload.get("duration_seconds"))
    generation_seconds = max(4, duration)
    params["frames"] = production_frames_for_duration(generation_seconds, fps=int(params.get("fps") or 24))
    params["delivery_duration_seconds"] = duration
    assembled_default = compose_exact_h3_prompt(payload.get("visual_prompt"), payload.get("script"), sources)
    final_prompt_override = _clean_prompt_text(payload.get("final_prompt"), field="final_prompt")
    assembled = {
        **assembled_default,
        "prompt": final_prompt_override or assembled_default["prompt"],
        "sha256": _prompt_hash(final_prompt_override or assembled_default["prompt"]),
    }
    if len(assembled["prompt"]) > MAX_PROMPT_CHARS:
        raise AppError("MEDIA_PROMPT_TOO_LONG", 422, {"field": "final_prompt", "maximum": MAX_PROMPT_CHARS})
    _validate_reference_tokens(assembled["prompt"], assembled["bindings"])
    supplied_preview = _text(payload.get("prompt_preview_sha256"), 64)
    if supplied_preview and supplied_preview != assembled["sha256"]:
        raise AppError(
            "MEDIA_PROMPT_HASH_MISMATCH",
            409,
            {"preview_sha256": supplied_preview, "server_sha256": assembled["sha256"]},
        )
    optimization_meta = _dict(payload.get("prompt_optimization"))
    optimization_applied = bool(optimization_meta.get("applied"))
    if optimization_applied:
        if _text(optimization_meta.get("policy_version"), 100) != PROMPT_OPTIMIZATION_POLICY_VERSION:
            raise AppError("MEDIA_PROMPT_OPTIMIZATION_POLICY_INVALID", 422)
        adopted_visual_hash = _text(optimization_meta.get("adopted_visual_prompt_sha256"), 64)
        if adopted_visual_hash != _prompt_hash(_clean_prompt_text(payload.get("visual_prompt"), field="visual_prompt")):
            raise AppError("MEDIA_PROMPT_OPTIMIZATION_HASH_MISMATCH", 409)
        optimization_lineage = {
            "applied": True,
            "policy_version": PROMPT_OPTIMIZATION_POLICY_VERSION,
            "model": _text(optimization_meta.get("model"), 180),
            "original_visual_prompt_sha256": _text(
                optimization_meta.get("original_visual_prompt_sha256"), 64
            ),
            "candidate_visual_prompt_sha256": _text(
                optimization_meta.get("candidate_visual_prompt_sha256"), 64
            ),
            "adopted_visual_prompt_sha256": adopted_visual_hash,
            "user_edited_after_optimization": bool(
                optimization_meta.get("user_edited_after_optimization")
            ),
            "agent": {
                "agent_key": _text(_dict(optimization_meta.get("agent")).get("agent_key"), 60),
                "display_name": _text(_dict(optimization_meta.get("agent")).get("display_name"), 100),
                "version": int(_dict(optimization_meta.get("agent")).get("version") or 1),
                "prompt_sha256": _text(_dict(optimization_meta.get("agent")).get("prompt_sha256"), 64),
                "model": _text(_dict(optimization_meta.get("agent")).get("model"), 180) or AGENT_MODEL_PROFILE,
            },
        }
    else:
        optimization_lineage = {"applied": False}
    business = _dict(payload.get("business_info"))
    business_participation = _dict(payload.get("business_participation"))
    contract_version = _text(payload.get("_prompt_contract_version"), 80)
    if contract_version not in {EXACT_PROMPT_POLICY_VERSION, "material-fde-h3-v1"}:
        contract_version = EXACT_PROMPT_POLICY_VERSION
    plan = _exact_plan(
        assembled["prompt"],
        mode=mode,
        params=params,
        origin="direct_ai_optimized" if optimization_applied else "direct",
        contract_version=contract_version,
        metadata={
            "source_bindings": assembled["bindings"],
            "original_source_roles": original_sources,
            "reference_preprocessing": reference_preprocessing,
            "business_context_usage": "task_management_only",
            "business_info": business,
            "business_participation": business_participation,
            "base_assembled_prompt": assembled_default["prompt"],
            "user_prompt_edited": assembled["prompt"] != assembled_default["prompt"],
            "prompt_optimization": optimization_lineage,
        },
    )
    submit_payload = {
        "selected_source": "manual",
        "final_plan": plan,
        "brief": {
            "product": _text(business.get("product"), 120),
            "platform": _text(business.get("platform"), 80),
            "ratio": payload.get("ratio") or "9:16",
            "duration_seconds": generation_seconds,
            "delivery_duration_seconds": duration,
            "script": str(payload.get("script") or ""),
            "request": str(payload.get("visual_prompt") or ""),
        },
        "mode": mode,
        "params": params,
        "reference_assets": _reference_requests(sources),
        "source_roles": sources,
        "prompt_preview_sha256": assembled["sha256"],
        "output_preset_id": preset_id,
        "business_title": _text(payload.get("business_title"), 240) or _text(business.get("product"), 120) or "原文直出素材",
        "creative_option": "direct_ai_optimized" if optimization_applied else "direct_original",
        "idempotency_key": _text(payload.get("idempotency_key"), 128) or _json_hash({
            "run": run.id,
            "contract": contract_version,
            "prompt": assembled["sha256"],
            "params": params,
            "sources": sources,
        }),
    }
    try:
        quantity = int(payload.get("quantity") or 1)
    except (TypeError, ValueError) as exc:
        raise AppError("MEDIA_QUANTITY_INVALID", 422) from exc
    if quantity < 1 or quantity > 600:
        raise AppError("MEDIA_QUANTITY_INVALID", 422, {"minimum": 1, "maximum": 600})
    results = []
    for index in range(quantity):
        item_payload = {
            **submit_payload,
            "business_title": (
                f"{submit_payload['business_title']} · v{index + 1:02d}"
                if quantity > 1 else submit_payload["business_title"]
            ),
            "idempotency_key": _json_hash({"base": submit_payload["idempotency_key"], "variant": index + 1}),
            "creative_option": "direct_ai_optimized" if optimization_applied else "direct_original",
        }
        results.append(await _core()._submit_job(
            db, user, project, run, item_payload, allow_exact_prompt_contract=True
        ))
    jobs = [item["job"] for item in results]
    return {
        "deduped": all(bool(item.get("deduped")) for item in results),
        "job": jobs[0],
        "jobs": jobs,
        "count": len(jobs),
        "preview": {
            "final_prompt": assembled["prompt"],
            "prompt_sha256": assembled["sha256"],
            "bridge_prompt_sha256": jobs[0].get("execution_prompt_sha256"),
            "mode": mode,
            "source_bindings": assembled["bindings"],
        },
    }


def _included_business_context(payload: dict[str, Any]) -> dict[str, Any]:
    info = _dict(payload.get("business_info"))
    participation = _dict(payload.get("business_participation"))
    return {
        key: info.get(key)
        for key in ("product", "sku", "platform", "selling_points", "promotion")
        if participation.get(key) == "ai_strategy" and info.get(key) not in (None, "", [])
    }


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


async def _strategy_ai(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    *,
    prompt: str,
    system: str,
    feature: str,
    policy_version: str = STRATEGY_POLICY_VERSION,
    temperature: float = 0.45,
    max_output_tokens: int = 5000,
    model_profile: str | None = None,
) -> tuple[str, dict[str, Any]]:
    last: Any = None
    for attempt in (1, 2):
        response = await _core().codex_service._builtin_platform_ai_analyze(
            db,
            user,
            {
                "prompt": prompt if attempt == 1 else prompt + "\n上次返回无法解析。请只返回字段完整的 JSON 对象。",
                "system": system,
                "context_pack": {"project_run_id": run.id, "feature": feature, "policy_version": policy_version},
                "json_mode": True,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "model_profile": model_profile or _core().MEDIA_BASELINE_PROFILE,
            },
            effective_skill_id=None,
            effective_run_id=run.execution_run_id or run.id,
        )
        last = response.get("output")
        parsed = _parse_json_object(last)
        if parsed:
            return _text(response.get("model"), 180) or model_profile or _core().MEDIA_BASELINE_PROFILE, parsed
    raise AppError("MEDIA_STRATEGY_JSON_INVALID", 422, {"editable_output": _text(last, 12000)})


async def _agent_list(db: AsyncSession, user: User, project: Project) -> dict[str, Any]:
    agents = [
        _public_agent(await _agent_runtime(db, user, project, key))
        for key in WORKBENCH_AGENT_SPECS
    ]
    return {"agents": agents, "model": AGENT_MODEL_PROFILE}


async def _agent_save(
    db: AsyncSession,
    user: User,
    project: Project,
    payload: dict[str, Any],
) -> dict[str, Any]:
    key, spec = _agent_spec(payload.get("agent_key"))
    prompt = _clean_agent_prompt(payload.get("system_prompt"))
    user_id = _user_id(user)
    row = await _agent_profile_row(db, project_id=project.id, user_id=user_id, agent_key=key)
    now = now_bjt()
    if row is None:
        row = MediaWorkbenchAgentProfile(
            id=_new_id("mwa"),
            project_id=project.id,
            user_id=user_id,
            agent_key=key,
            display_name=spec["display_name"],
            model=AGENT_MODEL_PROFILE,
            system_prompt=prompt,
            version=1,
            default_prompt_sha256=_prompt_hash(_default_agent_prompt(key)),
            updated_by=user_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.system_prompt = prompt
        row.model = AGENT_MODEL_PROFILE
        row.display_name = spec["display_name"]
        row.default_prompt_sha256 = _prompt_hash(_default_agent_prompt(key))
        row.version = int(row.version or 0) + 1
        row.updated_by = user_id
        row.updated_at = now
    await db.flush()
    return {"agent": _public_agent(await _agent_runtime(db, user, project, key))}


async def _agent_restore(
    db: AsyncSession,
    user: User,
    project: Project,
    payload: dict[str, Any],
) -> dict[str, Any]:
    key, spec = _agent_spec(payload.get("agent_key"))
    default_prompt = _default_agent_prompt(key)
    user_id = _user_id(user)
    row = await _agent_profile_row(db, project_id=project.id, user_id=user_id, agent_key=key)
    now = now_bjt()
    if row is None:
        row = MediaWorkbenchAgentProfile(
            id=_new_id("mwa"), project_id=project.id, user_id=user_id,
            agent_key=key, display_name=spec["display_name"], model=AGENT_MODEL_PROFILE,
            system_prompt=default_prompt, version=1,
            default_prompt_sha256=_prompt_hash(default_prompt), updated_by=user_id,
            created_at=now, updated_at=now,
        )
        db.add(row)
    else:
        row.system_prompt = default_prompt
        row.model = AGENT_MODEL_PROFILE
        row.display_name = spec["display_name"]
        row.default_prompt_sha256 = _prompt_hash(default_prompt)
        row.version = int(row.version or 0) + 1
        row.updated_by = user_id
        row.updated_at = now
    await db.flush()
    return {"agent": _public_agent(await _agent_runtime(db, user, project, key)), "restored": True}


async def _call_agent_json(
    db: AsyncSession,
    user: User,
    project: Project,
    run: ProjectRun,
    *,
    agent_key: str,
    feature: str,
    prompt: str,
    runtime_contract: str,
    temperature: float = 0.2,
    max_output_tokens: int = 7000,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    runtime = await _agent_runtime(db, user, project, agent_key)
    system = (
        runtime["system_prompt"]
        + "\n\n---\nSkillForge 运行契约（平台控制，不属于用户可编辑 Agent 指令）：\n"
        + runtime_contract
        + "\n必须只返回一个合法 JSON 对象，不要 Markdown 代码围栏。"
    )
    model, result = await _strategy_ai(
        db, user, run,
        prompt=prompt,
        system=system,
        feature=feature,
        policy_version=f"{agent_key}-v{runtime['version']}",
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        model_profile=AGENT_MODEL_PROFILE,
    )
    return runtime, model, result


async def _agent_invoke(
    db: AsyncSession,
    user: User,
    project: Project,
    run: ProjectRun,
    payload: dict[str, Any],
) -> dict[str, Any]:
    key, _ = _agent_spec(payload.get("agent_key"))
    request = _clean_prompt_text(payload.get("request"), field="request", required=True)
    if len(request) > 20000:
        raise AppError("MEDIA_WORKBENCH_AGENT_REQUEST_TOO_LONG", 422, {"maximum": 20000})
    if key == "video_prompt_h3":
        contract = (
            "输出字段：analysis（对象）、recommended_mode（T2VA/I2VA/FL2VA/L2VA/Ref2VA）、"
            "duration_seconds、ratio、asset_roles（数组）、final_h3_prompt（字符串）、"
            "acceptance_checks（数组）、assumptions（数组）、warnings（数组）。"
            "本次试运行没有平台确认的素材绑定；除非 request 本身包含结构化 confirmed_source_bindings，"
            "且每项都带平台分配的 <Picture N>/<Video N>/<Audio N> 标签，否则必须按无素材处理："
            "recommended_mode=T2VA、asset_roles=[]，不得虚构参考图、参考视频、文件名、Subject 来源或视觉事实。"
            "用户只写‘这个场景’‘参考图’‘原视频’不代表素材已上传，应在 warnings 提醒先上传并确认素材。"
        )
    else:
        contract = (
            "输出字段：analysis（对象，含内容类型、节拍、视角、构图、妆造）、"
            "segments（数组，每项含 index、start_seconds、end_seconds、visual_prompt）、"
            "copy_points（数组）、avoid_points（数组）、continuation_plan（数组）、"
            "acceptance_checks（数组）、assumptions（数组）、warnings（数组）。"
            "如果没有真实视频视觉事实，必须在 warnings 说明这只是 Agent 指令试运行，不得伪造已看过视频。"
        )
    runtime, model, result = await _call_agent_json(
        db, user, project, run,
        agent_key=key,
        feature=f"workbench_agent_invoke:{key}",
        prompt=request,
        runtime_contract=contract,
        max_output_tokens=8000,
    )
    return {
        "agent": _public_agent(runtime, include_prompt=False),
        "actual_model": model,
        "result": result,
    }


async def _prompt_optimize(
    db: AsyncSession,
    user: User,
    project: Project,
    run: ProjectRun,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Return a user-reviewable H3 prompt candidate; never submit a job."""

    original_visual = _clean_prompt_text(
        payload.get("visual_prompt"), field="visual_prompt", required=True
    )
    script = _clean_prompt_text(payload.get("script"), field="script")
    sources = _normalize_explicit_sources(_list(payload.get("source_roles")))
    duration = _generation_duration(payload.get("duration_seconds"))
    ratio = _text(payload.get("ratio"), 20) or "9:16"
    original = compose_exact_h3_prompt(original_visual, script, sources)

    agent = await _agent_runtime(db, user, project, "video_prompt_h3")
    system = (
        agent["system_prompt"]
        + "\n\n---\nSkillForge 运行契约（平台控制，不属于用户可编辑 Agent 指令）："
        "本次只做保真优化，不做创意发散，也不提交生成任务。"
        "只返回 JSON 对象，字段为 optimized_visual_prompt、summary、modifications、preserved_items、assumptions、warnings。"
        "optimized_visual_prompt 只包含优化后的画面提示词，不要重复独立 script 字段，不要自行写 <Picture N>/<Video N>/<Audio N>。"
        "按 H3 易执行顺序整理：输出目标与时长画幅；主体与引用用途；按时间先后排列动作和台词；场景与光线；"
        "景别、机位、运镜和剪辑；画面风格与节奏；声音、对白和环境声；最后写必须保留和避免事项。"
        "用户已写好的主体、人物关系、物体、场景、服装、表情、动作、台词、语调以及保留/更换/允许调整/不要出现等要求都是硬事实。"
        "所有引号内台词、说话人、轮次和顺序必须逐字保持；独立 script 字段由服务端原样追加，禁止改写。"
        "不得新增价格、促销、商品功效、品牌承诺、角色国籍、商品文字或用户没有提出的情节。"
        "可以补充最小必要的时间线、自然动作、镜头衔接、光线、环境声和防漂移约束；补充内容必须列入 assumptions。"
        "modifications 每项使用 field、before、after、reason；preserved_items 明列保留项。"
    )
    prompt = (
        "请把以下操作员原文整理成一份更适合 MiniMax H3 执行、但业务含义不变的候选提示词。"
        "候选只供用户比较，用户未确认前不得替换原文。\n"
        + json.dumps(
            {
                "duration_seconds": duration,
                "ratio": ratio,
                "visual_prompt": original_visual,
                "script_preserve_verbatim": script,
                "confirmed_source_bindings": original["bindings"],
            },
            ensure_ascii=False,
        )
    )
    result: dict[str, Any] | None = None
    model = ""
    fidelity_failure: AppError | None = None
    current_prompt = prompt
    for fidelity_attempt in (1, 2):
        model, raw = await _strategy_ai(
            db,
            user,
            run,
            prompt=current_prompt,
            system=system,
            feature="prompt_optimize",
            policy_version=PROMPT_OPTIMIZATION_POLICY_VERSION,
            temperature=0.2,
            max_output_tokens=6000,
            model_profile=AGENT_MODEL_PROFILE,
        )
        try:
            result = normalize_prompt_optimization(
                raw,
                original_visual_prompt=original_visual,
                script=script,
                sources=sources,
            )
            break
        except AppError as exc:
            if exc.code not in _PROMPT_OPTIMIZATION_FIDELITY_CODES:
                raise
            fidelity_failure = exc
            if fidelity_attempt == 1:
                current_prompt = (
                    prompt
                    + "\n上一个候选未通过原文保真校验。不要概括、纠错、润色或规范化原文中的台词、数字和保留项。"
                    + "请让下列操作员原文作为连续原文逐字出现在 optimized_visual_prompt 中，再只在其前后补充 H3 执行结构：\n"
                    + original_visual
                )

    if result is None:
        fallback = _safe_prompt_optimization_candidate(
            original_visual_prompt=original_visual,
            duration_seconds=duration,
            ratio=ratio,
        )
        result = normalize_prompt_optimization(
            fallback,
            original_visual_prompt=original_visual,
            script=script,
            sources=sources,
        )
        result["fidelity_fallback"] = True
        result["fidelity_failure_code"] = fidelity_failure.code if fidelity_failure else ""
    else:
        result["fidelity_fallback"] = False
    result["model"] = model
    result["agent"] = _public_agent(agent, include_prompt=False)
    result["requires_user_confirmation"] = True
    result["applied"] = False
    return {"optimization": result}


def _normalize_directions(raw: Any, original_script: str) -> list[dict[str, Any]]:
    items = _list(_dict(raw).get("directions"))
    if len(items) != 3:
        raise AppError("MEDIA_STRATEGY_DIRECTIONS_INVALID", 422, {"expected": 3, "actual": len(items)})
    result: list[dict[str, Any]] = []
    for index, direction_id in enumerate(("A", "B", "C")):
        item = _dict(items[index])
        suggested = _text(item.get("suggested_script"), 6000)
        selected_suggestion = suggested if direction_id == "C" else original_script
        original_lines = original_script.splitlines()
        suggested_lines = selected_suggestion.splitlines()
        script_diff = [
            {
                "line": line_index + 1,
                "original": original_lines[line_index] if line_index < len(original_lines) else "",
                "suggested": suggested_lines[line_index] if line_index < len(suggested_lines) else "",
                "changed": (
                    original_lines[line_index] if line_index < len(original_lines) else ""
                ) != (
                    suggested_lines[line_index] if line_index < len(suggested_lines) else ""
                ),
            }
            for line_index in range(max(len(original_lines), len(suggested_lines)))
        ]
        result.append({
            "id": direction_id,
            "title": _text(item.get("title"), 100) or f"方向 {direction_id}",
            "summary": _text(item.get("summary"), 800),
            "scene": _text(item.get("scene"), 800),
            "performance": _text(item.get("performance"), 800),
            "camera_rhythm": _text(item.get("camera_rhythm"), 800),
            "original_script": original_script,
            "suggested_script": selected_suggestion,
            "script_diff": script_diff,
            "script_change_allowed": direction_id == "C",
            "script_change_accepted": False,
        })
    return result


def _serialize_strategy(row: MediaCreativeStrategySession) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status,
        "model": row.model,
        "policy_version": row.policy_version,
        "original_input": row.original_input_json or {},
        "included_context": row.included_context_json or {},
        "directions": row.directions_json or [],
        "selected_direction_ids": row.selected_direction_ids_json or [],
        "questions": row.questions_json or [],
        "answers": row.answers_json or [],
        "assumptions": row.assumptions_json or [],
        "compiled_plans": row.compiled_plans_json or [],
        "round_no": row.round_no,
        "max_rounds": row.max_rounds,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


async def _require_strategy(db: AsyncSession, run: ProjectRun, session_id: Any) -> MediaCreativeStrategySession:
    row = await db.get(MediaCreativeStrategySession, _text(session_id, 50))
    if row is None or row.project_run_id != run.id:
        raise AppError("MEDIA_STRATEGY_NOT_FOUND", 404)
    return row


async def _strategy_start(db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    requested_contract = _text(payload.get("_prompt_contract_version"), 80)
    if requested_contract not in {EXACT_PROMPT_POLICY_VERSION, "material-fde-h3-v1"}:
        requested_contract = EXACT_PROMPT_POLICY_VERSION
    original = {
        "visual_prompt": _clean_prompt_text(payload.get("visual_prompt"), field="visual_prompt", required=True),
        "script": _clean_prompt_text(payload.get("script"), field="script"),
        "source_roles": _normalize_explicit_sources(_list(payload.get("source_roles"))),
        "ratio": _text(payload.get("ratio"), 20) or "9:16",
        "duration_seconds": _generation_duration(payload.get("duration_seconds")),
        "prompt_contract_version": requested_contract,
    }
    included = _included_business_context(payload)
    fingerprint = _json_hash({"policy": STRATEGY_POLICY_VERSION, "input": original, "included": included})
    refresh_direction_id = _text(payload.get("refresh_direction_id"), 1).upper()
    refresh_session = None
    if refresh_direction_id:
        if refresh_direction_id not in {"A", "B", "C"}:
            raise AppError("MEDIA_STRATEGY_DIRECTION_REQUIRED", 422)
        refresh_session = await _require_strategy(db, run, payload.get("strategy_session_id"))
        original = _dict(refresh_session.original_input_json)
        included = _dict(refresh_session.included_context_json)
        fingerprint = refresh_session.input_fingerprint
    if not bool(payload.get("refresh")):
        cached = (
            await db.execute(
                select(MediaCreativeStrategySession)
                .where(
                    MediaCreativeStrategySession.project_run_id == run.id,
                    MediaCreativeStrategySession.input_fingerprint == fingerprint,
                )
                .order_by(MediaCreativeStrategySession.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if cached is not None:
            return {"cache_hit": True, "strategy": _serialize_strategy(cached)}
    system = (
        "你是视频素材场景策略助手，不是广告文案发明器。只输出 JSON 对象 directions，且必须正好三项并按 A/B/C 排序。"
        "A、B 必须逐字保留输入台词，只改变场景、人物自然动作和镜头节奏；C 可以给 suggested_script，但必须同时保留 original_script。"
        "每项字段 title、summary、scene、performance、camera_rhythm、suggested_script。"
        "不得新增用户未提供的价格、促销、功效、品牌承诺、人物国籍或商品事实。"
    )
    prompt = "基于以下原始输入给出三个可选择方向，不要替用户扩大需求：\n" + json.dumps(
        {"original": original, "explicit_business_context": included}, ensure_ascii=False
    )
    model, raw = await _strategy_ai(db, user, run, prompt=prompt, system=system, feature="strategy_start")
    directions = _normalize_directions(raw, original["script"])
    if refresh_session is not None:
        replacement = next(item for item in directions if item["id"] == refresh_direction_id)
        refresh_session.directions_json = [
            replacement if str(item.get("id") or "") == refresh_direction_id else item
            for item in _list(refresh_session.directions_json)
        ]
        refresh_session.compiled_plans_json = [
            item for item in _list(refresh_session.compiled_plans_json)
            if str(_dict(item).get("direction_id") or "") != refresh_direction_id
        ]
        refresh_session.status = "directions_ready"
        refresh_session.model = model
        refresh_session.updated_at = now_bjt()
        await db.flush()
        return {"cache_hit": False, "refreshed_direction_id": refresh_direction_id, "strategy": _serialize_strategy(refresh_session)}
    now = now_bjt()
    row = MediaCreativeStrategySession(
        id=_new_id("mcs"),
        project_id=project.id,
        project_run_id=run.id,
        department_id=_department_id(run, project),
        requested_by=_user_id(user),
        status="directions_ready",
        input_fingerprint=fingerprint,
        original_input_json=original,
        included_context_json=included,
        directions_json=directions,
        selected_direction_ids_json=[],
        questions_json=[],
        answers_json=[],
        assumptions_json=[],
        compiled_plans_json=[],
        round_no=0,
        max_rounds=MAX_STRATEGY_ROUNDS,
        model=model,
        policy_version=STRATEGY_POLICY_VERSION,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    return {"cache_hit": False, "strategy": _serialize_strategy(row)}


async def _strategy_answer(db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    row = await _require_strategy(db, run, payload.get("strategy_session_id"))
    selected = [item for item in _list(payload.get("selected_direction_ids")) if item in {"A", "B", "C"}]
    if not selected:
        selected = [item for item in row.selected_direction_ids_json if item in {"A", "B", "C"}]
    if not selected:
        raise AppError("MEDIA_STRATEGY_DIRECTION_REQUIRED", 422)
    answers = [*_list(row.answers_json), _dict(payload.get("answers"))]
    row.selected_direction_ids_json = list(dict.fromkeys(selected))
    row.answers_json = answers
    row.round_no = min(int(row.round_no or 0) + 1, MAX_STRATEGY_ROUNDS)
    skip = bool(payload.get("skip")) or row.round_no >= MAX_STRATEGY_ROUNDS
    if skip:
        row.questions_json = []
        row.assumptions_json = [
            "用户跳过的缺失信息由当前场景方向中的最小必要假设补齐；所有假设会显示在最终方案中。"
        ]
        row.status = "ready_to_compile"
    else:
        system = (
            "你只负责向视频素材用户追问缺失信息。只输出 JSON 对象 questions。每题字段 id、label、options、multiple、required。"
            "一次可返回多题，但只问会显著改变场景、人物关系、表演强度、镜头、商品入镜或台词保留的问题。最多五轮。"
        )
        prompt = json.dumps({
            "input": row.original_input_json,
            "directions": [item for item in row.directions_json if item.get("id") in selected],
            "prior_answers": answers,
            "round": row.round_no,
        }, ensure_ascii=False)
        _, raw = await _strategy_ai(db, user, run, prompt=prompt, system=system, feature="strategy_questions")
        row.questions_json = _list(raw.get("questions"))[:8]
        row.status = "questions_ready" if row.questions_json else "ready_to_compile"
    row.updated_at = now_bjt()
    await db.flush()
    return {"strategy": _serialize_strategy(row)}


async def _strategy_compile(db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    row = await _require_strategy(db, run, payload.get("strategy_session_id"))
    selected = [item for item in _list(payload.get("selected_direction_ids")) if item in {"A", "B", "C"}]
    if not selected:
        selected = list(row.selected_direction_ids_json or [])
    if not selected:
        raise AppError("MEDIA_STRATEGY_DIRECTION_REQUIRED", 422)
    accept_c = bool(payload.get("accept_direction_c_script"))
    preset_id, preset_params, _ = await _resolve_output_preset(db, project, payload.get("output_preset_id"))
    original = _dict(row.original_input_json)
    params = output_params_for_ratio({**preset_params, **_dict(payload.get("params"))}, original.get("ratio") or "9:16")
    duration = int(original.get("duration_seconds") or 5)
    params["frames"] = production_frames_for_duration(max(4, duration), fps=int(params.get("fps") or 24))
    params["delivery_duration_seconds"] = duration
    sources = _list(original.get("source_roles"))
    plans: list[dict[str, Any]] = []
    for direction in [item for item in row.directions_json if item.get("id") in selected]:
        direction_id = str(direction.get("id"))
        system = (
            "你把已选场景方向完善成可编辑的 MiniMax H3 画面提示词。只输出 JSON 对象，字段 visual_prompt、summary、"
            "scene、characters、shots、assumptions。不得编造产品事实，不得改写台词，不得加入隐藏负面词或系统模板。"
        )
        prompt = json.dumps({
            "original_visual_prompt": original.get("visual_prompt"),
            "selected_direction": direction,
            "answers": row.answers_json,
            "explicit_business_context": row.included_context_json,
            "duration_seconds": duration,
        }, ensure_ascii=False)
        model, raw = await _strategy_ai(db, user, run, prompt=prompt, system=system, feature="strategy_compile")
        visual_prompt = _clean_prompt_text(raw.get("visual_prompt"), field="visual_prompt", required=True)
        script = str(original.get("script") or "")
        if direction_id == "C" and accept_c:
            script = str(direction.get("suggested_script") or script)
        assembled = compose_exact_h3_prompt(visual_prompt, script, sources)
        plan = _exact_plan(
            assembled["prompt"],
            mode=_execution_mode(sources),
            params=params,
            origin="ai_strategy",
            contract_version=(
                _text(original.get("prompt_contract_version"), 80)
                if _text(original.get("prompt_contract_version"), 80)
                in {EXACT_PROMPT_POLICY_VERSION, "material-fde-h3-v1"}
                else EXACT_PROMPT_POLICY_VERSION
            ),
            metadata={
                "strategy_policy_version": STRATEGY_POLICY_VERSION,
                "strategy_direction_id": direction_id,
                "strategy_model": model,
                "direction_summary": _text(raw.get("summary"), 1000) or direction.get("summary"),
                "scene": raw.get("scene"),
                "characters": _list(raw.get("characters")),
                "shots": _list(raw.get("shots")),
                "assumptions": _list(raw.get("assumptions")),
                "modifications": [
                    {
                        "field": "画面提示词",
                        "before": original.get("visual_prompt") or "",
                        "after": visual_prompt,
                    },
                    {
                        "field": "台词",
                        "before": original.get("script") or "",
                        "after": script,
                    },
                ],
                "original_script": original.get("script") or "",
                "selected_script": script,
                "script_change_accepted": direction_id == "C" and accept_c,
                "source_bindings": assembled["bindings"],
                "included_business_context": row.included_context_json or {},
                "original_visual_prompt": original.get("visual_prompt") or "",
            },
        )
        plans.append({
            "direction_id": direction_id,
            "title": direction.get("title"),
            "final_prompt": assembled["prompt"],
            "prompt_sha256": assembled["sha256"],
            "plan": plan,
        })
    row.selected_direction_ids_json = selected
    row.compiled_plans_json = plans
    row.assumptions_json = [item for plan in plans for item in _list(_dict(plan.get("plan")).get("assumptions"))]
    row.status = "compiled"
    row.updated_at = now_bjt()
    await db.flush()
    return {"output_preset_id": preset_id, "strategy": _serialize_strategy(row)}


async def _strategy_submit(db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    row = await _require_strategy(db, run, payload.get("strategy_session_id"))
    edited = {str(item.get("direction_id")): _dict(item) for item in _list(payload.get("plans")) if isinstance(item, dict)}
    selected = [item for item in _list(payload.get("selected_direction_ids")) if item in {"A", "B", "C"}]
    if not selected:
        selected = list(row.selected_direction_ids_json or [])
    original_sources = _normalize_explicit_sources(
        _list(_dict(row.original_input_json).get("source_roles"))
    )
    sources, reference_preprocessing = await _materialize_h3_source_roles(
        db,
        user,
        run,
        original_sources,
    )
    execution_bindings = compose_exact_h3_prompt("素材引用校验", "", sources)["bindings"]
    jobs = []
    for compiled in row.compiled_plans_json or []:
        direction_id = str(compiled.get("direction_id") or "")
        if direction_id not in selected:
            continue
        override = edited.get(direction_id, {})
        final_prompt = _clean_prompt_text(
            override.get("final_prompt") if override else compiled.get("final_prompt"),
            field="final_prompt",
            required=True,
        )
        if len(final_prompt) > MAX_PROMPT_CHARS:
            raise AppError("MEDIA_PROMPT_TOO_LONG", 422, {"direction_id": direction_id})
        sha = _prompt_hash(final_prompt)
        compiled_prompt = str(compiled.get("final_prompt") or "")
        plan = {
            **_dict(compiled.get("plan")),
            "integrated_multimodal_description": final_prompt,
            "video_prompt": final_prompt,
            "user_visible_prompt": final_prompt,
            "user_visible_prompt_sha256": sha,
            "user_prompt_edit": {
                "changed": final_prompt != compiled_prompt,
                "compiled_prompt_sha256": _prompt_hash(compiled_prompt),
                "final_prompt_sha256": sha,
            },
            "original_source_roles": original_sources,
            "source_bindings": execution_bindings,
            "reference_preprocessing": reference_preprocessing,
        }
        _validate_reference_tokens(final_prompt, execution_bindings)
        result = await _core()._submit_job(db, user, project, run, {
            "selected_source": "manual",
            "final_plan": plan,
            "brief": row.original_input_json,
            "mode": plan.get("h3_mode"),
            "params": plan.get("recommended_params"),
            "reference_assets": _reference_requests(sources),
            "source_roles": sources,
            "prompt_preview_sha256": sha,
            "strategy_session_id": row.id,
            "strategy_direction_id": direction_id,
            "creative_option": "ai_strategy",
            "business_title": _text(override.get("business_title"), 240) or f"AI 场景方向 {direction_id} · {compiled.get('title') or ''}",
            "idempotency_key": _text(override.get("idempotency_key"), 128) or _json_hash({"session": row.id, "direction": direction_id, "prompt": sha}),
        }, allow_exact_prompt_contract=True)
        jobs.append(result["job"])
    if not jobs:
        raise AppError("MEDIA_STRATEGY_DIRECTION_REQUIRED", 422)
    row.status = "submitted"
    row.updated_at = now_bjt()
    await db.flush()
    return {"jobs": jobs, "count": len(jobs), "strategy": _serialize_strategy(row)}


def plan_replay_segments(duration_seconds: float, shots: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Build contiguous 4–15 second ranges covering the complete source."""

    duration = round(float(duration_seconds), 3)
    if duration < 2 or duration > 60:
        raise AppError("MEDIA_REPLAY_DURATION_INVALID", 422, {"minimum": 2, "maximum": 60, "actual": duration})
    if duration <= 15:
        return [{"index": 0, "start_seconds": 0.0, "end_seconds": duration, "duration_seconds": duration, "overlap_seconds": 0.0}]
    boundaries = sorted({
        round(float(_dict(shot).get("end_seconds") or 0), 3)
        for shot in list(shots or [])
        if 0 < float(_dict(shot).get("end_seconds") or 0) < duration
    })
    result: list[dict[str, Any]] = []
    start = 0.0
    while duration - start > 15:
        candidates = [value for value in boundaries if 4 <= value - start <= 15]
        end = max(candidates) if candidates else min(duration, start + 14)
        result.append({"index": len(result), "start_seconds": round(start, 3), "end_seconds": round(end, 3), "duration_seconds": round(end - start, 3), "overlap_seconds": 0.0})
        start = end
    remainder = duration - start
    if 0 < remainder < 4 and result:
        previous = result[-1]
        combined_start = float(previous["start_seconds"])
        combined_duration = duration - combined_start
        if combined_duration <= 15:
            previous["end_seconds"] = duration
            previous["duration_seconds"] = round(combined_duration, 3)
            remainder = 0
        else:
            shift = 4 - remainder
            previous["end_seconds"] = round(float(previous["end_seconds"]) - shift, 3)
            previous["duration_seconds"] = round(float(previous["end_seconds"]) - combined_start, 3)
            start = float(previous["end_seconds"])
    if duration - start > 0.001:
        result.append({"index": len(result), "start_seconds": round(start, 3), "end_seconds": duration, "duration_seconds": round(duration - start, 3), "overlap_seconds": 0.0})
    for index, item in enumerate(result):
        item["index"] = index
        item["overlap_seconds"] = (
            0.0 if index == 0 or round(float(item["start_seconds"]), 3) in boundaries
            else min(0.5, float(item["start_seconds"]))
        )
    return result


def _serialize_replay(row: MediaReplayProject, segments: list[MediaReplaySegment]) -> dict[str, Any]:
    return {
        "id": row.id,
        "mode": row.mode,
        "status": row.status,
        "source_asset_id": row.source_asset_id,
        "source_duration_seconds": row.source_duration_seconds,
        "analysis": row.analysis_json or {},
        "config": row.config_json or {},
        "preview_asset_id": row.preview_asset_id,
        "policy_version": REPLAY_POLICY_VERSION,
        "segments": [
            {
                "id": item.id,
                "index": item.segment_index,
                "start_seconds": item.start_seconds,
                "end_seconds": item.end_seconds,
                "duration_seconds": item.duration_seconds,
                "status": item.status,
                "source_clip_asset_id": item.source_clip_asset_id,
                "media_job_id": item.media_job_id,
                "prompt": item.prompt_json or {},
                "dialogue": item.dialogue_json or {},
                "seam_analysis": item.seam_analysis_json or {},
            }
            for item in segments
        ],
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


async def _replay_rows(db: AsyncSession, run: ProjectRun, replay_id: Any) -> tuple[MediaReplayProject, list[MediaReplaySegment]]:
    row = await db.get(MediaReplayProject, _text(replay_id, 50))
    if row is None or row.project_run_id != run.id:
        raise AppError("MEDIA_REPLAY_NOT_FOUND", 404)
    segments = (
        await db.execute(
            select(MediaReplaySegment)
            .where(MediaReplaySegment.replay_project_id == row.id)
            .order_by(MediaReplaySegment.segment_index.asc())
        )
    ).scalars().all()
    return row, list(segments)


async def _build_replay_proxy_and_audio_map(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    source: ProjectRunAsset,
    duration: float,
) -> dict[str, Any]:
    """Create a cached lightweight proxy and deterministic audio-activity map."""

    metadata = _dict(source.metadata_json)
    cached = _dict(metadata.get("media_replay_analysis_cache"))
    if cached.get("source_sha256") == source.sha256 and cached.get("policy_version") == REPLAY_POLICY_VERSION:
        return cached
    ffmpeg = _core()._media_quality_ffmpeg_executable()
    source_path = project_service._project_run_asset_abs_path(source)
    with tempfile.TemporaryDirectory(prefix="sf-replay-analysis-") as temp_dir:
        proxy = Path(temp_dir) / f"{source.id}-proxy.mp4"
        proxy_proc = await asyncio.to_thread(
            subprocess.run,
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source_path),
                "-vf", "scale='min(540,iw)':-2:flags=lanczos", "-r", "12", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "27", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-y", str(proxy),
            ],
            capture_output=True,
            timeout=600,
            check=False,
        )
        proxy_asset_id = ""
        if proxy_proc.returncode == 0 and proxy.is_file():
            uploaded = await project_service.upload_project_run_asset(
                db, user, run.id, file_name=proxy.name, mime_type="video/mp4", content=proxy.read_bytes(),
                metadata={"source": "media_replay_proxy", "source_asset_id": source.id, "source_sha256": source.sha256},
            )
            proxy_asset_id = _text(_dict(uploaded.get("asset")).get("id"), 50)
    silence_proc = await asyncio.to_thread(
        subprocess.run,
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(source_path), "-af", "silencedetect=noise=-35dB:d=0.35", "-f", "null", "-"],
        capture_output=True,
        timeout=300,
        check=False,
    )
    silence_text = silence_proc.stderr.decode("utf-8", "ignore")
    starts = [float(item) for item in re.findall(r"silence_start:\s*([0-9.]+)", silence_text)]
    ends = [float(item) for item in re.findall(r"silence_end:\s*([0-9.]+)", silence_text)]
    silence_ranges = []
    for index, start in enumerate(starts):
        silence_ranges.append({"start_seconds": round(start, 3), "end_seconds": round(ends[index] if index < len(ends) else duration, 3)})
    evidence = {
        "policy_version": REPLAY_POLICY_VERSION,
        "source_sha256": source.sha256,
        "proxy_asset_id": proxy_asset_id or None,
        "keyframe_times_seconds": [round(duration * index / 11, 3) for index in range(12)],
        "silence_ranges": silence_ranges,
        "audio_activity_source": "ffmpeg_silencedetect" if silence_proc.returncode == 0 else "unavailable",
    }
    source.metadata_json = {**metadata, "media_replay_analysis_cache": evidence}
    await db.flush()
    return evidence


def _normalize_replay_agent_segments(
    raw: dict[str, Any],
    ranges: list[dict[str, Any]],
    *,
    required: bool,
) -> list[dict[str, Any]]:
    candidates = {
        int(item.get("index")): item
        for item in _list(raw.get("segments"))
        if isinstance(item, dict) and str(item.get("index", "")).lstrip("-").isdigit()
    }
    normalized: list[dict[str, Any]] = []
    missing: list[int] = []
    for source_range in ranges:
        index = int(source_range["index"])
        item = _dict(candidates.get(index))
        visual = _clean_prompt_text(
            item.get("visual_prompt"),
            field=f"segments[{index}].visual_prompt",
        )
        if not visual:
            missing.append(index)
            continue
        if _REFERENCE_TOKEN_RE.search(visual):
            raise AppError(
                "MEDIA_REPLAY_AGENT_OUTPUT_INVALID",
                422,
                {"segment_index": index, "detail": "素材引用占位符由服务端装配，Agent 不得自行编号"},
            )
        normalized.append({
            "index": index,
            "start_seconds": float(source_range["start_seconds"]),
            "end_seconds": float(source_range["end_seconds"]),
            "duration_seconds": float(source_range["duration_seconds"]),
            "visual_prompt": visual,
            "speaker": _text(item.get("speaker"), 80),
            "dialogue": _text(item.get("dialogue"), 4000),
        })
    if required and missing:
        raise AppError(
            "MEDIA_REPLAY_AGENT_OUTPUT_INVALID",
            422,
            {"missing_segment_indexes": missing, "editable_output": raw},
        )
    return normalized


async def _replay_agent_plan(
    db: AsyncSession,
    user: User,
    project: Project,
    run: ProjectRun,
    *,
    mode: str,
    duration: float,
    source_fact: dict[str, Any],
    ranges: list[dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], str, dict[str, Any], list[dict[str, Any]]]:
    request = {
        "task": "基于视觉前置事实生成完整视频复刻方案，不得假装直接看到了未提供的画面。",
        "mode": mode,
        "source_duration_seconds": duration,
        "operator_request": _text(payload.get("request") or payload.get("visual_prompt"), 12000),
        "operator_controls": {
            key: payload.get(key)
            for key in (
                "replace_people", "remove_text", "product_mode", "audio_mode",
                "keep_scene", "keep_camera_motion", "replacement_items",
            )
            if key in payload
        },
        "vision_facts": source_fact,
        "service_owned_segments": ranges,
        "constraints": {
            "segment_boundaries_are_immutable": True,
            "do_not_emit_reference_placeholders": True,
            "full_structure_requires_one_visual_prompt_per_segment": mode == "full_structure",
            "local_replace_must_not_redesign_pixels_outside_masks": mode == "local_replace",
        },
    }
    contract = (
        "基于 vision_facts 而不是常识猜测。输出字段：analysis（对象，含 content_type、hook、beat_table、"
        "viewpoint、composition、wardrobe、subject_relations）、copy_points（数组）、avoid_points（数组）、"
        "segments（数组）、continuation_plan（数组）、acceptance_checks（数组）、assumptions（数组）、warnings（数组）。"
        "segments 必须严格对应 service_owned_segments 的 index/start_seconds/end_seconds；每项包含 index、"
        "start_seconds、end_seconds、visual_prompt、speaker、dialogue。visual_prompt 写该段可编辑画面要求，"
        "不得包含 <Picture N>/<Video N>/<Audio N>，引用编号由服务端添加。"
    )
    runtime, model, raw = await _call_agent_json(
        db, user, project, run,
        agent_key="video_replay_h3",
        feature="video_replay_analyze",
        prompt=json.dumps(request, ensure_ascii=False),
        runtime_contract=contract,
        temperature=0.15,
        max_output_tokens=10000,
    )
    segments = _normalize_replay_agent_segments(raw, ranges, required=mode == "full_structure")
    return runtime, model, raw, segments


async def _replay_analyze(db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    asset = await db.get(ProjectRunAsset, _text(payload.get("source_asset_id"), 50))
    if asset is None or asset.project_run_id != run.id or not str(asset.mime_type or "").lower().startswith("video/"):
        raise AppError("MEDIA_REPLAY_SOURCE_VIDEO_REQUIRED", 422)
    duration = await _reference_asset_duration(db, asset)
    if duration is None:
        raise AppError("MEDIA_REPLAY_DURATION_UNAVAILABLE", 422)
    mode = _text(payload.get("mode"), 40) or "full_structure"
    if mode not in {"full_structure", "local_replace"}:
        raise AppError("MEDIA_REPLAY_MODE_INVALID", 422)
    requested_config = {
        "replace_people": bool(payload.get("replace_people", False)),
        "remove_text": bool(payload.get("remove_text", False)),
        "product_mode": _text(payload.get("product_mode"), 20) or "keep",
        "audio_mode": _text(payload.get("audio_mode"), 20) or "redub",
        "keep_scene": bool(payload.get("keep_scene", True)),
        "keep_camera_motion": bool(payload.get("keep_camera_motion", True)),
        "replacement_items": _normalize_local_replacement_items(payload.get("replacement_items"), float(duration))
        if mode == "local_replace" else [],
        "prompt_contract_version": (
            "material-fde-h3-v1"
            if _text(payload.get("_prompt_contract_version"), 80) == "material-fde-h3-v1"
            else EXACT_PROMPT_POLICY_VERSION
        ),
    }
    if requested_config["product_mode"] not in {"keep", "replace", "remove"}:
        raise AppError("MEDIA_REPLAY_PRODUCT_MODE_INVALID", 422)
    if requested_config["audio_mode"] not in {"original", "redub", "partial_redub", "silent"}:
        raise AppError("MEDIA_REPLAY_AUDIO_MODE_INVALID", 422)
    if mode != "local_replace" and requested_config["audio_mode"] == "partial_redub":
        raise AppError("MEDIA_REPLAY_AUDIO_MODE_INVALID", 422)
    if mode == "local_replace" and (
        requested_config["replace_people"]
        or requested_config["keep_scene"] is False
        or requested_config["keep_camera_motion"] is False
    ):
        raise AppError("MEDIA_LOCAL_REPLACE_CONTROL_CONFLICT", 422)
    replay_agent = await _agent_runtime(db, user, project, "video_replay_h3")
    idempotency_key = _text(payload.get("idempotency_key"), 128) or _json_hash({
        "run": run.id,
        "asset": asset.sha256,
        "mode": mode,
        "policy": REPLAY_POLICY_VERSION,
        "agent_prompt_sha256": replay_agent["prompt_sha256"],
        "request": _text(payload.get("request") or payload.get("visual_prompt"), 12000),
        "config": requested_config,
    })
    existing = (
        await db.execute(
            select(MediaReplayProject).where(
                MediaReplayProject.project_id == project.id,
                MediaReplayProject.idempotency_key == idempotency_key,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        _, existing_segments = await _replay_rows(db, run, existing.id)
        return {"cache_hit": True, "replay": _serialize_replay(existing, existing_segments)}
    source_roles = [{"asset_id": asset.id, "role": "motion_reference", "technical_role": "reference_video", "purpose": "完整源视频结构分析", "role_locked": True}]
    understanding = await _understand_source_assets(db, user, run, source_roles)
    proxy_evidence = await _build_replay_proxy_and_audio_map(db, user, run, asset, float(duration))
    source_fact = next((item for item in _list(_dict(understanding).get("assets")) if _dict(item).get("asset_id") == asset.id), {})
    shots = _list(_dict(source_fact).get("shots"))
    ranges = plan_replay_segments(duration, shots)
    replay_agent, agent_model, agent_output, agent_segments = await _replay_agent_plan(
        db, user, project, run,
        mode=mode,
        duration=float(duration),
        source_fact=_dict(source_fact),
        ranges=ranges,
        payload=payload,
    )
    agent_segment_by_index = {item["index"]: item for item in agent_segments}
    config = requested_config
    now = now_bjt()
    row = MediaReplayProject(
        id=_new_id("mrp"), project_id=project.id, project_run_id=run.id,
        department_id=_department_id(run, project), requested_by=_user_id(user),
        source_asset_id=asset.id, mode=mode, status="analyzed",
        source_duration_seconds=float(duration), input_fingerprint=_json_hash({"asset": asset.sha256, "policy": REPLAY_POLICY_VERSION}),
        analysis_json={
            "source_understanding": understanding or {}, "shots": shots,
            "proxy_asset_id": proxy_evidence.get("proxy_asset_id"),
            "keyframe_times_seconds": proxy_evidence.get("keyframe_times_seconds") or [],
            "silence_ranges": proxy_evidence.get("silence_ranges") or [],
            "audio_activity_source": proxy_evidence.get("audio_activity_source"),
            "coverage_seconds": float(duration), "coverage_ratio": 1.0,
            "agent": {
                **_public_agent(replay_agent, include_prompt=False),
                "actual_model": agent_model,
            },
            "agent_analysis": agent_output,
        },
        config_json=config, idempotency_key=idempotency_key, created_at=now, updated_at=now,
    )
    db.add(row)
    await db.flush()
    segments: list[MediaReplaySegment] = []
    for item in ranges:
        agent_segment = _dict(agent_segment_by_index.get(int(item["index"])))
        segment = MediaReplaySegment(
            id=_new_id("mrs"), replay_project_id=row.id, segment_index=item["index"],
            start_seconds=item["start_seconds"], end_seconds=item["end_seconds"], duration_seconds=item["duration_seconds"],
            status="analyzed", prompt_json={
                "source_range": item,
                "editable_visual_prompt": _text(agent_segment.get("visual_prompt"), MAX_PROMPT_CHARS),
                "agent_key": replay_agent["agent_key"],
                "agent_version": replay_agent["version"],
                "agent_prompt_sha256": replay_agent["prompt_sha256"],
                "agent_model": agent_model,
            },
            dialogue_json={
                "speaker": _text(agent_segment.get("speaker"), 80),
                "text": _text(agent_segment.get("dialogue"), 4000),
                "audio_mode": "redub" if _text(agent_segment.get("dialogue"), 4000) else "silent",
                "suggested_max_characters": max(0, int(item["duration_seconds"] * 4)),
            },
            seam_analysis_json={}, created_at=now, updated_at=now,
        )
        db.add(segment)
        segments.append(segment)
    await db.flush()
    return {"cache_hit": False, "replay": _serialize_replay(row, segments)}


async def _replay_update(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    row, segments = await _replay_rows(db, run, payload.get("replay_project_id"))
    config = {**_dict(row.config_json), **_dict(payload.get("config"))}
    if config.get("audio_mode") not in {"original", "redub", "partial_redub", "silent"}:
        raise AppError("MEDIA_REPLAY_AUDIO_MODE_INVALID", 422)
    if row.mode != "local_replace" and config.get("audio_mode") == "partial_redub":
        raise AppError("MEDIA_REPLAY_AUDIO_MODE_INVALID", 422)
    if config.get("product_mode") not in {"keep", "replace", "remove"}:
        raise AppError("MEDIA_REPLAY_PRODUCT_MODE_INVALID", 422)
    if row.mode == "local_replace" and (
        config.get("replace_people")
        or config.get("keep_scene") is False
        or config.get("keep_camera_motion") is False
    ):
        raise AppError(
            "MEDIA_LOCAL_REPLACE_CONTROL_CONFLICT",
            422,
            {"detail": "local replacement preserves people, scene and camera motion outside the selected masks"},
        )
    product_asset_ids = [str(item) for item in _list(config.get("product_asset_ids")) if str(item).strip()]
    if len(product_asset_ids) > 2:
        raise AppError("MEDIA_REPLAY_PRODUCT_ASSET_LIMIT", 422, {"maximum": 2})
    for asset_id in product_asset_ids:
        asset = await db.get(ProjectRunAsset, asset_id)
        if asset is None or asset.project_run_id != run.id or not str(asset.mime_type or "").lower().startswith("image/"):
            raise AppError("MEDIA_REPLAY_PRODUCT_ASSET_INVALID", 422, {"asset_id": asset_id})
    config["product_asset_ids"] = product_asset_ids
    if config.get("product_mode") == "replace" and not product_asset_ids:
        raise AppError("MEDIA_REPLAY_PRODUCT_ASSET_REQUIRED", 422)
    if row.mode == "local_replace":
        config["replacement_items"] = _normalize_local_replacement_items(
            config.get("replacement_items"), float(row.source_duration_seconds)
        )
        audio_targets = [
            item for item in config["replacement_items"]
            if item["scope"] in {"visual_audio", "audio_only"}
        ]
        if audio_targets and config.get("audio_mode") not in {"partial_redub", "redub"}:
            raise AppError(
                "MEDIA_LOCAL_REPLACEMENT_AUDIO_MODE_REQUIRED",
                422,
                {"detail": "口播替换需要选择“只替换指定口播”或“整段重新配音”"},
            )
        if not config["replacement_items"] and not config.get("remove_text") and config.get("product_mode") == "keep":
            raise AppError(
                "MEDIA_LOCAL_REPLACEMENT_TARGET_REQUIRED",
                422,
                {"detail": "请填写至少一个原内容和新内容，或选择去除文字 / 替换商品"},
            )
    row.config_json = config
    changes = {str(item.get("segment_id")): _dict(item) for item in _list(payload.get("segments")) if isinstance(item, dict)}
    for segment in segments:
        change = changes.get(segment.id)
        if not change:
            continue
        visual = _clean_prompt_text(change.get("visual_prompt") or _dict(segment.prompt_json).get("editable_visual_prompt"), field="visual_prompt", required=True)
        dialogue = _clean_prompt_text(change.get("dialogue"), field="dialogue")
        segment.prompt_json = {**_dict(segment.prompt_json), "editable_visual_prompt": visual}
        segment.dialogue_json = {
            **_dict(segment.dialogue_json),
            "speaker": _text(change.get("speaker"), 80),
            "text": dialogue,
            "audio_mode": "redub" if dialogue else "silent",
        }
        segment.updated_at = now_bjt()
    if row.mode == "local_replace":
        local_sources = [{
            "asset_id": row.source_asset_id,
            "technical_role": "reference_video",
            "role": "motion_reference",
            "purpose": "需要保持遮罩外像素不变的完整源视频",
        }, *_replay_product_sources(config)]
        local_preview = compose_exact_h3_prompt(_local_replace_instruction(config), "", local_sources)
        row.analysis_json = {
            **_dict(row.analysis_json),
            "execution_prompt_preview": local_preview["prompt"],
            "execution_prompt_sha256": local_preview["sha256"],
            "execution_kind": "video_local_edit",
        }
        for segment in segments:
            segment.prompt_json = {
                **_dict(segment.prompt_json),
                "final_prompt_preview": None,
                "prompt_preview_sha256": None,
            }
        row.status = "configured"
        row.updated_at = now_bjt()
        await db.flush()
        return {"replay": _serialize_replay(row, segments)}

    controls = _replay_control_prompt(config)
    for segment in segments:
        visual = _clean_prompt_text(
            _dict(segment.prompt_json).get("editable_visual_prompt"),
            field="visual_prompt",
            required=True,
        )
        preview_sources = _replay_preview_sources(segment, config)
        preview = compose_exact_h3_prompt(f"{visual}\n{controls}", "", preview_sources)
        change = changes.get(segment.id) or {}
        final_prompt = _clean_prompt_text(change.get("final_prompt"), field="final_prompt") or preview["prompt"]
        if len(final_prompt) > MAX_PROMPT_CHARS:
            raise AppError("MEDIA_PROMPT_TOO_LONG", 422, {"segment_id": segment.id, "maximum": MAX_PROMPT_CHARS})
        _validate_reference_tokens(final_prompt, preview["bindings"])
        segment.prompt_json = {
            **_dict(segment.prompt_json),
            "base_prompt_preview": preview["prompt"],
            "final_prompt_preview": final_prompt,
            "prompt_preview_sha256": _prompt_hash(final_prompt),
            "user_prompt_edited": final_prompt != preview["prompt"],
        }
    row.status = "configured"
    row.updated_at = now_bjt()
    await db.flush()
    return {"replay": _serialize_replay(row, segments)}


async def _source_clip_asset(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    source: ProjectRunAsset,
    segment: MediaReplaySegment,
) -> str:
    if segment.source_clip_asset_id:
        return segment.source_clip_asset_id
    source_path = project_service._project_run_asset_abs_path(source)
    ffmpeg = _core()._media_quality_ffmpeg_executable()
    source_range = _dict(_dict(segment.prompt_json).get("source_range"))
    overlap = max(0.0, min(0.5, float(source_range.get("overlap_seconds") or 0)))
    clip_start = max(0.0, float(segment.start_seconds) - overlap)
    clip_duration = float(segment.end_seconds) - clip_start
    with tempfile.TemporaryDirectory(prefix="sf-replay-") as temp_dir:
        output = Path(temp_dir) / f"replay-{segment.segment_index + 1:02d}.mp4"
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{clip_start:.3f}",
            "-i", str(source_path), "-t", f"{clip_duration:.3f}", "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", "-y", str(output),
        ]
        proc = await asyncio.to_thread(subprocess.run, command, capture_output=True, timeout=180, check=False)
        if proc.returncode != 0 or not output.is_file():
            raise AppError("MEDIA_REPLAY_CLIP_FAILED", 500, {"segment_id": segment.id, "detail": proc.stderr.decode("utf-8", "ignore")[-1000:]})
        uploaded = await project_service.upload_project_run_asset(
            db, user, run.id, file_name=output.name, mime_type="video/mp4", content=output.read_bytes(),
            metadata={"source": "media_replay_segment", "replay_segment_id": segment.id, "source_asset_id": source.id, "start_seconds": clip_start, "end_seconds": segment.end_seconds, "overlap_seconds": overlap},
        )
    asset_id = _text(_dict(uploaded.get("asset")).get("id"), 50)
    if not asset_id:
        raise AppError("MEDIA_REPLAY_CLIP_FAILED", 500)
    segment.source_clip_asset_id = asset_id
    await db.flush()
    return asset_id


async def _continuity_anchor_asset(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    segment: MediaReplaySegment,
    previous_job: MediaGenerationJob,
) -> str:
    """Extract one tail frame instead of reusing the whole prior video.

    H3 limits the aggregate duration of reference videos. Reusing both the
    current 14-second source clip and the prior 14-second generated clip would
    exceed that limit. A still tail-frame preserves the continuity cue without
    consuming reference-video duration.
    """

    previous_asset_id = _text(previous_job.result_asset_id, 50)
    if not previous_asset_id:
        raise AppError("MEDIA_REPLAY_CONTINUITY_ANCHOR_PENDING", 409, {"segment_id": segment.id})
    previous_asset = await db.get(ProjectRunAsset, previous_asset_id)
    if previous_asset is None:
        raise AppError("MEDIA_REPLAY_CONTINUITY_ANCHOR_PENDING", 409, {"segment_id": segment.id})
    prompt_json = _dict(segment.prompt_json)
    cached_asset_id = _text(prompt_json.get("continuity_anchor_asset_id"), 50)
    cached_source_sha = _text(prompt_json.get("continuity_anchor_source_sha256"), 64)
    source_sha = _text(previous_job.result_sha256 or previous_asset.sha256, 64)
    if cached_asset_id and cached_source_sha and cached_source_sha == source_sha:
        cached = await db.get(ProjectRunAsset, cached_asset_id)
        if cached is not None:
            return cached.id
    previous_path = project_service._project_run_asset_abs_path(previous_asset)
    ffmpeg = _core()._media_quality_ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="sf-replay-anchor-") as temp_dir:
        output = Path(temp_dir) / f"replay-{segment.segment_index + 1:02d}-anchor.jpg"
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-sseof", "-0.12",
            "-i", str(previous_path), "-frames:v", "1", "-q:v", "2", "-y", str(output),
        ]
        proc = await asyncio.to_thread(subprocess.run, command, capture_output=True, timeout=90, check=False)
        if proc.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
            raise AppError(
                "MEDIA_REPLAY_CONTINUITY_ANCHOR_FAILED",
                500,
                {"segment_id": segment.id, "detail": proc.stderr.decode("utf-8", "ignore")[-1000:]},
            )
        uploaded = await project_service.upload_project_run_asset(
            db,
            user,
            run.id,
            file_name=output.name,
            mime_type="image/jpeg",
            content=output.read_bytes(),
            metadata={
                "source": "media_replay_continuity_anchor",
                "replay_segment_id": segment.id,
                "source_job_id": previous_job.id,
                "source_asset_id": previous_asset.id,
                "source_sha256": source_sha,
                "frame": "tail",
            },
        )
    anchor_asset_id = _text(_dict(uploaded.get("asset")).get("id"), 50)
    if not anchor_asset_id:
        raise AppError("MEDIA_REPLAY_CONTINUITY_ANCHOR_FAILED", 500, {"segment_id": segment.id})
    segment.prompt_json = {
        **prompt_json,
        "continuity_anchor_asset_id": anchor_asset_id,
        "continuity_anchor_source_job_id": previous_job.id,
        "continuity_anchor_source_sha256": source_sha,
    }
    await db.flush()
    return anchor_asset_id


async def _submit_replay_segment(
    db: AsyncSession,
    user: User,
    project: Project,
    run: ProjectRun,
    replay: MediaReplayProject,
    segment: MediaReplaySegment,
    source: ProjectRunAsset,
    config: dict[str, Any],
    previous_job: MediaGenerationJob | None,
) -> MediaGenerationJob:
    """Create one segment only after its continuity anchor really exists."""

    clip_asset_id = await _source_clip_asset(db, user, run, source, segment)
    visual = _clean_prompt_text(
        _dict(segment.prompt_json).get("editable_visual_prompt"),
        field="visual_prompt",
        required=True,
    )
    dialogue = _text(_dict(segment.dialogue_json).get("text"), 6000)
    audio_mode = str(config.get("audio_mode") or "redub")
    visible_visual = f"{visual}\n{_replay_control_prompt(config)}"
    source_items = [{
        "asset_id": clip_asset_id,
        "technical_role": "reference_video",
        "role": "motion_reference",
        "purpose": f"源视频 {segment.start_seconds:.2f}–{segment.end_seconds:.2f} 秒完整片段",
    }]
    references = [{"asset_id": clip_asset_id, "role": "reference_video", "purpose": "完整源片段"}]
    if previous_job is not None:
        previous_asset_id = await _continuity_anchor_asset(db, user, run, segment, previous_job)
        source_items.append({
            "asset_id": previous_asset_id,
            "technical_role": "reference_image",
            "role": "continuity_anchor",
            "purpose": "上一生成片段的尾帧锚点，用于保持人物、场景、动作方向和光线连续",
        })
        references.append({
            "asset_id": previous_asset_id,
            "role": "reference_image",
            "purpose": "上一生成片段尾帧连续性锚点",
        })
    for product_source in _replay_product_sources(config):
        source_items.append(product_source)
        references.append({
            "asset_id": product_source["asset_id"],
            "role": "reference_image",
            "purpose": product_source["purpose"],
        })
    assembled_default = compose_exact_h3_prompt(visible_visual, "", source_items)
    final_prompt = _clean_prompt_text(
        _dict(segment.prompt_json).get("final_prompt_preview"),
        field="final_prompt",
    ) or assembled_default["prompt"]
    _validate_reference_tokens(final_prompt, assembled_default["bindings"])
    assembled = {
        **assembled_default,
        "prompt": final_prompt,
        "sha256": _prompt_hash(final_prompt),
    }
    expected_preview_sha = _text(_dict(segment.prompt_json).get("prompt_preview_sha256"), 64)
    if expected_preview_sha and expected_preview_sha != assembled["sha256"]:
        raise AppError(
            "MEDIA_PROMPT_HASH_MISMATCH",
            409,
            {"segment_id": segment.id, "preview_sha256": expected_preview_sha, "execution_sha256": assembled["sha256"]},
        )
    params = {
        **_core().MEDIA_DEFAULT_PRESET,
        "frames": production_frames_for_duration(
            max(4, int(math.ceil(segment.duration_seconds))), fps=24
        ),
        "audio_enabled": audio_mode == "original",
        "delivery_duration_seconds": segment.duration_seconds,
    }
    dialogue_delivery = {
        "requested": True,
        "policy_version": _core().SPEECH_DELIVERY_POLICY_VERSION,
        "visual_audio_enabled": False,
        "delivery_mode": "auto_after_visual_gate",
        "auto_finalize": True,
        "segment_speaker": _dict(segment.dialogue_json).get("speaker") or "",
    } if audio_mode == "redub" and dialogue else None
    plan = _exact_plan(
        assembled["prompt"],
        mode="reference_replay",
        params=params,
        origin="full_structure_replay",
        contract_version=(
            "material-fde-h3-v1"
            if _text(config.get("prompt_contract_version"), 80) == "material-fde-h3-v1"
            else EXACT_PROMPT_POLICY_VERSION
        ),
        metadata={
            "replay_policy_version": REPLAY_POLICY_VERSION,
            "source_range": _dict(segment.prompt_json).get("source_range") or {
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
            },
            "replay_config": config,
            "requested_audio_prompt": dialogue if dialogue_delivery else "",
            "dialogue_delivery": dialogue_delivery or {},
            "redub": {
                "speaker": _dict(segment.dialogue_json).get("speaker"),
                "text": dialogue,
            } if dialogue_delivery else None,
            "audio_mode": audio_mode,
            "blank_dialogue_is_silent": audio_mode != "original" and not bool(dialogue),
            "continuity_anchor_job_id": previous_job.id if previous_job else None,
            "source_bindings": assembled["bindings"],
        },
    )
    result = await _core()._submit_job(
        db,
        user,
        project,
        run,
        {
            "selected_source": "manual",
            "final_plan": plan,
            "brief": {
                "request": visual,
                "script": dialogue,
                "duration_seconds": segment.duration_seconds,
                "ratio": "9:16",
            },
            "mode": "reference_replay",
            "params": params,
            "reference_assets": references,
            "source_roles": [
                {**item, "role_locked": True} for item in source_items
            ],
            "prompt_preview_sha256": assembled["sha256"],
            "replay_project_id": replay.id,
            "replay_segment_id": segment.id,
            "depends_on_job_id": previous_job.id if previous_job else None,
            "sequence_index": segment.segment_index,
            "creative_option": "full_structure_replay",
            "business_title": f"全时长结构复刻 · 第 {segment.segment_index + 1} 段",
            "idempotency_key": _json_hash({
                "replay": replay.id,
                "segment": segment.id,
                "prompt": assembled["sha256"],
                "anchor": previous_job.result_sha256 if previous_job else None,
            }),
        },
        allow_exact_prompt_contract=True,
    )
    job = await db.get(MediaGenerationJob, result["job"]["id"])
    if job is None:
        raise AppError("MEDIA_REPLAY_SEGMENT_SUBMIT_FAILED", 500, {"segment_id": segment.id})
    segment.media_job_id = job.id
    segment.status = job.status
    segment.prompt_json = {
        **_dict(segment.prompt_json),
        "final_prompt": assembled["prompt"],
        "prompt_sha256": assembled["sha256"],
    }
    segment.updated_at = now_bjt()
    await db.flush()
    return job


async def _replay_submit(db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    row, segments = await _replay_rows(db, run, payload.get("replay_project_id"))
    if row.mode == "local_replace":
        config = _dict(row.config_json)
        config["replacement_items"] = _normalize_local_replacement_items(
            config.get("replacement_items"), float(row.source_duration_seconds)
        )
        if config.get("audio_mode") not in {"original", "redub", "partial_redub", "silent"}:
            raise AppError("MEDIA_REPLAY_AUDIO_MODE_INVALID", 422)
        if config.get("product_mode") not in {"keep", "replace", "remove"}:
            raise AppError("MEDIA_REPLAY_PRODUCT_MODE_INVALID", 422)
        audio_targets = [
            item for item in config["replacement_items"]
            if item["scope"] in {"visual_audio", "audio_only"}
        ]
        if audio_targets and config.get("audio_mode") not in {"partial_redub", "redub"}:
            raise AppError(
                "MEDIA_LOCAL_REPLACEMENT_AUDIO_MODE_REQUIRED",
                422,
                {"detail": "口播替换需要选择“只替换指定口播”或“整段重新配音”"},
            )
        if not config["replacement_items"] and not config.get("remove_text") and config.get("product_mode") == "keep":
            raise AppError(
                "MEDIA_LOCAL_REPLACEMENT_TARGET_REQUIRED",
                422,
                {"detail": "请填写至少一个原内容和新内容，或选择去除文字 / 替换商品"},
            )
        row.config_json = config
        capable = (
            await db.execute(
                select(OpenClawInstance)
                .where(OpenClawInstance.is_active.is_(True))
                .where(OpenClawInstance.agent_purpose.in_(["media", "mixed"]))
            )
        ).scalars().all()
        supported = any(_node_supports_local_replace(node) for node in capable)
        if not supported:
            raise AppError(
                "MEDIA_LOCAL_REPLACE_CAPABILITY_UNAVAILABLE",
                409,
                {
                    "required_profile": LOCAL_REPLACE_PROFILE,
                    "reason": "当前媒体节点未安装 SAM2/ProPainter 局部替换能力，任务未创建也未派单。",
                },
            )
        source = await db.get(ProjectRunAsset, row.source_asset_id)
        if source is None:
            raise AppError("MEDIA_REPLAY_SOURCE_VIDEO_REQUIRED", 422)
        source_items = [{"asset_id": source.id, "technical_role": "reference_video", "role": "motion_reference", "purpose": "需要保持遮罩外像素不变的完整源视频"}]
        source_items.extend(_replay_product_sources(config))
        assembled = compose_exact_h3_prompt(_local_replace_instruction(config), "", source_items)
        preview_sha = _text(_dict(row.analysis_json).get("execution_prompt_sha256"), 64)
        if preview_sha and preview_sha != assembled["sha256"]:
            raise AppError(
                "MEDIA_PROMPT_HASH_MISMATCH",
                409,
                {"replay_project_id": row.id, "preview_sha256": preview_sha, "execution_sha256": assembled["sha256"]},
            )
        params = {**_core().MEDIA_DEFAULT_PRESET, "audio_enabled": config.get("audio_mode") != "silent"}
        plan = _exact_plan(
            assembled["prompt"], mode="video_local_edit", params=params, origin="local_replacement",
            contract_version=(
                "material-fde-h3-v1"
                if _text(config.get("prompt_contract_version"), 80) == "material-fde-h3-v1"
                else EXACT_PROMPT_POLICY_VERSION
            ),
            metadata={
                "local_edit_profile": LOCAL_REPLACE_PROFILE,
                "local_edit_config": config,
                "immutable_outside_mask": True,
                "source_asset_sha256": source.sha256,
            },
        )
        references = [{"asset_id": source.id, "role": "reference_video", "purpose": "完整源视频"}] + [
            {"asset_id": item["asset_id"], "role": "reference_image", "purpose": item["purpose"]}
            for item in _replay_product_sources(config)
        ]
        result = await _core()._submit_job(db, user, project, run, {
            "selected_source": "manual", "final_plan": plan,
            "brief": {"request": assembled["prompt"], "duration_seconds": row.source_duration_seconds},
            "mode": "video_local_edit", "params": params,
            "reference_assets": references,
            "source_roles": source_items,
            "prompt_preview_sha256": assembled["sha256"],
            "replay_project_id": row.id,
            "creative_option": "local_replacement",
            "business_title": "原片局部替换",
            "idempotency_key": _json_hash({"replay": row.id, "config": config, "prompt": assembled["sha256"]}),
        }, allow_exact_prompt_contract=True)
        row.status = result["job"]["status"]
        row.updated_at = now_bjt()
        await db.flush()
        return {"jobs": [result["job"]], "count": 1, "coverage_ratio": 1.0, "replay": _serialize_replay(row, segments)}
    source = await db.get(ProjectRunAsset, row.source_asset_id)
    if source is None:
        raise AppError("MEDIA_REPLAY_SOURCE_VIDEO_REQUIRED", 422)
    config = _dict(row.config_json)
    jobs = [
        job for job in [
            await db.get(MediaGenerationJob, segment.media_job_id)
            if segment.media_job_id else None
            for segment in segments
        ]
        if job is not None
    ]
    if not jobs and segments:
        jobs.append(await _submit_replay_segment(
            db, user, project, run, row, segments[0], source, config, None
        ))
    row.status = "queued"
    row.updated_at = now_bjt()
    await db.flush()
    return {
        "jobs": [_core().serialize_job(job, await _core()._latest_attempt(db, job.id)) for job in jobs],
        "count": len(segments),
        "submitted_count": len(jobs),
        "coverage_ratio": 1.0,
        "replay": _serialize_replay(row, segments),
    }


async def _create_replay_preview(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    replay: MediaReplayProject,
    segments: list[MediaReplaySegment],
) -> ProjectRunAsset | None:
    assets: list[tuple[ProjectRunAsset, MediaReplaySegment]] = []
    for segment in segments:
        job = await db.get(MediaGenerationJob, segment.media_job_id) if segment.media_job_id else None
        asset = await db.get(ProjectRunAsset, job.result_asset_id) if job and job.result_asset_id else None
        if asset is None:
            return None
        assets.append((asset, segment))
    ffmpeg = _core()._media_quality_ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="sf-replay-preview-") as temp_dir:
        normalized_paths: list[Path] = []
        for index, (asset, segment) in enumerate(assets):
            source_path = project_service._project_run_asset_abs_path(asset)
            probe = _dict(_dict(asset.metadata_json).get("media_probe"))
            if not probe.get("streams"):
                probe = await asyncio.to_thread(
                    project_service._probe_project_media_file,
                    source_path,
                    asset.mime_type,
                )
            has_audio = any(
                _dict(stream).get("codec_type") == "audio"
                for stream in _list(probe.get("streams"))
            )
            normalized = Path(temp_dir) / f"normalized-{index + 1:02d}.mp4"
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source_path)]
            if not has_audio:
                command.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
            command.extend([
                "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0",
                "-t", f"{float(segment.duration_seconds):.3f}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
                "-c:a", "aac", "-ar", "48000", "-ac", "2",
            ])
            if has_audio:
                command.extend(["-af", "apad"])
            command.extend(["-movflags", "+faststart", "-y", str(normalized)])
            normalize_proc = await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                timeout=600,
                check=False,
            )
            if normalize_proc.returncode != 0 or not normalized.is_file():
                replay.analysis_json = {
                    **_dict(replay.analysis_json),
                    "preview_error": normalize_proc.stderr.decode("utf-8", "ignore")[-1000:],
                    "preview_failed_segment_id": segment.id,
                }
                return None
            normalized_paths.append(normalized)
        concat_file = Path(temp_dir) / "inputs.txt"
        concat_file.write_text(
            "\n".join(
                "file '" + str(path).replace("'", "'\\''") + "'"
                for path in normalized_paths
            ),
            encoding="utf-8",
        )
        output = Path(temp_dir) / f"{replay.id}-preview.mp4"
        proc = await asyncio.to_thread(
            subprocess.run,
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", "-y", str(output)],
            capture_output=True,
            timeout=900,
            check=False,
        )
        if proc.returncode != 0 or not output.is_file():
            replay.analysis_json = {**_dict(replay.analysis_json), "preview_error": proc.stderr.decode("utf-8", "ignore")[-1000:]}
            return None
        uploaded = await project_service.upload_project_run_asset(
            db, user, run.id, file_name=output.name, mime_type="video/mp4", content=output.read_bytes(),
            metadata={"source": "media_full_replay_preview", "replay_project_id": replay.id, "preview_only": True},
        )
    return await db.get(ProjectRunAsset, _text(_dict(uploaded.get("asset")).get("id"), 50))


async def advance_replays_once(db: AsyncSession, *, limit: int = 20) -> int:
    """Advance replay lineage and build a complete preview after all segments finish."""

    rows = (
        await db.execute(
            select(MediaReplayProject)
            .where(MediaReplayProject.status.in_(["queued", "running"]))
            .order_by(MediaReplayProject.updated_at.asc())
            .limit(max(1, min(int(limit), 50)))
        )
    ).scalars().all()
    advanced = 0
    for replay in rows:
        run = await db.get(ProjectRun, replay.project_run_id)
        user = await db.get(User, replay.requested_by) if replay.requested_by else None
        if run is None or user is None:
            replay.status = "failed"
            replay.analysis_json = {**_dict(replay.analysis_json), "error": "replay run or owner is missing"}
            advanced += 1
            continue
        _, segments = await _replay_rows(db, run, replay.id)
        if replay.mode == "local_replace":
            job = (
                await db.execute(
                    select(MediaGenerationJob)
                    .where(MediaGenerationJob.replay_project_id == replay.id)
                    .order_by(MediaGenerationJob.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if job and job.status == "awaiting_review" and job.result_asset_id:
                replay.preview_asset_id = job.result_asset_id
                replay.status = "awaiting_review"
                replay.updated_at = now_bjt()
                advanced += 1
            elif job and job.status in {"failed", "cancelled", "rejected"}:
                replay.status = "failed"
                replay.analysis_json = {**_dict(replay.analysis_json), "error": job.error or job.status}
                replay.updated_at = now_bjt()
                advanced += 1
            continue
        jobs = [await db.get(MediaGenerationJob, segment.media_job_id) if segment.media_job_id else None for segment in segments]
        existing_jobs = [job for job in jobs if job is not None]
        if any(job.status in {"failed", "cancelled", "rejected"} for job in existing_jobs):
            replay.status = "failed"
            replay.analysis_json = {**_dict(replay.analysis_json), "error": "one or more replay segments failed"}
            replay.updated_at = now_bjt()
            advanced += 1
            continue
        for segment, job in zip(segments, jobs):
            if job is not None:
                segment.status = job.status
                segment.updated_at = now_bjt()
        pending_index = next((index for index, job in enumerate(jobs) if job is None), None)
        if pending_index is not None:
            previous_job = jobs[pending_index - 1] if pending_index > 0 else None
            previous_ready = previous_job is None or (
                previous_job.status in {"awaiting_review", "approved", "syncing", "synced"}
                and bool(previous_job.result_asset_id)
            )
            if previous_ready:
                project = await db.get(Project, replay.project_id)
                source = await db.get(ProjectRunAsset, replay.source_asset_id)
                if project is None or source is None:
                    replay.status = "failed"
                    replay.analysis_json = {**_dict(replay.analysis_json), "error": "replay project or source is missing"}
                else:
                    await _submit_replay_segment(
                        db, user, project, run, replay, segments[pending_index], source,
                        _dict(replay.config_json), previous_job,
                    )
                    replay.status = "running"
                replay.updated_at = now_bjt()
                advanced += 1
            else:
                replay.status = "running"
                replay.updated_at = now_bjt()
            continue
        if not jobs or not all(job.status in {"awaiting_review", "approved", "syncing", "synced"} for job in existing_jobs):
            replay.status = "running"
            replay.updated_at = now_bjt()
            continue
        # A segment that requested re-dubbing is not complete merely because
        # the H3 clean plate exists.  Reuse the same delivery gate as review so
        # the stitched preview can never silently omit, misalign, or substitute
        # dialogue audio.
        from .workbench_v2 import dialogue_delivery_gate

        dialogue_pending = any(not dialogue_delivery_gate(job)["passed"] for job in existing_jobs)
        if dialogue_pending:
            replay.status = "running"
            replay.updated_at = now_bjt()
            continue
        from .workbench_v2 import _continuation_seam_analysis

        hard_failures = []
        for index in range(1, len(existing_jobs)):
            previous_asset = await db.get(ProjectRunAsset, existing_jobs[index - 1].result_asset_id)
            current_asset = await db.get(ProjectRunAsset, existing_jobs[index].result_asset_id)
            if previous_asset is None or current_asset is None:
                continue
            seam = await _continuation_seam_analysis(previous_asset, current_asset, {"status": "passed"})
            segments[index].seam_analysis_json = seam
            if seam.get("hard_gate"):
                hard_failures.append({"segment_id": segments[index].id, "analysis": seam})
        preview = await _create_replay_preview(db, user, run, replay, segments)
        replay.preview_asset_id = preview.id if preview else None
        replay.status = "seam_failed" if hard_failures else ("awaiting_review" if preview else "failed")
        replay.analysis_json = {
            **_dict(replay.analysis_json),
            "coverage_ratio": 1.0,
            "missing_segments": [],
            "seam_failures": hard_failures,
            "preview_ready": preview is not None,
        }
        replay.updated_at = now_bjt()
        advanced += 1
    await db.flush()
    return advanced


async def dispatch_v3_capability(
    db: AsyncSession,
    user: User,
    project: Project,
    run: ProjectRun,
    capability: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if capability == "video.create.direct_submit":
        return await _direct_submit(db, user, project, run, payload)
    if capability == "video.prompt.optimize":
        return await _prompt_optimize(db, user, project, run, payload)
    if capability == "video.agent.list":
        return await _agent_list(db, user, project)
    if capability == "video.agent.get":
        runtime = await _agent_runtime(db, user, project, payload.get("agent_key"))
        return {"agent": _public_agent(runtime)}
    if capability == "video.agent.save":
        return await _agent_save(db, user, project, payload)
    if capability == "video.agent.restore":
        return await _agent_restore(db, user, project, payload)
    if capability == "video.agent.invoke":
        return await _agent_invoke(db, user, project, run, payload)
    if capability == "video.strategy.start":
        return await _strategy_start(db, user, project, run, payload)
    if capability == "video.strategy.answer":
        return await _strategy_answer(db, user, run, payload)
    if capability == "video.strategy.compile":
        return await _strategy_compile(db, user, project, run, payload)
    if capability == "video.strategy.submit":
        return await _strategy_submit(db, user, project, run, payload)
    if capability == "video.replay.analyze":
        return await _replay_analyze(db, user, project, run, payload)
    if capability == "video.replay.update":
        return await _replay_update(db, run, payload)
    if capability == "video.replay.submit":
        return await _replay_submit(db, user, project, run, payload)
    if capability == "video.replay.get":
        row, segments = await _replay_rows(db, run, payload.get("replay_project_id"))
        return {"replay": _serialize_replay(row, segments)}
    raise AppError("PROJECT_CAPABILITY_DENIED", 400, {"capability": capability})
