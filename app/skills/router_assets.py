"""Skill 资产管理 API 路由：模板发布 / 发布历史 / 血缘图 / Skill Fork / Export-Import"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
# v2.8.0: uuid4 不再直接用，id 生成走 app.skills.id_gen
from loguru import logger

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt
from app.config import settings
from app.database import get_db
from app.skills import asset_service
from app.skills.core.access import SkillTag
from app.skills.lifecycle import service as skill_service
from app.skills.core.service_shared import ensure_skill_department_access, validate_skill_id

router = APIRouter()
_SKILL_VERSION_RE = re.compile(r"^v\d+(?:\.\d+){0,2}$")


def _normalize_skill_version(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if value[0] in {"v", "V"}:
        value = f"v{value[1:]}"
    elif value[0].isdigit():
        value = f"v{value}"
    if not _SKILL_VERSION_RE.fullmatch(value):
        raise AppError(
            "PARAM_INVALID",
            422,
            {"detail": "版本格式非法，示例：v1.0 / v1.2.3"},
        )
    return value


# ────────────────────────── 请求模型 ──────────────────────────

class ForkSkillRequest(BaseModel):
    new_skill_id: str
    department: str


class BatchForkItem(BaseModel):
    source_skill_id: str
    new_skill_id: str


class BatchForkRequest(BaseModel):
    department: str
    items: list[BatchForkItem]


class BatchUnpublishRequest(BaseModel):
    skill_ids: list[str]


class BatchSetTagsItem(BaseModel):
    skill_id: str
    tags: list[str]


class BatchSetTagsRequest(BaseModel):
    items: list[BatchSetTagsItem]


class CreateReleaseRequest(BaseModel):
    version: str
    git_ref: str | None = None
    review_id: int | None = None
    release_notes: str | None = None


# ────────────────────────── 模板 ──────────────────────────

@router.post("/{skill_id}/publish-as-template")
async def publish_as_template(
    skill_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """从已有 Skill 创建模板"""
    await ensure_skill_department_access(db, skill_id, current_user)
    return await asset_service.create_template_from_skill(db, skill_id, current_user.id)


@router.get("/templates")
async def list_templates(
    category: str | None = Query(None),
    published_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """列出所有模板"""
    return await asset_service.list_templates(db, category, published_only)


@router.get("/templates/{template_id}")
async def get_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """获取单个模板详情"""
    return await asset_service.get_template(db, template_id)


@router.post("/templates/{template_id}/publish")
async def publish_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """发布模板到市场"""
    return await asset_service.publish_template(db, template_id, current_user.id)


# ────────────────────────── 发布记录 ──────────────────────────

@router.get("/{skill_id}/releases")
async def list_releases(
    skill_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """获取 Skill 的发布历史"""
    await ensure_skill_department_access(db, skill_id, current_user)
    return await asset_service.list_releases(db, skill_id)


@router.post("/{skill_id}/releases")
async def create_release(
    skill_id: str,
    body: CreateReleaseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """手动创建发布记录"""
    await ensure_skill_department_access(db, skill_id, current_user)
    return await asset_service.create_release(
        db, skill_id, body.version, body.git_ref, body.review_id,
        current_user.id, body.release_notes,
    )


# ────────────────────────── 血缘关系 ──────────────────────────

@router.get("/{skill_id}/lineage")
async def get_lineage(
    skill_id: str,
    direction: str = Query("both", pattern="^(upstream|downstream|both)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """获取 Skill 血缘图（上游 + 下游）"""
    return await asset_service.get_lineage(db, skill_id, direction)


# ────────────────────────── Skill-to-Skill Fork ──────────────────────────

@router.post("/{skill_id}/fork")
async def fork_skill(
    skill_id: str,
    body: ForkSkillRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """从已有 Skill Fork 创建新 Skill"""
    await ensure_skill_department_access(db, skill_id, current_user)
    return await asset_service.fork_skill(
        db, skill_id, body.new_skill_id, body.department, current_user.id,
    )


@router.post("/batch-fork")
async def batch_fork_skills(
    body: BatchForkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """N5: 批量 Fork — 一次提交多条 (source_skill_id, new_skill_id)，
    单条失败不阻断其他（部分成功也返回 200，前端按 results[].status 区分）。

    W2-F: 速率限制 items ≤ 50，防止后端过载。
    """
    from app.common.audit import audit

    if len(body.items) > 50:
        raise AppError("PARAM_INVALID", 422, {"detail": "batch-fork 单次最多 50 条，请分批"})
    if len(body.items) == 0:
        raise AppError("PARAM_INVALID", 422, {"detail": "items 不能为空"})
    results: list[dict] = []
    for item in body.items:
        result: dict = {"source": item.source_skill_id, "new_id": item.new_skill_id}
        try:
            await ensure_skill_department_access(db, item.source_skill_id, current_user)
            fork_result = await asset_service.fork_skill(
                db, item.source_skill_id, item.new_skill_id, body.department, current_user.id,
            )
            result["status"] = "ok"
            result["forked"] = fork_result
        except AppError as e:
            result["status"] = "err"
            result["code"] = e.code
            result["message"] = e.message
        except Exception as e:  # noqa: BLE001 — 保守兜底让其他条继续跑
            result["status"] = "err"
            result["code"] = "UNEXPECTED"
            result["message"] = str(e)
        results.append(result)
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    err_count = sum(1 for r in results if r.get("status") == "err")
    try:
        await audit.log(
            user_id=current_user.id,
            action="skill.batch_fork",
            target_type="skill_batch",
            target_id=body.department,
            detail={
                "department": body.department,
                "total": len(results),
                "ok_count": ok_count,
                "err_count": err_count,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("batch-fork audit log 失败 department={} err={}", body.department, exc)
    return {"total": len(results), "ok_count": ok_count, "err_count": err_count, "results": results}


# ─────────────────────────── W2-C 批量操作 ───────────────────────────

@router.post("/batch-unpublish")
async def batch_unpublish_skills(
    body: BatchUnpublishRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """W2-C: 批量下线（status: active → deprecated）。仅当前 status=active 的才会被改；
    其他状态忽略。单次最多 50 条。"""
    if len(body.skill_ids) > 50:
        raise AppError("PARAM_INVALID", 422, {"detail": "batch-unpublish 单次最多 50 条"})
    if len(body.skill_ids) == 0:
        raise AppError("PARAM_INVALID", 422, {"detail": "skill_ids 不能为空"})
    from sqlalchemy import select
    from app.skills.core.models import Skill
    results: list[dict] = []
    for sid in body.skill_ids:
        result: dict = {"skill_id": sid}
        try:
            await ensure_skill_department_access(db, sid, current_user)
            skill = (await db.execute(select(Skill).where(Skill.id == sid))).scalar_one_or_none()
            if not skill:
                result["status"] = "err"
                result["message"] = "skill 不存在"
            elif skill.status != "active":
                result["status"] = "skipped"
                result["message"] = f"当前 status={skill.status}，非 active 不可下线"
            else:
                skill.status = "deprecated"
                await db.commit()
                result["status"] = "ok"
        except AppError as e:
            result["status"] = "err"
            result["code"] = e.code
            result["message"] = e.message
        except Exception as e:  # noqa: BLE001
            result["status"] = "err"
            result["message"] = str(e)
        results.append(result)
    return {
        "total": len(results),
        "ok_count": sum(1 for r in results if r.get("status") == "ok"),
        "err_count": sum(1 for r in results if r.get("status") == "err"),
        "skipped_count": sum(1 for r in results if r.get("status") == "skipped"),
        "results": results,
    }


@router.post("/batch-set-tags")
async def batch_set_tags(
    body: BatchSetTagsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """W2-C: 批量替换 Skill tag。先删旧，再写新。单次最多 50 条。"""
    if len(body.items) > 50:
        raise AppError("PARAM_INVALID", 422, {"detail": "batch-set-tags 单次最多 50 条"})
    if len(body.items) == 0:
        raise AppError("PARAM_INVALID", 422, {"detail": "items 不能为空"})
    from sqlalchemy import delete as sa_delete
    results: list[dict] = []
    for item in body.items:
        result: dict = {"skill_id": item.skill_id}
        try:
            await ensure_skill_department_access(db, item.skill_id, current_user)
            await db.execute(sa_delete(SkillTag).where(SkillTag.skill_id == item.skill_id))
            for tag in item.tags:
                if tag and tag.strip():
                    db.add(SkillTag(skill_id=item.skill_id, tag=tag.strip()))
            await db.commit()
            result["status"] = "ok"
            result["tags"] = item.tags
        except AppError as e:
            await db.rollback()
            result["status"] = "err"
            result["code"] = e.code
            result["message"] = e.message
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            result["status"] = "err"
            result["message"] = str(e)
        results.append(result)
    return {
        "total": len(results),
        "ok_count": sum(1 for r in results if r.get("status") == "ok"),
        "err_count": sum(1 for r in results if r.get("status") == "err"),
        "results": results,
    }


# ─────────────────────────── W4-C Export / Import ───────────────────────────

@router.get("/{skill_id}/export")
async def export_skill(
    skill_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """W4-C: 把 Skill 打包成 zip 下载。包含 skills-repo/<skill_id>/ 目录下所有文件
    （SKILL.md + scripts/ + tests/ + references/ 等）+ 一个 manifest.json 记录元数据。
    """
    await ensure_skill_department_access(db, skill_id, current_user)
    src = Path(settings.SKILL_REPO_PATH) / skill_id
    if not src.is_dir():
        raise AppError("SKILL_REPO_NOT_FOUND", 404, {"detail": f"未找到 Skill 目录: {src}"})

    from app.skills.core.models import Skill
    from sqlalchemy import select
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    manifest = {
        "id": skill.id,
        "name": skill.name,
        "display_name": skill.display_name,
        "department": skill.department,
        "role": skill.role if hasattr(skill, "role") else None,
        "trigger_type": skill.trigger_type,
        "risk_level": skill.risk_level,
        "category": skill.category,
        "description": skill.description,
        "exported_at": isoformat_bjt(skill.updated_at),
        "exported_by": current_user.id,
        "version": "1",
        "current_version": skill.current_version,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for path in src.rglob("*"):
            if path.is_file():
                # 排除大体积缓存 / hidden
                rel = path.relative_to(src)
                if any(part in {".git", "__pycache__", "cache", ".venv"} for part in rel.parts):
                    continue
                zf.write(path, arcname=str(rel))

    buf.seek(0)
    archive_name = f"{skill_id}-{skill.current_version}.zip" if skill.current_version else f"{skill_id}.zip"
    headers = {
        "Content-Disposition": f'attachment; filename="{archive_name}"',
    }
    return StreamingResponse(iter([buf.getvalue()]), media_type="application/zip", headers=headers)


@router.post("/import")
async def import_skill(
    file: UploadFile = File(...),
    new_skill_id: str | None = Query(None, description="可选：导入时覆盖 manifest 里的 skill_id"),
    department: str | None = Query(None, description="可选：覆盖 manifest 里的 department"),
    current_version: str | None = Query(None, description="可选：覆盖 manifest 里的当前版本，如 v1.0"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """W4-C: 上传 Skill 包 zip，解包校验，创建新 Skill。manifest.json 提供默认 meta，
    可通过 query 参数覆盖 skill_id 和 department。"""
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:  # 20 MB
        raise AppError("PARAM_INVALID", 422, {"detail": "zip 最大 20 MB"})
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise AppError("PARAM_INVALID", 422, {"detail": "不是合法的 zip 文件"})

    files: dict[str, str] = {}
    manifest: dict = {}
    for name in zf.namelist():
        if name.endswith("/") or ".." in name:  # 防 zip-slip
            continue
        content = zf.read(name)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1")  # 兜底
        if name == "manifest.json":
            try:
                manifest = json.loads(text)
            except json.JSONDecodeError:
                raise AppError("PARAM_INVALID", 422, {"detail": "manifest.json 非合法 JSON"})
        else:
            files[name] = text

    if "SKILL.md" not in files:
        raise AppError("PARAM_INVALID", 422, {"detail": "zip 必须包含 SKILL.md"})

    # v2.8.0：统一走 id_gen.gen_from_import，产出 `imp-{dept}-{uuid8}` 格式。
    target_department = (department or manifest.get("department") or current_user.department or "").strip()
    if not target_department or target_department == "未指定":
        raise AppError("PARAM_INVALID", 422, {"detail": "import 时必须指定有效部门（manifest 或 query 参数）"})

    # v2.8.0: 部门访问校验 —— 非 admin 只能导到自己可访问的部门
    _is_global = (current_user.role in ("admin", "system_admin")) or current_user.can_view_all
    if not _is_global:
        accessible = set(
            [current_user.department] + (getattr(current_user, "accessible_departments", None) or [])
        )
        if target_department not in accessible:
            raise AppError(
                "FORBIDDEN", 403,
                {"detail": f"你没有权限向部门 {target_department!r} 导入 Skill"},
            )

    from app.skills.core.id_gen import gen_from_import
    target_skill_id = (new_skill_id or manifest.get("id") or gen_from_import(department=target_department)).strip()
    try:
        validate_skill_id(target_skill_id)
    except Exception as e:
        raise AppError("PARAM_INVALID", 422, {"detail": f"skill_id 非法: {e}"})

    target_risk = manifest.get("risk_level") or "R2"
    if target_risk not in ("R1", "R2", "R3", "R4"):
        target_risk = "R2"
    target_version = _normalize_skill_version(
        current_version or manifest.get("current_version") or manifest.get("skill_version")
    )

    result = await skill_service.create_skill_from_files(
        db,
        skill_id=target_skill_id,
        name=manifest.get("name") or target_skill_id,
        department=target_department,
        files=files,
        user_id=current_user.id,
        trigger_type=manifest.get("trigger_type") or "manual",
        risk_level=target_risk,
        current_version=target_version,
    )

    # v2.8.0: 导入审计（安全要求）
    try:
        from app.common.audit import audit

        await audit.log(
            user_id=current_user.id,
            action="skill.import",
            target_type="skill",
            target_id=target_skill_id,
            detail={
                "department": target_department,
                "current_version": target_version,
                "file_count": len(files),
                "zip_size_bytes": len(raw),
                "manifest_id": manifest.get("id"),
            },
        )
    except Exception as e:  # noqa: BLE001
        from loguru import logger
        logger.warning("skill.import audit log 失败: {}", e)

    return {
        "skill_id": result.get("skill_id"),
        "git_commit": result.get("git_commit"),
        "current_version": result.get("current_version") or target_version,
        "imported_files": list(files.keys()),
        "manifest": manifest,
    }
