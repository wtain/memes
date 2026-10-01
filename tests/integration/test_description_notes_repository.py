"""
Integration tests for ImageRepository's description-note CRUD methods.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from Backend.app.repositories.image_repository import ImageRepository
from Storage.models import DescriptionNote, DescriptionNoteEmbedding, DescriptionNoteLemma, Image, ImageTag


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
    db_session.add(DescriptionNoteLemma(image_id=image.id, lemma="zzmanual"))
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


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_description_note_deletes_note_tags_but_not_other_sources(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add_all([
        ImageTag(image_id=image.id, key="k", value="v", source="Note"),
        ImageTag(image_id=image.id, key="k", value="v", source="OCR"),
    ])
    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "a cat")
    await db_session.flush()

    await repo.clear_description_note(str(image.id))
    await db_session.flush()

    sources = (await db_session.execute(
        select(ImageTag.source).where(ImageTag.image_id == image.id)
    )).scalars().all()
    assert sources == ["OCR"]


@pytest.mark.asyncio(loop_scope="session")
async def test_set_description_note_writes_lemmas_and_marks_them_built(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "a pineapple wearing a hat")
    await db_session.flush()

    lemmas = set((await db_session.execute(
        select(DescriptionNoteLemma.lemma).where(DescriptionNoteLemma.image_id == image.id)
    )).scalars().all())
    note = (await db_session.execute(
        select(DescriptionNote).where(DescriptionNote.image_id == image.id)
    )).scalar_one()
    assert {"pineapple", "hat"} <= lemmas
    assert note.lemmas_built_at == note.updated_at   # batch staleness predicate will skip it


@pytest.mark.asyncio(loop_scope="session")
async def test_set_description_note_replaces_lemmas_when_edited(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_description_note(str(image.id), "pineapple")
    await db_session.flush()
    await repo.set_description_note(str(image.id), "zebra")
    await db_session.flush()

    lemmas = set((await db_session.execute(
        select(DescriptionNoteLemma.lemma).where(DescriptionNoteLemma.image_id == image.id)
    )).scalars().all())
    assert lemmas == {"zebra"}