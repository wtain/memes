"""
Integration tests for repository/ocr_lemmas.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.ocr_lemmas import OCRLemmasRepository, OCRLemmasSaver, matching_image_ids
from Storage.models import Image, ImageTag, OCRLemma


@pytest.mark.asyncio(loop_scope="session")
async def test_saver_writes_one_row_per_lemma(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    async with OCRLemmasSaver(db_session) as saver:
        await saver.add_lemmas(image.id, {"кот", "собака"})

    rows = (await db_session.execute(
        select(OCRLemma.lemma).where(OCRLemma.image_id == image.id)
    )).scalars().all()
    assert set(rows) == {"кот", "собака"}


@pytest.mark.asyncio(loop_scope="session")
async def test_add_lemmas_is_safe_to_call_twice_for_same_pair(db_session):
    """Regression test: a duplicate (image_id, lemma) write (e.g. from an
    overlapping/concurrent job invocation) must be a no-op, not a crash."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    async with OCRLemmasSaver(db_session) as saver:
        await saver.add_lemmas(image.id, {"кот"})
        await saver.add_lemmas(image.id, {"кот"})

    rows = (await db_session.execute(
        select(OCRLemma.lemma).where(OCRLemma.image_id == image.id)
    )).scalars().all()
    assert rows == ["кот"]


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_all_clears_table(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="кот"))
    await db_session.flush()

    await OCRLemmasRepository(db_session).delete_all()

    remaining = (await db_session.execute(select(OCRLemma))).scalars().all()
    assert remaining == []


@pytest.mark.asyncio(loop_scope="session")
async def test_no_query_returns_none(db_session):
    assert await matching_image_ids(db_session, None) is None
    assert await matching_image_ids(db_session, "") is None
    assert await matching_image_ids(db_session, "   ") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_single_lemma_matches_indexed_image(db_session):
    matching = Image(filename=f"{uuid.uuid4()}.jpg")
    other = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([matching, other])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=matching.id, lemma="полиция"),
        OCRLemma(image_id=other.id, lemma="магазин"),
    ])
    await db_session.flush()

    ids = await matching_image_ids(db_session, "полицию")

    assert ids == {matching.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_multi_word_query_requires_all_lemmas(db_session):
    both = Image(filename=f"{uuid.uuid4()}.jpg")
    only_one = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([both, only_one])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=both.id, lemma="звонить"),
        OCRLemma(image_id=both.id, lemma="полиция"),
        OCRLemma(image_id=only_one.id, lemma="звонить"),
    ])
    await db_session.flush()

    ids = await matching_image_ids(db_session, "звоню в полицию")

    assert ids == {both.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_multi_lemma_query_mixes_exact_and_fuzzy_matching(db_session):
    """One query lemma matches exactly, the other only via fuzzy fallback --
    proves the AND-across-lemmas intersection works correctly when the two
    lemmas take different code paths (_exact_lemma_ids vs _fuzzy_lemma_ids)
    within the same query."""
    both = Image(filename=f"{uuid.uuid4()}.jpg")
    only_one = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([both, only_one])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=both.id, lemma="кот"),        # matches "кот" exactly
        OCRLemma(image_id=both.id, lemma="рекламо"),    # matches "реклама" via fuzzy only
        OCRLemma(image_id=only_one.id, lemma="кот"),    # exact match, but missing the second lemma entirely
    ])
    await db_session.flush()

    ids = await matching_image_ids(db_session, "кот реклама")

    assert ids == {both.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_tag_value_matches_query_lemma(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageTag(image_id=image.id, key="животное", value="кот", source="rules"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "коты")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_no_match_returns_empty_set(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    ids = await matching_image_ids(db_session, "nonexistentword")

    assert ids == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_exact_match_takes_precedence_over_fuzzy(db_session):
    exact = Image(filename=f"{uuid.uuid4()}.jpg")
    similar_only = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([exact, similar_only])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=exact.id, lemma="реклама"),
        OCRLemma(image_id=similar_only.id, lemma="рекламо"),
    ])
    await db_session.flush()

    ids = await matching_image_ids(db_session, "реклама")

    assert ids == {exact.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_fuzzy_fallback_finds_misspelled_lemma_when_no_exact_match(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="рекламо"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "реклама")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_short_lemma_below_length_guard_is_not_fuzzy_matched(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="код"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "кот")

    assert ids == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_fuzzy_fallback_respects_configured_threshold_not_pg_trgm_default(db_session):
    """Regression test: 'который'/'колотрый' scores ~0.31 similarity -- above
    pg_trgm's own built-in default threshold (0.30) but below our configured
    threshold (settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD = 0.35). If the
    SET LOCAL statement in _fuzzy_lemma_ids ever silently failed or was
    removed, this pair would incorrectly match under pg_trgm's looser
    default -- this test is the only thing in the suite that would catch
    that, since every other fuzzy test uses pairs far above or far below
    the 0.30-0.35 gap."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="колотрый"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "который")

    assert ids == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_no_similar_match_returns_empty_set(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="совершенно"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "различие")

    assert ids == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_errative_query_matches_canonical_form_via_phonetic_fallback(db_session):
    """"превед" (an errative) has no exact match to "привет", and their
    trigram similarity (0.167, verified against the real corpus during
    design) is well below settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD
    (0.35) -- only the phonetic path connects them."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    async with OCRLemmasSaver(db_session) as saver:
        await saver.add_lemmas(image.id, {"привет"})

    ids = await matching_image_ids(db_session, "превед")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_known_word_phonetic_collision_does_not_cross_match(db_session):
    """"полка" (shelf) and "палка" (stick) are both real dictionary words
    (is_known=True) that are already their own nominative-singular form
    (lemmatization leaves each unchanged) and phonetically collide (both
    reduce to "ПАЛКА"). Their trigram similarity (0.333, verified against
    the real corpus during design) is below the 0.35 threshold, so trigram
    doesn't already connect them either -- this isolates the is_known gate
    specifically: without it, phonetic matching alone would incorrectly
    connect these two unrelated real words. (An earlier candidate pair,
    "парта"/"порта", doesn't work for this: "порта" lemmatizes to "порт",
    which no longer collides with "парта"'s code at all.)"""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    async with OCRLemmasSaver(db_session) as saver:
        await saver.add_lemmas(image.id, {"палка"})

    ids = await matching_image_ids(db_session, "полка")

    assert ids == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_short_lemma_does_not_reach_phonetic_fallback(db_session):
    """"жот" (3 chars) would phonetically collide with "жжот" (both reduce
    to "ЖАТ") if it reached the phonetic fallback at all, but its length is
    below both FUZZY_MIN_LEMMA_LENGTH and PHONETIC_MIN_LEMMA_LENGTH (both
    5 by default) -- it never attempts trigram or phonetic matching."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    async with OCRLemmasSaver(db_session) as saver:
        await saver.add_lemmas(image.id, {"жжот"})

    ids = await matching_image_ids(db_session, "жот")

    assert ids == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_non_cyrillic_query_does_not_trigger_phonetic_matching(db_session):
    """Guards against russian_metaphone() being applied to non-Russian
    query tokens -- is_known(lemma) would also be False for most
    non-Cyrillic tokens (pymorphy3's dictionary is Russian-only), so
    without the explicit Cyrillic check every Latin-script query would
    otherwise attempt (meaningless) phonetic matching too."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    async with OCRLemmasSaver(db_session) as saver:
        await saver.add_lemmas(image.id, {"привет"})

    ids = await matching_image_ids(db_session, "hello")

    assert ids == set()
