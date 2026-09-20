"""数据治理四层模型：data_assets / data_products / data_policies / data_contracts

Revision ID: 047
Revises: 030
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "047"
down_revision = "046"


def upgrade():
    # 数据资产（逻辑层：对 data_source 的业务抽象）
    op.create_table(
        "data_assets",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("source_id", sa.String(50), sa.ForeignKey("data_sources.id")),
        sa.Column("asset_type", sa.String(50)),  # table/api/file/stream
        sa.Column("schema_json", JSONB),  # [{name, type, description, sensitivity}]
        sa.Column("owner_org_unit_id", sa.String(100)),
        sa.Column("owner_user_id", sa.String(50)),
        sa.Column("sensitivity", sa.String(10), server_default="L2"),  # L1/L2/L3/L4
        sa.Column("tags", ARRAY(sa.Text)),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # 数据产品（可共享消费视图）
    op.create_table(
        "data_products",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("asset_ids", ARRAY(sa.Text), nullable=False),  # 聚合的 data_asset ID 列表
        sa.Column("output_schema", JSONB),  # 输出数据 schema
        sa.Column("sla_json", JSONB),  # {"freshness_hours": 24, "availability": 99.9}
        sa.Column("access_mode", sa.String(20), server_default="read"),  # read/read_masked/full
        sa.Column("consumer_count", sa.Integer, server_default="0"),
        sa.Column("published", sa.Boolean, server_default="false"),
        sa.Column("owner_id", sa.String(50)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # 数据策略（脱敏/导出/访问规则）
    op.create_table(
        "data_policies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("policy_type", sa.String(30), nullable=False),  # masking/export_limit/access_control/retention
        sa.Column("target_type", sa.String(30), nullable=False),  # asset/product/field
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("rules_json", JSONB, nullable=False),
        sa.Column("enabled", sa.Boolean, server_default="true"),
        sa.Column("priority", sa.Integer, server_default="0"),
        sa.Column("created_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_data_policies_target", "data_policies", ["target_type", "target_id"])

    # 数据契约（Skill 使用数据的声明）
    op.create_table(
        "data_contracts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.String(100), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("product_id", sa.String(100), sa.ForeignKey("data_products.id")),
        sa.Column("asset_id", sa.String(100), sa.ForeignKey("data_assets.id")),
        sa.Column("contract_type", sa.String(20), server_default="consumer"),  # consumer/producer
        sa.Column("fields_used", ARRAY(sa.Text)),  # Skill 使用的字段列表
        sa.Column("access_level", sa.String(20), server_default="read"),
        sa.Column("sla_requirement", JSONB),  # Skill 对数据的 SLA 要求
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("approved_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_data_contracts_skill", "data_contracts", ["skill_id"])


def downgrade():
    op.drop_table("data_contracts")
    op.drop_table("data_policies")
    op.drop_table("data_products")
    op.drop_table("data_assets")
