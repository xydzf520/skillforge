"""session_orchestrator 模块单元测试。

测试 app/workbench/session_orchestrator.py 中的函数：
create_session, get_session, analyze_intent,
list_references, cleanup_expired_sessions。
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.exceptions import AppError
from app.workbench.schemas import SkillStructure, WorkbenchReferenceListResponse


# ── helpers ──────────────────────────────────────────────────────────


def _mock_structure():
    """构造最小 SkillStructure 用于 context_builder 返回"""
    return SkillStructure(
        meta={"name": "测试Skill", "department": "EC"},
        goal="测试目的",
        rules=[],
        params=[],
        output_table=[],
        test_cases=[],
        workflow={},
        custom_sections={},
    )


def _mock_wb_session(session_id="wb-test123", skill_id="EC-投放-01", user_id="admin",
                     mode="pro", status="active", current_module=None):
    """构造模拟的 SkillWorkbenchSession ORM 对象"""
    s = MagicMock()
    s.id = session_id
    s.skill_id = skill_id
    s.user_id = user_id
    s.mode = mode
    s.status = status
    s.current_module = current_module
    s.context_snapshot = {}
    s.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    s.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return s


def _mock_session_factory(wb_session=None, skills=None, commit_side_effect=None):
    """构造 async_session_factory 的 mock，使 async with _sf()() as session 可用。

    wb_session: 单个 SkillWorkbenchSession 查询结果
    skills: Skill 查询结果列表（用于 list_references）
    """
    db_session = AsyncMock()
    db_session.add = MagicMock()
    result_mock = MagicMock()

    if skills is not None:
        # list_references 用 scalars().all()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = skills
        result_mock.scalars.return_value = scalars_mock
        result_mock.scalar_one_or_none.return_value = wb_session
    else:
        result_mock.scalar_one_or_none.return_value = wb_session

    db_session.execute.return_value = result_mock
    if commit_side_effect:
        db_session.commit.side_effect = commit_side_effect

    # rowcount 用于 cleanup_expired_sessions
    result_mock.rowcount = 3

    factory = MagicMock()
    # _sf()() 返回一个 async context manager
    ctx = AsyncMock()
    ctx.__aenter__.return_value = db_session
    ctx.__aexit__.return_value = None
    factory.return_value = ctx
    return factory


# ── create_session ───────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.workbench.session_orchestrator._sf")
@patch("app.workbench.session_orchestrator.context_builder")
async def test_create_session_normal(mock_cb, mock_sf):
    """正常创建 workbench session"""
    from app.workbench.session_orchestrator import create_session

    structure = _mock_structure()
    mock_cb.load_skill_structure.return_value = structure
    mock_cb.summarize_modules.return_value = []
    mock_sf.return_value = _mock_session_factory()

    result = await create_session(skill_id="EC-投放-01", user_id="admin", mode="pro")
    assert result.skill_id == "EC-投放-01"
    assert result.mode == "pro"
    assert result.session_id.startswith("wb-")
    assert result.persistence == "database"


@pytest.mark.asyncio
@patch("app.workbench.session_orchestrator._sf")
@patch("app.workbench.session_orchestrator.context_builder")
async def test_create_session_novice_mode(mock_cb, mock_sf):
    """创建 novice 模式的 session"""
    from app.workbench.session_orchestrator import create_session

    structure = _mock_structure()
    mock_cb.load_skill_structure.return_value = structure
    mock_cb.summarize_modules.return_value = []
    mock_sf.return_value = _mock_session_factory()

    result = await create_session(skill_id="EC-投放-01", user_id="user1", mode="novice")
    assert result.mode == "novice"


# ── get_session ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.workbench.session_orchestrator._sf")
@patch("app.workbench.session_orchestrator.context_builder")
async def test_get_session_normal(mock_cb, mock_sf):
    """正常获取已有 session"""
    from app.workbench.session_orchestrator import get_session

    wb = _mock_wb_session()
    structure = _mock_structure()
    mock_cb.load_skill_structure.return_value = structure
    mock_cb.summarize_modules.return_value = []
    mock_sf.return_value = _mock_session_factory(wb_session=wb)

    result = await get_session(skill_id="EC-投放-01", session_id="wb-test123", user_id="admin")
    assert result.session_id == "wb-test123"
    assert result.skill_id == "EC-投放-01"


@pytest.mark.asyncio
@patch("app.workbench.session_orchestrator._sf")
async def test_get_session_not_found(mock_sf):
    """session 不存在时抛出 WORKBENCH_SESSION_NOT_FOUND 404"""
    from app.workbench.session_orchestrator import get_session

    mock_sf.return_value = _mock_session_factory(wb_session=None)

    with pytest.raises(AppError) as exc_info:
        await get_session(skill_id="EC-投放-01", session_id="wb-nonexist", user_id="admin")
    assert exc_info.value.code == "WORKBENCH_SESSION_NOT_FOUND"
    assert exc_info.value.status == 404


@pytest.mark.asyncio
@patch("app.workbench.session_orchestrator._sf")
async def test_get_session_permission_denied(mock_sf):
    """session 属于其他用户时抛出 AUTH_PERMISSION_DENIED 403"""
    from app.workbench.session_orchestrator import get_session

    wb = _mock_wb_session(user_id="other_user")
    mock_sf.return_value = _mock_session_factory(wb_session=wb)

    with pytest.raises(AppError) as exc_info:
        await get_session(skill_id="EC-投放-01", session_id="wb-test123", user_id="admin")
    assert exc_info.value.code == "AUTH_PERMISSION_DENIED"
    assert exc_info.value.status == 403


@pytest.mark.asyncio
@patch("app.workbench.session_orchestrator._sf")
async def test_get_session_wrong_skill(mock_sf):
    """session 的 skill_id 不匹配时也抛出 NOT_FOUND"""
    from app.workbench.session_orchestrator import get_session

    wb = _mock_wb_session(skill_id="OTHER-SKILL")
    mock_sf.return_value = _mock_session_factory(wb_session=wb)

    with pytest.raises(AppError) as exc_info:
        await get_session(skill_id="EC-投放-01", session_id="wb-test123", user_id="admin")
    assert exc_info.value.code == "WORKBENCH_SESSION_NOT_FOUND"


# ── analyze_intent ───────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.workbench.session_orchestrator.detect_intent")
@patch("app.workbench.session_orchestrator._sf")
async def test_analyze_intent_normal(mock_sf, mock_detect):
    """正常意图分析"""
    from app.workbench.session_orchestrator import analyze_intent

    wb = _mock_wb_session()
    mock_sf.return_value = _mock_session_factory(wb_session=wb)
    mock_detect.return_value = {
        "intent": "tune_threshold",
        "target_module": "params",
        "confidence": 0.9,
    }

    result = await analyze_intent(
        skill_id="EC-投放-01",
        session_id="wb-test123",
        user_id="admin",
        message="把 ROI 阈值调到 2.0",
    )
    assert result.skill_id == "EC-投放-01"
    mock_detect.assert_called_once()


@pytest.mark.asyncio
@patch("app.workbench.session_orchestrator._sf")
async def test_analyze_intent_session_not_found(mock_sf):
    """session 不存在时意图分析应抛出 404"""
    from app.workbench.session_orchestrator import analyze_intent

    mock_sf.return_value = _mock_session_factory(wb_session=None)

    with pytest.raises(AppError) as exc_info:
        await analyze_intent(
            skill_id="EC-投放-01",
            session_id="wb-nonexist",
            user_id="admin",
            message="test",
        )
    assert exc_info.value.code == "WORKBENCH_SESSION_NOT_FOUND"


# ── list_references ──────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.workbench.session_orchestrator.playbook_service")
@patch("app.workbench.session_orchestrator._sf")
async def test_list_references_with_skills(mock_sf, mock_pb):
    """列出其他 Skill 的引用（排除自身）"""
    from app.workbench.session_orchestrator import list_references

    skill1 = MagicMock()
    skill1.id = "EC-投放-01"
    skill1.name = "投放决策"
    skill1.description = "投放"
    skill1.department = "EC"
    skill1.status = "active"
    skill1.updated_at = datetime.utcnow()

    skill2 = MagicMock()
    skill2.id = "EC-推广-01"
    skill2.name = "推广决策"
    skill2.description = "推广"
    skill2.department = "EC"
    skill2.status = "active"
    skill2.updated_at = datetime.utcnow()

    mock_sf.return_value = _mock_session_factory(skills=[skill1, skill2])
    mock_pb.list_playbooks = AsyncMock(return_value=[])

    result = await list_references(skill_id="EC-投放-01")
    # skill1 是自身，应排除；skill2 保留，每个 Skill 有 5 个模块引用
    assert isinstance(result, WorkbenchReferenceListResponse)
    # 只有 skill2 的 5 个模块
    ids = [item.id for item in result.items]
    assert all("EC-投放-01" not in item_id for item_id in ids)
    assert any("EC-推广-01" in item_id for item_id in ids)


@pytest.mark.asyncio
@patch("app.workbench.session_orchestrator.playbook_service")
@patch("app.workbench.session_orchestrator._sf")
async def test_list_references_filter_by_source_type(mock_sf, mock_pb):
    """按 source_type 过滤引用"""
    from app.workbench.session_orchestrator import list_references

    mock_sf.return_value = _mock_session_factory(skills=[])
    mock_pb.list_playbooks = AsyncMock(return_value=[
        {"file_name": "daily.yaml", "name": "每日报告", "description": "desc", "department": "EC"},
    ])

    result = await list_references(source_type="workflow_template")
    assert len(result.items) == 1
    assert result.items[0].source_type == "workflow_template"


# ── cleanup_expired_sessions ─────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.workbench.session_orchestrator._sf")
async def test_cleanup_expired_sessions(mock_sf):
    """清理过期 session"""
    from app.workbench.session_orchestrator import cleanup_expired_sessions

    mock_sf.return_value = _mock_session_factory()

    count = await cleanup_expired_sessions(max_age_hours=72)
    assert count == 3  # rowcount 在 _mock_session_factory 中设为 3
