"""通用抖动退避重试工具。

参考 Hermes Agent retry_utils.py 设计，用于 LLM 调用和 Git 操作的瞬态失败重试。
"""

from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable, TypeVar

from loguru import logger

T = TypeVar("T")


class RetryableError(Exception):
    """标记为可重试的异常基类。"""
    pass


async def retry_with_backoff(
    func: Callable[..., Awaitable[T]],
    *args,
    max_retries: int = 2,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple[type[BaseException], ...] = (RetryableError,),
    description: str = "operation",
    **kwargs,
) -> T:
    """带抖动退避的异步重试。

    Args:
        func: 要重试的异步函数
        max_retries: 最大重试次数（不含首次调用）
        base_delay: 基础延迟秒数
        max_delay: 最大延迟秒数
        retryable_exceptions: 可重试的异常类型
        description: 操作描述（用于日志）
    """
    delay = base_delay
    last_exc: BaseException | None = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt == max_retries:
                logger.warning(
                    "{} 重试 {} 次后仍失败: {}",
                    description, max_retries, exc,
                )
                raise
            # 去相关抖动：jitter ∈ [0.5*delay, 1.5*delay]
            jitter = delay * (0.5 + random.random())
            actual_delay = min(jitter, max_delay)
            logger.info(
                "{} 第 {} 次失败 ({}), {:.1f}s 后重试",
                description, attempt + 1, type(exc).__name__, actual_delay,
            )
            await asyncio.sleep(actual_delay)
            delay *= 2

    # 不应该到这里，但以防万一
    raise last_exc  # type: ignore[misc]
