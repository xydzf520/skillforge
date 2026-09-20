"""LangGraph checkpointer 单例（AsyncPostgresSaver + InMemorySaver fallback）。

策略：
- 首次访问 get_checkpointer() 时懒加载 psycopg AsyncConnectionPool。
- DB 不可用 / setup 失败 → fallback InMemorySaver，保证 graph 仍能运行（测试 + 离线开发）。
- 进程级单例，所有 graph 共享同一个 checkpointer。
"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from app.config import settings


_pool = None
_saver = None
_init_lock: Optional[asyncio.Lock] = None


def _to_psycopg_dsn(url: str) -> str:
    """SkillForge DATABASE_URL（asyncpg 格式）→ psycopg 兼容 DSN。"""
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    return url


async def get_checkpointer():
    """返回进程级 checkpointer 单例。"""
    global _pool, _saver, _init_lock

    if _saver is not None:
        return _saver

    if _init_lock is None:
        _init_lock = asyncio.Lock()

    async with _init_lock:
        if _saver is not None:
            return _saver

        try:
            from psycopg_pool import AsyncConnectionPool
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            dsn = _to_psycopg_dsn(settings.DATABASE_URL)
            _pool = AsyncConnectionPool(
                dsn,
                min_size=1,
                max_size=4,
                kwargs={"autocommit": True, "prepare_threshold": 0},
                open=False,
            )
            await _pool.open()
            _saver = AsyncPostgresSaver(_pool)
            await _saver.setup()
            logger.info("[agent_core] AsyncPostgresSaver 初始化成功 dsn={}", dsn.split("@")[-1])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[agent_core] AsyncPostgresSaver 初始化失败，fallback InMemorySaver: {}",
                exc,
            )
            from langgraph.checkpoint.memory import InMemorySaver
            _saver = InMemorySaver()
            if _pool is not None:
                try:
                    await _pool.close()
                except Exception:  # noqa: BLE001
                    pass
                _pool = None

    return _saver


async def close_checkpointer() -> None:
    """关闭 pool（FastAPI lifespan shutdown 调用）。"""
    global _pool, _saver
    if _pool is not None:
        try:
            await _pool.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[agent_core] 关闭 checkpointer pool 失败: {}", exc)
    _pool = None
    _saver = None
