import base64
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _write_basic_skill(root: Path, name: str) -> None:
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    (root / "scripts" / "main.py").write_text("def main(payload):\n    return payload\n", encoding="utf-8")


def _install_store() -> dict[str, list[dict]]:
    return {}


def _remember_install(store: dict[str, list[dict]], instance_id: str, files: list[dict]) -> None:
    store[instance_id] = [dict(item) for item in files]


def _read_installed_skill(store: dict[str, list[dict]]):
    async def fake_read(self, skill_id, target_dir=None):
        return {"files": store.get(self.instance_id, [])}

    return fake_read


def test_sync_bundle_includes_large_entry_script_but_skips_other_large_files(tmp_path):
    from app.execution.sync_service import (
        SYNC_MAX_FILE_BYTES,
        _build_skill_sync_bundle,
    )

    skill_root = tmp_path / "skill-large-main"
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "data").mkdir()
    (skill_root / "SKILL.md").write_text(
        "---\n"
        "name: Large Main Skill\n"
        "runtime:\n"
        "  script_entry: scripts/main.py\n"
        "---\n",
        encoding="utf-8",
    )
    (skill_root / "scripts" / "main.py").write_text(
        "x = '" + ("a" * (SYNC_MAX_FILE_BYTES + 1024)) + "'\n",
        encoding="utf-8",
    )
    (skill_root / "data" / "large.json").write_text(
        "x" * (SYNC_MAX_FILE_BYTES + 1024),
        encoding="utf-8",
    )

    files, error = _build_skill_sync_bundle(str(skill_root))
    paths = {item["path"] for item in files}

    assert error is None
    assert "scripts/main.py" in paths
    assert "data/large.json" not in paths


def test_sync_bundle_from_git_ref_ignores_dirty_worktree(monkeypatch, tmp_path):
    from app.config import settings
    from app.execution.sync_service import _build_skill_sync_bundle_from_git

    skill_root = tmp_path / "skill-ref"
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\n"
        "name: Ref Skill\n"
        "runtime:\n"
        "  script_entry: scripts/main.py\n"
        "---\n",
        encoding="utf-8",
    )
    (skill_root / "scripts" / "main.py").write_text("VERSION = 'tagged'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Tester"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "skill-ref"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "-C", str(tmp_path), "tag", "skill-ref/v0.1"], check=True)

    (skill_root / "scripts" / "main.py").write_text("VERSION = 'dirty'\n", encoding="utf-8")
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    files, error = _build_skill_sync_bundle_from_git("skill-ref", "skill-ref/v0.1")
    main_file = next(item for item in files if item["path"] == "scripts/main.py")

    assert error is None
    assert main_file["content"] == "VERSION = 'tagged'\n"


def test_sync_source_error_blocks_entry_script_over_entry_limit(monkeypatch, tmp_path):
    from app.config import settings
    from app.execution.sync_service import (
        SYNC_MAX_ENTRY_FILE_BYTES,
        sync_service,
    )

    skill_root = tmp_path / "skill-too-large-main"
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\n"
        "name: Too Large Main Skill\n"
        "runtime:\n"
        "  script_entry: scripts/main.py\n"
        "---\n",
        encoding="utf-8",
    )
    (skill_root / "scripts" / "main.py").write_text(
        "x = '" + ("a" * (SYNC_MAX_ENTRY_FILE_BYTES + 1)) + "'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    assert sync_service.skill_source_error("skill-too-large-main") == "SKILL_ENTRY_SCRIPT_NOT_SYNCABLE"


@pytest.mark.asyncio
async def test_sync_skill_defaults_to_skill_department_targets(client, monkeypatch, tmp_path):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill
    from app.config import settings

    skill_root = tmp_path / "skill-ec"
    _write_basic_skill(skill_root, "EC Skill")
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(User(id="ec-user", username="ec-user", name="EC", role="aibp", department="EC", is_active=True))
        session.add(Skill(id="skill-ec", name="EC Skill", department="EC", status="active", owner="ec-user"))
        session.add(OpenClawInstance(
            id="ec-openclaw",
            name="EC OpenClaw",
            department="EC",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
        ))
        session.add(OpenClawInstance(
            id="ai-openclaw",
            name="AI OpenClaw",
            department="AI",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
        ))
        await session.commit()

    calls: list[str] = []
    installed_files = _install_store()

    async def fake_install(self, skill_id, files, target_dir=None):
        calls.append(self.instance_id)
        _remember_install(installed_files, self.instance_id, files)
        return {"path": f"/skills/{skill_id}", "files": [f["path"] for f in files], "count": len(files)}

    async def fake_reload(self):
        return {"ok": True}

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", _read_installed_skill(installed_files))
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="ec-user", role="aibp", department="EC", can_view_all=False)
    results = await sync_service.push_skill_to_targets("skill-ec", actor=actor)

    assert calls == ["ec-openclaw"]
    assert len(results) == 1
    assert results[0]["instance_id"] == "ec-openclaw"
    assert results[0]["name"] == "EC OpenClaw"
    assert results[0]["agent_type"] == "aiclaw"
    assert results[0]["agent_purpose"] == "skill_runtime"
    assert results[0]["runtime_eligible"] is True
    assert results[0]["is_platform_default"] is False
    assert results[0]["ok"] is True
    assert results[0]["error"] is None
    assert results[0]["reload"] == {"ok": True, "status": "ok"}
    assert results[0]["result"]["path"] == "/skills/skill-ec"
    assert results[0]["result"]["files"] == ["SKILL.md", "scripts/main.py"]
    assert results[0]["result"]["count"] == 2
    assert results[0]["result"]["verify"]["ok"] is True
    assert results[0]["result"]["verify"]["path"] == "scripts/main.py"
    assert results[0]["result"]["verify"]["expected_sha256"] == results[0]["result"]["verify"]["actual_sha256"]


