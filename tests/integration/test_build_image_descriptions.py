"""
Integration tests for batch/build_image_descriptions.py's fill-missing-pairs logic.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest

from ai.image_description_prompts import PromptConfig
from batch.build_image_descriptions import _images_missing_prompts, _status_repos
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
    image_c = await _insert_image(db_session)

    descriptions_repo = ImageDescriptionsRepository(db_session)
    descriptions_repo.save(image_a.id, "general_description", "llava", "existing text")
    descriptions_repo.save(image_c.id, "general_description", "llava", "existing text")
    descriptions_repo.save(image_c.id, "humor_explanation", "llava", "existing text")
    await db_session.flush()

    prompts = [
        PromptConfig(key="general_description", prompt="What is shown?"),
        PromptConfig(key="humor_explanation", prompt="Explain the joke."),
    ]
    images_repo = ImagesRepository(db_session)

    status_repos = _status_repos(db_session, prompts)
    work = await _images_missing_prompts(images_repo, descriptions_repo, status_repos, prompts)
    work_by_image = {image_id: missing for _, image_id, missing in work}

    assert {p.key for p in work_by_image[image_a.id]} == {"humor_explanation"}
    assert {p.key for p in work_by_image[image_b.id]} == {"general_description", "humor_explanation"}
    total_missing_pairs = sum(len(missing) for _, _, missing in work)
    assert total_missing_pairs == 3

    # image_c has every configured prompt already covered — it must be excluded
    # from the work list entirely, not merely mapped to an empty missing list.
    assert image_c.id not in work_by_image
    assert len(work) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_images_missing_prompts_excludes_failed_pairs_unless_retrying(db_session):
    image = await _insert_image(db_session)

    descriptions_repo = ImageDescriptionsRepository(db_session)
    prompts = [
        PromptConfig(key="general_description", prompt="What is shown?"),
        PromptConfig(key="humor_explanation", prompt="Explain the joke."),
    ]
    images_repo = ImagesRepository(db_session)
    status_repos = _status_repos(db_session, prompts)

    await status_repos["general_description"].record_failure(image.id, "context size exceeded")
    await db_session.flush()

    work_default = await _images_missing_prompts(images_repo, descriptions_repo, status_repos, prompts)
    work_by_image_default = {image_id: missing for _, image_id, missing in work_default}
    assert {p.key for p in work_by_image_default[image.id]} == {"humor_explanation"}

    work_retry = await _images_missing_prompts(
        images_repo, descriptions_repo, status_repos, prompts, retry_failed=True
    )
    work_by_image_retry = {image_id: missing for _, image_id, missing in work_retry}
    assert {p.key for p in work_by_image_retry[image.id]} == {"general_description", "humor_explanation"}
