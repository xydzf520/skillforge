"""Skills 工作台 ORM 模型。"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import now_bjt
from app.database import Base


def _now_bjt():
    """返回北京时间（naive），兼容 TIMESTAMP WITHOUT TIME ZONE 列。"""
    return now_bjt()


class SkillWorkbenchSession(Base):
    __tablename__ = "skill_workbench_sessions"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), default="pro")
    current_module: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    context_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt, onupdate=_now_bjt)


# 会话消息历史和 AI 调用追踪（message_persistence.py 使用）
class SkillWorkbenchMessage(Base):
    __tablename__ = "skill_workbench_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("skill_workbench_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent_json: Mapped[dict | None] = mapped_column(JSONB)
    # Studio 新增：上下文感知字段
    module_id: Mapped[str | None] = mapped_column(String(30))
    selection_range: Mapped[dict | None] = mapped_column(JSONB)
    draft_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)


class SkillWorkbenchPatch(Base):
    __tablename__ = "skill_workbench_patches"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("skill_workbench_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_module: Mapped[str] = mapped_column(String(30), nullable=False)
    intent: Mapped[str | None] = mapped_column(String(50))
    summary: Mapped[str | None] = mapped_column(Text)
    patch_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    diff_preview_json: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Studio 新增：Hunk 级操作字段
    hunks_json: Mapped[dict | None] = mapped_column(JSONB)
    accepted_hunks: Mapped[dict | None] = mapped_column(JSONB)
    rejected_hunks: Mapped[dict | None] = mapped_column(JSONB)
    source_context: Mapped[dict | None] = mapped_column(JSONB)


class SkillWorkbenchValidationRun(Base):
    __tablename__ = "skill_workbench_validation_runs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    patch_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("skill_workbench_patches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    structural_report: Mapped[dict | None] = mapped_column(JSONB)
    sample_case_report: Mapped[dict | None] = mapped_column(JSONB)
    replay_report: Mapped[dict | None] = mapped_column(JSONB)
    impact_summary: Mapped[dict | None] = mapped_column(JSONB)
    can_apply: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)


class SkillWorkbenchReference(Base):
    __tablename__ = "skill_workbench_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patch_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("skill_workbench_patches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_module: Mapped[str | None] = mapped_column(String(30))
    reference_mode: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)


class SkillWorkbenchAIRun(Base):
    __tablename__ = "skill_workbench_ai_runs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("skill_workbench_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patch_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("skill_workbench_patches.id", ondelete="CASCADE"),
        index=True,
    )
    run_type: Mapped[str] = mapped_column(String(30), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(100))
    prompt_key: Mapped[str | None] = mapped_column(String(100))
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(default=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)


class SkillStudioDraft(Base):
    __tablename__ = "skill_drafts"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    skill_id: Mapped[str | None] = mapped_column(String(255), index=True)
    branch: Mapped[str] = mapped_column(String(50), default="main")
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_message: Mapped[str | None] = mapped_column(Text)
    intent_md: Mapped[str | None] = mapped_column(Text)
    skill_md: Mapped[str | None] = mapped_column(Text)
    policy_yaml: Mapped[str | None] = mapped_column(Text)
    contract_json: Mapped[dict | None] = mapped_column(JSONB)
    review_state_json: Mapped[dict | None] = mapped_column(JSONB)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    # v7 C2 / C3：草稿锁状态、确认次数、信任模式
    lock_state: Mapped[dict | None] = mapped_column(JSONB)
    confirmation_count: Mapped[int] = mapped_column(Integer, default=0)
    trust_mode: Mapped[bool] = mapped_column(default=False)
    # v7 一步到位：aiclaw 生成阶段保留的非标准文件（scripts/main.py / tests/test_main.py 等）
    # 4 个标准字段 skill_md / intent_md / policy_yaml / contract_json 仍然占独立列
    extra_files: Mapped[dict | None] = mapped_column(JSONB)
    # 生成阶段状态：pending / running / ready / finalized / failed / cancelled
    generation_status: Mapped[str] = mapped_column(String(20), default="ready")
    # 失败时保留的诊断信息（含 scratch_dir 路径供管理后台 debug）
    error_detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt, onupdate=_now_bjt)


class SkillStudioRun(Base):
    __tablename__ = "skill_runs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    skill_id: Mapped[str | None] = mapped_column(String(255), index=True)
    draft_id: Mapped[str | None] = mapped_column(String(50), ForeignKey("skill_drafts.id"), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(20), default="save")
    contract_json: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    preview_cache_key: Mapped[str | None] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    cost_usd: Mapped[float | None] = mapped_column(Float)


class SkillStudioPreview(Base):
    __tablename__ = "skill_previews"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    skill_id: Mapped[str | None] = mapped_column(String(255), index=True)
    draft_id: Mapped[str | None] = mapped_column(String(50), ForeignKey("skill_drafts.id"), index=True)
    contract_json: Mapped[dict | None] = mapped_column(JSONB)
    adapter_name: Mapped[str | None] = mapped_column(String(50))
    rendered_output: Mapped[str | None] = mapped_column(Text)
    card_payload_json: Mapped[dict | None] = mapped_column(JSONB)
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class SkillStudioReview(Base):
    __tablename__ = "skill_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str | None] = mapped_column(String(255), index=True)
    draft_id: Mapped[str | None] = mapped_column(String(50), ForeignKey("skill_drafts.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    checkpoint: Mapped[str] = mapped_column(String(50), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    detail_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)
