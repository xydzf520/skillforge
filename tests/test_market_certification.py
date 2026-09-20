"""市场分层可见性 + 认证流程 + 复用指标测试"""

import pytest
from unittest.mock import MagicMock


# ===== 辅助函数 =====

async def _create_skill(client, skill_id="TEST-SKILL-01", department="AI小组", market_status="private"):
    """在测试数据库中直接插入一条 Skill 记录"""
    import app.database as db_mod
    from app.skills.core.models import Skill

    async with db_mod.async_session_factory() as session:
        skill = Skill(
            id=skill_id,
            name=f"测试Skill-{skill_id}",
            department=department,
            status="active",
            market_status=market_status,
        )
        session.add(skill)
        await session.commit()


async def _get_skill(skill_id):
    """从数据库读取 Skill 记录"""
    import app.database as db_mod
    from sqlalchemy import select
    from app.skills.core.models import Skill

    async with db_mod.async_session_factory() as session:
        result = await session.execute(select(Skill).where(Skill.id == skill_id))
        return result.scalar_one_or_none()


# ===== 市场列表测试 =====

@pytest.mark.asyncio
async def test_list_market_empty(client):
    """空数据库时市场列表返回空"""
    resp = await client.get("/api/portal/market")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_market_with_skills(client):
    """有 Skill 时市场列表正常返回"""
    await _create_skill(client, "MKT-01", market_status="company_public")
    await _create_skill(client, "MKT-02", market_status="marketplace_listed")
    await _create_skill(client, "MKT-03", market_status="marketplace_certified")
    await _create_skill(client, "MKT-04", market_status="private")

    resp = await client.get("/api/portal/market")
    assert resp.status_code == 200
    data = resp.json()
    # 管理员能看到所有状态（包括 private）
    assert data["total"] == 4


