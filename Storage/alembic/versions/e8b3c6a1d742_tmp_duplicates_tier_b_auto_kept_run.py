"""tmp_duplicates.tier_b_auto_kept_run: which ingest_text_filter run settled a Tier B pair

Revision ID: e8b3c6a1d742
Revises: c4f8a1d7e925
Create Date: 2026-11-26

Nullable, no foreign key (a run id tag, so `--undo --run-id` can find the pairs). See
docs/superpowers/specs/2026-11-26-ingestion-tier-b-text-filter-design.md.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e8b3c6a1d742"
down_revision = "c4f8a1d7e925"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tmp_duplicates", sa.Column("tier_b_auto_kept_run", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(
        "idx_tmp_duplicates_tier_b_auto_kept_run", "tmp_duplicates", ["tier_b_auto_kept_run"],
        postgresql_where=sa.text("tier_b_auto_kept_run IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_tmp_duplicates_tier_b_auto_kept_run", table_name="tmp_duplicates")
    op.drop_column("tmp_duplicates", "tier_b_auto_kept_run")
