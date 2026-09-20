"""Project hosting API.

These endpoints let SkillForge open many lightweight department projects without
spawning a Docker container per project. Browser projects call back through this
gateway so outputs, AI analysis state, reports and todos are recorded.
"""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_state_active, require_state_active_web_or_cli
from app.auth.models import User
from app.common.exceptions import AppError
from app.database import async_session_factory, get_db
from app.projects import service

router = APIRouter()


class ProjectCreateRequest(BaseModel):
    id: str | None = Field(default=None, max_length=42)
    name: str = Field(..., min_length=1, max_length=160)
    description: str | None = None
    type: str = "external_web"
    department_id: str | None = None
    department: str | None = None
    visibility: str = "department"
    status: str = "published"
    entry_url: str | None = None
    version: str | None = None
    sf_package_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    version_metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProjectRunCreateRequest(BaseModel):
    request_id: str | None = Field(default=None, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    parent_run_id: str | None = None

    model_config = {"extra": "allow"}


class ProjectAutoRegisterRequest(BaseModel):
    manifest: dict[str, Any] = Field(default_factory=dict)
    package_hash: str | None = None
    source: str | None = None

    model_config = {"extra": "allow"}


class ProjectRunHeartbeatRequest(BaseModel):
    status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProjectRunCloseRequest(BaseModel):
    request_id: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProjectIngestRequest(BaseModel):
    request_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    reports: list[dict[str, Any]] = Field(default_factory=list)
    todos: list[dict[str, Any]] = Field(default_factory=list)
    proofs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str = "completed"
    event_type: str = "output"
    auto_analyze: bool = True
    analysis_prompt: str | None = None

    model_config = {"extra": "allow"}


class ProjectInputRequest(BaseModel):
    request_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    replace: bool = False

    model_config = {"extra": "allow"}


class ProjectAnalyzeRequest(BaseModel):
    prompt: str | None = None
    request_id: str | None = Field(default=None, max_length=80)

    model_config = {"extra": "allow"}


class ProjectCapabilityRequest(BaseModel):
    capability: str | None = None
    capability_key: str | None = None
    key: str | None = None
    type: str | None = None
    request_id: str | None = None
    prompt: str | None = None
    messages: list[Any] | None = None
    model: str | None = Field(default=None, max_length=180)
    model_id: str | None = Field(default=None, max_length=180)
    target_gateway_id: str | None = Field(default=None, max_length=80)
    gateway_id: str | None = Field(default=None, max_length=80)
    json_mode: bool | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=16384)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=16384)
    max_output_tokens: int | None = Field(default=None, ge=1, le=16384)
    timeout_seconds: int | None = Field(default=None, ge=30, le=3600)
    temperature: float | None = Field(default=None, ge=0, le=2)
    input: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProjectSdkTokenCreateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    scopes: list[str] = Field(default_factory=list)
    expires_in_days: int | None = Field(default=None, ge=1, le=3660)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProjectSdkRunCreateRequest(BaseModel):
    project_id: str | None = Field(default=None, max_length=42)
    request_id: str | None = Field(default=None, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    parent_run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProjectSdkTrainingSyncRequest(BaseModel):
    request_id: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProjectSdkTrainingSampleRequest(BaseModel):
    request_id: str | None = Field(default=None, max_length=80)
    asset_id: str | None = Field(default=None, max_length=50)
    project_run_asset_id: str | None = Field(default=None, max_length=50)
    dataset_profile: str = Field(default="text_sft_v1", max_length=50)
    modality: str | None = Field(default=None, max_length=30)
    content: dict[str, Any] = Field(default_factory=dict)
    media_refs: list[dict[str, Any]] = Field(default_factory=list)
    labels: list[Any] = Field(default_factory=list)
    quality_score: float | None = Field(default=0.0, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProjectSdkTrainingDatasetVersionRequest(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    version: str | None = Field(default=None, max_length=60)
    dataset_profile: str = Field(default="text_sft_v1", max_length=50)
    modality: str | None = Field(default=None, max_length=30)
    target_model_family: str | None = Field(default=None, max_length=120)
    sample_ids: list[str] | None = Field(default=None, max_length=5000)
    limit: int | None = Field(default=None, ge=1, le=5000)
    status: str | None = Field(default="ready", max_length=30)
    storage_location_id: str | None = Field(default=None, max_length=50)
    target_gateway_id: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProjectSdkTrainingDatasetSyncRequest(BaseModel):
    target_gateway_id: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProjectServiceInvokeRequest(BaseModel):
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


async def _project_sdk_principal(
    request: Request,
    db: AsyncSession,
    *,
    required_scope: str,
    project_id: str | None = None,
) -> service.ProjectSdkPrincipal:
    authorization = str(request.headers.get("authorization") or "").strip()
    if not authorization.lower().startswith("bearer "):
        raise AppError("PROJECT_SDK_AUTH_REQUIRED", 401)
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise AppError("PROJECT_SDK_AUTH_REQUIRED", 401)
    return await service.authenticate_project_sdk_token(
        db,
        token,
        required_scope=required_scope,
        project_id=project_id,
    )


async def _project_openai_actor(
    request: Request,
    db: AsyncSession,
    *,
    required_scope: str = "runs:capability",
) -> User | service.ProjectSdkPrincipal:
    authorization = str(request.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token.startswith(f"{service.PROJECT_SDK_TOKEN_PREFIX}_"):
            return await service.authenticate_project_sdk_token(db, token, required_scope=required_scope)
    return await require_state_active_web_or_cli(request, db)


@router.get("/")
async def list_projects(
    scope: str | None = Query(default=None, description="all/mine/department/company/playbook 或项目 type"),
    include_playbooks: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=60, ge=1, le=100),
    search: str | None = Query(default=None, max_length=120),
    run_status: str | None = Query(default=None, description="all/running/stale/waiting_ai/ai_completed/failed"),
    department_id: str | None = Query(default=None, max_length=50),
    department: str | None = Query(default=None, max_length=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_projects(
        db,
        current_user,
        scope=scope,
        include_playbooks=include_playbooks,
        page=page,
        page_size=page_size,
        search=search,
        run_status=run_status,
        department_id=department_id,
        department=department,
    )


@router.post("/")
async def create_project(
    body: ProjectCreateRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.create_project(db, current_user, body.model_dump())


@router.post("/auto-register")
async def auto_register_project(
    body: ProjectAutoRegisterRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.auto_register_project(db, current_user, body.model_dump())


@router.get("/models/236")
async def get_project_236_models(
    current_user: User = Depends(require_state_active_web_or_cli),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_project_236_model_catalog(db, current_user)


@router.get("/openai/236/v1/models")
async def get_project_openai_236_models(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    actor = await _project_openai_actor(request, db, required_scope="runs:capability")
    return await service.project_openai_236_models(db, actor)


@router.post("/openai/236/v1/chat/completions")
async def post_project_openai_236_chat_completions(
    request: Request,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(request, db, required_scope="runs:capability")
    result = await service.project_openai_236_chat_completion(db, principal, body)
    if body.get("stream") is True:
        response_id = result.get("id")
        created = result.get("created")
        model = result.get("model")
        choice = (result.get("choices") or [{}])[0]
        message = choice.get("message") if isinstance(choice, dict) else {}
        text = str((message or {}).get("content") or "")
        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else "stop"

        async def event_stream():
            first = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
            }
            final = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason or "stop"}],
            }
            yield "data: " + json.dumps(first, ensure_ascii=False, separators=(",", ":")) + "\n\n"
            yield "data: " + json.dumps(final, ensure_ascii=False, separators=(",", ":")) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")
    return result


@router.post("/{project_id}/tokens")
async def create_project_sdk_token(
    project_id: str,
    body: ProjectSdkTokenCreateRequest | None = None,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.create_project_sdk_token(db, current_user, project_id, body.model_dump() if body else {})


@router.get("/{project_id}/tokens")
async def list_project_sdk_tokens(
    project_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_project_sdk_tokens(db, current_user, project_id)


@router.delete("/{project_id}/tokens/{token_id}")
async def revoke_project_sdk_token(
    project_id: str,
    token_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.revoke_project_sdk_token(db, current_user, project_id, token_id)


@router.post("/upload")
async def upload_project_package(
    manifest_json: str = Form(default=""),
    package_hash: str = Form(default=""),
    sf_auto_build_json: str = Form(default=""),
    package: UploadFile = File(...),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    package_bytes = await package.read()
    sf_auto_build = service.parse_project_form_json_dict(sf_auto_build_json, field="sf_auto_build_json")
    return await service.upload_project_package(
        db,
        current_user,
        package_bytes=package_bytes,
        manifest_raw=manifest_json,
        package_hash=package_hash,
        submit_metadata={"sf_auto_build": sf_auto_build} if sf_auto_build else None,
    )


@router.post("/runs/reconcile-stale")
async def reconcile_stale_project_runs(
    stale_after_seconds: int = Query(default=service.PROJECT_HEARTBEAT_STALE_SECONDS, ge=30, le=86400),
    limit: int = Query(default=service.PROJECT_STALE_RECONCILE_LIMIT, ge=1, le=2000),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.reconcile_stale_project_runs(
        db,
        current_user,
        stale_after_seconds=stale_after_seconds,
        limit=limit,
    )


@router.get("/runtime/status")
async def get_project_runtime_status(
    stale_after_seconds: int = Query(default=service.PROJECT_HEARTBEAT_STALE_SECONDS, ge=30, le=86400),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_project_runtime_status(
        db,
        current_user,
        stale_after_seconds=stale_after_seconds,
    )


@router.post("/sdk/check/project")
async def ensure_project_sdk_check_project(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.ensure_company_sdk_project(db, current_user)


@router.get("/sdk/check/runs/{project_run_id}")
async def get_project_sdk_check_run_summary(
    project_run_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_project_sdk_check_run_summary(db, current_user, project_run_id)


@router.post("/sdk/runs")
async def sdk_create_project_run(
    request: Request,
    body: ProjectSdkRunCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(
        request,
        db,
        required_scope="runs:create",
        project_id=body.project_id,
    )
    return await service.sdk_create_project_run(db, principal, body.model_dump())


@router.post("/sdk/runs/{project_run_id}/input")
async def sdk_record_project_run_input(
    project_run_id: str,
    request: Request,
    body: ProjectInputRequest,
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(request, db, required_scope="runs:input")
    return await service.sdk_record_project_input(db, principal, project_run_id, body.model_dump())


@router.post("/sdk/runs/{project_run_id}/capability")
async def sdk_call_project_capability(
    project_run_id: str,
    request: Request,
    body: ProjectCapabilityRequest,
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(request, db, required_scope="runs:capability")
    return await service.sdk_call_project_capability(db, principal, project_run_id, body.model_dump())


@router.post("/sdk/runs/{project_run_id}/ingest")
async def sdk_ingest_project_run(
    project_run_id: str,
    request: Request,
    body: ProjectIngestRequest,
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(request, db, required_scope="runs:ingest")
    return await service.sdk_ingest_project_output(db, principal, project_run_id, body.model_dump())


@router.get("/sdk/runs/{project_run_id}/trace")
async def sdk_get_project_run_trace(
    project_run_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ingress_cursor: str | None = Query(default=None, max_length=300),
    capability_cursor: str | None = Query(default=None, max_length=300),
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(request, db, required_scope="runs:trace")
    return await service.sdk_get_project_run_trace(
        db,
        principal,
        project_run_id,
        limit=limit,
        offset=offset,
        ingress_cursor=ingress_cursor,
        capability_cursor=capability_cursor,
    )


@router.post("/sdk/runs/{project_run_id}/training-sync")
async def sdk_sync_project_run_training_sink(
    project_run_id: str,
    request: Request,
    body: ProjectSdkTrainingSyncRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(request, db, required_scope="training:sync")
    return await service.sdk_sync_project_run_training_sink(
        db,
        principal,
        project_run_id,
        body.model_dump() if body else {},
    )


@router.post("/sdk/runs/{project_run_id}/assets")
async def sdk_upload_project_run_asset(
    project_run_id: str,
    request: Request,
    file: UploadFile = File(...),
    metadata_json: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(request, db, required_scope="runs:assets")
    metadata: dict[str, Any] = {}
    if metadata_json:
        import json

        try:
            parsed = json.loads(metadata_json)
            metadata = parsed if isinstance(parsed, dict) else {}
        except Exception:
            metadata = {"raw_metadata_json": metadata_json[:1000]}
    content = await file.read()
    return await service.sdk_upload_project_run_asset(
        db,
        principal,
        project_run_id,
        file_name=file.filename or "asset",
        mime_type=file.content_type,
        content=content,
        metadata=metadata,
    )


@router.get("/sdk/training/assets")
async def sdk_list_trainable_assets(
    request: Request,
    limit: int = Query(default=200, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(request, db, required_scope="training:assets")
    return await service.sdk_list_trainable_assets(db, principal, limit=limit)


@router.post("/sdk/runs/{project_run_id}/training-samples")
async def sdk_record_training_sample(
    project_run_id: str,
    request: Request,
    body: ProjectSdkTrainingSampleRequest,
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(request, db, required_scope="training:samples")
    return await service.sdk_record_training_sample(db, principal, project_run_id, body.model_dump())


@router.post("/sdk/training/datasets")
async def sdk_create_training_dataset_version(
    request: Request,
    body: ProjectSdkTrainingDatasetVersionRequest,
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(request, db, required_scope="training:datasets:create")
    return await service.sdk_create_training_dataset_version(db, principal, body.model_dump())


@router.post("/sdk/training/datasets/{dataset_version_id}/sync")
async def sdk_sync_training_dataset_version(
    dataset_version_id: str,
    request: Request,
    body: ProjectSdkTrainingDatasetSyncRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    principal = await _project_sdk_principal(request, db, required_scope="training:datasets:sync")
    return await service.sdk_sync_training_dataset_version(
        db,
        principal,
        dataset_version_id,
        body.model_dump() if body else {},
    )


@router.get("/runs/{project_run_id}")
async def get_project_run(
    project_run_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_project_run(db, current_user, project_run_id)


@router.get("/runs/{project_run_id}/trace")
async def get_project_run_trace(
    project_run_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ingress_cursor: str | None = Query(default=None, max_length=300),
    capability_cursor: str | None = Query(default=None, max_length=300),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_project_run_trace(
        db,
        current_user,
        project_run_id,
        limit=limit,
        offset=offset,
        ingress_cursor=ingress_cursor,
        capability_cursor=capability_cursor,
    )


@router.post("/runs/{project_run_id}/ingest")
async def ingest_project_run(
    project_run_id: str,
    body: ProjectIngestRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data = body.model_dump()
    return await service.ingest_project_output(db, current_user, project_run_id, data, auto_analyze=body.auto_analyze)


@router.post("/runs/{project_run_id}/input")
async def record_project_run_input(
    project_run_id: str,
    body: ProjectInputRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.record_project_input_event(db, current_user, project_run_id, body.model_dump())


@router.post("/runs/{project_run_id}/heartbeat")
async def heartbeat_project_run(
    project_run_id: str,
    body: ProjectRunHeartbeatRequest | None = None,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.heartbeat_project_run(db, current_user, project_run_id, body.model_dump() if body else {})


@router.post("/runs/{project_run_id}/close")
async def close_project_run(
    project_run_id: str,
    body: ProjectRunCloseRequest | None = None,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.close_project_run(db, current_user, project_run_id, body.model_dump() if body else {})


@router.post("/runs/{project_run_id}/analyze")
async def analyze_project_run(
    project_run_id: str,
    body: ProjectAnalyzeRequest | None = None,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.analyze_project_run(
        db,
        current_user,
        project_run_id,
        prompt=body.prompt if body else None,
        request_id=body.request_id if body else None,
    )


@router.post("/runs/{project_run_id}/capability")
async def call_project_capability(
    project_run_id: str,
    body: ProjectCapabilityRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.call_project_capability(db, current_user, project_run_id, body.model_dump())


@router.get("/runs/{project_run_id}/media/events")
async def stream_project_media_events(
    project_run_id: str,
    request: Request,
    after_cursor: str | None = Query(default=None, max_length=200),
    current_user: User = Depends(require_state_active),
):
    """Stream governed workbench snapshots without consuming capability quota."""

    async with async_session_factory() as initial_session:
        run, project = await service._require_run(initial_session, current_user, project_run_id, require_write=False)
        snapshot_capability = (
            "material.workbench.snapshot"
            if service._capability_allowed(project, "material.workbench.snapshot")
            else "video.workbench.snapshot"
        )
        if not service._capability_allowed(project, snapshot_capability):
            raise AppError("PROJECT_CAPABILITY_DENIED", 403, {"capability": snapshot_capability})
        user_id = str(current_user.id)
        project_id = str(project.id)
        run_model = type(run)
        project_model = type(project)

    async def event_stream():
        from app.common.audit import audit
        from app.common.time_utils import isoformat_bjt, now_bjt
        from app.media.models import MediaWorkbenchStreamEvent
        from app.media.fde_v4 import fde_workbench_snapshot
        from app.media.workbench_v2 import workbench_snapshot

        resume_cursor = after_cursor or request.headers.get("last-event-id")
        try:
            cursor = max(0, int(str(resume_cursor or "0")))
        except ValueError:
            cursor = 0
        resume_requested = bool(resume_cursor)
        first_pass = True
        await audit.log(user_id, "project.media_stream.connect", "project_run", project_run_id, {"project_id": project_id})
        try:
            while not await request.is_disconnected():
                async with async_session_factory() as session:
                    live_run = await session.get(run_model, project_run_id)
                    live_project = await session.get(project_model, project_id)
                    live_user = await session.get(User, user_id)
                    if live_run is None or live_project is None or live_user is None:
                        yield "event: error\ndata: {\"code\":\"PROJECT_RUN_NOT_FOUND\"}\n\n"
                        return
                    if snapshot_capability == "material.workbench.snapshot":
                        snapshot = await fde_workbench_snapshot(
                            session, live_user, live_project, live_run,
                            {"limit": 60, "include_recent_jobs": True},
                        )
                    else:
                        snapshot = await workbench_snapshot(
                            session,
                            live_project,
                            live_run,
                            {"limit": 60, "include_recent_jobs": True},
                        )
                    await session.commit()
                    latest_cursor = int(snapshot.get("cursor") or 0)
                    if first_pass and (not resume_requested or cursor > latest_cursor):
                        cursor = max(0, latest_cursor - 1)
                    rows = (
                        await session.execute(
                            select(MediaWorkbenchStreamEvent)
                            .where(
                                MediaWorkbenchStreamEvent.project_run_id == project_run_id,
                                MediaWorkbenchStreamEvent.id > cursor,
                            )
                            .order_by(MediaWorkbenchStreamEvent.id.asc())
                            .limit(100)
                        )
                    ).scalars().all()
                first_pass = False
                if not rows:
                    yield f"event: heartbeat\ndata: {json.dumps({'cursor': str(cursor)}, ensure_ascii=False)}\n\n"
                for row in rows:
                    cursor = int(row.id)
                    event_snapshot = {**(row.snapshot_json or {}), "server_time": isoformat_bjt(now_bjt())}
                    payload = json.dumps({"type": "snapshot", "cursor": str(cursor), "data": event_snapshot}, ensure_ascii=False, default=str)
                    yield f"id: {cursor}\nevent: snapshot\ndata: {payload}\n\n"
                    event_groups = {
                        "job.updated": event_snapshot.get("jobs") or [],
                        "batch.updated": event_snapshot.get("batches") or [],
                        "continuation.updated": event_snapshot.get("continuations") or [],
                        "strategy.updated": event_snapshot.get("strategies") or [],
                        "replay.updated": event_snapshot.get("replays") or [],
                        "node.updated": event_snapshot.get("nodes") or [],
                        "review.updated": event_snapshot.get("review_counts") or {},
                        "training.updated": event_snapshot.get("training_status") or {},
                        "request.updated": event_snapshot.get("request_counts") or {},
                        "candidate.updated": event_snapshot.get("candidate_counts") or {},
                        "decision.updated": event_snapshot.get("candidate_counts") or {},
                        "delivery.updated": event_snapshot.get("candidate_counts") or {},
                        "performance.updated": event_snapshot.get("north_star") or {},
                    }
                    for event_name in row.changed_events_json or []:
                        if event_name not in event_groups:
                            continue
                        event_payload = json.dumps(
                            {"type": event_name, "cursor": str(cursor), "data": event_groups[event_name]},
                            ensure_ascii=False,
                            default=str,
                        )
                        yield f"id: {cursor}\nevent: {event_name}\ndata: {event_payload}\n\n"
                await asyncio.sleep(5)
        finally:
            try:
                await audit.log(user_id, "project.media_stream.disconnect", "project_run", project_run_id, {"project_id": project_id, "cursor": str(cursor)})
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{project_run_id}/assets")
async def upload_project_run_asset(
    project_run_id: str,
    file: UploadFile = File(...),
    metadata_json: str | None = Form(default=None),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    metadata: dict[str, Any] = {}
    if metadata_json:
        import json

        try:
            parsed = json.loads(metadata_json)
            metadata = parsed if isinstance(parsed, dict) else {}
        except Exception:
            metadata = {"raw_metadata_json": metadata_json[:1000]}
    return await service.upload_project_run_asset_stream(
        db,
        current_user,
        project_run_id,
        file_name=file.filename or "asset",
        mime_type=file.content_type,
        stream=file.file,
        metadata=metadata,
    )


@router.get("/runs/{project_run_id}/assets")
async def list_project_run_assets(
    project_run_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_project_run_assets(db, current_user, project_run_id)


@router.get("/runs/{project_run_id}/assets/{asset_id}/download")
async def download_project_run_asset(
    project_run_id: str,
    asset_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    path, asset = await service.project_run_asset_path(db, current_user, project_run_id, asset_id)
    return FileResponse(
        path,
        filename=asset.file_name,
        media_type=asset.mime_type or "application/octet-stream",
        headers=service.project_run_asset_response_headers(asset),
        content_disposition_type="inline",
    )


@router.get("/runs/{project_run_id}/assets/{asset_id}/public-download", include_in_schema=False)
async def public_download_project_run_asset(
    project_run_id: str,
    asset_id: str,
    expires: int = Query(..., ge=1),
    token: str = Query(..., min_length=16, max_length=200),
    db: AsyncSession = Depends(get_db),
):
    path, asset = await service.public_project_run_asset_path(
        db,
        project_run_id,
        asset_id,
        expires=expires,
        token=token,
    )
    return FileResponse(
        path,
        filename=asset.file_name,
        media_type=asset.mime_type or "application/octet-stream",
        headers=service.project_run_asset_response_headers(asset),
        content_disposition_type="inline",
    )


@router.get("/assets/{project_id}/{version_token}/{asset_path:path}", include_in_schema=False)
async def get_project_asset(
    project_id: str,
    version_token: str,
    asset_path: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    dynamic_payload = await service.project_dynamic_asset_payload(db, current_user, project_id, version_token, asset_path)
    if dynamic_payload is not None:
        return JSONResponse(dynamic_payload, headers={"Cache-Control": "no-store"})
    path = await service.project_asset_path(db, current_user, project_id, version_token, asset_path)
    return FileResponse(path, headers=service.project_asset_response_headers(path))


@router.get("/{project_id}/service")
async def get_project_service_contract(
    project_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_project_service_contract(db, current_user, project_id)


@router.post("/{project_id}/service/invoke")
async def invoke_project_service(
    project_id: str,
    body: ProjectServiceInvokeRequest | None = None,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.invoke_project_service(db, current_user, project_id, body.model_dump() if body else {})


@router.get("/{project_id}/runtime-evaluation")
async def get_project_runtime_evaluation(
    project_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_project_runtime_evaluation(db, current_user, project_id)


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_project(db, current_user, project_id)


@router.post("/{project_id}/runs")
async def create_project_run(
    project_id: str,
    body: ProjectRunCreateRequest | None = None,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.create_project_run(db, current_user, project_id, body.model_dump() if body else {})
