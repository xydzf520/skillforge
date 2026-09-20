"""create project sdk tokens

Revision ID: 126_project_sdk_tokens
Revises: 125_training_model_transfers
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "126_project_sdk_tokens"
down_revision = "125_training_model_transfers"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "project_sdk_tokens",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("project_id", sa.String(length=42), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_prefix", sa.String(length=30), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("scopes_json", JSONB, nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_project_sdk_tokens_token_hash"),
        sa.UniqueConstraint("token_prefix", name="uq_project_sdk_tokens_token_prefix"),
    )
    op.create_index("ix_project_sdk_tokens_project_id", "project_sdk_tokens", ["project_id"])
    op.create_index("ix_project_sdk_tokens_created_by", "project_sdk_tokens", ["created_by"])
    op.create_index("ix_project_sdk_tokens_expires_at", "project_sdk_tokens", ["expires_at"])
    op.create_index("ix_project_sdk_tokens_revoked_at", "project_sdk_tokens", ["revoked_at"])
    op.create_index("ix_project_sdk_tokens_token_hash", "project_sdk_tokens", ["token_hash"])
    op.create_index("ix_project_sdk_tokens_token_prefix", "project_sdk_tokens", ["token_prefix"])
    op.create_index("ix_project_sdk_tokens_project_created", "project_sdk_tokens", ["project_id", "created_at"])
    op.create_index(
        "ix_project_sdk_tokens_project_active",
        "project_sdk_tokens",
        ["project_id", "revoked_at", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_sdk_tokens_project_active", table_name="project_sdk_tokens")
    op.drop_index("ix_project_sdk_tokens_project_created", table_name="project_sdk_tokens")
    op.drop_index("ix_project_sdk_tokens_token_prefix", table_name="project_sdk_tokens")
    op.drop_index("ix_project_sdk_tokens_token_hash", table_name="project_sdk_tokens")
    op.drop_index("ix_project_sdk_tokens_revoked_at", table_name="project_sdk_tokens")
    op.drop_index("ix_project_sdk_tokens_expires_at", table_name="project_sdk_tokens")
    op.drop_index("ix_project_sdk_tokens_created_by", table_name="project_sdk_tokens")
    op.drop_index("ix_project_sdk_tokens_project_id", table_name="project_sdk_tokens")
    op.drop_table("project_sdk_tokens")
