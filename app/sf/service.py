"""SF usage dashboard service.

This module reads existing Codex/SF audit data and Skill run report outputs. It
never calls external MCP tools; it only summarizes platform-owned audit rows.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import role_matches_any
from app.auth.models import User
from app.codex import service as codex_service
from app.codex.models import CodexCliSession, CodexDebugRun, CodexMcpCallAudit
from app.common.exceptions import AppError
from app.common.audit import AuditLog
from app.common.time_utils import isoformat_bjt, now_bjt
from app.execution.models import DecisionLog, ExecutionRun
from app.inbox.models import InboxReportCard, UserInboxPreference
from app.inbox.service import extract_report_cards
from app.skills.core.models import Skill
from app.todos.models import DecisionRequest
from app.sf.data_service import list_sf_data_records


_GLOBAL_ROLES = ("admin", "system_admin")
_REPORT_LOG_LIMIT = 800
_EVENT_FETCH_LIMIT = 1000


def _is_global_scope_user(user: User) -> bool:
    return role_matches_any(user, _GLOBAL_ROLES) or bool(getattr(user, "can_view_all", False))


def _scope_user_id(user: User) -> str | None:
    return None if _is_global_scope_user(user) else str(getattr(user, "id", "") or "")


def _clean_filter(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _to_iso(value: datetime | None) -> str | None:
    return isoformat_bjt(value) if value else None


def _day_key(value: datetime | None) -> str | None:
    return value.date().isoformat() if value else None


def _text_blob(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("id", "kind", "user_id", "user_name", "username", "action", "target_id", "tool", "run_mode", "skill_id", "shop_id", "data_scope", "error_code", "title", "summary", "channel", "skill_name"):
        value = item.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    tags = item.get("tags")
    if isinstance(tags, list):
        parts.extend(str(tag) for tag in tags if tag not in (None, ""))
    detail = item.get("detail")
    if isinstance(detail, dict):
        parts.extend(str(value) for value in detail.values() if isinstance(value, (str, int, float)))
    return "\n".join(parts).lower()


def _matches_query(item: dict[str, Any], query: str | None) -> bool:
    if not query:
        return True
    return query.lower() in _text_blob(item)


def _user_display_map(rows: list[User]) -> dict[str, dict[str, Any]]:
    return {
        row.id: {
            "user_id": row.id,
            "user_name": row.name,
            "username": row.username,
            "department": row.department,
            "role": row.role,
        }
        for row in rows
    }


def _attach_user_display(item: dict[str, Any], user_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    user = user_map.get(str(item.get("user_id") or ""))
    if not user:
        return {
            **item,
            "user_name": item.get("user_id"),
            "username": None,
            "department": None,
            "role": None,
        }
    return {**item, **user}


def _event_from_audit(row: AuditLog) -> dict[str, Any]:
    detail = row.detail if isinstance(row.detail, dict) else {}
    return {
        "id": f"audit:{row.id}",
        "kind": "api",
        "user_id": row.user_id,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "tool": None,
        "ok": True,
        "dry_run": None,
        "run_mode": detail.get("run_mode"),
        "skill_id": detail.get("skill_id") or (row.target_id if row.target_type == "skill" else None),
        "shop_id": detail.get("shop_id"),
        "data_scope": detail.get("data_scope"),
        "detail": detail,
        "created_at": _to_iso(row.created_at),
        "created_day": _day_key(row.created_at),
    }


def _event_from_mcp(row: CodexMcpCallAudit) -> dict[str, Any]:
    detail = dict(row.detail_json or {})
    if row.error_code and "error_code" not in detail:
        detail["error_code"] = row.error_code
    return {
        "id": f"mcp:{row.id}",
        "kind": "mcp",
        "user_id": row.user_id,
        "action": "codex.mcp.call",
        "target_type": "mcp_tool",
        "target_id": row.tool,
        "tool": row.tool,
        "ok": bool(row.ok),
        "dry_run": bool(row.dry_run),
        "run_mode": row.run_mode,
        "skill_id": row.skill_id,
        "shop_id": row.shop_id,
        "data_scope": row.data_scope,
        "error_code": row.error_code,
        "detail": detail,
        "created_at": _to_iso(row.created_at),
        "created_day": _day_key(row.created_at),
    }


async def _load_user_map(db: AsyncSession, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    user_ids = sorted({str(item.get("user_id")) for item in items if item.get("user_id")})
    if not user_ids:
        return {}
    rows = list((await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all())
    return _user_display_map(rows)


def _effective_user_filter(scope_user_id: str | None, user_id: str | None) -> str | None:
    return scope_user_id or _clean_filter(user_id)


async def _reports(
    db: AsyncSession,
    *,
    cutoff: datetime,
    scope_user_id: str | None,
    user_id: str | None,
    skill_id: str | None,
    query: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    trigger_user = _effective_user_filter(scope_user_id, user_id)
    trigger_filter = ExecutionRun.trigger_type.like("codex:%")
    if trigger_user:
        trigger_filter = ExecutionRun.trigger_type == f"codex:{trigger_user}"

    skill_filter = _clean_filter(skill_id)
    projected_count, projected_items = await _projected_reports(
        db,
        cutoff=cutoff,
        trigger_filter=trigger_filter,
        skill_id=skill_filter,
        query=query,
    )
    if projected_count:
        return projected_count, projected_items

    return await _reports_from_decision_logs(
        db,
        cutoff=cutoff,
        trigger_filter=trigger_filter,
        skill_id=skill_filter,
        query=query,
    )


async def _projected_reports(
    db: AsyncSession,
    *,
    cutoff: datetime,
    trigger_filter: Any,
    skill_id: str | None,
    query: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    stmt = (
        select(
            InboxReportCard,
            ExecutionRun.trigger_type,
            ExecutionRun.status,
            Skill.display_name,
            Skill.name,
        )
        .join(ExecutionRun, ExecutionRun.id == InboxReportCard.run_id)
        .outerjoin(Skill, Skill.id == InboxReportCard.skill_id)
        .where(
            InboxReportCard.created_at >= cutoff,
            trigger_filter,
        )
        .order_by(InboxReportCard.created_at.desc(), InboxReportCard.id.desc())
    )
    if skill_id:
        stmt = stmt.where(InboxReportCard.skill_id == skill_id)

    rows = (await db.execute(stmt.limit(_REPORT_LOG_LIMIT))).all()
    items: list[dict[str, Any]] = []
    for card, trigger_type, run_status, display_name, name in rows:
        report = {
            "id": f"{card.decision_log_id}-{card.report_index}",
            "decision_log_id": card.decision_log_id,
            "report_index": card.report_index,
            "run_id": card.run_id,
            "skill_id": card.skill_id,
            "skill_name": display_name or name or card.skill_id,
            "title": card.title,
            "summary": card.summary,
            "channel": card.channel,
            "tags": card.tags or [],
            "metrics": card.metrics or [],
            "created_at": _to_iso(card.created_at),
            "created_day": _day_key(card.created_at),
            "trigger_type": trigger_type,
            "run_status": run_status,
        }
        if _matches_query(report, query):
            items.append(report)
    return len(items), items


async def _reports_from_decision_logs(
    db: AsyncSession,
    *,
    cutoff: datetime,
    trigger_filter: Any,
    skill_id: str | None,
    query: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    stmt = (
        select(DecisionLog, ExecutionRun)
        .join(ExecutionRun, ExecutionRun.id == DecisionLog.run_id)
        .where(
            DecisionLog.created_at >= cutoff,
            DecisionLog.output_result.is_not(None),
            trigger_filter,
        )
        .order_by(DecisionLog.created_at.desc())
        .limit(_REPORT_LOG_LIMIT)
    )
    if skill_id:
        stmt = stmt.where(DecisionLog.skill_id == skill_id)

    rows = (await db.execute(stmt)).all()

    skill_ids = sorted({decision.skill_id for decision, _run in rows if decision.skill_id})
    skill_names: dict[str, str] = {}
    if skill_ids:
        skill_rows = list((await db.execute(select(Skill).where(Skill.id.in_(skill_ids)))).scalars().all())
        skill_names = {skill.id: (skill.display_name or skill.name or skill.id) for skill in skill_rows}

    items: list[dict[str, Any]] = []
    for decision, run in rows:
        for card in extract_report_cards(decision.id, decision.output_result):
            report = {
                "id": card["id"],
                "decision_log_id": card["decision_log_id"],
                "report_index": card["report_index"],
                "run_id": decision.run_id,
                "skill_id": decision.skill_id,
                "skill_name": skill_names.get(decision.skill_id, decision.skill_id),
                "title": card.get("title"),
                "summary": card.get("summary"),
                "channel": card.get("channel"),
                "tags": card.get("tags") or [],
                "metrics": card.get("metrics") or [],
                "created_at": _to_iso(decision.created_at),
                "created_day": _day_key(decision.created_at),
                "trigger_type": run.trigger_type,
                "run_status": run.status,
            }
            if _matches_query(report, query):
                items.append(report)
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return len(items), items


async def _summary(
    db: AsyncSession,
    *,
    days: int,
    cutoff: datetime,
    scope_user_id: str | None,
    user_id: str | None,
    skill_id: str | None,
    report_count: int,
) -> dict[str, Any]:
    active_now = now_bjt()
    filter_user = _effective_user_filter(scope_user_id, user_id)
    filter_skill = _clean_filter(skill_id)

    plugin_users_stmt = select(func.count(func.distinct(CodexCliSession.user_id)))
    active_sessions_stmt = select(func.count(CodexCliSession.id)).where(
        CodexCliSession.revoked_at.is_(None),
        CodexCliSession.expires_at > active_now,
    )
    api_calls_stmt = select(func.count(AuditLog.id)).where(
        AuditLog.action.like("codex.%"),
        AuditLog.action != "codex.mcp.call",
        AuditLog.created_at >= cutoff,
    )
    mcp_calls_stmt = select(func.count(CodexMcpCallAudit.id)).where(CodexMcpCallAudit.created_at >= cutoff)
    failed_mcp_calls_stmt = select(func.count(CodexMcpCallAudit.id)).where(
        CodexMcpCallAudit.created_at >= cutoff,
        CodexMcpCallAudit.ok.is_(False),
    )
    debug_runs_stmt = select(func.count(CodexDebugRun.id)).where(CodexDebugRun.created_at >= cutoff)

    if filter_user:
        plugin_users_stmt = plugin_users_stmt.where(CodexCliSession.user_id == filter_user)
        active_sessions_stmt = active_sessions_stmt.where(CodexCliSession.user_id == filter_user)
        api_calls_stmt = api_calls_stmt.where(AuditLog.user_id == filter_user)
        mcp_calls_stmt = mcp_calls_stmt.where(CodexMcpCallAudit.user_id == filter_user)
        failed_mcp_calls_stmt = failed_mcp_calls_stmt.where(CodexMcpCallAudit.user_id == filter_user)
        debug_runs_stmt = debug_runs_stmt.where(CodexDebugRun.user_id == filter_user)
    if filter_skill:
        api_calls_stmt = api_calls_stmt.where(AuditLog.target_id == filter_skill)
        mcp_calls_stmt = mcp_calls_stmt.where(CodexMcpCallAudit.skill_id == filter_skill)
        failed_mcp_calls_stmt = failed_mcp_calls_stmt.where(CodexMcpCallAudit.skill_id == filter_skill)
        debug_runs_stmt = debug_runs_stmt.where(CodexDebugRun.skill_id == filter_skill)

    plugin_users = int((await db.execute(plugin_users_stmt)).scalar() or 0)
    active_sessions = int((await db.execute(active_sessions_stmt)).scalar() or 0)
    api_calls = int((await db.execute(api_calls_stmt)).scalar() or 0)
    mcp_calls = int((await db.execute(mcp_calls_stmt)).scalar() or 0)
    failed_mcp_calls = int((await db.execute(failed_mcp_calls_stmt)).scalar() or 0)
    debug_runs = int((await db.execute(debug_runs_stmt)).scalar() or 0)
    success_rate = round(((mcp_calls - failed_mcp_calls) / mcp_calls) * 100, 1) if mcp_calls else None

    return {
        "days": days,
        "scope": "personal" if scope_user_id else "global",
        "sf_users": plugin_users,
        "plugin_users": plugin_users,
        "active_sessions": active_sessions,
        "api_calls": api_calls,
        "mcp_calls": mcp_calls,
        "failed_mcp_calls": failed_mcp_calls,
        "debug_runs": debug_runs,
        "report_count": report_count,
        "total_calls": api_calls + mcp_calls,
        "success_rate": success_rate,
        "period_start_at": _to_iso(cutoff),
        "generated_at": _to_iso(active_now),
    }


async def _top_tools(
    db: AsyncSession,
    *,
    cutoff: datetime,
    scope_user_id: str | None,
    user_id: str | None,
    skill_id: str | None,
) -> list[dict[str, Any]]:
    stmt = (
        select(
            CodexMcpCallAudit.tool,
            func.count(CodexMcpCallAudit.id).label("count"),
            func.sum(case((CodexMcpCallAudit.ok.is_(False), 1), else_=0)).label("failed_count"),
            func.max(CodexMcpCallAudit.created_at).label("last_called_at"),
        )
        .where(CodexMcpCallAudit.created_at >= cutoff)
        .group_by(CodexMcpCallAudit.tool)
        .order_by(func.count(CodexMcpCallAudit.id).desc(), func.max(CodexMcpCallAudit.created_at).desc())
        .limit(20)
    )
    filter_user = _effective_user_filter(scope_user_id, user_id)
    filter_skill = _clean_filter(skill_id)
    if filter_user:
        stmt = stmt.where(CodexMcpCallAudit.user_id == filter_user)
    if filter_skill:
        stmt = stmt.where(CodexMcpCallAudit.skill_id == filter_skill)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "tool": tool,
            "count": int(count or 0),
            "failed_count": int(failed_count or 0),
            "success_count": int(count or 0) - int(failed_count or 0),
            "last_called_at": _to_iso(last_called_at),
        }
        for tool, count, failed_count, last_called_at in rows
    ]


async def _recent_events(
    db: AsyncSession,
    *,
    cutoff: datetime,
    scope_user_id: str | None,
    page: int,
    page_size: int,
    kind: str | None,
    tool: str | None,
    user_id: str | None,
    skill_id: str | None,
    query: str | None,
) -> dict[str, Any]:
    offset = (page - 1) * page_size
    items: list[dict[str, Any]] = []
    filter_user = _effective_user_filter(scope_user_id, user_id)
    filter_tool = _clean_filter(tool)
    filter_skill = _clean_filter(skill_id)
    filter_kind = _clean_filter(kind)

    if filter_kind in {None, "api"}:
        api_stmt = select(AuditLog).where(
            AuditLog.action.like("codex.%"),
            AuditLog.action != "codex.mcp.call",
            AuditLog.created_at >= cutoff,
        )
        if filter_user:
            api_stmt = api_stmt.where(AuditLog.user_id == filter_user)
        api_rows = (await db.execute(api_stmt.order_by(AuditLog.created_at.desc()).limit(_EVENT_FETCH_LIMIT))).scalars().all()
        items.extend(_event_from_audit(row) for row in api_rows)
    if filter_kind in {None, "mcp"}:
        mcp_stmt = select(CodexMcpCallAudit).where(CodexMcpCallAudit.created_at >= cutoff)
        if filter_user:
            mcp_stmt = mcp_stmt.where(CodexMcpCallAudit.user_id == filter_user)
        if filter_tool:
            mcp_stmt = mcp_stmt.where(CodexMcpCallAudit.tool == filter_tool)
        if filter_skill:
            mcp_stmt = mcp_stmt.where(CodexMcpCallAudit.skill_id == filter_skill)
        mcp_rows = (await db.execute(mcp_stmt.order_by(CodexMcpCallAudit.created_at.desc()).limit(_EVENT_FETCH_LIMIT))).scalars().all()
        items.extend(_event_from_mcp(row) for row in mcp_rows)

    if filter_skill:
        items = [item for item in items if str(item.get("skill_id") or "") == filter_skill]
    if filter_tool:
        items = [item for item in items if str(item.get("tool") or "") == filter_tool]
    items = [item for item in items if _matches_query(item, query)]

    user_map = await _load_user_map(db, items)
    items = [_attach_user_display(item, user_map) for item in items]
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {"total": len(items), "page": page, "page_size": page_size, "items": items[offset : offset + page_size]}


async def _daily_usage(
    db: AsyncSession,
    *,
    days: int,
    cutoff: datetime,
    scope_user_id: str | None,
    user_id: str | None,
    skill_id: str | None,
    reports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    today = now_bjt().date()
    start_date = cutoff.date()
    buckets: dict[str, dict[str, Any]] = {}
    for idx in range((today - start_date).days + 1):
        day = (start_date + timedelta(days=idx)).isoformat()
        buckets[day] = {"day": day, "api_calls": 0, "mcp_calls": 0, "failed_mcp_calls": 0, "report_count": 0, "total_calls": 0}

    filter_user = _effective_user_filter(scope_user_id, user_id)
    filter_skill = _clean_filter(skill_id)
    api_stmt = select(AuditLog.created_at).where(
        AuditLog.action.like("codex.%"),
        AuditLog.action != "codex.mcp.call",
        AuditLog.created_at >= cutoff,
    )
    if filter_user:
        api_stmt = api_stmt.where(AuditLog.user_id == filter_user)
    if filter_skill:
        api_stmt = api_stmt.where(AuditLog.target_id == filter_skill)
    api_rows = (await db.execute(api_stmt.limit(_EVENT_FETCH_LIMIT * 2))).scalars().all()
    for created_at in api_rows:
        day = _day_key(created_at)
        if day in buckets:
            buckets[day]["api_calls"] += 1

    mcp_stmt = select(CodexMcpCallAudit.created_at, CodexMcpCallAudit.ok).where(CodexMcpCallAudit.created_at >= cutoff)
    if filter_user:
        mcp_stmt = mcp_stmt.where(CodexMcpCallAudit.user_id == filter_user)
    if filter_skill:
        mcp_stmt = mcp_stmt.where(CodexMcpCallAudit.skill_id == filter_skill)
    for created_at, ok in (await db.execute(mcp_stmt.limit(_EVENT_FETCH_LIMIT * 2))).all():
        day = _day_key(created_at)
        if day in buckets:
            buckets[day]["mcp_calls"] += 1
            if not ok:
                buckets[day]["failed_mcp_calls"] += 1

    for report in reports:
        day = str(report.get("created_day") or "")
        if day in buckets:
            buckets[day]["report_count"] += 1

    for item in buckets.values():
        item["total_calls"] = int(item["api_calls"] or 0) + int(item["mcp_calls"] or 0)
    return list(buckets.values())


def _version_tuple(value: str | None) -> tuple[int, ...]:
    import re

    parts = re.findall(r"\d+", str(value or "0"))
    return tuple(int(part) for part in parts[:4]) or (0,)


def _version_lt(left: str | None, right: str | None) -> bool:
    l_tuple = _version_tuple(left)
    r_tuple = _version_tuple(right)
    width = max(len(l_tuple), len(r_tuple))
    return l_tuple + (0,) * (width - len(l_tuple)) < r_tuple + (0,) * (width - len(r_tuple))


def _command_template(tool: dict[str, Any]) -> str:
    args: dict[str, Any] = {}
    if tool.get("requires_shop_id"):
        args["shop_id"] = "<shop_id>"
    if tool.get("write"):
        args["dry_run"] = True
    body = json.dumps(args, ensure_ascii=False)
    return f"sf mcp call {tool.get('name')} --args '{body}'"


def _manifest_payload() -> dict[str, Any]:
    # 延迟导入，避免 app.codex.router 与 app.sf.router 初始化时形成循环依赖。
    from app.codex.router import _catalog_manifest_payload

    return _catalog_manifest_payload()


async def _tool_stats(
    db: AsyncSession,
    *,
    cutoff: datetime,
    scope_user_id: str | None,
    user_id: str | None,
) -> dict[str, dict[str, Any]]:
    stmt = (
        select(
            CodexMcpCallAudit.tool,
            func.count(CodexMcpCallAudit.id).label("count"),
            func.sum(case((CodexMcpCallAudit.ok.is_(False), 1), else_=0)).label("failed_count"),
            func.max(CodexMcpCallAudit.created_at).label("last_called_at"),
        )
        .where(CodexMcpCallAudit.created_at >= cutoff)
        .group_by(CodexMcpCallAudit.tool)
    )
    filter_user = _effective_user_filter(scope_user_id, user_id)
    if filter_user:
        stmt = stmt.where(CodexMcpCallAudit.user_id == filter_user)
    rows = (await db.execute(stmt)).all()
    stats: dict[str, dict[str, Any]] = {}
    for tool, count, failed_count, last_called_at in rows:
        total = int(count or 0)
        failed = int(failed_count or 0)
        stats[str(tool)] = {
            "count": total,
            "failed_count": failed,
            "success_count": total - failed,
            "success_rate": round(((total - failed) / total) * 100, 1) if total else None,
            "last_called_at": _to_iso(last_called_at),
        }
    return stats


async def _health(
    db: AsyncSession,
    *,
    cutoff: datetime,
    scope_user_id: str | None,
    user_id: str | None,
    summary: dict[str, Any],
) -> dict[str, Any]:
    now = now_bjt()
    filter_user = _effective_user_filter(scope_user_id, user_id)
    manifest = _manifest_payload()
    update = manifest.get("plugin_update") if isinstance(manifest.get("plugin_update"), dict) else {}
    latest_version = str(update.get("latest_version") or update.get("version") or "")
    tool_names = {str(tool.get("name")) for tool in ((manifest.get("mcp_capabilities") or {}).get("tools") or []) if isinstance(tool, dict)}
    expected_tools = set(getattr(codex_service, "BUILTIN_MCP_TOOL_NAMES", set()) or set())
    missing_tools = sorted(tool for tool in expected_tools if tool and tool not in tool_names)

    session_stmt = select(CodexCliSession).where(CodexCliSession.created_at >= cutoff - timedelta(days=30))
    if filter_user:
        session_stmt = session_stmt.where(CodexCliSession.user_id == filter_user)
    sessions = list((await db.execute(session_stmt)).scalars().all())
    active_sessions = [row for row in sessions if not row.revoked_at and row.expires_at > now]
    expiring_sessions = [row for row in active_sessions if row.expires_at <= now + timedelta(days=3)]
    unknown_version = 0
    outdated = 0
    for row in active_sessions:
        meta = row.client_meta_json if isinstance(getattr(row, "client_meta_json", None), dict) else {}
        version = str(meta.get("sf_version") or meta.get("version") or "")
        if not version:
            unknown_version += 1
        elif latest_version and _version_lt(version, latest_version):
            outdated += 1

    alerts: list[dict[str, Any]] = []
    mcp_calls = int(summary.get("mcp_calls") or 0)
    failed = int(summary.get("failed_mcp_calls") or 0)
    if mcp_calls >= 3 and (failed / mcp_calls) >= 0.2:
        alerts.append({"level": "warning", "code": "high_mcp_failure_rate", "message": f"MCP 失败率 {round((failed / mcp_calls) * 100, 1)}%，需要排查工具参数或凭证。"})
    if not active_sessions:
        alerts.append({"level": "warning", "code": "no_active_session", "message": "当前范围没有活跃 sf CLI 会话。"})
    if missing_tools:
        alerts.append({"level": "danger", "code": "catalog_missing_tools", "message": f"MCP catalog 缺少 {len(missing_tools)} 个内置工具。"})
    if outdated:
        alerts.append({"level": "warning", "code": "outdated_cli", "message": f"{outdated} 个活跃会话低于推荐版本 {latest_version}。"})
    if unknown_version:
        alerts.append({"level": "info", "code": "unknown_cli_version", "message": f"{unknown_version} 个活跃会话缺少版本信息，建议升级 sf。"})
    if expiring_sessions:
        alerts.append({"level": "warning", "code": "token_expiring", "message": f"{len(expiring_sessions)} 个 CLI token 将在 3 天内过期。"})

    return {
        "latest_version": latest_version,
        "update_available": bool(update.get("update_available")),
        "active_sessions": len(active_sessions),
        "expiring_sessions": len(expiring_sessions),
        "version_unknown_sessions": unknown_version,
        "outdated_sessions": outdated,
        "missing_catalog_tools": missing_tools,
        "alerts": alerts,
    }


async def _failure_summary(
    db: AsyncSession,
    *,
    cutoff: datetime,
    scope_user_id: str | None,
    user_id: str | None,
    skill_id: str | None,
) -> dict[str, Any]:
    filter_user = _effective_user_filter(scope_user_id, user_id)
    filter_skill = _clean_filter(skill_id)
    base = CodexMcpCallAudit.created_at >= cutoff
    tool_stmt = (
        select(
            CodexMcpCallAudit.tool,
            func.count(CodexMcpCallAudit.id).label("count"),
            func.sum(case((CodexMcpCallAudit.ok.is_(False), 1), else_=0)).label("failed_count"),
            func.max(CodexMcpCallAudit.created_at).label("last_failed_at"),
        )
        .where(base)
        .group_by(CodexMcpCallAudit.tool)
        .order_by(func.sum(case((CodexMcpCallAudit.ok.is_(False), 1), else_=0)).desc(), func.count(CodexMcpCallAudit.id).desc())
        .limit(20)
    )
    error_stmt = (
        select(CodexMcpCallAudit.error_code, func.count(CodexMcpCallAudit.id), func.max(CodexMcpCallAudit.created_at))
        .where(base, CodexMcpCallAudit.ok.is_(False))
        .group_by(CodexMcpCallAudit.error_code)
        .order_by(func.count(CodexMcpCallAudit.id).desc())
        .limit(20)
    )
    recent_stmt = select(CodexMcpCallAudit).where(base, CodexMcpCallAudit.ok.is_(False)).order_by(CodexMcpCallAudit.created_at.desc()).limit(10)
    if filter_user:
        tool_stmt = tool_stmt.where(CodexMcpCallAudit.user_id == filter_user)
        error_stmt = error_stmt.where(CodexMcpCallAudit.user_id == filter_user)
        recent_stmt = recent_stmt.where(CodexMcpCallAudit.user_id == filter_user)
    if filter_skill:
        tool_stmt = tool_stmt.where(CodexMcpCallAudit.skill_id == filter_skill)
        error_stmt = error_stmt.where(CodexMcpCallAudit.skill_id == filter_skill)
        recent_stmt = recent_stmt.where(CodexMcpCallAudit.skill_id == filter_skill)
    tool_rows = (await db.execute(tool_stmt)).all()
    error_rows = (await db.execute(error_stmt)).all()
    recent = [_event_from_mcp(row) for row in (await db.execute(recent_stmt)).scalars().all()]
    user_map = await _load_user_map(db, recent)
    recent = [_attach_user_display(item, user_map) for item in recent]
    return {
        "by_tool": [
            {
                "tool": tool,
                "count": int(count or 0),
                "failed_count": int(failed or 0),
                "failure_rate": round((int(failed or 0) / int(count or 1)) * 100, 1),
                "last_failed_at": _to_iso(last_failed_at),
            }
            for tool, count, failed, last_failed_at in tool_rows
            if int(failed or 0) > 0
        ],
        "by_error_code": [
            {"error_code": code or "MCP_CALL_FAILED", "count": int(count or 0), "last_failed_at": _to_iso(last_failed_at)}
            for code, count, last_failed_at in error_rows
        ],
        "recent": recent,
    }


async def _rankings(
    db: AsyncSession,
    *,
    cutoff: datetime,
    scope_user_id: str | None,
    user_id: str | None,
) -> dict[str, Any]:
    filter_user = _effective_user_filter(scope_user_id, user_id)
    api_stmt = (
        select(AuditLog.user_id, func.count(AuditLog.id), func.max(AuditLog.created_at))
        .where(AuditLog.action.like("codex.%"), AuditLog.action != "codex.mcp.call", AuditLog.created_at >= cutoff)
        .group_by(AuditLog.user_id)
    )
    mcp_stmt = (
        select(CodexMcpCallAudit.user_id, func.count(CodexMcpCallAudit.id), func.max(CodexMcpCallAudit.created_at))
        .where(CodexMcpCallAudit.created_at >= cutoff)
        .group_by(CodexMcpCallAudit.user_id)
    )
    skill_stmt = (
        select(CodexMcpCallAudit.skill_id, func.count(CodexMcpCallAudit.id), func.max(CodexMcpCallAudit.created_at))
        .where(CodexMcpCallAudit.created_at >= cutoff, CodexMcpCallAudit.skill_id.is_not(None))
        .group_by(CodexMcpCallAudit.skill_id)
        .order_by(func.count(CodexMcpCallAudit.id).desc())
        .limit(20)
    )
    if filter_user:
        api_stmt = api_stmt.where(AuditLog.user_id == filter_user)
        mcp_stmt = mcp_stmt.where(CodexMcpCallAudit.user_id == filter_user)
        skill_stmt = skill_stmt.where(CodexMcpCallAudit.user_id == filter_user)
    by_user: dict[str, dict[str, Any]] = {}
    for uid, count, last_at in (await db.execute(api_stmt)).all():
        item = by_user.setdefault(str(uid or ""), {"user_id": str(uid or ""), "api_calls": 0, "mcp_calls": 0, "total_calls": 0, "last_called_at": None})
        item["api_calls"] += int(count or 0)
        item["total_calls"] += int(count or 0)
        item["last_called_at"] = max([value for value in (item["last_called_at"], last_at) if value], default=None)
    for uid, count, last_at in (await db.execute(mcp_stmt)).all():
        item = by_user.setdefault(str(uid or ""), {"user_id": str(uid or ""), "api_calls": 0, "mcp_calls": 0, "total_calls": 0, "last_called_at": None})
        item["mcp_calls"] += int(count or 0)
        item["total_calls"] += int(count or 0)
        item["last_called_at"] = max([value for value in (item["last_called_at"], last_at) if value], default=None)
    user_map = _user_display_map(list((await db.execute(select(User).where(User.id.in_([uid for uid in by_user if uid])))).scalars().all())) if by_user else {}
    users = []
    departments: dict[str, dict[str, Any]] = {}
    for raw in by_user.values():
        item = _attach_user_display({**raw, "last_called_at": _to_iso(raw.get("last_called_at"))}, user_map)
        users.append(item)
        dept = item.get("department") or "未分组"
        dep = departments.setdefault(dept, {"department": dept, "total_calls": 0, "user_count": 0, "mcp_calls": 0, "api_calls": 0})
        dep["total_calls"] += int(item.get("total_calls") or 0)
        dep["mcp_calls"] += int(item.get("mcp_calls") or 0)
        dep["api_calls"] += int(item.get("api_calls") or 0)
        dep["user_count"] += 1
    skill_rows = (await db.execute(skill_stmt)).all()
    skill_ids = [sid for sid, _count, _last in skill_rows if sid]
    skill_map = {}
    if skill_ids:
        skill_map = {
            row.id: row.display_name or row.name or row.id
            for row in (await db.execute(select(Skill).where(Skill.id.in_(skill_ids)))).scalars().all()
        }
    return {
        "users": sorted(users, key=lambda item: int(item.get("total_calls") or 0), reverse=True)[:20],
        "departments": sorted(departments.values(), key=lambda item: int(item.get("total_calls") or 0), reverse=True)[:20],
        "skills": [
            {"skill_id": sid, "skill_name": skill_map.get(sid, sid), "mcp_calls": int(count or 0), "last_called_at": _to_iso(last_at)}
            for sid, count, last_at in skill_rows
        ],
    }


async def _output_summary(
    db: AsyncSession,
    *,
    cutoff: datetime,
    scope_user_id: str | None,
    user_id: str | None,
    skill_id: str | None,
    report_count: int,
) -> dict[str, Any]:
    trigger_user = _effective_user_filter(scope_user_id, user_id)
    trigger_filter = ExecutionRun.trigger_type.like("codex:%")
    if trigger_user:
        trigger_filter = ExecutionRun.trigger_type == f"codex:{trigger_user}"
    todo_stmt = (
        select(func.count(DecisionRequest.id))
        .join(ExecutionRun, ExecutionRun.id == DecisionRequest.run_id)
        .where(DecisionRequest.created_at >= cutoff, trigger_filter)
    )
    related_stmt = (
        select(func.count(DecisionRequest.id))
        .join(ExecutionRun, ExecutionRun.id == DecisionRequest.run_id)
        .where(DecisionRequest.created_at >= cutoff, DecisionRequest.decision_log_id.is_not(None), trigger_filter)
    )
    if skill_id:
        todo_stmt = todo_stmt.where(DecisionRequest.skill_id == skill_id)
        related_stmt = related_stmt.where(DecisionRequest.skill_id == skill_id)
    read_stmt = select(func.count(UserInboxPreference.user_id)).where(UserInboxPreference.reports_last_viewed_at >= cutoff)
    if trigger_user:
        read_stmt = read_stmt.where(UserInboxPreference.user_id == trigger_user)
    return {
        "report_count": report_count,
        "todo_count": int((await db.execute(todo_stmt)).scalar() or 0),
        "related_todo_count": int((await db.execute(related_stmt)).scalar() or 0),
        "report_open_count": int((await db.execute(read_stmt)).scalar() or 0),
    }


async def build_sf_overview(
    db: AsyncSession,
    user: User,
    *,
    days: int = 30,
    page: int = 1,
    page_size: int = 50,
    kind: str | None = None,
    tool: str | None = None,
    user_id: str | None = None,
    skill_id: str | None = None,
    q: str | None = None,
    report_q: str | None = None,
) -> dict[str, Any]:
    cutoff = now_bjt() - timedelta(days=days)
    scope_user_id = _scope_user_id(user)
    clean_user_id = _clean_filter(user_id) if scope_user_id is None else None
    clean_skill_id = _clean_filter(skill_id)
    clean_report_q = _clean_filter(report_q)
    report_count, reports = await _reports(
        db,
        cutoff=cutoff,
        scope_user_id=scope_user_id,
        user_id=clean_user_id,
        skill_id=clean_skill_id,
        query=clean_report_q,
    )
    summary = await _summary(db, days=days, cutoff=cutoff, scope_user_id=scope_user_id, user_id=clean_user_id, skill_id=clean_skill_id, report_count=report_count)
    top_tools = await _top_tools(db, cutoff=cutoff, scope_user_id=scope_user_id, user_id=clean_user_id, skill_id=clean_skill_id)
    recent_events = await _recent_events(
        db,
        cutoff=cutoff,
        scope_user_id=scope_user_id,
        page=page,
        page_size=page_size,
        kind=kind,
        tool=tool,
        user_id=clean_user_id,
        skill_id=clean_skill_id,
        query=_clean_filter(q),
    )
    daily_usage = await _daily_usage(
        db,
        days=days,
        cutoff=cutoff,
        scope_user_id=scope_user_id,
        user_id=clean_user_id,
        skill_id=clean_skill_id,
        reports=reports,
    )
    health = await _health(db, cutoff=cutoff, scope_user_id=scope_user_id, user_id=clean_user_id, summary=summary)
    failure_summary = await _failure_summary(
        db,
        cutoff=cutoff,
        scope_user_id=scope_user_id,
        user_id=clean_user_id,
        skill_id=clean_skill_id,
    )
    rankings = await _rankings(db, cutoff=cutoff, scope_user_id=scope_user_id, user_id=clean_user_id)
    output_summary = await _output_summary(
        db,
        cutoff=cutoff,
        scope_user_id=scope_user_id,
        user_id=clean_user_id,
        skill_id=clean_skill_id,
        report_count=report_count,
    )
    data_records = await list_sf_data_records(
        db,
        user,
        page=1,
        page_size=min(page_size, 50),
        skill_id=clean_skill_id,
        q=_clean_filter(q),
    )
    return {
        "summary": summary,
        "top_tools": top_tools,
        "reports": reports[: min(page_size, 50)],
        "data_records": data_records,
        "daily_usage": daily_usage,
        "recent_events": recent_events,
        "health": health,
        "failure_summary": failure_summary,
        "rankings": rankings,
        "output_summary": output_summary,
        "filters": {
            "days": days,
            "kind": _clean_filter(kind),
            "tool": _clean_filter(tool),
            "user_id": clean_user_id or scope_user_id,
            "skill_id": clean_skill_id,
            "q": _clean_filter(q),
            "report_q": clean_report_q,
        },
    }


async def build_sf_catalog(
    db: AsyncSession,
    user: User,
    *,
    days: int = 30,
) -> dict[str, Any]:
    cutoff = now_bjt() - timedelta(days=days)
    scope_user_id = _scope_user_id(user)
    manifest = _manifest_payload()
    stats = await _tool_stats(db, cutoff=cutoff, scope_user_id=scope_user_id, user_id=None)
    mcp = manifest.get("mcp_capabilities") if isinstance(manifest.get("mcp_capabilities"), dict) else {}
    tools = []
    for raw in mcp.get("tools") or []:
        if not isinstance(raw, dict):
            continue
        item = {**raw}
        item.update(stats.get(str(item.get("name") or ""), {"count": 0, "failed_count": 0, "success_count": 0, "success_rate": None, "last_called_at": None}))
        item["command_template"] = _command_template(item)
        item["permission_hint"] = _tool_permission_hint(item)
        tools.append(item)
    tools.sort(key=lambda item: (-(int(item.get("count") or 0)), str(item.get("name") or "")))
    return {
        "version": manifest.get("version"),
        "plugin_update": manifest.get("plugin_update") or {},
        "command_groups": manifest.get("command_groups") or [],
        "commands": manifest.get("commands") or [],
        "mcp_capabilities": {**mcp, "tools": tools},
        "module_capabilities": manifest.get("module_capabilities") or [],
        "usage_tutorials": manifest.get("usage_tutorials") or [],
        "scope": "personal" if scope_user_id else "global",
        "days": days,
    }


def _tool_permission_hint(tool: dict[str, Any]) -> str:
    hints = []
    if tool.get("write"):
        hints.append("写工具：Web 侧只允许 dry-run，真实写入必须走 sf CLI + 幂等键。")
    else:
        hints.append("读工具：按当前账号和 Skill 权限读取。")
    if tool.get("requires_shop_id"):
        hints.append("需要 shop_id。")
    data_scope = tool.get("data_scope")
    if data_scope:
        hints.append(f"数据范围：{data_scope}。")
    return " ".join(hints)


async def build_sf_trace(
    db: AsyncSession,
    user: User,
    *,
    event_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    scope_user_id = _scope_user_id(user)
    event: dict[str, Any] | None = None
    confidence = "exact"
    reasons: list[str] = []
    resolved_run_id = _clean_filter(run_id)
    proof_id: str | None = None
    debug_run_id: str | None = None

    if event_id:
        prefix, _, raw_id = event_id.partition(":")
        if prefix == "mcp" and raw_id.isdigit():
            row = await db.get(CodexMcpCallAudit, int(raw_id))
            if row and (scope_user_id is None or row.user_id == scope_user_id):
                event = _event_from_mcp(row)
                proof_id = row.proof_id
                debug_run_id = row.debug_run_id
                if not resolved_run_id and row.debug_run_id:
                    resolved_run_id = row.debug_run_id
                    confidence = "best_effort"
                    reasons.append("MCP 记录关联的是 debug_run_id，可能不是正式 ExecutionRun。")
        elif prefix == "audit" and raw_id.isdigit():
            row = await db.get(AuditLog, int(raw_id))
            if row and (scope_user_id is None or row.user_id == scope_user_id):
                event = _event_from_audit(row)
                detail = row.detail if isinstance(row.detail, dict) else {}
                resolved_run_id = resolved_run_id or detail.get("run_id") or (row.target_id if row.target_type == "execution_run" else None)
        else:
            reasons.append("event_id 格式不支持。")

    execution_run = None
    debug_run = None
    if resolved_run_id:
        execution_run = await db.get(ExecutionRun, resolved_run_id)
        if execution_run and scope_user_id is not None and execution_run.trigger_type != f"codex:{scope_user_id}":
            execution_run = None
            reasons.append("ExecutionRun 不在当前账号范围内。")
        if not execution_run:
            debug_run = await db.get(CodexDebugRun, resolved_run_id)
            if debug_run and scope_user_id is not None and debug_run.user_id != scope_user_id:
                debug_run = None
            if debug_run:
                confidence = "best_effort"
    if debug_run_id and not debug_run:
        debug_run = await db.get(CodexDebugRun, debug_run_id)
        if debug_run and scope_user_id is not None and debug_run.user_id != scope_user_id:
            debug_run = None

    decision_logs = []
    reports: list[dict[str, Any]] = []
    todos = []
    if execution_run:
        log_rows = list((await db.execute(select(DecisionLog).where(DecisionLog.run_id == execution_run.id).order_by(DecisionLog.created_at.desc()))).scalars().all())
        for log in log_rows:
            decision_logs.append({"id": log.id, "run_id": log.run_id, "skill_id": log.skill_id, "created_at": _to_iso(log.created_at), "approval_level": log.approval_level})
            reports.extend(
                {
                    **card,
                    "run_id": log.run_id,
                    "skill_id": log.skill_id,
                    "created_at": _to_iso(log.created_at),
                }
                for card in extract_report_cards(log.id, log.output_result if isinstance(log.output_result, dict) else None)
            )
        todo_rows = list((await db.execute(select(DecisionRequest).where(DecisionRequest.run_id == execution_run.id).order_by(DecisionRequest.created_at.desc()))).scalars().all())
        todos = [
            {
                "id": row.id,
                "title": row.title,
                "status": row.aggregate_status,
                "skill_id": row.skill_id,
                "run_id": row.run_id,
                "decision_log_id": row.decision_log_id,
                "created_at": _to_iso(row.created_at),
            }
            for row in todo_rows
        ]

    skill_id = (
        getattr(execution_run, "skill_id", None)
        or getattr(debug_run, "skill_id", None)
        or (event or {}).get("skill_id")
    )
    commands = []
    if (event or {}).get("tool"):
        commands.append({"label": "复用 MCP 调用", "command": f"sf mcp call {(event or {}).get('tool')} --args '{{}}'"})
    if execution_run:
        commands.append({"label": "AI 复盘运行", "command": f"sf run analyze --run-id {execution_run.id} --include-raw"})
        commands.append({"label": "查询原始 DecisionLog", "command": f"sf raw query --source decision_logs --run-id {execution_run.id}"})
        commands.append({"label": "生成训练候选", "command": f"sf run candidate --run-id {execution_run.id}"})
    elif skill_id:
        commands.append({"label": "查询 Skill 原始数据", "command": f"sf raw query --source decision_logs --skill-id {skill_id} --limit 20"})

    return {
        "event": event,
        "proof_id": proof_id,
        "debug_run": _serialize_debug_run(debug_run) if debug_run else None,
        "execution_run": _serialize_execution_run(execution_run) if execution_run else None,
        "decision_logs": decision_logs,
        "reports": reports,
        "todos": todos,
        "commands": commands,
        "confidence": confidence,
        "reasons": reasons,
    }


def _serialize_execution_run(row: ExecutionRun | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row.id,
        "skill_id": row.skill_id,
        "status": row.status,
        "run_mode": row.run_mode,
        "trigger_type": row.trigger_type,
        "started_at": _to_iso(row.started_at),
        "completed_at": _to_iso(row.completed_at),
    }


def _serialize_debug_run(row: CodexDebugRun | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row.id,
        "skill_id": row.skill_id,
        "status": row.status,
        "run_mode": row.run_mode,
        "tool_call_count": row.tool_call_count,
        "created_at": _to_iso(row.created_at),
        "expires_at": _to_iso(row.expires_at),
        "completed_at": _to_iso(row.completed_at),
    }


async def sf_mcp_dry_run(
    db: AsyncSession,
    user: User,
    *,
    tool: str,
    arguments: dict[str, Any] | None = None,
    skill_id: str | None = None,
    shop_id: str | None = None,
) -> dict[str, Any]:
    args = dict(arguments or {})
    if shop_id and "shop_id" not in args and "shopId" not in args:
        args["shop_id"] = shop_id
    encoded_user = hashlib.sha256(str(user.id).encode("utf-8")).hexdigest()[:20]
    session_id = f"websf_{encoded_user}"
    session = await db.get(CodexCliSession, session_id)
    now = now_bjt()
    if not session:
        session = CodexCliSession(
            id=session_id,
            user_id=user.id,
            token_hash=f"websf:{encoded_user}",
            device_name="Web SF dry-run",
            scopes_json={"source": "web_sf_dry_run"},
            client_meta_json={"source": "web_sf_dry_run"},
            permissions_rev_snapshot=int(getattr(user, "permissions_rev", 0) or 0),
            created_at=now,
            expires_at=now + timedelta(days=30),
            last_seen_at=now,
        )
        db.add(session)
        await db.flush()
    else:
        session.last_seen_at = now
        session.expires_at = max(session.expires_at, now + timedelta(days=1))
        meta = session.client_meta_json if isinstance(session.client_meta_json, dict) else {}
        session.client_meta_json = {**meta, "source": "web_sf_dry_run", "last_seen_at": _to_iso(now)}
    principal = codex_service.CliPrincipal(user=user, session=session)
    result = await codex_service.call_mcp_tool(
        db,
        principal,
        server="skillforge",
        tool=tool,
        arguments=args,
        skill_id=skill_id,
        run_id=None,
        run_mode="web_dry_run",
        dry_run=True,
        idempotency_key=None,
    )
    if not result.get("proof", {}).get("dry_run", False):
        raise AppError("PARAM_INVALID", 400, {"detail": "Web SF 仅允许 dry-run"})
    return result
