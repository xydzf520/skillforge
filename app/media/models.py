"""ORM models for the governed H3 media production workbench."""

from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import now_bjt
from app.database import Base


class MediaPlanComparison(Base):
    __tablename__ = "media_plan_comparisons"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_run_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    requested_by: Mapped[str | None] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned", index=True)
    brief_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    baseline_model: Mapped[str] = mapped_column(String(180), nullable=False)
    baseline_output_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    candidate_deployment_id: Mapped[str | None] = mapped_column(String(120), index=True)
    candidate_model: Mapped[str | None] = mapped_column(String(180))
    candidate_output_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    selected_source: Mapped[str | None] = mapped_column(String(30), index=True)
    final_output_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    edits_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    template_version: Mapped[str | None] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.Index("ix_media_plan_comparisons_run_created", "project_run_id", "created_at"),
        sa.Index("ix_media_plan_comparisons_department_created", "department_id", "created_at"),
    )


class MediaGenerationJob(Base):
    __tablename__ = "media_generation_jobs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_run_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_comparison_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_plan_comparisons.id", ondelete="SET NULL"), index=True
    )
    cloned_from_job_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_generation_jobs.id", ondelete="SET NULL"), index=True
    )
    production_batch_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_production_batches.id", ondelete="SET NULL"), index=True
    )
    workflow_definition_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_workflow_definitions.id", ondelete="SET NULL"), index=True
    )
    workflow_definition_version: Mapped[int | None] = mapped_column(Integer)
    continuation_chain_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_continuation_chains.id", ondelete="SET NULL"), index=True
    )
    strategy_session_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_creative_strategy_sessions.id", ondelete="SET NULL"), index=True
    )
    strategy_direction_id: Mapped[str | None] = mapped_column(String(50), index=True)
    replay_project_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_replay_projects.id", ondelete="SET NULL"), index=True
    )
    # The segment already points back to its generated job. Keep this reverse
    # lookup as a plain indexed lineage id to avoid a cyclic DDL dependency.
    replay_segment_id: Mapped[str | None] = mapped_column(String(50), index=True)
    prompt_preview_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    execution_prompt_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    depends_on_job_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_generation_jobs.id", ondelete="SET NULL"), index=True
    )
    sequence_index: Mapped[int | None] = mapped_column(Integer)
    not_before_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    department_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    requested_by: Mapped[str | None] = mapped_column(String(50), index=True)
    mode: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100", index=True)
    assigned_instance_id: Mapped[str | None] = mapped_column(String(50), index=True)
    current_attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    prompt_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    params_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reference_assets_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    workflow_template_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    workflow_version: Mapped[str | None] = mapped_column(String(80), index=True)
    model_version: Mapped[str | None] = mapped_column(String(180), index=True)
    model_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    result_asset_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="SET NULL"), index=True
    )
    result_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    review_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    review_assignee_id: Mapped[str | None] = mapped_column(String(50), index=True)
    review_tags_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    business_title: Mapped[str | None] = mapped_column(String(240), index=True)
    output_preset_id: Mapped[str | None] = mapped_column(String(50), index=True)
    poster_asset_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="SET NULL"), index=True
    )
    source_roles_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    creative_option: Mapped[str | None] = mapped_column(String(50), index=True)
    job_group_id: Mapped[str | None] = mapped_column(String(80), index=True)
    rights_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    training_eligibility: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending", index=True
    )
    error: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(50), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_media_generation_jobs_project_idempotency"),
        sa.Index("ix_media_generation_jobs_queue", "status", "priority", "created_at"),
        sa.Index("ix_media_generation_jobs_run_created", "project_run_id", "created_at"),
    )


class MediaLibraryAsset(Base):
    __tablename__ = "media_library_assets"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_asset_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    media_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_key: Mapped[str | None] = mapped_column(String(120), index=True)
    view_type: Mapped[str | None] = mapped_column(String(40), index=True)
    package_count: Mapped[int | None] = mapped_column(Integer)
    aliases_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    thumbnail_asset_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="SET NULL"), index=True
    )
    auto_reference_eligible: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false(), index=True
    )
    import_batch: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", index=True)
    tags_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    rights_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    scan_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("department_id", "source_asset_id", name="uq_media_library_asset_department_source"),
        sa.Index("ix_media_library_assets_department_status", "department_id", "status", "created_at"),
    )


class MediaAssetGroup(Base):
    __tablename__ = "media_asset_groups"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    items_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    history_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    published_by: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.Index("ix_media_asset_groups_department_status", "department_id", "status", "updated_at"),
    )


class MediaWorkflowDefinition(Base):
    __tablename__ = "media_workflow_definitions"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(500))
    platform: Mapped[str | None] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    workflow_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    h3_mode: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    bridge_template_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    required_roles_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    history_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    published_by: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.Index("ix_media_workflows_department_status", "department_id", "status", "updated_at"),
    )


