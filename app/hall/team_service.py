"""大厅 v3 · 团队能力聚合服务。

按部门聚合 Skill / 数据源 / AI 联系人，让新人能一眼看出"哪个部门在做什么"。
不新增数据模型，纯查询时聚合（Skill + DataSource + User 三张表 groupby）。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common import cache as cache_mod
from app.config import settings
from app.datasources.models import DataSource
from app.hall.category_domain import get_domain as _cat_domain
from app.org.models import OrgUnit
from app.skills.core.models import Skill
from app.skills.core.access import get_skill_permissions
from app.skills import hall_service as skill_hall_service
from app.datasources import hall_service as datasource_hall_service
from app.common.time_utils import isoformat_bjt, now_bjt


AI_ROLES = ("ai_engineer", "biz_owner")
FALLBACK_ROLES = ("dept_admin",)

# 大厅聚合查询 5 分钟 TTL（Skill 增删改高频时也能 5 分钟内拉齐）
HALL_CACHE_TTL = 300


async def _cache_get_safe(key: str):
    try:
        return await cache_mod.cache_get(key)
    except Exception as e:  # pragma: no cover - Redis 未起时降级
        logger.warning("hall cache_get 失败，降级直查：{}", e)
        return None


async def _cache_set_safe(key: str, value, ttl: int = HALL_CACHE_TTL) -> None:
    try:
        await cache_mod.cache_set(key, value, ttl=ttl)
    except Exception as e:  # pragma: no cover
        logger.warning("hall cache_set 失败：{}", e)


async def invalidate_hall_cache() -> None:
    """Skill / DataSource 写操作后调此方法清大厅聚合缓存。"""
    try:
        await cache_mod.cache_delete_pattern("hall:*")
    except Exception as e:  # pragma: no cover
        logger.warning("hall cache invalidate 失败：{}", e)


def _isoformat_bjt(dt: datetime | None) -> str | None:
    return isoformat_bjt(dt)


async def _load_related_data_sources(
    db: AsyncSession,
    current_user: User,
    skill_ids: list[str] | set[str],
):
    unique_skill_ids = skill_hall_service._dedupe_skill_ids(list(skill_ids))
    if not unique_skill_ids:
        return []

    ds_stmt = select(DataSource).where(
        DataSource.is_active.is_(True),
        DataSource.related_skills.isnot(None),
    )
    if skill_hall_service._is_postgresql(db):
        ds_stmt = ds_stmt.where(DataSource.related_skills.overlap(unique_skill_ids)).order_by(
            DataSource.name.asc(),
            DataSource.id.asc(),
        )
        ds_stmt = await datasource_hall_service._apply_visibility(db, current_user, ds_stmt)
        return (await db.execute(ds_stmt)).scalars().all()

    ds_stmt = await datasource_hall_service._apply_visibility(db, current_user, ds_stmt)
    ds_rows = (await db.execute(ds_stmt)).scalars().all()
    skill_id_set = set(unique_skill_ids)
    return sorted([
        row for row in ds_rows
        if row.related_skills and skill_id_set.intersection(row.related_skills)
    ], key=lambda row: (str(row.name or ""), str(row.id)))


async def list_departments(db: AsyncSession) -> dict:
    """返回任务树口径的一级部门候选项。

    大厅筛选项不能从当前页聚合结果反推，否则会漏掉没有出现在当前分页里的部门。
    """
    root_id = settings.TASKTREE_TOP_LEVEL_PARENT_ID
    rows = (
        await db.execute(
            select(
                OrgUnit.id,
                OrgUnit.name,
                OrgUnit.type,
                OrgUnit.parent_id,
                OrgUnit.path,
                OrgUnit.sort_order,
            )
            .where(OrgUnit.type == "department")
            .where(OrgUnit.parent_id == root_id)
            .order_by(OrgUnit.sort_order.asc(), OrgUnit.name.asc(), OrgUnit.id.asc())
        )
    ).all()

    items: list[dict] = []
    seen_names: set[str] = set()
    for unit_id, name, unit_type, parent_id, path, _sort_order in rows:
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        items.append(
            {
                "id": unit_id,
                "name": name,
                "department": name,
                "type": unit_type,
                "parent_id": parent_id,
                "path": path,
            }
        )
    return {"departments": items}


def _team_sorter(sort_by: str):
    m = {
        "active_desc": lambda x: (-x["skill_count_active"], -x["skill_count_total"]),
        "total_desc": lambda x: -x["skill_count_total"],
        "data_desc": lambda x: -(x["data_sources_count"] or 0),
        "name_asc": lambda x: str(x["department"]),
    }
    return m.get(sort_by, m["active_desc"])


async def list_team_capabilities(
    db: AsyncSession,
    current_user: User,
    q: str | None = None,
    sort_by: str = "active_desc",
    page: int = 1,
    page_size: int = 60,
) -> dict:
    """按部门聚合能力。返回 ``{items, total, page, page_size}``。

    ``q`` 模糊匹配部门名或 AI 联系人名；``sort_by`` 支持
    ``active_desc``/``total_desc``/``data_desc``/``name_asc``。
    """
    cache_key = f"hall:teams:{current_user.id}:{q or ''}:{sort_by}:{page}:{page_size}"
    cached = await _cache_get_safe(cache_key)
    if cached is not None:
        return cached
    result = await _compute_team_capabilities(db, current_user)
    if q:
        needle = q.strip().lower()
        result = [
            t
            for t in result
            if needle in str(t["department"]).lower()
            or needle in str((t.get("ai_contact") or {}).get("name") or "").lower()
            or needle in str((t.get("ai_contact") or {}).get("user_id") or "").lower()
        ]
    result.sort(key=_team_sorter(sort_by))
    total = len(result)
    start = (page - 1) * page_size
    end = start + page_size
    payload = {
        "items": result[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
    await _cache_set_safe(cache_key, payload)
    return payload


async def _compute_team_capabilities(db: AsyncSession, current_user: User) -> list[dict]:
    """实际聚合逻辑 —— 与分页/过滤解耦，便于缓存分层（外层按 key 存分页切片）。"""
    # Skill 聚合：按 department group count + 按 category 取 top 3
    skill_stmt = select(
        Skill.department,
        Skill.status,
        func.count(Skill.id).label("cnt"),
    ).group_by(Skill.department, Skill.status)
    skill_stmt = await skill_hall_service._apply_visibility(db, current_user, skill_stmt)
    skill_rows = (await db.execute(skill_stmt)).all()

    # 每部门 top category
    category_stmt = (
        select(
            Skill.department,
            Skill.category,
            func.count(Skill.id).label("cnt"),
        )
        .where(Skill.category.isnot(None))
        .group_by(Skill.department, Skill.category)
    )
    category_stmt = await skill_hall_service._apply_visibility(db, current_user, category_stmt)
    category_rows = (await db.execute(category_stmt)).all()

    # 数据源数量
    ds_stmt = (
        select(
            DataSource.department, func.count(DataSource.id).label("cnt")
        )
        .where(DataSource.is_active.is_(True))
        .group_by(DataSource.department)
    )
    ds_stmt = await datasource_hall_service._apply_visibility(db, current_user, ds_stmt)
    ds_rows = (await db.execute(ds_stmt)).all()

    # AI 联系人：每部门按 role 优先级取第一个
    user_rows = (
        await db.execute(
            select(User.id, User.name, User.department, User.role)
            .where(User.is_active.is_(True), User.state == "active")
        )
    ).all()

    dept_skill_total: dict[str, int] = defaultdict(int)
    dept_skill_active: dict[str, int] = defaultdict(int)
    for dept, status, cnt in skill_rows:
        if not dept:
            continue
        dept_skill_total[dept] += cnt
        if status == "active":
            dept_skill_active[dept] += cnt

    dept_categories: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for dept, cat, cnt in category_rows:
        if dept and cat:
            dept_categories[dept].append((cat, cnt))
    for dept in dept_categories:
        dept_categories[dept].sort(key=lambda p: p[1], reverse=True)

    dept_datasources: dict[str, int] = {dept: cnt for dept, cnt in ds_rows if dept}

    # AI 联系人：按 role 优先级选一个，fallback dept_admin
    dept_contact: dict[str, dict] = {}
    for uid, uname, udept, urole in user_rows:
        if not udept:
            continue
        current = dept_contact.get(udept)
        # 优先级：ai_engineer > biz_owner > dept_admin > 无
        def rank(r: str) -> int:
            if r in AI_ROLES:
                return AI_ROLES.index(r)
            if r in FALLBACK_ROLES:
                return len(AI_ROLES) + FALLBACK_ROLES.index(r)
            return 999

        if not current or rank(urole) < rank(current["role"]):
            dept_contact[udept] = {"user_id": uid, "name": uname, "role": urole}

    # 合成卡片
    result = []
    all_depts = sorted(
        set(dept_skill_total.keys()) | set(dept_datasources.keys()) | set(dept_contact.keys())
    )
    for dept in all_depts:
        result.append(
            {
                "department": dept,
                "skill_count_total": dept_skill_total.get(dept, 0),
                "skill_count_active": dept_skill_active.get(dept, 0),
                "top_categories": [
                    {"category": cat, "count": cnt}
                    for cat, cnt in (dept_categories.get(dept, []))[:3]
                ],
                "data_sources_count": dept_datasources.get(dept, 0),
                "ai_contact": dept_contact.get(dept),
            }
        )
    # 按活跃 Skill 数降序
    result.sort(key=lambda x: (-x["skill_count_active"], -x["skill_count_total"]))
    return result


async def list_capabilities(
    db: AsyncSession,
    current_user: User,
    q: str | None = None,
    departments: list[str] | None = None,
    sort_by: str = "active_desc",
    page: int = 1,
    page_size: int = 60,
) -> dict:
    """v2.7.2：按 Skill.category 聚合"能力分类"。

    返回分页结构 ``{items, total, page, page_size}``。支持：

    - ``q``：按 category 名 ILIKE 模糊匹配
    - ``departments``：要求 top_departments 至少命中一个
    - ``sort_by``：``active_desc`` / ``total_desc`` / ``name_asc``
    - 分页：page 从 1 起，page_size 默认 60

    v2.7.5：每条 item 追加 ``last_updated_at`` / ``last_run_at`` / ``new_skills_7d``
    用于前端新鲜度展示。

    未分类（category IS NULL / '' ）的 Skill 归入"其它"。
    """
    dept_key = ",".join(sorted(departments)) if departments else ""
    cache_key = f"hall:caps:{current_user.id}:{q or ''}:{dept_key}:{sort_by}:{page}:{page_size}"
    cached = await _cache_get_safe(cache_key)
    if cached is not None:
        return cached
    payload = await _compute_list_capabilities(
        db,
        current_user=current_user,
        q=q,
        departments=departments,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )
    await _cache_set_safe(cache_key, payload)
    return payload


async def _compute_list_capabilities(
    db: AsyncSession,
    *,
    current_user: User,
    q: str | None,
    departments: list[str] | None,
    sort_by: str,
    page: int,
    page_size: int,
) -> dict:
    from app.datasources.models import DataSource

    category_expr = func.coalesce(func.nullif(Skill.category, ""), "其它")
    seven_days_ago = now_bjt() - timedelta(days=7)

    skill_stmt = (
        select(
            Skill.id.label("skill_id"),
            category_expr.label("category"),
            Skill.department.label("department"),
            Skill.status.label("status"),
            Skill.name.label("name"),
            func.coalesce(Skill.usage_count, 0).label("usage_count"),
            Skill.updated_at.label("updated_at"),
            Skill.last_run_at.label("last_run_at"),
            Skill.created_at.label("created_at"),
        )
        .where(Skill.status != "deprecated")
    )
    skill_stmt = await skill_hall_service._apply_visibility(db, current_user, skill_stmt)
    visible_skills = skill_stmt.subquery("visible_capability_skills")

    agg_rows = (
        await db.execute(
            select(
                visible_skills.c.category,
                func.count().label("skill_count_total"),
                func.sum(case((visible_skills.c.status == "active", 1), else_=0)).label("skill_count_active"),
                func.max(visible_skills.c.updated_at).label("last_updated_at"),
                func.max(visible_skills.c.last_run_at).label("last_run_at"),
                func.sum(case((visible_skills.c.created_at >= seven_days_ago, 1), else_=0)).label("new_skills_7d"),
            ).group_by(visible_skills.c.category)
        )
    ).all()

    dept_rows = (
        await db.execute(
            select(
                visible_skills.c.category,
                visible_skills.c.department,
                func.count().label("cnt"),
            )
            .where(visible_skills.c.department.isnot(None))
            .group_by(visible_skills.c.category, visible_skills.c.department)
        )
    ).all()
    top_departments_by_category: dict[str, list[dict]] = defaultdict(list)
    for category, department, cnt in sorted(
        dept_rows,
        key=lambda row: (-int(row[2] or 0), str(row[1] or ""), str(row[0] or "")),
    ):
        bucket = top_departments_by_category[category]
        if len(bucket) >= 3:
            continue
        bucket.append({"department": department, "count": int(cnt or 0)})

    sample_filter = select(
        visible_skills.c.category,
        visible_skills.c.skill_id,
        visible_skills.c.name,
        visible_skills.c.department,
        visible_skills.c.usage_count,
        func.max(case((visible_skills.c.status == "active", 1), else_=0)).over(
            partition_by=visible_skills.c.category
        ).label("has_active"),
        case((visible_skills.c.status == "active", 1), else_=0).label("is_active"),
    ).subquery("capability_sample_filter")
    ranked_samples = select(
        sample_filter.c.category,
        sample_filter.c.skill_id,
        sample_filter.c.name,
        sample_filter.c.department,
        sample_filter.c.usage_count,
        func.row_number().over(
            partition_by=sample_filter.c.category,
            order_by=(sample_filter.c.usage_count.desc(), sample_filter.c.skill_id.asc()),
        ).label("rn"),
    ).where(
        or_(
            and_(sample_filter.c.has_active == 1, sample_filter.c.is_active == 1),
            sample_filter.c.has_active == 0,
        )
    ).subquery("capability_ranked_samples")
    sample_rows = (
        await db.execute(
            select(
                ranked_samples.c.category,
                ranked_samples.c.skill_id,
                ranked_samples.c.name,
                ranked_samples.c.department,
                ranked_samples.c.usage_count,
            )
            .where(ranked_samples.c.rn <= 3)
            .order_by(ranked_samples.c.category, ranked_samples.c.rn)
        )
    ).all()
    sample_skills_by_category: dict[str, list[dict]] = defaultdict(list)
    for category, skill_id, name, department, usage_count in sample_rows:
        sample_skills_by_category[category].append({
            "id": skill_id,
            "name": name,
            "department": department,
            "usage_count": int(usage_count or 0),
        })

    data_sources_by_category: dict[str, list[dict]] = defaultdict(list)
    if skill_hall_service._is_postgresql(db):
        ds_links = select(
            DataSource.id.label("source_id"),
            DataSource.name.label("source_name"),
            func.unnest(DataSource.related_skills).label("skill_id"),
        ).where(
            DataSource.is_active.is_(True),
            DataSource.related_skills.isnot(None),
        )
        ds_links = await datasource_hall_service._apply_visibility(db, current_user, ds_links)
        ds_links_sq = ds_links.subquery("capability_ds_links")
        ds_link_rows = (
            await db.execute(
                select(
                    visible_skills.c.category,
                    ds_links_sq.c.source_id,
                    ds_links_sq.c.source_name,
                )
                .join(ds_links_sq, ds_links_sq.c.skill_id == visible_skills.c.skill_id)
                .group_by(visible_skills.c.category, ds_links_sq.c.source_id, ds_links_sq.c.source_name)
            )
        ).all()
    else:
        skill_key_rows = (await db.execute(select(visible_skills.c.skill_id, visible_skills.c.category))).all()
        category_by_skill_id = {skill_id: category for skill_id, category in skill_key_rows}
        ds_stmt = select(DataSource.id, DataSource.name, DataSource.related_skills).where(
            DataSource.is_active.is_(True),
            DataSource.related_skills.isnot(None),
        )
        ds_stmt = await datasource_hall_service._apply_visibility(db, current_user, ds_stmt)
        ds_link_rows = []
        for source_id, source_name, related_skills in (await db.execute(ds_stmt)).all():
            if not related_skills:
                continue
            for category in sorted({category_by_skill_id[sid] for sid in related_skills if sid in category_by_skill_id}):
                ds_link_rows.append((category, source_id, source_name))

    seen_ds_pairs: set[tuple[str, str]] = set()
    for category, source_id, source_name in sorted(
        ds_link_rows,
        key=lambda row: (str(row[0] or ""), str(row[2] or ""), str(row[1] or "")),
    ):
        pair = (str(category), str(source_id))
        if pair in seen_ds_pairs:
            continue
        seen_ds_pairs.add(pair)
        data_sources_by_category[category].append({"id": source_id, "name": source_name})

    result: list[dict] = []
    for row in agg_rows:
        result.append({
            "category": row.category,
            "domain": _cat_domain(row.category),
            "skill_count_total": int(row.skill_count_total or 0),
            "skill_count_active": int(row.skill_count_active or 0),
            "top_departments": top_departments_by_category.get(row.category, []),
            "data_sources": data_sources_by_category.get(row.category, []),
            "sample_skills": sample_skills_by_category.get(row.category, []),
            "last_updated_at": _isoformat_bjt(row.last_updated_at),
            "last_run_at": _isoformat_bjt(row.last_run_at),
            "new_skills_7d": int(row.new_skills_7d or 0),
        })

    # 过滤
    if q:
        needle = q.strip().lower()
        if needle:
            result = [r for r in result if needle in str(r["category"]).lower()]
    if departments:
        dept_set = {d for d in departments if d}
        if dept_set:
            result = [
                r
                for r in result
                if any(td["department"] in dept_set for td in r["top_departments"])
            ]

    # 排序
    sorters: dict[str, callable] = {
        "active_desc": lambda x: (-x["skill_count_active"], -x["skill_count_total"]),
        "total_desc": lambda x: -x["skill_count_total"],
        "name_asc": lambda x: str(x["category"]),
    }
    result.sort(key=sorters.get(sort_by, sorters["active_desc"]))

    total = len(result)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": result[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_capability_detail(db: AsyncSession, current_user: User, category: str) -> dict:
    """某能力的详情：所有 Skill（带状态/owner/依赖）+ 所有数据源 + 主要部门联系人。"""
    from collections import Counter

    # 支持"其它"伪类别：category 为 NULL 或空
    if category == "其它":
        stmt = select(Skill).where(
            Skill.status != "deprecated",
            or_(Skill.category.is_(None), Skill.category == ""),
        )
    else:
        stmt = select(Skill).where(
            Skill.status != "deprecated",
            Skill.category == category,
        )
    stmt = await skill_hall_service._apply_visibility(db, current_user, stmt)
    skills = (await db.execute(stmt)).scalars().all()
    skill_ids = {s.id for s in skills}

    # 反链数据源
    related_ds = await _load_related_data_sources(db, current_user, skill_ids)

    # 主要提供部门 + 每部门 AI 联系人
    dept_counter = Counter(s.department for s in skills if s.department)
    top_depts = [d for d, _ in dept_counter.most_common(5)]
    contact_rows = (
        await db.execute(
            select(User.id, User.name, User.department, User.role)
            .where(User.department.in_(top_depts) if top_depts else False)
            .where(User.is_active.is_(True), User.state == "active")
        )
    ).all()
    dept_contact: dict[str, dict] = {}
    for uid, uname, udept, urole in contact_rows:
        if not udept or udept in dept_contact:
            continue
        if urole in AI_ROLES:
            dept_contact[udept] = {"user_id": uid, "name": uname, "role": urole}
    # fallback dept_admin
    for uid, uname, udept, urole in contact_rows:
        if udept in dept_contact or udept not in top_depts:
            continue
        if urole in FALLBACK_ROLES:
            dept_contact[udept] = {"user_id": uid, "name": uname, "role": urole}

    return {
        "category": category,
        "summary": {
            "skill_count_total": len(skills),
            "skill_count_active": sum(1 for s in skills if s.status == "active"),
            "data_sources_count": len(related_ds),
            "department_count": len(dept_counter),
        },
        "skills": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "department": s.department,
                "owner": s.owner,
                "status": s.status,
                "risk_level": s.risk_level,
                "usage_count": s.usage_count or 0,
                "success_rate": s.success_rate,
                "last_run_at": isoformat_bjt(s.last_run_at),
                "permissions": await get_skill_permissions(db, s, current_user),
            }
            for s in sorted(skills, key=lambda s: -(s.usage_count or 0))
        ],
        "data_sources": [
            {
                "id": d.id,
                "name": d.name,
                "department": d.department,
                "visibility": d.visibility,
                "description": d.description,
                "source_type": d.source_type,
            }
            for d in related_ds
        ],
        "top_departments": [
            {
                "department": d,
                "skill_count": dept_counter[d],
                "ai_contact": dept_contact.get(d),
            }
            for d in top_depts
        ],
    }


async def get_team_detail(db: AsyncSession, current_user: User, department: str) -> dict:
    """团队详情：该部门的 active skills + 负责的数据源 + members。"""
    from app.datasources.models import DataSource

    # active skills
    skill_stmt = (
        select(Skill)
        .where(Skill.department == department)
        .order_by(Skill.status.desc(), Skill.usage_count.desc().nulls_last())
        .limit(200)
    )
    skill_stmt = await skill_hall_service._apply_visibility(db, current_user, skill_stmt)
    skill_rows = (await db.execute(skill_stmt)).scalars().all()

    # 数据源（owner=本部门 或 department=本部门）
    ds_stmt = select(DataSource).where(
        DataSource.department == department,
        DataSource.is_active.is_(True),
    )
    ds_stmt = await datasource_hall_service._apply_visibility(db, current_user, ds_stmt)
    ds_rows = (await db.execute(ds_stmt)).scalars().all()

    # 成员（active）
    member_rows = (
        await db.execute(
            select(User.id, User.name, User.role).where(
                User.department == department,
                User.is_active.is_(True),
                User.state == "active",
            )
        )
    ).all()

    return {
        "department": department,
        "skills": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "category": s.category,
                "status": s.status,
                "risk_level": s.risk_level,
                "usage_count": s.usage_count or 0,
                "success_rate": s.success_rate,
                "last_run_at": isoformat_bjt(s.last_run_at),
                "permissions": await get_skill_permissions(db, s, current_user),
            }
            for s in skill_rows
        ],
        "data_sources": [
            {
                "id": d.id,
                "name": d.name,
                "visibility": d.visibility,
                "source_type": d.source_type,
                "description": d.description,
            }
            for d in ds_rows
        ],
        "members": [
            {"user_id": uid, "name": uname, "role": urole}
            for uid, uname, urole in member_rows
        ],
        "summary": {
            "skill_count_total": len(skill_rows),
            "skill_count_active": sum(1 for s in skill_rows if s.status == "active"),
            "data_sources_count": len(ds_rows),
            "member_count": len(member_rows),
        },
    }
