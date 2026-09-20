"""role-matrix-v2 组织切片测试。"""

from __future__ import annotations

import re

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as db_mod
from app.auth.models import User
from app.common.exceptions import AppError
from app.config import settings
from app.database import Base
from app.execution.models import OpenClawInstance
from app.org.models import OrgUnit, UserOrgMembership
from app.org import service
from app.skills.core.models import Skill
from app.users.role_matrix import set_user_state


@pytest_asyncio.fixture
async def db():
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$",
        "/skillforge_test",
        str(settings.DATABASE_URL),
    )
    engine = create_async_engine(test_url, echo=False)

    # 注册所有与 skills/users FK 相关的模型，避免 drop_all 顺序错乱
    # （skill_instances / skill_releases / optimizer_sessions 等对 skills.id 有 FK）
    import app.auth.models  # noqa: F401
    import app.common.audit  # noqa: F401
    import app.execution.models  # noqa: F401
    import app.optimizer.models  # noqa: F401
    import app.org.models  # noqa: F401
    import app.skills.asset_models  # noqa: F401
    import app.skills.members  # noqa: F401
    import app.skills.models  # noqa: F401
    import app.todos.models  # noqa: F401

    async with engine.begin() as conn:
        # 强制清空 public schema，绕过历史遗留表 / ORM metadata 未覆盖表的 FK 依赖问题
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    original_engine, original_factory = db_mod.engine, db_mod.async_session_factory
    db_mod.engine, db_mod.async_session_factory = engine, factory

    async with factory() as session:
        yield session

    db_mod.engine, db_mod.async_session_factory = original_engine, original_factory
    await engine.dispose()


async def _create_user(session, *, user_id: str, role: str, department: str | None = None) -> User:
    user = User(
        id=user_id,
        username=user_id,
        name=user_id,
        role=role,
        department=department,
        is_active=True,
        must_change_password=False,
    )
    session.add(user)
    await session.flush()
    await set_user_state(session, user, "active")
    return user


@pytest.mark.asyncio
async def test_merge_org_moves_memberships_skills_and_display_fields(db: AsyncSession):
    admin = await _create_user(db, user_id="sys-merge", role="system_admin")
    source = await service.create_org_unit(db, unit_id="org-source", name="源部门")
    target = await service.create_org_unit(db, unit_id="org-target", name="目标部门")
    member = await _create_user(db, user_id="member-merge", role="aibp", department="源部门")
    dual_member = await _create_user(db, user_id="member-dual", role="observer", department="源部门")
    db.add_all(
        [
            UserOrgMembership(user_id=member.id, org_unit_id=source["id"], membership_type="primary", is_manager=False),
            UserOrgMembership(user_id=dual_member.id, org_unit_id=source["id"], membership_type="secondary", is_manager=True),
            UserOrgMembership(user_id=dual_member.id, org_unit_id=target["id"], membership_type="primary", is_manager=False),
            Skill(id="skill-merge", name="Skill Merge", department="源部门", status="draft", org_unit_id=source["id"]),
            OpenClawInstance(
                id="inst-1",
                name="实例1",
                department="源部门",
                gateway_url="http://gateway",
                reload_hook_url="http://reload",
                reload_token="token",
            ),
        ]
    )
    await db.commit()

    result = await service.merge_org(
        db,
        source_id=source["id"],
        target_id=target["id"],
        operator=admin,
    )
    await db.commit()

    assert result == {
        "source_id": "org-source",
        "target_id": "org-target",
        "affected_users": 2,
        "affected_skills": 1,
        "affected_playbooks": 0,
        "affected_todos": 0,
    }
    merged_memberships = (
        await db.execute(
            select(UserOrgMembership).where(UserOrgMembership.user_id == member.id)
        )
    ).scalars().all()
    assert len(merged_memberships) == 1
    assert merged_memberships[0].org_unit_id == target["id"]

    deduped_memberships = (
        await db.execute(
            select(UserOrgMembership).where(UserOrgMembership.user_id == dual_member.id)
        )
    ).scalars().all()
    assert len(deduped_memberships) == 1
    assert deduped_memberships[0].org_unit_id == target["id"]
    assert deduped_memberships[0].is_manager is True

    merged_skill = await db.get(Skill, "skill-merge")
    assert merged_skill.org_unit_id == target["id"]
    assert merged_skill.department == "目标部门"

    refreshed_member = await db.get(User, member.id)
    assert refreshed_member.department == "目标部门"
    inst = await db.get(OpenClawInstance, "inst-1")
    assert inst.department == "目标部门"

    source_org = await db.get(OrgUnit, source["id"])
    assert source_org is None
    row = (
        await db.execute(
            text("SELECT permissions_rev FROM users WHERE id = :user_id"),
            {"user_id": member.id},
        )
    ).first()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_merge_org_rejects_dept_admin(db: AsyncSession):
    dept_admin = await _create_user(db, user_id="dept-admin-merge", role="dept_admin")
    source = await service.create_org_unit(db, unit_id="org-scope-a", name="A")
    target = await service.create_org_unit(db, unit_id="org-scope-b", name="B")
    await db.commit()

    with pytest.raises(AppError) as exc:
        await service.merge_org(
            db,
            source_id=source["id"],
            target_id=target["id"],
            operator=dept_admin,
        )

    assert exc.value.code == "AUTH_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_add_membership_enforces_dept_admin_scope(db: AsyncSession):
    managed = await service.create_org_unit(db, unit_id="org-managed", name="管辖部门")
    other = await service.create_org_unit(db, unit_id="org-other", name="其他部门")
    dept_admin = await _create_user(db, user_id="dept-admin-scope", role="dept_admin")
    target_user = await _create_user(db, user_id="member-scope", role="observer")
    db.add(UserOrgMembership(user_id=dept_admin.id, org_unit_id=managed["id"], membership_type="primary", is_manager=True))
    await db.commit()

    with pytest.raises(AppError) as exc:
        await service.add_membership(
            db,
            user_id=target_user.id,
            org_unit_id=other["id"],
            operator=dept_admin,
            actor_id=dept_admin.id,
        )

    assert exc.value.code == "AUTH_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_membership_changes_bump_permissions_rev_and_primary_department(db: AsyncSession):
    org = await service.create_org_unit(db, unit_id="org-ai", name="AI组")
    member = await _create_user(db, user_id="member-rev", role="aibp")
    await db.commit()

    await service.add_membership(
        db,
        user_id=member.id,
        org_unit_id=org["id"],
        membership_type="primary",
        actor_id="sys-test",
    )
    await db.commit()
    await db.refresh(member)
    assert member.permissions_rev == 1
    assert member.department == "AI组"

    # An idempotent write must not log a phantom permission change or expire
    # the user's current session again.
    await service.add_membership(
        db,
        user_id=member.id,
        org_unit_id=org["id"],
        membership_type="primary",
        actor_id="sys-test",
    )
    await db.commit()
    await db.refresh(member)
    assert member.permissions_rev == 1

    result = await service.remove_membership(
        db,
        user_id=member.id,
        org_unit_id=org["id"],
        actor_id="sys-test",
    )
    await db.commit()
    await db.refresh(member)
    assert result == {"deleted": True}
    assert member.permissions_rev == 2
    assert member.department is None

    # Idempotent deletion also leaves the revision unchanged.
    result = await service.remove_membership(
        db,
        user_id=member.id,
        org_unit_id=org["id"],
        actor_id="sys-test",
    )
    await db.commit()
    await db.refresh(member)
    assert result == {"deleted": False}
    assert member.permissions_rev == 2
