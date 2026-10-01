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


# --- Real-indexer tests: description lemmas are Snowball stems ("funny" -> "funni") -------------

from batch.build_description_lemmas import run as build_description_lemmas_run  # noqa: E402
from rules.normalize import make_morph  # noqa: E402

_MORPH = make_morph()


async def _indexed_image(db_session, texts, feedbacks=None):
    """Image whose descriptions (one per text) are indexed by the real build_description_lemmas."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    for i, text in enumerate(texts):
        description = ImageDescription(image_id=image.id, prompt_key=f"p{i}", model_used="m", text=text)
        db_session.add(description)
        await db_session.flush()
        if feedbacks and feedbacks[i] is not None:
            db_session.add(ImageDescriptionFeedback(
                image_description_id=description.id, approved=feedbacks[i],
            ))
    await db_session.flush()
    await build_description_lemmas_run(db_session, morph=_MORPH, min_word_length=3)
    return image


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("word", ["funny", "dancing"])
async def test_really_indexed_description_found_despite_competing_exact_hit(db_session, word):
    described = await _indexed_image(db_session, [f"A {word} cat on a sofa"])
    competitor = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(competitor)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=competitor.id, lemma=word))
    await db_session.flush()

    assert await matching_image_ids(db_session, word) == {described.id, competitor.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_one_rejected_description_does_not_hide_a_non_rejected_one(db_session):
    image = await _indexed_image(
        db_session, ["A funny cat", "A funny dog"], feedbacks=[False, None],
    )

    assert await matching_image_ids(db_session, "funny") == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_only_matching_description_rejected_means_no_match(db_session):
    await _indexed_image(
        db_session, ["A funny cat", "A serious dog"], feedbacks=[False, None],
    )

    assert await matching_image_ids(db_session, "funny") == set()
