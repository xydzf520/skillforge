"""Project hosting service.

A Project is a lightweight app surface served or linked by SkillForge. Runtime is
browser/static/iframe based, not container based. All project launches, gateway
capability calls and output ingress are persisted as SkillForge execution traces
so they can participate in reports, todos and the learning loop.
"""

from __future__ import annotations

import base64
import ast
import asyncio
import hashlib
import hmac
import io
import json
import re
import secrets
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from pathlib import Path, PurePosixPath
from time import perf_counter, time
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

from loguru import logger
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import (
    can_access_department,
    expand_department_ancestors,
    get_accessible_departments,
    get_primary_department_id,
    role_matches_any,
)
from app.auth.models import User
from app.aiclaw.bridge_registry import bridge_registry
from app.aiclaw.client import AIClawClient
from app.codex import cloud_video as codex_cloud_video
from app.codex import service as codex_service
from app.common.advisory_lock import acquire_xact_lock
from app.common.ai import call_llm_multimodal, redact_secret_text
from app.common.audit import AuditLog
from app.common.exceptions import AppError
from app.common.models import SystemConfig
from app.common.rate_limiter import DistributedRateLimiter
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime
from app.config import settings
from app.execution.models import DecisionLog, ExecutionRun, OpenClawInstance, RUN_MODE_SDK_SUBMIT
from app.inbox import report_designs
from app.inbox.service import extract_report_cards
from app.learning.service import (
    capture_decision_log,
    capture_execution_run,
    capture_project_capability_call,
    capture_project_ingress_event,
    capture_project_run_opened,
)
from app.learning.models import LearningEvent, LearningIngestionJob
from app.org.models import OrgUnit
from app.playbooks import service as playbook_service
from app.projects.models import (
    Project,
    ProjectCapabilityCall,
    ProjectIngressEvent,
    ProjectRun,
    ProjectRunAsset,
    ProjectSdkToken,
    ProjectVersion,
)
from app.todos.service import todo_service

PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,41}$")
PROJECT_TYPES = {"external_web", "web_static", "dashboard", "internal_tool", "playbook_ref"}
PROJECT_VISIBILITIES = {"department", "company", "private"}
PROJECT_STATUSES = {"draft", "published", "archived"}
PROJECT_AI_STATES = {"waiting_ai", "ai_completed", "ai_failed"}
PROJECT_SKILL_PREFIX = "project:"
PROJECT_LIST_DEFAULT_PAGE_SIZE = 60
PROJECT_LIST_MAX_PAGE_SIZE = 100
PROJECT_AI_CAPABILITIES = {
    "ai.generate",
    "ai.chat",
    "ai.example.testplete",
    "ai.cheap.generate",
    "ai.cheap.chat",
    "platform.ai.generate",
    "platform.ai.chat",
    "platform.ai.cheap.generate",
    "platform.ai.cheap.chat",
}
PROJECT_MEDIA_CAPABILITIES = {
    "video.plan_compare",
    "video.job.submit",
    "video.job.batch_submit",
    "video.job.batch_cancel",
    "video.job.list",
    "video.job.get",
    "video.job.cancel",
    "video.job.retry",
    "video.job.clone",
    "video.quality.analyze",
    "video.review",
    "cloud_video.sync",
}
PROJECT_MEDIA_CAPABILITIES |= {
    "material.workbench.snapshot",
    "material.request.create", "material.request.update", "material.request.list",
    "material.request.get", "material.request.submit",
    "material.candidate.generate", "material.candidate.list", "material.candidate.get",
    "material.candidate.manifest", "material.candidate.batch_tag", "material.candidate.tag.update",
    "material.asset.recommend",
    "material.review.session", "material.review.annotation.save",
    "material.decision.submit", "material.decision.batch_reject", "material.decision.batch_hold",
    "material.delivery.submit", "material.delivery.get",
    "material.performance.summary", "material.performance.list",
    "material.weekly_report.get", "material.review.preference.save",
    "video.create.direct_submit",
    "video.prompt.optimize",
    "video.agent.list", "video.agent.get", "video.agent.save", "video.agent.restore", "video.agent.invoke",
    "video.strategy.start", "video.strategy.answer", "video.strategy.compile", "video.strategy.submit",
    "video.replay.analyze", "video.replay.update", "video.replay.submit", "video.replay.get",
    "video.workbench.snapshot",
    "video.theme.diverge",
    "video.asset.library.list", "video.asset.library.upsert", "video.asset.library.archive",
    "video.asset.group.list", "video.asset.group.get", "video.asset.group.upsert", "video.asset.group.archive",
    "video.workflow.list", "video.workflow.get", "video.workflow.upsert", "video.workflow.publish", "video.workflow.archive",
    "video.production_batch.create", "video.production_batch.list", "video.production_batch.get",
    "video.production_batch.update", "video.production_batch.cancel",
    "video.continuation.plan", "video.continuation.submit", "video.continuation.get",
    "video.continuation.cancel", "video.continuation.retry_segment",
    "video.review.manifest", "video.review.annotation.list", "video.review.annotation.save",
    "video.review.annotation.resolve", "video.review.batch_reject", "video.review.batch_update",
    "video.dialogue_delivery.retry",
    "video.enhance.submit",
    "video.production.prepare", "video.production.compile", "video.production.submit", "video.first_frame.generate",
    "video.review.list",
    "video.cloud_reference.search", "video.cloud_reference.import",
}
PROJECT_SDK_TOKEN_PREFIX = "sfproj"
PROJECT_SDK_DEFAULT_TOKEN_TTL_DAYS = 90
PROJECT_SDK_TOKEN_SCOPES = {
    "runs:create",
    "runs:input",
    "runs:capability",
    "runs:ingest",
    "runs:trace",
    "runs:assets",
    "training:sync",
    "training:assets",
    "training:samples",
    "training:datasets:create",
    "training:datasets:sync",
}
PROJECT_SDK_DEFAULT_TOKEN_SCOPES = ["runs:capability", "runs:create", "runs:trace"]
PROJECT_SDK_QPS_LIMITER = DistributedRateLimiter(
    scope="project_sdk_qps",
    max_requests=max(1, int(getattr(settings, "PROJECT_SDK_QPS_PER_TOKEN", 5) or 5)),
    window_seconds=1,
)
PROJECT_SDK_DAILY_LIMITER = DistributedRateLimiter(
    scope="project_sdk_daily",
    max_requests=max(1, int(getattr(settings, "PROJECT_SDK_DAILY_LIMIT_PER_TOKEN", 10_000) or 10_000)),
    window_seconds=24 * 60 * 60,
)
PROJECT_TRAINING_SINK_DEFAULT_GATEWAY_ID = "GB10 237"
PROJECT_TRAINING_SINK_DEFAULT_MODE = "async_dataset"
PROJECT_COMPANY_SDK_PROJECT_ID = "skillforge-company-sdk"
PROJECT_COMPANY_SDK_PROJECT_NAME = "SkillForge Company SDK"
PROJECT_RESIDENT_MODEL_GATEWAY_ID = "inference-primary"
PROJECT_236_CONTEXT_CONFIG_KEY = "project.openai236.context_measurements"
PROJECT_236_CONTEXT_SAFETY_MARGIN_TOKENS = 2048
PROJECT_236_CONTEXT_FALLBACK_WINDOW_TOKENS = 32768
PROJECT_236_CONTEXT_UNVERIFIED_INPUT_TOKENS = 16384
PROJECT_236_CONTEXT_MAX_OUTPUT_TOKENS = 4096
PROJECT_236_PROMPT_TEXT_LIMIT_CHARS = 2 * 1024 * 1024
PROJECT_RESIDENT_MODEL_GATEWAY_ALIASES = {
    PROJECT_RESIDENT_MODEL_GATEWAY_ID,
    "236",
    "mac-236",
    "m3-ultra-236",
    "platform_mac_236",
}
PROJECT_MCP_READ_CAPABILITIES = {
    "mcp://skillforge_org_search_users": {"tool": "skillforge_org_search_users", "data_scope": "org.users"},
    "mcp://skillforge_org_list_members": {"tool": "skillforge_org_list_members", "data_scope": "org.users"},
    "mcp://skillforge_agent_coverage": {"tool": "skillforge_agent_coverage", "data_scope": "platform.agent_coverage"},
    "mcp://skillforge_cloud_video_accounts": {"tool": "skillforge_cloud_video_accounts", "data_scope": "cloud_video.accounts"},
    "mcp://skillforge_cloud_video_ad_report": {"tool": "skillforge_cloud_video_ad_report", "data_scope": "cloud_video.ad_report"},
    "mcp://skillforge_cloud_video_audit_rejects": {
        "tool": "skillforge_cloud_video_audit_rejects",
        "data_scope": "cloud_video.audit_rejects",
    },
    "mcp://skillforge_cloud_video_daily_person_video_report": {
        "tool": "skillforge_cloud_video_daily_person_video_report",
        "data_scope": "cloud_video.daily_person_video_report",
    },
    "mcp://skillforge_cloud_video_material_report": {
        "tool": "skillforge_cloud_video_material_report",
        "data_scope": "cloud_video.material_report",
    },
    "mcp://skillforge_cloud_video_video_usage_report": {
        "tool": "skillforge_cloud_video_video_usage_report",
        "data_scope": "cloud_video.video_usage_report",
    },
    "mcp://skillforge_cloud_video_categories": {"tool": "skillforge_cloud_video_categories", "data_scope": "cloud_video.categories"},
    "mcp://skillforge_cloud_video_tags": {"tool": "skillforge_cloud_video_tags", "data_scope": "cloud_video.tags"},
    "mcp://skillforge_cloud_video_videos": {"tool": "skillforge_cloud_video_videos", "data_scope": "cloud_video.videos"},
    "mcp://skillforge_samplebrand_cloud_video_daily_analysis_input": {
        "tool": "skillforge_samplebrand_cloud_video_daily_analysis_input",
        "data_scope": "cloud_video.samplebrand_weekly.daily_analysis_input",
    },
    "mcp://skillforge_samplebrand_cloud_video_data_latest": {
        "tool": "skillforge_samplebrand_cloud_video_data_latest",
        "data_scope": "cloud_video.samplebrand_weekly.cached_artifacts",
    },
    "mcp://skillforge_samplebrand_cloud_video_data_get": {
        "tool": "skillforge_samplebrand_cloud_video_data_get",
        "data_scope": "cloud_video.samplebrand_weekly.cached_artifacts",
    },
    "mcp://skillforge_tmall_link_decline_data_latest": {
        "tool": "skillforge_tmall_link_decline_data_latest",
        "data_scope": "tmall.link_decline.cached_artifacts",
    },
    "mcp://skillforge_tmall_link_decline_data_get": {
        "tool": "skillforge_tmall_link_decline_data_get",
        "data_scope": "tmall.link_decline.cached_artifacts",
    },
    "tmall_link_health_latest_analysis": {
        "tool": "tmall_link_health_latest_analysis",
        "data_scope": "tmall.link_health.latest_cached_analysis",
    },
    "mcp://tmall_link_health_latest_analysis": {
        "tool": "tmall_link_health_latest_analysis",
        "data_scope": "tmall.link_health.latest_cached_analysis",
    },
}
PROJECT_RUN_ACTIVE_STATUSES = {"opening", "running"}
PROJECT_RUN_TERMINAL_STATUSES = {"completed", "failed", "ai_completed", "ai_failed"}
TMALL_LINK_HEALTH_PROJECT_ID = "samplebrand-tmall-link-health-dashboard-v1"
TMALL_LINK_HEALTH_SOURCE_SKILL_ID = "tmall-link-decline-collector-v1"
TMALL_LINK_HEALTH_ANALYSIS_SKILL_ID = "samplebrand-tmall-link-health-daily-v1"
TMALL_LINK_HEALTH_ARTIFACT_MAX_BYTES = 5 * 1024 * 1024
PROJECT_HEARTBEAT_STALE_SECONDS = 120
PROJECT_STALE_RECONCILE_LIMIT = 500
PROJECT_CAPACITY_LOCK_KEY = "project_run_capacity:global"
PROJECT_PACKAGE_MAX_BYTES = int(getattr(settings, "MAX_UPLOAD_SIZE", 50 * 1024 * 1024) or 50 * 1024 * 1024)
PROJECT_PACKAGE_MAX_FILES = 2000
PROJECT_PACKAGE_MAX_EXTRACTED_BYTES = max(PROJECT_PACKAGE_MAX_BYTES, PROJECT_PACKAGE_MAX_BYTES * 4)
PROJECT_PACKAGE_MAX_MANIFEST_BYTES = 128 * 1024
PROJECT_INGEST_MAX_BYTES = int(getattr(settings, "PROJECT_INGEST_MAX_BYTES", 512 * 1024) or 512 * 1024)
PROJECT_INPUT_MAX_BYTES = int(getattr(settings, "PROJECT_INPUT_MAX_BYTES", 256 * 1024) or 256 * 1024)
PROJECT_CAPABILITY_MAX_INPUT_BYTES = int(getattr(settings, "PROJECT_CAPABILITY_MAX_INPUT_BYTES", 256 * 1024) or 256 * 1024)
PROJECT_CAPABILITY_MAX_CALLS_PER_RUN = int(getattr(settings, "PROJECT_CAPABILITY_MAX_CALLS_PER_RUN", 100) or 100)
PROJECT_MAX_REPORTS_PER_INGEST = int(getattr(settings, "PROJECT_MAX_REPORTS_PER_INGEST", 50) or 50)
PROJECT_MAX_TODOS_PER_INGEST = int(getattr(settings, "PROJECT_MAX_TODOS_PER_INGEST", 100) or 100)
PROJECT_MAX_PROOFS_PER_INGEST = int(getattr(settings, "PROJECT_MAX_PROOFS_PER_INGEST", 50) or 50)
PROJECT_RUN_ASSET_MAX_BYTES = int(getattr(settings, "MAX_UPLOAD_SIZE", 50 * 1024 * 1024) or 50 * 1024 * 1024)
PROJECT_RUN_ASSET_STREAM_CHUNK_BYTES = 1024 * 1024
PROJECT_VISUAL_VIDEO_MAX_COUNT = int(getattr(settings, "PROJECT_VISUAL_VIDEO_MAX_COUNT", 2) or 2)
PROJECT_VISUAL_VIDEO_MAX_BYTES = int(getattr(settings, "PROJECT_VISUAL_VIDEO_MAX_BYTES", 25 * 1024 * 1024) or 25 * 1024 * 1024)
PROJECT_VISUAL_VIDEO_MAX_FRAMES = int(getattr(settings, "PROJECT_VISUAL_VIDEO_MAX_FRAMES", 16) or 16)
PROJECT_VISUAL_VIDEO_FPS = float(getattr(settings, "PROJECT_VISUAL_VIDEO_FPS", 1) or 1)
PROJECT_VISUAL_FRAME_MAX_COUNT = int(getattr(settings, "PROJECT_VISUAL_FRAME_MAX_COUNT", 16) or 16)
PROJECT_VISUAL_FRAME_MAX_BYTES = int(getattr(settings, "PROJECT_VISUAL_FRAME_MAX_BYTES", 512 * 1024) or 512 * 1024)
PROJECT_VISUAL_FRAME_MAX_TOTAL_BYTES = int(getattr(settings, "PROJECT_VISUAL_FRAME_MAX_TOTAL_BYTES", 3 * 1024 * 1024) or 3 * 1024 * 1024)
PROJECT_RUN_ASSET_SIGNED_URL_TTL_SECONDS = int(getattr(settings, "PROJECT_RUN_ASSET_SIGNED_URL_TTL_SECONDS", 15 * 60) or 15 * 60)
PROJECT_VISUAL_LABEL_PRIORITY = {
    "opening": 0,
    "first": 0,
    "start": 0,
    "intro": 0,
    "early": 1,
    "product": 2,
    "middle": 3,
    "proof": 3,
    "demo": 3,
    "ending": 4,
    "end": 4,
    "closing": 4,
    "last": 4,
}
PROJECT_ASSETS_DIR = Path(settings.SKILL_REPO_PATH) / "project-assets"
# Project-run files are durable business assets, not release artifacts. Keep
# them beside the persistent Skill workspace so immutable release worktrees can
# be switched without making older videos and evidence files disappear.
PROJECT_RUN_ASSETS_DIR = Path(settings.SKILL_REPO_PATH) / "project-run-assets"
PROJECT_STATIC_ENTRY_CANDIDATES = (
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
PROJECT_ASSET_SANDBOX_CSP = (
    "sandbox allow-scripts allow-forms allow-popups allow-downloads; "
    "default-src 'self' data: blob: https:; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'self'"
)
PROJECT_ASSET_SANDBOX_SUFFIXES = {".html", ".htm", ".svg", ".xml", ".xhtml"}
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
PROJECT_STATIC_REWRITE_SUFFIXES = {".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".svg"}
PROJECT_STATIC_REWRITE_MAX_BYTES = 5 * 1024 * 1024
PROJECT_GATEWAY_BOOTSTRAP_SNIPPET = (
    '<script src="/project-gateway-sdk.js"></script>\n'
    '<script src="/project-autowire.js" defer></script>'
)
PROJECT_GATEWAY_BOOTSTRAP_MARKERS = (
    "project-gateway-sdk.js",
    "project-autowire.js",
    "PlatformProjectGateway",
    "SkillForgeProject",
    "SFProjectGateway",
)
PROJECT_FRONTEND_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_api_key", re.compile(r"\b(?:VITE_|NEXT_PUBLIC_)?OPENAI[_-]?(?:API[_-]?)?KEY\b", re.IGNORECASE)),
    ("anthropic_api_key", re.compile(r"\b(?:VITE_|NEXT_PUBLIC_)?ANTHROPIC[_-]?(?:API[_-]?)?KEY\b", re.IGNORECASE)),
    ("gemini_api_key", re.compile(r"\b(?:VITE_|NEXT_PUBLIC_)?(?:GEMINI|GOOGLE)[_-]?(?:API[_-]?)?KEY\b", re.IGNORECASE)),
    ("dashscope_api_key", re.compile(r"\b(?:VITE_|NEXT_PUBLIC_)?DASHSCOPE[_-]?(?:API[_-]?)?KEY\b", re.IGNORECASE)),
    ("deepseek_api_key", re.compile(r"\b(?:VITE_|NEXT_PUBLIC_)?DEEPSEEK[_-]?(?:API[_-]?)?KEY\b", re.IGNORECASE)),
    ("llm_secret_token", re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{20,}\b")),
    ("bearer_llm_secret", re.compile(r"Authorization\s*[:=]\s*['\"]?Bearer\s+sk-[A-Za-z0-9_-]{8,}", re.IGNORECASE)),
)
PROJECT_FRONTEND_AI_ENDPOINT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_endpoint", re.compile(r"https?://api\.openai\.com/|api\.openai\.com/v1", re.IGNORECASE)),
    ("anthropic_endpoint", re.compile(r"https?://api\.anthropic\.com/|anthropic\.com/v1", re.IGNORECASE)),
    (
        "gemini_endpoint",
        re.compile(r"https?://generativelanguage\.googleapis\.com/|generativelanguage\.googleapis\.com/v1", re.IGNORECASE),
    ),
    ("dashscope_endpoint", re.compile(r"https?://dashscope\.aliyuncs\.com/|dashscope\.aliyuncs\.com/api/", re.IGNORECASE)),
    ("deepseek_endpoint", re.compile(r"https?://api\.deepseek\.com/|api\.deepseek\.com/v1", re.IGNORECASE)),
)
PROJECT_UNBUILT_FRONTEND_REF_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
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


def _setting_int(name: str, default: int, *, minimum: int = 1, maximum: int = 1_000_000) -> int:
    try:
        value = int(getattr(settings, name, default) or default)
    except Exception:
        value = default
    return min(max(value, minimum), maximum)


def _normalize_sha256(value: str | None) -> str:
    return str(value or "").strip().lower().removeprefix("sha256:")


def _package_limit_int(value: Any, default: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(parsed, minimum)


def _project_package_limits() -> dict[str, int]:
    return {
        "max_package_bytes": _package_limit_int(PROJECT_PACKAGE_MAX_BYTES, int(settings.MAX_UPLOAD_SIZE)),
        "max_files": _package_limit_int(PROJECT_PACKAGE_MAX_FILES, 2000),
        "max_extracted_bytes": _package_limit_int(PROJECT_PACKAGE_MAX_EXTRACTED_BYTES, int(settings.MAX_UPLOAD_SIZE) * 4),
        "max_manifest_bytes": _package_limit_int(PROJECT_PACKAGE_MAX_MANIFEST_BYTES, 128 * 1024),
    }


def _project_gateway_limits() -> dict[str, int]:
    return {
        "max_ingest_bytes": _package_limit_int(PROJECT_INGEST_MAX_BYTES, 512 * 1024),
        "max_input_event_bytes": _package_limit_int(PROJECT_INPUT_MAX_BYTES, 256 * 1024),
        "max_capability_input_bytes": _package_limit_int(PROJECT_CAPABILITY_MAX_INPUT_BYTES, 256 * 1024),
        "max_236_capability_input_bytes": _package_limit_int(getattr(settings, "PROJECT_236_CAPABILITY_MAX_INPUT_BYTES", 2 * 1024 * 1024), 2 * 1024 * 1024),
        "max_capability_calls_per_run": _package_limit_int(PROJECT_CAPABILITY_MAX_CALLS_PER_RUN, 100),
        "max_reports_per_ingest": _package_limit_int(PROJECT_MAX_REPORTS_PER_INGEST, 50),
        "max_todos_per_ingest": _package_limit_int(PROJECT_MAX_TODOS_PER_INGEST, 100),
        "max_proofs_per_ingest": _package_limit_int(PROJECT_MAX_PROOFS_PER_INGEST, 50),
        "max_asset_upload_bytes": _package_limit_int(PROJECT_RUN_ASSET_MAX_BYTES, 50 * 1024 * 1024),
        "asset_upload_stream_chunk_bytes": PROJECT_RUN_ASSET_STREAM_CHUNK_BYTES,
        "asset_upload_streaming": 1,
        # Honest capability boundary: multipart uploads are memory-bounded but
        # do not yet have a durable cross-request resume session.
        "asset_upload_resumable": 0,
    }


def _raise_package_too_large(reason: str, **detail: Any) -> None:
    raise AppError("PROJECT_PACKAGE_TOO_LARGE", 413, {"reason": reason, **detail})


def _raise_payload_too_large(reason: str, **detail: Any) -> None:
    raise AppError("PROJECT_PAYLOAD_TOO_LARGE", 413, {"reason": reason, **detail})


def _json_payload_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    except Exception:
        return len(str(value).encode("utf-8", errors="ignore"))


def _validate_package_bytes(package_bytes: bytes) -> None:
    limits = _project_package_limits()
    actual = len(package_bytes or b"")
    if actual <= 0:
        raise AppError("PROJECT_INVALID", 400, {"detail": "项目包为空"})
    if actual > limits["max_package_bytes"]:
        _raise_package_too_large(
            "package_bytes",
            actual_bytes=actual,
            max_bytes=limits["max_package_bytes"],
        )


def _safe_entry_path(value: str | None) -> str:
    text = str(value or "").strip().lstrip("/")
    if not text:
        return "index.html"
    pure = PurePosixPath(text)
    if any(part in {"", ".", ".."} or part.startswith(".") for part in pure.parts):
        raise AppError("PROJECT_INVALID", 400, {"field": "entry", "reason": "入口路径不允许隐藏目录或路径穿越"})
    return str(pure)


def _safe_project_entry_url(value: str | None, *, required: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise AppError("PROJECT_INVALID", 400, {"field": "entry_url", "reason": "项目入口 URL 不能为空"})
        return None
    if any(ord(ch) < 32 for ch in text) or any(ch.isspace() for ch in text):
        raise AppError("PROJECT_INVALID", 400, {"field": "entry_url", "reason": "项目入口 URL 不能包含空白或控制字符"})
    if text.startswith("/"):
        if text.startswith("//"):
            raise AppError("PROJECT_INVALID", 400, {"field": "entry_url", "reason": "平台相对入口不能使用 // 协议相对 URL"})
        parsed_relative = urlsplit(text)
        if parsed_relative.scheme or parsed_relative.netloc:
            raise AppError("PROJECT_INVALID", 400, {"field": "entry_url", "reason": "平台相对入口不能包含 scheme 或 host"})
        if any(part in {"", ".", ".."} or part.startswith(".") for part in PurePosixPath(parsed_relative.path).parts):
            raise AppError("PROJECT_INVALID", 400, {"field": "entry_url", "reason": "平台相对入口不允许隐藏目录或路径穿越"})
        return text[:4000]

    parsed = urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise AppError("PROJECT_INVALID", 400, {"field": "entry_url", "reason": "外部项目入口只允许 http/https URL 或平台相对路径"})
    if parsed.username or parsed.password:
        raise AppError("PROJECT_INVALID", 400, {"field": "entry_url", "reason": "项目入口 URL 不能包含用户名或密码"})
    return text[:4000]


def _detect_project_static_entry(root: Path, preferred: str | None = None) -> str:
    candidates = []
    if preferred:
        candidates.append(_safe_entry_path(preferred))
    candidates.extend(PROJECT_STATIC_ENTRY_CANDIDATES)
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (root / candidate).is_file():
            return candidate
    return _safe_entry_path(preferred) if preferred else PROJECT_STATIC_ENTRY_CANDIDATES[0]


def _should_fallback_to_project_entry(rel: str) -> bool:
    # Support browser-history SPA routes such as /projects/assets/<id>/<ver>/orders/42
    # while still returning 404 for missing JS/CSS/images. Routes with no file
    # suffix are treated as client-side routes.
    pure = PurePosixPath(rel)
    return not pure.suffix


def _project_asset_url(project_id: str, version_token: str, entry: str) -> str:
    return f"/api/projects/assets/{project_id}/{version_token}/{entry}"


def project_asset_response_headers(asset_path: str | Path) -> dict[str, str]:
    """Security headers for uploaded project assets.

    HTML/SVG documents are sandboxed even when opened as a top-level URL. This
    prevents same-origin uploaded JavaScript from using platform session cookies
    while still allowing the project page to communicate through postMessage.
    """

    headers = {
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    suffix = Path(str(asset_path or "")).suffix.lower()
    if suffix in PROJECT_ASSET_SANDBOX_SUFFIXES:
        headers["Content-Security-Policy"] = PROJECT_ASSET_SANDBOX_CSP
    return headers


def _new_id(prefix: str, size: int = 24) -> str:
    return f"{prefix}_{uuid4().hex[:size]}"


def _iso(value: datetime | None) -> str | None:
    return isoformat_bjt(value) if value else None


def _heartbeat_age_seconds(run: ProjectRun, now: datetime | None = None) -> int | None:
    last = run.last_heartbeat_at or run.started_at or run.created_at
    if not last:
        return None
    age = int(((now or now_bjt()) - last).total_seconds())
    return max(age, 0)


def _is_run_heartbeat_stale(run: ProjectRun, now: datetime | None = None, *, stale_after_seconds: int = PROJECT_HEARTBEAT_STALE_SECONDS) -> bool:
    status = str(run.status or "")
    if status == "stale":
        return True
    if status not in PROJECT_RUN_ACTIVE_STATUSES:
        return False
    age = _heartbeat_age_seconds(run, now)
    return age is not None and age > stale_after_seconds


def _run_liveness(run: ProjectRun, now: datetime | None = None) -> dict[str, Any]:
    age = _heartbeat_age_seconds(run, now)
    status = str(run.status or "none")
    if status in PROJECT_RUN_TERMINAL_STATUSES:
        state = "terminal"
    elif _is_run_heartbeat_stale(run, now):
        state = "stale"
    elif status in PROJECT_RUN_ACTIVE_STATUSES:
        state = "online"
    elif status == "waiting_ai":
        state = "waiting_ai"
    else:
        state = "unknown"
    return {
        "liveness": state,
        "heartbeat_age_seconds": age,
        "stale_after_seconds": PROJECT_HEARTBEAT_STALE_SECONDS,
    }


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_project_resident_gateway_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.lower().replace("_", "-")
    if normalized in PROJECT_RESIDENT_MODEL_GATEWAY_ALIASES:
        return PROJECT_RESIDENT_MODEL_GATEWAY_ID
    return text


def _project_resident_model_request(data: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    payload = _safe_dict(data.get("input"))
    target = (
        data.get("target_gateway_id")
        or data.get("targetGatewayId")
        or data.get("gateway_id")
        or data.get("gatewayId")
        or payload.get("target_gateway_id")
        or payload.get("targetGatewayId")
        or payload.get("gateway_id")
        or payload.get("gatewayId")
    )
    model = (
        data.get("model")
        or data.get("model_id")
        or data.get("modelId")
        or data.get("openwebui_model_id")
        or data.get("openwebuiModelId")
        or payload.get("model")
        or payload.get("model_id")
        or payload.get("modelId")
        or payload.get("openwebui_model_id")
        or payload.get("openwebuiModelId")
    )
    normalized_target = _normalize_project_resident_gateway_id(target)
    model_id = _text(model, limit=180)
    if not normalized_target and not model_id:
        return False, None, None
    if normalized_target and normalized_target != PROJECT_RESIDENT_MODEL_GATEWAY_ID:
        raise AppError(
            "PROJECT_RESIDENT_MODEL_GATEWAY_UNSUPPORTED",
            400,
            {
                "detail": "Project SDK 当前仅开放 236 macOS 平台节点的常驻模型。",
                "requested_gateway_id": normalized_target,
                "supported_gateway_id": PROJECT_RESIDENT_MODEL_GATEWAY_ID,
            },
        )
    if not model_id:
        raise AppError(
            "PROJECT_RESIDENT_MODEL_REQUIRED",
            422,
            {"detail": "调用 236 常驻模型需要 model 或 model_id。"},
        )
    return True, PROJECT_RESIDENT_MODEL_GATEWAY_ID, model_id


def _safe_project_resident_model_record(item: dict[str, Any], *, gateway_id: str) -> dict[str, Any] | None:
    model_id = _text(item.get("model") or item.get("id") or item.get("model_id"), limit=180)
    if not model_id:
        return None
    status = str(item.get("status") or "").strip().lower()
    loaded = item.get("loaded") is True or status in {"loaded", "ready", "running"}
    record = {
        "id": model_id,
        "model": model_id,
        "object": "model",
        "owned_by": "skillforge",
        "gateway_id": gateway_id,
        "display_name": _text(item.get("display_name") or item.get("name") or model_id, limit=180),
        "runtime_profile": _text(item.get("runtime_profile") or item.get("profile"), limit=80),
        "deployment_id": _text(item.get("deployment_id") or item.get("model_deployment_id"), limit=120),
        "job_id": _text(item.get("job_id") or item.get("training_job_id"), limit=120),
        "status": status or ("loaded" if loaded else "unknown"),
        "loaded": bool(loaded),
        "last_heartbeat_at": _text(item.get("last_heartbeat_at"), limit=80),
    }
    raw_context = (
        item.get("context_window")
        or item.get("context_length")
        or item.get("max_context_length")
        or item.get("num_ctx")
        or item.get("n_ctx")
    )
    if raw_context not in (None, ""):
        record["reported_context_window"] = _safe_int(
            raw_context,
            default=PROJECT_236_CONTEXT_FALLBACK_WINDOW_TOKENS,
            minimum=1024,
            maximum=1_000_000,
        )
    return record


def _project_resident_models_from_capabilities(capabilities: dict[str, Any], *, gateway_id: str) -> list[dict[str, Any]]:
    raw_items = []
    raw_items.extend(_safe_list(capabilities.get("resident_models")))
    training_capability = _safe_dict(capabilities.get("training"))
    raw_items.extend(_safe_list(training_capability.get("resident_models")))
    models: dict[str, dict[str, Any]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        record = _safe_project_resident_model_record(item, gateway_id=gateway_id)
        if record:
            models.setdefault(record["id"], record)
    return sorted(models.values(), key=lambda item: (not item.get("loaded"), str(item.get("id") or "")))


def _project_openai_tool_names(tools: Any) -> set[str]:
    names: set[str] = set()
    for tool in _safe_list(tools):
        record = _safe_dict(tool)
        function = _safe_dict(record.get("function"))
        name = _text(function.get("name") or record.get("name"), limit=120)
        if name:
            names.add(name)
    return names


def _project_openai_tools_prompt(tools: Any, tool_choice: Any = None) -> str:
    normalized = []
    for tool in _safe_list(tools):
        record = _safe_dict(tool)
        function = _safe_dict(record.get("function"))
        name = _text(function.get("name") or record.get("name"), limit=120)
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "description": _text(function.get("description") or record.get("description"), limit=600),
                "parameters": _redact_trace_value(_safe_dict(function.get("parameters") or record.get("parameters"))),
            }
        )
    if not normalized:
        return ""
    required_tool = ""
    choice_record = _safe_dict(tool_choice)
    if str(tool_choice or "").strip() not in {"", "auto", "none"}:
        required_tool = _text(_safe_dict(choice_record.get("function")).get("name") or choice_record.get("name"), limit=120)
    lines = [
        "工具协议：不要输出 <think>、推理过程或解释。需要调用工具时，最终回答只输出一行 tool_use(name=\"<tool_name>\", arguments={<JSON object>})。",
    ]
    if required_tool:
        lines.append(f"本次必须调用工具 {required_tool}，回答第一行必须是 tool_use(...)。")
    if tool_choice not in (None, "", "auto"):
        lines.append("tool_choice: " + json.dumps(_redact_trace_value(tool_choice), ensure_ascii=False, default=str)[:1000])
    for item in normalized:
        schema = json.dumps(item.get("parameters") or {}, ensure_ascii=False, separators=(",", ":"), default=str)
        detail = f"- {item['name']}"
        if item.get("description"):
            detail += f": {item['description']}"
        if schema and schema != "{}":
            detail += f" parameters={schema[:1200]}"
        lines.append(detail)
    return "\n".join(lines).strip()


def _project_openai_append_tools_prompt(base_prompt: str, tools: Any, tool_choice: Any = None) -> str:
    prompt = str(base_prompt or "").strip()
    tools_prompt = _project_openai_tools_prompt(tools, tool_choice)
    if not tools_prompt:
        return prompt
    if prompt:
        return f"{tools_prompt}\n\n{prompt}\n\n再次强调：如需工具或 tool_choice 指定工具，只输出 tool_use(...)。".strip()
    return tools_prompt


def _project_openai_messages_to_prompt(messages: Any, *, tools: Any = None, tool_choice: Any = None) -> str:
    if not isinstance(messages, list):
        return _project_openai_append_tools_prompt(str(messages or "").strip(), tools, tool_choice)
    parts: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").strip() or "user"
        role_label = role
        if role == "tool":
            tool_name = _text(item.get("name") or item.get("tool_call_id"), limit=120)
            if tool_name:
                role_label = f"tool({tool_name})"
        content = item.get("content")
        if isinstance(content, list):
            chunks = []
            for chunk in content:
                if isinstance(chunk, dict):
                    if chunk.get("type") in {None, "text"}:
                        chunks.append(str(chunk.get("text") or ""))
                else:
                    chunks.append(str(chunk or ""))
            text = "\n".join(part for part in chunks if part)
        else:
            text = str(content or "")
        text = text.strip()
        if not text and role == "assistant" and _safe_list(item.get("tool_calls")):
            tool_call_summaries = []
            for call in _safe_list(item.get("tool_calls")):
                function = _safe_dict(_safe_dict(call).get("function"))
                tool_call_summaries.append(
                    {
                        "id": _text(_safe_dict(call).get("id"), limit=120),
                        "name": _text(function.get("name"), limit=120),
                        "arguments": function.get("arguments"),
                    }
                )
            text = "tool_calls: " + json.dumps(_redact_trace_value(tool_call_summaries), ensure_ascii=False, default=str)
        if text:
            parts.append(f"{role_label}: {text}")
    return _project_openai_append_tools_prompt("\n".join(parts).strip(), tools, tool_choice)


def _project_236_estimate_tokens(text: Any) -> int:
    value = str(text or "")
    if not value:
        return 0
    cjk = 0
    non_space = 0
    for char in value:
        if char.isspace():
            continue
        non_space += 1
        if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff":
            cjk += 1
    asciiish = max(0, non_space - cjk)
    return max(1, ceil(cjk * 1.1 + asciiish / 3.5))


def _project_236_context_measurement(config: dict[str, Any], model_id: str) -> dict[str, Any]:
    models = _safe_dict(config.get("models")) or _safe_dict(config.get("data"))
    record = _safe_dict(models.get(model_id)) if models else _safe_dict(config.get(model_id))
    return record


def _project_236_context_metadata(config: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    model_id = str(model.get("id") or model.get("model") or "").strip()
    measurement = _project_236_context_measurement(config, model_id)
    status = str(
        measurement.get("context_status")
        or measurement.get("status")
        or ("verified" if measurement.get("verified_at") or measurement.get("context_verified_at") else "unverified")
    ).strip() or "unverified"
    measured_window = _safe_int(
        measurement.get("measured_context_window") or measurement.get("context_window") or measurement.get("max_stable_context_tokens"),
        default=0,
        minimum=0,
        maximum=1_000_000,
    )
    reported_window = _safe_int(
        model.get("reported_context_window") or model.get("context_window"),
        default=0,
        minimum=0,
        maximum=1_000_000,
    )
    context_window = measured_window or reported_window or PROJECT_236_CONTEXT_FALLBACK_WINDOW_TOKENS
    max_output_tokens = min(
        PROJECT_236_CONTEXT_MAX_OUTPUT_TOKENS,
        _safe_int(
            measurement.get("max_output_tokens") or model.get("max_output_tokens"),
            default=PROJECT_236_CONTEXT_MAX_OUTPUT_TOKENS,
            minimum=1,
            maximum=PROJECT_236_CONTEXT_MAX_OUTPUT_TOKENS,
        ),
    )
    measured_input = _safe_int(
        measurement.get("max_input_tokens") or measurement.get("measured_input_tokens") or measurement.get("recommended_max_input_tokens"),
        default=0,
        minimum=0,
        maximum=1_000_000,
    )
    available_input = max(1, context_window - max_output_tokens - PROJECT_236_CONTEXT_SAFETY_MARGIN_TOKENS)
    if measured_input:
        max_input_tokens = min(measured_input, available_input)
    elif status == "verified":
        max_input_tokens = available_input
    else:
        max_input_tokens = min(PROJECT_236_CONTEXT_UNVERIFIED_INPUT_TOKENS, available_input)
    recommended_input_tokens = _safe_int(
        measurement.get("recommended_input_tokens") or measurement.get("recommended_max_input_tokens"),
        default=min(max_input_tokens, PROJECT_236_CONTEXT_UNVERIFIED_INPUT_TOKENS if status != "verified" else int(max_input_tokens * 0.8)),
        minimum=1,
        maximum=max_input_tokens,
    )
    verified_at = (
        _text(measurement.get("context_verified_at") or measurement.get("verified_at") or measurement.get("measured_at"), limit=80)
        or None
    )
    return {
        "context_window": context_window,
        "measured_context_window": measured_window or None,
        "reported_context_window": reported_window or None,
        "max_input_tokens": max_input_tokens,
        "recommended_input_tokens": recommended_input_tokens,
        "max_output_tokens": max_output_tokens,
        "context_status": status,
        "context_verified_at": verified_at,
        "safety_margin_tokens": PROJECT_236_CONTEXT_SAFETY_MARGIN_TOKENS,
    }


def _project_236_trim_text_to_token_budget(text: str, max_input_tokens: int) -> str:
    value = str(text or "")
    if _project_236_estimate_tokens(value) <= max_input_tokens:
        return value
    keep_chars = max(1, int(len(value) * (max_input_tokens / max(_project_236_estimate_tokens(value), 1)) * 0.92))
    trimmed = value[-keep_chars:].lstrip()
    while trimmed and _project_236_estimate_tokens(trimmed) > max_input_tokens:
        trimmed = trimmed[max(1, len(trimmed) // 20):].lstrip()
    return trimmed


def _project_236_truncate_messages_to_budget(
    messages: list[Any],
    *,
    tools: Any,
    tool_choice: Any,
    max_input_tokens: int,
) -> tuple[str, list[Any]]:
    normalized = [item for item in messages if isinstance(item, dict)]
    system_messages = [item for item in normalized if str(item.get("role") or "").strip() == "system"]
    tail_messages = [item for item in normalized if str(item.get("role") or "").strip() != "system"]
    selected: list[Any] = []
    for item in reversed(tail_messages):
        candidate = [*system_messages, item, *selected]
        prompt = _project_openai_messages_to_prompt(candidate, tools=tools, tool_choice=tool_choice)
        if _project_236_estimate_tokens(prompt) <= max_input_tokens or not selected:
            selected.insert(0, item)
        else:
            break
    truncated_messages = [*system_messages, *selected]
    prompt = _project_openai_messages_to_prompt(truncated_messages, tools=tools, tool_choice=tool_choice)
    if _project_236_estimate_tokens(prompt) > max_input_tokens:
        prompt = _project_236_trim_text_to_token_budget(prompt, max_input_tokens)
    return prompt, truncated_messages


def _project_236_apply_context_budget(
    *,
    model_record: dict[str, Any],
    prompt: str,
    messages: list[Any],
    tools: Any,
    tool_choice: Any,
    max_tokens: int,
    requested_max_input_tokens: Any = None,
    truncation: Any = None,
) -> tuple[str, list[Any], dict[str, Any]]:
    context_window = _safe_int(
        model_record.get("context_window"),
        default=PROJECT_236_CONTEXT_FALLBACK_WINDOW_TOKENS,
        minimum=1024,
        maximum=1_000_000,
    )
    model_max_input = _safe_int(
        model_record.get("max_input_tokens"),
        default=PROJECT_236_CONTEXT_UNVERIFIED_INPUT_TOKENS,
        minimum=1,
        maximum=1_000_000,
    )
    requested_input = _safe_int(
        requested_max_input_tokens,
        default=model_max_input,
        minimum=1,
        maximum=model_max_input,
    )
    reserved_window_input = max(1, context_window - max_tokens - PROJECT_236_CONTEXT_SAFETY_MARGIN_TOKENS)
    max_input_tokens = min(model_max_input, requested_input, reserved_window_input)
    mode = str(truncation or "disabled").strip().lower() or "disabled"
    if mode not in {"disabled", "auto"}:
        raise AppError(
            "PROJECT_OPENAI_236_INVALID_REQUEST",
            422,
            {"detail": "truncation must be disabled or auto", "truncation": mode},
        )
    original_estimate = _project_236_estimate_tokens(prompt)
    truncated = False
    applied_prompt = prompt
    applied_messages = messages
    if original_estimate > max_input_tokens:
        if mode != "auto":
            raise AppError(
                "PROJECT_OPENAI_CONTEXT_OVERFLOW",
                413,
                {
                    "detail": "236 模型输入超过对外上下文限制；减少 messages，降低 max_tokens，或显式传 truncation=auto。",
                    "model": model_record.get("id") or model_record.get("model"),
                    "estimated_input_tokens": original_estimate,
                    "max_input_tokens": max_input_tokens,
                    "recommended_input_tokens": model_record.get("recommended_input_tokens"),
                    "context_window": context_window,
                    "requested_max_tokens": max_tokens,
                    "safety_margin_tokens": PROJECT_236_CONTEXT_SAFETY_MARGIN_TOKENS,
                    "context_status": model_record.get("context_status"),
                    "context_verified_at": model_record.get("context_verified_at"),
                    "error_category": "context_overflow",
                },
            )
        if messages:
            applied_prompt, applied_messages = _project_236_truncate_messages_to_budget(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                max_input_tokens=max_input_tokens,
            )
        else:
            applied_prompt = _project_236_trim_text_to_token_budget(prompt, max_input_tokens)
            applied_messages = messages
        truncated = True
    estimate = _project_236_estimate_tokens(applied_prompt)
    return applied_prompt, applied_messages, {
        "context_window": context_window,
        "max_input_tokens": max_input_tokens,
        "recommended_input_tokens": model_record.get("recommended_input_tokens"),
        "max_output_tokens": PROJECT_236_CONTEXT_MAX_OUTPUT_TOKENS,
        "requested_max_tokens": max_tokens,
        "estimated_input_tokens": estimate,
        "original_estimated_input_tokens": original_estimate,
        "safety_margin_tokens": PROJECT_236_CONTEXT_SAFETY_MARGIN_TOKENS,
        "truncation": mode,
        "truncated": truncated,
        "context_status": model_record.get("context_status"),
        "context_verified_at": model_record.get("context_verified_at"),
        "prompt_chars": len(applied_prompt),
    }


def _project_openai_literal(value: str) -> Any:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty literal")
    try:
        return json.loads(raw)
    except Exception:
        return ast.literal_eval(raw)


def _project_openai_literal_node(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except Exception:
        if isinstance(node, ast.Name):
            return node.id
        raise


def _project_openai_balanced_call_args(text: str, function_name: str = "tool_use") -> list[tuple[str, int, int]]:
    chunks: list[tuple[str, int, int]] = []
    pattern = re.compile(rf"\b{re.escape(function_name)}\s*\(")
    position = 0
    while True:
        match = pattern.search(text, position)
        if not match:
            break
        start = match.end()
        depth = 1
        quote_char = ""
        escaped = False
        index = start
        while index < len(text):
            char = text[index]
            if quote_char:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote_char:
                    quote_char = ""
            else:
                if char in {"'", '"'}:
                    quote_char = char
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        chunks.append((text[start:index], match.start(), index + 1))
                        position = index + 1
                        break
            index += 1
        else:
            position = start
    return chunks


def _project_openai_tool_call_from_record(
    value: Any,
    *,
    allowed_tool_names: set[str] | None = None,
    index: int = 0,
) -> dict[str, Any] | None:
    record = _safe_dict(value)
    if "tool_use" in record and isinstance(record.get("tool_use"), dict):
        record = _safe_dict(record.get("tool_use"))
    function = _safe_dict(record.get("function"))
    name = _text(record.get("name") or record.get("tool") or function.get("name"), limit=120)
    if not name:
        return None
    if allowed_tool_names and name not in allowed_tool_names:
        return None
    args_value = record.get("arguments")
    if args_value is None:
        args_value = record.get("args")
    if args_value is None:
        args_value = record.get("input")
    if args_value is None:
        args_value = record.get("parameters")
    if args_value is None:
        args_value = function.get("arguments")
    if isinstance(args_value, str):
        try:
            parsed_args = _project_openai_literal(args_value)
        except Exception:
            parsed_args = {"input": args_value}
    else:
        parsed_args = args_value
    if not isinstance(parsed_args, dict):
        parsed_args = {"value": parsed_args}
    arguments = json.dumps(parsed_args, ensure_ascii=False, separators=(",", ":"), default=str)
    call_hash = hashlib.sha256(f"{index}:{name}:{arguments}".encode("utf-8")).hexdigest()[:18]
    return {
        "id": f"call_sf_{call_hash}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


def _project_openai_tool_call_from_function_args(
    args_text: str,
    *,
    allowed_tool_names: set[str] | None = None,
    index: int = 0,
) -> dict[str, Any] | None:
    try:
        parsed = ast.parse(f"_tool_use({args_text})", mode="eval")
    except SyntaxError:
        return None
    expression = parsed.body
    if not isinstance(expression, ast.Call):
        return None
    record: dict[str, Any] = {}
    if expression.args:
        record["name"] = _project_openai_literal_node(expression.args[0])
    if len(expression.args) > 1:
        record["arguments"] = _project_openai_literal_node(expression.args[1])
    for keyword in expression.keywords:
        if not keyword.arg:
            continue
        try:
            record[keyword.arg] = _project_openai_literal_node(keyword.value)
        except Exception:
            continue
    return _project_openai_tool_call_from_record(record, allowed_tool_names=allowed_tool_names, index=index)


def _project_openai_json_tool_call_candidates(text: str) -> list[Any]:
    candidates: list[Any] = []
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL):
        block = match.group(1).strip()
        if not block:
            continue
        try:
            candidates.append(_project_openai_literal(block))
        except Exception:
            continue
    for match in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, flags=re.DOTALL):
        block = match.group(0).strip()
        if not any(key in block for key in ("name", "tool", "arguments", "tool_use")):
            continue
        try:
            candidates.append(_project_openai_literal(block))
        except Exception:
            continue
    return candidates


def _project_openai_parse_text_tool_calls(text: Any, *, tools: Any = None) -> list[dict[str, Any]]:
    content = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    allowed_tool_names = _project_openai_tool_names(tools)
    calls: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def append_call(call: dict[str, Any] | None) -> None:
        if not call:
            return
        function = _safe_dict(call.get("function"))
        signature = (str(function.get("name") or ""), str(function.get("arguments") or ""))
        if signature in seen:
            return
        seen.add(signature)
        calls.append(call)

    for index, (args_text, _start, _end) in enumerate(_project_openai_balanced_call_args(content)):
        append_call(
            _project_openai_tool_call_from_function_args(
                args_text,
                allowed_tool_names=allowed_tool_names,
                index=index,
            )
        )
    if calls:
        return calls
    for candidate in _project_openai_json_tool_call_candidates(content):
        records = candidate if isinstance(candidate, list) else [candidate]
        for record in records:
            append_call(
                _project_openai_tool_call_from_record(
                    record,
                    allowed_tool_names=allowed_tool_names,
                    index=len(calls),
                )
            )
    return calls


def _project_openai_usage_reliable_from_usage(usage: dict[str, Any] | None) -> bool:
    raw = _safe_dict(usage)
    return any(key in raw for key in ("prompt_tokens", "completion_tokens", "generated_tokens", "total_tokens"))


def _project_openai_completion_response(
    *,
    model_id: str,
    text: Any,
    response_id: str | None = None,
    finish_reason: str = "stop",
    usage: dict[str, Any] | None = None,
    tools: Any = None,
    usage_reliable: bool | None = None,
) -> dict[str, Any]:
    content = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    prompt_tokens = int(_safe_dict(usage).get("prompt_tokens") or 0)
    completion_tokens = int(_safe_dict(usage).get("completion_tokens") or _safe_dict(usage).get("generated_tokens") or 0)
    parsed_tool_calls = _project_openai_parse_text_tool_calls(content, tools=tools) if _safe_list(tools) else []
    message: dict[str, Any] = {"role": "assistant", "content": content}
    resolved_finish_reason = finish_reason or "stop"
    tool_call_compat: dict[str, Any] | None = None
    if parsed_tool_calls:
        message = {"role": "assistant", "content": None, "tool_calls": parsed_tool_calls}
        resolved_finish_reason = "tool_calls"
        tool_call_compat = {
            "mode": "parsed_from_text",
            "raw_tool_text_preview": redact_secret_text(content, limit=500),
        }
    response = {
        "id": response_id or "chatcmpl-sf-project-" + hashlib.sha256(f"{time()}:{model_id}".encode()).hexdigest()[:16],
        "object": "chat.completion",
        "created": int(time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": resolved_finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    response["_skillforge_usage_reliable"] = (
        bool(usage_reliable) if usage_reliable is not None else _project_openai_usage_reliable_from_usage(usage)
    )
    if tool_call_compat:
        response["_skillforge_tool_call_compat"] = tool_call_compat
    return response


@dataclass
class ProjectSdkPrincipal:
    token: ProjectSdkToken
    project: Project
    user: Any


SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(authorization|cookie|token|secret|password|credential|api[_-]?key|access[_-]?key|private[_-]?key)"
)


def _redact_trace_value(value: Any, *, depth: int = 0, max_depth: int = 6) -> Any:
    if depth > max_depth:
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 80:
                cleaned["_truncated_keys"] = max(len(value) - index, 0)
                break
            key_text = str(key)
            if SENSITIVE_KEY_PATTERN.search(key_text):
                cleaned[key_text] = "[REDACTED]"
            else:
                cleaned[key_text] = _redact_trace_value(item, depth=depth + 1, max_depth=max_depth)
        return cleaned
    if isinstance(value, list):
        return [_redact_trace_value(item, depth=depth + 1, max_depth=max_depth) for item in value[:80]]
    text = str(value) if isinstance(value, str) else None
    if text and len(text) > 4000:
        return text[:4000] + "...[TRUNCATED]"
    return value


def _text(value: Any, *, limit: int = 4000) -> str | None:
    text = str(value or "").strip()
    return text[:limit] or None


def _project_skill_id(project_id: str) -> str:
    return f"{PROJECT_SKILL_PREFIX}{project_id}"[:50]


def _is_global_user(user: User) -> bool:
    return role_matches_any(user, ("admin", "system_admin")) or bool(getattr(user, "can_view_all", False))


def _can_create_or_edit_role(user: User) -> bool:
    return role_matches_any(user, ("admin", "system_admin", "dept_admin", "aibp", "ai_engineer", "biz_owner"))


def _project_sdk_token_hash(raw_token: str) -> str:
    secret = str(settings.SECRET_KEY or "skillforge").encode("utf-8")
    return hmac.new(secret, str(raw_token or "").encode("utf-8"), hashlib.sha256).hexdigest()


def _project_sdk_token_status(row: ProjectSdkToken, *, now: datetime | None = None) -> str:
    now = now or now_bjt()
    if row.revoked_at:
        return "revoked"
    if row.expires_at and row.expires_at <= now:
        return "expired"
    return "active"


def _normalize_project_sdk_scopes(value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return list(PROJECT_SDK_DEFAULT_TOKEN_SCOPES)
    raw_items = value if isinstance(value, list) else [value]
    scopes = []
    for item in raw_items:
        scope = str(item or "").strip()
        if not scope:
            continue
        if scope == "*":
            return ["*"]
        if scope not in PROJECT_SDK_TOKEN_SCOPES:
            raise AppError("PROJECT_SDK_SCOPE_INVALID", 400, {"scope": scope})
        scopes.append(scope)
    return sorted(set(scopes)) or list(PROJECT_SDK_DEFAULT_TOKEN_SCOPES)


def _project_sdk_token_snapshot(user: User) -> dict[str, Any]:
    return {
        "id": str(getattr(user, "id", "") or ""),
        "username": str(getattr(user, "username", "") or ""),
        "name": str(getattr(user, "name", "") or ""),
        "role": str(getattr(user, "role", "") or "operator"),
        "department": _text(getattr(user, "department", None), limit=100),
        "can_view_all": bool(getattr(user, "can_view_all", False)),
        "is_active": bool(getattr(user, "is_active", True)),
        "state": str(getattr(user, "state", "") or "active"),
        "permissions_rev": int(getattr(user, "permissions_rev", 0) or 0),
    }


def _project_sdk_user_from_snapshot(snapshot: dict[str, Any]) -> Any:
    return SimpleNamespace(
        id=str(snapshot.get("id") or ""),
        username=str(snapshot.get("username") or snapshot.get("id") or "project-sdk"),
        name=str(snapshot.get("name") or snapshot.get("username") or "Project SDK"),
        role=str(snapshot.get("role") or "operator"),
        department=snapshot.get("department"),
        can_view_all=bool(snapshot.get("can_view_all")),
        is_active=bool(snapshot.get("is_active", True)),
        state=str(snapshot.get("state") or "active"),
        permissions_rev=int(snapshot.get("permissions_rev") or 0),
    )


def _serialize_project_sdk_token(row: ProjectSdkToken, *, include_plain_token: str | None = None) -> dict[str, Any]:
    status = _project_sdk_token_status(row)
    payload = {
        "id": row.id,
        "project_id": row.project_id,
        "name": row.name,
        "token_prefix": row.token_prefix,
        "scopes": list(row.scopes_json or []),
        "status": status,
        "created_by": row.created_by,
        "last_used_at": _iso(row.last_used_at),
        "expires_at": _iso(row.expires_at),
        "revoked_at": _iso(row.revoked_at),
        "metadata": _safe_dict(row.metadata_json),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if include_plain_token:
        payload["token"] = include_plain_token
        payload["token_returned_once"] = True
    return payload


def _ensure_project_sdk_scope(principal: ProjectSdkPrincipal, scope: str) -> None:
    scopes = set(principal.token.scopes_json or [])
    if "*" in scopes or scope in scopes:
        return
    raise AppError("PROJECT_SDK_SCOPE_DENIED", 403, {"scope": scope, "project_id": principal.project.id})


def _project_sdk_retry_after_seconds(info: dict[str, Any], *, default: int = 1) -> int:
    try:
        value = float(info.get("retry_after") or default)
    except Exception:
        value = float(default)
    return max(1, int(ceil(value)))


def _project_sdk_rate_limit_detail(
    *,
    kind: str,
    required_scope: str,
    info: dict[str, Any],
    window_seconds: int,
) -> dict[str, Any]:
    retry_after = _project_sdk_retry_after_seconds(info)
    return {
        "detail": "Project SDK token 调用频率已达到平台上限，请按 retry_after_seconds 退避后重试。",
        "error_category": "quota_error",
        "retry_after_seconds": retry_after,
        "scope": required_scope,
        "rate_limit": {
            "kind": kind,
            "limit": int(info.get("limit") or 0),
            "remaining": int(info.get("remaining") or 0),
            "window_seconds": window_seconds,
        },
    }


async def _ensure_project_sdk_token_rate_limits(row: ProjectSdkToken, *, required_scope: str) -> None:
    identity = f"{row.project_id}:{row.id}"
    checks = (
        ("qps", PROJECT_SDK_QPS_LIMITER, 1),
        ("daily", PROJECT_SDK_DAILY_LIMITER, 24 * 60 * 60),
    )
    for kind, limiter, window_seconds in checks:
        allowed, info = await limiter.check(identity)
        if allowed:
            continue
        logger.warning(
            "Project SDK token rate limited kind={} project_id={} token_prefix={} scope={} retry_after={}",
            kind,
            row.project_id,
            row.token_prefix,
            required_scope,
            info.get("retry_after"),
        )
        raise AppError(
            "PROJECT_SDK_RATE_LIMITED",
            429,
            _project_sdk_rate_limit_detail(
                kind=kind,
                required_scope=required_scope,
                info=info,
                window_seconds=window_seconds,
            ),
        )


def _project_sdk_236_concurrency_limit() -> int:
    try:
        value = int(getattr(settings, "PROJECT_SDK_CONCURRENT_236_CALLS_PER_TOKEN_MODEL", 4) or 4)
    except Exception:
        value = 4
    return max(1, min(value, 10_000))


def _project_sdk_internal_token_marker(data: dict[str, Any]) -> dict[str, str]:
    token_id = _text(data.get("_sdk_token_id"), limit=80)
    token_prefix = _text(data.get("_sdk_token_prefix"), limit=80)
    marker: dict[str, str] = {}
    if token_id:
        marker["sdk_token_id"] = token_id
    if token_prefix:
        marker["sdk_token_prefix"] = token_prefix
    return marker


async def _ensure_project_sdk_236_concurrency(
    db: AsyncSession,
    project: Project,
    *,
    model_id: str,
    sdk_token_id: str | None,
) -> None:
    if not sdk_token_id:
        return
    limit = _project_sdk_236_concurrency_limit()
    await acquire_xact_lock(db, f"project_sdk_236_concurrency:{project.id}:{sdk_token_id}:{model_id}")
    running_count = (
        await db.execute(
            select(func.count(ProjectCapabilityCall.id))
            .select_from(ProjectCapabilityCall)
            .join(ProjectRun, ProjectRun.id == ProjectCapabilityCall.project_run_id)
            .where(
                ProjectRun.project_id == project.id,
                ProjectCapabilityCall.capability_type == "ai_resident",
                ProjectCapabilityCall.status == "running",
                ProjectCapabilityCall.input_summary["sdk_token_id"].astext == sdk_token_id,
                ProjectCapabilityCall.input_summary["model"].astext == model_id,
            )
        )
    ).scalar_one()
    if int(running_count or 0) < limit:
        return
    logger.warning(
        "Project SDK 236 concurrency limited project_id={} token_id={} model={} running={} limit={}",
        project.id,
        sdk_token_id,
        model_id,
        running_count,
        limit,
    )
    raise AppError(
        "PROJECT_SDK_CONCURRENCY_LIMITED",
        429,
        {
            "detail": "同一 Project SDK token 对同一 236 模型的并发调用已达到平台上限。",
            "error_category": "quota_error",
            "retry_after_seconds": 2,
            "project_id": project.id,
            "model": model_id,
            "rate_limit": {
                "kind": "concurrency",
                "limit": limit,
                "running": int(running_count or 0),
                "window_seconds": 0,
            },
        },
    )


async def create_project_sdk_token(db: AsyncSession, user: User, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("PROJECT_NOT_FOUND", 404)
    if not await can_write_project(db, user, project):
        raise AppError("PROJECT_ACCESS_DENIED", 403)
    scopes = _normalize_project_sdk_scopes(data.get("scopes"))
    ttl_days = int(data.get("expires_in_days") or PROJECT_SDK_DEFAULT_TOKEN_TTL_DAYS)
    if ttl_days < 1 or ttl_days > 3660:
        raise AppError("PROJECT_SDK_TOKEN_INVALID", 400, {"field": "expires_in_days", "min": 1, "max": 3660})
    now = now_bjt()
    token_id = _new_id("psdk", 24)
    prefix = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]
    secret = secrets.token_urlsafe(32)
    raw_token = f"{PROJECT_SDK_TOKEN_PREFIX}_{prefix}_{secret}"
    metadata = _safe_dict(data.get("metadata"))
    row = ProjectSdkToken(
        id=token_id,
        project_id=project.id,
        name=(_text(data.get("name"), limit=120) or "Project SDK token"),
        token_prefix=f"{PROJECT_SDK_TOKEN_PREFIX}_{prefix}",
        token_hash=_project_sdk_token_hash(raw_token),
        scopes_json=scopes,
        created_by=str(getattr(user, "id", "") or "") or None,
        expires_at=now + timedelta(days=ttl_days),
        metadata_json={
            **metadata,
            "created_for": "project_gateway_sdk",
            "creator_snapshot": _project_sdk_token_snapshot(user),
        },
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    return _serialize_project_sdk_token(row, include_plain_token=raw_token)


async def list_project_sdk_tokens(db: AsyncSession, user: User, project_id: str) -> dict[str, Any]:
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("PROJECT_NOT_FOUND", 404)
    if not await can_write_project(db, user, project):
        raise AppError("PROJECT_ACCESS_DENIED", 403)
    rows = (
        await db.execute(
            select(ProjectSdkToken)
            .where(ProjectSdkToken.project_id == project.id)
            .order_by(ProjectSdkToken.created_at.desc())
        )
    ).scalars().all()
    return {"project_id": project.id, "items": [_serialize_project_sdk_token(row) for row in rows]}


async def revoke_project_sdk_token(db: AsyncSession, user: User, project_id: str, token_id: str) -> dict[str, Any]:
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("PROJECT_NOT_FOUND", 404)
    if not await can_write_project(db, user, project):
        raise AppError("PROJECT_ACCESS_DENIED", 403)
    row = await db.get(ProjectSdkToken, token_id)
    if not row or row.project_id != project.id:
        raise AppError("PROJECT_SDK_TOKEN_NOT_FOUND", 404)
    now = now_bjt()
    row.revoked_at = row.revoked_at or now
    row.updated_at = now
    await db.flush()
    return _serialize_project_sdk_token(row)


async def authenticate_project_sdk_token(
    db: AsyncSession,
    raw_token: str,
    *,
    required_scope: str,
    project_id: str | None = None,
) -> ProjectSdkPrincipal:
    token_hash = _project_sdk_token_hash(raw_token)
    row = (
        await db.execute(select(ProjectSdkToken).where(ProjectSdkToken.token_hash == token_hash).limit(1))
    ).scalar_one_or_none()
    if not row:
        raise AppError("PROJECT_SDK_AUTH_INVALID", 401)
    if project_id and row.project_id != project_id:
        raise AppError("PROJECT_SDK_PROJECT_DENIED", 403, {"project_id": project_id})
    now = now_bjt()
    status = _project_sdk_token_status(row, now=now)
    if status != "active":
        raise AppError("PROJECT_SDK_AUTH_INVALID", 401, {"status": status})
    project = await db.get(Project, row.project_id)
    if not project or project.status == "archived":
        raise AppError("PROJECT_NOT_FOUND", 404)
    snapshot = _safe_dict(_safe_dict(row.metadata_json).get("creator_snapshot"))
    user = None
    if row.created_by:
        user = await db.get(User, row.created_by)
        if user and (getattr(user, "state", None) == "disabled" or not getattr(user, "is_active", True)):
            raise AppError("AUTH_ACCOUNT_DISABLED", 403)
    principal_user = user or _project_sdk_user_from_snapshot(snapshot)
    principal = ProjectSdkPrincipal(token=row, project=project, user=principal_user)
    _ensure_project_sdk_scope(principal, required_scope)
    await _ensure_project_sdk_token_rate_limits(row, required_scope=required_scope)
    row.last_used_at = now
    row.updated_at = now
    await db.flush()
    return principal


def _system_config_value(row: SystemConfig | None, default: Any = None) -> Any:
    if row is None:
        return default
    value = row.value
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return default if value is None else value


async def _project_training_sink_config(db: AsyncSession, project: Project) -> dict[str, Any]:
    keys = [
        "project.training_sink.enabled",
        "project.training_sink.default_gateway_id",
        "project.training_sink.mode",
        "project.training_sink.include_failed_calls",
    ]
    rows = {
        row.key: row
        for row in (
            await db.execute(select(SystemConfig).where(SystemConfig.key.in_(keys)))
        ).scalars().all()
    }
    project_cfg = _safe_dict(_safe_dict(project.metadata_json).get("training_sink"))
    enabled = project_cfg.get("enabled")
    if enabled is None:
        enabled = _system_config_value(rows.get("project.training_sink.enabled"), True)
    target_gateway_id = (
        project_cfg.get("target_gateway_id")
        or project_cfg.get("default_gateway_id")
        or _system_config_value(rows.get("project.training_sink.default_gateway_id"), PROJECT_TRAINING_SINK_DEFAULT_GATEWAY_ID)
    )
    mode = project_cfg.get("mode") or _system_config_value(rows.get("project.training_sink.mode"), PROJECT_TRAINING_SINK_DEFAULT_MODE)
    include_failed = project_cfg.get("include_failed_calls")
    if include_failed is None:
        include_failed = _system_config_value(rows.get("project.training_sink.include_failed_calls"), True)
    return {
        "enabled": bool(enabled),
        "target_gateway_id": str(target_gateway_id or PROJECT_TRAINING_SINK_DEFAULT_GATEWAY_ID).strip(),
        "mode": str(mode or PROJECT_TRAINING_SINK_DEFAULT_MODE).strip() or PROJECT_TRAINING_SINK_DEFAULT_MODE,
        "include_failed_calls": bool(include_failed),
    }


async def _project_236_context_measurements_config(db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(select(SystemConfig).where(SystemConfig.key == PROJECT_236_CONTEXT_CONFIG_KEY))
    ).scalar_one_or_none()
    return _safe_dict(_system_config_value(row, {}))


def _project_sdk_dataset_ref(project: Project, now: datetime | None = None) -> str:
    stamp = (now or now_bjt()).strftime("%Y%m%d")
    return f"learning-artifacts://project/{project.id}/training/sdk/{stamp}"


def _serialize_project_training_sink_job(row: LearningIngestionJob | None, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    if row is None:
        cfg = config or {}
        return {
            "status": "disabled" if cfg.get("enabled") is False else "not_queued",
            "target_gateway_id": cfg.get("target_gateway_id"),
            "mode": cfg.get("mode") or PROJECT_TRAINING_SINK_DEFAULT_MODE,
        }
    policy = _safe_dict(row.policy_json)
    return {
        "status": row.status,
        "job_id": row.job_id,
        "dataset_ref": policy.get("dataset_ref"),
        "target_gateway_id": row.sink_id or policy.get("target_gateway_id"),
        "mode": policy.get("mode") or PROJECT_TRAINING_SINK_DEFAULT_MODE,
        "source_type": policy.get("source_type"),
        "source_id": policy.get("source_id"),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


async def enqueue_project_sdk_training_sink(
    db: AsyncSession,
    user: Any,
    project: Project,
    run: ProjectRun,
    *,
    source_type: str,
    source_id: str | int | None,
    source_status: str = "completed",
    request_id: str | None = None,
) -> dict[str, Any]:
    config = await _project_training_sink_config(db, project)
    if not config.get("enabled"):
        return _serialize_project_training_sink_job(None, config=config)
    if source_status != "completed" and not config.get("include_failed_calls", True):
        return {
            **_serialize_project_training_sink_job(None, config=config),
            "status": "skipped",
            "reason": "failed_calls_disabled",
        }
    target_gateway_id = str(config.get("target_gateway_id") or PROJECT_TRAINING_SINK_DEFAULT_GATEWAY_ID).strip()
    if not target_gateway_id:
        return {
            **_serialize_project_training_sink_job(None, config=config),
            "status": "skipped",
            "reason": "target_gateway_missing",
        }
    now = now_bjt()
    source_id_text = str(source_id or run.id)
    dataset_ref = _project_sdk_dataset_ref(project, now)
    job_hash = hashlib.sha256(
        f"{project.id}:{run.id}:{source_type}:{source_id_text}:{target_gateway_id}".encode("utf-8")
    ).hexdigest()[:32]
    job_id = f"ling_{job_hash}"
    existing = (
        await db.execute(select(LearningIngestionJob).where(LearningIngestionJob.job_id == job_id).limit(1))
    ).scalar_one_or_none()
    if existing:
        return _serialize_project_training_sink_job(existing)
    event = (
        await db.execute(
            select(LearningEvent)
            .where(LearningEvent.source_type == source_type, LearningEvent.source_id == source_id_text)
            .order_by(LearningEvent.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    policy = {
        "source": "project_gateway_sdk",
        "source_type": source_type,
        "source_id": source_id_text,
        "source_status": source_status,
        "project_id": project.id,
        "project_run_id": run.id,
        "execution_run_id": run.execution_run_id,
        "request_id": request_id or getattr(run, "request_id", None),
        "dataset_ref": dataset_ref,
        "target_gateway_id": target_gateway_id,
        "mode": config.get("mode") or PROJECT_TRAINING_SINK_DEFAULT_MODE,
        "format": "learning_artifacts_jsonl",
        "redaction": "skillforge_trace_redacted",
        "sample_selector": {
            "source_type": source_type,
            "source_id": source_id_text,
            "artifact_kinds": ["training_sample"],
        },
        "gb10_target": {
            "gateway_id": target_gateway_id,
            "label": "GB10 237 · NVIDIA GB10" if target_gateway_id == PROJECT_TRAINING_SINK_DEFAULT_GATEWAY_ID else target_gateway_id,
        },
        "credential_policy": "platform_only",
    }
    row = LearningIngestionJob(
        job_id=job_id,
        event_id=event.id if event else None,
        artifact_id=None,
        sink_type="training_gateway_dataset",
        sink_id=target_gateway_id,
        department=project.department,
        org_unit_id=project.department_id,
        skill_id=_project_skill_id(project.id),
        action="project_sdk_training_sink",
        status="pending",
        attempts=0,
        policy_json=policy,
        created_by=str(getattr(user, "id", "") or "") or None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    return _serialize_project_training_sink_job(row)


async def _department_name(db: AsyncSession, department_id: str | None) -> str | None:
    if not department_id:
        return None
    row = await db.get(OrgUnit, department_id)
    return row.name if row else None


async def _default_department(db: AsyncSession, user: User) -> tuple[str | None, str | None]:
    department_id = await get_primary_department_id(db, user)
    department = await _department_name(db, department_id)
    if not department:
        department = _text(getattr(user, "department", None), limit=100)
    return department_id, department


async def _user_matches_department(db: AsyncSession, user: User, project: Project) -> bool:
    if project.department_id:
        accessible = await get_accessible_departments(db, user)
        if accessible is None:
            return True
        readable = await expand_department_ancestors(db, accessible)
        return project.department_id in readable
    user_dept = _text(getattr(user, "department", None), limit=100)
    return bool(project.department and user_dept and project.department == user_dept)


async def can_read_project(db: AsyncSession, user: User, project: Project) -> bool:
    if _is_global_user(user):
        return True
    if project.owner_user_id and str(project.owner_user_id) == str(getattr(user, "id", "")):
        return True
    if project.visibility == "company" and project.status != "archived":
        return True
    if project.visibility == "department" and project.status != "archived":
        return await _user_matches_department(db, user, project)
    return False


async def can_write_project(db: AsyncSession, user: User, project: Project | None = None, department_id: str | None = None) -> bool:
    if _is_global_user(user):
        return True
    if project and project.owner_user_id and str(project.owner_user_id) == str(getattr(user, "id", "")):
        return True
    if not _can_create_or_edit_role(user):
        return False
    target_department_id = department_id or (project.department_id if project else None)
    if target_department_id:
        return await can_access_department(db, user, target_department_id)
    # 历史数据没有 org_unit_id 时按展示部门兜底。
    if project and project.department:
        return project.department == _text(getattr(user, "department", None), limit=100)
    return role_matches_any(user, ("aibp", "ai_engineer", "biz_owner", "dept_admin"))


async def can_read_project_run(db: AsyncSession, user: User, project: Project, run: ProjectRun) -> bool:
    if _is_global_user(user):
        return True
    user_id = str(getattr(user, "id", "") or "")
    if user_id and run.user_id and str(run.user_id) == user_id:
        return True
    return await can_write_project(db, user, project)


async def can_write_project_run(db: AsyncSession, user: User, project: Project, run: ProjectRun) -> bool:
    # Runs are user-scoped. Project owners/admin roles can operate for support;
    # ordinary users in the same department/company may open their own run but
    # cannot mutate or replay someone else's input/output/AI calls by guessing
    # a project_run_id.
    return await can_read_project_run(db, user, project, run)


def _company_sdk_project_metadata(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = _safe_dict(existing)
    capabilities = set(_safe_list(metadata.get("capabilities")))
    capabilities.update({"ai.chat", "ai.generate", "ai.analyze"})
    metadata.update(
        {
            "capabilities": sorted(str(item) for item in capabilities if str(item or "").strip()),
            "source": metadata.get("source") or "sf_sdk_check_page",
            "sdk_check": {
                **_safe_dict(metadata.get("sdk_check")),
                "enabled": True,
                "openai_236_models_url": "/api/projects/openai/236/v1/models",
                "openai_236_chat_url": "/api/projects/openai/236/v1/chat/completions",
                "project_sdk_url": "/api/projects/sdk/*",
            },
            "training_sink": {
                **_safe_dict(metadata.get("training_sink")),
                "target_gateway_id": PROJECT_TRAINING_SINK_DEFAULT_GATEWAY_ID,
                "mode": PROJECT_TRAINING_SINK_DEFAULT_MODE,
            },
        }
    )
    return metadata


async def ensure_company_sdk_project(db: AsyncSession, user: User) -> dict[str, Any]:
    if not _can_create_or_edit_role(user):
        raise AppError("PROJECT_ACCESS_DENIED", 403)
    now = now_bjt()
    project = await db.get(Project, PROJECT_COMPANY_SDK_PROJECT_ID)
    if project:
        if not await can_write_project(db, user, project):
            raise AppError("PROJECT_ACCESS_DENIED", 403)
        project.name = project.name or PROJECT_COMPANY_SDK_PROJECT_NAME
        project.description = project.description or "公司后端统一 Project SDK 与 236 常驻模型验证入口。"
        project.type = "internal_tool"
        project.visibility = "company"
        project.status = "published"
        project.department_id = None
        project.department = None
        project.metadata_json = _company_sdk_project_metadata(project.metadata_json)
        project.updated_at = now
    else:
        project = Project(
            id=PROJECT_COMPANY_SDK_PROJECT_ID,
            name=PROJECT_COMPANY_SDK_PROJECT_NAME,
            description="公司后端统一 Project SDK 与 236 常驻模型验证入口。",
            type="internal_tool",
            department_id=None,
            department=None,
            owner_user_id=str(getattr(user, "id", "") or "") or None,
            visibility="company",
            status="published",
            entry_url=None,
            metadata_json=_company_sdk_project_metadata({}),
            created_at=now,
            updated_at=now,
        )
        db.add(project)
    await db.flush()
    return await _serialize_project(db, user, project)


def _project_236_capabilities(instance: OpenClawInstance) -> dict[str, Any]:
    raw = getattr(instance, "bridge_capabilities_json", None)
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _project_236_gateway_instance(db: AsyncSession) -> OpenClawInstance:
    instance = await db.get(OpenClawInstance, PROJECT_RESIDENT_MODEL_GATEWAY_ID)
    if not instance or not getattr(instance, "is_active", True):
        raise AppError(
            "PROJECT_RESIDENT_MODEL_GATEWAY_NOT_FOUND",
            404,
            {
                "detail": "236 macOS 平台节点尚未注册或未启用。",
                "gateway_id": PROJECT_RESIDENT_MODEL_GATEWAY_ID,
            },
        )
    return instance


async def get_project_236_model_catalog(
    db: AsyncSession,
    user: User | Any | None = None,
    *,
    include_live_status: bool = True,
) -> dict[str, Any]:
    instance = await _project_236_gateway_instance(db)
    capabilities = _project_236_capabilities(instance)
    online = bridge_registry.is_online(instance.id)
    models_by_id = {
        item["id"]: {**item, "source": "bridge_capabilities", "callable": bool(online)}
        for item in _project_resident_models_from_capabilities(capabilities, gateway_id=instance.id)
    }
    live_error: str | None = None
    if online and include_live_status:
        try:
            live_status = await AIClawClient(
                instance.id,
                gateway_kind=instance.bridge_gateway_kind,
            ).get_openwebui_status({}, timeout=30)
            live_models = _safe_list(_safe_dict(live_status).get("models"))
            for raw_item in live_models:
                if not isinstance(raw_item, dict):
                    continue
                record = _safe_project_resident_model_record(raw_item, gateway_id=instance.id)
                if not record:
                    continue
                existing = models_by_id.get(record["id"], {})
                loaded = bool(existing.get("loaded") or record.get("loaded"))
                models_by_id[record["id"]] = {
                    **record,
                    **{k: v for k, v in existing.items() if v not in (None, "", [])},
                    "loaded": loaded,
                    "status": str(existing.get("status") or record.get("status") or ("loaded" if loaded else "registered")),
                    "source": "openwebui_status",
                    "callable": True,
                }
        except Exception as exc:  # noqa: BLE001
            live_error = _text(getattr(exc, "message", None) or str(exc), limit=1000)
            logger.warning("读取 236 OpenWebUI 模型目录失败: {}", live_error)

    context_config = await _project_236_context_measurements_config(db)
    models = [
        {
            **item,
            **_project_236_context_metadata(context_config, item),
        }
        for item in models_by_id.values()
    ]
    models = sorted(models, key=lambda item: (not bool(item.get("callable")), not bool(item.get("loaded")), str(item.get("id") or "")))
    return {
        "object": "list",
        "gateway": {
            "id": instance.id,
            "name": instance.name,
            "online": bool(online),
            "bridge_platform": instance.bridge_platform,
            "bridge_gateway_kind": instance.bridge_gateway_kind or instance.agent_type,
            "resident_required": True,
            "credential_location": "platform_only",
            "context_measurements_key": PROJECT_236_CONTEXT_CONFIG_KEY,
        },
        "data": models,
        "models": models,
        "total": len(models),
        "callable_count": sum(1 for item in models if item.get("callable")),
        "loaded_count": sum(1 for item in models if item.get("loaded")),
        "live_status_error": live_error,
    }


async def _resolve_project_236_model_for_call(
    db: AsyncSession,
    user: User | Any,
    model_id: str,
) -> tuple[OpenClawInstance, dict[str, Any]]:
    instance = await _project_236_gateway_instance(db)
    if not bridge_registry.is_online(instance.id):
        raise AppError(
            "PROJECT_RESIDENT_MODEL_GATEWAY_OFFLINE",
            503,
            {
                "detail": "236 macOS 平台节点 Bridge 当前离线，无法调用常驻模型。",
                "gateway_id": instance.id,
            },
        )
    catalog = await get_project_236_model_catalog(db, user, include_live_status=True)
    wanted = str(model_id or "").strip()
    for item in _safe_list(catalog.get("data")):
        if str(_safe_dict(item).get("id") or "") == wanted:
            return instance, _safe_dict(item)
    raise AppError(
        "PROJECT_RESIDENT_MODEL_NOT_FOUND",
        404,
        {
            "detail": "236 macOS 平台节点未上报该模型，不能代调用。",
            "gateway_id": instance.id,
            "model": wanted,
            "available_models": [str(_safe_dict(item).get("id") or "") for item in _safe_list(catalog.get("data"))[:30]],
            "total": catalog.get("total"),
        },
    )


def _project_permissions(project: Project, *, can_write: bool) -> dict[str, bool]:
    published = project.status == "published"
    return {
        "read": True,
        "launch": project.status != "archived",
        "edit": can_write,
        "submit": can_write,
        "analyze": project.status != "archived",
        "publish": can_write and not published,
        "archive": can_write and project.status != "archived",
    }


def _latest_run_to_dict(run: ProjectRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    now = now_bjt()
    return {
        "id": run.id,
        "execution_run_id": run.execution_run_id,
        "request_id": getattr(run, "request_id", None),
        "decision_log_id": run.decision_log_id,
        "status": run.status,
        **_run_liveness(run, now),
        "report_count": _report_count(run),
        "todo_count": _todo_count(run),
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "last_heartbeat_at": _iso(run.last_heartbeat_at),
        "completed_at": _iso(run.completed_at),
        "updated_at": _iso(run.updated_at),
    }


async def _serialize_project(
    db: AsyncSession,
    user: User,
    project: Project,
    *,
    latest_run: ProjectRun | None = None,
    include_permissions: bool = True,
) -> dict[str, Any]:
    can_write = await can_write_project(db, user, project) if include_permissions else False
    if latest_run and not await can_read_project_run(db, user, project, latest_run):
        latest_run = None
    item = {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "type": project.type,
        "department_id": project.department_id,
        "department": project.department,
        "owner_user_id": project.owner_user_id,
        "visibility": project.visibility,
        "status": project.status,
        "entry_url": project.entry_url,
        "current_version_id": project.current_version_id,
        "metadata": project.metadata_json or {},
        "created_at": _iso(project.created_at),
        "updated_at": _iso(project.updated_at),
        "runtime": {
            "mode": "browser_hosted",
            "containerized": False,
            "gateway": "skillforge_project_gateway",
            "sdk_path": "/project-gateway-sdk.js",
            "gateway_limits": _project_gateway_limits(),
        },
        "latest_run": _latest_run_to_dict(latest_run),
    }
    if include_permissions:
        item["permissions"] = _project_permissions(project, can_write=can_write)
    return item


def _playbook_project(pb: dict[str, Any]) -> dict[str, Any]:
    file_name = str(pb.get("file_name") or pb.get("name") or "").strip()
    return {
        "id": f"playbook:{file_name}",
        "name": pb.get("name") or file_name,
        "description": pb.get("description") or "",
        "type": "playbook",
        "department_id": None,
        "department": pb.get("department") or None,
        "owner_user_id": None,
        "visibility": "department" if pb.get("department") else "company",
        "status": "published" if not pb.get("parse_error") else "draft",
        "entry_url": f"/playbook/{file_name}" if file_name else None,
        "current_version_id": None,
        "metadata": {
            "source": "playbook",
            "file_name": file_name,
            "steps_count": pb.get("steps_count", 0),
            "trigger": pb.get("trigger"),
            "sla_minutes": pb.get("sla_minutes"),
            "modified_at": pb.get("modified_at"),
            "parse_error": bool(pb.get("parse_error")),
        },
        "runtime": {
            "mode": "playbook_workbench",
            "containerized": False,
            "gateway": "playbook_executor",
        },
        "latest_run": None,
        "permissions": {"read": True, "launch": True, "edit": True, "submit": False, "analyze": False, "publish": False, "archive": False},
        "created_at": None,
        "updated_at": pb.get("modified_at"),
    }


async def _visible_playbook_projects(user: User) -> list[dict[str, Any]]:
    user_dept = _text(getattr(user, "department", None), limit=100)
    global_user = _is_global_user(user)
    items: list[dict[str, Any]] = []
    try:
        playbooks = await playbook_service.list_playbooks()
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 Playbook 项目失败: {}", exc)
        return items
    for pb in playbooks:
        project = _playbook_project(pb)
        dept = project.get("department")
        if global_user or not dept or dept == user_dept:
            items.append(project)
    return items


def _scope_matches(item: dict[str, Any], scope: str | None, user: User) -> bool:
    scope = (scope or "all").strip().lower()
    if scope in {"all", ""}:
        return True
    if scope == "mine":
        return item.get("owner_user_id") == getattr(user, "id", None)
    if scope == "department":
        return bool(item.get("department") and item.get("department") == getattr(user, "department", None))
    if scope == "company":
        return item.get("visibility") == "company"
    if scope == "playbook":
        return item.get("type") == "playbook"
    return item.get("type") == scope


async def _latest_runs_by_project(db: AsyncSession, project_ids: list[str]) -> dict[str, ProjectRun]:
    if not project_ids:
        return {}
    ranked = (
        select(
            ProjectRun.id.label("id"),
            func.row_number()
            .over(partition_by=ProjectRun.project_id, order_by=ProjectRun.created_at.desc())
            .label("rank"),
        )
        .where(ProjectRun.project_id.in_(project_ids))
        .subquery()
    )
    rows = (
        await db.execute(
            select(ProjectRun)
            .where(ProjectRun.id.in_(select(ranked.c.id).where(ranked.c.rank == 1)))
        )
    ).scalars().all()
    return {row.project_id: row for row in rows}


async def _latest_visible_runs_by_project(
    db: AsyncSession,
    user: User,
    projects: list[Project],
) -> dict[str, ProjectRun]:
    """Return the latest run a user may resume from the project directory.

    The absolute latest run for a company/department project can belong to
    another ordinary user. Project cards must not expose that run, but they
    should still show the current user's own latest run so clicking the card
    can resume their existing project session instead of opening a blank one.
    """

    if not projects:
        return {}
    latest = await _latest_runs_by_project(db, [project.id for project in projects])
    visible: dict[str, ProjectRun] = {}
    fallback_project_ids: list[str] = []
    user_id = str(getattr(user, "id", "") or "")
    for project in projects:
        run = latest.get(project.id)
        if run and await can_read_project_run(db, user, project, run):
            visible[project.id] = run
            continue
        if user_id:
            fallback_project_ids.append(project.id)

    if fallback_project_ids:
        ranked = (
            select(
                ProjectRun.id.label("id"),
                func.row_number()
                .over(partition_by=ProjectRun.project_id, order_by=ProjectRun.created_at.desc())
                .label("rank"),
            )
            .where(ProjectRun.project_id.in_(fallback_project_ids), ProjectRun.user_id == user_id)
            .subquery()
        )
        own_runs = (
            await db.execute(
                select(ProjectRun).where(ProjectRun.id.in_(select(ranked.c.id).where(ranked.c.rank == 1)))
            )
        ).scalars().all()
        for run in own_runs:
            visible[run.project_id] = run

    return visible


def _report_count(run: ProjectRun | None) -> int:
    if not run:
        return 0
    direct = getattr(run, "report_count", None)
    if direct is not None:
        return int(direct or 0)
    return len(_safe_list(_safe_dict(run.output_result).get("reports")))


def _todo_count(run: ProjectRun | None) -> int:
    if not run:
        return 0
    direct = getattr(run, "todo_count", None)
    if direct is not None:
        return int(direct or 0)
    return len(_safe_list(_safe_dict(run.output_result).get("todos")))


def _run_bucket(status: str | None) -> str:
    raw = str(status or "none")
    if raw in {"opening", "running"}:
        return "running"
    if raw == "stale":
        return "stale"
    if raw == "waiting_ai":
        return "waiting_ai"
    if raw in {"ai_completed", "completed"}:
        return "ai_completed"
    if raw in {"ai_failed", "failed"}:
        return "failed"
    return "none"


def _run_filter_matches(status: str | None, run_status: str | None) -> bool:
    expected = str(run_status or "all").strip().lower()
    if expected in {"", "all"}:
        return True
    return _run_bucket(status) == expected


def _summarize_projects(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_department: dict[str, int] = {}
    running = 0
    waiting_ai = 0
    stale = 0
    reports = 0
    todos = 0
    for item in items:
        typ = str(item.get("type") or "unknown")
        status = str(item.get("status") or "unknown")
        dept = str(item.get("department") or "未分配")
        by_type[typ] = by_type.get(typ, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        by_department[dept] = by_department.get(dept, 0) + 1
        latest = _safe_dict(item.get("latest_run"))
        run_status = str(latest.get("status") or "")
        if run_status in {"opening", "running"}:
            running += 1
        if run_status == "stale" or str(latest.get("liveness") or "") == "stale":
            stale += 1
        if run_status in PROJECT_AI_STATES or run_status == "waiting_ai":
            waiting_ai += 1
        reports += int(latest.get("report_count") or 0)
        todos += int(latest.get("todo_count") or 0)
    return {
        "total": len(items),
        "by_type": by_type,
        "by_status": by_status,
        "by_department": by_department,
        "running": running,
        "stale": stale,
        "waiting_ai": waiting_ai,
        "report_count": reports,
        "todo_count": todos,
    }


def _project_summary_item(project: Project, run: ProjectRun | None) -> dict[str, Any]:
    return {
        "type": project.type,
        "status": project.status,
        "department": project.department,
        "latest_run": _latest_run_to_dict(run) or {},
    }


def _summarize_project_rows(
    projects: list[Project],
    latest_runs: dict[str, ProjectRun],
    playbooks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items = [_project_summary_item(project, latest_runs.get(project.id)) for project in projects]
    items.extend(playbooks or [])
    return _summarize_projects(items)


def _summarize_project_pairs(
    rows: list[tuple[Project, ProjectRun | None]],
    playbooks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items = [_project_summary_item(project, run) for project, run in rows]
    items.extend(playbooks or [])
    return _summarize_projects(items)


def _summarize_project_stat_values(rows: list[tuple[Any, Any, Any, Any, Any, Any]], playbooks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for project_type, project_status, department, run_status, report_count, todo_count in rows:
        items.append(
            {
                "type": project_type,
                "status": project_status,
                "department": department,
                "latest_run": {
                    "status": run_status,
                    "report_count": int(report_count or 0),
                    "todo_count": int(todo_count or 0),
                },
            }
        )
    items.extend(playbooks or [])
    return _summarize_projects(items)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _project_search_condition(search: str | None):
    q = str(search or "").strip()
    if not q:
        return None
    pattern = f"%{_escape_like(q)}%"
    return or_(
        Project.id.ilike(pattern, escape="\\"),
        Project.name.ilike(pattern, escape="\\"),
        Project.description.ilike(pattern, escape="\\"),
        Project.department.ilike(pattern, escape="\\"),
        Project.type.ilike(pattern, escape="\\"),
    )


async def _project_access_condition(db: AsyncSession, user: User):
    if _is_global_user(user):
        return None
    user_id = str(getattr(user, "id", "") or "")
    user_dept = _text(getattr(user, "department", None), limit=100)
    accessible_departments = await get_accessible_departments(db, user)
    dept_conditions = []
    if accessible_departments is None:
        dept_conditions.append(Project.visibility == "department")
    elif accessible_departments:
        readable_departments = await expand_department_ancestors(db, accessible_departments)
        dept_conditions.append(Project.department_id.in_(sorted(readable_departments)))
    if user_dept:
        dept_conditions.append(Project.department == user_dept)

    conditions = []
    if user_id:
        conditions.append(Project.owner_user_id == user_id)
    conditions.append(and_(Project.visibility == "company", Project.status != "archived"))
    if dept_conditions:
        conditions.append(and_(Project.visibility == "department", Project.status != "archived", or_(*dept_conditions)))
    return or_(*conditions) if conditions else false()


async def _project_run_read_condition(db: AsyncSession, user: User):
    """SQL condition for ProjectRun rows the current user may inspect/mutate.

    Project visibility is broader than run visibility: ordinary users can see a
    company/department project but must not see or update another user's
    ProjectRun. Project owners and writable department roles keep support
    access to all runs for projects they can edit.
    """

    if _is_global_user(user):
        return None
    user_id = str(getattr(user, "id", "") or "")
    conditions = []
    if user_id:
        conditions.append(ProjectRun.user_id == user_id)
        conditions.append(Project.owner_user_id == user_id)
    if _can_create_or_edit_role(user):
        writable_project_conditions = []
        accessible_departments = await get_accessible_departments(db, user)
        if accessible_departments is None:
            writable_project_conditions.append(Project.department_id.is_not(None))
        elif accessible_departments:
            writable_project_conditions.append(Project.department_id.in_(list(accessible_departments)))
        user_dept = _text(getattr(user, "department", None), limit=100)
        if user_dept:
            writable_project_conditions.append(Project.department == user_dept)
        writable_project_conditions.append(and_(Project.department_id.is_(None), Project.department.is_(None)))
        conditions.append(or_(*writable_project_conditions))
    return or_(*conditions) if conditions else false()


def _project_scope_condition(scope: str | None, user: User):
    normalized = str(scope or "all").strip().lower()
    if normalized in {"", "all"}:
        return None
    if normalized == "playbook":
        return false()
    if normalized == "mine":
        return Project.owner_user_id == str(getattr(user, "id", "") or "")
    if normalized == "department":
        user_dept = _text(getattr(user, "department", None), limit=100)
        return Project.department == user_dept if user_dept else false()
    if normalized == "company":
        return Project.visibility == "company"
    return Project.type == normalized


def _project_department_condition(department_id: str | None = None, department: str | None = None):
    dept_id = _text(department_id, limit=50)
    dept = _text(department, limit=100)
    conditions = []
    if dept_id:
        conditions.append(Project.department_id == dept_id)
    if dept:
        conditions.append(Project.department == dept)
    if not conditions:
        return None
    return and_(*conditions)


def _latest_run_ranked_subquery():
    return (
        select(
            ProjectRun.id.label("id"),
            ProjectRun.project_id.label("project_id"),
            func.row_number()
            .over(partition_by=ProjectRun.project_id, order_by=ProjectRun.created_at.desc())
            .label("rank"),
        )
        .subquery()
    )


async def _visible_latest_run_ranked_subquery(db: AsyncSession, user: User):
    stmt = (
        select(
            ProjectRun.id.label("id"),
            ProjectRun.project_id.label("project_id"),
            func.row_number()
            .over(partition_by=ProjectRun.project_id, order_by=ProjectRun.created_at.desc())
            .label("rank"),
        )
        .select_from(ProjectRun)
        .join(Project, Project.id == ProjectRun.project_id)
    )
    run_read_condition = await _project_run_read_condition(db, user)
    if run_read_condition is not None:
        stmt = stmt.where(run_read_condition)
    return stmt.subquery()


def _latest_run_join(stmt, latest_run_ranked):
    return (
        stmt.outerjoin(
            latest_run_ranked,
            and_(latest_run_ranked.c.project_id == Project.id, latest_run_ranked.c.rank == 1),
        )
        .outerjoin(ProjectRun, ProjectRun.id == latest_run_ranked.c.id)
    )


def _latest_run_status_condition(run_status: str | None):
    expected = str(run_status or "all").strip().lower()
    if expected in {"", "all"}:
        return None
    if expected == "running":
        return ProjectRun.status.in_(["opening", "running"])
    if expected == "stale":
        return ProjectRun.status == "stale"
    if expected == "waiting_ai":
        return ProjectRun.status == "waiting_ai"
    if expected == "ai_completed":
        return ProjectRun.status.in_(["ai_completed", "completed"])
    if expected == "failed":
        return ProjectRun.status.in_(["ai_failed", "failed"])
    return false()


async def _mark_project_run_stale_if_needed(
    db: AsyncSession,
    run: ProjectRun | None,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = PROJECT_HEARTBEAT_STALE_SECONDS,
) -> bool:
    if not run:
        return False
    now = now or now_bjt()
    if not _is_run_heartbeat_stale(run, now, stale_after_seconds=stale_after_seconds):
        return False
    if run.status == "stale":
        return False
    run.status = "stale"
    run.updated_at = now
    run.error = run.error or "项目运行心跳超时，网页可能已离线。"
    execution = await db.get(ExecutionRun, run.execution_run_id) if run.execution_run_id else None
    if execution and str(execution.status or "") not in PROJECT_RUN_TERMINAL_STATUSES:
        execution.status = "stale"
        execution.summary = "项目网页心跳超时，运行已标记为失活；历史输入输出保留。"
        execution.metadata_json = {
            **_safe_dict(execution.metadata_json),
            "project_run_id": run.id,
            "project_id": run.project_id,
            "last_project_stale_at": isoformat_bjt(now),
            "project_heartbeat_stale_after_seconds": stale_after_seconds,
        }
    return True


async def reconcile_stale_project_runs(
    db: AsyncSession,
    user: User,
    *,
    stale_after_seconds: int = PROJECT_HEARTBEAT_STALE_SECONDS,
    limit: int = PROJECT_STALE_RECONCILE_LIMIT,
) -> dict[str, Any]:
    stale_after_seconds = max(30, min(int(stale_after_seconds or PROJECT_HEARTBEAT_STALE_SECONDS), 24 * 3600))
    limit = max(1, min(int(limit or PROJECT_STALE_RECONCILE_LIMIT), 2000))
    now = now_bjt()
    cutoff = now - timedelta(seconds=stale_after_seconds)
    conditions = [
        ProjectRun.status.in_(list(PROJECT_RUN_ACTIVE_STATUSES)),
        or_(ProjectRun.last_heartbeat_at.is_(None), ProjectRun.last_heartbeat_at < cutoff),
    ]
    access_condition = await _project_access_condition(db, user)
    if access_condition is not None:
        conditions.append(access_condition)
    run_read_condition = await _project_run_read_condition(db, user)
    if run_read_condition is not None:
        conditions.append(run_read_condition)
    rows = (
        await db.execute(
            select(ProjectRun)
            .join(Project, Project.id == ProjectRun.project_id)
            .where(*conditions)
            .order_by(ProjectRun.last_heartbeat_at.asc().nullsfirst(), ProjectRun.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()
    updated = 0
    for run in rows:
        if await _mark_project_run_stale_if_needed(db, run, now=now, stale_after_seconds=stale_after_seconds):
            updated += 1
    await db.flush()
    return {
        "ok": True,
        "updated": updated,
        "checked": len(rows),
        "stale_after_seconds": stale_after_seconds,
        "cutoff_at": isoformat_bjt(cutoff),
    }


async def _reconcile_stale_project_runs_for_capacity(
    db: AsyncSession,
    *,
    stale_after_seconds: int = PROJECT_HEARTBEAT_STALE_SECONDS,
    limit: int = PROJECT_STALE_RECONCILE_LIMIT,
) -> dict[str, Any]:
    """System-side stale reconciliation before enforcing global run capacity."""

    stale_after_seconds = max(30, min(int(stale_after_seconds or PROJECT_HEARTBEAT_STALE_SECONDS), 24 * 3600))
    limit = max(1, min(int(limit or PROJECT_STALE_RECONCILE_LIMIT), 2000))
    now = now_bjt()
    cutoff = now - timedelta(seconds=stale_after_seconds)
    rows = (
        await db.execute(
            select(ProjectRun)
            .where(
                ProjectRun.status.in_(list(PROJECT_RUN_ACTIVE_STATUSES)),
                or_(ProjectRun.last_heartbeat_at.is_(None), ProjectRun.last_heartbeat_at < cutoff),
            )
            .order_by(ProjectRun.last_heartbeat_at.asc().nullsfirst(), ProjectRun.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()
    updated = 0
    for run in rows:
        if await _mark_project_run_stale_if_needed(db, run, now=now, stale_after_seconds=stale_after_seconds):
            updated += 1
    await db.flush()
    return {
        "ok": True,
        "updated": updated,
        "checked": len(rows),
        "stale_after_seconds": stale_after_seconds,
        "cutoff_at": isoformat_bjt(cutoff),
    }


def _item_search_matches(item: dict[str, Any], search: str | None) -> bool:
    q = str(search or "").strip().lower()
    if not q:
        return True
    fields = (item.get("id"), item.get("name"), item.get("description"), item.get("department"), item.get("type"))
    return any(q in str(field or "").lower() for field in fields)


def _item_department_matches(item: dict[str, Any], *, department_id: str | None = None, department: str | None = None) -> bool:
    dept_id = _text(department_id, limit=50)
    dept = _text(department, limit=100)
    if dept_id and str(item.get("department_id") or "") != dept_id:
        return False
    if dept and str(item.get("department") or "") != dept:
        return False
    return True


def _project_sort_value(item: dict[str, Any]) -> str:
    return str(item.get("updated_at") or item.get("created_at") or item.get("name") or item.get("id") or "")


def _project_row_sort_value(project: Project) -> str:
    return str(_iso(project.updated_at) or _iso(project.created_at) or project.name or project.id)


async def list_projects(
    db: AsyncSession,
    user: User,
    *,
    scope: str | None = None,
    include_playbooks: bool = True,
    page: int = 1,
    page_size: int = PROJECT_LIST_DEFAULT_PAGE_SIZE,
    search: str | None = None,
    run_status: str | None = None,
    department_id: str | None = None,
    department: str | None = None,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or PROJECT_LIST_DEFAULT_PAGE_SIZE), PROJECT_LIST_MAX_PAGE_SIZE))
    offset = (page - 1) * page_size
    await reconcile_stale_project_runs(db, user, limit=PROJECT_STALE_RECONCILE_LIMIT)

    playbook_items: list[dict[str, Any]] = []
    if include_playbooks:
        playbook_items = [
            item
            for item in await _visible_playbook_projects(user)
            if _scope_matches(item, scope, user)
            and _item_search_matches(item, search)
            and _item_department_matches(item, department_id=department_id, department=department)
            and _run_filter_matches(_safe_dict(item.get("latest_run")).get("status"), run_status)
        ]

    conditions = []
    access_condition = await _project_access_condition(db, user)
    if access_condition is not None:
        conditions.append(access_condition)
    scope_condition = _project_scope_condition(scope, user)
    if scope_condition is not None:
        conditions.append(scope_condition)
    search_condition = _project_search_condition(search)
    if search_condition is not None:
        conditions.append(search_condition)
    department_condition = _project_department_condition(department_id=department_id, department=department)
    if department_condition is not None:
        conditions.append(department_condition)
    run_condition = _latest_run_status_condition(run_status)
    if run_condition is not None:
        conditions.append(run_condition)

    latest_ranked = await _visible_latest_run_ranked_subquery(db, user)
    project_rows_stmt = _latest_run_join(select(Project, ProjectRun).select_from(Project), latest_ranked)
    count_stmt = _latest_run_join(select(func.count(Project.id)).select_from(Project), latest_ranked)
    stat_stmt = _latest_run_join(
        select(
            Project.type,
            Project.status,
            Project.department,
            ProjectRun.status,
            ProjectRun.report_count,
            ProjectRun.todo_count,
        ).select_from(Project),
        latest_ranked,
    )
    if conditions:
        project_rows_stmt = project_rows_stmt.where(*conditions)
        count_stmt = count_stmt.where(*conditions)
        stat_stmt = stat_stmt.where(*conditions)

    project_rows_stmt = project_rows_stmt.order_by(Project.updated_at.desc(), Project.created_at.desc())

    total_projects = int((await db.execute(count_stmt)).scalar() or 0)
    stat_rows = (await db.execute(stat_stmt)).all()

    # Playbook rows are in-memory legacy items. When mixed with DB projects,
    # fetch only the project prefix needed to assemble the requested mixed page.
    # A page at position `offset..offset+page_size` can never need more than
    # `offset + page_size` project rows, because each playbook can only shift
    # projects later in the mixed ordering.
    if playbook_items:
        project_fetch_limit = min(total_projects, offset + page_size)
        project_pairs = [
            (project, run)
            for project, run in (await db.execute(project_rows_stmt.limit(project_fetch_limit))).all()
        ]
        total_items = total_projects + len(playbook_items)
        stats = _summarize_project_stat_values(stat_rows, playbook_items)
        mixed: list[tuple[str, Project | dict[str, Any], ProjectRun | None]] = [
            ("project", project, run) for project, run in project_pairs
        ] + [("playbook", item, None) for item in playbook_items]
        mixed.sort(
            key=lambda row: _project_row_sort_value(row[1]) if row[0] == "project" else _project_sort_value(row[1]),  # type: ignore[arg-type]
            reverse=True,
        )
        page_rows = mixed[offset : offset + page_size]
        page_projects = [value for kind, value, _run in page_rows if kind == "project"]  # type: ignore[list-item]
        visible_latest_runs = await _latest_visible_runs_by_project(db, user, page_projects)
        items = []
        for kind, value, _run in page_rows:
            if kind == "project":
                project = value  # type: ignore[assignment]
                items.append(await _serialize_project(db, user, project, latest_run=visible_latest_runs.get(project.id)))
            else:
                items.append(value)  # type: ignore[arg-type]
        query_strategy = "bounded_mixed_playbook_project_page"
    else:
        stats = _summarize_project_stat_values(stat_rows)
        page_pairs = [
            (project, run)
            for project, run in (await db.execute(project_rows_stmt.offset(offset).limit(page_size))).all()
        ]
        page_projects = [project for project, _run in page_pairs]
        visible_latest_runs = await _latest_visible_runs_by_project(db, user, page_projects)
        items = [
            await _serialize_project(db, user, project, latest_run=visible_latest_runs.get(project.id))
            for project, _run in page_pairs
        ]
        total_items = total_projects
        query_strategy = "server_side_latest_run_page"

    return {
        "items": items,
        "stats": stats,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total_items,
            "has_more": offset + page_size < total_items,
        },
        "runtime_policy": {
            "containerized": False,
            "click_to_use": True,
            "capability_gateway": "SkillForge Project Gateway",
            "records": ["project_runs", "execution_runs", "decision_log", "project_capability_calls", "learning_events"],
            "package_limits": _project_package_limits(),
            "gateway_limits": _project_gateway_limits(),
            "query_strategy": query_strategy,
            "project_page_fetch_limit": project_fetch_limit if playbook_items else page_size,
        },
    }


def _metric_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _metric_float(value: Any, *, digits: int = 3) -> float:
    try:
        return round(float(value or 0), digits)
    except Exception:
        return 0.0


def _capacity_state(utilization: float) -> str:
    if utilization >= 1:
        return "saturated"
    if utilization >= 0.75:
        return "busy"
    return "ok"


def _status_count_map(rows: list[tuple[Any, Any]]) -> dict[str, int]:
    return {str(status or "unknown"): _metric_int(count) for status, count in rows}


async def _project_run_capacity_snapshot(
    db: AsyncSession,
    *,
    target_concurrent: int | None = None,
    stale_after_seconds: int | None = None,
) -> dict[str, Any]:
    target = target_concurrent or _setting_int("PROJECT_TARGET_CONCURRENT_RUNS", 100, minimum=1, maximum=100_000)
    conditions = [ProjectRun.status.in_(list(PROJECT_RUN_ACTIVE_STATUSES))]
    if stale_after_seconds:
        stale_after_seconds = max(30, min(int(stale_after_seconds), 24 * 3600))
        cutoff = now_bjt() - timedelta(seconds=stale_after_seconds)
        conditions.append(or_(ProjectRun.last_heartbeat_at.is_(None), ProjectRun.last_heartbeat_at >= cutoff))
    active_runs = _metric_int(
        (
            await db.execute(
                select(func.count(ProjectRun.id)).where(*conditions)
            )
        ).scalar()
    )
    utilization = _metric_float(active_runs / target if target else 0)
    return {
        "target_concurrent_runs": target,
        "active_runs": active_runs,
        "available_run_slots": max(target - active_runs, 0),
        "concurrency_utilization": utilization,
        "concurrency_state": _capacity_state(utilization),
    }


async def _ensure_project_run_capacity(db: AsyncSession) -> dict[str, Any]:
    stale_after_seconds = PROJECT_HEARTBEAT_STALE_SECONDS
    lock_acquired = await acquire_xact_lock(db, PROJECT_CAPACITY_LOCK_KEY)
    lock_info = {"key": PROJECT_CAPACITY_LOCK_KEY, "acquired": lock_acquired}
    reconcile = await _reconcile_stale_project_runs_for_capacity(db, stale_after_seconds=stale_after_seconds)
    snapshot = await _project_run_capacity_snapshot(db, stale_after_seconds=stale_after_seconds)
    if snapshot["active_runs"] >= snapshot["target_concurrent_runs"]:
        raise AppError(
            "PROJECT_CAPACITY_EXCEEDED",
            429,
            {
                "detail": "平台项目运行容量已满，请等待其它项目心跳失活或完成后再打开。",
                **snapshot,
                "stale_reconcile": reconcile,
                "capacity_lock": lock_info,
            },
        )
    return {**snapshot, "stale_reconcile": reconcile, "capacity_lock": lock_info}


async def get_project_runtime_status(
    db: AsyncSession,
    user: User,
    *,
    stale_after_seconds: int = PROJECT_HEARTBEAT_STALE_SECONDS,
) -> dict[str, Any]:
    """Return capacity and AI/runtime health for the visible project host fleet."""

    stale_after_seconds = max(30, min(int(stale_after_seconds or PROJECT_HEARTBEAT_STALE_SECONDS), 24 * 3600))
    reconcile = await reconcile_stale_project_runs(
        db,
        user,
        stale_after_seconds=stale_after_seconds,
        limit=PROJECT_STALE_RECONCILE_LIMIT,
    )
    now = now_bjt()
    recent_hours = _setting_int("PROJECT_RUNTIME_RECENT_HOURS", 24, minimum=1, maximum=24 * 30)
    recent_cutoff = now - timedelta(hours=recent_hours)
    target_concurrent = _setting_int("PROJECT_TARGET_CONCURRENT_RUNS", 100, minimum=1, maximum=100_000)
    target_projects = _setting_int("PROJECT_TARGET_VISIBLE_PROJECTS", 1000, minimum=1, maximum=1_000_000)
    access_condition = await _project_access_condition(db, user)
    project_conditions = [access_condition] if access_condition is not None else []
    run_read_condition = await _project_run_read_condition(db, user)
    run_conditions = [run_read_condition] if run_read_condition is not None else []

    total_projects = _metric_int(
        (await db.execute(select(func.count(Project.id)).where(*project_conditions))).scalar()
    )
    active_runs = _metric_int(
        (
            await db.execute(
                select(func.count(ProjectRun.id))
                .join(Project, Project.id == ProjectRun.project_id)
                .where(*project_conditions, *run_conditions, ProjectRun.status.in_(list(PROJECT_RUN_ACTIVE_STATUSES)))
            )
        ).scalar()
    )
    recent_runs = _metric_int(
        (
            await db.execute(
                select(func.count(ProjectRun.id))
                .join(Project, Project.id == ProjectRun.project_id)
                .where(*project_conditions, *run_conditions, ProjectRun.created_at >= recent_cutoff)
            )
        ).scalar()
    )
    run_status_rows = (
        await db.execute(
            select(ProjectRun.status, func.count(ProjectRun.id))
            .join(Project, Project.id == ProjectRun.project_id)
            .where(*project_conditions, *run_conditions)
            .group_by(ProjectRun.status)
        )
    ).all()
    by_run_status = _status_count_map(run_status_rows)

    project_type_rows = (
        await db.execute(
            select(Project.type, func.count(Project.id))
            .where(*project_conditions)
            .group_by(Project.type)
            .order_by(func.count(Project.id).desc())
        )
    ).all()
    project_department_rows = (
        await db.execute(
            select(Project.department, func.count(Project.id))
            .where(*project_conditions)
            .group_by(Project.department)
            .order_by(func.count(Project.id).desc())
            .limit(30)
        )
    ).all()
    active_department_rows = (
        await db.execute(
            select(Project.department, func.count(ProjectRun.id))
            .join(Project, Project.id == ProjectRun.project_id)
            .where(*project_conditions, *run_conditions, ProjectRun.status.in_(list(PROJECT_RUN_ACTIVE_STATUSES)))
            .group_by(Project.department)
            .order_by(func.count(ProjectRun.id).desc())
            .limit(30)
        )
    ).all()

    capability_status_rows = (
        await db.execute(
            select(ProjectCapabilityCall.status, func.count(ProjectCapabilityCall.id))
            .join(ProjectRun, ProjectRun.id == ProjectCapabilityCall.project_run_id)
            .join(Project, Project.id == ProjectRun.project_id)
            .where(*project_conditions, *run_conditions, ProjectCapabilityCall.created_at >= recent_cutoff)
            .group_by(ProjectCapabilityCall.status)
        )
    ).all()
    capability_type_rows = (
        await db.execute(
            select(ProjectCapabilityCall.capability_type, func.count(ProjectCapabilityCall.id))
            .join(ProjectRun, ProjectRun.id == ProjectCapabilityCall.project_run_id)
            .join(Project, Project.id == ProjectRun.project_id)
            .where(*project_conditions, *run_conditions, ProjectCapabilityCall.created_at >= recent_cutoff)
            .group_by(ProjectCapabilityCall.capability_type)
        )
    ).all()
    avg_latency_ms = _metric_float(
        (
            await db.execute(
                select(func.avg(ProjectCapabilityCall.latency_ms))
                .join(ProjectRun, ProjectRun.id == ProjectCapabilityCall.project_run_id)
                .join(Project, Project.id == ProjectRun.project_id)
                .where(*project_conditions, *run_conditions, ProjectCapabilityCall.created_at >= recent_cutoff)
            )
        ).scalar(),
        digits=1,
    )
    recent_error_rows = (
        await db.execute(
            select(ProjectRun, Project)
            .join(Project, Project.id == ProjectRun.project_id)
            .where(
                *project_conditions,
                *run_conditions,
                ProjectRun.updated_at >= recent_cutoff,
                or_(ProjectRun.status.in_(["failed", "ai_failed", "stale"]), ProjectRun.error.is_not(None)),
            )
            .order_by(ProjectRun.updated_at.desc())
            .limit(20)
        )
    ).all()

    active_utilization = _metric_float(active_runs / target_concurrent if target_concurrent else 0)
    project_utilization = _metric_float(total_projects / target_projects if target_projects else 0)
    capability_by_status = _status_count_map(capability_status_rows)
    capability_by_type = _status_count_map(capability_type_rows)
    capability_total_recent = sum(capability_by_status.values())
    concurrency_state = _capacity_state(active_utilization)
    project_state = _capacity_state(project_utilization)
    gateway_state = "busy" if concurrency_state == "busy" or by_run_status.get("waiting_ai", 0) else concurrency_state
    if by_run_status.get("ai_failed", 0) or by_run_status.get("failed", 0):
        gateway_state = "degraded" if gateway_state == "ok" else gateway_state

    return {
        "ok": True,
        "generated_at": isoformat_bjt(now),
        "scope": {
            "visible_project_count": total_projects,
            "recent_window_hours": recent_hours,
            "stale_after_seconds": stale_after_seconds,
        },
        "capacity": {
            "target_concurrent_runs": target_concurrent,
            "active_runs": active_runs,
            "available_run_slots": max(target_concurrent - active_runs, 0),
            "concurrency_utilization": active_utilization,
            "concurrency_state": concurrency_state,
            "target_visible_projects": target_projects,
            "visible_projects": total_projects,
            "project_utilization": project_utilization,
            "project_state": project_state,
        },
        "runs": {
            "active": active_runs,
            "recent_24h": recent_runs if recent_hours == 24 else recent_runs,
            "recent_window_hours": recent_hours,
            "by_status": by_run_status,
            "running": by_run_status.get("running", 0) + by_run_status.get("opening", 0),
            "stale": by_run_status.get("stale", 0),
            "waiting_ai": by_run_status.get("waiting_ai", 0),
            "ai_completed": by_run_status.get("ai_completed", 0),
            "failed": by_run_status.get("failed", 0) + by_run_status.get("ai_failed", 0),
            "by_active_department": _status_count_map(active_department_rows),
            "stale_reconcile": reconcile,
        },
        "ai": {
            "waiting": by_run_status.get("waiting_ai", 0),
            "completed": by_run_status.get("ai_completed", 0),
            "failed": by_run_status.get("ai_failed", 0),
            "capability_calls_recent": capability_total_recent,
            "capability_by_status": capability_by_status,
            "capability_by_type": capability_by_type,
            "avg_latency_ms": avg_latency_ms,
        },
        "projects": {
            "total": total_projects,
            "by_type": _status_count_map(project_type_rows),
            "by_department": _status_count_map(project_department_rows),
        },
        "gateway": {
            "status": gateway_state,
            "mode": "browser_static_iframe",
            "containerized": False,
            "credential_location": "platform_only",
            "gateway_limits": _project_gateway_limits(),
            "records": [
                "project_runs",
                "execution_runs",
                "decision_log",
                "project_ingress_events",
                "project_capability_calls",
                "learning_events",
            ],
            "supports_click_to_use": True,
            "supports_hundreds_to_thousands_projects": target_projects >= 1000,
            "supports_100_concurrent_runs": target_concurrent >= 100,
        },
        "recent_errors": [
            {
                "project_run_id": run.id,
                "project_id": project.id,
                "project_name": project.name,
                "status": run.status,
                "liveness": _run_liveness(run).get("liveness"),
                "error": _text(run.error, limit=500),
                "updated_at": _iso(run.updated_at),
            }
            for run, project in recent_error_rows
        ],
    }


async def get_project(db: AsyncSession, user: User, project_id: str) -> dict[str, Any]:
    if project_id.startswith("playbook:"):
        file_name = project_id.split(":", 1)[1]
        pb = await playbook_service.get_playbook(file_name)
        return _playbook_project(pb)
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("PROJECT_NOT_FOUND", 404)
    if not await can_read_project(db, user, project):
        raise AppError("PROJECT_ACCESS_DENIED", 403)
    latest = await _latest_runs_by_project(db, [project.id])
    now = now_bjt()
    await _mark_project_run_stale_if_needed(db, latest.get(project.id), now=now)
    latest_run = latest.get(project.id)
    if latest_run and not await can_read_project_run(db, user, project, latest_run):
        latest_run = (
            await db.execute(
                select(ProjectRun)
                .where(ProjectRun.project_id == project.id, ProjectRun.user_id == str(getattr(user, "id", "") or ""))
                .order_by(ProjectRun.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
    item = await _serialize_project(db, user, project, latest_run=latest_run)
    can_inspect_project_runs = _is_global_user(user) or await can_write_project(db, user, project)
    runs_stmt = (
        select(ProjectRun)
        .where(ProjectRun.project_id == project.id)
        .order_by(ProjectRun.created_at.desc())
        .limit(20)
    )
    if not can_inspect_project_runs:
        runs_stmt = runs_stmt.where(ProjectRun.user_id == str(getattr(user, "id", "") or ""))
    runs = (
        await db.execute(
            runs_stmt
        )
    ).scalars().all()
    for run in runs:
        await _mark_project_run_stale_if_needed(db, run, now=now)
    item["runs"] = [serialize_run(row) for row in runs]
    return item


async def get_project_service_contract(db: AsyncSession, user: User, project_id: str) -> dict[str, Any]:
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("PROJECT_NOT_FOUND", 404)
    if not await can_read_project(db, user, project):
        raise AppError("PROJECT_ACCESS_DENIED", 403)
    metadata = _safe_dict(project.metadata_json)
    service_conversion = _safe_dict(metadata.get("service_conversion"))
    if not service_conversion:
        service_conversion = {
            "enabled": False,
            "status": "not_converted",
            "project_id": project.id,
            "api_service": {
                "contract_url": f"/api/projects/{project.id}/service",
                "invoke_url": f"/api/projects/{project.id}/service/invoke",
                "invoke_method": "POST",
                "entry_url": project.entry_url,
                "gateway_sdk": "/project-gateway-sdk.js",
            },
            "runtime_contract": {
                "input": {"source": "project_gateway"},
                "output": _safe_dict(metadata.get("outputs")) or {"reports": True, "todos": True, "proofs": True},
                "capabilities": _safe_list(metadata.get("capabilities")),
            },
        }
    return {
        "ok": True,
        "project_id": project.id,
        "name": project.name,
        "current_version_id": project.current_version_id,
        "entry_url": project.entry_url,
        "service": service_conversion,
        "credential_location": "platform_only",
    }


async def get_project_runtime_evaluation(db: AsyncSession, user: User, project_id: str) -> dict[str, Any]:
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("PROJECT_NOT_FOUND", 404)
    if not await can_read_project(db, user, project):
        raise AppError("PROJECT_ACCESS_DENIED", 403)
    metadata = _safe_dict(project.metadata_json)
    evaluation = _safe_dict(metadata.get("runtime_evaluation"))
    if not evaluation:
        evaluation = {
            "status": "warn" if project.entry_url else "fail",
            "normal_run_ready": bool(project.entry_url),
            "summary": "外部 URL 项目未做静态包评估" if project.entry_url else "项目缺少入口 URL",
            "checks": [
                _runtime_eval_check(
                    "entry_url",
                    "入口 URL",
                    "pass" if project.entry_url else "fail",
                    project.entry_url or "未配置入口 URL",
                )
            ],
            "entry_url": project.entry_url,
            "evaluated_at": None,
            "evaluator": "skillforge_runtime_fallback_v1",
        }
    latest = await _latest_runs_by_project(db, [project.id])
    latest_run = latest.get(project.id)
    return {
        "ok": True,
        "project_id": project.id,
        "name": project.name,
        "entry_url": project.entry_url,
        "current_version_id": project.current_version_id,
        "runtime_evaluation": evaluation,
        "latest_run_evaluation": _safe_dict(_safe_dict(getattr(latest_run, "ai_summary", None)).get("result_evaluation")) if latest_run else {},
    }


def _project_service_request_id(data: dict[str, Any]) -> str:
    explicit = _request_id(data)
    if explicit:
        return explicit
    payload = {
        "input": _safe_dict(data.get("input")),
        "params": _safe_dict(data.get("params")),
        "prompt": _text(data.get("prompt"), limit=8000),
        "capability": data.get("capability") or data.get("capability_key") or data.get("key"),
        "output": _safe_dict(data.get("output")),
        "reports": _safe_list(data.get("reports")),
        "todos": _safe_list(data.get("todos")),
    }
    return f"svc-{_hash_payload(payload)[:32]}"


def _project_service_declared_capabilities(project: Project) -> list[str]:
    metadata = _safe_dict(project.metadata_json)
    service = _safe_dict(metadata.get("service_conversion"))
    runtime_contract = _safe_dict(service.get("runtime_contract"))
    declared = _safe_list(runtime_contract.get("capabilities")) or _safe_list(metadata.get("capabilities"))
    result: list[str] = []
    for item in declared:
        key = _canonical_capability_key(item if isinstance(item, str) else _safe_dict(item).get("key") or _safe_dict(item).get("capability"))
        if key and key not in result:
            result.append(key)
    return result


def _select_project_service_capability(project: Project, data: dict[str, Any]) -> str:
    requested = _canonical_capability_key(data.get("capability") or data.get("capability_key") or data.get("key") or "")
    if requested:
        return requested
    declared = _project_service_declared_capabilities(project)
    for preferred in ("ai.cheap.generate", "platform.ai.cheap.generate", "ai.generate", "platform.ai.generate", "ai.chat", "platform.ai.chat"):
        if preferred in declared:
            return preferred
    for key in declared:
        if key in PROJECT_AI_CAPABILITIES:
            return key
    return "ai.cheap.generate"


def _project_service_prompt(project: Project, data: dict[str, Any], input_payload: dict[str, Any]) -> str:
    prompt = _text(data.get("prompt"), limit=8000)
    if prompt:
        return prompt
    metadata = _safe_dict(project.metadata_json)
    service = _safe_dict(metadata.get("service_conversion"))
    page_summary = _safe_dict(service.get("page_summary"))
    title = _text(page_summary.get("title"), limit=160) or project.name or project.id
    return (
        f"请把 SkillForge 上传项目《{title}》作为平台 API 服务执行。"
        "基于 input 生成结构化业务输出，优先返回 summary、reports、todos、proofs。"
        f" input={input_payload}"
    )


def _service_capability_output_to_project_output(project: Project, capability_result: dict[str, Any]) -> dict[str, Any]:
    result = _safe_dict(capability_result.get("result"))
    raw_output = result.get("output")
    if isinstance(raw_output, dict):
        output = dict(raw_output)
    else:
        output = {"summary": _text(raw_output, limit=4000) or _text(result.get("summary"), limit=4000) or ""}
    if not output.get("summary"):
        output["summary"] = f"项目 {project.name} API 服务已完成。"
    output["_service_capability"] = {
        "capability": capability_result.get("capability"),
        "call_id": capability_result.get("call_id"),
        "model": result.get("model"),
        "model_profile": result.get("model_profile"),
        "credential_location": result.get("credential_location") or "platform_only",
    }
    return output


def _service_capability_data_response(capability_result: dict[str, Any]) -> dict[str, Any] | None:
    result = _safe_dict(capability_result.get("result"))
    if result.get("tool") and "data" in result:
        return result
    return None


def _tmall_first_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for item in value.values():
            if isinstance(item, list):
                return item
    return []


def _tmall_search_key(value: Any, key_candidates: set[str]) -> Any:
    queue: list[Any] = [value]
    seen = 0
    while queue and seen < 500:
        current = queue.pop(0)
        seen += 1
        if isinstance(current, dict):
            for key in key_candidates:
                if key in current:
                    return current[key]
            queue.extend(item for item in current.values() if isinstance(item, (dict, list)))
        elif isinstance(current, list):
            queue.extend(item for item in current if isinstance(item, (dict, list)))
    return None


def _tmall_first_value(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _tmall_parse_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number else None
    matched = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not matched:
        return None
    number = float(matched.group(0))
    return number / 100 if "%" in str(value) else number


def _tmall_evidence_from_row(row: dict[str, Any]) -> list[str]:
    pattern = re.compile(
        r"环比|下滑|下降|访客|搜索|转化|成交|支付|加购|ROI|PPC|推广|关键词|价格|活动|评价|问大家|差评|竞品|市场|风险|高价|优惠|完整性|"
        r"conversion|decline|cart|pay|uv|visitor|traffic|promotion|campaign|price|activity|review|market|competitor|rank|missing|error",
        re.IGNORECASE,
    )
    preferred = [(key, value) for key, value in row.items() if pattern.search(f"{key} {value}")]
    source = preferred or list(row.items())
    return [
        f"{key}: {str(value)[:48]}"
        for key, value in source
        if value not in (None, "")
    ][:6]


def _tmall_reason_labels(text: str, source_type: str) -> list[str]:
    labels: list[str] = []
    lower = text.lower()
    checks = [
        (re.search(r"搜索|访客|免费|自然|流量|search|uv|visitor|traffic", lower) or source_type in {"decline", "traffic_conversion"}, "免费搜索/自然流下滑"),
        (re.search(r"roi|ppc|推广|关键词|阿里妈妈|千牛|promotion|campaign|adcost|spend|cost", lower), "阿里妈妈投放效率下滑"),
        (re.search(r"价格|活动|高价|优惠|价格力|price|activity|discount|coupon", lower), "价格力/活动配置风险"),
        (re.search(r"评价|问大家|差评|负向|review|qa|negative|badreview", lower), "评价/问大家风险"),
        (re.search(r"竞品|市场|排行|同行|market|competitor|rank", lower), "竞品压力"),
        (re.search(r"pay_amount_pool|支付金额.*池", lower), "支付金额波动池"),
        (re.search(r"conversion_change_rate|转化率.*下滑|cvr.*decline", lower), "转化率波动"),
        (re.search(r"转化|成交|支付|加购|下滑|异常|conversion|cvr|pay|cart|decline", lower) or source_type in {"abnormal", "pay_top_bottom"}, "成交/转化异常波动"),
        (re.search(r"完整性|缺失|timeout|失败|missing|error|empty", lower), "数据缺失待补查"),
    ]
    for matched, label in checks:
        if matched and label not in labels:
            labels.append(label)
    return labels[:3]


def _tmall_next_action(labels: list[str]) -> str:
    if "阿里妈妈投放效率下滑" in labels:
        return "先查关键词/人群计划 ROI、PPC、点击转化率和预算，优先停低效计划或降价测试。"
    if "价格力/活动配置风险" in labels:
        return "先处理价格力、活动状态和营销风险，避免高价限流、活动暂停或优惠配置错误继续影响链接。"
    if "评价/问大家风险" in labels:
        return "先处理差评置顶、问大家缺口和高频风险词，补充客服解释、问大家内容和评价运营动作。"
    if "竞品压力" in labels:
        return "先看竞品排行、价格带、主图卖点和活动力度，确认是否需要调整承接策略。"
    if "转化率波动" in labels:
        return "先检查详情页承接、SKU价格/优惠、评价问大家和主图卖点，确认转化率波动原因。"
    if "支付金额波动池" in labels:
        return "优先核对支付金额上下10分析池里的同款价格、活动节奏和投放变化。"
    if "成交/转化异常波动" in labels:
        return "先核对成交、转化、加购和访客的环比拆解，判断是流量入口变化还是详情页承接转化问题。"
    if "数据缺失待补查" in labels:
        return "先补采缺失数据，避免只凭单侧数据误判。"
    return "先核对搜索词、标题卖点、主图承接和自然搜索入口，确认是否需要补搜索承接或改链接内容。"


def _tmall_data_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [
        ("异常商品清单", "abnormal"),
        ("下滑系数排名", "decline"),
        ("重点商品清单", "focus"),
        ("高流量低转化清单", "traffic_conversion"),
        ("支付金额上下10分析商品清单", "pay_top_bottom"),
        ("生意参谋_商品排行榜", "rank"),
        ("生意参谋_商品排行榜_访客排序", "rank_uv"),
    ]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_key, source_type in sources:
        value = _tmall_search_key(raw, {source_key})
        for row in _tmall_first_array(value):
            if not isinstance(row, dict):
                continue
            item_id = str(_tmall_first_value(row, ["item_id", "itemId", "商品ID", "商品id", "宝贝ID", "链接ID", "id"]) or "")
            title = str(_tmall_first_value(row, ["title", "商品名称", "商品名", "item_title", "itemName", "宝贝标题", "name"]) or "")
            key = item_id or title or json.dumps(row, ensure_ascii=False, default=str)[:80]
            if key in seen:
                continue
            seen.add(key)
            rows.append({"row": row, "source_key": source_key, "source_type": source_type})
            if len(rows) >= 30:
                return rows
    return rows


def _tmall_diagnose_row(item: dict[str, Any], index: int) -> dict[str, Any]:
    row = _safe_dict(item.get("row"))
    source_key = str(item.get("source_key") or "")
    source_type = str(item.get("source_type") or "")
    evidence = _tmall_evidence_from_row(row)
    labels = _tmall_reason_labels(f"{source_key} {' '.join(evidence)}", source_type)
    pay_change = _tmall_parse_number(_tmall_first_value(row, ["成交金额环比", "支付金额环比", "pay_amount_change_pct", "payAmtChangePct", "支付金额变化率"]))
    uv_change = _tmall_parse_number(_tmall_first_value(row, ["访客环比", "uv_change_pct", "访客数环比", "搜索访客环比"]))
    conv_change = _tmall_parse_number(_tmall_first_value(row, ["转化率环比", "conversion_rate_change_pct", "点击转化率环比"]))
    roi = _tmall_parse_number(_tmall_first_value(row, ["ROI", "roi", "投入产出比", "推广ROI"]))
    decline_coefficient = _tmall_parse_number(_tmall_first_value(row, ["decline_coefficient", "declineCoefficient", "下滑系数"]))
    cart_count = _tmall_parse_number(_tmall_first_value(row, ["cart_count", "cartCount", "加购人数", "加购件数"]))
    cart_count_prev = _tmall_parse_number(_tmall_first_value(row, ["cart_count_prev", "cartCountPrev", "前日加购人数", "加购人数前值"]))
    cart_drop = cart_count is not None and cart_count_prev is not None and cart_count_prev > 0 and (cart_count / cart_count_prev - 1) <= -0.2
    coefficient_risk = decline_coefficient is not None and abs(decline_coefficient) >= 0.5
    mild_signal = any(value is not None and value <= -0.08 for value in (pay_change, uv_change, conv_change)) or (
        decline_coefficient is not None and abs(decline_coefficient) >= 0.15
    )
    severe_drop = any(value is not None and value <= -0.3 for value in (pay_change, uv_change, conv_change)) or cart_drop or coefficient_risk
    paid_risk = "阿里妈妈投放效率下滑" in labels and roi is not None and 0 < roi < 1
    severity = "red" if severe_drop or paid_risk else "yellow" if labels or mild_signal or source_type == "abnormal" else "green"
    reason = labels or (["成交/转化异常波动"] if source_type == "abnormal" else ["待运营复查"])
    item_id = _tmall_first_value(row, ["item_id", "itemId", "商品ID", "商品id", "宝贝ID", "链接ID", "id"]) or f"cache-row-{index + 1}"
    title = _tmall_first_value(row, ["title", "商品名称", "商品名", "item_title", "itemName", "宝贝标题", "name"]) or f"天猫重点链接 {index + 1}"
    issue_score = 100 + len(evidence) if severity == "red" else 50 + len(evidence) if severity == "yellow" else 0
    return {
        "item_id": str(item_id),
        "title": str(title),
        "source": source_key,
        "severity": severity,
        "priority": "P0" if severity == "red" else "P1" if severity == "yellow" else "P2",
        "reason_labels": reason,
        "evidence": evidence or [f"来源: {source_key}"],
        "next_actions": [_tmall_next_action(reason)],
        "issue_score": issue_score,
    }


def _tmall_build_summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"red": 0, "yellow": 0, "green": 0}
    issue_counts: dict[str, int] = {}
    for item in findings:
        severity = str(item.get("severity") or "green")
        counts[severity] = counts.get(severity, 0) + 1
        for label in _safe_list(item.get("reason_labels")):
            text = str(label)
            issue_counts[text] = issue_counts.get(text, 0) + 1
    urgent = sorted((item for item in findings if item.get("severity") != "green"), key=lambda row: int(row.get("issue_score") or 0), reverse=True)[:3]
    return {
        "severity_counts": counts,
        "collection_success_rate": 1,
        "top_issue_types": [
            {"label": label, "count": count}
            for label, count in sorted(issue_counts.items(), key=lambda row: row[1], reverse=True)[:6]
        ],
        "top_urgent": [
            {
                "item_id": item.get("item_id"),
                "title": item.get("title"),
                "severity": item.get("severity"),
                "reason_labels": item.get("reason_labels"),
                "next_action": (_safe_list(item.get("next_actions")) or [""])[0],
            }
            for item in urgent
        ],
    }


def _tmall_build_todos(findings: list[dict[str, Any]], analysis_date: str) -> list[dict[str, Any]]:
    todos: list[dict[str, Any]] = []
    for item in [row for row in findings if row.get("severity") != "green"][:10]:
        action = (_safe_list(item.get("next_actions")) or ["先复查链接指标。"])[0]
        evidence = "；".join(str(value) for value in _safe_list(item.get("evidence"))[:3])
        todos.append(
            {
                "kind": "dispatch",
                "title": f"{item.get('priority')}｜示例品牌链接健康待办｜{item.get('title')}",
                "summary": f"{'、'.join(str(value) for value in _safe_list(item.get('reason_labels')))}；{evidence}",
                "payload": {"analysis_date": analysis_date, "finding": item},
                "tasks": [{"content": action, "extra": {"item_id": item.get("item_id"), "severity": item.get("severity")}}],
            }
        )
    return todos


def _tmall_analysis_date(source: dict[str, Any]) -> str:
    stamp = str(source.get("run_completed_at") or source.get("created_at") or "")
    matched = re.search(r"\d{4}-\d{2}-\d{2}", stamp)
    return matched.group(0) if matched else now_bjt().date().isoformat()


def _tmall_build_output_from_raw(raw: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    findings = sorted(
        [_tmall_diagnose_row(item, index) for index, item in enumerate(_tmall_data_rows(raw))],
        key=lambda row: int(row.get("issue_score") or 0),
        reverse=True,
    )
    if not findings:
        raise AppError("PROJECT_CAPABILITY_DENIED", 404, {"detail": "平台最新天猫采集缓存未包含可诊断商品"})
    analysis_date = _tmall_analysis_date(source)
    output = {
        "analysis_schema": "samplebrand_tmall_link_health_daily_project_preview_v1",
        "analysis_date": analysis_date,
        "department": "示例品牌传统电商运营部",
        "source_label": "SkillForge 平台最新采集缓存",
        "summary": _tmall_build_summary(findings),
        "findings": findings,
        "todos": _tmall_build_todos(findings, analysis_date),
        "source": source,
    }
    output["notification_markdown"] = "\n".join(
        [
            f"# 示例品牌天猫链接健康日报｜{analysis_date}",
            "",
            f"- 数据来源：{source.get('skill_id')} / {source.get('run_id')}",
            f"- 红色：{output['summary']['severity_counts'].get('red', 0)}，黄色：{output['summary']['severity_counts'].get('yellow', 0)}",
        ]
    )
    return output


async def _tmall_latest_raw_collection_analysis(db: AsyncSession, user: User, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    args = {
        **_safe_dict(payload),
        "include_content": True,
        "max_bytes": int(_safe_dict(payload).get("max_bytes") or _safe_dict(payload).get("maxBytes") or TMALL_LINK_HEALTH_ARTIFACT_MAX_BYTES),
    }
    data = await codex_service._builtin_data_artifact_get(
        db,
        user,
        args,
        capability_key="tmall.link_decline.raw_collection",
    )
    raw = _safe_dict(data.get("content_json"))
    source = _safe_dict(data.get("artifact"))
    output = _tmall_build_output_from_raw(raw, source)
    return {
        "generated_at": source.get("created_at") or now_bjt().isoformat(),
        "generated_by": "skillforge_project_gateway",
        "source": source,
        "output": output,
    }


async def project_dynamic_asset_payload(
    db: AsyncSession,
    user: User,
    project_id: str,
    version_token: str,
    asset_path: str,
) -> dict[str, Any] | None:
    rel = str(asset_path or "").lstrip("/")
    if project_id != TMALL_LINK_HEALTH_PROJECT_ID or rel not in {"web/latest-analysis.json", "latest-analysis.json"}:
        return None
    project = await db.get(Project, project_id)
    if not project or not await can_read_project(db, user, project):
        return None
    try:
        return await _tmall_latest_raw_collection_analysis(db, user, {"limit": 1})
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目动态 latest-analysis 读取平台缓存失败 project={} version={}: {}", project_id, version_token, exc)
        return None


async def invoke_project_service(db: AsyncSession, user: User, project_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Invoke an uploaded project as a platform API service.

    This is the non-browser path for the user request "上传以后自动转化成服务":
    create a ProjectRun, persist service input, run declared platform AI/data
    capability through Project Gateway, ingest the service output and keep the
    same DecisionLog/Learning Loop/Trace evidence as iframe runs.
    """

    data = data or {}
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("PROJECT_NOT_FOUND", 404)
    if not await can_read_project(db, user, project):
        raise AppError("PROJECT_ACCESS_DENIED", 403)
    if project.status == "archived":
        raise AppError("PROJECT_INVALID", 400, {"detail": "项目已归档，不能作为 API 服务调用"})

    service_request_id = _project_service_request_id(data)
    input_payload = {**_safe_dict(data.get("params")), **_safe_dict(data.get("input"))}
    run = await create_project_run(
        db,
        user,
        project.id,
        {
            "request_id": f"service:{service_request_id}",
            "params": _safe_dict(data.get("params")),
            "input": _safe_dict(data.get("input")),
            "parent_run_id": _text(data.get("parent_run_id"), limit=50),
        },
    )
    run_id = str(run["id"])

    input_result = await record_project_input_event(
        db,
        user,
        run_id,
        {
            "request_id": f"{service_request_id}:input",
            "input": input_payload,
            "metadata": {
                "source": "project_api_service",
                "project_id": project.id,
                "service_request_id": service_request_id,
                "invoke_url": f"/api/projects/{project.id}/service/invoke",
            },
            "replace": False,
        },
    )

    capability_key = _select_project_service_capability(project, data)
    capability_result: dict[str, Any]
    if _safe_dict(data.get("output")):
        capability_result = {
            "ok": True,
            "capability": "manual.output",
            "project_run_id": run_id,
            "call_id": None,
            "result": {"output": _safe_dict(data.get("output")), "credential_location": "platform_only"},
        }
    else:
        try:
            capability_result = await call_project_capability(
                db,
                user,
                run_id,
                {
                    "request_id": f"{service_request_id}:capability",
                    "capability": capability_key,
                    "prompt": _project_service_prompt(project, data, input_payload),
                    "input": {
                        **input_payload,
                        "_service": {
                            "project_id": project.id,
                            "project_name": project.name,
                            "service_request_id": service_request_id,
                            "contract_url": f"/api/projects/{project.id}/service",
                        },
                    },
                    "json_mode": bool(data.get("json_mode") or data.get("jsonMode")),
                    "temperature": data.get("temperature"),
                    "max_output_tokens": data.get("max_output_tokens") or data.get("maxOutputTokens"),
                },
            )
        except AppError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"detail": str(exc.detail or exc)}
            capability_result = {
                "ok": False,
                "capability": capability_key,
                "project_run_id": run_id,
                "call_id": detail.get("call_id"),
                "error": detail.get("detail") or detail.get("error") or str(exc.detail or exc),
                "detail": detail,
            }

    if capability_result.get("ok"):
        output = _safe_dict(data.get("output")) or _service_capability_output_to_project_output(project, capability_result)
        ingest_status = "completed"
    else:
        output = {
            "ok": False,
            "status": "failed",
            "error": _text(capability_result.get("error"), limit=1000) or "项目 API 服务能力调用失败",
            "_service_capability": {
                "capability": capability_result.get("capability"),
                "call_id": capability_result.get("call_id"),
                "credential_location": "platform_only",
            },
        }
        ingest_status = "failed"

    reports = _safe_list(data.get("reports")) or _safe_list(output.get("reports"))
    if not reports and output.get("summary"):
        reports = [
            {
                "title": f"{project.name} API 服务结果",
                "summary": _text(output.get("summary"), limit=1000) or "项目 API 服务已完成。",
                "payload": {"project_id": project.id, "project_run_id": run_id, "service_request_id": service_request_id},
            }
        ]
    todos = _safe_list(data.get("todos")) or _safe_list(output.get("todos"))
    proofs = _safe_list(data.get("proofs")) or _safe_list(output.get("proofs"))
    proofs.append(
        {
            "source": "project_api_service",
            "project_id": project.id,
            "project_run_id": run_id,
            "capability": capability_result.get("capability"),
            "call_id": capability_result.get("call_id"),
            "credential_location": "platform_only",
        }
    )

    output_result = await ingest_project_output(
        db,
        user,
        run_id,
        {
            "request_id": f"{service_request_id}:output",
            "input": input_payload,
            "output": output,
            "reports": reports,
            "todos": todos,
            "proofs": proofs,
            "metadata": {
                "source": "project_api_service",
                "project_id": project.id,
                "service_request_id": service_request_id,
                "capability": capability_result.get("capability"),
                "capability_call_id": capability_result.get("call_id"),
            },
            "status": ingest_status,
            "analysis_prompt": _text(data.get("analysis_prompt"), limit=1000),
        },
        auto_analyze=bool(data.get("auto_analyze", True)),
    )

    trace_url = f"/projects/{project.id}?run_id={run_id}"
    response = {
        "ok": bool(capability_result.get("ok")) and ingest_status == "completed",
        "project_id": project.id,
        "project_run_id": run_id,
        "execution_run_id": output_result.get("execution_run_id"),
        "request_id": service_request_id,
        "deduped": bool(run.get("deduped") or input_result.get("deduped") or output_result.get("deduped")),
        "service": {
            "contract_url": f"/api/projects/{project.id}/service",
            "invoke_url": f"/api/projects/{project.id}/service/invoke",
            "trace_url": trace_url,
            "credential_location": "platform_only",
        },
        "input_event_id": input_result.get("input_event_id"),
        "capability": {
            "key": capability_result.get("capability"),
            "ok": bool(capability_result.get("ok")),
            "call_id": capability_result.get("call_id"),
            "error": capability_result.get("error"),
        },
        "output": _redact_trace_value(output_result.get("output_result") or output),
        "run": output_result,
    }
    service_data = _service_capability_data_response(capability_result)
    if service_data is not None:
        response["data"] = _redact_trace_value(service_data)
    return response


def _validate_project_payload(data: dict[str, Any]) -> dict[str, Any]:
    raw_id = _text(data.get("id"), limit=42)
    if raw_id and not PROJECT_ID_PATTERN.match(raw_id):
        raise AppError("PROJECT_INVALID", 400, {"field": "id", "reason": "项目 ID 只允许字母、数字、_、-，最长 42 位"})
    project_type = _text(data.get("type"), limit=30) or "external_web"
    if project_type not in PROJECT_TYPES:
        raise AppError("PROJECT_INVALID", 400, {"field": "type", "allowed": sorted(PROJECT_TYPES)})
    visibility = _text(data.get("visibility"), limit=20) or "department"
    if visibility not in PROJECT_VISIBILITIES:
        raise AppError("PROJECT_INVALID", 400, {"field": "visibility", "allowed": sorted(PROJECT_VISIBILITIES)})
    status = _text(data.get("status"), limit=20) or "published"
    if status not in PROJECT_STATUSES:
        raise AppError("PROJECT_INVALID", 400, {"field": "status", "allowed": sorted(PROJECT_STATUSES)})
    name = _text(data.get("name"), limit=160)
    if not name:
        raise AppError("PROJECT_INVALID", 400, {"field": "name", "reason": "项目名称不能为空"})
    return {"id": raw_id, "type": project_type, "visibility": visibility, "status": status, "name": name}


async def create_project(db: AsyncSession, user: User, data: dict[str, Any]) -> dict[str, Any]:
    if not _can_create_or_edit_role(user):
        raise AppError("PROJECT_ACCESS_DENIED", 403)
    normalized = _validate_project_payload(data)
    department_id = _text(data.get("department_id"), limit=50)
    department = _text(data.get("department"), limit=100)
    if department_id and not await can_access_department(db, user, department_id):
        raise AppError("AUTH_DEPARTMENT_DENIED", 403)
    if not department_id and not department:
        department_id, department = await _default_department(db, user)
    if not await can_write_project(db, user, None, department_id=department_id):
        raise AppError("PROJECT_ACCESS_DENIED", 403)

    project_id = normalized["id"] or _new_id("proj", 16)
    if await db.get(Project, project_id):
        raise AppError("PROJECT_ALREADY_EXISTS", 409)

    now = now_bjt()
    project = Project(
        id=project_id,
        name=normalized["name"],
        description=_text(data.get("description"), limit=4000),
        type=normalized["type"],
        department_id=department_id,
        department=department,
        owner_user_id=str(getattr(user, "id", "") or "") or None,
        visibility=normalized["visibility"],
        status=normalized["status"],
        entry_url=_safe_project_entry_url(data.get("entry_url")),
        metadata_json=_safe_dict(data.get("metadata")) or {},
        created_at=now,
        updated_at=now,
    )
    db.add(project)
    if project.entry_url:
        version = ProjectVersion(
            id=_new_id("pver", 18),
            project_id=project.id,
            version=_text(data.get("version"), limit=40) or "1",
            artifact_type="external_url",
            artifact_ref=project.entry_url,
            sf_package_hash=_text(data.get("sf_package_hash"), limit=128),
            created_by=project.owner_user_id,
            metadata_json={"source": "platform_create", **_safe_dict(data.get("version_metadata"))},
            created_at=now,
        )
        db.add(version)
        project.current_version_id = version.id
    await db.flush()
    return await _serialize_project(db, user, project)


def _read_project_manifest(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        manifest = raw
    else:
        import yaml

        text = str(raw or "").strip()
        if not text:
            manifest = {}
        else:
            try:
                manifest = json_loads_or_yaml(text)
            except Exception as exc:  # noqa: BLE001
                raise AppError("PROJECT_INVALID", 400, {"field": "manifest_json", "reason": str(exc)}) from exc
    if not isinstance(manifest, dict):
        raise AppError("PROJECT_INVALID", 400, {"field": "manifest_json", "reason": "manifest 顶层必须是对象"})
    return manifest


def json_loads_or_yaml(text: str) -> dict[str, Any]:
    try:
        parsed = __import__("json").loads(text)
    except Exception:
        parsed = __import__("yaml").safe_load(text)
    return parsed if isinstance(parsed, dict) else {}


def parse_project_form_json_dict(raw: str | None, *, field: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception as exc:
        raise AppError("PROJECT_INVALID", 400, {"field": field, "reason": f"JSON 解析失败: {exc}"}) from exc
    if not isinstance(parsed, dict):
        raise AppError("PROJECT_INVALID", 400, {"field": field, "reason": "必须是 JSON 对象"})
    return parsed


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


def _project_version_text(value: Any, version_token: str) -> str:
    return _text(value, limit=40) or version_token


def _project_version_with_upload_suffix(base_version: str, version_token: str) -> str:
    suffix = f"+{version_token[:8]}"
    base = _text(base_version, limit=max(1, 40 - len(suffix))) or "upload"
    return f"{base}{suffix}"[:40]


def _validate_archive_member_name(name: str) -> str:
    rel = str(name or "").strip().replace("\\", "/").lstrip("/")
    pure = PurePosixPath(rel)
    if not rel or any(part in {"", ".", ".."} or part.startswith(".") for part in pure.parts):
        raise AppError("PROJECT_INVALID", 400, {"file": name, "reason": "项目包包含非法路径"})
    return str(pure)


def _validate_project_static_security(root: Path) -> dict[str, Any]:
    """Block obvious frontend AI secrets and catalog direct model endpoints.

    Projects run as browser/static surfaces. Production AI and data calls must go
    through Project Gateway so that auth, audit, run trace and learning-loop
    capture stay under the platform control plane. Direct provider URLs without
    secrets are allowed only as Project Autowire proxy candidates.
    """

    direct_ai_endpoints: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() not in PROJECT_STATIC_SECURITY_SUFFIXES:
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
                raise AppError(
                    "PROJECT_INVALID",
                    400,
                    {
                        "field": "package",
                        "reason": "项目静态资源疑似包含前端 AI 密钥或直连模型接口，请改用 Project Gateway 调平台 AI/数据能力",
                        "file": rel,
                        "match": label,
                    },
                )
        for label, pattern in PROJECT_FRONTEND_AI_ENDPOINT_PATTERNS:
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            direct_ai_endpoints.append(
                {
                    "file": rel,
                    "match": label,
                    "count": len(matches),
                    "gateway_proxy": "project-autowire",
                }
            )
    return {
        "frontend_secret_scan": "pass",
        "direct_ai_endpoint_count": sum(int(item.get("count") or 0) for item in direct_ai_endpoints),
        "direct_ai_endpoints": direct_ai_endpoints[:50],
        "ai_endpoint_policy": "allowed_with_project_gateway_proxy",
    }


def _validate_project_static_entry_runnable(root: Path, entry: str) -> None:
    entry_path = root / _safe_entry_path(entry)
    suffix = entry_path.suffix.lower()
    if suffix not in {".html", ".htm"} or not entry_path.is_file():
        return
    try:
        raw = entry_path.read_bytes()[:PROJECT_STATIC_SECURITY_SCAN_BYTES]
    except OSError:
        return
    if b"\x00" in raw[:8192]:
        return
    text = raw.decode("utf-8", errors="ignore")
    for label, pattern in PROJECT_UNBUILT_FRONTEND_REF_PATTERNS:
        for match in pattern.finditer(text):
            ref = next((group for group in match.groups() if group), "")
            raise AppError(
                "PROJECT_INVALID",
                400,
                {
                    "field": "entry",
                    "reason": "项目入口疑似引用未构建前端源码，请先执行构建并上传 dist/build/out 等静态产物",
                    "file": entry,
                    "dependency": ref,
                    "match": label,
                },
            )


def _project_asset_absolute_url(project_id: str, version_token: str, rel: str) -> str:
    return _project_asset_url(project_id, version_token, rel)


def _split_project_url_suffix(value: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    suffix = ""
    if parsed.query:
        suffix += f"?{parsed.query}"
    if parsed.fragment:
        suffix += f"#{parsed.fragment}"
    return parsed.path, suffix


def _local_project_asset_for_root_ref(root: Path, entry: str, value: str) -> str | None:
    """Map generated-app root-absolute asset URLs to an uploaded package file.

    Codex/Vite/CRA/Next static exports commonly emit `/assets/...`,
    `/static/...` or `/_next/static/...` even when the generated page is hosted
    below `/api/projects/assets/<project>/<version>/...`.  If the referenced
    file exists in the package, rewrite it to the authenticated project asset
    URL; otherwise leave it alone so normal app routes are not changed.
    """

    raw = str(value or "").strip()
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        return None
    path_part, suffix = _split_project_url_suffix(raw)
    rel_part = path_part.lstrip("/")
    if not rel_part or rel_part.startswith(("api/", "project-assets/")):
        return None
    if rel_part in {"project-gateway-sdk.js", "project-autowire.js"}:
        return None
    try:
        rel = _safe_entry_path(rel_part)
    except AppError:
        return None
    if PurePosixPath(rel).suffix.lower() in {".ts", ".tsx", ".jsx", ".vue", ".svelte"}:
        return None
    entry_parent = PurePosixPath(_safe_entry_path(entry)).parent
    candidates: list[str] = []
    if str(entry_parent) not in {"", "."}:
        candidates.append(str(entry_parent / rel))
    candidates.append(rel)
    for candidate in candidates:
        try:
            safe_candidate = _safe_entry_path(candidate)
        except AppError:
            continue
        if (root / safe_candidate).is_file():
            return f"{safe_candidate}{suffix}"
    return None


def _rewrite_project_root_absolute_asset_refs(root: Path, project_id: str, version_token: str, entry: str) -> dict[str, Any]:
    """Rewrite root-absolute generated frontend asset references in-place."""

    changed_files: list[dict[str, Any]] = []

    def replace_url(url: str) -> str:
        local_rel = _local_project_asset_for_root_ref(root, entry, url)
        if not local_rel:
            return url
        return _project_asset_absolute_url(project_id, version_token, local_rel)

    srcset_pattern = re.compile(
        r"(?P<prefix>\b(?:srcset|imagesrcset)\s*=\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
        re.IGNORECASE | re.S,
    )
    unquoted_attr_pattern = re.compile(
        r"(?P<prefix>\b(?:src|href|poster|data|action|formaction|xlink:href)\s*=\s*)(?P<url>/(?!/)[^\s\"'<>]+)",
        re.IGNORECASE,
    )
    quoted_url_pattern = re.compile(r"(?P<quote>[\"'])(?P<url>/(?!/)[^\"'<>\s)]+)(?P=quote)")
    css_url_pattern = re.compile(r"url\(\s*(?P<quote>[\"']?)(?P<url>/(?!/)[^\"')\s]+)(?P=quote)\s*\)", re.IGNORECASE)

    def rewrite_srcset_value(value: str) -> tuple[str, int]:
        changed = 0
        rewritten_items: list[str] = []
        for raw_item in str(value or "").split(","):
            leading = raw_item[: len(raw_item) - len(raw_item.lstrip())]
            trailing = raw_item[len(raw_item.rstrip()):]
            body = raw_item.strip()
            if not body:
                rewritten_items.append(raw_item)
                continue
            parts = body.split(None, 1)
            original_url = parts[0]
            rewritten_url = replace_url(original_url)
            if rewritten_url != original_url:
                changed += 1
            suffix = f" {parts[1]}" if len(parts) > 1 else ""
            rewritten_items.append(f"{leading}{rewritten_url}{suffix}{trailing}")
        return ",".join(rewritten_items), changed

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in PROJECT_STATIC_REWRITE_SUFFIXES:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if not raw or len(raw) > PROJECT_STATIC_REWRITE_MAX_BYTES or b"\x00" in raw[:8192]:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue

        replacements = 0

        def replace_css(match: re.Match[str]) -> str:
            nonlocal replacements
            original = match.group("url")
            rewritten = replace_url(original)
            if rewritten == original:
                return match.group(0)
            replacements += 1
            quote = match.group("quote") or ""
            return f"url({quote}{rewritten}{quote})"

        def replace_quoted(match: re.Match[str]) -> str:
            nonlocal replacements
            original = match.group("url")
            rewritten = replace_url(original)
            if rewritten == original:
                return match.group(0)
            replacements += 1
            quote = match.group("quote")
            return f"{quote}{rewritten}{quote}"

        def replace_srcset(match: re.Match[str]) -> str:
            nonlocal replacements
            rewritten_value, changed = rewrite_srcset_value(match.group("value"))
            if not changed:
                return match.group(0)
            replacements += changed
            return f"{match.group('prefix')}{match.group('quote')}{rewritten_value}{match.group('quote')}"

        def replace_unquoted_attr(match: re.Match[str]) -> str:
            nonlocal replacements
            original = match.group("url")
            rewritten = replace_url(original)
            if rewritten == original:
                return match.group(0)
            replacements += 1
            return f"{match.group('prefix')}{rewritten}"

        rewritten = css_url_pattern.sub(replace_css, text)
        rewritten = srcset_pattern.sub(replace_srcset, rewritten)
        rewritten = unquoted_attr_pattern.sub(replace_unquoted_attr, rewritten)
        rewritten = quoted_url_pattern.sub(replace_quoted, rewritten)
        if rewritten == text:
            continue
        path.write_text(rewritten, encoding="utf-8")
        changed_files.append(
            {
                "file": path.relative_to(root).as_posix(),
                "replacements": replacements,
            }
        )

    return {
        "enabled": True,
        "changed_files": changed_files[:50],
        "changed_file_count": len(changed_files),
        "replacement_count": sum(int(item.get("replacements") or 0) for item in changed_files),
        "asset_prefix": _project_asset_absolute_url(project_id, version_token, ""),
    }


def _manifest_gateway_autowire_enabled(manifest: dict[str, Any]) -> bool:
    raw = manifest.get("gateway_autowire")
    if raw is None:
        raw = _safe_dict(manifest.get("metadata")).get("gateway_autowire")
    return raw is not False


def _inject_project_gateway_bootstrap_if_needed(
    root: Path,
    entry: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Inject gateway SDK/autowire into generated HTML that has no gateway code."""

    if not _manifest_gateway_autowire_enabled(manifest):
        return {"enabled": False, "reason": "manifest_disabled"}
    entry_path = root / _safe_entry_path(entry)
    if entry_path.suffix.lower() not in {".html", ".htm"} or not entry_path.is_file():
        return {"enabled": False, "reason": "entry_not_html"}
    try:
        raw = entry_path.read_bytes()
    except OSError:
        return {"enabled": False, "reason": "entry_unreadable"}
    if not raw or len(raw) > PROJECT_STATIC_REWRITE_MAX_BYTES or b"\x00" in raw[:8192]:
        return {"enabled": False, "reason": "entry_not_text"}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"enabled": False, "reason": "entry_not_utf8"}
    if any(marker in text for marker in PROJECT_GATEWAY_BOOTSTRAP_MARKERS):
        return {"enabled": True, "injected": False, "reason": "gateway_already_present", "file": entry}

    snippet = PROJECT_GATEWAY_BOOTSTRAP_SNIPPET
    lower = text.lower()
    if "</head>" in lower:
        idx = lower.rfind("</head>")
        rewritten = f"{text[:idx]}{snippet}\n{text[idx:]}"
        location = "head"
    elif "</body>" in lower:
        idx = lower.rfind("</body>")
        rewritten = f"{text[:idx]}{snippet}\n{text[idx:]}"
        location = "body"
    else:
        rewritten = f"{snippet}\n{text}"
        location = "prefix"
    entry_path.write_text(rewritten, encoding="utf-8")
    return {
        "enabled": True,
        "injected": True,
        "file": entry,
        "location": location,
        "sdk": "/project-gateway-sdk.js",
        "autowire": "/project-autowire.js",
    }


def _runtime_eval_check(key: str, label: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        **{name: value for name, value in extra.items() if value is not None},
    }


def _runtime_eval_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def _project_asset_paths_referenced_by_entry(text: str, project_id: str, version_token: str) -> list[str]:
    pattern = re.compile(
        rf"/api/projects/assets/{re.escape(project_id)}/{re.escape(version_token)}/(?P<path>[^\"'<>\s)]+)",
        re.IGNORECASE,
    )
    refs: list[str] = []
    for match in pattern.finditer(text or ""):
        raw = match.group("path").split("?", 1)[0].split("#", 1)[0]
        try:
            refs.append(_safe_entry_path(raw))
        except AppError:
            refs.append(raw)
    return sorted(set(refs))


def _root_absolute_asset_refs_left(text: str) -> list[str]:
    pattern = re.compile(
        r"""(?:\b(?:src|href|poster|data|action|formaction|xlink:href)\s*=\s*["']?|url\(\s*["']?)(?P<url>/(?!/|api/|project-gateway-sdk\.js|project-autowire\.js)[^"'<>\s)]+)""",
        re.IGNORECASE,
    )
    refs: list[str] = []
    for match in pattern.finditer(text or ""):
        url = match.group("url").split("?", 1)[0].split("#", 1)[0]
        if url.startswith(("/assets/", "/static/", "/_next/", "/images/", "/fonts/", "/favicon", "/manifest")):
            refs.append(url)
    return sorted(set(refs))[:50]


def _evaluate_project_static_runtime(
    root: Path,
    project_id: str,
    version_token: str,
    entry: str,
    *,
    manifest: dict[str, Any],
    asset_rewrites: dict[str, Any],
    gateway_bootstrap: dict[str, Any],
    service_conversion: dict[str, Any],
    package_security: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic run-readiness evaluation for uploaded generated pages."""

    checks: list[dict[str, Any]] = []
    safe_entry = _safe_entry_path(entry)
    entry_path = root / safe_entry
    text = ""
    if entry_path.is_file():
        checks.append(_runtime_eval_check("entry_exists", "入口文件", "pass", f"找到 {safe_entry}"))
        try:
            raw = entry_path.read_bytes()
            text = raw.decode("utf-8", errors="replace") if raw and b"\x00" not in raw[:8192] else ""
            checks.append(_runtime_eval_check("entry_readable", "入口可读", "pass", f"入口大小 {len(raw)} bytes", bytes=len(raw)))
        except OSError as exc:
            checks.append(_runtime_eval_check("entry_readable", "入口可读", "fail", f"入口读取失败: {exc}"))
    else:
        checks.append(_runtime_eval_check("entry_exists", "入口文件", "fail", f"未找到 {safe_entry}"))

    replacement_count = int(asset_rewrites.get("replacement_count") or 0)
    if replacement_count > 0:
        checks.append(
            _runtime_eval_check(
                "asset_rewrites",
                "根路径资源重写",
                "pass",
                f"已重写 {replacement_count} 处根路径资源，避免 iframe 子路径 404",
                replacement_count=replacement_count,
            )
        )
    else:
        checks.append(_runtime_eval_check("asset_rewrites", "根路径资源重写", "pass", "未发现需要重写的根路径资源"))

    leftover_refs = _root_absolute_asset_refs_left(text)
    if leftover_refs:
        checks.append(
            _runtime_eval_check(
                "root_asset_refs_left",
                "遗留根路径资源",
                "warn",
                f"仍有 {len(leftover_refs)} 个根路径资源引用，可能需要真实构建/导出后再上传",
                refs=leftover_refs[:20],
            )
        )
    else:
        checks.append(_runtime_eval_check("root_asset_refs_left", "遗留根路径资源", "pass", "未发现遗留根路径静态资源引用"))

    rewritten_refs = _project_asset_paths_referenced_by_entry(text, project_id, version_token)
    missing_refs = [rel for rel in rewritten_refs if not (root / rel).is_file()]
    if missing_refs:
        checks.append(
            _runtime_eval_check(
                "referenced_assets_exist",
                "引用资源存在",
                "fail",
                f"{len(missing_refs)} 个已接管资源在包内不存在",
                missing=missing_refs[:20],
                checked=len(rewritten_refs),
            )
        )
    else:
        checks.append(
            _runtime_eval_check(
                "referenced_assets_exist",
                "引用资源存在",
                "pass",
                f"已校验 {len(rewritten_refs)} 个 Project Asset 引用",
                checked=len(rewritten_refs),
            )
        )

    autowire_required = _manifest_gateway_autowire_enabled(manifest)
    if not autowire_required:
        checks.append(_runtime_eval_check("gateway_autowire", "Gateway 自动接入", "warn", "manifest 关闭了自动接入，需项目自行调用 Gateway SDK"))
    elif gateway_bootstrap.get("injected") is True:
        checks.append(_runtime_eval_check("gateway_autowire", "Gateway 自动接入", "pass", "已注入 Project Gateway SDK 和 Autowire"))
    elif gateway_bootstrap.get("enabled") is True:
        checks.append(_runtime_eval_check("gateway_autowire", "Gateway 自动接入", "pass", "页面已自行接入 Project Gateway"))
    else:
        checks.append(
            _runtime_eval_check(
                "gateway_autowire",
                "Gateway 自动接入",
                "warn",
                str(gateway_bootstrap.get("reason") or "未能自动注入 Gateway SDK"),
            )
        )

    if service_conversion.get("status") == "converted":
        checks.append(_runtime_eval_check("service_conversion", "转服务契约", "pass", "已生成平台服务契约，可通过 /service 查看"))
    else:
        checks.append(
            _runtime_eval_check(
                "service_conversion",
                "转服务契约",
                "warn",
                str(service_conversion.get("reason") or service_conversion.get("status") or "未生成服务契约"),
            )
        )

    security = _safe_dict(package_security)
    direct_ai_count = int(security.get("direct_ai_endpoint_count") or 0)
    gateway_ready = bool(gateway_bootstrap.get("injected") is True or gateway_bootstrap.get("enabled") is True)
    if direct_ai_count > 0 and gateway_ready:
        checks.append(
            _runtime_eval_check(
                "ai_provider_gateway_proxy",
                "底层 AI 接管",
                "pass",
                f"发现 {direct_ai_count} 处前端模型接口，已由 Project Autowire 代理到平台 AI Gateway",
                direct_ai_endpoint_count=direct_ai_count,
                endpoints=security.get("direct_ai_endpoints"),
            )
        )
    elif direct_ai_count > 0:
        checks.append(
            _runtime_eval_check(
                "ai_provider_gateway_proxy",
                "底层 AI 接管",
                "warn",
                f"发现 {direct_ai_count} 处前端模型接口，但 Gateway 自动接入未启用，需项目自行调用平台 AI",
                direct_ai_endpoint_count=direct_ai_count,
                endpoints=security.get("direct_ai_endpoints"),
            )
        )
    else:
        checks.append(_runtime_eval_check("ai_provider_gateway_proxy", "底层 AI 接管", "pass", "未发现前端直连模型接口"))

    status = _runtime_eval_status(checks)
    return {
        "status": status,
        "normal_run_ready": status != "fail",
        "summary": "可正常打开运行" if status == "pass" else "可运行但有适配提醒" if status == "warn" else "存在阻断，需修复后再运行",
        "checks": checks,
        "entry": safe_entry,
        "entry_url": _project_asset_url(project_id, version_token, safe_entry),
        "evaluated_at": isoformat_bjt(now_bjt()),
        "evaluator": "skillforge_static_runtime_v1",
    }


def _service_conversion_button_text(text_value: str) -> str:
    return _text(re.sub(r"\s+", " ", text_value or ""), limit=160)


def _extract_project_service_page_sample(root: Path, entry: str) -> dict[str, Any]:
    entry_path = root / _safe_entry_path(entry)
    sample: dict[str, Any] = {
        "entry": entry,
        "title": "",
        "forms": [],
        "buttons": [],
        "inputs": [],
        "text_preview": "",
    }
    if entry_path.suffix.lower() not in {".html", ".htm"} or not entry_path.is_file():
        return sample
    try:
        raw = entry_path.read_bytes()[:PROJECT_STATIC_REWRITE_MAX_BYTES]
        html = raw.decode("utf-8", errors="ignore")
    except OSError:
        return sample
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.S)
    if title_match:
        sample["title"] = _service_conversion_button_text(re.sub(r"<[^>]+>", "", title_match.group(1)))
    stripped = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.S)
    stripped = re.sub(r"<style\b[^>]*>.*?</style>", " ", stripped, flags=re.IGNORECASE | re.S)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    sample["text_preview"] = _text(re.sub(r"\s+", " ", stripped), limit=1600)

    forms: list[dict[str, Any]] = []
    for index, form_match in enumerate(re.finditer(r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>", html, re.IGNORECASE | re.S)):
        if index >= 10:
            break
        attrs = form_match.group("attrs") or ""
        body = form_match.group("body") or ""
        form_inputs = []
        for input_match in re.finditer(r"<(?:input|textarea|select)\b(?P<attrs>[^>]*)>", body, re.IGNORECASE | re.S):
            input_attrs = input_match.group("attrs") or ""
            name = re.search(r"\bname\s*=\s*['\"]([^'\"]+)['\"]", input_attrs, re.IGNORECASE)
            input_type = re.search(r"\btype\s*=\s*['\"]([^'\"]+)['\"]", input_attrs, re.IGNORECASE)
            placeholder = re.search(r"\bplaceholder\s*=\s*['\"]([^'\"]+)['\"]", input_attrs, re.IGNORECASE)
            form_inputs.append(
                {
                    "name": _text(name.group(1) if name else "", limit=120),
                    "type": _text(input_type.group(1) if input_type else "", limit=40) or "text",
                    "placeholder": _text(placeholder.group(1) if placeholder else "", limit=160),
                }
            )
        action = re.search(r"\baction\s*=\s*['\"]([^'\"]+)['\"]", attrs, re.IGNORECASE)
        forms.append(
            {
                "index": index,
                "action": _text(action.group(1) if action else "", limit=400),
                "inputs": form_inputs[:30],
            }
        )
    sample["forms"] = forms

    buttons = []
    for index, button_match in enumerate(re.finditer(r"<button\b[^>]*>(.*?)</button>", html, re.IGNORECASE | re.S)):
        if index >= 30:
            break
        buttons.append(
            {
                "index": index,
                "label": _service_conversion_button_text(re.sub(r"<[^>]+>", "", button_match.group(1))),
            }
        )
    sample["buttons"] = [item for item in buttons if item.get("label")]
    inputs = []
    for index, input_match in enumerate(re.finditer(r"<input\b(?P<attrs>[^>]*)>", html, re.IGNORECASE | re.S)):
        if index >= 50:
            break
        attrs = input_match.group("attrs") or ""
        name = re.search(r"\bname\s*=\s*['\"]([^'\"]+)['\"]", attrs, re.IGNORECASE)
        input_type = re.search(r"\btype\s*=\s*['\"]([^'\"]+)['\"]", attrs, re.IGNORECASE)
        inputs.append(
            {
                "name": _text(name.group(1) if name else "", limit=120),
                "type": _text(input_type.group(1) if input_type else "", limit=40) or "text",
            }
        )
    sample["inputs"] = inputs
    return sample


def _fallback_project_service_contract(
    project_id: str,
    version_token: str,
    entry: str,
    manifest: dict[str, Any],
    page_sample: dict[str, Any],
    asset_rewrites: dict[str, Any],
    gateway_bootstrap: dict[str, Any],
) -> dict[str, Any]:
    declared_capabilities = _safe_list(manifest.get("capabilities"))
    if not declared_capabilities:
        declared_capabilities = ["ai.cheap.generate"]
    return {
        "enabled": True,
        "status": "converted",
        "mode": "platform_gateway_service",
        "project_id": project_id,
        "asset_version": version_token,
        "entry": entry,
        "model_profile": "ai.cheap",
        "ai_refinement": {"enabled": False, "reason": "project.service_conversion.ai_enabled 未开启或便宜模型未配置"},
        "api_service": {
            "contract_url": f"/api/projects/{project_id}/service",
            "invoke_url": f"/api/projects/{project_id}/service/invoke",
            "invoke_method": "POST",
            "open_url": f"/project-run/{project_id}",
            "entry_url": _project_asset_url(project_id, version_token, entry),
            "gateway_sdk": "/project-gateway-sdk.js",
            "autowire": "/project-autowire.js",
            "bridge_global": "window.SkillForgeProjectBridge",
            "methods": ["ready", "input", "output", "report", "todo", "ai", "autoOutput", "service.invoke"],
        },
        "runtime_contract": {
            "input": {
                "source": "autowire/page/form/action_click",
                "forms": page_sample.get("forms") or [],
                "inputs": page_sample.get("inputs") or [],
            },
            "output": {
                "reports": True,
                "todos": True,
                "proofs": True,
                "auto_analyze": True,
                "autowire_snapshots": ["page_loaded_output", "form_submit_output", "action_click_output"],
            },
            "capabilities": declared_capabilities,
        },
        "page_summary": {
            "title": page_sample.get("title") or _text(manifest.get("name"), limit=160) or project_id,
            "buttons": page_sample.get("buttons") or [],
            "text_preview": page_sample.get("text_preview") or "",
        },
        "asset_rewrites": asset_rewrites,
        "gateway_bootstrap": gateway_bootstrap,
    }


async def _system_config_bool(db: AsyncSession, key: str, default: bool = False) -> bool:
    try:
        from app.common.models import SystemConfig

        row = (await db.execute(select(SystemConfig).where(SystemConfig.key == key))).scalar_one_or_none()
        raw = getattr(row, "value", None) if row else None
    except Exception:
        raw = None
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


async def _maybe_refine_project_service_contract_with_cheap_ai(
    db: AsyncSession,
    user: User,
    contract: dict[str, Any],
    manifest: dict[str, Any],
    page_sample: dict[str, Any],
) -> dict[str, Any]:
    if not await _system_config_bool(db, "project.service_conversion.ai_enabled", False):
        return contract
    try:
        from app.common.ai import call_llm, get_ai_profile_config

        cheap_cfg = await get_ai_profile_config(model_profile="cheap", require_system_config=True)
        model = str(cheap_cfg.get("ai.model") or "").strip()
        prompt_payload = {
            "manifest": manifest,
            "page_sample": page_sample,
            "existing_contract": {
                "api_service": contract.get("api_service"),
                "runtime_contract": contract.get("runtime_contract"),
            },
        }
        refined = await call_llm(
            "你是 SkillForge 项目转服务助手。把上传的静态网页整理成可运行平台服务契约，只输出 JSON 对象。",
            json.dumps(prompt_payload, ensure_ascii=False, default=str),
            max_tokens=1200,
            temperature=0.1,
            timeout=45,
            json_mode=True,
            call_source="project_service_conversion",
            cost_context={
                "user_id": getattr(user, "id", None),
                "department": getattr(user, "department", None),
                "project_id": contract.get("project_id"),
                "asset_version": contract.get("asset_version"),
            },
            model_profile="cheap",
            require_system_config=True,
        )
        if isinstance(refined, dict):
            merged = dict(contract)
            merged["ai_refinement"] = {
                "enabled": True,
                "status": "completed",
                "model": model,
                "credential_location": "platform_only",
            }
            merged["ai_service_suggestion"] = refined
            return merged
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目转服务便宜模型优化失败 project_id={}: {}", contract.get("project_id"), exc)
        contract = dict(contract)
        contract["ai_refinement"] = {
            "enabled": True,
            "status": "failed",
            "model_profile": "ai.cheap",
            "error": _text(exc, limit=500),
        }
    return contract


async def _build_project_service_conversion(
    db: AsyncSession,
    user: User,
    *,
    project_id: str,
    version_token: str,
    target_dir: Path,
    entry: str,
    manifest: dict[str, Any],
    asset_rewrites: dict[str, Any],
    gateway_bootstrap: dict[str, Any],
) -> dict[str, Any]:
    page_sample = _extract_project_service_page_sample(target_dir, entry)
    contract = _fallback_project_service_contract(
        project_id,
        version_token,
        entry,
        manifest,
        page_sample,
        asset_rewrites,
        gateway_bootstrap,
    )
    return await _maybe_refine_project_service_contract_with_cheap_ai(db, user, contract, manifest, page_sample)


def _manifest_from_package(package_bytes: bytes) -> dict[str, Any]:
    candidates: dict[str, bytes] = {}
    limits = _project_package_limits()
    try:
        with tarfile.open(fileobj=io.BytesIO(package_bytes), mode="r:*") as tar:
            file_count = 0
            extracted_bytes = 0
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                file_count += 1
                extracted_bytes += max(int(member.size or 0), 0)
                if file_count > limits["max_files"]:
                    _raise_package_too_large("file_count", actual_files=file_count, max_files=limits["max_files"])
                if extracted_bytes > limits["max_extracted_bytes"]:
                    _raise_package_too_large(
                        "extracted_bytes",
                        actual_bytes=extracted_bytes,
                        max_bytes=limits["max_extracted_bytes"],
                    )
                rel = _validate_archive_member_name(member.name)
                if PurePosixPath(rel).name in PROJECT_MANIFEST_NAMES:
                    if int(member.size or 0) > limits["max_manifest_bytes"]:
                        _raise_package_too_large(
                            "manifest_bytes",
                            file=rel,
                            actual_bytes=int(member.size or 0),
                            max_bytes=limits["max_manifest_bytes"],
                        )
                    src = tar.extractfile(member)
                    if src is not None:
                        candidates[rel] = src.read(limits["max_manifest_bytes"])
            if candidates:
                _, raw = sorted(candidates.items(), key=lambda item: (len(PurePosixPath(item[0]).parts), item[0]))[0]
                return json_loads_or_yaml(raw.decode("utf-8"))
    except tarfile.TarError:
        pass

    try:
        with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
            file_count = 0
            extracted_bytes = 0
            for info in archive.infolist():
                if info.is_dir():
                    continue
                file_count += 1
                extracted_bytes += max(int(info.file_size or 0), 0)
                if file_count > limits["max_files"]:
                    _raise_package_too_large("file_count", actual_files=file_count, max_files=limits["max_files"])
                if extracted_bytes > limits["max_extracted_bytes"]:
                    _raise_package_too_large(
                        "extracted_bytes",
                        actual_bytes=extracted_bytes,
                        max_bytes=limits["max_extracted_bytes"],
                    )
                rel = _validate_archive_member_name(info.filename)
                if PurePosixPath(rel).name in PROJECT_MANIFEST_NAMES:
                    if int(info.file_size or 0) > limits["max_manifest_bytes"]:
                        _raise_package_too_large(
                            "manifest_bytes",
                            file=rel,
                            actual_bytes=int(info.file_size or 0),
                            max_bytes=limits["max_manifest_bytes"],
                        )
                    with archive.open(info) as src:
                        candidates[rel] = src.read(limits["max_manifest_bytes"])
            if candidates:
                _, raw = sorted(candidates.items(), key=lambda item: (len(PurePosixPath(item[0]).parts), item[0]))[0]
                return json_loads_or_yaml(raw.decode("utf-8"))
    except zipfile.BadZipFile:
        pass
    return {}


def _extract_project_package(package_bytes: bytes, target_dir: Path) -> tuple[str, dict[str, Any]]:
    limits = _project_package_limits()
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = target_dir.parent / f".{target_dir.name}.tmp-{uuid4().hex[:8]}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        try:
            tar = tarfile.open(fileobj=io.BytesIO(package_bytes), mode="r:*")
        except tarfile.TarError:
            tar = None
        if tar is not None:
            package_format = "tar"
            with tar:
                members = tar.getmembers()
                if not members:
                    raise AppError("PROJECT_INVALID", 400, {"detail": "项目包为空"})
                file_count = 0
                extracted_bytes = 0
                for member in members:
                    if not member.isfile():
                        continue
                    file_count += 1
                    member_size = max(int(member.size or 0), 0)
                    extracted_bytes += member_size
                    if file_count > limits["max_files"]:
                        _raise_package_too_large("file_count", actual_files=file_count, max_files=limits["max_files"])
                    if extracted_bytes > limits["max_extracted_bytes"]:
                        _raise_package_too_large(
                            "extracted_bytes",
                            actual_bytes=extracted_bytes,
                            max_bytes=limits["max_extracted_bytes"],
                            file=member.name,
                        )
                    rel = _validate_archive_member_name(member.name)
                    dest = (tmp_dir / rel).resolve()
                    if not str(dest).startswith(str(tmp_dir.resolve())):
                        raise AppError("PROJECT_INVALID", 400, {"file": member.name, "reason": "项目包路径越界"})
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    src = tar.extractfile(member)
                    if src is None:
                        continue
                    with src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)
        else:
            try:
                archive = zipfile.ZipFile(io.BytesIO(package_bytes))
            except zipfile.BadZipFile as exc:
                raise AppError("PROJECT_INVALID", 400, {"detail": f"项目包不是合法 tar/zip: {exc}"}) from exc
            package_format = "zip"
            with archive:
                infos = archive.infolist()
                if not infos:
                    raise AppError("PROJECT_INVALID", 400, {"detail": "项目包为空"})
                file_count = 0
                extracted_bytes = 0
                for info in infos:
                    if info.is_dir():
                        continue
                    file_count += 1
                    file_size = max(int(info.file_size or 0), 0)
                    extracted_bytes += file_size
                    if file_count > limits["max_files"]:
                        _raise_package_too_large("file_count", actual_files=file_count, max_files=limits["max_files"])
                    if extracted_bytes > limits["max_extracted_bytes"]:
                        _raise_package_too_large(
                            "extracted_bytes",
                            actual_bytes=extracted_bytes,
                            max_bytes=limits["max_extracted_bytes"],
                            file=info.filename,
                        )
                    rel = _validate_archive_member_name(info.filename)
                    dest = (tmp_dir / rel).resolve()
                    if not str(dest).startswith(str(tmp_dir.resolve())):
                        raise AppError("PROJECT_INVALID", 400, {"file": info.filename, "reason": "项目包路径越界"})
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)
        package_security = _validate_project_static_security(tmp_dir)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        tmp_dir.replace(target_dir)
        return package_format, package_security
    except AppError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except tarfile.TarError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise AppError("PROJECT_INVALID", 400, {"detail": f"项目包不是合法 tar: {exc}"}) from exc
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


async def auto_register_project(db: AsyncSession, user: User, data: dict[str, Any]) -> dict[str, Any]:
    manifest = _read_project_manifest(_safe_dict(data.get("manifest")) or data)
    project_id = _text(manifest.get("project_id") or manifest.get("id"), limit=42)
    kind = _text(manifest.get("kind") or manifest.get("type"), limit=30) or "external_web"
    name = _text(manifest.get("name"), limit=160) or project_id
    entry_url = _safe_project_entry_url(manifest.get("entry_url") or manifest.get("url"), required=True)
    if not project_id or not name:
        raise AppError("PROJECT_INVALID", 400, {"detail": "project_id/name 不能为空，自动注册必须来自项目 manifest"})
    normalized = _validate_project_payload(
        {
            "id": project_id,
            "name": name,
            "type": kind,
            "visibility": manifest.get("visibility") or "department",
            "status": manifest.get("status") or "published",
        }
    )

    department_id = _text(manifest.get("department_id"), limit=50)
    department = _text(manifest.get("department"), limit=100)
    if department_id and not await can_access_department(db, user, department_id):
        raise AppError("AUTH_DEPARTMENT_DENIED", 403)
    if not department_id and not department:
        department_id, department = await _default_department(db, user)

    project = await db.get(Project, project_id)
    if project:
        if not await can_write_project(db, user, project):
            raise AppError("PROJECT_ACCESS_DENIED", 403)
    elif not await can_write_project(db, user, None, department_id=department_id):
        raise AppError("PROJECT_ACCESS_DENIED", 403)

    source = _text(data.get("source") or manifest.get("source"), limit=80) or "sf_auto_register"
    source_hash = hashlib.sha256(str(sorted(manifest.items())).encode("utf-8", errors="ignore")).hexdigest()
    package_hash = _text(data.get("package_hash") or manifest.get("package_hash") or manifest.get("sf_package_hash"), limit=128)
    version_token = source_hash[:16]
    now = now_bjt()
    if not project:
        project = Project(
            id=project_id,
            name=normalized["name"],
            description=_text(manifest.get("description"), limit=4000),
            type=normalized["type"],
            department_id=department_id,
            department=department,
            owner_user_id=str(getattr(user, "id", "") or "") or None,
            visibility=normalized["visibility"],
            status=normalized["status"],
            entry_url=entry_url,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        db.add(project)
    else:
        project.name = normalized["name"]
        project.description = _text(manifest.get("description"), limit=4000) or project.description
        project.type = normalized["type"]
        project.visibility = normalized["visibility"]
        project.status = normalized["status"]
        project.department_id = department_id or project.department_id
        project.department = department or project.department
        project.entry_url = entry_url
        project.updated_at = now

    version_id = f"pver_{project_id[:24]}_{version_token}"
    version = await db.get(ProjectVersion, version_id)
    if not version:
        version = ProjectVersion(
            id=version_id,
            project_id=project_id,
            version=str(manifest.get("version") or version_token),
            artifact_type="external_url",
            artifact_ref=entry_url,
            sf_package_hash=package_hash,
            created_by=str(getattr(user, "id", "") or "") or None,
            metadata_json={"manifest": manifest, "source": source, "auto_registered": True},
            created_at=now,
        )
        db.add(version)
    project.current_version_id = version.id
    project.metadata_json = {
        **_safe_dict(project.metadata_json),
        "source": source,
        "auto_registered": True,
        "entry": entry_url,
        "package_hash": package_hash,
        "package_format": "external_url",
        "capabilities": _safe_list(manifest.get("capabilities")),
        "outputs": _safe_dict(manifest.get("outputs")),
    }
    await db.flush()
    item = await _serialize_project(db, user, project)
    item["auto_registered"] = True
    return item


async def upload_project_package(
    db: AsyncSession,
    user: User,
    *,
    package_bytes: bytes,
    manifest_raw: str | dict[str, Any],
    package_hash: str | None = None,
    submit_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_package_bytes(package_bytes)
    actual_hash = hashlib.sha256(package_bytes).hexdigest()
    expected_hash = _normalize_sha256(package_hash)
    if expected_hash and expected_hash != actual_hash:
        raise AppError("PROJECT_INVALID", 400, {"detail": "项目包 sha256 校验失败", "expected": expected_hash, "actual": actual_hash})

    manifest = _read_project_manifest(manifest_raw)
    if not manifest:
        manifest = _manifest_from_package(package_bytes)
    if not manifest:
        raise AppError("PROJECT_INVALID", 400, {"field": "manifest_json", "reason": "未提供 manifest_json，项目包内也未找到 projectforge.yaml/json"})
    project_id = _text(manifest.get("project_id") or manifest.get("id"), limit=42)
    kind = _text(manifest.get("kind") or manifest.get("type"), limit=30) or "web_static"
    name = _text(manifest.get("name"), limit=160) or project_id
    if not project_id or not name:
        raise AppError("PROJECT_INVALID", 400, {"detail": "project_id/name 不能为空"})
    normalized = _validate_project_payload({
        "id": project_id,
        "name": name,
        "type": kind,
        "visibility": manifest.get("visibility") or "department",
        "status": manifest.get("status") or "published",
    })

    department_id = _text(manifest.get("department_id"), limit=50)
    department = _text(manifest.get("department"), limit=100)
    if department_id and not await can_access_department(db, user, department_id):
        raise AppError("AUTH_DEPARTMENT_DENIED", 403)
    if not department_id and not department:
        department_id, department = await _default_department(db, user)

    project = await db.get(Project, project_id)
    if project:
        if not await can_write_project(db, user, project):
            raise AppError("PROJECT_ACCESS_DENIED", 403)
    elif not await can_write_project(db, user, None, department_id=department_id):
        raise AppError("PROJECT_ACCESS_DENIED", 403)

    version_token = actual_hash[:16]
    explicit_entry_url = _safe_project_entry_url(manifest.get("entry_url"))
    manifest_entry = _text(manifest.get("entry"), limit=4000)
    entry = explicit_entry_url or manifest_entry or PROJECT_STATIC_ENTRY_CANDIDATES[0]
    artifact_type = "external_url" if explicit_entry_url else "static_dist"
    entry_url = explicit_entry_url
    local_entry = None
    asset_rewrites: dict[str, Any] = {}
    gateway_bootstrap: dict[str, Any] = {}
    service_conversion: dict[str, Any] = {}
    runtime_evaluation: dict[str, Any] = {}
    package_security: dict[str, Any] = {}
    if artifact_type == "static_dist":
        target_dir = PROJECT_ASSETS_DIR / project_id / version_token
        package_format, package_security = _extract_project_package(package_bytes, target_dir)
        local_entry = _detect_project_static_entry(target_dir, manifest_entry)
        if not (target_dir / local_entry).is_file():
            raise AppError(
                "PROJECT_INVALID",
                400,
                {
                    "field": "entry",
                    "reason": f"项目包中未找到入口 {local_entry}",
                    "candidates": list(PROJECT_STATIC_ENTRY_CANDIDATES),
                },
            )
        asset_rewrites = _rewrite_project_root_absolute_asset_refs(target_dir, project_id, version_token, local_entry)
        gateway_bootstrap = _inject_project_gateway_bootstrap_if_needed(target_dir, local_entry, manifest)
        _validate_project_static_entry_runnable(target_dir, local_entry)
        service_conversion = await _build_project_service_conversion(
            db,
            user,
            project_id=project_id,
            version_token=version_token,
            target_dir=target_dir,
            entry=local_entry,
            manifest=manifest,
            asset_rewrites=asset_rewrites,
            gateway_bootstrap=gateway_bootstrap,
        )
        runtime_evaluation = _evaluate_project_static_runtime(
            target_dir,
            project_id,
            version_token,
            local_entry,
            manifest=manifest,
            asset_rewrites=asset_rewrites,
            gateway_bootstrap=gateway_bootstrap,
            service_conversion=service_conversion,
            package_security=package_security,
        )
        entry = local_entry
        entry_url = _project_asset_url(project_id, version_token, local_entry)
    else:
        package_format = "external_url"

    now = now_bjt()
    submit_metadata = _safe_dict(submit_metadata)
    sf_auto_build = _safe_dict(submit_metadata.get("sf_auto_build"))

    if not project:
        project = Project(
            id=project_id,
            name=normalized["name"],
            description=_text(manifest.get("description"), limit=4000),
            type=normalized["type"],
            department_id=department_id,
            department=department,
            owner_user_id=str(getattr(user, "id", "") or "") or None,
            visibility=normalized["visibility"],
            status=normalized["status"],
            entry_url=entry_url,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        db.add(project)
    else:
        project.name = normalized["name"]
        project.description = _text(manifest.get("description"), limit=4000) or project.description
        project.type = normalized["type"]
        project.visibility = normalized["visibility"]
        project.status = normalized["status"]
        project.department_id = department_id or project.department_id
        project.department = department or project.department
        project.entry_url = entry_url
        project.updated_at = now

    package_hash_ref = f"sha256:{actual_hash}"
    manifest_version = _project_version_text(manifest.get("version"), version_token)
    effective_version = manifest_version
    version_id = f"pver_{project_id[:24]}_{version_token}"
    version = await db.get(ProjectVersion, version_id)
    if not version:
        same_manifest_version = (
            await db.execute(
                select(ProjectVersion)
                .where(ProjectVersion.project_id == project_id, ProjectVersion.version == manifest_version)
                .limit(1)
            )
        ).scalar_one_or_none()
        version_conflict_resolved = False
        if same_manifest_version and same_manifest_version.sf_package_hash != package_hash_ref:
            effective_version = _project_version_with_upload_suffix(manifest_version, version_token)
            version_conflict_resolved = True
        version_metadata = {
            "manifest": manifest,
            "entry": entry,
            "local_entry": local_entry,
            "package_format": package_format,
            "manifest_version": manifest_version,
            "effective_version": effective_version,
            "asset_rewrites": asset_rewrites,
            "gateway_bootstrap": gateway_bootstrap,
            "service_conversion": service_conversion,
            "runtime_evaluation": runtime_evaluation,
            "package_security": package_security,
        }
        if version_conflict_resolved:
            version_metadata["version_conflict_resolved"] = True
            version_metadata["version_conflict_reason"] = "same_manifest_version_different_package_hash"
        if sf_auto_build:
            version_metadata["sf_auto_build"] = sf_auto_build
        version = ProjectVersion(
            id=version_id,
            project_id=project_id,
            version=effective_version,
            artifact_type=artifact_type,
            artifact_ref=entry_url,
            sf_package_hash=package_hash_ref,
            created_by=str(getattr(user, "id", "") or "") or None,
            metadata_json=version_metadata,
            created_at=now,
        )
        db.add(version)
    metadata_json = {
        **_safe_dict(project.metadata_json),
        "source": "sf_project_submit",
        "manifest_metadata": _safe_dict(manifest.get("metadata")),
        "submit_metadata": submit_metadata,
        "package_hash": package_hash_ref,
        "entry": entry,
        "local_entry": local_entry,
        "package_format": package_format,
        "manifest_version": manifest_version,
        "effective_version": version.version,
        "asset_rewrites": asset_rewrites,
        "gateway_bootstrap": gateway_bootstrap,
        "service_conversion": service_conversion,
        "runtime_evaluation": runtime_evaluation,
        "package_security": package_security,
        "capabilities": _safe_list(manifest.get("capabilities")),
        "outputs": _safe_dict(manifest.get("outputs")),
    }
    if sf_auto_build:
        metadata_json["sf_auto_build"] = sf_auto_build
    project.current_version_id = version.id
    project.metadata_json = metadata_json
    await db.flush()
    item = await _serialize_project(db, user, project)
    item["package_hash"] = package_hash_ref
    item["asset_version"] = version_token
    return item


async def project_asset_path(
    db: AsyncSession,
    user: User,
    project_id: str,
    version_token: str,
    asset_path: str,
) -> Path:
    if not PROJECT_ID_PATTERN.match(str(project_id or "")):
        raise AppError("PROJECT_NOT_FOUND", 404, {"project_id": project_id})
    token = _text(version_token, limit=80) or ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", token):
        raise AppError("PROJECT_NOT_FOUND", 404, {"version": version_token})
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("PROJECT_NOT_FOUND", 404)
    if not await can_read_project(db, user, project):
        raise AppError("PROJECT_ACCESS_DENIED", 403)

    version_id = f"pver_{project_id[:24]}_{token}"
    version = await db.get(ProjectVersion, version_id)
    if not version or version.project_id != project_id or version.artifact_type != "static_dist":
        raise AppError("PROJECT_NOT_FOUND", 404, {"project_id": project_id, "version": token})

    root = (PROJECT_ASSETS_DIR / project_id / token).resolve()
    rel = _safe_entry_path(asset_path)
    path = (root / rel).resolve()
    if path != root and not str(path).startswith(f"{root}/"):
        raise AppError("PROJECT_NOT_FOUND", 404, {"path": asset_path})
    if not path.is_file():
        local_entry = _text(_safe_dict(version.metadata_json).get("local_entry"), limit=4000)
        if _should_fallback_to_project_entry(rel) and local_entry:
            fallback_rel = _safe_entry_path(local_entry)
            fallback = (root / fallback_rel).resolve()
            if fallback != root and str(fallback).startswith(f"{root}/") and fallback.is_file():
                return fallback
        raise AppError("PROJECT_NOT_FOUND", 404, {"path": rel})
    return path


def serialize_run(run: ProjectRun) -> dict[str, Any]:
    output = _safe_dict(run.output_result)
    report_cards = extract_report_cards(run.decision_log_id or 0, output) if run.decision_log_id else []
    report_count = _report_count(run)
    todo_count = _todo_count(run)
    liveness = _run_liveness(run)
    return {
        "id": run.id,
        "project_id": run.project_id,
        "version_id": run.version_id,
        "execution_run_id": run.execution_run_id,
        "request_id": getattr(run, "request_id", None),
        "decision_log_id": run.decision_log_id,
        "user_id": run.user_id,
        "department_id": run.department_id,
        "department": run.department,
        "status": run.status,
        **liveness,
        "input_snapshot": run.input_snapshot or {},
        "output_result": output,
        "ai_summary": run.ai_summary or {},
        "report_cards": report_cards,
        "report_count": report_count,
        "todo_count": todo_count,
        "capability_call_count": int(getattr(run, "capability_call_count", 0) or 0),
        "error": run.error,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "last_heartbeat_at": _iso(run.last_heartbeat_at),
        "completed_at": _iso(run.completed_at),
        "updated_at": _iso(run.updated_at),
    }


def _evaluate_project_run_result(
    run: ProjectRun,
    project: Project,
    input_snapshot: dict[str, Any],
    output: dict[str, Any],
    *,
    auto_analyze: bool,
) -> dict[str, Any]:
    reports = _safe_list(output.get("reports"))
    todos = _safe_list(output.get("todos"))
    proofs = _safe_list(output.get("proofs")) or _safe_list(_safe_dict(output.get("_skillforge_meta")).get("proofs"))
    checks = [
        _runtime_eval_check(
            "input_recorded",
            "输入经过平台",
            "pass" if bool(input_snapshot) else "warn",
            "已记录输入快照" if bool(input_snapshot) else "没有显式输入，可能只是打开页面",
        ),
        _runtime_eval_check(
            "output_recorded",
            "输出经过平台",
            "pass" if bool(output) else "fail",
            "已记录项目输出" if bool(output) else "没有收到项目输出",
        ),
        _runtime_eval_check(
            "reports",
            "报告入库",
            "pass" if reports else "warn",
            f"报告 {len(reports)} 条" if reports else "未返回 reports[]，用户难以评估结果",
            count=len(reports),
        ),
        _runtime_eval_check(
            "todos",
            "待办闭环",
            "pass" if todos else "warn",
            f"待办 {len(todos)} 条" if todos else "未返回 todos[]，后续改进无法派发",
            count=len(todos),
        ),
        _runtime_eval_check(
            "proofs",
            "证据链",
            "pass" if proofs else "warn",
            f"证据 {len(proofs)} 条" if proofs else "未返回 proofs[]，复杂项目建议补充运行证据",
            count=len(proofs),
        ),
        _runtime_eval_check(
            "ai_loop",
            "AI 循环",
            "pass" if auto_analyze or reports or todos else "warn",
            "输出将进入 AI 分析/报告/待办循环" if auto_analyze or reports or todos else "仅记录运行，尚未形成分析循环",
            auto_analyze=auto_analyze,
        ),
    ]
    if output.get("error"):
        checks.append(_runtime_eval_check("runtime_error", "运行错误", "fail", _text(output.get("error"), limit=1000) or "项目返回错误"))
    status = _runtime_eval_status(checks)
    return {
        "status": status,
        "normal_result_ready": status != "fail",
        "summary": "结果可正常评估" if status == "pass" else "结果已记录但闭环信息不足" if status == "warn" else "结果存在错误，需修复",
        "checks": checks,
        "project_id": project.id,
        "project_run_id": run.id,
        "evaluated_at": isoformat_bjt(now_bjt()),
        "evaluator": "skillforge_project_result_v1",
    }


def _merge_run_ai_summary(run: ProjectRun, summary: dict[str, Any]) -> dict[str, Any]:
    existing = _safe_dict(getattr(run, "ai_summary", None))
    merged = {**existing, **_safe_dict(summary)}
    if "result_evaluation" in existing and "result_evaluation" not in merged:
        merged["result_evaluation"] = existing["result_evaluation"]
    return merged


async def create_project_run(db: AsyncSession, user: User, project_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or {}
    if project_id.startswith("playbook:"):
        raise AppError("PROJECT_INVALID", 400, {"detail": "Playbook 项目请直接打开 /playbook/<name>"})
    project = await db.get(Project, project_id)
    if not project:
        raise AppError("PROJECT_NOT_FOUND", 404)
    if not await can_read_project(db, user, project):
        raise AppError("PROJECT_ACCESS_DENIED", 403)
    if project.status == "archived":
        raise AppError("PROJECT_INVALID", 400, {"detail": "项目已归档，不能运行"})

    user_id = str(getattr(user, "id", "") or "") or None
    project_id_value = project.id
    request_id = _request_id(data)
    if request_id:
        existing = (
            await db.execute(
                select(ProjectRun)
                .where(ProjectRun.project_id == project_id_value, ProjectRun.user_id == user_id, ProjectRun.request_id == request_id)
                .order_by(ProjectRun.created_at.desc())
            )
        ).scalars().first()
        if existing:
            row = serialize_run(existing)
            row["deduped"] = True
            return row

    capacity = await _ensure_project_run_capacity(db)
    now = now_bjt()
    execution_id = uuid4().hex
    params_snapshot = _safe_dict(data.get("params"))
    explicit_input = _safe_dict(data.get("input"))
    input_snapshot = {**params_snapshot, **explicit_input} if params_snapshot or explicit_input else {}
    project_run = ProjectRun(
        id=_new_id("prun", 24),
        project_id=project.id,
        version_id=project.current_version_id,
        execution_run_id=execution_id,
        request_id=request_id,
        user_id=user_id,
        department_id=project.department_id,
        department=project.department,
        status="running",
        input_snapshot=input_snapshot,
        output_result={},
        ai_summary={},
        created_at=now,
        started_at=now,
        last_heartbeat_at=now,
        updated_at=now,
    )
    execution = ExecutionRun(
        id=execution_id,
        skill_id=_project_skill_id(project.id),
        playbook_id=None,
        trigger_type=f"project:{project.type}",
        run_mode=RUN_MODE_SDK_SUBMIT,
        parent_run_id=_text(data.get("parent_run_id"), limit=50),
        started_at=now,
        status="running",
        total_steps=1,
        completed_steps=0,
        summary=f"项目 {project.name} 已打开，等待前端输出回传。",
        business_value_tag="project_host",
        business_ref_id=project.id,
        metadata_json={
            "project_id": project.id,
            "project_name": project.name,
            "project_type": project.type,
            "project_run_id": project_run.id,
            "project_run_request_id": request_id,
            "project_version_id": project.current_version_id,
            "entry_url": project.entry_url,
            "containerized": False,
            "user_id": project_run.user_id,
            "department_id": project_run.department_id,
            "department": project_run.department,
            "capacity_at_open": {
                "target_concurrent_runs": capacity["target_concurrent_runs"],
                "active_runs_before_open": capacity["active_runs"],
                "available_run_slots_before_open": capacity["available_run_slots"],
                "concurrency_state": capacity["concurrency_state"],
                "lock": capacity.get("capacity_lock"),
            },
        },
    )
    db.add(execution)
    db.add(project_run)
    project.updated_at = now
    try:
        await db.flush()
    except IntegrityError:
        if not request_id:
            raise
        await db.rollback()
        existing = (
            await db.execute(
                select(ProjectRun)
                .where(ProjectRun.project_id == project_id_value, ProjectRun.user_id == user_id, ProjectRun.request_id == request_id)
                .order_by(ProjectRun.created_at.desc())
            )
        ).scalars().first()
        if not existing:
            raise
        row = serialize_run(existing)
        row["deduped"] = True
        return row
    try:
        await capture_project_run_opened(db, project_run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目打开写入学习事件失败 run={}: {}", project_run.id, exc)
    return serialize_run(project_run)


async def heartbeat_project_run(db: AsyncSession, user: User, project_run_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or {}
    run, project = await _require_run(db, user, project_run_id, require_write=True)
    now = now_bjt()
    previous_status = str(run.status or "")
    if previous_status not in PROJECT_RUN_TERMINAL_STATUSES:
        requested_status = (_text(data.get("status"), limit=30) or "running").lower()
        run.status = requested_status if requested_status in PROJECT_RUN_ACTIVE_STATUSES else "running"
        run.error = None if run.status in PROJECT_RUN_ACTIVE_STATUSES else run.error
    run.last_heartbeat_at = now
    run.updated_at = now
    execution = await db.get(ExecutionRun, run.execution_run_id) if run.execution_run_id else None
    if execution:
        if previous_status == "stale" and run.status in PROJECT_RUN_ACTIVE_STATUSES:
            execution.status = "running"
            execution.summary = f"项目 {project.name} 心跳已恢复，继续等待前端输出回传。"
            execution.completed_at = None
        execution.metadata_json = {
            **_safe_dict(execution.metadata_json),
            "project_run_id": run.id,
            "project_id": project.id,
            "last_project_heartbeat_at": isoformat_bjt(now),
            "last_project_heartbeat_restored": previous_status == "stale" and run.status in PROJECT_RUN_ACTIVE_STATUSES,
            "heartbeat_payload": _redact_trace_value(_safe_dict(data.get("metadata"))),
        }
    await db.flush()
    return serialize_run(run)


async def close_project_run(db: AsyncSession, user: User, project_run_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or {}
    run, project = await _require_run(db, user, project_run_id, require_write=True)
    now = now_bjt()
    previous_status = str(run.status or "")
    should_close = previous_status in PROJECT_RUN_ACTIVE_STATUSES or previous_status == "stale"
    if should_close:
        run.status = "completed"
        run.completed_at = run.completed_at or now
        run.error = None if previous_status == "stale" else run.error
    run.last_heartbeat_at = now
    run.updated_at = now

    execution = await db.get(ExecutionRun, run.execution_run_id) if run.execution_run_id else None
    if execution:
        execution.metadata_json = {
            **_safe_dict(execution.metadata_json),
            "project_run_id": run.id,
            "project_id": project.id,
            "last_project_closed_at": isoformat_bjt(now),
            "last_project_close_request_id": _request_id(data),
            "last_project_close_previous_status": previous_status,
            "last_project_close_released_capacity": should_close and previous_status in PROJECT_RUN_ACTIVE_STATUSES,
            "close_payload": _redact_trace_value(_safe_dict(data.get("metadata"))),
        }
        if should_close and str(execution.status or "") not in PROJECT_RUN_TERMINAL_STATUSES:
            execution.status = "completed"
            execution.completed_at = now
            execution.completed_steps = execution.completed_steps or 1
            execution.summary = f"项目 {project.name} 网页已关闭，运行槽位已释放。"

    await db.flush()
    if execution and should_close:
        try:
            await capture_execution_run(db, execution, user_id=run.user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("项目关闭写入学习事件失败 run={}: {}", run.id, exc)
    row = serialize_run(run)
    row["closed"] = True
    row["capacity_released"] = should_close and previous_status in PROJECT_RUN_ACTIVE_STATUSES
    row["previous_status"] = previous_status
    return row


async def _require_run(db: AsyncSession, user: User, project_run_id: str, *, require_write: bool = False) -> tuple[ProjectRun, Project]:
    run = await db.get(ProjectRun, project_run_id)
    if not run:
        raise AppError("PROJECT_RUN_NOT_FOUND", 404)
    project = await db.get(Project, run.project_id)
    if not project:
        raise AppError("PROJECT_NOT_FOUND", 404)
    if not await can_read_project(db, user, project):
        raise AppError("PROJECT_ACCESS_DENIED", 403)
    allowed = (
        await can_write_project_run(db, user, project, run)
        if require_write
        else await can_read_project_run(db, user, project, run)
    )
    if not allowed:
        raise AppError("PROJECT_ACCESS_DENIED", 403, {"detail": "无权访问该项目运行"})
    return run, project


def _project_sdk_trace_url(project_id: str, project_run_id: str) -> str:
    return f"/projects/{project_id}?run_id={project_run_id}"


def _with_project_sdk_response(result: dict[str, Any], *, training_sink: dict[str, Any] | None = None) -> dict[str, Any]:
    project_run_id = str(result.get("project_run_id") or result.get("id") or "")
    project_id = str(result.get("project_id") or "")
    payload = {
        **result,
        "ok": result.get("ok", True),
        "projectRunId": project_run_id or None,
        "project_run_id": project_run_id or result.get("project_run_id"),
        "traceUrl": _project_sdk_trace_url(project_id, project_run_id) if project_id and project_run_id else None,
        "trace_url": _project_sdk_trace_url(project_id, project_run_id) if project_id and project_run_id else None,
        "credential_location": "platform_only",
    }
    if training_sink is not None:
        payload["trainingSink"] = training_sink
        payload["training_sink"] = training_sink
    return payload


async def _require_project_sdk_run(
    db: AsyncSession,
    principal: ProjectSdkPrincipal,
    project_run_id: str,
    *,
    require_write: bool = False,
) -> tuple[ProjectRun, Project]:
    run, project = await _require_run(db, principal.user, project_run_id, require_write=require_write)
    if project.id != principal.project.id:
        raise AppError("PROJECT_SDK_PROJECT_DENIED", 403, {"project_id": project.id})
    return run, project


async def sdk_create_project_run(db: AsyncSession, principal: ProjectSdkPrincipal, data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or {}
    requested_project_id = _text(data.get("project_id"), limit=42)
    if requested_project_id and requested_project_id != principal.project.id:
        raise AppError("PROJECT_SDK_PROJECT_DENIED", 403, {"project_id": requested_project_id})
    result = await create_project_run(
        db,
        principal.user,
        principal.project.id,
        {
            "request_id": data.get("request_id") or data.get("requestId"),
            "params": _safe_dict(data.get("params")),
            "input": _safe_dict(data.get("input")),
            "parent_run_id": data.get("parent_run_id") or data.get("parentRunId"),
        },
    )
    run = await db.get(ProjectRun, result["id"])
    if run:
        execution = await db.get(ExecutionRun, run.execution_run_id) if run.execution_run_id else None
        if execution:
            execution.metadata_json = {
                **_safe_dict(execution.metadata_json),
                "project_gateway_sdk": {
                    "token_id": principal.token.id,
                    "token_prefix": principal.token.token_prefix,
                    "source": "server_sdk",
                    "metadata": _redact_trace_value(_safe_dict(data.get("metadata"))),
                },
            }
        sink = await enqueue_project_sdk_training_sink(
            db,
            principal.user,
            principal.project,
            run,
            source_type="project_run",
            source_id=run.id,
            source_status="opened",
            request_id=_request_id(data),
        )
    else:
        sink = None
    return _with_project_sdk_response(result, training_sink=sink)


async def sdk_record_project_input(db: AsyncSession, principal: ProjectSdkPrincipal, project_run_id: str, data: dict[str, Any]) -> dict[str, Any]:
    run, project = await _require_project_sdk_run(db, principal, project_run_id, require_write=True)
    metadata = {
        **_safe_dict(data.get("metadata")),
        "source": "project_gateway_sdk",
        "sdk_token_id": principal.token.id,
        "sdk_token_prefix": principal.token.token_prefix,
    }
    result = await record_project_input_event(db, principal.user, run.id, {**data, "metadata": metadata})
    sink = await enqueue_project_sdk_training_sink(
        db,
        principal.user,
        project,
        run,
        source_type="project_ingress_event",
        source_id=result.get("input_event_id"),
        source_status="completed",
        request_id=_request_id(data),
    )
    return _with_project_sdk_response({**result, "project_id": project.id, "project_run_id": run.id}, training_sink=sink)


async def sdk_call_project_capability(db: AsyncSession, principal: ProjectSdkPrincipal, project_run_id: str, data: dict[str, Any]) -> dict[str, Any]:
    run, project = await _require_project_sdk_run(db, principal, project_run_id, require_write=True)
    sdk_data = {
        **_safe_dict(data),
        "_sdk_token_id": principal.token.id,
        "_sdk_token_prefix": principal.token.token_prefix,
    }
    try:
        result = await call_project_capability(db, principal.user, run.id, sdk_data)
    except AppError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        call_id = detail.get("call_id")
        if call_id:
            await enqueue_project_sdk_training_sink(
                db,
                principal.user,
                project,
                run,
                source_type="project_capability_call",
                source_id=call_id,
                source_status="failed",
                request_id=_request_id(data),
            )
            await db.commit()
        raise
    call_id = result.get("call_id")
    sink = await enqueue_project_sdk_training_sink(
        db,
        principal.user,
        project,
        run,
        source_type="project_capability_call",
        source_id=call_id,
        source_status="completed" if result.get("ok") else "failed",
        request_id=_request_id(data),
    )
    return _with_project_sdk_response({**result, "project_id": project.id, "project_run_id": run.id}, training_sink=sink)


async def _finalize_project_openai_236_run(
    db: AsyncSession,
    principal: ProjectSdkPrincipal,
    project_run_id: str,
    *,
    model_id: str,
    capability_result: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> None:
    run = await db.get(ProjectRun, project_run_id)
    if not run:
        return
    now = now_bjt()
    failed = error is not None
    terminal_status = "failed" if failed else "completed"
    result = _safe_dict((capability_result or {}).get("result"))
    error_text = ""
    if failed:
        detail = getattr(error, "detail", None)
        error_text = str(detail or getattr(error, "message", None) or error or "")[:4000]
    if str(run.status or "") not in PROJECT_RUN_TERMINAL_STATUSES:
        run.status = terminal_status
        run.completed_at = run.completed_at or now
    run.last_heartbeat_at = now
    run.updated_at = now
    run.error = error_text or None
    run.output_result = _redact_trace_value(
        {
            **_safe_dict(run.output_result),
            "ok": not failed,
            "source": "openai_236_proxy",
            "model": model_id,
            "target_gateway_id": PROJECT_RESIDENT_MODEL_GATEWAY_ID,
            "capability_call_id": (capability_result or {}).get("call_id"),
            "data_sink": result.get("data_sink") or PROJECT_TRAINING_SINK_DEFAULT_GATEWAY_ID,
            "usage": _safe_dict(result.get("usage")),
            "context": _safe_dict(result.get("context")),
            "output": result.get("output") if not failed else None,
            "error": error_text if failed else None,
        }
    )

    execution = await db.get(ExecutionRun, run.execution_run_id) if run.execution_run_id else None
    if execution:
        execution.metadata_json = {
            **_safe_dict(execution.metadata_json),
            "project_openai_236_proxy": {
                "project_id": principal.project.id,
                "project_run_id": run.id,
                "model": model_id,
                "target_gateway_id": PROJECT_RESIDENT_MODEL_GATEWAY_ID,
                "status": terminal_status,
                "completed_at": isoformat_bjt(now),
                "capability_call_id": (capability_result or {}).get("call_id"),
                "credential_location": "platform_only",
            },
        }
        if str(execution.status or "") not in PROJECT_RUN_TERMINAL_STATUSES:
            execution.status = terminal_status
            execution.completed_at = now
            execution.completed_steps = execution.completed_steps or 1
        execution.summary = (
            f"Project OpenAI 236 proxy call failed for model {model_id}: {error_text[:500]}"
            if failed
            else f"Project OpenAI 236 proxy call completed for model {model_id}."
        )

    await db.flush()
    if execution:
        try:
            await capture_execution_run(db, execution, user_id=run.user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Project OpenAI 236 proxy run capture failed run={}: {}", run.id, exc)


async def project_openai_236_models(db: AsyncSession, actor: User | Any | ProjectSdkPrincipal | None = None) -> dict[str, Any]:
    user = actor.user if isinstance(actor, ProjectSdkPrincipal) else actor
    catalog = await get_project_236_model_catalog(db, user)
    return {
        "object": "list",
        "data": [
            {
                "id": str(item.get("id") or ""),
                "object": "model",
                "created": int(time()),
                "owned_by": "skillforge",
                "gateway_id": item.get("gateway_id") or PROJECT_RESIDENT_MODEL_GATEWAY_ID,
                "loaded": bool(item.get("loaded")),
                "callable": bool(item.get("callable")),
                "runtime_profile": item.get("runtime_profile"),
                "deployment_id": item.get("deployment_id"),
                "context_window": item.get("context_window"),
                "measured_context_window": item.get("measured_context_window"),
                "reported_context_window": item.get("reported_context_window"),
                "max_input_tokens": item.get("max_input_tokens"),
                "recommended_input_tokens": item.get("recommended_input_tokens"),
                "max_output_tokens": item.get("max_output_tokens"),
                "context_status": item.get("context_status"),
                "context_verified_at": item.get("context_verified_at"),
                "safety_margin_tokens": item.get("safety_margin_tokens"),
            }
            for item in _safe_list(catalog.get("data"))
            if isinstance(item, dict) and item.get("id")
        ],
        "gateway": catalog.get("gateway"),
        "total": catalog.get("total"),
        "callable_count": catalog.get("callable_count"),
        "loaded_count": catalog.get("loaded_count"),
    }


async def project_openai_236_chat_completion(
    db: AsyncSession,
    principal: ProjectSdkPrincipal,
    payload: dict | None = None,
) -> dict[str, Any]:
    _ensure_project_sdk_scope(principal, "runs:create")
    _ensure_project_sdk_scope(principal, "runs:capability")
    raw = payload if isinstance(payload, dict) else {}
    model_id = _text(raw.get("model") or raw.get("model_id"), limit=180)
    if not model_id:
        raise AppError("PROJECT_OPENAI_236_INVALID_REQUEST", 422, {"detail": "model is required"})
    messages = _safe_list(raw.get("messages"))
    tools = _safe_list(raw.get("tools"))
    tool_choice = raw.get("tool_choice") if "tool_choice" in raw else raw.get("toolChoice")
    prompt = _project_openai_messages_to_prompt(messages, tools=tools, tool_choice=tool_choice) or _project_openai_append_tools_prompt(
        _text(raw.get("prompt"), limit=PROJECT_236_PROMPT_TEXT_LIMIT_CHARS),
        tools,
        tool_choice,
    )
    if not prompt:
        raise AppError("PROJECT_OPENAI_236_INVALID_REQUEST", 422, {"detail": "messages or prompt is required"})

    suffix = _text(raw.get("request_id") or raw.get("idempotency_key") or raw.get("idempotencyKey"), limit=48)
    if not suffix:
        suffix = uuid4().hex[:24]
    run_request_id = f"openai236:{suffix}"[:80]
    cap_request_id = "openai236-chat:" + hashlib.sha256(f"{principal.project.id}:{run_request_id}:{model_id}".encode()).hexdigest()[:16]
    run_result = await sdk_create_project_run(
        db,
        principal,
        {
            "request_id": run_request_id,
            "params": {
                "source": "openai_236_proxy",
                "target_gateway_id": PROJECT_RESIDENT_MODEL_GATEWAY_ID,
                "model": model_id,
                "stream": bool(raw.get("stream") is True),
            },
            "input": {
                "messages": messages,
                "prompt": raw.get("prompt"),
                "model": model_id,
                "tools": tools,
                "tool_choice": tool_choice,
                "max_input_tokens": raw.get("max_input_tokens") or raw.get("maxInputTokens"),
                "context_window": raw.get("context_window") or raw.get("contextWindow"),
                "truncation": raw.get("truncation") or "disabled",
            },
            "metadata": {
                "source": "openai_236_proxy",
                "sdk_token_id": principal.token.id,
                "sdk_token_prefix": principal.token.token_prefix,
            },
        },
    )
    project_run_id = str(run_result.get("project_run_id") or run_result.get("id") or "")
    capability_payload = {
        "request_id": cap_request_id,
        "capability": "ai.chat",
        "target_gateway_id": PROJECT_RESIDENT_MODEL_GATEWAY_ID,
        "model": model_id,
        "messages": messages,
        "prompt": raw.get("prompt"),
        "tools": tools,
        "tool_choice": tool_choice,
        "input": {
            "messages": messages,
            "prompt": raw.get("prompt"),
            "tools": tools,
            "tool_choice": tool_choice,
            "source": "openai_236_proxy",
        },
        "max_tokens": raw.get("max_tokens") or raw.get("max_completion_tokens"),
        "max_input_tokens": raw.get("max_input_tokens") or raw.get("maxInputTokens"),
        "context_window": raw.get("context_window") or raw.get("contextWindow"),
        "truncation": raw.get("truncation") or "disabled",
        "temperature": raw.get("temperature"),
        "timeout_seconds": raw.get("timeout_seconds") or raw.get("timeoutSeconds"),
    }
    try:
        capability_result = await sdk_call_project_capability(db, principal, project_run_id, capability_payload)
    except Exception as exc:  # noqa: BLE001
        await _finalize_project_openai_236_run(db, principal, project_run_id, model_id=model_id, error=exc)
        raise
    result = _safe_dict(capability_result.get("result"))
    response = _project_openai_completion_response(
        model_id=model_id,
        text=result.get("output") or "",
        response_id=_text(result.get("response_id"), limit=120),
        finish_reason=str(result.get("finish_reason") or "stop"),
        usage=_safe_dict(result.get("usage")),
        tools=tools,
        usage_reliable=bool(result.get("usage_reliable")),
    )
    usage_reliable = bool(response.pop("_skillforge_usage_reliable", False))
    tool_call_compat = response.pop("_skillforge_tool_call_compat", None)
    response["skillforge"] = {
        "project_id": principal.project.id,
        "project_run_id": project_run_id,
        "trace_url": capability_result.get("trace_url") or capability_result.get("traceUrl"),
        "capability_call_id": capability_result.get("call_id"),
        "target_gateway_id": PROJECT_RESIDENT_MODEL_GATEWAY_ID,
        "credential_location": "platform_only",
        "training_sink": capability_result.get("training_sink") or capability_result.get("trainingSink"),
        "run_training_sink": run_result.get("training_sink") or run_result.get("trainingSink"),
        "usage_reliable": usage_reliable,
    }
    context_budget = _safe_dict(result.get("context"))
    if context_budget:
        response["skillforge"]["context"] = context_budget
    if tool_call_compat:
        response["skillforge"]["tool_call_compat"] = tool_call_compat
    await _finalize_project_openai_236_run(
        db,
        principal,
        project_run_id,
        model_id=model_id,
        capability_result=capability_result,
    )
    return response


async def sdk_ingest_project_output(db: AsyncSession, principal: ProjectSdkPrincipal, project_run_id: str, data: dict[str, Any]) -> dict[str, Any]:
    run, project = await _require_project_sdk_run(db, principal, project_run_id, require_write=True)
    metadata = {
        **_safe_dict(data.get("metadata")),
        "source": "project_gateway_sdk",
        "sdk_token_id": principal.token.id,
        "sdk_token_prefix": principal.token.token_prefix,
    }
    result = await ingest_project_output(db, principal.user, run.id, {**data, "metadata": metadata}, auto_analyze=bool(data.get("auto_analyze", data.get("autoAnalyze", True))))
    event_type = _text(data.get("event_type"), limit=40) or "output"
    request_id = _request_id(data)
    event = None
    if request_id:
        event = (
            await db.execute(
                select(ProjectIngressEvent)
                .where(
                    ProjectIngressEvent.project_run_id == run.id,
                    ProjectIngressEvent.event_type == event_type,
                    ProjectIngressEvent.request_id == request_id,
                )
                .order_by(ProjectIngressEvent.created_at.desc(), ProjectIngressEvent.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    sink = await enqueue_project_sdk_training_sink(
        db,
        principal.user,
        project,
        run,
        source_type="project_ingress_event" if event else "project_run",
        source_id=event.id if event else run.id,
        source_status="completed" if result.get("status") not in {"failed", "ai_failed"} else "failed",
        request_id=request_id,
    )
    return _with_project_sdk_response(result, training_sink=sink)


async def sdk_get_project_run_trace(
    db: AsyncSession,
    principal: ProjectSdkPrincipal,
    project_run_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    ingress_cursor: str | None = None,
    capability_cursor: str | None = None,
) -> dict[str, Any]:
    run, _project = await _require_project_sdk_run(db, principal, project_run_id)
    result = await get_project_run_trace(
        db,
        principal.user,
        run.id,
        limit=limit,
        offset=offset,
        ingress_cursor=ingress_cursor,
        capability_cursor=capability_cursor,
    )
    result["credential_location"] = "platform_only"
    return result


async def sdk_sync_project_run_training_sink(
    db: AsyncSession,
    principal: ProjectSdkPrincipal,
    project_run_id: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = data or {}
    run, project = await _require_project_sdk_run(db, principal, project_run_id, require_write=True)
    sink = await enqueue_project_sdk_training_sink(
        db,
        principal.user,
        project,
        run,
        source_type="project_run",
        source_id=run.id,
        source_status="manual_sync",
        request_id=_request_id(data),
    )
    return {"ok": True, "project_id": project.id, "project_run_id": run.id, "trainingSink": sink, "training_sink": sink}


async def sdk_upload_project_run_asset(
    db: AsyncSession,
    principal: ProjectSdkPrincipal,
    project_run_id: str,
    *,
    file_name: str,
    mime_type: str | None,
    content: bytes,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run, project = await _require_project_sdk_run(db, principal, project_run_id, require_write=True)
    result = await upload_project_run_asset(
        db,
        principal.user,
        run.id,
        file_name=file_name,
        mime_type=mime_type,
        content=content,
        metadata={
            **_safe_dict(metadata),
            "source": "project_gateway_sdk",
            "sdk_token_id": principal.token.id,
            "sdk_token_prefix": principal.token.token_prefix,
        },
    )
    asset = _safe_dict(result.get("asset"))
    training_asset: dict[str, Any] | None = None
    if asset.get("id"):
        try:
            from app.training.service import register_training_asset_from_project_run_asset

            training_asset = await register_training_asset_from_project_run_asset(
                db,
                principal.user,
                str(asset.get("id")),
                commit=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Project SDK 资产登记训练资产失败 asset={}: {}", asset.get("id"), exc)
    sink = await enqueue_project_sdk_training_sink(
        db,
        principal.user,
        project,
        run,
        source_type="project_run_asset",
        source_id=asset.get("id") or run.id,
        source_status="completed",
    )
    return _with_project_sdk_response(
        {
            "ok": True,
            "project_id": project.id,
            "project_run_id": run.id,
            **result,
            "trainingAsset": training_asset,
            "training_asset": training_asset,
        },
        training_sink=sink,
    )


async def sdk_record_training_sample(
    db: AsyncSession,
    principal: ProjectSdkPrincipal,
    project_run_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    run, project = await _require_project_sdk_run(db, principal, project_run_id, require_write=True)
    asset_id = _text(data.get("asset_id") or data.get("project_run_asset_id"), limit=50)
    source_payload: dict[str, Any] | None = None
    media_refs = data.get("media_refs") if isinstance(data.get("media_refs"), list) else []
    if asset_id:
        asset = await db.get(ProjectRunAsset, asset_id)
        if not asset or asset.project_run_id != run.id or asset.project_id != project.id:
            raise AppError("PROJECT_ASSET_NOT_FOUND", 404)
        source_payload = {
            "source_type": "project_run_asset",
            "source_id": asset.id,
            "title": asset.file_name,
            "department": run.department or project.department,
            "org_unit_id": run.department_id or project.department_id,
            "owner_user_id": asset.owner_user_id,
            "project_id": project.id,
            "project_run_id": run.id,
            "modality": "image_text" if str(asset.mime_type or "").lower().startswith("image/") else None,
            "storage_backend": asset.storage_backend,
            "storage_ref": asset.storage_path,
            "mime_type": asset.mime_type,
            "byte_size": asset.byte_size,
            "sha256": asset.sha256,
            "metadata": {
                "source": "project_gateway_sdk",
                "sdk_token_id": principal.token.id,
                "sdk_token_prefix": principal.token.token_prefix,
                "file_name": asset.file_name,
            },
        }
        if not media_refs:
            media_refs = [{
                "source_type": "project_run_asset",
                "asset_id": asset.id,
                "storage_backend": asset.storage_backend,
                "storage_ref": asset.storage_path,
                "mime_type": asset.mime_type,
                "sha256": asset.sha256,
                "byte_size": asset.byte_size,
            }]
    else:
        source_payload = {
            "source_type": "project_run",
            "source_id": run.id,
            "title": f"Project run {run.id}",
            "department": run.department or project.department,
            "org_unit_id": run.department_id or project.department_id,
            "owner_user_id": run.user_id,
            "project_id": project.id,
            "project_run_id": run.id,
            "modality": data.get("modality") or "text",
            "metadata": {
                "source": "project_gateway_sdk",
                "sdk_token_id": principal.token.id,
                "sdk_token_prefix": principal.token.token_prefix,
            },
        }
    from app.training.service import record_training_sample, register_training_asset_source

    source_result = await register_training_asset_source(db, principal.user, source_payload, commit=False)
    source = _safe_dict(source_result.get("asset_source"))
    sample_result = await record_training_sample(
        db,
        principal.user,
        {
            "source_id": source.get("id"),
            "dataset_profile": data.get("dataset_profile") or data.get("profile") or "text_sft_v1",
            "modality": data.get("modality") or source.get("modality"),
            "content": data.get("content") if isinstance(data.get("content"), dict) else {},
            "media_refs": media_refs,
            "labels": data.get("labels") if isinstance(data.get("labels"), list) else [],
            "quality_score": data.get("quality_score"),
            "metadata": {
                **_safe_dict(data.get("metadata")),
                "source": "project_gateway_sdk",
                "sdk_token_id": principal.token.id,
                "sdk_token_prefix": principal.token.token_prefix,
                "project_id": project.id,
                "project_run_id": run.id,
            },
        },
        commit=False,
    )
    sink = await enqueue_project_sdk_training_sink(
        db,
        principal.user,
        project,
        run,
        source_type="project_training_sample",
        source_id=_safe_dict(sample_result.get("sample")).get("id") or run.id,
        source_status="completed",
        request_id=_request_id(data),
    )
    return {
        "ok": True,
        "project_id": project.id,
        "project_run_id": run.id,
        "asset_source": source,
        "trainingSample": sample_result.get("sample"),
        "training_sample": sample_result.get("sample"),
        "trainingSink": sink,
        "training_sink": sink,
    }


async def sdk_list_trainable_assets(
    db: AsyncSession,
    principal: ProjectSdkPrincipal,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    from app.training.service import list_training_asset_sources

    result = await list_training_asset_sources(db, principal.user, department=principal.project.department, limit=limit)
    result["project_id"] = principal.project.id
    result["credential_location"] = "platform_only"
    return result


async def sdk_create_training_dataset_version(
    db: AsyncSession,
    principal: ProjectSdkPrincipal,
    data: dict[str, Any],
) -> dict[str, Any]:
    from app.training.service import create_training_dataset_version

    payload = {
        **_safe_dict(data),
        "department": data.get("department") or principal.project.department,
        "org_unit_id": data.get("org_unit_id") or principal.project.department_id,
    }
    result = await create_training_dataset_version(db, principal.user, payload, commit=False)
    dataset_version = _safe_dict(result.get("dataset_version"))
    return {
        "ok": True,
        "project_id": principal.project.id,
        "datasetVersion": dataset_version,
        "dataset_version": dataset_version,
    }


async def sdk_sync_training_dataset_version(
    db: AsyncSession,
    principal: ProjectSdkPrincipal,
    dataset_version_id: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.training.service import sync_training_dataset_version

    result = await sync_training_dataset_version(db, principal.user, dataset_version_id, data or {}, commit=False)
    result["project_id"] = principal.project.id
    return result


def _project_run_asset_root() -> Path:
    root = PROJECT_RUN_ASSETS_DIR.expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_asset_file_name(value: str | None) -> str:
    name = Path(str(value or "asset")).name.strip().replace("\x00", "")
    if not name or name in {".", ".."}:
        name = "asset"
    return name[:240]


def _asset_source_kind(file_name: str, mime_type: str | None) -> str:
    lower = file_name.lower()
    mime = str(mime_type or "").lower()
    if mime.startswith(("video/", "audio/", "image/")):
        return "media"
    if lower.endswith((".csv", ".json", ".jsonl", ".xlsx", ".xls")) or mime in {
        "text/csv",
        "application/csv",
        "application/json",
        "application/vnd.ms-excel",
    }:
        return "data"
    if mime.startswith("text/") or lower.endswith((".html", ".htm", ".md", ".txt", ".pdf", ".doc", ".docx")):
        return "document"
    return "file"


def _validate_run_asset_upload(file_name: str, mime_type: str | None, content: bytes) -> None:
    size = len(content)
    if size <= 0:
        raise AppError("PROJECT_ASSET_INVALID", 400, {"detail": "上传文件为空"})
    if size > PROJECT_RUN_ASSET_MAX_BYTES:
        raise AppError(
            "PROJECT_ASSET_TOO_LARGE",
            413,
            {"actual_bytes": size, "max_bytes": PROJECT_RUN_ASSET_MAX_BYTES},
        )
    if file_name.lower().endswith((".env", ".pem", ".key", ".p12", ".pfx")):
        raise AppError("PROJECT_ASSET_INVALID", 400, {"detail": "该文件类型不允许作为项目运行资产上传"})


def _stream_project_run_asset_to_temp(stream: Any, temp_path: Path, file_name: str) -> tuple[int, str, int]:
    """Copy one UploadFile spool to disk without materializing it in RAM."""

    if file_name.lower().endswith((".env", ".pem", ".key", ".p12", ".pfx")):
        raise AppError("PROJECT_ASSET_INVALID", 400, {"detail": "该文件类型不允许作为项目运行资产上传"})
    size = 0
    chunks = 0
    digest = hashlib.sha256()
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    with temp_path.open("wb") as target:
        while True:
            chunk = stream.read(PROJECT_RUN_ASSET_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > PROJECT_RUN_ASSET_MAX_BYTES:
                raise AppError(
                    "PROJECT_ASSET_TOO_LARGE",
                    413,
                    {"actual_bytes": size, "max_bytes": PROJECT_RUN_ASSET_MAX_BYTES},
                )
            target.write(chunk)
            digest.update(chunk)
            chunks += 1
    if size <= 0:
        raise AppError("PROJECT_ASSET_INVALID", 400, {"detail": "上传文件为空"})
    return size, digest.hexdigest(), chunks


def _probe_mp4_duration(path: Path) -> dict[str, Any]:
    """Read the ISO-BMFF movie header without decoding untrusted media."""

    def find_box(stream: Any, start: int, end: int, expected: bytes) -> tuple[int, int] | None:
        position = start
        while position + 8 <= end:
            stream.seek(position)
            header = stream.read(16)
            if len(header) < 8:
                return None
            size = int.from_bytes(header[0:4], "big")
            kind = header[4:8]
            header_size = 8
            if size == 1:
                if len(header) < 16:
                    return None
                size = int.from_bytes(header[8:16], "big")
                header_size = 16
            elif size == 0:
                size = end - position
            if size < header_size or position + size > end:
                return None
            if kind == expected:
                return position + header_size, position + size
            position += size
        return None

    try:
        file_size = path.stat().st_size
        with path.open("rb") as stream:
            moov = find_box(stream, 0, file_size, b"moov")
            if not moov:
                return {}
            mvhd = find_box(stream, moov[0], moov[1], b"mvhd")
            if not mvhd:
                return {}
            stream.seek(mvhd[0])
            payload = stream.read(min(40, mvhd[1] - mvhd[0]))
    except (OSError, ValueError):
        return {}
    if not payload:
        return {}
    version = payload[0]
    if version == 0 and len(payload) >= 20:
        timescale = int.from_bytes(payload[12:16], "big")
        duration = int.from_bytes(payload[16:20], "big")
    elif version == 1 and len(payload) >= 32:
        timescale = int.from_bytes(payload[20:24], "big")
        duration = int.from_bytes(payload[24:32], "big")
    else:
        return {}
    if timescale <= 0 or duration <= 0:
        return {}
    return {
        "status": "ok",
        "source": "server_mp4_parser",
        "duration_seconds": round(duration / timescale, 3),
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "streams": [],
    }


def _probe_project_media_file(path: Path, mime_type: str | None) -> dict[str, Any]:
    mime = str(mime_type or "").lower()
    if not (mime.startswith("video/") or mime.startswith("audio/")):
        return {}
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        fallback = _probe_mp4_duration(path) if mime in {"video/mp4", "audio/mp4"} else {}
        if fallback:
            return fallback
        return {"status": "unavailable", "source": "server_ffprobe", "detail": "ffprobe is not installed"}
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name:stream=codec_type,codec_name,width,height,duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "source": "server_ffprobe", "detail": _text(exc, limit=500)}
    if proc.returncode != 0:
        fallback = _probe_mp4_duration(path) if mime in {"video/mp4", "audio/mp4"} else {}
        if fallback:
            return fallback
        return {
            "status": "failed",
            "source": "server_ffprobe",
            "detail": _text(proc.stderr or "ffprobe failed", limit=500),
        }
    try:
        parsed = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        fallback = _probe_mp4_duration(path) if mime in {"video/mp4", "audio/mp4"} else {}
        if fallback:
            return fallback
        return {"status": "failed", "source": "server_ffprobe", "detail": "ffprobe returned invalid JSON"}
    format_row = _safe_dict(parsed.get("format"))
    streams = [item for item in _safe_list(parsed.get("streams")) if isinstance(item, dict)]
    duration_candidates = [format_row.get("duration"), *[item.get("duration") for item in streams]]
    duration = None
    for raw in duration_candidates:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            duration = value
            break
    if duration is None:
        fallback = _probe_mp4_duration(path) if mime in {"video/mp4", "audio/mp4"} else {}
        if fallback:
            return fallback
        return {"status": "failed", "source": "server_ffprobe", "detail": "media duration is unavailable"}

    def _stream_dimension(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    return {
        "status": "ok",
        "source": "server_ffprobe",
        "duration_seconds": round(duration, 3),
        "format_name": _text(format_row.get("format_name"), limit=120),
        "streams": [
            {
                "codec_type": _text(item.get("codec_type"), limit=30),
                "codec_name": _text(item.get("codec_name"), limit=60),
                "width": _stream_dimension(item.get("width")),
                "height": _stream_dimension(item.get("height")),
            }
            for item in streams[:8]
        ],
    }


def serialize_run_asset(asset: ProjectRunAsset, *, include_download_url: bool = False) -> dict[str, Any]:
    row = {
        "id": asset.id,
        "project_id": asset.project_id,
        "project_run_id": asset.project_run_id,
        "source_kind": asset.source_kind,
        "file_name": asset.file_name,
        "mime_type": asset.mime_type,
        "byte_size": asset.byte_size,
        "sha256": asset.sha256,
        "metadata": _redact_trace_value(asset.metadata_json or {}),
        "created_at": _iso(asset.created_at),
    }
    if include_download_url:
        row["download_url"] = f"/api/projects/runs/{asset.project_run_id}/assets/{asset.id}/download"
    return row


def project_run_asset_response_headers(asset: ProjectRunAsset) -> dict[str, str]:
    """Project assets are immutable by id/hash; keep private browser caches warm for review."""

    headers = {
        "Cache-Control": "private, max-age=43200, immutable",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
    }
    if asset.sha256:
        headers["ETag"] = f'"sha256-{asset.sha256}"'
    return headers


def _project_run_asset_abs_path(asset: ProjectRunAsset) -> Path:
    if asset.storage_backend != "local":
        raise AppError("PROJECT_ASSET_UNAVAILABLE", 404)
    rel = PurePosixPath(str(asset.storage_path or ""))
    if rel.is_absolute() or ".." in rel.parts:
        raise AppError("PROJECT_ASSET_UNAVAILABLE", 404)
    root = _project_run_asset_root()
    path = (root / rel.as_posix()).resolve()
    if root not in path.parents and path != root:
        raise AppError("PROJECT_ASSET_UNAVAILABLE", 404)
    if not path.is_file():
        raise AppError("PROJECT_ASSET_UNAVAILABLE", 404)
    return path


def _signed_asset_token(project_run_id: str, asset_id: str, expires_at: int) -> str:
    payload = f"{project_run_id}:{asset_id}:{int(expires_at)}"
    return hmac.new(str(settings.SECRET_KEY).encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _project_run_asset_signed_download_url(asset: ProjectRunAsset, *, ttl_seconds: int | None = None) -> str:
    base = str(settings.PUBLIC_BASE_URL or "").rstrip("/")
    expires_at = int(datetime.now().timestamp()) + int(ttl_seconds or PROJECT_RUN_ASSET_SIGNED_URL_TTL_SECONDS)
    token = _signed_asset_token(asset.project_run_id, asset.id, expires_at)
    run_id = quote(asset.project_run_id, safe="")
    asset_id = quote(asset.id, safe="")
    path = f"/api/projects/runs/{run_id}/assets/{asset_id}/public-download?expires={expires_at}&token={token}"
    return f"{base}{path}" if base else path


async def public_project_run_asset_path(
    db: AsyncSession,
    project_run_id: str,
    asset_id: str,
    *,
    expires: int,
    token: str,
) -> tuple[Path, ProjectRunAsset]:
    if not expires or int(expires) < int(datetime.now().timestamp()):
        raise AppError("PROJECT_ASSET_LINK_EXPIRED", 403)
    expected = _signed_asset_token(project_run_id, asset_id, int(expires))
    if not hmac.compare_digest(str(token or ""), expected):
        raise AppError("PROJECT_ASSET_LINK_INVALID", 403)
    asset = await db.get(ProjectRunAsset, asset_id)
    if not asset or asset.project_run_id != project_run_id:
        raise AppError("PROJECT_ASSET_NOT_FOUND", 404)
    return _project_run_asset_abs_path(asset), asset


def _image_data_url(mime: str | None, data: bytes) -> str:
    safe_mime = str(mime or "image/jpeg").split(";", 1)[0].strip() or "image/jpeg"
    return f"data:{safe_mime};base64,{base64.b64encode(data).decode('ascii')}"


def _asset_data_url(mime: str | None, data: bytes, *, default_mime: str = "application/octet-stream") -> str:
    safe_mime = str(mime or default_mime).split(";", 1)[0].strip() or default_mime
    return f"data:{safe_mime};base64,{base64.b64encode(data).decode('ascii')}"


def _asset_metadata_role(asset: ProjectRunAsset) -> str:
    metadata = _safe_dict(asset.metadata_json)
    return str(metadata.get("role") or metadata.get("material_role") or metadata.get("video_role") or "").strip()


def _asset_frame_label(asset: ProjectRunAsset) -> str:
    metadata = _safe_dict(asset.metadata_json)
    return str(metadata.get("frame_label") or metadata.get("frameLabel") or "").strip().lower()


def _asset_frame_time(asset: ProjectRunAsset) -> float:
    metadata = _safe_dict(asset.metadata_json)
    try:
        return float(metadata.get("frame_time") or metadata.get("frameTime") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_visual_frame_asset(asset: ProjectRunAsset) -> bool:
    metadata = _safe_dict(asset.metadata_json)
    mime = str(asset.mime_type or "").lower()
    file_name = str(asset.file_name or "").lower()
    if metadata.get("visual_frame") is True or str(metadata.get("source") or "") == "short_video_frame_capture":
        return mime.startswith("image/")
    return mime.startswith("image/") and "frame" in file_name


def _is_visual_video_asset(asset: ProjectRunAsset) -> bool:
    mime = str(asset.mime_type or "").lower()
    metadata = _safe_dict(asset.metadata_json)
    role = _asset_metadata_role(asset)
    source = str(metadata.get("source") or "")
    if not mime.startswith("video/"):
        return False
    if role in {"material_a", "material_b", "good", "bad"}:
        return True
    return source == "short_video_analysis_page"


def _normalise_material_role(role: str) -> str:
    normalized = str(role or "").strip()
    return {
        "good": "material_a",
        "bad": "material_b",
        "a": "material_a",
        "b": "material_b",
    }.get(normalized, normalized)


def _visual_asset_sort_key(asset: ProjectRunAsset) -> tuple[str, float, str]:
    return _asset_metadata_role(asset), _asset_frame_time(asset), asset.id


def _visual_label_priority(asset: ProjectRunAsset) -> int:
    return PROJECT_VISUAL_LABEL_PRIORITY.get(_asset_frame_label(asset), 50)


def _select_visual_frame_assets(frames: list[ProjectRunAsset], max_count: int) -> list[ProjectRunAsset]:
    """Keep a complete A/B narrative while bounding the model payload."""
    grouped: dict[str, list[ProjectRunAsset]] = {}
    for asset in sorted(frames, key=_visual_asset_sort_key):
        role = _asset_metadata_role(asset) or "unknown"
        grouped.setdefault(role, []).append(asset)

    if not grouped:
        return []

    max_count = max(max_count, 1)
    role_count = max(len(grouped), 1)
    per_role = max(1, max_count // role_count)
    remainder = max_count % role_count
    selected: list[ProjectRunAsset] = []
    seen_global: set[str] = set()

    def add(asset: ProjectRunAsset | None) -> None:
        if not asset or asset.id in seen_global or len(selected) >= max_count:
            return
        selected.append(asset)
        seen_global.add(asset.id)

    for role_index, role in enumerate(sorted(grouped)):
        role_frames = grouped[role]
        role_limit = per_role + (1 if role_index < remainder else 0)
        role_selected: list[ProjectRunAsset] = []
        role_seen: set[str] = set()

        def add_role(asset: ProjectRunAsset | None) -> None:
            if not asset or asset.id in role_seen or len(role_selected) >= role_limit:
                return
            role_selected.append(asset)
            role_seen.add(asset.id)

        ordered = sorted(role_frames, key=_visual_asset_sort_key)
        opening = next((asset for asset in ordered if _asset_frame_label(asset) in {"opening", "first", "start", "intro"}), None)
        ending = next((asset for asset in reversed(ordered) if _asset_frame_label(asset) in {"ending", "end", "closing", "last"}), None)
        middle = min(ordered, key=lambda asset: abs(_asset_frame_time(asset) - (ordered[-1] and _asset_frame_time(ordered[-1]) / 2))) if ordered else None
        add_role(opening or (ordered[0] if ordered else None))
        if role_limit >= 3:
            add_role(middle)
        if role_limit >= 2:
            add_role(ending or (ordered[-1] if ordered else None))
        for asset in sorted(ordered, key=lambda item: (_visual_label_priority(item), _asset_frame_time(item), item.id)):
            add_role(asset)

        for asset in sorted(role_selected, key=_visual_asset_sort_key):
            add(asset)

    for asset in sorted(frames, key=_visual_asset_sort_key):
        add(asset)
        if len(selected) >= max_count:
            break
    return selected


async def _project_visual_frame_assets(db: AsyncSession, run: ProjectRun) -> list[ProjectRunAsset]:
    assets = (
        await db.execute(
            select(ProjectRunAsset)
            .where(ProjectRunAsset.project_run_id == run.id)
            .order_by(ProjectRunAsset.created_at.asc(), ProjectRunAsset.id.asc())
        )
    ).scalars().all()
    frames = [asset for asset in assets if _is_visual_frame_asset(asset)]
    return _select_visual_frame_assets(frames, max(PROJECT_VISUAL_FRAME_MAX_COUNT, 1))


async def _project_visual_video_assets(db: AsyncSession, run: ProjectRun) -> list[ProjectRunAsset]:
    assets = (
        await db.execute(
            select(ProjectRunAsset)
            .where(ProjectRunAsset.project_run_id == run.id)
            .order_by(ProjectRunAsset.created_at.asc(), ProjectRunAsset.id.asc())
        )
    ).scalars().all()
    videos = [asset for asset in assets if _is_visual_video_asset(asset)]
    grouped: dict[str, ProjectRunAsset] = {}
    for asset in videos:
        role = _normalise_material_role(_asset_metadata_role(asset) or "unknown")
        grouped.setdefault(role, asset)
    selected = [grouped[role] for role in sorted(grouped) if grouped[role].byte_size <= PROJECT_VISUAL_VIDEO_MAX_BYTES]
    return selected[: max(PROJECT_VISUAL_VIDEO_MAX_COUNT, 1)]


def _visual_frame_asset_summary(asset: ProjectRunAsset) -> dict[str, Any]:
    metadata = _safe_dict(asset.metadata_json)
    return {
        "asset_id": asset.id,
        "role": _asset_metadata_role(asset) or None,
        "file_name": asset.file_name,
        "mime_type": asset.mime_type,
        "byte_size": asset.byte_size,
        "frame_time": metadata.get("frame_time") or metadata.get("frameTime"),
        "frame_label": metadata.get("frame_label") or metadata.get("frameLabel"),
        "source_video": metadata.get("source_video") or metadata.get("sourceVideo"),
    }


def _visual_video_asset_summary(asset: ProjectRunAsset) -> dict[str, Any]:
    metadata = _safe_dict(asset.metadata_json)
    return {
        "asset_id": asset.id,
        "role": _normalise_material_role(_asset_metadata_role(asset)) or None,
        "file_name": asset.file_name,
        "mime_type": asset.mime_type,
        "byte_size": asset.byte_size,
        "source": metadata.get("source"),
    }


def _visual_frame_content_parts(frames: list[tuple[ProjectRunAsset, bytes]], *, role: str = "") -> list[dict[str, Any]]:
    scope = f"素材 {role}" if role else "素材 A/B"
    parts: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"下面是本次短视频分析从{scope}抽取的关键帧序列。"
                "请按消费者看到视频的顺序分析：首屏钩子、商品露出、卖点/证据展开、节奏密度、结尾行动引导。"
                "关键帧不能代表全片每一秒，但必须基于可见画面做保守判断，不要编造未出现的信息。"
            ),
        }
    ]
    for index, (asset, content) in enumerate(frames, start=1):
        summary = _visual_frame_asset_summary(asset)
        parts.append({
            "type": "text",
            "text": f"关键帧 {index} 元数据：{json.dumps(summary, ensure_ascii=False, default=str)}",
        })
        parts.append({
            "type": "image_url",
            "image_url": {"url": _image_data_url(asset.mime_type, content)},
        })
    return parts


def _visual_video_content_parts(asset: ProjectRunAsset, content: bytes) -> list[dict[str, Any]]:
    summary = {
        **_visual_video_asset_summary(asset),
        "delivery": "signed_download_url",
        "signed_url_ttl_seconds": PROJECT_RUN_ASSET_SIGNED_URL_TTL_SECONDS,
    }
    video_url = _project_run_asset_signed_download_url(asset)
    return [
        {
            "type": "text",
            "text": (
                "下面是一条短视频原始素材。请按消费者从开头看到结尾的顺序做视觉诊断，"
                "重点判断首屏钩子、商品露出时机和清晰度、卖点/证据展开、节奏密度、结尾行动引导。"
                "不要使用未提供的投放数据，不要编造画面外信息。"
                f"素材元数据：{json.dumps(summary, ensure_ascii=False, default=str)}"
            ),
        },
        {
            "type": "video_url",
            "video_url": {
                "url": video_url,
                "detail": "low",
                "fps": PROJECT_VISUAL_VIDEO_FPS,
                "max_frames": PROJECT_VISUAL_VIDEO_MAX_FRAMES,
            },
        },
    ]


def _visual_model_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _redact_trace_value(value, max_depth=10)
    text = _text(value, limit=8000)
    if text:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            first_nl = cleaned.find("\n")
            cleaned = cleaned[first_nl + 1:] if first_nl >= 0 else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return _redact_trace_value(parsed, max_depth=10)
            if isinstance(parsed, list):
                return {"items": _redact_trace_value(parsed, max_depth=10), "format": "json_array"}
        except json.JSONDecodeError:
            pass
        return {"summary": text, "format": "text"}
    raise RuntimeError("vision_model_empty_response")


def _visual_role_label(role: str) -> str:
    normalized = str(role or "").strip()
    labels = {
        "material_a": "素材 A",
        "material_b": "素材 B",
        "good": "素材 A",
        "bad": "素材 B",
    }
    return labels.get(normalized, normalized or "未标注素材")


async def _call_project_visual_model_for_frames(
    user: User,
    run: ProjectRun,
    *,
    role: str,
    frames: list[tuple[ProjectRunAsset, bytes]],
) -> dict[str, Any]:
    role_label = _visual_role_label(role)
    system = (
        "你是 SkillForge 短视频素材视觉诊断 Agent。"
        "你必须基于用户上传素材的多张关键帧做保守、可验证的中文分析。"
        "优先返回 JSON，不要返回 Markdown。字段建议包含 summary、frame_sequence、diagnosis、evidence、risks、confidence。"
        "diagnosis 必须包含 hook、product_exposure、pace_density、proof、action_guidance、consumer_takeaway。"
        "evidence 写具体画面证据，说明消费者能看懂什么、看不懂什么，以及哪里影响点击或转化。"
    )
    retry_system = (
        "你是短视频关键帧视觉描述 Agent。只看画面，不看投放数据。"
        f"请用中文直接描述{role_label}从开头到结尾消费者能看到什么，并按"
        "首屏钩子、商品露出、节奏密度、卖点证据、行动引导给出保守判断。"
        "不要输出思考过程，不要编造不可见信息。"
    )
    last_error: Exception | None = None
    for attempt, prompt_system in enumerate((system, retry_system), start=1):
        result = await call_llm_multimodal(
            prompt_system,
            _visual_frame_content_parts(frames, role=role_label),
            json_mode=False,
            max_tokens=1800 if attempt == 1 else 1200,
            temperature=0.1,
            timeout=90,
            call_source="project_visual_analysis",
            cost_context={
                "user_id": getattr(user, "id", None),
                "department": getattr(user, "department", None),
                "conversation_id": run.execution_run_id or run.id,
                "project_run_id": run.id,
                "material_role": role,
                "attempt": attempt,
            },
            model_profile="vision",
            require_system_config=True,
        )
        try:
            parsed = _visual_model_result(result)
            if attempt > 1:
                parsed["recovered_after_empty_retry"] = True
            return parsed
        except RuntimeError as exc:
            last_error = exc
            if "vision_model_empty_response" not in str(exc) or attempt == 2:
                raise
    raise last_error or RuntimeError("vision_model_empty_response")


async def _call_project_visual_model_for_video(
    user: User,
    run: ProjectRun,
    *,
    asset: ProjectRunAsset,
    content: bytes,
) -> dict[str, Any]:
    role = _normalise_material_role(_asset_metadata_role(asset) or "unknown")
    role_label = _visual_role_label(role)
    system = (
        "你是 SkillForge 短视频素材视觉诊断 Agent。"
        "你只负责分析视频画面和画面文字，不负责根据投放数据下最终业务结论。"
        "请从普通消费者视角判断：看到什么、是否能立即理解商品/利益点、为什么继续看或划走、是否有购买/点击下一步。"
        "优先返回 JSON，不要返回 Markdown。字段建议包含 summary、timeline、diagnosis、evidence、risks、confidence。"
        "diagnosis 必须包含 hook、product_exposure、pace_density、proof、action_guidance、consumer_takeaway。"
        f"当前素材为 {role_label}。不要编造未出现在视频里的价格、销量、功效或优惠。"
    )
    retry_system = (
        "你是短视频视觉描述 Agent。只看这条视频，不看投放数据。"
        f"请用中文描述{role_label}从开头到结尾消费者能看到的关键信息，"
        "并判断首屏钩子、商品露出、节奏密度、卖点证据、行动引导。"
        "不要输出思考过程，不要编造画面外事实。"
    )
    last_error: Exception | None = None
    for attempt, prompt_system in enumerate((system, retry_system), start=1):
        result = await call_llm_multimodal(
            prompt_system,
            _visual_video_content_parts(asset, content),
            json_mode=False,
            max_tokens=1800 if attempt == 1 else 1200,
            temperature=0.1,
            timeout=120,
            call_source="project_visual_video_analysis",
            cost_context={
                "user_id": getattr(user, "id", None),
                "department": getattr(user, "department", None),
                "conversation_id": run.execution_run_id or run.id,
                "project_run_id": run.id,
                "material_role": role,
                "asset_id": asset.id,
                "attempt": attempt,
            },
            model_profile="vision",
            require_system_config=True,
        )
        try:
            parsed = _visual_model_result(result)
            if attempt > 1:
                parsed["recovered_after_empty_retry"] = True
            return parsed
        except RuntimeError as exc:
            last_error = exc
            if "vision_model_empty_response" not in str(exc) or attempt == 2:
                raise
    raise last_error or RuntimeError("vision_model_empty_response")


async def _analyze_project_visual_assets(db: AsyncSession, user: User, run: ProjectRun) -> dict[str, Any]:
    videos = await _project_visual_video_assets(db, run)
    video_results: dict[str, Any] = {}
    failed_videos: dict[str, Any] = {}
    if videos:
        async def analyze_video(asset: ProjectRunAsset) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
            role = _normalise_material_role(_asset_metadata_role(asset) or "unknown")
            try:
                content = _project_run_asset_abs_path(asset).read_bytes()
                result = await _call_project_visual_model_for_video(user, run, asset=asset, content=content)
                return role, {
                    "label": _visual_role_label(role),
                    "asset": _visual_video_asset_summary(asset),
                    "result": result,
                }, None
            except Exception as exc:  # noqa: BLE001
                return role, None, {
                    "label": _visual_role_label(role),
                    "asset": _visual_video_asset_summary(asset),
                    "error": redact_secret_text(exc, limit=400) or type(exc).__name__,
                }

        video_role_results = await asyncio.gather(*(analyze_video(asset) for asset in videos))
        for role, result, failure in video_role_results:
            if result is not None:
                video_results[role] = result
            if failure is not None:
                failed_videos[role] = failure

    frames = await _project_visual_frame_assets(db, run)
    if video_results and not frames:
        return {
            "status": "ok" if not failed_videos else "partial",
            "mode": "direct_video",
            "model_profile": "vision",
            "videos": [_visual_video_asset_summary(asset) for asset in videos],
            "materials": video_results,
            "failed_materials": failed_videos,
        }
    if not frames:
        return {
            "status": "vision_failed" if failed_videos else "no_visual_assets",
            "message": "本次运行没有可供视觉模型分析的原视频或关键帧资产。" if not failed_videos else "原视频视觉分析失败，且没有可用关键帧兜底。",
            "videos": [_visual_video_asset_summary(asset) for asset in videos],
            "failed_materials": failed_videos,
            "frames": [],
        }

    selected: list[tuple[ProjectRunAsset, bytes]] = []
    skipped: list[dict[str, Any]] = []
    total_bytes = 0
    for asset in frames:
        try:
            path = _project_run_asset_abs_path(asset)
            content = path.read_bytes()
        except Exception as exc:  # noqa: BLE001
            skipped.append({**_visual_frame_asset_summary(asset), "reason": redact_secret_text(exc, limit=200)})
            continue
        if len(content) > PROJECT_VISUAL_FRAME_MAX_BYTES:
            skipped.append({**_visual_frame_asset_summary(asset), "reason": "frame_too_large"})
            continue
        if total_bytes + len(content) > PROJECT_VISUAL_FRAME_MAX_TOTAL_BYTES:
            skipped.append({**_visual_frame_asset_summary(asset), "reason": "visual_frame_total_limit"})
            continue
        total_bytes += len(content)
        selected.append((asset, content))

    if not selected:
        if video_results:
            return {
                "status": "ok" if not failed_videos else "partial",
                "mode": "direct_video",
                "model_profile": "vision",
                "videos": [_visual_video_asset_summary(asset) for asset in videos],
                "materials": video_results,
                "failed_materials": failed_videos,
                "frames": [_visual_frame_asset_summary(asset) for asset in frames],
                "skipped": skipped,
            }
        return {
            "status": "no_usable_visual_frames",
            "message": "本次运行的关键帧资产存在，但超出大小限制或无法读取。",
            "videos": [_visual_video_asset_summary(asset) for asset in videos],
            "failed_videos": failed_videos,
            "frames": [_visual_frame_asset_summary(asset) for asset in frames],
            "skipped": skipped,
        }

    grouped: dict[str, list[tuple[ProjectRunAsset, bytes]]] = {}
    for asset, content in selected:
        role = _normalise_material_role(_asset_metadata_role(asset) or "unknown")
        if not video_results or role not in video_results or role in failed_videos:
            grouped.setdefault(role, []).append((asset, content))

    if video_results and not grouped:
        return {
            "status": "ok" if not failed_videos else "partial",
            "mode": "direct_video",
            "model_profile": "vision",
            "videos": [_visual_video_asset_summary(asset) for asset in videos],
            "materials": video_results,
            "failed_materials": failed_videos,
            "frames": [_visual_frame_asset_summary(asset) for asset, _content in selected],
            "skipped": skipped,
        }

    async def analyze_role(role: str, role_frames: list[tuple[ProjectRunAsset, bytes]]) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        try:
            return role, {
                "label": _visual_role_label(role),
                "frames": [_visual_frame_asset_summary(asset) for asset, _content in role_frames],
                "result": await _call_project_visual_model_for_frames(user, run, role=role, frames=role_frames),
            }, None
        except Exception as exc:  # noqa: BLE001
            return role, None, {
                "label": _visual_role_label(role),
                "error": redact_secret_text(exc, limit=400) or type(exc).__name__,
                "frames": [_visual_frame_asset_summary(asset) for asset, _content in role_frames],
            }

    role_results = await asyncio.gather(
        *(analyze_role(role, role_frames) for role, role_frames in sorted(grouped.items()))
    )
    material_results: dict[str, Any] = {}
    failed_materials: dict[str, Any] = {}
    for role, result, failure in role_results:
        if result is not None:
            material_results[role] = result
        if failure is not None:
            failed_materials[role] = failure

    merged_materials = {**video_results, **material_results}

    if not merged_materials:
        return {
            "status": "vision_failed",
            "mode": "per_material_multiframe",
            "model_profile": "vision",
            "videos": [_visual_video_asset_summary(asset) for asset in videos],
            "failed_videos": failed_videos,
            "frames": [_visual_frame_asset_summary(asset) for asset, _content in selected],
            "skipped": skipped,
            "materials": failed_materials,
        }

    mode = "per_material_multiframe"
    if video_results and material_results:
        mode = "mixed_video_frame"
    elif video_results:
        mode = "direct_video"

    return {
        "status": "ok" if not failed_videos and not failed_materials else "partial",
        "mode": mode,
        "model_profile": "vision",
        "videos": [_visual_video_asset_summary(asset) for asset in videos],
        "failed_videos": failed_videos,
        "frames": [_visual_frame_asset_summary(asset) for asset, _content in selected],
        "skipped": skipped,
        "materials": merged_materials,
        "failed_materials": failed_materials,
    }


async def _record_project_run_asset_event(
    db: AsyncSession,
    run: ProjectRun,
    project: Project,
    asset: ProjectRunAsset,
) -> ProjectIngressEvent:
    now = now_bjt()
    asset_row = serialize_run_asset(asset)
    event = ProjectIngressEvent(
        project_run_id=run.id,
        request_id=f"asset:{asset.id}",
        event_type="asset",
        input_snapshot={"asset": asset_row},
        output_result={"ok": True, "status": "uploaded", "asset_id": asset.id, "file_name": asset.file_name},
        reports=[],
        todos=[],
        proofs=[],
        metadata_json={"source": "project_run_asset_upload", "project_id": project.id, "asset_id": asset.id},
        created_at=now,
    )
    db.add(event)
    run.updated_at = now
    run.last_heartbeat_at = now
    if str(run.status or "") in {"opening", "stale"}:
        run.status = "running"
        run.error = None
    project.updated_at = now
    await db.flush()
    try:
        await capture_project_ingress_event(db, event)
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目运行资产写入学习事件失败 event={}: {}", event.id, exc)
    return event


async def upload_project_run_asset(
    db: AsyncSession,
    user: User,
    project_run_id: str,
    *,
    file_name: str,
    mime_type: str | None,
    content: bytes,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run, project = await _require_run(db, user, project_run_id, require_write=True)
    safe_name = _safe_asset_file_name(file_name)
    _validate_run_asset_upload(safe_name, mime_type, content)
    sha256 = hashlib.sha256(content).hexdigest()
    existing = (
        await db.execute(
            select(ProjectRunAsset).where(ProjectRunAsset.project_run_id == run.id, ProjectRunAsset.sha256 == sha256).limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        return {"ok": True, "deduped": True, "asset": serialize_run_asset(existing, include_download_url=True)}

    asset_id = _new_id("pra")
    suffix = Path(safe_name).suffix.lower()[:16]
    rel = PurePosixPath(project.id) / run.id / f"{asset_id}{suffix}"
    root = _project_run_asset_root()
    path = (root / rel.as_posix()).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    stored_metadata = _safe_dict(metadata)
    media_probe = await asyncio.to_thread(_probe_project_media_file, path, mime_type)
    if media_probe:
        stored_metadata = {**stored_metadata, "media_probe": media_probe}
    now = now_bjt()
    asset = ProjectRunAsset(
        id=asset_id,
        project_id=project.id,
        project_run_id=run.id,
        owner_user_id=str(getattr(user, "id", "") or "") or None,
        department_id=run.department_id or project.department_id,
        source_kind=_asset_source_kind(safe_name, mime_type),
        file_name=safe_name,
        mime_type=mime_type or "application/octet-stream",
        byte_size=len(content),
        sha256=sha256,
        storage_backend="local",
        storage_path=rel.as_posix(),
        metadata_json=stored_metadata,
        created_at=now,
    )
    db.add(asset)
    run.updated_at = now
    run.last_heartbeat_at = now
    if str(run.status or "") in {"opening", "stale"}:
        run.status = "running"
    await db.flush()
    await _record_project_run_asset_event(db, run, project, asset)
    return {"ok": True, "asset": serialize_run_asset(asset, include_download_url=True)}


async def upload_project_run_asset_stream(
    db: AsyncSession,
    user: User,
    project_run_id: str,
    *,
    file_name: str,
    mime_type: str | None,
    stream: Any,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a multipart upload with bounded memory and immutable dedupe."""

    run, project = await _require_run(db, user, project_run_id, require_write=True)
    safe_name = _safe_asset_file_name(file_name)
    root = _project_run_asset_root()
    temp_path = (root / ".uploads" / f"{_new_id('prau')}.part").resolve()
    if root not in temp_path.parents:
        raise AppError("PROJECT_ASSET_INVALID", 400)
    try:
        size, sha256, chunk_count = await asyncio.to_thread(
            _stream_project_run_asset_to_temp,
            stream,
            temp_path,
            safe_name,
        )
        existing = (
            await db.execute(
                select(ProjectRunAsset).where(
                    ProjectRunAsset.project_run_id == run.id,
                    ProjectRunAsset.sha256 == sha256,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            return {"ok": True, "deduped": True, "asset": serialize_run_asset(existing, include_download_url=True)}

        asset_id = _new_id("pra")
        suffix = Path(safe_name).suffix.lower()[:16]
        rel = PurePosixPath(project.id) / run.id / f"{asset_id}{suffix}"
        path = (root / rel.as_posix()).resolve()
        if root not in path.parents:
            raise AppError("PROJECT_ASSET_INVALID", 400)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(temp_path.replace, path)

        stored_metadata = {
            **_safe_dict(metadata),
            "upload_transport": "streamed_multipart",
            "upload_chunk_bytes": PROJECT_RUN_ASSET_STREAM_CHUNK_BYTES,
            "upload_chunk_count": chunk_count,
        }
        media_probe = await asyncio.to_thread(_probe_project_media_file, path, mime_type)
        if media_probe:
            stored_metadata = {**stored_metadata, "media_probe": media_probe}
        now = now_bjt()
        asset = ProjectRunAsset(
            id=asset_id,
            project_id=project.id,
            project_run_id=run.id,
            owner_user_id=str(getattr(user, "id", "") or "") or None,
            department_id=run.department_id or project.department_id,
            source_kind=_asset_source_kind(safe_name, mime_type),
            file_name=safe_name,
            mime_type=mime_type or "application/octet-stream",
            byte_size=size,
            sha256=sha256,
            storage_backend="local",
            storage_path=rel.as_posix(),
            metadata_json=stored_metadata,
            created_at=now,
        )
        db.add(asset)
        run.updated_at = now
        run.last_heartbeat_at = now
        if str(run.status or "") in {"opening", "stale"}:
            run.status = "running"
        await db.flush()
        await _record_project_run_asset_event(db, run, project, asset)
        return {"ok": True, "asset": serialize_run_asset(asset, include_download_url=True)}
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                logger.warning("项目运行资产临时文件清理失败 path={}", temp_path)


async def list_project_run_assets(db: AsyncSession, user: User, project_run_id: str) -> dict[str, Any]:
    run, project = await _require_run(db, user, project_run_id)
    assets = (
        await db.execute(
            select(ProjectRunAsset)
            .where(ProjectRunAsset.project_run_id == run.id)
            .order_by(ProjectRunAsset.created_at.desc())
        )
    ).scalars().all()
    return {
        "project": {"id": project.id, "name": project.name},
        "run": serialize_run(run),
        "items": [serialize_run_asset(asset, include_download_url=True) for asset in assets],
        "total": len(assets),
    }


async def project_run_asset_path(db: AsyncSession, user: User, project_run_id: str, asset_id: str) -> tuple[Path, ProjectRunAsset]:
    run, _project = await _require_run(db, user, project_run_id)
    asset = await db.get(ProjectRunAsset, asset_id)
    if not asset or asset.project_run_id != run.id:
        raise AppError("PROJECT_ASSET_NOT_FOUND", 404)
    return _project_run_asset_abs_path(asset), asset


async def _latest_capability_call_for_run(db: AsyncSession, project_run_id: str) -> ProjectCapabilityCall | None:
    return (
        await db.execute(
            select(ProjectCapabilityCall)
            .where(ProjectCapabilityCall.project_run_id == project_run_id)
            .order_by(ProjectCapabilityCall.created_at.desc(), ProjectCapabilityCall.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_project_run(db: AsyncSession, user: User, project_run_id: str) -> dict[str, Any]:
    run, _ = await _require_run(db, user, project_run_id)
    await _mark_project_run_stale_if_needed(db, run)
    row = serialize_run(run)
    row["latest_capability_call"] = serialize_capability_call_summary(await _latest_capability_call_for_run(db, run.id))
    return row


def serialize_ingress_event(event: ProjectIngressEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "project_run_id": event.project_run_id,
        "request_id": event.request_id,
        "event_type": event.event_type,
        "input_snapshot": _redact_trace_value(event.input_snapshot or {}),
        "input_keys": sorted(str(key) for key in _safe_dict(event.input_snapshot).keys())[:50],
        "output_result": _redact_trace_value(event.output_result or {}),
        "report_count": len(_safe_list(event.reports)),
        "todo_count": len(_safe_list(event.todos)),
        "proof_count": len(_safe_list(event.proofs)),
        "metadata": _redact_trace_value(event.metadata_json or {}),
        "created_at": _iso(event.created_at),
    }


def _ingress_timeline_title(row: dict[str, Any]) -> str:
    event_type = str(row.get("event_type") or "output")
    metadata = _safe_dict(row.get("metadata"))
    if str(metadata.get("status") or "") == "failed":
        reason = metadata.get("payload_reason") or metadata.get("reason") or "failed"
        return f"{'输入' if event_type == 'input' else '输出'}失败 · {reason}"
    if event_type == "asset":
        asset = _safe_dict(row.get("input_snapshot")).get("asset")
        asset_name = _text(_safe_dict(asset).get("file_name"), limit=80) if isinstance(asset, dict) else ""
        return f"运行资产上传{f' · {asset_name}' if asset_name else ''}"
    if event_type == "input":
        input_keys = _safe_list(row.get("input_keys"))
        suffix = f" · keys {', '.join(str(key) for key in input_keys[:6])}" if input_keys else ""
        return f"输入记录{suffix}"
    return f"输出回传 · reports {row.get('report_count', 0)} / todos {row.get('todo_count', 0)}"


def serialize_capability_call(call: ProjectCapabilityCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "project_run_id": call.project_run_id,
        "request_id": call.request_id,
        "capability_key": call.capability_key,
        "capability_type": call.capability_type,
        "status": call.status,
        "input_payload": _redact_trace_value(call.input_payload or {}),
        "input_summary": _redact_trace_value(call.input_summary or {}),
        "output_result": _redact_trace_value(call.output_result or {}),
        "output_summary": _redact_trace_value(call.output_summary or {}),
        "cost": _redact_trace_value(call.cost_json or {}),
        "latency_ms": call.latency_ms,
        "error": call.error,
        "created_at": _iso(call.created_at),
        "completed_at": _iso(call.completed_at),
    }


def serialize_capability_call_summary(call: ProjectCapabilityCall | None) -> dict[str, Any] | None:
    if not call:
        return None
    return {
        "id": call.id,
        "request_id": call.request_id,
        "capability_key": call.capability_key,
        "capability_type": call.capability_type,
        "status": call.status,
        "output_summary": _redact_trace_value(call.output_summary or {}),
        "latency_ms": call.latency_ms,
        "error": _text(call.error, limit=1000),
        "created_at": _iso(call.created_at),
        "completed_at": _iso(call.completed_at),
    }


def _serialize_sdk_check_capability_call(call: ProjectCapabilityCall) -> dict[str, Any]:
    output = _safe_dict(call.output_result)
    summary = _safe_dict(call.output_summary)
    return {
        "id": call.id,
        "request_id": call.request_id,
        "capability_key": call.capability_key,
        "capability_type": call.capability_type,
        "status": call.status,
        "latency_ms": call.latency_ms,
        "model": output.get("model") or summary.get("model"),
        "target_gateway_id": output.get("target_gateway_id") or summary.get("target_gateway_id"),
        "data_sink": output.get("data_sink"),
        "usage": _safe_dict(output.get("usage") or summary.get("usage")),
        "error": _text(call.error, limit=1000),
        "created_at": _iso(call.created_at),
        "completed_at": _iso(call.completed_at),
    }


async def get_project_sdk_check_run_summary(db: AsyncSession, user: User, project_run_id: str) -> dict[str, Any]:
    run, project = await _require_run(db, user, project_run_id)
    calls = (
        await db.execute(
            select(ProjectCapabilityCall)
            .where(ProjectCapabilityCall.project_run_id == run.id)
            .order_by(ProjectCapabilityCall.created_at.desc(), ProjectCapabilityCall.id.desc())
            .limit(20)
        )
    ).scalars().all()
    source_ids = {run.id, *{str(call.id) for call in calls}}
    candidate_jobs = (
        await db.execute(
            select(LearningIngestionJob)
            .where(
                LearningIngestionJob.skill_id == _project_skill_id(project.id),
                LearningIngestionJob.sink_id == PROJECT_TRAINING_SINK_DEFAULT_GATEWAY_ID,
            )
            .order_by(LearningIngestionJob.created_at.desc(), LearningIngestionJob.id.desc())
            .limit(100)
        )
    ).scalars().all()
    sink_jobs = []
    for job in candidate_jobs:
        policy = _safe_dict(job.policy_json)
        if policy.get("project_run_id") != run.id and str(policy.get("source_id") or "") not in source_ids:
            continue
        sink_jobs.append(_serialize_project_training_sink_job(job))

    serialized_calls = [_serialize_sdk_check_capability_call(call) for call in calls]
    return {
        "ok": True,
        "project": {"id": project.id, "name": project.name},
        "run": {
            "id": run.id,
            "request_id": run.request_id,
            "status": run.status,
            "execution_run_id": run.execution_run_id,
            "capability_call_count": int(getattr(run, "capability_call_count", 0) or 0),
            "output_model": _safe_dict(run.output_result).get("model"),
            "output_data_sink": _safe_dict(run.output_result).get("data_sink"),
            "error": _text(run.error, limit=1000),
            "created_at": _iso(run.created_at),
            "completed_at": _iso(run.completed_at),
            "updated_at": _iso(run.updated_at),
        },
        "capability_calls": serialized_calls,
        "training_sink_jobs": sink_jobs,
        "counts": {
            "capability_calls": len(serialized_calls),
            "completed_capability_calls": sum(1 for call in serialized_calls if call.get("status") == "completed"),
            "gb10_237_jobs": len(sink_jobs),
        },
    }


def _trace_cursor_token(row: Any) -> str | None:
    created_at = _iso(getattr(row, "created_at", None))
    row_id = getattr(row, "id", None)
    if not created_at or row_id is None:
        return None
    payload = json.dumps(
        {"created_at": created_at, "id": int(row_id)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _parse_trace_cursor(cursor: str | None, *, field: str) -> tuple[datetime, int] | None:
    text = str(cursor or "").strip()
    if not text:
        return None
    try:
        padded = text + ("=" * (-len(text) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        created_at = parse_bjt_datetime(str(payload.get("created_at") or ""))
        row_id = int(payload.get("id"))
        return created_at, row_id
    except Exception as exc:
        raise AppError("PROJECT_INVALID", 400, {"field": field, "reason": "trace cursor invalid"}) from exc


def _trace_cursor_condition(model: Any, cursor: str | None, *, field: str):
    parsed = _parse_trace_cursor(cursor, field=field)
    if not parsed:
        return None
    created_at, row_id = parsed
    return or_(model.created_at < created_at, and_(model.created_at == created_at, model.id < row_id))


async def get_project_run_trace(
    db: AsyncSession,
    user: User,
    project_run_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    ingress_cursor: str | None = None,
    capability_cursor: str | None = None,
) -> dict[str, Any]:
    run, project = await _require_run(db, user, project_run_id)
    await _mark_project_run_stale_if_needed(db, run)
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    ingress_filter = ProjectIngressEvent.project_run_id == run.id
    capability_filter = ProjectCapabilityCall.project_run_id == run.id
    ingress_total = int((await db.execute(select(func.count(ProjectIngressEvent.id)).where(ingress_filter))).scalar() or 0)
    input_total = int((await db.execute(select(func.count(ProjectIngressEvent.id)).where(ingress_filter, ProjectIngressEvent.event_type == "input"))).scalar() or 0)
    output_total = int((await db.execute(select(func.count(ProjectIngressEvent.id)).where(ingress_filter, ProjectIngressEvent.event_type == "output"))).scalar() or 0)
    asset_total = int((await db.execute(select(func.count(ProjectIngressEvent.id)).where(ingress_filter, ProjectIngressEvent.event_type == "asset"))).scalar() or 0)
    capability_total = int((await db.execute(select(func.count(ProjectCapabilityCall.id)).where(capability_filter))).scalar() or 0)
    ingress_query = select(ProjectIngressEvent).where(ingress_filter)
    ingress_cursor_condition = _trace_cursor_condition(ProjectIngressEvent, ingress_cursor, field="ingress_cursor")
    if ingress_cursor_condition is not None:
        ingress_query = ingress_query.where(ingress_cursor_condition)
    else:
        ingress_query = ingress_query.offset(offset)
    ingress_page = (
        await db.execute(
            ingress_query
            .order_by(ProjectIngressEvent.created_at.desc(), ProjectIngressEvent.id.desc())
            .limit(limit + 1)
        )
    ).scalars().all()
    ingress_has_more = len(ingress_page) > limit
    ingress_events = ingress_page[:limit]

    capability_query = select(ProjectCapabilityCall).where(capability_filter)
    capability_cursor_condition = _trace_cursor_condition(ProjectCapabilityCall, capability_cursor, field="capability_cursor")
    if capability_cursor_condition is not None:
        capability_query = capability_query.where(capability_cursor_condition)
    else:
        capability_query = capability_query.offset(offset)
    capability_page = (
        await db.execute(
            capability_query
            .order_by(ProjectCapabilityCall.created_at.desc(), ProjectCapabilityCall.id.desc())
            .limit(limit + 1)
        )
    ).scalars().all()
    capability_has_more = len(capability_page) > limit
    capability_calls = capability_page[:limit]
    event_rows = [serialize_ingress_event(event) for event in ingress_events]
    call_rows = [serialize_capability_call(call) for call in capability_calls]
    next_ingress_cursor = _trace_cursor_token(ingress_events[-1]) if ingress_events else ingress_cursor
    next_capability_cursor = _trace_cursor_token(capability_calls[-1]) if capability_calls else capability_cursor
    timeline = [
        {
            "kind": "input" if row.get("event_type") == "input" else "ingress",
            "id": row["id"],
            "request_id": row.get("request_id"),
            "status": row.get("event_type"),
            "title": _ingress_timeline_title(row),
            "created_at": row.get("created_at"),
        }
        for row in event_rows
    ] + [
        {
            "kind": "capability",
            "id": row["id"],
            "request_id": row.get("request_id"),
            "status": row.get("status"),
            "title": f"{row.get('capability_key')} · {row.get('status')}",
            "created_at": row.get("created_at"),
        }
        for row in call_rows
    ]
    timeline.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "department_id": project.department_id,
            "department": project.department,
            "type": project.type,
        },
        "run": serialize_run(run),
        "ingress_events": event_rows,
        "capability_calls": call_rows,
        "timeline": timeline[:200],
        "counts": {
            "ingress_events": len(event_rows),
            "input_events": sum(1 for row in event_rows if row.get("event_type") == "input"),
            "output_events": sum(1 for row in event_rows if row.get("event_type") == "output"),
            "asset_events": sum(1 for row in event_rows if row.get("event_type") == "asset"),
            "capability_calls": len(call_rows),
            "ingress_events_total": ingress_total,
            "input_events_total": input_total,
            "output_events_total": output_total,
            "asset_events_total": asset_total,
            "capability_calls_total": capability_total,
            "reports": _report_count(run),
            "todos": _todo_count(run),
        },
        "pagination": {
            "limit": limit,
            "offset": offset,
            "ingress_events_total": ingress_total,
            "input_events_total": input_total,
            "output_events_total": output_total,
            "asset_events_total": asset_total,
            "capability_calls_total": capability_total,
            "has_more_ingress_events": ingress_has_more,
            "has_more_capability_calls": capability_has_more,
            "next_offset": offset + limit,
            "next_ingress_cursor": next_ingress_cursor,
            "next_capability_cursor": next_capability_cursor,
            "cursor_mode": bool(ingress_cursor or capability_cursor),
        },
    }


def _compose_output_result(data: dict[str, Any], run: ProjectRun, project: Project) -> dict[str, Any]:
    output = _safe_dict(data.get("output")) or {}
    reports = _safe_list(data.get("reports")) or _safe_list(output.get("reports"))
    todos = _safe_list(data.get("todos")) or _safe_list(output.get("todos"))
    proofs = _safe_list(data.get("proofs")) or _safe_list(output.get("proofs"))
    if reports:
        output["reports"] = reports
    if todos:
        output["todos"] = todos
    meta = _safe_dict(output.get("_skillforge_meta"))
    meta.update(
        {
            "source_type": "project",
            "project_id": project.id,
            "project_name": project.name,
            "project_type": project.type,
            "project_run_id": run.id,
            "project_version_id": run.version_id,
            "containerized": False,
        }
    )
    if proofs:
        meta["proofs"] = proofs
    output["_skillforge_meta"] = meta
    template_id = report_designs.selected_report_design_template_id(
        data,
        data.get("input"),
        run.input_snapshot,
        output,
        project.metadata_json,
    )
    if template_id:
        output = report_designs.decorate_reports_with_design(
            output,
            template_id=template_id,
            source="project_gateway",
        )
    return output


def _validate_project_ingest_payload(data: dict[str, Any]) -> None:
    limits = _project_gateway_limits()
    actual_bytes = _json_payload_size(data)
    if actual_bytes > limits["max_ingest_bytes"]:
        _raise_payload_too_large(
            "ingest_bytes",
            actual_bytes=actual_bytes,
            max_bytes=limits["max_ingest_bytes"],
        )
    output = _safe_dict(data.get("output"))
    list_specs = (
        ("reports", _safe_list(data.get("reports")) or _safe_list(output.get("reports")), limits["max_reports_per_ingest"]),
        ("todos", _safe_list(data.get("todos")) or _safe_list(output.get("todos")), limits["max_todos_per_ingest"]),
        ("proofs", _safe_list(data.get("proofs")) or _safe_list(output.get("proofs")), limits["max_proofs_per_ingest"]),
    )
    for field, values, max_count in list_specs:
        actual_count = len(values)
        if actual_count > max_count:
            _raise_payload_too_large(
                f"{field}_count",
                field=field,
                actual_count=actual_count,
                max_count=max_count,
            )


def _validate_project_input_payload(data: dict[str, Any]) -> None:
    limits = _project_gateway_limits()
    actual_bytes = _json_payload_size(data)
    if actual_bytes > limits["max_input_event_bytes"]:
        _raise_payload_too_large(
            "input_event_bytes",
            actual_bytes=actual_bytes,
            max_bytes=limits["max_input_event_bytes"],
        )


def _payload_limit_detail(exc: AppError) -> dict[str, Any]:
    return exc.detail if isinstance(exc.detail, dict) else {"detail": str(exc.detail or exc)}


def _failed_ingress_error_message(event_type: str, detail: dict[str, Any]) -> str:
    reason = _text(detail.get("reason"), limit=80) or "payload_too_large"
    actual = detail.get("actual_bytes", detail.get("actual_count"))
    maximum = detail.get("max_bytes", detail.get("max_count"))
    label = "输入" if event_type == "input" else "输出"
    if actual is not None and maximum is not None:
        return f"项目{label}超过平台限制：{actual}/{maximum}。"
    return f"项目{label}超过平台限制：{reason}。"


def _failed_ingress_detail(event: ProjectIngressEvent, *, deduped: bool = False) -> dict[str, Any]:
    metadata = _safe_dict(event.metadata_json)
    output = _safe_dict(event.output_result)
    detail = {
        "reason": metadata.get("payload_reason") or metadata.get("reason") or output.get("reason") or "payload_too_large",
        "event_id": event.id,
        "project_run_id": event.project_run_id,
        "event_type": event.event_type,
        "ingress_event_status": metadata.get("status") or output.get("status") or "failed",
    }
    for key in ("actual_bytes", "max_bytes", "field", "actual_count", "max_count"):
        if key in metadata:
            detail[key] = metadata.get(key)
    if output.get("error"):
        detail["detail"] = output.get("error")
    if deduped:
        detail["deduped"] = True
    return detail


def _is_failed_project_ingress_event(event: ProjectIngressEvent) -> bool:
    return str(_safe_dict(event.metadata_json).get("status") or _safe_dict(event.output_result).get("status") or "") == "failed"


async def _record_failed_project_ingress_event(
    db: AsyncSession,
    run: ProjectRun,
    project: Project,
    *,
    request_id: str | None,
    event_type: str,
    detail: dict[str, Any],
) -> ProjectIngressEvent:
    now = now_bjt()
    payload_reason = _text(detail.get("reason"), limit=80) or "payload_too_large"
    error = _failed_ingress_error_message(event_type, detail)
    metadata = {
        "status": "failed",
        "reason": "payload_too_large",
        "payload_reason": payload_reason,
        "source": "project_gateway_input" if event_type == "input" else "project_gateway_ingest",
        "project_id": project.id,
        "project_run_id": run.id,
    }
    for key in ("actual_bytes", "max_bytes", "field", "actual_count", "max_count"):
        if key in detail:
            metadata[key] = detail.get(key)
    event = ProjectIngressEvent(
        project_run_id=run.id,
        request_id=request_id,
        event_type=event_type,
        input_snapshot={},
        output_result={"ok": False, "status": "failed", "reason": payload_reason, "error": error},
        reports=[],
        todos=[],
        proofs=[],
        metadata_json=metadata,
        created_at=now,
    )
    db.add(event)
    run.last_heartbeat_at = now
    run.updated_at = now
    if str(run.status or "") == "stale":
        run.status = "running"
        run.error = None
    project.updated_at = now
    await db.flush()
    try:
        await capture_project_ingress_event(db, event)
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目失败输入输出写入学习事件失败 event={}: {}", event.id, exc)
    return event


async def _commit_failed_ingress_event(db: AsyncSession, event: ProjectIngressEvent) -> None:
    # AppError 会触发请求级 rollback；失败的输入/输出网关事件仍需保留，
    # 以便 Trace / Learning Loop 能审计异常网页、过大 payload 或重试风暴。
    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目失败输入输出持久化失败 event={}: {}", getattr(event, "id", None), exc)
        raise


def _project_input_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    direct = _safe_dict(data.get("input"))
    params = _safe_dict(data.get("params"))
    extra = {
        key: value
        for key, value in data.items()
        if key
        not in {
            "request_id",
            "requestId",
            "input",
            "params",
            "metadata",
            "replace",
        }
    }
    return {**params, **extra, **direct}


def _summarize_output(output: dict[str, Any], project: Project) -> str:
    summary = _text(output.get("summary"), limit=1000)
    if summary:
        return summary
    reports = _safe_list(output.get("reports"))
    if reports and isinstance(reports[0], dict):
        return _text(reports[0].get("summary"), limit=1000) or f"项目 {project.name} 已产生报告"
    if output.get("error"):
        return f"项目 {project.name} 输出失败：{_text(output.get('error'), limit=300)}"
    return f"项目 {project.name} 输出已回传 SkillForge。"



async def record_project_input_event(
    db: AsyncSession,
    user: User,
    project_run_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    run, project = await _require_run(db, user, project_run_id, require_write=True)
    request_id = _request_id(data)
    event_type = "input"
    await _acquire_project_gateway_request_lock(db, project_run_id=run.id, request_id=request_id, channel=event_type)
    if request_id:
        existing_event = (
            await db.execute(
                select(ProjectIngressEvent)
                .where(
                    ProjectIngressEvent.project_run_id == run.id,
                    ProjectIngressEvent.event_type == event_type,
                    ProjectIngressEvent.request_id == request_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing_event:
            if _is_failed_project_ingress_event(existing_event):
                raise AppError("PROJECT_PAYLOAD_TOO_LARGE", 413, _failed_ingress_detail(existing_event, deduped=True))
            result = serialize_run(run)
            result["deduped"] = True
            result["input_event_id"] = existing_event.id
            return result

    try:
        _validate_project_input_payload(data)
    except AppError as exc:
        if exc.code != "PROJECT_PAYLOAD_TOO_LARGE":
            raise
        event = await _record_failed_project_ingress_event(
            db,
            run,
            project,
            request_id=request_id,
            event_type=event_type,
            detail=_payload_limit_detail(exc),
        )
        await _commit_failed_ingress_event(db, event)
        raise AppError("PROJECT_PAYLOAD_TOO_LARGE", 413, _failed_ingress_detail(event)) from exc
    input_snapshot = _project_input_snapshot(data)
    now = now_bjt()
    previous_input = _safe_dict(run.input_snapshot)
    merged_input = input_snapshot if data.get("replace") else {**previous_input, **input_snapshot}

    metadata = _safe_dict(data.get("metadata"))
    event = ProjectIngressEvent(
        project_run_id=run.id,
        request_id=request_id,
        event_type=event_type,
        input_snapshot=input_snapshot,
        output_result={},
        reports=[],
        todos=[],
        proofs=[],
        metadata_json={
            **metadata,
            "replace": bool(data.get("replace")),
            "source": metadata.get("source") or "project_gateway_input",
        },
        created_at=now,
    )
    db.add(event)

    run.input_snapshot = merged_input
    run.last_heartbeat_at = now
    run.updated_at = now
    if str(run.status or "") == "stale":
        run.status = "running"
        run.error = None

    execution = await db.get(ExecutionRun, run.execution_run_id) if run.execution_run_id else None
    if execution:
        if str(execution.status or "") == "stale":
            execution.status = "running"
            execution.completed_at = None
        execution.metadata_json = {
            **_safe_dict(execution.metadata_json),
            "project_run_id": run.id,
            "project_id": project.id,
            "last_project_input_at": isoformat_bjt(now),
            "last_project_input_request_id": request_id,
            "last_project_input_keys": sorted(str(key) for key in input_snapshot.keys())[:50],
        }

    project.updated_at = now
    await db.flush()
    try:
        await capture_project_ingress_event(db, event)
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目输入记录写入学习事件失败 event={}: {}", event.id, exc)

    result = serialize_run(run)
    result["input_event_id"] = event.id
    return result


async def ingest_project_output(
    db: AsyncSession,
    user: User,
    project_run_id: str,
    data: dict[str, Any],
    *,
    auto_analyze: bool = True,
) -> dict[str, Any]:
    run, project = await _require_run(db, user, project_run_id, require_write=True)
    request_id = _request_id(data)
    event_type = _text(data.get("event_type"), limit=40) or "output"
    await _acquire_project_gateway_request_lock(db, project_run_id=run.id, request_id=request_id, channel=event_type)
    if request_id:
        existing_event = (
            await db.execute(
                select(ProjectIngressEvent)
                .where(
                    ProjectIngressEvent.project_run_id == run.id,
                    ProjectIngressEvent.event_type == event_type,
                    ProjectIngressEvent.request_id == request_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing_event:
            if _is_failed_project_ingress_event(existing_event):
                raise AppError("PROJECT_PAYLOAD_TOO_LARGE", 413, _failed_ingress_detail(existing_event, deduped=True))
            result = serialize_run(run)
            result["deduped"] = True
            if auto_analyze and str(existing_event.event_type or "") != "input":
                analysis = await analyze_project_run(
                    db,
                    user,
                    run.id,
                    prompt=_text(data.get("analysis_prompt"), limit=1000),
                    raise_on_error=False,
                    request_id=_auto_analyze_request_id(existing_event),
                )
                result = serialize_run(run)
                result["deduped"] = True
                result["analysis"] = analysis
            return result

    try:
        _validate_project_ingest_payload(data)
    except AppError as exc:
        if exc.code != "PROJECT_PAYLOAD_TOO_LARGE":
            raise
        event = await _record_failed_project_ingress_event(
            db,
            run,
            project,
            request_id=request_id,
            event_type=event_type,
            detail=_payload_limit_detail(exc),
        )
        await _commit_failed_ingress_event(db, event)
        raise AppError("PROJECT_PAYLOAD_TOO_LARGE", 413, _failed_ingress_detail(event)) from exc
    output = _compose_output_result(data, run, project)
    input_snapshot = _safe_dict(data.get("input")) or _safe_dict(run.input_snapshot)
    now = now_bjt()
    declared_status = str(data.get("status") or output.get("status") or "completed").lower()
    execution_status = "failed" if declared_status in {"failed", "failure", "error", "timeout"} or output.get("error") else "completed"

    event = ProjectIngressEvent(
        project_run_id=run.id,
        request_id=request_id,
        event_type=event_type,
        input_snapshot=input_snapshot,
        output_result=output,
        reports=_safe_list(output.get("reports")),
        todos=_safe_list(output.get("todos")),
        proofs=_safe_list(data.get("proofs")) or _safe_list(_safe_dict(data.get("output")).get("proofs")),
        metadata_json=_safe_dict(data.get("metadata")),
        created_at=now,
    )
    db.add(event)

    run.input_snapshot = input_snapshot
    run.output_result = output
    run.report_count = len(_safe_list(output.get("reports")))
    run.todo_count = len(_safe_list(output.get("todos")))
    result_evaluation = _evaluate_project_run_result(run, project, input_snapshot, output, auto_analyze=auto_analyze)
    run.ai_summary = _merge_run_ai_summary(run, {"result_evaluation": result_evaluation})
    run.status = "waiting_ai" if auto_analyze else execution_status
    run.completed_at = now
    run.updated_at = now
    run.error = _text(output.get("error"), limit=4000)

    execution = await db.get(ExecutionRun, run.execution_run_id) if run.execution_run_id else None
    if execution:
        execution.status = execution_status
        execution.completed_at = now
        execution.completed_steps = 1
        execution.summary = _summarize_output(output, project)
        execution.metadata_json = {
            **_safe_dict(execution.metadata_json),
            "project_run_id": run.id,
            "decision_log_id": run.decision_log_id,
            "last_ingress_event_type": event.event_type,
            "last_ingress_at": isoformat_bjt(now),
        }

    decision = await db.get(DecisionLog, run.decision_log_id) if run.decision_log_id else None
    if not decision:
        decision = DecisionLog(
            run_id=run.execution_run_id,
            skill_id=_project_skill_id(project.id),
            input_snapshot=input_snapshot,
            output_result=output,
            suggested_action=_safe_dict(output.get("suggested_action")) or None,
            approval_level=1,
            approval_status="auto",
            target_user=run.user_id,
            model_id="project-host",
            is_sandbox=False,
            created_at=now,
        )
        db.add(decision)
        await db.flush()
        run.decision_log_id = decision.id
    else:
        decision.input_snapshot = input_snapshot
        decision.output_result = output
        decision.suggested_action = _safe_dict(output.get("suggested_action")) or None
        decision.approval_status = decision.approval_status or "auto"

    try:
        from app.inbox.service import sync_report_cards_for_decision_log

        await sync_report_cards_for_decision_log(db, decision)
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目报告投影同步失败 run={} decision={}: {}", run.id, decision.id, exc)

    if execution:
        execution.metadata_json = {**_safe_dict(execution.metadata_json), "decision_log_id": decision.id}
        try:
            await capture_execution_run(db, execution, user_id=run.user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("项目运行写入学习事件失败 run={}: {}", run.id, exc)
    try:
        await capture_decision_log(db, decision, user_id=run.user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目决策写入学习事件失败 decision={}: {}", decision.id, exc)

    if _safe_list(output.get("todos")):
        try:
            await todo_service.create_from_execution(
                db,
                run_id=run.execution_run_id or run.id,
                skill_id=_project_skill_id(project.id),
                skill_meta={
                    "name": project.name,
                    "department": project.department,
                    "approval_level": 1,
                    "project_id": project.id,
                },
                decision={"input_snapshot": input_snapshot, "output_result": output, "suggested_action": output.get("suggested_action")},
            )
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("项目待办创建失败 run={}: {}", run.id, exc)

    project.updated_at = now
    await db.flush()
    try:
        await capture_project_ingress_event(db, event)
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目输出回传写入学习事件失败 event={}: {}", event.id, exc)

    result = serialize_run(run)
    if auto_analyze:
        analysis = await analyze_project_run(
            db,
            user,
            run.id,
            prompt=_text(data.get("analysis_prompt"), limit=1000),
            raise_on_error=False,
            request_id=_auto_analyze_request_id(event),
        )
        result = serialize_run(run)
        result["analysis"] = analysis
    return result


def _auto_analyze_request_id(event: ProjectIngressEvent) -> str:
    return f"ingress:{int(event.id)}:analyze"


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="ignore")).hexdigest()


def _project_ai_visible_prompt(context_pack: dict[str, Any]) -> str:
    prompt = _text(context_pack.get("prompt"), limit=8000) or "分析这个平台项目运行输出，提炼结论、风险、待办建议和可进入 AI 循环的学习点。"
    visible_context = {
        "project": _safe_dict(context_pack.get("project")),
        "run": _safe_dict(context_pack.get("run")),
        "capability_input": _redact_trace_value(_safe_dict(context_pack.get("capability_input"))),
        "visual_analysis": _redact_trace_value(_safe_dict(context_pack.get("visual_analysis"))),
        "output_contract": _safe_dict(context_pack.get("output_contract")),
    }
    context_text = json.dumps(visible_context, ensure_ascii=False, indent=2, default=str)
    return (
        f"{prompt}\n\n"
        "以下 JSON 是本次 Project Gateway 页面提交给 ai.analyze 的可见上下文。"
        "如果 capability_input 内有数据、视频、评分或分析深度，不得回答“未收到具体的视频样本元数据、好坏标签或评分矩阵”；"
        "只能指出哪些具体字段为空或仍需补采。"
        "如果 visual_analysis.status 为 ok 或 partial，必须把视觉证据和投放数据结合起来做 A/B 对比；"
        "必须覆盖首屏钩子、商品露出、节奏密度、卖点证据、行动引导五个维度，说明哪些来自画面、哪些来自投放数据。"
        "用户在 capability_input.videos[].note 或补充文本里写的内容只能当作背景/待验证假设，不能当作画面证据；"
        "你的结论必须分清：视觉模型实际看见了什么、投放数据证明了什么、用户补充只是提供了什么假设。"
        "如果某个素材视觉分析失败，必须说明具体原因，不能编造该素材画面细节。\n\n"
        f"{context_text}"
    )


def _request_id(data: dict[str, Any]) -> str | None:
    return _text(data.get("request_id") or data.get("requestId"), limit=80)


async def _acquire_project_gateway_request_lock(
    db: AsyncSession,
    *,
    project_run_id: str,
    request_id: str | None,
    channel: str,
) -> dict[str, Any] | None:
    if not request_id:
        return None
    key = f"project_gateway:{channel}:{project_run_id}:{request_id}"
    acquired = await acquire_xact_lock(db, key)
    return {"key": key, "acquired": acquired}


async def _cached_capability_call(db: AsyncSession, run_id: str, request_id: str | None) -> ProjectCapabilityCall | None:
    if not request_id:
        return None
    return (
        await db.execute(
            select(ProjectCapabilityCall)
            .where(ProjectCapabilityCall.project_run_id == run_id, ProjectCapabilityCall.request_id == request_id)
            .limit(1)
        )
    ).scalar_one_or_none()


def _capability_error_detail(
    call: ProjectCapabilityCall,
    *,
    capability: str,
    detail: Any,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(detail, dict):
        payload = {**detail}
        payload.setdefault("detail", call.error or f"能力调用状态为 {call.status}")
    else:
        payload = {"detail": _text(detail, limit=1000) or call.error or f"能力调用状态为 {call.status}"}
    payload.update({"capability": capability, "call_id": call.id, "capability_call_status": call.status})
    if extra:
        payload.update(extra)
    return payload


async def _capability_cache_response(db: AsyncSession, run: ProjectRun, call: ProjectCapabilityCall) -> dict[str, Any]:
    if call.status == "running":
        for _ in range(40):
            await asyncio.sleep(0.05)
            await db.refresh(call)
            if call.status != "running":
                break
    if call.status == "completed":
        return {
            "ok": True,
            "capability": call.capability_key,
            "project_run_id": run.id,
            "call_id": call.id,
            "deduped": True,
            "result": call.output_result or call.output_summary or {},
        }
    reason = _safe_dict(call.input_summary).get("reason")
    if reason == "capability_quota_exceeded":
        status_code = 429
        error_code = "PROJECT_CAPABILITY_DENIED"
    elif reason == "capability_payload_too_large":
        status_code = 413
        error_code = "PROJECT_PAYLOAD_TOO_LARGE"
    else:
        status_code = 400
        error_code = "PROJECT_CAPABILITY_DENIED"
    raise AppError(
        error_code,
        502 if call.status == "running" else status_code,
        _capability_error_detail(call, capability=call.capability_key, detail=call.error, extra={"deduped": True}),
    )


def _canonical_capability_key(value: Any) -> str:
    key = str(value or "").strip()
    raw_key = key[6:] if key.lower().startswith("mcp://") else key
    raw_lower = raw_key.lower()
    aliases = {
        "analyze": "ai.analyze",
        "chat": "ai.chat",
        "generate": "ai.generate",
        "cheap": "ai.cheap.generate",
        "cheap.generate": "ai.cheap.generate",
        "cheap.chat": "ai.cheap.chat",
        "complete": "ai.example.testplete",
        "platform.ai": "platform.ai.generate",
        "platform.cheap": "platform.ai.cheap.generate",
        "cloud_video.samplebrand_weekly.raw_collection": "mcp://skillforge_samplebrand_cloud_video_daily_analysis_input",
        "tmall.link_decline.raw_collection": "mcp://skillforge_tmall_link_decline_data_latest",
    }
    if raw_lower in aliases:
        return aliases[raw_lower]
    if key.startswith("mcp://"):
        return key
    if key in {
        "skillforge_samplebrand_cloud_video_daily_analysis_input",
        "skillforge_samplebrand_cloud_video_data_latest",
        "skillforge_samplebrand_cloud_video_data_get",
        "skillforge_tmall_link_decline_data_latest",
        "skillforge_tmall_link_decline_data_get",
    }:
        return f"mcp://{key}"
    return raw_lower


def _declared_project_capabilities(project: Project) -> set[str]:
    meta = _safe_dict(project.metadata_json)
    declared = _safe_list(meta.get("capabilities"))
    result: set[str] = set()
    for item in declared:
        if isinstance(item, str):
            result.add(_canonical_capability_key(item))
        elif isinstance(item, dict):
            result.add(_canonical_capability_key(item.get("key") or item.get("capability") or item.get("name")))
    return {item for item in result if item}


def _capability_allowed(project: Project, key: str) -> bool:
    declared = _declared_project_capabilities(project)
    if not declared:
        # Legacy/manual projects can still call platform AI through the gateway;
        # data/MCP capabilities must be explicitly declared by uploaded packages.
        return key in PROJECT_AI_CAPABILITIES or key == "ai.analyze"
    if key in declared:
        return True
    if key.startswith("platform.ai.") and key.replace("platform.", "", 1) in declared:
        return True
    if key.startswith("ai.") and f"platform.{key}" in declared:
        return True
    return False


def _capability_input(data: dict[str, Any]) -> dict[str, Any]:
    direct = _safe_dict(data.get("input"))
    if direct:
        return direct
    return {
        key: value
        for key, value in data.items()
        if key
        not in {
            "capability",
            "capability_key",
            "key",
            "type",
            "request_id",
            "requestId",
            "_sdk_token_id",
            "_sdk_token_prefix",
        }
    }


def _capability_gateway_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"input": _capability_input(data)}
    for key in (
        "prompt",
        "question",
        "query",
        "json_mode",
        "jsonMode",
        "temperature",
        "messages",
        "model",
        "model_id",
        "modelId",
        "target_gateway_id",
        "targetGatewayId",
        "gateway_id",
        "gatewayId",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "maxOutputTokens",
        "max_input_tokens",
        "maxInputTokens",
        "context_window",
        "contextWindow",
        "truncation",
        "timeout_seconds",
        "timeoutSeconds",
    ):
        if key in data:
            payload[key] = data.get(key)
    return payload


def _validate_project_capability_payload(run: ProjectRun, data: dict[str, Any]) -> None:
    limits = _project_gateway_limits()
    payload = _capability_gateway_payload(data)
    actual_bytes = _json_payload_size(payload)
    is_resident_236 = False
    try:
        is_resident_236 = _project_resident_model_request(data)[0]
    except AppError:
        is_resident_236 = False
    max_bytes = limits["max_236_capability_input_bytes"] if is_resident_236 else limits["max_capability_input_bytes"]
    if actual_bytes > max_bytes:
        _raise_payload_too_large(
            "236_capability_input_bytes" if is_resident_236 else "capability_input_bytes",
            actual_bytes=actual_bytes,
            max_bytes=max_bytes,
        )


def _ensure_project_capability_quota(run: ProjectRun) -> None:
    limits = _project_gateway_limits()
    current_calls = int(getattr(run, "capability_call_count", 0) or 0)
    if current_calls >= limits["max_capability_calls_per_run"]:
        raise AppError(
            "PROJECT_CAPABILITY_DENIED",
            429,
            {
                "detail": "单次项目运行能力调用已达到平台上限，请重新打开项目或减少网页内循环调用。",
                "actual_count": current_calls,
                "max_calls_per_run": limits["max_capability_calls_per_run"],
            },
        )


async def _record_failed_project_capability_call(
    db: AsyncSession,
    run: ProjectRun,
    project: Project,
    *,
    request_id: str | None,
    capability: str,
    capability_type: str,
    input_payload: dict[str, Any] | None = None,
    input_summary: dict[str, Any] | None = None,
    error: str,
) -> ProjectCapabilityCall:
    now = now_bjt()
    call = ProjectCapabilityCall(
        project_run_id=run.id,
        request_id=request_id,
        capability_key=capability or "unknown",
        capability_type=capability_type[:40] or "unknown",
        status="failed",
        input_payload=input_payload or {},
        input_summary=input_summary or {},
        output_result={"ok": False, "error": _text(error, limit=1000), "capability": capability or "unknown"},
        output_summary={"ok": False, "error": _text(error, limit=1000)},
        error=_text(error, limit=4000),
        created_at=now,
        completed_at=now,
    )
    db.add(call)
    run.capability_call_count = int(getattr(run, "capability_call_count", 0) or 0) + 1
    run.updated_at = now
    await db.flush()
    await _capture_project_capability_learning(db, call)
    return call


async def _capture_project_capability_learning(db: AsyncSession, call: ProjectCapabilityCall) -> None:
    try:
        await capture_project_capability_call(db, call)
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目能力调用写入学习事件失败 call={}: {}", call.id, exc)


async def _commit_failed_capability_call(db: AsyncSession, call: ProjectCapabilityCall) -> None:
    # AppError 会触发请求级 rollback；失败的网关调用本身也必须进入
    # project_capability_calls / Learning Loop，才能让平台内项目和审计看见失败状态。
    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("项目失败能力调用持久化失败 call={}: {}", getattr(call, "id", None), exc)
        raise


async def _call_declared_project_mcp_read_tool(db: AsyncSession, user: User, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    meta = PROJECT_MCP_READ_CAPABILITIES.get(key)
    if not meta:
        raise AppError("PROJECT_CAPABILITY_DENIED", 400, {"detail": "该 MCP/数据能力尚未接入项目网关", "capability": key})
    tool = str(meta["tool"])
    tool_payload = _project_mcp_read_tool_payload(tool, payload)
    if tool == "skillforge_org_search_users":
        data = await codex_service._builtin_org_search_users(db, user, tool_payload)
    elif tool == "skillforge_org_list_members":
        data = await codex_service._builtin_org_list_members(db, user, tool_payload)
    elif tool == "skillforge_agent_coverage":
        data = await codex_service._builtin_agent_coverage(db, user, tool_payload)
    elif tool == "skillforge_cloud_video_accounts":
        data = await codex_cloud_video.builtin_cloud_video_accounts(tool_payload)
    elif tool == "skillforge_cloud_video_ad_report":
        data = await codex_cloud_video.builtin_cloud_video_ad_report(tool_payload)
    elif tool == "skillforge_cloud_video_audit_rejects":
        data = await codex_cloud_video.builtin_cloud_video_audit_rejects(tool_payload)
    elif tool == "skillforge_cloud_video_material_report":
        data = await codex_cloud_video.builtin_cloud_video_material_report(tool_payload)
    elif tool == "skillforge_cloud_video_video_usage_report":
        data = await codex_cloud_video.builtin_cloud_video_video_usage_report(tool_payload)
    elif tool == "skillforge_cloud_video_daily_person_video_report":
        data = await codex_cloud_video.builtin_cloud_video_daily_person_video_report(tool_payload)
    elif tool == "skillforge_cloud_video_categories":
        data = await codex_cloud_video.builtin_cloud_video_categories(tool_payload)
    elif tool == "skillforge_cloud_video_tags":
        data = await codex_cloud_video.builtin_cloud_video_tags(tool_payload)
    elif tool == "skillforge_cloud_video_videos":
        data = await codex_cloud_video.builtin_cloud_video_videos(tool_payload)
    elif tool == "skillforge_samplebrand_cloud_video_daily_analysis_input":
        data = await codex_service._builtin_samplebrand_daily_analysis_input(db, user, tool_payload)
    elif tool == "skillforge_samplebrand_cloud_video_data_latest":
        data = await codex_service._builtin_data_capability_latest(
            db,
            user,
            tool_payload,
            capability_key="cloud_video.samplebrand_weekly.raw_collection",
        )
    elif tool == "skillforge_samplebrand_cloud_video_data_get":
        data = await codex_service._builtin_data_artifact_get(
            db,
            user,
            tool_payload,
            capability_key="cloud_video.samplebrand_weekly.raw_collection",
        )
    elif tool == "skillforge_tmall_link_decline_data_latest":
        data = await codex_service._builtin_data_capability_latest(
            db,
            user,
            tool_payload,
            capability_key="tmall.link_decline.raw_collection",
        )
    elif tool == "skillforge_tmall_link_decline_data_get":
        data = await codex_service._builtin_data_artifact_get(
            db,
            user,
            tool_payload,
            capability_key="tmall.link_decline.raw_collection",
        )
    elif tool == "tmall_link_health_latest_analysis":
        data = await _tmall_latest_raw_collection_analysis(db, user, tool_payload)
    else:
        raise AppError("PROJECT_CAPABILITY_DENIED", 400, {"detail": "该 MCP/数据能力尚未接入项目网关", "capability": key})
    return {
        "ok": True,
        "tool": tool,
        "data": data,
        "proof": {
            "gateway": "skillforge_project_gateway",
            "credential_location": "platform_only",
            "data_scope": meta.get("data_scope"),
            "dry_run": True,
        },
    }


def _project_mcp_read_tool_payload(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    if tool == "skillforge_tmall_link_decline_data_get":
        result = dict(payload)
        if result.get("include_content") is True and not result.get("max_bytes") and not result.get("maxBytes"):
            result["max_bytes"] = 5 * 1024 * 1024
        return result
    if tool not in {
        "skillforge_samplebrand_cloud_video_daily_analysis_input",
        "skillforge_samplebrand_cloud_video_data_latest",
        "skillforge_samplebrand_cloud_video_data_get",
    }:
        return payload
    kind = str(payload.get("kind") or "").strip()
    if kind in {"", "raw-input", "raw-output"}:
        return payload
    result = dict(payload)
    result.pop("kind", None)
    result.setdefault("request_kind", kind)
    return result


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _safe_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _project_ai_context_pack(project: Project, run: ProjectRun, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "type": project.type,
            "department_id": project.department_id,
            "department": project.department,
            "visibility": project.visibility,
            "version_id": run.version_id,
        },
        "run": {
            "id": run.id,
            "execution_run_id": run.execution_run_id,
            "status": run.status,
            "input_snapshot": run.input_snapshot or {},
            "output_result": run.output_result or {},
        },
        "capability": {
            "key": key,
            "gateway": "skillforge_project_gateway",
            "credential_location": "platform_only",
        },
        "input": payload,
    }


def _project_ai_gateway_model_profile(project: Project, *, capability_key: str = "") -> str | None:
    metadata = _safe_dict(project.metadata_json)
    service = _safe_dict(metadata.get("service_conversion"))
    gateway = _safe_dict(metadata.get("ai_gateway")) or _safe_dict(service.get("ai_gateway"))
    raw = (
        gateway.get("model_profile")
        or gateway.get("profile")
        or service.get("ai_model_profile")
        or metadata.get("ai_model_profile")
    )
    profile = str(raw or "").strip().lower().replace("_", "-")
    aliases = {
        "default": "",
        "global": "",
        "main": "",
        "ai.default": "",
        "deepseek-v4-pro": "",
        "cheap": "cheap",
        "ai.cheap": "cheap",
        "flash": "flash",
        "v4f": "flash",
        "deepseek-v4-flash": "flash",
    }
    selected = aliases.get(profile)
    if ".cheap." in capability_key:
        return "cheap"
    return selected


def _project_236_prompt_from_payload(data: dict[str, Any]) -> tuple[str | None, dict[str, Any], list[Any]]:
    payload = _capability_input(data)
    messages = _safe_list(data.get("messages")) or _safe_list(payload.get("messages"))
    tools = _safe_list(data.get("tools")) or _safe_list(payload.get("tools"))
    tool_choice = data.get("tool_choice") if "tool_choice" in data else payload.get("tool_choice")
    prompt = (
        _text(data.get("prompt"), limit=PROJECT_236_PROMPT_TEXT_LIMIT_CHARS)
        or _project_openai_messages_to_prompt(messages)
        or _text(payload.get("prompt"), limit=PROJECT_236_PROMPT_TEXT_LIMIT_CHARS)
        or _text(payload.get("question"), limit=PROJECT_236_PROMPT_TEXT_LIMIT_CHARS)
        or _text(payload.get("query"), limit=PROJECT_236_PROMPT_TEXT_LIMIT_CHARS)
    )
    prompt = _project_openai_append_tools_prompt(prompt or "", tools, tool_choice)
    if not prompt and payload:
        prompt = "请基于以下 Project SDK 输入完成模型调用：\n" + json.dumps(
            _redact_trace_value(payload),
            ensure_ascii=False,
            default=str,
        )
    return prompt, payload, messages


def _project_236_usage_reliable(result: dict[str, Any]) -> bool:
    metrics = _safe_dict(result.get("metrics"))
    return any(key in metrics for key in ("prompt_tokens", "completion_tokens", "generated_tokens", "total_tokens")) or any(
        key in result for key in ("prompt_tokens", "completion_tokens", "generated_tokens", "total_tokens")
    )


def _project_236_completion_usage(result: dict[str, Any]) -> dict[str, int]:
    metrics = _safe_dict(result.get("metrics"))
    prompt_tokens = _safe_int(metrics.get("prompt_tokens") or result.get("prompt_tokens"), default=0, minimum=0, maximum=10_000_000)
    completion_tokens = _safe_int(
        metrics.get("completion_tokens") or metrics.get("generated_tokens") or result.get("completion_tokens") or result.get("generated_tokens"),
        default=0,
        minimum=0,
        maximum=10_000_000,
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


async def _call_project_236_resident_model(
    db: AsyncSession,
    user: User | Any,
    run: ProjectRun,
    project: Project,
    *,
    capability: str,
    data: dict[str, Any],
    request_id: str | None,
    model_id: str,
) -> dict[str, Any]:
    instance, model_record = await _resolve_project_236_model_for_call(db, user, model_id)
    prompt, payload, messages = _project_236_prompt_from_payload(data)
    if not prompt:
        raise AppError("PARAM_INVALID", 400, {"detail": "236 模型调用需要 messages、prompt、question、query 或 input。"})
    max_tokens = _safe_int(
        data.get("max_tokens")
        or data.get("max_completion_tokens")
        or data.get("max_output_tokens")
        or data.get("maxOutputTokens")
        or payload.get("max_tokens")
        or payload.get("max_completion_tokens")
        or payload.get("max_output_tokens"),
        default=512,
        minimum=1,
        maximum=4096,
    )
    tools = _safe_list(data.get("tools") or payload.get("tools"))
    tool_choice = data.get("tool_choice") if "tool_choice" in data else payload.get("tool_choice")
    if tools and max_tokens < 256:
        max_tokens = 256
    timeout_seconds = _safe_int(
        data.get("timeout_seconds") or data.get("timeoutSeconds") or payload.get("timeout_seconds"),
        default=600,
        minimum=30,
        maximum=3600,
    )
    requested_max_input_tokens = (
        data.get("max_input_tokens")
        or data.get("maxInputTokens")
        or data.get("context_window")
        or data.get("contextWindow")
        or payload.get("max_input_tokens")
        or payload.get("maxInputTokens")
        or payload.get("context_window")
        or payload.get("contextWindow")
    )
    truncation = data.get("truncation") if "truncation" in data else payload.get("truncation")
    try:
        prompt, messages, context_budget = _project_236_apply_context_budget(
            model_record=model_record,
            prompt=prompt,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            requested_max_input_tokens=requested_max_input_tokens,
            truncation=truncation,
        )
    except AppError as exc:
        if exc.code != "PROJECT_OPENAI_CONTEXT_OVERFLOW":
            raise
        detail = exc.detail if isinstance(exc.detail, dict) else {"detail": str(exc.detail or exc)}
        call = await _record_failed_project_capability_call(
            db,
            run,
            project,
            request_id=request_id,
            capability=capability,
            capability_type="ai_resident",
            input_payload={
                "model": model_id,
                "target_gateway_id": instance.id,
                "prompt": prompt,
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "input": payload,
                "max_tokens": max_tokens,
                "max_input_tokens": requested_max_input_tokens,
                "truncation": truncation or "disabled",
            },
            input_summary={
                "reason": "context_overflow",
                "project_id": project.id,
                "project_run_id": run.id,
                "target_gateway_id": instance.id,
                "model": model_id,
                "input_hash": _hash_payload(payload),
                "prompt_hash": _hash_payload(prompt),
                **detail,
            },
            error=str(detail),
        )
        await enqueue_project_sdk_training_sink(
            db,
            user,
            project,
            run,
            source_type="project_capability_call",
            source_id=call.id,
            source_status="failed",
            request_id=request_id,
        )
        await _commit_failed_capability_call(db, call)
        raise AppError(exc.code, exc.status, _capability_error_detail(call, capability=capability, detail=detail)) from exc
    sdk_token_marker = _project_sdk_internal_token_marker(data)
    await _ensure_project_sdk_236_concurrency(
        db,
        project,
        model_id=model_id,
        sdk_token_id=sdk_token_marker.get("sdk_token_id"),
    )
    call_input_payload = {
        "model": model_id,
        "target_gateway_id": instance.id,
        "prompt": prompt,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "input": payload,
        "max_tokens": max_tokens,
        "max_input_tokens": context_budget["max_input_tokens"],
        "truncation": context_budget["truncation"],
        "context_budget": context_budget,
        "timeout_seconds": timeout_seconds,
    }
    now = now_bjt()
    started = perf_counter()
    call = ProjectCapabilityCall(
        project_run_id=run.id,
        request_id=request_id,
        capability_key=capability,
        capability_type="ai_resident",
        status="running",
        input_payload=call_input_payload,
        input_summary={
            "project_id": project.id,
            "project_run_id": run.id,
            "target_gateway_id": instance.id,
            "model": model_id,
            "model_loaded": bool(model_record.get("loaded")),
            "input_hash": _hash_payload(payload),
            "prompt_hash": _hash_payload(prompt),
            "context_budget": context_budget,
            **sdk_token_marker,
        },
        created_at=now,
    )
    db.add(call)
    run.capability_call_count = int(getattr(run, "capability_call_count", 0) or 0) + 1
    run.updated_at = now
    await db.flush()

    # Release project row locks before the external resident-model call.
    await db.commit()
    try:
        bridge_result = await AIClawClient(
            instance.id,
            gateway_kind=instance.bridge_gateway_kind,
        ).test_openwebui_chat(
            {
                "model_id": model_id,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "max_input_chars": len(prompt),
                "max_input_tokens": context_budget["max_input_tokens"],
                "context_window": context_budget["context_window"],
                "timeout_seconds": timeout_seconds,
            },
            timeout=timeout_seconds + 60,
        )
        if not isinstance(bridge_result, dict):
            bridge_result = {"text": str(bridge_result or ""), "status": "succeeded"}
        if bridge_result.get("ok") is not True and bridge_result.get("status") != "succeeded":
            raise AppError(
                "PROJECT_RESIDENT_MODEL_CHAT_FAILED",
                502,
                {
                    "detail": bridge_result.get("error") or "236 resident model call failed",
                    "gateway_id": instance.id,
                    "model": model_id,
                    "status": bridge_result.get("status"),
                },
            )
        completed = now_bjt()
        usage = _project_236_completion_usage(bridge_result)
        usage_reliable = _project_236_usage_reliable(bridge_result)
        output_text = str(bridge_result.get("text") or bridge_result.get("output") or "")
        result_payload = {
            "output": output_text,
            "model": model_id,
            "target_gateway_id": instance.id,
            "resident_model": bool(model_record.get("loaded")),
            "runtime_profile": model_record.get("runtime_profile"),
            "deployment_id": model_record.get("deployment_id"),
            "finish_reason": str(bridge_result.get("finish_reason") or "stop"),
            "response_id": bridge_result.get("response_id"),
            "usage": usage,
            "usage_reliable": usage_reliable,
            "context": context_budget,
            "openai_compatible": True,
            "credential_location": "platform_only",
            "data_sink": PROJECT_TRAINING_SINK_DEFAULT_GATEWAY_ID,
        }
        call.status = "completed"
        call.output_result = result_payload
        call.output_summary = {
            "ok": True,
            "target_gateway_id": instance.id,
            "model": model_id,
            "model_loaded": bool(model_record.get("loaded")),
            "output_hash": _hash_payload(output_text),
            "usage": usage,
            "usage_reliable": usage_reliable,
            "context": context_budget,
        }
        call.cost_json = {
            "credential_location": "platform_only",
            "target_gateway_id": instance.id,
            "model": model_id,
            "usage": usage,
            "usage_reliable": usage_reliable,
            "context": context_budget,
        }
        call.latency_ms = int((perf_counter() - started) * 1000)
        call.completed_at = completed
        run.updated_at = completed
        db.add(AuditLog(
            user_id=str(getattr(user, "id", "") or "") or None,
            action="project.resident_model.call",
            target_type="training_gateway",
            target_id=instance.id,
            detail={
                "project_id": project.id,
                "project_run_id": run.id,
                "capability_call_id": call.id,
                "model": model_id,
                "loaded": bool(model_record.get("loaded")),
                "max_tokens": max_tokens,
                "max_input_tokens": context_budget["max_input_tokens"],
                "estimated_input_tokens": context_budget["estimated_input_tokens"],
                "status": "completed",
                "credential_location": "platform_only",
            },
        ))
        await db.flush()
        await _capture_project_capability_learning(db, call)
        sink = await enqueue_project_sdk_training_sink(
            db,
            user,
            project,
            run,
            source_type="project_capability_call",
            source_id=call.id,
            source_status="completed",
            request_id=request_id,
        )
        return {
            "ok": True,
            "capability": capability,
            "project_run_id": run.id,
            "call_id": call.id,
            "result": result_payload,
            "training_sink": sink,
            "trainingSink": sink,
        }
    except Exception as exc:  # noqa: BLE001
        completed = now_bjt()
        detail = getattr(exc, "message", None) or str(exc)
        call.status = "failed"
        call.output_result = {
            "ok": False,
            "error": detail[:1000],
            "capability": capability,
            "model": model_id,
            "target_gateway_id": instance.id,
            "credential_location": "platform_only",
        }
        call.output_summary = {
            "ok": False,
            "target_gateway_id": instance.id,
            "model": model_id,
            "error": detail[:1000],
        }
        call.error = detail[:4000]
        call.latency_ms = int((perf_counter() - started) * 1000)
        call.completed_at = completed
        run.updated_at = completed
        db.add(AuditLog(
            user_id=str(getattr(user, "id", "") or "") or None,
            action="project.resident_model.call",
            target_type="training_gateway",
            target_id=instance.id,
            detail={
                "project_id": project.id,
                "project_run_id": run.id,
                "capability_call_id": call.id,
                "model": model_id,
                "status": "failed",
                "error": detail[:1000],
                "credential_location": "platform_only",
            },
        ))
        await db.flush()
        await _capture_project_capability_learning(db, call)
        await enqueue_project_sdk_training_sink(
            db,
            user,
            project,
            run,
            source_type="project_capability_call",
            source_id=call.id,
            source_status="failed",
            request_id=request_id,
        )
        await _commit_failed_capability_call(db, call)
        if isinstance(exc, AppError):
            raise AppError(exc.code, exc.status, _capability_error_detail(call, capability=capability, detail=exc.detail or detail)) from exc
        raise AppError(
            "PROJECT_CAPABILITY_DENIED",
            502,
            _capability_error_detail(call, capability=capability, detail=detail[:1000]),
        ) from exc


async def analyze_project_run(
    db: AsyncSession,
    user: User,
    project_run_id: str,
    *,
    prompt: str | None = None,
    raise_on_error: bool = False,
    request_id: str | None = None,
    context_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run, project = await _require_run(db, user, project_run_id, require_write=True)
    request_id = _text(request_id, limit=80)
    model_profile = _project_ai_gateway_model_profile(project, capability_key="ai.analyze")
    await _acquire_project_gateway_request_lock(db, project_run_id=run.id, request_id=request_id, channel="capability")
    cached = await _cached_capability_call(db, run.id, request_id)
    if cached:
        cached_result = _safe_dict(cached.output_result)
        if cached.status == "completed":
            return {**cached_result, "deduped": True, "call_id": cached.id}
        if cached.status == "failed":
            return {**cached_result, "deduped": True, "call_id": cached.id}
        return {
            "ok": False,
            "status": cached.status,
            "deduped": True,
            "call_id": cached.id,
            "error": cached.error or "项目 AI 分析仍在运行中",
        }

    try:
        _ensure_project_capability_quota(run)
    except AppError as exc:
        if raise_on_error:
            raise
        detail = str(exc.detail if hasattr(exc, "detail") else exc)
        completed = now_bjt()
        call = await _record_failed_project_capability_call(
            db,
            run,
            project,
            request_id=request_id,
            capability="ai.analyze",
            capability_type="ai",
            input_payload={"prompt": prompt or "", "run_status": run.status},
            input_summary={
                "reason": "capability_quota_exceeded",
                "project_id": project.id,
                "project_run_id": run.id,
            },
            error=detail,
        )
        summary = {
            "ok": False,
            "status": "ai_failed",
            "error": detail[:1000],
            "call_id": call.id,
            "generated_at": isoformat_bjt(completed),
        }
        run.ai_summary = _merge_run_ai_summary(run, summary)
        run.status = "ai_failed"
        run.error = detail[:4000]
        run.updated_at = completed
        await db.flush()
        return summary

    started = perf_counter()
    visual_analysis = await _analyze_project_visual_assets(db, user, run)
    now = now_bjt()
    context_pack = {
        "prompt": prompt or "分析这个平台项目运行输出，提炼结论、风险、待办建议和可进入 AI 循环的学习点。",
        "project": {
            "id": project.id,
            "name": project.name,
            "type": project.type,
            "department": project.department,
            "visibility": project.visibility,
            "version_id": run.version_id,
        },
        "run": serialize_run(run),
        "output_contract": {
            "reports": "项目可返回 reports[] 进入收件报告",
            "todos": "项目可返回 todos[] 进入待办",
            "proofs": "项目可返回 proofs[] 作为证据链摘要",
        },
        "capability": {
            "key": "ai.analyze",
            "gateway": "skillforge_project_gateway",
            "credential_location": "platform_only",
        },
        "capability_input": _safe_dict(context_input),
        "visual_analysis": visual_analysis,
    }
    call = ProjectCapabilityCall(
        project_run_id=run.id,
        request_id=request_id,
        capability_key="ai.analyze",
        capability_type="ai",
        status="running",
        input_payload=context_pack,
        input_summary={
            "project_id": project.id,
            "project_run_id": run.id,
            "input_hash": _hash_payload(run.input_snapshot),
            "output_hash": _hash_payload(run.output_result),
            "visual_analysis_status": visual_analysis.get("status"),
            "visual_frame_count": len(_safe_list(visual_analysis.get("frames"))),
        },
        created_at=now,
    )
    db.add(call)
    run.status = "waiting_ai"
    run.capability_call_count = int(getattr(run, "capability_call_count", 0) or 0) + 1
    run.updated_at = now
    await db.flush()

    # Persist the running call before waiting on an external model so project
    # heartbeat updates are not blocked by this request's row locks.
    await db.commit()

    try:
        response = await codex_service._builtin_platform_ai_analyze(
            db,
            user,
            {
                "prompt": _project_ai_visible_prompt(context_pack),
                "context_pack": context_pack,
                "json_mode": False,
                "max_output_tokens": 4096,
                "model_profile": model_profile,
            },
            effective_skill_id=None,
            effective_run_id=run.execution_run_id or run.id,
        )
        completed = now_bjt()
        summary = {
            "ok": True,
            "status": "ai_completed",
            "call_id": call.id,
            "model": response.get("model"),
            "model_profile": response.get("model_profile") or (model_profile or "default"),
            "output": response.get("output"),
            "visual_analysis": visual_analysis,
            "prompt_hash": response.get("prompt_hash"),
            "credential_location": response.get("credential_location"),
            "generated_at": isoformat_bjt(completed),
        }
        call.status = "completed"
        call.output_result = summary
        call.output_summary = {
            "ok": True,
            "model": response.get("model"),
            "model_profile": response.get("model_profile") or (model_profile or "default"),
            "prompt_hash": response.get("prompt_hash"),
            "visual_analysis_status": visual_analysis.get("status"),
            "visual_frame_count": len(_safe_list(visual_analysis.get("frames"))),
        }
        call.cost_json = {"credential_location": response.get("credential_location")}
        call.latency_ms = int((perf_counter() - started) * 1000)
        call.completed_at = completed
        run.ai_summary = _merge_run_ai_summary(run, summary)
        run.status = "ai_completed"
        run.updated_at = completed
        await db.flush()
        await _capture_project_capability_learning(db, call)
        return summary
    except Exception as exc:  # noqa: BLE001
        completed = now_bjt()
        detail = getattr(exc, "message", None) or str(exc)
        summary = {
            "ok": False,
            "status": "ai_failed",
            "error": detail[:1000],
            "generated_at": isoformat_bjt(completed),
        }
        call.status = "failed"
        call.output_result = summary
        call.error = detail[:4000]
        call.latency_ms = int((perf_counter() - started) * 1000)
        call.completed_at = completed
        run.ai_summary = _merge_run_ai_summary(run, summary)
        run.status = "ai_failed"
        run.error = detail[:4000]
        run.updated_at = completed
        await db.flush()
        await _capture_project_capability_learning(db, call)
        if raise_on_error and isinstance(exc, AppError):
            raise
        if raise_on_error:
            raise AppError("PROJECT_CAPABILITY_DENIED", 502, {"detail": detail}) from exc
        return summary


async def call_project_capability(db: AsyncSession, user: User, project_run_id: str, data: dict[str, Any]) -> dict[str, Any]:
    key = _canonical_capability_key(data.get("capability") or data.get("capability_key") or data.get("key") or "")
    run, project = await _require_run(db, user, project_run_id, require_write=True)
    request_id = _request_id(data)
    await _acquire_project_gateway_request_lock(db, project_run_id=run.id, request_id=request_id, channel="capability")
    cached = await _cached_capability_call(db, run.id, request_id)
    if cached:
        return await _capability_cache_response(db, run, cached)

    try:
        _validate_project_capability_payload(run, data)
    except AppError as exc:
        if exc.code != "PROJECT_PAYLOAD_TOO_LARGE":
            raise
        detail = exc.detail if isinstance(exc.detail, dict) else {"detail": str(exc.detail or exc)}
        actual_bytes = detail.get("actual_bytes")
        max_bytes = detail.get("max_bytes")
        error = (
            f"项目能力输入超过平台限制：{actual_bytes}/{max_bytes} bytes"
            if actual_bytes is not None and max_bytes is not None
            else "项目能力输入超过平台限制。"
        )
        call = await _record_failed_project_capability_call(
            db,
            run,
            project,
            request_id=request_id,
            capability=key or "unknown",
            capability_type=str(data.get("type") or ("ai" if key in PROJECT_AI_CAPABILITIES or key == "ai.analyze" else "unknown"))[:40],
            input_payload={},
            input_summary={
                "reason": "capability_payload_too_large",
                "payload_reason": detail.get("reason"),
                "actual_bytes": actual_bytes,
                "max_bytes": max_bytes,
                "project_id": project.id,
                "project_run_id": run.id,
            },
            error=error,
        )
        await _commit_failed_capability_call(db, call)
        raise AppError(
            "PROJECT_PAYLOAD_TOO_LARGE",
            413,
            _capability_error_detail(call, capability=key, detail=detail),
        ) from exc
    if key in {"ai.analyze", "platform.ai.analyze"}:
        return await analyze_project_run(
            db,
            user,
            project_run_id,
            prompt=_text(data.get("prompt"), limit=1000),
            raise_on_error=False,
            request_id=request_id,
            context_input=_capability_input(data),
        )
    try:
        _ensure_project_capability_quota(run)
    except AppError as exc:
        detail = str(exc.detail if hasattr(exc, "detail") else exc)
        call = await _record_failed_project_capability_call(
            db,
            run,
            project,
            request_id=request_id,
            capability=key or "unknown",
            capability_type=str(data.get("type") or ("ai" if key in PROJECT_AI_CAPABILITIES else "unknown"))[:40],
            input_payload=_capability_input(data),
            input_summary={
                "reason": "capability_quota_exceeded",
                "project_id": project.id,
                "project_run_id": run.id,
                "actual_count": int(getattr(run, "capability_call_count", 0) or 0),
            },
            error=detail,
        )
        await _commit_failed_capability_call(db, call)
        raise AppError("PROJECT_CAPABILITY_DENIED", 429, _capability_error_detail(call, capability=key, detail=exc.detail or detail)) from exc
    if not _capability_allowed(project, key):
        now = now_bjt()
        call = ProjectCapabilityCall(
            project_run_id=run.id,
            request_id=request_id,
            capability_key=key or "unknown",
            capability_type=str(data.get("type") or "unknown")[:40],
            status="failed",
            input_payload=_capability_input(data),
            input_summary={
                "reason": "capability_not_declared",
                "declared": sorted(_declared_project_capabilities(project)),
                "payload_keys": sorted(_capability_input(data).keys())[:30],
            },
            error="项目包未声明该能力，平台拒绝代调用。",
            created_at=now,
            completed_at=now,
        )
        db.add(call)
        run.capability_call_count = int(getattr(run, "capability_call_count", 0) or 0) + 1
        run.updated_at = now
        await db.flush()
        await _capture_project_capability_learning(db, call)
        await _commit_failed_capability_call(db, call)
        raise AppError("PROJECT_CAPABILITY_DENIED", 403, _capability_error_detail(call, capability=key, detail=call.error))

    resident_requested, _resident_gateway_id, resident_model_id = _project_resident_model_request(data)
    if resident_requested:
        if key not in PROJECT_AI_CAPABILITIES:
            raise AppError(
                "PROJECT_CAPABILITY_DENIED",
                400,
                {
                    "detail": "236 常驻模型只能通过项目 AI 能力调用。",
                    "capability": key,
                    "model": resident_model_id,
                },
            )
        return await _call_project_236_resident_model(
            db,
            user,
            run,
            project,
            capability=key,
            data=data,
            request_id=request_id,
            model_id=resident_model_id or "",
        )

    if key in PROJECT_MEDIA_CAPABILITIES:
        payload = _capability_input(data)
        now = now_bjt()
        started = perf_counter()
        call = ProjectCapabilityCall(
            project_run_id=run.id,
            request_id=request_id,
            capability_key=key,
            capability_type="media",
            status="running",
            input_payload=payload,
            input_summary={
                "project_id": project.id,
                "project_run_id": run.id,
                "input_hash": _hash_payload(payload),
                "control_plane": True,
            },
            created_at=now,
        )
        db.add(call)
        run.capability_call_count = int(getattr(run, "capability_call_count", 0) or 0) + 1
        run.updated_at = now
        await db.flush()
        # Planning and Bridge execution can take minutes.  Persist the trace and
        # release project row locks before leaving the Project Gateway service.
        await db.commit()
        try:
            from app.media.service import dispatch_project_capability

            result_payload = await dispatch_project_capability(db, user, project, run, key, payload)
            completed = now_bjt()
            call.status = "completed"
            call.output_result = result_payload
            call.output_summary = {
                "ok": True,
                "capability": key,
                "output_hash": _hash_payload(result_payload),
            }
            call.cost_json = {"credential_location": "platform_only", "execution_location": "bridge_or_control_plane"}
            call.latency_ms = int((perf_counter() - started) * 1000)
            call.completed_at = completed
            run.updated_at = completed
            await db.flush()
            await _capture_project_capability_learning(db, call)
            return {
                "ok": True,
                "capability": key,
                "project_run_id": run.id,
                "call_id": call.id,
                "result": result_payload,
            }
        except Exception as exc:  # noqa: BLE001
            completed = now_bjt()
            exc_detail = getattr(exc, "detail", None)
            if isinstance(exc_detail, dict):
                detail = (
                    _text(exc_detail.get("detail"), limit=1000)
                    or _text(exc_detail.get("reason"), limit=1000)
                    or getattr(exc, "message", None)
                    or str(exc)
                )
            else:
                detail = _text(exc_detail, limit=1000) or getattr(exc, "message", None) or str(exc)
            call.status = "failed"
            call.output_result = {"ok": False, "capability": key, "error": detail[:1000]}
            call.output_summary = {"ok": False, "capability": key, "error": detail[:1000]}
            call.error = detail[:4000]
            call.latency_ms = int((perf_counter() - started) * 1000)
            call.completed_at = completed
            run.updated_at = completed
            await db.flush()
            await _capture_project_capability_learning(db, call)
            await _commit_failed_capability_call(db, call)
            if isinstance(exc, AppError):
                raise AppError(exc.code, exc.status, _capability_error_detail(call, capability=key, detail=exc.detail or detail)) from exc
            raise AppError("PROJECT_CAPABILITY_DENIED", 502, _capability_error_detail(call, capability=key, detail=detail[:1000])) from exc

    if key in PROJECT_AI_CAPABILITIES:
        model_profile = _project_ai_gateway_model_profile(project, capability_key=key)
        payload = _capability_input(data)
        prompt = (
            _text(data.get("prompt"), limit=8000)
            or _text(payload.get("prompt"), limit=8000)
            or _text(payload.get("question"), limit=8000)
            or _text(payload.get("query"), limit=8000)
        )
        if not prompt and payload:
            prompt = f"请基于以下项目输入完成平台 AI 能力调用：{payload}"
        if not prompt:
            raise AppError("PARAM_INVALID", 400, {"detail": "AI 能力调用需要 prompt/question/query 或 input"})
        json_mode = bool(data.get("json_mode") or data.get("jsonMode") or payload.get("json_mode"))
        temperature = _safe_float(data.get("temperature") or payload.get("temperature"), default=0.2, minimum=0.0, maximum=2.0)
        max_output_tokens = _safe_int(
            data.get("max_output_tokens") or data.get("maxOutputTokens") or payload.get("max_output_tokens"),
            default=4096,
            minimum=1,
            maximum=16384,
        )
        call_input_payload = {
            "prompt": prompt,
            "input": payload,
            "json_mode": json_mode,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }

        now = now_bjt()
        started = perf_counter()
        call = ProjectCapabilityCall(
            project_run_id=run.id,
            request_id=request_id,
            capability_key=key,
            capability_type="ai",
            status="running",
            input_payload=call_input_payload,
            input_summary={
                "project_id": project.id,
                "project_run_id": run.id,
                "input_hash": _hash_payload(payload),
                "prompt_hash": _hash_payload(prompt),
            },
            created_at=now,
        )
        db.add(call)
        run.capability_call_count = int(getattr(run, "capability_call_count", 0) or 0) + 1
        run.updated_at = now
        await db.flush()

        # External inference can take minutes; release project row locks first.
        await db.commit()
        try:
            response = await codex_service._builtin_platform_ai_analyze(
                db,
                user,
                {
                    "prompt": prompt,
                    "context_pack": _project_ai_context_pack(project, run, key, payload),
                    "json_mode": json_mode,
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                    "model_profile": model_profile,
                },
                effective_skill_id=None,
                effective_run_id=run.execution_run_id or run.id,
            )
            completed = now_bjt()
            result_payload = {
                "output": response.get("output"),
                "model": response.get("model"),
                "model_profile": response.get("model_profile") or (model_profile or "default"),
                "prompt_hash": response.get("prompt_hash"),
                "credential_location": response.get("credential_location"),
            }
            call.status = "completed"
            call.output_result = result_payload
            call.output_summary = {
                "ok": True,
                "model": response.get("model"),
                "prompt_hash": response.get("prompt_hash"),
                "output_hash": _hash_payload(response.get("output")),
            }
            call.cost_json = {"credential_location": response.get("credential_location")}
            call.latency_ms = int((perf_counter() - started) * 1000)
            call.completed_at = completed
            run.updated_at = completed
            await db.flush()
            await _capture_project_capability_learning(db, call)
            return {
                "ok": True,
                "capability": key,
                "project_run_id": run.id,
                "call_id": call.id,
                "result": result_payload,
            }
        except Exception as exc:  # noqa: BLE001
            completed = now_bjt()
            detail = getattr(exc, "message", None) or str(exc)
            call.status = "failed"
            call.output_result = {"ok": False, "error": detail[:1000], "capability": key}
            call.error = detail[:4000]
            call.latency_ms = int((perf_counter() - started) * 1000)
            call.completed_at = completed
            run.updated_at = completed
            await db.flush()
            await _capture_project_capability_learning(db, call)
            await _commit_failed_capability_call(db, call)
            if isinstance(exc, AppError):
                raise AppError(exc.code, exc.status, _capability_error_detail(call, capability=key, detail=exc.detail or detail)) from exc
            raise AppError("PROJECT_CAPABILITY_DENIED", 502, _capability_error_detail(call, capability=key, detail=detail[:1000])) from exc

    if key in PROJECT_MCP_READ_CAPABILITIES:
        payload = _capability_input(data)
        now = now_bjt()
        started = perf_counter()
        call = ProjectCapabilityCall(
            project_run_id=run.id,
            request_id=request_id,
            capability_key=key,
            capability_type="mcp_read",
            status="running",
            input_payload=payload,
            input_summary={
                "project_id": project.id,
                "project_run_id": run.id,
                "input_hash": _hash_payload(payload),
                "data_scope": PROJECT_MCP_READ_CAPABILITIES[key].get("data_scope"),
            },
            created_at=now,
        )
        db.add(call)
        run.capability_call_count = int(getattr(run, "capability_call_count", 0) or 0) + 1
        run.updated_at = now
        await db.flush()
        try:
            result_payload = await _call_declared_project_mcp_read_tool(db, user, key, payload)
            completed = now_bjt()
            data_result = _safe_dict(result_payload.get("data"))
            call.status = "completed"
            call.output_result = result_payload
            call.output_summary = {
                "ok": True,
                "tool": result_payload.get("tool"),
                "data_scope": _safe_dict(result_payload.get("proof")).get("data_scope"),
                "result_count": data_result.get("count") or len(_safe_list(data_result.get("items"))),
            }
            call.cost_json = {"credential_location": "platform_only", "dry_run": True}
            call.latency_ms = int((perf_counter() - started) * 1000)
            call.completed_at = completed
            run.updated_at = completed
            await db.flush()
            await _capture_project_capability_learning(db, call)
            return {
                "ok": True,
                "capability": key,
                "project_run_id": run.id,
                "call_id": call.id,
                "result": result_payload,
            }
        except Exception as exc:  # noqa: BLE001
            completed = now_bjt()
            detail = getattr(exc, "message", None) or str(exc)
            call.status = "failed"
            call.output_result = {"ok": False, "error": detail[:1000], "capability": key}
            call.error = detail[:4000]
            call.latency_ms = int((perf_counter() - started) * 1000)
            call.completed_at = completed
            run.updated_at = completed
            await db.flush()
            await _capture_project_capability_learning(db, call)
            await _commit_failed_capability_call(db, call)
            if isinstance(exc, AppError):
                raise AppError(exc.code, exc.status, _capability_error_detail(call, capability=key, detail=exc.detail or detail)) from exc
            raise AppError("PROJECT_CAPABILITY_DENIED", 502, _capability_error_detail(call, capability=key, detail=detail[:1000])) from exc

    now = now_bjt()
    call = ProjectCapabilityCall(
        project_run_id=run.id,
        request_id=request_id,
        capability_key=key or "unknown",
        capability_type=str(data.get("type") or "unknown")[:40],
        status="failed",
        input_payload=_capability_input(data),
        input_summary={"reason": "capability_not_supported", "payload_keys": sorted(_safe_dict(data.get("input")).keys())[:30]},
        error="当前项目网关已支持平台 AI、受控媒体任务和已声明的只读平台数据/MCP 能力；其它能力需要先接入项目网关白名单。",
        created_at=now,
        completed_at=now,
    )
    db.add(call)
    run.capability_call_count = int(getattr(run, "capability_call_count", 0) or 0) + 1
    run.updated_at = now
    await db.flush()
    await _capture_project_capability_learning(db, call)
    await _commit_failed_capability_call(db, call)
    raise AppError("PROJECT_CAPABILITY_DENIED", 400, _capability_error_detail(call, capability=key, detail=call.error))
