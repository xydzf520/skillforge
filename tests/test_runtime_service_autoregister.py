from pathlib import Path


def test_auto_register_systemd_service_skips_when_active_and_watchdog_enabled(monkeypatch):
    from app.bootstrap import runtime_init
    monkeypatch.setenv("SKILLFORGE_SERVICE_AUTOREGISTER", "1")

    calls = []

    monkeypatch.setattr(runtime_init.sys, "platform", "linux")
    monkeypatch.setattr(runtime_init.shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)
    monkeypatch.setattr(Path, "exists", lambda self: True)

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Result()

    monkeypatch.setattr(runtime_init.subprocess, "run", fake_run)

    runtime_init._auto_register_systemd_service()

    assert [call[0] for call in calls] == [
        ["systemctl", "--user", "is-active", "skillforge.service"],
        ["systemctl", "--user", "is-enabled", "skillforge.service"],
        ["systemctl", "--user", "is-enabled", "skillforge-watchdog.timer"],
    ]


def test_auto_register_systemd_service_repairs_when_inactive_but_watchdog_enabled(monkeypatch):
    from app.bootstrap import runtime_init
    monkeypatch.setenv("SKILLFORGE_SERVICE_AUTOREGISTER", "1")

    calls = []

    monkeypatch.setattr(runtime_init.sys, "platform", "linux")
    monkeypatch.setattr(runtime_init.shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)
    monkeypatch.setattr(Path, "exists", lambda self: True)

    class Result:
        stdout = "ok"
        stderr = ""

        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["systemctl", "--user", "is-active", "skillforge.service"]:
            return Result(3)
        return Result(0)

    monkeypatch.setattr(runtime_init.subprocess, "run", fake_run)

    runtime_init._auto_register_systemd_service()

    assert len(calls) == 4
    cmd, kwargs = calls[-1]
    assert Path(cmd[-3]).name == "register_skillforge_service.py"
    assert cmd[-2:] == ["install", "--no-restart"]
    assert kwargs["timeout"] == 15


def test_auto_register_systemd_service_installs_when_missing(monkeypatch):
    from app.bootstrap import runtime_init
    monkeypatch.setenv("SKILLFORGE_SERVICE_AUTOREGISTER", "1")

    calls = []

    monkeypatch.setattr(runtime_init.sys, "platform", "linux")
    monkeypatch.setattr(runtime_init.shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)

    def fake_exists(self):
        return self.name == "register_skillforge_service.py"

    monkeypatch.setattr(Path, "exists", fake_exists)

    class Missing:
        returncode = 1
        stdout = "disabled"
        stderr = ""

    class Installed:
        returncode = 0
        stdout = "installed"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            return Missing()
        if cmd[:3] == ["systemctl", "--user", "is-enabled"]:
            return Missing()
        return Installed()

    monkeypatch.setattr(runtime_init.subprocess, "run", fake_run)

    runtime_init._auto_register_systemd_service()

    assert len(calls) == 4
    cmd, kwargs = calls[-1]
    assert Path(cmd[-3]).name == "register_skillforge_service.py"
    assert cmd[-2:] == ["install", "--no-restart"]
    assert kwargs["timeout"] == 15


def test_auto_register_systemd_service_can_be_disabled(monkeypatch):
    from app.bootstrap import runtime_init
    monkeypatch.setenv("SKILLFORGE_SERVICE_AUTOREGISTER", "1")

    monkeypatch.setenv("SKILLFORGE_SKIP_SERVICE_AUTOREGISTER", "1")
    monkeypatch.setattr(runtime_init.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not run")))

    runtime_init._auto_register_systemd_service()


def test_public_startup_does_not_install_service_by_default(monkeypatch):
    from app.bootstrap import runtime_init
    monkeypatch.delenv("SKILLFORGE_SERVICE_AUTOREGISTER", raising=False)
    monkeypatch.setattr(runtime_init.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not invoke systemctl")))
    runtime_init._auto_register_systemd_service()
