"""File side effects of a recut request, tied to the transaction that get_async_db owns (screenshot recut R6).
A file is only removed or copied once the database outcome is known: the old cut file after the commit, a freshly
written cut file if the transaction rolls back. Failures here are logged and never fail the request."""
import logging
import os
import shutil
from typing import Callable

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

PRE_CUTS_DIR = "pre-cuts"


def remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("recut: could not remove %s", path)


def copy_to_pre_cuts(base_path: str, filename: str) -> None:
    """Back up the source file of an approved cut next to the library, never overwriting (R6, decision 7)."""
    source = os.path.join(base_path, filename)
    target = os.path.join(base_path, PRE_CUTS_DIR, os.path.basename(filename))
    try:
        if os.path.exists(target):
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
    except OSError:
        logger.exception("recut: could not copy %s to pre-cuts", source)


def file_effects(session: AsyncSession, on_commit: Callable[[], None] | None = None,
                 on_rollback: Callable[[], None] | None = None) -> None:
    """Run `on_commit` once after the session's outermost transaction commits, or `on_rollback` once after it rolls
    back; whichever happens first cancels the other. A savepoint rolling back is not the request failing."""
    sync = session.sync_session
    state = {"done": False}

    def settle(action: Callable[[], None] | None) -> None:
        if state["done"]:
            return
        state["done"] = True  # the listeners stay attached (removing one inside its own dispatch breaks it); the session is per request
        if action is None:
            return
        try:
            action()
        except Exception:
            logger.exception("recut: a file action after the transaction failed")

    def committed(_session) -> None:
        settle(on_commit)

    def rolled_back(_session, previous_transaction) -> None:
        if previous_transaction.parent is None:
            settle(on_rollback)

    event.listen(sync, "after_commit", committed)
    event.listen(sync, "after_soft_rollback", rolled_back)