@pytest.mark.asyncio
async def test_sync_skill_auto_targets_only_skill_runtime_agents(client, monkeypatch, tmp_path):
    from app.auth.models import User
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill

    skill_root = tmp_path / "skill-runtime-routing"
    _write_basic_skill(skill_root, "Runtime Routing")
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(User(id="ec-runtime-user", username="ec-runtime-user", name="EC", role="aibp", department="EC", is_active=True))
        session.add(Skill(id="skill-runtime-routing", name="Runtime Routing", department="EC", status="active", owner="ec-runtime-user"))
        session.add_all([
            OpenClawInstance(
                id="ec-analysis",
                name="EC 分析 Agent",
                department="EC",
                gateway_url="ws://127.0.0.1:18789",
                reload_hook_url="http://127.0.0.1:9000/reload",
                reload_token="",
                is_active=True,
                agent_purpose="analysis",
            ),
            OpenClawInstance(
                id="ec-training",
                name="EC 训练 Agent",
                department="EC",
                gateway_url="ws://127.0.0.1:18789",
                reload_hook_url="http://127.0.0.1:9000/reload",
                reload_token="",
                is_active=True,
                agent_purpose="training",
            ),
            OpenClawInstance(
                id="ec-runtime",
                name="EC 执行 Agent",
                department="EC",
                gateway_url="ws://127.0.0.1:18789",
                reload_hook_url="http://127.0.0.1:9000/reload",
                reload_token="",
                is_active=True,
                agent_purpose="skill_runtime",
            ),
        ])
        await session.commit()

    calls: list[str] = []
    installed_files = _install_store()

    async def fake_install(self, skill_id, files, target_dir=None, shared_files=None):
        calls.append(self.instance_id)
        _remember_install(installed_files, self.instance_id, files)
        return {"ok": True, "count": len(files)}

    async def fake_reload(self):
        return {"ok": True}

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", _read_installed_skill(installed_files))
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="ec-runtime-user", role="aibp", department="EC", can_view_all=False)
    results = await sync_service.push_skill_to_targets("skill-runtime-routing", actor=actor)

    assert calls == ["ec-runtime"]
    assert [item["instance_id"] for item in results] == ["ec-runtime"]
    assert results[0]["runtime_eligible"] is True

    targets = await sync_service.list_sync_targets("skill-runtime-routing", actor=actor)
    by_id = {item["id"]: item for item in targets}
    assert by_id["ec-runtime"]["selectable"] is True
    assert by_id["ec-runtime"]["auto_target"] is True
    assert by_id["ec-analysis"]["runtime_eligible"] is False
    assert by_id["ec-analysis"]["selectable"] is False
    assert by_id["ec-analysis"]["reason"] == "该 Agent 用途为 analysis，不承载 Skill Runtime"
    assert by_id["ec-training"]["runtime_eligible"] is False
    assert by_id["ec-training"]["selectable"] is False


@pytest.mark.asyncio
async def test_sync_skill_selected_analysis_agent_fails_without_install(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill

    skill_root = tmp_path / "skill-selected-analysis"
    _write_basic_skill(skill_root, "Selected Analysis")
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(Skill(id="skill-selected-analysis", name="Selected Analysis", department="EC", status="active", owner="admin"))
        session.add(OpenClawInstance(
            id="analysis-only",
            name="分析专用 Agent",
            department="EC",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
            agent_purpose="analysis",
        ))
        await session.commit()

    async def fail_install(*args, **kwargs):
        raise AssertionError("analysis-only Agent should not receive Skill files")

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fail_install)

    actor = SimpleNamespace(id="admin", role="system_admin", department="EC", can_view_all=True)
    results = await sync_service.push_skill_to_targets(
        "skill-selected-analysis",
        target_instance_ids=["analysis-only"],
        actor=actor,
    )

    assert len(results) == 1
    assert results[0]["instance_id"] == "analysis-only"
    assert results[0]["ok"] is False
    assert results[0]["runtime_eligible"] is False
    assert results[0]["error"] == "AGENT_PURPOSE_NOT_SKILL_RUNTIME"
    assert results[0]["reload"]["reason"] == "agent_purpose_not_skill_runtime"


@pytest.mark.asyncio
async def test_department_without_terminal_falls_back_to_platform_default(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill

    skill_root = tmp_path / "skill-no-terminal"
    _write_basic_skill(skill_root, "No Terminal Skill")
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(Skill(id="skill-no-terminal", name="No Terminal Skill", department="EC", status="active", owner="ec-user"))
        session.add(OpenClawInstance(
            id="platform-aiclaw",
            name="平台 AIClaw",
            department="AI",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
            is_platform_default=True,
        ))
        await session.commit()

    calls: list[str] = []
    installed_files = _install_store()

    async def fake_install(self, skill_id, files, target_dir=None):
        calls.append(self.instance_id)
        _remember_install(installed_files, self.instance_id, files)
        return {"ok": True, "count": len(files)}

    async def fake_reload(self):
        return {"ok": True}

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", _read_installed_skill(installed_files))
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="ec-user", role="aibp", department="EC", can_view_all=False)
    results = await sync_service.push_skill_to_targets("skill-no-terminal", actor=actor)

    assert calls == ["platform-aiclaw"]
    assert results[0]["instance_id"] == "platform-aiclaw"
    assert results[0]["ok"] is True
    assert results[0]["fallback_target"] is True
    assert results[0]["target_scope"] == "platform_default"
    assert "部门 EC 没有可用 Agent 终端" in results[0]["fallback_reason"]


