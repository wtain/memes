import uuid
from contextlib import asynccontextmanager

from repository.batch_runs import BatchRunRepository
from Storage.db import AsyncSessionLocal


@asynccontextmanager
async def tracked_run(kind: str, trigger: str):
    """Self-creates a run, yields its run_id, commits/fails it around the wrapped work.
    Used by each script's own main() for direct-CLI/self-tracked use, and by the wrapper
    when no run_id was pre-created (the scheduler's case)."""
    async with AsyncSessionLocal() as session:
        repo = BatchRunRepository(session)
        run_id = await repo.create_run(kind=kind, trigger=trigger)
        await session.commit()
    try:
        yield run_id
    except Exception as e:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).fail(run_id, error=str(e))
            await session.commit()
        raise
    else:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).commit(run_id)
            await session.commit()


async def record_stats(run_id: uuid.UUID, stats: dict) -> None:
    """Merges stats into a run's stats (BatchRunRepository.update_stats merges, not replaces).
    Call inside tracked_run/finish_existing_run so the run's final commit keeps them."""
    async with AsyncSessionLocal() as session:
        await BatchRunRepository(session).update_stats(run_id, **stats)
        await session.commit()


@asynccontextmanager
async def finish_existing_run(run_id: uuid.UUID):
    """Commits/fails a run_id the CALLER already created (the admin endpoint's case, where
    the run_id must exist synchronously before the subprocess is even spawned)."""
    try:
        yield
    except Exception as e:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).fail(run_id, error=str(e))
            await session.commit()
        raise
    else:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).commit(run_id)
            await session.commit()
