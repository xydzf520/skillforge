#!/usr/bin/env python3
"""Install and manage the SkillForge user systemd service.

This script is intentionally kept in-repo so a deployment can register the
backend from the checked-out code path instead of relying on a hand-written
tmux command. It installs a user-level unit by default, which works without
sudo when user lingering is enabled.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from shutil import which


UNIT_NAME = "skillforge.service"
WATCHDOG_SERVICE_NAME = "skillforge-watchdog.service"
WATCHDOG_TIMER_NAME = "skillforge-watchdog.timer"
DEFAULT_LOG_PATH = "/tmp/skillforge-uvicorn-codex.log"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def venv_dir(root: Path) -> Path:
    for name in ("venv", ".venv"):
        candidate = root / name
        if (candidate / "bin" / "python").exists():
            return candidate
    return root / "venv"


def python_executable(root: Path) -> Path:
    """Prefer the project venv, but allow system Python for local checked-out dev trees."""
    for name in ("venv", ".venv"):
        candidate = root / name / "bin" / "python"
        if candidate.exists():
            return candidate
    current = Path(sys.executable)
    if current.exists():
        return current
    system = which("python3") or which("python")
    return Path(system) if system else current


def path_entries(root: Path) -> str:
    entries: list[str] = []
    for name in ("venv", ".venv"):
        bin_dir = root / name / "bin"
        if (bin_dir / "python").exists():
            entries.append(str(bin_dir))
            break
    entries.extend(["/home/skillforge/.local/bin", "/usr/local/bin", "/usr/bin", "/bin"])
    return ":".join(dict.fromkeys(entries))


def render_user_unit(
    *,
    root: Path,
    host: str,
    port: int,
    workers: int,
    log_path: str,
) -> str:
    python = python_executable(root)
    env_file = root / ".env"
    return f"""[Unit]
Description=SkillForge Web Backend
After=network-online.target docker.service
Wants=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=20

[Service]
Type=simple
WorkingDirectory={root}
EnvironmentFile={env_file}
Environment="PATH={path_entries(root)}"
ExecStart={python} -m uvicorn app.main:app --host {host} --port {port} --workers {workers}
Restart=always
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=30
StandardOutput=append:{log_path}
StandardError=append:{log_path}

[Install]
WantedBy=default.target
"""


def render_watchdog_service(*, root: Path) -> str:
    python = python_executable(root)
    script = root / "scripts" / "register_skillforge_service.py"
    return f"""[Unit]
Description=SkillForge Web Backend Watchdog
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={root}
ExecStart={python} {script} ensure
"""


def render_watchdog_timer() -> str:
    return f"""[Unit]
Description=Monitor and recover SkillForge Web Backend

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
AccuracySec=10s
Persistent=true
Unit={WATCHDOG_SERVICE_NAME}