@pytest.mark.asyncio
async def test_platform_default_fallback_can_be_disabled(client, monkeypatch, tmp_path):
    from app.common.models import SystemConfig
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill

    skill_root = tmp_path / "skill-no-fallback"
    _write_basic_skill(skill_root, "No Fallback Skill")
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(SystemConfig(key="security.platform_node_fallback_enabled", value=False, updated_by="admin"))
        session.add(Skill(id="skill-no-fallback", name="No Fallback Skill", department="EC", status="active", owner="ec-user"))
        session.add(OpenClawInstance(
            id="platform-aiclaw-disabled",
            name="平台 AIClaw",
            department="AI",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
            is_platform_default=True,
        ))
        await session.commit()

    calls: list[str] = []

    async def fake_install(self, skill_id, files, target_dir=None):
        calls.append(self.instance_id)
        return {"ok": True, "count": len(files)}

    async def fake_reload(self):
        return {"ok": True}

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="ec-user", role="aibp", department="EC", can_view_all=False)
    results = await sync_service.push_skill_to_targets("skill-no-fallback", actor=actor)

    assert calls == []
    assert results == []


@pytest.mark.asyncio
async def test_list_sync_targets_marks_platform_fallback_when_department_empty(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill

    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(Skill(id="skill-targets", name="Targets Skill", department="EC", status="active", owner="ec-user"))
        session.add(OpenClawInstance(
            id="platform-aiclaw",
            name="平台 AIClaw",
            department="AI",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
            is_platform_default=True,
        ))
        await session.commit()

    actor = SimpleNamespace(id="ec-user", role="aibp", department="EC", can_view_all=False)
    targets = await sync_service.list_sync_targets("skill-targets", actor=actor)

    assert len(targets) == 1
    assert targets[0]["id"] == "platform-aiclaw"
    assert targets[0]["auto_target"] is True
    assert targets[0]["selectable"] is True
    assert targets[0]["fallback_target"] is True
    assert targets[0]["target_scope"] == "platform_default"


@pytest.mark.asyncio
async def test_list_sync_targets_hides_platform_fallback_when_disabled(client, monkeypatch, tmp_path):
    from app.common.models import SystemConfig
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill

    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(SystemConfig(key="security.platform_node_fallback_enabled", value=False, updated_by="admin"))
        session.add(Skill(id="skill-targets-no-fallback", name="Targets Skill", department="EC", status="active", owner="ec-user"))
        session.add(OpenClawInstance(
            id="platform-aiclaw-hidden",
            name="平台 AIClaw",
            department="AI",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
            is_platform_default=True,
        ))
        await session.commit()

    actor = SimpleNamespace(id="ec-user", role="aibp", department="EC", can_view_all=False)
    targets = await sync_service.list_sync_targets("skill-targets-no-fallback", actor=actor)

    assert len(targets) == 1
    assert targets[0]["id"] == "platform-aiclaw-hidden"
    assert targets[0]["auto_target"] is False
    assert targets[0]["selectable"] is False
    assert targets[0]["fallback_target"] is False
    assert targets[0]["target_scope"] is None


@pytest.mark.asyncio
async def test_admin_can_sync_skill_to_any_selected_instance(client, monkeypatch, tmp_path):
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill
    from app.config import settings

    skill_root = tmp_path / "skill-ec"
    _write_basic_skill(skill_root, "EC Skill")
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(Skill(id="skill-ec", name="EC Skill", department="EC", status="active", owner="ec-user"))
        session.add(OpenClawInstance(
            id="ai-openclaw",
            name="AI OpenClaw",
            department="AI",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
        ))
        await session.commit()

    calls: list[str] = []
    installed_files = _install_store()

    async def fake_install(self, skill_id, files, target_dir=None):
        calls.append(self.instance_id)
        _remember_install(installed_files, self.instance_id, files)
        return {"path": f"/skills/{skill_id}", "files": [f["path"] for f in files], "count": len(files)}

    async def fake_reload(self):
        return {"ok": True}

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", _read_installed_skill(installed_files))
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="admin", role="system_admin", department="EC", can_view_all=True)
    results = await sync_service.push_skill_to_targets(
        "skill-ec",
        target_instance_ids=["ai-openclaw"],
        actor=actor,
    )

    assert calls == ["ai-openclaw"]
    assert results[0]["instance_id"] == "ai-openclaw"
    assert results[0]["ok"] is True


@pytest.mark.asyncio
async def test_aiclaw_sync_allows_legacy_nodes_without_reload_rpc(client, monkeypatch, tmp_path):
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill
    from app.config import settings

    skill_root = tmp_path / "skill-ec"
    _write_basic_skill(skill_root, "EC Skill")
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(Skill(id="skill-ec", name="EC Skill", department="EC", status="active", owner="ec-user"))
        session.add(OpenClawInstance(
            id="ec-openclaw",
            name="EC OpenClaw",
            department="EC",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
        ))
        await session.commit()

    installed_files = _install_store()

    async def fake_install(self, skill_id, files, target_dir=None):
        _remember_install(installed_files, self.instance_id, files)
        return {"path": f"/skills/{skill_id}", "count": len(files)}

    async def fake_reload(self):
        return {"ok": False, "status": "unsupported", "error": "unknown method skills.reload"}

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", _read_installed_skill(installed_files))
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="ec-user", role="aibp", department="EC", can_view_all=False)
    results = await sync_service.push_skill_to_targets("skill-ec", actor=actor)

    assert results[0]["instance_id"] == "ec-openclaw"
    assert results[0]["name"] == "EC OpenClaw"
    assert results[0]["ok"] is True


