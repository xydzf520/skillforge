"""
Prompt 管理 API（仅 admin 可访问）。

提供：
- GET  /api/admin/prompts          列出所有已注册 prompt（含元数据）
- GET  /api/admin/prompts/{name}   单个 prompt 的所有版本详情
- PUT  /api/admin/prompts/{name}/default  切换某 prompt 的默认版本

参考方案：docs/plans/aiclawcode-migration-plan.md §3.3
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.dependencies import require_role
from app.auth.models import User
from app.common.audit import audit
from app.common.exceptions import AppError
from app.common.prompt_registry import prompt_registry

router = APIRouter()


class SetDefaultRequest(BaseModel):
    version: str


@router.get("/prompts")
async def list_prompts(
    current_user: User = Depends(require_role("admin")),
):
    """列出所有已注册 prompt 的元数据"""
    return {"items": prompt_registry.list_all()}


@router.get("/prompts/{name}")
async def get_prompt_detail(
    name: str,
    current_user: User = Depends(require_role("admin")),
):
    """获取某个 prompt 所有版本的完整内容（管理界面渲染用）"""
    sections = prompt_registry._sections.get(name)
    if not sections:
        raise AppError("PROMPT_NOT_FOUND", 404, {"detail": f"prompt 未注册: {name}"})

    return {
        "name": name,
        "default_version": prompt_registry._default_versions.get(name, ""),
        "versions": [
            {
                "version": s.version,
                "hash": s.hash,
                "description": s.description,
                "priority": s.priority,
                "cached": s.cached,
                "has_gate": s.gate is not None,
                "content": s.content,
            }
            for s in sections
        ],
    }


@router.put("/prompts/{name}/default")
async def set_prompt_default(
    name: str,
    body: SetDefaultRequest,
    current_user: User = Depends(require_role("admin")),
):
    """切换某 prompt 的默认版本"""
    try:
        prompt_registry.set_default(name, body.version)
    except KeyError as e:
        raise AppError("PROMPT_NOT_FOUND", 404, {"detail": str(e)})

    # 审计日志
    section = prompt_registry.get_section(name, body.version)
    await audit.log(
        user_id=current_user.id,
        action="prompt.set_default",
        target_type="prompt",
        target_id=name,
        detail={"version": body.version},
        prompt_hash=section.hash if section else None,
    )

    return {
        "ok": True,
        "name": name,
        "default_version": body.version,
        "hash": section.hash if section else "",
    }
