"""跨 worker 缓存失效用的版本号戳（C3）。

背景：`_load_org_indexes` 曾经靠进程级 dict + 60s TTL 做缓存，invalidate 时
只清本 worker 内存。uvicorn 多 worker 部署时，worker A 写 + 清缓存，worker B
仍在 TTL 窗口内读旧索引。

方案：PG 级单表 `system_meta(key, version)` 作为自增版本号源。
- 所有修改 org_units / UserOrgMembership 的事务在 commit 前
  ``UPDATE system_meta SET version = version + 1 WHERE key = 'org_units_cache_version'``
- 读缓存前先 ``SELECT version FROM system_meta WHERE key = ...``（< 1ms，无 JOIN）
- 缓存 entry 带 version 字段；`cached_version != db_version` → 强制 miss 重查

事务语义保证：
- UPDATE 与主业务写入同一 tx，commit 成功 → 全局可见；rollback → 版本不涨，不会
  误触发其他 worker 重查
- 本进程的 `_ORG_INDEXES_CACHE` 清空是"最好努力"——即使 rollback 后清了也只是
  下次读重查 DB，拿到的仍是回滚后的状态，无正确性问题
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


ORG_UNITS_CACHE_KEY = "org_units_cache_version"


async def read_cache_version(db: AsyncSession, key: str) -> int | None:
    """读某个 key 的当前 version；表不存在（SQLite 老夹具）或 key 不存在返回 None。

    None 语义 = "版本号不可用，不走跨 worker 一致性保证"——调用方应降级到
    仅用本进程 TTL。这样生产 PG 走版本号路径，轻量测试夹具仍然跑。

    与 bump 对称，用 SAVEPOINT 包裹避免表不存在时污染外层 tx。
    """
    try:
        async with db.begin_nested():
            value = await db.scalar(
                text("SELECT version FROM system_meta WHERE key = :key"),
                {"key": key},
            )
    except Exception:
        return None
    return int(value) if value is not None else None


async def bump_cache_version(db: AsyncSession, key: str) -> None:
    """将 `system_meta[key].version` 自增 1。与调用方当前事务绑定。

    用 SAVEPOINT 包裹：表不存在（部分历史库 / 轻量测试夹具没跑迁移）时，
    UPDATE 抛错会让 PG 把整个 transaction 标记为 aborted，后续 SQL 全挂。
    用 `begin_nested` 把异常隔离在 savepoint 内，rollback savepoint 即可，
    外层事务继续可用。

    异常静默——走"无版本号"降级。
    """
    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    "UPDATE system_meta "
                    "SET version = version + 1, updated_at = NOW() "
                    "WHERE key = :key"
                ),
                {"key": key},
            )
    except Exception:
        return
