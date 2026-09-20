from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger

from app.bootstrap.background_jobs import BackgroundTasksState, start_background_jobs, stop_background_jobs
from app.bootstrap.health_checks import release_advisory_lock, try_advisory_lock, verify_required_dependencies
from app.common.cache import close_cache, init_cache
from app.common.prompt_registry import init_registry
from app.database import close_db, init_db


def load_prompt_registry() -> None:
    try:
        loaded_count = init_registry()
        logger.info(f"PromptRegistry: 加载了 {loaded_count} 个 prompt 文件")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"PromptRegistry 加载失败: {exc}")


def _auto_register_systemd_service() -> None:
    """Best-effort registration for the web backend service and watchdog.

    This runs inside application startup so any manual `uvicorn app.main:app`
    launch can repair missing/disabled user-level systemd units. It uses
    `--no-restart` to avoid restarting the process that is currently booting.
    """
    if os.environ.get("SKILLFORGE_SERVICE_AUTOREGISTER") != "1" or os.environ.get("SKILLFORGE_SKIP_SERVICE_AUTOREGISTER") == "1":
        return
    if sys.platform != "linux" or not shutil.which("systemctl"):
        return

    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "register_skillforge_service.py"
    if not script.exists():
        logger.warning("service auto-register skipped: missing {}", script)
        return

    try:
        service_active = subprocess.run(
            ["systemctl", "--user", "is-active", "skillforge.service"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).returncode == 0
        service_enabled = subprocess.run(
            ["systemctl", "--user", "is-enabled", "skillforge.service"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).returncode == 0
        timer_enabled = subprocess.run(
            ["systemctl", "--user", "is-enabled", "skillforge-watchdog.timer"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).returncode == 0
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        service_registered = service_enabled and (unit_dir / "skillforge.service").exists()
        watchdog_registered = (
            timer_enabled
            and (unit_dir / "skillforge-watchdog.service").exists()
            and (unit_dir / "skillforge-watchdog.timer").exists()
        )
        if service_registered and watchdog_registered and service_active:
            logger.debug("service auto-register skipped: skillforge.service active and watchdog enabled")
            return

        if service_registered and watchdog_registered:
            logger.info("service auto-register repairing inactive SkillForge service units")

        proc = subprocess.run(
            [sys.executable, str(script), "install", "--no-restart"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("service auto-register failed: {}", exc)
        return

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        logger.warning("service auto-register failed rc={}: {}", proc.returncode, detail)
        return
    logger.info("service auto-register ok: skillforge.service and watchdog enabled")


async def _auto_migrate() -> None:
    """兼容旧测试的可选自动迁移入口。

    当前启动流程不默认执行 Alembic upgrade；这里只保留一个显式可调用的
    best-effort helper：缺少 alembic 时静默跳过，其它异常仅记录日志。
    """
    try:
        from alembic import command
        from alembic.config import Config
    except Exception as exc:  # noqa: BLE001
        logger.info(f"auto_migrate 跳过：alembic 不可用 ({exc})")
        return

    try:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        alembic_ini = root / "alembic.ini"
        if not alembic_ini.exists():
            logger.info("auto_migrate 跳过：缺少 alembic.ini")
            return
        cfg = Config(str(alembic_ini))
        command.upgrade(cfg, "head")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"auto_migrate 执行失败（已降级跳过）: {exc}")


async def startup_runtime() -> BackgroundTasksState:
    await verify_required_dependencies()
    await init_db()
    await init_cache()
    load_prompt_registry()
    is_scheduler_worker = await try_advisory_lock()

    if is_scheduler_worker:
        _auto_register_systemd_service()

        # 启动即清孤儿 bridge_connected_at：只由调度 worker 执行，避免多 worker 重复写库。
        await _clear_bridge_connected_orphans()

        # 缓存预热：只由调度 worker 填充热点数据，避免冷启动时重复打 DB。
        try:
            from app.common.cache_warmup import warmup_all
            warmup_results = await warmup_all()
            ok_count = sum(1 for v in warmup_results.values() if v == "ok")
            logger.info(f"缓存预热完成: {ok_count}/{len(warmup_results)} 成功")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"缓存预热整体失败（不阻塞启动）: {exc}")

        await _validate_skill_repo_git_remote()
    else:
        logger.info("非调度 Worker 跳过 service autoregister / bridge orphan cleanup / cache warmup / git remote check")
    return await start_background_jobs(is_scheduler_worker=is_scheduler_worker)


async def _validate_skill_repo_git_remote() -> None:
    """启动时验证 Skill Git 主线可用；外部 mirror remote 只是可选备份。"""
    try:
        import os
        import subprocess as sp
        from app.config import settings

        canonical_remote = getattr(settings, "SKILL_REPO_CANONICAL_REMOTE", "")
        if canonical_remote:
            result = sp.run(
                ["git", "ls-remote", "--heads", canonical_remote, settings.SKILL_REPO_BRANCH],
                cwd=settings.SKILL_REPO_PATH,
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if result.returncode == 0:
                logger.info("本地 Docker/Gitea Skill Git 主线 remote 可用: {}", canonical_remote.split("@")[-1] if "@" in canonical_remote else canonical_remote)
            else:
                logger.warning("本地 Docker/Gitea Skill Git 主线 remote 不可用: {}", (result.stderr or result.stdout).strip())
        elif getattr(settings, "SKILL_REPO_DOCKER_CONTAINER", "") and getattr(settings, "SKILL_REPO_DOCKER_BARE_REPO", ""):
            result = sp.run(
                [
                    "docker",
                    "exec",
                    "--user",
                    "git",
                    settings.SKILL_REPO_DOCKER_CONTAINER,
                    "git",
                    f"--git-dir={settings.SKILL_REPO_DOCKER_BARE_REPO}",
                    "rev-parse",
                    "--git-dir",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info("本地 Docker/Gitea Skill Git 主线仓库可用: {}", settings.SKILL_REPO_DOCKER_BARE_REPO)
            else:
                logger.warning("本地 Docker/Gitea Skill Git 主线仓库不可用: {}", (result.stderr or result.stdout).strip())
        else:
            logger.warning("未配置 SKILL_REPO_CANONICAL_REMOTE，Skill Git 主线 push 会失败")

        result = sp.run(
            ["git", "remote", "get-url", "origin"],
            cwd=settings.SKILL_REPO_PATH, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.info("skills-repo 外部 mirror remote 已配置: {}", result.stdout.strip().split("@")[-1] if "@" in result.stdout else result.stdout.strip())
        else:
            logger.info("skills-repo 未配置外部 mirror remote origin；不影响本地 Docker/Gitea 主线和运行节点下发")
    except Exception:
        logger.warning("无法验证 Skill Git 主线状态")


async def _clear_bridge_connected_orphans() -> None:
    try:
        from sqlalchemy import update
        from app.database import async_session_factory
        from app.execution.models import OpenClawInstance

        async with async_session_factory() as session:
            result = await session.execute(
                update(OpenClawInstance)
                .where(OpenClawInstance.bridge_connected_at.is_not(None))
                .values(bridge_connected_at=None)
            )
            await session.commit()
            if result.rowcount:
                logger.info(f"启动清理：重置 {result.rowcount} 个实例的残留 bridge_connected_at")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"启动清理 bridge_connected_at 残留失败（不阻塞）: {exc}")


async def shutdown_runtime(state: BackgroundTasksState) -> None:
    await stop_background_jobs(state)
    await release_advisory_lock()
    try:
        from app.agent_core.checkpointer import close_checkpointer

        await close_checkpointer()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"关闭 agent_core checkpointer 失败: {exc}")
    await close_cache()
    await close_db()
