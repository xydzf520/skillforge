"""
Skill文件同步：审核通过后 git push + 通知OpenClaw reload。
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from loguru import logger

from app.common.exceptions import AppError
from app.config import settings
from app.dingtalk.recipients import enqueue_admin_work_notice
from app.execution.bridge_placement import (
    BRIDGE_PLACEMENT_CONFLICT,
    active_platform_fingerprints,
    bridge_has_platform_fingerprint_conflict,
)
from app.execution.model_context import active_model_context_for_skill
from app.common.time_utils import isoformat_bjt, now_bjt
from app.platform_settings import is_platform_node_fallback_enabled


PLATFORM_FALLBACK_DEPARTMENTS = ("AI", "AI小组", "平台", "平台内置", "系统", "SkillForge")
AGENT_PURPOSE_ALLOWED = {"skill_runtime", "analysis", "training", "media", "mixed"}
SKILL_RUNTIME_AGENT_PURPOSES = {"skill_runtime", "mixed"}

SYNC_EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "tests",
}
SYNC_EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
}
SYNC_MAX_FILE_BYTES = 512 * 1024
SYNC_MAX_ENTRY_FILE_BYTES = 2 * 1024 * 1024
AUTO_RETRY_MAX_ATTEMPTS = 5
NON_RETRYABLE_SYNC_ERRORS = {
    "SKILL_SOURCE_NOT_FOUND",
    "SKILL_SOURCE_EMPTY",
    "SKILL_ENTRY_SCRIPT_NOT_SYNCABLE",
    "SKILL_NOT_FOUND",
}
RUNTIME_MCP_BUNDLES = {
    "mcp://tmall_": [
        "scripts/mcp_base.py",
        "scripts/skillforge_mcp_runtime.py",
        "scripts/collection_client.py",
        "scripts/tmall_mcp_server.py",
        "scripts/browser_mcp_server.py",
        "scripts/tmall_item_reviews_probe.py",
    ],
    "mcp://yuyidata_": [
        "scripts/mcp_base.py",
        "scripts/skillforge_mcp_runtime.py",
        "scripts/collection_client.py",
        "scripts/yuyidata_mcp_server.py",
    ],
}


def _agent_purpose(instance: Any | None) -> str:
    value = str(getattr(instance, "agent_purpose", "") or "skill_runtime").strip().lower()
    return value if value in AGENT_PURPOSE_ALLOWED else "skill_runtime"


def _is_skill_runtime_agent(instance: Any | None) -> bool:
    return _agent_purpose(instance) in SKILL_RUNTIME_AGENT_PURPOSES


def _agent_purpose_reject_reason(instance: Any | None) -> str:
    purpose = _agent_purpose(instance)
    return f"该 Agent 用途为 {purpose}，不承载 Skill Runtime"

_SKILL_GIT_CONFIG_KEYS = {
    "skill_git.local_remote_url": "local_remote_url",
    "skill_git.username": "username",
    "skill_git.password": "password",
}


def build_reload_endpoint_url(raw_url: str | None) -> tuple[str | None, str | None]:
    """Return a concrete /reload endpoint or a stable validation error."""
    clean_url = str(raw_url or "").strip()
    if not clean_url:
        return None, "INVALID_RELOAD_HOOK_URL: empty"

    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", clean_url):
        parsed = urlsplit(clean_url)
        if parsed.scheme not in {"http", "https"}:
            return None, f"INVALID_RELOAD_HOOK_URL: unsupported scheme {parsed.scheme}"
    else:
        parsed = urlsplit(f"http://{clean_url}")

    if parsed.scheme not in {"http", "https"}:
        return None, f"INVALID_RELOAD_HOOK_URL: unsupported scheme {parsed.scheme}"
    if not parsed.netloc:
        return None, "INVALID_RELOAD_HOOK_URL: missing host"

    path = parsed.path.rstrip("/")
    if path.endswith("/reload"):
        path = path[: -len("/reload")]
    path = path.rstrip("/")
    base = urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")
    return f"{base}/reload", None


def build_reload_health_url(raw_url: str | None) -> tuple[str | None, str | None]:
    """Return the health endpoint matching a reload hook base."""
    reload_endpoint, error = build_reload_endpoint_url(raw_url)
    if error or not reload_endpoint:
        return None, error
    return reload_endpoint[: -len("/reload")] + "/health", None


def _reload_auth_headers(token: str | None) -> dict[str, str]:
    clean_token = str(token or "").strip()
    if not clean_token:
        return {}
    return {"Authorization": f"Bearer {clean_token}"}


def _inject_git_credentials(url: str, username: str = "", password: str = "") -> str:
    """Return an HTTP(S) Git URL with configured credentials injected."""
    clean_url = (url or "").strip()
    clean_user = (username or "").strip()
    if not clean_url or not clean_user:
        return clean_url
    parsed = urlsplit(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return clean_url
    netloc = parsed.hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    userinfo = quote(clean_user, safe="")
    if password:
        userinfo = f"{userinfo}:{quote(str(password), safe='')}"
    return urlunsplit((parsed.scheme, f"{userinfo}@{netloc}", parsed.path, parsed.query, parsed.fragment))


def _redact_git_url(url: str) -> str:
    parsed = urlsplit(url or "")
    if not parsed.username:
        return url
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, f"***@{netloc}", parsed.path, parsed.query, parsed.fragment))


def _should_sync_skill_file(rel_path: str, size: int) -> bool:
    """Return whether a file belongs in the runtime sync bundle."""
    path = rel_path.replace("\\", "/").lstrip("/")
    parts = path.split("/")
    if any(part in SYNC_EXCLUDED_DIRS for part in parts):
        return False
    if any(path.endswith(suffix) for suffix in SYNC_EXCLUDED_SUFFIXES):
        return False
    limit = SYNC_MAX_ENTRY_FILE_BYTES if path == "scripts/main.py" else SYNC_MAX_FILE_BYTES
    return size <= limit


def _script_entry_sync_limit(rel_path: str) -> int:
    path = rel_path.replace("\\", "/").lstrip("/")
    return SYNC_MAX_ENTRY_FILE_BYTES if path == "scripts/main.py" else SYNC_MAX_FILE_BYTES


def _skill_runtime_script_entry(skill_dir: Path) -> str:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return "scripts/main.py"
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        return _skill_runtime_script_entry_from_text(text, str(skill_md))
    except Exception as exc:  # noqa: BLE001
        logger.warning("解析 Skill runtime script_entry 失败 path={}: {}", skill_md, exc)
        return "scripts/main.py"


def _skill_runtime_script_entry_from_text(text: str, source: str = "SKILL.md") -> str:
    try:
        from app.skills.core.parser import skill_parser

        parsed = skill_parser.parse(text)
        runtime = _normalize_runtime_config(parsed.frontmatter or {})
        return str(runtime.get("script_entry") or "scripts/main.py").replace("\\", "/").lstrip("/")
    except Exception as exc:  # noqa: BLE001
        logger.warning("解析 Skill runtime script_entry 失败 source={}: {}", source, exc)
        return "scripts/main.py"


_SAFE_SYNC_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~/-]{0,199}$")


def _normalize_sync_ref(ref: str | None) -> str | None:
    clean = str(ref or "").strip()
    if not clean:
        return None
    if clean.startswith("-") or ".." in clean or not _SAFE_SYNC_REF_RE.match(clean):
        return None
    return clean


def _skill_source_error(skill_id: str) -> str | None:
    """Return a stable error code when a Skill cannot produce a sync bundle."""
    skill_dir = Path(settings.SKILL_REPO_PATH) / skill_id
    if not skill_dir.is_dir():
        return "SKILL_SOURCE_NOT_FOUND"

    script_entry = _skill_runtime_script_entry(skill_dir)
    entry_path = skill_dir / script_entry
    try:
        entry_resolved = entry_path.resolve(strict=True)
        entry_resolved.relative_to(skill_dir.resolve(strict=True))
    except Exception:
        return "SKILL_ENTRY_SCRIPT_NOT_SYNCABLE"
    try:
        entry_size = entry_resolved.stat().st_size
    except OSError:
        return "SKILL_ENTRY_SCRIPT_NOT_SYNCABLE"
    if not _should_sync_skill_file(script_entry, entry_size):
        return "SKILL_ENTRY_SCRIPT_NOT_SYNCABLE"

    try:
        for root, dirs, filenames in os.walk(skill_dir):
            dirs[:] = [item for item in dirs if item not in SYNC_EXCLUDED_DIRS]
            for filename in filenames:
                full = Path(root) / filename
                try:
                    rel = full.relative_to(skill_dir).as_posix()
                    size = full.stat().st_size
                except OSError:
                    continue
                if _should_sync_skill_file(rel, size):
                    return None
    except OSError:
        return "SKILL_SOURCE_NOT_FOUND"
    return "SKILL_SOURCE_EMPTY"


def _skill_source_error_for_ref(skill_id: str, ref: str | None) -> str | None:
    if _normalize_sync_ref(ref):
        _files, error = _build_skill_sync_bundle_from_git(skill_id, ref)
        return error
    return _skill_source_error(skill_id)


def _build_skill_sync_bundle(skill_dir: str) -> tuple[list[dict], str | None]:
    """Build the node sync bundle and detect entry-script omissions early."""
    skill_root = Path(skill_dir)
    if not skill_root.is_dir():
        return [], "SKILL_SOURCE_NOT_FOUND"

    script_entry = _skill_runtime_script_entry(skill_root)
    skipped_entry_reason = ""
    files: list[dict] = []
    try:
        for root, dirs, fnames in os.walk(skill_root):
            dirs[:] = [d for d in dirs if d not in SYNC_EXCLUDED_DIRS]
            for fname in fnames:
                full = Path(root) / fname
                rel = full.relative_to(skill_root).as_posix()
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                if not _should_sync_skill_file(rel, size):
                    if rel == script_entry:
                        skipped_entry_reason = f"{rel} exceeds sync limit {_script_entry_sync_limit(rel)} bytes"
                    continue
                try:
                    content = full.read_bytes()
                except OSError:
                    continue
                files.append({
                    "path": rel,
                    "content_b64": base64.b64encode(content).decode("ascii"),
                    "content": content.decode("utf-8", "ignore"),
                })
    except OSError:
        return [], "SKILL_SOURCE_NOT_FOUND"

    if skipped_entry_reason or not any(item.get("path") == script_entry for item in files):
        logger.error("Skill 同步入口脚本缺失 skill_dir={} script_entry={} reason={}", skill_dir, script_entry, skipped_entry_reason or "not bundled")
        return [], "SKILL_ENTRY_SCRIPT_NOT_SYNCABLE"
    if not files:
        return [], "SKILL_SOURCE_EMPTY"
    return files, None


def _build_skill_sync_bundle_from_git(skill_id: str, ref: str | None) -> tuple[list[dict], str | None]:
    """Build a sync bundle from an audited Git ref instead of the dirty worktree."""
    clean_ref = _normalize_sync_ref(ref)
    if not clean_ref:
        return [], "SKILL_SOURCE_NOT_FOUND"

    repo_path = Path(settings.SKILL_REPO_PATH)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "archive", "--format=tar", clean_ref, "--", skill_id],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("构建 Skill Git 同步包失败 skill={} ref={}: {}", skill_id, clean_ref, exc)
        return [], "SKILL_SOURCE_NOT_FOUND"
    if proc.returncode != 0:
        logger.warning(
            "构建 Skill Git 同步包失败 skill={} ref={} stderr={}",
            skill_id,
            clean_ref,
            proc.stderr.decode("utf-8", "ignore")[:300],
        )
        return [], "SKILL_SOURCE_NOT_FOUND"

    prefix = f"{skill_id.rstrip('/')}/"
    archived: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                name = member.name.replace("\\", "/").lstrip("/")
                if not name.startswith(prefix):
                    continue
                rel = name[len(prefix):]
                if not rel or rel.startswith("../") or "/../" in rel:
                    continue
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                archived[rel] = extracted.read()
    except Exception as exc:  # noqa: BLE001
        logger.warning("解析 Skill Git 同步包失败 skill={} ref={}: {}", skill_id, clean_ref, exc)
        return [], "SKILL_SOURCE_NOT_FOUND"

    skill_text = archived.get("SKILL.md", b"").decode("utf-8", "replace")
    script_entry = _skill_runtime_script_entry_from_text(skill_text, f"{skill_id}@{clean_ref}:SKILL.md")
    skipped_entry_reason = ""
    files: list[dict] = []
    for rel, content in sorted(archived.items()):
        size = len(content)
        if not _should_sync_skill_file(rel, size):
            if rel == script_entry:
                skipped_entry_reason = f"{rel} exceeds sync limit {_script_entry_sync_limit(rel)} bytes"
            continue
        files.append({
            "path": rel,
            "content_b64": base64.b64encode(content).decode("ascii"),
            "content": content.decode("utf-8", "ignore"),
        })

    if skipped_entry_reason or not any(item.get("path") == script_entry for item in files):
        logger.error(
            "Skill Git 同步入口脚本缺失 skill={} ref={} script_entry={} reason={}",
            skill_id,
            clean_ref,
            script_entry,
            skipped_entry_reason or "not bundled",
        )
        return [], "SKILL_ENTRY_SCRIPT_NOT_SYNCABLE"
    if not files:
        return [], "SKILL_SOURCE_EMPTY"
    return files, None


def _normalize_runtime_config(frontmatter: dict | None) -> dict:
    """Normalize Skill runtime config for node schedules."""
    frontmatter = frontmatter if isinstance(frontmatter, dict) else {}
    raw_runtime = frontmatter.get("runtime")
    runtime = dict(raw_runtime) if isinstance(raw_runtime, dict) else {}
    if isinstance(raw_runtime, str):
        runtime["backend"] = raw_runtime

    backend = str(
        runtime.get("backend")
        or frontmatter.get("execution_backend")
        or "bridge_script"
    ).strip().lower()
    if backend not in {"openclaw_agent", "hybrid", "bridge_script"}:
        backend = "bridge_script"

    fallback = runtime.get("fallback")
    if fallback is not None:
        fallback = str(fallback).strip().lower() or None
        if fallback not in {"bridge_script", "openclaw_agent", "hybrid"}:
            fallback = None

    timeout = runtime.get("timeout") or frontmatter.get("verify_script_timeout") or 300
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 300
    timeout = max(1, min(timeout, 5400))

    tools = runtime.get("tools") or []
    if not isinstance(tools, list):
        tools = []

    normalized = {
        "backend": backend,
        "fallback": fallback,
        "entry": str(runtime.get("entry") or "SKILL.md"),
        "script_entry": str(runtime.get("script_entry") or runtime.get("script_path") or "scripts/main.py"),
        "timeout": timeout,
        "tools": [str(item) for item in tools if item],
        "output_schema": str(runtime.get("output_schema") or "contract.json"),
        "policy_pack": str(runtime.get("policy_pack") or "policy_pack.yaml"),
    }
    if runtime.get("allow_center_fallback") is not None:
        normalized["allow_center_fallback"] = bool(runtime.get("allow_center_fallback"))
    return normalized


def _latest_skill_git_commit_full(skill_id: str) -> str | None:
    try:
        from app.skills.core.git_service import git_service

        logs = git_service.log(skill_id=skill_id, max_count=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 skill git 版本失败 skill={}: {}", skill_id, exc)
        return None
    if not logs:
        return None
    return str(logs[0].get("hash_full") or "").strip() or None


def _skill_git_commit_for_ref(ref: str | None) -> str | None:
    clean_ref = _normalize_sync_ref(ref)
    if not clean_ref:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(settings.SKILL_REPO_PATH), "rev-parse", f"{clean_ref}^{{commit}}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 Skill Git ref 失败 ref={}: {}", clean_ref, exc)
        return None
    if proc.returncode != 0:
        return None
    commit = proc.stdout.decode("utf-8", "ignore").strip()
    return commit if re.match(r"^[a-fA-F0-9]{40}$", commit) else None


def _skill_git_metadata(skill, *, version_ref: str | None = None) -> dict:
    commit = (
        _skill_git_commit_for_ref(version_ref)
        or _latest_skill_git_commit_full(skill.id)
        or (str(getattr(skill, "git_commit", "") or "").strip() or None)
    )
    metadata: dict[str, Any] = {}
    if commit:
        metadata["git_commit"] = commit[:8]
        metadata["git_commit_full"] = commit
    current_version = getattr(skill, "current_version", None)
    if current_version:
        metadata["current_version"] = current_version
    return metadata


def _stable_schedule_runtime(value: Any) -> str:
    return json.dumps(value or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _stable_schedule_model_context(value: Any) -> str:
    return json.dumps(value or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _runtime_config_for_schedule_tracking(runtime_config: dict | None, model_context: dict | None) -> dict | None:
    result = dict(runtime_config or {})
    if not model_context:
        result.pop("_model_context", None)
        return result
    stable_context = _stable_schedule_model_context(model_context)
    active_deployment = (
        model_context.get("active_model_deployment")
        if isinstance(model_context.get("active_model_deployment"), dict)
        else {}
    )
    result["_model_context"] = {
        "model_deployment_id": str(
            model_context.get("model_deployment_id")
            or active_deployment.get("model_deployment_id")
            or ""
        )[:80],
        "sha256": hashlib.sha256(stable_context.encode("utf-8")).hexdigest(),
    }
    return result


def _tracked_runtime_sdk_path() -> Path:
    return Path(__file__).resolve().parents[2] / "app" / "skill_runtime_sdk" / "skillforge_sdk.py"


def _should_bundle_runtime_sdk(files: list[dict]) -> bool:
    for item in files:
        path = str(item.get("path") or "")
        if path == "scripts/skillforge_sdk.py":
            return True
        if path.endswith(".py"):
            text = item.get("content")
            if isinstance(text, str) and ("skillforge_sdk" in text or "SkillForge(" in text):
                return True
    return False


def _maybe_append_runtime_sdk(files: list[dict]) -> list[dict]:
    if not _should_bundle_runtime_sdk(files):
        return files
    sdk_path = _tracked_runtime_sdk_path()
    if not sdk_path.exists():
        return files
    content = sdk_path.read_bytes()
    bundled = [item for item in files if str(item.get("path") or "") != "scripts/skillforge_sdk.py"]
    bundled.append({
        "path": "scripts/skillforge_sdk.py",
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content": content.decode("utf-8", "ignore"),
    })
    return bundled


def _repo_file(repo_relative_path: str) -> Path:
    return Path(__file__).resolve().parents[2] / repo_relative_path


def _append_repo_runtime_file(files: list[dict], *, source_rel_path: str, target_path: str) -> list[dict]:
    if any(str(item.get("path") or "") == target_path for item in files):
        return files
    source_path = _repo_file(source_rel_path)
    if not source_path.exists():
        return files
    content = source_path.read_bytes()
    bundled = list(files)
    bundled.append({
        "path": target_path,
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content": content.decode("utf-8", "ignore"),
    })
    return bundled


def _runtime_mcp_shared_files(files: list[dict]) -> list[dict]:
    shared: list[dict] = []
    texts = [
        item.get("content")
        for item in files
        if isinstance(item.get("content"), str) and str(item.get("path") or "").endswith(".py")
    ]
    joined = "\n".join(texts)
    for marker, source_paths in RUNTIME_MCP_BUNDLES.items():
        if marker not in joined:
            continue
        for source_rel_path in source_paths:
            shared = _append_repo_runtime_file(
                shared,
                source_rel_path=source_rel_path,
                target_path=f"scripts/{Path(source_rel_path).name}",
            )
    return shared


def _runtime_config_from_files(files: list[dict]) -> dict:
    for item in files:
        if str(item.get("path") or "") != "SKILL.md":
            continue
        text = item.get("content")
        if not isinstance(text, str):
            continue
        try:
            from app.skills.core.parser import skill_parser

            return _normalize_runtime_config(skill_parser.parse(text).frontmatter or {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("解析同步包 runtime 配置失败: {}", exc)
            break
    return _normalize_runtime_config({})


def _sync_file_sha256(files: list[dict], rel_path: str) -> str | None:
    target = str(rel_path or "").replace("\\", "/").lstrip("/")
    for item in files:
        if str(item.get("path") or "").replace("\\", "/").lstrip("/") != target:
            continue
        content_b64 = item.get("content_b64")
        if isinstance(content_b64, str) and content_b64:
            try:
                return hashlib.sha256(base64.b64decode(content_b64)).hexdigest()
            except Exception:  # noqa: BLE001
                return None
        content = item.get("content")
        if isinstance(content, str):
            return hashlib.sha256(content.encode("utf-8")).hexdigest()
    return None


class SkillSyncService:
    """审核通过后同步Skill到OpenClaw实例"""

    def skill_source_error(self, skill_id: str) -> str | None:
        """Return why a Skill cannot be synced from the current repo, if any."""
        return _skill_source_error(skill_id)

    def sync_job_auto_retry_skip_reason(self, job: Any) -> str | None:
        """Return a reason when bridge reconnect should not auto-retry a job."""
        error = str(getattr(job, "error", "") or "")
        if error in NON_RETRYABLE_SYNC_ERRORS:
            return error
        skill_id = str(getattr(job, "skill_id", "") or "")
        if not skill_id:
            return "SKILL_NOT_FOUND"
        source_error = _skill_source_error(skill_id)
        if source_error:
            return source_error
        attempt_count = int(getattr(job, "attempt_count", 0) or 0)
        if attempt_count >= AUTO_RETRY_MAX_ATTEMPTS:
            return f"AUTO_RETRY_LIMIT:{attempt_count}"
        return None

    async def _fail_sync_job_without_instance(
        self,
        *,
        job_id: int,
        skill_id: str,
        version_tag: str | None,
        review_id: int | None,
        trigger: str,
        actor_id: str | None,
        error_message: str,
        push_ok: bool,
        reload_reason: str,
    ) -> dict:
        """Persist a failed sync that never reached a concrete node target."""
        from app.database import async_session_factory
        from app.execution.models import SkillSyncAttempt, SkillSyncJob

        instance_results = [{
            "instance_id": None,
            "name": None,
            "agent_type": None,
            "ok": False,
            "error": error_message,
            "reload": {"ok": False, "status": "skipped", "reason": reload_reason},
        }]
        async with async_session_factory() as session:
            job = await session.get(SkillSyncJob, job_id)
            if job:
                job.status = "failed"
                job.error = error_message
                job.push_ok = push_ok
                job.updated_at = now_bjt()
                attempt = SkillSyncAttempt(
                    job_id=job_id,
                    skill_id=skill_id,
                    version_tag=version_tag,
                    review_id=review_id,
                    instance_id=None,
                    agent_type=None,
                    status="failed",
                    error=error_message,
                    trigger=trigger,
                    actor=actor_id,
                    attempt_count=job.attempt_count,
                    result={"ok": False, "error": error_message},
                    started_at=now_bjt(),
                    completed_at=now_bjt(),
                )
                session.add(attempt)
                await session.flush()
                instance_results[0]["attempt_id"] = int(attempt.id)
                await session.commit()

        await self._alert_sync_failure(
            skill_id,
            {"error": error_message, "push_ok": push_ok, "instances": instance_results},
        )
        return {
            "job_id": job_id,
            "all_ok": False,
            "push_ok": push_ok,
            "instances": instance_results,
        }

    async def sync_after_approval(
        self,
        skill_id: str,
        *,
        version_tag: str | None = None,
        review_id: int | None = None,
        target_instance_ids: list[str] | None = None,
        department: str | None = None,
        actor: Any | None = None,
    ) -> dict:
        """审核通过后触发：push 到 Skill Git 主线 + 按部门/指定终端推送 Skill 文件。

        完整流程：
        1. git push 到本地 Docker/Gitea canonical 分支
        2. 尝试 mirror 到外部 Skill Git 远端（非阻断）
        3. 按 Skill 部门 / 操作者部门 / 显式 instance_ids 选择目标终端
        4. 按 agent_type 分别处理：
           - aiclaw: 通过 bridge 推送 Skill 文件
           - hermes: 通过 HTTP API 推送 Skill 文件

        权限规则：
        - system_admin / admin / can_view_all：可指定任意 active 终端；未指定时按 Skill 部门。
        - 普通账号：只能推送到自己部门的 active 终端。
        """
        return await self.create_and_run_sync_job(
            skill_id,
            version_tag=version_tag,
            review_id=review_id,
            trigger="approval",
            actor=actor,
            target_instance_ids=target_instance_ids,
            department=department,
            push_git=True,
        )

    async def create_and_run_sync_job(
        self,
        skill_id: str,
        *,
        version_tag: str | None = None,
        review_id: int | None = None,
        trigger: str = "manual",
        actor: Any | None = None,
        target_instance_ids: list[str] | None = None,
        department: str | None = None,
        push_git: bool = False,
    ) -> dict:
        job_id = await self.create_sync_job(
            skill_id,
            version_tag=version_tag,
            review_id=review_id,
            trigger=trigger,
            actor=actor,
            target_instance_ids=target_instance_ids,
            department=department,
        )
        return await self.run_sync_job(job_id, push_git=push_git, actor=actor)

    async def create_sync_job(
        self,
        skill_id: str,
        *,
        version_tag: str | None = None,
        review_id: int | None = None,
        trigger: str = "manual",
        actor: Any | None = None,
        target_instance_ids: list[str] | None = None,
        department: str | None = None,
    ) -> int:
        from app.database import async_session_factory
        from app.execution.models import SkillSyncJob

        now = now_bjt()
        clean_target_ids = [item.strip() for item in (target_instance_ids or []) if item and item.strip()]
        job = SkillSyncJob(
            skill_id=skill_id,
            version_tag=version_tag,
            review_id=review_id,
            status="pending",
            trigger=trigger,
            actor=self._actor_id(actor),
            target_instance_ids=clean_target_ids or None,
            target_department=self._normalize_department(department) or None,
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        async with async_session_factory() as session:
            session.add(job)
            await session.commit()
            return int(job.id)

    async def run_sync_job(
        self,
        job_id: int,
        *,
        push_git: bool = False,
        target_instance_ids: list[str] | None = None,
        actor: Any | None = None,
    ) -> dict:
        from app.database import async_session_factory
        from app.execution.models import SkillSyncAttempt, SkillSyncJob

        async with async_session_factory() as session:
            job = await session.get(SkillSyncJob, job_id)
            if not job:
                raise AppError("SYNC_JOB_NOT_FOUND", 404)
            job.status = "running"
            job.error = None
            job.attempt_count = int(job.attempt_count or 0) + 1
            job.updated_at = now_bjt()
            if actor is not None:
                job.actor = self._actor_id(actor)
            await session.commit()

            skill_id = job.skill_id
            version_tag = job.version_tag
            review_id = job.review_id
            trigger = job.trigger
            actor_id = job.actor
            target_department = job.target_department
            clean_target_ids = (
                [item.strip() for item in target_instance_ids if item and item.strip()]
                if target_instance_ids is not None
                else list(job.target_instance_ids or [])
            )

        source_error = _skill_source_error_for_ref(skill_id, version_tag)
        if source_error:
            return await self._fail_sync_job_without_instance(
                job_id=job_id,
                skill_id=skill_id,
                version_tag=version_tag,
                review_id=review_id,
                trigger=trigger,
                actor_id=actor_id,
                error_message=source_error,
                push_ok=True,
                reload_reason=source_error.lower(),
            )

        # 1. push 到本地 Docker/Gitea canonical 分支。这个本地 Git 是 Skill 版本主线；
        # 外部远程只是 mirror，不能因为 mirror 不可用而阻断节点下发。
        push_ok = True
        if push_git:
            push_ok = await self._push_canonical_git()
            if not push_ok:
                return await self._fail_sync_job_without_instance(
                    job_id=job_id,
                    skill_id=skill_id,
                    version_tag=version_tag,
                    review_id=review_id,
                    trigger=trigger,
                    actor_id=actor_id,
                    error_message="GIT_PUSH_FAILED",
                    push_ok=False,
                    reload_reason="git_push_failed",
                )
            mirror_ok = await self._git_push()
            if not mirror_ok:
                await self._alert_sync_failure(
                    skill_id,
                    {
                        "mirror_push_ok": False,
                        "push_ok": True,
                        "error": "SKILL_GIT_MIRROR_PUSH_FAILED",
                    },
                )

        # 2. 自动推送 Skill。这里不再广播所有实例，避免跨部门泄露/误发布。
        actor_for_push = actor if (actor is not None and not isinstance(actor, str)) else self._job_actor_proxy(actor_id or (actor if isinstance(actor, str) else None))
        instance_results = await self.push_skill_to_targets(
            skill_id,
            target_instance_ids=clean_target_ids or None,
            department=target_department,
            actor=actor_for_push,
            job_id=job_id,
            version_tag=version_tag,
            review_id=review_id,
            trigger=trigger,
            actor_id=actor_id,
        )

        all_ok = push_ok
        if instance_results:
            all_ok = all_ok and all(r.get("ok", False) for r in instance_results)
        else:
            all_ok = False

        error_message = None
        if not all_ok:
            failed = [r for r in instance_results if isinstance(r, dict) and not r.get("ok")]
            if not push_ok:
                error_message = "GIT_PUSH_FAILED"
            elif failed:
                error_message = "; ".join(
                    str(item.get("error") or item.get("instance_id") or "SYNC_FAILED")
                    for item in failed[:5]
                )
            else:
                error_message = "NO_SYNC_TARGETS"

        async with async_session_factory() as session:
            job = await session.get(SkillSyncJob, job_id)
            if job:
                job.status = "succeeded" if all_ok else "failed"
                job.error = error_message
                job.push_ok = push_ok
                job.updated_at = now_bjt()
                # 文件缺失/无目标时也保留一条 pending/failed 记录，便于详情页解释原因。
                if not instance_results:
                    attempt = SkillSyncAttempt(
                        job_id=job_id,
                        skill_id=skill_id,
                        version_tag=version_tag,
                        review_id=review_id,
                        instance_id=None,
                        agent_type=None,
                        status="failed",
                        error=error_message or "NO_SYNC_TARGETS",
                        trigger=trigger,
                        actor=actor_id,
                        attempt_count=job.attempt_count,
                        result={"ok": False, "error": error_message or "NO_SYNC_TARGETS"},
                        started_at=now_bjt(),
                        completed_at=now_bjt(),
                    )
                    session.add(attempt)
                    await session.flush()
                    instance_results.append({
                        "instance_id": None,
                        "name": None,
                        "agent_type": None,
                        "ok": False,
                        "error": attempt.error,
                        "attempt_id": int(attempt.id),
                        "reload": {"ok": False, "status": "skipped", "reason": "no_sync_targets"},
                    })
                await session.commit()

        if not all_ok:
            await self._alert_sync_failure(skill_id, {"push_ok": push_ok, "instances": instance_results})

        return {
            "job_id": job_id,
            "all_ok": all_ok,
            "push_ok": push_ok,
            "instances": instance_results,
        }

    async def retry_sync_job(self, job_id: int, *, actor: Any | None = None) -> dict:
        from sqlalchemy import select

        from app.database import async_session_factory
        from app.execution.models import SkillSyncAttempt, SkillSyncJob

        async with async_session_factory() as session:
            job = await session.get(SkillSyncJob, job_id)
            if not job:
                raise AppError("SYNC_JOB_NOT_FOUND", 404)
            if job.status not in {"failed", "pending"}:
                raise AppError("INVALID_STATUS", 400, {"detail": "只有 failed/pending 的同步 job 可以重试"})
            retry_push_git = bool(job.push_ok is False or job.error == "GIT_PUSH_FAILED")
            rows = (
                await session.execute(
                    select(SkillSyncAttempt)
                    .where(
                        SkillSyncAttempt.job_id == job_id,
                        SkillSyncAttempt.status.in_(["failed", "pending"]),
                    )
                    .order_by(SkillSyncAttempt.created_at.desc())
                )
            ).scalars().all()
            retry_ids = []
            retry_platform_fallback = False
            for row in rows:
                if isinstance(row.result, dict) and row.result.get("target_scope") == "platform_default":
                    retry_platform_fallback = True
                if row.instance_id and row.instance_id not in retry_ids:
                    retry_ids.append(row.instance_id)

        return await self.run_sync_job(
            job_id,
            push_git=retry_push_git,
            target_instance_ids=None if retry_platform_fallback else (retry_ids or None),
            actor=actor,
        )

    async def get_recent_sync_status(
        self,
        *,
        skill_id: str | None = None,
        review_id: int | None = None,
        limit: int = 10,
    ) -> dict:
        from sqlalchemy import select

        from app.database import async_session_factory
        from app.execution.models import SkillSyncAttempt, SkillSyncJob

        if not skill_id and review_id is None:
            raise AppError("PARAM_INVALID", 400, {"detail": "skill_id 或 review_id 至少提供一个"})

        async with async_session_factory() as session:
            stmt = select(SkillSyncJob)
            if skill_id:
                stmt = stmt.where(SkillSyncJob.skill_id == skill_id)
            if review_id is not None:
                stmt = stmt.where(SkillSyncJob.review_id == review_id)
            jobs = (
                await session.execute(
                    stmt.order_by(SkillSyncJob.created_at.desc()).limit(limit)
                )
            ).scalars().all()

            job_ids = [job.id for job in jobs]
            attempts_by_job: dict[int, list[SkillSyncAttempt]] = {int(job.id): [] for job in jobs}
            if job_ids:
                attempts = (
                    await session.execute(
                        select(SkillSyncAttempt)
                        .where(SkillSyncAttempt.job_id.in_(job_ids))
                        .order_by(SkillSyncAttempt.created_at.desc())
                    )
                ).scalars().all()
                for attempt in attempts:
                    attempts_by_job.setdefault(int(attempt.job_id), []).append(attempt)

        return {
            "items": [
                self._serialize_sync_job(job, attempts_by_job.get(int(job.id), []))
                for job in jobs
            ]
        }

    async def commit_and_sync(
        self,
        *,
        skill_id: str,
        message: str,
        author: str = "SkillForge",
        actor: Any | None = None,
        target_instance_ids: list[str] | None = None,
        department: str | None = None,
    ) -> dict:
        """提交 skill 目录并按目标终端同步。

        旧代码在 workbench finalize 里调用了这个方法，但服务没有实现，导致创建完成后
        发布阶段只能吞 warning。这里补齐，确保“编写完成 → 提交 → 部门终端同步”是真路径。
        """
        from app.skills.core.git_service import git_service

        await asyncio.to_thread(git_service.commit, skill_id=skill_id, message=message, author=author)
        return await self.sync_after_approval(
            skill_id,
            target_instance_ids=target_instance_ids,
            department=department,
            actor=actor,
        )

    async def list_sync_targets(
        self,
        skill_id: str,
        *,
        actor: Any | None = None,
    ) -> list[dict]:
        """返回当前用户可见/可推送的终端列表，用于前端选择目标设备。"""
        from sqlalchemy import select

        from app.aiclaw.bridge_registry import bridge_registry
        from app.database import async_session_factory
        from app.execution.models import OpenClawInstance
        from app.skills.core.models import Skill

        async with async_session_factory() as session:
            skill = await session.get(Skill, skill_id)
            if not skill:
                raise AppError("SKILL_NOT_FOUND", 404)
            rows = (
                await session.execute(
                    select(OpenClawInstance)
                    .where(OpenClawInstance.is_active == True)  # noqa: E712
                    .order_by(OpenClawInstance.name.asc())
                )
            ).scalars().all()

        global_scope = self._is_global_operator(actor)
        actor_department = self._normalize_department(getattr(actor, "department", None))
        skill_department = self._normalize_department(getattr(skill, "department", None))
        department_target_ids = {
            inst.id
            for inst in rows
            if (
                skill_department
                and self._normalize_department(getattr(inst, "department", None)) == skill_department
                and _is_skill_runtime_agent(inst)
            )
        }
        platform_fallback_enabled = await is_platform_node_fallback_enabled()
        use_platform_fallback = bool(platform_fallback_enabled and skill_department and not department_target_ids)
        explicit_platform_default_ids = {
            inst.id
            for inst in rows
            if bool(getattr(inst, "is_platform_default", False)) and _is_skill_runtime_agent(inst)
        }
        platform_default_ids = explicit_platform_default_ids or {
            inst.id
            for inst in rows
            if self._is_platform_default_instance(inst) and _is_skill_runtime_agent(inst)
        }

        items: list[dict] = []
        for inst in rows:
            inst_department = self._normalize_department(getattr(inst, "department", None))
            runtime_eligible = _is_skill_runtime_agent(inst)
            is_platform_fallback = use_platform_fallback and inst.id in platform_default_ids
            selectable = (
                runtime_eligible
                and (
                    global_scope
                    or bool(actor_department and inst_department == actor_department)
                    or is_platform_fallback
                )
            )
            auto_target = (
                bool(runtime_eligible and skill_department and inst_department == skill_department)
                or is_platform_fallback
            )
            reason = None
            if not runtime_eligible:
                reason = _agent_purpose_reject_reason(inst)
            elif not selectable:
                reason = "只能推送到当前账号所在部门的终端"
            if is_platform_fallback:
                reason = "当前部门没有终端，默认使用平台内置终端兜底"
            items.append({
                "id": inst.id,
                "name": inst.name,
                "department": inst.department,
                "agent_type": getattr(inst, "agent_type", "aiclaw") or "aiclaw",
                "agent_purpose": _agent_purpose(inst),
                "runtime_eligible": runtime_eligible,
                "is_platform_default": bool(getattr(inst, "is_platform_default", False)),
                "fallback_target": is_platform_fallback,
                "target_scope": "platform_default" if is_platform_fallback else ("department" if auto_target else None),
                "bridge_online": bridge_registry.is_online(inst.id),
                "gateway_kind": getattr(inst, "bridge_gateway_kind", None),
                "skills_dir": getattr(inst, "bridge_skills_dir", None),
                "selectable": selectable,
                "auto_target": auto_target,
                "reason": reason,
            })
        return items

    async def push_skill_to_targets(
        self,
        skill_id: str,
        *,
        target_instance_ids: list[str] | None = None,
        department: str | None = None,
        actor: Any | None = None,
        job_id: int | None = None,
        version_tag: str | None = None,
        review_id: int | None = None,
        trigger: str = "manual",
        actor_id: str | None = None,
    ) -> list[dict]:
        """把 Skill 推送到目标终端。

        未显式指定 instance_ids 时，目标部门优先级：
        1. department 参数
        2. Skill.department
        3. actor.department
        4. 若目标部门没有 active 终端，回退到平台内置终端
        """
        import os as _os

        from sqlalchemy import desc, select
        from app.database import async_session_factory
        from app.execution.models import OpenClawInstance, SkillSyncAttempt, SkillSyncJob
        from app.skills.core.models import Skill

        results = []
        clean_version_tag = _normalize_sync_ref(version_tag)
        skill_dir = _os.path.join(settings.SKILL_REPO_PATH, skill_id)
        if not clean_version_tag and not _os.path.exists(skill_dir):
            return results

        # 收集 Skill 文件。运行入口脚本必须进入同步包；否则节点会在下一次定时运行时报
        # script not found，比同步阶段失败更难排查。
        if clean_version_tag:
            files, bundle_error = _build_skill_sync_bundle_from_git(skill_id, clean_version_tag)
        else:
            files, bundle_error = _build_skill_sync_bundle(skill_dir)
        if bundle_error:
            logger.error("Skill 同步包构建失败 skill={} error={}", skill_id, bundle_error)
            return results

        files = _maybe_append_runtime_sdk(files)
        shared_files = _runtime_mcp_shared_files(files)

        if not files:
            return results

        async with async_session_factory() as session:
            skill = await session.get(Skill, skill_id)
            if not skill:
                raise AppError("SKILL_NOT_FOUND", 404)
            git_metadata = _skill_git_metadata(skill, version_ref=clean_version_tag)

            global_scope = self._is_global_operator(actor)
            actor_department = self._normalize_department(getattr(actor, "department", None))
            skill_department = self._normalize_department(getattr(skill, "department", None))
            target_department = self._normalize_department(department) or skill_department or actor_department
            clean_target_ids = [item.strip() for item in (target_instance_ids or []) if item and item.strip()]
            platform_fallback_ids: set[str] = set()
            departments_to_invalidate: set[str] = {target_department} if target_department else set()

            stmt = select(OpenClawInstance).where(OpenClawInstance.is_active == True)  # noqa: E712
            if clean_target_ids:
                stmt = stmt.where(OpenClawInstance.id.in_(clean_target_ids))
            elif target_department:
                stmt = stmt.where(OpenClawInstance.department == target_department)

            queried_instances = (await session.execute(stmt)).scalars().all()
            ineligible_instances = [
                inst for inst in queried_instances if not _is_skill_runtime_agent(inst)
            ]
            instances = [
                inst for inst in queried_instances if _is_skill_runtime_agent(inst)
            ]
            platform_fallback_enabled = await is_platform_node_fallback_enabled(session)
            if platform_fallback_enabled and not clean_target_ids and target_department and not instances:
                platform_default_stmt = (
                    select(OpenClawInstance)
                    .where(OpenClawInstance.is_active == True)  # noqa: E712
                    .where(OpenClawInstance.is_platform_default == True)  # noqa: E712
                    .order_by(desc(OpenClawInstance.is_platform_default), OpenClawInstance.name.asc())
                )
                instances = [
                    inst
                    for inst in (await session.execute(platform_default_stmt)).scalars().all()
                    if _is_skill_runtime_agent(inst)
                ]
                if not instances:
                    fallback_stmt = (
                        select(OpenClawInstance)
                        .where(OpenClawInstance.is_active == True)  # noqa: E712
                        .where(OpenClawInstance.department.in_(PLATFORM_FALLBACK_DEPARTMENTS))
                        .order_by(OpenClawInstance.name.asc())
                    )
                    instances = [
                        inst
                        for inst in (await session.execute(fallback_stmt)).scalars().all()
                        if _is_skill_runtime_agent(inst)
                    ]
                platform_fallback_ids = {inst.id for inst in instances}

            if clean_target_ids:
                for inst in ineligible_instances:
                    attempt = None
                    if job_id is not None:
                        job = await session.get(SkillSyncJob, job_id)
                        attempt = SkillSyncAttempt(
                            job_id=job_id,
                            skill_id=skill_id,
                            version_tag=version_tag,
                            review_id=review_id,
                            instance_id=inst.id,
                            agent_type=getattr(inst, "agent_type", "aiclaw") or "aiclaw",
                            status="failed",
                            error="AGENT_PURPOSE_NOT_SKILL_RUNTIME",
                            trigger=trigger,
                            actor=actor_id or self._actor_id(actor),
                            attempt_count=int(getattr(job, "attempt_count", 1) or 1),
                            started_at=now_bjt(),
                            completed_at=now_bjt(),
                        )
                        session.add(attempt)
                        await session.flush()
                    result = self._sync_result(
                        inst,
                        ok=False,
                        error="AGENT_PURPOSE_NOT_SKILL_RUNTIME",
                        reload={
                            "ok": False,
                            "status": "skipped",
                            "reason": "agent_purpose_not_skill_runtime",
                        },
                    )
                    result.update(git_metadata)
                    result["reason"] = _agent_purpose_reject_reason(inst)
                    if attempt is not None:
                        result["attempt_id"] = int(attempt.id)
                        attempt.result = result
                    results.append(result)

            for inst in instances:
                attempt = None
                if job_id is not None:
                    job = await session.get(SkillSyncJob, job_id)
                    attempt = SkillSyncAttempt(
                        job_id=job_id,
                        skill_id=skill_id,
                        version_tag=version_tag,
                        review_id=review_id,
                        instance_id=inst.id,
                        agent_type=getattr(inst, "agent_type", "aiclaw") or "aiclaw",
                        status="running",
                        trigger=trigger,
                        actor=actor_id or self._actor_id(actor),
                        attempt_count=int(getattr(job, "attempt_count", 1) or 1),
                        started_at=now_bjt(),
                    )
                    session.add(attempt)
                    await session.flush()
                inst_department = self._normalize_department(inst.department)
                if inst_department:
                    departments_to_invalidate.add(inst_department)
                if not global_scope and actor is not None:
                    is_platform_fallback = inst.id in platform_fallback_ids
                    dept_ok = await self._department_lineage_match(actor_department, inst_department)
                    if not is_platform_fallback and (not actor_department or not dept_ok):
                        result = self._sync_result(
                            inst,
                            ok=False,
                            error="AUTH_DEPARTMENT_DENIED",
                            reload={"ok": False, "status": "skipped", "reason": "permission_denied"},
                        )
                        result.update(git_metadata)
                        if attempt is not None:
                            result["attempt_id"] = int(attempt.id)
                            attempt.status = "failed"
                            attempt.error = result["error"]
                            attempt.result = result
                            attempt.completed_at = now_bjt()
                        results.append(result)
                        continue

                result = await self._push_files_to_instance(skill_id, files, inst, shared_files=shared_files)
                result.update(git_metadata)
                if inst.id in platform_fallback_ids:
                    result["fallback_target"] = True
                    result["target_scope"] = "platform_default"
                    result["fallback_reason"] = f"部门 {target_department} 没有可用 Agent 终端，使用平台内置终端"
                if attempt is not None:
                    result["attempt_id"] = int(attempt.id)
                    attempt.status = "succeeded" if result.get("ok") else "failed"
                    attempt.error = result.get("error")
                    attempt.result = result
                    attempt.completed_at = now_bjt()
                results.append(result)
                inst.last_sync_at = now_bjt()
                inst.last_sync_ok = bool(result.get("ok"))
                if not result.get("ok"):
                    # 离线设备入重试队列（通过钉钉告警提醒管理员手动重推）
                    try:
                        await self._alert_sync_failure(
                            skill_id,
                            {
                                "error": f"实例 {inst.id} 推送失败: {result.get('error')}",
                                "push_ok": True,
                                "instance_id": inst.id,
                            },
                        )
                    except Exception as alert_err:
                        logger.warning("sync 告警投递失败 skill={} inst={}: {}", skill_id, inst.id, alert_err)

            if clean_target_ids:
                found = {inst.id for inst in instances} | {inst.id for inst in ineligible_instances}
                for missing in sorted(set(clean_target_ids) - found):
                    missing_result = {
                        "instance_id": missing,
                        "name": None,
                        "agent_type": None,
                        "ok": False,
                        "error": "INSTANCE_NOT_FOUND_OR_INACTIVE",
                        "reload": {"ok": False, "status": "skipped", "reason": "instance_not_found_or_inactive"},
                    }
                    missing_result.update(git_metadata)
                    if job_id is not None:
                        job = await session.get(SkillSyncJob, job_id)
                        attempt = SkillSyncAttempt(
                            job_id=job_id,
                            skill_id=skill_id,
                            version_tag=version_tag,
                            review_id=review_id,
                            instance_id=missing,
                            agent_type=None,
                            status="failed",
                            error=missing_result["error"],
                            trigger=trigger,
                            actor=actor_id or self._actor_id(actor),
                            attempt_count=int(getattr(job, "attempt_count", 1) or 1),
                            result=missing_result,
                            started_at=now_bjt(),
                            completed_at=now_bjt(),
                        )
                        session.add(attempt)
                        await session.flush()
                        missing_result["attempt_id"] = int(attempt.id)
                    results.append(missing_result)

            await session.commit()

        await self._invalidate_tasktree_after_sync(departments_to_invalidate)

        # 同步后推送定时配置到节点
        if skill and getattr(skill, 'trigger_type', None) == 'cron' and getattr(skill, 'trigger_expression', None):
            try:
                inst_ids = [r["instance_id"] for r in results if r.get("ok") or r.get("install", {}).get("ok")]
                if inst_ids:
                    await self.push_schedules_to_targets(
                        skill_ids=[skill_id],
                        target_instance_ids=inst_ids,
                    )
            except Exception as exc:
                logger.warning("同步后推送定时配置失败 skill={}: {}", skill_id, exc)

        return results

    async def _push_files_to_instance(
        self,
        skill_id: str,
        files: list[dict],
        inst,
        *,
        shared_files: list[dict] | None = None,
    ) -> dict:
        agent_type = getattr(inst, "agent_type", "aiclaw") or "aiclaw"
        shared_files = shared_files or []
        try:
            if agent_type == "hermes":
                from app.aiclaw.hermes_client import HermesClient
                hermes = HermesClient(base_url=inst.gateway_url, api_key=inst.auth_token or "")
                # Hermes 用 SKILL.hermes.md 替代 SKILL.md
                hermes_files = []
                for f in files:
                    if f["path"] == "SKILL.hermes.md":
                        hermes_files.append({"path": "SKILL.md", "content": f["content"]})
                    elif f["path"] != "SKILL.md":
                        hermes_files.append({"path": f["path"], "content": f["content"]})
                r = await hermes.install_skill(skill_id, hermes_files)
                install_ok = self._install_result_ok(r)
                return self._sync_result(
                    inst,
                    ok=install_ok,
                    error=None if install_ok else "INSTALL_SKILL_FAILED",
                    reload={"ok": False, "status": "unsupported", "reason": "hermes_install_has_no_reload_rpc"},
                    result=r,
                )

            from app.aiclaw.client import AIClawClient

            bridge_files = [{"path": f["path"], "content_b64": f["content_b64"]} for f in files]
            bridge_shared_files = [
                {"path": f["path"], "content_b64": f["content_b64"]}
                for f in shared_files
            ]
            runtime_config = _runtime_config_from_files(files)
            client = AIClawClient(inst.id)
            target_dir = str(getattr(inst, "bridge_skills_dir", "") or "").strip() or None
            if bridge_shared_files:
                r = await client.install_skill(
                    skill_id,
                    bridge_files,
                    target_dir=target_dir,
                    shared_files=bridge_shared_files,
                )
            else:
                r = await client.install_skill(skill_id, bridge_files, target_dir=target_dir)
            install_ok = self._install_result_ok(r)
            sync_result = r if isinstance(r, dict) else {"result": r}
            if target_dir:
                sync_result = {**sync_result, "target_dir": target_dir}
            verify_result: dict | None = None
            verify_ok = True
            script_entry = str(runtime_config.get("script_entry") or "scripts/main.py").replace("\\", "/").lstrip("/")
            expected_hash = _sync_file_sha256(bridge_files, script_entry)
            if install_ok and expected_hash:
                verify_ok = False
                try:
                    device = await client.read_skill_from_device(skill_id, target_dir=target_dir)
                    actual_hash = _sync_file_sha256(device.get("files") or [], script_entry)
                    verify_ok = actual_hash == expected_hash
                    verify_result = {
                        "ok": verify_ok,
                        "path": script_entry,
                        "expected_sha256": expected_hash,
                        "actual_sha256": actual_hash,
                        "target_dir": target_dir,
                    }
                except Exception as verify_err:  # noqa: BLE001
                    verify_result = {
                        "ok": False,
                        "path": script_entry,
                        "expected_sha256": expected_hash,
                        "target_dir": target_dir,
                        "error": str(getattr(verify_err, "detail", None) or verify_err)[:300],
                    }
            if verify_result is not None:
                sync_result = {**sync_result, "verify": verify_result}
            register_required = runtime_config.get("backend") in {"openclaw_agent", "hybrid"}
            register_result: dict | None = None
            register_ok = True
            if install_ok and verify_ok and register_required:
                try:
                    register_result = self._normalize_register_result(
                        await client.register_agent_skill(skill_id, runtime=runtime_config)
                    )
                except Exception as register_err:  # noqa: BLE001
                    register_result = self._register_error_result(register_err)
                register_ok = bool(register_result.get("ok"))
                if not register_ok and runtime_config.get("fallback") == "bridge_script":
                    register_result = {
                        **register_result,
                        "non_blocking": True,
                        "message": "节点已接收完整 Skill，但当前 OpenClaw 未支持 skill.register；定时执行会降级 bridge_script",
                    }
                    register_ok = True
                sync_result = {**sync_result, "register": register_result}
            reload_result = {"ok": False, "status": "skipped", "reason": "install_failed"}
            reload_ok = False
            try:
                if install_ok and verify_ok:
                    reload_result = self._normalize_reload_result(await client.reload_skills())
                    reload_ok = bool(reload_result.get("ok"))
            except Exception as reload_err:  # noqa: BLE001
                reload_result = self._reload_error_result(reload_err)
            reload_non_blocking = self._reload_non_blocking(reload_result)
            if install_ok and reload_non_blocking:
                reload_result = {
                    **reload_result,
                    "ok": True,
                    "non_blocking": True,
                    "message": "节点已接收 Skill 文件，但当前 Agent 不支持热重载；会在节点下次刷新/重启后生效",
                }
            ok = install_ok and verify_ok and register_ok and (reload_ok or reload_non_blocking)
            return self._sync_result(
                inst,
                ok=ok,
                error=None if ok else (
                    "INSTALL_VERIFY_FAILED"
                    if install_ok and not verify_ok
                    else
                    "REGISTER_SKILL_FAILED"
                    if install_ok and not register_ok
                    else self._sync_error(install_ok, reload_result)
                ),
                reload=reload_result,
                result=sync_result,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"推送 Skill {skill_id} 到实例 {inst.id} ({agent_type}) 失败: {e}")
            return self._sync_result(
                inst,
                ok=False,
                error=str(e)[:200],
                reload={"ok": False, "status": "skipped", "reason": "install_exception"},
            )

    async def _invalidate_tasktree_after_sync(self, departments: set[str]) -> None:
        try:
            from app.tasktree.service import invalidate_tasktree

            cleaned = [department for department in departments if department]
            if not cleaned:
                await invalidate_tasktree(None)
                return
            for department in cleaned:
                await invalidate_tasktree(department)
        except Exception as exc:  # noqa: BLE001
            logger.warning("同步后任务树缓存失效失败 departments={}: {}", sorted(departments), exc)

    async def push_schedules_to_targets(
        self,
        skill_ids: list[str] | None = None,
        target_instance_ids: list[str] | None = None,
        department: str | None = None,
    ) -> list[dict]:
        """将定时执行配置推送到目标节点 bridge。

        1. 查询 active + trigger_type=cron + trigger_expression IS NOT NULL 的 Skill
        2. 构建 schedule 列表
        3. 逐实例推送 sync_schedules
        4. 记录 NodeScheduleConfig
        """
        from sqlalchemy import select

        from app.aiclaw.client import AIClawClient
        from app.database import async_session_factory
        from app.execution.models import NodeScheduleConfig, OpenClawInstance, SkillSyncAttempt
        from app.skills.core.models import Skill

        results: list[dict] = []

        async with async_session_factory() as session:
            requested_skill_ids = {item for item in (skill_ids or []) if item}

            # ── 查询可下发 cron skill ──
            # sync_schedules 是桥端完整快照，不能只下发本次变更的单个 Skill，
            # 否则会把同一节点上其它已同步定时任务覆盖掉。
            skill_stmt = (
                select(Skill)
                .where(Skill.status.in_(["active", "archived"]))
                .where(Skill.trigger_type == "cron")
                .where(Skill.trigger_expression.isnot(None))
            )
            if department:
                skill_stmt = skill_stmt.where(Skill.department == department)

            skills = (await session.execute(skill_stmt)).scalars().all()
            skill_by_id = {s.id: s for s in skills}

            runtime_by_skill_id: dict[str, dict] = {}
            schedule_by_skill_id: dict[str, dict] = {}
            for s in skills:
                frontmatter: dict = {}
                skill_md_path = Path(settings.SKILL_REPO_PATH) / s.id / "SKILL.md"
                if skill_md_path.exists():
                    try:
                        from app.skills.core.parser import skill_parser

                        frontmatter = skill_parser.parse(skill_md_path.read_text(encoding="utf-8")).frontmatter or {}
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("解析 Skill runtime 配置失败 skill={}: {}", s.id, exc)
                runtime = _normalize_runtime_config(frontmatter)
                commit = _latest_skill_git_commit_full(s.id) or (
                    str(getattr(s, "git_commit", "") or "").strip() or None
                )
                runtime_by_skill_id[s.id] = runtime
                schedule_by_skill_id[s.id] = {
                    "skill_id": s.id,
                    "cron": s.trigger_expression,
                    "timezone": settings.SCHEDULER_TIMEZONE,
                    "enabled": True,
                    "runtime": runtime,
                    "skill_git_commit_full": commit,
                }

            config_version = int(now_bjt().timestamp())
            platform_fallback_enabled = await is_platform_node_fallback_enabled(session)

            # ── 查询目标实例 ──
            inst_stmt = select(OpenClawInstance).where(OpenClawInstance.is_active == True)  # noqa: E712
            if target_instance_ids:
                inst_stmt = inst_stmt.where(OpenClawInstance.id.in_(target_instance_ids))
            elif department:
                inst_stmt = inst_stmt.where(OpenClawInstance.department == department)

            instances = (await session.execute(inst_stmt)).scalars().all()
            platform_fingerprints = await active_platform_fingerprints(session)

            non_platform_target_skill_ids = set((await session.execute(
                select(NodeScheduleConfig.skill_id)
                .join(OpenClawInstance, OpenClawInstance.id == NodeScheduleConfig.instance_id)
                .where(OpenClawInstance.is_active == True)  # noqa: E712
                .where(OpenClawInstance.is_platform_default == False)  # noqa: E712
                .group_by(NodeScheduleConfig.skill_id)
            )).scalars().all())
            non_platform_target_skill_ids.update((await session.execute(
                select(SkillSyncAttempt.skill_id)
                .join(OpenClawInstance, OpenClawInstance.id == SkillSyncAttempt.instance_id)
                .where(SkillSyncAttempt.status == "succeeded")
                .where(OpenClawInstance.is_active == True)  # noqa: E712
                .where(OpenClawInstance.is_platform_default == False)  # noqa: E712
                .group_by(SkillSyncAttempt.skill_id)
            )).scalars().all())

            # ── 逐实例推送 ──
            for inst in instances:
                existing_configs = (await session.execute(
                    select(NodeScheduleConfig)
                    .where(NodeScheduleConfig.instance_id == inst.id)
                )).scalars().all()
                synced_skill_ids = set((await session.execute(
                    select(SkillSyncAttempt.skill_id)
                    .where(SkillSyncAttempt.instance_id == inst.id)
                    .where(SkillSyncAttempt.status == "succeeded")
                    .group_by(SkillSyncAttempt.skill_id)
                )).scalars().all())

                instance_skill_ids = set(requested_skill_ids)
                instance_skill_ids.update(synced_skill_ids)
                # NodeScheduleConfig 是节点当前 schedule 快照的一部分；只要对应
                # skill 仍是可运行 cron，就必须继续下发，避免增量推送覆盖掉旧配置。
                for cfg in existing_configs:
                    if cfg.skill_id in schedule_by_skill_id:
                        instance_skill_ids.add(cfg.skill_id)
                if not requested_skill_ids and not target_instance_ids:
                    instance_skill_ids.update(skill_by_id)
                if getattr(inst, "is_platform_default", False):
                    # 平台兜底节点只在没有真实业务终端承接该 Skill 时跑定时任务；
                    # 一旦已有非平台节点同步/配置过，就避免平台节点重复执行造成误报。
                    instance_skill_ids.difference_update(non_platform_target_skill_ids)
                    if not platform_fallback_enabled and not target_instance_ids:
                        instance_skill_ids.clear()
                purpose_warning = None
                if not _is_skill_runtime_agent(inst):
                    purpose_warning = (
                        f"AGENT_PURPOSE_NOT_SKILL_RUNTIME: {_agent_purpose_reject_reason(inst)}，"
                        "已清空该节点定时快照"
                    )
                    logger.warning("实例 {} 不承载 Skill Runtime，已清空定时快照", inst.id)
                    instance_skill_ids.clear()
                placement_conflict = bridge_has_platform_fingerprint_conflict(inst, platform_fingerprints)
                placement_warning = None
                if placement_conflict:
                    placement_warning = (
                        f"{BRIDGE_PLACEMENT_CONFLICT}: 非平台节点 {inst.id} 与平台主节点同机，"
                        "已清空该节点定时快照，请在真实节点机器重新安装 Bridge"
                    )
                    logger.warning(placement_warning)
                    instance_skill_ids.clear()

                client = AIClawClient(inst.id)
                local_skill_ids: set[str] | None = None
                local_skills_dir: str | None = None
                local_probe_error: str | None = None
                if not placement_conflict and not purpose_warning:
                    try:
                        local_result = await client.list_local_skills()
                        if isinstance(local_result, dict):
                            raw_local_skills = local_result.get("skills")
                            local_skills_dir = str(local_result.get("dir") or "") or None
                            if isinstance(raw_local_skills, list):
                                local_skill_ids = {
                                    str(item)
                                    for item in raw_local_skills
                                    if str(item or "").strip()
                                }
                            else:
                                local_probe_error = "NODE_SKILL_LIST_INVALID"
                        else:
                            local_probe_error = "NODE_SKILL_LIST_INVALID"
                    except Exception as exc:  # noqa: BLE001
                        local_probe_error = str(exc)[:200]
                        logger.warning("读取实例 {} 本地 Skill 清单失败: {}", inst.id, exc)

                candidate_skill_ids = {
                    sid for sid in instance_skill_ids if sid in schedule_by_skill_id
                }
                missing_local_skill_ids: list[str] = []
                if local_skill_ids is not None:
                    missing_local_skill_ids = sorted(candidate_skill_ids - local_skill_ids)
                    if missing_local_skill_ids:
                        logger.warning(
                            "实例 {} 本地缺少 Skill {}，已从定时快照移除",
                            inst.id,
                            ",".join(missing_local_skill_ids),
                        )
                    instance_skill_ids = candidate_skill_ids & local_skill_ids
                elif local_probe_error:
                    instance_skill_ids.clear()

                schedules = []
                model_context_by_skill_id: dict[str, dict | None] = {}
                for sid in sorted(instance_skill_ids):
                    if sid not in schedule_by_skill_id:
                        continue
                    schedule = dict(schedule_by_skill_id[sid])
                    skill = skill_by_id.get(sid)
                    model_context = None
                    if skill is not None:
                        model_context = await active_model_context_for_skill(
                            session,
                            skill_id=sid,
                            skill_department=getattr(skill, "department", None),
                            target_instance=inst,
                            include_artifact_uri=True,
                        )
                    if model_context:
                        schedule["model_context"] = model_context
                    model_context_by_skill_id[sid] = model_context
                    schedules.append(schedule)

                snapshot_ok = False
                ok = False
                error: str | None = None
                sync_result: dict[str, Any] = {}
                try:
                    from app.execution.router import issue_node_submit_token

                    raw_sync_result = await client.sync_schedules(
                        schedules=schedules,
                        submit_token=issue_node_submit_token(inst.id),
                        submit_url="",
                        config_version=config_version,
                    )
                    sync_result = raw_sync_result if isinstance(raw_sync_result, dict) else {}
                    snapshot_ok = True
                    ok = True
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)[:200]
                    logger.warning("推送定时配置到实例 {} 失败: {}", inst.id, exc)
                if snapshot_ok and placement_warning:
                    error = placement_warning
                if snapshot_ok and purpose_warning:
                    ok = False
                    error = purpose_warning
                if snapshot_ok and local_probe_error:
                    ok = False
                    error = f"NODE_SKILL_LIST_FAILED: {local_probe_error}"
                if snapshot_ok and missing_local_skill_ids:
                    ok = False
                    missing_text = ",".join(missing_local_skill_ids)
                    error = f"NODE_SKILL_MISSING: 节点本地未安装 Skill，已从定时快照移除: {missing_text}"

                pushed_skill_ids = {sched["skill_id"] for sched in schedules}
                if snapshot_ok:
                    for existing_config in existing_configs:
                        if existing_config.skill_id not in pushed_skill_ids:
                            await session.delete(existing_config)

                # upsert NodeScheduleConfig
                for sched in schedules:
                    existing_stmt = (
                        select(NodeScheduleConfig)
                        .where(NodeScheduleConfig.instance_id == inst.id)
                        .where(NodeScheduleConfig.skill_id == sched["skill_id"])
                    )
                    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
                    if existing:
                        runtime_config = runtime_by_skill_id.get(sched["skill_id"])
                        runtime_backend = (runtime_config or {}).get("backend")
                        model_context = model_context_by_skill_id.get(sched["skill_id"])
                        runtime_tracking = _runtime_config_for_schedule_tracking(runtime_config, model_context)
                        content_changed = (
                            existing.cron_expression != sched["cron"]
                            or existing.runtime_backend != runtime_backend
                            or _stable_schedule_runtime(existing.runtime_config) != _stable_schedule_runtime(runtime_tracking)
                        )
                        should_refresh_push_marker = (
                            content_changed
                            or existing.ack_ok is not True
                            or not snapshot_ok
                        )
                        existing.cron_expression = sched["cron"]
                        if should_refresh_push_marker:
                            existing.config_version = config_version
                            existing.pushed_at = now_bjt()
                        existing.ack_ok = snapshot_ok
                        existing.runtime_backend = runtime_backend
                        existing.runtime_config = runtime_tracking
                    else:
                        runtime_config = runtime_by_skill_id.get(sched["skill_id"])
                        model_context = model_context_by_skill_id.get(sched["skill_id"])
                        runtime_tracking = _runtime_config_for_schedule_tracking(runtime_config, model_context)
                        session.add(NodeScheduleConfig(
                            instance_id=inst.id,
                            skill_id=sched["skill_id"],
                            cron_expression=sched["cron"],
                            config_version=config_version,
                            pushed_at=now_bjt(),
                            ack_ok=snapshot_ok,
                            runtime_backend=(runtime_config or {}).get("backend"),
                            runtime_config=runtime_tracking,
                        ))

                result_item = {
                    "instance_id": inst.id,
                    "agent_purpose": _agent_purpose(inst),
                    "runtime_eligible": _is_skill_runtime_agent(inst),
                    "ok": ok,
                    "error": error,
                }
                if "accepted" in sync_result:
                    result_item["accepted"] = sync_result.get("accepted")
                if "config_version" in sync_result:
                    result_item["config_version"] = sync_result.get("config_version")
                if local_skills_dir:
                    result_item["local_skills_dir"] = local_skills_dir
                if missing_local_skill_ids:
                    result_item["missing_local_skills"] = missing_local_skill_ids
                results.append(result_item)

            await session.commit()

        return results

    def _sync_result(
        self,
        inst,
        *,
        ok: bool,
        error: str | None,
        reload: dict,
        result: dict | None = None,
    ) -> dict:
        payload = {
            "instance_id": inst.id,
            "name": getattr(inst, "name", None),
            "agent_type": getattr(inst, "agent_type", "aiclaw") or "aiclaw",
            "agent_purpose": _agent_purpose(inst),
            "runtime_eligible": _is_skill_runtime_agent(inst),
            "is_platform_default": bool(getattr(inst, "is_platform_default", False)),
            "ok": bool(ok),
            "error": error,
            "reload": reload,
        }
        if result is not None:
            payload["result"] = result
        return payload

    @staticmethod
    def _install_result_ok(result: dict | None) -> bool:
        if not isinstance(result, dict):
            return True
        if result.get("ok") is False:
            return False
        if "installed" in result:
            return bool(result.get("installed"))
        if "success" in result:
            return bool(result.get("success"))
        return True

    @staticmethod
    def _normalize_reload_result(result: dict | None) -> dict:
        if not isinstance(result, dict):
            return {"ok": True, "status": "ok", "result": result}
        status = result.get("status")
        if result.get("skipped"):
            status = "skipped"
        if result.get("unsupported"):
            status = "unsupported"
        ok = bool(result.get("ok", True)) and status not in {"skipped", "unsupported", "failed", "error"}
        status = status or ("ok" if ok else "failed")
        return {**result, "ok": ok, "status": status}

    @staticmethod
    def _normalize_register_result(result: dict | None) -> dict:
        if not isinstance(result, dict):
            return {"ok": True, "status": "ok", "result": result}
        status = result.get("status")
        if result.get("unsupported"):
            status = "unsupported"
        if result.get("registered") is False:
            status = status or "failed"
        ok = bool(result.get("ok", result.get("registered", True))) and status not in {
            "unsupported",
            "failed",
            "error",
        }
        status = status or ("ok" if ok else "failed")
        return {**result, "ok": ok, "status": status}

    @staticmethod
    def _register_error_result(exc: Exception) -> dict:
        code = getattr(exc, "code", None)
        detail = getattr(exc, "detail", None)
        raw = str(detail or exc)
        lowered = raw.lower()
        if "unsupported" in lowered or "not found" in lowered or "unknown method" in lowered or "method not" in lowered:
            return {
                "ok": False,
                "status": "unsupported",
                "error": raw[:200],
                "code": code,
            }
        return {
            "ok": False,
            "status": "failed",
            "error": raw[:200],
            "code": code,
        }

    @staticmethod
    def _reload_error_result(exc: Exception) -> dict:
        code = getattr(exc, "code", None)
        detail = getattr(exc, "detail", None)
        raw = str(detail or exc)
        lowered = raw.lower()
        if "unsupported" in lowered or "not found" in lowered or "unknown method" in lowered or "method not" in lowered:
            return {
                "ok": False,
                "status": "unsupported",
                "error": raw[:200],
                "code": code,
            }
        return {
            "ok": False,
            "status": "failed",
            "error": raw[:200],
            "code": code,
        }

    @staticmethod
    def _sync_error(install_ok: bool, reload_result: dict) -> str:
        if not install_ok:
            return "INSTALL_SKILL_FAILED"
        status = reload_result.get("status") or "failed"
        if status == "unsupported":
            return "RELOAD_UNSUPPORTED"
        if status == "skipped":
            return "RELOAD_SKIPPED"
        return "RELOAD_SKILLS_FAILED"

    @staticmethod
    def _reload_non_blocking(reload_result: dict) -> bool:
        # 老版本 AIClaw/OpenClaw 已能接收文件，但没有 skills.reload RPC。
        # 这种情况下同步本身成功，热重载作为兼容性提示展示，避免审批同步被误判失败。
        return (reload_result.get("status") or "").lower() == "unsupported"

    @staticmethod
    @staticmethod
    def _is_global_operator(actor: Any | None) -> bool:
        if actor is None:
            return False
        return bool(
            getattr(actor, "can_view_all", False)
            or getattr(actor, "role", "") in {"system_admin", "admin"}
        )

    @staticmethod
    def _normalize_department(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    async def _department_lineage_match(actor_dept: str, inst_dept: str) -> bool:
        """检查 actor 部门是否是 inst 部门的上级/同级/下级（按 org_units.path 血缘）。"""
        if not actor_dept or not inst_dept:
            return False
        if actor_dept == inst_dept:
            return True
        try:
            from app.database import async_session_factory
            from app.org.models import OrgUnit
            from sqlalchemy import select

            async with async_session_factory() as s:
                rows = (await s.execute(
                    select(OrgUnit.path).where(OrgUnit.name.in_([actor_dept, inst_dept]))
                )).all()
            paths: dict[str, str] = {}
            for (p,) in rows:
                if p:
                    paths.setdefault(p, p)
            # 用 path 包含判断血缘
            actor_path = None
            inst_path = None
            for p in paths:
                if p.endswith(f"/{actor_dept}") or p == f"/{actor_dept}":
                    actor_path = p
                if p.endswith(f"/{inst_dept}") or p == f"/{inst_dept}":
                    inst_path = p
            if not actor_path or not inst_path:
                # fallback: 逐个匹配 name
                for p in paths:
                    if actor_dept in p.split("/"):
                        actor_path = p
                    if inst_dept in p.split("/"):
                        inst_path = p
            if actor_path and inst_path:
                # 同一条 lineage 链上即通过
                return actor_path.startswith(inst_path) or inst_path.startswith(actor_path)
        except Exception:
            pass
        return actor_dept == inst_dept  # fallback to string match

    @classmethod
    def _is_platform_default_instance(cls, inst: Any) -> bool:
        if bool(getattr(inst, "is_platform_default", False)):
            return True
        return cls._normalize_department(getattr(inst, "department", None)) in PLATFORM_FALLBACK_DEPARTMENTS

    @staticmethod
    def _actor_id(actor: Any | None) -> str | None:
        if actor is None:
            return None
        if isinstance(actor, str):
            return actor
        return str(getattr(actor, "id", "") or getattr(actor, "username", "") or "") or None

    @staticmethod
    def _job_actor_proxy(actor_id: str | None):
        if not actor_id:
            return None
        return type("SyncJobActor", (), {
            "id": actor_id,
            "role": "system_admin",
            "department": None,
            "can_view_all": True,
        })()

    @staticmethod
    def _serialize_sync_job(job, attempts: list) -> dict:
        return {
            "job_id": int(job.id),
            "skill_id": job.skill_id,
            "version_tag": job.version_tag,
            "review_id": job.review_id,
            "status": job.status,
            "error": job.error,
            "trigger": job.trigger,
            "actor": job.actor,
            "attempt_count": job.attempt_count,
            "target_instance_ids": job.target_instance_ids,
            "target_department": job.target_department,
            "push_ok": job.push_ok,
            "created_at": isoformat_bjt(job.created_at),
            "updated_at": isoformat_bjt(job.updated_at),
            "attempts": [
                {
                    "attempt_id": int(item.id),
                    "job_id": int(item.job_id),
                    "skill_id": item.skill_id,
                    "version_tag": item.version_tag,
                    "review_id": item.review_id,
                    "instance_id": item.instance_id,
                    "agent_type": item.agent_type,
                    "status": item.status,
                    "error": item.error,
                    "trigger": item.trigger,
                    "actor": item.actor,
                    "attempt_count": item.attempt_count,
                    "result": item.result,
                    "started_at": isoformat_bjt(item.started_at),
                    "completed_at": isoformat_bjt(item.completed_at),
                    "created_at": isoformat_bjt(item.created_at),
                }
                for item in attempts
            ],
        }

    async def _push_to_all_instances(self, skill_id: str) -> list[dict]:
        """兼容旧测试/脚本：显式全量推送。新业务路径不要调用它。"""
        from sqlalchemy import select

        from app.database import async_session_factory
        from app.execution.models import OpenClawInstance

        async with async_session_factory() as session:
            ids = (
                await session.execute(
                    select(OpenClawInstance.id).where(OpenClawInstance.is_active == True)  # noqa: E712
                )
            ).scalars().all()
        return await self.push_skill_to_targets(skill_id, target_instance_ids=list(ids), actor=None)

    @staticmethod
    def _git_remote_url() -> str | None:
        """读取 skills-repo git 仓库的 origin remote URL。"""
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=settings.SKILL_REPO_PATH,
                capture_output=True, text=True, timeout=10, check=False,
            )
            url = (result.stdout or "").strip()
            return url if url else None
        except Exception:
            return None

    @staticmethod
    def _canonical_branch() -> str:
        branch = (settings.SKILL_REPO_BRANCH or "main").strip() or "main"
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$", branch):
            logger.warning("SKILL_REPO_BRANCH 非法，回退到 main: {}", branch)
            return "main"
        return branch

    def _canonical_push_targets(self) -> list[str]:
        targets: list[str] = []
        configured_target = (settings.SKILL_REPO_CANONICAL_REMOTE or "").strip()
        if configured_target:
            targets.append(configured_target)
        if getattr(settings, "SKILL_REPO_DOCKER_CONTAINER", "") and getattr(settings, "SKILL_REPO_DOCKER_BARE_REPO", ""):
            targets.append(
                "ext::docker exec -i --user git "
                f"{settings.SKILL_REPO_DOCKER_CONTAINER} "
                f"git receive-pack {settings.SKILL_REPO_DOCKER_BARE_REPO}"
            )
        return targets

    async def _skill_git_config(self) -> dict[str, str]:
        """读取后台配置的 Skill Git 地址和账号信息。"""
        try:
            from sqlalchemy import select

            from app.common.models import SystemConfig
            from app.database import async_session_factory

            async with async_session_factory() as session:
                rows = (
                    await session.execute(
                        select(SystemConfig).where(SystemConfig.key.in_(_SKILL_GIT_CONFIG_KEYS))
                    )
                ).scalars().all()
        except Exception as exc:  # noqa: BLE001
            logger.debug("读取 Skill Git 配置失败，回退到 git remote/.env: {}", exc)
            return {}

        config: dict[str, str] = {}
        for row in rows:
            mapped_key = _SKILL_GIT_CONFIG_KEYS.get(row.key)
            if not mapped_key:
                continue
            value = row.value
            if value is None:
                continue
            config[mapped_key] = str(value).strip()
        return config

    async def _push_canonical_git(self) -> bool:
        """Push current HEAD to the single Docker/Gitea Skill mainline.

        The local worktree may be on any branch created by an AI/editor flow; the
        canonical Skill Git service must keep one branch only.
        """
        repo_path = Path(settings.SKILL_REPO_PATH)
        git_dir = repo_path / ".git"
        if not git_dir.exists():
            logger.error("Skill Git 仓库未初始化: {}", repo_path)
            return False

        targets = self._canonical_push_targets()
        if not targets:
            logger.error("未配置本地 Docker/Gitea Skill Git 主线 remote")
            return False

        branch = self._canonical_branch()
        refspec = f"HEAD:refs/heads/{branch}"
        from app.common.retry import RetryableError, retry_with_backoff

        async def _do_push(target: str) -> bool:
            cmd = ["git", "push", target, refspec]
            env = dict(os.environ)
            if target.startswith("ext::"):
                env["GIT_ALLOW_PROTOCOL"] = "ext"
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                cwd=settings.SKILL_REPO_PATH,
                capture_output=True,
                timeout=30,
                env=env,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace")
                stdout = result.stdout.decode(errors="replace")
                detail = (stderr or stdout or "git push failed").strip()
                logger.error("canonical git push 失败 target={} branch={}: {}", _redact_git_url(target), branch, detail)
                lowered = detail.lower()
                if any(k in lowered for k in ("timeout", "connection", "lock", "unable to access", "remote end hung up")):
                    raise RetryableError(f"canonical git push 瞬态失败: {detail[:200]}")
                return False
            return True

        for target in targets:
            logger.info("canonical git push HEAD → {}:{}", _redact_git_url(target), branch)
            try:
                ok = await retry_with_backoff(
                    lambda target=target: _do_push(target),
                    max_retries=2,
                    base_delay=2.0,
                    description="canonical git push",
                    retryable_exceptions=(RetryableError,),
                )
            except RetryableError:
                ok = False
            except Exception as exc:  # noqa: BLE001
                logger.error("canonical git push 异常 target={}: {}", _redact_git_url(target), exc)
                ok = False
            if ok:
                return True
        return False

    async def _git_push(self) -> bool:
        """推送到外部 Skill Git mirror（含抖动退避重试）。

        优先使用后台 Skill Git 配置；未配置时读取 git remote origin；
        两者都缺时再回退到 SKILL_REPO_REMOTE。
        """
        repo_path = Path(settings.SKILL_REPO_PATH)
        git_dir = repo_path / ".git"
        if not git_dir.exists():
            logger.error("Skill Git 仓库未初始化: {}", repo_path)
            return False

        git_config = await self._skill_git_config()
        configured_url = git_config.get("local_remote_url") or ""
        origin_url = self._git_remote_url()
        remote_url = configured_url or origin_url
        if not remote_url:
            if settings.SKILL_REPO_REMOTE:
                remote_url = settings.SKILL_REPO_REMOTE
                logger.warning("git remote origin 未配置，回退到 SKILL_REPO_REMOTE")
            else:
                logger.warning("skills-repo 未配置 git remote，跳过 push")
                return False

        from app.common.retry import retry_with_backoff, RetryableError
        branch = self._canonical_branch()
        refspec = f"HEAD:refs/heads/{branch}"
        remote_with_auth = _inject_git_credentials(
            remote_url,
            git_config.get("username", ""),
            git_config.get("password", ""),
        )
        push_target = (
            "origin"
            if origin_url and remote_url == origin_url and not git_config.get("username") and not git_config.get("password")
            else remote_with_auth
        )

        async def _do_push() -> bool:
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "push", push_target, refspec],
                cwd=settings.SKILL_REPO_PATH,
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode()
                logger.error(f"git push失败: {stderr}")
                # 网络/锁等瞬态错误可重试
                if any(k in stderr.lower() for k in ("timeout", "connection", "lock", "unable to access")):
                    raise RetryableError(f"git push 瞬态失败: {stderr[:200]}")
                return False
            return True

        logger.info("git mirror push HEAD → {}:{}", _redact_git_url(remote_with_auth), branch)
        try:
            return await retry_with_backoff(
                _do_push, max_retries=2, base_delay=2.0, description="git push",
                retryable_exceptions=(RetryableError,),
            )
        except RetryableError:
            return False
        except Exception as e:
            logger.error(f"git push异常: {e}")
            return False

    @staticmethod
    def _current_repo_branch() -> str:
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=settings.SKILL_REPO_PATH,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            branch = (result.stdout or "").strip()
            if branch:
                return branch
        except Exception:
            return "HEAD"
        return "HEAD"

    async def _notify_reload(self) -> dict:
        """通知OpenClaw实例reload"""
        reload_url = settings.OPENCLAW_RELOAD_HOOK_URL
        reload_token = settings.OPENCLAW_RELOAD_TOKEN

        if not reload_url:
            logger.info("未配置reload_hook，跳过通知")
            return {"ok": True, "skipped": True}

        try:
            endpoint_url, url_error = build_reload_endpoint_url(reload_url)
            if url_error or not endpoint_url:
                return {"ok": False, "error": url_error or "INVALID_RELOAD_HOOK_URL"}
            headers = _reload_auth_headers(reload_token)
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(endpoint_url, headers=headers)
                return resp.json()
        except Exception as e:
            logger.error(f"通知OpenClaw reload失败: {e}")
            return {"ok": False, "error": str(e)}

    async def check_health(self) -> dict:
        """检查OpenClaw实例健康状态"""
        reload_url = settings.OPENCLAW_RELOAD_HOOK_URL
        if not reload_url:
            return {"available": False, "reason": "未配置"}

        try:
            health_url, url_error = build_reload_health_url(reload_url)
            if url_error or not health_url:
                return {"openclaw_active": False, "error": url_error or "INVALID_RELOAD_HOOK_URL"}
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(health_url)
                return resp.json()
        except Exception:
            return {"openclaw_active": False}

    async def reload_all(self) -> dict:
        """批量发布后统一操作：push到Git + 通知所有active的OpenClaw实例reload"""
        push_ok = await self._git_push()

        # 查询数据库中所有active实例，逐个发送reload
        from sqlalchemy import select
        from app.execution.models import OpenClawInstance
        from datetime import datetime

        def _sf():
            from app.database import async_session_factory
            return async_session_factory

        instance_results = []
        async with _sf()() as session:
            stmt = select(OpenClawInstance).where(OpenClawInstance.is_active == True)  # noqa: E712
            instances = (await session.execute(stmt)).scalars().all()

            for inst in instances:
                reload_url = inst.reload_hook_url
                token = inst.reload_token
                try:
                    endpoint_url, url_error = build_reload_endpoint_url(reload_url)
                    if url_error or not endpoint_url:
                        raise ValueError(url_error or "INVALID_RELOAD_HOOK_URL")
                    headers = _reload_auth_headers(token)
                    async with httpx.AsyncClient(timeout=60) as client:
                        resp = await client.post(endpoint_url, headers=headers)
                        result = resp.json()

                    # 更新实例同步状态
                    inst.last_sync_at = now_bjt()
                    inst.last_sync_ok = result.get("ok", False)
                    instance_results.append({"instance": inst.id, "ok": True, **result})
                except Exception as e:
                    inst.last_sync_at = now_bjt()
                    inst.last_sync_ok = False
                    instance_results.append({"instance": inst.id, "ok": False, "error": str(e)})
                    logger.error(f"通知OpenClaw实例 {inst.id} reload失败: {e}")

            await session.commit()

        # 如果没有数据库中的实例，回退到默认配置的reload
        if not instance_results:
            default_result = await self._notify_reload()
            instance_results.append({"instance": "default", **default_result})

        all_ok = push_ok and all(r.get("ok", False) for r in instance_results)

        # 任一环节失败即告警（与 sync_after_approval 行为对齐）
        # 包 try/except：告警通道异常不能影响主返回
        if not all_ok:
            try:
                failed = [r for r in instance_results if not r.get("ok")]
                await self._alert_sync_failure(
                    "batch_publish",
                    {
                        "push_ok": push_ok,
                        "error": (
                            f"push_ok={push_ok}; "
                            f"failed_instances={[r.get('instance') for r in failed]}"
                        ),
                        "details": failed,
                    },
                )
            except Exception as alert_err:
                logger.error(f"批量发布告警发送失败（不影响主流程）: {alert_err}")

        return {"all_ok": all_ok, "push_ok": push_ok, "instances": instance_results}

    async def sync_playbook_after_approval(self, playbook_name: str) -> dict:
        """Playbook 审核通过后触发: git push + 通知 OpenClaw reload。

        Playbook 是单 YAML 文件, 不走 bridge install_skill 那套目录推送;
        OpenClaw 接到 reload 后会重新扫描 skills-repo/playbooks/ 即可。
        """
        push_ok = await self._push_canonical_git()
        mirror_push_ok: bool | None = None
        if not push_ok:
            reload_result = {"ok": False, "status": "skipped", "reason": "git_push_failed"}
            instance_results: list[dict] = []
            await self._alert_sync_failure(
                f"playbook:{playbook_name}",
                {"push_ok": False, "reload": reload_result, "instances": instance_results},
            )
            return {
                "all_ok": False,
                "push_ok": False,
                "mirror_push_ok": mirror_push_ok,
                "reload": reload_result,
                "instances": instance_results,
            }

        mirror_push_ok = await self._git_push()
        if not mirror_push_ok:
            await self._alert_sync_failure(
                f"playbook:{playbook_name}",
                {
                    "mirror_push_ok": False,
                    "push_ok": True,
                    "error": "SKILL_GIT_MIRROR_PUSH_FAILED",
                },
            )
        reload_result = await self._notify_reload()

        # 遍历所有注册实例, 发 /reload HTTP 通知 (bridge 不做 install_skill)
        instance_results = await self._notify_reload_all_instances()

        all_ok = push_ok and reload_result.get("ok", False)
        if instance_results:
            all_ok = all_ok and all(r.get("ok", False) for r in instance_results)

        if not all_ok:
            await self._alert_sync_failure(
                f"playbook:{playbook_name}",
                {
                    "push_ok": push_ok,
                    "mirror_push_ok": mirror_push_ok,
                    "reload": reload_result,
                    "instances": instance_results,
                },
            )

        return {
            "all_ok": all_ok,
            "push_ok": push_ok,
            "mirror_push_ok": mirror_push_ok,
            "reload": reload_result,
            "instances": instance_results,
        }

    async def _notify_reload_all_instances(self) -> list[dict]:
        """遍历 DB 中所有 active 实例, 发 /reload HTTP 通知 (不推送文件)。

        H8 修复：原实现串行 await 每个实例的 60s HTTP，N 实例时
        approve_review 阻塞 N×60s，5 实例 ≈ 5min 不可接受。
        改为并发：每个实例独立协程 + 60s 超时；单实例失败不阻塞其他实例，
        失败会写 audit + warning log。最终返回所有实例结果。
        """
        from sqlalchemy import select
        from app.execution.models import OpenClawInstance

        def _sf():
            from app.database import async_session_factory
            return async_session_factory

        async with _sf()() as session:
            stmt = select(OpenClawInstance).where(OpenClawInstance.is_active == True)  # noqa: E712
            instances = (await session.execute(stmt)).scalars().all()

        # 注意：上面已退出 session 上下文，下面的并发请求与 DB 解耦，
        # 长时间 HTTP 不再压制 DB 连接。
        if not instances:
            return []

        async def _notify_one(inst) -> dict:
            """单实例 reload；本协程内捕获异常，绝不向 gather 抛出。"""
            raw_reload_url = inst.reload_hook_url
            reload_url = str(raw_reload_url or "").strip()
            token = inst.reload_token
            failure_exc: Exception | None = None
            try:
                endpoint_url, url_error = build_reload_endpoint_url(reload_url)
                if url_error or not endpoint_url:
                    raise ValueError(url_error or "INVALID_RELOAD_HOOK_URL")
                headers = _reload_auth_headers(token)
                async with asyncio.timeout(60):
                    async with httpx.AsyncClient(timeout=60) as client:
                        resp = await client.post(endpoint_url, headers=headers)
                        result = resp.json() if resp.content else {"ok": False}
                return {"instance": inst.id, "ok": result.get("ok", False), **result}
            except (asyncio.TimeoutError, httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "通知实例 {} reload 失败（不阻塞其它实例）: {}", inst.id, exc,
                )
                failure_exc = exc
            except Exception as exc:
                # 兜底：协程内消化所有异常，确保 gather 拿到完整列表
                logger.exception("通知实例 {} reload 出现未预期异常: {}", inst.id, exc)
                failure_exc = exc

            # 失败统一写审计日志（不论何种异常类别都记录，便于排障）
            try:
                from app.common.audit import audit
                await audit.log(
                    "system",
                    "execution.reload_failed",
                    "openclaw_instance",
                    str(inst.id),
                    detail={"error": str(failure_exc)[:200], "url": reload_url},
                )
            except Exception as audit_err:  # pragma: no cover
                logger.warning("reload 失败审计写入异常 inst={}: {}", inst.id, audit_err)
            return {"instance": inst.id, "ok": False, "error": str(failure_exc)[:200]}

        # v2.0.16 H3：return_exceptions=True 兜底。_notify_one 自身已 try/except 所有 Exception，
        # 但 asyncio.CancelledError（Python 3.8+ 继承自 BaseException，不是 Exception）仍会
        # 冒泡；若父任务 cancel，这里整批 raise 会让审批链拿不到任何 results。
        # 用 True 模式 + 后处理把 BaseException 映射成 {"ok": False, "error": ...}。
        raw_results = await asyncio.gather(
            *[_notify_one(inst) for inst in instances],
            return_exceptions=True,
        )
        results: list[dict] = []
        for inst, item in zip(instances, raw_results, strict=True):
            if isinstance(item, BaseException):
                # 协程本身抛了异常（CancelledError / 不预期错误）—— 回落成 ok=False
                logger.warning(
                    "通知实例 {} reload 协程抛异常：{} {}",
                    inst.id, type(item).__name__, item,
                )
                results.append({
                    "instance": inst.id,
                    "ok": False,
                    "error": f"{type(item).__name__}: {item}"[:200],
                })
            else:
                results.append(item)
        return results

    @staticmethod
    def _sync_failure_items(detail: dict) -> list[dict]:
        items: list[dict] = []
        for key in ("instances", "details"):
            value = detail.get(key)
            if isinstance(value, list):
                items.extend(item for item in value if isinstance(item, dict))
        if any(detail.get(key) for key in ("instance_id", "instance", "name")):
            items.append(detail)
        return items

    @staticmethod
    def _sync_failure_error(detail: dict, items: list[dict]) -> str:
        error = detail.get("error")
        if not error and detail.get("push_ok") is False:
            error = "GIT_PUSH_FAILED"
        reload_detail = detail.get("reload")
        if not error and isinstance(reload_detail, dict):
            error = reload_detail.get("error") or reload_detail.get("reason")
            if not error and reload_detail.get("ok") is False:
                error = reload_detail.get("status") or "RELOAD_FAILED"
        if not error:
            for item in items:
                if item.get("error"):
                    error = item.get("error")
                    break
                item_reload = item.get("reload")
                if isinstance(item_reload, dict) and item_reload.get("error"):
                    error = item_reload.get("error")
                    break
        return str(error or "未知错误")

    @staticmethod
    def _sync_failure_target(item: dict) -> str:
        return str(item.get("instance_id") or item.get("instance") or item.get("name") or "无具体节点")

    @classmethod
    def _sync_failure_advice(cls, detail: dict, error: str, items: list[dict]) -> list[str]:
        push_ok = detail.get("push_ok")
        lowered = error.lower()
        has_concrete_node = any(
            item.get("instance_id") or item.get("instance") or item.get("name")
            for item in items
        )
        has_node_stage = (
            has_concrete_node
            or isinstance(detail.get("reload"), dict)
            or any(key in lowered for key in ("reload", "install", "connection", "request url", "http://", "https://"))
        )

        advice: list[str] = []
        if detail.get("mirror_push_ok") is False or error == "SKILL_GIT_MIRROR_PUSH_FAILED":
            advice.append(
                "外部 Skill Git mirror 推送失败：本地 Docker/Gitea 主线已成功，不阻断运行节点下发；"
                "检查外部仓库地址、账号/Token 或网络后可稍后补推。"
            )
        if push_ok is False or error == "GIT_PUSH_FAILED" or "push_ok=false" in lowered:
            advice.append(
                "本地 Docker/Gitea Skill Git 主线推送失败：检查 Gitea 容器状态、bare repo 路径、"
                "本地 skills-repo 是否能快进到 canonical 分支。该失败会阻断运行节点下发。"
            )
        if has_node_stage:
            prefix = "Skill Git 推送未失败；" if push_ok is True else ""
            advice.append(
                f"{prefix}当前错误来自节点同步/Reload 阶段。检查 OpenClaw/Bridge 节点在线状态，以及节点 HTTP 地址"
                "（Gateway/Reload Hook）是否完整包含 http:// 或 https://。"
            )
        if not advice and error in NON_RETRYABLE_SYNC_ERRORS:
            advice.append("检查本地 skills-repo 中对应 Skill 目录和文件是否存在；保存入库后再重新同步。")
        if not advice:
            advice.append("查看同步任务详情中的失败阶段；不要把项目代码仓和 Skill Git 远端仓库混在一起排查。")
        return advice

    async def _alert_sync_failure(self, skill_id: str, detail: dict):
        """同步失败钉钉告警"""
        detail = detail if isinstance(detail, dict) else {}
        instances = self._sync_failure_items(detail)
        error = self._sync_failure_error(detail, instances)

        target_lines = []
        for item in instances[:5]:
            target = self._sync_failure_target(item)
            item_error = item.get("error") or error
            target_lines.append(f"- 目标: {target}，错误: {item_error}")
        target_block = "\n".join(target_lines)
        target_block = f"\n\n{target_block}" if target_block else ""
        advice_block = "\n".join(f"- {item}" for item in self._sync_failure_advice(detail, error, instances))

        logger.error(f"Skill同步失败: {skill_id} error={error}")
        await enqueue_admin_work_notice({
            "title": "Skill同步失败告警",
            "markdown": (
                f"**Skill同步失败**\n\n"
                f"- Skill: {skill_id}\n"
                f"- 错误: {error}"
                f"{target_block}\n\n"
                f"**检查建议**\n\n{advice_block}"
            ),
        }, priority=1, related_type="skill_sync", related_id=skill_id)


# 全局实例
sync_service = SkillSyncService()
