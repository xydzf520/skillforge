"""Codex /sf integration API.

The local Codex skill never receives platform credentials for real MCP servers.
It holds a 30-day CLI token for SkillForge only, then calls this API. SkillForge
checks account state and permissions on every request and runs MCP tools server
side.
"""

from __future__ import annotations

import html
import io
import json
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import can_access_department, get_accessible_departments, role_matches_any
from app.auth.dependencies import SESSION_COOKIE, require_role, verify_session_token
from app.auth.models import User
from app.codex import plugin_bundle, service
from app.codex.models import CodexCliSession, CodexDebugRun, CodexMcpCallAudit, CodexOutputPreview, CodexSkillSubmission
from app.common.exceptions import AppError
from app.common.audit import AuditLog, audit
from app.common.cache import cached, invalidate_sf_cache
from app.common.time_utils import isoformat_bjt, now_bjt
from app.config import settings
from app.database import async_session_factory, get_db
from app.org.models import OrgUnit, UserOrgMembership
from app.platform_settings import SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH_REASON, get_platform_security_settings
from app.projects import service as project_service
from app.reviews.models import Review
from app.reviews.models import ReviewComment
from app.skills.core.access import get_skill_permissions, require_skill_access
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill
from app.execution.models import DecisionLog, ExecutionArtifact, ExecutionRun, ExecutionRunLog, ExecutionStep, NodeScheduleConfig, SkillSyncAttempt
from app.execution.execution_service import issue_run_token
from app.todos.models import DecisionRequest
from app.agents.router import (
    AnalysisAgentUpsertRequest,
    AnalysisAgentValidateRequest,
    list_analysis_agents as list_business_analysis_agents,
    upsert_analysis_agent as upsert_business_analysis_agent,
    validate_analysis_agent as validate_business_analysis_agent,
)


router = APIRouter()

_ADMIN_CACHE_KEY_EXCLUDES = frozenset({"db", "session", "request", "background_tasks", "current_user"})
_ADMIN_CACHE_SCOPE = ("current_user.id", "current_user.role", "current_user.department", "current_user.can_view_all")


class LoginIntentRequest(BaseModel):
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str = "S256"
    state: str = ""
    nonce: str | None = None
    device_name: str | None = None


class TokenRequest(BaseModel):
    login_intent_id: str
    auth_code: str = ""
    code_verifier: str


class DebugRunRequest(BaseModel):
    skill_id: str
    run_mode: str = "local_debug"
    package_hash: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    requested_tools: list[str] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)


class CompleteDebugRunRequest(BaseModel):
    status: str = "completed"
    duration_ms: int | None = None
    output: Any = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    test_summary: dict[str, Any] | None = None


class McpCallRequest(BaseModel):
    server: str = "skillforge"
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    skill_id: str | None = None
    run_id: str | None = None
    run_mode: str = "local_debug"
    dry_run: bool | None = True
    idempotency_key: str | None = None


class ProjectOrgResolveRequest(BaseModel):
    department: str = ""
    department_id: str = ""
    owner: str = ""
    visibility: str = ""


def _codex_project_generation_contract() -> dict[str, Any]:
    return {
        "version": 1,
        "project_contract": {
            "manifest_names": ["projectforge.yaml", "projectforge.yml", "projectforge.json", "skillforge-project.yaml", "manifest.yaml", "manifest.json"],
            "required_fields": ["project_id", "name", "kind", "entry"],
            "kinds": ["web_static", "external_web", "dashboard", "internal_tool", "playbook_ref"],
            "visibility": ["company", "department", "private"],
            "capabilities": ["ai.generate", "ai.chat", "ai.analyze"],
            "outputs": {"reports": True, "todos": True, "proofs": True},
        },
        "gateway_sdk": {
            "script": "/project-gateway-sdk.js",
            "global": "window.PlatformProjectGateway",
            "methods": ["ready", "startHeartbeat", "input", "ingest", "analyze", "capability", "uploadAsset", "listAssets", "media.subscribe"],
            "credential_location": "platform_only",
        },
        "run_assets": {
            "upload_method": "PlatformProjectGateway.uploadAsset({file, metadata})",
            "list_method": "PlatformProjectGateway.listAssets()",
            "metadata_required": True,
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
            "fallback": "原视频上传或视觉模型失败时使用关键帧序列兜底。",
        },
        "generation_recipes": {
            "short-video-analysis": {
                "name": "短视频投放素材分析",
                "default_project_id": "short-video-analysis-mvp",
                "default_department_id": "931765248",
                "default_department": "示例品牌内容电商运营部",
                "default_visibility": "company",
                "default_owner": "祁莹莹",
                "required_capabilities": ["ai.analyze", "ai.generate"],
                "required_outputs": ["reports", "proofs"],
                "required_inputs": ["campaign_data", "material_a", "material_b"],
            }
        },
    }


class CodexRunSkillRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    sandbox: bool = False
    run_mode: str | None = Field(default=None, max_length=30)
    parent_run_id: str | None = Field(default=None, max_length=50)
    data_provenance: list[dict[str, Any]] | None = None
    sample_used: bool | None = None


class LocalSkillStatusItem(BaseModel):
    client_ref: str | None = None
    skill_id: str
    base_commit: str | None = None
    package_hash: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)


class LocalSkillStatusRequest(BaseModel):
    items: list[LocalSkillStatusItem] = Field(default_factory=list)


class ConflictCheckRequest(BaseModel):
    base_commit: str | None = None
    package_hash: str | None = None


class ApproveSubmissionRequest(BaseModel):
    verify_after_sync: bool = True
    runtime_instance_id: str | None = None
    cron_expression: str | None = None
    static_check_override: bool = False
    static_check_override_reason: str = ""


class CodexProjectAutoRegisterRequest(BaseModel):
    manifest: dict[str, Any] = Field(default_factory=dict)
    package_hash: str | None = None
    source: str | None = "sf_project_submit"

    model_config = {"extra": "allow"}


class CodexProjectServiceInvokeRequest(BaseModel):
    request_id: str | None = Field(default=None, max_length=80)
    input: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    prompt: str | None = None
    capability: str | None = None
    capability_key: str | None = None
    key: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    reports: list[dict[str, Any]] = Field(default_factory=list)
    todos: list[dict[str, Any]] = Field(default_factory=list)
    proofs: list[dict[str, Any]] = Field(default_factory=list)
    auto_analyze: bool = True
    analysis_prompt: str | None = None
    parent_run_id: str | None = None

    model_config = {"extra": "allow"}


class OutputPreviewRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    source_name: str | None = Field(default=None, max_length=255)
    content_type: str = Field(default="auto", pattern="^(auto|json|markdown|html|text)$")
    content: Any
    expires_in_hours: int = Field(default=72, ge=1, le=168)


class PreviewApplyRequest(BaseModel):
    real: bool = False
    idempotency_key: str | None = Field(default=None, max_length=200)
    skill_id: str | None = Field(default=None, max_length=100)
    run_id: str | None = Field(default=None, max_length=50)
    params: dict[str, Any] = Field(default_factory=dict)
    run_mode: str | None = Field(default="manual_real", max_length=30)
    triggered_by: str | None = Field(default=None, max_length=80)
    parent_run_id: str | None = Field(default=None, max_length=50)
    batch_id: str | None = Field(default=None, max_length=50)
    data_provenance: list[dict[str, Any]] = Field(default_factory=list)
    sample_used: bool | None = None


class ScheduleUpdateRequest(BaseModel):
    action: str = Field(..., pattern="^(start|stop|update|sync)$")
    cron_expression: str | None = Field(default=None, max_length=100)


class SkillSyncRequest(BaseModel):
    instance_ids: list[str] | None = None
    department: str | None = Field(default=None, max_length=120)
    push_git: bool = True


class ReviewCommentRequest(BaseModel):
    content: str = Field(..., max_length=4000)
    file_path: str | None = Field(default=None, max_length=255)
    line_number: int | None = None
    side: str | None = Field(default=None, pattern="^(left|right)?$")


class ReviewRequestChangesRequest(BaseModel):
    reason: str = Field("", max_length=4000)
    reject_reason: str = Field("request_changes", max_length=50)


class OutputValidateRequest(BaseModel):
    output: Any
    max_bytes: int = Field(default=6 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)


class MarketVisibilityRequest(BaseModel):
    market_status: str


class MarketRatingRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field("", max_length=2000)


CODEX_USAGE_EXCLUDED_PATHS = {
    "/api/codex/auth/introspect",
    "/api/codex/auth/logout",
}


def _client_meta_from_headers(request: Request) -> dict[str, Any]:
    headers = request.headers
    meta = {
        "sf_version": headers.get("x-skillforge-sf-version"),
        "plugin": headers.get("x-skillforge-plugin"),
        "platform": headers.get("x-skillforge-client-platform"),
        "python": headers.get("x-skillforge-client-python"),
        "base_url_source": headers.get("x-skillforge-base-url-source"),
    }
    return {key: value for key, value in meta.items() if value}


def _absolute_public_url(path: str) -> str:
    base = str(settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}{path}" if base else path


def _project_web_path(project_id: Any, **query: Any) -> str:
    encoded_id = quote(str(project_id or ""), safe="")
    path = f"/projects/{encoded_id}"
    clean_query = {key: str(value) for key, value in query.items() if value is not None and str(value) != ""}
    return f"{path}?{urlencode(clean_query)}" if clean_query else path


def _project_standalone_run_path(project_id: Any, project_run_id: Any | None = None) -> str:
    encoded_id = quote(str(project_id or ""), safe="")
    path = f"/project-run/{encoded_id}"
    clean_query = {"run_id": str(project_run_id)} if project_run_id is not None and str(project_run_id) != "" else {}
    return f"{path}?{urlencode(clean_query)}" if clean_query else path


def _project_web_links(project_id: Any, project_run_id: Any | None = None) -> dict[str, Any]:
    project_path = _project_web_path(project_id)
    app_path = _project_standalone_run_path(project_id, project_run_id)
    trace_path = _project_web_path(project_id, run_id=project_run_id)
    return {
        "project_url": _absolute_public_url(project_path),
        "app_url": _absolute_public_url(app_path),
        "trace_url": _absolute_public_url(trace_path),
        "relative_urls": {
            "project": project_path,
            "app": app_path,
            "trace": trace_path,
        },
    }


def _codex_route_name(path: str) -> str:
    if path.endswith("/capabilities"):
        return "capabilities"
    if path.endswith("/mcp-catalog"):
        return "mcp_catalog"
    if "/skill/debug-runs" in path:
        return "debug_run"
    if "/skills/submissions" in path:
        return "submission"
    if path.endswith("/platform/capabilities"):
        return "platform_capabilities"
    if path.endswith("/editor/capabilities"):
        return "editor_capabilities"
    if "/skills" in path:
        return "skill"
    return path.removeprefix("/api/codex/").replace("/", ".")[:80] or "api"


