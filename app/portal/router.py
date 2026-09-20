"""企业市场门户 API 路由"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.database import get_db
from app.portal import market_service, service as portal_service, ui_service as portal_ui_service

router = APIRouter()


class PortalSubmitRequest(BaseModel):
    params: dict | None = Field(default=None)
    ui_pref_id: str | None = Field(default=None)
    ui_pref_version: int | None = Field(default=None)
    ui_surface: str | None = Field(default="run_form")
    merged_ui_schema_hash: str | None = Field(default=None)


class PortalUIPreferenceRequest(BaseModel):
    overlay: dict | None = Field(default=None)
    surface: str | None = Field(default="run_form")
    generated_by: str | None = Field(default="manual")
    prompt_summary: str | None = Field(default=None, max_length=2000)


class PortalUIAIPreviewRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    surface: str | None = Field(default="run_form")
    current_overlay: dict | None = Field(default=None)


@router.get("/skills")
async def list_portal_skills(
    search: str | None = Query(None),
    category: str | None = Query(None),
    department: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_service.list_portal_skills(
        db,
        current_user,
        search=search,
        category=category,
        department=department,
        page=page,
        page_size=page_size,
    )


@router.get("/skills/{skill_id}")
async def get_portal_skill(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_service.get_portal_skill(db, skill_id, current_user)


@router.get("/skills/{skill_id}/ui")
async def get_portal_skill_ui(
    skill_id: str,
    surface: str = Query("run_form"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_ui_service.get_skill_ui(db, skill_id, current_user, surface)


@router.post("/skills/{skill_id}/ui/ai-preview")
async def preview_portal_skill_ui(
    skill_id: str,
    body: PortalUIAIPreviewRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_ui_service.preview_ai_ui(
        db,
        skill_id=skill_id,
        user=current_user,
        surface=body.surface,
        instruction=body.instruction,
        current_overlay=body.current_overlay,
    )


@router.put("/skills/{skill_id}/ui/preferences")
async def save_portal_skill_ui_preference(
    skill_id: str,
    body: PortalUIPreferenceRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_ui_service.save_ui_preference(
        db,
        skill_id=skill_id,
        user=current_user,
        surface=body.surface,
        overlay=body.overlay,
        generated_by=body.generated_by or "manual",
        prompt_summary=body.prompt_summary,
    )


@router.get("/skills/{skill_id}/ui/preferences")
async def list_portal_skill_ui_preferences(
    skill_id: str,
    surface: str = Query("run_form"),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_ui_service.list_ui_preferences(
        db,
        skill_id=skill_id,
        user=current_user,
        surface=surface,
        limit=limit,
    )


@router.post("/skills/{skill_id}/ui/preferences/{preference_id}/restore")
async def restore_portal_skill_ui_preference(
    skill_id: str,
    preference_id: str,
    surface: str = Query("run_form"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_ui_service.restore_ui_preference(
        db,
        skill_id=skill_id,
        user=current_user,
        preference_id=preference_id,
        surface=surface,
    )


@router.delete("/skills/{skill_id}/ui/preferences")
async def delete_portal_skill_ui_preference(
    skill_id: str,
    surface: str = Query("run_form"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_ui_service.delete_ui_preference(
        db,
        skill_id=skill_id,
        user=current_user,
        surface=surface,
    )


@router.post("/skills/{skill_id}/submit")
async def submit_portal_skill(
    skill_id: str,
    body: PortalSubmitRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_service.submit_portal_skill(
        db,
        skill_id,
        current_user,
        body.params,
        ui_pref_id=body.ui_pref_id,
        ui_pref_version=body.ui_pref_version,
        ui_surface=body.ui_surface,
        merged_ui_schema_hash=body.merged_ui_schema_hash,
    )


@router.get("/submissions")
async def list_my_submissions(
    status: str | None = Query(None),
    skill_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_service.list_my_submissions(
        db,
        current_user,
        status=status,
        skill_id=skill_id,
        page=page,
        page_size=page_size,
    )


@router.get("/submissions/{submission_id}")
async def get_submission_detail(
    submission_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_service.get_submission_detail(db, submission_id, current_user)


@router.get("/overview")
async def get_portal_overview(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await portal_service.get_portal_overview(db, current_user)


@router.get("/market")
async def list_market(
    category: str | None = Query(None, description="按部门/分类筛选"),
    certified_only: bool = Query(False, description="仅显示已认证 Skill"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """市场 Skill 列表，按用户权限分层过滤"""
    items = await market_service.list_market_skills(
        db, user=current_user, category=category, certified_only=certified_only,
    )
    return {"items": items, "total": len(items)}


@router.post("/market/{skill_id}/certify")
async def submit_certification(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """提交 Skill 认证申请"""
    return await market_service.submit_for_certification(db, skill_id, current_user.id)


@router.post("/market/certifications/{certification_id}/review")
async def review_certification(
    certification_id: int,
    body: dict,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """审核 Skill 认证申请（仅管理员/AI工程师）"""
    decision = body.get("decision")
    notes = body.get("notes")
    if not decision:
        from app.common.exceptions import AppError
        raise AppError("PARAM_INVALID", 400)
    return await market_service.review_certification(
        db, certification_id, current_user.id, decision, notes,
    )


@router.get("/market/{skill_id}/metrics")
async def reuse_metrics(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取 Skill 复用指标"""
    return await market_service.get_reuse_metrics(db, skill_id)


@router.put("/market/{skill_id}/visibility")
async def update_visibility(
    skill_id: str,
    body: dict,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """修改 Skill 市场可见性"""
    market_status = body.get("market_status")
    if not market_status:
        from app.common.exceptions import AppError
        raise AppError("PARAM_INVALID", 400)
    return await market_service.update_visibility(
        db, skill_id, market_status, current_user.id,
    )
