"""todo dispatch default executor preferences

Revision ID: 079
Revises: 078
"""

from alembic import op
import sqlalchemy as sa

revision = "079"
down_revision = "078"


def upgrade():
    op.create_table(
        "todo_dispatch_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.String(length=50), nullable=False),
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("target_org_unit_id", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("department", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("last_executor_id", sa.String(length=50), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint(
            "actor_id",
            "skill_id",
            "target_org_unit_id",
            "department",
            name="uq_todo_dispatch_pref_scope",
        ),
    )
    op.create_index(
        "ix_todo_dispatch_pref_actor_skill",
        "todo_dispatch_preferences",
        ["actor_id", "skill_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_todo_dispatch_pref_actor_skill", table_name="todo_dispatch_preferences")
    op.drop_table("todo_dispatch_preferences")
