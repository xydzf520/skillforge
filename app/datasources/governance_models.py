"""数据治理四层模型 ORM：资产 / 产品 / 策略 / 契约"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class DataAsset(Base):
    """数据资产：对 data_source 的业务逻辑层抽象"""
    __tablename__ = "data_assets"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[str | None] = mapped_column(String(50))  # FK → data_sources.id
    asset_type: Mapped[str | None] = mapped_column(String(50))  # table/api/file/stream
    schema_json: Mapped[dict | None] = mapped_column(JSONB)  # [{name, type, description, sensitivity}]
    owner_org_unit_id: Mapped[str | None] = mapped_column(String(100))
    owner_user_id: Mapped[str | None] = mapped_column(String(50))
    sensitivity: Mapped[str] = mapped_column(String(10), default="L2")  # L1/L2/L3/L4
    tags: Mapped[list | None] = mapped_column(ARRAY(Text))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class DataProduct(Base):
    """数据产品：聚合多个资产的可共享消费视图"""
    __tablename__ = "data_products"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    asset_ids: Mapped[list] = mapped_column(ARRAY(Text), nullable=False)  # 聚合的 data_asset ID 列表
    output_schema: Mapped[dict | None] = mapped_column(JSONB)  # 输出数据 schema
    sla_json: Mapped[dict | None] = mapped_column(JSONB)  # {"freshness_hours": 24, "availability": 99.9}
    access_mode: Mapped[str] = mapped_column(String(20), default="read")  # read/read_masked/full
    consumer_count: Mapped[int] = mapped_column(Integer, default=0)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class DataPolicy(Base):
    """数据策略：脱敏 / 导出限制 / 访问控制 / 留存规则"""
    __tablename__ = "data_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    policy_type: Mapped[str] = mapped_column(String(30), nullable=False)  # masking/export_limit/access_control/retention
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)  # asset/product/field
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    rules_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)


class DataContract(Base):
    """数据契约：Skill 使用数据的正式声明（消费/生产）"""
    __tablename__ = "data_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False)  # FK → skills.id
    product_id: Mapped[str | None] = mapped_column(String(100))  # FK → data_products.id
    asset_id: Mapped[str | None] = mapped_column(String(100))  # FK → data_assets.id
    contract_type: Mapped[str] = mapped_column(String(20), default="consumer")  # consumer/producer
    fields_used: Mapped[list | None] = mapped_column(ARRAY(Text))  # Skill 使用的字段列表
    access_level: Mapped[str] = mapped_column(String(20), default="read")
    sla_requirement: Mapped[dict | None] = mapped_column(JSONB)  # Skill 对数据的 SLA 要求
    status: Mapped[str] = mapped_column(String(20), default="active")
    approved_by: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)
