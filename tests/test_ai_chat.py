"""Focused regression tests for the durable Hall AI Chat runtime."""

from __future__ import annotations

import base64
import io
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers


@pytest_asyncio.fixture
async def db(client):
    """Reuse the isolated PostgreSQL schema created by the shared client fixture."""
    del client
    import app.database as db_mod

    async with db_mod.async_session_factory() as session:
        yield session


def test_ai_chat_is_a_public_direct_capability_for_observers():
    from app.hall import direct_capability_service

    observer = SimpleNamespace(
        id="observer-1",
        role="observer",
        department="运营组",
        can_view_all=False,
    )
    item = {
        "id": "ai-chat",
        "status": "active",
        "visibility": "company",
        "department": "平台",
    }

    assert direct_capability_service._direct_capability_allowed(item, observer, action="read") is True
    assert direct_capability_service._direct_capability_allowed(item, observer, action="execute") is True


@pytest.mark.asyncio
async def test_threads_messages_archive_and_idempotency_are_user_isolated(db, monkeypatch):
    from app.hall import ai_chat_service as service

    monkeypatch.setenv("FASTAITOKEN_DOMESTIC_API_KEY", "test-only-key")
    one = await service.create_thread(db, user_id="user-one", title="项目复盘")
    two = await service.create_thread(db, user_id="user-two", title="另一个人的对话")
    await db.commit()

    listed_one = await service.list_threads(db, user_id="user-one")
    assert [row["id"] for row in listed_one["items"]] == [one["id"]]
    assert two["id"] not in {row["id"] for row in listed_one["items"]}

    with pytest.raises(HTTPException) as denied:
        await service.get_owned_thread(db, user_id="user-two", thread_id=one["id"])
    assert denied.value.status_code == 404

    first_user, first_assistant, duplicate = await service.prepare_turn(
        db,
        user_id="user-one",
        thread_id=one["id"],
        content="把今天的项目结论整理成三点",
        model="deepseek-v4-pro",
        attachment_ids=[],
        idempotency_key="idem-user-one-0001",
    )
    second_user, second_assistant, duplicate_again = await service.prepare_turn(
        db,
        user_id="user-one",
        thread_id=one["id"],
        content="这次重试不应生成第二轮",
        model="deepseek-v4-pro",
        attachment_ids=[],
        idempotency_key="idem-user-one-0001",
    )
    assert duplicate is False
    assert duplicate_again is True
    assert second_user.id == first_user.id
    assert second_assistant.id == first_assistant.id

    messages = await service.list_messages(db, user_id="user-one", thread_id=one["id"])
    assert len(messages["items"]) == 2
    assert [item["role"] for item in messages["items"]] == ["user", "assistant"]

    archived = await service.update_thread(db, user_id="user-one", thread_id=one["id"], archived=True)
    await db.commit()
    assert archived["archived"] is True
    assert (await service.list_threads(db, user_id="user-one"))["items"] == []
    assert [row["id"] for row in (await service.list_threads(db, user_id="user-one", archived=True))["items"]] == [one["id"]]

    restored = await service.update_thread(db, user_id="user-one", thread_id=one["id"], archived=False)
    assert restored["archived"] is False

    renamed = await service.update_thread(
        db,
        user_id="user-one",
        thread_id=one["id"],
        title="八月投流复盘",
    )
    assert renamed["title"] == "八月投流复盘"


