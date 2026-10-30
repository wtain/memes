"""
Integration tests for batch/ingest_promote.py (ingestion Stage 4: promotion).

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from batch.ingest_promote import get_promotable_ids, maybe_complete_run, promote
from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService
from Backend.app.services.ingestion_metrics import record_decision, record_proposed
from Backend.app.services.ingestion_service import IngestionService
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, ProcessEvent, TmpDuplicates

_TIER_B_THRESHOLD = 0.3


async def _make_run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")


async def _make_image(session, status: str, batch_id=None) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    return image.id


async def _make_pair(session, id1, id2, distance: float, match_source: str = "cross_corpus") -> None:
    session.add(TmpDuplicates(
        image_id1=min(id1, id2), image_id2=max(id1, id2),
        distance=distance, match_source=match_source,
    ))
    await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_get_promotable_ids_excludes_blocked_includes_clear(db_session):
    batch_id = await _make_run(db_session)
    clear = await _make_image(db_session, "pending", batch_id)
    blocked = await _make_image(db_session, "pending", batch_id)
    active = await _make_image(db_session, "active")
    await _make_pair(db_session, blocked, active, distance=0.02)

    repo = IngestionRepository(db_session)
    promotable = await get_promotable_ids(repo, batch_id, _TIER_B_THRESHOLD)

    assert promotable == [clear]


@pytest.mark.asyncio(loop_scope="session")
async def test_promote_flips_status_for_clear_images_only(db_session):
    batch_id = await _make_run(db_session)
    clear = await _make_image(db_session, "pending", batch_id)
    blocked = await _make_image(db_session, "pending", batch_id)
    active = await _make_image(db_session, "active")
    await _make_pair(db_session, blocked, active, distance=0.02)

    promoted = await promote(db_session, batch_id, _TIER_B_THRESHOLD)

    assert promoted == [clear]
    assert (await db_session.get(Image, clear)).status == "active"
    assert (await db_session.get(Image, blocked)).status == "pending"


@pytest.mark.asyncio(loop_scope="session")
async def test_promote_ignores_pairs_already_reviewed(db_session):
    """A cleared-via-Keep pair shouldn't block promotion -- reviewed_at set means resolved,
    not still-actionable."""
    batch_id = await _make_run(db_session)
    pending = await _make_image(db_session, "pending", batch_id)
    active = await _make_image(db_session, "active")
    db_session.add(TmpDuplicates(
        image_id1=min(pending, active), image_id2=max(pending, active),
        distance=0.02, match_source="cross_corpus", tier_a_reviewed_at=datetime.now(timezone.utc),
    ))
    await db_session.flush()

    promoted = await promote(db_session, batch_id, _TIER_B_THRESHOLD)

    assert promoted == [pending]


@pytest.mark.asyncio(loop_scope="session")
async def test_maybe_complete_run_marks_completed_when_nothing_pending_remains(db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await _make_run(db_session)
    await _make_image(db_session, "active", batch_id)  # already promoted, nothing pending

    remaining = await maybe_complete_run(db_session, runs_repo, batch_id)

    assert remaining == 0
    run = await runs_repo.get_run(batch_id)
    assert run.status == "completed"
    assert run.stage == "promoted"


@pytest.mark.asyncio(loop_scope="session")
async def test_maybe_complete_run_leaves_run_started_when_pending_remains(db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await _make_run(db_session)
    await _make_image(db_session, "pending", batch_id)

    remaining = await maybe_complete_run(db_session, runs_repo, batch_id)

    assert remaining == 1
    run = await runs_repo.get_run(batch_id)
    assert run.status == "started"


async def _process_events(session, process, subject):
    rows = await session.execute(select(ProcessEvent.event).where(
        ProcessEvent.process == process, ProcessEvent.subject_id == subject))
    return set(rows.scalars())


@pytest.mark.asyncio(loop_scope="session")
async def test_promote_records_kept_for_proposed_images_nobody_decided(db_session):
    batch_id = await _make_run(db_session)
    undecided = await _make_image(db_session, "pending", batch_id)
    decided = await _make_image(db_session, "pending", batch_id)
    never = await _make_image(db_session, "pending", batch_id)
    await record_proposed(db_session, "tier_a", batch_id, [undecided, decided])
    await record_proposed(db_session, "tier_b", batch_id, [undecided])
    await record_decision(db_session, "tier_a", "kept", decided, batch_id)

    promoted = await promote(db_session, batch_id, _TIER_B_THRESHOLD)

    assert set(promoted) == {undecided, decided, never}
    assert await _process_events(db_session, "ingest_tier_a", undecided) == {"proposed", "kept"}
    assert await _process_events(db_session, "ingest_tier_b", undecided) == {"proposed", "kept"}
    assert await _process_events(db_session, "ingest_tier_a", decided) == {"proposed", "kept"}   # not counted twice
    assert await _process_events(db_session, "ingest_tier_a", never) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_an_image_released_by_its_partners_rejection_ends_kept_and_the_run_has_nothing_pending(db_session):
    batch_id = await _make_run(db_session)
    x = await _make_image(db_session, "pending", batch_id)
    y = await _make_image(db_session, "pending", batch_id)
    await _make_pair(db_session, x, y, distance=0.02, match_source="in_batch")
    await record_proposed(db_session, "tier_a", batch_id, [x, y])
    with patch("Backend.app.services.ingestion_service.image_store.move_to_rejected"):
        await IngestionService(IngestionRepository(db_session)).resolve(
            "tier_a", [{"image_id": y, "decision": "reject"}])

    promoted = await promote(db_session, batch_id, _TIER_B_THRESHOLD)

    assert promoted == [x]
    assert await _process_events(db_session, "ingest_tier_a", x) == {"proposed", "kept"}
    items = (await BusinessMetricsService(db_session).batch_metrics("ingest_tier_a"))["items"]
    counts = next(i for i in items if i["run_id"] == str(batch_id))["counts"]
    assert counts == {"proposed": 2, "rejected": 1, "kept": 1}
