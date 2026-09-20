"""Direct-run capabilities for the hall.

These capabilities are filesystem skills that can be executed directly from the
hall UI through their ``scripts/main.py`` contract wrapper. They deliberately do
not allocate an OpenClaw session.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import ipaddress
import json
import mimetypes
import os
import re
import socket
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import yaml
from fastapi import HTTPException, Response, UploadFile
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.auth.access import role_matches_any
from app.common.exceptions import AppError
from app.common.time_utils import now_bjt
from app.hall.models import DirectCapabilityImageHistory, DirectCapabilityTask
from app.portal import ui_service as portal_ui_service
from app.portal.models import UserSkillUIPreference
from app.users.role_matrix import normalize_role


_CAPABILITY_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_UPLOAD_ROOT = Path(os.environ.get("SKILLFORGE_DIRECT_UPLOAD_DIR", "/tmp/skillforge-direct-capability-uploads"))
_MAX_REFERENCE_UPLOAD_BYTES = 10 * 1024 * 1024
_MAX_IMAGE_DOWNLOAD_BYTES = 50 * 1024 * 1024
_IMAGE_HISTORY_SIZE_REFRESH_LIMIT = 40
_SUPPORTED_REFERENCE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_SUPPORTED_REFERENCE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_BLOCKED_DOWNLOAD_HOSTS = {"localhost", "localhost.localdomain"}
_TASK_ACTIVE_STATUSES = {"submitting", "in_progress", "rate_limited"}
_TASK_DEDUPE_STATUSES = {*_TASK_ACTIVE_STATUSES, "queued", "completed", "failed"}
_TASK_TERMINAL_STATUSES = {"completed", "failed"}
_DEFAULT_USER_ACTIVE_TASK_LIMIT = 3
_TASK_POLL_INTERVAL_SECONDS = 3
_TASK_RATE_LIMIT_BACKOFF_SECONDS = 12
_TASK_STALE_TIMEOUT_SECONDS = int(os.environ.get("SKILLFORGE_DIRECT_TASK_STALE_SECONDS", "1800"))
_TASK_DUPLICATE_WINDOW_SECONDS = 10 * 60
_TASK_DEDUPE_IGNORED_PARAM_KEYS = {"wait", "download", "workspace_name"}
_TASK_DEDUPE_EMPTY_EQUIVALENT_KEYS = {
    "image_urls",
    "mask",
    "mask_guide_path",
    "mask_path",
    "mask_source_path",
    "reference_images",
}
_GPT_IMAGEGEN_FALLBACK_MODEL = "gpt-image-2-vip"
_GPT_IMAGEGEN_REQUIRED_MODEL = "gpt-image-2"
_GPT_IMAGEGEN_FALLBACK_API_BASE = "https://toapis.com/v1"
_GPT_IMAGEGEN_CAPABILITY_IDS = {"gpt-imagegen", "gpt-imagegen-dialogue"}
_GPT_IMAGEGEN_ONE_K_RATIOS = {"1:1", "3:2", "2:3"}
_GPT_IMAGEGEN_FOUR_K_RATIOS = {"16:9", "9:16", "2:1", "1:2", "21:9", "9:21"}
_DIRECT_RUN_DEDUPE_LOCK = asyncio.Lock()
_DIRECT_RUN_IN_FLIGHT_KEYS: set[str] = set()
_PUBLIC_DIRECT_CAPABILITY_IDS = {"ai-chat", "gpt-imagegen", "gpt-imagegen-dialogue"}
RunnerEventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

_GPT_IMAGEGEN_FALLBACK_RETRYABLE_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "api key is required",
    "apikey is required",
    "connection",
    "generation failed",
    "generation_failed",
    "network error",
    "rate limit",
    "rate_limit",
    "temporarily unavailable",
    "timeout",
    "timed out",
    "too many requests",
    "unavailable",
    "upstream",
    "任务处理失败",
    "生成失败",
)
_GPT_IMAGEGEN_FALLBACK_NEW_TASK_MARKERS = (
    "generation failed",
    "generation_failed",
    "任务处理失败",
    "生成失败",
)
_GPT_IMAGEGEN_FALLBACK_NON_RETRYABLE_MARKERS = (
    "base64",
    "data uri",
    "must be a json object",
    "must include a hostname",
    "must not include credentials",
    "n must be",
    "payload must",
    "prompt exceeds",
    "prompt is required",
    "reference image must",
    "reference_images only accepts",
    "resolution must",
    "size must",
    "unsupported reference image",
)


def _truthy_config_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _skills_repo_candidates() -> list[Path]:
    candidates = [
        Path(settings.SKILL_REPO_PATH).expanduser(),
        _PROJECT_ROOT / "skills-repo",
    ]
    seen: set[str] = set()
    result: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists():
            result.append(resolved)
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _is_direct_capability(skill_id: str, meta: dict[str, Any], contract: dict[str, Any]) -> bool:
    hall_type = str(meta.get("hall_type") or contract.get("hall_type") or "").lower()
    runtime_mode = str(meta.get("runtime_mode") or contract.get("runtime_mode") or "").lower()
    if hall_type == "capability":
        return True
    if runtime_mode in {"direct", "inline", "local"}:
        return True
    if meta.get("default_in_hall") is True:
        return True
    # Public hall tools must keep working even if old metadata is missing.
    return skill_id in _PUBLIC_DIRECT_CAPABILITY_IDS


def _is_public_direct_capability(item: dict[str, Any]) -> bool:
    capability_id = str(item.get("id") or item.get("skill_id") or "").strip()
    return capability_id in _PUBLIC_DIRECT_CAPABILITY_IDS


def _schema_fields(contract: dict[str, Any]) -> dict[str, Any]:
    schema = contract.get("input_schema")
    if isinstance(schema, dict):
        return schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    return {}


def _direct_capability_project_surface(skill_id: str, meta: dict[str, Any], contract: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    safe_project_id = "direct_" + _SAFE_FILENAME_RE.sub("_", skill_id).strip("._-")[:34]
    output_schema = contract.get("output_schema") if isinstance(contract.get("output_schema"), dict) else {}
    result_ui_schema = contract.get("result_ui_schema") if isinstance(contract.get("result_ui_schema"), dict) else {}
    return {
        "enabled": True,
        "mode": "direct_skill_project_surface",
        "virtual_project_id": safe_project_id,
        "is_project_hosted": False,
        "boundary": "Hall 直接运行 Skill，不占用 Project 静态上传目录；运行、设计、记录和循环按项目面拆分展示。",
        "credential_location": "platform_only",
        "design_split": {
            "run_surface": "direct_capability_run",
            "design_surface": "user_skill_ui_overlay",
            "history_surface": "direct_capability_tasks_and_artifacts",
            "loop_surface": "reports_todos_artifacts_learning",
        },
        "segments": [
            {
                "key": "run",
                "label": "运行面",
                "summary": f"通过 Hall 直接运行 {skill_id}，输入参数由平台提交。",
                "records": ["direct_capability_tasks"],
            },
            {
                "key": "design",
                "label": "设计面",
                "summary": "个人页面设计保存为 user_id + skill_id + surface overlay，不修改 Skill 默认界面。",
                "records": ["user_skill_ui_preferences"],
            },
            {
                "key": "history",
                "label": "记录面",
                "summary": "产物、图片和任务历史按当前用户隔离保存。",
                "records": ["direct_capability_tasks", "direct_capability_image_history"],
            },
            {
                "key": "loop",
                "label": "AI 循环面",
                "summary": "输出产物可沉淀为报告、待办、证据和学习样本，后续接入 Project Gateway Trace。",
                "records": ["reports", "todos", "learning_events"],
            },
        ],
        "input_field_count": len(fields),
        "output_schema_available": bool(output_schema or result_ui_schema),
    }


def _capability_summary(skill_dir: Path, meta: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    skill_id = str(contract.get("id") or skill_dir.name)
    fields = _schema_fields(contract)
    input_schema = contract.get("input_schema") if isinstance(contract.get("input_schema"), dict) else {}
    stat = skill_dir.stat()
    return {
        "id": skill_id,
        "display_name": meta.get("display_name") or contract.get("name") or skill_id,
        "name": contract.get("name") or meta.get("display_name") or skill_id,
        "description": meta.get("description") or contract.get("description") or "",
        "category": meta.get("category") or contract.get("category") or "通用能力",
        "department": meta.get("department") or contract.get("department") or "平台",
        "risk_level": meta.get("risk_level") or contract.get("risk_level") or "R1",
        "approval_level": meta.get("approval_level") or contract.get("approval_level") or 0,
        "owner": meta.get("owner") or contract.get("owner") or "system",
        "visibility": meta.get("visibility") or contract.get("visibility") or "company",
        "status": meta.get("status") or contract.get("status") or "active",
        "runtime_mode": meta.get("runtime_mode") or contract.get("runtime_mode") or "direct",
        "hall_type": meta.get("hall_type") or contract.get("hall_type") or "capability",
        "parameter_count": len(fields),
        "required_count": len(input_schema.get("required", []) or []),
        "requires_api_key": "api_key" in fields,
        "project_surface": _direct_capability_project_surface(skill_id, meta, contract, fields),
        "updated_at": int(stat.st_mtime),
        "tags": meta.get("tags") if isinstance(meta.get("tags"), list) else ["直接运行"],
    }


def _iter_direct_capabilities() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for repo in _skills_repo_candidates():
        for skill_dir in sorted(p for p in repo.iterdir() if p.is_dir()):
            contract_path = skill_dir / "contract.json"
            meta_path = skill_dir / "skillforge.yaml"
            if not contract_path.exists() or not meta_path.exists():
                continue
            contract = _load_json(contract_path)
            meta = _load_yaml(meta_path)
            skill_id = str(contract.get("id") or skill_dir.name)
            if not _is_direct_capability(skill_id, meta, contract):
                continue
            item = _capability_summary(skill_dir, meta, contract)
            item["_skill_dir"] = str(skill_dir)
            item["_contract"] = contract
            item["_meta"] = meta
            items.append(item)
    # Keep the first repo's version if candidates overlap.
    deduped: dict[str, dict[str, Any]] = {}
    for item in items:
        deduped.setdefault(str(item["id"]), item)
    return list(deduped.values())


def _direct_capability_allowed(item: dict[str, Any], user: Any, action: str = "read") -> bool:
    """Metadata-level access for direct capabilities.

    Direct capabilities are filesystem-backed, so they cannot rely on
    SkillMember unless they are later mapped to a DB Skill. Company-visible
    active tools are intentionally public to active users; department/private
    capabilities are scoped by metadata.
    """
    status = str(item.get("status") or "active").lower()
    if status != "active" and not role_matches_any(user, ("admin",)):
        return False
    if role_matches_any(user, ("admin",)):
        return True
    if _is_public_direct_capability(item):
        return True
    if action != "read" and normalize_role(getattr(user, "role", None)) == "observer":
        return False

    visibility = str(item.get("visibility") or "company").lower()
    if visibility == "company":
        return True
    if visibility == "department":
        user_dept = str(getattr(user, "department", "") or "").strip()
        cap_dept = str(item.get("department") or "").strip()
        return bool(user_dept and cap_dept and user_dept == cap_dept) or (
            action == "read" and bool(getattr(user, "can_view_all", False))
        )
    if visibility == "private":
        owner = str(item.get("owner") or "").strip()
        return bool(owner and owner == str(getattr(user, "id", "")))
    return False


def _direct_capability_permissions(item: dict[str, Any], user: Any) -> dict[str, bool]:
    return {
        "read": _direct_capability_allowed(item, user, action="read"),
        "execute": _direct_capability_allowed(item, user, action="execute"),
    }


def _require_direct_capability_access(item: dict[str, Any], user: Any, action: str = "read") -> None:
    if not _direct_capability_allowed(item, user, action=action):
        raise HTTPException(status_code=403, detail="direct capability access denied")


def list_direct_capabilities(
    *,
    current_user: Any | None = None,
    q: str | None = None,
    category: str | None = None,
    department: list[str] | str | None = None,
    page: int = 1,
    page_size: int = 60,
    sort_by: str = "name_asc",
) -> dict[str, Any]:
    items = _iter_direct_capabilities()
    if current_user is not None:
        items = [
            item for item in items
            if _direct_capability_allowed(item, current_user, action="read")
        ]
    needle = (q or "").strip().lower()
    if needle:
        items = [
            item for item in items
            if needle in str(item.get("id", "")).lower()
            or needle in str(item.get("display_name", "")).lower()
            or needle in str(item.get("description", "")).lower()
            or needle in str(item.get("category", "")).lower()
        ]
    categories = sorted({str(item.get("category") or "通用能力") for item in items})
    if category:
        items = [item for item in items if str(item.get("category") or "") == category]
    department_counts: dict[str, int] = {}
    for item in items:
        dept = str(item.get("department") or "平台")
        department_counts[dept] = department_counts.get(dept, 0) + 1
    departments = [
        {"name": name, "count": count}
        for name, count in sorted(department_counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]
    if department:
        selected = department if isinstance(department, list) else [department]
        selected_set = {str(item).strip() for item in selected if str(item).strip()}
        if selected_set:
            items = [item for item in items if str(item.get("department") or "平台") in selected_set]

    if sort_by == "updated_at":
        items.sort(key=lambda item: int(item.get("updated_at") or 0), reverse=True)
    elif sort_by == "risk_asc":
        items.sort(key=lambda item: str(item.get("risk_level") or ""))
    else:
        items.sort(key=lambda item: str(item.get("display_name") or item.get("id") or ""))

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start:start + page_size]
    for item in page_items:
        if current_user is not None:
            item["permissions"] = _direct_capability_permissions(item, current_user)
        item.pop("_skill_dir", None)
        item.pop("_contract", None)
        item.pop("_meta", None)
    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "categories": categories,
        "departments": departments,
    }


def _resolve_capability(capability_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if not _CAPABILITY_ID_RE.match(capability_id):
        raise HTTPException(status_code=400, detail="invalid capability id")
    for item in _iter_direct_capabilities():
        if item.get("id") != capability_id:
            continue
        skill_dir = Path(str(item["_skill_dir"])).resolve()
        return skill_dir, item["_meta"], item["_contract"]
    raise HTTPException(status_code=404, detail="direct capability not found")


def get_direct_capability(capability_id: str, current_user: Any | None = None) -> dict[str, Any]:
    skill_dir, meta, contract = _resolve_capability(capability_id)
    summary = _capability_summary(skill_dir, meta, contract)
    if current_user is not None:
        _require_direct_capability_access(summary, current_user, action="read")
        summary["permissions"] = _direct_capability_permissions(summary, current_user)
    skill_md_path = skill_dir / "SKILL.md"
    docs = ""
    if skill_md_path.exists():
        try:
            docs = skill_md_path.read_text(encoding="utf-8")[:24000]
        except OSError:
            docs = ""
    return {
        **summary,
        "contract": contract,
        "input_schema": contract.get("input_schema") or {"type": "object", "properties": {}},
        "output_schema": contract.get("output_schema") or {},
        "result_ui_schema": contract.get("result_ui_schema") or {},
        "docs": docs,
    }


def _capability_git_commit(skill_dir: Path, meta: dict[str, Any], contract: dict[str, Any]) -> str:
    payload = {
        "id": contract.get("id") or skill_dir.name,
        "contract": contract,
        "meta": meta,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:40]


def _virtual_skill_for_direct_capability(
    skill_dir: Path,
    meta: dict[str, Any],
    contract: dict[str, Any],
):
    summary = _capability_summary(skill_dir, meta, contract)
    param_ui_schema = contract.get("param_ui_schema")
    if not isinstance(param_ui_schema, dict):
        param_ui_schema = contract.get("input_schema")
    if not isinstance(param_ui_schema, dict):
        param_ui_schema = {"type": "object", "properties": {}}
    result_ui_schema = contract.get("result_ui_schema")
    if not isinstance(result_ui_schema, dict):
        result_ui_schema = {}
    return SimpleNamespace(
        id=str(summary["id"]),
        name=summary.get("name") or summary.get("display_name") or summary["id"],
        display_name=summary.get("display_name") or summary.get("name") or summary["id"],
        description=summary.get("description") or "",
        category=summary.get("category") or "通用能力",
        department=summary.get("department") or "平台",
        risk_level=summary.get("risk_level") or "R1",
        approval_level=summary.get("approval_level") or 0,
        owner=summary.get("owner") or "system",
        visibility=summary.get("visibility") or "company",
        status=summary.get("status") or "active",
        param_ui_schema=param_ui_schema,
        result_ui_schema=result_ui_schema,
        git_commit=_capability_git_commit(skill_dir, meta, contract),
    )


def _ui_action_for_surface(surface: str) -> str:
    return "execute" if surface == "run_form" else "read"


def _resolve_direct_capability_ui(
    capability_id: str,
    current_user: Any,
    *,
    surface: str,
    access_action: str | None = None,
):
    skill_dir, meta, contract = _resolve_capability(capability_id)
    summary = _capability_summary(skill_dir, meta, contract)
    _require_direct_capability_access(summary, current_user, action=access_action or _ui_action_for_surface(surface))
    return _virtual_skill_for_direct_capability(skill_dir, meta, contract), summary


def _direct_capability_ui_permissions(summary: dict[str, Any], current_user: Any, surface: str) -> dict[str, bool]:
    return {
        "read": _direct_capability_allowed(summary, current_user, action="read"),
        "execute": _direct_capability_allowed(summary, current_user, action="execute"),
        "customize_ui": _direct_capability_allowed(summary, current_user, action=_ui_action_for_surface(surface)),
    }


def _public_ui_error_reason(detail: Any) -> str:
    text = str(detail or "ui_error")
    return text[:240]


async def _disable_invalid_direct_ui_preference(
    db: AsyncSession,
    preference: UserSkillUIPreference,
    exc: AppError,
) -> dict[str, Any]:
    preference.enabled = False
    preference.updated_at = now_bjt()
    await db.flush()
    return {
        "ui_pref_id": preference.id,
        "ui_pref_version": preference.version,
        "reason": _public_ui_error_reason(exc.detail or exc.code),
    }


async def _build_direct_capability_ui_response(
    db: AsyncSession,
    *,
    skill: Any,
    summary: dict[str, Any],
    current_user: Any,
    surface: str,
    preference: UserSkillUIPreference | None,
) -> dict[str, Any]:
    default_schema = portal_ui_service.default_ui_schema_for_skill(skill, surface)
    overlay = preference.overlay_json if preference else {}
    invalidated_pref: dict[str, Any] | None = None
    try:
        validated = portal_ui_service.validate_overlay_schema(overlay, skill=skill, surface=surface)
        effective_preference = preference
    except AppError as exc:
        if preference is None:
            raise
        invalidated_pref = await _disable_invalid_direct_ui_preference(db, preference, exc)
        validated = {}
        effective_preference = None
    merged = portal_ui_service.merge_ui_schema(default_schema, validated)
    portal_ui_service._validate_merged_schema_budget(merged)
    return {
        "schema_version": portal_ui_service.SCHEMA_VERSION,
        "skill_id": skill.id,
        "capability_id": skill.id,
        "surface": surface,
        "base_skill_commit": skill.git_commit,
        "ui_pref_id": effective_preference.id if effective_preference else None,
        "ui_pref_version": effective_preference.version if effective_preference else None,
        "generated_by": effective_preference.generated_by if effective_preference else None,
        "saved_prompt": portal_ui_service._public_saved_prompt(effective_preference),
        "ui_pref_invalidated": invalidated_pref,
        "overlay": validated,
        "merged_schema": merged,
        "merged_ui_schema_hash": portal_ui_service.stable_schema_hash(merged),
        "permissions": _direct_capability_ui_permissions(summary, current_user, surface),
    }


async def get_direct_capability_ui(
    db: AsyncSession,
    capability_id: str,
    *,
    current_user: Any,
    surface: str | None = None,
) -> dict[str, Any]:
    resolved = portal_ui_service.normalize_surface(surface)
    skill, summary = _resolve_direct_capability_ui(
        capability_id,
        current_user,
        surface=resolved,
        access_action="read",
    )
    preference = await portal_ui_service.get_active_preference(
        db,
        user_id=str(current_user.id),
        skill_id=skill.id,
        surface=resolved,
    )
    return await _build_direct_capability_ui_response(
        db,
        skill=skill,
        summary=summary,
        current_user=current_user,
        surface=resolved,
        preference=preference,
    )


async def preview_direct_capability_ui(
    db: AsyncSession,
    capability_id: str,
    *,
    current_user: Any,
    surface: str | None,
    instruction: str,
    current_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = portal_ui_service.normalize_surface(surface)
    normalized_instruction = portal_ui_service._normalize_ai_instruction(instruction)
    skill, summary = _resolve_direct_capability_ui(capability_id, current_user, surface=resolved)
    base_overlay = portal_ui_service.validate_overlay_schema(current_overlay or {}, skill=skill, surface=resolved)
    proposed_overlay, ai_status, ai_reason = await portal_ui_service._llm_instruction_overlay(
        skill=skill,
        user=current_user,
        surface=resolved,
        instruction=normalized_instruction,
        current_overlay=base_overlay,
    )
    if proposed_overlay is None:
        proposed_overlay = portal_ui_service._instruction_overlay(skill, resolved, normalized_instruction)
    merged_overlay = portal_ui_service._merge_overlay_patch(base_overlay, proposed_overlay)
    validated = portal_ui_service.validate_overlay_schema(merged_overlay, skill=skill, surface=resolved)
    response = await _build_direct_capability_ui_response(
        db,
        skill=skill,
        summary=summary,
        current_user=current_user,
        surface=resolved,
        preference=None,
    )
    default_schema = portal_ui_service.default_ui_schema_for_skill(skill, resolved)
    merged_schema = portal_ui_service.merge_ui_schema(default_schema, validated)
    portal_ui_service._validate_merged_schema_budget(merged_schema)
    return {
        **response,
        "overlay": validated,
        "merged_schema": merged_schema,
        "merged_ui_schema_hash": portal_ui_service.stable_schema_hash(merged_schema),
        "ai_status": ai_status,
        "ai_reason": ai_reason,
        "ai_context": {
            "skill_id": skill.id,
            "capability_id": capability_id,
            "surface": resolved,
            "base_skill_commit": skill.git_commit,
            "allowed_component_types": sorted(portal_ui_service.ALLOWED_COMPONENT_TYPES),
            "allowed_binding_prefixes": list(portal_ui_service.ALLOWED_BINDING_PREFIXES),
        },
    }


async def save_direct_capability_ui_preference(
    db: AsyncSession,
    capability_id: str,
    *,
    current_user: Any,
    surface: str | None,
    overlay: dict[str, Any] | None,
    generated_by: str = "manual",
    prompt_summary: str | None = None,
) -> dict[str, Any]:
    resolved = portal_ui_service.normalize_surface(surface)
    skill, summary = _resolve_direct_capability_ui(capability_id, current_user, surface=resolved)
    validated = portal_ui_service.validate_overlay_schema(overlay or {}, skill=skill, surface=resolved)
    saved_prompt = portal_ui_service._normalize_prompt_summary(prompt_summary)

    active_rows = (
        await db.execute(
            select(UserSkillUIPreference)
            .where(UserSkillUIPreference.user_id == str(current_user.id))
            .where(UserSkillUIPreference.skill_id == skill.id)
            .where(UserSkillUIPreference.surface == resolved)
            .where(UserSkillUIPreference.enabled == True)  # noqa: E712
        )
    ).scalars().all()
    for row in active_rows:
        row.enabled = False
        row.updated_at = now_bjt()
    if active_rows:
        await db.flush()

    max_version = await db.scalar(
        select(func.max(UserSkillUIPreference.version))
        .where(UserSkillUIPreference.user_id == str(current_user.id))
        .where(UserSkillUIPreference.skill_id == skill.id)
        .where(UserSkillUIPreference.surface == resolved)
    )
    preference = UserSkillUIPreference(
        id=f"uipref-{uuid.uuid4().hex[:16]}",
        user_id=str(current_user.id),
        skill_id=skill.id,
        surface=resolved,
        base_skill_commit=skill.git_commit,
        overlay_schema_version=portal_ui_service.SCHEMA_VERSION,
        overlay_json=validated,
        generated_by=generated_by if generated_by in {"manual", "ai"} else "manual",
        prompt_summary=saved_prompt,
        enabled=True,
        version=int(max_version or 0) + 1,
        created_at=now_bjt(),
        updated_at=now_bjt(),
    )
    db.add(preference)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise AppError(
            "UI_PREF_CONFLICT",
            409,
            {"detail": "个人界面已被其它请求更新，请刷新后重试"},
        ) from exc
    return await _build_direct_capability_ui_response(
        db,
        skill=skill,
        summary=summary,
        current_user=current_user,
        surface=resolved,
        preference=preference,
    )


async def list_direct_capability_ui_preferences(
    db: AsyncSession,
    capability_id: str,
    *,
    current_user: Any,
    surface: str | None,
    limit: int = 20,
) -> dict[str, Any]:
    resolved = portal_ui_service.normalize_surface(surface)
    skill, _summary = _resolve_direct_capability_ui(capability_id, current_user, surface=resolved)
    user_id = str(current_user.id)
    page_size = min(max(int(limit or 20), 1), 50)
    rows = (
        await db.execute(
            select(UserSkillUIPreference)
            .where(UserSkillUIPreference.user_id == user_id)
            .where(UserSkillUIPreference.skill_id == skill.id)
            .where(UserSkillUIPreference.surface == resolved)
            .order_by(UserSkillUIPreference.version.desc(), UserSkillUIPreference.created_at.desc())
            .limit(page_size)
        )
    ).scalars().all()
    total = await db.scalar(
        select(func.count())
        .select_from(UserSkillUIPreference)
        .where(UserSkillUIPreference.user_id == user_id)
        .where(UserSkillUIPreference.skill_id == skill.id)
        .where(UserSkillUIPreference.surface == resolved)
    )
    return {
        "items": [portal_ui_service._preference_history_item(row) for row in rows],
        "total": int(total or 0),
        "surface": resolved,
        "limit": page_size,
        "capability_id": skill.id,
    }


async def restore_direct_capability_ui_preference(
    db: AsyncSession,
    capability_id: str,
    *,
    current_user: Any,
    preference_id: str,
    surface: str | None,
) -> dict[str, Any]:
    resolved = portal_ui_service.normalize_surface(surface)
    skill, summary = _resolve_direct_capability_ui(capability_id, current_user, surface=resolved)
    user_id = str(current_user.id)
    source = await db.get(UserSkillUIPreference, preference_id)
    if not source or source.user_id != user_id or source.skill_id != skill.id or source.surface != resolved:
        raise AppError("PARAM_INVALID", 422, {"detail": "ui preference version not found"})
    try:
        validated = portal_ui_service.validate_overlay_schema(source.overlay_json or {}, skill=skill, surface=resolved)
    except AppError as exc:
        raise AppError(
            "PARAM_INVALID",
            422,
            {"detail": f"历史页面不再适用于当前能力：{_public_ui_error_reason(exc.detail or exc.code)}"},
        ) from exc

    active_rows = (
        await db.execute(
            select(UserSkillUIPreference)
            .where(UserSkillUIPreference.user_id == user_id)
            .where(UserSkillUIPreference.skill_id == skill.id)
            .where(UserSkillUIPreference.surface == resolved)
            .where(UserSkillUIPreference.enabled == True)  # noqa: E712
        )
    ).scalars().all()
    for row in active_rows:
        row.enabled = False
        row.updated_at = now_bjt()
    if active_rows:
        await db.flush()

    max_version = await db.scalar(
        select(func.max(UserSkillUIPreference.version))
        .where(UserSkillUIPreference.user_id == user_id)
        .where(UserSkillUIPreference.skill_id == skill.id)
        .where(UserSkillUIPreference.surface == resolved)
    )
    preference = UserSkillUIPreference(
        id=f"uipref-{uuid.uuid4().hex[:16]}",
        user_id=user_id,
        skill_id=skill.id,
        surface=resolved,
        base_skill_commit=skill.git_commit,
        overlay_schema_version=portal_ui_service.SCHEMA_VERSION,
        overlay_json=validated,
        generated_by=source.generated_by if source.generated_by in {"manual", "ai"} else "manual",
        prompt_summary=portal_ui_service._normalize_prompt_summary(source.prompt_summary),
        enabled=True,
        version=int(max_version or 0) + 1,
        created_at=now_bjt(),
        updated_at=now_bjt(),
    )
    db.add(preference)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise AppError(
            "UI_PREF_CONFLICT",
            409,
            {"detail": "个人界面已被其它请求更新，请刷新后重试"},
        ) from exc
    return await _build_direct_capability_ui_response(
        db,
        skill=skill,
        summary=summary,
        current_user=current_user,
        surface=resolved,
        preference=preference,
    )


async def delete_direct_capability_ui_preference(
    db: AsyncSession,
    capability_id: str,
    *,
    current_user: Any,
    surface: str | None,
) -> dict[str, Any]:
    resolved = portal_ui_service.normalize_surface(surface)
    skill, summary = _resolve_direct_capability_ui(capability_id, current_user, surface=resolved)
    active_rows = (
        await db.execute(
            select(UserSkillUIPreference)
            .where(UserSkillUIPreference.user_id == str(current_user.id))
            .where(UserSkillUIPreference.skill_id == skill.id)
            .where(UserSkillUIPreference.surface == resolved)
            .where(UserSkillUIPreference.enabled == True)  # noqa: E712
        )
    ).scalars().all()
    for row in active_rows:
        row.enabled = False
        row.updated_at = now_bjt()
    await db.flush()
    return await _build_direct_capability_ui_response(
        db,
        skill=skill,
        summary=summary,
        current_user=current_user,
        surface=resolved,
        preference=None,
    )


def _history_item(row: DirectCapabilityImageHistory) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "capability_id": row.capability_id,
        "url": row.image_url,
        "image_url": row.image_url,
        "name": row.name,
        "byte_size": row.byte_size,
        "prompt": row.prompt,
        "task_id": row.task_id,
        "source": row.source,
        "extra": row.extra or {},
        "created_at": row.created_at,
    }


async def _direct_capability_image_history_stats(
    db: AsyncSession,
    where: tuple[Any, ...],
) -> dict[str, Any]:
    total, known_bytes, known_count = (
        await db.execute(
            select(
                func.count(DirectCapabilityImageHistory.id),
                func.coalesce(func.sum(DirectCapabilityImageHistory.byte_size), 0),
                func.count(DirectCapabilityImageHistory.byte_size),
            ).where(*where)
        )
    ).one()
    total_count = int(total or 0)
    known_size_count = int(known_count or 0)
    return {
        "total_count": total_count,
        "known_count": known_size_count,
        "unknown_count": max(total_count - known_size_count, 0),
        "known_bytes": int(known_bytes or 0),
    }


async def _direct_capability_user_scope_ids(
    db: AsyncSession,
    user_id: str,
) -> tuple[str, ...]:
    """Resolve verified legacy DingTalk identities for user-owned history.

    Older DingTalk applications exposed an openId-shaped identity before the
    directory callback supplied the canonical enterprise userid.  The login
    flow records that verified reconciliation in the immutable audit log.  We
    use only that trusted relation here; names and departments are never used
    to broaden access.
    """

    from app.common.audit import AuditLog

    resolved = {str(user_id)}
    details = (
        await db.execute(
            select(AuditLog.detail)
            .where(AuditLog.user_id == str(user_id))
            .where(AuditLog.action == "user.login")
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(200)
        )
    ).scalars().all()
    for detail in details:
        if not isinstance(detail, dict):
            continue
        alias = str(detail.get("reconciled_shadow_user_id") or "").strip()
        if alias:
            resolved.add(alias)
    return tuple(sorted(resolved))


def _measure_image_url_size(url: str) -> int | None:
    safe_url = _validate_download_url(url)
    opener = urllib_request.build_opener(_ValidatedRedirectHandler)
    guessed = mimetypes.guess_type(urllib_parse.urlsplit(safe_url).path)[0] or ""
    head_req = urllib_request.Request(
        safe_url,
        headers={"User-Agent": "SkillForge/1.0 image-size"},
        method="HEAD",
    )
    try:
        with opener.open(head_req, timeout=10) as response:
            final_url = _validate_download_url(response.geturl())
            header_type = (response.headers.get_content_type() or "").lower()
            guessed = mimetypes.guess_type(urllib_parse.urlsplit(final_url).path)[0] or guessed
            content_length = _positive_int(response.headers.get("Content-Length"))
            if content_length is not None and content_length > _MAX_IMAGE_DOWNLOAD_BYTES:
                raise HTTPException(status_code=413, detail="image exceeds 50MB download limit")
            if content_length is not None and (header_type.startswith("image/") or guessed.startswith("image/")):
                return content_length
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        pass
    req = urllib_request.Request(
        safe_url,
        headers={"User-Agent": "SkillForge/1.0 image-size"},
        method="GET",
    )
    with opener.open(req, timeout=15) as response:
        final_url = _validate_download_url(response.geturl())
        header_type = (response.headers.get_content_type() or "").lower()
        guessed = mimetypes.guess_type(urllib_parse.urlsplit(final_url).path)[0] or guessed
        prefix = bytearray()
        size = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_IMAGE_DOWNLOAD_BYTES:
                raise HTTPException(status_code=413, detail="image exceeds 50MB download limit")
            if len(prefix) < 16:
                prefix.extend(chunk[: 16 - len(prefix)])
    if size <= 0:
        return None
    if header_type.startswith("image/") or _detect_image_media_type(bytes(prefix)) or guessed.startswith("image/"):
        return size
    return None


async def _refresh_missing_history_image_sizes(
    db: AsyncSession,
    where: tuple[Any, ...],
) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(DirectCapabilityImageHistory)
            .where(*where)
            .where(DirectCapabilityImageHistory.byte_size.is_(None))
            .order_by(DirectCapabilityImageHistory.created_at.desc(), DirectCapabilityImageHistory.id.desc())
            .limit(_IMAGE_HISTORY_SIZE_REFRESH_LIMIT)
        )
    ).scalars().all()
    refreshed = 0
    failed = 0
    for row in rows:
        try:
            byte_size = await asyncio.to_thread(_measure_image_url_size, row.image_url)
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        if byte_size is None:
            failed += 1
            continue
        row.byte_size = byte_size
        row.extra = {
            **(row.extra or {}),
            "byte_size_source": "measured",
            "byte_size_measured_at": now_bjt().isoformat(),
        }
        refreshed += 1
    if refreshed:
        await db.flush()
    return {
        "attempted": len(rows),
        "refreshed": refreshed,
        "failed": failed,
        "capped": len(rows) >= _IMAGE_HISTORY_SIZE_REFRESH_LIMIT,
    }


async def list_direct_capability_image_history(
    db: AsyncSession,
    *,
    user_id: str,
    capability_id: str,
    current_user: Any | None = None,
    page: int = 1,
    page_size: int = 200,
    refresh_missing_sizes: bool = False,
) -> dict[str, Any]:
    skill_dir, meta, contract = _resolve_capability(capability_id)
    if current_user is not None:
        _require_direct_capability_access(
            _capability_summary(skill_dir, meta, contract),
            current_user,
            action="read",
        )
    page_size = min(max(int(page_size or 200), 1), 500)
    page = max(int(page or 1), 1)
    user_scope_ids = await _direct_capability_user_scope_ids(db, user_id)
    where = (
        DirectCapabilityImageHistory.user_id.in_(user_scope_ids),
        DirectCapabilityImageHistory.capability_id == capability_id,
    )
    # Never perform remote HEAD/GET probes in an interactive list request.  The
    # legacy workspace requested up to 40 probes during page load; users with a
    # large history could exceed the browser timeout and the UI then reported
    # the otherwise healthy capability as unavailable.  Keep the query flag for
    # backward compatibility, but make old cached clients safe as well.
    size_refresh = {
        "attempted": 0,
        "refreshed": 0,
        "failed": 0,
        "capped": False,
        "requested": bool(refresh_missing_sizes),
        "mode": "deferred" if refresh_missing_sizes else "not_requested",
    }
    stats = await _direct_capability_image_history_stats(db, where)
    total = int(stats["total_count"])
    rows_result = await db.execute(
        select(DirectCapabilityImageHistory)
        .where(*where)
        .order_by(DirectCapabilityImageHistory.created_at.desc(), DirectCapabilityImageHistory.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [_history_item(row) for row in rows_result.scalars().all()]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
        "stats": stats,
        "size_refresh": size_refresh,
    }


async def list_direct_capability_artifacts(
    db: AsyncSession,
    *,
    user_id: str,
    current_user: Any | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page_size = min(max(int(page_size or 20), 1), 100)
    page = max(int(page or 1), 1)
    user_scope_ids = await _direct_capability_user_scope_ids(db, user_id)
    where = (DirectCapabilityImageHistory.user_id.in_(user_scope_ids),)
    total_result = await db.execute(
        select(func.count()).select_from(DirectCapabilityImageHistory).where(*where)
    )
    total = int(total_result.scalar() or 0)
    rows_result = await db.execute(
        select(DirectCapabilityImageHistory)
        .where(*where)
        .order_by(DirectCapabilityImageHistory.created_at.desc(), DirectCapabilityImageHistory.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    names: dict[str, str] = {}
    for cap in _iter_direct_capabilities():
        if current_user is not None and not _direct_capability_allowed(cap, current_user, action="read"):
            continue
        cap_id = str(cap.get("id") or "")
        if not cap_id:
            continue
        names[cap_id] = str(cap.get("display_name") or cap.get("name") or cap_id)
    items = []
    for row in rows_result.scalars().all():
        item = _history_item(row)
        item["capability_name"] = names.get(row.capability_id, row.capability_id)
        items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
    }


def _display_name_from_url(url: str, fallback: str) -> str:
    try:
        parsed = urllib_parse.urlsplit(url)
        raw = urllib_parse.unquote(Path(parsed.path or "").name)
    except ValueError:
        raw = ""
    name = (raw or fallback or "image.png").strip()
    if not Path(name).suffix:
        name = f"{name}.png"
    return name[:255]


def _is_http_image_url(value: Any) -> bool:
    return (
        isinstance(value, str)
        and re.match(r"^https?://", value)
        and re.search(r"\.(png|jpe?g|webp|gif)(\?|#|$)", value, flags=re.I)
    )


def _collect_explicit_result_image_urls(result: dict[str, Any]) -> list[str]:
    urls: list[str] = []

    def add(value: Any) -> None:
        if _is_http_image_url(value) and value not in urls:
            urls.append(value)

    roots = [result]
    nested_result = result.get("result")
    if isinstance(nested_result, dict):
        roots.append(nested_result)

    for root in roots:
        image_urls = root.get("image_urls")
        if isinstance(image_urls, (list, tuple)):
            for item in image_urls:
                add(item)
        elif isinstance(image_urls, str):
            add(image_urls)
        add(root.get("image_url"))
        add(root.get("url"))

    return urls


def _collect_uploaded_image_urls(
    value: Any,
    out: set[str] | None = None,
    seen: set[int] | None = None,
    in_upload: bool = False,
) -> set[str]:
    out = out if out is not None else set()
    seen = seen if seen is not None else set()
    if isinstance(value, str):
        return out
    if not isinstance(value, (dict, list, tuple)):
        return out
    marker = id(value)
    if marker in seen:
        return out
    seen.add(marker)
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_uploaded_image_urls(item, out, seen, in_upload)
        return out
    if in_upload and _is_http_image_url(value.get("url")):
        out.add(str(value.get("url")))
    for key, nested in value.items():
        next_in_upload = in_upload or bool(re.search(r"uploaded|upload", str(key), flags=re.I))
        _collect_uploaded_image_urls(nested, out, seen, next_in_upload)
    return out


def _collect_result_image_urls(
    value: Any,
    out: list[str] | None = None,
    seen: set[int] | None = None,
    key_path: str = "",
) -> list[str]:
    out = out if out is not None else []
    seen = seen if seen is not None else set()
    if len(out) >= 24:
        return out
    if isinstance(value, str):
        if (
            re.search(r"(image_urls?|files?|file|url|image_url)", key_path, flags=re.I)
            and _is_http_image_url(value)
            and value not in out
        ):
            out.append(value)
        return out
    if not isinstance(value, (dict, list, tuple)):
        return out
    marker = id(value)
    if marker in seen:
        return out
    seen.add(marker)
    if re.search(r"api_key|prompt|reference|input|message|request|uploaded|upload", key_path, flags=re.I):
        return out
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_result_image_urls(item, out, seen, key_path)
        return out
    for key, nested in value.items():
        if re.search(r"api_key|prompt|reference|input|message|request|uploaded|upload", str(key), flags=re.I):
            continue
        _collect_result_image_urls(nested, out, seen, str(key))
    return out


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _image_byte_size_from_container(value: dict[str, Any]) -> int | None:
    for key in (
        "byte_size",
        "bytes",
        "size_bytes",
        "content_length",
        "contentLength",
        "file_size",
        "fileSize",
        "downloaded_bytes",
    ):
        found = _positive_int(value.get(key))
        if found is not None:
            return found
    return None


def _collect_result_image_byte_sizes(result: dict[str, Any]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    seen: set[int] = set()

    def add(url: Any, byte_size: int | None) -> None:
        if byte_size is None or not _is_http_image_url(url):
            return
        sizes.setdefault(str(url), byte_size)

    def walk(value: Any) -> None:
        marker = id(value)
        if isinstance(value, (dict, list, tuple)):
            if marker in seen:
                return
            seen.add(marker)
        if isinstance(value, dict):
            local_size = _image_byte_size_from_container(value)
            add(value.get("url"), local_size)
            add(value.get("image_url"), local_size)
            image_urls = value.get("image_urls")
            image_sizes = value.get("image_byte_sizes") or value.get("image_size_bytes")
            if isinstance(image_urls, (list, tuple)) and isinstance(image_sizes, (list, tuple)):
                for url, raw_size in zip(image_urls, image_sizes):
                    add(url, _positive_int(raw_size))
            for nested in value.values():
                walk(nested)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(result)
    return sizes


def _pick_nested_text(value: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(value, dict):
        return ""
    for key in keys:
        raw = value.get(key)
        if raw not in (None, ""):
            return str(raw)
    for nested in value.values():
        if isinstance(nested, dict):
            found = _pick_nested_text(nested, keys)
            if found:
                return found
    return ""


async def save_direct_capability_image_history(
    db: AsyncSession | None,
    *,
    user_id: str | None,
    capability_id: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> int:
    if not db or not user_id:
        return 0
    urls = _collect_explicit_result_image_urls(result) or _collect_result_image_urls(result)
    uploaded_urls = _collect_uploaded_image_urls(result)
    if uploaded_urls:
        urls = [url for url in urls if url not in uploaded_urls]
    if not urls:
        return 0
    prompt = str(payload.get("prompt") or payload.get("message") or "").strip()[:4000] or None
    task_id = _pick_nested_text(result, ("task_id", "taskId", "job_id", "jobId", "id"))[:120] or None
    source = str(payload.get("operation") or payload.get("action") or "generate")[:30] or "generate"
    result_byte_sizes = _collect_result_image_byte_sizes(result)
    inserted = 0
    workspace_id = str(payload.get("workspace_id") or "").strip() or None
    workspace_name = str(payload.get("workspace_name") or "").strip() or None
    for index, url in enumerate(urls):
        stmt = (
            pg_insert(DirectCapabilityImageHistory)
            .values(
                user_id=user_id,
                capability_id=capability_id,
                image_url=url,
                name=_display_name_from_url(url, f"生成图 {index + 1}.png"),
                byte_size=result_byte_sizes.get(url),
                prompt=prompt,
                task_id=task_id,
                source=source,
                extra={
                    "index": index,
                    "workspace_id": workspace_id,
                    "workspace_name": workspace_name,
                },
                created_at=now_bjt(),
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "capability_id", "image_url"],
            )
        )
        result_proxy = await db.execute(stmt)
        inserted += int(result_proxy.rowcount or 0)
    return inserted


def _safe_upload_name(filename: str | None) -> str:
    raw = Path(filename or "reference.png").name
    suffix = Path(raw).suffix.lower()
    stem = Path(raw).stem
    safe_stem = _SAFE_FILENAME_RE.sub("_", stem).strip("._-")
    safe_suffix = suffix if suffix in _SUPPORTED_REFERENCE_EXTENSIONS else ".png"
    return f"{safe_stem or 'reference'}{safe_suffix}"


def _validate_reference_upload(filename: str, content_type: str | None) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in _SUPPORTED_REFERENCE_EXTENSIONS:
        supported = ", ".join(sorted(_SUPPORTED_REFERENCE_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"unsupported image type: {suffix or '(none)'}. Supported: {supported}")
    guessed = mimetypes.guess_type(filename)[0] or ""
    mime_type = (content_type or guessed or "").lower()
    if mime_type == "application/octet-stream" and guessed:
        mime_type = guessed
    if mime_type not in _SUPPORTED_REFERENCE_MIME_TYPES:
        supported = ", ".join(sorted(_SUPPORTED_REFERENCE_MIME_TYPES))
        raise HTTPException(status_code=400, detail=f"unsupported image MIME type: {mime_type or '(unknown)'}. Supported: {supported}")
    return mime_type


def _safe_download_name(filename: str | None, source_url: str) -> str:
    parsed = urllib_parse.urlsplit(source_url)
    source_name = urllib_parse.unquote(Path(parsed.path or "").name)
    raw = Path(filename or source_name or "image.png").name
    suffix = Path(raw).suffix.lower()
    stem = Path(raw).stem
    safe_stem = _SAFE_FILENAME_RE.sub("_", stem).strip("._-")
    safe_suffix = suffix if suffix in _SUPPORTED_REFERENCE_EXTENSIONS else ".png"
    return f"{safe_stem or 'image'}{safe_suffix}"


def _blocked_download_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return any((
        ip.is_private,
        ip.is_loopback,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ))


def _validate_download_url(raw_url: str) -> str:
    url = str(raw_url or "").strip()
    parsed = urllib_parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="download url must be http or https")
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname or hostname in _BLOCKED_DOWNLOAD_HOSTS:
        raise HTTPException(status_code=400, detail="invalid download host")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid download port") from exc
    try:
        addresses = [hostname] if _is_ip_literal(hostname) else [
            item[4][0] for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        ]
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="download host cannot be resolved") from exc
    if not addresses or any(_blocked_download_address(address) for address in set(addresses)):
        raise HTTPException(status_code=400, detail="download host is not allowed")
    return urllib_parse.urlunsplit(parsed)


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


class _ValidatedRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        target_url = urllib_parse.urljoin(req.full_url, newurl)
        _validate_download_url(target_url)
        return super().redirect_request(req, fp, code, msg, headers, target_url)


def _detect_image_media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _fetch_download_image(url: str) -> tuple[bytes, str]:
    safe_url = _validate_download_url(url)
    opener = urllib_request.build_opener(_ValidatedRedirectHandler)
    req = urllib_request.Request(
        safe_url,
        headers={"User-Agent": "SkillForge/1.0 image-download"},
        method="GET",
    )
    try:
        with opener.open(req, timeout=30) as response:
            final_url = _validate_download_url(response.geturl())
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > _MAX_IMAGE_DOWNLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="image exceeds 50MB download limit")
                except ValueError:
                    pass
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_IMAGE_DOWNLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="image exceeds 50MB download limit")
                chunks.append(chunk)
            data = b"".join(chunks)
            header_type = (response.headers.get_content_type() or "").lower()
    except HTTPException:
        raise
    except urllib_error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"image download failed with HTTP {exc.code}") from exc
    except urllib_error.URLError as exc:
        raise HTTPException(status_code=502, detail="image download failed") from exc

    if not data:
        raise HTTPException(status_code=400, detail="empty image download")
    detected_type = _detect_image_media_type(data)
    if header_type.startswith("image/"):
        return data, header_type
    if detected_type:
        return data, detected_type
    guessed = mimetypes.guess_type(urllib_parse.urlsplit(final_url).path)[0] or ""
    if guessed.startswith("image/"):
        return data, guessed
    raise HTTPException(status_code=400, detail="download url did not return an image")


async def download_direct_capability_image(
    capability_id: str,
    url: str,
    filename: str | None = None,
    current_user: Any | None = None,
) -> Response:
    skill_dir, meta, contract = _resolve_capability(capability_id)
    if current_user is not None:
        _require_direct_capability_access(
            _capability_summary(skill_dir, meta, contract),
            current_user,
            action="read",
        )
    safe_name = _safe_download_name(filename, url)
    data, media_type = await asyncio.to_thread(_fetch_download_image, url)
    quoted_name = urllib_parse.quote(safe_name)
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename=\"{safe_name}\"; filename*=UTF-8''{quoted_name}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def save_direct_capability_upload(
    capability_id: str,
    file: UploadFile,
    current_user: Any | None = None,
) -> dict[str, Any]:
    """Store a browser-uploaded reference image and return a server-local path.

    The direct capability runner already knows how to upload local image files to
    ToAPIs before generation, so the hall UI only needs a safe temporary server
    path that can be passed through ``reference_images``.
    """
    skill_dir, meta, contract = _resolve_capability(capability_id)
    if current_user is not None:
        _require_direct_capability_access(
            _capability_summary(skill_dir, meta, contract),
            current_user,
            action="read",
        )
    original_name = Path(file.filename or "").name or "reference.png"
    filename = _safe_upload_name(file.filename)
    content_type = _validate_reference_upload(filename, file.content_type)
    upload_dir = (_UPLOAD_ROOT / capability_id).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{uuid.uuid4().hex}_{filename}"
    size = 0
    try:
        with target.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_REFERENCE_UPLOAD_BYTES:
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise HTTPException(status_code=413, detail="reference image exceeds 10MB upload limit")
                fh.write(chunk)
    finally:
        await file.close()
    if size <= 0:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="empty upload")
    return {
        "id": target.stem,
        "name": filename,
        "original_name": original_name,
        "path": str(target),
        "size": size,
        "content_type": content_type,
        "max_bytes": _MAX_REFERENCE_UPLOAD_BYTES,
    }


def _runner_timeout(meta: dict[str, Any], payload: dict[str, Any]) -> int:
    runtime = meta.get("runtime") if isinstance(meta.get("runtime"), dict) else {}
    configured = runtime.get("timeout_seconds") or runtime.get("timeout")
    candidates = [configured, payload.get("timeout")]
    values: list[int] = []
    for value in candidates:
        try:
            if value not in (None, ""):
                values.append(int(float(value)))
        except (TypeError, ValueError):
            continue
    base = max(values) if values else 600
    return min(max(base + 60, 120), 3660)


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    sdk_dir = _PROJECT_ROOT / "app" / "skill_runtime_sdk"
    pythonpath = [str(_PROJECT_ROOT)]
    if sdk_dir.exists():
        pythonpath.append(str(sdk_dir))
    existing = env.get("PYTHONPATH")
    if existing:
        pythonpath.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    return env


def _redact(text: str, payload: dict[str, Any]) -> str:
    redacted = text or ""
    for key in ("api_key", "token"):
        value = payload.get(key)
        if value:
            redacted = redacted.replace(str(value), "[redacted]")
    return redacted


def _normalize_direct_capability_payload(capability_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    if capability_id in _GPT_IMAGEGEN_CAPABILITY_IDS:
        normalized["model"] = _GPT_IMAGEGEN_REQUIRED_MODEL
        size = str(normalized.get("size") or "").strip()
        resolution = str(normalized.get("resolution") or "").strip().upper()
        if (
            resolution == "1K"
            and size
            and size not in _GPT_IMAGEGEN_ONE_K_RATIOS
        ) or (
            resolution == "4K"
            and size
            and size not in _GPT_IMAGEGEN_FOUR_K_RATIOS
        ):
            normalized["resolution"] = "2K"
    return normalized


def _gpt_imagegen_local_config_candidates() -> list[Path]:
    candidates: list[Path] = []
    for root in _skills_repo_candidates():
        candidates.append(root / "gpt-imagegen" / "config.json")
        candidates.append(root / "gpt-imagegen-dialogue" / "config.json")
    candidates.append(Path.home() / ".config" / "gpt-imagegen" / "config.json")
    candidates.append(Path.home() / ".config" / "dcha-imagegen" / "config.json")
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path.expanduser())
    return unique


def _gpt_imagegen_fallback_config_from_file() -> dict[str, Any]:
    for path in _gpt_imagegen_local_config_candidates():
        if not path.exists():
            continue
        data = _load_json(path)
        api_key = str(data.get("api_key") or data.get("apiKey") or "").strip()
        if not api_key:
            continue
        return {
            "enabled": True,
            "api_base": str(data.get("base_url") or data.get("baseUrl") or _GPT_IMAGEGEN_FALLBACK_API_BASE).strip(),
            "api_key": api_key,
            "model": _GPT_IMAGEGEN_FALLBACK_MODEL,
            "timeout": _coerce_positive_float(data.get("timeout"), 60.0, 1.0, 300.0),
            "source": str(path),
        }
    return {"enabled": False, "disabled_reason": "missing_api_key"}


async def _gpt_imagegen_fallback_config() -> dict[str, Any]:
    try:
        from app.common.models import SystemConfig
        from app.database import async_session_factory

        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(SystemConfig).where(SystemConfig.key.like("ai.image_fallback.toapis.%"))
                )
            ).scalars().all()
    except Exception as exc:  # noqa: BLE001
        return {"enabled": False, "disabled_reason": f"config_unavailable:{type(exc).__name__}"}

    values = {row.key: row.value for row in rows}
    enabled = values.get("ai.image_fallback.toapis.enabled")
    has_explicit_enabled = enabled is not None
    enabled_value = _truthy_config_value(enabled)
    api_key = str(values.get("ai.image_fallback.toapis.api_key") or "").strip()
    if has_explicit_enabled and not enabled_value:
        return {"enabled": False, "disabled_reason": "disabled"}
    if not api_key and not has_explicit_enabled:
        return _gpt_imagegen_fallback_config_from_file()
    if not api_key:
        local_config = _gpt_imagegen_fallback_config_from_file()
        if local_config.get("enabled"):
            if values.get("ai.image_fallback.toapis.api_base"):
                local_config["api_base"] = str(values["ai.image_fallback.toapis.api_base"]).strip()
            if values.get("ai.image_fallback.toapis.model"):
                local_config["model"] = str(values["ai.image_fallback.toapis.model"]).strip()
            if values.get("ai.image_fallback.toapis.timeout"):
                local_config["timeout"] = _coerce_positive_float(values.get("ai.image_fallback.toapis.timeout"), 60.0, 1.0, 300.0)
            return local_config
        return {"enabled": False, "disabled_reason": "missing_api_key"}
    return {
        "enabled": True,
        "api_base": str(values.get("ai.image_fallback.toapis.api_base") or _GPT_IMAGEGEN_FALLBACK_API_BASE).strip(),
        "api_key": api_key,
        "model": str(values.get("ai.image_fallback.toapis.model") or _GPT_IMAGEGEN_FALLBACK_MODEL).strip(),
        "timeout": _coerce_positive_float(values.get("ai.image_fallback.toapis.timeout"), 60.0, 1.0, 300.0),
    }


def _coerce_positive_float(value: Any, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _toapis_api_endpoint(base_url: str, path: str) -> str:
    value = (base_url or _GPT_IMAGEGEN_FALLBACK_API_BASE).strip().rstrip("/")
    if not value:
        value = _GPT_IMAGEGEN_FALLBACK_API_BASE
    if "://" not in value:
        value = "https://" + value
    parsed = urllib_parse.urlsplit(value)
    if parsed.scheme.lower() != "https":
        raise RuntimeError("fallback api_base must use HTTPS")
    base_path = parsed.path.rstrip("/")
    prefix = base_path if base_path.endswith("/v1") else f"{base_path}/v1" if base_path else "/v1"
    full_path = f"{prefix.rstrip('/')}/{path.lstrip('/')}"
    return urllib_parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, full_path, "", ""))


def _gpt_imagegen_prompt_from_payload(capability_id: str, payload: dict[str, Any]) -> str:
    prompt = str(payload.get("prompt") or payload.get("message") or payload.get("request") or "").strip()
    if prompt:
        return prompt
    if capability_id == "gpt-imagegen-dialogue" and isinstance(payload.get("messages"), list):
        parts: list[str] = []
        for item in payload["messages"]:
            if isinstance(item, dict):
                content = item.get("content") or item.get("text") or item.get("message")
                if isinstance(content, list):
                    content = "\n".join(
                        str(part.get("text") if isinstance(part, dict) else part)
                        for part in content
                        if part
                    )
                if content:
                    parts.append(str(content))
            elif item:
                parts.append(str(item))
        prompt = "\n".join(parts).strip()
    return prompt


def _gpt_imagegen_reference_images(payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("reference_images", "image_urls", "images", "image"):
        value = payload.get(key)
        items = value if isinstance(value, list) else [value]
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            for part in text.replace(",", "\n").splitlines():
                part = part.strip()
                if part and part not in refs:
                    refs.append(part)
    return refs


def _toapis_extract_image_urls(response: dict[str, Any]) -> list[str]:
    urls: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in urls:
            urls.append(value)

    roots: list[Any] = [response]
    for key in ("data", "result", "output"):
        value = response.get(key)
        if isinstance(value, (dict, list)):
            roots.append(value)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            add(value.get("url"))
            add(value.get("image_url"))
            for key in ("data", "images", "image_urls", "result", "output"):
                if key in value:
                    walk(value[key])
        elif isinstance(value, list):
            for item in value:
                walk(item)
        else:
            add(value)

    for root in roots:
        walk(root)
    return urls


def _toapis_task_id(response: dict[str, Any]) -> str:
    for key in ("id", "task_id", "taskId", "job_id", "jobId"):
        value = response.get(key)
        if value:
            return str(value)[:120]
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("id", "task_id", "taskId", "job_id", "jobId"):
            value = data.get(key)
            if value:
                return str(value)[:120]
    return ""


def _toapis_status(response: dict[str, Any], image_urls: list[str]) -> str:
    raw = str(response.get("status") or "").strip().lower()
    if raw in {"succeeded", "success"}:
        return "completed"
    if raw in {"error", "canceled", "cancelled"}:
        return "failed"
    if raw:
        return raw
    data = response.get("data")
    if isinstance(data, dict):
        raw = str(data.get("status") or "").strip().lower()
        if raw in {"succeeded", "success"}:
            return "completed"
        if raw in {"error", "canceled", "cancelled"}:
            return "failed"
        if raw:
            return raw
    return "completed" if image_urls else "queued"


async def _call_gpt_imagegen_toapis_fallback(
    *,
    capability_id: str,
    payload: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    prompt = _gpt_imagegen_prompt_from_payload(capability_id, payload)
    task_id = str(payload.get("task_id") or "").strip()
    if not prompt and not task_id:
        raise RuntimeError("prompt is required unless task_id is provided")

    timeout = _coerce_positive_float(payload.get("http_timeout"), float(config.get("timeout") or 60.0), 1.0, 300.0)

    async def request_json(method: str, url: str, body: bytes | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Accept": "application/json",
            "User-Agent": str(payload.get("user_agent") or "skillforge-gpt-imagegen-fallback/1.0"),
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib_request.Request(url, data=body, headers=headers, method=method)

        def read_response() -> bytes:
            try:
                with urllib_request.urlopen(request, timeout=timeout) as response:
                    return response.read()
            except urllib_error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"fallback HTTP {exc.code}: {_redact(error_body[:500], {'api_key': config['api_key']})}"
                ) from exc
            except urllib_error.URLError as exc:
                raise RuntimeError(f"fallback network error: {exc.reason}") from exc

        raw = await asyncio.to_thread(read_response)
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("fallback API returned non-JSON response") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("fallback API response must be a JSON object")
        return parsed

    if task_id:
        parsed = await request_json(
            "GET",
            _toapis_api_endpoint(config["api_base"], f"/images/generations/{urllib_parse.quote(task_id, safe='')}"),
        )
    else:
        request_payload: dict[str, Any] = {
            "model": config.get("model") or _GPT_IMAGEGEN_FALLBACK_MODEL,
            "prompt": prompt,
            "n": max(1, int(float(payload.get("n") or 1))),
            "size": str(payload.get("size") or "1:1"),
            "resolution": str(payload.get("resolution") or "1K"),
            "response_format": str(payload.get("response_format") or "url"),
        }
        reference_images = _gpt_imagegen_reference_images(payload)
        if reference_images:
            request_payload["reference_images"] = reference_images
        parsed = await request_json(
            "POST",
            _toapis_api_endpoint(config["api_base"], "/images/generations"),
            json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        )

    image_urls = _toapis_extract_image_urls(parsed)
    resolved_task_id = _toapis_task_id(parsed) or task_id
    status = _toapis_status(parsed, image_urls)
    if payload.get("wait") is not False and resolved_task_id and status not in {"completed", "failed"}:
        started = asyncio.get_running_loop().time()
        max_wait = _coerce_positive_float(payload.get("timeout"), 120.0, 1.0, 3600.0)
        first_wait = _coerce_positive_float(payload.get("initial_wait"), 2.0, 0.1, 60.0)
        poll_interval = _coerce_positive_float(payload.get("poll_interval"), 3.0, 0.1, 60.0)
        delay = first_wait
        while status not in {"completed", "failed"}:
            elapsed = asyncio.get_running_loop().time() - started
            if elapsed >= max_wait:
                break
            await asyncio.sleep(min(delay, max_wait - elapsed))
            delay = poll_interval
            parsed = await request_json(
                "GET",
                _toapis_api_endpoint(config["api_base"], f"/images/generations/{urllib_parse.quote(resolved_task_id, safe='')}"),
            )
            image_urls = _toapis_extract_image_urls(parsed)
            status = _toapis_status(parsed, image_urls)
    if status == "failed":
        raise RuntimeError(f"fallback generation failed: {_task_error_from_response({'result': parsed}) or parsed}")
    return {
        "kind": "image",
        "status": status,
        "task_id": resolved_task_id,
        "object": parsed.get("object") or "image.generation",
        "model": config.get("model") or _GPT_IMAGEGEN_FALLBACK_MODEL,
        "size": payload.get("size") or "1:1",
        "resolution": payload.get("resolution") or "1K",
        "n": payload.get("n") or len(image_urls) or 1,
        "progress": parsed.get("progress"),
        "image_urls": image_urls,
        "files": [],
        "file": "",
        "provider": "toapis_fallback",
        "fallback": True,
        "raw_response": parsed,
    }


def _gpt_imagegen_fallback_reason(result: dict[str, Any] | None, *, exit_code: int | None = None, error: str = "") -> str | None:
    if not result and not error and exit_code in (None, 0):
        return None
    if exit_code not in (None, 0):
        text = error or _task_error_from_response({"result": result or {}})
        if _gpt_imagegen_error_retryable(text):
            return text or f"exit_code={exit_code}"
        return None
    status = str((result or {}).get("status") or "").strip().lower()
    if status and status not in {"failed", "error", "timeout", "timed_out"}:
        return None
    text = error or _task_error_from_response({"result": result or {}})
    if _gpt_imagegen_error_retryable(text):
        return text or status or "primary_unavailable"
    return None


def _gpt_imagegen_error_retryable(text: str) -> bool:
    value = str(text or "").lower()
    if not value:
        return True
    if any(marker in value for marker in _GPT_IMAGEGEN_FALLBACK_NON_RETRYABLE_MARKERS):
        return False
    return any(marker in value for marker in _GPT_IMAGEGEN_FALLBACK_RETRYABLE_MARKERS)


def _gpt_imagegen_fallback_payload(
    *,
    capability_id: str,
    payload: dict[str, Any],
    reason: str,
) -> tuple[dict[str, Any], str | None]:
    fallback_payload = dict(payload or {})
    original_task_id = str(fallback_payload.get("task_id") or "").strip()
    if not original_task_id:
        return fallback_payload, None
    prompt = _gpt_imagegen_prompt_from_payload(capability_id, fallback_payload)
    reason_text = str(reason or "").lower()
    if prompt and any(marker in reason_text for marker in _GPT_IMAGEGEN_FALLBACK_NEW_TASK_MARKERS):
        fallback_payload.pop("task_id", None)
        return fallback_payload, original_task_id[:120]
    return fallback_payload, None


async def _maybe_run_gpt_imagegen_fallback(
    *,
    capability_id: str,
    payload: dict[str, Any],
    primary_result: dict[str, Any] | None,
    db: AsyncSession | None,
    user_id: str | None,
    on_event: RunnerEventCallback | None,
    exit_code: int | None = None,
    error: str = "",
) -> dict[str, Any] | None:
    if capability_id not in _GPT_IMAGEGEN_CAPABILITY_IDS:
        return None
    reason = _gpt_imagegen_fallback_reason(primary_result, exit_code=exit_code, error=error)
    if reason is None:
        return None
    config = await _gpt_imagegen_fallback_config()
    if not config.get("enabled"):
        return None
    fallback_payload, replaced_task_id = _gpt_imagegen_fallback_payload(
        capability_id=capability_id,
        payload=payload,
        reason=reason,
    )
    await _emit_runner_event(on_event, {
        "type": "fallback_started",
        "provider": "toapis",
        "reason": str(reason)[:300],
        "replaced_task_id": replaced_task_id,
    })
    try:
        fallback_result = await _call_gpt_imagegen_toapis_fallback(
            capability_id=capability_id,
            payload=fallback_payload,
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        fallback_error = _redact(str(exc), {"api_key": config.get("api_key")})[:500]
        if isinstance(primary_result, dict):
            primary_result["fallback_attempted"] = True
            primary_result["fallback_provider"] = "toapis"
            primary_result["fallback_error"] = fallback_error
            if replaced_task_id:
                primary_result["fallback_replaced_task_id"] = replaced_task_id
        await _emit_runner_event(on_event, {
            "type": "fallback_failed",
            "provider": "toapis",
            "error": fallback_error[:300],
            "replaced_task_id": replaced_task_id,
        })
        return None

    fallback_result["primary_failure"] = _redact(str(reason), payload)[:500]
    if replaced_task_id:
        fallback_result["fallback_replaced_task_id"] = replaced_task_id
    try:
        fallback_result["saved_image_count"] = await save_direct_capability_image_history(
            db,
            user_id=user_id,
            capability_id=capability_id,
            payload=fallback_payload,
            result=fallback_result,
        )
    except Exception as exc:  # noqa: BLE001
        if db:
            await db.rollback()
        fallback_result["saved_image_count"] = 0
        fallback_result["image_history_error"] = str(exc)[:300]
    await _emit_runner_event(on_event, {
        "type": "fallback_completed",
        "provider": "toapis",
        "status": fallback_result.get("status"),
    })
    return {
        "capability_id": capability_id,
        "result": fallback_result,
    }


async def _emit_runner_event(
    on_event: RunnerEventCallback | None,
    event: dict[str, Any],
) -> None:
    if on_event is not None:
        result = on_event(event)
        if inspect.isawaitable(result):
            await result


async def _write_runner_stdin(proc: Any, stdin: bytes) -> None:
    if proc.stdin is None:
        return
    try:
        proc.stdin.write(stdin)
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        return
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
    try:
        await proc.stdin.wait_closed()
    except (AttributeError, BrokenPipeError, ConnectionResetError):
        pass


async def _read_runner_stream(
    stream: Any,
    stream_name: str,
    *,
    payload: dict[str, Any],
    on_event: RunnerEventCallback | None,
) -> bytes:
    chunks = bytearray()
    if stream is None:
        return b""
    while True:
        chunk = await stream.readline()
        if not chunk:
            break
        chunks.extend(chunk)
        text = _redact(chunk.decode("utf-8", errors="replace").rstrip("\r\n"), payload)
        if text:
            await _emit_runner_event(on_event, {
                "type": "log",
                "stream": stream_name,
                "level": "warn" if stream_name == "stderr" else "info",
                "text": text,
            })
    return bytes(chunks)


async def _communicate_runner_streaming(
    proc: Any,
    *,
    stdin: bytes,
    payload: dict[str, Any],
    on_event: RunnerEventCallback | None,
) -> tuple[bytes, bytes]:
    stdout_task = asyncio.create_task(
        _read_runner_stream(proc.stdout, "stdout", payload=payload, on_event=on_event)
    )
    stderr_task = asyncio.create_task(
        _read_runner_stream(proc.stderr, "stderr", payload=payload, on_event=on_event)
    )
    try:
        await _write_runner_stdin(proc, stdin)
        await proc.wait()
        stdout_bytes, stderr_bytes = await asyncio.gather(stdout_task, stderr_task)
    except BaseException:
        for task in (stdout_task, stderr_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise
    return stdout_bytes, stderr_bytes


async def run_direct_capability(
    capability_id: str,
    body: dict[str, Any] | None,
    *,
    db: AsyncSession | None = None,
    user_id: str | None = None,
    current_user: Any | None = None,
    on_event: RunnerEventCallback | None = None,
) -> dict[str, Any]:
    skill_dir, meta, _contract = _resolve_capability(capability_id)
    if current_user is not None:
        _require_direct_capability_access(
            _capability_summary(skill_dir, meta, _contract),
            current_user,
            action="execute",
        )
    payload_raw = (body or {}).get("params", body or {})
    if not isinstance(payload_raw, dict):
        raise HTTPException(status_code=400, detail="params must be an object")
    payload = _normalize_direct_capability_payload(capability_id, payload_raw)
    script = skill_dir / "scripts" / "main.py"
    if not script.exists():
        raise HTTPException(status_code=404, detail="direct capability runner not found")

    dedupe_key = await _acquire_direct_run_dedupe_key(
        _direct_run_dedupe_key(user_id=user_id, capability_id=capability_id, payload=payload)
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script),
            cwd=str(skill_dir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_subprocess_env(),
        )
        await _emit_runner_event(on_event, {"type": "process_started", "pid": proc.pid})
        stdin = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                _communicate_runner_streaming(proc, stdin=stdin, payload=payload, on_event=on_event),
                timeout=_runner_timeout(meta, payload),
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            await _emit_runner_event(on_event, {"type": "process_timeout"})
            fallback = await _maybe_run_gpt_imagegen_fallback(
                capability_id=capability_id,
                payload=payload,
                primary_result=None,
                db=db,
                user_id=user_id,
                on_event=on_event,
                error="primary timed out",
            )
            if fallback is not None:
                return fallback
            raise HTTPException(status_code=504, detail="direct capability timed out")
        await _emit_runner_event(on_event, {"type": "process_exit", "exit_code": proc.returncode})

        stdout = _redact(stdout_bytes.decode("utf-8", errors="replace").strip(), payload)
        stderr = _redact(stderr_bytes.decode("utf-8", errors="replace").strip(), payload)
        try:
            result: Any = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError:
            result = {"stdout": stdout}
        if not isinstance(result, dict):
            result = {"output": result}
        result.setdefault("kind", "direct_capability")
        result.setdefault("status", "completed" if proc.returncode == 0 else "failed")
        result["exit_code"] = proc.returncode
        if stderr and "stderr" not in result:
            result["stderr"] = stderr[-8000:]
        if proc.returncode != 0:
            result["status"] = "failed"
        fallback = await _maybe_run_gpt_imagegen_fallback(
            capability_id=capability_id,
            payload=payload,
            primary_result=result,
            db=db,
            user_id=user_id,
            on_event=on_event,
            exit_code=proc.returncode,
        )
        if fallback is not None:
            return fallback
        if proc.returncode == 0 and str(result.get("status") or "").lower() != "failed":
            try:
                result["saved_image_count"] = await save_direct_capability_image_history(
                    db,
                    user_id=user_id,
                    capability_id=capability_id,
                    payload=payload,
                    result=result,
                )
            except Exception as exc:  # noqa: BLE001
                if db:
                    await db.rollback()
                result["saved_image_count"] = 0
                result["image_history_error"] = str(exc)[:300]
        return {
            "capability_id": capability_id,
            "result": result,
        }
    finally:
        await _release_direct_run_dedupe_key(dedupe_key)


def _task_result_root(response: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    result = response.get("result")
    return result if isinstance(result, dict) else response


def _task_status_from_response(response: dict[str, Any] | None) -> str:
    root = _task_result_root(response)
    status = str(root.get("status") or "").strip().lower()
    if status:
        return status
    if root.get("exit_code"):
        return "failed"
    return "unknown"


def _task_error_from_response(response: dict[str, Any] | None) -> str:
    root = _task_result_root(response)
    error = root.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    if error:
        return str(error)
    for key in ("stderr", "stdout"):
        raw = root.get(key)
        if raw:
            return str(raw)[-1000:]
    return ""


def _is_rate_limited_response(response: dict[str, Any] | None, error: str = "") -> bool:
    text = error or _task_error_from_response(response)
    if response:
        try:
            text = f"{text}\n{json.dumps(response, ensure_ascii=False, default=str)}"
        except TypeError:
            pass
    text = text.lower()
    return "429" in text or "rate_limit" in text or "rate limit" in text


def _task_image_urls(response: dict[str, Any] | None) -> list[str]:
    root = _task_result_root(response)
    values = root.get("image_urls")
    if isinstance(values, list):
        return [str(item) for item in values if item]
    value = root.get("image_url") or root.get("url")
    return [str(value)] if value else []


def direct_capability_result_image_urls(response: dict[str, Any] | None) -> list[str]:
    """Return only generated image URLs from a governed capability result.

    This public adapter lets other control-plane workflows reuse Hall image
    generation without knowing provider-specific response nesting.  Uploaded
    reference URLs are intentionally excluded by the existing collectors.
    """

    root = _task_result_root(response)
    urls = _collect_explicit_result_image_urls(root) or _collect_result_image_urls(root)
    uploaded = set(_collect_uploaded_image_urls(root))
    return list(dict.fromkeys(str(item) for item in urls if item and str(item) not in uploaded))


async def _saved_image_history_urls_for_task(db: AsyncSession, row: DirectCapabilityTask) -> list[str]:
    task_ids = {
        str(value).strip()
        for value in (
            row.upstream_task_id,
            _pick_nested_text(row.result if isinstance(row.result, dict) else {}, ("task_id", "taskId", "job_id", "jobId", "id")),
        )
        if value
    }
    if not task_ids:
        return []
    rows = (await db.execute(
        select(DirectCapabilityImageHistory.image_url)
        .where(DirectCapabilityImageHistory.user_id == row.user_id)
        .where(DirectCapabilityImageHistory.capability_id == row.capability_id)
        .where(DirectCapabilityImageHistory.task_id.in_(task_ids))
        .order_by(DirectCapabilityImageHistory.created_at.asc(), DirectCapabilityImageHistory.id.asc())
        .limit(20)
    )).scalars().all()
    urls: list[str] = []
    for item in rows:
        url = str(item or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


async def _task_has_saved_image_history(db: AsyncSession, row: DirectCapabilityTask) -> bool:
    return bool(await _saved_image_history_urls_for_task(db, row))


async def _complete_task_if_result_is_saved(db: AsyncSession, row: DirectCapabilityTask) -> bool:
    if row.status in _TASK_TERMINAL_STATUSES and row.status != "completed":
        return False
    result = row.result if isinstance(row.result, dict) else {}
    image_urls = _task_image_urls(result)
    if not image_urls:
        image_urls = await _saved_image_history_urls_for_task(db, row)
    if not image_urls:
        return False
    now = now_bjt()
    root = _task_result_root(result)
    root["status"] = "completed"
    root["image_urls"] = image_urls
    if isinstance(result.get("result"), dict):
        result["result"] = root
    else:
        result = root
    row.result = result
    row.status = "completed"
    row.error = None
    row.completed_at = row.completed_at or now
    row.next_poll_at = None
    row.updated_at = now
    await db.flush()
    return True


def _serialize_direct_capability_task(row: DirectCapabilityTask) -> dict[str, Any]:
    result = row.result if isinstance(row.result, dict) else {}
    return {
        "id": row.id,
        "user_id": row.user_id,
        "capability_id": row.capability_id,
        "workspace_id": row.workspace_id,
        "workspace_name": row.workspace_name,
        "upstream_task_id": row.upstream_task_id,
        "task_id": row.upstream_task_id,
        "status": row.status,
        "prompt": row.prompt,
        "params": row.params or {},
        "result": result,
        "image_urls": _task_image_urls(result),
        "error": row.error,
        "attempt_count": row.attempt_count,
        "next_poll_at": row.next_poll_at,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _task_duration_seconds(started_at: Any, completed_at: Any) -> float | None:
    if not started_at or not completed_at:
        return None
    try:
        seconds = (completed_at - started_at).total_seconds()
    except Exception:  # noqa: BLE001
        return None
    return seconds if seconds > 0 else None


def _is_successful_direct_capability_status(status: str | None, error: str | None = None) -> bool:
    value = str(status or "").lower()
    if value in {"completed", "succeeded", "success"} and not error:
        return True
    return False


def _is_failed_direct_capability_status(status: str | None) -> bool:
    return str(status or "").lower() in {"failed", "error", "canceled", "cancelled"}


async def _direct_capability_task_stats(
    db: AsyncSession,
    where: tuple[Any, ...],
) -> dict[str, Any]:
    rows = (await db.execute(
        select(
            DirectCapabilityTask.status,
            DirectCapabilityTask.error,
            DirectCapabilityTask.started_at,
            DirectCapabilityTask.completed_at,
            DirectCapabilityTask.created_at,
        )
        .where(*where)
        .order_by(DirectCapabilityTask.created_at.desc())
    )).all()
    total = len(rows)
    success_count = 0
    failed_count = 0
    durations: list[float] = []
    last_run_at = None
    for idx, row in enumerate(rows):
        status = row.status
        if idx == 0:
            last_run_at = row.created_at
        if _is_successful_direct_capability_status(status, row.error):
            success_count += 1
        if _is_failed_direct_capability_status(status):
            failed_count += 1
        duration = _task_duration_seconds(row.started_at or row.created_at, row.completed_at)
        if duration is not None:
            durations.append(duration)
    terminal_count = success_count + failed_count
    success_rate = round(success_count / terminal_count * 100, 1) if terminal_count else None
    avg_duration_seconds = round(sum(durations) / len(durations), 3) if durations else None
    return {
        "total": total,
        "success_count": success_count,
        "failed_count": failed_count,
        "terminal_count": terminal_count,
        "success_rate": success_rate,
        "avg_duration_seconds": avg_duration_seconds,
        "last_run_at": last_run_at,
    }


async def _active_task_count(
    db: AsyncSession,
    *,
    user_id: str,
    capability_id: str,
    exclude_task_id: str | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(DirectCapabilityTask)
        .where(DirectCapabilityTask.user_id == user_id)
        .where(DirectCapabilityTask.capability_id == capability_id)
        .where(DirectCapabilityTask.status.in_(_TASK_ACTIVE_STATUSES))
    )
    if exclude_task_id:
        stmt = stmt.where(DirectCapabilityTask.id != exclude_task_id)
    return int((await db.execute(stmt)).scalar() or 0)


def _normalize_task_payload(payload: dict[str, Any], row: DirectCapabilityTask) -> dict[str, Any]:
    normalized = _normalize_direct_capability_payload(row.capability_id, payload or {})
    normalized["wait"] = False
    normalized["download"] = False
    if row.prompt and not normalized.get("prompt") and not normalized.get("message"):
        normalized["prompt"] = row.prompt
    if row.workspace_id:
        normalized["workspace_id"] = row.workspace_id
    if row.workspace_name:
        normalized["workspace_name"] = row.workspace_name
    return normalized


def _task_dedupe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _task_dedupe_value(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_task_dedupe_value(item) for item in value]
    return value


def _task_dedupe_params(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        key_text = str(key)
        if key_text in _TASK_DEDUPE_IGNORED_PARAM_KEYS:
            continue
        if key_text in _TASK_DEDUPE_EMPTY_EQUIVALENT_KEYS and value in (None, "", [], {}):
            continue
        normalized[key_text] = _task_dedupe_value(value)
    return normalized


def _task_params_equivalent(left: Any, right: Any) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return _task_dedupe_params(left) == _task_dedupe_params(right)


def _direct_run_dedupe_key(
    *,
    user_id: str | None,
    capability_id: str,
    payload: dict[str, Any],
) -> str | None:
    if payload.get("task_id"):
        return None
    prompt = str(payload.get("prompt") or payload.get("message") or "").strip()
    if not user_id or not prompt:
        return None
    params = _task_dedupe_params({**payload, "wait": False, "download": False})
    encoded = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"{user_id}:{capability_id}:{digest}"


def _task_creation_lock_key(
    *,
    user_id: str,
    capability_id: str,
    workspace_id: str | None,
    params: dict[str, Any],
) -> int:
    payload = {
        "user_id": user_id,
        "capability_id": capability_id,
        "workspace_id": workspace_id or "",
        "params": _task_dedupe_params(params),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(encoded.encode("utf-8")).digest()[:8], "big", signed=True)


async def _acquire_task_creation_lock(db: AsyncSession, key: int) -> None:
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


async def _acquire_direct_run_dedupe_key(key: str | None) -> str | None:
    if not key:
        return None
    async with _DIRECT_RUN_DEDUPE_LOCK:
        if key in _DIRECT_RUN_IN_FLIGHT_KEYS:
            raise HTTPException(status_code=409, detail="相同生成请求正在运行，请勿重复提交")
        _DIRECT_RUN_IN_FLIGHT_KEYS.add(key)
    return key


async def _release_direct_run_dedupe_key(key: str | None) -> None:
    if not key:
        return
    async with _DIRECT_RUN_DEDUPE_LOCK:
        _DIRECT_RUN_IN_FLIGHT_KEYS.discard(key)


async def _find_recent_duplicate_direct_capability_task(
    db: AsyncSession,
    *,
    user_id: str,
    capability_id: str,
    workspace_id: str | None,
    prompt: str | None,
    params: dict[str, Any],
    now,
) -> DirectCapabilityTask | None:
    if not prompt:
        return None
    threshold = now - timedelta(seconds=_TASK_DUPLICATE_WINDOW_SECONDS)
    rows = (await db.execute(
        select(DirectCapabilityTask)
        .where(DirectCapabilityTask.user_id == user_id)
        .where(DirectCapabilityTask.capability_id == capability_id)
        .where(DirectCapabilityTask.workspace_id == workspace_id)
        .where(DirectCapabilityTask.prompt == prompt)
        .where(DirectCapabilityTask.status.in_(_TASK_DEDUPE_STATUSES))
        .where(DirectCapabilityTask.created_at >= threshold)
        .order_by(DirectCapabilityTask.created_at.desc())
        .limit(20)
    )).scalars().all()
    for row in rows:
        if _task_params_equivalent(row.params or {}, params):
            return row
    return None


async def _apply_runner_response_to_task(
    db: AsyncSession,
    row: DirectCapabilityTask,
    response: dict[str, Any],
    *,
    now,
) -> None:
    root = _task_result_root(response)
    row.result = response
    row.updated_at = now
    upstream_task_id = _pick_nested_text(response, ("task_id", "taskId", "job_id", "jobId", "id"))[:120]
    if upstream_task_id:
        row.upstream_task_id = upstream_task_id
    status = _task_status_from_response(response)
    if status == "completed":
        row.status = "completed"
        row.error = None
        row.completed_at = now
        row.next_poll_at = None
    elif status == "failed" or int(root.get("exit_code") or 0) != 0:
        error_text = _task_error_from_response(response)
        if _is_rate_limited_response(response, error_text):
            row.status = "rate_limited"
            row.error = "请求太多，已自动排队重试"
            row.next_poll_at = now + timedelta(seconds=_TASK_RATE_LIMIT_BACKOFF_SECONDS)
        else:
            row.status = "failed"
            row.error = error_text or "任务失败"
            row.completed_at = now
            row.next_poll_at = None
    else:
        row.status = "in_progress"
        row.error = None
        row.next_poll_at = now + timedelta(seconds=_TASK_POLL_INTERVAL_SECONDS)
    await db.flush()


async def _submit_direct_capability_task(
    db: AsyncSession,
    row: DirectCapabilityTask,
    *,
    current_user: Any | None,
    on_event: RunnerEventCallback | None = None,
) -> None:
    now = now_bjt()
    row.status = "submitting"
    row.started_at = row.started_at or now
    row.updated_at = now
    row.attempt_count = int(row.attempt_count or 0) + 1
    row.error = None
    await db.flush()
    async def emit_task_runner_event(event: dict[str, Any]) -> None:
        if on_event is None:
            return
        await on_event({
            **event,
            "task_id": row.id,
            "capability_id": row.capability_id,
        })

    runner_kwargs: dict[str, Any] = {
        "db": db,
        "user_id": row.user_id,
        "current_user": current_user,
    }
    if on_event is not None:
        runner_kwargs["on_event"] = emit_task_runner_event
    try:
        response = await run_direct_capability(
            row.capability_id,
            {"params": _normalize_task_payload(row.params or {}, row)},
            **runner_kwargs,
        )
    except HTTPException as exc:
        error_text = str(exc.detail or exc.status_code)
        if exc.status_code == 409:
            row.result = {"status": "in_progress", "message": error_text}
            row.status = "in_progress"
            row.error = None
            row.next_poll_at = now + timedelta(seconds=_TASK_POLL_INTERVAL_SECONDS)
            row.completed_at = None
        else:
            row.result = {"status": "failed", "error": error_text}
            row.status = "rate_limited" if exc.status_code == 429 else "failed"
            row.error = "请求太多，已自动排队重试" if exc.status_code == 429 else error_text
            row.next_poll_at = now + timedelta(seconds=_TASK_RATE_LIMIT_BACKOFF_SECONDS) if exc.status_code == 429 else None
            row.completed_at = None if exc.status_code == 429 else now
        row.updated_at = now_bjt()
        await db.flush()
        return
    except Exception as exc:  # noqa: BLE001
        error_text = str(exc)
        row.result = {"status": "failed", "error": error_text}
        row.status = "rate_limited" if _is_rate_limited_response(None, error_text) else "failed"
        row.error = "请求太多，已自动排队重试" if row.status == "rate_limited" else error_text
        row.next_poll_at = now + timedelta(seconds=_TASK_RATE_LIMIT_BACKOFF_SECONDS) if row.status == "rate_limited" else None
        row.completed_at = None if row.status == "rate_limited" else now
        row.updated_at = now_bjt()
        await db.flush()
        return
    await _apply_runner_response_to_task(db, row, response, now=now_bjt())


async def _poll_direct_capability_task(
    db: AsyncSession,
    row: DirectCapabilityTask,
    *,
    current_user: Any | None,
    on_event: RunnerEventCallback | None = None,
) -> None:
    if not row.upstream_task_id:
        await _submit_direct_capability_task(db, row, current_user=current_user, on_event=on_event)
        return
    async def emit_task_runner_event(event: dict[str, Any]) -> None:
        if on_event is None:
            return
        await on_event({
            **event,
            "task_id": row.id,
            "capability_id": row.capability_id,
        })

    runner_kwargs: dict[str, Any] = {
        "db": db,
        "user_id": row.user_id,
        "current_user": current_user,
    }
    if on_event is not None:
        runner_kwargs["on_event"] = emit_task_runner_event
    try:
        response = await run_direct_capability(
            row.capability_id,
            {"params": _normalize_task_payload({"task_id": row.upstream_task_id}, row)},
            **runner_kwargs,
        )
    except HTTPException as exc:
        now = now_bjt()
        row.status = "rate_limited" if exc.status_code == 429 else "failed"
        row.error = "请求太多，已自动排队重试" if exc.status_code == 429 else str(exc.detail or exc.status_code)
        row.next_poll_at = now + timedelta(seconds=_TASK_RATE_LIMIT_BACKOFF_SECONDS) if exc.status_code == 429 else None
        row.completed_at = None if exc.status_code == 429 else now
        row.updated_at = now
        await db.flush()
        return
    except Exception as exc:  # noqa: BLE001
        now = now_bjt()
        error_text = str(exc)
        row.status = "rate_limited" if _is_rate_limited_response(None, error_text) else "failed"
        row.error = "请求太多，已自动排队重试" if row.status == "rate_limited" else error_text
        row.next_poll_at = now + timedelta(seconds=_TASK_RATE_LIMIT_BACKOFF_SECONDS) if row.status == "rate_limited" else None
        row.completed_at = None if row.status == "rate_limited" else now
        row.updated_at = now
        await db.flush()
        return
    await _apply_runner_response_to_task(db, row, response, now=now_bjt())


async def _advance_direct_capability_task(
    db: AsyncSession,
    row: DirectCapabilityTask,
    *,
    current_user: Any | None,
    on_event: RunnerEventCallback | None = None,
) -> None:
    if row.status in _TASK_TERMINAL_STATUSES:
        return
    if await _complete_task_if_result_is_saved(db, row):
        return
    now = now_bjt()
    if _direct_capability_task_stale(row, now):
        row.status = "failed"
        row.error = "任务长时间未返回结果，已自动结束；可点击重试"
        row.result = {
            **(row.result if isinstance(row.result, dict) else {}),
            "status": "failed",
            "error": row.error,
        }
        row.completed_at = now
        row.next_poll_at = None
        row.updated_at = now
        await db.flush()
        return
    if row.next_poll_at and row.next_poll_at > now:
        return
    if row.status == "queued":
        active_count = await _active_task_count(
            db,
            user_id=row.user_id,
            capability_id=row.capability_id,
            exclude_task_id=row.id,
        )
        if active_count >= _DEFAULT_USER_ACTIVE_TASK_LIMIT:
            return
        await _submit_direct_capability_task(db, row, current_user=current_user, on_event=on_event)
        return
    if row.status == "rate_limited" and not row.upstream_task_id:
        active_count = await _active_task_count(
            db,
            user_id=row.user_id,
            capability_id=row.capability_id,
            exclude_task_id=row.id,
        )
        if active_count >= _DEFAULT_USER_ACTIVE_TASK_LIMIT:
            return
        await _submit_direct_capability_task(db, row, current_user=current_user, on_event=on_event)
        return
    if row.status in {"submitting", "in_progress", "rate_limited"}:
        await _poll_direct_capability_task(db, row, current_user=current_user, on_event=on_event)


def _task_advance_stmt(stmt):
    return stmt.with_for_update(skip_locked=True)


async def _advance_direct_capability_tasks(
    db: AsyncSession,
    *,
    user_id: str,
    capability_id: str,
    current_user: Any | None,
) -> None:
    rows = (await db.execute(
        _task_advance_stmt(
            select(DirectCapabilityTask)
            .where(DirectCapabilityTask.user_id == user_id)
            .where(DirectCapabilityTask.capability_id == capability_id)
            .where(DirectCapabilityTask.status.notin_(_TASK_TERMINAL_STATUSES))
            .order_by(DirectCapabilityTask.created_at.asc())
            .limit(20)
        )
    )).scalars().all()
    for row in rows:
        await _advance_direct_capability_task(db, row, current_user=current_user)


def _direct_capability_task_stale(row: DirectCapabilityTask, now) -> bool:
    if row.status in _TASK_TERMINAL_STATUSES:
        return False
    if row.status == "queued":
        return False
    started = row.started_at or row.created_at
    if not started:
        return False
    try:
        age = (now - started).total_seconds()
    except Exception:  # noqa: BLE001
        return False
    return age > max(_TASK_STALE_TIMEOUT_SECONDS, 60)


def _system_direct_capability_user(user_id: str) -> Any:
    return SimpleNamespace(
        id=user_id,
        role="admin",
        department=None,
        can_view_all=True,
        is_active=True,
        state="active",
    )


async def advance_due_direct_capability_tasks(
    db: AsyncSession,
    *,
    capability_ids: list[str] | tuple[str, ...] | None = None,
    limit: int = 20,
) -> dict[str, int]:
    """Advance due direct-capability tasks without relying on an open browser page."""
    now = now_bjt()
    limit = max(1, min(int(limit or 20), 100))
    stmt = (
        select(DirectCapabilityTask)
        .where(DirectCapabilityTask.status.notin_(_TASK_TERMINAL_STATUSES))
        .where(
            or_(
                DirectCapabilityTask.next_poll_at.is_(None),
                DirectCapabilityTask.next_poll_at <= now,
                DirectCapabilityTask.started_at <= now - timedelta(seconds=max(_TASK_STALE_TIMEOUT_SECONDS, 60)),
            )
        )
        .order_by(DirectCapabilityTask.created_at.asc())
        .limit(limit)
    )
    if capability_ids:
        stmt = stmt.where(DirectCapabilityTask.capability_id.in_([str(item) for item in capability_ids]))
    rows = (await db.execute(_task_advance_stmt(stmt))).scalars().all()
    advanced = 0
    failed_stale = 0
    for row in rows:
        before = row.status
        await _advance_direct_capability_task(
            db,
            row,
            current_user=_system_direct_capability_user(row.user_id),
        )
        if row.status != before or row.updated_at == now:
            advanced += 1
        if before not in _TASK_TERMINAL_STATUSES and row.status == "failed":
            failed_stale += 1
    return {"scanned": len(rows), "advanced": advanced, "failed_stale": failed_stale}


async def create_direct_capability_task(
    db: AsyncSession,
    capability_id: str,
    body: dict[str, Any] | None,
    *,
    user_id: str,
    current_user: Any | None = None,
    on_event: RunnerEventCallback | None = None,
    force_new: bool = False,
) -> dict[str, Any]:
    skill_dir, meta, contract = _resolve_capability(capability_id)
    if current_user is not None:
        _require_direct_capability_access(
            _capability_summary(skill_dir, meta, contract),
            current_user,
            action="execute",
        )
    payload_raw = (body or {}).get("params", body or {})
    if not isinstance(payload_raw, dict):
        raise HTTPException(status_code=400, detail="params must be an object")
    payload = _normalize_direct_capability_payload(capability_id, payload_raw)
    workspace_id = str(payload.get("workspace_id") or "").strip()[:80] or None
    workspace_name = str(payload.get("workspace_name") or "").strip()[:120] or None
    prompt = str(payload.get("prompt") or payload.get("message") or "").strip()[:4000] or None
    params = {**payload, "wait": False, "download": False}
    now = now_bjt()
    await _acquire_task_creation_lock(
        db,
        _task_creation_lock_key(
            user_id=user_id,
            capability_id=capability_id,
            workspace_id=workspace_id,
            params=params,
        ),
    )
    if not force_new:
        duplicate = await _find_recent_duplicate_direct_capability_task(
            db,
            user_id=user_id,
            capability_id=capability_id,
            workspace_id=workspace_id,
            prompt=prompt,
            params=params,
            now=now,
        )
        if duplicate is not None:
            return _serialize_direct_capability_task(duplicate)
    row = DirectCapabilityTask(
        id=str(uuid.uuid4()),
        user_id=user_id,
        capability_id=capability_id,
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        status="queued",
        prompt=prompt,
        params=params,
        result=None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    return _serialize_direct_capability_task(row)


async def list_direct_capability_tasks(
    db: AsyncSession,
    capability_id: str,
    *,
    user_id: str,
    current_user: Any | None = None,
    page: int = 1,
    page_size: int = 50,
    advance: bool = False,
) -> dict[str, Any]:
    skill_dir, meta, contract = _resolve_capability(capability_id)
    if current_user is not None:
        _require_direct_capability_access(
            _capability_summary(skill_dir, meta, contract),
            current_user,
            action="read",
        )
    if advance:
        await _advance_direct_capability_tasks(
            db,
            user_id=user_id,
            capability_id=capability_id,
            current_user=current_user,
        )
    page_size = min(max(int(page_size or 50), 1), 100)
    page = max(int(page or 1), 1)
    user_scope_ids = await _direct_capability_user_scope_ids(db, user_id)
    where = (
        DirectCapabilityTask.user_id.in_(user_scope_ids),
        DirectCapabilityTask.capability_id == capability_id,
    )
    total = int((await db.execute(
        select(func.count()).select_from(DirectCapabilityTask).where(*where)
    )).scalar() or 0)
    rows = (await db.execute(
        select(DirectCapabilityTask)
        .where(*where)
        .order_by(DirectCapabilityTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    if advance:
        for row in rows:
            await _complete_task_if_result_is_saved(db, row)
    return {
        "items": [_serialize_direct_capability_task(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
        "active_limit": _DEFAULT_USER_ACTIVE_TASK_LIMIT,
        "stats": await _direct_capability_task_stats(db, where),
    }


async def _get_direct_capability_task_row(
    db: AsyncSession,
    *,
    capability_id: str,
    task_id: str,
    user_id: str,
) -> DirectCapabilityTask | None:
    user_scope_ids = await _direct_capability_user_scope_ids(db, user_id)
    row = await db.get(DirectCapabilityTask, task_id)
    if row and row.capability_id == capability_id and row.user_id in user_scope_ids:
        return row
    return (await db.execute(
        select(DirectCapabilityTask)
        .where(DirectCapabilityTask.user_id.in_(user_scope_ids))
        .where(DirectCapabilityTask.capability_id == capability_id)
        .where(DirectCapabilityTask.upstream_task_id == task_id)
        .order_by(DirectCapabilityTask.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()


async def get_direct_capability_task(
    db: AsyncSession,
    capability_id: str,
    task_id: str,
    *,
    user_id: str,
    current_user: Any | None = None,
    advance: bool = True,
    on_event: RunnerEventCallback | None = None,
) -> dict[str, Any]:
    row = await _get_direct_capability_task_row(
        db,
        capability_id=capability_id,
        task_id=task_id,
        user_id=user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="direct capability task not found")
    if current_user is not None:
        skill_dir, meta, contract = _resolve_capability(capability_id)
        _require_direct_capability_access(
            _capability_summary(skill_dir, meta, contract),
            current_user,
            action="read",
    )
    if advance:
        locked_row = (await db.execute(
            _task_advance_stmt(
                select(DirectCapabilityTask)
                .where(DirectCapabilityTask.id == row.id)
                .limit(1)
            )
        )).scalar_one_or_none()
        if locked_row is None:
            return _serialize_direct_capability_task(row)
        row = locked_row
        await _advance_direct_capability_task(db, row, current_user=current_user, on_event=on_event)
    return _serialize_direct_capability_task(row)


async def retry_direct_capability_task(
    db: AsyncSession,
    capability_id: str,
    task_id: str,
    *,
    user_id: str,
    current_user: Any | None = None,
) -> dict[str, Any]:
    row = await _get_direct_capability_task_row(
        db,
        capability_id=capability_id,
        task_id=task_id,
        user_id=user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="direct capability task not found")
    payload = dict(row.params or {})
    payload["workspace_id"] = row.workspace_id
    payload["workspace_name"] = row.workspace_name
    payload["wait"] = False
    payload["download"] = False
    return await create_direct_capability_task(
        db,
        capability_id,
        {"params": payload},
        user_id=user_id,
        current_user=current_user,
        force_new=True,
    )
