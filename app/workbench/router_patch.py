"""Workbench Patch 相关路由：创建/校验/应用 patch、部分应用。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role
from app.auth.models import User
from app.database import get_db
from app.skills.core.service_shared import ensure_skill_access
from app.workbench.schemas import (
    PartialApplyRequest,
    WorkbenchApplyRequest,
    WorkbenchApplyResponse,
    WorkbenchPatchRequest,
    WorkbenchPatchResponse,
    WorkbenchValidateRequest,
    WorkbenchValidateResponse,
)
from app.workbench.service import workbench_service

router = APIRouter()


async def _check_skill_department(skill_id: str, current_user: User, db: AsyncSession):
    """加载 Skill 并校验部门权限，供需要 skill_id 的端点复用。"""
    await ensure_skill_access(db, skill_id, current_user, "edit")


@router.post("/{skill_id}/workbench/patch", response_model=WorkbenchPatchResponse)
async def create_workbench_patch(
    skill_id: str,
    body: WorkbenchPatchRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """创建单模块 patch 草稿。"""
    await _check_skill_department(skill_id, current_user, db)
    return await workbench_service.create_patch(
        skill_id=skill_id,
        session_id=body.session_id,
        user_id=current_user.id,
        message=body.message,
        target_module=body.target_module,
        references=body.references,
    )


@router.post("/{skill_id}/workbench/validate", response_model=WorkbenchValidateResponse)
async def validate_workbench_patch(
    skill_id: str,
    body: WorkbenchValidateRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """校验 patch 草稿。"""
    await _check_skill_department(skill_id, current_user, db)
    return await workbench_service.validate_patch(
        skill_id=skill_id,
        patch_id=body.patch_id,
        user_id=current_user.id,
    )


@router.post("/{skill_id}/workbench/apply", response_model=WorkbenchApplyResponse)
async def apply_workbench_patch(
    skill_id: str,
    body: WorkbenchApplyRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """将 patch 草稿应用到目标 Skill。"""
    await _check_skill_department(skill_id, current_user, db)
    return await workbench_service.apply_patch(
        skill_id=skill_id,
        patch_id=body.patch_id,
        user_id=current_user.id,
    )


@router.post("/{skill_id}/workbench/apply-partial")
async def workbench_apply_partial(
    skill_id: str,
    body: PartialApplyRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """Hunk 级部分应用 Patch。"""
    await _check_skill_department(skill_id, current_user, db)

    if not body.accepted_hunks:
        return {
            "status": "partial_applied",
            "applied_hunks": [],
            "rejected_hunks": body.rejected_hunks,
            "git_commit": None,
        }

    result = await workbench_service.apply_partial_patch(
        skill_id=skill_id,
        patch_id=body.patch_id,
        accepted_hunks=body.accepted_hunks,
        rejected_hunks=body.rejected_hunks,
        user_id=current_user.id,
    )
    return result
