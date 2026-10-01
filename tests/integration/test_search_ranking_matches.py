"""
Integration tests for repository.ocr_lemmas.scored_image_matches: per-token best hit (source x tier),
summed across tokens. Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.ocr_lemmas import matching_image_ids, scored_image_matches
from repository.search_ranking import get_weights
from Storage.models import (
    DescriptionLemma, DescriptionNoteLemma, Image, ImageDescription, ImageDescriptionFeedback,
    ImageTag, OCRLemma,
)

W = get_weights()


async def _image(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    return image


async def _description_lemma(db_session, image, lemma, rejected=False):
    description = ImageDescription(image_id=image.id, prompt_key=str(uuid.uuid4()), model_used="m", text="t")
    db_session.add(description)
    await db_session.flush()
    db_session.add(DescriptionLemma(image_description_id=description.id, lemma=lemma))
    if rejected:
        db_session.add(ImageDescriptionFeedback(image_description_id=description.id, approved=False))
    await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_each_source_scores_its_own_weight_at_exact_tier(db_session):
    note_img, ocr_img, tag_img, desc_img = [await _image(db_session) for _ in range(4)]
    db_session.add_all([
        DescriptionNoteLemma(image_id=note_img.id, lemma="zebracorn"),
        OCRLemma(image_id=ocr_img.id, lemma="zebracorn"),
        ImageTag(image_id=tag_img.id, key="k", value="zebracorn", source="OCR"),
    ])
    await _description_lemma(db_session, desc_img, "zebracorn")
    await db_session.flush()

    scores = await scored_image_matches(db_session, "zebracorn")

    assert scores[note_img.id] == W.hit("note", "exact")
    assert scores[ocr_img.id] == W.hit("ocr", "exact")
    assert scores[tag_img.id] == W.hit("tag", "exact")
    assert scores[desc_img.id] == W.hit("description", "exact")
    assert scores[note_img.id] > scores[ocr_img.id] > scores[tag_img.id] > scores[desc_img.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_token_scores_its_best_hit_not_the_sum_of_sources(db_session):
    both = await _image(db_session)
    db_session.add_all([
        OCRLemma(image_id=both.id, lemma="zebracorn"),
        DescriptionNoteLemma(image_id=both.id, lemma="zebracorn"),
    ])
    await db_session.flush()

    scores = await scored_image_matches(db_session, "zebracorn")

    assert scores[both.id] == W.hit("note", "exact")      # best of the two, not note + ocr


@pytest.mark.asyncio(loop_scope="session")
async def test_multiple_tokens_sum_and_all_tokens_are_required(db_session):
    both = await _image(db_session)
    one = await _image(db_session)
    db_session.add_all([
        OCRLemma(image_id=both.id, lemma="zebracorn"),
        DescriptionNoteLemma(image_id=both.id, lemma="quokkaberry"),
        OCRLemma(image_id=one.id, lemma="zebracorn"),
    ])
    await db_session.flush()

    scores = await scored_image_matches(db_session, "zebracorn quokkaberry")

    assert set(scores) == {both.id}
    assert scores[both.id] == round(W.hit("ocr", "exact") + W.hit("note", "exact"), 6)


@pytest.mark.asyncio(loop_scope="session")
async def test_exact_outranks_fuzzy_for_the_same_source(db_session):
    # The fallback tiers only run when NO source has an exact hit for the token, so exact and fuzzy are
    # exercised with two different query words.
    exact_img = await _image(db_session)
    fuzzy_img = await _image(db_session)
    db_session.add_all([
        OCRLemma(image_id=exact_img.id, lemma="hedgehogz"),
        OCRLemma(image_id=fuzzy_img.id, lemma="pineapples"),
    ])
    await db_session.flush()

    exact_scores = await scored_image_matches(db_session, "hedgehogz")
    fuzzy_scores = await scored_image_matches(db_session, "pineappel")   # no exact "pineappel" anywhere

    assert exact_scores[exact_img.id] == W.hit("ocr", "exact")
    assert fuzzy_scores[fuzzy_img.id] == W.hit("ocr", "fuzzy")
    assert W.hit("ocr", "fuzzy") < W.hit("ocr", "exact")


@pytest.mark.asyncio(loop_scope="session")
async def test_english_stem_fallback_scores_stem_tier_for_ocr(db_session):
    ocr_img = await _image(db_session)
    db_session.add(OCRLemma(image_id=ocr_img.id, lemma="gadget"))
    await db_session.flush()

    scores = await scored_image_matches(db_session, "gadgets")

    assert scores[ocr_img.id] == W.hit("ocr", "stem")


@pytest.mark.asyncio(loop_scope="session")
async def test_description_stem_form_counts_as_an_exact_hit(db_session):
    # Description lemmas are stems, and the exact tier already matches the query's stem form against them
    # (see _exact_hits), so a description match on an inflected query word scores at the exact tier.
    desc_img = await _image(db_session)
    await _description_lemma(db_session, desc_img, "widget")

    scores = await scored_image_matches(db_session, "widgets")

    assert scores[desc_img.id] == W.hit("description", "exact")


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_description_contributes_no_score(db_session):
    rejected = await _image(db_session)
    await _description_lemma(db_session, rejected, "zebracorn", rejected=True)

    assert await scored_image_matches(db_session, "zebracorn") == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_no_filter_and_wrapper_semantics_are_preserved(db_session):
    assert await scored_image_matches(db_session, None) is None
    assert await scored_image_matches(db_session, "   ") is None
    assert await matching_image_ids(db_session, None) is None

    img = await _image(db_session)
    db_session.add(OCRLemma(image_id=img.id, lemma="zebracorn"))
    await db_session.flush()

    assert await matching_image_ids(db_session, "zebracorn") == {img.id}
    assert await scored_image_matches(db_session, "nonexistentqwxz") == {}
