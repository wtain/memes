"""Row locks of the comics repository with two real sessions (C1 follow-up). The repository takes
SELECT ... FOR UPDATE (it waits, no NOWAIT): a second writer blocks until the first transaction ends."""
import asyncio
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from repository.comics import ComicsError, ComicsRepository
from Storage.models import Comics

GUARD = 2.0  # seconds: how long a blocked call may take after the lock is released
BLOCKED_FOR = 0.5  # how long a call is watched to confirm it is waiting


OPERATIONS = {
    "rename": lambda repo, cid: repo.rename(cid, f"Renamed {uuid.uuid4()}"),
    "delete": lambda repo, cid: repo.delete(cid),
    "mark_built": lambda repo, cid: repo.mark_built(cid, uuid.uuid4(), "h"),
    "get_lock": lambda repo, cid: repo.get(cid, lock=True),
}


@pytest.mark.parametrize("operation", list(OPERATIONS))
@pytest.mark.asyncio(loop_scope="session")
async def test_row_locks_serialise_writers(db_engine, operation):
    async with AsyncSession(db_engine, expire_on_commit=False) as s:
        comics = await ComicsRepository(s).create(f"Lock {uuid.uuid4()}")
        await s.commit()
        comics_id = comics.id
    try:
        async with AsyncSession(db_engine) as a, AsyncSession(db_engine) as b:
            await ComicsRepository(a).get(comics_id, lock=True)
            task = asyncio.ensure_future(OPERATIONS[operation](ComicsRepository(b), comics_id))
            done, _ = await asyncio.wait({task}, timeout=BLOCKED_FOR)
            assert not done, f"{operation} proceeded while another transaction held the row lock"
            await a.rollback()  # releases the lock
            try:
                await asyncio.wait_for(task, GUARD)
            except ComicsError:
                pass  # mark_built then refuses (too few panels): it waited, which is what is pinned
            finally:
                await b.rollback()
    finally:
        async with AsyncSession(db_engine) as s:
            await s.execute(delete(Comics).where(Comics.id == comics_id))
            await s.commit()
