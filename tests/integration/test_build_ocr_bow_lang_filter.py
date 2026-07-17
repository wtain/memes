"""
Integration test proving build_bow.py's OCR_LANG_SCORE_MIN filter excludes
cross-language garbage rows from the word-frequency output.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid
from unittest.mock import Mock

import pytest

from batch.build_bow import _build_ocr_bow
from metrics.listener import SimpleMetricsListener
from rules.normalize import make_morph
from Storage.models import Image, OCRText


@pytest.mark.asyncio(loop_scope="session")
async def test_build_ocr_bow_excludes_low_lang_score_rows(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    db_session.add_all([
        OCRText(
            image_id=image.id,
            text="genuine english words here",
            confidence=0.9,
            language="en",
            lang_score=1.0,
        ),
        OCRText(
            image_id=image.id,
            text="garbled cross language misread",
            confidence=0.9,
            language="en",
            lang_score=0.05,
        ),
    ])
    await db_session.flush()

    morph = make_morph()
    metrics = SimpleMetricsListener()
    output = await _build_ocr_bow(
        db_session,
        morph,
        confidence_min=0.4,
        lang_score_min=0.3,
        min_word_length=3,
        min_frequency=1,
        metrics=metrics,
    )

    en_lemmas = output.get("en", {})
    assert "genuine" in en_lemmas
    assert "garbled" not in en_lemmas


@pytest.mark.asyncio(loop_scope="session")
async def test_build_ocr_bow_skips_pymorphy3_for_non_russian_language(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    db_session.add(
        OCRText(
            image_id=image.id,
            text="genuine spanish words aqui",
            confidence=0.9,
            language="es",
            lang_score=1.0,
        )
    )
    await db_session.flush()

    morph = make_morph()
    wrapped_morph = Mock(wraps=morph)
    metrics = SimpleMetricsListener()
    output = await _build_ocr_bow(
        db_session,
        wrapped_morph,
        confidence_min=0.4,
        lang_score_min=0.3,
        min_word_length=3,
        min_frequency=1,
        metrics=metrics,
    )

    wrapped_morph.parse.assert_not_called()
    es_lemmas = output.get("es", {})
    assert "genuine" in es_lemmas
    assert "aqui" in es_lemmas
