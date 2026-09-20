"""AIClaw 业务 API。"""

from __future__ import annotations

from datetime import datetime, timedelta
import html
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any
from urllib.parse import quote, unquote, urlparse

from fastapi import APIRouter, Depends, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import role_matches_any
from app.auth.dependencies import require_role, require_state_active, require_state_active_web_or_cli
from app.auth.models import User
from app.common.audit import audit
from app.common.cache import cached, invalidate_agent_cache
from app.common.exceptions import AppError
from app.common.ws_auth import WS_CODE_AUTH_REQUIRED, get_current_user_ws
from app.config import settings
from app.database import get_db
from app.execution.models import OpenClawInstance
from app.execution.sync_service import build_reload_endpoint_url
from app.org.models import OrgUnit

from app.common.time_utils import isoformat_bjt, now_bjt
from .bridge_registry import bridge_registry
from .client import AIClawClient
from .rate_limit import chat_send_rate_limiter
from .script_generator import (
    BRIDGE_SOURCE_PATH,
    BRIDGE_VERSION,
    build_skillforge_bridge_ws_url,
    compute_bridge_script_hash,
    read_bridge_source,
    render,
)
from .security import (
    clear_rotation_token,
    generate_enrollment_token,
    generate_rotation_token,
    hash_enrollment_token,
    verify_enrollment_token,
    verify_signature,
)

router = APIRouter()

_CACHE_KEY_EXCLUDES = frozenset({"db", "session", "request", "background_tasks", "current_user"})
_CACHE_SCOPE = ("current_user.id", "current_user.role", "current_user.department", "current_user.can_view_all")


def _path_cache_key(*names: str):
    def _builder(*args, **kwargs) -> str:
        return ":".join(str(kwargs.get(name) or "") for name in names)

    return _builder

AGENT_TERMINAL_ROLES = ("admin", "dept_admin")
TRAINING_TASKS_ALLOWED = {"lora", "qlora", "eval", "merge", "inference"}
AGENT_PURPOSE_ALLOWED = {"skill_runtime", "analysis", "training", "media", "mixed"}
SKILL_RUNTIME_AGENT_PURPOSES = {"skill_runtime", "mixed"}
AICLAW_ACTIVE_TRAINING_JOB_STATUSES = {"queued", "running", "evaluating", "unknown"}
MODEL_CHAT_DEPLOYMENT_STATUSES = {"canary", "active"}
TRAINING_LIFECYCLE_OPS = ("training.submit_job", "training.collect_result", "training.inference")
MODEL_CHAT_DEFAULT_MAX_NEW_TOKENS = 1536
MODEL_CHAT_ABSOLUTE_MAX_NEW_TOKENS = 4096
MODEL_CHAT_ARTIFACT_STATUS_OP = "training.artifact_status"


class AIClawInstanceRequest(BaseModel):
    id: str
    name: str
    department: str | None = None
    agent_type: str = "aiclaw"  # "aiclaw" | "hermes"
    gateway_url: str = ""
    reload_hook_url: str = ""
    reload_token: str = ""
    auth_token: str | None = None
    network_zone: str = "internal"
    is_active: bool = True
    is_platform_default: bool = False
    agent_purpose: str = "skill_runtime"


class ChatModelContextValidationRequest(BaseModel):
    model_context: dict[str, Any] | None = None


class SyncSkillTargetsRequest(BaseModel):
    """Skill 下发目标。

    - instance_ids 为空：按 Skill.department / 操作者部门自动选择终端。
    - instance_ids 非空：管理员可选任意终端；普通账号仍受部门边界限制。
    """

    instance_ids: list[str] | None = None
    department: str | None = None


class MediaH3BootstrapRequest(BaseModel):
    profile: str = "h3_all_modes_v1"
    bandwidth_limit_mbps: int = 3
    accept_license: bool = False
    force: bool = False
    dry_run: bool = False


def _short_commit(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text[:8] if text else None


def _version_tuple(value: str | None) -> tuple[int, ...]:
    text = str(value or "").strip().lstrip("v")
    parts: list[int] = []
    for part in text.split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts)


def _bridge_update_available(current: str | None) -> bool:
    if not current:
        return False
    current_tuple = _version_tuple(current)
    latest_tuple = _version_tuple(BRIDGE_VERSION)
    return bool(current_tuple and latest_tuple and current_tuple < latest_tuple)


def _instance_runtime_type(instance: OpenClawInstance) -> str:
    agent_type = (getattr(instance, "agent_type", "") or "").strip().lower()
    if agent_type == "hermes":
        return "hermes"
    gateway_kind = (getattr(instance, "bridge_gateway_kind", "") or "").strip().lower()
    if gateway_kind and gateway_kind != "unknown":
        return gateway_kind
    return agent_type or "aiclaw"


def _normalize_agent_purpose(value: str | None) -> str:
    purpose = (value or "skill_runtime").strip().lower()
    return purpose if purpose in AGENT_PURPOSE_ALLOWED else "skill_runtime"


def _normalize_reload_hook_url(value: str | None, *, default: str | None = None) -> str:
    candidate = str(value or "").strip() or str(default or "").strip()
    endpoint_url, error = build_reload_endpoint_url(candidate)
    if error or not endpoint_url:
        raise AppError("PARAM_INVALID", 400, {"detail": error or "INVALID_RELOAD_HOOK_URL"})
    return endpoint_url


def _attempt_git_commit(attempt: Any | None) -> str | None:
    if attempt is None:
        return None
    result = getattr(attempt, "result", None)
    if not isinstance(result, dict):
        return None
    for key in ("deployed_git_commit_full", "docker_git_commit_full", "git_commit_full"):
        value = str(result.get(key) or "").strip()
        if value:
            return value
    value = str(result.get("git_commit") or "").strip()
    return value or None


def _skill_id_from_device_skill(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("id") or item.get("skill_id") or item.get("name") or "").strip()


