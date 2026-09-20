"""Inbox reports service."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, delete, func, inspect, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import (
    expand_department_subtree,
    expand_org_lineage,
    get_user_state,
)
from app.auth.models import User
from app.common import cache as cache_mod
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, parse_bjt_datetime, now_bjt
from app.execution.models import DecisionLog, ExecutionRun
from app.inbox.models import InboxReportCard
from app.org.models import OrgUnit, UserOrgMembership
from app.skills.core.access import require_skill_access
from app.skills.members import SkillMember
from app.skills.core.models import Skill
from app.todos.models import AITodo, DecisionRequest

_CARD_ID_RE = re.compile(r"^(?P<decision_log_id>\d+)-(?P<report_index>\d+)$")
_LIST_CACHE_TTL_SECONDS = 60
_DETAIL_CACHE_TTL_SECONDS = 300
_VISIBLE_SKILL_IDS_TTL_SECONDS = 60
_DEFAULT_LIST_DAYS = 90
_REPORT_CARD_TABLE_EXISTS: bool | None = None


@dataclass(slots=True)
class _VisibleSkillSeed:
    id: str
    visibility: str
    org_unit_id: str | None
    department: str | None


def _is_active_user(user: User) -> bool:
    return get_user_state(user) == "active" and bool(getattr(user, "is_active", True))


def _has_global_read_scope(user: User) -> bool:
    role = getattr(user, "role", "") or ""
    return _is_active_user(user) and (
        role in {"system_admin", "admin"} or bool(getattr(user, "can_view_all", False))
    )


def _can_view_debug_context(user: User) -> bool:
    role = getattr(user, "role", "") or ""
    return _is_active_user(user) and (
        role in {"system_admin", "admin"} or bool(getattr(user, "can_view_all", False))
    )


def parse_report_card_id(card_id: str) -> tuple[int, int]:
    match = _CARD_ID_RE.fullmatch((card_id or "").strip())
    if not match:
        raise AppError("REPORT_INVALID_CARD_ID", 400)
    return int(match.group("decision_log_id")), int(match.group("report_index"))


def _parse_datetime(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if len(raw) == 10 and "T" not in raw:
            parsed = parse_bjt_datetime(raw)
            if end_of_day:
                return parsed + timedelta(days=1) - timedelta(microseconds=1)
            return parsed
        parsed = parse_bjt_datetime(raw)
    except ValueError:
        return None
    return parsed


def _to_iso(value: datetime | None) -> str | None:
    return isoformat_bjt(value)


def _coerce_json(value: Any, *, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _normalize_metric(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    label = str(item.get("label") or "").strip()
    value = str(item.get("value") or "").strip()
    if not label or not value:
        return None
    trend = str(item.get("trend") or "").strip() or None
    delta = str(item.get("delta") or "").strip() or None
    return {
        "label": label[:80],
        "value": value[:200],
        "trend": trend[:40] if trend else None,
        "delta": delta[:80] if delta else None,
    }


def _normalize_metrics(value: Any) -> list[dict[str, str]]:
    metrics = _coerce_json(value, default=[])
    if not isinstance(metrics, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in metrics:
        metric = _normalize_metric(item)
        if metric is not None:
            normalized.append(metric)
    return normalized


def _normalize_tags(value: Any) -> list[str]:
    tags = _coerce_json(value, default=[])
    if not isinstance(tags, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in tags:
        tag = str(item or "").strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag[:80])
    return normalized


def extract_report_cards(decision_log_id: int, output_result: dict | None) -> list[dict[str, Any]]:
    """Extract valid report cards from a decision_log payload."""
    if not isinstance(output_result, dict):
        return []
    reports = output_result.get("reports")
    if not isinstance(reports, list):
        return []

    cards: list[dict[str, Any]] = []
    for report_index, report in enumerate(reports):
        if not isinstance(report, dict):
            continue
        title = str(report.get("title") or "").strip()
        summary = str(report.get("summary") or "").strip()
        if not title or not summary:
            continue
        payload = report.get("payload")
        payload_dict = payload if isinstance(payload, dict) else None
        report_design = payload_dict.get("_report_design") if isinstance(payload_dict, dict) and isinstance(payload_dict.get("_report_design"), dict) else None
        cards.append(
            {
                "id": f"{decision_log_id}-{report_index}",
                "decision_log_id": decision_log_id,
                "report_index": report_index,
                "channel": str(report.get("channel") or "").strip() or None,
                "title": title[:200],
                "summary": summary[:800],
                "content_markdown": str(report.get("content_markdown") or "").strip() or None,
                "metrics": _normalize_metrics(report.get("metrics")),
                "tags": _normalize_tags(report.get("tags")),
                "payload": payload_dict,
                "report_design": report_design,
            }
        )
    return cards


async def _inbox_report_card_table_available(db: AsyncSession) -> bool:
    global _REPORT_CARD_TABLE_EXISTS
    if _REPORT_CARD_TABLE_EXISTS is not None:
        return _REPORT_CARD_TABLE_EXISTS
    try:
        exists = await db.run_sync(
            lambda sync_session: inspect(sync_session.bind).has_table("inbox_report_cards")
        )
    except Exception:
        _REPORT_CARD_TABLE_EXISTS = False
        return False
    _REPORT_CARD_TABLE_EXISTS = bool(exists)
    return _REPORT_CARD_TABLE_EXISTS


async def _inbox_report_card_projection_ready(db: AsyncSession) -> bool:
    if not await _inbox_report_card_table_available(db):
        return False
    try:
        return bool(await db.scalar(text("SELECT EXISTS (SELECT 1 FROM inbox_report_cards LIMIT 1)")))
    except Exception:
        return False


def _projection_values_for_decision_log(decision_log: DecisionLog) -> list[dict[str, Any]]:
    if bool(getattr(decision_log, "is_sandbox", False)):
        return []
    cards = extract_report_cards(
        int(decision_log.id),
        decision_log.output_result if isinstance(decision_log.output_result, dict) else None,
    )
    values: list[dict[str, Any]] = []
    for card in cards:
        values.append(
            {
                "decision_log_id": int(decision_log.id),
                "report_index": int(card["report_index"]),
                "run_id": decision_log.run_id,
                "skill_id": decision_log.skill_id,
                "created_at": decision_log.created_at,
                "channel": card.get("channel"),
                "title": card["title"],
                "summary": card["summary"],
                "metrics": card.get("metrics") or [],
                "tags": card.get("tags") or [],
                "updated_at": now_bjt(),
            }
        )
    return values


async def sync_report_cards_for_decision_log(db: AsyncSession, decision_log: DecisionLog) -> int:
    """Upsert flattened report cards for a decision log.

    This keeps inbox list queries on a small projection table while leaving the
    original DecisionLog JSON untouched for detail/debug views.
    """
    if not await _inbox_report_card_table_available(db):
        return 0
    await db.execute(
        delete(InboxReportCard).where(InboxReportCard.decision_log_id == int(decision_log.id))
    )
    values = _projection_values_for_decision_log(decision_log)
    if not values:
        return 0
    stmt = pg_insert(InboxReportCard).values(values)
    update_values = {
        "run_id": stmt.excluded.run_id,
        "skill_id": stmt.excluded.skill_id,
        "created_at": stmt.excluded.created_at,
        "channel": stmt.excluded.channel,
        "title": stmt.excluded.title,
        "summary": stmt.excluded.summary,
        "metrics": stmt.excluded.metrics,
        "tags": stmt.excluded.tags,
        "updated_at": stmt.excluded.updated_at,
    }
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["decision_log_id", "report_index"],
            set_=update_values,
        )
    )
    return len(values)


async def sync_missing_report_cards_for_window(
    db: AsyncSession,
    *,
    date_from: datetime | None,
    date_to: datetime | None,
    skill_id: str | None = None,
    visible_skill_ids: list[str] | None = None,
    limit: int = 100,
) -> int:
    """Repair recent projection misses before reading the report list.

    Normal execution paths write the projection immediately. This is a narrow
    safety net for legacy/manual DecisionLog inserts so newly generated reports
    still surface without waiting for a separate maintenance job.
    """
    if not await _inbox_report_card_table_available(db):
        return 0

    latest_projected_at = await db.scalar(select(func.max(InboxReportCard.created_at)))
    if latest_projected_at and date_from:
        repair_from = max(date_from, latest_projected_at - timedelta(hours=6))
    elif latest_projected_at:
        repair_from = latest_projected_at - timedelta(hours=6)
    else:
        repair_from = date_from

    filters = [
        "dl.is_sandbox = false",
        """
        NOT EXISTS (
            SELECT 1
            FROM inbox_report_cards irc
            WHERE irc.decision_log_id = dl.id
        )
        """,
    ]
    params: dict[str, Any] = {"limit": max(1, min(int(limit or 100), 500))}
    if repair_from:
        filters.append("dl.created_at >= :date_from")
        params["date_from"] = repair_from
    if date_to:
        filters.append("dl.created_at <= :date_to")
        params["date_to"] = date_to
    if skill_id:
        filters.append("dl.skill_id = :skill_id")
        params["skill_id"] = skill_id
    if visible_skill_ids is not None:
        if not visible_skill_ids:
            return 0
        filters.append("dl.skill_id IN :visible_skill_ids")
        params["visible_skill_ids"] = visible_skill_ids

    stmt = text(
        f"""
        SELECT dl.id
        FROM decision_log dl
        WHERE {" AND ".join(filters)}
        ORDER BY dl.created_at DESC, dl.id DESC
        LIMIT :limit
        """
    )
    if visible_skill_ids is not None:
        stmt = stmt.bindparams(bindparam("visible_skill_ids", expanding=True))
    log_ids = [int(item) for item in (await db.execute(stmt, params)).scalars().all()]
    if not log_ids:
        return 0

    logs = (
        await db.execute(
            select(DecisionLog)
            .where(DecisionLog.id.in_(log_ids))
            .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
        )
    ).scalars().all()
    synced = 0
    for log in logs:
        synced += await sync_report_cards_for_decision_log(db, log)
    if synced:
        await db.flush()
    return synced


async def _cache_get_safe(key: str) -> Any | None:
    try:
        return await cache_mod.cache_get(key)
    except Exception:
        return None


async def _cache_set_safe(key: str, value: Any, ttl: int) -> None:
    try:
        await cache_mod.cache_set(key, value, ttl=ttl)
    except Exception:
        return


async def _cache_delete_pattern_safe(pattern: str) -> int:
    try:
        return await cache_mod.cache_delete_pattern(pattern)
    except Exception:
        return 0


async def _cache_delete_safe(key: str) -> None:
    try:
        await cache_mod.cache_delete(key)
    except Exception:
        return


async def invalidate_inbox_reports_cache() -> int:
    return await _cache_delete_pattern_safe("inbox:reports:*")


async def invalidate_inbox_visible_skill_cache(user_id: str | None = None) -> int:
    if user_id:
        await _cache_delete_safe(f"inbox:visible_skill_ids:{user_id}")
        return 1
    return await _cache_delete_pattern_safe("inbox:visible_skill_ids:*")


async def invalidate_inbox_permissions_cache(user_id: str | None = None) -> None:
    await invalidate_inbox_reports_cache()
    await invalidate_inbox_visible_skill_cache(user_id)


def _path_is_in_lineage(user_path: str, target_path: str) -> bool:
    """判断用户部门路径与目标（Skill 所属）部门路径是否在同一上下继承链。

    Org path 形如 "RT/EC/SUB"，直接用 `startswith` 会把 "RT/EC" 误匹配到
    "RT/ECX"。统一在两侧补尾斜杠再比较，保证必须按完整段匹配。
    """
    if not user_path or not target_path:
        return False
    u = user_path if user_path.endswith("/") else f"{user_path}/"
    t = target_path if target_path.endswith("/") else f"{target_path}/"
    return u.startswith(t) or t.startswith(u)


async def _compute_visible_skill_ids(db: AsyncSession, user: User) -> list[str]:
    if not _is_active_user(user):
        return []

    # 用户 membership 先算好，后面既要计算候选 org_ids 也要决定 dept_admin 的管辖范围
    membership_rows = (
        await db.execute(
            select(UserOrgMembership.org_unit_id, UserOrgMembership.is_manager).where(
                UserOrgMembership.user_id == user.id
            )
        )
    ).all()
    user_org_ids = {org_unit_id for org_unit_id, _ in membership_rows if org_unit_id}
    managed_org_ids = {
        org_unit_id
        for org_unit_id, is_manager in membership_rows
        if org_unit_id and bool(is_manager)
    }

    # dept_admin 读权：管辖部门向下展开到整棵子树（spec role-matrix-v2 §3.2）
    role = getattr(user, "role", "") or ""
    managed_org_subtree: set[str] = set(managed_org_ids)
    if managed_org_ids and role == "dept_admin":
        managed_org_subtree = await expand_department_subtree(db, managed_org_ids)

    member_skill_ids = set(
        (
            await db.execute(
                select(SkillMember.skill_id).where(SkillMember.user_id == user.id)
            )
        ).scalars().all()
    )
    user_department = str(getattr(user, "department", None) or "").strip()

    # Department 可见性按完整 lineage 继承（祖先/自身/子孙都算可见），把 user_org_ids
    # 展开到 lineage 后再做 SQL IN 预筛，相比原来 SELECT 全表大幅降低冷调数据量。
    lineage_org_ids = await expand_org_lineage(db, user_org_ids)
    candidate_org_ids = lineage_org_ids | managed_org_subtree
    candidate_org_names: set[str] = set()
    if candidate_org_ids:
        candidate_org_names = set(
            (
                await db.execute(
                    select(OrgUnit.name).where(OrgUnit.id.in_(candidate_org_ids))
                )
            ).scalars().all()
        )
    if user_department:
        candidate_org_names.add(user_department)

    skill_filters = [Skill.visibility == "company"]
    dept_filters = []
    if candidate_org_ids:
        dept_filters.append(Skill.org_unit_id.in_(candidate_org_ids))
    if candidate_org_names:
        dept_filters.append(Skill.department.in_(candidate_org_names))
    if dept_filters:
        skill_filters.append((Skill.visibility == "department") & or_(*dept_filters))
    if member_skill_ids:
        skill_filters.append(Skill.id.in_(member_skill_ids))
    # dept_admin 在管辖子树内可见全部 visibility（§3.2），需要把 private/department 都捞回来
    if role == "dept_admin" and managed_org_subtree:
        skill_filters.append(Skill.org_unit_id.in_(managed_org_subtree))

    skill_rows = (
        await db.execute(
            select(
                Skill.id,
                Skill.visibility,
                Skill.org_unit_id,
                Skill.department,
            ).where(or_(*skill_filters))
        )
    ).all()
    skills = [
        _VisibleSkillSeed(
            id=skill_id,
            visibility=visibility or "department",
            org_unit_id=org_unit_id,
            department=department,
        )
        for skill_id, visibility, org_unit_id, department in skill_rows
    ]
    if not skills:
        return []

    # 部门 path 映射：只查需要的 org（用户关联 + Skill 目标），避免全表
    org_ids = user_org_ids | {item.org_unit_id for item in skills if item.org_unit_id}
    org_path_map: dict[str, str | None] = {}
    if org_ids:
        org_rows = (
            await db.execute(
                select(OrgUnit.id, OrgUnit.path).where(OrgUnit.id.in_(org_ids))
            )
        ).all()
        org_path_map = {org_id: path for org_id, path in org_rows}

    user_paths = {
        path
        for org_id in user_org_ids
        if (path := org_path_map.get(org_id))
    }

    visible_skill_ids: list[str] = []
    for skill in skills:
        if skill.visibility == "company":
            visible_skill_ids.append(skill.id)
            continue
        if role == "dept_admin":
            department_ref = skill.org_unit_id or skill.department
            # 管辖部门子树任意一级都可见（§3.2）
            if department_ref and (
                department_ref in managed_org_subtree
                or department_ref in candidate_org_names
            ):
                visible_skill_ids.append(skill.id)
                continue
        if skill.visibility == "department":
            if skill.department and skill.department in candidate_org_names:
                visible_skill_ids.append(skill.id)
                continue
            target_path = org_path_map.get(skill.org_unit_id or "")
            if target_path and any(
                _path_is_in_lineage(path, target_path) for path in user_paths
            ):
                visible_skill_ids.append(skill.id)
                continue
        if skill.id in member_skill_ids:
            visible_skill_ids.append(skill.id)

    return visible_skill_ids


async def get_visible_skill_ids_cached(db: AsyncSession, user: User) -> list[str] | None:
    if _has_global_read_scope(user):
        return None
    if not _is_active_user(user):
        return []

    cache_key = f"inbox:visible_skill_ids:{user.id}"
    cached = await _cache_get_safe(cache_key)
    if isinstance(cached, list):
        return [str(item) for item in cached]

    skill_ids = await _compute_visible_skill_ids(db, user)
    await _cache_set_safe(cache_key, skill_ids, ttl=_VISIBLE_SKILL_IDS_TTL_SECONDS)
    return skill_ids


def _build_list_ctes(
    *,
    include_visible_skill_filter: bool,
    skill_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    tag: str | None,
    channel: str | None,
    trigger_type: str | None,
) -> str:
    base_filters = [
        "dl.is_sandbox = false",
        "jsonb_typeof(dl.output_result -> 'reports') = 'array'",
        "jsonb_array_length(dl.output_result -> 'reports') > 0",
    ]
    filtered_filters = [
        "jsonb_typeof(fr.report_json) = 'object'",
        "NULLIF(BTRIM(COALESCE(fr.report_json ->> 'title', '')), '') IS NOT NULL",
        "NULLIF(BTRIM(COALESCE(fr.report_json ->> 'summary', '')), '') IS NOT NULL",
    ]
    if include_visible_skill_filter:
        base_filters.append("dl.skill_id IN :visible_skill_ids")
    if skill_id:
        base_filters.append("dl.skill_id = :skill_id")
    if date_from:
        base_filters.append("dl.created_at >= :date_from")
    if date_to:
        base_filters.append("dl.created_at <= :date_to")
    if channel:
        filtered_filters.append("fr.report_json ->> 'channel' = :channel")
    if tag:
        filtered_filters.append(
            """
            CASE
                WHEN jsonb_typeof(fr.report_json -> 'tags') = 'array'
                    THEN fr.report_json -> 'tags'
                ELSE '[]'::jsonb
            END @> CAST(:tag_json AS jsonb)
            """
        )
    if trigger_type:
        filtered_filters.append("er_filter.trigger_type = :trigger_type")

    return f"""
        WITH base_logs AS (
            SELECT
                dl.id,
                dl.skill_id,
                dl.run_id,
                dl.created_at,
                dl.output_result
            FROM decision_log dl
            WHERE {" AND ".join(base_filters)}
        ),
        flat_reports AS (
            SELECT
                bl.id AS decision_log_id,
                bl.skill_id,
                bl.run_id,
                bl.created_at,
                rep.ordinality - 1 AS report_index,
                rep.report AS report_json
            FROM base_logs bl
            CROSS JOIN LATERAL jsonb_array_elements(bl.output_result -> 'reports')
                WITH ORDINALITY AS rep(report, ordinality)
        ),
        filtered_reports AS (
            SELECT
                fr.decision_log_id,
                fr.report_index,
                fr.skill_id,
                fr.run_id,
                fr.created_at,
                fr.report_json
            FROM flat_reports fr
            LEFT JOIN execution_runs er_filter ON er_filter.id = fr.run_id
            WHERE {" AND ".join(filtered_filters)}
        ),
        request_counts AS (
            SELECT
                dr.decision_log_id,
                COUNT(*) AS related_todo_count,
                COUNT(*) FILTER (
                    WHERE dr.aggregate_status = 'pending'
                ) AS related_pending_request_count
            FROM decision_requests dr
            WHERE dr.decision_log_id IS NOT NULL
            GROUP BY dr.decision_log_id
        )
    """


def _build_list_sql(
    *,
    include_visible_skill_filter: bool,
    include_pagination: bool,
    include_total: bool = False,
    skill_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    tag: str | None,
    channel: str | None,
    trigger_type: str | None,
) -> Any:
    total_select = "COUNT(*) OVER() AS total_count," if include_total else ""
    select_sql = """
        SELECT
            {total_select}
            fr.decision_log_id,
            fr.report_index,
            fr.skill_id,
            fr.run_id,
            fr.created_at,
            s.name AS skill_name,
            s.department AS skill_department,
            er.trigger_type,
            fr.report_json ->> 'channel' AS channel,
            fr.report_json ->> 'title' AS title,
            fr.report_json ->> 'summary' AS summary,
            CASE
                WHEN jsonb_typeof(fr.report_json -> 'metrics') = 'array'
                    THEN fr.report_json -> 'metrics'
                ELSE '[]'::jsonb
            END AS metrics,
            CASE
                WHEN jsonb_typeof(fr.report_json -> 'tags') = 'array'
                    THEN fr.report_json -> 'tags'
                ELSE '[]'::jsonb
            END AS tags,
            COALESCE(rc.related_todo_count, 0) AS related_todo_count,
            COALESCE(rc.related_pending_request_count, 0) AS related_pending_request_count
        FROM filtered_reports fr
        LEFT JOIN skills s ON s.id = fr.skill_id
        LEFT JOIN execution_runs er ON er.id = fr.run_id
        LEFT JOIN request_counts rc ON rc.decision_log_id = fr.decision_log_id
        ORDER BY fr.created_at DESC, fr.decision_log_id DESC, fr.report_index ASC
    """.format(total_select=total_select)
    if include_pagination:
        select_sql += "\nLIMIT :limit OFFSET :offset"

    stmt = text(
        _build_list_ctes(
            include_visible_skill_filter=include_visible_skill_filter,
            skill_id=skill_id,
            date_from=date_from,
            date_to=date_to,
            tag=tag,
            channel=channel,
            trigger_type=trigger_type,
        )
        + select_sql
    )
    if include_visible_skill_filter:
        stmt = stmt.bindparams(bindparam("visible_skill_ids", expanding=True))
    return stmt


def _build_simple_count_sql(
    *,
    include_visible_skill_filter: bool,
    skill_id: str | None,
) -> str:
    """全量计数 —— 不展开 JSON，直接走 partial index，极快。"""
    clauses = [
        "dl.is_sandbox = false",
        "jsonb_typeof(dl.output_result -> 'reports') = 'array'",
        "jsonb_array_length(dl.output_result -> 'reports') > 0",
    ]
    if include_visible_skill_filter:
        clauses.append("dl.skill_id IN :visible_skill_ids")
    if skill_id:
        clauses.append("dl.skill_id = :skill_id")
    return (
        "SELECT COUNT(*) AS total FROM decision_log dl"
        " WHERE " + " AND ".join(clauses)
    )


def _build_projection_list_sql(
    *,
    include_visible_skill_filter: bool,
    include_pagination: bool,
    include_total: bool = False,
    skill_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    tag: str | None,
    channel: str | None,
    trigger_type: str | None,
) -> Any:
    base_filters = ["1 = 1"]
    if include_visible_skill_filter:
        base_filters.append("irc.skill_id IN :visible_skill_ids")
    if skill_id:
        base_filters.append("irc.skill_id = :skill_id")
    if date_from:
        base_filters.append("irc.created_at >= :date_from")
    if date_to:
        base_filters.append("irc.created_at <= :date_to")
    if channel:
        base_filters.append("irc.channel = :channel")
    if tag:
        base_filters.append(
            """
            CASE
                WHEN jsonb_typeof(irc.tags) = 'array'
                    THEN irc.tags
                ELSE '[]'::jsonb
            END @> CAST(:tag_json AS jsonb)
            """
        )
    if trigger_type:
        base_filters.append("er.trigger_type = :trigger_type")

    total_select = "COUNT(*) OVER() AS total_count," if include_total else ""
    select_sql = """
        SELECT
            {total_select}
            irc.decision_log_id,
            irc.report_index,
            irc.skill_id,
            irc.run_id,
            irc.created_at,
            s.name AS skill_name,
            s.department AS skill_department,
            er.trigger_type,
            irc.channel,
            irc.title,
            irc.summary,
            COALESCE(irc.metrics, '[]'::jsonb) AS metrics,
            COALESCE(irc.tags, '[]'::jsonb) AS tags,
            COALESCE(rc.related_todo_count, 0) AS related_todo_count,
            COALESCE(rc.related_pending_request_count, 0) AS related_pending_request_count
        FROM inbox_report_cards irc
        LEFT JOIN skills s ON s.id = irc.skill_id
        LEFT JOIN execution_runs er ON er.id = irc.run_id
        LEFT JOIN (
            SELECT
                dr.decision_log_id,
                COUNT(*) AS related_todo_count,
                COUNT(*) FILTER (
                    WHERE dr.aggregate_status = 'pending'
                ) AS related_pending_request_count
            FROM decision_requests dr
            WHERE dr.decision_log_id IS NOT NULL
            GROUP BY dr.decision_log_id
        ) rc ON rc.decision_log_id = irc.decision_log_id
        WHERE {filters}
        ORDER BY irc.created_at DESC, irc.decision_log_id DESC, irc.report_index ASC
    """.format(total_select=total_select, filters=" AND ".join(base_filters))
    if include_pagination:
        select_sql += "\nLIMIT :limit OFFSET :offset"

    stmt = text(select_sql)
    if include_visible_skill_filter:
        stmt = stmt.bindparams(bindparam("visible_skill_ids", expanding=True))
    return stmt


async def _load_related_todos(
    db: AsyncSession,
    *,
    decision_log_id: int,
) -> list[dict[str, Any]]:
    request_rows = (
        await db.execute(
            select(DecisionRequest)
            .where(DecisionRequest.decision_log_id == decision_log_id)
            .order_by(DecisionRequest.created_at.desc(), DecisionRequest.id.asc())
        )
    ).scalars().all()
    if not request_rows:
        return []

    request_ids = [request.id for request in request_rows]
    todo_rows = (
        await db.execute(
            select(AITodo.request_id, AITodo.id, AITodo.assignee)
            .where(AITodo.request_id.in_(request_ids))
            .order_by(AITodo.request_id.asc(), AITodo.id.asc())
        )
    ).all()
    todo_map: dict[str, list[tuple[int, str]]] = {}
    for request_id, todo_id, assignee in todo_rows:
        todo_map.setdefault(request_id, []).append((todo_id, assignee))

    items: list[dict[str, Any]] = []
    for request in request_rows:
        todos = todo_map.get(request.id, [])
        items.append(
            {
                "id": todos[0][0] if todos else None,
                "request_id": request.id,
                "kind": request.kind,
                "title": request.title,
                "aggregate_status": request.aggregate_status,
                "sla_at": _to_iso(request.sla_at),
                "assignees": [assignee for _, assignee in todos],
            }
        )
    return items


async def _load_related_counts(
    db: AsyncSession,
    *,
    decision_log_id: int,
) -> tuple[int, int]:
    row = (
        await db.execute(
            select(
                func.count(DecisionRequest.id),
                func.count(DecisionRequest.id).filter(
                    DecisionRequest.aggregate_status == "pending"
                ),
            ).where(DecisionRequest.decision_log_id == decision_log_id)
        )
    ).one()
    total, pending = row
    return int(total or 0), int(pending or 0)


class InboxReportService:
    async def list_reports(
        self,
        db: AsyncSession,
        *,
        current_user: User,
        page: int = 1,
        page_size: int = 20,
        skill_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        tag: str | None = None,
        channel: str | None = None,
        trigger_type: str | None = None,
    ) -> dict[str, Any]:
        visible_skill_ids = await get_visible_skill_ids_cached(db, current_user)
        if visible_skill_ids == []:
            return {"total": 0, "page": page, "page_size": page_size, "items": []}
        if visible_skill_ids is not None and skill_id and skill_id not in set(visible_skill_ids):
            return {"total": 0, "page": page, "page_size": page_size, "items": []}

        parsed_date_from = _parse_datetime(date_from)
        parsed_date_to = _parse_datetime(date_to, end_of_day=True)
        # 列表默认近 90 天，配合 6 小时生成数据缓存，兼顾常用历史报告可见性与首刷性能
        list_date_from = parsed_date_from or (now_bjt() - timedelta(days=_DEFAULT_LIST_DAYS))

        params: dict[str, Any] = {
            "skill_id": skill_id,
            "date_from": list_date_from,
            "date_to": parsed_date_to,
            "channel": channel,
            "tag_json": json.dumps([tag], ensure_ascii=False) if tag else None,
            "trigger_type": trigger_type,
            "limit": page_size,
            "offset": max(page - 1, 0) * page_size,
        }
        if visible_skill_ids is not None:
            params["visible_skill_ids"] = visible_skill_ids

        if await _inbox_report_card_table_available(db):
            await sync_missing_report_cards_for_window(
                db,
                date_from=list_date_from,
                date_to=parsed_date_to,
                skill_id=skill_id,
                visible_skill_ids=visible_skill_ids,
                limit=max(page_size * 3, 50),
            )
        use_projection = await _inbox_report_card_projection_ready(db)
        build_list_stmt = _build_projection_list_sql if use_projection else _build_list_sql

        # total 与列表窗口保持一致；无日期时不再为 count 展开全量 decision_log JSON。
        list_stmt = build_list_stmt(
            include_visible_skill_filter=visible_skill_ids is not None,
            include_pagination=True,
            include_total=True,
            skill_id=skill_id,
            date_from=list_date_from,
            date_to=parsed_date_to,
            tag=tag,
            channel=channel,
            trigger_type=trigger_type,
        )
        rows = (await db.execute(list_stmt, params)).mappings().all()
        total = int(rows[0]["total_count"] or 0) if rows else 0
        if not rows and page > 1:
            if use_projection:
                count_stmt = _build_projection_list_sql(
                    include_visible_skill_filter=visible_skill_ids is not None,
                    include_pagination=False,
                    include_total=False,
                    skill_id=skill_id,
                    date_from=list_date_from,
                    date_to=parsed_date_to,
                    tag=tag,
                    channel=channel,
                    trigger_type=trigger_type,
                )
                count_wrapped = text(f"SELECT COUNT(*) AS total FROM ({count_stmt.text}) AS projected_reports")
                if visible_skill_ids is not None:
                    count_wrapped = count_wrapped.bindparams(bindparam("visible_skill_ids", expanding=True))
                count_stmt = count_wrapped
            else:
                count_stmt = text(
                    _build_list_ctes(
                        include_visible_skill_filter=visible_skill_ids is not None,
                        skill_id=skill_id,
                        date_from=list_date_from,
                        date_to=parsed_date_to,
                        tag=tag,
                        channel=channel,
                        trigger_type=trigger_type,
                    )
                    + "\nSELECT COUNT(*) AS total FROM filtered_reports"
                )
                if visible_skill_ids is not None:
                    count_stmt = count_stmt.bindparams(bindparam("visible_skill_ids", expanding=True))
            total = int((await db.execute(count_stmt, params)).scalar() or 0)
        items = [
            {
                "id": f"{int(row['decision_log_id'])}-{int(row['report_index'])}",
                "decision_log_id": int(row["decision_log_id"]),
                "report_index": int(row["report_index"]),
                "run_id": row["run_id"],
                "skill_id": row["skill_id"],
                "skill_name": row["skill_name"],
                "skill_department": row["skill_department"],
                "channel": (row["channel"] or "").strip() or None,
                "title": str(row["title"] or "").strip()[:200],
                "summary": str(row["summary"] or "").strip()[:800],
                "metrics": _normalize_metrics(row["metrics"]),
                "tags": _normalize_tags(row["tags"]),
                "trigger_type": row["trigger_type"],
                "related_todo_count": int(row["related_todo_count"] or 0),
                "related_pending_request_count": int(
                    row["related_pending_request_count"] or 0
                ),
                "created_at": _to_iso(row["created_at"]),
            }
            for row in rows
        ]
        return {"total": total, "page": page, "page_size": page_size, "items": items}

    async def get_report_detail(
        self,
        db: AsyncSession,
        *,
        card_id: str,
        current_user: User,
        include_debug: bool = False,
    ) -> dict[str, Any]:
        decision_log_id, report_index = parse_report_card_id(card_id)
        log = await db.get(DecisionLog, decision_log_id)
        if not log or bool(log.is_sandbox):
            raise AppError("REPORT_NOT_FOUND", 404)

        skill = await require_skill_access(db, log.skill_id, current_user, "read")
        report_cards = extract_report_cards(log.id, log.output_result)
        report = next(
            (item for item in report_cards if item["report_index"] == report_index),
            None,
        )
        if report is None:
            raise AppError("REPORT_NOT_FOUND", 404)

        execution_run = await db.get(ExecutionRun, log.run_id) if log.run_id else None
        related_todo_count, related_pending_request_count = await _load_related_counts(
            db,
            decision_log_id=log.id,
        )
        related_todos = await _load_related_todos(db, decision_log_id=log.id)

        detail = {
            "id": report["id"],
            "decision_log_id": log.id,
            "report_index": report["report_index"],
            "run_id": log.run_id,
            "skill_id": log.skill_id,
            "skill_name": skill.name,
            "skill_department": skill.department,
            "channel": report["channel"],
            "title": report["title"],
            "summary": report["summary"],
            "metrics": report["metrics"],
            "tags": report["tags"],
            "trigger_type": execution_run.trigger_type if execution_run else None,
            "related_todo_count": related_todo_count,
            "related_pending_request_count": related_pending_request_count,
            "created_at": _to_iso(log.created_at),
            "content_markdown": report["content_markdown"],
            "payload": report["payload"],
            "report_design": report.get("report_design"),
            "related_todos": related_todos,
            "debug_context": None,
        }
        if include_debug and _can_view_debug_context(current_user):
            detail["debug_context"] = {
                "raw_output_result": log.output_result if isinstance(log.output_result, dict) else None,
                "input_snapshot": log.input_snapshot if isinstance(log.input_snapshot, dict) else None,
            }
        return detail


inbox_service = InboxReportService()
