"""Portal 个人 UI overlay 安全边界测试。"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.common.ai import redact_secret_text
from app.common.exceptions import AppError
from app.portal import ui_service
from app.portal.models import UserSkillUIPreference
from app.portal.ui_service import (
    MAX_UI_AI_INSTRUCTION_CHARS,
    MAX_UI_COMPONENT_DEPTH,
    MAX_UI_COMPONENTS,
    MAX_UI_OVERLAY_BYTES,
    MAX_UI_PROMPT_SUMMARY_CHARS,
    build_ui_response,
    default_ui_schema_for_skill,
    delete_ui_preference,
    get_submission_ui_context,
    get_skill_ui,
    list_ui_preferences,
    merge_ui_schema,
    preview_ai_ui,
    restore_ui_preference,
    save_ui_preference,
    stable_schema_hash,
    validate_overlay_schema,
)


def _mock_skill():
    skill = MagicMock()
    skill.id = "skill-ui"
    skill.git_commit = "abc123"
    skill.param_ui_schema = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "format": "date", "title": "日期"},
            "platform": {"type": "string", "enum": ["直通车", "万相台"]},
        },
        "required": ["date"],
    }
    skill.result_ui_schema = {"type": "table", "columns": [{"key": "cost", "title": "花费"}]}
    return skill


def _mock_user(user_id: str = "u1"):
    user = MagicMock()
    user.id = user_id
    user.role = "operator"
    user.can_view_all = False
    return user


def test_user_skill_ui_preference_has_unique_active_index():
    indexes = {
        index.name: index
        for index in UserSkillUIPreference.__table__.indexes
    }
    active_index = indexes["uq_user_skill_ui_pref_active_enabled"]

    assert active_index.unique is True
    assert [column.name for column in active_index.columns] == ["user_id", "skill_id", "surface"]
    assert str(active_index.dialect_options["postgresql"]["where"]) == "enabled = true"


def test_overlay_rejects_executable_schema():
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "components": [{
            "id": "bad",
            "type": "html",
            "binding": "params.date",
            "html": "<script>alert(1)</script>",
        }],
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"


@pytest.mark.parametrize("value", [
    "<img src=x onerror=alert(1)>",
    "<b>重点指标</b>",
    "background: url(javascript:alert(1))",
    "width: expression(alert(1))",
    "safe text onerror=alert(1)",
    "data:image/svg+xml,<svg onload=alert(1)>",
])
def test_overlay_rejects_executable_string_values(value):
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "result",
        "components": [{
            "id": "summary",
            "type": "markdown_summary",
            "binding": "result.data",
            "title": value,
        }],
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="result")

    assert exc_info.value.code == "PARAM_INVALID"


def test_overlay_rejects_secret_like_plain_titles():
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "result",
        "components": [{
            "id": "summary",
            "type": "markdown_summary",
            "binding": "result.data",
            "title": "API Key",
        }],
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="result")

    assert exc_info.value.code == "PARAM_INVALID"


def test_overlay_rejects_secret_like_object_keys_without_echo():
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "api_key": "normal text",
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="run_form")

    detail = str(exc_info.value.detail)
    assert "secret-like ui key" in detail
    assert "api_key" not in detail


def test_default_run_ui_prunes_secret_like_param_specs_and_defaults():
    skill = _mock_skill()
    skill.param_ui_schema["properties"].update({
        "auth": {"type": "string", "format": "password", "title": "授权"},
        "private_key": {"type": "string", "title": "Private Key"},
        "plain": {
            "type": "string",
            "title": "API Key",
            "default": "sk-default-secret-123",
        },
        "normal": {
            "type": "string",
            "title": "正常字段",
            "description": "Authorization: Bearer raw-description-token",
            "default": "sk-normal-default-secret",
        },
        "channel": {
            "type": "string",
            "title": "渠道",
            "format": "api_key=sk-format-secret",
            "enum": ["直通车", "api_key=sk-option-secret"],
        },
    })

    schema = default_ui_schema_for_skill(skill, "run_form")
    schema_text = json.dumps(schema, ensure_ascii=False)

    assert "field-auth" not in schema_text
    assert "field-private-key" not in schema_text
    assert "field-plain" not in schema_text
    assert "sk-default-secret-123" not in schema_text
    assert "raw-description-token" not in schema_text
    assert schema["personal_defaults"].get("normal") is None
    by_id = {item["id"]: item for item in schema["components"]}
    assert by_id["field-normal"]["description"] == "[REDACTED]"
    assert by_id["field-channel"].get("format") is None
    assert by_id["field-channel"]["options"] == ["直通车"]
    assert "sk-format-secret" not in schema_text
    assert "sk-option-secret" not in schema_text


def test_default_result_ui_prunes_secret_like_columns():
    skill = _mock_skill()
    skill.result_ui_schema = {
        "type": "table",
        "title": "结果 Authorization: Bearer raw-result-title-token",
        "columns": [
            {"key": "date", "title": "日期"},
            {"key": "api_key", "title": "API Key"},
            {"key": "token_amount", "title": "Token"},
            {"key": "cost", "title": "花费", "binding": "result.cost"},
        ],
    }

    schema = default_ui_schema_for_skill(skill, "result")
    schema_text = json.dumps(schema, ensure_ascii=False)
    columns = schema["components"][0]["columns"]

    assert [column["key"] for column in columns] == ["date", "cost"]
    assert "api_key" not in schema_text
    assert "API Key" not in schema_text
    assert "token_amount" not in schema_text
    assert "raw-result-title-token" not in schema_text
    assert schema["components"][0]["title"] == "[REDACTED]"


@pytest.mark.parametrize("overlay", [
    {
        "schema_version": "skill-ui/v1",
        "surface": "result",
        "components": [{
            "id": "summary",
            "type": "markdown_summary",
            "binding": "result.data",
            "title": "api_key=sk-overlay-title-secret",
        }],
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {"date": "sk-overlay-default-secret"},
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [{
            "op": "set_personal_default",
            "path": "date",
            "value": "Authorization: Bearer raw-overlay-token",
        }],
    },
])
def test_overlay_rejects_secret_like_string_values(overlay):
    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface=overlay["surface"])

    assert exc_info.value.code == "PARAM_INVALID"
    assert "secret-like" in str(exc_info.value.detail)


def test_overlay_rejects_binding_outside_current_skill_contract():
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "components": [{
            "id": "other-skill-field",
            "type": "field",
            "binding": "params.other_skill_secret",
        }],
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"


@pytest.mark.parametrize("overlay", [
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "components": [{
            "id": "api-key-field",
            "type": "field",
            "binding": "params.api_key",
        }],
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "components": [{
            "id": "api-key-field",
            "type": "field",
            "field": "api_key",
        }],
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {"api_key": "sk-user-secret"},
    },
])
def test_overlay_rejects_secret_like_param_fields_even_if_declared(overlay):
    skill = _mock_skill()
    skill.param_ui_schema["properties"]["api_key"] = {"type": "string", "title": "API Key"}

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=skill, surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "secret-like" in str(exc_info.value.detail)


def test_default_run_form_schema_excludes_secret_like_param_fields():
    skill = _mock_skill()
    skill.param_ui_schema["properties"]["api_key"] = {
        "type": "string",
        "title": "API Key",
        "default": "sk-user-secret",
    }

    schema = default_ui_schema_for_skill(skill, "run_form")
    component_bindings = {
        component.get("binding")
        for component in schema.get("components", [])
    }

    assert "params.api_key" not in component_bindings
    assert "api_key" not in schema.get("personal_defaults", {})


def test_overlay_rejects_unknown_nested_param_binding():
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "components": [{
            "id": "ghost-nested-field",
            "type": "field",
            "binding": "params.date.extra",
        }],
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"


def test_overlay_rejects_component_field_outside_current_skill_contract():
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "components": [{
            "id": "ghost-field",
            "type": "field",
            "field": "other_skill_secret",
        }],
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"


def test_overlay_rejects_component_table_columns_with_forbidden_binding():
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "result",
        "components": [{
            "id": "result-table",
            "type": "table",
            "binding": "result.data",
            "columns": [{
                "key": "cost",
                "title": "花费",
                "binding": "secrets.api_key",
            }],
        }],
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="result")

    assert exc_info.value.code == "PARAM_INVALID"


def test_overlay_rejects_unsafe_component_id_lists():
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "component_order": ["field-date", "x" * 160],
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"


@pytest.mark.parametrize("overlay", [
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "hidden_component_ids": "",
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [{"op": "reorder_component", "component_ids": ""}],
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "components": [{
            "id": "bad-group",
            "type": "form_group",
            "children": "",
        }],
    },
])
def test_overlay_rejects_non_list_component_collections(overlay):
    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"


def test_overlay_rejects_oversized_json_payload():
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "result",
        "notes": "x" * MAX_UI_OVERLAY_BYTES,
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="result")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "too large" in str(exc_info.value.detail)


def test_overlay_rejects_too_many_components():
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "result",
        "components": [
            {"id": f"metric-{index}", "type": "metric", "binding": "result.data"}
            for index in range(MAX_UI_COMPONENTS + 1)
        ],
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="result")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "too many components" in str(exc_info.value.detail)


def test_overlay_rejects_deep_component_tree():
    def nested_component(level: int) -> dict:
        if level <= 1:
            return {"id": "leaf-metric", "type": "metric", "binding": "result.data"}
        return {
            "id": f"group-{level}",
            "type": "form_group",
            "children": [nested_component(level - 1)],
        }

    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "result",
        "components": [nested_component(MAX_UI_COMPONENT_DEPTH + 1)],
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="result")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "too deep" in str(exc_info.value.detail)


@pytest.mark.parametrize("overlay", [
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {"date.extra": "2026-05-21"},
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [{"op": "set_personal_default", "path": "date.extra", "value": "2026-05-21"}],
    },
])
def test_overlay_rejects_unknown_nested_personal_default(overlay):
    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"


@pytest.mark.parametrize("overlay", [
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {"platform": "超级平台"},
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [{"op": "set_personal_default", "path": "platform", "value": "超级平台"}],
    },
])
def test_overlay_rejects_personal_default_outside_enum(overlay):
    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "enum" in str(exc_info.value.detail)


def test_overlay_rejects_personal_default_wrong_type():
    skill = _mock_skill()
    skill.param_ui_schema["properties"]["budget"] = {"type": "number", "title": "预算"}
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {"budget": "100"},
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=skill, surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "number" in str(exc_info.value.detail)


def test_overlay_rejects_object_personal_default_unknown_nested_field():
    skill = _mock_skill()
    skill.param_ui_schema["properties"]["filters"] = {
        "type": "object",
        "required": ["platform"],
        "properties": {
            "platform": {"type": "string"},
        },
    }
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {
            "filters": {
                "platform": "直通车",
                "unexpected": "x",
            },
        },
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=skill, surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "filters.unexpected" in str(exc_info.value.detail)


def test_overlay_rejects_array_personal_default_invalid_item_schema():
    skill = _mock_skill()
    skill.param_ui_schema["properties"]["items"] = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["sku"],
            "properties": {
                "sku": {"type": "string"},
                "qty": {"type": "integer"},
            },
        },
    }
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {
            "items": [{"sku": "A1", "qty": "1"}],
        },
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=skill, surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "items[0].qty" in str(exc_info.value.detail)


def test_overlay_rejects_nested_secret_like_personal_default_key():
    skill = _mock_skill()
    skill.param_ui_schema["properties"]["filters"] = {
        "type": "object",
        "properties": {
            "platform": {"type": "string"},
            "access_token": {"type": "string"},
        },
    }
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {
            "filters": {"access_token": "raw-token"},
        },
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=skill, surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "secret-like ui key" in str(exc_info.value.detail)
    assert "access_token" not in str(exc_info.value.detail)


def test_overlay_rejects_personal_default_when_object_schema_properties_malformed():
    skill = _mock_skill()
    skill.param_ui_schema["properties"]["filters"] = {
        "type": "object",
        "properties": [],
    }
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {
            "filters": {"platform": "直通车"},
        },
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=skill, surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "filters.platform" in str(exc_info.value.detail)


def test_overlay_rejects_personal_default_when_array_items_schema_malformed():
    skill = _mock_skill()
    skill.param_ui_schema["properties"]["items"] = {
        "type": "array",
        "items": [],
    }
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {
            "items": ["A1"],
        },
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=skill, surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "items[0]" in str(exc_info.value.detail)


@pytest.mark.parametrize("overlay", [
    {
        "schema_version": "skill-ui/v1",
        "surface": "result",
        "personal_defaults": {"platform": "直通车"},
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "result",
        "operations": [{"op": "set_personal_default", "path": "platform", "value": "直通车"}],
    },
])
def test_overlay_rejects_personal_defaults_on_result_surface(overlay):
    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="result")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "run_form" in str(exc_info.value.detail)


def test_overlay_rejects_required_validation_changes():
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "components": [{
            "id": "field-date",
            "type": "field",
            "binding": "params.date",
            "required": False,
        }],
    }

    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"


@pytest.mark.parametrize("overlay", [
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "hidden_component_ids": ["field-date"],
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "components": [{
            "id": "field-date",
            "type": "field",
            "binding": "params.date",
            "visible": False,
        }],
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [{"op": "hide_component", "component_id": "field-date"}],
    },
    {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [{
            "op": "set_form_group",
            "group_id": "required-group",
            "children": [{
                "id": "field-date",
                "type": "field",
                "binding": "params.date",
                "visible": False,
            }],
        }],
    },
])
def test_overlay_rejects_hiding_required_run_form_fields(overlay):
    with pytest.raises(AppError) as exc_info:
        validate_overlay_schema(overlay, skill=_mock_skill(), surface="run_form")

    assert exc_info.value.code == "PARAM_INVALID"
    assert "required" in str(exc_info.value.detail)


def test_redact_secret_text_removes_llm_credentials():
    text = (
        'auth failed api_key=sk-live-secret-123 '  # gitleaks:allow -- enum/config key or deliberate synthetic test fixture; not a credential
        'Authorization: Bearer raw-token-456 '
        'Authorization: Basic raw-basic-token '
        'Cookie: session_id=raw-cookie-secret; '
        '{"api_key":"sk-json-secret-789","token":"json-token-123"} '
        'client_secret=raw-client-secret '
        'private_key=raw-private-key '
        'ssh_key=raw-ssh-key '
        'refresh_token=raw-refresh-token '
        'https://gateway.example/callback?access_token=raw-access-token&safe=1 '
        'postgresql://skillforge:db-password-456@db.internal:5432/app '
        '-----BEGIN PRIVATE KEY-----\nraw-pem-secret\n-----END PRIVATE KEY-----'  # public-scan: synthetic-fixture; deliberate redaction/rejection test
    )

    redacted = redact_secret_text(text, limit=1000)

    assert "sk-live-secret-123" not in redacted
    assert "raw-token-456" not in redacted
    assert "raw-basic-token" not in redacted
    assert "raw-cookie-secret" not in redacted
    assert "sk-json-secret-789" not in redacted
    assert "json-token-123" not in redacted
    assert "raw-client-secret" not in redacted
    assert "raw-private-key" not in redacted
    assert "raw-ssh-key" not in redacted
    assert "raw-refresh-token" not in redacted
    assert "raw-access-token" not in redacted
    assert "db-password-456" not in redacted
    assert "raw-pem-secret" not in redacted
    assert "postgresql://skillforge:[REDACTED]@db.internal" in redacted
    assert "[REDACTED]" in redacted


def test_overlay_operations_are_applied_to_merged_schema():
    skill = _mock_skill()
    default_schema = default_ui_schema_for_skill(skill, "run_form")
    overlay = validate_overlay_schema({
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [
            {"op": "rename_title", "component_id": "field-date", "title": "投放日期"},
            {"op": "hide_component", "component_id": "field-platform"},
            {"op": "set_personal_default", "path": "platform", "value": "万相台"},
        ],
    }, skill=skill, surface="run_form")

    merged = merge_ui_schema(default_schema, overlay)
    by_id = {item["id"]: item for item in merged["components"]}

    assert by_id["field-date"]["title"] == "投放日期"
    assert by_id["field-platform"]["visible"] is False
    assert merged["personal_defaults"]["platform"] == "万相台"


def test_hidden_component_ids_apply_to_nested_group_children():
    skill = _mock_skill()
    default_schema = default_ui_schema_for_skill(skill, "run_form")
    overlay = validate_overlay_schema({
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [
            {
                "op": "set_form_group",
                "group_id": "group-platform",
                "children": [{
                    "id": "field-platform",
                    "type": "field",
                    "binding": "params.platform",
                }],
            },
            {"op": "hide_component", "component_id": "field-platform"},
        ],
    }, skill=skill, surface="run_form")

    merged = merge_ui_schema(default_schema, overlay)
    by_id = {item["id"]: item for item in merged["components"]}
    group = by_id["group-platform"]
    child = group["children"][0]

    assert child["visible"] is False
    assert by_id["field-platform"]["visible"] is False


@pytest.mark.asyncio
async def test_get_skill_ui_requires_execute_for_run_form():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    skill = _mock_skill()

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)) as mock_require, \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})):
        await get_skill_ui(db, "skill-ui", _mock_user(), "run_form")
        await get_skill_ui(db, "skill-ui", _mock_user(), "result")

    assert mock_require.await_args_list[0].args[3] == "execute"
    assert mock_require.await_args_list[1].args[3] == "read"


@pytest.mark.asyncio
async def test_result_surface_personal_ui_customization_uses_read_permission():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
    db.scalar = AsyncMock(return_value=0)
    db.add = MagicMock()
    db.flush = AsyncMock()
    skill = _mock_skill()
    mock_call_llm = AsyncMock(return_value={
        "overlay": {
            "schema_version": "skill-ui/v1",
            "surface": "result",
            "components": [{
                "id": "result-table",
                "type": "table",
                "binding": "result.data",
                "title": "我的结果",
            }],
        },
    })

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)) as mock_require, \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": False})), \
         patch("app.common.ai.call_llm", new=mock_call_llm):
        preview = await preview_ai_ui(
            db,
            skill_id="skill-ui",
            user=_mock_user(),
            surface="result",
            instruction="结果表标题改成我的结果",
        )
        saved = await save_ui_preference(
            db,
            skill_id="skill-ui",
            user=_mock_user(),
            surface="result",
            overlay=preview["overlay"],
            generated_by="ai",
            prompt_summary="结果表标题改成我的结果",
        )
        reset = await delete_ui_preference(
            db,
            skill_id="skill-ui",
            user=_mock_user(),
            surface="result",
        )

    assert [call.args[3] for call in mock_require.await_args_list] == ["read", "read", "read"]
    assert preview["permissions"]["customize_ui"] is True
    assert saved["permissions"]["customize_ui"] is True
    assert reset["permissions"]["customize_ui"] is True


@pytest.mark.asyncio
async def test_ai_preview_rejects_oversized_instruction():
    with pytest.raises(AppError) as exc_info:
        await preview_ai_ui(
            AsyncMock(),
            skill_id="skill-ui",
            user=_mock_user(),
            surface="result",
            instruction="x" * (MAX_UI_AI_INSTRUCTION_CHARS + 1),
        )

    assert exc_info.value.code == "PARAM_INVALID"


@pytest.mark.asyncio
async def test_save_ui_preference_rejects_oversized_saved_prompt():
    db = AsyncMock()
    skill = _mock_skill()

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await save_ui_preference(
                db,
                skill_id="skill-ui",
                user=_mock_user(),
                surface="result",
                overlay={"schema_version": "skill-ui/v1", "surface": "result"},
                generated_by="manual",
                prompt_summary="x" * (MAX_UI_PROMPT_SUMMARY_CHARS + 1),
            )

    assert exc_info.value.code == "PARAM_INVALID"


@pytest.mark.asyncio
async def test_save_ui_preference_redacts_secret_text_from_saved_prompt():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    ))
    db.scalar = AsyncMock(return_value=0)
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=_mock_skill())), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})):
        result = await save_ui_preference(
            db,
            skill_id="skill-ui",
            user=_mock_user("u1"),
            surface="run_form",
            overlay={"schema_version": "skill-ui/v1", "surface": "run_form"},
            generated_by="ai",
            prompt_summary="按平台分组 api_key=sk-saved-prompt-secret Authorization: Bearer raw-saved-token",
        )

    saved_pref = db.add.call_args.args[0]
    assert "sk-saved-prompt-secret" not in saved_pref.prompt_summary
    assert "raw-saved-token" not in saved_pref.prompt_summary
    assert "[REDACTED]" in saved_pref.prompt_summary
    assert result["saved_prompt"] == saved_pref.prompt_summary


@pytest.mark.asyncio
async def test_save_ui_preference_flushes_disabled_active_rows_before_insert():
    db = AsyncMock()
    active_pref = UserSkillUIPreference(
        id="uipref-active",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        overlay_json={},
        enabled=True,
        version=2,
    )
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[active_pref])))
    ))
    db.scalar = AsyncMock(return_value=2)
    events: list[str] = []

    async def record_flush():
        events.append("flush")

    db.flush = AsyncMock(side_effect=record_flush)
    db.add = MagicMock(side_effect=lambda _pref: events.append("add"))

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=_mock_skill())), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})):
        result = await save_ui_preference(
            db,
            skill_id="skill-ui",
            user=_mock_user("u1"),
            surface="run_form",
            overlay={"schema_version": "skill-ui/v1", "surface": "run_form"},
            generated_by="manual",
        )

    assert active_pref.enabled is False
    assert events == ["flush", "add", "flush"]
    assert result["ui_pref_version"] == 3


@pytest.mark.asyncio
async def test_list_ui_preferences_returns_version_history_without_overlay_payload():
    rows = [
        UserSkillUIPreference(
            id="uipref-v2",
            user_id="u1",
            skill_id="skill-ui",
            surface="run_form",
            overlay_json={
                "schema_version": "skill-ui/v1",
                "surface": "run_form",
                "components": [{"id": "field-date", "type": "field", "binding": "params.date"}],
                "hidden_component_ids": ["field-platform"],
                "personal_defaults": {"platform": "直通车"},
            },
            generated_by="ai",
            prompt_summary="改成投放日期 api_key=sk-history-secret",
            enabled=True,
            version=2,
        ),
        UserSkillUIPreference(
            id="uipref-v1",
            user_id="u1",
            skill_id="skill-ui",
            surface="run_form",
            overlay_json={"schema_version": "skill-ui/v1", "surface": "run_form"},
            generated_by="manual",
            enabled=False,
            version=1,
        ),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    ))
    db.scalar = AsyncMock(return_value=2)

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=_mock_skill())) as mock_require:
        result = await list_ui_preferences(
            db,
            skill_id="skill-ui",
            user=_mock_user("u1"),
            surface="run_form",
        )

    assert mock_require.await_args.args[3] == "execute"
    assert result["total"] == 2
    assert result["items"][0]["id"] == "uipref-v2"
    assert result["items"][0]["enabled"] is True
    assert result["items"][0]["component_count"] == 1
    assert result["items"][0]["hidden_component_count"] == 1
    assert result["items"][0]["personal_default_count"] == 1
    assert "sk-history-secret" not in result["items"][0]["prompt_summary"]
    assert "overlay" not in result["items"][0]


@pytest.mark.asyncio
async def test_restore_ui_preference_copies_old_version_into_new_active_version():
    source_pref = UserSkillUIPreference(
        id="uipref-v1",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        base_skill_commit="old",
        overlay_json={
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "operations": [{"op": "rename_title", "component_id": "field-date", "title": "投放日期"}],
        },
        generated_by="ai",
        prompt_summary="把日期改成投放日期",
        enabled=False,
        version=1,
    )
    active_pref = UserSkillUIPreference(
        id="uipref-v2",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        overlay_json={"schema_version": "skill-ui/v1", "surface": "run_form"},
        enabled=True,
        version=2,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=source_pref)
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[active_pref])))
    ))
    db.scalar = AsyncMock(return_value=2)
    db.flush = AsyncMock()
    db.add = MagicMock()

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=_mock_skill())), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})):
        result = await restore_ui_preference(
            db,
            skill_id="skill-ui",
            user=_mock_user("u1"),
            preference_id="uipref-v1",
            surface="run_form",
        )

    restored_pref = db.add.call_args.args[0]
    assert active_pref.enabled is False
    assert restored_pref.version == 3
    assert restored_pref.overlay_json == source_pref.overlay_json
    assert restored_pref.base_skill_commit == "abc123"
    assert result["ui_pref_version"] == 3
    assert result["saved_prompt"] == "把日期改成投放日期"
    by_id = {item["id"]: item for item in result["merged_schema"]["components"]}
    assert by_id["field-date"]["title"] == "投放日期"


@pytest.mark.asyncio
async def test_save_ui_preference_returns_conflict_on_concurrent_active_insert():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    ))
    db.scalar = AsyncMock(return_value=2)
    db.add = MagicMock()
    db.flush = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate key")))

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=_mock_skill())):
        with pytest.raises(AppError) as exc_info:
            await save_ui_preference(
                db,
                skill_id="skill-ui",
                user=_mock_user("u1"),
                surface="run_form",
                overlay={"schema_version": "skill-ui/v1", "surface": "run_form"},
                generated_by="manual",
            )

    assert exc_info.value.code == "UI_PREF_CONFLICT"
    assert exc_info.value.status == 409
    assert "刷新后重试" in exc_info.value.detail["detail"]
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_ai_preview_reports_llm_status():
    db = AsyncMock()
    skill = _mock_skill()
    llm_overlay = {
        "overlay": {
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "components": [{
                "id": "field-date",
                "type": "field",
                "binding": "params.date",
                "title": "投放日期",
            }],
        },
    }

    mock_call_llm = AsyncMock(return_value=llm_overlay)
    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})), \
         patch("app.common.ai.call_llm", new=mock_call_llm):
        result = await preview_ai_ui(
            db,
            skill_id="skill-ui",
            user=_mock_user(),
            surface="run_form",
            instruction="日期叫投放日期",
        )

    assert result["ai_status"] == "llm"
    assert result["merged_schema"]["components"][0]["title"] == "投放日期"
    assert "api_key" not in json.dumps(result["ai_context"]).lower()
    assert "authorization" not in json.dumps(result["ai_context"]).lower()
    assert mock_call_llm.await_args.kwargs["require_system_config"] is True


@pytest.mark.asyncio
async def test_ai_preview_redacts_secret_like_llm_rejection_reason():
    db = AsyncMock()
    skill = _mock_skill()
    mock_call_llm = AsyncMock(return_value={
        "overlay": {
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "api_key": "normal text",
        },
    })

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})), \
         patch("app.common.ai.call_llm", new=mock_call_llm):
        result = await preview_ai_ui(
            db,
            skill_id="skill-ui",
            user=_mock_user(),
            surface="run_form",
            instruction="整理运行表单",
        )

    assert result["ai_status"] == "fallback"
    assert result["ai_reason"] == "llm_overlay_rejected:secret_like_ui_rejected"
    assert "api_key" not in result["ai_reason"]


@pytest.mark.asyncio
async def test_ai_preview_excludes_secret_like_param_fields_from_llm_context():
    db = AsyncMock()
    skill = _mock_skill()
    skill.param_ui_schema["properties"]["api_key"] = {"type": "string", "title": "API Key"}
    captured_prompt = {}

    async def fake_call_llm(_system, user_prompt, **_kwargs):
        captured_prompt["text"] = user_prompt
        return {
            "overlay": {
                "schema_version": "skill-ui/v1",
                "surface": "run_form",
            },
        }

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})), \
         patch("app.common.ai.call_llm", new=fake_call_llm):
        result = await preview_ai_ui(
            db,
            skill_id="skill-ui",
            user=_mock_user(),
            surface="run_form",
            instruction="整理运行表单",
        )

    prompt_text = captured_prompt["text"].lower()
    assert result["ai_status"] == "llm"
    assert "api_key" not in prompt_text
    assert "params.api_key" not in prompt_text


@pytest.mark.asyncio
async def test_ai_preview_rejects_current_overlay_secret_text_before_llm_call():
    db = AsyncMock()
    skill = _mock_skill()

    mock_call_llm = AsyncMock(return_value={
        "overlay": {
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
        },
    })

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})), \
         patch("app.common.ai.call_llm", new=mock_call_llm):
        with pytest.raises(AppError) as exc_info:
            await preview_ai_ui(
                db,
                skill_id="skill-ui",
                user=_mock_user(),
                surface="run_form",
                instruction="整理运行表单",
                current_overlay={
                    "schema_version": "skill-ui/v1",
                    "surface": "run_form",
                    "notes": "api_key=sk-current-overlay-secret Authorization: Bearer raw-current-token",
                },
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "secret-like" in str(exc_info.value.detail)
    mock_call_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_preview_redacts_secret_text_from_instruction_before_llm_call():
    db = AsyncMock()
    skill = _mock_skill()
    captured_prompt = {}

    async def fake_call_llm(_system, user_prompt, **_kwargs):
        captured_prompt["text"] = user_prompt
        return {
            "overlay": {
                "schema_version": "skill-ui/v1",
                "surface": "run_form",
            },
        }

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})), \
         patch("app.common.ai.call_llm", new=fake_call_llm):
        result = await preview_ai_ui(
            db,
            skill_id="skill-ui",
            user=_mock_user(),
            surface="run_form",
            instruction="整理界面 api_key=sk-instruction-secret Authorization: Bearer raw-instruction-token",
        )

    prompt_text = captured_prompt["text"]
    assert result["ai_status"] == "llm"
    assert "sk-instruction-secret" not in prompt_text
    assert "raw-instruction-token" not in prompt_text
    assert "[REDACTED]" in prompt_text


@pytest.mark.asyncio
async def test_ai_preview_merges_incremental_overlay_without_losing_current_changes():
    db = AsyncMock()
    skill = _mock_skill()
    current_overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [
            {"op": "rename_title", "component_id": "field-date", "title": "投放日期"},
        ],
    }
    llm_overlay = {
        "overlay": {
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "operations": [
                {"op": "set_personal_default", "path": "platform", "value": "万相台"},
            ],
        },
    }

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})), \
         patch("app.common.ai.call_llm", new=AsyncMock(return_value=llm_overlay)):
        result = await preview_ai_ui(
            db,
            skill_id="skill-ui",
            user=_mock_user(),
            surface="run_form",
            instruction="平台默认万相台",
            current_overlay=current_overlay,
        )

    by_id = {item["id"]: item for item in result["merged_schema"]["components"]}
    assert by_id["field-date"]["title"] == "投放日期"
    assert result["merged_schema"]["personal_defaults"]["platform"] == "万相台"
    assert len(result["overlay"]["operations"]) == 2


@pytest.mark.asyncio
async def test_ai_preview_patch_hide_wins_over_existing_show_operation():
    db = AsyncMock()
    skill = _mock_skill()
    current_overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [
            {"op": "show_component", "component_id": "field-platform"},
        ],
    }
    llm_overlay = {
        "overlay": {
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "hidden_component_ids": ["field-platform"],
        },
    }

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})), \
         patch("app.common.ai.call_llm", new=AsyncMock(return_value=llm_overlay)):
        result = await preview_ai_ui(
            db,
            skill_id="skill-ui",
            user=_mock_user(),
            surface="run_form",
            instruction="隐藏平台",
            current_overlay=current_overlay,
        )

    by_id = {item["id"]: item for item in result["merged_schema"]["components"]}
    assert by_id["field-platform"]["visible"] is False
    assert not any(
        item.get("op") == "show_component" and item.get("component_id") == "field-platform"
        for item in result["overlay"].get("operations", [])
    )


@pytest.mark.asyncio
async def test_ai_preview_patch_visible_true_removes_existing_hidden_state():
    db = AsyncMock()
    skill = _mock_skill()
    current_overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "hidden_component_ids": ["field-platform"],
        "operations": [
            {"op": "hide_component", "component_id": "field-platform"},
        ],
    }
    llm_overlay = {
        "overlay": {
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "components": [{
                "id": "field-platform",
                "type": "field",
                "binding": "params.platform",
                "visible": True,
            }],
        },
    }

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})), \
         patch("app.common.ai.call_llm", new=AsyncMock(return_value=llm_overlay)):
        result = await preview_ai_ui(
            db,
            skill_id="skill-ui",
            user=_mock_user(),
            surface="run_form",
            instruction="显示平台",
            current_overlay=current_overlay,
        )

    by_id = {item["id"]: item for item in result["merged_schema"]["components"]}
    assert by_id["field-platform"]["visible"] is True
    assert "field-platform" not in result["overlay"].get("hidden_component_ids", [])
    assert not any(
        item.get("op") == "hide_component" and item.get("component_id") == "field-platform"
        for item in result["overlay"].get("operations", [])
    )


@pytest.mark.asyncio
async def test_ai_preview_redacts_llm_error_reason():
    db = AsyncMock()
    skill = _mock_skill()
    unsafe_error = Exception("auth failed api_key=sk-live-secret-123 Authorization: Bearer raw-token-456")  # gitleaks:allow -- enum/config key or deliberate synthetic test fixture; not a credential

    with patch("app.portal.ui_service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})), \
         patch("app.common.ai.call_llm", new=AsyncMock(side_effect=unsafe_error)):
        result = await preview_ai_ui(
            db,
            skill_id="skill-ui",
            user=_mock_user(),
            surface="run_form",
            instruction="日期叫投放日期",
        )

    assert result["ai_status"] == "fallback"
    assert "sk-live-secret-123" not in result["ai_reason"]
    assert "raw-token-456" not in result["ai_reason"]
    assert "[REDACTED]" in result["ai_reason"]


@pytest.mark.asyncio
async def test_build_ui_response_returns_saved_prompt_scoped_to_preference():
    skill = _mock_skill()
    user = _mock_user("u1")
    pref = UserSkillUIPreference(
        id="uipref-1",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        base_skill_commit="abc123",
        overlay_json={
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "operations": [{"op": "rename_title", "component_id": "field-date", "title": "投放日期"}],
        },
        generated_by="ai",
        prompt_summary="把日期字段叫投放日期",
        version=2,
        enabled=True,
    )

    with patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})):
        result = await build_ui_response(AsyncMock(), skill=skill, user=user, surface="run_form", preference=pref)

    assert result["ui_pref_id"] == "uipref-1"
    assert result["generated_by"] == "ai"
    assert result["saved_prompt"] == "把日期字段叫投放日期"


@pytest.mark.asyncio
async def test_build_ui_response_redacts_legacy_saved_prompt_on_read():
    skill = _mock_skill()
    user = _mock_user("u1")
    pref = UserSkillUIPreference(
        id="uipref-legacy-secret",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        base_skill_commit="abc123",
        overlay_json={"schema_version": "skill-ui/v1", "surface": "run_form"},
        generated_by="ai",
        prompt_summary="旧提示词 api_key=sk-legacy-prompt-secret Authorization: Bearer raw-legacy-token",
        version=2,
        enabled=True,
    )

    with patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})):
        result = await build_ui_response(AsyncMock(), skill=skill, user=user, surface="run_form", preference=pref)

    assert "sk-legacy-prompt-secret" not in result["saved_prompt"]
    assert "raw-legacy-token" not in result["saved_prompt"]
    assert "[REDACTED]" in result["saved_prompt"]


@pytest.mark.asyncio
async def test_build_ui_response_disables_stale_invalid_preference_and_returns_default():
    skill = _mock_skill()
    user = _mock_user("u1")
    pref = UserSkillUIPreference(
        id="uipref-stale",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        base_skill_commit="old-commit",
        overlay_json={
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "operations": [{
                "op": "rename_title",
                "component_id": "field-date",
                "title": "投放日期",
            }],
            "personal_defaults": {"removed_field": "old-value"},
        },
        generated_by="ai",
        prompt_summary="旧字段偏好",
        version=4,
        enabled=True,
    )
    db = AsyncMock()
    db.flush = AsyncMock()

    with patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})):
        result = await build_ui_response(db, skill=skill, user=user, surface="run_form", preference=pref)

    assert result["ui_pref_id"] is None
    assert result["saved_prompt"] is None
    assert result["overlay"] == {}
    assert result["ui_pref_invalidated"]["ui_pref_id"] == "uipref-stale"
    assert pref.enabled is False
    db.flush.assert_awaited_once()
    by_id = {item["id"]: item for item in result["merged_schema"]["components"]}
    assert by_id["field-date"]["title"] == "日期"


@pytest.mark.asyncio
async def test_build_ui_response_redacts_secret_like_invalidated_reason():
    skill = _mock_skill()
    user = _mock_user("u1")
    pref = UserSkillUIPreference(
        id="uipref-secret-stale",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        base_skill_commit="old-commit",
        overlay_json={
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "api_key": "normal text",
        },
        generated_by="ai",
        prompt_summary="旧字段偏好",
        version=5,
        enabled=True,
    )
    db = AsyncMock()
    db.flush = AsyncMock()

    with patch("app.portal.ui_service.get_skill_permissions", new=AsyncMock(return_value={"read": True, "execute": True})):
        result = await build_ui_response(db, skill=skill, user=user, surface="run_form", preference=pref)

    assert result["ui_pref_invalidated"]["reason"] == "secret_like_ui_rejected"
    assert "api_key" not in result["ui_pref_invalidated"]["reason"]
    assert pref.enabled is False


@pytest.mark.asyncio
async def test_submission_context_records_requested_ui_preference_version():
    skill = _mock_skill()
    user = _mock_user("u1")
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {"platform": "直通车"},
    }
    pref = UserSkillUIPreference(
        id="uipref-1",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        base_skill_commit="abc123",
        overlay_json=overlay,
        version=3,
        enabled=True,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=pref)

    context = await get_submission_ui_context(
        db,
        skill=skill,
        user=user,
        surface="run_form",
        ui_pref_id="uipref-1",
        ui_pref_version=3,
    )

    assert context["ui_pref_id"] == "uipref-1"
    assert context["ui_pref_version"] == 3
    assert context["base_skill_commit"] == "abc123"
    assert context["ui_snapshot_json"]["personal_defaults"]["platform"] == "直通车"
    assert context["ui_snapshot_json"]["param_defaults_source"]["platform"] == "personal_overlay"
    assert context["ui_snapshot_json"]["overlay"] == overlay
    assert context["ui_snapshot_json"]["merged_schema"]["personal_defaults"]["platform"] == "直通车"


@pytest.mark.asyncio
async def test_submission_context_resolves_yesterday_personal_defaults_for_run_snapshot():
    from datetime import timedelta

    from app.common.time_utils import now_bjt

    skill = _mock_skill()
    skill.param_ui_schema["properties"]["date"]["default"] = "yesterday"
    user = _mock_user("u1")
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "personal_defaults": {"date": "yesterday"},
    }
    pref = UserSkillUIPreference(
        id="uipref-date",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        base_skill_commit="abc123",
        overlay_json=overlay,
        version=4,
        enabled=True,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=pref)

    context = await get_submission_ui_context(
        db,
        skill=skill,
        user=user,
        surface="run_form",
        ui_pref_id="uipref-date",
        ui_pref_version=4,
    )

    expected = (now_bjt().date() - timedelta(days=1)).isoformat()
    assert context["ui_snapshot_json"]["personal_defaults"]["date"] == expected
    assert context["ui_snapshot_json"]["merged_schema"]["personal_defaults"]["date"] == expected
    assert context["ui_snapshot_json"]["overlay"] == overlay


@pytest.mark.asyncio
async def test_submission_context_rejects_explicit_disabled_ui_preference():
    pref = UserSkillUIPreference(
        id="uipref-disabled",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        base_skill_commit="abc123",
        overlay_json={"schema_version": "skill-ui/v1", "surface": "run_form"},
        version=2,
        enabled=False,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=pref)

    with pytest.raises(AppError) as exc_info:
        await get_submission_ui_context(
            db,
            skill=_mock_skill(),
            user=_mock_user("u1"),
            surface="run_form",
            ui_pref_id="uipref-disabled",
            ui_pref_version=2,
        )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "no longer active" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submission_context_disables_implicit_stale_preference_and_uses_default():
    skill = _mock_skill()
    pref = UserSkillUIPreference(
        id="uipref-stale-active",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        base_skill_commit="old-commit",
        overlay_json={
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "personal_defaults": {"removed_field": "old-value"},
        },
        version=5,
        enabled=True,
    )
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=pref)
    db.flush = AsyncMock()

    context = await get_submission_ui_context(
        db,
        skill=skill,
        user=_mock_user("u1"),
        surface="run_form",
    )

    assert context["ui_pref_id"] is None
    assert context["ui_pref_version"] is None
    assert context["ui_pref_invalidated"]["ui_pref_id"] == "uipref-stale-active"
    assert context["ui_snapshot_json"]["ui_pref_id"] is None
    assert context["ui_snapshot_json"]["overlay"] == {}
    assert pref.enabled is False
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_submission_context_without_explicit_ui_context_uses_default_even_with_active_preference():
    skill = _mock_skill()
    pref = UserSkillUIPreference(
        id="uipref-active",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        base_skill_commit="abc123",
        overlay_json={
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "personal_defaults": {"platform": "直通车"},
        },
        version=7,
        enabled=True,
    )
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=pref)
    db.flush = AsyncMock()

    context = await get_submission_ui_context(
        db,
        skill=skill,
        user=_mock_user("u1"),
        surface="run_form",
    )

    assert context["ui_pref_id"] is None
    assert context["ui_pref_version"] is None
    assert context["ui_pref_invalidated"] is None
    assert context["ui_snapshot_json"]["overlay"] == {}
    assert context["ui_snapshot_json"]["personal_defaults"] == {}
    assert pref.enabled is True
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_submission_context_rejects_explicit_stale_preference_without_disabling():
    pref = UserSkillUIPreference(
        id="uipref-explicit-stale",
        user_id="u1",
        skill_id="skill-ui",
        surface="run_form",
        base_skill_commit="old-commit",
        overlay_json={
            "schema_version": "skill-ui/v1",
            "surface": "run_form",
            "personal_defaults": {"removed_field": "old-value"},
        },
        version=6,
        enabled=True,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=pref)
    db.flush = AsyncMock()

    with pytest.raises(AppError) as exc_info:
        await get_submission_ui_context(
            db,
            skill=_mock_skill(),
            user=_mock_user("u1"),
            surface="run_form",
            ui_pref_id="uipref-explicit-stale",
            ui_pref_version=6,
        )

    assert exc_info.value.code == "PARAM_INVALID"
    assert pref.enabled is True
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_submission_context_rejects_non_run_form_surface():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(AppError) as exc_info:
        await get_submission_ui_context(
            db,
            skill=_mock_skill(),
            user=_mock_user("u1"),
            surface="result",
        )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "run_form" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submission_context_rejects_oversized_ui_snapshot(monkeypatch):
    monkeypatch.setattr(ui_service, "MAX_UI_SNAPSHOT_BYTES", 64)
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(AppError) as exc_info:
        await get_submission_ui_context(
            db,
            skill=_mock_skill(),
            user=_mock_user("u1"),
            surface="run_form",
        )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "ui snapshot too large" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submission_context_rejects_cross_user_ui_preference():
    pref = UserSkillUIPreference(
        id="uipref-foreign",
        user_id="u2",
        skill_id="skill-ui",
        surface="run_form",
        overlay_json={},
        version=1,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=pref)

    with pytest.raises(AppError) as exc_info:
        await get_submission_ui_context(
            db,
            skill=_mock_skill(),
            user=_mock_user("u1"),
            surface="run_form",
            ui_pref_id="uipref-foreign",
        )

    assert exc_info.value.code == "AUTH_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_submission_context_rejects_missing_explicit_ui_preference_version():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(AppError) as exc_info:
        await get_submission_ui_context(
            db,
            skill=_mock_skill(),
            user=_mock_user("u1"),
            surface="run_form",
            ui_pref_version=999,
        )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "ui preference not found" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submission_context_rejects_stale_schema_hash():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(AppError) as exc_info:
        await get_submission_ui_context(
            db,
            skill=_mock_skill(),
            user=_mock_user("u1"),
            surface="run_form",
            merged_ui_schema_hash="sha256:not-current",
        )

    assert exc_info.value.code == "PARAM_INVALID"


def test_stable_schema_hash_is_deterministic():
    assert stable_schema_hash({"b": 1, "a": 2}) == stable_schema_hash({"a": 2, "b": 1})
