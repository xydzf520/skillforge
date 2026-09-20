"""M1 门户服务测试。"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import true

from app.common.exceptions import AppError


def _mock_user(*, user_id: str = "u1", role: str = "biz_owner", department: str = "EC"):
    user = MagicMock()
    user.id = user_id
    user.name = "测试用户"
    user.username = user_id
    user.role = role
    user.department = department
    user.can_view_all = False
    user.is_active = True
    user.permissions_rev = 7
    return user


def _mock_skill(
    *,
    skill_id: str = "skill-1",
    visibility: str = "company",
    status: str = "active",
    org_unit_id: str | None = "EC-OPS",
    owner: str | None = "owner-1",
    category: str = "报表",
    department: str = "EC",
):
    skill = MagicMock()
    skill.id = skill_id
    skill.name = "投放日报"
    skill.display_name = "投放日报"
    skill.summary = "汇总投放效果"
    skill.description = "说明"
    skill.category = category
    skill.icon = "apps"
    skill.visibility = visibility
    skill.status = status
    skill.org_unit_id = org_unit_id
    skill.owner = owner
    skill.department = department
    skill.usage_count = 12
    skill.success_rate = 0.92
    skill.last_run_at = datetime(2026, 4, 12, 18, 30, 0)
    skill.param_ui_schema = None
    skill.result_ui_schema = None
    return skill


def _mock_submission(
    *,
    submission_id: str = "sub-1",
    skill_id: str = "skill-1",
    requester_id: str = "u1",
    status: str = "completed",
    execution_id: str | None = "run-1",
):
    sub = MagicMock()
    sub.id = submission_id
    sub.skill_id = skill_id
    sub.requester_id = requester_id
    sub.org_unit_id = "EC-OPS"
    sub.params = {"date": "2026-04-12"}
    sub.execution_id = execution_id
    sub.status = status
    sub.result_summary = {"type": "text", "data": "ok", "analysis": "good"}
    sub.error_message = None
    sub.created_at = datetime(2026, 4, 12, 19, 0, 0)
    sub.completed_at = datetime(2026, 4, 12, 19, 2, 30)
    return sub


def _result(*rows):
    result = MagicMock()
    result.all.return_value = list(rows)
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.all.return_value = []
    result.scalars.return_value.first.return_value = None
    return result


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.all.return_value = []
    result.scalars.return_value.all.return_value = []
    result.scalars.return_value.first.return_value = None
    return result


def test_validate_params_rejects_secret_like_schema_specs():
    from app.portal.service import _validate_params

    skill = _mock_skill()
    skill.param_ui_schema = {
        "type": "object",
        "properties": {
            "date": {"type": "string"},
            "auth": {"type": "string", "format": "password", "title": "授权"},
        },
        "required": ["date", "auth"],
    }

    with pytest.raises(AppError) as exc_info:
        _validate_params(skill, {"date": "2026-05-21", "auth": "secret-value"})

    assert exc_info.value.code == "PARAM_INVALID"
    assert "auth" in str(exc_info.value.detail)


def test_validate_params_ignores_required_fields_not_in_safe_schema():
    from app.portal.service import _validate_params

    skill = _mock_skill()
    skill.param_ui_schema = {
        "type": "object",
        "required": ["date", "ghost", "api_key"],
        "properties": {
            "date": {"type": "string"},
            "api_key": {"type": "string", "title": "API Key"},
            "filters": {
                "type": "object",
                "required": ["platform", "ghost_child", "access_token"],
                "properties": {
                    "platform": {"type": "string"},
                    "access_token": {"type": "string"},
                },
            },
        },
    }

    result = _validate_params(skill, {"date": "2026-05-21", "filters": {"platform": "万相台"}})

    assert result["date"] == "2026-05-21"


@pytest.mark.asyncio
async def test_list_skills_respects_visibility_company():
    from app.portal.service import list_portal_skills

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[_mock_skill(visibility="company", org_unit_id=None)])))))

    user = _mock_user()
    with patch("app.portal.service._user_org_ids", new=AsyncMock(return_value={"EC-OPS"})), \
         patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=_mock_skill(visibility="company", org_unit_id=None))), \
         patch("app.portal.service._owner_name_map", new=AsyncMock(return_value={"owner-1": "负责人"})), \
         patch("app.portal.service._skill_tags_map", new=AsyncMock(return_value={"skill-1": ["投放"]})), \
         patch("app.portal.service.cache_get", new=AsyncMock(return_value=None)), \
         patch("app.portal.service.cache_set", new=AsyncMock()):
        result = await list_portal_skills(db, user)

    assert result["total"] == 1
    assert result["items"][0]["visibility"] == "company"


@pytest.mark.asyncio
async def test_list_skills_respects_visibility_department():
    from app.portal.service import list_portal_skills

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[_mock_skill(visibility="department", org_unit_id=None)])))))

    user = _mock_user(department="EC-OPS")
    with patch("app.portal.service._user_org_ids", new=AsyncMock(return_value={"EC-OPS"})), \
         patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=_mock_skill(visibility="department", org_unit_id=None))), \
         patch("app.portal.service._owner_name_map", new=AsyncMock(return_value={})), \
         patch("app.portal.service._skill_tags_map", new=AsyncMock(return_value={})), \
         patch("app.portal.service.cache_get", new=AsyncMock(return_value=None)), \
         patch("app.portal.service.cache_set", new=AsyncMock()):
        result = await list_portal_skills(db, user)

    assert result["total"] == 1
    assert result["items"][0]["visibility"] == "department"


@pytest.mark.asyncio
async def test_list_skills_respects_visibility_private():
    from app.portal.service import list_portal_skills

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[_mock_skill(visibility="private", org_unit_id=None)])))))

    user = _mock_user()
    with patch("app.portal.service._user_org_ids", new=AsyncMock(return_value={"EC-OPS"})), \
         patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=_mock_skill(visibility="private", org_unit_id=None))), \
         patch("app.portal.service._owner_name_map", new=AsyncMock(return_value={})), \
         patch("app.portal.service._skill_tags_map", new=AsyncMock(return_value={})), \
         patch("app.portal.service.cache_get", new=AsyncMock(return_value=None)), \
         patch("app.portal.service.cache_set", new=AsyncMock()):
        result = await list_portal_skills(db, user)

    assert result["total"] == 1
    assert result["items"][0]["visibility"] == "private"


@pytest.mark.asyncio
async def test_list_portal_skills_returns_department_counts_and_filters():
    from app.portal.service import list_portal_skills

    rows = [
        _mock_skill(skill_id="skill-ec-1", department="EC", category="报表"),
        _mock_skill(skill_id="skill-cs-1", department="CS", category="客服"),
        _mock_skill(skill_id="skill-ec-2", department="EC", category="报表"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows)))))

    with patch("app.portal.service.build_skill_access_filter", new=AsyncMock(return_value=true())), \
         patch("app.portal.service._owner_name_map", new=AsyncMock(return_value={"owner-1": "负责人"})), \
         patch("app.portal.service._skill_tags_map", new=AsyncMock(return_value={})), \
         patch("app.portal.service.cache_get", new=AsyncMock(return_value=None)), \
         patch("app.portal.service.cache_set", new=AsyncMock()):
        result = await list_portal_skills(db, _mock_user(), department="EC")

    assert result["total"] == 2
    assert [item["id"] for item in result["items"]] == ["skill-ec-1", "skill-ec-2"]
    assert result["departments"] == [
        {"name": "EC", "count": 2},
        {"name": "CS", "count": 1},
    ]
    assert result["categories"][0] == {"name": "报表", "count": 2}


@pytest.mark.asyncio
async def test_get_portal_skill_filters_secret_like_param_schema():
    from app.portal.service import get_portal_skill

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result("负责人"),
        _scalar_result("电商运营"),
        _result(),
        _result(),
    ])
    skill = _mock_skill()
    skill.param_ui_schema = {
        "type": "object",
        "x_internal": "sk-top-level-secret",
        "required": ["date", "api_key", "private_key", "auth", "ghost"],
        "properties": {
            "date": {
                "type": "string",
                "title": "日期",
                "description": "Authorization: Bearer raw-param-description-token",
                "default": "sk-param-default-secret",
                "enum": ["2026-05-21", "api_key=sk-param-enum-secret"],
                "examples": ["sk-example-secret"],
                "x_internal": "sk-field-secret",
            },
            "platform": {
                "type": "string",
                "title": "平台",
                "dependsOn": {
                    "field": "api_key",
                    "value": "sk-depends-secret",
                    "operator": "eq",
                    "internal": "sk-depends-internal",
                },
            },
            "channel": {
                "type": "string",
                "title": "渠道",
                "format": "api_key=sk-param-format-secret",
            },
            "api_key": {"type": "string", "title": "API Key"},
            "private_key": {"type": "string", "title": "Private Key"},
            "auth": {"type": "string", "format": "password", "title": "授权"},
            "filters": {
                "type": "object",
                "required": ["access_token", "platform", "ghost_child"],
                "properties": {
                    "access_token": {"type": "string"},
                    "platform": {"type": "string"},
                },
            },
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["sku", "api_key", "ghost_item"],
                    "properties": {
                        "sku": {"type": "string"},
                        "api_key": {"type": "string", "title": "API Key"},
                        "note": {
                            "type": "string",
                            "description": "Authorization: Bearer raw-array-description-token",
                            "x_internal": "sk-array-field-secret",
                        },
                    },
                },
            },
        },
    }
    skill.result_ui_schema = {
        "type": "table",
        "title": "结果 Authorization: Bearer raw-result-title-token",
        "x_internal": "sk-result-top-level-secret",
        "debug": {"api_key": "sk-result-debug-secret"},
        "columns": [
            {"key": "date", "title": "日期"},
            {"key": "api_key", "title": "API Key"},
            {"key": "token_amount", "title": "Token"},
            {"key": "cost", "title": "花费", "format": "currency"},
            {"key": "bad", "title": "正常", "format": "api_key=sk-format-secret"},
            {"key": "extra", "title": "扩展", "internal": "sk-internal-secret"},
        ],
    }

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.service.get_skill_permissions", new=AsyncMock(return_value={"read": True})), \
         patch("app.portal.service._extract_description", return_value=""):
        result = await get_portal_skill(db, "skill-1", _mock_user())

    schema = result["param_ui_schema"]
    assert "api_key" not in schema["properties"]
    assert "api_key" not in schema["required"]
    assert "private_key" not in schema["properties"]
    assert "private_key" not in schema["required"]
    assert "auth" not in schema["properties"]
    assert "auth" not in schema["required"]
    assert "ghost" not in schema["required"]
    assert schema["properties"]["date"]["description"] == "[REDACTED]"
    assert "default" not in schema["properties"]["date"]
    assert schema["properties"]["date"]["enum"] == ["2026-05-21"]
    assert "x_internal" not in schema
    assert "x_internal" not in schema["properties"]["date"]
    assert "examples" not in schema["properties"]["date"]
    assert "dependsOn" not in schema["properties"]["platform"]
    assert "format" not in schema["properties"]["channel"]
    assert "access_token" not in schema["properties"]["filters"]["properties"]
    assert "access_token" not in schema["properties"]["filters"]["required"]
    assert "ghost_child" not in schema["properties"]["filters"]["required"]
    assert "platform" in schema["properties"]["filters"]["properties"]
    line_item_schema = schema["properties"]["line_items"]["items"]
    assert "api_key" not in line_item_schema["properties"]
    assert line_item_schema["required"] == ["sku"]
    assert line_item_schema["properties"]["note"]["description"] == "[REDACTED]"
    assert "x_internal" not in line_item_schema["properties"]["note"]
    result_schema_text = json.dumps(result["result_ui_schema"], ensure_ascii=False)
    assert "api_key" not in result_schema_text
    assert "API Key" not in result_schema_text
    assert "token_amount" not in result_schema_text
    assert "raw-result-title-token" not in result_schema_text
    assert "x_internal" not in result_schema_text
    assert "sk-result-top-level-secret" not in result_schema_text
    assert "sk-result-debug-secret" not in result_schema_text
    assert "sk-format-secret" not in result_schema_text
    assert "sk-internal-secret" not in result_schema_text
    assert result["result_ui_schema"]["title"] == "[REDACTED]"
    assert result["result_ui_schema"]["columns"] == [
        {"key": "date", "title": "日期"},
        {"key": "cost", "title": "花费", "format": "currency"},
        {"key": "bad", "title": "正常"},
        {"key": "extra", "title": "扩展"},
    ]


@pytest.mark.asyncio
async def test_get_portal_skill_normalizes_malformed_param_schema_for_frontend():
    from app.portal.service import get_portal_skill

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result("负责人"),
        _scalar_result("电商运营"),
        _result(),
        _result(),
    ])
    skill = _mock_skill()
    skill.param_ui_schema = {
        "type": "object",
        "required": ["filters", "ghost"],
        "properties": {
            "filters": {
                "type": "object",
                "required": ["platform", "ghost_child"],
                "properties": [],
            },
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["sku", "ghost_item"],
                    "properties": [],
                },
            },
            "bad_items": {
                "type": "array",
                "items": [],
            },
        },
    }

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.service.get_skill_permissions", new=AsyncMock(return_value={"read": True})), \
         patch("app.portal.service._extract_description", return_value=""):
        result = await get_portal_skill(db, "skill-1", _mock_user())

    schema = result["param_ui_schema"]
    assert schema["required"] == ["filters"]
    assert schema["properties"]["filters"]["properties"] == {}
    assert schema["properties"]["filters"]["required"] == []
    assert schema["properties"]["line_items"]["items"]["properties"] == {}
    assert schema["properties"]["line_items"]["items"]["required"] == []
    assert "items" not in schema["properties"]["bad_items"]


@pytest.mark.asyncio
async def test_get_portal_skill_normalizes_malformed_top_level_param_schema_for_frontend():
    from app.portal.service import get_portal_skill

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result("负责人"),
        _scalar_result("电商运营"),
        _result(),
        _result(),
    ])
    skill = _mock_skill()
    skill.param_ui_schema = {
        "type": "object",
        "required": ["ghost"],
        "properties": [],
    }

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.service.get_skill_permissions", new=AsyncMock(return_value={"read": True})), \
         patch("app.portal.service._extract_description", return_value=""):
        result = await get_portal_skill(db, "skill-1", _mock_user())

    schema = result["param_ui_schema"]
    assert schema["properties"] == {}
    assert schema["required"] == []


@pytest.mark.asyncio
async def test_get_portal_skill_recent_runs_scoped_to_current_user():
    from app.portal.service import get_portal_skill

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result("负责人"),
        _scalar_result("电商运营"),
        _result(),
    ])

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=_mock_skill())), \
         patch("app.portal.service.get_skill_permissions", new=AsyncMock(return_value={"read": True})), \
         patch("app.portal.service._skill_tags_map", new=AsyncMock(return_value={})), \
         patch("app.portal.service._extract_description", return_value=""):
        await get_portal_skill(db, "skill-1", _mock_user(user_id="u-current"))

    recent_stmt = db.execute.await_args_list[2].args[0]
    assert "skill_submissions.requester_id" in str(recent_stmt)


@pytest.mark.asyncio
async def test_submit_skill_creates_submission():
    from app.portal.service import submit_portal_skill

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.get = AsyncMock()
    db.execute = AsyncMock()

    skill = _mock_skill(visibility="company")
    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)), \
         patch("app.portal.service.portal_ui_service.get_submission_ui_context", new=AsyncMock(return_value={
             "ui_pref_id": None,
             "ui_pref_version": None,
             "ui_surface": "run_form",
             "base_skill_commit": None,
             "merged_ui_schema_hash": "sha256:test",
             "ui_snapshot_json": {"merged_ui_schema_hash": "sha256:test", "merged_schema": {"components": []}},
         })), \
         patch("app.portal.service.execution_service.execute_skill", new=AsyncMock(return_value={"run_id": "exec-1", "status": "pending"})) as mock_execute, \
         patch("app.portal.service.cache_delete_pattern", new=AsyncMock()):
        result = await submit_portal_skill(
            db,
            "skill-1",
            _mock_user(),
            {"date": "2026-04-12", "platform": "直通车"},
        )

    assert result["status"] == "pending"
    assert result["execution_id"] == "exec-1"
    assert result["merged_ui_schema_hash"] == "sha256:test"
    assert mock_execute.await_args.kwargs["triggered_by"] == "portal:u1"
    assert mock_execute.await_args.kwargs["run_metadata"]["user_id"] == "u1"
    assert mock_execute.await_args.kwargs["run_metadata"]["requester_id"] == "u1"
    assert mock_execute.await_args.kwargs["run_metadata"]["ui"]["merged_ui_schema_hash"] == "sha256:test"
    assert mock_execute.await_args.kwargs["run_metadata"]["ui"]["actual_params"] == {
        "date": "2026-04-12",
        "platform": "直通车",
    }


@pytest.mark.asyncio
async def test_submit_skill_validates_params():
    from app.portal.service import submit_portal_skill

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    skill = _mock_skill(visibility="company")
    skill.param_ui_schema = {
        "type": "object",
        "properties": {
            "date": {"type": "string"},
            "platform": {"type": "string", "enum": ["直通车", "万相台"]},
        },
        "required": ["date"],
    }

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(db, "skill-1", _mock_user(), {"platform": "其他"})

    assert exc_info.value.code == "PARAM_INVALID"


@pytest.mark.asyncio
async def test_submit_skill_validates_schema_types():
    from app.portal.service import submit_portal_skill

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    skill = _mock_skill(visibility="company")
    skill.param_ui_schema = {
        "type": "object",
        "properties": {
            "threshold": {"type": "number"},
            "enabled": {"type": "boolean"},
            "report_date": {"type": "string", "format": "date"},
        },
    }

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(db, "skill-1", _mock_user(), {
                "threshold": "1.5",
                "enabled": "true",
                "report_date": "not-a-date",
            })

    assert exc_info.value.code == "PARAM_INVALID"


@pytest.mark.asyncio
async def test_submit_skill_rejects_unknown_schema_params():
    from app.portal.service import submit_portal_skill

    skill = _mock_skill(visibility="company")
    skill.param_ui_schema = {
        "type": "object",
        "properties": {
            "date": {"type": "string"},
        },
    }

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-1",
                _mock_user(),
                {"date": "2026-05-21", "unexpected": "x"},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "未知参数" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submit_skill_rejects_reserved_params_without_schema():
    from app.portal.service import submit_portal_skill

    skill = _mock_skill(visibility="company")
    skill.param_ui_schema = None

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-1",
                _mock_user(),
                {"instance_id": "bridge-1"},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "系统保留字段" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_get_submission_only_own():
    from app.portal.service import get_submission_detail

    db = AsyncMock()
    submission = _mock_submission(requester_id="u1")
    db.get = AsyncMock(side_effect=[submission, _mock_skill(visibility="company"), None])
    db.execute = AsyncMock(return_value=_result())

    result = await get_submission_detail(db, "sub-1", _mock_user())

    assert result["id"] == "sub-1"
    assert result["skill_display_name"] == "投放日报"


@pytest.mark.asyncio
async def test_get_submission_detail_redacts_historical_secret_params():
    from app.portal.service import get_submission_detail

    db = AsyncMock()
    submission = _mock_submission(requester_id="u1")
    submission.params = {
        "date": "2026-04-12",
        "api_key": "sk-historical-param-secret",
        "nested": {"access_token": "raw-history-token"},
    }
    db.get = AsyncMock(side_effect=[submission, _mock_skill(visibility="company"), None])
    db.execute = AsyncMock(return_value=_result())

    result = await get_submission_detail(db, "sub-1", _mock_user())
    params_text = json.dumps(result["params"], ensure_ascii=False)

    assert "2026-04-12" in params_text
    assert "api_key" not in params_text
    assert "access_token" not in params_text
    assert "sk-historical-param-secret" not in params_text
    assert "raw-history-token" not in params_text
    assert "[REDACTED]" in params_text


@pytest.mark.asyncio
async def test_get_submission_detail_redacts_historical_secret_result_output():
    from app.portal.service import get_submission_detail

    db = AsyncMock()
    submission = _mock_submission(requester_id="u1")
    submission.result_summary = {
        "type": "table",
        "data": [{
            "date": "2026-04-12",
            "api_key": "sk-result-summary-secret",
        }],
        "analysis": "Authorization: Bearer raw-result-token",
    }
    skill = _mock_skill(visibility="company")
    skill.result_ui_schema = {
        "type": "table",
        "title": "Authorization: Bearer raw-schema-title-token",
        "x_internal": "sk-schema-top-level-secret",
        "debug": {"api_key": "sk-schema-debug-secret"},
        "columns": [
            {"key": "date", "title": "日期"},
            {"key": "api_key", "title": "API Key"},
            {"key": "cost", "title": "花费", "format": "currency"},
            {"key": "bad", "title": "正常", "format": "api_key=sk-schema-format-secret"},
            {"key": "extra", "title": "扩展", "internal": "sk-schema-internal-secret"},
        ],
    }
    db.get = AsyncMock(side_effect=[submission, skill, None])
    db.execute = AsyncMock(return_value=_result())

    result = await get_submission_detail(db, "sub-1", _mock_user())
    result_text = json.dumps({
        "result_summary": result["result_summary"],
        "result_ui_schema": result["result_ui_schema"],
    }, ensure_ascii=False)

    assert "2026-04-12" in result_text
    assert "api_key" not in result_text
    assert "API Key" not in result_text
    assert "sk-result-summary-secret" not in result_text
    assert "raw-result-token" not in result_text
    assert "raw-schema-title-token" not in result_text
    assert "sk-schema-format-secret" not in result_text
    assert "sk-schema-internal-secret" not in result_text
    assert "x_internal" not in result_text
    assert "sk-schema-top-level-secret" not in result_text
    assert "sk-schema-debug-secret" not in result_text
    assert result["result_ui_schema"]["columns"] == [
        {"key": "date", "title": "日期"},
        {"key": "cost", "title": "花费", "format": "currency"},
        {"key": "bad", "title": "正常"},
        {"key": "extra", "title": "扩展"},
    ]
    assert "[REDACTED]" in result_text


@pytest.mark.asyncio
async def test_get_submission_detail_redacts_secret_error_message():
    from app.portal.service import get_submission_detail

    db = AsyncMock()
    submission = _mock_submission(requester_id="u1", status="failed")
    submission.error_message = "provider auth failed api_key=sk-error-message-secret Authorization: Bearer raw-error-token"
    db.get = AsyncMock(side_effect=[submission, _mock_skill(visibility="company"), None])
    db.execute = AsyncMock(return_value=_result())

    result = await get_submission_detail(db, "sub-1", _mock_user())

    assert "sk-error-message-secret" not in result["error_message"]
    assert "raw-error-token" not in result["error_message"]
    assert "[REDACTED]" in result["error_message"]


@pytest.mark.asyncio
async def test_list_my_submissions_redacts_historical_secret_params():
    from app.portal.service import list_my_submissions

    db = AsyncMock()
    submission = _mock_submission(requester_id="u1")
    submission.params = {
        "date": "2026-04-12",
        "api_key": "sk-list-param-secret",
    }
    db.execute = AsyncMock(return_value=_result((submission, "投放日报", "投放日报")))

    result = await list_my_submissions(db, _mock_user(user_id="u1"))
    params_text = json.dumps(result["items"][0]["params"], ensure_ascii=False)

    assert "2026-04-12" in params_text
    assert "api_key" not in params_text
    assert "sk-list-param-secret" not in params_text
    assert "[REDACTED]" in params_text


@pytest.mark.asyncio
async def test_get_submission_detail_redacts_historical_ui_snapshot_secrets():
    from app.portal.service import get_submission_detail

    db = AsyncMock()
    submission = _mock_submission(requester_id="u1")
    submission.ui_snapshot_json = {
        "overlay": {
            "components": [{
                "id": "field-api-key",
                "type": "field",
                "title": "API Key",
                "binding": "params.api_key",
            }],
        },
        "actual_params": {
            "date": "2026-04-12",
            "api_key": "sk-historical-submission-secret",  # public-scan: synthetic-fixture; deliberate redaction/rejection test
        },
    }
    db.get = AsyncMock(side_effect=[submission, _mock_skill(visibility="company"), None])
    db.execute = AsyncMock(return_value=_result())

    result = await get_submission_detail(db, "sub-1", _mock_user())
    ui_text = json.dumps(result["ui_snapshot_json"], ensure_ascii=False)

    assert "2026-04-12" in ui_text
    assert "sk-historical-submission-secret" not in ui_text  # public-scan: synthetic-fixture; deliberate redaction/rejection test
    assert "params.api_key" not in ui_text
    assert "API Key" not in ui_text
    assert "[REDACTED]" in ui_text


@pytest.mark.asyncio
async def test_get_submission_detail_denies_ai_engineer_non_requester():
    from app.portal.service import get_submission_detail

    db = AsyncMock()
    submission = _mock_submission(requester_id="u1")
    db.get = AsyncMock(return_value=submission)

    with pytest.raises(AppError) as exc_info:
        await get_submission_detail(
            db,
            "sub-1",
            _mock_user(user_id="u2", role="ai_engineer"),
        )

    assert exc_info.value.code == "AUTH_PERMISSION_DENIED"
    assert db.get.await_count == 1


@pytest.mark.asyncio
async def test_get_submission_detail_allows_can_view_all_auditor():
    from app.portal.service import get_submission_detail

    db = AsyncMock()
    submission = _mock_submission(requester_id="u1")
    user = _mock_user(user_id="auditor", role="operator")
    user.can_view_all = True
    db.get = AsyncMock(side_effect=[submission, _mock_skill(visibility="company"), None])
    db.execute = AsyncMock(return_value=_result())

    result = await get_submission_detail(db, "sub-1", user)

    assert result["id"] == "sub-1"
    assert result["params"]["date"] == "2026-04-12"


@pytest.mark.asyncio
async def test_overview_returns_org_stats():
    from app.portal.service import get_portal_overview

    db = AsyncMock()
    recent = _mock_submission()
    db.execute = AsyncMock(side_effect=[
        _result((recent, "张三", "投放日报", "张三")),
        _scalar_result("电商运营"),
    ])

    with patch("app.portal.service._user_org_ids", new=AsyncMock(return_value={"EC-OPS"})), \
         patch("app.portal.service.list_portal_skills", new=AsyncMock(return_value={"items": [{"id": "skill-1", "status": "active"}]})), \
         patch("app.portal.service.cache_get", new=AsyncMock(return_value=None)), \
         patch("app.portal.service.cache_set", new=AsyncMock()):
        result = await get_portal_overview(db, _mock_user())

    assert result["org_unit"]["name"] == "电商运营"
    assert result["stats"]["active_skills"] == 1
    assert result["recent_submissions"][0]["detail_id"] == "sub-1"
    assert result["recent_submissions"][0]["can_view_detail"] is True


@pytest.mark.asyncio
async def test_overview_hides_foreign_submission_detail_id_for_normal_user():
    from app.portal.service import get_portal_overview

    db = AsyncMock()
    foreign = _mock_submission(submission_id="sub-foreign", requester_id="u-other")
    db.execute = AsyncMock(side_effect=[
        _result((foreign, "张三", "投放日报", "同事")),
        _scalar_result("电商运营"),
    ])

    with patch("app.portal.service._user_org_ids", new=AsyncMock(return_value={"EC-OPS"})), \
         patch("app.portal.service.list_portal_skills", new=AsyncMock(return_value={"items": [{"id": "skill-1", "status": "active"}]})), \
         patch("app.portal.service.cache_get", new=AsyncMock(return_value=None)), \
         patch("app.portal.service.cache_set", new=AsyncMock()):
        result = await get_portal_overview(db, _mock_user(user_id="u-current"))

    item = result["recent_submissions"][0]
    assert item["id"] == "summary-0"
    assert item["detail_id"] is None
    assert item["can_view_detail"] is False


@pytest.mark.asyncio
async def test_overview_omits_submissions_for_unreadable_skills():
    from app.portal.service import get_portal_overview

    db = AsyncMock()
    hidden = _mock_submission(submission_id="sub-hidden", skill_id="skill-hidden", requester_id="u-other")
    db.execute = AsyncMock(side_effect=[
        _result((hidden, "隐藏 Skill", "隐藏 Skill", "同事")),
        _scalar_result("电商运营"),
    ])

    with patch("app.portal.service._user_org_ids", new=AsyncMock(return_value={"EC-OPS"})), \
         patch("app.portal.service.list_portal_skills", new=AsyncMock(return_value={"items": [{"id": "skill-1", "status": "active"}]})), \
         patch("app.portal.service.cache_get", new=AsyncMock(return_value=None)), \
         patch("app.portal.service.cache_set", new=AsyncMock()):
        result = await get_portal_overview(db, _mock_user(user_id="u-current"))

    assert result["recent_submissions"] == []
    assert result["stats"]["today_runs"] == 0
    assert result["stats"]["total_runs_7d"] == 0


@pytest.mark.asyncio
async def test_overview_cache_is_scoped_to_user_and_permission_revision():
    from app.portal.service import get_portal_overview

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _result(),
        _scalar_result("电商运营"),
    ])
    user = _mock_user(user_id="u-cache")
    user.permissions_rev = 42

    with patch("app.portal.service._user_org_ids", new=AsyncMock(return_value={"EC-OPS"})), \
         patch("app.portal.service.list_portal_skills", new=AsyncMock(return_value={"items": []})), \
         patch("app.portal.service.cache_get", new=AsyncMock(return_value=None)) as mock_cache_get, \
         patch("app.portal.service.cache_set", new=AsyncMock()) as mock_cache_set:
        await get_portal_overview(db, user)

    assert mock_cache_get.await_args.args[0] == "portal:overview:u-cache:42:EC-OPS"
    assert mock_cache_set.await_args.args[0] == "portal:overview:u-cache:42:EC-OPS"


@pytest.mark.asyncio
async def test_portal_router_exposes_skills_endpoint(client):
    with patch(
        "app.portal.router.portal_service.list_portal_skills",
        new=AsyncMock(return_value={"items": [{"id": "skill-1"}], "total": 1}),
    ) as mock_list:
        resp = await client.get("/api/portal/skills", params={"search": "库存", "department": "EC", "page": 2, "page_size": 5})

    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert mock_list.await_count == 1
    assert mock_list.await_args.kwargs["search"] == "库存"
    assert mock_list.await_args.kwargs["department"] == "EC"
    assert mock_list.await_args.kwargs["page"] == 2
    assert mock_list.await_args.kwargs["page_size"] == 5


@pytest.mark.asyncio
async def test_portal_router_exposes_submit_endpoint(client):
    with patch(
        "app.portal.router.portal_service.submit_portal_skill",
        new=AsyncMock(return_value={"id": "sub-1", "status": "pending", "execution_id": "run-1"}),
    ) as mock_submit:
        resp = await client.post("/api/portal/skills/skill-1/submit", json={"params": {"date": "2026-04-16"}})

    assert resp.status_code == 200
    assert resp.json()["id"] == "sub-1"
    assert mock_submit.await_count == 1
    assert mock_submit.await_args.args[1] == "skill-1"
    assert mock_submit.await_args.args[3] == {"date": "2026-04-16"}
    assert mock_submit.await_args.kwargs["ui_surface"] == "run_form"


@pytest.mark.asyncio
async def test_portal_router_exposes_personal_ui_endpoint(client):
    with patch(
        "app.portal.router.portal_ui_service.get_skill_ui",
        new=AsyncMock(return_value={
            "skill_id": "skill-1",
            "surface": "run_form",
            "ui_pref_id": None,
            "merged_ui_schema_hash": "sha256:test",
        }),
    ) as mock_get_ui:
        resp = await client.get("/api/portal/skills/skill-1/ui", params={"surface": "run_form"})

    assert resp.status_code == 200
    assert resp.json()["merged_ui_schema_hash"] == "sha256:test"
    assert mock_get_ui.await_count == 1
    assert mock_get_ui.await_args.args[1] == "skill-1"


@pytest.mark.asyncio
async def test_portal_router_exposes_ai_ui_preview_endpoint(client):
    with patch(
        "app.portal.router.portal_ui_service.preview_ai_ui",
        new=AsyncMock(return_value={
            "skill_id": "skill-1",
            "surface": "run_form",
            "overlay": {"schema_version": "skill-ui/v1"},
        }),
    ) as mock_preview:
        resp = await client.post(
            "/api/portal/skills/skill-1/ui/ai-preview",
            json={"surface": "run_form", "instruction": "把参数分组"},
        )

    assert resp.status_code == 200
    assert resp.json()["surface"] == "run_form"
    assert mock_preview.await_count == 1
    assert mock_preview.await_args.kwargs["instruction"] == "把参数分组"


@pytest.mark.asyncio
async def test_portal_router_exposes_ui_preference_history_endpoint(client):
    with patch(
        "app.portal.router.portal_ui_service.list_ui_preferences",
        new=AsyncMock(return_value={"items": [{"id": "pref-1", "version": 1}], "total": 1}),
    ) as mock_list:
        resp = await client.get(
            "/api/portal/skills/skill-1/ui/preferences",
            params={"surface": "run_form", "limit": 5},
        )

    assert resp.status_code == 200
    assert resp.json()["items"][0]["id"] == "pref-1"
    assert mock_list.await_count == 1
    assert mock_list.await_args.kwargs["surface"] == "run_form"
    assert mock_list.await_args.kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_portal_router_exposes_ui_preference_restore_endpoint(client):
    with patch(
        "app.portal.router.portal_ui_service.restore_ui_preference",
        new=AsyncMock(return_value={
            "skill_id": "skill-1",
            "surface": "run_form",
            "ui_pref_id": "pref-3",
            "ui_pref_version": 3,
        }),
    ) as mock_restore:
        resp = await client.post(
            "/api/portal/skills/skill-1/ui/preferences/pref-1/restore",
            params={"surface": "run_form"},
        )

    assert resp.status_code == 200
    assert resp.json()["ui_pref_version"] == 3
    assert mock_restore.await_count == 1
    assert mock_restore.await_args.kwargs["preference_id"] == "pref-1"
    assert mock_restore.await_args.kwargs["surface"] == "run_form"
