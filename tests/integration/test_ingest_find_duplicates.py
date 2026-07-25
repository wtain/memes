"""
Integration tests for batch/ingest_find_duplicates.py (ingestion Tier A/B candidate
population).

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import text

from batch.ingest_find_duplicates import find_batch_duplicates
from repository.batch_runs import BatchRunRepository
from Storage.models import Embedding, Image

_DIM = 512


def _unit_vector(index: int) -> list[float]:
    vec = [0.0] * _DIM
    vec[index] = 1.0
    return vec


async def _insert_image(session, embedding_values, status: str, batch_id=None) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    session.add(Embedding(image_id=image.id, embedding=embedding_values))
    await session.flush()
    return image.id


@pytest.mark.asyncio(loop_scope="session")
async def test_finds_in_batch_match(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, match_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.match_source == "in_batch"


@pytest.mark.asyncio(loop_scope="session")
async def test_finds_cross_corpus_match(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", stage="hash_dedup")
    pending = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    active = await _insert_image(db_session, _unit_vector(0), "active")

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, match_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {pending, active}
    assert row.match_source == "cross_corpus"


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_other_batches_pending_images(db_session):
    """A pending image from a different, concurrent-or-prior batch is neither an in-batch
    sibling nor part of the active corpus -- it must not surface as a candidate."""
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", stage="hash_dedup")
    other_batch_id = await runs_repo.create_run(kind="ingestion", stage="hash_dedup")
    await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    await _insert_image(db_session, _unit_vector(0), "pending", other_batch_id)

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_respects_threshold(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", stage="hash_dedup")
    await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    await _insert_image(db_session, _unit_vector(1), "pending", batch_id)  # orthogonal

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 0
