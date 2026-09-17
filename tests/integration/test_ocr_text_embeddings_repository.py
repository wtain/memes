"""Integration tests for repository/ocr_text_embeddings.py.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.utils.text_heavy_classifier import CLASSIFIER_NAME, TEXT_HEAVY
from repository.ocr_text_embeddings import OCRTextEmbeddingsRepository
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
async def test_returns_concatenated_text_for_a_text_heavy_image(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="hello world", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    repo = OCRTextEmbeddingsRepository(db_session)
    out = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, CONFIDENCE_MIN, LANG_SCORE_MIN)

    assert out == {image.id: "hello world"}


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_already_embedded_images(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="hello world", confidence=0.9, lang_score=0.9))
    await db_session.flush()
    db_session.add(OCRTextEmbedding(image_id=image.id, embedding=[0.0] * 384))
    await db_session.flush()

    repo = OCRTextEmbeddingsRepository(db_session)
    out = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, CONFIDENCE_MIN, LANG_SCORE_MIN)

    assert out == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_non_text_heavy_image_even_with_ocr_text(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status="active")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageClassification(
        image_id=image.id, classifier=CLASSIFIER_NAME, result="not_text_heavy", details={}))
    db_session.add(OCRText(image_id=image.id, text="hello world", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    repo = OCRTextEmbeddingsRepository(db_session)
    out = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, CONFIDENCE_MIN, LANG_SCORE_MIN)

    assert out == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_image_whose_ocr_text_is_entirely_filtered_out(db_session):
    image = await _text_heavy_image(db_session)
    db_session.add(OCRText(image_id=image.id, text="noise", confidence=0.1, lang_score=0.9))
    await db_session.flush()

    repo = OCRTextEmbeddingsRepository(db_session)
    out = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, CONFIDENCE_MIN, LANG_SCORE_MIN)

    assert out == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_pending_image_excluded_when_querying_active(db_session):
    image = await _text_heavy_image(db_session, status="pending")
    db_session.add(OCRText(image_id=image.id, text="hello world", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    repo = OCRTextEmbeddingsRepository(db_session)
    out = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, CONFIDENCE_MIN, LANG_SCORE_MIN, status="active")

    assert out == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_pending_image_included_when_querying_pending(db_session):
    image = await _text_heavy_image(db_session, status="pending")
    db_session.add(OCRText(image_id=image.id, text="hello world", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    repo = OCRTextEmbeddingsRepository(db_session)
    out = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, CONFIDENCE_MIN, LANG_SCORE_MIN, status="pending")

    assert out == {image.id: "hello world"}


@pytest.mark.asyncio(loop_scope="session")
async def test_save_upserts_on_second_call(db_session):
    image = await _text_heavy_image(db_session)
    repo = OCRTextEmbeddingsRepository(db_session)

    await repo.save(image.id, [0.1] * 384)
    await db_session.commit()
    await repo.save(image.id, [0.2] * 384)
    await db_session.commit()

    rows = (await db_session.execute(
        select(OCRTextEmbedding).where(OCRTextEmbedding.image_id == image.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].embedding[0] == pytest.approx(0.2)
    assert rows[0].computed_at is not None
