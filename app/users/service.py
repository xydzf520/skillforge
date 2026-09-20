"""用户管理服务：CRUD + 钉钉同步 + 密码重置"""

import asyncio
import traceback
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import case, select, func, text as sql_text, update as sql_update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.codex.models import CodexCliSession, CodexMcpCallAudit
from app.auth.service import hash_password
from app.common.audit import AuditLog, audit
from app.common.exceptions import AppError
from app.common.models import UsageLog
from app.common.time_utils import BJT, isoformat_bjt, now_bjt
from app.hall.models import DirectCapabilityImageHistory
from app.dingtalk.recipients import _display_name_aliases, _looks_like_oauth_openid
from app.org.models import OrgUnit, UserOrgMembership
from app.todos.models import AITodo, DecisionRequest
from app.users.role_matrix import (
    VALID_ALL_ROLES,
    get_managed_departments,
    get_related_departments,
    is_dept_admin,
    is_operator_manageable_role,
    is_system_admin,
    normalize_role,
)

# H4：post-commit 回调签名（router 在 commit 成功后串行调用）。
PostCommitCallback = Callable[[], Awaitable[None]]
ActivityRow = tuple[datetime, datetime, str]
LoginRow = tuple[datetime, datetime]
_AUDIT_BJT_WRITE_CUTOFF = datetime(2026, 5, 6, 12, 0, 0)


