import importlib.util
from argparse import Namespace
from pathlib import Path
import sys


def _load_register_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "register_skillforge_service.py"
    spec = importlib.util.spec_from_file_location("register_skillforge_service", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_render_user_unit_uses_current_project_paths():
    mod = _load_register_module()
    root = Path("/home/skillforge/skillforge")
    python = str(Path(sys.executable))

    text = mod.render_user_unit(
        root=root,
        host="127.0.0.1",
        port=8000,
        workers=1,
        log_path="/tmp/skillforge.log",
    )

    assert "WorkingDirectory=/home/skillforge/skillforge" in text
    assert "EnvironmentFile=/home/skillforge/skillforge/.env" in text
    assert f"ExecStart={python} -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1" in text
    assert "Restart=always" in text
    assert text.index("StartLimitIntervalSec=600") < text.index("[Service]")
    assert "WantedBy=default.target" in text


def test_render_watchdog_units_monitor_service():
    mod = _load_register_module()
    root = Path("/home/skillforge/skillforge")
    python = str(Path(sys.executable))

    service = mod.render_watchdog_service(root=root)
    timer = mod.render_watchdog_timer()

    assert "Type=oneshot" in service
    assert f"ExecStart={python} /home/skillforge/skillforge/scripts/register_skillforge_service.py ensure" in service
    assert "OnBootSec=30s" in timer
    assert "OnUnitActiveSec=60s" in timer
    assert "Unit=skillforge-watchdog.service" in timer


def test_render_user_unit_prefers_project_venv_when_available(tmp_path):
    mod = _load_register_module()
    root = tmp_path / "repo"
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").write_text("", encoding="utf-8")

    text = mod.render_user_unit(
        root=root,
        host="127.0.0.1",
        port=8000,
        workers=1,
        log_path="/tmp/skillforge.log",
    )

    assert f"ExecStart={root}/venv/bin/python -m uvicorn app.main:app" in text
    assert f'Environment="PATH={root}/venv/bin:' in text


def test_install_writes_service_and_watchdog_units(tmp_path, monkeypatch):
    mod = _load_register_module()
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("", encoding="utf-8")
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").write_text("", encoding="utf-8")
    calls = []

    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mod, "ensure_linger", lambda: None)
    monkeypatch.setattr(mod, "run_systemctl_user", lambda *args, **kwargs: calls.append(args))

    rc = mod.install(
        Namespace(
            root=str(root),
            host="127.0.0.1",
            port=8000,
            workers=1,
            log_path="/tmp/skillforge.log",
            no_restart=False,
        )
    )

    assert rc == 0
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    assert (unit_dir / "skillforge.service").exists()
    assert (unit_dir / "skillforge-watchdog.service").exists()
    assert (unit_dir / "skillforge-watchdog.timer").exists()
    assert ("daemon-reload",) in calls
    assert ("enable", "skillforge.service") in calls
    assert ("enable", "--now", "skillforge-watchdog.timer") in calls
    assert ("restart", "skillforge.service") in calls


def test_ensure_does_nothing_when_service_active(tmp_path, monkeypatch):
    mod = _load_register_module()
    calls = []

    monkeypatch.setattr(mod, "_validate_root", lambda root: 0)
    monkeypatch.setattr(mod, "_is_unit_active", lambda name: True)
    monkeypatch.setattr(mod, "run_systemctl_user", lambda *args, **kwargs: calls.append(args))

    rc = mod.ensure(Namespace(root=str(tmp_path)))

    assert rc == 0
    assert calls == []


def test_ensure_installs_when_inactive_and_registration_missing(tmp_path, monkeypatch):
    mod = _load_register_module()
    installed = []

    monkeypatch.setattr(mod, "_validate_root", lambda root: 0)
    monkeypatch.setattr(mod, "_is_unit_active", lambda name: False)
    monkeypatch.setattr(mod, "_is_unit_enabled", lambda name: False)
    monkeypatch.setattr(mod, "install", lambda args: installed.append(args) or 0)

    args = Namespace(root=str(tmp_path))
    rc = mod.ensure(args)

    assert rc == 0
    assert installed == [args]


def test_ensure_starts_registered_inactive_service(tmp_path, monkeypatch):
    mod = _load_register_module()
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "skillforge.service").write_text("", encoding="utf-8")
    (unit_dir / "skillforge-watchdog.service").write_text("", encoding="utf-8")
    (unit_dir / "skillforge-watchdog.timer").write_text("", encoding="utf-8")
    active_results = iter([False, True])
    calls = []

    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mod, "_validate_root", lambda root: 0)
    monkeypatch.setattr(mod, "_is_unit_active", lambda name: next(active_results))
    monkeypatch.setattr(mod, "_is_unit_enabled", lambda name: True)
    monkeypatch.setattr(mod, "run_systemctl_user", lambda *args, **kwargs: calls.append(args))

    rc = mod.ensure(Namespace(root=str(tmp_path)))

    assert rc == 0
    assert ("start", "skillforge.service") in calls
