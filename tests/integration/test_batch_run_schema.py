"""
Integration tests for the batch_runs schema itself (trigger column, one-active-per-kind
partial unique index) -- independent of BatchRunRepository, which Task 2 covers.
Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from Storage.models import BatchRun, RunStatus


@pytest.mark.asyncio(loop_scope="session")
async def test_trigger_column_round_trips(db_session):
    run = BatchRun(kind="trends", trigger="manual", status=str(RunStatus.started))
    db_session.add(run)
    await db_session.flush()

    await db_session.refresh(run)
    assert run.trigger == "manual"


@pytest.mark.asyncio(loop_scope="session")
async def test_second_started_run_of_same_kind_violates_unique_index(db_session):
    first = BatchRun(kind="trends", trigger="manual", status=str(RunStatus.started))
    db_session.add(first)
    await db_session.flush()

    second = BatchRun(kind="trends", trigger="scheduled", status=str(RunStatus.started))
    db_session.add(second)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_second_started_run_of_different_kind_is_fine(db_session):
    first = BatchRun(kind="trends", trigger="manual", status=str(RunStatus.started))
    db_session.add(first)
    await db_session.flush()

    second = BatchRun(kind="move_flagged", trigger="manual", status=str(RunStatus.started))
    db_session.add(second)
    await db_session.flush()  # must not raise


@pytest.mark.asyncio(loop_scope="session")
async def test_second_run_of_same_kind_is_fine_once_first_is_completed(db_session):
    first = BatchRun(kind="trends", trigger="manual", status=str(RunStatus.started))
    db_session.add(first)
    await db_session.flush()
    first.status = str(RunStatus.completed)
    await db_session.flush()

    second = BatchRun(kind="trends", trigger="scheduled", status=str(RunStatus.started))
    db_session.add(second)
    await db_session.flush()  # must not raise -- first is no longer 'started'
