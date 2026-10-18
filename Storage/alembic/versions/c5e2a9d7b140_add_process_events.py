"""add process_events

Revision ID: c5e2a9d7b140
Revises: b8d4f1a6c935
Create Date: 2026-10-18 12:00:00.000000

Epic screenshot-recut, project M1 (ADR-002). Append-only table of process outcomes; rates are computed at
read time. batch_run_id is SET NULL so pruning old batch runs never deletes events.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c5e2a9d7b140'
down_revision: Union[str, Sequence[str], None] = 'b8d4f1a6c935'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'process_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('process', sa.String(length=40), nullable=False),
        sa.Column('event', sa.String(length=40), nullable=False),
        sa.Column('batch_run_id', sa.UUID(), nullable=True),
        sa.Column('subject_id', sa.UUID(), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_run_id'], ['batch_runs.run_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('process', 'event', 'subject_id', name='uq_process_events_subject_event'),
    )
    op.create_index('ix_process_events_process_run_event', 'process_events', ['process', 'batch_run_id', 'event'])


def downgrade() -> None:
    op.drop_index('ix_process_events_process_run_event', table_name='process_events')
    op.drop_table('process_events')