@pytest.mark.asyncio
async def test_preference_and_permanent_thread_delete_are_user_isolated(db, monkeypatch):
    from app.hall import ai_chat_service as service

    monkeypatch.setenv("FASTAITOKEN_DOMESTIC_API_KEY", "test-only-key")
    created = await service.create_thread(db, user_id="delete-owner", title="待删除", model="glm-5.2")
    await db.commit()
    assert (await service.get_user_preference(db, user_id="delete-owner"))["last_model"] == "glm-5.2"

    await service.update_user_preference(db, user_id="delete-owner", model="kimi-k3")
    await db.commit()
    assert (await service.get_user_preference(db, user_id="delete-owner"))["last_model"] == "kimi-k3"

    with pytest.raises(HTTPException) as denied:
        await service.delete_thread(db, user_id="another-user", thread_id=created["id"])
    assert denied.value.status_code == 404

    deleted = await service.delete_thread(db, user_id="delete-owner", thread_id=created["id"])
    assert deleted == {"deleted": True, "id": created["id"]}
    with pytest.raises(HTTPException) as missing:
        await service.get_owned_thread(db, user_id="delete-owner", thread_id=created["id"])
    assert missing.value.status_code == 404


def test_context_keeps_complete_recent_turns_and_latest_regeneration():
    from app.hall import ai_chat_service as service

    started = datetime(2026, 8, 17, 9, 0, 0)
    rows = [
        SimpleNamespace(id="u1", turn_id="t1", role="user", status="completed", content="第一问", created_at=started),
        SimpleNamespace(id="a1", turn_id="t1", role="assistant", status="completed", content="旧回答", created_at=started + timedelta(seconds=1)),
        SimpleNamespace(id="a2", turn_id="t1", role="assistant", status="completed", content="新回答", created_at=started + timedelta(seconds=2)),
        SimpleNamespace(id="u2", turn_id="t2", role="user", status="completed", content="最新问题", created_at=started + timedelta(seconds=3)),
    ]

    selected = service._coherent_recent_context(rows)
    assert [(row.role, row.content) for row in selected] == [
        ("user", "第一问"),
        ("assistant", "新回答"),
        ("user", "最新问题"),
    ]


def test_context_budget_is_model_aware_and_keeps_the_latest_complete_turn():
    from app.hall import ai_chat_service as service

    started = datetime(2026, 8, 17, 10, 0, 0)
    rows = [
        SimpleNamespace(id="u1", turn_id="t1", role="user", status="completed", content="旧" * 80, created_at=started),
        SimpleNamespace(id="a1", turn_id="t1", role="assistant", status="completed", content="答" * 80, created_at=started + timedelta(seconds=1)),
        SimpleNamespace(id="u2", turn_id="t2", role="user", status="completed", content="新的问题", created_at=started + timedelta(seconds=2)),
        SimpleNamespace(id="a2", turn_id="t2", role="assistant", status="completed", content="新的回答", created_at=started + timedelta(seconds=3)),
    ]

    selected = service._coherent_recent_context(rows, max_tokens=20)
    assert [(row.role, row.content) for row in selected] == [
        ("user", "新的问题"),
        ("assistant", "新的回答"),
    ]


def test_every_allowlisted_model_has_safe_versioned_limits():
    from app.hall import ai_chat_service as service

    models = [model for group in service.MODEL_GROUPS for model in group["models"]]
    assert set(models) == set(service.MODEL_PROFILES)
    assert "claude-haiku-4-6" not in models
    assert "claude-haiku-4-5-20251001" in models
    assert "claude-sonnet-4-5-20250929" in models
    for model in models:
        profile = service._model_profile(model)
        assert profile["chat_context_tokens"] < profile["context_window_tokens"]
        assert 16_384 <= profile["chat_max_output_tokens"] <= 128_000
        assert profile["chat_max_output_tokens"] <= profile["max_output_tokens"]
        assert profile["chat_context_tokens"] + profile["chat_max_output_tokens"] < profile["context_window_tokens"]
        assert profile["limit_source_url"].startswith("https://")
        assert profile["limit_note"]

    assert service.MODEL_PROFILES["deepseek-v4-pro"]["context_window_tokens"] == 1_000_000
    assert service.MODEL_PROFILES["deepseek-v4-pro"]["max_output_tokens"] == 384_000
    assert service.MODEL_PROFILES["kimi-k3"]["max_output_tokens"] == 1_048_576
    assert service.MODEL_PROFILES["claude-sonnet-4-5-20250929"]["context_window_tokens"] == 200_000
    assert service.MODEL_PROFILES["claude-sonnet-4-5-20250929"]["max_output_tokens"] == 64_000
    for model in service.GPT_REASONING_EFFORTS:
        assert service.MODEL_PROFILES[model]["chat_context_tokens"] == 256_000
        assert service.MODEL_PROFILES[model]["chat_max_output_tokens"] == 128_000
        assert service.MODEL_REASONING_OPTIONS[model][0] == "auto"


