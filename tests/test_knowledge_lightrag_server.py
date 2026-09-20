import pytest


class FakeLightRagClient:
    requests: list[tuple[str, str, dict | None]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None):
        import httpx

        self.requests.append(("GET", url, params or {}))
        request = httpx.Request("GET", url)
        if url.endswith("/health"):
            return httpx.Response(200, json={"status": "ok", "service": "lightrag"}, request=request)
        return httpx.Response(404, json={"error": "not found"}, request=request)

    async def post(self, url, headers=None, json=None):
        import httpx

        self.requests.append(("POST", url, json or {}))
        request = httpx.Request("POST", url)
        if url.endswith("/documents/insert"):
            return httpx.Response(200, json={"status": "queued", "track_id": "trk-1"}, request=request)
        if url.endswith("/query"):
            return httpx.Response(200, json={"response": "五星价格力 SOP：先检查活动价、券后价和竞品价格带。"}, request=request)
        return httpx.Response(404, json={"error": "not found"}, request=request)

    async def request(self, method, url, headers=None, json=None):
        if method.upper() == "POST":
            return await self.post(url, headers=headers, json=json)
        return await self.get(url, headers=headers)


@pytest.mark.asyncio
async def test_lightrag_server_health_endpoint(client, monkeypatch):
    FakeLightRagClient.requests.clear()
    monkeypatch.setattr("app.knowledge.service.httpx.AsyncClient", FakeLightRagClient)

    data = (
        await client.post(
            "/api/knowledge/rag/test-server",
            json={"api_base": "http://127.0.0.1:9621", "flavor": "official", "api_key": "test-key"},
        )
    ).json()

    assert data["ok"] is True
    assert data["flavor"] == "official"
    assert data["health"]["endpoint"] == "/health"
    assert FakeLightRagClient.requests[0][0] == "GET"
    assert FakeLightRagClient.requests[0][1].endswith("/health")


@pytest.mark.asyncio
async def test_official_lightrag_server_mode_indexes_and_queries(client, monkeypatch):
    from app.common.models import SystemConfig
    from app.database import async_session_factory
    from app.org.models import OrgUnit

    FakeLightRagClient.requests.clear()
    monkeypatch.setattr("app.knowledge.service.httpx.AsyncClient", FakeLightRagClient)

    async with async_session_factory() as db:
        db.add_all([
            OrgUnit(id="dept-rag-server", name="RAG服务部", type="department", path="/dept-rag-server"),
            SystemConfig(key="knowledge.lightrag.mode", value="server"),
            SystemConfig(key="knowledge.lightrag.flavor", value="official"),
            SystemConfig(key="knowledge.lightrag.api_base", value="http://127.0.0.1:9621"),
            SystemConfig(key="knowledge.lightrag.api_key", value="test-key"),
        ])
        await db.commit()

    bases = (await client.get("/api/knowledge/bases")).json()
    base = next(item for item in bases["items"] if item["org_unit_id"] == "dept-rag-server")
    created = (
        await client.post(
            "/api/knowledge/documents",
            json={
                "kb_id": base["id"],
                "title": "五星价格力 SOP",
                "content": "五星价格力下降时，先检查活动价、券后价和竞品价格带。",
                "tags": ["价格力"],
            },
        )
    ).json()

    assert created["index_status"] == "indexed"
    assert any(req[1].endswith("/documents/insert") for req in FakeLightRagClient.requests)

    searched = (
        await client.post(
            "/api/knowledge/search",
            json={"kb_id": base["id"], "query": "五星价格力怎么处理", "top_k": 3},
        )
    ).json()

    assert searched["retrieval"]["lightrag_mode"] == "server"
    assert searched["items"][0]["document_id"] == created["id"]
    assert searched["items"][0]["source_trace"]["backend"] == "lightrag_official_bridge"
    assert any(req[1].endswith("/query") for req in FakeLightRagClient.requests)


@pytest.mark.asyncio
async def test_docling_and_vlm_config_endpoints(client, monkeypatch):
    import httpx

    class FakeRagInfraClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, params=None):
            request = httpx.Request("GET", url)
            if url.endswith("/docs"):
                return httpx.Response(200, text="Docling API", headers={"content-type": "text/html"}, request=request)
            return httpx.Response(404, json={"error": "not found"}, request=request)

        async def post(self, url, headers=None, json=None):
            request = httpx.Request("POST", url)
            if url.endswith("/chat/completions"):
                return httpx.Response(200, json={"choices": [{"message": {"content": "{\"ok\": true}"}}]}, request=request)
            return httpx.Response(404, json={"error": "not found"}, request=request)

    monkeypatch.setattr("app.knowledge.service.httpx.AsyncClient", FakeRagInfraClient)

    docling = (
        await client.post(
            "/api/knowledge/rag/test-docling",
            json={"api_base": "http://127.0.0.1:15001", "timeout": 10},
        )
    ).json()
    assert docling["ok"] is True
    assert docling["health"]["endpoint"] == "/docs"

    vlm = (
        await client.post(
            "/api/knowledge/rag/test-vlm",
            json={
                "provider": "openai",
                "api_base": "https://example.test/v1",
                "api_key": "sk-test",
                "model": "qwen-vl-plus",
            },
        )
    ).json()
    assert vlm["ok"] is True
    assert vlm["model"] == "qwen-vl-plus"
    assert vlm["used_unsaved_key"] is True


@pytest.mark.asyncio
async def test_rag_health_reports_multimodal_config(client):
    from app.common.models import SystemConfig
    from app.database import async_session_factory

    async with async_session_factory() as db:
        db.add_all([
            SystemConfig(key="knowledge.rag.enabled", value=True),
            SystemConfig(key="knowledge.docling.enabled", value=True),
            SystemConfig(key="knowledge.docling.api_base", value="http://127.0.0.1:15001"),
            SystemConfig(key="knowledge.vlm.provider", value="openai"),
            SystemConfig(key="knowledge.vlm.api_base", value="https://example.test/v1"),
            SystemConfig(key="knowledge.vlm.api_key", value="sk-test"),
            SystemConfig(key="knowledge.vlm.model", value="qwen-vl-plus"),
        ])
        await db.commit()

    health = (await client.get("/api/knowledge/rag/health")).json()
    assert health["rag_enabled"] is True
    assert health["docling_enabled"] is True
    assert health["docling_configured"] is True
    assert health["vlm_configured"] is True
    assert health["vlm_model"] == "qwen-vl-plus"
