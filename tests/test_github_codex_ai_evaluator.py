import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_github_codex_ai_projects.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("github_codex_ai_evaluator", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_evaluator_infers_nested_vite_target_and_strips_secret_env(tmp_path, monkeypatch):
    evaluator = _load_module()
    root = tmp_path / "repo"
    frontend = root / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text(
        json.dumps({"scripts": {"build": "vite build", "preview": "vite preview"}, "dependencies": {"vite": "latest"}}),
        encoding="utf-8",
    )
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-leak")
    monkeypatch.setenv("PATH", "/bin")

    targets = evaluator.infer_targets(root)
    assert targets[0].path == "frontend"
    assert targets[0].install == "npm ci --ignore-scripts"
    assert targets[0].build == "npm run build"
    assert "{port}" in targets[0].preview

    env = evaluator.sanitized_env(tmp_path / "cache", "owner/repo")
    assert "OPENAI_API_KEY" not in env
    assert env["HOME"].endswith("owner__repo")
    assert env["npm_config_audit"] == "false"


def test_evaluator_detects_native_addons_and_builds_report_package(tmp_path):
    evaluator = _load_module()
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"dependencies": {"node-pty": "1.0.0"}, "devDependencies": {"better-sqlite3": "latest"}}),
        encoding="utf-8",
    )
    assert evaluator.detect_native_addons(root) == ["better-sqlite3", "node-pty"]

    result = {
        "summary": {"repo_count": 1, "runnable_count": 1},
        "results": [{"repo": "owner/repo", "url": "https://github.com/owner/repo", "status": "runnable", "commit": "abc123", "native_addons": ["node-pty"], "targets": [{}]}],
    }
    package, manifest = evaluator.build_project_package("github-codex-ai-eval", result)
    assert manifest["metadata"]["runnable_count"] == 1
    assert b"GitHub Codex/AI" in package or package
