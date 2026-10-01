"""Integration tests for repository/description_lemmas.py. Requires a live PostgreSQL instance."""
import uuid

import pytest
from sqlalchemy import select

from repository.description_lemmas import DescriptionLemmasRepository, DescriptionLemmasSaver
from Storage.models import DescriptionLemma, Image, ImageDescription


async def _description(db_session, text="a cat on a sofa", prompt_key="p"):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    description = ImageDescription(image_id=image.id, prompt_key=prompt_key, model_used="m", text=text)
    db_session.add(description)
    await db_session.flush()
    return description


@pytest.mark.asyncio(loop_scope="session")
async def test_description_without_lemmas_is_selected_and_with_lemmas_is_not(db_session):
    fresh = await _description(db_session)
    indexed = await _description(db_session)
    db_session.add(DescriptionLemma(image_description_id=indexed.id, lemma="cat"))
    await db_session.flush()

    rows = await DescriptionLemmasRepository(db_session).get_descriptions_needing_lemmas()
    ids = {row.id for row in rows}

    assert fresh.id in ids
    assert indexed.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_saver_add_lemmas_is_idempotent(db_session):
    description = await _description(db_session)

    async with DescriptionLemmasSaver(db_session) as saver:
        await saver.add_lemmas(description.id, {"cat", "sofa"})
        await saver.add_lemmas(description.id, {"cat", "sofa"})

    lemmas = set((await db_session.execute(
        select(DescriptionLemma.lemma).where(DescriptionLemma.image_description_id == description.id)
    )).scalars().all())
    assert lemmas == {"cat", "sofa"}
