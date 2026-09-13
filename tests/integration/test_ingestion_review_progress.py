"""Integration tests for the review-progress count queries. Live PostgreSQL (see conftest)."""
import uuid
from datetime import datetime, timezone

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService, _tier_band
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, TmpDuplicates

TIER_A_LOW, TIER_A_HIGH = 0.0, 0.05
TIER_B_LOW, TIER_B_HIGH = 0.05, 0.30


async def _run(session):
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_b_review")


async def _img(session, status, batch_id):
    i = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(i); await session.flush(); return i.id


async def _pair(session, a, b, d, tier_b_reviewed=False, tier_a_reviewed=False):
    row = TmpDuplicates(image_id1=min(a, b), image_id2=max(a, b), distance=d, match_source="in_batch")
    if tier_b_reviewed:
        row.tier_b_reviewed_at = datetime.now(timezone.utc)
    if tier_a_reviewed:
        row.tier_a_reviewed_at = datetime.now(timezone.utc)
    session.add(row); await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_get_review_progress_excludes_reviewed_and_rejected(db_session):
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
    p6 = await _img(db_session, "pending", bid)
    corpus = await _img(db_session, "active", bid)
    await _pair(db_session, p6, corpus, 0.13)   # active other side: p6 is a subject, corpus never is

    repo = IngestionRepository(db_session)
    tier_remaining, blocked_total = await repo.get_review_progress(
        bid, "tier_b", TIER_A_LOW, TIER_A_HIGH, TIER_B_LOW, TIER_B_HIGH)
    # p1, p2, p6 -- p3 excluded (rejected other side), p4/p5 excluded (already reviewed), corpus
    # excluded because it's `active`, never `pending`, so it can never appear as a subject
    # regardless of which UNION ALL direction of the pair is evaluated.
    assert tier_remaining == 3
    assert blocked_total == 3   # same open set; no tier_a activity in this test


@pytest.mark.asyncio(loop_scope="session")
async def test_get_review_progress_tier_a_excludes_tier_a_reviewed(db_session):
    # Carries forward the case the progress-visibility branch's final review added (its own
    # Finding #1): the tier_a branch's reviewed-column handling must look only at
    # tier_a_reviewed_at, never tier_b_reviewed_at. A single batch run -- only one active
    # "ingestion" run is allowed at a time (ix_batch_runs_one_active_per_kind) -- so both halves
    # of this assertion share one bid, using distinct image pairs (uq_tmp_duplicates_pair forbids
    # reusing the same pair twice anyway).
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.02, tier_a_reviewed=True)  # tier A band, reviewed for tier A

    p3 = await _img(db_session, "pending", bid)
    p4 = await _img(db_session, "pending", bid)
    await _pair(db_session, p3, p4, 0.02, tier_b_reviewed=True)  # same band, reviewed for tier B only

    repo = IngestionRepository(db_session)
    tier_remaining, _ = await repo.get_review_progress(
        bid, "tier_a", TIER_A_LOW, TIER_A_HIGH, TIER_B_LOW, TIER_B_HIGH)
    assert tier_remaining == 2  # p3, p4 only -- p1/p2 excluded (tier_a-reviewed)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_review_progress_respects_band_and_tier(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.02)  # tier A band, not tier B

    repo = IngestionRepository(db_session)
    tier_a_remaining, _ = await repo.get_review_progress(
        bid, "tier_a", TIER_A_LOW, TIER_A_HIGH, TIER_B_LOW, TIER_B_HIGH)
    tier_b_remaining, _ = await repo.get_review_progress(
        bid, "tier_b", TIER_A_LOW, TIER_A_HIGH, TIER_B_LOW, TIER_B_HIGH)
    assert tier_a_remaining == 2
    assert tier_b_remaining == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_get_review_progress_blocked_total_dedupes_a_subject_open_in_both_tiers(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    p3 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.02)   # tier A band
    await _pair(db_session, p1, p3, 0.10)   # tier B band -- p1 open in BOTH tiers

    repo = IngestionRepository(db_session)
    _, blocked_total = await repo.get_review_progress(
        bid, "tier_a", TIER_A_LOW, TIER_A_HIGH, TIER_B_LOW, TIER_B_HIGH)
    assert blocked_total == 3   # p1, p2, p3 -- p1 counted once despite being open in both bands


@pytest.mark.asyncio(loop_scope="session")
async def test_get_run_status_end_to_end_real_db(db_session):
    """Finding #2 (final review, 2026-09-13): every other service-level integration test in this
    codebase builds IngestionService(IngestionRepository(db_session)) against a real DB rather
    than mocking the repository (see tests/integration/test_ingestion_tier_b_review.py) -- there
    was no equivalent covering get_run_status, which is exactly the method most exposed to a
    service->repo band-argument mismatch (a transposed low/high, or a tier_a/tier_b band swap,
    would pass every mocked unit test). Sets up real pending images + tmp_duplicates pairs in
    both tier bands and confirms tier_remaining/blocked_total come back correct end-to-end
    through the real SQL, not just mocks."""
    bid = await _run(db_session)  # stage="tier_b_review"
    tier_a_low, tier_a_high = _tier_band("tier_a")
    tier_b_low, tier_b_high = _tier_band("tier_b")
    mid_a = (tier_a_low + tier_a_high) / 2
    mid_b = (tier_b_low + tier_b_high) / 2

    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    p3 = await _img(db_session, "pending", bid)
    p4 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, mid_a)  # tier A band -- open, blocked, but not tier B's remaining
    await _pair(db_session, p3, p4, mid_b)  # tier B band -- open, blocked, IS tier B's remaining

    service = IngestionService(IngestionRepository(db_session))
    status = await service.get_run_status(bid)

    assert status["tier_remaining"] == 2   # p3, p4 -- current stage is tier_b_review
    assert status["blocked_total"] == 4    # p1, p2, p3, p4 -- union of both tiers' open subjects
