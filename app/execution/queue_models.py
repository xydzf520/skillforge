"""执行任务队列 ORM 模型"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class ExecutionQueueTask(Base):
    """PostgreSQL 持久化执行任务队列"""
    __tablename__ = "execution_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)            # skill_run / playbook_run / test_run
    skill_id: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5)                     # 1=最高, 10=最低
    status: Mapped[str] = mapped_column(String(20), default="pending")            # pending/claimed/running/completed/failed/cancelled
    queue_name: Mapped[str] = mapped_column(String(50), default="default")        # 队列分组
    claimed_by: Mapped[str | None] = mapped_column(String(100))                   # worker ID
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    created_by: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
