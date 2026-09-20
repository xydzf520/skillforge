"""Workbench Task Contract 相关路由：生成/审核/绑定合同、一步到位 Skill 创建流式端点。"""

from contextlib import suppress

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import role_matches_any
from app.auth.dependencies import require_role
from app.auth.models import User
from app.common.ws_auth import (
    WS_CODE_AUTH_REQUIRED,
    WS_CODE_PERMISSION_DENIED,
    get_current_user_ws,
)
from app.database import get_db
from app.workbench.schemas import (
    WorkbenchTaskContractRequest,
    WorkbenchTaskContractResponse,
    WorkbenchTaskContractBindRequest,
    WorkbenchTaskContractBindResponse,
    WorkbenchTaskContractReviewRequest,
    WorkbenchTaskContractReviewResponse,
)
from app.workbench.service import workbench_service
from app.workbench import review_context_service as review_ctx_service
from app.common.time_utils import isoformat_bjt, now_bjt

router = APIRouter()


class CreationIdPreviewRequest(BaseModel):
    name: str = Field("", max_length=80)
    department: str = Field("", max_length=50)


class FinalizeCreateSkillRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    department: str | None = Field(default=None, max_length=50)
    risk_level: str | None = Field(default=None, max_length=2)
    skill_id: str | None = Field(default=None, max_length=50)


