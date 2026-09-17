"""Integration tests for batch/build_ocr_text_embeddings.py.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.build_ocr_text_embeddings import run
from batch.utils.text_heavy_classifier import CLASSIFIER_NAME, TEXT_HEAVY
from Storage.models import Image, ImageClassification, OCRText, OCRTextEmbedding

CONFIDENCE_MIN = 0.4
LANG_SCORE_MIN = 0.3


async def _text_heavy_image(db_session, status="active"):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageClassification(
        image_id=image.id, classifier=CLASSIFIER_NAME, result=TEXT_HEAVY, details={}))
    await db_session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_embeds_and_persists_a_text_heavy_image(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="a real sentence to embed", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    metrics = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")
    await db_session.commit()

    assert metrics.counters_dict() == {"embedded": 1}
    row = (await db_session.execute(
        select(OCRTextEmbedding).where(OCRTextEmbedding.image_id == image.id)
    )).scalar_one()
    assert len(row.embedding) == 384


@pytest.mark.asyncio(loop_scope="session")
async def test_rerun_finds_no_candidates_for_already_embedded_images(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="a real sentence to embed", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    first = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")
    await db_session.commit()
    assert first.counters_dict() == {"embedded": 1}

    second = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")
    await db_session.commit()
    assert second.counters_dict() == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_non_text_heavy_image_is_excluded(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status="active")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageClassification(
        image_id=image.id, classifier=CLASSIFIER_NAME, result="not_text_heavy", details={}))
    db_session.add(OCRText(image_id=image.id, text="a real sentence", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    metrics = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")

    assert metrics.counters_dict() == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_entirely_filtered_text_is_excluded(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="noise", confidence=0.1, lang_score=0.9))
    await db_session.flush()

    metrics = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")

    assert metrics.counters_dict() == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_no_candidates_returns_empty_metrics_without_loading_model(db_session):
    metrics = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")
    assert metrics.counters_dict() == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_two_candidates_one_embeddable_one_filtered_out(db_session):
    embeddable = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=embeddable.id, text="a real sentence to embed", confidence=0.9, lang_score=0.9))
    filtered_out = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=filtered_out.id, text="noise", confidence=0.1, lang_score=0.9))
    await db_session.flush()

    metrics = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "active")
    await db_session.commit()

    assert metrics.counters_dict() == {"embedded": 1}
    embedded_ids = (await db_session.execute(select(OCRTextEmbedding.image_id))).scalars().all()
    assert embedded_ids == [embeddable.id]
