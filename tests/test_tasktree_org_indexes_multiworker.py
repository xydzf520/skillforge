"""Wave 1 C3 + H2：跨 worker 组织树缓存失效回归测试。

模拟两个独立 SQLAlchemy session（= 两个 worker 进程），一个写 + bump 版本号，
另一个下次读 `_load_org_indexes` 必须感知到版本变化并重查，不读陈旧缓存。

同时覆盖 H2：_invalidate_org_cache 在事务 rollback 时，版本号不涨
（因为 bump 是同一事务内的 UPDATE，rollback 自动回滚）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

import app.database as db_mod
from app.common.system_meta import ORG_UNITS_CACHE_KEY, read_cache_version
from app.org.models import OrgUnit
from app.org.service import _invalidate_org_cache
from app.tasktree.service import (
    _ORG_INDEXES_CACHE,
    _load_org_indexes,
    invalidate_tasktree_org_indexes,
)


@pytest.mark.asyncio
async def test_multiworker_cache_invalidation_via_version_stamp(client):
    """worker A 写 + bump version，worker B 再读必须 miss 缓存。"""
    # 清掉本进程的 cache，模拟 cold start
    await invalidate_tasktree_org_indexes()

    # worker B：首次读，装进缓存
    async with db_mod.async_session_factory() as s_b:
        s_b.add(OrgUnit(
            id="mw-root", name="Root", type="company", path="/mw-root", sort_order=0,
        ))
        await s_b.commit()
    async with db_mod.async_session_factory() as s_b:
        v1 = await read_cache_version(s_b, ORG_UNITS_CACHE_KEY)
        assert v1 is not None, "初始 version 应存在"

        # 假装 worker B 之前读过了：手写 cache
        _ORG_INDEXES_CACHE["value"] = ({}, {}, set(), {})  # 空占位
        _ORG_INDEXES_CACHE["expires_at"] = 9e12  # 不 TTL 过期
        _ORG_INDEXES_CACHE["version"] = v1

        # 此时本地缓存应命中
        result_hit = await _load_org_indexes(s_b)
        assert result_hit == ({}, {}, set(), {}), "version 未变，应命中本地缓存"

    # worker A：一个独立 session，写 org_unit + 调 _invalidate_org_cache（bump version）
    async with db_mod.async_session_factory() as s_a:
        unit = OrgUnit(
            id="mw-dept-new",
            name="MW New Dept",
            type="department",
            parent_id="mw-root",
            path="/mw-root/mw-dept-new",
            sort_order=0,
        )
        s_a.add(unit)
        await s_a.flush()
        await _invalidate_org_cache(s_a)
        await s_a.commit()

    # worker B：再读，应感知到 version 变化，miss 缓存，重查 DB
    async with db_mod.async_session_factory() as s_b:
        v2 = await read_cache_version(s_b, ORG_UNITS_CACHE_KEY)
        assert v2 is not None and v2 > v1, f"version 应上涨：{v1} → {v2}"

        # 手动把 worker B 的本地缓存 version 保留在 v1（模拟另一个 worker 没清）
        _ORG_INDEXES_CACHE["value"] = ({}, {}, set(), {})
        _ORG_INDEXES_CACHE["expires_at"] = 9e12
        _ORG_INDEXES_CACHE["version"] = v1

        # 由于 DB version 已变，应强制 miss，返回真实数据（含 mw-dept-new）
        result_miss = await _load_org_indexes(s_b)
        _lv1_by_id, _lv1_by_name, _lv1_names, all_by_name = result_miss
        assert "MW New Dept" in all_by_name, (
            f"version bump 应迫使重查；实际返回 all_by_name={list(all_by_name.keys())!r}"
        )


@pytest.mark.asyncio
async def test_rollback_does_not_bump_version(client):
    """H2：事务 rollback 时 version 不变（bump 跟主业务写入同一 tx）。"""
    await invalidate_tasktree_org_indexes()

    async with db_mod.async_session_factory() as s:
        v_before = await read_cache_version(s, ORG_UNITS_CACHE_KEY)
        assert v_before is not None

    async with db_mod.async_session_factory() as s:
        try:
            s.add(OrgUnit(id="mw-bad", name="Bad", type="department",
                           parent_id=None, path="/mw-bad", sort_order=0))
            await s.flush()
            await _invalidate_org_cache(s)
            # 不 commit，手动 rollback
            await s.rollback()
        finally:
            await s.close()

    async with db_mod.async_session_factory() as s:
        v_after = await read_cache_version(s, ORG_UNITS_CACHE_KEY)
        assert v_after == v_before, (
            f"rollback 后 version 不应上涨：before={v_before} after={v_after}"
        )
