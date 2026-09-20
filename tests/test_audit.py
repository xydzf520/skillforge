"""审计日志模块测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_mock_session(execute_side_effect=None):
    """创建mock async session（用于AuditLogger内部的async with调用）"""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    if execute_side_effect:
        mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        mock_session.execute = AsyncMock()
    return mock_session


def _make_mock_factory(mock_session):
    """创建mock async_session_factory，返回可用于async with的对象"""
    mock_factory = MagicMock()
    # async_session_factory() 返回一个async context manager
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_factory.return_value = mock_ctx
    return mock_factory


# ===== 审计日志写入和查询 =====


@pytest.mark.asyncio
async def test_audit_log_write():
    """测试审计日志写入"""
    from app.common.audit import AuditLog

    mock_session = _make_mock_session()
    mock_factory = _make_mock_factory(mock_session)

    with patch("app.database.async_session_factory", mock_factory):
        from app.common.audit import AuditLogger
        logger = AuditLogger()
        await logger.log(
            user_id="admin",
            action="skill.create",
            target_type="skill",
            target_id="EC-TEST-01",
            detail={"name": "测试Skill"},
            ip_address="127.0.0.1",
        )

    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()

    # 验证添加的AuditLog对象
    added_obj = mock_session.add.call_args[0][0]
    assert isinstance(added_obj, AuditLog)
    assert added_obj.user_id == "admin"
    assert added_obj.action == "skill.create"
    assert added_obj.target_type == "skill"
    assert added_obj.target_id == "EC-TEST-01"
    assert added_obj.ip_address == "127.0.0.1"


@pytest.mark.asyncio
async def test_audit_log_query():
    """测试审计日志查询——分页+筛选"""
    from app.common.audit import AuditLog

    mock_item = MagicMock(spec=AuditLog)
    mock_item.id = 1
    mock_item.user_id = "admin"
    mock_item.action = "skill.create"
    mock_item.target_type = "skill"
    mock_item.target_id = "EC-01"
    mock_item.detail = {"name": "测试"}
    mock_item.ip_address = "127.0.0.1"
    mock_item.created_at = MagicMock()
    mock_item.created_at.isoformat.return_value = "2026-03-01T10:00:00"

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            # count查询
            result.scalar.return_value = 1
        else:
            # 数据查询
            result.scalars.return_value.all.return_value = [mock_item]
        return result

    mock_session = _make_mock_session(execute_side_effect=mock_execute)
    mock_factory = _make_mock_factory(mock_session)

    with patch("app.database.async_session_factory", mock_factory):
        from app.common.audit import AuditLogger
        logger = AuditLogger()
        result = await logger.query(
            action="skill.create",
            page=1,
            page_size=10,
        )

    assert result["total"] == 1
    assert result["page"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skill.create"
    assert result["items"][0]["user_id"] == "admin"


@pytest.mark.asyncio
async def test_audit_log_query_empty():
    """测试审计日志查询——无数据时返回空"""
    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            result.scalar.return_value = 0
        else:
            result.scalars.return_value.all.return_value = []
        return result

    mock_session = _make_mock_session(execute_side_effect=mock_execute)
    mock_factory = _make_mock_factory(mock_session)

    with patch("app.database.async_session_factory", mock_factory):
        from app.common.audit import AuditLogger
        logger = AuditLogger()
        result = await logger.query()

    assert result["total"] == 0
    assert result["items"] == []


# ===== 审计统计 =====


@pytest.mark.asyncio
async def test_audit_stats():
    """测试审计统计——按action分组计数"""
    mock_rows = [
        ("skill.create", 10),
        ("skill.edit", 25),
        ("user.login", 100),
        ("user.login_failed", 3),
        ("auth.permission_denied", 1),
    ]

    mock_result = MagicMock()
    mock_result.all.return_value = mock_rows

    mock_session = _make_mock_session()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_factory = _make_mock_factory(mock_session)

    with patch("app.database.async_session_factory", mock_factory):
        from app.common.audit import AuditLogger
        logger = AuditLogger()
        result = await logger.get_stats(days=30)

    assert result["days"] == 30
    assert result["total"] == 139
    assert result["by_action"]["skill.create"] == 10
    assert result["by_action"]["skill.edit"] == 25
    assert result["security"]["login_failures"] == 3
    assert result["security"]["permission_denials"] == 1
    assert result["security"]["total"] == 4  # 3 + 1


@pytest.mark.asyncio
async def test_audit_stats_no_security_events():
    """测试审计统计——无安全事件"""
    mock_rows = [
        ("skill.create", 5),
        ("user.login", 20),
    ]

    mock_result = MagicMock()
    mock_result.all.return_value = mock_rows

    mock_session = _make_mock_session()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_factory = _make_mock_factory(mock_session)

    with patch("app.database.async_session_factory", mock_factory):
        from app.common.audit import AuditLogger
        logger = AuditLogger()
        result = await logger.get_stats(days=7)

    assert result["security"]["total"] == 0
    assert result["security"]["login_failures"] == 0


# ===== 审计日志导出CSV =====


@pytest.mark.asyncio
async def test_audit_export_csv():
    """测试审计日志导出为CSV"""
    from app.common.audit import AuditLog

    mock_item = MagicMock(spec=AuditLog)
    mock_item.id = 1
    mock_item.user_id = "admin"
    mock_item.action = "skill.create"
    mock_item.target_type = "skill"
    mock_item.target_id = "EC-01"
    mock_item.detail = {"name": "测试"}
    mock_item.ip_address = "127.0.0.1"
    mock_item.created_at = MagicMock()
    mock_item.created_at.isoformat.return_value = "2026-03-01T10:00:00"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_item]

    mock_session = _make_mock_session()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_factory = _make_mock_factory(mock_session)

    with patch("app.database.async_session_factory", mock_factory):
        from app.common.audit import AuditLogger
        logger = AuditLogger()
        csv_content = await logger.export_csv(action="skill.create")

    # 检查CSV包含表头
    assert "id" in csv_content
    assert "user_id" in csv_content
    assert "action" in csv_content

    # 检查CSV包含数据行
    assert "admin" in csv_content
    assert "skill.create" in csv_content
    assert "EC-01" in csv_content

    # CSV应该包含多行（表头+数据行）
    lines = csv_content.strip().split("\n")
    assert len(lines) >= 2


@pytest.mark.asyncio
async def test_audit_export_csv_empty():
    """测试审计日志导出——无数据时只有表头"""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []

    mock_session = _make_mock_session()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_factory = _make_mock_factory(mock_session)

    with patch("app.database.async_session_factory", mock_factory):
        from app.common.audit import AuditLogger
        logger = AuditLogger()
        csv_content = await logger.export_csv()

    # 只有表头行
    lines = csv_content.strip().split("\n")
    assert len(lines) == 1
    assert "id" in lines[0]


# ===== HTTP端点测试 =====


@pytest.mark.asyncio
async def test_audit_query_endpoint(client):
    """测试GET /api/audit/——HTTP端点"""
    resp = await client.get("/api/audit/")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "items" in data


@pytest.mark.asyncio
async def test_audit_stats_endpoint(client):
    """测试GET /api/audit/stats——HTTP端点"""
    resp = await client.get("/api/audit/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "security" in data


@pytest.mark.asyncio
async def test_audit_export_endpoint(client):
    """测试GET /api/audit/export——HTTP端点"""
    resp = await client.get("/api/audit/export")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]


# ===== 审计日志查询——参数组合 =====


@pytest.mark.asyncio
async def test_audit_query_with_all_filters():
    """测试审计日志查询——多条件组合筛选"""
    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            result.scalar.return_value = 0
        else:
            result.scalars.return_value.all.return_value = []
        return result

    mock_session = _make_mock_session(execute_side_effect=mock_execute)
    mock_factory = _make_mock_factory(mock_session)

    with patch("app.database.async_session_factory", mock_factory):
        from app.common.audit import AuditLogger
        logger = AuditLogger()
        result = await logger.query(
            action="skill.edit",
            user_id="admin",
            target_type="skill",
            target_id="EC-01",
            date_from="2026-01-01",
            date_to="2026-03-31",
            page=2,
            page_size=25,
        )

    assert result["page"] == 2
    assert result["page_size"] == 25
    # 验证execute被调用了两次（count + data）
    assert mock_session.execute.call_count == 2


# ===== 防回归: service-id 规范 (role-matrix-v2 §12) =====


def test_audit_service_ids_use_svc_prefix():
    """禁止 audit.log 第一个参数用字面量 "system"——应统一使用 svc_xxx 规范。

    role-matrix-v2 §12 要求：系统触发的审计必须用 svc_<module>（如 svc_execution / svc_reviews）
    而不是隐式的 "system"，否则审计上下文丢失、无法区分来源模块。

    扫 app/ 下所有 .py 文件，任何 audit.log("system", ...) / audit.log('system', ...)
    形式的调用都视为违规。
    """
    import re
    from pathlib import Path

    pattern = re.compile(r'''audit\.log\(\s*["']system["']''')
    app_root = Path(__file__).parent.parent / "app"
    violations: list[str] = []
    for py_file in app_root.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                rel = py_file.relative_to(app_root.parent)
                violations.append(f"{rel}:{line_no}: {line.strip()}")

    assert not violations, (
        "audit.log 第一个参数不得用字面量 'system'，违规点：\n  - "
        + "\n  - ".join(violations)
    )
