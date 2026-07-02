"""
Integration tests for repository/concepts.py's get_all_with_embeddings().

These tests require a live PostgreSQL instance with pgvector — same setup as
tests/integration/test_rebuild_duplicates.py.
"""
import uuid

import pytest

from repository.concepts import ConceptsRepository
from Storage.models import Concept


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