"""
Integration tests for ImagesRepository.get_images_and_descriptions_needing_tags and
TagsRepository.delete_tags_for_images -- the staleness selection behind
build_tags_from_descriptions --incremental.

Timestamps are set explicitly: server_default now() is constant inside the test's outer
transaction, so relying on it would make "newer"/"older" untestable.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from repository.images import ImagesRepository
from repository.tags import TagsRepository
from Storage.models import Image, ImageDescription, ImageDescriptionFeedback, ImageTag

_T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _image(db_session, status="active"):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    db_session.add(image)
    await db_session.flush()
    return image


def _description(image, prompt_key, text, created_at):
    return ImageDescription(
        image_id=image.id, prompt_key=prompt_key, model_used="m", text=text, created_at=created_at,
    )


def _tag(image, created_at, source="Ollama"):
    return ImageTag(image_id=image.id, key="k", value="v", source=source, created_at=created_at)


async def _needing(db_session, image):
    rows = await ImagesRepository(db_session).get_images_and_descriptions_needing_tags("Ollama")
    return sorted(text for _filename, image_id, text in rows if image_id == image.id)


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_no_ollama_tags_is_selected_with_all_its_descriptions(db_session):
    image = await _image(db_session)
    db_session.add_all([
        _description(image, "a", "first", _T0),
        _description(image, "b", "second", _T0),
    ])
    await db_session.flush()

    assert await _needing(db_session, image) == ["first", "second"]


@pytest.mark.asyncio(loop_scope="session")
async def test_image_whose_tags_are_newer_than_all_descriptions_is_skipped(db_session):
    image = await _image(db_session)
    db_session.add_all([
        _description(image, "a", "first", _T0),
        _tag(image, _T0 + timedelta(hours=1)),
    ])
    await db_session.flush()

    assert await _needing(db_session, image) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_redescribed_image_is_selected_with_every_description(db_session):
    image = await _image(db_session)
    db_session.add_all([
        _description(image, "a", "old", _T0),
        _description(image, "b", "re-described", _T0 + timedelta(hours=2)),
        _tag(image, _T0 + timedelta(hours=1)),
    ])
    await db_session.flush()

    # Both descriptions come back, not just the new one: the image's tags are rewritten
    # from the whole description set, so the old description must still contribute.
    assert await _needing(db_session, image) == ["old", "re-described"]


@pytest.mark.asyncio(loop_scope="session")
async def test_other_sources_tags_do_not_count_as_ollama_tags(db_session):
    image = await _image(db_session)
    db_session.add_all([
        _description(image, "a", "first", _T0),
        _tag(image, _T0 + timedelta(hours=1), source="OCR"),
    ])
    await db_session.flush()

    assert await _needing(db_session, image) == ["first"]


@pytest.mark.asyncio(loop_scope="session")
async def test_non_active_images_are_not_selected(db_session):
    image = await _image(db_session, status="pending")
    db_session.add(_description(image, "a", "first", _T0))
    await db_session.flush()

    assert await _needing(db_session, image) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_tags_for_images_only_touches_given_images_and_source(db_session):
    target = await _image(db_session)
    bystander = await _image(db_session)
    db_session.add_all([
        _tag(target, _T0),
        _tag(target, _T0, source="OCR"),
        _tag(bystander, _T0),
    ])
    await db_session.flush()

    await TagsRepository(db_session).delete_tags_for_images("Ollama", [target.id])

    remaining = (await db_session.execute(
        select(ImageTag.image_id, ImageTag.source).where(ImageTag.image_id.in_([target.id, bystander.id]))
    )).all()
    assert sorted(remaining, key=lambda r: (str(r[0]), r[1])) == sorted(
        [(target.id, "OCR"), (bystander.id, "Ollama")], key=lambda r: (str(r[0]), r[1])
    )


async def _feedback(db_session, description, approved):
    db_session.add(ImageDescriptionFeedback(image_description_id=description.id, approved=approved))
    await db_session.flush()


async def _all_texts(db_session, image):
    rows = await ImagesRepository(db_session).get_images_and_descriptions()
    return sorted(text for _filename, image_id, text in rows if image_id == image.id)


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_description_is_not_fed_to_the_tagger(db_session):
    image = await _image(db_session)
    rejected = _description(image, "a", "rejected text", _T0)
    approved = _description(image, "b", "approved text", _T0)
    unreviewed = _description(image, "c", "unreviewed text", _T0)
    db_session.add_all([rejected, approved, unreviewed])
    await db_session.flush()
    await _feedback(db_session, rejected, False)
    await _feedback(db_session, approved, True)

    assert await _needing(db_session, image) == ["approved text", "unreviewed text"]
    assert await _all_texts(db_session, image) == ["approved text", "unreviewed text"]


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_every_description_rejected_is_never_selected(db_session):
    image = await _image(db_session)
    only = _description(image, "a", "rejected text", _T0)
    db_session.add(only)
    await db_session.flush()
    await _feedback(db_session, only, False)

    assert await _needing(db_session, image) == []
    assert await _all_texts(db_session, image) == []
