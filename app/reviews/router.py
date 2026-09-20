"""审核API路由"""

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_department_access, require_role, require_state_active
from app.auth.access import role_matches_any
from app.auth.models import User
from app.common.cache import cache_delete_pattern
from app.common.cache_facade import NamespaceCache
from app.common.exceptions import AppError
from app.config import settings
from app.database import get_db
from app.database import async_session_factory
from app.platform_settings import SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH_REASON, get_platform_security_settings
from app.reviews import service
from app.common.time_utils import now_bjt, parse_bjt_datetime

router = APIRouter()

REVIEW_LIST_CACHE = NamespaceCache("reviews:list", ttl=settings.CACHE_TTL_REVIEW)
REVIEW_DETAIL_CACHE = NamespaceCache("reviews:detail", ttl=settings.CACHE_TTL_REVIEW)
REVIEW_AI_CACHE = NamespaceCache("reviews:ai_review", ttl=3600)


def _is_review_admin(user: User) -> bool:
    return bool(user.can_view_all) or role_matches_any(user, ("admin", "ai_engineer"))


def _check_review_dept(review_data: dict, user: User):
    """
    审核部门隔离：非管理角色只能操作本部门Skill的审核。
    通过 skill_id 关联 skills 表查 department；如果 review_data 里已携带
    skill_department 字段则直接使用，否则通过 skill_id 前缀约定推断。
    """
    if _is_review_admin(user):
        return

    # 优先使用直接携带的部门信息
    skill_dept = review_data.get("skill_department") or review_data.get("department")
    if skill_dept:
        if skill_dept != user.department:
            raise AppError("REVIEW_PERMISSION_DENIED", 403, detail={"reason": "无权操作其他部门的审核"})
        return

    # 兜底：通过 skill_id 前缀判断（约定 "EC-" 前缀属于传统电商部门等）
    skill_id = review_data.get("skill_id", "")
    if skill_id.startswith("playbook:"):
        # Playbook 部门隔离：通过 YAML 中的 department 字段
        pb_dept = review_data.get("playbook_department", "")
        if pb_dept and pb_dept != user.department:
            raise AppError("REVIEW_PERMISSION_DENIED", 403, detail={"reason": "无权操作其他部门的Playbook审核"})
        return
    # 无法判断时放行（避免误拦截），但记录日志
    logger.warning("审核部门隔离: 无法确定 skill_id={} 的部门，放行", skill_id)


def _check_review_actor(
    review_data: dict,
    user: User,
    *,
    allow_submitter: bool = False,
    allow_reviewer: bool = False,
):
    """审核参与者约束。

    - admin / ai_engineer / can_view_all 直接放行
    - 详情 / 评论：允许提交人和指定 reviewer
    - 审批 / 驳回：仅允许指定 reviewer
    """
    if _is_review_admin(user):
        return

    submitter = review_data.get("submitter")
    reviewer = review_data.get("reviewer")

    if allow_submitter and submitter and submitter == user.id:
        return
    if allow_reviewer and reviewer and reviewer == user.id:
        return

    raise AppError("REVIEW_PERMISSION_DENIED", 403, detail={"reason": "无权操作不属于你的审核"})


class CreateReviewRequest(BaseModel):
    skill_id: str
    change_type: str = "update"  # params/logic/code/new_skill/update
    diff_summary: str = ""
    diff_content: dict | None = None
    reason: str = ""
    force_submit: bool = False
    force_reason: str = ""


class RejectRequest(BaseModel):
    reason: str = ""
    reject_reason: str = ""  # 驳回原因分类（如 quality/logic/format）


class ApproveRequest(BaseModel):
    rating: float | None = Field(None, ge=0, le=5)
    feedback_type: str = ""
    runtime_instance_id: str | None = Field(None, max_length=50)
    cron_expression: str | None = Field(None, max_length=100)
    verify_after_sync: bool = True
    static_check_override: bool = False
    static_check_override_reason: str = ""


class CommentRequest(BaseModel):
    content: str
    file_path: str | None = None
    line_number: int | None = None
    side: str | None = None  # "left" / "right"


