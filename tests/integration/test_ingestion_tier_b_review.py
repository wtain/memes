"""Integration tests for the per-image Tier B review queue. Live PostgreSQL (see conftest)."""
import uuid

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, TmpDuplicates

LOW, HIGH = 0.05, 0.30


async def _run(session):
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_b_review")


async def _img(session, status, batch_id):
    i = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(i); await session.flush(); return i.id


async def _pair(session, a, b, d):
    session.add(TmpDuplicates(image_id1=min(a, b), image_id2=max(a, b), distance=d, match_source="in_batch"))
    await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_lists_pending_subjects_with_candidates_ordered_by_tightest(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    p3 = await _img(db_session, "pending", bid)          # no tier-b pair -> not a subject
    active = await _img(db_session, "active", bid)
    await _pair(db_session, p1, active, 0.06)             # p1's tightest, keeps p1 strictly ahead of p2
    await _pair(db_session, p1, p2, 0.08)
    await _pair(db_session, p2, active, 0.15)             # p2's min is 0.08 (the symmetric p1<->p2 edge)
    _ = p3

    repo = IngestionRepository(db_session)
    subjects, candidates = await repo.list_tier_b_review_page(bid, LOW, HIGH, cursor=None, limit=40)

    sids = [s.subject_id for s in subjects]
    assert sids == [p1, p2]                               # p1 (min 0.06) strictly before p2 (min 0.08)
    assert subjects[0].min_distance == pytest.approx(0.06)
    assert subjects[0].total_candidates == 2
    p1_cands = sorted((c for c in candidates if c.subject_id == p1), key=lambda c: c.distance)
    assert [c.cand_id for c in p1_cands] == [active, p2]  # tightest first
    assert all(c.subject_id in {p1, p2} for c in candidates)   # only page subjects


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_reviewed_rejected_and_out_of_band(db_session):
    bid = await _run(db_session)
    p = await _img(db_session, "pending", bid)
    rej = await _img(db_session, "rejected", bid)
    o1 = await _img(db_session, "active", bid)
    o2 = await _img(db_session, "active", bid)
    await _pair(db_session, p, rej, 0.10)                 # other side rejected -> excluded
    await _pair(db_session, p, o1, 0.40)                  # out of band -> excluded
    reviewed = TmpDuplicates(image_id1=min(p, o2), image_id2=max(p, o2), distance=0.10,
                             match_source="in_batch")
    from datetime import datetime, timezone
    reviewed.tier_b_reviewed_at = datetime.now(timezone.utc)
    db_session.add(reviewed); await db_session.flush()    # already reviewed -> excluded

    repo = IngestionRepository(db_session)
    subjects, _ = await repo.list_tier_b_review_page(bid, LOW, HIGH, cursor=None, limit=40)
    assert subjects == []


@pytest.mark.asyncio(loop_scope="session")
async def test_cursor_pages_disjoint_subjects(db_session):
    bid = await _run(db_session)
    active = await _img(db_session, "active", bid)
    subs = []
    for i in range(5):
        p = await _img(db_session, "pending", bid)
        await _pair(db_session, p, active, 0.10 + i * 0.01)
        subs.append(p)

    repo = IngestionRepository(db_session)
    page1, _ = await repo.list_tier_b_review_page(bid, LOW, HIGH, cursor=None, limit=2)
    assert len(page1) == 3                                # limit + 1
    boundary = page1[1]                                   # 2nd item is the last of page 1
    page2, _ = await repo.list_tier_b_review_page(
        bid, LOW, HIGH, cursor=(boundary.min_distance, str(boundary.subject_id)), limit=2)
    assert {s.subject_id for s in page1[:2]}.isdisjoint({s.subject_id for s in page2})