[Install]
WantedBy=timers.target
"""


def run_systemctl_user(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def ensure_linger() -> None:
    user = os.environ.get("USER") or ""
    if not user:
        return
    try:
        state = subprocess.run(
            ["loginctl", "show-user", user, "-p", "Linger"],
            check=False,
            capture_output=True,
            text=True,
        )
        if "Linger=yes" in (state.stdout or ""):
            return
        subprocess.run(["loginctl", "enable-linger", user], check=False)
    except FileNotFoundError:
        return


def _validate_root(root: Path) -> int:
    if not (root / "app" / "main.py").exists():
        print(f"invalid SkillForge root: {root}", file=sys.stderr)
        return 2
    python = python_executable(root)
    if not python.exists():
        print(
            f"missing python executable: checked {root / 'venv' / 'bin' / 'python'}, "
            f"{root / '.venv' / 'bin' / 'python'}, and current interpreter {python}",
            file=sys.stderr,
        )
        return 2
    return 0


def _write_units(
    *,
    root: Path,
    host: str,
    port: int,
    workers: int,
    log_path: str,
) -> dict[str, Path]:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / UNIT_NAME
    watchdog_service_path = unit_dir / WATCHDOG_SERVICE_NAME
    watchdog_timer_path = unit_dir / WATCHDOG_TIMER_NAME
    unit_path.write_text(
        render_user_unit(
            root=root,
            host=host,
            port=port,
            workers=workers,
            log_path=log_path,
        ),
        encoding="utf-8",
    )
    watchdog_service_path.write_text(render_watchdog_service(root=root), encoding="utf-8")
    watchdog_timer_path.write_text(render_watchdog_timer(), encoding="utf-8")
    return {
        UNIT_NAME: unit_path,
        WATCHDOG_SERVICE_NAME: watchdog_service_path,
        WATCHDOG_TIMER_NAME: watchdog_timer_path,
    }


def install(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    invalid = _validate_root(root)
    if invalid:
        return invalid

    paths = _write_units(
        root=root,
        host=args.host,
        port=args.port,
        workers=args.workers,
        log_path=args.log_path,
    )
    ensure_linger()
    run_systemctl_user("daemon-reload")
    run_systemctl_user("enable", UNIT_NAME)
    run_systemctl_user("enable", "--now", WATCHDOG_TIMER_NAME)
    if not args.no_restart:
        run_systemctl_user("restart", UNIT_NAME)
    print(f"installed {UNIT_NAME} -> {paths[UNIT_NAME]}")
    print(f"installed {WATCHDOG_TIMER_NAME} -> {paths[WATCHDOG_TIMER_NAME]}")
    print("status: systemctl --user status skillforge.service")
    return 0


def _is_unit_enabled(name: str) -> bool:
    return run_systemctl_user("is-enabled", name, check=False).returncode == 0


def _is_unit_active(name: str) -> bool:
    return run_systemctl_user("is-active", name, check=False).returncode == 0


def _service_unit_path(name: str) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / name


def ensure(args: argparse.Namespace) -> int:
    """Watchdog entrypoint: active? return; inactive? ensure registration then start."""
    root = Path(args.root).resolve()
    invalid = _validate_root(root)
    if invalid:
        return invalid

    if _is_unit_active(UNIT_NAME):
        print(f"{UNIT_NAME} active")
        return 0

    service_registered = _is_unit_enabled(UNIT_NAME) and _service_unit_path(UNIT_NAME).exists()
    timer_registered = (
        _is_unit_enabled(WATCHDOG_TIMER_NAME)
        and _service_unit_path(WATCHDOG_SERVICE_NAME).exists()
        and _service_unit_path(WATCHDOG_TIMER_NAME).exists()
    )
    if not service_registered or not timer_registered:
        print(f"{UNIT_NAME} inactive and registration incomplete; installing")
        return install(args)

    print(f"{UNIT_NAME} inactive; starting")
    run_systemctl_user("start", UNIT_NAME, check=False)
    if _is_unit_active(UNIT_NAME):
        print(f"{UNIT_NAME} started")
        return 0
    run_systemctl_user("restart", UNIT_NAME)
    print(f"{UNIT_NAME} restarted")
    return 0


def status(_args: argparse.Namespace) -> int:
    proc = run_systemctl_user("status", UNIT_NAME, "--no-pager", check=False)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def uninstall(_args: argparse.Namespace) -> int:
    run_systemctl_user("stop", WATCHDOG_TIMER_NAME, check=False)
    run_systemctl_user("disable", WATCHDOG_TIMER_NAME, check=False)
    run_systemctl_user("stop", WATCHDOG_SERVICE_NAME, check=False)
    run_systemctl_user("disable", WATCHDOG_SERVICE_NAME, check=False)
    run_systemctl_user("stop", UNIT_NAME, check=False)
    run_systemctl_user("disable", UNIT_NAME, check=False)
    for name in (UNIT_NAME, WATCHDOG_SERVICE_NAME, WATCHDOG_TIMER_NAME):
        unit_path = _service_unit_path(name)
        if unit_path.exists():
            unit_path.unlink()
    run_systemctl_user("daemon-reload", check=False)
    print(f"uninstalled {UNIT_NAME}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register SkillForge as a user systemd service")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_install = sub.add_parser("install", help="write, enable, and restart the user service")
    p_install.add_argument("--root", default=str(project_root()))
    p_install.add_argument("--host", default="127.0.0.1")
    p_install.add_argument("--port", type=int, default=8000)
    p_install.add_argument("--workers", type=int, default=1)
    p_install.add_argument("--log-path", default=DEFAULT_LOG_PATH)
    p_install.add_argument("--no-restart", action="store_true")
    p_install.set_defaults(func=install)

    p_ensure = sub.add_parser("ensure", help="watchdog check: start service if inactive, register if missing")
    p_ensure.add_argument("--root", default=str(project_root()))
    p_ensure.add_argument("--host", default="127.0.0.1")
    p_ensure.add_argument("--port", type=int, default=8000)
    p_ensure.add_argument("--workers", type=int, default=1)
    p_ensure.add_argument("--log-path", default=DEFAULT_LOG_PATH)
    p_ensure.add_argument("--no-restart", action="store_true")
    p_ensure.set_defaults(func=ensure)

    p_status = sub.add_parser("status", help="show service status")
    p_status.set_defaults(func=status)

    p_uninstall = sub.add_parser("uninstall", help="disable and remove the user service")
    p_uninstall.set_defaults(func=uninstall)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
