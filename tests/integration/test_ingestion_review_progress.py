"""Integration tests for the review-progress count queries. Live PostgreSQL (see conftest)."""
import uuid
from datetime import datetime, timezone

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, TmpDuplicates

TIER_A_LOW, TIER_A_HIGH = 0.0, 0.05
TIER_B_LOW, TIER_B_HIGH = 0.05, 0.30


async def _run(session):
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_b_review")


async def _img(session, status, batch_id):
    i = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(i); await session.flush(); return i.id


async def _pair(session, a, b, d, tier_b_reviewed=False):
    row = TmpDuplicates(image_id1=min(a, b), image_id2=max(a, b), distance=d, match_source="in_batch")
    if tier_b_reviewed:
        row.tier_b_reviewed_at = datetime.now(timezone.utc)
    session.add(row); await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_count_unreviewed_subjects_excludes_reviewed_and_rejected(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    p3 = await _img(db_session, "pending", bid)
    rejected = await _img(db_session, "rejected", bid)
    p4 = await _img(db_session, "pending", bid)
    p5 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.10)                        # open -> both count
    await _pair(db_session, p3, rejected, 0.12)                  # other side rejected -> excluded
    await _pair(db_session, p4, p5, 0.11, tier_b_reviewed=True)  # already reviewed -> excluded
    # NOTE: p4/p5's pair is a SEPARATE pair from p1/p2's -- uq_tmp_duplicates_pair is a real DB
    # unique constraint on (image_id1, image_id2), so a single pair of images can never have both
    # an open row and an already-reviewed row at once; use distinct images to test each exclusion.

    repo = IngestionRepository(db_session)
    n = await repo.count_unreviewed_subjects(bid, "tier_b", TIER_B_LOW, TIER_B_HIGH)
    assert n == 2  # p1, p2 -- p3 excluded (rejected other side), p4/p5 excluded (already reviewed)


@pytest.mark.asyncio(loop_scope="session")
async def test_count_unreviewed_subjects_respects_band_and_tier(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.02)  # tier A band, not tier B

    repo = IngestionRepository(db_session)
    assert await repo.count_unreviewed_subjects(bid, "tier_a", TIER_A_LOW, TIER_A_HIGH) == 2
    assert await repo.count_unreviewed_subjects(bid, "tier_b", TIER_B_LOW, TIER_B_HIGH) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_unreviewed_subject_ids_returns_the_actual_ids(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    other = await _img(db_session, "active", bid)
    await _pair(db_session, p1, p2, 0.10)
    await _pair(db_session, p2, other, 0.15)

    repo = IngestionRepository(db_session)
    ids = await repo.unreviewed_subject_ids(bid, "tier_b", TIER_B_LOW, TIER_B_HIGH)
    assert ids == {p1, p2}  # `other` is active, never a subject


@pytest.mark.asyncio(loop_scope="session")
async def test_unreviewed_subject_ids_union_dedupes_a_subject_open_in_both_tiers(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    p3 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.02)   # tier A band
    await _pair(db_session, p1, p3, 0.10)   # tier B band -- p1 open in BOTH tiers

    repo = IngestionRepository(db_session)
    ids_a = await repo.unreviewed_subject_ids(bid, "tier_a", TIER_A_LOW, TIER_A_HIGH)
    ids_b = await repo.unreviewed_subject_ids(bid, "tier_b", TIER_B_LOW, TIER_B_HIGH)
    union = ids_a | ids_b
    assert union == {p1, p2, p3}   # p1 counted once despite being in both sets
    assert len(union) == 3
