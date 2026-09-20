import asyncio
import base64
import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from app.common.exceptions import AppError
from app.codex import cloud_video
from app.codex import plugin_bundle
from app.codex import service as codex_service
from app.auth.dependencies import create_session_token
from app.common.audit import AuditLog
from app.codex.models import CodexCliSession, CodexMcpCallAudit, CodexOutputPreview, CodexSkillSubmission
from app.dingtalk.models import DingTalkOutbox
from app.execution.execution_service import issue_run_token, verify_run_token
from app.execution.models import DecisionLog, ExecutionArtifact, ExecutionRun, ExecutionRunLog, ExecutionStep
from app.portal.market_models import MarketRating
from app.org.models import OrgUnit, UserOrgMembership
from app.projects.models import Project, ProjectRun, ProjectRunAsset
from app.reviews.models import Review
from app.skills.core.access import SkillMember
from app.skills.core.models import Skill
from app.skill_runtime_sdk.skillforge_sdk import SkillForge
from scripts import sf as sf_cli


def _tar_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _valid_contract_bytes() -> bytes:
    return json.dumps(
        {
            "output_schema": {
                "type": "object",
                "required": ["summary"],
                "properties": {
                    "summary": {"type": "string"},
                },
            },
            "output_table": [{"name": "summary", "type": "string"}],
        },
        ensure_ascii=False,
    ).encode("utf-8")


def test_codex_redirect_uri_is_localhost_only():
    assert codex_service.validate_redirect_uri("http://127.0.0.1:49152/callback")
    assert codex_service.validate_redirect_uri("urn:skillforge:codex:poll")
    with pytest.raises(AppError):
        codex_service.validate_redirect_uri("https://example.com/callback")
    with pytest.raises(AppError):
        codex_service.validate_redirect_uri("http://127.0.0.1/callback")


def test_sf_media_bootstrap_cli_uses_fixed_profile_and_bandwidth(monkeypatch, capsys):
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json_body")))
        return {
            "status": "downloading",
            "profile": "h3_all_modes_v1",
            "progress_percent": 10,
            "downloaded_bytes": 1024,
            "total_bytes": 10240,
            "speed_bytes_per_second": 1024,
            "eta_seconds": 9,
        }

    monkeypatch.setattr(sf_cli, "api_request", fake_api_request)
    args = sf_cli.build_parser().parse_args([
        "media", "bootstrap", "pro-6000", "--accept-license",
    ])
    args.func(args)

    assert calls == [(
        "POST",
        "/api/aiclaw/instances/pro-6000/media/bootstrap",
        {
            "profile": "h3_all_modes_v1",
            "bandwidth_limit_mbps": 3,
            "accept_license": True,
            "force": False,
            "dry_run": False,
        },
    )]
    assert "进度: 10%" in capsys.readouterr().out


def test_sf_media_bootstrap_cli_requires_license_for_real_install():
    args = sf_cli.build_parser().parse_args(["media", "bootstrap", "pro-6000"])
    with pytest.raises(RuntimeError, match="--accept-license"):
        args.func(args)


def test_sf_media_refresh_bridge_uses_controlled_platform_endpoint(monkeypatch, capsys):
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json_body")))
        return {"status": "sent", "instance_id": "pro-6000"}

    monkeypatch.setattr(sf_cli, "api_request", fake_api_request)
    args = sf_cli.build_parser().parse_args(["media", "refresh-bridge", "pro-6000"])
    args.func(args)

    assert calls == [("POST", "/api/aiclaw/instances/pro-6000/force-update", {})]
    assert "Bridge 更新指令已下发" in capsys.readouterr().out


def test_codex_package_validation_rejects_secret_and_cleans_tmp():
    package = _tar_bytes(
        {
            "SKILL.md": b"---\nname: demo\ndescription: demo\ntrigger_type: manual\nrisk_level: R1\n---\n# Demo\n",
            "scripts/main.py": b"Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        }
    )
    with pytest.raises(AppError):
        codex_service.extract_and_validate_package(package)


def test_codex_package_allows_prompts_for_platform_intelligence():
    package = _tar_bytes(
        {
            "SKILL.md": b"---\nname: demo\ndescription: demo\ntrigger_type: manual\nrisk_level: R1\n---\n# Demo\n",
            "prompts/analysis_v1.md": b"Return JSON only.",
        }
    )

    extract_dir, files, checks = codex_service.extract_and_validate_package(package)
    try:
        assert ("prompts/analysis_v1.md", b"Return JSON only.") in files
        assert "prompts/analysis_v1.md" in checks["paths"]
    finally:
        import shutil

        shutil.rmtree(extract_dir.parent, ignore_errors=True)


def test_codex_contract_validation_rejects_missing_contract():
    with pytest.raises(AppError) as exc:
        codex_service.validate_package_contract([
            ("SKILL.md", b"---\nname: demo\n---\n# Demo\n"),
            ("scripts/main.py", b"print('{}')\n"),
        ])

    assert exc.value.code == "SKILL_VALIDATION_FAILED"
    assert "contract.json" in json.dumps(exc.value.detail, ensure_ascii=False)


def test_codex_contract_validation_rejects_misaligned_output_table():
    report = codex_service.validate_package_contract([
        (
            "SKILL.md",
            b"---\nname: demo\n---\n# Demo\n\n## \xe8\xbe\x93\xe5\x87\xba\xe5\xae\x9a\xe4\xb9\x89\n\n- summary: ok\n",
        ),
        (
            "contract.json",
            json.dumps({
                "output_schema": {
                    "type": "object",
                    "required": ["summary", "score"],
                    "properties": {
                        "summary": {"type": "string"},
                        "score": {"type": "number"},
                    },
                },
                "output_table": [{"name": "summary"}],
            }).encode("utf-8"),
        ),
    ])

    assert report["ok"] is False
    assert any("score" in item for item in report["errors"])


def test_codex_package_hash_matches_cli():
    files = [("SKILL.md", b"one"), ("scripts/main.py", b"two")]
    assert codex_service.normalized_package_hash(files) == sf_cli.package_hash(files)


def test_codex_skill_pull_package_uses_whitelist_and_base_commit(monkeypatch, tmp_path):
    skill_root = tmp_path / "demo-skill"
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: demo-skill\ndepartment: EC\n---\n# Demo\n",
        encoding="utf-8",
    )
    (skill_root / "scripts" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (skill_root / ".env").write_text("API_KEY=sk-should-not-export\n", encoding="utf-8")
    (skill_root / "notes.txt").write_text("not allowed\n", encoding="utf-8")

    class FakeGit:
        def skill_exists(self, skill_id):
            return skill_id == "demo-skill"

        def skill_dir(self, skill_id):
            return skill_root

        def log(self, skill_id, max_count=1):
            return [{"hash_full": "a" * 40}]

    monkeypatch.setattr(codex_service, "git_service", FakeGit())
    package, digest, manifest = codex_service.build_skill_pull_package(
        SimpleNamespace(id="demo-skill", name="Demo", department="EC", git_commit="b" * 40)
    )

    assert digest == manifest["package_hash"]
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
        names = set(tar.getnames())
        metadata = yaml.safe_load(tar.extractfile("skillforge.yaml").read().decode("utf-8"))

    assert names == {"SKILL.md", "scripts/main.py", "skillforge.yaml"}
    assert metadata["skill_id"] == "demo-skill"
    assert metadata["base_commit"] == "a" * 40
    assert ".env" not in names
    assert "notes.txt" not in names


def test_sf_skill_pull_extracts_and_verifies_package(monkeypatch, tmp_path, capsys):
    files = [
        ("SKILL.md", b"---\nname: demo-skill\n---\n# Demo\n"),
        ("scripts/main.py", b"print('ok')\n"),
        ("skillforge.yaml", b"skill_id: demo-skill\nbase_commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"),
    ]
    package = _tar_bytes(dict(files))
    expected_hash = sf_cli.package_hash(files)

    monkeypatch.setattr(
        sf_cli,
        "api_download",
        lambda path: (package, {
            "x-skillforge-package-hash": expected_hash,
            "x-skillforge-remote-head": "a" * 40,
        }),
    )

    sf_cli.skill_pull(SimpleNamespace(skill_id="demo-skill", path=str(tmp_path), force=False))
    out = json.loads(capsys.readouterr().out)

    target = tmp_path / "demo-skill"
    assert out["skill_id"] == "demo-skill"
    assert out["path"] == str(target)
    assert out["package_hash"] == expected_hash
    assert (target / "SKILL.md").exists()
    assert (target / "scripts" / "main.py").exists()


def test_sf_skill_pull_rejects_hash_mismatch_before_write(monkeypatch, tmp_path):
    package = _tar_bytes({
        "SKILL.md": b"---\nname: demo-skill\n---\n# Demo\n",
        "skillforge.yaml": b"skill_id: demo-skill\n",
    })
    monkeypatch.setattr(
        sf_cli,
        "api_download",
        lambda path: (package, {"x-skillforge-package-hash": "sha256:" + "0" * 64}),
    )

    with pytest.raises(SystemExit, match="package_hash"):
        sf_cli.skill_pull(SimpleNamespace(skill_id="demo-skill", path=str(tmp_path), force=False))

    assert not (tmp_path / "demo-skill").exists()


def test_sf_skill_pull_rejects_mismatched_package_skill_id_before_write(monkeypatch, tmp_path):
    package = _tar_bytes({
        "SKILL.md": b"---\nname: other-skill\n---\n# Demo\n",
        "skillforge.yaml": b"skill_id: other-skill\n",
    })
    expected_hash = sf_cli.package_hash([
        ("SKILL.md", b"---\nname: other-skill\n---\n# Demo\n"),
        ("skillforge.yaml", b"skill_id: other-skill\n"),
    ])
    monkeypatch.setattr(
        sf_cli,
        "api_download",
        lambda path: (package, {"x-skillforge-package-hash": expected_hash}),
    )

    with pytest.raises(SystemExit, match="元数据不匹配"):
        sf_cli.skill_pull(SimpleNamespace(skill_id="demo-skill", path=str(tmp_path), force=False))

    assert not (tmp_path / "demo-skill").exists()


def test_sf_skill_pull_refuses_to_overwrite_different_local_skill(monkeypatch, tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "SKILL.md").write_text("---\nname: other-skill\n---\n# Other\n", encoding="utf-8")
    (target / "skillforge.yaml").write_text("skill_id: other-skill\n", encoding="utf-8")

    package = _tar_bytes({
        "SKILL.md": b"---\nname: demo-skill\n---\n# Demo\n",
        "skillforge.yaml": b"skill_id: demo-skill\n",
    })
    expected_hash = sf_cli.package_hash([
        ("SKILL.md", b"---\nname: demo-skill\n---\n# Demo\n"),
        ("skillforge.yaml", b"skill_id: demo-skill\n"),
    ])
    monkeypatch.setattr(
        sf_cli,
        "api_download",
        lambda path: (package, {"x-skillforge-package-hash": expected_hash}),
    )

    with pytest.raises(SystemExit, match="已有不同 Skill"):
        sf_cli.skill_pull(SimpleNamespace(skill_id="demo-skill", path=str(target), force=True))

    assert "other-skill" in (target / "skillforge.yaml").read_text(encoding="utf-8")


def test_sf_skill_submit_runs_local_test_before_upload(monkeypatch, tmp_path):
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\n"
        "name: demo-submit-preflight\n"
        "description: demo\n"
        "trigger_type: manual\n"
        "risk_level: R1\n"
        "---\n"
        "# Demo\n",
        encoding="utf-8",
    )
    (root / "contract.json").write_bytes(_valid_contract_bytes())
    (root / "scripts" / "main.py").write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
    monkeypatch.setattr(
        sf_cli,
        "api_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("submit should not upload failed local test")),
    )

    with pytest.raises(SystemExit) as exc:
        sf_cli.skill_submit(SimpleNamespace(path=str(root), message="", skip_local_test=False))

    assert exc.value.code == 7


def test_sf_skill_submit_rejects_output_missing_required_before_upload(monkeypatch, tmp_path):
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\n"
        "name: demo-submit-schema\n"
        "description: demo\n"
        "trigger_type: manual\n"
        "risk_level: R1\n"
        "---\n"
        "# Demo\n",
        encoding="utf-8",
    )
    (root / "contract.json").write_bytes(_valid_contract_bytes())
    (root / "scripts" / "main.py").write_text("print('{\"other\":\"value\"}')\n", encoding="utf-8")
    monkeypatch.setattr(
        sf_cli,
        "api_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("submit should not upload schema-invalid output")),
    )

    with pytest.raises(SystemExit) as exc:
        sf_cli.skill_submit(SimpleNamespace(path=str(root), message="", skip_local_test=False))

    assert "main.py return 缺字段" in str(exc.value)


def test_sf_skill_submit_requires_force_reason(tmp_path):
    with pytest.raises(SystemExit) as exc:
        sf_cli.skill_submit(
            SimpleNamespace(
                path=str(tmp_path),
                message="",
                skip_local_test=True,
                force_submit=True,
                force_reason="",
            )
        )

    assert "--force-reason" in str(exc.value)


def test_sf_skill_submit_sends_force_fields_after_local_gate(monkeypatch, tmp_path, capsys):
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\n"
        "name: demo-force-submit\n"
        "description: demo\n"
        "trigger_type: manual\n"
        "risk_level: R1\n"
        "---\n"
        "# Demo\n",
        encoding="utf-8",
    )
    (root / "contract.json").write_bytes(_valid_contract_bytes())
    (root / "scripts" / "main.py").write_text("print('{\"summary\":\"ok\"}')\n", encoding="utf-8")
    calls = {}

    def fake_api_request(method, path, *, data=None, files=None, **kwargs):
        calls["method"] = method
        calls["path"] = path
        calls["data"] = data
        calls["files"] = files
        return {"status": "review_pending"}

    monkeypatch.setattr(sf_cli, "api_request", fake_api_request)

    sf_cli.skill_submit(
        SimpleNamespace(
            path=str(root),
            message="force submit",
            skip_local_test=False,
            force_submit=True,
            force_reason="确认强制提交：已知测试样例不足，进入人工审核",
        )
    )

    output = capsys.readouterr().out
    payload = json.loads(output[output.rfind("{\n  \"status\""):])
    assert payload["status"] == "review_pending"
    assert calls["path"] == "/api/codex/skills/submissions"
    assert calls["data"]["force_submit"] == "true"
    assert calls["data"]["force_reason"] == "确认强制提交：已知测试样例不足，进入人工审核"


