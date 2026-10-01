"""
Integration tests: Ollama description lemmas are a fourth source in smart search, with rejected
descriptions excluded at query time. Requires a live PostgreSQL instance.
"""
import uuid

import pytest

from repository.ocr_lemmas import matching_image_ids
from Storage.models import (
    DescriptionLemma, Image, ImageDescription, ImageDescriptionFeedback, OCRLemma,
)


async def _image_with_description_lemma(db_session, lemma, feedback=None, prompt_key="p"):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    description = ImageDescription(image_id=image.id, prompt_key=prompt_key, model_used="m", text="t")
    db_session.add(description)
    await db_session.flush()
    db_session.add(DescriptionLemma(image_description_id=description.id, lemma=lemma))
    if feedback is not None:
        db_session.add(ImageDescriptionFeedback(image_description_id=description.id, approved=feedback))
    await db_session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_only_a_description_is_found_by_exact_match(db_session):
    image = await _image_with_description_lemma(db_session, "pineapple")

    assert await matching_image_ids(db_session, "pineapple") == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_approved_description_still_matches(db_session):
    image = await _image_with_description_lemma(db_session, "pineapple", feedback=True)

    assert await matching_image_ids(db_session, "pineapple") == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_description_does_not_match(db_session):
    await _image_with_description_lemma(db_session, "pineapple", feedback=False)

    assert await matching_image_ids(db_session, "pineapple") == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_description_lemma_matches_via_trigram_fuzzy_fallback(db_session):
    image = await _image_with_description_lemma(db_session, "pineapples")

    # "pineapple" has no exact row, so the fuzzy fallback (length >= FUZZY_MIN_LEMMA_LENGTH) runs.
    assert image.id in await matching_image_ids(db_session, "pineappel")


@pytest.mark.asyncio(loop_scope="session")
async def test_description_lemma_matches_via_english_stem_fallback(db_session):
    image = await _image_with_description_lemma(db_session, "sofa")

    # "sofas" has no exact row; its English stem "sofa" does.
    assert image.id in await matching_image_ids(db_session, "sofas")


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_description_is_excluded_from_stem_and_fuzzy_fallbacks(db_session):
    rejected_stem = await _image_with_description_lemma(db_session, "sofa", feedback=False)
    rejected_fuzzy = await _image_with_description_lemma(db_session, "pineapples", feedback=False)

    stem_ids = await matching_image_ids(db_session, "sofas")
    fuzzy_ids = await matching_image_ids(db_session, "pineappel")

    assert rejected_stem.id not in stem_ids
    assert rejected_fuzzy.id not in fuzzy_ids


@pytest.mark.asyncio(loop_scope="session")
async def test_multi_token_query_matches_across_ocr_and_description_sources(db_session):
    """AND across tokens, OR across sources within a token."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    description = ImageDescription(image_id=image.id, prompt_key="p", model_used="m", text="t")
    db_session.add(description)
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=image.id, lemma="pineapple"),
        DescriptionLemma(image_description_id=description.id, lemma="sofa"),
    ])
    await db_session.flush()

    assert await matching_image_ids(db_session, "pineapple sofa") == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_description_lemma_does_not_match_a_different_word(db_session):
    await _image_with_description_lemma(db_session, "pineapple")

    assert await matching_image_ids(db_session, "zebra") == set()
