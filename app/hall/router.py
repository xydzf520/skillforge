"""大厅 v3 入口路由。"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_state_active
from app.auth.models import User
from app.common.ws_auth import WS_CODE_AUTH_REQUIRED, WS_CODE_OVERFLOW, WS_CODE_PERMISSION_DENIED, get_current_user_ws
from app.common.ws_session import spawn_json_heartbeat, spawn_ping_pong_listener, user_ws_session_limiter
from app.database import async_session_factory, get_db
from app.datasources import hall_service as data_hall_service
from app.hall import direct_capability_service, team_service
from app.hall.ai_chat_router import router as ai_chat_router
from app.skills import hall_service as skill_hall_service

router = APIRouter()
router.include_router(ai_chat_router, prefix="/ai-chat", tags=["大厅 · AI Chat"])


class DirectCapabilityUIPreferenceRequest(BaseModel):
    surface: str = Field(default="run_form")
    overlay: dict[str, Any] | None = Field(default=None)
    generated_by: str | None = Field(default="manual")
    prompt_summary: str | None = Field(default=None)


class DirectCapabilityUIAIPreviewRequest(BaseModel):
    surface: str = Field(default="run_form")
    instruction: str
    current_overlay: dict[str, Any] | None = Field(default=None)


async def _receive_direct_capability_stream_params(websocket: WebSocket) -> dict[str, Any]:
    """Read the first client payload for the direct-capability run stream."""
    deadline = asyncio.get_running_loop().time() + 10
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        msg = await asyncio.wait_for(websocket.receive_json(), timeout=remaining)
        if not isinstance(msg, dict):
            raise ValueError("invalid payload")
        msg_type = str(msg.get("type") or "").lower()
        if msg_type == "ping":
            await websocket.send_json({"type": "pong"})
            continue
        if msg_type == "params":
            params = msg.get("params")
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            return params
        if not msg_type:
            return msg
        raise ValueError(f"unsupported message type: {msg_type}")


def _direct_capability_task_terminal(task: dict[str, Any]) -> bool:
    return str(task.get("status") or "").lower() in {"completed", "succeeded", "success", "failed", "error", "canceled", "cancelled"}


@router.get("/data")
async def list_data_hall(
    q: str | None = Query(None),
    department: list[str] | None = Query(None, description="按部门过滤，多选"),
    source_type: str | None = Query(None),
    visibility: str | None = Query(None, description="company / department / private"),
    freshness: str | None = Query(None, description="fresh / stale / unknown"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort_by: str = Query("updated_at", description="updated_at / consumers_desc / name_asc"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 数据能力列表。按三级 visibility + admin 豁免过滤。

    v2.7.5：新增 visibility / freshness 过滤、部门多选、consumers_desc 排序。
    """
    return await data_hall_service.list_data_hall(
        db,
        current_user,
        q=q,
        department=department,
        source_type=source_type,
        visibility=visibility,
        freshness=freshness,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
    )


