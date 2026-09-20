"""门户服务层。"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import role_matches_any
from app.auth.models import User
from app.common.cache import cache_delete_pattern, cache_get, cache_set
from app.common.exceptions import AppError
from app.execution.models import DecisionLog, ExecutionRun
from app.execution.execution_service import execution_service
from app.org.models import OrgUnit, UserOrgMembership
from app.portal import ui_service as portal_ui_service
from app.portal.models import SkillSubmission
from app.skills.core.access import (
    build_skill_access_filter,
    get_skill_permissions,
    require_skill_access,
)
from app.skills.members import SkillMember, SkillTag
from app.skills.core.models import Skill
from app.skills.core.parser import skill_parser
from app.skills.core.git_service import git_service
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime

PORTAL_RESERVED_PARAM_KEYS = {
    "_execution",
    "_execution_backend",
    "_aiclaw_instance_id",
    "_script_path",
    "_script_timeout",
    "_target_dir",
    "instance_id",
}
PUBLIC_PARAM_SCHEMA_KEYS = {"type", "required", "properties"}
PUBLIC_PARAM_FIELD_KEYS = {
    "type",
    "format",
    "title",
    "label",
    "description",
    "default",
    "enum",
    "items",
    "minimum",
    "maximum",
    "properties",
    "required",
    "dependsOn",
    "placeholder",
    "help",
}
PUBLIC_DEPENDS_ON_KEYS = {"field", "value", "values", "operator"}


async def _cache_get_safe(key: str):
    try:
        return await cache_get(key)
    except RuntimeError:
        return None


async def _cache_set_safe(key: str, value, ttl: int) -> None:
    try:
        await cache_set(key, value, ttl=ttl)
    except RuntimeError:
        return


async def _cache_delete_pattern_safe(pattern: str) -> None:
    try:
        await cache_delete_pattern(pattern)
    except RuntimeError:
        return


def _iso(dt: datetime | None) -> str | None:
    return isoformat_bjt(dt)


def _user_permissions_rev(user: User) -> int:
    value = getattr(user, "permissions_rev", 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_schema_defaults(schema: dict | None) -> dict | None:
    if not isinstance(schema, dict):
        return schema
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    today = now_bjt().date()
    for field in properties.values():
        if not isinstance(field, dict):
            continue
        default = field.get("default")
        if default == "yesterday":
            field["default"] = (today - timedelta(days=1)).isoformat()
    return schema


def _portal_safe_param_ui_schema(schema: dict | None) -> dict | None:
    if not isinstance(schema, dict):
        return schema
    sanitized = copy.deepcopy(schema)

    def has_secret_value(value) -> bool:
        redacted = portal_ui_service.redact_sensitive_ui_response_value(value)
        return redacted != value

    def normalize_schema_container(spec: dict) -> None:
        if "properties" in spec and not isinstance(spec.get("properties"), dict):
            spec["properties"] = {}
            spec["required"] = []
        elif "required" in spec and not isinstance(spec.get("required"), list):
            spec.pop("required", None)
        if "items" in spec and not isinstance(spec.get("items"), dict):
            spec.pop("items", None)

    def scrub_spec(spec: dict, path: str = "") -> None:
        for key in list(spec.keys()):
            if key not in PUBLIC_PARAM_FIELD_KEYS:
                spec.pop(key, None)
        normalize_schema_container(spec)
        for text_key in ("description", "placeholder", "help"):
            value = spec.get(text_key)
            if isinstance(value, str):
                spec[text_key] = portal_ui_service.redact_sensitive_ui_response_value(value)
        if isinstance(spec.get("title"), str):
            spec["title"] = str(portal_ui_service.redact_sensitive_ui_response_value(spec["title"]))
        if isinstance(spec.get("label"), str):
            spec["label"] = str(portal_ui_service.redact_sensitive_ui_response_value(spec["label"]))
        if "default" in spec and has_secret_value(spec.get("default")):
            spec.pop("default", None)
        if isinstance(spec.get("format"), str) and has_secret_value(spec.get("format")):
            spec.pop("format", None)
        enum = spec.get("enum")
        if isinstance(enum, list):
            spec["enum"] = [item for item in enum if not has_secret_value(item)]
        depends_on = spec.get("dependsOn")
        if isinstance(depends_on, dict):
            contains_secret_condition = any(
                key in PUBLIC_DEPENDS_ON_KEYS and has_secret_value(value)
                for key, value in depends_on.items()
            )
            safe_depends_on = {
                key: copy.deepcopy(value)
                for key, value in depends_on.items()
                if key in PUBLIC_DEPENDS_ON_KEYS and not has_secret_value(value)
            }
            field = safe_depends_on.get("field")
            if contains_secret_condition or (isinstance(field, str) and portal_ui_service.is_secret_like_param_path(field)):
                safe_depends_on = {}
            if safe_depends_on:
                spec["dependsOn"] = safe_depends_on
            else:
                spec.pop("dependsOn", None)
        elif "dependsOn" in spec:
            spec.pop("dependsOn", None)
        items = spec.get("items")
        if isinstance(items, dict):
            item_path = f"{path}[]" if path else "[]"
            scrub_spec(items, item_path)
            if isinstance(items.get("properties"), dict):
                prune_properties(items.get("properties"), items.get("required"), item_path)

    def prune_properties(properties: dict | None, required: list | None, prefix: str = "") -> set[str]:
        if not isinstance(properties, dict):
            return set()
        removed: set[str] = set()
        for key in list(properties.keys()):
            if not isinstance(key, str):
                removed.add(str(key))
                properties.pop(key, None)
                continue
            path = f"{prefix}.{key}" if prefix else key
            spec = properties.get(key)
            if portal_ui_service.is_secret_like_param_spec(path, spec if isinstance(spec, dict) else None):
                removed.add(key)
                properties.pop(key, None)
                continue
            if isinstance(spec, dict) and isinstance(spec.get("properties"), dict):
                prune_properties(spec.get("properties"), spec.get("required"), path)
            if isinstance(spec, dict):
                scrub_spec(spec, path)
        if isinstance(required, list):
            required[:] = [
                item
                for item in required
                if isinstance(item, str)
                and item in properties
                and item not in removed
                and not portal_ui_service.is_secret_like_param_path(f"{prefix}.{item}" if prefix else item)
            ]
        return removed

    normalize_schema_container(sanitized)
    prune_properties(sanitized.get("properties"), sanitized.get("required"))
    for key in list(sanitized.keys()):
        if key not in PUBLIC_PARAM_SCHEMA_KEYS:
            sanitized.pop(key, None)
    return _normalize_schema_defaults(sanitized)


def _extract_description(skill_id: str) -> str:
    skill_md = git_service.read_file(skill_id, "SKILL.md") or ""
    if not skill_md:
        return ""
    try:
        parsed = skill_parser.parse(skill_md)
        return parsed.purpose or ""
    except Exception:
        return ""


async def _user_org_ids(db: AsyncSession, user_id: str) -> set[str]:
    cached = await _cache_get_safe(f"org:user:{user_id}")
    if isinstance(cached, list):
        return {str(item) for item in cached}
    rows = await db.execute(select(UserOrgMembership.org_unit_id).where(UserOrgMembership.user_id == user_id))
    org_ids = {row[0] for row in rows.all()}
    await _cache_set_safe(f"org:user:{user_id}", sorted(org_ids), ttl=300)
    return org_ids


async def _skill_tags_map(db: AsyncSession, skill_ids: list[str]) -> dict[str, list[str]]:
    if not skill_ids:
        return {}
    rows = await db.execute(select(SkillTag.skill_id, SkillTag.tag).where(SkillTag.skill_id.in_(skill_ids)))
    tags: dict[str, list[str]] = {}
    for skill_id, tag in rows.all():
        tags.setdefault(skill_id, []).append(tag)
    return tags


async def _owner_name_map(db: AsyncSession, user_ids: list[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    rows = await db.execute(select(User.id, User.name).where(User.id.in_(user_ids)))
    return {user_id: name for user_id, name in rows.all()}


async def list_portal_skills(
    db: AsyncSession,
    user: User,
    *,
    search: str | None = None,
    category: str | None = None,
    department: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    user_rev = _user_permissions_rev(user)
    cache_key = f"portal:skills:{user.id}:{user_rev}:{search or ''}:{category or ''}:{department or ''}:{page}:{page_size}"
    cached = await _cache_get_safe(cache_key)
    if cached is not None:
        return cached

    stmt = select(Skill).where(Skill.status == "active")
    stmt = stmt.where(await build_skill_access_filter(db, user, "read"))
    if category:
        stmt = stmt.where(Skill.category == category)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Skill.id.ilike(like), Skill.name.ilike(like), Skill.display_name.ilike(like), Skill.summary.ilike(like)))
    stmt = stmt.order_by(Skill.usage_count.desc(), Skill.updated_at.desc())

    scope_rows = (await db.execute(stmt)).scalars().all()
    category_counts: dict[str, int] = {}
    department_counts: dict[str, int] = {}
    for skill in scope_rows:
        category_name = _portal_group_name(getattr(skill, "category", None), fallback="通用")
        department_name = _portal_group_name(getattr(skill, "department", None), fallback="")
        category_counts[category_name] = category_counts.get(category_name, 0) + 1
        if department_name:
            department_counts[department_name] = department_counts.get(department_name, 0) + 1

    rows = scope_rows
    if department:
        department_key = department.strip()
        rows = [
            skill for skill in rows
            if _portal_group_name(getattr(skill, "department", None), fallback="") == department_key
        ]
    total = len(rows)
    page_items = rows[(page - 1) * page_size: page * page_size]
    skill_ids = [item.id for item in page_items]
    owner_names = await _owner_name_map(db, [item.owner for item in page_items if item.owner])
    tag_map = await _skill_tags_map(db, skill_ids)
    org_rows = await db.execute(select(OrgUnit.id, OrgUnit.name).where(OrgUnit.id.in_([item.org_unit_id for item in page_items if item.org_unit_id])))
    org_names = {org_id: name for org_id, name in org_rows.all()}

    data = {
        "total": total,
        "categories": [
            {"name": name, "count": count}
            for name, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "departments": [
            {"name": name, "count": count}
            for name, count in sorted(department_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "items": [
            {
                "id": skill.id,
                "display_name": skill.display_name or skill.name,
                "summary": skill.summary or skill.description or "",
                "category": skill.category or "通用",
                "icon": skill.icon or "apps",
                "owner_name": owner_names.get(skill.owner or "", skill.owner or ""),
                "org_unit_name": org_names.get(skill.org_unit_id or "", skill.department or ""),
                "usage_count": skill.usage_count or 0,
                "success_rate": skill.success_rate,
                "last_run_at": _iso(skill.last_run_at),
                "tags": tag_map.get(skill.id, []),
                "has_params": bool((_portal_safe_param_ui_schema(skill.param_ui_schema) or {}).get("properties")),
                "visibility": skill.visibility,
                "status": skill.status,
                "permissions": await get_skill_permissions(db, skill, user),
            }
            for skill in page_items
        ],
    }
    await _cache_set_safe(cache_key, data, ttl=60)
    return data


def _portal_group_name(value: object, *, fallback: str) -> str:
    if isinstance(value, str):
        text = value.strip()
        return text or fallback
    return fallback


def _is_portal_privileged_viewer(user: User) -> bool:
    return role_matches_any(user, ("admin", "system_admin")) or bool(getattr(user, "can_view_all", False))


async def get_portal_skill(db: AsyncSession, skill_id: str, user: User) -> dict:
    skill = await require_skill_access(db, skill_id, user, "read")
    owner_name = ""
    if skill.owner:
        owner_name = (await db.execute(select(User.name).where(User.id == skill.owner))).scalar_one_or_none() or skill.owner
    org_name = None
    if skill.org_unit_id:
        org_name = (await db.execute(select(OrgUnit.name).where(OrgUnit.id == skill.org_unit_id))).scalar_one_or_none()
    recent_stmt = (
        select(SkillSubmission, User.name)
        .join(User, User.id == SkillSubmission.requester_id, isouter=True)
        .where(SkillSubmission.skill_id == skill_id)
        .order_by(SkillSubmission.created_at.desc())
        .limit(5)
    )
    if not _is_portal_privileged_viewer(user):
        recent_stmt = recent_stmt.where(SkillSubmission.requester_id == user.id)
    recent_rows = await db.execute(recent_stmt)
    tag_map = await _skill_tags_map(db, [skill_id])
    return {
        "id": skill.id,
        "display_name": skill.display_name or skill.name,
        "summary": skill.summary or skill.description or "",
        "description": _extract_description(skill.id),
        "category": skill.category or "通用",
        "icon": skill.icon or "apps",
        "owner": {"id": skill.owner, "name": owner_name} if skill.owner else None,
        "org_unit": {"id": skill.org_unit_id, "name": org_name or skill.department} if skill.org_unit_id or skill.department else None,
        "param_ui_schema": _portal_safe_param_ui_schema(skill.param_ui_schema),
        "result_ui_schema": portal_ui_service.public_result_ui_schema(skill.result_ui_schema),
        "recent_runs": [
            {
                "id": sub.id,
                "status": sub.status,
                "created_at": _iso(sub.created_at),
                "requester_name": requester_name or sub.requester_id,
            }
            for sub, requester_name in recent_rows.all()
        ],
        "tags": tag_map.get(skill_id, []),
        "visibility": skill.visibility,
        "status": skill.status,
        "usage_count": skill.usage_count or 0,
        "success_rate": skill.success_rate,
        "permissions": await get_skill_permissions(db, skill, user),
    }


# ---------------------------------------------------------------------------
# 参数验证（支持嵌套对象 + 条件联动）
# ---------------------------------------------------------------------------

def _check_depends_on(depends_on: dict | None, payload: dict) -> bool:
    """判断 dependsOn 条件是否满足（字段应该可见/有效）。

    返回 True 表示条件满足、字段有效。
    返回 False 表示条件不满足、字段应跳过验证。
    """
    if not isinstance(depends_on, dict):
        return True  # 无条件依赖，始终有效

    dep_field = depends_on.get("field", "")
    if not dep_field:
        return True

    dep_value = _get_nested(payload, dep_field)
    operator = depends_on.get("operator", "eq")

    if operator == "eq":
        return dep_value == depends_on.get("value")
    if operator == "ne":
        return dep_value != depends_on.get("value")
    if operator == "in":
        values = depends_on.get("values", [])
        return isinstance(values, list) and dep_value in values
    if operator == "notEmpty":
        return dep_value is not None and dep_value != ""

    return True


def _get_nested(obj: dict, path: str):
    """按点分隔路径从嵌套字典取值。"""
    keys = path.split(".")
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _validate_field(key: str, value, spec: dict, payload: dict) -> None:
    """验证单个字段值是否符合 spec 规则。"""
    if not isinstance(spec, dict):
        return

    # 条件联动：依赖不满足时跳过
    depends_on = spec.get("dependsOn")
    if not _check_depends_on(depends_on, payload):
        return

    if value in (None, ""):
        return

    enum = spec.get("enum")
    if enum and value not in enum:
        raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {key} 不在允许选项中"})

    expected_type = spec.get("type")
    if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {key} 必须是整数"})
    if expected_type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {key} 必须是数字"})
    if expected_type == "boolean" and not isinstance(value, bool):
        raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {key} 必须是布尔值"})
    if expected_type == "array" and not isinstance(value, list):
        raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {key} 必须是数组"})
    if expected_type == "object" and not isinstance(value, dict):
        raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {key} 必须是对象"})
    if expected_type == "string" and not isinstance(value, str):
        raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {key} 必须是字符串"})

    if spec.get("format") == "date" and isinstance(value, str):
        try:
            parse_bjt_datetime(value)
        except ValueError as exc:
            raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {key} 必须是日期"}) from exc

    # 嵌套对象：递归验证
    if expected_type == "object" and isinstance(value, dict):
        if "properties" in spec:
            nested_props = spec.get("properties", {})
            nested_required = spec.get("required", [])
            _validate_properties(nested_props, nested_required, value, payload, prefix=f"{key}.")

    if expected_type == "array" and isinstance(value, list):
        _validate_array_items(key, value, spec.get("items"), payload)


def _validate_array_items(key: str, values: list, item_spec, root_payload: dict) -> None:
    """验证数组 items schema，防止数组对象绕过字段白名单。"""
    if not isinstance(item_spec, dict):
        return

    expected_type = item_spec.get("type")
    for index, item in enumerate(values):
        item_key = f"{key}[{index}]"
        if expected_type == "object":
            if not isinstance(item, dict):
                raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {item_key} 必须是对象"})
            if "properties" in item_spec:
                nested_props = item_spec.get("properties", {})
                nested_required = item_spec.get("required", [])
                _validate_properties(nested_props, nested_required, item, root_payload, prefix=f"{item_key}.")
            continue
        _validate_field(item_key, item, item_spec, root_payload)


def _validate_properties(
    properties: dict,
    required: list | None,
    payload: dict,
    root_payload: dict,
    prefix: str = "",
    *,
    reject_unknown: bool = True,
) -> None:
    """验证 properties 内所有字段。

    root_payload 用于 dependsOn 解析（始终从根对象查找依赖字段）。
    """
    if not isinstance(payload, dict):
        raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {prefix[:-1] or 'params'} 必须是对象"})

    if not isinstance(properties, dict):
        if reject_unknown and payload:
            unknown_keys = sorted(str(key) for key in payload)
            first_key = f"{prefix}{unknown_keys[0]}"
            raise AppError("PARAM_INVALID", 422, {"detail": f"未知参数 {first_key}"})
        properties = {}

    if reject_unknown:
        unknown_keys = sorted(str(key) for key in payload if key not in properties)
        if unknown_keys:
            first_key = f"{prefix}{unknown_keys[0]}"
            raise AppError("PARAM_INVALID", 422, {"detail": f"未知参数 {first_key}"})

    if isinstance(required, list):
        for key in required:
            spec = properties.get(key, {})
            depends_on = spec.get("dependsOn") if isinstance(spec, dict) else None
            # 依赖条件不满足时跳过 required 检查
            if not _check_depends_on(depends_on, root_payload):
                continue
            if payload.get(key) in (None, ""):
                raise AppError(
                    "PARAM_INVALID",
                    422,
                    {"detail": f"缺少必填参数 {prefix}{key}"},
                )

    if isinstance(properties, dict):
        for key, spec in properties.items():
            if key not in payload or not isinstance(spec, dict):
                continue
            _validate_field(f"{prefix}{key}", payload[key], spec, root_payload)


def _validate_params(skill: Skill, params: dict | None) -> dict:
    payload = params or {}
    if not isinstance(payload, dict):
        raise AppError("PARAM_INVALID", 422, {"detail": "params 必须是对象"})
    _reject_portal_control_or_secret_params(payload)
    schema = _portal_safe_param_ui_schema(skill.param_ui_schema) or {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    reject_unknown = isinstance(schema, dict) and "properties" in schema
    _validate_properties(properties, required, payload, payload, reject_unknown=reject_unknown)
    return payload


def _reject_portal_control_or_secret_params(payload: dict, prefix: str = "") -> None:
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        path = f"{prefix}.{key}" if prefix else key
        if key.startswith("_") or key in PORTAL_RESERVED_PARAM_KEYS:
            raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {path} 为系统保留字段"})
        if portal_ui_service.is_secret_like_param_path(path):
            raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {path} 疑似密钥字段，不能通过 Portal 提交"})
        _reject_secret_like_param_value(path, value)
        if isinstance(value, dict):
            _reject_portal_control_or_secret_params(value, path)


def _reject_secret_like_param_value(path: str, value) -> None:
    if isinstance(value, str) and portal_ui_service.contains_secret_text(value):
        raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {path} 疑似包含密钥值，不能通过 Portal 提交"})
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_like_param_value(f"{path}[{index}]", item)
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            child_path = f"{path}.{key}" if path else key
            if key.startswith("_") or key in PORTAL_RESERVED_PARAM_KEYS:
                raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {child_path} 为系统保留字段"})
            if portal_ui_service.is_secret_like_param_path(child_path):
                raise AppError("PARAM_INVALID", 422, {"detail": f"参数 {child_path} 疑似密钥字段，不能通过 Portal 提交"})
            _reject_secret_like_param_value(child_path, item)


async def submit_portal_skill(
    db: AsyncSession,
    skill_id: str,
    user: User,
    params: dict | None,
    *,
    ui_pref_id: str | None = None,
    ui_pref_version: int | None = None,
    merged_ui_schema_hash: str | None = None,
    ui_surface: str | None = "run_form",
) -> dict:
    skill = await require_skill_access(db, skill_id, user, "execute")
    payload = _validate_params(skill, params)
    ui_context = await portal_ui_service.get_submission_ui_context(
        db,
        skill=skill,
        user=user,
        surface=ui_surface,
        ui_pref_id=ui_pref_id,
        ui_pref_version=ui_pref_version,
        merged_ui_schema_hash=merged_ui_schema_hash,
    )
    ui_snapshot_json = copy.deepcopy(ui_context["ui_snapshot_json"])
    ui_snapshot_json["actual_params"] = copy.deepcopy(payload)
    portal_ui_service.validate_ui_snapshot_json(ui_snapshot_json)

    submission = SkillSubmission(
        id=f"sub-{uuid4().hex[:12]}",
        skill_id=skill_id,
        requester_id=user.id,
        org_unit_id=skill.org_unit_id,
        params=payload,
        ui_pref_id=ui_context["ui_pref_id"],
        ui_pref_version=ui_context["ui_pref_version"],
        ui_surface=ui_context["ui_surface"],
        base_skill_commit=ui_context["base_skill_commit"],
        merged_ui_schema_hash=ui_context["merged_ui_schema_hash"],
        ui_snapshot_json=ui_snapshot_json,
        status="pending",
    )
    db.add(submission)
    await db.flush()

    result = await execution_service.execute_skill(
        skill_id=skill_id,
        params=payload,
        sandbox=False,
        triggered_by=f"portal:{user.id}",
        run_metadata={
            "portal_submission_id": submission.id,
            "user_id": user.id,
            "requester_id": user.id,
            "ui": ui_snapshot_json,
        },
    )
    submission.execution_id = result.get("run_id")
    submission.status = result.get("status", "pending")
    skill.usage_count = (skill.usage_count or 0) + 1
    skill.last_run_at = now_bjt()

    await _cache_delete_pattern_safe("portal:skills:*")
    await _cache_delete_pattern_safe("portal:overview:*")

    return {
        "id": submission.id,
        "status": submission.status,
        "execution_id": submission.execution_id,
        "ui_pref_id": submission.ui_pref_id,
        "ui_pref_version": submission.ui_pref_version,
        "base_skill_commit": submission.base_skill_commit,
        "merged_ui_schema_hash": submission.merged_ui_schema_hash,
    }


async def list_my_submissions(
    db: AsyncSession,
    user: User,
    *,
    status: str | None = None,
    skill_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    stmt = (
        select(SkillSubmission, Skill.display_name, Skill.name)
        .join(Skill, Skill.id == SkillSubmission.skill_id)
        .where(SkillSubmission.requester_id == user.id)
    )
    if status:
        stmt = stmt.where(SkillSubmission.status == status)
    if skill_id:
        stmt = stmt.where(SkillSubmission.skill_id == skill_id)
    stmt = stmt.order_by(SkillSubmission.created_at.desc())
    rows = (await db.execute(stmt)).all()
    total = len(rows)
    rows = rows[(page - 1) * page_size: page * page_size]
    return {
        "total": total,
        "items": [
            {
                "id": sub.id,
                "skill_id": sub.skill_id,
                "skill_display_name": display_name or name or sub.skill_id,
                "status": sub.status,
                "params": portal_ui_service.redact_sensitive_ui_response_value(sub.params),
                "ui_pref_id": sub.ui_pref_id,
                "ui_pref_version": sub.ui_pref_version,
                "base_skill_commit": sub.base_skill_commit,
                "merged_ui_schema_hash": sub.merged_ui_schema_hash,
                "created_at": _iso(sub.created_at),
                "completed_at": _iso(sub.completed_at),
            }
            for sub, display_name, name in rows
        ],
    }


def _build_result_summary(raw_output: dict | None) -> dict | None:
    if raw_output is None:
        return None
    if isinstance(raw_output, dict):
        if isinstance(raw_output.get("data"), list):
            return {
                "type": "table",
                "data": raw_output.get("data"),
                "analysis": raw_output.get("analysis"),
            }
        return {
            "type": "text",
            "data": raw_output,
            "analysis": raw_output.get("analysis") if isinstance(raw_output.get("analysis"), str) else None,
        }
    return {"type": "text", "data": raw_output}


def _can_view_submission_detail(user: User, submission: SkillSubmission) -> bool:
    if submission.requester_id == user.id:
        return True
    return _is_portal_privileged_viewer(user)


async def get_submission_detail(db: AsyncSession, submission_id: str, user: User) -> dict:
    submission = await db.get(SkillSubmission, submission_id)
    if not submission:
        raise AppError("NOT_FOUND", 404)
    if not _can_view_submission_detail(user, submission):
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    skill = await db.get(Skill, submission.skill_id)
    run = None
    decision = None
    if submission.execution_id:
        run = await db.get(ExecutionRun, submission.execution_id)
        decision = (
            await db.execute(select(DecisionLog).where(DecisionLog.run_id == submission.execution_id).order_by(DecisionLog.created_at.desc()))
        ).scalars().first()

    if run:
        submission.status = run.status
        submission.completed_at = run.completed_at
    if decision and submission.result_summary is None:
        submission.result_summary = _build_result_summary(decision.output_result)
        submission.error_message = submission.error_message or None

    return {
        "id": submission.id,
        "skill_id": submission.skill_id,
        "skill_display_name": (skill.display_name if skill else None) or (skill.name if skill else submission.skill_id),
        "status": submission.status,
        "params": portal_ui_service.redact_sensitive_ui_response_value(submission.params or {}),
        "ui_pref_id": submission.ui_pref_id,
        "ui_pref_version": submission.ui_pref_version,
        "ui_surface": submission.ui_surface,
        "base_skill_commit": submission.base_skill_commit,
        "merged_ui_schema_hash": submission.merged_ui_schema_hash,
        "ui_snapshot_json": portal_ui_service.redact_sensitive_ui_response_value(submission.ui_snapshot_json),
        "result_summary": portal_ui_service.redact_sensitive_ui_response_value(submission.result_summary),
        "result_ui_schema": portal_ui_service.public_result_ui_schema(skill.result_ui_schema if skill else None),
        "error_message": portal_ui_service.redact_sensitive_ui_response_value(submission.error_message),
        "execution_id": submission.execution_id,
        "created_at": _iso(submission.created_at),
        "completed_at": _iso(submission.completed_at),
    }


async def get_portal_overview(db: AsyncSession, user: User) -> dict:
    user_orgs = await _user_org_ids(db, user.id)
    if not user_orgs and user.department:
        user_orgs = {user.department}
    primary_org = next(iter(sorted(user_orgs)), None)
    cache_key = f"portal:overview:{user.id}:{_user_permissions_rev(user)}:{primary_org or 'none'}"
    cached = await _cache_get_safe(cache_key)
    if cached is not None:
        return cached

    visible_skills = (await list_portal_skills(db, user, page=1, page_size=1000))["items"]
    visible_ids = {item["id"] for item in visible_skills}
    active_skills = len([item for item in visible_skills if item.get("status") == "active"])

    today = now_bjt().date()
    recent_stmt = (
        select(SkillSubmission, Skill.display_name, Skill.name, User.name)
        .join(Skill, Skill.id == SkillSubmission.skill_id)
        .join(User, User.id == SkillSubmission.requester_id, isouter=True)
        .order_by(SkillSubmission.created_at.desc())
        .limit(50)
    )
    recent_rows = (await db.execute(recent_stmt)).all()
    recent_filtered = []
    today_runs = 0
    completed_7d = 0
    total_7d = 0
    since = now_bjt() - timedelta(days=7)
    for sub, display_name, skill_name, requester_name in recent_rows:
        if sub.skill_id not in visible_ids:
            continue
        if sub.created_at and sub.created_at.date() == today:
            today_runs += 1
        if sub.created_at and sub.created_at >= since:
            total_7d += 1
            if sub.status == "completed":
                completed_7d += 1
        can_view_detail = _can_view_submission_detail(user, sub)
        recent_filtered.append(
            {
                "id": sub.id if can_view_detail else f"summary-{len(recent_filtered)}",
                "detail_id": sub.id if can_view_detail else None,
                "can_view_detail": can_view_detail,
                "skill_id": sub.skill_id,
                "skill_display_name": display_name or skill_name or sub.skill_id,
                "requester_name": requester_name or sub.requester_id,
                "status": sub.status,
                "created_at": _iso(sub.created_at),
            }
        )

    org_name = None
    if primary_org:
        org_name = (await db.execute(select(OrgUnit.name).where(OrgUnit.id == primary_org))).scalar_one_or_none() or primary_org

    data = {
        "org_unit": {"id": primary_org, "name": org_name} if primary_org else None,
        "stats": {
            "total_skills": len(visible_ids),
            "active_skills": active_skills,
            "today_runs": today_runs,
            "success_rate_7d": round(completed_7d / total_7d, 4) if total_7d else 0,
            "total_runs_7d": total_7d,
        },
        "recent_submissions": recent_filtered[:10],
    }
    await _cache_set_safe(cache_key, data, ttl=120)
    return data


async def get_portal_market(db: AsyncSession, user: User) -> dict:
    from app.skills.template_market import list_templates

    skills = await list_portal_skills(db, user, page=1, page_size=100)
    templates = list_templates()
    categories = sorted({
        *(item.get("category") or "通用" for item in skills["items"]),
        *(item.get("category") or "通用" for item in templates),
    })
    return {
        "categories": categories,
        "skills": skills["items"],
        "templates": templates,
        "stats": {
            "skill_count": len(skills["items"]),
            "template_count": len(templates),
        },
    }
