"""Optimizer ORM 模型"""

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base
from app.common.time_utils import now_bjt


class OptimizerSession(Base):
    """优化会话"""
    __tablename__ = "optimizer_sessions"

    id = Column(String(50), primary_key=True)
    skill_id = Column(String(100), ForeignKey("skills.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    goal = Column(String(200), nullable=False)
    config = Column(JSONB, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="created")  # created/running/paused/completed/failed
    baseline_commit = Column(String(50))
    baseline_score = Column(JSONB)
    best_candidate_id = Column(String(50))
    current_iteration = Column(Integer, default=0)
    max_iterations = Column(Integer, default=20)
    total_tokens = Column(Integer, default=0)
    created_by = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=now_bjt)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)


class OptimizerCandidate(Base):
    """候选版本"""
    __tablename__ = "optimizer_candidates"

    id = Column(String(50), primary_key=True)
    session_id = Column(String(50), ForeignKey("optimizer_sessions.id"), nullable=False, index=True)
    iteration = Column(Integer, nullable=False)
    parent_id = Column(String(50))
    skill_md_patch = Column(Text)
    diff_summary = Column(Text)
    generation_rationale = Column(Text)
    changes = Column(JSONB)

    validation_status = Column(String(20), default="pending")
    validation_errors = Column(JSONB)
    benchmark_status = Column(String(20), default="pending")
    benchmark_score = Column(JSONB)
    shadow_status = Column(String(20), default="pending")
    shadow_stats = Column(JSONB)

    decision = Column(String(20), default="pending")  # pending/accepted/rejected/promoted
    reject_reason = Column(Text)

    model_name = Column(String(100))
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=now_bjt)


class BenchmarkPack(Base):
    """评测包"""
    __tablename__ = "benchmark_packs"

    id = Column(String(50), primary_key=True)
    skill_id = Column(String(100), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    source = Column(String(50), nullable=False)  # decision_log/test_cases/manual/mixed
    case_count = Column(Integer, default=0)
    frozen = Column(Boolean, default=False)
    manifest = Column(JSONB)
    created_by = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=now_bjt)


class BenchmarkCase(Base):
    """评测用例"""
    __tablename__ = "benchmark_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pack_id = Column(String(50), ForeignKey("benchmark_packs.id"), nullable=False, index=True)
    case_key = Column(String(200), nullable=False)
    input_data = Column(JSONB, nullable=False)
    expected_output = Column(JSONB)
    assertions = Column(JSONB, nullable=False, default=list)
    tags = Column(JSONB, default=list)
    weight = Column(Float, default=1.0)
    provenance = Column(JSONB)

    __table_args__ = (
        UniqueConstraint("pack_id", "case_key", name="uq_bcase_pack_key"),
    )


class BenchmarkRun(Base):
    """评测运行记录"""
    __tablename__ = "benchmark_runs"

    id = Column(String(50), primary_key=True)
    pack_id = Column(String(50), ForeignKey("benchmark_packs.id"), nullable=False, index=True)
    candidate_id = Column(String(50), index=True)
    status = Column(String(20), default="running")
    metrics = Column(JSONB)
    case_results = Column(JSONB)
    started_at = Column(DateTime, default=now_bjt)
    finished_at = Column(DateTime)
