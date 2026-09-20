"""Skill AI 路由：explain/reviewer/调优/模板推荐等能力。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.ai import call_llm_cached
from app.common.audit import audit
from app.common.exceptions import AppError
from app.database import get_db
from app.execution.models import DecisionLog
from app.skills.intelligence import ai_service
from app.skills.lifecycle import service
from app.skills.core.service_shared import ensure_skill_access
from app.common.time_utils import now_bjt

router = APIRouter()


@router.get("/motif-library")
async def motif_library_endpoint(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """Motif 库（Phase 4）— 从 Skill 库提取可复用的规则模式。"""
    from app.skills.intelligence.motif_library import get_motif_library

    motifs = await get_motif_library(db)
    return {"motifs": motifs}


@router.post("/{skill_id}/reviewer/summarize")
async def reviewer_summarize_endpoint(
    skill_id: str,
    commit_a: str = "HEAD~1",
    commit_b: str = "HEAD",
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """AI Reviewer：生成审批辅助报告。"""
    from app.skills.core.git_service import git_service
    from app.workbench.reviewer import review_change

    await ensure_skill_access(db, skill_id, current_user, "read")

    try:
        before_md = git_service.get_file_at_commit(skill_id, "SKILL.md", commit_a) or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("读取历史版本失败 skill_id={} commit={}: {}", skill_id, commit_a, e)
        before_md = ""
    after_md = git_service.read_file(skill_id, "SKILL.md") or ""

    report = await review_change(skill_id, before_md, after_md)
    await audit.log(
        user_id=current_user.id,
        action="reviewer.summarize",
        target_type="skill",
        target_id=skill_id,
        detail={"commit_a": commit_a, "commit_b": commit_b},
        prompt_hash=report.prompt_hash or None,
    )
    return report.to_dict()


@router.get("/{skill_id}/param-evidence")
async def param_evidence(
    skill_id: str,
    param_name: str = Query(..., min_length=1, max_length=100),
    days: int = Query(30, ge=7, le=180),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """参数证据式建议。"""
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await ai_service.get_param_evidence(
        db, skill_id, param_name, days=days,
        user_id=current_user.id,
        department=current_user.department,
    )


@router.post("/{skill_id}/suggest-param-tuning")
async def suggest_param_tuning(
    skill_id: str,
    days: int = Query(14, ge=7, le=90),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """AI 驱动参数调优建议。"""
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await ai_service.suggest_param_tuning(
        db, skill_id, days,
        user_id=current_user.id,
        department=current_user.department,
    )


@router.post("/{skill_id}/discover-antipatterns")
async def discover_antipatterns(
    skill_id: str,
    days: int = Query(30, ge=7, le=180),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """从被否决决策自动发现 Anti-pattern。"""
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await ai_service.discover_antipatterns(
        db, skill_id, days,
        user_id=current_user.id,
        department=current_user.department,
    )


class SuggestBranchesRequest(BaseModel):
    step_id: str
    existing_branches: list[dict]


@router.post("/{skill_id}/suggest-branches")
async def suggest_branches(
    skill_id: str,
    body: SuggestBranchesRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """决策分支智能补全。"""
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await ai_service.suggest_branches(
        db, skill_id, body.step_id, body.existing_branches,
        user_id=current_user.id,
        department=current_user.department,
    )


class DeriveThresholdsRequest(BaseModel):
    data_source_id: str
    target_metric: str = "adoption_rate"
    min_sample_size: int = 20


@router.post("/{skill_id}/derive-thresholds")
async def derive_thresholds(
    skill_id: str,
    body: DeriveThresholdsRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """从历史数据推导决策阈值。"""
    await ensure_skill_access(db, skill_id, current_user, "read")

    since = now_bjt() - timedelta(days=60)
    stmt = (
        select(DecisionLog)
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
        .where(DecisionLog.created_at >= since)
        .order_by(DecisionLog.created_at.desc())
        .limit(100)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    if len(logs) < body.min_sample_size:
        return {
            "thresholds": [],
            "message": f"数据不足: {len(logs)}条，至少需要{body.min_sample_size}条",
        }

    samples = []
    for item in logs[:50]:
        samples.append({
            "input": item.input_snapshot,
            "output": item.suggested_action,
            "user_action": item.user_action,
            "impact": item.business_impact,
        })

    system = (
        "你是数据分析师。从历史决策数据中推导最优决策阈值。"
        "输出纯JSON: {\"thresholds\": [{\"field\": \"字段\", \"breakpoints\": ["
        "{\"value\": 数值, \"confidence\": 0-1}], \"suggested_tree\": {\"steps\": [...]}}]}"
    )
    user = (
        f"## 数据源: {body.data_source_id}\n"
        f"## 目标指标: {body.target_metric}\n"
        f"## 决策采样 ({len(samples)}条)\n```json\n{json.dumps(samples, ensure_ascii=False, indent=1)[:4000]}\n```\n\n"
        "从数据中推导自然分界点，生成决策树建议。"
    )

    cache_key = f"thresholds:{skill_id}:{body.data_source_id}"
    ai_result = await call_llm_cached(
        cache_key, system, user,
        cache_ttl=1800, max_tokens=2000, timeout=45,
    )
    return ai_result or {"thresholds": [], "message": "AI分析暂时不可用"}


class RecommendTemplateRequest(BaseModel):
    name: str
    department: str = ""
    purpose: str = ""


@router.post("/recommend-template")
async def recommend_template(
    body: RecommendTemplateRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """跨 Skill 模板推荐。"""
    return await ai_service.recommend_template(db, body.name, body.department, body.purpose)


@router.get("/{skill_id}/explain-pack")
async def get_explain_pack(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """Explain 视角结构化解释包。"""
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await ai_service.build_explain_pack(
        db,
        skill_id,
        user_id=current_user.id,
        department=current_user.department,
    )
