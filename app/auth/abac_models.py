"""ABAC 策略 ORM 模型"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class AbacPolicy(Base):
    """属性级访问控制策略。

    每条策略匹配一组 (resource_type, action)，通过 subject / resource /
    environment 三类条件判定是否放行或拒绝。多条策略按 priority 降序排列，
    第一条匹配的决定结果。
    """
    __tablename__ = "abac_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)   # skill / datasource / execution
    action: Mapped[str] = mapped_column(String(50), nullable=False)          # read / edit / execute / publish / export
    effect: Mapped[str] = mapped_column(String(10), server_default="deny", nullable=False)  # allow / deny
    priority: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)       # 高优先

    # 条件 JSON（复用 condition_engine 评估格式）
    subject_condition: Mapped[dict | None] = mapped_column(JSONB)      # 用户属性条件
    resource_condition: Mapped[dict | None] = mapped_column(JSONB)     # 资源属性条件
    environment_condition: Mapped[dict | None] = mapped_column(JSONB)  # 环境条件

    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)
