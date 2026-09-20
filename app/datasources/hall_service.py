"""数据源大厅服务（v2.7 大厅 v3 · 阶段 1）。

跨部门浏览公司可复用**数据能力**的入口。复制 `app/skills/hall_service.py` 的三级可见性模型：

- `visibility='company'`  ：所有登录用户可见
- `visibility='department'`：本部门 + 组织树内可见
- `visibility='private'`  ：仅 DataAccessGrant 名单 + owner_contact 可见
- admin / can_view_all   ：豁免全见

注意：此 service 只做**元数据**发现，实际 row-level 数据访问仍走 `DataAccessGrant`（在 preview/download 端点校验）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import expand_org_lineage, role_matches_any
from app.auth.models import User
from app.datasources.models import DataAccessGrant, DataAccessRequest, DataSource
from app.org.models import OrgUnit, UserOrgMembership
from app.skills.core.access import build_skill_access_filter, get_skill_permissions
from app.skills.core.models import Skill
from app.common.time_utils import isoformat_bjt, now_bjt


def _is_privileged(user: User) -> bool:
    """admin / can_view_all 豁免可见性过滤。"""
    return role_matches_any(user, ("admin",)) or getattr(user, "can_view_all", False)


async def _get_user_org_ids(db: AsyncSession, user_id: str) -> list[str]:
    result = await db.execute(
        select(UserOrgMembership.org_unit_id).where(UserOrgMembership.user_id == user_id)
    )
    return [row[0] for row in result.all()]


async def _get_user_visible_departments(
    db: AsyncSession, user_id: str
) -> list[str]:
    """将用户 org membership 展开成可用于 DataSource.department 匹配的展示名集合。"""
    user_org_ids = set(await _get_user_org_ids(db, user_id))
    if not user_org_ids:
        return []

    expanded_org_ids = await expand_org_lineage(db, user_org_ids)
    if not expanded_org_ids:
        return []

    result = await db.execute(
        select(OrgUnit.name).where(OrgUnit.id.in_(expanded_org_ids))
    )
    return sorted({name for name in result.scalars().all() if name})


async def _get_user_grant_source_ids(db: AsyncSession, user_id: str) -> list[str]:
    """用户已被 grant 的 source_id 列表（grantee_user_id 直授；不考虑 org_unit 间接授权 v1）。"""
    now = now_bjt()
    result = await db.execute(
        select(DataAccessGrant.source_id).where(
            DataAccessGrant.grantee_user_id == user_id,
            or_(DataAccessGrant.expires_at.is_(None), DataAccessGrant.expires_at > now),
        )
    )
    return list({row[0] for row in result.all()})


async def _get_user_pending_source_ids(db: AsyncSession, user_id: str) -> list[str]:
    """用户有 pending 申请的 source_id 列表（卡片 my_access='pending' 状态标记用）。"""
    result = await db.execute(
        select(DataAccessRequest.source_id).where(
            DataAccessRequest.requester_id == user_id,
            DataAccessRequest.status == "pending",
        )
    )
    return list({row[0] for row in result.all()})


def _build_visibility_filter(
    user: User, visible_departments: list[str], grant_source_ids: list[str]
):
    """三级可发现性 WHERE 条件。"""
    conditions = [DataSource.visibility == "company", DataSource.owner_contact == user.id]

    dept_names = {name for name in visible_departments if name}
    if user.department:
        dept_names.add(user.department)
    if dept_names:
        conditions.append(
            and_(
                DataSource.visibility == "department",
                DataSource.department.in_(sorted(dept_names)),
            )
        )
    if grant_source_ids:
        # 已被 grant 的一律可发现（即使 visibility=private）
        conditions.append(DataSource.id.in_(grant_source_ids))
    return or_(*conditions)


async def _apply_visibility(db: AsyncSession, user: User, stmt):
    if _is_privileged(user):
        return stmt
    visible_departments = await _get_user_visible_departments(db, user.id)
    grant_ids = await _get_user_grant_source_ids(db, user.id)
    stmt = stmt.where(_build_visibility_filter(user, visible_departments, grant_ids))
    return stmt


def _freshness_status(source: DataSource, last_ingestion_at: datetime | None) -> str:
    """根据 stale_threshold_hours 和最近 ingestion 时间计算新鲜度。"""
    if not last_ingestion_at:
        return "unknown"
    threshold = timedelta(hours=source.stale_threshold_hours or 24)
    age = now_bjt() - last_ingestion_at
    return "fresh" if age <= threshold else "stale"


async def _compute_my_access(
    db: AsyncSession, user: User, source_id: str
) -> dict[str, Any]:
    """返回当前用户对该 source 的访问状态。

    granted: 有未过期 DataAccessGrant
    pending: 有 pending DataAccessRequest
    none   : 其余（admin 豁免也算 'granted'）
    """
    if _is_privileged(user):
        return {"status": "granted", "expires_at": None, "note": "admin 豁免"}

    now = now_bjt()
    grant = (
        await db.execute(
            select(DataAccessGrant)
            .where(
                DataAccessGrant.source_id == source_id,
                DataAccessGrant.grantee_user_id == user.id,
                or_(
                    DataAccessGrant.expires_at.is_(None),
                    DataAccessGrant.expires_at > now,
                ),
            )
            .order_by(DataAccessGrant.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if grant:
        return {
            "status": "granted",
            "expires_at": isoformat_bjt(grant.expires_at),
        }

    pending = (
        await db.execute(
            select(DataAccessRequest.id).where(
                DataAccessRequest.source_id == source_id,
                DataAccessRequest.requester_id == user.id,
                DataAccessRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if pending:
        return {"status": "pending", "request_id": pending}

    return {"status": "none"}


def _serialize(source: DataSource, freshness: str, my_access: dict[str, Any]) -> dict:
    return {
        "id": source.id,
        "name": source.name,
        "description": source.description,
        "usage_hint": source.usage_hint,
        "source_type": source.source_type,
        "department": source.department,
        "visibility": source.visibility,
        "owner_contact": source.owner_contact,
        "stale_threshold_hours": source.stale_threshold_hours,
        "freshness_status": freshness,
        "related_skills": list(source.related_skills or []),
        "is_active": source.is_active,
        "my_access": my_access,
        "updated_at": isoformat_bjt(source.updated_at),
        "created_at": isoformat_bjt(source.created_at),
    }


async def list_data_hall(
    db: AsyncSession,
    current_user: User,
    *,
    q: str | None = None,
    department: list[str] | str | None = None,
    source_type: str | None = None,
    visibility: str | None = None,
    freshness: str | None = None,
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "updated_at",
) -> dict:
    """大厅 · 数据能力列表（/api/hall/data）。

    v2.7.5：新增 visibility / freshness 过滤、部门多选、consumers_desc / name_asc 排序。
    freshness 在 SQL 层拿不到真实 ingestion，只能按 Python 计算结果后过滤。
    """
    stmt = select(DataSource).where(DataSource.is_active.is_(True))
    stmt = await _apply_visibility(db, current_user, stmt)

    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                DataSource.name.ilike(like),
                DataSource.description.ilike(like),
                DataSource.usage_hint.ilike(like),
                DataSource.id.ilike(like),
            )
        )
    if department:
        depts = department if isinstance(department, list) else [department]
        depts = [d for d in depts if d]
        if depts:
            stmt = stmt.where(DataSource.department.in_(depts))
    if source_type:
        stmt = stmt.where(DataSource.source_type == source_type)
    if visibility:
        stmt = stmt.where(DataSource.visibility == visibility)

    # 排序：name_asc 和 updated_at 可在 SQL；consumers_desc 需 Python 侧
    if sort_by == "name_asc" or sort_by == "name":
        stmt = stmt.order_by(DataSource.name.asc())
    else:
        stmt = stmt.order_by(DataSource.updated_at.desc().nulls_last())

    # freshness / consumers_desc 需要 Python 过滤/排序 —— 先拿候选集再切片
    need_post_filter = freshness is not None or sort_by == "consumers_desc"
    if need_post_filter:
        # 拉候选（上限 500 防爆）
        stmt_bulk = stmt.limit(500)
        rows_all = (await db.execute(stmt_bulk)).scalars().all()
        enriched = []
        for src in rows_all:
            fr = _freshness_status(src, src.updated_at)
            enriched.append((src, fr))
        if freshness:
            enriched = [(s, f) for s, f in enriched if f == freshness]
        if sort_by == "consumers_desc":
            enriched.sort(
                key=lambda t: -(len(t[0].related_skills or [])),
            )
        total = len(enriched)
        start = (page - 1) * page_size
        end = start + page_size
        slice_rows = enriched[start:end]
        items = []
        for src, fr in slice_rows:
            my_access = await _compute_my_access(db, current_user, src.id)
            items.append(_serialize(src, fr, my_access))
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # 计数
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.offset((page - 1) * page_size).limit(min(page_size, 200))
    rows = (await db.execute(stmt)).scalars().all()

    items = []
    for src in rows:
        # ingestion 最近一次（简化：暂用 updated_at；真实新鲜度可 join DataIngestionLog）
        freshness_val = _freshness_status(src, src.updated_at)
        my_access = await _compute_my_access(db, current_user, src.id)
        items.append(_serialize(src, freshness_val, my_access))

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_data_hall_detail(
    db: AsyncSession,
    current_user: User,
    source_id: str,
    *,
    unmask: bool = False,
) -> dict:
    """大厅 · 数据能力详情。

    可见性校验 + 反链 consumers（哪些 Skill 用此数据源）+ my_access 状态。
    不返回 row data（原始数据仍走 `/datasources/{id}/preview`，受 DataAccessGrant 校验）。

    v2.7 大厅 v3：`unmask=True` 时敏感字段的 description / desc 原样返回；否则替换为 `[脱敏]`。
    unmask 要求：admin 或该数据源的有效 grant 持有者。unmask 行为写 audit。
    """
    from app.common.audit import audit
    src = (
        await db.execute(select(DataSource).where(DataSource.id == source_id))
    ).scalar_one_or_none()
    if not src:
        from app.common.exceptions import AppError

        raise AppError("DATASOURCE_NOT_FOUND", 404)

    # 可见性：复用列表页的同一套过滤，避免 detail / list 行为漂移。
    if not _is_privileged(current_user):
        visible_stmt = await _apply_visibility(
            db,
            current_user,
            select(DataSource.id).where(DataSource.id == source_id),
        )
        visible = (await db.execute(visible_stmt)).scalar_one_or_none()
        if visible is None:
            # 对 private 且无 grant 的用户返回 404（不泄漏存在性）
            from app.common.exceptions import AppError

            raise AppError("DATASOURCE_NOT_FOUND", 404)

    freshness = _freshness_status(src, src.updated_at)
    my_access = await _compute_my_access(db, current_user, src.id)

    # 反链 consumers：有哪些 Skill 把此 source_id 列在 related_skills 里
    # 注：related_skills 是 DataSource 的字段（存 Skill id 列表）；反向查 Skill 需要从 Skill 结构里找依赖
    # 简化：直接返回 src.related_skills 里的 Skill 元信息
    consumers: list[dict] = []
    if src.related_skills:
        skill_stmt = select(Skill).where(Skill.id.in_(list(src.related_skills)))
        skill_stmt = skill_stmt.where(await build_skill_access_filter(db, current_user, "read"))
        skill_rows = (
            await db.execute(
                skill_stmt
            )
        ).scalars().all()
        consumers = []
        for skill in skill_rows:
            consumers.append({
                "id": skill.id,
                "name": skill.name,
                "department": skill.department,
                "status": skill.status,
                "permissions": await get_skill_permissions(db, skill, current_user),
            })

    result = _serialize(src, freshness, my_access)
    result["consumers"] = consumers
    # schema_preview 占位 —— 真实字段 schema 由 DataSource.config['schema'] 提供（若有）
    schema: list = []
    if isinstance(src.config, dict):
        schema = src.config.get("schema") or src.config.get("fields") or []

    # 敏感字段脱敏（unmask 要求 admin 或有效 grant）
    can_unmask = _is_privileged(current_user) or my_access.get("status") == "granted"
    if unmask and not can_unmask:
        from app.common.exceptions import AppError

        raise AppError(
            "PERMISSION_DENIED",
            403,
            detail={"reason": "无权查看敏感字段详情；请先申请访问"},
        )

    has_sensitive = False
    display_schema = []
    for f in (schema or []):
        field = dict(f) if isinstance(f, dict) else {"field": str(f)}
        if field.get("sensitive"):
            has_sensitive = True
            if not unmask:
                # 保留 field + type + sensitive 标记；脱敏 desc / description / sample
                for key in ("desc", "description", "sample", "sample_values"):
                    if key in field:
                        field[key] = "[脱敏 · 申请访问后可查看]"
        display_schema.append(field)
    result["schema_preview"] = display_schema
    result["has_sensitive_fields"] = has_sensitive
    result["schema_unmasked"] = bool(unmask and can_unmask)

    if unmask and can_unmask and has_sensitive:
        await audit.log(
            current_user.id,
            "datasource.unmask_schema",
            "datasource",
            source_id,
            detail={"fields": sum(1 for f in display_schema if isinstance(f, dict) and f.get("sensitive"))},
        )

    return result
