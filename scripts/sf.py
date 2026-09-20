#!/usr/bin/env python3
"""SkillForge CLI used by the Codex /sf skill."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import webbrowser
import difflib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
import yaml


CONFIG_DIR = Path.home() / ".skillforge"
CONFIG_PATH = CONFIG_DIR / "codex-cli.json"
CLI_VERSION = "0.3.7"
PLUGIN_NAME = "skillforge-codex"
DEFAULT_PLATFORM_URL = "http://skillforge.example.com"
DEFAULT_BASE_URL = DEFAULT_PLATFORM_URL
POLL_REDIRECT_URI = "urn:skillforge:codex:poll"
_RUNTIME_BASE_URL = ""
SF_HELP_TEXT = """SkillForge /sf 常用命令

/sf auth                 登录状态，失效时扫码登录
/sf 权限                 查看新增、发布、MCP/API 权限
/sf 查看哪些技能          列出你可见的 Skill
/sf 拉取 <skill_id>       拉取你有权读取的 Skill 到本地
/sf 查看我能编辑哪些技能   列出你可编辑的 Skill
/sf 查看我能发布哪些技能   列出你可提交审核或发布的 Skill
/sf 哪些未上传            扫描本地 Skill，比较哪些还没提交平台
/sf 当前目录有没有上传     查看当前 Skill 的本地/平台状态
/sf mcp                  查看可用 MCP 工具
/sf mcp call skillforge_cloud_video_daily_person_video_report 拉取云视频每日人员-视频消耗数据
/sf agent coverage       查看部门执行/分析/训练 Agent 覆盖度
/sf agent analysis create 创建部门业务分析 Agent blueprint
/sf ai analyze           调用平台 AI（DeepSeek V4 Pro 1M 上下文）
/sf raw query            查询 Skill 运行后的原始数据
/sf run analyze          一键读取运行数据并调用平台 AI 复盘
/sf run candidate        基于运行复盘生成训练候选任务
/sf training jobs        查看训练资源、数据资产、任务、部署并触发全流程动作
/sf media status <node>  查看 H3 安装真实进度、速率和滚动 ETA
/sf media bootstrap <node> --accept-license 以 3 MB/s 受控安装 H3 全模式环境
/sf media refresh-bridge <node> 强制节点拉取当前平台维护的 Bridge 脚本并重连
/sf project init        初始化网页/外部 URL 项目包
/sf project spec        输出 Codex 生成项目所需的标准和配方
/sf project doctor      校验项目入口、能力声明和输出 contract
/sf project submit      上传静态包或自动登记外部 URL 项目
/sf project verify      验证上传项目的服务调用/Trace 闭环
/sf project status <id> 查看项目版本、运行和 AI 状态
/sf project logs <run>  分页查看项目运行脱敏记录
/sf project invoke <id> 把上传项目作为平台 API 服务调用
/sf project asset upload <run> <file> 上传项目运行资产
/sf doctor               检查当前 Skill 包
/sf test                 离线测试，不调真实 MCP
/sf test-real --shop-id x 本地调试，真实 MCP 在平台执行
/sf sandbox              平台沙箱运行，不影响生产
/sf submit               提交审核，不直接发布生产
/sf review <id>          查看审核状态
/sf update               更新本地 Codex 插件和 sf CLI
/sf plugin doctor        诊断插件安装、登录、MCP 和 Project API；--fix 修复本地配置
/sf capabilities         刷新并查看平台能力目录"""
PACKAGE_EXACT = {
    "SKILL.md",
    "contract.json",
    "skillforge.yaml",
    "policy_pack.yaml",
    "policy.yaml",
    "intent.md",
    "source_contract.yaml",
    "metric_registry.yaml",
}
PACKAGE_DIRS = ("scripts/", "tests/", "fixtures/", "references/", "assets/", "prompts/")
PROJECT_MANIFEST_NAMES = (
    "projectforge.yaml",
    "projectforge.yml",
    "projectforge.json",
    "skillforge-project.yaml",
    "skillforge-project.yml",
    "manifest.yaml",
    "manifest.yml",
    "manifest.json",
)
PROJECT_PACKAGE_EXACT = {
    *PROJECT_MANIFEST_NAMES,
    "README.md",
    "readme.md",
    "package.json",
    "vite.config.ts",
    "vite.config.js",
    "index.html",
    "favicon.ico",
    "robots.txt",
    "asset-manifest.json",
    "manifest.webmanifest",
    "site.webmanifest",
}
PROJECT_PACKAGE_DIRS = (
    "web/",
    "frontend/",
    "client/",
    "apps/",
    "dist/",
    "build/",
    "out/",
    "public/",
    "assets/",
    "static/",
    "_next/",
    "images/",
    "fonts/",
    "src/",
)
PROJECT_ENTRY_CANDIDATES = (
    "web/index.html",
    "frontend/dist/index.html",
    "frontend/build/index.html",
    "frontend/out/index.html",
    "client/dist/index.html",
    "client/build/index.html",
    "client/out/index.html",
    "apps/web/dist/index.html",
    "apps/web/build/index.html",
    "apps/web/out/index.html",
    "dist/index.html",
    "build/index.html",
    "out/index.html",
    "public/index.html",
    "index.html",
)
PROJECT_STATIC_SECURITY_SUFFIXES = {
    ".html",
    ".htm",
    ".css",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".vue",
    ".svelte",
    ".json",
    ".map",
}
PROJECT_STATIC_SECURITY_SCAN_BYTES = 1024 * 1024
PROJECT_FRONTEND_FORBIDDEN_PATTERNS = (
    ("openai_api_key", re.compile(r"\b(?:VITE_|NEXT_PUBLIC_)?OPENAI[_-]?(?:API[_-]?)?KEY\b", re.IGNORECASE)),
    ("anthropic_api_key", re.compile(r"\b(?:VITE_|NEXT_PUBLIC_)?ANTHROPIC[_-]?(?:API[_-]?)?KEY\b", re.IGNORECASE)),
    ("gemini_api_key", re.compile(r"\b(?:VITE_|NEXT_PUBLIC_)?(?:GEMINI|GOOGLE)[_-]?(?:API[_-]?)?KEY\b", re.IGNORECASE)),
    ("dashscope_api_key", re.compile(r"\b(?:VITE_|NEXT_PUBLIC_)?DASHSCOPE[_-]?(?:API[_-]?)?KEY\b", re.IGNORECASE)),
    ("deepseek_api_key", re.compile(r"\b(?:VITE_|NEXT_PUBLIC_)?DEEPSEEK[_-]?(?:API[_-]?)?KEY\b", re.IGNORECASE)),
    ("llm_secret_token", re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{20,}\b")),
    ("bearer_llm_secret", re.compile(r"Authorization\s*[:=]\s*['\"]?Bearer\s+sk-[A-Za-z0-9_-]{8,}", re.IGNORECASE)),
)
PROJECT_FRONTEND_AI_ENDPOINT_PATTERNS = (
    ("openai_endpoint", re.compile(r"https?://api\.openai\.com/|api\.openai\.com/v1", re.IGNORECASE)),
    ("anthropic_endpoint", re.compile(r"https?://api\.anthropic\.com/|anthropic\.com/v1", re.IGNORECASE)),
    (
        "gemini_endpoint",
        re.compile(r"https?://generativelanguage\.googleapis\.com/|generativelanguage\.googleapis\.com/v1", re.IGNORECASE),
    ),
    ("dashscope_endpoint", re.compile(r"https?://dashscope\.aliyuncs\.com/|dashscope\.aliyuncs\.com/api/", re.IGNORECASE)),
    ("deepseek_endpoint", re.compile(r"https?://api\.deepseek\.com/|api\.deepseek\.com/v1", re.IGNORECASE)),
)
PROJECT_UNBUILT_FRONTEND_REF_PATTERNS = (
    (
        "html_attr",
        re.compile(
            r"""\b(?:src|href)\s*=\s*["']([^"']+\.(?:ts|tsx|jsx|vue|svelte)(?:[?#][^"']*)?)["']""",
            re.IGNORECASE,
        ),
    ),
    (
        "module_import",
        re.compile(
            r"""\b(?:from|import)\s*["']([^"']+\.(?:ts|tsx|jsx|vue|svelte)(?:[?#][^"']*)?)["']""",
            re.IGNORECASE,
        ),
    ),
    (
        "dynamic_import",
        re.compile(
            r"""\bimport\s*\(\s*["']([^"']+\.(?:ts|tsx|jsx|vue|svelte)(?:[?#][^"']*)?)["']""",
            re.IGNORECASE,
        ),
    ),
)

PROJECT_RECIPE_SHORT_VIDEO_ANALYSIS = "short-video-analysis"


def project_generation_spec(recipe: str = "") -> dict[str, Any]:
    recipe = str(recipe or "").strip()
    base = {
        "version": 1,
        "purpose": "让 Codex 在不读取平台密钥的前提下生成可上传到 SkillForge Project Host 的轻量网页项目。",
        "project_contract": {
            "manifest_names": list(PROJECT_MANIFEST_NAMES),
            "required_fields": ["project_id", "name", "kind", "entry"],
            "kinds": ["web_static", "external_web", "dashboard", "internal_tool", "playbook_ref"],
            "visibility": ["company", "department", "private"],
            "entry_candidates": list(PROJECT_ENTRY_CANDIDATES),
            "capabilities": ["ai.generate", "ai.chat", "ai.analyze"],
            "outputs": {"reports": True, "todos": True, "proofs": True},
            "package": {
                "allowed_top_level_files": sorted(PROJECT_PACKAGE_EXACT),
                "allowed_directories": list(PROJECT_PACKAGE_DIRS),
                "runtime": "static_or_external_url",
                "containerized": False,
            },
        },
        "gateway_sdk": {
            "script": "/project-gateway-sdk.js",
            "global": "window.PlatformProjectGateway",
            "methods": [
                "ready",
                "startHeartbeat",
                "input",
                "ingest",
                "analyze",
                "capability",
                "uploadAsset",
                "listAssets",
            ],
            "rules": [
                "网页不得读取平台 Cookie、AI key、MCP env 或业务系统密钥。",
                "AI/数据/MCP 调用必须走 Project Gateway capability/analyze。",
                "业务输入先调用 input() 记录，再调用能力或 ingest() 输出。",
            ],
        },
        "run_assets": {
            "upload_method": "PlatformProjectGateway.uploadAsset({file, metadata})",
            "metadata_json_field": "metadata_json",
            "list_method": "PlatformProjectGateway.listAssets()",
            "rules": [
                "素材、数据文件和关键帧都作为 ProjectRunAsset 进入当前 ProjectRun。",
                "metadata 必须写清 role/source，便于服务端视觉诊断和 Trace 归因。",
            ],
        },
        "visual_analysis": {
            "video_metadata": {
                "role": "material_a|material_b",
                "material_role": "material_a|material_b",
                "video_role": "material_a|material_b",
                "asset_type": "original_video",
            },
            "frame_metadata": {
                "role": "material_a|material_b",
                "asset_type": "visual_frame",
                "frame_time": "seconds",
                "frame_label": "opening|middle|ending|frame",
                "source_file": "原视频文件名",
            },
            "fallback": "原视频上传或视觉模型失败时，服务端会使用关键帧序列兜底；Codex 生成项目必须上传可用关键帧。",
        },
        "generation_recipes": ["short-video-analysis"],
    }
    if recipe and recipe != PROJECT_RECIPE_SHORT_VIDEO_ANALYSIS:
        raise SystemExit(f"不支持的项目配方: {recipe}")
    if recipe == PROJECT_RECIPE_SHORT_VIDEO_ANALYSIS:
        base["recipe"] = {
            "id": PROJECT_RECIPE_SHORT_VIDEO_ANALYSIS,
            "name": "短视频投放素材分析",
            "default_project_id": "short-video-analysis-mvp",
            "default_department": "示例品牌内容电商运营部",
            "default_department_id": "931765248",
            "default_visibility": "company",
            "default_owner": "祁莹莹",
            "inputs": [
                {"id": "data", "label": "投放数据", "required": True, "asset_metadata": {"role": "campaign_data"}},
                {"id": "material_a", "label": "素材 A", "required": True, "asset_metadata": {"role": "material_a"}},
                {"id": "material_b", "label": "素材 B", "required": True, "asset_metadata": {"role": "material_b"}},
            ],
            "ai_requirements": [
                "前端不得预设素材分数。",
                "Agent 诊断维度由服务端 AI 基于素材视觉证据和投放数据判断。",
                "必须输出素材表现判断、可复投依据、需改版点、下一版剪辑动作、需要补采的数据、证据和置信度。",
            ],
            "doctor_expectations": [
                "manifest 声明 ai.analyze 和 ai.generate。",
                "页面使用 Project Gateway SDK。",
                "页面上传运行资产。",
                "页面写入 material_a/material_b 视觉 metadata。",
                "页面实现关键帧兜底。",
                "输出 reports/proofs，必要时输出 todos。",
            ],
        }
    return base


def project_spec(args) -> None:
    data = project_generation_spec(str(getattr(args, "recipe", "") or ""))
    print_json(data)


def _project_source_text(root: Path) -> str:
    parts: list[str] = []
    for rel, path in iter_project_package_files(root):
        if PurePosixPath(rel).suffix.lower() not in PROJECT_STATIC_SECURITY_SUFFIXES:
            continue
        try:
            raw = path.read_bytes()[:PROJECT_STATIC_SECURITY_SCAN_BYTES]
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue
        parts.append(raw.decode("utf-8", errors="ignore"))
    return "\n".join(parts)


def _recipe_check(checks: list[dict[str, Any]], check_id: str, ok: bool, message: str) -> None:
    checks.append({"id": check_id, "ok": bool(ok), "message": message})


def project_recipe_checks(root: Path, manifest: dict[str, Any], recipe: str) -> dict[str, Any]:
    recipe = str(recipe or "").strip()
    if not recipe:
        return {"recipe": "", "ok": True, "checks": []}
    if recipe != PROJECT_RECIPE_SHORT_VIDEO_ANALYSIS:
        raise SystemExit(f"不支持的项目配方: {recipe}")
    text = _project_source_text(root)
    lower_text = text.lower()
    capabilities = {str(item) for item in (manifest.get("capabilities") or []) if str(item)}
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    checks: list[dict[str, Any]] = []
    _recipe_check(checks, "capability.ai_analyze", "ai.analyze" in capabilities, "manifest 必须声明 ai.analyze。")
    _recipe_check(checks, "capability.ai_generate", "ai.generate" in capabilities, "manifest 必须声明 ai.generate。")
    _recipe_check(
        checks,
        "gateway.sdk",
        "platformprojectgateway" in lower_text or "skillforgeproject" in lower_text or "/project-gateway-sdk.js" in lower_text,
        "页面必须使用 Project Gateway SDK。",
    )
    _recipe_check(
        checks,
        "gateway.input",
        ".input" in lower_text or "skillforge.project.input" in lower_text,
        "页面必须在分析前记录输入 input。",
    )
    _recipe_check(
        checks,
        "run_assets.upload",
        "uploadasset" in lower_text or "assets.upload" in lower_text or "skillforge.project.assets.upload" in lower_text,
        "页面必须上传运行资产。",
    )
    _recipe_check(
        checks,
        "visual.material_roles",
        "material_a" in lower_text and "material_b" in lower_text,
        "素材 A/B 的 metadata 必须包含 material_a 和 material_b。",
    )
    _recipe_check(
        checks,
        "visual.frame_fallback",
        ("frame_time" in lower_text or "framelabel" in lower_text or "frame_label" in lower_text)
        and ("canvas" in lower_text or "capture" in lower_text or "关键帧" in text),
        "必须实现关键帧提取/上传兜底。",
    )
    _recipe_check(
        checks,
        "ai.analyze_call",
        ".analyze" in lower_text or "ai.analyze" in lower_text,
        "页面必须触发平台 AI 分析。",
    )
    _recipe_check(
        checks,
        "outputs.reports",
        bool(outputs.get("reports")),
        "manifest outputs.reports 必须开启。",
    )
    _recipe_check(
        checks,
        "outputs.proofs",
        bool(outputs.get("proofs")),
        "manifest outputs.proofs 必须开启。",
    )
    return {"recipe": recipe, "ok": all(item["ok"] for item in checks), "checks": checks}
SKIP_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "skills-repo"}


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(data: dict) -> None:
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def _clean_base_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def set_runtime_base_url(value: str) -> None:
    global _RUNTIME_BASE_URL
    _RUNTIME_BASE_URL = _clean_base_url(value)


def env_base_url() -> str:
    return _clean_base_url(os.environ.get("SKILLFORGE_PLATFORM_URL") or os.environ.get("SKILLFORGE_BASE_URL") or "")


def configured_base_url() -> str:
    cfg = load_config()
    return _clean_base_url(cfg.get("base_url") or DEFAULT_BASE_URL)


def base_url_source() -> str:
    cfg = load_config()
    if _RUNTIME_BASE_URL:
        return "arg"
    if env_base_url():
        return "env"
    if cfg.get("base_url"):
        return "config"
    return "default"


def base_url() -> str:
    return _RUNTIME_BASE_URL or env_base_url() or configured_base_url()


def base_url_is_transient() -> bool:
    return base_url_source() in {"arg", "env"}


def token() -> str:
    return str(load_config().get("access_token") or "")


def client_headers(accept: str = "application/json") -> dict[str, str]:
    return {
        "Accept": accept,
        "X-SkillForge-SF-Version": local_plugin_version(),
        "X-SkillForge-Plugin": PLUGIN_NAME,
        "X-SkillForge-Client-Platform": platform.platform()[:120],
        "X-SkillForge-Client-Python": platform.python_version(),
        "X-SkillForge-Base-URL-Source": base_url_source(),
    }


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def normalize_sha256(value: str) -> str:
    text = str(value or "").strip().lower()
    return text.removeprefix("sha256:")


def response_json(resp: requests.Response, *, path: str = "") -> Any:
    try:
        return resp.json()
    except Exception as exc:
        target = path or getattr(resp, "url", "") or ""
        status = getattr(resp, "status_code", "")
        content_type = str(getattr(resp, "headers", {}).get("content-type", "") or "")
        preview = str(getattr(resp, "text", "") or "")[:500]
        context = " ".join(str(item) for item in (status, target) if str(item).strip())
        suffix = f" ({context})" if context else ""
        ctype = f" content-type={content_type}" if content_type else ""
        raise SystemExit(f"响应不是 JSON{suffix}:{ctype} {preview}") from exc


def version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(value or "0"))
    return tuple(int(part) for part in parts[:4]) or (0,)


def is_newer_version(latest: str, current: str) -> bool:
    latest_tuple = version_tuple(latest)
    current_tuple = version_tuple(current)
    width = max(len(latest_tuple), len(current_tuple))
    return latest_tuple + (0,) * (width - len(latest_tuple)) > current_tuple + (0,) * (width - len(current_tuple))


def api_request(method: str, path: str, *, json_body=None, data=None, files=None, auth: bool = True, retry_login: bool = True):
    headers = client_headers("application/json")
    if auth:
        current_token = token()
        if not current_token:
            if not retry_login:
                raise SystemExit("未登录。请先执行: sf auth login")
            auth_login(argparse.Namespace())
            current_token = token()
        headers["Authorization"] = f"Bearer {current_token}"
    try:
        resp = requests.request(
            method,
            f"{base_url()}{path}",
            json=json_body,
            data=data,
            files=files,
            headers=headers,
            timeout=180,
        )
    except requests.RequestException as exc:
        raise SystemExit(f"无法连接 SkillForge: {base_url()} ({exc})") from exc
    if resp.status_code in {401, 403} and auth and retry_login:
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        if payload.get("code") in {"AUTH_REQUIRED", "TOKEN_EXPIRED", "TOKEN_REVOKED", "USER_DISABLED", "PERMISSION_REV_CHANGED"}:
            if payload.get("code") == "USER_DISABLED":
                raise SystemExit("账号已禁用，无法继续。")
            auth_login(argparse.Namespace())
            return api_request(method, path, json_body=json_body, data=data, files=files, auth=auth, retry_login=False)
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise SystemExit(f"请求失败 {resp.status_code}: {detail}")
    return response_json(resp, path=path)


def api_download(path: str, *, auth: bool = True, retry_login: bool = True) -> tuple[bytes, dict[str, str]]:
    headers = client_headers("application/gzip")
    if auth:
        current_token = token()
        if not current_token:
            if not retry_login:
                raise SystemExit("未登录。请先执行: sf auth login")
            auth_login(argparse.Namespace())
            current_token = token()
        headers["Authorization"] = f"Bearer {current_token}"
    try:
        resp = requests.get(f"{base_url()}{path}", headers=headers, timeout=180)
    except requests.RequestException as exc:
        raise SystemExit(f"无法连接 SkillForge: {base_url()} ({exc})") from exc
    if resp.status_code in {401, 403} and auth and retry_login:
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        if payload.get("code") in {"AUTH_REQUIRED", "TOKEN_EXPIRED", "TOKEN_REVOKED", "USER_DISABLED", "PERMISSION_REV_CHANGED"}:
            if payload.get("code") == "USER_DISABLED":
                raise SystemExit("账号已禁用，无法继续。")
            auth_login(argparse.Namespace())
            return api_download(path, auth=auth, retry_login=False)
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise SystemExit(f"请求失败 {resp.status_code}: {detail}")
    return resp.content, {key.lower(): value for key, value in resp.headers.items()}


def api_request_bytes(method: str, path: str, *, auth: bool = True, retry_login: bool = True) -> tuple[bytes, requests.Response]:
    headers = {"Accept": "application/gzip, application/octet-stream"}
    if auth:
        current_token = token()
        if not current_token:
            if not retry_login:
                raise SystemExit("未登录。请先执行: sf auth login")
            auth_login(argparse.Namespace())
            current_token = token()
        headers["Authorization"] = f"Bearer {current_token}"
    try:
        resp = requests.request(method, f"{base_url()}{path}", headers=headers, timeout=180)
    except requests.RequestException as exc:
        raise SystemExit(f"无法连接 SkillForge: {base_url()} ({exc})") from exc
    if resp.status_code in {401, 403} and auth and retry_login:
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        if payload.get("code") in {"AUTH_REQUIRED", "TOKEN_EXPIRED", "TOKEN_REVOKED", "USER_DISABLED", "PERMISSION_REV_CHANGED"}:
            if payload.get("code") == "USER_DISABLED":
                raise SystemExit("账号已禁用，无法继续。")
            auth_login(argparse.Namespace())
            return api_request_bytes(method, path, auth=auth, retry_login=False)
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise SystemExit(f"请求失败 {resp.status_code}: {detail}")
    return resp.content, resp


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class CallbackState:
    code: str = ""
    state: str = ""
    login_intent_id: str = ""
    error: str = ""


