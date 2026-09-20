"""AIClaw Bridge WebSocket 路由。"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import ValidationError
from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory
from app.execution.bridge_placement import (
    BRIDGE_PLACEMENT_CONFLICT,
    bridge_fingerprints_match,
    has_active_platform_fingerprint_conflict,
)
from app.execution.models import OpenClawInstance

from app.common.time_utils import isoformat_bjt, now_bjt
from .bridge_registry import bridge_registry
from .script_generator import BRIDGE_VERSION
from .bridge_protocol import (
    AuthInitFrame,
    AuthResponseFrame,
    BridgeCapabilitiesFrame,
    BridgeOpResponseFrame,
    BridgeOpResponseChunkFrame,
    ForwardEventFrame,
    ForwardResponseFrame,
    PongFrame,
    ShutdownFrame,
)
from .metrics import bridge_online, bridge_reject_total
from .security import (
    clear_rotation_token,
    get_rotation_token,
    verify_enrollment_token,
    verify_signature,
)

ws_router = APIRouter()
_USED_NONCES: dict[str, datetime] = {}
_USED_NONCES_MAX = 10000
_BRIDGE_UPDATE_NUDGED_AT: dict[str, float] = {}
BRIDGE_AUTO_UPDATE_NUDGE_INTERVAL_SECONDS = 300


def _prune_used_nonces() -> None:
    now = now_bjt()
    expired = [nonce for nonce, expires_at in _USED_NONCES.items() if expires_at <= now]
    for nonce in expired:
        _USED_NONCES.pop(nonce, None)
    # 超出上限时按过期时间丢最老的一半（防恶意 bridge burst 十万次 auth 撑爆内存）
    if len(_USED_NONCES) > _USED_NONCES_MAX:
        items = sorted(_USED_NONCES.items(), key=lambda kv: kv[1])
        drop_count = len(items) // 2
        for nonce, _ in items[:drop_count]:
            _USED_NONCES.pop(nonce, None)


def _version_tuple(value: str | None) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(part) for part in parts[:4])


def _bridge_needs_update(current_version: str | None) -> bool:
    current = _version_tuple(current_version)
    latest = _version_tuple(BRIDGE_VERSION)
    return bool(current and latest and current < latest)


async def _maybe_request_bridge_auto_update(websocket: WebSocket, instance_id: str, current_version: str | None) -> None:
    if not _bridge_needs_update(current_version):
        return
    now = time.monotonic()
    last = _BRIDGE_UPDATE_NUDGED_AT.get(instance_id, 0)
    if now - last < BRIDGE_AUTO_UPDATE_NUDGE_INTERVAL_SECONDS:
        return
    _BRIDGE_UPDATE_NUDGED_AT[instance_id] = now
    request_id = f"auto-update-{secrets.token_hex(4)}"
    try:
        await websocket.send_json({
            "type": "bridge_op",
            "op": "force_update",
            "payload": {"reason": "bridge_version_outdated", "latest_version": BRIDGE_VERSION},
            "request_id": request_id,
        })
        logger.info(
            "bridge 自动更新已下发 instance={} current={} latest={} request_id={}",
            instance_id,
            current_version,
            BRIDGE_VERSION,
            request_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("bridge 自动更新下发失败 instance={}: {}", instance_id, exc)


async def _mark_connected(instance_id: str, *, pubkey: str | None = None, fingerprint: str | None = None) -> None:
    department: str | None = None
    now: datetime | None = None
    async with async_session_factory() as session:
        result = await session.execute(
            select(OpenClawInstance).where(OpenClawInstance.id == instance_id)
        )
        instance = result.scalar_one_or_none()
        if instance:
            now = now_bjt()
            instance.bridge_connected_at = now
            instance.last_heartbeat = now
            department = instance.department
            if pubkey:
                instance.device_pubkey = pubkey
            if fingerprint:
                instance.bridge_fingerprint = fingerprint
            await session.commit()

    # 心跳同步到 task_nodes_light：fire-and-forget，失败入 repair_queue
    if now is not None:
        from app.tasktree import dispatcher as tasktree_dispatcher

        # [m5] 复用共享的心跳协程工厂
        await tasktree_dispatcher.schedule_writer(
            tasktree_dispatcher.make_heartbeat_coro(
                instance_id=instance_id,
                last_heartbeat_at=now,
                department_id=department,
                record_history=True,
            ),
            source_type="heartbeat",
            source_ref=instance_id,
            operation="sync_heartbeat",
            payload={
                "instance_id": instance_id,
                "department_id": department,
                "last_heartbeat_at": isoformat_bjt(now),
                "record_history": True,
            },
        )

    # [M4] 统一走 finalize helper（bridge_online.set(1) + invalidate_tasktree）
    await _finalize_bridge_authenticated(instance_id, department)


async def _finalize_bridge_authenticated(
    instance_id: str,
    department: str | None,
) -> None:
    """[M4] 认证通过后的统一善后动作：bridge_online.set(1) + invalidate_tasktree。

    原 `_mark_connected` 做了这两件事，但 `_authenticate` 的 admin_reenroll /
    rotation_reenroll / 新设备三条分支直接 send_json(auth_ok) 返回，漏了：
    1) bridge_online 指标不跟 set(1)，监控面板认为 bridge 离线
    2) invalidate_tasktree 没清，任务树不会立刻反映实例恢复
    """
    try:
        bridge_online.labels(instance_id=instance_id).set(1)
    except Exception as e:
        logger.warning(
            "bridge_online.set(1) failed (instance={}): {}",
            instance_id,
            e,
            exc_info=True,
        )
    try:
        from app.tasktree.service import invalidate_tasktree
        await invalidate_tasktree(department)
    except Exception as e:
        logger.warning(
            "invalidate_tasktree failed on finalize_bridge_authenticated (instance={}): {}",
            instance_id,
            e,
            exc_info=True,
        )


async def _retry_pending_syncs_on_reconnect(instance_id: str) -> None:
    """bridge 重连后自动重试该实例积压的 Skill 同步任务。"""
    try:
        from app.execution.sync_service import AUTO_RETRY_MAX_ATTEMPTS, NON_RETRYABLE_SYNC_ERRORS, sync_service
        from app.database import async_session_factory
        from app.execution.models import SkillSyncJob
        from sqlalchemy import or_, select

        async with async_session_factory() as session:
            # 找该实例的最近失败 job（限制数量，避免 bridge 刚连就炸）
            jobs = (await session.execute(
                select(SkillSyncJob)
                .where(SkillSyncJob.status == "failed")
                .where(SkillSyncJob.target_instance_ids.is_not(None))
                .where(SkillSyncJob.attempt_count < AUTO_RETRY_MAX_ATTEMPTS)
                .where(or_(
                    SkillSyncJob.error.is_(None),
                    SkillSyncJob.error.notin_(tuple(NON_RETRYABLE_SYNC_ERRORS)),
                ))
                .order_by(SkillSyncJob.id.desc())
                .limit(20)
            )).scalars().all()

            # 过滤：只重试目标包含该实例、且仍有源码和重试价值的 job
            for job in jobs:
                try:
                    targets = job.target_instance_ids if isinstance(job.target_instance_ids, list) else []
                    if instance_id not in targets:
                        continue
                    skip_reason = sync_service.sync_job_auto_retry_skip_reason(job)
                    if skip_reason:
                        logger.info(
                            "跳过 bridge 重连自动重试: job={} skill={} instance={} reason={}",
                            job.id,
                            job.skill_id,
                            instance_id,
                            skip_reason,
                        )
                        continue
                    await sync_service.retry_sync_job(int(job.id), actor="system")
                except Exception:
                    pass
    except Exception:
        pass


async def _repush_schedules_on_reconnect(instance_id: str) -> None:
    """bridge 重连后自动重推定时配置，修正 ack_ok 状态。"""
    try:
        from app.execution.sync_service import sync_service
        await sync_service.push_schedules_to_targets(
            target_instance_ids=[instance_id],
        )
    except Exception:
        pass  # fire-and-forget，不阻塞 bridge 主循环


async def _mark_disconnected(instance_id: str) -> None:
    department: str | None = None
    async with async_session_factory() as session:
        result = await session.execute(
            select(OpenClawInstance).where(OpenClawInstance.id == instance_id)
        )
        instance = result.scalar_one_or_none()
        if instance:
            instance.bridge_connected_at = None
            department = instance.department
            await session.commit()
    try:
        from app.tasktree.service import invalidate_tasktree
        await invalidate_tasktree(department)
    except Exception as e:
        logger.warning(
            "invalidate_tasktree failed on mark_disconnected (instance={}): {}",
            instance_id,
            e,
            exc_info=True,
        )
    bridge_online.labels(instance_id=instance_id).set(0)


async def _lookup_instance(instance_id: str) -> OpenClawInstance | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(OpenClawInstance).where(OpenClawInstance.id == instance_id)
        )
        return result.scalar_one_or_none()


_GATEWAY_KIND_ALLOWED = {"aiclaw", "openclaw", "openclaw-cn", "unknown"}
_PLATFORM_ALLOWED = {"linux", "darwin", "win32", "windows", "freebsd", "unknown"}
# 拒绝把 skills 目录设到这些系统关键路径下，防止 install_skill 写入产生破坏
_FORBIDDEN_PATH_PREFIXES = (
    "/etc", "/usr", "/var", "/bin", "/sbin", "/root", "/boot", "/sys",
    "/proc", "/dev", "/tmp", "/run", "/lib", "/lib64", "/mnt", "/media",
    "/opt", "C:\\Windows", "C:\\Program Files",
)
# 拒绝用户家目录下的敏感子目录（SSH 私钥 / AWS 凭证 / 浏览器凭据等）
_FORBIDDEN_PATH_PATTERNS = (
    re.compile(r"^/home/[^/]+/\.ssh(/|$)"),
    re.compile(r"^/home/[^/]+/\.aws(/|$)"),
    re.compile(r"^/home/[^/]+/\.config(/|$)"),
    re.compile(r"^/home/[^/]+/\.gnupg(/|$)"),
    re.compile(r"^/Users/[^/]+/\.ssh(/|$)"),
    re.compile(r"^/Users/[^/]+/\.aws(/|$)"),
    re.compile(r"^/Users/[^/]+/Library/Keychains(/|$)"),
    re.compile(r"^C:\\Users\\[^\\]+\\AppData(\\|$)", re.IGNORECASE),
    re.compile(r"^C:\\Users\\[^\\]+\\\.ssh(\\|$)", re.IGNORECASE),
)


def _sanitize_skills_dir(path: str | None) -> str | None:
    """校验并清洗一个 bridge 上报的 skills 目录路径。

    返回 None 表示该路径不可信任，应被丢弃。

    要求：
    - 必须是字符串，长度 <= 512
    - 必须是绝对路径
    - 不含 .. 和 NUL
    - 不在系统关键路径 / 用户敏感子目录黑名单内
    """
    if not path or not isinstance(path, str):
        return None
    if len(path) > 512:
        return None
    if "\x00" in path or ".." in path:
        return None
    if not (path.startswith("/") or (len(path) >= 3 and path[1:3] == ":\\")):
        return None  # 必须是 absolute path（unix / windows）
    for prefix in _FORBIDDEN_PATH_PREFIXES:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "\\"):
            return None
    for pattern in _FORBIDDEN_PATH_PATTERNS:
        if pattern.match(path):
            return None
    return path


def _sanitize_capabilities(cap) -> dict:
    """对 bridge 上报的 capabilities 做严格白名单校验。

    P0-7 安全：bridge 已通过 auth_init/auth_response 双向 ed25519 校验，
    但 capabilities 帧本身无签名，仍需对字段值做长度/枚举/路径白名单防御，
    防止恶意 bridge 把 install_skill 目标改写到 /etc /usr 等系统目录。
    """
    gateway_kind = (cap.gateway_kind or "unknown").strip().lower()
    if gateway_kind not in _GATEWAY_KIND_ALLOWED:
        gateway_kind = "unknown"

    platform_value = (cap.platform or "unknown").strip().lower()
    if platform_value not in _PLATFORM_ALLOWED:
        platform_value = "unknown"

    safe_dirs: list[str] = []
    for d in (cap.skills_dirs or [])[:16]:  # 数量上限
        sane = _sanitize_skills_dir(d)
        if sane:
            safe_dirs.append(sane)

    default_dir = _sanitize_skills_dir(cap.skills_dir_default)
    if default_dir and default_dir not in safe_dirs:
        # default 必须出现在 dirs 列表中（前置约束）
        default_dir = safe_dirs[0] if safe_dirs else None
    if not default_dir and safe_dirs:
        default_dir = safe_dirs[0]

    gateway_version = (cap.gateway_version or "")[:64] or None
    bridge_version = (cap.bridge_version or "")[:64] or None
    runtimes = [
        str(item)[:50]
        for item in (getattr(cap, "runtimes", None) or [])
        if str(item).strip()
    ][:20]
    ops = [
        str(item)[:80]
        for item in (getattr(cap, "ops", None) or [])
        if str(item).strip()
    ][:120]

    return {
        "gateway_kind": gateway_kind,
        "gateway_version": gateway_version,
        "platform": platform_value,
        "skills_dirs": safe_dirs,
        "skills_dir_default": default_dir,
        "bridge_version": bridge_version,
        "runtimes": runtimes,
        "ops": ops,
    }


_TRAINING_TASKS_ALLOWED = {"lora", "qlora", "eval", "merge", "inference"}
_WORKLOAD_ROLES_ALLOWED = {"video_generation"}
_MEDIA_MODES_ALLOWED = {"text_to_video", "image_to_video", "reference_replay", "video_enhance"}
_MEDIA_TEMPLATES_ALLOWED = {
    "h3_t2v_v1", "h3_i2v_v1", "h3_r2v_v1", "video_upscale_realesrgan_v1",
}


def _sanitize_resident_models(value) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    safe: list[dict[str, object]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        model = str(item.get("model") or item.get("model_name") or item.get("model_id") or "").strip()[:160]
        deployment_id = str(item.get("deployment_id") or item.get("deploymentId") or "").strip()[:80]
        runtime_profile = str(item.get("runtime_profile") or item.get("runtimeProfile") or "").strip()[:80]
        status = str(item.get("status") or ("loaded" if item.get("loaded") else "unknown")).strip().lower()[:40]
        if not model and not deployment_id:
            continue
        safe.append({
            "model": model or deployment_id,
            "deployment_id": deployment_id or None,
            "runtime_profile": runtime_profile or None,
            "status": status or "unknown",
            "loaded": bool(item.get("loaded") or status in {"loaded", "ready", "running", "active"}),
            "last_heartbeat_at": str(item.get("last_heartbeat_at") or item.get("lastHeartbeatAt") or "")[:40] or None,
        })
    return safe


def _sanitize_training_capability(value) -> dict:
    if not isinstance(value, dict):
        return {}
    supported_tasks = [
        task
        for task in (str(item).strip().lower() for item in (value.get("supported_tasks") or []))
        if task in _TRAINING_TASKS_ALLOWED
    ][:10]
    safe: dict[str, object] = {
        "gateway": bool(value.get("gateway")),
        "supported_tasks": supported_tasks,
    }
    for key in ("gpu_count", "worker_count"):
        try:
            safe[key] = max(0, min(int(value.get(key) or 0), 1024))
        except (TypeError, ValueError):
            safe[key] = 0
    resident_models = _sanitize_resident_models(value.get("resident_models"))
    if resident_models:
        safe["resident_models"] = resident_models
    return safe


def _sanitize_workload_roles(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        role
        for role in (str(item or "").strip().lower() for item in value[:20])
        if role in _WORKLOAD_ROLES_ALLOWED
    ))


def _sanitize_media_capability(value) -> dict:
    if not isinstance(value, dict):
        return {}
    configured = bool(value.get("configured"))
    raw_template_ids = value.get("template_ids") if isinstance(value.get("template_ids"), list) else []
    template_ids = [
        item
        for item in (str(raw or "").strip() for raw in raw_template_ids[:20])
        if item in _MEDIA_TEMPLATES_ALLOWED
    ]
    raw_supported_modes = value.get("supported_modes") if isinstance(value.get("supported_modes"), list) else []
    supported_modes = [
        item
        for item in (str(raw or "").strip().lower() for raw in raw_supported_modes[:20])
        if item in _MEDIA_MODES_ALLOWED
    ]
    try:
        queue_depth = max(0, min(int(value.get("queue_depth") or 0), 10000))
    except (TypeError, ValueError):
        queue_depth = 0
    model_files = []
    raw_model_files = value.get("model_files") if isinstance(value.get("model_files"), list) else []
    for item in raw_model_files[:80]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:180]
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            continue
        try:
            size_bytes = max(0, min(int(item.get("size_bytes") or 0), 20 * 1024**4))
        except (TypeError, ValueError):
            size_bytes = 0
        sha256 = str(item.get("sha256") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            sha256 = ""
        model_files.append({
            "name": name,
            "size_bytes": size_bytes,
            "sha256": sha256 or None,
            "complete": bool(item.get("complete", True)),
        })
    model_sha256 = str(value.get("model_sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", model_sha256):
        model_sha256 = ""
    raw_postprocess = value.get("postprocess") if isinstance(value.get("postprocess"), dict) else {}
    governed_product_overlay = raw_postprocess.get("governed_product_overlay") is True
    allowed_overlay_anchors = {"bottom_right", "center"}
    raw_overlay_anchors = (
        raw_postprocess.get("anchors")
        if isinstance(raw_postprocess.get("anchors"), list)
        else []
    )
    overlay_anchors = [
        anchor
        for anchor in (str(raw or "").strip().lower() for raw in raw_overlay_anchors[:10])
        if anchor in allowed_overlay_anchors
    ]
    if governed_product_overlay and "bottom_right" not in overlay_anchors:
        overlay_anchors.append("bottom_right")
    allowed_motion_profiles = {
        "static_verified_product",
        "static_verified_product_dynamic_background",
    }
    raw_motion_profiles = (
        raw_postprocess.get("motion_profiles")
        if isinstance(raw_postprocess.get("motion_profiles"), list)
        else []
    )
    motion_profiles = [
        profile
        for profile in (str(raw or "").strip() for raw in raw_motion_profiles[:10])
        if profile in allowed_motion_profiles
    ]
    if governed_product_overlay and "static_verified_product" not in motion_profiles:
        motion_profiles.append("static_verified_product")
    raw_content_crop_policies = (
        raw_postprocess.get("content_crop_policies")
        if isinstance(raw_postprocess.get("content_crop_policies"), list)
        else []
    )
    content_crop_policies = [
        policy
        for policy in (str(raw or "").strip() for raw in raw_content_crop_policies[:10])
        if policy == "alpha_bbox_v1"
    ]
    postprocess = {
        "governed_product_overlay": governed_product_overlay,
        "overlay_roles": ["product_packshot", "product_detail"] if governed_product_overlay else [],
        "anchor": "bottom_right" if governed_product_overlay else None,
        "anchors": list(dict.fromkeys(overlay_anchors)) if governed_product_overlay else [],
        "motion_profiles": list(dict.fromkeys(motion_profiles)) if governed_product_overlay else [],
        "content_crop_policies": list(dict.fromkeys(content_crop_policies)) if governed_product_overlay else [],
    }
    return {
        "configured": configured,
        "online": bool(value.get("online")),
        "comfyui_version": str(value.get("comfyui_version") or "").strip()[:80] or None,
        "template_ids": list(dict.fromkeys(template_ids)),
        "supported_modes": list(dict.fromkeys(supported_modes)),
        "workload_roles": _sanitize_workload_roles(value.get("workload_roles")) if configured else [],
        "queue_depth": queue_depth,
        "model_files": model_files,
        "model_sha256": model_sha256 or None,
        "hashes_complete": bool(value.get("hashes_complete")),
        "postprocess": postprocess,
    }


async def _store_bridge_capabilities(instance_id: str, cap) -> None:
    """把 bridge 上报的本机能力写入实例字段（先做白名单 sanitize）。"""
    import json as _json
    sane = _sanitize_capabilities(cap)
    # 构建完整的 capabilities JSON（含 disk/memory/gpu），用于前端展示
    cap_full = dict(sane)
    if cap.disk:
        cap_full["disk"] = cap.disk
    if cap.memory:
        cap_full["memory"] = cap.memory
    if cap.gpu:
        cap_full["gpu"] = cap.gpu
    if getattr(cap, "training", None):
        training = _sanitize_training_capability(cap.training)
        if training:
            cap_full["training"] = training
    workload_roles = _sanitize_workload_roles(getattr(cap, "workload_roles", None))
    media = _sanitize_media_capability(getattr(cap, "media", None))
    if media:
        cap_full["media"] = media
        workload_roles = list(dict.fromkeys(workload_roles + media.get("workload_roles", [])))
    if not media.get("configured"):
        workload_roles = [role for role in workload_roles if role != "video_generation"]
    if workload_roles:
        cap_full["workload_roles"] = workload_roles
    resident_models = _sanitize_resident_models(getattr(cap, "resident_models", None))
    if resident_models:
        cap_full["resident_models"] = resident_models
    async with async_session_factory() as session:
        result = await session.execute(
            select(OpenClawInstance).where(OpenClawInstance.id == instance_id)
        )
        inst = result.scalar_one_or_none()
        if not inst:
            return
        inst.bridge_gateway_kind = sane["gateway_kind"]
        inst.bridge_gateway_version = sane["gateway_version"]
        inst.bridge_platform = sane["platform"]
        inst.bridge_skills_dir = sane["skills_dir_default"]
        inst.bridge_skills_dirs_json = _json.dumps(sane["skills_dirs"]) if sane["skills_dirs"] else None
        inst.bridge_version = sane["bridge_version"]
        inst.bridge_capabilities_json = _json.dumps(cap_full, ensure_ascii=False)
        await session.commit()


async def _authenticate(websocket: WebSocket, first: dict) -> str | None:
    if first.get("type") != "auth_init":
        await websocket.close(code=4400, reason="first frame must be auth_init")
        return None

    try:
        frame = AuthInitFrame.model_validate(first)
    except ValidationError:
        await websocket.close(code=4400, reason="invalid auth frame")
        return None

    instance_id = frame.instance_id.strip()
    if not instance_id:
        await websocket.close(code=4403, reason="missing instance_id")
        return None

    instance = await _lookup_instance(instance_id)
    if not instance:
        await websocket.close(code=4404, reason="instance not found")
        return None

    pubkey = frame.pubkey.strip()
    enrollment_token = frame.enrollment_token.strip()
    fingerprint = (frame.fingerprint or "").strip() or None

    async with async_session_factory() as session:
        placement_conflict = await has_active_platform_fingerprint_conflict(
            session,
            instance_id=instance_id,
            fingerprint=fingerprint,
            is_platform_default=bool(getattr(instance, "is_platform_default", False)),
        )
    if placement_conflict:
        bridge_reject_total.labels(instance_id=instance_id, reason="placement_conflict").inc()
        logger.warning(
            "{}: reject non-platform bridge on platform host instance={} fingerprint={}",
            BRIDGE_PLACEMENT_CONFLICT,
            instance_id,
            fingerprint,
        )
        await websocket.close(code=4403, reason="bridge placement conflict")
        return None

    # 判断 bridge 是否带着一个有效的 enrollment_token（admin 刚 regenerate 的情况）
    has_active_enrollment = bool(
        instance.enrollment_token_hash
        and not instance.enrollment_consumed_at
        and (not instance.enrollment_expires_at or instance.enrollment_expires_at >= now_bjt())
    )
    enrollment_token_valid = (
        has_active_enrollment
        and enrollment_token
        and verify_enrollment_token(enrollment_token, instance.enrollment_token_hash)
    )

    is_rotation_reenroll = False
    is_admin_reenroll = False  # admin 重发 enrollment 后的覆盖式 re-enrollment
    if instance.device_pubkey:
        if (
            instance.bridge_fingerprint
            and fingerprint
            and not bridge_fingerprints_match(instance.bridge_fingerprint, fingerprint)
        ):
            bridge_reject_total.labels(instance_id=instance_id, reason="fingerprint_mismatch").inc()
            await websocket.close(code=4403, reason="fingerprint mismatch")
            return None
        if pubkey and pubkey != instance.device_pubkey:
            # pubkey 不匹配的几种合法情况：
            # 1) admin 刚 regenerate-enrollment：bridge 可能换了新 device.key，
            #    如果 bridge 提供有效的 enrollment_token，允许覆盖
            # 2) admin 触发了 rotate-key（pending_rotation=True + rotation_token_hash）
            if enrollment_token_valid:
                is_admin_reenroll = True
            elif instance.pending_rotation and enrollment_token:
                if not verify_enrollment_token(enrollment_token, instance.rotation_token_hash):
                    bridge_reject_total.labels(instance_id=instance_id, reason="rotation_token_invalid").inc()
                    await websocket.close(code=4403, reason="rotation token invalid")
                    return None
                if instance.rotation_expires_at and instance.rotation_expires_at < now_bjt():
                    bridge_reject_total.labels(instance_id=instance_id, reason="rotation_token_expired").inc()
                    await websocket.close(code=4403, reason="rotation token expired")
                    return None
                is_rotation_reenroll = True
            else:
                bridge_reject_total.labels(instance_id=instance_id, reason="pubkey_mismatch").inc()
                await websocket.close(code=4403, reason="pubkey mismatch")
                return None
        else:
            pubkey = instance.device_pubkey
    else:
        if not pubkey or not enrollment_token:
            bridge_reject_total.labels(instance_id=instance_id, reason="missing_enrollment").inc()
            await websocket.close(code=4403, reason="missing enrollment credentials")
            return None
        if instance.enrollment_consumed_at:
            bridge_reject_total.labels(instance_id=instance_id, reason="enrollment_used").inc()
            await websocket.close(code=4403, reason="enrollment already consumed")
            return None
        if not verify_enrollment_token(enrollment_token, instance.enrollment_token_hash):
            bridge_reject_total.labels(instance_id=instance_id, reason="enrollment_invalid").inc()
            await websocket.close(code=4403, reason="invalid enrollment token")
            return None
        if instance.enrollment_expires_at and instance.enrollment_expires_at < now_bjt():
            bridge_reject_total.labels(instance_id=instance_id, reason="enrollment_expired").inc()
            await websocket.close(code=4403, reason="enrollment expired")
            return None

    nonce = secrets.token_hex(32)
    expires_at = now_bjt() + timedelta(seconds=settings.BRIDGE_AUTH_NONCE_TTL_SECONDS)
    await websocket.send_json(
        {
            "type": "auth_challenge",
            "instance_id": instance_id,
            "nonce": nonce,
            "expires_at": isoformat_bjt(expires_at),
        }
    )
    try:
        response = AuthResponseFrame.model_validate(
            await asyncio.wait_for(
                websocket.receive_json(),
                timeout=settings.BRIDGE_AUTH_NONCE_TTL_SECONDS,
            )
        )
    except (asyncio.TimeoutError, ValidationError):
        bridge_reject_total.labels(instance_id=instance_id, reason="auth_response_invalid").inc()
        await websocket.close(code=4403, reason="invalid auth response")
        return None
    _prune_used_nonces()
    if nonce in _USED_NONCES:
        bridge_reject_total.labels(instance_id=instance_id, reason="nonce_replayed").inc()
        await websocket.close(code=4403, reason="nonce replayed")
        return None
    if not verify_signature(pubkey, nonce, response.signature.strip()):
        bridge_reject_total.labels(instance_id=instance_id, reason="signature_invalid").inc()
        await websocket.close(code=4403, reason="signature invalid")
        return None
    _USED_NONCES[nonce] = expires_at

    if not instance.device_pubkey or is_rotation_reenroll or is_admin_reenroll:
        async with async_session_factory() as session:
            result = await session.execute(
                select(OpenClawInstance).where(OpenClawInstance.id == instance_id)
            )
            locked = result.scalar_one_or_none()
            if not locked:
                await websocket.close(code=4404, reason="instance disappeared")
                return None
            if locked.enrollment_consumed_at:
                await websocket.close(code=4403, reason="enrollment already consumed")
                return None
            locked.device_pubkey = pubkey
            locked.bridge_fingerprint = fingerprint
            locked.enrollment_consumed_at = now_bjt()
            locked.enrollment_token_hash = None
            locked.pending_rotation = False
            locked.rotation_token_hash = None
            locked.rotation_expires_at = None
            locked.bridge_connected_at = now_bjt()
            locked.last_heartbeat = locked.bridge_connected_at
            auth_heartbeat_at = locked.last_heartbeat
            auth_department = locked.department
            await session.commit()
            clear_rotation_token(instance_id)

        # 心跳同步到 task_nodes_light：fire-and-forget，失败入 repair_queue
        from app.tasktree import dispatcher as tasktree_dispatcher

        # [m5] 复用共享的心跳协程工厂
        await tasktree_dispatcher.schedule_writer(
            tasktree_dispatcher.make_heartbeat_coro(
                instance_id=instance_id,
                last_heartbeat_at=auth_heartbeat_at,
                department_id=auth_department,
                record_history=True,
            ),
            source_type="heartbeat",
            source_ref=instance_id,
            operation="sync_heartbeat",
            payload={
                "instance_id": instance_id,
                "department_id": auth_department,
                "last_heartbeat_at": isoformat_bjt(auth_heartbeat_at),
                "record_history": True,
            },
        )
        # [M4] 新设备 / rotation_reenroll / admin_reenroll 三条路径补齐 finalize
        await _finalize_bridge_authenticated(instance_id, auth_department)
    else:
        # 已绑定 pubkey 走 challenge-signature 路径成功后，
        # 顺手清掉残留的 enrollment token hash（避免 admin 重发后忘了用而长期遗留）
        async with async_session_factory() as session:
            result = await session.execute(
                select(OpenClawInstance).where(OpenClawInstance.id == instance_id)
            )
            locked = result.scalar_one_or_none()
            if locked and locked.enrollment_token_hash:
                locked.enrollment_token_hash = None
                locked.enrollment_expires_at = None
                if not locked.enrollment_consumed_at:
                    locked.enrollment_consumed_at = now_bjt()
                await session.commit()
        await _mark_connected(instance_id, fingerprint=fingerprint)

    await websocket.send_json({"type": "auth_ok", "instance_id": instance_id, "auth_mode": "device_pubkey"})
    return instance_id


@ws_router.websocket("/bridge/ws")
async def bridge_ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    instance_id = None
    ping_task = None
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        instance_id = await _authenticate(websocket, first)
        if not instance_id:
            return

        conn = await bridge_registry.register(instance_id, websocket)
        await websocket.send_json({"type": "epoch_bound", "instance_id": instance_id, "epoch": conn.epoch})
        ping_task = asyncio.create_task(_ping_loop(websocket, conn))

        # bridge 重连后自动推送定时配置  + 重试积压的 Skill 同步任务
        asyncio.create_task(_repush_schedules_on_reconnect(instance_id))
        asyncio.create_task(_retry_pending_syncs_on_reconnect(instance_id))

        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")
            if msg_type == "pong":
                PongFrame.model_validate(msg)
                conn.last_ping = now_bjt()
                await _mark_connected(instance_id)
            elif msg_type == "forward_response":
                await conn.handle_forward_response(ForwardResponseFrame.model_validate(msg).model_dump())
            elif msg_type == "forward_event":
                await conn.handle_forward_event(ForwardEventFrame.model_validate(msg).model_dump())
            elif msg_type == "bridge_op_response":
                await conn.handle_bridge_op_response(BridgeOpResponseFrame.model_validate(msg).model_dump())
            elif msg_type == "bridge_op_response_chunk":
                await conn.handle_bridge_op_response_chunk(BridgeOpResponseChunkFrame.model_validate(msg).model_dump())
            elif msg_type == "bridge_capabilities":
                try:
                    cap = BridgeCapabilitiesFrame.model_validate(msg)
                    await _store_bridge_capabilities(instance_id, cap)
                    await _maybe_request_bridge_auto_update(websocket, instance_id, cap.bridge_version)
                except Exception as e:
                    logger.warning(
                        "bridge_capabilities 处理失败 (instance={}): {}",
                        instance_id,
                        e,
                        exc_info=True,
                    )
            elif msg_type == "shutdown":
                ShutdownFrame.model_validate(msg)
                break
    except WebSocketDisconnect:
        pass
    finally:
        if ping_task:
            ping_task.cancel()
        if instance_id:
            removed_current = await bridge_registry.unregister(instance_id, websocket)
            if removed_current:
                await _mark_disconnected(instance_id)


async def _ping_loop(websocket: WebSocket, conn) -> None:
    try:
        while True:
            await asyncio.sleep(30)
            if (now_bjt() - conn.last_ping).total_seconds() > 60:
                await websocket.close(code=1011, reason="bridge heartbeat timeout")
                return
            instance = await _lookup_instance(conn.instance_id)
            payload = {"type": "ping"}
            if instance and instance.pending_rotation and instance.rotation_token_hash and instance.rotation_expires_at and instance.rotation_expires_at > now_bjt():
                payload["rotate_required"] = True
                payload["rotation_token"] = get_rotation_token(conn.instance_id)
            await websocket.send_json(payload)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            "bridge ping_loop 异常退出 (instance={}): {}",
            conn.instance_id if conn else "?",
            e,
            exc_info=True,
        )
        return
