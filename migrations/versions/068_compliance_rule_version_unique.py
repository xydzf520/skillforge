"""add unique constraint for compliance rule version numbers

Revision ID: 068
Revises: 067
"""

from alembic import op


revision = "068"
down_revision = "067"


def upgrade():
    op.execute(
        """
        WITH dupes AS (
            SELECT
                id,
                rule_id,
                version_no,
                created_at,
                ROW_NUMBER() OVER (
                    PARTITION BY rule_id, version_no
                    ORDER BY created_at, id
                ) AS dup_rank,
                MAX(version_no) OVER (PARTITION BY rule_id) AS max_version_no
            FROM compliance_rule_versions
        ),
        reassigned AS (
            SELECT
                id,
                max_version_no + ROW_NUMBER() OVER (
                    PARTITION BY rule_id
                    ORDER BY version_no, created_at, id
                ) AS new_version_no
            FROM dupes
            WHERE dup_rank > 1
        )
        UPDATE compliance_rule_versions AS target
        SET version_no = reassigned.new_version_no
        FROM reassigned
        WHERE target.id = reassigned.id
        """
    )
    op.create_unique_constraint(
        "uq_compliance_rule_versions_rule_version_no",
        "compliance_rule_versions",
        ["rule_id", "version_no"],
    )


def downgrade():
    op.drop_constraint(
        "uq_compliance_rule_versions_rule_version_no",
        "compliance_rule_versions",
        type_="unique",
    )
