"""add cascade foreign keys for review and workbench tables

Revision ID: 073
Revises: 072
Create Date: 2026-04-23
"""

from alembic import op


revision = "073"
down_revision = "072"
branch_labels = None
depends_on = None


REVIEW_FK = "fk_review_comments_review_id_reviews"

WORKBENCH_FKS = [
    (
        "skill_workbench_messages",
        "fk_wb_messages_session_id_sessions",
        "session_id",
        "skill_workbench_sessions",
        "id",
        "skill_workbench_messages_session_id_fkey",
    ),
    (
        "skill_workbench_patches",
        "fk_wb_patches_session_id_sessions",
        "session_id",
        "skill_workbench_sessions",
        "id",
        "skill_workbench_patches_session_id_fkey",
    ),
    (
        "skill_workbench_validation_runs",
        "fk_wb_validation_runs_patch_id_patches",
        "patch_id",
        "skill_workbench_patches",
        "id",
        "skill_workbench_validation_runs_patch_id_fkey",
    ),
    (
        "skill_workbench_references",
        "fk_wb_references_patch_id_patches",
        "patch_id",
        "skill_workbench_patches",
        "id",
        "skill_workbench_references_patch_id_fkey",
    ),
    (
        "skill_workbench_ai_runs",
        "fk_wb_ai_runs_session_id_sessions",
        "session_id",
        "skill_workbench_sessions",
        "id",
        "skill_workbench_ai_runs_session_id_fkey",
    ),
    (
        "skill_workbench_ai_runs",
        "fk_wb_ai_runs_patch_id_patches",
        "patch_id",
        "skill_workbench_patches",
        "id",
        "skill_workbench_ai_runs_patch_id_fkey",
    ),
]


def _drop_constraint_if_exists(table: str, name: str) -> None:
    op.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{name}"')


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM review_comments rc
        WHERE NOT EXISTS (
            SELECT 1 FROM reviews r WHERE r.id = rc.review_id
        )
        """
    )
    op.execute(
        """
        DELETE FROM skill_workbench_ai_runs ar
        WHERE NOT EXISTS (
            SELECT 1 FROM skill_workbench_sessions s WHERE s.id = ar.session_id
        )
        OR (
            ar.patch_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM skill_workbench_patches p WHERE p.id = ar.patch_id
            )
        )
        """
    )
    op.execute(
        """
        DELETE FROM skill_workbench_validation_runs vr
        WHERE NOT EXISTS (
            SELECT 1 FROM skill_workbench_patches p WHERE p.id = vr.patch_id
        )
        """
    )
    op.execute(
        """
        DELETE FROM skill_workbench_references ref
        WHERE NOT EXISTS (
            SELECT 1 FROM skill_workbench_patches p WHERE p.id = ref.patch_id
        )
        """
    )
    op.execute(
        """
        DELETE FROM skill_workbench_messages msg
        WHERE NOT EXISTS (
            SELECT 1 FROM skill_workbench_sessions s WHERE s.id = msg.session_id
        )
        """
    )
    op.execute(
        """
        DELETE FROM skill_workbench_patches p
        WHERE NOT EXISTS (
            SELECT 1 FROM skill_workbench_sessions s WHERE s.id = p.session_id
        )
        """
    )

    _drop_constraint_if_exists("review_comments", "review_comments_review_id_fkey")
    _drop_constraint_if_exists("review_comments", REVIEW_FK)
    op.create_foreign_key(
        REVIEW_FK,
        "review_comments",
        "reviews",
        ["review_id"],
        ["id"],
        ondelete="CASCADE",
    )

    for table, name, column, target_table, target_column, old_name in WORKBENCH_FKS:
        _drop_constraint_if_exists(table, old_name)
        _drop_constraint_if_exists(table, name)
        op.create_foreign_key(
            name,
            table,
            target_table,
            [column],
            [target_column],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    _drop_constraint_if_exists("review_comments", REVIEW_FK)

    for table, name, column, target_table, target_column, _old_name in WORKBENCH_FKS:
        _drop_constraint_if_exists(table, name)
        op.create_foreign_key(
            name,
            table,
            target_table,
            [column],
            [target_column],
        )
