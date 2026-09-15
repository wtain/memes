"""Integration tests for batch/classify_text_heavy.py.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
Filesystem operations use pytest's tmp_path, standing in for BASE_PATH.
"""
import os

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from batch.classify_text_heavy import run
from batch.utils.text_heavy_classifier import CLASSIFIER_NAME, TEXT_HEAVY
from Storage.models import Image, ImageClassification, OCRText

CONFIDENCE_MIN = 0.4


def _screenshot_image(path, size=(200, 200)):
    """Same fixture shape as batch/tests/test_text_heavy_classifier.py's
    _fake_screenshot_bboxes -- 8 text-line bboxes, coverage ~0.68, dominant-color ~0.86,
    ink-consistency ~1.0. Kept independently here rather than imported, since integration tests
    in this codebase don't import fixtures from batch/tests/ (separate test roots, separate
    asyncio_mode -- see this repo's testing gotcha)."""
    img = PILImage.new("RGB", size, (255, 255, 255))
    pixels = img.load()
    bboxes = []
    for i in range(8):
        y0, y1 = 5 + i * 25, 25 + i * 25
        for x in range(10, 180):
            for y in range(y0 + 8, y0 + 12):
                pixels[x, y] = (0, 0, 0)
        bboxes.append([[10, y0], [180, y0], [180, y1], [10, y1]])
    img.save(path, "JPEG")
    return bboxes


@pytest.mark.asyncio(loop_scope="session")
async def test_classifies_and_persists_a_text_heavy_image(tmp_path, db_session):
    image = Image(filename="a.jpg", status="active", width=200, height=200)
    db_session.add(image)
    await db_session.flush()
    bboxes = _screenshot_image(os.path.join(str(tmp_path), "a.jpg"))
    for bbox in bboxes:
        db_session.add(OCRText(image_id=image.id, bbox=bbox, confidence=0.9, text="x"))
    await db_session.flush()

    metrics = await run(db_session, str(tmp_path), CONFIDENCE_MIN, "active")
    await db_session.commit()

    assert metrics.counters_dict() == {TEXT_HEAVY: 1}
    row = (await db_session.execute(
        select(ImageClassification).where(ImageClassification.image_id == image.id)
    )).scalar_one()
    assert row.classifier == CLASSIFIER_NAME
    assert row.result == TEXT_HEAVY
    assert isinstance(row.details["coverage_ratio"], float)
    assert row.details["coverage_ratio"] > 0.5


@pytest.mark.asyncio(loop_scope="session")
async def test_rerun_finds_no_candidates_for_already_classified_images(tmp_path, db_session):
    image = Image(filename="a.jpg", status="active", width=200, height=200)
    db_session.add(image)
    await db_session.flush()
    bboxes = _screenshot_image(os.path.join(str(tmp_path), "a.jpg"))
    for bbox in bboxes:
        db_session.add(OCRText(image_id=image.id, bbox=bbox, confidence=0.9, text="x"))
    await db_session.flush()

    first = await run(db_session, str(tmp_path), CONFIDENCE_MIN, "active")
    await db_session.commit()
    assert first.counters_dict() == {TEXT_HEAVY: 1}

    second = await run(db_session, str(tmp_path), CONFIDENCE_MIN, "active")
    await db_session.commit()
    assert second.counters_dict() == {}  # zero candidates -- already classified, not recomputed


@pytest.mark.asyncio(loop_scope="session")
async def test_image_without_dimensions_is_excluded(tmp_path, db_session):
    image = Image(filename="a.jpg", status="active")  # width/height left NULL
    db_session.add(image)
    await db_session.flush()
    bboxes = _screenshot_image(os.path.join(str(tmp_path), "a.jpg"))
    for bbox in bboxes:
        db_session.add(OCRText(image_id=image.id, bbox=bbox, confidence=0.9, text="x"))
    await db_session.flush()

    metrics = await run(db_session, str(tmp_path), CONFIDENCE_MIN, "active")

    assert metrics.counters_dict() == {}