@pytest.mark.asyncio
async def test_sync_hybrid_skill_registers_agent_runtime(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill

    skill_root = tmp_path / "skill-agent"
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\n"
        "name: Agent Skill\n"
        "runtime:\n"
        "  backend: hybrid\n"
        "  fallback: bridge_script\n"
        "  entry: SKILL.md\n"
        "  script_entry: scripts/main.py\n"
        "  timeout: 900\n"
        "  tools:\n"
        "    - browser\n"
        "---\n",
        encoding="utf-8",
    )
    (skill_root / "scripts" / "main.py").write_text("def main(payload):\n    return payload\n", encoding="utf-8")
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(Skill(id="skill-agent", name="Agent Skill", department="EC", status="active", owner="ec-user"))
        session.add(OpenClawInstance(
            id="ec-openclaw",
            name="EC OpenClaw",
            department="EC",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
        ))
        await session.commit()

    registrations: list[dict] = []
    installed_files = _install_store()

    async def fake_install(self, skill_id, files, target_dir=None):
        _remember_install(installed_files, self.instance_id, files)
        return {"path": f"/skills/{skill_id}", "count": len(files)}

    async def fake_register(self, skill_id, *, runtime=None, target_dir=None, timeout=30):
        registrations.append({
            "instance_id": self.instance_id,
            "skill_id": skill_id,
            "runtime": runtime,
        })
        return {"ok": True, "registered": True}

    async def fake_reload(self):
        return {"ok": True}

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", _read_installed_skill(installed_files))
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.register_agent_skill", fake_register)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="admin", role="system_admin", department="EC", can_view_all=True)
    results = await sync_service.push_skill_to_targets("skill-agent", actor=actor)

    assert results[0]["ok"] is True
    assert registrations == [
        {
            "instance_id": "ec-openclaw",
            "skill_id": "skill-agent",
            "runtime": {
                "backend": "hybrid",
                "fallback": "bridge_script",
                "entry": "SKILL.md",
                "script_entry": "scripts/main.py",
                "timeout": 900,
                "tools": ["browser"],
                "output_schema": "contract.json",
                "policy_pack": "policy_pack.yaml",
            },
        }
    ]
    assert results[0]["result"]["register"]["ok"] is True


@pytest.mark.asyncio
async def test_sync_skill_bundles_runtime_sdk_when_skill_uses_it(client, monkeypatch, tmp_path):
    from app.auth.models import User
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill

    skill_root = tmp_path / "skill-sdk"
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("---\nname: SDK Skill\n---\n", encoding="utf-8")
    (skill_root / "scripts" / "main.py").write_text(
        "from skillforge_sdk import SkillForge\n"
        "sf = SkillForge('skill-sdk')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(User(id="sdk-user", username="sdk-user", name="SDK", role="aibp", department="EC", is_active=True))
        session.add(Skill(id="skill-sdk", name="SDK Skill", department="EC", status="active", owner="sdk-user"))
        session.add(OpenClawInstance(
            id="sdk-openclaw",
            name="SDK OpenClaw",
            department="EC",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
        ))
        await session.commit()

    captured_paths: list[str] = []
    captured_shared_paths: list[str] = []
    installed_files = _install_store()

    async def fake_install(self, skill_id, files, target_dir=None, shared_files=None):
        captured_paths.extend(f["path"] for f in files)
        captured_shared_paths.extend(f["path"] for f in (shared_files or []))
        _remember_install(installed_files, self.instance_id, files)
        return {
            "path": f"/skills/{skill_id}",
            "files": [f["path"] for f in files],
            "shared_files": [f["path"] for f in (shared_files or [])],
            "count": len(files),
        }

    async def fake_reload(self):
        return {"ok": True}

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", _read_installed_skill(installed_files))
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="sdk-user", role="aibp", department="EC", can_view_all=False)
    results = await sync_service.push_skill_to_targets("skill-sdk", actor=actor)

    assert results[0]["ok"] is True
    assert "scripts/main.py" in captured_paths
    assert "scripts/skillforge_sdk.py" in captured_paths
    assert results[0]["error"] is None


@pytest.mark.asyncio
async def test_sync_skill_replaces_stale_bundled_runtime_sdk(client, monkeypatch, tmp_path):
    from app.auth.models import User
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill

    skill_root = tmp_path / "skill-stale-sdk"
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("---\nname: Stale SDK Skill\n---\n", encoding="utf-8")
    (skill_root / "scripts" / "main.py").write_text(
        "from skillforge_sdk import SkillForge\n"
        "sf = SkillForge('skill-stale-sdk')\n",
        encoding="utf-8",
    )
    (skill_root / "scripts" / "skillforge_sdk.py").write_text(
        "STALE_NODE_SDK = True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(User(id="stale-sdk-user", username="stale-sdk-user", name="SDK", role="aibp", department="EC", is_active=True))
        session.add(Skill(id="skill-stale-sdk", name="Stale SDK Skill", department="EC", status="active", owner="stale-sdk-user"))
        session.add(OpenClawInstance(
            id="stale-sdk-openclaw",
            name="Stale SDK OpenClaw",
            department="EC",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
        ))
        await session.commit()

    captured_sdk_contents: list[str] = []
    installed_files = _install_store()

    async def fake_install(self, skill_id, files, target_dir=None, shared_files=None):
        captured_sdk_contents.extend(
            base64.b64decode(str(item.get("content_b64") or "")).decode("utf-8", "ignore")
            for item in files
            if item.get("path") == "scripts/skillforge_sdk.py"
        )
        _remember_install(installed_files, self.instance_id, files)
        return {"path": f"/skills/{skill_id}", "files": [f["path"] for f in files], "count": len(files)}

    async def fake_reload(self):
        return {"ok": True}

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", _read_installed_skill(installed_files))
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="stale-sdk-user", role="aibp", department="EC", can_view_all=False)
    results = await sync_service.push_skill_to_targets("skill-stale-sdk", actor=actor)

    assert results[0]["ok"] is True
    assert len(captured_sdk_contents) == 1
    assert "STALE_NODE_SDK" not in captured_sdk_contents[0]
    assert "SKILLFORGE_MCP_GATEWAY_URL" in captured_sdk_contents[0]
    assert "X-Run-Token" in captured_sdk_contents[0]


