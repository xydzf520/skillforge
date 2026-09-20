"""训练控制面 API。"""

import json

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_state_active_web_or_cli
from app.auth.models import User
from app.common.cache import cached, invalidate_training_cache
from app.config import settings
from app.database import get_db

from .service import (
    activate_training_deployment,
    apply_training_gateway_result,
    approve_training_deployment,
    approve_training_job,
    auto_advance_training_job,
    auto_run_training_dataset_version,
    bootstrap_training_gateway_env,
    cancel_training_job,
    cancel_training_model_transfer,
    collect_due_training_job_results,
    collect_training_job_result,
    configure_training_gateway_runtime,
    chat_full_history_finetuned_model,
    chat_training_deployment_model,
    create_training_candidate_from_run,
    create_training_candidate_from_skill,
    create_training_dataset_version,
    create_training_job,
    discover_training_gateway_python_envs,
    dispatch_training_job,
    evaluate_training_job,
    force_cancel_training_job,
    discover_training_models_across_gateways,
    discover_training_gateway_models,
    get_training_automation_status,
    get_full_history_finetune_status,
    get_training_gateway_openwebui_status,
    get_training_gateway_bootstrap_status,
    get_training_gateway_model_status,
    get_training_model_transfer_status,
    get_training_candidate_readiness,
    get_training_deployment,
    get_training_dataset_version,
    get_training_dataset_version_automation,
    get_training_job_artifacts,
    download_training_job_artifact,
    get_training_job,
    get_training_job_logs,
    list_training_deployments,
    list_training_asset_sources,
    list_training_datasets,
    list_training_dataset_versions,
    list_training_jobs,
    list_training_samples,
    list_training_resources,
    kickoff_training_model_transfer,
    prepare_training_gateway_model,
    proxy_training_gateway_openwebui_chat_completion,
    proxy_training_gateway_openwebui_models,
    register_training_gateway_openwebui_base_model,
    reject_training_deployment,
    request_training_deployment,
    retry_training_job,
    rollback_training_deployment,
    record_training_sample,
    register_training_asset_source,
    start_training_model_transfer,
    sync_training_dataset_version,
    sync_training_deployment_openwebui,
    run_training_automation_once,
    run_full_history_finetune_cycles,
    simulate_training_job_result,
    training_dataset_version_manifest,
    test_training_gateway_openwebui_chat,
    test_training_gateway_model,
    transfer_training_model_to_training_gateway,
)

router = APIRouter()

_CACHE_KEY_EXCLUDES = frozenset({"db", "session", "request", "background_tasks", "current_user"})
_CACHE_SCOPE = ("current_user.id", "current_user.role", "current_user.department", "current_user.can_view_all")


def _id_cache_key(name: str):
    def _builder(*args, **kwargs) -> str:
        return str(kwargs.get(name) or (args[0] if args else ""))

    return _builder


class TrainingJobCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    department: str | None = Field(default=None, max_length=50)
    job_type: str = Field(default="lora", max_length=30)
    training_strategy: str | None = Field(default=None, max_length=80)
    target_skill_id: str | None = Field(default=None, max_length=50)
    target_gateway_id: str | None = Field(default=None, max_length=50)
    dataset_ref: str | None = Field(default=None, max_length=200)
    dataset_version_id: str | None = Field(default=None, max_length=50)
    objective: str | None = Field(default=None, max_length=4000)
    risk_level: str | None = Field(default=None, max_length=20)
    spec: dict | None = None


class TrainingAssetSourceRequest(BaseModel):
    source_type: str = Field(..., max_length=40)
    source_id: str = Field(..., max_length=120)
    title: str | None = Field(default=None, max_length=240)
    department: str | None = Field(default=None, max_length=100)
    org_unit_id: str | None = Field(default=None, max_length=50)
    owner_user_id: str | None = Field(default=None, max_length=50)
    skill_id: str | None = Field(default=None, max_length=100)
    project_id: str | None = Field(default=None, max_length=42)
    project_run_id: str | None = Field(default=None, max_length=50)
    modality: str | None = Field(default=None, max_length=30)
    storage_location_id: str | None = Field(default=None, max_length=50)
    storage_backend: str | None = Field(default=None, max_length=40)
    storage_ref: str | None = Field(default=None, max_length=1000)
    mime_type: str | None = Field(default=None, max_length=120)
    byte_size: int | None = Field(default=0, ge=0)
    sha256: str | None = Field(default=None, max_length=64)
    sensitivity_level: str | None = Field(default="internal", max_length=20)
    status: str | None = Field(default="active", max_length=30)
    policy_result: dict | None = None
    metadata: dict | None = None

    model_config = {"extra": "allow"}


