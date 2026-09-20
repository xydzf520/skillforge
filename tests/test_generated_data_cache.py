from __future__ import annotations

import pytest

from app.config import settings
from app.common import cache as cache_mod
import importlib

inbox_router = importlib.import_module("app.inbox.router")
todos_router = importlib.import_module("app.todos.router")


@pytest.mark.asyncio
async def test_generated_data_cache_invalidation_clears_all_generated_namespaces(monkeypatch):
    patterns: list[str] = []

    async def fake_delete_pattern(pattern: str) -> int:
        patterns.append(pattern)
        return 1

    monkeypatch.setattr(cache_mod, "cache_delete_pattern", fake_delete_pattern)

    deleted = await cache_mod.invalidate_generated_data_cache()

    assert deleted == 7
    assert patterns == [
        "inbox:reports:*",
        "inbox:overview:*",
        "todos:*",
        "execution:decisions:*",
        "sf:overview:*",
        "sf:trace:*",
        "learning:*",
    ]


def test_generated_data_routes_use_six_hour_cache_ttl():
    assert settings.CACHE_TTL_GENERATED_DATA == 6 * 60 * 60
    assert inbox_router.list_reports._cache_ttl == settings.CACHE_TTL_GENERATED_DATA
    assert inbox_router.overview._cache_ttl == settings.CACHE_TTL_GENERATED_DATA
    assert inbox_router.get_report_detail._cache_ttl == settings.CACHE_TTL_GENERATED_DATA
    assert todos_router.list_todos._cache_ttl == settings.CACHE_TTL_GENERATED_DATA
    assert todos_router.get_todo_stats._cache_ttl == settings.CACHE_TTL_GENERATED_DATA
    assert todos_router.get_todo_detail._cache_ttl == settings.CACHE_TTL_GENERATED_DATA
    assert todos_router.get_todo_detail._cache_max_bytes is None
    assert callable(todos_router.get_todo_detail._cache_skip_cache_if)
    assert todos_router.bulk_todo_summary._cache_ttl == settings.CACHE_TTL_GENERATED_DATA
