"""browser automation tables

Revision ID: 029
Revises: 028
Create Date: 2026-04-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, server_default="default"),
        sa.Column("container_id", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False, server_default="stopped"),
        sa.Column("cdp_url", sa.String(255)),
        sa.Column("novnc_url", sa.String(255)),
        sa.Column("department", sa.String(50)),
        sa.Column("created_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_table(
        "collection_tasks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("browser_session_id", sa.Integer, sa.ForeignKey("browser_sessions.id")),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("params", JSONB),
        sa.Column("result", JSONB),
        sa.Column("error_message", sa.Text),
        sa.Column("created_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime),
    )


def downgrade() -> None:
    op.drop_table("collection_tasks")
    op.drop_table("browser_sessions")
