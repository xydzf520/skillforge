#!/usr/bin/env python3
"""SkillForge deployment health audit.

Read-only by default. It prints a redacted JSON report and exits non-zero only
when BLOCKER checks fail. Use --probe-ai to perform a tiny LLM call; that call
may create a usage_logs row and minimal model cost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import redis.asyncio as aioredis
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bootstrap.health_checks import _normalize_operational_warnings  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import async_session_factory, engine  # noqa: E402

try:
    engine.echo = False
except Exception:
    pass


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: dict[str, Any]


BLOCKER = "BLOCKER"
DEGRADED = "DEGRADED"
OK = "OK"
INFO = "INFO"

SYSTEM_CONFIG_KEYS = [
    "ai.api_base",
    "ai.api_key",
    "ai.model",
    "ai.cheap.api_base",
    "ai.cheap.api_key",
    "ai.cheap.model",
    "coding_agent.enabled",
    "coding_agent.provider",
    "coding_agent.api_base",
    "coding_agent.api_key",
    "coding_agent.model",
    "coding_agent.mcp_servers",
    "project.service_conversion.ai_enabled",
    "knowledge.rag.enabled",
    "knowledge.rag.strict_server",
    "knowledge.lightrag.mode",
    "knowledge.lightrag.flavor",
    "knowledge.lightrag.api_base",
    "knowledge.lightrag.api_key",
    "knowledge.docling.enabled",
    "knowledge.docling.api_base",
    "knowledge.vlm.provider",
    "knowledge.vlm.api_base",
    "knowledge.vlm.api_key",
    "knowledge.vlm.model",
    "ai.embedding.provider",
    "ai.embedding.api_base",
    "ai.embedding.api_key",
    "ai.embedding.model",
    "ai.embedding.dim",
    "connector.api_key",
    "yuyidata.app_key",
    "yuyidata.app_secret",
    "skill_git.local_remote_url",
    "skill_git.username",
    "skill_git.password",
]

REQUIRED_TABLES = [
    "alembic_version",
    "system_config",
    "users",
    "skills",
    "execution_runs",
    "node_schedule_configs",
    "projects",
    "project_versions",
    "project_runs",
    "project_ingress_events",
    "project_capability_calls",
    "training_jobs",
    "training_job_tasks",
    "model_deployments",
    "codex_cli_sessions",
    "codex_login_intents",
    "codex_debug_runs",
    "codex_run_tokens",
    "codex_mcp_call_audit",
    "codex_skill_submissions",
    "codex_output_previews",
    "execution_artifacts",
    "inbox_report_cards",
    "learning_automation_runs",
    "learning_governance_tasks",
    "improvement_candidates",
    "usage_logs",
]

OPTIONAL_TABLES = [
    "department_knowledge_bases",
]

SECRET_KEY_PARTS = ("key", "secret", "token", "password", "credential", "cookie")
SECRET_URL_KEYS = {"DATABASE_URL", "DATABASE_URL_SYNC", "REDIS_URL"}


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SECRET_KEY_PARTS)


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def value_shape(value: Any, *, key: str = "") -> dict[str, Any]:
    if value is None:
        return {"present": False, "type": None}
    if isinstance(value, str):
        shape: dict[str, Any] = {"present": bool(value.strip()), "type": "str", "length": len(value)}
        if not is_secret_key(key):
            shape["sample"] = value[:120]
        return shape
    if isinstance(value, dict):
        return {"present": bool(value), "type": "dict", "keys": sorted(map(str, value.keys()))[:30]}
    if isinstance(value, list):
        return {"present": bool(value), "type": "list", "length": len(value)}
    return {"present": True, "type": type(value).__name__}


def mask_url(url: Any) -> str:
    parsed = urlparse(str(url or ""))
    netloc = parsed.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        user = userinfo.split(":", 1)[0] if userinfo else ""
        netloc = f"{user}:***@{host}" if user else f"***@{host}"
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def run_cmd(args: list[str], *, cwd: Path = ROOT, timeout: int = 5) -> dict[str, Any]:
    try:
        proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip()[:2000],
            "stderr": proc.stderr.strip()[:2000],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def tcp_probe(host: str, port: int, *, timeout: float = 2) -> dict[str, Any]:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def http_probe(url: str, *, timeout: float = 3) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        return {
            "ok": 200 <= resp.status_code < 400,
            "status_code": resp.status_code,
            "content_type": resp.headers.get("content-type", "")[:120],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def db_scalar(query: str, params: dict[str, Any] | None = None) -> Any:
    async with async_session_factory() as db:
        return (await db.execute(text(query), params or {})).scalar()


def parse_systemctl_show(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in str(output or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def read_process_env(pid: str, keys: list[str]) -> dict[str, Any]:
    if not pid or pid == "0":
        return {}
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    env: dict[str, Any] = {}
    wanted = set(keys)
    for item in raw:
        if b"=" not in item:
            continue
        key_raw, value_raw = item.split(b"=", 1)
        key = key_raw.decode("utf-8", "replace")
        if key not in wanted:
            continue
        value = value_raw.decode("utf-8", "replace")
        env[key] = value_shape(value, key=key)
        if key in SECRET_URL_KEYS:
            env[key].pop("sample", None)
            env[key]["value"] = mask_url(value)
        elif key in {"VUE_DIST_PATH", "PUBLIC_BASE_URL"}:
            env[key]["value"] = value
    return env


async def check_service() -> Check:
    status = run_cmd(["systemctl", "--user", "show", "skillforge.service", "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "WorkingDirectory", "--no-pager"])
    parsed = parse_systemctl_show(str(status.get("stdout", "")))
    runtime_env = read_process_env(
        parsed.get("MainPID", ""),
        ["VUE_DIST_PATH", "REDIS_URL", "DATABASE_URL", "DATABASE_URL_SYNC", "PUBLIC_BASE_URL", "DINGTALK_CALLBACK_TOKEN"],
    )
    details = {"systemctl": status, "parsed": parsed, "runtime_env": runtime_env, "expected_root": str(ROOT)}
    ok = parsed.get("ActiveState") == "active" and parsed.get("SubState") == "running"
    if ok and parsed.get("WorkingDirectory") == str(ROOT):
        return Check("systemd_service", OK, details)
    return Check("systemd_service", BLOCKER if not ok else DEGRADED, details)


async def check_git() -> Check:
    status = run_cmd(["git", "status", "-sb"])
    branch = str(status.get("stdout", ""))
    lines = [line for line in branch.splitlines() if line.strip()]
    clean = bool(lines) and "[ahead" not in lines[0] and "[behind" not in lines[0] and len(lines) == 1
    return Check("release_git", OK if clean else DEGRADED, {"status": branch})


async def check_alembic() -> Check:
    alembic = run_cmd([sys.executable, "-m", "alembic", "current"], timeout=10)
    heads = run_cmd([sys.executable, "-m", "alembic", "heads"], timeout=10)
    db_versions = []
    try:
        async with async_session_factory() as db:
            db_versions = list((await db.execute(text("select version_num from alembic_version order by version_num"))).scalars().all())
    except Exception as exc:  # noqa: BLE001
        return Check("database_migrations", BLOCKER, {"error": f"{type(exc).__name__}: {exc}", "alembic": alembic, "heads": heads})
    head_versions = [
        line.split(" ", 1)[0]
        for line in str(heads.get("stdout", "")).splitlines()
        if line.strip()
    ]
    ok = (
        alembic.get("ok")
        and heads.get("ok")
        and bool(head_versions)
        and sorted(db_versions) == sorted(head_versions)
        and all(f"{version} (head)" in str(alembic.get("stdout", "")) for version in head_versions)
    )
    return Check(
        "database_migrations",
        OK if ok else BLOCKER,
        {"alembic": alembic, "heads": heads, "db_versions": db_versions, "expected_heads": head_versions},
    )


async def check_tables() -> Check:
    detail: dict[str, Any] = {"required": {}, "optional": {}}
    missing: list[str] = []
    try:
        async with async_session_factory() as db:
            for table in REQUIRED_TABLES + OPTIONAL_TABLES:
                exists = (await db.execute(text("select to_regclass(:name)"), {"name": table})).scalar()
                bucket = "required" if table in REQUIRED_TABLES else "optional"
                if exists:
                    count = (await db.execute(text(f"select count(*) from {table}"))).scalar()
                    detail[bucket][table] = {"exists": True, "count": int(count or 0)}
                else:
                    detail[bucket][table] = {"exists": False}
                    if bucket == "required":
                        missing.append(table)
    except Exception as exc:  # noqa: BLE001
        return Check("database_tables", BLOCKER, {"error": f"{type(exc).__name__}: {exc}"})
    return Check("database_tables", OK if not missing else BLOCKER, {"missing_required": missing, **detail})


async def check_system_config() -> Check:
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(text("select key, value from system_config where key = any(:keys) order by key"), {"keys": SYSTEM_CONFIG_KEYS})).all()
    except Exception as exc:  # noqa: BLE001
        return Check("system_config", BLOCKER, {"error": f"{type(exc).__name__}: {exc}"})
    values = {key: value_shape(value, key=key) for key, value in rows}
    for key in SYSTEM_CONFIG_KEYS:
        values.setdefault(key, {"present": False, "type": None})
    missing_ai = [key for key in ["ai.api_base", "ai.api_key", "ai.model"] if not values[key].get("present")]
    missing_coding = [key for key in ["coding_agent.api_key", "coding_agent.model"] if not values[key].get("present")]
    status = OK if not missing_ai else BLOCKER
    if not missing_ai and missing_coding:
        status = DEGRADED
    return Check("system_config", status, {"values": values, "missing_ai": missing_ai, "missing_coding_agent": missing_coding})


async def check_settings() -> Check:
    prod_errors = settings.validate_production()
    raw_warnings = settings.validate_operational_warnings()
    normalized = await _normalize_operational_warnings(raw_warnings)
    dingtalk_callback_missing = any("DINGTALK_CALLBACK_TOKEN" in warning for warning in normalized)
    detail = {
        "production_errors": prod_errors,
        "operational_warnings": normalized,
        "configured_paths": {
            "root": str(ROOT),
            "script_process_vue_dist_path": os.environ.get("VUE_DIST_PATH") or settings.VUE_DIST_PATH,
            "redis_url": mask_url(settings.REDIS_URL),
            "database_url": mask_url(settings.DATABASE_URL),
            "skill_repo_path": settings.SKILL_REPO_PATH,
            "public_base_url": settings.PUBLIC_BASE_URL,
        },
        "env_presence": {
            "DINGTALK_CALLBACK_TOKEN": value_shape(settings.DINGTALK_CALLBACK_TOKEN, key="DINGTALK_CALLBACK_TOKEN"),
            "DINGTALK_LOGIN_REDIRECT": value_shape(settings.DINGTALK_LOGIN_REDIRECT, key="DINGTALK_LOGIN_REDIRECT"),
            "COOKIE_ENCRYPT_KEY": value_shape(settings.COOKIE_ENCRYPT_KEY, key="COOKIE_ENCRYPT_KEY"),
        },
    }
    if prod_errors:
        return Check("settings", BLOCKER, detail)
    return Check("settings", DEGRADED if dingtalk_callback_missing else OK, detail)


async def check_frontend_release_alignment() -> Check:
    expected_dist = ROOT / "web" / "dist"
    configured_raw = str(os.environ.get("VUE_DIST_PATH") or settings.VUE_DIST_PATH or "").strip()
    configured_dist = Path(configured_raw).expanduser() if configured_raw else None
    release_mode = ROOT.parent.name == "skillforge-releases"
    effective_dist = expected_dist if release_mode else configured_dist or expected_dist
    index_path = effective_dist / "index.html"
    entry_asset = None
    if index_path.is_file():
        try:
            content = index_path.read_text(encoding="utf-8")
            match = re.search(r'(?:src|href)=["\']/assets/([^"\']+)', content)
            entry_asset = match.group(1) if match else None
        except OSError:
            entry_asset = None
    configured_mismatch = bool(
        release_mode
        and configured_dist is not None
        and configured_dist.resolve(strict=False) != expected_dist.resolve(strict=False)
    )
    detail = {
        "release_mode": release_mode,
        "root": str(ROOT),
        "expected_dist": str(expected_dist),
        "configured_dist": str(configured_dist) if configured_dist else None,
        "effective_dist": str(effective_dist),
        "configured_mismatch_ignored": configured_mismatch,
        "index_exists": index_path.is_file(),
        "entry_asset": entry_asset,
    }
    if not index_path.is_file() or not entry_asset:
        return Check("frontend_release_alignment", BLOCKER, detail)
    return Check("frontend_release_alignment", DEGRADED if configured_mismatch else OK, detail)


async def check_redis() -> Check:
    try:
        client = aioredis.from_url(str(settings.REDIS_URL), socket_connect_timeout=2, socket_timeout=2)
        try:
            pong = await client.ping()
            info = await client.info("server")
        finally:
            await client.aclose()
        return Check("redis", OK if pong else BLOCKER, {"url": mask_url(settings.REDIS_URL), "redis_version": info.get("redis_version")})
    except Exception as exc:  # noqa: BLE001
        return Check("redis", BLOCKER, {"url": mask_url(settings.REDIS_URL), "error": f"{type(exc).__name__}: {exc}"})


async def check_browser() -> Check:
    cdp = await http_probe(f"http://{settings.BROWSER_CDP_HOST}:{settings.BROWSER_CDP_PORT}/json/version")
    verify = await http_probe(f"http://{settings.BROWSER_VERIFY_CDP_HOST}:{settings.BROWSER_VERIFY_CDP_PORT}/json/version")
    novnc = await http_probe(f"http://{settings.BROWSER_NOVNC_HOST}:{settings.BROWSER_NOVNC_PORT}/")
    docker = run_cmd(["docker", "ps", "-a", "--filter", "name=skillforge-browser", "--filter", "name=skillforge-browser-verify", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"], timeout=5)
    status = OK if cdp.get("ok") and verify.get("ok") and novnc.get("ok") else DEGRADED
    if not cdp.get("ok"):
        status = BLOCKER
    return Check("browser", status, {"cdp": cdp, "verify_cdp": verify, "novnc": novnc, "containers": docker.get("stdout", "")})


async def check_public_endpoints() -> Check:
    health = await http_probe("http://127.0.0.1:8000/health")
    manifest = await http_probe("http://127.0.0.1:8000/api/codex/catalog/manifest?format=json")
    status = OK if health.get("ok") and manifest.get("ok") else BLOCKER
    return Check("public_endpoints", status, {"health": health, "codex_manifest": manifest})


async def check_gitea() -> Check:
    remote = settings.SKILL_REPO_CANONICAL_REMOTE or settings.SKILL_REPO_REMOTE
    if not remote:
        return Check("skill_git", DEGRADED, {"remote_present": False})
    base = str(remote)
    if base.endswith(".git"):
        parts = base.rsplit("/", 2)
        if len(parts) >= 3:
            base = parts[0]
    parsed = urlparse(base)
    host_url = urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")) if parsed.scheme else ""
    detail = {"remote_present": True, "remote": mask_url(remote), "base": mask_url(host_url)}
    if not host_url:
        return Check("skill_git", DEGRADED, detail)
    probe = await http_probe(f"{host_url}/api/v1/version")
    detail["gitea_version_api"] = probe
    return Check("skill_git", OK if probe.get("ok") else DEGRADED, detail)


async def check_vector_rag() -> Check:
    keys = [
        "knowledge.rag.enabled",
        "knowledge.lightrag.mode",
        "knowledge.lightrag.api_base",
        "knowledge.docling.enabled",
        "knowledge.docling.api_base",
        "knowledge.vlm.api_base",
        "knowledge.vlm.api_key",
        "knowledge.vlm.model",
        "ai.embedding.api_base",
        "ai.embedding.api_key",
        "ai.embedding.model",
    ]
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(text("select key, value from system_config where key = any(:keys)"), {"keys": keys})).all()
    except Exception as exc:  # noqa: BLE001
        return Check("vector_rag", DEGRADED, {"error": f"{type(exc).__name__}: {exc}"})
    raw_values = {key: value for key, value in rows}
    values = {key: value_shape(value, key=key) for key, value in rows}
    for key in keys:
        values.setdefault(key, {"present": False, "type": None})
    rag_enabled = truthy(raw_values.get("knowledge.rag.enabled"))
    lightrag_api = values["knowledge.lightrag.api_base"].get("present")
    embedding_ok = all(values[key].get("present") for key in ["ai.embedding.api_base", "ai.embedding.api_key", "ai.embedding.model"])
    docling_enabled = truthy(raw_values.get("knowledge.docling.enabled"))
    docling_api = values["knowledge.docling.api_base"].get("present")
    vlm_ok = all(values[key].get("present") for key in ["knowledge.vlm.api_base", "knowledge.vlm.api_key", "knowledge.vlm.model"])
    lightrag = await http_probe("http://127.0.0.1:19621/health")
    docling = await http_probe("http://127.0.0.1:15001/docs")
    missing = []
    if rag_enabled and not lightrag_api:
        missing.append("knowledge.lightrag.api_base")
    if rag_enabled and not embedding_ok:
        missing.append("ai.embedding.api_base/api_key/model")
    if rag_enabled and not lightrag.get("ok"):
        missing.append("skillforge-lightrag loopback health")
    if docling_enabled and not docling_api:
        missing.append("knowledge.docling.api_base")
    if docling_enabled and not vlm_ok:
        missing.append("knowledge.vlm.api_base/api_key/model")
    if docling_enabled and not docling.get("ok"):
        missing.append("skillforge-docling loopback docs")
    status = OK
    if missing:
        status = DEGRADED
    return Check("vector_rag", status, {
        "values": values,
        "missing_when_enabled": missing,
        "lightrag_loopback": lightrag,
        "docling_loopback": docling,
    })


async def check_ai_probe(enabled: bool) -> Check:
    if not enabled:
        return Check("ai_probe", INFO, {"skipped": True, "reason": "pass --probe-ai to perform a tiny LLM call"})
    try:
        from app.common.ai import call_llm, get_ai_profile_config
        cfg = await get_ai_profile_config(require_system_config=True)
        result = await call_llm(
            "Return compact JSON only.",
            "Return {\"ok\":true,\"probe\":\"skillforge\"}.",
            max_tokens=32,
            temperature=0,
            timeout=10,
            json_mode=True,
            call_source="deploy_health_audit",
            require_system_config=True,
        )
        ok = bool(result)
        return Check("ai_probe", OK if ok else DEGRADED, {"result_type": type(result).__name__, "model_present": bool(cfg.get("ai.model")), "api_base_present": bool(cfg.get("ai.api_base"))})
    except Exception as exc:  # noqa: BLE001
        return Check("ai_probe", BLOCKER, {"error": f"{type(exc).__name__}: {str(exc)[:500]}"})


async def audit(*, probe_ai: bool) -> dict[str, Any]:
    checks = [
        await check_service(),
        await check_git(),
        await check_alembic(),
        await check_tables(),
        await check_system_config(),
        await check_settings(),
        await check_frontend_release_alignment(),
        await check_redis(),
        await check_browser(),
        await check_public_endpoints(),
        await check_gitea(),
        await check_vector_rag(),
        await check_ai_probe(probe_ai),
    ]
    counts = {BLOCKER: 0, DEGRADED: 0, OK: 0, INFO: 0}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    overall = BLOCKER if counts[BLOCKER] else DEGRADED if counts[DEGRADED] else OK
    return {
        "ok": overall == OK,
        "overall": overall,
        "counts": counts,
        "root": str(ROOT),
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="SkillForge deployment health audit")
    parser.add_argument("--probe-ai", action="store_true", help="perform a tiny LLM call; may write usage_logs/cost records")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    args = parser.parse_args()
    result = asyncio.run(audit(probe_ai=args.probe_ai))
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    raise SystemExit(1 if result["overall"] == BLOCKER else 0)


if __name__ == "__main__":
    main()
