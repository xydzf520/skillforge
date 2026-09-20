#!/usr/bin/env python3
"""MCP Server 公共基类 — 统一 JSON-RPC 协议 + 超时保护。

解决问题:
- browser/tmall/yuyidata 3 个 MCP server 无超时保护，一个 tool 卡住阻塞整个进程。
- JSON-RPC 协议层在 4 个文件中逐字重复 ~200 行。
- tmall 同步 handle_tool_call 内 asyncio.run() 嵌套 event loop 风险。

用法:
    class MyServer(AsyncMcpServer):
        TOOLS = [...]
        server_name = "my-server"

        async def handle_tool_call(self, name: str, arguments: dict) -> str:
            ...
            return json.dumps(result)

    if __name__ == "__main__":
        asyncio.run(MyServer().main())
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any


class AsyncMcpServer:
    """async MCP server 基类。

    子类只需提供:
    - TOOLS: list[dict] — MCP 工具定义
    - server_name: str — 服务名 (initialize 回包用)
    - server_version: str — 版本号 (默认 "1.0.0")
    - DEFAULT_TIMEOUT: int — 默认超时秒数 (默认 60)
    - handle_tool_call(name, arguments) -> str — 业务逻辑
    - effective_timeout(name, arguments) -> int | None — 可选，按工具定制超时
    """

    TOOLS: list[dict] = []
    server_name: str = "unnamed"
    server_version: str = "1.0.0"
    DEFAULT_TIMEOUT: int = 60

    # ── 子类重载 ──

    async def handle_tool_call(self, name: str, arguments: dict) -> str:
        raise NotImplementedError

    def effective_timeout(self, tool_name: str, tool_args: dict) -> int:
        """可按工具名/参数定制超时，返回秒。"""
        return self.DEFAULT_TIMEOUT

    # ── JSON-RPC 输出 ──

    def send_response(self, id_val, result) -> None:
        msg = {"jsonrpc": "2.0", "id": id_val, "result": result}
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def send_error(self, id_val, code, message) -> None:
        msg = {"jsonrpc": "2.0", "id": id_val, "error": {"code": code, "message": message}}
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def log(self, msg: str) -> None:
        sys.stderr.write(f"[{self.server_name}-mcp] {msg}\n")
        sys.stderr.flush()

    # ── 请求派发 ──

    async def _handle_tool_call_request(self, req_id: Any, params: dict) -> None:
        tool_name = str(params.get("name", ""))
        tool_args = params.get("arguments", {}) or {}
        timeout = self.effective_timeout(tool_name, tool_args)
        try:
            result_text = await asyncio.wait_for(
                self.handle_tool_call(tool_name, tool_args),
                timeout=timeout,
            )
            self.send_response(req_id, {"content": [{"type": "text", "text": result_text}]})
        except asyncio.TimeoutError:
            self.log(f"tools/call timeout tool={tool_name} timeout={timeout}s")
            self.send_response(req_id, {
                "content": [{"type": "text", "text": f"工具执行超时(>{timeout}s): {tool_name}"}],
                "isError": True,
            })
        except Exception as e:  # noqa: BLE001
            self.log(f"tools/call exception tool={tool_name}: {e!r}")
            self.send_response(req_id, {
                "content": [{"type": "text", "text": f"工具执行异常: {e}"}],
                "isError": True,
            })

    async def _dispatch(self, req: dict) -> None:
        method = req.get("method", "")
        req_id = req.get("id")
        params = req.get("params", {}) or {}

        if method == "initialize":
            self.send_response(req_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": self.server_name, "version": self.server_version},
                "capabilities": {"tools": {}},
            })
        elif method == "notifications/initialized":
            return
        elif method == "tools/list":
            self.send_response(req_id, {"tools": self.TOOLS})
        elif method == "tools/call":
            await self._handle_tool_call_request(req_id, params)
        elif method == "ping":
            self.send_response(req_id, {})
        elif req_id is not None:
            self.send_error(req_id, -32601, f"Method not found: {method}")

    # ── 主循环 ──

    async def main(self) -> None:
        loop = asyncio.get_running_loop()
        pending_tasks: set[asyncio.Task] = set()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line_str = line.strip()
            if not line_str:
                continue
            try:
                req = json.loads(line_str)
            except json.JSONDecodeError:
                self.log(f"invalid JSON line: {line_str[:200]!r}")
                continue
            task = asyncio.create_task(self._dispatch(req))
            pending_tasks.add(task)
            task.add_done_callback(pending_tasks.discard)
        # stdin 关闭后等待所有进行中的 tools/call 完成再退出
        if pending_tasks:
            await asyncio.wait(pending_tasks, timeout=self.DEFAULT_TIMEOUT)


class SyncMcpServer(AsyncMcpServer):
    """同步兼容基类 — 内部仍用 asyncio event loop。

    子类的 handle_tool_call 是同步方法（保持向后兼容），
    基类通过 asyncio.to_thread 并行化 + wait_for 超时保护。
    子类只需提供:
    - TOOLS / server_name / handle_tool_call (sync) / effective_timeout (sync)
    """

    # 子类重载这个 sync 版本
    def handle_tool_call(self, name: str, arguments: dict) -> str:
        raise NotImplementedError

    def effective_timeout(self, tool_name: str, tool_args: dict) -> int:
        return self.DEFAULT_TIMEOUT

    async def _run_sync_tool(self, name: str, arguments: dict) -> str:
        return await asyncio.to_thread(self.handle_tool_call, name, arguments)

    async def _handle_tool_call_request(self, req_id: Any, params: dict) -> None:
        tool_name = str(params.get("name", ""))
        tool_args = params.get("arguments", {}) or {}
        timeout = self.effective_timeout(tool_name, tool_args)
        try:
            result_text = await asyncio.wait_for(
                self._run_sync_tool(tool_name, tool_args),
                timeout=timeout,
            )
            self.send_response(req_id, {"content": [{"type": "text", "text": result_text}]})
        except asyncio.TimeoutError:
            self.log(f"tools/call timeout tool={tool_name} timeout={timeout}s")
            self.send_response(req_id, {
                "content": [{"type": "text", "text": f"工具执行超时(>{timeout}s): {tool_name}"}],
                "isError": True,
            })
        except Exception as e:  # noqa: BLE001
            self.log(f"tools/call exception tool={tool_name}: {e!r}")
            self.send_response(req_id, {
                "content": [{"type": "text", "text": f"工具执行异常: {e}"}],
                "isError": True,
            })
