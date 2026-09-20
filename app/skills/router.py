"""Skill管理API路由"""

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from loguru import logger
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import role_matches_any
from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.cache import cache_get, cache_set, invalidate_skill
from app.common.cache_facade import NamespaceCache
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.config import settings
from app.database import get_db
from app.platform_settings import SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH_REASON, get_platform_security_settings
from app.skills.core.access import (
    SkillMember,
    get_skill_permissions,
    invalidate_skill_access_cache,
)
from app.skills.core.models import Skill
from app.skills.router_ai import router as ai_router
from app.skills.router_files import router as files_router
from app.skills.router_quality import router as quality_router
from app.skills.router_runtime import router as runtime_router
from app.skills.intelligence import generation_service
from app.skills.lifecycle import service, shadow_service
from app.skills.tooling import validation_service
from app.workbench import service as workbench_service_module
from app.skills.core.service_shared import ensure_skill_access

router = APIRouter()
router.include_router(ai_router)
router.include_router(files_router)
router.include_router(quality_router)
router.include_router(runtime_router)

from app.skills.router_assets import router as assets_router  # noqa: E402
router.include_router(assets_router)

from app.skills.router_hall import router as hall_router  # noqa: E402
router.include_router(hall_router)

from app.skills.router_admin import router as admin_router  # noqa: E402
router.include_router(admin_router)

SKILL_LIST_CACHE_VERSION = "v4-usage-today-view-counts"
SKILL_LIST_CACHE = NamespaceCache("skills:list", ttl=settings.CACHE_TTL_SKILL_LIST)


# ===== Hermes Agent 兼容 =====

