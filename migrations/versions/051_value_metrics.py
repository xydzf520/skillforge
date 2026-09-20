"""execution_runs / skills 价值度量字段

Revision ID: 051
Revises: 050
"""

from alembic import op
import sqlalchemy as sa

revision = "051"
down_revision = "050"


def upgrade():
    op.add_column("execution_runs", sa.Column("manual_baseline_minutes", sa.Float()))
    op.add_column("execution_runs", sa.Column("business_value_tag", sa.String(50)))
    op.add_column("execution_runs", sa.Column("business_ref_id", sa.String(200)))
    op.add_column("skills", sa.Column("default_baseline_minutes", sa.Float()))


def downgrade():
    op.drop_column("skills", "default_baseline_minutes")
    op.drop_column("execution_runs", "business_ref_id")
    op.drop_column("execution_runs", "business_value_tag")
    op.drop_column("execution_runs", "manual_baseline_minutes")
