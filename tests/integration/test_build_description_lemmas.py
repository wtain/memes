"""Integration test for batch/build_description_lemmas.py's run(): descriptions are indexed as
English (stemmed), already-indexed descriptions are skipped. Requires a live PostgreSQL instance."""
import uuid

import pytest
from sqlalchemy import select

from batch.build_description_lemmas import run
from rules.normalize import make_morph
from Storage.models import DescriptionLemma, Image, ImageDescription

_MORPH = make_morph()


async def _description(db_session, text):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    description = ImageDescription(image_id=image.id, prompt_key="p", model_used="m", text=text)
    db_session.add(description)
    await db_session.flush()
    return description


async def _lemmas(db_session, description):
    return set((await db_session.execute(
        select(DescriptionLemma.lemma).where(DescriptionLemma.image_description_id == description.id)
    )).scalars().all())


@pytest.mark.asyncio(loop_scope="session")
async def test_run_indexes_english_description_with_stemming(db_session):
    description = await _description(db_session, "Two cats sitting on sofas")

    await run(db_session, morph=_MORPH, min_word_length=3)

    lemmas = await _lemmas(db_session, description)
    assert "cat" in lemmas      # "cats" stemmed, so a query for "cat" matches exactly
    assert "sofa" in lemmas
    assert "cats" not in lemmas


@pytest.mark.asyncio(loop_scope="session")
async def test_run_skips_descriptions_that_already_have_lemmas(db_session):
    description = await _description(db_session, "a dog")
    db_session.add(DescriptionLemma(image_description_id=description.id, lemma="sentinel"))
    await db_session.flush()

    await run(db_session, morph=_MORPH, min_word_length=3)

    assert await _lemmas(db_session, description) == {"sentinel"}
