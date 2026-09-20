"""todo dispatch preference object key

Revision ID: 080
Revises: 079
"""

from alembic import op
import sqlalchemy as sa

revision = "080"
down_revision = "079"


def upgrade():
    op.add_column(
        "todo_dispatch_preferences",
        sa.Column(
            "dispatch_object_key",
            sa.String(length=120),
            nullable=False,
            server_default="",
        ),
    )
    op.drop_constraint(
        "uq_todo_dispatch_pref_scope",
        "todo_dispatch_preferences",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_todo_dispatch_pref_scope_object",
        "todo_dispatch_preferences",
        [
            "actor_id",
            "skill_id",
            "target_org_unit_id",
            "department",
            "dispatch_object_key",
        ],
    )
    op.create_index(
        "ix_todo_dispatch_pref_actor_skill_object",
        "todo_dispatch_preferences",
        ["actor_id", "skill_id", "dispatch_object_key"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_todo_dispatch_pref_actor_skill_object",
        table_name="todo_dispatch_preferences",
    )
    op.drop_constraint(
        "uq_todo_dispatch_pref_scope_object",
        "todo_dispatch_preferences",
        type_="unique",
    )
    op.execute(
        """
        DELETE FROM todo_dispatch_preferences p
        USING todo_dispatch_preferences newer
        WHERE p.actor_id = newer.actor_id
          AND p.skill_id = newer.skill_id
          AND p.target_org_unit_id = newer.target_org_unit_id
          AND p.department = newer.department
          AND (
              newer.updated_at > p.updated_at
              OR (newer.updated_at = p.updated_at AND newer.id > p.id)
          )
        """
    )
    op.create_unique_constraint(
        "uq_todo_dispatch_pref_scope",
        "todo_dispatch_preferences",
        ["actor_id", "skill_id", "target_org_unit_id", "department"],
    )
    op.drop_column("todo_dispatch_preferences", "dispatch_object_key")
