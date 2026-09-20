"""Run a lightweight backend for browser E2E against a seeded test database."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as db_mod
from app.auth.service import hash_password
from app.common.exceptions import AppError, app_error_handler, generic_exception_handler, validation_exception_handler
from app.config import settings
from app.database import Base
from fastapi.exceptions import RequestValidationError
from app.common.time_utils import now_bjt


async def _prepare_database() -> None:
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(r"/skillforge$", "/skillforge_test", str(settings.DATABASE_URL))
    sync_test_url = re.sub(r"\+asyncpg", "+psycopg2", test_url)

    os.environ["DATABASE_URL"] = test_url
    os.environ["DATABASE_URL_SYNC"] = sync_test_url

    engine = create_async_engine(test_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # register all required models
    import app.auth.models  # noqa: F401
    import app.skills.models  # noqa: F401
    import app.skills.members  # noqa: F401
    import app.org.models  # noqa: F401
    import app.portal.models  # noqa: F401
    import app.execution.models  # noqa: F401
    import app.common.models  # noqa: F401
    import app.todos.models  # noqa: F401
    import app.notifications.models  # noqa: F401
    import app.optimizer.models  # noqa: F401
    import app.tasktree.models  # noqa: F401 — 用于 e2e backend drop_all/create_all 包含 task_nodes_light 和 tasktree_repair_queue

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    db_mod.engine = engine
    db_mod.async_session_factory = factory

    # local http e2e
    settings.COOKIE_SECURE = False

    from app.auth.models import User
    from app.execution.models import DecisionLog, ExecutionRun
    from app.org.models import OrgUnit, UserOrgMembership
    from app.portal.models import SkillSubmission
    from app.skills.members import SkillMember, SkillTag
    from app.skills.core.models import Skill

    async with factory() as session:
        admin = User(
            id="admin",
            username="admin",
            name="管理员",
            role="admin",
            department="总部",
            can_view_all=True,
            is_active=True,
            must_change_password=False,
            password_hash=hash_password(__import__("os").environ["SKILLFORGE_TEST_PASSWORD"]),
        )
        # Wave 5 T01/T18 需要：不同角色的测试账号
        dept_admin = User(
            id="bd_tester",
            username="bd_tester",
            name="BD部门管理员",
            role="dept_admin",
            department="BD",
            can_view_all=False,
            is_active=True,
            must_change_password=False,
            password_hash=hash_password(__import__("os").environ["SKILLFORGE_TEST_PASSWORD"]),
        )
        engineer = User(
            id="aibp_test",
            username="aibp_test",
            name="AIBP测试",
            role="aibp",
            department="总部",
            can_view_all=False,
            is_active=True,
            must_change_password=False,
            password_hash=hash_password(__import__("os").environ["SKILLFORGE_TEST_PASSWORD"]),
        )
        observer = User(
            id="observer_test",
            username="observer_test",
            name="观察员测试",
            role="observer",
            department="总部",
            can_view_all=False,
            is_active=True,
            must_change_password=False,
            password_hash=hash_password(__import__("os").environ["SKILLFORGE_TEST_PASSWORD"]),
        )
        session.add_all([admin, dept_admin, engineer, observer])

        root = OrgUnit(id="org-root", name="总部", type="department", path="/org-root", sort_order=0)
        supply = OrgUnit(id="org-supply", name="供应链中心", type="department", parent_id="org-root", path="/org-root/org-supply", sort_order=1)
        bd = OrgUnit(id="org-bd", name="BD", type="department", parent_id="org-root", path="/org-root/org-bd", sort_order=2)
        session.add_all([root, supply, bd])
        session.add_all(
            [
                UserOrgMembership(user_id="admin", org_unit_id="org-root", membership_type="primary", is_manager=True),
                UserOrgMembership(user_id="bd_tester", org_unit_id="org-bd", membership_type="primary", is_manager=True),
                UserOrgMembership(user_id="aibp_test", org_unit_id="org-root", membership_type="primary", is_manager=False),
                UserOrgMembership(user_id="observer_test", org_unit_id="org-root", membership_type="primary", is_manager=False),
            ]
        )

        skill = Skill(
            id="skill-forecast",
            name="inventory_forecast",
            display_name="库存优化助手",
            summary="根据最近订单生成补货建议",
            description="面向运营同学的库存优化建议 Skill。",
            category="运营",
            icon="bar-chart",
            department="总部",
            org_unit_id="org-supply",
            visibility="department",
            status="active",
            owner="admin",
            usage_count=12,
            success_rate=0.95,
            param_ui_schema={
                "type": "object",
                "required": ["keywords"],
                "properties": {
                    "keywords": {"type": "string", "title": "关键词"},
                    "scope": {"type": "string", "title": "范围", "enum": ["全量", "重点商品"]},
                },
            },
            result_ui_schema={
                "type": "table",
                "columns": [
                    {"key": "recommendation", "title": "推荐补货量"},
                    {"key": "reason", "title": "建议原因"},
                ],
            },
            last_run_at=now_bjt(),
        )
        session.add(skill)
        session.add(SkillMember(skill_id="skill-forecast", user_id="admin", role="owner", granted_by="admin"))
        session.add_all([
            SkillTag(skill_id="skill-forecast", tag="库存"),
            SkillTag(skill_id="skill-forecast", tag="补货"),
        ])

        run = ExecutionRun(id="exec-seeded-1", trigger_type="portal", status="completed", started_at=now_bjt(), completed_at=now_bjt())
        session.add(run)
        session.add(
            DecisionLog(
                run_id="exec-seeded-1",
                skill_id="skill-forecast",
                output_result={
                    "data": [{"recommendation": "补货 120 件", "reason": "最近 7 天需求增长 18%"}],
                    "analysis": "最近 7 天需求增长 18%",
                },
            )
        )
        session.add(
            SkillSubmission(
                id="submission-overview-1",
                skill_id="skill-forecast",
                requester_id="admin",
                org_unit_id="org-root",
                params={"keywords": "周例会"},
                execution_id="exec-seeded-1",
                status="completed",
                result_summary={
                    "type": "table",
                    "data": [{"recommendation": "补货 120 件", "reason": "最近 7 天需求增长 18%"}],
                    "analysis": "最近 7 天需求增长 18%",
                },
                created_at=now_bjt(),
                completed_at=now_bjt(),
            )
        )
        await session.commit()


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    from app.auth.router import router as auth_router
    from app.org.router import router as org_router
    from app.portal.router import router as portal_router
    from app.skills.router import router as skills_router
    from app.todos.router import router as todos_router

    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(org_router, prefix="/api/org")
    app.include_router(portal_router, prefix="/api/portal")
    app.include_router(skills_router, prefix="/api/skills")
    app.include_router(todos_router, prefix="/api/todos")

    @app.get("/api/changelog")
    async def changelog(limit: int = 10):
        return {"versions": [{"version": "2026.04.13", "title": "Live E2E backend", "date": "2026-04-13"}][:limit]}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # fake execute_skill for portal submit
    from app.execution.execution_service import execution_service

    async def _fake_execute_skill(skill_id: str, params: dict | None = None, sandbox: bool = False, triggered_by: str = "portal"):
        run_id = "exec-live-submit"
        async with db_mod.async_session_factory() as session:
            run = await session.get(__import__("app.execution.models", fromlist=["ExecutionRun"]).ExecutionRun, run_id)
            if not run:
                from app.execution.models import DecisionLog, ExecutionRun

                session.add(ExecutionRun(id=run_id, trigger_type=triggered_by, status="completed", started_at=now_bjt(), completed_at=now_bjt()))
                session.add(
                    DecisionLog(
                        run_id=run_id,
                        skill_id=skill_id,
                        output_result={
                            "data": [{"recommendation": "补货 120 件", "reason": "最近 7 天需求增长 18%"}],
                            "analysis": "最近 7 天需求增长 18%",
                        },
                    )
                )
                await session.commit()
        return {"run_id": run_id, "status": "completed"}

    execution_service.execute_skill = _fake_execute_skill  # type: ignore[assignment]

    @app.on_event("startup")
    async def _startup() -> None:
        await _prepare_database()

    return app


def main() -> None:
    app = _build_app()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
