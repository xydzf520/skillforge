from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_add_regression_case_writes_skill_git_and_creates_review(monkeypatch):
    from app.skills import router as skill_router
    from app.skills.core.git_service import git_service
    from app.skills.router import RegressionCaseRequest

    class Lock:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(skill_router, "ensure_skill_access", AsyncMock())
    monkeypatch.setattr(skill_router, "invalidate_skill", AsyncMock())
    monkeypatch.setattr(
        "app.reviews.service.enforce_static_detection_gate",
        AsyncMock(return_value={"passed": True, "finding_count": 0, "findings": []}),
    )
    create_review = AsyncMock(return_value={"review_id": 12, "status": "pending"})
    monkeypatch.setattr("app.reviews.service.create_review", create_review)
    monkeypatch.setattr(git_service, "skill_advisory_lock", lambda skill_id: Lock())
    monkeypatch.setattr(git_service, "read_file", MagicMock(return_value=None))
    write_file = MagicMock()
    monkeypatch.setattr(git_service, "write_file", write_file)
    monkeypatch.setattr(git_service, "commit_all", MagicMock(return_value="commit123"))

    body = RegressionCaseRequest(
        case_id="case_001",
        case={"expected_output": {"signal": "decline"}},
        reason="运营反馈：该洞察应纳入回归",
    )
    user = SimpleNamespace(id="operator-001")

    result = await skill_router.add_regression_case("tmall-skill", body, user, AsyncMock())

    assert result["path"] == "tests/regression/cases/case_001.json"
    assert result["commit"] == "commit123"
    assert result["review"]["review_id"] == 12
    write_file.assert_called_once()
    assert write_file.call_args.args[1] == "tests/regression/cases/case_001.json"
    create_review.assert_awaited_once()
    assert create_review.await_args.kwargs["change_type"] == "regression_case"


@pytest.mark.asyncio
async def test_add_regression_case_retries_review_with_force_when_gate_blocks(monkeypatch):
    from app.common.exceptions import AppError
    from app.skills import router as skill_router
    from app.skills.core.git_service import git_service
    from app.skills.router import RegressionCaseRequest

    class Lock:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(skill_router, "ensure_skill_access", AsyncMock())
    monkeypatch.setattr(skill_router, "invalidate_skill", AsyncMock())
    monkeypatch.setattr(
        "app.reviews.service.enforce_static_detection_gate",
        AsyncMock(return_value={"passed": True, "finding_count": 0, "findings": []}),
    )
    create_review = AsyncMock(side_effect=[
        AppError("PARAM_INVALID", 400, {"reason": "提交审核 gate 未通过"}),
        {"review_id": 13, "status": "pending"},
    ])
    monkeypatch.setattr("app.reviews.service.create_review", create_review)
    monkeypatch.setattr(git_service, "skill_advisory_lock", lambda skill_id: Lock())
    monkeypatch.setattr(git_service, "read_file", MagicMock(return_value=None))
    monkeypatch.setattr(git_service, "write_file", MagicMock())
    monkeypatch.setattr(git_service, "commit_all", MagicMock(return_value="commit456"))

    body = RegressionCaseRequest(
        case_id="case_002",
        case={"must_not_say": ["请持续关注"]},
    )
    user = SimpleNamespace(id="operator-001")

    result = await skill_router.add_regression_case("tmall-skill", body, user, AsyncMock())

    assert result["review"]["review_id"] == 13
    assert create_review.await_count == 2
    assert create_review.await_args.kwargs["force_submit"] is True
    assert "回归用例自动创建审核" in create_review.await_args.kwargs["force_reason"]
