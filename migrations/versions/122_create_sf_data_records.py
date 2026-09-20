"""create sf data records

Revision ID: 122_create_sf_data_records
Revises: 121_ai_todo_feedback_payload
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "122_create_sf_data_records"
down_revision = "121_ai_todo_feedback_payload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sf_data_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("namespace", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=True),
        sa.Column("content_type", sa.String(length=40), nullable=False),
        sa.Column("data_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("text_preview", sa.Text(), nullable=True),
        sa.Column("artifact_ref_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("schema_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=True),
        sa.Column("source_tool", sa.String(length=160), nullable=True),
        sa.Column("source_ref", sa.String(length=160), nullable=True),
        sa.Column("skill_id", sa.String(length=100), nullable=True),
        sa.Column("run_id", sa.String(length=80), nullable=True),
        sa.Column("user_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("visibility", sa.String(length=30), server_default="global", nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_sf_data_records_idempotency_key"),
    )
    op.create_index(op.f("ix_sf_data_records_content_type"), "sf_data_records", ["content_type"], unique=False)
    op.create_index(op.f("ix_sf_data_records_created_at"), "sf_data_records", ["created_at"], unique=False)
    op.create_index(op.f("ix_sf_data_records_department"), "sf_data_records", ["department"], unique=False)
    op.create_index(op.f("ix_sf_data_records_idempotency_key"), "sf_data_records", ["idempotency_key"], unique=False)
    op.create_index("ix_sf_data_records_namespace_created", "sf_data_records", ["namespace", "created_at"], unique=False)
    op.create_index(op.f("ix_sf_data_records_namespace"), "sf_data_records", ["namespace"], unique=False)
    op.create_index(op.f("ix_sf_data_records_run_id"), "sf_data_records", ["run_id"], unique=False)
    op.create_index(op.f("ix_sf_data_records_sha256"), "sf_data_records", ["sha256"], unique=False)
    op.create_index("ix_sf_data_records_skill_created", "sf_data_records", ["skill_id", "created_at"], unique=False)
    op.create_index(op.f("ix_sf_data_records_skill_id"), "sf_data_records", ["skill_id"], unique=False)
    op.create_index(op.f("ix_sf_data_records_source"), "sf_data_records", ["source"], unique=False)
    op.create_index(op.f("ix_sf_data_records_source_tool"), "sf_data_records", ["source_tool"], unique=False)
    op.create_index(op.f("ix_sf_data_records_user_id"), "sf_data_records", ["user_id"], unique=False)
    op.create_index(op.f("ix_sf_data_records_visibility"), "sf_data_records", ["visibility"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sf_data_records_visibility"), table_name="sf_data_records")
    op.drop_index(op.f("ix_sf_data_records_user_id"), table_name="sf_data_records")
    op.drop_index(op.f("ix_sf_data_records_source_tool"), table_name="sf_data_records")
    op.drop_index(op.f("ix_sf_data_records_source"), table_name="sf_data_records")
    op.drop_index(op.f("ix_sf_data_records_skill_id"), table_name="sf_data_records")
    op.drop_index("ix_sf_data_records_skill_created", table_name="sf_data_records")
    op.drop_index(op.f("ix_sf_data_records_sha256"), table_name="sf_data_records")
    op.drop_index(op.f("ix_sf_data_records_run_id"), table_name="sf_data_records")
    op.drop_index(op.f("ix_sf_data_records_namespace"), table_name="sf_data_records")
    op.drop_index("ix_sf_data_records_namespace_created", table_name="sf_data_records")
    op.drop_index(op.f("ix_sf_data_records_idempotency_key"), table_name="sf_data_records")
    op.drop_index(op.f("ix_sf_data_records_department"), table_name="sf_data_records")
    op.drop_index(op.f("ix_sf_data_records_created_at"), table_name="sf_data_records")
    op.drop_index(op.f("ix_sf_data_records_content_type"), table_name="sf_data_records")
    op.drop_table("sf_data_records")
