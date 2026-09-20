"""数据采集基类：所有平台采集器继承此类"""

from __future__ import annotations

import json
import asyncio
from typing import Any

import websockets
from loguru import logger

from app.common.retry import RetryableError


class BrowserRetryableError(RetryableError):
    """瞬态浏览器错误：CDP 断开、navigate 超时、远端临时失败 → 可重试。

    与 schemas.CollectionValidationError 区分：schema 失败是定性问题，重试只浪费资源。
    """


# 默认屏蔽：图片/字体/媒体/常见广告统计域。在 sycm 这类 SPA 上能省 50%+ 流量
DEFAULT_BLOCKED_PATTERNS = [
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.ico", "*.svg",
    "*.woff", "*.woff2", "*.ttf", "*.otf", "*.eot",
    "*.mp4", "*.webm", "*.mp3", "*.ogg",
    "*googletagmanager*", "*google-analytics*", "*doubleclick*",
    "*googlesyndication*", "*hm.baidu.com*", "*cnzz.com*", "*tongji.baidu.com*",
]
CDP_CONNECT_TIMEOUT_SECONDS = 8
CDP_COMMAND_TIMEOUT_SECONDS = 20


def build_fetch_json_js(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    body: Any = None,
) -> str:
    """生成在浏览器上下文里 fetch URL 并返回结构化结果的 JS 表达式。

    返回 {url, status, ok, content_type, data?, parse_error?, text_preview?}
    """
    fetch_opts = [
        f"method: {json.dumps((method or 'GET').upper())}",
        "credentials: 'include'",
        "cache: 'no-store'",
    ]
    if headers:
        fetch_opts.append(f"headers: {json.dumps(headers, ensure_ascii=False)}")
    if body is not None:
        if isinstance(body, str):
            fetch_opts.append(f"body: {json.dumps(body, ensure_ascii=False)}")
        else:
            fetch_opts.append(f"body: JSON.stringify({json.dumps(body, ensure_ascii=False)})")
    return f"""
    (async () => {{
      const resp = await fetch({json.dumps(url, ensure_ascii=False)}, {{
        {", ".join(fetch_opts)}
      }});
      const text = await resp.text();
      try {{
        return {{
          url: {json.dumps(url, ensure_ascii=False)},
          status: resp.status,
          ok: resp.ok,
          content_type: resp.headers.get('content-type') || '',
          data: JSON.parse(text),
        }};
      }} catch (e) {{
        return {{
          url: {json.dumps(url, ensure_ascii=False)},
          status: resp.status,
          ok: resp.ok,
          content_type: resp.headers.get('content-type') || '',
          parse_error: String(e),
          text_preview: text.substring(0, 2000),
        }};
      }}
    }})()
    """


class BaseCollector:
    """
    采集器基类。
    通过 CDP WebSocket 连接已运行的 Chrome 实例，
    在真实浏览器环境中采集数据。
    """
    platform: str = ""

    async def run(self, cdp_ws_url: str, task_type: str, params: dict) -> dict:
        """连接 Chrome → 执行采集 → 返回结果"""
        try:
            async with websockets.connect(
                cdp_ws_url,
                max_size=50 * 1024 * 1024,
                open_timeout=CDP_CONNECT_TIMEOUT_SECONDS,
                close_timeout=5,
                ping_timeout=CDP_CONNECT_TIMEOUT_SECONDS,
            ) as ws:
                self._ws = ws
                self._msg_id = 0
                try:
                    return await self.collect(task_type, params)
                except websockets.ConnectionClosed as exc:
                    raise BrowserRetryableError(f"CDP WebSocket 中途断开: {exc}") from exc
                finally:
                    self._ws = None
        except OSError as exc:
            # 包括 ConnectionRefused / TimeoutError 等套接字层异常
            raise BrowserRetryableError(f"CDP 连接失败: {exc}") from exc

    async def collect(self, task_type: str, params: dict) -> dict:
        """子类实现：根据 task_type 分发到具体采集方法"""
        raise NotImplementedError

    # ── CDP 工具方法 ──

    async def _send(self, method: str, params: dict | None = None) -> dict:
        """发送 CDP 命令并等待响应"""
        self._msg_id += 1
        msg = {"id": self._msg_id, "method": method, "params": params or {}}
        msg_id = self._msg_id
        await self._ws.send(json.dumps(msg))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + CDP_COMMAND_TIMEOUT_SECONDS
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError(f"CDP command timeout: {method}")
            resp = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=remaining))
            if resp.get("id") == msg_id:
                if "error" in resp:
                    raise RuntimeError(f"CDP error: {resp['error']}")
                return resp.get("result", {})

    async def enable_resource_blocking(
        self,
        patterns: list[str] | None = None,
    ) -> None:
        """屏蔽图片/字体/广告/统计请求，加速页面加载。

        基于 CDP Network.setBlockedURLs，patterns 为 URL glob。传 None 用默认列表。
        """
        pats = list(patterns) if patterns is not None else DEFAULT_BLOCKED_PATTERNS
        await self._send("Network.enable")
        await self._send("Network.setBlockedURLs", {"urls": pats})

    async def fetch_json(
        self,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        body: Any = None,
    ) -> dict:
        """在当前页面上下文中 fetch URL，复用页面 cookie/UA 等环境。"""
        js = build_fetch_json_js(url, method=method, headers=headers, body=body)
        return await self.evaluate(js) or {}

    async def get_targets(self) -> list[dict]:
        """获取所有 page target"""
        result = await self._send("Target.getTargets")
        return [t for t in result.get("targetInfos", []) if t.get("type") == "page"]

    async def navigate(self, url: str, wait_ms: int = 3000) -> None:
        """导航到指定 URL 并等待加载"""
        await self._send("Page.navigate", {"url": url})
        # 简单等待页面加载
        import asyncio
        await asyncio.sleep(wait_ms / 1000)

    async def evaluate(self, expression: str) -> Any:
        """在页面中执行 JavaScript 并返回结果"""
        result = await self._send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        value = result.get("result", {}).get("value")
        return value

    async def screenshot(self) -> str:
        """截图，返回 base64 编码"""
        result = await self._send("Page.captureScreenshot", {"format": "png"})
        return result.get("data", "")

    async def add_script_before_navigate(self, source: str) -> str:
        """注入在每次新文档加载前自动执行的 JS，返回 identifier 用于移除"""
        # Page 域必须 enable 后 addScriptToEvaluateOnNewDocument 才生效
        await self._send("Page.enable")
        result = await self._send(
            "Page.addScriptToEvaluateOnNewDocument", {"source": source}
        )
        return result.get("identifier", "")

    async def remove_injected_script(self, identifier: str) -> None:
        """移除之前注入的 JS"""
        if identifier:
            await self._send(
                "Page.removeScriptToEvaluateOnNewDocument",
                {"identifier": identifier},
            )


# ── 采集器注册 ──

_COLLECTORS: dict[str, type[BaseCollector]] = {}


def register_collector(cls: type[BaseCollector]) -> type[BaseCollector]:
    """装饰器：注册采集器"""
    _COLLECTORS[cls.platform] = cls
    return cls


def get_collector(platform: str) -> BaseCollector:
    """按平台名获取采集器实例"""
    # 确保所有采集器已导入
    import app.browser.collectors.sycm  # noqa: F401
    import app.browser.collectors.alimama  # noqa: F401
    import app.browser.collectors.generic  # noqa: F401

    cls = _COLLECTORS.get(platform)
    if not cls:
        raise ValueError(f"未注册的采集平台: {platform}，可用: {list(_COLLECTORS.keys())}")
    return cls()
