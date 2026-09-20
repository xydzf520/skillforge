"""
Historical stream-session compatibility helpers owned by the platform.

The CLI launcher has been removed. start() always reports runtime unavailable.
The event/permission helpers remain for historical protocol regression tests;
new adapters must provide their own transport, not revive the old CLI flags.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path
from typing import Any, AsyncIterator

from loguru import logger

from app.common.exceptions import AppError
from app.coding_agent.local_path_guard import find_first_unavailable_local_path
from app.coding_agent.schemas import CodingAgentErrorCode
from app.coding_agent.stream_protocol import (
    build_interrupt_request,
    build_permission_response,
    build_user_message,
    decode_line,
    encode_message,
)
from app.coding_agent.runtime_availability import require_authoring_runtime


#: 透传给 aiclawcode Node 子进程的环境变量白名单。
#:
#: ⚠️ 安全(C7 收口): 不能 os.environ.copy() 全量透传 —
#:   子进程会拿到 DATABASE_URL / DINGTALK_APP_SECRET /
#:   SKILLFORGE_SESSION_SECRET / ANTHROPIC_API_KEY 等敏感变量,
#:   一旦子进程被攻陷或 prompt injection 诱导调 Bash echo $X,
#:   就直接外泄。这里只放行 Node + LLM 网关运行所必需的基础环境;
#:   敏感凭据通过调用方主动构造的 _env_override 显式传入。
#:
#: 与 app.common.contract_schema._SAFE_ENV_KEYS 同步维护(那边
#: 是给 LLM 生成的 main.py 用的,这边是给 aiclawcode CLI 用的,
#: 用途不同所以不直接 import 复用,允许独立增删)。
_SAFE_ENV_KEYS: frozenset[str] = frozenset(
    {
        # 通用基础
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TMPDIR",
        # Node.js
        "NODE_OPTIONS",
        "NODE_PATH",
        "NODE_ENV",
        # 网络代理 / 证书(curl/fetch 走代理时需要)
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        # LLM 网关地址 / 鉴权(同时也接受调用方通过 _env_override 显式传)
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
    }
)


def _build_subprocess_env(env_override: dict[str, str]) -> dict[str, str]:
    """构造 aiclawcode 子进程的最终 env。

    步骤:
      1) 从 os.environ 中只挑 _SAFE_ENV_KEYS 白名单内的键;
      2) 用调用方主动注入的 env_override 覆盖(其中常见的是
         ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL —— 这些必须保留);
      3) 强制注入 NODE_OPTIONS 的 --max-old-space-size,避免 V8 OOM。

    返回新 dict;不修改 os.environ 也不修改 env_override。
    """
    env: dict[str, str] = {
        k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS
    }
    env.update(env_override or {})

    # Node.js V8 内存限制(RLIMIT_AS 对 V8 无效,V8 预分配大虚拟空间)
    env.setdefault("NODE_OPTIONS", "")
    if "--max-old-space-size" not in env["NODE_OPTIONS"]:
        env["NODE_OPTIONS"] = f"{env['NODE_OPTIONS']} --max-old-space-size=1024".strip()

    return env


class SubprocessSession:
    """单 Node 子进程会话。线程安全：所有公共方法都是 async。"""

    def __init__(
        self,
        *,
        skill_id: str,
        user_id: str,
        work_dir: Path,
        env: dict[str, str] | None = None,
        provider: str | None = None,   # 例如 "tencent" / "glm" / "kimi"
        model: str | None = None,      # 例如 "glm-5" / "tc-code-latest"
        permission_mode: str = "acceptEdits",  # default/acceptEdits/bypassPermissions
        max_turns: int | None = 30,
        system_prompt_append: str | None = None,  # 注入到 system prompt 末尾的引导
        mcp_config_json: str | None = None,  # JSON 字符串, 见 mcp_config.build_mcp_config_json
    ):
        self.skill_id = skill_id
        self.user_id = user_id
        self.work_dir = Path(work_dir).resolve()
        self._env_override = env or {}
        self._provider = provider
        self._model = model
        self._permission_mode = permission_mode
        self._max_turns = max_turns
        self._system_prompt_append = system_prompt_append
        self._mcp_config_json = mcp_config_json

        self.proc: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None  # 由 system/init 事件填充
        self.model: str | None = None
        self.tools: list[str] = []

        self._event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=1024)
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stdin_lock = asyncio.Lock()
        self._closed = False
        self.last_active_at = asyncio.get_event_loop().time()
        self.prompt_hash: str | None = None
        self.config_dir: Path | None = None
        self.runtime_profile: dict[str, Any] | None = None
        # 未完成的 tool_use 数 (tool_use - tool_result)。> 0 时 idle gc 必须跳过,
        # 避免一个长时间运行的 Bash 命令 (例如同步等 30 分钟外部 API) 期间 session
        # 被 session_pool gc 错误回收 → SIGTERM 中断 → 用户看到"AI 卡住"
        self.pending_tool_calls: int = 0
        # [M5] 健壮性诊断指标 — 暴露给 GC / metrics, 也用于触发告警
        self._parse_failure_count: int = 0   # 累计 decode_line 返回 None 的次数
        self._dropped_event_count: int = 0   # event_queue 满导致丢弃事件的累计次数

        # [resume-v2] 前端事件缓冲 — 断连重连时回放给新 WS
        #
        # ⚠️ 为什么不按 raw seq 回放:
        #   原始 stream-json 的一条 raw event 经 _translate_event 会产生 0~N 个
        #   翻译后的 Event (例如一条 assistant raw 可能同时产生 TEXT_DELTA +
        #   TOOL_CALL + FILE_CHANGE + USAGE 多个事件)。如果所有这些兄弟事件都共享
        #   同一个 raw._seq, 前端收到第一条就把 lastSeq 推进到 raw._seq, 下次 resume
        #   请求 `> lastSeq` 就会把**整条 raw 的剩余兄弟事件全部跳过** → 永久丢失。
        #
        # ✅ 正确做法: 给每个**翻译后**的 Event 分配独立单调 fe_seq, 缓冲翻译后的
        #    事件 dict (而不是 raw), resume 时按 fe_seq 过滤。
        self._fe_seq: int = 0                         # 前端事件单调游标
        self._fe_history: list[dict[str, Any]] = []   # 缓冲翻译后的 event dict
        self._fe_history_max: int = 500

        # [resume-v2] 活跃 consumer token — 防止旧 chat() 的 stream_events 还没退出
        # 新 resume_stream() 就进入第二个 stream_events 循环, 两个消费者抢同一 queue
        # 导致事件随机分流。新 consumer 进入前替换 token, 旧 consumer 每轮检测发现
        # token 被替换就优雅退出。
        self._active_stream_token: int = 0

    # ─────────────────────────────────────────────────────
    # 启动
    # ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Compatibility shell for platform event tests; no CLI can be launched.

        The removed runtime is not a supported adapter. A future adapter must
        implement its own session transport and pass the acceptance contract.
        """
        if not self.work_dir.exists():
            raise AppError(
                CodingAgentErrorCode.SKILL_DIR_NOT_FOUND.value,
                404,
                {"work_dir": str(self.work_dir)},
            )
        require_authoring_runtime()

    def absorb_init_event(self, event: dict) -> None:
        """
        chat() 在收到 system/init 事件时调用，回填 session_id / model / tools。
        多轮模式下每轮都会有 init，我们只在第一次填，之后保持稳定。
        """
        if self.session_id is None:
            self.session_id = event.get("session_id")
            self.model = event.get("model") or self.model
            self.tools = list(event.get("tools") or [])
            logger.info(
                f"CodingAgent first init: skill={self.skill_id} user={self.user_id} "
                f"session={self.session_id} model={self.model} tools={len(self.tools)}"
            )

    # ─────────────────────────────────────────────────────
    # 写入 stdin
    # ─────────────────────────────────────────────────────

    async def send_user_message(self, content: str) -> None:
        path_issue = find_first_unavailable_local_path(content)
        if path_issue is not None:
            raise AppError(
                CodingAgentErrorCode.LOCAL_PATH_UNAVAILABLE.value,
                409,
                path_issue.detail(),
            )
        await self._send(build_user_message(content))

    async def respond_permission(
        self,
        request_id: str,
        behavior: str,
        *,
        updated_input: dict | None = None,
        message: str | None = None,
    ) -> None:
        await self._send(build_permission_response(
            request_id, behavior, updated_input=updated_input, message=message,
        ))
        # [H9] deny 时主动 dec pending_tool_calls — 防止 aiclawcode 在拒绝后
        # 不发对应的 tool_result, 计数器永远 > 0 导致 GC 永远不清理这个会话。
        # reader_loop 之后若仍收到 tool_result 也无害, 因为有 max(0, ...) 下界保护。
        if behavior == "deny":
            if self.pending_tool_calls > 0:
                self.pending_tool_calls -= 1

    async def send_interrupt(self) -> None:
        await self._send(build_interrupt_request())

    async def _send(self, msg: dict) -> None:
        if self._closed or not self.proc or not self.proc.stdin:
            raise AppError(
                CodingAgentErrorCode.PROCESS_DIED.value,
                503,
                {"detail": "session is closed or stdin unavailable"},
            )
        async with self._stdin_lock:
            try:
                self.proc.stdin.write(encode_message(msg))
                await self.proc.stdin.drain()
                self.last_active_at = asyncio.get_event_loop().time()
            except (BrokenPipeError, ConnectionResetError) as e:
                logger.warning(f"CodingAgent stdin write failed: {e}")
                raise AppError(
                    CodingAgentErrorCode.PROCESS_DIED.value,
                    503,
                    {"detail": str(e)},
                )

    # ─────────────────────────────────────────────────────
    # 读取 stdout
    # ─────────────────────────────────────────────────────

    def acquire_stream_token(self) -> int:
        """生成新 stream consumer token, 替换掉旧的 → 旧 consumer 下一轮检测到会退出。

        调用方: session_service.chat() / resume_stream() 进入循环前先调用。
        """
        self._active_stream_token += 1
        return self._active_stream_token

    async def stream_events(self, *, token: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """
        单消费者 async iterator — yield 原始 stream-json 事件。

        token 协议 (防止多个 consumer 并发抢同一 queue):
            - 调用前先 acquire_stream_token() 拿到 my_token
            - 传入 stream_events(token=my_token)
            - 每轮循环对比 self._active_stream_token, 被替换则优雅 return
            - token=None 的调用走兼容路径 (旧测试/启动期), 不做 token 检查

        ⚠️ 边界: 如果 get() 已经取到事件才发现 token 被替换, 必须把事件
        put 回 queue 头 (或通过 sentinel 让新 consumer 知道), 否则这条事件
        被旧 consumer 吃掉 — 新 consumer 看不到也没进 fe_history。
        """
        while True:
            # [resume-v2] 每次 await 前先检查 token, 被替换则退让给新 consumer
            if token is not None and token != self._active_stream_token:
                return
            event = await self._event_queue.get()
            if event is None:
                # sentinel: 子进程已退出。把 sentinel 放回, 让后续 consumer 也能看到
                with suppress(asyncio.QueueFull):
                    self._event_queue.put_nowait(None)
                return
            if token is not None and token != self._active_stream_token:
                # 拿到事件后发现 token 已被替换 — 把事件 put 回 queue, 交给新 consumer。
                # 注意: 这里用 await put 而非 put_nowait。旧实现 QueueFull 时记 warning 就丢事件,
                # 会破坏 resume 协议 (已翻译的事件永远进不去 fe_history, 前端 resume 看不到
                # → tool_result 丢帧 / AI 回复断链)。宁可短暂阻塞等新 consumer 消费腾位:
                # 1) 这是 token 被替换后的"交接"动作, 旧 consumer 已准备 return, 多等几十 ms OK;
                # 2) 新 consumer 一进来就会 get() 消费, 空位很快腾出;
                # 3) 丢事件破坏 resume 正确性, 远比短暂阻塞严重。
                await self._event_queue.put(event)
                return
            yield event

    # ─────────────────────────────────────────────────────
    # 前端事件缓冲 (translate 层负责填充; resume 按 fe_seq 读取)
    # ─────────────────────────────────────────────────────

    def next_fe_seq(self) -> int:
        """分配一个新的前端事件 fe_seq (单调递增)。translate 层调用。"""
        self._fe_seq += 1
        return self._fe_seq

    def append_fe_event(self, event_dict: dict[str, Any]) -> None:
        """把翻译后的 event dict (含 _seq = fe_seq) 写进缓冲, 用于 resume 回放。"""
        self._fe_history.append(event_dict)
        if len(self._fe_history) > self._fe_history_max:
            self._fe_history = self._fe_history[-self._fe_history_max:]

    def replay_fe_events_since(self, last_fe_seq: int) -> list[dict[str, Any]]:
        """返回 last_fe_seq 之后的所有已翻译前端事件 (供 WS 重连回放)。"""
        return [e for e in self._fe_history if e.get("_seq", 0) > last_fe_seq]

    async def _reader_loop(self) -> None:
        """后台读 stdout，把每行解析后塞队列。

        每收到一行 stream-json 事件就刷新 last_active_at — 这是 idle gc 的
        keepalive。否则一个长时间运行的 Bash 工具（例如同步等 30 分钟的外部 API）
        会让 session 看起来 "idle 600s+" 被 session_pool gc 错误回收掉。
        """
        if not self.proc or not self.proc.stdout:
            return
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                event = decode_line(line)
                if event is None:
                    # [M5] 解析失败计数 — 累计 ≥ 10 触发 error 告警 (子进程输出格式可能损坏)
                    self._parse_failure_count += 1
                    if self._parse_failure_count == 10:
                        logger.error(
                            f"CodingAgent decode_line 已累计失败 {self._parse_failure_count} 次, "
                            f"子进程输出格式可能损坏 skill={self.skill_id} user={self.user_id}"
                        )
                    elif self._parse_failure_count > 10 and self._parse_failure_count % 50 == 0:
                        # 之后每 50 次再 warn 一次, 避免日志爆炸
                        logger.warning(
                            f"CodingAgent decode_line 累计失败 {self._parse_failure_count} 次"
                        )
                    logger.debug(f"CodingAgent skipped non-json: {line[:120]!r}")
                    continue
                # 关键：任何有效事件都算 "活跃"，包括 tool_use / tool_result
                self.last_active_at = asyncio.get_event_loop().time()
                # 跟踪未完成的 tool_use,避免长 Bash 期间被 idle gc 误杀
                self._track_tool_event(event)
                try:
                    self._event_queue.put_nowait(event)
                except asyncio.QueueFull:
                    # [M5] queue 满 — 计数 + 升级日志级别 (warning → error if persistent)
                    self._dropped_event_count += 1
                    if self._dropped_event_count <= 5:
                        logger.warning(
                            f"CodingAgent event queue full (size=1024), dropping oldest "
                            f"skill={self.skill_id} user={self.user_id} dropped={self._dropped_event_count}"
                        )
                    elif self._dropped_event_count == 50:
                        logger.error(
                            f"CodingAgent event queue 持续满载, 已丢弃 {self._dropped_event_count} 条事件; "
                            f"前端 WebSocket 可能未正常消费 skill={self.skill_id} user={self.user_id}"
                        )
                    with suppress(asyncio.QueueEmpty):
                        self._event_queue.get_nowait()
                    self._event_queue.put_nowait(event)
        except Exception as e:
            logger.exception(f"CodingAgent reader loop crashed: {e}")
        finally:
            with suppress(asyncio.QueueFull):
                self._event_queue.put_nowait(None)

    async def _stderr_drain(self) -> None:
        """后台消费 stderr，避免 pipe 阻塞。错误日志记到 logger。"""
        if not self.proc or not self.proc.stderr:
            return
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                txt = line.decode("utf-8", errors="replace").rstrip()
                if txt:
                    logger.warning(f"[aiclawcode stderr] {txt}")
        except Exception as e:
            logger.debug("aiclawcode stderr 读取循环退出: {}", e)

    async def _wait_for_event_type(
        self, type_: str, *, subtype: str | None = None
    ) -> dict[str, Any]:
        """从 stream 中阻塞等待第一个匹配的事件（仅启动期使用）。"""
        async for event in self.stream_events():
            if event.get("type") != type_:
                continue
            if subtype is not None and event.get("subtype") != subtype:
                continue
            return event
        raise AppError(
            CodingAgentErrorCode.PROCESS_DIED.value,
            500,
            {"detail": f"process exited before {type_}/{subtype} event"},
        )

    # ─────────────────────────────────────────────────────
    # 关闭
    # ─────────────────────────────────────────────────────

    @property
    def is_alive(self) -> bool:
        return (
            self.proc is not None
            and self.proc.returncode is None
            and not self._closed
        )

    @property
    def has_pending_tool(self) -> bool:
        """是否有未完成的工具调用 (idle gc 时跳过这种 session)。"""
        return self.pending_tool_calls > 0

    def _track_tool_event(self, event: dict) -> None:
        """从 stream-json 事件提取 tool_use / tool_result, 维护 pending_tool_calls 计数。

        协议（aiclawcode stream-json）:
        - assistant 消息含 content[].type == "tool_use" → 计数 +1
        - user 消息含 content[].type == "tool_result" → 计数 -1
        """
        try:
            etype = event.get("type")
            msg = event.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                return
            if etype == "assistant":
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        self.pending_tool_calls += 1
            elif etype == "user":
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        if self.pending_tool_calls > 0:
                            self.pending_tool_calls -= 1
        except Exception:
            # 不让计数 bug 拖垮 reader loop
            pass

    async def close(self, *, kill: bool = False) -> None:
        """幂等关闭。kill=True 直接 SIGKILL，否则先 EOF stdin 等优雅退出。

        [H9] 关闭流程升级:
          1. EOF stdin → 等 5s
          2. SIGTERM → 等 10s (容器内 Node 进程常忽略 SIGTERM, 给足时间)
          3. SIGKILL → 等 3s 收尸
        """
        if self._closed:
            return
        self._closed = True

        if not self.proc:
            return

        try:
            if not kill and self.proc.stdin and not self.proc.stdin.is_closing():
                self.proc.stdin.close()
                with suppress(Exception):
                    await asyncio.wait_for(self.proc.wait(), timeout=5)

            if self.proc.returncode is None:
                # 优雅关闭超时或者 kill 模式：SIGTERM 给 10s 余量, 容器内的 Node 进程
                # 常会忽略 SIGTERM (尤其是在 Bun runtime 下), 旧版 3s 容易直接进入
                # SIGKILL 路径产生僵尸 / 资源泄漏。
                with suppress(ProcessLookupError):
                    self.proc.terminate()
                with suppress(Exception):
                    await asyncio.wait_for(self.proc.wait(), timeout=10)
                if self.proc.returncode is None:
                    logger.warning(
                        f"CodingAgent SIGTERM ignored after 10s, escalating to SIGKILL: "
                        f"skill={self.skill_id} user={self.user_id} pid={self.proc.pid}"
                    )
                    with suppress(ProcessLookupError):
                        self.proc.kill()
                    with suppress(Exception):
                        await asyncio.wait_for(self.proc.wait(), timeout=3)
        finally:
            for task in (self._reader_task, self._stderr_task):
                if task and not task.done():
                    task.cancel()
                    with suppress(BaseException):
                        await task
            logger.info(
                f"CodingAgent session closed: skill={self.skill_id} user={self.user_id} "
                f"rc={self.proc.returncode if self.proc else None}"
            )
