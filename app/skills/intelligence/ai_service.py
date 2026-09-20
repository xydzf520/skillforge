"""
Skill AI 自动化服务 façade：保留稳定 API，按 feature 转发到拆分后的模块。
"""

import json
from datetime import datetime, timedelta

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ai import call_llm_cached
from app.common.cache_facade import NamespaceCache
from app.skills.intelligence.ai_service_catalog import (
    answer_skill_question,
    preview_field_migration,
    recommend_template,
)
from app.skills.intelligence.ai_service_evidence import get_param_evidence_impl
from app.skills.intelligence.ai_service_explain import build_explain_pack_impl
from app.skills.intelligence.ai_service_helpers import _truncate_dict
from app.skills.intelligence.ai_service_quality import (
    discover_antipatterns,
    generate_execution_summary,
    suggest_branches,
)
from app.skills.intelligence.ai_service_tuning import suggest_param_tuning
from app.skills.core.git_service import git_service
from app.skills.core.parser import skill_parser
from app.skills.core.service_shared import validate_skill_id

EXPLAIN_PACK_CACHE = NamespaceCache("skills:explain_pack", ttl=600)


async def cache_get(skill_id: str, version_key: str):
    return await EXPLAIN_PACK_CACHE.get(skill_id, version_key)


async def cache_set(skill_id: str, version_key: str, value: dict):
    await EXPLAIN_PACK_CACHE.set(skill_id, version_key, value=value)


async def build_explain_pack(
    db: AsyncSession,
    skill_id: str,
    *,
    user_id: str | None = None,
    department: str | None = None,
) -> dict:
    return await build_explain_pack_impl(
        db=db,
        skill_id=skill_id,
        cache_get=cache_get,
        cache_set=cache_set,
        call_llm_cached=call_llm_cached,
        user_id=user_id,
        department=department,
    )


# ── §4.5: 参数证据式建议（结构化证据卡）──

async def get_param_evidence(
    db: AsyncSession,
    skill_id: str,
    param_name: str,
    *,
    days: int = 30,
    user_id: str | None = None,
    department: str | None = None,
) -> dict:
    return await get_param_evidence_impl(
        db=db,
        skill_id=skill_id,
        param_name=param_name,
        validate_skill_id=validate_skill_id,
        git_service=git_service,
        call_llm_cached=call_llm_cached,
        truncate_dict=_truncate_dict,
        days=days,
        user_id=user_id,
        department=department,
    )
