"""Ingestion review decisions and undos record business metrics (screenshot recut epic, M2a).
Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py."""
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService
from Backend.app.services.ingestion_metrics import record_proposed
from Backend.app.services.ingestion_service import IngestionService
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, ProcessEvent, TmpDuplicates

MOVE_OUT = "Backend.app.services.ingestion_service.image_store.move_to_rejected"
MOVE_BACK = "Backend.app.services.ingestion_service.image_store.move_from_rejected"


async def _make_run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_a_review")


async def _img(session, status, batch_id=None):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    return image.id


async def _pair(session, id1, id2, distance):
    session.add(TmpDuplicates(image_id1=min(id1, id2), image_id2=max(id1, id2), distance=distance,
                              match_source="in_batch"))
    await session.flush()


async def _events(session, process, subject):
    rows = await session.execute(select(ProcessEvent.event).where(
        ProcessEvent.process == process, ProcessEvent.subject_id == subject))
    return set(rows.scalars())


def _service(session):
    return IngestionService(IngestionRepository(session))


@pytest.mark.asyncio(loop_scope="session")
async def test_a_reject_records_rejected_and_a_keep_records_kept(db_session):
    run = await _make_run(db_session)
    a, b = await _img(db_session, "pending", run), await _img(db_session, "pending", run)
    active = await _img(db_session, "active")
    await _pair(db_session, a, active, 0.02)
    await _pair(db_session, b, active, 0.02)
    await record_proposed(db_session, "tier_a", run, [a, b])

    with patch(MOVE_OUT):
        result = await _service(db_session).resolve("tier_a", [
            {"image_id": a, "decision": "reject"}, {"image_id": b, "decision": "keep"}])

    assert result["rejected"] == [str(a)] and result["kept"] == [str(b)]
    assert await _events(db_session, "ingest_tier_a", a) == {"proposed", "rejected"}
    assert await _events(db_session, "ingest_tier_a", b) == {"proposed", "kept"}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_decision_for_an_image_that_was_never_proposed_counts_nothing(db_session):
    run = await _make_run(db_session)
    a = await _img(db_session, "pending", run)

    with patch(MOVE_OUT):
        await _service(db_session).resolve("tier_a", [{"image_id": a, "decision": "keep"}])

    assert await _events(db_session, "ingest_tier_a", a) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_stale_reject_of_a_non_pending_image_counts_nothing(db_session):
    run = await _make_run(db_session)
    a = await _img(db_session, "active", run)
    await record_proposed(db_session, "tier_a", run, [a])

    with patch(MOVE_OUT):
        result = await _service(db_session).resolve("tier_a", [{"image_id": a, "decision": "reject"}])

    assert result["rejected"] == []
    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_metrics_failure_leaves_the_decision_applied(db_session, monkeypatch):
    run = await _make_run(db_session)
    a, b = await _img(db_session, "pending", run), await _img(db_session, "pending", run)
    active = await _img(db_session, "active")
    await _pair(db_session, a, active, 0.02)
    await _pair(db_session, b, active, 0.02)
    await record_proposed(db_session, "tier_a", run, [a, b])      # seed BEFORE the failure is installed

    async def boom(self, *args, **kwargs):
        raise RuntimeError("metrics down")
    monkeypatch.setattr(BusinessMetricsService, "record", boom)

    with patch(MOVE_OUT):
        result = await _service(db_session).resolve("tier_a", [
            {"image_id": a, "decision": "reject"}, {"image_id": b, "decision": "keep"}])

    assert result["rejected"] == [str(a)] and result["kept"] == [str(b)] and result["failed"] == []
    assert (await db_session.get(Image, a)).status == "rejected"
    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}
    assert await _events(db_session, "ingest_tier_a", b) == {"proposed"}


