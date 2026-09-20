"""create collection proofs

Revision ID: 086
Revises: 085
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "086"
down_revision = "085"


def upgrade():
    op.create_table(
        "collection_proofs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("proof_id", sa.String(length=80), nullable=False),
        sa.Column("run_id", sa.String(length=100), nullable=True),
        sa.Column("skill_id", sa.String(length=100), nullable=True),
        sa.Column("mcp_tool_name", sa.String(length=100), nullable=True),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("shop_id", sa.String(length=50), nullable=False),
        sa.Column("data_scope", sa.String(length=80), nullable=False),
        sa.Column("endpoint_family", sa.String(length=80), nullable=True),
        sa.Column("warning_group", sa.String(length=80), nullable=True),
        sa.Column("credential_scope", sa.String(length=160), nullable=True),
        sa.Column("credential_plan_id", sa.String(length=100), nullable=True),
        sa.Column("cookie_pool_id", sa.BigInteger(), nullable=True),
        sa.Column("credential_alias", sa.String(length=80), nullable=True),
        sa.Column("browser_slot_id", sa.String(length=80), nullable=True),
        sa.Column("retry_of_proof_id", sa.String(length=80), nullable=True),
        sa.Column("fallback_cookie_pool_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("data_keys", postgresql.JSONB(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("warning_signal", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("proof_id", name="uq_collection_proofs_proof_id"),
    )
    op.create_index("ix_collection_proofs_proof_id", "collection_proofs", ["proof_id"])
    op.create_index("ix_collection_proofs_run_scope", "collection_proofs", ["run_id", "data_scope", "created_at"])
    op.create_index("ix_collection_proofs_cookie_time", "collection_proofs", ["cookie_pool_id", "created_at"])
    op.create_index("ix_collection_proofs_skill_run", "collection_proofs", ["skill_id", "run_id"])
    op.create_index("ix_collection_proofs_mcp_tool_name", "collection_proofs", ["mcp_tool_name"])


def downgrade():
    op.drop_index("ix_collection_proofs_skill_run", table_name="collection_proofs")
    op.drop_index("ix_collection_proofs_mcp_tool_name", table_name="collection_proofs")
    op.drop_index("ix_collection_proofs_cookie_time", table_name="collection_proofs")
    op.drop_index("ix_collection_proofs_run_scope", table_name="collection_proofs")
    op.drop_index("ix_collection_proofs_proof_id", table_name="collection_proofs")
    op.drop_table("collection_proofs")
