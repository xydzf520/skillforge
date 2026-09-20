"""关键权限边界回归测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.common.exceptions import AppError, app_error_handler


def _build_app(router, prefix: str, mock_user):
    from app.auth.dependencies import get_current_user
    from app.database import get_db

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.dependency_overrides[get_current_user] = lambda: mock_user

    async def _fake_db():
        db = AsyncMock()
        # ensure_skill_access() 调用 db.get(Skill, skill_id)，返回 None 触发 NOT_FOUND
        db.get = AsyncMock(return_value=None)
        yield db

    app.dependency_overrides[get_db] = _fake_db
    app.include_router(router, prefix=prefix)
    return app


def _mock_user(*, user_id: str, role: str, department: str, can_view_all: bool = False):
    user = MagicMock()
    user.id = user_id
    user.role = role
    user.department = department
    user.can_view_all = can_view_all
    user.is_active = True
    return user


@pytest.mark.asyncio
async def test_biz_owner_cannot_write_script_content():
    from app.skills.router import router

    user = _mock_user(user_id="biz_a", role="biz_owner", department="EC")
    app = _build_app(router, "/api/skills", user)

    with patch("app.skills.service.get_skill", new=AsyncMock(return_value={"department": "EC"})), \
         patch("app.skills.service.save_skill_content", new=AsyncMock(return_value={"ok": True})) as mock_save:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                "/api/skills/EC-001/content",
                json={"script_content": "print('nope')"},
            )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_PERMISSION_DENIED"
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_biz_owner_cannot_save_other_department_skill():
    """biz_owner 无法保存非本部门 Skill（资源级权限检查拒绝）"""
    from app.skills.router import router

    user = _mock_user(user_id="biz_a", role="biz_owner", department="EC")
    app = _build_app(router, "/api/skills", user)

    # 资源级权限模型下，ensure_skill_access 检查 Skill 成员权限
    # biz_owner 非 Skill member → SKILL_ACCESS_DENIED 403
    async def _deny_access(*args, **kwargs):
        raise AppError("SKILL_ACCESS_DENIED", 403)

    with patch("app.skills.router_files.ensure_skill_access", new=AsyncMock(side_effect=_deny_access)), \
         patch("app.skills.service.get_skill", new=AsyncMock(return_value={"department": "LIVE"})), \
         patch("app.skills.service.save_skill_content", new=AsyncMock(return_value={"ok": True})) as mock_save:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                "/api/skills/LIVE-001/content",
                json={"skill_md": "# updated"},
            )

    assert resp.status_code == 403
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_datasource_list_is_scoped_to_user_department():
    from app.datasources.router import router

    user = _mock_user(user_id="biz_a", role="biz_owner", department="EC")
    app = _build_app(router, "/api/data-sources", user)

    with patch("app.datasources.service.list_sources", new=AsyncMock(return_value={"items": [], "total": 0})) as mock_list:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/data-sources/")

    assert resp.status_code == 200
    assert mock_list.await_args.kwargs["department"] == "EC"


@pytest.mark.asyncio
async def test_dashboard_overview_is_scoped_to_user_department():
    from app.dashboard.router import router

    user = _mock_user(user_id="biz_a", role="biz_owner", department="EC")
    app = _build_app(router, "/api/dashboard", user)

    with patch("app.dashboard.service.get_overview", new=AsyncMock(return_value={"total_runs": 0})) as mock_overview:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/dashboard/overview")

    assert resp.status_code == 200
    assert mock_overview.await_args.kwargs["department"] == "EC"


@pytest.mark.asyncio
async def test_execution_run_list_is_scoped_to_user_department():
    from app.execution.router import router

    user = _mock_user(user_id="biz_a", role="biz_owner", department="EC")
    app = _build_app(router, "/api/executions", user)

    with patch(
        "app.execution.execution_service.execution_service.list_execution_runs_paged",
        new=AsyncMock(return_value={"items": [], "total": 0, "page": 1, "page_size": 20}),
    ) as mock_paged:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/executions/runs")

    assert resp.status_code == 200
    assert mock_paged.await_args.kwargs["department"] == "EC"
