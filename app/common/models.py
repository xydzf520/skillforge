"""公共模型：合规规则库 + 系统配置 + LLM 用量日志"""

from datetime import datetime, date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DECIMAL, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class SystemConfig(Base):
    """系统配置键值表"""
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSONB)
    updated_by: Mapped[str | None] = mapped_column(String(50))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)


class ComplianceRule(Base):
    __tablename__ = "compliance_rules"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # tmall/jd/douyin/meituan/eleme/all
    surface: Mapped[str] = mapped_column(String(20), nullable=False)  # title/main_image/detail/video/...
    category_scope: Mapped[str | None] = mapped_column(String(50))
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)  # keyword/regex/image_label
    pattern_value: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(5), nullable=False)  # P0/P1/P2/P3
    decision: Mapped[str] = mapped_column(String(20), nullable=False)  # block/rewrite/escalate/pass_with_log
    rewrite_suggestion: Mapped[str | None] = mapped_column(Text)
    required_evidence: Mapped[str | None] = mapped_column(String(50))
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class ComplianceRuleVersion(Base):
    """规则版本历史：每次 update 时插入一条，支持回滚到旧快照。"""
    __tablename__ = "compliance_rule_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    author: Mapped[str | None] = mapped_column(String(50))
    reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, index=True)


class UsageLog(Base):
    """F4: LLM 调用用量日志，每次 call_llm/call_llm_stream 完成后写一条"""
    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, index=True)
    user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department: Mapped[str | None] = mapped_column(String(50), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(50), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(50))
    call_source: Mapped[str | None] = mapped_column(String(64))  # agent_chat/coach/verifier/reviewer/...
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 6))
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=sa.text("'{}'::jsonb"), nullable=False)


class IntelligenceAnalyzeCache(Base):
    """平台级 LLM analyze 缓存，按 cache_key + model + prompt/context hash 唯一。"""
    __tablename__ = "intelligence_analyze_cache"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    evidence_passed: Mapped[bool | None] = mapped_column(Boolean)
    first_seen_run_id: Mapped[str | None] = mapped_column(String(100))
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default=sa.text("0"))
    last_hit_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint(
            "cache_key",
            "model",
            "prompt_hash",
            "context_hash",
            name="uq_intelligence_analyze_cache",
        ),
        sa.Index("ix_intelligence_analyze_cache_last_hit", "last_hit_at"),
    )


class IntelligenceAnalyzeRun(Base):
    """平台级 LLM analyze append-only 明细。cache 命中也写一行。"""
    __tablename__ = "intelligence_analyze_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(128), nullable=False)
    cache_id: Mapped[int | None] = mapped_column(BigInteger)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default=sa.false())
    cache_hit_of_run_id: Mapped[str | None] = mapped_column(String(100))
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False)
    skill_git_commit_full: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_git_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(100), index=True)
    instance_id: Mapped[str | None] = mapped_column(String(100))
    department: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    analysis_backend: Mapped[str | None] = mapped_column(String(20))
    analysis_agent_id: Mapped[str | None] = mapped_column(String(100))
    analysis_delegate_route: Mapped[dict | None] = mapped_column(JSONB)
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    data_health_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default=sa.false())
    degraded_reason: Mapped[str | None] = mapped_column(String(50))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    llm_output_hash: Mapped[str | None] = mapped_column(String(64))
    evidence_passed: Mapped[bool | None] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["cache_id"],
            ["intelligence_analyze_cache.id"],
            name="fk_intelligence_analyze_runs_cache",
            ondelete="SET NULL",
        ),
        sa.Index("ix_intelligence_analyze_runs_skill_run", "skill_id", "run_id", "created_at"),
        sa.Index("ix_intelligence_analyze_runs_cache_id", "cache_id"),
    )
