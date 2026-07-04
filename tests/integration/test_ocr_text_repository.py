"""
Integration tests for repository/ocr_text.py's lang_score behaviour.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.ocr_text import OCRTextRepository
from Storage.models import Image, OCRText


async def _insert_image(session) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


_BBOX = [[0, 0], [10, 0], [10, 10], [0, 10]]


@pytest.mark.asyncio(loop_scope="session")
async def test_overwrite_texts_scores_genuine_text_high(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)

    await repo.overwrite_texts(
        image,
        [(_BBOX, "when your friends finally get the joke", 0.95)],
        "en",
    )
    await db_session.flush()

    result = await db_session.execute(select(OCRText).where(OCRText.image_id == image.id))
    row = result.scalar_one()
    assert row.lang_score == pytest.approx(1.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_overwrite_texts_scores_garbled_cross_language_misread_low(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)

    await repo.overwrite_texts(
        image,
        [(_BBOX, "ctapt 3gect xdbl qwzk", 0.72)],
        "en",
    )
    await db_session.flush()

    result = await db_session.execute(select(OCRText).where(OCRText.image_id == image.id))
    row = result.scalar_one()
    assert row.lang_score == pytest.approx(0.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_overwrite_texts_leaves_short_text_unscored(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)

    await repo.overwrite_texts(image, [(_BBOX, "lol", 0.5)], "en")
    await db_session.flush()

    result = await db_session.execute(select(OCRText).where(OCRText.image_id == image.id))
    row = result.scalar_one()
    assert row.lang_score is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_texts_with_language_includes_lang_score(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)
    await repo.overwrite_texts(
        image, [(_BBOX, "when your friends finally get the joke", 0.9)], "en"
    )
    await db_session.flush()

    rows = await repo.get_all_texts_with_language()
    matches = [
        (text, confidence, language, lang_score)
        for text, confidence, language, lang_score in rows
        if text == "when your friends finally get the joke"
    ]
    assert len(matches) == 1
    assert matches[0][3] == pytest.approx(1.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_rows_for_scoring_defaults_to_unscored_only(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)
    await repo.overwrite_texts(
        image,
        [
            (_BBOX, "when your friends finally get the joke", 0.9),  # scored (1.0)
            (_BBOX, "lol", 0.5),  # unscored (None)
        ],
        "en",
    )
    await db_session.flush()

    unscored = await repo.get_rows_for_scoring(rescore_all=False)
    unscored_texts = {r.text for r in unscored if r.text in ("when your friends finally get the joke", "lol")}
    assert unscored_texts == {"lol"}

    all_rows = await repo.get_rows_for_scoring(rescore_all=True)
    all_texts = {r.text for r in all_rows if r.text in ("when your friends finally get the joke", "lol")}
    assert all_texts == {"when your friends finally get the joke", "lol"}


@pytest.mark.asyncio(loop_scope="session")
async def test_update_lang_score_writes_the_value(db_session):
    image = await _insert_image(db_session)
    repo = OCRTextRepository(db_session)
    await repo.overwrite_texts(image, [(_BBOX, "lol", 0.5)], "en")
    await db_session.flush()

    result = await db_session.execute(select(OCRText).where(OCRText.image_id == image.id))
    row = result.scalar_one()
    assert row.lang_score is None

    await repo.update_lang_score(row.id, 0.42)
    await db_session.flush()
    await db_session.refresh(row)
    assert row.lang_score == pytest.approx(0.42)
