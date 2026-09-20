#!/usr/bin/env python3
"""Safely scout, build/probe, and register GitHub Codex/AI projects.

The evaluator is intentionally conservative:
- clones public repos into a cache directory;
- installs with ``--ignore-scripts`` by default;
- runs build/preview commands only when ``--execute`` is provided;
- uses an isolated HOME and strips model/API secrets from child env;
- can register a summarized Project so evidence enters SkillForge reports,
  todos, DecisionLog and learning loop.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from app.database import async_session_factory
from app.projects import service as project_service

DEFAULT_CORPUS = ROOT / "artifacts" / "project-checks" / "github-codex-ai-projects.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "project-checks" / "github-codex-ai-run-eval.json"
DEFAULT_CACHE = Path(os.environ.get("SKILLFORGE_CODEX_AI_SCOUT_CACHE") or "/tmp/skillforge-codex-ai-scout")
SECRET_ENV_RE = re.compile(r"(TOKEN|SECRET|PASSWORD|PASS|KEY|COOKIE|AUTH|OPENAI|ANTHROPIC|GEMINI|DASHSCOPE|DEEPSEEK)", re.I)
NATIVE_ADDON_PACKAGES = {
    "node-pty",
    "better-sqlite3",
    "sqlite3",
    "sharp",
    "canvas",
    "keytar",
    "@parcel/watcher",
    "fsevents",
    "cpu-features",
    "ssh2",
}
DEFAULT_ALLOWED_ENV = {"PATH", "LANG", "LC_ALL", "SHELL", "TERM", "TMPDIR", "COREPACK_HOME"}


@dataclass
class CommandResult:
    cmd: str
    cwd: str
    returncode: int | None = None
    seconds: float | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    skipped: bool = False
    reason: str | None = None


@dataclass
class ProbeResult:
    cmd: str
    cwd: str
    url: str
    ok: bool
    status: int | None = None
    seconds: float | None = None
    body_head: str = ""
    output_tail: str = ""
    skipped: bool = False
    reason: str | None = None


@dataclass
class EvalTarget:
    label: str
    path: str
    install: str
    build: str | None = None
    preview: str | None = None
    health_path: str = "/"
    native_rebuild: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class RepoEval:
    repo: str
    url: str
    commit: str | None
    status: str
    targets: list[dict[str, Any]]
    native_addons: list[str]
    tags: list[str]
    note: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def repo_slug(repo: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", repo).strip("_")


def run_cmd(cmd: str, cwd: Path, *, timeout: int, env: dict[str, str]) -> CommandResult:
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=cwd, shell=True, text=True, capture_output=True, timeout=timeout, env=env, check=False)
        return CommandResult(
            cmd=cmd,
            cwd=str(cwd),
            returncode=proc.returncode,
            seconds=round(time.time() - started, 2),
            stdout_tail=(proc.stdout or "")[-5000:],
            stderr_tail=(proc.stderr or "")[-5000:],
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            cmd=cmd,
            cwd=str(cwd),
            returncode=124,
            seconds=round(time.time() - started, 2),
            stdout_tail=(exc.stdout or "")[-5000:] if isinstance(exc.stdout, str) else "",
            stderr_tail=(exc.stderr or "")[-5000:] if isinstance(exc.stderr, str) else "timeout",
        )


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def probe_server(cmd_template: str, cwd: Path, *, health_path: str, timeout: int, env: dict[str, str]) -> ProbeResult:
    port = free_port()
    cmd = cmd_template.format(port=port)
    url = f"http://127.0.0.1:{port}{health_path if health_path.startswith('/') else '/' + health_path}"
    started = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        preexec_fn=os.setsid,
    )
    ok = False
    body = ""
    status: int | None = None
    output = ""
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            curl = subprocess.run(
                f"curl -fsS --max-time 2 {url}",
                shell=True,
                text=True,
                capture_output=True,
                timeout=4,
                env=env,
                check=False,
            )
            if curl.returncode == 0:
                ok = True
                status = 200
                body = (curl.stdout or "")[:1200]
                break
            time.sleep(0.5)
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass
        try:
            more, _ = proc.communicate(timeout=4)
            output += more or ""
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
            try:
                more, _ = proc.communicate(timeout=2)
                output += more or ""
            except Exception:
                pass
    return ProbeResult(
        cmd=cmd,
        cwd=str(cwd),
        url=url,
        ok=ok,
        status=status,
        seconds=round(time.time() - started, 2),
        body_head=body,
        output_tail=output[-5000:],
    )


def sanitized_env(cache_root: Path, repo: str) -> dict[str, str]:
    sandbox_home = cache_root / ".homes" / repo_slug(repo)
    sandbox_home.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key in DEFAULT_ALLOWED_ENV and not SECRET_ENV_RE.search(key)}
    env.update(
        {
            "HOME": str(sandbox_home),
            "CI": "true",
            "NO_COLOR": "1",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_update_notifier": "false",
            "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
        }
    )
    return env


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def package_json(path: Path) -> dict[str, Any]:
    try:
        data = load_json(path)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def package_manager_install(cwd: Path) -> str:
    if (cwd / "pnpm-lock.yaml").is_file():
        return "pnpm install --ignore-scripts --frozen-lockfile"
    if (cwd / "yarn.lock").is_file():
        return "yarn install --ignore-scripts --frozen-lockfile"
    if (cwd / "package-lock.json").is_file() or (cwd / "npm-shrinkwrap.json").is_file():
        return "npm ci --ignore-scripts"
    return "npm install --ignore-scripts"


def npm_run(script: str) -> str:
    return f"npm run {script}"


def detect_native_addons(root: Path) -> list[str]:
    found: set[str] = set()
    for pkg_path in root.rglob("package.json"):
        if "node_modules" in pkg_path.parts:
            continue
        data = package_json(pkg_path)
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            deps = data.get(section) if isinstance(data.get(section), dict) else {}
            for name in deps:
                if name in NATIVE_ADDON_PACKAGES:
                    found.add(name)
    return sorted(found)


def infer_targets(root: Path) -> list[EvalTarget]:
    candidates: list[Path] = []
    preferred = [root, root / "frontend", root / "client", root / "web", root / "app", root / "apps" / "web", root / "apps" / "pwa"]
    for path in preferred:
        if (path / "package.json").is_file():
            candidates.append(path)
    for pkg in root.glob("packages/*/package.json"):
        candidates.append(pkg.parent)
    seen: set[Path] = set()
    targets: list[EvalTarget] = []
    for cwd in candidates:
        cwd = cwd.resolve()
        if cwd in seen:
            continue
        seen.add(cwd)
        data = package_json(cwd / "package.json")
        scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
        if not scripts:
            continue
        build_script = None
        for name in ("build", "build:frontend", "check", "typecheck"):
            if name in scripts:
                build_script = name
                break
        if not build_script:
            for name in scripts:
                if name.endswith(":build") or name.startswith("build:"):
                    build_script = name
                    break
        preview_script = "preview" if "preview" in scripts else None
        rel = "." if cwd == root.resolve() else str(cwd.relative_to(root))
        targets.append(
            EvalTarget(
                label=rel,
                path=rel,
                install=package_manager_install(cwd),
                build=npm_run(build_script) if build_script else None,
                preview=f"npm run {preview_script} -- --host 127.0.0.1 --port {{port}}" if preview_script else None,
            )
        )
    return targets[:12]


def normalize_targets(root: Path, spec: dict[str, Any]) -> list[EvalTarget]:
    raw_targets = spec.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        return infer_targets(root)
    targets: list[EvalTarget] = []
    for index, item in enumerate(raw_targets, start=1):
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or ".").strip() or "."
        cwd = root / rel
        targets.append(
            EvalTarget(
                label=str(item.get("label") or rel or f"target-{index}"),
                path=rel,
                install=str(item.get("install") or package_manager_install(cwd)),
                build=str(item.get("build") or "").strip() or None,
                preview=str(item.get("preview") or "").strip() or None,
                health_path=str(item.get("health_path") or "/"),
                native_rebuild=[str(x) for x in item.get("native_rebuild") or [] if str(x).strip()],
                notes=str(item.get("notes") or ""),
            )
        )
    return targets


def git_clone_or_update(repo: str, dest: Path, *, refresh: bool, env: dict[str, str]) -> CommandResult:
    if refresh and dest.exists():
        shutil.rmtree(dest)
    if (dest / ".git").is_dir():
        return run_cmd("git fetch --depth=1 origin HEAD && git reset --hard FETCH_HEAD", dest, timeout=120, env=env)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return run_cmd(f"git clone --depth=1 https://github.com/{repo}.git {dest.name}", dest.parent, timeout=180, env=env)


def git_commit(dest: Path, env: dict[str, str]) -> str | None:
    proc = run_cmd("git rev-parse --short HEAD", dest, timeout=20, env=env)
    if proc.returncode == 0:
        return proc.stdout_tail.strip().splitlines()[-1]
    return None


def target_status(install: CommandResult, build: CommandResult | None, probe: ProbeResult | None) -> str:
    if install.skipped:
        return "planned"
    if install.returncode != 0:
        return "install_failed"
    if build and build.returncode != 0:
        return "build_failed"
    if build and build.returncode == 0 and probe and not probe.ok:
        return "build_ok_probe_failed"
    if build and build.returncode == 0:
        return "runnable"
    return "install_ok"


def repo_status(targets: list[dict[str, Any]]) -> str:
    statuses = {str(t.get("status")) for t in targets}
    if not targets:
        return "no_targets"
    if "runnable" in statuses:
        return "runnable"
    if statuses == {"planned"}:
        return "planned"
    if any(s in statuses for s in {"build_ok_probe_failed", "install_ok"}):
        return "partial"
    return "failed"


async def evaluate_specs(
    specs: list[dict[str, Any]],
    *,
    cache_root: Path,
    execute: bool,
    refresh: bool,
    timeout: int,
) -> dict[str, Any]:
    cache_root.mkdir(parents=True, exist_ok=True)
    repos_out: list[RepoEval] = []
    for spec in specs:
        repo = str(spec.get("repo") or "").strip()
        if not repo or "/" not in repo:
            continue
        env = sanitized_env(cache_root, repo)
        dest = cache_root / repo_slug(repo)
        clone = git_clone_or_update(repo, dest, refresh=refresh, env=env)
        targets_out: list[dict[str, Any]] = []
        commit = git_commit(dest, env) if clone.returncode == 0 else None
        native_addons = detect_native_addons(dest) if dest.exists() else []
        if clone.returncode != 0:
            repos_out.append(
                RepoEval(
                    repo=repo,
                    url=f"https://github.com/{repo}",
                    commit=commit,
                    status="clone_failed",
                    targets=[{"clone": asdict(clone), "status": "clone_failed"}],
                    native_addons=native_addons,
                    tags=[str(x) for x in spec.get("tags") or []],
                    note=str(spec.get("note") or ""),
                )
            )
            continue
        for target in normalize_targets(dest, spec):
            cwd = (dest / target.path).resolve()
            install = CommandResult(cmd=target.install, cwd=str(cwd), skipped=True, reason="execute flag not set")
            build: CommandResult | None = None
            rebuild: CommandResult | None = None
            probe: ProbeResult | None = None
            if execute:
                install = run_cmd(target.install, cwd, timeout=timeout, env=env)
                if install.returncode == 0 and target.native_rebuild:
                    rebuild = run_cmd("npm rebuild " + " ".join(target.native_rebuild), cwd, timeout=timeout, env=env)
                if install.returncode == 0 and target.build:
                    build = run_cmd(target.build, cwd, timeout=timeout, env=env)
                if build and build.returncode == 0 and target.preview:
                    probe = probe_server(target.preview, cwd, health_path=target.health_path, timeout=min(timeout, 30), env=env)
            targets_out.append(
                {
                    "label": target.label,
                    "path": target.path,
                    "install": asdict(install),
                    "native_rebuild": asdict(rebuild) if rebuild else None,
                    "build": asdict(build) if build else None,
                    "probe": asdict(probe) if probe else None,
                    "status": target_status(install, build, probe),
                    "notes": target.notes,
                }
            )
        repos_out.append(
            RepoEval(
                repo=repo,
                url=f"https://github.com/{repo}",
                commit=commit,
                status=repo_status(targets_out),
                targets=targets_out,
                native_addons=native_addons,
                tags=[str(x) for x in spec.get("tags") or []],
                note=str(spec.get("note") or ""),
            )
        )
    return {
        "ok": True,
        "generated_at": now_iso(),
        "cache_root": str(cache_root),
        "execute": execute,
        "safety": {
            "isolated_home": True,
            "secret_env_stripped": True,
            "install_ignores_lifecycle_scripts_by_default": True,
            "model_calls_made": False,
            "third_party_tokens_used": False,
        },
        "summary": {
            "repo_count": len(repos_out),
            "runnable_count": sum(1 for item in repos_out if item.status == "runnable"),
            "partial_count": sum(1 for item in repos_out if item.status == "partial"),
            "failed_count": sum(1 for item in repos_out if item.status in {"failed", "clone_failed"}),
            "planned_count": sum(1 for item in repos_out if item.status == "planned"),
        },
        "results": [asdict(item) for item in repos_out],
    }


def make_report_html(result: dict[str, Any]) -> str:
    rows = []
    for item in result.get("results") or []:
        native = ", ".join(item.get("native_addons") or []) or "-"
        rows.append(
            f"<tr><td><a href='{item.get('url')}'>{item.get('repo')}</a></td><td>{item.get('status')}</td>"
            f"<td>{item.get('commit') or '-'}</td><td>{native}</td><td>{len(item.get('targets') or [])}</td></tr>"
        )
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>GitHub Codex/AI 项目评估</title>
<style>body{font-family:system-ui;margin:24px;background:#0b1220;color:#e5eefc}table{border-collapse:collapse;width:100%}td,th{border:1px solid #334155;padding:8px}a{color:#7dd3fc}pre{white-space:pre-wrap;background:#111827;padding:16px;border-radius:12px}</style></head><body><main>
<h1>GitHub Codex/AI 项目评估</h1><p>SkillForge 自动下载、构建/探测并沉淀进平台循环；未调用第三方模型或 token。</p>
<table><thead><tr><th>Repo</th><th>Status</th><th>Commit</th><th>Native add-ons</th><th>Targets</th></tr></thead><tbody>""" + "\n".join(rows) + """</tbody></table>
<button data-sf-report="GitHub Codex/AI 评估已进入平台循环">沉淀报告</button>
<pre id="eval-json">""" + json.dumps(result, ensure_ascii=False, indent=2) + """</pre></main></body></html>"""


