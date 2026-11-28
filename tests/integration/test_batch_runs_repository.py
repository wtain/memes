"""
Integration tests for repository/batch_runs.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.

No dedicated coverage existed for this repository before -- a gap that let a real bug
through: batch/ingest_hash_dedup.py called commit() (which flips status to "completed")
after finishing Stage 1, when Stage 1 is only the first of several stages spanning
multiple later script invocations, so the run should have stayed "started" (via
update_stats(), which must not touch status) until promotion actually finishes it. These
tests pin down that distinction directly.
"""
import pytest

from repository.batch_runs import BatchRunRepository, BatchAlreadyRunningError


@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_starts_as_started(db_session):
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")

    run = await repo.get_run(run_id)

    assert run.status == "started"
    assert run.stage == "hash_dedup"
    assert run.completed_at is None


@pytest.mark.asyncio(loop_scope="session")
async def test_update_stats_merges_without_changing_status(db_session):
    """The bug this test exists to prevent: a script recording progress mid-pipeline must
    not accidentally mark the whole run finished."""
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")

    await repo.update_stats(run_id, intake=3, registered=2)
    await repo.update_stats(run_id, tier_a_candidates=5)  # a later stage's own call

    run = await repo.get_run(run_id)

    assert run.status == "started"
    assert run.completed_at is None
    assert run.stats == {"intake": 3, "registered": 2, "tier_a_candidates": 5}


@pytest.mark.asyncio(loop_scope="session")
async def test_commit_marks_completed(db_session):
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="trends", trigger="manual")

    await repo.commit(run_id, stats={"sources": 4})

    run = await repo.get_run(run_id)
    assert run.status == "completed"
    assert run.completed_at is not None
    assert run.stats == {"sources": 4}


@pytest.mark.asyncio(loop_scope="session")
async def test_fail_marks_failed_with_error(db_session):
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")

    await repo.fail(run_id, error="disk full")

    run = await repo.get_run(run_id)
    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.error == "disk full"


@pytest.mark.asyncio(loop_scope="session")
async def test_abort_marks_status_aborted_with_note(db_session):
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")

    await repo.abort(run_id, note="Aborted by user via ingest_abort.py")

    run = await repo.get_run(run_id)
    assert run.status == "aborted"
    assert run.completed_at is not None
    assert run.error == "Aborted by user via ingest_abort.py"


@pytest.mark.asyncio(loop_scope="session")
async def test_set_stage_updates_stage_only(db_session):
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")

    await repo.set_stage(run_id, "tier_a_review")

    run = await repo.get_run(run_id)
    assert run.stage == "tier_a_review"
    assert run.status == "started"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_active_run_finds_started_run_of_given_kind(db_session):
    repo = BatchRunRepository(db_session)
    await repo.create_run(kind="trends", trigger="manual")
    ingestion_id = await repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")

    active = await repo.get_active_run(kind="ingestion")

    assert active is not None
    assert active.run_id == ingestion_id


@pytest.mark.asyncio(loop_scope="session")
async def test_get_active_run_none_once_completed(db_session):
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    await repo.commit(run_id)

    assert await repo.get_active_run(kind="ingestion") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_active_run_none_when_no_runs_of_that_kind(db_session):
    repo = BatchRunRepository(db_session)
    await repo.create_run(kind="trends", trigger="manual")

    assert await repo.get_active_run(kind="ingestion") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_most_recent_run_returns_latest_regardless_of_status(db_session):
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select
    from Storage.models import BatchRun

    repo = BatchRunRepository(db_session)
    older_id = await repo.create_run(kind="trends", trigger="manual")
    await repo.commit(older_id)
    newer_id = await repo.create_run(kind="trends", trigger="manual")
    await repo.fail(newer_id, error="disk full")

    # Explicitly set distinguishable created_at values to avoid timestamp collisions
    # (PostgreSQL's func.now() returns transaction start time, not per-statement time)
    older_time = datetime(2026, 7, 27, 10, 0, 0, tzinfo=timezone.utc)
    newer_time = older_time + timedelta(seconds=1)

    older_run = await db_session.scalar(select(BatchRun).where(BatchRun.run_id == older_id))
    older_run.created_at = older_time

    newer_run = await db_session.scalar(select(BatchRun).where(BatchRun.run_id == newer_id))
    newer_run.created_at = newer_time

    await db_session.flush()

    result = await repo.get_most_recent_run(kind="trends")

    assert result is not None
    assert result.run_id == newer_id


@pytest.mark.asyncio(loop_scope="session")
async def test_get_most_recent_run_none_when_no_runs_of_that_kind(db_session):
    repo = BatchRunRepository(db_session)
    await repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")

    assert await repo.get_most_recent_run(kind="trends") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_stores_trigger(db_session):
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="trends", trigger="scheduled")

    run = await repo.get_run(run_id)
    assert run.trigger == "scheduled"


