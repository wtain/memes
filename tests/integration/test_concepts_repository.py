"""
Integration tests for repository/concepts.py's get_all_with_embeddings().

These tests require a live PostgreSQL instance with pgvector — same setup as
tests/integration/test_rebuild_duplicates.py.
"""
import uuid

import pytest

from repository.concepts import ConceptsRepository
from Storage.models import Concept, ConceptImage, ConceptImageSet, Embedding, Image


@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_with_embeddings_returns_concepts_with_vectors(db_session):
    dim = 512
    vec = [0.0] * dim
    vec[0] = 1.0
    concept = Concept(name=f"test-{uuid.uuid4()}", embedding=vec)
    db_session.add(concept)
    await db_session.flush()

    repo = ConceptsRepository(db_session)
    rows = await repo.get_all_with_embeddings()

    matching = [r for r in rows if r.id == concept.id]
    assert len(matching) == 1
    assert matching[0].name == concept.name
    assert list(matching[0].embedding) == pytest.approx(vec)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_with_embeddings_excludes_null_embeddings(db_session):
    concept = Concept(name=f"test-null-{uuid.uuid4()}", embedding=None)
    db_session.add(concept)
    await db_session.flush()

    repo = ConceptsRepository(db_session)
    rows = await repo.get_all_with_embeddings()

    assert concept.id not in [r.id for r in rows]


@pytest.mark.asyncio(loop_scope="session")
async def test_top_images_for_concept_only_ranks_active_images(db_session):
    vec = [0.0] * 512
    vec[0] = 1.0
    concept = Concept(name=f"test-{uuid.uuid4()}")
    db_session.add(concept)
    await db_session.flush()
    image_set = ConceptImageSet(concept_id=concept.id, name="s")
    db_session.add(image_set)
    await db_session.flush()
    db_session.add(ConceptImage(concept_image_set_id=image_set.id, filename="ref.jpg", embedding=vec))
    ids = {}
    for status in ("active", "pending", "cut_proposed"):
        image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
        db_session.add(image)
        await db_session.flush()
        db_session.add(Embedding(image_id=image.id, embedding=vec))  # identical to the reference: the nearest match
        ids[status] = image.id
    await db_session.flush()

    rows = await ConceptsRepository(db_session).top_images_for_concept(concept.id, threshold=0.5, limit=50)

    found = {r.image_id for r in rows}
    assert ids["active"] in found
    assert ids["pending"] not in found and ids["cut_proposed"] not in found
