"""drop orphaned runstatus enum types

Revision ID: a545480b9388
Revises: 3ce6b76e0b01
Create Date: 2026-09-12 19:37:21.324555

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a545480b9388'
down_revision: Union[str, Sequence[str], None] = '3ce6b76e0b01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `runstatus` (created by fb26ce64e8b5, 2026-04-24) and `runstatus1` (created implicitly by
    # 972d165a99fb the same day) both backed `trends_runs.status`. Two later same-day migrations
    # (cddcbebd682f, then 65281efbcb60) converted that column away from each native enum type in
    # turn -- to a non-native sa.Enum, then to a plain sa.String -- but neither DROP'd the PG type
    # it had just orphaned. No column anywhere references either type today (confirmed: no model
    # in Storage/models.py declares them), so they've sat unused in every environment's database
    # since April, invisible to SQLAlchemy's create_all()/drop_all() (which only manage types a
    # *current* model column actually uses) -- which is exactly why a from-scratch `alembic
    # upgrade head` (e.g. rebuilding the disposable test database) hits `DuplicateObject` the
    # moment this migration chain tries to (re)create them, without ever getting the chance to
    # clean them up itself. IF EXISTS makes this safely re-runnable regardless of which of the
    # two is (or isn't) still present on a given environment.
    op.execute("DROP TYPE IF EXISTS runstatus1")
    op.execute("DROP TYPE IF EXISTS runstatus")


def downgrade() -> None:
    # Recreate exactly as the original migrations defined them (fb26ce64e8b5, 972d165a99fb) --
    # nothing references either type, so this only restores the types themselves, not any column.
    sa.Enum('started', 'completed', 'failed', name='runstatus').create(op.get_bind(), checkfirst=True)
    sa.Enum('started', 'completed', 'failed', name='runstatus1', create_constraint=False).create(
        op.get_bind(), checkfirst=True)
