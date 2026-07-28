"""
Integration tests for trends_batch.py's main()/run() split and tracking behavior.
run()'s actual scraping logic is exercised separately by tests/batch/test_trends_batch.py
(process_source) -- these tests monkeypatch run() to a stub and focus purely on tracking.
"""
from unittest.mock import AsyncMock

import pytest

import batch.run_tracking as run_tracking
import batch.trends_batch as trends_batch
from repository.batch_runs import BatchRunRepository

# tracked_run/finish_existing_run (batch/run_tracking.py) open their own fresh
# AsyncSessionLocal() connections, independent of whatever session main() itself uses.
# The db_session fixture wraps each test in an outer transaction that is only ever
# rolled back (never truly committed) so its writes are invisible to any other real
# connection -- so run_tracking's own AsyncSessionLocal must be patched too, or any
# row created directly via db_session (as in the last test below) is invisible to
# finish_existing_run's lookup on a separate connection.


@pytest.mark.asyncio(loop_scope="session")
async def test_main_creates_completed_run_with_manual_trigger_by_default(db_session, monkeypatch):
    monkeypatch.setattr(trends_batch, "run", AsyncMock())
    monkeypatch.setattr(trends_batch, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))

    await trends_batch.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="trends")
    assert most_recent.trigger == "manual"
    assert most_recent.status == "completed"


@pytest.mark.asyncio(loop_scope="session")
async def test_main_marks_run_failed_when_run_raises(db_session, monkeypatch):
    monkeypatch.setattr(trends_batch, "run", AsyncMock(side_effect=RuntimeError("connector down")))
    monkeypatch.setattr(trends_batch, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))

    with pytest.raises(RuntimeError, match="connector down"):
        await trends_batch.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="trends")
    assert most_recent.status == "failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_main_with_pre_created_run_id_finishes_that_row(db_session, monkeypatch):
    monkeypatch.setattr(trends_batch, "run", AsyncMock())
    monkeypatch.setattr(trends_batch, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    repo = BatchRunRepository(db_session)
    existing_run_id = await repo.create_run(kind="trends", trigger="scheduled")
    await db_session.commit()

    await trends_batch.main(trigger="scheduled", run_id=existing_run_id)

    finished = await repo.get_run(existing_run_id)
    assert finished.status == "completed"


def _session_ctx(session):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()