@router.post("/")
async def create_review(
    body: CreateReviewRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "biz_owner", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """提交审核请求"""
    try:
        await cache_delete_pattern("reviews:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    security_settings = await get_platform_security_settings(db)
    review = await service.create_review(
        db,
        skill_id=body.skill_id,
        submitter=current_user.id,
        change_type=body.change_type,
        diff_summary=body.diff_summary,
        diff_content=body.diff_content,
        reason=body.reason,
        force_submit=body.force_submit or security_settings.bypass_review_direct_publish,
        force_reason=body.force_reason or (
            SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH_REASON
            if security_settings.bypass_review_direct_publish else ""
        ),
    )
    if security_settings.bypass_review_direct_publish and review.get("review_id"):
        await db.commit()
        async with async_session_factory() as publish_db:
            approve_result = await service.approve_review_as_system(
                publish_db,
                int(review["review_id"]),
                verify_after_sync=True,
            )
            await publish_db.commit()
        return {**review, "status": "approved", "auto_publish": {"ok": True}, "review": approve_result}
    return review


@router.get("/")
async def list_reviews(
    status: str | None = Query(None),
    skill_id: str | None = Query(None),
    q: str | None = Query(None, description="搜索 Skill ID / 摘要 / 提交人 / 审核人"),
    change_type: str | None = Query(None, description="变更类型：new_skill/update/fix 或历史精确类型"),
    reviewer: str | None = Query(None, description="审核人ID，传 'me' 表示当前用户"),
    submitter: str | None = Query(None, description="提交人ID，传 'me' 表示当前用户"),
    date_from: str | None = Query(None, description="开始日期 YYYY-MM-DD"),
    date_to: str | None = Query(None, description="结束日期 YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """列出审核请求（支持按审核人/提交人/日期筛选）"""
    # 部门隔离：非管理角色只看本部门
    department = None
    if not _is_review_admin(current_user):
        department = current_user.department

    # "me" 快捷方式 → 当前用户ID
    if reviewer == "me":
        reviewer = current_user.id
    if submitter == "me":
        submitter = current_user.id

    # 尝试读缓存
    try:
        cached = await REVIEW_LIST_CACHE.get(
            status or 'all',
            skill_id or 'all',
            q or 'all',
            change_type or 'all',
            reviewer or 'all',
            submitter or 'all',
            date_from or 'all',
            date_to or 'all',
            page,
            page_size,
            department or 'all',
        )
        if cached is not None:
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据库: {}", e)
    result = await service.list_reviews(db, status=status, skill_id=skill_id,
                                        q=q, change_type=change_type,
                                        reviewer=reviewer, submitter=submitter,
                                        date_from=date_from, date_to=date_to,
                                        page=page, page_size=page_size,
                                        department=department)
    try:
        await REVIEW_LIST_CACHE.set(
            status or 'all',
            skill_id or 'all',
            q or 'all',
            change_type or 'all',
            reviewer or 'all',
            submitter or 'all',
            date_from or 'all',
            date_to or 'all',
            page,
            page_size,
            department or 'all',
            value=result,
        )
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)
    return result


@router.get("/{review_id}")
async def get_review(
    review_id: int,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """审核详情（部门隔离）"""
    # 尝试读缓存
    try:
        cached = await REVIEW_DETAIL_CACHE.get(review_id)
        if cached is not None:
            _check_review_dept(cached, current_user)
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据库: {}", e)
    data = await service.get_review(db, review_id)
    _check_review_dept(data, current_user)
    _check_review_actor(data, current_user, allow_submitter=True, allow_reviewer=True)
    try:
        await REVIEW_DETAIL_CACHE.set(review_id, value=data)
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)
    return data


@router.post("/{review_id}/approve")
async def approve_review(
    review_id: int,
    body: ApproveRequest | None = None,
    current_user: User = Depends(require_role("admin", "ai_engineer", "biz_owner", "director")),
    db: AsyncSession = Depends(get_db),
):
    """审核通过（部门隔离）"""
    data = await service.get_review(db, review_id)
    _check_review_dept(data, current_user)
    _check_review_actor(data, current_user, allow_reviewer=True)
    try:
        await cache_delete_pattern("reviews:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    result = await service.approve_review(
        db,
        review_id,
        current_user.id,
        rating=body.rating if body else None,
        feedback_type=body.feedback_type if body else "",
        runtime_instance_id=body.runtime_instance_id if body else None,
        cron_expression=body.cron_expression if body else None,
        verify_after_sync=body.verify_after_sync if body else True,
        actor=current_user,
        static_check_override=body.static_check_override if body else False,
        static_check_override_reason=body.static_check_override_reason if body else "",
    )
    # §11 审批决策时间埋点
    try:
        from app.common.telemetry import record_review_decision_time
        if data.get("created_at"):
            created = data["created_at"]
            if isinstance(created, str):
                created = parse_bjt_datetime(created)
            elapsed = (now_bjt() - created).total_seconds()
            record_review_decision_time(elapsed, decision="approve")
    except Exception as e:
        logger.debug("review 审批耗时埋点失败 review_id={}: {}", review_id, e)
    return result


@router.post("/{review_id}/reject")
async def reject_review(
    review_id: int,
    body: RejectRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "biz_owner", "director")),
    db: AsyncSession = Depends(get_db),
):
    """审核驳回（部门隔离）"""
    data = await service.get_review(db, review_id)
    _check_review_dept(data, current_user)
    _check_review_actor(data, current_user, allow_reviewer=True)
    try:
        await cache_delete_pattern("reviews:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    result = await service.reject_review(
        db,
        review_id,
        current_user.id,
        body.reason,
        reject_reason=body.reject_reason,
    )
    try:
        from app.common.telemetry import record_review_decision_time
        if data.get("created_at"):
            created = data["created_at"]
            if isinstance(created, str):
                created = parse_bjt_datetime(created)
            elapsed = (now_bjt() - created).total_seconds()
            record_review_decision_time(elapsed, decision="reject")
    except Exception as e:
        logger.debug("review 驳回耗时埋点失败 review_id={}: {}", review_id, e)
    return result


@router.post("/{review_id}/comment")
async def add_comment(
    review_id: int,
    body: CommentRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """添加评论（需部门权限）"""
    data = await service.get_review(db, review_id)
    _check_review_dept(data, current_user)
    _check_review_actor(data, current_user, allow_submitter=True, allow_reviewer=True)
    try:
        await cache_delete_pattern("reviews:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return await service.add_comment(
        db, review_id, current_user.id, body.content,
        file_path=body.file_path, line_number=body.line_number, side=body.side,
    )


@router.post("/{review_id}/comments/{comment_id}/resolve")
async def resolve_comment(
    review_id: int,
    comment_id: int,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """C5: 标记评论为已解决（需部门权限）"""
    # 权限检查
    data = await service.get_review(db, review_id)
    _check_review_dept(data, current_user)
    _check_review_actor(data, current_user, allow_submitter=True, allow_reviewer=True)

    from sqlalchemy import select as sa_select, update as sa_update
    from app.reviews.models import ReviewComment
    from datetime import datetime

    result = await db.execute(
        sa_select(ReviewComment)
        .where(ReviewComment.id == comment_id)
        .where(ReviewComment.review_id == review_id)
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise AppError("REVIEW_NOT_FOUND", 404, {"detail": "评论不存在"})

    await db.execute(
        sa_update(ReviewComment)
        .where(ReviewComment.id == comment_id)
        .values(resolved=True, resolved_by=current_user.id, resolved_at=now_bjt())
    )
    await db.commit()
    return {"resolved": True, "comment_id": comment_id}


@router.post("/{review_id}/semantic-diff")
async def get_semantic_diff(
    review_id: int,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """语义化审核 diff：解析新旧版本的 SKILL.md，返回结构化变更列表"""
    from app.reviews.semantic_diff import compute_semantic_diff

    data = await service.get_review(db, review_id)
    _check_review_dept(data, current_user)
    _check_review_actor(data, current_user, allow_submitter=True, allow_reviewer=True)

    # 需要从 db 获取 review ORM 对象
    from sqlalchemy import select as sa_select
    from app.reviews.models import Review
    result = await db.execute(sa_select(Review).where(Review.id == review_id))
    review_obj = result.scalar_one_or_none()
    if not review_obj:
        raise AppError("REVIEW_NOT_FOUND", 404)

    diff_result = await compute_semantic_diff(review_obj)

    # B3: 增强 AI 语义审核
    try:
        from app.common.ai import call_llm_cached
        from app.skills.core.git_service import git_service
        cached_review = await REVIEW_AI_CACHE.get(review_id)
        if cached_review:
            diff_result["ai_review"] = cached_review
        else:
            skill_id = review_obj.skill_id
            # 使用 review 冻结的 commit，避免仓库后续变更导致 AI 审核针对错误版本
            commit_before = review_obj.git_commit_before or "HEAD~1"
            commit_after = review_obj.git_commit_after or "HEAD"
            old_md = git_service.get_file_at_commit(skill_id, "SKILL.md", commit_before) or ""
            new_md = git_service.get_file_at_commit(skill_id, "SKILL.md", commit_after) or ""

            if old_md and new_md:
                system = (
                    "你是Skill变更审核专家。从5个维度审核变更：阈值变动、逻辑覆盖、反例保护、数据依赖、合规风险。"
                    "输出纯JSON: {\"opinions\": [{\"severity\": \"critical/warning/info\", "
                    "\"dimension\": \"维度\", \"title\": \"标题\", \"detail\": \"详情\", "
                    "\"suggestion\": \"建议\"}], \"risk_score\": 1-10, \"summary\": \"总结\"}"
                )
                user_msg = (
                    f"## 变更前\n```\n{old_md[:3000]}\n```\n\n"
                    f"## 变更后\n```\n{new_md[:3000]}\n```\n\n"
                    "从阈值变动、逻辑覆盖、反例保护、数据依赖、合规风险5个维度审核。"
                )
                ai_review = await call_llm_cached(
                    f"reviews:ai_review:{review_id}", system, user_msg,
                    cache_ttl=3600, max_tokens=3000, timeout=60,
                )
                diff_result["ai_review"] = ai_review
            else:
                diff_result["ai_review"] = None
    except Exception as e:
        logger.warning("AI 审核失败，降级: {}", e)
        diff_result["ai_review"] = None

    return diff_result
