"""create user skill ui preferences

Revision ID: 097
Revises: 096
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "097"
down_revision = "096"


def upgrade():
    op.create_table(
        "user_skill_ui_preferences",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("surface", sa.String(length=30), nullable=False),
        sa.Column("base_skill_commit", sa.String(length=40), nullable=True),
        sa.Column(
            "overlay_schema_version",
            sa.String(length=30),
            server_default="skill-ui/v1",
            nullable=False,
        ),
        sa.Column("overlay_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_by", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("prompt_summary", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "skill_id",
            "surface",
            "version",
            name="uq_user_skill_ui_pref_version",
        ),
    )
    op.create_index(
        "ix_user_skill_ui_preferences_user_id",
        "user_skill_ui_preferences",
        ["user_id"],
    )
    op.create_index(
        "ix_user_skill_ui_preferences_skill_id",
        "user_skill_ui_preferences",
        ["skill_id"],
    )
    op.create_index(
        "ix_user_skill_ui_preferences_enabled",
        "user_skill_ui_preferences",
        ["enabled"],
    )
    op.create_index(
        "uq_user_skill_ui_pref_active_enabled",
        "user_skill_ui_preferences",
        ["user_id", "skill_id", "surface"],
        unique=True,
        postgresql_where=sa.text("enabled = true"),
        sqlite_where=sa.text("enabled = 1"),
    )

    op.add_column("skill_submissions", sa.Column("ui_pref_id", sa.String(length=50), nullable=True))
    op.add_column("skill_submissions", sa.Column("ui_pref_version", sa.Integer(), nullable=True))
    op.add_column("skill_submissions", sa.Column("ui_surface", sa.String(length=30), nullable=True))
    op.add_column("skill_submissions", sa.Column("base_skill_commit", sa.String(length=40), nullable=True))
    op.add_column("skill_submissions", sa.Column("merged_ui_schema_hash", sa.String(length=80), nullable=True))
    op.add_column(
        "skill_submissions",
        sa.Column("ui_snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_skill_submissions_ui_pref_id", "skill_submissions", ["ui_pref_id"])


def downgrade():
    op.drop_index("ix_skill_submissions_ui_pref_id", table_name="skill_submissions")
    op.drop_column("skill_submissions", "ui_snapshot_json")
    op.drop_column("skill_submissions", "merged_ui_schema_hash")
    op.drop_column("skill_submissions", "base_skill_commit")
    op.drop_column("skill_submissions", "ui_surface")
    op.drop_column("skill_submissions", "ui_pref_version")
    op.drop_column("skill_submissions", "ui_pref_id")

    op.drop_index("uq_user_skill_ui_pref_active_enabled", table_name="user_skill_ui_preferences")
    op.drop_index("ix_user_skill_ui_preferences_enabled", table_name="user_skill_ui_preferences")
    op.drop_index("ix_user_skill_ui_preferences_skill_id", table_name="user_skill_ui_preferences")
    op.drop_index("ix_user_skill_ui_preferences_user_id", table_name="user_skill_ui_preferences")
    op.drop_table("user_skill_ui_preferences")
