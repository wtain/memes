"""
Integration tests confirming duplicate_decisions has no effect on the general similarity
search endpoint -- these are two deliberately independent mechanisms. duplicate_decisions
only scopes batch/clusterize.py's union-find (see tests/integration/test_clusterize.py);
ImageRepository.get_similar (backing GET /api/images/{id}/similar) queries embeddings
directly and has never referenced duplicate_decisions at all. This locks that contract in
so it can't silently regress.

Requires a live PostgreSQL instance with pgvector -- same fixtures/conventions as
tests/integration/test_rebuild_duplicates.py and tests/integration/test_clusterize.py.
"""
import uuid

import pytest

from Backend.app.repositories.image_repository import ImageRepository
from Storage.models import DescriptionNote, DescriptionNoteEmbedding, DuplicateDecision, Embedding, Image

_DIM = 512
_NOTE_DIM = 1024


def _unit_vector(index: int) -> list[float]:
    vec = [0.0] * _DIM
    vec[index] = 1.0
    return vec


def _unit_note_vector(index: int) -> list[float]:
    vec = [0.0] * _NOTE_DIM
    vec[index] = 1.0
    return vec


async def _insert_image_with_embedding(session, embedding_values: list[float], status: str = "active") -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    embedding = Embedding(image_id=image.id, embedding=embedding_values)
    session.add(embedding)
    await session.flush()
    return image.id


async def _insert_image_with_note_embedding(session, embedding_values: list[float], status: str = "active") -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    session.add(DescriptionNote(image_id=image.id, text="a note"))
    await session.flush()
    session.add(DescriptionNoteEmbedding(description_note_id=image.id, embedding=embedding_values))
    await session.flush()
    return image.id


def _normalize(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if a < b else (b, a)


@pytest.mark.asyncio(loop_scope="session")
async def test_decided_not_duplicate_pair_still_appears_in_similar_images(db_session):
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(0))  # identical -> distance 0

    id1, id2 = _normalize(a, b)
    db_session.add(DuplicateDecision(image_id1=id1, image_id2=id2))
    await db_session.flush()

    repo = ImageRepository(db_session)
    embedding = await repo.get_embedding(str(a))
    rows = await repo.get_similar(str(a), embedding, limit=10)

    similar_ids = {row[0] for row in rows}
    assert b in similar_ids  # decision has zero effect on this query


@pytest.mark.asyncio(loop_scope="session")
async def test_get_similar_by_note_finds_close_embedding(db_session):
    a = await _insert_image_with_note_embedding(db_session, _unit_note_vector(0))
    b = await _insert_image_with_note_embedding(db_session, _unit_note_vector(0))  # identical

    repo = ImageRepository(db_session)
    embedding = await repo.get_description_note_embedding(str(a))
    rows = await repo.get_similar_by_note(str(a), embedding, limit=10)

    similar_ids = {row[0] for row in rows}
    assert b in similar_ids
    assert a not in similar_ids  # excludes itself


@pytest.mark.asyncio(loop_scope="session")
async def test_get_description_note_embedding_returns_none_when_absent(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    assert await repo.get_description_note_embedding(str(image.id)) is None
