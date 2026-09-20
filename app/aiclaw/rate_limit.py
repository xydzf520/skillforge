"""chat.send 速率限制器（分布式 + 本地回退）。

优先使用 Redis 分布式滑动窗口限流（DistributedRateLimiter），
Redis 不可用时自动回退到单进程 Token Bucket，保证服务不中断。
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger

from app.common.rate_limiter import chat_limiter
from app.config import settings


class _UserBucket:
    """本地 Token Bucket 的单用户桶"""
    __slots__ = ("tokens", "last_refill")

    def __init__(self, tokens: float, last_refill: float):
        self.tokens = tokens
        self.last_refill = last_refill


class _LocalTokenBucket:
    """单进程内 Token Bucket 限流器（Redis 不可用时的回退方案）"""

    def __init__(self):
        self._buckets: dict[str, _UserBucket] = {}
        self._lock = asyncio.Lock()

    async def try_consume(self, user_id: str) -> bool:
        """尝试为用户扣 1 个 token，返回是否成功"""
        rate = max(1, settings.CHAT_SEND_QPS_PER_USER)
        capacity = float(rate)
        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets.get(user_id)
            if bucket is None:
                bucket = _UserBucket(tokens=capacity, last_refill=now)
                self._buckets[user_id] = bucket
            elapsed = now - bucket.last_refill
            bucket.tokens = min(capacity, bucket.tokens + elapsed * rate)
            bucket.last_refill = now
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return True
            return False


class ChatSendRateLimiter:
    """chat.send 限流器，兼容原有接口。

    内部优先使用 DistributedRateLimiter（Redis），
    Redis 不可用时自动回退到本地 Token Bucket。
    """

    def __init__(self):
        self._local_fallback = _LocalTokenBucket()

    async def try_consume(self, user_id: str) -> bool:
        """尝试为用户扣 1 次配额，返回是否允许。

        方法签名与原版完全一致，保持向后兼容。
        """
        try:
            # 优先使用分布式限流器
            allowed, info = await chat_limiter.check(user_id)
            return allowed
        except Exception as exc:
            # 分布式限流器完全失败 → 回退本地 Token Bucket
            logger.warning("[rate_limit] 分布式限流异常，回退本地限流 user={} err={}", user_id, exc)
            return await self._local_fallback.try_consume(user_id)


# 全局单例（保持向后兼容的导入路径）
chat_send_rate_limiter = ChatSendRateLimiter()