@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_raises_when_kind_already_active(db_session):
    repo = BatchRunRepository(db_session)
    await repo.create_run(kind="trends", trigger="manual")

    with pytest.raises(BatchAlreadyRunningError):
        await repo.create_run(kind="trends", trigger="scheduled")


@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_succeeds_for_different_kind_while_one_active(db_session):
    repo = BatchRunRepository(db_session)
    await repo.create_run(kind="trends", trigger="manual")

    # must not raise
    await repo.create_run(kind="move_flagged", trigger="manual")


@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_succeeds_once_prior_run_of_same_kind_is_completed(db_session):
    repo = BatchRunRepository(db_session)
    first_id = await repo.create_run(kind="trends", trigger="manual")
    await repo.commit(first_id)

    # must not raise -- first run is no longer 'started'
    await repo.create_run(kind="trends", trigger="scheduled")


@pytest.mark.asyncio(loop_scope="session")
async def test_list_runs_filters_by_kind_and_paginates(db_session):
    repo = BatchRunRepository(db_session)
    await repo.create_run(kind="trends", trigger="manual")
    await repo.create_run(kind="move_flagged", trigger="scheduled")
    await repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")

    items, total = await repo.list_runs(kinds=["trends", "move_flagged"], limit=10, offset=0)

    assert total == 2
    assert {item.kind for item in items} == {"trends", "move_flagged"}

    items_page_2, total_page_2 = await repo.list_runs(
        kinds=["trends", "move_flagged"], limit=1, offset=1
    )
    assert total_page_2 == 2
    assert len(items_page_2) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_set_progress_writes_value_and_server_updated_at(db_session):
    from datetime import datetime

    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="progress_a", trigger="manual")

    assert await repo.set_progress(run_id, {"done": 5, "total": 10}) is True

    run = await repo.get_run(run_id)
    await db_session.refresh(run)
    assert run.progress["done"] == 5
    assert run.progress["total"] == 10
    datetime.fromisoformat(run.progress["updated_at"])


@pytest.mark.asyncio(loop_scope="session")
async def test_set_progress_overwrites_whole_value(db_session):
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="progress_b", trigger="manual")

    await repo.set_progress(run_id, {"done": 1, "phase": "x"})
    await repo.set_progress(run_id, {"other": 2})

    run = await repo.get_run(run_id)
    await db_session.refresh(run)
    assert set(run.progress) == {"other", "updated_at"}


@pytest.mark.asyncio(loop_scope="session")
async def test_set_progress_ignores_finished_run(db_session):
    repo = BatchRunRepository(db_session)
    done_id = await repo.create_run(kind="progress_c", trigger="manual")
    await repo.set_progress(done_id, {"done": 1})
    await repo.commit(done_id)
    failed_id = await repo.create_run(kind="progress_d", trigger="manual")
    await repo.set_progress(failed_id, {"done": 1})
    await repo.fail(failed_id, "boom")

    assert await repo.set_progress(done_id, {"done": 99}) is False
    assert await repo.set_progress(failed_id, {"done": 99}) is False

    for rid in (done_id, failed_id):
        run = await repo.get_run(rid)
        await db_session.refresh(run)
        assert run.progress["done"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_set_progress_does_not_touch_stats(db_session):
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="progress_e", trigger="manual")
    await repo.update_stats(run_id, registered=2)

    await repo.set_progress(run_id, {"done": 1})

    run = await repo.get_run(run_id)
    await db_session.refresh(run)
    assert run.stats == {"registered": 2}


@pytest.mark.asyncio(loop_scope="session")
async def test_tracking_paths_work_without_progress_column(db_session):
    """Deploy order (F1): `BatchRun.progress` is deferred, so every path run tracking and the
    scheduler use keeps working on a database that has not been migrated yet. The column is
    dropped inside the test's own transaction (rolled back afterwards, PostgreSQL DDL is
    transactional) to model an unmigrated database."""
    from sqlalchemy import text

    await db_session.execute(text("ALTER TABLE batch_runs DROP COLUMN progress"))
    db_session.expunge_all()

    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="noprog_a", trigger="manual", stage="s1")
    db_session.expunge_all()

    active = await repo.get_active_run(kind="noprog_a")
    assert active is not None and active.run_id == run_id
    await repo.set_stage(run_id, "s2")
    await repo.update_stats(run_id, n=1)
    assert (await repo.get_most_recent_run(kind="noprog_a")).run_id == run_id
    await repo.commit(run_id)
    assert (await repo.get_latest_completed_run(kind="noprog_a")).run_id == run_id

    other_id = await repo.create_run(kind="noprog_b", trigger="manual")
    await repo.fail(other_id, "boom")
    third_id = await repo.create_run(kind="noprog_c", trigger="manual")
    await repo.abort(third_id, "stop")
    db_session.expunge_all()
    run = await repo.get_run(third_id, with_progress=False)
    assert run.status == "aborted"


