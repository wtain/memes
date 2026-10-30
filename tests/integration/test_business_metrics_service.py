"""BusinessMetricsService and repository/process_events.py. Requires a live PostgreSQL instance."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete

from Backend.app.services.business_metrics_service import (
    PROCESSES, BusinessMetricsService, ConflictingEventError, UnknownEventError, UnknownProcessError,
)
from Storage.models import BatchRun, RunStatus


async def _run(session, day):
    run = BatchRun(kind="detect_screenshot_cuts", trigger="manual", status=str(RunStatus.completed),
                   created_at=datetime(2026, 10, day, tzinfo=timezone.utc))
    session.add(run)
    await session.flush()
    return run


async def _record(service, event, run, count=1):
    for _ in range(count):
        await service.record("recut", event, run.run_id if run else None, uuid.uuid4())


@pytest.mark.asyncio(loop_scope="session")
async def test_record_is_idempotent_per_subject_and_event(db_session):
    service = BusinessMetricsService(db_session)
    run, subject = await _run(db_session, 1), uuid.uuid4()

    assert await service.record("recut", "proposed", run.run_id, subject) is True
    assert await service.record("recut", "proposed", run.run_id, subject) is False

    assert (await service.batch_metrics("recut"))["items"][0]["counts"]["proposed"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_record_rejects_an_unknown_process_or_event(db_session):
    service = BusinessMetricsService(db_session)
    with pytest.raises(UnknownProcessError):
        await service.record("nope", "proposed", None, uuid.uuid4())
    with pytest.raises(UnknownEventError):
        await service.record("recut", "typo", None, uuid.uuid4())
    with pytest.raises(UnknownProcessError):
        await service.batch_metrics("nope")


@pytest.mark.asyncio(loop_scope="session")
async def test_record_rolls_back_with_the_callers_transaction(db_session):
    service = BusinessMetricsService(db_session)
    run = await _run(db_session, 1)

    nested = await db_session.begin_nested()
    await service.record("recut", "proposed", run.run_id, uuid.uuid4())
    await nested.rollback()

    assert (await service.batch_metrics("recut"))["items"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_counts_and_rates_per_run(db_session):
    service = BusinessMetricsService(db_session)
    first, second = await _run(db_session, 1), await _run(db_session, 2)
    await _record(service, "proposed", first, 4)
    await _record(service, "approved", first, 1)
    await _record(service, "rejected", first, 1)
    await _record(service, "proposed", second, 1)

    items = {item["run_id"]: item for item in (await service.batch_metrics("recut"))["items"]}

    one = items[str(first.run_id)]
    assert one["counts"] == {"proposed": 4, "adjusted": 0, "approved": 1, "rejected": 1}
    assert one["metrics"] == {"approve_rate": 0.5, "decided_share": 0.5, "pending": 2, "adjusted_share": 0.0}
    two = items[str(second.run_id)]
    assert two["counts"] == {"proposed": 1, "adjusted": 0, "approved": 0, "rejected": 0}
    assert two["metrics"] == {"approve_rate": None, "decided_share": 0.0, "pending": 1, "adjusted_share": 0.0}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_late_decision_counts_toward_the_proposing_run(db_session):
    service = BusinessMetricsService(db_session)
    proposing, newer = await _run(db_session, 1), await _run(db_session, 2)
    subject = uuid.uuid4()
    await service.record("recut", "proposed", proposing.run_id, subject)
    await _record(service, "proposed", newer)
    await service.record("recut", "approved", proposing.run_id, subject)

    items = {item["run_id"]: item for item in (await service.batch_metrics("recut"))["items"]}

    assert items[str(proposing.run_id)]["counts"]["approved"] == 1
    assert items[str(newer.run_id)]["counts"]["approved"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_events_without_a_run_are_unattributed_and_runs_without_events_are_omitted(db_session):
    service = BusinessMetricsService(db_session)
    await _run(db_session, 1)  # no events: must not appear
    empty = {"counts": {"proposed": 0, "adjusted": 0, "approved": 0, "rejected": 0},
             "metrics": {"approve_rate": None, "decided_share": None, "pending": 0, "adjusted_share": None}}
    assert (await service.batch_metrics("recut")) == {"process": "recut", "items": [], "unattributed": empty}

    await _record(service, "proposed", None, 2)
    result = await service.batch_metrics("recut")

    assert result["items"] == []
    assert result["unattributed"]["counts"] == {"proposed": 2, "adjusted": 0, "approved": 0, "rejected": 0}


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_a_run_keeps_its_events_as_unattributed(db_session):
    service = BusinessMetricsService(db_session)
    run = await _run(db_session, 1)
    await _record(service, "proposed", run, 3)

    await db_session.execute(delete(BatchRun).where(BatchRun.run_id == run.run_id))

    result = await service.batch_metrics("recut")
    assert result["items"] == []
    assert result["unattributed"]["counts"]["proposed"] == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_keeps_the_newest_runs_and_still_reports_unattributed(db_session):
    service = BusinessMetricsService(db_session)
    runs = [await _run(db_session, day) for day in (1, 2, 3)]
    for run in runs:
        await _record(service, "proposed", run)
    await _record(service, "proposed", None)

    result = await service.batch_metrics("recut", limit=2)

    assert [item["run_id"] for item in result["items"]] == [str(runs[2].run_id), str(runs[1].run_id)]
    assert result["unattributed"]["counts"]["proposed"] == 1


def test_the_recut_formulas():
    metrics = PROCESSES["recut"].metrics
    counts = {"proposed": 10, "approved": 3, "rejected": 1}
    assert metrics["approve_rate"](counts) == 0.75
    assert metrics["decided_share"](counts) == 0.4
    assert metrics["pending"](counts) == 6


@pytest.mark.parametrize("name", sorted(PROCESSES))
def test_every_metric_survives_all_zero_counts(name):
    definition = PROCESSES[name]
    counts = {event: 0 for event in definition.events}
    for metric in definition.metrics.values():
        value = metric(counts)  # must not raise ZeroDivisionError
        assert value is None or isinstance(value, (int, float))


@pytest.mark.asyncio(loop_scope="session")
async def test_a_subject_cannot_carry_both_decisions(db_session):
    service = BusinessMetricsService(db_session)
    run, subject, other = await _run(db_session, 1), uuid.uuid4(), uuid.uuid4()
    await service.record("recut", "approved", run.run_id, subject)

    with pytest.raises(ConflictingEventError):
        await service.record("recut", "rejected", run.run_id, subject)
    assert await service.record("recut", "approved", run.run_id, subject) is False  # a retry stays idempotent
    assert await service.record("recut", "rejected", run.run_id, other) is True     # other subjects are unaffected

    counts = (await service.batch_metrics("recut"))["items"][0]["counts"]
    assert counts == {"proposed": 0, "adjusted": 0, "approved": 1, "rejected": 1}


@pytest.mark.asyncio(loop_scope="session")
async def test_an_adjusted_proposal_can_still_be_decided_and_is_counted_once(db_session):
    service = BusinessMetricsService(db_session)
    run, subject = await _run(db_session, 1), uuid.uuid4()
    await service.record("recut", "proposed", run.run_id, subject)

    assert await service.record("recut", "adjusted", run.run_id, subject) is True
    assert await service.record("recut", "adjusted", run.run_id, subject) is False   # a second adjustment adds nothing
    assert await service.record("recut", "approved", run.run_id, subject) is True

    bucket = (await service.batch_metrics("recut"))["items"][0]
    assert bucket["counts"] == {"proposed": 1, "adjusted": 1, "approved": 1, "rejected": 0}
    assert bucket["metrics"]["adjusted_share"] == 1.0


def test_the_adjusted_share_formula():
    metrics = PROCESSES["recut"].metrics
    assert metrics["adjusted_share"]({"proposed": 4, "adjusted": 1, "approved": 0, "rejected": 0}) == 0.25
    assert metrics["adjusted_share"]({"proposed": 0, "adjusted": 0, "approved": 0, "rejected": 0}) is None