def _current_skill_git_commit(skill: Any) -> str | None:
    try:
        from app.skills.core.git_service import git_service

        logs = git_service.log(skill_id=skill.id, max_count=1)
        if logs:
            return str(logs[0].get("hash_full") or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 skill git 版本失败 skill={}: {}", getattr(skill, "id", ""), exc)
    return str(getattr(skill, "git_commit", "") or "").strip() or None


async def _enrich_agent_skills_with_git(
    db: AsyncSession,
    *,
    instance_id: str,
    items: list[Any],
) -> list[Any]:
    """Add SkillForge Git metadata to skills reported by a running terminal."""
    skill_ids = sorted({_skill_id_from_device_skill(item) for item in items if _skill_id_from_device_skill(item)})
    if not skill_ids:
        return items

    from app.execution.models import SkillSyncAttempt
    from app.skills.core.models import Skill

    skill_rows = (await db.execute(select(Skill).where(Skill.id.in_(skill_ids)))).scalars().all()
    skill_map = {row.id: row for row in skill_rows}
    if not skill_map:
        return items

    attempt_rows = (await db.execute(
        select(SkillSyncAttempt)
        .where(SkillSyncAttempt.instance_id == instance_id)
        .where(SkillSyncAttempt.skill_id.in_(list(skill_map.keys())))
        .where(SkillSyncAttempt.status == "succeeded")
        .order_by(SkillSyncAttempt.completed_at.desc().nullslast(), SkillSyncAttempt.id.desc())
    )).scalars().all()
    latest_attempt: dict[str, SkillSyncAttempt] = {}
    for attempt in attempt_rows:
        latest_attempt.setdefault(attempt.skill_id, attempt)

    enriched: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            enriched.append(item)
            continue
        sid = _skill_id_from_device_skill(item)
        skill = skill_map.get(sid)
        if skill is None:
            enriched.append(item)
            continue

        attempt = latest_attempt.get(sid)
        deployed_commit = _attempt_git_commit(attempt)
        current_commit = _current_skill_git_commit(skill)
        data = dict(item)
        data.setdefault("display_name", skill.display_name)
        data.setdefault("current_version", skill.current_version)
        data["is_skillforge_skill"] = True
        data["skill_git_commit"] = _short_commit(current_commit)
        data["skill_git_commit_full"] = current_commit
        data["git_commit"] = _short_commit(current_commit)
        data["git_commit_full"] = current_commit
        data["deployed_git_commit"] = _short_commit(deployed_commit)
        data["deployed_git_commit_full"] = deployed_commit
        data["docker_git_commit"] = _short_commit(deployed_commit or current_commit)
        data["docker_git_commit_full"] = deployed_commit or current_commit
        data["sync_version_tag"] = attempt.version_tag if attempt else None
        data["sync_completed_at"] = isoformat_bjt(attempt.completed_at) if attempt else None
        data["sync_status"] = attempt.status if attempt else None
        data["git_deploy_recorded"] = bool(deployed_commit)
        enriched.append(data)
    return enriched


def _serialize_resource_job_summary(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "title": job.title,
        "status": job.status,
        "job_type": job.job_type,
        "target_skill_id": job.target_skill_id,
        "updated_at": isoformat_bjt(job.updated_at),
    }


def _analysis_summary_from_capabilities(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {"agent": False, "ops": []}
    try:
        cap = json.loads(raw)
    except (TypeError, ValueError):
        return {"agent": False, "ops": []}
    if not isinstance(cap, dict):
        return {"agent": False, "ops": []}
    ops = [
        str(item or "").strip()
        for item in (cap.get("ops") or [])
        if str(item or "").strip().startswith("intelligence.")
    ][:20]
    return {
        "agent": "intelligence.analyze" in ops,
        "ops": ops,
    }


def _media_summary_from_capabilities(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {"configured": False, "workload_roles": [], "ops": []}
    try:
        cap = json.loads(raw)
    except (TypeError, ValueError):
        return {"configured": False, "workload_roles": [], "ops": []}
    if not isinstance(cap, dict):
        return {"configured": False, "workload_roles": [], "ops": []}
    media = cap.get("media") if isinstance(cap.get("media"), dict) else {}
    ops = [str(item) for item in (cap.get("ops") or []) if str(item).startswith("media.")]
    roles = list(dict.fromkeys([
        str(item) for item in (cap.get("workload_roles") or []) + (media.get("workload_roles") or []) if str(item)
    ]))
    return {**media, "configured": bool(media.get("configured")), "workload_roles": roles, "ops": ops[:20]}


def _serialize_instance(
    instance: OpenClawInstance,
    *,
    active_training_jobs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # bridge_connected_at 是 DB 字段，只在握手/断开时写入；
    # 进程异常退出可能漏写断开导致字段残留 → 加一个实时字段 bridge_online 作为 UI 真源。
    training = _training_summary_from_capabilities(instance.bridge_capabilities_json)
    active_jobs = active_training_jobs or []
    if training or active_jobs:
        training = training or {
            "gateway": False,
            "supported_tasks": [],
            "gpu_count": 0,
            "worker_count": 0,
            "vram_total_gb": 0,
            "vram_free_gb": 0,
        }
        training["active_jobs"] = active_jobs
        training["active_jobs_count"] = len(active_jobs)
    return {
        "id": instance.id,
        "name": instance.name,
        "department": instance.department,
        "agent_type": getattr(instance, "agent_type", "aiclaw") or "aiclaw",
        "agent_purpose": _normalize_agent_purpose(getattr(instance, "agent_purpose", None)),
        "runtime_type": _instance_runtime_type(instance),
        "gateway_url": instance.gateway_url,
        "reload_hook_url": instance.reload_hook_url,
        "network_zone": instance.network_zone,
        "is_active": instance.is_active,
        "is_platform_default": bool(getattr(instance, "is_platform_default", False)),
        "connection_mode": instance.connection_mode,
        "bridge_online": bridge_registry.is_online(instance.id),
        "bridge_connected_at": isoformat_bjt(instance.bridge_connected_at),
        "last_heartbeat": isoformat_bjt(instance.last_heartbeat),
        "last_sync_at": isoformat_bjt(instance.last_sync_at),
        "last_sync_ok": instance.last_sync_ok,
        "has_device_pubkey": bool(instance.device_pubkey),
        "bridge_fingerprint": instance.bridge_fingerprint,
        "bridge_platform": instance.bridge_platform,
        "bridge_version": instance.bridge_version,
        "bridge_latest_version": BRIDGE_VERSION,
        "bridge_update_available": _bridge_update_available(instance.bridge_version),
        "bridge_gateway_kind": instance.bridge_gateway_kind,
        "bridge_gateway_version": instance.bridge_gateway_version,
        "bridge_skills_dir": instance.bridge_skills_dir,
        "training": training,
        "analysis": _analysis_summary_from_capabilities(instance.bridge_capabilities_json),
        "media": _media_summary_from_capabilities(instance.bridge_capabilities_json),
        "active_training_jobs": active_jobs,
        "active_training_jobs_count": len(active_jobs),
        "pending_rotation": instance.pending_rotation,
        "enrollment_expires_at": isoformat_bjt(instance.enrollment_expires_at),
        "enrollment_consumed_at": isoformat_bjt(instance.enrollment_consumed_at),
    }


def _agent_matches_capability(instance: OpenClawInstance, capability: str) -> bool:
    purpose = _normalize_agent_purpose(getattr(instance, "agent_purpose", None))
    if capability == "skill_runtime":
        return purpose in SKILL_RUNTIME_AGENT_PURPOSES
    if capability == "analysis":
        return purpose in {"analysis", "mixed"} and bool(
            _analysis_summary_from_capabilities(instance.bridge_capabilities_json).get("agent")
        )
    if capability == "training":
        return purpose in {"training", "mixed"} and bool(
            (_training_summary_from_capabilities(instance.bridge_capabilities_json) or {}).get("gateway")
        )
    if capability == "media":
        media = _media_summary_from_capabilities(instance.bridge_capabilities_json)
        return purpose in {"media", "mixed"} and bool(
            media.get("configured") and "video_generation" in (media.get("workload_roles") or [])
        )
    return False


def _coverage_agent_summary(instance: OpenClawInstance) -> dict[str, Any]:
    return {
        "id": instance.id,
        "name": instance.name or instance.id,
        "department": instance.department,
        "agent_purpose": _normalize_agent_purpose(getattr(instance, "agent_purpose", None)),
        "runtime_type": _instance_runtime_type(instance),
        "bridge_online": bridge_registry.is_online(instance.id),
        "is_platform_default": bool(getattr(instance, "is_platform_default", False)),
    }


def _coverage_capability_flow(capability: str, *, fallback: bool = False) -> dict[str, Any]:
    if capability == "skill_runtime":
        return {
            "input_channels": ["skill_run_request", "node_schedule_snapshot"],
            "control_ops": ["run_agent_skill", "run_skill_script"],
            "output_channels": ["execution_run", "decision_log", "execution_artifact"],
            "fallback": fallback,
        }
    if capability == "analysis":
        return {
            "input_channels": ["intelligence_context_pack", "model_deployment_context"],
            "control_ops": ["intelligence.analyze"],
            "output_channels": ["analysis_result", "decision_model_context", "learning_event"],
            "fallback": fallback,
        }
    if capability == "training":
        return {
            "input_channels": ["training_job_manifest", "learning_artifact_dataset_package"],
            "control_ops": ["training.submit_job", "training.collect_result", "training.inference"],
            "output_channels": ["training_job_task", "model_artifact", "model_deployment"],
            "fallback": fallback,
        }
    if capability == "media":
        return {
            "input_channels": ["media_job", "project_run_asset", "allowlisted_h3_template"],
            "control_ops": ["media.submit_job", "media.get_job", "media.collect_result"],
            "output_channels": ["media_generation_attempt", "project_run_asset", "training_sample"],
            "fallback": fallback,
        }
    return {"input_channels": [], "control_ops": [], "output_channels": [], "fallback": fallback}


def _agent_contract_summary(instance: OpenClawInstance, capabilities: list[str], ops: list[str]) -> dict[str, Any]:
    flows = [_coverage_capability_flow(capability) for capability in capabilities]
    input_channels = sorted({
        channel
        for flow in flows
        for channel in flow.get("input_channels", [])
        if channel
    })
    control_ops = sorted({
        op
        for flow in flows
        for op in flow.get("control_ops", [])
        if op
    })
    output_channels = sorted({
        channel
        for flow in flows
        for channel in flow.get("output_channels", [])
        if channel
    })
    advertised_ops = {str(op or "").strip() for op in ops if str(op or "").strip()}
    purpose = _normalize_agent_purpose(getattr(instance, "agent_purpose", None))
    missing_ops: list[str] = []
    required_ops_any: dict[str, list[str]] = {}
    available_control_ops: list[str] = []
    for capability in capabilities:
        capability_ops = tuple(_coverage_capability_flow(capability).get("control_ops", []))
        if capability == "training":
            missing_ops.extend(op for op in TRAINING_LIFECYCLE_OPS if op not in advertised_ops)
            available_control_ops.extend(op for op in capability_ops if op in advertised_ops)
        elif capability == "skill_runtime":
            required_ops_any[capability] = list(capability_ops)
            matched_ops = [op for op in capability_ops if op in advertised_ops]
            available_control_ops.extend(matched_ops)
            if capability_ops and not matched_ops:
                missing_ops.extend(capability_ops)
        elif capability_ops and not advertised_ops.intersection(capability_ops):
            missing_ops.extend(capability_ops)
        else:
            available_control_ops.extend(op for op in capability_ops if op in advertised_ops)
    missing: list[str] = []
    if not capabilities:
        missing.append("capability")
    if not input_channels:
        missing.append("input_channels")
    if missing_ops:
        missing.append("control_ops")
    if not output_channels:
        missing.append("output_channels")
    if purpose not in AGENT_PURPOSE_ALLOWED:
        missing.append("agent_purpose")
    return {
        "complete": not missing,
        "missing": missing,
        "purpose": purpose,
        "runtime_type": _instance_runtime_type(instance),
        "input_channels": input_channels,
        "control_ops": control_ops,
        "advertised_ops": sorted(advertised_ops)[:40],
        "required_ops": sorted(set(control_ops)),
        "required_ops_any": required_ops_any,
        "missing_ops": sorted(set(missing_ops)),
        "available_control_ops": sorted(set(available_control_ops)),
        "output_channels": output_channels,
        "lineage_required": output_channels,
        "flow_traceable": bool(input_channels and output_channels),
        "submission_ready": (
            ("training" not in capabilities or "training.submit_job" in advertised_ops)
            and ("media" not in capabilities or "media.submit_job" in advertised_ops)
        ),
        "lifecycle_ready": not missing_ops and bool(input_channels and output_channels),
    }


def _instance_capability_ops(instance: OpenClawInstance) -> list[str]:
    try:
        cap = json.loads(getattr(instance, "bridge_capabilities_json", "") or "{}")
    except (TypeError, ValueError):
        cap = {}
    return [
        str(item or "").strip()[:80]
        for item in ((cap.get("ops") if isinstance(cap, dict) else None) or [])
        if str(item or "").strip()
    ][:40]


def _agent_capability_contract_complete(instance: OpenClawInstance, capability: str) -> bool:
    if not _agent_matches_capability(instance, capability):
        return False
    contract = _agent_contract_summary(instance, [capability], _instance_capability_ops(instance))
    return bool(contract.get("complete"))


def _coverage_capability_payload(
    instances: list[OpenClawInstance],
    *,
    capability: str,
    fallback_instances: list[OpenClawInstance],
) -> dict[str, Any]:
    matching = [item for item in instances if _agent_matches_capability(item, capability)]
    online = [
        item
        for item in matching
        if getattr(item, "is_active", True) and bridge_registry.is_online(item.id)
    ]
    ready_online = [
        item
        for item in online
        if _agent_capability_contract_complete(item, capability)
    ]
    fallback_online_all = [
        item
        for item in fallback_instances
        if getattr(item, "is_active", True) and bridge_registry.is_online(item.id)
    ]
    fallback_online = [
        item
        for item in fallback_online_all
        if _agent_capability_contract_complete(item, capability)
    ]
    fallback_active = bool(not ready_online and fallback_online)
    return {
        "count": len(matching),
        "online": len(online),
        "ready": bool(ready_online),
        "fallback_ready": fallback_active,
        "fallback_count": len(fallback_online),
        "agents": [_coverage_agent_summary(item) for item in matching[:12]],
        "fallback_agents": [_coverage_agent_summary(item) for item in fallback_online[:6]] if fallback_active else [],
        "flow": _coverage_capability_flow(capability, fallback=fallback_active),
    }


def _controlled_node_summary(instance: OpenClawInstance) -> dict[str, Any]:
    capabilities = [
        capability
        for capability in ("skill_runtime", "analysis", "training", "media")
        if _agent_matches_capability(instance, capability)
    ]
    flows = {
        capability: _coverage_capability_flow(capability)
        for capability in capabilities
    }
    ops = _instance_capability_ops(instance)
    return {
        **_coverage_agent_summary(instance),
        "controlled": bool(capabilities),
        "capabilities": capabilities,
        "ops": ops,
        "flow": flows,
        "agent_contract": _agent_contract_summary(instance, capabilities, ops),
        "input_contract_clear": bool(capabilities),
        "lineage_channels": sorted({
            channel
            for flow in flows.values()
            for channel in flow.get("output_channels", [])
        }),
    }


async def _agent_coverage_departments(
    db: AsyncSession,
    current_user: User,
    rows: list[OpenClawInstance],
) -> list[str]:
    if not _can_view_all_instances(current_user):
        department = await _resolve_tasktree_lv1_department(
            db,
            current_user.department,
            allow_unknown=True,
        )
        return [department] if department else []

    departments = {str(item.department or "").strip() for item in rows if str(item.department or "").strip()}
    org_rows = (await db.execute(select(OrgUnit).where(OrgUnit.type == "department"))).scalars().all()
    for unit in org_rows:
        if unit.parent_id == settings.TASKTREE_TOP_LEVEL_PARENT_ID and unit.name:
            departments.add(unit.name)
    return sorted(departments, key=lambda item: item.lower())


@router.get("/departments/agent-coverage")
@cached(
    "agent:coverage",
    ttl=settings.CACHE_TTL_AGENT,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_agent_department_coverage(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    stmt = select(OpenClawInstance).where(OpenClawInstance.is_active == True)  # noqa: E712
    if not _can_view_all_instances(current_user):
        department = await _resolve_tasktree_lv1_department(
            db,
            current_user.department,
            allow_unknown=True,
        )
        stmt = stmt.where(
            or_(
                OpenClawInstance.department == department,
                OpenClawInstance.is_platform_default.is_(True),
            )
            if department
            else OpenClawInstance.is_platform_default.is_(True)
        )
    rows = (await db.execute(stmt)).scalars().all()
    departments = await _agent_coverage_departments(db, current_user, list(rows))

    platform_fallbacks = {
        capability: [
            item
            for item in rows
            if bool(getattr(item, "is_platform_default", False))
            and _agent_matches_capability(item, capability)
        ]
        for capability in ("skill_runtime", "analysis", "training", "media")
    }
    items = []
    for department in departments:
        department_instances = [item for item in rows if item.department == department]
        capabilities = {
            capability: _coverage_capability_payload(
                department_instances,
                capability=capability,
                fallback_instances=platform_fallbacks[capability],
            )
            for capability in ("skill_runtime", "analysis", "training", "media")
        }
        missing = [
            capability
            for capability, payload in capabilities.items()
            if not payload["ready"] and not payload["fallback_ready"]
        ]
        fallback = [
            capability
            for capability, payload in capabilities.items()
            if not payload["ready"] and payload["fallback_ready"]
        ]
        controlled_nodes = []
        for node in department_instances:
            summary = _controlled_node_summary(node)
            if summary.get("controlled"):
                controlled_nodes.append(summary)
        status = "ready" if not missing and not fallback else ("fallback" if not missing else "missing")
        items.append({
            "department": department,
            "status": status,
            "missing": missing,
            "fallback": fallback,
            "counts": {
                "total": len(department_instances),
                "online": sum(1 for item in department_instances if bridge_registry.is_online(item.id)),
                "platform_default": sum(1 for item in department_instances if bool(getattr(item, "is_platform_default", False))),
            },
            "capabilities": capabilities,
            "controlled_nodes": controlled_nodes,
            "controlled_node_count": len(controlled_nodes),
        })

    return {
        "items": items,
        "total": len(items),
        "summary": {
            "ready": sum(1 for item in items if item["status"] == "ready"),
            "fallback": sum(1 for item in items if item["status"] == "fallback"),
            "missing": sum(1 for item in items if item["status"] == "missing"),
        },
    }


@router.get("/departments/agent-kpi")
@cached(
    "agent:kpi",
    ttl=settings.CACHE_TTL_AGENT,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_agent_department_kpi(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """Agent 终端管理页 5 格 KPI 概要。

    对应 design canvas `admin.jsx AdminAgentTerminals` 顶部条：
    - department_total：当前可视范围内的一级部门总数
    - exec_configured / exec_missing：执行能力 ready (本部门自有可用) 部门数 / 仍缺口的部门数
      `exec_configured` 仅统计 ready，`exec_missing` 统计 missing + fallback（fallback 表示自己缺、仅靠平台兜底）
    - analysis_configured / analysis_missing：同上，针对分析 Agent
    - training_configured / training_missing：同上，针对训练 Agent
    - bridge_online / bridge_offline：当前可视范围内 active 实例的 bridge 在线 / 离线数
    """
    stmt = select(OpenClawInstance).where(OpenClawInstance.is_active == True)  # noqa: E712
    if not _can_view_all_instances(current_user):
        department = await _resolve_tasktree_lv1_department(
            db,
            current_user.department,
            allow_unknown=True,
        )
        stmt = stmt.where(
            or_(
                OpenClawInstance.department == department,
                OpenClawInstance.is_platform_default.is_(True),
            )
            if department
            else OpenClawInstance.is_platform_default.is_(True)
        )
    rows = list((await db.execute(stmt)).scalars().all())
    departments = await _agent_coverage_departments(db, current_user, rows)

    platform_fallbacks = {
        capability: [
            item
            for item in rows
            if bool(getattr(item, "is_platform_default", False))
            and _agent_matches_capability(item, capability)
        ]
        for capability in ("skill_runtime", "analysis", "training", "media")
    }

    configured = {"skill_runtime": 0, "analysis": 0, "training": 0, "media": 0}
    missing = {"skill_runtime": 0, "analysis": 0, "training": 0, "media": 0}
    for department in departments:
        department_instances = [item for item in rows if item.department == department]
        for capability in ("skill_runtime", "analysis", "training", "media"):
            payload = _coverage_capability_payload(
                department_instances,
                capability=capability,
                fallback_instances=platform_fallbacks[capability],
            )
            if payload["ready"]:
                configured[capability] += 1
            else:
                # fallback_ready 仍计为缺口（部门自己没配，靠平台兜底）
                missing[capability] += 1

    # Bridge 在线 / 离线统计当前可视范围内的全部 active 实例
    bridge_online = sum(1 for item in rows if bridge_registry.is_online(item.id))
    bridge_offline = len(rows) - bridge_online

    return {
        "department_total": len(departments),
        "exec_configured": configured["skill_runtime"],
        "exec_missing": missing["skill_runtime"],
        "analysis_configured": configured["analysis"],
        "analysis_missing": missing["analysis"],
        "training_configured": configured["training"],
        "training_missing": missing["training"],
        "bridge_online": bridge_online,
        "bridge_offline": bridge_offline,
    }


def _clean_chat_context_text(value: Any, *, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]


def _clean_chat_model_context(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    context = {
        "model_deployment_id": _clean_chat_context_text(value.get("model_deployment_id") or value.get("deployment_id"), limit=80),
        "model_family": _clean_chat_context_text(value.get("model_family") or value.get("model_name"), limit=160),
        "training_job_id": _clean_chat_context_text(value.get("training_job_id") or value.get("job_id"), limit=80),
        "artifact_id": _clean_chat_context_text(value.get("artifact_id"), limit=160),
        "artifact_sha256": _clean_chat_context_text(value.get("artifact_sha256"), limit=80),
        "target_gateway_id": _clean_chat_context_text(value.get("target_gateway_id") or value.get("gateway_id"), limit=80),
    }
    return {key: item for key, item in context.items() if item}


def _chat_deployment_artifact_uri(artifact_ref: dict[str, Any]) -> str:
    return str(
        artifact_ref.get("uri")
        or artifact_ref.get("artifact_uri")
        or artifact_ref.get("url")
        or artifact_ref.get("path")
        or ""
    ).strip()


def _chat_deployment_target_gateway_id(deployment: Any, job: Any | None) -> str:
    value = str(getattr(deployment, "deployment_target_gateway_id", "") or "").strip()
    if value:
        return value[:100]
    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    value = str(
        artifact_ref.get("deployment_target_gateway_id")
        or artifact_ref.get("target_gateway_id")
        or ""
    ).strip()
    if value:
        return value[:100]
    return str(getattr(job, "target_gateway_id", "") or "").strip()[:100] if job is not None else ""


def _chat_deployment_artifact_local_path(artifact_uri: str) -> str:
    text = str(artifact_uri or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme == "file":
        return unquote(parsed.path or "").strip()
    if parsed.scheme:
        return ""
    return text if text.startswith("/") else ""


def _chat_bridge_state_fragments(instance_id: str) -> tuple[str, ...]:
    text = str(instance_id or "").strip()
    if not text:
        return ()
    slug = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    return (
        f"/.skillforge_bridge/{slug}/",
        f"/.skillforge_bridge/{text}/",
    )


def _chat_model_artifact_access_disabled_reason(
    artifact_uri: str,
    gateway: OpenClawInstance | None,
    *,
    bound_gateway_id: str = "",
) -> str:
    if gateway is None:
        return ""
    gateway_id = str(getattr(gateway, "id", "") or "").strip()
    bound_gateway_id = str(bound_gateway_id or "").strip()
    if not gateway_id or not bound_gateway_id or gateway_id == bound_gateway_id:
        return ""
    artifact_path = _chat_deployment_artifact_local_path(artifact_uri)
    if not artifact_path:
        return ""
    if any(fragment in artifact_path for fragment in _chat_bridge_state_fragments(gateway_id)):
        return ""
    return (
        f"训练后模型产物只在原推理节点 {bound_gateway_id} 本地可用，"
        f"当前在线推理节点 {gateway_id} 尚未同步模型产物；请重新训练或重新收集模型产物后再对话"
    )


def _chat_artifact_matches_deployment(artifact_ref: dict[str, Any], artifact: Any) -> bool:
    if not isinstance(artifact, dict):
        return False
    ref_id = str(artifact_ref.get("id") or "").strip()
    ref_sha = str(artifact_ref.get("sha256") or "").strip().lower()
    ref_uri = _chat_deployment_artifact_uri(artifact_ref)
    raw_uri = str(
        artifact.get("uri")
        or artifact.get("artifact_uri")
        or artifact.get("url")
        or artifact.get("path")
        or ""
    ).strip()
    raw_sha = str(artifact.get("sha256") or artifact.get("hash") or "").strip().lower()
    raw_ids = {
        str(artifact.get("id") or "").strip(),
        str(artifact.get("name") or "").strip(),
        str(artifact.get("filename") or "").strip(),
    }
    return bool(
        (ref_sha and raw_sha and ref_sha == raw_sha)
        or (ref_uri and raw_uri and ref_uri == raw_uri)
        or (ref_id and ref_id in raw_ids)
    )


async def _chat_deployment_artifact_proof_missing_reason(
    db: AsyncSession,
    deployment: Any,
    job: Any | None,
    artifact_ref: dict[str, Any],
) -> str:
    """Require active chat deployments to trace back to a real collected training artifact."""
    from app.training.models import TrainingJobTask

    job_id = str(getattr(job, "id", "") or getattr(deployment, "job_id", "") or "").strip()
    if not job_id:
        return "训练后模型部署缺少训练任务，无法确认模型产物来源"
    count = int(
        (
            await db.execute(
                select(func.count(TrainingJobTask.id)).where(TrainingJobTask.job_id == job_id)
            )
        ).scalar()
        or 0
    )
    if count <= 0:
        return f"训练后模型部署 {getattr(deployment, 'id', '')} 没有训练任务产物记录，不能对话"

    tasks = (
        await db.execute(
            select(TrainingJobTask)
            .where(TrainingJobTask.job_id == job_id)
            .order_by(TrainingJobTask.id.desc())
            .limit(50)
        )
    ).scalars().all()
    for task in tasks:
        metrics = task.metrics_json if isinstance(task.metrics_json, dict) else {}
        gateway_result = metrics.get("gateway_result") if isinstance(metrics.get("gateway_result"), dict) else {}
        artifacts = gateway_result.get("artifacts") if isinstance(gateway_result.get("artifacts"), list) else []
        if any(_chat_artifact_matches_deployment(artifact_ref, item) for item in artifacts):
            return ""
    return f"训练后模型部署 {getattr(deployment, 'id', '')} 未找到匹配的训练产物记录，不能对话"


async def _chat_deployment_artifact_node_missing_reason(
    deployment: Any,
    job: Any | None,
    artifact_ref: dict[str, Any],
    gateway: OpenClawInstance | None,
) -> str:
    if gateway is None:
        return ""
    artifact_uri = _chat_deployment_artifact_uri(artifact_ref)
    if not _chat_deployment_artifact_local_path(artifact_uri):
        return ""
    ops = _chat_deployment_gateway_ops(gateway)
    if MODEL_CHAT_ARTIFACT_STATUS_OP not in ops:
        return f"训练后模型推理节点 {gateway.id} 未提供 {MODEL_CHAT_ARTIFACT_STATUS_OP} 能力，无法确认训练产物是否存在"
    job_id = str(getattr(job, "id", "") or getattr(deployment, "job_id", "") or "").strip()
    artifact_id = str(artifact_ref.get("id") or getattr(deployment, "artifact_id", "") or "").strip()
    if not job_id or not artifact_id:
        return "训练后模型部署缺少训练任务或产物 ID，无法确认模型产物是否存在"
    try:
        status = await AIClawClient(
            gateway.id,
            gateway_kind=getattr(gateway, "bridge_gateway_kind", None),
        ).get_training_artifact_status(
            {
                "job_id": job_id,
                "artifact_id": artifact_id,
                "artifact_name": artifact_ref.get("name"),
                "sha256": artifact_ref.get("sha256"),
            },
            timeout=30,
        )
    except AppError as exc:
        return f"训练后模型产物状态检查失败：{_chat_error_message(exc)}"
    except Exception as exc:  # noqa: BLE001
        return f"训练后模型产物状态检查失败：{str(exc)[:200]}"
    if not isinstance(status, dict) or status.get("exists") is not True:
        reason = str((status or {}).get("reason") if isinstance(status, dict) else "" or "").strip()
        detail = f"：{reason}" if reason else ""
        return f"训练后模型产物在推理节点 {gateway.id} 上不存在{detail}；请重新训练或重新收集模型产物后再对话"
    expected_sha = str(artifact_ref.get("sha256") or "").strip().lower()
    actual_sha = str(status.get("sha256") or "").strip().lower()
    if expected_sha and actual_sha and expected_sha != actual_sha:
        return f"训练后模型产物在推理节点 {gateway.id} 上 SHA256 不匹配；请重新训练或重新收集模型产物后再对话"
    return ""


def _chat_deployment_model_profile(deployment: Any, job: Any) -> str:
    spec = getattr(job, "spec_json", None)
    spec = spec if isinstance(spec, dict) else {}
    candidates = [
        spec.get("model_profile"),
        spec.get("base_model_profile"),
    ]
    deployment_spec = spec.get("deployment") if isinstance(spec.get("deployment"), dict) else {}
    model_spec = spec.get("model") if isinstance(spec.get("model"), dict) else {}
    candidates.extend([deployment_spec.get("model_profile"), model_spec.get("profile")])
    for raw in candidates:
        profile = str(raw or "").strip().lower().replace("_", "-")
        if profile:
            return profile[:80]
    family = str(getattr(deployment, "model_family", "") or "").lower()
    runtime_profile = str(getattr(deployment, "deployment_runtime_profile", "") or "").lower()
    if ("qwen3.6" in family or "qwen3.6" in runtime_profile) and ("35b" in family or "35b" in runtime_profile):
        return "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    if "qwen3.5" in family and "4b" in family:
        return "qwen3.5-4b"
    return "qwen3.5-4b"


def _chat_gateway_gpu_items(instance: OpenClawInstance | None) -> list[dict[str, Any]]:
    if instance is None:
        return []
    try:
        payload = json.loads(getattr(instance, "bridge_capabilities_json", None) or "{}")
    except Exception:
        return []
    items = payload.get("gpu") if isinstance(payload, dict) else []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _chat_model_max_new_tokens(profile: str, instance: OpenClawInstance | None) -> int:
    """Best-effort chat budget based on model profile and reported GPU headroom."""
    profile_text = str(profile or "").lower()
    floor = MODEL_CHAT_DEFAULT_MAX_NEW_TOKENS
    if "35b" in profile_text:
        floor = 768
    elif "4b" in profile_text:
        floor = 1024
    elif "7b" in profile_text or "8b" in profile_text:
        floor = 1536
    elif "14b" in profile_text:
        floor = 1024

    gpu_items = _chat_gateway_gpu_items(instance)
    free_mb = max((int(item.get("vram_free_mb") or 0) for item in gpu_items), default=0)
    total_mb = max((int(item.get("vram_total_mb") or 0) for item in gpu_items), default=0)
    headroom_mb = free_mb or total_mb
    if "35b" in profile_text:
        if headroom_mb >= 24000:
            return 1536
        if headroom_mb >= 16000:
            return 1024
        return 768
    if headroom_mb >= 24000:
        return MODEL_CHAT_ABSOLUTE_MAX_NEW_TOKENS
    if headroom_mb >= 16000:
        return max(floor, 3072)
    if headroom_mb >= 10000:
        return max(floor, 1536)
    if headroom_mb >= 7000:
        return floor if "4b" in profile_text else min(floor, 1536)
    return min(floor, 1536)


def _chat_inference_metrics(result: Any, *, gateway_id: str, gateway_kind: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(result.get("metrics") or {}) if isinstance(result, dict) and isinstance(result.get("metrics"), dict) else {}
    generated_tokens = int(metrics.get("generated_tokens") or 0)
    duration_ms = int(metrics.get("duration_ms") or 0)
    if generated_tokens > 0 and duration_ms > 0 and not metrics.get("tokens_per_second"):
        metrics["tokens_per_second"] = round(generated_tokens / (duration_ms / 1000), 2)
    metrics.setdefault("inference_backend", "bridge_local_lora_inference")
    metrics.setdefault("inference_runtime", "local_bridge")
    metrics.setdefault("gateway_id", gateway_id)
    if gateway_kind:
        metrics.setdefault("gateway_kind", gateway_kind)
    metrics.setdefault("model_deployment_id", payload.get("deployment_id"))
    metrics.setdefault("model_profile", payload.get("profile"))
    metrics.setdefault("requested_max_new_tokens", payload.get("max_new_tokens"))
    metrics.setdefault("effective_max_new_tokens", metrics.get("effective_max_new_tokens") or payload.get("max_new_tokens"))
    return metrics


def _chat_inference_text(result: Any) -> str:
    raw_text = ""
    if not isinstance(result, dict):
        raw_text = str(result or "").strip()
        cleaned = _clean_chat_inference_output(raw_text)
        return cleaned if cleaned or _chat_inference_should_drop_raw(raw_text) else raw_text
    for key in ("text", "output_text", "answer", "response", "content"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            raw_text = value.strip()
            cleaned = _clean_chat_inference_output(raw_text)
            return cleaned if cleaned or _chat_inference_should_drop_raw(raw_text) else raw_text
    message = result.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            raw_text = content.strip()
            cleaned = _clean_chat_inference_output(raw_text)
            return cleaned if cleaned or _chat_inference_should_drop_raw(raw_text) else raw_text
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    parts.append(str(block.get("text") or block.get("content") or ""))
            text = "\n\n".join(item.strip() for item in parts if item.strip())
            if text:
                cleaned = _clean_chat_inference_output(text)
                return cleaned if cleaned or _chat_inference_should_drop_raw(text) else text
    return json.dumps(result, ensure_ascii=False, default=str)[:4000]


_CHAT_TRAILING_MODEL_SIGNATURE_RE = re.compile(
    "(?:(?<=[\u3400-\u9fff#）)\\]】。！？!?\"'”’])(?:Mistral|ng)|\\n\\s*Mistral)\\s*$"
)
_CHAT_PROMPT_TEMPLATE_LEAK_RE = re.compile(
    r"(?is)(?:system\s*\n\s*You are the conversation interface|<\|\s*(?:model_context|user|assistant)\b)"
)
_CHAT_REASONING_LEAK_RE = re.compile(
    r"(?is)^\s*(?:Thinking Process|Reasoning|思考过程|推理过程)\s*[:：]"
)


def _chat_inference_should_drop_raw(text: str) -> bool:
    cleaned = re.sub(r"</?think>", "", str(text or "").strip(), flags=re.IGNORECASE).strip()
    return bool(
        re.match(r"(?is)^\s*<think\b", str(text or ""))
        or _CHAT_REASONING_LEAK_RE.match(cleaned)
    )


def _clean_chat_inference_output(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    if _CHAT_REASONING_LEAK_RE.match(cleaned):
        for pattern in (
            r"(?im)^\s*(?:最终答案|正式回答|回答|输出)\s*[:：]\s*",
            r"(?im)^\s*(?:输入|Input)\s*[:：]\s*",
        ):
            answer_match = re.search(pattern, cleaned)
            if answer_match and answer_match.start() > 0:
                cleaned = cleaned[answer_match.start():].strip()
                break
        else:
            return ""
    marker_match = re.search(r"(?i)(?:\n\s*assistant\s*:?\s*|\bassistant\s*(?:\n|$))", cleaned)
    if marker_match and marker_match.start() > 0:
        first = cleaned[:marker_match.start()].strip()
        rest = cleaned[marker_match.end():].strip()
        if first:
            cleaned = first
        elif rest:
            cleaned = rest
    leak_match = _CHAT_PROMPT_TEMPLATE_LEAK_RE.search(cleaned)
    if leak_match:
        cleaned = cleaned[:leak_match.start()].strip()
    cleaned = re.sub(r"(?im)^\s*(assistant|user|system)\s*:?\s*$", "", cleaned)
    cleaned = re.sub(r"(?i)\b(assistant|user|system)\s*$", "", cleaned).strip()
    lines = [line.rstrip() for line in cleaned.splitlines()]
    cleaned = "\n".join(line for line in lines if line.strip()).strip()
    cleaned = re.sub(
        r"\s+(?:Girl|Boy|User|Assistant|System|Human|Bot|AI)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = _CHAT_TRAILING_MODEL_SIGNATURE_RE.sub("", cleaned).strip()
    return cleaned


def _chat_error_message(exc: AppError) -> str:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    return str(detail.get("detail") or exc.message or exc.code)


def _chat_deployment_gateway_ops(instance: OpenClawInstance | None) -> set[str]:
    if instance is None:
        return set()
    try:
        capabilities = json.loads(getattr(instance, "bridge_capabilities_json", "") or "{}")
    except (TypeError, ValueError):
        capabilities = {}
    raw_ops = capabilities.get("ops") if isinstance(capabilities, dict) else []
    return {
        str(item or "").strip()
        for item in (raw_ops or [])
        if str(item or "").strip()
    }


def _chat_instance_can_run_training_inference(instance: OpenClawInstance | None) -> bool:
    if instance is None:
        return False
    if getattr(instance, "is_active", True) is False:
        return False
    return "training.inference" in _chat_deployment_gateway_ops(instance)


def _chat_instance_online_ready(instance: OpenClawInstance | None, *, require_online: bool = True) -> bool:
    if not _chat_instance_can_run_training_inference(instance):
        return False
    return (not require_online) or bridge_registry.is_online(instance.id)


async def _chat_inference_gateway_accessible(
    db: AsyncSession,
    current_user: User | None,
    instance: OpenClawInstance,
) -> bool:
    if current_user is None or _can_view_all_instances(current_user):
        return True
    if bool(getattr(instance, "is_platform_default", False)):
        return True
    user_department = await _resolve_tasktree_lv1_department(
        db,
        current_user.department,
        allow_unknown=True,
    )
    instance_department = await _resolve_tasktree_lv1_department(
        db,
        instance.department,
        allow_unknown=True,
    )
    return bool(user_department and instance_department and user_department == instance_department)


async def _chat_model_fallback_gateway(
    db: AsyncSession,
    current_user: User | None,
    deployment: Any,
    job: Any | None,
    *,
    requested_gateway_id: str = "",
    require_online: bool = True,
) -> OpenClawInstance | None:
    """Pick an online inference-capable node when a deployment points to a retired runtime node."""
    requested_gateway_id = str(requested_gateway_id or "").strip()
    ids: list[str] = []
    for value in (
        _chat_deployment_target_gateway_id(deployment, job),
        requested_gateway_id,
        str(getattr(job, "target_gateway_id", "") or "").strip() if job is not None else "",
    ):
        if value and value not in ids:
            ids.append(value)
    if ids:
        explicit_rows = (
            await db.execute(
                select(OpenClawInstance)
                .where(OpenClawInstance.id.in_(ids))
                .where(OpenClawInstance.is_active == True)  # noqa: E712
            )
        ).scalars().all()
        explicit_by_id = {row.id: row for row in explicit_rows}
        for instance_id in ids:
            instance = explicit_by_id.get(instance_id)
            if (
                _chat_instance_online_ready(instance, require_online=require_online)
                and await _chat_inference_gateway_accessible(db, current_user, instance)
            ):
                return instance

    stmt = (
        select(OpenClawInstance)
        .where(OpenClawInstance.is_active == True)  # noqa: E712
        .order_by(
            OpenClawInstance.is_platform_default.asc(),
            OpenClawInstance.name.asc(),
            OpenClawInstance.id.asc(),
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    candidates = [
        row for row in rows
        if _chat_instance_online_ready(row, require_online=require_online)
    ]
    if not candidates:
        return None

    allowed: list[OpenClawInstance] = []
    for row in candidates:
        if await _chat_inference_gateway_accessible(db, current_user, row):
            allowed.append(row)
    candidates = allowed
    if not candidates:
        return None

    preferred_departments = [
        str(getattr(job, "department", "") or "").strip(),
        str(getattr(deployment, "department", "") or "").strip(),
    ]
    for department in preferred_departments:
        if not department:
            continue
        direct = [row for row in candidates if str(row.department or "").strip() == department]
        if direct:
            return direct[0]
    mixed = [
        row for row in candidates
        if _normalize_agent_purpose(getattr(row, "agent_purpose", None)) == "mixed"
    ]
    if mixed:
        return mixed[0]
    non_platform = [row for row in candidates if not bool(getattr(row, "is_platform_default", False))]
    return (non_platform or candidates)[0]


async def _resolve_chat_inference_gateway(
    db: AsyncSession,
    current_user: User | None,
    deployment: Any,
    job: Any | None,
    *,
    requested_gateway_id: str = "",
    require_online: bool = True,
) -> tuple[OpenClawInstance | None, str, str | None]:
    bound_gateway_id = _chat_deployment_target_gateway_id(deployment, job)
    fallback = await _chat_model_fallback_gateway(
        db,
        current_user,
        deployment,
        job,
        requested_gateway_id=requested_gateway_id,
        require_online=require_online,
    )
    if fallback is not None:
        origin_gateway_id = bound_gateway_id or str(requested_gateway_id or "").strip()
        fallback_reason = (
            f"原推理节点 {origin_gateway_id} 不可用，已切换到在线推理节点 {fallback.id}"
            if origin_gateway_id and origin_gateway_id != fallback.id
            else None
        )
        return fallback, bound_gateway_id, fallback_reason
    return None, bound_gateway_id, None


async def _chat_model_context_disabled_reason(
    db: AsyncSession,
    current_user: User | None,
    deployment: Any,
    job: Any | None,
    *,
    requested_gateway_id: str = "",
    require_online: bool = True,
) -> str:
    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    artifact_uri = _chat_deployment_artifact_uri(artifact_ref)
    if deployment.status not in MODEL_CHAT_DEPLOYMENT_STATUSES:
        return "训练后模型尚未进入灰度或激活状态，不能对话"
    if not artifact_uri:
        return f"训练后模型 {deployment.id} 缺少可推理的模型产物 URI"
    artifact_proof_reason = await _chat_deployment_artifact_proof_missing_reason(
        db,
        deployment,
        job,
        artifact_ref,
    )
    if artifact_proof_reason:
        return artifact_proof_reason
    gateway, bound_gateway_id, fallback_reason = await _resolve_chat_inference_gateway(
        db,
        current_user,
        deployment,
        job,
        requested_gateway_id=requested_gateway_id,
        require_online=require_online,
    )
    artifact_access_reason = _chat_model_artifact_access_disabled_reason(
        artifact_uri,
        gateway,
        bound_gateway_id=bound_gateway_id,
    )
    if artifact_access_reason:
        return artifact_access_reason
    artifact_node_reason = await _chat_deployment_artifact_node_missing_reason(
        deployment,
        job,
        artifact_ref,
        gateway,
    )
    if artifact_node_reason:
        return artifact_node_reason
    gateway_id = bound_gateway_id or str(requested_gateway_id or "").strip()
    if not gateway_id and gateway is None:
        return f"训练后模型 {deployment.id} 没有绑定推理节点"
    if gateway is None:
        instance = await db.get(OpenClawInstance, gateway_id) if gateway_id else None
        if instance is None or getattr(instance, "is_active", True) is False:
            return f"训练后模型推理节点 {gateway_id} 不存在或未启用"
        if "training.inference" not in _chat_deployment_gateway_ops(instance):
            return f"训练后模型推理节点 {gateway_id} 未提供 training.inference 能力"
        if require_online and not bridge_registry.is_online(gateway_id):
            return f"训练后模型推理节点 {gateway_id} 未连接"
        return f"训练后模型推理节点 {gateway_id} 不存在或未启用"
    return ""


async def _ensure_chat_model_department_access(
    db: AsyncSession,
    current_user: User,
    department: str | None,
) -> None:
    if _can_view_all_instances(current_user):
        return
    user_department = await _resolve_tasktree_lv1_department(
        db,
        current_user.department,
        allow_unknown=True,
    )
    model_department = await _resolve_tasktree_lv1_department(
        db,
        department,
        allow_unknown=True,
    )
    if user_department and model_department and user_department == model_department:
        return
    raise AppError("AUTH_DEPARTMENT_DENIED", 403, {"detail": "无权访问该模型部署"})


async def _load_chat_model_deployment_context(
    db: AsyncSession,
    current_user: User,
    *,
    deployment_id: str,
    requested_training_job_id: str | None = None,
    requested_artifact_id: str | None = None,
    requested_gateway_id: str | None = None,
) -> dict[str, str]:
    from app.training.models import TrainingJob, TrainingModelDeployment

    deployment = await db.get(TrainingModelDeployment, deployment_id)
    if deployment is None:
        raise AppError("NOT_FOUND", 404, {"detail": "training deployment not found"})
    await _ensure_chat_model_department_access(db, current_user, deployment.department)
    if deployment.status not in MODEL_CHAT_DEPLOYMENT_STATUSES:
        raise AppError(
            "INVALID_STATUS",
            400,
            {"detail": "only canary or active model deployments can be used for Agent chat"},
        )
    if requested_training_job_id and requested_training_job_id != deployment.job_id:
        raise AppError("PARAM_INVALID", 422, {"detail": "model deployment and training job do not match"})

    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    artifact_uri = _chat_deployment_artifact_uri(artifact_ref)
    artifact_id = str(artifact_ref.get("id") or deployment.artifact_id or "").strip()
    if requested_artifact_id and artifact_id and requested_artifact_id != artifact_id:
        raise AppError("PARAM_INVALID", 422, {"detail": "model deployment and artifact do not match"})
    job = await db.get(TrainingJob, deployment.job_id)
    artifact_proof_reason = ""
    if artifact_uri:
        artifact_proof_reason = await _chat_deployment_artifact_proof_missing_reason(
            db,
            deployment,
            job,
            artifact_ref,
        )
    gateway, bound_gateway_id, fallback_reason = await _resolve_chat_inference_gateway(
        db,
        current_user,
        deployment,
        job,
        requested_gateway_id=requested_gateway_id or "",
    )
    disabled_reason = artifact_proof_reason
    if not disabled_reason:
        disabled_reason = _chat_model_artifact_access_disabled_reason(
            artifact_uri,
            gateway,
            bound_gateway_id=bound_gateway_id,
        ) if gateway is not None else ""
    if not disabled_reason:
        disabled_reason = await _chat_deployment_artifact_node_missing_reason(
            deployment,
            job,
            artifact_ref,
            gateway,
        )
    if not disabled_reason:
        disabled_reason = "" if gateway is not None else await _chat_model_context_disabled_reason(
            db,
            current_user,
            deployment,
            job,
            requested_gateway_id=requested_gateway_id or "",
        )
    target_gateway_id = (
        getattr(gateway, "id", None)
        or bound_gateway_id
        or str(requested_gateway_id or "").strip()
    )

    context = {
        "model_deployment_id": deployment.id,
        "model_family": deployment.model_family,
        "training_job_id": deployment.job_id,
        "artifact_id": artifact_id,
        "artifact_sha256": str(artifact_ref.get("sha256") or "").strip(),
        "deployment_status": deployment.status,
        "target_gateway_id": target_gateway_id,
        "bound_gateway_id": bound_gateway_id if bound_gateway_id and bound_gateway_id != target_gateway_id else "",
        "inference_gateway_fallback_reason": fallback_reason or "",
        "inference_ready": "true" if not disabled_reason else "false",
        "inference_disabled_reason": disabled_reason,
    }
    return {key: _clean_chat_context_text(value) for key, value in context.items() if value}


async def _prepare_chat_model_inference(
    db: AsyncSession,
    current_user: User,
    *,
    message: str,
    model_context: dict[str, str],
) -> tuple[str, str | None, dict[str, Any]]:
    from app.training.models import TrainingJob, TrainingModelDeployment

    deployment_id = model_context.get("model_deployment_id")
    if not deployment_id:
        raise AppError("PARAM_INVALID", 422, {"detail": "缺少模型部署 ID"})
    deployment = await db.get(TrainingModelDeployment, deployment_id)
    if deployment is None:
        raise AppError("NOT_FOUND", 404, {"detail": "training deployment not found"})
    await _ensure_chat_model_department_access(db, current_user, deployment.department)
    if deployment.status not in MODEL_CHAT_DEPLOYMENT_STATUSES:
        raise AppError("INVALID_STATUS", 400, {"detail": "训练后模型尚未进入灰度或激活状态，不能对话"})
    job = await db.get(TrainingJob, deployment.job_id)
    if job is None:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})

    artifact_ref = deployment.artifact_ref_json if isinstance(deployment.artifact_ref_json, dict) else {}
    artifact_uri = _chat_deployment_artifact_uri(artifact_ref)
    artifact_id = str(artifact_ref.get("id") or deployment.artifact_id or "").strip()
    requested_training_job_id = str(model_context.get("training_job_id") or "").strip()
    if requested_training_job_id and requested_training_job_id != deployment.job_id:
        raise AppError("PARAM_INVALID", 422, {"detail": "model deployment and training job do not match"})
    requested_artifact_id = str(model_context.get("artifact_id") or "").strip()
    if requested_artifact_id and artifact_id and requested_artifact_id != artifact_id:
        raise AppError("PARAM_INVALID", 422, {"detail": "model deployment and artifact do not match"})
    artifact_proof_reason = ""
    if artifact_uri:
        artifact_proof_reason = await _chat_deployment_artifact_proof_missing_reason(
            db,
            deployment,
            job,
            artifact_ref,
        )
    requested_gateway_id = str(model_context.get("target_gateway_id") or "").strip()
    gateway, bound_gateway_id, fallback_reason = await _resolve_chat_inference_gateway(
        db,
        current_user,
        deployment,
        job,
        requested_gateway_id=requested_gateway_id,
    )
    disabled_reason = artifact_proof_reason
    if not disabled_reason:
        disabled_reason = _chat_model_artifact_access_disabled_reason(
            artifact_uri,
            gateway,
            bound_gateway_id=bound_gateway_id,
        ) if gateway is not None else ""
    if not disabled_reason:
        disabled_reason = await _chat_deployment_artifact_node_missing_reason(
            deployment,
            job,
            artifact_ref,
            gateway,
        )
    if not disabled_reason:
        disabled_reason = "" if gateway is not None else await _chat_model_context_disabled_reason(
            db,
            current_user,
            deployment,
            job,
            requested_gateway_id=requested_gateway_id,
        )
    if disabled_reason:
        if "缺少可推理的模型产物" in disabled_reason:
            code, status = "PARAM_INVALID", 422
        elif "没有绑定推理节点" in disabled_reason or "不存在或未启用" in disabled_reason:
            code, status = "AICLAW_INSTANCE_NOT_FOUND", 404
        elif "未提供 training." in disabled_reason:
            code, status = "BRIDGE_OP_ERROR", 502
        elif "未连接" in disabled_reason:
            code, status = "BRIDGE_OFFLINE", 503
        elif "模型产物" in disabled_reason:
            code, status = "PARAM_INVALID", 422
        else:
            code, status = "INVALID_STATUS", 400
        detail: dict[str, Any] = {"detail": disabled_reason}
        gateway_id = bound_gateway_id or requested_gateway_id
        if gateway_id:
            detail["target_gateway_id"] = gateway_id
        if deployment.id:
            detail["model_deployment_id"] = deployment.id
        raise AppError(code, status, detail)
    gateway_id = getattr(gateway, "id", None) if gateway is not None else ""
    if not gateway_id:
        raise AppError(
            "AICLAW_INSTANCE_NOT_FOUND",
            404,
            {"detail": f"训练后模型 {deployment.id} 没有绑定推理节点"},
        )
    instance = gateway

    target_gateway_id = (
        getattr(gateway, "id", None)
        or bound_gateway_id
        or requested_gateway_id
    )
    resolved_context = {
        "model_deployment_id": deployment.id,
        "model_family": deployment.model_family,
        "training_job_id": deployment.job_id,
        "artifact_id": artifact_id,
        "artifact_sha256": str(artifact_ref.get("sha256") or model_context.get("artifact_sha256") or "").strip(),
        "deployment_status": deployment.status,
        "target_gateway_id": target_gateway_id,
        "bound_gateway_id": bound_gateway_id if bound_gateway_id and bound_gateway_id != target_gateway_id else "",
        "inference_gateway_fallback_reason": fallback_reason or "",
        "inference_ready": "true",
    }
    resolved_context = {
        key: _clean_chat_context_text(value)
        for key, value in resolved_context.items()
        if value
    }
    prompt = _chat_message_with_model_context(message, resolved_context)
    model_profile = _chat_deployment_model_profile(deployment, job)
    max_new_tokens = _chat_model_max_new_tokens(model_profile, instance)
    payload = {
        "deployment_id": deployment.id,
        "profile": model_profile,
        "runtime_profile": str(getattr(deployment, "deployment_runtime_profile", "") or "")[:80] or None,
        "artifact_uri": artifact_uri,
        "artifact_sha256": str(artifact_ref.get("sha256") or model_context.get("artifact_sha256") or "").strip() or None,
        "prompt": prompt,
        "max_new_tokens": max_new_tokens,
        "timeout_seconds": 300,
        "model_context": resolved_context,
        "generation_policy": {
            "mode": "best_effort_by_model_and_gateway",
            "max_new_tokens": max_new_tokens,
        },
    }
    return gateway_id, getattr(instance, "bridge_gateway_kind", None), payload


async def _chat_model_inference_events(
    current_user: User,
    *,
    message: str,
    model_context: dict[str, str],
):
    from app.database import async_session_factory

    run_id = f"sf-model-{secrets.token_hex(12)}"
    async with async_session_factory() as db:
        gateway_id, gateway_kind, payload = await _prepare_chat_model_inference(
            db,
            current_user,
            message=message,
            model_context=model_context,
        )
    yield {
        "state": "started",
        "runId": run_id,
        "backend": "bridge_local_lora_inference",
        "gatewayId": gateway_id,
        "modelDeploymentId": payload.get("deployment_id"),
    }
    result = await AIClawClient(gateway_id, gateway_kind=gateway_kind).run_training_inference(
        payload,
        timeout=int(payload.get("timeout_seconds") or 300),
    )
    text = _chat_inference_text(result)
    metrics = _chat_inference_metrics(result, gateway_id=gateway_id, gateway_kind=gateway_kind, payload=payload)
    yield {
        "state": "final",
        "runId": run_id,
        "backend": "bridge_local_lora_inference",
        "gatewayId": gateway_id,
        "modelDeploymentId": payload.get("deployment_id"),
        "delta": text,
        "text": text,
        "metrics": metrics,
        "finishReason": result.get("finish_reason") if isinstance(result, dict) else None,
    }


async def _resolve_chat_model_context(
    db: AsyncSession,
    current_user: User,
    value: Any,
) -> dict[str, str]:
    context = _clean_chat_model_context(value)
    deployment_id = context.get("model_deployment_id")
    if deployment_id:
        return await _load_chat_model_deployment_context(
            db,
            current_user,
            deployment_id=deployment_id,
            requested_training_job_id=context.get("training_job_id"),
            requested_artifact_id=context.get("artifact_id"),
            requested_gateway_id=context.get("target_gateway_id"),
        )

    training_job_id = context.get("training_job_id")
    if not training_job_id:
        return context

    from app.training.models import TrainingJob, TrainingModelDeployment

    job = await db.get(TrainingJob, training_job_id)
    if job is None:
        raise AppError("NOT_FOUND", 404, {"detail": "training job not found"})
    await _ensure_chat_model_department_access(db, current_user, job.department)

    deployments = (
        await db.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.job_id == job.id)
            .where(TrainingModelDeployment.status.in_(MODEL_CHAT_DEPLOYMENT_STATUSES))
            .order_by(
                TrainingModelDeployment.updated_at.desc(),
                TrainingModelDeployment.created_at.desc(),
                TrainingModelDeployment.activated_at.desc().nullslast(),
                TrainingModelDeployment.id.desc(),
            )
        )
    ).scalars().all()
    deployment = None
    fallback_deployment = None
    for item in deployments:
        if fallback_deployment is None:
            fallback_deployment = item
        if not await _chat_model_context_disabled_reason(
            db,
            current_user,
            item,
            job,
            requested_gateway_id=context.get("target_gateway_id") or "",
        ):
            deployment = item
            break
    deployment = deployment or fallback_deployment
    if deployment is not None:
        return await _load_chat_model_deployment_context(
            db,
            current_user,
            deployment_id=deployment.id,
            requested_training_job_id=job.id,
            requested_artifact_id=context.get("artifact_id"),
            requested_gateway_id=context.get("target_gateway_id"),
        )

    context["training_job_id"] = job.id
    context["model_family"] = _clean_chat_context_text(context.get("model_family") or job.target_skill_id or job.title)
    return context


def _chat_message_with_model_context(message: str, model_context: dict[str, str]) -> str:
    if not model_context:
        return message
    lines = [
        "<|im_start|>system",
        "你是 SkillForge 已部署训练模型的对话接口。下列上下文只用于路由和审计，禁止复述或解释这些上下文字段；直接回答用户问题。",
        "如果用户要求做模型测试对话或说明适用场景，必须完整输出“输入、输出、适用场景”三段；输出控制在 1200 字以内，避免展开过长，并用完整句子收尾。",
        "禁止输出 Thinking Process、Reasoning、思考过程、推理过程或内部分析。",
        "",
        "模型上下文：",
    ]
    if model_context.get("model_family"):
        lines.append(f"模型: {model_context['model_family']}")
    if model_context.get("model_deployment_id"):
        lines.append(f"部署ID: {model_context['model_deployment_id']}")
    if model_context.get("training_job_id"):
        lines.append(f"训练任务: {model_context['training_job_id']}")
    if model_context.get("artifact_id"):
        lines.append(f"产物: {model_context['artifact_id']}")
    if model_context.get("artifact_sha256"):
        lines.append(f"产物SHA256: {model_context['artifact_sha256']}")
    if model_context.get("target_gateway_id"):
        lines.append(f"训练/推理节点: {model_context['target_gateway_id']}")
    if model_context.get("bound_gateway_id"):
        lines.append(f"原绑定节点: {model_context['bound_gateway_id']}")
    if model_context.get("inference_gateway_fallback_reason"):
        lines.append(f"推理节点切换: {model_context['inference_gateway_fallback_reason']}")
    if model_context.get("deployment_status"):
        lines.append(f"部署状态: {model_context['deployment_status']}")
    lines.append("<|im_end|>")
    lines.append("<|im_start|>user")
    lines.append(message)
    lines.append("<|im_end|>")
    lines.append("<|im_start|>assistant")
    return "\n".join(lines)


@router.post("/chat/model-context/validate")
async def validate_chat_model_context(
    payload: ChatModelContextValidationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    try:
        context = await _resolve_chat_model_context(db, current_user, payload.model_context or {})
    except AppError as exc:
        if exc.code in {"AUTH_DEPARTMENT_DENIED", "NOT_FOUND"}:
            raise
        return {
            "ready": False,
            "disabled_reason": _chat_error_message(exc),
            "code": exc.code,
            "context": _clean_chat_model_context(payload.model_context or {}),
        }

    deployment_id = context.get("model_deployment_id")
    if not deployment_id:
        return {
            "ready": False,
            "disabled_reason": "当前对话没有训练后模型部署上下文",
            "context": context,
        }
    disabled_reason = context.get("inference_disabled_reason") or ""
    return {
        "ready": not disabled_reason,
        "disabled_reason": disabled_reason,
        "context": context,
    }


async def _get_instance(db: AsyncSession, instance_id: str) -> OpenClawInstance:
    result = await db.execute(select(OpenClawInstance).where(OpenClawInstance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise AppError("AICLAW_INSTANCE_NOT_FOUND", 404)
    return instance


async def _get_instance_or_none(instance_id: str) -> OpenClawInstance | None:
    """不需要 db session 的查询（WS 端点用）"""
    try:
        from app.database import async_session_factory
        async with async_session_factory() as db:
            result = await db.execute(select(OpenClawInstance).where(OpenClawInstance.id == instance_id))
            return result.scalar_one_or_none()
    except Exception:
        return None


async def _get_accessible_chat_instance(instance_id: str, current_user: User) -> OpenClawInstance:
    from app.database import async_session_factory

    async with async_session_factory() as db:
        instance = await db.get(OpenClawInstance, instance_id)
        if instance is None:
            raise AppError("AICLAW_INSTANCE_NOT_FOUND", 404)
        await _ensure_instance_access(db, instance, current_user)
        return instance


def _normalize_text(value: str | None) -> str:
    return (value or "").strip()


def _path_segments(path: str | None) -> list[str]:
    return [item for item in (path or "").split("/") if item]


def _can_view_all_instances(user: User) -> bool:
    return role_matches_any(user, ("admin",)) or bool(getattr(user, "can_view_all", False))


async def _resolve_tasktree_lv1_department(
    db: AsyncSession,
    department: str | None,
    *,
    allow_unknown: bool = False,
) -> str | None:
    """把部门 ID/名称归一到任务树一级部门名称；AI 保留为虚拟组。"""
    raw = _normalize_text(department)
    if not raw:
        return None
    if raw in {"AI", "tasktree-virtual-ai", "AI 组"}:
        return "AI"

    rows = (await db.execute(select(OrgUnit).where(OrgUnit.type == "department"))).scalars().all()
    if not rows:
        return raw

    by_id = {unit.id: unit for unit in rows if unit.id}
    by_name = {unit.name: unit for unit in rows if unit.name}
    lv1_by_id = {
        unit.id: unit
        for unit in rows
        if unit.id and unit.parent_id == settings.TASKTREE_TOP_LEVEL_PARENT_ID
    }
    lv1_by_name = {unit.name: unit for unit in lv1_by_id.values() if unit.name}

    if not lv1_by_id:
        return raw
    if raw in lv1_by_id:
        return lv1_by_id[raw].name
    if raw in lv1_by_name:
        return raw

    node = by_id.get(raw) or by_name.get(raw)
    if node is not None:
        for segment_id in _path_segments(node.path):
            lv1 = lv1_by_id.get(segment_id)
            if lv1 is not None:
                return lv1.name

    if allow_unknown:
        return raw
    raise AppError(
        "AICLAW_DEPARTMENT_INVALID",
        400,
        {"detail": "部门必须来自任务树一级部门"},
    )


async def _normalize_instance_department_for_write(
    db: AsyncSession,
    department: str | None,
    current_user: User,
    *,
    allow_platform_default: bool = False,
) -> str | None:
    requested_department = _normalize_text(department)
    if not requested_department and allow_platform_default and _can_view_all_instances(current_user):
        return None
    raw = requested_department or _normalize_text(current_user.department)
    if not raw:
        raise AppError("AICLAW_DEPARTMENT_REQUIRED", 400, {"detail": "请选择部门"})

    resolved = await _resolve_tasktree_lv1_department(db, raw, allow_unknown=False)
    if _can_view_all_instances(current_user):
        return resolved or raw

    user_department = await _resolve_tasktree_lv1_department(
        db,
        current_user.department,
        allow_unknown=True,
    )
    if not user_department or resolved != user_department:
        raise AppError(
            "AUTH_DEPARTMENT_DENIED",
            403,
            {"detail": "Agent 终端只能归属当前账号所在部门"},
        )
    return resolved


async def _ensure_instance_access(
    db: AsyncSession,
    instance: OpenClawInstance,
    current_user: User,
) -> None:
    if _can_view_all_instances(current_user):
        return
    user_department = await _resolve_tasktree_lv1_department(
        db,
        current_user.department,
        allow_unknown=True,
    )
    instance_department = await _resolve_tasktree_lv1_department(
        db,
        instance.department,
        allow_unknown=True,
    )
    if user_department and instance_department and user_department == instance_department:
        return
    raise AppError("AUTH_DEPARTMENT_DENIED", 403, {"detail": "无权访问该 Agent 终端"})


async def _ensure_skill_sync_access(db: AsyncSession, skill, current_user: User) -> None:
    """Skill 下发权限：管理员全局；普通账号只能下发自己部门/自己创建的 Skill。"""
    if _can_view_all_instances(current_user):
        return
    if getattr(skill, "owner", None) == current_user.id:
        return
    user_department = await _resolve_tasktree_lv1_department(
        db,
        current_user.department,
        allow_unknown=True,
    )
    skill_department = await _resolve_tasktree_lv1_department(
        db,
        getattr(skill, "department", None),
        allow_unknown=True,
    )
    if user_department and skill_department and user_department == skill_department:
        return
    raise AppError("AUTH_DEPARTMENT_DENIED", 403, {"detail": "只能下发当前账号所在部门的 Skill"})


def _issue_enrollment_payload(instance: OpenClawInstance) -> dict[str, Any]:
    token, expires_at = generate_enrollment_token()
    instance.enrollment_token_hash = hash_enrollment_token(token)
    instance.enrollment_expires_at = expires_at
    instance.enrollment_consumed_at = None
    return {
        "enrollment_token": token,
        "expires_at": isoformat_bjt(expires_at),
    }


def _safe_int(value: Any) -> int:
    try:
        number = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return max(number, 0)


def _safe_float(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return max(number, 0.0)


def _round_gb_from_mb(value: Any) -> float:
    return round(_safe_float(value) / 1024, 1)


def _training_summary_from_capabilities(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        cap = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(cap, dict):
        return None

    gpu_items = [item for item in (cap.get("gpu") or []) if isinstance(item, dict)][:64]
    raw_training = cap.get("training") if isinstance(cap.get("training"), dict) else {}
    supported_tasks = [
        task
        for task in (str(item or "").strip().lower() for item in (raw_training.get("supported_tasks") or []))
        if task in TRAINING_TASKS_ALLOWED
    ][:10]
    if not supported_tasks and (gpu_items or raw_training.get("gateway")):
        supported_tasks = ["lora", "qlora", "eval", "merge", "inference"]
    elif not supported_tasks and raw_training:
        supported_tasks = ["eval"]

    if not raw_training and not gpu_items:
        return None

    gpu_count = len(gpu_items)
    total_mb = sum(_safe_float(item.get("vram_total_mb")) for item in gpu_items)
    free_mb = sum(_safe_float(item.get("vram_free_mb")) for item in gpu_items)
    return {
        "gateway": bool(raw_training.get("gateway") or gpu_count),
        "supported_tasks": supported_tasks,
        "gpu_count": max(_safe_int(raw_training.get("gpu_count")), gpu_count),
        "worker_count": max(_safe_int(raw_training.get("worker_count")), gpu_count),
        "vram_total_gb": _round_gb_from_mb(total_mb),
        "vram_free_gb": _round_gb_from_mb(free_mb),
    }


async def _active_training_jobs_by_instance(
    db: AsyncSession,
    current_user: User,
    instance_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    ids = [item for item in dict.fromkeys(str(value or "").strip() for value in instance_ids) if item]
    if not ids:
        return {}
    from app.training.models import TrainingJob

    stmt = (
        select(TrainingJob)
        .where(TrainingJob.target_gateway_id.in_(ids))
        .where(TrainingJob.status.in_(AICLAW_ACTIVE_TRAINING_JOB_STATUSES))
        .where(or_(TrainingJob.status != "queued", TrainingJob.failure_stage.is_distinct_from("dispatch")))
        .order_by(TrainingJob.updated_at.desc(), TrainingJob.created_at.desc())
    )
    if not _can_view_all_instances(current_user):
        user_department = await _resolve_tasktree_lv1_department(
            db,
            current_user.department,
            allow_unknown=True,
        )
        stmt = stmt.where(TrainingJob.department == user_department if user_department else false())
    rows = (await db.execute(stmt.limit(500))).scalars().all()
    grouped: dict[str, list[dict[str, Any]]] = {item: [] for item in ids}
    for job in rows:
        gateway_id = str(job.target_gateway_id or "")
        if gateway_id in grouped and len(grouped[gateway_id]) < 8:
            grouped[gateway_id].append(_serialize_resource_job_summary(job))
    return grouped


@router.get("/instances")
@cached(
    "agent:instances",
    ttl=settings.CACHE_TTL_AGENT,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def list_instances(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    stmt = select(OpenClawInstance).order_by(
        OpenClawInstance.is_platform_default.desc(),
        OpenClawInstance.name,
    )
    if not _can_view_all_instances(current_user):
        stmt = stmt.where(OpenClawInstance.is_active == True)  # noqa: E712
        user_department = await _resolve_tasktree_lv1_department(
            db,
            current_user.department,
            allow_unknown=True,
        )
        stmt = stmt.where(
            OpenClawInstance.department == user_department if user_department else false()
        )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    active_jobs = await _active_training_jobs_by_instance(
        db,
        current_user,
        [item.id for item in rows],
    )
    return [
        _serialize_instance(item, active_training_jobs=active_jobs.get(item.id, []))
        for item in rows
    ]


@router.get("/instances/{instance_id}")
@cached(
    "agent:instances:detail",
    ttl=settings.CACHE_TTL_AGENT,
    scope_by=_CACHE_SCOPE,
    key_builder=_path_cache_key("instance_id"),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_instance(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    active_jobs = await _active_training_jobs_by_instance(db, current_user, [instance.id])
    return _serialize_instance(instance, active_training_jobs=active_jobs.get(instance.id, []))


@router.get("/instances/{instance_id}/status")
async def get_instance_status(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    client = AIClawClient(instance_id)
    try:
        agents = await client.list_agents()
        return {
            "online": True,
            "agents_count": len(agents),
            "agents": agents,
            "auth_mode": "device_pubkey",
        }
    except AppError as exc:
        if exc.code == "BRIDGE_OFFLINE":
            return {
                "online": False,
                "agents_count": 0,
                "agents": [],
                "auth_mode": "device_pubkey",
            }
        raise


@router.post("/instances")
async def create_instance(
    body: AIClawInstanceRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*AGENT_TERMINAL_ROLES)),
):
    # 防止 unique violation 冒泡成 500
    existing = await db.scalar(select(OpenClawInstance).where(OpenClawInstance.id == body.id))
    if existing:
        raise AppError("AICLAW_INSTANCE_EXISTS", 409, {"detail": f"实例 ID {body.id} 已存在"})

    # Hermes 实例默认不同的 gateway URL（避开 Vite dev :3000，走 HERMES_DEFAULT_URL 默认 :3200）
    default_url = settings.AICLAW_LOCAL_DEFAULT if body.agent_type != "hermes" else settings.HERMES_DEFAULT_URL
    department = await _normalize_instance_department_for_write(
        db,
        body.department,
        current_user,
        allow_platform_default=bool(body.is_platform_default),
    )

    reload_hook_url = _normalize_reload_hook_url(
        body.reload_hook_url,
        default="http://127.0.0.1:9000/reload",
    )
    instance = OpenClawInstance(
        id=body.id,
        name=body.name,
        department=department,
        agent_type=body.agent_type,
        gateway_url=body.gateway_url or default_url,
        reload_hook_url=reload_hook_url,
        reload_token=body.reload_token or "",
        auth_token=body.auth_token,
        network_zone=body.network_zone,
        is_active=body.is_active,
        is_platform_default=bool(body.is_platform_default) if _can_view_all_instances(current_user) else False,
        agent_purpose=_normalize_agent_purpose(body.agent_purpose),
        connection_mode="bridge" if body.agent_type == "aiclaw" else "http",
        device_pubkey=None,
        bridge_fingerprint=None,
        pending_rotation=False,
        rotation_token_hash=None,
        rotation_expires_at=None,
    )
    enrollment = _issue_enrollment_payload(instance)
    db.add(instance)
    await db.commit()
    await audit.log(current_user.id, "aiclaw.instance.create", "aiclaw_instance", instance.id, ip_address=request.client.host if request.client else None)
    await invalidate_agent_cache()
    return {
        **_serialize_instance(instance),
        **enrollment,
    }


@router.put("/instances/{instance_id}")
async def update_instance(
    instance_id: str,
    body: AIClawInstanceRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*AGENT_TERMINAL_ROLES)),
):
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    department = await _normalize_instance_department_for_write(
        db,
        body.department,
        current_user,
        allow_platform_default=bool(body.is_platform_default),
    )
    previous_purpose = _normalize_agent_purpose(getattr(instance, "agent_purpose", None))
    next_purpose = _normalize_agent_purpose(body.agent_purpose)
    instance.name = body.name
    instance.department = department
    instance.agent_type = body.agent_type
    instance.agent_purpose = next_purpose
    instance.gateway_url = body.gateway_url or instance.gateway_url
    instance.reload_hook_url = _normalize_reload_hook_url(
        body.reload_hook_url,
        default=instance.reload_hook_url,
    )
    instance.reload_token = body.reload_token or instance.reload_token
    instance.auth_token = body.auth_token
    instance.network_zone = body.network_zone
    instance.is_active = body.is_active
    if _can_view_all_instances(current_user):
        instance.is_platform_default = bool(body.is_platform_default)
    await db.commit()
    if previous_purpose in SKILL_RUNTIME_AGENT_PURPOSES and next_purpose not in SKILL_RUNTIME_AGENT_PURPOSES:
        try:
            from app.execution.sync_service import sync_service

            await sync_service.push_schedules_to_targets(target_instance_ids=[instance.id])
        except Exception as exc:  # noqa: BLE001
            logger.warning("切换 Agent 用途后清空定时快照失败 instance={}: {}", instance.id, exc)
    await audit.log(current_user.id, "aiclaw.instance.update", "aiclaw_instance", instance.id, ip_address=request.client.host if request.client else None)
    await invalidate_agent_cache()
    return _serialize_instance(instance)


@router.delete("/instances/{instance_id}")
async def delete_instance(
    instance_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*AGENT_TERMINAL_ROLES)),
):
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    await db.delete(instance)
    await db.commit()
    await audit.log(current_user.id, "aiclaw.instance.delete", "aiclaw_instance", instance.id, ip_address=request.client.host if request.client else None)
    await invalidate_agent_cache()
    return {"ok": True}


@router.post("/instances/{instance_id}/regenerate-token")
async def regenerate_token(
    instance_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*AGENT_TERMINAL_ROLES)),
):
    return await regenerate_enrollment(instance_id, request, db, current_user)


@router.post("/instances/{instance_id}/regenerate-enrollment")
async def regenerate_enrollment(
    instance_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*AGENT_TERMINAL_ROLES)),
):
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    enrollment = _issue_enrollment_payload(instance)
    await db.commit()
    await audit.log(current_user.id, "aiclaw.enrollment.regenerate", "aiclaw_instance", instance.id, ip_address=request.client.host if request.client else None)
    await invalidate_agent_cache()
    return {"instance_id": instance.id, **enrollment}


@router.post("/instances/{instance_id}/revoke-pubkey")
async def revoke_pubkey(
    instance_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*AGENT_TERMINAL_ROLES)),
):
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    instance.device_pubkey = None
    instance.bridge_fingerprint = None
    instance.bridge_connected_at = None
    instance.pending_rotation = False
    instance.rotation_token_hash = None
    instance.rotation_expires_at = None
    clear_rotation_token(instance.id)
    await db.commit()
    # 立即踢掉当前在线的 bridge 连接，下次重连必须重新 enroll
    kicked = await bridge_registry.kick(instance.id, reason="pubkey revoked")
    await audit.log(
        current_user.id,
        "aiclaw.pubkey.revoke",
        "aiclaw_instance",
        instance.id,
        detail={"kicked": kicked},
        ip_address=request.client.host if request.client else None,
    )
    await invalidate_agent_cache()
    return {"instance_id": instance.id, "revoked": True, "kicked": kicked}


@router.post("/instances/{instance_id}/rotate-key")
async def rotate_key(
    instance_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*AGENT_TERMINAL_ROLES)),
):
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    rotation_token, expires_at = generate_rotation_token(instance.id)
    instance.pending_rotation = True
    instance.rotation_token_hash = hash_enrollment_token(rotation_token)
    instance.rotation_expires_at = expires_at
    await db.commit()
    await audit.log(current_user.id, "aiclaw.key.rotate", "aiclaw_instance", instance_id, ip_address=request.client.host if request.client else None)
    await invalidate_agent_cache()
    return {
        "instance_id": instance.id,
        "pending_rotation": True,
        "rotation_token": rotation_token,
        "expires_at": isoformat_bjt(expires_at),
    }


@router.post("/instances/{instance_id}/reset-binding")
async def reset_binding(
    instance_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*AGENT_TERMINAL_ROLES)),
):
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    instance.bridge_fingerprint = None
    await db.commit()
    await audit.log(current_user.id, "aiclaw.binding.reset", "aiclaw_instance", instance.id, ip_address=request.client.host if request.client else None)
    await invalidate_agent_cache()
    return {"instance_id": instance.id, "binding_reset": True}


@router.get("/instances/{instance_id}/systemd-unit")
async def download_systemd_unit(
    instance_id: str,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*AGENT_TERMINAL_ROLES)),
):
    """生成对应的 systemd unit 文件，admin 一键拿到部署包。"""
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    unit_text = (
        "[Unit]\n"
        f"Description=SkillForge AIClaw Bridge — {instance.id}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart=/usr/bin/python3 -u /opt/skillforge_bridge/{instance.id}/skillforgebridge_{instance.id}.py\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "StartLimitIntervalSec=300\n"
        "StartLimitBurst=10\n"
        f"WorkingDirectory=/opt/skillforge_bridge/{instance.id}\n"
        "\n"
        "# 安全收紧\n"
        "NoNewPrivileges=true\n"
        "PrivateTmp=true\n"
        "ProtectSystem=strict\n"
        "ProtectHome=read-only\n"
        "ReadWritePaths=%h/.skillforge_bridge\n"
        "\n"
        "# 资源限制\n"
        "MemoryMax=200M\n"
        "TasksMax=64\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    await audit.log(
        current_user.id,
        "aiclaw.systemd.download",
        "aiclaw_instance",
        instance.id,
        ip_address=request.client.host if request and request.client else None,
    )
    return Response(
        content=unit_text,
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="openclaw-bridge-{instance.id}.service"'
        },
    )


BRIDGE_SELF_UPDATE_TS_TOLERANCE_SECONDS = 300


async def _verify_bridge_self_update_sig(
    db: AsyncSession,
    instance_id: str,
    platform: str,
    ts: str,
    sig: str,
) -> OpenClawInstance:
    """Ed25519 challenge-response 校验 bridge 自更新 HTTP 请求。

    challenge = f"{instance_id}:{platform}:{ts}"，bridge 用 device_privkey 签名；
    server 从 DB 取 instance.device_pubkey 验签。ts 允许 ±300 秒偏差防重放。

    成功返回 instance；失败统一 401 BRIDGE_UPDATE_SIG_INVALID（避免区分原因被探测）。
    """
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        raise AppError("BRIDGE_UPDATE_SIG_INVALID", 401, {"detail": "bad ts"})
    now = int(time.time())
    if abs(now - ts_int) > BRIDGE_SELF_UPDATE_TS_TOLERANCE_SECONDS:
        raise AppError("BRIDGE_UPDATE_SIG_INVALID", 401, {"detail": "ts out of range"})

    instance = await _get_instance(db, instance_id)
    if not instance.device_pubkey:
        raise AppError("BRIDGE_UPDATE_SIG_INVALID", 401, {"detail": "instance not enrolled"})

    challenge = f"{instance_id}:{platform}:{ts}"
    if not verify_signature(instance.device_pubkey, challenge, sig):
        raise AppError("BRIDGE_UPDATE_SIG_INVALID", 401, {"detail": "bad sig"})
    return instance


def _mlx_wheelhouse_dir() -> Path:
    return Path(os.environ.get("SKILLFORGE_MLX_WHEELHOUSE_DIR") or "/tmp/sf-mlx-wheelhouse").expanduser()


@router.get("/bridge/mlx-wheelhouse/")
async def list_mlx_wheelhouse():
    root = _mlx_wheelhouse_dir()
    files = sorted(path.name for path in root.glob("*.whl") if path.is_file()) if root.is_dir() else []
    links = "\n".join(
        f'<a href="{quote(name)}">{html.escape(name)}</a><br/>'
        for name in files
    )
    return Response(
        content=f"<!doctype html><html><body>{links}</body></html>",
        media_type="text/html; charset=utf-8",
    )


@router.get("/bridge/mlx-wheelhouse/{filename}")
async def download_mlx_wheelhouse_file(filename: str):
    if not re.match(r"^[A-Za-z0-9_.+!:-]+\.whl$", filename or ""):
        raise AppError("MLX_WHEEL_NOT_FOUND", 404, {"detail": "wheel not found"})
    root = _mlx_wheelhouse_dir().resolve(strict=False)
    path = (root / filename).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError:
        raise AppError("MLX_WHEEL_NOT_FOUND", 404, {"detail": "wheel not found"})
    if not path.is_file():
        raise AppError("MLX_WHEEL_NOT_FOUND", 404, {"detail": "wheel not found"})
    return FileResponse(path, media_type="application/octet-stream", filename=filename)


@router.get("/bridge/script-hash")
async def get_bridge_script_hash(
    platform: str = Query(..., description="目标平台：linux 或 darwin"),
    instance_id: str = Query(..., description="bridge 自身 instance_id"),
    ts: str = Query(..., description="unix 秒时间戳，±300s 容差"),
    sig: str = Query(..., description="Ed25519(device_privkey, f'{instance_id}:{platform}:{ts}') base64"),
    db: AsyncSession = Depends(get_db),
):
    """返回指定平台的 bridge 源码 hash + 版本号，供 bridge 客户端自检更新。

    要求 bridge 用 device_privkey 对 `{instance_id}:{platform}:{ts}` 签名，server 用
    DB 里的 device_pubkey 验签，防止匿名探测和重放攻击。
    """
    await _verify_bridge_self_update_sig(db, instance_id, platform, ts, sig)
    return {
        "version": BRIDGE_VERSION,
        "hash": compute_bridge_script_hash(platform),
        "platform": platform,
    }


@router.get("/bridge/script")
async def download_latest_bridge_script(
    instance_id: str = Query(..., description="bridge 自身 instance_id"),
    platform: str = Query(..., description="目标平台：linux 或 darwin"),
    ts: str = Query(..., description="unix 秒时间戳，±300s 容差"),
    sig: str = Query(..., description="Ed25519(device_privkey, f'{instance_id}:{platform}:{ts}') base64"),
    db: AsyncSession = Depends(get_db),
):
    """bridge 客户端自动更新专用：按 platform 下载最新 bridge 源码（不含 token，仅模板主体）。

    安全模型：同 `/bridge/script-hash`，要求 bridge 签名。未签名 / 错签 / 时间漂移
    均 401。注意：此端点返回的源码仍需 bridge 端在下载后比对 X-Bridge-Hash 与预期
    一致后再 os.execv，以防窗口期 MITM。
    """
    instance = await _verify_bridge_self_update_sig(db, instance_id, platform, ts, sig)
    _ = instance  # 保留以备按 instance 做灰度
    source = read_bridge_source(platform)
    return Response(
        content=source,
        media_type="text/x-python",
        headers={
            "X-Bridge-Version": BRIDGE_VERSION,
            "X-Bridge-Hash": compute_bridge_script_hash(platform),
            "X-Bridge-Platform": platform,
            "Cache-Control": "no-store",
        },
    )


@router.get("/instances/{instance_id}/bridge-script")
async def download_bridge_script(
    instance_id: str,
    one_time_token: str = Query(""),
    platform: str = Query(..., description="目标平台：linux 或 darwin"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*AGENT_TERMINAL_ROLES)),
):
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    if not one_time_token:
        raise AppError("AICLAW_ENROLLMENT_INVALID", 400, {"detail": "missing one_time_token"})
    if instance.enrollment_token_hash:
        if not verify_enrollment_token(one_time_token, instance.enrollment_token_hash):
            raise AppError("AICLAW_ENROLLMENT_INVALID", 400, {"detail": "token mismatch"})
        if instance.enrollment_expires_at and instance.enrollment_expires_at < now_bjt():
            raise AppError("AICLAW_ENROLLMENT_INVALID", 400, {"detail": "token expired"})
    script = render(
        instance_id=instance.id,
        enrollment_token=one_time_token,
        skillforge_ws_url=build_skillforge_bridge_ws_url(),
        platform=platform,
        local_aiclaw_url=instance.gateway_url or settings.AICLAW_LOCAL_DEFAULT,
        local_aiclaw_token=instance.auth_token or "",
    )
    await audit.log(
        current_user.id,
        "aiclaw.script.download",
        "aiclaw_instance",
        instance.id,
        detail={
            "platform": platform,
            "user_agent": request.headers.get("user-agent", "") if request else "",
        },
        ip_address=request.client.host if request and request.client else None,
    )
    return Response(
        content=script,
        media_type="text/x-python",
        headers={
            "Content-Disposition": f'attachment; filename="skillforgebridge_{platform}.py"'
        },
    )


@router.post("/instances/{instance_id}/force-update")
async def force_update_bridge(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active_web_or_cli),
):
    """通过 bridge WS 连接下发 force_update 指令，让远程 bridge 拉取最新脚本并重启。"""
    if not role_matches_any(current_user, AGENT_TERMINAL_ROLES):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    conn = bridge_registry.get(instance_id)
    if not conn:
        raise AppError("BRIDGE_OFFLINE", 409, {"detail": "bridge 不在线"})
    try:
        await conn.ws.send_json({
            "type": "bridge_op",
            "op": "force_update",
            "payload": {},
            "request_id": f"force-update-{secrets.token_hex(4)}",
            "epoch": conn.epoch,
        })
        await audit.log(
            current_user.id,
            "aiclaw.bridge.force_update",
            "aiclaw_instance",
            instance_id,
            ip_address=None,
        )
        await invalidate_agent_cache()
        return {"status": "sent", "instance_id": instance_id}
    except Exception as exc:
        raise AppError("BRIDGE_SEND_FAILED", 502, {"detail": str(exc)})


@router.post("/instances/{instance_id}/disconnect")
async def disconnect_bridge(instance_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(require_role("system_admin"))):
    """强制断开 bridge 的 WebSocket 连接，使其重连。"""
    conn = bridge_registry.get(instance_id)
    if not conn:
        raise AppError("BRIDGE_OFFLINE", 409, {"detail": "bridge 不在线"})
    await conn.ws.close(code=1012, reason="server restart")
    await invalidate_agent_cache()
    return {"status": "disconnected", "instance_id": instance_id}


@router.post("/instances/{instance_id}/debug-capabilities")
async def debug_bridge_capabilities(instance_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(require_role("system_admin"))):
    """请求 bridge 探测本机能力并返回详细调试信息。"""
    conn = bridge_registry.get(instance_id)
    if not conn:
        raise AppError("BRIDGE_OFFLINE", 409, {"detail": "bridge 不在线"})
    try:
        result = await conn.bridge_op("debug_capabilities", {}, timeout=10)
        return result
    except Exception as exc:
        raise AppError("BRIDGE_OP_FAILED", 502, {"detail": str(exc)})


@router.post("/instances/{instance_id}/media/bootstrap")
async def bootstrap_instance_h3_media(
    instance_id: str,
    body: MediaH3BootstrapRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active_web_or_cli),
):
    if not role_matches_any(current_user, ("system_admin",)):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    if not bridge_registry.is_online(instance_id):
        raise AppError("BRIDGE_OFFLINE", 409, {"detail": "bridge 不在线"})
    try:
        result = await AIClawClient(instance_id).bootstrap_h3_media(body.model_dump(), timeout=120)
    except Exception as exc:
        raise AppError("BRIDGE_OP_FAILED", 502, {"detail": str(exc)}) from exc
    await audit.log(
        current_user.id,
        "aiclaw.media.bootstrap_h3",
        "aiclaw_instance",
        instance_id,
        detail={
            "profile": body.profile,
            "bandwidth_limit_mbps": body.bandwidth_limit_mbps,
            "force": body.force,
            "dry_run": body.dry_run,
            "license_accepted": body.accept_license,
            "result_status": result.get("status") if isinstance(result, dict) else None,
        },
        ip_address=request.client.host if request.client else None,
    )
    return result


@router.get("/instances/{instance_id}/media/bootstrap")
async def get_instance_h3_media_bootstrap(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active_web_or_cli),
):
    if not role_matches_any(current_user, ("system_admin",)):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    if not bridge_registry.is_online(instance_id):
        raise AppError("BRIDGE_OFFLINE", 409, {"detail": "bridge 不在线"})
    try:
        return await AIClawClient(instance_id).get_h3_media_bootstrap_status(timeout=30)
    except Exception as exc:
        raise AppError("BRIDGE_OP_FAILED", 502, {"detail": str(exc)}) from exc


