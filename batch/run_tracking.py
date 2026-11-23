import logging
import uuid
from contextlib import asynccontextmanager

from batch.utils.run_progress import current_run_id, drain_progress
from repository.batch_runs import BatchRunRepository
from Storage.db import AsyncSessionLocal

log = logging.getLogger(__name__)


async def _drain(run_id: uuid.UUID) -> None:
    """Lands the run's last progress value before the run is finished. Never raises (except
    cancellation): a drain problem must neither fail a successful run nor hide the error of a
    failing one."""
    try:
        await drain_progress(run_id)
    except Exception as e:
        log.warning("batch progress drain failed (%s)", type(e).__name__)


class _RunContext:
    """Exposes run_id as current_run_id while the wrapped work runs (batch/utils/run_progress.py)
    and restores the previous value (a driver's own run id, or None) once the run is drained."""

    def __init__(self, run_id: uuid.UUID) -> None:
        self._token = current_run_id.set(run_id)

    def reset(self) -> None:
        if self._token is None:
            return
        token, self._token = self._token, None
        try:
            current_run_id.reset(token)
        except ValueError:  # exited in another context: restore the value, not the token
            current_run_id.set(token.old_value if token.old_value is not token.MISSING else None)


async def _finish(run_id: uuid.UUID, ctx: _RunContext, error: Exception | None) -> None:
    # Drain first, then reset current_run_id, so a report made after the drain (e.g. during the
    # final commit/fail) finds no run and cannot create new state or a new write task.
    await _drain(run_id)
    ctx.reset()
    async with AsyncSessionLocal() as session:
        repo = BatchRunRepository(session)
        if error is None:
            await repo.commit(run_id)
        else:
            await repo.fail(run_id, error=str(error))
        await session.commit()


@asynccontextmanager
async def tracked_run(kind: str, trigger: str):
    """Self-creates a run, yields its run_id, commits/fails it around the wrapped work.
    Used by each script's own main() for direct-CLI/self-tracked use, and by the wrapper
    when no run_id was pre-created (the scheduler's case). Inside the block
    current_run_id.get() is this run's id (progress reporting)."""
    async with AsyncSessionLocal() as session:
        repo = BatchRunRepository(session)
        run_id = await repo.create_run(kind=kind, trigger=trigger)
        await session.commit()
    ctx = _RunContext(run_id)
    try:
        yield run_id
    except Exception as e:
        await _finish(run_id, ctx, e)
        raise
    else:
        await _finish(run_id, ctx, None)
    finally:
        ctx.reset()


async def record_stats(run_id: uuid.UUID, stats: dict) -> None:
    """Merges stats into a run's stats (BatchRunRepository.update_stats merges, not replaces).
    Call inside tracked_run/finish_existing_run so the run's final commit keeps them."""
    async with AsyncSessionLocal() as session:
        await BatchRunRepository(session).update_stats(run_id, **stats)
        await session.commit()


@asynccontextmanager
async def finish_existing_run(run_id: uuid.UUID):
    """Commits/fails a run_id the CALLER already created (the admin endpoint's case, where
    the run_id must exist synchronously before the subprocess is even spawned). Inside the
    block current_run_id.get() is this run's id (progress reporting)."""
    ctx = _RunContext(run_id)
    try:
        yield
    except Exception as e:
        await _finish(run_id, ctx, e)
        raise
    else:
        await _finish(run_id, ctx, None)
    finally:
        ctx.reset()
