"""
Integration tests for unregister_deleted_images.py's new main() tracking behavior. run()'s
actual file-unregistering logic is unchanged and untested here (out of scope for this
change) -- run() itself is monkeypatched to a no-op/raising stub so these tests focus
purely on whether main() creates/finishes the right BatchRun row.
"""
import uuid
from unittest.mock import AsyncMock

import pytest

import batch.unregister_deleted_images as unregister_deleted_images
import batch.run_tracking as run_tracking
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
    monkeypatch.setattr(unregister_deleted_images, "run", AsyncMock())
    monkeypatch.setattr(unregister_deleted_images, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))

    await unregister_deleted_images.main()

    repo = BatchRunRepository(db_session)
    active = await repo.get_active_run(kind="unregister_deleted_images")
    # main() commits on success, so nothing should still be "active" (started)
    assert active is None


@pytest.mark.asyncio(loop_scope="session")
async def test_main_creates_completed_run_with_manual_trigger(db_session, monkeypatch):
    monkeypatch.setattr(unregister_deleted_images, "run", AsyncMock())
    monkeypatch.setattr(unregister_deleted_images, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))

    await unregister_deleted_images.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="unregister_deleted_images")
    assert most_recent is not None
    assert most_recent.trigger == "manual"
    assert most_recent.status == "completed"


@pytest.mark.asyncio(loop_scope="session")
async def test_main_marks_run_failed_when_run_raises(db_session, monkeypatch):
    monkeypatch.setattr(unregister_deleted_images, "run", AsyncMock(side_effect=RuntimeError("disk full")))
    monkeypatch.setattr(unregister_deleted_images, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))

    with pytest.raises(RuntimeError, match="disk full"):
        await unregister_deleted_images.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="unregister_deleted_images")
    assert most_recent.status == "failed"
    assert most_recent.error == "disk full"


@pytest.mark.asyncio(loop_scope="session")
async def test_main_with_pre_created_run_id_finishes_that_row_not_a_new_one(db_session, monkeypatch):
    monkeypatch.setattr(unregister_deleted_images, "run", AsyncMock())
    monkeypatch.setattr(unregister_deleted_images, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    repo = BatchRunRepository(db_session)
    existing_run_id = await repo.create_run(kind="unregister_deleted_images", trigger="manual")
    await db_session.commit()

    await unregister_deleted_images.main(trigger="manual", run_id=existing_run_id)

    finished = await repo.get_run(existing_run_id)
    assert finished.status == "completed"
    # No second row was created for this kind
    all_recent = await repo.get_most_recent_run(kind="unregister_deleted_images")
    assert all_recent.run_id == existing_run_id


def _session_ctx(session):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()