@router.post("/{skill_id}/generate-hermes")
async def generate_hermes_compat(
    skill_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """为指定 Skill 生成 Hermes Agent 兼容文件（SKILL.hermes.md + CLI 入口）"""
    await ensure_skill_access(db, skill_id, current_user, "edit")
    from app.skills.integrations.hermes_adapter import generate_hermes_files
    return generate_hermes_files(skill_id)


# ===== 模板市场 =====

@router.get("/templates")
async def list_skill_templates(current_user: User = Depends(require_state_active)):
    """列出所有 Skill 模板"""
    from app.skills.template_market import list_templates
    return {"items": list_templates()}


@router.get("/templates/{template_id}")
async def get_skill_template(template_id: str, current_user: User = Depends(require_state_active)):
    """获取模板详情"""
    from app.skills.template_market import get_template_detail
    detail = get_template_detail(template_id)
    if not detail:
        raise AppError("TEMPLATE_NOT_FOUND", 404)
    return detail


class ForkTemplateRequest(BaseModel):
    skill_id: str
    department: str


@router.post("/templates/{template_id}/fork")
async def fork_skill_template(
    template_id: str,
    body: ForkTemplateRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """从模板 fork 创建新 Skill"""
    from app.skills.template_market import fork_template
    return await fork_template(db, template_id, body.skill_id, body.department, current_user.id)


class CreateSkillRequest(BaseModel):
    skill_id: str
    name: str
    department: str
    role: str = ""
    trigger_type: str = "manual"
    trigger_expression: str = ""
    risk_level: str = "R2"
    approval_level: int = 1
    skill_md: str = ""
    policy_pack: dict | None = None


class SkillMemberRequest(BaseModel):
    user_id: str
    role: str


class SkillVisibilityRequest(BaseModel):
    visibility: str


@router.get("/")
async def list_skills(
    department: str | None = Query(None),
    status: str | None = Query(None),
    include_deprecated: bool = Query(False),
    q: str | None = Query(None),
    risk_level: str | None = Query(None),
    trigger_type: str | None = Query(None),
    sort_by: str = Query("updated_at"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_health: bool = Query(False),
    view_filter: str | None = Query(None, description="all / mine_created / mine_owned / favorited / unhealthy"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """列出Skill（分页+搜索+筛选+排序+状态计数，部门隔离）

    include_health=true 时为每条记录附 health_score（0-100 / 1 位小数），
    用于前端 SkillList 的 HealthBar 列 + "不健康" sidebar 计数。
    通过 asyncio.gather 批量并行；列表级 Redis 缓存按该 flag 分桶。
    """
    include_deprecated_flag = include_deprecated is True
    include_health_flag = include_health is True
    scope = (
        f"user={current_user.id}|role={current_user.role}|dept={current_user.department}|"
        f"view_all={bool(current_user.can_view_all)}|perm_rev={getattr(current_user, 'permissions_rev', 0)}"
    )
    try:
        cached = await SKILL_LIST_CACHE.get(
            scope,
            department or "all",
            status or "all",
            "with-deprecated" if include_deprecated_flag else "without-deprecated",
            q or "",
            risk_level or "all",
            trigger_type or "all",
            sort_by,
            sort_order,
            page,
            page_size,
            "with-health" if include_health_flag else "no-health",
            view_filter or "all",
            SKILL_LIST_CACHE_VERSION,
        )
        if cached is not None:
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据库: {}", e)

    result = await service.list_skills(
        db,
        current_user=current_user,
        department=department,
        status=status,
        include_deprecated=include_deprecated_flag,
        q=q,
        risk_level=risk_level,
        trigger_type=trigger_type,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
        include_health=include_health_flag,
        view_filter=view_filter,
    )
    try:
        await SKILL_LIST_CACHE.set(
            scope,
            department or "all",
            status or "all",
            "with-deprecated" if include_deprecated_flag else "without-deprecated",
            q or "",
            risk_level or "all",
            trigger_type or "all",
            sort_by,
            sort_order,
            page,
            page_size,
            "with-health" if include_health_flag else "no-health",
            view_filter or "all",
            SKILL_LIST_CACHE_VERSION,
            value=result,
        )
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)
    return result


# ===== 置顶 =====

@router.post("/{skill_id}/pin")
async def pin_skill(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """置顶 Skill"""
    await ensure_skill_access(db, skill_id, current_user, "read")
    from app.skills.core.models import UserSkillPin
    from sqlalchemy import select as sa_select
    existing = await db.execute(
        sa_select(UserSkillPin)
        .where(UserSkillPin.user_id == current_user.id)
        .where(UserSkillPin.skill_id == skill_id)
    )
    if existing.scalar_one_or_none():
        return {"pinned": True}
    # 限制最多 10 个置顶
    from sqlalchemy import func
    count = (await db.execute(
        sa_select(func.count(UserSkillPin.id)).where(UserSkillPin.user_id == current_user.id)
    )).scalar() or 0
    if count >= 10:
        raise AppError("PARAM_INVALID", 400, {"detail": "最多置顶 10 个 Skill"})
    db.add(UserSkillPin(user_id=current_user.id, skill_id=skill_id))
    await db.commit()
    return {"pinned": True}


@router.delete("/{skill_id}/pin")
async def unpin_skill(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """取消置顶"""
    from app.skills.core.models import UserSkillPin
    from sqlalchemy import delete as sa_delete
    await db.execute(
        sa_delete(UserSkillPin)
        .where(UserSkillPin.user_id == current_user.id)
        .where(UserSkillPin.skill_id == skill_id)
    )
    await db.commit()
    return {"pinned": False}


@router.get("/pinned")
async def get_pinned_skills(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户置顶的 Skill 列表"""
    from app.skills.core.models import UserSkillPin, Skill
    from app.skills.core.access import build_skill_access_filter, get_skill_permissions
    from sqlalchemy import select as sa_select
    access_filter = await build_skill_access_filter(db, current_user, "read")
    result = await db.execute(
        sa_select(Skill)
        .join(UserSkillPin, UserSkillPin.skill_id == Skill.id)
        .where(UserSkillPin.user_id == current_user.id)
        .where(access_filter)
        .order_by(UserSkillPin.created_at.desc())
    )
    skills = result.scalars().all()
    items = []
    for s in skills:
        items.append(
            {"id": s.id, "name": s.name, "department": s.department,
             "status": s.status, "trigger_type": s.trigger_type,
             "risk_level": s.risk_level, "current_version": s.current_version,
             "visibility": s.visibility,
             "permissions": await get_skill_permissions(db, s, current_user),
             "updated_at": isoformat_bjt(s.updated_at)}
        )
    return items


# 注意：此路由必须在 /{skill_id} 之前声明，否则 "departments" 会被当作 skill_id 匹配
@router.get("/departments")
async def list_departments(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """返回现有 Skill 使用过的部门列表 + 数量（用于前端筛选下拉）。"""
    from sqlalchemy import func, select
    from app.skills.core.models import Skill
    from app.skills.core.access import build_skill_access_filter

    access_filter = await build_skill_access_filter(db, current_user, "read")
    stmt = (
        select(Skill.department, func.count(Skill.id).label("count"))
        .where(Skill.department.isnot(None))
        .where(access_filter)
        .group_by(Skill.department)
        .order_by(func.count(Skill.id).desc())
    )
    rows = (await db.execute(stmt)).all()
    return {
        "departments": [
            {"name": r[0], "skill_count": r[1]}
            for r in rows if r[0]
        ]
    }


@router.post("/generate-from-report")
async def generate_from_report(
    report: UploadFile = File(...),
    department: str = Form(""),
    role: str = Form(""),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """上传运营周报 → AI提取决策逻辑 → 生成SKILL.md初稿"""
    raw = await report.read()
    if len(raw) > settings.MAX_UPLOAD_SIZE:
        raise AppError("FILE_TOO_LARGE", 413, detail={"max_mb": settings.MAX_UPLOAD_SIZE // (1024 * 1024)})
    content = raw.decode("utf-8")
    return await generation_service.generate_from_report(content, department, role)


@router.get("/{skill_id}")
async def get_skill(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取Skill详情"""
    skill = await ensure_skill_access(db, skill_id, current_user, "read")
    data = await service.get_skill(db, skill_id)
    data["visibility"] = skill.visibility
    data["permissions"] = await get_skill_permissions(db, skill, current_user)
    return data


@router.get("/{skill_id}/bootstrap")
async def bootstrap_skill(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """v2.8.2 B4：Skill Studio 首屏一次性请求合并

    合并原先 4 个请求：
    - `GET /skills/{id}` — Skill meta
    - `GET /skills/{id}/files/SKILL.md` — 主文件内容
    - `GET /skills/{id}/lock` — 编辑锁状态
    - `HEAD` commit 信息（从 get_skill 已带）

    减少首屏 waterfall 等待。失败个别项不阻塞其他（用 try/except 降级）。
    """
    skill = await ensure_skill_access(db, skill_id, current_user, "read")

    # Skill meta
    skill_data = await service.get_skill(db, skill_id)
    skill_data["visibility"] = skill.visibility
    skill_data["permissions"] = await get_skill_permissions(db, skill, current_user)

    # 文件内容
    skill_md: str | None = None
    try:
        from app.skills.core.git_service import git_service

        skill_md = git_service.read_file(skill_id, "SKILL.md")
    except Exception as e:  # noqa: BLE001
        logger.warning("bootstrap read SKILL.md 失败 skill={}: {}", skill_id, e)

    # 编辑锁
    lock_info: dict | None = None
    try:
        from sqlalchemy import select as _sa_select

        from app.skills.core.models import SkillLock

        row = (
            await db.execute(
                _sa_select(SkillLock).where(SkillLock.skill_id == skill_id)
            )
        ).scalar_one_or_none()
        if row:
            lock_info = {
                "user_id": row.user_id,
                "locked_at": isoformat_bjt(row.locked_at),
                "self": row.user_id == current_user.id,
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("bootstrap lock 查询失败 skill={}: {}", skill_id, e)

    return {
        "skill": skill_data,
        "skill_md": skill_md,
        "lock": lock_info,
    }


@router.get("/{skill_id}/members")
async def list_skill_members(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    skill = await ensure_skill_access(db, skill_id, current_user, "read")
    from app.auth.models import User as UserModel

    rows = await db.execute(
        select(SkillMember, UserModel.name)
        .join(UserModel, UserModel.id == SkillMember.user_id, isouter=True)
        .where(SkillMember.skill_id == skill_id)
        .order_by(SkillMember.granted_at.asc())
    )
    return {
        "items": [
            {
                "user_id": member.user_id,
                "name": name or member.user_id,
                "role": member.role,
                "granted_by": member.granted_by,
                "granted_at": isoformat_bjt(member.granted_at),
            }
            for member, name in rows.all()
        ],
        "visibility": skill.visibility,
        "permissions": await get_skill_permissions(db, skill, current_user),
    }


_SKILL_MEMBER_ROLES = {"owner", "editor", "reviewer", "viewer"}
_SKILL_VISIBILITIES = {"company", "department", "private"}


async def _count_skill_owners(db: AsyncSession, skill_id: str) -> int:
    return int(
        (await db.execute(
            select(func.count())
            .select_from(SkillMember)
            .where(SkillMember.skill_id == skill_id, SkillMember.role == "owner")
        )).scalar()
        or 0
    )


async def _get_active_member_target(db: AsyncSession, user_id: str) -> User:
    target = await db.get(User, user_id)
    if not target:
        raise AppError("AUTH_USER_NOT_FOUND", 404)
    state = getattr(target, "state", None) or ("active" if target.is_active else "disabled")
    if state != "active" or not target.is_active:
        raise AppError("AUTH_ACCOUNT_NOT_ACTIVE", 403)
    return target


async def _after_skill_permission_change(
    db: AsyncSession,
    *,
    skill_id: str,
) -> None:
    await db.flush()
    await invalidate_skill_access_cache(skill_id)
    await invalidate_skill(skill_id)


@router.get("/{skill_id}/member-candidates")
async def list_skill_member_candidates(
    skill_id: str,
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """列出可添加到当前 Skill 的成员候选。"""
    skill = await ensure_skill_access(db, skill_id, current_user, "manage_members")

    stmt = select(User).where(User.is_active == True, User.state == "active")  # noqa: E712
    if not role_matches_any(current_user, ("admin",)):
        stmt = stmt.where(User.department == skill.department)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(or_(User.id.ilike(like), User.username.ilike(like), User.name.ilike(like)))
    stmt = stmt.order_by(User.name.asc(), User.id.asc()).limit(limit)
    users = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": user.role,
                "department": user.department,
            }
            for user in users
        ]
    }


@router.post("/{skill_id}/members")
async def upsert_skill_member(
    skill_id: str,
    body: SkillMemberRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """新增或更新 Skill 成员角色。"""
    await ensure_skill_access(db, skill_id, current_user, "manage_members")
    member_role = body.role.strip()
    if member_role not in _SKILL_MEMBER_ROLES:
        raise AppError("PARAM_INVALID", 400, detail={"field": "role"})
    user_id = body.user_id.strip()
    if not user_id:
        raise AppError("PARAM_INVALID", 400, detail={"field": "user_id"})
    await _get_active_member_target(db, user_id)

    existing = await db.get(SkillMember, (skill_id, user_id))
    if existing:
        if existing.role == "owner" and member_role != "owner" and await _count_skill_owners(db, skill_id) <= 1:
            raise AppError("SKILL_LAST_OWNER", 400)
        existing.role = member_role
        existing.granted_by = current_user.id
        existing.granted_at = now_bjt()
    else:
        db.add(SkillMember(
            skill_id=skill_id,
            user_id=user_id,
            role=member_role,
            granted_by=current_user.id,
            granted_at=now_bjt(),
        ))

    await _after_skill_permission_change(db, skill_id=skill_id)
    skill = await db.get(Skill, skill_id)
    return {
        "ok": True,
        "member": {"user_id": user_id, "role": member_role},
        "permissions": await get_skill_permissions(db, skill, current_user) if skill else {},
    }


@router.delete("/{skill_id}/members/{member_user_id}")
async def delete_skill_member(
    skill_id: str,
    member_user_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """移除 Skill 成员。"""
    await ensure_skill_access(db, skill_id, current_user, "manage_members")
    member = await db.get(SkillMember, (skill_id, member_user_id))
    if not member:
        raise AppError("SKILL_MEMBER_NOT_FOUND", 404)
    if member.role == "owner" and await _count_skill_owners(db, skill_id) <= 1:
        raise AppError("SKILL_LAST_OWNER", 400)
    await db.delete(member)
    await _after_skill_permission_change(db, skill_id=skill_id)
    return {"ok": True}


@router.put("/{skill_id}/visibility")
async def update_skill_visibility(
    skill_id: str,
    body: SkillVisibilityRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """更新 Skill 可见范围。"""
    skill = await ensure_skill_access(db, skill_id, current_user, "manage_members")
    next_visibility = body.visibility.strip()
    if next_visibility not in _SKILL_VISIBILITIES:
        raise AppError("PARAM_INVALID", 400, detail={"field": "visibility"})

    if next_visibility == "private" and await _count_skill_owners(db, skill_id) == 0:
        existing = await db.get(SkillMember, (skill_id, current_user.id))
        if existing:
            existing.role = "owner"
            existing.granted_by = current_user.id
            existing.granted_at = now_bjt()
        else:
            db.add(SkillMember(
                skill_id=skill_id,
                user_id=current_user.id,
                role="owner",
                granted_by=current_user.id,
                granted_at=now_bjt(),
            ))

    skill.visibility = next_visibility
    skill.updated_at = now_bjt()
    await _after_skill_permission_change(db, skill_id=skill_id)
    return {
        "ok": True,
        "visibility": skill.visibility,
        "permissions": await get_skill_permissions(db, skill, current_user),
    }


@router.post("/generate-preview")
async def generate_preview(
    body: dict,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """LLM 生成 SKILL.md 预览（不写入 Git）"""
    from app.skills.lifecycle.service import _generate_skill_with_llm
    name = body.get("name", "")
    department = body.get("department", "")
    role = body.get("role", "")
    skill_id = body.get("skill_id", name.lower().replace(" ", "-"))
    skill_md = await _generate_skill_with_llm(
        skill_id=skill_id, name=name, department=department,
        role=role, trigger_type=body.get("trigger_type", "manual"),
        trigger_expression="", risk_level=body.get("risk_level", "R2"),
    )
    return {"skill_md": skill_md}


@router.post("/{skill_id}/run-aiclaw")
async def run_on_aiclaw(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """在 AIClaw 节点执行 Skill。

    系统级执行必须走 bridge_script 直接运行 scripts/main.py；不再通过
    chat.send 让 Agent 生成执行结果，避免把样例当成真实运行。
    """
    await ensure_skill_access(db, skill_id, current_user, "execute")
    try:
        from app.execution.execution_service import execution_service

        return await execution_service.execute_skill(
            skill_id=skill_id,
            params={"_execution_backend": "bridge_script"},
            sandbox=True,
            triggered_by="manual_aiclaw",
            run_mode="sandbox_test",
        )
    except Exception as e:
        raise AppError("AICLAW_EXEC_FAILED", 502, {"detail": str(e)})


# v2.8.0: batch-create 限流：每用户每 60s 最多 10 条 Skill，防刷 LLM token
_batch_create_limiter = None


def _get_batch_create_limiter():
    global _batch_create_limiter
    if _batch_create_limiter is None:
        from app.common.rate_limiter import DistributedRateLimiter
        _batch_create_limiter = DistributedRateLimiter(
            scope="skill_batch_create", max_requests=10, window_seconds=60
        )
    return _batch_create_limiter


@router.post("/batch-create")
async def batch_create(
    body: dict,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """批量创建 Skill（并发调 LLM）"""
    import asyncio
    skills_input = body.get("skills", [])
    if not skills_input:
        raise AppError("INVALID_INPUT", 400, {"detail": "skills 数组为空"})

    # v2.8.0: 单次调用数量上限（防超大 payload）
    if len(skills_input) > 50:
        raise AppError(
            "PARAM_INVALID", 400,
            {"detail": "单次 batch-create 最多 50 条"},
        )

    # v2.8.0: 每用户 60 秒最多创建 10 条 Skill
    limiter = _get_batch_create_limiter()
    allowed, info = await limiter.check(current_user.id)
    if not allowed:
        raise AppError(
            "RATE_LIMITED", 429,
            {
                "detail": f"batch-create 频率过高，{info.get('retry_after', 0):.0f} 秒后重试",
                "retry_after": info.get("retry_after"),
                "limit": info.get("limit"),
            },
        )

    sem = asyncio.Semaphore(3)  # 限制 3 并发
    created = []
    failed = []

    async def create_one(item):
        async with sem:
            try:
                result = await service.create_skill(
                    db,
                    skill_id=item["skill_id"],
                    name=item.get("name", item["skill_id"]),
                    department=item.get("department", "AI"),
                    role=item.get("role", ""),
                    trigger_type=item.get("trigger_type", "manual"),
                    risk_level=item.get("risk_level", "R2"),
                    approval_level=item.get("approval_level", 0),
                    user_id=current_user.id,
                )
                created.append(result)
            except Exception as e:
                failed.append({"skill_id": item.get("skill_id"), "error": str(e)})

    await asyncio.gather(*[create_one(item) for item in skills_input])
    return {"created": created, "failed": failed, "total": len(skills_input)}


@router.post("/")
async def create_skill(
    body: CreateSkillRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """新建Skill（**裸接口，脚本用**；用户路径请走 `/workbench/create-skill`）"""
    result = await service.create_skill(
        db,
        skill_id=body.skill_id,
        name=body.name,
        department=body.department,
        role=body.role,
        trigger_type=body.trigger_type,
        trigger_expression=body.trigger_expression,
        risk_level=body.risk_level,
        approval_level=body.approval_level,
        skill_md=body.skill_md,
        policy_pack=body.policy_pack,
        user_id=current_user.id,
    )
    try:
        await invalidate_skill(body.skill_id)
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


# v2.8.2 D2：统一创建入口 —— body.source.type 决定走哪条子流程。
# 六种 source：scratch / template / fork / import / report / architect
# 对用户隐藏底层分支，集中做权限 / 审计 / 限流 / telemetry。
class SkillSourceDescriptor(BaseModel):
    type: str  # scratch | template | fork | import | report | architect
    # 各分支按需取字段（template_id / parent_skill_id / skill_md 等），统一 Dict 透传
    payload: dict = {}


class UnifiedCreateRequest(BaseModel):
    source: SkillSourceDescriptor
    # 公共字段（被 source.payload 覆盖时以 payload 为准）
    name: str = ""
    department: str = ""
    skill_id: str = ""
    risk_level: str = "R2"


@router.post("/create")
async def unified_create_skill(
    body: UnifiedCreateRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """v2.8.2 D2：统一创建入口，body.source.type 分流到底层端点。

    支持的 source.type：

    - `scratch`：对应老 `POST /skills/` 裸创建
    - `template`：`payload.template_id` → `POST /skills/templates/{id}/fork`
    - `fork`：`payload.parent_skill_id` → `POST /skills/{id}/fork`
    - `architect`：`payload.skill_dict` → `POST /skills/workbench/create-skill`
    - `import`：400，让用户走独立的 multipart `POST /skills/import`
    - `report`：400，让用户走独立的 multipart `POST /skills/generate-from-report`

    文档：`docs/spec/skill-creation-paths.md`。
    """
    from app.common.telemetry import record_creation_step

    src_type = body.source.type
    payload = body.source.payload or {}

    record_creation_step("commit", source=src_type)

    if src_type == "scratch":
        req = CreateSkillRequest(
            skill_id=body.skill_id or payload.get("skill_id") or "",
            name=body.name or payload.get("name") or "",
            department=body.department or payload.get("department") or "",
            role=payload.get("role", ""),
            trigger_type=payload.get("trigger_type", "manual"),
            trigger_expression=payload.get("trigger_expression", ""),
            risk_level=body.risk_level or payload.get("risk_level", "R2"),
            approval_level=payload.get("approval_level", 1),
            skill_md=payload.get("skill_md", ""),
            policy_pack=payload.get("policy_pack"),
        )
        return await create_skill(req, current_user=current_user, db=db)

    if src_type == "template":
        template_id = payload.get("template_id")
        if not template_id:
            raise AppError("PARAM_INVALID", 400, {"detail": "template source requires payload.template_id"})
        from app.skills.template_market import fork_template
        return await fork_template(
            db, str(template_id),
            body.skill_id or payload.get("new_skill_id") or "",
            body.department or payload.get("department") or current_user.department or "",
            current_user.id,
        )

    if src_type == "fork":
        parent_id = payload.get("parent_skill_id")
        if not parent_id:
            raise AppError("PARAM_INVALID", 400, {"detail": "fork source requires payload.parent_skill_id"})
        from app.skills.asset_service import fork_skill as _fork_skill
        # 若未指定 new_skill_id，后端生成 fork-{parent}-{uuid6}
        from app.skills.core.id_gen import gen_from_fork
        new_id = body.skill_id or payload.get("new_skill_id") or gen_from_fork(str(parent_id))
        return await _fork_skill(
            db, str(parent_id),
            new_skill_id=new_id,
            department=body.department or payload.get("department") or current_user.department or "",
            user_id=current_user.id,
        )

    if src_type == "architect":
        skill_dict = payload.get("skill_dict") or payload.get("skill")
        if not skill_dict:
            raise AppError("PARAM_INVALID", 400, {"detail": "architect source requires payload.skill_dict"})
        from app.workbench.schemas import SkillStructure
        try:
            draft = SkillStructure.model_validate(skill_dict)
        except ValidationError as e:
            raise AppError(
                "PARAM_INVALID", 400,
                {
                    "detail": "architect source payload.skill_dict 格式不合法",
                    "errors": e.errors(),
                },
            ) from e
        return await workbench_service_module.workbench_service.create_skill_from_draft(
            draft, current_user.id, body.skill_id or payload.get("skill_id") or None,
        )

    if src_type in ("import", "report"):
        raise AppError(
            "USE_DEDICATED_ENDPOINT", 400,
            {
                "detail": f"{src_type} source 需 multipart 上传，请用 POST /skills/import 或 /skills/generate-from-report",
            },
        )

    raise AppError("PARAM_INVALID", 400, {"detail": f"未知的 source.type: {src_type!r}"})


@router.post("/scan-repo")
async def scan_repo(
    dry_run: bool = Query(False, description="只列出不写入"),
    skill_id: list[str] | None = Query(None, description="可选：只扫描指定 Skill ID，可重复传"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
    x_confirm: str | None = Header(None, alias="X-Confirm"),
):
    """
    扫描 skills-repo 目录，把文件存在但 DB 没记录的 Skill 自动注册。
    并自动 git commit 未跟踪的文件，确保 git 历史完整。

    v2.8.0：
    - dry_run 允许 admin/system_admin/ai_engineer
    - 非 dry_run 模式要求 admin/system_admin + X-Confirm header 二次确认
    - 每次 scan-repo 写 audit log

    场景：用户手动上传 Skill 目录到 skills-repo（或从其他仓库 git clone 进来），
    需要一键同步到平台 DB。

    返回 {found, imported[], skipped[], errors[], commit_sha, dry_run}
    """
    allowed_roles = {"admin", "system_admin"} if not dry_run else {"admin", "system_admin", "ai_engineer"}
    if getattr(current_user, "role", "") not in allowed_roles:
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    # v2.8.0: 非 dry_run 要 X-Confirm: I-UNDERSTAND header，防止误触
    if not dry_run and x_confirm != "I-UNDERSTAND":
        raise AppError(
            "CONFIRM_REQUIRED", 400,
            {
                "detail": "scan-repo 实际写入需要请求头 X-Confirm: I-UNDERSTAND",
                "hint": "先用 dry_run=true 看会写什么，再传 header 正式执行",
            },
        )

    result = await service.scan_repo(
        db,
        user_id=current_user.id,
        dry_run=dry_run,
        skill_ids=skill_id,
    )

    # v2.8.0: 写审计
    try:
        from app.common.audit import audit
        await audit.log(
            user_id=current_user.id,
            action="skill.scan_repo",
            detail={
                "dry_run": dry_run,
                "found": result.get("found"),
                "imported_count": len(result.get("imported") or []),
                "skipped_count": len(result.get("skipped") or []),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("scan-repo audit 失败: {}", e)
    if result["imported"] and not dry_run:
        try:
            from app.common.cache import cache_delete_pattern
            await cache_delete_pattern("skills:*")
        except Exception as e:
            logger.warning("缓存失效失败: {}", e)
    return result


# ===== 决策树 Mermaid 可视化 =====

@router.get("/{skill_id}/mermaid")
async def get_skill_mermaid(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """将 SKILL.md 决策树渲染为 Mermaid 流程图语法"""
    # 部门权限检查
    await ensure_skill_access(db, skill_id, current_user, "read")

    from app.skills.core.git_service import git_service
    from app.skills.core.parser import skill_parser
    from app.common.mermaid import steps_to_mermaid, build_step_line_map

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        raise AppError("SKILL_NOT_FOUND", 404)

    parsed_data = skill_parser.parse(skill_md)
    mermaid_code = steps_to_mermaid(parsed_data.steps)
    line_map = build_step_line_map(skill_md, parsed_data.steps)

    return {
        "mermaid": mermaid_code,
        "line_map": line_map,
        "step_count": len(parsed_data.steps),
        "branch_count": sum(len(s.branches) for s in parsed_data.steps),
    }


# ===== P1: 测试用例自动生成 =====

class GenerateTestsRequest(BaseModel):
    include_edge_cases: bool = True
    strategy: str = "branch_coverage"  # branch_coverage/mutation/boundary/adversarial/comprehensive
    # 用历史 decision_log 回填 __TODO__ 占位符（默认开启，让生成结果开箱即用）
    auto_fill_from_history: bool = True


class RegressionCaseRequest(BaseModel):
    case: dict
    case_id: str | None = None
    reason: str = ""
    override_static_check: bool = False
    override_reason: str = ""


@router.post("/{skill_id}/generate-tests")
async def generate_tests(
    skill_id: str,
    body: GenerateTestsRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """解析决策树分支，自动生成骨架测试用例（B7: 支持多种策略）"""
    from app.skills.core.git_service import git_service
    from app.skills.core.parser import skill_parser
    from app.skills.tooling.test_generation import (
        auto_fill_test_inputs,
        generate_test_cases,
        generate_test_cases_ai,
    )

    await ensure_skill_access(db, skill_id, current_user, "read")

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        raise AppError("SKILL_NOT_FOUND", 404)

    parsed_data = skill_parser.parse(skill_md)

    if body.strategy == "branch_coverage":
        cases = generate_test_cases(parsed_data, body.include_edge_cases)
    else:
        cases = await generate_test_cases_ai(parsed_data, body.strategy)

    # 回填 __TODO__ 占位符（用真实历史样本，让生成结果可直接执行）
    autofill_stats = None
    if body.auto_fill_from_history and cases:
        try:
            autofill_stats = await auto_fill_test_inputs(db, skill_id, cases)
        except Exception as e:
            logger.warning(f"自动回填测试输入失败 skill={skill_id}: {e}")
            autofill_stats = {"error": str(e), "filled_count": 0, "samples_used": 0, "unfilled_keys": []}

    return {
        "skill_id": skill_id,
        "strategy": body.strategy,
        "total_paths": len([c for c in cases if not c.get("is_edge_case") and not c.get("strategy")]),
        "total_edge_cases": len([c for c in cases if c.get("is_edge_case")]),
        "total_ai_generated": len([c for c in cases if c.get("strategy")]),
        "test_cases": cases,
        "autofill": autofill_stats,
    }


# ═══════════════════════════════════════════════════════
# 回归 diff（保存后看改动影响）
# ═══════════════════════════════════════════════════════

@router.post("/{skill_id}/regression-cases")
async def add_regression_case(
    skill_id: str,
    body: RegressionCaseRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp", "biz_owner")),
    db: AsyncSession = Depends(get_db),
):
    """写入回归用例到 Skill Git，并自动创建审核。"""
    from app.reviews import service as review_service
    from app.reviews.regression_cases import build_regression_case_artifact
    from app.skills.core.git_service import git_service

    await ensure_skill_access(db, skill_id, current_user, "edit")

    static_detection = await review_service.enforce_static_detection_gate(
        skill_id=skill_id,
        actor_id=current_user.id,
        target_type="skill",
        target_id=skill_id,
        source="regression_cases_write",
        override=body.override_static_check,
        override_reason=body.override_reason,
    )

    artifact = build_regression_case_artifact(
        body.case,
        case_id=body.case_id,
        actor_id=current_user.id,
    )
    async with git_service.skill_advisory_lock(skill_id):
        if git_service.read_file(skill_id, artifact.path) is not None:
            raise AppError("PARAM_INVALID", 409, {"detail": f"回归用例已存在: {artifact.path}"})
        git_service.write_file(skill_id, artifact.path, artifact.content)
        commit = git_service.commit_all(
            f"regression: add case {artifact.case_id}",
            current_user.id,
            paths=[f"{skill_id}/{artifact.path}"],
            validate=False,
        )

    try:
        await invalidate_skill(skill_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("回归用例写入后缓存失效失败 skill={}: {}", skill_id, exc)

    diff_content = {
        "reason": "regression_case_add",
        "regression_case": {
            "case_id": artifact.case_id,
            "path": artifact.path,
            "commit": commit,
        },
        "static_detection": {
            "passed": static_detection.get("passed"),
            "finding_count": static_detection.get("finding_count", 0),
            "override": bool(body.override_static_check),
        },
    }
    review_reason = body.reason or f"新增回归用例 {artifact.case_id}"
    security_settings = await get_platform_security_settings(db)

    async def _create_review(*, force_submit: bool = False, force_reason: str = "") -> dict:
        return await review_service.create_review(
            db,
            skill_id=skill_id,
            submitter=current_user.id,
            change_type="regression_case",
            diff_summary=f"新增回归用例 {artifact.case_id}",
            diff_content=diff_content,
            reason=review_reason,
            force_submit=force_submit,
            force_reason=force_reason,
        )

    try:
        review = await _create_review(
            force_submit=security_settings.bypass_review_direct_publish,
            force_reason=(
                SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH_REASON
                if security_settings.bypass_review_direct_publish else ""
            ),
        )
    except AppError as exc:
        gate_failed = (
            exc.code == "PARAM_INVALID"
            and isinstance(exc.detail, dict)
            and exc.detail.get("reason") == "提交审核 gate 未通过"
        )
        if not gate_failed:
            raise
        review = await _create_review(
            force_submit=True,
            force_reason="回归用例自动创建审核，发布前仍需完整门禁与人工审核",
        )

    approve_result = None
    if security_settings.bypass_review_direct_publish and review.get("review_id"):
        approve_result = await review_service.approve_review_as_system(
            db,
            int(review["review_id"]),
            verify_after_sync=True,
        )

    response = {
        "skill_id": skill_id,
        "case_id": artifact.case_id,
        "path": artifact.path,
        "commit": commit,
        "review": review,
        "static_detection": static_detection,
    }
    if approve_result is not None:
        response.update(
            {
                "status": "approved",
                "auto_publish": {"ok": True},
                "publish_review": approve_result,
            }
        )
    return response


@router.get("/{skill_id}/regression-diff")
async def regression_diff_endpoint(
    skill_id: str,
    against: str = Query("HEAD~1", description="对比的 base 版本，默认上一次 commit"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """改动后的回归 diff：当前 SKILL.md vs 历史 commit 的结构化对比。

    返回每条变化的分支以及它会影响哪些测试用例。
    """
    from app.skills.lifecycle.regression_diff import compute_regression_diff

    await ensure_skill_access(db, skill_id, current_user, "read")

    try:
        diff = compute_regression_diff(skill_id, base_ref=against)
    except Exception as e:
        logger.exception(f"回归 diff 计算失败 skill={skill_id}: {e}")
        raise AppError("REGRESSION_DIFF_FAILED", 500, {"detail": str(e)[:200]})

    return diff.to_dict()


# ═══════════════════════════════════════════════════════
# 参数反向引用（改阈值的下游影响）
# ═══════════════════════════════════════════════════════

@router.get("/params/{param_name}/usages")
async def param_usages_endpoint(
    param_name: str,
    exclude_skill: str | None = Query(None, description="排除某个 Skill"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """查询某个参数 / metric 在全库的所有引用位置。用途：改阈值前看下游影响。"""
    # [H7] 业务逻辑下沉到 service.get_param_usages_grouped
    return await service.get_param_usages_grouped(
        db, param_name, current_user=current_user, exclude_skill=exclude_skill,
    )


@router.post("/params/index/refresh")
async def refresh_param_index(
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """主动刷新参数倒排索引（编辑器保存后或定期任务）。"""
    from app.skills.intelligence.param_index import get_param_index, invalidate_param_index

    await invalidate_param_index()
    index = await get_param_index(db, force=True)
    return {
        "ok": True,
        "param_count": len(index),
        "total_usages": sum(len(v) for v in index.values()),
    }


# ═══════════════════════════════════════════════════════
# 跨 Skill 规则冲突
# ═══════════════════════════════════════════════════════

@router.get("/conflicts")
async def cross_skill_conflicts_endpoint(
    department: str | None = Query(None, description="只查某部门内的冲突"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """全库扫描跨 Skill 规则冲突。

    冲突定义：两个 active/shadow Skill 对同一 metric 的相交区间给出相反极性的结论。
    """
    # [H7] 业务逻辑下沉到 service.get_cross_skill_conflicts_scoped
    return await service.get_cross_skill_conflicts_scoped(
        db, current_user=current_user, department=department,
    )


@router.get("/{skill_id}/conflicts")
async def skill_conflicts_endpoint(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """查询某个 Skill 涉及的跨 Skill 冲突。"""
    # [H7] 业务逻辑下沉到 service.get_skill_conflicts_with_access
    conflicts = await service.get_skill_conflicts_with_access(
        db, skill_id, current_user=current_user,
    )
    return {
        "skill_id": skill_id,
        "total": len(conflicts),
        "conflicts": conflicts,
    }


# ===== P3: 缺失端点补齐 =====


class ValidateAntipatternRequest(BaseModel):
    scenario: str
    expected_action: str


@router.post("/{skill_id}/validate-antipattern")
async def validate_antipattern(
    skill_id: str,
    body: ValidateAntipatternRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """验证单条反例：检查 Skill 是否能正确处理该场景"""
    from app.skills.core.git_service import git_service
    from app.skills.core.parser import skill_parser

    await ensure_skill_access(db, skill_id, current_user, "read")

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        raise AppError("SKILL_NOT_FOUND", 404)

    parsed = skill_parser.parse(skill_md)
    # 检查该反例场景是否已在反例列表中
    existing = [a for a in parsed.antipatterns if a.scenario == body.scenario]
    # 检查决策步骤中是否有覆盖此场景的条件
    covered_branches = []
    for step in parsed.steps:
        for branch in step.branches:
            if body.scenario.lower() in branch.condition.lower():
                covered_branches.append({"step": step.id, "condition": branch.condition})

    return {
        "scenario": body.scenario,
        "already_listed": len(existing) > 0,
        "covered_by_branches": covered_branches,
        "recommendation": "已有覆盖" if covered_branches else "建议补充决策分支处理此场景",
    }


@router.post("/{skill_id}/preview-output")
async def preview_output(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """输出预览：展示 Skill 的输出定义 + 钉钉卡片模板预览"""
    import json
    from app.skills.core.git_service import git_service
    from app.skills.core.parser import skill_parser
    from app.dingtalk.card_templates import build_execution_report
    from app.sandbox.executor import run_preview

    await ensure_skill_access(db, skill_id, current_user, "read")

    contract_raw = git_service.read_file(skill_id, "task-contract.json")
    if contract_raw:
        try:
            contract = json.loads(contract_raw)
            preview = run_preview(contract)
            # task_gate 来自 task-review-state.json，不在 contract 里；读不到就 None
            task_gate = None
            review_state_raw = git_service.read_file(skill_id, "task-review-state.json")
            if review_state_raw:
                try:
                    review_state = json.loads(review_state_raw)
                    if isinstance(review_state, dict):
                        task_gate = review_state.get("gate")
                except json.JSONDecodeError:
                    pass
            return {
                "output_definition": (contract.get("output") or {}).get("schema") or {},
                "card_preview": preview.get("card_payload") or {},
                "rendered_output": preview.get("rendered_output") or "",
                "task_gate": task_gate,
            }
        except json.JSONDecodeError:
            pass

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    parsed = skill_parser.parse(skill_md) if skill_md else None

    output_def = [{"field": o.field, "label": o.label, "format": o.format}
                  for o in parsed.output_definition] if parsed else []

    # 生成样例钉钉卡片 — 用 frontmatter.name 或回退到 skill_id
    sample_output = {o.field: f"<{o.label}示例值>" for o in parsed.output_definition} if parsed else {}
    skill_name = (parsed.frontmatter.get("name") if parsed else None) or skill_id
    card_preview = build_execution_report(skill_name, "preview", str(sample_output), 0)

    return {
        "output_definition": output_def,
        "card_preview": card_preview,
        "rendered_output": card_preview.get("markdown") if isinstance(card_preview, dict) else str(card_preview),
    }


class RunSkillDirectRequest(BaseModel):
    params: dict = {}
    sandbox: bool = True  # 默认沙箱模式，防止误触生产执行
