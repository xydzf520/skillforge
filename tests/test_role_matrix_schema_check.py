"""H3：role_matrix.ensure_role_matrix_columns 只校验、不 ALTER TABLE。

- 列齐 → 不抛、不执行任何 ALTER
- 缺列 → 抛 AppError(ROLE_MATRIX_SCHEMA_MISSING)
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as db_mod
from app.common.exceptions import AppError
from app.config import settings
from app.database import Base
from app.users.role_matrix import ensure_role_matrix_columns


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

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
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


@pytest.mark.asyncio
async def test_ensure_role_matrix_columns_passes_when_schema_present(db: AsyncSession):
    """列齐 → 不抛。回归：迁移已 apply 后启动期不应误报。"""
    # 直接调用，不应抛
    await ensure_role_matrix_columns(db)
    # 重复调用走 per-session 缓存，依然不抛
    await ensure_role_matrix_columns(db)


@pytest.mark.asyncio
async def test_ensure_role_matrix_columns_raises_when_missing(db: AsyncSession):
    """模拟缺列：直接 DROP COLUMN 后再调，应抛 ROLE_MATRIX_SCHEMA_MISSING。"""
    # 清掉缓存标记（fixture 起手已经过 create_all）
    db.info.pop("_role_matrix_v2_ready", None)
    await db.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS state"))
    await db.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS permissions_rev"))
    await db.commit()

    db.info.pop("_role_matrix_v2_ready", None)
    with pytest.raises(AppError) as exc:
        await ensure_role_matrix_columns(db)

    assert exc.value.code == "ROLE_MATRIX_SCHEMA_MISSING"
    detail = exc.value.detail or {}
    missing = set(detail.get("missing_columns", []))
    assert "state" in missing
    assert "permissions_rev" in missing


@pytest.mark.asyncio
async def test_ensure_role_matrix_columns_does_not_alter_table(db: AsyncSession):
    """关键回归：ensure 在请求路径只校验，绝不发 ALTER TABLE。

    通过 monkeypatch session.execute 抓所有 SQL，断言不含 ALTER。
    """
    import sqlalchemy as sa

    issued_sql: list[str] = []
    orig_execute = db.execute

    async def _capturing_execute(stmt, *args, **kwargs):
        # 仅记录字符串语句；ORM Select 等对象同样能转 str
        try:
            issued_sql.append(str(stmt))
        except Exception:  # noqa: BLE001
            issued_sql.append("<unprintable>")
        return await orig_execute(stmt, *args, **kwargs)

    db.execute = _capturing_execute  # type: ignore[assignment]
    try:
        # 重置缓存让其真正跑一次
        db.info.pop("_role_matrix_v2_ready", None)
        await ensure_role_matrix_columns(db)
    finally:
        db.execute = orig_execute  # type: ignore[assignment]

    joined = "\n".join(issued_sql).upper()
    assert "ALTER TABLE" not in joined, (
        f"ensure_role_matrix_columns 不应执行 ALTER TABLE，实际：{issued_sql}"
    )