def _bjt_iso(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is not None:
        return value.astimezone(BJT).isoformat()
    return value.replace(tzinfo=BJT).isoformat()


def _activity_candidate(
    value: datetime | None,
    source: str,
    *,
    stored_as: str,
) -> ActivityRow | None:
    if not value:
        return None
    if stored_as == "audit":
        # AuditLog.created_at switched from datetime.utcnow to now_bjt in the
        # Beijing-time migration. Older audit rows remain UTC-naive.
        stored_as = (
            "utc"
            if value.tzinfo is None and value < _AUDIT_BJT_WRITE_CUTOFF
            else "bjt"
        )
    if stored_as == "utc":
        compare_at = value.replace(tzinfo=timezone.utc)
        display_at = compare_at.astimezone(BJT).replace(tzinfo=None)
    else:
        display_at = value
        compare_at = value.replace(tzinfo=BJT).astimezone(timezone.utc)
    return compare_at.replace(tzinfo=None), display_at, source


def _newer_activity(current: ActivityRow | None, candidate: ActivityRow | None) -> ActivityRow | None:
    if candidate is None:
        return current
    if current is None or candidate[0] > current[0]:
        return candidate
    return current


async def _load_last_activity_by_user(
    db: AsyncSession,
    users: list[User],
    last_login_by_user: dict[str, LoginRow],
) -> dict[str, ActivityRow]:
    """用户管理页的最近活跃时间。

    last_login_at 只代表完成登录认证。session 未过期时继续使用 Skill 不会刷新它，
    所以这里额外聚合实际使用轨迹：直接能力图片生成、LLM 用量、审计操作。
    """
    user_ids = [u.id for u in users]
    if not user_ids:
        return {}

    activity: dict[str, ActivityRow] = {}
    for user_id, (compare_at, display_at) in last_login_by_user.items():
        activity[user_id] = (compare_at, display_at, "login")

    image_rows = (
        await db.execute(
            select(
                DirectCapabilityImageHistory.user_id,
                func.max(DirectCapabilityImageHistory.created_at),
            )
            .where(DirectCapabilityImageHistory.user_id.in_(user_ids))
            .group_by(DirectCapabilityImageHistory.user_id)
        )
    ).all()
    for user_id, latest_at in image_rows:
        activity[user_id] = _newer_activity(
            activity.get(user_id),
            _activity_candidate(latest_at, "image_generation", stored_as="bjt"),
        )

    usage_rows = (
        await db.execute(
            select(UsageLog.user_id, func.max(UsageLog.ts))
            .where(UsageLog.user_id.in_(user_ids))
            .group_by(UsageLog.user_id)
        )
    ).all()
    for user_id, latest_at in usage_rows:
        activity[user_id] = _newer_activity(
            activity.get(user_id),
            _activity_candidate(latest_at, "llm_usage", stored_as="bjt"),
        )

    codex_usage = await _load_codex_usage_by_user(db, users)
    for user_id, item in codex_usage.items():
        activity[user_id] = _newer_activity(
            activity.get(user_id),
            _activity_candidate(item.get("last_codex_activity_at"), "codex", stored_as="bjt"),
        )

    audit_rows = (
        await db.execute(
            select(AuditLog.user_id, func.max(AuditLog.created_at))
            .where(AuditLog.user_id.in_(user_ids))
            .where(AuditLog.action != "user.login")
            .where(~AuditLog.action.like("codex.%"))
            .group_by(AuditLog.user_id)
        )
    ).all()
    for user_id, latest_at in audit_rows:
        activity[user_id] = _newer_activity(
            activity.get(user_id),
            _activity_candidate(latest_at, "audit", stored_as="audit"),
        )

    return activity


async def _load_codex_usage_by_user(db: AsyncSession, users: list[User]) -> dict[str, dict]:
    user_ids = [u.id for u in users]
    if not user_ids:
        return {}
    now = now_bjt()
    session_rows = (
        await db.execute(
            select(
                CodexCliSession.user_id,
                func.count(CodexCliSession.id).label("session_count"),
                func.sum(
                    case((CodexCliSession.revoked_at.is_(None) & (CodexCliSession.expires_at > now), 1), else_=0)
                ).label("active_sessions"),
                func.max(func.coalesce(CodexCliSession.last_seen_at, CodexCliSession.created_at)).label("last_session_at"),
            )
            .where(CodexCliSession.user_id.in_(user_ids))
            .group_by(CodexCliSession.user_id)
        )
    ).all()
    mcp_rows = (
        await db.execute(
            select(
                CodexMcpCallAudit.user_id,
                func.count(CodexMcpCallAudit.id).label("mcp_call_count"),
                func.max(CodexMcpCallAudit.created_at).label("last_mcp_at"),
            )
            .where(CodexMcpCallAudit.user_id.in_(user_ids))
            .group_by(CodexMcpCallAudit.user_id)
        )
    ).all()
    audit_rows = (
        await db.execute(
            select(AuditLog.user_id, func.max(AuditLog.created_at))
            .where(AuditLog.user_id.in_(user_ids))
            .where(AuditLog.action.like("codex.%"))
            .group_by(AuditLog.user_id)
        )
    ).all()
    result: dict[str, dict] = {}
    for user_id, session_count, active_sessions, last_session_at in session_rows:
        result[user_id] = {
            "is_codex_user": True,
            "codex_session_count": int(session_count or 0),
            "codex_active_sessions": int(active_sessions or 0),
            "codex_mcp_call_count": 0,
            "last_codex_activity_at": last_session_at,
        }
    for user_id, mcp_call_count, last_mcp_at in mcp_rows:
        bucket = result.setdefault(
            user_id,
            {
                "is_codex_user": True,
                "codex_session_count": 0,
                "codex_active_sessions": 0,
                "codex_mcp_call_count": 0,
                "last_codex_activity_at": None,
            },
        )
        bucket["codex_mcp_call_count"] = int(mcp_call_count or 0)
        if last_mcp_at and (not bucket["last_codex_activity_at"] or last_mcp_at > bucket["last_codex_activity_at"]):
            bucket["last_codex_activity_at"] = last_mcp_at
    for user_id, last_audit_at in audit_rows:
        bucket = result.setdefault(
            user_id,
            {
                "is_codex_user": True,
                "codex_session_count": 0,
                "codex_active_sessions": 0,
                "codex_mcp_call_count": 0,
                "last_codex_activity_at": None,
            },
        )
        if last_audit_at and (not bucket["last_codex_activity_at"] or last_audit_at > bucket["last_codex_activity_at"]):
            bucket["last_codex_activity_at"] = last_audit_at
    return result


async def _load_last_login_by_user(db: AsyncSession, users: list[User]) -> dict[str, LoginRow]:
    user_ids = [u.id for u in users]
    if not user_ids:
        return {}

    latest: dict[str, LoginRow] = {}
    for user in users:
        candidate = _activity_candidate(user.last_login_at, "login", stored_as="bjt")
        if candidate:
            latest[user.id] = (candidate[0], candidate[1])

    rows = (
        await db.execute(
            select(AuditLog.user_id, func.max(AuditLog.created_at))
            .where(AuditLog.user_id.in_(user_ids))
            .where(AuditLog.action == "user.login")
            .group_by(AuditLog.user_id)
        )
    ).all()
    for user_id, latest_at in rows:
        candidate = _activity_candidate(latest_at, "login", stored_as="audit")
        if not candidate:
            continue
        current = latest.get(user_id)
        if current is None or candidate[0] > current[0]:
            latest[user_id] = (candidate[0], candidate[1])
    return latest


def _codex_usage_response(item: dict | None) -> dict:
    if not item:
        return {
            "is_codex_user": False,
            "codex_session_count": 0,
            "codex_active_sessions": 0,
            "codex_mcp_call_count": 0,
            "last_codex_activity_at": None,
        }
    return {
        **item,
        "last_codex_activity_at": _bjt_iso(item.get("last_codex_activity_at")),
    }


def _newer_login(current: LoginRow | None, candidate: LoginRow | None) -> LoginRow | None:
    if candidate is None:
        return current
    if current is None or candidate[0] > current[0]:
        return candidate
    return current


def _merge_codex_usage(current: dict | None, candidate: dict | None) -> dict | None:
    if not candidate:
        return current
    if current is None:
        return dict(candidate)
    current["is_codex_user"] = bool(current.get("is_codex_user") or candidate.get("is_codex_user"))
    current["codex_session_count"] = int(current.get("codex_session_count") or 0) + int(
        candidate.get("codex_session_count") or 0
    )
    current["codex_active_sessions"] = int(current.get("codex_active_sessions") or 0) + int(
        candidate.get("codex_active_sessions") or 0
    )
    current["codex_mcp_call_count"] = int(current.get("codex_mcp_call_count") or 0) + int(
        candidate.get("codex_mcp_call_count") or 0
    )
    candidate_last = candidate.get("last_codex_activity_at")
    current_last = current.get("last_codex_activity_at")
    if candidate_last and (not current_last or candidate_last > current_last):
        current["last_codex_activity_at"] = candidate_last
    return current


def _may_be_dingtalk_shadow_user(user: User) -> bool:
    dingtalk_user_id = str(user.dingtalk_user_id or "").strip()
    if not dingtalk_user_id:
        return False
    if _looks_like_oauth_openid(dingtalk_user_id):
        return True
    return any(ch.isalpha() for ch in dingtalk_user_id) and len(_display_name_aliases(user.name)) > 1


def _collapse_user_sources(
    users: list[User],
    canonical_by_shadow_id: dict[str, User],
) -> tuple[list[User], dict[str, list[str]]]:
    display_users_by_id: dict[str, User] = {}
    source_ids_by_display_id: dict[str, list[str]] = {}
    for user in users:
        display_user = canonical_by_shadow_id.get(user.id) or user
        display_users_by_id.setdefault(display_user.id, display_user)
        sources = source_ids_by_display_id.setdefault(display_user.id, [])
        for user_id in (user.id, display_user.id):
            if user_id and user_id not in sources:
                sources.append(user_id)
    return list(display_users_by_id.values()), source_ids_by_display_id


async def _resolve_shadow_canonical_users(db: AsyncSession, users: list[User]) -> dict[str, User]:
    """Map DingTalk OAuth shadow users to org-synced users for display.

    OAuth login can create an openId based account before org-sync knows its
    real enterprise userid. The admin user list should show one employee row,
    while preserving usage stats from the shadow account.
    """
    shadows = [
        user
        for user in users
        if _may_be_dingtalk_shadow_user(user)
    ]
    if not shadows:
        return {}

    dept_rank = case((User.department.is_(None), 1), else_=0)
    role_rank = case(
        (User.role == "admin", 0),
        (User.role == "system_admin", 1),
        (User.role == "operator", 2),
        (User.role == "ai_engineer", 3),
        else_=9,
    )
    canonical_by_shadow_id: dict[str, User] = {}
    for user in shadows:
        canonical: User | None = None
        aliases = _display_name_aliases(user.name)
        if aliases:
            candidates = list(
                (
                    await db.execute(
                        select(User)
                        .where(User.id != user.id)
                        .where(User.name.in_(aliases))
                        .where(User.dingtalk_user_id.is_not(None))
                        .where(User.dingtalk_user_id != "")
                        .where(User.is_active.is_(True))
                        .where(User.state == "active")
                        .order_by(dept_rank.asc(), role_rank.asc(), User.id.asc())
                        .limit(10)
                    )
                )
                .scalars()
                .all()
            )
            canonical = next(
                (
                    item
                    for item in candidates
                    if item.department and not _looks_like_oauth_openid(item.dingtalk_user_id)
                ),
                None,
            ) or next((item for item in candidates if item.department), None)

        avatar_url = str(user.avatar_url or "").strip()
        if canonical is None and avatar_url:
            candidates = list(
                (
                    await db.execute(
                        select(User)
                        .where(User.id != user.id)
                        .where(User.avatar_url == avatar_url)
                        .where(User.dingtalk_user_id.is_not(None))
                        .where(User.dingtalk_user_id != "")
                        .where(User.is_active.is_(True))
                        .where(User.state == "active")
                        .order_by(dept_rank.asc(), role_rank.asc(), User.id.asc())
                        .limit(10)
                    )
                )
                .scalars()
                .all()
            )
            canonical = next(
                (
                    item
                    for item in candidates
                    if item.department and not _looks_like_oauth_openid(item.dingtalk_user_id)
                ),
                None,
            ) or next((item for item in candidates if item.department), None)

        if canonical and canonical.department:
            canonical_by_shadow_id[user.id] = canonical
    return canonical_by_shadow_id


def _operator_id(operator: User | None, operator_id: str | None) -> str:
    """兼容两种调用风格：传 User 对象或 operator_id 字符串。"""
    if operator is not None:
        return getattr(operator, "id", None) or operator_id or "system"
    return operator_id or "system"


async def _managed_user_ids_stmt(db: AsyncSession, operator: User | None):
    """返回 dept_admin 可见成员子查询；None 表示 system_admin 全量可见。"""
    if operator is None or is_system_admin(operator):
        return None
    if not is_dept_admin(operator):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    managed = await get_managed_departments(db, operator)
    if not managed:
        return False
    return (
        select(UserOrgMembership.user_id)
        .where(UserOrgMembership.org_unit_id.in_(sorted(managed)))
    )


async def _ensure_user_visible_to_operator(
    db: AsyncSession,
    target: User,
    operator: User | None,
) -> None:
    if operator is None or is_system_admin(operator):
        return
    if not is_dept_admin(operator):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    managed = await get_managed_departments(db, operator)
    if not managed:
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    target_depts = await get_related_departments(db, target)
    if not target_depts.intersection(managed):
        raise AppError("AUTH_PERMISSION_DENIED", 403)


async def _ensure_user_manageable_by_operator(
    db: AsyncSession,
    target: User,
    operator: User | None,
    *,
    new_role: str | None = None,
    can_view_all: bool | None = None,
    department: str | None = None,
) -> None:
    """dept_admin 只能管理其直接管辖部门内的 aibp/observer 成员。"""
    await _ensure_user_visible_to_operator(db, target, operator)
    if operator is None or is_system_admin(operator):
        return

    if not is_operator_manageable_role(target.role):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    if new_role is not None and not is_operator_manageable_role(new_role):
        if normalize_role(new_role) == "dept_admin":
            raise AppError("DEPT_ADMIN_CANNOT_PROMOTE", 403)
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    if can_view_all is True:
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    if department is not None:
        primary_names = set((await _load_primary_departments_by_user(db, [target])).values())
        allowed_department_names = {target.department or "", *primary_names}
        if department not in allowed_department_names:
            raise AppError("AUTH_PERMISSION_DENIED", 403)


def _register_post_commit(db: AsyncSession, callback: PostCommitCallback) -> None:
    """H4：把回调登记到 session.info，由 router 在 db.commit() 成功后串行执行。

    这样 rollback 路径（commit 抛错）不会触发 cache invalidate，避免短窗口
    "DB 未生效但缓存已清" 的不一致。

    设计取舍：
    - 不用 SQLAlchemy `after_commit` event（async 路径下 listener 触发时机难控）
    - 在 service 里登记、在 router 里 await flush_post_commit_callbacks(db)
    - 同 session 多次注册按入队顺序执行
    """
    bucket = db.info.setdefault("_post_commit_callbacks", [])
    bucket.append(callback)


async def flush_post_commit_callbacks(db: AsyncSession) -> None:
    """H4：router 在 await db.commit() 成功后调用一次，串行执行登记的回调。

    任意单个回调失败仅 warning，不抛——cache invalidate 的失败不应影响响应返回。
    回调列表执行后清空，避免下一个请求复用同 session 时重复触发。
    """
    bucket = db.info.pop("_post_commit_callbacks", None)
    if not bucket:
        return
    for cb in bucket:
        try:
            await cb()
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("post_commit_callback failed: {}", exc)


def _drop_post_commit_callbacks(db: AsyncSession) -> None:
    """rollback / 异常路径用：丢弃尚未触发的回调。"""
    db.info.pop("_post_commit_callbacks", None)


async def _trigger_reresolve(
    db: AsyncSession,
    user_id: str,
    triggering_user_id: str = "system",
) -> None:
    """role-matrix-v2 §6.5：权限变更后重解析受影响的审批步骤。

    H2：失败不阻断主流程（避免 disable_user 被审批自愈拖垮），但**不**静默吞——
    必须 audit.log 高优告警 priority，并精确匹配预期可恢复异常类型；
    KeyboardInterrupt / asyncio.CancelledError 必须 re-raise 让协程正确取消。
    """
    try:
        from app.approval.reresolve import reresolve_affected_decision_requests

        await reresolve_affected_decision_requests(db, user_id)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except (SQLAlchemyError, AppError, ValueError) as exc:
        logger.warning(
            "users.reresolve_hook failed user_id={} error={}",
            user_id,
            exc,
        )
        # 写一条高优告警审计：approval 链可能卡住，运维需关注。priority 字段存 detail。
        try:
            await audit.log(
                triggering_user_id,
                "approval.reresolve_failed",
                "user",
                str(user_id),
                detail={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "trace": traceback.format_exc()[:2000],
                    "priority": "high",
                },
            )
        except Exception as audit_exc:  # noqa: BLE001
            # audit.log 自身失败仅 warning，避免连锁中断主流程
            logger.warning(
                "users.reresolve_hook audit.log failed user_id={} audit_error={}",
                user_id,
                audit_exc,
            )


async def list_users(
    db: AsyncSession,
    role: str | None = None,
    department: str | None = None,
    page: int = 1,
    page_size: int = 50,
    operator: User | None = None,
) -> dict:
    """用户列表（分页、支持按角色/部门筛选）"""
    stmt = select(User)
    count_stmt = select(func.count()).select_from(User)

    scoped_user_ids = await _managed_user_ids_stmt(db, operator)
    if scoped_user_ids is False:
        return {"total": 0, "page": page, "items": []}
    if scoped_user_ids is not None:
        stmt = stmt.where(User.id.in_(scoped_user_ids))
        count_stmt = count_stmt.where(User.id.in_(scoped_user_ids))

    if role:
        stmt = stmt.where(User.role == role)
        count_stmt = count_stmt.where(User.role == role)
    if department:
        stmt = stmt.where(User.department == department)
        count_stmt = count_stmt.where(User.department == department)

    # 总数
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页
    stmt = stmt.order_by(User.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    users = list(result.scalars().all())
    canonical_by_shadow_id = await _resolve_shadow_canonical_users(db, users)
    loaded_user_ids = {user.id for user in users}
    extra_canonical_users = [
        canonical
        for canonical in canonical_by_shadow_id.values()
        if canonical.id not in loaded_user_ids
    ]
    metric_users = users + extra_canonical_users
    display_users, source_ids_by_display_id = _collapse_user_sources(users, canonical_by_shadow_id)
    last_login_by_user = await _load_last_login_by_user(db, metric_users)
    codex_usage_by_user = await _load_codex_usage_by_user(db, metric_users)
    last_activity_by_user = await _load_last_activity_by_user(db, metric_users, last_login_by_user)
    primary_departments = await _load_primary_departments_by_user(db, metric_users)

    def _merged_login(display_user_id: str) -> LoginRow | None:
        merged: LoginRow | None = None
        for source_id in source_ids_by_display_id.get(display_user_id, [display_user_id]):
            merged = _newer_login(merged, last_login_by_user.get(source_id))
        return merged

    def _merged_activity(display_user_id: str) -> ActivityRow | None:
        merged: ActivityRow | None = None
        for source_id in source_ids_by_display_id.get(display_user_id, [display_user_id]):
            merged = _newer_activity(merged, last_activity_by_user.get(source_id))
        return merged

    def _merged_codex_usage(display_user_id: str) -> dict | None:
        merged: dict | None = None
        for source_id in source_ids_by_display_id.get(display_user_id, [display_user_id]):
            merged = _merge_codex_usage(merged, codex_usage_by_user.get(source_id))
        return merged
    display_total = len(display_users) if page == 1 and page_size >= total else total

    return {
        "total": display_total,
        "page": page,
        "items": [
            {
                "id": u.id,
                "username": u.username,
                "name": u.name,
                "role": u.role,
                "department": primary_departments.get(u.id) or u.department,
                "is_active": u.is_active,
                "can_view_all": u.can_view_all,
                "dingtalk_user_id": u.dingtalk_user_id,
                "avatar_url": u.avatar_url,
                "last_login_at": _bjt_iso(login[1]) if (login := _merged_login(u.id)) else None,
                "last_activity_at": _bjt_iso(activity[1]) if (activity := _merged_activity(u.id)) else None,
                "last_activity_source": activity[2] if activity else None,
                "codex": _codex_usage_response(_merged_codex_usage(u.id)),
                "created_at": isoformat_bjt(u.created_at),
            }
            for u in display_users
        ],
    }


async def _load_primary_departments_by_user(db: AsyncSession, users: list[User]) -> dict[str, str]:
    user_ids = [u.id for u in users]
    if not user_ids:
        return {}
    rows = (
        await db.execute(
            select(UserOrgMembership.user_id, OrgUnit.name)
            .join(OrgUnit, OrgUnit.id == UserOrgMembership.org_unit_id)
            .where(UserOrgMembership.user_id.in_(user_ids))
            .where(UserOrgMembership.membership_type == "primary")
            .order_by(OrgUnit.sort_order.asc(), OrgUnit.name.asc())
        )
    ).all()
    result: dict[str, str] = {}
    for user_id, org_name in rows:
        if user_id not in result and org_name:
            result[user_id] = org_name
    return result


# 角色 → 显式权限键列表（仅用于 L3-E 用户详情抽屉展示）。
# SkillForge 没有专门的「explicit permissions」表，权限实质来自
# (role + state + UserOrgMembership) 组合，由 app/auth/access.py 在请求路径上判定。
# 为了让用户管理员能直观看到「某角色能做什么」，这里维护一份按角色摘要的能力清单。
# 想精细到「按权限键勾选」需要新增 user_explicit_permissions 表 + RBAC engine，
# 不在本次范围内（详见 docs/spec/role-matrix-v2.md §10）。
_ROLE_PERMISSION_SUMMARY: dict[str, list[str]] = {
    "system_admin": [
        "user.manage_all",
        "org.manage_all",
        "skill.review_all",
        "audit.read_all",
        "system.configure",
    ],
    "dept_admin": [
        "user.activate_in_dept",
        "user.view_dept",
        "skill.review_in_dept",
        "approval.handle_in_dept",
    ],
    "aibp": [
        "skill.edit_authorized",
        "skill.submit_review",
        "skill.execute_authorized",
        "datasource.upload",
    ],
    "observer": [
        "skill.view_authorized",
        "execution.view_history",
        "report.view",
    ],
}


def _derive_permission_keys(user: User, can_view_all: bool) -> list[str]:
    """根据 role + can_view_all 推导可见的权限键列表。

    返回的是「按角色摘要」的能力清单，方便管理员快速判断该用户的能力范围。
    未来若引入 user_explicit_permissions 表，可在此扩展为合并显式权限。
    """
    from app.users.role_matrix import normalize_role

    normalized = normalize_role(user.role) or user.role or ""
    keys = list(_ROLE_PERMISSION_SUMMARY.get(normalized, []))
    if can_view_all and "org.view_all_readonly" not in keys:
        keys.append("org.view_all_readonly")
    return keys


async def get_user_detail(
    db: AsyncSession,
    user_id: str,
    operator: User | None = None,
) -> dict:
    """获取单个用户详情（L3-E 用户详情抽屉用）。

    返回字段在 list_users 基础上额外增加：
    - email / phone（User 模型字段，未来钉钉同步会回填）
    - permissions：按角色推导的能力键列表（_ROLE_PERMISSION_SUMMARY）
    - state：role-matrix-v2 状态字段（pending/active/disabled）

    与 list_users 一样会合并 DingTalk shadow 用户的 Codex/活跃统计，
    保证抽屉看到的活跃数据与列表行一致。
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise AppError("AUTH_USER_NOT_FOUND", 404)

    # 复用 list_users 的合并逻辑：把可能存在的 shadow 账号活跃数据并进展示账号
    # （否则抽屉里 Codex / last_activity_at 会比列表少）
    canonical_by_shadow_id = await _resolve_shadow_canonical_users(db, [user])
    metric_users = [user]
    # 如果当前 user 自己是 shadow，那它的 canonical 才是「真实展示账号」
    canonical_user = canonical_by_shadow_id.get(user.id)
    if canonical_user and canonical_user.id != user.id:
        # 入参 user_id 是 shadow 账号，但管理员通常通过列表点进来——列表展示的是
        # canonical_user，所以也按 canonical 来响应，保证抽屉与列表显示同一身份。
        user = canonical_user
        metric_users = [canonical_user]
        # 反过来再找一次 shadow 账号补齐 metric_users
        # （shadow 不会再有自己的 canonical，所以只展开一层即可）
        shadow_ids = [
            sid for sid, target in canonical_by_shadow_id.items()
            if target.id == canonical_user.id and sid != canonical_user.id
        ]
        if shadow_ids:
            shadow_rows = (
                await db.execute(select(User).where(User.id.in_(shadow_ids)))
            ).scalars().all()
            metric_users.extend(shadow_rows)
    else:
        # user 自己是 canonical：把指向它的 shadow 账号加进来一起聚合
        shadow_ids = [
            sid for sid, target in canonical_by_shadow_id.items()
            if target.id == user.id and sid != user.id
        ]
        if shadow_ids:
            shadow_rows = (
                await db.execute(select(User).where(User.id.in_(shadow_ids)))
            ).scalars().all()
            metric_users.extend(shadow_rows)

    await _ensure_user_visible_to_operator(db, user, operator)

    last_login_by_user = await _load_last_login_by_user(db, metric_users)
    codex_usage_by_user = await _load_codex_usage_by_user(db, metric_users)
    last_activity_by_user = await _load_last_activity_by_user(db, metric_users, last_login_by_user)
    primary_departments = await _load_primary_departments_by_user(db, metric_users)

    merged_login: LoginRow | None = None
    merged_activity: ActivityRow | None = None
    merged_codex: dict | None = None
    for mu in metric_users:
        merged_login = _newer_login(merged_login, last_login_by_user.get(mu.id))
        merged_activity = _newer_activity(merged_activity, last_activity_by_user.get(mu.id))
        merged_codex = _merge_codex_usage(merged_codex, codex_usage_by_user.get(mu.id))

    can_view_all = bool(getattr(user, "can_view_all", False))
    state = getattr(user, "state", None) or ("active" if user.is_active else "disabled")

    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "role": user.role,
        "department": primary_departments.get(user.id) or user.department,
        "state": state,
        "is_active": user.is_active,
        "can_view_all": can_view_all,
        "email": user.email,
        "phone": user.phone,
        "dingtalk_user_id": user.dingtalk_user_id,
        "avatar_url": user.avatar_url,
        "created_at": isoformat_bjt(user.created_at),
        "last_login_at": _bjt_iso(merged_login[1]) if merged_login else None,
        "last_activity_at": _bjt_iso(merged_activity[1]) if merged_activity else None,
        "last_activity_source": merged_activity[2] if merged_activity else None,
        "permissions": _derive_permission_keys(user, can_view_all),
        "codex": _codex_usage_response(merged_codex),
    }


async def create_user(
    db: AsyncSession,
    user_id: str,
    username: str,
    name: str,
    role: str,
    department: str | None = None,
    password: str | None = None,
    operator_id: str = "system",
) -> dict:
    """创建用户"""
    # 检查用户名是否已存在
    exists = await db.execute(select(User).where(User.username == username))
    if exists.scalar_one_or_none():
        raise AppError("AUTH_USER_EXISTS", 409)

    # 检查ID是否已存在
    exists = await db.execute(select(User).where(User.id == user_id))
    if exists.scalar_one_or_none():
        raise AppError("AUTH_USER_EXISTS", 409)

    if role not in VALID_ALL_ROLES:
        raise AppError("PARAM_INVALID", 400)

    user = User(
        id=user_id,
        username=username,
        name=name,
        role=role,
        department=department,
        password_hash=hash_password(password) if password else None,
        must_change_password=True,
    )
    db.add(user)
    await db.flush()

    await audit.log(operator_id, "user.create", "user", user_id,
                    detail={"username": username, "role": role})

    return {"id": user.id, "username": user.username, "name": user.name}


async def update_user(
    db: AsyncSession,
    user_id: str,
    name: str | None = None,
    role: str | None = None,
    department: str | None = None,
    is_active: bool | None = None,
    can_view_all: bool | None = None,
    operator_id: str | None = None,
    operator: User | None = None,
) -> dict:
    """更新用户信息。

    role-matrix-v2 §6.5：role 发生变化时（尤其是 dept_admin → aibp / observer 这类
    降权）触发审批链重解析，避免未决审批卡死在已失权用户身上。

    Args:
        operator_id: 旧风格调用（路由层传字符串）
        operator: 新风格（role-matrix-v2 测试 / 服务层传 User 对象）
    """
    actor_id = _operator_id(operator, operator_id)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise AppError("AUTH_USER_NOT_FOUND", 404)

    changes: dict = {}
    role_changed = False
    is_active_changed = False
    can_view_all_changed = False
    target_role = role if role is not None else user.role
    target_is_active = bool(is_active) if is_active is not None else bool(user.is_active)
    if role is not None and role not in VALID_ALL_ROLES:
        raise AppError("PARAM_INVALID", 400)

    await _ensure_user_manageable_by_operator(
        db,
        user,
        operator,
        new_role=role,
        can_view_all=can_view_all,
        department=department,
    )

    reduces_system_admin = (
        normalize_role(user.role) == "system_admin"
        and (normalize_role(target_role) != "system_admin" or not target_is_active)
    )
    if reduces_system_admin:
        from app.common.advisory_lock import acquire_user_lock, acquire_xact_lock
        from app.users.role_matrix import count_active_system_admins

        await acquire_xact_lock(db, "system_admin_disable")
        await acquire_user_lock(db, user.id)
        remaining = await count_active_system_admins(db, exclude_user_id=user.id)
        if remaining <= 0:
            raise AppError("LAST_SYSTEM_ADMIN", 400)

    if name is not None:
        user.name = name
        changes["name"] = name
    if role is not None:
        if user.role != role:
            role_changed = True
            changes["role_from"] = user.role
            user.role = role
            changes["role_to"] = role
    if department is not None:
        user.department = department
        changes["department"] = department
    if is_active is not None and user.is_active != is_active:
        from app.users.role_matrix import set_user_state

        await set_user_state(db, user, "active" if is_active else "disabled")
        changes["is_active"] = is_active
        is_active_changed = True
    if can_view_all is not None and bool(getattr(user, "can_view_all", False)) != bool(can_view_all):
        user.can_view_all = can_view_all
        changes["can_view_all"] = can_view_all
        can_view_all_changed = True

    user.updated_at = now_bjt()
    await db.flush()

    await audit.log(actor_id, "user.update", "user", user_id, detail=changes)

    # role / is_active 变更都需要触发审批链重解析（§6.5）：
    # role 降权会把人从 approver 候选里踢掉；is_active=False 在这条 update 入口
    # 也会使人失去审批资格（disable_user 是另一个入口）。
    if role_changed or is_active_changed:
        await _trigger_reresolve(db, user_id, triggering_user_id=actor_id)

    # 权限相关字段变更必须 bump permissions_rev 让旧 session 立即失效（§10.2），
    # 同时清掉该用户的收件中心可见 Skill 缓存（TTL 60s，否则新权限视图延迟可见）。
    # H4：bump 是 DB 写入（事务内），cache invalidate 是外部副作用 → 移到 commit 之后。
    if role_changed or is_active_changed or can_view_all_changed:
        try:
            from app.users.role_matrix import bump_permissions_rev

            await bump_permissions_rev(db, user_id)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except (SQLAlchemyError, ValueError) as exc:
            logger.warning(
                "users.update bump_permissions_rev failed user_id={} error={}",
                user_id,
                exc,
            )

        # H4：cache invalidate 必须 post-commit。rollback 后 DB 未生效，缓存清了反而
        # 让旧权限快照"消失"，引入读不到的窗口。
        async def _invalidate_cache():
            from app.inbox.service import invalidate_inbox_permissions_cache

            await invalidate_inbox_permissions_cache(user_id)

        _register_post_commit(db, _invalidate_cache)

    return {"id": user.id, "name": user.name, "role": user.role}


async def update_user_state(
    db: AsyncSession,
    *,
    user_id: str,
    state: str,
    operator_id: str = "system",
    operator: User | None = None,
) -> dict:
    """切换 active/disabled 状态，保证 state 与 is_active 同步。"""
    normalized_state = (state or "").strip().lower()
    if normalized_state not in {"active", "disabled"}:
        raise AppError("PARAM_INVALID", 400, {"detail": "state must be active or disabled"})

    actor_id = _operator_id(operator, operator_id)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise AppError("AUTH_USER_NOT_FOUND", 404)

    await _ensure_user_manageable_by_operator(db, user, operator)

    from app.common.advisory_lock import acquire_user_lock, acquire_xact_lock
    from app.users.role_matrix import (
        bump_permissions_rev,
        count_active_system_admins,
        get_user_state,
        set_user_state,
    )

    if user.role in ("system_admin", "admin"):
        await acquire_xact_lock(db, "system_admin_disable")
    await acquire_user_lock(db, user.id)

    current_state = await get_user_state(db, user)
    if normalized_state == "disabled" and user.role in ("system_admin", "admin"):
        remaining = await count_active_system_admins(db, exclude_user_id=user.id)
        if remaining <= 0:
            raise AppError("LAST_SYSTEM_ADMIN", 400)

    desired_is_active = normalized_state != "disabled"
    if current_state != normalized_state or bool(user.is_active) != desired_is_active:
        await set_user_state(db, user, normalized_state)
        user.updated_at = now_bjt()
        await bump_permissions_rev(db, user_id)
        await audit.log(
            actor_id,
            "user.state_update",
            "user",
            user_id,
            detail={"from": current_state, "to": normalized_state},
        )
        await _trigger_reresolve(db, user_id, triggering_user_id=actor_id)

        async def _invalidate_cache():
            from app.inbox.service import invalidate_inbox_permissions_cache

            await invalidate_inbox_permissions_cache(user_id)

        _register_post_commit(db, _invalidate_cache)

    await db.flush()
    return {"id": user.id, "state": normalized_state, "is_active": user.is_active}


async def unlink_dingtalk(
    db: AsyncSession,
    *,
    user_id: str,
    operator_id: str = "system",
    operator: User | None = None,
) -> dict:
    """解除用户钉钉身份绑定，不修改账号状态。"""
    actor_id = _operator_id(operator, operator_id)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise AppError("AUTH_USER_NOT_FOUND", 404)

    from app.common.advisory_lock import acquire_user_lock
    from app.users.role_matrix import bump_permissions_rev

    await acquire_user_lock(db, user.id)
    previous = {
        "dingtalk_user_id": user.dingtalk_user_id,
        "dingtalk_union_id": user.dingtalk_union_id,
    }
    if user.dingtalk_user_id or user.dingtalk_union_id:
        user.dingtalk_user_id = None
        user.dingtalk_union_id = None
        user.updated_at = now_bjt()
        await bump_permissions_rev(db, user_id)
        await audit.log(
            actor_id,
            "user.unlink_dingtalk",
            "user",
            user_id,
            detail=previous,
        )
    await db.flush()
    return {"id": user.id, "dingtalk_user_id": None}


async def activate_pending_user(
    db: AsyncSession,
    *,
    user_id: str,
    new_role: str,
    org_unit_id: str,
    is_manager: bool = False,
    can_view_all: bool = False,
    operator: User,
) -> dict:
    """激活 pending 用户（role-matrix-v2 §3.6 / §9.2）。

    规则：
    - 目标用户 state 必须为 pending
    - system_admin 可激活为 system_admin / dept_admin / aibp / observer
    - operator 必须是 system_admin 或 dept_admin
    - dept_admin 只能激活自己管辖部门内的 pending 用户（按 get_managed_departments 判定）
    - dept_admin 不能把 can_view_all 置 True（仅 system_admin）
    - dept_admin 不能把他人提拔为 dept_admin（抛 DEPT_ADMIN_CANNOT_PROMOTE，语义上映射到 AUTH_PERMISSION_DENIED 的细分）

    成功后：
    - set_user_state → active，bump_permissions_rev
    - user.role = new_role，department = org.name，can_view_all 按参数写回
    - 写入 UserOrgMembership(primary, is_manager)
    - audit.log("user.activate")
    """
    # 延迟 import 避免循环依赖
    from app.users.role_matrix import (
        bump_permissions_rev,
        get_managed_departments,
        is_dept_admin,
        is_system_admin,
        normalize_role,
        set_user_state,
    )

    if operator is None:
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    # 只允许 system_admin / dept_admin 执行激活
    if not (is_system_admin(operator) or is_dept_admin(operator)):
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    # role 值域校验：system_admin 可指定四类 v2 角色；dept_admin 后续再收窄。
    normalized_new_role = normalize_role(new_role)
    if normalized_new_role not in {"system_admin", "dept_admin", "aibp", "observer"}:
        raise AppError("PARAM_INVALID", 400)
    if is_dept_admin(operator) and not is_system_admin(operator) and normalized_new_role not in {"aibp", "observer"}:
        if normalized_new_role == "dept_admin":
            raise AppError("DEPT_ADMIN_CANNOT_PROMOTE", 403)
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    # 加载目标用户
    target = await db.get(User, user_id)
    if not target:
        raise AppError("AUTH_USER_NOT_FOUND", 404)

    # 读取 state（兼容字段可能没落库）
    try:
        from app.users.role_matrix import get_user_state

        current_state = await get_user_state(db, target)
    except Exception:  # noqa: BLE001
        current_state = getattr(target, "state", None) or (
            "active" if target.is_active else "disabled"
        )
    if current_state != "pending":
        raise AppError("USER_NOT_PENDING", 400)

    # 目标部门必须存在
    org = await db.get(OrgUnit, org_unit_id)
    if not org:
        raise AppError("ORG_UNIT_NOT_FOUND", 404)

    # dept_admin：只能在自己管辖的部门内激活
    if is_dept_admin(operator) and not is_system_admin(operator):
        managed = await get_managed_departments(db, operator)
        if org_unit_id not in managed:
            raise AppError("AUTH_PERMISSION_DENIED", 403)
        if can_view_all:
            raise AppError("AUTH_PERMISSION_DENIED", 403)
        # spec §9.2：若 pending 用户已有钉钉同步的 membership，则目标部门必须在其中
        #   防止 dept_admin 把非本部门候选人"拽"进管辖部门
        pending_orgs = {
            row
            for row in (
                await db.execute(
                    select(UserOrgMembership.org_unit_id).where(
                        UserOrgMembership.user_id == user_id
                    )
                )
            ).scalars().all()
        }
        if pending_orgs and org_unit_id not in pending_orgs:
            raise AppError("AUTH_PERMISSION_DENIED", 403)

    # 1) 写 role / 展示字段
    target.role = normalized_new_role
    target.department = org.name
    if normalized_new_role == "system_admin":
        target.can_view_all = False
    elif can_view_all is True:
        target.can_view_all = True
    # 不主动改 can_view_all=False，避免踩到 system_admin 预设
    target.updated_at = now_bjt()

    # 2) 写 state → active（内部兼容 is_active）
    try:
        await set_user_state(db, target, "active")
    except Exception as exc:  # noqa: BLE001
        # 没有 state 列时（极端环境）让 is_active 兜底。schema 正常情况下这条分支
        # 不应该进来，落入此处说明 role-matrix-v2 迁移未跑或 users 表被改过。
        logger.warning(
            "activate_pending_user state write failed user_id={} error={}",
            user_id,
            exc,
        )
        target.is_active = True

    await db.flush()

    # 3) 维护主部门 membership（幂等：已存在则更新 is_manager，不重复 insert）
    existing = (
        await db.execute(
            select(UserOrgMembership).where(
                UserOrgMembership.user_id == user_id,
                UserOrgMembership.org_unit_id == org_unit_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            UserOrgMembership(
                user_id=user_id,
                org_unit_id=org_unit_id,
                membership_type="primary",
                is_manager=True if normalized_new_role == "dept_admin" else is_manager,
            )
        )
    else:
        existing.is_manager = True if normalized_new_role == "dept_admin" else is_manager
        existing.membership_type = "primary"
    await db.flush()

    # 4) bump permissions_rev（session 立即失效）。失败仅告警，不阻断激活：
    # 用户已经写入了 role/state/membership，可以登录使用；rev 漂移风险由下一次
    # 写操作纠正（或运维看到 warning 手动 bump）。
    try:
        await bump_permissions_rev(db, user_id)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except (SQLAlchemyError, ValueError) as exc:
        logger.warning(
            "activate_pending_user bump_permissions_rev failed user_id={} error={}",
            user_id,
            exc,
        )

    # H4：清缓存延后到 router commit 成功后，避免 commit 失败时缓存已清的窗口。
    async def _invalidate_cache():
        from app.inbox.service import invalidate_inbox_permissions_cache

        await invalidate_inbox_permissions_cache(user_id)

    _register_post_commit(db, _invalidate_cache)

    await audit.log(
        getattr(operator, "id", "system"),
        "user.activate",
        "user",
        user_id,
        detail={
            "role": normalized_new_role,
            "department_id": org_unit_id,
            "department": org.name,
            "is_manager": True if normalized_new_role == "dept_admin" else is_manager,
            "can_view_all": bool(target.can_view_all),
        },
    )

    return {"id": user_id, "state": "active", "role": normalized_new_role}


async def list_pending_users(
    db: AsyncSession,
    *,
    operator: User,
) -> dict:
    """列出 pending 用户（role-matrix-v2 §9 / §17 pending-users 接口）。

    - system_admin：返回全部 state='pending' 的用户
    - dept_admin：仅返回 primary membership 在自己管辖部门（is_manager=True）内的 pending
    - 其他角色：抛 AUTH_PERMISSION_DENIED

    返回每条包含：id, name, role, dingtalk_user_id, created_at,
    dingtalk_department (部门展示名), dingtalk_department_id (canonical org_unit_id)。
    """
    from app.users.role_matrix import (
        ensure_role_matrix_columns,
        get_managed_departments,
        is_dept_admin,
        is_system_admin,
    )

    if operator is None:
        raise AppError("AUTH_REQUIRED", 401)

    await ensure_role_matrix_columns(db)

    if not (is_system_admin(operator) or is_dept_admin(operator)):
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    # 受限部门集合：None 表示不做过滤（system_admin）
    managed_depts: set[str] | None
    if is_system_admin(operator):
        managed_depts = None
    else:
        managed_depts = await get_managed_departments(db, operator)
        # dept_admin 没有任何管辖部门 → 直接返回空
        if not managed_depts:
            return {"total": 0, "items": []}

    # 用原生 SQL 读 users.state='pending' + primary membership join
    # 这样即使 ORM 没有 state 字段也能运行（ensure_role_matrix_columns 已兜底建列）
    where_clauses = ["u.state = 'pending'"]
    params: dict[str, object] = {}
    if managed_depts is not None:
        where_clauses.append("m.org_unit_id = ANY(:dept_ids)")
        params["dept_ids"] = list(managed_depts)

    where_sql = " AND ".join(where_clauses)

    rows = (
        await db.execute(
            sql_text(
                "SELECT u.id AS id, u.name AS name, u.role AS role, "
                "       u.dingtalk_user_id AS dingtalk_user_id, "
                "       u.department AS department, u.created_at AS created_at, "
                "       m.org_unit_id AS org_unit_id, ou.name AS org_name "
                "FROM users u "
                "LEFT JOIN user_org_memberships m "
                "       ON m.user_id = u.id AND m.membership_type = 'primary' "
                "LEFT JOIN org_units ou ON ou.id = m.org_unit_id "
                f"WHERE {where_sql} "
                "ORDER BY u.created_at DESC NULLS LAST"
            ),
            params,
        )
    ).mappings().all()

    items = [
        {
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "dingtalk_user_id": row["dingtalk_user_id"],
            "dingtalk_department": row["org_name"] or row["department"],
            "dingtalk_department_id": row["org_unit_id"],
            "department_id": row["org_unit_id"],
            "created_at": isoformat_bjt(row["created_at"]),
        }
        for row in rows
    ]

    return {"total": len(items), "items": items}


async def disable_user(
    db: AsyncSession,
    user_id: str,
    operator_id: str | None = None,
    operator: User | None = None,
    successor_user_id: str | None = None,
) -> dict:
    """禁用用户（软删除）。

    role-matrix-v2 §9 / §6.5：
    - 如果指定 successor_user_id：把该用户名下 pending 状态的 AITodo 转交接手人；
    - 无论是否有接手人：对审批类 DecisionRequest 触发重解析，避免审批卡死；
    - 最后一位 system_admin 禁止禁用（LAST_SYSTEM_ADMIN）。
    """
    actor_id = _operator_id(operator, operator_id)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise AppError("AUTH_USER_NOT_FOUND", 404)

    await _ensure_user_manageable_by_operator(db, user, operator)

    successor: User | None = None
    if successor_user_id:
        if successor_user_id == user_id:
            raise AppError("SUCCESSOR_INVALID", 400)
        successor = await db.get(User, successor_user_id)
        if not successor:
            raise AppError("SUCCESSOR_INVALID", 400)
        successor_state = getattr(successor, "state", None) or (
            "active" if successor.is_active else "disabled"
        )
        if successor_state != "active" or not successor.is_active:
            raise AppError("SUCCESSOR_INVALID", 400)
        await _ensure_user_manageable_by_operator(db, successor, operator)
        if operator is not None and is_dept_admin(operator) and not is_system_admin(operator):
            managed = await get_managed_departments(db, operator)
            target_depts = await get_related_departments(db, user)
            successor_depts = await get_related_departments(db, successor)
            if not target_depts.intersection(successor_depts).intersection(managed):
                raise AppError("AUTH_PERMISSION_DENIED", 403)

    # v2.0.16 H5：两层 advisory lock，固定获取顺序避免死锁：
    # 1) 全局锁 `system_admin_disable`（仅当 user 是 admin 时拿）：防两个不同 admin
    #    并发 disable 都通过 "remaining > 0" 校验，导致最后一位 admin 被禁用。
    # 2) 用户锁 `user:{user_id}`：与 update_user / reresolve 共享 namespace，同一
    #    user 的写操作串行化，消除 TOCTOU。
    # 顺序始终是 1) → 2)，reresolve 内部只拿 2)，所以不会反向获取 → 无死锁风险。
    from app.common.advisory_lock import acquire_user_lock, acquire_xact_lock

    if user.role in ("system_admin", "admin"):
        await acquire_xact_lock(db, "system_admin_disable")
    await acquire_user_lock(db, user.id)

    if user.role in ("system_admin", "admin"):
        try:
            from app.users.role_matrix import count_active_system_admins

            remaining = await count_active_system_admins(db, exclude_user_id=user.id)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except (SQLAlchemyError, ValueError) as exc:
            logger.warning(
                "users.disable count_active_system_admins failed user_id={} error={}",
                user_id,
                exc,
            )
            remaining = None
        if remaining is not None and remaining <= 0:
            raise AppError("LAST_SYSTEM_ADMIN", 400)

    user.is_active = False
    user.updated_at = now_bjt()

    # 同步 role-matrix-v2 的 state 列（schema 已由 migration 056 建立，
    # 启动时由 verify_role_matrix_schema 兜底；此处失败视为异常路径，必须知会）。
    try:
        from app.users.role_matrix import bump_permissions_rev, set_user_state

        await set_user_state(db, user, "disabled")
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except (SQLAlchemyError, ValueError) as exc:
        logger.warning(
            "users.disable state update failed user_id={} error={}",
            user_id,
            exc,
        )

    # 立刻把该用户的 session 顶掉（§10.2）：token 携带的 permissions_rev 快照
    # 与新值不匹配即视为过期。bump 失败不阻断禁用主流程但必须告警。
    try:
        from app.users.role_matrix import bump_permissions_rev

        await bump_permissions_rev(db, user_id)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except (SQLAlchemyError, ValueError) as exc:
        logger.warning(
            "users.disable bump_permissions_rev failed user_id={} error={}",
            user_id,
            exc,
        )

    # 先对 approval_step 类 DecisionRequest 做重解析
    #   —— 该类型要走 resolved_by_peer + 候选 fan-out，不是简单转交。
    # 注意必须在下面的"其余 todo 转交接手人"之前运行，避免 target 的 todo
    # 被提前改成 successor 后 reresolve 找不到它。
    await _trigger_reresolve(db, user_id, triggering_user_id=actor_id)

    # 转交剩余 pending AITodo（非 approval_step 类）给接手人
    reassigned = 0
    if successor_user_id:
        # 子查询：approval_step 类 DecisionRequest 的 request_id 集合，排除
        approval_req_ids_stmt = select(DecisionRequest.id).where(
            DecisionRequest.source_type == "approval_step"
        )
        reassigned_result = await db.execute(
            sql_update(AITodo)
            .where(
                AITodo.assignee == user_id,
                AITodo.status == "pending",
                ~AITodo.request_id.in_(approval_req_ids_stmt),
            )
            .values(assignee=successor_user_id, updated_at=now_bjt())
        )
        reassigned = reassigned_result.rowcount or 0

    # 转交 SkillMember owner 角色给接手人（§9 资产转交）
    transferred_owners = 0
    if successor_user_id:
        try:
            from app.skills.members import SkillMember

            transfer_result = await db.execute(
                sql_update(SkillMember)
                .where(
                    SkillMember.user_id == user_id,
                    SkillMember.role == "owner",
                )
                .values(user_id=successor_user_id)
            )
            transferred_owners = transfer_result.rowcount or 0
        except Exception as exc:  # noqa: BLE001
            logger.debug("users.disable skill-owner transfer skipped: {}", exc)

    cascade_result: dict[str, int] = {"revoked_connector_keys": 0, "disabled_cookies": 0}
    try:
        from app.datasources.service import cascade_disable_by_user_disabled

        async with db.begin_nested():
            cascade_result = await cascade_disable_by_user_disabled(
                db,
                user_id=user_id,
                actor_user_id=actor_id,
            )
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("users.disable datasource cascade failed user_id={} error={}", user_id, exc)

    await db.flush()

    await audit.log(
        actor_id,
        "user.disable",
        "user",
        user_id,
        detail={
            "successor_user_id": successor_user_id,
            "reassigned_todos": reassigned,
            "transferred_owners": transferred_owners,
            **cascade_result,
        },
    )

    # H4：清掉被禁用者的收件中心可见 Skill 缓存延后到 router commit 成功后，
    # 防止 commit rollback 后缓存已清造成的"读到旧权限但 DB 未变更"窗口。
    async def _invalidate_cache():
        from app.inbox.service import invalidate_inbox_permissions_cache

        await invalidate_inbox_permissions_cache(user_id)

    _register_post_commit(db, _invalidate_cache)

    return {"id": user.id, "state": "disabled"}


async def reset_password(
    db: AsyncSession,
    user_id: str,
    new_password: str,
    operator_id: str | None = "system",
    operator: User | None = None,
) -> dict:
    """重置密码"""
    actor_id = _operator_id(operator, operator_id)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise AppError("AUTH_USER_NOT_FOUND", 404)

    await _ensure_user_manageable_by_operator(db, user, operator)

    user.password_hash = hash_password(new_password)
    user.must_change_password = True
    user.updated_at = now_bjt()
    await db.flush()

    await audit.log(actor_id, "user.reset_password", "user", user_id)
    return {"id": user.id, "must_change_password": True}


async def sync_dingtalk(
    db: AsyncSession,
    operator_id: str = "system",
) -> dict:
    """从钉钉同步用户。

    用户管理页的同步必须走组织同步主链路，否则只会更新 users 表，
    不会写 UserOrgMembership，后台和权限系统就拿不到部门。
    """
    from app.org.service import sync_dingtalk_org

    result = await sync_dingtalk_org(db, actor_id=operator_id)
    await audit.log(operator_id, "user.sync_dingtalk", detail=result)
    return result
