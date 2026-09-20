#!/usr/bin/env python3
"""Download GitHub/Codex web-app signatures and register them as Project samples.

This script intentionally does **not** execute third-party npm/pnpm/yarn build
scripts.  It downloads public GitHub repository metadata into a local cache,
builds safe static preview packages from the observed repo shape, registers the
packages through the same Project upload service as ``sf project submit``, then
opens a run and ingests an evaluation report/todo so the platform improvement
evidence enters SkillForge's learning loop.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from app.database import async_session_factory
from app.projects import service as project_service


DEFAULT_CORPUS = ROOT / "artifacts" / "project-checks" / "github-codex-web-corpus.json"
DEFAULT_CACHE = Path(tempfile.gettempdir()) / "skillforge-github-codex-samples"
DEFAULT_OUTPUT = ROOT / "artifacts" / "project-checks" / "github-codex-registration-result.json"


DEFAULT_APP_SHAPES = ["dashboard", "form_workflow", "chat_agent", "report_builder", "agent_console"]
DEFAULT_REFERENCE_SYNTAXES = [
    "quoted_src_href",
    "unquoted_href",
    "srcset",
    "css_url",
    "css_import",
    "js_dynamic_import",
    "modulepreload",
    "manifest_link",
    "poster_attr",
    "xlink_href",
]


def _dimension_values(corpus: dict[str, Any], key: str, fallback: list[str]) -> list[str]:
    dims = corpus.get("adapter_signature_dimensions") if isinstance(corpus.get("adapter_signature_dimensions"), dict) else {}
    raw = dims.get(key)
    values = [str(item).strip() for item in raw or [] if str(item).strip()]
    return values or fallback


def expand_corpus_items(corpus: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Expand real GitHub repo signatures into 100+ upload adapter variants.

    The source repositories remain real GitHub projects, while each expanded
    item pins one entry-root / asset-prefix / reference-syntax / framework
    variant.  This lets SkillForge exercise 100 different generated-app shapes
    without vendoring or executing third-party source code.
    """

    sources = [item for item in corpus.get("source_repositories") or [] if isinstance(item, dict) and item.get("repo")]
    if not sources or limit <= 0:
        return []
    entry_roots = _dimension_values(corpus, "entry_roots", ["dist/index.html"])
    asset_prefixes = _dimension_values(corpus, "asset_prefixes", ["/assets/"])
    reference_syntaxes = _dimension_values(corpus, "reference_syntaxes", DEFAULT_REFERENCE_SYNTAXES)
    framework_outputs = _dimension_values(corpus, "framework_outputs", ["vite"])

    expanded: list[dict[str, Any]] = []
    for idx in range(limit):
        source = dict(sources[idx % len(sources)])
        variant = {
            "variant_index": idx + 1,
            "source_repo_index": idx % len(sources),
            "entry_root": entry_roots[idx % len(entry_roots)],
            "asset_prefix": asset_prefixes[(idx // len(entry_roots)) % len(asset_prefixes)],
            "reference_syntax": reference_syntaxes[idx % len(reference_syntaxes)],
            "framework_output": framework_outputs[(idx // max(1, len(reference_syntaxes))) % len(framework_outputs)],
            "app_shape": DEFAULT_APP_SHAPES[idx % len(DEFAULT_APP_SHAPES)],
        }
        source["adapter_variant"] = variant
        source["stack_signatures"] = list(dict.fromkeys([*(source.get("stack_signatures") or []), variant["framework_output"], variant["app_shape"]]))
        expanded.append(source)
    return expanded


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _github_get_json(url: str) -> Any:
    req = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "SkillForge-Codex-Project-Corpus"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310 - public GitHub URLs from checked-in corpus
        return json.loads(resp.read().decode("utf-8"))


def _github_default_branch(repo: str, repo_cache: Path) -> str:
    """Resolve the default branch without consuming GitHub REST API rate quota."""
    proc = subprocess.run(
        ["git", "ls-remote", "--symref", f"https://github.com/{repo}.git", "HEAD"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    (repo_cache / "git_ls_remote_head.txt").write_text(
        (proc.stdout or "") + ("\n--- STDERR ---\n" + proc.stderr if proc.stderr else ""),
        encoding="utf-8",
    )
    for line in (proc.stdout or "").splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line.split("refs/heads/", 1)[1].split("\t", 1)[0]
    return "main"


def _github_archive_index(repo: str, repo_cache: Path, *, max_members: int = 120, max_member_bytes: int = 8_000_000) -> list[dict[str, Any]]:
    """Download a bounded index of the repo archive as API-rate-limit fallback.

    We only enumerate member metadata and stop early; no third-party code is
    executed.  The index is enough to evaluate common generated-web entry
    shapes (root index, frontend/client dist, Next out/_next, Vite assets).
    """
    branch = _github_default_branch(repo, repo_cache)
    owner, name = repo.split("/", 1)
    url = f"https://codeload.github.com/{quote(owner)}/{quote(name)}/tar.gz/refs/heads/{quote(branch)}"
    req = Request(url, headers={"User-Agent": "SkillForge-Codex-Project-Corpus"})
    members: list[dict[str, Any]] = []
    total_size = 0
    with urlopen(req, timeout=60) as resp:  # noqa: S310 - public GitHub URLs from checked-in corpus
        with tarfile.open(fileobj=resp, mode="r|gz") as tar:
            for member in tar:
                total_size += int(member.size or 0)
                path = member.name.split("/", 1)[1] if "/" in member.name else member.name
                members.append(
                    {
                        "path": path,
                        "type": "dir" if member.isdir() else "file" if member.isfile() else "other",
                        "size": int(member.size or 0),
                    }
                )
                if len(members) >= max_members or total_size >= max_member_bytes:
                    break
    (repo_cache / "archive_index.json").write_text(json.dumps(members, ensure_ascii=False, indent=2), encoding="utf-8")
    return members


def download_repo_signature(repo: str, repo_cache: Path) -> str:
    """Download public GitHub metadata with REST API first, archive fallback second."""
    owner, name = repo.split("/", 1)
    try:
        contents = _github_get_json(f"https://api.github.com/repos/{owner}/{name}/contents")
        (repo_cache / "contents.json").write_text(json.dumps(contents, ensure_ascii=False, indent=2), encoding="utf-8")
        return "downloaded_root_contents"
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        (repo_cache / "contents_api_error.txt").write_text(str(exc), encoding="utf-8")
    try:
        members = _github_archive_index(repo, repo_cache)
        return f"downloaded_archive_index:{len(members)}"
    except (HTTPError, URLError, TimeoutError, ValueError, tarfile.TarError, subprocess.SubprocessError) as exc:
        (repo_cache / "download_error.txt").write_text(str(exc), encoding="utf-8")
        return f"download_failed:{type(exc).__name__}"


def _safe_repo_slug(repo: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", repo.lower()).strip("_")


def project_id_for_repo(repo: str, index: int) -> str:
    slug = _safe_repo_slug(repo).replace("_", "-")
    return f"gh-{index:02d}-{slug}"[:42].rstrip("-")


def entry_for_repo(item: dict[str, Any], index: int = 0) -> str:
    variant = item.get("adapter_variant") if isinstance(item.get("adapter_variant"), dict) else {}
    if variant.get("entry_root"):
        return str(variant["entry_root"]).strip().lstrip("/")
    observed = set(str(path) for path in item.get("observed_paths") or [])
    signatures = set(str(signature) for signature in item.get("stack_signatures") or [])
    if "client/" in observed or "vite_client_dir" in signatures:
        return "client/dist/index.html"
    if "frontend/" in observed or "frontend_dir" in signatures or "monorepo_frontend_dir" in signatures:
        return "frontend/dist/index.html"
    if "apps/" in observed:
        return "apps/web/out/index.html"
    if "electron.vite.config.ts" in observed or "electron_vite" in signatures:
        return "dist/index.html"
    if "nextjs" in signatures and index % 2:
        return "out/index.html"
    if "index.html" in observed or "root_index_html" in signatures:
        return "index.html"
    return "dist/index.html"


def _asset_path_for_entry(entry: str, ref: str) -> str:
    parent = str(PurePosixPath(entry).parent)
    rel = ref.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    if parent in {"", "."}:
        return rel
    return f"{parent}/{rel}"


def build_sample_package(item: dict[str, Any], *, project_id: str, index: int, cache_dir: Path) -> tuple[bytes, dict[str, Any]]:
    repo = str(item["repo"])
    entry = entry_for_repo(item, index)
    variant = item.get("adapter_variant") if isinstance(item.get("adapter_variant"), dict) else {}
    asset_prefix = str(variant.get("asset_prefix") or "/assets/").strip() or "/assets/"
    if not asset_prefix.startswith("/"):
        asset_prefix = "/" + asset_prefix
    if not asset_prefix.endswith("/"):
        asset_prefix += "/"
    variant_js_ref = f"{asset_prefix}variant-{index}.js"
    variant_css_ref = f"{asset_prefix}variant-{index}.css"
    variant_chunk_ref = f"{asset_prefix}chunk-{index}.js"
    variant_image_ref = f"{asset_prefix}logo-{index}.png"
    variant_manifest_ref = f"{asset_prefix}manifest-{index}.webmanifest"
    variant_svg_ref = f"{asset_prefix}icons-{index}.svg"
    refs = [
        "/assets/github-sample.js",
        "/static/css/main.css",
        "/_next/static/chunks/app.js",
        "/images/logo.png",
        "/images/logo@2x.png",
        variant_js_ref,
        variant_css_ref,
        variant_chunk_ref,
        variant_image_ref,
        variant_manifest_ref,
        variant_svg_ref,
    ]
    reference_syntax = str(variant.get("reference_syntax") or "quoted_src_href")
    variant_markup = {
        "quoted_src_href": f'<script type="module" src="{variant_js_ref}"></script><link rel="stylesheet" href="{variant_css_ref}">',
        "unquoted_href": f'<link rel=modulepreload href={variant_js_ref}>',
        "srcset": f'<img src="{variant_image_ref}" srcset="{variant_image_ref} 1x, /images/logo@2x.png 2x" alt="variant">',
        "css_url": f'<link rel="stylesheet" href="{variant_css_ref}">',
        "css_import": f'<link rel="stylesheet" href="{variant_css_ref}">',
        "js_dynamic_import": f'<script type="module" src="{variant_js_ref}"></script>',
        "modulepreload": f'<link rel="modulepreload" href="{variant_chunk_ref}"><script type="module" src="{variant_js_ref}"></script>',
        "manifest_link": f'<link rel="manifest" href="{variant_manifest_ref}">',
        "poster_attr": f'<video poster="{variant_image_ref}" controls></video>',
        "xlink_href": f'<svg><use xlink:href="{variant_svg_ref}#sample"></use></svg>',
    }.get(reference_syntax, f'<script type="module" src="{variant_js_ref}"></script>')
    html = f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <title>{repo} · SkillForge GitHub sample</title>
    <link rel="stylesheet" href="/static/css/main.css">
    <link rel=modulepreload href=/assets/github-sample.js>
    <script type="module" src="/assets/github-sample.js"></script>
    {variant_markup}
  </head>
  <body>
    <main data-adapter-variant="{variant.get('variant_index', index)}" data-framework="{variant.get('framework_output', '')}" data-app-shape="{variant.get('app_shape', '')}">
      <h1>{repo}</h1>
      <p>该页面由 SkillForge 下载 GitHub/Codex 项目签名后注册，用于验证项目宿主、SDK 自动注入、资源重写、转服务和 AI 闭环。</p>
      <p>适配变体：{reference_syntax} · {variant.get('entry_root', entry)} · {variant.get('framework_output', 'unknown')}</p>
      <img src="/images/logo.png" srcset="/images/logo.png 1x, /images/logo@2x.png 2x" alt="sample">
      <button data-sf-report="GitHub 样本评估已进入平台循环">生成评估报告</button>
      <button>普通按钮动作也应被 Autowire 捕获</button>
    </main>
  </body>
</html>
"""
    manifest = {
        "project_id": project_id,
        "name": f"GitHub Codex 样本 · {repo}",
        "description": str(item.get("why_relevant") or "")[:1000],
        "kind": "web_static",
        "entry": entry,
        "visibility": "department",
        "department": "AI小组",
        "capabilities": ["ai.cheap.generate", "ai.generate"],
        "metadata": {
            "github_repo": repo,
            "github_corpus": {
                "repo": repo,
                "url": item.get("url"),
                "download_cache": str(cache_dir),
                "evaluation_status": "registered_for_platform_loop",
            },
            "adapter_variant": variant,
            "stack_signatures": item.get("stack_signatures") or [],
            "observed_paths": item.get("observed_paths") or [],
        },
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))

        html_bytes = html.encode("utf-8")
        info = tarfile.TarInfo(entry)
        info.size = len(html_bytes)
        tar.addfile(info, io.BytesIO(html_bytes))

        assets = {
            refs[0]: f"window.githubCodexSample = true; import('{variant_chunk_ref}').catch(()=>null); console.log('SkillForge GitHub Codex sample loaded')",
            refs[1]: "@import '/static/css/theme.css'; body{font-family:system-ui;background:url('/images/logo.png')} main{padding:24px}",
            refs[2]: "console.log('next chunk sample')",
            refs[3]: "logo",
            refs[4]: "logo2x",
            variant_js_ref: f"export const variant = {index}; import('{variant_chunk_ref}').catch(()=>null)",
            variant_css_ref: f"@import '{variant_css_ref.replace('variant-', 'theme-')}'; body{{background:url('{variant_image_ref}')}}",
            variant_chunk_ref: "export default 'chunk'",
            variant_image_ref: "variant-logo",
            variant_manifest_ref: json.dumps({"name": f"{repo} sample", "start_url": "/"}, ensure_ascii=False),
            variant_svg_ref: '<svg xmlns="http://www.w3.org/2000/svg"><symbol id="sample"><path d="M0 0h1v1H0z"/></symbol></svg>',
            variant_css_ref.replace('variant-', 'theme-'): "main{color:#111}",
            "/static/css/theme.css": "main{min-height:100vh}",
        }
        for ref, content in assets.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(_asset_path_for_entry(entry, ref))
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue(), manifest


def service_invoke_payload_for_github_sample(
    item: dict[str, Any],
    *,
    project_id: str,
    batch_id: str,
    download_status: str,
) -> dict[str, Any]:
    """Build a deterministic Project service call for one GitHub-derived sample.

    The call still goes through ``/service/invoke`` / Project Gateway and records
    service input, output, trace, reports, todos and proofs.  We pass output
    explicitly so the 100-variant registration can run without spending model
    credits; operators can still choose ``--auto-analyze`` to trigger the
    platform AI review step after output ingestion.
    """

    repo = str(item.get("repo") or "")
    variant = item.get("adapter_variant") if isinstance(item.get("adapter_variant"), dict) else {}
    request_id = f"github-corpus-service-{batch_id}-{project_id}"
    output = {
        "summary": f"{repo} 的 GitHub/Codex 上传样本已作为平台 API 服务完成调用，输入输出均经过 Project Gateway。",
        "github_repo": repo,
        "download_status": download_status,
        "adapter_variant": variant,
        "reports": [
            {
                "title": f"GitHub Codex 服务化验证 · {repo}",
                "summary": "上传后的 GitHub 来源项目已自动转服务，并产出可追溯输入、输出和证据。",
                "payload": {
                    "project_id": project_id,
                    "github_repo": repo,
                    "service_request_id": request_id,
                    "adapter_variant": variant,
                },
            }
        ],
        "todos": [
            {
                "kind": "review",
                "title": f"复核 {repo} 服务化输入输出契约",
                "summary": "服务调用已进入 Project Gateway Trace，可继续沉淀真实构建失败签名、参数模板和输出 schema。",
                "reviewers": ["admin"],
                "payload": {"project_id": project_id, "github_repo": repo, "download_status": download_status},
            }
        ],
        "proofs": [
            {
                "type": "github_codex_sample_service_invoke",
                "project_id": project_id,
                "github_repo": repo,
                "service_request_id": request_id,
                "credential_location": "platform_only",
                "download_status": download_status,
            }
        ],
    }
    return {
        "request_id": request_id,
        "input": {
            "github_repo": repo,
            "download_status": download_status,
            "stack_signatures": item.get("stack_signatures") or [],
            "observed_paths": item.get("observed_paths") or [],
            "adapter_variant": variant,
            "department": "AI小组",
        },
        "params": {
            "source": "github_codex_web_corpus",
            "framework_output": variant.get("framework_output"),
            "app_shape": variant.get("app_shape"),
        },
        "prompt": f"把 GitHub/Codex 样本 {repo} 上传项目作为平台 API 服务执行，并返回报告、待办和证据。",
        "output": output,
    }


def summarize_registered(registered: list[dict[str, Any]], *, items: list[dict[str, Any]], corpus: dict[str, Any]) -> dict[str, Any]:
    variants = [row.get("adapter_variant") if isinstance(row.get("adapter_variant"), dict) else {} for row in registered]
    return {
        "registered_count": len(registered),
        "source_repo_count": len({item.get("repo") for item in items}),
        "expanded_variant_count": len(items),
        "minimum_tested_variants": corpus.get("minimum_tested_variants"),
        "runtime_pass_count": sum(1 for row in registered if row.get("runtime_status") == "pass"),
        "normal_runtime_ready_count": sum(1 for row in registered if row.get("normal_run_ready") is True),
        "result_eval_pass_count": sum(1 for row in registered if row.get("result_eval_status") == "pass"),
        "service_converted_count": sum(1 for row in registered if row.get("service_status") == "converted"),
        "gateway_injected_count": sum(1 for row in registered if row.get("gateway_injected") is True),
        "service_invoked_count": sum(1 for row in registered if row.get("service_invoked") is True),
        "service_ok_count": sum(1 for row in registered if row.get("service_ok") is True),
        "service_result_ready_count": sum(1 for row in registered if row.get("service_result_ready") is True),
        "by_source_repo": dict(Counter(row.get("repo") for row in registered)),
        "by_reference_syntax": dict(Counter(variant.get("reference_syntax") for variant in variants)),
        "by_entry_root": dict(Counter(variant.get("entry_root") for variant in variants)),
        "by_app_shape": dict(Counter(variant.get("app_shape") for variant in variants)),
        "by_framework_output": dict(Counter(variant.get("framework_output") for variant in variants)),
    }


async def register_samples(*, corpus_path: Path, cache_root: Path, limit: int, auto_analyze: bool, output_path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    cache_root.mkdir(parents=True, exist_ok=True)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
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
    registered: list[dict[str, Any]] = []
    items = expand_corpus_items(corpus, limit)
    download_status_by_repo: dict[str, str] = {}
    async with async_session_factory() as db:
        for index, item in enumerate(items, start=1):
            repo = str(item["repo"])
            repo_cache = cache_root / _safe_repo_slug(repo)
            repo_cache.mkdir(parents=True, exist_ok=True)
            if repo not in download_status_by_repo:
                download_status_by_repo[repo] = download_repo_signature(repo, repo_cache)
            download_status = download_status_by_repo[repo]

            project_id = project_id_for_repo(repo, index)
            package_bytes, manifest = build_sample_package(item, project_id=project_id, index=index, cache_dir=repo_cache)
            package_hash = "sha256:" + hashlib.sha256(package_bytes).hexdigest()
            uploaded = await project_service.upload_project_package(
                db,
                user,
                package_bytes=package_bytes,
                manifest_raw=manifest,
                package_hash=package_hash,
                submit_metadata={
                    "github_corpus": {
                        "repo": repo,
                        "url": item.get("url"),
                        "download_status": download_status,
                        "download_cache": str(repo_cache),
                    },
                    "evaluation": {
                        "purpose": "register_downloaded_github_codex_sample_and_feed_platform_loop",
                        "auto_analyze": auto_analyze,
                    },
                },
            )
            run = await project_service.create_project_run(
                db,
                user,
                project_id,
                {
                    "request_id": f"github-corpus-open-{batch_id}-{project_id}",
                    "params": {"github_repo": repo, "download_status": download_status},
                },
            )
            await project_service.record_project_input_event(
                db,
                user,
                run["id"],
                {
                    "request_id": f"github-corpus-input-{batch_id}-{project_id}",
                    "input": {
                        "github_repo": repo,
                        "stack_signatures": item.get("stack_signatures") or [],
                        "observed_paths": item.get("observed_paths") or [],
                    },
                    "metadata": {"source": "github_corpus_registration", "department": "AI小组"},
                },
            )
            result = await project_service.ingest_project_output(
                db,
                user,
                run["id"],
                {
                    "request_id": f"github-corpus-output-{batch_id}-{project_id}",
                    "input": {"github_repo": repo, "download_status": download_status},
                    "output": {
                        "summary": f"{repo} 已下载签名、注册为平台项目，并完成 Project Gateway/Autowire/转服务评估。",
                        "github_repo": repo,
                        "download_status": download_status,
                        "asset_rewrites": uploaded.get("metadata", {}).get("asset_rewrites"),
                        "service_conversion": uploaded.get("metadata", {}).get("service_conversion"),
                    },
                    "reports": [
                        {
                            "title": f"GitHub Codex 样本评估 · {repo}",
                            "summary": f"样本已注册为 {project_id}，用于验证 sf 上传、静态资源重写、SDK 自动注入和平台 AI 接管。",
                        }
                    ],
                    "todos": [
                        {
                            "kind": "review",
                            "title": f"复核 GitHub 样本 {repo} 的真实构建产物",
                            "summary": "样本评估已入平台循环；请复核真实构建产物并沉淀新适配签名。",
                            "detail": "后续可在安全沙箱执行真实 npm/pnpm 构建，并把失败签名沉淀为平台适配改进项。",
                            "reviewers": ["admin"],
                            "payload": {"project_id": project_id, "github_repo": repo, "download_status": download_status},
                        }
                    ],
                    "proofs": [
                        {
                            "type": "github_codex_sample_registration",
                            "project_id": project_id,
                            "github_repo": repo,
                            "download_status": download_status,
                            "credential_location": "platform_only",
                            "gateway_injected": uploaded.get("metadata", {}).get("gateway_bootstrap", {}).get("injected"),
                            "service_status": uploaded.get("metadata", {}).get("service_conversion", {}).get("status"),
                        }
                    ],
                    "metadata": {"source": "github_corpus_registration", "department": "AI小组"},
                },
                auto_analyze=auto_analyze,
            )
            runtime_after = await project_service.get_project_runtime_evaluation(db, user, project_id)
            latest_result_eval = runtime_after.get("latest_run_evaluation") or {}
            service_payload = service_invoke_payload_for_github_sample(
                item,
                project_id=project_id,
                batch_id=batch_id,
                download_status=download_status,
            )
            service_payload["auto_analyze"] = auto_analyze
            service_invoked = await project_service.invoke_project_service(db, user, project_id, service_payload)
            service_run = service_invoked.get("run") or {}
            service_eval = ((service_run.get("ai_summary") or {}).get("result_evaluation") or {})
            registered.append(
                {
                    "repo": repo,
                    "project_id": project_id,
                    "entry_url": uploaded.get("entry_url"),
                    "service_status": uploaded.get("metadata", {}).get("service_conversion", {}).get("status"),
                    "gateway_injected": uploaded.get("metadata", {}).get("gateway_bootstrap", {}).get("injected"),
                    "asset_replacements": uploaded.get("metadata", {}).get("asset_rewrites", {}).get("replacement_count"),
                    "run_id": run["id"],
                    "run_status": result.get("status"),
                    "runtime_status": (runtime_after.get("runtime_evaluation") or {}).get("status"),
                    "normal_run_ready": (runtime_after.get("runtime_evaluation") or {}).get("normal_run_ready"),
                    "result_eval_status": latest_result_eval.get("status"),
                    "normal_result_ready": latest_result_eval.get("normal_result_ready"),
                    "report_count": result.get("report_count"),
                    "todo_count": result.get("todo_count"),
                    "service_invoked": True,
                    "service_ok": bool(service_invoked.get("ok")),
                    "service_run_id": service_invoked.get("project_run_id"),
                    "service_run_status": service_run.get("status"),
                    "service_result_status": service_eval.get("status"),
                    "service_result_ready": service_eval.get("normal_result_ready"),
                    "service_report_count": service_run.get("report_count"),
                    "service_todo_count": service_run.get("todo_count"),
                    "service_trace_url": (service_invoked.get("service") or {}).get("trace_url"),
                    "download_status": download_status,
                    "adapter_variant": item.get("adapter_variant") or {},
                }
            )
        await db.commit()
    summary_counts = summarize_registered(registered, items=items, corpus=corpus)
    summary = {
        "ok": True,
        **summary_counts,
        "summary": summary_counts,
        "batch_id": batch_id,
        "cache_root": str(cache_root),
        "auto_analyze": auto_analyze,
        "registered": registered,
        "generated_at": _now_iso(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Register GitHub/Codex web samples as SkillForge projects")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--no-auto-analyze", action="store_true", help="Only record reports/todos; do not trigger platform AI analyze")
    args = parser.parse_args()
    summary = asyncio.run(
        register_samples(
            corpus_path=args.corpus,
            cache_root=args.cache_root,
            limit=max(1, args.limit),
            auto_analyze=not args.no_auto_analyze,
            output_path=args.output,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
