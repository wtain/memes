"""Integration tests for repository/ingestion_text_filter.py (ingestion Tier B text filter, Task 2).

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timezone

import pytest

from repository.batch_runs import BatchRunRepository
from repository.ingestion_text_filter import FilterParams, apply_pairs, plan_pairs, undo_run
from Storage.models import Image, OCRLemma, OCRTextEmbedding, TmpDuplicates
from Storage.models import OCR_TEXT_EMBEDDING_DIM

PARAMS = FilterParams(min_lemmas=3, max_overlap=0.2, veto_distance=0.10)
BAND = (0.05, 0.12)


async def _batch(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_b_review")


async def _image(session, status="pending", batch_id=None, lemmas=(), stale=False) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    for lemma in lemmas:
        session.add(OCRLemma(image_id=image.id, lemma=lemma, stale=stale))
    await session.flush()
    return image.id


async def _pair(session, a, b, distance=0.08, distance_source="clip", **extra) -> uuid.UUID:
    row = TmpDuplicates(
        image_id1=min(a, b), image_id2=max(a, b), distance=distance, match_source="cross_corpus",
        distance_source=distance_source, **extra,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _embed(session, image_id, hot: int) -> None:
    vec = [0.0] * OCR_TEXT_EMBEDDING_DIM
    vec[hot] = 1.0
    session.add(OCRTextEmbedding(image_id=image_id, embedding=vec))
    await session.flush()


def _by_id(planned):
    return {p.pair_id: p for p in planned}


def _words(prefix, n):
    return [f"{prefix}{i}" for i in range(n)]


@pytest.mark.asyncio(loop_scope="session")
async def test_clearly_different_text_is_eligible(db_session):
    batch = await _batch(db_session)
    a = await _image(db_session, "pending", batch, _words("a", 4))
    b = await _image(db_session, "active", None, _words("b", 4))
    pair = await _pair(db_session, a, b)

    planned = _by_id(await plan_pairs(db_session, batch, BAND, PARAMS))

    assert planned[pair].bucket == "eligible"
    assert planned[pair].overlap == 0.0
    assert planned[pair].text_distance is None


@pytest.mark.asyncio(loop_scope="session")
async def test_overlap_at_threshold_is_not_eligible(db_session):
    batch = await _batch(db_session)
    shared = ["s0"]
    a = await _image(db_session, "pending", batch, shared + _words("a", 4))   # 5 lemmas
    b = await _image(db_session, "active", None, shared + _words("b", 4))     # 5 lemmas, 1 shared -> 0.2
    pair = await _pair(db_session, a, b)

    planned = _by_id(await plan_pairs(db_session, batch, BAND, PARAMS))

    assert planned[pair].overlap == pytest.approx(0.2)
    assert planned[pair].bucket == "overlap_high"


@pytest.mark.asyncio(loop_scope="session")
async def test_fewer_than_min_lemmas_is_no_text(db_session):
    batch = await _batch(db_session)
    a = await _image(db_session, "pending", batch, _words("a", 2))
    b = await _image(db_session, "active", None, _words("b", 5))
    pair = await _pair(db_session, a, b)

    planned = _by_id(await plan_pairs(db_session, batch, BAND, PARAMS))

    assert planned[pair].bucket == "no_text"


@pytest.mark.asyncio(loop_scope="session")
async def test_embedding_within_veto_distance_is_vetoed(db_session):
    batch = await _batch(db_session)
    a = await _image(db_session, "pending", batch, _words("a", 4))
    b = await _image(db_session, "active", None, _words("b", 4))
    await _embed(db_session, a, hot=0)
    await _embed(db_session, b, hot=0)   # identical vectors: cosine distance 0
    pair = await _pair(db_session, a, b)

    planned = _by_id(await plan_pairs(db_session, batch, BAND, PARAMS))

    assert planned[pair].bucket == "vetoed"
    assert planned[pair].text_distance == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_embeddings_far_apart_do_not_veto(db_session):
    batch = await _batch(db_session)
    a = await _image(db_session, "pending", batch, _words("a", 4))
    b = await _image(db_session, "active", None, _words("b", 4))
    await _embed(db_session, a, hot=0)
    await _embed(db_session, b, hot=1)   # orthogonal vectors: cosine distance 1
    pair = await _pair(db_session, a, b)

    planned = _by_id(await plan_pairs(db_session, batch, BAND, PARAMS))

    assert planned[pair].bucket == "eligible"


@pytest.mark.asyncio(loop_scope="session")
async def test_one_sided_embedding_does_not_veto(db_session):
    batch = await _batch(db_session)
    a = await _image(db_session, "pending", batch, _words("a", 4))
    b = await _image(db_session, "active", None, _words("b", 4))
    await _embed(db_session, a, hot=0)
    pair = await _pair(db_session, a, b)

    planned = _by_id(await plan_pairs(db_session, batch, BAND, PARAMS))

    assert planned[pair].bucket == "eligible"


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_sourced_pair_is_never_eligible(db_session):
    batch = await _batch(db_session)
    a = await _image(db_session, "pending", batch, _words("a", 4))
    b = await _image(db_session, "active", None, _words("b", 4))
    pair = await _pair(db_session, a, b, distance_source="ocr_text")

    planned = _by_id(await plan_pairs(db_session, batch, BAND, PARAMS))

    assert planned[pair].bucket == "ocr_text_source"


@pytest.mark.asyncio(loop_scope="session")
async def test_stale_lemmas_keep_the_pair(db_session):
    batch = await _batch(db_session)
    a = await _image(db_session, "pending", batch, _words("a", 4), stale=True)
    b = await _image(db_session, "active", None, _words("b", 4))
    pair = await _pair(db_session, a, b)

    planned = _by_id(await plan_pairs(db_session, batch, BAND, PARAMS))

    assert planned[pair].bucket == "stale_text"


@pytest.mark.asyncio(loop_scope="session")
async def test_pair_with_both_sides_pending_is_planned_once(db_session):
    batch = await _batch(db_session)
    a = await _image(db_session, "pending", batch, _words("a", 4))
    b = await _image(db_session, "pending", batch, _words("b", 4))
    pair = await _pair(db_session, a, b)

    planned = [p for p in await plan_pairs(db_session, batch, BAND, PARAMS) if p.pair_id == pair]

    assert len(planned) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_settled_rejected_out_of_band_and_other_batch_pairs_are_not_planned(db_session):
    batch = await _batch(db_session)
    # one active run per kind, so the foreign batch comes from a run of another kind
    other_batch = await BatchRunRepository(db_session).create_run(kind="ingestion_auto_prep", trigger="manual")
    words = _words("a", 4)
    subject = await _image(db_session, "pending", batch, words)
    active = await _image(db_session, "active", None, _words("b", 4))
    rejected = await _image(db_session, "rejected", None, _words("c", 4))
    foreign = await _image(db_session, "pending", other_batch, _words("d", 4))
    settled = await _pair(db_session, subject, active, tier_b_reviewed_at=datetime.now(timezone.utc))
    to_rejected = await _pair(db_session, subject, rejected)
    out_of_band_low = await _pair(db_session, subject, foreign, distance=0.03)
    far = await _image(db_session, "active", None, _words("e", 4))
    out_of_band_high = await _pair(db_session, subject, far, distance=0.2)
    other_subject = await _pair(db_session, foreign, active)

    ids = {p.pair_id for p in await plan_pairs(db_session, batch, BAND, PARAMS)}

    assert not ids & {settled, to_rejected, out_of_band_low, out_of_band_high, other_subject}


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_marks_only_given_pairs_and_tags_run(db_session):
    batch = await _batch(db_session)
    a = await _image(db_session, "pending", batch, _words("a", 4))
    b = await _image(db_session, "active", None, _words("b", 4))
    c = await _image(db_session, "active", None, _words("c", 4))
    chosen = await _pair(db_session, a, b)
    untouched = await _pair(db_session, a, c)
    run = uuid.uuid4()

    assert await apply_pairs(db_session, run, [chosen]) == 1
    assert await apply_pairs(db_session, run, [chosen]) == 0   # already settled

    row_chosen = await db_session.get(TmpDuplicates, chosen)
    row_untouched = await db_session.get(TmpDuplicates, untouched)
    assert row_chosen.tier_b_reviewed_at is not None and row_chosen.tier_b_auto_kept_run == run
    assert row_untouched.tier_b_reviewed_at is None and row_untouched.tier_b_auto_kept_run is None


@pytest.mark.asyncio(loop_scope="session")
async def test_undo_reopens_only_pairs_still_tagged_with_that_run(db_session):
    batch = await _batch(db_session)
    a = await _image(db_session, "pending", batch, _words("a", 4))
    others = [await _image(db_session, "active", None, _words(f"o{i}", 4)) for i in range(3)]
    mine = await _pair(db_session, a, others[0])
    by_person = await _pair(db_session, a, others[1], tier_b_reviewed_at=datetime.now(timezone.utc))
    by_other_run = await _pair(db_session, a, others[2])
    run, other_run = uuid.uuid4(), uuid.uuid4()
    await apply_pairs(db_session, run, [mine])
    await apply_pairs(db_session, other_run, [by_other_run])

    assert await undo_run(db_session, run) == 1

    row_mine = await db_session.get(TmpDuplicates, mine)
    await db_session.refresh(row_mine)
    assert row_mine.tier_b_reviewed_at is None and row_mine.tier_b_auto_kept_run is None
    row_person = await db_session.get(TmpDuplicates, by_person)
    row_other = await db_session.get(TmpDuplicates, by_other_run)
    await db_session.refresh(row_person)
    await db_session.refresh(row_other)
    assert row_person.tier_b_reviewed_at is not None and row_person.tier_b_auto_kept_run is None
    assert row_other.tier_b_reviewed_at is not None and row_other.tier_b_auto_kept_run == other_run
