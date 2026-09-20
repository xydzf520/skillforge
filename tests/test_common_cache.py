"""app/common/cache.py 单元测试 — 重点验证 cached() 装饰器的 scope 隔离。

[C2] 防御跨用户/部门缓存污染：
  - cached() 必须按 scope_by 声明的字段拼接 cache_key
  - 不同 user/department 在 scope_by 配置正确时, 必须命中不同的缓存条目
  - 缺少 scope_by 但 kwargs 含 current_user 时, 应发出 WARNING (不阻断)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.common import cache as cache_mod


def test_adaptive_cache_ttl_boosts_hot_keys(monkeypatch):
    """同一 key 访问变热后 TTL 自动放大，但不超过全局上限。"""
    monkeypatch.setattr(cache_mod.settings, "CACHE_ADAPTIVE_ENABLED", True, raising=False)
    monkeypatch.setattr(cache_mod.settings, "CACHE_ADAPTIVE_HOT_THRESHOLD", 3, raising=False)
    monkeypatch.setattr(cache_mod.settings, "CACHE_ADAPTIVE_HOT_MULTIPLIER", 3, raising=False)
    monkeypatch.setattr(cache_mod.settings, "CACHE_ADAPTIVE_MAX_TTL", 100, raising=False)

    assert cache_mod.adaptive_cache_ttl(10, 1) == 10
    assert cache_mod.adaptive_cache_ttl(10, 2) == 10
    assert cache_mod.adaptive_cache_ttl(10, 3) == 30
    assert cache_mod.adaptive_cache_ttl(60, 10) == 100


def test_cache_value_size_counts_serialized_utf8_bytes():
    assert cache_mod.cache_value_size({"a": "中文"}) == len('{"a": "中文"}'.encode("utf-8"))


# ═══════════════════════════════════════════════════════
# fixture — 用 in-memory dict 模拟 Redis
# ═══════════════════════════════════════════════════════

@pytest.fixture
def fake_cache(monkeypatch):
    """替换 cache_get/cache_set 为内存字典, 隔离测试。"""
    store: dict[str, object] = {}

    async def fake_get(key: str):
        return store.get(f"sf:{key}")

    async def fake_set(key: str, value, ttl=None):
        store[f"sf:{key}"] = value

    monkeypatch.setattr(cache_mod, "cache_get", fake_get)
    monkeypatch.setattr(cache_mod, "cache_set", fake_set)
    return store


# ═══════════════════════════════════════════════════════
# 基础: 装饰器走通缓存读/写路径
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cached_basic_hit_and_miss(fake_cache):
    """无 scope_by 时按 kwargs hash 缓存, 第二次命中跳过 func 调用。"""
    call_count = 0

    @cache_mod.cached("test:basic", ttl=60)
    async def get_data(x: int):
        nonlocal call_count
        call_count += 1
        return {"x": x, "value": x * 2}

    r1 = await get_data(x=5)
    r2 = await get_data(x=5)
    assert r1 == r2 == {"x": 5, "value": 10}
    assert call_count == 1, "第二次应命中缓存, func 不应被调用"

    # 不同参数应未命中
    r3 = await get_data(x=6)
    assert r3 == {"x": 6, "value": 12}
    assert call_count == 2


@pytest.mark.asyncio
async def test_cached_skips_oversized_values(fake_cache):
    """超大响应不写入缓存，避免 Redis 命中后仍被 JSON 反序列化拖慢。"""
    call_count = 0

    @cache_mod.cached("test:oversized", ttl=60, max_bytes=20)
    async def get_data():
        nonlocal call_count
        call_count += 1
        return {"payload": "x" * 100}

    assert await get_data() == {"payload": "x" * 100}
    assert await get_data() == {"payload": "x" * 100}
    assert call_count == 2
    assert not any(key.startswith("sf:test:oversized:") for key in fake_cache)


@pytest.mark.asyncio
async def test_cached_skip_cache_if_bypasses_read_and_write(fake_cache):
    """debug/导出请求可显式绕过长缓存，避免把排障大对象塞进 6 小时缓存。"""
    call_count = 0

    @cache_mod.cached("test:conditional", ttl=60, skip_cache_if=lambda **kw: kw.get("debug") is True)
    async def get_data(debug: bool = False):
        nonlocal call_count
        call_count += 1
        return {"debug": debug, "call": call_count}

    assert await get_data(debug=False) == {"debug": False, "call": 1}
    assert await get_data(debug=False) == {"debug": False, "call": 1}
    assert await get_data(debug=True) == {"debug": True, "call": 2}
    assert await get_data(debug=True) == {"debug": True, "call": 3}


@pytest.mark.asyncio
async def test_cached_hot_hit_extends_ttl(fake_cache, monkeypatch):
    """同一缓存 key 访问达到热点阈值后，命中时会主动续期。"""
    monkeypatch.setattr(cache_mod.settings, "CACHE_ADAPTIVE_ENABLED", True, raising=False)
    monkeypatch.setattr(cache_mod.settings, "CACHE_ADAPTIVE_HOT_THRESHOLD", 3, raising=False)
    monkeypatch.setattr(cache_mod.settings, "CACHE_ADAPTIVE_HOT_MULTIPLIER", 3, raising=False)
    monkeypatch.setattr(cache_mod.settings, "CACHE_ADAPTIVE_MAX_TTL", 1000, raising=False)

    counts: dict[str, int] = {}
    expires: list[tuple[str, int]] = []

    async def fake_record_access(key: str, *, window=None):
        counts[key] = counts.get(key, 0) + 1
        return counts[key]

    async def fake_expire(key: str, ttl: int | None):
        expires.append((key, int(ttl or 0)))

    monkeypatch.setattr(cache_mod, "cache_record_access", fake_record_access)
    monkeypatch.setattr(cache_mod, "cache_expire", fake_expire)

    call_count = 0

    @cache_mod.cached("test:adaptive", ttl=10)
    async def get_data(x: int):
        nonlocal call_count
        call_count += 1
        return {"x": x}

    assert await get_data(x=1) == {"x": 1}
    assert await get_data(x=1) == {"x": 1}
    assert await get_data(x=1) == {"x": 1}

    assert call_count == 1
    assert any(key.startswith("test:adaptive:") and ttl == 30 for key, ttl in expires)


# ═══════════════════════════════════════════════════════
# [C2 关键] scope_by 隔离 — 不同部门必须独立缓存
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cached_scope_by_department(fake_cache):
    """[C2] scope_by=("current_user.department",) — A 部门写入后 B 部门必须不命中。"""
    call_count = 0

    @cache_mod.cached("test:scoped", ttl=60, scope_by=("current_user.department",))
    async def list_skills(current_user=None):
        nonlocal call_count
        call_count += 1
        return [f"skill_for_{current_user.department}"]

    user_a = SimpleNamespace(id="u1", department="EC", role="ai_engineer")
    user_b = SimpleNamespace(id="u2", department="SEM", role="ai_engineer")

    r_a = await list_skills(current_user=user_a)
    assert r_a == ["skill_for_EC"]
    assert call_count == 1

    # B 部门第一次访问 — 不应命中 A 的缓存
    r_b = await list_skills(current_user=user_b)
    assert r_b == ["skill_for_SEM"]
    assert call_count == 2, "B 部门应触发独立的 func 调用 (无跨部门污染)"

    # A 部门第二次访问 — 命中
    r_a2 = await list_skills(current_user=user_a)
    assert r_a2 == ["skill_for_EC"]
    assert call_count == 2

    # 验证 cache 里确实有两条 — key 后缀含 department
    keys = [k for k in fake_cache.keys() if k.startswith("sf:test:scoped:")]
    assert len(keys) == 2
    assert any("department=EC" in k for k in keys)
    assert any("department=SEM" in k for k in keys)


@pytest.mark.asyncio
async def test_cached_scope_by_user_id(fake_cache):
    """scope_by=("current_user.id",) — 不同 user 各自独立缓存。"""
    call_count = 0

    @cache_mod.cached("test:user", ttl=60, scope_by=("current_user.id",))
    async def get_my_inbox(current_user=None):
        nonlocal call_count
        call_count += 1
        return f"inbox_of_{current_user.id}"

    u1 = SimpleNamespace(id="alice", department="EC")
    u2 = SimpleNamespace(id="bob", department="EC")

    assert await get_my_inbox(current_user=u1) == "inbox_of_alice"
    assert await get_my_inbox(current_user=u2) == "inbox_of_bob"
    assert call_count == 2

    # 同 user 第二次命中
    assert await get_my_inbox(current_user=u1) == "inbox_of_alice"
    assert call_count == 2


# ═══════════════════════════════════════════════════════
# 防御性 WARNING — current_user 在但 scope_by 没声明
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cached_warning_when_current_user_unscoped(fake_cache, caplog):
    """current_user 在 kwargs 但 scope_by 没含 user/department → 发 WARNING。"""
    @cache_mod.cached("test:unscoped", ttl=60)  # 故意不声明 scope_by
    async def dangerous(current_user=None):
        return f"data_for_{current_user.id}"

    user = SimpleNamespace(id="u1", department="EC")

    # loguru 默认不写 caplog, 我们 patch 它
    with patch.object(cache_mod, "logger") as mock_logger:
        await dangerous(current_user=user)
        # 应触发警告
        warning_calls = [c for c in mock_logger.warning.call_args_list if "scope_by" in str(c)]
        assert len(warning_calls) >= 1, "应触发跨用户污染警告"


@pytest.mark.asyncio
async def test_cached_no_warning_when_scoped_correctly(fake_cache):
    """正确声明 scope_by=current_user.department 时, 不应警告。"""
    @cache_mod.cached("test:safe", ttl=60, scope_by=("current_user.department",))
    async def safe(current_user=None):
        return f"data_for_{current_user.department}"

    user = SimpleNamespace(id="u1", department="EC")

    with patch.object(cache_mod, "logger") as mock_logger:
        await safe(current_user=user)
        warning_calls = [c for c in mock_logger.warning.call_args_list if "scope_by" in str(c)]
        assert len(warning_calls) == 0, "声明了 scope 不该再警告"


# ═══════════════════════════════════════════════════════
# key_excludes — db/session 不参与 hash
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cached_key_excludes_db_param(fake_cache):
    """db/session 等不可序列化参数应从 hash 中排除, 否则不同 db connection 都 miss。"""
    call_count = 0

    @cache_mod.cached("test:exclude", ttl=60)
    async def query(x: int, db=None):
        nonlocal call_count
        call_count += 1
        return x

    db1 = object()
    db2 = object()

    await query(x=1, db=db1)
    await query(x=1, db=db2)
    assert call_count == 1, "db 实例不同但 x 相同, 应命中同一缓存"


# ═══════════════════════════════════════════════════════
# scope_by 字段为 None 时不会崩
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cached_scope_field_missing(fake_cache):
    """scope_by 字段在 kwargs 中缺失时, 应优雅降级到 None, 不抛异常。"""
    @cache_mod.cached("test:missing", ttl=60, scope_by=("current_user.department",))
    async def no_user():
        return "anon"

    # 调用时不传 current_user
    result = await no_user()
    assert result == "anon"
