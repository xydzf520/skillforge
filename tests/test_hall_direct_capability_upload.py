from types import SimpleNamespace

import pytest

from app.hall import direct_capability_service


def test_safe_upload_name_preserves_extension_for_unicode_filename():
    assert direct_capability_service._safe_upload_name("中文商品图.png") == "reference.png"
    assert direct_capability_service._safe_upload_name("产品-01.webp") == "01.webp"
    assert direct_capability_service._safe_upload_name(None) == "reference.png"


def test_direct_capability_access_respects_metadata_scope():
    item = {
        "status": "active",
        "visibility": "department",
        "department": "AI",
        "owner": "owner-1",
    }
    ai_user = SimpleNamespace(id="u-ai", role="aibp", department="AI", can_view_all=False)
    observer_user = SimpleNamespace(id="u-observer", role="observer", department="AI", can_view_all=False)
    other_user = SimpleNamespace(id="u-ops", role="observer", department="OPS", can_view_all=False)
    read_all_user = SimpleNamespace(id="u-read", role="observer", department="OPS", can_view_all=True)

    assert direct_capability_service._direct_capability_allowed(item, ai_user, "execute") is True
    assert direct_capability_service._direct_capability_allowed(item, observer_user, "execute") is False
    assert direct_capability_service._direct_capability_allowed(item, other_user, "read") is False
    assert direct_capability_service._direct_capability_allowed(item, read_all_user, "read") is True
    assert direct_capability_service._direct_capability_allowed(item, read_all_user, "execute") is False


def test_public_imagegen_capabilities_are_visible_and_executable_for_hall_users():
    observer_user = SimpleNamespace(id="u-observer", role="observer", department="OPS", can_view_all=False)

    for capability_id in ("gpt-imagegen", "gpt-imagegen-dialogue"):
        item = {
            "id": capability_id,
            "status": "active",
            "visibility": "private",
            "department": "AI",
            "owner": "owner-1",
        }

        assert direct_capability_service._direct_capability_allowed(item, observer_user, "read") is True
        assert direct_capability_service._direct_capability_allowed(item, observer_user, "execute") is True


def test_public_imagegen_capabilities_still_require_active_status_for_non_admins():
    user = SimpleNamespace(id="u-aibp", role="aibp", department="OPS", can_view_all=False)
    item = {
        "id": "gpt-imagegen",
        "status": "disabled",
        "visibility": "company",
    }

    assert direct_capability_service._direct_capability_allowed(item, user, "read") is False
    assert direct_capability_service._direct_capability_allowed(item, user, "execute") is False


def test_direct_capability_access_blocks_legacy_observer_aliases_from_execute():
    item = {"status": "active", "visibility": "company"}

    for role in ("observer", "operator", "director"):
        user = SimpleNamespace(id=f"u-{role}", role=role, department="AI", can_view_all=False)
        assert direct_capability_service._direct_capability_allowed(item, user, "read") is True
        assert direct_capability_service._direct_capability_allowed(item, user, "execute") is False


def test_direct_capability_list_includes_execute_permission(monkeypatch):
    item = {
        "id": "dept-cap",
        "display_name": "Dept Cap",
        "category": "测试",
        "status": "active",
        "visibility": "department",
        "department": "AI",
    }
    monkeypatch.setattr(direct_capability_service, "_iter_direct_capabilities", lambda: [dict(item)])
    read_all_user = SimpleNamespace(id="u-read", role="aibp", department="OPS", can_view_all=True)

    result = direct_capability_service.list_direct_capabilities(current_user=read_all_user)

    assert result["total"] == 1
    assert result["items"][0]["permissions"] == {"read": True, "execute": False}


def _ui_capability_fixture(tmp_path, capability_id: str = "gpt-imagegen-ui"):
    skill_dir = tmp_path / capability_id
    skill_dir.mkdir()
    meta = {
        "display_name": "GPT ImageGen",
        "visibility": "company",
        "status": "active",
        "department": "AI",
    }
    contract = {
        "id": capability_id,
        "name": "GPT ImageGen",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "title": "提示词"},
                "size": {"type": "string", "title": "画幅比例", "default": "1:1"},
            },
        },
        "result_ui_schema": {"type": "text"},
    }
    return skill_dir, meta, contract


def test_direct_capability_summary_exposes_project_design_split(tmp_path):
    skill_dir, meta, contract = _ui_capability_fixture(tmp_path, "gpt-imagegen-project-split")

    summary = direct_capability_service._capability_summary(skill_dir, meta, contract)

    assert summary["project_surface"]["mode"] == "direct_skill_project_surface"
    assert summary["project_surface"]["virtual_project_id"] == "direct_gpt-imagegen-project-split"
    assert [item["key"] for item in summary["project_surface"]["segments"]] == [
        "run",
        "design",
        "history",
        "loop",
    ]


