"""分布式滑动窗口限流器测试。

使用 fakeredis 模拟 Redis，无需真实 Redis 实例。
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch, AsyncMock, PropertyMock

import pytest
import pytest_asyncio

# ── fakeredis 检测 ──
# 优先用 fakeredis（无外部依赖的 Redis 模拟），没有则用 mock
try:
    import fakeredis.aioredis as fakeredis_aio
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False


class FakeRedisPool:
    """轻量级 Redis mock，仅实现限流器用到的命令子集。

    当 fakeredis 不可用时作为纯 Python 回退方案。
    内部用 dict 模拟 ZSET 操作，足够覆盖限流器的测试需求。
    """

    def __init__(self):
        self._data: dict[str, dict[str, float]] = {}  # key -> {member: score}
        self._ttls: dict[str, float] = {}

    async def eval(self, script: str, numkeys: int, *args):
        """执行 Lua 脚本的 Python 等价实现"""
        key = args[0]

        if "ZCARD" in script and "ZADD" in script:
            # 这是 check 脚本
            window_start = float(args[1])
            now = float(args[2])
            max_requests = int(args[3])
            member_id = args[4]
            window_seconds = int(args[5])

            # 初始化 key
            if key not in self._data:
                self._data[key] = {}

            # 1. 清理过期成员
            zset = self._data[key]
            expired = [m for m, s in zset.items() if s <= window_start]
            for m in expired:
                del zset[m]

            # 2. 计数
            count = len(zset)

            # 3. 判断
            if count < max_requests:
                zset[member_id] = now
                self._ttls[key] = now + window_seconds + 1
                return [1, max_requests - count - 1]
            else:
                # 找最早的
                if zset:
                    oldest_score = min(zset.values())
                    return [0, oldest_score]
                return [0, now]

        elif "ZCARD" in script and "ZADD" not in script:
            # 这是 get_usage 脚本
            window_start = float(args[1])

            if key not in self._data:
                return 0

            zset = self._data[key]
            expired = [m for m, s in zset.items() if s <= window_start]
            for m in expired:
                del zset[m]

            return len(zset)

        raise ValueError(f"未识别的 Lua 脚本")

    async def delete(self, key: str) -> int:
        """删除 key"""
        if key in self._data:
            del self._data[key]
            self._ttls.pop(key, None)
            return 1
        return 0

    async def ping(self):
        return True


@pytest.fixture
def fake_pool():
    """提供一个 fake Redis 连接池"""
    if HAS_FAKEREDIS:
        return fakeredis_aio.FakeRedis(decode_responses=True)
    return FakeRedisPool()


@pytest.fixture
def patched_pool(fake_pool):
    """将 cache._pool 替换为 fake pool"""
    with patch("app.common.cache._pool", fake_pool):
        yield fake_pool


# ── 基本限流测试 ──


@pytest.mark.asyncio
async def test_basic_allow_then_deny(patched_pool):
    """验证：在窗口内，前 N 次请求允许，第 N+1 次拒绝"""
    from app.common.rate_limiter import DistributedRateLimiter

    limiter = DistributedRateLimiter(scope="test_basic", max_requests=3, window_seconds=10)

    # 前 3 次应该全部允许
    for i in range(3):
        allowed, info = await limiter.check("user_A")
        assert allowed, f"第 {i+1} 次请求应该允许"
        assert info["limit"] == 3
        assert info["remaining"] == 3 - i - 1

    # 第 4 次应该拒绝
    allowed, info = await limiter.check("user_A")
    assert not allowed, "超出限额后应该拒绝"
    assert info["remaining"] == 0
    assert info["retry_after"] > 0


@pytest.mark.asyncio
async def test_different_identities_isolated(patched_pool):
    """验证：不同用户的限额互相独立"""
    from app.common.rate_limiter import DistributedRateLimiter

    limiter = DistributedRateLimiter(scope="test_iso", max_requests=2, window_seconds=10)

    # user_A 消耗 2 次配额
    await limiter.check("user_A")
    await limiter.check("user_A")
    allowed_a, _ = await limiter.check("user_A")
    assert not allowed_a, "user_A 应该被限流"

    # user_B 仍然有配额
    allowed_b, info = await limiter.check("user_B")
    assert allowed_b, "user_B 应该不受 user_A 影响"
    assert info["remaining"] == 1


# ── 窗口滑动测试 ──


@pytest.mark.asyncio
async def test_window_sliding(patched_pool):
    """验证：窗口过期后，配额恢复"""
    from app.common.rate_limiter import DistributedRateLimiter

    # 使用极短的窗口方便测试
    limiter = DistributedRateLimiter(scope="test_slide", max_requests=2, window_seconds=1)

    # 消耗全部配额
    await limiter.check("user_X")
    await limiter.check("user_X")
    allowed, _ = await limiter.check("user_X")
    assert not allowed

    # 等待窗口滑过
    await asyncio.sleep(1.1)

    # 配额应恢复
    allowed, info = await limiter.check("user_X")
    assert allowed, "窗口滑过后应恢复配额"
    assert info["remaining"] >= 0


# ── Redis 不可用时 fail-open 测试 ──


@pytest.mark.asyncio
async def test_fail_open_when_redis_unavailable():
    """验证：Redis 不可用时，限流器放行（fail-open）"""
    from app.common.rate_limiter import DistributedRateLimiter

    limiter = DistributedRateLimiter(scope="test_failopen", max_requests=5, window_seconds=60)

    # _pool 为 None（Redis 未初始化）
    with patch("app.common.cache._pool", None):
        allowed, info = await limiter.check("user_Z")
        assert allowed, "Redis 不可用时应放行"
        assert info["remaining"] == 4
        assert info["limit"] == 5


@pytest.mark.asyncio
async def test_fail_open_on_redis_error(patched_pool):
    """验证：Redis 执行异常时，限流器放行"""
    from app.common.rate_limiter import DistributedRateLimiter

    limiter = DistributedRateLimiter(scope="test_err", max_requests=5, window_seconds=60)

    # 让 eval 抛异常
    patched_pool.eval = AsyncMock(side_effect=ConnectionError("Redis connection lost"))

    allowed, info = await limiter.check("user_E")
    assert allowed, "Redis 异常时应放行"


# ── get_usage 测试 ──


@pytest.mark.asyncio
async def test_get_usage(patched_pool):
    """验证：get_usage 返回正确的使用量，不消耗配额"""
    from app.common.rate_limiter import DistributedRateLimiter

    limiter = DistributedRateLimiter(scope="test_usage", max_requests=10, window_seconds=60)

    # 初始：used=0
    usage = await limiter.get_usage("user_U")
    assert usage["used"] == 0
    assert usage["remaining"] == 10
    assert usage["limit"] == 10
    assert usage["window"] == 60

    # 消耗 3 次
    await limiter.check("user_U")
    await limiter.check("user_U")
    await limiter.check("user_U")

    # 查询用量
    usage = await limiter.get_usage("user_U")
    assert usage["used"] == 3
    assert usage["remaining"] == 7

    # 再次 get_usage 不会改变计数
    usage2 = await limiter.get_usage("user_U")
    assert usage2["used"] == 3


@pytest.mark.asyncio
async def test_get_usage_redis_unavailable():
    """验证：Redis 不可用时 get_usage 返回安全默认值"""
    from app.common.rate_limiter import DistributedRateLimiter

    limiter = DistributedRateLimiter(scope="test_usage_fail", max_requests=10, window_seconds=60)

    with patch("app.common.cache._pool", None):
        usage = await limiter.get_usage("user_U")
        assert usage["used"] == 0
        assert usage["remaining"] == 10


# ── reset 测试 ──


@pytest.mark.asyncio
async def test_reset(patched_pool):
    """验证：reset 清除限流计数"""
    from app.common.rate_limiter import DistributedRateLimiter

    limiter = DistributedRateLimiter(scope="test_reset", max_requests=2, window_seconds=60)

    # 消耗全部配额
    await limiter.check("user_R")
    await limiter.check("user_R")
    allowed, _ = await limiter.check("user_R")
    assert not allowed

    # 重置
    await limiter.reset("user_R")

    # 配额恢复
    allowed, info = await limiter.check("user_R")
    assert allowed, "reset 后应恢复配额"
    assert info["remaining"] == 1


@pytest.mark.asyncio
async def test_reset_redis_unavailable():
    """验证：Redis 不可用时 reset 静默忽略"""
    from app.common.rate_limiter import DistributedRateLimiter

    limiter = DistributedRateLimiter(scope="test_reset_fail", max_requests=5, window_seconds=60)

    with patch("app.common.cache._pool", None):
        # 不应抛异常
        await limiter.reset("user_R")


# ── ChatSendRateLimiter 兼容性测试 ──


@pytest.mark.asyncio
async def test_chat_send_rate_limiter_compat(patched_pool):
    """验证：改造后的 ChatSendRateLimiter 接口不变"""
    from app.aiclaw.rate_limit import ChatSendRateLimiter

    limiter = ChatSendRateLimiter()
    # try_consume 返回 bool
    result = await limiter.try_consume("test_user")
    assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_chat_send_rate_limiter_fallback():
    """验证：Redis 完全不可用时，ChatSendRateLimiter 回退到本地限流"""
    from app.aiclaw.rate_limit import ChatSendRateLimiter

    limiter = ChatSendRateLimiter()

    # 模拟 chat_limiter.check 抛异常（分布式限流器不可用）
    with patch("app.aiclaw.rate_limit.chat_limiter") as mock_limiter:
        mock_limiter.check = AsyncMock(side_effect=RuntimeError("Redis completely down"))

        # 应回退到本地 Token Bucket，仍然能正常工作
        result = await limiter.try_consume("test_user")
        assert isinstance(result, bool)
        assert result is True  # 首次请求应放行


# ── 全局实例测试 ──


def test_global_instances_configured():
    """验证：全局限流器实例正确配置"""
    from app.common.rate_limiter import chat_limiter, api_limiter, execution_limiter
    from app.config import settings

    assert chat_limiter.scope == "chat_send"
    assert chat_limiter.max_requests == settings.CHAT_SEND_QPS_PER_USER
    assert chat_limiter.window_seconds == 1

    assert api_limiter.scope == "api_call"
    assert api_limiter.max_requests == 120
    assert api_limiter.window_seconds == 60

    assert execution_limiter.scope == "skill_execute"
    assert execution_limiter.max_requests == 30
    assert execution_limiter.window_seconds == 60


# ── retry_after 精度测试 ──


@pytest.mark.asyncio
async def test_retry_after_is_reasonable(patched_pool):
    """验证：被拒绝时 retry_after 值合理（0 < retry_after <= window_seconds）"""
    from app.common.rate_limiter import DistributedRateLimiter

    limiter = DistributedRateLimiter(scope="test_retry", max_requests=1, window_seconds=10)

    # 消耗唯一配额
    await limiter.check("user_T")

    # 被拒绝
    allowed, info = await limiter.check("user_T")
    assert not allowed
    assert 0 < info["retry_after"] <= 10, f"retry_after={info['retry_after']} 应在 (0, 10] 范围内"
