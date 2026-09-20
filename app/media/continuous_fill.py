"""Overnight fill from overnight_recipes.json via official material.* APIs."""
from __future__ import annotations
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any
from uuid import uuid4
import sqlalchemy as sa
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.models import User
from app.config import settings
from app.common.time_utils import now_bjt
from app.projects.models import Project, ProjectRun
from .fde_v4 import MATERIAL_FDE_PROJECT_ID, dispatch_fde_capability
from .models import MediaGenerationJob, MediaMaterialRequest
FILL_USER_ID = "admin"
TARGET_ACTIVE_JOBS = 2
MAX_BUFFER_JOBS = 4
ACTIVE_JOB_STATUSES = ("queued", "assigned", "running", "collecting")
def _load_pack() -> dict[str, Any]:
    if not settings.MEDIA_RECIPE_FILE:
        return {}
    path = Path(settings.MEDIA_RECIPE_FILE).expanduser()
    if not path.is_file():
        raise FileNotFoundError("Configured MEDIA_RECIPE_FILE does not exist")
    pack = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(pack, dict) or not pack.get("project_id"):
        raise ValueError("Recipe pack requires an explicit project_id")
    return pack
def _stop_create_at(pack: dict[str, Any]) -> datetime:
    raw = str(pack.get("stop_create_at") or "")
    if not raw:
        raise ValueError("Recipe pack requires an explicit stop_create_at deadline")
    deadline = datetime.fromisoformat(raw)
    if deadline.tzinfo is not None:
        deadline = deadline.astimezone(ZoneInfo("Asia/Shanghai"))
    return deadline.replace(tzinfo=None)
def create_window_status(now: datetime | None = None) -> dict[str, Any]:
    pack = _load_pack()
    current = now or now_bjt()
    if not pack:
        return {"open": False, "phase": "disabled", "now": current.isoformat(), "stop_create_at": None}
    stop_at = _stop_create_at(pack)
    if current >= stop_at:
        return {"open": False, "phase": "draining", "now": current.isoformat(sep=" ", timespec="seconds"), "stop_create_at": stop_at.isoformat(sep=" ")}
    return {"open": True, "phase": "filling", "now": current.isoformat(sep=" ", timespec="seconds"), "stop_create_at": stop_at.isoformat(sep=" ")}
async def _active_job_count(db: AsyncSession, project_id: str) -> int:
    return int((await db.execute(select(func.count(MediaGenerationJob.id)).where(
        MediaGenerationJob.project_id == project_id,
        MediaGenerationJob.status.in_(ACTIVE_JOB_STATUSES),
    ))).scalar() or 0)
async def _used_titles(db: AsyncSession, project_id: str) -> set[str]:
    rows = (await db.execute(select(MediaMaterialRequest.title).where(MediaMaterialRequest.project_id == project_id))).all()
    return {str(title) for (title,) in rows if title}
async def _pick_live_run(db: AsyncSession, project: Project) -> ProjectRun | None:
    return (await db.execute(
        select(ProjectRun).where(ProjectRun.project_id == project.id, ProjectRun.status == "running")
        .order_by(ProjectRun.last_heartbeat_at.desc().nullslast(), ProjectRun.updated_at.desc()).limit(1)
    )).scalar_one_or_none()
async def _next_recipe(db: AsyncSession, pack: dict[str, Any], *, prefer_kind: str | None = None) -> dict[str, Any]:
    used = await _used_titles(db, pack["project_id"])
    unused = [dict(item) for item in (pack.get("recipes") or []) if item.get("title") not in used]
    if not unused:
        raise RuntimeError("all overnight recipes already used")
    if prefer_kind:
        preferred = [item for item in unused if item.get("kind") == prefer_kind]
        if preferred:
            return preferred[0]
    return unused[0]
async def _create_one(db: AsyncSession, user: User, project: Project, run: ProjectRun, recipe: dict[str, Any]) -> dict[str, Any]:
    rules = _load_pack().get("rules") or {}
    payload = {
        "source": "editorial", "title": recipe["title"], "visual_prompt": recipe["prompt"],
        "script": recipe.get("line") or "", "production_mode": "direct", "quantity": 1,
        "duration_seconds": int(recipe.get("seconds") or 8), "ratio": rules.get("aspect") or "9:16",
        "output_preset_id": rules.get("clarity") or "quick_preview",
        "allow_ai_optimization": False, "allow_script_changes": False,
        "business": {"product": "连续生产-不要进画面", "platform": "douyin", "promotion": "", "selling_points": ""},
        "generation_participation": {"sku": False, "product": False, "platform": False, "promotion": False, "selling_points": False},
        "client_nonce": uuid4().hex,
    }
    created = await dispatch_fde_capability(db, user, project, run, "material.request.create", payload)
    request = created["request"]
    await dispatch_fde_capability(db, user, project, run, "material.request.submit", {"request_id": request["id"]})
    generated = await dispatch_fde_capability(db, user, project, run, "material.candidate.generate", {"request_id": request["id"], "mode": "direct", "action": "submit"})
    job_ids = [item.get("id") for item in (generated.get("jobs") or []) if item.get("id")]
    for candidate in generated.get("candidates") or []:
        job_id = candidate.get("media_job_id")
        if job_id and job_id not in job_ids:
            job_ids.append(job_id)
    logger.info("media-fill created request={} title={} jobs={} run={}", request["id"], recipe["title"], job_ids, run.id)
    return {"request_id": request["id"], "title": recipe["title"], "job_ids": job_ids}
async def replenish_material_production_once(db: AsyncSession, *, target_active: int = TARGET_ACTIVE_JOBS, max_buffer: int = MAX_BUFFER_JOBS, prefer_kind: str | None = None) -> dict[str, Any]:
    pack = _load_pack()
    project_id = pack.get("project_id") or MATERIAL_FDE_PROJECT_ID
    window = create_window_status()
    await db.execute(sa.text("SELECT pg_advisory_xact_lock(931765248, 2)"))
    active = await _active_job_count(db, project_id)
    stats: dict[str, Any] = {"active": active, "created": 0, "request_ids": [], "job_ids": [], "titles": [], "window": window, "need": 0}
    if not window["open"]:
        stats["error"] = window["phase"]
        return stats
    need = max(0, min(int(target_active) - active, int(max_buffer) - active, 1))
    stats["need"] = need
    if need <= 0:
        return stats
    project = await db.get(Project, project_id)
    user = await db.get(User, FILL_USER_ID)
    if project is None or user is None:
        stats["error"] = "missing_project_or_user"
        return stats
    run = await _pick_live_run(db, project)
    if run is None:
        stats["error"] = "no_running_project_run"
        return stats
    recipe = await _next_recipe(db, pack, prefer_kind=prefer_kind)
    created = await _create_one(db, user, project, run, recipe)
    stats["created"] = 1
    stats["request_ids"].append(created["request_id"])
    stats["job_ids"].extend(created["job_ids"])
    stats["titles"].append(created["title"])
    stats["active"] = await _active_job_count(db, project_id)
    return stats
