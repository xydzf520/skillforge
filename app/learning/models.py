"""Unified intelligence learning loop ORM models.

The learning loop is append-first: sources produce LearningEvent rows, extractors
produce LearningArtifact rows, materializers write downstream Knowledge / Training
/ Agent / Skill / SF objects, and LearningFlowEdge keeps cross-page lineage.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import now_bjt
from app.database import Base


class LearningEvent(Base):
    __tablename__ = "learning_events"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(100), index=True)
    user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    run_id: Mapped[str | None] = mapped_column(String(100), index=True)
    modality: Mapped[str] = mapped_column(String(20), nullable=False, default="text", server_default="text", index=True)
    payload_ref: Mapped[str | None] = mapped_column(String(240), index=True)
    redacted_summary: Mapped[str | None] = mapped_column(Text)
    sensitivity_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="internal", server_default="internal", index=True
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="captured", server_default="captured", index=True
    )
    quality_score: Mapped[float | None] = mapped_column(Float)
    policy_result_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("event_type", "source_type", "source_id", name="uq_learning_event_source"),
        sa.Index("ix_learning_events_scope_created", "department", "org_unit_id", "created_at"),
        sa.Index("ix_learning_events_skill_created", "skill_id", "created_at"),
    )


class LearningArtifact(Base):
    __tablename__ = "learning_artifacts"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    artifact_kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(40), index=True)
    target_id: Mapped[str | None] = mapped_column(String(120), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(100), index=True)
    run_id: Mapped[str | None] = mapped_column(String(100), index=True)
    title: Mapped[str | None] = mapped_column(String(240))
    summary: Mapped[str | None] = mapped_column(Text)
    content_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    labels_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    sensitivity_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="internal", server_default="internal", index=True
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="ready", server_default="ready", index=True
    )
    review_status: Mapped[str | None] = mapped_column(String(30), index=True)
    sink_type: Mapped[str | None] = mapped_column(String(40), index=True)
    sink_id: Mapped[str | None] = mapped_column(String(120), index=True)
    policy_result_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (
        sa.UniqueConstraint("event_id", "artifact_kind", "artifact_hash", name="uq_learning_artifact_event_hash"),
        sa.Index("ix_learning_artifacts_kind_status", "artifact_kind", "status"),
        sa.Index("ix_learning_artifacts_scope_created", "department", "org_unit_id", "created_at"),
    )


class LearningFlowEdge(Base):
    __tablename__ = "learning_flow_edges"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    from_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    from_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    to_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    to_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    relation: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(100), index=True)
    run_id: Mapped[str | None] = mapped_column(String(100), index=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", server_default="active", index=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)

    __table_args__ = (
        sa.UniqueConstraint("from_type", "from_id", "to_type", "to_id", "relation", name="uq_learning_flow_edge"),
        sa.Index("ix_learning_flow_edges_scope_created", "department", "org_unit_id", "created_at"),
    )


class LearningIngestionJob(Base):
    __tablename__ = "learning_ingestion_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    event_id: Mapped[str | None] = mapped_column(String(50), index=True)
    artifact_id: Mapped[str | None] = mapped_column(String(50), index=True)
    sink_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    sink_id: Mapped[str | None] = mapped_column(String(120), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", server_default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    policy_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        sa.Index("ix_learning_ingestion_jobs_status_created", "status", "created_at"),
    )


class LearningAutomationRun(Base):
    __tablename__ = "learning_automation_runs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    trigger_type: Mapped[str] = mapped_column(String(30), nullable=False, default="scheduled", server_default="scheduled", index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running", server_default="running", index=True)
    days: Mapped[int] = mapped_column(Integer, nullable=False, default=7, server_default="7")
    limit: Mapped[int] = mapped_column(Integer, nullable=False, default=300, server_default="300")
    materialize: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa.text("true"))
    captured_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    result_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (
        sa.Index("ix_learning_automation_runs_status_started", "status", "started_at"),
        sa.Index("ix_learning_automation_runs_trigger_started", "trigger_type", "started_at"),
    )


class LearningGovernanceTask(Base):
    __tablename__ = "learning_governance_tasks"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    queue_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    target_id: Mapped[str | None] = mapped_column(String(120), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    assignee: Mapped[str | None] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending", index=True
    )
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    payload_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    __table_args__ = (
        sa.Index("ix_learning_governance_tasks_queue_status", "queue_id", "status", "created_at"),
        sa.Index("ix_learning_governance_tasks_scope_created", "department", "org_unit_id", "created_at"),
    )


class ImprovementCandidate(Base):
    __tablename__ = "improvement_candidates"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    source_artifact_id: Mapped[str | None] = mapped_column(String(50), unique=True, index=True)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    target_id: Mapped[str | None] = mapped_column(String(120), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(100), index=True)
    run_id: Mapped[str | None] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    proposal: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_event_ids_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    risk_level: Mapped[str | None] = mapped_column(String(20), index=True)
    expected_impact_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open", server_default="open", index=True)
    review_id: Mapped[int | None] = mapped_column(Integer, index=True)
    todo_id: Mapped[str | None] = mapped_column(String(50), index=True)
    git_commit: Mapped[str | None] = mapped_column(String(80), index=True)
    external_ref_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    updated_by: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (
        sa.Index("ix_improvement_candidates_target_status", "target_type", "target_id", "status"),
        sa.Index("ix_improvement_candidates_scope_created", "department", "org_unit_id", "created_at"),
    )
