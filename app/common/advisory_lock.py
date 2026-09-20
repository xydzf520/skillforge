"""PostgreSQL advisory lock 公用 helper（H5 + L1）。

统一管理 `pg_advisory_xact_lock` 的获取，承担以下责任：

1. **异常降级**：生产 PG 走真实 advisory lock；SQLite / 无 pg_advisory_xact_lock
   的轻量测试夹具上静默跳过。except 需覆盖所有可能的方言不匹配异常
   （`ProgrammingError` / `NoSuchTableError` / `OperationalError` / 甚至
   `NotImplementedError`——不同 driver 报错类别不一）。

2. **Key 规范化**：所有 key 走 `hashtext(:key)`，避免各处手写 SQL 文本、漏加
   参数化带来注入风险。

3. **命名空间**：所有 key 用 `"<namespace>:<id>"` 约定，相关操作锁同 id 走同一
   lock，确保同一实体的写操作串行化。

当前命名空间：
- ``user:{user_id}``: disable / update / 审批链重解析（统一顺序，避免死锁）
- ``playbook_version:{playbook_name}``: Playbook 版本号分配 + tag 创建
- ``project_gateway:{channel}:{run_id}:{request_id}``: 项目输入/输出/能力调用幂等串行化
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import NoSuchTableError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession


# 所有"可忽略为非 PG 方言"的异常类别。列表化便于审计。
_DIALECT_FALLBACK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ProgrammingError,
    NoSuchTableError,
    OperationalError,
    NotImplementedError,
)


async def acquire_xact_lock(db: AsyncSession, key: str) -> bool:
    """获取 pg_advisory_xact_lock。事务 commit / rollback 时自动释放。

    返回 True 表示锁已拿到（或已在此事务中拿过）；False 表示方言不支持、
    已降级到"无锁"模式——调用方可据此决定是否额外做乐观并发检查。
    """
    try:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": key},
        )
        return True
    except _DIALECT_FALLBACK_EXCEPTIONS as exc:
        logger.warning(
            "advisory_xact_lock 已跳过（非 PG 方言）key={} err={}",
            key, exc,
        )
        return False


async def acquire_user_lock(db: AsyncSession, user_id: str) -> bool:
    """为一个 user_id 的写操作（禁用 / 改角色 / 审批链重解析）拿锁。

    所有涉及同一 user 的临界区必须先调本函数，确保：
    - disable_user(u) vs update_user(u) vs reresolve(u) 串行化
    - 避免 "disable_user 先过 count 校验 → reresolve 并发插入新 approval"
      这类 TOCTOU
    """
    return await acquire_xact_lock(db, f"user:{user_id}")
