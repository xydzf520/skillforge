"""approval rules, instances, steps

Revision ID: 038
Revises: 037
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "038"
down_revision = "037"


def upgrade():
    op.create_table(
        "approval_rules",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("business_type", sa.String(length=50), nullable=False),
        sa.Column("condition_json", JSONB(), nullable=True),
        sa.Column("approval_chain", JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True, server_default=sa.text("true")),
        sa.Column("priority", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_rules_business_type", "approval_rules", ["business_type"])
    op.create_index("ix_approval_rules_priority", "approval_rules", ["priority"])

    op.create_table(
        "approval_instances",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("rule_id", sa.String(length=50), nullable=True),
        sa.Column("business_type", sa.String(length=50), nullable=False),
        sa.Column("business_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=True, server_default="pending"),
        sa.Column("current_step", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("dingtalk_process_id", sa.String(length=100), nullable=True),
        sa.Column("requester_id", sa.String(length=50), nullable=False),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_instances_rule_id", "approval_instances", ["rule_id"])
    op.create_index("ix_approval_instances_business_type", "approval_instances", ["business_type"])
    op.create_index("ix_approval_instances_business_id", "approval_instances", ["business_id"])
    op.create_index("ix_approval_instances_status", "approval_instances", ["status"])
    op.create_index("ix_approval_instances_requester_id", "approval_instances", ["requester_id"])

    op.create_table(
        "approval_steps",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("instance_id", sa.String(length=50), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("approver_id", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True, server_default="pending"),
        sa.Column("comment", sa.String(length=2000), nullable=True),
        sa.Column("acted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_steps_instance_id", "approval_steps", ["instance_id"])
    op.create_index("ix_approval_steps_approver_id", "approval_steps", ["approver_id"])
    op.create_index("ix_approval_steps_status", "approval_steps", ["status"])


def downgrade():
    op.drop_index("ix_approval_steps_status", table_name="approval_steps")
    op.drop_index("ix_approval_steps_approver_id", table_name="approval_steps")
    op.drop_index("ix_approval_steps_instance_id", table_name="approval_steps")
    op.drop_table("approval_steps")

    op.drop_index("ix_approval_instances_requester_id", table_name="approval_instances")
    op.drop_index("ix_approval_instances_status", table_name="approval_instances")
    op.drop_index("ix_approval_instances_business_id", table_name="approval_instances")
    op.drop_index("ix_approval_instances_business_type", table_name="approval_instances")
    op.drop_index("ix_approval_instances_rule_id", table_name="approval_instances")
    op.drop_table("approval_instances")

    op.drop_index("ix_approval_rules_priority", table_name="approval_rules")
    op.drop_index("ix_approval_rules_business_type", table_name="approval_rules")
    op.drop_table("approval_rules")
