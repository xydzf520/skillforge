"""缓存预热 + write-through 装饰器单元测试"""

import pytest
import pytest_asyncio

from app.common.cache_warmup import (
    _warmup_items,
    register_warmup,
    warmup_all,
)
from app.common.cache import write_through


# ── warmup_all 测试 ──


@pytest.mark.asyncio
async def test_warmup_all_success():
    """预热函数全部成功时，返回结果都是 ok"""
    # 备份原注册表
    original = list(_warmup_items)
    _warmup_items.clear()

    call_order = []

    async def fn_a():
        call_order.append("a")

    async def fn_b():
        call_order.append("b")

    register_warmup("test_a", fn_a)
    register_warmup("test_b", fn_b)

    results = await warmup_all()

    assert results == {"test_a": "ok", "test_b": "ok"}
    assert call_order == ["a", "b"]

    # 恢复
    _warmup_items.clear()
    _warmup_items.extend(original)


@pytest.mark.asyncio
async def test_warmup_all_partial_failure():
    """单个预热函数失败不影响其他项"""
    original = list(_warmup_items)
    _warmup_items.clear()

    async def fn_ok():
        pass

    async def fn_fail():
        raise RuntimeError("模拟预热失败")

    async def fn_ok2():
        pass

    register_warmup("ok_first", fn_ok)
    register_warmup("failing", fn_fail)
    register_warmup("ok_second", fn_ok2)

    results = await warmup_all()

    assert results["ok_first"] == "ok"
    assert results["failing"].startswith("error:")
    assert results["ok_second"] == "ok"

    _warmup_items.clear()
    _warmup_items.extend(original)


@pytest.mark.asyncio
async def test_warmup_all_empty():
    """无注册项时返回空字典"""
    original = list(_warmup_items)
    _warmup_items.clear()

    results = await warmup_all()
    assert results == {}

    _warmup_items.clear()
    _warmup_items.extend(original)


# ── register_warmup 测试 ──


def test_register_warmup():
    """注册后项目出现在注册表中"""
    original = list(_warmup_items)
    _warmup_items.clear()

    async def my_fn():
        pass

    register_warmup("my_item", my_fn)

    assert len(_warmup_items) == 1
    assert _warmup_items[0][0] == "my_item"
    assert _warmup_items[0][1] is my_fn

    _warmup_items.clear()
    _warmup_items.extend(original)


# ── write_through 装饰器测试 ──


@pytest.mark.asyncio
async def test_write_through_calls_function_and_caches(monkeypatch):
    """write_through 装饰器执行被装饰函数并写入缓存"""
    cached_data = {}

    async def mock_cache_set(key, value, ttl=None):
        cached_data[key] = {"value": value, "ttl": ttl}

    monkeypatch.setattr("app.common.cache.cache_set", mock_cache_set)

    @write_through(lambda source_id: f"test:{source_id}", ttl=120)
    async def update_something(source_id: str):
        return {"id": source_id, "updated": True}

    result = await update_something(source_id="ds-001")

    assert result == {"id": "ds-001", "updated": True}
    assert "test:ds-001" in cached_data
    assert cached_data["test:ds-001"]["value"] == result
    assert cached_data["test:ds-001"]["ttl"] == 120


@pytest.mark.asyncio
async def test_write_through_cache_failure_does_not_block(monkeypatch):
    """缓存写入失败时，函数结果仍然正常返回"""
    async def mock_cache_set_fail(key, value, ttl=None):
        raise ConnectionError("Redis 连接断开")

    monkeypatch.setattr("app.common.cache.cache_set", mock_cache_set_fail)

    @write_through(lambda x: f"test:{x}", ttl=60)
    async def do_work(x: str):
        return {"done": True}

    # 不应抛异常
    result = await do_work(x="abc")
    assert result == {"done": True}


@pytest.mark.asyncio
async def test_write_through_metadata():
    """装饰器附加元数据到函数上"""
    key_fn = lambda x: f"k:{x}"

    @write_through(key_fn, ttl=300)
    async def some_func(x):
        return x

    assert some_func._write_through_key_fn is key_fn
    assert some_func._write_through_ttl == 300
