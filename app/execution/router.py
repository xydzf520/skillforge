"""执行API路由"""

import base64
import hashlib
import hmac
import json
from datetime import datetime
from time import time
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Query, Request
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.audit import audit
from app.common.cache import cache_get, cache_set, cache_delete_pattern
from app.common.contract_schema import normalize_data_proofs
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, parse_bjt_datetime
from app.config import settings
from app.database import get_db
from app.execution.execution_service import (
    _notify_link_decline_operator_success,
    _capture_execution_artifact_refs,
    attach_model_context_to_output,
    attach_execution_metadata,
    issue_run_token,
    latest_decision_model_context,
    model_context_from_output,
    normalize_run_mode,
    verify_run_token,
)
from app.execution.execution_service import execution_service
from app.execution.artifact_service import persist_execution_artifact
from app.execution.sync_service import _latest_skill_git_commit_full
from app.execution.models import ExecutionRun, ExecutionStep, DecisionLog, OpenClawInstance
from app.skills.core.models import Skill
from app.todos.models import DecisionRequest

router = APIRouter()
NODE_SUBMIT_TOKEN_AUDIENCE = "node_schedule_submit"
NODE_SUBMIT_TOKEN_TTL_SECONDS = 14 * 24 * 3600


def _can_read_all_execution_runs(current_user: User) -> bool:
    if bool(getattr(current_user, "can_view_all", False)):
        return True
    role = (getattr(current_user, "role", "") or "").lower()
    return role in {"system_admin", "admin", "ai_engineer"}


def _execution_department_scope(current_user: User) -> str | None:
    if _can_read_all_execution_runs(current_user):
        return None
    return current_user.department


async def _check_run_department(run_id: str, current_user: User) -> None:
    """通过 run_id 查找关联 Skill，检查部门隔离"""
    department = _execution_department_scope(current_user)
    if department is None:
        return
    from app.execution.models import DecisionLog
    from app.skills.core.models import Skill
    from app.database import async_session_factory
    async with async_session_factory() as session:
        result = await session.execute(
            select(DecisionLog.skill_id).where(DecisionLog.run_id == run_id).limit(1)
        )
        row = result.first()
        if row:
            skill_result = await session.execute(
                select(Skill.department).where(Skill.id == row[0])
            )
            dept = skill_result.scalar()
            if dept and dept != department:
                raise AppError("AUTH_DEPARTMENT_DENIED", 403)


def _validate_hook_url(url: str) -> None:
    """验证reload hook URL安全，防止SSRF攻击"""
    import ipaddress
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname or ""
    # 只允许 http/https
    if parsed.scheme not in ("http", "https"):
        raise AppError("PARAM_INVALID", 400, {"detail": "reload_hook_url 只支持 http/https"})
    # 阻止内网/保留地址
    if host in ("localhost", "0.0.0.0", "::1"):
        raise AppError("PARAM_INVALID", 400, {"detail": "reload_hook_url 不允许内网地址"})
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise AppError("PARAM_INVALID", 400, {"detail": "reload_hook_url 不允许内网地址"})
    except ValueError:
        # 域名（非IP）：阻止常见内网域名
        if host.endswith(".local") or host.endswith(".internal"):
            raise AppError("PARAM_INVALID", 400, {"detail": "reload_hook_url 不允许内网地址"})


class RunSkillRequest(BaseModel):
    skill_id: str
    params: dict | None = None
    sandbox: bool = False
    run_mode: str | None = Field(default=None, max_length=30)
    parent_run_id: str | None = Field(default=None, max_length=50)
    data_provenance: list[dict] | None = None
    sample_used: bool | None = None


@router.post("/run")
async def run_skill(
    body: RunSkillRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """手动触发Skill执行"""
    result = await execution_service.execute_skill(
        skill_id=body.skill_id,
        params=body.params,
        sandbox=body.sandbox,
        triggered_by=f"manual:{current_user.id}",
        run_mode=body.run_mode,
        parent_run_id=body.parent_run_id,
        data_proofs=body.data_provenance,
        sample_used=body.sample_used,
    )
    try:
        await cache_delete_pattern("execution:*")
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    return result


@router.get("/runs/stats")
async def runs_stats(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """执行记录状态分布（部门隔离）。为 ExecutionList 顶部统计卡提供全局数字。"""
    from sqlalchemy import func

    department = _execution_department_scope(current_user)
    cache_key = f"execution:runs:stats:{department}"
    try:
        cached_val = await cache_get(cache_key)
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    q = select(ExecutionRun.status, func.count(ExecutionRun.id)).group_by(ExecutionRun.status)
    if department:
        q = q.join(Skill, ExecutionRun.skill_id == Skill.id).where(Skill.department == department)
    result = await db.execute(q)
    counts: dict[str, int] = {"total": 0, "success": 0, "failed": 0, "running": 0, "pending": 0}
    for status_val, n in result.all():
        k = status_val or "unknown"
        counts["total"] += int(n)
        if k in ("success", "completed"):
            counts["success"] += int(n)
        elif k in counts:
            counts[k] += int(n)
    try:
        await cache_set(cache_key, counts, ttl=settings.CACHE_TTL_EXECUTION)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    return counts


@router.get("/runs")
async def list_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    current_user: User = Depends(require_state_active),
):
    """查询执行记录（部门隔离，统一分页格式）"""
    department = _execution_department_scope(current_user)
    cache_key = f"execution:runs:{status}:{department}:{page}:{page_size}"
    try:
        cached_val = await cache_get(cache_key)
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    # count + list 在同一 session 内, 避免两次查询之间写入造成 total/items 不一致
    result = await execution_service.list_execution_runs_paged(
        page=page,
        page_size=page_size,
        department=department,
        status=status,
    )
    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_EXECUTION)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    return result


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    current_user: User = Depends(require_state_active),
):
    """获取单次执行详情（部门隔离）"""
    await _check_run_department(run_id, current_user)
    cache_key = f"execution:run:{run_id}"
    try:
        cached_val = await cache_get(cache_key)
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    result = await execution_service.get_run_detail(run_id)
    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_EXECUTION)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    return result


