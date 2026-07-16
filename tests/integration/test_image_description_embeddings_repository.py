"""
Integration tests for repository/image_description_embeddings.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.image_description_embeddings import ImageDescriptionEmbeddingsRepository
from Storage.models import Image, ImageDescription, ImageDescriptionEmbedding

_DIM = 1024


def _text_unit_vector(index: int) -> list[float]:
    vec = [0.0] * _DIM
    vec[index] = 1.0
    return vec


async def _insert_description(session, text: str = "a description") -> ImageDescription:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    description = ImageDescription(
        image_id=image.id, prompt_key="general_description", model_used="llava", text=text,
    )
    session.add(description)
    await session.flush()
    return description


@pytest.mark.asyncio(loop_scope="session")
async def test_get_descriptions_without_embedding_excludes_already_embedded(db_session):
    embedded = await _insert_description(db_session, text="already embedded")
    unembedded = await _insert_description(db_session, text="needs embedding")

    repo = ImageDescriptionEmbeddingsRepository(db_session)
    repo.save(embedded.id, _text_unit_vector(0))
    await db_session.flush()

    rows = await repo.get_descriptions_without_embedding()
    ids = {row.id for row in rows}

    assert embedded.id not in ids
    assert unembedded.id in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_save_persists_the_embedding(db_session):
    description = await _insert_description(db_session)
    repo = ImageDescriptionEmbeddingsRepository(db_session)

    vec = _text_unit_vector(5)
    repo.save(description.id, vec)
    await db_session.flush()

    result = await db_session.execute(
        select(ImageDescriptionEmbedding.embedding)
        .where(ImageDescriptionEmbedding.image_description_id == description.id)
    )
    stored = result.scalar_one()
    assert list(stored) == pytest.approx(vec)


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_all_removes_every_row(db_session):
    description_a = await _insert_description(db_session)
    description_b = await _insert_description(db_session)
    repo = ImageDescriptionEmbeddingsRepository(db_session)
    repo.save(description_a.id, _text_unit_vector(0))
    repo.save(description_b.id, _text_unit_vector(1))
    await db_session.flush()

    await repo.delete_all()
    await db_session.flush()

    rows = await repo.get_descriptions_without_embedding()
    ids = {row.id for row in rows}
    assert description_a.id in ids
    assert description_b.id in ids
