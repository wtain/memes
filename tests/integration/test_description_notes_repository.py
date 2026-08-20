"""
Integration tests for ImageRepository's description-note CRUD methods.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from Backend.app.repositories.image_repository import ImageRepository
from Storage.models import DescriptionNote, DescriptionNoteEmbedding, DescriptionNoteLemma, Image


@pytest.mark.asyncio(loop_scope="session")
async def test_get_description_note_returns_none_when_unset(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    assert await repo.get_description_note(str(image.id)) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_set_then_get_description_note(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "a cat wearing a hat")
    await db_session.flush()

    assert await repo.get_description_note(str(image.id)) == "a cat wearing a hat"


@pytest.mark.asyncio(loop_scope="session")
async def test_set_twice_overwrites_text_and_bumps_updated_at(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "first version")
    await db_session.flush()
    first_updated_at = (await db_session.execute(
        select(DescriptionNote.updated_at).where(DescriptionNote.image_id == image.id)
    )).scalar_one()

    await repo.set_description_note(str(image.id), "second version")
    await db_session.flush()

    row = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image.id)
    )).scalar_one()
    assert row.text == "second version"
    assert row.updated_at >= first_updated_at


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_description_note_deletes_row_and_cascades(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "will be cleared")
    await db_session.flush()
    db_session.add(DescriptionNoteEmbedding(description_note_id=image.id, embedding=[0.0] * 1024))
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="cleared"))
    await db_session.flush()

    await repo.clear_description_note(str(image.id))
    await db_session.flush()

    assert await repo.get_description_note(str(image.id)) is None
    assert (await db_session.execute(
        select(DescriptionNoteEmbedding).where(DescriptionNoteEmbedding.description_note_id == image.id)
    )).scalar_one_or_none() is None
    assert (await db_session.execute(
        select(DescriptionNoteLemma).where(DescriptionNoteLemma.image_id == image.id)
    )).scalars().all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_description_note_on_unset_note_is_a_safe_noop(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.clear_description_note(str(image.id))  # no note ever set
    await db_session.flush()

    assert await repo.get_description_note(str(image.id)) is None
