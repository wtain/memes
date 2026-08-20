"""
Integration tests confirming description-note lemmas participate in the main search
match the same way OCR lemmas do -- including as a fallback for images with zero OCR
text, per docs/superpowers/specs/2026-08-20-description-notes-design.md.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.ocr_lemmas import matching_image_ids
from Storage.models import DescriptionNoteLemma, Image, OCRLemma


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_only_a_note_is_found_by_exact_lemma_match(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="pineapple"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "pineapple")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_only_ocr_still_matches_unaffected(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="pineapple"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "pineapple")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_multi_token_query_matches_across_ocr_and_note_sources_per_token(db_session):
    """AND is across tokens, OR is across sources within a token -- an image whose
    tokens are split across OCR and note text (neither source alone has both) must
    still match, since each token only needs to hit one source."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="pineapple"))
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="upsidedown"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "pineapple upsidedown")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_note_lemma_does_not_match_a_different_image(db_session):
    image_a = Image(filename=f"{uuid.uuid4()}.jpg")
    image_b = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([image_a, image_b])
    await db_session.flush()
    db_session.add(DescriptionNoteLemma(image_id=image_a.id, lemma="pineapple"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "pineapple")

    assert ids == {image_a.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_note_lemma_matches_via_trigram_fuzzy_fallback(db_session):
    """Exact match fails (typo'd query), trigram similarity against
    description_note_lemmas should still find it -- mirrors the existing OCR
    trigram-fallback behavior, now extended to notes."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="pineapple"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "pinapple")  # missing one 'e'

    assert ids == {image.id}
