"""市场认证记录 ORM 模型"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class MarketCertification(Base):
    """市场认证记录：Skill 上架市场后的认证审核"""
    __tablename__ = "market_certifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending/approved/rejected
    reviewer_id: Mapped[str | None] = mapped_column(String(50))
    review_notes: Mapped[str | None] = mapped_column(Text)
    certified_at: Mapped[datetime | None] = mapped_column(DateTime)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    submitted_by: Mapped[str] = mapped_column(String(50), nullable=False)


class MarketRating(Base):
    """用户对市场 Skill 的评分。"""
    __tablename__ = "market_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("skill_id", "user_id", name="uq_market_ratings_skill_user"),
    )
