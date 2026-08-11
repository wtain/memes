"""
Integration tests for repository/images.py's lang_score exposure.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import ImagesRepository, OCR_LEMMAS_PIPELINE
from repository.ocr_text import OCRTextRepository
from Storage.models import Image

_BBOX = [[0, 0], [10, 0], [10, 10], [0, 10]]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_includes_lang_score(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(
        image, [(_BBOX, "when your friends finally get the joke", 0.9)], "en"
    )
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    rows = await images_repo.get_images_and_ocr_texts()
    matches = [
        (filename, img_id, txt, confidence, lang_score)
        for filename, img_id, txt, confidence, lang_score in rows
        if img_id == image.id
    ]

    assert len(matches) == 1
    assert matches[0][4] == pytest.approx(1.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_without_tags_includes_lang_score(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(image, [(_BBOX, "ctapt 3gect xdbl qwzk", 0.7)], "en")
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    rows = await images_repo.get_images_and_ocr_texts_without_tags("OCR")
    matches = [
        (filename, img_id, txt, confidence, lang_score)
        for filename, img_id, txt, confidence, lang_score in rows
        if img_id == image.id
    ]

    assert len(matches) == 1
    assert matches[0][4] == pytest.approx(0.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_with_language_includes_language(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(
        image, [(_BBOX, "when your friends finally get the joke", 0.9)], "en"
    )
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    rows = await images_repo.get_images_and_ocr_texts_with_language()
    matches = [
        (filename, img_id, txt, confidence, language, lang_score)
        for filename, img_id, txt, confidence, language, lang_score in rows
        if img_id == image.id
    ]

    assert len(matches) == 1
    assert matches[0][4] == "en"
    assert matches[0][5] == pytest.approx(1.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_without_tags_with_language_includes_language(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(image, [(_BBOX, "ctapt 3gect xdbl qwzk", 0.7)], "en")
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    rows = await images_repo.get_images_and_ocr_texts_without_tags_with_language("OCR")
    matches = [
        (filename, img_id, txt, confidence, language, lang_score)
        for filename, img_id, txt, confidence, language, lang_score in rows
        if img_id == image.id
    ]

    assert len(matches) == 1
    assert matches[0][4] == "en"
    assert matches[0][5] == pytest.approx(0.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_without_lemmas_excludes_indexed_images(db_session):
    indexed = Image(filename=f"{uuid.uuid4()}.jpg")
    not_indexed = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([indexed, not_indexed])
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(indexed, [(_BBOX, "already indexed text", 0.9)], "en")
    await ocr_repo.overwrite_texts(not_indexed, [(_BBOX, "not indexed yet", 0.9)], "en")
    await db_session.flush()

    status_repo = ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE)
    await status_repo.mark_done_by_id(indexed.id)
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    rows = await images_repo.get_images_and_ocr_texts_without_lemmas_with_language()
    matched_ids = {img_id for _filename, img_id, _text, _confidence, _language, _lang_score in rows}

    assert not_indexed.id in matched_ids
    assert indexed.id not in matched_ids


@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_without_lemmas_excludes_lemma_less_but_done_images(db_session):
    """Regression test: an image whose OCR text yields zero lemmas must still
    converge once marked done -- it must not be reprocessed forever just
    because it has no ocr_lemmas rows."""
    done_but_lemma_less = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(done_but_lemma_less)
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(done_but_lemma_less, [(_BBOX, "xy", 0.9)], "en")
    await db_session.flush()

    status_repo = ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE)
    await status_repo.mark_done_by_id(done_but_lemma_less.id)
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    rows = await images_repo.get_images_and_ocr_texts_without_lemmas_with_language()
    matched_ids = {img_id for _filename, img_id, _text, _confidence, _language, _lang_score in rows}

    assert done_but_lemma_less.id not in matched_ids


@pytest.mark.asyncio(loop_scope="session")
async def test_update_filename_and_hash_updates_filename_only_when_hash_omitted(db_session):
    image = Image(filename="a.jpg", content_hash="original-hash")
    db_session.add(image)
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    await images_repo.update_filename_and_hash(image.id, "a.png")
    await db_session.flush()

    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.png"
    assert refreshed.content_hash == "original-hash"  # untouched


@pytest.mark.asyncio(loop_scope="session")
async def test_update_filename_and_hash_updates_both_when_hash_given(db_session):
    image = Image(filename="a.webp", content_hash="original-hash")
    db_session.add(image)
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    await images_repo.update_filename_and_hash(image.id, "a.jpg", content_hash="new-hash")
    await db_session.flush()

    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.jpg"
    assert refreshed.content_hash == "new-hash"
