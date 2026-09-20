"""
SQLAlchemy async engine + session 管理。
所有数据库操作统一通过 get_db() 获取 AsyncSession。
"""

import importlib.util

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

_engine_kwargs = {
    "pool_size": settings.DB_POOL_SIZE,
    "max_overflow": settings.DB_MAX_OVERFLOW,
    "pool_timeout": settings.DB_POOL_TIMEOUT,
    "pool_recycle": 3600,     # 每小时回收连接（防止stale connections）
    "pool_pre_ping": True,    # 使用前ping检测连接有效性
    "echo": settings.SQL_ECHO,
}
if str(settings.DATABASE_URL).startswith("postgresql+asyncpg"):
    # DB 列多为 TIMESTAMP WITHOUT TIME ZONE；连接时区固定为北京时间，
    # 让 server_default=now()、日期截断和 SQL 侧时间计算与业务口径一致。
    _engine_kwargs["connect_args"] = {
        "server_settings": {"timezone": "Asia/Shanghai"},
    }

# 异步引擎（asyncpg驱动）
engine = create_async_engine(
    settings.DATABASE_URL,
    **_engine_kwargs,
)

# 异步session工厂
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """所有ORM模型的基类"""
    pass


def _latest_migration_revision(versions_dir) -> str | None:
    version_files = sorted(f for f in versions_dir.glob("*.py") if f.stem != "__pycache__")
    if not version_files:
        return None
    latest_file = version_files[-1]
    spec = importlib.util.spec_from_file_location(latest_file.stem, latest_file)
    if spec is None or spec.loader is None:
        return latest_file.stem.split("_")[0]
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(getattr(module, "revision", "") or latest_file.stem.split("_")[0])


async def get_db():
    """FastAPI依赖注入：获取数据库session，请求结束自动关闭。

    H4：commit 成功后才执行 service 层注册的 post-commit 回调（缓存失效等
    外部副作用），rollback 路径完全跳过——避免"DB 未生效但缓存已清"的窗口。
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            # 异常路径：必须丢弃尚未触发的回调，防止 session 复用时漏触发或重复触发
            try:
                from app.users.service import _drop_post_commit_callbacks

                _drop_post_commit_callbacks(session)
            except Exception:  # noqa: BLE001
                pass
            raise
        else:
            # commit 成功后串行执行 post-commit 回调
            try:
                from app.users.service import flush_post_commit_callbacks

                await flush_post_commit_callbacks(session)
            except Exception:  # noqa: BLE001
                # 回调本身已捕获并降级；这里仅兜底防止依赖 import 失败炸请求
                pass
        finally:
            await session.close()


async def init_db():
    """应用启动时检查数据库连接 + Alembic 迁移版本校验"""
    from sqlalchemy import text
    from loguru import logger

    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

    # 检查 Alembic 迁移版本是否与代码中最新 revision 一致
    try:
        from pathlib import Path
        versions_dir = Path(__file__).resolve().parent.parent / "migrations" / "versions"
        if versions_dir.exists():
            latest_code = _latest_migration_revision(versions_dir)
            if latest_code:

                async with engine.connect() as conn:
                    result = await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
                    row = result.first()
                    db_version = row[0] if row else None

                if db_version != latest_code:
                    logger.warning(
                        f"Alembic 版本不匹配: 数据库={db_version}, 代码={latest_code}。"
                        f"请运行 alembic upgrade head"
                    )
                else:
                    logger.info(f"Alembic 版本一致: {db_version}")
    except Exception as e:
        # 版本检查失败不阻塞启动（alembic_version 表可能不存在）
        logger.debug(f"Alembic 版本检查跳过: {e}")


async def close_db():
    """应用关闭时释放连接池"""
    await engine.dispose()
