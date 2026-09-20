"""
异步Redis缓存工具模块。

提供装饰器和函数式API，用于缓存API响应。
所有缓存key以 "sf:" 前缀命名空间隔离。
"""

import asyncio
import json
import hashlib
import inspect
from functools import wraps
from typing import Any, Callable, get_type_hints

import redis.asyncio as aioredis
from loguru import logger

from app.config import settings

# 全局Redis连接池
_pool: aioredis.Redis | None = None


async def init_cache() -> None:
    """初始化Redis连接池"""
    global _pool
    _pool = aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        max_connections=10,
    )
    # 测试连接
    await _pool.ping()
    logger.info("Redis缓存连接成功: {}", settings.REDIS_URL)


async def close_cache() -> None:
    """关闭Redis连接池"""
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None
        logger.info("Redis缓存连接已关闭")


def _get_pool() -> aioredis.Redis:
    """获取Redis连接池，未初始化时抛异常"""
    if _pool is None:
        raise RuntimeError("Redis未初始化，请先调用 init_cache()")
    return _pool


# ── 函数式API ──


async def cache_get(key: str) -> Any | None:
    """获取缓存值，返回反序列化的Python对象或None"""
    pool = _get_pool()
    raw = await pool.get(f"sf:{key}")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


async def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    """设置缓存，ttl为秒数，None则使用默认值"""
    pool = _get_pool()
    ttl = ttl or settings.CACHE_TTL_DEFAULT
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    await pool.set(f"sf:{key}", serialized, ex=ttl)


def cache_value_size(value: Any) -> int:
    """Return the serialized cache payload size in bytes."""
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    return len(serialized.encode("utf-8"))


async def cache_delete(key: str) -> None:
    """删除指定缓存key"""
    pool = _get_pool()
    await pool.delete(f"sf:{key}")


async def cache_delete_pattern(pattern: str) -> int:
    """删除匹配模式的所有key，返回删除数量"""
    pool = _get_pool()
    keys = []
    async for key in pool.scan_iter(f"sf:{pattern}"):
        keys.append(key)
    if keys:
        return await pool.delete(*keys)
    return 0


async def cache_exists(key: str) -> bool:
    """检查缓存key是否存在"""
    pool = _get_pool()
    return bool(await pool.exists(f"sf:{key}"))


# ── 智能热点缓存 ──


def _as_positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _adaptive_meta_key(key: str) -> str:
    """访问热度元数据 key；用 hash 避免原始业务 key 过长。"""
    digest = hashlib.md5(key.encode()).hexdigest()[:24]
    return f"cache:adaptive:{digest}"


def adaptive_cache_ttl(base_ttl: int | None, access_count: int | None) -> int:
    """按访问热度动态放大 TTL。

    策略：同一缓存 key 在统计窗口内达到热点阈值后，TTL 自动乘以配置倍数；
    上限默认 6 小时，避免热点页面反复打开时持续打穿后端。
    """
    ttl = _as_positive_int(base_ttl or settings.CACHE_TTL_DEFAULT, settings.CACHE_TTL_DEFAULT)
    if not getattr(settings, "CACHE_ADAPTIVE_ENABLED", True):
        return ttl

    count = _as_positive_int(access_count, 1)
    threshold = _as_positive_int(getattr(settings, "CACHE_ADAPTIVE_HOT_THRESHOLD", 3), 3)
    if count >= threshold:
        multiplier = _as_positive_int(getattr(settings, "CACHE_ADAPTIVE_HOT_MULTIPLIER", 3), 3)
        ttl *= multiplier

    max_ttl = _as_positive_int(getattr(settings, "CACHE_ADAPTIVE_MAX_TTL", 6 * 60 * 60), 6 * 60 * 60)
    return min(ttl, max_ttl)


async def cache_record_access(key: str, *, window: int | None = None) -> int:
    """记录一次缓存访问，返回当前统计窗口内访问次数。"""
    if not getattr(settings, "CACHE_ADAPTIVE_ENABLED", True):
        return 1
    pool = _get_pool()
    meta_key = f"sf:{_adaptive_meta_key(key)}"
    count = int(await pool.incr(meta_key))
    if count == 1:
        effective_window = _as_positive_int(
            window or getattr(settings, "CACHE_ADAPTIVE_HOT_WINDOW", 10 * 60),
            10 * 60,
        )
        await pool.expire(meta_key, effective_window)
    return count


