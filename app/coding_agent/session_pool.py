"""
Session 池：按 (skill_id, user_id) 复用 SubprocessSession，LRU + 空闲回收。

容量上限：CODING_AGENT_MAX_SESSIONS（默认 16）
空闲超时：CODING_AGENT_IDLE_TIMEOUT_SECONDS（默认 600s）
gc_loop 由 FastAPI lifespan 启动，每 30 秒巡检一次。
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path

from loguru import logger

from app.common.exceptions import AppError
from app.coding_agent.schemas import CodingAgentErrorCode
from app.coding_agent.subprocess_session import SubprocessSession
from app.config import settings


SessionKey = tuple[str, str]  # (skill_id, user_id)


class SessionPool:
    """LRU 池。acquire/release 都是 async；非线程安全（单 event loop）。"""

    def __init__(self):
        self._sessions: OrderedDict[SessionKey, SubprocessSession] = OrderedDict()
        self._lock = asyncio.Lock()
        self._gc_task: asyncio.Task | None = None

    # ─────────────────────────────────────────────────────
    # 主要 API
    # ─────────────────────────────────────────────────────

    async def acquire(
        self,
        *,
        skill_id: str,
        user_id: str,
        work_dir: Path,
        env: dict[str, str] | None = None,
        provider: str | None = None,
        model: str | None = None,
        permission_mode: str = "acceptEdits",
        max_turns: int | None = 30,
        system_prompt_append: str | None = None,
        mcp_config_json: str | None = None,
        prompt_hash: str | None = None,
        config_dir: Path | None = None,
    ) -> SubprocessSession:
        """
        取或创建一个 session。同一 (skill_id, user_id) 复用。
        若达到容量上限，先 evict 最旧的空闲 session。
        """
        key = (skill_id, user_id)
        async with self._lock:
            # 命中且仍 alive：移到 LRU 末尾后返回
            existing = self._sessions.get(key)
            if existing is not None:
                if existing.is_alive:
                    existing_prompt_hash = getattr(existing, "prompt_hash", None)
                    existing_config_dir = getattr(existing, "config_dir", None)
                    target_config_dir = config_dir.resolve() if config_dir else None
                    if (
                        (prompt_hash and existing_prompt_hash and existing_prompt_hash != prompt_hash)
                        or (
                            target_config_dir is not None
                            and existing_config_dir is not None
                            and existing_config_dir.resolve() != target_config_dir
                        )
                    ):
                        logger.info(
                            "CodingAgent session runtime changed, recreating {} "
                            "(prompt_hash={}→{}, config_dir={}→{})",
                            key,
                            existing_prompt_hash,
                            prompt_hash,
                            existing_config_dir,
                            target_config_dir,
                        )
                        with suppress(Exception):
                            await existing.close(kill=True)
                        del self._sessions[key]
                        existing = None
                    else:
                        self._sessions.move_to_end(key)
                        return existing
                if existing is not None:
                    # 死的：清理
                    logger.warning(f"CodingAgent session dead, recreating: {key}")
                    with suppress(Exception):
                        await existing.close(kill=True)
                    del self._sessions[key]

            # 容量检查
            await self._evict_if_needed_locked()

            # 创建新 session
            session = SubprocessSession(
                skill_id=skill_id, user_id=user_id, work_dir=work_dir, env=env,
                provider=provider, model=model,
                permission_mode=permission_mode, max_turns=max_turns,
                system_prompt_append=system_prompt_append,
                mcp_config_json=mcp_config_json,
            )
            session.prompt_hash = prompt_hash
            session.config_dir = config_dir.resolve() if config_dir else None
            try:
                await session.start()
            except Exception:
                # 启动失败 → 不入池
                with suppress(Exception):
                    await session.close(kill=True)
                raise

            self._sessions[key] = session
            return session

    async def release_by_key(self, *, skill_id: str, user_id: str) -> None:
        """显式关闭并从池中移除。"""
        key = (skill_id, user_id)
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session:
            with suppress(Exception):
                await session.close()

    async def get(self, *, skill_id: str, user_id: str) -> SubprocessSession | None:
        """只读获取，不创建。"""
        return self._sessions.get((skill_id, user_id))

    async def close_all(self) -> None:
        """lifespan shutdown 时调用。"""
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            with suppress(Exception):
                await s.close()
        logger.info(f"CodingAgent pool closed all ({len(sessions)} sessions)")

    # ─────────────────────────────────────────────────────
    # 容量管理
    # ─────────────────────────────────────────────────────

    async def _evict_if_needed_locked(self) -> None:
        """假设已持有 self._lock。如果接近容量上限，evict 最旧的死/空闲 session。"""
        max_n = settings.CODING_AGENT_MAX_SESSIONS
        if len(self._sessions) < max_n:
            return

        # 优先 evict 死的
        for key, sess in list(self._sessions.items()):
            if not sess.is_alive:
                del self._sessions[key]
                with suppress(Exception):
                    await sess.close(kill=True)
                if len(self._sessions) < max_n:
                    return

        # 还不够：evict LRU 最旧的
        if len(self._sessions) >= max_n:
            oldest_key, oldest_sess = next(iter(self._sessions.items()))
            del self._sessions[oldest_key]
            logger.info(f"CodingAgent pool evicting LRU session: {oldest_key}")
            with suppress(Exception):
                await oldest_sess.close()

        if len(self._sessions) >= max_n:
            raise AppError(
                CodingAgentErrorCode.POOL_EXHAUSTED.value,
                503,
                {"max_sessions": max_n},
            )

    # ─────────────────────────────────────────────────────
    # GC 循环（lifespan 启动）
    # ─────────────────────────────────────────────────────

    async def gc_loop(self, *, interval_sec: int = 30) -> None:
        """周期回收：清理死会话 + 空闲超时会话。

        动态间隔策略：
        - 无会话时拉长到 120s（减少 CPU 浪费）
        - 有活跃会话时缩短到 10s（及时清理）
        - 默认 30s
        """
        logger.info(
            f"CodingAgent gc_loop started "
            f"(base_interval={interval_sec}s, idle_timeout={settings.CODING_AGENT_IDLE_TIMEOUT_SECONDS}s)"
        )
        try:
            while True:
                # 动态调整间隔
                session_count = len(self._sessions)
                if session_count == 0:
                    actual_interval = min(interval_sec * 4, 120)
                elif session_count >= settings.CODING_AGENT_MAX_SESSIONS // 2:
                    actual_interval = max(interval_sec // 3, 10)
                else:
                    actual_interval = interval_sec
                await asyncio.sleep(actual_interval)
                await self._gc_once()
        except asyncio.CancelledError:
            logger.info("CodingAgent gc_loop cancelled")
            raise

    async def _gc_once(self) -> None:
        now = asyncio.get_event_loop().time()
        idle_limit = settings.CODING_AGENT_IDLE_TIMEOUT_SECONDS

        async with self._lock:
            stale: list[SessionKey] = []
            for key, sess in self._sessions.items():
                if not sess.is_alive:
                    stale.append(key)
                    continue
                if (now - sess.last_active_at) > idle_limit:
                    # 关键: 有未完成的 tool_use (例如长时间运行的 Bash) 不能 gc,
                    # 否则会 SIGTERM 中断 → 用户看到 "AI 卡住"
                    if sess.has_pending_tool:
                        logger.debug(
                            f"CodingAgent skip gc (pending_tools={sess.pending_tool_calls}): {key}"
                        )
                        continue
                    stale.append(key)

            for key in stale:
                sess = self._sessions.pop(key, None)
                if sess:
                    logger.info(
                        f"CodingAgent gc reaping {key}: alive={sess.is_alive} "
                        f"idle={int(now - sess.last_active_at)}s "
                        f"pending_tools={sess.pending_tool_calls}"
                    )
                    asyncio.create_task(self._safe_close(sess))

    @staticmethod
    async def _safe_close(session: SubprocessSession) -> None:
        with suppress(Exception):
            await session.close()


# 全局单例
coding_agent_pool = SessionPool()
