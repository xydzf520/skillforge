"""Skill 资产管理服务：模板 / 发布记录 / 血缘追踪 / Skill-to-Skill Fork"""

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import audit
from app.common.exceptions import AppError
from app.config import settings
from app.skills.asset_models import SkillInstance, SkillLineage, SkillRelease, SkillTemplate
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill
from app.skills.core.service_shared import validate_skill_id
from app.common.time_utils import isoformat_bjt, now_bjt


# ────────────────────────── 模板管理 ──────────────────────────

async def create_template_from_skill(
    db: AsyncSession, skill_id: str, user_id: str,
) -> dict:
    """从已有 Skill 提取为模板。读取 SKILL.md + policy_pack.yaml，生成 schema 快照。"""
    validate_skill_id(skill_id)

    # 确认源 Skill 存在
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    # 生成模板 ID：tpl-{skill_id}
    template_id = f"tpl-{skill_id}"

    # 检查是否已存在
    existing = await db.execute(
        select(SkillTemplate).where(SkillTemplate.id == template_id)
    )
    if existing.scalar_one_or_none():
        raise AppError("TEMPLATE_ALREADY_EXISTS", 409)

    # 读取 SKILL.md 和 policy_pack
    skill_md = git_service.read_file(skill_id, "SKILL.md") or ""
    policy_raw = git_service.read_file(skill_id, "policy_pack.yaml") or ""

    import yaml
    try:
        policy = yaml.safe_load(policy_raw) or {}
    except yaml.YAMLError:
        policy = {}

    template = SkillTemplate(
        id=template_id,
        name=f"{skill.name} 模板",
        description=skill.description or f"从 {skill_id} 提取的模板",
        category=skill.department,
        owner_id=user_id,
        schema_json=policy,
        ui_schema_json=None,
        runtime_profile={"trigger_type": skill.trigger_type, "risk_level": skill.risk_level},
        is_published=False,
        version="v1.0",
        source_skill_id=skill_id,
        tags=[skill.department] if skill.department else [],
        fork_count=0,
    )
    db.add(template)
    await db.flush()

    await audit.log(user_id, "template.create", "skill_template", template_id,
                    {"source_skill_id": skill_id})

    logger.info(f"模板创建成功: {template_id} (来源: {skill_id})")
    return {
        "template_id": template_id,
        "name": template.name,
        "source_skill_id": skill_id,
        "version": template.version,
    }


async def list_templates(
    db: AsyncSession,
    category: str | None = None,
    published_only: bool = True,
) -> list[dict]:
    """列出模板，可按分类和发布状态筛选"""
    stmt = select(SkillTemplate).order_by(SkillTemplate.updated_at.desc())
    if category:
        stmt = stmt.where(SkillTemplate.category == category)
    if published_only:
        stmt = stmt.where(SkillTemplate.is_published == True)  # noqa: E712

    result = await db.execute(stmt)
    templates = result.scalars().all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "category": t.category,
            "version": t.version,
            "is_published": t.is_published,
            "fork_count": t.fork_count,
            "tags": t.tags or [],
            "updated_at": isoformat_bjt(t.updated_at),
        }
        for t in templates
    ]


async def get_template(db: AsyncSession, template_id: str) -> dict:
    """获取单个模板详情"""
    result = await db.execute(
        select(SkillTemplate).where(SkillTemplate.id == template_id)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise AppError("TEMPLATE_NOT_FOUND", 404)
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "category": t.category,
        "owner_id": t.owner_id,
        "schema_json": t.schema_json,
        "ui_schema_json": t.ui_schema_json,
        "runtime_profile": t.runtime_profile,
        "is_published": t.is_published,
        "version": t.version,
        "source_skill_id": t.source_skill_id,
        "tags": t.tags or [],
        "fork_count": t.fork_count,
        "created_at": isoformat_bjt(t.created_at),
        "updated_at": isoformat_bjt(t.updated_at),
    }


