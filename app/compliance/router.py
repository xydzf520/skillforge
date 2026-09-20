"""合规规则API路由"""

from datetime import date

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.exceptions import AppError
from app.common.cache import cache_get, cache_set, cache_delete_pattern
from app.config import settings
from app.database import get_db
from app.compliance import service

router = APIRouter()


class CreateRuleRequest(BaseModel):
    """创建合规规则请求体"""
    id: str
    platform: str              # tmall/jd/douyin/pdd/all
    surface: str               # title/main_image/detail/video
    category_scope: str | None = None
    trigger_type: str          # keyword/regex/image_label
    pattern_value: str
    severity: str              # P0/P1/P2/P3
    decision: str              # block/rewrite/escalate/pass_with_log
    rewrite_suggestion: str | None = None
    required_evidence: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    source_url: str | None = None
    owner: str | None = None


class UpdateRuleRequest(BaseModel):
    """更新合规规则请求体"""
    platform: str | None = None
    surface: str | None = None
    category_scope: str | None = None
    trigger_type: str | None = None
    pattern_value: str | None = None
    severity: str | None = None
    decision: str | None = None
    rewrite_suggestion: str | None = None
    required_evidence: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    source_url: str | None = None
    owner: str | None = None


class CheckContentRequest(BaseModel):
    """内容合规检查请求体"""
    content: str
    platform: str | None = None


@router.get("/")
async def list_rules(
    platform: str | None = Query(None),
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """列出合规规则（分页+筛选）"""
    cache_key = f"compliance:list:{platform or 'all'}:{category or 'all'}:{page}:{page_size}"
    try:
        cached_val = await cache_get(cache_key)
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    result = await service.list_rules(
        db, platform=platform, category=category,
        page=page, page_size=page_size,
    )

    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_COMPLIANCE)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


@router.post("/import")
async def import_rules(
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """批量导入合规规则（CSV文件，仅admin）"""
    # 先检查 Content-Length / file.size 再读取，避免大文件耗尽内存
    if file.size and file.size > settings.MAX_UPLOAD_SIZE:
        raise AppError("FILE_TOO_LARGE", 413, detail={"max_mb": settings.MAX_UPLOAD_SIZE // (1024 * 1024)})
    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise AppError("FILE_TOO_LARGE", 413, detail={"max_mb": settings.MAX_UPLOAD_SIZE // (1024 * 1024)})
    result = await service.import_from_csv(db, content, current_user.id)
    await db.commit()

    try:
        await cache_delete_pattern("compliance:*")
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


@router.get("/export")
async def export_rules(
    platform: str | None = Query(None),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """导出合规规则为CSV文件（仅admin）"""
    csv_content = await service.export_csv(db, platform)
    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=compliance_rules.csv",
        },
    )


@router.get("/{rule_id}")
async def get_rule(
    rule_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取规则详情"""
    return await service.get_rule(db, rule_id)


@router.post("/")
async def create_rule(
    body: CreateRuleRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """创建合规规则（仅admin）"""
    result = await service.create_rule(
        db,
        rule_id=body.id,
        platform=body.platform,
        surface=body.surface,
        category_scope=body.category_scope,
        trigger_type=body.trigger_type,
        pattern_value=body.pattern_value,
        severity=body.severity,
        decision=body.decision,
        rewrite_suggestion=body.rewrite_suggestion,
        required_evidence=body.required_evidence,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        source_url=body.source_url,
        owner=body.owner,
        user_id=current_user.id,
    )

    try:
        await cache_delete_pattern("compliance:*")
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


@router.put("/{rule_id}")
async def update_rule(
    rule_id: str,
    body: UpdateRuleRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """更新合规规则（仅admin）"""
    fields = body.model_dump(exclude_none=True)
    result = await service.update_rule(
        db, rule_id, user_id=current_user.id, **fields,
    )

    try:
        await cache_delete_pattern("compliance:*")
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


@router.get("/{rule_id}/versions")
async def list_rule_versions(
    rule_id: str,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """规则版本历史（仅 admin）"""
    return await service.list_rule_versions(db, rule_id)


@router.post("/{rule_id}/rollback/{version_no}")
async def rollback_rule(
    rule_id: str,
    version_no: int,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """回滚到指定历史版本（会产生一个新的 version_no）"""
    result = await service.rollback_rule(db, rule_id, version_no, user_id=current_user.id)
    try:
        await cache_delete_pattern("compliance:*")
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    return result


@router.delete("/{rule_id}")
async def delete_rule(
    rule_id: str,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """删除合规规则（仅admin）"""
    result = await service.delete_rule(db, rule_id, user_id=current_user.id)

    try:
        await cache_delete_pattern("compliance:*")
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


@router.post("/check")
async def check_content(
    body: CheckContentRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """检查内容是否违反合规规则（任何登录用户）"""
    violations = await service.check_content(
        db, content=body.content, platform=body.platform,
    )
    return {
        "content_length": len(body.content),
        "platform": body.platform,
        "violation_count": len(violations),
        "violations": violations,
    }
