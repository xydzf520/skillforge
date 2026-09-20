"""create project run assets

Revision ID: 119
Revises: 118
Create Date: 2026-06-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "119"
down_revision = "118"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "project_run_assets",
        sa.Column("id", sa.String(length=50), primary_key=True),
        sa.Column("project_id", sa.String(length=42), nullable=False),
        sa.Column("project_run_id", sa.String(length=50), nullable=False),
        sa.Column("owner_user_id", sa.String(length=50), nullable=True),
        sa.Column("department_id", sa.String(length=50), nullable=True),
        sa.Column("source_kind", sa.String(length=30), nullable=False, server_default="file"),
        sa.Column("file_name", sa.String(length=240), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_backend", sa.String(length=30), nullable=False, server_default="local"),
        sa.Column("storage_path", sa.String(length=1000), nullable=False),
        sa.Column("metadata_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_run_id", "sha256", name="uq_project_run_assets_run_sha256"),
    )
    op.create_index("ix_project_run_assets_project_id", "project_run_assets", ["project_id"])
    op.create_index("ix_project_run_assets_project_run_id", "project_run_assets", ["project_run_id"])
    op.create_index("ix_project_run_assets_owner_user_id", "project_run_assets", ["owner_user_id"])
    op.create_index("ix_project_run_assets_department_id", "project_run_assets", ["department_id"])
    op.create_index("ix_project_run_assets_source_kind", "project_run_assets", ["source_kind"])
    op.create_index("ix_project_run_assets_sha256", "project_run_assets", ["sha256"])
    op.create_index("ix_project_run_assets_run_created", "project_run_assets", ["project_run_id", "created_at"])
    op.create_index("ix_project_run_assets_project_created", "project_run_assets", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_project_run_assets_project_created", table_name="project_run_assets")
    op.drop_index("ix_project_run_assets_run_created", table_name="project_run_assets")
    op.drop_index("ix_project_run_assets_sha256", table_name="project_run_assets")
    op.drop_index("ix_project_run_assets_source_kind", table_name="project_run_assets")
    op.drop_index("ix_project_run_assets_department_id", table_name="project_run_assets")
    op.drop_index("ix_project_run_assets_owner_user_id", table_name="project_run_assets")
    op.drop_index("ix_project_run_assets_project_run_id", table_name="project_run_assets")
    op.drop_index("ix_project_run_assets_project_id", table_name="project_run_assets")
    op.drop_table("project_run_assets")
