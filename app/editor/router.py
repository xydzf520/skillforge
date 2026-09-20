"""
编辑器 API 路由

提供 AI 补全端点和配置查询端点。
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.database import get_db
from app.editor.service import generate_completion, get_editor_config

router = APIRouter()


class CompletionResponse(BaseModel):
    """AI 补全响应"""
    completion: str | None = None
    error: str | None = None


@router.post("/completions", response_model=CompletionResponse)
async def editor_completions(
    request: Request,
    current_user: User = Depends(require_role("admin", "ai_engineer", "biz_owner")),
    db: AsyncSession = Depends(get_db),
):
    """
    monacopilot AI 补全端点

    monacopilot 发送的格式是扁平结构：
    { completionMetadata: {...}, textBeforeCursor: "...", textAfterCursor: "..." }
    """
    body = await request.json()

    # monacopilot 把所有数据都放在 completionMetadata 内：
    # { completionMetadata: { filename, language, textBeforeCursor, textAfterCursor, ... } }
    # 也可能在顶层（curl 手动测试时）
    metadata = body.get("completionMetadata", {})

    filename = metadata.get("filename", body.get("filename", "SKILL.md"))
    language = metadata.get("language", body.get("language", "skill-md"))
    text_before = metadata.get("textBeforeCursor", body.get("textBeforeCursor", ""))
    text_after = metadata.get("textAfterCursor", body.get("textAfterCursor", ""))

    # 截断过长文本
    lines_before = text_before.split("\n")
    lines_after = text_after.split("\n")
    text_before = "\n".join(lines_before[-200:])
    text_after = "\n".join(lines_after[:50])

    completion = await generate_completion(db, filename, language, text_before, text_after)
    return CompletionResponse(completion=completion if completion else None)


class InlineEditRequest(BaseModel):
    mode: str = "inline_edit"
    skill_id: str = ""
    selected_text: str
    instruction: str
    context: str = ""


@router.post("/inline-edit")
async def inline_edit(
    body: InlineEditRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """C4: 内联 AI 编辑 — 选中文本 + 修改指令 → AI 生成修改"""
    from app.common.ai import call_llm

    system = (
        "你是SKILL.md编辑助手。用户选中了一段文本并给出修改指令。"
        "输出纯JSON: {\"modified\": \"修改后文本\", \"explanation\": \"简要说明\"}"
    )
    user = (
        f"## 选中文本\n```\n{body.selected_text}\n```\n\n"
        f"## 修改指令\n{body.instruction}\n\n"
        f"## 上下文\n```\n{body.context[:1500]}\n```\n\n"
        "按指令修改选中文本，保持周围格式一致。"
    )
    result = await call_llm(system, user, max_tokens=1000, timeout=20)
    if not result:
        return {"modified": body.selected_text, "explanation": "AI 暂时不可用"}
    return result


@router.get("/config")
async def editor_config(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取编辑器前端配置（非敏感项）"""
    config = await get_editor_config(db)
    return {
        "ai_enabled": config.get("editor.ai_enabled", True),
        "trigger": config.get("editor.trigger", "onIdle"),
        "model": config.get("editor.model", ""),
    }
