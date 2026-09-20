from __future__ import annotations

import importlib

import pytest

from app.common import cache as cache_mod
from app.config import settings

knowledge_router = importlib.import_module("app.knowledge.router")
training_router = importlib.import_module("app.training.router")
sf_router = importlib.import_module("app.sf.router")
users_router = importlib.import_module("app.users.router")
org_router = importlib.import_module("app.org.router")
aiclaw_router = importlib.import_module("app.aiclaw.router")
codex_router = importlib.import_module("app.codex.router")


def test_high_value_pages_have_backend_cache_ttl():
    assert knowledge_router.get_knowledge_summary._cache_ttl == settings.CACHE_TTL_KNOWLEDGE
    assert knowledge_router.get_knowledge_documents._cache_ttl == settings.CACHE_TTL_KNOWLEDGE
    assert knowledge_router.search_knowledge._cache_ttl == settings.CACHE_TTL_KNOWLEDGE
    assert knowledge_router.build_knowledge_context._cache_ttl == settings.CACHE_TTL_KNOWLEDGE

    assert training_router.get_training_jobs._cache_ttl == settings.CACHE_TTL_TRAINING
    assert training_router.get_training_deployments._cache_ttl == settings.CACHE_TTL_TRAINING
    assert training_router.get_training_job_detail._cache_ttl == settings.CACHE_TTL_TRAINING
    assert training_router.get_training_resources._cache_ttl == settings.CACHE_TTL_KNOWLEDGE

    assert sf_router.sf_overview._cache_ttl == settings.CACHE_TTL_GENERATED_DATA
    assert sf_router.sf_catalog._cache_ttl == settings.CACHE_TTL_SF_CATALOG
    assert sf_router.sf_trace._cache_ttl == settings.CACHE_TTL_TRACE

    assert aiclaw_router.list_instances._cache_ttl == settings.CACHE_TTL_AGENT
    assert aiclaw_router.get_agent_department_kpi._cache_ttl == settings.CACHE_TTL_AGENT
    assert users_router.list_users._cache_ttl == settings.CACHE_TTL_ADMIN
    assert org_router.get_org_tree._cache_ttl == settings.CACHE_TTL_ADMIN
    assert codex_router.codex_admin_usage_summary._cache_ttl == settings.CACHE_TTL_ADMIN


@pytest.mark.asyncio
async def test_page_cache_invalidation_uses_namespaces(monkeypatch):
    patterns: list[str] = []

    async def fake_delete_pattern(pattern: str) -> int:
        patterns.append(pattern)
        return 1

    monkeypatch.setattr(cache_mod, "cache_delete_pattern", fake_delete_pattern)

    deleted = await cache_mod.invalidate_page_cache("knowledge", "training:*", "agent")

    assert deleted == 3
    assert patterns == ["knowledge:*", "training:*", "agent:*"]