@pytest.mark.asyncio
async def test_sync_skill_bundles_tmall_mcp_helpers_when_skill_uses_mcp_urls(client, monkeypatch, tmp_path):
    from app.auth.models import User
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill

    skill_root = tmp_path / "skill-mcp"
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("---\nname: MCP Skill\n---\n", encoding="utf-8")
    (skill_root / "scripts" / "main.py").write_text(
        "from skillforge_sdk import SkillForge\n"
        "sf = SkillForge('skill-mcp')\n"
        "def main(payload):\n"
        "    return sf.fetch_api('mcp://tmall_sycm_item_rank_top', method='POST', body={'limit': 1})\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))

    async with async_session_factory() as session:
        session.add(User(id="mcp-user", username="mcp-user", name="MCP", role="aibp", department="EC", is_active=True))
        session.add(Skill(id="skill-mcp", name="MCP Skill", department="EC", status="active", owner="mcp-user"))
        session.add(OpenClawInstance(
            id="mcp-openclaw",
            name="MCP OpenClaw",
            department="EC",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
        ))
        await session.commit()

    captured_paths: list[str] = []
    captured_shared_paths: list[str] = []
    installed_files = _install_store()

    async def fake_install(self, skill_id, files, target_dir=None, shared_files=None):
        captured_paths.extend(f["path"] for f in files)
        captured_shared_paths.extend(f["path"] for f in (shared_files or []))
        _remember_install(installed_files, self.instance_id, files)
        return {
            "path": f"/skills/{skill_id}",
            "files": [f["path"] for f in files],
            "shared_files": [f["path"] for f in (shared_files or [])],
            "count": len(files),
        }

    async def fake_reload(self):
        return {"ok": True}

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", _read_installed_skill(installed_files))
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="mcp-user", role="aibp", department="EC", can_view_all=False)
    results = await sync_service.push_skill_to_targets("skill-mcp", actor=actor)

    assert results[0]["ok"] is True
    assert "scripts/skillforge_sdk.py" in captured_paths
    assert "scripts/tmall_mcp_server.py" not in captured_paths
    assert "scripts/browser_mcp_server.py" not in captured_paths
    assert "scripts/tmall_item_reviews_probe.py" not in captured_paths
    assert "scripts/mcp_base.py" in captured_shared_paths
    assert "scripts/skillforge_mcp_runtime.py" in captured_shared_paths
    assert "scripts/collection_client.py" in captured_shared_paths
    assert "scripts/tmall_mcp_server.py" in captured_shared_paths
    assert "scripts/browser_mcp_server.py" in captured_shared_paths
    assert "scripts/tmall_item_reviews_probe.py" in captured_shared_paths


def test_reload_skipped_is_not_counted_as_sync_success():
    from app.execution.sync_service import SkillSyncService

    result = SkillSyncService._normalize_reload_result({"ok": True, "skipped": True})

    assert result["ok"] is False
    assert result["status"] == "skipped"


def test_openclaw_final_json_is_parsed_for_reports_and_todos():
    from app.execution.openclaw_client import OpenClawClient

    parsed = OpenClawClient._parse_final_output(
        """```json
{"reports":[{"title":"日报","summary":"已生成"}],"todos":[{"kind":"review","title":"确认","reviewers":["u1"]}]}
```"""
    )

    assert parsed["reports"][0]["title"] == "日报"
    assert parsed["todos"][0]["title"] == "确认"