@pytest.mark.asyncio
async def test_attachment_uses_signature_thumbnail_and_owner_boundary(db, tmp_path, monkeypatch):
    from app.hall import ai_chat_service as service

    monkeypatch.setattr(service, "ATTACHMENT_ROOT", tmp_path / "chat-assets")
    # 1x1 opaque PNG. The bytes, not the filename, decide the accepted MIME.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    upload = UploadFile(
        filename="evidence.png",
        file=io.BytesIO(png),
        headers=Headers({"content-type": "image/png"}),
    )
    payload = await service.save_attachment(db, user_id="owner-one", file=upload)
    await db.commit()

    assert payload["mime_type"] == "image/png"
    assert payload["thumbnail_url"].endswith("?thumbnail=true")
    original, _, original_mime = await service.attachment_path(
        db, user_id="owner-one", attachment_id=payload["id"]
    )
    thumbnail, _, thumbnail_mime = await service.attachment_path(
        db, user_id="owner-one", attachment_id=payload["id"], thumbnail=True
    )
    assert original.is_file() and thumbnail.is_file()
    assert original_mime == "image/png"
    assert thumbnail_mime == "image/jpeg"

    with pytest.raises(HTTPException) as denied:
        await service.attachment_path(db, user_id="owner-two", attachment_id=payload["id"])
    assert denied.value.status_code == 404

    mismatched = UploadFile(
        filename="wrong.jpg",
        file=io.BytesIO(png),
        headers=Headers({"content-type": "image/jpeg"}),
    )
    with pytest.raises(HTTPException) as invalid:
        await service.save_attachment(db, user_id="owner-one", file=mismatched)
    assert invalid.value.status_code == 400


@pytest.mark.asyncio
async def test_stream_persists_result_and_replays_idempotent_request(client, monkeypatch):
    from app.hall import ai_chat_service as service

    monkeypatch.setenv("FASTAITOKEN_DOMESTIC_API_KEY", "test-only-key")

    async def fake_stream(model, messages, *, reasoning_effort="auto"):
        assert model == "deepseek-v4-pro"
        assert reasoning_effort == "high"
        assert messages[-1]["content"] == "给我一句确认回复"
        yield {"type": "delta", "text": "已确认", "degraded": False}
        yield {"type": "usage", "usage": {"prompt_tokens": 8, "completion_tokens": 2}, "degraded": False}

    monkeypatch.setattr(service, "_upstream_stream", fake_stream)
    created = (await client.post("/api/hall/ai-chat/threads", json={"model": "deepseek-v4-pro"})).json()
    body = {
        "content": "给我一句确认回复",
        "model": "deepseek-v4-pro",
        "reasoning_effort": "high",
        "attachment_ids": [],
        "idempotency_key": "http-idem-admin-0001",
    }

    first = await client.post(f"/api/hall/ai-chat/threads/{created['id']}/messages/stream", json=body)
    assert first.status_code == 200
    assert "event: message.completed" in first.text
    assert "已确认" in first.text

    replay = await client.post(f"/api/hall/ai-chat/threads/{created['id']}/messages/stream", json=body)
    assert replay.status_code == 200
    assert '"duplicate": true' in replay.text
    assert "已确认" in replay.text

    history = (await client.get(f"/api/hall/ai-chat/threads/{created['id']}/messages")).json()
    assert len(history["items"]) == 2
    assert history["items"][-1]["content"] == "已确认"
    assert history["items"][-1]["usage"]["completion_tokens"] == 2


