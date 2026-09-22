"""Integration tests for the per-image Tier B review queue. Live PostgreSQL (see conftest)."""
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import CANDIDATE_CAP, IngestionService
from config.settings import settings
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, TmpDuplicates

LOW, HIGH = 0.05, 0.30


async def _run(session):
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_b_review")


async def _img(session, status, batch_id):
    i = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(i); await session.flush(); return i.id


async def _pair(session, a, b, d, match_source="in_batch"):
    session.add(TmpDuplicates(image_id1=min(a, b), image_id2=max(a, b), distance=d, match_source=match_source))
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
    subjects, candidates = await repo.list_tier_b_review_page(
        bid, LOW, HIGH, cursor=None, limit=40, candidate_cap=CANDIDATE_CAP)

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
    subjects, _ = await repo.list_tier_b_review_page(
        bid, LOW, HIGH, cursor=None, limit=40, candidate_cap=CANDIDATE_CAP)
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
    page1, _ = await repo.list_tier_b_review_page(
        bid, LOW, HIGH, cursor=None, limit=2, candidate_cap=CANDIDATE_CAP)
    assert len(page1) == 3                                # limit + 1
    boundary = page1[1]                                   # 2nd item is the last of page 1
    page2, _ = await repo.list_tier_b_review_page(
        bid, LOW, HIGH, cursor=(boundary.min_distance, str(boundary.subject_id)), limit=2,
        candidate_cap=CANDIDATE_CAP)
    assert {s.subject_id for s in page1[:2]}.isdisjoint({s.subject_id for s in page2})


