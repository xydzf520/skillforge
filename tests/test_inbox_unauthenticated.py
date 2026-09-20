"""T1：未携带 cookie 的请求访问 inbox / todos 端点 → 必须 401。

历史背景：T1 工作的一部分。覆盖 7 个端点（含 list_reports，因为它仍被
get_current_user 守护，无 cookie 时直接 401）。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.common.exceptions import AppError, app_error_handler
from app.inbox.router import router as inbox_router
from app.todos.router import router as todos_router

# 7 个登录态守护的端点（method, path, [body]）
# - list_reports / get_report_detail：get_current_user
# - overview / unread-count / mark-read / list_todos / bulk-summary / trends：require_state_active
# 无 cookie 时两类都会在第一道闸 get_current_user 抛 AUTH_REQUIRED → 401
ENDPOINTS_REQUIRING_AUTH: list[tuple[str, str, dict | None]] = [
    ("GET", "/api/inbox/overview", None),
    ("GET", "/api/inbox/reports", None),
    ("GET", "/api/inbox/reports/unread-count", None),
    ("POST", "/api/inbox/reports/mark-read", {}),
    ("GET", "/api/todos/", None),
    ("POST", "/api/todos/bulk-summary", {"ids": [1]}),
    ("GET", "/api/todos/trends", None),
]


@pytest_asyncio.fixture
async def unauth_client(client):
    """复用 conftest 的 client（PG 已 setup），但**不**注册 get_current_user override。

    这样 cookie 缺失时会真的走到 get_current_user 触发 AUTH_REQUIRED(401)。
    """
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    # 故意不加 dependency_overrides[get_current_user]
    app.include_router(inbox_router, prefix="/api/inbox")
    app.include_router(todos_router, prefix="/api/todos")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_inbox_endpoints_require_authentication(unauth_client):
    """无 cookie 调任何 inbox/todos 端点 → 401 + AUTH_REQUIRED 类错误码。"""
    failures = []
    for method, path, body in ENDPOINTS_REQUIRING_AUTH:
        if method == "GET":
            resp = await unauth_client.get(path)
        else:
            resp = await unauth_client.post(path, json=body or {})

        if resp.status_code != 401:
            failures.append(
                f"{method} {path}: expected 401, got {resp.status_code} {resp.text!r}"
            )
            continue
        body_json = resp.json()
        err_code = (
            body_json.get("error", {}).get("code")
            if isinstance(body_json.get("error"), dict)
            else None
        ) or body_json.get("code") or ""
        # AUTH_REQUIRED / AUTH_SESSION_EXPIRED 都算合法
        if "AUTH" not in err_code.upper():
            failures.append(
                f"{method} {path}: unexpected error_code {err_code!r}"
            )
    assert not failures, "401 negative tests failed:\n" + "\n".join(failures)
