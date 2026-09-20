"""合规规则模块测试"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, text


# ===== 创建规则 =====


@pytest.mark.asyncio
async def test_create_rule():
    """测试创建合规规则"""
    mock_existing = MagicMock()
    mock_existing.scalar_one_or_none.return_value = None  # 不存在

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_existing)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    with patch("app.compliance.service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.compliance.service import create_rule
        result = await create_rule(
            mock_db,
            rule_id="R-001",
            platform="tmall",
            surface="title",
            category_scope="食品",
            trigger_type="keyword",
            pattern_value="最好",
            severity="P1",
            decision="block",
            user_id="admin",
        )

    assert result["id"] == "R-001"
    assert result["platform"] == "tmall"
    assert result["trigger_type"] == "keyword"
    mock_db.add.assert_called_once()


@pytest.mark.asyncio
async def test_create_rule_duplicate():
    """测试创建重复规则——应返回409"""
    from app.common.exceptions import AppError

    existing_rule = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_rule

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(AppError) as exc_info:
        from app.compliance.service import create_rule
        await create_rule(
            mock_db,
            rule_id="R-001",
            platform="tmall",
            surface="title",
            category_scope=None,
            trigger_type="keyword",
            pattern_value="最好",
            severity="P1",
            decision="block",
        )

    assert exc_info.value.code == "COMPLIANCE_RULE_EXISTS"


@pytest.mark.asyncio
async def test_create_rule_invalid_regex():
    """测试创建含无效正则的规则——应返回400"""
    from app.common.exceptions import AppError

    mock_existing = MagicMock()
    mock_existing.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_existing)

    with pytest.raises(AppError) as exc_info:
        from app.compliance.service import create_rule
        await create_rule(
            mock_db,
            rule_id="R-BAD",
            platform="tmall",
            surface="title",
            category_scope=None,
            trigger_type="regex",
            pattern_value="[invalid((",  # 无效正则
            severity="P1",
            decision="block",
        )

    assert exc_info.value.code == "COMPLIANCE_INVALID_REGEX"


# ===== 列表+筛选 =====


@pytest.mark.asyncio
async def test_list_rules():
    """测试列出合规规则——分页+筛选"""
    from app.common.models import ComplianceRule

    mock_rule = MagicMock(spec=ComplianceRule)
    mock_rule.id = "R-001"
    mock_rule.platform = "tmall"
    mock_rule.surface = "title"
    mock_rule.category_scope = "食品"
    mock_rule.trigger_type = "keyword"
    mock_rule.pattern_value = "最好"
    mock_rule.severity = "P1"
    mock_rule.decision = "block"
    mock_rule.rewrite_suggestion = None
    mock_rule.required_evidence = None
    mock_rule.effective_from = None
    mock_rule.effective_to = None
    mock_rule.source_url = None
    mock_rule.owner = None
    mock_rule.created_at = None
    mock_rule.updated_at = None

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            result.scalar.return_value = 1  # total count
        else:
            result.scalars.return_value.all.return_value = [mock_rule]
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)

    from app.compliance.service import list_rules
    result = await list_rules(mock_db, platform="tmall")

    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == "R-001"


# ===== 更新规则 =====


@pytest.mark.asyncio
async def test_update_rule():
    """测试更新合规规则"""
    from app.common.models import ComplianceRule

    mock_rule = MagicMock(spec=ComplianceRule)
    mock_rule.id = "R-001"
    mock_rule.platform = "tmall"
    mock_rule.surface = "title"
    mock_rule.category_scope = None
    mock_rule.trigger_type = "keyword"
    mock_rule.pattern_value = "最好"
    mock_rule.severity = "P1"
    mock_rule.decision = "block"
    mock_rule.rewrite_suggestion = None
    mock_rule.required_evidence = None
    mock_rule.effective_from = None
    mock_rule.effective_to = None
    mock_rule.source_url = None
    mock_rule.owner = None
    mock_rule.created_at = None
    mock_rule.updated_at = None

    mock_rule_result = MagicMock()
    mock_rule_result.scalar_one_or_none.return_value = mock_rule
    mock_version_result = MagicMock()
    mock_version_result.scalar_one.return_value = 0

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[mock_rule_result, mock_version_result])
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    with patch("app.compliance.service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.compliance.service import update_rule
        result = await update_rule(
            mock_db, "R-001", user_id="admin",
            severity="P0", decision="rewrite",
        )

    assert result["id"] == "R-001"
    # 验证字段被更新
    assert mock_rule.severity == "P0"
    assert mock_rule.decision == "rewrite"


@pytest.mark.asyncio
async def test_update_rule_not_found():
    """测试更新不存在的规则——应返回404"""
    from app.common.exceptions import AppError

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(AppError) as exc_info:
        from app.compliance.service import update_rule
        await update_rule(mock_db, "NOT-EXIST", severity="P0")

    assert exc_info.value.code == "COMPLIANCE_RULE_NOT_FOUND"


# ===== 删除规则 =====


@pytest.mark.asyncio
async def test_delete_rule():
    """测试删除合规规则"""
    mock_rule = MagicMock()
    mock_rule.id = "R-DEL"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_rule

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.delete = AsyncMock()
    mock_db.flush = AsyncMock()

    with patch("app.compliance.service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.compliance.service import delete_rule
        result = await delete_rule(mock_db, "R-DEL", "admin")

    assert result["deleted"] == "R-DEL"
    mock_db.delete.assert_called_once_with(mock_rule)


@pytest.mark.asyncio
async def test_delete_rule_not_found():
    """测试删除不存在的规则——应返回404"""
    from app.common.exceptions import AppError

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(AppError) as exc_info:
        from app.compliance.service import delete_rule
        await delete_rule(mock_db, "NOT-EXIST", "admin")

    assert exc_info.value.code == "COMPLIANCE_RULE_NOT_FOUND"


# ===== 内容合规检查——关键词匹配 =====


@pytest.mark.asyncio
async def test_check_content_keyword_match():
    """测试内容检查——关键词匹配命中"""
    from app.common.models import ComplianceRule

    mock_rule = MagicMock(spec=ComplianceRule)
    mock_rule.id = "R-KW-01"
    mock_rule.platform = "tmall"
    mock_rule.surface = "title"
    mock_rule.category_scope = None
    mock_rule.trigger_type = "keyword"
    mock_rule.pattern_value = "最好"
    mock_rule.severity = "P1"
    mock_rule.decision = "block"
    mock_rule.rewrite_suggestion = "删除'最好'"
    mock_rule.required_evidence = None
    mock_rule.effective_from = None
    mock_rule.effective_to = None
    mock_rule.source_url = None
    mock_rule.owner = None
    mock_rule.created_at = None
    mock_rule.updated_at = None

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_rule]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    from app.compliance.service import check_content
    violations = await check_content(mock_db, "这是最好的产品", platform="tmall")

    assert len(violations) == 1
    assert violations[0]["id"] == "R-KW-01"


@pytest.mark.asyncio
async def test_check_content_keyword_no_match():
    """测试内容检查——关键词不匹配"""
    from app.common.models import ComplianceRule

    mock_rule = MagicMock(spec=ComplianceRule)
    mock_rule.id = "R-KW-02"
    mock_rule.platform = "tmall"
    mock_rule.trigger_type = "keyword"
    mock_rule.pattern_value = "最好"
    mock_rule.effective_from = None
    mock_rule.effective_to = None

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_rule]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    from app.compliance.service import check_content
    violations = await check_content(mock_db, "这是一款优质产品", platform="tmall")

    assert len(violations) == 0


# ===== 内容合规检查——正则匹配 =====


@pytest.mark.asyncio
async def test_check_content_regex_match():
    """测试内容检查——正则表达式匹配命中"""
    from app.common.models import ComplianceRule

    mock_rule = MagicMock(spec=ComplianceRule)
    mock_rule.id = "R-RE-01"
    mock_rule.platform = "all"
    mock_rule.surface = "title"
    mock_rule.category_scope = None
    mock_rule.trigger_type = "regex"
    mock_rule.pattern_value = r"第一|NO\.?\s*1"
    mock_rule.severity = "P0"
    mock_rule.decision = "block"
    mock_rule.rewrite_suggestion = None
    mock_rule.required_evidence = None
    mock_rule.effective_from = None
    mock_rule.effective_to = None
    mock_rule.source_url = None
    mock_rule.owner = None
    mock_rule.created_at = None
    mock_rule.updated_at = None

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_rule]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    from app.compliance.service import check_content
    violations = await check_content(mock_db, "销量第一的品牌", platform="tmall")

    assert len(violations) == 1
    assert violations[0]["severity"] == "P0"


@pytest.mark.asyncio
async def test_check_content_regex_no_match():
    """测试内容检查——正则不匹配"""
    from app.common.models import ComplianceRule

    mock_rule = MagicMock(spec=ComplianceRule)
    mock_rule.id = "R-RE-02"
    mock_rule.platform = "all"
    mock_rule.trigger_type = "regex"
    mock_rule.pattern_value = r"第一|NO\.?\s*1"
    mock_rule.effective_from = None
    mock_rule.effective_to = None

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_rule]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    from app.compliance.service import check_content
    violations = await check_content(mock_db, "优质品牌推荐", platform="tmall")

    assert len(violations) == 0


# ===== 导入CSV =====


@pytest.mark.asyncio
async def test_import_rules_csv():
    """测试从CSV导入合规规则"""
    csv_content = (
        "id,platform,surface,trigger_type,pattern_value,severity,decision\n"
        "IMP-001,tmall,title,keyword,最好,P1,block\n"
        "IMP-002,jd,title,keyword,第一,P0,block\n"
    ).encode("utf-8")

    # 模拟空的已有规则集
    mock_existing_result = MagicMock()
    mock_existing_result.all.return_value = []

    mock_id_result = MagicMock()
    mock_id_result.all.return_value = []

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_existing_result
        elif call_count[0] == 2:
            return mock_id_result
        return MagicMock()

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    with patch("app.compliance.service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.compliance.service import import_from_csv
        result = await import_from_csv(mock_db, csv_content, "admin")

    assert result["imported"] == 2
    assert result["skipped"] == 0
    assert len(result["errors"]) == 0


@pytest.mark.asyncio
async def test_import_rules_csv_with_errors():
    """测试导入CSV——有校验错误的行"""
    csv_content = (
        "id,platform,surface,trigger_type,pattern_value,severity,decision\n"
        "IMP-E1,invalid_platform,title,keyword,测试,P1,block\n"
        "IMP-E2,tmall,title,keyword,合法,P1,block\n"
    ).encode("utf-8")

    mock_existing_result = MagicMock()
    mock_existing_result.all.return_value = []
    mock_id_result = MagicMock()
    mock_id_result.all.return_value = []

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_existing_result
        elif call_count[0] == 2:
            return mock_id_result
        return MagicMock()

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    with patch("app.compliance.service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.compliance.service import import_from_csv
        result = await import_from_csv(mock_db, csv_content, "admin")

    assert result["imported"] == 1  # 只有合法行被导入
    assert len(result["errors"]) == 1  # invalid_platform出错


# ===== 导出CSV =====


@pytest.mark.asyncio
async def test_export_rules_csv():
    """测试导出合规规则为CSV"""
    from app.common.models import ComplianceRule

    mock_rule = MagicMock(spec=ComplianceRule)
    mock_rule.id = "EXP-001"
    mock_rule.platform = "tmall"
    mock_rule.surface = "title"
    mock_rule.category_scope = "食品"
    mock_rule.trigger_type = "keyword"
    mock_rule.pattern_value = "最好"
    mock_rule.severity = "P1"
    mock_rule.decision = "block"
    mock_rule.rewrite_suggestion = "删除最好"
    mock_rule.required_evidence = None
    mock_rule.effective_from = None
    mock_rule.effective_to = None
    mock_rule.source_url = None
    mock_rule.owner = "admin"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_rule]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    from app.compliance.service import export_csv
    csv_output = await export_csv(mock_db)

    assert "EXP-001" in csv_output
    assert "tmall" in csv_output
    assert "最好" in csv_output
    # 检查含有BOM头
    assert csv_output.startswith("\ufeff")


# ===== 有效期过滤 =====


@pytest.mark.asyncio
async def test_check_content_expired_rule_skipped():
    """测试内容检查——过期规则应被跳过"""
    from datetime import date, timedelta
    from app.common.models import ComplianceRule

    mock_rule = MagicMock(spec=ComplianceRule)
    mock_rule.id = "R-EXP-01"
    mock_rule.platform = "all"
    mock_rule.trigger_type = "keyword"
    mock_rule.pattern_value = "测试关键词"
    mock_rule.effective_from = None
    mock_rule.effective_to = date.today() - timedelta(days=1)  # 昨天过期

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_rule]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    from app.compliance.service import check_content
    violations = await check_content(mock_db, "包含测试关键词的内容")

    # 过期规则不应匹配
    assert len(violations) == 0


@pytest.mark.asyncio
async def test_check_content_future_rule_skipped():
    """测试内容检查——未生效规则应被跳过"""
    from datetime import date, timedelta
    from app.common.models import ComplianceRule

    mock_rule = MagicMock(spec=ComplianceRule)
    mock_rule.id = "R-FUT-01"
    mock_rule.platform = "all"
    mock_rule.trigger_type = "keyword"
    mock_rule.pattern_value = "测试关键词"
    mock_rule.effective_from = date.today() + timedelta(days=7)  # 下周才生效
    mock_rule.effective_to = None

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_rule]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    from app.compliance.service import check_content
    violations = await check_content(mock_db, "包含测试关键词的内容")

    assert len(violations) == 0


# ===== 日期解析 =====


def test_parse_date_valid():
    """测试日期解析——合法日期"""
    from app.compliance.service import _parse_date
    from datetime import date

    result = _parse_date("2026-03-15")
    assert result == date(2026, 3, 15)


def test_parse_date_invalid():
    """测试日期解析——无效日期返回None"""
    from app.compliance.service import _parse_date

    assert _parse_date("not-a-date") is None
    assert _parse_date("") is None
    assert _parse_date(None) is None


def _build_rule(rule_id: str):
    from app.common.models import ComplianceRule

    return ComplianceRule(
        id=rule_id,
        platform="tmall",
        surface="title",
        category_scope="食品",
        trigger_type="keyword",
        pattern_value="最好",
        severity="P1",
        decision="block",
        rewrite_suggestion=None,
        required_evidence=None,
        effective_from=None,
        effective_to=None,
        source_url=None,
        owner="admin",
    )


@pytest.mark.asyncio
async def test_update_rule_versions_use_max_plus_one(client, monkeypatch):
    from app.compliance import service
    from app.common.models import ComplianceRuleVersion
    from app.database import async_session_factory

    monkeypatch.setattr(service.audit, "log", AsyncMock())

    async with async_session_factory() as session:
        session.add(_build_rule("R-SEQ"))
        session.add_all(
            [
                ComplianceRuleVersion(
                    rule_id="R-SEQ",
                    version_no=1,
                    snapshot={"severity": "P3"},
                    author="seed",
                ),
                ComplianceRuleVersion(
                    rule_id="R-SEQ",
                    version_no=3,
                    snapshot={"severity": "P2"},
                    author="seed",
                ),
            ]
        )
        await session.commit()

    async with async_session_factory() as session:
        await service.update_rule(session, "R-SEQ", user_id="admin", severity="P0")
        await session.commit()

    async with async_session_factory() as session:
        versions = (
            await session.execute(
                select(ComplianceRuleVersion)
                .where(ComplianceRuleVersion.rule_id == "R-SEQ")
                .order_by(ComplianceRuleVersion.version_no)
            )
        ).scalars().all()

    assert [item.version_no for item in versions] == [1, 3, 4]
    assert versions[-1].snapshot["severity"] == "P1"


@pytest.mark.asyncio
async def test_update_rule_serializes_version_numbers(client, monkeypatch):
    from app.compliance import service
    from app.common.models import ComplianceRuleVersion
    from app.database import async_session_factory

    monkeypatch.setattr(service.audit, "log", AsyncMock())

    async with async_session_factory() as session:
        if session.bind.dialect.name != "postgresql":
            pytest.skip("row-level locking requires PostgreSQL")
        session.add(_build_rule("R-CONC"))
        await session.flush()
        await session.execute(
            text(
                "CREATE UNIQUE INDEX uq_compliance_rule_versions_rule_version_no "
                "ON compliance_rule_versions(rule_id, version_no)"
            )
        )
        await session.commit()

    async def _update(*, severity: str, start_delay: float = 0.0, commit_delay: float = 0.0):
        if start_delay:
            await asyncio.sleep(start_delay)
        async with async_session_factory() as session:
            await service.update_rule(session, "R-CONC", user_id=severity, severity=severity)
            if commit_delay:
                await asyncio.sleep(commit_delay)
            await session.commit()

    await asyncio.gather(
        _update(severity="P0", commit_delay=0.2),
        _update(severity="P2", start_delay=0.05),
    )

    async with async_session_factory() as session:
        versions = (
            await session.execute(
                select(ComplianceRuleVersion)
                .where(ComplianceRuleVersion.rule_id == "R-CONC")
                .order_by(ComplianceRuleVersion.version_no)
            )
        ).scalars().all()

    assert [item.version_no for item in versions] == [1, 2]
    assert len({item.version_no for item in versions}) == 2