@pytest.mark.asyncio
async def test_list_market_certified_only(client):
    """certified_only 参数只返回已认证 Skill"""
    await _create_skill(client, "CERT-01", market_status="marketplace_certified")
    await _create_skill(client, "CERT-02", market_status="marketplace_listed")

    resp = await client.get("/api/portal/market?certified_only=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == "CERT-01"


@pytest.mark.asyncio
async def test_list_market_by_category(client):
    """按部门分类筛选"""
    await _create_skill(client, "CAT-01", department="EC", market_status="company_public")
    await _create_skill(client, "CAT-02", department="运营", market_status="company_public")

    resp = await client.get("/api/portal/market?category=EC")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["department"] == "EC"


# ===== 认证提交测试 =====

@pytest.mark.asyncio
async def test_submit_certification(client):
    """提交认证申请"""
    await _create_skill(client, "CERT-SUBMIT-01", market_status="marketplace_listed")

    resp = await client.post("/api/portal/market/CERT-SUBMIT-01/certify")
    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_id"] == "CERT-SUBMIT-01"
    assert data["status"] == "pending"
    assert data["submitted_by"] == "admin"


@pytest.mark.asyncio
async def test_submit_certification_not_listed(client):
    """未上架的 Skill 不能提交认证"""
    await _create_skill(client, "CERT-PRIV-01", market_status="private")

    resp = await client.post("/api/portal/market/CERT-PRIV-01/certify")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MARKET_NOT_LISTED"


@pytest.mark.asyncio
async def test_submit_certification_duplicate(client):
    """重复提交认证应返回错误"""
    await _create_skill(client, "CERT-DUP-01", market_status="marketplace_listed")

    resp1 = await client.post("/api/portal/market/CERT-DUP-01/certify")
    assert resp1.status_code == 200

    resp2 = await client.post("/api/portal/market/CERT-DUP-01/certify")
    assert resp2.status_code == 400
    assert resp2.json()["error"]["code"] == "MARKET_CERT_PENDING"


@pytest.mark.asyncio
async def test_submit_certification_skill_not_found(client):
    """不存在的 Skill 返回 404"""
    resp = await client.post("/api/portal/market/NOT-EXIST-99/certify")
    assert resp.status_code == 404


# ===== 认证审核测试 =====

@pytest.mark.asyncio
async def test_review_certification_approve(client):
    """审核通过后 Skill 变为 marketplace_certified"""
    await _create_skill(client, "REV-APPROVE-01", market_status="marketplace_listed")

    # 提交认证
    submit_resp = await client.post("/api/portal/market/REV-APPROVE-01/certify")
    cert_id = submit_resp.json()["id"]

    # 审核通过（mock 用户是 admin，submitted_by 也是 admin，需要不同用户）
    # 因为 conftest mock_admin 的 id 是 "admin"，submitted_by 也是 "admin"
    # 修改 submitted_by 来绕过自审限制
    import app.database as db_mod
    from sqlalchemy import select
    from app.portal.market_models import MarketCertification
    async with db_mod.async_session_factory() as session:
        result = await session.execute(
            select(MarketCertification).where(MarketCertification.id == cert_id)
        )
        cert = result.scalar_one()
        cert.submitted_by = "other_user"
        await session.commit()

    resp = await client.post(
        f"/api/portal/market/certifications/{cert_id}/review",
        json={"decision": "approved", "notes": "质量优秀"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["certified_at"] is not None

    # 验证 Skill 的 market_status 已更新
    skill = await _get_skill("REV-APPROVE-01")
    assert skill.market_status == "marketplace_certified"


@pytest.mark.asyncio
async def test_review_certification_reject(client):
    """审核拒绝"""
    await _create_skill(client, "REV-REJECT-01", market_status="marketplace_listed")

    submit_resp = await client.post("/api/portal/market/REV-REJECT-01/certify")
    cert_id = submit_resp.json()["id"]

    # 修改 submitted_by 来绕过自审限制
    import app.database as db_mod
    from sqlalchemy import select
    from app.portal.market_models import MarketCertification
    async with db_mod.async_session_factory() as session:
        result = await session.execute(
            select(MarketCertification).where(MarketCertification.id == cert_id)
        )
        cert = result.scalar_one()
        cert.submitted_by = "other_user"
        await session.commit()

    resp = await client.post(
        f"/api/portal/market/certifications/{cert_id}/review",
        json={"decision": "rejected", "notes": "缺少测试用例"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert data["review_notes"] == "缺少测试用例"


@pytest.mark.asyncio
async def test_review_certification_self_approve(client):
    """不能审核自己提交的认证"""
    await _create_skill(client, "REV-SELF-01", market_status="marketplace_listed")

    submit_resp = await client.post("/api/portal/market/REV-SELF-01/certify")
    cert_id = submit_resp.json()["id"]

    resp = await client.post(
        f"/api/portal/market/certifications/{cert_id}/review",
        json={"decision": "approved"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "REVIEW_SELF_APPROVE"


@pytest.mark.asyncio
async def test_review_certification_not_found(client):
    """认证记录不存在"""
    resp = await client.post(
        "/api/portal/market/certifications/99999/review",
        json={"decision": "approved"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_review_certification_invalid_decision(client):
    """无效的审核决策"""
    await _create_skill(client, "REV-INV-01", market_status="marketplace_listed")

    submit_resp = await client.post("/api/portal/market/REV-INV-01/certify")
    cert_id = submit_resp.json()["id"]

    resp = await client.post(
        f"/api/portal/market/certifications/{cert_id}/review",
        json={"decision": "maybe"},
    )
    assert resp.status_code == 400


# ===== 复用指标测试 =====

@pytest.mark.asyncio
async def test_reuse_metrics(client):
    """获取复用指标（空数据）"""
    await _create_skill(client, "METRIC-01")

    resp = await client.get("/api/portal/market/METRIC-01/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_id"] == "METRIC-01"
    assert data["fork_count"] == 0
    assert data["execution_count"] == 0
    assert data["unique_users"] == 0
    assert data["departments_using"] >= 1


@pytest.mark.asyncio
async def test_reuse_metrics_not_found(client):
    """不存在的 Skill 返回 404"""
    resp = await client.get("/api/portal/market/NOT-EXIST-99/metrics")
    assert resp.status_code == 404


# ===== 可见性修改测试 =====

@pytest.mark.asyncio
async def test_update_visibility(client):
    """修改 Skill 可见性"""
    await _create_skill(client, "VIS-01", market_status="private")

    resp = await client.put(
        "/api/portal/market/VIS-01/visibility",
        json={"market_status": "company_public"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["market_status"] == "company_public"
    assert data["previous_status"] == "private"


@pytest.mark.asyncio
async def test_update_visibility_invalid_status(client):
    """无效的可见性状态"""
    await _create_skill(client, "VIS-INV-01")

    resp = await client.put(
        "/api/portal/market/VIS-INV-01/visibility",
        json={"market_status": "invalid_status"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_visibility_skill_not_found(client):
    """不存在的 Skill"""
    resp = await client.put(
        "/api/portal/market/NOT-EXIST-99/visibility",
        json={"market_status": "company_public"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_visibility_missing_param(client):
    """缺少参数"""
    await _create_skill(client, "VIS-MISS-01")

    resp = await client.put(
        "/api/portal/market/VIS-MISS-01/visibility",
        json={},
    )
    assert resp.status_code == 400
