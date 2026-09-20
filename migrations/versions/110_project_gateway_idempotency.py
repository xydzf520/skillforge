"""project gateway idempotency and call payloads

Revision ID: 110
Revises: 109
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "110"
down_revision = "109"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("project_ingress_events", sa.Column("request_id", sa.String(length=80), nullable=True))
    op.create_index("ix_project_ingress_events_request_id", "project_ingress_events", ["request_id"])
    op.create_unique_constraint("uq_project_ingress_run_request", "project_ingress_events", ["project_run_id", "request_id"])

    op.add_column("project_capability_calls", sa.Column("request_id", sa.String(length=80), nullable=True))
    op.add_column("project_capability_calls", sa.Column("input_payload", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")))
    op.add_column("project_capability_calls", sa.Column("output_result", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")))
    op.create_index("ix_project_capability_calls_request_id", "project_capability_calls", ["request_id"])
    op.create_unique_constraint("uq_project_capability_run_request", "project_capability_calls", ["project_run_id", "request_id"])


def downgrade() -> None:
    op.drop_constraint("uq_project_capability_run_request", "project_capability_calls", type_="unique")
    op.drop_index("ix_project_capability_calls_request_id", table_name="project_capability_calls")
    op.drop_column("project_capability_calls", "output_result")
    op.drop_column("project_capability_calls", "input_payload")
    op.drop_column("project_capability_calls", "request_id")

    op.drop_constraint("uq_project_ingress_run_request", "project_ingress_events", type_="unique")
    op.drop_index("ix_project_ingress_events_request_id", table_name="project_ingress_events")
    op.drop_column("project_ingress_events", "request_id")
