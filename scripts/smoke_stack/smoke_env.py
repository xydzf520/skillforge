from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = PROJECT_ROOT / "web"
PORT = int(os.environ.get("SMOKE_PORT", "18000"))
BASE_URL = os.environ.get("SMOKE_BASE_URL", f"http://127.0.0.1:{PORT}")
ASYNC_DB_URL = os.environ.get(
    "SMOKE_DATABASE_URL",
    "postgresql+asyncpg://skillforge:password@localhost:5432/skillforge_test",
)
SYNC_DB_URL = os.environ.get(
    "SMOKE_DATABASE_URL_SYNC",
    "postgresql+psycopg2://skillforge:password@localhost:5432/skillforge_test",
)
SMOKE_SECRET = os.environ.get("SMOKE_SECRET_KEY", "smoke-secret-key-2026")
SMOKE_ADMIN_USERNAME = os.environ.get("SMOKE_ADMIN_USERNAME", "smoke_admin")
SMOKE_ADMIN_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD", "TestPwd2026!")
SMOKE_ADMIN_NAME = os.environ.get("SMOKE_ADMIN_NAME", "Smoke Admin")
SKILL_REPO_PATH = os.environ.get("SMOKE_SKILL_REPO_PATH", str(PROJECT_ROOT / "skills-repo"))


def cprint(text: str, code: str) -> None:
    print(f"\033[{code}m{text}\033[0m")


def step(msg: str) -> None:
    cprint(f"\n== {msg}", "1;36")


def ok(msg: str) -> None:
    cprint(f"  ✓ {msg}", "32")


def fail(msg: str) -> None:
    cprint(f"  ✗ {msg}", "31")
    raise SystemExit(1)


def smoke_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": ASYNC_DB_URL,
        "DATABASE_URL_SYNC": SYNC_DB_URL,
        "REDIS_URL": env.get("REDIS_URL", "redis://localhost:6379/0"),
        "SKILL_REPO_PATH": SKILL_REPO_PATH,
        "PUBLIC_BASE_URL": BASE_URL,
        "SECRET_KEY": SMOKE_SECRET,
        "COOKIE_SECURE": "false",
    })
    return env


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=cwd or PROJECT_ROOT,
        env=smoke_env(),
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        fail(f"命令失败: {' '.join(cmd)}")
    return result


def prepare_database() -> None:
    result = run([sys.executable, "-m", "alembic", "upgrade", "head"], check=False)
    if result.returncode == 0:
        ok("alembic upgrade head 完成")
        return

    combined = f"{result.stdout}\n{result.stderr}"
    if "DuplicateTable" in combined or "already exists" in combined:
        ok("检测到测试库已有预建 schema，执行 alembic stamp head")
        run([sys.executable, "-m", "alembic", "stamp", "head"])
        ok("alembic stamp head 完成")
        return

    sys.stderr.write(result.stdout)
    sys.stderr.write(result.stderr)
    fail("测试库迁移失败")


def wait_healthy(proc: subprocess.Popen[str], timeout: int = 60) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            fail("uvicorn 提前退出，未能启动")
        try:
            resp = requests.get(f"{BASE_URL}/health", timeout=2)
            if resp.status_code == 200:
                ok("/health 返回 200")
                return
            last_error = f"status={resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(1)
    fail(f"等待服务健康检查超时: {last_error}")
