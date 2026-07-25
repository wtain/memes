"""Add images.status (pending/active/rejected) and images.ingestion_batch_id

Revision ID: 51b3d777ab8a
Revises: 2a3b1bd88450
Create Date: 2026-07-25 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '51b3d777ab8a'
down_revision: Union[str, Sequence[str], None] = '2a3b1bd88450'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('images', sa.Column('status', sa.String(length=20), nullable=True))
    op.execute("UPDATE images SET status = 'active' WHERE status IS NULL")
    op.alter_column('images', 'status', nullable=False, server_default='active')

    op.add_column(
        'images',
        sa.Column('ingestion_batch_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'images_ingestion_batch_id_fkey', 'images', 'batch_runs',
        ['ingestion_batch_id'], ['run_id'], ondelete='SET NULL',
    )

    # Partial index sized to the small pending minority, not the whole (overwhelmingly
    # active) table -- see 2026-07-25-image-visibility-status-design.md.
    op.create_index(
        'ix_images_status_pending', 'images', ['id'],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index('ix_images_status_pending', table_name='images')
    op.drop_constraint('images_ingestion_batch_id_fkey', 'images', type_='foreignkey')
    op.drop_column('images', 'ingestion_batch_id')
    op.drop_column('images', 'status')