def make_handler(callback_state: CallbackState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # noqa: D401
            return

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            callback_state.code = params.get("code", [""])[0]
            callback_state.state = params.get("state", [""])[0]
            callback_state.login_intent_id = params.get("login_intent_id", [""])[0]
            callback_state.error = params.get("error", [""])[0]
            body = "SkillForge 登录完成，可以回到 Codex。"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    return Handler


def auth_login(_args) -> None:
    verifier, challenge = pkce_pair()
    callback_state = CallbackState()
    server = None
    port = 0
    use_loopback = bool(getattr(_args, "loopback", False))
    if use_loopback:
        server = HTTPServer(("127.0.0.1", 0), make_handler(callback_state))
        port = server.server_address[1]
    state = secrets.token_urlsafe(18)
    intent = api_request(
        "POST",
        "/api/codex/auth/login-intent",
        json_body={
            "redirect_uri": f"http://127.0.0.1:{port}/callback" if use_loopback else POLL_REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "device_name": os.uname().nodename if hasattr(os, "uname") else "codex",
        },
        auth=False,
    )
    print(f"打开登录页: {intent['authorize_url']}", flush=True)
    print("请在浏览器完成授权；如果浏览器没有自动打开，请复制上面的链接手动打开。", flush=True)
    thread = None
    if server is not None:
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
    if not getattr(_args, "no_browser", False):
        def _open_browser() -> None:
            try:
                opened = webbrowser.open(intent["authorize_url"])
                if not opened:
                    print("无法自动打开浏览器，请手动打开上面的授权链接。", flush=True)
            except Exception:
                print("无法自动打开浏览器，请手动打开上面的授权链接。", flush=True)

        threading.Thread(target=_open_browser, daemon=True).start()
    if server is not None and thread is not None:
        thread.join(timeout=600)
        server.server_close()
        if not callback_state.code:
            raise SystemExit("登录超时或失败。")
        if callback_state.state != intent["state"]:
            raise SystemExit("登录 state 校验失败。")
        token_payload = api_request(
            "POST",
            "/api/codex/auth/token",
            json_body={
                "login_intent_id": callback_state.login_intent_id or intent["login_intent_id"],
                "auth_code": callback_state.code,
                "code_verifier": verifier,
            },
            auth=False,
        )
    else:
        deadline = time.time() + 600
        token_payload = None
        while time.time() < deadline:
            try:
                resp = requests.post(
                    f"{base_url()}/api/codex/auth/token",
                    json={
                        "login_intent_id": intent["login_intent_id"],
                        "auth_code": "",
                        "code_verifier": verifier,
                    },
                    headers=client_headers("application/json"),
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise SystemExit(f"无法连接 SkillForge: {base_url()} ({exc})") from exc
            if resp.status_code == 200:
                token_payload = resp.json()
                break
            try:
                detail = resp.json()
            except Exception:
                detail = {}
            code = detail.get("code") if isinstance(detail, dict) else ""
            if not code and isinstance(detail, dict) and isinstance(detail.get("error"), dict):
                code = detail["error"].get("code") or ""
            if code != "TOKEN_PENDING":
                raise SystemExit(f"登录失败 {resp.status_code}: {detail or resp.text}")
            time.sleep(2)
        if token_payload is None:
            raise SystemExit("登录超时或失败。")
    save_config(
        {
            **load_config(),
            "base_url": base_url(),
            "access_token": token_payload["access_token"],
            "expires_at": token_payload["expires_at"],
            "session_id": token_payload["session_id"],
            "user": token_payload["user"],
            "saved_at": int(time.time()),
        }
    )
    print(f"已登录: {token_payload['user']['name']}，有效期至 {token_payload['expires_at']}", flush=True)


def auth_status(_args) -> None:
    if not token():
        raise SystemExit("未登录。请执行: sf auth login")
    print_json(api_request("POST", "/api/codex/auth/introspect"))


def auth_logout(_args) -> None:
    if token():
        api_request("POST", "/api/codex/auth/logout")
    cfg = load_config()
    cfg.pop("access_token", None)
    save_config(cfg)
    print("已退出。")


def current_plugin_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / ".codex-plugin" / "plugin.json").exists():
            return parent
    return None


def default_plugin_root() -> Path:
    return Path.home() / "plugins" / PLUGIN_NAME


def target_plugin_root(args) -> Path:
    target = str(getattr(args, "target", "") or "").strip()
    if target:
        return Path(target).expanduser().resolve()
    return current_plugin_root() or default_plugin_root()


def local_plugin_version(plugin_root: Path | None = None) -> str:
    root = plugin_root or current_plugin_root()
    if not root:
        return CLI_VERSION
    try:
        data = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except Exception:
        return CLI_VERSION
    return str(data.get("version") or CLI_VERSION)


def resolve_url(maybe_url: str) -> str:
    text = str(maybe_url or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    return urljoin(f"{base_url()}/", text.lstrip("/"))


def download_bytes(url: str) -> bytes:
    try:
        resp = requests.get(url, timeout=180)
    except requests.RequestException as exc:
        raise SystemExit(f"无法下载更新包: {url} ({exc})") from exc
    if resp.status_code >= 400:
        raise SystemExit(f"下载更新包失败 {resp.status_code}: {resp.text[:500]}")
    return resp.content


def validate_tar_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SystemExit(f"更新包包含非法路径: {name}")
    return path


def extract_plugin_bundle(bundle: bytes, temp_root: Path) -> Path:
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
            for member in tar.getmembers():
                validate_tar_member(member.name)
                if member.isdev() or member.issym() or member.islnk():
                    raise SystemExit(f"更新包包含不支持的文件类型: {member.name}")
            tar.extractall(temp_root, filter="data")
    except tarfile.TarError as exc:
        raise SystemExit(f"更新包格式无效: {exc}") from exc
    candidates = [temp_root / PLUGIN_NAME, temp_root]
    for candidate in candidates:
        if (candidate / ".codex-plugin" / "plugin.json").exists():
            return candidate
    raise SystemExit("更新包缺少 .codex-plugin/plugin.json。")


def replace_plugin_tree(source: Path, target: Path) -> None:
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists():
        backup = target.with_name(f"{target.name}.backup-{int(time.time())}")
        if backup.exists():
            shutil.rmtree(backup)
        target.rename(backup)
    try:
        shutil.move(str(source), str(target))
    except Exception:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if backup and backup.exists():
            backup.rename(target)
        raise
    if backup and backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def marketplace_source_path(plugin_root: Path) -> str:
    home = Path.home().resolve()
    try:
        return "./" + plugin_root.resolve().relative_to(home).as_posix()
    except ValueError:
        return str(plugin_root.resolve())


def ensure_marketplace(plugin_root: Path) -> Path:
    path = Path.home() / ".agents" / "plugins" / "marketplace.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {"name": "skillforge-local", "interface": {"displayName": "SkillForge Local Plugins"}, "plugins": []}
    if not isinstance(data, dict):
        data = {"name": "skillforge-local", "interface": {"displayName": "SkillForge Local Plugins"}, "plugins": []}
    data.setdefault("name", "skillforge-local")
    data.setdefault("interface", {"displayName": "SkillForge Local Plugins"})
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        plugins = []
        data["plugins"] = plugins
    entry = {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": marketplace_source_path(plugin_root)},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }
    for idx, item in enumerate(plugins):
        if isinstance(item, dict) and item.get("name") == PLUGIN_NAME:
            plugins[idx] = entry
            break
    else:
        plugins.append(entry)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def ensure_codex_config_plugin_enabled(plugin_root: Path) -> Path:
    path = Path.home() / ".codex" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    additions = []
    if "[marketplaces.skillforge-local]" not in text:
        additions.append(
            "\n[marketplaces.skillforge-local]\n"
            'source_type = "local"\n'
            f"source = {json.dumps(str(Path.home()))}\n"
        )
    if f'[plugins."{PLUGIN_NAME}@skillforge-local"]' not in text:
        additions.append(f'\n[plugins."{PLUGIN_NAME}@skillforge-local"]\nenabled = true\n')
    if additions:
        suffix = "" if text.endswith("\n") or not text else "\n"
        path.write_text(text + suffix + "".join(additions), encoding="utf-8")
    return path


def codex_plugin_selectors() -> list[str]:
    path = Path.home() / ".codex" / "config.toml"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    selectors: list[str] = []
    for match in re.finditer(r'^\[plugins\."([^"]+)"\]', text, flags=re.MULTILINE):
        selector = match.group(1)
        if selector.startswith(f"{PLUGIN_NAME}@"):
            selectors.append(selector)
    return selectors


def refresh_codex_plugin_cache() -> dict[str, Any]:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return {"ok": False, "reason": "codex command not found"}
    selectors = codex_plugin_selectors() or [f"{PLUGIN_NAME}@skillforge-local"]
    attempts = []
    for selector in selectors:
        try:
            proc = subprocess.run(
                [codex_bin, "plugin", "add", selector],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as exc:
            attempts.append({"selector": selector, "returncode": None, "output": str(exc)})
            continue
        output = (proc.stdout or "") + (proc.stderr or "")
        attempts.append({"selector": selector, "returncode": proc.returncode, "output": output.strip()})
        if proc.returncode == 0:
            return {"ok": True, "selector": selector, "output": output.strip(), "attempts": attempts}
    return {"ok": False, "reason": "codex plugin add failed", "attempts": attempts}


def install_sf_shim(plugin_root: Path) -> Path:
    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "sf"
    script = plugin_root / "scripts" / "sf.py"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "export PYTHONUNBUFFERED=1\n"
        'PYTHON_BIN="${SKILLFORGE_SF_PYTHON:-python3}"\n'
        f'exec "$PYTHON_BIN" {shlex.quote(str(script))} "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def update_catalog_cache(manifest: dict, *, persist_base_url: bool = False) -> bool:
    if base_url_is_transient() and not persist_base_url:
        return False
    cfg = load_config()
    cfg["base_url"] = base_url()
    cfg["catalog"] = manifest
    save_config(cfg)
    return True


def update_info_from_manifest(manifest: dict) -> dict:
    update_info = manifest.get("plugin_update")
    if isinstance(update_info, dict):
        return update_info
    publisher = manifest.get("publisher_skill")
    if isinstance(publisher, dict):
        return {
            "name": publisher.get("name") or PLUGIN_NAME,
            "latest_version": publisher.get("latest_version") or publisher.get("recommended_version") or publisher.get("version"),
            "bundle_url": publisher.get("bundle_url"),
            "sha256": publisher.get("sha256"),
            "format": publisher.get("format") or "tar.gz",
            "recommended_cli_version": publisher.get("recommended_cli_version"),
        }
    legacy = manifest.get("update")
    if isinstance(legacy, dict):
        return {
            "name": PLUGIN_NAME,
            "latest_version": manifest.get("version"),
            "bundle_url": legacy.get("bundle_url"),
            "sha256": legacy.get("sha256"),
            "format": "tar.gz",
        }
    return {}


def release_notes_from_update_info(info: dict) -> list[str]:
    notes = info.get("release_notes") if isinstance(info, dict) else []
    if not isinstance(notes, list):
        return []
    return [str(note) for note in notes if str(note or "").strip()]


def update_notice_data(args) -> dict | None:
    try:
        manifest = api_request("GET", "/api/codex/catalog/manifest", auth=False)
    except SystemExit:
        return None
    info = update_info_from_manifest(manifest)
    plugin_root = target_plugin_root(args)
    current_version = local_plugin_version(plugin_root if plugin_root.exists() else None)
    latest_version = str(info.get("latest_version") or current_version or CLI_VERSION)
    bundle_url = str(info.get("bundle_url") or "")
    if not bundle_url or not is_newer_version(latest_version, current_version):
        return None
    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "release_notes": release_notes_from_update_info(info),
    }


def should_check_update_notice(argv: list[str], args) -> bool:
    if not argv:
        return False
    if getattr(args, "cmd", "") == "update":
        return False
    if getattr(args, "cmd", "") == "help":
        return False
    if getattr(args, "cmd", "") == "mcp" and getattr(args, "mcp_cmd", "") == "stdio":
        return False
    return True


def maybe_print_update_notice(argv: list[str], args) -> None:
    if not should_check_update_notice(argv, args):
        return
    notice = update_notice_data(args)
    if not notice:
        return
    print(
        f"SkillForge Codex 插件有新版本: 当前 {notice['current_version']}，最新 {notice['latest_version']}。"
        "请运行 sf update 升级。",
        file=sys.stderr,
        flush=True,
    )
    notes = notice.get("release_notes") or []
    if notes:
        print("更新内容:", file=sys.stderr, flush=True)
        for note in notes[:8]:
            print(f"- {note}", file=sys.stderr, flush=True)


def plugin_status_data(args) -> dict:
    manifest = api_request("GET", "/api/codex/catalog/manifest", auth=False)
    manifest_cached = update_catalog_cache(manifest, persist_base_url=bool(getattr(args, "persist_base_url", False)))
    info = update_info_from_manifest(manifest)
    plugin_root = target_plugin_root(args)
    installed = plugin_root.exists() and (plugin_root / ".codex-plugin" / "plugin.json").exists()
    current_version = local_plugin_version(plugin_root if installed else None)
    latest_version = str(info.get("latest_version") or current_version or CLI_VERSION)
    bundle_url = str(info.get("bundle_url") or "")
    version_update_available = bool(bundle_url) and is_newer_version(latest_version, current_version)
    install_required = bool(bundle_url) and not installed
    marketplace = Path.home() / ".agents" / "plugins" / "marketplace.json"
    codex_config = Path.home() / ".codex" / "config.toml"
    shim = Path.home() / ".local" / "bin" / "sf"
    codex_config_text = codex_config.read_text(encoding="utf-8") if codex_config.exists() else ""
    return {
        "ok": True,
        "plugin": PLUGIN_NAME,
        "base_url": base_url(),
        "base_url_source": base_url_source(),
        "persistent_base_url": configured_base_url(),
        "manifest_url": f"{base_url()}/api/codex/catalog/manifest",
        "manifest_cached": manifest_cached,
        "plugin_root": str(plugin_root),
        "installed": installed,
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": version_update_available,
        "install_required": install_required,
        "bundle_url": resolve_url(bundle_url) if bundle_url else "",
        "sha256": str(info.get("sha256") or ""),
        "mcp_config": str(plugin_root / ".mcp.json"),
        "mcp_config_exists": (plugin_root / ".mcp.json").exists(),
        "marketplace": str(marketplace),
        "marketplace_exists": marketplace.exists(),
        "codex_config": str(codex_config),
        "codex_plugin_enabled": (
            f'[plugins."{PLUGIN_NAME}@skillforge-local"]' in codex_config_text
        ),
        "sf_shim": str(shim),
        "sf_shim_installed": shim.exists(),
    }


def plugin_status(args) -> None:
    print_json(plugin_status_data(args))


def _check_result(ok: bool, *, detail: dict | None = None, error: str = "", warning: bool = False) -> dict:
    result: dict[str, Any] = {"ok": bool(ok)}
    if warning:
        result["warning"] = True
    if detail:
        result.update(detail)
    if error:
        result["error"] = error
    return result


def _run_doctor_check(func) -> dict:
    try:
        return func()
    except SystemExit as exc:
        return _check_result(False, error=str(exc))
    except Exception as exc:
        return _check_result(False, error=str(exc))


def plugin_doctor_data(args) -> dict:
    plugin_root = target_plugin_root(args)
    fix = bool(getattr(args, "fix", False))
    status = _run_doctor_check(lambda: plugin_status_data(args))
    checks: dict[str, dict] = {"plugin_status": status}
    repaired: dict[str, Any] = {}

    installed = bool(status.get("installed")) if isinstance(status, dict) else False
    if fix:
        if plugin_root.exists() and installed:
            repaired["marketplace"] = str(ensure_marketplace(plugin_root))
            repaired["codex_config"] = str(ensure_codex_config_plugin_enabled(plugin_root))
            if not bool(getattr(args, "no_install_sf_shim", False)):
                repaired["sf_shim"] = str(install_sf_shim(plugin_root))
            repaired["codex_plugin_cache_refresh"] = refresh_codex_plugin_cache()
        else:
            repaired["skipped"] = "plugin_not_installed"

    checks["manifest"] = _run_doctor_check(
        lambda: _check_result(
            True,
            detail={
                "latest_version": str(
                    update_info_from_manifest(api_request("GET", "/api/codex/catalog/manifest", auth=False)).get("latest_version")
                    or ""
                ),
                "base_url": base_url(),
            },
        )
    )
    checks["auth"] = _run_doctor_check(
        lambda: _check_result(True, detail=api_request("POST", "/api/codex/auth/introspect", retry_login=False))
    )
    checks["mcp_catalog"] = _run_doctor_check(
        lambda: _check_result(True, detail={"servers": len(api_request("GET", "/api/codex/mcp-catalog", retry_login=False).get("servers") or [])})
    )

    def _project_api_check() -> dict:
        capabilities_payload = api_request("GET", "/api/codex/capabilities", retry_login=False)
        project_payload = capabilities_payload.get("project") if isinstance(capabilities_payload, dict) else None
        if not isinstance(project_payload, dict):
            return _check_result(False, error="/api/codex/capabilities 未返回 project 能力")
        return _check_result(bool(project_payload.get("can_submit")), detail=project_payload)

    checks["project_api"] = _run_doctor_check(_project_api_check)

    local_checks = {
        "plugin_root_exists": plugin_root.exists(),
        "plugin_manifest_exists": (plugin_root / ".codex-plugin" / "plugin.json").exists(),
        "mcp_config_exists": (plugin_root / ".mcp.json").exists(),
        "sf_shim_installed": (Path.home() / ".local" / "bin" / "sf").exists(),
        "codex_command": shutil.which("codex") or "",
    }
    checks["local_files"] = _check_result(
        bool(local_checks["plugin_manifest_exists"] and local_checks["mcp_config_exists"]),
        detail=local_checks,
    )
    checks["codex_cli"] = _check_result(bool(local_checks["codex_command"]), detail={"command": local_checks["codex_command"]}, warning=True)

    required = ["plugin_status", "manifest", "auth", "mcp_catalog", "project_api", "local_files"]
    ok = all(bool(checks[name].get("ok")) for name in required)
    return {
        "ok": ok,
        "plugin": PLUGIN_NAME,
        "base_url": base_url(),
        "plugin_root": str(plugin_root),
        "fixed": fix,
        "checks": checks,
        "repaired": repaired,
    }


def plugin_doctor(args) -> None:
    print_json(plugin_doctor_data(args))


def my_plugins_data(args) -> dict:
    status = plugin_status_data(args)
    skills = api_request("GET", "/api/codex/skills?scope=mine")
    items = skills.get("items") if isinstance(skills, dict) else []
    return {
        "ok": True,
        "plugin": status,
        "skills": items if isinstance(items, list) else [],
    }


def my_plugins(args) -> None:
    print_json(my_plugins_data(args))


def sf_update(args) -> None:
    manifest = api_request("GET", "/api/codex/catalog/manifest", auth=False)
    manifest_cached = update_catalog_cache(manifest, persist_base_url=bool(getattr(args, "persist_base_url", False)))
    info = update_info_from_manifest(manifest)
    plugin_root = target_plugin_root(args)
    current_version = local_plugin_version(plugin_root if plugin_root.exists() else None)
    latest_version = str(info.get("latest_version") or current_version or CLI_VERSION)
    bundle_url = str(info.get("bundle_url") or "")
    expected_sha = normalize_sha256(str(info.get("sha256") or ""))
    installed = plugin_root.exists() and (plugin_root / ".codex-plugin" / "plugin.json").exists()
    install_required = bool(bundle_url) and not installed
    version_update_available = bool(bundle_url) and is_newer_version(latest_version, current_version)
    needs_update = install_required or version_update_available
    check_only = bool(getattr(args, "check", False))

    result = {
        "ok": True,
        "plugin": PLUGIN_NAME,
        "base_url": base_url(),
        "base_url_source": base_url_source(),
        "persistent_base_url": configured_base_url(),
        "plugin_root": str(plugin_root),
        "installed": installed,
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": version_update_available,
        "install_required": install_required,
        "release_notes": release_notes_from_update_info(info),
        "manifest_cached": manifest_cached,
    }
    if check_only:
        print_json(result)
        return
    if not bundle_url:
        raise SystemExit("manifest 未提供插件更新包地址。")

    if needs_update or bool(getattr(args, "force", False)):
        bundle = download_bytes(resolve_url(bundle_url))
        actual_sha = hashlib.sha256(bundle).hexdigest()
        if expected_sha and actual_sha != expected_sha:
            raise SystemExit(f"更新包 sha256 校验失败: expected={expected_sha} actual={actual_sha}")
        with tempfile.TemporaryDirectory(prefix="skillforge-plugin-update-") as tmp:
            extracted = extract_plugin_bundle(bundle, Path(tmp))
            replace_plugin_tree(extracted, plugin_root)
        result["updated"] = True
        result["installed_version"] = local_plugin_version(plugin_root)
        result["sha256"] = f"sha256:{actual_sha}"
    else:
        result["updated"] = False
        result["installed_version"] = current_version

    marketplace = ensure_marketplace(plugin_root)
    codex_config = ensure_codex_config_plugin_enabled(plugin_root)
    result["marketplace"] = str(marketplace)
    result["codex_config"] = str(codex_config)
    result["codex_plugin_cache_refresh"] = refresh_codex_plugin_cache()
    if not bool(getattr(args, "no_install_sf_shim", False)):
        result["sf_shim"] = str(install_sf_shim(plugin_root))
    result["restart_codex_required"] = True
    print_json(result)


def allowed_package_path(path: str) -> bool:
    return path in PACKAGE_EXACT or any(path.startswith(prefix) for prefix in PACKAGE_DIRS)


def normalize_path(path: Path, root: Path) -> str:
    rel = str(path.relative_to(root)).replace("\\", "/").strip("/")
    pure = PurePosixPath(rel)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"invalid path: {rel}")
    return str(pure)


def iter_package_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_NAMES or part.startswith(".") for part in rel_parts):
            continue
        rel = normalize_path(path, root)
        if allowed_package_path(rel):
            yield rel, path


def read_frontmatter(root: Path) -> dict:
    skill_md = root / "SKILL.md"
    if not skill_md.exists():
        return {}
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    data = yaml.safe_load(parts[1]) or {}
    return data if isinstance(data, dict) else {}


def read_skillforge_yaml(root: Path) -> dict:
    path = root / "skillforge.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def skill_id_for(root: Path) -> str:
    sf_yaml = read_skillforge_yaml(root)
    fm = read_frontmatter(root)
    metadata = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    return str(sf_yaml.get("skill_id") or metadata.get("skill_id") or fm.get("name") or root.name).strip()


def package_hash(file_items: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for rel, data in sorted(file_items, key=lambda item: item[0]):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def build_package(root: Path) -> tuple[bytes, str, dict]:
    root = root.resolve()
    file_items = [(rel, path.read_bytes()) for rel, path in iter_package_files(root)]
    if not file_items:
        raise SystemExit(f"未找到可提交的 Skill 文件: {root}")
    digest = package_hash(file_items)
    manifest = {
        "skill_id": skill_id_for(root),
        "frontmatter": read_frontmatter(root),
        "base_commit": read_skillforge_yaml(root).get("base_commit"),
    }
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            for rel, data in file_items:
                src = root / rel
                info = tar.gettarinfo(str(src), arcname=rel)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                with tempfile.NamedTemporaryFile(delete=False) as one:
                    one.write(data)
                    one_path = Path(one.name)
                try:
                    with one_path.open("rb") as fh:
                        tar.addfile(info, fh)
                finally:
                    one_path.unlink(missing_ok=True)
        return tmp_path.read_bytes(), digest, manifest
    finally:
        tmp_path.unlink(missing_ok=True)


def skill_list(args) -> None:
    print_json(api_request("GET", f"/api/codex/skills?scope={args.scope}"))


def run_skill(args) -> None:
    params = apply_arg_overrides(parse_json_object(args.params, field_name="--params"), args.arg)
    print_json(
        api_request(
            "POST",
            f"/api/codex/skills/{args.skill_id}/run",
            json_body={
                "params": params,
                "sandbox": bool(args.sandbox),
                "run_mode": args.run_mode or None,
                "parent_run_id": args.parent_run_id or None,
                "sample_used": args.sample_used,
            },
        )
    )


def preview_content_type(path: str, explicit: str) -> str:
    if explicit and explicit != "auto":
        return explicit
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in {".txt", ".log"}:
        return "text"
    return "auto"


def read_preview_content(path: str, content_type: str) -> tuple[Any, str, str]:
    if path == "-":
        text = sys.stdin.read()
        source_name = "stdin"
    else:
        source = Path(path).expanduser()
        if not source.exists() or not source.is_file():
            raise SystemExit(f"输出文件不存在: {source}")
        text = source.read_text(encoding="utf-8")
        source_name = source.name
    resolved_type = preview_content_type(path, content_type)
    if resolved_type == "json":
        try:
            return json.loads(text), resolved_type, source_name
        except json.JSONDecodeError as exc:
            raise SystemExit(f"输出文件不是合法 JSON: {exc}") from exc
    if resolved_type == "auto":
        stripped = text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(text), "json", source_name
            except json.JSONDecodeError:
                pass
    return text, resolved_type, source_name


def preview_output(args) -> None:
    content, content_type, source_name = read_preview_content(args.file, args.content_type)
    source_name_arg = str(args.source_name or "").strip() or source_name
    result = api_request(
        "POST",
        "/api/codex/previews",
        json_body={
            "title": str(args.title or "").strip() or None,
            "source_name": source_name_arg,
            "content_type": content_type,
            "content": content,
            "expires_in_hours": args.expires_in_hours,
        },
    )
    print_json(result)


def preview_apply(args) -> None:
    params = apply_arg_overrides(parse_json_object(args.params, field_name="--params"), args.arg)
    if args.real and not args.idempotency_key:
        raise SystemExit("真实 apply 需要 --idempotency-key")
    print_json(
        api_request(
            "POST",
            f"/api/codex/previews/{args.preview_id}/apply",
            json_body={
                "real": bool(args.real),
                "idempotency_key": args.idempotency_key or None,
                "skill_id": args.skill_id or None,
                "run_id": args.run_id or None,
                "params": params,
                "run_mode": args.run_mode,
                "triggered_by": args.triggered_by or None,
                "parent_run_id": args.parent_run_id or None,
                "sample_used": args.sample_used,
            },
        )
    )


def runs_list(args) -> None:
    query = []
    if args.skill_id:
        query.append(f"skill_id={requests.utils.quote(args.skill_id)}")
    if args.status:
        query.append(f"status={requests.utils.quote(args.status)}")
    query.append(f"page={int(args.page)}")
    query.append(f"page_size={int(args.page_size)}")
    print_json(api_request("GET", "/api/codex/runs?" + "&".join(query)))


def runs_status(args) -> None:
    print_json(api_request("GET", f"/api/codex/runs/{args.run_id}"))


def runs_result(args) -> None:
    print_json(api_request("GET", f"/api/codex/runs/{args.run_id}/result"))


def runs_steps(args) -> None:
    print_json(api_request("GET", f"/api/codex/runs/{args.run_id}/steps"))


def runs_logs(args) -> None:
    query = f"?stream={requests.utils.quote(args.stream)}" if args.stream else ""
    print_json(api_request("GET", f"/api/codex/runs/{args.run_id}/logs{query}"))


def runs_artifacts(args) -> None:
    query = []
    if args.kind:
        query.append(f"kind={requests.utils.quote(args.kind)}")
    if args.include_content:
        query.append("include_content=true")
    if args.max_bytes:
        query.append(f"max_bytes={int(args.max_bytes)}")
    path = f"/api/codex/runs/{args.run_id}/artifacts"
    if query:
        path += "?" + "&".join(query)
    result = api_request("GET", path)
    if args.output:
        out = Path(args.output).expanduser()
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print_json({"ok": True, "path": str(out.resolve()), "run_id": args.run_id})
        return
    print_json(result)


def runs_diagnose(args) -> None:
    print_json(api_request("GET", f"/api/codex/runs/{args.run_id}/diagnose"))


def validate_skill_package_member(name: str) -> PurePosixPath:
    path = validate_tar_member(name)
    if path.parts and path.parts[0] == PLUGIN_NAME:
        raise SystemExit(f"Skill 包路径异常: {name}")
    return path


def install_skill_package_bytes(package: bytes, target: Path, *, force: bool) -> list[str]:
    target = target.expanduser().resolve()
    if target.exists() and any(target.iterdir()) and not force:
        raise SystemExit(f"目标目录非空: {target}。如需覆盖请加 --force")
    if target.exists() and force:
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                validate_skill_package_member(member.name)
                if member.isdev() or member.issym() or member.islnk():
                    raise SystemExit(f"Skill 包包含不支持的文件类型: {member.name}")
                if member.isfile() and not allowed_package_path(str(PurePosixPath(member.name))):
                    raise SystemExit(f"Skill 包包含不允许的路径: {member.name}")
            tar.extractall(target, filter="data")
    except tarfile.TarError as exc:
        raise SystemExit(f"Skill 包格式无效: {exc}") from exc
    return sorted(str(PurePosixPath(member.name)) for member in members if member.isfile())


def skill_install(args) -> None:
    skill_id = str(args.skill_id or "").strip()
    if not skill_id:
        raise SystemExit("缺少 skill_id")
    target = Path(args.path).expanduser()
    path_arg = str(args.path or "").strip()
    if path_arg in {"", "."}:
        target = Path.cwd() / skill_id
    elif target.name != skill_id:
        target = target / skill_id
    package, resp = api_request_bytes("GET", f"/api/codex/skills/{skill_id}/package")
    files = install_skill_package_bytes(package, target, force=bool(args.force))
    print_json(
        {
            "ok": True,
            "skill_id": skill_id,
            "path": str(target.resolve()),
            "base_commit": resp.headers.get("X-SkillForge-Skill-Commit") or "",
            "file_count": len(files),
            "files": files,
        }
    )


SKILL_TEMPLATES = ("basic", "mcp", "report-todos", "tmall", "yuyi", "cloud-video-weekly-diagnosis")


def _cloud_video_weekly_diagnosis_template(
    skill_id: str,
    *,
    name: str,
    description: str,
    department: str,
    trigger_type: str,
    cron: str,
    risk_level: str,
    template: str,
) -> dict[str, str]:
    title = name or "示例品牌云视频周度全量数据采集"
    desc = description or "每天 08:00 自动读取示例品牌团队过去一周云视频素材、消耗、上传量、卡审和拒因原始数据，沉淀为 raw-output 数据能力。"
    skill_md = '---\nname: {title}\ndescription: {desc}\ndepartment: {department}\ntrigger_type: {trigger_type}\ntrigger_expression: {cron}\nrisk_level: {risk_level}\nruntime:\n  backend: bridge_script\n  script_entry: scripts/main.py\n  timeout: 1800\n---\n\n# {title}\n\n每天 08:00 Asia/Shanghai 读取云视频示例品牌团队数据，默认采集截至昨日的滚动 7 个完整自然日。\n\n本 Skill 只负责自动读取和沉淀原始数据，不做 AI 诊断、不生成待办、不推送钉钉。后续分析控制 Skill 通过平台数据能力读取本 Skill 的 raw-output artifact，再决定分析和推送策略。\n\n## 数据源\n\n- 云视频团队树：`skillforge_cloud_video_accounts`\n- 素材元数据：`skillforge_cloud_video_videos`\n- 投放消耗：`skillforge_cloud_video_daily_person_video_report` / `skillforge_cloud_video_ad_report`\n- 上传分母：`skillforge_cloud_video_video_usage_report`\n- 卡审拒因：`skillforge_cloud_video_audit_rejects`\n- 数据能力：`skillforge_samplebrand_cloud_video_data_latest` / `skillforge_samplebrand_cloud_video_data_get`\n\n## 输出\n\n- `reports`: 空数组。\n- `todos`: 空数组。\n- `raw_data.videos`: 团队过去一周素材视频全量元数据。\n- `raw_data.audit_videos`: 团队过去一周卡审视频全量元数据。\n- `raw_data.daily_report`: 每人每日每视频消耗明细。\n- `raw_data.usage_team` / `raw_data.usage_people`: 团队和个人上传/使用汇总。\n- `raw_data.audit_rejects`: 可获取的卡审拒因样本。\n- `_skillforge_meta.source_counts`: 各数据集行数和截断提示。\n\n## 默认口径\n\n- 时间窗口：滚动 7 天，截至运行日前一天。\n- 素材维度：示例品牌团队 `4016`，视频分区 `0=成片`，上传日期口径。\n- 消耗维度：巨量千川汇总，返回逐日人员和视频明细。\n'.format(
        title=title,
        desc=desc,
        department=department or "示例品牌内容电商运营部",
        trigger_type=trigger_type,
        cron=cron or "0 8 * * *",
        risk_level=risk_level,
    )
    contract = {'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object', 'required': ['collection_schema', 'reports', 'todos', 'raw_data', '_skillforge_meta'], 'properties': {'collection_schema': {'type': 'string'}, 'reports': {'type': 'array', 'items': {'type': 'object', 'required': ['channel', 'title', 'summary', 'recipients'], 'properties': {'channel': {'type': 'string', 'enum': ['dingtalk_card', 'dingtalk_markdown', 'email', 'feishu']}, 'title': {'type': 'string'}, 'summary': {'type': 'string'}, 'content_markdown': {'type': 'string'}, 'recipients': {'type': 'object', 'properties': {'users': {'type': 'array', 'items': {'type': 'string'}}, 'roles': {'type': 'array', 'items': {'type': 'string'}}, 'departments': {'type': 'array', 'items': {'type': 'string'}}}}, 'payload': {'type': 'object'}, 'primary_indicator': {'type': ['number', 'null']}}}}, 'todos': {'type': 'array', 'items': {'type': 'object', 'required': ['kind', 'title'], 'properties': {'kind': {'type': 'string', 'enum': ['review', 'dispatch']}, 'title': {'type': 'string'}, 'priority': {'type': 'string'}, 'summary': {'type': 'string'}, 'payload': {'type': 'object'}, 'tasks': {'type': 'array', 'items': {'type': 'object', 'required': ['content'], 'properties': {'executor': {'type': ['string', 'null']}, 'content': {'type': 'string'}, 'deadline': {'type': 'string'}, 'extra': {'type': 'object'}}}}, 'reviewers': {'type': 'array', 'items': {'type': 'string'}}, 'reviewer_role': {'type': 'string', 'enum': ['biz_owner', 'operator', 'ai_engineer', 'admin', 'director']}, 'sla_hours': {'type': 'number'}, 'decision_mode': {'type': 'string', 'enum': ['any_of', 'all_of', 'independent']}, 'callback': {'type': 'object'}}}}, 'raw_data': {'type': 'object', 'required': ['collection_schema', 'date_range', 'videos', 'audit_videos', 'daily_report', 'usage_team', 'usage_people', 'audit_rejects'], 'properties': {'collection_schema': {'type': 'string'}, 'department': {'type': 'string'}, 'team_id': {'type': 'string'}, 'team_name': {'type': 'string'}, 'date_range': {'type': 'object'}, 'videos': {'type': 'array'}, 'audit_videos': {'type': 'array'}, 'daily_report': {'type': 'object'}, 'usage_team': {'type': 'object'}, 'usage_people': {'type': 'object'}, 'audit_rejects': {'type': 'array'}}}, '_skillforge_meta': {'type': 'object'}}}}
    sample_input = {'dry_run': True, 'date_range': {'start_date': '2026-06-02', 'end_date': '2026-06-08'}, 'cloud_video_team_id': '4016', 'sample_data': True}
    main_py = 'from __future__ import annotations\n\nimport json\nimport sys\nfrom datetime import date, timedelta\nfrom typing import Any\n\ntry:\n    from skillforge_sdk import SkillForge\nexcept Exception:\n    class SkillForge:  # type: ignore[no-redef]\n        def __init__(self, skill_id: str):\n            self.skill_id = skill_id\n\n        def fetch_api(self, *args: Any, **kwargs: Any) -> dict[str, Any]:\n            raise RuntimeError("skillforge_sdk is required for real MCP calls")\n\n\nSKILL_ID = "__SKILL_ID__"\n\n\nTEAM_ID = "4016"\nTEAM_NAME = "示例品牌团队"\nDEPARTMENT = "示例品牌内容电商运营部"\nCOLLECTION_SCHEMA = "samplebrand_cloud_video_weekly_collection_v1"\nDEFAULT_REJECT_DETAIL_LIMIT = 50\nVIDEO_PAGE_SIZE = 60\nVIDEO_BATCH_MAX_PAGES = 20\nVIDEO_BATCH_MAX_ITEMS = 1000\nDAILY_REPORT_MAX_ROWS = 200000\nDAILY_REPORT_MAX_PERSON_ROWS = 10000\nDAILY_REPORT_TOP_N = 20\n\n\ndef _read_payload() -> dict[str, Any]:\n    raw = sys.stdin.read().strip()\n    if raw:\n        return json.loads(raw)\n    return {}\n\n\ndef _today() -> date:\n    return date.today()\n\n\ndef _default_range() -> tuple[str, str]:\n    end = _today() - timedelta(days=1)\n    start = end - timedelta(days=6)\n    return start.isoformat(), end.isoformat()\n\n\ndef _date_range(payload: dict[str, Any]) -> tuple[str, str]:\n    dr = payload.get("date_range") if isinstance(payload.get("date_range"), dict) else {}\n    start = str(dr.get("start_date") or payload.get("start_date") or "").strip()\n    end = str(dr.get("end_date") or payload.get("end_date") or "").strip()\n    if start and end:\n        return start, end\n    return _default_range()\n\n\ndef _fetch(sf: SkillForge, tool: str, args: dict[str, Any], *, dry_run: bool | None = None) -> dict[str, Any]:\n    return sf.fetch_api(f"mcp://{tool}", body=args, timeout=300, dry_run=dry_run)\n\n\ndef _int_or_none(value: Any) -> int | None:\n    try:\n        return int(value)\n    except (TypeError, ValueError):\n        return None\n\n\ndef _full_paged_videos(\n    sf: SkillForge,\n    args: dict[str, Any],\n    *,\n    label: str,\n    warnings: list[str] | None = None,\n) -> list[dict[str, Any]]:\n    items: list[dict[str, Any]] = []\n    page = max(1, int(args.get("page") or 1))\n    seen_pages: set[int] = set()\n    while page not in seen_pages:\n        seen_pages.add(page)\n        payload = {\n            **args,\n            "page": page,\n            "page_size": int(args.get("page_size") or VIDEO_PAGE_SIZE),\n            "auto_page": True,\n            "max_pages": VIDEO_BATCH_MAX_PAGES,\n            "max_items": VIDEO_BATCH_MAX_ITEMS,\n        }\n        try:\n            data = _fetch(sf, "skillforge_cloud_video_videos", payload)\n        except Exception as exc:\n            if warnings is not None:\n                warnings.append(f"{label} page {page} failed: {str(exc)[:160]}")\n            break\n        batch = data.get("items") if isinstance(data, dict) else []\n        if not isinstance(batch, list) or not batch:\n            break\n\n        items.extend(item for item in batch if isinstance(item, dict))\n\n        pagination = data.get("pagination") if isinstance(data.get("pagination"), dict) else {}\n        pages_read = _int_or_none(pagination.get("pages_read")) or 1\n        last_page = _int_or_none(data.get("page")) or (page + pages_read - 1)\n        total_pages = _int_or_none(data.get("total_pages"))\n        total = _int_or_none(data.get("total"))\n        if pagination.get("truncated") and warnings is not None:\n            warnings.append(\n                f"{label} batch truncated at page {last_page}; "\n                f"continue from page {last_page + 1}"\n            )\n        if total_pages is not None and last_page >= total_pages:\n            break\n        if total is not None and len(items) >= total:\n            break\n        next_page = last_page + 1\n        if next_page <= page:\n            if warnings is not None:\n                warnings.append(f"{label} pagination did not advance from page {page}")\n            break\n        page = next_page\n    return items\n\n\ndef _video_id(video: dict[str, Any]) -> str:\n    raw = video.get("raw") if isinstance(video.get("raw"), dict) else {}\n    return str(video.get("id") or video.get("video_id") or raw.get("videoId") or "")\n\n\ndef _cost_value(video: dict[str, Any]) -> float:\n    metrics = video.get("metrics") if isinstance(video.get("metrics"), dict) else {}\n    raw = video.get("raw") if isinstance(video.get("raw"), dict) else {}\n    for source in (metrics, raw, video):\n        for key in ("sumStatCost", "totalGlobalStatCost", "stat_cost", "cost", "statCost"):\n            try:\n                return float(source.get(key) or 0)\n            except Exception:\n                continue\n    return 0.0\n\n\ndef _merge_costs(videos: list[dict[str, Any]], daily_report: dict[str, Any]) -> None:\n    by_id = {str(item.get("id") or item.get("video_id") or ""): item for item in videos}\n    rows = daily_report.get("daily_video_rows") if isinstance(daily_report.get("daily_video_rows"), list) else []\n    for row in rows:\n        if not isinstance(row, dict):\n            continue\n        video_id = str(row.get("video_id") or row.get("id") or "").strip()\n        if not video_id or video_id not in by_id:\n            continue\n        target = by_id[video_id]\n        metrics = target.setdefault("metrics", {})\n        try:\n            metrics["stat_cost"] = float(metrics.get("stat_cost") or 0) + float(row.get("stat_cost") or row.get("cost") or 0)\n        except Exception:\n            pass\n\n\ndef _usage_summary(sf: SkillForge, start: str, end: str) -> tuple[dict[str, Any], dict[str, Any]]:\n    team = _fetch(sf, "skillforge_cloud_video_video_usage_report", {\n        "start_date": start, "end_date": end, "data_type": 3, "ids": [TEAM_ID], "video_type": 0\n    })\n    people = _fetch(sf, "skillforge_cloud_video_video_usage_report", {\n        "start_date": start, "end_date": end, "data_type": 1, "video_type": 0\n    })\n    return team, people\n\n\ndef _audit_label(video: dict[str, Any]) -> str:\n    raw = video.get("raw") if isinstance(video.get("raw"), dict) else {}\n    return str(raw.get("isAuditRejectStr") or raw.get("auditRejectStr") or video.get("audit_label") or "卡审").strip() or "卡审"\n\n\ndef _person_for_video(video: dict[str, Any]) -> str:\n    return str(video.get("account_name") or "未知人员")\n\n\ndef _audit_reject_details(sf: SkillForge, audit_videos: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:\n    details = []\n    seen: set[str] = set()\n    for video in audit_videos:\n        video_id = _video_id(video)\n        if not video_id or video_id in seen:\n            continue\n        seen.add(video_id)\n        try:\n            data = _fetch(sf, "skillforge_cloud_video_audit_rejects", {\n                "video_id": video_id,\n                "platform_type": 2,\n                "page_size": 20,\n            })\n        except Exception as exc:\n            details.append({\n                "video_id": video_id,\n                "title": video.get("title"),\n                "person": _person_for_video(video),\n                "category": video.get("category_name"),\n                "audit_label": _audit_label(video),\n                "error": str(exc)[:200],\n                "reject_reason_text": [],\n                "suggestion_text": [],\n            })\n            continue\n        reasons: list[str] = []\n        suggestions: list[str] = []\n        platform_names: list[str] = []\n        state_labels: list[str] = []\n        for item in data.get("items") or []:\n            if not isinstance(item, dict):\n                continue\n            reasons.extend(str(text) for text in item.get("reject_reason_text") or [] if str(text).strip())\n            suggestions.extend(str(text) for text in item.get("suggestion_text") or [] if str(text).strip())\n            if item.get("platform_name"):\n                platform_names.append(str(item.get("platform_name")))\n            if item.get("state_label"):\n                state_labels.append(str(item.get("state_label")))\n        details.append({\n            "video_id": video_id,\n            "title": video.get("title"),\n            "person": _person_for_video(video),\n            "category": video.get("category_name"),\n            "audit_label": _audit_label(video),\n            "platforms": sorted(set(platform_names)),\n            "states": sorted(set(state_labels)),\n            "reject_reason_text": reasons[:10],\n            "suggestion_text": suggestions[:10],\n        })\n        if len(details) >= limit:\n            break\n    return details\n\n\ndef _list_count(value: Any) -> int:\n    return len(value) if isinstance(value, list) else 0\n\n\ndef _source_counts(\n    *,\n    videos: list[dict[str, Any]],\n    audit_videos: list[dict[str, Any]],\n    daily_report: dict[str, Any],\n    usage_team: dict[str, Any],\n    usage_people: dict[str, Any],\n    reject_details: list[dict[str, Any]],\n) -> dict[str, Any]:\n    daily = daily_report.get("daily") if isinstance(daily_report.get("daily"), dict) else {}\n    return {\n        "videos": len(videos),\n        "audit_videos": len(audit_videos),\n        "audit_rejects": len(reject_details),\n        "daily_person_rows": daily.get("person_total_rows", _list_count(daily_report.get("daily_person_rows"))),\n        "daily_video_rows": daily.get("video_total_rows", _list_count(daily_report.get("daily_video_rows"))),\n        "daily_person_returned_rows": daily.get("person_returned_rows", _list_count(daily_report.get("daily_person_rows"))),\n        "daily_video_returned_rows": daily.get("video_returned_rows", _list_count(daily_report.get("daily_video_rows"))),\n        "usage_team_rows": _list_count(usage_team.get("details")),\n        "usage_people_rows": _list_count(usage_people.get("details")),\n    }\n\n\ndef main(payload: dict[str, Any]) -> dict[str, Any]:\n    sf = SkillForge(SKILL_ID)\n    start, end = _date_range(payload)\n    fetch_warnings: list[str] = []\n    reject_detail_limit = max(0, int(payload.get("audit_reject_detail_limit") or DEFAULT_REJECT_DETAIL_LIMIT))\n\n    if payload.get("sample_data"):\n        videos = payload.get("videos") or []\n        audit_videos = payload.get("audit_videos") or []\n        reject_details = payload.get("audit_rejects") if isinstance(payload.get("audit_rejects"), list) else []\n        usage_team = payload.get("usage_team") or {"summary": {"upload_count": len(videos)}}\n        usage_people = payload.get("usage_people") or {"details": []}\n        daily_report = {"daily_video_rows": []}\n    else:\n        videos = _full_paged_videos(sf, {\n            "search_type": 3,\n            "search_ids": [TEAM_ID],\n            "date_mode": "upload",\n            "start_date": start,\n            "end_date": end,\n            "page_size": VIDEO_PAGE_SIZE,\n            "video_type": 0,\n            "include_raw": True,\n        }, label="cloud_video_videos", warnings=fetch_warnings)\n        daily_report = _fetch(sf, "skillforge_cloud_video_daily_person_video_report", {\n            "start_date": start,\n            "end_date": end,\n            "top_videos_per_person": DAILY_REPORT_TOP_N,\n            "low_videos_per_person": DAILY_REPORT_TOP_N,\n            "include_daily_rows": True,\n            "max_video_rows": DAILY_REPORT_MAX_ROWS,\n            "max_returned_video_rows": DAILY_REPORT_MAX_ROWS,\n            "max_person_rows": DAILY_REPORT_MAX_PERSON_ROWS,\n        })\n        daily = daily_report.get("daily") if isinstance(daily_report.get("daily"), dict) else {}\n        if daily.get("truncated"):\n            fetch_warnings.append("skillforge_cloud_video_daily_person_video_report returned truncated daily rows")\n        _merge_costs(videos, daily_report)\n        audit_videos = _full_paged_videos(sf, {\n            "search_type": 3,\n            "search_ids": [TEAM_ID],\n            "system_auto_label_type": "1",\n            "date_mode": "upload",\n            "start_date": start,\n            "end_date": end,\n            "page_size": VIDEO_PAGE_SIZE,\n            "video_type": 0,\n            "include_raw": True,\n        }, label="cloud_video_audit_videos", warnings=fetch_warnings)\n        reject_details = _audit_reject_details(sf, audit_videos, limit=reject_detail_limit)\n        usage_team, usage_people = _usage_summary(sf, start, end)\n    source_counts = _source_counts(\n        videos=videos,\n        audit_videos=audit_videos,\n        daily_report=daily_report,\n        usage_team=usage_team,\n        usage_people=usage_people,\n        reject_details=reject_details,\n    )\n\n    return {\n        "collection_schema": COLLECTION_SCHEMA,\n        "reports": [],\n        "todos": [],\n        "raw_data": {\n            "collection_schema": COLLECTION_SCHEMA,\n            "department": DEPARTMENT,\n            "team_id": TEAM_ID,\n            "team_name": TEAM_NAME,\n            "date_range": {"start_date": start, "end_date": end, "window": "rolling_7_complete_days"},\n            "videos": videos,\n            "audit_videos": audit_videos,\n            "daily_report": daily_report,\n            "usage_team": usage_team,\n            "usage_people": usage_people,\n            "audit_rejects": reject_details,\n        },\n        "_skillforge_meta": {\n            "collection_schema": COLLECTION_SCHEMA,\n            "collector": True,\n            "department": DEPARTMENT,\n            "cloud_video_team_id": TEAM_ID,\n            "date_range": {"start_date": start, "end_date": end},\n            "source_counts": source_counts,\n            "fetch_warnings": fetch_warnings,\n            "analysis_disabled": True,\n            "todo_disabled": True,\n            "notification_disabled": True,\n            "sample_used": bool(payload.get("sample_data")),\n        },\n    }\n\n\nif __name__ == "__main__":\n    print(json.dumps(main(_read_payload()), ensure_ascii=False))\n'
    main_py = main_py.replace("__SKILL_ID__", skill_id)
    test_py = 'import json\nimport os\nimport subprocess\nimport sys\nfrom pathlib import Path\nimport importlib.util\n\n\ndef _load_module():\n    root = Path(__file__).resolve().parents[1]\n    spec = importlib.util.spec_from_file_location("samplebrand_skill_main", root / "scripts" / "main.py")\n    module = importlib.util.module_from_spec(spec)\n    assert spec and spec.loader\n    spec.loader.exec_module(module)\n    return module\n\n\ndef test_sample_run_outputs_collector_payload():\n    root = Path(__file__).resolve().parents[1]\n    proc = subprocess.run(\n        [sys.executable, str(root / "scripts" / "main.py")],\n        input=json.dumps({"sample_data": True, "dry_run": True, "videos": []}),\n        text=True,\n        capture_output=True,\n        check=True,\n    )\n    data = json.loads(proc.stdout)\n    assert data["collection_schema"] == "samplebrand_cloud_video_weekly_collection_v1"\n    assert data["reports"] == []\n    assert data["todos"] == []\n    assert data["raw_data"]["collection_schema"] == "samplebrand_cloud_video_weekly_collection_v1"\n    assert data["_skillforge_meta"]["cloud_video_team_id"] == "4016"\n    assert data["_skillforge_meta"]["analysis_disabled"] is True\n    assert data["_skillforge_meta"]["notification_disabled"] is True\n\n\ndef test_collector_does_not_analyze_or_notify(monkeypatch):\n    module = _load_module()\n    calls = []\n\n    class FakeSkillForge:\n        def __init__(self, skill_id):\n            self.skill_id = skill_id\n\n        def fetch_api(self, *args, **kwargs):\n            calls.append((args, kwargs))\n            return {}\n\n        def analyze(self, **kwargs):\n            raise AssertionError("collector must not call sf.analyze")\n\n    monkeypatch.setattr(module, "SkillForge", FakeSkillForge)\n    monkeypatch.setenv("SKILLFORGE_RUN_MODE", "scheduled_real")\n    output = module.main({"sample_data": True, "notify": {"real": True}, "videos": []})\n\n    assert output["reports"] == []\n    assert output["todos"] == []\n    assert calls == []\n\n\ndef test_full_paged_videos_reads_multiple_batches(monkeypatch):\n    module = _load_module()\n\n    class FakeSkillForge:\n        def fetch_api(self, _url, *, body, **_kwargs):\n            page = body["page"]\n            if page == 1:\n                return {\n                    "page": 20,\n                    "total": 61,\n                    "total_pages": 21,\n                    "items": [{"id": str(i)} for i in range(60)],\n                    "pagination": {"pages_read": 20, "truncated": True},\n                }\n            if page == 21:\n                return {\n                    "page": 21,\n                    "total": 61,\n                    "total_pages": 21,\n                    "items": [{"id": "60"}],\n                    "pagination": {"pages_read": 1, "truncated": False},\n                }\n            raise AssertionError(f"unexpected page {page}")\n\n    warnings = []\n    rows = module._full_paged_videos(\n        FakeSkillForge(),\n        {"search_type": 3, "search_ids": ["4016"], "page_size": 60},\n        label="videos",\n        warnings=warnings,\n    )\n\n    assert len(rows) == 61\n    assert rows[-1]["id"] == "60"\n    assert warnings == ["videos batch truncated at page 20; continue from page 21"]\n\n\ndef test_daily_truncated_warning_in_output(monkeypatch):\n    module = _load_module()\n\n    class FakeSkillForge:\n        def __init__(self, skill_id):\n            self.skill_id = skill_id\n\n        def fetch_api(self, url, *, body, **_kwargs):\n            if "skillforge_cloud_video_videos" in url:\n                return {"page": body["page"], "total": 0, "total_pages": 1, "items": [], "pagination": {"pages_read": 1}}\n            if "skillforge_cloud_video_daily_person_video_report" in url:\n                return {"daily": {"truncated": True, "video_total_rows": 1, "video_returned_rows": 0}}\n            if "skillforge_cloud_video_video_usage_report" in url:\n                return {"details": []}\n            raise AssertionError(url)\n\n    monkeypatch.setattr(module, "SkillForge", FakeSkillForge)\n    output = module.main({"date_range": {"start_date": "2026-06-01", "end_date": "2026-06-07"}})\n\n    assert output["reports"] == []\n    assert output["todos"] == []\n    assert output["_skillforge_meta"]["fetch_warnings"] == [\n        "skillforge_cloud_video_daily_person_video_report returned truncated daily rows"\n    ]\n    assert output["raw_data"]["daily_report"]["daily"]["truncated"] is True\n'
    return {
        "SKILL.md": skill_md,
        "skillforge.yaml": yaml.safe_dump(
            {
                "skill_id": skill_id,
                "template": template,
                "department": department or "示例品牌内容电商运营部",
                "analysis_agent": "samplebrand-same-topic-video-diagnosis-agent",
                "cloud_video_team_id": "4016",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        "contract.json": json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
        "prompts/analysis_v1.md": "此采集 Skill 不调用 AI 诊断。后续分析控制 Skill 可读取 raw-output artifact 后自行定义 prompt。\n",
        "scripts/main.py": main_py,
        "fixtures/sample_input.json": json.dumps(sample_input, ensure_ascii=False, indent=2) + "\n",
        "tests/test_main.py": test_py,
    }

def _skill_template_files(skill_id: str, *, name: str, description: str, department: str, trigger_type: str, cron: str, risk_level: str, template: str) -> dict[str, str]:
    if template == "cloud-video-weekly-diagnosis":
        return _cloud_video_weekly_diagnosis_template(
            skill_id,
            name=name,
            description=description,
            department=department,
            trigger_type=trigger_type,
            cron=cron,
            risk_level=risk_level,
            template=template,
        )
    trigger_lines = [
        "---",
        f"name: {name or skill_id}",
        f"description: {description or 'SkillForge Skill'}",
        f"trigger_type: {trigger_type}",
        f"risk_level: {risk_level}",
    ]
    if department:
        trigger_lines.append(f"department: {department}")
    if trigger_type == "cron" and cron:
        trigger_lines.append(f"trigger_expression: {cron}")
    trigger_lines.extend(
        [
            "runtime:",
            "  backend: bridge_script",
            "  script_entry: scripts/main.py",
            "  timeout: 300",
            "---",
            "",
            f"# {name or skill_id}",
            "",
            description or "Describe what this Skill does.",
            "",
        ]
    )
    sample_output = {
        "reports": [
            {
                "title": f"{name or skill_id} 测试报告",
                "summary": "本地样例输出。",
                "content_markdown": "# 测试报告\n\n- 这里写结论和证据。",
            }
        ],
        "todos": [
            {
                "kind": "dispatch",
                "title": "P2｜处理样例事项",
                "priority": "P2",
                "summary": "本地样例待办。",
                "payload": {"priority": "P2", "key_evidence": ["样例证据"], "suggested_action": "补充真实逻辑"},
            }
        ],
    }
    main_py = (
        "import json\n"
        "import sys\n\n"
        "def main():\n"
        "    try:\n"
        "        params = json.load(sys.stdin) if not sys.stdin.isatty() else {}\n"
        "    except Exception:\n"
        "        params = {}\n"
        f"    output = {json.dumps(sample_output, ensure_ascii=False, indent=4)}\n"
        "    output.setdefault('_skillforge_meta', {})['params_seen'] = bool(params)\n"
        "    print(json.dumps(output, ensure_ascii=False))\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    return {
        "SKILL.md": "\n".join(trigger_lines),
        "skillforge.yaml": yaml.safe_dump({"skill_id": skill_id, "template": template}, allow_unicode=True, sort_keys=False),
        "contract.json": json.dumps({"output_schema": {"type": "object"}}, ensure_ascii=False, indent=2) + "\n",
        "scripts/main.py": main_py,
        "fixtures/sample_input.json": json.dumps({"dry_run": True}, ensure_ascii=False, indent=2) + "\n",
        "tests/test_main.py": "def test_placeholder():\n    assert True\n",
    }


def skill_init(args) -> None:
    skill_id = str(args.skill_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,98}", skill_id):
        raise SystemExit("skill_id 格式非法，只允许字母、数字、点、下划线和连字符")
    base = Path(args.path).expanduser().resolve()
    target = base if base.name == skill_id else base / skill_id
    if target.exists() and any(target.iterdir()) and not args.force:
        raise SystemExit(f"目标目录非空: {target}。如需覆盖请加 --force")
    if target.exists() and args.force:
        shutil.rmtree(target)
    files = _skill_template_files(
        skill_id,
        name=args.name or skill_id,
        description=args.description or "",
        department=args.department or "",
        trigger_type=args.trigger_type,
        cron=args.cron or "",
        risk_level=args.risk_level,
        template=args.template,
    )
    for rel, content in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print_json({"ok": True, "skill_id": skill_id, "path": str(target), "files": sorted(files)})


def read_text_files(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for rel, path in iter_package_files(root):
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return files


def package_text_files(package: bytes) -> dict[str, str]:
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
        for member in tar.getmembers():
            validate_skill_package_member(member.name)
            if not member.isfile():
                continue
            fh = tar.extractfile(member)
            if not fh:
                continue
            raw = fh.read()
            try:
                files[str(PurePosixPath(member.name))] = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
    return files


def skill_diff(args) -> None:
    root = Path(args.path).resolve()
    skill_id = args.skill_id or skill_id_for(root)
    package, _resp = api_request_bytes("GET", f"/api/codex/skills/{skill_id}/package")
    remote_files = package_text_files(package)
    local_files = read_text_files(root)
    paths = sorted(set(remote_files) | set(local_files))
    lines: list[str] = []
    for rel in paths:
        old = remote_files.get(rel)
        new = local_files.get(rel)
        if old == new:
            continue
        if old is None:
            lines.append(f"新增: {rel}")
        elif new is None:
            lines.append(f"删除: {rel}")
        else:
            lines.append(f"修改: {rel}")
        if args.unified:
            lines.extend(
                difflib.unified_diff(
                    (old or "").splitlines(),
                    (new or "").splitlines(),
                    fromfile=f"remote/{rel}",
                    tofile=f"local/{rel}",
                    lineterm="",
                )
            )
    print("\n".join(lines) if lines else "本地与远端文本文件一致")


def skill_publish_status(args) -> None:
    print_json(api_request("GET", f"/api/codex/skills/{args.skill_id}/publish-status"))


def skill_sync(args) -> None:
    instance_ids = []
    for raw in getattr(args, "instance_id", []) or []:
        instance_ids.extend(item.strip() for item in str(raw).split(",") if item.strip())
    body = {
        "instance_ids": instance_ids or None,
        "department": args.department or None,
        "push_git": not bool(getattr(args, "no_push_git", False)),
    }
    print_json(api_request("POST", f"/api/codex/skills/{args.skill_id}/sync", json_body=body))


def find_skill_dirs(root: Path) -> list[Path]:
    root = root.resolve()
    found = []
    if (root / "SKILL.md").exists():
        return [root]
    for path in root.rglob("SKILL.md"):
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_NAMES or part.startswith(".") for part in rel_parts):
            continue
        found.append(path.parent)
    return sorted(set(found))


def skill_scan(args) -> None:
    roots = find_skill_dirs(Path(args.path))
    items = []
    for root in roots:
        try:
            _package, digest, manifest = build_package(root)
            sf_yaml = read_skillforge_yaml(root)
            items.append(
                {
                    "client_ref": str(root),
                    "skill_id": manifest["skill_id"],
                    "base_commit": sf_yaml.get("base_commit") or manifest.get("base_commit"),
                    "package_hash": digest,
                    "manifest": manifest,
                }
            )
        except Exception as exc:
            items.append({"client_ref": str(root), "skill_id": root.name, "status": "missing_metadata", "error": str(exc)})
    comparable = [item for item in items if "status" not in item]
    result = {"items": items}
    if args.compare_remote and comparable:
        result = api_request("POST", "/api/codex/skills/local-status", json_body={"items": comparable})
        missing = [item for item in items if "status" in item]
        result["items"].extend(missing)
    print_json(result)


def skill_status(args) -> None:
    root = Path(args.path).resolve()
    _package, digest, manifest = build_package(root)
    sf_yaml = read_skillforge_yaml(root)
    result = api_request(
        "POST",
        "/api/codex/skills/local-status",
        json_body={
            "items": [
                {
                    "client_ref": str(root),
                    "skill_id": manifest["skill_id"],
                    "base_commit": sf_yaml.get("base_commit") or manifest.get("base_commit"),
                    "package_hash": digest,
                    "manifest": manifest,
                }
            ]
        },
    )
    print_json(result)


def _pull_target_dir(skill_id: str, path_value: str) -> Path:
    target = Path(path_value or ".").resolve()
    if target.exists() and target.is_file():
        raise SystemExit(f"目标路径不是目录: {target}")
    if target.name == skill_id or (target / "SKILL.md").exists():
        return target
    return target / skill_id


def _skill_pull_manifest_from_items(file_items: list[tuple[str, bytes]]) -> dict:
    metadata = {}
    for rel, data in file_items:
        if rel == "skillforge.yaml":
            try:
                parsed = yaml.safe_load(data.decode("utf-8")) or {}
            except Exception:
                parsed = {}
            if isinstance(parsed, dict):
                metadata.update(parsed)
    return metadata


def _existing_skill_id_for_target(target_dir: Path) -> str:
    if not target_dir.exists():
        return ""
    if not ((target_dir / "SKILL.md").exists() or (target_dir / "skillforge.yaml").exists()):
        return ""
    try:
        return skill_id_for(target_dir)
    except Exception:
        return ""


def _safe_extract_skill_package(
    package: bytes,
    target_dir: Path,
    *,
    force: bool,
    expected_hash: str = "",
    expected_skill_id: str = "",
) -> tuple[list[str], str, dict]:
    target_dir = target_dir.resolve()
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
        members = [member for member in tar.getmembers() if not member.isdir()]
        file_items: list[tuple[str, bytes]] = []
        for member in members:
            if not member.isfile() or member.issym() or member.islnk() or member.isdev():
                raise SystemExit(f"Skill 包包含不支持的文件类型: {member.name}")
            rel = str(validate_tar_member(member.name))
            if not allowed_package_path(rel):
                raise SystemExit(f"Skill 包包含不允许的路径: {rel}")
            if int(member.size or 0) > 5 * 1024 * 1024:
                raise SystemExit(f"Skill 包文件过大: {rel}")
            extracted = tar.extractfile(member)
            data = extracted.read() if extracted else b""
            if len(data) != int(member.size or 0):
                raise SystemExit(f"Skill 包文件大小不一致: {rel}")
            file_items.append((rel, data))
        if not any(rel == "SKILL.md" for rel, _ in file_items):
            raise SystemExit("Skill 包缺少 SKILL.md")
        manifest = _skill_pull_manifest_from_items(file_items)
        manifest_skill_id = str(manifest.get("skill_id") or "").strip()
        if expected_skill_id and manifest_skill_id != expected_skill_id:
            raise SystemExit(
                f"Skill 包元数据不匹配: expected={expected_skill_id} actual={manifest_skill_id or '<missing>'}"
            )
        local_hash = package_hash(file_items)
        if expected_hash and local_hash != expected_hash:
            raise SystemExit(f"拉取后 package_hash 不一致: local={local_hash} remote={expected_hash}")
        for rel, _data in file_items:
            dst = (target_dir / rel).resolve()
            if not str(dst).startswith(str(target_dir) + os.sep):
                raise SystemExit(f"Skill 包路径越界: {rel}")
            if dst.exists() and not force:
                raise SystemExit(f"目标文件已存在: {dst}。如需覆盖请加 --force")
        target_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        for rel, data in file_items:
            dst = target_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            written.append(rel)
    return sorted(written), local_hash, manifest


def skill_pull(args) -> None:
    skill_id = str(args.skill_id or "").strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", skill_id):
        raise SystemExit("skill_id 格式不合法")
    package, headers = api_download(f"/api/codex/skills/{skill_id}/package")
    expected_hash = headers.get("x-skillforge-package-hash", "")
    target_dir = _pull_target_dir(skill_id, args.path)
    existing_skill_id = _existing_skill_id_for_target(target_dir)
    if existing_skill_id and existing_skill_id != skill_id:
        raise SystemExit(
            f"目标目录已有不同 Skill: {existing_skill_id}。请切到父目录拉取，或选择空目录。"
        )
    written, local_hash, manifest = _safe_extract_skill_package(
        package,
        target_dir,
        force=bool(args.force),
        expected_hash=expected_hash,
        expected_skill_id=skill_id,
    )
    print_json(
        {
            "skill_id": skill_id,
            "path": str(target_dir),
            "package_hash": local_hash,
            "remote_head": headers.get("x-skillforge-remote-head") or manifest.get("base_commit"),
            "file_count": len(written),
            "files": written,
        }
    )


def skill_doctor(args) -> None:
    root = Path(args.path).resolve()
    try:
        from app.skills.validators import structural_validate
    except Exception:
        print_json({"ok": (root / "SKILL.md").exists(), "checks": ["SKILL.md exists"], "path": str(root)})
        return
    report = structural_validate(root)
    print_json(report.to_detail() | {"ok": report.ok})
    if not report.ok:
        raise SystemExit(1)


def doctor_remote(args) -> None:
    root = Path(args.path).resolve()
    local = {}
    try:
        _package, digest, manifest = build_package(root)
        local = {"ok": True, "package_hash": digest, "manifest": manifest}
    except Exception as exc:
        local = {"ok": False, "error": str(exc)}
    remote = {}
    if local.get("manifest", {}).get("skill_id"):
        skill_id = local["manifest"]["skill_id"]
        try:
            remote["publish_status"] = api_request("GET", f"/api/codex/skills/{skill_id}/publish-status")
        except SystemExit as exc:
            remote["publish_status_error"] = str(exc)
        try:
            remote["schedule"] = api_request("GET", f"/api/codex/skills/{skill_id}/schedule")
        except SystemExit as exc:
            remote["schedule_error"] = str(exc)
    checks = {
        "auth": api_request("POST", "/api/codex/auth/introspect"),
        "mcp_catalog": api_request("GET", "/api/codex/mcp-catalog"),
    }
    print_json({"local": local, "remote": remote, "checks": checks})


def doctor_cli(args) -> None:
    if bool(getattr(args, "remote", False)):
        doctor_remote(args)
        return
    skill_doctor(args)


def create_debug_run(root: Path, run_mode: str, input_json: dict | None = None) -> dict:
    _package, digest, manifest = build_package(root)
    return api_request(
        "POST",
        "/api/codex/skill/debug-runs",
        json_body={
            "skill_id": manifest["skill_id"],
            "run_mode": run_mode,
            "package_hash": digest,
            "manifest": manifest,
            "input": input_json or {},
            "requested_tools": [],
        },
    )


def runtime_sdk_path() -> Path:
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        bundled = parent / "sdk"
        if (bundled / "skillforge_sdk.py").exists():
            return bundled
        repo_sdk = parent / "app" / "skill_runtime_sdk"
        if (repo_sdk / "skillforge_sdk.py").exists():
            return repo_sdk
    return here.parents[1] / "app" / "skill_runtime_sdk"


def run_skill_script(
    root: Path,
    run: dict,
    *,
    real_mcp: bool,
    extra_env: dict | None = None,
    input_json: dict | None = None,
) -> tuple[int, str, str]:
    script = root / "scripts" / "main.py"
    if not script.exists():
        raise SystemExit(f"未找到默认入口: {script}")
    env = os.environ.copy()
    sdk_path = str(runtime_sdk_path())
    env["PYTHONPATH"] = sdk_path + os.pathsep + env.get("PYTHONPATH", "")
    env["SKILLFORGE_PLATFORM_URL"] = base_url()
    env["SKILLFORGE_SKILL_ID"] = run["skill_id"]
    env["SKILLFORGE_RUN_ID"] = run["run_id"]
    env["SKILLFORGE_RUN_MODE"] = run["run_mode"]
    env["SKILLFORGE_SKILL_ROOT"] = str(root)
    skill_git_commit = str(run.get("skill_git_commit_full") or run.get("base_commit") or "").strip()
    if skill_git_commit:
        env["SKILLFORGE_SKILL_GIT_COMMIT_FULL"] = skill_git_commit
    if real_mcp:
        env["SKILLFORGE_RUN_TOKEN"] = run["run_token"]
        if run.get("runtime_run_token"):
            env["SKILLFORGE_INTELLIGENCE_RUN_TOKEN"] = run["runtime_run_token"]
        env["SKILLFORGE_MCP_GATEWAY_URL"] = f"{base_url()}/api/codex/mcp/call"
    for key, value in (extra_env or {}).items():
        if value is not None:
            env[str(key)] = str(value)
    stdin_data = None
    if input_json is not None:
        stdin_data = json.dumps(input_json, ensure_ascii=False)
    elif not real_mcp:
        sample_input = root / "fixtures" / "sample_input.json"
        if sample_input.exists():
            stdin_data = sample_input.read_text(encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(root),
        env=env,
        input=stdin_data,
        text=True,
        capture_output=True,
        timeout=1800,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _local_output_schema(contract: dict) -> dict | None:
    schema = contract.get("output_schema")
    if isinstance(schema, dict):
        return schema
    output = contract.get("output")
    if isinstance(output, dict) and isinstance(output.get("output_schema"), dict):
        return output["output_schema"]
    return None


def _schema_type_matches(value: Any, expected: Any) -> bool:
    expected_types = expected if isinstance(expected, list) else [expected]
    for item in expected_types:
        if item == "string" and isinstance(value, str):
            return True
        if item == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if item == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if item == "boolean" and isinstance(value, bool):
            return True
        if item == "array" and isinstance(value, list):
            return True
        if item == "object" and isinstance(value, dict):
            return True
        if item == "null" and value is None:
            return True
    return False


def validate_local_test_output(root: Path, stdout: str) -> None:
    contract_path = root / "contract.json"
    if not contract_path.exists():
        raise SystemExit("提交前门禁失败: 缺少 contract.json")
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"提交前门禁失败: contract.json 不是合法 JSON: {exc.msg}") from exc
    if not isinstance(contract, dict):
        raise SystemExit("提交前门禁失败: contract.json 顶层必须是对象")

    output_schema = _local_output_schema(contract)
    if not isinstance(output_schema, dict):
        raise SystemExit("提交前门禁失败: contract.json 缺少 output_schema")
    required = output_schema.get("required")
    if not isinstance(required, list) or not required:
        raise SystemExit("提交前门禁失败: output_schema 必须声明 required 字段")

    raw = (stdout or "").strip()
    try:
        output = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"提交前门禁失败: main.py stdout 不是合法 JSON: {exc.msg}") from exc
    if not isinstance(output, dict):
        raise SystemExit("提交前门禁失败: main.py stdout 顶层必须是对象")

    missing = [field for field in required if isinstance(field, str) and field not in output]
    if missing:
        raise SystemExit(f"提交前门禁失败: main.py return 缺字段 {missing}")

    properties = output_schema.get("properties") if isinstance(output_schema.get("properties"), dict) else {}
    type_errors: list[str] = []
    for field in required:
        if not isinstance(field, str):
            continue
        field_schema = properties.get(field)
        expected_type = field_schema.get("type") if isinstance(field_schema, dict) else None
        if expected_type and not _schema_type_matches(output.get(field), expected_type):
            type_errors.append(f"{field} 应为 {expected_type}")
    if type_errors:
        raise SystemExit("提交前门禁失败: " + "; ".join(type_errors[:8]))


def run_real_skill(root: Path, *, run_mode: str, shop_id: str | None = None) -> None:
    input_json = {"dry_run": False}
    if shop_id:
        input_json["shop_id"] = shop_id
    run = create_debug_run(root, run_mode, input_json=input_json)
    sf_yaml = read_skillforge_yaml(root)
    if sf_yaml.get("base_commit") and not run.get("skill_git_commit_full"):
        run["skill_git_commit_full"] = sf_yaml.get("base_commit")
    extra_env = {"SKILLFORGE_SHOP_ID": shop_id, "SHOP_ID": shop_id} if shop_id else {}
    code, stdout, stderr = run_skill_script(root, run, real_mcp=True, extra_env=extra_env, input_json=input_json)
    status = "completed" if code == 0 else "failed"
    resp = requests.post(
        f"{base_url()}/api/codex/skill/debug-runs/{run['run_id']}/complete",
        json={"status": status, "stdout_tail": stdout[-65536:], "stderr_tail": stderr[-65536:]},
        headers={**client_headers("application/json"), "Authorization": f"Bearer {run['run_token']}"},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"debug run complete failed {resp.status_code}: {resp.text}")
    complete = resp.json()
    print(stdout, end="")
    print(stderr, end="", file=sys.stderr)
    print_json({"run": complete, "exit_code": code})
    raise SystemExit(code)


def skill_test(args) -> None:
    root = Path(args.path).resolve()
    if not args.real_mcp:
        skill_doctor(argparse.Namespace(path=str(root)))
        code, stdout, stderr = run_skill_script(
            root,
            {"skill_id": skill_id_for(root), "run_id": "local", "run_mode": "local", "run_token": ""},
            real_mcp=False,
        )
        print(stdout, end="")
        print(stderr, end="", file=sys.stderr)
        raise SystemExit(code)
    run_real_skill(root, run_mode="local_debug", shop_id=args.shop_id)


def skill_sandbox(args) -> None:
    run_real_skill(Path(args.path).resolve(), run_mode="sandbox", shop_id=args.shop_id)


def skill_submit(args) -> None:
    root = Path(args.path).resolve()
    if bool(getattr(args, "force_submit", False)) and not str(getattr(args, "force_reason", "") or "").strip():
        raise SystemExit("--force-submit 需要同时提供 --force-reason")
    if not bool(getattr(args, "skip_local_test", False)):
        skill_doctor(argparse.Namespace(path=str(root)))
        code, stdout, stderr = run_skill_script(
            root,
            {"skill_id": skill_id_for(root), "run_id": "local", "run_mode": "local", "run_token": ""},
            real_mcp=False,
        )
        print(stdout, end="")
        print(stderr, end="", file=sys.stderr)
        if code != 0:
            raise SystemExit(code)
        validate_local_test_output(root, stdout)
    package, digest, manifest = build_package(root)
    sf_yaml = read_skillforge_yaml(root)
    files = {"package": ("skill.tar.gz", package, "application/gzip")}
    data = {
        "manifest_json": json.dumps(manifest, ensure_ascii=False),
        "package_hash": digest,
        "base_commit": sf_yaml.get("base_commit") or manifest.get("base_commit") or "",
        "message": args.message or f"Codex submit {manifest['skill_id']}",
    }
    if bool(getattr(args, "force_submit", False)):
        data["force_submit"] = "true"
        data["force_reason"] = str(getattr(args, "force_reason", "") or "").strip()
    print_json(api_request("POST", "/api/codex/skills/submissions", data=data, files=files))


def read_projectforge_yaml(root: Path) -> dict:
    for name in PROJECT_MANIFEST_NAMES:
        path = root / name
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}
    return {}


def project_id_for(root: Path) -> str:
    data = read_projectforge_yaml(root)
    raw = str(data.get("project_id") or data.get("id") or root.name).strip()
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")[:42]
    return normalized or f"proj-{hashlib.sha256(str(root).encode()).hexdigest()[:8]}"


def normalize_project_entry(value: str) -> str:
    entry = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not entry or any(part in {"", ".", ".."} or part.startswith(".") for part in entry.split("/")):
        raise SystemExit("projectforge.yaml: entry 不能包含隐藏目录、空目录或路径穿越")
    return entry


def validate_project_entry_url(value: str, *, required: bool = False) -> str:
    entry_url = str(value or "").strip()
    if not entry_url:
        if required:
            raise SystemExit("projectforge.yaml: entry_url 不能为空")
        return ""
    if any(ord(ch) < 32 for ch in entry_url) or any(ch.isspace() for ch in entry_url):
        raise SystemExit("projectforge.yaml: entry_url 不能包含空白或控制字符")
    if entry_url.startswith("/"):
        if entry_url.startswith("//"):
            raise SystemExit("projectforge.yaml: entry_url 平台相对路径不能使用 // 协议相对 URL")
        parsed_relative = urlparse(entry_url)
        if parsed_relative.scheme or parsed_relative.netloc:
            raise SystemExit("projectforge.yaml: entry_url 平台相对路径不能包含 scheme 或 host")
        parts = PurePosixPath(parsed_relative.path).parts
        if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
            raise SystemExit("projectforge.yaml: entry_url 平台相对路径不能包含隐藏目录或路径穿越")
        return entry_url
    parsed = urlparse(entry_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise SystemExit("projectforge.yaml: entry_url 只允许 http/https URL 或平台相对路径")
    if parsed.username or parsed.password:
        raise SystemExit("projectforge.yaml: entry_url 不能包含用户名或密码")
    return entry_url


def detect_project_entry(root: Path, preferred: str = "") -> str:
    candidates = []
    if preferred:
        candidates.append(normalize_project_entry(preferred))
    candidates.extend(PROJECT_ENTRY_CANDIDATES)
    seen = set()
    for entry in candidates:
        if entry in seen:
            continue
        seen.add(entry)
        if (root / entry).is_file():
            return entry
    return normalize_project_entry(preferred) if preferred else PROJECT_ENTRY_CANDIDATES[0]


def project_init(args) -> None:
    root = Path(args.path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    project_id = str(getattr(args, "project_id", "") or "").strip() or project_id_for(root)
    name = str(getattr(args, "name", "") or "").strip() or root.name
    entry_url = validate_project_entry_url(str(getattr(args, "entry_url", "") or ""))
    kind = str(getattr(args, "kind", "") or "").strip() or ("external_web" if entry_url else "web_static")
    if kind not in {"external_web", "web_static", "dashboard", "internal_tool", "playbook_ref"}:
        raise SystemExit(f"--kind 不支持 {kind}")
    entry = detect_project_entry(root, str(getattr(args, "entry", "") or ""))
    capabilities = list(getattr(args, "capability", None) or []) or ["ai.generate", "ai.analyze"]
    manifest_path = root / "projectforge.yaml"
    index_path = root / entry
    if manifest_path.exists() and not bool(getattr(args, "force", False)):
        raise SystemExit(f"projectforge.yaml 已存在，如需覆盖请加 --force: {manifest_path}")
    if not entry_url:
        index_path.parent.mkdir(parents=True, exist_ok=True)
    if not entry_url and (not index_path.exists() or bool(getattr(args, "force", False))):
        index_path.write_text(
            """<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\">
  <title>SkillForge Project</title>
  <script src=\"/project-gateway-sdk.js\"></script>
</head>
<body>
  <h1>SkillForge Project</h1>
  <label>项目输入 <input id=\"keyword\" value=\"今日运营\" /></label>
  <button id=\"record-input\">记录输入</button>
  <button id=\"send\">提交示例输出</button>
  <button id=\"ai\">调用平台 AI</button>
  <pre id=\"status\"></pre>
  <script>
    const gateway = window.PlatformProjectGateway || window.SkillForgeProject
    const statusEl = document.getElementById('status')
    function setStatus(value) {
      statusEl.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
    }
    function currentInput() {
      return {
        keyword: document.getElementById('keyword').value,
        page: 'index'
      }
    }
    async function recordCurrentInput() {
      const recorded = await gateway.input({
        input: currentInput(),
        metadata: { source: 'sf_project_template' }
      })
      setStatus({ action: 'input_recorded', recorded })
      return recorded
    }
    function aiText(result) {
      return (result && result.result && result.result.output) || result.output || result.text || result.summary || '平台 AI 已返回'
    }
    async function boot() {
      const ctx = await gateway.ready()
      gateway.startHeartbeat({ intervalMs: 25000, payload: { page: 'index' } })
      document.getElementById('record-input').onclick = recordCurrentInput
      document.getElementById('send').onclick = async () => {
        await recordCurrentInput()
        await gateway.ingest({
          output: { summary: '项目输出完成', input: currentInput(), project: ctx.project },
          reports: [{ title: '项目报告', summary: '这是来自项目网页的报告。' }],
          todos: []
        })
        setStatus('输出已进入 SkillForge Run Trace / DecisionLog / Learning Loop')
      }
      document.getElementById('ai').onclick = async () => {
        await recordCurrentInput()
        const result = await gateway.capability({
          capability: 'ai.generate',
          prompt: '请总结这个项目当前页面状态',
          input: { project: ctx.project, input: currentInput() }
        })
        setStatus({ action: 'ai_result', output: aiText(result), raw: result })
      }
    }
    boot().catch(console.error)
  </script>
</body>
</html>
""",
            encoding="utf-8",
        )
    manifest = {
        "project_id": project_id,
        "name": name,
        "kind": kind,
        "visibility": "department",
        "capabilities": capabilities,
        "outputs": {"reports": True, "todos": True, "proofs": True},
    }
    if entry_url:
        manifest["entry_url"] = entry_url
    else:
        manifest["entry"] = entry
    manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print_json({
        "ok": True,
        "path": str(root),
        "manifest": str(manifest_path),
        "entry": entry_url or str(index_path),
        "project_id": project_id,
        "kind": kind,
    })


def validate_project_static_security(root: Path) -> dict[str, Any]:
    direct_ai_endpoints: list[dict[str, Any]] = []
    for rel, path in iter_project_package_files(root):
        if PurePosixPath(rel).suffix.lower() not in PROJECT_STATIC_SECURITY_SUFFIXES:
            continue
        try:
            raw = path.read_bytes()[:PROJECT_STATIC_SECURITY_SCAN_BYTES]
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue
        text = raw.decode("utf-8", errors="ignore")
        for label, pattern in PROJECT_FRONTEND_FORBIDDEN_PATTERNS:
            if pattern.search(text):
                raise SystemExit(
                    "项目静态资源疑似包含前端 AI 密钥，"
                    f"请改用 Project Gateway/PlatformProjectGateway 调平台 AI/数据能力: {rel} ({label})"
                )
        for label, pattern in PROJECT_FRONTEND_AI_ENDPOINT_PATTERNS:
            matches = list(pattern.finditer(text))
            if matches:
                direct_ai_endpoints.append({
                    "file": rel,
                    "match": label,
                    "count": len(matches),
                    "gateway_proxy": "project-autowire",
                })
    return {
        "frontend_secret_scan": "pass",
        "direct_ai_endpoint_count": sum(int(item.get("count") or 0) for item in direct_ai_endpoints),
        "direct_ai_endpoints": direct_ai_endpoints[:50],
        "ai_endpoint_policy": "allowed_with_project_gateway_proxy",
    }


def project_unbuilt_entry_issue(root: Path, entry: str) -> tuple[str, str] | None:
    entry_path = root / normalize_project_entry(entry)
    if entry_path.suffix.lower() not in {".html", ".htm"} or not entry_path.is_file():
        return None
    try:
        raw = entry_path.read_bytes()[:PROJECT_STATIC_SECURITY_SCAN_BYTES]
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    text = raw.decode("utf-8", errors="ignore")
    for label, pattern in PROJECT_UNBUILT_FRONTEND_REF_PATTERNS:
        for match in pattern.finditer(text):
            ref = next((group for group in match.groups() if group), "")
            return ref, label
    return None


def validate_project_entry_runnable(root: Path, entry: str) -> None:
    issue = project_unbuilt_entry_issue(root, entry)
    if not issue:
        return
    ref, label = issue
    raise SystemExit(
        "项目入口疑似引用未构建前端源码，请先执行构建并上传 dist/build/out 等静态产物: "
        f"{entry} -> {ref} ({label})"
    )


def project_package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    if (root / "bun.lockb").is_file() or (root / "bun.lock").is_file():
        return "bun"
    return "npm"


def project_has_build_script(root: Path) -> bool:
    package_json = root / "package.json"
    if not package_json.is_file():
        return False
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except Exception:
        return False
    scripts = data.get("scripts") if isinstance(data, dict) else {}
    return isinstance(scripts, dict) and bool(str(scripts.get("build") or "").strip())


def run_project_build(root: Path) -> dict[str, Any]:
    if not project_has_build_script(root):
        raise SystemExit("项目入口仍是源码入口，但 package.json 未提供 scripts.build；请先构建并上传 dist/build/out")
    manager = project_package_manager(root)
    executable = shutil.which(manager)
    if not executable:
        raise SystemExit(f"未找到 {manager}，无法自动构建项目；请本地执行构建后再 sf project submit，或安装 {manager}")
    cmd = [executable, "run", "build"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("项目自动构建超过 600 秒，请本地构建后再提交") from exc
    if proc.returncode != 0:
        preview = "\n".join((proc.stdout or "").splitlines()[-20:] + (proc.stderr or "").splitlines()[-20:])
        raise SystemExit(f"项目自动构建失败，请修复后再提交。\n命令: {' '.join(cmd)}\n{preview}")
    return {"ok": True, "command": " ".join(cmd), "package_manager": manager}


def prepare_project_manifest_for_submit(root: Path, args) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = read_projectforge_yaml(root)
    if not manifest:
        raise SystemExit(f"未找到 projectforge.yaml: {root}")
    entry_url = validate_project_entry_url(str(manifest.get("entry_url") or ""))
    if entry_url:
        return manifest, {"entry_url": entry_url}
    entry = detect_project_entry(root, str(manifest.get("entry") or ""))
    issue = project_unbuilt_entry_issue(root, entry)
    if not issue:
        if not str(manifest.get("entry") or "").strip():
            manifest = {**manifest, "entry": entry}
        return manifest, {"entry": entry}
    if bool(getattr(args, "no_build", False)):
        validate_project_entry_runnable(root, entry)
    ref, label = issue
    build_result = run_project_build(root)
    built_entry = ""
    for candidate in PROJECT_ENTRY_CANDIDATES:
        if (root / candidate).is_file() and not project_unbuilt_entry_issue(root, candidate):
            built_entry = candidate
            break
    if not built_entry:
        raise SystemExit(
            "项目自动构建后仍未找到可运行入口；请确认 build 输出到 web/dist/build/out/public/root index.html。"
        )
    manifest = {**manifest, "entry": built_entry}
    return manifest, {
        "entry": built_entry,
        "auto_build": {
            **build_result,
            "original_entry": entry,
            "original_dependency": ref,
            "original_match": label,
        },
    }


def project_doctor(args) -> None:
    root = Path(args.path).resolve()
    manifest = read_projectforge_yaml(root)
    if not manifest:
        raise SystemExit(f"未找到 projectforge.yaml: {root}")
    project_id = str(manifest.get("project_id") or manifest.get("id") or "").strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,41}$", project_id):
        raise SystemExit("projectforge.yaml: project_id 只允许字母、数字、_、-，最长 42 位")
    name = str(manifest.get("name") or "").strip()
    if not name:
        raise SystemExit("projectforge.yaml: name 不能为空")
    kind = str(manifest.get("kind") or manifest.get("type") or "web_static").strip()
    if kind not in {"external_web", "web_static", "dashboard", "internal_tool", "playbook_ref"}:
        raise SystemExit(f"projectforge.yaml: kind 不支持 {kind}")
    entry_url = validate_project_entry_url(str(manifest.get("entry_url") or ""))
    entry_override = str(getattr(args, "entry_override", "") or "")
    entry = detect_project_entry(root, entry_override or str(manifest.get("entry") or ""))
    if kind == "external_web" and not entry_url:
        raise SystemExit("projectforge.yaml: external_web 必须提供 entry_url；本地静态网页请使用 kind=web_static/dashboard/internal_tool")
    if not entry_url and not (root / entry).is_file():
        raise SystemExit(f"projectforge.yaml: entry 文件不存在: {entry}")
    static_security = {
        "frontend_secret_scan": "skipped",
        "direct_ai_endpoint_count": 0,
        "direct_ai_endpoints": [],
        "ai_endpoint_policy": "not_applicable",
    }
    if not entry_url:
        static_security = validate_project_static_security(root)
        validate_project_entry_runnable(root, entry)
    capabilities = manifest.get("capabilities") or []
    if not isinstance(capabilities, list):
        raise SystemExit("projectforge.yaml: capabilities 必须是列表")
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    recipe_result = project_recipe_checks(root, manifest, str(getattr(args, "recipe", "") or ""))
    result = {
        "ok": True,
        "project_id": project_id,
        "name": name,
        "kind": kind,
        "entry": entry_url or entry,
        "capabilities": capabilities,
        "outputs": outputs,
        "containerized": False,
        "static_security": static_security,
        "ai_takeover": {
            "status": "gateway_proxy_candidate" if int(static_security.get("direct_ai_endpoint_count") or 0) else "no_direct_provider_endpoint",
            "direct_ai_endpoint_count": int(static_security.get("direct_ai_endpoint_count") or 0),
            "gateway_proxy": "project-autowire",
        },
        "recipe": recipe_result,
    }
    result["ok"] = bool(result["ok"] and recipe_result.get("ok", True))
    if not bool(getattr(args, "quiet", False)):
        print_json(result)
    return result


def project_org_resolve(args) -> None:
    payload = {
        "department": str(getattr(args, "department", "") or ""),
        "department_id": str(getattr(args, "department_id", "") or ""),
        "owner": str(getattr(args, "owner", "") or ""),
        "visibility": str(getattr(args, "visibility", "") or ""),
    }
    print_json(api_request("POST", "/api/codex/projects/org/resolve", json_body=payload))


def project_verify(args) -> None:
    input_payload: dict[str, Any] = {
        "scenario": str(getattr(args, "recipe", "") or "default") or "default",
        "source": "sf_project_verify",
    }
    if getattr(args, "simulate_video_upload_failure", ""):
        input_payload["simulate_video_upload_failure"] = args.simulate_video_upload_failure
    if getattr(args, "input_json", ""):
        input_payload.update(parse_json_object(args.input_json, field_name="--input-json"))
    payload = {
        "request_id": getattr(args, "request_id", "") or f"sf-project-verify-{int(time.time())}",
        "input": input_payload,
        "prompt": getattr(args, "prompt", "") or "请基于项目服务契约执行一次验证，并返回可用于判断项目是否正常运行的结构化结果。",
        "capability": getattr(args, "capability", "") or "",
        "auto_analyze": True,
        "analysis_prompt": "验证项目是否完成输入记录、能力调用、输出回传和 AI 分析闭环。",
    }
    data = api_request("POST", f"/api/codex/projects/{args.project_id}/service/invoke", json_body=payload)
    run_id = str(data.get("project_run_id") or data.get("run_id") or "")
    logs = None
    if run_id:
        try:
            logs = api_request("GET", f"/api/codex/projects/runs/{run_id}/logs?limit=50")
        except SystemExit as exc:
            logs = {"ok": False, "error": str(exc)}
    print_json({
        "ok": bool(data.get("ok", True)),
        "project_id": args.project_id,
        "recipe": str(getattr(args, "recipe", "") or ""),
        "invoke": data,
        "logs": logs,
        "project_run_id": run_id or None,
        "app_url": data.get("app_url"),
        "trace_url": data.get("trace_url") or ((logs or {}).get("trace_url") if isinstance(logs, dict) else None),
    })


def allowed_project_package_path(path: str) -> bool:
    return path in PROJECT_PACKAGE_EXACT or any(path.startswith(prefix) for prefix in PROJECT_PACKAGE_DIRS)


def iter_project_package_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_NAMES or part.startswith(".") for part in rel_parts):
            continue
        rel = normalize_path(path, root)
        if allowed_project_package_path(rel):
            yield rel, path


def build_project_package(root: Path, manifest_override: dict | None = None) -> tuple[bytes, str, dict]:
    root = root.resolve()
    manifest = manifest_override if isinstance(manifest_override, dict) else read_projectforge_yaml(root)
    if not manifest:
        raise SystemExit(f"未找到 projectforge.yaml: {root}")
    file_items = [(rel, path.read_bytes()) for rel, path in iter_project_package_files(root)]
    if not any(rel in PROJECT_MANIFEST_NAMES for rel, _ in file_items):
        raise SystemExit("项目包缺少 projectforge.yaml/manifest.yaml")
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            for rel, path in iter_project_package_files(root):
                tar.add(path, arcname=rel)
        data = tmp_path.read_bytes()
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    return data, digest, manifest


def project_submit(args) -> None:
    root = Path(args.path).resolve()
    manifest, prepare_info = prepare_project_manifest_for_submit(root, args)
    project_doctor(argparse.Namespace(path=str(root), quiet=True, entry_override=prepare_info.get("entry") or ""))
    entry_url = str(prepare_info.get("entry_url") or manifest.get("entry_url") or "").strip()
    if entry_url:
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
        print_json(api_request(
            "POST",
            "/api/codex/projects/auto-register",
            json_body={"manifest": manifest, "package_hash": digest, "source": "sf_project_submit"},
        ))
        return
    package, digest, manifest = build_project_package(root, manifest_override=manifest)
    files = {"package": ("project.tar.gz", package, "application/gzip")}
    data = {"manifest_json": json.dumps(manifest, ensure_ascii=False), "package_hash": digest}
    if prepare_info.get("auto_build"):
        data["sf_auto_build_json"] = json.dumps(prepare_info["auto_build"], ensure_ascii=False)
    print_json(api_request("POST", "/api/codex/projects/upload", data=data, files=files))


def project_status(args) -> None:
    print_json(api_request("GET", f"/api/codex/projects/{args.project_id}/status"))


def project_logs(args) -> None:
    query = {}
    limit = int(getattr(args, "limit", 100) or 100)
    offset = int(getattr(args, "offset", 0) or 0)
    if limit != 100:
        query["limit"] = limit
    if offset:
        query["offset"] = offset
    if getattr(args, "ingress_cursor", ""):
        query["ingress_cursor"] = args.ingress_cursor
    if getattr(args, "capability_cursor", ""):
        query["capability_cursor"] = args.capability_cursor
    suffix = f"?{urlencode(query)}" if query else ""
    print_json(api_request("GET", f"/api/codex/projects/runs/{args.project_run_id}/logs{suffix}"))


def _project_asset_metadata_from_args(args) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if getattr(args, "metadata_json", ""):
        metadata.update(parse_json_object(args.metadata_json, field_name="--metadata-json"))
    for field in ("role", "material_role", "video_role", "asset_type", "frame_label", "source_file"):
        value = str(getattr(args, field, "") or "").strip()
        if value:
            metadata[field] = value
    frame_time = str(getattr(args, "frame_time", "") or "").strip()
    if frame_time:
        try:
            metadata["frame_time"] = float(frame_time)
        except ValueError as exc:
            raise SystemExit("--frame-time 必须是数字秒数") from exc
    return metadata


def _guess_mime_type(path: Path) -> str:
    import mimetypes

    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def project_asset_upload(args) -> None:
    target = Path(args.file).expanduser()
    if not target.is_file():
        raise SystemExit(f"运行资产文件不存在: {target}")
    metadata = _project_asset_metadata_from_args(args)
    file_name = str(getattr(args, "name", "") or "").strip() or target.name
    mime_type = str(getattr(args, "mime_type", "") or "").strip() or _guess_mime_type(target)
    with target.open("rb") as fh:
        result = api_request(
            "POST",
            f"/api/codex/projects/runs/{args.project_run_id}/assets",
            data={"metadata_json": json.dumps(metadata, ensure_ascii=False)},
            files={"file": (file_name, fh, mime_type)},
        )
    print_json(result)


def project_asset_list(args) -> None:
    print_json(api_request("GET", f"/api/codex/projects/runs/{args.project_run_id}/assets"))


def _json_object_from_file(path: str, *, field_name: str) -> dict:
    target = Path(path).expanduser()
    try:
        return parse_json_object(target.read_text(encoding="utf-8"), field_name=field_name)
    except OSError as exc:
        raise SystemExit(f"{field_name} 读取失败: {exc}") from exc


def project_invoke(args) -> None:
    payload: dict[str, Any] = {}
    if getattr(args, "request_id", ""):
        payload["request_id"] = args.request_id
    if getattr(args, "input_json", ""):
        payload["input"] = parse_json_object(args.input_json, field_name="--input-json")
    if getattr(args, "input_file", ""):
        payload["input"] = _json_object_from_file(args.input_file, field_name="--input-file")
    if getattr(args, "params_json", ""):
        payload["params"] = parse_json_object(args.params_json, field_name="--params-json")
    if getattr(args, "prompt", ""):
        payload["prompt"] = args.prompt
    if getattr(args, "capability", ""):
        payload["capability"] = args.capability
    if getattr(args, "output_json", ""):
        payload["output"] = parse_json_object(args.output_json, field_name="--output-json")
    if getattr(args, "report_json", ""):
        payload["reports"] = [parse_json_object(item, field_name="--report-json") for item in args.report_json]
    if getattr(args, "todo_json", ""):
        payload["todos"] = [parse_json_object(item, field_name="--todo-json") for item in args.todo_json]
    if getattr(args, "proof_json", ""):
        payload["proofs"] = [parse_json_object(item, field_name="--proof-json") for item in args.proof_json]
    payload["auto_analyze"] = not bool(getattr(args, "no_auto_analyze", False))
    if getattr(args, "analysis_prompt", ""):
        payload["analysis_prompt"] = args.analysis_prompt
    print_json(api_request("POST", f"/api/codex/projects/{args.project_id}/service/invoke", json_body=payload))


def submission_status(args) -> None:
    print_json(api_request("GET", f"/api/codex/skills/submissions/{args.submission_id}"))


def submission_approve(args) -> None:
    print_json(
        api_request(
            "POST",
            f"/api/codex/skills/submissions/{args.submission_id}/approve",
            json_body={
                "verify_after_sync": not bool(args.no_verify),
                "static_check_override": bool(args.static_check_override),
                "static_check_override_reason": args.static_check_override_reason or "",
            },
        )
    )


def schedule_get(args) -> None:
    print_json(api_request("GET", f"/api/codex/skills/{args.skill_id}/schedule"))


def schedule_set(args) -> None:
    print_json(
        api_request(
            "POST",
            f"/api/codex/skills/{args.skill_id}/schedule",
            json_body={"action": "update" if args.update else "start", "cron_expression": args.cron},
        )
    )


def schedule_stop(args) -> None:
    print_json(api_request("POST", f"/api/codex/skills/{args.skill_id}/schedule", json_body={"action": "stop"}))


def schedule_sync(args) -> None:
    print_json(api_request("POST", f"/api/codex/skills/{args.skill_id}/schedule", json_body={"action": "sync"}))


def review_list(args) -> None:
    query = [f"page={int(args.page)}", f"page_size={int(args.page_size)}"]
    for key in ("status", "skill_id", "reviewer", "submitter"):
        value = getattr(args, key)
        if value:
            query.append(f"{key}={requests.utils.quote(str(value))}")
    print_json(api_request("GET", "/api/codex/reviews?" + "&".join(query)))


def review_get(args) -> None:
    print_json(api_request("GET", f"/api/codex/reviews/{args.review_id}"))


def review_comment(args) -> None:
    content = args.content
    if args.file:
        content = Path(args.file).expanduser().read_text(encoding="utf-8")
    print_json(
        api_request(
            "POST",
            f"/api/codex/reviews/{args.review_id}/comment",
            json_body={
                "content": content,
                "file_path": args.file_path or None,
                "line_number": args.line_number,
                "side": args.side or None,
            },
        )
    )


def review_request_changes(args) -> None:
    reason = args.reason
    if args.file:
        reason = Path(args.file).expanduser().read_text(encoding="utf-8")
    print_json(
        api_request(
            "POST",
            f"/api/codex/reviews/{args.review_id}/request-changes",
            json_body={"reason": reason, "reject_reason": args.reject_reason or "request_changes"},
        )
    )


def review_approve(args) -> None:
    print_json(api_request("POST", f"/api/codex/reviews/{args.review_id}/approve", json_body={"verify_after_sync": not args.no_verify}))


def review_reject(args) -> None:
    print_json(
        api_request(
            "POST",
            f"/api/codex/reviews/{args.review_id}/reject",
            json_body={"reason": args.reason or "", "reject_reason": args.reject_reason or ""},
        )
    )


def capabilities(_args) -> None:
    print_json(api_request("GET", "/api/codex/capabilities"))


def platform_capabilities(_args) -> None:
    print_json(api_request("GET", "/api/codex/platform/capabilities"))


def editor_capabilities(_args) -> None:
    print_json(api_request("GET", "/api/codex/editor/capabilities"))


def _compact_text(value: Any, *, limit: int = 96) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip() + "…"


CLIENT_KNOWN_MCP_BUILTINS: list[dict[str, Any]] = [
    {
        "name": "skillforge_org_search_users",
        "description": "查询当前账号可见范围内的组织成员，可按姓名、账号、部门、组织单元过滤。",
        "meta": {
            "platform": "skillforge",
            "data_scope": "org.users",
            "endpoint_family": "org_user_search",
            "requires_shop_id": False,
            "write": False,
            "client_known_builtin": True,
        },
    },
    {
        "name": "skillforge_org_list_members",
        "description": "列出部门或组织单元内当前账号可见的成员。",
        "meta": {
            "platform": "skillforge",
            "data_scope": "org.users",
            "endpoint_family": "org_members",
            "requires_shop_id": False,
            "write": False,
            "client_known_builtin": True,
        },
    },
    {
        "name": "skillforge_dingtalk_send_work_notice",
        "description": "向组织成员发送钉钉工作通知。真实推送需要 dry_run=false 且提供 idempotency_key。",
        "meta": {
            "platform": "skillforge",
            "data_scope": "dingtalk.work_notice",
            "endpoint_family": "dingtalk_work_notice",
            "requires_shop_id": False,
            "write": True,
            "client_known_builtin": True,
        },
    },
    {
        "name": "skillforge_agent_coverage",
        "description": "查询当前账号可见部门的执行/分析/训练 Agent 覆盖度、在线状态和平台兜底状态。",
        "meta": {
            "platform": "skillforge",
            "data_scope": "platform.agent_coverage",
            "endpoint_family": "agent_department_coverage",
            "requires_shop_id": False,
            "write": False,
            "client_known_builtin": True,
        },
    },
    {
        "name": "skillforge_ai_analyze",
        "description": "通过平台后台 AI 配置调用 DeepSeek V4 Pro 1M 上下文模型做分析，不向本地暴露模型密钥。",
        "meta": {
            "platform": "skillforge",
            "data_scope": "platform.ai",
            "endpoint_family": "platform_ai_analyze",
            "requires_shop_id": False,
            "write": False,
            "client_known_builtin": True,
        },
    },
    {
        "name": "skillforge_raw_data_query",
        "description": "按权限查询 Skill 运行后的原始记录，包括 execution_runs、steps、decision_log、collection proof 和 schema snapshot。",
        "meta": {
            "platform": "skillforge",
            "data_scope": "platform.raw_data",
            "endpoint_family": "skill_run_raw_data",
            "requires_shop_id": False,
            "write": False,
            "client_known_builtin": True,
        },
    },
    {
        "name": "skillforge_run_analyze",
        "description": "一键读取某次 Skill 运行后的脱敏原始数据，并调用平台 DeepSeek V4 Pro 1M 上下文模型生成运行复盘。",
        "meta": {
            "platform": "skillforge",
            "data_scope": "platform.run_analysis",
            "endpoint_family": "skill_run_analysis",
            "requires_shop_id": False,
            "write": False,
            "client_known_builtin": True,
        },
    },
    {
        "name": "skillforge_sf_data_write",
        "description": "向 SkillForge 通用数据存储写入 JSON、文本、表格或大型 artifact 引用。",
        "meta": {
            "platform": "skillforge",
            "data_scope": "sf.data_store",
            "endpoint_family": "sf_data_store",
            "requires_shop_id": False,
            "write": True,
            "client_known_builtin": True,
        },
    },
    {
        "name": "skillforge_sf_data_list",
        "description": "分页查询已写入的通用 SF 数据记录。",
        "meta": {
            "platform": "skillforge",
            "data_scope": "sf.data_store",
            "endpoint_family": "sf_data_store",
            "requires_shop_id": False,
            "write": False,
            "client_known_builtin": True,
        },
    },
    {
        "name": "skillforge_sf_data_get",
        "description": "读取单条通用 SF 数据记录详情。",
        "meta": {
            "platform": "skillforge",
            "data_scope": "sf.data_store",
            "endpoint_family": "sf_data_store",
            "requires_shop_id": False,
            "write": False,
            "client_known_builtin": True,
        },
    },
]


def _iter_mcp_tools(catalog: dict) -> list[dict]:
    tools: list[dict] = []
    servers = catalog.get("servers") if isinstance(catalog, dict) else []
    for server in servers if isinstance(servers, list) else []:
        if not isinstance(server, dict):
            continue
        server_name = str(server.get("name") or "skillforge")
        for tool in server.get("tools") or []:
            if isinstance(tool, dict) and tool.get("name"):
                tools.append({**tool, "_server": server_name})
    return tools


def _missing_client_known_mcp_builtins(catalog: dict) -> list[dict]:
    platform_names = {str(tool.get("name") or "") for tool in _iter_mcp_tools(catalog)}
    return [
        tool
        for tool in CLIENT_KNOWN_MCP_BUILTINS
        if str(tool.get("name") or "") not in platform_names
    ]


def _mcp_tool_flags(tool: dict) -> str:
    meta = tool.get("meta") if isinstance(tool.get("meta"), dict) else {}
    flags = []
    if meta.get("write"):
        flags.append("write")
    if meta.get("requires_shop_id"):
        flags.append("shop")
    platform = str(meta.get("platform") or "").strip()
    if platform:
        flags.append(platform)
    return f" [{' '.join(flags)}]" if flags else ""


def format_mcp_catalog_summary(catalog: dict) -> str:
    tools = _iter_mcp_tools(catalog)
    server_names = sorted({str(tool.get("_server") or "skillforge") for tool in tools}) or ["skillforge"]
    read_tools = [tool for tool in tools if not (isinstance(tool.get("meta"), dict) and tool["meta"].get("write"))]
    write_tools = [tool for tool in tools if isinstance(tool.get("meta"), dict) and tool["meta"].get("write")]
    lines = [
        "SkillForge MCP 可用工具",
        f"服务: {', '.join(server_names)} | 工具: {len(tools)} | 只读: {len(read_tools)} | 写入: {len(write_tools)}",
    ]

    def append_group(title: str, items: list[dict]) -> None:
        if not items:
            return
        lines.append("")
        lines.append(title)
        for tool in sorted(items, key=lambda item: str(item.get("name") or "")):
            name = str(tool.get("name") or "")
            desc = _compact_text(tool.get("description"), limit=88)
            lines.append(f"  - {name}{_mcp_tool_flags(tool)}")
            if desc:
                lines.append(f"    {desc}")

    append_group("只读工具", read_tools)
    append_group("写入工具（默认 dry-run；真实写入需 --real 和 --idempotency-key）", write_tools)
    missing_builtins = _missing_client_known_mcp_builtins(catalog)
    if missing_builtins:
        lines.append("")
        lines.append("本地 CLI 已知但当前平台 catalog 未返回")
        lines.append("  这通常表示平台 catalog 元数据未刷新，或当前服务与本地 sf CLI 版本不一致。")
        for tool in sorted(missing_builtins, key=lambda item: str(item.get("name") or "")):
            name = str(tool.get("name") or "")
            desc = _compact_text(tool.get("description"), limit=88)
            lines.append(f"  - {name}{_mcp_tool_flags(tool)}")
            if desc:
                lines.append(f"    {desc}")
        lines.append("  对应 sf 命令仍会通过 /api/codex/mcp/call 调用；如后端未部署，会返回明确错误。")
    lines.extend([
        "",
        "常用命令",
        "  sf mcp call <tool> --args '{\"key\":\"value\"}'",
        "  sf agent coverage",
        "  sf ai analyze --prompt '分析这次 Skill 运行结果' --context-json '{...}'",
        "  sf raw query --source decision_logs --skill-id <skill_id> --limit 20",
        "  sf run analyze --run-id <execution_run_id> --include-raw",
        "  sf run candidate --run-id <execution_run_id> --analysis-prompt-hash <hash>",
        "  sf training resources",
        "  sf training datasets",
        "  sf training readiness <skill_id>",
        "  sf training candidate <skill_id>",
        "  sf training jobs --status running",
        "  sf training create --title '训练新模型' --target-skill-id <skill_id>",
        "  sf training dispatch <training_job_id>",
        "  sf training bootstrap-env <training_gateway_id>",
        "  sf training collect-due --gateway <training_gateway_id>",
        "  sf training deploy-request <training_job_id> --target-skill-id <skill_id>",
        "  sf training deployment-reject <deployment_id> --reason '说明原因'",
        "  sf mcp call <write_tool> --real --idempotency-key <stable-key> --args '{...}'",
        "  sf mcp catalog --json  # 输出原始 JSON",
    ])
    return "\n".join(lines)


def mcp_catalog(args) -> None:
    catalog = api_request("GET", "/api/codex/mcp-catalog")
    if bool(getattr(args, "json", False)):
        print_json(catalog)
    else:
        print(format_mcp_catalog_summary(catalog))


def _coverage_status_label(value: str) -> str:
    return {
        "ready": "齐备",
        "fallback": "平台兜底",
        "missing": "缺口",
    }.get(str(value or ""), str(value or "-"))


def _coverage_capability_text(row: dict, key: str) -> str:
    payload = ((row.get("capabilities") or {}).get(key) or {})
    if payload.get("ready"):
        return f"{int(payload.get('online') or 0)}/{int(payload.get('count') or 0)}"
    if payload.get("fallback_ready"):
        return f"兜底 {int(payload.get('fallback_count') or 0)}"
    return "缺"


def _filtered_agent_coverage(data: dict, args) -> dict:
    items = list(data.get("items") or [])
    department = str(getattr(args, "department", "") or "").strip().lower()
    status = str(getattr(args, "status", "") or "").strip().lower()
    if department:
        items = [
            item
            for item in items
            if department in str(item.get("department") or "").lower()
        ]
    if status:
        items = [item for item in items if str(item.get("status") or "") == status]
    return {
        **data,
        "items": items,
        "total": len(items),
        "summary": {
            "ready": sum(1 for item in items if item.get("status") == "ready"),
            "fallback": sum(1 for item in items if item.get("status") == "fallback"),
            "missing": sum(1 for item in items if item.get("status") == "missing"),
        },
    }


def format_agent_coverage_summary(data: dict) -> str:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    lines = [
        "SkillForge Agent 覆盖度",
        (
            f"部门: {int(data.get('total') or 0)} | "
            f"齐备: {int(summary.get('ready') or 0)} | "
            f"平台兜底: {int(summary.get('fallback') or 0)} | "
            f"缺口: {int(summary.get('missing') or 0)}"
        ),
    ]
    for row in data.get("items") or []:
        if not isinstance(row, dict):
            continue
        department = str(row.get("department") or "-")
        lines.append(
            "  - "
            f"{department} [{_coverage_status_label(str(row.get('status') or ''))}] "
            f"执行 {_coverage_capability_text(row, 'skill_runtime')} | "
            f"分析 {_coverage_capability_text(row, 'analysis')} | "
            f"训练 {_coverage_capability_text(row, 'training')}"
        )
        missing = [str(item) for item in (row.get("missing") or []) if str(item).strip()]
        fallback = [str(item) for item in (row.get("fallback") or []) if str(item).strip()]
        if missing:
            lines.append(f"    缺口: {', '.join(missing)}")
        if fallback:
            lines.append(f"    兜底: {', '.join(fallback)}")
    return "\n".join(lines)


def agent_coverage(args) -> None:
    arguments = {
        "department": str(getattr(args, "department", "") or ""),
        "status": str(getattr(args, "status", "") or ""),
        "include_agents": not bool(getattr(args, "summary_only", False)),
    }
    data = api_request(
        "POST",
        "/api/codex/mcp/call",
        json_body={
            "server": "skillforge",
            "tool": "skillforge_agent_coverage",
            "arguments": {key: value for key, value in arguments.items() if value not in ("", None)},
            "run_mode": "sf_agent_coverage",
            "dry_run": True,
        },
    )
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    filtered = _filtered_agent_coverage(data, args)
    if bool(getattr(args, "json", False)):
        print_json(filtered)
    else:
        print(format_agent_coverage_summary(filtered))


def agent_analysis_list(args) -> None:
    query = ""
    if str(getattr(args, "department", "") or "").strip():
        query = "?" + urlencode({"department": str(args.department).strip()})
    data = api_request("GET", f"/api/codex/agents/analysis{query}")
    if bool(getattr(args, "json", False)):
        print_json(data)
        return
    lines = ["SkillForge 业务分析 Agent"]
    for item in data.get("items") or []:
        control = item.get("control") if isinstance(item.get("control"), dict) else {}
        effective = control.get("effective") if isinstance(control.get("effective"), dict) else {}
        params = effective.get("default_params") if isinstance(effective.get("default_params"), dict) else {}
        if not params and isinstance(item.get("default_params"), dict):
            params = item["default_params"]
        output_sections = params.get("output_sections") if isinstance(params.get("output_sections"), list) else []
        section_text = ",".join(str(value) for value in output_sections[:6]) or "-"
        todo_text = "on" if params.get("todo_enabled", True) is not False else "off"
        lines.append(
            f"  - {item.get('name')} [{item.get('department')}] "
            f"skill={item.get('skill_id') or '-'} prompt={item.get('prompt_version') or '-'}"
        )
        lines.append(
            "    "
            f"preset={params.get('preset') or '-'} "
            f"window_days={params.get('window_days') or '-'} "
            f"top_n={params.get('top_n') or '-'} "
            f"sections={section_text} "
            f"todos={todo_text}"
        )
        if item.get("description"):
            lines.append(f"    {item.get('description')}")
    if len(lines) == 1:
        lines.append("  暂无业务分析 Agent")
    print("\n".join(lines))


def _csv_values(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def agent_analysis_create(args) -> None:
    dimensions = list(getattr(args, "dimension", []) or [])
    if getattr(args, "dimensions", ""):
        dimensions.extend(_csv_values(args.dimensions))
    default_params: dict[str, Any] = {}
    if getattr(args, "default_params", ""):
        default_params = parse_json_object(args.default_params, field_name="--default-params")
    payload = {
        "id": args.agent_id or None,
        "name": args.name,
        "department": args.department,
        "department_id": args.department_id or None,
        "owner_user_id": args.owner_user_id or None,
        "owner_query": args.owner or None,
        "skill_id": args.skill_id or None,
        "prompt_version": args.prompt_version,
        "description": args.description or None,
        "dimensions": dimensions or [
            "首屏钩子",
            "商品露出",
            "节奏密度",
            "卖点证据",
            "行动引导",
        ],
        "default_params": default_params or {
            "window": "rolling_7_complete_days",
            "timezone": "Asia/Shanghai",
            "same_topic_rule": "category_labels_title",
            "top_n": 5,
            "low_spend_rule": "zero_spend_first",
        },
        "editor_user_ids": getattr(args, "editor_user_id", []) or [],
        "editor_queries": getattr(args, "editor", []) or [],
        "status": args.status,
    }
    print_json(api_request("POST", "/api/codex/agents/analysis", json_body=payload))


def agent_analysis_update(args) -> None:
    args.agent_id = args.agent_id or args.id
    return agent_analysis_create(args)


def agent_analysis_validate(args) -> None:
    params: dict[str, Any] = {}
    if getattr(args, "params", ""):
        params = parse_json_object(args.params, field_name="--params")
    data = api_request(
        "POST",
        f"/api/codex/agents/analysis/{requests.utils.quote(args.id, safe='')}/validate",
        json_body={"params": params},
    )
    if bool(getattr(args, "json", False)):
        print_json(data)
        return
    checks = data.get("checks") if isinstance(data.get("checks"), list) else []
    lines = [f"业务分析 Agent 验证: {'通过' if data.get('ok') else '未通过'}"]
    for item in checks:
        if not isinstance(item, dict):
            continue
        detail = f" · {item.get('detail')}" if item.get("detail") else ""
        lines.append(
            f"  - {item.get('label') or item.get('key')}: "
            f"{item.get('status') or '-'}"
            f"{detail}"
        )
    payload = data.get("sample_payload") if isinstance(data.get("sample_payload"), dict) else {}
    sample_params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if payload.get("skill_id"):
        lines.append(f"  sample skill: {payload.get('skill_id')}")
    if sample_params.get("_analysis_agent_id"):
        lines.append(f"  sample agent: {sample_params.get('_analysis_agent_id')}")
    print("\n".join(lines))


def _training_query(params: dict[str, Any]) -> str:
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    return f"?{urlencode(clean)}" if clean else ""


def _training_status_label(value: str) -> str:
    return {
        "awaiting_review": "待审批",
        "queued": "已排队",
        "running": "训练中",
        "evaluating": "评估中",
        "completed": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
        "unknown": "未知",
        "candidate": "候选",
        "canary": "灰度",
        "active": "生效",
        "rejected": "已驳回",
        "rolled_back": "已回滚",
    }.get(str(value or ""), str(value or "-"))


def _training_scope_label(value: str) -> str:
    return {
        "department": "部门",
        "platform_fallback": "平台兜底",
        "external": "外部",
    }.get(str(value or ""), str(value or "-"))


def _training_duration_text(seconds: Any) -> str:
    total = int(float(seconds or 0))
    if total <= 0:
        return "-"
    hours = total // 3600
    minutes = (total % 3600) // 60
    if hours:
        return f"{hours}h{minutes}m"
    return f"{max(minutes, 1)}m"


def _training_parameter_text(params: dict | None) -> str:
    if not isinstance(params, dict) or not params:
        return "-"
    parts = []
    for key in ("epochs", "learning_rate", "lr", "batch_size", "rank", "lora_rank", "max_steps"):
        value = params.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return ", ".join(parts) if parts else "-"


def format_training_resources_summary(data: dict) -> str:
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    lines = [
        "SkillForge 训练资源",
        (
            f"部门: {context.get('department') or '-'} | "
            f"训练网关: {int(stats.get('training_gateways') or 0)} | "
            f"GPU: {int(stats.get('gpu_count') or 0)} | "
            f"空闲: {int(stats.get('idle_gpu_count') or 0)} | "
            f"运行中任务: {int(stats.get('active_training_jobs') or 0)}"
        ),
    ]
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        training = item.get("training") if isinstance(item.get("training"), dict) else {}
        tasks = ", ".join(str(task) for task in (training.get("supported_tasks") or [])) or "-"
        lines.append(
            "  - "
            f"{item.get('id') or '-'} {item.get('name') or ''} "
            f"[{_training_scope_label(str(item.get('route_scope') or ''))}] "
            f"{'在线' if item.get('online') else '离线'} | "
            f"部门 {item.get('department') or '-'} | "
            f"GPU {int(item.get('idle_gpu_count') or 0)}/{int(item.get('gpu_count') or 0)} | "
            f"显存 {item.get('vram_free_gb') or 0}/{item.get('vram_total_gb') or 0}GB | "
            f"任务 {tasks}"
        )
        active_jobs = item.get("active_training_jobs") if isinstance(item.get("active_training_jobs"), list) else []
        for job in active_jobs[:3]:
            if isinstance(job, dict):
                lines.append(
                    "    运行中: "
                    f"{job.get('id') or '-'} "
                    f"{_compact_text(job.get('title'), limit=48)} "
                    f"[{_training_status_label(str(job.get('status') or ''))}]"
                )
    return "\n".join(lines)


def training_resources(args) -> None:
    data = api_request("GET", "/api/training/resources")
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_resources_summary(data))


def format_training_bootstrap_summary(data: dict) -> str:
    gateway = data.get("gateway") if isinstance(data.get("gateway"), dict) else {}
    bootstrap = data.get("bootstrap") if isinstance(data.get("bootstrap"), dict) else {}
    lines = [
        "SkillForge 训练环境",
        (
            f"网关: {gateway.get('id') or '-'} {gateway.get('name') or ''} | "
            f"用途: {gateway.get('agent_purpose') or '-'} | "
            f"在线: {'是' if gateway.get('online') else '否'}"
        ),
        (
            f"状态: {bootstrap.get('status') or '-'} | "
            f"profile: {bootstrap.get('profile') or '-'} | "
            f"cuda: {bootstrap.get('cuda') or '-'}"
        ),
    ]
    if bootstrap.get("python"):
        lines.append(f"Python: {bootstrap.get('python')}")
    if bootstrap.get("error"):
        lines.append(f"错误: {bootstrap.get('error')}")
    if data.get("agent_purpose_updated"):
        lines.append(f"节点用途已更新为: {data.get('agent_purpose_updated')}")
    events = bootstrap.get("events") if isinstance(bootstrap.get("events"), list) else []
    for item in events[-5:]:
        if isinstance(item, dict):
            lines.append(
                "  - "
                f"{item.get('step') or '-'} "
                f"exit={item.get('returncode') if item.get('returncode') is not None else '-'}"
            )
    return "\n".join(lines)


def format_training_model_summary(data: dict) -> str:
    gateway = data.get("gateway") if isinstance(data.get("gateway"), dict) else {}
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    lines = [
        "SkillForge 训练模型",
        (
            f"网关: {gateway.get('id') or '-'} {gateway.get('name') or ''} | "
            f"用途: {gateway.get('agent_purpose') or '-'} | "
            f"在线: {'是' if gateway.get('online') else '否'}"
        ),
        (
            f"状态: {model.get('status') or '-'} | "
            f"profile: {model.get('profile') or '-'} | "
            f"model: {model.get('model_id') or '-'} | "
            f"来源: {model.get('source') or '-'} | "
            f"验证: {model.get('validate_mode') or '-'}"
        ),
    ]
    if model.get("model_dir"):
        lines.append(f"模型目录: {model.get('model_dir')}")
    if model.get("size_gb") is not None:
        lines.append(f"大小: {model.get('size_gb')}GB | 文件: {model.get('file_count') or 0}")
    config = model.get("config") if isinstance(model.get("config"), dict) else {}
    if config:
        lines.append(
            f"Config: {config.get('model_type') or '-'} | "
            f"layers={config.get('num_hidden_layers') or '-'} | "
            f"hidden={config.get('hidden_size') or '-'}"
        )
    if isinstance(model.get("load_4bit"), dict):
        load = model["load_4bit"]
        lines.append(f"4bit: {'ok' if load.get('ok') else '-'} | device={load.get('device') or '-'}")
    if model.get("error"):
        lines.append(f"错误: {model.get('error')}")
    events = model.get("events") if isinstance(model.get("events"), list) else []
    for item in events[-3:]:
        if isinstance(item, dict):
            lines.append(
                "  - "
                f"{item.get('step') or '-'} "
                f"exit={item.get('returncode') if item.get('returncode') is not None else '-'}"
            )
    return "\n".join(lines)


def training_bootstrap_status(args) -> None:
    query = _training_query({
        "profile": getattr(args, "profile", ""),
        "cuda": getattr(args, "cuda", ""),
    })
    data = api_request("GET", f"/api/training/resources/{args.gateway_id}/bootstrap-env{query}")
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_bootstrap_summary(data if isinstance(data, dict) else {}))


def _media_size_text(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        size = 0
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.2f} {units[index]}"


def _media_eta_text(value: Any) -> str:
    try:
        seconds = max(0, int(value))
    except (TypeError, ValueError):
        return "-"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分"
    if minutes:
        return f"{minutes}分{seconds}秒"
    return f"{seconds}秒"


def format_media_bootstrap_summary(data: dict) -> str:
    speed = data.get("speed_bytes_per_second")
    speed_text = _media_size_text(speed) + "/s" if speed else "-"
    lines = [
        f"状态: {data.get('status') or 'not_started'} | 进度: {data.get('progress_percent') or 0}% | "
        f"profile: {data.get('profile') or '-'}",
        f"下载: {_media_size_text(data.get('downloaded_bytes'))} / {_media_size_text(data.get('total_bytes'))} | "
        f"速度: {speed_text} | ETA: {_media_eta_text(data.get('eta_seconds'))}",
        f"阶段: {data.get('current_step') or '-'} | 文件: {data.get('current_file') or '-'}",
    ]
    if data.get("error"):
        lines.append(f"错误: {data.get('error')}")
    return "\n".join(lines)


def media_bootstrap_status(args) -> None:
    data = api_request("GET", f"/api/aiclaw/instances/{args.instance_id}/media/bootstrap")
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_media_bootstrap_summary(data if isinstance(data, dict) else {}))


