"""
Integration tests confirming the description_notes / description_note_embeddings /
description_note_lemmas tables and their ORM models are wired correctly, including
cascade-delete behavior.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from Storage.models import DescriptionNote, DescriptionNoteEmbedding, DescriptionNoteLemma, Image

_DIM = 1024


@pytest.mark.asyncio(loop_scope="session")
async def test_description_note_round_trip(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    note = DescriptionNote(image_id=image.id, text="a cat wearing a hat")
    db_session.add(note)
    await db_session.flush()

    row = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image.id)
    )).scalar_one()
    assert row.text == "a cat wearing a hat"
    assert row.lemmas_built_at is None
    assert row.embedding_built_at is None
    assert row.updated_at is not None  # server_default=func.now() fired


@pytest.mark.asyncio(loop_scope="session")
async def test_description_note_embedding_round_trip(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    note = DescriptionNote(image_id=image.id, text="a dog in sunglasses")
    db_session.add(note)
    await db_session.flush()

    vector = [0.0] * _DIM
    vector[0] = 1.0
    db_session.add(DescriptionNoteEmbedding(description_note_id=note.image_id, embedding=vector))
    await db_session.flush()

    row = (await db_session.execute(
        select(DescriptionNoteEmbedding).where(DescriptionNoteEmbedding.description_note_id == note.image_id)
    )).scalar_one()
    assert row.embedding is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_description_note_lemma_composite_pk(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    note = DescriptionNote(image_id=image.id, text="funny cat meme")
    db_session.add(note)
    await db_session.flush()

    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="cat"))
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="meme"))
    await db_session.flush()

    rows = (await db_session.execute(
        select(DescriptionNoteLemma.lemma).where(DescriptionNoteLemma.image_id == image.id)
    )).scalars().all()
    assert set(rows) == {"cat", "meme"}


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_image_cascades_to_note_embedding_and_lemmas(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    note = DescriptionNote(image_id=image.id, text="soon to be deleted")
    db_session.add(note)
    await db_session.flush()
    vector = [0.0] * _DIM
    db_session.add(DescriptionNoteEmbedding(description_note_id=note.image_id, embedding=vector))
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="deleted"))
    await db_session.flush()

    await db_session.delete(image)
    await db_session.flush()

    assert (await db_session.execute(select(DescriptionNote))).scalars().all() == []
    assert (await db_session.execute(select(DescriptionNoteEmbedding))).scalars().all() == []
    assert (await db_session.execute(select(DescriptionNoteLemma))).scalars().all() == []
