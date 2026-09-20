"""Authenticated native runtime routes for the Hall AI Chat Skill."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_state_active
from app.auth.models import User
from app.database import get_db
from app.hall import ai_chat_service as service


router = APIRouter()


class CreateThreadRequest(BaseModel):
    title: str = Field(default="", max_length=160)
    model: str = Field(default=service.DEFAULT_MODEL, max_length=80)


class UpdateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    archived: bool | None = None
    default_model: str | None = Field(default=None, max_length=80)


class SendMessageRequest(BaseModel):
    content: str = Field(default="", max_length=service.MAX_MESSAGE_CHARS)
    model: str = Field(max_length=80)
    reasoning_effort: str = Field(default="auto", max_length=16)
    attachment_ids: list[str] = Field(default_factory=list, max_length=service.MAX_IMAGES_PER_MESSAGE)
    idempotency_key: str = Field(min_length=8, max_length=128)


class RegenerateMessageRequest(BaseModel):
    model: str = Field(max_length=80)
    reasoning_effort: str = Field(default="auto", max_length=16)


class UpdatePreferenceRequest(BaseModel):
    last_model: str = Field(max_length=80)


@router.get("/models")
async def ai_chat_models(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    catalog = dict(await service.get_model_catalog())
    preference = await service.get_user_preference(db, user_id=current_user.id)
    available = {
        item["id"]
        for group in catalog.get("groups", [])
        for item in group.get("models", [])
        if item.get("available")
    }
    preferred = preference["last_model"]
    catalog["preferred_model"] = preferred if preferred in available else catalog.get("default_model")
    return catalog


@router.patch("/preferences")
async def update_ai_chat_preference(
    body: UpdatePreferenceRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.update_user_preference(db, user_id=current_user.id, model=body.last_model)


@router.get("/threads")
async def ai_chat_threads(
    archived: bool = Query(False),
    q: str = Query("", max_length=100),
    cursor: str | None = Query(None),
    limit: int = Query(service.THREAD_PAGE_SIZE, ge=1, le=60),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_threads(
        db,
        user_id=current_user.id,
        archived=archived,
        q=q,
        cursor=cursor,
        limit=limit,
    )


@router.post("/threads")
async def create_ai_chat_thread(
    body: CreateThreadRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.create_thread(
        db,
        user_id=current_user.id,
        title=body.title,
        model=body.model,
    )


@router.patch("/threads/{thread_id}")
async def update_ai_chat_thread(
    thread_id: str,
    body: UpdateThreadRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.update_thread(
        db,
        user_id=current_user.id,
        thread_id=thread_id,
        title=body.title,
        archived=body.archived,
        default_model=body.default_model,
    )


@router.delete("/threads/{thread_id}")
async def delete_ai_chat_thread(
    thread_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.delete_thread(db, user_id=current_user.id, thread_id=thread_id)


@router.get("/threads/{thread_id}/messages")
async def ai_chat_messages(
    thread_id: str,
    cursor: str | None = Query(None),
    limit: int = Query(service.MESSAGE_PAGE_SIZE, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_messages(
        db,
        user_id=current_user.id,
        thread_id=thread_id,
        cursor=cursor,
        limit=limit,
    )


@router.post("/attachments")
async def upload_ai_chat_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.save_attachment(db, user_id=current_user.id, file=file)


@router.get("/attachments/{attachment_id}/content")
async def read_ai_chat_attachment(
    attachment_id: str,
    thumbnail: bool = Query(False),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    path, row, media_type = await service.attachment_path(
        db,
        user_id=current_user.id,
        attachment_id=attachment_id,
        thumbnail=thumbnail,
    )
    return FileResponse(
        path,
        filename=row.original_name,
        media_type=media_type,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/threads/{thread_id}/messages/stream")
async def stream_ai_chat_message(
    thread_id: str,
    body: SendMessageRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    user_message, assistant, duplicate = await service.prepare_turn(
        db,
        user_id=current_user.id,
        thread_id=thread_id,
        content=body.content,
        model=body.model,
        reasoning_effort=body.reasoning_effort,
        attachment_ids=body.attachment_ids,
        idempotency_key=body.idempotency_key,
    )
    if duplicate:
        if assistant.status not in {"completed", "interrupted", "failed"}:
            raise HTTPException(status_code=409, detail="该消息仍在生成，请稍后刷新")
        lease = "none"
    else:
        try:
            lease = await service.acquire_stream_lease(current_user.id, assistant.id)
        except HTTPException as exc:
            await service.fail_stream_admission(
                db,
                message_id=assistant.id,
                error_code="concurrency_limit" if exc.status_code == 429 else "stream_admission_failed",
                error_message=str(exc.detail),
            )
            raise
    return StreamingResponse(
        service.stream_turn(
            user_id=current_user.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant.id,
            duplicate=duplicate,
            lease_backend=lease,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/messages/{message_id}/cancel")
async def cancel_ai_chat_message(
    message_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.cancel_message(db, user_id=current_user.id, message_id=message_id)


@router.post("/messages/{message_id}/regenerate")
async def regenerate_ai_chat_message(
    message_id: str,
    body: RegenerateMessageRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    user_message, assistant = await service.prepare_regeneration(
        db,
        user_id=current_user.id,
        assistant_message_id=message_id,
        model=body.model,
        reasoning_effort=body.reasoning_effort,
    )
    try:
        lease = await service.acquire_stream_lease(current_user.id, assistant.id)
    except HTTPException as exc:
        await service.fail_stream_admission(
            db,
            message_id=assistant.id,
            error_code="concurrency_limit" if exc.status_code == 429 else "stream_admission_failed",
            error_message=str(exc.detail),
        )
        raise
    return StreamingResponse(
        service.stream_turn(
            user_id=current_user.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant.id,
            duplicate=False,
            lease_backend=lease,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