def media_bootstrap_start(args) -> None:
    if not bool(getattr(args, "accept_license", False)) and not bool(getattr(args, "dry_run", False)):
        raise RuntimeError("正式安装必须显式传 --accept-license；许可链接见 MiniMax H3 官方模型页面")
    payload = {
        "profile": "h3_all_modes_v1",
        "bandwidth_limit_mbps": 3,
        "accept_license": bool(getattr(args, "accept_license", False)),
        "force": bool(getattr(args, "force", False)),
        "dry_run": bool(getattr(args, "dry_run", False)),
    }
    data = api_request("POST", f"/api/aiclaw/instances/{args.instance_id}/media/bootstrap", json_body=payload)
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_media_bootstrap_summary(data if isinstance(data, dict) else {}))


def media_bootstrap_cancel(args) -> None:
    data = api_request("POST", f"/api/aiclaw/instances/{args.instance_id}/media/bootstrap/cancel", json_body={})
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_media_bootstrap_summary(data if isinstance(data, dict) else {}))


def media_bridge_refresh(args) -> None:
    data = api_request("POST", f"/api/aiclaw/instances/{args.instance_id}/force-update", json_body={})
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(f"Bridge 更新指令已下发: {data.get('instance_id') or args.instance_id} ({data.get('status') or 'sent'})")