class TrainingSampleRequest(BaseModel):
    source_id: str | None = Field(default=None, max_length=50)
    source: dict | None = None
    dataset_profile: str = Field(default="text_sft_v1", max_length=50)
    modality: str | None = Field(default=None, max_length=30)
    content: dict = Field(default_factory=dict)
    media_refs: list[dict] = Field(default_factory=list)
    labels: list = Field(default_factory=list)
    quality_score: float | None = Field(default=0.0, ge=0, le=1)
    sensitivity_level: str | None = Field(default=None, max_length=20)
    status: str | None = Field(default="ready", max_length=30)
    metadata: dict | None = None

    model_config = {"extra": "allow"}


class TrainingDatasetVersionCreateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    version: str | None = Field(default=None, max_length=60)
    dataset_profile: str = Field(default="text_sft_v1", max_length=50)
    modality: str | None = Field(default=None, max_length=30)
    target_model_family: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=100)
    org_unit_id: str | None = Field(default=None, max_length=50)
    sample_ids: list[str] | None = Field(default=None, max_length=5000)
    limit: int | None = Field(default=None, ge=1, le=5000)
    status: str | None = Field(default="ready", max_length=30)
    storage_location_id: str | None = Field(default=None, max_length=50)
    target_gateway_id: str | None = Field(default=None, max_length=80)
    auto_sync: bool = True

    model_config = {"extra": "allow"}


class TrainingDatasetSyncRequest(BaseModel):
    target_gateway_id: str | None = Field(default=None, max_length=80)
    metadata: dict | None = None

    model_config = {"extra": "allow"}


class TrainingAutomationAdvanceRequest(BaseModel):
    dispatch: bool = True
    evaluate: bool = True
    target_gateway_id: str | None = Field(default=None, max_length=80)
    metadata: dict | None = None

    model_config = {"extra": "allow"}


class TrainingAutomationRunRequest(BaseModel):
    materialize_learning: bool = True
    materialize_limit: int = Field(default=50000, ge=1, le=50000)
    promote_learning_samples: bool = True
    create_datasets: bool = True
    max_dataset_samples: int = Field(default=5000, ge=1, le=5000)
    datasets: bool = True
    jobs: bool = True
    dispatch: bool = True
    evaluate: bool = True
    max_datasets: int = Field(default=20, ge=0, le=100)
    max_jobs: int = Field(default=50, ge=1, le=200)
    trigger: str | None = Field(default="manual", max_length=40)
    metadata: dict | None = None

    model_config = {"extra": "allow"}


class TrainingFullHistoryFinetuneRunRequest(BaseModel):
    cycles: int = Field(default=3, ge=1, le=3)
    min_sample_count: int = Field(default=4, ge=1, le=100000)
    capture_sources: bool = True
    blocking_source_sync: bool = False
    days: int = Field(default=3650, ge=1, le=3650)
    per_source_limit: int = Field(default=2000, ge=1, le=50000)
    materialize_limit: int = Field(default=50000, ge=1, le=50000)
    background_dispatch: bool = True
    metadata: dict | None = None

    model_config = {"extra": "allow"}


class TrainingFullHistoryFinetuneChatRequest(BaseModel):
    messages: list[dict] | None = None
    prompt: str | None = Field(default=None, max_length=20000)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    timeout_seconds: int = Field(default=600, ge=60, le=3600)

    model_config = {"extra": "allow"}


class TrainingDeploymentChatRequest(BaseModel):
    messages: list[dict] | None = None
    prompt: str | None = Field(default=None, max_length=20000)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    timeout_seconds: int = Field(default=300, ge=60, le=1800)

    model_config = {"extra": "allow"}


class TrainingSimulationResultRequest(BaseModel):
    auto_advance_first: bool = True
    gateway_id: str | None = Field(default=None, max_length=50)
    worker_id: str | None = Field(default=None, max_length=100)
    status: str = Field(default="completed", max_length=30)
    progress: float | None = None
    metrics: dict | None = None
    artifacts: list[dict] | None = None
    logs_url: str | None = Field(default=None, max_length=500)
    size_bytes: int | None = Field(default=None, ge=0)
    metadata: dict | None = None

    model_config = {"extra": "allow"}


class TrainingSkillCandidateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    job_type: str = Field(default="lora", max_length=30)
    training_strategy: str | None = Field(default=None, max_length=80)
    target_gateway_id: str | None = Field(default=None, max_length=50)
    dataset_ref: str | None = Field(default=None, max_length=200)
    objective: str | None = Field(default=None, max_length=4000)
    risk_level: str | None = Field(default=None, max_length=20)
    model_family: str | None = Field(default=None, max_length=100)
    eval_gate: dict | None = None


class TrainingRunCandidateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    job_type: str = Field(default="lora", max_length=30)
    training_strategy: str | None = Field(default=None, max_length=80)
    target_gateway_id: str | None = Field(default=None, max_length=50)
    dataset_ref: str | None = Field(default=None, max_length=200)
    objective: str | None = Field(default=None, max_length=4000)
    risk_level: str | None = Field(default=None, max_length=20)
    model_family: str | None = Field(default=None, max_length=100)
    eval_gate: dict | None = None
    analysis_summary: str | None = Field(default=None, max_length=4000)
    analysis_model: str | None = Field(default=None, max_length=80)
    analysis_prompt_hash: str | None = Field(default=None, max_length=120)
    raw_counts: dict | None = None
    base_model: str | None = Field(default=None, max_length=120)
    target_metric: str | None = Field(default=None, max_length=120)
    estimated_gpu_hours: float | None = None
    gpu_estimate: dict | None = None
    forbidden_actions: list[str] | None = None
    risk_notes: str | None = Field(default=None, max_length=2000)
    allow_sandbox: bool = False


class TrainingGatewayResultRequest(BaseModel):
    job_id: str | None = Field(default=None, max_length=50)
    gateway_id: str | None = Field(default=None, max_length=50)
    worker_id: str | None = Field(default=None, max_length=100)
    status: str = Field(..., max_length=30)
    progress: float | None = None
    metrics: dict | None = None
    artifacts: list[dict] | None = None
    logs_url: str | None = Field(default=None, max_length=500)
    error: str | None = Field(default=None, max_length=4000)
    error_message: str | None = Field(default=None, max_length=4000)
    failure_stage: str | None = Field(default=None, max_length=30)
    callback_token: str | None = Field(default=None, max_length=2000)


class TrainingForceCancelRequest(BaseModel):
    reason: str = Field(..., min_length=10, max_length=1000)


class TrainingCollectDueRequest(BaseModel):
    stale_seconds: int = Field(default=300, ge=0, le=24 * 60 * 60)
    max_jobs: int = Field(default=20, ge=1, le=100)
    target_gateway_id: str | None = Field(default=None, max_length=50)


class TrainingDeploymentRequest(BaseModel):
    target_skill_ids: list[str] | None = Field(default=None, max_length=20)
    model_family: str | None = Field(default=None, max_length=100)
    target_gateway_id: str | None = Field(default=None, max_length=50)
    deployment_target_gateway_id: str | None = Field(default=None, max_length=50)
    deployment_runtime_profile: str | None = Field(default=None, max_length=80)
    rollout_percent: int | None = Field(default=0, ge=0, le=100)
    reason: str | None = Field(default=None, max_length=1000)


class TrainingDeploymentRollbackRequest(BaseModel):
    reason: str = Field(..., min_length=10, max_length=1000)


class TrainingDeploymentRejectRequest(BaseModel):
    reason: str = Field(..., min_length=10, max_length=1000)


class TrainingBootstrapEnvRequest(BaseModel):
    profile: str = Field(default="qlora-1b", max_length=50)
    cuda: str = Field(default="cu130", max_length=20)
    force: bool = False
    dry_run: bool = False
    timeout_seconds: int = Field(default=3600, ge=60, le=7200)
    agent_purpose: str | None = Field(default="mixed", max_length=20)


class TrainingPrepareModelRequest(BaseModel):
    profile: str = Field(default="qwen3.5-4b", max_length=50)
    revision: str = Field(default="main", max_length=120)
    modelscope_revision: str = Field(default="master", max_length=120)
    bootstrap_profile: str = Field(default="qlora-1b", max_length=50)
    force: bool = False
    dry_run: bool = False
    timeout_seconds: int = Field(default=7200, ge=60, le=21600)
    validate_mode: str = Field(default="metadata", max_length=20)
    source: str = Field(default="auto", max_length=20)
    internal_model_ref: str | None = Field(default=None, max_length=500)
    auto_discover: bool = False


