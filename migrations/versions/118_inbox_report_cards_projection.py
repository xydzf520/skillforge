"""create inbox report card projection

Revision ID: 118
Revises: 117
Create Date: 2026-06-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "118"
down_revision = "117"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "inbox_report_cards",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("decision_log_id", sa.Integer(), nullable=False),
        sa.Column("report_index", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=50), nullable=True),
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("channel", sa.String(length=80), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metrics", JSONB, nullable=True),
        sa.Column("tags", JSONB, nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("decision_log_id", "report_index", name="uq_inbox_report_cards_log_index"),
    )
    op.create_index("ix_inbox_report_cards_created", "inbox_report_cards", ["created_at"])
    op.create_index("ix_inbox_report_cards_skill_created", "inbox_report_cards", ["skill_id", "created_at"])
    op.create_index("ix_inbox_report_cards_channel_created", "inbox_report_cards", ["channel", "created_at"])

    op.execute(
        """
        INSERT INTO inbox_report_cards (
            decision_log_id,
            report_index,
            run_id,
            skill_id,
            created_at,
            channel,
            title,
            summary,
            metrics,
            tags,
            updated_at
        )
        SELECT
            dl.id,
            rep.ordinality - 1,
            dl.run_id,
            dl.skill_id,
            dl.created_at,
            NULLIF(BTRIM(COALESCE(rep.report ->> 'channel', '')), ''),
            LEFT(BTRIM(rep.report ->> 'title'), 200),
            LEFT(BTRIM(rep.report ->> 'summary'), 800),
            CASE
                WHEN jsonb_typeof(rep.report -> 'metrics') = 'array'
                    THEN rep.report -> 'metrics'
                ELSE '[]'::jsonb
            END,
            CASE
                WHEN jsonb_typeof(rep.report -> 'tags') = 'array'
                    THEN rep.report -> 'tags'
                ELSE '[]'::jsonb
            END,
            NOW()
        FROM (
            SELECT id, run_id, skill_id, created_at, output_result
            FROM decision_log
            WHERE is_sandbox = false
              AND jsonb_typeof(output_result -> 'reports') = 'array'
        ) dl
        CROSS JOIN LATERAL jsonb_array_elements(dl.output_result -> 'reports')
            WITH ORDINALITY AS rep(report, ordinality)
        WHERE jsonb_typeof(rep.report) = 'object'
          AND NULLIF(BTRIM(COALESCE(rep.report ->> 'title', '')), '') IS NOT NULL
          AND NULLIF(BTRIM(COALESCE(rep.report ->> 'summary', '')), '') IS NOT NULL
        ON CONFLICT (decision_log_id, report_index) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_inbox_report_cards_channel_created", table_name="inbox_report_cards")
    op.drop_index("ix_inbox_report_cards_skill_created", table_name="inbox_report_cards")
    op.drop_index("ix_inbox_report_cards_created", table_name="inbox_report_cards")
    op.drop_table("inbox_report_cards")
