"""缓存预热策略。

启动时和关键数据变更后主动预热缓存，避免冷启动风暴。
通过 register_warmup() 注册预热函数，startup 时调用 warmup_all() 执行。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Coroutine

from loguru import logger

# 预热项注册表：(名称, 异步预热函数)
_warmup_items: list[tuple[str, Callable[[], Coroutine[Any, Any, None]]]] = []


def register_warmup(name: str, fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """注册一个预热函数。fn 是 async callable，负责填充缓存。"""
    _warmup_items.append((name, fn))


async def warmup_all() -> dict[str, str]:
    """执行所有已注册的预热函数。

    返回 {name: "ok"} 或 {name: "error: ..."} 的结果摘要。
    单个预热失败不影响其他项。
    """
    results: dict[str, str] = {}
    total_start = time.monotonic()

    for name, fn in _warmup_items:
        start = time.monotonic()
        try:
            await fn()
            elapsed = time.monotonic() - start
            results[name] = "ok"
            logger.info("[cache_warmup] {} 完成 ({:.1f}ms)", name, elapsed * 1000)
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - start
            results[name] = f"error: {exc}"
            logger.warning("[cache_warmup] {} 失败 ({:.1f}ms): {}", name, elapsed * 1000, exc)

    total_elapsed = time.monotonic() - total_start
    ok_count = sum(1 for v in results.values() if v == "ok")
    logger.info(
        "[cache_warmup] 预热完成: {}/{} 成功，总耗时 {:.1f}ms",
        ok_count, len(results), total_elapsed * 1000,
    )
    return results


# ── 内置预热函数 ──


async def warmup_skill_list() -> None:
    """Skill 列表缓存按用户权限分片，启动时没有安全的用户 scope 可预热。"""
    return None


async def warmup_datasource_list() -> None:
    """数据源列表缓存按用户权限分片，启动时没有安全的用户 scope 可预热。"""
    return None


async def warmup_dashboard_overview() -> None:
    """预热看板概览缓存"""
    from app.dashboard.router import OVERVIEW_CACHE
    from app.dashboard.service import get_overview
    from app.database import async_session_factory

    async with async_session_factory() as db:
        result = await get_overview(db)
        await OVERVIEW_CACHE.set(30, "all", value=result)


# ── 注册内置预热项 ──
# Skill / datasource 列表已经按用户权限分片，启动阶段无用户 scope，不能写全局缓存。
register_warmup("dashboard_overview", warmup_dashboard_overview)
