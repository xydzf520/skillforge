"""pytest共用fixture"""

from unittest.mock import MagicMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import settings


def _make_mock_admin():
    """创建mock admin用户，用于测试认证依赖"""
    user = MagicMock()
    user.id = "admin"
    user.name = "管理员"
    user.username = "admin"
    user.role = "admin"
    user.department = "AI小组"
    user.can_view_all = True
    user.is_active = True
    user.must_change_password = False
    user.dingtalk_user_id = None
    user.avatar_url = None
    user.email = None
    user.phone = None
    user.permissions_rev = 0
    user.state = "active"
    return user


@pytest_asyncio.fixture
async def client(monkeypatch):
    """每个测试函数独立的HTTP客户端，自带engine + mock认证"""
    import app.database as db_mod

    # F3: 加载 prompt registry（测试环境也需要 prompts 可用）
    try:
        from app.common.prompt_registry import init_registry, prompt_registry
        if not prompt_registry._sections:
            init_registry()
    except Exception:
        pass

    # 使用独立的测试数据库（不破坏生产数据）
    import re
    test_url = getattr(settings, 'DATABASE_URL_TEST', None) or re.sub(r'/skillforge$', '/skillforge_test', str(settings.DATABASE_URL))
    eng = create_async_engine(test_url, echo=False)
    fac = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    # 先删再建，确保schema与最新model完全一致（测试环境可以drop）
    from app.database import Base
    # 确保所有 ORM 模型注册到 Base.metadata
    # 注意：optimizer.models 必须 import，否则 drop_all 时 FK 依赖顺序错乱
    # （optimizer_sessions.skill_id → skills.id 的 FK 阻止 drop skills 表）
    import app.skills.models, app.skills.members, app.auth.models, app.reviews.models  # noqa: F401
    import app.skills.asset_models  # noqa: F401  # skill_instances/skill_releases 依赖 skills 表
    import app.execution.models, app.datasources.models, app.browser.models, app.collection.models  # noqa: F401
    import app.datasources.governance_models  # noqa: F401
    import app.dingtalk.models, app.common.models, app.common.audit  # noqa: F401
    import app.workbench.models, app.optimizer.models, app.testing.models  # noqa: F401
    import app.todos.models  # noqa: F401
    import app.org.models  # noqa: F401
    import app.portal.models, app.portal.market_models  # noqa: F401
    import app.approval.models  # noqa: F401
    import app.execution.queue_models  # noqa: F401
    import app.auth.abac_models  # noqa: F401
    import app.tasktree.models  # noqa: F401
    import app.inbox.models  # noqa: F401  # user_inbox_preferences / inbox_report_cards
    import app.hall.models  # noqa: F401
    import app.codex.models  # noqa: F401
    import app.training.models  # noqa: F401
    import app.knowledge.models  # noqa: F401
    import app.learning.models  # noqa: F401
    import app.projects.models  # noqa: F401
    import app.media.models  # noqa: F401  # media jobs reference project assets
    import app.agents.models  # noqa: F401
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # optimizer_events 表只有 alembic migration 011 创建，没有 ORM 模型，
        # 所以 create_all 不会建，需要手动建（用于 delete_skill 等测试场景）
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
        # v2.0.15 C3：跨 worker 缓存失效用的版本号戳
        # alembic 迁移 061 创建此表；测试环境用 create_all 时需要手动补
        await conn.execute(_text("""
            CREATE TABLE IF NOT EXISTS system_meta (
                key VARCHAR(64) PRIMARY KEY,
                version BIGINT NOT NULL DEFAULT 1,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        await conn.execute(_text("""
            INSERT INTO system_meta (key, version) VALUES ('org_units_cache_version', 1)
            ON CONFLICT (key) DO NOTHING
        """))

    # 替换全局
    orig_engine, orig_factory = db_mod.engine, db_mod.async_session_factory
    db_mod.engine = eng
    db_mod.async_session_factory = fac

    # HTTP API tests should not spawn real browser cookie sync/verify background
    # tasks. Those tasks can outlive a test's DB fixture and interfere with the
    # next drop_all/create_all cycle.
    from app.datasources import service as datasource_service
    monkeypatch.setattr(datasource_service, "_schedule_cookie_sync_to_browser", lambda source_id: None)
    monkeypatch.setattr(datasource_service, "_schedule_cookie_verify", lambda source_id: None)
    datasource_service._last_cookie_sync_ts.clear()
    datasource_service._last_cookie_verify_ts.clear()

    # 创建纯API app，覆盖认证依赖为mock admin
    from app.common.exceptions import AppError, app_error_handler
    from app.auth.dependencies import get_current_user, require_state_active_web_or_cli
    from app.auth.models import User
    from fastapi import FastAPI

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    mock_admin = _make_mock_admin()
    app.dependency_overrides[get_current_user] = lambda: mock_admin
    app.dependency_overrides[require_state_active_web_or_cli] = lambda: mock_admin

    from app.auth.router import router as r1
    from app.skills.router import router as r2
    from app.reviews.router import router as r3
    from app.execution.router import router as r4
    from app.datasources.router import router as r5
    from app.dashboard.router import router as r6
    from app.testing.router import router as r7
    from app.dingtalk.router import router as r8
    from app.compliance.router import router as r9
    from app.playbooks.router import router as r10
    from app.users.router import router as r11
    from app.audit.router import router as r12
    from app.workbench.router import router as r13
    from app.todos.router import router as r14
    from app.aiclaw.router import router as r15
    from app.agent_core.router import router as r16
    from app.org.router import router as r17
    from app.portal.router import router as r18
    from app.approval.router import router as r19
    from app.dashboard.router import router as r20
    from app.tasktree.router import router as r21
    from app.hall.router import router as r_hall  # v2.7 大厅 v3
    from app.datasources.governance_router import router as r_gov
    from app.browser.router import router as r_browser
    from app.execution.run_trace_router import router as r_trace
    from app.codex.router import router as r_codex
    from app.sf.router import router as r_sf
    from app.training.router import router as r_training
    from app.knowledge.router import router as r_knowledge
    from app.learning.router import router as r_learning
    from app.inbox.router import router as r_inbox
    from app.projects.router import router as r_projects
    from app.agents.router import router as r_agents

    app.include_router(r1, prefix="/api/auth")
    app.include_router(r2, prefix="/api/skills")
    app.include_router(r3, prefix="/api/reviews")
    app.include_router(r4, prefix="/api/executions")
    app.include_router(r5, prefix="/api/data-sources")
    app.include_router(r6, prefix="/api/dashboard")
    app.include_router(r7, prefix="/api/skills")
    app.include_router(r8, prefix="/api/dingtalk")
    app.include_router(r9, prefix="/api/compliance")
    app.include_router(r10, prefix="/api/playbooks")
    app.include_router(r11, prefix="/api/users")
    app.include_router(r12, prefix="/api/audit")
    app.include_router(r13, prefix="/api/skills")
    app.include_router(r14, prefix="/api/todos")
    app.include_router(r15, prefix="/api/aiclaw")
    app.include_router(r16, prefix="/api/agent-core")
    app.include_router(r17, prefix="/api/org")
    app.include_router(r18, prefix="/api/portal")
    app.include_router(r19, prefix="/api/approval")
    app.include_router(r20, prefix="/api/dashboard")
    app.include_router(r21, prefix="/api")
    app.include_router(r_hall, prefix="/api/hall")
    app.include_router(r_gov, prefix="/api/data-governance")
    app.include_router(r_browser, prefix="/api/browser")
    app.include_router(r_trace, prefix="/api/executions")
    app.include_router(r_trace, prefix="/api/admin")
    app.include_router(r_codex, prefix="/api/codex")
    app.include_router(r_sf, prefix="/api/sf")
    app.include_router(r_training, prefix="/api/training")
    app.include_router(r_knowledge, prefix="/api/knowledge")
    app.include_router(r_learning, prefix="/api/learning")
    app.include_router(r_inbox, prefix="/api/inbox")
    app.include_router(r_projects, prefix="/api/projects")
    app.include_router(r_agents, prefix="/api/agents")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await eng.dispose()
    db_mod.engine, db_mod.async_session_factory = orig_engine, orig_factory