async def record_codex_cli_usage(
    request: Request,
    principal: service.CliPrincipal,
    *,
    action: str | None = None,
    target_type: str | None = "codex_cli_session",
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    path = request.url.path
    session = principal.session
    client_meta = _client_meta_from_headers(request)
    if client_meta:
        previous = session.client_meta_json if isinstance(session.client_meta_json, dict) else {}
        session.client_meta_json = {**previous, **client_meta, "last_seen_at": isoformat_bjt(now_bjt())}
    if path in CODEX_USAGE_EXCLUDED_PATHS or path.startswith("/api/codex/admin/"):
        return
    payload = {
        "path": path,
        "method": request.method,
        "route": _codex_route_name(path),
        "cli_session_id": session.id,
        "device_name": session.device_name,
        "client_meta": session.client_meta_json if isinstance(session.client_meta_json, dict) else {},
        **(detail or {}),
    }
    await audit.log(
        principal.user.id,
        action or "codex.api.call",
        target_type,
        target_id or session.id,
        detail=payload,
    )


def _clean_publish_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _submission_publish_config(
    row: CodexSkillSubmission,
    body: ApproveSubmissionRequest | None,
) -> tuple[str | None, str | None]:
    manifest = row.manifest_json if isinstance(row.manifest_json, dict) else {}
    frontmatter = manifest.get("frontmatter") if isinstance(manifest.get("frontmatter"), dict) else {}

    runtime_instance_id = _clean_publish_text(body.runtime_instance_id if body else None)
    cron_expression = _clean_publish_text(body.cron_expression if body else None)

    if runtime_instance_id is None:
        runtime_instance_id = _clean_publish_text(frontmatter.get("instance_id"))
    if cron_expression is None and _clean_publish_text(frontmatter.get("trigger_type")) == "cron":
        cron_expression = _clean_publish_text(frontmatter.get("trigger_expression"))

    return runtime_instance_id, cron_expression


def bearer_token(authorization: str | None) -> str:
    text = str(authorization or "").strip()
    if not text.lower().startswith("bearer "):
        raise AppError("AUTH_REQUIRED", 401)
    token = text.split(" ", 1)[1].strip()
    if not token:
        raise AppError("AUTH_REQUIRED", 401)
    return token


async def require_cli_principal(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> service.CliPrincipal:
    return await service.authenticate_cli_session(db, bearer_token(authorization))


async def require_any_principal(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> service.CliPrincipal | service.RunPrincipal | service.RuntimePrincipal:
    token = bearer_token(authorization)
    try:
        return await service.authenticate_run_token(db, token)
    except AppError as exc:
        if exc.code not in {"AUTH_REQUIRED"}:
            raise
    try:
        return await service.authenticate_cli_session(db, token)
    except AppError as exc:
        if exc.code not in {"AUTH_REQUIRED"}:
            raise
    return await service.authenticate_runtime_run_token(db, token)


async def current_web_user_or_none(request: Request, db: AsyncSession) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    verified = verify_session_token(token)
    if not verified:
        return None
    user_id, rev = verified
    user = await db.get(User, user_id)
    if not user:
        return None
    if int(getattr(user, "permissions_rev", 0) or 0) != int(rev or 0):
        return None
    try:
        service.assert_active_user(user)
    except AppError:
        return None
    return user


def parse_json_form(value: str, *, field_name: str) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise AppError("PARAM_INVALID", 400, {"field": field_name, "error": str(exc)}) from exc
    if not isinstance(parsed, dict):
        raise AppError("PARAM_INVALID", 400, {"field": field_name, "reason": "must be object"})
    return parsed


MAX_PREVIEW_CONTENT_CHARS = 6 * 1024 * 1024


def _as_preview_text(content: Any, content_type: str) -> tuple[str, str, Any | None]:
    parsed_json: Any | None = None
    if content_type == "json":
        if isinstance(content, str):
            try:
                parsed_json = json.loads(content)
            except json.JSONDecodeError as exc:
                raise AppError("PARAM_INVALID", 400, {"detail": f"content 不是合法 JSON: {exc}"}) from exc
        else:
            parsed_json = content
        return json.dumps(parsed_json, ensure_ascii=False, indent=2, default=str), "json", parsed_json
    if content_type == "auto":
        if isinstance(content, (dict, list)):
            return json.dumps(content, ensure_ascii=False, indent=2, default=str), "json", content
        text = str(content or "")
        stripped = text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed_json = json.loads(text)
                return json.dumps(parsed_json, ensure_ascii=False, indent=2, default=str), "json", parsed_json
            except json.JSONDecodeError:
                pass
        if "<html" in stripped[:200].lower() or "<!doctype html" in stripped[:200].lower():
            return text, "html", None
        if any(marker in text for marker in ("\n#", "\n-", "\n*", "|---", "```", "**")) or stripped.startswith("#"):
            return text, "markdown", None
        return text, "text", None
    return str(content or ""), content_type, None


def _safe_preview_title(title: str | None, *, content_type: str, parsed_json: Any | None, source_name: str | None) -> str:
    text = str(title or "").strip()
    if text:
        return text[:200]
    if isinstance(parsed_json, dict):
        reports = parsed_json.get("reports")
        if isinstance(reports, list) and reports and isinstance(reports[0], dict):
            report_title = str(reports[0].get("title") or "").strip()
            if report_title:
                return report_title[:200]
        for key in ("title", "name", "summary"):
            value = str(parsed_json.get(key) or "").strip()
            if value:
                return value[:200]
    if source_name:
        return f"sf 输出预览 · {source_name}"[:200]
    labels = {"json": "JSON", "markdown": "Markdown", "html": "HTML", "text": "文本"}
    return f"sf 输出预览 · {labels.get(content_type, content_type)}"


def _preview_summary(parsed_json: Any | None, text: str, content_type: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "content_type": content_type,
        "size_chars": len(text),
    }
    if isinstance(parsed_json, dict):
        reports = parsed_json.get("reports")
        todos = parsed_json.get("todos")
        actions = parsed_json.get("actions")
        notifications = parsed_json.get("notifications")
        collector = parsed_json.get("collector")
        summary.update(
            {
                "top_level_keys": sorted(str(key) for key in parsed_json.keys())[:80],
                "report_count": len(reports) if isinstance(reports, list) else 0,
                "todo_count": len(todos) if isinstance(todos, list) else 0,
                "action_count": len(actions) if isinstance(actions, list) else 0,
                "notification_count": len(notifications) if isinstance(notifications, list) else 0,
                "has_collector": isinstance(collector, dict),
            }
        )
        report_titles = []
        if isinstance(reports, list):
            for item in reports[:5]:
                if isinstance(item, dict) and item.get("title"):
                    report_titles.append(str(item.get("title")))
        if report_titles:
            summary["report_titles"] = report_titles
    elif isinstance(parsed_json, list):
        summary["item_count"] = len(parsed_json)
    return summary


def _priority_counts(todos: Any) -> dict[str, int]:
    counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    if not isinstance(todos, list):
        return counts
    for item in todos:
        item_dict = item if isinstance(item, dict) else {}
        payload = item_dict.get("payload") if isinstance(item_dict.get("payload"), dict) else {}
        priority = str(item_dict.get("priority") or payload.get("priority") or "").strip().upper()
        if priority in counts:
            counts[priority] += 1
    return counts


def _validate_output_contract(output: Any, *, max_bytes: int = 6 * 1024 * 1024) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not isinstance(output, dict):
        return {
            "ok": False,
            "errors": [{"path": "$", "message": "输出必须是 JSON object"}],
            "warnings": [],
            "summary": {},
        }
    try:
        size_bytes = len(json.dumps(output, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        size_bytes = 0
    if size_bytes > max_bytes:
        warnings.append({"path": "$", "message": "输出超过建议大小", "size_bytes": size_bytes, "max_bytes": max_bytes})

    reports = output.get("reports")
    todos = output.get("todos")
    actions = output.get("actions")
    notifications = output.get("notifications")
    if reports is not None and not isinstance(reports, list):
        errors.append({"path": "reports", "message": "reports 必须是数组"})
    if todos is not None and not isinstance(todos, list):
        errors.append({"path": "todos", "message": "todos 必须是数组"})
    if actions is not None and not isinstance(actions, list):
        errors.append({"path": "actions", "message": "actions 必须是数组"})
    if notifications is not None and not isinstance(notifications, list):
        errors.append({"path": "notifications", "message": "notifications 必须是数组"})

    if isinstance(reports, list):
        for idx, report in enumerate(reports[:200]):
            if not isinstance(report, dict):
                errors.append({"path": f"reports[{idx}]", "message": "report 必须是 object"})
                continue
            if not str(report.get("title") or "").strip():
                warnings.append({"path": f"reports[{idx}].title", "message": "报告缺少 title"})
            if not (str(report.get("content_markdown") or "").strip() or str(report.get("summary") or "").strip()):
                warnings.append({"path": f"reports[{idx}]", "message": "报告缺少 content_markdown 或 summary"})

    if isinstance(todos, list):
        for idx, todo in enumerate(todos[:500]):
            if not isinstance(todo, dict):
                errors.append({"path": f"todos[{idx}]", "message": "todo 必须是 object"})
                continue
            payload = todo.get("payload") if isinstance(todo.get("payload"), dict) else {}
            if not str(todo.get("title") or payload.get("title") or "").strip():
                errors.append({"path": f"todos[{idx}].title", "message": "待办缺少 title"})
            priority = str(todo.get("priority") or payload.get("priority") or "").strip().upper()
            if priority and priority not in {"P0", "P1", "P2", "P3"}:
                errors.append({"path": f"todos[{idx}].priority", "message": "priority 必须是 P0/P1/P2/P3"})
            if not payload:
                warnings.append({"path": f"todos[{idx}].payload", "message": "待办缺少 payload，平台只能创建轻量待办"})

    summary = {
        "size_bytes": size_bytes,
        "report_count": len(reports) if isinstance(reports, list) else 0,
        "todo_count": len(todos) if isinstance(todos, list) else 0,
        "priority_counts": _priority_counts(todos),
        "action_count": len(actions) if isinstance(actions, list) else 0,
        "notification_count": len(notifications) if isinstance(notifications, list) else 0,
    }
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
    }


def _serialize_preview(row: CodexOutputPreview, request: Request | None = None) -> dict[str, Any]:
    relative_url = f"/api/codex/previews/{row.id}"
    base = str(request.base_url).rstrip("/") if request is not None else str(settings.PUBLIC_BASE_URL).rstrip("/")
    return {
        "id": row.id,
        "title": row.title,
        "source_name": row.source_name,
        "content_type": row.content_type,
        "summary": row.summary_json or {},
        "url": relative_url,
        "public_url": f"{base}{relative_url}",
        "created_at": isoformat_bjt(row.created_at),
        "expires_at": isoformat_bjt(row.expires_at),
    }


def _serialize_execution_run(row: ExecutionRun) -> dict[str, Any]:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    return {
        "run_id": row.id,
        "skill_id": row.skill_id,
        "playbook_id": row.playbook_id,
        "trigger_type": row.trigger_type,
        "run_mode": row.run_mode,
        "parent_run_id": row.parent_run_id,
        "batch_id": row.batch_id,
        "status": row.status,
        "total_steps": row.total_steps,
        "completed_steps": row.completed_steps,
        "summary": row.summary,
        "source_instance_id": row.source_instance_id,
        "started_at": isoformat_bjt(row.started_at),
        "completed_at": isoformat_bjt(row.completed_at),
        "metadata": metadata,
    }


def _serialize_execution_step(row: ExecutionStep) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "skill_id": row.skill_id,
        "step_order": row.step_order,
        "status": row.status,
        "input_data": row.input_data,
        "output_data": row.output_data,
        "started_at": isoformat_bjt(row.started_at),
        "completed_at": isoformat_bjt(row.completed_at),
        "duration_ms": row.duration_ms,
        "error_message": row.error_message,
    }


def _serialize_execution_decision(row: DecisionLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "skill_id": row.skill_id,
        "input_snapshot": row.input_snapshot,
        "output_result": row.output_result,
        "suggested_action": row.suggested_action,
        "approval_level": row.approval_level,
        "approval_status": row.approval_status,
        "target_user": row.target_user,
        "user_action": row.user_action,
        "rating": row.rating,
        "created_at": isoformat_bjt(row.created_at),
    }


def _serialize_execution_log(row: ExecutionRunLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "skill_id": row.skill_id,
        "stream": row.stream,
        "source": row.source,
        "content_tail": row.content_tail,
        "truncated": row.truncated,
        "created_at": isoformat_bjt(row.created_at),
    }


async def _require_codex_run_access(db: AsyncSession, user: User, run_id: str) -> ExecutionRun:
    run = await db.get(ExecutionRun, run_id)
    if not run:
        raise AppError("NOT_FOUND", 404, {"run_id": run_id})
    if not run.skill_id:
        if role_matches_any(user, ("admin", "ai_engineer")) or bool(getattr(user, "can_view_all", False)):
            return run
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    await require_skill_access(db, str(run.skill_id), user, "read")
    return run


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --ink: #172033;
      --muted: #64748b;
      --line: #d8e0ea;
      --panel: #fff;
      --blue: #2563eb;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); line-height: 1.58; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 28px 18px 60px; }}
    .top {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }}
    h1 {{ margin: 0; font-size: 30px; line-height: 1.15; letter-spacing: 0; }}
    .meta {{ color: var(--muted); margin-top: 8px; }}
    .badge {{ display: inline-flex; padding: 5px 9px; border: 1px solid #bfdbfe; border-radius: 999px; background: #eff6ff; color: var(--blue); font-weight: 800; font-size: 12px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-top: 14px; overflow: auto; }}
    pre {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.65 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
    th, td {{ border: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f8fafc; }}
    .stats {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }}
    .stat {{ background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .stat strong {{ display: block; font-size: 22px; line-height: 1; }}
    .stat span {{ color: var(--muted); font-size: 12px; }}
    iframe {{ width: 100%; min-height: 78vh; border: 0; background: #fff; }}
    @media (max-width: 760px) {{ .top, .stats {{ display: block; }} .stat {{ margin-top: 10px; }} }}
  </style>
</head>
<body><main>{body}</main></body>
</html>"""


def _markdown_to_html(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    in_list = False
    in_code = False
    code_lines: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            if in_code:
                out.append(f"<pre>{html.escape(chr(10).join(code_lines))}</pre>")
                code_lines = []
                in_code = False
            else:
                if in_list:
                    out.append("</ul>")
                    in_list = False
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if line.startswith("#"):
            if in_list:
                out.append("</ul>")
                in_list = False
            level = min(len(line) - len(line.lstrip("#")), 3)
            content = html.escape(line[level:].strip())
            out.append(f"<h{level}>{content}</h{level}>")
        elif line.startswith(("- ", "* ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html.escape(line[2:].strip())}</li>")
        elif not line.strip():
            if in_list:
                out.append("</ul>")
                in_list = False
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            safe = html.escape(line).replace("**", "")
            out.append(f"<p>{safe}</p>")
    if in_code:
        out.append(f"<pre>{html.escape(chr(10).join(code_lines))}</pre>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _render_json_preview(row: CodexOutputPreview, parsed: Any) -> str:
    summary = row.summary_json or {}
    stats = [
        ("报告", summary.get("report_count", 0)),
        ("待办", summary.get("todo_count", 0)),
        ("动作", summary.get("action_count", 0)),
        ("通知", summary.get("notification_count", 0)),
    ]
    stats_html = "".join(
        f"<div class='stat'><strong>{html.escape(str(value))}</strong><span>{html.escape(label)}</span></div>"
        for label, value in stats
    )
    detail_html = ""
    if isinstance(parsed, dict):
        reports = parsed.get("reports")
        if isinstance(reports, list) and reports:
            first = reports[0] if isinstance(reports[0], dict) else {}
            md = str(first.get("content_markdown") or first.get("summary") or "")
            if md:
                detail_html = f"<section class='panel'><h2>报告正文</h2>{_markdown_to_html(md)}</section>"
        todos = parsed.get("todos")
        if isinstance(todos, list) and todos:
            rows = []
            for idx, item in enumerate(todos[:80], start=1):
                item_dict = item if isinstance(item, dict) else {}
                payload = item_dict.get("payload") if isinstance(item_dict.get("payload"), dict) else {}
                priority = item_dict.get("priority") or payload.get("priority") or ""
                rows.append(
                    "<tr>"
                    f"<td>{idx}</td>"
                    f"<td>{html.escape(str(item_dict.get('title') or item_dict.get('name') or ''))}</td>"
                    f"<td>{html.escape(str(priority))}</td>"
                    f"<td>{html.escape(str(item_dict.get('assignee') or item_dict.get('owner') or ''))}</td>"
                    "</tr>"
                )
            detail_html += (
                "<section class='panel'><h2>待办预览</h2><table><thead><tr><th>#</th><th>标题</th><th>优先级</th><th>负责人</th></tr></thead>"
                f"<tbody>{''.join(rows)}</tbody></table></section>"
            )
    raw = html.escape(json.dumps(parsed, ensure_ascii=False, indent=2, default=str))
    return f"<section class='stats'>{stats_html}</section>{detail_html}<section class='panel'><h2>原始 JSON</h2><pre>{raw}</pre></section>"


def _render_preview_page(row: CodexOutputPreview) -> str:
    summary = row.summary_json or {}
    header = (
        "<div class='top'>"
        "<div>"
        f"<h1>{html.escape(row.title)}</h1>"
        f"<div class='meta'>来源：{html.escape(row.source_name or 'sf preview')} · 创建：{html.escape(isoformat_bjt(row.created_at))} · 过期：{html.escape(isoformat_bjt(row.expires_at))}</div>"
        "</div>"
        f"<span class='badge'>{html.escape(row.content_type.upper())}</span>"
        "</div>"
    )
    if row.content_type == "json":
        try:
            parsed = json.loads(row.content_text)
        except json.JSONDecodeError:
            parsed = {"raw": row.content_text}
        body = header + _render_json_preview(row, parsed)
    elif row.content_type == "markdown":
        body = header + f"<section class='panel'>{_markdown_to_html(row.content_text)}</section>"
    elif row.content_type == "html":
        escaped_doc = html.escape(row.content_text, quote=True)
        body = header + f"<section class='panel'><iframe sandbox srcdoc=\"{escaped_doc}\"></iframe></section>"
    else:
        body = header + f"<section class='panel'><pre>{html.escape(row.content_text)}</pre></section>"
    if summary:
        body += f"<section class='panel'><h2>摘要</h2><pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=2, default=str))}</pre></section>"
    return _html_page(row.title, body)


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "username": user.username,
        "role": user.role,
        "department": user.department,
        "permissions_rev": int(getattr(user, "permissions_rev", 0) or 0),
    }


def serialize_debug_run(run: CodexDebugRun) -> dict:
    return {
        "run_id": run.id,
        "skill_id": run.skill_id,
        "run_mode": run.run_mode,
        "status": run.status,
        "package_hash": run.package_hash,
        "tool_call_count": run.tool_call_count,
        "created_at": isoformat_bjt(run.created_at),
        "expires_at": isoformat_bjt(run.expires_at),
        "completed_at": isoformat_bjt(run.completed_at) if run.completed_at else None,
        "result": run.result_json,
    }


def serialize_submission(row: CodexSkillSubmission) -> dict:
    return {
        "submission_id": row.id,
        "skill_id": row.skill_id,
        "status": row.status,
        "review_id": row.review_id,
        "git_commit": row.git_commit,
        "package_hash": row.package_hash,
        "checks": row.checks_json,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


def _codex_event_from_audit(row: AuditLog) -> dict[str, Any]:
    detail = row.detail if isinstance(row.detail, dict) else {}
    return {
        "id": f"audit:{row.id}",
        "kind": "api",
        "user_id": row.user_id,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "tool": None,
        "ok": True,
        "dry_run": None,
        "run_mode": detail.get("run_mode"),
        "skill_id": detail.get("skill_id") or (row.target_id if row.target_type == "skill" else None),
        "detail": detail,
        "created_at": isoformat_bjt(row.created_at),
    }


def _codex_event_from_mcp(row: CodexMcpCallAudit) -> dict[str, Any]:
    return {
        "id": f"mcp:{row.id}",
        "kind": "mcp",
        "user_id": row.user_id,
        "action": "codex.mcp.call",
        "target_type": "mcp_tool",
        "target_id": row.tool,
        "tool": row.tool,
        "ok": row.ok,
        "dry_run": row.dry_run,
        "run_mode": row.run_mode,
        "skill_id": row.skill_id,
        "shop_id": row.shop_id,
        "data_scope": row.data_scope,
        "detail": row.detail_json or {},
        "created_at": isoformat_bjt(row.created_at),
    }


def _user_display_map(rows: list[User]) -> dict[str, dict[str, Any]]:
    return {
        row.id: {
            "user_id": row.id,
            "user_name": row.name,
            "username": row.username,
            "department": row.department,
            "role": row.role,
        }
        for row in rows
    }


def _attach_user_display(item: dict[str, Any], user_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    user = user_map.get(str(item.get("user_id") or ""))
    if not user:
        return {
            **item,
            "user_name": item.get("user_id"),
            "username": None,
            "department": None,
            "role": None,
        }
    return {**item, **user}


async def _codex_admin_summary(db: AsyncSession, days: int) -> dict[str, Any]:
    cutoff = now_bjt() - timedelta(days=days)
    active_now = now_bjt()
    plugin_users = (
        await db.execute(select(func.count(func.distinct(CodexCliSession.user_id))))
    ).scalar() or 0
    active_sessions = (
        await db.execute(
            select(func.count(CodexCliSession.id)).where(
                CodexCliSession.revoked_at.is_(None),
                CodexCliSession.expires_at > active_now,
            )
        )
    ).scalar() or 0
    api_calls = (
        await db.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.action.like("codex.%"),
                AuditLog.action != "codex.mcp.call",
                AuditLog.created_at >= cutoff,
            )
        )
    ).scalar() or 0
    mcp_calls = (
        await db.execute(
            select(func.count(CodexMcpCallAudit.id)).where(CodexMcpCallAudit.created_at >= cutoff)
        )
    ).scalar() or 0
    failed_mcp_calls = (
        await db.execute(
            select(func.count(CodexMcpCallAudit.id)).where(
                CodexMcpCallAudit.created_at >= cutoff,
                CodexMcpCallAudit.ok.is_(False),
            )
        )
    ).scalar() or 0
    top_users_rows = (
        await db.execute(
            select(CodexMcpCallAudit.user_id, func.count(CodexMcpCallAudit.id).label("count"))
            .where(CodexMcpCallAudit.created_at >= cutoff)
            .group_by(CodexMcpCallAudit.user_id)
            .order_by(func.count(CodexMcpCallAudit.id).desc())
            .limit(10)
        )
    ).all()
    top_user_ids = [user_id for user_id, _count in top_users_rows if user_id]
    top_user_map = (
        _user_display_map(
            list((await db.execute(select(User).where(User.id.in_(top_user_ids)))).scalars().all())
        )
        if top_user_ids
        else {}
    )
    top_tools_rows = (
        await db.execute(
            select(CodexMcpCallAudit.tool, func.count(CodexMcpCallAudit.id).label("count"))
            .where(CodexMcpCallAudit.created_at >= cutoff)
            .group_by(CodexMcpCallAudit.tool)
            .order_by(func.count(CodexMcpCallAudit.id).desc())
            .limit(10)
        )
    ).all()
    return {
        "days": days,
        "plugin_users": int(plugin_users),
        "active_sessions": int(active_sessions),
        "api_calls": int(api_calls),
        "mcp_calls": int(mcp_calls),
        "failed_mcp_calls": int(failed_mcp_calls),
        "top_users": [
            _attach_user_display({"user_id": user_id, "count": int(count or 0)}, top_user_map)
            for user_id, count in top_users_rows
        ],
        "top_tools": [{"tool": tool, "count": int(count or 0)} for tool, count in top_tools_rows],
    }


@router.post("/auth/login-intent")
async def create_login_intent(body: LoginIntentRequest, db: AsyncSession = Depends(get_db)):
    intent = await service.create_login_intent(
        db,
        redirect_uri=body.redirect_uri,
        code_challenge=body.code_challenge,
        code_challenge_method=body.code_challenge_method,
        state=body.state,
        nonce=body.nonce,
        device_name=body.device_name,
    )
    query = urlencode({"login_intent_id": intent.id, "state": intent.state})
    authorize_url = f"{str(settings.PUBLIC_BASE_URL).rstrip('/')}/api/codex/auth/authorize?{query}"
    return {
        "login_intent_id": intent.id,
        "authorize_url": authorize_url,
        "state": intent.state,
        "expires_at": isoformat_bjt(intent.expires_at),
    }


@router.get("/auth/authorize")
async def authorize_login_intent(
    request: Request,
    login_intent_id: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    user = await current_web_user_or_none(request, db)
    if not user:
        next_url = str(request.url.path)
        if request.url.query:
            next_url = f"{next_url}?{request.url.query}"
        return RedirectResponse(url=f"/login?{urlencode({'next': next_url})}", status_code=302)
    intent, code = await service.authorize_login_intent(
        db,
        login_intent_id=login_intent_id,
        state=state,
        user=user,
    )
    if intent.redirect_uri == service.POLL_REDIRECT_URI:
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><title>SkillForge 授权完成</title>"
            "<body style='font-family:system-ui,sans-serif;margin:40px'>"
            "<h2>SkillForge 授权完成</h2><p>可以回到 Codex 继续执行。</p></body>",
            status_code=200,
        )
    separator = "&" if "?" in intent.redirect_uri else "?"
    return RedirectResponse(
        url=f"{intent.redirect_uri}{separator}{urlencode({'code': code, 'state': intent.state, 'login_intent_id': intent.id})}",
        status_code=302,
    )


@router.post("/auth/token")
async def exchange_token(body: TokenRequest, db: AsyncSession = Depends(get_db)):
    session, raw_token, user = await service.exchange_auth_code(
        db,
        login_intent_id=body.login_intent_id,
        auth_code=body.auth_code,
        code_verifier=body.code_verifier,
    )
    return {
        "access_token": raw_token,
        "token_type": "Bearer",
        "expires_at": isoformat_bjt(session.expires_at),
        "session_id": session.id,
        "user": serialize_user(user),
    }


@router.post("/auth/introspect")
async def introspect(principal: service.CliPrincipal = Depends(require_cli_principal)):
    return {
        "active": True,
        "session_id": principal.session.id,
        "expires_at": isoformat_bjt(principal.session.expires_at),
        "user": serialize_user(principal.user),
    }


@router.post("/auth/logout")
async def logout(
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.session.revoked_at = now_bjt()
    principal.session.revoked_reason = "user_logout"
    await service.revoke_run_tokens_for_session(db, principal.session.id)
    return {"ok": True}


@router.get("/admin/usage-summary")
@cached(
    "admin:codex:usage-summary",
    ttl=settings.CACHE_TTL_ADMIN,
    scope_by=_ADMIN_CACHE_SCOPE,
    key_excludes=_ADMIN_CACHE_KEY_EXCLUDES,
)
async def codex_admin_usage_summary(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await _codex_admin_summary(db, days)


@router.get("/admin/usage-events")
@cached(
    "admin:codex:usage-events",
    ttl=settings.CACHE_TTL_ADMIN,
    scope_by=_ADMIN_CACHE_SCOPE,
    key_excludes=_ADMIN_CACHE_KEY_EXCLUDES,
)
async def codex_admin_usage_events(
    user_id: str | None = Query(None),
    kind: str | None = Query(None, pattern="^(api|mcp)?$"),
    days: int = Query(30, ge=1, le=365),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    cutoff = now_bjt() - timedelta(days=days)
    offset = (page - 1) * page_size
    items: list[dict[str, Any]] = []
    total = 0
    if kind in {None, "api"}:
        api_stmt = select(AuditLog).where(
            AuditLog.action.like("codex.%"),
            AuditLog.action != "codex.mcp.call",
            AuditLog.created_at >= cutoff,
        )
        api_count_stmt = select(func.count(AuditLog.id)).where(
            AuditLog.action.like("codex.%"),
            AuditLog.action != "codex.mcp.call",
            AuditLog.created_at >= cutoff,
        )
        if user_id:
            api_stmt = api_stmt.where(AuditLog.user_id == user_id)
            api_count_stmt = api_count_stmt.where(AuditLog.user_id == user_id)
        api_rows = (await db.execute(api_stmt.order_by(AuditLog.created_at.desc()).limit(500))).scalars().all()
        total += int((await db.execute(api_count_stmt)).scalar() or 0)
        items.extend(_codex_event_from_audit(row) for row in api_rows)
    if kind in {None, "mcp"}:
        mcp_stmt = select(CodexMcpCallAudit).where(CodexMcpCallAudit.created_at >= cutoff)
        mcp_count_stmt = select(func.count(CodexMcpCallAudit.id)).where(CodexMcpCallAudit.created_at >= cutoff)
        if user_id:
            mcp_stmt = mcp_stmt.where(CodexMcpCallAudit.user_id == user_id)
            mcp_count_stmt = mcp_count_stmt.where(CodexMcpCallAudit.user_id == user_id)
        mcp_rows = (await db.execute(mcp_stmt.order_by(CodexMcpCallAudit.created_at.desc()).limit(500))).scalars().all()
        total += int((await db.execute(mcp_count_stmt)).scalar() or 0)
        items.extend(_codex_event_from_mcp(row) for row in mcp_rows)
    user_ids = sorted({str(item.get("user_id")) for item in items if item.get("user_id")})
    user_map = (
        _user_display_map(
            list((await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all())
        )
        if user_ids
        else {}
    )
    items = [_attach_user_display(item, user_map) for item in items]
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items[offset : offset + page_size],
    }


@router.get("/admin/users")
@cached(
    "admin:codex:users",
    ttl=settings.CACHE_TTL_ADMIN,
    scope_by=_ADMIN_CACHE_SCOPE,
    key_excludes=_ADMIN_CACHE_KEY_EXCLUDES,
)
async def codex_admin_users(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    min_activity_at = datetime.min
    session_rows = (
        await db.execute(
            select(
                CodexCliSession.user_id,
                func.count(CodexCliSession.id).label("session_count"),
                func.sum(
                    case((CodexCliSession.revoked_at.is_(None) & (CodexCliSession.expires_at > now_bjt()), 1), else_=0)
                ).label("open_sessions"),
                func.max(func.coalesce(CodexCliSession.last_seen_at, CodexCliSession.created_at)).label("last_session_at"),
            )
            .group_by(CodexCliSession.user_id)
        )
    ).all()
    mcp_rows = (
        await db.execute(
            select(
                CodexMcpCallAudit.user_id,
                func.count(CodexMcpCallAudit.id).label("mcp_call_count"),
                func.max(CodexMcpCallAudit.created_at).label("last_mcp_at"),
            ).group_by(CodexMcpCallAudit.user_id)
        )
    ).all()
    by_user: dict[str, dict[str, Any]] = {}
    for user_id, session_count, open_sessions, last_session_at in session_rows:
        by_user[user_id] = {
            "user_id": user_id,
            "session_count": int(session_count or 0),
            "open_sessions": int(open_sessions or 0),
            "mcp_call_count": 0,
            "last_codex_activity_at": last_session_at,
        }
    for user_id, mcp_call_count, last_mcp_at in mcp_rows:
        item = by_user.setdefault(
            user_id,
            {
                "user_id": user_id,
                "session_count": 0,
                "open_sessions": 0,
                "mcp_call_count": 0,
                "last_codex_activity_at": None,
            },
        )
        item["mcp_call_count"] = int(mcp_call_count or 0)
        if last_mcp_at and (not item["last_codex_activity_at"] or last_mcp_at > item["last_codex_activity_at"]):
            item["last_codex_activity_at"] = last_mcp_at
    user_ids = sorted({str(user_id) for user_id in by_user if user_id})
    user_map = (
        _user_display_map(
            list((await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all())
        )
        if user_ids
        else {}
    )
    return {
        "items": [
            _attach_user_display(
                {
                    **item,
                    "last_codex_activity_at": isoformat_bjt(item["last_codex_activity_at"]) if item["last_codex_activity_at"] else None,
                },
                user_map,
            )
            for item in sorted(by_user.values(), key=lambda value: value.get("last_codex_activity_at") or min_activity_at, reverse=True)
        ]
    }


@router.get("/capabilities")
async def user_capabilities(
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
):
    await record_codex_cli_usage(request, principal, action="codex.capabilities")
    user = principal.user
    can_create = role_matches_any(user, ("admin", "ai_engineer", "biz_owner", "aibp"))
    return {
        "user": serialize_user(user),
        "token": {"ttl_days": service.CLI_SESSION_DAYS, "checked_on_every_start": True},
        "skill": {
            "can_create": can_create,
            "can_view_all": bool(getattr(user, "can_view_all", False)),
            "can_use_real_mcp": True,
            "can_submit_review": can_create,
        },
        "project": {
            "can_submit": can_create,
            "host": "/projects",
            "submit_api": "/api/codex/projects/upload",
            "auto_register_api": "/api/codex/projects/auto-register",
            "status_api": "/api/codex/projects/{project_id}/status",
            "logs_api": "/api/codex/projects/runs/{project_run_id}/logs",
            "asset_upload_api": "/api/codex/projects/runs/{project_run_id}/assets",
            "asset_list_api": "/api/codex/projects/runs/{project_run_id}/assets",
            "service_invoke_api": "/api/codex/projects/{project_id}/service/invoke",
            "org_resolve_api": "/api/codex/projects/org/resolve",
            "spec_command": "sf project spec --recipe short-video-analysis --json",
            "doctor_command": "sf project doctor --recipe short-video-analysis --path . --json",
            "verify_command": "sf project verify <project_id> --recipe short-video-analysis --json",
            "runtime": {"containerized": False, "gateway": "SkillForge Project Gateway"},
            **_codex_project_generation_contract(),
        },
        "platform": {
            "todos": {"create": True, "source": "skill_output.todos"},
            "performance_reports": {"create": True, "source": "skill_output.reports"},
            "output_previews": {"create": True, "source": "sf preview <file>"},
            "mcp": {"server": "skillforge", "credential_location": "platform_only"},
            "api": {"base_url": str(settings.PUBLIC_BASE_URL).rstrip("/")},
        },
    }


@router.get("/mcp-catalog")
async def mcp_catalog(
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
):
    await record_codex_cli_usage(request, principal, action="codex.mcp.catalog")
    return service.mcp_catalog_for_user(principal.user)


@router.get("/platform/capabilities")
async def platform_capabilities(
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
):
    await record_codex_cli_usage(request, principal, action="codex.platform.capabilities")
    return {
        "version": 1,
        "features": [
            {"id": "todos", "name": "待办", "api": "/api/todos", "available_to_codex_skill": True},
            {"id": "performance_reports", "name": "性能报告", "api": "/api/inbox", "available_to_codex_skill": True},
            {"id": "output_previews", "name": "输出结果预览", "api": "/api/codex/previews", "available_to_codex_skill": True},
            {"id": "mcp_gateway", "name": "真实 MCP 网关", "api": "/api/codex/mcp/call", "available_to_codex_skill": True},
            {"id": "skill_submit", "name": "Skill 提交审核", "api": "/api/codex/skills/submissions", "available_to_codex_skill": True},
            {"id": "project_host", "name": "项目宿主", "api": "/api/codex/projects/upload", "available_to_codex_project": True},
            {
                "id": "project_service_invoke",
                "name": "上传项目 API 服务调用",
                "api": "/api/codex/projects/{project_id}/service/invoke",
                "available_to_codex_project": True,
            },
            {
                "id": "project_run_asset_upload",
                "name": "项目运行资产上传",
                "api": "/api/codex/projects/runs/{project_run_id}/assets",
                "available_to_codex_project": True,
            },
            {
                "id": "project_generation_spec",
                "name": "项目生成标准",
                "api": "sf project spec --recipe <recipe>",
                "available_to_codex_project": True,
            },
            {
                "id": "project_org_resolve",
                "name": "项目组织权限解析",
                "api": "/api/codex/projects/org/resolve",
                "available_to_codex_project": True,
            },
            {
                "id": "project_recipe_verify",
                "name": "项目配方验证",
                "api": "sf project verify <project_id> --recipe <recipe>",
                "available_to_codex_project": True,
            },
            {
                "id": "analysis_agent_blueprint",
                "name": "部门业务分析 Agent",
                "api": "/api/codex/agents/analysis",
                "available_to_codex_skill": True,
            },
            {
                "id": "cloud_video_weekly_diagnosis_skill_template",
                "name": "云视频周度采集 Skill 模板",
                "api": "sf skill init --template cloud-video-weekly-diagnosis",
                "available_to_codex_skill": True,
            },
        ],
    }


@router.get("/agents/analysis")
async def codex_list_analysis_agents(
    request: Request,
    department: str = Query(default=""),
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.agent.analysis.list")
    return await list_business_analysis_agents(
        request=request,
        department=department,
        current_user=principal.user,
        db=db,
    )


@router.post("/agents/analysis")
async def codex_upsert_analysis_agent(
    request: Request,
    body: AnalysisAgentUpsertRequest,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.agent.analysis.upsert",
        target_type="department_analysis_agent",
        target_id=body.id or body.name,
        detail={"department": body.department, "skill_id": body.skill_id},
    )
    return await upsert_business_analysis_agent(
        request=request,
        body=body,
        current_user=principal.user,
        db=db,
    )


@router.post("/agents/analysis/{agent_id}/validate")
async def codex_validate_analysis_agent(
    request: Request,
    agent_id: str,
    body: AnalysisAgentValidateRequest,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.agent.analysis.validate",
        target_type="department_analysis_agent",
        target_id=agent_id,
    )
    return await validate_business_analysis_agent(
        agent_id=agent_id,
        body=body,
        current_user=principal.user,
        db=db,
    )


def _project_can_create_or_edit(user: User) -> bool:
    return role_matches_any(user, ("admin", "system_admin", "dept_admin", "aibp", "ai_engineer", "biz_owner"))


async def _resolve_visible_org_units(db: AsyncSession, user: User, *, department: str, department_id: str) -> list[OrgUnit]:
    accessible = await get_accessible_departments(db, user)
    conditions = []
    if department_id:
        conditions.append(OrgUnit.id == department_id)
    if department:
        pattern = f"%{department}%"
        conditions.append(OrgUnit.name.ilike(pattern))
    if not conditions:
        return []
    stmt = select(OrgUnit).where(or_(*conditions))
    if accessible is not None and not accessible:
        return []
    if accessible is not None:
        stmt = stmt.where(OrgUnit.id.in_(list(accessible)))
    stmt = stmt.order_by(OrgUnit.sort_order.asc(), OrgUnit.name.asc(), OrgUnit.id.asc()).limit(10)
    return list((await db.execute(stmt)).scalars().all())


async def _resolve_project_owner(db: AsyncSession, current_user: User, owner: str, department_id: str | None) -> dict[str, Any] | None:
    owner = owner.strip()
    if not owner:
        return None
    pattern = f"%{owner}%"
    stmt = (
        select(User)
        .where(
            User.state == "active",
            User.is_active == True,  # noqa: E712
            or_(
                User.id.ilike(pattern),
                User.name.ilike(pattern),
                User.username.ilike(pattern),
                User.dingtalk_user_id.ilike(pattern),
            ),
        )
        .order_by(User.name.asc(), User.username.asc())
        .limit(10)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if department_id:
        member_ids = set(
            (
                await db.execute(
                    select(UserOrgMembership.user_id).where(UserOrgMembership.org_unit_id == department_id)
                )
            ).scalars().all()
        )
        dept_rows = [row for row in rows if row.id in member_ids]
        rows = dept_rows
    if not rows:
        return None
    orgs_by_user = await service._member_orgs_for_users(db, rows)  # type: ignore[attr-defined]
    item = service._serialize_org_user(rows[0], orgs_by_user)  # type: ignore[attr-defined]
    item["can_edit_as_owner"] = True
    item["matched_count"] = len(rows)
    return item


@router.post("/projects/org/resolve")
async def codex_project_org_resolve(
    request: Request,
    body: ProjectOrgResolveRequest,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.project.org.resolve")
    user = principal.user
    visibility = (body.visibility or "").strip() or "department"
    if visibility not in {"company", "department", "private"}:
        raise AppError("PROJECT_INVALID", 400, {"field": "visibility", "allowed": ["company", "department", "private"]})
    org_units = await _resolve_visible_org_units(
        db,
        user,
        department=(body.department or "").strip(),
        department_id=(body.department_id or "").strip(),
    )
    selected = org_units[0] if org_units else None
    owner = await _resolve_project_owner(db, user, (body.owner or "").strip(), selected.id if selected else None)
    can_submit = _project_can_create_or_edit(user)
    can_use_department = bool(selected and await can_access_department(db, user, selected.id))
    return {
        "ok": bool(selected and can_submit and (visibility != "department" or can_use_department)),
        "visibility": visibility,
        "department": (
            {"id": selected.id, "name": selected.name, "type": selected.type, "path": selected.path}
            if selected
            else None
        ),
        "department_candidates": [
            {"id": item.id, "name": item.name, "type": item.type, "path": item.path}
            for item in org_units
        ],
        "owner": owner,
        "permissions": {
            "current_user_can_submit_project": can_submit,
            "current_user_can_use_department": can_use_department,
            "owner_will_have_edit": bool(owner),
        },
        "manifest_defaults": {
            "department_id": selected.id if selected else None,
            "department": selected.name if selected else None,
            "visibility": visibility,
            "owner": owner.get("user_id") if isinstance(owner, dict) else None,
        },
    }


@router.get("/editor/capabilities")
async def editor_capabilities(
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
):
    await record_codex_cli_usage(request, principal, action="codex.editor.capabilities")
    return {
        "version": 1,
        "skill_definition": {
            "required_files": ["SKILL.md"],
            "allowed_top_level_files": sorted(service.ALLOWED_PACKAGE_EXACT),
            "allowed_directories": list(service.ALLOWED_PACKAGE_DIRS),
            "required_frontmatter": ["name", "description", "trigger_type", "risk_level"],
            "valid_trigger_type": ["manual", "cron", "event"],
            "valid_risk_level": ["R1", "R2", "R3", "R4"],
        },
        "output_contract": {
            "todos": "Skill 输出 output.todos，由平台生成待办和分发",
            "reports": "Skill 输出 output.reports，由平台生成报告/性能摘要",
            "data_provenance": "SDK 自动附带 MCP proof 与 data_proofs",
        },
        "local_debug": {
            "mcp_scheme": "mcp://<tool_name>",
            "gateway_env": ["SKILLFORGE_PLATFORM_URL", "SKILLFORGE_RUN_TOKEN", "SKILLFORGE_RUN_ID"],
            "credential_location": "platform_only",
            "preview_command": "sf preview output.json",
        },
    }


def _sf_command_groups() -> list[dict[str, Any]]:
    return [
        {
            "title": "安装与更新",
            "items": [
                {"command": "/sf update", "description": "检查版本、校验 sha256、替换本地插件，并写入 Codex 插件市场配置和 ~/.local/bin/sf"},
                {"command": "/sf update --check", "description": "只检查 manifest 最新版本、当前版本和是否需要更新"},
                {"command": "/sf plugin status", "description": "查看本机 Codex 插件安装状态、版本、manifest、shim 和更新可用性"},
                {"command": "/sf plugin doctor --fix", "description": "诊断本机插件、登录、MCP 和 Project API，并修复 marketplace、Codex config 与 sf shim"},
                {"command": "/sf 我的插件", "description": "同时查看本机插件状态和当前账号拥有/可编辑的 Skill"},
                {"command": "/sf catalog refresh", "description": "刷新本地缓存的 SkillForge Codex manifest"},
            ],
        },
        {
            "title": "登录与账号",
            "items": [
                {"command": "/sf 登录", "description": "打开浏览器完成 SkillForge 授权，生成 30 天命令行令牌"},
                {"command": "/sf 我是谁", "description": "检查当前登录账号、权限版本和令牌有效期"},
                {"command": "sf auth logout", "description": "撤销当前命令行令牌并清理本地登录缓存"},
            ],
        },
        {
            "title": "我的 Skill / 部门 Skill",
            "items": [
                {"command": "/sf 查看哪些技能", "description": "列出当前账号按统一权限可见的 Skill"},
                {"command": "/sf 我的技能", "description": "列出当前账号拥有或可编辑的 Skill"},
                {"command": "/sf 我的部门插件", "description": "列出当前账号可访问部门下的 Skill，权限范围由 SkillForge 统一计算"},
                {"command": "sf skill install <skill_id> --path ./skills", "description": "下载安装到本地工作区；只允许下载当前账号可读的 Skill"},
                {"command": "/sf 查看我能编辑哪些技能", "description": "列出当前账号可编辑的 Skill"},
                {"command": "/sf 哪些可发布", "description": "列出当前账号可提交审核、审核或发布的 Skill"},
                {"command": "/sf 哪些未上传", "description": "扫描本地 Skill 包并与平台 Git 版本对比"},
                {"command": "/sf 当前目录有没有上传", "description": "检查当前目录 Skill 的 package_hash/base_commit 与平台记录是否一致"},
            ],
        },
        {
            "title": "MCP 与组织推送",
            "items": [
                {"command": "/sf mcp", "description": "查看当前账号可用的 SkillForge MCP 工具目录"},
                {"command": "/sf agent coverage", "description": "查看当前账号可见部门的执行/分析/训练 Agent 覆盖度和平台兜底状态"},
                {"command": "/sf agent analysis list --department \"示例品牌内容电商运营部\"", "description": "查看部门业务分析 Agent blueprint，包含绑定 Skill、prompt 版本、分析维度和编辑人"},
                {"command": "/sf agent analysis create --name \"示例品牌同主题视频消耗诊断 Agent\" --department \"示例品牌内容电商运营部\" --skill-id samplebrand-weekly-video-diagnosis --owner \"祁莹莹\" --prompt-version analysis_v1", "description": "创建/更新部门业务分析 Agent，让祁莹莹可管理默认分析维度和提示词版本"},
                {"command": "/sf mcp call skillforge_org_search_users --args '{\"query\":\"姓名\",\"limit\":5}'", "description": "通过 MCP 查询当前账号可见的组织成员"},
                {"command": "/sf mcp call skillforge_org_list_members --args '{\"department\":\"部门名\",\"limit\":20}'", "description": "按部门列出当前账号可见的组织成员"},
                {"command": "/sf mcp call skillforge_dingtalk_send_work_notice --args '{\"user_ids\":[\"u1\"],\"title\":\"标题\",\"markdown\":\"正文\"}'", "description": "通过平台 outbox dry-run 钉钉工作通知；真实入队需加 --real --idempotency-key"},
                {"command": "/sf mcp call skillforge_cloud_video_accounts --args '{\"max_people\":20}'", "description": "读取云视频团队-分组-人员树，拿到可用于报表 idStr 的账号 ID"},
                {"command": "/sf mcp call skillforge_cloud_video_daily_person_video_report --args '{\"start_date\":\"2026-05-01\",\"end_date\":\"2026-06-09\",\"top_videos_per_person\":5}'", "description": "按天读取每个人消耗和视频消耗明细，供短视频项目生成个人改进方案"},
                {"command": "/sf mcp call skillforge_cloud_video_material_report --args '{\"start_date\":\"2026-05-11\",\"end_date\":\"2026-06-09\",\"search_type\":1}'", "description": "读取云视频广告平台分析页素材统计，按上传人/分组/团队核对素材数、消耗和标签口径"},
                {"command": "/sf mcp call skillforge_cloud_video_video_usage_report --args '{\"start_date\":\"2026-05-11\",\"end_date\":\"2026-06-09\",\"data_type\":3,\"ids\":[\"4016\"]}'", "description": "读取云视频视频统计报表，按团队/分组/个人核对上传条数和使用统计"},
                {"command": "/sf mcp call skillforge_cloud_video_audit_rejects --args '{\"video_id\":\"100715117\",\"platform_type\":2}'", "description": "读取云视频单条视频的卡审拒因，用于汇总卡审原因和改进建议"},
                {"command": "/sf mcp call skillforge_cloud_video_videos --args '{\"search\":\"示例品牌\",\"page_size\":5}'", "description": "搜索云视频素材库元数据，默认不返回视频/封面原始 URL"},
                {"command": "sf ai analyze --prompt '分析这次运行结果' --context-json '{...}'", "description": "通过平台 AI 调用 DeepSeek V4 Pro 1M 上下文，密钥只在服务端后台配置中使用"},
                {"command": "sf raw query --source decision_logs --skill-id <skill_id> --limit 20", "description": "按 Skill 权限查询运行后的原始记录，input/output 会脱敏返回"},
                {"command": "sf run analyze --run-id <execution_run_id> --include-raw", "description": "一键读取某次 Skill 运行数据并交给平台 AI 复盘，形成可执行问题诊断"},
                {"command": "sf run candidate --run-id <execution_run_id>", "description": "把脱敏运行复盘沉淀为待审批训练候选任务"},
                {"command": "sf training resources", "description": "查看当前账号可见训练 Agent/GPU 资源、平台兜底和运行中训练摘要"},
                {"command": "sf training datasets", "description": "查看当前账号可见 Skill 的训练数据资产门禁，不返回原始 DecisionLog 正文"},
                {"command": "sf training candidate <skill_id>", "description": "基于通过门禁的 Skill 历史数据资产生成待审批训练候选"},
                {"command": "sf training jobs --status running", "description": "查看训练任务的模型、路由、进度、预计耗时和目标网关"},
                {"command": "sf training create --title '训练新模型' --target-skill-id <skill_id>", "description": "创建待审批训练任务，可指定数据、模型、参数和目标训练 Agent"},
                {"command": "sf training dispatch <training_job_id>", "description": "按训练权限触发任务下发；审批、同步、评估、取消也走 sf training approve/collect/evaluate/cancel"},
                {"command": "sf training collect-due --gateway <training_gateway_id>", "description": "批量补偿同步到期训练任务结果，可限定当前训练 Agent 网关"},
                {"command": "sf training deploy-request <training_job_id> --target-skill-id <skill_id>", "description": "对评估通过的训练任务发起模型部署审批"},
                {"command": "sf training deployments", "description": "查看模型部署注册表、灰度比例、artifact hash 和目标 Skill"},
                {"command": "sf training deployment-approve <deployment_id>", "description": "按训练部署权限审批模型部署；驳回/激活/回滚走 deployment-reject/deployment-activate/deployment-rollback"},
                {"command": "sf mcp stdio", "description": "给 Codex MCP 配置使用的本地 stdio 代理，真实调用仍走 SkillForge Gateway"},
                {"command": "/sf mcp call skillforge_org_search_users --args '{\"query\":\"姓名\",\"limit\":5}'", "description": "通过 MCP 查询平台组织成员，未命中时兜底查钉钉通讯录"},
                {"command": "/sf mcp call skillforge_org_list_members --args '{\"department\":\"部门名\",\"limit\":20}'", "description": "按部门列出当前账号可见的组织成员"},
                {"command": "/sf mcp call skillforge_dingtalk_send_work_notice --args '{\"user_ids\":[\"u1\"],\"title\":\"标题\",\"markdown\":\"正文\"}'", "description": "通过平台 outbox dry-run 钉钉工作通知；真实入队需加 --real --idempotency-key"},
                {"command": "/sf mcp call skillforge_data_capability_list --args '{}'", "description": "列出 Tmall、语艺等平台缓存数据能力"},
                {"command": "/sf mcp call skillforge_sf_data_write --args '{\"namespace\":\"demo\",\"data\":{\"hello\":\"world\"}}' --real --idempotency-key <key>", "description": "向通用 SF 数据库写入 JSON/文本/表格数据，真实写入必须带幂等键"},
                {"command": "/sf mcp call skillforge_sf_data_list --args '{\"namespace\":\"demo\"}'", "description": "查询已写入的通用 SF 数据记录"},
                {"command": "/sf mcp call skillforge_sf_data_get --args '{\"id\":\"sfdata_xxx\"}'", "description": "读取单条通用 SF 数据记录详情"},
                {"command": "/sf mcp call skillforge_samplebrand_cloud_video_data_latest --args '{}'", "description": "查询示例品牌云视频周度全量采集最近一次原始数据摘要"},
                {"command": "/sf mcp call skillforge_tmall_link_decline_data_latest --args '{}'", "description": "查询天猫链接下滑每日采集缓存的最近一次原始数据摘要"},
                {"command": "/sf mcp call skillforge_yuyidata_customer_service_data_latest --args '{}'", "description": "查询语艺客服会话缓存的最近一次原始数据摘要"},
                {"command": "/sf mcp call skillforge_execution_artifact_latest --args '{\"skill_id\":\"tmall-link-decline-collector-v1\"}'", "description": "查询某个 Skill 最近一次平台保存的原始数据 Artifact 摘要、大小和 sha256"},
                {"command": "/sf mcp call skillforge_cloud_video_daily_person_video_report --args '{\"start_date\":\"2026-05-01\",\"end_date\":\"2026-06-09\",\"include_daily_rows\":false}'", "description": "读取 2026 年 5-6 月人员消耗、Top 花费视频和低 ROI 视频"},
                {"command": "sf mcp stdio", "description": "给 Codex MCP 配置使用的本地 stdio 代理，真实调用仍走 SkillForge 网关"},
            ],
        },
        {
            "title": "缓存数据与通知",
            "items": [
                {"command": "/sf data list", "description": "列出当前账号可读取的 Tmall、语艺等平台缓存数据能力"},
                {"command": "/sf data write --namespace demo --json '{\"hello\":\"world\"}' --real --idempotency-key <key>", "description": "写入通用 SF 数据库，支持 JSON、文本、表格和大型 artifact 引用"},
                {"command": "/sf data records --namespace demo", "description": "列出通用 SF 数据库中的记录"},
                {"command": "/sf data record <sfdata_id>", "description": "查看单条通用 SF 数据记录详情"},
                {"command": "/sf data latest tmall", "description": "查看天猫链接下滑最近一次缓存原始数据摘要和 Artifact 引用"},
                {"command": "/sf data latest yuyi", "description": "查看语艺客服会话最近一次缓存原始数据摘要和 Artifact 引用"},
                {"command": "/sf data get tmall --include-content --max-bytes 5242880", "description": "在权限和大小限制内读取已缓存原始 JSON，不重新下载外部平台"},
                {"command": "/sf notify users --query 示例成员甲", "description": "查询当前账号可见的组织/钉钉接收人"},
                {"command": "/sf notify send --title 标题 --markdown 正文 --query 示例成员4", "description": "dry-run 钉钉工作通知；真实入队需加 --real --idempotency-key"},
            ],
        },
        {
            "title": "运行与结果",
            "items": [
                {"command": "/sf run <skill_id> --params '{\"dry_run\":false}'", "description": "按当前账号 execute 权限触发受控 Skill 运行"},
                {"command": "/sf runs list --skill-id <skill_id>", "description": "列出当前账号可读 Skill 的执行记录"},
                {"command": "/sf runs status <run_id>", "description": "查看单次执行状态、进度、来源节点和元数据"},
                {"command": "/sf runs result <run_id>", "description": "查看执行产生的报告、待办和 DecisionLog 输出"},
                {"command": "/sf runs artifacts <run_id> --kind raw-output", "description": "查看原始输入/输出 Artifact 摘要，必要时受控读取内容"},
                {"command": "/sf runs logs <run_id>", "description": "查看平台保存且已脱敏的运行日志尾部"},
                {"command": "/sf runs diagnose <run_id>", "description": "对失败执行生成诊断结果"},
            ],
        },
        {
            "title": "开发、测试与审核",
            "items": [
                {"command": "/sf doctor", "description": "检查当前目录 Skill 结构、元数据和包规则"},
                {"command": "/sf doctor --remote", "description": "同时检查当前包、平台发布状态、节点定时、登录和 MCP 目录"},
                {"command": "/sf test", "description": "本地执行当前目录 Skill，优先使用 fixtures/sample_input.json"},
                {"command": "/sf test-real --shop-id <id>", "description": "创建调试运行令牌，并通过 SkillForge 网关调用真实 MCP"},
                {"command": "/sf sandbox", "description": "以 sandbox run_mode 执行当前目录 Skill 的真实网关调试"},
                {"command": "sf skill init <skill_id>", "description": "创建本地 Skill 脚手架，包含 SKILL.md、skillforge.yaml、入口脚本、样例输入和测试"},
                {"command": "sf skill init samplebrand-weekly-video-diagnosis --template cloud-video-weekly-diagnosis --department \"示例品牌内容电商运营部\" --trigger-type cron --cron '0 8 * * *'", "description": "生成云视频周度同主题消耗诊断 Skill，内置 MCP 数据读取、analysis_v1 prompt、报告/待办和钉钉推送配置"},
                {"command": "sf skill diff --path .", "description": "对比本地 Skill 包和平台当前可读包的文本差异"},
                {"command": "sf skill publish-status <skill_id>", "description": "查看最新审核、Git 版本、节点同步、定时配置和最近运行"},
                {"command": "sf preview output.json", "description": "把本地 Skill 输出上传成只读预览页，返回 URL 供业务先核对报告和待办"},
                {"command": "sf preview apply <preview_id>", "description": "从预览输出 dry-run 生成正式报告/待办；真实写入必须加 --real --idempotency-key"},
                {"command": "sf output validate output.json", "description": "校验输出是否符合 reports/todos/actions/notifications 合约和大小目标"},
                {"command": "/sf submit", "description": "打包当前目录 Skill，提交到 SkillForge Git 与审核链路"},
                {"command": "/sf review <submission_id>", "description": "查看 Codex 提交单和关联 Review 状态"},
                {"command": "sf review list", "description": "列出当前账号可见的审核记录，可按状态、Skill、提交人或审核人过滤"},
                {"command": "sf review comment <review_id> --content \"意见\"", "description": "给审核记录添加评论或代码行意见"},
                {"command": "sf review request-changes <review_id> --reason \"原因\"", "description": "通过审核状态机要求修改，不直接改发布态"},
                {"command": "sf review approve <review_id>", "description": "有权限时批准审核并触发正常发布流程"},
                {"command": "sf submission approve <submission_id>", "description": "有审核权限时通过 SkillForge 状态机批准提交，不绕过审核链"},
            ],
        },
        {
            "title": "项目宿主",
            "items": [
                {"command": "/sf project spec --recipe short-video-analysis", "description": "输出 Project Host contract、Gateway SDK、运行资产 metadata 和短视频分析配方"},
                {"command": "/sf project org resolve --department \"部门\" --owner \"姓名\"", "description": "在写 projectforge.yaml 前解析部门、owner 和当前账号项目权限"},
                {"command": "/sf project init", "description": "在当前目录生成 projectforge.yaml 与网页 Project Gateway 示例"},
                {"command": "/sf project doctor", "description": "校验项目入口、能力声明和输出 contract；短视频项目可加 --recipe short-video-analysis"},
                {"command": "/sf project submit", "description": "通过 Codex CLI token 上传/登记到平台项目宿主，项目页点击即用"},
                {"command": "/sf project verify <project_id> --recipe short-video-analysis", "description": "调用上传项目服务并读取 logs/trace，验证输入、能力调用、输出和 AI 分析闭环"},
                {"command": "/sf project status <project_id>", "description": "查看项目版本、入口、最近运行和 AI 状态"},
                {"command": "/sf project invoke <project_id> --input-json '{...}'", "description": "把上传项目作为平台 API 服务调用，输入、输出、能力调用进入 Project Gateway Trace"},
                {"command": "/sf project asset upload <project_run_id> <file>", "description": "上传原视频、关键帧或数据文件到 ProjectRunAsset，metadata 支持 material_a/material_b 与 visual_frame"},
                {"command": "/sf project asset list <project_run_id>", "description": "列出当前 run 已上传的运行资产，确认视频/关键帧是否进入 Trace"},
                {"command": "/sf project logs <project_run_id>", "description": "查看项目运行脱敏输入、输出、运行资产、能力调用和 Gateway Trace"},
            ],
        },
        {
            "title": "节点定时",
            "items": [
                {"command": "sf schedule get <skill_id>", "description": "查看 Skill 的 trigger_type、trigger_expression 和节点 schedule 快照"},
                {"command": "sf schedule set <skill_id> --cron '50 7 * * *'", "description": "通过平台更新 cron 并向节点下发 schedule，不让中心调度替代节点主路径"},
                {"command": "sf schedule stop <skill_id>", "description": "停止定时：改为 manual、清空 cron、向节点下发空 schedule 快照，历史 run 保留"},
                {"command": "sf schedule sync <skill_id>", "description": "重新向目标节点同步当前 schedule 快照"},
            ],
        },
        {
            "title": "共享市场",
            "items": [
                {"command": "sf market list", "description": "列出当前账号可见的共享 Skill"},
                {"command": "sf market detail <skill_id>", "description": "查看共享 Skill 基础信息、可见性和版本"},
                {"command": "sf market install <skill_id> --path ./skills", "description": "下载安装到本地工作区，仍按 read 权限控制"},
                {"command": "sf market metrics <skill_id>", "description": "查看复用次数、执行次数、使用部门和 fork 统计"},
                {"command": "sf market certify <skill_id>", "description": "提交共享 Skill 认证申请"},
                {"command": "sf market visibility <skill_id> company_public", "description": "有发布权限时调整市场可见状态"},
                {"command": "sf market ratings <skill_id>", "description": "查看共享 Skill 评分"},
                {"command": "sf market rate <skill_id> --rating 5 --comment \"稳定好用\"", "description": "给共享 Skill 打分并反馈"},
            ],
        },
        {
            "title": "流水线",
            "items": [
                {"command": "sf pipeline run --path .", "description": "执行本地 doctor 检查并输出步骤结果"},
                {"command": "sf pipeline run --path . --remote", "description": "本地检查后继续检查平台发布状态、定时和 MCP 能力"},
                {"command": "sf pipeline run --path . --remote --submit --message \"变更说明\"", "description": "检查通过后提交审核，失败时按 --keep-going 决定是否继续"},
            ],
        },
        {
            "title": "平台能力",
            "items": [
                {"command": "/sf 能力", "description": "查看 Skill 可用的平台待办、报告、MCP 网关与提交审核能力"},
                {"command": "sf platform capabilities", "description": "查看 SkillForge 平台面向 Codex Skill 的运行时能力"},
                {"command": "sf editor capabilities", "description": "查看 Skill 包结构、frontmatter 和本地调试约束"},
            ],
        },
    ]


def _flatten_sf_commands(groups: list[dict[str, Any]]) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for group in groups:
        for item in group.get("items") or []:
            if isinstance(item, dict):
                commands.append({"command": str(item.get("command") or ""), "description": str(item.get("description") or "")})
    return commands


MCP_TOOL_CAPABILITY_TEXT: dict[str, tuple[str, str]] = {
    "skillforge_agent_coverage": ("查询当前账号可见部门的执行、分析、训练 Agent 覆盖度、在线状态和平台默认 Agent 兜底状态。", "适合让 Codex/sf 判断部门是否缺执行 Agent、分析 Agent 或训练 Agent，并指导后续新建或调整用途。"),
    "skillforge_ai_analyze": ("通过平台 AI 配置调用 DeepSeek V4 Pro 1M 上下文模型做分析，不向本地暴露模型密钥。", "适合让 Codex/sf 对调试上下文、运行结果或原始数据做一次性分析；Skill 运行期仍应使用 sf.analyze()。"),
    "skillforge_cloud_video_accounts": ("读取云视频团队、分组和人员树，返回可用于报表 idStr 的 account_id。", "适合先定位示例品牌团队成员，再按人拉取消耗和视频表现。"),
    "skillforge_cloud_video_ad_report": ("读取云视频投放报表的个人或明细页，默认查询 2026-05-01 至 2026-06-09 巨量千川汇总。", "适合直接拿个人消耗排行或视频消耗明细，作为短视频分析项目的投放事实输入。"),
    "skillforge_cloud_video_categories": ("读取云视频素材库分类树，包含示例品牌及产品线分类。", "适合让 Codex/sf 构建素材筛选器、短视频分析输入和分类维度。"),
    "skillforge_cloud_video_daily_person_video_report": ("按天读取并聚合云视频人员消耗和视频消耗明细，输出 date -> person -> video 的分析数据集。", "适合短视频分析项目判断每个人每天的钱花在哪些视频上、差异视频是什么，再结合视频视觉分析生成个人改进方案。"),
    "skillforge_cloud_video_material_report": ("读取云视频广告平台分析页素材统计，支持按上传人、分组、团队和分类统计素材数、消耗与标签项。", "适合核对卡审率、首发/优质/低质素材数量等业务口径，和投放消耗明细分开使用。"),
    "skillforge_cloud_video_video_usage_report": ("读取云视频视频统计报表，支持按团队、分组或个人统计上传条数、上传人数、下载、推送、剪映和爆款视频数。", "适合核对总上传条数、团队上传趋势和素材使用统计，和素材列表当前可见条目分开使用。"),
    "skillforge_cloud_video_audit_rejects": ("按视频读取云视频平台审核拒因，支持巨量千川、巨量广告和腾讯ADQ。", "适合在卡审视频列表上抽样或批量汇总拒因，给出具体剪辑/合规改进建议。"),
    "skillforge_cloud_video_session": ("检查并刷新云视频服务端登录态；账号密码只保存在平台服务端配置中。", "适合验证云视频账号是否可用，不暴露 token、签名或密码。"),
    "skillforge_cloud_video_tags": ("按云视频素材分类读取标签树。", "适合把素材标签、产品分类和投放表现关联起来做诊断。"),
    "skillforge_cloud_video_videos": ("搜索或分页读取云视频素材元数据，默认不返回视频/封面原始 URL。", "适合按素材名、分类、标签、上传/拍摄/消耗日期找到要分析的视频候选。"),
    "skillforge_data_artifact_get": ("受控读取平台缓存的数据 Artifact；默认只返回摘要，显式 include_content=true 且未超 max_bytes 才返回解压 JSON。", "适合让分析 Skill 复用每日采集缓存，避免重复下载云视频、Tmall 或语艺原始数据。"),
    "skillforge_data_capability_latest": ("按数据能力 key 查询最近一次平台缓存原始 JSON Artifact 摘要、大小、sha256 和 run 引用。", "适合用 cloud_video.samplebrand_weekly.raw_collection、tmall.link_decline.raw_collection 或 yuyidata.customer_service.raw_collection 获取可分析的数据入口。"),
    "skillforge_data_capability_list": ("列出当前账号可读取的平台缓存数据能力，包括示例品牌云视频、Tmall 链接下滑和语艺客服会话数据。", "适合先发现有哪些已沉淀数据可被 sf 或 Skill 直接复用。"),
    "skillforge_dingtalk_send_work_notice": ("向指定组织成员发送钉钉工作通知，返回 dry-run 预览或真实 outbox 入队结果。", "适合把 Skill 产生的风险提醒、待办摘要、日报链接推送给负责人。"),
    "skillforge_samplebrand_cloud_video_data_get": ("受控读取示例品牌云视频周度全量采集缓存 JSON；默认只返回摘要，显式 include_content 才返回原文。", "适合后续分析控制 Skill 直接消费每天 08:00 自动采集的云视频原始数据。"),
    "skillforge_samplebrand_cloud_video_data_latest": ("读取示例品牌云视频周度全量采集 Skill 最近一次平台缓存原始数据摘要。", "适合快速定位最近一次云视频全量采集的 run 和 artifact。"),
    "skillforge_samplebrand_cloud_video_daily_analysis_input": ("从示例品牌云视频周度采集缓存中按日期过滤日诊断输入。", "适合分析 Skill 只读取某天的视频消耗行和关联视频元数据，避免拉取完整周度 artifact。"),
    "skillforge_execution_artifact_latest": ("按 skill_id 查询最近一次成功执行在平台保存的原始数据 Artifact 摘要、大小、sha256 和 run 引用。", "适合让 sf 复用 tmall-link-decline-collector-v1、Yuyi 等每日采集结果，避免重复下载外部平台数据。"),
    "skillforge_execution_artifact_summary": ("按 run_id 查询平台保存的 raw-input/raw-output Artifact 摘要、大小、sha256 和存储引用；默认不返回完整原文。", "适合排查某次执行原始 JSON 多大、schema 是什么、是否已经被平台归档。"),
    "skillforge_org_list_members": ("获取指定部门或组织单元下当前账号可见的成员列表、用户 ID、部门和钉钉可接收状态。", "适合在推送前确认接收人范围，或给 Skill 生成待办负责人候选。"),
    "skillforge_org_search_users": ("按姓名、账号、用户 ID、钉钉 userId、部门或组织单元搜索组织成员；平台用户未命中时兜底查询钉钉通讯录。", "适合快速找人、确认钉钉接收人 ID、把业务负责人映射到平台用户。"),
    "skillforge_qianchuan_video_content_analysis": ("读取千川素材分析-视频素材-推商品-点击里的单视频内容分析，包含互动时序整体点击次数、流失、点赞、评论、转发、关注、脚本文本和 benchmark 标签缺口。", "适合让短视频分析 Skill 对单条素材还原完整点击生命周期，判断开头、中段、结尾在哪些秒点流失或点击，并补齐内容层面的优化依据。"),
    "skillforge_raw_data_query": ("按权限查询 Skill 运行后的 execution_runs、steps、decision_log、collection proof 和 schema snapshot。", "适合用 sf/Codex 复盘某次运行、取原始 input/output 再交给平台 AI 分析；敏感字段会脱敏。"),
    "skillforge_run_analyze": ("一键读取某次 Skill 运行后的脱敏原始数据，并调用平台 DeepSeek V4 Pro 1M 上下文模型生成运行复盘。", "适合从 run_id 直接定位失败原因、数据缺口、proof 线索和下一步改进动作。"),
    "skillforge_sf_data_get": ("读取单条通用 SF 数据记录详情、预览、inline JSON 和 artifact 引用。", "适合在 Codex/sf 或前端 SF 页面复用已经写入平台的数据。"),
    "skillforge_sf_data_list": ("分页查询通用 SF 数据库，支持 namespace、Skill、Run、类型和关键词过滤。", "适合发现项目、调试或分析流程沉淀的数据记录。"),
    "skillforge_sf_data_write": ("向 SkillForge 通用数据存储写入 JSON、文本、表格或大型 artifact 引用，真实写入要求幂等键。", "适合让 Codex/sf 把分析输入、项目中间结果、业务样本或调试数据沉淀到平台并在 SF 页面展示。"),
    "skillforge_tmall_link_decline_data_get": ("受控读取天猫链接下滑每日采集缓存原始 JSON；默认只返回摘要，显式 include_content 才返回原文。", "适合 Operator 或其它分析 Skill 直接消费上午 collector 已保存数据。"),
    "skillforge_tmall_link_decline_data_latest": ("读取天猫链接下滑每日采集 Skill 最近一次平台缓存原始数据摘要。", "适合快速定位今天早上 7:50 等节点采集的 run 和 artifact。"),
    "skillforge_yuyidata_customer_service_data_get": ("受控读取语艺客服会话每日缓存原始 JSON；默认只返回摘要，显式 include_content 才返回原文。", "适合需求分析、客服问题 TopN 和日报 Skill 复用语艺每日下载数据。"),
    "skillforge_yuyidata_customer_service_data_latest": ("读取语艺客服会话每日数据 Skill 最近一次平台缓存原始数据摘要。", "适合先拿到语艺已缓存数据的大小、schema、sha256 和 run 引用。"),
    "tmall_alimama_item_promotion": ("获取单个商品在阿里妈妈关键词推广或人群推广中的计划列表、计划状态和基础投放数据。", "适合查看某个商品当前有哪些推广计划在跑，辅助判断付费流量变化。"),
    "tmall_alimama_item_promotion_compare": ("获取商品关键词/人群推广当前期和上一期计划级数据，并汇总 ROI、PPC、点击、转化环比。", "适合分析推广效果下滑、花费异常、转化变差和环比波动原因。"),
    "tmall_item_activity_price_check": ("组合获取活动状态、价格风险和五星价格力检查结果。", "适合发现活动价、优惠叠加、价格力不足导致的链接转化或流量风险。"),
    "tmall_item_flow_required_metrics": ("获取商品 360 流量来源里搜索免费、搜索成交、无界关键词推广等运营必看指标及环比。", "适合链接下滑归因，判断免费流还是付费流出问题。"),
    "tmall_item_flow_required_metrics_batch": ("批量获取多个商品的关键流量来源指标和环比。", "适合对一批链接做巡检、排行或异常筛选。"),
    "tmall_item_promotion_required_metrics": ("获取阿里妈妈关键词推广底部合计，包括花费、直接成交金额、ROI、点击转化率、PPC、CTR 及环比。", "适合按运营待办口径检查推广是否花费升高、ROI 下降或点击转化异常。"),
    "tmall_item_review_qa_check": ("检查商品是否存在问大家、差评置顶、负向评价等评价风险信号。", "适合判断评价舆情是否影响转化，生成客服/内容优化待办。"),
    "tmall_item_reviews": ("采集商品 TopN 评价、问大家和风险词，失败时降级到页面采集。", "适合做评价文本分析、负面问题归类和卖点/痛点提取。"),
    "tmall_item_reviews_api": ("通过天猫前端 MTOP API 直接获取评价列表和问大家内容。", "适合在不需要 DOM 解析时更稳定地拉取评价原文。"),
    "tmall_link_decline_required_context": ("一次返回链接下滑分析所需的免费流、付费流、活动价格、评价风险等上下文。", "适合快速生成完整链接下滑诊断报告。"),
    "tmall_seller_marketing_activity_list": ("获取商家中心营销活动列表及生效、暂停、结束状态。", "适合检查商品或店铺活动是否缺失、暂停或结束。"),
    "tmall_seller_price_competitiveness_check": ("获取价格竞争力/五星价格力、建议价、高价限流和流量诊断。", "适合判断价格力是否拖累搜索、推荐和转化。"),
    "tmall_seller_price_risk_check": ("获取 0 元订单、超低价订单、活动范围不一致、优惠力度过大等价格风险。", "适合预警营销配置错误和价格安全问题。"),
    "tmall_store_weekly_snapshot": ("汇总店铺周度经营快照，覆盖排行、流量、活动价格、评价和风险概览。", "适合生成周报、例会材料和店铺健康巡检。"),
    "tmall_sycm_activity_price": ("获取生意参谋活动列表、活动榜单、价格风险和商品价格提醒。", "适合核对活动表现、价格风险和商品级价格提醒。"),
    "tmall_sycm_item_360_metrics": ("获取商品 360 的流量来源指标，包括免费搜索、付费推广、成交、转化和环比。", "适合做单品经营诊断和流量结构分析。"),
    "tmall_sycm_item_detail": ("获取商品 360 基础信息、属性诊断、人群包、新品、SKU 和价格风险提醒。", "适合补齐单品档案、诊断属性和价格基础信息。"),
    "tmall_sycm_item_flow_sources": ("获取商品 360 流量来源树、根渠道和扁平化来源路径。", "适合拆解流量从哪些渠道进入，定位渠道层级变化。"),
    "tmall_sycm_item_rank_summary": ("获取商品排行总商品数、更新时间和首屏采集状态。", "适合确认排行数据是否可用、是否需要继续拉取 TopN。"),
    "tmall_sycm_item_rank_top": ("获取商品排行 TopN，包含商品名称、ID、支付金额、访客、转化率和环比。", "适合找出表现最好或下滑最明显的商品。"),
    "tmall_sycm_market_rank": ("获取生意参谋市场竞品排行、类目、价格带和更新时间。", "适合做竞品监控、价格带对比和市场位置判断。"),
    "yuyidata_api_catalog": ("获取当前 Yuyi MCP 封装的开放接口目录、路径、用途和调用限制。", "适合开发前确认可用接口和参数边界。"),
    "yuyidata_build_sso_url": ("生成 Yuyi Neosight 快速登录 URL。", "适合给授权用户跳转到 Yuyi 后台核验数据或查看原始分析。"),
    "yuyidata_check_comments": ("拉取 Yuyi 处理后的商品/SKU 评论质检或分析结果。", "适合分析评论质量、用户反馈和商品问题。"),
    "yuyidata_check_customized_data": ("拉取 Yuyi 自定义文本/业务数据处理结果。", "适合获取自定义数据集的清洗、分类或模型分析结果。"),
    "yuyidata_check_orders": ("拉取 Yuyi 订单相关处理结果。", "适合分析订单咨询、售后、履约或成交相关问题。"),
    "yuyidata_check_training": ("拉取 Yuyi 训练结果数据。", "适合检查训练数据处理效果、模型或质检任务结果。"),
    "yuyidata_create_sessions_retrieve_task": ("创建客服对话导出任务，按时间范围拉取会话数据，平台自动补签名。", "适合定时拉取昨日/近几日客服对话做需求、情绪和风险分析。"),
    "yuyidata_create_sso_user": ("创建 Yuyi SSO 用户账号，默认 dry-run，真实调用需明确关闭 dry-run。", "适合为业务用户开通 Yuyi 访问账号。"),
    "yuyidata_download_task_file": ("获取并解析 Yuyi 导出任务文件，支持 NDJSON 记录和行数/大小限制。", "适合拿到客服对话明细后进入 Skill 分析流程。"),
    "yuyidata_get_task_info": ("按 taskId 查询 Yuyi 导出任务状态、文件地址和执行结果。", "适合轮询导出任务直到文件可获取。"),
    "yuyidata_upload_audio_url": ("上传音频 URL 会话到 Yuyi，默认 dry-run。", "适合把客服录音、通话音频交给 Yuyi 做后续分析。"),
    "yuyidata_upload_batch_sentences": ("批量上传最多 100 条单句对话事件，默认 dry-run。", "适合接入实时或批量拆分的客服消息。"),
    "yuyidata_upload_comments": ("上传商品/SKU 评论数据到 Yuyi，单次最多 10 条，默认 dry-run。", "适合把平台评论交给 Yuyi 做评论分析或质检。"),
    "yuyidata_upload_custom_text_data": ("上传自定义文本数据到 Yuyi，默认 dry-run。", "适合接入问卷、工单、私域反馈等非标准文本。"),
    "yuyidata_upload_sentence": ("上传单条单句对话事件，默认 dry-run。", "适合实时增量同步客服单句消息。"),
    "yuyidata_upload_text_session": ("上传一整段文本客服会话到 Yuyi，默认 dry-run。", "适合把完整聊天记录同步给 Yuyi 做会话级分析。"),
}


def _mcp_tool_capability(tool_name: str, description: str) -> tuple[str, str]:
    if tool_name in MCP_TOOL_CAPABILITY_TEXT:
        return MCP_TOOL_CAPABILITY_TEXT[tool_name]
    if description and description != tool_name:
        return description, "适合在 Skill 中作为业务数据来源，具体用途以工具描述和 inputSchema 为准。"
    return "获取该工具对应平台的数据。", "适合在 Skill 中补充业务上下文。"


def _mcp_capabilities_snapshot() -> dict[str, Any]:
    catalog = service.mcp_catalog_snapshot()
    server = (catalog.get("servers") or [{"name": "skillforge", "tools": []}])[0]
    tools = []
    platform_counts: dict[str, int] = {}
    write_count = 0
    for tool in server.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        meta = tool.get("meta") if isinstance(tool.get("meta"), dict) else {}
        platform = str(meta.get("platform") or "skillforge")
        write = bool(meta.get("write"))
        if write:
            write_count += 1
        platform_counts[platform] = platform_counts.get(platform, 0) + 1
        name = str(tool.get("name") or "")
        description = str(tool.get("description") or "")
        provides, use_when = _mcp_tool_capability(name, description)
        tools.append(
            {
                "name": name,
                "description": provides,
                "provides": provides,
                "use_when": use_when,
                "platform": platform,
                "data_scope": str(meta.get("data_scope") or ""),
                "endpoint_family": str(meta.get("endpoint_family") or ""),
                "requires_shop_id": bool(meta.get("requires_shop_id")),
                "write": write,
                "input_schema": tool.get("inputSchema") or tool.get("input_schema") or {},
            }
        )
    tools.sort(key=lambda item: item["name"])
    return {
        "server": str(server.get("name") or "skillforge"),
        "version": plugin_bundle.PLUGIN_VERSION,
        "tool_count": len(tools),
        "read_tool_count": len(tools) - write_count,
        "write_tool_count": write_count,
        "platform_count": len(platform_counts),
        "platforms": sorted(platform_counts),
        "platform_counts": dict(sorted(platform_counts.items())),
        "tools": tools,
    }


def _module_capabilities() -> list[dict[str, Any]]:
    return [
        {
            "name": "输出待办",
            "contract": "output.todos",
            "description": "Skill 返回待办列表，由 SkillForge 生成可追踪任务、分配负责人并保留执行证据。",
            "boundary": "脚本只输出待办数据，不直接写任务库。",
        },
        {
            "name": "输出报告",
            "contract": "output.reports",
            "description": "Skill 返回结构化报告、摘要和指标，平台沉淀到报告/性能摘要模块，便于复盘。",
            "boundary": "报告内容应包含数据来源、时间范围、结论和建议动作。",
        },
        {
            "name": "真实 MCP 数据",
            "contract": "mcp://<tool_name>",
            "description": "Skill 通过 SkillForge 网关调用 Yuyi、天猫、生意参谋等 MCP 工具，平台注入凭证并记录 proof。",
            "boundary": "不要在本地脚本读取 Cookie、access_token 或 .env 里的业务密钥。",
        },
        {
            "name": "执行原始数据归档",
            "contract": "execution_artifacts(raw-input/raw-output)",
            "description": "平台在执行完成时保存输入与输出原始 JSON 的 gzip Artifact，并记录大小、sha256、schema 和 run 引用。",
            "boundary": "通用 artifact 工具默认读取摘要和引用；完整原文只能通过受控数据能力显式读取。",
        },
        {
            "name": "缓存数据能力",
            "contract": "skillforge_data_capability_* / skillforge_*_data_*",
            "description": "把 Tmall 每日链接下滑采集和语艺客服会话采集拆成可发现、可复用、可受控取回的 sf 数据能力。",
            "boundary": "数据来源仍是节点定时 Skill 的执行产物；分析 Skill 读取缓存，不直接重复下载外部平台。",
        },
        {
            "name": "通用 SF 数据存储",
            "contract": "sf data write/records/record + skillforge_sf_data_write/list/get",
            "description": "Codex/sf 可把 JSON、文本、表格和文件内容写入 SkillForge 通用数据存储，并通过 MCP 或前端 SF 页面检索展示。",
            "boundary": "真实写入必须显式 --real 和 idempotency-key；平台按当前用户、namespace、visibility、Skill/Run lineage 和数据大小限制校验。",
            "highlight": True,
        },
        {
            "name": "输出结果预览",
            "contract": "sf preview <file> / /api/codex/previews/{id}",
            "description": "Codex 可把本地 Skill 输出上传成短期只读页面，直接给出 URL 预览报告、待办、动作、通知和原始 JSON。",
            "boundary": "预览只保存到 codex_output_previews，不生成正式待办、报告、通知、执行记录或钉钉 outbox。",
        },
        {
            "name": "钉钉推送",
            "contract": "platform outbox / skillforge_dingtalk_send_work_notice",
            "description": "Codex 可通过 MCP dry-run 或真实入队发送组织工作通知；Skill 运行产出的待办/报告也可由平台分发。",
            "boundary": "Skill 脚本不要直接调用钉钉 access_token，真实推送必须走平台 outbox 和幂等键。",
        },
        {
            "name": "定时执行",
            "contract": "trigger_type=cron + trigger_expression",
            "description": "审核发布后，定时配置下发到节点 Bridge/OpenClaw/AIClaw，由节点按 cron 触发执行。",
            "boundary": "平台中心调度只做兜底补偿，不替代节点定时主路径。",
        },
        {
            "name": "审核与版本",
            "contract": "sf skill submit / review",
            "description": "Codex 打包提交后进入 SkillForge Git、静态检查和审核状态机，发布态引用明确 commit。",
            "boundary": "不要绕过审核状态机，也不要直接写 skills-repo 发布。",
        },
        {
            "name": "项目宿主",
            "contract": "sf project submit / project asset upload / Project Gateway",
            "description": "Codex 产出的小网页通过 projectforge.yaml 声明入口、部门、能力和输出 contract，上传后在项目页点击即用；运行资产可通过 sf project asset upload 进入 ProjectRunAsset。",
            "boundary": "网页不带 AI key、MCP env 或业务 Cookie；AI/数据调用、运行资产上传和输出回传都走平台 Project Gateway。",
            "highlight": True,
        },
        {
            "name": "项目功能声明",
            "contract": "projectforge.yaml capabilities / Project Gateway",
            "description": "项目 manifest 可声明页面入口、服务函数、AI 分析、MCP 数据、运行资产、输出预览等功能，Codex 生成项目时按声明能力打通可调用范围。",
            "boundary": "只开放 manifest 声明且当前用户有权的能力；前端不能自行越权调用平台接口。",
            "highlight": True,
        },
        {
            "name": "项目生成标准",
            "contract": "sf project spec / sf project doctor --recipe",
            "description": "Codex 可读取项目宿主 contract、Gateway SDK、输出 contract、配方约束和可用能力，再按标准生成可直接上传运行的项目。",
            "boundary": "生成前先取平台标准，生成后用 doctor 校验入口、manifest、依赖、能力声明和输出 contract。",
            "highlight": True,
        },
        {
            "name": "项目组织权限解析",
            "contract": "sf project org resolve / /api/codex/projects/org/resolve",
            "description": "Codex 可在写 projectforge.yaml 前解析部门、负责人、公开范围和编辑权限，避免把项目归属或可见性写错。",
            "boundary": "组织、负责人和权限以 SkillForge 当前账号可见范围为准，不从本地配置或前端输入绕过。",
            "highlight": True,
        },
        {
            "name": "项目服务调用",
            "contract": "sf project invoke / /api/codex/projects/{project_id}/service/invoke",
            "description": "上传项目可通过 Project Gateway 调用平台服务，记录输入、输出、能力调用、AI loop 和回传结果，让页面按钮真正触发平台能力。",
            "boundary": "项目页面只拿短期运行上下文和授权 gateway；服务侧统一处理 AI key、MCP 凭证、审计和错误兜底。",
            "highlight": True,
        },
        {
            "name": "项目运行资产",
            "contract": "sf project asset upload/list / ProjectRunAsset",
            "description": "Codex 或项目页可把原视频、关键帧、投放数据等上传到当前 ProjectRun，并保留 material、asset_role、frame_time、source_file 等元数据。",
            "boundary": "大文件和视觉材料进入平台资产表与 run trace；分析服务只读取授权 run 的资产，不把文件塞进前端状态。",
            "highlight": True,
        },
        {
            "name": "短视频分析配方",
            "contract": "short-video-analysis / original_video / visual_frame",
            "description": "short-video-analysis 类项目可按素材 A/B、数据、原视频和关键帧生成诊断；维度由 Agent 基于钩子、商品露出、节奏、卖点证据和行动引导归因。",
            "boundary": "服务端优先用原视频做视觉诊断，失败时用关键帧兜底；前端不预设分数或结论。",
            "highlight": True,
        },
        {
            "name": "云视频投放数据",
            "contract": "mcp://skillforge_cloud_video_daily_person_video_report, mcp://skillforge_cloud_video_material_report, mcp://skillforge_cloud_video_video_usage_report, mcp://skillforge_cloud_video_audit_rejects",
            "description": "Codex/项目可通过 sf MCP 读取云视频人员树、素材分类、素材元数据、个人/明细消耗报表、素材统计报表、视频上传统计报表、卡审拒因和按天聚合的人-视频消耗数据。",
            "boundary": "云视频账号、密码、token 和签名只在 SkillForge 服务端使用；项目拿到的是可分析的人员、视频、素材数、消耗、ROI、CTR、转化率、卡审原因和 proof。",
            "highlight": True,
        },
        {
            "name": "云视频周度采集 Skill 模板",
            "contract": "sf skill init --template cloud-video-weekly-diagnosis",
            "description": "Codex 可直接生成一个每天 08:00 自动读取示例品牌云视频过去一周素材、消耗、上传量、卡审和拒因原始数据的采集 Skill。",
            "boundary": "模板只生成 Skill Git 包和受控 MCP 数据读取；分析、待办和推送交给后续独立 Skill，真实运行、审核、发布、节点定时下发仍走 SkillForge 状态机。",
            "highlight": True,
        },
        {
            "name": "部门业务分析 Agent",
            "contract": "sf agent analysis create/list / /api/codex/agents/analysis",
            "description": "Codex 可为部门创建业务分析 Agent blueprint，绑定 Skill、prompt version、默认分析维度、owner 和 editor，让业务负责人在 Agent 页面查看和管理分析提示词。",
            "boundary": "业务分析 Agent 是分析蓝图，不伪装成运行终端；执行仍由 Skill、节点 Bridge/OpenClaw/AIClaw、平台 Intelligence 服务和 Skill Git prompt 共同完成。",
            "highlight": True,
        },
        {
            "name": "Skill 共享与安装",
            "contract": "sf skill list --scope department / sf skill install",
            "description": "sf 可以按统一权限列出部门共享 Skill，并把可读 Skill 下载成本地可编辑工作区。",
            "boundary": "下载只读受控包，不暴露平台密钥；修改后仍需通过 sf skill submit 进入审核。",
        },
    ]


def _usage_tutorials() -> list[dict[str, Any]]:
    return [
        {
            "title": "案例：安装或刷新 SkillForge Codex 插件并验证 MCP 能力",
            "scenario": "目标是让 Codex 使用最新 SkillForge 插件和平台 catalog，避免旧 MCP schema 缓存导致 Invalid schema for function 'skillforge_ai_analyze'，并确认后续 /sf、真实 MCP、AI 分析和 Skill 提交都走 SkillForge 控制面。",
            "prompt": "请安装或更新 SkillForge Codex 插件：http://skillforge.example.com/api/codex/catalog/manifest。完成后运行 sf update --check、sf plugin status、sf auth status、sf plugin doctor --fix、sf mcp catalog --json，并确认 skillforge_ai_analyze 的 inputSchema 顶层只有 type/description/properties，没有 anyOf/oneOf/allOf/enum/not；如果 Codex 仍报 Invalid schema，请刷新插件缓存并重开 Codex 会话。",
            "flow_title": "系统是如何运作的",
            "flow": [
                "Codex 先读取 manifest 入口，拿到 plugin_update、bundle_url 和 sha256，按 sf update 流程校验并安装本地插件。",
                "安装后运行 sf plugin status 和 sf auth status；未登录时走 sf auth login，不手写 token、Cookie 或 .env 密钥。",
                "运行 sf plugin doctor --fix，确保 marketplace、Codex config、~/.local/bin/sf 和 MCP stdio 代理都指向当前 SkillForge 插件。",
                "运行 sf mcp catalog --json，检查 skillforge_ai_analyze.inputSchema 顶层是 type=object，只有 description/properties/type，不再包含 anyOf、oneOf、allOf、enum 或 not。",
                "如果 Codex 仍显示 Invalid schema，通常是本地插件或 MCP catalog 缓存未刷新；重新执行 sf update --force 或重开 Codex 会话，让 /sf 和 MCP 工具重新注册。",
                "验证通过后，再使用 /sf mcp、sf ai analyze、sf raw query、sf run analyze、sf skill submit 等命令；真实 MCP、AI 分析、钉钉推送和审核发布仍全部通过 SkillForge 控制面。",
            ],
        }
    ]


def _catalog_manifest_payload() -> dict[str, Any]:
    update_info = plugin_bundle.plugin_update_manifest()
    json_url = "/api/codex/catalog/manifest?format=json"
    command_groups = _sf_command_groups()
    return {
        "id": "skillforge-publisher",
        "version": 1,
        "schema_version": 2,
        "human_url": "/api/codex/catalog/manifest",
        "json_url": json_url,
        "plugin_update": update_info,
        "publisher_skill": {
            "name": "skillforge-publisher",
            "latest_version": update_info["latest_version"],
            "bundle_url": update_info["bundle_url"],
            "sha256": update_info["sha256"],
            "min_cli_version": update_info["min_cli_version"],
            "recommended_cli_version": update_info["recommended_cli_version"],
        },
        "command_groups": command_groups,
        "commands": _flatten_sf_commands(command_groups),
        "mcp_capabilities": _mcp_capabilities_snapshot(),
        "module_capabilities": _module_capabilities(),
        "usage_tutorials": _usage_tutorials(),
        "update": {
            "manifest_url": "/api/codex/catalog/manifest",
            "manifest_json_url": json_url,
            "bundle_url": update_info["bundle_url"],
            "catalog_bundle_url": "/api/codex/catalog/bundle",
            "sha256": update_info["sha256"],
        },
        "codex_install": {
            "entry_url": "/api/codex/catalog/manifest",
            "json_url": json_url,
            "bundle_url": update_info["bundle_url"],
            "sha256": update_info["sha256"],
            "plugin_root": "~/plugins/skillforge-codex",
            "post_install_commands": ["sf update --check", "sf plugin status", "sf auth login", "sf plugin doctor --fix", "sf 我的插件", "sf mcp catalog"],
        },
    }


def _request_wants_manifest_html(request: Request, fmt: str | None) -> bool:
    if fmt:
        return fmt.lower() in {"html", "page"}
    accept = request.headers.get("accept") or ""
    return "text/html" in accept and "application/json" not in accept


def _catalog_manifest_html(manifest: dict[str, Any], request: Request) -> str:
    update_info = manifest["plugin_update"]
    base = str(request.base_url).rstrip("/")
    manifest_url = f"{base}/api/codex/catalog/manifest"
    version = html.escape(str(update_info["latest_version"]))
    release_title = str(update_info.get("release_title") or "SkillForge Codex 发布")
    install_prompt = (
        "请安装或更新 SkillForge Codex 插件："
        f"{manifest_url}。读取这个入口提供的 plugin_update 信息，校验 sha256，"
        "安装到本地 Codex 插件目录，写入 Codex 插件市场和配置文件以及 sf 命令，"
        "执行 codex plugin add skillforge-codex@skillforge-local 或当前已配置插件市场对应的选择器刷新 Codex 插件缓存，让 /sf 注册成命令按钮，"
        "然后运行 sf update --check、sf plugin status、sf auth login、sf plugin doctor --fix、sf 我的插件、sf mcp catalog 验证。"
    )
    install_prompt_attr = html.escape(install_prompt, quote=True)
    install_prompt_js = json.dumps(install_prompt, ensure_ascii=False)
    codex_install = manifest.get("codex_install") if isinstance(manifest.get("codex_install"), dict) else {}
    post_install_commands = [
        str(item)
        for item in (codex_install.get("post_install_commands") or [])
        if str(item or "").strip()
    ]
    post_install_html = "\n".join(
        f"<li><code>{html.escape(command)}</code><span>安装或更新后执行，用来确认本机 sf、登录和 MCP 能力可用。</span></li>"
        for command in post_install_commands
    )
    version_meta = [
        ("sf 插件版本", update_info.get("latest_version")),
        ("最低 CLI 版本", update_info.get("min_cli_version")),
        ("推荐 CLI 版本", update_info.get("recommended_cli_version")),
        ("更新包格式", update_info.get("format")),
        ("更新包地址", update_info.get("bundle_url")),
        ("sha256", update_info.get("sha256")),
    ]
    version_meta_html = "\n".join(
        "<li>"
        f"<span>{html.escape(label)}</span>"
        f"<code>{html.escape(str(value or ''))}</code>"
        "</li>"
        for label, value in version_meta
    )
    release_notes = update_info.get("release_notes") if isinstance(update_info.get("release_notes"), list) else []
    cover_notes = [
        str(note)
        for note in release_notes[:3]
        if str(note or "").strip()
    ]
    cover_note_html = "\n".join(
        f"<li><span>{html.escape(note)}</span></li>"
        for note in cover_notes
    ) or "<li><span>当前版本未提供封面更新说明。</span></li>"
    release_note_html = "\n".join(
        f"<li class='highlight'><span class='new-gradient'>{html.escape(str(note))}</span></li>"
        for note in release_notes
        if str(note or "").strip()
    ) or "<li>当前版本未提供功能说明。</li>"
    version_history = update_info.get("version_history") if isinstance(update_info.get("version_history"), list) else []
    current_version_history_html = ""
    old_version_history_html = ""
    status_labels = {
        "current": "当前版本",
        "released": "已发布",
        "internal": "内部合并",
    }
    for entry in version_history:
        if not isinstance(entry, dict):
            continue
        entry_version = str(entry.get("version") or "").strip()
        if not entry_version:
            continue
        status = str(entry.get("status") or "").strip()
        note_items = entry.get("notes") if isinstance(entry.get("notes"), list) else []
        note_html = "\n".join(
            f"<li>{html.escape(str(note))}</li>"
            for note in note_items
            if str(note or "").strip()
        ) or "<li>该版本暂无记录。</li>"
        title_text = str(entry.get("title") or "版本更新")
        date_text = str(entry.get("date") or "")
        status_text = status_labels.get(status, status or "已发布")
        if status == "current" or entry_version == str(update_info.get("latest_version") or ""):
            current_version_history_html += (
                "<article class='release-card current'>"
                "<div class='release-head'>"
                "<div>"
                f"<div class='release-kicker'>版本 {html.escape(entry_version)} · {html.escape(status_text)}</div>"
                f"<h3>{html.escape(title_text)}</h3>"
                "</div>"
                f"<span>{html.escape(date_text)}</span>"
                "</div>"
                f"<ul class='release-notes'>{note_html}</ul>"
                "</article>"
            )
            continue
        old_version_history_html += (
            "<details class='release-card old'>"
            "<summary>"
            "<span>"
            f"<strong>版本 {html.escape(entry_version)}</strong>"
            f"<em>{html.escape(status_text)} · {html.escape(title_text)}</em>"
            "</span>"
            f"<code>{html.escape(date_text)}</code>"
            "</summary>"
            f"<ul class='release-notes'>{note_html}</ul>"
            "</details>"
        )
    if not current_version_history_html and release_note_html:
        current_version_history_html = (
            "<article class='release-card current'>"
            "<div class='release-head'><div>"
            f"<div class='release-kicker'>版本 {version} · 当前版本</div>"
            "<h3>本版本更新</h3>"
            "</div></div>"
            f"<ul class='release-notes'>{release_note_html}</ul>"
            "</article>"
        )
    old_version_history_html = old_version_history_html or "<p class='empty-release'>暂无旧版本记录。</p>"
    sf_feature_html = "\n".join(
        f"<li class='highlight'><span class='new-gradient'>{html.escape(item)}</span></li>"
        for item in [
            "当前封面发布：通用 SF 数据写入与存储，Codex/sf 可把 JSON、文本、表格和文件内容写入平台数据存储。",
            "新增 skillforge_sf_data_write/list/get MCP 能力，支持受控写入、分页检索和单条读取；真实写入必须带 --real 和幂等键。",
            "前端 SF 数据页面会展示这份数据，并支持按 namespace、内容类型、Skill、Run、来源和关键词筛选。",
            "保留 Tmall 项目数据网关与动态日报输出，项目页面可从 Project Gateway 读取平台缓存并生成最新诊断。",
            "保留 tmall_link_health_latest_analysis 项目能力和 latest-analysis.json 动态输出，静态项目包只作兜底。",
            "Tmall Artifact 内容读取默认支持 5MB，3MB 级采集 JSON 可通过受控能力读取，不要求页面实时访问外部业务系统。",
            "支持 sf update 自更新、sha256 校验、Codex 插件缓存刷新和 ~/.local/bin/sf shim 安装。",
            "支持 sf auth 登录、sf plugin status、我的插件、我的部门插件和统一权限范围内的 Skill 列表。",
            "支持 sf skill install / pull，把有 read 权限的共享 Skill 下载到本地工作区，并写入 base_commit。",
            "新增 sf run / sf runs，可触发执行并查看状态、步骤、结果、Artifact、脱敏日志和失败诊断。",
            "新增 sf preview apply / sf output validate，输出先校验和预演，真实落库必须提供幂等键。",
            "新增 sf data / sf notify，让 Codex 直接复用平台缓存原始数据并 dry-run 组织通知。",
            "新增 Project Host / Project Gateway，支持 Codex 生成的小网页通过 sf project spec/org resolve/doctor/submit/status/invoke/asset upload/logs 进入平台运行和追踪。",
            "新增 short-video-analysis 项目生成标准，明确原视频、关键帧、material_a/material_b、visual_frame metadata 和视觉兜底规则。",
            "新增 cloud-video-weekly-diagnosis Skill 模板，Codex 可直接生成云视频周度同主题消耗诊断 Skill。",
            "新增 sf agent analysis create/list，Codex 可创建部门业务分析 Agent blueprint 并绑定 Skill/prompt/维度/管理人。",
            "新增训练资源、数据集、训练候选、训练任务、模型部署审批、驳回、激活、回滚和产物下载命令。",
            "新增 sf schedule / sf review / sf market / sf pipeline，补齐定时、审核、共享市场和流水线能力。",
            "支持 SkillForge MCP 网关，包括组织用户查询、钉钉 outbox、Tmall 链接下滑/语艺缓存数据能力和执行 Artifact 查询。",
        ]
    )
    command_group_html = "\n".join(
        "<div class='command-group'>"
        f"<h3>{html.escape(str(group.get('title') or 'sf'))}</h3>"
        "<ul class='command-list'>"
        + "\n".join(
            (
                "<li>"
                f"<code>{html.escape(str(item['command']))}</code>"
                f"<span class='new-gradient'>{html.escape(str(item['description']))}</span>"
                "</li>"
                if (
                    str(group.get("title") or "") == "项目宿主" and (
                    "project spec" in str(item.get("command") or "")
                    or "project org resolve" in str(item.get("command") or "")
                    or "project verify" in str(item.get("command") or "")
                    or "project asset" in str(item.get("command") or "")
                )
                )
                or "skillforge_cloud_video" in str(item.get("command") or "")
                or "agent analysis" in str(item.get("command") or "")
                or "cloud-video-weekly-diagnosis" in str(item.get("command") or "")
                else f"<li><code>{html.escape(str(item['command']))}</code><span>{html.escape(str(item['description']))}</span></li>"
            )
            for item in group.get("items", [])
        )
        + "</ul></div>"
        for group in manifest.get("command_groups", [])
    )
    mcp = manifest.get("mcp_capabilities") if isinstance(manifest.get("mcp_capabilities"), dict) else {}
    mcp_tools = [tool for tool in mcp.get("tools", []) if isinstance(tool, dict)]
    mcp_tool_html = "\n".join(
        "<li>"
        f"<code>{html.escape(str(tool.get('name') or ''))}</code>"
        "<span>"
        f"<strong>能获得什么</strong>{html.escape(str(tool.get('provides') or tool.get('description') or ''))}"
        f"<strong>适合场景</strong>{html.escape(str(tool.get('use_when') or ''))}"
        f"<em>{html.escape(str(tool.get('platform') or 'skillforge'))}"
        f" · {html.escape(str(tool.get('data_scope') or ''))}"
        f" · {'写入' if tool.get('write') else '读取'}"
        f"{' · 需要 shop_id' if tool.get('requires_shop_id') else ''}</em>"
        "</span>"
        "</li>"
        for tool in mcp_tools
    )
    def _render_module_item(item: dict[str, Any]) -> str:
        body = (
            f"<strong>{html.escape(str(item.get('name') or ''))}</strong>"
            f"{html.escape(str(item.get('description') or ''))}"
        )
        if item.get("highlight"):
            body = f"<span class='new-gradient'>{body}</span>"
        return (
            "<li>"
            f"<code>{html.escape(str(item.get('contract') or ''))}</code>"
            "<span>"
            f"{body}"
            f"<em>{html.escape(str(item.get('boundary') or ''))}</em>"
            "</span>"
            "</li>"
        )

    module_html = "\n".join(
        _render_module_item(item)
        for item in manifest.get("module_capabilities", [])
        if isinstance(item, dict)
    )
    tutorial = next(
        (item for item in manifest.get("usage_tutorials", []) if isinstance(item, dict)),
        {},
    )
    tutorial_flow_html = "\n".join(
        f"<li><span>{idx}</span><p>{html.escape(str(step))}</p></li>"
        for idx, step in enumerate(tutorial.get("flow", []), start=1)
    )
    tutorial_prompt = html.escape(str(tutorial.get("prompt") or ""))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SkillForge Codex 插件</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --ink: #172033;
      --muted: #5f6d7f;
      --line: #d8e0ea;
      --panel: #ffffff;
      --blue: #2463eb;
      --green: #0f8a63;
      --amber: #b45309;
      --navy: #111827;
      --soft-blue: #e9f0ff;
      --soft-green: #e7f6ef;
      --soft-amber: #fff4df;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); line-height: 1.55; }}
    a {{ color: inherit; }}
    .topbar {{
      position: sticky;
      top: 0;
      z-index: 10;
      background: rgba(255,255,255,.92);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(14px);
    }}
    .nav {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 12px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    .brand {{ display: flex; align-items: center; gap: 10px; font-weight: 800; }}
    .mark {{
      width: 34px;
      height: 34px;
      display: grid;
      place-items: center;
      border-radius: 8px;
      background: var(--navy);
      color: #fff;
      letter-spacing: 0;
      font-size: 14px;
    }}
    .nav-links {{ display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }}
    .nav-links a {{
      padding: 8px 10px;
      border-radius: 7px;
      text-decoration: none;
      color: var(--muted);
      font-size: 14px;
    }}
    .nav-links a:hover {{ background: #eef2f7; color: var(--ink); }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 34px 20px 56px; }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.08fr) minmax(320px, .92fr);
      gap: 28px;
      align-items: stretch;
    }}
    .hero-copy {{ padding: 34px 0 20px; }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fff;
      color: var(--muted);
      font-size: 13px;
      font-weight: 650;
    }}
    h1 {{
      margin: 18px 0 14px;
      font-size: clamp(36px, 7vw, 66px);
      line-height: .98;
      letter-spacing: 0;
    }}
    .lead {{ max-width: 680px; margin: 0; color: var(--muted); font-size: 18px; }}
    .actions {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 28px; }}
    .cover-notes {{
      display: grid;
      gap: 10px;
      margin: 24px 0 0;
      padding: 0;
      list-style: none;
      max-width: 720px;
    }}
    .cover-notes li {{
      border: 1px solid var(--line);
      border-left: 4px solid var(--blue);
      border-radius: 8px;
      background: #fff;
      padding: 12px 14px;
      color: var(--ink);
      font-weight: 650;
      line-height: 1.55;
    }}
    .cover-notes span {{ color: var(--ink); }}
    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
      padding: 10px 14px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      text-decoration: none;
      font-weight: 700;
    }}
    .btn.primary {{ background: var(--blue); border-color: var(--blue); color: #fff; }}
    button.btn {{ cursor: pointer; font: inherit; }}
    .product-shot {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 18px 48px rgba(23, 32, 51, .10);
      overflow: hidden;
      min-height: 420px;
      display: flex;
      flex-direction: column;
    }}
    .shot-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }}
    .dots {{ display: flex; gap: 6px; }}
    .dots span {{ width: 10px; height: 10px; border-radius: 50%; background: #cbd5e1; }}
    .status-pill {{ color: var(--green); background: var(--soft-green); border-radius: 999px; padding: 5px 9px; font-size: 12px; font-weight: 750; }}
    .terminal {{
      margin: 0;
      padding: 18px;
      background: #111827;
      color: #d1fae5;
      font: 13px/1.7 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: pre-wrap;
      flex: 1;
      overflow-wrap: anywhere;
    }}
    .terminal .dim {{ color: #9ca3af; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
      margin-top: 26px;
    }}
    .panel, .step, .command-panel, .prompt-box {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .panel {{ padding: 20px; }}
    .panel h2, .command-panel h2 {{ margin: 0 0 10px; font-size: 20px; letter-spacing: 0; }}
    .panel p {{ margin: 0; color: var(--muted); }}
    .metric {{
      display: inline-flex;
      margin-bottom: 12px;
      padding: 5px 8px;
      border-radius: 7px;
      font-weight: 800;
      font-size: 12px;
    }}
    .metric.blue {{ background: var(--soft-blue); color: var(--blue); }}
    .metric.green {{ background: var(--soft-green); color: var(--green); }}
    .metric.amber {{ background: var(--soft-amber); color: var(--amber); }}
    .section {{ margin-top: 34px; }}
    .section-head {{ display: flex; align-items: end; justify-content: space-between; gap: 18px; margin-bottom: 14px; }}
    .section h2 {{ margin: 0; font-size: 28px; letter-spacing: 0; }}
    .section p.sub {{ margin: 6px 0 0; color: var(--muted); }}
    .steps {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
    }}
    .install-grid, .version-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 16px;
      margin-top: 16px;
    }}
    .info-panel {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      min-width: 0;
    }}
    .info-panel h3 {{ margin: 0 0 10px; font-size: 18px; letter-spacing: 0; }}
    .info-panel p {{ margin: 0 0 12px; color: var(--muted); }}
    .feature-list, .version-meta {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 10px;
    }}
    .feature-list li {{
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      color: var(--muted);
    }}
    .feature-list li.highlight {{
      border-color: #93c5fd;
      background: #eff6ff;
      color: #1d4ed8;
      font-weight: 750;
    }}
    .new-gradient {{
      background: linear-gradient(90deg, #2563eb 0%, #0f8a63 42%, #b45309 100%);
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
      font-weight: 850;
    }}
    .command-list span.new-gradient {{
      color: transparent;
      font-weight: 800;
    }}
    .version-meta li {{
      display: grid;
      grid-template-columns: 130px minmax(0, 1fr);
      gap: 10px;
      align-items: start;
      padding: 10px 0;
      border-top: 1px solid var(--line);
    }}
    .version-meta li:first-child {{ border-top: 0; }}
    .version-meta span {{ color: var(--muted); font-weight: 750; }}
    .version-meta code {{
      color: var(--ink);
      overflow-wrap: anywhere;
      white-space: normal;
    }}
    .release-timeline {{
      display: grid;
      gap: 12px;
    }}
    .release-card {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 0;
    }}
    .release-card.current {{
      border-color: #93c5fd;
      background: linear-gradient(180deg, #eff6ff 0%, #fff 58%);
      box-shadow: inset 4px 0 0 var(--blue);
      padding: 18px;
    }}
    .release-card.old {{
      overflow: hidden;
    }}
    .release-head, .release-card summary {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }}
    .release-card summary {{
      cursor: pointer;
      padding: 14px 16px;
      list-style: none;
    }}
    .release-card summary::-webkit-details-marker {{ display: none; }}
    .release-card summary::after {{
      content: "展开";
      flex: 0 0 auto;
      color: var(--blue);
      font-weight: 800;
      font-size: 13px;
    }}
    .release-card[open] summary::after {{ content: "收起"; }}
    .release-head h3 {{
      margin: 4px 0 0;
      font-size: 20px;
      letter-spacing: 0;
    }}
    .release-head span, .release-card summary code {{
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }}
    .release-kicker {{
      color: var(--blue);
      font-weight: 850;
      font-size: 13px;
    }}
    .release-card summary span {{
      min-width: 0;
      display: grid;
      gap: 3px;
    }}
    .release-card summary strong {{
      color: var(--ink);
      font-size: 16px;
    }}
    .release-card summary em {{
      color: var(--muted);
      font-style: normal;
      overflow-wrap: anywhere;
    }}
    .release-notes {{
      margin: 12px 0 0;
      padding-left: 18px;
      color: var(--muted);
    }}
    .release-card.old .release-notes {{
      margin: 0;
      padding: 0 16px 16px 34px;
    }}
    .release-notes li {{
      margin: 7px 0;
    }}
    .release-notes li.highlight {{
      color: #1d4ed8;
      font-weight: 750;
    }}
    .empty-release {{
      margin: 0;
      color: var(--muted);
    }}
    .step {{ padding: 18px; }}
    .step-num {{
      width: 30px;
      height: 30px;
      display: grid;
      place-items: center;
      border-radius: 7px;
      background: var(--navy);
      color: #fff;
      font-weight: 800;
      margin-bottom: 12px;
    }}
    .step h3 {{ margin: 0 0 8px; font-size: 17px; }}
    .step p {{ margin: 0; color: var(--muted); }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: 0; }}
    .command-panel, .prompt-box {{ padding: 18px; }}
    .cmd-row {{
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
      padding: 10px 0;
      border-top: 1px solid var(--line);
    }}
    .cmd-row:first-of-type {{ border-top: 0; }}
    .cmd-row span {{ color: var(--muted); font-weight: 700; }}
    .cmd-row code, .prompt-box code {{
      display: block;
      background: #f2f5f9;
      color: #111827;
      border: 1px solid #e2e8f0;
      border-radius: 7px;
      padding: 9px 10px;
      overflow-wrap: anywhere;
    }}
    .prompt-box {{ margin-top: 16px; }}
    .prompt-box p {{ margin: 0 0 10px; color: var(--muted); }}
    .prompt-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }}
    .prompt-head p {{ margin: 0; }}
    .copy-feedback {{
      min-height: 22px;
      margin-top: 10px;
      color: var(--green);
      font-size: 14px;
      font-weight: 700;
    }}
    .copy-feedback.error {{ color: #b91c1c; }}
    .copy-toast {{
      position: fixed;
      right: 20px;
      bottom: 20px;
      z-index: 20;
      max-width: min(360px, calc(100vw - 40px));
      padding: 12px 14px;
      border-radius: 8px;
      background: var(--navy);
      color: #fff;
      box-shadow: 0 14px 36px rgba(17, 24, 39, .22);
      opacity: 0;
      transform: translateY(8px);
      pointer-events: none;
      transition: opacity .18s ease, transform .18s ease;
      font-weight: 750;
    }}
    .copy-toast.visible {{ opacity: 1; transform: translateY(0); }}
    .copy-toast.error {{ background: #991b1b; }}
    .command-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 10px;
    }}
    .command-groups {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .command-group {{ min-width: 0; }}
    .command-group h3 {{
      margin: 0 0 10px;
      font-size: 18px;
      letter-spacing: 0;
    }}
    .command-list li {{
      display: grid;
      grid-template-columns: minmax(210px, .72fr) minmax(0, 1fr);
      gap: 12px;
      align-items: start;
      padding: 12px;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .command-list code {{ color: var(--blue); overflow-wrap: anywhere; }}
    .command-list span {{ color: var(--muted); }}
    .mcp-summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}
    .mcp-stat {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .mcp-stat strong {{
      display: block;
      font-size: 26px;
      line-height: 1;
      color: var(--ink);
      letter-spacing: 0;
    }}
    .mcp-stat span {{ display: block; margin-top: 6px; color: var(--muted); font-size: 13px; }}
    .mcp-tools li span em {{
      display: block;
      margin-top: 5px;
      color: #64748b;
      font-style: normal;
      font-size: 12px;
    }}
    .command-list span strong {{
      display: block;
      color: var(--ink);
      margin-bottom: 4px;
    }}
    .tutorial {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }}
    .tutorial h3 {{ margin: 0 0 8px; font-size: 22px; letter-spacing: 0; }}
    .tutorial p {{ margin: 0; color: var(--muted); }}
    .tutorial-prompt {{
      margin-top: 16px;
      padding: 14px;
      border-radius: 8px;
      border: 1px solid #c7d2fe;
      background: #eef2ff;
      color: var(--ink);
      font-weight: 650;
    }}
    .tutorial-steps {{
      list-style: none;
      padding: 0;
      margin: 16px 0;
      display: grid;
      gap: 10px;
    }}
    .tutorial-steps li {{
      display: grid;
      grid-template-columns: 30px minmax(0, 1fr);
      gap: 10px;
      align-items: start;
    }}
    .tutorial-steps span {{
      width: 30px;
      height: 30px;
      display: grid;
      place-items: center;
      border-radius: 7px;
      background: var(--navy);
      color: #fff;
      font-weight: 800;
      font-size: 13px;
    }}
    .footer {{
      margin-top: 34px;
      padding-top: 18px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      display: flex;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      font-size: 14px;
    }}
    @media (max-width: 860px) {{
      .hero, .grid, .steps {{ grid-template-columns: 1fr; }}
      .hero-copy {{ padding-top: 14px; }}
      .product-shot {{ min-height: 320px; }}
      .cmd-row, .command-list li, .command-groups, .mcp-summary, .install-grid, .version-grid, .version-meta li {{ grid-template-columns: 1fr; }}
      .nav {{ align-items: flex-start; flex-direction: column; }}
      .nav-links {{ justify-content: flex-start; }}
    }}
  </style>
</head>
<body>
  <header class="topbar">
    <nav class="nav" aria-label="主导航">
      <div class="brand"><div class="mark">SF</div><span>SkillForge Codex 插件</span></div>
      <div class="nav-links">
        <a href="#top">首页</a>
        <a href="#version">版本</a>
        <a href="#modules">模块能力</a>
        <a href="#install">安装</a>
        <a href="#tutorial">教程</a>
        <a href="#commands">命令</a>
        <a href="#mcp">MCP 能力</a>
      </div>
    </nav>
  </header>
  <main id="top">
    <section class="hero">
      <div class="hero-copy">
        <div class="eyebrow">生产环境 · 自动更新入口 · 版本 {version}</div>
        <h1>{html.escape(release_title)}</h1>
        <p class="lead">SF 封面发布的是当前 Codex 可直接使用的平台能力。本版本重点是让 Codex/sf 把业务样本、分析中间结果、表格和文件写入 SkillForge 通用数据存储，并能在 SF 页面和 MCP 工具中继续检索复用。</p>
        <div class="actions">
          <a class="btn primary" href="#install" data-copy-install data-copy-text="{install_prompt_attr}">复制安装提示词</a>
          <a class="btn" href="#version">查看版本说明</a>
          <a class="btn" href="#modules">查看模块能力</a>
          <a class="btn" href="#mcp">查看 MCP 能力</a>
        </div>
        <ul class="cover-notes">
          {cover_note_html}
        </ul>
      </div>
      <div class="product-shot" aria-label="SkillForge CLI 示例">
        <div class="shot-head"><div class="dots"><span></span><span></span><span></span></div><div class="status-pill">已连接生产</div></div>
        <pre class="terminal">$ sf update --check
<span class="dim">平台地址</span>: http://skillforge.example.com
<span class="dim">当前版本</span>: {version}
<span class="dim">可升级</span>: false

$ sf mcp call skillforge_tmall_link_decline_data_latest --args '{{}}'
<span class="dim">来源</span>: tmall-link-decline-collector-v1
<span class="dim">返回</span>: 最新缓存 Artifact 摘要

$ sf mcp catalog
skillforge_sf_data_write
skillforge_sf_data_list
skillforge_sf_data_get

$ sf data write --namespace demo --json '{{"hello":"world"}}' \
  --real --idempotency-key demo-001
<span class="dim">返回</span>: sfdata_xxx，已写入通用 SF 数据库

$ sf data records --namespace demo
<span class="dim">返回</span>: SF 页面同源展示的数据记录列表

$ sf auth login
打开浏览器完成授权，Codex 即可使用平台能力。</pre>
      </div>
    </section>

    <section class="section" id="version">
      <div class="section-head"><div><h2>sf 版本功能说明</h2><p class="sub">当前发布版本、校验信息和本版本可用能力。Codex 和 sf update 都读取同一份 manifest。</p></div></div>
      <div class="version-grid">
        <div class="info-panel">
          <h3>版本信息</h3>
          <ul class="version-meta">
            {version_meta_html}
          </ul>
        </div>
        <div class="info-panel">
          <h3>本版本重点能力</h3>
          <ul class="feature-list">
            {sf_feature_html}
          </ul>
        </div>
      </div>
      <div class="info-panel" style="margin-top:16px">
        <h3>版本历史</h3>
        <div class="release-timeline">
          {current_version_history_html}
          {old_version_history_html}
        </div>
      </div>
    </section>

    <section class="section" id="modules">
      <div class="section-head"><div><h2>模块能力</h2><p class="sub">Skill 只产出结构化结果，写入、推送、调度、审计由 SkillForge 平台处理。</p></div></div>
      <ul class="command-list">
        {module_html}
      </ul>
    </section>

    <section class="grid" aria-label="插件能力">
      <div class="panel"><div class="metric blue">写入</div><h2>SF 数据存储</h2><p>Codex/sf 可通过 sf data write 写入 JSON、文本、表格和文件内容，统一记录 namespace、metadata 和来源链路。</p></div>
      <div class="panel"><div class="metric green">MCP</div><h2>sf_data 工具</h2><p>skillforge_sf_data_write/list/get 提供受控写入、分页检索和单条读取，真实写入要求 --real 与幂等键。</p></div>
      <div class="panel"><div class="metric amber">前端</div><h2>SF 页面展示</h2><p>写入后的数据会在前端 SF 数据页展示，可按 namespace、内容类型、Skill、Run、来源和关键词筛选。</p></div>
    </section>

    <section class="section" id="install">
      <div class="section-head"><div><h2>安装流程</h2><p class="sub">把下面这段话复制到 Codex。新电脑和已安装电脑都走同一路径，Codex 会自动检查、安装、更新和验证。</p></div></div>
      <div class="prompt-box">
        <div class="prompt-head">
          <p>复制给 Codex：</p>
          <button class="btn primary" type="button" data-copy-install data-copy-text="{install_prompt_attr}">复制提示词</button>
        </div>
        <code>{html.escape(install_prompt)}</code>
        <div class="copy-feedback" id="copy-status" role="status" aria-live="polite"></div>
      </div>
      <div class="install-grid">
        <div class="info-panel">
          <h3>安装后验证</h3>
          <p>安装或更新完成后，按顺序确认版本、插件状态、登录和 MCP 目录。</p>
          <ul class="command-list">
            {post_install_html}
          </ul>
        </div>
        <div class="info-panel">
          <h3>常用入口</h3>
          <ul class="feature-list">
            <li><code>sf skill list --scope department</code> 查看部门共享 Skill。</li>
            <li><code>sf skill install &lt;skill_id&gt; --path ./skills</code> 下载可读 Skill 到本地。</li>
            <li><code>sf skill submit --path . --message "修改说明"</code> 上传本地修改并进入审核。</li>
            <li><code>sf mcp catalog</code> 查看当前账号可用的真实 MCP 能力。</li>
          </ul>
        </div>
      </div>
      <div class="steps">
        <div class="step"><div class="step-num">1</div><h3>复制提示词</h3><p>复制上面的安装提示词，直接发给 Codex。</p></div>
        <div class="step"><div class="step-num">2</div><h3>Codex 自动处理</h3><p>Codex 会检查版本、校验 sha256、写入插件市场和配置文件，安装 sf 命令，并刷新 /sf 命令按钮。</p></div>
        <div class="step"><div class="step-num">3</div><h3>登录后使用</h3><p>按 Codex 提示执行 `sf auth login` 完成授权，然后用 `sf mcp catalog` 和 `/sf ...` 命令。</p></div>
      </div>
    </section>

    <section class="section" id="tutorial">
      <div class="section-head"><div><h2>使用教程</h2><p class="sub">核心用法是把业务目标告诉 Codex，让 sf 插件完成生成、验证和提交。</p></div></div>
      <div class="tutorial">
        <h3>{html.escape(str(tutorial.get('title') or '案例'))}</h3>
        <p>{html.escape(str(tutorial.get('scenario') or ''))}</p>
        <div class="tutorial-prompt">{tutorial_prompt}</div>
        <h3>{html.escape(str(tutorial.get('flow_title') or '系统是如何运作的'))}</h3>
        <ol class="tutorial-steps">
          {tutorial_flow_html}
        </ol>
      </div>
    </section>

    <section class="section" id="commands">
      <div class="section-head"><div><h2>全部 sf 命令</h2><p class="sub">这些命令会随 manifest 一起提供给 Codex，包含中文入口和标准 CLI 写法。</p></div></div>
      <div class="command-groups">
        {command_group_html}
      </div>
    </section>

    <section class="section" id="mcp">
      <div class="section-head"><div><h2>当前版本 MCP 能力</h2><p class="sub">来自当前生产版本 manifest，Codex 登录后通过 `sf mcp catalog` 获取同一套能力。</p></div></div>
      <div class="mcp-summary">
        <div class="mcp-stat"><strong>{html.escape(str(mcp.get('tool_count') or 0))}</strong><span>工具总数</span></div>
        <div class="mcp-stat"><strong>{html.escape(str(mcp.get('read_tool_count') or 0))}</strong><span>读取工具</span></div>
        <div class="mcp-stat"><strong>{html.escape(str(mcp.get('write_tool_count') or 0))}</strong><span>写入工具</span></div>
        <div class="mcp-stat"><strong>{html.escape(str(mcp.get('platform_count') or 0))}</strong><span>平台类型</span></div>
      </div>
      <ul class="command-list mcp-tools">
        {mcp_tool_html}
      </ul>
    </section>

    <footer class="footer">
      <span>插件：skillforge-codex · 版本 {version}</span>
      <span>生产地址：{html.escape(base)}</span>
    </footer>
  </main>
  <div class="copy-toast" id="copy-toast" role="status" aria-live="polite"></div>
  <script>
    (() => {{
      const installPrompt = {install_prompt_js};
      const toast = document.getElementById('copy-toast');
      const status = document.getElementById('copy-status');
      let toastTimer;

      const showMessage = (message, isError = false) => {{
        if (status) {{
          status.textContent = message;
          status.classList.toggle('error', isError);
        }}
        if (!toast) return;
        toast.textContent = message;
        toast.classList.toggle('error', isError);
        toast.classList.add('visible');
        window.clearTimeout(toastTimer);
        toastTimer = window.setTimeout(() => {{
          toast.classList.remove('visible');
        }}, 2600);
      }};

      const fallbackCopy = (text) => {{
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.top = '-1000px';
        textarea.style.left = '-1000px';
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        textarea.setSelectionRange(0, textarea.value.length);
        let copied = false;
        try {{
          copied = document.execCommand('copy');
        }} finally {{
          textarea.remove();
        }}
        if (!copied) throw new Error('copy failed');
      }};

      const copyText = async (text) => {{
        if (navigator.clipboard && window.isSecureContext) {{
          await navigator.clipboard.writeText(text);
          return;
        }}
        fallbackCopy(text);
      }};

      document.querySelectorAll('[data-copy-install]').forEach((trigger) => {{
        trigger.addEventListener('click', (event) => {{
          const text = trigger.dataset.copyText || installPrompt;
          const copyTask = copyText(text);
          const href = trigger.getAttribute('href');
          if (href) {{
            event.preventDefault();
            const target = document.querySelector(href);
            if (target) target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
            if (window.history && window.history.pushState) {{
              window.history.pushState(null, '', href);
            }} else {{
              window.location.hash = href.slice(1);
            }}
          }}
          copyTask
            .then(() => showMessage('安装提示词已复制，可以直接粘贴到 Codex。'))
            .catch(() => showMessage('没有自动复制成功，请手动复制下方提示词。', true));
        }});
      }});
    }})();
  </script>
</body>
</html>"""


@router.get("/catalog/manifest")
async def catalog_manifest(
    request: Request,
    format: str | None = Query(None, pattern="^(html|page|json)?$"),
):
    manifest = _catalog_manifest_payload()
    if _request_wants_manifest_html(request, format):
        return HTMLResponse(_catalog_manifest_html(manifest, request))
    return JSONResponse(manifest)


@router.get("/catalog/bundle")
async def catalog_bundle():
    manifest = _catalog_manifest_payload()
    return {
        **manifest,
        "files": [
            {"path": "SKILL.md", "sha256": "platform-managed"},
            {"path": "agents/openai.yaml", "sha256": "platform-managed"},
        ],
    }


def _skillforge_codex_plugin_bundle_response(*, include_content: bool) -> Response:
    bundle = plugin_bundle.build_plugin_bundle()
    digest = plugin_bundle.sha256_hex(bundle)
    return Response(
        content=bundle if include_content else b"",
        media_type="application/gzip",
        headers={
            "Content-Disposition": 'attachment; filename="skillforge-codex.tar.gz"',
            "X-SkillForge-Plugin": plugin_bundle.PLUGIN_NAME,
            "X-SkillForge-Plugin-Version": plugin_bundle.PLUGIN_VERSION,
            "X-SkillForge-Plugin-SHA256": f"sha256:{digest}",
        },
    )


@router.get("/plugin/skillforge-codex/bundle")
async def skillforge_codex_plugin_bundle():
    return _skillforge_codex_plugin_bundle_response(include_content=True)


@router.head("/plugin/skillforge-codex/bundle")
async def skillforge_codex_plugin_bundle_head():
    return _skillforge_codex_plugin_bundle_response(include_content=False)


@router.post("/projects/upload")
async def codex_upload_project_package(
    request: Request,
    manifest_json: str = Form(default=""),
    package_hash: str = Form(default=""),
    sf_auto_build_json: str = Form(default=""),
    package: UploadFile = File(...),
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    package_bytes = await package.read()
    sf_auto_build = project_service.parse_project_form_json_dict(sf_auto_build_json, field="sf_auto_build_json")
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.project.submit",
        target_type="project",
        detail={
            "package_filename": package.filename,
            "package_bytes": len(package_bytes),
            "package_hash": package_hash,
            "sf_auto_build": sf_auto_build or None,
        },
    )
    item = await project_service.upload_project_package(
        db,
        principal.user,
        package_bytes=package_bytes,
        manifest_raw=manifest_json,
        package_hash=package_hash,
        submit_metadata={"sf_auto_build": sf_auto_build} if sf_auto_build else None,
    )
    return {
        "ok": True,
        "project": item,
        "project_id": item.get("id"),
        "entry_url": item.get("entry_url"),
        "asset_version": item.get("asset_version"),
        "runtime": item.get("runtime"),
        "package_hash": item.get("package_hash") or package_hash,
        **_project_web_links(item.get("id")),
    }


@router.post("/projects/auto-register")
async def codex_auto_register_project(
    request: Request,
    body: CodexProjectAutoRegisterRequest,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.project.auto_register",
        target_type="project",
        target_id=str((body.manifest or {}).get("project_id") or (body.manifest or {}).get("id") or ""),
        detail={"source": body.source or "sf_project_submit", "package_hash": body.package_hash},
    )
    item = await project_service.auto_register_project(db, principal.user, body.model_dump())
    return {
        "ok": True,
        "project": item,
        "project_id": item.get("id"),
        "entry_url": item.get("entry_url"),
        "runtime": item.get("runtime"),
        **_project_web_links(item.get("id")),
    }


@router.get("/projects/{project_id}/status")
async def codex_project_status(
    request: Request,
    project_id: str,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.project.status", target_type="project", target_id=project_id)
    item = await project_service.get_project(db, principal.user, project_id)
    latest_run = item.get("latest_run") if isinstance(item.get("latest_run"), dict) else None
    latest_run_id = latest_run.get("id") if latest_run else None
    return {
        "ok": True,
        "project": item,
        "project_id": item.get("id"),
        "current_version_id": item.get("current_version_id"),
        "latest_run": item.get("latest_run"),
        "runs": item.get("runs") or [],
        "runtime": item.get("runtime"),
        "entry_url": item.get("entry_url"),
        **_project_web_links(item.get("id"), latest_run_id),
    }


@router.post("/projects/{project_id}/service/invoke")
async def codex_project_service_invoke(
    request: Request,
    project_id: str,
    body: CodexProjectServiceInvokeRequest,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.project.service.invoke",
        target_type="project",
        target_id=project_id,
        detail={
            "request_id": body.request_id,
            "input_keys": sorted(str(key) for key in (body.input or {}).keys())[:30],
            "params_keys": sorted(str(key) for key in (body.params or {}).keys())[:30],
            "capability": body.capability or body.capability_key or body.key,
            "auto_analyze": body.auto_analyze,
            "has_manual_output": bool(body.output),
        },
    )
    result = await project_service.invoke_project_service(db, principal.user, project_id, body.model_dump())
    run_id = result.get("project_run_id")
    return {
        "ok": bool(result.get("ok")),
        **result,
        **_project_web_links(project_id, run_id),
    }


@router.get("/projects/runs/{project_run_id}/logs")
async def codex_project_run_logs(
    request: Request,
    project_run_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ingress_cursor: str | None = Query(default=None, max_length=300),
    capability_cursor: str | None = Query(default=None, max_length=300),
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.project.logs",
        target_type="project_run",
        target_id=project_run_id,
        detail={"limit": limit, "offset": offset, "ingress_cursor": bool(ingress_cursor), "capability_cursor": bool(capability_cursor)},
    )
    trace = await project_service.get_project_run_trace(
        db,
        principal.user,
        project_run_id,
        limit=limit,
        offset=offset,
        ingress_cursor=ingress_cursor,
        capability_cursor=capability_cursor,
    )
    project = trace.get("project") if isinstance(trace.get("project"), dict) else {}
    return {"ok": True, **trace, **_project_web_links(project.get("id"), project_run_id)}


@router.post("/projects/runs/{project_run_id}/assets")
async def codex_upload_project_run_asset(
    request: Request,
    project_run_id: str,
    metadata_json: str = Form(default=""),
    file: UploadFile = File(...),
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    metadata = project_service.parse_project_form_json_dict(metadata_json, field="metadata_json")
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.project.asset.upload",
        target_type="project_run",
        target_id=project_run_id,
        detail={
            "file_name": file.filename or "asset",
            "mime_type": file.content_type,
            "byte_size": len(content),
            "metadata_keys": sorted(str(key) for key in metadata.keys())[:30],
        },
    )
    result = await project_service.upload_project_run_asset(
        db,
        principal.user,
        project_run_id,
        file_name=file.filename or "asset",
        mime_type=file.content_type,
        content=content,
        metadata=metadata,
    )
    asset = result.get("asset") if isinstance(result.get("asset"), dict) else {}
    return {
        **result,
        "project_run_id": project_run_id,
        **_project_web_links(asset.get("project_id"), project_run_id),
    }


@router.get("/projects/runs/{project_run_id}/assets")
async def codex_list_project_run_assets(
    request: Request,
    project_run_id: str,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.project.asset.list",
        target_type="project_run",
        target_id=project_run_id,
    )
    result = await project_service.list_project_run_assets(db, principal.user, project_run_id)
    project = result.get("project") if isinstance(result.get("project"), dict) else {}
    return {"ok": True, **result, **_project_web_links(project.get("id"), project_run_id)}


@router.post("/previews")
async def create_output_preview(
    body: OutputPreviewRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    text, content_type, parsed_json = _as_preview_text(body.content, body.content_type)
    if len(text) > MAX_PREVIEW_CONTENT_CHARS:
        raise AppError(
            "PREVIEW_TOO_LARGE",
            413,
            {
                "max_chars": MAX_PREVIEW_CONTENT_CHARS,
                "actual_chars": len(text),
                "hint": "请先压缩输出，或只上传报告、待办摘要和关键证据。",
            },
        )
    title = _safe_preview_title(
        body.title,
        content_type=content_type,
        parsed_json=parsed_json,
        source_name=body.source_name,
    )
    row = CodexOutputPreview(
        id=service.new_id("preview"),
        user_id=principal.user.id,
        cli_session_id=principal.session.id,
        title=title,
        source_name=str(body.source_name or "").strip()[:255] or None,
        content_type=content_type,
        content_text=text,
        summary_json=_preview_summary(parsed_json, text, content_type),
        expires_at=now_bjt() + timedelta(hours=body.expires_in_hours),
    )
    db.add(row)
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.preview.create",
        target_type="codex_output_preview",
        target_id=row.id,
        detail={"content_type": content_type, "size_chars": len(text)},
    )
    await db.flush()
    return _serialize_preview(row, request)


@router.get("/previews/{preview_id}")
async def get_output_preview(
    preview_id: str,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(CodexOutputPreview, preview_id)
    if not row or row.expires_at <= now_bjt():
        raise AppError("NOT_FOUND", 404)
    return HTMLResponse(_render_preview_page(row))


@router.post("/previews/{preview_id}/apply")
async def apply_output_preview(
    preview_id: str,
    body: PreviewApplyRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(CodexOutputPreview, preview_id)
    if not row or row.expires_at <= now_bjt():
        raise AppError("NOT_FOUND", 404)
    if row.user_id != principal.user.id and not role_matches_any(principal.user, ("admin", "ai_engineer")):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    if row.content_type != "json":
        raise AppError("PARAM_INVALID", 400, {"detail": "只有 JSON 预览可以 apply"})
    try:
        output = json.loads(row.content_text)
    except json.JSONDecodeError as exc:
        raise AppError("PARAM_INVALID", 400, {"detail": f"preview content 不是合法 JSON: {exc}"}) from exc
    validation = _validate_output_contract(output)
    skill_id = body.skill_id or (
        output.get("_skillforge_meta", {}).get("skill_id")
        if isinstance(output.get("_skillforge_meta"), dict)
        else None
    )
    if not skill_id:
        skill_id = output.get("skill_id") if isinstance(output.get("skill_id"), str) else None
    if not skill_id:
        raise AppError("PARAM_INVALID", 400, {"detail": "apply 需要 skill_id，或输出中包含 _skillforge_meta.skill_id"})
    await require_skill_access(db, str(skill_id), principal.user, "execute")
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.preview.apply",
        target_type="codex_output_preview",
        target_id=preview_id,
        detail={"real": body.real, "skill_id": skill_id},
    )
    summary = {
        **validation["summary"],
        "preview_id": preview_id,
        "skill_id": skill_id,
        "would_create": {
            "reports": validation["summary"]["report_count"],
            "todos": validation["summary"]["todo_count"],
            "actions": validation["summary"]["action_count"],
            "notifications": validation["summary"]["notification_count"],
        },
    }
    if not body.real:
        return {
            "ok": validation["ok"],
            "dry_run": True,
            "summary": summary,
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        }
    if not validation["ok"]:
        raise AppError("OUTPUT_VALIDATE_FAILED", 422, {"errors": validation["errors"], "warnings": validation["warnings"]})
    if not body.idempotency_key:
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", 400)
    from app.execution.router import SubmitResultRequest, persist_submitted_result

    result = await persist_submitted_result(
        db,
        SubmitResultRequest(
            skill_id=str(skill_id),
            output=output,
            params=body.params,
            triggered_by=body.triggered_by or f"codex.preview.apply:{principal.user.id}",
            idempotency_key=body.idempotency_key,
            run_id=body.run_id,
            run_mode=body.run_mode or "manual_real",
            parent_run_id=body.parent_run_id,
            batch_id=body.batch_id,
            data_provenance=body.data_provenance,
            sample_used=body.sample_used,
        ),
        request=request,
        actor=principal.user.id,
    )
    return {
        "ok": True,
        "dry_run": False,
        "preview_id": preview_id,
        "summary": summary,
        "result": result,
    }


@router.post("/output/validate")
async def validate_output(
    body: OutputValidateRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
):
    await record_codex_cli_usage(request, principal, action="codex.output.validate")
    return _validate_output_contract(body.output, max_bytes=body.max_bytes)


@router.get("/skills")
async def list_skills(
    request: Request,
    scope: str = Query("visible", pattern="^(visible|editable|publishable|mine|department)$"),
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.skill.list", detail={"scope": scope})
    return {"items": await service.list_visible_skills(db, principal.user, scope)}


@router.post("/skills/local-status")
async def local_status(
    body: LocalSkillStatusRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.skill.local_status", detail={"item_count": len(body.items)})
    items = []
    for item in body.items[:200]:
        skill = await db.get(Skill, item.skill_id)
        if not skill:
            items.append({**item.model_dump(), "status": "never_uploaded", "remote_head": None})
            continue
        perms = await get_skill_permissions(db, skill, principal.user)
        if not perms.get("read"):
            items.append({**item.model_dump(), "status": "no_permission", "remote_head": None})
            continue
        logs = git_service.log(skill_id=item.skill_id, max_count=1) if git_service.skill_exists(item.skill_id) else []
        remote_head = logs[0]["hash_full"] if logs else skill.git_commit
        last_submission = (
            await db.execute(
                select(CodexSkillSubmission)
                .where(CodexSkillSubmission.skill_id == item.skill_id)
                .order_by(CodexSkillSubmission.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if item.base_commit and remote_head and item.base_commit != remote_head:
            status = "remote_newer"
        elif (
            item.package_hash
            and last_submission
            and last_submission.package_hash == item.package_hash
            and (not remote_head or last_submission.git_commit == remote_head)
        ):
            status = "synced"
        elif item.package_hash:
            status = "modified_not_submitted"
        else:
            status = "synced"
        items.append({**item.model_dump(), "status": status, "remote_head": remote_head, "permissions": perms})
    return {"items": items}


@router.get("/skills/{skill_id}/remote-status")
async def remote_status(
    skill_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.skill.remote_status", target_type="skill", target_id=skill_id)
    skill = await require_skill_access(db, skill_id, principal.user, "read")
    logs = git_service.log(skill_id=skill_id, max_count=20) if git_service.skill_exists(skill_id) else []
    review = (
        await db.execute(
            select(Review).where(Review.skill_id == skill_id).order_by(Review.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    return {
        "skill_id": skill_id,
        "name": skill.name,
        "git_commit": skill.git_commit,
        "history": logs,
        "latest_review": {
            "id": review.id,
            "status": review.status,
            "created_at": isoformat_bjt(review.created_at),
        } if review else None,
    }


@router.get("/skills/{skill_id}/package")
async def pull_skill_package(
    skill_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.skill.pull", target_type="skill", target_id=skill_id)
    skill = await require_skill_access(db, skill_id, principal.user, "read")
    package, digest, manifest = service.build_skill_pull_package(skill)
    remote_head = str(manifest.get("remote_head") or manifest.get("base_commit") or "")
    file_count = str(manifest.get("file_count") or len(manifest.get("paths") or []))
    return Response(
        content=package,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{skill_id}.tar.gz"',
            "X-SkillForge-Skill-ID": skill_id,
            "X-SkillForge-Package-Hash": digest,
            "X-SkillForge-Skill-Id": skill_id,
            "X-SkillForge-Skill-Commit": remote_head,
            "X-SkillForge-Skill-File-Count": file_count,
            "X-SkillForge-Remote-Head": remote_head,
        },
    )


@router.post("/skills/{skill_id}/conflict-check")
async def conflict_check(
    skill_id: str,
    body: ConflictCheckRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.skill.conflict_check", target_type="skill", target_id=skill_id)
    await require_skill_access(db, skill_id, principal.user, "read")
    logs = git_service.log(skill_id=skill_id, max_count=1) if git_service.skill_exists(skill_id) else []
    remote_head = logs[0]["hash_full"] if logs else None
    return {
        "skill_id": skill_id,
        "remote_head": remote_head,
        "base_commit": body.base_commit,
        "conflict": bool(remote_head and body.base_commit and remote_head != body.base_commit),
    }


@router.get("/skills/{skill_id}/publish-status")
async def skill_publish_status(
    skill_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.skill.publish_status", target_type="skill", target_id=skill_id)
    skill = await require_skill_access(db, skill_id, principal.user, "read")
    logs = git_service.log(skill_id=skill_id, max_count=1) if git_service.skill_exists(skill_id) else []
    remote_head = logs[0]["hash_full"] if logs else skill.git_commit
    review = (
        await db.execute(
            select(Review).where(Review.skill_id == skill_id).order_by(Review.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    sync_attempts = (
        await db.execute(
            select(SkillSyncAttempt)
            .where(SkillSyncAttempt.skill_id == skill_id)
            .order_by(SkillSyncAttempt.created_at.desc(), SkillSyncAttempt.id.desc())
            .limit(10)
        )
    ).scalars().all()
    schedules = (
        await db.execute(
            select(NodeScheduleConfig)
            .where(NodeScheduleConfig.skill_id == skill_id)
            .order_by(NodeScheduleConfig.pushed_at.desc(), NodeScheduleConfig.id.desc())
        )
    ).scalars().all()
    last_run = (
        await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.skill_id == skill_id)
            .order_by(ExecutionRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "skill_id": skill_id,
        "name": skill.name,
        "status": skill.status,
        "trigger_type": skill.trigger_type,
        "trigger_expression": skill.trigger_expression,
        "git_commit": skill.git_commit,
        "remote_head": remote_head,
        "latest_review": {
            "id": review.id,
            "status": review.status,
            "reviewer": review.reviewer,
            "created_at": isoformat_bjt(review.created_at),
            "decided_at": isoformat_bjt(review.decided_at),
        } if review else None,
        "sync_attempts": [
            {
                "id": row.id,
                "instance_id": row.instance_id,
                "status": row.status,
                "version_tag": row.version_tag,
                "error": row.error,
                "created_at": isoformat_bjt(row.created_at),
                "completed_at": isoformat_bjt(row.completed_at),
            }
            for row in sync_attempts
        ],
        "schedules": [
            {
                "instance_id": row.instance_id,
                "cron_expression": row.cron_expression,
                "config_version": row.config_version,
                "ack_ok": row.ack_ok,
                "runtime_backend": row.runtime_backend,
                "pushed_at": isoformat_bjt(row.pushed_at),
            }
            for row in schedules
        ],
        "last_run": _serialize_execution_run(last_run) if last_run else None,
    }


@router.get("/skills/{skill_id}/schedule")
async def get_skill_schedule(
    skill_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.skill.schedule.get", target_type="skill", target_id=skill_id)
    skill = await require_skill_access(db, skill_id, principal.user, "read")
    schedules = (
        await db.execute(
            select(NodeScheduleConfig)
            .where(NodeScheduleConfig.skill_id == skill_id)
            .order_by(NodeScheduleConfig.instance_id.asc())
        )
    ).scalars().all()
    return {
        "skill_id": skill_id,
        "trigger_type": skill.trigger_type,
        "trigger_expression": skill.trigger_expression,
        "status": "active" if skill.trigger_type == "cron" and skill.trigger_expression else "stopped",
        "node_schedules": [
            {
                "instance_id": row.instance_id,
                "cron_expression": row.cron_expression,
                "config_version": row.config_version,
                "ack_ok": row.ack_ok,
                "runtime_backend": row.runtime_backend,
                "runtime_config": row.runtime_config,
                "pushed_at": isoformat_bjt(row.pushed_at),
            }
            for row in schedules
        ],
    }


@router.post("/skills/{skill_id}/schedule")
async def update_skill_schedule_from_codex(
    skill_id: str,
    body: ScheduleUpdateRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.skill.schedule.update",
        target_type="skill",
        target_id=skill_id,
        detail={"action": body.action, "cron": body.cron_expression},
    )
    await require_skill_access(db, skill_id, principal.user, "publish")
    if body.action == "sync":
        from app.execution.sync_service import sync_service

        result = await sync_service.push_schedules_to_targets(skill_ids=[skill_id])
        return {"skill_id": skill_id, "action": "sync", "result": result}
    from app.tasktree.schedule_query import update_skill_schedule

    return await update_skill_schedule(db, skill_id, body.action, body.cron_expression, principal.user.id)


@router.post("/skills/{skill_id}/sync")
async def sync_skill_files_from_codex(
    skill_id: str,
    body: SkillSyncRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.skill.sync",
        target_type="skill",
        target_id=skill_id,
        detail={"instance_ids": body.instance_ids or [], "department": body.department},
    )
    await require_skill_access(db, skill_id, principal.user, "publish")

    from app.execution.sync_service import sync_service

    return await sync_service.create_and_run_sync_job(
        skill_id,
        trigger="codex_cli",
        actor=principal.user,
        target_instance_ids=body.instance_ids,
        department=body.department,
        push_git=body.push_git,
    )


@router.post("/skill/debug-runs")
async def create_debug_run(
    body: DebugRunRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.debug_run.create",
        target_type="skill",
        target_id=body.skill_id,
        detail={"run_mode": body.run_mode, "requested_tool_count": len(body.requested_tools)},
    )
    skill = await db.get(Skill, body.skill_id)
    if skill:
        perms = await get_skill_permissions(db, skill, principal.user)
        if not (perms.get("execute") or perms.get("edit") or perms.get("publish") or perms.get("review")):
            raise AppError("SKILL_ACCESS_DENIED", 403)
    run, raw_token = await service.create_debug_run(
        db,
        principal,
        skill_id=body.skill_id,
        run_mode=body.run_mode,
        package_hash=body.package_hash,
        manifest=body.manifest,
        input_json=body.input,
        requested_tools=body.requested_tools,
        limits=body.limits,
    )
    skill_git_commit_full = str((body.manifest or {}).get("base_commit") or "").strip()
    department = str(getattr(skill, "department", "") or principal.user.department or "").strip()
    runtime_run_token = issue_run_token(
        skill_id=body.skill_id,
        run_id=run.id,
        instance_id=None,
        skill_git_commit_full=skill_git_commit_full,
        department=department or None,
    )
    return {
        **serialize_debug_run(run),
        "run_token": raw_token,
        "runtime_run_token": runtime_run_token,
        "skill_git_commit_full": skill_git_commit_full,
        "token_type": "Bearer",
    }


@router.get("/skill/debug-runs/{run_id}")
async def get_debug_run(
    run_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.debug_run.get", target_type="codex_debug_run", target_id=run_id)
    run = await db.get(CodexDebugRun, run_id)
    if not run or run.user_id != principal.user.id:
        raise AppError("NOT_FOUND", 404)
    return serialize_debug_run(run)


@router.post("/skill/debug-runs/{run_id}/complete")
async def complete_debug_run(
    run_id: str,
    body: CompleteDebugRunRequest,
    principal: service.RunPrincipal = Depends(require_any_principal),
    db: AsyncSession = Depends(get_db),
):
    if not isinstance(principal, service.RunPrincipal) or principal.run.id != run_id:
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    status = body.status if body.status in {"completed", "failed", "cancelled"} else "completed"
    principal.run.status = status
    principal.run.completed_at = now_bjt()
    principal.run.result_json = {
        "duration_ms": body.duration_ms,
        "output": body.output,
        "stdout_tail": service.safe_tail(body.stdout_tail),
        "stderr_tail": service.safe_tail(body.stderr_tail),
        "test_summary": body.test_summary or {},
    }
    principal.token.revoked_at = now_bjt()
    await db.flush()
    return serialize_debug_run(principal.run)


@router.post("/mcp/call")
async def mcp_call(
    body: McpCallRequest,
    principal: service.CliPrincipal | service.RunPrincipal | service.RuntimePrincipal = Depends(require_any_principal),
    db: AsyncSession = Depends(get_db),
):
    result = await service.call_mcp_tool(
        db,
        principal,
        server=body.server,
        tool=body.tool,
        arguments=body.arguments,
        skill_id=body.skill_id,
        run_id=body.run_id,
        run_mode=body.run_mode,
        dry_run=body.dry_run,
        idempotency_key=body.idempotency_key,
    )
    await invalidate_sf_cache()
    return result


@router.get("/runs")
async def list_codex_runs(
    request: Request,
    skill_id: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.runs.list", detail={"skill_id": skill_id, "status": status})
    access_filter = await service.build_skill_access_filter(db, principal.user, "read")
    stmt = select(ExecutionRun).join(Skill, Skill.id == ExecutionRun.skill_id).where(access_filter)
    count_stmt = select(func.count(ExecutionRun.id)).join(Skill, Skill.id == ExecutionRun.skill_id).where(access_filter)
    if skill_id:
        stmt = stmt.where(ExecutionRun.skill_id == skill_id)
        count_stmt = count_stmt.where(ExecutionRun.skill_id == skill_id)
    if status:
        stmt = stmt.where(ExecutionRun.status == status)
        count_stmt = count_stmt.where(ExecutionRun.status == status)
    total = int((await db.execute(count_stmt)).scalar() or 0)
    rows = (
        await db.execute(
            stmt.order_by(ExecutionRun.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return {"total": total, "page": page, "page_size": page_size, "items": [_serialize_execution_run(row) for row in rows]}


@router.get("/runs/{run_id}")
async def get_codex_run(
    run_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.runs.get", target_type="execution_run", target_id=run_id)
    run = await _require_codex_run_access(db, principal.user, run_id)
    return _serialize_execution_run(run)


@router.get("/runs/{run_id}/steps")
async def get_codex_run_steps(
    run_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.runs.steps", target_type="execution_run", target_id=run_id)
    await _require_codex_run_access(db, principal.user, run_id)
    rows = (
        await db.execute(
            select(ExecutionStep).where(ExecutionStep.run_id == run_id).order_by(ExecutionStep.step_order.asc(), ExecutionStep.id.asc())
        )
    ).scalars().all()
    return {"run_id": run_id, "items": [_serialize_execution_step(row) for row in rows]}


@router.get("/runs/{run_id}/result")
async def get_codex_run_result(
    run_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.runs.result", target_type="execution_run", target_id=run_id)
    run = await _require_codex_run_access(db, principal.user, run_id)
    decisions = (
        await db.execute(
            select(DecisionLog).where(DecisionLog.run_id == run_id).order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
        )
    ).scalars().all()
    todo_count = int((await db.scalar(select(func.count(DecisionRequest.id)).where(DecisionRequest.run_id == run_id))) or 0)
    latest = decisions[0] if decisions else None
    return {
        "run": _serialize_execution_run(run),
        "decision_count": len(decisions),
        "todo_count": todo_count,
        "latest_output": latest.output_result if latest else None,
        "decisions": [_serialize_execution_decision(row) for row in decisions],
    }


@router.get("/runs/{run_id}/artifacts")
async def get_codex_run_artifacts(
    run_id: str,
    request: Request,
    kind: str | None = Query(None),
    include_content: bool = Query(False),
    max_bytes: int = Query(1024 * 1024, ge=1, le=10 * 1024 * 1024),
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.runs.artifacts", target_type="execution_run", target_id=run_id)
    run = await _require_codex_run_access(db, principal.user, run_id)
    stmt = select(ExecutionArtifact).where(ExecutionArtifact.run_id == run_id).order_by(ExecutionArtifact.created_at.desc(), ExecutionArtifact.id.desc())
    if kind:
        stmt = stmt.where(ExecutionArtifact.kind == kind)
    rows = (await db.execute(stmt)).scalars().all()
    items = []
    for row in rows:
        item = service._serialize_execution_artifact(row, run)
        if include_content:
            content, omitted, reason = service._read_artifact_json_content(row, max_bytes=max_bytes)
            item["content_omitted"] = omitted
            if reason:
                item["content_omitted_reason"] = reason
            if not omitted:
                if isinstance(content, (dict, list)):
                    item["content_json"] = content
                else:
                    item["content_text"] = str(content)
        items.append(item)
    return {"run_id": run_id, "skill_id": run.skill_id, "count": len(items), "items": items}


@router.get("/runs/{run_id}/logs")
async def get_codex_run_logs(
    run_id: str,
    request: Request,
    stream: str | None = Query(None),
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.runs.logs", target_type="execution_run", target_id=run_id)
    await _require_codex_run_access(db, principal.user, run_id)
    stmt = select(ExecutionRunLog).where(ExecutionRunLog.run_id == run_id).order_by(ExecutionRunLog.created_at.asc(), ExecutionRunLog.id.asc())
    if stream:
        stmt = stmt.where(ExecutionRunLog.stream == stream)
    rows = (await db.execute(stmt)).scalars().all()
    return {"run_id": run_id, "count": len(rows), "items": [_serialize_execution_log(row) for row in rows]}


@router.get("/runs/{run_id}/diagnose")
async def get_codex_run_diagnose(
    run_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.runs.diagnose", target_type="execution_run", target_id=run_id)
    await _require_codex_run_access(db, principal.user, run_id)
    from app.tasktree.ai_diagnose import diagnose_failure

    diag = await diagnose_failure(db, run_id, user_id=principal.user.id, department=principal.user.department)
    return {"run_id": run_id, "diagnosis": diag}


@router.post("/skills/{skill_id}/run")
async def run_skill_from_codex(
    skill_id: str,
    body: CodexRunSkillRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.skill.run",
        target_type="skill",
        target_id=skill_id,
        detail={"run_mode": body.run_mode, "sandbox": body.sandbox},
    )
    await require_skill_access(db, skill_id, principal.user, "execute")

    from app.execution.execution_service import execution_service

    return await execution_service.execute_skill(
        skill_id=skill_id,
        params=body.params,
        sandbox=body.sandbox,
        triggered_by=f"codex:{principal.user.id}",
        run_mode=body.run_mode,
        parent_run_id=body.parent_run_id,
        data_proofs=body.data_provenance,
        sample_used=body.sample_used,
    )


@router.post("/skills/submissions")
async def submit_skill_package(
    request: Request,
    package: UploadFile = File(...),
    manifest_json: str = Form("{}"),
    package_hash: str = Form(""),
    base_commit: str | None = Form(None),
    message: str = Form(""),
    force_submit: bool = Form(False),
    force_reason: str = Form(""),
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    manifest = parse_json_form(manifest_json, field_name="manifest_json")
    security_settings = await get_platform_security_settings(db)
    await record_codex_cli_usage(
        request,
        principal,
        action="codex.skill.submit.request",
        target_type="skill",
        target_id=str((manifest.get("skill_id") or manifest.get("name") or "") if isinstance(manifest, dict) else ""),
        detail={"package_hash": package_hash, "message": message[:120], "force_submit": bool(force_submit)},
    )
    effective_force_submit = bool(force_submit or security_settings.bypass_review_direct_publish)
    effective_force_reason = (
        force_reason.strip()
        if force_submit
        else (
            SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH_REASON
            if security_settings.bypass_review_direct_publish
            else ""
        )
    )
    package_bytes = await package.read()
    submission = await service.create_or_update_skill_from_package(
        db,
        user=principal.user,
        package_bytes=package_bytes,
        manifest=manifest,
        supplied_package_hash=package_hash,
        base_commit=base_commit,
        message=message,
        force_review_submit=effective_force_submit,
        force_review_reason=effective_force_reason,
    )
    response = serialize_submission(submission)
    if security_settings.bypass_review_direct_publish and submission.review_id:
        await db.commit()
        try:
            auto_publish = await auto_approve_submission_after_commit(
                submission.id,
                verify_after_sync=True,
            )
            response = auto_publish
        except Exception as exc:  # noqa: BLE001
            logger.warning("codex submission auto publish failed submission={} err={}", submission.id, exc)
            response = {**response, "auto_publish": {"ok": False, "error": str(exc)[:300]}}
    return response


@router.get("/skills/submissions/{submission_id}")
async def submission_status(
    submission_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.submission.status", target_type="codex_submission", target_id=submission_id)
    row = await db.get(CodexSkillSubmission, submission_id)
    if not row:
        raise AppError("NOT_FOUND", 404)
    if row.user_id != principal.user.id and not role_matches_any(principal.user, ("admin",)):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    return serialize_submission(row)


@router.post("/skills/submissions/{submission_id}/approve")
async def approve_submission(
    submission_id: str,
    request: Request,
    body: ApproveSubmissionRequest | None = None,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.submission.approve", target_type="codex_submission", target_id=submission_id)
    row = await db.get(CodexSkillSubmission, submission_id)
    if not row:
        raise AppError("NOT_FOUND", 404)
    if row.user_id != principal.user.id and not role_matches_any(principal.user, ("admin",)):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    if not row.review_id:
        raise AppError("REVIEW_NOT_FOUND", 404)
    skill = await require_skill_access(db, row.skill_id, principal.user, "publish")
    review = await db.get(Review, row.review_id)
    if not review:
        raise AppError("REVIEW_NOT_FOUND", 404)
    if review.skill_id != row.skill_id:
        raise AppError("PARAM_INVALID", 400, {"reason": "submission review skill mismatch"})
    if row.git_commit and review.git_commit_after and row.git_commit != review.git_commit_after:
        raise AppError(
            "PACKAGE_CONFLICT",
            409,
            {"reason": "submission commit does not match review", "submission_commit": row.git_commit, "review_commit": review.git_commit_after},
        )
    perms = await get_skill_permissions(db, skill, principal.user)
    if not perms.get("publish"):
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    from app.reviews import service as review_service

    runtime_instance_id, cron_expression = _submission_publish_config(row, body)
    approve_result = await review_service.approve_review(
        db,
        row.review_id,
        principal.user.id,
        runtime_instance_id=runtime_instance_id,
        cron_expression=cron_expression,
        verify_after_sync=body.verify_after_sync if body else True,
        actor=principal.user,
        static_check_override=body.static_check_override if body else False,
        static_check_override_reason=body.static_check_override_reason if body else "",
    )
    row.status = "approved"
    await db.flush()
    return {**serialize_submission(row), "review": approve_result}


@router.get("/reviews")
async def list_codex_reviews(
    request: Request,
    status: str | None = Query(None),
    skill_id: str | None = Query(None),
    reviewer: str | None = Query(None),
    submitter: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.review.list")
    from app.reviews import service as review_service

    department = None if role_matches_any(principal.user, ("admin", "ai_engineer")) or bool(getattr(principal.user, "can_view_all", False)) else principal.user.department
    return await review_service.list_reviews(
        db,
        status=status,
        skill_id=skill_id,
        reviewer=principal.user.id if reviewer == "me" else reviewer,
        submitter=principal.user.id if submitter == "me" else submitter,
        page=page,
        page_size=page_size,
        department=department,
    )


async def _require_codex_review_access(db: AsyncSession, user: User, review_id: int, *, write: bool = False) -> dict:
    from app.reviews import service as review_service

    data = await review_service.get_review(db, review_id)
    if role_matches_any(user, ("admin", "ai_engineer")) or bool(getattr(user, "can_view_all", False)):
        return data
    skill_id = str(data.get("skill_id") or "")
    if skill_id and not skill_id.startswith("playbook:"):
        skill = await db.get(Skill, skill_id)
        if skill:
            perms = await get_skill_permissions(db, skill, user)
            if write:
                if perms.get("review") or data.get("reviewer") == user.id:
                    return data
            elif perms.get("read") or data.get("submitter") == user.id or data.get("reviewer") == user.id:
                return data
    if data.get("submitter") == user.id or data.get("reviewer") == user.id:
        return data
    raise AppError("AUTH_PERMISSION_DENIED", 403)


@router.get("/reviews/{review_id}")
async def get_codex_review(
    review_id: int,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.review.get", target_type="review", target_id=str(review_id))
    return await _require_codex_review_access(db, principal.user, review_id)


@router.post("/reviews/{review_id}/comment")
async def comment_codex_review(
    review_id: int,
    body: ReviewCommentRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.review.comment", target_type="review", target_id=str(review_id))
    await _require_codex_review_access(db, principal.user, review_id)
    from app.reviews import service as review_service

    return await review_service.add_comment(
        db,
        review_id,
        principal.user.id,
        body.content,
        file_path=body.file_path,
        line_number=body.line_number,
        side=body.side,
    )


@router.post("/reviews/{review_id}/request-changes")
async def request_changes_codex_review(
    review_id: int,
    body: ReviewRequestChangesRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.review.request_changes", target_type="review", target_id=str(review_id))
    await _require_codex_review_access(db, principal.user, review_id, write=True)
    from app.reviews import service as review_service

    return await review_service.reject_review(
        db,
        review_id,
        principal.user.id,
        body.reason,
        reject_reason=body.reject_reason or "request_changes",
    )


@router.post("/reviews/{review_id}/approve")
async def approve_codex_review(
    review_id: int,
    request: Request,
    body: ApproveSubmissionRequest | None = None,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.review.approve", target_type="review", target_id=str(review_id))
    data = await _require_codex_review_access(db, principal.user, review_id, write=True)
    if data.get("skill_id") and not str(data["skill_id"]).startswith("playbook:"):
        await require_skill_access(db, str(data["skill_id"]), principal.user, "publish")
    from app.reviews import service as review_service

    return await review_service.approve_review(
        db,
        review_id,
        principal.user.id,
        runtime_instance_id=body.runtime_instance_id if body else None,
        cron_expression=body.cron_expression if body else None,
        verify_after_sync=body.verify_after_sync if body else True,
        actor=principal.user,
        static_check_override=body.static_check_override if body else False,
        static_check_override_reason=body.static_check_override_reason if body else "",
    )


@router.post("/reviews/{review_id}/reject")
async def reject_codex_review(
    review_id: int,
    body: ReviewRequestChangesRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.review.reject", target_type="review", target_id=str(review_id))
    await _require_codex_review_access(db, principal.user, review_id, write=True)
    from app.reviews import service as review_service

    return await review_service.reject_review(
        db,
        review_id,
        principal.user.id,
        body.reason,
        reject_reason=body.reject_reason,
    )


@router.get("/market")
async def list_codex_market(
    request: Request,
    category: str | None = Query(None),
    certified_only: bool = Query(False),
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.market.list")
    from app.portal import market_service

    items = await market_service.list_market_skills(db, principal.user, category=category, certified_only=certified_only)
    return {"items": items, "total": len(items)}


@router.get("/market/{skill_id}")
async def get_codex_market_skill(
    skill_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.market.detail", target_type="skill", target_id=skill_id)
    skill = await require_skill_access(db, skill_id, principal.user, "read")
    return {
        "skill_id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "department": skill.department,
        "owner": skill.owner,
        "status": skill.status,
        "visibility": skill.visibility,
        "market_status": skill.market_status,
        "current_version": skill.current_version,
        "updated_at": isoformat_bjt(skill.updated_at),
    }


@router.get("/market/{skill_id}/metrics")
async def get_codex_market_metrics(
    skill_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.market.metrics", target_type="skill", target_id=skill_id)
    await require_skill_access(db, skill_id, principal.user, "read")
    from app.portal import market_service

    return await market_service.get_reuse_metrics(db, skill_id)


@router.post("/market/{skill_id}/certify")
async def certify_codex_market_skill(
    skill_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.market.certify", target_type="skill", target_id=skill_id)
    await require_skill_access(db, skill_id, principal.user, "publish")
    from app.portal import market_service

    return await market_service.submit_for_certification(db, skill_id, principal.user.id)


@router.put("/market/{skill_id}/visibility")
async def update_codex_market_visibility(
    skill_id: str,
    body: MarketVisibilityRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.market.visibility", target_type="skill", target_id=skill_id)
    await require_skill_access(db, skill_id, principal.user, "publish")
    from app.portal import market_service

    return await market_service.update_visibility(db, skill_id, body.market_status, principal.user.id)


@router.get("/market/{skill_id}/ratings")
async def list_codex_market_ratings(
    skill_id: str,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.market.ratings", target_type="skill", target_id=skill_id)
    await require_skill_access(db, skill_id, principal.user, "read")
    from app.portal.market_models import MarketRating

    rows = (
        await db.execute(
            select(MarketRating).where(MarketRating.skill_id == skill_id).order_by(MarketRating.updated_at.desc(), MarketRating.id.desc())
        )
    ).scalars().all()
    avg = round(sum(row.rating for row in rows) / len(rows), 2) if rows else None
    return {
        "skill_id": skill_id,
        "count": len(rows),
        "average_rating": avg,
        "items": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "rating": row.rating,
                "comment": row.comment,
                "created_at": isoformat_bjt(row.created_at),
                "updated_at": isoformat_bjt(row.updated_at),
            }
            for row in rows
        ],
    }


@router.post("/market/{skill_id}/ratings")
async def rate_codex_market_skill(
    skill_id: str,
    body: MarketRatingRequest,
    request: Request,
    principal: service.CliPrincipal = Depends(require_cli_principal),
    db: AsyncSession = Depends(get_db),
):
    await record_codex_cli_usage(request, principal, action="codex.market.rate", target_type="skill", target_id=skill_id)
    await require_skill_access(db, skill_id, principal.user, "read")
    from app.portal.market_models import MarketRating

    row = (
        await db.execute(
            select(MarketRating)
            .where(MarketRating.skill_id == skill_id)
            .where(MarketRating.user_id == principal.user.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        row = MarketRating(skill_id=skill_id, user_id=principal.user.id, rating=body.rating, comment=body.comment.strip() or None)
        db.add(row)
    else:
        row.rating = body.rating
        row.comment = body.comment.strip() or None
        row.updated_at = now_bjt()
    await db.flush()
    return {
        "id": row.id,
        "skill_id": row.skill_id,
        "user_id": row.user_id,
        "rating": row.rating,
        "comment": row.comment,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


async def auto_approve_submission_after_commit(
    submission_id: str,
    *,
    verify_after_sync: bool = True,
) -> dict:
    """Approve a Codex submission after the submit transaction is committed."""
    async with async_session_factory() as session:
        row = await session.get(CodexSkillSubmission, submission_id)
        if not row:
            raise AppError("NOT_FOUND", 404)
        if not row.review_id:
            raise AppError("REVIEW_NOT_FOUND", 404)
        review = await session.get(Review, row.review_id)
        if not review:
            raise AppError("REVIEW_NOT_FOUND", 404)
        if review.skill_id != row.skill_id:
            raise AppError("PARAM_INVALID", 400, {"reason": "submission review skill mismatch"})
        if row.git_commit and review.git_commit_after and row.git_commit != review.git_commit_after:
            raise AppError(
                "PACKAGE_CONFLICT",
                409,
                {
                    "reason": "submission commit does not match review",
                    "submission_commit": row.git_commit,
                    "review_commit": review.git_commit_after,
                },
            )

        runtime_instance_id, cron_expression = _submission_publish_config(row, None)

        from app.reviews import service as review_service

        approve_result = await review_service.approve_review_as_system(
            session,
            row.review_id,
            runtime_instance_id=runtime_instance_id,
            cron_expression=cron_expression,
            verify_after_sync=verify_after_sync,
        )
        row.status = "approved"
        await session.commit()
        return {
            **serialize_submission(row),
            "review": approve_result,
            "auto_publish": {
                "ok": True,
                "reason": "security.bypass_review_direct_publish",
            },
        }