@router.get("/runs/{run_id}/steps")
async def get_run_steps(
    run_id: str,
    current_user: User = Depends(require_state_active),
):
    """获取执行步骤列表（部门隔离）"""
    await _check_run_department(run_id, current_user)
    cache_key = f"execution:steps:{run_id}"
    try:
        cached_val = await cache_get(cache_key)
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    result = await execution_service.get_run_steps(run_id)
    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_EXECUTION)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    return result


@router.get("/runs/{run_id}/decisions")
async def get_decisions(
    run_id: str,
    current_user: User = Depends(require_state_active),
):
    """决策追溯（部门隔离）"""
    await _check_run_department(run_id, current_user)
    cache_key = f"execution:decisions:{run_id}"
    try:
        cached_val = await cache_get(cache_key)
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    result = await execution_service.get_run_decisions(run_id)
    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_GENERATED_DATA)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    return result


class DecisionActionRequest(BaseModel):
    action: str = Field(..., pattern="^(confirm|reject|help)$")
    reason: str = Field("", max_length=2000)
    note: str = Field("", max_length=2000)


@router.post("/runs/{run_id}/decisions/{decision_id}/action")
async def decision_action(
    run_id: str,
    decision_id: int,
    body: DecisionActionRequest,
    current_user: User = Depends(require_state_active),
):
    """L1决策确认/拒绝/求助（平台内操作）"""
    from sqlalchemy import update
    from app.execution.models import DecisionLog
    action = body.action
    reason = body.reason.strip()
    note = body.note.strip()
    action_map = {
        "confirm": "completed",
        "reject": "rejected",
        "help": "help_requested",
    }
    mapped = action_map.get(action)
    if not mapped:
        raise AppError("PARAM_INVALID", 400)
    if action == "help" and not reason:
        raise AppError("PARAM_INVALID", 400, {"detail": "求助原因不能为空"})

    await _check_run_department(run_id, current_user)

    from sqlalchemy import or_
    from app.database import async_session_factory
    update_values = {"user_action": mapped, "approver": current_user.id}
    if action == "help":
        feedback_lines = [f"求助原因: {reason}"]
        if note:
            feedback_lines.append(f"补充说明: {note}")
        update_values["user_feedback"] = "\n".join(feedback_lines)

    async with async_session_factory() as session:
        result = await session.execute(
            update(DecisionLog)
            .where(DecisionLog.id == decision_id)
            .where(DecisionLog.run_id == run_id)
            .where(or_(
                DecisionLog.user_action.is_(None),
                DecisionLog.user_action == "pending",
            ))
            .values(**update_values)
        )
        if result.rowcount == 0:
            raise AppError("DECISION_NOT_FOUND_OR_ALREADY_DECIDED", 404)
        try:
            decision = await session.get(DecisionLog, decision_id)
            if decision:
                from app.learning.service import capture_decision_log

                await capture_decision_log(session, decision, user_id=current_user.id)
        except Exception as e:  # noqa: BLE001
            logger.debug("智能闭环捕获决策反馈失败 run={} decision={} err={}", run_id, decision_id, e)
        await session.commit()

    from app.common.audit import audit
    await audit.log(current_user.id, f"decision.{action}", "execution", run_id)
    try:
        await cache_delete_pattern(f"execution:decisions:{run_id}")
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    return {
        "status": "ok",
        "action": mapped,
        "reason": reason or None,
        "note": note or None,
    }


@router.get("/compare")
async def compare_runs(
    run1: str = Query(...),
    run2: str = Query(...),
    current_user: User = Depends(require_state_active),
):
    """对比两次执行记录（部门隔离）"""
    await _check_run_department(run1, current_user)
    await _check_run_department(run2, current_user)
    return await execution_service.compare_runs(run1, run2)


# ===== OpenClaw 实例管理 =====

from sqlalchemy import select
from app.common.time_utils import now_bjt


class OpenClawInstanceRequest(BaseModel):
    id: str
    name: str
    department: str | None = None
    gateway_url: str
    reload_hook_url: str
    reload_token: str
    auth_token: str | None = None
    network_zone: str = "internal"
    is_active: bool = True
    is_platform_default: bool = False
    agent_purpose: str = "skill_runtime"


class ExecutionWorkerRequest(BaseModel):
    id: str
    name: str
    queue_name: str | None = None
    capacity: int = 1
    active_runs: int = 0
    metadata: dict | None = None


class ExecutionWorkerHeartbeatRequest(BaseModel):
    active_runs: int | None = None
    status: str | None = None
    metadata: dict | None = None


