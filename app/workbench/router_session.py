"""Workbench 会话相关路由：创建会话、获取会话、意图分析。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role
from app.auth.models import User
from app.database import get_db
from app.skills.core.service_shared import ensure_skill_access
from app.workbench.schemas import (
    WorkbenchIntentRequest,
    WorkbenchIntentResponse,
    WorkbenchSessionCreateRequest,
    WorkbenchSessionResponse,
)
from app.workbench.service import workbench_service

router = APIRouter()


async def _check_skill_department(skill_id: str, current_user: User, db: AsyncSession):
    """加载 Skill 并校验部门权限，供需要 skill_id 的端点复用。"""
    await ensure_skill_access(db, skill_id, current_user, "read")


@router.post("/{skill_id}/workbench/session", response_model=WorkbenchSessionResponse)
async def create_workbench_session(
    skill_id: str,
    body: WorkbenchSessionCreateRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """为某个 Skill 创建 workbench 会话。"""
    await _check_skill_department(skill_id, current_user, db)
    return await workbench_service.create_session(
        skill_id=skill_id,
        user_id=current_user.id,
        mode=body.mode,
        active_module=body.active_module,
    )


@router.get("/{skill_id}/workbench/session/{session_id}", response_model=WorkbenchSessionResponse)
async def get_workbench_session(
    skill_id: str,
    session_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户的 workbench 会话。"""
    await _check_skill_department(skill_id, current_user, db)
    return await workbench_service.get_session(
        skill_id=skill_id,
        session_id=session_id,
        user_id=current_user.id,
    )


@router.get("/{skill_id}/workbench/coding/timeline")
async def get_coding_timeline(
    skill_id: str,
    limit: int = Query(80, ge=1, le=200),
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """读取当前用户在该 Skill 下的 Coding Agent 可观测时间线。"""
    await _check_skill_department(skill_id, current_user, db)
    items = await workbench_service.load_coding_timeline(
        skill_id=skill_id,
        user_id=current_user.id,
        limit=limit,
    )
    return {"items": items}


@router.get("/{skill_id}/workbench/coding/turns")
async def get_recent_ai_turns(
    skill_id: str,
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """读取当前用户在该 Skill 下最近的 AI 修改回合。"""
    await _check_skill_department(skill_id, current_user, db)
    items = await workbench_service.load_recent_ai_turns(
        skill_id=skill_id,
        user_id=current_user.id,
        limit=limit,
    )
    return {"items": items}


@router.post("/{skill_id}/workbench/intent", response_model=WorkbenchIntentResponse)
async def parse_workbench_intent(
    skill_id: str,
    body: WorkbenchIntentRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """启发式判断用户消息的目标模块和意图。"""
    await _check_skill_department(skill_id, current_user, db)
    return await workbench_service.analyze_intent(
        skill_id=skill_id,
        session_id=body.session_id,
        user_id=current_user.id,
        message=body.message,
        active_module=body.active_module,
    )
