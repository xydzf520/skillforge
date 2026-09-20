"""Product asset metadata, review proxies and candidate tag index.

Revision ID: 138_media_library_review_wall_v41
Revises: 137_media_fde_supply_v4
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "138_media_review_wall_v41"
down_revision = "137_media_fde_supply_v4"
branch_labels = None
depends_on = None


JSON_OBJECT = sa.text("'{}'::jsonb")
JSON_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.add_column("media_library_assets", sa.Column("product_key", sa.String(120)))
    op.add_column("media_library_assets", sa.Column("view_type", sa.String(40)))
    op.add_column("media_library_assets", sa.Column("package_count", sa.Integer()))
    op.add_column(
        "media_library_assets",
        sa.Column("aliases_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
    )
    op.add_column("media_library_assets", sa.Column("thumbnail_asset_id", sa.String(50)))
    op.add_column(
        "media_library_assets",
        sa.Column("auto_reference_eligible", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("media_library_assets", sa.Column("import_batch", sa.String(120)))
    op.create_foreign_key(
        "fk_media_library_assets_thumbnail_asset_id",
        "media_library_assets",
        "project_run_assets",
        ["thumbnail_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in (
        "product_key", "view_type", "thumbnail_asset_id", "auto_reference_eligible", "import_batch",
    ):
        op.create_index(f"ix_media_library_assets_{column}", "media_library_assets", [column])

    op.add_column(
        "media_material_requests",
        sa.Column("generation_options_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
    )

    op.add_column("media_candidates", sa.Column("preview_asset_id", sa.String(50)))
    op.create_foreign_key(
        "fk_media_candidates_preview_asset_id",
        "media_candidates",
        "project_run_assets",
        ["preview_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_media_candidates_preview_asset_id", "media_candidates", ["preview_asset_id"])
    op.create_index(
        "ix_media_candidates_tags_gin",
        "media_candidates",
        ["tags_json"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_media_candidates_tags_gin", table_name="media_candidates")
    op.drop_index("ix_media_candidates_preview_asset_id", table_name="media_candidates")
    op.drop_constraint("fk_media_candidates_preview_asset_id", "media_candidates", type_="foreignkey")
    op.drop_column("media_candidates", "preview_asset_id")
    op.drop_column("media_material_requests", "generation_options_json")

    for column in (
        "import_batch", "auto_reference_eligible", "thumbnail_asset_id", "view_type", "product_key",
    ):
        op.drop_index(f"ix_media_library_assets_{column}", table_name="media_library_assets")
    op.drop_constraint("fk_media_library_assets_thumbnail_asset_id", "media_library_assets", type_="foreignkey")
    for column in (
        "import_batch", "auto_reference_eligible", "thumbnail_asset_id", "aliases_json",
        "package_count", "view_type", "product_key",
    ):
        op.drop_column("media_library_assets", column)