@pytest.mark.asyncio
async def test_direct_capability_ui_preview_uses_personal_overlay_flow(monkeypatch, tmp_path):
    from app.database import async_session_factory
    from app.portal import ui_service as portal_ui_service

    skill_dir, meta, contract = _ui_capability_fixture(tmp_path)
    monkeypatch.setattr(
        direct_capability_service,
        "_resolve_capability",
        lambda capability_id: (skill_dir, meta, contract),
    )

    async def fake_llm_overlay(**kwargs):
        return None, "fallback", "llm_disabled"

    monkeypatch.setattr(portal_ui_service, "_llm_instruction_overlay", fake_llm_overlay)

    async with async_session_factory() as session:
        result = await direct_capability_service.preview_direct_capability_ui(
            session,
            contract["id"],
            current_user=SimpleNamespace(id="admin-ui-preview", role="admin", department="AI", can_view_all=True),
            surface="run_form",
            instruction="把参数分组",
        )

    assert result["skill_id"] == contract["id"]
    assert result["permissions"]["customize_ui"] is True
    assert result["overlay"]["components"][0]["type"] == "form_group"
    assert result["merged_ui_schema_hash"].startswith("sha256:")


@pytest.mark.asyncio
async def test_direct_capability_ui_preference_save_and_get(monkeypatch, tmp_path):
    from app.database import async_session_factory

    skill_dir, meta, contract = _ui_capability_fixture(tmp_path, "gpt-imagegen-ui-save")
    monkeypatch.setattr(
        direct_capability_service,
        "_resolve_capability",
        lambda capability_id: (skill_dir, meta, contract),
    )
    user = SimpleNamespace(id="admin-ui-save", role="admin", department="AI", can_view_all=True)
    overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [{
            "op": "rename_title",
            "component_id": "field-prompt",
            "title": "商品描述",
        }],
    }

    async with async_session_factory() as session:
        saved = await direct_capability_service.save_direct_capability_ui_preference(
            session,
            contract["id"],
            current_user=user,
            surface="run_form",
            overlay=overlay,
            generated_by="ai",
            prompt_summary="把提示词改名",
        )
        loaded = await direct_capability_service.get_direct_capability_ui(
            session,
            contract["id"],
            current_user=user,
            surface="run_form",
        )

    assert saved["ui_pref_id"]
    assert loaded["ui_pref_version"] == 1
    assert loaded["generated_by"] == "ai"
    prompt_component = next(item for item in loaded["merged_schema"]["components"] if item["id"] == "field-prompt")
    assert prompt_component["title"] == "商品描述"


@pytest.mark.asyncio
async def test_direct_capability_ui_preference_history_and_restore(monkeypatch, tmp_path):
    from app.database import async_session_factory, engine

    await engine.dispose()

    skill_dir, meta, contract = _ui_capability_fixture(tmp_path, "gpt-imagegen-ui-history")
    monkeypatch.setattr(
        direct_capability_service,
        "_resolve_capability",
        lambda capability_id: (skill_dir, meta, contract),
    )
    user = SimpleNamespace(id="admin-ui-history", role="admin", department="AI", can_view_all=True)
    prompt_overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [{
            "op": "rename_title",
            "component_id": "field-prompt",
            "title": "商品描述",
        }],
    }
    size_overlay = {
        "schema_version": "skill-ui/v1",
        "surface": "run_form",
        "operations": [{
            "op": "rename_title",
            "component_id": "field-size",
            "title": "画幅",
        }],
    }

    async with async_session_factory() as session:
        saved_prompt = await direct_capability_service.save_direct_capability_ui_preference(
            session,
            contract["id"],
            current_user=user,
            surface="run_form",
            overlay=prompt_overlay,
            generated_by="ai",
            prompt_summary="提示词改成商品描述",
        )
        await direct_capability_service.save_direct_capability_ui_preference(
            session,
            contract["id"],
            current_user=user,
            surface="run_form",
            overlay=size_overlay,
            generated_by="manual",
            prompt_summary="画幅字段改名",
        )
        history = await direct_capability_service.list_direct_capability_ui_preferences(
            session,
            contract["id"],
            current_user=user,
            surface="run_form",
        )
        restored = await direct_capability_service.restore_direct_capability_ui_preference(
            session,
            contract["id"],
            current_user=user,
            preference_id=saved_prompt["ui_pref_id"],
            surface="run_form",
        )
        restored_history = await direct_capability_service.list_direct_capability_ui_preferences(
            session,
            contract["id"],
            current_user=user,
            surface="run_form",
        )

    assert history["total"] == 2
    assert [item["version"] for item in history["items"]] == [2, 1]
    assert history["items"][0]["enabled"] is True
    assert "overlay" not in history["items"][0]
    assert restored["ui_pref_version"] == 3
    prompt_component = next(item for item in restored["merged_schema"]["components"] if item["id"] == "field-prompt")
    assert prompt_component["title"] == "商品描述"
    assert restored_history["items"][0]["version"] == 3
    assert restored_history["items"][0]["enabled"] is True