@router.post("/instances/{instance_id}/media/bootstrap/cancel")
async def cancel_instance_h3_media_bootstrap(
    instance_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active_web_or_cli),
):
    if not role_matches_any(current_user, ("system_admin",)):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    if not bridge_registry.is_online(instance_id):
        raise AppError("BRIDGE_OFFLINE", 409, {"detail": "bridge 不在线"})
    try:
        result = await AIClawClient(instance_id).cancel_h3_media_bootstrap(timeout=30)
    except Exception as exc:
        raise AppError("BRIDGE_OP_FAILED", 502, {"detail": str(exc)}) from exc
    await audit.log(
        current_user.id,
        "aiclaw.media.bootstrap_cancel",
        "aiclaw_instance",
        instance_id,
        detail={"result_status": result.get("status") if isinstance(result, dict) else None},
        ip_address=request.client.host if request.client else None,
    )
    return result


@router.get("/instances/{instance_id}/agents")
@cached(
    "agent:instances:agents",
    ttl=settings.CACHE_TTL_AGENT,
    scope_by=_CACHE_SCOPE,
    key_builder=_path_cache_key("instance_id"),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_agents(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    try:
        instance = await _get_instance(db, instance_id)
        await _ensure_instance_access(db, instance, current_user)
        kind = instance.bridge_gateway_kind
        return {"items": await AIClawClient(instance_id, gateway_kind=kind).list_agents()}
    except AppError as exc:
        await _annotate_bridge_error(exc, db, instance_id)
        raise


@router.get("/instances/{instance_id}/agents/{agent_id}/skills")
@cached(
    "agent:instances:agent-skills",
    ttl=settings.CACHE_TTL_AGENT,
    scope_by=_CACHE_SCOPE,
    key_builder=_path_cache_key("instance_id", "agent_id"),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_agent_skills(
    instance_id: str,
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    try:
        instance = await _get_instance(db, instance_id)
        await _ensure_instance_access(db, instance, current_user)
        kind = instance.bridge_gateway_kind
        items = await AIClawClient(instance_id, gateway_kind=kind).list_skills(agent_id)
        return {"items": await _enrich_agent_skills_with_git(db, instance_id=instance_id, items=items)}
    except AppError as exc:
        await _annotate_bridge_error(exc, db, instance_id)
        raise


async def _annotate_bridge_error(exc: AppError, db: AsyncSession, instance_id: str) -> None:
    """给 AppError.detail 补上 gateway_kind 和 hint，方便 UI 区分 AIClaw vs openclaw，
    以及给出下一步可操作的排查提示。
    """
    try:
        instance = await db.get(OpenClawInstance, instance_id)
    except Exception:  # noqa: BLE001
        instance = None
    detail = dict(exc.detail or {})
    if instance is not None:
        detail.setdefault("gateway_kind", instance.bridge_gateway_kind)
        detail.setdefault("bridge_platform", instance.bridge_platform)
    method = detail.get("method")
    bridge_err = detail.get("bridge_error")
    hints: list[str] = []
    if exc.code == "BRIDGE_OFFLINE":
        hints.append("代理设备未连接 —— 检查内网 bridge 进程是否在跑、能否访问 SkillForge")
    elif exc.code in {"AICLAW_ERROR", "BRIDGE_OP_ERROR"} and bridge_err:
        kind = detail.get("gateway_kind")
        if kind and kind != "aiclaw":
            hints.append(
                f"本地 gateway 类型是 {kind}（非 AIClaw），method `{method}` 可能在该"
                "协议下不受支持或返回结构不同。请对比 bridge 日志里的原始错误。"
            )
        hints.append(
            f"查看 bridge 日志：ssh <host>; tail -n 80 ~/.skillforge_bridge/{instance_id}/bridge.log"
        )
    if hints:
        detail["hints"] = hints
    exc.detail = detail


@router.get("/instances/{instance_id}/capabilities")
@cached(
    "agent:instances:capabilities",
    ttl=settings.CACHE_TTL_AGENT,
    scope_by=_CACHE_SCOPE,
    key_builder=_path_cache_key("instance_id"),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_instance_capabilities(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """返回 bridge 上报的本机能力（gateway 类型 / 平台 / 可写 skills 目录 / 硬件资源）。"""
    import json as _json
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)
    result = {
        "gateway_kind": instance.bridge_gateway_kind,
        "gateway_version": instance.bridge_gateway_version,
        "platform": instance.bridge_platform,
        "skills_dir": instance.bridge_skills_dir,
        "skills_dirs": _json.loads(instance.bridge_skills_dirs_json) if instance.bridge_skills_dirs_json else [],
        "bridge_version": instance.bridge_version,
    }
    # 从完整 capabilities JSON 中提取硬件资源信息
    if instance.bridge_capabilities_json:
        try:
            cap = _json.loads(instance.bridge_capabilities_json)
            if cap.get("disk"):
                result["disk"] = cap["disk"]
            if cap.get("memory"):
                result["memory"] = cap["memory"]
            if cap.get("gpu"):
                result["gpu"] = cap["gpu"]
            if cap.get("runtimes"):
                result["runtimes"] = cap["runtimes"]
            if cap.get("ops"):
                result["ops"] = cap["ops"]
            if cap.get("training"):
                result["training"] = cap["training"]
            if cap.get("media"):
                result["media"] = cap["media"]
            if cap.get("workload_roles"):
                result["workload_roles"] = cap["workload_roles"]
        except Exception:
            pass
    return result


@router.post("/instances/{instance_id}/sync-skill/{skill_id}")
async def sync_skill_to_device(
    instance_id: str,
    skill_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer", "dept_admin", "aibp", "biz_owner")),
):
    """把 SkillForge 仓库里的某个 skill 推到 bridge 主机的本地 skills 目录。

    通过 bridge 的 bridge_op('install_skill') 通道，bridge 在设备本地写文件。
    """
    from app.skills.core.models import Skill
    from app.execution.sync_service import sync_service

    skill = await db.get(Skill, skill_id)
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)
    await _ensure_skill_sync_access(db, skill, current_user)

    # 根据实例 agent_type 选择下发方式
    instance = await _get_instance(db, instance_id)
    await _ensure_instance_access(db, instance, current_user)

    sync_result = await sync_service.create_and_run_sync_job(
        skill_id,
        trigger="manual",
        actor=current_user,
        target_instance_ids=[instance_id],
        push_git=True,
    )
    results = sync_result.get("instances", [])
    result = results[0] if results else {
        "instance_id": instance_id,
        "name": instance.name,
        "agent_type": getattr(instance, "agent_type", "aiclaw") or "aiclaw",
        "ok": False,
        "error": "NO_SYNC_RESULT",
        "reload": {"ok": False, "status": "skipped", "reason": "no_sync_result"},
    }

    await audit.log(
        current_user.id,
        "aiclaw.skill.sync",
        "aiclaw_instance",
        instance_id,
        detail={"skill_id": skill_id, "result": result},
        ip_address=request.client.host if request.client else None,
    )
    returned_results = results or [result]
    await invalidate_agent_cache()
    return {
        "job_id": sync_result.get("job_id"),
        "ok": bool(result.get("ok")),
        "total": len(returned_results),
        "success": sum(1 for item in returned_results if item.get("ok")),
        "results": returned_results,
    }


@router.get("/sync-targets/{skill_id}")
@cached(
    "agent:sync-targets",
    ttl=settings.CACHE_TTL_AGENT,
    scope_by=_CACHE_SCOPE,
    key_builder=_path_cache_key("skill_id"),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def list_skill_sync_targets(
    skill_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer", "dept_admin", "aibp", "biz_owner")),
):
    """列出某个 Skill 可下发的 Agent 终端。"""
    from app.skills.core.models import Skill
    from app.execution.sync_service import sync_service

    skill = await db.get(Skill, skill_id)
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)
    await _ensure_skill_sync_access(db, skill, current_user)
    return {"items": await sync_service.list_sync_targets(skill_id, actor=current_user)}


@router.post("/sync-skill/{skill_id}")
async def sync_skill_to_targets(
    skill_id: str,
    body: SyncSkillTargetsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer", "dept_admin", "aibp", "biz_owner")),
):
    """按部门/账号规则下发 Skill。

    默认按 Skill 所属部门选择终端；管理员可在 body.instance_ids 中指定任意 active 终端。
    """
    from app.skills.core.models import Skill
    from app.execution.sync_service import sync_service

    skill = await db.get(Skill, skill_id)
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)
    await _ensure_skill_sync_access(db, skill, current_user)

    sync_result = await sync_service.create_and_run_sync_job(
        skill_id,
        trigger="manual",
        actor=current_user,
        target_instance_ids=body.instance_ids,
        department=body.department,
        push_git=True,
    )
    results = sync_result.get("instances", [])
    ok = bool(results) and all(item.get("ok") for item in results)
    await audit.log(
        current_user.id,
        "aiclaw.skill.sync_targets",
        "skill",
        skill_id,
        detail={"results": results, "ok": ok},
        ip_address=request.client.host if request.client else None,
    )
    await invalidate_agent_cache()
    return {
        "job_id": sync_result.get("job_id"),
        "ok": ok,
        "total": len(results),
        "success": sum(1 for item in results if item.get("ok")),
        "results": results,
    }


@router.get("/sync-jobs")
async def get_sync_jobs(
    skill_id: str | None = Query(None),
    review_id: int | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer", "dept_admin", "aibp", "biz_owner")),
):
    """按 skill_id/review_id 查询最近同步状态。"""
    from app.execution.models import SkillSyncJob
    from app.execution.sync_service import sync_service
    from app.skills.core.models import Skill

    resolved_skill_id = skill_id
    if resolved_skill_id is None and review_id is not None:
        job = (
            await db.execute(
                select(SkillSyncJob).where(SkillSyncJob.review_id == review_id).order_by(SkillSyncJob.created_at.desc())
            )
        ).scalars().first()
        resolved_skill_id = job.skill_id if job else None
    if resolved_skill_id:
        skill = await db.get(Skill, resolved_skill_id)
        if skill:
            await _ensure_skill_sync_access(db, skill, current_user)
    return await sync_service.get_recent_sync_status(skill_id=skill_id, review_id=review_id, limit=limit)


@router.post("/sync-jobs/{job_id}/retry")
async def retry_sync_job(
    job_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer", "dept_admin", "aibp", "biz_owner")),
):
    """重试 failed/pending 的同步 job。默认只补发失败/待处理节点。"""
    from app.execution.models import SkillSyncJob
    from app.execution.sync_service import sync_service
    from app.skills.core.models import Skill

    job = await db.get(SkillSyncJob, job_id)
    if not job:
        raise AppError("SYNC_JOB_NOT_FOUND", 404)
    skill = await db.get(Skill, job.skill_id)
    if skill:
        await _ensure_skill_sync_access(db, skill, current_user)
    result = await sync_service.retry_sync_job(job_id, actor=current_user)
    await audit.log(
        current_user.id,
        "aiclaw.skill.sync_retry",
        "skill_sync_job",
        str(job_id),
        detail={"skill_id": job.skill_id, "result": result},
        ip_address=request.client.host if request.client else None,
    )
    await invalidate_agent_cache()
    return result


async def _import_skill_from_device_impl(
    db: AsyncSession,
    *,
    instance_id: str,
    skill_id: str,
    current_user: User,
) -> dict:
    """反向导入 Skill 的业务流程实现（codex review 要求从 router 下沉到 service 层）。

    流程：
      1. 校验 instance 部门访问权
      2. 校验 skill_id 合法性 + 工作区残留检查
      3. bridge 读设备文件
      4. 解析 frontmatter（部门二次校验）
      5. 写 git 仓库 + commit
      6. 写 DB

    错误统一抛 AppError，由 endpoint 转 HTTP。返回结构供 endpoint 直接 JSON 化。
    """
    import base64 as _b64
    import os as _os
    import re as _re
    import yaml as _yaml
    from app.auth.dependencies import require_department_access
    from app.skills.core.git_service import git_service
    from app.skills.core.models import Skill

    # P0-5 部门隔离：必须有该 instance 所属部门的访问权
    instance = await _get_instance(db, instance_id)
    if not require_department_access(instance.department, current_user):
        raise AppError("AUTH_DEPARTMENT_DENIED", 403, {
            "detail": f"无权从 {instance.department} 部门的设备导入 Skill",
        })

    # P0-6 校验 skill_id 合法性，禁止路径穿越字符
    if not skill_id or "/" in skill_id or ".." in skill_id or skill_id.startswith("."):
        raise AppError("SKILL_ID_INVALID", 400, {"detail": "skill_id 含非法字符"})

    # P1-4 行锁：避免并发同名导入双写
    existing = await db.scalar(
        select(Skill).where(Skill.id == skill_id).with_for_update()
    )
    if existing:
        raise AppError("SKILL_ALREADY_EXISTS", 409, {"detail": f"SkillForge 已有 skill '{skill_id}'，请直接编辑"})

    # [M7] git 工作树文件级锁 — 包住 has_uncommitted_changes 检查 + 后续 write/commit,
    # 杜绝 has_uncommitted_changes 与 commit 间的 TOCTOU 时间窗口 (DB 行锁仅锁 DB)
    async with git_service.skill_advisory_lock(skill_id):
        # P0-6 检测本地未提交修改：DB 没记录但 git 工作区已存在残留目录
        repo_skill_dir = _os.path.join(settings.SKILL_REPO_PATH, skill_id)
        if _os.path.exists(repo_skill_dir):
            try:
                dirty = git_service.has_uncommitted_changes(skill_id)
            except Exception:
                dirty = True  # 检查失败保守拒绝
            if dirty:
                raise AppError("GIT_CONFLICT", 409, {
                    "detail": f"工作区残留目录 {repo_skill_dir} 含未提交修改，请先清理或提交后再导入",
                })

        # 1. 通过 bridge 读设备文件
        try:
            result = await AIClawClient(instance_id).read_skill_from_device(skill_id)
        except AppError as exc:
            msg = ""
            if isinstance(exc.detail, dict):
                inner = exc.detail.get("detail") or {}
                if isinstance(inner, dict):
                    msg = inner.get("message") or ""
            if exc.code == "BRIDGE_OP_ERROR" and "not found" in msg.lower():
                raise AppError("SKILL_FILE_NOT_FOUND", 404, {
                    "detail": f"设备上没有 skill '{skill_id}'：{msg}",
                })
            raise
        except Exception as exc:
            raise AppError("AICLAW_ERROR", 502, {"detail": str(exc)})

        files = result.get("files") or []
        if not files:
            raise AppError("SKILL_FILE_NOT_FOUND", 404, {"detail": "设备上 skill 没有任何文件"})

        # 2. 解析 SKILL.md 拿 name 和 frontmatter
        skill_md_content = ""
        for f in files:
            if f.get("path") == "SKILL.md":
                skill_md_content = _b64.b64decode(f["content_b64"]).decode("utf-8", errors="replace")
                break
        name = skill_id
        department = instance.department or current_user.department or "imported"
        role = "operator"
        risk_level = "R2"
        approval_level = 1
        trigger_type = "manual"
        if skill_md_content:
            m = _re.match(r"^---\s*\n(.*?)\n---\s*\n", skill_md_content, _re.DOTALL)
            if m:
                try:
                    fm = _yaml.safe_load(m.group(1)) or {}
                    name = fm.get("name") or fm.get("display_name") or skill_id
                    fm_department = fm.get("department")
                    if fm_department:
                        # [H3] frontmatter 部门越权防御
                        # 漏洞: require_department_access(ai_engineer, ANY) → True, 所以
                        # ai_engineer 用户能让 frontmatter 把 skill 写到任意第三个部门。
                        # 修复: 写入部门必须 ∈ {instance.department, current_user.department},
                        #       admin 例外 (毕竟 admin 是管理员可任意指定)。
                        is_admin = current_user.role == "admin"
                        allowed_departments = {instance.department, current_user.department}
                        allowed_departments.discard(None)
                        if not is_admin and fm_department not in allowed_departments:
                            raise AppError("AUTH_DEPARTMENT_DENIED", 403, {
                                "detail": (
                                    f"frontmatter 声明部门 {fm_department}, 但仅允许写入到 "
                                    f"{sorted(allowed_departments)} (instance 部门或当前用户部门)"
                                ),
                            })
                        # 二次校验 (保留原有 require_department_access 作为防御深度)
                        if not require_department_access(fm_department, current_user):
                            raise AppError("AUTH_DEPARTMENT_DENIED", 403, {
                                "detail": f"Skill frontmatter 声明部门 {fm_department}，但你无权写入",
                            })
                        department = fm_department
                    risk_level = fm.get("risk_level") or risk_level
                    approval_level = int(fm.get("approval_level", approval_level))
                    trigger_type = fm.get("trigger_type") or trigger_type
                except AppError:
                    raise
                except Exception as fm_err:
                    logger.warning("AIClaw 导入 Skill 读取 frontmatter 失败 skill={}: {}", skill_id, fm_err)

        # 3. 写文件到 SKILL_REPO_PATH/<id>/
        git_service.create_skill_dir(skill_id)
        for f in files:
            path = (f.get("path") or "").lstrip("/")
            if not path or ".." in path.split("/"):
                continue
            try:
                content = _b64.b64decode(f["content_b64"]).decode("utf-8", errors="replace")
                git_service.write_file(skill_id, path, content)
            except Exception:
                try:
                    full_path = _os.path.join(settings.SKILL_REPO_PATH, skill_id, path)
                    _os.makedirs(_os.path.dirname(full_path), exist_ok=True)
                    with open(full_path, "wb") as fp:
                        fp.write(_b64.b64decode(f["content_b64"]))
                except Exception as write_err:
                    logger.warning("AIClaw 导入 Skill 写文件失败 skill={} path={}: {}", skill_id, path, write_err)

        # 4. Git commit
        commit_sha = git_service.commit_all(
            f"从 AIClaw 设备 {instance_id} 导入 Skill: {skill_id}",
            current_user.id,
            skill_id=skill_id,
        )

        # 5. 写 DB (在 git 锁内, 确保 git commit 与 db 行原子)
        skill = Skill(
            id=skill_id,
            name=name,
            description=f"从 {instance_id} 导入",
            department=department,
            role=role,
            trigger_type=trigger_type,
            risk_level=risk_level,
            approval_level=approval_level,
            status="draft",
            owner=current_user.id,
            git_commit=commit_sha,
        )
        db.add(skill)
        await db.commit()

    return {
        "ok": True,
        "skill_id": skill_id,
        "name": name,
        "files_imported": len(files),
        "source_dir": result.get("source_dir"),
        "git_commit": commit_sha,
    }


@router.post("/instances/{instance_id}/import-skill/{skill_id}")
async def import_skill_from_device(
    instance_id: str,
    skill_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """从设备 ~/.aiclaw/skills/<skill_id>/ 反向读取并导入到 SkillForge。

    用于：admin 在 AIClawWorkspace 看到一个非平台 Skill，点击想编辑，
    通过这个接口把它"领养"进 SkillForge，之后可以在 SkillStudio 编辑 + 推送回设备。

    Endpoint 只做：依赖注入 + 业务调用 + 审计 + JSON 返回。
    业务流程见 _import_skill_from_device_impl。
    """
    payload = await _import_skill_from_device_impl(
        db,
        instance_id=instance_id,
        skill_id=skill_id,
        current_user=current_user,
    )
    await audit.log(
        current_user.id,
        "aiclaw.skill.import",
        "skill",
        skill_id,
        detail={
            "instance_id": instance_id,
            "files": payload.get("files_imported"),
            "source_dir": payload.get("source_dir"),
        },
        ip_address=request.client.host if request.client else None,
    )
    await invalidate_agent_cache()
    return payload


@router.delete("/instances/{instance_id}/sync-skill/{skill_id}")
async def remove_skill_from_device(
    instance_id: str,
    skill_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """从设备本地 skills 目录移除某个 skill。"""
    try:
        result = await AIClawClient(instance_id).remove_skill(skill_id)
    except AppError:
        raise
    except Exception as exc:
        raise AppError("AICLAW_ERROR", 502, {"detail": str(exc)})
    await audit.log(
        current_user.id,
        "aiclaw.skill.remove",
        "aiclaw_instance",
        instance_id,
        detail={"skill_id": skill_id},
        ip_address=request.client.host if request.client else None,
    )
    await invalidate_agent_cache()
    return {"ok": True, "result": result}


@router.websocket("/instances/{instance_id}/chat/{agent_id}/stream")
async def chat_ws_endpoint(websocket: WebSocket, instance_id: str, agent_id: str):
    user = await get_current_user_ws(websocket)
    if not user:
        await websocket.close(code=WS_CODE_AUTH_REQUIRED, reason="未登录")
        return
    await websocket.accept()

    try:
        instance = await _get_accessible_chat_instance(instance_id, user)
    except AppError as exc:
        await websocket.close(
            code=4403 if exc.status == 403 else 4404,
            reason=str(exc.detail.get("detail") if exc.detail else exc.message)[:120],
        )
        return

    # 根据实例 agent_type 选择客户端
    agent_type = getattr(instance, "agent_type", "aiclaw") or "aiclaw"

    client = AIClawClient(instance_id)
    active_run_ids: set[str] = set()
    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")
            if msg_type == "user_message":
                if not await chat_send_rate_limiter.try_consume(user.id):
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": "AICLAW_RATE_LIMITED",
                            "message": "chat 调用过于频繁，请稍后再试",
                        }
                    )
                    continue
                try:
                    from app.database import async_session_factory

                    async with async_session_factory() as db:
                        model_context = await _resolve_chat_model_context(
                            db,
                            user,
                            msg.get("model_context") or msg.get("modelContext"),
                        )
                except AppError as exc:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": exc.code,
                            "message": _chat_error_message(exc),
                            "detail": exc.detail or {},
                        }
                    )
                    continue
                if model_context.get("model_deployment_id"):
                    try:
                        async for chunk in _chat_model_inference_events(
                            user,
                            message=str(msg.get("content", "") or ""),
                            model_context=model_context,
                        ):
                            await websocket.send_json({"type": "chat_event", "payload": chunk})
                    except AppError as exc:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": exc.code,
                                "message": _chat_error_message(exc),
                                "detail": exc.detail or {},
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": "BRIDGE_OP_ERROR",
                                "message": f"训练后模型推理失败：{str(exc)[:200]}",
                                "detail": {},
                            }
                        )
                    continue
                chat_message = _chat_message_with_model_context(str(msg.get("content", "") or ""), model_context)

                # Hermes 实例走 HTTP 流式 API
                if agent_type == "hermes":
                    try:
                        from app.aiclaw.hermes_client import HermesClient
                        hermes = HermesClient(
                            base_url=instance.gateway_url,
                            api_key=instance.auth_token or "",
                        )
                        async for chunk in hermes.chat_stream(
                            chat_message,
                            session_id=f"{instance_id}-{agent_id}",
                        ):
                            await websocket.send_json({"type": "chat_event", "payload": chunk})
                    except Exception as exc:
                        await websocket.send_json({"type": "error", "code": "HERMES_ERROR", "message": str(exc)[:300]})
                    continue

                # AIClaw 实例走 bridge WebSocket
                try:
                    async for chunk in client.chat_send(
                        agent_id=agent_id,
                        message=chat_message,
                        attachments=msg.get("attachments", []),
                        model_context=model_context,
                    ):
                        run_id = chunk.get("runId")
                        if run_id:
                            active_run_ids.add(run_id)
                        await websocket.send_json({"type": "chat_event", "payload": chunk})
                        if chunk.get("state") in {"final", "aborted", "error", "queue_overflow_lost"}:
                            if run_id:
                                active_run_ids.discard(run_id)
                except AppError as exc:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": exc.code,
                            "message": _chat_error_message(exc),
                            "detail": exc.detail or {},
                        }
                    )
            elif msg_type == "abort" and msg.get("runId"):
                run_id = msg["runId"]
                try:
                    await client.chat_abort(agent_id=agent_id, run_id=run_id)
                except AppError:
                    pass
                active_run_ids.discard(run_id)
    except WebSocketDisconnect:
        # 客户端主动断线 → 把所有未结束的 run 触发 chat.abort
        for run_id in list(active_run_ids):
            try:
                await client.chat_abort(agent_id=agent_id, run_id=run_id)
            except Exception as abort_err:
                logger.debug("chat_abort 失败 agent={} run={}: {}", agent_id, run_id, abort_err)
        return
