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
