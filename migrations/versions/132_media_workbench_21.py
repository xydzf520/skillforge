"""material workbench 2.1 simplified production and review fields

Revision ID: 132_media_workbench_21
Revises: 131_media_workbench_v2
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "132_media_workbench_21"
down_revision = "131_media_workbench_v2"
branch_labels = None
depends_on = None

JSON_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.add_column("media_production_batches", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.create_unique_constraint(
        "uq_media_batches_project_idempotency",
        "media_production_batches",
        ["project_id", "idempotency_key"],
    )
    additions = (
        ("business_title", sa.String(240)),
        ("output_preset_id", sa.String(50)),
        ("poster_asset_id", sa.String(50)),
        ("source_roles_json", postgresql.JSONB()),
        ("creative_option", sa.String(50)),
        ("job_group_id", sa.String(80)),
    )
    for name, type_ in additions:
        op.add_column("media_generation_jobs", sa.Column(name, type_, nullable=True))
    op.create_foreign_key(
        "fk_media_jobs_poster_asset",
        "media_generation_jobs",
        "project_run_assets",
        ["poster_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute("UPDATE media_generation_jobs SET source_roles_json = '[]'::jsonb WHERE source_roles_json IS NULL")
    op.alter_column("media_generation_jobs", "source_roles_json", nullable=False, server_default=JSON_ARRAY)
    for column in (
        "business_title", "output_preset_id", "poster_asset_id", "creative_option", "job_group_id",
    ):
        op.create_index(f"ix_media_generation_jobs_{column}", "media_generation_jobs", [column])


def downgrade() -> None:
    for column in (
        "business_title", "output_preset_id", "poster_asset_id", "creative_option", "job_group_id",
    ):
        op.drop_index(f"ix_media_generation_jobs_{column}", table_name="media_generation_jobs")
    op.drop_constraint("fk_media_jobs_poster_asset", "media_generation_jobs", type_="foreignkey")
    for name in (
        "job_group_id", "creative_option", "source_roles_json", "poster_asset_id", "output_preset_id", "business_title",
    ):
        op.drop_column("media_generation_jobs", name)
    op.drop_constraint("uq_media_batches_project_idempotency", "media_production_batches", type_="unique")
    op.drop_column("media_production_batches", "idempotency_key")
