import pytest


@pytest.mark.asyncio
async def test_knowledge_base_auto_creates_department_and_searches(client):
    from app.database import async_session_factory
    from app.org.models import OrgUnit

    async with async_session_factory() as db:
        db.add(OrgUnit(id="dept-ec", name="电商部", type="department", path="/dept-ec"))
        await db.commit()

    bases = (await client.get("/api/knowledge/bases")).json()
    ec_base = next(item for item in bases["items"] if item["org_unit_id"] == "dept-ec")
    assert ec_base["department"] == "电商部"
    assert ec_base["storage_backend"] == "lightrag"

    created = (
        await client.post(
            "/api/knowledge/documents",
            json={
                "kb_id": ec_base["id"],
                "title": "五星价格力处理 SOP",
                "content": "五星价格力下降时，先检查活动价和券后价，再同步检查高价限流与竞品价格带。",
                "tags": ["价格力", "SOP"],
            },
        )
    ).json()
    assert created["chunk_count"] >= 1
    assert created["index_status"] == "indexed"

    searched = (
        await client.post(
            "/api/knowledge/search",
            json={"kb_id": ec_base["id"], "query": "价格力下降怎么处理", "top_k": 3},
        )
    ).json()
    assert searched["total"] >= 1
    assert searched["items"][0]["document_id"] == created["id"]
    assert searched["retrieval"]["backend"] == "lightrag"
    assert searched["retrieval"]["mode"] == "hybrid"

    answer = (
        await client.post(
            "/api/knowledge/ask",
            json={"kb_id": ec_base["id"], "query": "价格力下降怎么处理", "top_k": 3},
        )
    ).json()
    assert "五星价格力" in answer["answer"]
    assert answer["sources"][0]["document_id"] == created["id"]
    assert answer["citations"][0]["document_id"] == created["id"]

    updated = (
        await client.put(
            f"/api/knowledge/documents/{created['id']}",
            json={
                "title": "五星价格力处理 SOP v2",
                "content": "五星价格力下降时，先确认券后价，再补充竞品价格带和活动节奏。",
                "source_type": "url",
                "source_ref": "https://docs.example.com/price-power",
                "tags": ["价格力", "SOP", "v2"],
            },
        )
    ).json()
    assert updated["title"].endswith("v2")
    assert updated["source_type"] == "url"
    assert updated["source_ref"].endswith("price-power")
    assert updated["chunk_count"] >= 1

    context = (
        await client.post(
            "/api/knowledge/context",
            json={"kb_id": ec_base["id"], "query": "券后价和竞品价格带", "top_k": 2, "max_chars": 1000},
        )
    ).json()
    assert "prompt_context" in context
    assert "券后价" in context["prompt_context"]
    assert context["usage"]["api"] == "POST /api/knowledge/context"

    jobs = (await client.get("/api/knowledge/index-jobs", params={"kb_id": ec_base["id"]})).json()
    assert jobs["total"] >= 1
    assert jobs["items"][0]["status"] == "succeeded"

    health = (await client.get("/api/knowledge/rag/health")).json()
    assert health["backend"] == "lightrag"

    deleted = (await client.delete(f"/api/knowledge/documents/{created['id']}")).json()
    assert deleted["status"] == "deleted"

    searched_after_delete = (
        await client.post(
            "/api/knowledge/search",
            json={"kb_id": ec_base["id"], "query": "券后价", "top_k": 3},
        )
    ).json()
    assert searched_after_delete["total"] == 0