def test_sf_install_skill_package_bytes_writes_workspace(tmp_path):
    package = _tar_bytes(
        {
            "SKILL.md": b"---\nname: install-demo\n---\n# Install Demo\n",
            "skillforge.yaml": b"skill_id: install-demo\nbase_commit: abc123\n",
            "scripts/main.py": b"print('{}')\n",
        }
    )
    target = tmp_path / "install-demo"

    files = sf_cli.install_skill_package_bytes(package, target, force=False)

    assert files == ["SKILL.md", "scripts/main.py", "skillforge.yaml"]
    assert (target / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    assert "base_commit: abc123" in (target / "skillforge.yaml").read_text(encoding="utf-8")


def test_sf_install_skill_package_rejects_unsafe_path(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        data = b"bad"
        info = tarfile.TarInfo("../bad.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with pytest.raises(SystemExit):
        sf_cli.install_skill_package_bytes(buffer.getvalue(), tmp_path / "bad", force=False)


def test_sf_skill_install_uses_parent_directory_for_path(monkeypatch, tmp_path):
    package = _tar_bytes(
        {
            "SKILL.md": b"---\nname: remote-skill\n---\n",
            "skillforge.yaml": b"skill_id: remote-skill\n",
        }
    )
    monkeypatch.setattr(
        sf_cli,
        "api_request_bytes",
        lambda method, path: (
            package,
            SimpleNamespace(headers={"X-SkillForge-Skill-Commit": "abc123"}),
        ),
    )
    output = []
    monkeypatch.setattr(sf_cli, "print_json", lambda data: output.append(data))

    sf_cli.skill_install(SimpleNamespace(skill_id="remote-skill", path=str(tmp_path / "skills"), force=False))

    installed = tmp_path / "skills" / "remote-skill"
    assert (installed / "SKILL.md").exists()
    assert output[0]["path"] == str(installed.resolve())


def test_codex_plugin_bundle_is_copyable_and_hashable():
    bundle = plugin_bundle.build_plugin_bundle()
    manifest = plugin_bundle.plugin_update_manifest()
    assert manifest["sha256"] == f"sha256:{plugin_bundle.sha256_hex(bundle)}"
    assert manifest["release_title"] == "通用 SF 数据写入与存储"
    assert manifest["release_notes"][0].startswith("新增通用 SF 数据库存储")
    assert [item["version"] for item in manifest["version_history"]] == [
        "0.3.7",
        "0.3.6",
        "0.3.5",
        "0.3.4",
        "0.3.3",
        "0.3.2",
        "0.3.1",
        "0.3.0",
        "0.2.9",
        "0.2.0",
        "0.1.9",
        "0.1.8",
        "0.1.7",
        "0.1.6",
        "0.1.5",
        "0.1.4",
        "0.1.3",
        "0.1.2",
    ]
    assert manifest["version_history"][0]["status"] == "current"
    assert manifest["version_history"][1]["status"] == "released"
    assert manifest["version_history"][12]["status"] == "internal"

    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
        names = set(tar.getnames())
        plugin_json = json.loads(tar.extractfile(f"{plugin_bundle.PLUGIN_NAME}/.codex-plugin/plugin.json").read())
        sf_command = tar.extractfile(f"{plugin_bundle.PLUGIN_NAME}/commands/sf.md").read().decode("utf-8")
        sf_skill = tar.extractfile(f"{plugin_bundle.PLUGIN_NAME}/skills/sf/SKILL.md").read().decode("utf-8")
        sf_script = tar.extractfile(f"{plugin_bundle.PLUGIN_NAME}/scripts/sf.py").read().decode("utf-8")

    assert f"{plugin_bundle.PLUGIN_NAME}/scripts/sf.py" in names
    assert f"{plugin_bundle.PLUGIN_NAME}/scripts/sf" in names
    assert f"{plugin_bundle.PLUGIN_NAME}/sdk/skillforge_sdk.py" in names
    assert f"{plugin_bundle.PLUGIN_NAME}/.mcp.json" in names
    assert plugin_json["version"] == plugin_bundle.PLUGIN_VERSION
    assert plugin_json["repository"].startswith("http://skillforge.example.com/")
    assert "/home/skillforge" not in json.dumps(plugin_json, ensure_ascii=False)
    assert f"{plugin_bundle.PLUGIN_NAME}/commands/sf.md" in names
    assert sf_command.startswith("---\ndescription:")
    assert "argument-hint:" in sf_command
    assert "allowed-tools:" in sf_command
    assert "# /sf" in sf_command
    assert "sf update" in sf_skill
    assert "sf help" in sf_command
    assert "我的插件" in sf_skill
    assert "scope department" in sf_skill
    assert "sf skill pull <skill_id> --path ." in sf_skill
    assert "sf skill sandbox --path ." in sf_skill
    assert "sf data latest tmall" in sf_skill
    assert "sf data write" in sf_skill
    assert "skillforge_sf_data_write" in sf_skill
    assert "sf project submit" in sf_skill
    assert "project asset upload" in sf_skill
    assert "sf training resources" in sf_skill
    assert "agent analysis create" in sf_skill
    assert "cloud-video-weekly-diagnosis" in sf_skill
    assert f'CLI_VERSION = "{plugin_bundle.PLUGIN_VERSION}"' in sf_script
    assert "sf preview output.json" in sf_skill
    assert "preview apply" in sf_skill
    assert "sf runs" in sf_skill
    assert "sf market" in sf_skill
    assert "def preview_output" in sf_script
    assert "def preview_apply" in sf_script
    assert "打开登录页:" in sf_script
    assert "flush=True" in sf_script


def test_sf_update_installs_bundle_and_shim(monkeypatch, tmp_path):
    bundle = plugin_bundle.build_plugin_bundle()
    manifest = {
        "plugin_update": {
            "name": plugin_bundle.PLUGIN_NAME,
            "latest_version": plugin_bundle.PLUGIN_VERSION,
            "bundle_url": "/bundle.tar.gz",
            "sha256": f"sha256:{plugin_bundle.sha256_hex(bundle)}",
            "format": "tar.gz",
        }
    }
    home = tmp_path / "home"
    target = home / "plugins" / plugin_bundle.PLUGIN_NAME

    class Response:
        status_code = 200
        content = bundle
        text = ""

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sf_cli, "CONFIG_DIR", home / ".skillforge")
    monkeypatch.setattr(sf_cli, "CONFIG_PATH", home / ".skillforge" / "codex-cli.json")
    monkeypatch.setattr(sf_cli, "api_request", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(sf_cli.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(sf_cli, "base_url", lambda: "http://skillforge.example.com")
    monkeypatch.setattr(sf_cli.shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None)

    codex_calls = []

    def fake_subprocess_run(cmd, **kwargs):
        codex_calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="Added plugin\n", stderr="")

    monkeypatch.setattr(sf_cli.subprocess, "run", fake_subprocess_run)

    sf_cli.sf_update(
        SimpleNamespace(
            check=False,
            force=False,
            target=str(target),
            install_sf_shim=True,
            no_install_sf_shim=False,
        )
    )

    plugin_json = json.loads((target / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin_json["name"] == plugin_bundle.PLUGIN_NAME
    assert (target / "scripts" / "sf.py").exists()
    assert (target / "sdk" / "skillforge_sdk.py").exists()
    assert (home / ".local" / "bin" / "sf").exists()
    marketplace = json.loads((home / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    assert marketplace["plugins"][0]["source"]["path"] == f"./plugins/{plugin_bundle.PLUGIN_NAME}"
    config = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert f'{plugin_bundle.PLUGIN_NAME}@skillforge-local' in config
    assert codex_calls == [
        (
            ["/usr/bin/codex", "plugin", "add", f"{plugin_bundle.PLUGIN_NAME}@skillforge-local"],
            {"check": False, "capture_output": True, "text": True, "timeout": 60},
        )
    ]


def test_sf_plugin_status_and_my_data(monkeypatch, tmp_path):
    home = tmp_path / "home"
    plugin_root = home / "plugins" / plugin_bundle.PLUGIN_NAME
    (plugin_root / ".codex-plugin").mkdir(parents=True)
    (plugin_root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": plugin_bundle.PLUGIN_NAME, "version": "0.1.2"}),
        encoding="utf-8",
    )
    (plugin_root / ".mcp.json").write_text("{}", encoding="utf-8")
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        f'[plugins."{plugin_bundle.PLUGIN_NAME}@skillforge-local"]\nenabled = true\n',
        encoding="utf-8",
    )
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "sf").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    manifest = {
        "plugin_update": {
            "name": plugin_bundle.PLUGIN_NAME,
            "latest_version": "0.1.5",
            "bundle_url": "/bundle.tar.gz",
            "sha256": "sha256:" + "a" * 64,
            "format": "tar.gz",
            "release_notes": ["修复登录链接输出", "提示可用更新"],
        }
    }
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path.startswith("/api/codex/catalog/manifest"):
            return manifest
        if path == "/api/codex/skills?scope=mine":
            return {"items": [{"skill_id": "my-skill"}]}
        raise AssertionError(path)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sf_cli, "CONFIG_DIR", home / ".skillforge")
    monkeypatch.setattr(sf_cli, "CONFIG_PATH", home / ".skillforge" / "codex-cli.json")
    monkeypatch.setattr(sf_cli, "api_request", fake_api_request)
    monkeypatch.setattr(sf_cli, "base_url", lambda: "http://skillforge.example.com")

    status = sf_cli.plugin_status_data(SimpleNamespace(target=str(plugin_root)))
    assert status["installed"] is True
    assert status["current_version"] == "0.1.2"
    assert status["latest_version"] == "0.1.5"
    assert status["update_available"] is True
    assert status["codex_plugin_enabled"] is True
    assert status["sf_shim_installed"] is True

    data = sf_cli.my_plugins_data(SimpleNamespace(target=str(plugin_root)))
    assert data["plugin"]["plugin"] == plugin_bundle.PLUGIN_NAME
    assert data["skills"] == [{"skill_id": "my-skill"}]
    assert ("GET", "/api/codex/skills?scope=mine", {}) in calls


def test_sf_update_check_includes_release_notes(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    plugin_root = home / "plugins" / plugin_bundle.PLUGIN_NAME
    (plugin_root / ".codex-plugin").mkdir(parents=True)
    (plugin_root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": plugin_bundle.PLUGIN_NAME, "version": "0.1.4"}),
        encoding="utf-8",
    )
    manifest = {
        "plugin_update": {
            "name": plugin_bundle.PLUGIN_NAME,
            "latest_version": "0.1.5",
            "bundle_url": "/bundle.tar.gz",
            "sha256": "sha256:" + "b" * 64,
            "format": "tar.gz",
            "release_notes": ["显示更新内容", "任意功能调用前提示新版本"],
        }
    }

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sf_cli, "CONFIG_DIR", home / ".skillforge")
    monkeypatch.setattr(sf_cli, "CONFIG_PATH", home / ".skillforge" / "codex-cli.json")
    monkeypatch.setattr(sf_cli, "api_request", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(sf_cli, "base_url", lambda: "http://skillforge.example.com")

    sf_cli.sf_update(
        SimpleNamespace(
            check=True,
            force=False,
            target=str(plugin_root),
            install_sf_shim=True,
            no_install_sf_shim=False,
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["update_available"] is True
    assert payload["release_notes"] == ["显示更新内容", "任意功能调用前提示新版本"]


def test_sf_update_check_separates_missing_install_from_version_update(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    plugin_root = home / "plugins" / plugin_bundle.PLUGIN_NAME
    manifest = {
        "plugin_update": {
            "name": plugin_bundle.PLUGIN_NAME,
            "latest_version": "0.1.6",
            "bundle_url": "/bundle.tar.gz",
            "sha256": "sha256:" + "d" * 64,
            "format": "tar.gz",
            "release_notes": ["可安装插件包"],
        }
    }

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sf_cli, "CONFIG_DIR", home / ".skillforge")
    monkeypatch.setattr(sf_cli, "CONFIG_PATH", home / ".skillforge" / "codex-cli.json")
    monkeypatch.setattr(sf_cli, "api_request", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(sf_cli, "base_url", lambda: "http://skillforge.example.com")

    sf_cli.sf_update(
        SimpleNamespace(
            check=True,
            force=False,
            target=str(plugin_root),
            install_sf_shim=True,
            no_install_sf_shim=False,
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["installed"] is False
    assert payload["current_version"] == sf_cli.CLI_VERSION
    assert payload["latest_version"] == "0.1.6"
    assert payload["install_required"] is True
    assert payload["update_available"] is False


def test_sf_plugin_status_does_not_report_downgrade_as_update(monkeypatch, tmp_path):
    home = tmp_path / "home"
    plugin_root = home / "plugins" / plugin_bundle.PLUGIN_NAME
    (plugin_root / ".codex-plugin").mkdir(parents=True)
    (plugin_root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": plugin_bundle.PLUGIN_NAME, "version": "0.1.9"}),
        encoding="utf-8",
    )
    manifest = {
        "plugin_update": {
            "name": plugin_bundle.PLUGIN_NAME,
            "latest_version": "0.1.6",
            "bundle_url": "/bundle.tar.gz",
            "sha256": "sha256:" + "e" * 64,
            "format": "tar.gz",
        }
    }

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sf_cli, "CONFIG_DIR", home / ".skillforge")
    monkeypatch.setattr(sf_cli, "CONFIG_PATH", home / ".skillforge" / "codex-cli.json")
    monkeypatch.setattr(sf_cli, "api_request", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(sf_cli, "base_url", lambda: "http://skillforge.example.com")

    status = sf_cli.plugin_status_data(SimpleNamespace(target=str(plugin_root)))
    assert status["installed"] is True
    assert status["current_version"] == "0.1.9"
    assert status["latest_version"] == "0.1.6"
    assert status["install_required"] is False
    assert status["update_available"] is False


def test_sf_prints_update_notice_before_normal_commands(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    plugin_root = home / "plugins" / plugin_bundle.PLUGIN_NAME
    (plugin_root / ".codex-plugin").mkdir(parents=True)
    (plugin_root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": plugin_bundle.PLUGIN_NAME, "version": "0.1.4"}),
        encoding="utf-8",
    )
    manifest = {
        "plugin_update": {
            "name": plugin_bundle.PLUGIN_NAME,
            "latest_version": "0.1.5",
            "bundle_url": "/bundle.tar.gz",
            "sha256": "sha256:" + "c" * 64,
            "format": "tar.gz",
            "release_notes": ["任意功能调用前提示新版本"],
        }
    }

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sf_cli, "CONFIG_DIR", home / ".skillforge")
    monkeypatch.setattr(sf_cli, "CONFIG_PATH", home / ".skillforge" / "codex-cli.json")
    monkeypatch.setattr(sf_cli, "api_request", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(sf_cli, "base_url", lambda: "http://skillforge.example.com")

    sf_cli.maybe_print_update_notice(["auth", "status"], SimpleNamespace(cmd="auth", auth_cmd="status", target=str(plugin_root)))
    err = capsys.readouterr().err
    assert "当前 0.1.4，最新 0.1.5" in err
    assert "任意功能调用前提示新版本" in err


def test_sf_chinese_aliases():
    assert sf_cli.apply_chinese_alias(["查看哪些技能"]) == ["skill", "list", "--scope", "visible"]
    assert sf_cli.apply_chinese_alias(["我的插件"]) == ["my"]
    assert sf_cli.apply_chinese_alias(["我的技能"]) == ["skill", "list", "--scope", "mine"]
    assert sf_cli.apply_chinese_alias(["我的部门插件"]) == ["skill", "list", "--scope", "department"]
    assert sf_cli.apply_chinese_alias(["插件状态"]) == ["plugin", "status"]
    assert sf_cli.apply_chinese_alias(["哪些未上传"]) == ["skill", "scan", "--path", ".", "--compare-remote"]
    assert sf_cli.apply_chinese_alias(["local", "pending"]) == ["skill", "scan", "--path", ".", "--compare-remote"]
    assert sf_cli.apply_chinese_alias(["拉取", "demo-skill"]) == ["skill", "pull", "demo-skill", "--path", "."]
    assert sf_cli.apply_chinese_alias(["同步到本地", "demo-skill", "--force"]) == [
        "skill",
        "pull",
        "demo-skill",
        "--path",
        ".",
        "--force",
    ]
    assert sf_cli.apply_chinese_alias(["skills"]) == ["skill", "list", "--scope", "visible"]
    assert sf_cli.apply_chinese_alias(["test-real", "--shop-id", "s1"]) == [
        "skill",
        "test",
        "--real-mcp",
        "--path",
        ".",
        "--shop-id",
        "s1",
    ]
    assert sf_cli.apply_chinese_alias(["sandbox", "--shop-id", "s1"]) == [
        "skill",
        "sandbox",
        "--path",
        ".",
        "--shop-id",
        "s1",
    ]
    assert sf_cli.apply_chinese_alias(["review", "sub_1"]) == ["submission", "status", "sub_1"]
    assert sf_cli.apply_chinese_alias(["status"]) == ["skill", "status", "--path", "."]
    assert sf_cli.apply_chinese_alias(["status", "sub_1"]) == ["submission", "status", "sub_1"]
    assert sf_cli.apply_chinese_alias(["auth"]) == ["auth", "status"]
    assert sf_cli.apply_chinese_alias(["Agent覆盖"]) == ["agent", "coverage"]
    assert sf_cli.apply_chinese_alias(["agent", "coverage"]) == ["agent", "coverage"]
    assert sf_cli.apply_chinese_alias(["训练资源"]) == ["training", "resources"]
    assert sf_cli.apply_chinese_alias(["训练任务"]) == ["training", "jobs"]
    assert sf_cli.apply_chinese_alias(["更新"]) == ["update"]
    assert sf_cli.apply_chinese_alias(["help"]) == ["help"]
    assert sf_cli.apply_chinese_alias(["submit"]) == ["skill", "submit", "--path", "."]
    assert sf_cli.apply_chinese_alias(["预览", "output.json"]) == ["preview", "output.json"]
    assert sf_cli.apply_chinese_alias(["preview", "apply", "preview_1"]) == ["preview-apply", "preview_1"]
    assert sf_cli.apply_chinese_alias(["review", "sub_1"]) == ["submission", "status", "sub_1"]


def test_sf_preview_uploads_output_file(monkeypatch, tmp_path):
    output_path = tmp_path / "output.json"
    output_path.write_text(
        json.dumps(
            {
                "reports": [{"title": "测试报告", "content_markdown": "# 测试报告"}],
                "todos": [{"title": "P0 待办", "priority": "P0"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "public_url": "http://skillforge.example.com/api/codex/previews/preview_1"}

    printed = []
    monkeypatch.setattr(sf_cli, "api_request", fake_api_request)
    monkeypatch.setattr(sf_cli, "print_json", lambda data: printed.append(data))

    sf_cli.preview_output(
        SimpleNamespace(
            file=str(output_path),
            title="",
            source_name="",
            content_type="auto",
            expires_in_hours=72,
        )
    )

    assert printed == [{"ok": True, "public_url": "http://skillforge.example.com/api/codex/previews/preview_1"}]
    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", "/api/codex/previews")
    body = kwargs["json_body"]
    assert body["content_type"] == "json"
    assert body["source_name"] == "output.json"
    assert body["content"]["reports"][0]["title"] == "测试报告"


def test_sf_help_is_local_and_chinese(monkeypatch, capsys):
    monkeypatch.setattr(sf_cli, "api_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("help must not call api")))

    assert sf_cli.main(["help"]) == 0
    out = capsys.readouterr().out
    assert "SkillForge /sf 常用命令" in out
    assert "/sf 拉取 <skill_id>" in out
    assert "/sf agent coverage" in out
    assert "/sf training jobs" in out
    assert "/sf update" in out

    assert sf_cli.main([]) == 0
    assert "SkillForge /sf 常用命令" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_codex_manifest_page_lists_all_sf_commands(client):
    html_resp = await client.get("/api/codex/catalog/manifest", headers={"Accept": "text/html"})
    assert html_resp.status_code == 200
    assert "text/html" in html_resp.headers["content-type"]
    html_text = html_resp.text
    assert "全部 sf 命令" in html_text
    assert "sf plugin status" in html_text
    assert "sf 我的插件" in html_text
    assert "sf 我的部门插件" in html_text
    assert "当前版本 MCP 能力" in html_text
    assert "模块能力" in html_text
    assert "项目功能声明" in html_text
    assert "项目生成标准" in html_text
    assert "项目组织权限解析" in html_text
    assert "项目服务调用" in html_text
    assert "项目运行资产" in html_text
    assert "短视频分析配方" in html_text
    assert "<span class='new-gradient'><strong>项目功能声明</strong>" in html_text
    assert "安装或刷新 SkillForge Codex 插件并验证 MCP 能力" in html_text
    assert "Codex 仍报 Invalid schema" in html_text
    assert "系统是如何运作的" in html_text
    assert '<a href="#top">首页</a>' in html_text
    assert html_text.find('<a href="#version">版本</a>') < html_text.find('<a href="#modules">模块能力</a>')
    assert html_text.find('id="version"') < html_text.find('id="modules"')
    assert html_text.find('id="modules"') < html_text.find('id="install"')
    assert html_text.find("全部 sf 命令") < html_text.find("当前版本 MCP 能力")
    assert "复制安装提示词" in html_text
    assert "让 /sf 注册成命令按钮" in html_text
    assert 'data-copy-install' in html_text
    assert 'id="copy-status"' in html_text
    assert "安装提示词已复制，可以直接粘贴到 Codex。" in html_text
    assert "navigator.clipboard.writeText" in html_text
    assert "JSON manifest" not in html_text
    assert "下载插件包" not in html_text
    assert "机器可读 JSON 地址" not in html_text
    assert "当前插件包校验" not in html_text
    assert "常用命令" not in html_text
    assert "步骤命令" not in html_text
    assert "Codex 通常会生成" not in html_text
    assert "scripts/main.py" not in html_text
    assert "department: 示例品牌传统电商运营部" not in html_text

    json_resp = await client.get("/api/codex/catalog/manifest?format=json", headers={"Accept": "application/json"})
    assert json_resp.status_code == 200
    manifest = json_resp.json()
    commands = {item["command"] for item in manifest["commands"]}
    assert "/sf 我的插件" in commands
    assert "/sf 我的部门插件" in commands
    assert "/sf plugin status" in commands
    assert "sf training resources" in commands
    assert "sf training datasets" in commands
    assert "sf training candidate <skill_id>" in commands
    assert "sf training jobs --status running" in commands
    assert "sf training create --title '训练新模型' --target-skill-id <skill_id>" in commands
    assert "sf training collect-due --gateway <training_gateway_id>" in commands
    assert "sf training deploy-request <training_job_id> --target-skill-id <skill_id>" in commands
    assert "sf training deployments" in commands
    assert "sf data latest tmall" in commands
    assert "sf project submit" in commands
    assert manifest["codex_install"]["post_install_commands"][:3] == [
        "sf update --check",
        "sf plugin status",
        "sf auth login",
    ]
    assert manifest["mcp_capabilities"]["tool_count"] >= 1
    org_search = next(tool for tool in manifest["mcp_capabilities"]["tools"] if tool["name"] == "skillforge_org_search_users")
    assert "按姓名" in org_search["provides"]
    tmall_data = next(tool for tool in manifest["mcp_capabilities"]["tools"] if tool["name"] == "skillforge_tmall_link_decline_data_latest")
    assert "天猫" in tmall_data["provides"]
    samplebrand_data = next(tool for tool in manifest["mcp_capabilities"]["tools"] if tool["name"] == "skillforge_samplebrand_cloud_video_data_latest")
    assert "示例品牌" in samplebrand_data["provides"]
    yuyi_task = next(tool for tool in manifest["mcp_capabilities"]["tools"] if tool["name"] == "yuyidata_create_sessions_retrieve_task")
    assert "客服对话" in yuyi_task["provides"]
    assert "定时拉取" in yuyi_task["use_when"]
    assert any(item["contract"] == "output.todos" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "trigger_type=cron + trigger_expression" for item in manifest["module_capabilities"])
    assert manifest["usage_tutorials"][0]["title"] == "案例：安装或刷新 SkillForge Codex 插件并验证 MCP 能力"
    assert "skillforge_ai_analyze" in manifest["usage_tutorials"][0]["prompt"]
    assert manifest["usage_tutorials"][0]["flow_title"] == "系统是如何运作的"
    assert any("anyOf、oneOf、allOf、enum 或 not" in step for step in manifest["usage_tutorials"][0]["flow"])


def test_sf_preview_apply_and_runs_cli_call_expected_endpoints(monkeypatch, tmp_path):
    calls = []
    printed = []
    output_path = tmp_path / "output.json"
    output_path.write_text('{"reports":[],"todos":[]}', encoding="utf-8")

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "path": path}

    monkeypatch.setattr(sf_cli, "api_request", fake_api_request)
    monkeypatch.setattr(sf_cli, "print_json", lambda data: printed.append(data))

    sf_cli.preview_apply(
        SimpleNamespace(
            preview_id="preview_1",
            real=False,
            idempotency_key="",
            skill_id="skill_1",
            run_id="run_1",
            params='{"a":1}',
            arg=["b=2"],
            run_mode="manual_real",
            triggered_by="",
            parent_run_id="",
            sample_used=False,
        )
    )
    sf_cli.runs_logs(SimpleNamespace(run_id="run_1", stream="stderr"))
    sf_cli.output_validate(SimpleNamespace(file=str(output_path), max_bytes=12345))

    assert calls[0][0:2] == ("POST", "/api/codex/previews/preview_1/apply")
    assert calls[0][2]["json_body"]["skill_id"] == "skill_1"
    assert calls[0][2]["json_body"]["params"] == {"a": 1, "b": 2}
    assert calls[1][0:2] == ("GET", "/api/codex/runs/run_1/logs?stream=stderr")
    assert calls[2][0:2] == ("POST", "/api/codex/output/validate")
    assert printed[-1]["ok"] is True


def test_sf_data_notify_market_schedule_pipeline_commands(monkeypatch, tmp_path):
    calls = []
    printed = []
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: pipe\n---\n", encoding="utf-8")

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "path": path}

    monkeypatch.setattr(sf_cli, "api_request", fake_api_request)
    monkeypatch.setattr(sf_cli, "print_json", lambda data: printed.append(data))
    monkeypatch.setattr(sf_cli, "skill_doctor", lambda args: None)

    sf_cli.data_latest(SimpleNamespace(capability="tmall", limit=1))
    sf_cli.data_latest(SimpleNamespace(capability="samplebrand", limit=1))
    sf_cli.notify_users(SimpleNamespace(query="示例成员4", department="", limit=5))
    sf_cli.schedule_set(SimpleNamespace(skill_id="skill_1", cron="50 7 * * *", update=False))
    sf_cli.skill_sync(SimpleNamespace(skill_id="skill_1", instance_id=["platform,node-a"], department="", no_push_git=False))
    sf_cli.market_rate(SimpleNamespace(skill_id="skill_1", rating=5, comment="稳定"))
    sf_cli.pipeline_run(SimpleNamespace(path=str(root), remote=False, submit=False, message="", keep_going=False))

    assert calls[0][1] == "/api/codex/mcp/call"
    assert calls[0][2]["json_body"]["tool"] == "skillforge_data_capability_latest"
    assert calls[0][2]["json_body"]["arguments"]["capability"] == "tmall.link_decline.raw_collection"
    assert calls[1][2]["json_body"]["tool"] == "skillforge_data_capability_latest"
    assert calls[1][2]["json_body"]["arguments"]["capability"] == "cloud_video.samplebrand_weekly.raw_collection"
    assert calls[2][2]["json_body"]["tool"] == "skillforge_org_search_users"
    assert calls[3][0:2] == ("POST", "/api/codex/skills/skill_1/schedule")
    assert calls[3][2]["json_body"] == {"action": "start", "cron_expression": "50 7 * * *"}
    assert calls[4][0:2] == ("POST", "/api/codex/skills/skill_1/sync")
    assert calls[4][2]["json_body"] == {"instance_ids": ["platform", "node-a"], "department": None, "push_git": True}
    assert calls[5][0:2] == ("POST", "/api/codex/market/skill_1/ratings")
    assert printed[-1]["ok"] is True


@pytest.mark.asyncio
async def test_codex_output_preview_creates_url_and_renders_page(client):
    from app.database import async_session_factory

    raw_token = "test-codex-output-preview-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex_preview_user",
            username="codex_preview_user",
            name="Codex Preview 用户",
            role="observer",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        cli_session = CodexCliSession(
            id="cli_preview_user",
            user_id=user.id,
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([user, cli_session])
        await session.commit()

    payload = {
        "title": "上午输出预览",
        "source_name": "operator-output.json",
        "content_type": "json",
        "content": {
            "reports": [{"title": "报告 A", "content_markdown": "# 报告 A\n\n- 结论"}],
            "todos": [{"title": "P0 待办", "priority": "P0", "payload": {"priority": "P1"}}],
            "actions": [{"type": "inspect"}],
            "notifications": [{"channel": "dingtalk"}],
            "collector": {"run_id": "run_1"},
        },
        "expires_in_hours": 24,
    }

    resp = await client.post(
        "/api/codex/previews",
        headers={"Authorization": f"Bearer {raw_token}"},
        json=payload,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "上午输出预览"
    assert data["content_type"] == "json"
    assert data["summary"]["report_count"] == 1
    assert data["summary"]["todo_count"] == 1
    assert data["summary"]["action_count"] == 1
    assert data["summary"]["notification_count"] == 1
    assert data["summary"]["has_collector"] is True
    assert data["url"].startswith("/api/codex/previews/")
    assert data["public_url"].endswith(data["url"])

    page_resp = await client.get(data["url"])
    assert page_resp.status_code == 200
    assert "text/html" in page_resp.headers["content-type"]
    assert "上午输出预览" in page_resp.text
    assert "报告正文" in page_resp.text
    assert "待办预览" in page_resp.text
    assert "P0 待办" in page_resp.text
    assert "原始 JSON" in page_resp.text

    async with async_session_factory() as session:
        row = await session.get(CodexOutputPreview, data["id"])
        assert row is not None
        assert row.user_id == "codex_preview_user"
        audit_row = (
            await session.execute(
                codex_service.select(AuditLog).where(
                    AuditLog.user_id == "codex_preview_user",
                    AuditLog.action == "codex.preview.create",
                )
            )
        ).scalar_one()
        assert audit_row.target_id == data["id"]


@pytest.mark.asyncio
async def test_codex_output_validate_and_preview_apply_create_run_result(client, monkeypatch):
    from app.database import async_session_factory
    from app.todos.models import DecisionRequest

    raw_token = "test-codex-preview-apply-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex_apply_user",
            username="codex_apply_user",
            name="Codex Apply 用户",
            role="aibp",
            state="active",
            department="运营部",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        skill = Skill(
            id="codex-apply-skill",
            name="Codex Apply Skill",
            department="运营部",
            visibility="company",
            owner=user.id,
            status="active",
            approval_level=1,
        )
        session.add_all([
            user,
            skill,
            CodexCliSession(
                id="cli_apply_user",
                user_id=user.id,
                token_hash=codex_service.token_hash(raw_token),
                scopes_json={"source": "test"},
                permissions_rev_snapshot=0,
                expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
            ),
        ])
        await session.commit()

    output = {
        "_skillforge_meta": {"skill_id": "codex-apply-skill"},
        "reports": [{"title": "正式报告", "content_markdown": "# 正式报告\n\n完整内容"}],
        "todos": [
            {
                "kind": "review",
                "title": "P1｜处理测试事项",
                "priority": "P1",
                "summary": "需要跟进",
                "reviewers": ["codex_apply_user"],
                "payload": {
                    "priority": "P1",
                    "primary_indicator": {"label": "支付金额", "value": "-20%"},
                    "key_evidence": ["测试证据"],
                    "suggested_action": "处理",
                },
            }
        ],
        "actions": [{"type": "inspect"}],
        "notifications": [{"channel": "dingtalk"}],
    }

    validate_resp = await client.post(
        "/api/codex/output/validate",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"output": output},
    )
    assert validate_resp.status_code == 200
    validation = validate_resp.json()
    assert validation["ok"] is True
    assert validation["summary"]["priority_counts"]["P1"] == 1

    preview_resp = await client.post(
        "/api/codex/previews",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"content_type": "json", "content": output, "source_name": "apply.json"},
    )
    assert preview_resp.status_code == 200
    preview_id = preview_resp.json()["id"]

    dry_resp = await client.post(
        f"/api/codex/previews/{preview_id}/apply",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"skill_id": "codex-apply-skill"},
    )
    assert dry_resp.status_code == 200
    dry = dry_resp.json()
    assert dry["dry_run"] is True
    assert dry["summary"]["would_create"]["todos"] == 1

    monkeypatch.setattr("app.tasktree.writer.writer.create_execution_node", AsyncMock())
    monkeypatch.setattr("app.tasktree.service.invalidate_tasktree", AsyncMock())
    monkeypatch.setattr("app.common.cache.cache_delete_pattern", AsyncMock())

    real_resp = await client.post(
        f"/api/codex/previews/{preview_id}/apply",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "real": True,
            "idempotency_key": "preview-apply-test-key",
            "skill_id": "codex-apply-skill",
            "run_id": "preview-apply-run-1",
            "params": {"date": "2026-05-28"},
        },
    )
    assert real_resp.status_code == 200
    real = real_resp.json()
    assert real["dry_run"] is False
    assert real["result"]["run_id"] == "preview-apply-run-1"
    assert real["result"]["todo_count"] == 1

    async with async_session_factory() as session:
        run = await session.get(ExecutionRun, "preview-apply-run-1")
        assert run is not None
        assert run.status == "completed"
        decision = (
            await session.execute(
                codex_service.select(DecisionLog).where(DecisionLog.run_id == "preview-apply-run-1")
            )
        ).scalar_one()
        todo = (
            await session.execute(
                codex_service.select(DecisionRequest).where(DecisionRequest.run_id == "preview-apply-run-1")
            )
        ).scalar_one()
        artifacts = (
            await session.execute(
                codex_service.select(ExecutionArtifact).where(ExecutionArtifact.run_id == "preview-apply-run-1")
            )
        ).scalars().all()

    assert decision.output_result["reports"][0]["title"] == "正式报告"
    assert todo.title == "P1｜处理测试事项"
    assert todo.payload["priority"] == "P1"
    assert {row.kind for row in artifacts} == {"raw-input", "raw-output"}


@pytest.mark.asyncio
async def test_codex_run_introspection_routes_return_result_logs_artifacts(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.artifact_service import persist_execution_artifact, persist_execution_log
    from app.todos.models import DecisionRequest

    monkeypatch.setattr(settings, "EXECUTION_ARTIFACT_ROOT", str(tmp_path / "execution-artifacts"))
    raw_token = "test-codex-run-introspection-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex_runs_user",
            username="codex_runs_user",
            name="Codex Runs 用户",
            role="aibp",
            state="active",
            department="运行部",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        skill = Skill(
            id="codex-runs-skill",
            name="Codex Runs Skill",
            department="运行部",
            visibility="company",
            owner=user.id,
            status="active",
        )
        run = ExecutionRun(
            id="codex-run-inspect-1",
            skill_id=skill.id,
            trigger_type="node_scheduler",
            run_mode="scheduled_real",
            status="completed",
            total_steps=1,
            completed_steps=1,
            summary="完成",
        )
        step = ExecutionStep(
            run_id=run.id,
            skill_id=skill.id,
            step_order=1,
            status="completed",
            input_data={"date": "2026-05-28"},
            output_data={"ok": True},
        )
        decision = DecisionLog(
            run_id=run.id,
            skill_id=skill.id,
            input_snapshot={"date": "2026-05-28"},
            output_result={"reports": [{"title": "运行报告"}]},
            approval_level=1,
        )
        todo = DecisionRequest(
            id="dr-codex-run-inspect",
            source_type="skill_execution_review",
            source_id="codex-run-inspect-1:P1",
            skill_id=skill.id,
            run_id=run.id,
            kind="review",
            title="P1｜运行待办",
            payload={"priority": "P1"},
            sla_at=codex_service.utc_safe_now(),
        )
        session.add_all([
            user,
            skill,
            run,
            step,
            decision,
            todo,
            CodexCliSession(
                id="cli_runs_user",
                user_id=user.id,
                token_hash=codex_service.token_hash(raw_token),
                scopes_json={"source": "test"},
                permissions_rev_snapshot=0,
                expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
            ),
        ])
        await session.flush()
        await persist_execution_artifact(
            session,
            run_id=run.id,
            skill_id=skill.id,
            kind="raw-output",
            payload={"collection_schema": "codex_runs_schema", "items": [1]},
        )
        await persist_execution_log(
            session,
            run_id=run.id,
            skill_id=skill.id,
            stream="stdout",
            content="Authorization: Bearer secret-token\n正常日志",
            source="pytest",
        )
        await session.commit()

    list_resp = await client.get(
        "/api/codex/runs?skill_id=codex-runs-skill",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["items"][0]["run_id"] == "codex-run-inspect-1"

    steps_resp = await client.get(
        "/api/codex/runs/codex-run-inspect-1/steps",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert steps_resp.status_code == 200
    assert steps_resp.json()["items"][0]["step_order"] == 1

    result_resp = await client.get(
        "/api/codex/runs/codex-run-inspect-1/result",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert result_resp.status_code == 200
    result = result_resp.json()
    assert result["todo_count"] == 1
    assert result["latest_output"]["reports"][0]["title"] == "运行报告"

    artifacts_resp = await client.get(
        "/api/codex/runs/codex-run-inspect-1/artifacts?include_content=true",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert artifacts_resp.status_code == 200
    artifact = artifacts_resp.json()["items"][0]
    assert artifact["schema"] == "codex_runs_schema"
    assert artifact["content_json"]["items"] == [1]

    logs_resp = await client.get(
        "/api/codex/runs/codex-run-inspect-1/logs",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert logs_resp.status_code == 200
    log_tail = logs_resp.json()["items"][0]["content_tail"]
    assert "Bearer secret-token" not in log_tail
    assert "Authorization: Bearer ***" in log_tail


@pytest.mark.asyncio
async def test_codex_schedule_review_and_market_routes(client, monkeypatch):
    from app.database import async_session_factory

    raw_token = "test-codex-control-routes-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex_control_user",
            username="codex_control_user",
            name="Codex Control 用户",
            role="system_admin",
            state="active",
            department="控制部",
            is_active=True,
            can_view_all=True,
            permissions_rev=0,
            must_change_password=False,
        )
        skill = Skill(
            id="codex-control-skill",
            name="Codex Control Skill",
            description="control routes",
            department="控制部",
            visibility="company",
            owner=user.id,
            status="active",
            current_version="v1.0",
            market_status="company_public",
            trigger_type="manual",
        )
        review = Review(
            skill_id=skill.id,
            submitter=user.id,
            reviewer=user.id,
            status="pending",
            change_type="codex_submit",
            reason="测试审核",
        )
        session.add_all([
            user,
            skill,
            review,
            CodexCliSession(
                id="cli_control_user",
                user_id=user.id,
                token_hash=codex_service.token_hash(raw_token),
                scopes_json={"source": "test"},
                permissions_rev_snapshot=0,
                expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
            ),
        ])
        await session.commit()
        review_id = review.id

    async def fake_update_skill_schedule(db, skill_id, action, cron_expression, user_id):
        return {"skill_id": skill_id, "action": action, "cron_expression": cron_expression, "user_id": user_id}

    monkeypatch.setattr("app.tasktree.schedule_query.update_skill_schedule", fake_update_skill_schedule)

    async def fake_create_and_run_sync_job(skill_id, **kwargs):
        return {
            "job_id": 99,
            "all_ok": True,
            "instances": [{"instance_id": kwargs["target_instance_ids"][0], "ok": True}],
            "actor": getattr(kwargs["actor"], "id", None),
            "push_git": kwargs["push_git"],
        }

    monkeypatch.setattr(
        "app.execution.sync_service.sync_service.create_and_run_sync_job",
        fake_create_and_run_sync_job,
    )

    schedule_resp = await client.post(
        "/api/codex/skills/codex-control-skill/schedule",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"action": "start", "cron_expression": "50 7 * * *"},
    )
    assert schedule_resp.status_code == 200
    assert schedule_resp.json()["cron_expression"] == "50 7 * * *"

    schedule_get_resp = await client.get(
        "/api/codex/skills/codex-control-skill/schedule",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert schedule_get_resp.status_code == 200
    assert schedule_get_resp.json()["status"] == "stopped"

    sync_resp = await client.post(
        "/api/codex/skills/codex-control-skill/sync",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"instance_ids": ["platform"], "push_git": True},
    )
    assert sync_resp.status_code == 200
    assert sync_resp.json()["job_id"] == 99
    assert sync_resp.json()["instances"][0]["instance_id"] == "platform"

    reviews_resp = await client.get(
        "/api/codex/reviews?skill_id=codex-control-skill",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert reviews_resp.status_code == 200
    assert reviews_resp.json()["items"][0]["id"] == review_id

    comment_resp = await client.post(
        f"/api/codex/reviews/{review_id}/comment",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"content": "补充说明", "file_path": "SKILL.md", "line_number": 3, "side": "right"},
    )
    assert comment_resp.status_code == 200
    assert comment_resp.json()["comment_id"]

    review_get_resp = await client.get(
        f"/api/codex/reviews/{review_id}",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert review_get_resp.status_code == 200
    assert review_get_resp.json()["comments"][0]["file_path"] == "SKILL.md"

    market_resp = await client.get(
        "/api/codex/market",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert market_resp.status_code == 200
    assert any(item["id"] == "codex-control-skill" for item in market_resp.json()["items"])

    rate_resp = await client.post(
        "/api/codex/market/codex-control-skill/ratings",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"rating": 5, "comment": "稳定好用"},
    )
    assert rate_resp.status_code == 200
    assert rate_resp.json()["rating"] == 5

    ratings_resp = await client.get(
        "/api/codex/market/codex-control-skill/ratings",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert ratings_resp.status_code == 200
    ratings = ratings_resp.json()
    assert ratings["count"] == 1
    assert ratings["average_rating"] == 5

    async with async_session_factory() as session:
        rating = (
            await session.execute(
                codex_service.select(MarketRating).where(MarketRating.skill_id == "codex-control-skill")
            )
        ).scalar_one()
    assert rating.comment == "稳定好用"


@pytest.mark.asyncio
async def test_codex_manifest_page_lists_all_sf_commands(client):
    html_resp = await client.get("/api/codex/catalog/manifest", headers={"Accept": "text/html"})
    assert html_resp.status_code == 200
    assert "text/html" in html_resp.headers["content-type"]
    html_text = html_resp.text
    assert "全部 sf 命令" in html_text
    assert "sf plugin status" in html_text
    assert "sf 我的插件" in html_text
    assert "sf 我的部门插件" in html_text
    assert "sf 版本功能说明" in html_text
    assert '<a href="#version">版本</a>' in html_text
    assert "版本历史" in html_text
    assert "通用 SF 数据写入与存储" in html_text
    assert "当前封面发布：通用 SF 数据写入与存储" in html_text
    assert "SF 数据存储" in html_text
    assert "sf_data 工具" in html_text
    assert "SF 页面展示" in html_text
    assert "skillforge_sf_data_write" in html_text
    assert "sf data write --namespace demo" in html_text
    assert "写入后的数据会在前端 SF 数据页展示" in html_text
    assert "Tmall 项目数据网关与动态日报输出" in html_text
    assert "SF 封面发布的是当前 Codex 可直接使用的平台能力" in html_text
    assert "Project Gateway" in html_text
    assert "tmall_link_health_latest_analysis" in html_text
    assert "Project Gateway 新增 Tmall 链接下滑平台缓存数据读取能力" in html_text
    assert "云视频周度诊断 Skill 与业务分析 Agent" in html_text
    assert "业务分析 Agent" in html_text
    assert "cloud-video-weekly-diagnosis" in html_text
    assert "项目运行资产与短视频生成标准" in html_text
    assert "版本 0.3.0" in html_text
    assert "new-gradient" in html_text
    assert "linear-gradient(90deg, #2563eb 0%, #0f8a63 42%, #b45309 100%)" in html_text
    assert "/sf project spec --recipe short-video-analysis" in html_text
    assert "/sf project asset upload &lt;project_run_id&gt; &lt;file&gt;" in html_text
    assert "/sf project asset list &lt;project_run_id&gt;" in html_text
    assert "short-video-analysis 项目生成标准" in html_text
    assert "Release Notes" not in html_text
    assert "Codex 端完整运维与发布闭环" in html_text
    assert "新增 sf run / sf runs" in html_text
    assert "输出结果预览 URL" in html_text
    assert "新增 sf preview" in html_text
    assert "缓存数据能力与共享 Skill 安装" in html_text
    assert "<details class='release-card old'>" in html_text
    assert "版本 0.1.6" in html_text
    assert "版本 0.1.2" in html_text
    assert "内部过渡版" in html_text
    assert "feature-list li.highlight" in html_text
    assert "安装后验证" in html_text
    assert "sf skill install &lt;skill_id&gt; --path ./skills" in html_text
    assert "支持 sf skill install / pull" in html_text
    assert "sf runs logs &lt;run_id&gt;" in html_text
    assert "sf preview apply &lt;preview_id&gt;" in html_text
    assert "sf schedule set &lt;skill_id&gt;" in html_text
    assert "sf market rate &lt;skill_id&gt;" in html_text
    assert "sf preview output.json" in html_text
    assert "输出结果预览" in html_text
    assert "sha256" in html_text
    assert "当前版本 MCP 能力" in html_text
    assert "模块能力" in html_text
    assert "项目功能声明" in html_text
    assert "项目生成标准" in html_text
    assert "项目组织权限解析" in html_text
    assert "项目服务调用" in html_text
    assert "项目运行资产" in html_text
    assert "短视频分析配方" in html_text
    assert "云视频周度采集 Skill 模板" in html_text
    assert "部门业务分析 Agent" in html_text
    assert "通用 SF 数据存储" in html_text
    assert "<span class='new-gradient'><strong>项目功能声明</strong>" in html_text
    assert "安装或刷新 SkillForge Codex 插件并验证 MCP 能力" in html_text
    assert "Codex 仍报 Invalid schema" in html_text
    assert "系统是如何运作的" in html_text
    assert '<a href="#top">首页</a>' in html_text
    assert html_text.find('<a href="#version">版本</a>') < html_text.find('<a href="#modules">模块能力</a>')
    assert html_text.find('id="version"') < html_text.find('id="modules"')
    assert html_text.find('id="modules"') < html_text.find('id="install"')
    assert html_text.find("全部 sf 命令") < html_text.find("当前版本 MCP 能力")
    assert "复制安装提示词" in html_text
    assert "让 /sf 注册成命令按钮" in html_text
    assert 'data-copy-install' in html_text
    assert 'id="copy-status"' in html_text
    assert "安装提示词已复制，可以直接粘贴到 Codex。" in html_text
    assert "navigator.clipboard.writeText" in html_text
    assert "Plugin:" not in html_text
    assert "Version " not in html_text
    assert "Production:" not in html_text
    assert "Auth" not in html_text
    assert "Update" not in html_text
    assert "JSON manifest" not in html_text
    assert "下载插件包" not in html_text
    assert "机器可读 JSON 地址" not in html_text
    assert "当前插件包校验" not in html_text
    assert "常用命令" not in html_text
    assert "步骤命令" not in html_text
    assert "Codex 通常会生成" not in html_text
    assert "scripts/main.py" not in html_text
    assert "department: 示例品牌传统电商运营部" not in html_text

    json_resp = await client.get("/api/codex/catalog/manifest?format=json", headers={"Accept": "application/json"})
    assert json_resp.status_code == 200
    manifest = json_resp.json()
    commands = {item["command"] for item in manifest["commands"]}
    assert "/sf 我的插件" in commands
    assert "/sf 我的部门插件" in commands
    assert "/sf plugin status" in commands
    assert "sf preview output.json" in commands
    assert "/sf runs logs <run_id>" in commands
    assert "sf preview apply <preview_id>" in commands
    assert "sf output validate output.json" in commands
    assert "/sf agent analysis list --department \"示例品牌内容电商运营部\"" in commands
    assert "/sf agent analysis create --name \"示例品牌同主题视频消耗诊断 Agent\" --department \"示例品牌内容电商运营部\" --skill-id samplebrand-weekly-video-diagnosis --owner \"祁莹莹\" --prompt-version analysis_v1" in commands
    assert "/sf project spec --recipe short-video-analysis" in commands
    assert "/sf project org resolve --department \"部门\" --owner \"姓名\"" in commands
    assert "/sf project verify <project_id> --recipe short-video-analysis" in commands
    assert "/sf project asset upload <project_run_id> <file>" in commands
    assert "/sf project asset list <project_run_id>" in commands
    assert "/sf data write --namespace demo --json '{\"hello\":\"world\"}' --real --idempotency-key <key>" in commands
    assert "/sf data records --namespace demo" in commands
    assert "/sf data record <sfdata_id>" in commands
    assert "/sf mcp call skillforge_sf_data_write --args '{\"namespace\":\"demo\",\"data\":{\"hello\":\"world\"}}' --real --idempotency-key <key>" in commands
    assert "/sf mcp call skillforge_sf_data_list --args '{\"namespace\":\"demo\"}'" in commands
    assert "/sf mcp call skillforge_sf_data_get --args '{\"id\":\"sfdata_xxx\"}'" in commands
    assert "sf skill init samplebrand-weekly-video-diagnosis --template cloud-video-weekly-diagnosis --department \"示例品牌内容电商运营部\" --trigger-type cron --cron '0 8 * * *'" in commands
    assert "sf schedule set <skill_id> --cron '50 7 * * *'" in commands
    assert "sf market rate <skill_id> --rating 5 --comment \"稳定好用\"" in commands
    assert manifest["codex_install"]["post_install_commands"][:3] == [
        "sf update --check",
        "sf plugin status",
        "sf auth login",
    ]
    assert manifest["mcp_capabilities"]["tool_count"] >= 1
    org_search = next(tool for tool in manifest["mcp_capabilities"]["tools"] if tool["name"] == "skillforge_org_search_users")
    assert "按姓名" in org_search["provides"]
    yuyi_task = next(tool for tool in manifest["mcp_capabilities"]["tools"] if tool["name"] == "yuyidata_create_sessions_retrieve_task")
    assert "客服对话" in yuyi_task["provides"]
    assert "定时拉取" in yuyi_task["use_when"]
    sf_data_write = next(tool for tool in manifest["mcp_capabilities"]["tools"] if tool["name"] == "skillforge_sf_data_write")
    assert sf_data_write["write"] is True
    assert "通用数据存储" in sf_data_write["provides"]
    assert "平台并在 SF 页面展示" in sf_data_write["use_when"]
    assert any(item["contract"] == "output.todos" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "sf preview <file> / /api/codex/previews/{id}" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "trigger_type=cron + trigger_expression" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "projectforge.yaml capabilities / Project Gateway" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "sf project spec / sf project doctor --recipe" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "sf project org resolve / /api/codex/projects/org/resolve" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "sf project invoke / /api/codex/projects/{project_id}/service/invoke" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "sf project asset upload/list / ProjectRunAsset" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "short-video-analysis / original_video / visual_frame" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "sf skill init --template cloud-video-weekly-diagnosis" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "sf agent analysis create/list / /api/codex/agents/analysis" for item in manifest["module_capabilities"])
    assert any(item["contract"] == "sf data write/records/record + skillforge_sf_data_write/list/get" for item in manifest["module_capabilities"])
    assert manifest["usage_tutorials"][0]["title"] == "案例：安装或刷新 SkillForge Codex 插件并验证 MCP 能力"
    assert "skillforge_ai_analyze" in manifest["usage_tutorials"][0]["prompt"]
    assert manifest["usage_tutorials"][0]["flow_title"] == "系统是如何运作的"
    assert any("anyOf、oneOf、allOf、enum 或 not" in step for step in manifest["usage_tutorials"][0]["flow"])


@pytest.mark.asyncio
async def test_analysis_agent_blueprint_api_binds_skill_and_editors(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.agents.models import DepartmentAnalysisAgent

    async with async_session_factory() as session:
        dept = OrgUnit(
            id="dept-samplebrand-content",
            name="示例品牌内容电商运营部",
            type="department",
            parent_id=None,
            path="/dept-samplebrand-content",
        )
        owner = User(
            id="qiyingying",
            username="qiyingying",
            name="祁莹莹",
            role="dept_admin",
            department="示例品牌内容电商运营部",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        skill = Skill(
            id="samplebrand-weekly-video-diagnosis",
            name="示例品牌周度同主题视频消耗诊断",
            description="测试 Skill",
            department="示例品牌内容电商运营部",
            trigger_type="cron",
            trigger_expression="0 8 * * *",
            risk_level="R2",
            owner="admin",
            status="draft",
            org_unit_id=dept.id,
        )
        session.add_all([
            dept,
            owner,
            UserOrgMembership(user_id=owner.id, org_unit_id=dept.id, membership_type="primary", is_manager=True),
            skill,
            SkillMember(skill_id=skill.id, user_id="admin", role="owner", granted_by="admin"),
        ])
        await session.commit()

    resp = await client.post(
        "/api/agents/analysis-agents",
        json={
            "id": "samplebrand-same-topic-video-diagnosis-agent",
            "name": "示例品牌同主题视频消耗诊断 Agent",
            "department": "示例品牌内容电商运营部",
            "owner_query": "祁莹莹",
            "editor_queries": ["祁莹莹"],
            "skill_id": "samplebrand-weekly-video-diagnosis",
            "prompt_version": "analysis_v1",
            "dimensions": ["首屏钩子", "商品露出", "行动引导"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()["agent"]
    assert data["owner_user_id"] == "qiyingying"
    assert data["skill_id"] == "samplebrand-weekly-video-diagnosis"
    assert data["dimensions"] == ["首屏钩子", "商品露出", "行动引导"]
    assert data["permissions"]["edit"] is True
    assert data["permissions"]["control_run"] is True
    assert data["control"]["schema"]["presets"][0]["value"] == "weekly_standard"
    assert "output_sections" in data["control"]["effective"]["default_params"]
    assert data["prompt"]["goal"]
    assert "prompt_focus" in data["prompt"]["editable_params"]
    assert any(item["key"] == "same_topic_compare" for item in data["capabilities"])
    assert data["verification"]["sample_payload"]["params"]["_analysis_agent_id"] == "samplebrand-same-topic-video-diagnosis-agent"

    list_resp = await client.get("/api/agents/analysis-agents", params={"department": "示例品牌内容电商运营部"})
    assert list_resp.status_code == 200
    listed_agent = list_resp.json()["items"][0]
    assert listed_agent["id"] == "samplebrand-same-topic-video-diagnosis-agent"
    assert listed_agent["permissions"]["edit"] is True
    assert any(item["value"] == "brief_summary" for item in listed_agent["control"]["schema"]["presets"])
    assert listed_agent["prompt"]["verification_questions"]

    validate_resp = await client.post(
        "/api/agents/analysis-agents/samplebrand-same-topic-video-diagnosis-agent/validate",
        json={"params": {"preset": "brief_summary", "top_n": 2}},
    )
    assert validate_resp.status_code == 200
    validation = validate_resp.json()
    assert validation["ok"] is True
    assert {item["key"] for item in validation["checks"]} >= {"bound_skill", "prompt_contract", "traceability"}
    assert validation["sample_payload"]["skill_id"] == "samplebrand-weekly-video-diagnosis"
    assert validation["sample_payload"]["params"]["top_n"] == 2
    assert validation["sample_payload"]["params"]["analysis_agent"]["prompt_version"] == "analysis_v1"
    assert validation["sample_payload"]["params"]["analysis_agent"]["default_params"]["preset"] == "brief_summary"

    async with async_session_factory() as session:
        row = await session.get(DepartmentAnalysisAgent, "samplebrand-same-topic-video-diagnosis-agent")
        member = await session.get(SkillMember, ("samplebrand-weekly-video-diagnosis", "qiyingying"))
        assert row is not None
        assert row.prompt_version == "analysis_v1"
    assert member is not None
    assert member.role == "editor"


@pytest.mark.asyncio
async def test_analysis_agent_owner_can_edit_prompt_and_model_profile(client, monkeypatch):
    from app.agents.models import DepartmentAnalysisAgent
    from app.agents.router import AnalysisAgentUpsertRequest, upsert_analysis_agent
    from app.auth.models import User
    from app.database import async_session_factory

    async with async_session_factory() as session:
        dept = OrgUnit(
            id="dept-samplebrand-content",
            name="示例品牌内容电商运营部",
            type="department",
            parent_id=None,
            path="/dept-samplebrand-content",
        )
        owner = User(
            id="qiyingying",
            username="qiyingying",
            name="祁莹莹",
            role="observer",
            department="示例品牌内容电商运营部",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        skill = Skill(
            id="samplebrand-video-low-consumption-operator-v1",
            name="示例品牌低消耗视频日诊断",
            description="测试 Skill",
            department="示例品牌内容电商运营部",
            visibility="department",
            trigger_type="cron",
            trigger_expression="50 7 * * *",
            risk_level="R2",
            owner="admin",
            status="active",
            org_unit_id=dept.id,
        )
        agent = DepartmentAnalysisAgent(
            id="samplebrand-video-low-consumption-diagnosis-agent",
            name="示例品牌低消耗视频日诊断 Agent",
            department_id=dept.id,
            department="示例品牌内容电商运营部",
            owner_user_id=owner.id,
            skill_id=skill.id,
            prompt_version="analysis_v1",
            description="原说明",
            dimensions_json=["首屏钩子", "商品露出"],
            default_params_json={"model_profile": "default", "top_n": 3, "todo_enabled": True},
            editor_user_ids_json=[owner.id],
            status="active",
            is_active=True,
            created_by="admin",
            updated_by="admin",
        )
        session.add_all([
            dept,
            owner,
            UserOrgMembership(user_id=owner.id, org_unit_id=dept.id, membership_type="primary", is_manager=False),
            skill,
            agent,
        ])
        await session.commit()

    monkeypatch.setattr("app.agents.router.audit.log", AsyncMock())
    async with async_session_factory() as session:
        current_user = await session.get(User, "qiyingying")
        result = await upsert_analysis_agent(
            request=None,
            body=AnalysisAgentUpsertRequest(
                id="samplebrand-video-low-consumption-diagnosis-agent",
                name="示例品牌低消耗视频日诊断 Agent",
                department="其它部门",
                department_id="dept-other",
                owner_user_id="someone-else",
                skill_id="other-skill",
                prompt_version="analysis_v2",
                description="业务改过的说明",
                dimensions=["首屏钩子", "行动引导", "投放承接"],
                default_params={
                    "model_profile": "flash",
                    "top_n": 3,
                    "todo_enabled": True,
                    "prompt_goal": "优先识别高效低放量，不要套模板。",
                    "output_sections": ["executive_summary", "personal_improvements"],
                },
                editor_user_ids=["someone-else"],
                status="archived",
            ),
            current_user=current_user,
            db=session,
        )
        await session.commit()

    assert result["ok"] is True
    data = result["agent"]
    assert data["department_id"] == "dept-samplebrand-content"
    assert data["owner_user_id"] == "qiyingying"
    assert data["skill_id"] == "samplebrand-video-low-consumption-operator-v1"
    assert data["status"] == "active"
    assert data["default_params"]["model_profile"] == "deepseek-v4-flash"
    assert data["prompt"]["goal"] == "优先识别高效低放量，不要套模板。"
    assert data["permissions"]["edit"] is True

    async with async_session_factory() as session:
        row = await session.get(DepartmentAnalysisAgent, "samplebrand-video-low-consumption-diagnosis-agent")
        assert row.department_id == "dept-samplebrand-content"
        assert row.owner_user_id == "qiyingying"
        assert row.skill_id == "samplebrand-video-low-consumption-operator-v1"
        assert row.default_params_json["model_profile"] == "deepseek-v4-flash"
        assert row.editor_user_ids_json == ["qiyingying"]


def test_analysis_agent_id_autogeneration_handles_chinese_name():
    from app.agents.router import _clean_agent_id

    first = _clean_agent_id(None, "示例品牌同主题视频消耗诊断 Agent")
    second = _clean_agent_id(None, "示例品牌同主题视频消耗诊断 Agent")

    assert first == second
    assert first.startswith("agent-du-lei-si-tong-zhu-ti-shi-pin-xiao-hao-")
    assert first != "agent-"
    assert len(first) <= 80


@pytest.mark.asyncio
async def test_analysis_agent_blueprint_rejects_cross_department_editor_user_id(client, monkeypatch):
    from app.agents.router import AnalysisAgentUpsertRequest, upsert_analysis_agent
    from app.auth.models import User
    from app.common.exceptions import AppError
    from app.database import async_session_factory

    async with async_session_factory() as session:
        samplebrand_dept = OrgUnit(
            id="dept-samplebrand-content",
            name="示例品牌内容电商运营部",
            type="department",
            parent_id=None,
            path="/dept-samplebrand-content",
        )
        other_dept = OrgUnit(
            id="dept-other",
            name="其它部门",
            type="department",
            parent_id=None,
            path="/dept-other",
        )
        session.add_all([
            samplebrand_dept,
            other_dept,
            User(
                id="dept-admin",
                username="dept-admin",
                name="部门管理员",
                role="dept_admin",
                department="示例品牌内容电商运营部",
                state="active",
                is_active=True,
                can_view_all=False,
                permissions_rev=0,
                must_change_password=False,
            ),
            User(
                id="other-editor",
                username="other-editor",
                name="其它部门编辑",
                role="dept_admin",
                department="其它部门",
                state="active",
                is_active=True,
                can_view_all=False,
                permissions_rev=0,
                must_change_password=False,
            ),
            UserOrgMembership(user_id="dept-admin", org_unit_id=samplebrand_dept.id, membership_type="primary", is_manager=True),
            UserOrgMembership(user_id="other-editor", org_unit_id=other_dept.id, membership_type="primary", is_manager=True),
        ])
        await session.commit()

    monkeypatch.setattr("app.agents.router.audit.log", AsyncMock())
    async with async_session_factory() as session:
        current_user = await session.get(User, "dept-admin")
        with pytest.raises(AppError) as exc:
            await upsert_analysis_agent(
                request=None,
                body=AnalysisAgentUpsertRequest(
                    name="示例品牌同主题视频消耗诊断 Agent",
                    department="示例品牌内容电商运营部",
                    editor_user_ids=["other-editor"],
                ),
                current_user=current_user,
                db=session,
            )

    assert exc.value.code == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_analysis_agent_blueprint_allows_skill_owner_aibp_for_skill_department(client, monkeypatch):
    from app.agents.models import DepartmentAnalysisAgent
    from app.agents.router import AnalysisAgentUpsertRequest, list_analysis_agents, upsert_analysis_agent
    from app.auth.models import User
    from app.database import async_session_factory

    async with async_session_factory() as session:
        samplebrand_dept = OrgUnit(
            id="dept-samplebrand-content",
            name="示例品牌内容电商运营部",
            type="department",
            parent_id=None,
            path="/dept-samplebrand-content",
        )
        it_dept = OrgUnit(
            id="dept-it",
            name="信息技术部",
            type="department",
            parent_id=None,
            path="/dept-it",
        )
        aibp = User(
            id="aibp-owner",
            username="aibp-owner",
            name="AIBP Owner",
            role="aibp",
            department="信息技术部",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        owner = User(
            id="qiyingying-skill-owner",
            username="qiyingying",
            name="祁莹莹",
            role="dept_admin",
            department="示例品牌内容电商运营部",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        skill = Skill(
            id="samplebrand-weekly-video-diagnosis",
            name="示例品牌周度同主题视频消耗诊断",
            description="测试 Skill",
            department="示例品牌内容电商运营部",
            trigger_type="cron",
            trigger_expression="0 8 * * *",
            risk_level="R2",
            owner="aibp-owner",
            status="active",
            org_unit_id=samplebrand_dept.id,
        )
        session.add_all([
            samplebrand_dept,
            it_dept,
            aibp,
            owner,
            UserOrgMembership(user_id=aibp.id, org_unit_id=it_dept.id, membership_type="primary", is_manager=False),
            UserOrgMembership(user_id=owner.id, org_unit_id=samplebrand_dept.id, membership_type="primary", is_manager=True),
            skill,
            SkillMember(skill_id=skill.id, user_id=aibp.id, role="owner", granted_by="aibp-owner"),
        ])
        await session.commit()

    monkeypatch.setattr("app.agents.router.audit.log", AsyncMock())
    async with async_session_factory() as session:
        current_user = await session.get(User, "aibp-owner")
        result = await upsert_analysis_agent(
            request=None,
            body=AnalysisAgentUpsertRequest(
                id="samplebrand-same-topic-video-diagnosis-agent",
                name="示例品牌同主题视频消耗诊断 Agent",
                department="示例品牌内容电商运营部",
                owner_query="祁莹莹",
                editor_queries=["祁莹莹"],
                skill_id="samplebrand-weekly-video-diagnosis",
                prompt_version="analysis_v1",
                dimensions=["首屏钩子", "商品露出", "行动引导"],
            ),
            current_user=current_user,
            db=session,
        )
        listed = await list_analysis_agents(
            request=None,
            department="示例品牌内容电商运营部",
            current_user=current_user,
            db=session,
        )
        await session.commit()

    assert result["ok"] is True
    assert result["agent"]["department_id"] == "dept-samplebrand-content"
    assert [item["id"] for item in listed["items"]] == ["samplebrand-same-topic-video-diagnosis-agent"]
    async with async_session_factory() as session:
        row = await session.get(DepartmentAnalysisAgent, "samplebrand-same-topic-video-diagnosis-agent")
        member = await session.get(SkillMember, ("samplebrand-weekly-video-diagnosis", "qiyingying-skill-owner"))
        assert row is not None
        assert row.created_by == "aibp-owner"
        assert member is not None
        assert member.role == "editor"


@pytest.mark.asyncio
async def test_codex_authorize_login_redirect_roundtrip_uses_next_query(client):
    from app.database import async_session_factory

    raw_token = "test-codex-login-next-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex_next_user",
            username="codex_next_user",
            name="Codex Next 用户",
            role="observer",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        cli_session = CodexCliSession(
            id="cli_next_user_seed",
            user_id=user.id,
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([user, cli_session])
        await session.commit()

    intent_resp = await client.post(
        "/api/codex/auth/login-intent",
        json={
            "redirect_uri": codex_service.POLL_REDIRECT_URI,
            "code_challenge": "plain-verifier",
            "code_challenge_method": "plain",
            "state": "state-next-test",
            "device_name": "pytest",
        },
    )
    assert intent_resp.status_code == 200
    authorize_path = intent_resp.json()["authorize_url"].split("http://test")[-1]

    unauth_resp = await client.get(authorize_path, follow_redirects=False)
    assert unauth_resp.status_code == 302
    location = unauth_resp.headers["location"]
    assert "/login?next=" in location
    assert "http%3A" not in location
    assert "%2Fapi%2Fcodex%2Fauth%2Fauthorize" in location

    client.cookies.set("skillforge_session", create_session_token("codex_next_user", 0))
    auth_resp = await client.get(authorize_path)
    assert auth_resp.status_code == 200
    assert "SkillForge 授权完成" in auth_resp.text

    async with async_session_factory() as session:
        row = await session.get(codex_service.CodexLoginIntent, intent_resp.json()["login_intent_id"])
        assert row.user_id == "codex_next_user"


@pytest.mark.asyncio
async def test_codex_admin_usage_summary_and_events(client):
    from app.database import async_session_factory

    raw_token = "test-codex-admin-usage-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex_usage_user",
            username="codex_usage_user",
            name="Codex Usage 用户",
            role="observer",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        cli_session = CodexCliSession(
            id="cli_usage_user",
            user_id=user.id,
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        mcp_audit = CodexMcpCallAudit(
            request_id="req_usage_user",
            proof_id="proof_usage_user",
            user_id=user.id,
            skill_id="skill-usage",
            server="skillforge",
            tool="skillforge_org_search_users",
            data_scope="org.users",
            run_mode="mcp_cli",
            dry_run=True,
            ok=True,
            detail_json={"source": "pytest"},
        )
        session.add_all([user, cli_session, mcp_audit])
        await session.commit()

    cap_resp = await client.get("/api/codex/capabilities", headers={"Authorization": f"Bearer {raw_token}"})
    assert cap_resp.status_code == 200
    project_caps = cap_resp.json()["project"]
    assert project_caps["org_resolve_api"] == "/api/codex/projects/org/resolve"
    assert project_caps["generation_recipes"]["short-video-analysis"]["default_project_id"] == "short-video-analysis-mvp"
    assert project_caps["visual_analysis"]["video_metadata"]["role"] == "material_a|material_b"
    assert "uploadAsset" in project_caps["gateway_sdk"]["methods"]

    summary_resp = await client.get("/api/codex/admin/usage-summary?days=30")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["plugin_users"] >= 1
    assert summary["mcp_calls"] >= 1
    assert any(item["tool"] == "skillforge_org_search_users" for item in summary["top_tools"])
    assert any(item["user_id"] == "codex_usage_user" and item["user_name"] == "Codex Usage 用户" for item in summary["top_users"])

    events_resp = await client.get("/api/codex/admin/usage-events?days=30&page_size=20")
    assert events_resp.status_code == 200
    events = events_resp.json()["items"]
    assert any(item["action"] == "codex.capabilities" for item in events)
    assert any(item["tool"] == "skillforge_org_search_users" for item in events)
    assert any(item["user_id"] == "codex_usage_user" and item["user_name"] == "Codex Usage 用户" for item in events)

    users_resp = await client.get("/api/codex/admin/users")
    assert users_resp.status_code == 200
    users = users_resp.json()["items"]
    assert any(item["user_id"] == "codex_usage_user" and item["user_name"] == "Codex Usage 用户" for item in users)

    async with async_session_factory() as session:
        audit_row = (
            await session.execute(
                codex_service.select(AuditLog).where(
                    AuditLog.user_id == "codex_usage_user",
                    AuditLog.action == "codex.capabilities",
                )
            )
        ).scalar_one()
    assert audit_row.detail["cli_session_id"] == "cli_usage_user"


@pytest.mark.asyncio
async def test_codex_project_org_resolve_returns_manifest_defaults(client):
    from app.database import async_session_factory

    raw_token = "test-project-org-resolve-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="project_org_actor",
            username="project_org_actor",
            name="Project Org Actor",
            role="aibp",
            department="示例品牌内容电商运营部",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        owner = codex_service.User(
            id="project_org_owner",
            username="project_org_owner",
            name="祁莹莹",
            role="observer",
            department="示例品牌内容电商运营部",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        org = OrgUnit(id="project_org_samplebrand", name="示例品牌内容电商运营部", type="department", path="/project_org_samplebrand")
        cli_session = CodexCliSession(
            id="cli_project_org_resolve",
            user_id=user.id,
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([
            user,
            owner,
            org,
            UserOrgMembership(user_id=user.id, org_unit_id=org.id, membership_type="primary", is_manager=False),
            UserOrgMembership(user_id=owner.id, org_unit_id=org.id, membership_type="primary", is_manager=False),
            cli_session,
        ])
        await session.commit()

    resp = await client.post(
        "/api/codex/projects/org/resolve",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"department": "示例品牌内容电商运营部", "owner": "祁莹莹", "visibility": "company"},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["department"]["id"] == "project_org_samplebrand"
    assert payload["owner"]["user_id"] == "project_org_owner"
    assert payload["manifest_defaults"]["visibility"] == "company"
    assert payload["permissions"]["current_user_can_submit_project"] is True

    async with async_session_factory() as session:
        other_owner = codex_service.User(
            id="project_org_other_owner",
            username="project_org_other_owner",
            name="跨部门祁莹莹",
            role="observer",
            department="其他部门",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        session.add(other_owner)
        await session.commit()

    cross_resp = await client.post(
        "/api/codex/projects/org/resolve",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"department": "示例品牌内容电商运营部", "owner": "跨部门祁莹莹", "visibility": "company"},
    )
    assert cross_resp.status_code == 200
    cross_payload = cross_resp.json()
    assert cross_payload["department"]["id"] == "project_org_samplebrand"
    assert cross_payload["owner"] is None
    assert cross_payload["permissions"]["owner_will_have_edit"] is False


@pytest.mark.asyncio
async def test_codex_project_run_asset_upload_list_and_trace(client, monkeypatch, tmp_path):
    from app.database import async_session_factory
    from app.projects import service as project_service

    monkeypatch.setattr(project_service, "PROJECT_RUN_ASSETS_DIR", tmp_path / "project-run-assets")
    raw_token = "test-project-run-asset-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="project_asset_actor",
            username="project_asset_actor",
            name="Project Asset Actor",
            role="aibp",
            department="信息技术部",
            state="active",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        project = Project(
            id="codex_asset_project",
            name="Codex Asset Project",
            type="web_static",
            department="信息技术部",
            department_id="demo-department",
            owner_user_id=user.id,
            visibility="company",
            status="published",
            entry_url="https://example.com/project",
            created_at=codex_service.now_bjt(),
            updated_at=codex_service.now_bjt(),
        )
        run = ProjectRun(
            id="prun_codex_asset",
            project_id=project.id,
            user_id=user.id,
            department="信息技术部",
            department_id="demo-department",
            status="opening",
            input_snapshot={"source": "codex-asset-test"},
            created_at=codex_service.now_bjt(),
            started_at=codex_service.now_bjt(),
            last_heartbeat_at=codex_service.now_bjt(),
            updated_at=codex_service.now_bjt(),
        )
        cli_session = CodexCliSession(
            id="cli_project_asset",
            user_id=user.id,
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([user, project, run, cli_session])
        await session.commit()

    upload_resp = await client.post(
        "/api/codex/projects/runs/prun_codex_asset/assets",
        headers={"Authorization": f"Bearer {raw_token}"},
        data={
            "metadata_json": json.dumps(
                {
                    "asset_type": "visual_frame",
                    "role": "material_a",
                    "material_role": "material_a",
                    "frame_time": 2,
                    "frame_label": "opening",
                    "source_file": "demo-a.mp4",
                },
                ensure_ascii=False,
            )
        },
        files={"file": ("opening-frame.png", b"pngdata", "image/png")},
    )
    assert upload_resp.status_code == 200, upload_resp.text
    uploaded = upload_resp.json()
    assert uploaded["ok"] is True
    asset = uploaded["asset"]
    assert asset["project_run_id"] == "prun_codex_asset"
    assert asset["source_kind"] == "media"
    assert asset["metadata"]["material_role"] == "material_a"
    assert uploaded["trace_url"].endswith("/projects/codex_asset_project?run_id=prun_codex_asset")

    list_resp = await client.get(
        "/api/codex/projects/runs/prun_codex_asset/assets",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert list_resp.status_code == 200
    listed = list_resp.json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == asset["id"]

    logs_resp = await client.get(
        "/api/codex/projects/runs/prun_codex_asset/logs",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert logs_resp.status_code == 200
    logs = logs_resp.json()
    assert logs["counts"]["asset_events_total"] == 1
    assert any(row.get("request_id") == f"asset:{asset['id']}" for row in logs["ingress_events"])

    async with async_session_factory() as session:
        asset_model = await session.get(ProjectRunAsset, asset["id"])
    assert asset_model is not None
    assert asset_model.metadata_json["frame_label"] == "opening"


@pytest.mark.asyncio
async def test_codex_skill_list_department_scope_uses_unified_access(client):
    from app.database import async_session_factory

    raw_token = "test-department-scope-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex_dept_user",
            username="codex_dept_user",
            name="部门 Skill 用户",
            role="observer",
            can_view_all=False,
            state="active",
            department="部门 A",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        org = OrgUnit(id="codex_dept_a", name="部门 A", type="department", path="/codex_dept_a")
        membership = UserOrgMembership(
            user_id=user.id,
            org_unit_id=org.id,
            membership_type="primary",
            is_manager=False,
        )
        dept_skill = Skill(
            id="codex-dept-skill",
            name="Codex Dept Skill",
            department="部门 A",
            visibility="department",
            org_unit_id=org.id,
            owner="someone_else",
            status="draft",
        )
        company_same_dept = Skill(
            id="codex-company-same-dept",
            name="Codex Company Same Dept Skill",
            department="部门 A",
            visibility="company",
            owner="someone_else",
            status="draft",
        )
        company_other_dept = Skill(
            id="codex-company-other-dept",
            name="Codex Company Other Dept Skill",
            department="其他部门",
            visibility="company",
            owner="someone_else",
            status="draft",
        )
        cli_session = CodexCliSession(
            id="cli_test_department_scope",
            user_id=user.id,
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([user, org, membership, dept_skill, company_same_dept, company_other_dept, cli_session])
        await session.commit()

    visible_resp = await client.get(
        "/api/codex/skills?scope=visible",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert visible_resp.status_code == 200
    visible_ids = {item["skill_id"] for item in visible_resp.json()["items"]}
    assert {"codex-dept-skill", "codex-company-same-dept", "codex-company-other-dept"} <= visible_ids

    department_resp = await client.get(
        "/api/codex/skills?scope=department",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert department_resp.status_code == 200
    department_ids = {item["skill_id"] for item in department_resp.json()["items"]}
    assert "codex-dept-skill" in department_ids
    assert "codex-company-same-dept" in department_ids
    assert "codex-company-other-dept" not in department_ids


@pytest.mark.asyncio
async def test_codex_skill_package_download_requires_read_and_includes_base_commit(client, monkeypatch, tmp_path):
    from app.database import async_session_factory
    from app.skills.core.git_service import GitService
    import app.codex.router as codex_router
    from app.codex import service as codex_service_module
    import app.skills.core.git_service as git_service_module
    from git import Repo

    repo = Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "test")
        cw.set_value("user", "email", "test@example.com")
    skill_id = "codex-installable-skill"
    skill_dir = tmp_path / skill_id
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: codex-installable-skill\n---\n# Skill\n", encoding="utf-8")
    (skill_dir / "scripts" / "main.py").write_text("print('{}')\n", encoding="utf-8")
    repo.git.add("-A")
    commit = repo.index.commit("init installable skill").hexsha
    test_git = GitService(str(tmp_path))
    monkeypatch.setattr(codex_router, "git_service", test_git)
    monkeypatch.setattr(codex_service_module, "git_service", test_git)
    monkeypatch.setattr(git_service_module, "git_service", test_git)

    raw_token = "test-codex-skill-package-token"
    denied_token = "test-codex-skill-package-denied-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex_pkg_user",
            username="codex_pkg_user",
            name="部门下载用户",
            role="observer",
            can_view_all=False,
            state="active",
            department="部门 P",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        denied = codex_service.User(
            id="codex_pkg_denied",
            username="codex_pkg_denied",
            name="无权下载用户",
            role="observer",
            can_view_all=False,
            state="active",
            department="其他部门",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        org = OrgUnit(id="codex_pkg_dept", name="部门 P", type="department", path="/codex_pkg_dept")
        membership = UserOrgMembership(
            user_id=user.id,
            org_unit_id=org.id,
            membership_type="primary",
            is_manager=False,
        )
        skill = Skill(
            id=skill_id,
            name="Codex Installable Skill",
            department="部门 P",
            visibility="department",
            org_unit_id=org.id,
            owner="someone_else",
            status="active",
            git_commit=commit,
        )
        session.add_all(
            [
                user,
                denied,
                org,
                membership,
                skill,
                CodexCliSession(
                    id="cli_test_codex_pkg",
                    user_id=user.id,
                    token_hash=codex_service.token_hash(raw_token),
                    scopes_json={"source": "test"},
                    permissions_rev_snapshot=0,
                    expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
                ),
                CodexCliSession(
                    id="cli_test_codex_pkg_denied",
                    user_id=denied.id,
                    token_hash=codex_service.token_hash(denied_token),
                    scopes_json={"source": "test"},
                    permissions_rev_snapshot=0,
                    expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
                ),
            ]
        )
        await session.commit()

    denied_resp = await client.get(
        f"/api/codex/skills/{skill_id}/package",
        headers={"Authorization": f"Bearer {denied_token}"},
    )
    assert denied_resp.status_code == 403

    resp = await client.get(
        f"/api/codex/skills/{skill_id}/package",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["X-SkillForge-Skill-Commit"] == commit
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        names = set(tar.getnames())
        skillforge_yaml = tar.extractfile("skillforge.yaml").read().decode("utf-8")

    assert {"SKILL.md", "scripts/main.py", "skillforge.yaml"} <= names
    assert f"skill_id: {skill_id}" in skillforge_yaml
    assert f"base_commit: {commit}" in skillforge_yaml


@pytest.mark.asyncio
async def test_codex_run_skill_uses_execute_permission_and_service(client, monkeypatch):
    from app.database import async_session_factory

    raw_token = "test-codex-run-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex_run_user",
            username="codex_run_user",
            name="Codex Run 用户",
            role="aibp",
            can_view_all=False,
            state="active",
            department="部门 R",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        org = OrgUnit(id="codex_run_dept", name="部门 R", type="department", path="/codex_run_dept")
        membership = UserOrgMembership(
            user_id=user.id,
            org_unit_id=org.id,
            membership_type="primary",
            is_manager=False,
        )
        skill = Skill(
            id="codex-run-skill",
            name="Codex Run Skill",
            department="部门 R",
            visibility="department",
            org_unit_id=org.id,
            owner="someone_else",
            status="draft",
        )
        cli_session = CodexCliSession(
            id="cli_test_codex_run",
            user_id=user.id,
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([user, org, membership, skill, cli_session])
        await session.commit()

    calls = []

    async def fake_execute_skill(**kwargs):
        calls.append(kwargs)
        return {"run_id": "run_codex", "status": "completed"}

    monkeypatch.setattr(
        "app.execution.execution_service.execution_service.execute_skill",
        fake_execute_skill,
    )

    resp = await client.post(
        "/api/codex/skills/codex-run-skill/run",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "params": {"_execution_backend": "bridge_script"},
            "sandbox": False,
            "run_mode": "manual_real",
            "parent_run_id": "parent_1",
            "data_provenance": [{"proof_id": "proof_1"}],
            "sample_used": False,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["run_id"] == "run_codex"
    assert calls == [{
        "skill_id": "codex-run-skill",
        "params": {"_execution_backend": "bridge_script"},
        "sandbox": False,
        "triggered_by": "codex:codex_run_user",
        "run_mode": "manual_real",
        "parent_run_id": "parent_1",
        "data_proofs": [{"proof_id": "proof_1"}],
        "sample_used": False,
    }]


@pytest.mark.asyncio
async def test_codex_run_skill_denies_unreadable_skill(client, monkeypatch):
    from app.database import async_session_factory

    raw_token = "test-codex-run-denied-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex_run_denied_user",
            username="codex_run_denied_user",
            name="Codex Run Denied 用户",
            role="observer",
            can_view_all=False,
            state="active",
            department="部门 A",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        skill = Skill(
            id="codex-denied-skill",
            name="Codex Denied Skill",
            department="部门 B",
            visibility="private",
            owner="someone_else",
            status="draft",
        )
        cli_session = CodexCliSession(
            id="cli_test_codex_run_denied",
            user_id=user.id,
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([user, skill, cli_session])
        await session.commit()

    execute_mock = AsyncMock(return_value={"run_id": "should_not_run"})
    monkeypatch.setattr(
        "app.execution.execution_service.execution_service.execute_skill",
        execute_mock,
    )

    resp = await client.post(
        "/api/codex/skills/codex-denied-skill/run",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"params": {}, "sandbox": False},
    )

    assert resp.status_code == 403
    execute_mock.assert_not_awaited()


def test_sf_real_mcp_test_passes_non_dry_run_payload(monkeypatch, tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")

    calls = {}

    def fake_create_debug_run(path, run_mode, input_json=None):
        calls["debug_run"] = (path, run_mode, input_json)
        return {"skill_id": "demo", "run_id": "debug_1", "run_mode": run_mode, "run_token": "token"}

    def fake_run_skill_script(path, run, *, real_mcp, extra_env=None, input_json=None):
        calls["script"] = (path, run, real_mcp, extra_env, input_json)
        return 0, "{}", ""

    class Response:
        status_code = 200

        def json(self):
            return {"run_id": "debug_1", "status": "completed"}

    monkeypatch.setattr(sf_cli, "create_debug_run", fake_create_debug_run)
    monkeypatch.setattr(sf_cli, "run_skill_script", fake_run_skill_script)
    monkeypatch.setattr(sf_cli, "base_url", lambda: "http://platform.test")
    monkeypatch.setattr(sf_cli.requests, "post", lambda *args, **kwargs: Response())

    with pytest.raises(SystemExit) as exc:
        sf_cli.run_real_skill(root, run_mode="local_debug", shop_id="default")

    assert exc.value.code == 0
    assert calls["debug_run"][2] == {"dry_run": False, "shop_id": "default"}
    assert calls["script"][2] is True
    assert calls["script"][3] == {"SKILLFORGE_SHOP_ID": "default", "SHOP_ID": "default"}
    assert calls["script"][4] == {"dry_run": False, "shop_id": "default"}


@pytest.mark.asyncio
async def test_debug_run_returns_runtime_token_for_intelligence(client):
    from app.database import async_session_factory

    raw_token = "test-debug-run-runtime-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="debug_runtime_user",
            username="debug_runtime_user",
            name="Debug Runtime 用户",
            role="aibp",
            state="active",
            department="示例品牌内容电商运营部",
            is_active=True,
            can_view_all=False,
            permissions_rev=0,
            must_change_password=False,
        )
        skill = Skill(
            id="debug-runtime-skill",
            name="Debug Runtime Skill",
            department="示例品牌内容电商运营部",
            visibility="company",
            owner=user.id,
            status="active",
        )
        cli_session = CodexCliSession(
            id="cli_debug_runtime",
            user_id=user.id,
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([user, skill, cli_session])
        await session.commit()

    resp = await client.post(
        "/api/codex/skill/debug-runs",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "skill_id": "debug-runtime-skill",
            "run_mode": "local_debug",
            "package_hash": "sha256:test",
            "manifest": {"base_commit": "d" * 40},
            "input": {"dry_run": False},
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_token"]
    assert body["runtime_run_token"]
    assert body["skill_git_commit_full"] == "d" * 40
    claims = verify_run_token(
        body["runtime_run_token"],
        skill_id="debug-runtime-skill",
        run_id=body["run_id"],
        skill_git_commit_full="d" * 40,
        require_intelligence=True,
    )
    assert claims["department"] == "示例品牌内容电商运营部"


def test_sdk_mcp_uses_platform_gateway_when_run_token(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "data": {"value": 1},
                "proof": {"id": "proof_1", "run_mode": "local_debug", "dry_run": True},
            }

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setenv("SKILLFORGE_PLATFORM_URL", "http://platform.test")
    monkeypatch.setenv("SKILLFORGE_RUN_TOKEN", "run-token")
    monkeypatch.setenv("SKILLFORGE_RUN_ID", "debug_1")
    monkeypatch.setattr("requests.post", fake_post)

    sf = SkillForge("demo-skill")
    data = sf.fetch_api("mcp://tmall_shop_overview", body={"shop_id": "s1"})

    assert data == {"value": 1}
    assert calls[0][0] == "http://platform.test/api/codex/mcp/call"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer run-token"
    payload = calls[0][1]["json"]
    assert payload["tool"] == "tmall_shop_overview"
    assert payload["skill_id"] == "demo-skill"


def test_sdk_mcp_forwards_explicit_dry_run(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "data": {"value": 1},
                "proof": {"id": "proof_1", "run_mode": "local_debug", "dry_run": False},
            }

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setenv("SKILLFORGE_PLATFORM_URL", "http://platform.test")
    monkeypatch.setenv("SKILLFORGE_RUN_TOKEN", "run-token")
    monkeypatch.setenv("SKILLFORGE_RUN_ID", "debug_1")
    monkeypatch.setattr("requests.post", fake_post)

    sf = SkillForge("demo-skill")
    data = sf.fetch_api("mcp://yuyidata_api_catalog", body={}, dry_run=False)

    assert data == {"value": 1}
    assert calls[0][1]["json"]["dry_run"] is False


@pytest.mark.asyncio
async def test_runtime_run_token_authenticates_for_gateway():
    class DB:
        async def get(self, model, key):
            if model is ExecutionRun:
                assert key == "run_1"
                return SimpleNamespace(id="run_1", skill_id="demo-skill", status="running", run_mode="scheduled_real")
            if model is Skill:
                assert key == "demo-skill"
                return SimpleNamespace(id="demo-skill", owner="runtime-owner", department="EC")
            if model is codex_service.User:
                assert key == "runtime-owner"
                return codex_service.User(
                    id="runtime-owner",
                    username="runtime-owner",
                    name="Runtime Owner",
                    role="aibp",
                    state="active",
                    is_active=True,
                )
            return None

        async def execute(self, stmt):
            class Result:
                def scalars(self):
                    return self

                def all(self):
                    return ["runtime-owner"]

            return Result()

    token = issue_run_token(skill_id="demo-skill", run_id="run_1")
    principal = await codex_service.authenticate_runtime_run_token(DB(), token)

    assert principal.skill_id == "demo-skill"
    assert principal.run_id == "run_1"
    assert principal.run_mode == "scheduled_real"
    assert principal.user_id == "runtime-owner"


@pytest.mark.asyncio
async def test_runtime_run_token_uses_active_skill_owner_member():
    from uuid import uuid4

    from app.auth.models import User
    from app.database import async_session_factory, engine

    suffix = uuid4().hex[:8]
    owner_id = f"runtime-owner-member-{suffix}"
    skill_id = f"runtime-owner-member-skill-{suffix}"
    run_id = f"runtime-owner-member-run-{suffix}"
    await engine.dispose()
    async with async_session_factory() as session:
        session.add_all([
            User(
                id=owner_id,
                username=owner_id,
                name="Runtime Owner Member",
                role="aibp",
                state="active",
                is_active=True,
                can_view_all=False,
                permissions_rev=0,
                must_change_password=False,
            ),
            Skill(
                id=skill_id,
                name="Runtime Owner Skill",
                department="EC",
                owner="missing-owner",
                visibility="department",
                status="active",
            ),
            SkillMember(skill_id=skill_id, user_id=owner_id, role="owner", granted_by="admin"),
            ExecutionRun(id=run_id, skill_id=skill_id, status="running", run_mode="scheduled_real"),
        ])
        await session.commit()

        token = issue_run_token(skill_id=skill_id, run_id=run_id)
        principal = await codex_service.authenticate_runtime_run_token(session, token)

    assert principal.user_id == owner_id
    assert principal.user is not None
    assert principal.user.id == owner_id


@pytest.mark.asyncio
async def test_runtime_run_token_requires_active_skill_runtime_actor():
    from uuid import uuid4

    from app.database import async_session_factory, engine

    suffix = uuid4().hex[:8]
    skill_id = f"runtime-missing-actor-skill-{suffix}"
    run_id = f"runtime-missing-actor-run-{suffix}"
    await engine.dispose()
    async with async_session_factory() as session:
        session.add_all([
            Skill(
                id=skill_id,
                name="Runtime Missing Actor Skill",
                department="EC",
                owner="missing-owner",
                visibility="department",
                status="active",
            ),
            ExecutionRun(id=run_id, skill_id=skill_id, status="running", run_mode="scheduled_real"),
        ])
        await session.commit()

        token = issue_run_token(skill_id=skill_id, run_id=run_id)
        with pytest.raises(AppError) as exc:
            await codex_service.authenticate_runtime_run_token(session, token)

    assert exc.value.code == "RUNTIME_ACTOR_NOT_FOUND"


@pytest.mark.asyncio
async def test_runtime_builtin_cloud_video_mcp_uses_skill_actor(monkeypatch):
    from app.auth.models import User

    captured = {}

    class DB:
        async def get(self, model, key):
            if model is Skill:
                return SimpleNamespace(id=key, department="EC", visibility="department")
            if model is User and key == "runtime-owner":
                return User(
                    id="runtime-owner",
                    username="runtime-owner",
                    name="Runtime Owner",
                    role="system_admin",
                    state="active",
                    is_active=True,
                )
            return None

        async def execute(self, stmt):
            class Result:
                def scalars(self):
                    return self

                def all(self):
                    return ["runtime-owner"]

            return Result()

        def add(self, row):
            captured["audit_row"] = row

        async def flush(self):
            return None

    async def fake_cloud_video_daily(args):
        captured["args"] = args
        return {"people": [], "daily_video_rows": []}

    monkeypatch.setattr(
        codex_service,
        "_load_tool_registry",
        lambda: {
            "skillforge_cloud_video_daily_person_video_report": SimpleNamespace(
                write=False,
                platform="cloud_video",
                data_scope="cloud_video.daily_person_video_report",
            )
        },
    )
    monkeypatch.setattr(
        codex_service.codex_cloud_video,
        "builtin_cloud_video_daily_person_video_report",
        fake_cloud_video_daily,
    )
    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())

    result = await codex_service.call_mcp_tool(
        DB(),
        codex_service.RuntimePrincipal(
            skill_id="demo-skill",
            run_id="run_1",
            run_mode="scheduled_real",
            claims={"instance_id": "node-1"},
        ),
        server="skillforge",
        tool="skillforge_cloud_video_daily_person_video_report",
        arguments={"start_date": "2026-06-01", "end_date": "2026-06-07"},
        skill_id="demo-skill",
        run_id="run_1",
        run_mode="scheduled_real",
        dry_run=True,
        idempotency_key=None,
    )

    assert result["ok"] is True
    assert result["proof"]["call_source"] == "skill_runtime"
    assert result["proof"]["user_id"] == "runtime-owner"
    assert captured["args"]["start_date"] == "2026-06-01"


@pytest.mark.asyncio
async def test_mcp_gateway_injects_yuyidata_backend_config(monkeypatch):
    rows = [
        SimpleNamespace(key="yuyidata.base_url", value="https://api.test"),
        SimpleNamespace(key="yuyidata.app_key", value="backend-key"),
    ]

    class Result:
        def scalars(self):
            return self

        def all(self):
            return rows

    class DB:
        async def get(self, model, key):
            if model is Skill:
                return SimpleNamespace(id=key, department="EC")
            return None

        async def execute(self, stmt):
            return Result()

        def add(self, row):
            return None

        async def flush(self):
            return None

    principal = codex_service.RuntimePrincipal(
        skill_id="demo-skill",
        run_id="run_1",
        run_mode="scheduled_real",
        user=codex_service.User(
            id="runtime-owner",
            username="runtime-owner",
            name="Runtime Owner",
            role="system_admin",
            state="active",
            is_active=True,
        ),
        user_id="runtime-owner",
        claims={"instance_id": "node-1"},
    )
    monkeypatch.setattr(
        codex_service,
        "_load_tool_registry",
        lambda: {
            "yuyidata_api_catalog": SimpleNamespace(
                write=False,
                platform="yuyidata",
                data_scope="yuyidata.customer_service",
            )
        },
    )
    monkeypatch.setattr(codex_service, "tool_script_for_name", lambda tool: codex_service.Path(__file__))
    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())
    captured = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs["env"]
        captured["request"] = json.loads(kwargs["input"])
        return SimpleNamespace(
            returncode=0,
            stdout=json_line({"result": {"content": [{"text": "{\"ok\": true}"}]}}),
            stderr="",
        )

    monkeypatch.setattr(codex_service.subprocess, "run", fake_run)

    result = await codex_service.call_mcp_tool(
        DB(),
        principal,
        server="skillforge",
        tool="yuyidata_api_catalog",
        arguments={},
        skill_id="demo-skill",
        run_id="run_1",
        run_mode="scheduled_real",
        dry_run=True,
        idempotency_key=None,
    )

    args = captured["request"]["params"]["arguments"]
    assert result["ok"] is True
    assert args["baseUrl"] == "https://api.test"
    assert args["appKey"] == "backend-key"
    assert captured["env"]["YUYIDATA_APP_KEY"] == "backend-key"


@pytest.mark.asyncio
async def test_mcp_gateway_subprocess_does_not_block_event_loop(monkeypatch):
    class DB:
        async def get(self, model, key):
            if model is ExecutionRun:
                return SimpleNamespace(
                    id="run_1",
                    skill_id="demo-skill",
                    status="running",
                    run_mode="scheduled_real",
                    metadata_json={},
                )
            return SimpleNamespace(id=key, department="EC")

        def add(self, row):
            return None

        async def flush(self):
            return None

    principal = codex_service.RuntimePrincipal(
        skill_id="demo-skill",
        run_id="run_1",
        run_mode="scheduled_real",
        user=codex_service.User(
            id="runtime-owner",
            username="runtime-owner",
            name="Runtime Owner",
            role="system_admin",
            state="active",
            is_active=True,
        ),
        user_id="runtime-owner",
        claims={"instance_id": "node-1"},
    )

    monkeypatch.setattr(
        codex_service,
        "_load_tool_registry",
        lambda: {
            "tmall_sycm_item_rank_top": SimpleNamespace(
                write=False,
                platform="sycm",
                data_scope="sycm.item_rank",
            )
        },
    )
    monkeypatch.setattr(codex_service, "tool_script_for_name", lambda tool: codex_service.Path(__file__))

    def slow_run(*args, **kwargs):
        import time

        time.sleep(0.05)
        return SimpleNamespace(
            returncode=0,
            stdout=json_line({"result": {"content": [{"text": "{\"ok\": true}"}]}}),
            stderr="",
        )

    monkeypatch.setattr(codex_service.subprocess, "run", slow_run)
    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())
    ticked = False

    async def ticker():
        nonlocal ticked
        await asyncio.sleep(0.01)
        ticked = True

    result, _ = await asyncio.gather(
        codex_service.call_mcp_tool(
            DB(),
            principal,
            server="skillforge",
            tool="tmall_sycm_item_rank_top",
            arguments={"shop_id": "s1"},
            skill_id="demo-skill",
            run_id="run_1",
            run_mode="scheduled_real",
            dry_run=True,
            idempotency_key=None,
        ),
        ticker(),
    )

    assert ticked is True
    assert result["ok"] is True


def json_line(payload: dict) -> str:
    import json

    return json.dumps(payload) + "\n"


def test_mcp_stdout_parser_handles_json_line_separators():
    payload = {
        "result": {
            "content": [
                {"text": json.dumps({"value": "a\u2028b"}, ensure_ascii=False)}
            ]
        }
    }
    parsed = codex_service._parse_mcp_stdout(json.dumps(payload, ensure_ascii=False) + "\n")

    content_text = parsed["result"]["content"][0]["text"]
    assert json.loads(content_text)["value"] == "a\u2028b"


def test_mcp_catalog_includes_org_push_tools():
    user = SimpleNamespace(id="admin", role="system_admin", can_view_all=True)
    catalog = codex_service.mcp_catalog_for_user(user)
    tools = {
        tool["name"]: tool
        for tool in (catalog.get("servers") or [{}])[0].get("tools") or []
    }

    assert "skillforge_org_search_users" in tools
    assert tools["skillforge_org_search_users"]["meta"]["write"] is False
    assert "query" in tools["skillforge_org_search_users"]["inputSchema"]["properties"]
    assert "skillforge_dingtalk_send_work_notice" in tools
    assert tools["skillforge_dingtalk_send_work_notice"]["meta"]["write"] is True
    assert "markdown" in tools["skillforge_dingtalk_send_work_notice"]["inputSchema"]["properties"]
    assert "skillforge_qianchuan_video_content_analysis" in tools
    assert tools["skillforge_qianchuan_video_content_analysis"]["meta"]["write"] is False
    qianchuan_schema = tools["skillforge_qianchuan_video_content_analysis"]["inputSchema"]
    assert "material_id" in qianchuan_schema["properties"]
    assert "start_date" in qianchuan_schema["required"]
    assert "skillforge_agent_coverage" in tools
    assert tools["skillforge_agent_coverage"]["meta"]["write"] is False
    assert "department" in tools["skillforge_agent_coverage"]["inputSchema"]["properties"]
    assert "skillforge_ai_analyze" in tools
    assert tools["skillforge_ai_analyze"]["meta"]["write"] is False
    ai_schema = tools["skillforge_ai_analyze"]["inputSchema"]
    assert ai_schema["type"] == "object"
    assert "context_pack" in ai_schema["properties"]
    for keyword in ("oneOf", "anyOf", "allOf", "enum", "not"):
        assert keyword not in ai_schema
    assert "skillforge_raw_data_query" in tools
    assert tools["skillforge_raw_data_query"]["meta"]["write"] is False
    assert "source" in tools["skillforge_raw_data_query"]["inputSchema"]["properties"]
    assert "skillforge_run_analyze" in tools
    assert tools["skillforge_run_analyze"]["meta"]["write"] is False
    assert "run_id" in tools["skillforge_run_analyze"]["inputSchema"]["properties"]
    assert "yuyidata_upload_sentence" in tools
    assert tools["yuyidata_upload_sentence"]["meta"]["write"] is True
    assert "yuyidata_create_sessions_retrieve_task" in tools
    assert tools["yuyidata_create_sessions_retrieve_task"]["meta"]["write"] is True
    assert "yuyidata_check_comments" in tools
    assert tools["yuyidata_check_comments"]["meta"]["write"] is False
    assert "skillforge_execution_artifact_latest" in tools
    assert tools["skillforge_execution_artifact_latest"]["meta"]["write"] is False
    assert tools["skillforge_execution_artifact_latest"]["meta"]["data_scope"] == "execution.artifacts"
    assert "skill_id" in tools["skillforge_execution_artifact_latest"]["inputSchema"]["properties"]
    assert "skillforge_execution_artifact_summary" in tools
    assert "run_id" in tools["skillforge_execution_artifact_summary"]["inputSchema"]["properties"]
    assert "skillforge_data_capability_list" in tools
    assert tools["skillforge_data_capability_list"]["meta"]["data_scope"] == "data.capabilities"
    assert "platform" in tools["skillforge_data_capability_list"]["inputSchema"]["properties"]
    assert "skillforge_data_capability_latest" in tools
    assert "capability" in tools["skillforge_data_capability_latest"]["inputSchema"]["properties"]
    assert "skillforge_data_artifact_get" in tools
    assert "include_content" in tools["skillforge_data_artifact_get"]["inputSchema"]["properties"]
    assert "skillforge_samplebrand_cloud_video_data_latest" in tools
    assert tools["skillforge_samplebrand_cloud_video_data_latest"]["meta"]["platform"] == "cloud_video"
    assert tools["skillforge_samplebrand_cloud_video_data_latest"]["meta"]["data_scope"] == "cloud_video.samplebrand_weekly.cached_artifacts"
    assert "skillforge_samplebrand_cloud_video_data_get" in tools
    assert tools["skillforge_samplebrand_cloud_video_data_get"]["meta"]["write"] is False
    assert "skillforge_samplebrand_cloud_video_daily_analysis_input" in tools
    assert tools["skillforge_samplebrand_cloud_video_daily_analysis_input"]["meta"]["write"] is False
    assert "analysis_date" in tools["skillforge_samplebrand_cloud_video_daily_analysis_input"]["inputSchema"]["properties"]
    assert "skillforge_tmall_link_decline_data_latest" in tools
    assert tools["skillforge_tmall_link_decline_data_latest"]["meta"]["platform"] == "tmall"
    assert "skillforge_yuyidata_customer_service_data_latest" in tools
    assert tools["skillforge_yuyidata_customer_service_data_latest"]["meta"]["platform"] == "yuyidata"
    assert "skillforge_cloud_video_daily_person_video_report" in tools
    cloud_daily = tools["skillforge_cloud_video_daily_person_video_report"]
    assert cloud_daily["meta"]["platform"] == "cloud_video"
    assert cloud_daily["meta"]["data_scope"] == "cloud_video.daily_person_video_report"
    assert "start_date" in cloud_daily["inputSchema"]["properties"]
    assert "top_videos_per_person" in cloud_daily["inputSchema"]["properties"]
    assert "skillforge_cloud_video_accounts" in tools
    assert "skillforge_cloud_video_videos" in tools
    assert "search_type" in tools["skillforge_cloud_video_videos"]["inputSchema"]["properties"]
    assert "search_ids" in tools["skillforge_cloud_video_videos"]["inputSchema"]["properties"]
    assert "auto_page" in tools["skillforge_cloud_video_videos"]["inputSchema"]["properties"]
    assert "max_pages" in tools["skillforge_cloud_video_videos"]["inputSchema"]["properties"]
    assert "skillforge_cloud_video_visual_analysis" in tools
    visual_tool = tools["skillforge_cloud_video_visual_analysis"]
    assert visual_tool["meta"]["write"] is False
    assert visual_tool["meta"]["data_scope"] == "cloud_video.visual_analysis"
    assert "benchmark_videos" in visual_tool["inputSchema"]["properties"]
    assert "skillforge_cloud_video_material_report" in tools
    material_report = tools["skillforge_cloud_video_material_report"]
    assert material_report["meta"]["data_scope"] == "cloud_video.material_report"
    assert "search_type" in material_report["inputSchema"]["properties"]
    assert "skillforge_cloud_video_video_usage_report" in tools
    usage_report = tools["skillforge_cloud_video_video_usage_report"]
    assert usage_report["meta"]["data_scope"] == "cloud_video.video_usage_report"
    assert "data_type" in usage_report["inputSchema"]["properties"]
    assert "skillforge_cloud_video_audit_rejects" in tools
    audit_rejects = tools["skillforge_cloud_video_audit_rejects"]
    assert audit_rejects["meta"]["data_scope"] == "cloud_video.audit_rejects"
    assert "video_id" in audit_rejects["inputSchema"]["properties"]


def _fake_cloud_video_token() -> str:
    payload = {"exp": 4102444800, "sub": "test"}
    payload_part = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{payload_part}.sig"


@pytest.mark.asyncio
async def test_cloud_video_videos_passes_upload_scope_filters(monkeypatch):
    token = _fake_cloud_video_token()
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_ENABLED", True)
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_API_BASE_URL", "https://cloud-video.example")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_LOGIN_ACCOUNT", "test-account")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_PASSWORD", "test-password")
    cloud_video.invalidate_cloud_video_session_cache()

    def fake_post_form(config, path, body, headers=None):
        calls.append((path, body))
        if path == "/login/login":
            return 200, {"code": 1, "success": True, "data": {"token": token}}
        if path == "/api/video/query":
            assert body["searchType"] == 1
            assert body["searchIds"] == "60454,60453"
            assert body["systemAutoLabelType"] == "1"
            assert body["videoState"] == "10"
            assert body["videoType"] == 0
            return 200, {
                "code": 1,
                "success": True,
                "data": {
                    "pageNo": 1,
                    "pageSize": 24,
                    "total": 1,
                    "totalPage": 1,
                    "list": [{"videoId": 1, "name": "素材", "accountId": 60454, "accountName": "孟军丽"}],
                },
            }
        raise AssertionError(path)

    monkeypatch.setattr(cloud_video, "_post_form_sync", fake_post_form)

    result = await cloud_video.builtin_cloud_video_videos(
        {
            "search_type": 1,
            "search_ids": ["60454", "60453"],
            "system_auto_label_type": "1",
            "video_state": "10",
            "include_raw": True,
        }
    )

    assert result["ok"] is True
    assert result["query"]["search_type"] == 1
    assert result["query"]["search_ids"] == ["60454", "60453"]
    assert result["query"]["system_auto_label_type"] == "1"
    assert result["query"]["video_state"] == "10"
    assert result["query"]["video_type"] == 0
    assert calls[-1][0] == "/api/video/query"


@pytest.mark.asyncio
async def test_cloud_video_videos_auto_pages_inside_mcp_call(monkeypatch):
    token = _fake_cloud_video_token()
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_ENABLED", True)
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_API_BASE_URL", "https://cloud-video.example")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_LOGIN_ACCOUNT", "test-account")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_PASSWORD", "test-password")
    cloud_video.invalidate_cloud_video_session_cache()

    def fake_post_form(config, path, body, headers=None):
        calls.append((path, body))
        if path == "/login/login":
            return 200, {"code": 1, "success": True, "data": {"token": token}}
        if path == "/api/video/query":
            page = int(body["start"] / body["length"]) + 1
            return 200, {
                "code": 1,
                "success": True,
                "data": {
                    "pageNo": page,
                    "pageSize": body["length"],
                    "total": 5,
                    "totalPage": 3,
                    "list": [
                        {"videoId": page * 10 + index, "name": f"素材{page}-{index}"}
                        for index in range(2)
                    ],
                },
            }
        raise AssertionError(path)

    monkeypatch.setattr(cloud_video, "_post_form_sync", fake_post_form)

    result = await cloud_video.builtin_cloud_video_videos(
        {
            "page_size": 2,
            "auto_page": True,
            "max_pages": 2,
            "max_items": 4,
        }
    )

    video_calls = [body for path, body in calls if path == "/api/video/query"]
    assert [body["start"] for body in video_calls] == [0, 2]
    assert result["count"] == 4
    assert result["pagination"]["auto_page"] is True
    assert result["pagination"]["pages_read"] == 2
    assert result["pagination"]["truncated"] is True


@pytest.mark.asyncio
async def test_cloud_video_visual_analysis_uses_topic_benchmark_cache_and_redacts_urls(monkeypatch, tmp_path):
    token = _fake_cloud_video_token()
    calls: list[tuple[str, dict]] = []
    model_calls: list[list[dict]] = []

    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_ENABLED", True)
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_API_BASE_URL", "https://cloud-video.example")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_LOGIN_ACCOUNT", "test-account")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_PASSWORD", "test-password")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_VISUAL_CACHE_ROOT", str(tmp_path / "visual-cache"))
    cloud_video.invalidate_cloud_video_session_cache()

    videos = {
        "low-1": {"videoId": "low-1", "name": "低消耗1", "videoUrl": "https://cdn.example/low.mp4?token=secret", "duration": 10, "fileSize": 100},
        "high-1": {"videoId": "high-1", "name": "高质量1", "videoUrl": "https://cdn.example/high.mp4?token=secret", "duration": 12, "fileSize": 120},
    }

    def fake_post_form(config, path, body, headers=None):
        calls.append((path, body))
        if path == "/login/login":
            return 200, {"code": 1, "success": True, "data": {"token": token}}
        if path == "/api/video/query":
            item = videos.get(str(body.get("name")))
            return 200, {
                "code": 1,
                "success": True,
                "data": {"pageNo": 1, "total": 1 if item else 0, "totalPage": 1, "list": [item] if item else []},
            }
        raise AssertionError(path)

    async def fake_multimodal(system, content_parts, **kwargs):
        model_calls.append(content_parts)
        return {"summary": "首屏直接露出商品", "diagnosis": {"hook": "明确"}}

    monkeypatch.setattr(cloud_video, "_post_form_sync", fake_post_form)
    monkeypatch.setattr(cloud_video, "call_llm_multimodal", fake_multimodal)

    args = {
        "low_videos": [{"video_id": "low-1", "video_name": "低消耗1", "topic_key": "001经典｜美女口播"}],
        "benchmark_videos": [
            {
                "video_id": "high-1",
                "video_name": "高质量1",
                "topic_key": "001经典｜美女口播",
                "lifecycle_hint": {
                    "source": "qianchuan.video_content_analysis",
                    "click_total": 43719,
                    "click_peak": {"duration": 1, "duration_label": "01s", "value": 3187},
                    "script_opening": "开头直接给出利益点和下单理由",
                },
            },
            {"video_id": "high-1", "video_name": "高质量1", "topic_key": "001经典｜美女口播"},
        ],
    }
    first = await cloud_video.builtin_cloud_video_visual_analysis(args)
    second = await cloud_video.builtin_cloud_video_visual_analysis(args)

    assert first["status"] == "ok"
    assert first["counts"]["ok"] == 2
    assert second["counts"]["cache_hits"] == 2
    assert len(model_calls) == 2
    assert first["low_videos"]["low-1"]["media"]["delivery"] == "direct_url"
    encoded = json.dumps(first, ensure_ascii=False)
    assert "token=secret" not in encoded
    assert "videoUrl" not in encoded
    assert "001经典｜美女口播::high-1" in first["benchmark_videos"]
    benchmark_prompt = json.dumps(model_calls[1], ensure_ascii=False)
    assert "千川点击生命周期摘要" in benchmark_prompt
    assert "click_peak" in benchmark_prompt
    assert "01s" in benchmark_prompt
    assert "开头直接给出利益点" in benchmark_prompt


@pytest.mark.asyncio
async def test_cloud_video_visual_analysis_isolates_video_query_failures(monkeypatch, tmp_path):
    token = _fake_cloud_video_token()
    calls: list[tuple[str, dict]] = []
    model_calls: list[list[dict]] = []

    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_ENABLED", True)
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_API_BASE_URL", "https://cloud-video.example")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_LOGIN_ACCOUNT", "test-account")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_PASSWORD", "test-password")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_VISUAL_CACHE_ROOT", str(tmp_path / "visual-cache"))
    cloud_video.invalidate_cloud_video_session_cache()

    videos = {
        "low-1": {"videoId": "low-1", "name": "低消耗1", "videoUrl": "https://cdn.example/low.mp4", "duration": 10, "fileSize": 100},
    }

    def fake_post_form(config, path, body, headers=None):
        calls.append((path, body))
        if path == "/login/login":
            return 200, {"code": 1, "success": True, "data": {"token": token}}
        if path == "/api/video/query":
            if body.get("name") == "high-busy":
                return 200, {"code": 0, "success": False, "msg": "当前访问人数较多，请稍后再试"}
            item = videos.get(str(body.get("name")))
            return 200, {
                "code": 1,
                "success": True,
                "data": {"pageNo": 1, "total": 1 if item else 0, "totalPage": 1, "list": [item] if item else []},
            }
        raise AssertionError(path)

    async def fake_multimodal(system, content_parts, **kwargs):
        model_calls.append(content_parts)
        return {"summary": "首屏直接露出商品", "diagnosis": {"hook": "明确"}}

    monkeypatch.setattr(cloud_video, "_post_form_sync", fake_post_form)
    monkeypatch.setattr(cloud_video, "call_llm_multimodal", fake_multimodal)

    result = await cloud_video.builtin_cloud_video_visual_analysis(
        {
            "low_videos": [{"video_id": "low-1", "video_name": "低消耗1", "topic_key": "001经典｜美女口播"}],
            "benchmark_videos": [{"video_id": "high-busy", "video_name": "高质量忙碌", "topic_key": "001经典｜美女口播"}],
        }
    )

    assert result["status"] == "partial"
    assert result["counts"]["ok"] == 1
    assert result["counts"]["skipped_or_failed"] == 1
    assert result["missing"][0]["reason"] == "video_query_failed"
    assert result["missing"][0]["error_code"] == "MCP_CALL_FAILED"
    assert len(model_calls) == 1


@pytest.mark.asyncio
async def test_cloud_video_material_report_matches_frontend_endpoint(monkeypatch):
    token = _fake_cloud_video_token()
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_ENABLED", True)
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_API_BASE_URL", "https://cloud-video.example")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_LOGIN_ACCOUNT", "test-account")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_PASSWORD", "test-password")
    cloud_video.invalidate_cloud_video_session_cache()

    def fake_post_form(config, path, body, headers=None):
        calls.append((path, body))
        if path == "/login/login":
            return 200, {"code": 1, "success": True, "data": {"token": token}}
        if path == "/api/dy/video-material-relation/query-video-relation-material-report":
            assert body["type"] == 1
            assert body["searchType"] == 1
            assert body["searchIds"] == "60454"
            assert body["statCostStartDate"] == "2026-05-11"
            assert body["statCostEndDate"] == "2026-06-09"
            assert body["platformType"] == 2
            return 200, {
                "code": 1,
                "success": True,
                "data": [
                    {
                        "accountId": 60454,
                        "accountName": "孟军丽",
                        "materialCount": 150,
                        "statCost": 1234.5,
                        "items": [
                            {
                                "labelName": "千川低质素材",
                                "materialCount": 3,
                                "materialCountRatio": 2,
                                "statCost": 12.3,
                                "statCostRatio": 1,
                            }
                        ],
                    }
                ],
            }
        raise AssertionError(path)

    monkeypatch.setattr(cloud_video, "_post_form_sync", fake_post_form)

    result = await cloud_video.builtin_cloud_video_material_report(
        {"start_date": "2026-05-11", "end_date": "2026-06-09", "search_type": 1, "search_ids": ["60454"]}
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["items"][0]["account_name"] == "孟军丽"
    assert result["items"][0]["material_count"] == 150
    assert result["items"][0]["items"][0]["label_name"] == "千川低质素材"
    assert calls[-1][0] == "/api/dy/video-material-relation/query-video-relation-material-report"


@pytest.mark.asyncio
async def test_cloud_video_video_usage_report_matches_frontend_endpoint(monkeypatch):
    token = _fake_cloud_video_token()
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_ENABLED", True)
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_API_BASE_URL", "https://cloud-video.example")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_LOGIN_ACCOUNT", "test-account")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_PASSWORD", "test-password")
    cloud_video.invalidate_cloud_video_session_cache()

    def fake_post_form(config, path, body, headers=None):
        calls.append((path, body))
        if path == "/login/login":
            return 200, {"code": 1, "success": True, "data": {"token": token}}
        if path == "/api/video/query-video-report":
            assert body["dataType"] == 3
            assert body["topType"] == 2
            assert body["type"] == 0
            assert body["videoType"] == 0
            assert body["startDate"] == "2026-05-11"
            assert body["endDate"] == "2026-06-09"
            assert body["idStr"] == "4016"
            assert body["typeIdStr"] == ""
            assert body["labelIdsStr"] == ""
            assert body["queryType"] == 0
            return 200, {
                "code": 1,
                "success": True,
                "data": {
                    "videoReportDetailVos": [
                        {
                            "tId": 4016,
                            "name": "示例品牌团队",
                            "uploadUserCount": 21,
                            "uploadCount": 15039,
                            "downloadCount": 100,
                            "numberOfPush": 20,
                            "numberOfJianying": 18,
                            "burstNumber": 3,
                        }
                    ],
                    "videoReportDateVos": [{"name": "示例品牌团队", "videoReportDateCalendarVos": [{"date": "2026-06-09", "count": 250}]}],
                    "videoProportionVos": [{"name": "示例品牌团队", "value": 15039}],
                },
            }
        raise AssertionError(path)

    monkeypatch.setattr(cloud_video, "_post_form_sync", fake_post_form)

    result = await cloud_video.builtin_cloud_video_video_usage_report(
        {"start_date": "2026-05-11", "end_date": "2026-06-09", "data_type": 3, "ids": ["4016"]}
    )

    assert result["ok"] is True
    assert result["summary"]["upload_count"] == 15039
    assert result["details"][0]["name"] == "示例品牌团队"
    assert result["details"][0]["upload_user_count"] == 21
    assert calls[-1][0] == "/api/video/query-video-report"


@pytest.mark.asyncio
async def test_cloud_video_audit_rejects_matches_frontend_endpoint(monkeypatch):
    token = _fake_cloud_video_token()
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_ENABLED", True)
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_API_BASE_URL", "https://cloud-video.example")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_LOGIN_ACCOUNT", "test-account")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_PASSWORD", "test-password")
    cloud_video.invalidate_cloud_video_session_cache()

    def fake_post_form(config, path, body, headers=None):
        calls.append((path, body))
        if path == "/login/login":
            return 200, {"code": 1, "success": True, "data": {"token": token}}
        if path == "/api/video/get-audit-reject":
            assert body["length"] == 50
            assert body["start"] == 0
            assert body["videoId"] == "100715117"
            assert body["type"] == 2
            assert body["state"] == ""
            return 200, {
                "code": 1,
                "success": True,
                "data": {
                    "total": 1,
                    "list": [
                        {
                            "videoId": 100715117,
                            "type": 2,
                            "advertiserId": 123,
                            "advertiserName": "测试广告主",
                            "materialId": "m1",
                            "state": 0,
                            "rejectReasons": ["<p>涉及夸大宣传</p>"],
                            "suggestions": ["<p>弱化绝对化表述</p>"],
                            "refreshTime": "2026-06-09 10:00:00",
                        }
                    ],
                },
            }
        raise AssertionError(path)

    monkeypatch.setattr(cloud_video, "_post_form_sync", fake_post_form)

    result = await cloud_video.builtin_cloud_video_audit_rejects({"video_id": "100715117", "platform_type": 2})

    assert result["ok"] is True
    assert result["total"] == 1
    assert result["items"][0]["platform_name"] == "巨量千川"
    assert result["items"][0]["reject_reason_text"] == ["涉及夸大宣传"]
    assert result["items"][0]["suggestion_text"] == ["弱化绝对化表述"]
    assert calls[-1][0] == "/api/video/get-audit-reject"


@pytest.mark.asyncio
async def test_cloud_video_daily_person_video_report_redacts_credentials(monkeypatch):
    token = _fake_cloud_video_token()
    calls: list[tuple[str, dict, dict | None]] = []

    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_ENABLED", True)
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_API_BASE_URL", "https://cloud-video.example")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_LOGIN_ACCOUNT", "test-account")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_PASSWORD", "test-password")
    cloud_video.invalidate_cloud_video_session_cache()

    def fake_post_form(config, path, body, headers=None):
        calls.append((path, body, headers))
        if path == "/login/login":
            assert body["type"] == ""
            return 200, {"code": 1, "success": True, "data": {"token": token}}
        assert headers
        assert headers.get("token") == token
        assert headers.get("sign")
        if path == "/api/team/query":
            return 200, {
                "code": 1,
                "success": True,
                "data": [
                    {
                        "id": 1,
                        "name": "示例品牌团队",
                        "childrens": [
                            {
                                "id": 2,
                                "name": "打品一组",
                                "childrens": [
                                    {"id": 60454, "name": "孟军丽"},
                                    {"id": 60453, "name": "洪静雯"},
                                ],
                            }
                        ],
                    }
                ],
            }
        if path == "/api/report/query-video-report":
            is_detail = str(body.get("type")) == "1"
            if is_detail:
                return 200, {
                    "code": 1,
                    "success": True,
                    "data": {
                        "data": {
                            "total": 2,
                            "list": [
                                {
                                    "teamName": "示例品牌团队",
                                    "groupName": "打品一组",
                                    "accountId": 60454,
                                    "accountName": "孟军丽",
                                    "videoId": 96289777,
                                    "videoName": "战甲AA_以后他在说状态不好",
                                    "parentTypeName": "示例品牌",
                                    "typeName": "战甲三合一",
                                    "statCost": 100,
                                    "payOrderAmountAndPayOrderCouponAmount": 80,
                                    "convertCnt": 8,
                                    "showCnt": 1000,
                                    "clickCnt": 80,
                                    "totalPlay": 900,
                                    "playOverRate": 1.2,
                                },
                                {
                                    "teamName": "示例品牌团队",
                                    "groupName": "打品一组",
                                    "accountId": 60453,
                                    "accountName": "洪静雯",
                                    "videoId": 94037755,
                                    "videoName": "战甲AA_你的套套是镶金边了吗",
                                    "parentTypeName": "示例品牌",
                                    "typeName": "战甲三合一",
                                    "statCost": 200,
                                    "payOrderAmountAndPayOrderCouponAmount": 300,
                                    "convertCnt": 10,
                                    "showCnt": 2000,
                                    "clickCnt": 120,
                                    "totalPlay": 1500,
                                    "playOverRate": 3.1,
                                },
                            ],
                        },
                        "sum": {},
                    },
                }
            return 200, {
                "code": 1,
                "success": True,
                "data": {
                    "data": {
                        "total": 2,
                        "list": [
                            {
                                "teamName": "示例品牌团队",
                                "groupName": "打品一组",
                                "accountId": 60454,
                                "accountName": "孟军丽",
                                "statCost": 100,
                                "roi": 0.8,
                                "convertCnt": 8,
                            },
                            {
                                "teamName": "示例品牌团队",
                                "groupName": "打品一组",
                                "accountId": 60453,
                                "accountName": "洪静雯",
                                "statCost": 200,
                                "roi": 1.5,
                                "convertCnt": 10,
                            },
                        ],
                    },
                    "sum": {},
                },
            }
        raise AssertionError(path)

    monkeypatch.setattr(cloud_video, "_post_form_sync", fake_post_form)

    result = await cloud_video.builtin_cloud_video_daily_person_video_report(
        {
            "start_date": "2026-05-01",
            "end_date": "2026-05-01",
            "top_videos_per_person": 1,
            "low_videos_per_person": 1,
            "include_daily_rows": True,
        }
    )

    assert result["ok"] is True
    assert result["scope"]["people_count"] == 2
    assert result["daily"]["person_returned_rows"] == 2
    assert result["daily"]["video_returned_rows"] == 2
    assert result["daily"]["analysis_rows"] == 2
    assert result["daily"]["analysis_truncated"] is False
    assert result["daily"]["returned_rows_truncated"] is False
    assert result["people"][0]["account_name"] == "洪静雯"
    assert result["people"][0]["top_cost_videos"][0]["video_id"] == "94037755"
    assert result["people"][0]["top_cost_videos"][0]["daily_breakdown"][0]["date"] == "2026-05-01"
    text = json.dumps(result, ensure_ascii=False)
    assert "test-password" not in text
    assert token not in text
    assert "credential_location" in text


@pytest.mark.asyncio
async def test_cloud_video_daily_summary_uses_rows_not_returned(monkeypatch):
    token = _fake_cloud_video_token()

    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_ENABLED", True)
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_API_BASE_URL", "https://cloud-video.example")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_LOGIN_ACCOUNT", "test-account")
    monkeypatch.setattr(cloud_video.settings, "CLOUD_VIDEO_PASSWORD", "test-password")
    cloud_video.invalidate_cloud_video_session_cache()

    def fake_post_form(config, path, body, headers=None):
        if path == "/login/login":
            return 200, {"code": 1, "success": True, "data": {"token": token}}
        if path == "/api/team/query":
            return 200, {
                "code": 1,
                "success": True,
                "data": [{"id": 1, "name": "示例品牌团队", "childrens": [{"id": 60454, "name": "孟军丽"}]}],
            }
        if path == "/api/report/query-video-report":
            if str(body.get("type")) == "1":
                return 200, {
                    "code": 1,
                    "success": True,
                    "data": {
                        "data": {
                            "total": 3,
                            "list": [
                                {
                                    "teamName": "示例品牌团队",
                                    "accountId": 60454,
                                    "accountName": "孟军丽",
                                    "videoId": 1,
                                    "videoName": "低花费素材",
                                    "statCost": 10,
                                    "payOrderAmountAndPayOrderCouponAmount": 10,
                                    "showCnt": 100,
                                    "clickCnt": 10,
                                },
                                {
                                    "teamName": "示例品牌团队",
                                    "accountId": 60454,
                                    "accountName": "孟军丽",
                                    "videoId": 2,
                                    "videoName": "高花费素材",
                                    "statCost": 999,
                                    "payOrderAmountAndPayOrderCouponAmount": 99,
                                    "showCnt": 1000,
                                    "clickCnt": 50,
                                },
                                {
                                    "teamName": "示例品牌团队",
                                    "accountId": 60454,
                                    "accountName": "孟军丽",
                                    "videoId": 3,
                                    "videoName": "中花费素材",
                                    "statCost": 100,
                                    "payOrderAmountAndPayOrderCouponAmount": 200,
                                    "showCnt": 1000,
                                    "clickCnt": 100,
                                },
                            ],
                        }
                    },
                }
            return 200, {
                "code": 1,
                "success": True,
                "data": {
                    "data": {
                        "total": 1,
                        "list": [
                            {
                                "teamName": "示例品牌团队",
                                "accountId": 60454,
                                "accountName": "孟军丽",
                                "statCost": 1109,
                            }
                        ],
                    }
                },
            }
        raise AssertionError(path)

    monkeypatch.setattr(cloud_video, "_post_form_sync", fake_post_form)

    result = await cloud_video.builtin_cloud_video_daily_person_video_report(
        {
            "start_date": "2026-05-01",
            "end_date": "2026-05-01",
            "max_video_rows": 10,
            "max_returned_video_rows": 1,
            "include_daily_rows": True,
            "top_videos_per_person": 1,
            "low_videos_per_person": 1,
        }
    )

    assert result["daily"]["video_returned_rows"] == 1
    assert result["daily"]["analysis_rows"] == 3
    assert result["daily"]["analysis_truncated"] is False
    assert result["daily"]["returned_rows_truncated"] is True
    assert result["people"][0]["top_cost_videos"][0]["video_id"] == "2"


def test_mcp_catalog_includes_market_rank_schema():
    user = SimpleNamespace(id="admin", role="system_admin", can_view_all=True)
    catalog = codex_service.mcp_catalog_for_user(user)
    tools = {
        tool["name"]: tool
        for tool in (catalog.get("servers") or [{}])[0].get("tools") or []
    }
    props = tools["tmall_sycm_market_rank"]["inputSchema"]["properties"]

    assert tools["tmall_sycm_market_rank"]["description"].startswith("获取生意参谋市场排行竞品")
    assert "parentCateId" in props
    assert "cateId" in props
    assert "cateFlag" in props
    assert "categoryPreset" in props
    assert "includeDetailRows" in props
    assert props["limit"]["maximum"] == 300


@pytest.mark.asyncio
async def test_builtin_execution_artifact_mcp_returns_summary(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.artifact_service import persist_execution_artifact
    from app.execution.models import ExecutionRun
    from app.skills.core.models import Skill

    monkeypatch.setattr(settings, "EXECUTION_ARTIFACT_ROOT", str(tmp_path / "execution-artifacts"))
    raw_token = "test-execution-artifact-mcp-token"
    async with async_session_factory() as session:
        admin = await session.get(codex_service.User, "admin")
        if admin is None:
            admin = codex_service.User(
                id="admin",
                username="admin",
                name="管理员",
                role="system_admin",
                can_view_all=True,
                state="active",
                department="AI小组",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            )
            session.add(admin)
        else:
            admin.role = "system_admin"
            admin.can_view_all = True
            admin.state = "active"
            admin.is_active = True
        session.add(
            Skill(
                id="artifact-skill",
                name="Artifact Skill",
                department="AI小组",
                visibility="company",
                status="active",
                approval_level=0,
            )
        )
        session.add(
            ExecutionRun(
                id="artifact-run-1",
                skill_id="artifact-skill",
                trigger_type="node_scheduler",
                run_mode="scheduled_real",
                status="completed",
            )
        )
        session.add(
            CodexCliSession(
                id="cli_test_execution_artifact_mcp",
                user_id="admin",
                token_hash=codex_service.token_hash(raw_token),
                scopes_json={"source": "test"},
                permissions_rev_snapshot=0,
                expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
            )
        )
        await session.flush()
        await persist_execution_artifact(
            session,
            run_id="artifact-run-1",
            skill_id="artifact-skill",
            kind="raw-output",
            payload={"collection_schema": "artifact_schema_v1", "rows": [{"id": 1}]},
            decision_log_id=12,
        )
        await session.commit()

    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())

    latest_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_execution_artifact_latest",
            "arguments": {"skill_id": "artifact-skill"},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert latest_resp.status_code == 200
    latest = latest_resp.json()["data"]
    assert latest["count"] == 1
    assert latest["items"][0]["run_id"] == "artifact-run-1"
    assert latest["items"][0]["schema"] == "artifact_schema_v1"
    assert latest["items"][0]["uncompressed_size_bytes"] > 0
    assert latest["items"][0]["sha256"]

    summary_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_execution_artifact_summary",
            "arguments": {"run_id": "artifact-run-1", "kind": "raw-output"},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert summary_resp.status_code == 200
    summary = summary_resp.json()["data"]
    assert summary["count"] == 1
    assert summary["items"][0]["summary"]["collection_schema"] == "artifact_schema_v1"


@pytest.mark.asyncio
async def test_builtin_data_capability_mcp_reads_samplebrand_tmall_and_yuyi_cached_artifacts(client, monkeypatch, tmp_path):
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.artifact_service import persist_execution_artifact
    from app.execution.models import ExecutionRun
    from app.skills.core.models import Skill

    monkeypatch.setattr(settings, "EXECUTION_ARTIFACT_ROOT", str(tmp_path / "execution-artifacts"))
    raw_token = "test-data-capability-mcp-token"
    async with async_session_factory() as session:
        admin = await session.get(codex_service.User, "admin")
        if admin is None:
            admin = codex_service.User(
                id="admin",
                username="admin",
                name="管理员",
                role="system_admin",
                can_view_all=True,
                state="active",
                department="AI小组",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            )
            session.add(admin)
        else:
            admin.role = "system_admin"
            admin.can_view_all = True
            admin.state = "active"
            admin.is_active = True
        session.add_all(
            [
                Skill(
                    id="samplebrand-weekly-video-diagnosis",
                    name="SampleBrand Cloud Video Collector",
                    department="示例品牌内容电商运营部",
                    visibility="company",
                    status="active",
                    approval_level=0,
                ),
                Skill(
                    id="tmall-link-decline-collector-v1",
                    name="Tmall Collector",
                    department="AI小组",
                    visibility="company",
                    status="active",
                    approval_level=0,
                ),
                Skill(
                    id="yuyidata-customer-service-collector-v1",
                    name="Yuyi Collector",
                    department="AI小组",
                    visibility="company",
                    status="active",
                    approval_level=0,
                ),
                ExecutionRun(
                    id="samplebrand-data-run-1",
                    skill_id="samplebrand-weekly-video-diagnosis",
                    trigger_type="node_scheduler",
                    run_mode="scheduled_real",
                    status="completed",
                ),
                ExecutionRun(
                    id="tmall-data-run-1",
                    skill_id="tmall-link-decline-collector-v1",
                    trigger_type="node_scheduler",
                    run_mode="scheduled_real",
                    status="completed",
                ),
                ExecutionRun(
                    id="yuyi-data-run-1",
                    skill_id="yuyidata-customer-service-collector-v1",
                    trigger_type="node_scheduler",
                    run_mode="scheduled_real",
                    status="completed",
                ),
                CodexCliSession(
                    id="cli_test_data_capability_mcp",
                    user_id="admin",
                    token_hash=codex_service.token_hash(raw_token),
                    scopes_json={"source": "test"},
                    permissions_rev_snapshot=0,
                    expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
                ),
            ]
        )
        await session.flush()
        await persist_execution_artifact(
            session,
            run_id="samplebrand-data-run-1",
            skill_id="samplebrand-weekly-video-diagnosis",
            kind="raw-output",
            payload={
                "raw_data": {
                    "collection_schema": "samplebrand_cloud_video_weekly_collection_v1",
                    "date_range": {"start_date": "2026-06-08", "end_date": "2026-06-09"},
                    "videos": [
                        {"id": "v1", "title": "示例品牌视频", "category_name": "001经典"},
                        {"id": "v2", "title": "非目标日视频", "category_name": "001经典"},
                    ],
                    "qianchuan_high_consumption_lifecycle": [
                        {
                            "video_id": "high-1",
                            "topic_key": "反正都要买套套",
                            "cloud_video": {"video_id": "high-1", "video_name": "高消耗参考"},
                            "qianchuan": {"summary": {"click_total": 199}},
                        },
                        {
                            "video_id": "high-2",
                            "topic_key": "拿出避孕套",
                            "cloud_video": {
                                "video_id": "high-2",
                                "video_name": "未命中生命周期高消耗参考",
                                "metrics": {"statCost": 1888.8, "clickCnt": 321, "clickRate": 4.2},
                            },
                            "lifecycle_available": False,
                            "qianchuan": {
                                "ok": False,
                                "error_code": "QIANCHUAN_MATERIAL_RESOLVE_FAILED",
                                "warnings": [
                                    {"endpoint": "material_search", "message": "浏览器 fetch_json 未返回响应状态"}
                                ],
                            },
                        }
                    ],
                    "daily_report": {
                        "daily": {"video_total_rows": 3},
                        "daily_video_rows": [
                            {"date": "2026-06-09", "video_id": "v1", "metrics": {"statCost": 233.3}},
                            {"date": "2026-06-09", "video_id": "v0", "metrics": {"statCost": 0}},
                            {"date": "2026-06-08", "video_id": "v2", "metrics": {"statCost": 999}},
                        ],
                    },
                }
            },
        )
        await persist_execution_artifact(
            session,
            run_id="tmall-data-run-1",
            skill_id="tmall-link-decline-collector-v1",
            kind="raw-output",
            payload={
                "collection_schema": "tmall_link_decline_collection_v1",
                "items": [{"item_id": "1", "uv": 100}],
            },
        )
        await persist_execution_artifact(
            session,
            run_id="yuyi-data-run-1",
            skill_id="yuyidata-customer-service-collector-v1",
            kind="raw-output",
            payload={
                "collection_schema": "yuyidata_customer_service_collection_v1",
                "sessions": [{"session_id": "s1", "summary": "need follow up"}],
            },
        )
        await session.commit()

    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())

    list_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_data_capability_list",
            "arguments": {},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert list_resp.status_code == 200
    capability_keys = {item["capability"] for item in list_resp.json()["data"]["items"]}
    assert "cloud_video.samplebrand_weekly.raw_collection" in capability_keys
    assert "tmall.link_decline.raw_collection" in capability_keys
    assert "yuyidata.customer_service.raw_collection" in capability_keys

    samplebrand_latest_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_samplebrand_cloud_video_data_latest",
            "arguments": {},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert samplebrand_latest_resp.status_code == 200
    samplebrand_latest = samplebrand_latest_resp.json()["data"]
    assert samplebrand_latest["capability"] == "cloud_video.samplebrand_weekly.raw_collection"
    assert samplebrand_latest["count"] == 1
    assert samplebrand_latest["items"][0]["run_id"] == "samplebrand-data-run-1"

    samplebrand_get_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_samplebrand_cloud_video_data_get",
            "arguments": {"run_id": "samplebrand-data-run-1", "include_content": True, "max_bytes": 2048},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert samplebrand_get_resp.status_code == 200
    samplebrand_get = samplebrand_get_resp.json()["data"]
    assert samplebrand_get["content_omitted"] is False
    assert samplebrand_get["content_json"]["raw_data"]["collection_schema"] == "samplebrand_cloud_video_weekly_collection_v1"
    assert samplebrand_get["content_json"]["raw_data"]["videos"][0]["id"] == "v1"

    samplebrand_daily_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_samplebrand_cloud_video_daily_analysis_input",
            "arguments": {"run_id": "samplebrand-data-run-1", "analysis_date": "2026-06-09"},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert samplebrand_daily_resp.status_code == 200
    samplebrand_daily = samplebrand_daily_resp.json()["data"]
    assert samplebrand_daily["analysis_date"] == "2026-06-09"
    assert [row["video_id"] for row in samplebrand_daily["daily_video_rows"]] == ["v1"]
    assert samplebrand_daily["videos_by_id"]["v1"]["title"] == "示例品牌视频"
    assert samplebrand_daily["qianchuan_high_consumption_lifecycle"][0]["video_id"] == "high-1"
    assert samplebrand_daily["qianchuan_lifecycle_by_video_id"]["high-1"]["qianchuan"]["summary"]["click_total"] == 199
    assert samplebrand_daily["qianchuan_lifecycle_by_topic"]["反正都要买套套"][0]["video_id"] == "high-1"
    assert samplebrand_daily["qianchuan_lifecycle_by_video_id"]["high-2"]["lifecycle_status"] == "unavailable"
    assert samplebrand_daily["qianchuan_lifecycle_by_video_id"]["high-2"]["cloud_video_proxy_metrics"]["cost"] == 1888.8
    assert samplebrand_daily["qianchuan_lifecycle_quality"]["attempted"] == 2
    assert samplebrand_daily["qianchuan_lifecycle_quality"]["available"] == 1
    assert samplebrand_daily["qianchuan_lifecycle_quality"]["failed"] == 1
    assert samplebrand_daily["source_counts"]["target_date_rows"] == 2
    assert samplebrand_daily["source_counts"]["qianchuan_lifecycle_rows"] == 2
    assert samplebrand_daily["source_counts"]["qianchuan_lifecycle_available_rows"] == 1
    assert samplebrand_daily["source_counts"]["qianchuan_lifecycle_failed_rows"] == 1
    assert samplebrand_daily["source_counts"]["excluded_zero_cost_rows"] == 1

    latest_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_tmall_link_decline_data_latest",
            "arguments": {},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert latest_resp.status_code == 200
    latest = latest_resp.json()["data"]
    assert latest["capability"] == "tmall.link_decline.raw_collection"
    assert latest["count"] == 1
    assert latest["items"][0]["run_id"] == "tmall-data-run-1"
    assert latest["items"][0]["schema"] == "tmall_link_decline_collection_v1"

    get_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_tmall_link_decline_data_get",
            "arguments": {"run_id": "tmall-data-run-1", "include_content": True, "max_bytes": 2048},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert get_resp.status_code == 200
    get_data = get_resp.json()["data"]
    assert get_data["content_omitted"] is False
    assert get_data["content_json"]["collection_schema"] == "tmall_link_decline_collection_v1"
    assert get_data["content_json"]["items"][0]["item_id"] == "1"

    yuyi_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_yuyidata_customer_service_data_latest",
            "arguments": {},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert yuyi_resp.status_code == 200
    yuyi = yuyi_resp.json()["data"]
    assert yuyi["capability"] == "yuyidata.customer_service.raw_collection"
    assert yuyi["items"][0]["run_id"] == "yuyi-data-run-1"
    assert yuyi["items"][0]["schema"] == "yuyidata_customer_service_collection_v1"


@pytest.mark.asyncio
async def test_builtin_agent_coverage_mcp_tool(client, monkeypatch):
    from app.database import async_session_factory
    from app.execution.models import OpenClawInstance
    import app.aiclaw.router as aiclaw_router

    raw_token = "test-agent-coverage-token"
    async with async_session_factory() as session:
        admin = await session.get(codex_service.User, "admin")
        if admin is None:
            admin = codex_service.User(
                id="admin",
                username="admin",
                name="管理员",
                role="system_admin",
                can_view_all=True,
                state="active",
                department="AI小组",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            )
            session.add(admin)
        else:
            admin.role = "system_admin"
            admin.can_view_all = True
            admin.state = "active"
            admin.is_active = True
        session.add_all([
            OpenClawInstance(
                id="coverage-runtime",
                name="EC 执行 Agent",
                department="EC",
                gateway_url="ws://runtime",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                agent_purpose="skill_runtime",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({"ops": ["run_agent_skill"]}),
            ),
            OpenClawInstance(
                id="coverage-platform-analysis",
                name="平台分析 Agent",
                department="AI",
                gateway_url="ws://analysis",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                is_platform_default=True,
                agent_purpose="analysis",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({"ops": ["intelligence.analyze"]}),
            ),
            OpenClawInstance(
                id="coverage-platform-training",
                name="平台训练 Agent",
                department="AI",
                gateway_url="ws://training",
                reload_hook_url="",
                reload_token="",
                is_active=True,
                is_platform_default=True,
                agent_purpose="training",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                    "training": {"gateway": True, "supported_tasks": ["lora"], "gpu_count": 1},
                }),
            ),
            CodexCliSession(
                id="cli_test_agent_coverage",
                user_id="admin",
                token_hash=codex_service.token_hash(raw_token),
                scopes_json={"source": "test"},
                permissions_rev_snapshot=0,
                expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
            ),
        ])
        await session.commit()

    monkeypatch.setattr(
        aiclaw_router.bridge_registry,
        "is_online",
        lambda instance_id: instance_id in {
            "coverage-runtime",
            "coverage-platform-analysis",
            "coverage-platform-training",
        },
    )
    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())

    resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_agent_coverage",
            "arguments": {"department": "EC"},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total"] == 1
    row = data["items"][0]
    assert row["department"] == "EC"
    assert row["status"] == "fallback"
    assert row["capabilities"]["skill_runtime"]["ready"] is True
    assert row["capabilities"]["analysis"]["fallback_ready"] is True
    assert row["capabilities"]["training"]["fallback_ready"] is True
    assert row["capabilities"]["training"]["flow"]["input_channels"] == [
        "training_job_manifest",
        "learning_artifact_dataset_package",
    ]
    assert row["capabilities"]["analysis"]["flow"]["fallback"] is True
    assert row["controlled_node_count"] == 1
    assert row["controlled_nodes"][0]["id"] == "coverage-runtime"
    assert row["controlled_nodes"][0]["input_contract_clear"] is True
    assert row["controlled_nodes"][0]["agent_contract"]["complete"] is True
    assert row["controlled_nodes"][0]["agent_contract"]["missing"] == []
    assert row["controlled_nodes"][0]["agent_contract"]["missing_ops"] == []
    assert row["controlled_nodes"][0]["agent_contract"]["required_ops_any"] == {
        "skill_runtime": ["run_agent_skill", "run_skill_script"],
    }
    assert row["controlled_nodes"][0]["agent_contract"]["available_control_ops"] == ["run_agent_skill"]
    assert row["controlled_nodes"][0]["agent_contract"]["lifecycle_ready"] is True
    assert row["controlled_nodes"][0]["agent_contract"]["flow_traceable"] is True
    assert row["controlled_nodes"][0]["agent_contract"]["lineage_required"] == [
        "decision_log",
        "execution_artifact",
        "execution_run",
    ]
    assert row["controlled_nodes"][0]["flow"]["skill_runtime"]["output_channels"] == [
        "execution_run",
        "decision_log",
        "execution_artifact",
    ]
    assert data["scope"]["payload_redacted"] is True
    assert "gateway_url" not in json.dumps(data, ensure_ascii=False)


@pytest.mark.asyncio
async def test_builtin_org_mcp_search_and_dingtalk_notice(client, monkeypatch):
    from app.database import async_session_factory

    raw_token = "test-org-mcp-token"
    async with async_session_factory() as session:
        admin = await session.get(codex_service.User, "admin")
        if admin is None:
            admin = codex_service.User(
                id="admin",
                username="admin",
                name="管理员",
                role="system_admin",
                can_view_all=True,
                state="active",
                department="AI小组",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            )
            session.add(admin)
        else:
            admin.role = "system_admin"
            admin.can_view_all = True
            admin.state = "active"
            admin.is_active = True
        user = codex_service.User(
            id="codex_org_user",
            username="codex_org_user",
            name="组织 MCP 用户",
            role="observer",
            can_view_all=False,
            state="active",
            department="组织测试部",
            dingtalk_user_id="dt_codex_org_user",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        org = OrgUnit(id="codex_org_unit", name="组织测试部", type="department", path="/codex_org_unit")
        membership = UserOrgMembership(
            user_id=user.id,
            org_unit_id=org.id,
            membership_type="primary",
            is_manager=False,
        )
        cli_session = CodexCliSession(
            id="cli_test_org_mcp",
            user_id="admin",
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([user, org, membership, cli_session])
        await session.commit()

    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())

    search_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_org_search_users",
            "arguments": {"query": "组织 MCP", "limit": 5},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert search_resp.status_code == 200
    search_data = search_resp.json()["data"]
    assert search_data["count"] == 1
    assert search_data["items"][0]["user_id"] == "codex_org_user"
    assert search_data["items"][0]["can_receive_dingtalk"] is True

    dry_run_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_dingtalk_send_work_notice",
            "arguments": {
                "user_ids": ["codex_org_user"],
                "title": "组织 MCP 验证",
                "markdown": "仅验证 dry-run",
            },
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert dry_run_resp.status_code == 200
    dry_run_data = dry_run_resp.json()["data"]
    assert dry_run_data["dryRun"] is True
    assert dry_run_data["queued"] == 0
    assert dry_run_data["recipient_count"] == 1

    missing_key_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_dingtalk_send_work_notice",
            "arguments": {
                "user_ids": ["codex_org_user"],
                "title": "组织 MCP 验证",
                "markdown": "缺少幂等键",
            },
            "run_mode": "mcp_cli",
            "dry_run": False,
        },
    )
    assert missing_key_resp.status_code == 400

    real_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_dingtalk_send_work_notice",
            "arguments": {
                "user_ids": ["codex_org_user"],
                "title": "组织 MCP 验证",
                "markdown": "真实入队",
            },
            "run_mode": "mcp_cli",
            "dry_run": False,
            "idempotency_key": "codex-org-mcp-test-key",
        },
    )
    assert real_resp.status_code == 200
    real_data = real_resp.json()["data"]
    assert real_data["queued"] == 1
    assert real_data["outbox_ids"]

    again_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_dingtalk_send_work_notice",
            "arguments": {
                "user_ids": ["codex_org_user"],
                "title": "组织 MCP 验证",
                "markdown": "重复幂等",
            },
            "run_mode": "mcp_cli",
            "dry_run": False,
            "idempotency_key": "codex-org-mcp-test-key",
        },
    )
    assert again_resp.status_code == 200
    again_data = again_resp.json()["data"]
    assert again_data["deduped"] is True
    assert again_data["queued"] == 0

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                codex_service.select(DingTalkOutbox).where(
                    DingTalkOutbox.related_type == "codex_mcp_dingtalk_notice",
                    DingTalkOutbox.related_id == "codex-org-mcp-test-key",
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].recipient_user_id == "dt_codex_org_user"


@pytest.mark.asyncio
async def test_builtin_platform_ai_and_raw_data_mcp_tools(client, monkeypatch):
    from app.collection.models import CollectionProof
    from app.database import async_session_factory

    raw_token = "test-platform-ai-raw-token"
    ai_prompts = []

    async def fake_call_llm(system, user, **kwargs):
        payload = json.loads(user)
        ai_prompts.append(payload["prompt"])
        assert kwargs["model_override"] == codex_service.PLATFORM_AI_MCP_MODEL
        assert kwargs["require_system_config"] is True
        assert kwargs["cost_context"]["skill_id"] == "codex_raw_skill"
        if isinstance(payload.get("context_pack"), dict) and "run" in payload["context_pack"]:
            assert payload["context_pack"]["run"]["id"] == "codex-raw-run-1"
            return "平台 AI 运行复盘"
        return "平台 AI 分析结果"

    async with async_session_factory() as session:
        admin = await session.get(codex_service.User, "admin")
        if admin is None:
            admin = codex_service.User(
                id="admin",
                username="admin",
                name="管理员",
                role="system_admin",
                can_view_all=True,
                state="active",
                department="AI小组",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            )
            session.add(admin)
        else:
            admin.role = "system_admin"
            admin.can_view_all = True
            admin.state = "active"
            admin.is_active = True
        session.add(Skill(
            id="codex_raw_skill",
            name="Codex Raw Skill",
            department="AI小组",
            visibility="company",
            owner="admin",
            status="active",
            trigger_type="manual",
            risk_level="R1",
        ))
        session.add(ExecutionRun(
            id="codex-raw-run-1",
            skill_id="codex_raw_skill",
            trigger_type="manual",
            run_mode="manual_real",
            status="completed",
            metadata_json={"token": "raw-run-secret"},
        ))
        session.add(ExecutionStep(
            run_id="codex-raw-run-1",
            skill_id="codex_raw_skill",
            step_order=1,
            status="completed",
            input_data={"authorization": "Bearer should-not-leak"},
            output_data={"summary": "ok"},
        ))
        session.add(DecisionLog(
            run_id="codex-raw-run-1",
            skill_id="codex_raw_skill",
            input_snapshot={"query": "原始输入", "token": "decision-secret"},
            output_result={"summary": "原始输出"},
            suggested_action={"type": "todo"},
            approval_status="auto",
        ))
        session.add(CollectionProof(
            proof_id="codex-raw-proof-1",
            run_id="codex-raw-run-1",
            skill_id="codex_raw_skill",
            platform="tmall",
            shop_id="shop-1",
            data_scope="reviews",
            endpoint_family="item_reviews",
            warning_group="read",
            status="success",
            response_hash="hash-1",
            data_keys=["items"],
            row_count=2,
        ))
        session.add(CodexCliSession(
            id="cli_test_platform_ai_raw",
            user_id="admin",
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        ))
        await session.commit()

    monkeypatch.setattr(codex_service, "call_llm", fake_call_llm)
    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())

    ai_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_ai_analyze",
            "skill_id": "codex_raw_skill",
            "arguments": {
                "prompt": "Skill 运行摘要",
                "context_pack": {"run_id": "codex-raw-run-1"},
                "max_output_tokens": 1024,
            },
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert ai_resp.status_code == 200
    ai_data = ai_resp.json()["data"]
    assert ai_data["model"] == codex_service.PLATFORM_AI_MCP_MODEL
    assert ai_data["max_context_tokens"] == codex_service.PLATFORM_AI_MCP_MAX_CONTEXT_TOKENS
    assert ai_data["output"] == "平台 AI 分析结果"
    assert ai_data["credential_location"] == "platform_only"

    raw_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_raw_data_query",
            "arguments": {
                "source": "decision_logs",
                "skill_id": "codex_raw_skill",
                "run_id": "codex-raw-run-1",
                "limit": 5,
            },
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert raw_resp.status_code == 200
    raw_data = raw_resp.json()["data"]
    assert raw_data["source"] == "decision_logs"
    assert raw_data["count"] == 1
    assert raw_data["items"][0]["input_snapshot"]["query"] == "原始输入"
    assert raw_data["items"][0]["input_snapshot"]["token"] == "[REDACTED]"
    assert raw_data["scope"]["payload_redacted"] is True

    run_analysis_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_run_analyze",
            "arguments": {
                "run_id": "codex-raw-run-1",
                "include_raw": True,
                "step_limit": 5,
                "decision_limit": 5,
                "proof_limit": 5,
            },
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert run_analysis_resp.status_code == 200
    run_analysis = run_analysis_resp.json()["data"]
    assert run_analysis["analysis"] == "平台 AI 运行复盘"
    assert run_analysis["run_id"] == "codex-raw-run-1"
    assert run_analysis["raw_counts"]["execution_steps"] == 1
    assert run_analysis["raw_counts"]["decision_logs"] == 1
    assert run_analysis["raw_counts"]["collection_proofs"] == 1
    assert run_analysis["raw_data"]["decision_logs"][0]["input_snapshot"]["token"] == "[REDACTED]"
    assert any("运行" in prompt for prompt in ai_prompts)


@pytest.mark.asyncio
async def test_builtin_org_mcp_search_falls_back_to_dingtalk_contacts(client, monkeypatch):
    from app.database import async_session_factory

    raw_token = "test-org-mcp-dingtalk-contact-token"
    async with async_session_factory() as session:
        admin = await session.get(codex_service.User, "admin")
        if admin is None:
            admin = codex_service.User(
                id="admin",
                username="admin",
                name="管理员",
                role="system_admin",
                can_view_all=True,
                state="active",
                department="AI小组",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            )
            session.add(admin)
        else:
            admin.role = "system_admin"
            admin.can_view_all = True
            admin.state = "active"
            admin.is_active = True
        cli_session = CodexCliSession(
            id="cli_test_org_dingtalk_contact_mcp",
            user_id="admin",
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add(cli_session)
        await session.commit()

    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())
    monkeypatch.setattr(
        codex_service.dingtalk_client,
        "list_sub_departments",
        AsyncMock(
            side_effect=[
                {"ok": True, "data": [{"dept_id": "10", "name": "运营部"}]},
                {"ok": True, "data": []},
            ]
        ),
    )
    monkeypatch.setattr(
        codex_service.dingtalk_client,
        "list_department_users",
        AsyncMock(
            return_value={
                "ok": True,
                "data": [
                    {
                        "user_id": "900000000000000001",
                        "name": "示例成员甲",
                        "dept_id_list": ["10"],
                    }
                ],
            }
        ),
    )

    search_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_org_search_users",
            "arguments": {"query": "示例成员甲", "limit": 5},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )

    assert search_resp.status_code == 200
    search_data = search_resp.json()["data"]
    assert search_data["count"] == 1
    assert search_data["items"][0]["source"] == "dingtalk_contact"
    assert search_data["items"][0]["name"] == "示例成员甲"
    assert search_data["items"][0]["dingtalk_user_id"] == "900000000000000001"
    assert search_data["items"][0]["department"] == "运营部"


@pytest.mark.asyncio
async def test_org_mcp_search_falls_back_to_cached_dingtalk_contact_outside_scope(client, monkeypatch):
    from app.database import async_session_factory

    raw_token = "test-org-mcp-cached-dingtalk-contact-token"
    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex_cached_contact_viewer",
            username="codex_cached_contact_viewer",
            name="查询人",
            role="aibp",
            can_view_all=False,
            state="active",
            department="信息技术部",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        contact = codex_service.User(
            id="900000000000000001",
            username="dingtalk_900000000000000001",
            name="示例成员甲",
            role="aibp",
            can_view_all=False,
            state="active",
            department="阿里店群组",
            dingtalk_user_id="900000000000000001",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        org = OrgUnit(
            id="991264067",
            name="阿里店群组",
            type="department",
            path="/demo-root/1056594588/931580573/991264067",
            dingtalk_dept_id="991264067",
        )
        membership = UserOrgMembership(
            user_id=contact.id,
            org_unit_id=org.id,
            membership_type="primary",
            is_manager=False,
        )
        cli_session = CodexCliSession(
            id="cli_test_cached_dingtalk_contact_mcp",
            user_id=user.id,
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([user, contact, org, membership, cli_session])
        await session.commit()

    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())
    monkeypatch.setattr(codex_service.dingtalk_client, "list_sub_departments", AsyncMock(return_value={"ok": True, "data": []}))
    monkeypatch.setattr(codex_service.dingtalk_client, "list_department_users", AsyncMock(return_value={"ok": True, "data": []}))

    search_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_org_search_users",
            "arguments": {"query": "示例成员甲", "limit": 5},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )

    assert search_resp.status_code == 200
    search_data = search_resp.json()["data"]
    assert search_data["count"] == 1
    assert search_data["items"][0]["source"] == "dingtalk_contact_cache"
    assert search_data["items"][0]["name"] == "示例成员甲"
    assert search_data["items"][0]["department"] == "阿里店群组"
    assert search_data["items"][0]["dingtalk_user_id"] == "900000000000000001"


@pytest.mark.asyncio
async def test_dingtalk_notice_accepts_direct_dingtalk_user_id_without_platform_user(client, monkeypatch):
    from app.database import async_session_factory

    raw_token = "test-direct-dingtalk-userid-token"
    async with async_session_factory() as session:
        admin = await session.get(codex_service.User, "admin")
        if admin is None:
            admin = codex_service.User(
                id="admin",
                username="admin",
                name="管理员",
                role="system_admin",
                can_view_all=True,
                state="active",
                department="AI小组",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            )
            session.add(admin)
        else:
            admin.role = "system_admin"
            admin.can_view_all = True
            admin.state = "active"
            admin.is_active = True
        cli_session = CodexCliSession(
            id="cli_test_direct_dingtalk_userid_mcp",
            user_id="admin",
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add(cli_session)
        await session.commit()

    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())
    monkeypatch.setattr(
        codex_service.dingtalk_client,
        "get_user_detail",
        AsyncMock(
            return_value={
                "ok": True,
                "data": {
                    "user_id": "900000000000000001",
                    "name": "示例成员甲",
                    "dept_id_list": [],
                },
            }
        ),
    )

    real_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_dingtalk_send_work_notice",
            "arguments": {
                "dingtalk_user_ids": ["900000000000000001"],
                "title": "钉钉 userId 推送验证",
                "markdown": "真实入队",
            },
            "run_mode": "mcp_cli",
            "dry_run": False,
            "idempotency_key": "direct-dingtalk-userid-test-key",
        },
    )

    assert real_resp.status_code == 200
    data = real_resp.json()["data"]
    assert data["queued"] == 1
    assert data["recipients"][0]["source"] == "dingtalk_contact"

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                codex_service.select(DingTalkOutbox).where(
                    DingTalkOutbox.related_type == "codex_mcp_dingtalk_notice",
                    DingTalkOutbox.related_id == "direct-dingtalk-userid-test-key",
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].recipient_user_id == "900000000000000001"


@pytest.mark.asyncio
async def test_codex_dingtalk_notice_resolves_openid_shadow_user(client, monkeypatch):
    from app.database import async_session_factory

    raw_token = "codex-shadow-user-token"
    async with async_session_factory() as session:
        admin = await session.get(codex_service.User, "admin")
        if admin is None:
            admin = codex_service.User(
                id="admin",
                username="admin",
                name="管理员",
                role="system_admin",
                can_view_all=True,
                state="active",
                department="AI小组",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            )
            session.add(admin)
        else:
            admin.role = "system_admin"
            admin.can_view_all = True
            admin.state = "active"
            admin.is_active = True
        shadow = codex_service.User(
            id="dt_openid_shadow",
            username="dingtalk_openid_shadow",
            name="示例成员",
            role="aibp",
            state="active",
            department=None,
            dingtalk_user_id="openid-shadow-userid-abcdef",
            avatar_url="https://example.com/avatar/examplemember.png",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        canonical = codex_service.User(
            id="dt_real_user",
            username="dingtalk_real_user",
            name="示例成员甲",
            role="aibp",
            state="active",
            department="阿里店群组",
            dingtalk_user_id="real-dingtalk-userid",
            avatar_url="https://example.com/avatar/examplemember.png",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        cli_session = CodexCliSession(
            id="cli_test_shadow_push",
            user_id="admin",
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([shadow, canonical, cli_session])
        await session.commit()

    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())

    real_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_dingtalk_send_work_notice",
            "arguments": {
                "user_ids": ["dt_openid_shadow"],
                "title": "影子账号推送验证",
                "markdown": "真实入队",
            },
            "run_mode": "mcp_cli",
            "dry_run": False,
            "idempotency_key": "codex-shadow-push-test-key",
        },
    )
    assert real_resp.status_code == 200
    data = real_resp.json()["data"]
    assert data["queued"] == 1
    assert data["recipients"][0]["user_id"] == "dt_real_user"
    assert data["recipients"][0]["requested_user_id"] == "dt_openid_shadow"

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                codex_service.select(DingTalkOutbox).where(
                    DingTalkOutbox.related_type == "codex_mcp_dingtalk_notice",
                    DingTalkOutbox.related_id == "codex-shadow-push-test-key",
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].recipient_user_id == "real-dingtalk-userid"


@pytest.mark.asyncio
async def test_builtin_org_mcp_search_collapses_shadow_user(client, monkeypatch):
    from app.database import async_session_factory

    raw_token = "test-org-mcp-shadow-token"
    async with async_session_factory() as session:
        admin = await session.get(codex_service.User, "admin")
        if admin is None:
            admin = codex_service.User(
                id="admin",
                username="admin",
                name="管理员",
                role="system_admin",
                can_view_all=True,
                state="active",
                department="AI小组",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            )
            session.add(admin)
        else:
            admin.role = "system_admin"
            admin.can_view_all = True
            admin.state = "active"
            admin.is_active = True
        canonical = codex_service.User(
            id="dt_real_yang",
            username="dt_real_yang",
            name="杨昌亮",
            role="operator",
            state="active",
            department="办公室",
            dingtalk_user_id="0213644726269578",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        shadow = codex_service.User(
            id="dt_open_yang",
            username="dt_open_yang",
            name="老杨-杨昌亮",
            role="aibp",
            state="active",
            department="办公室",
            dingtalk_user_id="iPk5JHfadiiToiE",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        cli_session = CodexCliSession(
            id="cli_test_org_shadow_mcp",
            user_id="admin",
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add_all([canonical, shadow, cli_session])
        await session.commit()

    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())

    search_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_org_search_users",
            "arguments": {"query": "杨昌亮", "limit": 5},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )

    assert search_resp.status_code == 200
    search_data = search_resp.json()["data"]
    assert search_data["count"] == 1
    assert search_data["items"][0]["user_id"] == "dt_real_yang"
    assert search_data["items"][0]["name"] == "杨昌亮"
    assert search_data["items"][0]["dingtalk_user_id"] == "0213644726269578"


@pytest.mark.asyncio
async def test_codex_submission_preserves_review_gate_detail(monkeypatch, tmp_path):
    package = _tar_bytes(
        {
            "SKILL.md": (
                b"---\n"
                b"name: demo-codex-gate\n"
                b"description: demo\n"
                b"trigger_type: manual\n"
                b"risk_level: R1\n"
                b"owner: admin\n"
                b"---\n# Demo\n"
            ),
            "contract.json": _valid_contract_bytes(),
            "scripts/main.py": b"print('{}')\n",
        }
    )

    user = SimpleNamespace(id="admin", role="system_admin", department=None)
    gate_detail = {
        "reason": "提交审核 gate 未通过",
        "score": 75,
        "missing": [{"key": "test_or_sample_run", "label": "测试/样例运行"}],
    }

    class DB:
        def __init__(self):
            self.rows = {}
            self.added = []

        async def get(self, model, key):
            return self.rows.get((model, key))

        def add(self, row):
            self.added.append(row)

        async def flush(self):
            return None

    db = DB()

    class FakeGit:
        def __init__(self):
            self.root = tmp_path / "skills"

        def skill_exists(self, skill_id):
            return False

        def log(self, skill_id=None, max_count=1):
            return []

        def skill_dir(self, skill_id):
            return self.root / skill_id

        def commit_all(self, message, author=None, skill_id=None, validate=False):
            return "a" * 40

        def skill_advisory_lock(self, skill_id):
            class Lock:
                async def __aenter__(self_inner):
                    return None

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return Lock()

    async def fake_upsert_skill_metadata(*args, **kwargs):
        return SimpleNamespace(id=kwargs["skill_id"])

    async def fake_create_review(*args, **kwargs):
        raise AppError("PARAM_INVALID", 400, gate_detail)

    monkeypatch.setattr(codex_service, "git_service", FakeGit())
    monkeypatch.setattr(codex_service, "upsert_skill_metadata", fake_upsert_skill_metadata)
    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())

    import app.reviews.service as review_service

    monkeypatch.setattr(review_service, "create_review", fake_create_review)

    submission = await codex_service.create_or_update_skill_from_package(
        db,
        user=user,
        package_bytes=package,
        manifest={"skill_id": "demo-codex-gate"},
        supplied_package_hash="",
        base_commit=None,
        message="submit demo",
    )

    assert submission.status == "committed"
    assert submission.checks_json["review_error"] == "参数无效"
    assert submission.checks_json["review_error_detail"] == gate_detail


@pytest.mark.asyncio
async def test_codex_submission_force_submit_writes_force_audit(monkeypatch, tmp_path):
    package = _tar_bytes(
        {
            "SKILL.md": (
                b"---\n"
                b"name: demo-codex-force\n"
                b"description: demo\n"
                b"trigger_type: manual\n"
                b"risk_level: R1\n"
                b"owner: admin\n"
                b"---\n# Demo\n"
            ),
            "contract.json": _valid_contract_bytes(),
            "scripts/main.py": b"print('{\"summary\":\"ok\"}')\n",
        }
    )

    user = SimpleNamespace(id="admin", role="system_admin", department=None)
    gate_detail = {
        "reason": "提交审核 gate 未通过",
        "score": 75,
        "missing": [{"key": "test_or_sample_run", "label": "测试/样例运行"}],
    }

    class DB:
        def __init__(self):
            self.rows = {}
            self.added = []

        async def get(self, model, key):
            return self.rows.get((model, key))

        def add(self, row):
            self.added.append(row)

        async def flush(self):
            return None

    db = DB()

    class FakeGit:
        def __init__(self):
            self.root = tmp_path / "skills"

        def skill_exists(self, skill_id):
            return False

        def log(self, skill_id=None, max_count=1):
            return []

        def skill_dir(self, skill_id):
            return self.root / skill_id

        def commit_all(self, message, author=None, skill_id=None, validate=False):
            return "f" * 40

        def skill_advisory_lock(self, skill_id):
            class Lock:
                async def __aenter__(self_inner):
                    return None

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return Lock()

    async def fake_upsert_skill_metadata(*args, **kwargs):
        return SimpleNamespace(id=kwargs["skill_id"])

    create_kwargs = {}

    async def fake_create_review(*args, **kwargs):
        create_kwargs.update(kwargs)
        return {
            "review_id": 42,
            "status": "pending",
            "force_submit": True,
            "force_submit_gate": gate_detail,
        }

    audit_log = AsyncMock()
    monkeypatch.setattr(codex_service, "git_service", FakeGit())
    monkeypatch.setattr(codex_service, "upsert_skill_metadata", fake_upsert_skill_metadata)
    monkeypatch.setattr(codex_service.audit, "log", audit_log)

    import app.reviews.service as review_service

    monkeypatch.setattr(review_service, "create_review", fake_create_review)

    submission = await codex_service.create_or_update_skill_from_package(
        db,
        user=user,
        package_bytes=package,
        manifest={"skill_id": "demo-codex-force"},
        supplied_package_hash="",
        base_commit=None,
        message="submit demo",
        force_review_submit=True,
        force_review_reason="确认强制提交：样例 proof 缺失，进入人工审核",
    )

    assert submission.status == "review_pending"
    assert submission.review_id == 42
    assert create_kwargs["force_submit"] is True
    assert create_kwargs["force_reason"] == "确认强制提交：样例 proof 缺失，进入人工审核"
    force_call = next(
        call for call in audit_log.await_args_list
        if call.args[1] == "codex.skill.force_submit"
    )
    assert force_call.args[3] == "demo-codex-force"
    assert force_call.kwargs["detail"]["submission_id"] == submission.id
    assert force_call.kwargs["detail"]["review_id"] == 42
    assert force_call.kwargs["detail"]["force_reason"] == "确认强制提交：样例 proof 缺失，进入人工审核"
    assert force_call.kwargs["detail"]["gate"] == gate_detail


@pytest.mark.asyncio
async def test_codex_skill_upsert_adds_owner_member(client):
    from app.database import async_session_factory

    async with async_session_factory() as session:
        user = codex_service.User(
            id="codex-owner",
            username="codex-owner",
            name="Codex Owner",
            role="aibp",
            department="本部门",
            state="active",
            is_active=True,
            permissions_rev=0,
            must_change_password=False,
        )
        session.add(user)
        skill = await codex_service.upsert_skill_metadata(
            session,
            user=user,
            skill_id="codex-owner-skill",
            frontmatter={
                "name": "codex-owner-skill",
                "description": "demo",
                "metadata": {"owner": "codex-owner", "department": "本部门"},
            },
            git_commit="a" * 40,
        )
        member = await session.get(
            SkillMember,
            {"skill_id": "codex-owner-skill", "user_id": "codex-owner"},
        )

    assert skill.department == "本部门"
    assert skill.owner == "codex-owner"
    assert member is not None
    assert member.role == "owner"


@pytest.mark.asyncio
async def test_codex_submission_approve_uses_review_state_machine(client, monkeypatch):
    from app.database import async_session_factory

    raw_token = "test-cli-token"
    async with async_session_factory() as session:
        user = await session.get(codex_service.User, "admin")
        if user is None:
            user = codex_service.User(
                id="admin",
                username="admin",
                name="管理员",
                role="system_admin",
                can_view_all=True,
                state="active",
                department="AI小组",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            )
            session.add(user)
        skill = Skill(
            id="codex-approve-skill",
            name="Codex Approve Skill",
            department="AI小组",
            status="draft",
            git_commit="b" * 40,
        )
        review = Review(
            skill_id=skill.id,
            submitter="admin",
            reviewer="admin",
            status="pending",
            change_type="codex_submit",
            git_commit_after="b" * 40,
        )
        session.add(skill)
        session.add(review)
        cli_session = CodexCliSession(
            id="cli_test_approve",
            user_id="admin",
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        )
        session.add(cli_session)
        await session.flush()
        submission = CodexSkillSubmission(
            id="sub_test_approve",
            user_id="admin",
            skill_id=skill.id,
            package_hash="sha256:test",
            base_commit="a" * 40,
            git_commit="b" * 40,
            review_id=review.id,
            status="review_pending",
            checks_json={},
            manifest_json={
                "frontmatter": {
                    "instance_id": "内容电商",
                    "trigger_type": "cron",
                    "trigger_expression": "50 8,12,14,16 * * *",
                }
            },
        )
        session.add(submission)
        await session.commit()
        review_id = review.id

    async def fake_approve_review(db, review_id_arg, reviewer, **kwargs):
        assert review_id_arg == review_id
        assert reviewer == "admin"
        assert kwargs["verify_after_sync"] is False
        assert kwargs["runtime_instance_id"] == "内容电商"
        assert kwargs["cron_expression"] == "50 8,12,14,16 * * *"
        row = await db.get(CodexSkillSubmission, "sub_test_approve")
        assert row is not None
        return {"review_id": review_id_arg, "status": "approved", "new_version": "v0.1"}

    monkeypatch.setattr("app.reviews.service.approve_review", fake_approve_review)

    resp = await client.post(
        "/api/codex/skills/submissions/sub_test_approve/approve",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"verify_after_sync": False},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["submission_id"] == "sub_test_approve"
    assert data["status"] == "approved"
    assert data["review"]["status"] == "approved"


@pytest.mark.asyncio
async def test_codex_submission_auto_publishes_when_security_switch_enabled(client, monkeypatch):
    from app.common.models import SystemConfig
    from app.database import async_session_factory

    raw_token = "test-auto-publish-token"
    async with async_session_factory() as session:
        user = await session.get(codex_service.User, "admin")
        if user is None:
            user = codex_service.User(
                id="admin",
                username="admin",
                name="管理员",
                role="system_admin",
                can_view_all=True,
                state="active",
                department="AI小组",
                is_active=True,
                permissions_rev=0,
                must_change_password=False,
            )
            session.add(user)
        session.add(SystemConfig(key="security.bypass_review_direct_publish", value=True, updated_by="admin"))
        session.add(CodexCliSession(
            id="cli_auto_publish",
            user_id="admin",
            token_hash=codex_service.token_hash(raw_token),
            scopes_json={"source": "test"},
            permissions_rev_snapshot=0,
            expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
        ))
        await session.commit()

    package = _tar_bytes(
        {
            "SKILL.md": (
                b"---\n"
                b"name: auto-publish-skill\n"
                b"description: demo\n"
                b"trigger_type: manual\n"
                b"risk_level: R1\n"
                b"owner: admin\n"
                b"---\n# Demo\n"
            ),
            "contract.json": _valid_contract_bytes(),
            "scripts/main.py": b"print('{}')\n",
        }
    )

    class FakeGit:
        def __init__(self):
            self.root = Path("/tmp/skillforge-auto-publish-test")

        def skill_exists(self, skill_id):
            return False

        def log(self, skill_id=None, max_count=1):
            return []

        def skill_dir(self, skill_id):
            return self.root / skill_id

        def commit_all(self, message, author=None, skill_id=None, validate=False):
            return "c" * 40

        def skill_advisory_lock(self, skill_id):
            class Lock:
                async def __aenter__(self_inner):
                    return None

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return Lock()

    async def fake_approve_review_as_system(db, review_id, **kwargs):
        assert kwargs["verify_after_sync"] is True
        assert kwargs["runtime_instance_id"] is None
        assert kwargs["cron_expression"] is None
        return {"review_id": review_id, "status": "approved", "new_version": "v0.1"}

    monkeypatch.setattr(codex_service, "git_service", FakeGit())
    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())
    monkeypatch.setattr("app.reviews.service.approve_review_as_system", fake_approve_review_as_system)

    resp = await client.post(
        "/api/codex/skills/submissions",
        headers={"Authorization": f"Bearer {raw_token}"},
        data={
            "manifest_json": json.dumps({"skill_id": "auto-publish-skill"}),
            "package_hash": "",
            "base_commit": "",
            "message": "auto publish",
        },
        files={"package": ("skill.tar.gz", package, "application/gzip")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["auto_publish"]["ok"] is True
    assert data["review"]["status"] == "approved"
