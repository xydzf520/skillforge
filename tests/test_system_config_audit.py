"""系统配置审计测试"""

import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete, select


def _admin_user():
    user = MagicMock()
    user.id = "admin"
    return user


@pytest.mark.asyncio
async def test_system_config_update_redacts_secret_audit(client):
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.main import upsert_system_config

    async with async_session_factory() as session:
        resp = await upsert_system_config(
            "connector.api_key",
            {"value": "super-secret-api-key"},
            db=session,
            current_user=_admin_user(),
        )

    assert resp["message"] == "保存成功"
    async with async_session_factory() as session:
        row = (await session.execute(
            select(AuditLog).where(AuditLog.action == "system_config.update.connector.api_key")
        )).scalar_one()

    detail_text = json.dumps(row.detail, ensure_ascii=False)
    assert row.target_type == "system_config"
    assert row.target_id == "connector.api_key"
    assert row.detail["new_value"]["configured"] is True
    assert row.detail["new_value"]["hash_prefix"].startswith("sha256:")
    assert "super-secret-api-key" not in detail_text


@pytest.mark.asyncio
async def test_system_config_update_redacts_app_key_audit(client):
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.main import upsert_system_config

    async with async_session_factory() as session:
        await upsert_system_config(
            "yuyidata.app_key",
            {"value": "sp-test-secret"},
            db=session,
            current_user=_admin_user(),
        )

    async with async_session_factory() as session:
        row = (await session.execute(
            select(AuditLog).where(AuditLog.action == "system_config.update.yuyidata.app_key")
        )).scalar_one()

    detail_text = json.dumps(row.detail, ensure_ascii=False)
    assert row.detail["new_value"]["configured"] is True
    assert "sp-test-secret" not in detail_text