@pytest.mark.asyncio
async def test_stream_admission_rejection_does_not_leave_thinking_message(client, monkeypatch):
    from app.hall import ai_chat_service as service

    monkeypatch.setenv("FASTAITOKEN_DOMESTIC_API_KEY", "test-only-key")

    async def reject_stream(*args, **kwargs):
        del args, kwargs
        raise HTTPException(status_code=429, detail="每位用户最多同时生成 2 条回复")

    monkeypatch.setattr(service, "acquire_stream_lease", reject_stream)
    created = (await client.post("/api/hall/ai-chat/threads", json={"model": "deepseek-v4-pro"})).json()
    response = await client.post(
        f"/api/hall/ai-chat/threads/{created['id']}/messages/stream",
        json={
            "content": "这条请求应被并发门禁拒绝",
            "model": "deepseek-v4-pro",
            "reasoning_effort": "none",
            "attachment_ids": [],
            "idempotency_key": "http-admission-reject-0001",
        },
    )

    assert response.status_code == 429
    history = (await client.get(f"/api/hall/ai-chat/threads/{created['id']}/messages")).json()
    assert [item["status"] for item in history["items"]] == ["completed", "failed"]
    assert history["items"][-1]["error_code"] == "concurrency_limit"
    assert "正在" not in (history["items"][-1]["error_message"] or "")