@pytest.mark.asyncio(loop_scope="session")
async def test_default_load_does_not_select_progress(db_session):
    from sqlalchemy import event, inspect

    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="noprog_d", trigger="manual")
    await repo.set_progress(run_id, {"done": 1})
    db_session.expunge_all()

    statements: list[str] = []

    def _capture(conn, cursor, statement, *args):
        statements.append(statement)

    sync_engine = db_session.bind.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        active = await repo.get_active_run(kind="noprog_d")
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)

    assert "progress" in inspect(active).unloaded
    selects = [s for s in statements if "FROM batch_runs" in s]
    assert selects and all("batch_runs.progress" not in s for s in selects)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_run_and_list_runs_load_progress(db_session):
    from sqlalchemy import inspect

    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="noprog_e", trigger="manual")
    await repo.set_progress(run_id, {"done": 4, "total": 9})
    db_session.expunge_all()

    run = await repo.get_run(run_id)
    assert "progress" not in inspect(run).unloaded
    assert run.progress["done"] == 4

    db_session.expunge_all()
    items, _ = await repo.list_runs(kinds=["noprog_e"], limit=10, offset=0)
    assert "progress" not in inspect(items[0]).unloaded
    assert items[0].progress["total"] == 9


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_service_returns_progress_from_real_repository(db_session):
    from unittest.mock import MagicMock

    from Backend.app.services.admin_batch_service import AdminBatchService

    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="noprog_f", trigger="manual")
    await repo.set_progress(
        run_id, {"label": "x", "unit": "images", "done": 2, "total": 5, "rate_per_min": None}
    )
    db_session.expunge_all()

    registry = MagicMock()
    registry.all_names.return_value = ["noprog_f_batch"]
    registry.get.return_value = {"module": "batch.noprog_f", "kind": "noprog_f"}
    registry.name_for_kind.return_value = "noprog_f_batch"
    service = AdminBatchService(repo, db_session, registry)

    one = await service.get_run(run_id)
    assert one["progress"]["done"] == 2
    db_session.expunge_all()
    listed = await service.list_runs(limit=10, offset=0)
    assert listed["items"][0]["progress"]["total"] == 5