async def cache_current_access_count(key: str) -> int:
    """读取当前统计窗口内访问次数；未记录时按冷 key 处理。"""
    if not getattr(settings, "CACHE_ADAPTIVE_ENABLED", True):
        return 1
    pool = _get_pool()
    raw = await pool.get(f"sf:{_adaptive_meta_key(key)}")
    return _as_positive_int(raw, 1)


async def cache_expire(key: str, ttl: int | None) -> None:
    """刷新缓存 key 的过期时间。"""
    effective_ttl = _as_positive_int(ttl or settings.CACHE_TTL_DEFAULT, settings.CACHE_TTL_DEFAULT)
    pool = _get_pool()
    await pool.expire(f"sf:{key}", effective_ttl)


async def cache_set_adaptive(
    key: str,
    value: Any,
    ttl: int | None = None,
    *,
    access_count: int | None = None,
) -> None:
    """按当前访问热度写缓存。"""
    if access_count is None:
        try:
            access_count = await cache_current_access_count(key)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[cache] 读取热点计数失败 key={} err={}", key, exc)
            access_count = 1
    await cache_set(key, value, ttl=adaptive_cache_ttl(ttl, access_count))


async def refresh_hot_cache_ttl(key: str, base_ttl: int | None, access_count: int | None) -> None:
    """热点命中时续期，让访问越多的缓存保留越久。"""
    base = _as_positive_int(base_ttl or settings.CACHE_TTL_DEFAULT, settings.CACHE_TTL_DEFAULT)
    boosted = adaptive_cache_ttl(base, access_count)
    if boosted <= base:
        return
    try:
        await cache_expire(key, boosted)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[cache] 热点缓存续期失败 key={} ttl={} err={}", key, boosted, exc)


# ── 装饰器 ──


# 默认从 kwargs hash 中排除的参数 — 不可序列化且不应作为缓存维度
_KEY_EXCLUDES_DEFAULT = frozenset({"db", "session", "request", "background_tasks"})


