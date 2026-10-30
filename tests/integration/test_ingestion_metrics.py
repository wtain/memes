"""Business metrics of the ingestion duplicate review (screenshot recut epic, M2a): Backend/app/services/ingestion_metrics.py.
Requires a live PostgreSQL instance -- see tests/integration/conftest.py."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from Backend.app.services.business_metrics_service import BusinessMetricsService
from Backend.app.services.ingestion_metrics import (
    record_decision, record_implicit_keeps, record_proposed, record_tier_proposals, retract_rejection)
from repository.batch_runs import BatchRunRepository
from Storage.models import BatchRun, Image, ProcessEvent, RunStatus, TmpDuplicates


async def _make_run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_a_review")


async def _finished_run(session) -> uuid.UUID:
    run = BatchRun(kind="ingestion", trigger="manual", status=str(RunStatus.completed))
    session.add(run)
    await session.flush()
    return run.run_id


async def _img(session, status, batch_id=None):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    return image.id


async def _pair(session, id1, id2, distance, **kw):
    session.add(TmpDuplicates(image_id1=min(id1, id2), image_id2=max(id1, id2), distance=distance,
                              match_source="in_batch", **kw))
    await session.flush()


async def _events(session, process, subject):
    rows = await session.execute(select(ProcessEvent.event).where(
        ProcessEvent.process == process, ProcessEvent.subject_id == subject))
    return set(rows.scalars())


@pytest.mark.asyncio(loop_scope="session")
async def test_record_tier_proposals_covers_only_pending_members_of_the_run_in_the_band(db_session):
    run = await _make_run(db_session)
    other_run = await _finished_run(db_session)               # only one ingestion run can be active at a time
    a = await _img(db_session, "pending", run)
    b = await _img(db_session, "pending", run)
    active = await _img(db_session, "active")
    stranger = await _img(db_session, "pending", other_run)   # another run's pending image
    await _pair(db_session, a, active, 0.02)                  # tier A band
    await _pair(db_session, b, stranger, 0.07)                # tier B band, partner of another run

    await record_tier_proposals(db_session, "tier_a", run, 0.0, 0.05)
    await record_tier_proposals(db_session, "tier_b", run, 0.05, 0.12)

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}
    assert await _events(db_session, "ingest_tier_a", b) == set()
    assert await _events(db_session, "ingest_tier_b", a) == set()          # 0.02 is not in band B
    assert await _events(db_session, "ingest_tier_b", b) == {"proposed"}   # 0.07 is band B only
    assert await _events(db_session, "ingest_tier_a", active) == set()
    assert await _events(db_session, "ingest_tier_b", stranger) == set()   # not this run's image


@pytest.mark.asyncio(loop_scope="session")
async def test_record_tier_proposals_is_idempotent_and_skips_reviewed_and_rejected_pairs(db_session):
    run = await _make_run(db_session)
    a = await _img(db_session, "pending", run)
    reviewed = await _img(db_session, "pending", run)
    rejected_partner = await _img(db_session, "pending", run)
    gone = await _img(db_session, "rejected", run)
    active = await _img(db_session, "active")
    await _pair(db_session, a, active, 0.02)
    await _pair(db_session, reviewed, active, 0.02, tier_a_reviewed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    await _pair(db_session, rejected_partner, gone, 0.02)

    await record_tier_proposals(db_session, "tier_a", run, 0.0, 0.05)
    await record_tier_proposals(db_session, "tier_a", run, 0.0, 0.05)

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}
    assert await _events(db_session, "ingest_tier_a", reviewed) == set()
    assert await _events(db_session, "ingest_tier_a", rejected_partner) == set()
    items = (await BusinessMetricsService(db_session).batch_metrics("ingest_tier_a"))["items"]
    assert next(i for i in items if i["run_id"] == str(run))["counts"]["proposed"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_record_decision_needs_a_proposal_in_that_tier(db_session):
    run = await _make_run(db_session)
    proposed, never = await _img(db_session, "pending", run), await _img(db_session, "pending", run)
    await record_proposed(db_session, "tier_a", run, [proposed])

    await record_decision(db_session, "tier_a", "rejected", proposed, run)
    await record_decision(db_session, "tier_a", "kept", never, run)
    await record_decision(db_session, "tier_b", "kept", proposed, run)   # proposed in A, not in B

    assert await _events(db_session, "ingest_tier_a", proposed) == {"proposed", "rejected"}
    assert await _events(db_session, "ingest_tier_a", never) == set()
    assert await _events(db_session, "ingest_tier_b", proposed) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_conflicting_decision_is_swallowed_and_leaves_the_session_usable(db_session):
    run = await _make_run(db_session)
    a = await _img(db_session, "pending", run)
    await record_proposed(db_session, "tier_a", run, [a])
    await record_decision(db_session, "tier_a", "rejected", a, run)

    await record_decision(db_session, "tier_a", "kept", a, run)          # exclusive with rejected: swallowed

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed", "rejected"}
    assert (await db_session.execute(select(Image.id).where(Image.id == a))).scalar_one() == a   # session still usable


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failing_metrics_write_is_swallowed(db_session, monkeypatch):
    async def boom(self, *args, **kwargs):
        raise RuntimeError("metrics down")
    monkeypatch.setattr(BusinessMetricsService, "record", boom)
    run = await _make_run(db_session)
    a = await _img(db_session, "pending", run)

    await record_proposed(db_session, "tier_a", run, [a])                 # must not raise
    await record_decision(db_session, "tier_a", "rejected", a, run)

    assert await _events(db_session, "ingest_tier_a", a) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_retract_rejection_clears_either_tier(db_session):
    run = await _make_run(db_session)
    a, b = await _img(db_session, "pending", run), await _img(db_session, "pending", run)
    await record_proposed(db_session, "tier_a", run, [a])
    await record_proposed(db_session, "tier_b", run, [b])
    await record_decision(db_session, "tier_a", "rejected", a, run)
    await record_decision(db_session, "tier_b", "rejected", b, run)

    await retract_rejection(db_session, a)
    await retract_rejection(db_session, b)
    await retract_rejection(db_session, uuid.uuid4())                      # nothing to retract: no error

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}
    assert await _events(db_session, "ingest_tier_b", b) == {"proposed"}


@pytest.mark.asyncio(loop_scope="session")
async def test_record_implicit_keeps_covers_only_proposed_and_undecided_images(db_session):
    run = await _make_run(db_session)
    undecided = await _img(db_session, "pending", run)
    decided = await _img(db_session, "pending", run)
    never = await _img(db_session, "pending", run)
    await record_proposed(db_session, "tier_a", run, [undecided, decided])
    await record_proposed(db_session, "tier_b", run, [undecided])
    await record_decision(db_session, "tier_a", "rejected", decided, run)

    await record_implicit_keeps(db_session, run, [undecided, decided, never])
    await record_implicit_keeps(db_session, run, [undecided, decided, never])      # idempotent

    assert await _events(db_session, "ingest_tier_a", undecided) == {"proposed", "kept"}
    assert await _events(db_session, "ingest_tier_b", undecided) == {"proposed", "kept"}
    assert await _events(db_session, "ingest_tier_a", decided) == {"proposed", "rejected"}
    assert await _events(db_session, "ingest_tier_a", never) == set()
    await record_implicit_keeps(db_session, run, [])                                # an empty list is fine