@pytest.mark.asyncio(loop_scope="session")
async def test_pool_usable_after_rollback_following_already_running_error(db_engine):
    """Verifies AdminBatchService.trigger_run's 409 handling is safe: when create_run()'s
    flush() hits the DB's IntegrityError, Postgres leaves that session's transaction in an
    aborted state (create_run itself does not call rollback() before re-raising as
    BatchAlreadyRunningError). AdminBatchService only catches BatchAlreadyRunningError and
    re-raises fastapi.HTTPException(409) -- it never touches the session directly.

    Correction (an earlier version of this docstring got the mechanism wrong): the recovery
    here is NOT because of Storage.db.get_async_db's own
    `except Exception: await db.rollback(); raise` handler. The sibling test right below,
    test_pool_recovers_even_without_any_explicit_rollback, is the same three-session
    scenario with rollback removed at EVERY layer (not just the one AdminBatchService
    itself skips) and it still recovers cleanly, reusing the exact same underlying DBAPI
    connection. The actual mechanism is SQLAlchemy's connection pool self-healing on
    session close/checkin: `pool_reset_on_return` defaults to `"rollback"`, so simply
    closing a session -- which `async with AsyncSessionLocal() as db:` always does,
    exception or not -- issues a ROLLBACK on the underlying DBAPI connection before
    returning it to the pool, regardless of whether any app-level code ever explicitly
    called `.rollback()`. get_async_db's explicit rollback is still good practice (it
    frees the connection sooner and keeps the session object itself usable for any code
    that might run before the `async with` block actually exits), but it is not what
    makes this specific 409 scenario safe.

    Uses three independent sessions bound directly to the session-scoped `db_engine`
    (not the function-scoped `db_session` savepoint fixture the rest of this file uses)
    so each one models a genuinely separate HTTP request sharing the same connection
    pool, matching how Storage.db.get_async_db actually works in production (a fresh
    AsyncSessionLocal() per request):

      1. "request 1" creates and commits an active 'trends' run (a stand-in for some
         earlier, already-completed request).
      2. "request 2" mimics AdminBatchService.trigger_run + get_async_db: a fresh
         session's create_run() collides with request 1's still-active run, raises
         BatchAlreadyRunningError, and -- WITHOUT the service touching the session
         itself -- is followed by the same rollback get_async_db's except clause
         performs (standing in for the HTTPException(409) propagating out uncaught).
      3. "request 3" is a brand new session pulled from the same pool. If request 2's
         aborted transaction had poisoned the underlying connection/pool, this would
         fail here with asyncpg's InFailedSQLTransactionError or SQLAlchemy's
         PendingRollbackError. Instead it must both read request 1's run back out and
         perform a brand new write, proving the pool is completely healthy.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session1:
        repo1 = BatchRunRepository(session1)
        await repo1.create_run(kind="trends", trigger="manual")
        await session1.commit()

    async with AsyncSession(db_engine, expire_on_commit=False) as session2:
        repo2 = BatchRunRepository(session2)
        try:
            await repo2.create_run(kind="trends", trigger="manual")
            await session2.commit()
        except BatchAlreadyRunningError:
            # Mirrors get_async_db's `except Exception: await db.rollback(); raise` --
            # the step AdminBatchService itself never performs. See the sibling test
            # below for proof this line isn't actually what makes recovery work.
            await session2.rollback()
        else:
            pytest.fail("expected BatchAlreadyRunningError")

    async with AsyncSession(db_engine, expire_on_commit=False) as session3:
        repo3 = BatchRunRepository(session3)

        active = await repo3.get_active_run(kind="trends")
        assert active is not None  # request 1's run, unaffected by request 2's failure

        other_id = await repo3.create_run(kind="move_flagged", trigger="manual")
        assert other_id is not None

        # Clean up both 'started' runs (partial unique index only guards one active
        # run per kind) so this test doesn't leave permanently-active rows behind for
        # other tests sharing this session-scoped db_engine.
        await repo3.commit(active.run_id)
        await repo3.commit(other_id)
        await session3.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_pool_recovers_even_without_any_explicit_rollback(db_engine):
    """Stronger variant of test_pool_usable_after_rollback_following_already_running_error,
    added after a review found the other test's docstring misattributed *why* recovery
    works. This version omits session.rollback() entirely -- not just the one
    AdminBatchService skips, but also the one get_async_db's except-clause would normally
    perform -- and shows the pool still recovers via SQLAlchemy's default checkin reset
    (`pool_reset_on_return="rollback"`), which fires whenever a session is closed,
    independent of any explicit app-level rollback call. This is a permanent regression
    guard for the actual claim: AdminBatchService.trigger_run needs no special rollback
    handling for the 409 path because the pool would recover even if
    get_async_db's own rollback line were deleted.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine, expire_on_commit=False) as session1:
        repo1 = BatchRunRepository(session1)
        await repo1.create_run(kind="trends", trigger="manual")
        await session1.commit()

    # No rollback anywhere in this block -- just closes via `async with`'s __aexit__,
    # exactly like a get_async_db WITHOUT its except-clause rollback would.
    async with AsyncSession(db_engine, expire_on_commit=False) as session2:
        repo2 = BatchRunRepository(session2)
        with pytest.raises(BatchAlreadyRunningError):
            await repo2.create_run(kind="trends", trigger="manual")

    async with AsyncSession(db_engine, expire_on_commit=False) as session3:
        repo3 = BatchRunRepository(session3)

        active = await repo3.get_active_run(kind="trends")
        assert active is not None

        other_id = await repo3.create_run(kind="move_flagged", trigger="manual")
        assert other_id is not None

        await repo3.commit(active.run_id)
        await repo3.commit(other_id)
        await session3.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_fail_started_of_kinds_fails_only_started_runs_of_those_kinds_since_the_cutoff(db_session):
    from datetime import datetime, timedelta, timezone

    repo = BatchRunRepository(db_session)
    hit = await repo.create_run(kind="build_tags_from_ocr", trigger="manual")
    other_kind = await repo.create_run(kind="build_bow", trigger="manual")
    finished = await repo.create_run(kind="build_ocr_lemmas", trigger="manual")
    await repo.commit(finished)

    count = await repo.fail_started_of_kinds(
        ["build_tags_from_ocr", "build_ocr_lemmas"], since=datetime.now(timezone.utc) - timedelta(minutes=5),
        error="orphaned: driver died")

    assert count == 1
    assert (await repo.get_run(hit)).status == "failed"
    assert (await repo.get_run(hit)).error == "orphaned: driver died"
    assert (await repo.get_run(hit)).completed_at is not None
    assert (await repo.get_run(other_kind)).status == "started"
    assert (await repo.get_run(finished)).status == "completed"


@pytest.mark.asyncio(loop_scope="session")
async def test_fail_started_of_kinds_leaves_runs_started_before_the_cutoff(db_session):
    from datetime import datetime, timedelta, timezone

    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="build_tags_from_ocr", trigger="manual")

    count = await repo.fail_started_of_kinds(
        ["build_tags_from_ocr"], since=datetime.now(timezone.utc) + timedelta(minutes=5), error="x")

    assert count == 0
    assert (await repo.get_run(run_id)).status == "started"
