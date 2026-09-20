"""material workbench editable video Agents

Revision ID: 136_media_workbench_agents
Revises: 135_media_workbench_v3
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "136_media_workbench_agents"
down_revision = "135_media_workbench_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_workbench_agent_profiles",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("project_id", sa.String(42), nullable=False),
        sa.Column("user_id", sa.String(50), nullable=False),
        sa.Column("agent_key", sa.String(60), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("model", sa.String(180), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("default_prompt_sha256", sa.String(64), nullable=False),
        sa.Column("updated_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "project_id", "user_id", "agent_key",
            name="uq_media_workbench_agent_project_user_key",
        ),
    )
    for column in ("project_id", "user_id", "agent_key", "updated_by"):
        op.create_index(
            f"ix_media_workbench_agent_profiles_{column}",
            "media_workbench_agent_profiles",
            [column],
        )
    op.create_index(
        "ix_media_workbench_agent_project_user_updated",
        "media_workbench_agent_profiles",
        ["project_id", "user_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_table("media_workbench_agent_profiles")
