"""部门隔离测试：验证非本部门用户无法访问受保护的 Skill 端点。

覆盖 A1 任务中的 13 个端点：
- run-aiclaw, lock(3), compare-params, guardian(4), health-score, shadow(3)
"""

import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import settings


def _make_mock_biz_owner(department: str = "BD"):
    """创建 biz_owner 角色 mock 用户，部门与测试 Skill 不同。"""
    user = MagicMock()
    user.id = "biz_user_bd"
    user.name = "BD负责人"
    user.username = "biz_bd"
    user.role = "biz_owner"
    user.department = department
    user.can_view_all = False
    user.is_active = True
    user.must_change_password = False
    user.dingtalk_user_id = None
    user.avatar_url = None
    return user


@pytest_asyncio.fixture
async def isolated_client():
    """使用 biz_owner (BD 部门) 用户的客户端 — Skill 属于 EC 部门。"""
    import re
    import app.database as db_mod
    from app.database import Base
    from app.common.exceptions import AppError, app_error_handler
    from app.auth.dependencies import get_current_user
    from fastapi import FastAPI

    try:
        from app.common.prompt_registry import init_registry, prompt_registry
        if not prompt_registry._sections:
            init_registry()
    except Exception:
        pass

    test_url = getattr(settings, 'DATABASE_URL_TEST', None) or re.sub(
        r'/skillforge$', '/skillforge_test', str(settings.DATABASE_URL))
    eng = create_async_engine(test_url, echo=False)
    fac = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    import app.skills.models, app.auth.models, app.reviews.models  # noqa: F401
    import app.execution.models, app.datasources.models  # noqa: F401
    import app.dingtalk.models, app.common.models, app.common.audit  # noqa: F401
    import app.workbench.models, app.optimizer.models, app.testing.models  # noqa: F401
    import app.todos.models  # noqa: F401
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text as _text
        await conn.execute(_text("""
            CREATE TABLE IF NOT EXISTS optimizer_events (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(50) NOT NULL,
                candidate_id VARCHAR(50),
                event_type VARCHAR(50) NOT NULL,
                detail JSON,
                user_id VARCHAR(50),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

    # 插入一个 EC 部门的 Skill
    async with fac() as session:
        from app.skills.core.models import Skill
        session.add(Skill(id="EC-测试-01", name="测试Skill", department="EC"))
        await session.commit()

    orig_engine, orig_factory = db_mod.engine, db_mod.async_session_factory
    db_mod.engine = eng
    db_mod.async_session_factory = fac

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    # 用 BD 部门 biz_owner 登录
    mock_user = _make_mock_biz_owner("BD")
    app.dependency_overrides[get_current_user] = lambda: mock_user

    from app.skills.router import router as skills_router
    app.include_router(skills_router, prefix="/api/skills")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await eng.dispose()
    db_mod.engine, db_mod.async_session_factory = orig_engine, orig_factory


SKILL_ID = "EC-测试-01"


@pytest.mark.asyncio
async def test_run_aiclaw_denied(isolated_client):
    """BD 用户不能在 EC 部门 Skill 上执行 AIClaw"""
    resp = await isolated_client.post(f"/api/skills/{SKILL_ID}/run-aiclaw")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_lock_acquire_denied(isolated_client):
    """BD 用户不能锁定 EC 部门 Skill"""
    resp = await isolated_client.post(f"/api/skills/{SKILL_ID}/lock")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_lock_release_denied(isolated_client):
    resp = await isolated_client.delete(f"/api/skills/{SKILL_ID}/lock")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_lock_status_denied(isolated_client):
    resp = await isolated_client.get(f"/api/skills/{SKILL_ID}/lock")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_compare_params_denied(isolated_client):
    resp = await isolated_client.post(
        f"/api/skills/{SKILL_ID}/compare-params",
        json={"new_params": {"roi": 2.0}},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_guardian_anomalies_denied(isolated_client):
    resp = await isolated_client.get(f"/api/skills/{SKILL_ID}/guardian/anomalies")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_guardian_root_cause_denied(isolated_client):
    resp = await isolated_client.post(f"/api/skills/{SKILL_ID}/guardian/root-cause")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_guardian_conflicts_denied(isolated_client):
    resp = await isolated_client.get(f"/api/skills/{SKILL_ID}/guardian/conflicts")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_guardian_drift_denied(isolated_client):
    resp = await isolated_client.get(f"/api/skills/{SKILL_ID}/guardian/drift")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_health_score_denied(isolated_client):
    resp = await isolated_client.get(f"/api/skills/{SKILL_ID}/health-score")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_shadow_stats_denied(isolated_client):
    resp = await isolated_client.get(f"/api/skills/{SKILL_ID}/shadow/stats")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_shadow_comparisons_denied(isolated_client):
    resp = await isolated_client.get(f"/api/skills/{SKILL_ID}/shadow/comparisons")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_shadow_divergence_rate_denied(isolated_client):
    resp = await isolated_client.get(f"/api/skills/{SKILL_ID}/shadow/divergence-rate")
    assert resp.status_code == 403
