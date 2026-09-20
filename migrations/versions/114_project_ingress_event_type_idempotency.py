"""project ingress event type idempotency

Revision ID: 114
Revises: 113
Create Date: 2026-06-03
"""

from alembic import op


revision = "114"
down_revision = "113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_project_ingress_run_request", "project_ingress_events", type_="unique")
    op.create_unique_constraint(
        "uq_project_ingress_run_event_request",
        "project_ingress_events",
        ["project_run_id", "event_type", "request_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_project_ingress_run_event_request", "project_ingress_events", type_="unique")
    op.create_unique_constraint(
        "uq_project_ingress_run_request",
        "project_ingress_events",
        ["project_run_id", "request_id"],
    )