@pytest.mark.asyncio
async def test_sync_after_approval_creates_job_and_attempt_records(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance, SkillSyncAttempt, SkillSyncJob
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill
    from sqlalchemy import select

    skill_root = tmp_path / "skill-job"
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("---\nname: Job Skill\n---\n", encoding="utf-8")
    (skill_root / "scripts" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Tester"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "skill-job"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "-C", str(tmp_path), "tag", "skill-job/v0.1"], check=True)
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))
    monkeypatch.setattr(sync_service, "_push_canonical_git", AsyncMock(return_value=True))
    monkeypatch.setattr(sync_service, "_git_push", AsyncMock(return_value=False))
    monkeypatch.setattr(sync_service, "_alert_sync_failure", AsyncMock())

    async with async_session_factory() as session:
        session.add(Skill(id="skill-job", name="Job Skill", department="EC", status="active", owner="ec-user"))
        session.add(OpenClawInstance(
            id="ec-openclaw",
            name="EC OpenClaw",
            department="EC",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
        ))
        await session.commit()

    async def fake_install(self, skill_id, files, target_dir=None):
        return {"ok": True, "count": len(files)}

    async def fake_reload(self):
        return {"ok": True}

    async def fake_read_skill_from_device(self, skill_id, target_dir=None):
        return {
            "files": [{
                "path": "scripts/main.py",
                "content_b64": base64.b64encode(b"print('ok')\n").decode("ascii"),
            }]
        }

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", fake_read_skill_from_device)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="reviewer", role="system_admin", department="EC", can_view_all=True)
    result = await sync_service.sync_after_approval(
        "skill-job",
        version_tag="skill-job/v0.1",
        review_id=101,
        actor=actor,
    )

    assert result["all_ok"] is True
    assert result["job_id"]
    assert result["instances"][0]["attempt_id"]
    assert result["instances"][0]["git_commit_full"]
    assert result["instances"][0]["git_commit_full"] == subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "skill-job/v0.1"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()

    async with async_session_factory() as session:
        job = await session.get(SkillSyncJob, result["job_id"])
        attempts = (
            await session.execute(select(SkillSyncAttempt).where(SkillSyncAttempt.job_id == result["job_id"]))
        ).scalars().all()

    assert job.skill_id == "skill-job"
    assert job.version_tag == "skill-job/v0.1"
    assert job.review_id == 101
    assert job.status == "succeeded"
    assert job.push_ok is True
    assert job.trigger == "approval"
    assert job.actor == "reviewer"
    assert job.attempt_count == 1
    assert len(attempts) == 1
    assert attempts[0].instance_id == "ec-openclaw"
    assert attempts[0].status == "succeeded"

    detail = await sync_service.get_recent_sync_status(skill_id="skill-job", review_id=101)
    assert detail["items"][0]["job_id"] == result["job_id"]
    assert detail["items"][0]["attempts"][0]["attempt_id"] == result["instances"][0]["attempt_id"]
    sync_service._push_canonical_git.assert_awaited_once()
    sync_service._git_push.assert_awaited_once()
    sync_service._alert_sync_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_failed_sync_job_records_new_attempt(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance, SkillSyncAttempt, SkillSyncJob
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill
    from sqlalchemy import select

    skill_root = tmp_path / "skill-retry"
    _write_basic_skill(skill_root, "Retry Skill")
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))
    monkeypatch.setattr(sync_service, "_alert_sync_failure", AsyncMock())

    async with async_session_factory() as session:
        session.add(Skill(id="skill-retry", name="Retry Skill", department="EC", status="active", owner="ec-user"))
        session.add(OpenClawInstance(
            id="ec-openclaw",
            name="EC OpenClaw",
            department="EC",
            gateway_url="ws://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            is_active=True,
        ))
        await session.commit()

    installed_files = _install_store()

    async def fake_install(self, skill_id, files, target_dir=None):
        _remember_install(installed_files, self.instance_id, files)
        return {"ok": True, "count": len(files)}

    reload_results = [{"ok": False, "status": "failed", "error": "offline"}, {"ok": True}]

    async def fake_reload(self):
        return reload_results.pop(0)

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", _read_installed_skill(installed_files))
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload)

    actor = SimpleNamespace(id="admin", role="system_admin", department="EC", can_view_all=True)
    first = await sync_service.create_and_run_sync_job(
        "skill-retry",
        trigger="manual",
        actor=actor,
        target_instance_ids=["ec-openclaw"],
        push_git=False,
    )

    assert first["all_ok"] is False
    assert first["instances"][0]["attempt_id"]

    retry = await sync_service.retry_sync_job(first["job_id"], actor=actor)

    assert retry["all_ok"] is True
    assert retry["instances"][0]["attempt_id"] != first["instances"][0]["attempt_id"]

    async with async_session_factory() as session:
        job = await session.get(SkillSyncJob, first["job_id"])
        attempts = (
            await session.execute(
                select(SkillSyncAttempt)
                .where(SkillSyncAttempt.job_id == first["job_id"])
                .order_by(SkillSyncAttempt.id.asc())
            )
        ).scalars().all()

    assert job.status == "succeeded"
    assert job.attempt_count == 2
    assert [item.status for item in attempts] == ["failed", "succeeded"]


@pytest.mark.asyncio
async def test_git_push_uses_configured_fallback_remote_and_canonical_branch(monkeypatch):
    from app.config import settings
    from app.execution.sync_service import SkillSyncService

    calls: list[dict] = []

    class Result:
        def __init__(self, *, returncode=0, stdout="", stderr=b""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, cwd=None, capture_output=False, timeout=None, text=False, check=False):
        calls.append({
            "cmd": cmd,
            "cwd": cwd,
            "capture_output": capture_output,
            "timeout": timeout,
            "text": text,
            "check": check,
        })
        if cmd == ["git", "remote", "get-url", "origin"]:
            return Result(stdout="")
        if cmd == ["git", "push", "http://localhost/fake.git", "HEAD:refs/heads/main"]:
            return Result(returncode=0, stderr=b"")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(settings, "SKILL_REPO_REMOTE", "http://localhost/fake.git")
    monkeypatch.setattr(settings, "SKILL_REPO_BRANCH", "main")
    monkeypatch.setattr("app.execution.sync_service.subprocess.run", fake_run)
    monkeypatch.setattr(SkillSyncService, "_skill_git_config", AsyncMock(return_value={}))

    ok = await SkillSyncService()._git_push()

    assert ok is True
    assert calls[0]["cmd"] == ["git", "remote", "get-url", "origin"]
    assert calls[1]["cmd"] == ["git", "push", "http://localhost/fake.git", "HEAD:refs/heads/main"]


