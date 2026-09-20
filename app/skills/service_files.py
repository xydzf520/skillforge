"""Skills 文件域服务。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import audit
from app.common.exceptions import AppError
from app.skills.core.git_service import git_service
from app.skills.tooling.manifest_service import build_skill_manifest_from_dir
from app.skills.core.models import Skill
from app.skills.core.parser import skill_parser
from app.skills.core.service_shared import sync_skill_fields_from_frontmatter, validate_skill_id
from app.common.time_utils import isoformat_bjt, now_bjt


def _elapsed_seconds_since(value: datetime) -> float:
    """Handle legacy UTC-naive locks and current BJT-naive locks."""
    legacy_utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    ages = [
        (now_bjt() - value).total_seconds(),
        (legacy_utc_now - value).total_seconds(),
    ]
    non_negative = [age for age in ages if age >= 0]
    return min(non_negative) if non_negative else 0


def list_scripts(skill_id: str) -> list[dict]:
    validate_skill_id(skill_id)
    manifest = build_skill_manifest_from_dir(git_service.skill_dir(skill_id), skill_id=skill_id)
    return manifest.get("scripts") or []


def run_script(skill_id: str, relative_path: str, payload: dict | None = None, timeout: int = 10) -> dict:
    validate_skill_id(skill_id)
    if not relative_path or not relative_path.startswith("scripts/"):
        raise AppError("PARAM_INVALID", 400, {"detail": "仅允许执行 scripts/ 下的脚本"})
    script_path = git_service._safe_path(skill_id, relative_path)
    if not script_path.exists():
        raise AppError("SKILL_FILE_NOT_FOUND", 404)
    from app.sandbox.executor import run_script as run_script_sandboxed

    result = run_script_sandboxed(str(script_path), payload=payload or {}, timeout=timeout)
    return {
        "script": relative_path,
        "success": bool(result.get("success")),
        "output": result.get("output"),
        "error": result.get("error"),
        "duration_ms": result.get("duration_ms"),
        "raw": result.get("raw"),
    }


async def save_file(
    db: AsyncSession,
    skill_id: str,
    file_path: str,
    content: str,
    user_id: str = "system",
) -> dict:
    validate_skill_id(skill_id)
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    if file_path == "SKILL.md":
        from app.skills.validators import quick_validate
        qv = quick_validate(content)
        if not qv.ok:
            raise AppError("SKILL_VALIDATION_FAILED", 422, {"detail": qv.to_detail()})

    git_service.write_file(skill_id, file_path, content)

    if file_path == "SKILL.md":
        parsed = skill_parser.parse(content)
        sync_skill_fields_from_frontmatter(skill, parsed.frontmatter)

    commit_sha = git_service.commit_all(f"更新 {file_path}", user_id, skill_id=skill_id)
    if commit_sha:
        skill.git_commit = commit_sha
    skill.updated_at = now_bjt()
    await db.flush()
    await audit.log(user_id, "skill.edit", "skill", skill_id, detail={"file": file_path})
    return {"skill_id": skill_id, "file": file_path, "git_commit": commit_sha}


async def create_file(
    db: AsyncSession,
    skill_id: str,
    file_path: str,
    content: str = "",
    is_dir: bool = False,
    user_id: str = "system",
) -> dict:
    validate_skill_id(skill_id)
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    if is_dir:
        git_service.create_dir(skill_id, file_path)
        git_service.write_file(skill_id, f"{file_path}/.gitkeep", "")
    else:
        git_service.write_file(skill_id, file_path, content)

    commit_sha = git_service.commit_all(f"新建 {file_path}", user_id, skill_id=skill_id)
    if commit_sha:
        skill.git_commit = commit_sha
    skill.updated_at = now_bjt()
    await db.flush()
    await audit.log(user_id, "skill.file.create", "skill", skill_id, detail={"file": file_path})
    return {"skill_id": skill_id, "file": file_path, "git_commit": commit_sha}


async def delete_file(
    db: AsyncSession,
    skill_id: str,
    file_path: str,
    user_id: str = "system",
) -> dict:
    validate_skill_id(skill_id)
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    git_service.delete_file(skill_id, file_path)
    commit_sha = git_service.commit_all(f"删除 {file_path}", user_id, skill_id=skill_id)
    if commit_sha:
        skill.git_commit = commit_sha
    skill.updated_at = now_bjt()
    await db.flush()
    await audit.log(user_id, "skill.file.delete", "skill", skill_id, detail={"file": file_path})
    return {"skill_id": skill_id, "deleted": file_path, "git_commit": commit_sha}


async def rename_file(
    db: AsyncSession,
    skill_id: str,
    old_path: str,
    new_path: str,
    user_id: str = "system",
) -> dict:
    validate_skill_id(skill_id)
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    git_service.rename_file(skill_id, old_path, new_path)
    commit_sha = git_service.commit_all(f"重命名 {old_path} → {new_path}", user_id, skill_id=skill_id)
    if commit_sha:
        skill.git_commit = commit_sha
    skill.updated_at = now_bjt()
    await db.flush()
    await audit.log(user_id, "skill.file.rename", "skill", skill_id, detail={"old": old_path, "new": new_path})
    return {"skill_id": skill_id, "old_path": old_path, "new_path": new_path, "git_commit": commit_sha}


async def acquire_lock(db: AsyncSession, skill_id: str, user_id: str) -> dict:
    from app.skills.core.models import SkillLock

    validate_skill_id(skill_id)
    result = await db.execute(select(SkillLock).where(SkillLock.skill_id == skill_id))
    existing = result.scalar_one_or_none()

    if existing:
        elapsed = _elapsed_seconds_since(existing.locked_at)
        if elapsed < 1800 and existing.user_id != user_id:
            raise AppError("SKILL_LOCKED", 423, detail={
                "locked_by": existing.user_id,
                "locked_at": isoformat_bjt(existing.locked_at),
            })
        existing.user_id = user_id
        existing.locked_at = now_bjt()
    else:
        db.add(SkillLock(skill_id=skill_id, user_id=user_id, locked_at=now_bjt()))

    await db.flush()
    return {"skill_id": skill_id, "locked_by": user_id}


async def release_lock(db: AsyncSession, skill_id: str, user_id: str) -> dict:
    from app.skills.core.models import SkillLock

    result = await db.execute(select(SkillLock).where(SkillLock.skill_id == skill_id))
    existing = result.scalar_one_or_none()
    if existing and existing.user_id == user_id:
        await db.delete(existing)
        await db.flush()
    return {"skill_id": skill_id, "unlocked": True}


async def get_lock_status(db: AsyncSession, skill_id: str) -> dict | None:
    from app.skills.core.models import SkillLock

    result = await db.execute(select(SkillLock).where(SkillLock.skill_id == skill_id))
    lock = result.scalar_one_or_none()
    if not lock:
        return None

    elapsed = _elapsed_seconds_since(lock.locked_at)
    if elapsed >= 1800:
        await db.delete(lock)
        await db.flush()
        return None

    return {
        "skill_id": skill_id,
        "locked_by": lock.user_id,
        "locked_at": isoformat_bjt(lock.locked_at),
        "expires_in_seconds": int(1800 - elapsed),
    }
