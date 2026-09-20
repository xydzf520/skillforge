"""Training domain ORM models."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import now_bjt
from app.database import Base


class TrainingJob(Base):
    __tablename__ = "training_jobs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="awaiting_review", index=True)
    job_type: Mapped[str] = mapped_column(String(30), nullable=False, default="lora")
    training_strategy: Mapped[str | None] = mapped_column(String(80))
    target_skill_id: Mapped[str | None] = mapped_column(String(50), index=True)
    target_gateway_id: Mapped[str | None] = mapped_column(String(50), index=True)
    dataset_ref: Mapped[str | None] = mapped_column(String(200))
    objective: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str | None] = mapped_column(String(20))
    failure_stage: Mapped[str | None] = mapped_column(String(30))
    spec_json: Mapped[dict | None] = mapped_column(JSONB)
    gateway_payload_json: Mapped[dict | None] = mapped_column(JSONB)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa.true())
    approved_by: Mapped[str | None] = mapped_column(String(50))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_by: Mapped[str | None] = mapped_column(String(50))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)


class TrainingJobTask(Base):
    __tablename__ = "training_job_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    gateway_id: Mapped[str | None] = mapped_column(String(50), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    metrics_json: Mapped[dict | None] = mapped_column(JSONB)
    logs_url: Mapped[str | None] = mapped_column(String(500))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)


class TrainingModelTransfer(Base):
    __tablename__ = "training_model_transfers"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    profile: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_gateway_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_gateway_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_public_host: Mapped[str | None] = mapped_column(String(120))
    internal_model_ref: Mapped[str | None] = mapped_column(String(1000))
    transfer_mode: Mapped[str | None] = mapped_column(String(30))
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    transferred_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    imported_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    current_file: Mapped[str | None] = mapped_column(String(500))
    manifest_sha256: Mapped[str | None] = mapped_column(String(128))
    hash_files: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa.false())
    prepare: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa.true())
    force: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa.false())
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=21600, server_default="21600")
    error: Mapped[str | None] = mapped_column(Text)
    request_json: Mapped[dict | None] = mapped_column(JSONB)
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)


class TrainingFullHistoryAutomationRun(Base):
    __tablename__ = "training_full_history_automation_runs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running", index=True)
    trigger_type: Mapped[str] = mapped_column(String(40), nullable=False, default="scheduled", index=True)
    current_step: Mapped[str | None] = mapped_column(String(80), index=True)
    current_cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    current_job_id: Mapped[str | None] = mapped_column(String(50), index=True)
    target_skill_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    model_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    training_gateway_id: Mapped[str | None] = mapped_column(String(80), index=True)
    deployment_gateway_id: Mapped[str | None] = mapped_column(String(80), index=True)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    request_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    data_preparation_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    actions_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (
        sa.Index("ix_training_full_history_runs_target_status", "target_skill_id", "status", "updated_at"),
    )


class TrainingModelDeployment(Base):
    __tablename__ = "model_deployments"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    department: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model_family: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    artifact_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    artifact_ref_json: Mapped[dict | None] = mapped_column(JSONB)
    eval_task_id: Mapped[int | None] = mapped_column(Integer, index=True)
    target_skill_ids_json: Mapped[list | None] = mapped_column(JSONB)
    deployment_target_gateway_id: Mapped[str | None] = mapped_column(String(50), index=True)
    deployment_runtime_profile: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="awaiting_review", index=True)
    rollout_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rollback_to: Mapped[str | None] = mapped_column(String(50))
    requested_by: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    request_reason: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(50))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    rejected_by: Mapped[str | None] = mapped_column(String(50))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)


class TrainingStorageLocation(Base):
    __tablename__ = "training_storage_locations"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    backend: Mapped[str] = mapped_column(String(40), nullable=False, default="local", server_default="local", index=True)
    uri: Mapped[str | None] = mapped_column(String(1000))
    gateway_id: Mapped[str | None] = mapped_column(String(80), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", server_default="active", index=True)
    capabilities_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    retention_policy_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    encryption_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)


class TrainingAssetSource(Base):
    __tablename__ = "training_asset_sources"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(240))
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(100), index=True)
    project_id: Mapped[str | None] = mapped_column(String(42), index=True)
    project_run_id: Mapped[str | None] = mapped_column(String(50), index=True)
    modality: Mapped[str] = mapped_column(String(30), nullable=False, default="text", server_default="text", index=True)
    storage_location_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("training_storage_locations.id", ondelete="SET NULL"),
        index=True,
    )
    storage_backend: Mapped[str | None] = mapped_column(String(40), index=True)
    storage_ref: Mapped[str | None] = mapped_column(String(1000))
    mime_type: Mapped[str | None] = mapped_column(String(120), index=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sensitivity_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="internal", server_default="internal", index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", server_default="active", index=True)
    policy_result_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("source_type", "source_id", name="uq_training_asset_sources_source"),
        sa.Index("ix_training_asset_sources_scope_created", "department", "org_unit_id", "created_at"),
    )


class TrainingSample(Base):
    __tablename__ = "training_samples"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    source_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("training_asset_sources.id", ondelete="SET NULL"),
        index=True,
    )
    dataset_profile: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    modality: Mapped[str] = mapped_column(String(30), nullable=False, default="text", server_default="text", index=True)
    sample_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    media_refs_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    labels_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(100), index=True)
    project_id: Mapped[str | None] = mapped_column(String(42), index=True)
    project_run_id: Mapped[str | None] = mapped_column(String(50), index=True)
    sensitivity_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="internal", server_default="internal", index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ready", server_default="ready", index=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("source_id", "dataset_profile", "sample_hash", name="uq_training_samples_source_profile_hash"),
        sa.Index("ix_training_samples_scope_profile", "department", "org_unit_id", "dataset_profile", "created_at"),
    )


class TrainingDatasetVersion(Base):
    __tablename__ = "training_dataset_versions"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(60), nullable=False)
    dataset_profile: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    modality: Mapped[str] = mapped_column(String(30), nullable=False, default="text", server_default="text", index=True)
    target_model_family: Mapped[str | None] = mapped_column(String(120), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ready", server_default="ready", index=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    media_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    manifest_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    storage_location_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("training_storage_locations.id", ondelete="SET NULL"),
        index=True,
    )
    target_gateway_id: Mapped[str | None] = mapped_column(String(80), index=True)
    sync_status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_synced", server_default="not_synced", index=True)
    synced_sink_job_id: Mapped[str | None] = mapped_column(String(50), index=True)
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    approved_by: Mapped[str | None] = mapped_column(String(50), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("department", "name", "version", name="uq_training_dataset_versions_department_name_version"),
        sa.Index("ix_training_dataset_versions_scope_created", "department", "org_unit_id", "created_at"),
    )
