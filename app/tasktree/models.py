"""任务树轻量投影 ORM。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class TaskNodeLight(Base):
    """Phase 1.5 的任务树读写轻量表。"""

    __tablename__ = "task_nodes_light"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    root_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("task_nodes_light.id"),
    )
    source_run_id: Mapped[str | None] = mapped_column(String(100))
    source_instance_id: Mapped[str | None] = mapped_column(String(100))
    department_id: Mapped[str] = mapped_column(String(100), nullable=False)
    node_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    sort_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=now_bjt,
        onupdate=now_bjt,
        server_default=text("NOW()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=now_bjt,
        server_default=text("NOW()"),
    )


Index("ix_tnl_tree", TaskNodeLight.department_id, TaskNodeLight.root_id, TaskNodeLight.parent_id, TaskNodeLight.sort_key)
Index("ix_tnl_updated", TaskNodeLight.department_id, TaskNodeLight.updated_at.desc())
Index("ix_tnl_run", TaskNodeLight.source_run_id)
Index(
    "ix_tnl_active",
    TaskNodeLight.department_id,
    TaskNodeLight.status,
    postgresql_where=text("status IN ('queued', 'running', 'stale')"),
)
# [M2] 主读路径索引：匹配 _get_tree_from_tnl 的 WHERE + ORDER BY 形状
Index(
    "ix_tnl_main_read",
    TaskNodeLight.department_id,
    TaskNodeLight.status,
    TaskNodeLight.started_at.desc(),
    postgresql_where=text("source_run_id IS NOT NULL"),
)
# [codex-2026-04-14] 幂等约束：同 run_id 的 execution/run 节点最多 1 条
Index(
    "ix_tnl_source_run_unique",
    TaskNodeLight.source_run_id,
    unique=True,
    postgresql_where=text(
        "node_type IN ('execution', 'run') AND source_run_id IS NOT NULL"
    ),
)


class TaskTreeRepairQueue(Base):
    """任务树双写补偿队列。

    每当 writer 写入 task_nodes_light 失败时，把原始 payload + 操作类型
    写入本队列；`repair_worker` 每 30s 扫描并按指数退避重试。漂移扫描
    （drift_scan）发现 execution_runs 无对应 light 节点时也入本表。
    """

    __tablename__ = "tasktree_repair_queue"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # 补偿来源：execution_start / execution_finish / heartbeat / drift_scan / ...
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # 指向原始业务记录（如 execution_run.id 或 instance.id）
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    # Writer 方法名：create_execution_node / finish_execution_node / sync_heartbeat
    operation: Mapped[str] = mapped_column(String(50), nullable=False)
    # 重写时的完整参数（JSONB，包含 department_id / run_id / title 等）
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=now_bjt,
        server_default=text("NOW()"),
    )
    # pending / retrying / failed / done
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=now_bjt,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=now_bjt,
        onupdate=now_bjt,
        server_default=text("NOW()"),
    )


Index(
    "ix_trq_scan",
    TaskTreeRepairQueue.status,
    TaskTreeRepairQueue.next_attempt_at,
)
Index(
    "ix_trq_source",
    TaskTreeRepairQueue.source_type,
    TaskTreeRepairQueue.source_ref,
)
# [codex-2026-04-14] 活跃任务唯一约束：同 (source_type, source_ref, operation)
# 在 pending/retrying 下最多 1 条；done/failed 状态释放约束
Index(
    "ix_trq_active_unique",
    TaskTreeRepairQueue.source_type,
    TaskTreeRepairQueue.source_ref,
    TaskTreeRepairQueue.operation,
    unique=True,
    postgresql_where=text("status IN ('pending', 'retrying')"),
)


class DriftScanCursor(Base):
    """[codex-2026-04-14] drift_scan 持久化游标（单例）。

    last_scanned_at 记录上次扫描上限；下一次从 min(last_scanned_at - overlap,
    now - window) 起扫，避免 worker 停摆超过窗口后老缺口永久脱离扫描面。
    """

    __tablename__ = "drift_scan_cursor"
    __table_args__ = (
        CheckConstraint("id = 1", name="drift_scan_cursor_singleton"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default="1")
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_run_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=now_bjt,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=now_bjt,
        onupdate=now_bjt,
        server_default=text("NOW()"),
    )