def build_project_package(project_id: str, result: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    html = make_report_html(result).encode("utf-8")
    manifest = {
        "project_id": project_id,
        "name": "GitHub Codex/AI 项目评估",
        "description": "自动下载、构建/探测 Codex/AI GitHub 项目，并把结果输入 SkillForge 改进循环。",
        "kind": "web_static",
        "entry": "web/index.html",
        "visibility": "department",
        "department": "AI小组",
        "capabilities": ["ai.cheap.generate", "ai.generate"],
        "metadata": {
            "source": "github_codex_ai_evaluator",
            "evaluated_count": result.get("summary", {}).get("repo_count"),
            "runnable_count": result.get("summary", {}).get("runnable_count"),
        },
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        info = tarfile.TarInfo("web/index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
    return buf.getvalue(), manifest


def collect_result_proofs(result: dict[str, Any]) -> list[dict[str, Any]]:
    proofs: list[dict[str, Any]] = [
        {
            "type": "github_codex_ai_eval_summary",
            "title": "GitHub Codex/AI 项目评估汇总",
            **(result.get("summary") or {}),
            "generated_at": result.get("generated_at"),
            "execute": result.get("execute"),
        }
    ]
    for item in result.get("results") or []:
        if not isinstance(item, dict):
            continue
        target_evidence = []
        for target in item.get("targets") or []:
            if not isinstance(target, dict):
                continue
            target_evidence.append(
                {
                    "kind": target.get("kind"),
                    "status": target.get("status"),
                    "install": (target.get("install") or {}).get("returncode"),
                    "build": (target.get("build") or {}).get("returncode"),
                    "preview_probe": target.get("preview_probe"),
                }
            )
        proofs.append(
            {
                "type": "github_repo_eval",
                "repo": item.get("repo"),
                "url": item.get("url"),
                "commit": item.get("commit"),
                "status": item.get("status"),
                "native_addons": item.get("native_addons"),
                "target_evidence": target_evidence[:5],
            }
        )
    return proofs[:50]


async def register_result(result: dict[str, Any], *, project_id: str, auto_analyze: bool) -> dict[str, Any]:
    package_bytes, manifest = build_project_package(project_id, result)
    package_hash = "sha256:" + hashlib.sha256(package_bytes).hexdigest()
    user = SimpleNamespace(
        id="admin",
        username="admin",
        name="管理员",
        role="admin",
        department="AI小组",
        can_view_all=True,
        state="active",
        is_active=True,
    )
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    async with async_session_factory() as db:
        uploaded = await project_service.upload_project_package(
            db,
            user,
            package_bytes=package_bytes,
            manifest_raw=manifest,
            package_hash=package_hash,
            submit_metadata={"github_codex_ai_evaluator": {"batch_id": batch_id, "summary": result.get("summary")}},
        )
        run = await project_service.create_project_run(
            db,
            user,
            project_id,
            {"request_id": f"github-codex-ai-eval-open-{batch_id}", "params": result.get("summary") or {}},
        )
        output = {
            "summary": (
                f"已评估 {result.get('summary', {}).get('repo_count', 0)} 个 GitHub Codex/AI 项目；"
                f"可运行 {result.get('summary', {}).get('runnable_count', 0)} 个，"
                f"部分通过 {result.get('summary', {}).get('partial_count', 0)} 个。"
            ),
            "evaluation": result,
            "proofs": collect_result_proofs(result),
            "reports": [
                {
                    "title": "GitHub Codex/AI 项目评估",
                    "summary": "自动下载、构建/探测、native add-on 识别和平台接管缺口已沉淀。",
                    "metrics": [
                        {"name": "repo_count", "value": result.get("summary", {}).get("repo_count", 0)},
                        {"name": "runnable_count", "value": result.get("summary", {}).get("runnable_count", 0)},
                    ],
                }
            ],
            "todos": [
                {
                    "kind": "review",
                    "title": "补齐 Codex/AI 项目企业级运行沙箱",
                    "summary": "基于 GitHub 实测补齐 native add-on rebuild、长期服务托管、端口探活、凭证隔离和 Project Gateway AI 接管。",
                    "reviewers": ["admin"],
                    "payload": {"project_id": project_id, "summary": result.get("summary")},
                }
            ],
        }
        run_result = await project_service.ingest_project_output(
            db,
            user,
            run["id"],
            {
                "request_id": f"github-codex-ai-eval-output-{batch_id}",
                "input": {"cache_root": result.get("cache_root")},
                "output": output,
                "metadata": {"source": "github_codex_ai_evaluator", "department": "AI小组"},
            },
            auto_analyze=auto_analyze,
        )
        await db.commit()
    return {
        "project_id": uploaded.get("id"),
        "entry_url": uploaded.get("entry_url"),
        "run_id": run.get("id"),
        "run_status": run_result.get("status"),
        "report_count": run_result.get("report_count"),
        "todo_count": run_result.get("todo_count"),
    }


def load_specs(path: Path, repos: list[str]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if path.is_file():
        raw = load_json(path)
        items = raw.get("repositories") if isinstance(raw, dict) else raw
        if isinstance(items, list):
            specs.extend([item for item in items if isinstance(item, dict)])
    for repo in repos:
        specs.append({"repo": repo})
    return specs


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate GitHub Codex/AI projects and optionally register the result in SkillForge")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--repo", action="append", default=[], help="Additional owner/name repo; can repeat")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--execute", action="store_true", help="Actually run install/build/preview commands")
    parser.add_argument("--refresh", action="store_true", help="Re-clone repos")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--register", action="store_true", help="Register evaluation report as a SkillForge Project")
    parser.add_argument("--project-id", default="github-codex-ai-eval")
    parser.add_argument("--auto-analyze", action="store_true", help="Trigger platform AI analyze after registration")
    args = parser.parse_args()

    specs = load_specs(args.corpus, args.repo)
    if args.limit and args.limit > 0:
        specs = specs[: args.limit]
    result = asyncio.run(
        evaluate_specs(specs, cache_root=args.cache_root, execute=args.execute, refresh=args.refresh, timeout=max(10, args.timeout))
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.register:
        result["registration"] = asyncio.run(register_result(result, project_id=args.project_id, auto_analyze=args.auto_analyze))
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
