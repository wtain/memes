"""
Integration tests for repository/image_descriptions.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.image_descriptions import ImageDescriptionsRepository
from Storage.models import Image, ImageDescription


async def _insert_image(session) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_save_persists_prompt_key_and_model(db_session):
    image = await _insert_image(db_session)
    repo = ImageDescriptionsRepository(db_session)

    repo.save(image.id, "general_description", "qwen2.5vl:7b", "a meme about cats")
    await db_session.flush()

    result = await db_session.execute(select(ImageDescription).where(ImageDescription.image_id == image.id))
    row = result.scalar_one()
    assert row.prompt_key == "general_description"
    assert row.model_used == "qwen2.5vl:7b"
    assert row.text == "a meme about cats"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_image_ids_with_prompt_returns_only_matching_prompt_key(db_session):
    image_a = await _insert_image(db_session)
    image_b = await _insert_image(db_session)
    repo = ImageDescriptionsRepository(db_session)

    repo.save(image_a.id, "general_description", "llava", "text a")
    repo.save(image_b.id, "humor_explanation", "llava", "text b")
    await db_session.flush()

    ids = await repo.get_image_ids_with_prompt("general_description")

    assert ids == {image_a.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_save_raises_on_duplicate_image_and_prompt_key(db_session):
    image = await _insert_image(db_session)
    repo = ImageDescriptionsRepository(db_session)

    repo.save(image.id, "general_description", "llava", "first")
    await db_session.flush()

    repo.save(image.id, "general_description", "llava", "second")
    with pytest.raises(Exception):
        await db_session.flush()
