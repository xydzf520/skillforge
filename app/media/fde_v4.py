"""FDE material supply facade for Material Workbench 4.0.

The legacy ``video.*`` APIs remain available for historical project runs and
low-level workers.  This module exposes the business-facing ``material.*``
contract and keeps generation, technical validation, editorial selection,
delivery and attribution as separate state machines.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import role_matches_any
from app.auth.models import User
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime
from app.projects.models import Project, ProjectRun, ProjectRunAsset

from .models import (
    CloudVideoSyncOutbox,
    MediaCandidate,
    MediaDelivery,
    MediaEditorialDecision,
    MediaGenerationJob,
    MediaMaterialRequest,
    MediaPerformanceDaily,
    MediaReviewAnnotation,
    MediaReviewPreference,
)


MATERIAL_FDE_PROJECT_ID = "samplebrand-material-workbench"
MATERIAL_FDE_VERSION = "4.1.0"
MATERIAL_FDE_PROMPT_POLICY = "material-fde-h3-v1"

FDE_MEDIA_CAPABILITIES = {
    "material.workbench.snapshot",
    "material.request.create",
    "material.request.update",
    "material.request.list",
    "material.request.get",
    "material.asset.recommend",
    "material.request.submit",
    "material.candidate.generate",
    "material.candidate.list",
    "material.candidate.get",
    "material.candidate.manifest",
    "material.candidate.batch_tag",
    "material.candidate.tag.update",
    "material.review.session",
    "material.review.annotation.save",
    "material.decision.submit",
    "material.decision.batch_reject",
    "material.decision.batch_hold",
    "material.delivery.submit",
    "material.delivery.get",
    "material.performance.summary",
    "material.performance.list",
    "material.weekly_report.get",
    "material.review.preference.save",
}

REQUEST_SOURCES = {"editorial", "history", "market_structure", "operations"}
REQUEST_STATUSES = {
    "draft", "pending", "producing", "awaiting_selection", "partially_selected",
    "selected", "rejected", "delivered", "spending",
}
PRODUCTION_MODES = {"direct", "ai_strategy", "replay"}
EDITORIAL_DECISIONS = {"selected", "held", "rejected"}
TECHNICAL_STATUSES = {"checking", "passed", "repair_required", "failed"}
DELIVERY_STATUSES = {"not_requested", "pending", "syncing", "delivered", "failed"}
ATTRIBUTION_STATUSES = {"unlinked", "linking", "linked", "spending", "no_spend"}
MANAGER_ROLES = {"system_admin", "dept_admin", "aibp", "admin", "biz_owner", "ai_engineer"}
EDITORIAL_REVIEWER_ROLES = {"system_admin", "dept_admin", "aibp", "admin", "biz_owner", "ai_engineer"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:24]}"


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return min(max(result, minimum), maximum)


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _department_id(run: ProjectRun, project: Project | None = None) -> str:
    return _text(run.department_id or getattr(project, "department_id", None), 50) or "931765248"


def _can_manage(user: User) -> bool:
    return role_matches_any(user, MANAGER_ROLES)


def _can_make_editorial_decision(user: User) -> bool:
    """Use the platform role matrix; observers may view but cannot decide."""

    return role_matches_any(user, EDITORIAL_REVIEWER_ROLES)


def _assert_request_access(row: MediaMaterialRequest, user: User, run: ProjectRun, *, mutate: bool = False) -> None:
    if row.project_id != run.project_id or row.department_id != _department_id(run):
        raise AppError("MEDIA_MATERIAL_REQUEST_NOT_FOUND", 404)
    if mutate and row.requested_by != str(user.id) and not _can_manage(user):
        raise AppError("MEDIA_MATERIAL_REQUEST_FORBIDDEN", 403)


def _encode_cursor(created_at: datetime | None, row_id: str) -> str:
    payload = json.dumps({"at": isoformat_bjt(created_at), "id": row_id}, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: Any) -> tuple[datetime | None, str]:
    raw = _text(value, 600)
    if not raw:
        return None, ""
    try:
        padding = "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(raw + padding).decode("utf-8"))
        return parse_bjt_datetime(data.get("at")), _text(data.get("id"), 50)
    except Exception as exc:  # noqa: BLE001
        raise AppError("MEDIA_CURSOR_INVALID", 422) from exc


def _encode_request_cursor(priority: int, created_at: datetime | None, row_id: str) -> str:
    payload = json.dumps(
        {"priority": priority, "at": isoformat_bjt(created_at), "id": row_id}, ensure_ascii=False
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_request_cursor(value: Any) -> tuple[int | None, datetime | None, str]:
    raw = _text(value, 600)
    if not raw:
        return None, None, ""
    try:
        padding = "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(raw + padding).decode("utf-8"))
        priority = int(data.get("priority"))
        return priority, parse_bjt_datetime(data.get("at")), _text(data.get("id"), 50)
    except Exception as exc:  # noqa: BLE001
        raise AppError("MEDIA_CURSOR_INVALID", 422) from exc


def _encode_value_cursor(value: float, row_id: str) -> str:
    payload = json.dumps({"value": value, "id": row_id}, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_value_cursor(value: Any) -> tuple[float | None, str]:
    raw = _text(value, 600)
    if not raw:
        return None, ""
    try:
        padding = "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(raw + padding).decode("utf-8"))
        return float(data.get("value")), _text(data.get("id"), 50)
    except Exception as exc:  # noqa: BLE001
        raise AppError("MEDIA_CURSOR_INVALID", 422) from exc


def _serialize_request(row: MediaMaterialRequest) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "department_id": row.department_id,
        "requested_by": row.requested_by,
        "assignee_id": row.assignee_id,
        "source": row.source,
        "title": row.title,
        "status": row.status,
        "priority": row.priority,
        "deadline_at": isoformat_bjt(row.deadline_at),
        "business": row.business_json or {},
        "generation_participation": row.generation_participation_json or {},
        "generation_options": row.generation_options_json or {},
        "visual_prompt": row.visual_prompt,
        "script": row.script,
        "source_roles": row.source_roles_json or [],
        "production_mode": row.production_mode,
        "quantity": row.quantity,
        "duration_seconds": row.duration_seconds,
        "ratio": row.ratio,
        "output_preset_id": row.output_preset_id,
        "allow_ai_optimization": row.allow_ai_optimization,
        "allow_script_changes": row.allow_script_changes,
        "submitted_at": isoformat_bjt(row.submitted_at),
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


def _request_values(payload: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if not partial or "source" in payload:
        source = _text(payload.get("source"), 40) or "editorial"
        if source not in REQUEST_SOURCES:
            raise AppError("MEDIA_MATERIAL_REQUEST_INVALID", 422, {"field": "source"})
        values["source"] = source
    if not partial or "title" in payload:
        title = _text(payload.get("title"), 240)
        if not title:
            business = _dict(payload.get("business"))
            title = _text(business.get("product"), 120) or "未命名素材需求"
        values["title"] = title
    if "assignee_id" in payload:
        values["assignee_id"] = _text(payload.get("assignee_id"), 50) or None
    if not partial or "priority" in payload:
        values["priority"] = _int(payload.get("priority"), default=100, minimum=1, maximum=1000)
    if "deadline_at" in payload or not partial:
        values["deadline_at"] = parse_bjt_datetime(payload.get("deadline_at")) if payload.get("deadline_at") else None
    if not partial or "business" in payload:
        values["business_json"] = _dict(payload.get("business"))
    if not partial or "generation_participation" in payload:
        raw = _dict(payload.get("generation_participation"))
        values["generation_participation_json"] = {
            key: bool(raw.get(key)) for key in ("product", "sku", "platform", "selling_points", "promotion")
        }
    if not partial or "generation_options" in payload:
        raw_options = _dict(payload.get("generation_options"))
        values["generation_options_json"] = {
            "commercial_appearance": raw_options.get("commercial_appearance", True) is not False,
        }
    if not partial or "visual_prompt" in payload:
        values["visual_prompt"] = str(payload.get("visual_prompt") or "").strip()
    if not partial or "script" in payload:
        values["script"] = str(payload.get("script") or "").strip()
    if not partial or "source_roles" in payload:
        values["source_roles_json"] = [item for item in _list(payload.get("source_roles")) if isinstance(item, dict)]
    if not partial or "production_mode" in payload:
        mode = _text(payload.get("production_mode"), 30) or "direct"
        if mode not in PRODUCTION_MODES:
            raise AppError("MEDIA_MATERIAL_REQUEST_INVALID", 422, {"field": "production_mode"})
        values["production_mode"] = mode
    if not partial or "quantity" in payload:
        values["quantity"] = _int(payload.get("quantity"), default=1, minimum=1, maximum=600)
    if not partial or "duration_seconds" in payload:
        values["duration_seconds"] = _int(payload.get("duration_seconds"), default=5, minimum=2, maximum=60)
    if not partial or "ratio" in payload:
        ratio = _text(payload.get("ratio"), 20) or "9:16"
        if ratio not in {"9:16", "16:9", "1:1", "4:3", "3:4", "2:3", "3:2"}:
            raise AppError("MEDIA_MATERIAL_REQUEST_INVALID", 422, {"field": "ratio"})
        values["ratio"] = ratio
    if not partial or "output_preset_id" in payload:
        values["output_preset_id"] = _text(payload.get("output_preset_id"), 50) or "fast_preview"
    if not partial or "allow_ai_optimization" in payload:
        values["allow_ai_optimization"] = bool(payload.get("allow_ai_optimization"))
    if not partial or "allow_script_changes" in payload:
        values["allow_script_changes"] = bool(payload.get("allow_script_changes"))
    return values


async def _create_request(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    idempotency_key = _text(payload.get("idempotency_key"), 128) or _hash({
        "run": run.id,
        "user": user.id,
        "title": payload.get("title"),
        "visual_prompt": payload.get("visual_prompt"),
        "created_nonce": payload.get("client_nonce") or uuid4().hex,
    })
    existing = (
        await db.execute(
            select(MediaMaterialRequest).where(
                MediaMaterialRequest.project_id == project.id,
                MediaMaterialRequest.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing:
        _assert_request_access(existing, user, run)
        return {"request": _serialize_request(existing), "deduped": True}
    now = now_bjt()
    row = MediaMaterialRequest(
        id=_new_id("mmr"),
        project_id=project.id,
        project_run_id=run.id,
        department_id=_department_id(run, project),
        requested_by=str(user.id),
        status="draft",
        idempotency_key=idempotency_key,
        created_at=now,
        updated_at=now,
        **_request_values(payload),
    )
    db.add(row)
    await db.flush()
    return {"request": _serialize_request(row), "deduped": False}


async def _require_request(
    db: AsyncSession, user: User, run: ProjectRun, request_id: Any, *, mutate: bool = False
) -> MediaMaterialRequest:
    row = await db.get(MediaMaterialRequest, _text(request_id, 50))
    if row is None:
        raise AppError("MEDIA_MATERIAL_REQUEST_NOT_FOUND", 404)
    _assert_request_access(row, user, run, mutate=mutate)
    return row


async def _update_request(
    db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    row = await _require_request(db, user, run, payload.get("request_id"), mutate=True)
    if row.status not in {"draft", "pending", "rejected"}:
        raise AppError("MEDIA_MATERIAL_REQUEST_LOCKED", 409, {"status": row.status})
    for key, value in _request_values(payload, partial=True).items():
        setattr(row, key, value)
    row.updated_at = now_bjt()
    await db.flush()
    return {"request": _serialize_request(row)}


async def _submit_request(
    db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    row = await _require_request(db, user, run, payload.get("request_id"), mutate=True)
    if not row.visual_prompt and row.production_mode != "replay":
        raise AppError("MEDIA_MATERIAL_REQUEST_INVALID", 422, {"field": "visual_prompt"})
    if row.production_mode == "replay" and not any(
        item.get("technical_role") == "reference_video" or item.get("role") in {"motion_reference", "reference_video"}
        for item in row.source_roles_json or [] if isinstance(item, dict)
    ):
        raise AppError("MEDIA_REPLAY_SOURCE_VIDEO_REQUIRED", 422)
    if row.status == "draft":
        row.status = "pending"
        row.submitted_at = now_bjt()
        row.updated_at = row.submitted_at
    await db.flush()
    return {"request": _serialize_request(row), "next_action": "material.candidate.generate"}


async def _list_requests(
    db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    limit = _int(payload.get("limit"), default=30, minimum=1, maximum=100)
    conditions = [
        MediaMaterialRequest.project_id == run.project_id,
        MediaMaterialRequest.department_id == _department_id(run),
    ]
    if not _can_manage(user) or bool(payload.get("mine_only")):
        conditions.append(MediaMaterialRequest.requested_by == str(user.id))
    status = _text(payload.get("status"), 40)
    if status:
        if status not in REQUEST_STATUSES:
            raise AppError("MEDIA_MATERIAL_REQUEST_FILTER_INVALID", 422, {"field": "status"})
        conditions.append(MediaMaterialRequest.status == status)
    query = _text(payload.get("query"), 120)
    if query:
        escaped = query.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        conditions.append(or_(MediaMaterialRequest.title.ilike(pattern, escape="\\"), MediaMaterialRequest.id.ilike(pattern, escape="\\")))
    cursor_priority, cursor_at, cursor_id = _decode_request_cursor(payload.get("cursor"))
    if cursor_priority is not None and cursor_at and cursor_id:
        conditions.append(or_(
            MediaMaterialRequest.priority < cursor_priority,
            and_(MediaMaterialRequest.priority == cursor_priority, MediaMaterialRequest.created_at < cursor_at),
            and_(
                MediaMaterialRequest.priority == cursor_priority,
                MediaMaterialRequest.created_at == cursor_at,
                MediaMaterialRequest.id < cursor_id,
            ),
        ))
    rows = (
        await db.execute(
            select(MediaMaterialRequest).where(*conditions)
            .order_by(MediaMaterialRequest.priority.desc(), MediaMaterialRequest.created_at.desc(), MediaMaterialRequest.id.desc())
            .limit(limit + 1)
        )
    ).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [_serialize_request(row) for row in rows],
        "has_more": has_more,
        "next_cursor": _encode_request_cursor(rows[-1].priority, rows[-1].created_at, rows[-1].id) if has_more and rows else None,
    }


def _technical_state(job: MediaGenerationJob) -> tuple[str, dict[str, Any]]:
    result = _dict(job.result_json)
    technical = _dict(result.get("technical_validation"))
    manifest = _dict(result.get("review_manifest"))
    if job.status in {"failed", "cancelled"}:
        return "failed", {"job_status": job.status, "error": job.error, "technical_validation": technical}
    explicit_failed = technical.get("passed") is False or technical.get("status") in {"failed", "invalid", "repair_required"}
    if explicit_failed:
        return "repair_required", {"job_status": job.status, "technical_validation": technical, "manifest": manifest}
    ready = (
        technical.get("passed") is True
        or technical.get("status") in {"passed", "ok", "valid"}
        or manifest.get("status") in {"ready", "passed", "completed"}
    )
    if ready and job.result_asset_id:
        return "passed", {"job_status": job.status, "technical_validation": technical, "manifest": manifest}
    # Jobs that already reached the legacy review-ready state have completed
    # collection and own an immutable result asset.  Older rows predate the
    # explicit ``technical_validation`` envelope, so keeping them in
    # ``checking`` forever would make the 4.0 migration hide every historical
    # review item.  Record the compatibility evidence instead of inventing an
    # editorial decision or a quality score.
    if job.result_asset_id and job.status in {"awaiting_review", "approved", "rejected", "syncing", "synced"}:
        return "passed", {
            "job_status": job.status,
            "technical_validation": technical,
            "manifest": manifest,
            "compatibility_gate": "legacy_review_ready_asset",
        }
    return "checking", {"job_status": job.status, "technical_validation": technical, "manifest": manifest}


async def _sync_candidate_from_job(candidate: MediaCandidate, job: MediaGenerationJob) -> None:
    now = now_bjt()
    technical_status, technical_json = _technical_state(job)
    candidate.result_asset_id = job.result_asset_id
    candidate.poster_asset_id = job.poster_asset_id
    review_manifest = _dict(_dict(job.result_json).get("review_manifest"))
    preview = _dict(review_manifest.get("preview")) or _dict(review_manifest.get("proxy"))
    if preview.get("asset_id"):
        candidate.preview_asset_id = _text(preview.get("asset_id"), 50) or candidate.preview_asset_id
    candidate.business_title = job.business_title or candidate.business_title
    candidate.technical_status = technical_status
    candidate.technical_json = technical_json
    if technical_status == "passed" and candidate.entered_selection_at is None:
        candidate.entered_selection_at = job.completed_at or now
    candidate.updated_at = now


def _derive_candidate_tags(
    request: MediaMaterialRequest | None,
    job: MediaGenerationJob | None,
    generation_method: str,
) -> list[str]:
    tags: list[str] = []
    method_label = {
        "direct": "原文直出",
        "ai_strategy": "AI策略",
        "replay": "视频复刻",
        "historical": "历史候选",
    }.get(generation_method)
    if method_label:
        tags.append(method_label)
    if request is not None:
        business = _dict(request.business_json)
        tags.extend(filter(None, [
            _text(business.get("product"), 80),
            _text(business.get("sku"), 80),
        ]))
        content = f"{request.visual_prompt}\n{request.script}".casefold()
        if request.script:
            speaker_turns = sum(content.count(marker) for marker in ("男：", "女：", "男:", "女:", "左女", "右女"))
            tags.append("双人对话" if speaker_turns >= 2 else "口播")
        elif any(word in content for word in ("b-roll", "b roll", "空镜", "氛围镜头")):
            tags.append("B-roll")
        if any(word in content for word in ("商品展示", "包装展示", "产品展示", "手持商品")):
            tags.append("商品展示")
        for scene in ("地铁", "厨房", "卧室", "客厅", "冰箱", "医院", "咖啡店", "街道", "直播间", "办公室"):
            if scene in content:
                tags.append(scene)
        tags.extend([request.ratio, f"{request.duration_seconds}秒", request.source])
    if job is not None:
        params = _dict(job.params_json)
        duration = params.get("delivery_duration_seconds") or params.get("duration_seconds")
        if duration and not any(tag.endswith("秒") for tag in tags):
            tags.append(f"{duration}秒")
        if job.production_batch_id:
            tags.append(f"批次:{job.production_batch_id}")
    return list(dict.fromkeys(_text(tag, 80) for tag in tags if _text(tag, 80)))[:50]


def _merge_candidate_tags(
    row: MediaCandidate,
    request: MediaMaterialRequest | None,
    job: MediaGenerationJob | None,
) -> None:
    derived = _derive_candidate_tags(request, job, row.generation_method)
    row.tags_json = list(dict.fromkeys([*(row.tags_json or []), *derived]))[:50]


async def _materialize_candidates(
    db: AsyncSession,
    request: MediaMaterialRequest | None,
    jobs: list[dict[str, Any]],
    *,
    generation_method: str,
) -> list[MediaCandidate]:
    result: list[MediaCandidate] = []
    for index, serialized in enumerate(jobs, start=1):
        job_id = _text(serialized.get("id"), 50)
        job = await db.get(MediaGenerationJob, job_id)
        if job is None:
            continue
        existing = (
            await db.execute(select(MediaCandidate).where(MediaCandidate.media_job_id == job.id))
        ).scalar_one_or_none()
        if existing is None:
            now = now_bjt()
            existing = MediaCandidate(
                id=_new_id("mca"),
                request_id=request.id if request else None,
                project_id=job.project_id,
                project_run_id=job.project_run_id,
                department_id=job.department_id or (request.department_id if request else "931765248"),
                media_job_id=job.id,
                generation_method=generation_method,
                direction_id=job.strategy_direction_id,
                variant_index=index,
                business_title=job.business_title or "生成候选",
                technical_status="checking",
                editorial_status="pending",
                delivery_status="not_requested",
                attribution_status="unlinked",
                created_at=now,
                updated_at=now,
            )
            db.add(existing)
        elif request and existing.request_id is None:
            existing.request_id = request.id
        await _link_request_by_title(db, existing, job)
        linked_request = request
        if linked_request is None and existing.request_id:
            linked_request = await db.get(MediaMaterialRequest, existing.request_id)
        _merge_candidate_tags(existing, linked_request, job)
        await _sync_candidate_from_job(existing, job)
        result.append(existing)
    await db.flush()
    return result


async def _link_request_by_title(db: AsyncSession, candidate: MediaCandidate, job: MediaGenerationJob) -> None:
    if candidate.request_id or not job.business_title:
        return
    row = (
        await db.execute(
            select(MediaMaterialRequest)
            .where(
                MediaMaterialRequest.project_id == job.project_id,
                MediaMaterialRequest.title == job.business_title,
            )
            .order_by(MediaMaterialRequest.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        candidate.request_id = row.id


async def _backfill_historical_candidates(db: AsyncSession, project: Project, run: ProjectRun) -> int:
    department_id = _department_id(run, project)
    rows = (
        await db.execute(
            select(MediaGenerationJob)
            .where(
                MediaGenerationJob.result_asset_id.is_not(None),
                MediaGenerationJob.status.in_(["awaiting_review", "approved", "rejected", "syncing", "synced"]),
                or_(
                    MediaGenerationJob.project_id == project.id,
                    MediaGenerationJob.department_id == department_id,
                ),
                ~exists(select(MediaCandidate.id).where(MediaCandidate.media_job_id == MediaGenerationJob.id)),
            )
            .order_by(MediaGenerationJob.created_at.desc())
            .limit(200)
        )
    ).scalars().all()
    if not rows:
        return 0
    serialized = [{"id": row.id} for row in rows]
    await _materialize_candidates(db, None, serialized, generation_method="historical")
    return len(rows)


async def _promote_ready_candidates(db: AsyncSession, project: Project, run: ProjectRun) -> int:
    """Create missing candidates and promote checking rows once the job is review-ready."""
    backfilled = await _backfill_historical_candidates(db, project, run)
    department_id = _department_id(run, project)
    rows = (
        await db.execute(
            select(MediaCandidate).where(
                MediaCandidate.project_id == project.id,
                MediaCandidate.department_id == department_id,
                MediaCandidate.technical_status != "passed",
            )
        )
    ).scalars().all()
    request_ids: set[str] = set()
    promoted = 0
    for candidate in rows:
        job = await db.get(MediaGenerationJob, candidate.media_job_id)
        if job is None:
            continue
        await _link_request_by_title(db, candidate, job)
        before = candidate.technical_status
        await _sync_candidate_from_job(candidate, job)
        if candidate.technical_status == "passed" and before != "passed":
            promoted += 1
        if candidate.request_id:
            request_ids.add(candidate.request_id)
    for request_id in request_ids:
        await _refresh_request_status(db, request_id)
    await db.flush()
    return backfilled + promoted


async def _candidate_generate(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    request = await _require_request(db, user, run, payload.get("request_id"), mutate=True)
    if request.status == "draft":
        raise AppError("MEDIA_MATERIAL_REQUEST_NOT_SUBMITTED", 409)
    mode = _text(payload.get("mode"), 30) or request.production_mode
    action = _text(payload.get("action"), 30) or (
        "submit" if mode == "direct" else ("analyze" if mode == "replay" else "start")
    )
    request.status = "producing"
    request.updated_at = now_bjt()

    business = request.business_json or {}
    participation = request.generation_participation_json or {}
    strategy_participation = {
        key: "ai_strategy" if participation.get(key) else "management_only"
        for key in ("product", "sku", "platform", "selling_points", "promotion")
    }
    common = {
        "visual_prompt": request.visual_prompt,
        "script": request.script,
        "source_roles": request.source_roles_json or [],
        "generation_options": request.generation_options_json or {"commercial_appearance": True},
        "business_info": business,
        "business_participation": strategy_participation,
        "duration_seconds": request.duration_seconds,
        "ratio": request.ratio,
        "quantity": request.quantity,
        "output_preset_id": request.output_preset_id,
        "business_title": request.title,
        "material_request_id": request.id,
        "_prompt_contract_version": MATERIAL_FDE_PROMPT_POLICY,
    }
    override = _dict(payload.get("generation"))

    from .workbench_v3 import dispatch_v3_capability

    if mode == "direct":
        result = await dispatch_v3_capability(db, user, project, run, "video.create.direct_submit", {**common, **override})
        candidates = await _materialize_candidates(db, request, _list(result.get("jobs")), generation_method="direct")
        return {**result, "request": _serialize_request(request), "candidates": [await _serialize_candidate(db, item) for item in candidates]}
    if mode == "ai_strategy":
        if not request.allow_ai_optimization:
            raise AppError("MEDIA_AI_STRATEGY_NOT_ALLOWED", 409)
        capability = {
            "start": "video.strategy.start",
            "answer": "video.strategy.answer",
            "compile": "video.strategy.compile",
            "submit": "video.strategy.submit",
        }.get(action)
        if not capability:
            raise AppError("MEDIA_STRATEGY_ACTION_INVALID", 422)
        strategy_payload = {**common, **override}
        if request.allow_script_changes is False:
            strategy_payload["accept_direction_c_script"] = False
        result = await dispatch_v3_capability(db, user, project, run, capability, strategy_payload)
        if action == "submit":
            candidates = await _materialize_candidates(db, request, _list(result.get("jobs")), generation_method="ai_strategy")
            result = {**result, "candidates": [await _serialize_candidate(db, item) for item in candidates]}
        return {**result, "request": _serialize_request(request)}
    if mode == "replay":
        capability = {
            "analyze": "video.replay.analyze",
            "update": "video.replay.update",
            "submit": "video.replay.submit",
            "get": "video.replay.get",
        }.get(action)
        if not capability:
            raise AppError("MEDIA_REPLAY_ACTION_INVALID", 422)
        replay_payload = {**common, **override}
        result = await dispatch_v3_capability(db, user, project, run, capability, replay_payload)
        if action == "submit":
            candidates = await _materialize_candidates(db, request, _list(result.get("jobs")), generation_method="replay")
            result = {**result, "candidates": [await _serialize_candidate(db, item) for item in candidates]}
        return {**result, "request": _serialize_request(request)}
    raise AppError("MEDIA_MATERIAL_REQUEST_INVALID", 422, {"field": "production_mode"})


async def _serialize_asset(asset: ProjectRunAsset | None) -> dict[str, Any] | None:
    if asset is None:
        return None
    from app.projects import service as project_service

    return {
        "asset_id": asset.id,
        "mime_type": asset.mime_type,
        "sha256": asset.sha256,
        "byte_size": asset.byte_size,
        "download_url": project_service._project_run_asset_signed_download_url(asset),  # noqa: SLF001
    }


async def _serialize_candidate(db: AsyncSession, row: MediaCandidate) -> dict[str, Any]:
    job = await db.get(MediaGenerationJob, row.media_job_id)
    if job is not None:
        await _sync_candidate_from_job(row, job)
    poster = await db.get(ProjectRunAsset, row.poster_asset_id) if row.poster_asset_id else None
    result_asset = await db.get(ProjectRunAsset, row.result_asset_id) if row.result_asset_id else None
    preview_asset = await db.get(ProjectRunAsset, row.preview_asset_id) if row.preview_asset_id else None
    request = await db.get(MediaMaterialRequest, row.request_id) if row.request_id else None
    _merge_candidate_tags(row, request, job)
    return {
        "id": row.id,
        "request_id": row.request_id,
        "media_job_id": row.media_job_id,
        "generation_method": row.generation_method,
        "direction_id": row.direction_id,
        "variant_index": row.variant_index,
        "business_title": row.business_title,
        "technical_status": row.technical_status,
        "technical": row.technical_json or {},
        "editorial_status": row.editorial_status,
        "delivery_status": row.delivery_status,
        "attribution_status": row.attribution_status,
        "cumulative_spend": float(row.cumulative_spend or 0),
        "performance": row.performance_json or {},
        "tags": row.tags_json or [],
        "entered_selection_at": isoformat_bjt(row.entered_selection_at),
        "selected_at": isoformat_bjt(row.selected_at),
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
        "poster": await _serialize_asset(poster),
        "preview_asset": await _serialize_asset(preview_asset),
        "result_asset": await _serialize_asset(result_asset),
        "job": {
            "status": job.status,
            "mode": job.mode,
            "duration_seconds": _dict(job.params_json).get("delivery_duration_seconds") or _dict(job.params_json).get("duration_seconds"),
            "width": _dict(job.params_json).get("width"),
            "height": _dict(job.params_json).get("height"),
            "assigned_instance_id": job.assigned_instance_id,
            "output_preset_id": job.output_preset_id,
            "prompt_preview_sha256": job.prompt_preview_sha256,
            "execution_prompt_sha256": job.execution_prompt_sha256,
        } if job else None,
        "request": {
            "title": request.title,
            "source": request.source,
            "deadline_at": isoformat_bjt(request.deadline_at),
            "business": request.business_json or {},
            "requested_by": request.requested_by,
            "assignee_id": request.assignee_id,
        } if request else None,
    }


async def _require_candidate(db: AsyncSession, run: ProjectRun, candidate_id: Any) -> MediaCandidate:
    row = await db.get(MediaCandidate, _text(candidate_id, 50))
    if row is None or row.project_id != run.project_id or row.department_id != _department_id(run):
        raise AppError("MEDIA_CANDIDATE_NOT_FOUND", 404)
    return row


async def _candidate_list(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    backfilled = await _backfill_historical_candidates(db, project, run)
    limit = _int(payload.get("limit"), default=30, minimum=1, maximum=100)
    conditions = [MediaCandidate.project_id == project.id, MediaCandidate.department_id == _department_id(run)]
    technical_status = _text(payload.get("technical_status"), 30) or "passed"
    if technical_status != "all":
        if technical_status not in TECHNICAL_STATUSES:
            raise AppError("MEDIA_CANDIDATE_FILTER_INVALID", 422, {"field": "technical_status"})
        conditions.append(MediaCandidate.technical_status == technical_status)
    for field, column, allowed in (
        ("editorial_status", MediaCandidate.editorial_status, {"pending", "selected", "held", "rejected"}),
        ("delivery_status", MediaCandidate.delivery_status, DELIVERY_STATUSES),
        ("attribution_status", MediaCandidate.attribution_status, ATTRIBUTION_STATUSES),
        ("generation_method", MediaCandidate.generation_method, {"direct", "ai_strategy", "replay", "historical"}),
    ):
        value = _text(payload.get(field), 40)
        if value and value != "all":
            if value not in allowed:
                raise AppError("MEDIA_CANDIDATE_FILTER_INVALID", 422, {"field": field})
            conditions.append(column == value)
    request_id = _text(payload.get("request_id"), 50)
    if request_id:
        conditions.append(MediaCandidate.request_id == request_id)
    entered_after_raw = _text(payload.get("entered_after"), 80)
    entered_after = parse_bjt_datetime(entered_after_raw) if entered_after_raw else None
    if entered_after:
        conditions.append(func.coalesce(MediaCandidate.entered_selection_at, MediaCandidate.created_at) >= entered_after)
    source = _text(payload.get("source"), 40)
    if source and source != "all":
        if source not in REQUEST_SOURCES:
            raise AppError("MEDIA_CANDIDATE_FILTER_INVALID", 422, {"field": "source"})
        conditions.append(exists(select(MediaMaterialRequest.id).where(
            MediaMaterialRequest.id == MediaCandidate.request_id,
            MediaMaterialRequest.source == source,
        )))
    assignee_id = _text(payload.get("assignee_id"), 50)
    if assignee_id:
        conditions.append(exists(select(MediaMaterialRequest.id).where(
            MediaMaterialRequest.id == MediaCandidate.request_id,
            MediaMaterialRequest.assignee_id == assignee_id,
        )))
    batch_id = _text(payload.get("batch_id"), 50)
    if batch_id:
        conditions.append(exists(select(MediaGenerationJob.id).where(
            MediaGenerationJob.id == MediaCandidate.media_job_id,
            MediaGenerationJob.production_batch_id == batch_id,
        )))
    product = _text(payload.get("product"), 120)
    if product:
        escaped_product = product.replace("%", r"\%").replace("_", r"\_")
        conditions.append(exists(select(MediaMaterialRequest.id).where(
            MediaMaterialRequest.id == MediaCandidate.request_id,
            sa.cast(MediaMaterialRequest.business_json, sa.Text).ilike(f"%{escaped_product}%", escape="\\"),
        )))
    query = _text(payload.get("query"), 120)
    if query:
        escaped = query.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        conditions.append(or_(
            MediaCandidate.business_title.ilike(pattern, escape="\\"),
            MediaCandidate.id.ilike(pattern, escape="\\"),
            sa.cast(MediaCandidate.tags_json, sa.Text).ilike(pattern, escape="\\"),
            exists(select(MediaMaterialRequest.id).where(
                MediaMaterialRequest.id == MediaCandidate.request_id,
                or_(
                    MediaMaterialRequest.title.ilike(pattern, escape="\\"),
                    MediaMaterialRequest.visual_prompt.ilike(pattern, escape="\\"),
                    MediaMaterialRequest.script.ilike(pattern, escape="\\"),
                    sa.cast(MediaMaterialRequest.business_json, sa.Text).ilike(pattern, escape="\\"),
                ),
            )),
        ))
    tags = list(dict.fromkeys(
        _text(item, 80) for item in _list(payload.get("tags")) if _text(item, 80)
    ))[:20]
    tag_match = _text(payload.get("tag_match"), 10) or "all"
    if tag_match not in {"all", "any"}:
        raise AppError("MEDIA_CANDIDATE_FILTER_INVALID", 422, {"field": "tag_match"})
    if tags:
        tag_conditions = [MediaCandidate.tags_json.contains([tag]) for tag in tags]
        conditions.append(and_(*tag_conditions) if tag_match == "all" else or_(*tag_conditions))
    sort = _text(payload.get("sort"), 40) or "selection_latest"
    sort_column = {
        "selection_latest": func.coalesce(MediaCandidate.entered_selection_at, MediaCandidate.created_at),
        "selected_latest": func.coalesce(MediaCandidate.selected_at, MediaCandidate.created_at),
        "spend": MediaCandidate.cumulative_spend,
        "quality": sa.cast(MediaCandidate.technical_json["quality"]["score"].astext, sa.Float),
        "created_latest": MediaCandidate.created_at,
    }.get(sort)
    if sort_column is None:
        raise AppError("MEDIA_CANDIDATE_SORT_INVALID", 422)
    numeric_sort = sort in {"spend", "quality"}
    if numeric_sort:
        cursor_value, cursor_id = _decode_value_cursor(payload.get("cursor"))
        effective_sort = func.coalesce(sort_column, -1.0)
        if cursor_value is not None and cursor_id:
            conditions.append(or_(
                effective_sort < cursor_value,
                and_(effective_sort == cursor_value, MediaCandidate.id < cursor_id),
            ))
    else:
        cursor_at, cursor_id = _decode_cursor(payload.get("cursor"))
        if cursor_at and cursor_id:
            conditions.append(or_(sort_column < cursor_at, and_(sort_column == cursor_at, MediaCandidate.id < cursor_id)))
    rows = (
        await db.execute(
            select(MediaCandidate).where(*conditions)
            .order_by(sort_column.desc().nullslast(), MediaCandidate.id.desc()).limit(limit + 1)
        )
    ).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [await _serialize_candidate(db, row) for row in rows]
    next_cursor = None
    if has_more and rows:
        if sort == "spend":
            next_cursor = _encode_value_cursor(float(rows[-1].cumulative_spend or 0), rows[-1].id)
        elif sort == "quality":
            quality = _dict(_dict(rows[-1].technical_json).get("quality"))
            next_cursor = _encode_value_cursor(float(quality.get("score") or -1), rows[-1].id)
        else:
            if sort == "selected_latest":
                at = rows[-1].selected_at or rows[-1].created_at
            elif sort == "created_latest":
                at = rows[-1].created_at
            else:
                at = rows[-1].entered_selection_at or rows[-1].created_at
            next_cursor = _encode_cursor(at, rows[-1].id)
    return {
        "items": items,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "sort": sort,
        "page_size": limit,
        "historical_backfilled": backfilled,
        "video_elements_required": False,
        "hover_preview_delay_ms": 300,
        "tag_match": tag_match,
        "tags": tags,
    }


async def _candidate_manifest(
    db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    candidate = await _require_candidate(db, run, payload.get("candidate_id"))
    from .workbench_v2 import review_manifest

    compare_job_id = payload.get("compare_media_job_id")
    compare_candidate_id = payload.get("compare_candidate_id")
    if compare_candidate_id and not compare_job_id:
        compare_candidate = await _require_candidate(db, run, compare_candidate_id)
        compare_job_id = compare_candidate.media_job_id
    manifest = await review_manifest(db, user, run, {
        "job_id": candidate.media_job_id,
        "compare_job_id": compare_job_id,
    })
    return {"candidate": await _serialize_candidate(db, candidate), "manifest": manifest}


async def _candidate_batch_tag(
    db: AsyncSession, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    candidate_ids = list(dict.fromkeys(
        _text(item, 50) for item in _list(payload.get("candidate_ids")) if _text(item, 50)
    ))[:100]
    tags = list(dict.fromkeys(
        _text(item, 40) for item in _list(payload.get("tags")) if _text(item, 40)
    ))[:20]
    if not candidate_ids:
        raise AppError("MEDIA_CANDIDATE_IDS_REQUIRED", 422)
    if not tags:
        raise AppError("MEDIA_CANDIDATE_TAGS_REQUIRED", 422)
    rows = (
        await db.execute(select(MediaCandidate).where(
            MediaCandidate.id.in_(candidate_ids),
            MediaCandidate.project_id == run.project_id,
            MediaCandidate.department_id == _department_id(run),
        ))
    ).scalars().all()
    if len(rows) != len(candidate_ids):
        raise AppError("MEDIA_CANDIDATE_NOT_FOUND", 404)
    now = now_bjt()
    for row in rows:
        row.tags_json = list(dict.fromkeys([*(row.tags_json or []), *tags]))[:50]
        row.updated_at = now
    await db.flush()
    return {"updated": len(rows), "candidate_ids": candidate_ids, "tags": tags}


async def _candidate_tag_update(
    db: AsyncSession, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    row = await _require_candidate(db, run, payload.get("candidate_id"))
    operation = _text(payload.get("operation"), 20) or "replace"
    tags = list(dict.fromkeys(
        _text(item, 80) for item in _list(payload.get("tags")) if _text(item, 80)
    ))[:50]
    current = list(dict.fromkeys(_text(item, 80) for item in (row.tags_json or []) if _text(item, 80)))
    if operation == "add":
        updated = list(dict.fromkeys([*current, *tags]))[:50]
    elif operation == "remove":
        removed = set(tags)
        updated = [tag for tag in current if tag not in removed]
    elif operation == "replace":
        updated = tags
    else:
        raise AppError("MEDIA_CANDIDATE_TAG_OPERATION_INVALID", 422)
    row.tags_json = updated
    row.updated_at = now_bjt()
    await db.flush()
    return {"candidate_id": row.id, "tags": updated, "operation": operation}


async def _review_session(
    db: AsyncSession, user: User, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    candidate = await _require_candidate(db, run, payload.get("candidate_id"))
    base = [
        MediaCandidate.project_id == run.project_id,
        MediaCandidate.department_id == _department_id(run),
        MediaCandidate.technical_status == "passed",
    ]
    order_time = func.coalesce(MediaCandidate.entered_selection_at, MediaCandidate.created_at)
    current_time = candidate.entered_selection_at or candidate.created_at
    previous = (
        await db.execute(
            select(MediaCandidate).where(*base).where(
                or_(order_time > current_time, and_(order_time == current_time, MediaCandidate.id > candidate.id))
            ).order_by(order_time.asc(), MediaCandidate.id.asc()).limit(1)
        )
    ).scalar_one_or_none()
    following = (
        await db.execute(
            select(MediaCandidate).where(*base).where(
                or_(order_time < current_time, and_(order_time == current_time, MediaCandidate.id < candidate.id))
            ).order_by(order_time.desc(), MediaCandidate.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    total = int((await db.execute(select(func.count(MediaCandidate.id)).where(*base))).scalar() or 0)
    newer = int((await db.execute(select(func.count(MediaCandidate.id)).where(*base).where(order_time > current_time))).scalar() or 0)
    manifest = await _candidate_manifest(db, user, run, {
        "candidate_id": candidate.id,
        "compare_candidate_id": payload.get("compare_candidate_id"),
    })
    return {
        **manifest,
        "previous_candidate_id": previous.id if previous else None,
        "next_candidate_id": following.id if following else None,
        "position": newer + 1,
        "total": total,
    }


async def _refresh_request_status(db: AsyncSession, request_id: str | None) -> None:
    if not request_id:
        return
    request = await db.get(MediaMaterialRequest, request_id)
    if request is None:
        return
    candidates = (
        await db.execute(select(MediaCandidate).where(MediaCandidate.request_id == request_id))
    ).scalars().all()
    if not candidates:
        request.status = "producing"
    elif any(item.attribution_status == "spending" for item in candidates):
        request.status = "spending"
    elif any(item.delivery_status == "delivered" for item in candidates):
        request.status = "delivered"
    elif all(item.editorial_status == "selected" for item in candidates):
        request.status = "selected"
    elif any(item.editorial_status == "selected" for item in candidates):
        request.status = "partially_selected"
    elif all(item.editorial_status == "rejected" for item in candidates):
        request.status = "rejected"
    elif any(item.technical_status == "passed" for item in candidates):
        request.status = "awaiting_selection"
    else:
        request.status = "producing"
    request.updated_at = now_bjt()


async def _decision_submit(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    if not _can_make_editorial_decision(user):
        raise AppError("MEDIA_EDITORIAL_DECISION_FORBIDDEN", 403)
    candidate = await _require_candidate(db, run, payload.get("candidate_id"))
    job = await db.get(MediaGenerationJob, candidate.media_job_id)
    if job:
        await _sync_candidate_from_job(candidate, job)
    if candidate.technical_status != "passed":
        raise AppError("MEDIA_CANDIDATE_TECHNICAL_GATE", 409, {"technical_status": candidate.technical_status})
    decision = _text(payload.get("decision"), 20)
    if decision not in EDITORIAL_DECISIONS:
        raise AppError("MEDIA_EDITORIAL_DECISION_INVALID", 422)
    problem_types = list(dict.fromkeys(_text(item, 60) for item in _list(payload.get("problem_types")) if _text(item, 60)))[:20]
    annotation_ids = list(dict.fromkeys(_text(item, 50) for item in _list(payload.get("annotation_ids")) if _text(item, 50)))[:100]
    if decision == "rejected" and not problem_types and not annotation_ids:
        annotation_count = int((
            await db.execute(
                select(func.count(MediaReviewAnnotation.id)).where(
                    MediaReviewAnnotation.media_job_id == candidate.media_job_id,
                    MediaReviewAnnotation.status == "open",
                )
            )
        ).scalar() or 0)
        if annotation_count == 0:
            raise AppError("MEDIA_EDITORIAL_REJECTION_REASON_REQUIRED", 422)
    now = now_bjt()
    row = (
        await db.execute(
            select(MediaEditorialDecision).where(
                MediaEditorialDecision.candidate_id == candidate.id,
                MediaEditorialDecision.decided_by == str(user.id),
            )
        )
    ).scalar_one_or_none()
    previous = None
    if row is None:
        row = MediaEditorialDecision(
            id=_new_id("med"), candidate_id=candidate.id, project_id=project.id,
            department_id=candidate.department_id, decided_by=str(user.id), decision=decision,
            created_at=now, updated_at=now, decided_at=now,
        )
        db.add(row)
    else:
        previous = {
            "decision": row.decision,
            "usage_direction": row.usage_direction,
            "edit_notes": row.edit_notes,
            "problem_types": row.problem_types_json or [],
            "annotation_ids": row.annotation_ids_json or [],
            "decided_at": isoformat_bjt(row.decided_at),
        }
        row.history_json = [*(row.history_json or []), previous][-100:]
    row.decision = decision
    row.usage_direction = _text(payload.get("usage_direction"), 240) or None
    row.edit_notes = _text(payload.get("edit_notes"), 4000) or None
    row.problem_types_json = problem_types
    row.annotation_ids_json = annotation_ids
    row.decided_at = now
    row.updated_at = now
    candidate.editorial_status = decision
    candidate.selected_at = now if decision == "selected" else None
    candidate.updated_at = now
    await _refresh_request_status(db, candidate.request_id)
    await db.flush()
    return {
        "candidate": await _serialize_candidate(db, candidate),
        "decision": {
            "id": row.id,
            "decision": row.decision,
            "decided_by": row.decided_by,
            "decided_at": isoformat_bjt(row.decided_at),
            "usage_direction": row.usage_direction,
            "edit_notes": row.edit_notes,
            "problem_types": row.problem_types_json or [],
            "annotation_ids": row.annotation_ids_json or [],
            "history": row.history_json or [],
        },
        "previous": previous,
        "delivery_allowed": decision == "selected",
    }


async def _batch_decision(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any], decision: str
) -> dict[str, Any]:
    candidate_ids = list(dict.fromkeys(_text(item, 50) for item in _list(payload.get("candidate_ids")) if _text(item, 50)))
    if not candidate_ids or len(candidate_ids) > 100:
        raise AppError("MEDIA_EDITORIAL_BATCH_INVALID", 422)
    if decision == "selected":
        raise AppError("MEDIA_EDITORIAL_BATCH_SELECT_FORBIDDEN", 403)
    results = []
    for candidate_id in candidate_ids:
        item = await _decision_submit(db, user, project, run, {
            **payload,
            "candidate_id": candidate_id,
            "decision": decision,
        })
        results.append(item["candidate"])
    return {"items": results, "updated_count": len(results), "batch_select_supported": False}


def _serialize_delivery(row: MediaDelivery) -> dict[str, Any]:
    return {
        "id": row.id,
        "candidate_id": row.candidate_id,
        "team": row.team,
        "category": row.category,
        "status": row.status,
        "remote_video_id": row.remote_video_id,
        "ad_asset_id": row.ad_asset_id,
        "attempt_count": row.attempt_count,
        "last_error": row.last_error,
        "delivered_at": isoformat_bjt(row.delivered_at),
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


def _delivery_status_from_outbox(row: CloudVideoSyncOutbox) -> str:
    """Map the connector outbox without claiming delivery before an ID exists."""

    status = _text(row.status, 40).lower()
    if row.remote_video_id and status in {"completed", "delivered", "success", "succeeded", "synced"}:
        return "delivered"
    if status in {"processing", "running", "syncing"}:
        return "syncing"
    if status in {"failed", "dead", "cancelled"}:
        return "failed"
    # pending and blocked_connector_pending both remain honest pending states.
    return "pending"


async def reconcile_material_deliveries(
    db: AsyncSession,
    *,
    project_id: str | None = None,
    candidate_id: str | None = None,
) -> int:
    """Copy durable connector facts into the FDE delivery state machine.

    This deliberately trusts only the outbox primary key and its returned remote
    video ID.  It never performs a title/name match and never invents an upload.
    """

    conditions = [MediaDelivery.cloud_outbox_id.is_not(None)]
    if project_id:
        conditions.append(MediaDelivery.project_id == project_id)
    if candidate_id:
        conditions.append(MediaDelivery.candidate_id == candidate_id)
    rows = (
        await db.execute(select(MediaDelivery).where(*conditions))
    ).scalars().all()
    if not rows:
        return 0
    outbox_ids = [row.cloud_outbox_id for row in rows if row.cloud_outbox_id]
    outboxes = (
        await db.execute(select(CloudVideoSyncOutbox).where(CloudVideoSyncOutbox.id.in_(outbox_ids)))
    ).scalars().all()
    by_id = {row.id: row for row in outboxes}
    changed = 0
    now = now_bjt()
    for delivery in rows:
        outbox = by_id.get(delivery.cloud_outbox_id)
        if outbox is None:
            continue
        mapped_status = _delivery_status_from_outbox(outbox)
        remote_video_id = _text(outbox.remote_video_id, 180) or None
        attempt_count = int(outbox.attempt_count or 0)
        last_error = _text(outbox.last_error, 2000) or None
        before = (
            delivery.status,
            delivery.remote_video_id,
            delivery.attempt_count,
            delivery.last_error,
            delivery.delivered_at,
        )
        delivery.status = mapped_status
        delivery.remote_video_id = remote_video_id
        delivery.attempt_count = attempt_count
        delivery.last_error = last_error
        if mapped_status == "delivered" and remote_video_id:
            delivery.delivered_at = outbox.completed_at or delivery.delivered_at or now
        delivery.updated_at = now
        candidate = await db.get(MediaCandidate, delivery.candidate_id)
        if candidate is not None:
            candidate.delivery_status = mapped_status
            if mapped_status == "delivered":
                candidate.attribution_status = "linked" if delivery.ad_asset_id else "linking"
            elif mapped_status == "failed" and candidate.attribution_status != "spending":
                candidate.attribution_status = "unlinked"
            candidate.updated_at = now
            await _refresh_request_status(db, candidate.request_id)
        after = (
            delivery.status,
            delivery.remote_video_id,
            delivery.attempt_count,
            delivery.last_error,
            delivery.delivered_at,
        )
        changed += int(before != after)
    if changed:
        await db.flush()
    return changed


async def _delivery_submit(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    candidate = await _require_candidate(db, run, payload.get("candidate_id"))
    if candidate.editorial_status != "selected":
        raise AppError("MEDIA_DELIVERY_SELECTION_REQUIRED", 409)
    job = await db.get(MediaGenerationJob, candidate.media_job_id)
    if job is None or not job.result_sha256 or not job.result_asset_id:
        raise AppError("MEDIA_RESULT_REQUIRED", 409)
    team = _text(payload.get("team"), 120) or "示例品牌内容电商运营部"
    category = _text(payload.get("category"), 180) or "示例品牌内容电商运营部/AI生成素材"
    key = hashlib.sha256(f"{job.result_sha256}:{team}:{category}".encode("utf-8")).hexdigest()
    existing = (
        await db.execute(select(MediaDelivery).where(MediaDelivery.idempotency_key == key))
    ).scalar_one_or_none()
    if existing:
        return {"delivery": _serialize_delivery(existing), "deduped": True}
    from .service import _ensure_cloud_sync_outboxes

    outboxes = await _ensure_cloud_sync_outboxes(db, job, category=category)
    outbox = outboxes[0] if outboxes else None
    now = now_bjt()
    row = MediaDelivery(
        id=_new_id("mdl"), candidate_id=candidate.id, project_id=project.id,
        department_id=candidate.department_id, requested_by=str(user.id),
        video_sha256=job.result_sha256, team=team, category=category,
        status="pending", idempotency_key=key,
        cloud_outbox_id=outbox.id if outbox else None,
        payload_json={"result_asset_id": job.result_asset_id, "media_job_id": job.id},
        created_at=now, updated_at=now,
    )
    db.add(row)
    candidate.delivery_status = "pending"
    candidate.updated_at = now
    await _refresh_request_status(db, candidate.request_id)
    await db.flush()
    return {
        "delivery": _serialize_delivery(row),
        "deduped": False,
        "connector_status": "pending" if outbox else "connector_unavailable",
        "business_effect_counted": False,
    }


async def _delivery_get(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    candidate = await _require_candidate(db, run, payload.get("candidate_id"))
    await reconcile_material_deliveries(db, project_id=run.project_id, candidate_id=candidate.id)
    rows = (
        await db.execute(
            select(MediaDelivery).where(MediaDelivery.candidate_id == candidate.id)
            .order_by(MediaDelivery.created_at.desc())
        )
    ).scalars().all()
    return {"items": [_serialize_delivery(row) for row in rows]}


def _decimal_metric(value: Any, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value if value not in (None, "") else 0))
    except Exception as exc:  # noqa: BLE001
        raise AppError("MEDIA_PERFORMANCE_ROW_INVALID", 422, {"field": field}) from exc
    if parsed < 0:
        raise AppError("MEDIA_PERFORMANCE_ROW_INVALID", 422, {"field": field})
    return parsed


def normalize_material_performance_fact(row: dict[str, Any], *, source_version: str) -> dict[str, Any]:
    """Normalize one governed connector fact; both exact join keys are required."""

    remote_video_id = _text(row.get("remote_video_id") or row.get("video_id"), 180)
    ad_asset_id = _text(row.get("ad_asset_id") or row.get("material_id"), 180)
    date_text = _text(row.get("date") or row.get("metric_date"), 10)
    if not remote_video_id or not ad_asset_id:
        raise AppError(
            "MEDIA_PERFORMANCE_EXACT_LINK_REQUIRED",
            422,
            {"remote_video_id": bool(remote_video_id), "ad_asset_id": bool(ad_asset_id)},
        )
    try:
        metric_date = date.fromisoformat(date_text)
    except ValueError as exc:
        raise AppError("MEDIA_PERFORMANCE_ROW_INVALID", 422, {"field": "date"}) from exc
    metrics = _dict(row.get("metrics"))
    spend = _decimal_metric(row.get("spend", metrics.get("statCost")), field="spend")
    gmv = _decimal_metric(
        row.get("gmv", metrics.get("payOrderAmountAndPayOrderCouponAmount", metrics.get("payOrderAmount"))),
        field="gmv",
    )
    conversions = int(_decimal_metric(row.get("conversions", metrics.get("convertCnt")), field="conversions"))
    impressions = int(_decimal_metric(row.get("impressions", metrics.get("showCnt")), field="impressions"))
    clicks = int(_decimal_metric(row.get("clicks", metrics.get("clickCnt")), field="clicks"))
    return {
        "remote_video_id": remote_video_id,
        "ad_asset_id": ad_asset_id,
        "metric_date": metric_date,
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "gmv": gmv,
        "roi": (gmv / spend) if spend > 0 else None,
        "source_version": _text(source_version, 120) or "unknown",
        "raw_json": row,
    }


async def ingest_material_performance_rows(
    db: AsyncSession,
    *,
    rows: list[dict[str, Any]],
    source_version: str,
) -> dict[str, Any]:
    """Ingest exact cloud-video/ad-material facts from a controlled connector.

    This is intentionally an internal service entrypoint rather than a browser
    project capability.  A row is accepted only when its remote video ID maps to
    one delivery and its ad material ID is either already bound to that delivery
    or is being bound for the first time by the same exact connector row.
    """

    accepted = 0
    deduped = 0
    rejected: list[dict[str, Any]] = []
    touched_candidates: set[str] = set()
    for index, raw in enumerate(rows):
        try:
            fact = normalize_material_performance_fact(_dict(raw), source_version=source_version)
            deliveries = (
                await db.execute(select(MediaDelivery).where(
                    MediaDelivery.remote_video_id == fact["remote_video_id"],
                    MediaDelivery.status == "delivered",
                ).limit(2))
            ).scalars().all()
            if len(deliveries) != 1:
                raise AppError(
                    "MEDIA_PERFORMANCE_EXACT_LINK_NOT_FOUND",
                    409,
                    {"remote_video_id": fact["remote_video_id"], "matches": len(deliveries)},
                )
            delivery = deliveries[0]
            if delivery.ad_asset_id and delivery.ad_asset_id != fact["ad_asset_id"]:
                raise AppError("MEDIA_PERFORMANCE_AD_ASSET_CONFLICT", 409)
            delivery.ad_asset_id = fact["ad_asset_id"]
            delivery.updated_at = now_bjt()
            existing = (
                await db.execute(select(MediaPerformanceDaily).where(
                    MediaPerformanceDaily.ad_asset_id == fact["ad_asset_id"],
                    MediaPerformanceDaily.metric_date == fact["metric_date"],
                ))
            ).scalar_one_or_none()
            if existing is None:
                existing = MediaPerformanceDaily(
                    id=_new_id("mpd"), candidate_id=delivery.candidate_id,
                    delivery_id=delivery.id, department_id=delivery.department_id,
                    created_at=now_bjt(), updated_at=now_bjt(), **fact,
                )
                db.add(existing)
                accepted += 1
            else:
                if existing.candidate_id != delivery.candidate_id or existing.remote_video_id != fact["remote_video_id"]:
                    raise AppError("MEDIA_PERFORMANCE_IDEMPOTENCY_CONFLICT", 409)
                changed = any(getattr(existing, key) != fact[key] for key in (
                    "spend", "impressions", "clicks", "conversions", "gmv", "roi", "source_version"
                ))
                for key, value in fact.items():
                    setattr(existing, key, value)
                existing.delivery_id = delivery.id
                existing.updated_at = now_bjt()
                accepted += int(changed)
                deduped += int(not changed)
            touched_candidates.add(delivery.candidate_id)
        except AppError as exc:
            rejected.append({"index": index, "code": exc.code, "details": exc.detail or {}})
    await db.flush()
    for candidate_id in touched_candidates:
        totals = (
            await db.execute(select(
                func.coalesce(func.sum(MediaPerformanceDaily.spend), 0),
                func.coalesce(func.sum(MediaPerformanceDaily.impressions), 0),
                func.coalesce(func.sum(MediaPerformanceDaily.clicks), 0),
                func.coalesce(func.sum(MediaPerformanceDaily.conversions), 0),
                func.coalesce(func.sum(MediaPerformanceDaily.gmv), 0),
                func.max(MediaPerformanceDaily.metric_date),
            ).where(MediaPerformanceDaily.candidate_id == candidate_id))
        ).one()
        spend, impressions, clicks, conversions, gmv, latest_date = totals
        candidate = await db.get(MediaCandidate, candidate_id)
        if candidate is None:
            continue
        spend_value = Decimal(spend or 0)
        gmv_value = Decimal(gmv or 0)
        candidate.cumulative_spend = spend_value
        candidate.attribution_status = "spending" if spend_value > 0 else "no_spend"
        candidate.performance_json = {
            "spend": float(spend_value),
            "impressions": int(impressions or 0),
            "clicks": int(clicks or 0),
            "conversions": int(conversions or 0),
            "gmv": float(gmv_value),
            "roi": float(gmv_value / spend_value) if spend_value > 0 else None,
            "latest_metric_date": latest_date.isoformat() if latest_date else None,
            "prediction_used": False,
        }
        candidate.updated_at = now_bjt()
        await _refresh_request_status(db, candidate.request_id)
    await db.flush()
    return {"accepted": accepted, "deduped": deduped, "rejected": rejected}


def _performance_item(row: MediaPerformanceDaily) -> dict[str, Any]:
    return {
        "id": row.id,
        "candidate_id": row.candidate_id,
        "delivery_id": row.delivery_id,
        "remote_video_id": row.remote_video_id,
        "ad_asset_id": row.ad_asset_id,
        "date": row.metric_date.isoformat(),
        "spend": float(row.spend or 0),
        "impressions": int(row.impressions or 0),
        "clicks": int(row.clicks or 0),
        "conversions": int(row.conversions or 0),
        "gmv": float(row.gmv or 0),
        "roi": float(row.roi) if row.roi is not None else None,
        "source_version": row.source_version,
    }


def _date_range(payload: dict[str, Any], *, default_days: int = 7) -> tuple[date, date]:
    today = now_bjt().date()
    try:
        start = date.fromisoformat(_text(payload.get("date_from"), 10)) if payload.get("date_from") else today - timedelta(days=default_days - 1)
        end = date.fromisoformat(_text(payload.get("date_to"), 10)) if payload.get("date_to") else today
    except ValueError as exc:
        raise AppError("MEDIA_PERFORMANCE_DATE_INVALID", 422) from exc
    if start > end or (end - start).days > 366:
        raise AppError("MEDIA_PERFORMANCE_DATE_INVALID", 422)
    return start, end


async def _performance_summary(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    start, end = _date_range(payload)
    conditions = [
        MediaPerformanceDaily.department_id == _department_id(run),
        MediaPerformanceDaily.metric_date.between(start, end),
    ]
    values = (
        await db.execute(
            select(
                func.coalesce(func.sum(MediaPerformanceDaily.spend), 0),
                func.coalesce(func.sum(MediaPerformanceDaily.impressions), 0),
                func.coalesce(func.sum(MediaPerformanceDaily.clicks), 0),
                func.coalesce(func.sum(MediaPerformanceDaily.conversions), 0),
                func.coalesce(func.sum(MediaPerformanceDaily.gmv), 0),
                func.count(func.distinct(MediaPerformanceDaily.candidate_id)),
            ).where(*conditions)
        )
    ).one()
    spend, impressions, clicks, conversions, gmv, candidates = values
    spend_decimal = Decimal(spend or 0)
    gmv_decimal = Decimal(gmv or 0)
    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "spend": float(spend_decimal),
        "impressions": int(impressions or 0),
        "clicks": int(clicks or 0),
        "conversions": int(conversions or 0),
        "gmv": float(gmv_decimal),
        "roi": float(gmv_decimal / spend_decimal) if spend_decimal > 0 else None,
        "attributed_candidates": int(candidates or 0),
        "prediction_used": False,
    }


async def _performance_list(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    start, end = _date_range(payload, default_days=30)
    limit = _int(payload.get("limit"), default=30, minimum=1, maximum=100)
    conditions = [
        MediaPerformanceDaily.department_id == _department_id(run),
        MediaPerformanceDaily.metric_date.between(start, end),
    ]
    candidate_id = _text(payload.get("candidate_id"), 50)
    if candidate_id:
        conditions.append(MediaPerformanceDaily.candidate_id == candidate_id)
    rows = (
        await db.execute(
            select(MediaPerformanceDaily).where(*conditions)
            .order_by(MediaPerformanceDaily.metric_date.desc(), MediaPerformanceDaily.id.desc()).limit(limit)
        )
    ).scalars().all()
    return {"items": [_performance_item(row) for row in rows], "date_from": start.isoformat(), "date_to": end.isoformat()}


async def _weekly_report(db: AsyncSession, run: ProjectRun, payload: dict[str, Any]) -> dict[str, Any]:
    today = now_bjt().date()
    week_start = today - timedelta(days=today.weekday())
    previous_start = week_start - timedelta(days=7)
    department_id = _department_id(run)

    async def period(start: date, end: date) -> dict[str, Any]:
        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())
        request_count = int((await db.execute(select(func.count(MediaMaterialRequest.id)).where(
            MediaMaterialRequest.department_id == department_id,
            MediaMaterialRequest.created_at >= start_dt,
            MediaMaterialRequest.created_at < end_dt,
        ))).scalar() or 0)
        candidate_count = int((await db.execute(select(func.count(MediaCandidate.id)).where(
            MediaCandidate.department_id == department_id,
            MediaCandidate.created_at >= start_dt,
            MediaCandidate.created_at < end_dt,
        ))).scalar() or 0)
        selected_count = int((await db.execute(select(func.count(func.distinct(MediaCandidate.id))).where(
            MediaCandidate.department_id == department_id,
            MediaCandidate.editorial_status == "selected",
            MediaCandidate.selected_at >= start_dt,
            MediaCandidate.selected_at < end_dt,
        ))).scalar() or 0)
        attributed_count = int((await db.execute(select(func.count(func.distinct(MediaPerformanceDaily.candidate_id))).where(
            MediaPerformanceDaily.department_id == department_id,
            MediaPerformanceDaily.metric_date.between(start, end),
        ))).scalar() or 0)
        spend = Decimal((await db.execute(select(func.coalesce(func.sum(MediaPerformanceDaily.spend), 0)).where(
            MediaPerformanceDaily.department_id == department_id,
            MediaPerformanceDaily.metric_date.between(start, end),
        ))).scalar() or 0)
        return {
            "new_requests": request_count,
            "generated_candidates": candidate_count,
            "selected_candidates": selected_count,
            "selection_rate": round(selected_count / candidate_count, 4) if candidate_count else 0,
            "attributed_candidates": attributed_count,
            "real_spend": float(spend),
        }

    current = await period(week_start, today)
    previous = await period(previous_start, week_start - timedelta(days=1))
    rows = []
    labels = {
        "new_requests": "新增真实需求",
        "generated_candidates": "生成候选",
        "selected_candidates": "编导选用素材",
        "selection_rate": "选用率",
        "attributed_candidates": "已归因素材",
        "real_spend": "真实消耗",
    }
    for key, label in labels.items():
        now_value = current[key]
        before = previous[key]
        change = None if before == 0 else round((now_value - before) / before, 4)
        rows.append({"key": key, "label": label, "current": now_value, "previous": before, "change": change})
    return {
        "week_start": week_start.isoformat(),
        "week_end": today.isoformat(),
        "north_star": {
            "selected_candidates": current["selected_candidates"],
            "real_spend": current["real_spend"],
        },
        "rows": rows,
        "attribution_rule": "candidate -> editorial selected -> remote video id -> ad asset id -> actual spend",
        "prediction_used": False,
    }


async def _review_preference_save(
    db: AsyncSession, user: User, project: Project, payload: dict[str, Any]
) -> dict[str, Any]:
    allowed = {"sort", "filters", "density", "sidebar_collapsed", "history_assistance"}
    preference = {key: value for key, value in _dict(payload.get("preference")).items() if key in allowed}
    row = (
        await db.execute(select(MediaReviewPreference).where(
            MediaReviewPreference.project_id == project.id,
            MediaReviewPreference.user_id == str(user.id),
        ))
    ).scalar_one_or_none()
    now = now_bjt()
    if row is None:
        row = MediaReviewPreference(
            id=_new_id("mrp"), project_id=project.id, user_id=str(user.id),
            preference_json=preference, created_at=now, updated_at=now,
        )
        db.add(row)
    else:
        row.preference_json = {**(row.preference_json or {}), **preference}
        row.updated_at = now
    await db.flush()
    return {"preference": row.preference_json or {}, "updated_at": isoformat_bjt(row.updated_at)}


async def fde_workbench_snapshot(
    db: AsyncSession, user: User, project: Project, run: ProjectRun, payload: dict[str, Any]
) -> dict[str, Any]:
    from .workbench_v2 import _record_workbench_snapshot, workbench_snapshot

    await reconcile_material_deliveries(db, project_id=project.id)
    await _promote_ready_candidates(db, project, run)

    base = await workbench_snapshot(
        db, project, run,
        {
            "limit": min(_int(payload.get("limit"), default=20, minimum=1, maximum=60), 60),
            "include_recent_jobs": bool(payload.get("include_recent_jobs")),
            "_skip_record": True,
        },
    )
    department_id = _department_id(run, project)
    request_counts = dict((
        await db.execute(select(MediaMaterialRequest.status, func.count(MediaMaterialRequest.id)).where(
            MediaMaterialRequest.project_id == project.id,
            MediaMaterialRequest.department_id == department_id,
        ).group_by(MediaMaterialRequest.status))
    ).all())
    candidate_rows = (
        await db.execute(select(
            MediaCandidate.technical_status,
            MediaCandidate.editorial_status,
            MediaCandidate.delivery_status,
            MediaCandidate.attribution_status,
            func.count(MediaCandidate.id),
        ).where(
            MediaCandidate.project_id == project.id,
            MediaCandidate.department_id == department_id,
        ).group_by(
            MediaCandidate.technical_status, MediaCandidate.editorial_status,
            MediaCandidate.delivery_status, MediaCandidate.attribution_status,
        ))
    ).all()
    candidate_counts = {
        "total": sum(int(row[4]) for row in candidate_rows),
        "technical": {}, "editorial": {}, "delivery": {}, "attribution": {},
    }
    for technical, editorial, delivery, attribution, count in candidate_rows:
        count = int(count)
        candidate_counts["technical"][technical] = candidate_counts["technical"].get(technical, 0) + count
        candidate_counts["editorial"][editorial] = candidate_counts["editorial"].get(editorial, 0) + count
        candidate_counts["delivery"][delivery] = candidate_counts["delivery"].get(delivery, 0) + count
        candidate_counts["attribution"][attribution] = candidate_counts["attribution"].get(attribution, 0) + count
    weekly = await _weekly_report(db, run, payload)
    preference = (
        await db.execute(select(MediaReviewPreference).where(
            MediaReviewPreference.project_id == project.id,
            MediaReviewPreference.user_id == str(user.id),
        ))
    ).scalar_one_or_none()
    snapshot = {
        key: value for key, value in base.items() if key not in {"cursor", "state_fingerprint", "server_time"}
    }
    active_job_statuses = ("queued", "assigned", "running", "collecting")
    job_active = int((await db.execute(select(func.count(MediaGenerationJob.id)).where(
        MediaGenerationJob.project_id == project.id,
        MediaGenerationJob.department_id == department_id,
        MediaGenerationJob.status.in_(active_job_statuses),
    ))).scalar() or 0)
    snapshot.update({
        "workbench_version": MATERIAL_FDE_VERSION,
        "prompt_policy_version": MATERIAL_FDE_PROMPT_POLICY,
        "request_counts": request_counts,
        "job_counts": {"active": job_active},
        "candidate_counts": candidate_counts,
        "north_star": weekly["north_star"],
        "review_preference": preference.preference_json if preference else {},
        "server_time": isoformat_bjt(now_bjt()),
    })
    fingerprint_source = {key: value for key, value in snapshot.items() if key != "server_time"}
    persisted = await _record_workbench_snapshot(db, project, run, fingerprint_source)
    return {**persisted, "server_time": snapshot["server_time"]}


async def dispatch_fde_capability(
    db: AsyncSession,
    user: User,
    project: Project,
    run: ProjectRun,
    capability: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if capability == "material.workbench.snapshot":
        return await fde_workbench_snapshot(db, user, project, run, payload)
    if capability == "material.request.create":
        return await _create_request(db, user, project, run, payload)
    if capability == "material.request.update":
        return await _update_request(db, user, run, payload)
    if capability == "material.request.submit":
        return await _submit_request(db, user, run, payload)
    if capability == "material.request.list":
        return await _list_requests(db, user, run, payload)
    if capability == "material.request.get":
        row = await _require_request(db, user, run, payload.get("request_id"))
        return {"request": _serialize_request(row)}
    if capability == "material.asset.recommend":
        from .product_assets import recommend_product_assets

        return await recommend_product_assets(db, run, payload)
    if capability == "material.candidate.generate":
        return await _candidate_generate(db, user, project, run, payload)
    if capability == "material.candidate.list":
        return await _candidate_list(db, user, project, run, payload)
    if capability == "material.candidate.get":
        row = await _require_candidate(db, run, payload.get("candidate_id"))
        return {"candidate": await _serialize_candidate(db, row)}
    if capability == "material.candidate.manifest":
        return await _candidate_manifest(db, user, run, payload)
    if capability == "material.candidate.batch_tag":
        return await _candidate_batch_tag(db, run, payload)
    if capability == "material.candidate.tag.update":
        return await _candidate_tag_update(db, run, payload)
    if capability == "material.review.session":
        return await _review_session(db, user, run, payload)
    if capability == "material.review.annotation.save":
        candidate = await _require_candidate(db, run, payload.get("candidate_id"))
        from .workbench_v2 import save_annotation

        return await save_annotation(db, user, run, {**payload, "job_id": candidate.media_job_id})
    if capability == "material.decision.submit":
        return await _decision_submit(db, user, project, run, payload)
    if capability == "material.decision.batch_reject":
        return await _batch_decision(db, user, project, run, payload, "rejected")
    if capability == "material.decision.batch_hold":
        return await _batch_decision(db, user, project, run, payload, "held")
    if capability == "material.delivery.submit":
        return await _delivery_submit(db, user, project, run, payload)
    if capability == "material.delivery.get":
        return await _delivery_get(db, run, payload)
    if capability == "material.performance.summary":
        return await _performance_summary(db, run, payload)
    if capability == "material.performance.list":
        return await _performance_list(db, run, payload)
    if capability == "material.weekly_report.get":
        return await _weekly_report(db, run, payload)
    if capability == "material.review.preference.save":
        return await _review_preference_save(db, user, project, payload)
    raise AppError("PROJECT_CAPABILITY_DENIED", 400, {"capability": capability})