@pytest.mark.asyncio(loop_scope="session")
async def test_rejecting_a_subject_drops_it_and_prunes_it_from_other_cards(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    active = await _img(db_session, "active", bid)
    await _pair(db_session, p1, p2, 0.09)       # p1<->p2
    await _pair(db_session, p2, active, settings.DUPLICATES.THRESHOLD - 0.02)  # p2 also has an active candidate (in-band)

    service = IngestionService(IngestionRepository(db_session))
    with patch("Backend.app.services.ingestion_service.image_store.move_to_rejected"):
        await service.resolve("tier_b", [{"image_id": p1, "decision": "reject"}])

    page = await service.list_tier_b_review(batch_id=bid)
    sids = [it["image"]["image_id"] for it in page["items"]]
    assert str(p1) not in sids                  # p1 rejected -> not a subject
    p2_item = next(it for it in page["items"] if it["image"]["image_id"] == str(p2))
    cand_ids = {c["member"]["image_id"] for c in p2_item["candidates"]}
    assert str(p1) not in cand_ids              # p1<->p2 pair excluded (rejected side)
    assert str(active) in cand_ids              # p2's other candidate remains

    blocked = await service.repo.get_blocked_pending_ids(bid, tier_a_high=0.05, tier_b_high=0.30)
    assert p1 not in blocked and p2 in blocked


@pytest.mark.asyncio(loop_scope="session")
async def test_keeping_a_subject_settles_its_pairs_and_drops_a_now_empty_other(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.09)       # p2's only pair is to p1

    service = IngestionService(IngestionRepository(db_session))
    await service.resolve("tier_b", [{"image_id": p1, "decision": "keep"}])

    page = await service.list_tier_b_review(batch_id=bid)
    assert page["items"] == []                  # p1 kept -> its pair to p2 reviewed -> p2 has none
    blocked = await service.repo.get_blocked_pending_ids(bid, tier_a_high=0.05, tier_b_high=0.30)
    assert p1 not in blocked and p2 not in blocked


@pytest.mark.asyncio(loop_scope="session")
async def test_candidates_capped_at_source_tightest_first(db_session):
    bid = await _run(db_session)
    subject = await _img(db_session, "pending", bid)
    cand_ids = [await _img(db_session, "active", bid) for _ in range(CANDIDATE_CAP + 10)]
    for i, cid in enumerate(cand_ids):
        await _pair(db_session, subject, cid, 0.05 + i * 0.001)  # distinct ascending distances, all in-band

    repo = IngestionRepository(db_session)
    subjects, candidates = await repo.list_tier_b_review_page(
        bid, LOW, HIGH, cursor=None, limit=40, candidate_cap=CANDIDATE_CAP)

    subj_row = next(s for s in subjects if s.subject_id == subject)
    assert subj_row.total_candidates == CANDIDATE_CAP + 10           # uncapped count, from Query A

    subj_cands = [c for c in candidates if c.subject_id == subject]
    assert len(subj_cands) == CANDIDATE_CAP                          # capped at the source now
    assert [c.cand_id for c in subj_cands] == cand_ids[:CANDIDATE_CAP]  # tightest-first, by construction


@pytest.mark.asyncio(loop_scope="session")
async def test_candidate_cap_tie_break_matches_str_cand_id(db_session):
    """All-equal distances: the cap boundary is decided purely by the tie-break, which must be
    `cand_id::text` ascending -- byte-identical to the old Python key (distance, str(cand_id))."""
    bid = await _run(db_session)
    subject = await _img(db_session, "pending", bid)
    cand_ids = [await _img(db_session, "active", bid) for _ in range(CANDIDATE_CAP + 10)]
    for cid in cand_ids:
        await _pair(db_session, subject, cid, 0.10)          # identical distance for every pair

    repo = IngestionRepository(db_session)
    _, candidates = await repo.list_tier_b_review_page(
        bid, LOW, HIGH, cursor=None, limit=40, candidate_cap=CANDIDATE_CAP)

    got = [str(c.cand_id) for c in candidates if c.subject_id == subject]
    assert got == sorted(str(c) for c in cand_ids)[:CANDIDATE_CAP]


@pytest.mark.asyncio(loop_scope="session")
async def test_in_batch_candidates_sort_before_cross_corpus_even_when_looser(db_session):
    """A never-reviewed (in_batch) candidate should outrank an already-reviewed (cross_corpus,
    i.e. the other side is `active`) one even when the cross_corpus candidate is a tighter
    distance match -- the whole point of the reorder is to surface genuinely-new comparisons
    first, not just the closest ones."""
    bid = await _run(db_session)
    subject = await _img(db_session, "pending", bid)
    pending_cand = await _img(db_session, "pending", bid)
    active_cand = await _img(db_session, "active", bid)
    await _pair(db_session, subject, active_cand, 0.06, match_source="cross_corpus")   # tighter, already reviewed
    await _pair(db_session, subject, pending_cand, 0.08, match_source="in_batch")      # looser, never reviewed

    repo = IngestionRepository(db_session)
    _, candidates = await repo.list_tier_b_review_page(
        bid, LOW, HIGH, cursor=None, limit=40, candidate_cap=CANDIDATE_CAP)

    subj_cands = [c for c in candidates if c.subject_id == subject]
    assert [c.cand_id for c in subj_cands] == [pending_cand, active_cand]  # in_batch first despite looser distance


@pytest.mark.asyncio(loop_scope="session")
async def test_cap_prioritizes_in_batch_candidates_over_tighter_cross_corpus(db_session):
    """The cap boundary is decided by the new (match_source, distance, cand_id) ordering, not
    distance alone -- a looser in_batch candidate must survive the cap ahead of a tighter
    cross_corpus one, matching what test_in_batch_candidates_sort_before_cross_corpus_even_when_looser
    proves for display order."""
    bid = await _run(db_session)
    subject = await _img(db_session, "pending", bid)
    cross_ids = [await _img(db_session, "active", bid) for _ in range(CANDIDATE_CAP)]
    for i, cid in enumerate(cross_ids):
        await _pair(db_session, subject, cid, 0.05 + i * 0.001, match_source="cross_corpus")
    pending_cand = await _img(db_session, "pending", bid)
    await _pair(db_session, subject, pending_cand, 0.20, match_source="in_batch")  # loosest of all

    repo = IngestionRepository(db_session)
    _, candidates = await repo.list_tier_b_review_page(
        bid, LOW, HIGH, cursor=None, limit=40, candidate_cap=CANDIDATE_CAP)

    subj_cands = [c for c in candidates if c.subject_id == subject]
    assert len(subj_cands) == CANDIDATE_CAP
    assert pending_cand in [c.cand_id for c in subj_cands]      # made the cut despite being loosest overall
    assert cross_ids[-1] not in [c.cand_id for c in subj_cands]  # bumped out despite a tighter distance


@pytest.mark.asyncio(loop_scope="session")
async def test_query_a_session_tuning_takes_effect(db_session):
    """list_tier_b_review_page's SET LOCAL statement actually changes the session's work_mem for
    Query A (not silently a no-op). random_page_cost is deliberately NOT touched -- see the
    method's docstring/comment for why it was tried and dropped."""
    bid = await _run(db_session)
    subject = await _img(db_session, "pending", bid)
    other = await _img(db_session, "active", bid)
    await _pair(db_session, subject, other, 0.10)

    repo = IngestionRepository(db_session)
    await repo.list_tier_b_review_page(bid, LOW, HIGH, cursor=None, limit=40, candidate_cap=CANDIDATE_CAP)

    row = (await db_session.execute(text("SHOW work_mem"))).scalar()
    assert row == "256MB"


@pytest.mark.asyncio(loop_scope="session")
async def test_query_a_session_tuning_does_not_leak_past_the_transaction(db_engine):
    """SET LOCAL is transaction-scoped in Postgres -- confirm that holds for real through this
    codebase's connection pooling: a fresh top-level transaction (mirroring get_async_db's one
    transaction per request) must see the server default again, not the previous request's
    override, even if it reuses the same pooled connection."""
    async with db_engine.connect() as conn1:
        await conn1.begin()
        session1 = AsyncSession(bind=conn1, expire_on_commit=False)
        bid = await _run(session1)
        subject = await _img(session1, "pending", bid)
        other = await _img(session1, "active", bid)
        await _pair(session1, subject, other, 0.10)
        repo = IngestionRepository(session1)
        await repo.list_tier_b_review_page(bid, LOW, HIGH, cursor=None, limit=40, candidate_cap=CANDIDATE_CAP)
        overridden = (await session1.execute(text("SHOW work_mem"))).scalar()
        assert overridden == "256MB"                      # took effect inside this transaction
        await session1.close()
        # Postgres resets SET LOCAL at the end of a transaction whether committed or rolled back
        # -- rollback proves the same thing commit would, without permanently writing this test's
        # rows into the shared ocrdb_test database (a real leak an earlier version of this test had:
        # conn1.commit() here left a stray tmp_duplicates row that other tests in the same pytest
        # session -- e.g. test_rebuild_duplicates.py's exact-one-row assertions -- then tripped
        # over when the full tests/integration/ suite ran together).
        await conn1.rollback()

    async with db_engine.connect() as conn2:
        await conn2.begin()
        default_mem = (await conn2.execute(text("SHOW work_mem"))).scalar()
        await conn2.rollback()

    assert default_mem == "4MB"                           # back to the server default
