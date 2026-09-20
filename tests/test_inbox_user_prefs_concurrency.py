"""C6：`mark_reports_read` 并发 upsert 回归。

历史背景：v2.0.13 之前 `_get_or_create_pref` 走 SELECT-then-INSERT 模式，多端
登录的同一用户并发 mark-read 会撞主键抛 IntegrityError → 500。本测试用真实
PG 连接（独立 session）并发跑两次 `mark_reports_read`，断言：
1. 不抛异常（ON CONFLICT 兜住主键冲突）
2. 单条 INSERT 路径：第一次写完留下行
3. UPDATE 路径：后续 mark-read 取 `GREATEST` 不会回退时间戳
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

import app.database as db_mod
from app.common.time_utils import isoformat_bjt
from app.inbox.models import UserInboxPreference
from app.inbox.user_prefs_service import mark_reports_read
from tests.test_inbox_reports import _add_users_and_orgs


def _is_postgres() -> bool:
    """conftest 用 settings.DATABASE_URL；测试库 URL 含 postgresql 即为 PG。

    ON CONFLICT 是 PG 方言，sqlite 不支持。本套件 conftest 默认用 PG，
    保留 skipif 保险起见，方便他人切换 sqlite 时不至于 hang。
    """
    from app.config import settings

    return "postgresql" in str(settings.DATABASE_URL).lower()


pytestmark = pytest.mark.skipif(
    not _is_postgres(),
    reason="ON CONFLICT requires PostgreSQL; conftest must use asyncpg",
)


@pytest.mark.asyncio
async def test_mark_reports_read_first_call_inserts_row(client):
    """单条 INSERT 路径：从未 mark 过 → 行被建出来。"""
    users = await _add_users_and_orgs()
    when = datetime(2026, 4, 18, 10, 0, 0)

    async with db_mod.async_session_factory() as s:
        result = await mark_reports_read(s, current_user=users["alice"], until=when)
        await s.commit()

    assert result["ok"] is True
    assert result["last_viewed_at"] == isoformat_bjt(when)

    async with db_mod.async_session_factory() as s:
        pref = await s.get(UserInboxPreference, "alice")
        assert pref is not None
        assert pref.reports_last_viewed_at == when


@pytest.mark.asyncio
async def test_mark_reports_read_second_call_updates_in_place(client):
    """UPDATE 路径：第二次 mark 时间戳更晚 → 落库为更晚的值。"""
    users = await _add_users_and_orgs()
    t0 = datetime(2026, 4, 18, 10, 0, 0)
    t1 = t0 + timedelta(hours=1)

    async with db_mod.async_session_factory() as s:
        await mark_reports_read(s, current_user=users["alice"], until=t0)
        await s.commit()

    async with db_mod.async_session_factory() as s:
        result = await mark_reports_read(s, current_user=users["alice"], until=t1)
        await s.commit()

    assert result["last_viewed_at"] == isoformat_bjt(t1)

    async with db_mod.async_session_factory() as s:
        pref = await s.get(UserInboxPreference, "alice")
        assert pref is not None
        assert pref.reports_last_viewed_at == t1


@pytest.mark.asyncio
async def test_mark_reports_read_greatest_protects_against_regression(client):
    """`GREATEST` 保护：第二次 mark 给一个更早的时间戳 → 落库仍是更晚的那个。

    场景：两端时钟漂移 / 老 mark 请求被晚送达。"""
    users = await _add_users_and_orgs()
    t_late = datetime(2026, 4, 18, 12, 0, 0)
    t_early = datetime(2026, 4, 18, 10, 0, 0)

    async with db_mod.async_session_factory() as s:
        await mark_reports_read(s, current_user=users["alice"], until=t_late)
        await s.commit()

    async with db_mod.async_session_factory() as s:
        result = await mark_reports_read(s, current_user=users["alice"], until=t_early)
        await s.commit()

    # service 返回的应该是 GREATEST(early, late) = late
    assert result["last_viewed_at"] == isoformat_bjt(t_late)

    async with db_mod.async_session_factory() as s:
        pref = await s.get(UserInboxPreference, "alice")
        assert pref is not None
        # 表里仍是更晚的时间戳，没被回退
        assert pref.reports_last_viewed_at == t_late


@pytest.mark.asyncio
async def test_mark_reports_read_concurrent_writes_no_integrity_error(client):
    """并发回归：两个独立 session 同时 mark-read 同一用户 → 不抛 IntegrityError。

    模拟同一用户在两端同时点开报告页的情况。`asyncio.gather` 让两个
    `mark_reports_read` 并发跑（各用独立 session + transaction），
    走 ON CONFLICT 后两边都应该返回成功。
    """
    users = await _add_users_and_orgs()
    t1 = datetime(2026, 4, 18, 10, 0, 0)
    t2 = datetime(2026, 4, 18, 11, 0, 0)

    async def _do_mark(when: datetime) -> dict:
        async with db_mod.async_session_factory() as s:
            r = await mark_reports_read(s, current_user=users["alice"], until=when)
            await s.commit()
            return r

    # 关键：两次并发 → 旧实现会有一边走 INSERT 撞 PK → IntegrityError
    results = await asyncio.gather(_do_mark(t1), _do_mark(t2))
    assert all(r["ok"] is True for r in results)

    # 落库一定是较晚的那个时间戳（GREATEST 保护）
    async with db_mod.async_session_factory() as s:
        pref = await s.get(UserInboxPreference, "alice")
        assert pref is not None
        assert pref.reports_last_viewed_at == max(t1, t2)
