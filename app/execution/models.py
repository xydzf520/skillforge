"""执行记录 + 决策日志 + OpenClaw实例 ORM 模型"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


RUN_MODE_SAMPLE_PREVIEW = "sample_preview"
RUN_MODE_SANDBOX_TEST = "sandbox_test"
RUN_MODE_MANUAL_REAL = "manual_real"
RUN_MODE_SCHEDULED_REAL = "scheduled_real"
RUN_MODE_SDK_SUBMIT = "sdk_submit"
RUN_MODE_BATCH_CANDIDATE = "batch_candidate"

RUN_MODES = {
    RUN_MODE_SAMPLE_PREVIEW,
    RUN_MODE_SANDBOX_TEST,
    RUN_MODE_MANUAL_REAL,
    RUN_MODE_SCHEDULED_REAL,
    RUN_MODE_SDK_SUBMIT,
    RUN_MODE_BATCH_CANDIDATE,
}
SAMPLE_RUN_MODES = {RUN_MODE_SAMPLE_PREVIEW, RUN_MODE_SANDBOX_TEST}


class ExecutionRun(Base):
    __tablename__ = "execution_runs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # UUID
    skill_id: Mapped[str | None] = mapped_column(String(50), index=True)
    playbook_id: Mapped[str | None] = mapped_column(String(50))
    trigger_type: Mapped[str | None] = mapped_column(String(80))  # cron/manual/event/source marker
    run_mode: Mapped[str | None] = mapped_column(String(30), index=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(50), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running/completed/failed/timeout
    total_steps: Mapped[int | None] = mapped_column(Integer)
    completed_steps: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text)  # B5: AI 生成的执行摘要
    manual_baseline_minutes: Mapped[float | None] = mapped_column(Float)
    business_value_tag: Mapped[str | None] = mapped_column(String(50))
    business_ref_id: Mapped[str | None] = mapped_column(String(200))
    # v2.9.1 M2 · 多次运行投票（skill-multi-run-voting）
    batch_id: Mapped[str | None] = mapped_column(String(50), index=True)  # 同批并发共享
    is_winner: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default=sa.false(),
    )  # 用户选中的冠军结果
    vote_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        server_default=sa.text("0"),
    )  # 获得投票数（预留）
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)
    source_instance_id: Mapped[str | None] = mapped_column(String(50), index=True)


class ExecutionStep(Base):
    __tablename__ = "execution_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(50), nullable=False)  # 索引在 migration 003
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False)
    step_order: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    input_data: Mapped[dict | None] = mapped_column(JSONB)
    output_data: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)


class DecisionLog(Base):
    __tablename__ = "decision_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(50))  # 索引在 migration 003
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False)
    input_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    output_result: Mapped[dict | None] = mapped_column(JSONB)
    suggested_action: Mapped[dict | None] = mapped_column(JSONB)
    approval_level: Mapped[int | None] = mapped_column(Integer)
    approval_status: Mapped[str | None] = mapped_column(String(20))  # auto/approved/rejected
    approver: Mapped[str | None] = mapped_column(String(50))
    target_user: Mapped[str | None] = mapped_column(String(50))
    user_action: Mapped[str | None] = mapped_column(String(20))  # completed/rejected/na
    user_feedback: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[int | None] = mapped_column(Integer)  # 1-5星评分
    feedback_type: Mapped[str | None] = mapped_column(String(50))  # 反馈类型（5选1）
    reject_reason: Mapped[str | None] = mapped_column(String(50))  # 驳回原因（4选1）
    business_impact: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    dingtalk_msg_id: Mapped[str | None] = mapped_column(String(100))
    model_id: Mapped[str | None] = mapped_column(String(50))
    token_count: Mapped[int | None] = mapped_column(Integer)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False)


class ExecutionArtifact(Base):
    """Platform-persisted raw execution artifact."""
    __tablename__ = "execution_artifacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    decision_log_id: Mapped[int | None] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_name: Mapped[str | None] = mapped_column(String(100), index=True)
    storage_backend: Mapped[str] = mapped_column(String(30), default="local", nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), default="application/json+gzip", nullable=False)
    encoding: Mapped[str] = mapped_column(String(30), default="gzip", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    uncompressed_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("run_id", "kind", "sha256", name="uq_execution_artifacts_run_kind_sha"),
        sa.Index("ix_execution_artifacts_skill_kind_created", "skill_id", "kind", "created_at"),
    )


class ExecutionRunLog(Base):
    """Redacted execution log tail persisted for Codex/runtime diagnostics."""
    __tablename__ = "execution_run_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    stream: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str | None] = mapped_column(String(50))
    content_tail: Mapped[str] = mapped_column(Text, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default=sa.false())
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)

    __table_args__ = (
        sa.Index("ix_execution_run_logs_run_created", "run_id", "created_at"),
    )


class ShadowComparison(Base):
    """影子运行新旧版本对比记录"""
    __tablename__ = "shadow_comparisons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(50), nullable=False)
    old_version: Mapped[str | None] = mapped_column(String(20))
    new_version: Mapped[str | None] = mapped_column(String(20))
    old_output: Mapped[dict | None] = mapped_column(JSONB)
    new_output: Mapped[dict | None] = mapped_column(JSONB)
    is_divergent: Mapped[bool] = mapped_column(Boolean, default=False)
    divergence_detail: Mapped[dict | None] = mapped_column(JSONB)
    human_action: Mapped[str | None] = mapped_column(String(20))  # completed/rejected/na
    human_recorded_by: Mapped[str | None] = mapped_column(String(50))
    human_recorded_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)


class OpenClawInstance(Base):
    __tablename__ = "openclaw_instances"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str | None] = mapped_column(String(50))
    gateway_url: Mapped[str] = mapped_column(String(200), nullable=False)
    reload_hook_url: Mapped[str] = mapped_column(String(200), nullable=False)
    reload_token: Mapped[str] = mapped_column(String(200), nullable=False)
    auth_token: Mapped[str | None] = mapped_column(String(200))
    network_zone: Mapped[str] = mapped_column(String(20), default="internal")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_platform_default: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default=sa.false(),
    )
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_sync_ok: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)

    # Agent 类型："aiclaw"(默认) / "hermes" — 决定通信协议和 Skill 格式
    agent_type: Mapped[str] = mapped_column(String(20), default="aiclaw")
    # Agent 用途：skill_runtime / analysis / training / media / mixed。
    # 部门可用多个 Agent 承接不同分析或运行职责；平台训练 Agent 仍通过
    # is_platform_default + agent_purpose=training 显式标记。
    agent_purpose: Mapped[str] = mapped_column(
        String(30),
        default="skill_runtime",
        nullable=False,
        server_default="skill_runtime",
    )
    # AIClaw Bridge 模式（Phase 1.7）
    # connection_mode: "bridge" 是唯一支持模式（v1.7.0 起）；保留字段是为了向后兼容旧迁移
    connection_mode: Mapped[str] = mapped_column(String(20), default="bridge")
    # bridge_connected_at: 最近一次 bridge 注册成功时间（断线为 None）
    bridge_connected_at: Mapped[datetime | None] = mapped_column(DateTime)
    enrollment_token_hash: Mapped[str | None] = mapped_column(String(128))
    enrollment_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    enrollment_consumed_at: Mapped[datetime | None] = mapped_column(DateTime)
    device_pubkey: Mapped[str | None] = mapped_column(Text)
    bridge_fingerprint: Mapped[str | None] = mapped_column(String(200))
    pending_rotation: Mapped[bool] = mapped_column(Boolean, default=False)
    rotation_token_hash: Mapped[str | None] = mapped_column(String(128))
    rotation_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    # bridge 启动时上报的能力（每次重连刷新）
    bridge_gateway_kind: Mapped[str | None] = mapped_column(String(20))      # aiclaw / openclaw / unknown
    bridge_gateway_version: Mapped[str | None] = mapped_column(String(50))
    bridge_platform: Mapped[str | None] = mapped_column(String(30))           # linux / darwin / win32
    bridge_skills_dir: Mapped[str | None] = mapped_column(String(500))         # bridge 可写的 skills 根目录
    bridge_skills_dirs_json: Mapped[str | None] = mapped_column(Text)          # 全部候选目录 (JSON)
    bridge_version: Mapped[str | None] = mapped_column(String(50))
    # bridge 上报的完整能力 JSON（含 disk/memory/gpu），每次重连刷新
    bridge_capabilities_json: Mapped[str | None] = mapped_column(Text)


class SkillSyncJob(Base):
    """一次 Skill 同步任务。节点级结果写入 SkillSyncAttempt。"""

    __tablename__ = "skill_sync_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    version_tag: Mapped[str | None] = mapped_column(String(100), index=True)
    review_id: Mapped[int | None] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    actor: Mapped[str | None] = mapped_column(String(100))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default=sa.text("0"))
    target_instance_ids: Mapped[list | None] = mapped_column(JSONB)
    target_department: Mapped[str | None] = mapped_column(String(50))
    push_ok: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)


class SkillSyncAttempt(Base):
    """一次 Skill 同步任务对单个节点的一次投递尝试。"""

    __tablename__ = "skill_sync_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    version_tag: Mapped[str | None] = mapped_column(String(100), index=True)
    review_id: Mapped[int | None] = mapped_column(Integer, index=True)
    instance_id: Mapped[str | None] = mapped_column(String(50), index=True)
    agent_type: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    actor: Mapped[str | None] = mapped_column(String(100))
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default=sa.text("1"))
    result: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)


class NodeScheduleConfig(Base):
    """SkillForge 推送到节点的定时执行配置跟踪。"""

    __tablename__ = "node_schedule_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa.text("0"))
    pushed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    ack_ok: Mapped[bool | None] = mapped_column(Boolean)
    runtime_backend: Mapped[str | None] = mapped_column(String(30))
    runtime_config: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        sa.UniqueConstraint("instance_id", "skill_id", name="uq_node_schedule_inst_skill"),
    )


class ExecutionWorker(Base):
    __tablename__ = "execution_workers"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    queue_name: Mapped[str | None] = mapped_column(String(50))
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    active_runs: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="online")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)


class ContractDrift(Base):
    """Skill 运行时实际 output 与 contract.output_schema 不一致的记录。

    写入策略：warn-only；runtime 永远不 reject，owner 收钉钉提醒后
    决定是修 schema 还是修 main.py。
    """
    __tablename__ = "contract_drifts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False)
    run_id: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_errors: Mapped[list] = mapped_column(JSONB, nullable=False)
    # schema_hash 用于识别"同一版 schema 下重复告警",便于聚合
    schema_hash: Mapped[str | None] = mapped_column(String(64))
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by: Mapped[str | None] = mapped_column(String(50))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