@pytest.mark.asyncio
async def test_git_push_uses_admin_skill_git_config_credentials(monkeypatch):
    from app.config import settings
    from app.execution.sync_service import SkillSyncService

    calls: list[list[str]] = []

    class Result:
        def __init__(self, *, returncode=0, stdout="", stderr=b""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, cwd=None, capture_output=False, timeout=None, text=False, check=False):
        calls.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return Result(stdout="")
        if cmd == [
            "git",
            "push",
            "http://skillforge:Skillforge%402026@localhost:3000/skillforge/skills-repo.git",
            "HEAD:refs/heads/main",
        ]:
            return Result(returncode=0, stderr=b"")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(settings, "SKILL_REPO_REMOTE", "")
    monkeypatch.setattr(settings, "SKILL_REPO_BRANCH", "main")
    monkeypatch.setattr("app.execution.sync_service.subprocess.run", fake_run)
    monkeypatch.setattr(
        SkillSyncService,
        "_skill_git_config",
        AsyncMock(return_value={
            "local_remote_url": "http://localhost:3000/skillforge/skills-repo.git",
            "username": "skillforge",
            "password": "Skillforge@2026",
        }),
    )

    ok = await SkillSyncService()._git_push()

    assert ok is True
    assert calls[1] == [
        "git",
        "push",
        "http://skillforge:Skillforge%402026@localhost:3000/skillforge/skills-repo.git",
        "HEAD:refs/heads/main",
    ]


@pytest.mark.asyncio
async def test_canonical_git_push_uses_single_mainline_remote(monkeypatch, tmp_path):
    from app.config import settings
    from app.execution.sync_service import SkillSyncService

    calls: list[dict] = []

    class Result:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(cmd, cwd=None, capture_output=False, timeout=None, env=None, **kwargs):
        calls.append({
            "cmd": cmd,
            "cwd": cwd,
            "capture_output": capture_output,
            "timeout": timeout,
            "env": env,
            **kwargs,
        })
        return Result()

    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "SKILL_REPO_BRANCH", "main")
    monkeypatch.setattr(settings, "SKILL_REPO_CANONICAL_REMOTE", "http://localhost:3000/skillforge/skills-repo.git")
    monkeypatch.setattr(settings, "SKILL_REPO_DOCKER_CONTAINER", "")
    monkeypatch.setattr(settings, "SKILL_REPO_DOCKER_BARE_REPO", "")
    monkeypatch.setattr("app.execution.sync_service.subprocess.run", fake_run)

    ok = await SkillSyncService()._push_canonical_git()

    assert ok is True
    assert calls[0]["cmd"] == [
        "git",
        "push",
        "http://localhost:3000/skillforge/skills-repo.git",
        "HEAD:refs/heads/main",
    ]
    assert calls[0]["env"].get("GIT_ALLOW_PROTOCOL") != "ext"


@pytest.mark.asyncio
async def test_git_push_fails_when_skill_repo_not_initialized(monkeypatch, tmp_path):
    from app.config import settings
    from app.execution.sync_service import SkillSyncService

    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))
    monkeypatch.setattr(SkillSyncService, "_skill_git_config", AsyncMock())

    ok = await SkillSyncService()._git_push()

    assert ok is False
    SkillSyncService._skill_git_config.assert_not_called()


@pytest.mark.asyncio
async def test_run_sync_job_reports_missing_skill_source(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import SkillSyncAttempt, SkillSyncJob
    from app.execution.sync_service import sync_service
    from sqlalchemy import select

    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))
    monkeypatch.setattr(sync_service, "_alert_sync_failure", AsyncMock())

    async def fail_install(*args, **kwargs):
        raise AssertionError("missing source must not call node install")

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fail_install)

    actor = SimpleNamespace(id="admin", role="system_admin", department="EC", can_view_all=True)
    job_id = await sync_service.create_sync_job(
        "missing-source-skill",
        trigger="manual",
        actor=actor,
        target_instance_ids=["ec-openclaw"],
    )

    result = await sync_service.run_sync_job(job_id, push_git=False, actor=actor)

    assert result["all_ok"] is False
    assert result["push_ok"] is True
    assert result["instances"][0]["error"] == "SKILL_SOURCE_NOT_FOUND"
    assert result["instances"][0]["reload"]["reason"] == "skill_source_not_found"
    sync_service._alert_sync_failure.assert_awaited_once()

    async with async_session_factory() as session:
        job = await session.get(SkillSyncJob, job_id)
        attempts = (
            await session.execute(select(SkillSyncAttempt).where(SkillSyncAttempt.job_id == job_id))
        ).scalars().all()

    assert job.status == "failed"
    assert job.error == "SKILL_SOURCE_NOT_FOUND"
    assert job.attempt_count == 1
    assert len(attempts) == 1
    assert attempts[0].instance_id is None
    assert attempts[0].error == "SKILL_SOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_run_sync_job_stops_when_canonical_git_push_fails(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import SkillSyncAttempt, SkillSyncJob
    from app.execution.sync_service import sync_service
    from app.skills.models import Skill
    from sqlalchemy import select

    skill_root = tmp_path / "skill-canonical"
    _write_basic_skill(skill_root, "Canonical Skill")
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))
    monkeypatch.setattr(sync_service, "_push_canonical_git", AsyncMock(return_value=False))
    monkeypatch.setattr(sync_service, "_git_push", AsyncMock())
    monkeypatch.setattr(sync_service, "_alert_sync_failure", AsyncMock())

    async with async_session_factory() as session:
        session.add(Skill(id="skill-canonical", name="Canonical Skill", department="EC", status="active", owner="ec-user"))
        await session.commit()

    actor = SimpleNamespace(id="admin", role="system_admin", department="EC", can_view_all=True)
    job_id = await sync_service.create_sync_job(
        "skill-canonical",
        trigger="manual",
        actor=actor,
        target_instance_ids=["ec-openclaw"],
    )
    result = await sync_service.run_sync_job(job_id, push_git=True, actor=actor)

    assert result["all_ok"] is False
    assert result["push_ok"] is False
    assert result["instances"][0]["error"] == "GIT_PUSH_FAILED"
    assert result["instances"][0]["reload"]["reason"] == "git_push_failed"
    sync_service._git_push.assert_not_called()

    async with async_session_factory() as session:
        job = await session.get(SkillSyncJob, job_id)
        attempts = (
            await session.execute(select(SkillSyncAttempt).where(SkillSyncAttempt.job_id == job_id))
        ).scalars().all()

    assert job.status == "failed"
    assert job.push_ok is False
    assert job.error == "GIT_PUSH_FAILED"
    assert len(attempts) == 1
    assert attempts[0].instance_id is None
    assert attempts[0].error == "GIT_PUSH_FAILED"


