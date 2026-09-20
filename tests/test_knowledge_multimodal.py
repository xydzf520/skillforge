import base64

import pytest


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


@pytest.mark.asyncio
async def test_knowledge_image_upload_indexes_and_serves_media(client, monkeypatch, tmp_path):
    from app.database import async_session_factory
    from app.org.models import OrgUnit
    from app.knowledge import service as knowledge_service

    monkeypatch.setattr(knowledge_service, "KNOWLEDGE_MEDIA_ROOT", tmp_path / "knowledge_media")

    async def fake_analyze(*args, **kwargs):
        return {
            "status": "ok",
            "caption": "红色包装的商品主图，画面包含促销标签和品牌标识。",
            "visible_text": "限时促销 红色包装",
            "objects": ["商品包装", "促销标签", "品牌标识"],
            "tags": ["图片", "商品主图", "红色包装"],
        }

    monkeypatch.setattr("app.knowledge.service._analyze_image_with_vision", fake_analyze)

    async with async_session_factory() as db:
        db.add(OrgUnit(id="dept-mm", name="多模态部", type="department", path="/dept-mm"))
        await db.commit()

    bases = (await client.get("/api/knowledge/bases")).json()
    base = next(item for item in bases["items"] if item["org_unit_id"] == "dept-mm")

    uploaded = (
        await client.post(
            "/api/knowledge/documents/upload",
            data={"kb_id": base["id"], "title": "红色包装主图", "tags": "商品,主图"},
            files={"file": ("red-pack.png", PNG_1X1, "image/png")},
        )
    ).json()

    assert uploaded["modality"] == "image"
    assert uploaded["index_status"] == "indexed"
    assert uploaded["chunk_count"] >= 1
    assert uploaded["media_assets"]
    asset = uploaded["media_assets"][0]
    assert asset["mime_type"] == "image/png"
    assert asset["status"] == "indexed"
    assert asset["content_url"].endswith("/content")

    searched = (
        await client.post(
            "/api/knowledge/search",
            json={"kb_id": base["id"], "query": "红色包装促销标签", "top_k": 5, "modality": "image"},
        )
    ).json()
    assert searched["modality"] == "image"
    assert searched["total"] >= 1
    hit = searched["items"][0]
    assert hit["document_id"] == uploaded["id"]
    assert hit["modality"] == "image"
    assert hit["media"]["id"] == asset["id"]

    text_only = (
        await client.post(
            "/api/knowledge/search",
            json={"kb_id": base["id"], "query": "红色包装促销标签", "top_k": 5, "modality": "text"},
        )
    ).json()
    assert text_only["total"] == 0

    media_response = await client.get(f"/api/knowledge/media/{asset['id']}/content")
    assert media_response.status_code == 200
    assert media_response.headers["content-type"].startswith("image/png")
    assert media_response.content == PNG_1X1

    health = (await client.get("/api/knowledge/rag/health")).json()
    assert health["multimodal_enabled"] is True
    assert "image" in health["supported_modalities"]
    assert health["image_index_count"] >= 1


@pytest.mark.asyncio
async def test_knowledge_image_search_upload_uses_vision_query(client, monkeypatch, tmp_path):
    from app.database import async_session_factory
    from app.org.models import OrgUnit
    from app.knowledge import service as knowledge_service

    monkeypatch.setattr(knowledge_service, "KNOWLEDGE_MEDIA_ROOT", tmp_path / "knowledge_media")

    async def fake_analyze(*args, **kwargs):
        return {
            "status": "ok",
            "caption": "蓝色瓶身的客服流程海报，包含退换货说明。",
            "visible_text": "退换货 客服流程 蓝色瓶身",
            "objects": ["海报", "瓶身"],
            "tags": ["客服流程"],
        }

    monkeypatch.setattr("app.knowledge.service._analyze_image_with_vision", fake_analyze)

    async with async_session_factory() as db:
        db.add(OrgUnit(id="dept-img-search", name="图片检索部", type="department", path="/dept-img-search"))
        await db.commit()

    bases = (await client.get("/api/knowledge/bases")).json()
    base = next(item for item in bases["items"] if item["org_unit_id"] == "dept-img-search")
    created = (
        await client.post(
            "/api/knowledge/documents",
            json={
                "kb_id": base["id"],
                "title": "退换货客服流程",
                "content": "蓝色瓶身商品售后：客服先确认订单，再按退换货流程处理并登记原因。",
                "tags": ["客服流程", "退换货"],
            },
        )
    ).json()

    searched = (
        await client.post(
            "/api/knowledge/search/upload",
            data={"kb_id": base["id"], "top_k": "5", "modality": "all"},
            files={"file": ("query.png", PNG_1X1, "image/png")},
        )
    ).json()

    assert searched["image_query"]["status"] == "ok"
    assert "蓝色瓶身" in searched["query"]
    assert searched["total"] >= 1
    assert any(item["document_id"] == created["id"] for item in searched["items"])
