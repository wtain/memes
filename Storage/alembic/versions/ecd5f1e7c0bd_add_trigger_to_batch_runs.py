"""add trigger to batch_runs

Revision ID: ecd5f1e7c0bd
Revises: fa057003a158
Create Date: 2026-07-28 19:02:51.470841

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ecd5f1e7c0bd'
down_revision: Union[str, Sequence[str], None] = 'fa057003a158'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # First statement, before any data touches the table: blocks any concurrent create_run()
    # (scheduler tick, manual trigger) for this whole migration's transaction, closing the
    # window for a race between the cleanup UPDATE below and the index creation.
    op.execute("LOCK TABLE batch_runs IN ACCESS EXCLUSIVE MODE")

    op.add_column('batch_runs', sa.Column('trigger', sa.String(20), nullable=True))

    # Defensive cleanup: if this migration ever runs against data with more than one 'started'
    # row for the same kind (shouldn't happen given existing orphan-recovery elsewhere, but the
    # unique index below would fail outright on dirty data), keep only the most recent per kind
    # and fail the rest.
    op.execute("""
        UPDATE batch_runs SET status = 'failed', completed_at = now(),
               error = 'superseded (migration cleanup)'
        WHERE status = 'started' AND run_id NOT IN (
            SELECT DISTINCT ON (kind) run_id FROM batch_runs
            WHERE status = 'started' ORDER BY kind, created_at DESC
        )
    """)

    op.execute("UPDATE batch_runs SET trigger = 'unknown' WHERE trigger IS NULL")
    op.alter_column('batch_runs', 'trigger', nullable=False)

    op.create_index(
        'ix_batch_runs_one_active_per_kind', 'batch_runs', ['kind'],
        unique=True, postgresql_where=sa.text("status = 'started'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_batch_runs_one_active_per_kind', table_name='batch_runs')
    op.drop_column('batch_runs', 'trigger')
