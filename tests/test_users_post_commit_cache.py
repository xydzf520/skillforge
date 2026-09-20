"""H4：post-commit cache invalidate 不在 rollback 路径触发。

- commit 失败（rollback）→ invalidate_inbox_permissions_cache 不被调用
- commit 成功 → invalidate 被调用恰好 1 次
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as db_mod
from app.auth.models import User
from app.config import settings
from app.database import Base, get_db
from app.users import service as user_service
from app.users.role_matrix import set_user_state


@pytest_asyncio.fixture
async def db():
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$",
        "/skillforge_test",
        str(settings.DATABASE_URL),
    )
    # 终止该 DB 上残留连接，避免 DROP SCHEMA 因前一轮 test 的 idle 连接死锁
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


async def _create_user(session: AsyncSession, *, user_id: str, role: str) -> User:
    user = User(
        id=user_id,
        username=user_id,
        name=user_id,
        role=role,
        is_active=True,
        must_change_password=False,
    )
    session.add(user)
    await session.flush()
    await set_user_state(session, user, "active")
    return user


@pytest.mark.asyncio
async def test_post_commit_callback_runs_after_successful_commit(db: AsyncSession):
    """commit 成功 → invalidate_inbox_permissions_cache 被调用 1 次。

    模拟 router 路径：service 注册回调，外层（router 模拟）显式 commit + flush。
    """
    sys_admin = await _create_user(db, user_id="post-commit-admin", role="system_admin")
    target = await _create_user(db, user_id="post-commit-target", role="dept_admin")
    await db.commit()

    invalidate_mock = AsyncMock()
    with patch(
        "app.inbox.service.invalidate_inbox_permissions_cache",
        new=invalidate_mock,
    ):
        await user_service.update_user(
            db,
            user_id=target.id,
            role="aibp",
            operator=sys_admin,
        )
        # service 还没让 cache invalidate 触发（待 commit）
        assert invalidate_mock.await_count == 0

        await db.commit()
        await user_service.flush_post_commit_callbacks(db)

    invalidate_mock.assert_awaited_once_with(target.id)


@pytest.mark.asyncio
async def test_post_commit_callback_skipped_on_rollback(db: AsyncSession):
    """commit 失败/rollback → cache invalidate 不被调用。

    通过 get_db() 依赖注入完整 round-trip：在 yield 后我们手动 raise 模拟业务异常。
    """
    del db  # 仅用于把 app.database.* 绑定到当前测试 loop 下的临时 engine/factory
    invalidate_mock = AsyncMock()

    with patch(
        "app.inbox.service.invalidate_inbox_permissions_cache",
        new=invalidate_mock,
    ):
        gen = get_db()
        session = await gen.__anext__()

        # 准备数据
        sys_admin = await _create_user(session, user_id="rb-admin", role="system_admin")
        target = await _create_user(session, user_id="rb-target", role="dept_admin")
        await session.flush()

        await user_service.update_user(
            session,
            user_id=target.id,
            role="aibp",
            operator=sys_admin,
        )

        # 模拟外层抛异常 → get_db 的 except 分支会 rollback + drop callbacks
        try:
            await gen.athrow(RuntimeError("simulated failure"))
        except RuntimeError:
            pass

    # callback 未触发
    invalidate_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_drop_post_commit_callbacks_clears_bucket(db: AsyncSession):
    """_drop_post_commit_callbacks 把已注册回调清空，避免 session 复用泄漏。"""
    sys_admin = await _create_user(db, user_id="drop-admin", role="system_admin")
    target = await _create_user(db, user_id="drop-target", role="dept_admin")
    await db.commit()

    invalidate_mock = AsyncMock()
    with patch(
        "app.inbox.service.invalidate_inbox_permissions_cache",
        new=invalidate_mock,
    ):
        await user_service.update_user(
            db,
            user_id=target.id,
            role="aibp",
            operator=sys_admin,
        )
        # 注册了回调
        assert db.info.get("_post_commit_callbacks")

        user_service._drop_post_commit_callbacks(db)
        assert not db.info.get("_post_commit_callbacks")

        # flush 应该是 no-op
        await user_service.flush_post_commit_callbacks(db)

    invalidate_mock.assert_not_awaited()
