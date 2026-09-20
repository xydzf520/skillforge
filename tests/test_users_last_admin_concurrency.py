"""H5：disable last system_admin TOCTOU 防御。

构造 2 个 active system_admin → 并发 disable 两个 → 必有一个失败
LAST_SYSTEM_ADMIN，DB 至少留 1 个 active system_admin。

advisory xact lock 跨 session 串行化，第二路看到 remaining=0 时拒绝。
"""

from __future__ import annotations

import asyncio
import re

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as db_mod
from app.auth.models import User
from app.common.exceptions import AppError
from app.config import settings
from app.database import Base
from app.users import service as user_service
from app.users.role_matrix import count_active_system_admins, set_user_state


@pytest_asyncio.fixture
async def db():
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$",
        "/skillforge_test",
        str(settings.DATABASE_URL),
    )
    # 先 terminate 该 DB 上其他闲置连接，避免 DROP SCHEMA 死锁
    import asyncpg
    plain_url = test_url.replace("+asyncpg", "")
    _kill = await asyncpg.connect(plain_url)
    try:
        await _kill.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=current_database() AND pid <> pg_backend_pid()"
        )
    finally:
        await _kill.close()

    # 并发测试需要多 session：留 5 个连接位即可，超时迅速以避免阻塞
    engine = create_async_engine(test_url, echo=False, pool_size=5, max_overflow=0)

    import app.auth.models  # noqa: F401
    import app.common.audit  # noqa: F401
    import app.org.models  # noqa: F401
    import app.skills.members  # noqa: F401
    import app.skills.models  # noqa: F401
    import app.todos.models  # noqa: F401

    async with engine.begin() as conn:
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
    _close = await asyncpg.connect(plain_url)
    try:
        await _close.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=current_database() AND pid <> pg_backend_pid() AND state='idle'"
        )
    finally:
        await _close.close()


async def _create_admin(session: AsyncSession, user_id: str) -> User:
    user = User(
        id=user_id,
        username=user_id,
        name=user_id,
        role="system_admin",
        is_active=True,
        must_change_password=False,
    )
    session.add(user)
    await session.flush()
    await set_user_state(session, user, "active")
    return user


@pytest.mark.asyncio
async def test_concurrent_disable_last_two_admins_one_fails(db: AsyncSession):
    """H5 TOCTOU 防御：仅 2 个 active system_admin 时并发 disable，必有一路失败。

    关键：advisory xact lock 串行化 count + set_user_state 临界区。
    第二路在第一路提交后获取 lock，发现 remaining=0 → LAST_SYSTEM_ADMIN。
    """
    a1 = await _create_admin(db, "admin-toctou-1")
    a2 = await _create_admin(db, "admin-toctou-2")
    # 用一个非 admin 的 operator，避免增加 admin 计数
    operator = User(
        id="op-non-admin",
        username="op-non-admin",
        name="op",
        role="aibp",
        is_active=True,
        must_change_password=False,
    )
    db.add(operator)
    await db.flush()
    await set_user_state(db, operator, "active")
    await db.commit()

    factory = db_mod.async_session_factory

    async def _disable(target_id: str):
        async with factory() as s:
            try:
                result = await user_service.disable_user(
                    s,
                    user_id=target_id,
                    operator_id=operator.id,
                )
                await s.commit()
                return ("ok", result)
            except AppError as exc:
                await s.rollback()
                return ("err", exc.code)

    r1, r2 = await asyncio.gather(
        _disable(a1.id),
        _disable(a2.id),
    )

    outcomes = [r1[0], r2[0]]
    # 仅 2 个 admin → 一成功一失败
    assert "ok" in outcomes, f"应至少一路成功，实际 {r1} {r2}"
    assert "err" in outcomes, f"应至少一路失败 LAST_SYSTEM_ADMIN，实际 {r1} {r2}"
    err_pair = r1 if r1[0] == "err" else r2
    assert err_pair[1] == "LAST_SYSTEM_ADMIN"

    # DB 复核：必须留 1 个 active system_admin
    async with factory() as s:
        remaining = await count_active_system_admins(s)
        assert remaining >= 1, f"必须至少留 1 个 active system_admin，实际 {remaining}"


@pytest.mark.asyncio
async def test_disable_with_no_other_admins_raises(db: AsyncSession):
    """单 system_admin 场景：禁用唯一 admin 必然抛 LAST_SYSTEM_ADMIN。"""
    only_admin = await _create_admin(db, "only-admin")
    await db.commit()

    with pytest.raises(AppError) as exc:
        await user_service.disable_user(
            db,
            user_id=only_admin.id,
            operator_id=only_admin.id,
        )
    assert exc.value.code == "LAST_SYSTEM_ADMIN"