def training_bootstrap_env(args) -> None:
    payload = {
        "profile": args.profile,
        "cuda": args.cuda,
        "force": bool(getattr(args, "force", False)),
        "dry_run": bool(getattr(args, "dry_run", False)),
        "timeout_seconds": int(getattr(args, "timeout_seconds", 3600) or 3600),
    }
    agent_purpose = str(getattr(args, "agent_purpose", "") or "").strip()
    if agent_purpose:
        payload["agent_purpose"] = agent_purpose
    data = api_request("POST", f"/api/training/resources/{args.gateway_id}/bootstrap-env", json_body=payload)
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_bootstrap_summary(data if isinstance(data, dict) else {}))


def training_model_status(args) -> None:
    query = _training_query({
        "profile": getattr(args, "profile", ""),
        "revision": getattr(args, "revision", ""),
        "modelscope_revision": getattr(args, "modelscope_revision", ""),
        "bootstrap_profile": getattr(args, "bootstrap_profile", ""),
        "source": getattr(args, "source", ""),
    })
    data = api_request("GET", f"/api/training/resources/{args.gateway_id}/model{query}")
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_model_summary(data if isinstance(data, dict) else {}))


def training_prepare_model(args) -> None:
    payload = {
        "profile": args.profile,
        "revision": args.revision,
        "modelscope_revision": args.modelscope_revision,
        "bootstrap_profile": args.bootstrap_profile,
        "force": bool(getattr(args, "force", False)),
        "dry_run": bool(getattr(args, "dry_run", False)),
        "timeout_seconds": int(getattr(args, "timeout_seconds", 7200) or 7200),
        "validate_mode": args.validate_mode,
        "source": args.source,
    }
    data = api_request("POST", f"/api/training/resources/{args.gateway_id}/model", json_body=payload)
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_model_summary(data if isinstance(data, dict) else {}))


