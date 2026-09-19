"""
Integration tests for batch/rebuild_duplicates.py.

These tests require a live PostgreSQL instance with pgvector. They are
intentionally excluded from the standard `pytest` run (which uses mocked DB)
and are collected only when running from tests/integration/ explicitly or via
the integration-tests CI workflow.
"""
import uuid

import pytest
from sqlalchemy import text

from batch.rebuild_duplicates import find_duplicates, rebuild_active_library, _ACTIVE_CORPUS_FILTER_CLIP
from Storage.models import Embedding, Image, ImageClassification, OCRLemma, OCRTextEmbedding

_DIM = 512


def _unit_vector(index: int) -> list[float]:
    vec = [0.0] * _DIM
    vec[index] = 1.0
    return vec


async def _insert_image_with_embedding(session, embedding_values: list[float], status: str = "active") -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    embedding = Embedding(image_id=image.id, embedding=embedding_values)
    session.add(embedding)
    await session.flush()
    return image.id


@pytest.mark.asyncio(loop_scope="session")
async def test_rebuild_inserts_one_normalized_row_for_a_close_pair(db_session):
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(0))  # identical -> distance 0

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 1  # not 2 -- LEAST/GREATEST normalizes (a, b) and (b, a) to one row
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance, match_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.image_id1 < row.image_id2  # LEAST/GREATEST ordering
    assert row.distance == pytest.approx(0.0, abs=1e-6)
    assert row.match_source == "cross_corpus"


@pytest.mark.asyncio(loop_scope="session")
async def test_rebuild_excludes_pairs_past_threshold(db_session):
    await _insert_image_with_embedding(db_session, _unit_vector(0))
    await _insert_image_with_embedding(db_session, _unit_vector(1))  # orthogonal -> distance 1.0

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_rebuild_never_creates_a_self_pair(db_session):
    await _insert_image_with_embedding(db_session, _unit_vector(0))

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_incremental_rebuild_only_probes_images_with_no_existing_row(db_session):
    await _insert_image_with_embedding(db_session, _unit_vector(0))
    await _insert_image_with_embedding(db_session, _unit_vector(0))

    first = await rebuild_active_library(db_session, k=20, threshold=0.3)
    second = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert first == 1
    assert second == 0  # both images already have a tmp_duplicates row -- nothing left to probe


@pytest.mark.asyncio(loop_scope="session")
async def test_incremental_rebuild_finds_new_image_against_existing_ones(db_session):
    await _insert_image_with_embedding(db_session, _unit_vector(0))
    await rebuild_active_library(db_session, k=20, threshold=0.3)  # nothing to find yet (1 image)

    await _insert_image_with_embedding(db_session, _unit_vector(0))  # new, identical embedding
    second = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert second == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_full_rebuild_reprobes_without_duplicating_rows(db_session):
    await _insert_image_with_embedding(db_session, _unit_vector(0))
    await _insert_image_with_embedding(db_session, _unit_vector(0))
    await rebuild_active_library(db_session, k=20, threshold=0.3)

    # A --full rebuild re-probes every active image from scratch; ON CONFLICT DO NOTHING
    # must keep the pair from being duplicated even though both directions get re-found.
    await rebuild_active_library(db_session, k=20, threshold=0.3, full=True)

    count = (await db_session.execute(text("SELECT COUNT(*) FROM tmp_duplicates"))).scalar_one()
    assert count == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_full_rebuild_only_clears_active_active_pairs(db_session):
    """A --full rebuild must not touch rows involving a pending image -- those belong to
    an in-flight ingestion review, not routine active-library maintenance."""
    active_a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    active_b = await _insert_image_with_embedding(db_session, _unit_vector(0))
    pending = await _insert_image_with_embedding(db_session, _unit_vector(0), status="pending")

    await db_session.execute(text(
        "INSERT INTO tmp_duplicates (image_id1, image_id2, distance, match_source) "
        "VALUES (:a, :b, 0.0, 'cross_corpus'), (:a, :p, 0.0, 'in_batch')"
    ), {"a": min(active_a, active_b), "b": max(active_a, active_b), "p": pending})

    await rebuild_active_library(db_session, k=20, threshold=0.3, full=True)

    remaining_pending_row = (await db_session.execute(
        text("SELECT COUNT(*) FROM tmp_duplicates WHERE image_id1 = :p OR image_id2 = :p"),
        {"p": pending},
    )).scalar_one()
    assert remaining_pending_row == 1  # untouched by the active-only --full clear


