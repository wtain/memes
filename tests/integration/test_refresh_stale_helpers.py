"""batch/utils/refresh_stale.py and the two read helpers the refresh modes use."""
import argparse
import uuid

import pytest

from batch.utils.refresh_stale import refresh_stats, reject_conflicts
from repository.image_descriptions import ImageDescriptionsRepository
from repository.ocr_text import OCRTextRepository
from Storage.models import Image, ImageDescription, ImageDescriptionFeedback, OCRText


def test_refresh_stats_counts_what_is_still_stale():
    assert refresh_stats(selected=5, refreshed=3, skipped_upstream_stale=1, failed=1) == {
        "images_selected": 5, "refreshed": 3, "skipped_upstream_stale": 1, "failed": 1, "images_remaining": 2}


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    return parser


def test_reject_conflicts_names_the_flag_and_exits():
    with pytest.raises(SystemExit):
        reject_conflicts(_parser(), True, reset=True, retry_failed=False)


def test_reject_conflicts_is_silent_without_the_mode_or_without_a_conflict():
    reject_conflicts(_parser(), False, reset=True)
    reject_conflicts(_parser(), True, reset=False, retry_failed=False)


async def _image(session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_rows_for_images_returns_only_the_requested_images(db_session):
    a, b = await _image(db_session), await _image(db_session)
    db_session.add_all([OCRText(image_id=a.id, text="x", confidence=0.9, language="en", lang_score=0.8),
                        OCRText(image_id=b.id, text="y", confidence=0.9, language="en", lang_score=0.8)])
    await db_session.flush()

    rows = await OCRTextRepository(db_session).get_rows_for_images([a.id])
    assert rows == [(a.id, "x", 0.9, "en", 0.8)]
    assert await OCRTextRepository(db_session).get_rows_for_images([]) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_description_texts_for_images_leave_out_rejected_descriptions(db_session):
    image = await _image(db_session)
    kept = ImageDescription(image_id=image.id, prompt_key="p1", model_used="m", text="kept")
    rejected = ImageDescription(image_id=image.id, prompt_key="p2", model_used="m", text="rejected")
    db_session.add_all([kept, rejected])
    await db_session.flush()
    db_session.add(ImageDescriptionFeedback(image_description_id=rejected.id, approved=False))
    await db_session.flush()

    rows = await ImageDescriptionsRepository(db_session).get_texts_for_images([image.id])
    assert rows == [(image.id, "kept")]
