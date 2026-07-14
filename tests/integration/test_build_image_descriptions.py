"""
Integration tests for batch/build_image_descriptions.py's fill-missing-pairs logic.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest

from ai.image_description_prompts import PromptConfig
from batch.build_image_descriptions import _images_missing_prompts
from repository.image_descriptions import ImageDescriptionsRepository
from repository.images import ImagesRepository
from Storage.models import Image


async def _insert_image(session) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_images_missing_prompts_returns_only_uncovered_pairs(db_session):
    image_a = await _insert_image(db_session)
    image_b = await _insert_image(db_session)

    descriptions_repo = ImageDescriptionsRepository(db_session)
    descriptions_repo.save(image_a.id, "general_description", "llava", "existing text")
    await db_session.flush()

    prompts = [
        PromptConfig(key="general_description", prompt="What is shown?"),
        PromptConfig(key="humor_explanation", prompt="Explain the joke."),
    ]
    images_repo = ImagesRepository(db_session)

    work = await _images_missing_prompts(images_repo, descriptions_repo, prompts)
    work_by_image = {image_id: missing for _, image_id, missing in work}

    assert {p.key for p in work_by_image[image_a.id]} == {"humor_explanation"}
    assert {p.key for p in work_by_image[image_b.id]} == {"general_description", "humor_explanation"}
    total_missing_pairs = sum(len(missing) for _, _, missing in work)
    assert total_missing_pairs == 3
