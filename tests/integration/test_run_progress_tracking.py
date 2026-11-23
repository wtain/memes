"""Integration tests: tracked_run / finish_existing_run expose their run id to progress
reporting (batch/utils/run_progress.py) and drain the pending write before finishing the run.

Every AsyncSessionLocal (run_tracking's and the one write_progress imports from Storage.db) is
patched to the test's db_session: that session's outer transaction is only ever rolled back, so
rows written through it are invisible to any other connection.
"""
import pytest

import batch.run_tracking as run_tracking
import Storage.db as storage_db
from batch.utils import run_progress as rp
from repository.batch_runs import BatchRunRepository


def _session_ctx(session):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


@pytest.fixture
def patched(db_session, monkeypatch):
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(storage_db, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(rp, "_config", lambda: (True, 10.0))
    rp._states.clear()
    yield db_session
    rp._states.clear()


async def _reload(session, run_id):
    run = await BatchRunRepository(session).get_run(run_id)
    await session.refresh(run)
    return run


@pytest.mark.asyncio(loop_scope="session")
async def test_tracked_run_sets_and_resets_current_run_id(patched):
    assert rp.current_run_id.get() is None
    async with run_tracking.tracked_run(kind="progress_track_a", trigger="manual") as run_id:
        assert rp.current_run_id.get() == run_id
    assert rp.current_run_id.get() is None


@pytest.mark.asyncio(loop_scope="session")
async def test_nested_tracked_runs_use_their_own_id(patched):
    async with run_tracking.tracked_run(kind="progress_driver_a", trigger="manual") as driver_id:
        async with run_tracking.tracked_run(kind="progress_child_b", trigger="manual") as child_id:
            assert rp.current_run_id.get() == child_id
        assert child_id != driver_id
        assert rp.current_run_id.get() == driver_id
    assert rp.current_run_id.get() is None


@pytest.mark.asyncio(loop_scope="session")
async def test_report_progress_lands_on_the_run_row(patched):
    async with run_tracking.tracked_run(kind="progress_track_c", trigger="manual") as run_id:
        rp.report_progress("x", "images", 3, 10)

    run = await _reload(patched, run_id)
    assert run.status == "completed"
    assert run.progress["done"] == 3
    assert run.progress["total"] == 10
    assert run.progress["unit"] == "images"
    assert run_id not in rp._states


@pytest.mark.asyncio(loop_scope="session")
async def test_throttled_report_is_drained_before_completion(patched):
    async with run_tracking.tracked_run(kind="progress_track_d", trigger="manual") as run_id:
        rp.report_progress("x", "images", 1, 10)  # first report: written
        rp.report_progress("x", "images", 7, 10)  # throttled: only drain_progress writes it

    run = await _reload(patched, run_id)
    assert run.status == "completed"
    assert run.progress["done"] == 7


@pytest.mark.asyncio(loop_scope="session")
async def test_late_write_after_completion_does_not_change_row(patched):
    async with run_tracking.tracked_run(kind="progress_track_e", trigger="manual") as run_id:
        rp.report_progress("x", "images", 3, 10)

    await rp.write_progress(run_id, {"label": "late", "unit": "images", "done": 9, "total": 10})

    run = await _reload(patched, run_id)
    assert run.status == "completed"
    assert run.progress["done"] == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_report_after_drain_creates_no_state_or_task(patched, monkeypatch):
    """current_run_id is reset right after the drain, before the final commit/fail: a report
    made in that window (or after the block) creates no state and no write task."""
    seen = {}
    original_commit = BatchRunRepository.commit

    async def commit_with_late_report(self, run_id, *args, **kwargs):
        tasks_before = set(rp._tasks)
        rp.report_progress("late", "images", 9, 10)
        seen["current"] = rp.current_run_id.get()
        seen["state"] = run_id in rp._states
        seen["new_tasks"] = set(rp._tasks) - tasks_before
        return await original_commit(self, run_id, *args, **kwargs)

    monkeypatch.setattr(BatchRunRepository, "commit", commit_with_late_report)

    async with run_tracking.tracked_run(kind="progress_track_f", trigger="manual") as run_id:
        rp.report_progress("x", "images", 3, 10)

    assert seen == {"current": None, "state": False, "new_tasks": set()}
    tasks_before = set(rp._tasks)
    rp.report_progress("after", "images", 10, 10)
    assert run_id not in rp._states
    assert set(rp._tasks) == tasks_before

    run = await _reload(patched, run_id)
    assert run.progress["done"] == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_failing_run_still_drains_and_fails(patched):
    captured = {}
    with pytest.raises(RuntimeError, match="boom"):
        async with run_tracking.tracked_run(kind="progress_track_g", trigger="manual") as run_id:
            captured["run_id"] = run_id
            rp.report_progress("x", "images", 4, 10)
            raise RuntimeError("boom")

    run_id = captured["run_id"]
    assert rp.current_run_id.get() is None
    run = await _reload(patched, run_id)
    assert run.status == "failed"
    assert run.error == "boom"
    assert run.progress["done"] == 4
    assert run_id not in rp._states


@pytest.mark.asyncio(loop_scope="session")
async def test_drain_failure_does_not_fail_a_successful_run(patched, monkeypatch):
    async def broken_drain(run_id, timeout=5.0):
        raise RuntimeError("drain broke")

    monkeypatch.setattr(run_tracking, "drain_progress", broken_drain)

    async with run_tracking.tracked_run(kind="progress_track_h", trigger="manual") as run_id:
        pass

    run = await _reload(patched, run_id)
    assert run.status == "completed"
    assert rp.current_run_id.get() is None


@pytest.mark.asyncio(loop_scope="session")
async def test_drain_failure_does_not_hide_the_original_exception(patched, monkeypatch):
    async def broken_drain(run_id, timeout=5.0):
        raise RuntimeError("drain broke")

    monkeypatch.setattr(run_tracking, "drain_progress", broken_drain)
    captured = {}

    with pytest.raises(ValueError, match="original"):
        async with run_tracking.tracked_run(kind="progress_track_i", trigger="manual") as run_id:
            captured["run_id"] = run_id
            raise ValueError("original")

    run = await _reload(patched, captured["run_id"])
    assert run.status == "failed"
    assert run.error == "original"


@pytest.mark.asyncio(loop_scope="session")
async def test_finish_existing_run_sets_current_run_id(patched):
    repo = BatchRunRepository(patched)
    run_id = await repo.create_run(kind="progress_track_j", trigger="manual")
    await patched.commit()

    async with run_tracking.finish_existing_run(run_id):
        assert rp.current_run_id.get() == run_id
        rp.report_progress("x", "files", 2, 5)
    assert rp.current_run_id.get() is None

    run = await _reload(patched, run_id)
    assert run.status == "completed"
    assert run.progress["done"] == 2
    assert run_id not in rp._states


@pytest.mark.asyncio(loop_scope="session")
async def test_finish_existing_run_failure_drains_and_resets(patched):
    repo = BatchRunRepository(patched)
    run_id = await repo.create_run(kind="progress_track_k", trigger="manual")
    await patched.commit()

    with pytest.raises(RuntimeError, match="boom"):
        async with run_tracking.finish_existing_run(run_id):
            rp.report_progress("x", "files", 1, 5)
            raise RuntimeError("boom")
    assert rp.current_run_id.get() is None

    run = await _reload(patched, run_id)
    assert run.status == "failed"
    assert run.progress["done"] == 1
