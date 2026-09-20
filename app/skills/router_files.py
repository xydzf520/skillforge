"""Skill 文件与结构化保存路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import WebSocket, WebSocketDisconnect

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.cache import invalidate_skill
from app.common.exceptions import AppError
from app.common.ws_auth import WS_CODE_AUTH_REQUIRED, get_current_user_ws
from app.database import get_db
from app.skills.lifecycle import service
from app.skills.core.service_shared import ensure_skill_access

router = APIRouter()


class SaveContentRequest(BaseModel):
    skill_md: str | None = None
    policy_pack_raw: str | None = None
    script_content: str | None = None


class SaveStructuredRequest(BaseModel):
    frontmatter: dict | None = None
    purpose: str | None = None
    steps: list[dict] | None = None
    antipatterns: list[dict] | None = None
    output_definition: list[dict] | None = None
    data_inputs: list[dict] | None = None
    test_cases: list[dict] | None = None
    custom_sections: dict | None = None
    policy_pack: dict | None = None


class RunScriptRequest(BaseModel):
    path: str
    payload: dict | None = None
    timeout: int = 10


# ── 权限矩阵：角色 → 可写范围 ──────────────────────────────
# biz_owner 只能写 SKILL.md / policy_pack.yaml（业务层文件）
# admin / ai_engineer / aibp 可写所有文件（含脚本等技术文件）
_BIZ_OWNER_WRITABLE = {"SKILL.md", "policy_pack.yaml"}
_WRITE_ROLES_FULL = {"system_admin", "admin", "ai_engineer", "aibp"}
_WRITE_ROLES_LIMITED = _WRITE_ROLES_FULL | {"biz_owner"}


def _check_file_write_permission(role: str, file_path: str | None = None, is_script: bool = False) -> None:
    """统一文件写权限校验。不满足则抛 AUTH_PERMISSION_DENIED。"""
    if role in _WRITE_ROLES_FULL:
        return  # 全权限角色，不限制
    if role not in _WRITE_ROLES_LIMITED:
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    # biz_owner 只能写白名单文件，不能写脚本
    if is_script:
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    if file_path is not None and file_path not in _BIZ_OWNER_WRITABLE:
        raise AppError("AUTH_PERMISSION_DENIED", 403)


async def _enforce_edit_lock(db: AsyncSession, skill_id: str, user_id: str) -> None:
    """服务端编辑锁强制检查：只有锁持有者才能保存。无锁则跳过（向后兼容）。"""
    lock = await service.get_lock_status(db, skill_id)
    if lock and lock.get("locked") and lock.get("locked_by") != user_id:
        raise AppError("SKILL_LOCKED", 423, {
            "detail": f"Skill 正在被 {lock.get('locked_by')} 编辑，请等待释放后再保存",
            "locked_by": lock.get("locked_by"),
        })


class SaveFileRequest(BaseModel):
    content: str


class RenameFileRequest(BaseModel):
    new_path: str


class CreateFileRequest(BaseModel):
    path: str
    content: str = ""
    is_dir: bool = False


@router.get("/{skill_id}/manifest")
async def get_skill_manifest(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "read")
    return data.get("manifest") or {}


@router.get("/{skill_id}/scripts")
async def list_skill_scripts(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "read")
    return {"items": service.list_scripts(skill_id)}


@router.post("/{skill_id}/scripts/run")
async def run_skill_script(
    skill_id: str,
    body: RunScriptRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "execute")
    timeout = max(1, min(int(body.timeout or 10), 30))
    return service.run_script(skill_id, body.path, payload=body.payload or {}, timeout=timeout)


@router.put("/{skill_id}/content")
async def save_content(
    skill_id: str,
    body: SaveContentRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    _check_file_write_permission(current_user.role, is_script=body.script_content is not None)

    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "edit")

    # 编辑锁检查：只有锁持有者才能保存
    await _enforce_edit_lock(db, skill_id, current_user.id)

    result = await service.save_skill_content(
        db,
        skill_id=skill_id,
        skill_md=body.skill_md,
        policy_pack_raw=body.policy_pack_raw,
        script_content=body.script_content,
        user_id=current_user.id,
    )
    if not result.get("noop"):
        try:
            await invalidate_skill(skill_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("缓存失效失败: {}", e)
        if result.get("skill_md_changed"):
            try:
                from app.skills.lifecycle.cross_skill_conflict import invalidate_conflict_cache
                from app.skills.intelligence.param_index import invalidate_param_index

                await invalidate_param_index()
                await invalidate_conflict_cache()
            except Exception as e:  # noqa: BLE001
                logger.warning("索引缓存失效失败: {}", e)
    return result


@router.get("/{skill_id}/files/{file_path:path}")
async def read_file(
    skill_id: str,
    file_path: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "read")
    from app.skills.core.git_service import git_service

    content = git_service.read_file(skill_id, file_path)
    if content is None:
        raise AppError("SKILL_FILE_NOT_FOUND", 404)
    return {"path": file_path, "content": content}


@router.put("/{skill_id}/files/{file_path:path}")
async def save_file(
    skill_id: str,
    file_path: str,
    body: SaveFileRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    _check_file_write_permission(current_user.role, file_path=file_path)

    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "edit")

    result = await service.save_file(db, skill_id, file_path, body.content, current_user.id)
    try:
        await invalidate_skill(skill_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存失效失败 (save_file {}): {}", file_path, e)
    return result


@router.post("/{skill_id}/files")
async def create_file(
    skill_id: str,
    body: CreateFileRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "edit")

    result = await service.create_file(db, skill_id, body.path, body.content, body.is_dir, current_user.id)
    try:
        await invalidate_skill(skill_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存失效失败 (create_file {}): {}", body.path, e)
    return result


@router.delete("/{skill_id}/files/{file_path:path}")
async def delete_file(
    skill_id: str,
    file_path: str,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    if file_path == "SKILL.md":
        raise AppError("PARAM_INVALID", 400, {"detail": "不能删除 SKILL.md"})
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "edit")

    result = await service.delete_file(db, skill_id, file_path, current_user.id)
    try:
        await invalidate_skill(skill_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存失效失败 (delete_file {}): {}", file_path, e)
    return result


@router.patch("/{skill_id}/files/{file_path:path}")
async def rename_file(
    skill_id: str,
    file_path: str,
    body: RenameFileRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "edit")

    result = await service.rename_file(db, skill_id, file_path, body.new_path, current_user.id)
    try:
        await invalidate_skill(skill_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存失效失败 (rename_file {} → {}): {}", file_path, body.new_path, e)
    return result


@router.put("/{skill_id}/structured")
async def save_structured(
    skill_id: str,
    body: SaveStructuredRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    # 部门权限隔离（与 save_content 一致）
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "edit")

    # 编辑锁检查
    await _enforce_edit_lock(db, skill_id, current_user.id)

    result = await service.save_skill_structured(
        db,
        skill_id=skill_id,
        frontmatter=body.frontmatter,
        purpose=body.purpose,
        steps=body.steps,
        antipatterns=body.antipatterns,
        output_definition=body.output_definition,
        data_inputs=body.data_inputs,
        test_cases=body.test_cases,
        custom_sections=body.custom_sections,
        policy_pack=body.policy_pack,
        user_id=current_user.id,
    )
    if not result.get("noop"):
        try:
            await invalidate_skill(skill_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("缓存失效失败: {}", e)
        if result.get("skill_md_changed"):
            try:
                from app.skills.lifecycle.cross_skill_conflict import invalidate_conflict_cache
                from app.skills.intelligence.param_index import invalidate_param_index

                await invalidate_param_index()
                await invalidate_conflict_cache()
            except Exception as e:  # noqa: BLE001
                logger.warning("索引缓存失效失败: {}", e)
    return result


@router.get("/{skill_id}/file-head/{file_path:path}")
async def get_file_at_head(
    skill_id: str,
    file_path: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "read")

    from app.skills.core.git_service import git_service

    content = git_service.get_file_at_commit(skill_id, file_path, "HEAD")
    if content is None:
        return {"content": "", "is_new": True, "commit": None}

    logs = git_service.log(skill_id=skill_id, max_count=1)
    return {
        "content": content,
        "is_new": False,
        "commit": logs[0]["hash"] if logs else None,
    }


# ---------- 编辑锁 ----------

@router.post("/{skill_id}/lock")
async def acquire_lock(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取编辑锁"""
    await ensure_skill_access(db, skill_id, current_user, "edit")
    return await service.acquire_lock(db, skill_id, current_user.id)