async def publish_template(
    db: AsyncSession, template_id: str, user_id: str,
) -> dict:
    """发布模板，使其在模板市场可见"""
    result = await db.execute(
        select(SkillTemplate).where(SkillTemplate.id == template_id)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise AppError("TEMPLATE_NOT_FOUND", 404)

    t.is_published = True
    t.updated_at = now_bjt()
    await db.flush()

    await audit.log(user_id, "template.publish", "skill_template", template_id)
    return {"template_id": template_id, "is_published": True}


# ────────────────────────── 发布记录 ──────────────────────────

async def create_release(
    db: AsyncSession,
    skill_id: str,
    version: str,
    git_ref: str | None,
    review_id: int | None,
    user_id: str,
    release_notes: str | None = None,
) -> dict:
    """创建一条发布记录。计算 SKILL.md 的 sha256 作为 artifact_digest。"""
    validate_skill_id(skill_id)

    # 确认 Skill 存在
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    if not result.scalar_one_or_none():
        raise AppError("SKILL_NOT_FOUND", 404)

    # 计算 SKILL.md digest
    skill_md = git_service.read_file(skill_id, "SKILL.md") or ""
    digest = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()

    release = SkillRelease(
        skill_id=skill_id,
        version=version,
        git_ref=git_ref,
        artifact_digest=digest,
        review_id=review_id,
        release_notes=release_notes,
        released_by=user_id,
    )
    db.add(release)
    await db.flush()

    await audit.log(user_id, "skill.release", "skill", skill_id,
                    {"version": version, "git_ref": git_ref})

    logger.info(f"发布记录创建: {skill_id} {version}")
    return {
        "id": release.id,
        "skill_id": skill_id,
        "version": version,
        "git_ref": git_ref,
        "artifact_digest": digest,
        "released_at": isoformat_bjt(release.released_at),
    }


async def list_releases(db: AsyncSession, skill_id: str) -> list[dict]:
    """列出 Skill 的所有发布记录，按时间倒序"""
    validate_skill_id(skill_id)

    stmt = (
        select(SkillRelease)
        .where(SkillRelease.skill_id == skill_id)
        .order_by(SkillRelease.released_at.desc())
    )
    result = await db.execute(stmt)
    releases = result.scalars().all()
    return [
        {
            "id": r.id,
            "skill_id": r.skill_id,
            "version": r.version,
            "git_ref": r.git_ref,
            "artifact_digest": r.artifact_digest,
            "review_id": r.review_id,
            "release_notes": r.release_notes,
            "released_by": r.released_by,
            "released_at": isoformat_bjt(r.released_at),
        }
        for r in releases
    ]


# ────────────────────────── 血缘追踪 ──────────────────────────

async def record_lineage(
    db: AsyncSession,
    source_id: str,
    target_id: str,
    relation_type: str,
    source_version: str | None = None,
) -> dict:
    """记录一条血缘关系（fork/derive/upgrade）"""
    if relation_type not in ("fork", "derive", "upgrade"):
        raise AppError("PARAM_INVALID", 400, {"detail": f"非法 relation_type: {relation_type}"})

    lineage = SkillLineage(
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        source_version=source_version,
    )
    db.add(lineage)
    await db.flush()

    logger.info(f"血缘记录: {source_id} --[{relation_type}]--> {target_id}")
    return {
        "id": lineage.id,
        "source_id": source_id,
        "target_id": target_id,
        "relation_type": relation_type,
        "source_version": source_version,
    }


async def get_lineage(
    db: AsyncSession, skill_id: str, direction: str = "both",
) -> dict:
    """获取 Skill 的血缘关系，支持 upstream / downstream / both"""
    validate_skill_id(skill_id)
    result: dict = {"skill_id": skill_id, "upstream": [], "downstream": []}

    if direction in ("upstream", "both"):
        # 谁是我的源头（我是 target）
        stmt = select(SkillLineage).where(SkillLineage.target_id == skill_id)
        rows = (await db.execute(stmt)).scalars().all()
        result["upstream"] = [
            {
                "id": r.id,
                "source_id": r.source_id,
                "relation_type": r.relation_type,
                "source_version": r.source_version,
                "created_at": isoformat_bjt(r.created_at),
            }
            for r in rows
        ]

    if direction in ("downstream", "both"):
        # 谁从我 fork / derive（我是 source）
        stmt = select(SkillLineage).where(SkillLineage.source_id == skill_id)
        rows = (await db.execute(stmt)).scalars().all()
        result["downstream"] = [
            {
                "id": r.id,
                "target_id": r.target_id,
                "relation_type": r.relation_type,
                "source_version": r.source_version,
                "created_at": isoformat_bjt(r.created_at),
            }
            for r in rows
        ]

    return result


# ────────────────────────── Skill-to-Skill Fork ──────────────────────────

async def fork_skill(
    db: AsyncSession,
    source_skill_id: str,
    new_skill_id: str,
    department: str,
    user_id: str,
) -> dict:
    """从已有 Skill 复制一份新 Skill（文件 + DB + 血缘记录）"""
    validate_skill_id(source_skill_id)
    validate_skill_id(new_skill_id)

    # 确认源 Skill 存在
    result = await db.execute(select(Skill).where(Skill.id == source_skill_id))
    source = result.scalar_one_or_none()
    if not source:
        raise AppError("SKILL_NOT_FOUND", 404)

    # 确认目标 Skill 不存在
    result = await db.execute(select(Skill).where(Skill.id == new_skill_id))
    if result.scalar_one_or_none():
        raise AppError("SKILL_ALREADY_EXISTS", 409)

    # 复制 Git 文件目录
    source_dir = Path(settings.SKILL_REPO_PATH) / source_skill_id
    if not source_dir.is_dir():
        raise AppError("SKILL_NOT_FOUND", 404, {"detail": "源 Skill 文件目录不存在"})

    git_service.create_skill_dir(new_skill_id)
    target_dir = Path(settings.SKILL_REPO_PATH) / new_skill_id
    shutil.copytree(str(source_dir), str(target_dir), dirs_exist_ok=True)

    # 更新 SKILL.md 中的 frontmatter（替换 name / department）
    skill_md = git_service.read_file(new_skill_id, "SKILL.md") or ""
    if skill_md:
        import yaml as _yaml

        # 简单替换 frontmatter 中的 department 行（保持格式兼容）
        lines = skill_md.split("\n")
        new_lines = []
        for line in lines:
            if line.strip().startswith("department:"):
                new_lines.append(f"department: {department}")
            else:
                new_lines.append(line)
        git_service.write_file(new_skill_id, "SKILL.md", "\n".join(new_lines))

    # Git commit
    commit_sha = git_service.commit_all(
        f"Fork Skill: {source_skill_id} → {new_skill_id}",
        user_id,
        skill_id=new_skill_id,
    )

    # 写数据库 — 新 Skill 记录
    new_skill = Skill(
        id=new_skill_id,
        name=f"{source.name} (Fork)",
        description=source.description,
        department=department,
        role=source.role,
        trigger_type=source.trigger_type,
        trigger_expression=source.trigger_expression,
        risk_level=source.risk_level,
        approval_level=source.approval_level,
        status="draft",
        owner=user_id,
        git_commit=commit_sha,
    )
    db.add(new_skill)
    await db.flush()

    # 记录血缘关系
    lineage = await record_lineage(
        db,
        source_id=source_skill_id,
        target_id=new_skill_id,
        relation_type="fork",
        source_version=source.current_version,
    )

    # 审计日志
    await audit.log(user_id, "skill.fork", "skill", new_skill_id,
                    {"source_skill_id": source_skill_id})

    logger.info(f"Skill Fork 完成: {source_skill_id} → {new_skill_id}")
    return {
        "skill_id": new_skill_id,
        "source_skill_id": source_skill_id,
        "git_commit": commit_sha,
        "lineage_id": lineage["id"],
    }
