"""create training asset registry

Revision ID: 127_training_asset_registry
Revises: 126_project_sdk_tokens
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "127_training_asset_registry"
down_revision = "126_project_sdk_tokens"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "training_storage_locations",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("backend", sa.String(length=40), nullable=False, server_default="local"),
        sa.Column("uri", sa.String(length=1000), nullable=True),
        sa.Column("gateway_id", sa.String(length=80), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
        sa.Column("capabilities_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("retention_policy_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("encryption_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_training_storage_locations_backend", "training_storage_locations", ["backend"])
    op.create_index("ix_training_storage_locations_gateway_id", "training_storage_locations", ["gateway_id"])
    op.create_index("ix_training_storage_locations_department", "training_storage_locations", ["department"])
    op.create_index("ix_training_storage_locations_org_unit_id", "training_storage_locations", ["org_unit_id"])
    op.create_index("ix_training_storage_locations_status", "training_storage_locations", ["status"])
    op.create_index("ix_training_storage_locations_created_by", "training_storage_locations", ["created_by"])
    op.create_index("ix_training_storage_locations_created_at", "training_storage_locations", ["created_at"])

    op.create_table(
        "training_asset_sources",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("owner_user_id", sa.String(length=50), nullable=True),
        sa.Column("skill_id", sa.String(length=100), nullable=True),
        sa.Column("project_id", sa.String(length=42), nullable=True),
        sa.Column("project_run_id", sa.String(length=50), nullable=True),
        sa.Column("modality", sa.String(length=30), nullable=False, server_default="text"),
        sa.Column("storage_location_id", sa.String(length=50), nullable=True),
        sa.Column("storage_backend", sa.String(length=40), nullable=True),
        sa.Column("storage_ref", sa.String(length=1000), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sensitivity_level", sa.String(length=20), nullable=False, server_default="internal"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
        sa.Column("policy_result_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["storage_location_id"], ["training_storage_locations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_type", "source_id", name="uq_training_asset_sources_source"),
    )
    for column in (
        "source_type",
        "source_id",
        "department",
        "org_unit_id",
        "owner_user_id",
        "skill_id",
        "project_id",
        "project_run_id",
        "modality",
        "storage_location_id",
        "storage_backend",
        "mime_type",
        "sha256",
        "sensitivity_level",
        "status",
        "created_by",
        "created_at",
    ):
        op.create_index(f"ix_training_asset_sources_{column}", "training_asset_sources", [column])
    op.create_index(
        "ix_training_asset_sources_scope_created",
        "training_asset_sources",
        ["department", "org_unit_id", "created_at"],
    )

    op.create_table(
        "training_samples",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.String(length=50), nullable=True),
        sa.Column("dataset_profile", sa.String(length=50), nullable=False),
        sa.Column("modality", sa.String(length=30), nullable=False, server_default="text"),
        sa.Column("sample_hash", sa.String(length=64), nullable=False),
        sa.Column("content_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("media_refs_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("labels_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("skill_id", sa.String(length=100), nullable=True),
        sa.Column("project_id", sa.String(length=42), nullable=True),
        sa.Column("project_run_id", sa.String(length=50), nullable=True),
        sa.Column("sensitivity_level", sa.String(length=20), nullable=False, server_default="internal"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ready"),
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["source_id"], ["training_asset_sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "dataset_profile", "sample_hash", name="uq_training_samples_source_profile_hash"),
    )
    for column in (
        "source_id",
        "dataset_profile",
        "modality",
        "sample_hash",
        "department",
        "org_unit_id",
        "skill_id",
        "project_id",
        "project_run_id",
        "sensitivity_level",
        "status",
        "created_by",
        "created_at",
    ):
        op.create_index(f"ix_training_samples_{column}", "training_samples", [column])
    op.create_index(
        "ix_training_samples_scope_profile",
        "training_samples",
        ["department", "org_unit_id", "dataset_profile", "created_at"],
    )

    op.create_table(
        "training_dataset_versions",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=60), nullable=False),
        sa.Column("dataset_profile", sa.String(length=50), nullable=False),
        sa.Column("modality", sa.String(length=30), nullable=False, server_default="text"),
        sa.Column("target_model_family", sa.String(length=120), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ready"),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("media_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("byte_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("manifest_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("storage_location_id", sa.String(length=50), nullable=True),
        sa.Column("target_gateway_id", sa.String(length=80), nullable=True),
        sa.Column("sync_status", sa.String(length=30), nullable=False, server_default="not_synced"),
        sa.Column("synced_sink_job_id", sa.String(length=50), nullable=True),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("approved_by", sa.String(length=50), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["storage_location_id"], ["training_storage_locations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("department", "name", "version", name="uq_training_dataset_versions_department_name_version"),
    )
    for column in (
        "dataset_profile",
        "modality",
        "target_model_family",
        "department",
        "org_unit_id",
        "status",
        "manifest_sha256",
        "storage_location_id",
        "target_gateway_id",
        "sync_status",
        "synced_sink_job_id",
        "created_by",
        "approved_by",
        "created_at",
    ):
        op.create_index(f"ix_training_dataset_versions_{column}", "training_dataset_versions", [column])
    op.create_index(
        "ix_training_dataset_versions_scope_created",
        "training_dataset_versions",
        ["department", "org_unit_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_training_dataset_versions_scope_created", table_name="training_dataset_versions")
    for column in (
        "created_at",
        "approved_by",
        "created_by",
        "synced_sink_job_id",
        "sync_status",
        "target_gateway_id",
        "storage_location_id",
        "manifest_sha256",
        "status",
        "org_unit_id",
        "department",
        "target_model_family",
        "modality",
        "dataset_profile",
    ):
        op.drop_index(f"ix_training_dataset_versions_{column}", table_name="training_dataset_versions")
    op.drop_table("training_dataset_versions")

    op.drop_index("ix_training_samples_scope_profile", table_name="training_samples")
    for column in (
        "created_at",
        "created_by",
        "status",
        "sensitivity_level",
        "project_run_id",
        "project_id",
        "skill_id",
        "org_unit_id",
        "department",
        "sample_hash",
        "modality",
        "dataset_profile",
        "source_id",
    ):
        op.drop_index(f"ix_training_samples_{column}", table_name="training_samples")
    op.drop_table("training_samples")

    op.drop_index("ix_training_asset_sources_scope_created", table_name="training_asset_sources")
    for column in (
        "created_at",
        "created_by",
        "status",
        "sensitivity_level",
        "sha256",
        "mime_type",
        "storage_backend",
        "storage_location_id",
        "modality",
        "project_run_id",
        "project_id",
        "skill_id",
        "owner_user_id",
        "org_unit_id",
        "department",
        "source_id",
        "source_type",
    ):
        op.drop_index(f"ix_training_asset_sources_{column}", table_name="training_asset_sources")
    op.drop_table("training_asset_sources")

    for column in (
        "created_at",
        "created_by",
        "status",
        "org_unit_id",
        "department",
        "gateway_id",
        "backend",
    ):
        op.drop_index(f"ix_training_storage_locations_{column}", table_name="training_storage_locations")
    op.drop_table("training_storage_locations")
