"""Template/QA/migration AI features extracted from ai_service.py."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ai import call_llm, call_llm_cached
from app.common.cache import cache_get, cache_set
from app.common.cache_facade import NamespaceCache
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill
from app.skills.core.parser import skill_parser

TEMPLATE_CANDIDATES_CACHE = NamespaceCache("skills:template_candidates", ttl=300)
FIELD_MIGRATION_PREVIEW_CACHE = NamespaceCache("skills:field_migration_preview", ttl=300)


def _stable_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


async def recommend_template(db: AsyncSession, name: str, department: str, purpose: str) -> dict:
    candidate_list = await TEMPLATE_CANDIDATES_CACHE.get("active_shadow", "limit50")
    if candidate_list is None:
        stmt = (
            select(Skill)
            .where(Skill.status.in_(["active", "shadow"]))
            .limit(50)
        )
        result = await db.execute(stmt)
        candidates = result.scalars().all()

        candidate_list = []
        for skill in candidates:
            skill_md = git_service.read_file(skill.id, "SKILL.md")
            parsed_purpose = ""
            if skill_md:
                try:
                    parsed = skill_parser.parse(skill_md)
                    parsed_purpose = parsed.purpose[:200] if parsed else ""
                except Exception as e:
                    from loguru import logger
                    logger.debug("ai_service_catalog: SKILL.md 解析失败 skill={}: {}", skill.id, e)
            candidate_list.append({
                "skill_id": skill.id,
                "name": skill.name,
                "department": skill.department,
                "purpose": parsed_purpose,
            })
        await TEMPLATE_CANDIDATES_CACHE.set("active_shadow", "limit50", value=candidate_list)

    if len(candidate_list) < 1:
        return {"recommendations": []}

    system = (
        "你是Skill模板推荐助手。根据新Skill描述，从候选列表中推荐最相似的Top 3。"
        "输出纯JSON: {\"recommendations\": [{\"skill_id\": \"ID\", \"similarity\": 0-1, "
        "\"reason\": \"推荐原因\", \"reusable_parts\": [\"可复用部分\"]}]}"
    )
    user = (
        f"## 新 Skill\n名称: {name}\n部门: {department}\n目的: {purpose}\n\n"
        f"## 候选 Skill 列表\n```json\n{json.dumps(candidate_list, ensure_ascii=False, indent=1)}\n```\n\n"
        "推荐最相似的 Top 3，说明可复用的部分。"
    )

    purpose_hash = hashlib.md5(purpose.encode()).hexdigest()[:8]
    cache_key = f"template:{department}:{purpose_hash}"
    ai_result = await call_llm_cached(
        cache_key, system, user,
        cache_ttl=1800, max_tokens=1000, timeout=30,
        call_source="skill.recommend_template",
        cost_context={"department": department},
    )

    return {"recommendations": ai_result.get("recommendations", []) if ai_result else []}


async def answer_skill_question(skill_id: str, question: str) -> str:
    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        return f"未找到 Skill {skill_id} 的内容"

    q_hash = hashlib.md5(question.encode()).hexdigest()[:8]
    cache_key = f"dt_qa:{skill_id}:{q_hash}"
    cached = await cache_get(cache_key)
    if cached and isinstance(cached, str):
        return cached

    system = "你是SkillForge助手。基于Skill内容简洁回答运营人员的问题（100字以内）。"
    user = f"## Skill 内容\n{skill_md[:4000]}\n\n## 问题\n{question}"

    answer = await call_llm(
        system, user, json_mode=False, max_tokens=500, timeout=20,
        call_source="skill.dingtalk_qa",
        cost_context={"skill_id": skill_id},
    )
    if answer:
        await cache_set(cache_key, answer, ttl=300)
    return answer or "暂时无法回答，请稍后再试"


async def preview_field_migration(
    db: AsyncSession,
    ds_id: str,
    old_mapping: dict,
    new_mapping: dict,
) -> dict:
    changes = {}
    for display_name, old_field in old_mapping.items():
        new_field = new_mapping.get(display_name)
        if new_field and old_field != new_field:
            changes[old_field] = new_field

    if not changes:
        return {"affected_skills": [], "changes": {}}

    from app.datasources.models import DataSource

    ds_result = await db.execute(select(DataSource).where(DataSource.id == ds_id))
    datasource = ds_result.scalar_one_or_none()
    if not datasource:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    cache_key_parts = (
        ds_id,
        _stable_hash(old_mapping),
        _stable_hash(new_mapping),
        isoformat_bjt(getattr(datasource, "updated_at", None)),
    )
    cached = await FIELD_MIGRATION_PREVIEW_CACHE.get(*cache_key_parts)
    if cached is not None:
        return cached

    skills = await db.execute(select(Skill).where(Skill.status != "deprecated"))
    all_skills = skills.scalars().all()

    affected = []
    for skill in all_skills:
        skill_md = git_service.read_file(skill.id, "SKILL.md")
        if not skill_md:
            continue
        if ds_id not in skill_md and (datasource.name or "") not in skill_md:
            continue

        files_affected = []
        for old_field, new_field in changes.items():
            for index, line in enumerate(skill_md.split("\n"), 1):
                if old_field in line:
                    files_affected.append({
                        "path": "SKILL.md",
                        "line": index,
                        "old": old_field,
                        "new": new_field,
                        "context": line.strip()[:100],
                    })

            script = git_service.read_file(skill.id, "scripts/main.py")
            if script:
                for index, line in enumerate(script.split("\n"), 1):
                    if old_field in line:
                        files_affected.append({
                            "path": "scripts/main.py",
                            "line": index,
                            "old": old_field,
                            "new": new_field,
                            "context": line.strip()[:100],
                        })

        if files_affected:
            affected.append({
                "skill_id": skill.id,
                "skill_name": skill.name,
                "references": files_affected,
            })

    result = {
        "affected_skills": affected,
        "changes": changes,
        "auto_fixable": all(len(item["references"]) < 20 for item in affected),
    }
    await FIELD_MIGRATION_PREVIEW_CACHE.set(*cache_key_parts, value=result)
    return result
