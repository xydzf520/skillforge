"""M2 数据访问授权测试。"""

import pytest


@pytest.mark.asyncio
async def test_create_high_sensitivity_source_and_request_access(client):
    create_resp = await client.post("/api/data-sources/", json={
        "source_id": "ds-sensitive-1",
        "name": "高敏销售数据",
        "department": "总部",
        "source_type": "csv_upload",
        "config": {},
        "sensitivity": "L3",
        "owner_org_unit_id": "总部",
        "owner_contact": "owner-other",
    })
    assert create_resp.status_code == 200

    request_resp = await client.post("/api/data-sources/ds-sensitive-1/request-access", json={
        "reason": "需要查看经营指标并完成月度经营分析复核工作",
    })
    assert request_resp.status_code == 200
    data = request_resp.json()
    assert data["status"] == "pending"
    assert data["id"] > 0
    assert "request_id" not in data

    # 当前契约：重复申请命中幂等，仍返回 {id, status} 主键对
    request_resp_2 = await client.post("/api/data-sources/ds-sensitive-1/request-access", json={
        "reason": "需要查看经营指标并完成月度经营分析复核工作",
    })
    assert request_resp_2.status_code == 200
    data_2 = request_resp_2.json()
    assert data_2["status"] == "pending"
    assert data_2["id"] == data["id"]
    assert data_2["idempotent"] is True


@pytest.mark.asyncio
async def test_access_grant_transitions_after_approval(client):
    await client.post("/api/data-sources/", json={
        "source_id": "ds-sensitive-2",
        "name": "高敏库存数据",
        "department": "总部",
        "source_type": "csv_upload",
        "config": {},
        "sensitivity": "L3",
        "owner_org_unit_id": "其他部门",
        "owner_contact": "owner-other",
    })
    req = await client.post("/api/data-sources/ds-sensitive-2/request-access", json={
        "reason": "需要复核高敏库存数据并核对跨部门共享口径",
    })
    request_id = req.json()["id"]

    decide = await client.post(f"/api/data-sources/requests/{request_id}/approve", json={
        "comment": "允许访问",
    })
    assert decide.status_code == 200
    decide_data = decide.json()
    assert decide_data["status"] == "approved"
    assert decide_data["request_id"] == request_id
    assert decide_data["grant_id"] > 0
    assert decide_data["expires_at"]

    grants = await client.get("/api/data-sources/ds-sensitive-2/access-grants")
    assert grants.status_code == 200
    data = grants.json()
    assert data["total"] == 1
    assert data["items"][0]["source_id"] == "ds-sensitive-2"
    assert data["items"][0]["grantee_user_id"] == "admin"
    assert data["items"][0]["grantee_type"] == "user"
    assert data["items"][0]["grantee_id"] == "admin"


@pytest.mark.asyncio
async def test_dashboard_quota_endpoint_still_available(client):
    resp = await client.get("/api/approval/quotas")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
