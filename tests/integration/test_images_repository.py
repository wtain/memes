"""
Integration tests for repository/images.py's lang_score exposure.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.images import ImagesRepository
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