class TrainingDiscoverAllModelsRequest(TrainingPrepareModelRequest):
    roots: list[str] | None = Field(default=None, max_length=32)
    terms: list[str] | None = Field(default=None, max_length=20)
    gateway_ids: list[str] | None = Field(default=None, max_length=20)
    training_gateway_id: str | None = Field(default=None, max_length=80)
    max_dirs: int = Field(default=12000, ge=1, le=50000)
    max_depth: int = Field(default=9, ge=1, le=12)
    limit: int = Field(default=12, ge=1, le=50)
    include_metadata: bool = False


class TrainingModelTransferRequest(TrainingDiscoverAllModelsRequest):
    source_gateway_id: str | None = Field(default=None, max_length=80)
    source_path: str | None = Field(default=None, max_length=1000)
    source_public_host: str | None = Field(default=None, max_length=120)
    force: bool = False
    prepare: bool = True
    hash_files: bool = True
    run_inline: bool = False
    ttl_seconds: int = Field(default=3600, ge=60, le=24 * 60 * 60)
    resume_manifest_sha256: str | None = Field(default=None, max_length=64)
    resume_transferred_bytes: int = Field(default=0, ge=0)
    resume_imported_files: int = Field(default=0, ge=0)
    resume_current_file: str | None = Field(default=None, max_length=500)


class TrainingRuntimeConfigRequest(BaseModel):
    qwen36_35b_model_dir: str | None = Field(default=None, max_length=500)
    model_dir: str | None = Field(default=None, max_length=500)
    model_path: str | None = Field(default=None, max_length=500)
    openwebui_enabled: bool | None = None
    openwebui_host: str | None = Field(default=None, max_length=120)
    openwebui_public_host: str | None = Field(default=None, max_length=120)
    openwebui_port: int | None = Field(default=None, ge=1, le=65535)
    env: dict | None = None


class TrainingPythonEnvProbeRequest(BaseModel):
    max_candidates: int = Field(default=30, ge=1, le=80)
    timeout_seconds: int = Field(default=120, ge=15, le=600)
    probe_timeout_seconds: int = Field(default=12, ge=2, le=60)
    candidates: list[str] | None = Field(default=None, max_length=40)


class TrainingOpenWebUIBaseModelRequest(BaseModel):
    profile: str = Field(default="qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive", max_length=100)
    model_id: str | None = Field(default=None, max_length=180)
    aliases: list[str] | None = Field(default=None, max_length=10)
    runtime_profile: str | None = Field(default=None, max_length=80)
    display_name: str | None = Field(default=None, max_length=180)
    source: str | None = Field(default=None, max_length=30)
    internal_model_ref: str | None = Field(default=None, max_length=500)


class TrainingOpenWebUIChatTestRequest(BaseModel):
    profile: str = Field(default="qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive", max_length=100)
    model_id: str | None = Field(default=None, max_length=180)
    prompt: str = Field(
        default="用一句话回答：你是 SkillForge 上哪个 OpenWebUI 模型？",
        min_length=1,
        max_length=20000,
    )
    max_tokens: int = Field(default=32, ge=1, le=4096)
    timeout_seconds: int = Field(default=600, ge=30, le=3600)


class TrainingTestModelRequest(BaseModel):
    profile: str = Field(default="qwen3.5-4b", max_length=50)
    bootstrap_profile: str = Field(default="qlora-1b", max_length=50)
    prompt: str = Field(..., min_length=1, max_length=20000)
    max_new_tokens: int = Field(default=512, ge=1, le=4096)
    timeout_seconds: int = Field(default=600, ge=30, le=1800)
    base_model_only: bool = True


require_training_web_or_cli_user = require_state_active_web_or_cli