def _preserve_evaluated_signature(wrapper: Callable, func: Callable) -> None:
    """Keep FastAPI/Pydantic OpenAPI generation working for decorated routes.

    ``from __future__ import annotations`` stores route annotations as strings.
    ``functools.wraps`` preserves ``__wrapped__`` but the wrapper function's
    globals still point to this cache module, so Pydantic may fail to resolve
    route-local models such as ``BulkSummaryRequest`` when generating OpenAPI.
    Store an evaluated ``__signature__`` on the wrapper so FastAPI sees concrete
    classes instead of ForwardRef strings.
    """

    try:
        hints = get_type_hints(func)
        signature = inspect.signature(func)
        parameters = [
            parameter.replace(annotation=hints.get(name, parameter.annotation))
            for name, parameter in signature.parameters.items()
        ]
        wrapper.__signature__ = signature.replace(  # type: ignore[attr-defined]
            parameters=parameters,
            return_annotation=hints.get("return", signature.return_annotation),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[cache] preserve evaluated signature failed for {}: {}", getattr(func, "__name__", func), exc)


def cached(
    key_prefix: str,
    ttl: int | None = None,
    key_builder: Callable | None = None,
    *,
    scope_by: tuple[str, ...] = (),
    key_excludes: frozenset[str] = _KEY_EXCLUDES_DEFAULT,
    max_bytes: int | None = None,
    skip_cache_if: Callable[..., bool] | None = None,
):
    """API 路由缓存装饰器。

    [C2] 跨用户污染防御 — 当返回值依赖当前用户/部门时, 必须显式声明 scope_by,
    否则不同 user/department 命中同一缓存导致越权读取。

    用法:
        # 全局缓存 (返回值与用户无关) — dashboard 仪表盘按部门看, 用 scope_by
        @cached("dashboard:overview", ttl=120, scope_by=("current_user.department",))
        async def overview(days: int = 30, current_user=None, ...):
            ...

        # 完全全局 (无 user 维度) — 例如系统配置
        @cached("system:config", ttl=300)
        async def get_system_config():
            ...

    Args:
        key_prefix:    缓存命名空间, 必须以业务名打头 (如 "dashboard:health")
        ttl:           过期秒数, None 用 settings.CACHE_TTL_DEFAULT
        key_builder:   覆盖默认的 kwargs hash 生成器; 通常无需自定义
        scope_by:      关键 — 缓存按这些字段隔离, 支持点号路径 (如 "current_user.id" 或
                       "current_user.department"). 字段从 kwargs 取, 任一为 None 时仍参与
                       拼接 (避免 None / "" 撞同 key).
        key_excludes:  从 kwargs hash 中排除的参数集合 (默认包含 db/session/request)

    安全检查:
      - 若 kwargs 含 current_user 但 scope_by 未声明任何 user/department 字段,
        本函数发出 WARNING 提示作者考虑跨用户污染风险 (不阻断, 留给作者判断).
    """
    has_user_scope = any(s.startswith("current_user") or "user" in s or "department" in s for s in scope_by)

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            skip_cache = False
            if skip_cache_if:
                try:
                    skip_cache = bool(skip_cache_if(*args, **kwargs))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[cache] skip_cache_if 执行失败 fn={} err={}", func.__name__, exc)

            # 1. 业务 key 后缀 — 从 kwargs hash, 排除不可序列化项
            if key_builder:
                business_suffix = key_builder(*args, **kwargs)
            else:
                hashable_kwargs = {k: v for k, v in kwargs.items() if k not in key_excludes}
                raw = json.dumps(hashable_kwargs, sort_keys=True, default=str)
                business_suffix = hashlib.md5(raw.encode()).hexdigest()[:12]

            # 2. scope 后缀 — 强制按声明的字段隔离
            scope_parts: list[str] = []
            for field in scope_by:
                if "." in field:
                    obj_name, attr_path = field.split(".", 1)
                    val = kwargs.get(obj_name)
                    for attr in attr_path.split("."):
                        val = getattr(val, attr, None) if val is not None else None
                else:
                    val = kwargs.get(field)
                scope_parts.append(f"{field}={val}")
            scope_suffix = "|".join(scope_parts)

            cache_key = f"{key_prefix}:{business_suffix}"
            if scope_suffix:
                cache_key = f"{cache_key}:{scope_suffix}"

            # 3. 防御性安全检查 — current_user 在 kwargs 但 scope_by 没声明 user/dept
            if "current_user" in kwargs and not has_user_scope:
                logger.warning(
                    "[cache] 函数 {} 持有 current_user 但 cached() 未声明 scope_by 含 user/department, "
                    "可能导致跨用户缓存污染. cache_key={}",
                    func.__name__, cache_key,
                )

            if skip_cache:
                return await func(*args, **kwargs)

            # 4. 记录访问热度并尝试读缓存
            access_count = 1
            try:
                access_count = await cache_record_access(cache_key)
            except Exception as e:
                logger.debug("[cache] 热点访问计数失败，按冷 key 处理: {}", e)

            try:
                cached_val = await cache_get(cache_key)
                if cached_val is not None:
                    await refresh_hot_cache_ttl(cache_key, ttl, access_count)
                    return cached_val
            except Exception as e:
                logger.warning("缓存读取失败，穿透到数据库: {}", e)

            # 5. 执行实际函数
            result = await func(*args, **kwargs)

            # 6. 写入缓存。超大 JSON 即使命中 Redis 也会带来读取、反序列化和传输开销，
            # 因此默认按全局上限跳过，debug/导出类接口可显式提高或关闭。
            try:
                effective_max = max_bytes
                if effective_max is None:
                    effective_max = getattr(settings, "CACHE_MAX_VALUE_BYTES", 2 * 1024 * 1024)
                if effective_max and cache_value_size(result) > effective_max:
                    logger.info(
                        "[cache] 跳过超大缓存 key={} size>{}B",
                        cache_key,
                        effective_max,
                    )
                else:
                    await cache_set_adaptive(cache_key, result, ttl=ttl, access_count=access_count)
            except Exception as e:
                logger.warning("缓存写入失败: {}", e)

            return result

        # 标记为可缓存，方便手动失效
        wrapper._cache_prefix = key_prefix
        wrapper._cache_scope_by = scope_by
        wrapper._cache_ttl = ttl
        wrapper._cache_max_bytes = max_bytes
        wrapper._cache_skip_cache_if = skip_cache_if
        _preserve_evaluated_signature(wrapper, func)
        return wrapper
    return decorator


# ── Write-through 装饰器 ──


def write_through(
    cache_key_fn: Callable[..., str],
    ttl: int = 60,
):
    """Write-through 缓存装饰器。

    函数执行后自动更新缓存（而非等 TTL 过期）。
    用于写操作：写完 DB 后立即更新缓存，保证一致性。

    Args:
        cache_key_fn: 接收与被装饰函数相同参数，返回缓存 key 字符串。
        ttl:          缓存过期秒数，默认 60 秒。

    用法:
        @write_through(lambda db, source_id, **kw: f"datasources:detail:{source_id}", ttl=120)
        async def update_source(db, source_id, ...):
            ...  # 返回值会被写入缓存
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 先执行写操作
            result = await func(*args, **kwargs)

            # 计算缓存 key 并写入
            try:
                key = cache_key_fn(*args, **kwargs)
                await cache_set_adaptive(key, result, ttl=ttl)
            except Exception as e:
                logger.warning("[write_through] 缓存更新失败 fn={} err={}", func.__name__, e)

            return result

        wrapper._write_through_key_fn = cache_key_fn
        wrapper._write_through_ttl = ttl
        return wrapper
    return decorator


# ── 便捷失效方法 ──


async def invalidate_skill(skill_id: str) -> bool:
    """Skill变更时，清除相关缓存。

    P2-4: 内置 3 次重试。失败时仅清 Skill 相关命名空间（不再 cache_delete_pattern("*")
    全清，避免一次局部故障引发 dashboard / playbook 等无关缓存的雪崩 — 这是 codex 二轮
    review 指出的修正）。

    Returns:
        True 表示主路径或兜底任一成功；False 表示全部失败（极少发生）。
    """
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            await cache_delete_pattern("skills:*")
            await cache_delete(f"skill:detail:{skill_id}")
            return True
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < 2:
                await asyncio.sleep(0.1 * (attempt + 1))

    # 3 次重试均失败 → 兜底范围严格限制在 skill 相关命名空间，绝不波及其他业务
    logger.error(
        "Skill 缓存失效 3 次失败，尝试 skill 命名空间兜底 skill_id={} err={}",
        skill_id, last_exc,
    )
    skill_patterns = (
        "skills:*",
        f"skill:detail:{skill_id}",
        f"skill:meta:{skill_id}",
        f"tpl:meta:{skill_id}",
        f"rag:slice:{skill_id}:*",
    )
    any_success = False
    for pattern in skill_patterns:
        try:
            if "*" in pattern:
                await cache_delete_pattern(pattern)
            else:
                await cache_delete(pattern)
            any_success = True
        except Exception as exc:  # noqa: BLE001
            logger.error("Skill 缓存兜底清理 {} 失败 skill_id={} err={}", pattern, skill_id, exc)
    return any_success


async def invalidate_generated_data_cache(user_id: str | None = None) -> int:
    """清除 Skill 生成数据缓存（报告、待办、收件概览等）。

    这些缓存 TTL 较长（默认 6 小时），所有会新增/修改报告或待办状态的写路径
    都应调用本函数，避免用户在自然过期前看到旧数据。user_id 当前用于预留
    定向清理维度；由于 cached() 的 scope 后缀在 key 尾部，安全起见按命名空间
    清理全部生成数据缓存。
    """
    patterns = (
        "inbox:reports:*",
        "inbox:overview:*",
        "todos:*",
        "execution:decisions:*",
        "sf:overview:*",
        "sf:trace:*",
        "learning:*",
    )
    deleted = 0
    for pattern in patterns:
        try:
            deleted += int(await cache_delete_pattern(pattern) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[cache] 清理生成数据缓存失败 pattern={} user_id={} err={}", pattern, user_id, exc)
    return deleted


async def invalidate_page_cache(*namespaces: str) -> int:
    """按页面级命名空间清缓存。

    页面缓存统一使用 ``<namespace>:...`` 作为 key 前缀。写路径可调用本函数
    精准清理相关页面，而不是全局扫库；Redis 异常时仅记录 debug 并继续。
    """
    deleted = 0
    for namespace in namespaces:
        if not namespace:
            continue
        pattern = namespace if "*" in namespace else f"{namespace}:*"
        try:
            deleted += int(await cache_delete_pattern(pattern) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[cache] 清理页面缓存失败 namespace={} pattern={} err={}", namespace, pattern, exc)
    return deleted


async def invalidate_knowledge_cache() -> int:
    return await invalidate_page_cache("knowledge")


async def invalidate_training_cache() -> int:
    return await invalidate_page_cache("training", "dashboard")


async def invalidate_agent_cache() -> int:
    return await invalidate_page_cache("agent", "aiclaw", "tasktree")


async def invalidate_admin_cache() -> int:
    return await invalidate_page_cache("admin", "org", "users")


async def invalidate_sf_cache() -> int:
    return await invalidate_page_cache("sf")


async def invalidate_learning_cache() -> int:
    return await invalidate_page_cache("learning", "knowledge", "training", "sf", "agent")


async def invalidate_inbox() -> int:
    """向后兼容：旧调用点用于清收件箱相关缓存。"""
    return await invalidate_generated_data_cache()


async def invalidate_inbox_permissions(user_id: str | None = None) -> None:
    """向后兼容：组织/用户权限变化时清收件箱权限与生成数据缓存。"""
    try:
        from app.inbox.service import invalidate_inbox_permissions_cache

        await invalidate_inbox_permissions_cache(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[cache] 清理收件箱权限缓存失败 user_id={} err={}", user_id, exc)
    await invalidate_generated_data_cache(user_id)


async def invalidate_dashboard() -> None:
    """清除看板所有缓存"""
    await cache_delete_pattern("dashboard:*")


async def invalidate_compliance() -> None:
    """清除合规规则缓存"""
    await cache_delete_pattern("compliance:*")


# ── v7 E1：6 类语义命名空间 ──
# 1. tpl:list           任务模板列表（整体，TTL 5min）
# 2. tpl:meta:{id}      单个任务模板元数据（TTL 10min）
# 3. adapter:schema:{name}  adapter schema.json（TTL 10min）
# 4. rag:slice:{skill_id}:{query_hash}  RAG 检索切片（TTL 30min）
# 5. taskcontract:preview:{cache_key}  任务合同预演（TTL 30min，已存在）
# 6. xmodel:{contract_hash}  跨模型一致性 check（TTL 1h，由 D4 写入）


_NAMESPACE_TTL = {
    "tpl:list": 5 * 60,
    "tpl:meta": 10 * 60,
    "adapter:schema": 10 * 60,
    "rag:slice": 30 * 60,
    "taskcontract:preview": 30 * 60,
    "xmodel": 60 * 60,
}


async def cached_namespace_get(namespace: str, key: str, ttl: int | None = None) -> Any | None:
    """读取指定命名空间下的缓存，安全降级（未初始化时返回 None）。"""
    full_key = f"{namespace}:{key}" if key else namespace
    access_count = 1
    try:
        access_count = await cache_record_access(full_key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[cache] {} 热点计数失败: {}", full_key, exc)
    try:
        value = await cache_get(full_key)
        if value is not None:
            base_ttl = ttl or _NAMESPACE_TTL.get(namespace)
            if base_ttl:
                await refresh_hot_cache_ttl(full_key, base_ttl, access_count)
        return value
    except Exception as exc:  # noqa: BLE001
        logger.debug("[cache] {} 读取失败: {}", full_key, exc)
        return None


async def cached_namespace_set(namespace: str, key: str, value: Any, ttl: int | None = None) -> None:
    """写入指定命名空间下的缓存，安全降级。"""
    full_key = f"{namespace}:{key}" if key else namespace
    effective_ttl = ttl or _NAMESPACE_TTL.get(namespace) or settings.CACHE_TTL_DEFAULT
    try:
        await cache_set_adaptive(full_key, value, ttl=effective_ttl)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[cache] {} 写入失败: {}", full_key, exc)


async def invalidate_namespace(namespace: str) -> int:
    """清空指定命名空间下所有缓存，返回删除数。"""
    try:
        return await cache_delete_pattern(f"{namespace}:*")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[cache] 清空 {} 失败: {}", namespace, exc)
        return 0
