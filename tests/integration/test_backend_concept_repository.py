"""
Integration tests for Backend/app/repositories/concept_repository.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.

get_image_embedding previously used scalar_one(), which raised NoResultFound
(surfacing as an unhandled 500) for any image with no embeddings row instead
of returning None like the rest of the codebase's "missing embedding"
pattern (e.g. ImageRepository.get_embedding). Router-level tests mock the
service and never execute the real query, so nothing caught it — these
tests exercise the query against a real schema.
"""
import uuid

import pytest
from sqlalchemy.exc import NoResultFound

from Backend.app.repositories.concept_repository import ConceptRepository
from Storage.models import Concept, ConceptImage, ConceptImageSet, Embedding, Image

_DIM = 512


def _unit_vector(index: int) -> list[float]:
    vec = [0.0] * _DIM
    vec[index] = 1.0
    return vec


@pytest.mark.asyncio(loop_scope="session")
async def test_get_by_id_returns_concept(db_session):
    concept = Concept(name=f"test-{uuid.uuid4()}")
    db_session.add(concept)
    await db_session.flush()

    repo = ConceptRepository(db_session)
    row = await repo.get_by_id(concept.id)

    assert row.id == concept.id
    assert row.name == concept.name


@pytest.mark.asyncio(loop_scope="session")
async def test_get_by_id_missing_raises(db_session):
    repo = ConceptRepository(db_session)
    with pytest.raises(NoResultFound):
        await repo.get_by_id(-1)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_embedding_returns_vector(db_session):
    vec = _unit_vector(0)
    concept = Concept(name=f"test-{uuid.uuid4()}", embedding=vec)
    db_session.add(concept)
    await db_session.flush()

    repo = ConceptRepository(db_session)
    embedding = await repo.get_embedding(concept.id)

    assert list(embedding) == pytest.approx(vec)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_image_embedding_returns_none_when_missing(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ConceptRepository(db_session)
    embedding = await repo.get_image_embedding(image.id)

    assert embedding is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_image_embedding_returns_vector_when_present(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    vec = _unit_vector(1)
    db_session.add(Embedding(image_id=image.id, embedding=vec))
    await db_session.flush()

    repo = ConceptRepository(db_session)
    embedding = await repo.get_image_embedding(image.id)

    assert list(embedding) == pytest.approx(vec)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_top_images_orders_by_distance(db_session):
    query_vec = _unit_vector(0)
    near_image = Image(filename=f"{uuid.uuid4()}.jpg")
    far_image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([near_image, far_image])
    await db_session.flush()
    db_session.add_all([
        Embedding(image_id=near_image.id, embedding=_unit_vector(0)),
        Embedding(image_id=far_image.id, embedding=_unit_vector(1)),
    ])
    await db_session.flush()

    repo = ConceptRepository(db_session)
    rows = await repo.get_top_images(query_vec, limit=10)

    ordered_ids = [r.image_id for r in rows]
    assert ordered_ids.index(near_image.id) < ordered_ids.index(far_image.id)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_for_image_returns_matching_concepts(db_session):
    concept = Concept(name=f"test-{uuid.uuid4()}")
    db_session.add(concept)
    await db_session.flush()
    image_set = ConceptImageSet(concept_id=concept.id, name="set")
    db_session.add(image_set)
    await db_session.flush()
    db_session.add(ConceptImage(
        concept_image_set_id=image_set.id,
        filename="ref.jpg",
        embedding=_unit_vector(0),
    ))
    await db_session.flush()

    repo = ConceptRepository(db_session)
    rows = await repo.get_for_image(_unit_vector(0), limit=10)

    assert any(r.id == concept.id for r in rows)


@pytest.mark.asyncio(loop_scope="session")
async def test_top_concepts_for_image_returns_none_when_no_embedding(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ConceptRepository(db_session)
    result = await repo.top_concepts_for_image(image.id)

    assert result is None


@pytest.mark.asyncio(loop_scope="session")
async def test_top_concepts_for_image_returns_empty_when_no_concept_within_threshold(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(Embedding(image_id=image.id, embedding=_unit_vector(0)))
    await db_session.flush()

    concept = Concept(name=f"test-{uuid.uuid4()}")
    db_session.add(concept)
    await db_session.flush()
    image_set = ConceptImageSet(concept_id=concept.id, name="set")
    db_session.add(image_set)
    await db_session.flush()
    # Orthogonal vector -> cosine distance 1.0, well outside the default 0.2 threshold.
    db_session.add(ConceptImage(
        concept_image_set_id=image_set.id,
        filename="ref.jpg",
        embedding=_unit_vector(1),
    ))
    await db_session.flush()

    repo = ConceptRepository(db_session)
    result = await repo.top_concepts_for_image(image.id)

    assert result == []


@pytest.mark.asyncio(loop_scope="session")
async def test_top_concepts_for_image_returns_matching_concept(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(Embedding(image_id=image.id, embedding=_unit_vector(0)))
    await db_session.flush()

    concept = Concept(name=f"test-{uuid.uuid4()}")
    db_session.add(concept)
    await db_session.flush()
    image_set = ConceptImageSet(concept_id=concept.id, name="set")
    db_session.add(image_set)
    await db_session.flush()
    # Identical vector -> cosine distance 0.0, well within the default 0.2 threshold.
    db_session.add(ConceptImage(
        concept_image_set_id=image_set.id,
        filename="ref.jpg",
        embedding=_unit_vector(0),
    ))
    await db_session.flush()

    repo = ConceptRepository(db_session)
    result = await repo.top_concepts_for_image(image.id)

    assert len(result) == 1
    assert result[0].name == concept.name
    assert result[0].id == concept.id
    assert result[0].avg_distance == pytest.approx(0.0, abs=1e-6)
