"""
Ingestion review rejects and undoes record and reverse popularity edges (P2, task 3).

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService
from repository.batch_runs import BatchRunRepository
from repository.popularity import PopularityRepository
from Storage.models import Image, PopularityAbsorption, TmpDuplicates

_T0 = datetime(2026, 1, 1)  # images.created_at is timestamp without time zone
_T0_TZ = datetime(2026, 1, 1, tzinfo=timezone.utc)  # tmp_duplicates reviewed_at is timestamptz


async def _make_run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(
        kind="ingestion", trigger="manual", stage="tier_a_review"
    )


async def _img(session, status, batch_id=None, popularity=1, age=0):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id,
                  popularity=popularity, created_at=_T0 + timedelta(minutes=age))
    session.add(image)
    await session.flush()
    return image.id


async def _pair(session, id1, id2, distance, **kw):
    session.add(TmpDuplicates(
        image_id1=min(id1, id2), image_id2=max(id1, id2),
        distance=distance, match_source="in_batch", **kw,
    ))
    await session.flush()


async def _edges(session, loser):
    rows = await session.execute(
        select(PopularityAbsorption).where(PopularityAbsorption.loser_image_id == loser)
        .order_by(PopularityAbsorption.created_at, PopularityAbsorption.kind))
    return list(rows.scalars())


async def _pop(session, image_id):
    await session.refresh(await session.get(Image, image_id))
    return (await session.get(Image, image_id)).popularity


def _service(session):
    return IngestionService(IngestionRepository(session))


MOVE_OUT = "Backend.app.services.ingestion_service.image_store.move_to_rejected"
MOVE_BACK = "Backend.app.services.ingestion_service.image_store.move_from_rejected"


@pytest.mark.asyncio(loop_scope="session")
async def test_pick_duplicate_winner_prefers_active_then_popularity_then_age(db_session):
    repo = IngestionRepository(db_session)
    a = await _img(db_session, "pending")
    act = await _img(db_session, "active", popularity=1, age=5)
    pend_hi = await _img(db_session, "pending", popularity=9, age=1)
    pend_lo = await _img(db_session, "pending", popularity=2, age=0)
    for n in (act, pend_hi, pend_lo):
        await _pair(db_session, a, n, 0.02)
    assert await repo.pick_duplicate_winner(a, "tier_a", 0.0, 0.05) == act
    (await db_session.get(Image, act)).status = "rejected"
    await db_session.flush()
    assert await repo.pick_duplicate_winner(a, "tier_a", 0.0, 0.05) == pend_hi

    b = await _img(db_session, "pending")
    older = await _img(db_session, "pending", popularity=3, age=0)
    newer = await _img(db_session, "pending", popularity=3, age=7)
    await _pair(db_session, b, newer, 0.02)
    await _pair(db_session, b, older, 0.02)
    assert await repo.pick_duplicate_winner(b, "tier_a", 0.0, 0.05) == older


@pytest.mark.asyncio(loop_scope="session")
async def test_pick_ignores_rejected_reviewed_and_out_of_band_neighbours(db_session):
    repo = IngestionRepository(db_session)
    a = await _img(db_session, "pending")
    rej = await _img(db_session, "rejected")
    rev = await _img(db_session, "active")
    far = await _img(db_session, "active")
    await _pair(db_session, a, rej, 0.02)
    await _pair(db_session, a, rev, 0.02, tier_a_reviewed_at=_T0_TZ)
    await _pair(db_session, a, far, 0.2)
    assert await repo.pick_duplicate_winner(a, "tier_a", 0.0, 0.05) is None
    # tier_b: reviewed flag is the tier_b column, band 0.05..0.3
    assert await repo.pick_duplicate_winner(a, "tier_b", 0.05, 0.3) == far


@pytest.mark.asyncio(loop_scope="session")
async def test_pick_does_not_depend_on_own_status(db_session):
    repo = IngestionRepository(db_session)
    a = await _img(db_session, "rejected")
    w = await _img(db_session, "active")
    await _pair(db_session, a, w, 0.02)
    assert await repo.pick_duplicate_winner(a, "tier_a", 0.0, 0.05) == w


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_records_the_edge_and_undo_reverses_it(db_session):
    batch = await _make_run(db_session)
    a = await _img(db_session, "pending", batch)
    w = await _img(db_session, "active")
    await _pair(db_session, a, w, 0.02)
    service = _service(db_session)

    with patch(MOVE_OUT):
        result = await service.resolve("tier_a", [{"image_id": a, "decision": "reject"}])
    assert result["rejected"] == [str(a)]
    assert await _pop(db_session, w) == 2
    assert await _pop(db_session, a) == 0
    (edge,) = await _edges(db_session, a)
    assert edge.source == "ingest_tier_a"
    assert edge.winner_image_id == w
    assert edge.batch_run_id == batch

    with patch(MOVE_BACK):
        out = await service.undo_reject(a)
    assert out == {"image_id": str(a), "status": "pending"}
    assert await _pop(db_session, w) == 1
    assert await _pop(db_session, a) == 1
    kinds = [(e.kind, e.reason) for e in await _edges(db_session, a)]
    assert ("reverse", "undo_reject") in kinds


@pytest.mark.asyncio(loop_scope="session")
async def test_tier_b_uses_its_own_source(db_session):
    batch = await _make_run(db_session)
    a = await _img(db_session, "pending", batch)
    w = await _img(db_session, "active")
    await _pair(db_session, a, w, 0.1)
    with patch(MOVE_OUT):
        await _service(db_session).resolve("tier_b", [{"image_id": a, "decision": "reject"}])
    (edge,) = await _edges(db_session, a)
    assert edge.source == "ingest_tier_b"


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_with_no_neighbour_records_nothing(db_session):
    batch = await _make_run(db_session)
    a = await _img(db_session, "pending", batch)
    with patch(MOVE_OUT):
        result = await _service(db_session).resolve("tier_a", [{"image_id": a, "decision": "reject"}])
    assert result["rejected"] == [str(a)]
    assert await _edges(db_session, a) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_when_all_neighbours_are_rejected_records_nothing(db_session):
    batch = await _make_run(db_session)
    a = await _img(db_session, "pending", batch)
    r = await _img(db_session, "rejected")
    await _pair(db_session, a, r, 0.02)
    with patch(MOVE_OUT):
        result = await _service(db_session).resolve("tier_a", [{"image_id": a, "decision": "reject"}])
    assert result["rejected"] == [str(a)]
    assert await _edges(db_session, a) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_keep_changes_nothing(db_session):
    batch = await _make_run(db_session)
    a = await _img(db_session, "pending", batch)
    w = await _img(db_session, "active")
    await _pair(db_session, a, w, 0.02)
    result = await _service(db_session).resolve("tier_a", [{"image_id": a, "decision": "keep"}])
    assert result["kept"] == [str(a)]
    assert await _edges(db_session, a) == []
    assert await _pop(db_session, w) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_undo_reject_without_an_edge_is_a_no_op(db_session):
    batch = await _make_run(db_session)
    a = await _img(db_session, "pending", batch)
    service = _service(db_session)
    with patch(MOVE_OUT):
        await service.resolve("tier_a", [{"image_id": a, "decision": "reject"}])
    with patch(MOVE_BACK):
        out = await service.undo_reject(a)
    assert out == {"image_id": str(a), "status": "pending"}
    assert await _edges(db_session, a) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_a_popularity_error_does_not_undo_the_reject(db_session, monkeypatch):
    batch = await _make_run(db_session)
    a = await _img(db_session, "pending", batch)
    w = await _img(db_session, "active")
    await _pair(db_session, a, w, 0.02)
    c = await _img(db_session, "pending", batch)

    async def boom(self, *args, **kwargs):
        raise RuntimeError("popularity down")

    monkeypatch.setattr(PopularityRepository, "absorb", boom)
    with patch(MOVE_OUT):
        result = await _service(db_session).resolve("tier_a", [
            {"image_id": a, "decision": "reject"},
            {"image_id": c, "decision": "reject"},
        ])
    assert result["rejected"] == [str(a), str(c)]
    assert result["failed"] == []
    assert (await db_session.get(Image, a)).status == "rejected"
    assert (await db_session.get(Image, c)).status == "rejected"
    assert await _edges(db_session, a) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_move_failed_still_counts(db_session):
    batch = await _make_run(db_session)
    a = await _img(db_session, "pending", batch)
    w = await _img(db_session, "active")
    await _pair(db_session, a, w, 0.02)
    with patch(MOVE_OUT, side_effect=OSError("disk")):
        result = await _service(db_session).resolve("tier_a", [{"image_id": a, "decision": "reject"}])
    assert result["rejected"] == [str(a)]
    assert len(result["move_failed"]) == 1
    assert len(await _edges(db_session, a)) == 1
    assert await _pop(db_session, w) == 2
