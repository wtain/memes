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
from Storage.models import Embedding, Image, ImageClassification, OCRTextEmbedding

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

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance, distance_source FROM tmp_duplicates "
             "WHERE distance_source = 'ocr_text'")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_incremental_rerun_does_not_skip_ocr_text_probe_for_already_clip_probed_image(db_session):
    """Task 1's own §2 regression: a second signal's probe must not be silently skipped just
    because a different signal's probe already inserted a row for the same image earlier in the
    same incremental probe-set fragment's lifetime."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))  # orthogonal CLIP, no general-probe match
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))

    # First rebuild: general probe finds nothing (a,b are CLIP-orthogonal and text-heavy-excluded
    # anyway); safety net finds nothing (CLIP-orthogonal, past CLIP_SAFETY_NET_THRESHOLD); OCR-text
    # probe finds the pair.
    first = await rebuild_active_library(db_session, k=20, threshold=0.3)
    assert first == 1

    # Second, incremental rebuild: nothing new to find, but this must not raise or behave
    # differently -- confirms the incremental NOT EXISTS fragment's distance_source gating didn't
    # somehow desync between the two signals.
    second = await rebuild_active_library(db_session, k=20, threshold=0.3)
    assert second == 0