def test_sync_job_auto_retry_skip_reason_uses_missing_source(monkeypatch, tmp_path):
    from app.config import settings
    from app.execution.sync_service import sync_service

    monkeypatch.setattr(settings, "SKILL_REPO_PATH", str(tmp_path))
    job = SimpleNamespace(skill_id="missing-source-skill", error="NO_SYNC_TARGETS", attempt_count=6000)

    assert sync_service.sync_job_auto_retry_skip_reason(job) == "SKILL_SOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_alert_sync_failure_extracts_nested_instance_error(monkeypatch):
    from app.execution.sync_service import SkillSyncService

    captured = {}

    async def fake_enqueue(payload, **kwargs):
        captured["payload"] = payload
        captured["kwargs"] = kwargs

    monkeypatch.setattr("app.execution.sync_service.enqueue_admin_work_notice", fake_enqueue)

    await SkillSyncService()._alert_sync_failure(
        "skill-x",
        {"instances": [{"instance_id": "node-a", "error": "SKILL_SOURCE_NOT_FOUND"}]},
    )

    markdown = captured["payload"]["markdown"]
    assert "- 错误: SKILL_SOURCE_NOT_FOUND" in markdown
    assert "- 目标: node-a，错误: SKILL_SOURCE_NOT_FOUND" in markdown
    assert captured["kwargs"]["related_type"] == "skill_sync"


@pytest.mark.asyncio
async def test_alert_sync_failure_labels_node_url_errors_without_git_confusion(monkeypatch):
    from app.execution.sync_service import SkillSyncService

    captured = {}

    async def fake_enqueue(payload, **kwargs):
        captured["payload"] = payload
        captured["kwargs"] = kwargs

    monkeypatch.setattr("app.execution.sync_service.enqueue_admin_work_notice", fake_enqueue)

    await SkillSyncService()._alert_sync_failure(
        "playbook:replenishment-check",
        {
            "push_ok": True,
            "instances": [
                {
                    "instance": "openclaw-ec",
                    "ok": False,
                    "error": "Request URL is missing an 'http://' or 'https://' protocol.",
                }
            ],
        },
    )

    markdown = captured["payload"]["markdown"]
    assert "- 错误: Request URL is missing an 'http://' or 'https://' protocol." in markdown
    assert "- 目标: openclaw-ec，错误: Request URL is missing" in markdown
    assert "当前错误来自节点同步/Reload 阶段" in markdown
    assert "Skill Git 推送失败" not in markdown


@pytest.mark.asyncio
async def test_sync_playbook_uses_canonical_push_and_mirror_is_non_blocking(monkeypatch):
    from app.execution.sync_service import SkillSyncService

    service = SkillSyncService()
    alerts: list[tuple[str, dict]] = []

    async def fake_alert(skill_id, detail):
        alerts.append((skill_id, detail))

    monkeypatch.setattr(service, "_push_canonical_git", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_git_push", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_notify_reload", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(service, "_notify_reload_all_instances", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_alert_sync_failure", fake_alert)

    result = await service.sync_playbook_after_approval("replenishment-check")

    assert result["all_ok"] is True
    assert result["push_ok"] is True
    assert result["mirror_push_ok"] is False
    service._push_canonical_git.assert_awaited_once()
    service._git_push.assert_awaited_once()
    assert alerts == [
        (
            "playbook:replenishment-check",
            {
                "mirror_push_ok": False,
                "push_ok": True,
                "error": "SKILL_GIT_MIRROR_PUSH_FAILED",
            },
        )
    ]


@pytest.mark.asyncio
async def test_sync_playbook_skips_reload_when_canonical_push_fails(monkeypatch):
    from app.execution.sync_service import SkillSyncService

    service = SkillSyncService()
    alerts: list[tuple[str, dict]] = []

    async def fake_alert(skill_id, detail):
        alerts.append((skill_id, detail))

    monkeypatch.setattr(service, "_push_canonical_git", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_git_push", AsyncMock())
    monkeypatch.setattr(service, "_notify_reload", AsyncMock())
    monkeypatch.setattr(service, "_notify_reload_all_instances", AsyncMock())
    monkeypatch.setattr(service, "_alert_sync_failure", fake_alert)

    result = await service.sync_playbook_after_approval("replenishment-check")

    assert result == {
        "all_ok": False,
        "push_ok": False,
        "mirror_push_ok": None,
        "reload": {"ok": False, "status": "skipped", "reason": "git_push_failed"},
        "instances": [],
    }
    service._push_canonical_git.assert_awaited_once()
    service._git_push.assert_not_awaited()
    service._notify_reload.assert_not_awaited()
    service._notify_reload_all_instances.assert_not_awaited()
    assert alerts == [
        (
            "playbook:replenishment-check",
            {
                "push_ok": False,
                "reload": {"ok": False, "status": "skipped", "reason": "git_push_failed"},
                "instances": [],
            },
        )
    ]
