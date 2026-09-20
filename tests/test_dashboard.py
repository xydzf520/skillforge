"""效果看板 + 审计日志 API测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ===== 看板总览 =====


@pytest.mark.asyncio
async def test_overview(client):
    """测试GET /overview——返回执行统计"""
    resp = await client.get("/api/dashboard/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "executions" in data
    assert "active_skills" in data
    assert "time_saved_hours" in data
    assert "period_days" in data


@pytest.mark.asyncio
async def test_overview_with_days_param(client):
    """测试GET /overview——自定义天数参数"""
    resp = await client.get("/api/dashboard/overview?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period_days"] == 7


# ===== 采纳率 =====


@pytest.mark.asyncio
async def test_adoption(client):
    """测试GET /adoption——返回各Skill采纳率"""
    resp = await client.get("/api/dashboard/adoption")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # 即使无数据，也应返回空列表
    for item in data:
        assert "skill_id" in item
        assert "adoption_rate" in item
        assert "total" in item


# ===== 趋势数据 =====


@pytest.mark.asyncio
async def test_trends(client):
    """测试GET /trends——返回每日趋势"""
    resp = await client.get("/api/dashboard/trends")
    assert resp.status_code == 200
    data = resp.json()
    assert "dates" in data
    assert "executions" in data
    assert "adoptions" in data
    # 日期列表长度与数据列表长度应一致
    assert len(data["dates"]) == len(data["executions"])
    assert len(data["dates"]) == len(data["adoptions"])


# ===== 业务影响 =====


@pytest.mark.asyncio
async def test_impact(client):
    """测试GET /impact——返回业务影响统计"""
    resp = await client.get("/api/dashboard/impact")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total_amount" in data
    assert "top_skills" in data
    assert isinstance(data["items"], list)
    assert isinstance(data["top_skills"], list)


# ===== 周报 =====


@pytest.mark.asyncio
async def test_weekly_report(client):
    """测试GET /weekly-report——返回本周汇总"""
    resp = await client.get("/api/dashboard/weekly-report")
    assert resp.status_code == 200
    data = resp.json()
    assert "period" in data
    assert "total_executions" in data
    assert "success_rate" in data
    assert "adoption_rate" in data
    assert "time_saved_hours" in data
    assert "active_skills" in data
    assert "top_skills" in data
    assert "highlights" in data
    assert "trend_summary" in data
    assert isinstance(data["highlights"], list)


# ===== 审计统计 =====


@pytest.mark.asyncio
async def test_audit_stats(client):
    """测试GET /audit/stats——返回审计统计（admin权限）"""
    resp = await client.get("/api/audit/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "days" in data
    assert "total" in data
    assert "by_action" in data
    assert "security" in data
    assert "login_failures" in data["security"]
    assert "permission_denials" in data["security"]


# ===== 审计查询 =====


@pytest.mark.asyncio
async def test_audit_query(client):
    """测试GET /audit——查询审计日志"""
    resp = await client.get("/api/audit/")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "page" in data
    assert "items" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_audit_query_with_filters(client):
    """测试审计日志筛选"""
    resp = await client.get(
        "/api/audit/?action=skill.edit&page=1&page_size=10"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1


# ===== 审计导出CSV =====


@pytest.mark.asyncio
async def test_audit_export_csv(client):
    """测试GET /audit/export——导出审计日志CSV"""
    resp = await client.get("/api/audit/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    # CSV应该包含表头
    content = resp.text
    assert "id" in content
    assert "action" in content


# ===== 服务层直接测试 =====


@pytest.mark.asyncio
async def test_overview_service_with_department():
    """测试overview服务——按部门过滤"""
    from unittest.mock import MagicMock

    # 模拟数据库返回
    mock_runs_row = MagicMock()
    mock_runs_row.total = 100
    mock_runs_row.success = 80
    mock_runs_row.failed = 20

    mock_skills_scalar = 5

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            result.one.return_value = mock_runs_row
        else:
            result.scalar.return_value = mock_skills_scalar
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)

    from app.dashboard.service import get_overview
    result = await get_overview(mock_db, days=30, department="电商")

    assert result["executions"]["total"] == 100
    assert result["executions"]["success"] == 80
    assert result["active_skills"] == 5
    # 省时估算 = 80 * 15 / 60 = 20.0
    assert result["time_saved_hours"] == 20.0


@pytest.mark.asyncio
async def test_weekly_report_service():
    """测试周报服务——无数据时不崩溃"""
    mock_runs_row = MagicMock()
    mock_runs_row.total = 0
    mock_runs_row.success = 0

    mock_dec_row = MagicMock()
    mock_dec_row.total = 0
    mock_dec_row.adopted = 0

    # 创建多种mock返回值
    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            # 本周执行统计
            result.one.return_value = mock_runs_row
        elif call_count[0] in (2, 3):
            # 本周/上周采纳率
            result.one.return_value = mock_dec_row
        elif call_count[0] == 4:
            # 上周执行次数
            result.scalar.return_value = 0
            result.scalar_one_or_none.return_value = 0
        elif call_count[0] == 5:
            # 活跃Skill数
            result.scalar.return_value = 0
            result.scalar_one_or_none.return_value = 0
        elif call_count[0] == 6:
            # Top Skills
            result.all.return_value = []
        else:
            # 新上线Skills
            result.all.return_value = []
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)

    from app.dashboard.service import generate_weekly_report
    result = await generate_weekly_report(mock_db)

    assert result["total_executions"] == 0
    assert result["success_rate"] == 0
    assert result["adoption_rate"] == 0
    assert isinstance(result["highlights"], list)
    assert len(result["highlights"]) > 0  # 至少有"本周运行平稳"


# ===== 非admin用户访问审计——权限检查 =====


@pytest.mark.asyncio
async def test_audit_stats_non_admin():
    """测试非admin用户访问审计统计——应返回403"""
    from unittest.mock import MagicMock
    from httpx import ASGITransport, AsyncClient
    from app.common.exceptions import AppError, app_error_handler
    from app.auth.dependencies import get_current_user
    from fastapi import FastAPI

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    # mock非admin用户
    mock_user = MagicMock()
    mock_user.id = "biz_user"
    mock_user.role = "biz_owner"
    mock_user.department = "电商"
    mock_user.can_view_all = False
    mock_user.is_active = True
    app.dependency_overrides[get_current_user] = lambda: mock_user

    from app.audit.router import router as audit_router
    app.include_router(audit_router, prefix="/api/audit")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/audit/stats")
        assert resp.status_code == 403
