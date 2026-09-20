"""C4：pending 状态用户访问 inbox / todos 必须被 require_state_active 拒绝。

历史背景：v2.0.13 把 inbox & todos 路由从 `get_current_user` 切到
`require_state_active`，但缺乏 negative test。本测试覆盖：
1. pending 用户调用 6 个目标端点 → 403
2. active 用户调用同样端点 → 不会被 401/403（state 通过）

v2.0.14 整改后所有 inbox / 关键 todos 端点全部走 require_state_active，
本测试覆盖 8 个端点（含 reports list / detail）。

为避免 conftest 的 client 高频重建（drop_all + create_all 重）触发并发 PG 死锁，
所有断言压缩到两个测试函数里循环跑，复用同一个 client/engine。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.common.exceptions import AppError, app_error_handler
from app.inbox.router import router as inbox_router
from app.todos.router import router as todos_router
from tests.test_inbox_reports import _make_user

# 8 个 require_state_active 守护的端点（method, path, [body]）
PROTECTED_ENDPOINTS: list[tuple[str, str, dict | None]] = [
    ("GET", "/api/inbox/overview", None),
    ("GET", "/api/inbox/reports", None),
    ("GET", "/api/inbox/reports/abc123", None),
    ("GET", "/api/inbox/reports/unread-count", None),
    ("POST", "/api/inbox/reports/mark-read", {}),
    ("GET", "/api/todos/", None),
    ("POST", "/api/todos/bulk-summary", {"ids": [1]}),
    ("GET", "/api/todos/trends", None),
]


@pytest_asyncio.fixture
async def state_client(client):
    """复用 conftest 的 PG client，但允许测试切换 current_user 的 state。"""
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    state: dict[str, object] = {"current_user": None}
    app.dependency_overrides[get_current_user] = lambda: state["current_user"]
    app.include_router(inbox_router, prefix="/api/inbox")
    app.include_router(todos_router, prefix="/api/todos")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, state


def _make_pending_user():
    user = _make_user("pending_user", role="observer", department="EC")
    user.state = "pending"  # 钉钉首登：账号建出来但未 admin 审批
    user.is_active = True
    return user


def _make_active_user():
    return _make_user(
        "active_user", role="admin", can_view_all=True, department="EC"
    )


@pytest.mark.asyncio
async def test_pending_user_blocked_on_all_protected_endpoints(state_client):
    """pending 用户调被守护端点 → 全部 403 + AUTH_ACCOUNT_NOT_ACTIVE 类错误码。"""
    http, state = state_client
    state["current_user"] = _make_pending_user()

    failures = []
    for method, path, body in PROTECTED_ENDPOINTS:
        if method == "GET":
            resp = await http.get(path)
        else:
            resp = await http.post(path, json=body or {})

        if resp.status_code != 403:
            failures.append(f"{method} {path}: expected 403, got {resp.status_code}")
            continue
        body_json = resp.json()
        err_code = (
            body_json.get("error", {}).get("code")
            if isinstance(body_json.get("error"), dict)
            else None
        ) or body_json.get("code") or ""
        if "ACTIVE" not in err_code.upper() and "AUTH" not in err_code.upper():
            failures.append(f"{method} {path}: unexpected error_code {err_code!r}")
    assert not failures, "pending user assertions failed:\n" + "\n".join(failures)


@pytest.mark.asyncio
async def test_active_user_passes_state_check_on_all_endpoints(state_client):
    """对照组：active 用户调同样端点 → 不应被 401/403（state 通过）。

    业务侧仍可能 422/500（数据未 seed），关键是不能因为 state 被卡。
    """
    http, state = state_client
    state["current_user"] = _make_active_user()

    failures = []
    for method, path, body in PROTECTED_ENDPOINTS:
        if method == "GET":
            resp = await http.get(path)
        else:
            resp = await http.post(path, json=body or {})

        if resp.status_code in (401, 403):
            failures.append(
                f"{method} {path}: active user blocked by state, got "
                f"{resp.status_code} {resp.text!r}"
            )
    assert not failures, "active user assertions failed:\n" + "\n".join(failures)
