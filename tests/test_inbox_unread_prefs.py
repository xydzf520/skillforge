"""GAP-5 / GAP-6：服务端未读红点 + mark-read 回归。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

import app.database as db_mod
from app.common.audit import AuditLog
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime
from app.inbox.models import UserInboxPreference
from app.inbox.user_prefs_service import get_unread_count, mark_reports_read
from tests.test_inbox_reports import (
    _add_decision_log,
    _add_execution_run,
    _add_skill,
    _add_users_and_orgs,
)


async def _seed_reports(count: int, *, base_time: datetime) -> list[int]:
    """建 N 条 decision_log（都有 reports[]），返回 log_id 列表。"""
    await _add_skill(
        skill_id="skill-unread",
        name="Unread Skill",
        visibility="company",
        department="Dept A",
        org_unit_id="dept-a",
    )
    await _add_execution_run("run-unread")
    log_ids = []
    for i in range(count):
        log_id = await _add_decision_log(
            skill_id="skill-unread",
            run_id="run-unread",
            created_at=base_time + timedelta(minutes=i),
            reports=[
                {
                    "channel": "dingtalk_card",
                    "title": f"Report {i}",
                    "summary": "s",
                    "metrics": [],
                }
            ],
        )
        log_ids.append(log_id)
    return log_ids


@pytest.mark.asyncio
async def test_unread_count_initially_counts_all_visible_reports(client):
    users = await _add_users_and_orgs()
    base = datetime(2026, 4, 18, 8, 0, 0)
    log_ids = await _seed_reports(3, base_time=base)
    assert len(log_ids) == 3

    async with db_mod.async_session_factory() as s:
        # admin 全局可见
        result = await get_unread_count(s, current_user=users["admin"])
    assert result["unread"] == 3
    assert result["last_viewed_at"] is None


@pytest.mark.asyncio
async def test_mark_read_zeros_unread_and_writes_audit(client):
    users = await _add_users_and_orgs()
    base = datetime(2026, 4, 18, 8, 0, 0)
    await _seed_reports(2, base_time=base)

    # 标记已读到当前时刻（now 应该 > 所有 log.created_at）
    now = base + timedelta(hours=1)
    async with db_mod.async_session_factory() as s:
        pref_result = await mark_reports_read(
            s, current_user=users["admin"], until=now
        )
        await s.commit()

    assert pref_result["ok"] is True
    assert pref_result["last_viewed_at"] == isoformat_bjt(now)

    # Pref 表有记录
    async with db_mod.async_session_factory() as s:
        pref = await s.get(UserInboxPreference, "admin")
        assert pref is not None
        assert pref.reports_last_viewed_at == now

    # 未读数 = 0
    async with db_mod.async_session_factory() as s:
        result = await get_unread_count(s, current_user=users["admin"])
    assert result["unread"] == 0
    assert result["last_viewed_at"] == isoformat_bjt(now)

    # 再插一条新 report
    await _add_decision_log(
        skill_id="skill-unread",
        run_id="run-unread",
        created_at=now + timedelta(minutes=30),
        reports=[
            {"channel": "dingtalk_card", "title": "Later", "summary": "s", "metrics": []}
        ],
    )
    async with db_mod.async_session_factory() as s:
        result = await get_unread_count(s, current_user=users["admin"])
    assert result["unread"] == 1

    # 审计落库
    async with db_mod.async_session_factory() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "inbox.reports_mark_read")
            )
        ).scalars().all()
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_unread_isolation_between_users(client):
    users = await _add_users_and_orgs()
    base = datetime(2026, 4, 18, 8, 0, 0)
    await _seed_reports(2, base_time=base)

    # 两个用户独立标记
    async with db_mod.async_session_factory() as s:
        await mark_reports_read(s, current_user=users["admin"], until=base + timedelta(hours=1))
        await s.commit()

    # bob 没标过，仍能看到（但 bob 在 Dept B，可见不包含 Dept A 的 company skill... company 可见）
    # skill 是 visibility='company'，bob 应该能看到
    async with db_mod.async_session_factory() as s:
        bob_result = await get_unread_count(s, current_user=users["bob"])
    assert bob_result["unread"] == 2
    assert bob_result["last_viewed_at"] is None


@pytest.mark.asyncio
async def test_mark_read_default_uses_now(client):
    """mark_reports_read 不传 until 则用当前时间。"""
    users = await _add_users_and_orgs()
    await _seed_reports(1, base_time=datetime(2026, 4, 18, 8, 0, 0))

    before = now_bjt()
    async with db_mod.async_session_factory() as s:
        result = await mark_reports_read(s, current_user=users["admin"])
        await s.commit()
    after = now_bjt()

    written = parse_bjt_datetime(result["last_viewed_at"])
    assert before <= written <= after