class MediaProductionBatch(Base):
    __tablename__ = "media_production_batches"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_run_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    theme: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="scheduled", index=True)
    not_before_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    allowed_nodes_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    resource_policy_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    plan_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    counters_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    requested_by: Mapped[str | None] = mapped_column(String(50), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_media_batches_project_idempotency"),
        sa.Index("ix_media_batches_project_status", "project_id", "status", "created_at"),
        sa.Index("ix_media_batches_schedule", "status", "not_before_at", "deadline_at"),
    )


class MediaContinuationChain(Base):
    __tablename__ = "media_continuation_chains"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_run_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_asset_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned", index=True)
    target_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    preview_asset_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="SET NULL"), index=True
    )
    requested_by: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.Index("ix_media_continuation_project_status", "project_id", "status", "created_at"),
    )


class MediaContinuationSegment(Base):
    __tablename__ = "media_continuation_segments"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    chain_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("media_continuation_chains.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned", index=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    locks_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    anchor_asset_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="SET NULL"), index=True
    )
    media_job_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_generation_jobs.id", ondelete="SET NULL"), index=True
    )
    seam_analysis_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("chain_id", "segment_index", name="uq_media_continuation_chain_segment"),
        sa.Index("ix_media_continuation_segments_chain_status", "chain_id", "status", "segment_index"),
    )


class MediaReviewAnnotation(Base):
    __tablename__ = "media_review_annotations"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    media_job_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("media_generation_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    start_seconds: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0, server_default="0")
    end_seconds: Mapped[float | None] = mapped_column(sa.Float)
    category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning", index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.Index("ix_media_review_annotations_job_status", "media_job_id", "status", "start_seconds"),
    )


class MediaWorkbenchStreamEvent(Base):
    __tablename__ = "media_workbench_stream_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_run_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    previous_cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_events_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint(
            "project_run_id", "previous_cursor", "fingerprint",
            name="uq_media_workbench_stream_transition",
        ),
        sa.Index("ix_media_workbench_stream_run_cursor", "project_run_id", "id"),
    )


class MediaCreativeStrategySession(Base):
    """Durable, resumable AI strategy state for the 3.0 workbench."""

    __tablename__ = "media_creative_strategy_sessions"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_run_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[str | None] = mapped_column(String(50), index=True)
    requested_by: Mapped[str | None] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="directions_ready", index=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    original_input_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    included_context_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    directions_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    selected_direction_ids_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    questions_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    answers_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    assumptions_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    compiled_plans_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    model: Mapped[str | None] = mapped_column(String(180))
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False, default="material-strategy-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.Index(
            "ix_media_strategy_run_fingerprint",
            "project_run_id", "input_fingerprint", "created_at",
        ),
    )


class MediaWorkbenchAgentProfile(Base):
    """Per-user editable Agent instructions for the material workbench.

    The execution model is intentionally immutable at the product boundary;
    users can tune the Agent instructions without changing providers, API
    keys, or the allow-listed execution path.
    """

    __tablename__ = "media_workbench_agent_profiles"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    agent_key: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(180), nullable=False, default="deepseek-v4-flash")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    default_prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint(
            "project_id", "user_id", "agent_key",
            name="uq_media_workbench_agent_project_user_key",
        ),
        sa.Index(
            "ix_media_workbench_agent_project_user_updated",
            "project_id", "user_id", "updated_at",
        ),
    )


class MediaReplayProject(Base):
    """A complete-source replay or governed local-replacement project."""

    __tablename__ = "media_replay_projects"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_run_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[str | None] = mapped_column(String(50), index=True)
    requested_by: Mapped[str | None] = mapped_column(String(50), index=True)
    source_asset_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="analyzed", index=True)
    source_duration_seconds: Mapped[float] = mapped_column(sa.Float, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    analysis_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    preview_asset_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="SET NULL"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_media_replay_project_idempotency"),
        sa.Index("ix_media_replay_run_status", "project_run_id", "status", "updated_at"),
    )


class MediaReplaySegment(Base):
    __tablename__ = "media_replay_segments"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    replay_project_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("media_replay_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(sa.Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(sa.Float, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(sa.Float, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="analyzed", index=True)
    source_clip_asset_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="SET NULL"), index=True
    )
    media_job_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_generation_jobs.id", ondelete="SET NULL"), index=True
    )
    prompt_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dialogue_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    seam_analysis_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint(
            "replay_project_id", "segment_index", name="uq_media_replay_project_segment"
        ),
        sa.Index(
            "ix_media_replay_segments_project_status",
            "replay_project_id", "status", "segment_index",
        ),
    )


class MediaMaterialRequest(Base):
    """Business request that drives one or more governed generation jobs.

    This layer deliberately keeps business context separate from the H3 prompt.
    ``generation_participation_json`` is the auditable allow-list describing
    which business fields the requester explicitly permitted to participate in
    generation.
    """

    __tablename__ = "media_material_requests"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_run_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    assignee_id: Mapped[str | None] = mapped_column(String(50), index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="editorial", index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100", index=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    business_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    generation_participation_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    generation_options_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    visual_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    script: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_roles_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    production_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="direct", index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    ratio: Mapped[str] = mapped_column(String(20), nullable=False, default="9:16")
    output_preset_id: Mapped[str] = mapped_column(String(50), nullable=False, default="fast_preview")
    allow_ai_optimization: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.false())
    allow_script_changes: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.false())
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_media_material_requests_project_idempotency"),
        sa.Index("ix_media_material_requests_department_status", "department_id", "status", "priority", "created_at"),
        sa.Index("ix_media_material_requests_requester_created", "requested_by", "created_at"),
    )


