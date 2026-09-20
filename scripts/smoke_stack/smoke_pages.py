from __future__ import annotations

import requests

from smoke_stack import smoke_env


def verify_pages(session: requests.Session) -> None:
    for path in (
        "/skills",
        "/skills/list",
        "/skills/demo-skill?perspective=explain",
        "/skills/demo-skill?perspective=review",
        "/reviews",
        "/brain",
        "/admin/openclaw",
    ):
        resp = session.get(f"{smoke_env.BASE_URL}{path}", timeout=10)
        if resp.status_code != 200:
            smoke_env.fail(f"{path} 访问失败: {resp.status_code}")
        if '<div id="app">' not in resp.text or "/assets/" not in resp.text:
            smoke_env.fail(f"{path} 未返回 SPA 页面")
        smoke_env.ok(f"{path} 返回 200")
