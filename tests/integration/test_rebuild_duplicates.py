"""
Integration tests for batch/rebuild_duplicates.py.

These tests require a live PostgreSQL instance with pgvector. They are
intentionally excluded from the standard `pytest` run (which uses mocked DB)
and are collected only when running from tests/integration/ explicitly or via
the integration-tests CI workflow.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import inspect, text

from batch.rebuild_duplicates import create_tmp_duplicates
from Storage.models import Embedding, Image


async def _insert_image_with_embedding(session, embedding_values: list[float]) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    embedding = Embedding(image_id=image.id, embedding=embedding_values)
    session.add(embedding)
    await session.flush()
    return image.id


@pytest.mark.asyncio
async def test_create_tmp_duplicates_drops_and_recreates(db_session):
    """rebuild_duplicates drops tmp_duplicates and recreates it with pairwise distances."""
    dim = 512
    base = [0.0] * dim

    vec_a = base[:]
    vec_a[0] = 1.0
    vec_b = base[:]
    vec_b[1] = 1.0

    await _insert_image_with_embedding(db_session, vec_a)
    await _insert_image_with_embedding(db_session, vec_b)

    await create_tmp_duplicates(db_session)

    result = await db_session.execute(text("SELECT COUNT(*) FROM tmp_duplicates"))
    count = result.scalar_one()
    # 2 images → 2×2 = 4 pairwise rows (including self-pairs)
    assert count == 4


@pytest.mark.asyncio
async def test_create_tmp_duplicates_is_idempotent(db_session):
    """Running rebuild_duplicates twice does not raise — the DROP IF EXISTS handles it."""
    dim = 512
    vec = [0.0] * dim
    vec[0] = 1.0
    await _insert_image_with_embedding(db_session, vec)

    await create_tmp_duplicates(db_session)
    await create_tmp_duplicates(db_session)

    result = await db_session.execute(text("SELECT COUNT(*) FROM tmp_duplicates"))
    assert result.scalar_one() == 1  # 1 image → 1 self-pair


@pytest.mark.asyncio
async def test_create_tmp_duplicates_indexes_exist(db_session):
    """Expected indexes are created after rebuild."""
    dim = 512
    vec = [0.0] * dim
    await _insert_image_with_embedding(db_session, vec)

    await create_tmp_duplicates(db_session)

    result = await db_session.execute(text("""
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'tmp_duplicates'
    """))
    index_names = {row[0] for row in result.all()}
    assert "idx_tmp_duplicates_distance" in index_names
    assert "idx_tmp_duplicates_id" in index_names