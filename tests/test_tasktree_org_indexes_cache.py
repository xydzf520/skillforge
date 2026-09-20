"""tasktree _load_org_indexes 进程级 60s TTL 缓存测试（H13）。

回归：每次 get_tree/get_stats/_ensure_*_access 都会调 _load_org_indexes，
原本会全表扫 org_units WHERE type='department'。包了一层 60s 进程级 cache，
写入路径（app/org/service.py）通过 invalidate_tasktree_org_indexes() 清空。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tasktree import service as svc_mod


def _reset_cache():
    svc_mod._ORG_INDEXES_CACHE["value"] = None
    svc_mod._ORG_INDEXES_CACHE["expires_at"] = 0.0


def _fake_inner_result():
    sales = MagicMock(spec=["id", "name", "parent_id", "path", "sort_order"])
    sales.id = "lv1-sales"
    sales.name = "销售一部"
    sales.parent_id = "ROOT"
    sales.path = "/ROOT/lv1-sales"
    sales.sort_order = 0
    return (
        {sales.id: sales},
        {sales.name: sales},
        {sales.name},
        {sales.name: sales},
    )


@pytest.mark.asyncio
async def test_load_org_indexes_caches_60s_ttl(monkeypatch):
    """5 次连续调用 _load_org_indexes → uncached _load_org_indexes_inner 只调 1 次。"""
    _reset_cache()
    inner_calls = {"count": 0}

    async def _fake_inner(_db):
        inner_calls["count"] += 1
        return _fake_inner_result()

    monkeypatch.setattr(svc_mod, "_load_org_indexes_inner", _fake_inner)

    db = object()
    for _ in range(5):
        result = await svc_mod._load_org_indexes(db)
        assert "销售一部" in result[2]

    assert inner_calls["count"] == 1, "60s TTL 内应仅调一次 _load_org_indexes_inner"


@pytest.mark.asyncio
async def test_invalidate_tasktree_org_indexes_forces_next_call_to_refetch(monkeypatch):
    """invalidate_tasktree_org_indexes 后再调 → uncached 实现被再次调一次。"""
    _reset_cache()
    inner_calls = {"count": 0}

    async def _fake_inner(_db):
        inner_calls["count"] += 1
        return _fake_inner_result()

    monkeypatch.setattr(svc_mod, "_load_org_indexes_inner", _fake_inner)

    db = object()
    await svc_mod._load_org_indexes(db)
    await svc_mod._load_org_indexes(db)
    assert inner_calls["count"] == 1

    await svc_mod.invalidate_tasktree_org_indexes()
    await svc_mod._load_org_indexes(db)
    assert inner_calls["count"] == 2, "invalidate 后必须穿透到 inner"


@pytest.mark.asyncio
async def test_load_org_indexes_expires_after_ttl(monkeypatch):
    """模拟 expires_at 过期 → 必须重新 fetch。"""
    _reset_cache()
    inner_calls = {"count": 0}

    async def _fake_inner(_db):
        inner_calls["count"] += 1
        return _fake_inner_result()

    monkeypatch.setattr(svc_mod, "_load_org_indexes_inner", _fake_inner)

    db = object()
    await svc_mod._load_org_indexes(db)
    assert inner_calls["count"] == 1

    # 强制让缓存过期（expires_at = 0 → 一定 < monotonic())
    svc_mod._ORG_INDEXES_CACHE["expires_at"] = 0.0
    await svc_mod._load_org_indexes(db)
    assert inner_calls["count"] == 2


@pytest.mark.asyncio
async def test_org_invalidate_cache_calls_invalidate_tasktree_org_indexes(monkeypatch):
    """app/org/service.py 的 _invalidate_org_cache 必须调 invalidate_tasktree_org_indexes。"""
    from app.org import service as org_svc

    called = {"flag": False}

    async def _fake_invalidate():
        called["flag"] = True

    async def _fake_invalidate_tasktree(_dept):
        return None

    async def _fake_cache_delete_pattern(_pattern):
        return 0

    monkeypatch.setattr(svc_mod, "invalidate_tasktree_org_indexes", _fake_invalidate)
    monkeypatch.setattr(svc_mod, "invalidate_tasktree", _fake_invalidate_tasktree)
    monkeypatch.setattr(org_svc, "cache_delete_pattern", _fake_cache_delete_pattern)

    await org_svc._invalidate_org_cache()
    assert called["flag"], "org write path 必须显式清 _ORG_INDEXES_CACHE"