@router.get("/openclaw/instances")
async def list_openclaw_instances(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """列出所有 OpenClaw 实例"""
    result = await db.execute(select(OpenClawInstance).order_by(OpenClawInstance.name))
    instances = result.scalars().all()
    return [
        {
            "id": i.id, "name": i.name, "department": i.department,
            "gateway_url": i.gateway_url, "reload_hook_url": i.reload_hook_url,
            "network_zone": i.network_zone, "is_active": i.is_active,
            "is_platform_default": bool(getattr(i, "is_platform_default", False)),
            "agent_purpose": getattr(i, "agent_purpose", "skill_runtime") or "skill_runtime",
            "last_heartbeat": isoformat_bjt(i.last_heartbeat),
            "last_sync_at": isoformat_bjt(i.last_sync_at),
            "last_sync_ok": i.last_sync_ok,
            "reload_token": "******",
            "auth_token": "******" if i.auth_token else None,
        }
        for i in instances
    ]


@router.post("/openclaw/instances")
async def create_openclaw_instance(
    body: OpenClawInstanceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """创建 OpenClaw 实例"""
    _validate_hook_url(body.reload_hook_url)
    instance = OpenClawInstance(
        id=body.id, name=body.name, department=body.department,
        gateway_url=body.gateway_url, reload_hook_url=body.reload_hook_url,
        reload_token=body.reload_token, auth_token=body.auth_token,
        network_zone=body.network_zone,
        is_active=body.is_active,
        is_platform_default=body.is_platform_default,
        agent_purpose=body.agent_purpose or "skill_runtime",
    )
    db.add(instance)
    await db.commit()
    return {"message": "创建成功", "id": body.id, "reload_token": "******"}


@router.put("/openclaw/instances/{instance_id}")
async def update_openclaw_instance(
    instance_id: str,
    body: OpenClawInstanceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """更新 OpenClaw 实例"""
    _validate_hook_url(body.reload_hook_url)
    result = await db.execute(select(OpenClawInstance).where(OpenClawInstance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise AppError("NOT_FOUND", 404)
    instance.name = body.name
    instance.department = body.department
    instance.gateway_url = body.gateway_url
    instance.reload_hook_url = body.reload_hook_url
    instance.reload_token = body.reload_token
    instance.auth_token = body.auth_token
    instance.network_zone = body.network_zone
    instance.is_active = body.is_active
    instance.is_platform_default = body.is_platform_default
    instance.agent_purpose = body.agent_purpose or "skill_runtime"
    await db.commit()
    return {"message": "更新成功", "reload_token": "******"}


@router.delete("/openclaw/instances/{instance_id}")
async def delete_openclaw_instance(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """删除 OpenClaw 实例"""
    result = await db.execute(select(OpenClawInstance).where(OpenClawInstance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise AppError("NOT_FOUND", 404)
    await db.delete(instance)
    await db.commit()
    return {"message": "删除成功"}


@router.post("/openclaw/instances/{instance_id}/test")
async def test_openclaw_instance(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """测试 OpenClaw 实例连接"""
    from app.execution.openclaw_client import OpenClawClient

    result = await db.execute(select(OpenClawInstance).where(OpenClawInstance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise AppError("NOT_FOUND", 404)

    client = OpenClawClient(gateway_url=instance.gateway_url, auth_token=instance.auth_token or "")
    status = await client.get_status()
    return {"instance_id": instance_id, "status": status}


@router.get("/workers")
async def list_workers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    from app.execution.worker_service import list_workers as _list_workers
    return await _list_workers(db)


@router.post("/workers/register")
async def register_worker(
    body: ExecutionWorkerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    from app.execution.worker_service import register_worker as _register_worker
    return await _register_worker(
        db,
        worker_id=body.id,
        name=body.name,
        queue_name=body.queue_name,
        capacity=body.capacity,
        active_runs=body.active_runs,
        metadata=body.metadata,
    )


@router.post("/workers/{worker_id}/heartbeat")
async def heartbeat_worker(
    worker_id: str,
    body: ExecutionWorkerHeartbeatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    from app.execution.worker_service import heartbeat_worker as _heartbeat_worker
    return await _heartbeat_worker(
        db,
        worker_id=worker_id,
        active_runs=body.active_runs,
        status=body.status,
        metadata=body.metadata,
    )


# ═══════════════════════════════════════════════
# 远程结果提交（OpenClaw 上的 Skill 脚本回推执行结果）
# ═══════════════════════════════════════════════

class SubmitResultRequest(BaseModel):
    skill_id: str
    output: dict               # Skill 完整输出（含 todos 字段）
    params: dict = Field(default_factory=dict)  # 执行参数快照
    triggered_by: str = "remote"
    idempotency_key: str | None = Field(default=None, max_length=200)
    run_id: str | None = Field(default=None, max_length=50)
    instance_id: str | None = Field(default=None, max_length=100)
    remote_run_id: str | None = Field(default=None, max_length=200)
    skill_version: str | None = Field(default=None, max_length=100)
    agent_type: str | None = Field(default=None, max_length=50)
    signature: str | None = Field(default=None, max_length=1000)
    run_mode: str | None = Field(default=None, max_length=30)
    parent_run_id: str | None = Field(default=None, max_length=50)
    batch_id: str | None = Field(default=None, max_length=50)
    data_provenance: list[dict] = Field(default_factory=list)
    sample_used: bool | None = None
    status: str | None = Field(default=None, max_length=20)
    started_at: str | None = Field(default=None, max_length=50)
    completed_at: str | None = Field(default=None, max_length=50)


def _is_local_request(request) -> bool:
    """本地调用校验 — 禁止带转发头绕过。"""
    from app.common.internal_request import is_internal_request
    return is_internal_request(request)


def _runtime_submit_run_token(*, skill: Skill, run_id: str, instance_id: str | None) -> str:
    return issue_run_token(
        skill_id=skill.id,
        run_id=run_id,
        instance_id=instance_id,
        skill_git_commit_full=_latest_skill_git_commit_full(skill.id) or (
            str(getattr(skill, "git_commit", "") or "").strip() or None
        ),
        department=skill.department,
        skill_department=skill.department,
    )


def _b64url_encode_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode_text(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def issue_node_submit_token(instance_id: str | None = None) -> str:
    now = int(time())
    claims = {
        "aud": NODE_SUBMIT_TOKEN_AUDIENCE,
        "instance_id": instance_id,
        "iat": now,
        "exp": now + NODE_SUBMIT_TOKEN_TTL_SECONDS,
    }
    payload = _b64url_encode_bytes(
        json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"sfnode.{payload}.{_b64url_encode_bytes(signature)}"


def verify_node_submit_token(token: str | None, *, instance_id: str | None = None) -> dict:
    if not token:
        raise AppError("NODE_SUBMIT_TOKEN_INVALID", 401)
    try:
        prefix, payload, signature = str(token).split(".", 2)
        if prefix != "sfnode":
            raise ValueError("bad prefix")
        expected = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        actual = _b64url_decode_text(signature)
        if not hmac.compare_digest(actual, expected):
            raise ValueError("signature mismatch")
        claims = json.loads(_b64url_decode_text(payload).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise AppError("NODE_SUBMIT_TOKEN_INVALID", 401) from exc
    if claims.get("aud") != NODE_SUBMIT_TOKEN_AUDIENCE:
        raise AppError("NODE_SUBMIT_TOKEN_INVALID", 401, {"reason": "aud_mismatch"})
    if int(claims.get("exp") or 0) < int(time()):
        raise AppError("NODE_SUBMIT_TOKEN_INVALID", 401, {"reason": "expired"})
    claim_instance_id = str(claims.get("instance_id") or "")
    if instance_id and claim_instance_id and claim_instance_id != str(instance_id):
        raise AppError("NODE_SUBMIT_TOKEN_INVALID", 401, {"reason": "instance_id_mismatch"})
    return claims


def _clean_submit_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _submit_output_summary(output: dict | None) -> str | None:
    if not isinstance(output, dict):
        return None
    for key in ("summary", "recommendation", "decision", "conclusion"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    reports = output.get("reports")
    if isinstance(reports, list) and reports:
        first = reports[0]
        if isinstance(first, dict):
            for key in ("summary", "title"):
                value = first.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:500]
    return None


def _submit_failure_info(output: dict | None) -> dict | None:
    """识别远端运行失败结果，避免把 traceback/timeout 当成功审批。"""
    if not isinstance(output, dict):
        return None
    meta = _extract_execution_meta(output)
    error = output.get("error")
    code_value = output.get("code")
    status_value = output.get("status") or meta.get("status")
    meta_error = meta.get("error") or meta.get("error_message")
    failure_statuses = {"failed", "failure", "error", "timeout", "timed_out"}
    failure_codes = {
        "execution_failed",
        "runtime_execution_failed",
        "script_execution_failed",
        "script_timeout",
        "timeout",
    }
    code_text = str(code_value).strip() if code_value is not None else ""
    status_text = str(status_value).strip().lower() if status_value is not None else ""
    has_failure_signal = bool(error or meta_error)
    has_failure_signal = has_failure_signal or status_text in failure_statuses
    has_failure_signal = has_failure_signal or code_text.lower() in failure_codes
    if not has_failure_signal:
        return None
    code = str(error or code_value or status_value or "execution_failed").strip() or "execution_failed"
    message_value = (
        output.get("message")
        or output.get("detail")
        or output.get("error_description")
        or meta_error
        or code
    )
    message = str(message_value).strip() or code
    failure_type = "timeout" if "timeout" in f"{code} {message}".lower() else "failed"
    return {
        "code": code[:100],
        "message": message[:2000],
        "type": failure_type,
    }


def _submit_source_info(body: SubmitResultRequest) -> dict:
    return {
        "source_instance_id": _clean_submit_value(body.instance_id),
        "remote_run_id": _clean_submit_value(body.remote_run_id),
        "skill_version": _clean_submit_value(body.skill_version),
        "agent_type": _clean_submit_value(body.agent_type),
    }


def _source_info_from_meta(meta: dict) -> dict:
    return {
        "source_instance_id": meta.get("source_instance_id"),
        "remote_run_id": meta.get("remote_run_id"),
        "skill_version": meta.get("skill_version"),
        "agent_type": meta.get("agent_type"),
    }


def _runtime_info_from_meta(meta: dict) -> dict:
    keys = (
        "execution_backend",
        "requested_backend",
        "fallback_from",
        "instance_id",
        "remote_run_id",
        "agent_runtime",
        "script",
        "duration_ms",
        "script_duration_ms",
        "returncode",
    )
    return {key: meta.get(key) for key in keys if meta.get(key) is not None}


def _merge_runtime_metadata(current: dict | None, update: dict) -> dict:
    merged = dict(current or {})
    current_runtime = merged.get("runtime") if isinstance(merged.get("runtime"), dict) else {}
    update_runtime = update.get("runtime") if isinstance(update.get("runtime"), dict) else {}
    if current_runtime or update_runtime:
        merged["runtime"] = {**current_runtime, **update_runtime}
    for key, value in update.items():
        if key != "runtime":
            merged[key] = value
    return merged


def _with_submit_metadata(
    output: dict,
    *,
    idempotency_key: str | None,
    run_id: str,
    skill_id: str,
    run_mode: str,
    parent_run_id: str | None,
    batch_id: str | None,
    data_proofs: list[dict],
    sample_used: bool,
    source_info: dict,
    signature: str | None,
) -> dict:
    result = attach_execution_metadata(
        output,
        run_id=run_id,
        skill_id=skill_id,
        run_mode=run_mode,
        parent_run_id=parent_run_id,
        batch_id=batch_id,
        data_proofs=data_proofs,
        sample_used=sample_used,
    )
    meta = result.get("_skillforge_meta")
    meta = dict(meta) if isinstance(meta, dict) else {}
    meta["submit_result_run_id"] = run_id
    for key, value in source_info.items():
        if value:
            meta[key] = value
    if idempotency_key:
        meta["idempotency_key"] = idempotency_key
    if signature:
        meta["signature"] = signature
    result["_skillforge_meta"] = meta
    return result


def _submit_data_provenance(body: SubmitResultRequest) -> list[dict]:
    if body.data_provenance:
        return body.data_provenance
    if isinstance(body.output, dict):
        raw = body.output.get("data_provenance")
        if raw:
            return raw
        meta = body.output.get("_skillforge_meta")
        if isinstance(meta, dict):
            raw = meta.get("data_provenance") or meta.get("data_proofs")
            if raw:
                return raw
    return []


def _extract_execution_meta(output: dict | None) -> dict:
    meta = output.get("_skillforge_meta") if isinstance(output, dict) else None
    return dict(meta) if isinstance(meta, dict) else {}


def _parse_submit_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_bjt_datetime(value)
    except Exception:
        return None


def _submit_status(body: SubmitResultRequest, output: dict | None) -> str | None:
    candidates = [body.status, output.get("status") if isinstance(output, dict) else None]
    meta = _extract_execution_meta(output)
    candidates.append(meta.get("status"))
    for candidate in candidates:
        status = str(candidate or "").strip().lower()
        if status in {"running", "completed", "failed", "timeout", "blocked"}:
            return status
    return None


async def _find_existing_submit_result(
    db: AsyncSession,
    *,
    skill_id: str,
    instance_id: str | None,
    remote_run_id: str | None,
    run_id: str | None,
    idempotency_key: str | None,
) -> DecisionLog | None:
    if instance_id and remote_run_id:
        result = await db.execute(
            select(DecisionLog)
            .where(DecisionLog.skill_id == skill_id)
            .where(text("output_result -> '_skillforge_meta' ->> 'source_instance_id' = :instance_id"))
            .where(text("output_result -> '_skillforge_meta' ->> 'remote_run_id' = :remote_run_id"))
            .params(instance_id=instance_id, remote_run_id=remote_run_id)
            .order_by(DecisionLog.id.asc())
            .limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

    if idempotency_key:
        result = await db.execute(
            select(DecisionLog)
            .where(DecisionLog.skill_id == skill_id)
            .where(text("output_result -> '_skillforge_meta' ->> 'idempotency_key' = :idempotency_key"))
            .params(idempotency_key=idempotency_key)
            .order_by(DecisionLog.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    if run_id:
        result = await db.execute(
            select(DecisionLog)
            .where(DecisionLog.skill_id == skill_id)
            .where(DecisionLog.run_id == run_id)
            .order_by(DecisionLog.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    return None


async def _find_existing_remote_run(
    db: AsyncSession,
    *,
    skill_id: str,
    instance_id: str | None,
    remote_run_id: str | None,
    run_id: str | None,
    idempotency_key: str | None,
) -> ExecutionRun | None:
    if run_id:
        existing = await db.get(ExecutionRun, run_id)
        if existing and existing.skill_id == skill_id:
            return existing

    if instance_id and remote_run_id:
        result = await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.skill_id == skill_id)
            .where(ExecutionRun.source_instance_id == instance_id)
            .where(text("metadata_json ->> 'remote_run_id' = :remote_run_id"))
            .params(remote_run_id=remote_run_id)
            .order_by(ExecutionRun.started_at.asc())
            .limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

    if idempotency_key:
        result = await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.skill_id == skill_id)
            .where(text("metadata_json ->> 'idempotency_key' = :idempotency_key"))
            .params(idempotency_key=idempotency_key)
            .order_by(ExecutionRun.started_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    return None


async def _submit_result_response(
    db: AsyncSession,
    *,
    run_id: str,
    skill_id: str,
    output: dict | None,
    decision_log_id: int,
    idempotent: bool = False,
) -> dict:
    run = await db.get(ExecutionRun, run_id) if run_id else None
    meta = _extract_execution_meta(output)
    data_proofs = normalize_data_proofs(
        (run.metadata_json or {}).get("data_proofs") if run and isinstance(run.metadata_json, dict) else meta.get("data_proofs")
    )
    sample_used = (
        (run.metadata_json or {}).get("sample_used")
        if run and isinstance(run.metadata_json, dict) and "sample_used" in run.metadata_json
        else meta.get("sample_used")
    )
    reports = output.get("reports") if isinstance(output, dict) else None
    report_count = len(reports) if isinstance(reports, list) else 0
    todo_count = int(
        await db.scalar(
            select(func.count(DecisionRequest.id)).where(
                DecisionRequest.decision_log_id == decision_log_id
            )
        )
        or 0
    )
    run_metadata = run.metadata_json if run and isinstance(run.metadata_json, dict) else {}
    return {
        "run_id": run_id,
        "skill_id": skill_id,
        "status": getattr(run, "status", None),
        "todo_created": todo_count > 0,
        "todo_count": todo_count,
        "todo_error": None,
        "report_count": report_count,
        "decision_log_id": decision_log_id,
        "idempotent": idempotent,
        "run_mode": getattr(run, "run_mode", None) or meta.get("run_mode"),
        "data_proofs": data_proofs,
        "sample_used": bool(sample_used),
        "source": _source_info_from_meta(meta),
        "runtime": _runtime_info_from_meta(meta),
        "model_context": run_metadata.get("model_context"),
        "failure": run_metadata.get("failure"),
    }


async def _persist_submit_raw_artifacts(
    db: AsyncSession,
    *,
    run_id: str,
    skill_id: str,
    params: dict,
    output: dict,
    decision_log_id: int,
) -> list[dict]:
    refs: list[dict] = []
    input_ref = await persist_execution_artifact(
        db,
        run_id=run_id,
        skill_id=skill_id,
        kind="raw-input",
        payload=params,
        decision_log_id=decision_log_id,
    )
    if input_ref:
        refs.append(input_ref)
    output_ref = await persist_execution_artifact(
        db,
        run_id=run_id,
        skill_id=skill_id,
        kind="raw-output",
        payload=output,
        decision_log_id=decision_log_id,
    )
    if output_ref:
        refs.append(output_ref)
    if not refs:
        return refs
    run = await db.get(ExecutionRun, run_id)
    if run is not None:
        metadata = dict(run.metadata_json or {})
        current = metadata.get("artifacts")
        existing = current if isinstance(current, list) else []
        by_key = {
            (str(item.get("kind")), str(item.get("sha256"))): item
            for item in existing
            if isinstance(item, dict)
        }
        for ref in refs:
            by_key[(str(ref.get("kind")), str(ref.get("sha256")))] = ref
        metadata["artifacts"] = list(by_key.values())
        run.metadata_json = metadata
    await _capture_execution_artifact_refs(
        db,
        refs,
        skill_id=skill_id,
        run_id=run_id,
    )
    return refs


async def _enqueue_link_decline_operator_for_submit_result(
    *,
    skill_id: str,
    run_id: str,
    decision_log_id: int | None,
    output: dict | None,
    failure_info: dict | None,
    created_by: str,
) -> dict:
    if failure_info:
        return {"status": "skipped", "reason": "collector_failed"}
    return await execution_service.enqueue_link_decline_operator_trigger_if_needed(
        collector_skill_id=skill_id,
        collector_run_id=run_id,
        collector_decision_log_id=decision_log_id,
        collector_output=output,
        created_by=created_by,
    )


@router.post("/submit-result")
async def submit_result(
    body: SubmitResultRequest,
    request: Request,
    token: str = Query("", description="BROWSER_REMOTE_TOKEN"),
    x_run_token: str = Header("", alias="X-Run-Token"),
    db: AsyncSession = Depends(get_db),
):
    """接收 Skill 执行结果并创建平台待办/报告。

    本地调用免 token，远程调用需要 BROWSER_REMOTE_TOKEN。
    这里不直接推钉钉；只有平台待办审批通过后的 dispatch 子任务才会推给执行人。
    用 BROWSER_REMOTE_TOKEN 认证（和浏览器远程采集共用 token）。
    """
    run_token_claims = None
    cleaned_instance_id = _clean_submit_value(body.instance_id)
    if x_run_token:
        run_token_claims = verify_run_token(
            x_run_token,
            skill_id=body.skill_id,
            run_id=body.run_id,
            instance_id=cleaned_instance_id,
        )
        if not body.run_id and run_token_claims.get("run_id"):
            body.run_id = str(run_token_claims["run_id"])
        if not body.instance_id and run_token_claims.get("instance_id"):
            body.instance_id = str(run_token_claims["instance_id"])
            cleaned_instance_id = _clean_submit_value(body.instance_id)
    elif not _is_local_request(request):
        if str(token or "").startswith("sfnode."):
            verify_node_submit_token(token, instance_id=cleaned_instance_id)
        else:
            expected = settings.BROWSER_REMOTE_TOKEN
            if not expected:
                raise AppError("REMOTE_NOT_ENABLED", 403, {"detail": "未配置 BROWSER_REMOTE_TOKEN"})
            if not hmac.compare_digest(token, expected):
                raise AppError("AUTH_FAILED", 401)

    return await persist_submitted_result(db, body, request=request, actor="svc_execution")


async def persist_submitted_result(
    db: AsyncSession,
    body: SubmitResultRequest,
    *,
    request: Request | None = None,
    actor: str = "svc_execution",
) -> dict:
    # 查 Skill 元信息
    cleaned_instance_id = _clean_submit_value(body.instance_id)
    result = await db.execute(select(Skill).where(Skill.id == body.skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    cleaned_remote_run_id = _clean_submit_value(body.remote_run_id)
    existing = await _find_existing_submit_result(
        db,
        skill_id=body.skill_id,
        instance_id=cleaned_instance_id,
        remote_run_id=cleaned_remote_run_id,
        run_id=body.run_id,
        idempotency_key=body.idempotency_key,
    )
    if existing:
        existing_failure = _submit_failure_info(existing.output_result)
        if not existing_failure:
            await _persist_submit_raw_artifacts(
                db,
                run_id=existing.run_id or body.run_id or "",
                skill_id=body.skill_id,
                params=existing.input_snapshot or body.params,
                output=existing.output_result or body.output,
                decision_log_id=existing.id,
            )
        await _enqueue_link_decline_operator_for_submit_result(
            skill_id=body.skill_id,
            run_id=existing.run_id or body.run_id or "",
            decision_log_id=existing.id,
            output=existing.output_result,
            failure_info=existing_failure,
            created_by="execution.submit_result.idempotent",
        )
        if not existing_failure and body.skill_id == execution_service.OPERATOR_SKILL_ID:
            try:
                todo_count = int(
                    await db.scalar(
                        select(func.count(DecisionRequest.id)).where(DecisionRequest.decision_log_id == existing.id)
                    )
                    or 0
                )
                await _notify_link_decline_operator_success(
                    run_id=existing.run_id or body.run_id or "",
                    decision_log_id=existing.id,
                    output=existing.output_result,
                    todo_count=todo_count,
                )
            except Exception as notify_err:  # noqa: BLE001
                logger.warning(
                    "远程提交结果: Operator 幂等成功通知入队失败 skill={} run={} error={}",
                    body.skill_id,
                    existing.run_id or body.run_id,
                    notify_err,
                )
        return await _submit_result_response(
            db,
            run_id=existing.run_id or body.run_id or "",
            skill_id=body.skill_id,
            output=existing.output_result,
            decision_log_id=existing.id,
            idempotent=True,
        )

    skill_meta = {
        "id": skill.id, "name": skill.name,
        "approval_level": skill.approval_level,
        "target_users": skill.target_users or [],
        "owner": skill.owner,
    }

    existing_run = await _find_existing_remote_run(
        db,
        skill_id=body.skill_id,
        instance_id=cleaned_instance_id,
        remote_run_id=cleaned_remote_run_id,
        run_id=body.run_id,
        idempotency_key=body.idempotency_key,
    )

    # 写 ExecutionRun + DecisionLog
    run_id = existing_run.id if existing_run else (body.run_id or f"rm-{uuid4().hex[:8]}")
    run_mode = normalize_run_mode(body.run_mode, sandbox=bool(body.sample_used), triggered_by=body.triggered_by)
    data_proofs = normalize_data_proofs(_submit_data_provenance(body))
    sample_used = bool(body.sample_used) or any(item.get("is_sample") for item in data_proofs)
    source_info = _submit_source_info(body)
    source_metadata = {k: v for k, v in source_info.items() if v}
    if body.idempotency_key:
        source_metadata["idempotency_key"] = body.idempotency_key
    if body.signature:
        source_metadata["signature"] = body.signature
    output = _with_submit_metadata(
        body.output,
        idempotency_key=body.idempotency_key,
        run_id=run_id,
        skill_id=body.skill_id,
        run_mode=run_mode,
        parent_run_id=body.parent_run_id,
        batch_id=body.batch_id,
        data_proofs=data_proofs,
        sample_used=sample_used,
        source_info=source_info,
        signature=body.signature,
    )
    runtime_metadata = _runtime_info_from_meta(_extract_execution_meta(output))
    failure_info = _submit_failure_info(output)
    reported_status = _submit_status(body, output)
    run_status = "failed" if failure_info else ("running" if reported_status == "running" else "completed")
    run_summary = _submit_output_summary(output)
    if failure_info and not run_summary:
        run_summary = failure_info["message"][:500]
    submitted_at = now_bjt()
    started_at = _parse_submit_datetime(body.started_at) or (existing_run.started_at if existing_run else None) or submitted_at
    completed_at = None if run_status == "running" else (_parse_submit_datetime(body.completed_at) or submitted_at)
    metadata_json = {
        "data_proofs": data_proofs,
        "sample_used": sample_used,
        **source_metadata,
        **({"runtime": runtime_metadata} if runtime_metadata else {}),
        **({"failure": failure_info} if failure_info else {}),
    }
    model_context = model_context_from_output(output)
    if not model_context:
        model_context = await latest_decision_model_context(
            db,
            skill_id=body.skill_id,
            run_id=run_id,
        )
    if model_context:
        metadata_json["model_context"] = model_context
        output = attach_model_context_to_output(output, model_context)
    effective_params = dict(body.params or {})
    if model_context:
        effective_params["model_context"] = model_context

    if existing_run and existing_run.status == "completed" and run_status == "running":
        return {
            "run_id": existing_run.id,
            "skill_id": body.skill_id,
            "status": existing_run.status,
            "todo_created": False,
            "todo_count": 0,
            "todo_error": None,
            "report_count": 0,
            "decision_log_id": None,
            "idempotent": True,
            "run_mode": existing_run.run_mode,
            "data_proofs": normalize_data_proofs((existing_run.metadata_json or {}).get("data_proofs")),
            "sample_used": bool((existing_run.metadata_json or {}).get("sample_used")),
            "source": source_info,
            "failure": None,
        }

    if existing_run:
        run = existing_run
        run.trigger_type = body.triggered_by
        run.run_mode = run_mode
        run.parent_run_id = body.parent_run_id
        run.started_at = started_at
        run.completed_at = completed_at
        run.status = run_status
        run.batch_id = body.batch_id
        run.total_steps = 1
        run.completed_steps = 0 if run_status == "running" or failure_info else 1
        run.summary = run_summary
        run.source_instance_id = cleaned_instance_id
        run.metadata_json = _merge_runtime_metadata(run.metadata_json, metadata_json)
    else:
        run = ExecutionRun(
            id=run_id, skill_id=body.skill_id, trigger_type=body.triggered_by,
            run_mode=run_mode, parent_run_id=body.parent_run_id,
            started_at=started_at, completed_at=completed_at,
            status=run_status, batch_id=body.batch_id,
            total_steps=1, completed_steps=0 if run_status == "running" or failure_info else 1,
            summary=run_summary,
            source_instance_id=cleaned_instance_id,
            metadata_json=metadata_json,
        )
        db.add(run)

    step_result = await db.execute(
        select(ExecutionStep)
        .where(ExecutionStep.run_id == run_id)
        .where(ExecutionStep.step_order == 1)
        .order_by(ExecutionStep.id.asc())
        .limit(1)
    )
    step = step_result.scalar_one_or_none()
    duration_ms = None
    if run_status != "running" and completed_at is not None:
        duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))
    if step is None:
        db.add(
            ExecutionStep(
                run_id=run_id,
                skill_id=body.skill_id,
                step_order=1,
                status=run_status,
                input_data=effective_params,
                output_data=output if run_status != "running" else None,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                error_message=failure_info["message"] if failure_info else None,
            )
        )
    else:
        step.status = run_status
        step.input_data = effective_params
        step.output_data = output if run_status != "running" else step.output_data
        step.started_at = step.started_at or started_at
        step.completed_at = completed_at
        step.duration_ms = duration_ms
        step.error_message = failure_info["message"] if failure_info else None

    if run_status == "running":
        await db.commit()

        try:
            from app.tasktree.writer import writer as tasktree_writer

            if existing_run is None:
                await tasktree_writer.create_execution_node(
                    db,
                    run_id=run_id,
                    department_id=skill.department or "unknown",
                    title=skill.name or body.skill_id,
                    source_instance_id=cleaned_instance_id,
                    status="running",
                    started_at=started_at,
                )
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.warning("远程提交运行状态: 创建任务树节点失败 skill={} run={} error={}", body.skill_id, run_id, e)

        try:
            from app.tasktree.service import invalidate_tasktree

            await invalidate_tasktree(skill.department)
            await cache_delete_pattern("execution:*")
        except Exception as e:
            logger.warning("远程提交运行状态: invalidate execution/tasktree cache 失败 skill={} error={}", body.skill_id, e)

        return {
            "run_id": run_id,
            "skill_id": body.skill_id,
            "status": "running",
            "run_token": _runtime_submit_run_token(
                skill=skill,
                run_id=run_id,
                instance_id=cleaned_instance_id,
            ),
            "todo_created": False,
            "todo_count": 0,
            "todo_error": None,
            "report_count": 0,
            "decision_log_id": None,
            "idempotent": existing_run is not None,
            "run_mode": run_mode,
            "data_proofs": data_proofs,
            "sample_used": sample_used,
            "source": source_info,
            "failure": None,
        }

    decision = DecisionLog(
        run_id=run_id, skill_id=body.skill_id,
        input_snapshot=effective_params, output_result=output,
        model_id=model_context.get("model_deployment_id") if model_context else None,
        approval_level=skill.approval_level, is_sandbox=False,
        created_at=submitted_at,
    )
    db.add(decision)
    await db.flush()
    decision_log_id = decision.id
    try:
        from app.inbox.service import sync_report_cards_for_decision_log

        await sync_report_cards_for_decision_log(db, decision)
    except Exception as e:  # noqa: BLE001
        logger.warning("远程提交结果: 同步报告投影失败 skill={} run={} error={}", body.skill_id, run_id, e)
    try:
        from app.learning.service import capture_decision_log, capture_execution_run

        await capture_execution_run(db, run)
        await capture_decision_log(db, decision)
    except Exception as e:  # noqa: BLE001
        logger.debug("智能闭环捕获远程执行结果失败 skill={} run={} err={}", body.skill_id, run_id, e)
    await _persist_submit_raw_artifacts(
        db,
        run_id=run_id,
        skill_id=body.skill_id,
        params=effective_params,
        output=output,
        decision_log_id=decision_log_id,
    )
    await db.commit()

    try:
        from app.tasktree.writer import writer as tasktree_writer

        if existing_run is not None:
            node = await tasktree_writer.finish_execution_node(
                db,
                run_id=run_id,
                status=run_status,
                finished_at=completed_at,
            )
            if node is None:
                await tasktree_writer.create_execution_node(
                    db,
                    run_id=run_id,
                    department_id=skill.department or "unknown",
                    title=skill.name or body.skill_id,
                    source_instance_id=cleaned_instance_id,
                    status=run_status,
                    started_at=started_at,
                    finished_at=completed_at,
                )
        else:
            await tasktree_writer.create_execution_node(
                db,
                run_id=run_id,
                department_id=skill.department or "unknown",
                title=skill.name or body.skill_id,
                source_instance_id=cleaned_instance_id,
                status=run_status,
                started_at=started_at,
                finished_at=completed_at,
            )
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.warning("远程提交结果: 创建任务树节点失败 skill={} run={} error={}", body.skill_id, run_id, e)

    # 创建待办
    todo_created = False
    todo_count = 0
    todo_error = None
    reports = output.get("reports") if isinstance(output, dict) else None
    report_count = len(reports) if isinstance(reports, list) else 0
    try:
        if failure_info:
            logger.info(
                "远程提交结果: run={} skill={} 为失败结果，跳过待办创建",
                run_id,
                body.skill_id,
            )
        else:
            from app.todos.service import todo_service
            requests = await todo_service.create_from_execution(
                db, run_id=run_id, skill_id=body.skill_id,
                skill_meta=skill_meta,
                decision={
                    "input_snapshot": effective_params,
                    "output_result": output,
                    "decision_log_id": decision_log_id,
                },
            )
            await db.commit()
            todo_created = bool(requests)
            todo_count = len(requests or [])
    except Exception as e:
        if isinstance(e, AppError):
            detail = e.detail or {}
            todo_error = {
                "code": e.code,
                "message": e.message,
                "detail": detail,
            }
        else:
            todo_error = {
                "code": "TODO_CREATE_FAILED",
                "message": str(e),
                "detail": {},
            }
        logger.warning("远程提交结果: 创建待办失败 skill={} error={}", body.skill_id, e)

    if report_count or todo_count:
        try:
            from app.common.cache import invalidate_generated_data_cache

            await invalidate_generated_data_cache()
        except Exception as e:
            logger.warning("远程提交结果: invalidate generated data cache 失败 skill={} error={}", body.skill_id, e)

    if not failure_info and body.skill_id == execution_service.OPERATOR_SKILL_ID:
        try:
            await _notify_link_decline_operator_success(
                run_id=run_id,
                decision_log_id=decision_log_id,
                output=output,
                todo_count=todo_count,
            )
        except Exception as notify_err:  # noqa: BLE001
            logger.warning(
                "远程提交结果: Operator 成功通知入队失败 skill={} run={} error={}",
                body.skill_id,
                run_id,
                notify_err,
            )

    try:
        from app.tasktree.service import invalidate_tasktree

        await invalidate_tasktree(skill.department)
        await cache_delete_pattern("execution:*")
    except Exception as e:
        logger.warning("远程提交结果: invalidate execution/tasktree cache 失败 skill={} error={}", body.skill_id, e)

    client_ip = request.client.host if request and request.client else "internal"
    await audit.log(actor, "execution.submit_result", "skill", body.skill_id,
                    detail={
                        "run_id": run_id,
                        "remote_ip": client_ip,
                        "status": run_status,
                        "failure": failure_info,
                        "todo_created": todo_created,
                        "todo_count": todo_count,
                        "todo_error": todo_error,
                        "report_count": report_count,
                        "decision_log_id": decision_log_id,
                        "model_context": model_context,
                        "source": source_info,
                    })

    await _enqueue_link_decline_operator_for_submit_result(
        skill_id=body.skill_id,
        run_id=run_id,
        decision_log_id=decision_log_id,
        output=output,
        failure_info=failure_info,
        created_by="execution.submit_result",
    )

    return {
        "run_id": run_id,
        "skill_id": body.skill_id,
        "status": run_status,
        "todo_created": todo_created,
        "todo_count": todo_count,
        "todo_error": todo_error,
        "report_count": report_count,
        "decision_log_id": decision_log_id,
        "idempotent": False,
        "run_mode": run_mode,
        "data_proofs": data_proofs,
        "sample_used": sample_used,
        "source": source_info,
        "model_context": model_context,
        "failure": failure_info,
    }
