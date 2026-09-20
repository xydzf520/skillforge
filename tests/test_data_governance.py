"""数据治理四层模型 API 测试：资产 / 产品 / 策略 / 契约"""

import pytest

pytestmark = pytest.mark.asyncio


# ═══════════════════════════ 数据资产 ═══════════════════════════

async def test_create_asset(client):
    """创建数据资产"""
    resp = await client.post("/api/data-governance/assets", json={
        "id": "asset-ec-daily-report",
        "name": "EC日报数据",
        "description": "电商部每日运营报告",
        "asset_type": "table",
        "sensitivity": "L2",
        "schema_def": [
            {"name": "date", "type": "date", "description": "日期"},
            {"name": "revenue", "type": "number", "description": "营收"},
            {"name": "phone", "type": "string", "description": "联系电话", "sensitivity": "L3"},
        ],
        "tags": ["ec", "daily"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "asset-ec-daily-report"
    assert data["name"] == "EC日报数据"
    assert data["sensitivity"] == "L2"
    assert len(data["schema_json"]) == 3


async def test_create_asset_duplicate(client):
    """资产 ID 重复时返回 409"""
    payload = {"id": "dup-asset", "name": "测试资产"}
    resp1 = await client.post("/api/data-governance/assets", json=payload)
    assert resp1.status_code == 200
    resp2 = await client.post("/api/data-governance/assets", json=payload)
    assert resp2.status_code == 409


async def test_create_asset_invalid_id(client):
    """资产 ID 格式非法时返回 400"""
    resp = await client.post("/api/data-governance/assets", json={
        "id": "有中文", "name": "测试",
    })
    assert resp.status_code == 400


async def test_list_assets(client):
    """列表查询"""
    # 先创建两个
    await client.post("/api/data-governance/assets", json={"id": "a1", "name": "资产1", "sensitivity": "L1"})
    await client.post("/api/data-governance/assets", json={"id": "a2", "name": "资产2", "sensitivity": "L3"})

    resp = await client.get("/api/data-governance/assets")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2

    # 按敏感度过滤
    resp2 = await client.get("/api/data-governance/assets?sensitivity=L1")
    assert resp2.status_code == 200
    for item in resp2.json()["items"]:
        assert item["sensitivity"] == "L1"


async def test_get_asset(client):
    """获取单个资产详情"""
    await client.post("/api/data-governance/assets", json={"id": "detail-test", "name": "详情测试"})
    resp = await client.get("/api/data-governance/assets/detail-test")
    assert resp.status_code == 200
    assert resp.json()["id"] == "detail-test"


async def test_get_asset_not_found(client):
    """资产不存在时返回 404"""
    resp = await client.get("/api/data-governance/assets/not-exist-12345")
    assert resp.status_code == 404


# ═══════════════════════════ 数据产品 ═══════════════════════════

async def test_create_product(client):
    """创建数据产品"""
    # 先创建资产
    await client.post("/api/data-governance/assets", json={"id": "prod-asset-1", "name": "底层资产"})

    resp = await client.post("/api/data-governance/products", json={
        "id": "product-ec-overview",
        "name": "EC运营总览",
        "description": "聚合多个 EC 数据源",
        "asset_ids": ["prod-asset-1"],
        "sla_json": {"freshness_hours": 24, "availability": 99.9},
        "access_mode": "read",
        "published": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "product-ec-overview"
    assert data["published"] is True
    assert "prod-asset-1" in data["asset_ids"]


async def test_create_product_no_assets(client):
    """产品未关联资产时返回 400"""
    resp = await client.post("/api/data-governance/products", json={
        "id": "empty-product", "name": "空产品", "asset_ids": [],
    })
    assert resp.status_code == 400


async def test_list_products_published_only(client):
    """默认只返回已发布的产品"""
    await client.post("/api/data-governance/assets", json={"id": "lp-asset", "name": "资产"})
    await client.post("/api/data-governance/products", json={
        "id": "pub-prod", "name": "已发布", "asset_ids": ["lp-asset"], "published": True,
    })
    await client.post("/api/data-governance/products", json={
        "id": "draft-prod", "name": "草稿", "asset_ids": ["lp-asset"], "published": False,
    })

    resp = await client.get("/api/data-governance/products?published_only=true")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()["items"]]
    assert "pub-prod" in ids
    assert "draft-prod" not in ids


async def test_get_product(client):
    """获取产品详情"""
    await client.post("/api/data-governance/assets", json={"id": "gp-asset", "name": "资产"})
    await client.post("/api/data-governance/products", json={
        "id": "gp-product", "name": "产品详情测试", "asset_ids": ["gp-asset"],
    })
    resp = await client.get("/api/data-governance/products/gp-product")
    assert resp.status_code == 200
    assert resp.json()["id"] == "gp-product"


async def test_get_product_not_found(client):
    """产品不存在时返回 404"""
    resp = await client.get("/api/data-governance/products/no-such-product")
    assert resp.status_code == 404


# ═══════════════════════════ 数据策略 ═══════════════════════════

async def test_create_masking_policy(client):
    """创建脱敏策略"""
    await client.post("/api/data-governance/assets", json={"id": "mask-asset", "name": "脱敏测试资产"})

    resp = await client.post("/api/data-governance/policies", json={
        "name": "手机号脱敏",
        "policy_type": "masking",
        "target_type": "asset",
        "target_id": "mask-asset",
        "rules_json": {"fields": ["phone", "email"], "method": "partial_mask"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["policy_type"] == "masking"
    assert data["enabled"] is True


async def test_create_policy_invalid_type(client):
    """策略类型非法时返回 400"""
    resp = await client.post("/api/data-governance/policies", json={
        "name": "非法策略", "policy_type": "unknown_type",
        "target_type": "asset", "target_id": "x", "rules_json": {"a": 1},
    })
    assert resp.status_code == 400


async def test_create_policy_invalid_target_type(client):
    """目标类型非法时返回 400"""
    resp = await client.post("/api/data-governance/policies", json={
        "name": "非法目标", "policy_type": "masking",
        "target_type": "invalid_target", "target_id": "x", "rules_json": {"a": 1},
    })
    assert resp.status_code == 400


async def test_list_policies_filter(client):
    """策略列表支持按类型和目标过滤"""
    await client.post("/api/data-governance/policies", json={
        "name": "策略A", "policy_type": "masking",
        "target_type": "asset", "target_id": "filter-test-asset",
        "rules_json": {"fields": ["name"], "method": "full_mask"},
    })
    await client.post("/api/data-governance/policies", json={
        "name": "策略B", "policy_type": "retention",
        "target_type": "asset", "target_id": "filter-test-asset",
        "rules_json": {"days": 365, "action": "archive"},
    })

    resp = await client.get("/api/data-governance/policies?policy_type=masking&target_id=filter-test-asset")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(p["policy_type"] == "masking" for p in items)


# ═══════════════════════════ 数据契约 ═══════════════════════════

async def _setup_skill(client):
    """辅助：确保 skills 表中有一条记录用于 FK 约束"""
    from app.database import async_session_factory
    from app.skills.core.models import Skill

    async with async_session_factory() as db:
        from sqlalchemy import select
        existing = (await db.execute(select(Skill).where(Skill.id == "test-skill-001"))).scalar_one_or_none()
        if not existing:
            db.add(Skill(id="test-skill-001", name="测试Skill", department="EC", status="active"))
            await db.commit()


async def test_create_contract(client):
    """创建数据契约"""
    await _setup_skill(client)
    await client.post("/api/data-governance/assets", json={"id": "contract-asset", "name": "契约资产"})

    resp = await client.post("/api/data-governance/contracts", json={
        "skill_id": "test-skill-001",
        "asset_id": "contract-asset",
        "fields_used": ["date", "revenue"],
        "access_level": "read",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_id"] == "test-skill-001"
    assert data["asset_id"] == "contract-asset"
    assert data["status"] == "active"


async def test_create_contract_no_target(client):
    """契约既无 product_id 也无 asset_id 时返回 400"""
    await _setup_skill(client)
    resp = await client.post("/api/data-governance/contracts", json={
        "skill_id": "test-skill-001",
    })
    assert resp.status_code == 400


async def test_list_contracts_by_skill(client):
    """按 skill_id 过滤契约"""
    await _setup_skill(client)
    await client.post("/api/data-governance/assets", json={"id": "lc-asset", "name": "列表测试"})
    await client.post("/api/data-governance/contracts", json={
        "skill_id": "test-skill-001", "asset_id": "lc-asset",
    })

    resp = await client.get("/api/data-governance/contracts?skill_id=test-skill-001")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


# ═══════════════════════════ 合规检查 ═══════════════════════════

async def test_compliance_check_no_contracts(client):
    """无契约的 Skill 合规检查返回 compliant=True"""
    resp = await client.get("/api/data-governance/contracts/compliance/no-contracts-skill")
    assert resp.status_code == 200
    data = resp.json()
    assert data["compliant"] is True
    assert data["contracts_count"] == 0


async def test_compliance_check_field_missing(client):
    """契约中引用的字段在资产 schema 中不存在时报 fields_missing"""
    await _setup_skill(client)

    # 创建资产（schema 只有 date 和 revenue）
    await client.post("/api/data-governance/assets", json={
        "id": "compliance-asset",
        "name": "合规测试资产",
        "schema_def": [
            {"name": "date", "type": "date"},
            {"name": "revenue", "type": "number"},
        ],
    })

    # 契约声称使用 date + revenue + cost（cost 不在 schema 中）
    await client.post("/api/data-governance/contracts", json={
        "skill_id": "test-skill-001",
        "asset_id": "compliance-asset",
        "fields_used": ["date", "revenue", "cost"],
    })

    resp = await client.get("/api/data-governance/contracts/compliance/test-skill-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["compliant"] is False
    issue_types = [i["type"] for i in data["issues"]]
    assert "fields_missing" in issue_types


# ═══════════════════════════ 脱敏逻辑单元测试 ═══════════════════════════

async def test_apply_masking(client):
    """通过 governance_service.apply_masking 测试脱敏逻辑"""
    from app.database import async_session_factory
    from app.datasources.governance_models import DataAsset, DataPolicy
    from app.datasources import governance_service as gov_svc

    async with async_session_factory() as db:
        # 准备资产 + 脱敏策略
        db.add(DataAsset(id="masking-test-asset", name="脱敏测试"))
        db.add(DataPolicy(
            name="手机脱敏",
            policy_type="masking",
            target_type="asset",
            target_id="masking-test-asset",
            rules_json={"fields": ["phone"], "method": "partial_mask"},
            enabled=True,
        ))
        await db.commit()

        result = await gov_svc.apply_masking(
            {"phone": "13812345678", "name": "张三"},
            asset_id="masking-test-asset",
            db=db,
        )

    assert result["name"] == "张三"  # 非脱敏字段不变
    assert result["phone"] != "13812345678"  # 已脱敏
    assert result["phone"].startswith("1")  # partial_mask 保留首字符
    assert result["phone"].endswith("8")  # partial_mask 保留末字符
    assert "*" in result["phone"]


async def test_apply_masking_no_policy(client):
    """无脱敏策略时返回原始数据"""
    from app.database import async_session_factory
    from app.datasources.governance_models import DataAsset
    from app.datasources import governance_service as gov_svc

    async with async_session_factory() as db:
        db.add(DataAsset(id="no-policy-asset", name="无策略"))
        await db.commit()

        result = await gov_svc.apply_masking(
            {"phone": "13800000000"},
            asset_id="no-policy-asset",
            db=db,
        )

    assert result["phone"] == "13800000000"