@pytest.mark.asyncio
async def test_system_config_list_masks_secret_values(client):
    from app.common.models import SystemConfig
    from app.database import async_session_factory
    from app.main import list_system_config

    async with async_session_factory() as session:
        await session.execute(delete(SystemConfig).where(SystemConfig.key.in_(["ai.api_key", "ai.model"])))
        session.add(SystemConfig(key="ai.api_key", value="sk-secret-value", updated_by="admin"))
        session.add(SystemConfig(key="ai.model", value="deepseek-chat", updated_by="admin"))
        await session.commit()

    async with async_session_factory() as session:
        rows = await list_system_config(db=session, current_user=_admin_user())

    by_key = {row["key"]: row for row in rows}
    assert by_key["ai.api_key"]["value"] == ""
    assert by_key["ai.api_key"]["secret_configured"] is True
    assert by_key["ai.model"]["value"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_required_ai_config_does_not_fallback_to_env(client, monkeypatch):
    from app.common.ai import LLMAuthError, get_ai_config, invalidate_ai_config_cache
    from app.common.models import SystemConfig
    from app.config import settings
    from app.database import async_session_factory

    monkeypatch.setattr(settings, "AI_API_BASE", "https://env-only.example")
    monkeypatch.setattr(settings, "AI_API_KEY", "sk-env-should-not-be-used")
    monkeypatch.setattr(settings, "AI_DEFAULT_MODEL", "env-model")
    invalidate_ai_config_cache()

    async with async_session_factory() as session:
        await session.execute(delete(SystemConfig).where(SystemConfig.key.like("ai.%")))
        await session.commit()

    with pytest.raises(LLMAuthError):
        await get_ai_config(require_system_config=True)

    invalidate_ai_config_cache()


@pytest.mark.asyncio
async def test_required_ai_config_requires_backend_key(client, monkeypatch):
    from app.common.ai import LLMAuthError, get_ai_config, invalidate_ai_config_cache
    from app.common.models import SystemConfig
    from app.config import settings
    from app.database import async_session_factory

    monkeypatch.setattr(settings, "AI_API_BASE", "https://env.example")
    monkeypatch.setattr(settings, "AI_API_KEY", "sk-env-should-not-be-used")
    monkeypatch.setattr(settings, "AI_DEFAULT_MODEL", "env-model")
    invalidate_ai_config_cache()

    async with async_session_factory() as session:
        await session.execute(delete(SystemConfig).where(SystemConfig.key.like("ai.%")))
        session.add(SystemConfig(key="ai.api_base", value="https://backend.example", updated_by="admin"))
        session.add(SystemConfig(key="ai.model", value="backend-model", updated_by="admin"))
        await session.commit()

    with pytest.raises(LLMAuthError):
        await get_ai_config(require_system_config=True)

    invalidate_ai_config_cache()


@pytest.mark.asyncio
async def test_required_ai_config_uses_backend_configured_key(client, monkeypatch):
    from app.common.ai import get_ai_config, invalidate_ai_config_cache
    from app.common.models import SystemConfig
    from app.config import settings
    from app.database import async_session_factory

    monkeypatch.setattr(settings, "AI_API_BASE", "https://env.example")
    monkeypatch.setattr(settings, "AI_API_KEY", "sk-env-should-not-be-used")
    monkeypatch.setattr(settings, "AI_DEFAULT_MODEL", "env-model")
    invalidate_ai_config_cache()

    async with async_session_factory() as session:
        await session.execute(delete(SystemConfig).where(SystemConfig.key.like("ai.%")))
        session.add(SystemConfig(key="ai.api_base", value="https://backend.example", updated_by="admin"))
        session.add(SystemConfig(key="ai.api_key", value="sk-backend-only", updated_by="admin"))
        session.add(SystemConfig(key="ai.model", value="backend-model", updated_by="admin"))
        await session.commit()

    config = await get_ai_config(require_system_config=True)

    assert config["ai.api_base"] == "https://backend.example"
    assert config["ai.api_key"] == "sk-backend-only"
    assert config["ai.model"] == "backend-model"
    invalidate_ai_config_cache()


@pytest.mark.asyncio
async def test_siliconflow_ai_provider_fills_backend_defaults(client, monkeypatch):
    from app.common.ai import get_ai_profile_config, invalidate_ai_config_cache
    from app.common.models import SystemConfig
    from app.config import settings
    from app.database import async_session_factory

    monkeypatch.setattr(settings, "AI_API_BASE", "https://env-should-not-be-used.example")
    monkeypatch.setattr(settings, "AI_API_KEY", "sk-env-should-not-be-used")
    monkeypatch.setattr(settings, "AI_DEFAULT_MODEL", "env-model")
    invalidate_ai_config_cache()

    async with async_session_factory() as session:
        await session.execute(delete(SystemConfig).where(SystemConfig.key.like("ai.%")))
        session.add(SystemConfig(key="ai.provider", value="siliconflow", updated_by="admin"))
        session.add(SystemConfig(key="ai.api_key", value="sk-siliconflow", updated_by="admin"))
        await session.commit()

    config = await get_ai_profile_config(model_profile="cheap", require_system_config=True)

    assert config["ai.api_base"] == "https://api.siliconflow.cn/v1"
    assert config["ai.api_key"] == "sk-siliconflow"
    assert config["ai.model"] == "Qwen/Qwen3-8B"
    invalidate_ai_config_cache()


@pytest.mark.asyncio
async def test_required_ai_profile_config_allows_cheap_only_backend_config(client, monkeypatch):
    from app.common.ai import LLMAuthError, get_ai_config, get_ai_profile_config, invalidate_ai_config_cache
    from app.common.models import SystemConfig
    from app.config import settings
    from app.database import async_session_factory

    monkeypatch.setattr(settings, "AI_API_BASE", "https://env.example")
    monkeypatch.setattr(settings, "AI_API_KEY", "sk-env-should-not-be-used")
    monkeypatch.setattr(settings, "AI_DEFAULT_MODEL", "env-model")
    invalidate_ai_config_cache()

    async with async_session_factory() as session:
        await session.execute(delete(SystemConfig).where(SystemConfig.key.like("ai.%")))
        session.add(SystemConfig(key="ai.cheap.api_base", value="https://cheap-backend.example", updated_by="admin"))
        session.add(SystemConfig(key="ai.cheap.api_key", value="sk-cheap-backend", updated_by="admin"))
        session.add(SystemConfig(key="ai.cheap.model", value="cheap-backend-model", updated_by="admin"))
        await session.commit()

    with pytest.raises(LLMAuthError):
        await get_ai_config(require_system_config=True)

    config = await get_ai_profile_config(model_profile="cheap", require_system_config=True)

    assert config["ai.api_base"] == "https://cheap-backend.example"
    assert config["ai.api_key"] == "sk-cheap-backend"
    assert config["ai.model"] == "cheap-backend-model"
    invalidate_ai_config_cache()


@pytest.mark.asyncio
async def test_startup_ai_warning_uses_backend_system_config_when_complete(client):
    from app.bootstrap.health_checks import _normalize_operational_warnings
    from app.common.models import SystemConfig
    from app.database import async_session_factory

    async with async_session_factory() as session:
        await session.execute(delete(SystemConfig).where(SystemConfig.key.like("ai.%")))
        session.add(SystemConfig(key="ai.api_base", value="https://backend.example", updated_by="admin"))
        session.add(SystemConfig(key="ai.api_key", value="sk-backend-only", updated_by="admin"))
        session.add(SystemConfig(key="ai.model", value="backend-model", updated_by="admin"))
        await session.commit()

    warnings = await _normalize_operational_warnings(["AI_API_KEY 未设置 — .env 仅作为本地兜底"])

    assert warnings == []


@pytest.mark.asyncio
async def test_startup_ai_warning_reports_missing_backend_system_config(client):
    from app.bootstrap.health_checks import _normalize_operational_warnings
    from app.common.models import SystemConfig
    from app.database import async_session_factory

    async with async_session_factory() as session:
        await session.execute(delete(SystemConfig).where(SystemConfig.key.like("ai.%")))
        session.add(SystemConfig(key="ai.api_base", value="https://backend.example", updated_by="admin"))
        session.add(SystemConfig(key="ai.model", value="backend-model", updated_by="admin"))
        await session.commit()

    warnings = await _normalize_operational_warnings(["AI_API_KEY 未设置 — .env 仅作为本地兜底"])

    assert not any(w.startswith("AI_API_KEY") for w in warnings)
    assert any("system_config" in w and "ai.api_key" in w for w in warnings)


@pytest.mark.asyncio
async def test_system_config_delete_writes_audit_with_long_action(client):
    from app.common.audit import AuditLog
    from app.database import async_session_factory
    from app.main import delete_system_config, upsert_system_config

    key = "connector_keys.legacy_grace_until"
    async with async_session_factory() as session:
        await upsert_system_config(
            key,
            {"value": "2026-05-12T12:00:00+08:00"},
            db=session,
            current_user=_admin_user(),
        )
        resp = await delete_system_config(key, db=session, current_user=_admin_user())

    assert resp["message"] == "已删除"
    async with async_session_factory() as session:
        actions = (await session.execute(
            select(AuditLog.action).where(AuditLog.target_id == key).order_by(AuditLog.id)
        )).scalars().all()

    assert actions == [
        "system_config.update.connector_keys.legacy_grace_until",
        "system_config.delete.connector_keys.legacy_grace_until",
    ]