def _training_dataset_counts_text(counts: dict | None) -> str:
    if not isinstance(counts, dict):
        return "样本 0"
    return (
        f"SFT {int(counts.get('sft_samples') or 0)} | "
        f"偏好 {int(counts.get('preference_samples') or 0)} | "
        f"动作 {int(counts.get('action_outcome_samples') or 0)} | "
        f"评估 {int(counts.get('eval_samples') or 0)}"
    )


def _training_dataset_gap_text(item: dict) -> str:
    checks = item.get("checks") if isinstance(item.get("checks"), list) else []
    failed = [
        str(check.get("label") or check.get("name") or check.get("key") or check.get("metric") or "check")
        for check in checks
        if isinstance(check, dict) and not bool(check.get("passed"))
    ]
    return ", ".join(failed[:4]) if failed else "-"


def format_training_datasets_summary(data: dict) -> str:
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    lines = [
        "SkillForge 训练数据资产",
        (
            f"总数: {int(data.get('total') or 0)} | "
            f"达标: {int(stats.get('ready') or 0)} | "
            f"有样本: {int(stats.get('with_samples') or 0)} | "
            f"可生成候选: {int(stats.get('can_create_candidate') or 0)} | "
            f"SFT {int(stats.get('sft_samples') or 0)} | "
            f"动作 {int(stats.get('action_outcome_samples') or 0)}"
        ),
    ]
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        status = "已达标" if item.get("passed") else "待补齐"
        candidate = "可候选" if item.get("can_create_candidate") else "不可候选"
        lines.append(
            "  - "
            f"{item.get('skill_id') or '-'} [{status}/{candidate}] "
            f"{_compact_text(item.get('skill_name') or item.get('name'), limit=42)} | "
            f"{item.get('department') or '-'} | "
            f"{_training_dataset_counts_text(item.get('sample_counts'))} | "
            f"缺口 {_training_dataset_gap_text(item)}"
        )
    return "\n".join(lines)


def training_datasets(args) -> None:
    data = api_request("GET", "/api/training/datasets")
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_datasets_summary(data))