@pytest.mark.asyncio
async def test_knowledge_base_department_scope_and_asset_sync(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.knowledge.service import list_bases, sync_department_assets
    from app.org.models import OrgUnit, UserOrgMembership
    from app.skills.core.models import Skill

    async with async_session_factory() as db:
        db.add_all([
            OrgUnit(id="dept-ec", name="电商部", type="department", path="/dept-ec"),
            OrgUnit(id="dept-live", name="直播部", type="department", path="/dept-live"),
            User(
                id="aibp-ec",
                username="aibp-ec",
                name="电商 AIBP",
                role="aibp",
                department="电商部",
                is_active=True,
                state="active",
                must_change_password=False,
            ),
            UserOrgMembership(user_id="aibp-ec", org_unit_id="dept-ec", membership_type="primary"),
            Skill(
                id="price-check",
                name="价格检查",
                display_name="价格检查",
                description="检查活动价、券后价和五星价格力",
                department="电商部",
                org_unit_id="dept-ec",
                status="active",
                visibility="department",
                risk_level="R1",
            ),
        ])
        await db.commit()

    async with async_session_factory() as db:
        user = await db.get(User, "aibp-ec")
        bases = await list_bases(db, user)
        assert [item["department"] for item in bases["items"]] == ["电商部"]
        assert bases["items"][0]["can_write"] is True

        synced = await sync_department_assets(db, user, kb_id=bases["items"][0]["id"])
        assert synced["skill_documents"] == 1
        await db.commit()

    response = await client.post(
        "/api/knowledge/search",
        json={"query": "五星价格力", "top_k": 5},
    )
    data = response.json()
    assert any(item["document_title"].startswith("Skill: 价格检查") for item in data["items"])



def test_ollama_embedding_preset_is_keyless():
    from app.knowledge.service import _apply_rag_provider_defaults, _embedding_requires_api_key

    cfg = _apply_rag_provider_defaults({"ai.embedding.provider": "ollama"})
    assert cfg["ai.embedding.api_base"] == "http://127.0.0.1:11434/v1"
    assert cfg["ai.embedding.model"] == "bge-m3"
    assert cfg["ai.embedding.dim"] == 1024
    assert _embedding_requires_api_key(cfg) is False


def test_siliconflow_presets_exclude_pro_models():
    from app.knowledge.service import _apply_rag_provider_defaults

    default_cfg = _apply_rag_provider_defaults({"ai.embedding.provider": "siliconflow"})
    assert default_cfg["ai.embedding.api_base"] == "https://api.siliconflow.cn/v1"
    assert default_cfg["ai.embedding.model"] == "Qwen/Qwen3-Embedding-8B"
    assert default_cfg["ai.embedding.dim"] == 4096

    cfg = _apply_rag_provider_defaults({
        "ai.embedding.provider": "siliconflow",
        "ai.embedding.api_base": "https://api.siliconflow.cn/v1",
        "ai.embedding.model": "Pro/BAAI/bge-m3",
        "ai.reranker.provider": "siliconflow",
        "ai.reranker.api_base": "https://api.siliconflow.cn/v1",
        "ai.reranker.model": "Pro/BAAI/bge-reranker-v2-m3",
    })
    assert cfg["ai.embedding.model"] == "BAAI/bge-m3"
    assert cfg["ai.reranker.model"] == "BAAI/bge-reranker-v2-m3"


@pytest.mark.asyncio
async def test_embedding_config_surfaces_provider_detail(monkeypatch):
    import httpx
    from app.auth.models import User
    from app.knowledge.service import test_embedding_config

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            request = httpx.Request("POST", "https://api.siliconflow.cn/v1/embeddings")
            response = httpx.Response(
                403,
                json={"code": 30001, "message": "Sorry, your account balance is insufficient", "data": None},
                request=request,
            )
            raise httpx.HTTPStatusError("403", request=request, response=response)

    monkeypatch.setattr("app.knowledge.service.httpx.AsyncClient", FakeAsyncClient)
    user = User(id="admin", username="admin", name="管理员", role="admin", is_active=True, state="active")
    with pytest.raises(Exception) as exc_info:
        await test_embedding_config(
            None,
            user,
            overrides={
                "provider": "siliconflow",
                "api_base": "https://api.siliconflow.cn/v1",
                "api_key": "sk-test",
                "model": "BAAI/bge-m3",
            },
        )
    err = exc_info.value
    assert getattr(err, "code", "") == "LLM_API_ERROR"
    assert "chargeBalance" in err.detail["detail"]
    assert "balance is insufficient" in err.detail["detail"]
