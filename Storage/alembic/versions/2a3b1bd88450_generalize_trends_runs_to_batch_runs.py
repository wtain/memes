"""Generalize trends_runs to batch_runs: rename table, add kind/stage/stats/completed_at/error

Revision ID: 2a3b1bd88450
Revises: 44203e5c987f
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a3b1bd88450'
down_revision: Union[str, Sequence[str], None] = '44203e5c987f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table('trends_runs', 'batch_runs')

    op.add_column('batch_runs', sa.Column('kind', sa.String(length=50), nullable=True))
    op.add_column('batch_runs', sa.Column('stage', sa.String(length=50), nullable=True))
    op.add_column('batch_runs', sa.Column('stats', sa.JSON(), nullable=True))
    op.add_column('batch_runs', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('batch_runs', sa.Column('error', sa.Text(), nullable=True))

    op.execute("UPDATE batch_runs SET kind = 'trends' WHERE kind IS NULL")
    op.alter_column('batch_runs', 'kind', nullable=False)

    op.create_index('ix_batch_runs_kind', 'batch_runs', ['kind'])


def downgrade() -> None:
    connection = op.get_bind()
    non_trends_count = connection.execute(
        sa.text("SELECT count(*) FROM batch_runs WHERE kind != 'trends'")
    ).scalar()
    if non_trends_count:
        raise RuntimeError(
            f"Cannot downgrade: {non_trends_count} batch_runs row(s) have kind other than "
            "'trends' (e.g. 'ingestion'), which cannot be represented in the pre-migration "
            "trends-only schema. Remove or migrate those rows before downgrading."
        )

    op.drop_index('ix_batch_runs_kind', table_name='batch_runs')

    op.drop_column('batch_runs', 'error')
    op.drop_column('batch_runs', 'completed_at')
    op.drop_column('batch_runs', 'stats')
    op.drop_column('batch_runs', 'stage')
    op.drop_column('batch_runs', 'kind')

    op.rename_table('batch_runs', 'trends_runs')
