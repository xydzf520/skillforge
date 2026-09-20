"""AI 待办 ORM 模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class DecisionRequest(Base):
    __tablename__ = "decision_requests"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(50), index=True)
    # 关联到执行决策日志，让审核人能看到完整决策推理链
    decision_log_id: Mapped[int | None] = mapped_column(Integer)
    # kind: review = 纯审批; dispatch = 管理者审批后向 executor 派发任务
    kind: Mapped[str] = mapped_column(String(20), default="review", nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    decision_mode: Mapped[str] = mapped_column(String(20), default="any_of")
    aggregate_status: Mapped[str] = mapped_column(String(20), default="pending")
    aggregate_decision: Mapped[str | None] = mapped_column(String(20))
    sla_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)
    # OpenClaw 决策回调元数据：决策完成后回写给 Skill 让其继续后续步骤
    callback_payload: Mapped[dict | None] = mapped_column(JSONB)
    # callback_status: skipped(无回调)/pending(等待发送)/sent(已通知)/failed
    callback_status: Mapped[str] = mapped_column(String(20), default="skipped", nullable=False)
    callback_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    callback_error: Mapped[str | None] = mapped_column(Text)
    callback_completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_dr_skill_status", "skill_id", "aggregate_status"),
        Index("ix_dr_source", "source_type", "source_id"),
        Index("ix_dr_created_at", "created_at"),
        # 幂等创建: Skill 重试 / OpenClaw 重发结果时不重复建 DecisionRequest
        UniqueConstraint("source_type", "source_id", "kind", "skill_id", name="uq_dr_source_kind_skill"),
    )


class AITodo(Base):
    __tablename__ = "ai_todos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("decision_requests.id"),
        nullable=False,
        index=True,
    )
    # 与 DecisionRequest.kind 一致，便于按用户视角直接过滤"我的审批 / 我要派发的"
    kind: Mapped[str] = mapped_column(String(20), default="review", nullable=False)
    assignee: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_by: Mapped[str | None] = mapped_column(String(50))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decision_channel: Mapped[str | None] = mapped_column(String(20))
    feedback_payload: Mapped[dict | None] = mapped_column(JSONB)
    dingtalk_msg_id: Mapped[str | None] = mapped_column(String(100))
    dingtalk_msg_type: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now_bjt,
        onupdate=now_bjt,
    )

    __table_args__ = (
        Index("ix_todos_assignee_status", "assignee", "status", "created_at"),
        Index("ix_todos_assignee_kind_status", "assignee", "kind", "status"),
        Index("ix_todos_request_id", "request_id"),
    )


class TodoDispatchTask(Base):
    """派发子任务：kind=dispatch 的待办在管理者审批通过后，fan-out 给每个 executor 个人钉钉。"""

    __tablename__ = "todo_dispatch_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("decision_requests.id"),
        nullable=False,
        index=True,
    )
    # 触发派发的管理者待办（审批通过后才派发）
    manager_todo_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("ai_todos.id"))
    executor: Mapped[str | None] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime)
    extra: Mapped[dict | None] = mapped_column(JSONB)
    # awaiting_dispatch: 等待管理者审批（审批前）
    # pending_assignment: 已审批通过但尚未指定执行人
    # sent: 已推送给执行人，待开始
    # pushed_no_dingtalk: 已派发但钉钉未送达，仅站内可见
    # in_progress: 执行中
    # blocked: 执行受阻
    # done: executor 已确认完成
    # cancelled: 管理者驳回 / 撤销
    status: Mapped[str] = mapped_column(String(20), default="awaiting_dispatch", nullable=False)
    assigned_by: Mapped[str | None] = mapped_column(String(50))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime)
    dingtalk_msg_id: Mapped[str | None] = mapped_column(String(100))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)  # 执行人标记"进行中"时记录
    ack_at: Mapped[datetime | None] = mapped_column(DateTime)
    ack_note: Mapped[str | None] = mapped_column(Text)
    ack_channel: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now_bjt,
        onupdate=now_bjt,
    )

    __table_args__ = (
        Index("ix_dispatch_request", "request_id"),
        Index("ix_dispatch_executor_status", "executor", "status"),
    )


class TodoDispatchPreference(Base):
    """管理者在同一 Skill / 派发范围 / 业务对象内最近一次成功选择的执行人。"""

    __tablename__ = "todo_dispatch_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(50), nullable=False)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False)
    # 空字符串表示该范围维度不可解析；避免 NULL 破坏唯一约束语义。
    target_org_unit_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    department: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    # 空字符串表示无法解析具体商品/业务对象，保留旧的 Skill 级兜底语义。
    dispatch_object_key: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    last_executor_id: Mapped[str] = mapped_column(String(50), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now_bjt,
        onupdate=now_bjt,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "actor_id",
            "skill_id",
            "target_org_unit_id",
            "department",
            "dispatch_object_key",
            name="uq_todo_dispatch_pref_scope_object",
        ),
        Index("ix_todo_dispatch_pref_actor_skill", "actor_id", "skill_id"),
        Index(
            "ix_todo_dispatch_pref_actor_skill_object",
            "actor_id",
            "skill_id",
            "dispatch_object_key",
        ),
    )
