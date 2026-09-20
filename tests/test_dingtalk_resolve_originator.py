"""Wave 1 C2：_resolve_originator 的 state + system_admin 收口回归测试。

覆盖：
1. 提供有效 submitter_dingtalk_id → 直接返回（不查库）
2. 提供空 + 存在 active system_admin → 返回该 system_admin 钉钉 ID（优先于 legacy admin）
3. 提供空 + 仅有 disabled admin → 返回 ""
4. 提供空 + 仅有 state=pending admin → 返回 ""
"""
from __future__ import annotations

import pytest

import app.database as db_mod
from app.auth.models import User
from app.dingtalk.approval import _resolve_originator


def _mkuser(uid: str, *, role: str, state: str = "active", is_active: bool = True,
            dingtalk_id: str = "") -> User:
    return User(
        id=uid,
        username=uid,
        name=uid,
        role=role,
        state=state,
        permissions_rev=0,
        is_active=is_active,
        must_change_password=False,
        dingtalk_user_id=dingtalk_id or None,
    )


@pytest.mark.asyncio
async def test_returns_submitter_when_provided(client):
    """传入有效的 submitter_dingtalk_id 直接返回，不查库。"""
    result = await _resolve_originator("dd-caller-123")
    assert result == "dd-caller-123"


@pytest.mark.asyncio
async def test_fallback_prefers_system_admin_over_legacy_admin(client):
    """fallback 时 v2 system_admin 优先于 legacy admin。"""
    async with db_mod.async_session_factory() as s:
        s.add_all([
            _mkuser("legacy", role="admin", dingtalk_id="dd-legacy"),
            _mkuser("sysadm", role="system_admin", dingtalk_id="dd-sys"),
        ])
        await s.commit()

    result = await _resolve_originator("")
    assert result == "dd-sys", "system_admin 应优先"


@pytest.mark.asyncio
async def test_fallback_skips_disabled_admin(client):
    """disabled 状态的 admin 不能被选为审批发起人。"""
    async with db_mod.async_session_factory() as s:
        # 只有一个 admin，但被禁用
        s.add(_mkuser("adm1", role="admin", state="disabled",
                       is_active=False, dingtalk_id="dd-disabled"))
        await s.commit()

    result = await _resolve_originator("")
    assert result == "", "disabled admin 不应被选中"


@pytest.mark.asyncio
async def test_fallback_skips_pending_admin(client):
    """state=pending（钉钉首登未激活）的 admin 不能被选。"""
    async with db_mod.async_session_factory() as s:
        # is_active=True（legacy 字段）但 state=pending（v2 真源）
        s.add(_mkuser("adm2", role="admin", state="pending",
                       is_active=True, dingtalk_id="dd-pending"))
        await s.commit()

    result = await _resolve_originator("")
    assert result == "", "pending admin 不应被选中（只按 is_active 会漏过）"
