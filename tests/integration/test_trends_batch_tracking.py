"""
Integration tests for trends_batch.py's main()/run() split and tracking behavior.
run()'s actual scraping logic is exercised separately by tests/batch/test_trends_batch.py
(process_source) -- these tests monkeypatch run() to a stub and focus purely on tracking.
"""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

import batch.run_tracking as run_tracking
import batch.trends_batch as trends_batch
from repository.batch_runs import BatchRunRepository
from repository.trends import TrendsRunResultRepository
from Storage.db import AsyncSessionLocal as real_async_session_local
from Storage.models import BatchRun, TrendSource, TrendsRunResult

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


@pytest.mark.asyncio(loop_scope="session")
async def test_main_persists_trends_run_result_rows_to_a_separate_connection(db_engine, monkeypatch):
    """
    db_engine is requested (but otherwise unused) purely to guarantee the schema
    has been created before this test runs -- it's the only thing in this test
    file that never also depends on db_session, and db_engine's schema creation
    is what every other test in this file gets for free by depending on db_session.
    Regression test for the bug the code review caught: main() opened its own
    AsyncSessionLocal() session, passed it into run(), but never committed it --
    TrendsRunResultRepository.add_result() only add()s + flush()es (repositories
    don't commit, callers do, per this repo's convention), so every TrendsRunResult
    row was silently discarded when the uncommitted session was closed.

    Deliberately does NOT monkeypatch AsyncSessionLocal anywhere (unlike the tests
    above) -- main() and run_tracking use the real Storage.db.AsyncSessionLocal,
    hitting the real test database over genuinely independent connections, exactly
    like production. This is intentional: reusing the db_session fixture (as the
    tests above do, for the *tracking* assertions) wraps the write and the
    verification query in the very same already-open transaction/session, so an
    uncommitted write would still be visible to a query on that same session
    regardless of whether commit() ever ran -- that setup could not have caught
    this bug. Only a check from a truly separate connection (matching how the
    reviewer found the bug in the first place) can tell "flushed" apart from
    "committed".
    """
    source_id = None
    real_run_id = None
    try:
        # Seed a TrendSource for real (its own transaction, truly committed) so
        # it's visible to whatever fresh connection main() itself opens.
        async with real_async_session_local() as setup_session:
            source = TrendSource(name="persistence-test-source", connector_type="rss", config={})
            setup_session.add(source)
            await setup_session.commit()
            source_id = source.id

        async def _stub_run(session, run_id):
            # A real add_result() call through the real repository -- exactly the
            # write path that was losing data -- rather than an AsyncMock that
            # never touches TrendsRunResultRepository at all.
            nonlocal real_run_id
            real_run_id = run_id
            repo = TrendsRunResultRepository(session, run_id)
            await repo.add_result(source_id=source_id, label="test-label", name="test-name", value=7)

        monkeypatch.setattr(trends_batch, "run", _stub_run)

        await trends_batch.main()

        # Verify via a THIRD, brand-new connection -- never touched by main()'s own
        # session or the setup session above.
        async with real_async_session_local() as verify_session:
            result = await verify_session.execute(
                select(TrendsRunResult).where(TrendsRunResult.run_id == real_run_id)
            )
            rows = result.scalars().all()

        assert len(rows) == 1
        assert rows[0].label == "test-label"
        assert rows[0].name == "test-name"
        assert rows[0].value == 7
        assert rows[0].source_id == source_id
    finally:
        async with real_async_session_local() as cleanup_session:
            if real_run_id is not None:
                # Cascades (ondelete="CASCADE") to any trends_run_results row too.
                await cleanup_session.execute(delete(BatchRun).where(BatchRun.run_id == real_run_id))
            if source_id is not None:
                await cleanup_session.execute(delete(TrendSource).where(TrendSource.id == source_id))
            await cleanup_session.commit()


def _session_ctx(session):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()