@router.get("/data/{source_id}")
async def get_data_hall_detail(
    source_id: str,
    unmask: bool = Query(False, description="true: 返回敏感字段原值（写审计）；需 admin 或有效 grant"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 数据能力详情（含 consumers 反链 + my_access 状态 + schema_preview 脱敏）。"""
    return await data_hall_service.get_data_hall_detail(
        db, current_user, source_id, unmask=unmask
    )


@router.get("/team")
async def list_team_capabilities(
    q: str | None = Query(None, description="按部门名 / AI 联系人名模糊匹配"),
    sort_by: str = Query("active_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=1, le=200),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 团队能力：按部门聚合 Skill / 数据源 / AI 联系人。

    v2.7.5：从整体数组改为分页 ``{items, total, page, page_size}``；
    老调用方可从 items 字段取到与旧结构一致的数组。
    """
    return await team_service.list_team_capabilities(
        db, current_user, q=q, sort_by=sort_by, page=page, page_size=page_size
    )


@router.get("/departments")
async def list_departments(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅部门筛选项：直接读取系统组织架构部门。"""
    return await team_service.list_departments(db)


@router.get("/direct-capabilities")
async def list_direct_capabilities(
    q: str | None = Query(None, description="按能力名 / 描述 / 分类模糊匹配"),
    category: str | None = Query(None, description="能力分类"),
    department: list[str] | None = Query(None, description="按部门过滤，多选"),
    sort_by: str = Query("name_asc", description="name_asc / updated_at / risk_asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=1, le=200),
    current_user: User = Depends(require_state_active),
):
    """大厅 · 直接运行能力列表。

    这类能力来自 skills-repo 中声明为 direct/capability 的 Skill，不需要 OpenClaw 会话。
    """
    return direct_capability_service.list_direct_capabilities(
        current_user=current_user,
        q=q,
        category=category,
        department=department,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )


@router.get("/direct-artifacts")
async def list_direct_capability_artifacts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 当前用户直接能力生成产物聚合。"""
    return await direct_capability_service.list_direct_capability_artifacts(
        db,
        user_id=current_user.id,
        current_user=current_user,
        page=page,
        page_size=page_size,
    )


@router.get("/direct-capabilities/{capability_id}")
async def get_direct_capability(
    capability_id: str,
    current_user: User = Depends(require_state_active),
):
    """大厅 · 直接运行能力详情（含 contract/input_schema/output_schema）。"""
    return direct_capability_service.get_direct_capability(capability_id, current_user=current_user)


@router.get("/direct-capabilities/{capability_id}/ui")
async def get_direct_capability_ui(
    capability_id: str,
    surface: str = Query("run_form"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 直接运行能力个人页面 overlay。"""
    return await direct_capability_service.get_direct_capability_ui(
        db,
        capability_id,
        current_user=current_user,
        surface=surface,
    )


@router.post("/direct-capabilities/{capability_id}/ui/ai-preview")
async def preview_direct_capability_ui(
    capability_id: str,
    body: DirectCapabilityUIAIPreviewRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 用平台 AI 生成直接运行能力个人页面 overlay 预览。"""
    return await direct_capability_service.preview_direct_capability_ui(
        db,
        capability_id,
        current_user=current_user,
        surface=body.surface,
        instruction=body.instruction,
        current_overlay=body.current_overlay,
    )


@router.put("/direct-capabilities/{capability_id}/ui/preferences")
async def save_direct_capability_ui_preference(
    capability_id: str,
    body: DirectCapabilityUIPreferenceRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 保存直接运行能力个人页面 overlay。"""
    return await direct_capability_service.save_direct_capability_ui_preference(
        db,
        capability_id,
        current_user=current_user,
        surface=body.surface,
        overlay=body.overlay,
        generated_by=body.generated_by or "manual",
        prompt_summary=body.prompt_summary,
    )


@router.get("/direct-capabilities/{capability_id}/ui/preferences")
async def list_direct_capability_ui_preferences(
    capability_id: str,
    surface: str = Query("run_form"),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 直接运行能力个人页面保存记录。"""
    return await direct_capability_service.list_direct_capability_ui_preferences(
        db,
        capability_id,
        current_user=current_user,
        surface=surface,
        limit=limit,
    )


@router.post("/direct-capabilities/{capability_id}/ui/preferences/{preference_id}/restore")
async def restore_direct_capability_ui_preference(
    capability_id: str,
    preference_id: str,
    surface: str = Query("run_form"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 从保存记录恢复直接运行能力个人页面。"""
    return await direct_capability_service.restore_direct_capability_ui_preference(
        db,
        capability_id,
        current_user=current_user,
        preference_id=preference_id,
        surface=surface,
    )


@router.delete("/direct-capabilities/{capability_id}/ui/preferences")
async def delete_direct_capability_ui_preference(
    capability_id: str,
    surface: str = Query("run_form"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 恢复直接运行能力默认页面。"""
    return await direct_capability_service.delete_direct_capability_ui_preference(
        db,
        capability_id,
        current_user=current_user,
        surface=surface,
    )


@router.post("/direct-capabilities/{capability_id}/uploads")
async def upload_direct_capability_reference(
    capability_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(require_state_active),
):
    """大厅 · 上传直接运行能力的参考图，返回服务端临时路径。"""
    return await direct_capability_service.save_direct_capability_upload(
        capability_id,
        file,
        current_user=current_user,
    )


@router.get("/direct-capabilities/{capability_id}/download-image")
async def download_direct_capability_image(
    capability_id: str,
    url: str = Query(..., description="图片 URL"),
    filename: str | None = Query(None, description="下载文件名"),
    current_user: User = Depends(require_state_active),
):
    """大厅 · 代理下载直接运行能力返回的图片，避免跨域图片打开新页面。"""
    return await direct_capability_service.download_direct_capability_image(
        capability_id,
        url,
        filename,
        current_user=current_user,
    )


@router.get("/direct-capabilities/{capability_id}/image-history")
async def list_direct_capability_image_history(
    capability_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    refresh_missing_sizes: bool = Query(False),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 当前用户在该直接能力下生成过的历史图片。"""
    return await direct_capability_service.list_direct_capability_image_history(
        db,
        user_id=current_user.id,
        capability_id=capability_id,
        current_user=current_user,
        page=page,
        page_size=page_size,
        refresh_missing_sizes=refresh_missing_sizes,
    )


@router.post("/direct-capabilities/{capability_id}/run")
async def run_direct_capability(
    capability_id: str,
    body: dict[str, Any] | None = None,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 直接执行能力。运行本地 scripts/main.py，不占用 OpenClaw。"""
    return await direct_capability_service.run_direct_capability(
        capability_id,
        body,
        db=db,
        user_id=current_user.id,
        current_user=current_user,
    )


@router.post("/direct-capabilities/{capability_id}/tasks")
async def create_direct_capability_task(
    capability_id: str,
    body: dict[str, Any] | None = None,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 创建直接能力异步任务。"""
    return await direct_capability_service.create_direct_capability_task(
        db,
        capability_id,
        body,
        user_id=current_user.id,
        current_user=current_user,
    )


@router.websocket("/direct-capabilities/{capability_id}/tasks/stream")
async def stream_direct_capability_task(
    websocket: WebSocket,
    capability_id: str,
):
    """大厅 · 直接能力运行流。

    首帧接收 `{type:"params", params:{...}}`，创建任务后持续推进并推送
    `task` 事件，直到任务进入终态。Direct capability runner 仍走现有
    `DirectCapabilityTask` 持久化队列，断开后任务不会丢失。
    """
    await websocket.accept()

    current_user = await get_current_user_ws(websocket)
    if current_user is None:
        await websocket.close(code=WS_CODE_AUTH_REQUIRED, reason="unauthorized")
        return
    user_state = getattr(current_user, "state", None) or ("active" if current_user.is_active else "disabled")
    if user_state != "active":
        await websocket.close(code=WS_CODE_PERMISSION_DENIED, reason="account not active")
        return

    lease = await user_ws_session_limiter.acquire("hall-direct-capability", current_user.id)
    if not lease.acquired:
        await websocket.send_json({
            "type": "error",
            "message": f"该账号已有 {lease.count} 个能力运行流正在运行，请先关闭旧连接",
        })
        await websocket.close(code=WS_CODE_OVERFLOW, reason="too many direct capability streams")
        return

    heartbeat_task = None
    control_task = None
    try:
        params = await _receive_direct_capability_stream_params(websocket)
        heartbeat_task = spawn_json_heartbeat(
            websocket,
            lease=lease,
            payload={"type": "heartbeat", "scope": "hall-direct-capability"},
        )
        control_task = spawn_ping_pong_listener(websocket)

        await websocket.send_json({"type": "started", "capability_id": capability_id})
        async def emit_runner_event(event: dict[str, Any]) -> None:
            await websocket.send_json({"type": "runner_event", "event": event})

        async with async_session_factory() as session:
            task = await direct_capability_service.create_direct_capability_task(
                session,
                capability_id,
                {"params": params},
                user_id=current_user.id,
                current_user=current_user,
                on_event=emit_runner_event,
            )
            await session.commit()

        await websocket.send_json({"type": "task", "phase": "created", "task": task})

        while not _direct_capability_task_terminal(task):
            await asyncio.sleep(2)
            async with async_session_factory() as session:
                task = await direct_capability_service.get_direct_capability_task(
                    session,
                    capability_id,
                    str(task["id"]),
                    user_id=current_user.id,
                    current_user=current_user,
                    advance=True,
                    on_event=emit_runner_event,
                )
                await session.commit()
            await websocket.send_json({"type": "task", "phase": "progress", "task": task})

        await websocket.send_json({"type": "result", "task": task})
    except WebSocketDisconnect:
        logger.info("direct capability stream disconnected capability={} user={}", capability_id, current_user.id)
    except asyncio.TimeoutError:
        try:
            await websocket.send_json({"type": "error", "message": "参数接收超时"})
        except Exception as exc:  # noqa: BLE001
            logger.debug("direct capability stream timeout send failed: {}", exc)
    except ValueError as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception as send_exc:  # noqa: BLE001
            logger.debug("direct capability stream value error send failed: {}", send_exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("direct capability stream failed capability={} user={}", capability_id, current_user.id)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)[:500]})
        except Exception as send_exc:  # noqa: BLE001
            logger.debug("direct capability stream error send failed: {}", send_exc)
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        if control_task:
            control_task.cancel()
        if heartbeat_task:
            try:
                await heartbeat_task
            except BaseException:
                pass
        if control_task:
            try:
                await control_task
            except BaseException:
                pass
        await lease.release()
        try:
            await websocket.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("direct capability stream close failed: {}", exc)


@router.get("/direct-capabilities/{capability_id}/tasks")
async def list_direct_capability_tasks(
    capability_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    advance: bool = Query(False),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 当前用户直接能力任务队列。默认只读；调用方可显式 advance 推进。"""
    return await direct_capability_service.list_direct_capability_tasks(
        db,
        capability_id,
        user_id=current_user.id,
        current_user=current_user,
        page=page,
        page_size=page_size,
        advance=advance,
    )


@router.get("/direct-capabilities/{capability_id}/tasks/{task_id}")
async def get_direct_capability_task(
    capability_id: str,
    task_id: str,
    advance: bool = Query(False),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 查询直接能力单个任务。"""
    return await direct_capability_service.get_direct_capability_task(
        db,
        capability_id,
        task_id,
        user_id=current_user.id,
        current_user=current_user,
        advance=advance,
    )


@router.post("/direct-capabilities/{capability_id}/tasks/{task_id}/retry")
async def retry_direct_capability_task(
    capability_id: str,
    task_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 复制原参数重试直接能力任务。"""
    return await direct_capability_service.retry_direct_capability_task(
        db,
        capability_id,
        task_id,
        user_id=current_user.id,
        current_user=current_user,
    )


@router.get("/team/{department}")
async def get_team_detail(
    department: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """某部门详情：active skills + data_sources + members。"""
    return await team_service.get_team_detail(db, current_user, department)


@router.get("/capabilities")
async def list_capabilities(
    q: str | None = Query(None, description="按能力名模糊匹配"),
    department: list[str] | None = Query(None, description="按部门过滤（多选）"),
    sort_by: str = Query("active_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=1, le=200),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅 · 按能力（Skill.category）聚合 —— 按"公司能提供什么能力"视角。

    返回 ``{items, total, page, page_size}``。老调用方仍可读 ``items`` 字段。
    """
    return await team_service.list_capabilities(
        db,
        current_user,
        q=q,
        departments=department,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )


@router.get("/capability/{category}")
async def get_capability_detail(
    category: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """某能力的详情：skills + 依赖数据源 + 主要提供部门 + AI 联系人。"""
    return await team_service.get_capability_detail(db, current_user, category)


@router.get("/overview")
async def hall_overview(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅统一 landing：3 视图 summary。"""
    from sqlalchemy import func, select
    from app.skills.core.models import Skill
    from app.datasources.models import DataSource

    # Skill 总数（对当前用户可见的）
    skill_stmt = select(Skill.id)
    skill_stmt = await skill_hall_service._apply_visibility(db, current_user, skill_stmt)
    skill_total = (await db.execute(select(func.count()).select_from(skill_stmt.subquery()))).scalar() or 0
    # 数据源总数
    ds_stmt = select(DataSource.id).where(DataSource.is_active.is_(True))
    ds_stmt = await data_hall_service._apply_visibility(db, current_user, ds_stmt)
    ds_total = (await db.execute(select(func.count()).select_from(ds_stmt.subquery()))).scalar() or 0
    teams_resp = await team_service.list_team_capabilities(db, current_user, page=1, page_size=200)
    teams = teams_resp["items"] if isinstance(teams_resp, dict) else teams_resp

    return {
        "skill": {"total": skill_total, "department_count": len(teams)},
        "data": {"total": ds_total},
        "team": {"department_count": len(teams), "top": teams[:5]},
    }
