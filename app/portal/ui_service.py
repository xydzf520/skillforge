"""个人 Skill 图形化界面 overlay 服务。

个人 overlay 只改变当前用户在当前 Skill 上的交互界面。这里刻意只接受
声明式 schema，并在服务端做组件白名单、字段绑定和可执行内容拦截。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.exceptions import AppError
from app.common.time_utils import now_bjt, parse_bjt_datetime
from app.portal.models import UserSkillUIPreference
from app.skills.core.access import get_skill_permissions, require_skill_access
from app.skills.core.models import Skill

SCHEMA_VERSION = "skill-ui/v1"
SURFACES = {"run_form", "result", "dashboard"}
MAX_UI_OVERLAY_BYTES = 64 * 1024
MAX_MERGED_UI_SCHEMA_BYTES = 512 * 1024
MAX_UI_SNAPSHOT_BYTES = 768 * 1024
MAX_UI_COMPONENTS = 500
MAX_UI_COMPONENT_DEPTH = 8
MAX_UI_AI_INSTRUCTION_CHARS = 2000
MAX_UI_PROMPT_SUMMARY_CHARS = 2000

ALLOWED_COMPONENT_TYPES = {
    "form_group",
    "field",
    "metric",
    "metric_group",
    "table",
    "line_chart",
    "bar_chart",
    "pie_chart",
    "report_section",
    "markdown_summary",
    "timeline",
    "alert",
    "tabs",
}
FORBIDDEN_COMPONENT_TYPES = {
    "html",
    "script",
    "iframe",
    "custom_component",
    "remote_widget",
    "webview",
}
ALLOWED_OPERATION_TYPES = {
    "reorder_component",
    "hide_component",
    "show_component",
    "rename_title",
    "set_default_view",
    "set_chart_type",
    "set_chart_binding",
    "set_table_columns",
    "set_form_group",
    "set_personal_default",
}
ALLOWED_OPERATION_KEYS = {
    "op",
    "type",
    "component_id",
    "component_ids",
    "title",
    "binding",
    "bindings",
    "chart_type",
    "columns",
    "field",
    "path",
    "value",
    "defaults",
    "view",
    "group_id",
    "group_title",
    "children",
    "order",
}
CHART_COMPONENT_TYPES = {"line_chart", "bar_chart", "pie_chart"}
TABLE_COLUMN_KEYS = {"key", "title", "format", "binding"}
PUBLIC_RESULT_COLUMN_KEYS = {"key", "title", "format"}
PUBLIC_RESULT_SCHEMA_KEYS = {"type", "title", "columns"}
FORBIDDEN_OPERATION_TYPES = {
    "add_datasource",
    "add_mcp_scope",
    "change_trigger",
    "change_skill_code",
    "change_workflow",
    "change_permission",
    "change_visibility",
    "change_required",
    "publish",
    "write_git",
    "deploy_node",
}
ALLOWED_TOP_LEVEL_KEYS = {
    "schema_version",
    "surface",
    "layout",
    "components",
    "hidden_component_ids",
    "component_order",
    "personal_defaults",
    "default_view",
    "operations",
    "notes",
}
FORBIDDEN_SCHEMA_KEYS = {
    "html",
    "script",
    "iframe",
    "style",
    "css",
    "class",
    "classname",
    "srcdoc",
    "innerhtml",
    "outerhtml",
    "dangerouslysetinnerhtml",
    "remote",
    "url",
    "href",
    "eval",
    "function",
    "code",
}
ALLOWED_COMPONENT_KEYS = {
    "id",
    "type",
    "title",
    "description",
    "binding",
    "bindings",
    "field",
    "field_type",
    "columns",
    "chart_type",
    "visible",
    "order",
    "format",
    "options",
    "children",
    "group",
    "summary",
}
ALLOWED_BINDING_PREFIXES = (
    "params.",
    "result.",
    "reports.",
    "todos.",
    "run_trace.safe_summary.",
)
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
SAFE_BINDING_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
HTML_TAG_RE = re.compile(r"<\s*(?:!--|/?[A-Za-z][A-Za-z0-9:-]*)(?:\s|/?>)")
EVENT_HANDLER_ATTR_RE = re.compile(r"\bon[A-Za-z]+\s*=")
FORBIDDEN_STRING_PATTERNS = (
    "javascript:",
    "vbscript:",
    "data:text/html",
    "data:image/svg+xml",
    "expression(",
    "@import",
    "url(",
)
SECRET_PARAM_WORDS = {
    "token",
    "secret",
    "password",
    "credential",
    "credentials",
    "cookie",
    "authorization",
}
SECRET_PARAM_COMPACT_MARKERS = {
    "apikey",
    "appkey",
    "accesskey",
    "accesskeys",
    "accesstoken",
    "refreshtoken",
    "authtoken",
    "authheader",
    "mcpenv",
    "dingtalktoken",
    "privatekey",
    "secretkey",
    "serviceaccountkey",
    "signingkey",
    "sshkey",
}
SECRET_PARAM_COMPACT_SUFFIXES = {"token", "secret", "password", "credential", "cookie"}


def normalize_surface(surface: str | None) -> str:
    value = (surface or "run_form").strip()
    if value not in SURFACES:
        raise AppError("PARAM_INVALID", 422, {"detail": "unsupported ui surface"})
    return value


def stable_schema_hash(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _json_size_bytes(value) -> int:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(raw.encode("utf-8"))


def _validate_json_size(value, *, max_bytes: int, label: str) -> None:
    size = _json_size_bytes(value)
    if size > max_bytes:
        raise AppError("PARAM_INVALID", 422, {"detail": f"{label} too large: {size}>{max_bytes}"})


def _validate_merged_schema_budget(merged: dict) -> None:
    _validate_json_size(merged, max_bytes=MAX_MERGED_UI_SCHEMA_BYTES, label="merged ui schema")


def validate_ui_snapshot_json(snapshot: dict) -> None:
    _validate_json_size(snapshot, max_bytes=MAX_UI_SNAPSHOT_BYTES, label="ui snapshot")


def _normalize_ai_instruction(instruction: str) -> str:
    value = (instruction or "").strip()
    if not value:
        raise AppError("PARAM_INVALID", 422, {"detail": "ui ai instruction required"})
    if len(value) > MAX_UI_AI_INSTRUCTION_CHARS:
        raise AppError("PARAM_INVALID", 422, {"detail": "ui ai instruction too long"})
    return _redact_user_prompt_text(value, limit=MAX_UI_AI_INSTRUCTION_CHARS)


def _normalize_prompt_summary(prompt_summary: str | None) -> str | None:
    value = (prompt_summary or "").strip()
    if not value:
        return None
    if len(value) > MAX_UI_PROMPT_SUMMARY_CHARS:
        raise AppError("PARAM_INVALID", 422, {"detail": "ui prompt summary too long"})
    return _redact_user_prompt_text(value, limit=MAX_UI_PROMPT_SUMMARY_CHARS)


def _redact_user_prompt_text(value: str, *, limit: int) -> str:
    from app.common.ai import redact_secret_text

    return redact_secret_text(value, limit=limit)


def redact_sensitive_ui_response_value(value, *, key_path: str = ""):
    """Redact historical UI snapshots before returning them to clients.

    This is read-side only. It protects old rows written before current
    fail-closed validators existed without mutating immutable run traces.
    """
    from app.common.ai import redact_secret_text

    if isinstance(value, dict):
        redacted: dict = {}
        for index, (key, item) in enumerate(value.items()):
            raw_key = str(key)
            if _is_secret_like_param_path(raw_key):
                safe_key = f"[REDACTED_KEY_{index}]"
            else:
                safe_key = key
            redacted[safe_key] = redact_sensitive_ui_response_value(
                item,
                key_path=f"{key_path}.{raw_key}" if key_path else raw_key,
            )
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_ui_response_value(item, key_path=key_path) for item in value]
    if isinstance(value, str):
        if _is_secret_like_param_path(key_path) or _is_secret_like_param_path(value):
            return "[REDACTED]"
        return redact_secret_text(value, limit=max(len(value) + 64, 256))
    if key_path and _is_secret_like_param_path(key_path) and value not in (None, ""):
        return "[REDACTED]"
    return value


def _field_title(key: str, spec: dict) -> str:
    return str(spec.get("title") or spec.get("label") or key)


def _component_id(prefix: str, path: str) -> str:
    return f"{prefix}-{path.replace('.', '-')}"


def _iter_param_fields(
    properties: dict,
    *,
    required: Iterable[str] | None = None,
    prefix: str = "",
) -> Iterable[tuple[str, dict, bool]]:
    required_set = set(required or [])
    for key, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        path = f"{prefix}.{key}" if prefix else key
        yield path, spec, key in required_set
        if spec.get("type") == "object" and isinstance(spec.get("properties"), dict):
            yield from _iter_param_fields(
                spec["properties"],
                required=spec.get("required") or [],
                prefix=path,
            )


def _allowed_param_roots(skill: Skill) -> set[str]:
    schema = skill.param_ui_schema if isinstance(skill.param_ui_schema, dict) else {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    if not isinstance(props, dict):
        return set()
    return {
        key
        for key, spec in props.items()
        if isinstance(key, str)
        and isinstance(spec, dict)
        and not _is_secret_like_param_spec(key, spec)
    }


def _allowed_param_paths(skill: Skill) -> set[str]:
    schema = skill.param_ui_schema if isinstance(skill.param_ui_schema, dict) else {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    required = schema.get("required") if isinstance(schema, dict) else []
    if not isinstance(props, dict):
        return set()
    return {
        path
        for path, spec, _required in _iter_param_fields(props, required=required)
        if not _is_secret_like_param_spec(path, spec)
    }


def _param_spec_map(skill: Skill) -> dict[str, dict]:
    schema = skill.param_ui_schema if isinstance(skill.param_ui_schema, dict) else {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    required = schema.get("required") if isinstance(schema, dict) else []
    if not isinstance(props, dict):
        return {}
    return {
        path: spec
        for path, spec, _required in _iter_param_fields(props, required=required)
        if not _is_secret_like_param_spec(path, spec)
    }


def _run_form_default_schema(skill: Skill) -> dict:
    schema = skill.param_ui_schema if isinstance(skill.param_ui_schema, dict) else {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    required = schema.get("required") if isinstance(schema, dict) else []
    components = []
    personal_defaults = {}
    if isinstance(props, dict):
        for path, spec, is_required in _iter_param_fields(props, required=required):
            if _is_secret_like_param_spec(path, spec):
                continue
            binding = f"params.{path}"
            safe_format = _safe_ui_text(spec.get("format"))
            safe_options = [
                item
                for item in (spec.get("enum") or [])
                if not _is_secret_like_default_value(item)
            ] if isinstance(spec.get("enum"), list) else None
            component = {
                "id": _component_id("field", path),
                "type": "field",
                "title": _safe_ui_text(_field_title(path.split(".")[-1], spec)),
                "field": path,
                "field_type": spec.get("type") or "string",
                "binding": binding,
                "required": is_required,
                "description": _safe_ui_text(spec.get("description")),
                "format": safe_format if safe_format != "[REDACTED]" else None,
                "options": safe_options,
            }
            components.append({k: v for k, v in component.items() if v is not None})
            if spec.get("default") is not None and not _is_secret_like_default_value(spec.get("default")):
                personal_defaults[path] = spec.get("default")
    return {
        "schema_version": SCHEMA_VERSION,
        "skill_id": skill.id,
        "surface": "run_form",
        "base_skill_commit": skill.git_commit,
        "layout": {"type": "form", "columns": 1},
        "components": components,
        "personal_defaults": personal_defaults,
    }


def _result_column_is_secret_like(column: dict) -> bool:
    key = column.get("key")
    if isinstance(key, str) and _is_secret_like_param_path(key):
        return True
    title = column.get("title")
    if isinstance(title, str) and (_is_secret_like_param_path(title) or _contains_secret_text(title)):
        return True
    for path in _binding_paths(column.get("binding")):
        if _is_secret_like_param_path(path):
            return True
        if path.startswith("result.") and _is_secret_like_param_path(path[len("result."):]):
            return True
    return False


def _safe_result_columns(columns, *, public_only: bool = False) -> list:
    if not isinstance(columns, list):
        return []
    safe_columns = []
    for column in columns:
        if not isinstance(column, dict) or _result_column_is_secret_like(column):
            continue
        if not isinstance(column.get("key"), str) or not SAFE_ID_RE.match(column["key"]):
            continue
        allowed_keys = PUBLIC_RESULT_COLUMN_KEYS if public_only else TABLE_COLUMN_KEYS
        safe_column = {
            key: copy.deepcopy(value)
            for key, value in column.items()
            if key in allowed_keys
        }
        safe_column["key"] = column["key"]
        title = _safe_ui_text(column.get("title") or column["key"])
        safe_column["title"] = title or column["key"]
        if "format" in safe_column and not isinstance(safe_column.get("format"), str):
            safe_column.pop("format", None)
        if isinstance(safe_column.get("format"), str):
            safe_format = _safe_ui_text(safe_column["format"])
            if safe_format == "[REDACTED]":
                safe_column.pop("format", None)
            else:
                safe_column["format"] = safe_format
        if not public_only and "binding" in safe_column and not isinstance(safe_column.get("binding"), str):
            safe_column.pop("binding", None)
        safe_columns.append(safe_column)
    return safe_columns


def public_result_ui_schema(schema: dict | None) -> dict | None:
    if not isinstance(schema, dict):
        return schema
    sanitized: dict = {}
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        safe_type = _safe_ui_text(schema_type)
        if safe_type and safe_type != "[REDACTED]" and SAFE_ID_RE.match(safe_type):
            sanitized["type"] = safe_type
    if isinstance(schema.get("title"), str):
        sanitized["title"] = _safe_ui_text(schema.get("title"))
    if isinstance(schema.get("columns"), list):
        sanitized["columns"] = _safe_result_columns(schema.get("columns"), public_only=True)
    for key in list(sanitized.keys()):
        if key not in PUBLIC_RESULT_SCHEMA_KEYS:
            sanitized.pop(key, None)
    return sanitized


def _result_default_schema(skill: Skill, surface: str) -> dict:
    result_schema = skill.result_ui_schema if isinstance(skill.result_ui_schema, dict) else {}
    columns = _safe_result_columns(result_schema.get("columns") if isinstance(result_schema, dict) else None)
    title = _safe_ui_text(result_schema.get("title")) if isinstance(result_schema, dict) else None
    if isinstance(columns, list) and columns:
        components = [{
            "id": "result-table",
            "type": "table",
            "title": title or "执行结果",
            "binding": "result.data",
            "columns": columns,
        }]
    elif surface == "dashboard":
        components = [{
            "id": "result-metrics",
            "type": "metric_group",
            "title": "关键指标",
            "binding": "run_trace.safe_summary.metrics",
        }]
    else:
        components = [{
            "id": "result-summary",
            "type": "report_section",
            "title": title or "执行结果",
            "binding": "result",
        }]
    return {
        "schema_version": SCHEMA_VERSION,
        "skill_id": skill.id,
        "surface": surface,
        "base_skill_commit": skill.git_commit,
        "layout": {"type": surface},
        "components": components,
        "personal_defaults": {},
    }


def default_ui_schema_for_skill(skill: Skill, surface: str | None = None) -> dict:
    resolved = normalize_surface(surface)
    if resolved == "run_form":
        return _run_form_default_schema(skill)
    return _result_default_schema(skill, resolved)


def _customize_action_for_surface(surface: str) -> str:
    return "execute" if surface == "run_form" else "read"


def _required_run_form_component_ids(skill: Skill) -> set[str]:
    schema = default_ui_schema_for_skill(skill, "run_form")
    return {
        str(component.get("id"))
        for component in _iter_components(schema.get("components"))
        if component.get("type") == "field" and component.get("required") is True and component.get("id")
    }


def _looks_like_event_handler(key: str) -> bool:
    lowered = key.lower()
    return lowered in {
        "onclick",
        "onchange",
        "onload",
        "onerror",
        "onsubmit",
        "onmouseover",
    } or lowered.startswith(("on_", "on-", "v-on:"))


def _contains_secret_text(value: str) -> bool:
    from app.common.ai import redact_secret_text

    limit = max(len(value) + 64, 256)
    return redact_secret_text(value, limit=limit) != value[:limit]


def contains_secret_text(value: str) -> bool:
    return _contains_secret_text(value)


def _validate_no_executable(value, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise AppError("PARAM_INVALID", 422, {"detail": f"{path} contains non-string key"})
            if _is_secret_like_param_path(key) or _contains_secret_text(key):
                raise AppError("PARAM_INVALID", 422, {"detail": f"forbidden secret-like ui key at {path}"})
            lowered = key.lower()
            if lowered in FORBIDDEN_SCHEMA_KEYS or _looks_like_event_handler(key):
                raise AppError("PARAM_INVALID", 422, {"detail": f"forbidden ui key: {key}"})
            _validate_no_executable(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_no_executable(item, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if (
            any(pattern in lowered for pattern in FORBIDDEN_STRING_PATTERNS)
            or HTML_TAG_RE.search(value)
            or EVENT_HANDLER_ATTR_RE.search(value)
        ):
            raise AppError("PARAM_INVALID", 422, {"detail": f"forbidden executable ui value at {path}"})
        if _is_secret_like_param_path(value) or _contains_secret_text(value):
            raise AppError("PARAM_INVALID", 422, {"detail": f"forbidden secret-like ui value at {path}"})


def _binding_paths(bindings) -> Iterable[str]:
    if isinstance(bindings, str):
        yield bindings
    elif isinstance(bindings, dict):
        for value in bindings.values():
            yield from _binding_paths(value)
    elif isinstance(bindings, list):
        for item in bindings:
            yield from _binding_paths(item)


def _iter_components(components) -> Iterable[dict]:
    if not isinstance(components, list):
        return
    for component in components:
        if not isinstance(component, dict):
            continue
        yield component
        yield from _iter_components(component.get("children"))


def _validate_components_list(components, *, label: str) -> list:
    if components is None:
        return []
    if not isinstance(components, list):
        raise AppError("PARAM_INVALID", 422, {"detail": f"{label} must be list"})
    return components


def _component_count_and_depth(components, depth: int = 1) -> tuple[int, int]:
    if not isinstance(components, list):
        return 0, 0
    count = 0
    max_depth = 0
    for component in components:
        if not isinstance(component, dict):
            continue
        count += 1
        max_depth = max(max_depth, depth)
        child_count, child_depth = _component_count_and_depth(component.get("children"), depth + 1)
        count += child_count
        max_depth = max(max_depth, child_depth)
    return count, max_depth


def _validate_component_budget(components, *, label: str) -> None:
    count, depth = _component_count_and_depth(components)
    if count > MAX_UI_COMPONENTS:
        raise AppError("PARAM_INVALID", 422, {"detail": f"{label} has too many components: {count}>{MAX_UI_COMPONENTS}"})
    if depth > MAX_UI_COMPONENT_DEPTH:
        raise AppError("PARAM_INVALID", 422, {"detail": f"{label} component tree too deep: {depth}>{MAX_UI_COMPONENT_DEPTH}"})


def _validate_binding_path(path: str, *, skill: Skill, surface: str) -> None:
    if not isinstance(path, str) or not SAFE_BINDING_RE.match(path):
        raise AppError("PARAM_INVALID", 422, {"detail": f"invalid ui binding: {path}"})
    if not any(path.startswith(prefix) for prefix in ALLOWED_BINDING_PREFIXES):
        raise AppError("PARAM_INVALID", 422, {"detail": f"forbidden ui binding: {path}"})
    if path.startswith("params."):
        param_path = path[len("params."):]
        if param_path not in _allowed_param_paths(skill):
            raise AppError("PARAM_INVALID", 422, {"detail": f"unknown param binding: {path}"})
        if _is_secret_like_param_path(param_path):
            raise AppError("PARAM_INVALID", 422, {"detail": f"secret-like param binding not allowed: {path}"})
    if surface == "run_form" and not path.startswith("params."):
        raise AppError("PARAM_INVALID", 422, {"detail": f"run_form can only bind params: {path}"})


def is_secret_like_param_path(path: str) -> bool:
    lowered = str(path or "").lower()
    parts = [part for part in re.split(r"[._:\-\s]+", lowered) if part]
    if any(part in SECRET_PARAM_WORDS for part in parts):
        return True
    compact = re.sub(r"[^a-z0-9]", "", lowered)
    return (
        any(marker in compact for marker in SECRET_PARAM_COMPACT_MARKERS)
        or any(compact.endswith(suffix) for suffix in SECRET_PARAM_COMPACT_SUFFIXES)
    )


def is_secret_like_param_spec(path: str, spec: dict | None) -> bool:
    if _is_secret_like_param_path(path):
        return True
    if not isinstance(spec, dict):
        return False
    fmt = str(spec.get("format") or "").lower()
    if fmt in {"password", "secret", "token", "credential"}:
        return True
    for key in ("title", "label"):
        value = spec.get(key)
        if isinstance(value, str) and (_is_secret_like_param_path(value) or _contains_secret_text(value)):
            return True
    return False


def _is_secret_like_param_path(path: str) -> bool:
    return is_secret_like_param_path(path)


def _is_secret_like_param_spec(path: str, spec: dict | None) -> bool:
    return is_secret_like_param_spec(path, spec)


def _is_secret_like_default_value(value) -> bool:
    if not isinstance(value, str):
        return False
    return _is_secret_like_param_path(value) or _contains_secret_text(value)


def _safe_ui_text(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    if _is_secret_like_param_path(text) or _contains_secret_text(text):
        return "[REDACTED]"
    return text


def _validate_param_field_path(path: str | None, *, skill: Skill) -> None:
    if path is None:
        return
    if not isinstance(path, str) or not SAFE_BINDING_RE.match(path):
        raise AppError("PARAM_INVALID", 422, {"detail": f"invalid component field: {path}"})
    if path not in _allowed_param_paths(skill):
        raise AppError("PARAM_INVALID", 422, {"detail": f"unknown component field: {path}"})
    if _is_secret_like_param_path(path):
        raise AppError("PARAM_INVALID", 422, {"detail": f"secret-like component field not allowed: {path}"})


def _validate_component(component: dict, *, skill: Skill, surface: str) -> None:
    if not isinstance(component, dict):
        raise AppError("PARAM_INVALID", 422, {"detail": "component must be object"})
    unknown_keys = set(component) - ALLOWED_COMPONENT_KEYS
    if unknown_keys:
        raise AppError("PARAM_INVALID", 422, {"detail": f"unsupported component keys: {sorted(unknown_keys)}"})
    component_id = component.get("id")
    if not isinstance(component_id, str) or not SAFE_ID_RE.match(component_id):
        raise AppError("PARAM_INVALID", 422, {"detail": "component id invalid"})
    component_type = component.get("type")
    if component_type in FORBIDDEN_COMPONENT_TYPES or component_type not in ALLOWED_COMPONENT_TYPES:
        raise AppError("PARAM_INVALID", 422, {"detail": f"component type not allowed: {component_type}"})
    _validate_param_field_path(component.get("field"), skill=skill)
    _validate_table_columns(component.get("columns"), skill=skill, surface=surface)
    for path in _binding_paths(component.get("binding")):
        _validate_binding_path(path, skill=skill, surface=surface)
    for path in _binding_paths(component.get("bindings")):
        _validate_binding_path(path, skill=skill, surface=surface)
    children = component.get("children")
    if children is not None and not isinstance(children, list):
        raise AppError("PARAM_INVALID", 422, {"detail": "component children must be list"})
    for child in children or []:
        _validate_component(child, skill=skill, surface=surface)


def _validate_table_columns(columns, *, skill: Skill, surface: str) -> None:
    if columns is None:
        return
    if not isinstance(columns, list):
        raise AppError("PARAM_INVALID", 422, {"detail": "table columns must be list"})
    for column in columns:
        if not isinstance(column, dict):
            raise AppError("PARAM_INVALID", 422, {"detail": "table column must be object"})
        unknown = set(column) - TABLE_COLUMN_KEYS
        if unknown:
            raise AppError("PARAM_INVALID", 422, {"detail": f"unsupported table column keys: {sorted(unknown)}"})
        if not isinstance(column.get("key"), str) or not SAFE_ID_RE.match(column["key"]):
            raise AppError("PARAM_INVALID", 422, {"detail": "table column key invalid"})
        if column.get("title") is not None and not isinstance(column.get("title"), str):
            raise AppError("PARAM_INVALID", 422, {"detail": "table column title invalid"})
        if column.get("format") is not None and not isinstance(column.get("format"), str):
            raise AppError("PARAM_INVALID", 422, {"detail": "table column format invalid"})
        for path in _binding_paths(column.get("binding")):
            _validate_binding_path(path, skill=skill, surface=surface)


def _validate_component_id(value: str | None, *, field: str = "component_id") -> None:
    if not isinstance(value, str) or not SAFE_ID_RE.match(value):
        raise AppError("PARAM_INVALID", 422, {"detail": f"{field} invalid"})


def _reject_secret_like_personal_default_container(path: str, value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise AppError("PARAM_INVALID", 422, {"detail": f"personal default {path} contains invalid key"})
            child_path = f"{path}.{key}" if path else key
            if _is_secret_like_param_path(child_path):
                raise AppError("PARAM_INVALID", 422, {"detail": f"secret-like personal default not allowed: {child_path}"})
            _reject_secret_like_personal_default_container(child_path, item)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_like_personal_default_container(f"{path}[{index}]", item)
    elif isinstance(value, str) and _is_secret_like_default_value(value):
        raise AppError("PARAM_INVALID", 422, {"detail": f"secret-like personal default not allowed: {path}"})


def _validate_personal_default_properties(path: str, value: dict, properties, required) -> None:
    if not isinstance(properties, dict):
        if value:
            unknown_keys = sorted(str(key) for key in value)
            first_key = unknown_keys[0]
            child_path = f"{path}.{first_key}" if path else first_key
            raise AppError("PARAM_INVALID", 422, {"detail": f"unknown personal default: {child_path}"})
        return

    unknown_keys = sorted(str(key) for key in value if key not in properties)
    if unknown_keys:
        first_key = unknown_keys[0]
        child_path = f"{path}.{first_key}" if path else first_key
        raise AppError("PARAM_INVALID", 422, {"detail": f"unknown personal default: {child_path}"})

    if isinstance(required, list):
        for key in required:
            if not isinstance(key, str):
                continue
            child_spec = properties.get(key)
            child_path = f"{path}.{key}" if path else key
            if _is_secret_like_param_spec(child_path, child_spec if isinstance(child_spec, dict) else None):
                continue
            if value.get(key) in (None, ""):
                raise AppError("PARAM_INVALID", 422, {"detail": f"missing required personal default: {child_path}"})

    for key, item in value.items():
        child_spec = properties.get(key)
        child_path = f"{path}.{key}" if path else key
        if _is_secret_like_param_spec(child_path, child_spec if isinstance(child_spec, dict) else None):
            raise AppError("PARAM_INVALID", 422, {"detail": f"secret-like personal default not allowed: {child_path}"})
        if isinstance(child_spec, dict):
            _validate_personal_default_value(child_path, item, child_spec)


def _validate_personal_default_array_items(path: str, value: list, item_spec) -> None:
    if not isinstance(item_spec, dict):
        if value:
            raise AppError("PARAM_INVALID", 422, {"detail": f"unknown personal default: {path}[0]"})
        _reject_secret_like_personal_default_container(path, value)
        return
    for index, item in enumerate(value):
        _validate_personal_default_value(f"{path}[{index}]", item, item_spec)


def _validate_personal_default_value(path: str, value, spec: dict) -> None:
    if value in (None, ""):
        return
    if _is_secret_like_param_path(path):
        raise AppError("PARAM_INVALID", 422, {"detail": f"secret-like personal default not allowed: {path}"})
    if isinstance(value, str) and _is_secret_like_default_value(value):
        raise AppError("PARAM_INVALID", 422, {"detail": f"secret-like personal default not allowed: {path}"})
    enum = spec.get("enum")
    if enum and value not in enum:
        raise AppError("PARAM_INVALID", 422, {"detail": f"personal default {path} not in enum"})

    expected_type = spec.get("type")
    if expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise AppError("PARAM_INVALID", 422, {"detail": f"personal default {path} must be integer"})
    elif expected_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise AppError("PARAM_INVALID", 422, {"detail": f"personal default {path} must be number"})
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            raise AppError("PARAM_INVALID", 422, {"detail": f"personal default {path} must be boolean"})
    elif expected_type == "array":
        if not isinstance(value, list):
            raise AppError("PARAM_INVALID", 422, {"detail": f"personal default {path} must be array"})
        _validate_personal_default_array_items(path, value, spec.get("items"))
    elif expected_type == "object":
        if not isinstance(value, dict):
            raise AppError("PARAM_INVALID", 422, {"detail": f"personal default {path} must be object"})
        _validate_personal_default_properties(path, value, spec.get("properties"), spec.get("required"))
    elif expected_type == "string":
        if not isinstance(value, str):
            raise AppError("PARAM_INVALID", 422, {"detail": f"personal default {path} must be string"})

    if spec.get("format") == "date" and isinstance(value, str):
        if value == "yesterday":
            return
        try:
            parse_bjt_datetime(value)
        except ValueError as exc:
            raise AppError("PARAM_INVALID", 422, {"detail": f"personal default {path} must be date"}) from exc


def _validate_personal_defaults(defaults: dict | None, *, skill: Skill, surface: str) -> None:
    if defaults is None:
        return
    if not isinstance(defaults, dict):
        raise AppError("PARAM_INVALID", 422, {"detail": "personal_defaults must be object"})
    if surface != "run_form" and defaults:
        raise AppError("PARAM_INVALID", 422, {"detail": "personal_defaults only allowed on run_form"})
    allowed_specs = _param_spec_map(skill)
    for key, value in defaults.items():
        if not isinstance(key, str) or not key:
            raise AppError("PARAM_INVALID", 422, {"detail": "personal default key invalid"})
        spec = allowed_specs.get(key)
        if spec is None:
            raise AppError("PARAM_INVALID", 422, {"detail": f"unknown personal default: {key}"})
        if _is_secret_like_param_path(key):
            raise AppError("PARAM_INVALID", 422, {"detail": f"secret-like personal default not allowed: {key}"})
        _validate_personal_default_value(key, value, spec)


def _validate_component_id_list(values, *, field: str) -> None:
    if not isinstance(values, list):
        raise AppError("PARAM_INVALID", 422, {"detail": f"{field} must be string list"})
    if not all(isinstance(item, str) and SAFE_ID_RE.match(item) for item in values):
        raise AppError("PARAM_INVALID", 422, {"detail": f"{field} invalid"})


def _reject_hidden_required_components(components, required_component_ids: set[str]) -> None:
    if not required_component_ids:
        return
    for component in _iter_components(components):
        if component.get("visible") is False and component.get("id") in required_component_ids:
            raise AppError("PARAM_INVALID", 422, {"detail": f"cannot hide required field: {component.get('id')}"})


def validate_overlay_schema(overlay: dict | None, *, skill: Skill, surface: str) -> dict:
    if overlay is None:
        return {}
    if not isinstance(overlay, dict):
        raise AppError("PARAM_INVALID", 422, {"detail": "ui overlay must be object"})
    _validate_json_size(overlay, max_bytes=MAX_UI_OVERLAY_BYTES, label="ui overlay")
    _validate_no_executable(overlay)
    unknown_keys = set(overlay) - ALLOWED_TOP_LEVEL_KEYS
    if unknown_keys:
        raise AppError("PARAM_INVALID", 422, {"detail": f"unsupported ui overlay keys: {sorted(unknown_keys)}"})
    if overlay.get("schema_version") not in (None, SCHEMA_VERSION):
        raise AppError("PARAM_INVALID", 422, {"detail": "unsupported ui schema version"})
    if overlay.get("surface") not in (None, surface):
        raise AppError("PARAM_INVALID", 422, {"detail": "ui surface mismatch"})
    components = _validate_components_list(overlay.get("components"), label="components")
    _validate_component_budget(components, label="overlay")
    for component in components:
        _validate_component(component, skill=skill, surface=surface)
    hidden_ids = [] if overlay.get("hidden_component_ids") is None else overlay.get("hidden_component_ids")
    order_ids = [] if overlay.get("component_order") is None else overlay.get("component_order")
    _validate_component_id_list(hidden_ids, field="hidden_component_ids")
    _validate_component_id_list(order_ids, field="component_order")
    required_component_ids = _required_run_form_component_ids(skill) if surface == "run_form" else set()
    hidden_required_ids = required_component_ids.intersection(hidden_ids)
    if hidden_required_ids:
        raise AppError("PARAM_INVALID", 422, {"detail": f"cannot hide required fields: {sorted(hidden_required_ids)}"})
    _reject_hidden_required_components(overlay.get("components"), required_component_ids)
    _validate_personal_defaults(overlay.get("personal_defaults"), skill=skill, surface=surface)
    for operation in overlay.get("operations") or []:
        if not isinstance(operation, dict):
            raise AppError("PARAM_INVALID", 422, {"detail": "ui operation must be object"})
        unknown_keys = set(operation) - ALLOWED_OPERATION_KEYS
        if unknown_keys:
            raise AppError("PARAM_INVALID", 422, {"detail": f"unsupported ui operation keys: {sorted(unknown_keys)}"})
        op_type = operation.get("op") or operation.get("type")
        if op_type in FORBIDDEN_OPERATION_TYPES or op_type not in ALLOWED_OPERATION_TYPES:
            raise AppError("PARAM_INVALID", 422, {"detail": f"ui operation not allowed: {op_type}"})
        if operation.get("component_id") is not None:
            _validate_component_id(operation.get("component_id"))
        if op_type == "hide_component" and operation.get("component_id") in required_component_ids:
            raise AppError("PARAM_INVALID", 422, {"detail": f"cannot hide required field: {operation.get('component_id')}"})
        component_ids = operation.get("component_ids")
        if component_ids is None:
            component_ids = operation.get("order")
        if component_ids is None:
            component_ids = []
        if component_ids != [] and (
            not isinstance(component_ids, list)
            or not all(isinstance(item, str) and SAFE_ID_RE.match(item) for item in component_ids)
        ):
            raise AppError("PARAM_INVALID", 422, {"detail": "component_ids invalid"})
        chart_type = operation.get("chart_type")
        if op_type == "set_chart_type" and chart_type is None:
            chart_type = operation.get("value")
        if chart_type is not None and chart_type not in CHART_COMPONENT_TYPES:
            raise AppError("PARAM_INVALID", 422, {"detail": "chart_type not allowed"})
        _validate_table_columns(operation.get("columns"), skill=skill, surface=surface)
        children = _validate_components_list(operation.get("children"), label="operation children")
        _validate_component_budget(children, label="operation")
        for child in children or []:
            _validate_component(child, skill=skill, surface=surface)
        _reject_hidden_required_components(children, required_component_ids)
        if op_type == "set_personal_default":
            defaults = operation.get("defaults")
            if defaults is None and isinstance(operation.get("path") or operation.get("field"), str):
                defaults = {str(operation.get("path") or operation.get("field")): operation.get("value")}
            _validate_personal_defaults(defaults, skill=skill, surface=surface)
        for path in _binding_paths(operation.get("binding")):
            _validate_binding_path(path, skill=skill, surface=surface)
        for path in _binding_paths(operation.get("bindings")):
            _validate_binding_path(path, skill=skill, surface=surface)
    return copy.deepcopy(overlay)


def _find_component_type(component_id: str, *schemas: dict) -> str | None:
    for schema in schemas:
        for component in _iter_components(schema.get("components")):
            if component.get("id") == component_id and isinstance(component.get("type"), str):
                return component.get("type")
    return None


def _patch_overlay_component(overlay: dict, default_schema: dict, component_id: str, patch: dict) -> None:
    component_type = _find_component_type(component_id, overlay, default_schema)
    if not component_type and "type" not in patch:
        raise AppError("PARAM_INVALID", 422, {"detail": f"unknown component: {component_id}"})
    components = overlay.setdefault("components", [])
    for component in components:
        if isinstance(component, dict) and component.get("id") == component_id:
            component.update(patch)
            component.setdefault("type", component_type)
            return
    components.append({"id": component_id, "type": component_type or patch.get("type"), **patch})


def _apply_overlay_operations(default_schema: dict, overlay: dict) -> dict:
    normalized = copy.deepcopy(overlay)
    operations = normalized.pop("operations", []) or []
    for operation in operations:
        op_type = operation.get("op") or operation.get("type")
        component_id = operation.get("component_id")
        if op_type == "reorder_component":
            normalized["component_order"] = list(operation.get("component_ids") or operation.get("order") or [])
            continue
        if op_type == "hide_component":
            _validate_component_id(component_id)
            hidden = set(normalized.get("hidden_component_ids") or [])
            hidden.add(component_id)
            normalized["hidden_component_ids"] = sorted(hidden)
            continue
        if op_type == "show_component":
            _validate_component_id(component_id)
            hidden = set(normalized.get("hidden_component_ids") or [])
            hidden.discard(component_id)
            normalized["hidden_component_ids"] = sorted(hidden)
            _patch_overlay_component(normalized, default_schema, component_id, {"visible": True})
            continue
        if op_type == "rename_title":
            _validate_component_id(component_id)
            _patch_overlay_component(normalized, default_schema, component_id, {"title": str(operation.get("title") or "")})
            continue
        if op_type == "set_default_view":
            normalized["default_view"] = str(operation.get("view") or operation.get("value") or "")
            continue
        if op_type == "set_chart_type":
            _validate_component_id(component_id)
            chart_type = str(operation.get("chart_type") or operation.get("value") or "")
            _patch_overlay_component(normalized, default_schema, component_id, {"type": chart_type, "chart_type": chart_type})
            continue
        if op_type == "set_chart_binding":
            _validate_component_id(component_id)
            _patch_overlay_component(normalized, default_schema, component_id, {"binding": operation.get("binding")})
            continue
        if op_type == "set_table_columns":
            _validate_component_id(component_id)
            _patch_overlay_component(normalized, default_schema, component_id, {"columns": copy.deepcopy(operation.get("columns") or [])})
            continue
        if op_type == "set_form_group":
            group_id = operation.get("group_id") or component_id or f"group-{uuid4().hex[:8]}"
            _validate_component_id(str(group_id), field="group_id")
            children = copy.deepcopy(operation.get("children") or [])
            normalized.setdefault("components", []).append({
                "id": str(group_id),
                "type": "form_group",
                "title": str(operation.get("group_title") or operation.get("title") or "参数组"),
                "children": children,
            })
            continue
        if op_type == "set_personal_default":
            defaults = normalized.setdefault("personal_defaults", {})
            if isinstance(operation.get("defaults"), dict):
                defaults.update(copy.deepcopy(operation["defaults"]))
            else:
                path = operation.get("path") or operation.get("field")
                if isinstance(path, str) and path:
                    defaults[path] = operation.get("value")
    return normalized


def _apply_hidden_component_ids(components, hidden: set[str]) -> None:
    for component in components:
        if not isinstance(component, dict):
            continue
        if component.get("id") in hidden:
            component["visible"] = False
        children = component.get("children")
        if isinstance(children, list):
            _apply_hidden_component_ids(children, hidden)


def merge_ui_schema(default_schema: dict, overlay: dict | None) -> dict:
    merged = copy.deepcopy(default_schema)
    if not overlay:
        _resolve_merged_personal_defaults(merged)
        return merged
    overlay = _apply_overlay_operations(default_schema, overlay)

    if isinstance(overlay.get("layout"), dict):
        merged["layout"] = copy.deepcopy(overlay["layout"])
    if isinstance(overlay.get("default_view"), str):
        merged["default_view"] = overlay["default_view"]
    if isinstance(overlay.get("personal_defaults"), dict):
        defaults = copy.deepcopy(merged.get("personal_defaults") or {})
        defaults.update(copy.deepcopy(overlay["personal_defaults"]))
        merged["personal_defaults"] = defaults

    components = copy.deepcopy(merged.get("components") or [])
    by_id = {item.get("id"): item for item in components if isinstance(item, dict)}
    for component in overlay.get("components") or []:
        component_id = component.get("id")
        if component_id in by_id:
            by_id[component_id].update(copy.deepcopy(component))
        else:
            components.append(copy.deepcopy(component))
            by_id[component_id] = components[-1]

    hidden = set(overlay.get("hidden_component_ids") or [])
    _apply_hidden_component_ids(components, hidden)

    order = overlay.get("component_order") or []
    if order:
        order_index = {component_id: index for index, component_id in enumerate(order)}
        components.sort(key=lambda item: (order_index.get(item.get("id"), len(order_index)), item.get("order") or 0))

    merged["components"] = components
    _resolve_merged_personal_defaults(merged)
    return merged


def _resolve_merged_personal_defaults(merged: dict) -> None:
    defaults = merged.get("personal_defaults")
    if not isinstance(defaults, dict):
        return
    field_formats = {
        str(component.get("field")): component.get("format")
        for component in _iter_components(merged.get("components"))
        if component.get("type") == "field" and component.get("field")
    }
    for path, value in list(defaults.items()):
        if field_formats.get(str(path)) == "date" and value == "yesterday":
            defaults[path] = (now_bjt().date() - timedelta(days=1)).isoformat()


def _merge_overlay_patch(base_overlay: dict, patch_overlay: dict) -> dict:
    """合并 AI 生成的增量 overlay，避免预览时丢失用户已有个人设置。"""
    merged = copy.deepcopy(base_overlay or {})
    patch = copy.deepcopy(patch_overlay or {})
    patch_components = patch.get("components") if isinstance(patch.get("components"), list) else []
    patch_operations = patch.get("operations") if isinstance(patch.get("operations"), list) else []

    patch_visible_true_ids = {
        component.get("id")
        for component in _iter_components(patch_components)
        if component.get("visible") is True and isinstance(component.get("id"), str)
    }
    patch_visible_false_ids = {
        component.get("id")
        for component in _iter_components(patch_components)
        if component.get("visible") is False and isinstance(component.get("id"), str)
    }
    patch_show_ids = {
        operation.get("component_id")
        for operation in patch_operations
        if (operation.get("op") or operation.get("type")) == "show_component"
        and isinstance(operation.get("component_id"), str)
    }
    patch_hide_ids = {
        operation.get("component_id")
        for operation in patch_operations
        if (operation.get("op") or operation.get("type")) == "hide_component"
        and isinstance(operation.get("component_id"), str)
    }
    patch_hidden_ids = set(patch.get("hidden_component_ids") or [])

    explicit_show_ids = patch_visible_true_ids | patch_show_ids
    explicit_hide_ids = patch_visible_false_ids | patch_hide_ids | patch_hidden_ids

    if explicit_show_ids:
        merged["hidden_component_ids"] = [
            component_id
            for component_id in (merged.get("hidden_component_ids") or [])
            if component_id not in explicit_show_ids
        ]
    if explicit_show_ids or explicit_hide_ids:
        kept_operations = []
        for operation in merged.get("operations") or []:
            op_type = operation.get("op") or operation.get("type")
            component_id = operation.get("component_id")
            if op_type == "hide_component" and component_id in explicit_show_ids:
                continue
            if op_type == "show_component" and component_id in explicit_hide_ids:
                continue
            kept_operations.append(operation)
        merged["operations"] = kept_operations

    for key in ("schema_version", "surface", "layout", "default_view", "notes"):
        if key in patch:
            merged[key] = patch[key]

    if isinstance(patch.get("personal_defaults"), dict):
        defaults = copy.deepcopy(merged.get("personal_defaults") or {})
        defaults.update(patch["personal_defaults"])
        merged["personal_defaults"] = defaults

    if isinstance(patch.get("hidden_component_ids"), list):
        hidden = list(merged.get("hidden_component_ids") or [])
        hidden_set = set(hidden)
        for component_id in patch["hidden_component_ids"]:
            if component_id not in hidden_set:
                hidden.append(component_id)
                hidden_set.add(component_id)
        merged["hidden_component_ids"] = hidden

    if isinstance(patch.get("component_order"), list):
        merged["component_order"] = patch["component_order"]

    if patch_components:
        components = copy.deepcopy(merged.get("components") or [])
        by_id = {
            component.get("id"): component
            for component in components
            if isinstance(component, dict)
        }
        for component in patch_components:
            if not isinstance(component, dict):
                continue
            component_id = component.get("id")
            if component_id in by_id:
                by_id[component_id].update(component)
            else:
                components.append(component)
                by_id[component_id] = component
        merged["components"] = components

    if patch_operations:
        operations = copy.deepcopy(merged.get("operations") or [])
        operations.extend(patch_operations)
        merged["operations"] = operations

    return merged


async def get_active_preference(
    db: AsyncSession,
    *,
    user_id: str,
    skill_id: str,
    surface: str,
) -> UserSkillUIPreference | None:
    return await db.scalar(
        select(UserSkillUIPreference)
        .where(UserSkillUIPreference.user_id == user_id)
        .where(UserSkillUIPreference.skill_id == skill_id)
        .where(UserSkillUIPreference.surface == surface)
        .where(UserSkillUIPreference.enabled == True)  # noqa: E712
        .order_by(UserSkillUIPreference.version.desc())
        .limit(1)
    )


async def _resolve_requested_preference(
    db: AsyncSession,
    *,
    user: User,
    skill: Skill,
    surface: str,
    ui_pref_id: str | None,
    ui_pref_version: int | None,
) -> UserSkillUIPreference | None:
    if ui_pref_id:
        pref = await db.get(UserSkillUIPreference, ui_pref_id)
        if not pref:
            raise AppError("PARAM_INVALID", 422, {"detail": "ui preference not found"})
        if pref.user_id != user.id or pref.skill_id != skill.id or pref.surface != surface:
            raise AppError("AUTH_PERMISSION_DENIED", 403)
        if pref.enabled is False:
            raise AppError("PARAM_INVALID", 422, {"detail": "ui preference is no longer active"})
        if ui_pref_version is not None and pref.version != ui_pref_version:
            raise AppError("PARAM_INVALID", 422, {"detail": "ui preference version mismatch"})
        return pref
    if ui_pref_version is not None:
        pref = await db.scalar(
            select(UserSkillUIPreference)
            .where(UserSkillUIPreference.user_id == user.id)
            .where(UserSkillUIPreference.skill_id == skill.id)
            .where(UserSkillUIPreference.surface == surface)
            .where(UserSkillUIPreference.version == ui_pref_version)
            .limit(1)
        )
        if not pref:
            raise AppError("PARAM_INVALID", 422, {"detail": "ui preference not found"})
        if pref.enabled is False:
            raise AppError("PARAM_INVALID", 422, {"detail": "ui preference is no longer active"})
        return pref
    return await get_active_preference(db, user_id=user.id, skill_id=skill.id, surface=surface)


def _public_saved_prompt(preference: UserSkillUIPreference | None) -> str | None:
    if not preference or not preference.prompt_summary:
        return None
    return _redact_user_prompt_text(str(preference.prompt_summary), limit=MAX_UI_PROMPT_SUMMARY_CHARS)


async def build_ui_response(
    db: AsyncSession,
    *,
    skill: Skill,
    user: User,
    surface: str,
    preference: UserSkillUIPreference | None,
) -> dict:
    default_schema = default_ui_schema_for_skill(skill, surface)
    overlay = preference.overlay_json if preference else {}
    invalidated_pref: dict | None = None
    try:
        validated = validate_overlay_schema(overlay, skill=skill, surface=surface)
        effective_preference = preference
    except AppError as exc:
        if preference is None:
            raise
        invalidated_pref = await _disable_invalid_preference(db, preference, exc)
        validated = {}
        effective_preference = None
    merged = merge_ui_schema(default_schema, validated)
    _validate_merged_schema_budget(merged)
    merged_hash = stable_schema_hash(merged)
    permissions = await get_skill_permissions(db, skill, user)
    return {
        "schema_version": SCHEMA_VERSION,
        "skill_id": skill.id,
        "surface": surface,
        "base_skill_commit": skill.git_commit,
        "ui_pref_id": effective_preference.id if effective_preference else None,
        "ui_pref_version": effective_preference.version if effective_preference else None,
        "generated_by": effective_preference.generated_by if effective_preference else None,
        "saved_prompt": _public_saved_prompt(effective_preference),
        "ui_pref_invalidated": invalidated_pref,
        "overlay": validated,
        "merged_schema": merged,
        "merged_ui_schema_hash": merged_hash,
        "permissions": {
            "read": bool(permissions.get("read")),
            "execute": bool(permissions.get("execute")),
            "customize_ui": bool(permissions.get(_customize_action_for_surface(surface))),
        },
    }


async def _disable_invalid_preference(
    db: AsyncSession,
    preference: UserSkillUIPreference,
    exc: AppError,
) -> dict:
    preference.enabled = False
    preference.updated_at = now_bjt()
    await db.flush()
    return {
        "ui_pref_id": preference.id,
        "ui_pref_version": preference.version,
        "reason": _public_error_reason(exc.detail or exc.code),
    }


async def get_skill_ui(db: AsyncSession, skill_id: str, user: User, surface: str | None = None) -> dict:
    resolved = normalize_surface(surface)
    skill = await require_skill_access(db, skill_id, user, _customize_action_for_surface(resolved))
    preference = await get_active_preference(db, user_id=user.id, skill_id=skill.id, surface=resolved)
    return await build_ui_response(db, skill=skill, user=user, surface=resolved, preference=preference)


def _preference_history_item(preference: UserSkillUIPreference) -> dict:
    overlay = preference.overlay_json if isinstance(preference.overlay_json, dict) else {}
    components = list(_iter_components(overlay.get("components")))
    return {
        "id": preference.id,
        "skill_id": preference.skill_id,
        "surface": preference.surface,
        "version": preference.version,
        "enabled": bool(preference.enabled),
        "generated_by": preference.generated_by,
        "base_skill_commit": preference.base_skill_commit,
        "prompt_summary": _public_saved_prompt(preference),
        "component_count": len(components),
        "hidden_component_count": len(overlay.get("hidden_component_ids") or []),
        "personal_default_count": len(overlay.get("personal_defaults") or {}),
        "created_at": preference.created_at,
        "updated_at": preference.updated_at,
    }


async def list_ui_preferences(
    db: AsyncSession,
    *,
    skill_id: str,
    user: User,
    surface: str | None = None,
    limit: int = 20,
) -> dict:
    resolved = normalize_surface(surface)
    skill = await require_skill_access(db, skill_id, user, _customize_action_for_surface(resolved))
    page_size = min(max(int(limit or 20), 1), 50)
    rows = (
        await db.execute(
            select(UserSkillUIPreference)
            .where(UserSkillUIPreference.user_id == user.id)
            .where(UserSkillUIPreference.skill_id == skill.id)
            .where(UserSkillUIPreference.surface == resolved)
            .order_by(UserSkillUIPreference.version.desc(), UserSkillUIPreference.created_at.desc())
            .limit(page_size)
        )
    ).scalars().all()
    total = await db.scalar(
        select(func.count())
        .select_from(UserSkillUIPreference)
        .where(UserSkillUIPreference.user_id == user.id)
        .where(UserSkillUIPreference.skill_id == skill.id)
        .where(UserSkillUIPreference.surface == resolved)
    )
    return {
        "items": [_preference_history_item(row) for row in rows],
        "total": int(total or 0),
        "surface": resolved,
        "limit": page_size,
    }


def _instruction_overlay(skill: Skill, surface: str, instruction: str) -> dict:
    text = (instruction or "").strip().lower()
    overlay: dict = {
        "schema_version": SCHEMA_VERSION,
        "surface": surface,
        "notes": "ai_generated_declarative_overlay",
    }
    if surface == "run_form":
        schema = default_ui_schema_for_skill(skill, surface)
        fields = [
            item for item in schema.get("components", [])
            if item.get("type") == "field" and not _component_has_secret_param_binding(item)
        ]
        if fields and ("紧凑" in text or "分组" in text or "compact" in text or "group" in text):
            overlay["components"] = [{
                "id": "ai-form-group-main",
                "type": "form_group",
                "title": "常用参数",
                "children": [{"id": item["id"], "type": "field", "binding": item["binding"]} for item in fields],
            }]
        if "隐藏" in text or "hide" in text:
            optional = [item["id"] for item in fields if not item.get("required")]
            overlay["hidden_component_ids"] = optional[:3]
    else:
        chart_type = "line_chart"
        if "柱" in text or "bar" in text:
            chart_type = "bar_chart"
        if "饼" in text or "pie" in text:
            chart_type = "pie_chart"
        if any(word in text for word in ("图", "chart", "趋势", "折线", "柱", "饼")):
            overlay["components"] = [{
                "id": "ai-result-chart",
                "type": chart_type,
                "title": "结果图表",
                "binding": "result.data",
            }]
            overlay["default_view"] = "chart"
    return overlay


def _safe_skill_ui_context(skill: Skill, surface: str, current_overlay: dict) -> dict:
    default_schema = default_ui_schema_for_skill(skill, surface)
    display_name = getattr(skill, "display_name", None) or getattr(skill, "name", None)
    if not isinstance(display_name, str):
        display_name = None
    context = {
        "skill": {
            "id": skill.id,
            "display_name": display_name,
            "base_skill_commit": skill.git_commit,
        },
        "surface": surface,
        "allowed_component_types": sorted(ALLOWED_COMPONENT_TYPES),
        "allowed_operation_types": sorted(ALLOWED_OPERATION_TYPES),
        "allowed_binding_prefixes": list(ALLOWED_BINDING_PREFIXES),
        "default_component_bindings": [
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "binding": item.get("binding"),
                "title": item.get("title"),
            }
            for item in default_schema.get("components", [])
            if isinstance(item, dict) and not _component_has_secret_param_binding(item)
        ],
        "current_overlay": current_overlay,
    }
    return _redact_llm_context_value(context)


def _redact_llm_context_value(value):
    from app.common.ai import redact_secret_text

    if isinstance(value, dict):
        return {key: _redact_llm_context_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_llm_context_value(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value, limit=1000)
    return value


def _public_error_reason(detail) -> str:
    from app.common.ai import redact_secret_text

    text = redact_secret_text(str(detail or "ui_error"), limit=240)
    if _is_secret_like_param_path(text) or _contains_secret_text(text):
        return "secret_like_ui_rejected"
    return text


def _component_has_secret_param_binding(component: dict) -> bool:
    field = component.get("field")
    if isinstance(field, str) and _is_secret_like_param_path(field):
        return True
    for path in _binding_paths(component.get("binding")):
        if path.startswith("params.") and _is_secret_like_param_path(path[len("params."):]):
            return True
    for path in _binding_paths(component.get("bindings")):
        if path.startswith("params.") and _is_secret_like_param_path(path[len("params."):]):
            return True
    return False


async def _llm_instruction_overlay(
    *,
    skill: Skill,
    user: User,
    surface: str,
    instruction: str,
    current_overlay: dict,
) -> tuple[dict | None, str, str | None]:
    try:
        from app.common.ai import call_llm, redact_secret_text

        system = (
            "You generate SkillForge personal UI overlays. Return JSON only. "
            "The output must be an overlay object or {\"overlay\": {...}}. "
            "Use only the provided declarative component and operation whitelist. "
            "Never output HTML, JavaScript, CSS, event handlers, iframe, remote widgets, "
            "new data sources, MCP scopes, triggers, permissions, workflow changes, publish or deploy actions."
        )
        user_payload = {
            "instruction": instruction,
            "context": _safe_skill_ui_context(skill, surface, current_overlay),
        }
        result = await call_llm(
            system,
            json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
            max_tokens=1200,
            temperature=0.1,
            timeout=8,
            json_mode=True,
            call_source="portal_ui_ai",
            cost_context={"user_id": user.id, "skill_id": skill.id},
            require_system_config=True,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            from app.common.ai import redact_secret_text
            return None, "fallback", redact_secret_text(exc)
        except Exception:  # noqa: BLE001
            return None, "fallback", "llm_error"

    if not isinstance(result, dict):
        return None, "fallback", "llm_non_object_response"
    candidate = result.get("overlay") if isinstance(result.get("overlay"), dict) else result
    if not isinstance(candidate, dict):
        return None, "fallback", "llm_missing_overlay"
    try:
        candidate.setdefault("schema_version", SCHEMA_VERSION)
        candidate.setdefault("surface", surface)
        return validate_overlay_schema(candidate, skill=skill, surface=surface), "llm", None
    except AppError as exc:
        return None, "fallback", f"llm_overlay_rejected:{_public_error_reason(exc.detail or exc.code)}"


async def preview_ai_ui(
    db: AsyncSession,
    *,
    skill_id: str,
    user: User,
    surface: str | None,
    instruction: str,
    current_overlay: dict | None = None,
) -> dict:
    resolved = normalize_surface(surface)
    normalized_instruction = _normalize_ai_instruction(instruction)
    skill = await require_skill_access(db, skill_id, user, _customize_action_for_surface(resolved))
    base_overlay = validate_overlay_schema(current_overlay or {}, skill=skill, surface=resolved)
    proposed_overlay, ai_status, ai_reason = await _llm_instruction_overlay(
        skill=skill,
        user=user,
        surface=resolved,
        instruction=normalized_instruction,
        current_overlay=base_overlay,
    )
    if proposed_overlay is None:
        proposed_overlay = _instruction_overlay(skill, resolved, normalized_instruction)
    merged_overlay = _merge_overlay_patch(base_overlay, proposed_overlay)
    validated = validate_overlay_schema(merged_overlay, skill=skill, surface=resolved)
    response = await build_ui_response(db, skill=skill, user=user, surface=resolved, preference=None)
    default_schema = default_ui_schema_for_skill(skill, resolved)
    merged_schema = merge_ui_schema(default_schema, validated)
    _validate_merged_schema_budget(merged_schema)
    return {
        **response,
        "overlay": validated,
        "merged_schema": merged_schema,
        "merged_ui_schema_hash": stable_schema_hash(merged_schema),
        "ai_status": ai_status,
        "ai_reason": ai_reason,
        "ai_context": {
            "skill_id": skill.id,
            "surface": resolved,
            "base_skill_commit": skill.git_commit,
            "allowed_component_types": sorted(ALLOWED_COMPONENT_TYPES),
            "allowed_binding_prefixes": list(ALLOWED_BINDING_PREFIXES),
        },
    }


async def save_ui_preference(
    db: AsyncSession,
    *,
    skill_id: str,
    user: User,
    surface: str | None,
    overlay: dict | None,
    generated_by: str = "manual",
    prompt_summary: str | None = None,
) -> dict:
    resolved = normalize_surface(surface)
    skill = await require_skill_access(db, skill_id, user, _customize_action_for_surface(resolved))
    validated = validate_overlay_schema(overlay or {}, skill=skill, surface=resolved)
    saved_prompt = _normalize_prompt_summary(prompt_summary)

    active_rows = (
        await db.execute(
            select(UserSkillUIPreference)
            .where(UserSkillUIPreference.user_id == user.id)
            .where(UserSkillUIPreference.skill_id == skill.id)
            .where(UserSkillUIPreference.surface == resolved)
            .where(UserSkillUIPreference.enabled == True)  # noqa: E712
        )
    ).scalars().all()
    for row in active_rows:
        row.enabled = False
        row.updated_at = now_bjt()
    if active_rows:
        await db.flush()

    max_version = await db.scalar(
        select(func.max(UserSkillUIPreference.version))
        .where(UserSkillUIPreference.user_id == user.id)
        .where(UserSkillUIPreference.skill_id == skill.id)
        .where(UserSkillUIPreference.surface == resolved)
    )
    preference = UserSkillUIPreference(
        id=f"uipref-{uuid4().hex[:16]}",
        user_id=user.id,
        skill_id=skill.id,
        surface=resolved,
        base_skill_commit=skill.git_commit,
        overlay_schema_version=SCHEMA_VERSION,
        overlay_json=validated,
        generated_by=generated_by if generated_by in {"manual", "ai"} else "manual",
        prompt_summary=saved_prompt,
        enabled=True,
        version=int(max_version or 0) + 1,
        created_at=now_bjt(),
        updated_at=now_bjt(),
    )
    db.add(preference)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise AppError(
            "UI_PREF_CONFLICT",
            409,
            {"detail": "个人界面已被其它请求更新，请刷新后重试"},
        ) from exc
    return await build_ui_response(db, skill=skill, user=user, surface=resolved, preference=preference)


async def restore_ui_preference(
    db: AsyncSession,
    *,
    skill_id: str,
    user: User,
    preference_id: str,
    surface: str | None,
) -> dict:
    resolved = normalize_surface(surface)
    skill = await require_skill_access(db, skill_id, user, _customize_action_for_surface(resolved))
    source = await db.get(UserSkillUIPreference, preference_id)
    if not source or source.user_id != user.id or source.skill_id != skill.id or source.surface != resolved:
        raise AppError("PARAM_INVALID", 422, {"detail": "ui preference version not found"})
    try:
        validated = validate_overlay_schema(source.overlay_json or {}, skill=skill, surface=resolved)
    except AppError as exc:
        raise AppError(
            "PARAM_INVALID",
            422,
            {"detail": f"历史界面不再适用于当前 Skill：{_public_error_reason(exc.detail or exc.code)}"},
        ) from exc

    active_rows = (
        await db.execute(
            select(UserSkillUIPreference)
            .where(UserSkillUIPreference.user_id == user.id)
            .where(UserSkillUIPreference.skill_id == skill.id)
            .where(UserSkillUIPreference.surface == resolved)
            .where(UserSkillUIPreference.enabled == True)  # noqa: E712
        )
    ).scalars().all()
    for row in active_rows:
        row.enabled = False
        row.updated_at = now_bjt()
    if active_rows:
        await db.flush()

    max_version = await db.scalar(
        select(func.max(UserSkillUIPreference.version))
        .where(UserSkillUIPreference.user_id == user.id)
        .where(UserSkillUIPreference.skill_id == skill.id)
        .where(UserSkillUIPreference.surface == resolved)
    )
    preference = UserSkillUIPreference(
        id=f"uipref-{uuid4().hex[:16]}",
        user_id=user.id,
        skill_id=skill.id,
        surface=resolved,
        base_skill_commit=skill.git_commit,
        overlay_schema_version=SCHEMA_VERSION,
        overlay_json=validated,
        generated_by=source.generated_by if source.generated_by in {"manual", "ai"} else "manual",
        prompt_summary=_normalize_prompt_summary(source.prompt_summary),
        enabled=True,
        version=int(max_version or 0) + 1,
        created_at=now_bjt(),
        updated_at=now_bjt(),
    )
    db.add(preference)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise AppError(
            "UI_PREF_CONFLICT",
            409,
            {"detail": "个人界面已被其它请求更新，请刷新后重试"},
        ) from exc
    return await build_ui_response(db, skill=skill, user=user, surface=resolved, preference=preference)


async def delete_ui_preference(
    db: AsyncSession,
    *,
    skill_id: str,
    user: User,
    surface: str | None,
) -> dict:
    resolved = normalize_surface(surface)
    skill = await require_skill_access(db, skill_id, user, _customize_action_for_surface(resolved))
    active_rows = (
        await db.execute(
            select(UserSkillUIPreference)
            .where(UserSkillUIPreference.user_id == user.id)
            .where(UserSkillUIPreference.skill_id == skill.id)
            .where(UserSkillUIPreference.surface == resolved)
            .where(UserSkillUIPreference.enabled == True)  # noqa: E712
        )
    ).scalars().all()
    for row in active_rows:
        row.enabled = False
        row.updated_at = now_bjt()
    await db.flush()
    return await build_ui_response(db, skill=skill, user=user, surface=resolved, preference=None)


async def get_submission_ui_context(
    db: AsyncSession,
    *,
    skill: Skill,
    user: User,
    surface: str | None,
    ui_pref_id: str | None = None,
    ui_pref_version: int | None = None,
    merged_ui_schema_hash: str | None = None,
) -> dict:
    resolved = normalize_surface(surface)
    if resolved != "run_form":
        raise AppError("PARAM_INVALID", 422, {"detail": "submission ui surface must be run_form"})
    explicit_ui_context = bool(ui_pref_id or ui_pref_version is not None or merged_ui_schema_hash)
    requested_preference = await _resolve_requested_preference(
        db,
        user=user,
        skill=skill,
        surface=resolved,
        ui_pref_id=ui_pref_id,
        ui_pref_version=ui_pref_version,
    )
    default_schema = default_ui_schema_for_skill(skill, resolved)
    invalidated_pref = None
    if not explicit_ui_context and requested_preference is not None:
        try:
            validate_overlay_schema(requested_preference.overlay_json, skill=skill, surface=resolved)
        except AppError as exc:
            invalidated_pref = await _disable_invalid_preference(db, requested_preference, exc)
    preference = requested_preference if explicit_ui_context else None
    try:
        overlay = validate_overlay_schema(preference.overlay_json if preference else {}, skill=skill, surface=resolved)
    except AppError as exc:
        if preference is None or explicit_ui_context:
            raise
        invalidated_pref = await _disable_invalid_preference(db, preference, exc)
        preference = None
        overlay = {}
    merged = merge_ui_schema(default_schema, overlay)
    _validate_merged_schema_budget(merged)
    schema_hash = stable_schema_hash(merged)
    normalized_overlay = _apply_overlay_operations(default_schema, overlay) if overlay else {}
    param_defaults_source = {
        str(path): "skill_schema"
        for path in (default_schema.get("personal_defaults") or {})
    }
    param_defaults_source.update({
        str(path): "personal_overlay"
        for path in (normalized_overlay.get("personal_defaults") or {})
    })
    if merged_ui_schema_hash and merged_ui_schema_hash != schema_hash:
        raise AppError("PARAM_INVALID", 422, {"detail": "ui schema hash mismatch"})
    ui_snapshot_json = {
        "schema_version": SCHEMA_VERSION,
        "surface": resolved,
        "ui_pref_id": preference.id if preference else None,
        "ui_pref_version": preference.version if preference else None,
        "base_skill_commit": skill.git_commit,
        "merged_ui_schema_hash": schema_hash,
        "component_ids": [item.get("id") for item in merged.get("components", []) if isinstance(item, dict)],
        "personal_defaults": merged.get("personal_defaults") or {},
        "param_defaults_source": param_defaults_source,
        "ui_pref_invalidated": invalidated_pref,
        "overlay": overlay,
        "merged_schema": merged,
    }
    validate_ui_snapshot_json(ui_snapshot_json)
    return {
        "ui_pref_id": preference.id if preference else None,
        "ui_pref_version": preference.version if preference else None,
        "ui_surface": resolved,
        "base_skill_commit": skill.git_commit,
        "merged_ui_schema_hash": schema_hash,
        "ui_pref_invalidated": invalidated_pref,
        "ui_snapshot_json": ui_snapshot_json,
    }
