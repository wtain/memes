"""
Integration tests for repository/description_note_embeddings.py.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from repository.description_note_embeddings import DescriptionNoteEmbeddingsRepository
from Storage.models import DescriptionNote, DescriptionNoteEmbedding, Image

_DIM = 1024


async def _insert_note(session, text: str) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    session.add(DescriptionNote(image_id=image.id, text=text))
    await session.flush()
    return image.id


async def _updated_at(session, image_id):
    note = (await session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image_id)
    )).scalar_one()
    return note.updated_at


@pytest.mark.asyncio(loop_scope="session")
async def test_new_note_is_returned_as_needing_embedding(db_session):
    image_id = await _insert_note(db_session, "a cat wearing a hat")

    rows = await DescriptionNoteEmbeddingsRepository(db_session).get_notes_needing_embedding()

    assert (image_id, "a cat wearing a hat") in {(r[0], r[1]) for r in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_save_creates_embedding_and_marks_built(db_session):
    image_id = await _insert_note(db_session, "a dog in sunglasses")
    repo = DescriptionNoteEmbeddingsRepository(db_session)

    await repo.save(image_id, [0.0] * _DIM, await _updated_at(db_session, image_id))
    await db_session.flush()

    embedding_row = (await db_session.execute(
        select(DescriptionNoteEmbedding).where(DescriptionNoteEmbedding.description_note_id == image_id)
    )).scalar_one()
    assert embedding_row.embedding is not None

    note = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image_id)
    )).scalar_one()
    assert note.embedding_built_at is not None

    rows = await repo.get_notes_needing_embedding()
    assert image_id not in {row[0] for row in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_save_twice_overwrites_existing_embedding(db_session):
    image_id = await _insert_note(db_session, "overwrite me")
    repo = DescriptionNoteEmbeddingsRepository(db_session)
    await repo.save(image_id, [0.0] * _DIM, await _updated_at(db_session, image_id))
    await db_session.flush()

    second_vector = [1.0] * _DIM
    await repo.save(image_id, second_vector, await _updated_at(db_session, image_id))
    await db_session.flush()

    rows = (await db_session.execute(select(DescriptionNoteEmbedding))).scalars().all()
    assert len(rows) == 1  # upsert, not a second row


@pytest.mark.asyncio(loop_scope="session")
async def test_edited_note_becomes_stale_again_after_being_embedded(db_session):
    image_id = await _insert_note(db_session, "original text")
    repo = DescriptionNoteEmbeddingsRepository(db_session)
    await repo.save(image_id, [0.0] * _DIM, await _updated_at(db_session, image_id))
    await db_session.flush()
    assert image_id not in {row[0] for row in await repo.get_notes_needing_embedding()}

    note = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image_id)
    )).scalar_one()
    note.text = "edited text"
    note.updated_at = datetime.now() + timedelta(seconds=5)
    await db_session.flush()

    rows = await repo.get_notes_needing_embedding()
    assert image_id in {row[0] for row in rows}