@pytest.mark.asyncio(loop_scope="session")
async def test_rebuild_excludes_pending_images_from_probe_and_corpus(db_session):
    await _insert_image_with_embedding(db_session, _unit_vector(0), status="active")
    await _insert_image_with_embedding(db_session, _unit_vector(0), status="pending")

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 0  # only one active image -- nothing to pair it with


@pytest.mark.asyncio(loop_scope="session")
async def test_find_duplicates_is_reusable_with_arbitrary_scoping(db_session):
    """The underlying primitive isn't active-library-specific -- a caller (like the future
    ingestion pipeline) can pass its own probe/corpus SQL fragments."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(0))

    probe_sql = f"SELECT i.id, e.embedding FROM images i JOIN embeddings e ON e.image_id = i.id WHERE i.id = '{a}'"
    inserted = await find_duplicates(db_session, probe_sql, _ACTIVE_CORPUS_FILTER_CLIP, k=5,
                                      threshold=0.3, distance_source="clip")

    assert inserted == 1
    row = (await db_session.execute(text("SELECT image_id1, image_id2 FROM tmp_duplicates"))).one()
    assert {row.image_id1, row.image_id2} == {a, b}


@pytest.mark.asyncio(loop_scope="session")
async def test_expected_indexes_and_constraint_exist(db_session):
    result = await db_session.execute(text("""
        SELECT indexname FROM pg_indexes WHERE tablename = 'tmp_duplicates'
    """))
    index_names = {row[0] for row in result.all()}
    assert "idx_tmp_duplicates_distance" in index_names
    assert "ix_tmp_duplicates_image_id1" in index_names
    assert "ix_tmp_duplicates_image_id2" in index_names

    result = await db_session.execute(text("""
        SELECT conname FROM pg_constraint WHERE conrelid = 'tmp_duplicates'::regclass
    """))
    constraint_names = {row[0] for row in result.all()}
    assert "uq_tmp_duplicates_pair" in constraint_names


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
    vec = [0.0] * 384  # OCR_TEXT_EMBEDDING_DIM
    vec[index] = 1.0
    return vec


def _near_unit_vector(index: int, epsilon: float = 0.5) -> list[float]:
    """A CLIP vector close to (but not identical to) the pure unit vector at `index` -- cosine
    distance from _unit_vector(index) works out to ~0.106 for the default epsilon (comfortably
    inside the general probe's 0.3 threshold, and nowhere near the tight 0.02 safety-net
    threshold, though that only matters if the corpus filter would even consider the pair)."""
    vec = [0.0] * _DIM
    vec[index] = 1.0
    other = (index + 1) % _DIM
    vec[other] = epsilon
    norm = (1.0 + epsilon ** 2) ** 0.5
    return [v / norm for v in vec]


@pytest.mark.asyncio(loop_scope="session")
async def test_safety_net_finds_tight_text_heavy_match_general_probe_excludes(db_session):
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(0))  # identical -> distance 0
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    # Both images need an ocr_text_embeddings row for _EXCLUDE_TEXT_HEAVY_PAIR (keyed on
    # embedding existence, not classification -- see the constant's own comment) to actually
    # exclude this pair from the general CLIP probe. Deliberately far apart in OCR-text space
    # (orthogonal -> distance 1.0, past TEXT_EMBEDDING_LOOSE_THRESHOLD) so the OCR-text probe
    # doesn't find them either -- the safety net must be the only probe that surfaces this pair.
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(1))

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance, distance_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance_source == "clip"  # the safety net, not the excluded general probe
    assert row.distance == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_safety_net_not_starved_by_unrelated_general_probe_match(db_session):
    """The regression test for the correctness bug this plan's Global Constraints call out
    explicitly: a text-heavy image with an unrelated non-text-heavy CLIP match must still get
    its own safety-net-eligible text-heavy near-duplicate found, even though the general probe
    already inserted a distance_source='clip' row for it first. This must fail if the safety-net
    probe is changed back to share the general probe's own incremental marker."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))         # text-heavy
    b = await _insert_image_with_embedding(db_session, _unit_vector(0))         # text-heavy, near-dup of a
    c = await _insert_image_with_embedding(db_session, _near_unit_vector(0))    # NOT text-heavy, close enough to a for general CLIP (~0.106)
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    # c is deliberately left unclassified (not text_heavy)

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    pairs = {
        tuple(sorted((str(r.image_id1), str(r.image_id2)))): r.distance_source
        for r in (await db_session.execute(
            text("SELECT image_id1, image_id2, distance_source FROM tmp_duplicates")
        )).all()
    }
    assert tuple(sorted((str(a), str(b)))) in pairs  # safety net found the text-heavy pair
    assert tuple(sorted((str(a), str(c)))) in pairs  # general probe found the unrelated pair
    assert pairs[tuple(sorted((str(a), str(b))))] == "clip"
    assert pairs[tuple(sorted((str(a), str(c))))] == "clip"


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_finds_pair_via_ocr_text_embeddings(db_session):
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))  # orthogonal CLIP -- general probe won't find this
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))  # identical OCR-text embedding
    # Matching ocr_lemmas so the new lemma-overlap corroboration gate (Task 2 of
    # docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md) doesn't reject this
    # pair -- 5 shared lemmas, well above MIN_LEMMA_COUNT_FLOOR (3) and giving overlap
    # coefficient 1.0, well above MIN_LEMMA_OVERLAP_COEFFICIENT (0.2).
    shared_lemmas = {"репост", "мем", "смешно", "картинка", "текст"}
    await _insert_ocr_lemmas(db_session, a, shared_lemmas)
    await _insert_ocr_lemmas(db_session, b, shared_lemmas)

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance, distance_source FROM tmp_duplicates "
             "WHERE distance_source = 'ocr_text'")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_text_heavy_pair_without_ocr_embeddings_falls_back_to_general_clip_probe(db_session):
    """Regression test for a whole-branch-review finding: images classified text_heavy but with
    no ocr_text_embeddings row (e.g. OCR text too short/low-confidence to pass
    build_ocr_text_embeddings' own quality filter) must not silently lose all mid-band duplicate
    coverage. _EXCLUDE_TEXT_HEAVY_PAIR is keyed on ocr_text_embeddings existence, not
    classification, so a pair like this falls back to the general CLIP probe instead of being
    excluded with no replacement signal."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _near_unit_vector(0))  # ~0.1056: past the
    # 0.02 safety net threshold, well within the general probe's 0.3 threshold in this test
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    # deliberately no ocr_text_embeddings for either image -- the exact scenario this test guards

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance, distance_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance_source == "clip"  # found by the general probe's fallback coverage -- the
    # 0.02-only safety net alone would have missed this (0.1056 > 0.02), and with the OLD
    # classification-based exclusion this pair would have been excluded from general with no
    # replacement, causing this query to find zero rows
    assert row.distance == pytest.approx(0.10557280900008414, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_incremental_rerun_does_not_skip_ocr_text_probe_for_already_clip_probed_image(db_session):
    """Task 1's own §2 regression: a second signal's probe must not be silently skipped just
    because a different signal's probe already inserted a row for the same image earlier in the
    same incremental probe-set fragment's lifetime.

    Strengthened per final-whole-branch-review finding 4: the original fixture never actually gave
    `a` a `distance_source='clip'` row before the OCR-text probe ran within the same first call, so
    it couldn't have failed for the reason its name/docstring claims (the OCR incremental probe's
    own SQL hardcodes `distance_source = 'ocr_text'` in its NOT EXISTS check, so it was always
    structurally immune regardless). `c` is added specifically to give `a` a real
    `distance_source='clip'` row (from the general probe) in the same `rebuild_active_library()`
    call that the OCR-text probe also runs in, so this test now genuinely exercises the scenario
    its docstring describes."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))  # orthogonal CLIP, no general-probe match
    c = await _insert_image_with_embedding(db_session, _near_unit_vector(0))  # NOT text-heavy,
    # ~0.1056 from a -- close enough for the general CLIP probe to insert a distance_source='clip'
    # row touching a, before/alongside the OCR-text probe runs in the same call.
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    shared_lemmas = {"репост", "мем", "смешно", "картинка", "текст"}
    await _insert_ocr_lemmas(db_session, a, shared_lemmas)
    await _insert_ocr_lemmas(db_session, b, shared_lemmas)
    # c is deliberately left unclassified (not text_heavy) and without an ocr_text_embeddings row

    # First rebuild: general probe excludes (a,b) (both have ocr_text_embeddings rows) but finds
    # (a,c) (c has no ocr_text_embeddings row, so the pair isn't excluded, and is well within the
    # general 0.3 threshold); safety net finds nothing (a,b are CLIP-orthogonal, past
    # CLIP_SAFETY_NET_THRESHOLD, and c isn't text_heavy so the safety net's corpus filter excludes
    # it regardless); OCR-text probe finds (a,b) despite a already carrying a fresh
    # distance_source='clip' row from the general probe's (a,c) match earlier in this same call --
    # the actual regression this test guards against.
    first = await rebuild_active_library(db_session, k=20, threshold=0.3)
    assert first == 2

    pairs = {
        tuple(sorted((str(r.image_id1), str(r.image_id2)))): r.distance_source
        for r in (await db_session.execute(
            text("SELECT image_id1, image_id2, distance_source FROM tmp_duplicates")
        )).all()
    }
    assert pairs[tuple(sorted((str(a), str(c))))] == "clip"
    assert pairs[tuple(sorted((str(a), str(b))))] == "ocr_text"

    # Second, incremental rebuild: nothing new to find, but this must not raise or behave
    # differently -- confirms the incremental NOT EXISTS fragment's distance_source gating didn't
    # somehow desync between the two signals.
    second = await rebuild_active_library(db_session, k=20, threshold=0.3)
    assert second == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_excludes_pair_below_overlap_coefficient(db_session):
    """The core regression test for this task: a tight OCR-text-embedding match with NO shared
    ocr_lemmas must not be inserted at all -- not just excluded from clustering (that's
    clusterize.py's old, now-reverted, interim behavior), excluded from tmp_duplicates entirely."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))  # identical -- distance 0
    await _insert_ocr_lemmas(db_session, a, {"дом", "кот", "утро", "чай"})
    await _insert_ocr_lemmas(db_session, b, {"машина", "дорога", "город", "ночь"})  # zero overlap

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 0
    rows = (await db_session.execute(text("SELECT * FROM tmp_duplicates"))).all()
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_includes_pair_above_overlap_coefficient(db_session):
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    # 4 shared lemmas out of min(5, 6) = 5 total on the smaller side -> overlap coefficient 0.8,
    # comfortably above MIN_LEMMA_OVERLAP_COEFFICIENT (0.2).
    await _insert_ocr_lemmas(db_session, a, {"дом", "кот", "утро", "чай", "стол"})
    await _insert_ocr_lemmas(db_session, b, {"дом", "кот", "утро", "чай", "окно", "дверь"})

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance_source == "ocr_text"


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_lemma_overlap_check_uses_coefficient_not_raw_count(db_session):
    """Regression test for the hub-coincidence bug this design explicitly guards against
    (confirmed on real production data -- see the spec's Design §1): a probe image with a LARGE
    ocr_lemmas set can coincidentally share enough RAW lemmas with an unrelated candidate to clear
    a naive count threshold, while the ratio (normalized by the SMALLER side) correctly stays low.
    This must fail if _OCR_LEMMA_OVERLAP_CHECK is ever simplified back to a raw count."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))  # the "hub" -- huge lemma set
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))  # normal-sized, mostly-unrelated set
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    hub_lemmas = {f"слово{i}" for i in range(40)} | {"дом", "кот", "утро"}  # 43 total (the hub)
    normal_lemmas = {f"фраза{i}" for i in range(17)} | {"дом", "кот", "утро"}  # 20 total (the
    # smaller side -- this is the denominator)
    # raw intersection = 3 (>= MIN_LEMMA_COUNT_FLOOR of 3 -- a raw-count-only check with a
    # threshold <= 3 would wrongly admit this); overlap coefficient = 3 / min(43, 20) = 3/20 = 0.15,
    # below MIN_LEMMA_OVERLAP_COEFFICIENT (0.2) -- correctly rejected.
    await _insert_ocr_lemmas(db_session, a, hub_lemmas)
    await _insert_ocr_lemmas(db_session, b, normal_lemmas)

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 0
    rows = (await db_session.execute(text("SELECT * FROM tmp_duplicates"))).all()
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_lemma_overlap_check_respects_min_lemma_count_floor(db_session):
    """Two images with only 1-2 total ocr_lemmas each, fully overlapping (coefficient would
    compute to 1.0) -- must still be excluded, since MIN_LEMMA_COUNT_FLOOR (3) isn't met. Guards
    against the degenerate case where a single coincidental shared word looks like 100% overlap."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))
    await _insert_ocr_lemmas(db_session, a, {"привет"})
    await _insert_ocr_lemmas(db_session, b, {"привет"})  # coefficient = 1/1 = 1.0, but only 1 lemma total

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 0
    rows = (await db_session.execute(text("SELECT * FROM tmp_duplicates"))).all()
    assert rows == []
