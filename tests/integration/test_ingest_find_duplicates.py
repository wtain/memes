"""
Integration tests for batch/ingest_find_duplicates.py (ingestion Tier A/B candidate
population).

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select, text

from Backend.app.services.business_metrics_service import BusinessMetricsService
from batch.ingest_find_duplicates import find_batch_duplicates, record_proposals
from repository.batch_runs import BatchRunRepository
from Storage.models import (
    BatchRun, Embedding, Image, ImageClassification, OCRLemma, OCRTextEmbedding, ProcessEvent, RunStatus,
    TmpDuplicates)

_DIM = 512


def _unit_vector(index: int) -> list[float]:
    vec = [0.0] * _DIM
    vec[index] = 1.0
    return vec


async def _insert_image(session, embedding_values, status: str, batch_id=None) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    session.add(Embedding(image_id=image.id, embedding=embedding_values))
    await session.flush()
    return image.id


@pytest.mark.asyncio(loop_scope="session")
async def test_finds_in_batch_match(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, match_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.match_source == "in_batch"


@pytest.mark.asyncio(loop_scope="session")
async def test_finds_cross_corpus_match(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    pending = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    active = await _insert_image(db_session, _unit_vector(0), "active")

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, match_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {pending, active}
    assert row.match_source == "cross_corpus"


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_other_batches_pending_images(db_session):
    """A pending image from a different, concurrent-or-prior batch is neither an in-batch
    sibling nor part of the active corpus -- it must not surface as a candidate."""
    runs_repo = BatchRunRepository(db_session)
    # Create and complete a prior batch
    other_batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    await runs_repo.commit(other_batch_id)
    # Create the current batch to test
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    await _insert_image(db_session, _unit_vector(0), "pending", other_batch_id)

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_respects_threshold(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    await _insert_image(db_session, _unit_vector(1), "pending", batch_id)  # orthogonal

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 0


def test_should_advance_stage_tier_a_only_before_tier_a_review():
    """A re-run of --tier tier_a (operator re-joins a batch mid-review per the runbook's
    Concurrency section, or the scheduled ingest_auto_prep driver re-running every tick) must
    not stomp a later stage like tier_b_review back to tier_a_review."""
    from batch.ingest_find_duplicates import should_advance_stage

    assert should_advance_stage("hash_dedup", "tier_a_review") is True
    assert should_advance_stage("format_validation", "tier_a_review") is True
    assert should_advance_stage("tier_a_review", "tier_a_review") is False
    assert should_advance_stage("tier_b_review", "tier_a_review") is False
    assert should_advance_stage(None, "tier_a_review") is False


def test_should_advance_stage_tier_b_from_anything_before_it():
    """Tier B can advance from any earlier stage, but not stay at or rewind from tier_b_review."""
    from batch.ingest_find_duplicates import should_advance_stage

    assert should_advance_stage("hash_dedup", "tier_b_review") is True
    assert should_advance_stage("format_validation", "tier_b_review") is True
    assert should_advance_stage("tier_a_review", "tier_b_review") is True
    assert should_advance_stage("tier_b_review", "tier_b_review") is False


async def _mark_text_heavy(session, image_id: uuid.UUID) -> None:
    session.add(ImageClassification(
        image_id=image_id, classifier="text_heavy_v1", result="text_heavy", details={}))
    await session.flush()


async def _insert_ocr_text_embedding(session, image_id: uuid.UUID, embedding_values: list[float]) -> None:
    session.add(OCRTextEmbedding(image_id=image_id, embedding=embedding_values))
    await session.flush()


async def _insert_ocr_lemmas(session, image_id: uuid.UUID, lemmas: set[str]) -> None:
    for lemma in lemmas:
        session.add(OCRLemma(image_id=image_id, lemma=lemma))
    await session.flush()


def _text_unit_vector(index: int) -> list[float]:
    vec = [0.0] * 384
    vec[index] = 1.0
    return vec


def _near_text_unit_vector(index: int, epsilon: float = 0.41) -> list[float]:
    """A 384-dim OCR-text vector close to (but not identical to) _text_unit_vector(index) --
    cosine distance from _text_unit_vector(index) works out to ~0.0747 for the default epsilon,
    strictly between tier_a's threshold (0.05) and TEXT_EMBEDDING_LOOSE_THRESHOLD (0.10) -- the
    exact band needed to discriminate the correct literal-constant probe threshold from a
    min(threshold, TEXT_EMBEDDING_LOOSE_THRESHOLD) regression."""
    vec = [0.0] * 384
    vec[index] = 1.0
    other = (index + 1) % 384
    vec[other] = epsilon
    norm = (1.0 + epsilon ** 2) ** 0.5
    return [v / norm for v in vec]


@pytest.mark.asyncio(loop_scope="session")
async def test_general_clip_excludes_text_heavy_pair_safety_net_still_finds_it(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)  # identical -> distance 0
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    # Both images need an ocr_text_embeddings row for _EXCLUDE_TEXT_HEAVY_PAIR (keyed on
    # embedding existence, not classification) to actually exclude this pair from the general
    # CLIP probe. Deliberately far apart in OCR-text space (orthogonal -> distance 1.0, past
    # TEXT_EMBEDDING_LOOSE_THRESHOLD) so the OCR-text probe doesn't find them either -- the
    # safety net must be the only probe that surfaces this pair.
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(1))

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance_source == "clip"  # the safety net, since the general probe excludes this pair


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_finds_pair_at_tier_b(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(1), "pending", batch_id)  # orthogonal CLIP
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    shared_lemmas = {"репост", "мем", "смешно", "картинка", "текст"}
    await _insert_ocr_lemmas(db_session, a, shared_lemmas)
    await _insert_ocr_lemmas(db_session, b, shared_lemmas)

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.12)  # tier_b threshold

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance_source == "ocr_text"


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_ignores_threshold_argument(db_session):
    """Regression test for the min(threshold, TEXT_EMBEDDING_LOOSE_THRESHOLD) bug this plan's
    Global Constraints explicitly forbid reintroducing -- the OCR-text probe must still find a
    pair at distance ~0 even when called with Tier A's tight threshold (0.05), since it always
    uses TEXT_EMBEDDING_LOOSE_THRESHOLD (0.10) regardless of the `threshold` argument."""
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(1), "pending", batch_id)
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _near_text_unit_vector(0))  # distance ~0.0747:
    # strictly between tier_a's threshold (0.05) and TEXT_EMBEDDING_LOOSE_THRESHOLD (0.10) --
    # this is what makes the test actually discriminate the literal-constant probe threshold from
    # a min()-derived regression, rather than passing under both (a distance-0 pair would)
    shared_lemmas = {"репост", "мем", "смешно", "картинка", "текст"}
    await _insert_ocr_lemmas(db_session, a, shared_lemmas)
    await _insert_ocr_lemmas(db_session, b, shared_lemmas)

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.05)  # tier_a threshold

    assert inserted == 1  # only the OCR-text probe finds this pair -- general/safety-net CLIP
    # both miss it (orthogonal CLIP vectors, distance 1.0, past even the safety net's 0.02)
    row = (await db_session.execute(
        text("SELECT distance_source FROM tmp_duplicates")
    )).one()
    assert row.distance_source == "ocr_text"  # found despite tier_a's tight CLIP threshold


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_excludes_pair_below_overlap_coefficient(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(1), "pending", batch_id)
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    await _insert_ocr_lemmas(db_session, a, {"дом", "кот", "утро", "чай"})
    await _insert_ocr_lemmas(db_session, b, {"машина", "дорога", "город", "ночь"})  # zero overlap

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 0
    rows = (await db_session.execute(text("SELECT * FROM tmp_duplicates"))).all()
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_includes_pair_above_overlap_coefficient(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(1), "pending", batch_id)
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    await _insert_ocr_lemmas(db_session, a, {"дом", "кот", "утро", "чай", "стол"})
    await _insert_ocr_lemmas(db_session, b, {"дом", "кот", "утро", "чай", "окно", "дверь"})

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance_source == "ocr_text"


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_tolerates_image_with_no_ocr_lemmas(db_session):
    """Regression test mirroring rebuild_duplicates.py's own test of the same name: an image with
    zero ocr_lemmas rows (common in practice until build_ocr_lemmas.py's coverage catches up with
    ocr_text_embeddings) must be excluded cleanly by _OCR_LEMMA_OVERLAP_CHECK's GREATEST(..., 1)
    guard, not raise a DivisionByZeroError."""
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(1), "pending", batch_id)
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    await _insert_ocr_lemmas(db_session, a, {"дом", "кот", "утро", "чай"})
    # b deliberately gets NO ocr_lemmas rows at all -- LEAST(4, 0) = 0

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)  # must not raise

    assert inserted == 0
    rows = (await db_session.execute(text("SELECT * FROM tmp_duplicates"))).all()
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_ingestion_probe_ignores_cut_proposed_images(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    await _insert_image(db_session, _unit_vector(0), "cut_proposed")

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 0


async def _process_events(session, process, subject):
    rows = await session.execute(select(ProcessEvent.event).where(
        ProcessEvent.process == process, ProcessEvent.subject_id == subject))
    return set(rows.scalars())


@pytest.mark.asyncio(loop_scope="session")
async def test_record_proposals_follows_the_tier_bands_and_is_idempotent(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    near = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)     # identical to an active image: band A
    loose = await _insert_image(db_session, _unit_vector(5), "pending", batch_id)    # a hand-made 0.08 pair: band B only
    active = await _insert_image(db_session, _unit_vector(0), "active")
    active_loose = await _insert_image(db_session, _unit_vector(9), "active")
    await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)
    db_session.add(TmpDuplicates(image_id1=min(loose, active_loose), image_id2=max(loose, active_loose),
                                 distance=0.08, match_source="cross_corpus"))
    await db_session.flush()

    await record_proposals(db_session, batch_id, "tier_a")
    await record_proposals(db_session, batch_id, "tier_b")
    await record_proposals(db_session, batch_id, "tier_a")        # a re-run adds nothing

    assert await _process_events(db_session, "ingest_tier_a", near) == {"proposed"}
    assert await _process_events(db_session, "ingest_tier_b", near) == set()
    assert await _process_events(db_session, "ingest_tier_a", loose) == set()
    assert await _process_events(db_session, "ingest_tier_b", loose) == {"proposed"}
    assert await _process_events(db_session, "ingest_tier_a", active) == set()
    items = (await BusinessMetricsService(db_session).batch_metrics("ingest_tier_a"))["items"]
    assert next(i for i in items if i["run_id"] == str(batch_id))["counts"]["proposed"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_record_proposals_ignores_a_pending_image_of_another_run(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    other = BatchRun(kind="ingestion", trigger="manual", status=str(RunStatus.completed))
    db_session.add(other)
    await db_session.flush()
    mine = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    theirs = await _insert_image(db_session, _unit_vector(0), "pending", other.run_id)
    # A stale pair between the two runs' images (the find job itself never creates one): only this run's image counts.
    db_session.add(TmpDuplicates(image_id1=min(mine, theirs), image_id2=max(mine, theirs), distance=0.02,
                                 match_source="cross_corpus"))
    await db_session.flush()

    await record_proposals(db_session, batch_id, "tier_a")

    assert await _process_events(db_session, "ingest_tier_a", mine) == {"proposed"}
    assert await _process_events(db_session, "ingest_tier_a", theirs) == set()
