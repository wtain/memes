"""batch_runs.progress: live progress of a started batch run

Revision ID: c4f8a1d7e925
Revises: b7e2d4a9c615
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4f8a1d7e925"
down_revision = "b7e2d4a9c615"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batch_runs", sa.Column("progress", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("batch_runs", "progress")
