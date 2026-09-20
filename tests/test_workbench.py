"""Skill Workbench backend tests."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.skills.core.models import Skill
from app.workbench.schemas import SkillStructure

_TEST_SKILL_ID = "EC-投放-01"


async def _reset_agent_core_singletons():
    import app.agent_core.checkpointer as ckpt
    from app.agent_core.graph import reset_graphs

    reset_graphs()
    pool = ckpt._pool
    ckpt._pool = None
    ckpt._saver = None
    ckpt._init_lock = None
    if pool is not None:
        try:
            await pool.close()
        except Exception:
            pass


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_agent_core_state():
    await _reset_agent_core_singletons()
    yield
    await _reset_agent_core_singletons()


@pytest.fixture(autouse=True)
def _mock_agent_core_llm(monkeypatch):
    """让 workbench 的 task-contract 测试走 LangGraph 主链，但不依赖真实 LLM 网络。"""
    async def _fake_call_llm(*args, **kwargs):
        return None

    import app.agent_core.intent_extractor as ie
    import app.agent_core.nodes.skill_generate as sg

    monkeypatch.setattr(ie, "call_llm", _fake_call_llm)
    monkeypatch.setattr(sg, "call_llm", _fake_call_llm)


async def _ensure_test_skill(skill_id: str = _TEST_SKILL_ID):
    """在测试数据库中创建测试 Skill（如果不存在）。"""
    from app.database import async_session_factory
    from sqlalchemy import select
    async with async_session_factory() as session:
        existing = (await session.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
        if not existing:
            session.add(Skill(
                id=skill_id, name="投放优化", description="测试用Skill",
                department="AI小组", role="分析师", trigger_type="manual",
                risk_level="R2", owner="admin", status="draft",
            ))
            await session.commit()


def _sample_structure() -> SkillStructure:
    return SkillStructure(
        meta={"name": "投放优化", "department": "EC"},
        goal="优化 ROI 与转化率",
        rules=[
            {
                "id": "rule-1",
                "name": "阈值判断",
                "description": "根据 ROI 判断是否收紧",
                "branches": [],
            }
        ],
        params=[
            {
                "name": "roi_threshold",
                "default_value": 1.5,
                "description": "ROI 阈值",
            }
        ],
        output_table=[{"name": "结果", "format": "table", "recipient": "运营"}],
        test_cases=[{"name": "case-1", "input_data": {}, "expected_output": {}}],
        custom_sections={"notes": "workbench fixture"},
    )


@pytest.mark.asyncio
async def test_workbench_minimal_flow(client):
    await _ensure_test_skill()
    with patch("app.workbench.service.context_builder.load_skill_structure", return_value=_sample_structure()), \
         patch("app.workbench.service.context_builder.summarize_modules", return_value=[]):
        create_resp = await client.post(
            "/api/skills/EC-投放-01/workbench/session",
            json={"mode": "pro", "active_module": "rules"},
        )

        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["skill_id"] == "EC-投放-01"
        assert created["mode"] == "pro"
        assert created["active_module"] == "rules"
        assert created["persistence"] == "database"
        assert created["skill"]["meta"]["name"] == "投放优化"

        get_resp = await client.get(
            f"/api/skills/EC-投放-01/workbench/session/{created['session_id']}"
        )
        assert get_resp.status_code == 200
        fetched = get_resp.json()
        assert fetched["session_id"] == created["session_id"]
        assert fetched["skill"]["meta"]["name"] == "投放优化"

        intent_resp = await client.post(
            "/api/skills/EC-投放-01/workbench/intent",
            json={
                "session_id": created["session_id"],
                "message": "把ROI阈值调低一点，只改参数",
                "active_module": "params",
            },
        )
        assert intent_resp.status_code == 200
        intent = intent_resp.json()
        assert intent["session_id"] == created["session_id"]
        assert intent["target_module"] == "params"
        assert intent["intent"] in ("tune_threshold", "rewrite_module")


@pytest.mark.asyncio
async def test_workbench_generate_validate_apply_flow(client):
    with patch("app.workbench.service._generate_draft_with_ai", new=AsyncMock(return_value=None)):
        resp = await client.post(
            "/api/skills/workbench/generate-draft",
            json={"message": "生成一个监控 ROI 异常并通知运营的 Skill", "mode": "novice"},
        )
    assert resp.status_code == 200
    draft = resp.json()
    assert draft["source"] == "fallback"
    assert draft["summary"]
    assert draft["skill"]["goal"]

    await _ensure_test_skill()
    with patch("app.workbench.service.context_builder.load_skill_structure", return_value=_sample_structure()), \
         patch("app.workbench.service.context_builder.summarize_modules", return_value=[]), \
         patch("app.workbench.service.skill_service.save_skill_content", new=AsyncMock(return_value={
             "skill_id": "EC-投放-01",
             "git_commit": "abc12345",
             "changes": ["SKILL.md", "policy_pack.yaml"],
         })), \
         patch("app.skills.git_service.git_service.write_file"):
        session_resp = await client.post(
            "/api/skills/EC-投放-01/workbench/session",
            json={"mode": "pro", "active_module": "params"},
        )
        session_id = session_resp.json()["session_id"]

        patch_resp = await client.post(
            "/api/skills/EC-投放-01/workbench/patch",
            json={
                "session_id": session_id,
                "message": "把 ROI 阈值调整到 1.8",
                "target_module": "params",
            },
        )
        assert patch_resp.status_code == 200
        patch_data = patch_resp.json()
        patch_id = patch_data["patch_id"]
        assert patch_data["target_module"] == "params"

        validate_resp = await client.post(
            "/api/skills/EC-投放-01/workbench/validate",
            json={"patch_id": patch_id},
        )
        assert validate_resp.status_code == 200
        assert validate_resp.json()["validation"]["valid"] is True

        apply_resp = await client.post(
            "/api/skills/EC-投放-01/workbench/apply",
            json={"patch_id": patch_id},
        )
        assert apply_resp.status_code == 200
        applied = apply_resp.json()
        assert applied["git_commit"] == "abc12345"
        assert applied["target_module"] == "params"
        assert applied["skill"]["params"][0]["default_value"] == 1.8


@pytest.mark.asyncio
async def test_workbench_references_endpoint(client):
    from app.database import async_session_factory

    async with async_session_factory() as session:
        session.add(
            Skill(
                id="REF-SKILL-01",
                name="引用测试Skill",
                description="引用来源",
                department="EC",
                role="分析师",
                trigger_type="manual",
                risk_level="R2",
                owner="admin",
                status="draft",
            )
        )
        await session.commit()

    with patch("app.workbench.service.playbook_service.list_playbooks", new=AsyncMock(return_value=[
        {"file_name": "morning-check", "name": "晨检流程", "description": "每天晨检"},
    ])):
        resp = await client.get("/api/skills/workbench/references", params={"skill_id": "EC-投放-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert any(item["source_type"] == "workflow_template" for item in data["items"])
    assert any(item["source_type"] == "skill_module" for item in data["items"])


@pytest.mark.asyncio
async def test_workbench_workflow_patch_and_apply(client):
    await _ensure_test_skill()
    with patch("app.workbench.service.context_builder.load_skill_structure", return_value=_sample_structure()), \
         patch("app.workbench.service.context_builder.summarize_modules", return_value=[]), \
         patch("app.workbench.service.playbook_service.save_playbook", new=AsyncMock(return_value={"file_name": "EC-投放-01-workflow"})):
        session_resp = await client.post(
            "/api/skills/EC-投放-01/workbench/session",
            json={"mode": "pro", "active_module": "workflow"},
        )
        session_id = session_resp.json()["session_id"]

        patch_resp = await client.post(
            "/api/skills/EC-投放-01/workbench/patch",
            json={
                "session_id": session_id,
                "message": "在结果后增加人工审批节点",
                "target_module": "workflow",
                "references": [{"source_type": "skill_module", "source_module": "output_table", "source_id": "t9-wechat", "reference_mode": "copy_logic"}],
            },
        )
        assert patch_resp.status_code == 200
        patch_data = patch_resp.json()
        assert patch_data["target_module"] == "workflow"
        assert patch_data["patch"]["workflow"]["nodes"]

        validate_resp = await client.post(
            "/api/skills/EC-投放-01/workbench/validate",
            json={"patch_id": patch_data["patch_id"]},
        )
        assert validate_resp.status_code == 200
        assert validate_resp.json()["validation"]["can_apply"] is True

        apply_resp = await client.post(
            "/api/skills/EC-投放-01/workbench/apply",
            json={"patch_id": patch_data["patch_id"]},
        )
        assert apply_resp.status_code == 200
        applied = apply_resp.json()
        assert applied["status"] == "applied"
        assert applied["workflow_name"] == "EC-投放-01-workflow"


@pytest.mark.asyncio
async def test_workbench_create_skill_from_draft(client):
    draft = _sample_structure().model_dump()
    draft["meta"]["name"] = "新建测试Skill"
    with patch("app.workbench.service.skill_service.create_skill", new=AsyncMock(return_value={
        "skill_id": "新建测试Skill",
        "git_commit": "deadbeef",
        "quality_score": 88,
    })):
        resp = await client.post(
            "/api/skills/workbench/create-skill",
            json={"skill": draft},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_id"] == "新建测试Skill"
    assert data["git_commit"] == "deadbeef"


@pytest.mark.asyncio
async def test_workbench_task_contract_flow(client):
    message = "每天 18:00 发昨日销售钉钉日报到销售运营群"

    resp = await client.post(
        "/api/skills/workbench/task-contract",
        json={"message": message},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["contract"]["trigger"]["type"] == "cron"
    assert data["contract"]["output"]["adapter"] == "dingtalk_card"
    assert len(data["contract"]["test_cases"]) >= 3
    assert "intent.md" in data["intent_md"]
    assert data["preview"]["rendered_output"]
    assert data["gate"]["can_generate_skill"] is False

    draft_id = data["draft_id"]
    for checkpoint in ["target", "permission", "preview"]:
        review_resp = await client.post(
            f"/api/skills/workbench/task-contract/{draft_id}/review",
            json={"checkpoint": checkpoint, "decision": "approved", "detail": {}},
        )
        assert review_resp.status_code == 200

    gate_resp = review_resp.json()
    assert gate_resp["gate"]["can_generate_skill"] is True
    assert gate_resp["gate"]["can_publish"] is False

    final_resp = await client.post(
        f"/api/skills/workbench/task-contract/{draft_id}/review",
        json={"checkpoint": "responsibility", "decision": "approved", "detail": {"accepted": True}},
    )
    assert final_resp.status_code == 200
    final_data = final_resp.json()
    assert final_data["gate"]["can_publish"] is True
    assert any(item["key"] == "responsibility" and item["approved"] for item in final_data["checkpoints"])


@pytest.mark.asyncio
async def test_workbench_task_contract_preview_cache_reuse(client):
    message = "每天 18:00 发昨日销售钉钉日报到销售运营群"

    first = await client.post("/api/skills/workbench/task-contract", json={"message": message})
    second = await client.post("/api/skills/workbench/task-contract", json={"message": message})

    assert first.status_code == 200
    assert second.status_code == 200
    first_data = first.json()
    second_data = second.json()
    assert first_data["preview"]["cache_key"] == second_data["preview"]["cache_key"]
    assert second_data["preview"]["cached"] is True


@pytest.mark.asyncio
async def test_workbench_task_contract_bind_skill(client):
    resp = await client.post(
        "/api/skills/workbench/task-contract",
        json={"message": "每天 18:00 发昨日销售钉钉日报到销售运营群"},
    )
    assert resp.status_code == 200
    draft_id = resp.json()["draft_id"]

    bind_resp = await client.post(
        f"/api/skills/workbench/task-contract/{draft_id}/bind",
        json={"skill_id": "EC-投放-01"},
    )
    assert bind_resp.status_code == 200
    assert bind_resp.json()["bound"] is True
    assert bind_resp.json()["skill_id"] == "EC-投放-01"
