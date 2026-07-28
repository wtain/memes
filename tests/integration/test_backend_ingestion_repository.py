"""
Integration tests for Backend/app/repositories/ingestion_repository.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
Router tests mock the service layer and never execute real queries, so this file exercises
every public method against a real schema, same rationale as test_backend_image_repository.py.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from Backend.app.repositories.ingestion_repository import IngestionRepository
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, OCRText, TmpDuplicates


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


# --------------------------------------------------------------------------
# list_pending_images
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_list_pending_images_scoped_to_batch(db_session):
    # Create and complete a prior batch to avoid unique index conflict
    other_batch_id = await _make_run(db_session)
    await BatchRunRepository(db_session).commit(other_batch_id)
    # Create the current batch to test
    batch_id = await _make_run(db_session)
    mine = await _make_image(db_session, "pending", batch_id)
    await _make_image(db_session, "pending", other_batch_id)

    rows = await IngestionRepository(db_session).list_pending_images(batch_id)

    assert [r[0] for r in rows] == [mine]


# --------------------------------------------------------------------------
# get_tier_candidate_rows
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_tier_candidate_rows_respects_distance_band(db_session):
    batch_id = await _make_run(db_session)
    pending = await _make_image(db_session, "pending", batch_id)
    tight_match = await _make_image(db_session, "active")
    loose_match = await _make_image(db_session, "active")
    await _make_pair(db_session, pending, tight_match, distance=0.02)
    await _make_pair(db_session, pending, loose_match, distance=0.15)

    repo = IngestionRepository(db_session)
    tier_a_rows = await repo.get_tier_candidate_rows(batch_id, "tier_a", 0.0, 0.05)
    tier_b_rows = await repo.get_tier_candidate_rows(batch_id, "tier_b", 0.05, 0.3)

    def other_side(rows):
        (id1, _, _, id2, _, _, _, _), = rows
        return id2 if id1 == pending else id1

    assert len(tier_a_rows) == 1
    assert other_side(tier_a_rows) == tight_match
    assert len(tier_b_rows) == 1
    assert other_side(tier_b_rows) == loose_match


@pytest.mark.asyncio(loop_scope="session")
async def test_get_tier_candidate_rows_excludes_already_reviewed_pairs(db_session):
    batch_id = await _make_run(db_session)
    pending = await _make_image(db_session, "pending", batch_id)
    active = await _make_image(db_session, "active")
    session_pair = TmpDuplicates(
        image_id1=min(pending, active), image_id2=max(pending, active),
        distance=0.02, match_source="cross_corpus", tier_a_reviewed_at=datetime.now(timezone.utc),
    )
    db_session.add(session_pair)
    await db_session.flush()

    repo = IngestionRepository(db_session)
    tier_a_rows = await repo.get_tier_candidate_rows(batch_id, "tier_a", 0.0, 0.05)

    assert tier_a_rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_get_tier_candidate_rows_excludes_rejected_images(db_session):
    batch_id = await _make_run(db_session)
    pending = await _make_image(db_session, "pending", batch_id)
    rejected = await _make_image(db_session, "rejected", batch_id)
    await _make_pair(db_session, pending, rejected, distance=0.02, match_source="in_batch")

    repo = IngestionRepository(db_session)
    rows = await repo.get_tier_candidate_rows(batch_id, "tier_a", 0.0, 0.05)

    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_get_tier_candidate_rows_includes_in_batch_and_cross_corpus_together(db_session):
    batch_id = await _make_run(db_session)
    a = await _make_image(db_session, "pending", batch_id)
    b = await _make_image(db_session, "pending", batch_id)
    active = await _make_image(db_session, "active")
    await _make_pair(db_session, a, b, distance=0.01, match_source="in_batch")
    await _make_pair(db_session, a, active, distance=0.02, match_source="cross_corpus")

    repo = IngestionRepository(db_session)
    rows = await repo.get_tier_candidate_rows(batch_id, "tier_a", 0.0, 0.05)

    match_sources = {r[7] for r in rows}
    assert match_sources == {"in_batch", "cross_corpus"}


# --------------------------------------------------------------------------
# reject_image / undo_reject
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_reject_image_sets_status_and_returns_filename(db_session):
    batch_id = await _make_run(db_session)
    image_id = await _make_image(db_session, "pending", batch_id)

    repo = IngestionRepository(db_session)
    filename = await repo.reject_image(image_id)

    assert filename is not None
    image = await db_session.get(Image, image_id)
    assert image.status == "rejected"


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_image_returns_none_for_unknown_id(db_session):
    repo = IngestionRepository(db_session)
    assert await repo.reject_image(uuid.uuid4()) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_undo_reject_reverts_to_pending(db_session):
    batch_id = await _make_run(db_session)
    image_id = await _make_image(db_session, "rejected", batch_id)

    repo = IngestionRepository(db_session)
    filename = await repo.undo_reject(image_id)

    assert filename is not None
    image = await db_session.get(Image, image_id)
    assert image.status == "pending"


@pytest.mark.asyncio(loop_scope="session")
async def test_undo_reject_returns_none_if_not_rejected(db_session):
    batch_id = await _make_run(db_session)
    image_id = await _make_image(db_session, "pending", batch_id)

    repo = IngestionRepository(db_session)
    assert await repo.undo_reject(image_id) is None


# --------------------------------------------------------------------------
# mark_reviewed
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_mark_reviewed_sets_column_only_on_rows_touching_the_image(db_session):
    batch_id = await _make_run(db_session)
    a = await _make_image(db_session, "pending", batch_id)
    b = await _make_image(db_session, "pending", batch_id)
    c = await _make_image(db_session, "pending", batch_id)
    await _make_pair(db_session, a, b, distance=0.01, match_source="in_batch")
    await _make_pair(db_session, b, c, distance=0.01, match_source="in_batch")  # doesn't touch a

    repo = IngestionRepository(db_session)
    await repo.mark_reviewed(a, "tier_a")

    rows = (await db_session.execute(select(TmpDuplicates))).scalars().all()
    touched = {(r.image_id1, r.image_id2): r.tier_a_reviewed_at is not None for r in rows}
    ab = touched[(min(a, b), max(a, b))]
    bc = touched[(min(b, c), max(b, c))]
    assert ab is True
    assert bc is False


# --------------------------------------------------------------------------
# get_ocr_texts
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_ocr_texts_concatenates_blocks_and_drops_low_confidence(db_session):
    batch_id = await _make_run(db_session)
    image_id = await _make_image(db_session, "pending", batch_id)
    no_ocr_id = await _make_image(db_session, "pending", batch_id)
    db_session.add_all([
        OCRText(image_id=image_id, text="hello", confidence=0.9),
        OCRText(image_id=image_id, text="world", confidence=0.5),
        OCRText(image_id=image_id, text="noise", confidence=0.1),
    ])
    await db_session.flush()

    repo = IngestionRepository(db_session)
    texts = await repo.get_ocr_texts({image_id, no_ocr_id})

    assert texts[image_id] == "hello world"
    assert no_ocr_id not in texts


@pytest.mark.asyncio(loop_scope="session")
async def test_get_ocr_texts_empty_input_returns_empty_dict(db_session):
    repo = IngestionRepository(db_session)
    assert await repo.get_ocr_texts(set()) == {}


# --------------------------------------------------------------------------
# get_blocked_pending_ids / promote_images
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_blocked_pending_ids_includes_unresolved_tier_a_and_tier_b(db_session):
    batch_id = await _make_run(db_session)
    clear = await _make_image(db_session, "pending", batch_id)
    tier_a_blocked = await _make_image(db_session, "pending", batch_id)
    tier_b_blocked = await _make_image(db_session, "pending", batch_id)
    active_a = await _make_image(db_session, "active")
    active_b = await _make_image(db_session, "active")
    await _make_pair(db_session, tier_a_blocked, active_a, distance=0.02)
    await _make_pair(db_session, tier_b_blocked, active_b, distance=0.15)

    repo = IngestionRepository(db_session)
    blocked = await repo.get_blocked_pending_ids(batch_id, tier_a_high=0.05, tier_b_high=0.3)

    assert blocked == {tier_a_blocked, active_a, tier_b_blocked, active_b}
    assert clear not in blocked


@pytest.mark.asyncio(loop_scope="session")
async def test_get_blocked_pending_ids_excludes_reviewed_pairs(db_session):
    batch_id = await _make_run(db_session)
    pending = await _make_image(db_session, "pending", batch_id)
    active = await _make_image(db_session, "active")
    reviewed_pair = TmpDuplicates(
        image_id1=min(pending, active), image_id2=max(pending, active),
        distance=0.02, match_source="cross_corpus", tier_a_reviewed_at=datetime.now(timezone.utc),
    )
    db_session.add(reviewed_pair)
    await db_session.flush()

    repo = IngestionRepository(db_session)
    blocked = await repo.get_blocked_pending_ids(batch_id, tier_a_high=0.05, tier_b_high=0.3)

    assert pending not in blocked


@pytest.mark.asyncio(loop_scope="session")
async def test_get_blocked_pending_ids_excludes_pairs_with_rejected_side(db_session):
    batch_id = await _make_run(db_session)
    pending = await _make_image(db_session, "pending", batch_id)
    rejected = await _make_image(db_session, "rejected", batch_id)
    await _make_pair(db_session, pending, rejected, distance=0.02, match_source="in_batch")

    repo = IngestionRepository(db_session)
    blocked = await repo.get_blocked_pending_ids(batch_id, tier_a_high=0.05, tier_b_high=0.3)

    assert blocked == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_promote_images_sets_status_active(db_session):
    batch_id = await _make_run(db_session)
    a = await _make_image(db_session, "pending", batch_id)
    b = await _make_image(db_session, "pending", batch_id)

    repo = IngestionRepository(db_session)
    count = await repo.promote_images([a, b])

    assert count == 2
    assert (await db_session.get(Image, a)).status == "active"
    assert (await db_session.get(Image, b)).status == "active"


@pytest.mark.asyncio(loop_scope="session")
async def test_promote_images_empty_list_is_noop(db_session):
    repo = IngestionRepository(db_session)
    assert await repo.promote_images([]) == 0
