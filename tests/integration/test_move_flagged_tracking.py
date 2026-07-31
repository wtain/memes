"""
Integration tests for move_flagged.py's new main() tracking behavior. run()'s actual
file-moving logic is unchanged and untested here (out of scope for this change) --
run() itself is monkeypatched to a no-op/raising stub so these tests focus purely on
whether main() creates/finishes the right BatchRun row.

run()'s mock returns a real (empty) SimpleMetricsListener rather than a bare AsyncMock
sentinel -- main() now unconditionally calls metrics.counters_dict() and unpacks it into
update_stats(), which requires a real mapping, not a MagicMock. unregister_deleted_images.main
is mocked in every test here -- main() now unconditionally chains a real call to it, which
would otherwise create a genuine batch_runs row against this test DB and run
UnregisterNonExisting.run() for real (deleting any image row whose file is missing on disk).
"""
import uuid
from unittest.mock import AsyncMock

import pytest

import batch.move_flagged as move_flagged
import batch.run_tracking as run_tracking
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository

# tracked_run/finish_existing_run (batch/run_tracking.py) open their own fresh
# AsyncSessionLocal() connections, independent of whatever session main() itself uses.
# The db_session fixture wraps each test in an outer transaction that is only ever
# rolled back (never truly committed) so its writes are invisible to any other real
# connection -- so run_tracking's own AsyncSessionLocal must be patched too, or any
# row created directly via db_session (as in the last test below) is invisible to
# finish_existing_run's lookup on a separate connection.


@pytest.mark.asyncio(loop_scope="session")
async def test_main_self_tracks_as_manual_by_default(db_session, monkeypatch):
    monkeypatch.setattr(move_flagged, "run", AsyncMock(return_value=SimpleMetricsListener()))
    monkeypatch.setattr(move_flagged, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(move_flagged.unregister_deleted_images, "main", AsyncMock())

    await move_flagged.main()

    repo = BatchRunRepository(db_session)
    active = await repo.get_active_run(kind="move_flagged")
    # main() commits on success, so nothing should still be "active" (started)
    assert active is None


@pytest.mark.asyncio(loop_scope="session")
async def test_main_creates_completed_run_with_manual_trigger(db_session, monkeypatch):
    monkeypatch.setattr(move_flagged, "run", AsyncMock(return_value=SimpleMetricsListener()))
    monkeypatch.setattr(move_flagged, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    unregister_main = AsyncMock()
    monkeypatch.setattr(move_flagged.unregister_deleted_images, "main", unregister_main)

    await move_flagged.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="move_flagged")
    assert most_recent is not None
    assert most_recent.trigger == "manual"
    assert most_recent.status == "completed"
    unregister_main.assert_awaited_once_with(trigger="manual")


@pytest.mark.asyncio(loop_scope="session")
async def test_main_marks_run_failed_when_run_raises(db_session, monkeypatch):
    monkeypatch.setattr(move_flagged, "run", AsyncMock(side_effect=RuntimeError("disk full")))
    monkeypatch.setattr(move_flagged, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    unregister_main = AsyncMock()
    monkeypatch.setattr(move_flagged.unregister_deleted_images, "main", unregister_main)

    with pytest.raises(RuntimeError, match="disk full"):
        await move_flagged.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="move_flagged")
    assert most_recent.status == "failed"
    assert most_recent.error == "disk full"
    # run() raised before main() ever reaches the chained call
    unregister_main.assert_not_called()


@pytest.mark.asyncio(loop_scope="session")
async def test_main_with_pre_created_run_id_finishes_that_row_not_a_new_one(db_session, monkeypatch):
    monkeypatch.setattr(move_flagged, "run", AsyncMock(return_value=SimpleMetricsListener()))
    monkeypatch.setattr(move_flagged, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(move_flagged.unregister_deleted_images, "main", AsyncMock())
    repo = BatchRunRepository(db_session)
    existing_run_id = await repo.create_run(kind="move_flagged", trigger="manual")
    await db_session.commit()

    await move_flagged.main(trigger="manual", run_id=existing_run_id)

    finished = await repo.get_run(existing_run_id)
    assert finished.status == "completed"
    # No second row was created for this kind
    all_recent = await repo.get_most_recent_run(kind="move_flagged")
    assert all_recent.run_id == existing_run_id


def _session_ctx(session):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()
