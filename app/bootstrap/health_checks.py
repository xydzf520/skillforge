from __future__ import annotations

from pathlib import Path

from loguru import logger


_advisory_lock_conn = None
_AI_ENV_WARNING_PREFIX = "AI_API_KEY 未设置"


async def _system_config_has_complete_ai_config() -> bool:
    """Return whether backend-managed AI config is complete enough for LLM calls."""
    from sqlalchemy import select
    from app.common.models import SystemConfig
    from app.database import async_session_factory

    required = {"ai.api_base", "ai.api_key", "ai.model"}
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(SystemConfig.key, SystemConfig.value).where(SystemConfig.key.in_(required))
        )).all()
    values = {str(key): str(value or "").strip() for key, value in rows}
    return all(values.get(key) for key in required)


async def _normalize_operational_warnings(warnings: list[str]) -> list[str]:
    """Prefer backend system_config AI status over legacy .env AI_API_KEY warnings."""
    has_ai_env_warning = any(w.startswith(_AI_ENV_WARNING_PREFIX) for w in warnings)
    if not has_ai_env_warning:
        return warnings

    normalized = [w for w in warnings if not w.startswith(_AI_ENV_WARNING_PREFIX)]
    try:
        if await _system_config_has_complete_ai_config():
            return normalized
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取 AI 后台配置状态失败，保留 .env AI_API_KEY 告警: {}", exc)
        return warnings
    normalized.append("AI 后台 system_config 缺少 ai.api_base/ai.api_key/ai.model — Agent/AI 生成需要先在后台系统配置中维护")
    return normalized


async def try_advisory_lock() -> bool:
    """尝试获取 PostgreSQL Advisory Lock，确保只有一个 Worker 运行调度器和消息消费者。"""
    global _advisory_lock_conn
    from sqlalchemy import text
    from app.database import engine

    try:
        _advisory_lock_conn = await engine.connect()
        result = await _advisory_lock_conn.execute(text("SELECT pg_try_advisory_lock(1)"))
        locked = result.scalar()
        # Session-level advisory locks survive COMMIT. End the implicit
        # transaction immediately so this singleton lock cannot pin VACUUM's
        # xmin for the lifetime of the application process.
        await _advisory_lock_conn.commit()
        if not locked:
            await _advisory_lock_conn.close()
            _advisory_lock_conn = None
        return bool(locked)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Advisory lock 获取失败: {exc}")
        if _advisory_lock_conn:
            await _advisory_lock_conn.close()
            _advisory_lock_conn = None
        return False


async def release_advisory_lock() -> None:
    global _advisory_lock_conn
    if _advisory_lock_conn:
        await _advisory_lock_conn.close()
        _advisory_lock_conn = None


async def verify_required_dependencies() -> None:
    """启动自检：所有必备依赖必须可用，缺一不可。"""
    failures: list[str] = []

    pkg_checks = [
        ("prometheus_fastapi_instrumentator", "/metrics 端点 + FastAPI 中间件埋点"),
        ("prometheus_client", "telemetry KPI 计数器/直方图"),
        ("langgraph", "v7 agent_core LangGraph runtime"),
        ("psycopg_pool", "langgraph PostgresSaver checkpoint 池"),
        ("langgraph.checkpoint.postgres", "langgraph PostgreSQL checkpointer"),
        ("websockets", "AIClaw bridge / OpenClaw client"),
        ("jsonschema", "v7 adapter schema.json 校验"),
        ("redis", "缓存层客户端"),
        ("bcrypt", "用户密码 + enrollment token 哈希"),
        ("yaml", "SKILL.md frontmatter 解析"),
        ("git", "GitPython — Skill 版本控制"),
        ("apscheduler", "定时调度"),
        ("cryptography", "Cookie 加密 + 钉钉回调 AES 解密 + Bridge Ed25519"),
        ("itsdangerous", "session cookie 签名 + 钉钉 open link token"),
    ]
    for module_name, purpose in pkg_checks:
        try:
            __import__(module_name)
        except ImportError as exc:
            failures.append(f"  ✗ Python 包 {module_name} 缺失 ({purpose}): {exc}")

    try:
        from sqlalchemy import text
        from app.database import engine

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        failures.append(f"  ✗ PostgreSQL 连接失败: {type(exc).__name__}: {exc}")

    try:
        import redis.asyncio as aioredis
        from app.config import settings

        client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        try:
            await client.ping()
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        failures.append(
            f"  ✗ Redis 连接失败: {type(exc).__name__}: {exc}\n"
            f"    解决: sudo systemctl start redis-server 或 docker run -d -p 6379:6379 redis"
        )

    try:
        from app.config import settings

        repo = Path(settings.SKILL_REPO_PATH)
        if not repo.exists():
            failures.append(f"  ✗ SKILL_REPO_PATH 不存在: {repo}")
        elif not repo.is_dir():
            failures.append(f"  ✗ SKILL_REPO_PATH 不是目录: {repo}")
        elif not (repo / ".git").exists():
            failures.append(f"  ✗ Skill Git 仓库未初始化: {repo}")
        else:
            test_file = repo / ".skillforge_write_test"
            try:
                test_file.write_text("ok")
                test_file.unlink()
            except Exception as exc:
                failures.append(f"  ✗ SKILL_REPO_PATH 不可写 {repo}: {exc}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"  ✗ Skill 仓库检查异常: {exc}")

    try:
        from app.config import settings

        if settings.CODING_AGENT_ENABLED:
            from app.coding_agent.runtime_availability import RUNTIME_MESSAGE
            # Optional authoring must not prevent the control plane from starting.
            logger.warning("编程 Harness 未生效：{}", RUNTIME_MESSAGE)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"  ✗ coding_agent 检查异常: {exc}")

    try:
        from app.config import settings

        for issue in settings.validate_production():
            failures.append(f"  ✗ 配置错误: {issue}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"  ✗ 配置检查异常: {exc}")

    if failures:
        message = (
            "\n══════════════════════════════════════════════════════════════════\n"
            "[SkillForge] 启动自检失败 — 必备依赖/服务缺失, 拒绝运行\n"
            "══════════════════════════════════════════════════════════════════\n"
            + "\n".join(failures)
            + "\n\n请先解决以上问题再启动。pip 包: pip install -r requirements.txt\n"
        )
        logger.error(message)
        raise RuntimeError(message)

    # 非阻断性配置警告（功能降级但不阻止启动）
    try:
        from app.config import settings as _settings
        op_warnings = await _normalize_operational_warnings(_settings.validate_operational_warnings())
        if op_warnings:
            warn_msg = (
                "\n[SkillForge] 配置警告 — 以下功能可能不可用:\n"
                + "\n".join(f"  ⚠ {w}" for w in op_warnings)
            )
            logger.warning(warn_msg)
    except Exception as e:
        logger.warning("启动自检警告输出失败: {}", e)

    logger.info("启动自检通过: 所有必备依赖 + 外部服务可用")
