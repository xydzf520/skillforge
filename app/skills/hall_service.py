"""Skill 大厅服务。

跨部门 Skill 浏览、发现与协作的核心入口。
根据用户身份和 Skill 可见性规则决定哪些 Skill 对当前用户可见。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.org.models import UserOrgMembership
from app.skills.core.access import (
    SkillMember,
    build_skill_access_filter,
    get_skill_permissions,
    list_user_member_skill_ids,
)
from app.skills.core.models import Skill
from app.common.time_utils import isoformat_bjt, now_bjt


def _db_dialect_name(db: AsyncSession) -> str:
    return getattr(getattr(db.bind, "dialect", None), "name", "") or ""


def _is_postgresql(db: AsyncSession) -> bool:
    return _db_dialect_name(db) == "postgresql"


def _dedupe_skill_ids(skill_ids: list[str]) -> list[str]:
    return [sid for sid in dict.fromkeys(skill_ids) if sid]


def _build_visibility_filter(
    user: User,
    user_org_ids: list[str],
    member_skill_ids: list[str],
):
    """根据用户身份构建可见性 WHERE 条件。

    可见性规则：
    - visibility='company': 全公司可见
    - visibility='department': 同部门或同组织树可见
    - visibility='private': 仅 skill_members 可见
    - 自己是 skill_member 的: 无论 visibility 都可见
    """
    conditions = [
        Skill.visibility == "company",
        and_(Skill.visibility == "department", Skill.department == user.department),
    ]
    # 组织树匹配：skill.org_unit_id 在用户所属的 org_unit 列表中
    if user_org_ids:
        conditions.append(
            and_(Skill.visibility == "department", Skill.org_unit_id.in_(user_org_ids))
        )
    # 用户是成员的 Skill 无论 visibility 都可见
    if member_skill_ids:
        conditions.append(Skill.id.in_(member_skill_ids))

    return or_(*conditions)


async def _get_user_org_ids(db: AsyncSession, user_id: str) -> list[str]:
    """获取用户所属的所有 org_unit_id"""
    result = await db.execute(
        select(UserOrgMembership.org_unit_id).where(UserOrgMembership.user_id == user_id)
    )
    return [row[0] for row in result.all()]


async def _get_member_skill_ids(db: AsyncSession, user_id: str) -> list[str]:
    """获取用户作为成员的所有 skill_id"""
    return await list_user_member_skill_ids(db, user_id)


def _is_privileged(user: User) -> bool:
    """admin 或 can_view_all 的用户可以看到所有 Skill"""
    return user.role == "admin" or user.can_view_all


async def _apply_visibility(
    db: AsyncSession,
    user: User,
    stmt,
):
    """对查询语句追加可见性过滤（非特权用户才过滤）。返回过滤后的 stmt。"""
    return stmt.where(await build_skill_access_filter(db, user, "read"))


async def _build_skill_profiles(
    db: AsyncSession, skill_ids: list[str]
) -> dict[str, dict]:
    """v2.7 大厅 v3 · batch 构建 Skill 画像。

    每个 Skill 返回：
      - adoption_departments: 哪些部门有成员加入此 Skill
      - data_sources: 反查 data_sources.related_skills 里包含本 Skill 的数据源
      - 已有的 usage_count / success_rate / last_run_at 等字段在 list_hall_skills
        主体已拉，这里只补这两项"跨表聚合"结果
    """
    if not skill_ids:
        return {}
    skill_ids = _dedupe_skill_ids(skill_ids)

    # 尝试从 Redis 缓存读取
    from app.common.cache import cache_get, cache_set

    result: dict[str, dict] = {}
    missing: list[str] = []
    for sid in skill_ids:
        cached = await cache_get(f"sf:hall:skill_profile:{sid}")
        if cached:
            result[sid] = cached
        else:
            missing.append(sid)
    if not missing:
        return result

    # adoption_departments: SkillMember × User.department
    adoption: dict[str, set[str]] = {sid: set() for sid in missing}
    member_rows = (
        await db.execute(
            select(SkillMember.skill_id, User.department)
            .join(User, User.id == SkillMember.user_id)
            .where(SkillMember.skill_id.in_(missing))
            .where(User.department.isnot(None))
        )
    ).all()
    for skill_id, dept in member_rows:
        if dept:
            adoption[skill_id].add(dept)

    # data_sources: DataSource.related_skills ARRAY 反链 —— Python 端 filter
    # PG 走 ARRAY overlap 精确收窄；其它方言保留 Python 端降级，避免 sqlite/test 环境炸掉。
    from app.datasources.models import DataSource

    missing_set = set(missing)
    ds_stmt = select(DataSource.id, DataSource.name, DataSource.related_skills).where(
        DataSource.related_skills.isnot(None)
    )
    if _is_postgresql(db):
        ds_stmt = ds_stmt.where(DataSource.related_skills.overlap(missing))
    data_rows = (await db.execute(ds_stmt)).all()
    reverse: dict[str, list[dict]] = {sid: [] for sid in missing}
    for ds_id, ds_name, rel in data_rows:
        if not rel:
            continue
        for sid in rel:
            if sid in missing_set:
                reverse[sid].append({"id": ds_id, "name": ds_name})
    for sid in reverse:
        reverse[sid].sort(key=lambda item: (str(item.get("name") or ""), str(item.get("id") or "")))

    # 填充 + 写缓存
    TTL = 300
    for sid in missing:
        profile = {
            "adoption_departments": sorted(adoption.get(sid, set())),
            "data_sources": reverse.get(sid, []),
        }
        result[sid] = profile
        try:
            await cache_set(f"sf:hall:skill_profile:{sid}", profile, ttl=TTL)
        except Exception:
            pass

    return result


async def list_hall_skills(
    db: AsyncSession,
    current_user: User,
    *,
    q: str | None = None,
    department: str | None = None,
    category: str | None = None,
    status: str | None = None,
    sort_by: str = "popularity",
    page: int = 1,
    page_size: int = 24,
    include: list[str] | None = None,
) -> dict:
    """Skill 大厅列表。

    可见性规则：
    - admin/can_view_all: 看到所有 Skill
    - 普通用户:
      - visibility='company': 全公司可见
      - visibility='department': 同部门或同组织树可见
      - visibility='private': 仅 skill_members 可见
      - 自己是 skill_member 的: 无论 visibility 都可见

    排序：
    - popularity: usage_count DESC
    - updated_at: 按更新时间
    - name: 按名称
    - health: 按 success_rate DESC

    返回每个 Skill 额外包含：
    - can_fork: bool (当前用户是否可以 Fork)
    - is_member: bool (当前用户是否是成员)
    - owner_name: str (负责人名称)
    """
    # 预加载用户成员关系（后续判断 is_member / can_fork 用）
    member_skill_ids = await _get_member_skill_ids(db, current_user.id)
    member_skill_set = set(member_skill_ids)

    # 基础查询：左联 owner 用户表获取 owner_name
    stmt = (
        select(Skill, User.name.label("owner_name"))
        .outerjoin(User, User.id == Skill.owner)
    )
    total_stmt = select(func.count(Skill.id))

    # 可见性过滤
    stmt = await _apply_visibility(db, current_user, stmt)
    total_stmt = await _apply_visibility(db, current_user, total_stmt)

    # 筛选条件
    filters = []
    if department:
        filters.append(Skill.department == department)
    if category:
        filters.append(Skill.category == category)
    if status and status != "all":
        filters.append(Skill.status == status)
    if q:
        search = f"%{q}%"
        filters.append(or_(
            Skill.id.ilike(search),
            Skill.name.ilike(search),
            Skill.description.ilike(search),
        ))

    for f in filters:
        stmt = stmt.where(f)
        total_stmt = total_stmt.where(f)

    # 排序
    sort_map = {
        "popularity": Skill.usage_count.desc(),
        "updated_at": Skill.updated_at.desc(),
        "name": Skill.name.asc(),
        "health": Skill.success_rate.desc().nulls_last(),
    }
    order = sort_map.get(sort_by, Skill.usage_count.desc())
    stmt = stmt.order_by(order)

    # 总数
    total = (await db.execute(total_stmt)).scalar() or 0

    # 分页
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for row in rows:
        skill = row[0]
        owner_name = row[1]

        permissions = await get_skill_permissions(db, skill, current_user)
        # can_fork: 有创建 Skill 权限的角色可以 fork（admin/ai_engineer/aibp）
        can_fork = current_user.role in ("admin", "system_admin", "ai_engineer", "aibp")
        is_member = skill.id in member_skill_set

        items.append({
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "department": skill.department,
            "category": skill.category,
            "status": skill.status,
            "visibility": skill.visibility,
            "icon": skill.icon,
            "owner": skill.owner,
            "owner_name": owner_name or skill.owner,
            "usage_count": skill.usage_count or 0,
            "success_rate": skill.success_rate,
            "trigger_type": skill.trigger_type,
            "risk_level": skill.risk_level,
            "current_version": skill.current_version,
            "forked_from": skill.forked_from,
            "last_run_at": isoformat_bjt(skill.last_run_at),
            "can_fork": can_fork,
            "is_member": is_member,
            "permissions": permissions,
            "updated_at": isoformat_bjt(skill.updated_at),
            "created_at": isoformat_bjt(skill.created_at),
        })

    # v2.7 大厅 v3：include=profile 时 batch 查 adoption_departments + data_sources
    if include and "profile" in include and items:
        ids = [it["id"] for it in items]
        profiles = await _build_skill_profiles(db, ids)
        for it in items:
            p = profiles.get(it["id"], {"adoption_departments": [], "data_sources": []})
            it["profile"] = {
                "usage_count": it["usage_count"],
                "success_rate": it["success_rate"],
                "last_run_at": it["last_run_at"],
                "adoption_departments": p["adoption_departments"],
                "data_sources": p["data_sources"],
            }

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_hall_stats(db: AsyncSession, current_user: User) -> dict:
    """大厅统计摘要。

    返回：
    - total_visible: 当前用户可见的 Skill 总数
    - by_department: [{department, count}] 按部门分布
    - by_category: [{category, count}] 按分类分布
    - recently_published: 最近 7 天发布的 Skill 列表 (top 5)
    - most_forked: Fork 次数最多的 Skill (top 5)
    - trending: 最近 7 天 usage_count 增长最快的 (top 5，按 usage_count 排序)
    """
    seven_days_ago = now_bjt() - timedelta(days=7)

    # --- total_visible ---
    total_stmt = select(func.count(Skill.id))
    total_stmt = await _apply_visibility(db, current_user, total_stmt)
    total_visible = (await db.execute(total_stmt)).scalar() or 0

    # --- by_department ---
    dept_stmt = (
        select(Skill.department, func.count(Skill.id).label("count"))
        .where(Skill.department.isnot(None))
        .group_by(Skill.department)
        .order_by(func.count(Skill.id).desc())
    )
    dept_stmt = await _apply_visibility(db, current_user, dept_stmt)
    dept_rows = (await db.execute(dept_stmt)).all()
    by_department = [
        {"department": row[0], "count": row[1]}
        for row in dept_rows if row[0]
    ]

    # --- by_category ---
    cat_stmt = (
        select(Skill.category, func.count(Skill.id).label("count"))
        .where(Skill.category.isnot(None))
        .group_by(Skill.category)
        .order_by(func.count(Skill.id).desc())
    )
    cat_stmt = await _apply_visibility(db, current_user, cat_stmt)
    cat_rows = (await db.execute(cat_stmt)).all()
    by_category = [
        {"category": row[0], "count": row[1]}
        for row in cat_rows if row[0]
    ]

    # --- recently_published: 最近 7 天更新且状态为 active 的 Skill ---
    recent_stmt = (
        select(Skill)
        .where(Skill.updated_at >= seven_days_ago)
        .where(Skill.status == "active")
        .order_by(Skill.updated_at.desc())
        .limit(5)
    )
    recent_stmt = await _apply_visibility(db, current_user, recent_stmt)
    recent_rows = (await db.execute(recent_stmt)).scalars().all()
    recently_published = [
        {
            "id": s.id,
            "name": s.name,
            "department": s.department,
            "updated_at": isoformat_bjt(s.updated_at),
        }
        for s in recent_rows
    ]

    # --- most_forked: forked_from 被引用最多的源 Skill ---
    fork_stmt = select(Skill.forked_from, func.count(Skill.id).label("fork_count")).where(
        Skill.forked_from.isnot(None)
    )
    fork_stmt = await _apply_visibility(db, current_user, fork_stmt)
    fork_stmt = (
        fork_stmt
        .group_by(Skill.forked_from)
        .order_by(func.count(Skill.id).desc())
    )
    fork_rows = (await db.execute(fork_stmt)).all()
    # 源 Skill 本身也必须对当前用户可读，避免统计区泄露私有 Skill 名称或 ID。
    forked_source_ids = [row[0] for row in fork_rows if row[0]]
    source_names = {}
    if forked_source_ids:
        source_stmt = select(Skill.id, Skill.name).where(Skill.id.in_(forked_source_ids))
        source_stmt = await _apply_visibility(db, current_user, source_stmt)
        name_result = await db.execute(source_stmt)
        source_names = {row[0]: row[1] for row in name_result.all()}

    most_forked = [
        {
            "id": row[0],
            "name": source_names.get(row[0], row[0]),
            "fork_count": row[1],
        }
        for row in fork_rows if row[0] in source_names
    ][:5]

    # --- trending: usage_count 最高且近 7 天有活跃的 Skill ---
    trending_stmt = (
        select(Skill)
        .where(Skill.last_run_at >= seven_days_ago)
        .order_by(Skill.usage_count.desc())
        .limit(5)
    )
    trending_stmt = await _apply_visibility(db, current_user, trending_stmt)
    trending_rows = (await db.execute(trending_stmt)).scalars().all()
    trending = [
        {
            "id": s.id,
            "name": s.name,
            "department": s.department,
            "usage_count": s.usage_count or 0,
        }
        for s in trending_rows
    ]

    # B5: 显式"覆盖部门数" — 排除"未指定"占位，让前端不要再自己 .length 推算
    dept_count = sum(1 for d in by_department if d.get("department") and d["department"] != "未指定")
    # O10: 显式"本周新增条目数" — 前端原本 recently_published.length 口径混淆（top 5 截断）
    weekly_new = len(recently_published)

    # N2: 近 7 天每天新增条数（最早→最近），供 Hall mini trend chart
    daily_new = []
    for days_ago in range(6, -1, -1):
        day_start = now_bjt().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
        day_end = day_start + timedelta(days=1)
        cnt_stmt = select(func.count(Skill.id)).where(
            Skill.created_at >= day_start,
            Skill.created_at < day_end,
        )
        cnt_stmt = await _apply_visibility(db, current_user, cnt_stmt)
        count = (await db.execute(cnt_stmt)).scalar() or 0
        daily_new.append({
            "date": day_start.strftime("%m-%d"),
            "count": int(count),
        })

    return {
        "total_visible": total_visible,
        "by_department": by_department,
        "by_category": by_category,
        "recently_published": recently_published,
        "most_forked": most_forked,
        "trending": trending,
        "dept_count": dept_count,
        "weekly_new": weekly_new,
        "daily_new": daily_new,
    }


async def get_departments_list(db: AsyncSession, current_user: User | None = None) -> list[dict]:
    """获取所有有 Skill 的部门列表（大厅筛选器用）"""
    stmt = (
        select(Skill.department, func.count(Skill.id).label("count"))
        .where(Skill.department.isnot(None))
        .group_by(Skill.department)
        .order_by(func.count(Skill.id).desc())
    )
    if current_user is not None:
        stmt = stmt.where(await build_skill_access_filter(db, current_user, "read"))
    rows = (await db.execute(stmt)).all()
    return [
        {"department": row[0], "count": row[1]}
        for row in rows if row[0]
    ]


async def get_categories_list(db: AsyncSession, current_user: User | None = None) -> list[dict]:
    """获取所有分类列表（大厅筛选器用）"""
    stmt = (
        select(Skill.category, func.count(Skill.id).label("count"))
        .where(Skill.category.isnot(None))
        .group_by(Skill.category)
        .order_by(func.count(Skill.id).desc())
    )
    if current_user is not None:
        stmt = stmt.where(await build_skill_access_filter(db, current_user, "read"))
    rows = (await db.execute(stmt)).all()
    return [
        {"category": row[0], "count": row[1]}
        for row in rows if row[0]
    ]