def format_training_candidate_readiness(data: dict) -> str:
    lines = [
        f"训练候选数据 {data.get('skill_id') or '-'}",
        (
            f"状态: {'已达标' if data.get('passed') else '待补齐'} | "
            f"数据: {data.get('dataset_ref') or '-'} | "
            f"{_training_dataset_counts_text(data.get('sample_counts'))}"
        ),
    ]
    checks = data.get("checks") if isinstance(data.get("checks"), list) else []
    if checks:
        lines.append("门禁:")
        for check in checks:
            if isinstance(check, dict):
                lines.append(
                    "  - "
                    f"{check.get('label') or check.get('name') or check.get('key') or check.get('metric') or 'check'}: "
                    f"{'通过' if check.get('passed') else '未通过'} "
                    f"actual={check.get('actual', '-')} required={check.get('required', check.get('threshold', '-'))}"
                )
    return "\n".join(lines)


def training_readiness(args) -> None:
    data = api_request("GET", f"/api/training/skills/{args.skill_id}/candidate-readiness")
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_candidate_readiness(data if isinstance(data, dict) else {}))


def _training_job_plan(job: dict) -> dict:
    plan = job.get("training_plan") if isinstance(job.get("training_plan"), dict) else {}
    return plan


def format_training_jobs_summary(data: dict) -> str:
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    lines = [
        "SkillForge 训练任务",
        (
            f"总数: {int(data.get('total') or 0)} | "
            f"待审: {int(stats.get('awaiting_review') or 0)} | "
            f"排队: {int(stats.get('queued') or 0)} | "
            f"运行: {int(stats.get('running') or 0)} | "
            f"完成: {int(stats.get('completed') or 0)} | "
            f"失败: {int(stats.get('failed') or 0)}"
        ),
    ]
    for job in data.get("items") or []:
        if not isinstance(job, dict):
            continue
        plan = _training_job_plan(job)
        runtime = plan.get("runtime") if isinstance(plan.get("runtime"), dict) else {}
        routing = job.get("gateway_routing") if isinstance(job.get("gateway_routing"), dict) else {}
        lines.append(
            "  - "
            f"{job.get('id') or '-'} [{_training_status_label(str(job.get('status') or ''))}] "
            f"{_compact_text(job.get('title'), limit=56)} | "
            f"{job.get('department') or '-'} | "
            f"{job.get('job_type') or '-'} | "
            f"模型 {plan.get('model_name') or job.get('target_skill_id') or '-'} | "
            f"网关 {job.get('target_gateway_id') or '-'}"
        )
        if runtime or routing:
            lines.append(
                "    "
                f"进度 {round(float(runtime.get('progress') or 0) * 100)}% | "
                f"预计 {runtime.get('eta_text') or _training_duration_text(runtime.get('estimated_duration_seconds'))} | "
                f"路由 {_training_scope_label(str(routing.get('scope') or ''))}"
            )
    return "\n".join(lines)


def training_jobs(args) -> None:
    query = _training_query({
        "status": getattr(args, "status", ""),
        "target_gateway_id": getattr(args, "gateway", "") or getattr(args, "target_gateway_id", ""),
    })
    data = api_request("GET", f"/api/training/jobs{query}")
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_jobs_summary(data))


def format_training_job_detail(job: dict) -> str:
    plan = _training_job_plan(job)
    runtime = plan.get("runtime") if isinstance(plan.get("runtime"), dict) else {}
    routing = job.get("gateway_routing") if isinstance(job.get("gateway_routing"), dict) else {}
    lines = [
        f"训练任务 {job.get('id') or '-'}",
        f"状态: {_training_status_label(str(job.get('status') or ''))} | 部门: {job.get('department') or '-'} | 类型: {job.get('job_type') or '-'}",
        f"标题: {job.get('title') or '-'}",
        f"模型: {plan.get('model_name') or '-'} | 基座: {plan.get('base_model') or '-'}",
        f"数据: {job.get('dataset_ref') or '-'}",
        f"参数: {_training_parameter_text(plan.get('parameters') if isinstance(plan.get('parameters'), dict) else {})}",
        f"网关: {job.get('target_gateway_id') or '-'} | 路由: {_training_scope_label(str(routing.get('scope') or ''))}",
        f"进度: {round(float(runtime.get('progress') or 0) * 100)}% | 已用: {_training_duration_text(runtime.get('elapsed_seconds'))} | 预计: {runtime.get('eta_text') or _training_duration_text(runtime.get('estimated_duration_seconds'))}",
    ]
    tasks = job.get("tasks") if isinstance(job.get("tasks"), list) else []
    if tasks:
        lines.append("任务过程:")
        for task in tasks[-8:]:
            if isinstance(task, dict):
                lines.append(
                    "  - "
                    f"#{task.get('id')} {_training_status_label(str(task.get('status') or ''))} "
                    f"progress={round(float(task.get('progress') or 0) * 100)}% "
                    f"worker={task.get('worker_id') or '-'}"
                )
    deployments = job.get("deployments") if isinstance(job.get("deployments"), list) else []
    if deployments:
        lines.append("部署:")
        for deployment in deployments[:5]:
            if isinstance(deployment, dict):
                lines.append(
                    "  - "
                    f"{deployment.get('id') or '-'} "
                    f"{deployment.get('model_family') or '-'} "
                    f"[{_training_status_label(str(deployment.get('status') or ''))}] "
                    f"rollout={deployment.get('rollout_percent') or 0}%"
                )
    return "\n".join(lines)


def training_job_detail(args) -> None:
    data = api_request("GET", f"/api/training/jobs/{args.job_id}")
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_job_detail(data if isinstance(data, dict) else {}))


def training_job_logs(args) -> None:
    data = api_request("GET", f"/api/training/jobs/{args.job_id}/logs")
    if bool(getattr(args, "json", False)):
        print_json(data)
        return
    lines = data.get("lines") if isinstance(data.get("lines"), list) else []
    print(f"训练日志 {args.job_id} | available={bool(data.get('available'))} | status={data.get('status') or '-'}")
    if data.get("error"):
        print(f"错误: {data.get('error')}")
    for item in lines[-int(getattr(args, "tail", 100) or 100):]:
        if isinstance(item, dict):
            print(f"{item.get('ts') or ''} {item.get('type') or 'log'} {item.get('message') or ''}".strip())


def training_job_artifacts(args) -> None:
    data = api_request("GET", f"/api/training/jobs/{args.job_id}/artifacts")
    if bool(getattr(args, "json", False)):
        print_json(data)
        return
    print(f"训练产物 {args.job_id} | total={int(data.get('total') or 0)} | download_proxy={bool(data.get('download_proxy_available'))}")
    for item in data.get("items") or []:
        if isinstance(item, dict):
            print(
                "  - "
                f"{item.get('id') or '-'} {item.get('name') or '-'} "
                f"type={item.get('type') or '-'} sha256={item.get('sha256') or '-'} "
                f"downloadable={bool(item.get('downloadable'))}"
            )


def training_action(args) -> None:
    action = getattr(args, "training_action", "")
    path_map = {
        "approve": f"/api/training/jobs/{args.job_id}/approve",
        "dispatch": f"/api/training/jobs/{args.job_id}/dispatch",
        "retry": f"/api/training/jobs/{args.job_id}/retry",
        "collect": f"/api/training/jobs/{args.job_id}/collect-result",
        "evaluate": f"/api/training/jobs/{args.job_id}/evaluate",
        "cancel": f"/api/training/jobs/{args.job_id}/cancel",
    }
    path = path_map.get(action)
    if not path:
        raise SystemExit(f"未知训练动作: {action}")
    data = api_request("POST", path)
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_job_detail(data if isinstance(data, dict) else {}))


def format_training_collect_due_summary(data: dict) -> str:
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    lines = [
        "SkillForge 训练结果批量同步",
        (
            f"扫描: {int(data.get('total') or 0)} | "
            f"已同步: {int(stats.get('collected') or 0)} | "
            f"跳过: {int(stats.get('skipped') or 0)} | "
            f"失败: {int(stats.get('failed') or 0)} | "
            f"网关: {data.get('target_gateway_id') or '-'} | "
            f"过期: {int(data.get('stale_seconds') or 0)}s"
        ),
    ]
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            "  - "
            f"{item.get('job_id') or '-'} [{item.get('status') or '-'}] "
            f"result={item.get('result_status') or '-'} "
            f"reason={item.get('reason') or '-'}"
        )
    return "\n".join(lines)


def training_collect_due(args) -> None:
    payload = {
        "stale_seconds": args.stale_seconds,
        "max_jobs": args.max_jobs,
    }
    gateway = getattr(args, "gateway", "") or getattr(args, "target_gateway_id", "")
    if gateway:
        payload["target_gateway_id"] = gateway
    data = api_request("POST", "/api/training/jobs/collect-due", json_body=payload)
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_collect_due_summary(data if isinstance(data, dict) else {}))


def _apply_training_spec_options(args, spec: dict[str, Any]) -> dict[str, Any]:
    next_spec = dict(spec or {})
    for key in ("model_name", "base_model", "estimated_gpu_hours"):
        value = getattr(args, key, None)
        if value not in (None, ""):
            next_spec[key] = value
    params = next_spec.get("parameters") if isinstance(next_spec.get("parameters"), dict) else {}
    for attr, key in (
        ("epochs", "epochs"),
        ("learning_rate", "learning_rate"),
        ("batch_size", "batch_size"),
        ("lora_rank", "lora_rank"),
        ("max_steps", "max_steps"),
    ):
        value = getattr(args, attr, None)
        if value not in (None, ""):
            params[key] = value
    if getattr(args, "param", None):
        params = apply_arg_overrides(dict(params), args.param)
    if params:
        next_spec["parameters"] = params
    if getattr(args, "eval_gate", ""):
        next_spec["eval_gate"] = parse_json_object(args.eval_gate, field_name="--eval-gate")
    if getattr(args, "gpu_estimate", ""):
        next_spec["gpu_estimate"] = parse_json_object(args.gpu_estimate, field_name="--gpu-estimate")
    return next_spec


def training_create(args) -> None:
    spec = parse_json_object(args.spec, field_name="--spec") if args.spec else {}
    spec = _apply_training_spec_options(args, spec)
    payload: dict[str, Any] = {
        "title": args.title,
        "job_type": args.job_type,
    }
    for key in ("department", "training_strategy", "target_skill_id", "target_gateway_id", "dataset_ref", "risk_level"):
        value = getattr(args, key, "")
        if value not in (None, ""):
            payload[key] = value
    if args.objective:
        payload["objective"] = _stdin_or_value(args.objective)
    if spec:
        payload["spec"] = spec
    data = api_request("POST", "/api/training/jobs", json_body=payload)
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_job_detail(data if isinstance(data, dict) else {}))


def training_skill_candidate(args) -> None:
    payload: dict[str, Any] = {}
    optional_fields = {
        "title": args.title,
        "job_type": args.job_type,
        "training_strategy": args.training_strategy,
        "target_gateway_id": args.target_gateway_id,
        "dataset_ref": args.dataset_ref,
        "objective": args.objective,
        "risk_level": args.risk_level,
        "model_family": args.model_family,
    }
    for key, value in optional_fields.items():
        if value not in (None, ""):
            payload[key] = _stdin_or_value(value) if key == "objective" else value
    if args.eval_gate:
        payload["eval_gate"] = parse_json_object(args.eval_gate, field_name="--eval-gate")
    data = api_request(
        "POST",
        f"/api/training/skills/{args.skill_id}/candidate",
        json_body=payload,
    )
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_job_detail(data if isinstance(data, dict) else {}))


def training_force_cancel(args) -> None:
    data = api_request(
        "POST",
        f"/api/training/jobs/{args.job_id}/force-cancel",
        json_body={"reason": _stdin_or_value(args.reason)},
    )
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_job_detail(data if isinstance(data, dict) else {}))


def _deployment_artifact_sha(deployment: dict) -> str:
    artifact_ref = deployment.get("artifact_ref") if isinstance(deployment.get("artifact_ref"), dict) else {}
    return str(artifact_ref.get("sha256") or "")[:12]


def format_training_deployments_summary(data: dict) -> str:
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    lines = [
        "SkillForge 模型部署",
        (
            f"总数: {int(data.get('total') or 0)} | "
            f"待审: {int(stats.get('awaiting_review') or 0)} | "
            f"灰度: {int(stats.get('canary') or 0)} | "
            f"生效: {int(stats.get('active') or 0)} | "
            f"驳回: {int(stats.get('rejected') or 0)} | "
            f"回滚: {int(stats.get('rolled_back') or 0)} | "
            f"失败: {int(stats.get('failed') or 0)}"
        ),
    ]
    for deployment in data.get("items") or []:
        if not isinstance(deployment, dict):
            continue
        job = deployment.get("job") if isinstance(deployment.get("job"), dict) else {}
        targets = ", ".join(str(item) for item in (deployment.get("target_skill_ids") or [])) or "-"
        sha = _deployment_artifact_sha(deployment)
        lines.append(
            "  - "
            f"{deployment.get('id') or '-'} [{_training_status_label(str(deployment.get('status') or ''))}] "
            f"{deployment.get('model_family') or '-'} | "
            f"部门 {deployment.get('department') or '-'} | "
            f"job {deployment.get('job_id') or '-'} {job.get('status') or ''} | "
            f"rollout {deployment.get('rollout_percent') or 0}% | "
            f"targets {targets} | "
            f"sha {sha or '-'}"
        )
    return "\n".join(lines)


def training_deployments(args) -> None:
    query = _training_query({"status": getattr(args, "status", "")})
    data = api_request("GET", f"/api/training/deployments{query}")
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_deployments_summary(data))


def format_training_deployment_detail(data: dict) -> str:
    deployment = data.get("deployment") if isinstance(data.get("deployment"), dict) else data
    job = data.get("job") if isinstance(data.get("job"), dict) else {}
    targets = ", ".join(str(item) for item in (deployment.get("target_skill_ids") or [])) or "-"
    lines = [
        f"模型部署 {deployment.get('id') or '-'}",
        f"状态: {_training_status_label(str(deployment.get('status') or ''))} | 部门: {deployment.get('department') or '-'} | 灰度: {deployment.get('rollout_percent') or 0}%",
        f"模型: {deployment.get('model_family') or '-'} | Artifact: {deployment.get('artifact_id') or '-'} | sha: {_deployment_artifact_sha(deployment) or '-'}",
        f"目标 Skill: {targets}",
        f"训练任务: {deployment.get('job_id') or '-'} {job.get('title') or ''}",
    ]
    if job:
        plan = _training_job_plan(job)
        runtime = plan.get("runtime") if isinstance(plan.get("runtime"), dict) else {}
        lines.append(
            "训练状态: "
            f"{_training_status_label(str(job.get('status') or ''))} | "
            f"进度 {round(float(runtime.get('progress') or 0) * 100)}% | "
            f"网关 {job.get('target_gateway_id') or '-'}"
        )
    return "\n".join(lines)


def training_deployment_detail(args) -> None:
    data = api_request("GET", f"/api/training/deployments/{args.deployment_id}")
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_deployment_detail(data if isinstance(data, dict) else {}))


def _target_skill_ids(args) -> list[str]:
    values: list[str] = []
    for item in getattr(args, "target_skill_id", []) or []:
        values.extend(str(item or "").split(","))
    if getattr(args, "target_skill_ids", ""):
        values.extend(str(args.target_skill_ids or "").split(","))
    return [item.strip() for item in values if item.strip()]


def training_deploy_request(args) -> None:
    payload: dict[str, Any] = {}
    target_ids = _target_skill_ids(args)
    if target_ids:
        payload["target_skill_ids"] = target_ids
    if args.model_family:
        payload["model_family"] = args.model_family
    if args.rollout_percent is not None:
        payload["rollout_percent"] = args.rollout_percent
    if args.reason:
        payload["reason"] = _stdin_or_value(args.reason)
    data = api_request("POST", f"/api/training/jobs/{args.job_id}/deploy-request", json_body=payload)
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_deployment_detail(data if isinstance(data, dict) else {}))


def training_deployment_action(args) -> None:
    action = getattr(args, "deployment_action", "")
    if action not in {"approve", "activate", "reject", "rollback"}:
        raise SystemExit(f"未知部署动作: {action}")
    payload = {"reason": _stdin_or_value(args.reason)} if action in {"reject", "rollback"} else None
    data = api_request(
        "POST",
        f"/api/training/deployments/{args.deployment_id}/{action}",
        json_body=payload,
    )
    if bool(getattr(args, "json", False)):
        print_json(data)
    else:
        print(format_training_deployment_detail(data if isinstance(data, dict) else {}))


def _filename_from_disposition(value: str) -> str:
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', str(value or ""), flags=re.I)
    if not match:
        return ""
    return Path(match.group(1)).name


def training_artifact_download(args) -> None:
    content, headers = api_download(
        f"/api/training/jobs/{args.job_id}/artifacts/{args.artifact_id}/download",
    )
    filename = args.output or _filename_from_disposition(headers.get("content-disposition", ""))
    if not filename:
        filename = f"{args.job_id}_{args.artifact_id.replace(':', '_')}.bin"
    target = Path(filename).expanduser()
    target.write_bytes(content)
    result = {
        "ok": True,
        "path": str(target),
        "size_bytes": len(content),
        "sha256": headers.get("x-skillforge-artifact-sha256", ""),
        "content_type": headers.get("content-type", ""),
    }
    if bool(getattr(args, "json", False)):
        print_json(result)
    else:
        print(f"已下载训练产物: {result['path']} ({result['size_bytes']} bytes, sha256={result['sha256'] or '-'})")


def parse_json_object(value: str, *, field_name: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{field_name} 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{field_name} 必须是 JSON object")
    return parsed


def parse_scalar(value: str) -> Any:
    text = str(value)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def apply_arg_overrides(base: dict, overrides: list[str]) -> dict:
    data = dict(base)
    for item in overrides or []:
        if "=" not in item:
            raise SystemExit(f"--arg 需要 key=value: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit("--arg key 不能为空")
        data[key] = parse_scalar(value)
    return data


def mcp_call(args) -> None:
    arguments = apply_arg_overrides(parse_json_object(args.args, field_name="--args"), args.arg)
    dry_run = False if bool(args.real) else True
    print_json(
        api_request(
            "POST",
            "/api/codex/mcp/call",
            json_body={
                "server": "skillforge",
                "tool": args.tool,
                "arguments": arguments,
                "skill_id": args.skill_id or None,
                "run_id": args.run_id or None,
                "run_mode": args.run_mode,
                "dry_run": dry_run,
                "idempotency_key": args.idempotency_key or arguments.get("idempotency_key") or arguments.get("idempotencyKey"),
            },
        )
    )


def _stdin_or_value(value: str) -> str:
    if value == "-":
        return sys.stdin.read()
    return value


def ai_analyze(args) -> None:
    context_pack = parse_json_object(args.context_json, field_name="--context-json") if args.context_json else {}
    prompt = _stdin_or_value(args.prompt or "")
    arguments: dict[str, Any] = {
        "prompt": prompt,
        "json_mode": bool(args.json_mode),
        "max_output_tokens": args.max_output_tokens,
    }
    if args.system:
        arguments["system"] = _stdin_or_value(args.system)
    if args.context:
        arguments["context"] = _stdin_or_value(args.context)
    if context_pack:
        arguments["context_pack"] = context_pack
    if args.skill_id:
        arguments["skill_id"] = args.skill_id
    if args.temperature is not None:
        arguments["temperature"] = args.temperature
    print_json(
        api_request(
            "POST",
            "/api/codex/mcp/call",
            json_body={
                "server": "skillforge",
                "tool": "skillforge_ai_analyze",
                "arguments": arguments,
                "skill_id": args.skill_id or None,
                "run_mode": "sf_ai_analyze",
                "dry_run": True,
            },
        )
    )


def data_list(args) -> None:
    arguments = {}
    if args.platform:
        arguments["platform"] = args.platform
    if args.include_unavailable:
        arguments["include_unavailable"] = True
    print_json(
        api_request(
            "POST",
            "/api/codex/mcp/call",
            json_body={
                "server": "skillforge",
                "tool": "skillforge_data_capability_list",
                "arguments": arguments,
                "run_mode": "data_cli",
                "dry_run": True,
            },
        )
    )

def raw_query(args) -> None:
    arguments: dict[str, Any] = {
        "source": args.source,
        "limit": args.limit,
        "include_payload": bool(args.include_payload),
    }
    optional_fields = {
        "skill_id": args.skill_id,
        "run_id": args.run_id,
        "proof_id": args.proof_id,
        "platform": args.platform,
        "shop_id": args.shop_id,
        "data_scope": args.data_scope,
        "status": args.status,
    }
    arguments.update({key: value for key, value in optional_fields.items() if value})
    if args.include_endpoint:
        arguments["include_endpoint"] = True
    print_json(
        api_request(
            "POST",
            "/api/codex/mcp/call",
            json_body={
                "server": "skillforge",
                "tool": "skillforge_raw_data_query",
                "arguments": arguments,
                "skill_id": args.skill_id or None,
                "run_mode": "sf_raw_query",
                "dry_run": True,
            },
        )
    )


def data_latest(args) -> None:
    capability = DATA_ALIASES.get(args.capability, args.capability)
    print_json(
        api_request(
            "POST",
            "/api/codex/mcp/call",
            json_body={
                "server": "skillforge",
                "tool": "skillforge_data_capability_latest",
                "arguments": {"capability": capability, "limit": args.limit},
                "run_mode": "data_cli",
                "dry_run": True,
            },
        )
    )

def run_analyze(args) -> None:
    arguments: dict[str, Any] = {
        "run_id": args.run_id,
        "include_raw": bool(args.include_raw),
        "json_mode": bool(args.json_mode),
        "max_output_tokens": args.max_output_tokens,
        "step_limit": args.step_limit,
        "decision_limit": args.decision_limit,
        "proof_limit": args.proof_limit,
        "snapshot_limit": args.snapshot_limit,
    }
    if args.skill_id:
        arguments["skill_id"] = args.skill_id
    if args.prompt:
        arguments["prompt"] = _stdin_or_value(args.prompt)
    if args.temperature is not None:
        arguments["temperature"] = args.temperature
    print_json(
        api_request(
            "POST",
            "/api/codex/mcp/call",
            json_body={
                "server": "skillforge",
                "tool": "skillforge_run_analyze",
                "arguments": arguments,
                "skill_id": args.skill_id or None,
                "run_mode": "sf_run_analyze",
                "dry_run": True,
            },
        )
    )


DATA_ALIASES = {
    "samplebrand": "cloud_video.samplebrand_weekly.raw_collection",
    "samplebrand-cloud-video": "cloud_video.samplebrand_weekly.raw_collection",
    "samplebrand_cloud_video": "cloud_video.samplebrand_weekly.raw_collection",
    "cloud-video-samplebrand": "cloud_video.samplebrand_weekly.raw_collection",
    "cloud_video_samplebrand": "cloud_video.samplebrand_weekly.raw_collection",
    "tmall": "tmall.link_decline.raw_collection",
    "tmall-link-decline": "tmall.link_decline.raw_collection",
    "yuyi": "yuyidata.customer_service.raw_collection",
    "yuyidata": "yuyidata.customer_service.raw_collection",
}


def data_get(args) -> None:
    arguments: dict[str, Any] = {
        "include_content": bool(args.include_content),
        "max_bytes": args.max_bytes,
    }
    if args.artifact_id:
        arguments["artifact_id"] = int(args.artifact_id)
    if args.run_id:
        arguments["run_id"] = args.run_id
    if args.capability:
        arguments["capability"] = DATA_ALIASES.get(args.capability, args.capability)
    if args.kind:
        arguments["kind"] = args.kind
    result = api_request(
        "POST",
        "/api/codex/mcp/call",
        json_body={
            "server": "skillforge",
            "tool": "skillforge_data_artifact_get",
            "arguments": arguments,
            "run_mode": "data_cli",
            "dry_run": True,
        },
    )
    if args.output:
        out = Path(args.output).expanduser()
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print_json({"ok": True, "path": str(out.resolve())})
        return
    print_json(result)


def data_records(args) -> None:
    arguments = {
        "page": args.page,
        "page_size": args.page_size,
    }
    for key in ("namespace", "content_type", "skill_id", "run_id", "source", "q"):
        value = getattr(args, key, "")
        if value:
            arguments[key] = value
    print_json(
        api_request(
            "POST",
            "/api/codex/mcp/call",
            json_body={
                "server": "skillforge",
                "tool": "skillforge_sf_data_list",
                "arguments": arguments,
                "run_mode": "data_cli",
                "dry_run": True,
            },
        )
    )


def data_record(args) -> None:
    print_json(
        api_request(
            "POST",
            "/api/codex/mcp/call",
            json_body={
                "server": "skillforge",
                "tool": "skillforge_sf_data_get",
                "arguments": {"id": args.record_id},
                "run_mode": "data_cli",
                "dry_run": True,
            },
        )
    )


def data_write(args) -> None:
    payload: dict[str, Any] = {
        "namespace": args.namespace,
        "title": args.title or None,
        "visibility": args.visibility,
    }
    if args.json:
        payload["data"] = parse_json_object(_stdin_or_value(args.json), field_name="--json")
        payload["content_type"] = "json"
    elif args.text:
        payload["text"] = _stdin_or_value(args.text)
        payload["content_type"] = "text"
    elif args.rows_json:
        rows = json.loads(_stdin_or_value(args.rows_json))
        if not isinstance(rows, list):
            raise SystemExit("--rows-json 必须是 JSON array")
        payload["rows"] = rows
        payload["content_type"] = "table"
    elif args.file:
        raw = Path(args.file).expanduser().read_bytes()
        payload["base64"] = base64.b64encode(raw).decode("ascii")
        payload["filename"] = Path(args.file).name
        payload["content_type"] = args.content_type or "binary"
    else:
        raise SystemExit("需要提供 --json、--text、--rows-json 或 --file")
    if args.content_type:
        payload["content_type"] = args.content_type
    if args.metadata_json:
        payload["metadata"] = parse_json_object(_stdin_or_value(args.metadata_json), field_name="--metadata-json")
    if args.skill_id:
        payload["skill_id"] = args.skill_id
    if args.run_id:
        payload["run_id"] = args.run_id
    if args.source:
        payload["source"] = args.source
    if args.source_ref:
        payload["source_ref"] = args.source_ref
    print_json(
        api_request(
            "POST",
            "/api/codex/mcp/call",
            json_body={
                "server": "skillforge",
                "tool": "skillforge_sf_data_write",
                "arguments": payload,
                "skill_id": args.skill_id or None,
                "run_id": args.run_id or None,
                "run_mode": "data_cli",
                "dry_run": False if args.real else True,
                "idempotency_key": args.idempotency_key or None,
            },
        )
    )


def notify_users(args) -> None:
    print_json(
        api_request(
            "POST",
            "/api/codex/mcp/call",
            json_body={
                "server": "skillforge",
                "tool": "skillforge_org_search_users",
                "arguments": {
                    "query": args.query or None,
                    "department": args.department or None,
                    "limit": args.limit,
                },
                "run_mode": "notify_cli",
                "dry_run": True,
            },
        )
    )

def run_candidate(args) -> None:
    payload: dict[str, Any] = {}
    optional_fields = {
        "title": args.title,
        "job_type": args.job_type,
        "training_strategy": args.training_strategy,
        "target_gateway_id": args.target_gateway_id,
        "dataset_ref": args.dataset_ref,
        "objective": args.objective,
        "risk_level": args.risk_level,
        "model_family": args.model_family,
        "analysis_model": args.analysis_model,
        "analysis_prompt_hash": args.analysis_prompt_hash,
        "base_model": args.base_model,
        "target_metric": args.target_metric,
        "risk_notes": args.risk_notes,
    }
    for key, value in optional_fields.items():
        if value not in (None, ""):
            payload[key] = _stdin_or_value(value) if key in {"objective", "risk_notes"} else value
    if args.analysis_summary:
        payload["analysis_summary"] = _stdin_or_value(args.analysis_summary)
    if args.raw_counts:
        payload["raw_counts"] = parse_json_object(args.raw_counts, field_name="--raw-counts")
    if args.eval_gate:
        payload["eval_gate"] = parse_json_object(args.eval_gate, field_name="--eval-gate")
    if args.gpu_estimate:
        payload["gpu_estimate"] = parse_json_object(args.gpu_estimate, field_name="--gpu-estimate")
    if args.estimated_gpu_hours is not None:
        payload["estimated_gpu_hours"] = args.estimated_gpu_hours
    if args.forbidden_action:
        payload["forbidden_actions"] = args.forbidden_action
    if args.allow_sandbox:
        payload["allow_sandbox"] = True
    print_json(
        api_request(
            "POST",
            f"/api/training/runs/{args.run_id}/candidate",
            json_body=payload,
        )
    )


def notify_send(args) -> None:
    if args.real and not args.idempotency_key:
        raise SystemExit("真实推送需要 --idempotency-key")
    markdown = args.markdown or args.content or ""
    if args.markdown_file:
        markdown = Path(args.markdown_file).expanduser().read_text(encoding="utf-8")
    buttons = []
    if args.button_title and args.button_url:
        buttons.append({"title": args.button_title, "url": args.button_url})
    arguments = {
        "title": args.title,
        "markdown": markdown,
        "user_ids": args.user_id or [],
        "dingtalk_user_ids": args.dingtalk_user_id or [],
        "query": args.query or None,
        "department": args.department or None,
        "buttons": buttons,
        "limit": args.limit,
    }
    print_json(
        api_request(
            "POST",
            "/api/codex/mcp/call",
            json_body={
                "server": "skillforge",
                "tool": "skillforge_dingtalk_send_work_notice",
                "arguments": arguments,
                "run_mode": "notify_cli",
                "dry_run": False if args.real else True,
                "idempotency_key": args.idempotency_key or None,
            },
        )
    )


def jsonrpc_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def jsonrpc_error(msg_id, code: int, message: str):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def mcp_stdio(_args) -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        msg_id = None
        try:
            msg = json.loads(raw)
            method = msg.get("method")
            msg_id = msg.get("id")
            if method == "initialize":
                response = jsonrpc_result(
                    msg_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "skillforge", "version": "1"},
                    },
                )
            elif method in {"notifications/initialized", "$/cancelRequest"}:
                continue
            elif method == "tools/list":
                catalog = api_request("GET", "/api/codex/mcp-catalog", retry_login=False)
                tools = (catalog.get("servers") or [{}])[0].get("tools") or []
                response = jsonrpc_result(msg_id, {"tools": tools})
            elif method == "tools/call":
                params = msg.get("params") or {}
                tool_args = params.get("arguments") or {}
                dry_run = params.get("dry_run")
                if dry_run is None:
                    dry_run = tool_args.get("dryRun") if isinstance(tool_args, dict) else None
                idempotency_key = params.get("idempotency_key")
                if idempotency_key is None and isinstance(tool_args, dict):
                    idempotency_key = tool_args.get("idempotency_key") or tool_args.get("idempotencyKey")
                result = api_request(
                    "POST",
                    "/api/codex/mcp/call",
                    json_body={
                        "server": "skillforge",
                        "tool": params.get("name"),
                        "arguments": tool_args,
                        "run_mode": "mcp_stdio",
                        "dry_run": True if dry_run is None else bool(dry_run),
                        "idempotency_key": idempotency_key,
                    },
                    retry_login=False,
                )
                response = jsonrpc_result(
                    msg_id,
                    {"content": [{"type": "text", "text": json.dumps(result.get("data"), ensure_ascii=False)}]},
                )
            else:
                response = jsonrpc_error(msg_id, -32601, f"method not found: {method}")
        except SystemExit as exc:
            response = jsonrpc_error(msg_id, -32001, str(exc))
        except Exception as exc:  # noqa: BLE001
            response = jsonrpc_error(msg_id, -32603, str(exc))
        print(json.dumps(response, ensure_ascii=False), flush=True)


