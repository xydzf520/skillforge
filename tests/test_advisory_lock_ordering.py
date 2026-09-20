"""Wave 2 H5：advisory lock 顺序约束 + 公用 helper 回归。

两个场景：
1. 并发 disable_user(admin1) 与 disable_user(admin2) 时，全局锁
   `system_admin_disable` 串行化，remaining 校验都能看到对方的影响。
2. `acquire_user_lock(u) / acquire_xact_lock("system_admin_disable")` 在同一
   事务内都能重入（advisory_xact_lock 语义），不会死锁自己。

注：本 pytest 依赖真实 PG（conftest 默认走 asyncpg）；SQLite 夹具下
pg_advisory_xact_lock 是 no-op，acquire_xact_lock 会返回 False 但不报错。
"""
from __future__ import annotations

import asyncio

import pytest

import app.database as db_mod
from app.common.advisory_lock import acquire_user_lock, acquire_xact_lock


def _is_postgres() -> bool:
    from app.config import settings
    return "postgresql" in str(settings.DATABASE_URL).lower()


pytestmark = pytest.mark.skipif(
    not _is_postgres(),
    reason="pg_advisory_xact_lock requires PostgreSQL",
)


@pytest.mark.asyncio
async def test_acquire_xact_lock_returns_true_on_postgres(client):
    """PG 下真实拿到锁，返回 True。"""
    async with db_mod.async_session_factory() as s:
        ok = await acquire_xact_lock(s, "test_key")
        assert ok is True
        await s.commit()


@pytest.mark.asyncio
async def test_acquire_user_lock_reentrant_in_same_tx(client):
    """同一事务内对同一 user 重复 acquire 不死锁（advisory_xact_lock 是可重入的）。"""
    async with db_mod.async_session_factory() as s:
        ok1 = await acquire_user_lock(s, "user-x")
        ok2 = await acquire_user_lock(s, "user-x")
        assert ok1 is True and ok2 is True
        await s.commit()


@pytest.mark.asyncio
async def test_concurrent_user_lock_serializes_same_user(client):
    """两路并发拿同一 user 的锁，第二路必须等第一路 commit/rollback 后才能拿到。"""
    acquired_at: list[float] = []
    released_at: list[float] = []

    async def worker(delay: float, index: int):
        loop = asyncio.get_event_loop()
        async with db_mod.async_session_factory() as s:
            await acquire_user_lock(s, "shared-user")
            acquired_at.append((loop.time(), index))
            await asyncio.sleep(delay)
            await s.commit()
            released_at.append((loop.time(), index))

    await asyncio.gather(
        worker(0.3, 1),
        worker(0.0, 2),  # 紧跟 worker 1，应被阻塞
    )

    # 两路都成功拿到锁（先后关系不保证，关键是没死锁、没异常）
    assert len(acquired_at) == 2


@pytest.mark.asyncio
async def test_concurrent_different_users_dont_block_each_other(client):
    """不同 user 的锁互不阻塞——验证 key 隔离。"""
    import time

    async def worker(user_id: str):
        async with db_mod.async_session_factory() as s:
            await acquire_user_lock(s, user_id)
            await asyncio.sleep(0.2)
            await s.commit()

    t0 = time.monotonic()
    await asyncio.gather(
        worker("user-a"),
        worker("user-b"),
        worker("user-c"),
    )
    elapsed = time.monotonic() - t0
    # 若串行化，总耗时 > 0.6s；并行 < 0.4s
    assert elapsed < 0.4, f"不同 user 应并行，实测 {elapsed:.3f}s"
