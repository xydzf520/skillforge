"""钉钉消息发送队列（outbox模式）ORM 模型"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class DingTalkOutbox(Base):
    __tablename__ = "dingtalk_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_type: Mapped[str] = mapped_column(String(20), nullable=False)  # work_notice/interactive_card/approval
    priority: Mapped[int] = mapped_column(Integer, default=5)  # 1最高(审批) 3中(任务) 5低(报告)
    recipient_user_id: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/sending/sent/failed/expired
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_message: Mapped[str | None] = mapped_column(Text)
    related_type: Mapped[str | None] = mapped_column(String(30))  # skill_execution/review/approval
    related_id: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