@router.post("/workbench/task-contract", response_model=WorkbenchTaskContractResponse)
async def generate_task_contract(
    body: WorkbenchTaskContractRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """一句话目标 → TaskContract + intent.md + 预演结果。"""
    return await workbench_service.generate_task_contract(body.message, current_user.id)


@router.post("/workbench/task-contract/{draft_id}/review", response_model=WorkbenchTaskContractReviewResponse)
async def review_task_contract(
    draft_id: str,
    body: WorkbenchTaskContractReviewRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """记录 4 必感知点中的一次确认动作。"""
    return await workbench_service.review_task_contract(
        draft_id=draft_id,
        checkpoint=body.checkpoint,
        decision=body.decision,
        detail=body.detail,
        user_id=current_user.id,
    )


@router.post("/workbench/task-contract/{draft_id}/bind", response_model=WorkbenchTaskContractBindResponse)
async def bind_task_contract(
    draft_id: str,
    body: WorkbenchTaskContractBindRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """创建真实 Skill 后，把任务合同草稿与 Skill 绑定。"""
    return await workbench_service.bind_task_contract(
        draft_id=draft_id,
        skill_id=body.skill_id,
        user_id=current_user.id,
    )


# ===== v7 一步到位 skill 创建：流式 WS + finalize REST =====


@router.websocket("/workbench/create-skill/stream")
async def create_skill_stream(websocket: WebSocket):
    """
    一步到位 skill 创建流式 WebSocket 端点（后台任务模式）。

    协议：
        客户端 → {"type": "start", "message": "<完整 SOP>"}      (启动新创建)
        客户端 → {"type": "resume", "draft_id": "..."}            (刷新后重连已有创建)
        客户端 → {"type": "ping"}     → {"type": "pong"}         (保活)

        服务端事件同前（draft_created / started / milestone / thinking /
        tool_call / tool_result / contract_ready / skill_ready /
        skill_ready_enriched / retry / error / done）
    """
    import asyncio as _asyncio
    from app.coding_agent.creation_task_manager import creation_task_manager

    await websocket.accept()

    user = await get_current_user_ws(websocket)
    if not user:
        await websocket.close(code=WS_CODE_AUTH_REQUIRED, reason="unauthorized")
        return
    if not role_matches_any(user, ("admin", "ai_engineer", "aibp")):
        await websocket.close(code=WS_CODE_PERMISSION_DENIED, reason="permission denied")
        return

    # P1 #6: 首帧 30s 超时
    try:
        msg = await _asyncio.wait_for(websocket.receive_json(), timeout=30)
    except _asyncio.TimeoutError:
        with suppress(Exception):
            await websocket.send_json({"type": "error", "code": "TIMEOUT", "detail": "首帧超时 30s"})
            await websocket.close(code=1003)
        return
    except Exception:
        with suppress(Exception):
            await websocket.close(code=1003, reason="invalid payload")
        return

    msg_type = msg.get("type")
    draft_id = None

    if msg_type == "start":
        sop = (msg.get("message") or "").strip()
        if len(sop) < 5:
            await websocket.send_json({"type": "error", "code": "EMPTY_MESSAGE", "detail": "message 不能为空"})
            await websocket.close(code=1003)
            return
        if len(sop) > 20000:
            await websocket.send_json({"type": "error", "code": "MESSAGE_TOO_LONG", "detail": f"超过 20000 字符限制"})
            await websocket.close(code=1003)
            return

        # 启动后台任务（立即返回 draft_id，不阻塞 WS）
        draft_id = await workbench_service.start_skill_creation_task(
            message=sop, user_id=user.id,
        )
        await websocket.send_json({"type": "draft_created", "draft_id": draft_id})
        logger.info("[create-skill-stream] start user={} draft={} sop_len={}", user.id, draft_id, len(sop))

    elif msg_type == "resume":
        draft_id = msg.get("draft_id")
        if not draft_id:
            await websocket.send_json({"type": "error", "code": "MISSING_DRAFT_ID", "detail": "resume 需要 draft_id"})
            await websocket.close(code=1003)
            return
        # 安全：验证 draft 属于当前用户
        from app.workbench.models import SkillStudioDraft
        from sqlalchemy import select as _select
        from app.database import async_session_factory
        async with async_session_factory() as _sess:
            _draft = (await _sess.execute(
                _select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id)
            )).scalar_one_or_none()
            if not _draft or _draft.user_id != user.id:
                await websocket.send_json({"type": "error", "code": "AUTH_PERMISSION_DENIED", "detail": "无权访问此 draft"})
                await websocket.close(code=1003)
                return
        logger.info("[create-skill-stream] resume user={} draft={}", user.id, draft_id)

    else:
        await websocket.send_json({"type": "error", "code": "INVALID_MESSAGE_TYPE", "detail": "first message must be start or resume"})
        await websocket.close(code=1003)
        return

    # 订阅后台任务的事件队列
    # P2 #1: 每 25s 推 heartbeat 防 nginx 断开
    heartbeat_task = None
    try:
        async def _heartbeat():
            while True:
                await _asyncio.sleep(25)
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except Exception:
                    break

        heartbeat_task = _asyncio.create_task(_heartbeat())

        async for frame in creation_task_manager.subscribe(draft_id):
            try:
                await websocket.send_json(frame)
            except Exception:
                # 客户端断开 — 不影响后台任务，它继续跑
                logger.info("[create-skill-stream] send failed (client gone), detaching user={}", user.id)
                break
            if frame.get("type") == "done":
                break
    except WebSocketDisconnect:
        # 后台任务不受影响，继续跑
        logger.info("[create-skill-stream] client disconnected user={} draft={}", user.id, draft_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("[create-skill-stream] unexpected: {}", e)
        with suppress(Exception):
            await websocket.send_json({"type": "error", "code": "INTERNAL", "detail": str(e)[:200]})
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        with suppress(Exception):
            await websocket.close()


@router.get("/workbench/my-active-draft")
async def get_my_active_draft(
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """
    检查当前用户是否有正在运行或已就绪待确认的 skill 创建草稿。
    前端在 /skills/new 页面加载时调用，支持刷新后自动恢复。

    返回 {draft_id, generation_status, contract, checkpoints, gate} 或 null。
    """
    return await workbench_service.get_my_active_draft(user_id=current_user.id)


@router.get("/workbench/create-skill/context")
async def get_creation_context(
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """创建页上下文：默认部门、可选部门、部门来源和命名策略。"""
    return await review_ctx_service.get_creation_context(user=current_user)


@router.post("/workbench/create-skill/preview-id")
async def preview_creation_id(
    body: CreationIdPreviewRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """按当前名称/部门返回 Skill ID 预览。"""
    context = await review_ctx_service.get_creation_context(user=current_user)
    department = (body.department or context.get("default_department") or "").strip()
    allowed = set(context.get("allowed_departments") or [])
    if department and allowed and department not in allowed:
        from app.common.exceptions import AppError

        raise AppError(
            "AUTH_PERMISSION_DENIED",
            403,
            {"detail": f"你没有权限向部门 {department!r} 创建 Skill"},
        )
    return review_ctx_service.preview_creation_name(name=body.name, department=department)


@router.get("/workbench/my-drafts")
async def list_my_drafts(
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """v2.8.2 C2：列出当前用户所有 active draft（不仅一个）。

    active 的定义：`generation_status in (pending, running, ready)` 且
    `locked_until` 未过期（或为空）。finalized / cancelled / 过期 30 天以上不返回。

    前端用于"草稿中心"UI（想重新打开最近某个半成品）。
    """
    from datetime import datetime, timedelta

    from sqlalchemy import or_, select

    from app.workbench.models import SkillStudioDraft

    stale_threshold = now_bjt() - timedelta(days=30)

    rows = (
        await db.execute(
            select(SkillStudioDraft)
            .where(SkillStudioDraft.user_id == current_user.id)
            .where(SkillStudioDraft.generation_status.in_(["pending", "running", "ready"]))
            .where(SkillStudioDraft.updated_at >= stale_threshold)
            .order_by(SkillStudioDraft.updated_at.desc())
            .limit(20)
        )
    ).scalars().all()

    items = []
    for r in rows:
        meta_name = None
        if r.contract_json and isinstance(r.contract_json, dict):
            meta_name = (r.contract_json.get("meta") or {}).get("name")
        # error_detail 被复用成进度快照容器 (milestones/tool_calls),
        # 只有含 "code" 字段才是真错误
        err = r.error_detail if isinstance(r.error_detail, dict) else None
        has_real_error = bool(err and err.get("code"))
        items.append({
            "draft_id": r.id,
            "skill_id": r.skill_id,
            "generation_status": r.generation_status,
            "created_at": isoformat_bjt(r.created_at),
            "updated_at": isoformat_bjt(r.updated_at),
            "meta_name": meta_name,
            "description_excerpt": (r.source_message or "")[:80],
            "has_error": has_real_error,
        })
    return {"items": items, "total": len(items)}


@router.post("/workbench/draft/{draft_id}/dismiss")
async def dismiss_draft(
    draft_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """放弃草稿，标记 cancelled，不再出现在 my-active-draft 里。"""
    return await workbench_service.dismiss_draft(draft_id=draft_id, user_id=current_user.id)


@router.post("/workbench/create-skill/finalize/{draft_id}")
async def finalize_create_skill(
    draft_id: str,
    body: FinalizeCreateSkillRequest | None = None,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """
    4 必感知点全部确认后，把 draft 落到 skill_repo + git commit + 写 DB。
    返回 {draft_id, skill_id, git_commit, quality_score}。
    """
    return await review_ctx_service.finalize_skill_creation(
        draft_id=draft_id,
        user_id=current_user.id,
        override=body.model_dump(exclude_none=True) if body else None,
    )


# ===== v7 C2: 草稿锁与多用户分支 =====


@router.post("/workbench/skills/{skill_id}/fork-personal-branch")
async def fork_personal_branch(
    skill_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """v7 C2：基于 main 分支创建 personal/<user_id> 分支，避开冲突。"""
    return await workbench_service.fork_personal_branch(skill_id=skill_id, user_id=current_user.id)


@router.post("/workbench/drafts/{draft_id}/force-takeover")
async def force_takeover_draft(
    draft_id: str,
    current_user: User = Depends(require_role("admin")),
):
    """v7 C2：admin 强制接管草稿（释放原持有人的锁）。"""
    return await workbench_service.force_takeover_draft(
        draft_id=draft_id,
        admin_user_id=current_user.id,
        admin_role="admin",
    )
