"""基于 Redis 的分布式滑动窗口限流器。

使用 Redis Lua 脚本实现原子性的滑动窗口计数，
支持跨 worker 全局限流。回退：Redis 不可用时放行（fail-open）。
"""

from __future__ import annotations

import time
import uuid

from loguru import logger

from app.config import settings

# Lua 脚本：原子性滑动窗口计数
# KEYS[1] = 限流 key
# ARGV[1] = window_start (now - window_seconds)
# ARGV[2] = now (当前时间戳)
# ARGV[3] = max_requests (窗口内最大请求数)
# ARGV[4] = unique_member_id (本次请求唯一标识)
# ARGV[5] = window_seconds (窗口时长，用于设置 TTL)
#
# 返回值：
#   允许: {1, remaining}
#   拒绝: {0, oldest_score}  -- oldest_score 用于计算 retry_after
_LUA_SLIDING_WINDOW = """
local key = KEYS[1]
local window_start = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local member_id = ARGV[4]
local window_seconds = tonumber(ARGV[5])

-- 1. 清理过期成员
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

-- 2. 当前窗口内请求计数
local count = redis.call('ZCARD', key)

-- 3. 判断是否超限
if count < max_requests then
    -- 允许：添加本次请求，设置 TTL
    redis.call('ZADD', key, now, member_id)
    redis.call('EXPIRE', key, window_seconds + 1)
    return {1, max_requests - count - 1}
else
    -- 拒绝：返回最早请求的时间戳，用于计算 retry_after
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    if oldest and #oldest >= 2 then
        return {0, tonumber(oldest[2])}
    end
    return {0, now}
end
"""

# Lua 脚本：仅查询当前使用量（不消耗配额）
# KEYS[1] = 限流 key
# ARGV[1] = window_start
_LUA_GET_USAGE = """
local key = KEYS[1]
local window_start = tonumber(ARGV[1])

-- 清理过期成员后计数
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)
return redis.call('ZCARD', key)
"""


class DistributedRateLimiter:
    """分布式滑动窗口限流器。

    使用 Redis ZSET 实现滑动窗口：
    - key: sf:ratelimit:{scope}:{identity}
    - member: 唯一请求 ID (timestamp + random)
    - score: 时间戳
    - 每次请求：清理过期成员 -> 计数 -> 判断是否超限 -> 添加新成员
    - 全部在一个 Lua 脚本中原子执行
    """

    def __init__(self, scope: str, max_requests: int, window_seconds: int):
        """
        Args:
            scope: 限流维度名称，如 "chat_send", "api_call", "skill_execute"
            max_requests: 窗口内最大请求数
            window_seconds: 滑动窗口时长（秒）
        """
        self.scope = scope
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._key_prefix = f"sf:ratelimit:{scope}"
        # Lua 脚本 SHA 缓存（延迟加载）
        self._check_sha: str | None = None
        self._usage_sha: str | None = None

    def _make_key(self, identity: str) -> str:
        """构造 Redis key"""
        return f"{self._key_prefix}:{identity}"

    def _get_pool(self):
        """获取 Redis 连接池，复用 cache.py 的全局实例"""
        from app.common.cache import _pool
        return _pool

    async def check(self, identity: str) -> tuple[bool, dict]:
        """检查是否允许请求。

        Args:
            identity: 限流标识，如 user_id, org_unit_id, ip

        Returns:
            (allowed, info) 元组：
            - allowed: 是否放行
            - info: {"remaining": int, "retry_after": float, "limit": int}
        """
        pool = self._get_pool()
        if pool is None:
            # Redis 未初始化 → fail-open（放行）
            logger.debug("[rate_limiter] Redis 未初始化，fail-open scope={}", self.scope)
            return True, {"remaining": self.max_requests - 1, "retry_after": 0.0, "limit": self.max_requests}

        now = time.time()
        window_start = now - self.window_seconds
        member_id = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
        key = self._make_key(identity)

        try:
            result = await pool.eval(
                _LUA_SLIDING_WINDOW,
                1,  # numkeys
                key,
                str(window_start),
                str(now),
                str(self.max_requests),
                member_id,
                str(self.window_seconds),
            )

            allowed = int(result[0]) == 1
            if allowed:
                remaining = int(result[1])
                return True, {
                    "remaining": remaining,
                    "retry_after": 0.0,
                    "limit": self.max_requests,
                }
            else:
                # 计算 retry_after：最早请求过期时间 - 当前时间
                oldest_score = float(result[1])
                retry_after = max(0.0, (oldest_score + self.window_seconds) - now)
                return False, {
                    "remaining": 0,
                    "retry_after": round(retry_after, 2),
                    "limit": self.max_requests,
                }

        except Exception as exc:
            # Redis 异常 → fail-open（放行），记录警告
            logger.warning(
                "[rate_limiter] Redis 执行异常，fail-open scope={} identity={} err={}",
                self.scope, identity, exc,
            )
            return True, {"remaining": self.max_requests - 1, "retry_after": 0.0, "limit": self.max_requests}

    async def get_usage(self, identity: str) -> dict:
        """查询当前使用量（不消耗配额）。

        Returns:
            {"used": int, "limit": int, "remaining": int, "window": int}
        """
        pool = self._get_pool()
        if pool is None:
            return {"used": 0, "limit": self.max_requests, "remaining": self.max_requests, "window": self.window_seconds}

        now = time.time()
        window_start = now - self.window_seconds
        key = self._make_key(identity)

        try:
            used = await pool.eval(
                _LUA_GET_USAGE,
                1,
                key,
                str(window_start),
            )
            used = int(used)
            return {
                "used": used,
                "limit": self.max_requests,
                "remaining": max(0, self.max_requests - used),
                "window": self.window_seconds,
            }
        except Exception as exc:
            logger.warning(
                "[rate_limiter] Redis 查询使用量失败 scope={} identity={} err={}",
                self.scope, identity, exc,
            )
            return {"used": 0, "limit": self.max_requests, "remaining": self.max_requests, "window": self.window_seconds}

    async def reset(self, identity: str) -> None:
        """重置指定标识的限流计数。"""
        pool = self._get_pool()
        if pool is None:
            return

        key = self._make_key(identity)
        try:
            await pool.delete(key)
        except Exception as exc:
            logger.warning(
                "[rate_limiter] Redis 重置失败 scope={} identity={} err={}",
                self.scope, identity, exc,
            )


# ── 预配置的全局限流器实例 ──

# 聊天消息限流：每用户每秒 N 条（从配置读取）
chat_limiter = DistributedRateLimiter(
    scope="chat_send",
    max_requests=settings.CHAT_SEND_QPS_PER_USER,
    window_seconds=1,
)

# API 调用限流：每用户每分钟 120 次
api_limiter = DistributedRateLimiter(
    scope="api_call",
    max_requests=120,
    window_seconds=60,
)

# Skill 执行限流：每用户每分钟 30 次
execution_limiter = DistributedRateLimiter(
    scope="skill_execute",
    max_requests=30,
    window_seconds=60,
)

# Architect prepare-workspace 限流：每用户每分钟 5 次
# 防止: 前端 bug / 恶意脚本秒内调 N 次,累积孤儿 draft 目录 + DB 行
architect_prepare_limiter = DistributedRateLimiter(
    scope="architect_prepare",
    max_requests=5,
    window_seconds=60,
)