@router.delete("/{skill_id}/lock")
async def release_lock(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """释放编辑锁"""
    await ensure_skill_access(db, skill_id, current_user, "edit")
    return await service.release_lock(db, skill_id, current_user.id)


@router.get("/{skill_id}/lock")
async def lock_status(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """查询编辑锁状态"""
    await ensure_skill_access(db, skill_id, current_user, "read")
    lock = await service.get_lock_status(db, skill_id)
    return lock or {"skill_id": skill_id, "locked": False}


# ---------- 编辑感知协同 ----------

class ConflictCheckRequest(BaseModel):
    base_commit: str


@router.post("/{skill_id}/conflict-check")
async def check_save_conflict(
    skill_id: str,
    body: ConflictCheckRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """保存前冲突检测：比较 base_commit 是否仍是 HEAD"""
    await ensure_skill_access(db, skill_id, current_user, "edit")
    from app.skills.integrations.collab_ws import check_conflict
    return check_conflict(skill_id, body.base_commit)


@router.get("/{skill_id}/editors")
async def get_active_editors(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取当前正在编辑此 Skill 的用户列表"""
    await ensure_skill_access(db, skill_id, current_user, "read")
    from app.skills.integrations.collab_ws import get_active_editors
    return {"skill_id": skill_id, "editors": get_active_editors(skill_id)}


@router.websocket("/{skill_id}/collab")
async def collab_websocket(
    websocket: WebSocket,
    skill_id: str,
):
    """编辑感知协同 WebSocket。

    协议：
        客户端 → {"type": "editing", "module": "rules"}
        客户端 → {"type": "ping"}
        服务端 → {"type": "user_joined/left/editing/saved", ...}
        服务端 → {"type": "pong"}
    """
    await websocket.accept()
    user = await get_current_user_ws(websocket)
    if not user:
        await websocket.close(code=WS_CODE_AUTH_REQUIRED, reason="unauthorized")
        return

    from app.skills.integrations.collab_ws import connect, disconnect, notify_editing

    await connect(skill_id, user.id, websocket)
    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "editing":
                await notify_editing(skill_id, user.id, msg.get("module", ""))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("协同 WS 异常 skill={} user={}: {}", skill_id, user.id, e)
    finally:
        await disconnect(skill_id, user.id)
