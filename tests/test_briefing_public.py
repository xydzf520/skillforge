"""C1 收口验证：/briefing 公开页只放行白名单内容。

背景：
    旧实现 `app.mount("/briefing", StaticFiles(directory="docs/overview"))`
    把整个 docs/overview/ 挂公网,导致 project-atlas.md / skillforge_intro.html
    等内部架构地图被未认证用户读取(Critical 级别)。

新实现：
    - GET /briefing               → 308 /briefing/
    - GET /briefing/              → 200 HTML(skillforge_exec_briefing.html)
    - GET /briefing/assets/...    → 仅放行 assets/ 子目录
    - 其它 /briefing/* 一律 404

本测试用例不启动 lifespan,直接复用 app.main 的 app 对象。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


_OVERVIEW_DIR = Path(__file__).resolve().parent.parent / "docs" / "overview"


def _make_transport():
    """构造一个 ASGITransport,绕过 lifespan(避免触发 DB / runtime 启动)。"""
    from app.main import app
    return ASGITransport(app=app)


@pytest.mark.asyncio
async def test_briefing_no_slash_redirects_308():
    """GET /briefing 必须 308 跳到 /briefing/(保持斜杠以便 HTML 相对路径生效)。"""
    transport = _make_transport()
    # 注意 follow_redirects=False,需要明确观察 308
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
        resp = await ac.get("/briefing")
        if not (_OVERVIEW_DIR / "skillforge_exec_briefing.html").is_file():
            assert resp.status_code == 404
            return
        assert resp.status_code == 308, f"期望 308,实际 {resp.status_code}"
        assert resp.headers.get("location") == "/briefing/"


@pytest.mark.asyncio
async def test_briefing_index_returns_html():
    """GET /briefing/ 返回 200 + text/html,且页面有 <title>。"""
    transport = _make_transport()
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/briefing/")
        if not (_OVERVIEW_DIR / "skillforge_exec_briefing.html").is_file():
            assert resp.status_code == 404
            return
        assert resp.status_code == 200, f"期望 200,实际 {resp.status_code}"
        ctype = resp.headers.get("content-type", "")
        assert "text/html" in ctype, f"content-type 应为 text/html,实际 {ctype!r}"
        body = resp.text.lower()
        assert "<title>" in body, "HTML 应含 <title> 标签"


@pytest.mark.asyncio
async def test_briefing_assets_live_png_serves_when_present():
    """GET /briefing/assets/live/skill_studio_live.png 命中 200(资源若不存在则跳过)。"""
    asset = _OVERVIEW_DIR / "assets" / "live" / "skill_studio_live.png"
    if not asset.is_file():
        pytest.skip(f"测试资产不存在: {asset}")

    transport = _make_transport()
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/briefing/assets/live/skill_studio_live.png")
        assert resp.status_code == 200, f"assets 子目录内文件应可访问,实际 {resp.status_code}"
        # PNG content-type 校验(放宽:只要是 image/* 即可)
        ctype = resp.headers.get("content-type", "")
        assert ctype.startswith("image/") or ctype == "application/octet-stream", \
            f"PNG content-type 异常: {ctype!r}"


@pytest.mark.asyncio
async def test_briefing_project_atlas_md_is_404():
    """⚠️ Critical 防线：内部架构图 project-atlas.md 必须 404。"""
    transport = _make_transport()
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/briefing/project-atlas.md")
        assert resp.status_code == 404, (
            f"内部架构地图泄露!project-atlas.md 应该 404,实际 {resp.status_code}\n"
            f"body 前 200 字符: {resp.text[:200]!r}"
        )


@pytest.mark.asyncio
async def test_briefing_intro_html_is_404():
    """⚠️ Critical 防线：skillforge_intro.html 不应可访问。"""
    transport = _make_transport()
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/briefing/skillforge_intro.html")
        assert resp.status_code == 404, (
            f"skillforge_intro.html 应该 404,实际 {resp.status_code}"
        )


@pytest.mark.asyncio
async def test_briefing_path_traversal_blocked():
    """路径穿越攻击不得泄露宿主源码。

    httpx 客户端会把 `/briefing/../app/main.py` 规范化为 `/app/main.py`
    再送给 ASGI server,所以服务端实际收到的是 `/app/main.py` —
    SPA catch-all 会把它兜底回 index.html(状态 200),但 body 是
    Vue 首页,不是 main.py 源码 — 这是符合预期的(不会泄露源)。

    本用例的 Critical 防线是:
      1) 即使 ../ 漏到服务端,也不会读到 app/main.py 真实源码;
      2) 即使收到 200,内容必须是 SPA 而非平台源码。
    """
    transport = _make_transport()
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/briefing/../app/main.py")
        # 不允许泄露 app/main.py 的标志性源码片段
        body = resp.text
        assert "from fastapi import FastAPI" not in body, "app/main.py 源码被泄露!"
        assert "SPAStaticFiles" not in body, "app/main.py 源码被泄露!"
        assert "_BRIEFING_DIR" not in body, "app/main.py 源码被泄露!"
        # 状态可能是 404(若服务端没规范化) 或 200(SPA 兜底回 Vue index.html)
        assert resp.status_code in (200, 400, 404), f"非预期状态码 {resp.status_code}"
