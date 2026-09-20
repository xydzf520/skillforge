"""ORM models for governed collection proofs and schema snapshots."""

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.common.time_utils import now_bjt
from app.database import Base


class CollectionProof(Base):
    """Lightweight evidence for one governed collection fetch."""

    __tablename__ = "collection_proofs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    proof_id = Column(String(80), nullable=False, unique=True, index=True)
    run_id = Column(String(100), index=True)
    skill_id = Column(String(100), index=True)
    mcp_tool_name = Column(String(100), index=True)
    platform = Column(String(30), nullable=False)
    shop_id = Column(String(50), nullable=False)
    data_scope = Column(String(80), nullable=False)
    endpoint_family = Column(String(80))
    warning_group = Column(String(80))
    credential_scope = Column(String(160))
    credential_plan_id = Column(String(100))
    cookie_pool_id = Column(BigInteger)
    credential_alias = Column(String(80))
    browser_slot_id = Column(String(80))
    retry_of_proof_id = Column(String(80))
    fallback_cookie_pool_id = Column(BigInteger)
    status = Column(String(40), nullable=False)
    http_status = Column(Integer)
    error_code = Column(String(80))
    response_hash = Column(String(64))
    data_keys = Column(JSONB)
    row_count = Column(Integer)
    warning_signal = Column(JSONB)
    created_at = Column(DateTime, nullable=False, default=now_bjt)


class PlatformApiSchemaSnapshot(Base):
    """Schema snapshot captured from successful platform API responses."""

    __tablename__ = "platform_api_schema_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "shop_id",
            "endpoint_hash",
            "source_run_id",
            name="uq_platform_api_schema_snapshot_run",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    platform = Column(String(30), nullable=False, index=True)
    shop_id = Column(String(50))
    endpoint_hash = Column(String(64), nullable=False, index=True)
    endpoint = Column(Text, nullable=False)
    response_keys = Column(JSONB)
    row_count = Column(Integer)
    response_hash = Column(String(64))
    source_run_id = Column(String(100), index=True)
    captured_at = Column(DateTime, nullable=False, default=now_bjt)
