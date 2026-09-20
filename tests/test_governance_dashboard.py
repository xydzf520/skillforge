"""治理看板聚合服务测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ===== API 端点测试 =====


@pytest.mark.asyncio
async def test_governance_endpoint_returns_200(client):
    """GET /api/dashboard/governance 返回 200 且结构完整"""
    resp = await client.get("/api/dashboard/governance")
    assert resp.status_code == 200
    data = resp.json()

    # 基础计数
    assert "workers" in data
    assert "approvals_pending" in data

    # 新增聚合块
    assert "approval_efficiency" in data
    assert "cost_attribution" in data
    assert "data_governance" in data
    assert "skill_health" in data
    assert "recent_events" in data


@pytest.mark.asyncio
async def test_governance_workers_structure(client):
    """workers 子结构包含 total / online / stale"""
    resp = await client.get("/api/dashboard/governance")
    data = resp.json()
    w = data["workers"]
    assert "total" in w
    assert "online" in w
    assert "stale" in w
    assert isinstance(w["total"], int)
    assert isinstance(w["online"], int)


@pytest.mark.asyncio
async def test_governance_approval_efficiency_structure(client):
    """approval_efficiency 结构校验"""
    resp = await client.get("/api/dashboard/governance")
    ae = resp.json()["approval_efficiency"]
    assert "avg_decision_hours" in ae
    assert "pending_over_24h" in ae
    assert "approval_rate_30d" in ae
    assert "rejection_rate_30d" in ae
    assert isinstance(ae["avg_decision_hours"], (int, float))
    assert isinstance(ae["pending_over_24h"], int)


@pytest.mark.asyncio
async def test_governance_cost_attribution_structure(client):
    """cost_attribution 结构校验"""
    resp = await client.get("/api/dashboard/governance")
    ca = resp.json()["cost_attribution"]
    assert "total_executions_30d" in ca
    assert "by_department" in ca
    assert "top_skills" in ca
    assert isinstance(ca["by_department"], list)
    assert isinstance(ca["top_skills"], list)


@pytest.mark.asyncio
async def test_governance_data_governance_structure(client):
    """data_governance 结构校验"""
    resp = await client.get("/api/dashboard/governance")
    dg = resp.json()["data_governance"]
    assert "total_sources" in dg
    assert "stale_sources" in dg
    assert "pending_imports" in dg
    assert isinstance(dg["total_sources"], int)


@pytest.mark.asyncio
async def test_governance_skill_health_structure(client):
    """skill_health 结构校验"""
    resp = await client.get("/api/dashboard/governance")
    sh = resp.json()["skill_health"]
    assert "total" in sh
    assert "by_status" in sh
    assert "avg_health_score" in sh
    assert "low_health_count" in sh
    assert "no_test_cases" in sh
    assert isinstance(sh["by_status"], dict)


@pytest.mark.asyncio
async def test_governance_recent_events_structure(client):
    """recent_events 结构校验"""
    resp = await client.get("/api/dashboard/governance")
    events = resp.json()["recent_events"]
    assert isinstance(events, list)
    assert len(events) <= 10


@pytest.mark.asyncio
async def test_governance_empty_data_no_crash(client):
    """空数据库不应崩溃"""
    resp = await client.get("/api/dashboard/governance")
    assert resp.status_code == 200
    data = resp.json()
    # 空数据时所有计数字段应为 0 或空列表
    assert data["approvals_pending"] == 0
    assert data["workers"]["total"] == 0
    assert data["cost_attribution"]["total_executions_30d"] == 0
    assert data["data_governance"]["total_sources"] == 0
    assert data["skill_health"]["total"] == 0
    assert data["recent_events"] == []


# ===== 服务层直接测试（带 mock） =====


@pytest.mark.asyncio
async def test_governance_service_department_filter():
    """测试部门过滤参数被传递（mock 级别）"""
    mock_db = AsyncMock()

    # 所有 execute 返回合理的空结果
    def make_result():
        r = MagicMock()
        r.scalar.return_value = 0
        r.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
        r.all.return_value = []
        # 用于审批效率中的 .one() 调用
        row = MagicMock()
        row.total = 0
        row.approved = 0
        row.rejected = 0
        r.one.return_value = row
        return r

    mock_db.execute = AsyncMock(side_effect=lambda stmt: make_result())

    from app.dashboard.service import get_governance_overview
    result = await get_governance_overview(mock_db, department="EC")

    # 不崩溃、结构完整即通过
    assert "workers" in result
    assert "approval_efficiency" in result
    assert "cost_attribution" in result
    assert "data_governance" in result
    assert "skill_health" in result
    assert "recent_events" in result


@pytest.mark.asyncio
async def test_governance_service_no_department():
    """无部门过滤时也正常"""
    mock_db = AsyncMock()

    def make_result():
        r = MagicMock()
        r.scalar.return_value = 0
        r.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
        r.all.return_value = []
        row = MagicMock()
        row.total = 0
        row.approved = 0
        row.rejected = 0
        r.one.return_value = row
        return r

    mock_db.execute = AsyncMock(side_effect=lambda stmt: make_result())

    from app.dashboard.service import get_governance_overview
    result = await get_governance_overview(mock_db, department=None)

    assert result["approvals_pending"] == 0
    assert result["approval_efficiency"]["avg_decision_hours"] == 0.0
    assert result["approval_efficiency"]["pending_over_24h"] == 0
    assert result["cost_attribution"]["total_executions_30d"] == 0
    assert result["cost_attribution"]["by_department"] == []
    assert result["cost_attribution"]["top_skills"] == []
