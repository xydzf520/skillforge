"""create direct capability tasks

Revision ID: 093
Revises: 092
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "093"
down_revision = "092"


def upgrade():
    op.create_table(
        "direct_capability_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(50), nullable=False),
        sa.Column("capability_id", sa.String(100), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=True),
        sa.Column("workspace_name", sa.String(120), nullable=True),
        sa.Column("upstream_task_id", sa.String(120), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_poll_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_direct_capability_tasks_user_id", "direct_capability_tasks", ["user_id"])
    op.create_index("ix_direct_capability_tasks_capability_id", "direct_capability_tasks", ["capability_id"])
    op.create_index("ix_direct_capability_tasks_workspace_id", "direct_capability_tasks", ["workspace_id"])
    op.create_index("ix_direct_capability_tasks_upstream_task_id", "direct_capability_tasks", ["upstream_task_id"])
    op.create_index("ix_direct_capability_tasks_status", "direct_capability_tasks", ["status"])
    op.create_index("ix_direct_capability_tasks_next_poll_at", "direct_capability_tasks", ["next_poll_at"])
    op.create_index("ix_direct_capability_tasks_created_at", "direct_capability_tasks", ["created_at"])


def downgrade():
    op.drop_index("ix_direct_capability_tasks_created_at", table_name="direct_capability_tasks")
    op.drop_index("ix_direct_capability_tasks_next_poll_at", table_name="direct_capability_tasks")
    op.drop_index("ix_direct_capability_tasks_status", table_name="direct_capability_tasks")
    op.drop_index("ix_direct_capability_tasks_upstream_task_id", table_name="direct_capability_tasks")
    op.drop_index("ix_direct_capability_tasks_workspace_id", table_name="direct_capability_tasks")
    op.drop_index("ix_direct_capability_tasks_capability_id", table_name="direct_capability_tasks")
    op.drop_index("ix_direct_capability_tasks_user_id", table_name="direct_capability_tasks")
    op.drop_table("direct_capability_tasks")
