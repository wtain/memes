"""
Integration tests for batch/build_ocr_lemmas.py's run() -- the full
grouping -> saving -> status-marking pipeline exercised end-to-end against
a real database. Distinct from the unit-level group_lemmas_by_image tests
(batch/tests/test_ocr_lemmas_grouping.py) and the repository-level
OCRLemmasSaver/ImageProcessingStatusRepository tests
(tests/integration/test_ocr_lemmas_repository.py,
tests/integration/test_image_processing_status_repository.py) -- this file
proves those pieces are wired together correctly through the real code path.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.build_ocr_lemmas import run
from metrics.listener import SimpleMetricsListener
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import OCR_LEMMAS_PIPELINE
from repository.ocr_text import OCRTextRepository
from rules.normalize import make_morph
from Storage.models import Image, OCRLemma

_BBOX = [[0, 0], [10, 0], [10, 10], [0, 10]]
_MORPH = make_morph()


async def _insert_image_with_ocr(session, text: str, confidence: float, language: str = "en") -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    ocr_repo = OCRTextRepository(session)
    await ocr_repo.overwrite_texts(image, [(_BBOX, text, confidence)], language)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_full_mode_indexes_lemmas_and_marks_image_done(db_session):
    image = await _insert_image_with_ocr(db_session, "grumpy cat picture", 0.9)

    metrics = SimpleMetricsListener()
    await run(db_session, incremental=False, ocr_confidence_min=0.4, ocr_lang_score_min=0.3,
              min_word_length=3, morph=_MORPH, metrics=metrics)

    lemmas = (await db_session.execute(
        select(OCRLemma.lemma).where(OCRLemma.image_id == image.id)
    )).scalars().all()
    # "grumpy" stems to "grumpi" -- this row is tagged "en" (the default
    # language of _insert_image_with_ocr), so it goes through
    # lemmatize_word's STEMMABLE_LANGUAGES branch (see
    # docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md),
    # not plain lowercasing.
    assert "grumpi" in lemmas
    assert "cat" in lemmas

    status = await ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE).get_image_status(image.id)
    assert status.status == "done"


@pytest.mark.asyncio(loop_scope="session")
async def test_all_filtered_image_still_gets_marked_done(db_session):
    """Regression test for the Round 2 convergence fix, proven through the
    real run() code path rather than only through the unit-tested
    group_lemmas_by_image function in isolation."""
    image = await _insert_image_with_ocr(db_session, "garbled text", 0.1)  # below confidence_min=0.4

    metrics = SimpleMetricsListener()
    await run(db_session, incremental=False, ocr_confidence_min=0.4, ocr_lang_score_min=0.3,
              min_word_length=3, morph=_MORPH, metrics=metrics)

    lemmas = (await db_session.execute(
        select(OCRLemma).where(OCRLemma.image_id == image.id)
    )).scalars().all()
    assert lemmas == []

    status = await ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE).get_image_status(image.id)
    assert status is not None
    assert status.status == "done"


@pytest.mark.asyncio(loop_scope="session")
async def test_incremental_mode_skips_already_done_images(db_session):
    already_done = await _insert_image_with_ocr(db_session, "old news", 0.9)
    not_yet_done = await _insert_image_with_ocr(db_session, "grumpy cat", 0.9)

    await ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE).mark_done_by_id(already_done.id)
    await db_session.flush()

    metrics = SimpleMetricsListener()
    await run(db_session, incremental=True, ocr_confidence_min=0.4, ocr_lang_score_min=0.3,
              min_word_length=3, morph=_MORPH, metrics=metrics)

    already_done_lemmas = (await db_session.execute(
        select(OCRLemma).where(OCRLemma.image_id == already_done.id)
    )).scalars().all()
    assert already_done_lemmas == []  # untouched -- was already marked done, never reprocessed

    not_yet_done_lemmas = (await db_session.execute(
        select(OCRLemma.lemma).where(OCRLemma.image_id == not_yet_done.id)
    )).scalars().all()
    # "grumpy" stems to "grumpi" -- see the comment in
    # test_full_mode_indexes_lemmas_and_marks_image_done above.
    assert "grumpi" in not_yet_done_lemmas
    assert "cat" in not_yet_done_lemmas
