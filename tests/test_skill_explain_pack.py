from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.common.exceptions import AppError, app_error_handler


@pytest.mark.asyncio
async def test_build_explain_pack_fallback(monkeypatch):
    from app.skills import ai_service

    mock_db = AsyncMock()
    monkeypatch.setattr(
        "app.skills.service.get_skill",
        AsyncMock(return_value={
            "id": "EC-EXP-01",
            "name": "解释 Skill",
            "description": "测试描述",
            "department": "EC",
            "trigger_type": "manual",
            "current_version": "v1",
            "updated_at": "2026-04-11T00:00:00",
            "policy_pack": {"roi_threshold": 1.2},
            "parsed": {
                "purpose": "根据 ROI 判断是否加预算",
                "steps": [{"id": "step_1", "name": "判断 ROI", "branches": [{"condition": "ROI > 1.2", "conclusion": "绿灯"}]}],
                "output_definition": [{"name": "结论", "recipient": "运营", "format": "text"}],
                "test_cases": [{"name": "case_1"}],
            },
        }),
    )
    monkeypatch.setattr("app.skills.ai_service.cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr("app.skills.ai_service.cache_set", AsyncMock())
    monkeypatch.setattr("app.skills.ai_service.call_llm_cached", AsyncMock(return_value=None))

    result = await ai_service.build_explain_pack(mock_db, "EC-EXP-01", user_id="u1", department="EC")
    assert result["source"] == "fallback"
    assert result["executive_summary"]
    assert result["decision_ladder"]
    assert result["parameter_impacts"][0]["name"] == "roi_threshold"


@pytest.mark.asyncio
async def test_build_explain_pack_uses_ai_when_available(monkeypatch):
    from app.skills import ai_service

    mock_db = AsyncMock()
    monkeypatch.setattr(
        "app.skills.service.get_skill",
        AsyncMock(return_value={
            "id": "EC-EXP-02",
            "name": "解释 Skill",
            "description": "",
            "department": "EC",
            "trigger_type": "schedule",
            "current_version": "v2",
            "updated_at": "2026-04-11T00:00:00",
            "policy_pack": {},
            "parsed": {"purpose": "解释给业务", "steps": [], "output_definition": [], "test_cases": []},
        }),
    )
    monkeypatch.setattr("app.skills.ai_service.cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr("app.skills.ai_service.cache_set", AsyncMock())
    monkeypatch.setattr(
        "app.skills.ai_service.call_llm_cached",
        AsyncMock(return_value={
            "executive_summary": "AI 生成的业务解释",
            "trigger_summary": "按计划触发",
            "parameter_impacts": [],
            "output_contract": [],
            "decision_ladder": [],
            "test_confidence": {"total_cases": 0, "covered_rules": 0, "uncovered_rules": 0, "summary": "AI 认为当前无测试"},
        }),
    )

    result = await ai_service.build_explain_pack(mock_db, "EC-EXP-02", user_id="u1", department="EC")
    assert result["source"] == "ai"
    assert result["executive_summary"] == "AI 生成的业务解释"
    assert result["prompt_hash"]


@pytest.mark.asyncio
async def test_explain_pack_route_checks_department_access(monkeypatch):
    from app.auth.dependencies import get_current_user
    from app.database import get_db
    from app.skills.router import router

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    mock_user = MagicMock()
    mock_user.id = "biz_user"
    mock_user.role = "biz_owner"
    mock_user.department = "EC"
    mock_user.can_view_all = False
    mock_user.is_active = True

    app.dependency_overrides[get_current_user] = lambda: mock_user

    async def _fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    app.include_router(router, prefix="/api/skills")

    monkeypatch.setattr("app.skills.service.get_skill", AsyncMock(return_value={"department": "OTHER"}))
    monkeypatch.setattr("app.skills.ai_service.build_explain_pack", AsyncMock(return_value={"executive_summary": "x"}))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/skills/EC-EXP-03/explain-pack")

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "SKILL_ACCESS_DENIED"
