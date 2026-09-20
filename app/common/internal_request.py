"""
内部免认证端点的来源校验（防 SSRF 绕过 / 反向代理头伪造）。

背景：`/api/browser/*-local`、`/api/executions/submit-result` 等端点原本靠
`request.client.host in ("127.0.0.1", "::1", "localhost")` 判断本地调用。
隐患：
1. 若 uvicorn 起 `--proxy-headers`，`request.client` 会被 X-Forwarded-For 改写，
   调用方伪造头即可绕过。
2. `::ffff:127.0.0.1` 这类 IPv6 mapped IPv4 没覆盖。

本模块提供统一的 `is_internal_request()`：
- 显式拒绝任何带转发头（X-Forwarded-For / X-Real-IP / Forwarded）的请求，
  阻止反向代理配错时被伪造。
- 校验 `request.client.host` 落在精确 loopback 集合中（含 IPv6 mapped 形态）。
"""

from __future__ import annotations

from starlette.requests import HTTPConnection


_LOOPBACK_HOSTS = frozenset({
    "127.0.0.1", "::1", "localhost",
    "::ffff:127.0.0.1",
    "0:0:0:0:0:ffff:127.0.0.1",
    "0000:0000:0000:0000:0000:ffff:127.0.0.1",
})

_FORWARD_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")


def is_internal_request(conn: HTTPConnection) -> bool:
    """仅当客户端为 loopback 且无任何转发头时返回 True。

    conn 可以是 Request 或 WebSocket（都继承自 HTTPConnection）。
    """
    # 任一转发头存在即拒绝（正常 loopback 调用不会带这些头）
    headers = conn.headers
    if any(h in headers for h in _FORWARD_HEADERS):
        return False

    client = conn.client
    host = client.host if client else ""
    return host in _LOOPBACK_HOSTS
