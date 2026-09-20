"""M3 门户表单 schema 测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_skill():
    skill = MagicMock()
    skill.id = "skill-form"
    skill.visibility = "company"
    skill.org_unit_id = "总部"
    skill.param_ui_schema = {
        "type": "object",
        "required": ["keywords"],
        "properties": {
            "keywords": {"type": "string"},
            "retries": {"type": "integer"},
            "threshold": {"type": "number"},
            "include_analysis": {"type": "boolean"},
            "tags": {"type": "array"},
            "report_date": {"type": "string", "format": "date"},
        },
    }
    skill.usage_count = 0
    return skill


def _mock_user():
    user = MagicMock()
    user.id = "u1"
    user.role = "operator"
    user.department = "总部"
    user.can_view_all = False
    return user


@pytest.mark.asyncio
async def test_submit_skill_accepts_normalized_schema_payload():
    from app.portal.service import submit_portal_skill

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=_mock_skill())), \
         patch("app.portal.service.portal_ui_service.get_submission_ui_context", new=AsyncMock(return_value={
             "ui_pref_id": None,
             "ui_pref_version": None,
             "ui_surface": "run_form",
             "base_skill_commit": None,
             "merged_ui_schema_hash": "sha256:test",
             "ui_snapshot_json": {"merged_ui_schema_hash": "sha256:test"},
         })), \
         patch("app.portal.service.execution_service.execute_skill", new=AsyncMock(return_value={"run_id": "exec-1", "status": "completed"})) as mock_execute, \
         patch("app.portal.service._cache_delete_pattern_safe", new=AsyncMock()):
        result = await submit_portal_skill(
            db,
            "skill-form",
            _mock_user(),
            {
                "keywords": "断货风险",
                "retries": 2,
                "threshold": 1.5,
                "include_analysis": True,
                "tags": ["库存", "预警"],
                "report_date": "2026-04-13",
            },
        )

    assert result["status"] == "completed"
    assert mock_execute.await_args.kwargs["run_metadata"]["ui"]["actual_params"]["keywords"] == "断货风险"
    assert mock_execute.await_args.kwargs["run_metadata"]["ui"]["actual_params"]["retries"] == 2


@pytest.mark.asyncio
async def test_submit_skill_rejects_wrong_schema_types():
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=_mock_skill())):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                db,
                "skill-form",
                _mock_user(),
                {
                    "keywords": "断货风险",
                    "retries": "2",
                    "threshold": "1.5",
                    "include_analysis": "yes",
                    "tags": "库存,预警",
                    "report_date": "bad-date",
                },
            )

    assert exc_info.value.code == "PARAM_INVALID"


@pytest.mark.asyncio
async def test_submit_skill_rejects_unknown_nested_schema_payload():
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    skill = _mock_skill()
    skill.param_ui_schema = {
        "type": "object",
        "properties": {
            "filters": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string"},
                },
            },
        },
    }

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {"filters": {"platform": "直通车", "_script_path": "scripts/other.py"}},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "系统保留字段" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submit_skill_rejects_secret_like_params_even_if_declared():
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    skill = _mock_skill()
    skill.param_ui_schema["properties"]["api_key"] = {"type": "string"}

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {"keywords": "断货风险", "api_key": "sk-user-secret"},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "疑似密钥字段" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submit_skill_rejects_private_key_params_even_if_declared():
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    skill = _mock_skill()
    skill.param_ui_schema["properties"]["private_key"] = {"type": "string"}

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {"keywords": "断货风险", "private_key": "raw-private-key"},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "疑似密钥字段" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submit_skill_rejects_nested_secret_like_params_without_schema():
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    skill = _mock_skill()
    skill.param_ui_schema = None

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {"config": {"access_token": "raw-token"}},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "config.access_token" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submit_skill_rejects_secret_like_param_values():
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    skill = _mock_skill()
    skill.param_ui_schema["properties"]["note"] = {"type": "string"}
    skill.param_ui_schema["properties"]["labels"] = {"type": "array"}

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {
                    "keywords": "断货风险",
                    "note": "请用 api_key=sk-param-value-secret 调试",
                    "labels": ["安全"],
                },
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "note" in str(exc_info.value.detail)

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {
                    "keywords": "断货风险",
                    "labels": ["Authorization: Bearer raw-param-list-token"],
                },
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "labels[0]" in str(exc_info.value.detail)

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {
                    "keywords": "断货风险",
                    "labels": [{"name": "Authorization: Bearer raw-param-dict-token"}],
                },
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "labels[0].name" in str(exc_info.value.detail)

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {
                    "keywords": "断货风险",
                    "labels": [{"access_token": "raw-token"}],
                },
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "labels[0].access_token" in str(exc_info.value.detail)


def _array_item_skill():
    skill = _mock_skill()
    skill.param_ui_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["sku"],
                    "properties": {
                        "sku": {"type": "string"},
                        "qty": {"type": "integer"},
                    },
                },
            },
        },
    }
    return skill


@pytest.mark.asyncio
async def test_submit_skill_rejects_unknown_array_item_schema_payload():
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=_array_item_skill())):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {"items": [{"sku": "A1", "qty": 1, "unexpected": "x"}]},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "items[0].unexpected" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submit_skill_validates_array_item_schema_types():
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=_array_item_skill())):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {"items": [{"sku": "A1", "qty": "1"}]},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "items[0].qty" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submit_skill_accepts_array_item_schema_payload():
    from app.portal.service import submit_portal_skill

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=_array_item_skill())), \
         patch("app.portal.service.portal_ui_service.get_submission_ui_context", new=AsyncMock(return_value={
             "ui_pref_id": None,
             "ui_pref_version": None,
             "ui_surface": "run_form",
             "base_skill_commit": None,
             "merged_ui_schema_hash": "sha256:test",
             "ui_snapshot_json": {"merged_ui_schema_hash": "sha256:test"},
         })), \
         patch("app.portal.service.execution_service.execute_skill", new=AsyncMock(return_value={"run_id": "exec-1", "status": "completed"})) as mock_execute, \
         patch("app.portal.service._cache_delete_pattern_safe", new=AsyncMock()):
        result = await submit_portal_skill(
            db,
            "skill-form",
            _mock_user(),
            {"items": [{"sku": "A1", "qty": 1}]},
        )

    assert result["status"] == "completed"
    actual_params = mock_execute.await_args.kwargs["run_metadata"]["ui"]["actual_params"]
    assert actual_params == {"items": [{"sku": "A1", "qty": 1}]}


@pytest.mark.asyncio
async def test_submit_skill_rejects_array_item_when_properties_empty():
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    skill = _mock_skill()
    skill.param_ui_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
    }

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {"items": [{"unexpected": "x"}]},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "items[0].unexpected" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submit_skill_rejects_ui_snapshot_after_actual_params_exceeds_budget(monkeypatch):
    from app.portal import ui_service as portal_ui_service
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    monkeypatch.setattr(portal_ui_service, "MAX_UI_SNAPSHOT_BYTES", 180)
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=_mock_skill())), \
         patch("app.portal.service.portal_ui_service.get_submission_ui_context", new=AsyncMock(return_value={
             "ui_pref_id": None,
             "ui_pref_version": None,
             "ui_surface": "run_form",
             "base_skill_commit": None,
             "merged_ui_schema_hash": "sha256:test",
             "ui_snapshot_json": {"merged_ui_schema_hash": "sha256:test"},
         })), \
         patch("app.portal.service.execution_service.execute_skill", new=AsyncMock()) as mock_execute:
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                db,
                "skill-form",
                _mock_user(),
                {"keywords": "x" * 220},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "ui snapshot" in str(exc_info.value.detail)
    assert db.add.call_count == 0
    assert mock_execute.await_count == 0


@pytest.mark.asyncio
async def test_submit_skill_rejects_malformed_schema_properties_without_500():
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    skill = _mock_skill()
    skill.param_ui_schema = {
        "type": "object",
        "properties": [],
    }

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {"keywords": "断货风险"},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "未知参数 keywords" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_submit_skill_rejects_malformed_nested_properties_without_500():
    from app.portal.service import submit_portal_skill
    from app.common.exceptions import AppError

    skill = _mock_skill()
    skill.param_ui_schema = {
        "type": "object",
        "properties": {
            "filters": {
                "type": "object",
                "properties": [],
            },
        },
    }

    with patch("app.portal.service.require_skill_access", new=AsyncMock(return_value=skill)):
        with pytest.raises(AppError) as exc_info:
            await submit_portal_skill(
                AsyncMock(),
                "skill-form",
                _mock_user(),
                {"filters": {"platform": "直通车"}},
            )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "filters.platform" in str(exc_info.value.detail)
