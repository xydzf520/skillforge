from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.time_utils import now_bjt


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ExecuteResult:
    def __init__(self, *, scalar_value=None, scalars=None):
        self._scalar_value = scalar_value
        self._scalars = scalars or []

    def scalar(self):
        return self._scalar_value

    def scalars(self):
        return _ScalarResult(self._scalars)


@pytest.mark.asyncio
async def test_datasource_list_batches_latest_ingestion_logs():
    from app.datasources.service import list_sources

    now = now_bjt()
    sources = [
        SimpleNamespace(
            id="ds-a",
            name="A",
            department="EC",
            source_type="csv",
            config={"encrypted_cookies": "hidden", "foo": "bar"},
            schedule=None,
            is_active=True,
            stale_threshold_hours=24,
            related_skills=[],
        ),
        SimpleNamespace(
            id="ds-b",
            name="B",
            department="EC",
            source_type="api",
            config={},
            schedule=None,
            is_active=True,
            stale_threshold_hours=24,
            related_skills=[],
        ),
        SimpleNamespace(
            id="ds-c",
            name="C",
            department="EC",
            source_type="csv",
            config={},
            schedule=None,
            is_active=False,
            stale_threshold_hours=24,
            related_skills=[],
        ),
    ]
    logs = [
        SimpleNamespace(id=2, source_id="ds-a", created_at=now, row_count=20),
        SimpleNamespace(id=1, source_id="ds-b", created_at=now - timedelta(hours=1), row_count=10),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _ExecuteResult(scalar_value=3),
            _ExecuteResult(scalars=sources),
            _ExecuteResult(scalars=logs),
        ]
    )

    result = await list_sources(db, page=1, page_size=20)

    assert db.execute.await_count == 3
    assert result["total"] == 3
    by_id = {item["id"]: item for item in result["items"]}
    assert by_id["ds-a"]["last_row_count"] == 20
    assert by_id["ds-b"]["last_row_count"] == 10
    assert by_id["ds-c"]["last_updated"] is None
    assert "encrypted_cookies" not in by_id["ds-a"]["config"]


@pytest.mark.asyncio
async def test_org_tree_returns_cached_value_without_db(monkeypatch):
    from app.org import service as org_service

    class FakeCache:
        async def get(self, *parts):
            assert parts == ("default",)
            return [{"id": "cached"}]

        async def set(self, *parts, value):
            raise AssertionError("cached branch should not write")

    monkeypatch.setattr(org_service, "ORG_TREE_CACHE", FakeCache())
    db = AsyncMock()

    result = await org_service.get_org_tree(db)

    assert result == [{"id": "cached"}]
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_skill_list_returns_namespace_cache_hit(monkeypatch):
    from app.skills import router as skills_router

    seen = {}

    class FakeCache:
        async def get(self, *parts):
            seen["parts"] = parts
            return {"items": [{"id": "cached-skill"}], "total": 1}

        async def set(self, *parts, value):
            raise AssertionError("cache hit should not write")

    monkeypatch.setattr(skills_router, "SKILL_LIST_CACHE", FakeCache())
    monkeypatch.setattr(skills_router.service, "list_skills", AsyncMock())
    user = SimpleNamespace(
        id="u1",
        role="operator",
        department="EC",
        can_view_all=False,
        permissions_rev=3,
    )

    result = await skills_router.list_skills(
        department="EC",
        status="active",
        q=None,
        risk_level=None,
        trigger_type=None,
        sort_by="updated_at",
        sort_order="desc",
        page=1,
        page_size=50,
        current_user=user,
        db=AsyncMock(),
    )

    assert result["items"][0]["id"] == "cached-skill"
    skills_router.service.list_skills.assert_not_awaited()
    assert "user=u1" in seen["parts"][0]
    assert "perm_rev=3" in seen["parts"][0]


@pytest.mark.asyncio
async def test_datasource_list_returns_namespace_cache_hit(monkeypatch):
    from app.datasources import router as datasource_router

    seen = {}

    class FakeCache:
        async def get(self, *parts):
            seen["parts"] = parts
            return {"items": [{"id": "cached-source"}], "total": 1}

        async def set(self, *parts, value):
            raise AssertionError("cache hit should not write")

    monkeypatch.setattr(datasource_router, "DATASOURCE_LIST_CACHE", FakeCache())
    monkeypatch.setattr(datasource_router.service, "list_sources", AsyncMock())
    user = SimpleNamespace(
        id="u1",
        role="operator",
        department="EC",
        can_view_all=False,
    )

    result = await datasource_router.list_sources(
        department=None,
        source_type="csv",
        is_active=True,
        page=1,
        page_size=20,
        current_user=user,
        db=AsyncMock(),
    )

    assert result["items"][0]["id"] == "cached-source"
    datasource_router.service.list_sources.assert_not_awaited()
    assert "user=u1" in seen["parts"][0]
    assert seen["parts"][1:] == ("EC", "csv", True, 1, 20)


@pytest.mark.asyncio
async def test_audit_stats_cache_key_includes_days(monkeypatch):
    from app.audit import router as audit_router

    seen = {}

    async def fake_get(key):
        seen["get"] = key
        return None

    async def fake_set(key, value, ttl=None):
        seen["set"] = key
        seen["ttl"] = ttl

    monkeypatch.setattr(audit_router, "cache_get", fake_get)
    monkeypatch.setattr(audit_router, "cache_set", fake_set)
    monkeypatch.setattr(audit_router.audit, "get_stats", AsyncMock(return_value={"days": 7}))

    result = await audit_router.audit_stats(days=7, current_user=MagicMock())

    assert result == {"days": 7}
    assert seen["get"] == "audit:stats:7"
    assert seen["set"] == "audit:stats:7"


@pytest.mark.asyncio
async def test_security_events_cache_key_and_query_use_days(monkeypatch):
    from app.audit import router as audit_router

    seen = {}

    async def fake_get(key):
        seen["get"] = key
        return None

    async def fake_set(key, value, ttl=None):
        seen["set"] = key

    async def fake_query(**kwargs):
        seen["query"] = kwargs
        return {"items": []}

    monkeypatch.setattr(audit_router, "cache_get", fake_get)
    monkeypatch.setattr(audit_router, "cache_set", fake_set)
    monkeypatch.setattr(audit_router.audit, "query", fake_query)

    result = await audit_router.security_events(days=3, current_user=MagicMock())

    assert result == {"items": []}
    assert seen["get"] == "audit:security:3"
    assert seen["set"] == "audit:security:3"
    assert seen["query"]["action"] == "user.login_failed"
    assert seen["query"]["date_from"] is not None