@router.get("/resources")
async def get_training_resources(
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_training_resources(db, current_user)


@router.get("/automation/status")
async def get_training_automation_status_detail(
    window_days: int = Query(7, ge=1, le=3650),
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_automation_status(db, current_user, window_days=window_days)


@router.post("/automation/run")
async def post_training_automation_run(
    body: TrainingAutomationRunRequest | None = None,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    request = body or TrainingAutomationRunRequest()
    result = await run_training_automation_once(db, current_user, request.model_dump())
    await invalidate_training_cache()
    return result


@router.get("/full-history-finetune/status")
async def get_training_full_history_finetune_status(
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_full_history_finetune_status(db, current_user)


@router.post("/full-history-finetune/run")
async def post_training_full_history_finetune_run(
    body: TrainingFullHistoryFinetuneRunRequest | None = None,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    request = body or TrainingFullHistoryFinetuneRunRequest()
    result = await run_full_history_finetune_cycles(db, current_user, request.model_dump())
    await invalidate_training_cache()
    return result


@router.post("/full-history-finetune/chat")
async def post_training_full_history_finetune_chat(
    body: TrainingFullHistoryFinetuneChatRequest | None = None,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    request = body or TrainingFullHistoryFinetuneChatRequest()
    return await chat_full_history_finetuned_model(db, current_user, request.model_dump())


@router.post("/models/discover-all")
async def post_training_models_discover_all(
    body: TrainingDiscoverAllModelsRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await discover_training_models_across_gateways(db, current_user, body.model_dump())


@router.post("/models/transfer-to-training")
async def post_training_model_transfer_to_training(
    body: TrainingModelTransferRequest,
    response: Response,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    payload = body.model_dump()
    if payload.get("run_inline"):
        result = await transfer_training_model_to_training_gateway(db, current_user, payload)
        await invalidate_training_cache()
        return result
    result = await start_training_model_transfer(db, current_user, payload)
    if result.get("status") in {"queued", "running"}:
        kickoff_training_model_transfer(str(result.get("transfer_id") or result.get("id") or ""))
    response.status_code = 202
    await invalidate_training_cache()
    return result


@router.get("/model-transfers/{transfer_id}")
async def get_training_model_transfer(
    transfer_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_model_transfer_status(db, current_user, transfer_id)


@router.post("/model-transfers/{transfer_id}/cancel")
async def post_training_model_transfer_cancel(
    transfer_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await cancel_training_model_transfer(db, current_user, transfer_id)


@router.get("/resources/{gateway_id}/bootstrap-env")
async def get_training_resource_bootstrap_env(
    gateway_id: str,
    profile: str = "qlora-1b",
    cuda: str = "cu130",
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_gateway_bootstrap_status(
        db,
        current_user,
        gateway_id,
        {"profile": profile, "cuda": cuda},
    )


@router.post("/resources/{gateway_id}/bootstrap-env")
async def post_training_resource_bootstrap_env(
    gateway_id: str,
    body: TrainingBootstrapEnvRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await bootstrap_training_gateway_env(db, current_user, gateway_id, body.model_dump())
    await invalidate_training_cache()
    return result


@router.get("/resources/{gateway_id}/model")
async def get_training_resource_model(
    gateway_id: str,
    profile: str = "qwen3.5-4b",
    revision: str = "main",
    modelscope_revision: str = "master",
    bootstrap_profile: str = "qlora-1b",
    source: str = "auto",
    auto_discover: bool = False,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_gateway_model_status(
        db,
        current_user,
        gateway_id,
        {
            "profile": profile,
            "revision": revision,
            "modelscope_revision": modelscope_revision,
            "bootstrap_profile": bootstrap_profile,
            "source": source,
            "auto_discover": auto_discover,
        },
    )


@router.post("/resources/{gateway_id}/models/discover")
async def post_training_resource_models_discover(
    gateway_id: str,
    body: TrainingPrepareModelRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await discover_training_gateway_models(db, current_user, gateway_id, body.model_dump())


@router.post("/resources/{gateway_id}/model")
async def post_training_resource_model(
    gateway_id: str,
    body: TrainingPrepareModelRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await prepare_training_gateway_model(db, current_user, gateway_id, body.model_dump())
    await invalidate_training_cache()
    return result


@router.get("/resources/{gateway_id}/openwebui-status")
async def get_training_resource_openwebui_status(
    gateway_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_gateway_openwebui_status(db, current_user, gateway_id, {})


@router.get("/resources/{gateway_id}/openwebui-proxy/v1/models")
async def get_training_resource_openwebui_proxy_models(
    gateway_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await proxy_training_gateway_openwebui_models(db, current_user, gateway_id)


@router.post("/resources/{gateway_id}/openwebui-proxy/v1/chat/completions")
async def post_training_resource_openwebui_proxy_chat_completions(
    gateway_id: str,
    body: dict,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await proxy_training_gateway_openwebui_chat_completion(db, current_user, gateway_id, body)
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


@router.post("/resources/{gateway_id}/openwebui-base-model")
async def post_training_resource_openwebui_base_model(
    gateway_id: str,
    body: TrainingOpenWebUIBaseModelRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await register_training_gateway_openwebui_base_model(db, current_user, gateway_id, body.model_dump())
    await invalidate_training_cache()
    return result


@router.post("/resources/{gateway_id}/openwebui-chat/test")
async def post_training_resource_openwebui_chat_test(
    gateway_id: str,
    body: TrainingOpenWebUIChatTestRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await test_training_gateway_openwebui_chat(db, current_user, gateway_id, body.model_dump())


@router.post("/resources/{gateway_id}/runtime-config")
async def post_training_resource_runtime_config(
    gateway_id: str,
    body: TrainingRuntimeConfigRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await configure_training_gateway_runtime(db, current_user, gateway_id, body.model_dump())
    await invalidate_training_cache()
    return result


@router.post("/resources/{gateway_id}/python-envs")
async def post_training_resource_python_envs(
    gateway_id: str,
    body: TrainingPythonEnvProbeRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await discover_training_gateway_python_envs(db, current_user, gateway_id, body.model_dump())


@router.post("/resources/{gateway_id}/model/test")
async def post_training_resource_model_test(
    gateway_id: str,
    body: TrainingTestModelRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await test_training_gateway_model(db, current_user, gateway_id, body.model_dump())


@router.get("/jobs")
@cached(
    "training:jobs",
    ttl=settings.CACHE_TTL_TRAINING,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_training_jobs(
    status: str | None = None,
    gateway: str | None = None,
    target_gateway_id: str | None = None,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_training_jobs(
        db,
        current_user,
        status=status,
        target_gateway_id=target_gateway_id or gateway,
    )


@router.get("/deployments")
@cached(
    "training:deployments",
    ttl=settings.CACHE_TTL_TRAINING,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_training_deployments(
    status: str | None = None,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_training_deployments(db, current_user, status=status)


@router.get("/deployments/{deployment_id}")
@cached(
    "training:deployments:detail",
    ttl=settings.CACHE_TTL_TRAINING,
    scope_by=_CACHE_SCOPE,
    key_builder=_id_cache_key("deployment_id"),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_training_deployment_detail(
    deployment_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_deployment(db, current_user, deployment_id)


@router.post("/deployments/{deployment_id}/chat")
async def post_training_deployment_chat(
    deployment_id: str,
    request: TrainingDeploymentChatRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_training_deployment_model(db, current_user, deployment_id, request.model_dump())


@router.get("/datasets")
@cached(
    "training:datasets",
    ttl=settings.CACHE_TTL_KNOWLEDGE,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_training_datasets(
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_training_datasets(db, current_user)


@router.get("/assets/sources")
async def get_training_asset_sources(
    source_type: str | None = None,
    modality: str | None = None,
    department: str | None = None,
    limit: int = 200,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_training_asset_sources(
        db,
        current_user,
        source_type=source_type,
        modality=modality,
        department=department,
        limit=limit,
    )


@router.post("/assets/sources")
async def post_training_asset_source(
    body: TrainingAssetSourceRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await register_training_asset_source(db, current_user, body.model_dump())
    await invalidate_training_cache()
    return result


@router.get("/assets/samples")
async def get_training_asset_samples(
    dataset_profile: str | None = None,
    source_id: str | None = None,
    department: str | None = None,
    limit: int = 500,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_training_samples(
        db,
        current_user,
        dataset_profile=dataset_profile,
        source_id=source_id,
        department=department,
        limit=limit,
    )


@router.post("/assets/samples")
async def post_training_asset_sample(
    body: TrainingSampleRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await record_training_sample(db, current_user, body.model_dump())
    await invalidate_training_cache()
    return result


@router.get("/dataset-versions")
async def get_training_dataset_versions(
    dataset_profile: str | None = None,
    department: str | None = None,
    status: str | None = None,
    limit: int = 200,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_training_dataset_versions(
        db,
        current_user,
        dataset_profile=dataset_profile,
        department=department,
        status=status,
        limit=limit,
    )


@router.post("/dataset-versions")
async def post_training_dataset_version(
    body: TrainingDatasetVersionCreateRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await create_training_dataset_version(db, current_user, body.model_dump())
    await invalidate_training_cache()
    return result


@router.get("/dataset-versions/{dataset_version_id}")
async def get_training_dataset_version_detail(
    dataset_version_id: str,
    include_manifest: bool = False,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_dataset_version(db, current_user, dataset_version_id, include_manifest=include_manifest)


@router.get("/dataset-versions/{dataset_version_id}/automation")
async def get_training_dataset_version_automation_detail(
    dataset_version_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_dataset_version_automation(db, current_user, dataset_version_id)


@router.get("/dataset-versions/{dataset_version_id}/manifest")
async def get_training_dataset_version_manifest(
    dataset_version_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await training_dataset_version_manifest(db, current_user, dataset_version_id)


@router.post("/dataset-versions/{dataset_version_id}/sync")
async def post_training_dataset_version_sync(
    dataset_version_id: str,
    body: TrainingDatasetSyncRequest | None = None,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await sync_training_dataset_version(
        db,
        current_user,
        dataset_version_id,
        body.model_dump() if body else {},
    )
    await invalidate_training_cache()
    return result


@router.post("/dataset-versions/{dataset_version_id}/auto-run")
async def post_training_dataset_version_auto_run(
    dataset_version_id: str,
    body: TrainingAutomationAdvanceRequest | None = None,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await auto_run_training_dataset_version(
        db,
        current_user,
        dataset_version_id,
        body.model_dump() if body else {},
    )
    await invalidate_training_cache()
    return result


@router.post("/jobs")
async def post_training_job(
    body: TrainingJobCreateRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await create_training_job(db, current_user, body.model_dump())
    await invalidate_training_cache()
    return result


@router.get("/skills/{skill_id}/candidate-readiness")
@cached(
    "training:readiness",
    ttl=settings.CACHE_TTL_TRAINING,
    scope_by=_CACHE_SCOPE,
    key_builder=_id_cache_key("skill_id"),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_training_skill_candidate_readiness(
    skill_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_candidate_readiness(db, current_user, skill_id)


@router.post("/skills/{skill_id}/candidate")
async def post_training_skill_candidate(
    skill_id: str,
    body: TrainingSkillCandidateRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await create_training_candidate_from_skill(db, current_user, skill_id, body.model_dump())
    await invalidate_training_cache()
    return result


@router.post("/runs/{run_id}/candidate")
async def post_training_run_candidate(
    run_id: str,
    body: TrainingRunCandidateRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await create_training_candidate_from_run(db, current_user, run_id, body.model_dump())
    await invalidate_training_cache()
    return result


@router.post("/jobs/collect-due")
async def post_training_jobs_collect_due(
    body: TrainingCollectDueRequest | None = None,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    request = body or TrainingCollectDueRequest()
    result = await collect_due_training_job_results(
        db,
        current_user,
        stale_seconds=request.stale_seconds,
        max_jobs=request.max_jobs,
        target_gateway_id=request.target_gateway_id,
    )
    await invalidate_training_cache()
    return result


@router.post("/jobs/{job_id}/auto-advance")
async def post_training_job_auto_advance(
    job_id: str,
    body: TrainingAutomationAdvanceRequest | None = None,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await auto_advance_training_job(db, current_user, job_id, body.model_dump() if body else {})
    await invalidate_training_cache()
    return result


@router.post("/jobs/{job_id}/simulate-result")
async def post_training_job_simulate_result(
    job_id: str,
    body: TrainingSimulationResultRequest | None = None,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    request = body or TrainingSimulationResultRequest()
    result = await simulate_training_job_result(db, current_user, job_id, request.model_dump())
    await invalidate_training_cache()
    return result


@router.get("/jobs/{job_id}")
@cached(
    "training:jobs:detail",
    ttl=settings.CACHE_TTL_TRAINING,
    scope_by=_CACHE_SCOPE,
    key_builder=_id_cache_key("job_id"),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_training_job_detail(
    job_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_job(db, current_user, job_id)


@router.get("/jobs/{job_id}/logs")
@cached(
    "training:jobs:logs",
    ttl=settings.CACHE_TTL_TRAINING,
    scope_by=_CACHE_SCOPE,
    key_builder=_id_cache_key("job_id"),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_training_job_logs_detail(
    job_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_job_logs(db, current_user, job_id)


@router.get("/jobs/{job_id}/artifacts")
@cached(
    "training:jobs:artifacts",
    ttl=settings.CACHE_TTL_TRAINING,
    scope_by=_CACHE_SCOPE,
    key_builder=_id_cache_key("job_id"),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_training_job_artifacts_detail(
    job_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_training_job_artifacts(db, current_user, job_id)


@router.get("/jobs/{job_id}/artifacts/{artifact_id}/download")
async def download_training_job_artifact_file(
    job_id: str,
    artifact_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await download_training_job_artifact(db, current_user, job_id, artifact_id)
    filename = str(result["filename"]).replace('"', "")
    return Response(
        content=result["content"],
        media_type=result["content_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-SkillForge-Artifact-Sha256": str(result.get("sha256") or ""),
        },
    )


@router.post("/jobs/{job_id}/approve")
async def post_training_job_approve(
    job_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await approve_training_job(db, current_user, job_id)
    await invalidate_training_cache()
    return result


@router.post("/jobs/{job_id}/dispatch")
async def post_training_job_dispatch(
    job_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await dispatch_training_job(db, current_user, job_id)
    await invalidate_training_cache()
    return result


@router.post("/jobs/{job_id}/retry")
async def post_training_job_retry(
    job_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await retry_training_job(db, current_user, job_id)
    await invalidate_training_cache()
    return result


@router.post("/jobs/{job_id}/collect-result")
async def post_training_job_collect_result(
    job_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await collect_training_job_result(db, current_user, job_id)
    await invalidate_training_cache()
    return result


@router.post("/jobs/{job_id}/evaluate")
async def post_training_job_evaluate(
    job_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await evaluate_training_job(db, current_user, job_id)
    await invalidate_training_cache()
    return result


@router.post("/jobs/{job_id}/deploy-request")
async def post_training_job_deploy_request(
    job_id: str,
    body: TrainingDeploymentRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await request_training_deployment(db, current_user, job_id, body.model_dump())
    await invalidate_training_cache()
    return result


@router.post("/deployments/{deployment_id}/approve")
async def post_training_deployment_approve(
    deployment_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await approve_training_deployment(db, current_user, deployment_id)
    await invalidate_training_cache()
    return result


@router.post("/deployments/{deployment_id}/activate")
async def post_training_deployment_activate(
    deployment_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await activate_training_deployment(db, current_user, deployment_id)
    await invalidate_training_cache()
    return result


@router.post("/deployments/{deployment_id}/sync-openwebui")
async def post_training_deployment_sync_openwebui(
    deployment_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await sync_training_deployment_openwebui(db, current_user, deployment_id)
    await invalidate_training_cache()
    return result


@router.post("/deployments/{deployment_id}/reject")
async def post_training_deployment_reject(
    deployment_id: str,
    body: TrainingDeploymentRejectRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await reject_training_deployment(db, current_user, deployment_id, reason=body.reason)
    await invalidate_training_cache()
    return result


@router.post("/deployments/{deployment_id}/rollback")
async def post_training_deployment_rollback(
    deployment_id: str,
    body: TrainingDeploymentRollbackRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await rollback_training_deployment(db, current_user, deployment_id, reason=body.reason)
    await invalidate_training_cache()
    return result


@router.post("/jobs/{job_id}/gateway-result")
async def post_training_job_gateway_result(
    job_id: str,
    body: TrainingGatewayResultRequest,
    x_training_callback_token: str | None = Header(default=None, alias="X-Training-Callback-Token"),
    db: AsyncSession = Depends(get_db),
):
    result = await apply_training_gateway_result(
        db,
        job_id=job_id,
        token=x_training_callback_token or body.callback_token,
        payload=body.model_dump(exclude={"callback_token"}),
    )
    await invalidate_training_cache()
    return result


@router.post("/jobs/{job_id}/cancel")
async def post_training_job_cancel(
    job_id: str,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await cancel_training_job(db, current_user, job_id)
    await invalidate_training_cache()
    return result


@router.post("/jobs/{job_id}/force-cancel")
async def post_training_job_force_cancel(
    job_id: str,
    body: TrainingForceCancelRequest,
    current_user: User = Depends(require_training_web_or_cli_user),
    db: AsyncSession = Depends(get_db),
):
    result = await force_cancel_training_job(db, current_user, job_id, reason=body.reason)
    await invalidate_training_cache()
    return result