def catalog_refresh(_args) -> None:
    manifest = api_request("GET", "/api/codex/catalog/manifest", auth=False)
    update_catalog_cache(manifest, persist_base_url=bool(getattr(_args, "persist_base_url", False)))
    print_json(manifest)


def sf_help(_args) -> None:
    print(SF_HELP_TEXT)


def output_validate(args) -> None:
    content, content_type, _source = read_preview_content(args.file, "json")
    if content_type != "json":
        raise SystemExit("output validate 只接受 JSON 文件")
    print_json(api_request("POST", "/api/codex/output/validate", json_body={"output": content, "max_bytes": args.max_bytes}))


def market_list(args) -> None:
    query = []
    if args.category:
        query.append(f"category={requests.utils.quote(args.category)}")
    if args.certified_only:
        query.append("certified_only=true")
    path = "/api/codex/market" + (("?" + "&".join(query)) if query else "")
    print_json(api_request("GET", path))


def market_detail(args) -> None:
    print_json(api_request("GET", f"/api/codex/market/{args.skill_id}"))


def market_install(args) -> None:
    skill_pull(argparse.Namespace(skill_id=args.skill_id, path=args.path, force=args.force))


def market_metrics(args) -> None:
    print_json(api_request("GET", f"/api/codex/market/{args.skill_id}/metrics"))


def market_certify(args) -> None:
    print_json(api_request("POST", f"/api/codex/market/{args.skill_id}/certify", json_body={}))


def market_visibility(args) -> None:
    print_json(api_request("PUT", f"/api/codex/market/{args.skill_id}/visibility", json_body={"market_status": args.market_status}))


def market_ratings(args) -> None:
    print_json(api_request("GET", f"/api/codex/market/{args.skill_id}/ratings"))


def market_rate(args) -> None:
    print_json(
        api_request(
            "POST",
            f"/api/codex/market/{args.skill_id}/ratings",
            json_body={"rating": args.rating, "comment": args.comment or ""},
        )
    )


def pipeline_run(args) -> None:
    root = Path(args.path).resolve()
    steps: list[dict[str, Any]] = []
    try:
        skill_doctor(argparse.Namespace(path=str(root)))
        steps.append({"step": "doctor", "ok": True})
    except SystemExit as exc:
        steps.append({"step": "doctor", "ok": False, "error": str(exc)})
        if not args.keep_going:
            print_json({"ok": False, "steps": steps})
            raise
    if args.remote:
        try:
            doctor_remote(argparse.Namespace(path=str(root)))
            steps.append({"step": "doctor_remote", "ok": True})
        except SystemExit as exc:
            steps.append({"step": "doctor_remote", "ok": False, "error": str(exc)})
            if not args.keep_going:
                print_json({"ok": False, "steps": steps})
                raise
    if args.submit:
        try:
            package, digest, manifest = build_package(root)
            sf_yaml = read_skillforge_yaml(root)
            resp = api_request(
                "POST",
                "/api/codex/skills/submissions",
                data={
                    "manifest_json": json.dumps(manifest, ensure_ascii=False),
                    "package_hash": digest,
                    "base_commit": sf_yaml.get("base_commit") or manifest.get("base_commit") or "",
                    "message": args.message or f"Pipeline submit {manifest['skill_id']}",
                },
                files={"package": ("skill.tar.gz", package, "application/gzip")},
            )
            steps.append({"step": "submit", "ok": True, "result": resp})
        except SystemExit as exc:
            steps.append({"step": "submit", "ok": False, "error": str(exc)})
            if not args.keep_going:
                print_json({"ok": False, "steps": steps})
                raise
    print_json({"ok": all(step.get("ok") for step in steps), "steps": steps})


