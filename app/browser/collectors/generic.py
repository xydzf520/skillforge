"""通用采集器：导航任意 URL，探索页面结构，按需提取数据，抓包 API"""

import asyncio
import hashlib
import json

from app.browser.collector_base import BaseCollector, register_collector


@register_collector
class GenericCollector(BaseCollector):
    """
    通用页面探索和数据提取。
    AI agent 用这个来发现任意平台的数据结构。
    """
    platform = "generic"

    async def collect(self, task_type: str, params: dict) -> dict:
        dispatch = {
            "explore": self._explore,
            "extract": self._extract,
            "screenshot": self._screenshot,
            "capture_apis": self._capture_apis,
        }
        fn = dispatch.get(task_type)
        if not fn:
            raise ValueError(f"generic 不支持: {task_type}，可用: {list(dispatch.keys())}")
        return await fn(params)

    async def _explore(self, params: dict) -> dict:
        """
        导航到 URL，返回页面结构化摘要。
        AI agent 用这个了解页面有什么数据。

        params:
          url: 要探索的页面 URL
        """
        url = params.get("url")
        if not url:
            raise ValueError("缺少 url 参数")

        await self.navigate(url, wait_ms=params.get("wait_ms", 5000))

        result = await self.evaluate("""
        (() => {
            const result = {
                url: location.href,
                title: document.title,
                tables: [],
                data_cards: [],
                links: [],
                forms: [],
                text_blocks: [],
            };

            // 1. 提取所有表格
            document.querySelectorAll('table').forEach((table, ti) => {
                const headers = [];
                table.querySelectorAll('th').forEach(th => headers.push(th.textContent.trim()));
                const rows = [];
                table.querySelectorAll('tbody tr').forEach((tr, ri) => {
                    if (ri >= 5) return; // 只取前 5 行示例
                    const cells = [];
                    tr.querySelectorAll('td').forEach(td => cells.push(td.textContent.trim()));
                    rows.push(cells);
                });
                if (headers.length || rows.length) {
                    result.tables.push({
                        index: ti,
                        headers,
                        row_count: table.querySelectorAll('tbody tr').length,
                        sample_rows: rows,
                    });
                }
            });

            // 2. 提取数据卡片（带标签+数值的元素）
            const seen = new Set();
            const cardSelectors = [
                '[class*="data"]', '[class*="card"]', '[class*="metric"]',
                '[class*="summary"]', '[class*="stat"]', '[class*="kpi"]',
                '[class*="overview"]', '[class*="indicator"]',
            ];
            for (const sel of cardSelectors) {
                document.querySelectorAll(sel).forEach(el => {
                    const label = el.querySelector('[class*="label"], [class*="name"], [class*="title"], [class*="desc"], dt, .desc');
                    const value = el.querySelector('[class*="value"], [class*="num"], [class*="count"], [class*="amount"], dd, .number');
                    if (label && value) {
                        const key = label.textContent.trim();
                        const val = value.textContent.trim();
                        if (key && val && !seen.has(key) && key.length < 30) {
                            seen.add(key);
                            result.data_cards.push({ label: key, value: val });
                        }
                    }
                });
            }

            // 3. 提取导航链接（二级菜单、Tab 等）
            document.querySelectorAll('nav a, [class*="tab"] a, [class*="menu"] a, [role="tab"]').forEach(a => {
                const text = a.textContent.trim();
                const href = a.href || '';
                if (text && text.length < 30 && result.links.length < 20) {
                    result.links.push({ text, href: href.startsWith('http') ? href : '' });
                }
            });

            // 4. 提取表单字段
            document.querySelectorAll('form, [class*="filter"], [class*="search"]').forEach(form => {
                const fields = [];
                form.querySelectorAll('input, select').forEach(el => {
                    fields.push({
                        type: el.tagName.toLowerCase(),
                        name: el.name || el.id || '',
                        placeholder: el.placeholder || '',
                    });
                });
                if (fields.length) result.forms.push({ fields });
            });

            // 5. 提取可见文本块（大标题、段落）
            document.querySelectorAll('h1, h2, h3, [class*="title"]').forEach(el => {
                const text = el.textContent.trim();
                if (text && text.length > 2 && text.length < 60 && result.text_blocks.length < 10) {
                    result.text_blocks.push(text);
                }
            });

            return result;
        })()
        """)

        return result or {"url": url, "error": "页面数据提取为空"}

    async def _extract(self, params: dict) -> dict:
        """
        在当前页面按选择器或 JS 表达式提取数据。

        params:
          selector: CSS 选择器（可选）
          js: JavaScript 表达式（可选）
          url: 先导航到此 URL（可选）
        """
        if params.get("url"):
            await self.navigate(params["url"], wait_ms=params.get("wait_ms", 5000))

        if params.get("js"):
            data = await self.evaluate(params["js"])
            return {"type": "js_result", "data": data}

        selector = params.get("selector")
        if not selector:
            raise ValueError("缺少 selector 或 js 参数")

        data = await self.evaluate(f"""
        (() => {{
            const els = document.querySelectorAll({repr(selector)});
            return Array.from(els).slice(0, 50).map((el, i) => ({{
                index: i,
                tag: el.tagName.toLowerCase(),
                text: el.textContent.trim().substring(0, 200),
                html: el.outerHTML.substring(0, 500),
            }}));
        }})()
        """)

        return {"type": "selector_result", "selector": selector, "count": len(data) if data else 0, "items": data}

    async def _screenshot(self, params: dict) -> dict:
        """截取当前页面或指定 URL 的截图"""
        if params.get("url"):
            await self.navigate(params["url"], wait_ms=params.get("wait_ms", 3000))

        img = await self.screenshot()
        return {"type": "screenshot", "format": "png_base64", "data": img[:100] + "...(truncated)"}

    # ── API 抓包 ──

    # 注入到页面加载前的 fetch/XHR 拦截器
    _INTERCEPTOR_JS = r"""
    window.__captured_apis = [];
    window.__sfSha256 = async function(text) {
        try {
            if (!window.crypto || !window.crypto.subtle || !window.TextEncoder) return null;
            const bytes = new TextEncoder().encode(text || '');
            const digest = await window.crypto.subtle.digest('SHA-256', bytes);
            return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
        } catch(e) {
            return null;
        }
    };

    // ── 拦截 fetch ──
    const _origFetch = window.fetch;
    window.fetch = async function(input, init) {
        const url = typeof input === 'string' ? input : (input && input.url ? input.url : String(input));
        const method = (init && init.method) || 'GET';
        const reqBody = init && init.body ? String(init.body) : '';
        try {
            const resp = await _origFetch.apply(this, arguments);
            const clone = resp.clone();
            const text = await clone.text().catch(() => '');
            window.__captured_apis.push({
                type: 'fetch', url: url, method: method,
                status: resp.status,
                content_type: resp.headers.get('content-type') || '',
                request_body_length: reqBody.length,
                request_body_preview: reqBody.substring(0, 2000),
                response_hash: await window.__sfSha256(text),
                body_length: text.length,
                body_preview: text.substring(0, 2000),
            });
            return resp;
        } catch(e) {
            return _origFetch.apply(this, arguments);
        }
    };

    // ── 拦截 XMLHttpRequest ──
    const _origOpen = XMLHttpRequest.prototype.open;
    const _origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url) {
        this.__m = method; this.__u = url;
        return _origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function(body) {
        const reqBody = body ? String(body) : '';
        this.addEventListener('load', function() {
            try {
                const text = this.responseText || '';
                const rec = {
                    type: 'xhr', url: this.__u, method: this.__m,
                    status: this.status,
                    content_type: this.getResponseHeader('content-type') || '',
                    request_body_length: reqBody.length,
                    request_body_preview: reqBody.substring(0, 2000),
                    body_length: text.length,
                    body_preview: text.substring(0, 2000),
                };
                window.__captured_apis.push(rec);
                window.__sfSha256(text).then(h => { rec.response_hash = h; }).catch(() => {});
            } catch(e) {}
        });
        return _origSend.apply(this, arguments);
    };
    """

    async def _capture_apis(self, params: dict) -> dict:
        """
        导航到 URL，抓包页面发出的所有 XHR/fetch API 调用。
        双重采集: 拦截器捕获响应体 + Performance API 兜底发现 URL。

        params:
          url: 要抓包的页面 URL (必填)
          wait_ms: 等待页面 API 调用完成的时间，默认 8000ms
        """
        url = params.get("url")
        if not url:
            raise ValueError("缺少 url 参数")

        # SSRF 防御：capture_apis 可被直接通过 /collect 触发，必须挡 loopback/内网/元数据
        from app.browser.service import guard_discover_url
        url = guard_discover_url(url)

        # 1. 注入拦截器（在页面 JS 之前执行）
        script_id = await self.add_script_before_navigate(self._INTERCEPTOR_JS)

        try:
            # 2. 启用 CDP Network 域。页面自带 fetch/XHR monkey patch 在部分
            # 微前端/iframe 场景会漏，CDP 事件作为更底层的响应体抓取路径。
            await self._send("Network.enable", {"maxPostDataSize": 256 * 1024})

            # 3. 导航并在等待期间持续读取 CDP 事件。若等页面加载完再读，
            # Chrome/CDP 可能已丢弃部分早期 XHR 事件。
            network_events = await self._navigate_and_collect_network(
                url,
                wait_ms=int(params.get("wait_ms", 8000)),
            )

            # 4. 读取导航期间的 Network 事件，并尝试通过 CDP 取响应体。
            cdp_intercepted = await self._collect_cdp_response_bodies(network_events)

            # 5. 读取页面拦截器捕获结果（有响应体）
            raw = await self.evaluate("JSON.stringify(window.__captured_apis || [])")
            intercepted = json.loads(raw) if raw else []

            # 6. Performance API 兜底（微前端懒加载的请求拦截器可能漏掉）
            perf_raw = await self.evaluate("""
            (() => {
                const dataScriptHints = [
                    'mtop', 'h5api', '/api/', '/rest/', '/openapi/', '/alimama/',
                    'onebp', 'report', 'rpt', 'json', 'jsonp', 'callback=', '.do?'
                ];
                function shouldCapture(e) {
                    if (e.initiatorType === 'fetch' || e.initiatorType === 'xmlhttprequest') return true;
                    if (e.initiatorType !== 'script') return false;
                    const u = String(e.name || '').toLowerCase();
                    return dataScriptHints.some(h => u.includes(h));
                }
                return JSON.stringify(
                    performance.getEntriesByType('resource')
                        .filter(shouldCapture)
                        .map(e => ({url: e.name, type: e.initiatorType}))
                );
            })()
            """)
            perf_entries = json.loads(perf_raw) if perf_raw else []
        finally:
            # 7. 清理拦截器
            await self.remove_injected_script(script_id)

        # 8. 合并: 以拦截器/CDP 为主（有响应体），Performance API 补漏
        # URL 归一化: //host/path → https://host/path, 去掉查询参数做去重
        def _norm(u: str) -> str:
            if u.startswith("//"):
                u = "https:" + u
            return u.split("?")[0]
        body_captured = [*intercepted, *cdp_intercepted]
        intercepted_paths = {_norm(a.get("url", "")) for a in body_captured}
        SKIP_EXTS = (".js", ".css", ".png", ".jpg", ".gif", ".svg",
                      ".woff", ".woff2", ".ttf", ".ico")

        data_apis = []
        # 8a. 处理拦截器/CDP 抓到的（有响应体）
        for api in body_captured:
            entry = self._make_api_entry(api, SKIP_EXTS)
            if entry:
                entry["source"] = api.get("source") or "intercepted"
                data_apis.append(entry)

        # 8b. Performance API 补漏（只有 URL，没有响应体）
        for pe in perf_entries:
            pe_url = pe.get("url", "")
            if _norm(pe_url) in intercepted_paths:
                continue
            if any(pe_url.split("?")[0].endswith(ext) for ext in SKIP_EXTS):
                continue
            # 只保留 .json 路径或不含文件扩展名的（API 路径）
            path = pe_url.split("?")[0]
            if "." in path.split("/")[-1] and not path.endswith(".json"):
                continue
            data_apis.append({
                "source": "perf_api",
                "type": pe.get("type", "fetch"),
                "method": "GET",
                "url": pe_url,
                "status": None,
                "hint": "拦截器未捕获响应体, 用 extract+js fetch 此 URL 获取数据",
            })

        return {
            "url": url,
            "total_captured": len(body_captured),
            "js_captured": len(intercepted),
            "cdp_captured": len(cdp_intercepted),
            "perf_api_found": len(perf_entries),
            "data_apis_count": len(data_apis),
            "data_apis": data_apis,
        }

    async def _navigate_and_collect_network(self, url: str, wait_ms: int = 8000) -> list[dict]:
        """Page.navigate 后持续读取 WebSocket，避免 Network 事件在等待期间丢失。"""
        self._msg_id += 1
        nav_id = self._msg_id
        await self._ws.send(json.dumps({
            "id": nav_id,
            "method": "Page.navigate",
            "params": {"url": url},
        }))

        events: list[dict] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_ms / 1000
        while loop.time() < deadline:
            try:
                msg = await asyncio.wait_for(
                    self._ws.recv(),
                    timeout=min(0.25, max(0.01, deadline - loop.time())),
                )
            except asyncio.TimeoutError:
                continue
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                continue
            if payload.get("id") == nav_id:
                if "error" in payload:
                    raise RuntimeError(f"CDP error: {payload['error']}")
                continue
            method = payload.get("method", "")
            if isinstance(method, str) and method.startswith("Network."):
                events.append(payload)
        return events

    async def _drain_network_events(self, quiet_ms: int = 250, max_ms: int = 1500) -> list[dict]:
        """读取当前 WebSocket 队列里的 CDP Network 事件。

        BaseCollector._send 会在等待命令响应时跳过事件；抓包场景需要主动
        drain 一次，拿到 requestId 后再调用 Network.getResponseBody。
        """
        events: list[dict] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_ms / 1000
        quiet = quiet_ms / 1000
        while loop.time() < deadline:
            timeout = min(quiet, max(0.01, deadline - loop.time()))
            try:
                msg = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                break
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                continue
            method = payload.get("method", "")
            if isinstance(method, str) and method.startswith("Network."):
                events.append(payload)
        return events

    async def _collect_cdp_response_bodies(self, events: list[dict]) -> list[dict]:
        requests: dict[str, dict] = {}
        responses: dict[str, dict] = {}
        for event in events:
            method = event.get("method")
            params = event.get("params") or {}
            request_id = params.get("requestId")
            if not request_id:
                continue
            if method == "Network.requestWillBeSent":
                requests[request_id] = params.get("request") or {}
            elif method == "Network.responseReceived":
                responses[request_id] = params

        captured: list[dict] = []
        for request_id, params in responses.items():
            response = params.get("response") or {}
            url = response.get("url") or ""
            resource_type = str(params.get("type") or "").lower()
            if not self._looks_like_data_api(url, resource_type):
                continue
            req = requests.get(request_id) or {}
            body = ""
            body_error = None
            try:
                body_resp = await self._send("Network.getResponseBody", {"requestId": request_id})
                body = body_resp.get("body") or ""
                if body_resp.get("base64Encoded"):
                    body_error = "base64Encoded body skipped"
                    body = ""
            except Exception as exc:  # noqa: BLE001
                body_error = str(exc)[:200]
            entry = {
                "source": "cdp_network",
                "type": resource_type or "network",
                "url": url,
                "method": req.get("method") or "GET",
                "status": response.get("status"),
                "content_type": response.get("mimeType") or "",
                "request_body_length": len(req.get("postData") or ""),
                "request_body_preview": (req.get("postData") or "")[:2000],
                "body_length": len(body),
                "body_preview": body[:2000],
            }
            if body:
                entry["response_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if body_error:
                entry["body_error"] = body_error
            captured.append(entry)
        return captured

    @staticmethod
    def _looks_like_data_api(url: str, resource_type: str = "") -> bool:
        lowered = (url or "").lower()
        if resource_type in {"xhr", "fetch"}:
            return True
        hints = (
            "mtop", "h5api", "/api/", "/rest/", "/openapi/", "/alimama/",
            "onebp", "report", "rpt", ".json", "jsonp", "callback=", ".do?",
        )
        return any(hint in lowered for hint in hints)

    @staticmethod
    def _make_api_entry(api: dict, skip_exts: tuple) -> dict | None:
        """从拦截器记录构造 API 条目，返回 None 表示跳过"""
        api_url = api.get("url", "")
        ct = api.get("content_type", "")
        if any(api_url.split("?")[0].endswith(ext) for ext in skip_exts):
            return None
        if "json" not in ct and "text" not in ct and "javascript" not in ct:
            return None

        body = api.get("body_preview", "")
        entry = {
            "type": api["type"],
            "method": api.get("method", "GET"),
            "url": api_url,
            "status": api.get("status"),
            "content_type": ct,
        }
        if api.get("response_hash"):
            entry["response_hash"] = api["response_hash"]
        elif body:
            entry["response_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if api.get("body_length") is not None:
            entry["body_length"] = api.get("body_length")
        if api.get("request_body_length") is not None:
            entry["request_body_length"] = api.get("request_body_length")
        if api.get("request_body_preview"):
            entry["request_body_preview"] = api.get("request_body_preview", "")[:800]
        try:
            parsed = json.loads(body)
            if isinstance(parsed, list):
                entry["row_count"] = len(parsed)
                if parsed and isinstance(parsed[0], dict):
                    entry["data_keys"] = list(parsed[0].keys())[:20]
            if isinstance(parsed, dict):
                entry["response_keys"] = list(parsed.keys())[:15]
                data_val = parsed.get("data")
                if isinstance(data_val, dict):
                    entry["data_keys"] = list(data_val.keys())[:20]
                elif isinstance(data_val, list) and data_val:
                    entry["data_sample_keys"] = (
                        list(data_val[0].keys())[:20]
                        if isinstance(data_val[0], dict) else None
                    )
                    entry["data_count"] = len(data_val)
                    entry["row_count"] = len(data_val)
            entry["body_preview"] = body[:800]
        except (json.JSONDecodeError, TypeError):
            entry["body_preview"] = body[:300]
        return entry