@pytest.mark.asyncio
async def test_history_reconciles_expired_stream_without_losing_partial_content(db, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.hall import ai_chat_service as service

    monkeypatch.setenv("FASTAITOKEN_DOMESTIC_API_KEY", "test-only-key")
    created = await service.create_thread(db, user_id="stale-stream-owner", title="断线恢复")
    _, assistant, _ = await service.prepare_turn(
        db,
        user_id="stale-stream-owner",
        thread_id=created["id"],
        content="请生成一份汇报",
        model="deepseek-v4-pro",
        attachment_ids=[],
        idempotency_key="stale-stream-owner-0001",
    )
    assistant.content = "已经生成的部分内容"
    assistant.updated_at = now_bjt() - timedelta(seconds=service.STREAM_STALE_AFTER_SECONDS + 1)
    await db.commit()

    history = await service.list_messages(
        db,
        user_id="stale-stream-owner",
        thread_id=created["id"],
    )

    persisted = history["items"][-1]
    assert persisted["status"] == "interrupted"
    assert persisted["content"] == "已经生成的部分内容"
    assert persisted["error_code"] == "stale_stream"


def test_model_allowlist_and_static_vision_routing_require_configured_provider(monkeypatch):
    from app.hall import ai_chat_service as service

    with pytest.raises(HTTPException) as invalid:
        service._provider_group("https://attacker.example/v1")
    assert invalid.value.status_code == 400

    for group in service.MODEL_GROUPS:
        monkeypatch.delenv(group["key_env"], raising=False)
    monkeypatch.setenv("FASTAITOKEN_VISION_MODEL", "gpt-5.4-mini")
    assert "deepseek-v4-pro" not in service._supported_vision_models()
    assert "glm-5.2" not in service._supported_vision_models()
    assert "kimi-k3" in service._supported_vision_models()
    assert "gpt-5.4-mini" in service._supported_vision_models()
    assert service._selected_vision_model() is None

    monkeypatch.setenv("FASTAITOKEN_GPT_PRO_API_KEY", "test-only-key")
    assert service._selected_vision_model() == "gpt-5.4-mini"

    monkeypatch.setenv("FASTAITOKEN_VISION_MODEL", "gpt-5.4")
    assert service._selected_vision_model() == "gpt-5.4"

    claude_payload = {"model": "claude-opus-4-8", "temperature": 0.6}
    service._apply_model_options(claude_payload, "claude-opus-4-8")
    assert "temperature" not in claude_payload

    deepseek_payload = {"model": "deepseek-v4-pro", "temperature": 0.6}
    service._apply_model_options(deepseek_payload, "deepseek-v4-pro")
    assert "temperature" not in deepseek_payload
    assert "thinking" not in deepseek_payload

    deepseek_disabled = {"model": "deepseek-v4-pro"}
    service._apply_model_options(deepseek_disabled, "deepseek-v4-pro", "none")
    assert deepseek_disabled["thinking"] == {"type": "disabled"}

    deepseek_enabled = {"model": "deepseek-v4-pro"}
    service._apply_model_options(deepseek_enabled, "deepseek-v4-pro", "high")
    assert deepseek_enabled["thinking"] == {"type": "enabled"}

    gpt_payload = {"model": "gpt-5.6-sol", "max_tokens": 128_000, "temperature": 0.6}
    service._apply_model_options(gpt_payload, "gpt-5.6-sol", "max")
    assert gpt_payload["max_completion_tokens"] == 128_000
    assert gpt_payload["reasoning_effort"] == "max"
    assert "max_tokens" not in gpt_payload
    assert "temperature" not in gpt_payload

    claude_45_payload = {"model": "claude-haiku-4-5-20251001"}
    service._apply_model_options(claude_45_payload, "claude-haiku-4-5-20251001", "medium")
    assert claude_45_payload["thinking"] == {"type": "enabled", "budget_tokens": 4096}

    with pytest.raises(HTTPException) as unsupported_reasoning:
        service._apply_model_options({"model": "gpt-5.4"}, "gpt-5.4", "max")
    assert unsupported_reasoning.value.status_code == 400


@pytest.mark.asyncio
async def test_model_catalog_is_built_in_and_does_not_probe_upstream(monkeypatch):
    from app.hall import ai_chat_service as service

    for group in service.MODEL_GROUPS:
        monkeypatch.setenv(group["key_env"], "test-only-key")

    def fail_if_network_client_is_created(*args, **kwargs):
        del args, kwargs
        raise AssertionError("model catalog must not call an upstream API")

    monkeypatch.setattr(service.httpx, "AsyncClient", fail_if_network_client_is_created)

    catalog = await service.get_model_catalog(refresh=True)
    models = {
        item["id"]: item
        for group in catalog["groups"]
        for item in group["models"]
    }
    assert models["deepseek-v4-pro"]["available"] is True
    assert models["deepseek-v4-pro"]["latency_ms"] is None
    assert models["deepseek-v4-pro"]["supports_vision"] is False
    assert models["deepseek-v4-pro"]["vision_label"] == "仅文本"
    assert models["deepseek-v4-pro"]["context_window_tokens"] == 1_000_000
    assert models["deepseek-v4-pro"]["chat_context_tokens"] == 800_000
    assert models["deepseek-v4-pro"]["chat_max_output_tokens"] == 32_768
    assert models["deepseek-v4-pro"]["limit_policy_version"] == "ai-chat-context-v3"
    assert models["deepseek-v4-pro"]["reasoning_options"] == ["auto", "none", "high"]
    assert models["gpt-5.6-sol"]["reasoning_options"] == ["auto", "none", "low", "medium", "high", "xhigh", "max"]
    assert models["gpt-5.4-mini"]["chat_context_tokens"] == 256_000
    assert models["gpt-5.4-mini"]["chat_max_output_tokens"] == 128_000
    assert models["gpt-5.4-mini"]["available"] is True
    assert models["gpt-5.4-mini"]["status"] == "available"
    assert models["gpt-5.4-mini"]["supports_vision"] is True
    assert models["kimi-k3"]["supports_vision"] is True
    assert models["claude-opus-4-8"]["supports_vision"] is True
    assert catalog["vision"]["ready"] is True
    assert catalog["vision"]["model"] == "gpt-5.4"
    assert catalog["catalog_source"] == "built_in_vendor_metadata"
    assert catalog["capability_policy_version"] == "ai-chat-model-capabilities-v1"
