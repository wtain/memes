"""
Integration tests for repository.ocr_lemmas.lexical_or_scores. Requires a live PostgreSQL instance with pgvector --
see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.ocr_lemmas import lexical_or_scores
from Storage.models import Image, OCRLemma


async def _image(session, *lemmas) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    for lemma in lemmas:
        session.add(OCRLemma(image_id=image.id, lemma=lemma))
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_any_token_scores_are_summed_and_more_matches_rank_higher(db_session):
    best = await _image(db_session, "boyfriend", "pizza")
    partial = await _image(db_session, "pizza")
    await _image(db_session, "weather")

    scores = await lexical_or_scores(db_session, "boyfriend pizza tonight")

    assert scores[best.id] > scores[partial.id] > 0
    assert len([i for i in scores if i in (best.id, partial.id)]) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_the_restriction_is_applied(db_session):
    inside = await _image(db_session, "pizza")
    outside = await _image(db_session, "pizza")

    scores = await lexical_or_scores(db_session, "pizza", {inside.id})

    assert set(scores) == {inside.id} and outside.id not in scores


@pytest.mark.asyncio(loop_scope="session")
async def test_no_text_or_no_match_gives_an_empty_dict(db_session):
    assert await lexical_or_scores(db_session, "") == {}
    assert await lexical_or_scores(db_session, None) == {}
    assert await lexical_or_scores(db_session, "zzzqqqxx") == {}