@pytest.mark.asyncio(loop_scope="session")
async def test_undo_reject_then_keep_counts_one_outcome(db_session):
    run = await _make_run(db_session)
    a, active = await _img(db_session, "pending", run), await _img(db_session, "active")
    await _pair(db_session, a, active, 0.02)
    await record_proposed(db_session, "tier_a", run, [a])
    service = _service(db_session)
    with patch(MOVE_OUT):
        await service.resolve("tier_a", [{"image_id": a, "decision": "reject"}])
    with patch(MOVE_BACK):
        await service.undo_reject(a)
    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}

    with patch(MOVE_OUT):
        await service.resolve("tier_a", [{"image_id": a, "decision": "keep"}])

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed", "kept"}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_rejection_closes_the_images_open_proposals_in_both_tiers(db_session):
    run = await _make_run(db_session)
    x, y, z = await _img(db_session, "pending", run), await _img(db_session, "pending", run), await _img(db_session, "active")
    await _pair(db_session, x, y, 0.03)          # tier A pair
    await _pair(db_session, x, z, 0.08)          # tier B pair
    await record_proposed(db_session, "tier_a", run, [x])
    await record_proposed(db_session, "tier_b", run, [x])

    with patch(MOVE_OUT):
        await _service(db_session).resolve("tier_b", [{"image_id": x, "decision": "reject"}])

    assert await _events(db_session, "ingest_tier_b", x) == {"proposed", "rejected"}
    assert await _events(db_session, "ingest_tier_a", x) == {"proposed", "rejected"}   # not pending for ever
    items = (await BusinessMetricsService(db_session).batch_metrics("ingest_tier_a"))["items"]
    assert next(i for i in items if i["run_id"] == str(run))["metrics"]["pending"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_a_rejection_leaves_a_decision_taken_in_the_other_tier_alone(db_session):
    run = await _make_run(db_session)
    x, z = await _img(db_session, "pending", run), await _img(db_session, "active")
    await _pair(db_session, x, z, 0.03)
    await record_proposed(db_session, "tier_a", run, [x])
    await record_proposed(db_session, "tier_b", run, [x])
    service = _service(db_session)
    with patch(MOVE_OUT):
        await service.resolve("tier_a", [{"image_id": x, "decision": "keep"}])
        await service.resolve("tier_b", [{"image_id": x, "decision": "reject"}])

    assert await _events(db_session, "ingest_tier_a", x) == {"proposed", "kept"}
    assert await _events(db_session, "ingest_tier_b", x) == {"proposed", "rejected"}


@pytest.mark.asyncio(loop_scope="session")
async def test_undo_reject_reopens_the_proposal_in_both_tiers(db_session):
    run = await _make_run(db_session)
    x, z = await _img(db_session, "pending", run), await _img(db_session, "active")
    await _pair(db_session, x, z, 0.08)
    await record_proposed(db_session, "tier_a", run, [x])
    await record_proposed(db_session, "tier_b", run, [x])
    service = _service(db_session)
    with patch(MOVE_OUT):
        await service.resolve("tier_b", [{"image_id": x, "decision": "reject"}])
    with patch(MOVE_BACK):
        await service.undo_reject(x)

    assert await _events(db_session, "ingest_tier_a", x) == {"proposed"}
    assert await _events(db_session, "ingest_tier_b", x) == {"proposed"}


@pytest.mark.asyncio(loop_scope="session")
async def test_undo_reject_then_reject_again_counts_one_rejection(db_session):
    run = await _make_run(db_session)
    a, active = await _img(db_session, "pending", run), await _img(db_session, "active")
    await _pair(db_session, a, active, 0.02)
    await record_proposed(db_session, "tier_a", run, [a])
    service = _service(db_session)
    with patch(MOVE_OUT):
        await service.resolve("tier_a", [{"image_id": a, "decision": "reject"}])
    with patch(MOVE_BACK):
        await service.undo_reject(a)

    with patch(MOVE_OUT):
        await service.resolve("tier_a", [{"image_id": a, "decision": "reject"}])

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed", "rejected"}
    items = (await BusinessMetricsService(db_session).batch_metrics("ingest_tier_a"))["items"]
    item = next(i for i in items if i["run_id"] == str(run))
    assert item["counts"] == {"proposed": 1, "rejected": 1, "kept": 0}