class MediaCandidate(Base):
    """A generated asset offered to editors after technical validation."""

    __tablename__ = "media_candidates"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_material_requests.id", ondelete="SET NULL"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_run_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("project_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    media_job_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("media_generation_jobs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    generation_method: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    direction_id: Mapped[str | None] = mapped_column(String(20), index=True)
    variant_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    business_title: Mapped[str] = mapped_column(String(240), nullable=False)
    result_asset_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="SET NULL"), index=True
    )
    poster_asset_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="SET NULL"), index=True
    )
    preview_asset_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("project_run_assets.id", ondelete="SET NULL"), index=True
    )
    technical_status: Mapped[str] = mapped_column(String(30), nullable=False, default="checking", index=True)
    technical_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    editorial_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    delivery_status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_requested", index=True)
    attribution_status: Mapped[str] = mapped_column(String(30), nullable=False, default="unlinked", index=True)
    cumulative_spend: Mapped[Decimal] = mapped_column(sa.Numeric(18, 4), nullable=False, default=0, server_default="0")
    performance_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    tags_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    entered_selection_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.Index("ix_media_candidates_selection_queue", "department_id", "technical_status", "editorial_status", "entered_selection_at"),
        sa.Index("ix_media_candidates_request_created", "request_id", "created_at"),
        sa.Index("ix_media_candidates_delivery_attribution", "delivery_status", "attribution_status", "updated_at"),
        sa.Index("ix_media_candidates_tags_gin", "tags_json", postgresql_using="gin"),
    )


class MediaEditorialDecision(Base):
    """The latest decision per editor/candidate with append-only history JSON."""

    __tablename__ = "media_editorial_decisions"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("media_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    decided_by: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    usage_direction: Mapped[str | None] = mapped_column(String(240))
    edit_notes: Mapped[str | None] = mapped_column(Text)
    problem_types_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    annotation_ids_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    history_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("candidate_id", "decided_by", name="uq_media_editorial_decision_candidate_user"),
        sa.Index("ix_media_editorial_decisions_department_time", "department_id", "decision", "decided_at"),
    )


class MediaDelivery(Base):
    """Idempotent delivery/outbox state created only after editorial selection."""

    __tablename__ = "media_deliveries"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("media_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    video_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    team: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True, index=True)
    cloud_outbox_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("cloud_video_sync_outbox.id", ondelete="SET NULL"), index=True
    )
    remote_video_id: Mapped[str | None] = mapped_column(String(180), index=True)
    ad_asset_id: Mapped[str | None] = mapped_column(String(180), index=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.Index("ix_media_deliveries_candidate_status", "candidate_id", "status", "updated_at"),
        sa.Index("ix_media_deliveries_remote_link", "remote_video_id", "ad_asset_id"),
    )


class MediaPerformanceDaily(Base):
    """Immutable-by-key daily performance facts returned by the connector."""

    __tablename__ = "media_performance_daily"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("media_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delivery_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("media_deliveries.id", ondelete="SET NULL"), index=True
    )
    department_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    remote_video_id: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    ad_asset_id: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    metric_date: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    spend: Mapped[Decimal] = mapped_column(sa.Numeric(18, 4), nullable=False, default=0, server_default="0")
    impressions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    clicks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    conversions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    gmv: Mapped[Decimal] = mapped_column(sa.Numeric(18, 4), nullable=False, default=0, server_default="0")
    roi: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    source_version: Mapped[str] = mapped_column(String(120), nullable=False)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("ad_asset_id", "metric_date", name="uq_media_performance_ad_asset_date"),
        sa.Index("ix_media_performance_candidate_date", "candidate_id", "metric_date"),
        sa.Index("ix_media_performance_department_date", "department_id", "metric_date"),
    )


class MediaReviewPreference(Base):
    """Per-user review wall filters, sorting and display density."""

    __tablename__ = "media_review_preferences"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    preference_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("project_id", "user_id", name="uq_media_review_preferences_project_user"),
    )


class MediaGenerationAttempt(Base):
    __tablename__ = "media_generation_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("media_generation_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    instance_id: Mapped[str | None] = mapped_column(String(50), index=True)
    bridge_job_id: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="assigned", index=True)
    capability_snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    log_tail: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_media_generation_attempts_job_attempt"),
        sa.Index("ix_media_generation_attempts_instance_status", "instance_id", "status"),
    )


class CloudVideoSyncOutbox(Base):
    __tablename__ = "cloud_video_sync_outbox"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    media_job_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("media_generation_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True, index=True)
    team: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    remote_video_id: Mapped[str | None] = mapped_column(String(180), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        sa.Index("ix_cloud_video_sync_outbox_pending", "status", "next_attempt_at", "created_at"),
    )
