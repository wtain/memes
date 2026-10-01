"""
Integration tests for ImagesRepository.get_notes_needing_tags -- the staleness selection behind
build_tags_from_notes --incremental. Timestamps are set explicitly: server_default now() is constant
inside the test's outer transaction. Requires a live PostgreSQL instance.
"""
import uuid
from datetime import datetime, timedelta

import pytest

from repository.images import ImagesRepository
from Storage.models import DescriptionNote, Image, ImageTag

_T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _image_with_note(db_session, note_updated_at, status="active"):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    db_session.add(image)
    await db_session.flush()
    db_session.add(DescriptionNote(image_id=image.id, text="a cat", updated_at=note_updated_at))
    await db_session.flush()
    return image


def _tag(image, created_at, source="Note"):
    return ImageTag(image_id=image.id, key="k", value="v", source=source, created_at=created_at)


async def _selected(db_session, image):
    rows = await ImagesRepository(db_session).get_notes_needing_tags("Note")
    return [text for image_id, text in rows if image_id == image.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_note_without_note_tags_is_selected(db_session):
    image = await _image_with_note(db_session, _T0)

    assert await _selected(db_session, image) == ["a cat"]


@pytest.mark.asyncio(loop_scope="session")
async def test_note_older_than_its_tags_is_skipped(db_session):
    image = await _image_with_note(db_session, _T0)
    db_session.add(_tag(image, _T0 + timedelta(hours=1)))
    await db_session.flush()

    assert await _selected(db_session, image) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_note_edited_after_its_tags_is_selected(db_session):
    image = await _image_with_note(db_session, _T0 + timedelta(hours=2))
    db_session.add(_tag(image, _T0 + timedelta(hours=1)))
    await db_session.flush()

    assert await _selected(db_session, image) == ["a cat"]


@pytest.mark.asyncio(loop_scope="session")
async def test_other_sources_tags_do_not_count(db_session):
    image = await _image_with_note(db_session, _T0)
    db_session.add(_tag(image, _T0 + timedelta(hours=1), source="Ollama"))
    await db_session.flush()

    assert await _selected(db_session, image) == ["a cat"]


@pytest.mark.asyncio(loop_scope="session")
async def test_non_active_images_are_not_selected(db_session):
    image = await _image_with_note(db_session, _T0, status="pending")

    assert await _selected(db_session, image) == []
