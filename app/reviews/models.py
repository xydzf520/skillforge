"""审核请求 + 评论 ORM 模型"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import now_bjt
from app.database import Base


def _now_bjt():
    return now_bjt()


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False)
    submitter: Mapped[str] = mapped_column(String(50), nullable=False)
    reviewer: Mapped[str | None] = mapped_column(String(50))
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)  # params/logic/code/new_skill
    diff_summary: Mapped[str | None] = mapped_column(Text)
    diff_content: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/approved/rejected
    git_commit_before: Mapped[str | None] = mapped_column(String(40))
    git_commit_after: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    dingtalk_msg_id: Mapped[str | None] = mapped_column(String(100))
    review_feedback: Mapped[dict | None] = mapped_column(JSONB)


class ReviewComment(Base):
    __tablename__ = "review_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False,
    )  # 索引在 migration 003
    author: Mapped[str | None] = mapped_column(String(50))
    content: Mapped[str | None] = mapped_column(Text)
    # 行级评论支持（nullable，兼容现有数据）
    file_path: Mapped[str | None] = mapped_column(String(255))
    line_number: Mapped[int | None] = mapped_column(Integer)
    side: Mapped[str | None] = mapped_column(String(10))  # "left" / "right"
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_by: Mapped[str | None] = mapped_column(String(50))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_bjt)
