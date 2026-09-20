from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_namespace_cache_get_and_set(monkeypatch):
    from app.common.cache_facade import NamespaceCache

    cache = NamespaceCache("demo:ns", ttl=123)
    get_mock = AsyncMock(return_value={"ok": True})
    set_mock = AsyncMock()

    monkeypatch.setattr("app.common.cache_facade.cached_namespace_get", get_mock)
    monkeypatch.setattr("app.common.cache_facade.cached_namespace_set", set_mock)

    result = await cache.get("a", "b")
    await cache.set("a", "b", value={"ok": True})

    assert result == {"ok": True}
    get_mock.assert_awaited_once_with("demo:ns", "a:b", ttl=123)
    set_mock.assert_awaited_once_with("demo:ns", "a:b", {"ok": True}, ttl=123)


@pytest.mark.asyncio
async def test_namespace_cache_delete_and_invalidate_prefix(monkeypatch):
    from app.common.cache_facade import NamespaceCache

    cache = NamespaceCache("demo:ns")
    delete_mock = AsyncMock()
    delete_pattern_mock = AsyncMock(return_value=2)

    monkeypatch.setattr("app.common.cache_facade.cache_delete", delete_mock)
    monkeypatch.setattr("app.common.cache_facade.cache_delete_pattern", delete_pattern_mock)

    await cache.delete("x", "y")
    deleted = await cache.invalidate_prefix("skill-1")

    delete_mock.assert_awaited_once_with("demo:ns:x:y")
    delete_pattern_mock.assert_awaited_once_with("demo:ns:skill-1*")
    assert deleted == 2