def apply_chinese_alias(argv: list[str]) -> list[str]:
    if argv and argv[0] in {"拉取", "同步到本地"}:
        if len(argv) < 2:
            return ["skill", "pull"]
        return ["skill", "pull", argv[1], "--path", ".", *argv[2:]]
    if argv and argv[0] in {"test-real", "真实测试"}:
        return ["skill", "test", "--real-mcp", "--path", ".", *argv[1:]]
    if argv and argv[0] == "sandbox":
        return ["skill", "sandbox", "--path", ".", *argv[1:]]
    if argv and argv[0] in {"review", "审核"}:
        if len(argv) < 2:
            return ["submission", "status"]
        return ["submission", "status", argv[1], *argv[2:]]
    if argv and argv[0] == "status":
        if len(argv) >= 2 and not argv[1].startswith("-"):
            return ["submission", "status", argv[1], *argv[2:]]
        return ["skill", "status", "--path", ".", *argv[1:]]
    text = " ".join(argv).strip()
    if len(argv) >= 2 and argv[0] == "preview" and argv[1] == "apply":
        return ["preview-apply", *argv[2:]]
    if len(argv) >= 2 and argv[0] == "review" and argv[1] not in {"list", "get", "comment", "request-changes", "approve", "reject"}:
        return ["submission", "status", argv[1], *argv[2:]]
    if argv and argv[0] in {"安装", "下载"} and len(argv) >= 2:
        return ["skill", "install", argv[1], *argv[2:]]
    if argv and argv[0] in {"更新本地skill", "同步skill"} and len(argv) >= 2:
        return ["skill", "pull", argv[1], *argv[2:]]
    if argv and argv[0] in {"预览", "预览输出"} and len(argv) >= 2:
        return ["preview", argv[1], *argv[2:]]
    if len(argv) >= 2 and argv[0] == "run" and argv[1] not in {"analyze", "candidate"}:
        return ["run-skill", *argv[1:]]
    mapping = {
        "auth": ["auth", "status"],
        "update": ["update"],
        "更新": ["update"],
        "升级": ["update"],
        "登录": ["auth", "login"],
        "授权": ["auth", "login"],
        "我是谁": ["auth", "status"],
        "whoami": ["auth", "status"],
        "help": ["help"],
        "帮助": ["help"],
        "plugin status": ["plugin", "status"],
        "plugin doctor": ["plugin", "doctor"],
        "插件诊断": ["plugin", "doctor"],
        "修复插件": ["plugin", "doctor", "--fix"],
        "插件状态": ["plugin", "status"],
        "我的codex插件": ["plugin", "status"],
        "我的插件": ["my"],
        "我的技能": ["skill", "list", "--scope", "mine"],
        "我的部门插件": ["skill", "list", "--scope", "department"],
        "部门插件": ["skill", "list", "--scope", "department"],
        "我的部门技能": ["skill", "list", "--scope", "department"],
        "下载安装部门插件": ["skill", "list", "--scope", "department"],
        "查看哪些技能": ["skill", "list", "--scope", "visible"],
        "skills": ["skill", "list", "--scope", "visible"],
        "查看我能编辑哪些技能": ["skill", "list", "--scope", "editable"],
        "查看我能发布哪些技能": ["skill", "list", "--scope", "publishable"],
        "哪些可发布": ["skill", "list", "--scope", "publishable"],
        "哪些未上传": ["skill", "scan", "--path", ".", "--compare-remote"],
        "未上传": ["skill", "scan", "--path", ".", "--compare-remote"],
        "local pending": ["skill", "scan", "--path", ".", "--compare-remote"],
        "当前目录有没有上传": ["skill", "status", "--path", "."],
        "权限": ["capabilities"],
        "能力": ["capabilities"],
        "mcp": ["mcp", "catalog"],
        "agent覆盖": ["agent", "coverage"],
        "Agent覆盖": ["agent", "coverage"],
        "agent coverage": ["agent", "coverage"],
        "部门agent": ["agent", "coverage"],
        "部门Agent": ["agent", "coverage"],
        "ai分析": ["ai", "analyze"],
        "AI分析": ["ai", "analyze"],
        "原始数据": ["raw", "query"],
        "运行数据": ["raw", "query"],
        "运行分析": ["run", "analyze"],
        "复盘运行": ["run", "analyze"],
        "训练资源": ["training", "resources"],
        "训练数据": ["training", "datasets"],
        "数据资产": ["training", "datasets"],
        "训练候选": ["training", "candidate"],
        "批量同步训练": ["training", "collect-due"],
        "训练批量同步": ["training", "collect-due"],
        "训练任务": ["training", "jobs"],
        "训练部署": ["training", "deployments"],
        "模型部署": ["training", "deployments"],
        "training datasets": ["training", "datasets"],
        "training jobs": ["training", "jobs"],
        "training deployments": ["training", "deployments"],
        "doctor": ["skill", "doctor", "--path", "."],
        "test": ["skill", "test", "--local", "--path", "."],
        "submit": ["skill", "submit", "--path", "."],
        "上传": ["skill", "submit", "--path", "."],
        "发布": ["skill", "submit", "--path", "."],
        "project spec": ["project", "spec", "--recipe", PROJECT_RECIPE_SHORT_VIDEO_ANALYSIS, "--json"],
        "项目标准": ["project", "spec", "--recipe", PROJECT_RECIPE_SHORT_VIDEO_ANALYSIS, "--json"],
        "短视频项目标准": ["project", "spec", "--recipe", PROJECT_RECIPE_SHORT_VIDEO_ANALYSIS, "--json"],
        "短视频分析标准": ["project", "spec", "--recipe", PROJECT_RECIPE_SHORT_VIDEO_ANALYSIS, "--json"],
        "project doctor": ["project", "doctor", "--path", "."],
        "项目检查": ["project", "doctor", "--path", "."],
        "短视频项目检查": ["project", "doctor", "--path", ".", "--recipe", PROJECT_RECIPE_SHORT_VIDEO_ANALYSIS, "--json"],
        "project 上传": ["project", "submit", "--path", "."],
        "项目 上传": ["project", "submit", "--path", "."],
        "project 调用": ["project", "invoke"],
        "项目 调用": ["project", "invoke"],
        "项目 服务调用": ["project", "invoke"],
        "预览": ["preview", "-"],
        "预览输出": ["preview", "-"],
    }
    return mapping.get(text, argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sf", description="SkillForge Codex CLI")
    parser.add_argument("--base-url", default=argparse.SUPPRESS, help="本次命令临时使用的 SkillForge 地址，不写入本地配置")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("help").set_defaults(func=sf_help)

    update = sub.add_parser("update")
    update.add_argument("--check", action="store_true", help="只检查新版本，不安装")
    update.add_argument("--force", action="store_true", help="即使版本号未变化也重新安装")
    update.add_argument("--target", default="", help="插件安装目录，默认使用当前插件根目录或 ~/plugins/skillforge-codex")
    update.add_argument("--install-sf-shim", action="store_true", help="兼容旧参数；默认会安装 ~/.local/bin/sf")
    update.add_argument("--no-install-sf-shim", action="store_true", help="不写入 ~/.local/bin/sf")
    update.add_argument("--base-url", default=argparse.SUPPRESS, help="本次更新临时使用的 SkillForge 地址，不写入本地配置")
    update.add_argument("--persist-base-url", action="store_true", help="配合 --base-url 或 SKILLFORGE_BASE_URL 使用时，明确把该地址写入本地配置")
    update.set_defaults(func=sf_update)

    plugin = sub.add_parser("plugin")
    plugin_sub = plugin.add_subparsers(dest="plugin_cmd")
    plugin_status_p = plugin_sub.add_parser("status")
    plugin_status_p.add_argument("--target", default="", help="插件安装目录，默认使用当前插件根目录或 ~/plugins/skillforge-codex")
    plugin_status_p.add_argument("--base-url", default=argparse.SUPPRESS, help="本次检查临时使用的 SkillForge 地址，不写入本地配置")
    plugin_status_p.add_argument("--persist-base-url", action="store_true", help="配合 --base-url 或 SKILLFORGE_BASE_URL 使用时，明确把该地址写入本地配置")
    plugin_status_p.set_defaults(func=plugin_status)
    plugin_doctor_p = plugin_sub.add_parser("doctor")
    plugin_doctor_p.add_argument("--target", default="", help="插件安装目录，默认使用当前插件根目录或 ~/plugins/skillforge-codex")
    plugin_doctor_p.add_argument("--fix", action="store_true", help="修复 marketplace、Codex config 和 ~/.local/bin/sf shim")
    plugin_doctor_p.add_argument("--no-install-sf-shim", action="store_true", help="配合 --fix 时不写入 ~/.local/bin/sf")
    plugin_doctor_p.add_argument("--base-url", default=argparse.SUPPRESS, help="本次诊断临时使用的 SkillForge 地址，不写入本地配置")
    plugin_doctor_p.set_defaults(func=plugin_doctor)

    my = sub.add_parser("my")
    my.add_argument("--target", default="", help="插件安装目录，默认使用当前插件根目录或 ~/plugins/skillforge-codex")
    my.add_argument("--base-url", default=argparse.SUPPRESS, help="本次查询临时使用的 SkillForge 地址，不写入本地配置")
    my.set_defaults(func=my_plugins)

    auth = sub.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="auth_cmd")
    login = auth_sub.add_parser("login")
    login.add_argument("--no-browser", action="store_true")
    login.add_argument("--loopback", action="store_true", help="使用 127.0.0.1 回调模式（仅本机浏览器适用）")
    login.set_defaults(func=auth_login)
    auth_sub.add_parser("status").set_defaults(func=auth_status)
    auth_sub.add_parser("logout").set_defaults(func=auth_logout)

    sub.add_parser("capabilities").set_defaults(func=capabilities)

    run_skill_p = sub.add_parser("run-skill")
    run_skill_p.add_argument("skill_id")
    run_skill_p.add_argument("--params", default="{}", help="JSON object 参数")
    run_skill_p.add_argument("--arg", action="append", default=[], help="追加 key=value 参数")
    run_skill_p.add_argument("--sandbox", action="store_true")
    run_skill_p.add_argument("--run-mode", default="")
    run_skill_p.add_argument("--parent-run-id", default="")
    run_skill_p.add_argument("--sample-used", action="store_true")
    run_skill_p.set_defaults(func=run_skill)

    runs = sub.add_parser("runs")
    runs_sub = runs.add_subparsers(dest="runs_cmd")
    runs_list_p = runs_sub.add_parser("list")
    runs_list_p.add_argument("--skill-id", default="")
    runs_list_p.add_argument("--status", default="")
    runs_list_p.add_argument("--page", type=int, default=1)
    runs_list_p.add_argument("--page-size", type=int, default=20)
    runs_list_p.set_defaults(func=runs_list)
    runs_status_p = runs_sub.add_parser("status")
    runs_status_p.add_argument("run_id")
    runs_status_p.set_defaults(func=runs_status)
    runs_result_p = runs_sub.add_parser("result")
    runs_result_p.add_argument("run_id")
    runs_result_p.set_defaults(func=runs_result)
    runs_steps_p = runs_sub.add_parser("steps")
    runs_steps_p.add_argument("run_id")
    runs_steps_p.set_defaults(func=runs_steps)
    runs_logs_p = runs_sub.add_parser("logs")
    runs_logs_p.add_argument("run_id")
    runs_logs_p.add_argument("--stream", default="")
    runs_logs_p.set_defaults(func=runs_logs)
    runs_artifacts_p = runs_sub.add_parser("artifacts")
    runs_artifacts_p.add_argument("run_id")
    runs_artifacts_p.add_argument("--kind", default="")
    runs_artifacts_p.add_argument("--include-content", action="store_true")
    runs_artifacts_p.add_argument("--max-bytes", type=int, default=1024 * 1024)
    runs_artifacts_p.add_argument("--output", default="")
    runs_artifacts_p.set_defaults(func=runs_artifacts)
    runs_diag_p = runs_sub.add_parser("diagnose")
    runs_diag_p.add_argument("run_id")
    runs_diag_p.set_defaults(func=runs_diagnose)

    preview = sub.add_parser("preview")
    preview.add_argument("file", help="输出结果文件；用 - 从 stdin 读取")
    preview.add_argument("--title", default="", help="预览标题，默认从 JSON 报告或文件名推断")
    preview.add_argument("--source-name", default="", help="来源名称，默认使用文件名")
    preview.add_argument("--type", dest="content_type", default="auto", choices=["auto", "json", "markdown", "html", "text"])
    preview.add_argument("--expires-in-hours", type=int, default=72, help="预览有效期，1-168 小时")
    preview.set_defaults(func=preview_output)
    preview_apply_p = sub.add_parser("preview-apply")
    preview_apply_p.add_argument("preview_id")
    preview_apply_p.add_argument("--real", action="store_true", help="真实落库；默认 dry-run")
    preview_apply_p.add_argument("--idempotency-key", default="")
    preview_apply_p.add_argument("--skill-id", default="")
    preview_apply_p.add_argument("--run-id", default="")
    preview_apply_p.add_argument("--params", default="{}")
    preview_apply_p.add_argument("--arg", action="append", default=[])
    preview_apply_p.add_argument("--run-mode", default="manual_real")
    preview_apply_p.add_argument("--triggered-by", default="")
    preview_apply_p.add_argument("--parent-run-id", default="")
    preview_apply_p.add_argument("--sample-used", action="store_true")
    preview_apply_p.set_defaults(func=preview_apply)

    platform = sub.add_parser("platform")
    platform_sub = platform.add_subparsers(dest="platform_cmd")
    platform_sub.add_parser("capabilities").set_defaults(func=platform_capabilities)

    editor = sub.add_parser("editor")
    editor_sub = editor.add_subparsers(dest="editor_cmd")
    editor_sub.add_parser("capabilities").set_defaults(func=editor_capabilities)

    mcp = sub.add_parser("mcp")
    mcp_sub = mcp.add_subparsers(dest="mcp_cmd")
    catalog_mcp = mcp_sub.add_parser("catalog")
    catalog_mcp.add_argument("--json", action="store_true", help="输出原始 MCP catalog JSON")
    catalog_mcp.add_argument("--base-url", default=argparse.SUPPRESS, help="本次查询临时使用的 SkillForge 地址，不写入本地配置")
    catalog_mcp.set_defaults(func=mcp_catalog)
    call = mcp_sub.add_parser("call")
    call.add_argument("tool", help="MCP tool name, for example skillforge_org_search_users")
    call.add_argument("--args", default="{}", help="JSON object arguments")
    call.add_argument("--arg", action="append", default=[], help="追加单个 key=value 参数，value 会按 JSON 标量解析")
    call.add_argument("--skill-id", default="")
    call.add_argument("--run-id", default="")
    call.add_argument("--run-mode", default="mcp_cli")
    call.add_argument("--real", action="store_true", help="真实执行写工具；默认 dry-run")
    call.add_argument("--idempotency-key", default="", help="真实写工具必填幂等键")
    call.set_defaults(func=mcp_call)
    mcp_sub.add_parser("stdio").set_defaults(func=mcp_stdio)

    agent = sub.add_parser("agent")
    agent_sub = agent.add_subparsers(dest="agent_cmd")
    coverage = agent_sub.add_parser("coverage")
    coverage.add_argument("--department", default="", help="按部门名称过滤")
    coverage.add_argument("--status", default="", choices=["", "ready", "fallback", "missing"], help="按覆盖状态过滤")
    coverage.add_argument("--summary-only", action="store_true", help="只返回覆盖度汇总，不包含脱敏 Agent 摘要")
    coverage.add_argument("--json", action="store_true", help="输出原始 JSON")
    coverage.set_defaults(func=agent_coverage)
    analysis_agent = agent_sub.add_parser("analysis")
    analysis_sub = analysis_agent.add_subparsers(dest="agent_analysis_cmd")
    analysis_list = analysis_sub.add_parser("list")
    analysis_list.add_argument("--department", default="", help="按部门名称过滤")
    analysis_list.add_argument("--json", action="store_true", help="输出原始 JSON")
    analysis_list.set_defaults(func=agent_analysis_list)
    analysis_create = analysis_sub.add_parser("create")
    analysis_create.add_argument("--agent-id", default="")
    analysis_create.add_argument("--name", required=True)
    analysis_create.add_argument("--department", required=True)
    analysis_create.add_argument("--department-id", default="")
    analysis_create.add_argument("--owner", default="", help="负责人姓名/钉钉 ID 查询")
    analysis_create.add_argument("--owner-user-id", default="")
    analysis_create.add_argument("--skill-id", default="")
    analysis_create.add_argument("--prompt-version", default="analysis_v1")
    analysis_create.add_argument("--description", default="")
    analysis_create.add_argument("--dimension", action="append", default=[], help="分析维度，可重复")
    analysis_create.add_argument("--dimensions", default="", help="逗号分隔分析维度")
    analysis_create.add_argument("--default-params", default="", help="默认参数 JSON object")
    analysis_create.add_argument("--editor", action="append", default=[], help="可编辑人姓名/钉钉 ID 查询，可重复")
    analysis_create.add_argument("--editor-user-id", action="append", default=[], help="可编辑人平台 user_id，可重复")
    analysis_create.add_argument("--status", default="active")
    analysis_create.set_defaults(func=agent_analysis_create)
    analysis_update = analysis_sub.add_parser("update")
    analysis_update.add_argument("id")
    analysis_update.add_argument("--agent-id", default="")
    analysis_update.add_argument("--name", required=True)
    analysis_update.add_argument("--department", required=True)
    analysis_update.add_argument("--department-id", default="")
    analysis_update.add_argument("--owner", default="", help="负责人姓名/钉钉 ID 查询")
    analysis_update.add_argument("--owner-user-id", default="")
    analysis_update.add_argument("--skill-id", default="")
    analysis_update.add_argument("--prompt-version", default="analysis_v1")
    analysis_update.add_argument("--description", default="")
    analysis_update.add_argument("--dimension", action="append", default=[], help="分析维度，可重复")
    analysis_update.add_argument("--dimensions", default="", help="逗号分隔分析维度")
    analysis_update.add_argument("--default-params", default="", help="默认参数 JSON object")
    analysis_update.add_argument("--editor", action="append", default=[], help="可编辑人姓名/钉钉 ID 查询，可重复")
    analysis_update.add_argument("--editor-user-id", action="append", default=[], help="可编辑人平台 user_id，可重复")
    analysis_update.add_argument("--status", default="active")
    analysis_update.set_defaults(func=agent_analysis_update)
    analysis_validate = analysis_sub.add_parser("validate")
    analysis_validate.add_argument("id")
    analysis_validate.add_argument("--params", default="", help="覆盖验证参数 JSON object")
    analysis_validate.add_argument("--json", action="store_true", help="输出原始 JSON")
    analysis_validate.set_defaults(func=agent_analysis_validate)

    ai = sub.add_parser("ai")
    ai_sub = ai.add_subparsers(dest="ai_cmd")
    analyze = ai_sub.add_parser("analyze")
    analyze.add_argument("--prompt", default="", help="分析任务；传 - 时从 stdin 读取")
    analyze.add_argument("--system", default="", help="可选 system prompt；传 - 时从 stdin 读取")
    analyze.add_argument("--context", default="", help="可选文本上下文；传 - 时从 stdin 读取")
    analyze.add_argument("--context-json", default="", help="可选结构化上下文 JSON object")
    analyze.add_argument("--skill-id", default="", help="可选 Skill 归因并校验读取权限")
    analyze.add_argument("--json-mode", action="store_true", help="要求模型返回 JSON object")
    analyze.add_argument("--max-output-tokens", type=int, default=4096)
    analyze.add_argument("--temperature", type=float, default=None)
    analyze.set_defaults(func=ai_analyze)

    raw = sub.add_parser("raw")
    raw_sub = raw.add_subparsers(dest="raw_cmd")
    raw_query_p = raw_sub.add_parser("query")
    raw_query_p.add_argument(
        "--source",
        default="execution_runs",
        choices=["execution_runs", "execution_steps", "decision_logs", "collection_proofs", "api_schema_snapshots"],
    )
    raw_query_p.add_argument("--skill-id", default="")
    raw_query_p.add_argument("--run-id", default="", help="Skill 运行后的 execution_run id")
    raw_query_p.add_argument("--proof-id", default="")
    raw_query_p.add_argument("--platform", default="")
    raw_query_p.add_argument("--shop-id", default="")
    raw_query_p.add_argument("--data-scope", default="")
    raw_query_p.add_argument("--status", default="")
    raw_query_p.add_argument("--limit", type=int, default=20)
    raw_query_p.add_argument("--include-payload", action=argparse.BooleanOptionalAction, default=True)
    raw_query_p.add_argument("--include-endpoint", action="store_true")
    raw_query_p.set_defaults(func=raw_query)

    run = sub.add_parser("run")
    run_sub = run.add_subparsers(dest="run_cmd")
    run_analyze_p = run_sub.add_parser("analyze")
    run_analyze_p.add_argument("--run-id", required=True, help="Skill 运行后的 execution_run id")
    run_analyze_p.add_argument("--skill-id", default="", help="可选 Skill ID；会校验读取权限")
    run_analyze_p.add_argument("--prompt", default="", help="可选复盘任务；传 - 时从 stdin 读取")
    run_analyze_p.add_argument("--json-mode", action="store_true", help="要求模型返回 JSON object")
    run_analyze_p.add_argument("--max-output-tokens", type=int, default=4096)
    run_analyze_p.add_argument("--temperature", type=float, default=None)
    run_analyze_p.add_argument("--step-limit", type=int, default=20)
    run_analyze_p.add_argument("--decision-limit", type=int, default=50)
    run_analyze_p.add_argument("--proof-limit", type=int, default=50)
    run_analyze_p.add_argument("--snapshot-limit", type=int, default=20)
    run_analyze_p.add_argument("--include-raw", action="store_true", help="同时返回送入 AI 的脱敏原始数据")
    run_analyze_p.set_defaults(func=run_analyze)
    run_candidate_p = run_sub.add_parser("candidate")
    run_candidate_p.add_argument("--run-id", required=True, help="Skill 运行后的 execution_run id")
    run_candidate_p.add_argument("--title", default="")
    run_candidate_p.add_argument("--job-type", default="lora")
    run_candidate_p.add_argument("--training-strategy", default="")
    run_candidate_p.add_argument("--target-gateway-id", default="")
    run_candidate_p.add_argument("--dataset-ref", default="")
    run_candidate_p.add_argument("--objective", default="", help="训练目标；传 - 时从 stdin 读取")
    run_candidate_p.add_argument("--risk-level", default="")
    run_candidate_p.add_argument("--model-family", default="")
    run_candidate_p.add_argument("--analysis-summary", default="", help="AI 复盘摘要；传 - 时从 stdin 读取")
    run_candidate_p.add_argument("--analysis-model", default="")
    run_candidate_p.add_argument("--analysis-prompt-hash", default="")
    run_candidate_p.add_argument("--raw-counts", default="", help="AI 复盘返回的 raw_counts JSON object")
    run_candidate_p.add_argument("--eval-gate", default="", help="评估门禁 JSON object")
    run_candidate_p.add_argument("--base-model", default="")
    run_candidate_p.add_argument("--target-metric", default="")
    run_candidate_p.add_argument("--estimated-gpu-hours", type=float, default=None)
    run_candidate_p.add_argument("--gpu-estimate", default="", help="GPU 预估 JSON object")
    run_candidate_p.add_argument("--forbidden-action", action="append", default=[])
    run_candidate_p.add_argument("--risk-notes", default="", help="风险说明；传 - 时从 stdin 读取")
    run_candidate_p.add_argument("--allow-sandbox", action="store_true", help="允许基于 sandbox/sample 运行创建候选")
    run_candidate_p.set_defaults(func=run_candidate)

    training = sub.add_parser("training")
    training_sub = training.add_subparsers(dest="training_cmd")
    training_create_p = training_sub.add_parser("create")
    training_create_p.add_argument("--title", required=True)
    training_create_p.add_argument("--department", default="")
    training_create_p.add_argument("--job-type", default="lora")
    training_create_p.add_argument("--training-strategy", default="")
    training_create_p.add_argument("--target-skill-id", default="")
    training_create_p.add_argument("--target-gateway-id", default="")
    training_create_p.add_argument("--dataset-ref", default="")
    training_create_p.add_argument("--objective", default="", help="训练目标；传 - 时从 stdin 读取")
    training_create_p.add_argument("--risk-level", default="")
    training_create_p.add_argument("--spec", default="", help="训练 spec JSON object")
    training_create_p.add_argument("--model-name", default="")
    training_create_p.add_argument("--base-model", default="")
    training_create_p.add_argument("--estimated-gpu-hours", type=float, default=None)
    training_create_p.add_argument("--epochs", default="")
    training_create_p.add_argument("--learning-rate", default="")
    training_create_p.add_argument("--batch-size", default="")
    training_create_p.add_argument("--lora-rank", default="")
    training_create_p.add_argument("--max-steps", default="")
    training_create_p.add_argument("--param", action="append", default=[], help="追加训练参数 key=value，value 会按 JSON 标量解析")
    training_create_p.add_argument("--eval-gate", default="", help="评估门禁 JSON object")
    training_create_p.add_argument("--gpu-estimate", default="", help="GPU 预估 JSON object")
    training_create_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_create_p.set_defaults(func=training_create)
    training_resources_p = training_sub.add_parser("resources")
    training_resources_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_resources_p.set_defaults(func=training_resources)
    training_bootstrap_status_p = training_sub.add_parser("bootstrap-status")
    training_bootstrap_status_p.add_argument("gateway_id")
    training_bootstrap_status_p.add_argument("--profile", default="qlora-1b")
    training_bootstrap_status_p.add_argument("--cuda", default="cu121")
    training_bootstrap_status_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_bootstrap_status_p.set_defaults(func=training_bootstrap_status)
    training_bootstrap_env_p = training_sub.add_parser("bootstrap-env")
    training_bootstrap_env_p.add_argument("gateway_id")
    training_bootstrap_env_p.add_argument("--profile", default="qlora-1b")
    training_bootstrap_env_p.add_argument("--cuda", default="cu121")
    training_bootstrap_env_p.add_argument("--force", action="store_true")
    training_bootstrap_env_p.add_argument("--dry-run", action="store_true")
    training_bootstrap_env_p.add_argument("--timeout-seconds", type=int, default=3600)
    training_bootstrap_env_p.add_argument("--agent-purpose", default="mixed", choices=["", "training", "mixed"])
    training_bootstrap_env_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_bootstrap_env_p.set_defaults(func=training_bootstrap_env)
    training_model_status_p = training_sub.add_parser("model-status")
    training_model_status_p.add_argument("gateway_id")
    training_model_status_p.add_argument("--profile", default="qwen3.5-4b")
    training_model_status_p.add_argument("--revision", default="main")
    training_model_status_p.add_argument("--modelscope-revision", default="master")
    training_model_status_p.add_argument("--bootstrap-profile", default="qlora-1b")
    training_model_status_p.add_argument("--source", default="auto", choices=["auto", "huggingface", "modelscope"])
    training_model_status_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_model_status_p.set_defaults(func=training_model_status)
    training_prepare_model_p = training_sub.add_parser("prepare-model")
    training_prepare_model_p.add_argument("gateway_id")
    training_prepare_model_p.add_argument("--profile", default="qwen3.5-4b")
    training_prepare_model_p.add_argument("--revision", default="main")
    training_prepare_model_p.add_argument("--modelscope-revision", default="master")
    training_prepare_model_p.add_argument("--bootstrap-profile", default="qlora-1b")
    training_prepare_model_p.add_argument("--source", default="auto", choices=["auto", "huggingface", "modelscope"])
    training_prepare_model_p.add_argument("--validate-mode", default="metadata", choices=["none", "metadata", "load_4bit"])
    training_prepare_model_p.add_argument("--force", action="store_true")
    training_prepare_model_p.add_argument("--dry-run", action="store_true")
    training_prepare_model_p.add_argument("--timeout-seconds", type=int, default=7200)
    training_prepare_model_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_prepare_model_p.set_defaults(func=training_prepare_model)
    training_datasets_p = training_sub.add_parser("datasets")
    training_datasets_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_datasets_p.set_defaults(func=training_datasets)
    training_readiness_p = training_sub.add_parser("readiness")
    training_readiness_p.add_argument("skill_id")
    training_readiness_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_readiness_p.set_defaults(func=training_readiness)
    training_candidate_p = training_sub.add_parser("candidate")
    training_candidate_p.add_argument("skill_id")
    training_candidate_p.add_argument("--title", default="")
    training_candidate_p.add_argument("--job-type", default="lora")
    training_candidate_p.add_argument("--training-strategy", default="")
    training_candidate_p.add_argument("--target-gateway-id", default="")
    training_candidate_p.add_argument("--dataset-ref", default="")
    training_candidate_p.add_argument("--objective", default="", help="训练目标；传 - 时从 stdin 读取")
    training_candidate_p.add_argument("--risk-level", default="")
    training_candidate_p.add_argument("--model-family", default="")
    training_candidate_p.add_argument("--eval-gate", default="", help="评估门禁 JSON object")
    training_candidate_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_candidate_p.set_defaults(func=training_skill_candidate)
    training_jobs_p = training_sub.add_parser("jobs")
    training_jobs_p.add_argument("--status", default="", help="按训练任务状态过滤")
    training_jobs_p.add_argument("--gateway", default="", help="按训练 Agent / gateway id 过滤")
    training_jobs_p.add_argument("--target-gateway-id", default="", help="同 --gateway")
    training_jobs_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_jobs_p.set_defaults(func=training_jobs)
    training_job_p = training_sub.add_parser("job")
    training_job_p.add_argument("job_id")
    training_job_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_job_p.set_defaults(func=training_job_detail)
    training_logs_p = training_sub.add_parser("logs")
    training_logs_p.add_argument("job_id")
    training_logs_p.add_argument("--tail", type=int, default=100)
    training_logs_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_logs_p.set_defaults(func=training_job_logs)
    training_artifacts_p = training_sub.add_parser("artifacts")
    training_artifacts_p.add_argument("job_id")
    training_artifacts_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_artifacts_p.set_defaults(func=training_job_artifacts)
    training_download_p = training_sub.add_parser("download-artifact")
    training_download_p.add_argument("job_id")
    training_download_p.add_argument("artifact_id")
    training_download_p.add_argument("--output", "-o", default="", help="保存文件名，默认使用服务端文件名")
    training_download_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_download_p.set_defaults(func=training_artifact_download)
    for action_name in ("approve", "dispatch", "retry", "collect", "evaluate", "cancel"):
        action_parser = training_sub.add_parser(action_name)
        action_parser.add_argument("job_id")
        action_parser.add_argument("--json", action="store_true", help="输出原始 JSON")
        action_parser.set_defaults(func=training_action, training_action=action_name)
    training_collect_due_p = training_sub.add_parser("collect-due")
    training_collect_due_p.add_argument("--stale-seconds", type=int, default=300, help="只同步更新时间早于该秒数的运行态任务")
    training_collect_due_p.add_argument("--max-jobs", type=int, default=20, help="最多同步任务数")
    training_collect_due_p.add_argument("--gateway", default="", help="按训练 Agent / gateway id 过滤")
    training_collect_due_p.add_argument("--target-gateway-id", default="", help="同 --gateway")
    training_collect_due_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_collect_due_p.set_defaults(func=training_collect_due)
    training_force_cancel_p = training_sub.add_parser("force-cancel")
    training_force_cancel_p.add_argument("job_id")
    training_force_cancel_p.add_argument("--reason", required=True, help="强制取消原因；传 - 时从 stdin 读取")
    training_force_cancel_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_force_cancel_p.set_defaults(func=training_force_cancel)
    training_deploy_request_p = training_sub.add_parser("deploy-request")
    training_deploy_request_p.add_argument("job_id")
    training_deploy_request_p.add_argument("--target-skill-id", action="append", default=[], help="可重复；也可用逗号分隔")
    training_deploy_request_p.add_argument("--target-skill-ids", default="", help="逗号分隔目标 Skill")
    training_deploy_request_p.add_argument("--model-family", default="")
    training_deploy_request_p.add_argument("--rollout-percent", type=int, default=0)
    training_deploy_request_p.add_argument("--reason", default="", help="部署原因；传 - 时从 stdin 读取")
    training_deploy_request_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_deploy_request_p.set_defaults(func=training_deploy_request)
    training_deployments_p = training_sub.add_parser("deployments")
    training_deployments_p.add_argument("--status", default="", help="按部署状态过滤")
    training_deployments_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_deployments_p.set_defaults(func=training_deployments)
    training_deployment_p = training_sub.add_parser("deployment")
    training_deployment_p.add_argument("deployment_id")
    training_deployment_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    training_deployment_p.set_defaults(func=training_deployment_detail)
    for action_name in ("approve", "activate", "reject", "rollback"):
        deployment_action_parser = training_sub.add_parser(f"deployment-{action_name}")
        deployment_action_parser.add_argument("deployment_id")
        if action_name in {"reject", "rollback"}:
            help_text = "驳回原因；传 - 时从 stdin 读取" if action_name == "reject" else "回滚原因；传 - 时从 stdin 读取"
            deployment_action_parser.add_argument("--reason", required=True, help=help_text)
        else:
            deployment_action_parser.add_argument("--reason", default=argparse.SUPPRESS)
        deployment_action_parser.add_argument("--json", action="store_true", help="输出原始 JSON")
        deployment_action_parser.set_defaults(func=training_deployment_action, deployment_action=action_name)

    media = sub.add_parser("media")
    media_sub = media.add_subparsers(dest="media_cmd")
    media_status_p = media_sub.add_parser("status")
    media_status_p.add_argument("instance_id")
    media_status_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    media_status_p.set_defaults(func=media_bootstrap_status)
    media_bootstrap_p = media_sub.add_parser("bootstrap")
    media_bootstrap_p.add_argument("instance_id")
    media_bootstrap_p.add_argument("--accept-license", action="store_true", help="确认已接受 MiniMax H3 模型许可")
    media_bootstrap_p.add_argument("--force", action="store_true")
    media_bootstrap_p.add_argument("--dry-run", action="store_true")
    media_bootstrap_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    media_bootstrap_p.set_defaults(func=media_bootstrap_start)
    media_cancel_p = media_sub.add_parser("cancel")
    media_cancel_p.add_argument("instance_id")
    media_cancel_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    media_cancel_p.set_defaults(func=media_bootstrap_cancel)
    media_refresh_p = media_sub.add_parser("refresh-bridge")
    media_refresh_p.add_argument("instance_id")
    media_refresh_p.add_argument("--json", action="store_true", help="输出原始 JSON")
    media_refresh_p.set_defaults(func=media_bridge_refresh)

    data = sub.add_parser("data")
    data_sub = data.add_subparsers(dest="data_cmd")
    data_list_p = data_sub.add_parser("list")
    data_list_p.add_argument("--platform", default="")
    data_list_p.add_argument("--include-unavailable", action="store_true")
    data_list_p.set_defaults(func=data_list)
    data_latest_p = data_sub.add_parser("latest")
    data_latest_p.add_argument("capability")
    data_latest_p.add_argument("--limit", type=int, default=1)
    data_latest_p.set_defaults(func=data_latest)
    data_get_p = data_sub.add_parser("get")
    data_get_p.add_argument("capability", nargs="?")
    data_get_p.add_argument("--artifact-id", default="")
    data_get_p.add_argument("--run-id", default="")
    data_get_p.add_argument("--kind", default="")
    data_get_p.add_argument("--include-content", action="store_true")
    data_get_p.add_argument("--max-bytes", type=int, default=1024 * 1024)
    data_get_p.add_argument("--output", default="")
    data_get_p.set_defaults(func=data_get)
    data_records_p = data_sub.add_parser("records")
    data_records_p.add_argument("--page", type=int, default=1)
    data_records_p.add_argument("--page-size", type=int, default=50)
    data_records_p.add_argument("--namespace", default="")
    data_records_p.add_argument("--content-type", default="")
    data_records_p.add_argument("--skill-id", default="")
    data_records_p.add_argument("--run-id", default="")
    data_records_p.add_argument("--source", default="")
    data_records_p.add_argument("--q", default="")
    data_records_p.set_defaults(func=data_records)
    data_record_p = data_sub.add_parser("record")
    data_record_p.add_argument("record_id")
    data_record_p.set_defaults(func=data_record)
    data_write_p = data_sub.add_parser("write")
    data_write_p.add_argument("--namespace", required=True)
    data_write_p.add_argument("--title", default="")
    data_write_p.add_argument("--json", default="", help="JSON object；传 - 从 stdin 读取")
    data_write_p.add_argument("--text", default="", help="文本；传 - 从 stdin 读取")
    data_write_p.add_argument("--rows-json", default="", help="表格行 JSON array；传 - 从 stdin 读取")
    data_write_p.add_argument("--file", default="", help="作为 base64/binary 写入的本地文件")
    data_write_p.add_argument("--content-type", default="")
    data_write_p.add_argument("--metadata-json", default="")
    data_write_p.add_argument("--skill-id", default="")
    data_write_p.add_argument("--run-id", default="")
    data_write_p.add_argument("--source", default="sf_cli")
    data_write_p.add_argument("--source-ref", default="")
    data_write_p.add_argument("--visibility", default="global", choices=["global", "department", "private"])
    data_write_p.add_argument("--real", action="store_true", help="真实写入；默认 dry-run")
    data_write_p.add_argument("--idempotency-key", default="", help="真实写入必填幂等键")
    data_write_p.set_defaults(func=data_write)

    notify = sub.add_parser("notify")
    notify_sub = notify.add_subparsers(dest="notify_cmd")
    notify_users_p = notify_sub.add_parser("users")
    notify_users_p.add_argument("--query", default="")
    notify_users_p.add_argument("--department", default="")
    notify_users_p.add_argument("--limit", type=int, default=10)
    notify_users_p.set_defaults(func=notify_users)
    notify_send_p = notify_sub.add_parser("send")
    notify_send_p.add_argument("--title", required=True)
    notify_send_p.add_argument("--markdown", default="")
    notify_send_p.add_argument("--content", default="")
    notify_send_p.add_argument("--markdown-file", default="")
    notify_send_p.add_argument("--user-id", action="append", default=[])
    notify_send_p.add_argument("--dingtalk-user-id", action="append", default=[])
    notify_send_p.add_argument("--query", default="")
    notify_send_p.add_argument("--department", default="")
    notify_send_p.add_argument("--button-title", default="")
    notify_send_p.add_argument("--button-url", default="")
    notify_send_p.add_argument("--limit", type=int, default=20)
    notify_send_p.add_argument("--real", action="store_true")
    notify_send_p.add_argument("--idempotency-key", default="")
    notify_send_p.set_defaults(func=notify_send)

    catalog = sub.add_parser("catalog")
    catalog_sub = catalog.add_subparsers(dest="catalog_cmd")
    catalog_refresh_p = catalog_sub.add_parser("refresh")
    catalog_refresh_p.add_argument("--base-url", default=argparse.SUPPRESS, help="本次刷新临时使用的 SkillForge 地址，不写入本地配置")
    catalog_refresh_p.add_argument("--persist-base-url", action="store_true", help="配合 --base-url 或 SKILLFORGE_BASE_URL 使用时，明确把该地址写入本地配置")
    catalog_refresh_p.set_defaults(func=catalog_refresh)

    skill = sub.add_parser("skill")
    skill_sub = skill.add_subparsers(dest="skill_cmd")
    skill_list_p = skill_sub.add_parser("list")
    skill_list_p.add_argument("--scope", default="visible", choices=["visible", "editable", "publishable", "mine", "department"])
    skill_list_p.set_defaults(func=skill_list)
    install = skill_sub.add_parser("install")
    install.add_argument("skill_id")
    install.add_argument("--path", default=".", help="目标父目录；默认拉到 ./<skill_id>")
    install.add_argument("--force", action="store_true", help="覆盖目标目录中同名 Skill 文件")
    install.set_defaults(func=skill_pull)
    init = skill_sub.add_parser("init")
    init.add_argument("skill_id")
    init.add_argument("--name", default="")
    init.add_argument("--description", default="")
    init.add_argument("--department", default="")
    init.add_argument("--trigger-type", default="manual", choices=["manual", "cron", "event"])
    init.add_argument("--cron", default="")
    init.add_argument("--risk-level", default="R1", choices=["R1", "R2", "R3", "R4"])
    init.add_argument("--template", default="basic", choices=SKILL_TEMPLATES)
    init.add_argument("--path", default=".")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=skill_init)
    create = skill_sub.add_parser("create")
    create.add_argument("skill_id")
    create.add_argument("--name", default="")
    create.add_argument("--description", default="")
    create.add_argument("--department", default="")
    create.add_argument("--trigger-type", default="manual", choices=["manual", "cron", "event"])
    create.add_argument("--cron", default="")
    create.add_argument("--risk-level", default="R1", choices=["R1", "R2", "R3", "R4"])
    create.add_argument("--template", default="basic", choices=SKILL_TEMPLATES)
    create.add_argument("--path", default=".")
    create.add_argument("--force", action="store_true")
    create.set_defaults(func=skill_init)
    diff_p = skill_sub.add_parser("diff")
    diff_p.add_argument("--path", default=".")
    diff_p.add_argument("--skill-id", default="")
    diff_p.add_argument("--unified", action="store_true")
    diff_p.set_defaults(func=skill_diff)
    scan = skill_sub.add_parser("scan")
    scan.add_argument("--path", default=".")
    scan.add_argument("--compare-remote", action="store_true")
    scan.set_defaults(func=skill_scan)
    status = skill_sub.add_parser("status")
    status.add_argument("--path", default=".")
    status.set_defaults(func=skill_status)
    pull = skill_sub.add_parser("pull")
    pull.add_argument("skill_id")
    pull.add_argument("--path", default=".", help="目标父目录；默认拉到 ./<skill_id>")
    pull.add_argument("--force", action="store_true", help="覆盖目标目录中同名 Skill 文件")
    pull.set_defaults(func=skill_pull)
    doctor = skill_sub.add_parser("doctor")
    doctor.add_argument("--path", default=".")
    doctor.set_defaults(func=skill_doctor)
    test = skill_sub.add_parser("test")
    test.add_argument("--path", default=".")
    test.add_argument("--local", action="store_true")
    test.add_argument("--real-mcp", action="store_true")
    test.add_argument("--shop-id", default=None)
    test.set_defaults(func=skill_test)
    sandbox = skill_sub.add_parser("sandbox")
    sandbox.add_argument("--path", default=".")
    sandbox.add_argument("--shop-id", default=None)
    sandbox.set_defaults(func=skill_sandbox)
    submit = skill_sub.add_parser("submit")
    submit.add_argument("--path", default=".")
    submit.add_argument("--message", default="")
    submit.add_argument("--skip-local-test", action="store_true", help="跳过提交前本地 doctor/test（仅故障排查使用）")
    submit.add_argument("--force-submit", action="store_true", help="用户确认后强制进入审核队列，不直接发布")
    submit.add_argument("--force-reason", default="", help="强制提交原因，必须包含被绕过 gate 和用户确认摘要")
    submit.set_defaults(func=skill_submit)
    sync_p = skill_sub.add_parser("sync")
    sync_p.add_argument("skill_id")
    sync_p.add_argument("--instance-id", action="append", default=[], help="目标运行节点；可重复或用逗号分隔")
    sync_p.add_argument("--department", default="")
    sync_p.add_argument("--no-push-git", action="store_true")
    sync_p.set_defaults(func=skill_sync)
    publish_status = skill_sub.add_parser("publish-status")
    publish_status.add_argument("skill_id")
    publish_status.set_defaults(func=skill_publish_status)

    doctor_remote_p = sub.add_parser("doctor")
    doctor_remote_p.add_argument("--remote", action="store_true")
    doctor_remote_p.add_argument("--path", default=".")
    doctor_remote_p.set_defaults(func=doctor_cli)

    schedule = sub.add_parser("schedule")
    schedule_sub = schedule.add_subparsers(dest="schedule_cmd")
    schedule_get_p = schedule_sub.add_parser("get")
    schedule_get_p.add_argument("skill_id")
    schedule_get_p.set_defaults(func=schedule_get)
    schedule_set_p = schedule_sub.add_parser("set")
    schedule_set_p.add_argument("skill_id")
    schedule_set_p.add_argument("--cron", required=True)
    schedule_set_p.add_argument("--update", action="store_true")
    schedule_set_p.set_defaults(func=schedule_set)
    schedule_stop_p = schedule_sub.add_parser("stop")
    schedule_stop_p.add_argument("skill_id")
    schedule_stop_p.set_defaults(func=schedule_stop)
    schedule_sync_p = schedule_sub.add_parser("sync")
    schedule_sync_p.add_argument("skill_id")
    schedule_sync_p.set_defaults(func=schedule_sync)

    review = sub.add_parser("review")
    review_sub = review.add_subparsers(dest="review_cmd")
    review_list_p = review_sub.add_parser("list")
    review_list_p.add_argument("--status", default="")
    review_list_p.add_argument("--skill-id", default="")
    review_list_p.add_argument("--reviewer", default="")
    review_list_p.add_argument("--submitter", default="")
    review_list_p.add_argument("--page", type=int, default=1)
    review_list_p.add_argument("--page-size", type=int, default=20)
    review_list_p.set_defaults(func=review_list)
    review_get_p = review_sub.add_parser("get")
    review_get_p.add_argument("review_id", type=int)
    review_get_p.set_defaults(func=review_get)
    review_comment_p = review_sub.add_parser("comment")
    review_comment_p.add_argument("review_id", type=int)
    review_comment_p.add_argument("--content", default="")
    review_comment_p.add_argument("--file", default="")
    review_comment_p.add_argument("--file-path", default="")
    review_comment_p.add_argument("--line-number", type=int, default=None)
    review_comment_p.add_argument("--side", default="")
    review_comment_p.set_defaults(func=review_comment)
    review_changes_p = review_sub.add_parser("request-changes")
    review_changes_p.add_argument("review_id", type=int)
    review_changes_p.add_argument("--reason", default="")
    review_changes_p.add_argument("--file", default="")
    review_changes_p.add_argument("--reject-reason", default="request_changes")
    review_changes_p.set_defaults(func=review_request_changes)
    review_approve_p = review_sub.add_parser("approve")
    review_approve_p.add_argument("review_id", type=int)
    review_approve_p.add_argument("--no-verify", action="store_true")
    review_approve_p.set_defaults(func=review_approve)
    review_reject_p = review_sub.add_parser("reject")
    review_reject_p.add_argument("review_id", type=int)
    review_reject_p.add_argument("--reason", default="")
    review_reject_p.add_argument("--reject-reason", default="")
    review_reject_p.set_defaults(func=review_reject)

    project = sub.add_parser("project")
    project_sub = project.add_subparsers(dest="project_cmd")
    project_spec_p = project_sub.add_parser("spec")
    project_spec_p.add_argument("--recipe", default="", help="项目生成配方，例如 short-video-analysis")
    project_spec_p.add_argument("--json", action="store_true", help="兼容参数；spec 默认输出 JSON")
    project_spec_p.set_defaults(func=project_spec)
    project_org_p = project_sub.add_parser("org")
    project_org_sub = project_org_p.add_subparsers(dest="project_org_cmd")
    project_org_resolve_p = project_org_sub.add_parser("resolve")
    project_org_resolve_p.add_argument("--department", default="")
    project_org_resolve_p.add_argument("--department-id", default="")
    project_org_resolve_p.add_argument("--owner", default="")
    project_org_resolve_p.add_argument("--visibility", default="")
    project_org_resolve_p.set_defaults(func=project_org_resolve)
    project_init_p = project_sub.add_parser("init")
    project_init_p.add_argument("--path", default=".")
    project_init_p.add_argument("--project-id", default="")
    project_init_p.add_argument("--name", default="")
    project_init_p.add_argument("--kind", default="", help="web_static/external_web/dashboard/internal_tool/playbook_ref；不传时按 entry-url 自动判断")
    project_init_p.add_argument("--entry", default="", help="静态项目入口文件；不传时自动识别 web/dist/build/out/public/root index.html")
    project_init_p.add_argument("--entry-url", default="", help="外部 URL 项目入口；设置后 project submit 走自动登记")
    project_init_p.add_argument("--capability", action="append", default=[], help="声明项目可调用的平台能力，可重复传")
    project_init_p.add_argument("--force", action="store_true")
    project_init_p.set_defaults(func=project_init)
    project_doctor_p = project_sub.add_parser("doctor")
    project_doctor_p.add_argument("--path", default=".")
    project_doctor_p.add_argument("--recipe", default="", help="业务配方预检，例如 short-video-analysis")
    project_doctor_p.add_argument("--json", action="store_true", help="兼容参数；doctor 默认输出 JSON")
    project_doctor_p.set_defaults(func=project_doctor)
    project_submit_p = project_sub.add_parser("submit")
    project_submit_p.add_argument("--path", default=".")
    project_submit_p.add_argument("--no-build", action="store_true", help="发现未构建前端源码入口时不自动执行 package.json scripts.build")
    project_submit_p.set_defaults(func=project_submit)
    project_status_p = project_sub.add_parser("status")
    project_status_p.add_argument("project_id")
    project_status_p.set_defaults(func=project_status)
    project_logs_p = project_sub.add_parser("logs")
    project_logs_p.add_argument("project_run_id")
    project_logs_p.add_argument("--limit", type=int, default=100, help="每类 trace 最多返回条数，默认 100，平台上限 500")
    project_logs_p.add_argument("--offset", type=int, default=0, help="每类 trace 偏移量，用于兼容分页")
    project_logs_p.add_argument("--ingress-cursor", default="", help="输入/输出 trace 的 keyset 续页 cursor")
    project_logs_p.add_argument("--capability-cursor", default="", help="能力调用 trace 的 keyset 续页 cursor")
    project_logs_p.set_defaults(func=project_logs)
    project_asset_p = project_sub.add_parser("asset")
    project_asset_sub = project_asset_p.add_subparsers(dest="project_asset_cmd")
    project_asset_upload_p = project_asset_sub.add_parser("upload")
    project_asset_upload_p.add_argument("project_run_id")
    project_asset_upload_p.add_argument("file")
    project_asset_upload_p.add_argument("--name", default="", help="上传后显示的文件名；默认使用本地文件名")
    project_asset_upload_p.add_argument("--mime-type", default="", help="文件 MIME；默认按扩展名推断")
    project_asset_upload_p.add_argument("--metadata-json", default="{}", help="运行资产 metadata JSON object")
    project_asset_upload_p.add_argument("--role", default="", help="素材角色，例如 material_a/material_b")
    project_asset_upload_p.add_argument("--material-role", default="", help="素材角色别名，例如 material_a/material_b")
    project_asset_upload_p.add_argument("--video-role", default="", help="视频角色别名，例如 material_a/material_b")
    project_asset_upload_p.add_argument("--asset-type", default="", help="资产类型，例如 original_video/visual_frame")
    project_asset_upload_p.add_argument("--frame-time", default="", help="关键帧时间，单位秒")
    project_asset_upload_p.add_argument("--frame-label", default="", help="关键帧标签，例如 opening/middle/ending")
    project_asset_upload_p.add_argument("--source-file", default="", help="关键帧来源原视频文件名")
    project_asset_upload_p.set_defaults(func=project_asset_upload)
    project_asset_list_p = project_asset_sub.add_parser("list")
    project_asset_list_p.add_argument("project_run_id")
    project_asset_list_p.set_defaults(func=project_asset_list)
    project_invoke_p = project_sub.add_parser("invoke")
    project_invoke_p.add_argument("project_id")
    project_invoke_p.add_argument("--request-id", default="", help="幂等键；重复调用不会重复扣费或重复建待办")
    project_invoke_p.add_argument("--input-json", default="{}", help="项目服务 input JSON object")
    project_invoke_p.add_argument("--input-file", default="", help="从文件读取项目服务 input JSON object")
    project_invoke_p.add_argument("--params-json", default="", help="附加 params JSON object")
    project_invoke_p.add_argument("--prompt", default="", help="服务调用提示词；不传则由平台按服务契约生成")
    project_invoke_p.add_argument("--capability", default="", help="指定项目已声明的平台能力，默认优先 ai.cheap.generate")
    project_invoke_p.add_argument("--output-json", default="", help="手动提供 output JSON object；用于只记录服务输出不调用能力")
    project_invoke_p.add_argument("--report-json", action="append", default=[], help="追加 reports[] JSON object，可重复传")
    project_invoke_p.add_argument("--todo-json", action="append", default=[], help="追加 todos[] JSON object，可重复传")
    project_invoke_p.add_argument("--proof-json", action="append", default=[], help="追加 proofs[] JSON object，可重复传")
    project_invoke_p.add_argument("--analysis-prompt", default="", help="输出进入 AI 循环时的分析提示词")
    project_invoke_p.add_argument("--no-auto-analyze", action="store_true", help="只记录输出，不自动触发 AI 分析")
    project_invoke_p.set_defaults(func=project_invoke)
    project_verify_p = project_sub.add_parser("verify")
    project_verify_p.add_argument("project_id")
    project_verify_p.add_argument("--recipe", default="", help="业务验证配方，例如 short-video-analysis")
    project_verify_p.add_argument("--request-id", default="")
    project_verify_p.add_argument("--input-json", default="{}", help="追加验证输入 JSON object")
    project_verify_p.add_argument("--prompt", default="")
    project_verify_p.add_argument("--capability", default="")
    project_verify_p.add_argument("--simulate-video-upload-failure", default="")
    project_verify_p.add_argument("--json", action="store_true", help="兼容参数；verify 默认输出 JSON")
    project_verify_p.set_defaults(func=project_verify)

    submission = sub.add_parser("submission")
    submission_sub = submission.add_subparsers(dest="submission_cmd")
    sub_status = submission_sub.add_parser("status")
    sub_status.add_argument("submission_id")
    sub_status.set_defaults(func=submission_status)
    sub_approve = submission_sub.add_parser("approve")
    sub_approve.add_argument("submission_id")
    sub_approve.add_argument("--no-verify", action="store_true")
    sub_approve.add_argument("--static-check-override", action="store_true")
    sub_approve.add_argument("--static-check-override-reason", default="")
    sub_approve.set_defaults(func=submission_approve)

    output = sub.add_parser("output")
    output_sub = output.add_subparsers(dest="output_cmd")
    output_validate_p = output_sub.add_parser("validate")
    output_validate_p.add_argument("file", help="输出 JSON 文件")
    output_validate_p.add_argument("--max-bytes", type=int, default=6 * 1024 * 1024)
    output_validate_p.set_defaults(func=output_validate)

    market = sub.add_parser("market")
    market_sub = market.add_subparsers(dest="market_cmd")
    market_list_p = market_sub.add_parser("list")
    market_list_p.add_argument("--category", default="")
    market_list_p.add_argument("--certified-only", action="store_true")
    market_list_p.set_defaults(func=market_list)
    market_detail_p = market_sub.add_parser("detail")
    market_detail_p.add_argument("skill_id")
    market_detail_p.set_defaults(func=market_detail)
    market_install_p = market_sub.add_parser("install")
    market_install_p.add_argument("skill_id")
    market_install_p.add_argument("--path", default=".")
    market_install_p.add_argument("--force", action="store_true")
    market_install_p.set_defaults(func=market_install)
    market_metrics_p = market_sub.add_parser("metrics")
    market_metrics_p.add_argument("skill_id")
    market_metrics_p.set_defaults(func=market_metrics)
    market_certify_p = market_sub.add_parser("certify")
    market_certify_p.add_argument("skill_id")
    market_certify_p.set_defaults(func=market_certify)
    market_visibility_p = market_sub.add_parser("visibility")
    market_visibility_p.add_argument("skill_id")
    market_visibility_p.add_argument("market_status")
    market_visibility_p.set_defaults(func=market_visibility)
    market_ratings_p = market_sub.add_parser("ratings")
    market_ratings_p.add_argument("skill_id")
    market_ratings_p.set_defaults(func=market_ratings)
    market_rate_p = market_sub.add_parser("rate")
    market_rate_p.add_argument("skill_id")
    market_rate_p.add_argument("--rating", type=int, required=True)
    market_rate_p.add_argument("--comment", default="")
    market_rate_p.set_defaults(func=market_rate)

    pipeline = sub.add_parser("pipeline")
    pipeline_sub = pipeline.add_subparsers(dest="pipeline_cmd")
    pipeline_run_p = pipeline_sub.add_parser("run")
    pipeline_run_p.add_argument("--path", default=".")
    pipeline_run_p.add_argument("--remote", action="store_true")
    pipeline_run_p.add_argument("--submit", action="store_true")
    pipeline_run_p.add_argument("--message", default="")
    pipeline_run_p.add_argument("--keep-going", action="store_true")
    pipeline_run_p.set_defaults(func=pipeline_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    argv = apply_chinese_alias(raw_argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    set_runtime_base_url(getattr(args, "base_url", ""))
    if not hasattr(args, "func"):
        sf_help(args)
        return 0
    maybe_print_update_notice(argv, args)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
