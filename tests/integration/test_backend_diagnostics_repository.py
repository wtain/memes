"""
Integration tests for Backend/app/repositories/diagnostics_repository.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest

from Backend.app.repositories.diagnostics_repository import DiagnosticsRepository
from Storage.models import Embedding, Image, ImageExtras, ImageTag, OCRText
from rules.text_heavy_result import CLASSIFIER_NAME, NOT_TEXT_HEAVY, TEXT_HEAVY
from Storage.models import ImageClassification, OCR_TEXT_EMBEDDING_DIM, OCRLemma, OCRTextEmbedding


@pytest.mark.asyncio(loop_scope="session")
async def test_check_database_returns_true_when_reachable(db_session):
    repo = DiagnosticsRepository(db_session)
    assert await repo.check_database() is True


@pytest.mark.asyncio(loop_scope="session")
async def test_get_statistics_counts_seeded_rows(db_session):
    tagged = Image(filename=f"{uuid.uuid4()}.jpg")
    untagged = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([tagged, untagged])
    await db_session.flush()

    before = await DiagnosticsRepository(db_session).get_statistics()

    db_session.add_all([
        Embedding(image_id=tagged.id, embedding=[0.0] * 512),
        OCRText(image_id=tagged.id, text="hello", confidence=0.9),
        ImageTag(image_id=tagged.id, key="animal", value="cat", source="rules"),
        ImageExtras(image_id=tagged.id, flagged=True),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()

    assert after.total_memes == before.total_memes
    assert after.with_embeddings == before.with_embeddings + 1
    assert after.with_ocr == before.with_ocr + 1
    assert after.with_tags == before.with_tags + 1
    assert after.without_tags == before.without_tags - 1  # tagged image no longer counted
    assert after.flagged == before.flagged + 1
    assert after.ocr_texts == before.ocr_texts + 1
    assert after.tags == before.tags + 1


@pytest.mark.asyncio(loop_scope="session")
async def test_get_statistics_counts_pending_and_rejected_separately_from_total(db_session):
    before = await DiagnosticsRepository(db_session).get_statistics()

    db_session.add_all([
        Image(filename=f"{uuid.uuid4()}.jpg", status="pending"),
        Image(filename=f"{uuid.uuid4()}.jpg", status="rejected"),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()

    assert after.pending == before.pending + 1
    assert after.rejected == before.rejected + 1
    assert after.total_memes == before.total_memes  # neither counts as "active"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_statistics_counts_ocr_missing_text_heavy_classification(db_session):
    before = await DiagnosticsRepository(db_session).get_statistics()

    classified = await _new_image(db_session)
    unclassified = await _new_image(db_session)
    db_session.add_all([
        OCRText(image_id=classified.id, text="hello", confidence=0.9),
        ImageClassification(image_id=classified.id, classifier=CLASSIFIER_NAME, result=NOT_TEXT_HEAVY, details={}),
        OCRText(image_id=unclassified.id, text="hello", confidence=0.9),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()

    # classified image (despite having OCR text) must not count; only OCR-without-classification does
    assert after.ocr_missing_text_heavy_classification == before.ocr_missing_text_heavy_classification + 1


@pytest.mark.asyncio(loop_scope="session")
async def test_get_statistics_counts_text_heavy_missing_embeddings(db_session):
    before = await DiagnosticsRepository(db_session).get_statistics()

    text_heavy_no_embedding = await _new_image(db_session)
    text_heavy_with_embedding = await _new_image(db_session)
    db_session.add_all([
        ImageClassification(image_id=text_heavy_no_embedding.id, classifier=CLASSIFIER_NAME, result=TEXT_HEAVY, details={}),
        ImageClassification(image_id=text_heavy_with_embedding.id, classifier=CLASSIFIER_NAME, result=TEXT_HEAVY, details={}),
        OCRTextEmbedding(image_id=text_heavy_with_embedding.id, embedding=[0.0] * OCR_TEXT_EMBEDDING_DIM),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()
    assert after.text_heavy_missing_embeddings == before.text_heavy_missing_embeddings + 1


@pytest.mark.asyncio(loop_scope="session")
async def test_get_statistics_counts_embeddings_missing_lemmas(db_session):
    before = await DiagnosticsRepository(db_session).get_statistics()

    embedded_no_lemmas = await _new_image(db_session)
    embedded_with_lemmas = await _new_image(db_session)
    db_session.add_all([
        OCRTextEmbedding(image_id=embedded_no_lemmas.id, embedding=[0.0] * OCR_TEXT_EMBEDDING_DIM),
        OCRTextEmbedding(image_id=embedded_with_lemmas.id, embedding=[0.0] * OCR_TEXT_EMBEDDING_DIM),
        OCRLemma(image_id=embedded_with_lemmas.id, lemma="hello"),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()
    assert after.embeddings_missing_lemmas == before.embeddings_missing_lemmas + 1


@pytest.mark.asyncio(loop_scope="session")
async def test_get_statistics_coverage_gap_counts_exclude_pending_images(db_session):
    before = await DiagnosticsRepository(db_session).get_statistics()

    pending = Image(filename=f"{uuid.uuid4()}.jpg", status="pending")
    db_session.add(pending)
    await db_session.flush()
    db_session.add_all([
        OCRText(image_id=pending.id, text="hello", confidence=0.9),
        OCRTextEmbedding(image_id=pending.id, embedding=[0.0] * OCR_TEXT_EMBEDDING_DIM),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()
    # a pending image with OCR-but-no-classification and embeddings-but-no-lemmas must not
    # inflate either count -- scope is status == "active" only
    assert after.ocr_missing_text_heavy_classification == before.ocr_missing_text_heavy_classification
    assert after.embeddings_missing_lemmas == before.embeddings_missing_lemmas


@pytest.mark.asyncio(loop_scope="session")
async def test_get_statistics_fully_covered_image_appears_in_no_gap_count(db_session):
    before = await DiagnosticsRepository(db_session).get_statistics()

    image = await _new_image(db_session)
    db_session.add_all([
        OCRText(image_id=image.id, text="hello", confidence=0.9),
        ImageClassification(image_id=image.id, classifier=CLASSIFIER_NAME, result=TEXT_HEAVY, details={}),
        OCRTextEmbedding(image_id=image.id, embedding=[0.0] * OCR_TEXT_EMBEDDING_DIM),
        OCRLemma(image_id=image.id, lemma="hello"),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()
    assert after.ocr_missing_text_heavy_classification == before.ocr_missing_text_heavy_classification
    assert after.text_heavy_missing_embeddings == before.text_heavy_missing_embeddings
    assert after.embeddings_missing_lemmas == before.embeddings_missing_lemmas


async def _new_image(session) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image
