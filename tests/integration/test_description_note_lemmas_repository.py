"""
Integration tests for repository/description_note_lemmas.py.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from repository.description_note_lemmas import DescriptionNoteLemmasRepository, DescriptionNoteLemmasSaver
from Storage.models import DescriptionNote, DescriptionNoteLemma, Image


async def _insert_note(session, text: str) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    session.add(DescriptionNote(image_id=image.id, text=text))
    await session.flush()
    return image.id


@pytest.mark.asyncio(loop_scope="session")
async def test_new_note_is_returned_as_needing_lemmas(db_session):
    image_id = await _insert_note(db_session, "a cat wearing a hat")

    rows = await DescriptionNoteLemmasRepository(db_session).get_notes_needing_lemmas()

    assert (image_id, "a cat wearing a hat") in rows


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_lemmas_built_excludes_note_from_next_query(db_session):
    image_id = await _insert_note(db_session, "a dog in sunglasses")
    repo = DescriptionNoteLemmasRepository(db_session)

    await repo.mark_lemmas_built(image_id)
    await db_session.flush()

    rows = await repo.get_notes_needing_lemmas()
    assert image_id not in {row[0] for row in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_edited_note_becomes_stale_again_after_being_built(db_session):
    """Confirms the built_at < updated_at staleness check: re-editing a note
    already indexed must make it eligible for re-indexing again."""
    image_id = await _insert_note(db_session, "original text")
    repo = DescriptionNoteLemmasRepository(db_session)
    await repo.mark_lemmas_built(image_id)
    await db_session.flush()
    assert image_id not in {row[0] for row in await repo.get_notes_needing_lemmas()}

    note = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image_id)
    )).scalar_one()
    note.text = "edited text"
    note.updated_at = datetime.now() + timedelta(seconds=5)
    await db_session.flush()

    rows = await repo.get_notes_needing_lemmas()
    assert image_id in {row[0] for row in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_saver_replaces_existing_lemmas_rather_than_merging(db_session):
    image_id = await _insert_note(db_session, "first version")
    db_session.add(DescriptionNoteLemma(image_id=image_id, lemma="stale"))
    await db_session.flush()

    async with DescriptionNoteLemmasSaver(db_session) as saver:
        await saver.replace_lemmas(image_id, {"fresh"})

    rows = (await db_session.execute(
        select(DescriptionNoteLemma.lemma).where(DescriptionNoteLemma.image_id == image_id)
    )).scalars().all()
    assert set(rows) == {"fresh"}


@pytest.mark.asyncio(loop_scope="session")
async def test_saver_with_empty_lemma_set_just_clears(db_session):
    image_id = await _insert_note(db_session, "only stopwords")
    db_session.add(DescriptionNoteLemma(image_id=image_id, lemma="old"))
    await db_session.flush()

    async with DescriptionNoteLemmasSaver(db_session) as saver:
        await saver.replace_lemmas(image_id, set())

    rows = (await db_session.execute(
        select(DescriptionNoteLemma).where(DescriptionNoteLemma.image_id == image_id)
    )).scalars().all()
    assert rows == []
