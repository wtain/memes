"""
Integration test proving /api/images and /api/recommendations agree on which
images match a given query -- the core promise of the shared
repository.ocr_lemmas.matching_image_ids implementation both now use.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.repositories.recommendations_repository import RecommendationsRepository
from Storage.models import Image, OCRLemma


@pytest.mark.asyncio(loop_scope="session")
async def test_single_lemma_query_returns_same_ids_on_both_endpoints(db_session):
    matching = Image(filename=f"{uuid.uuid4()}.jpg")
    other = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([matching, other])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=matching.id, lemma="hedgehog"),
        OCRLemma(image_id=other.id, lemma="unrelated"),
    ])
    await db_session.flush()

    image_rows, _ = await ImageRepository(db_session).search(
        q="hedgehog", tags={}, cursor_created_at=None, cursor_id=None, limit=50
    )
    rec_rows = await RecommendationsRepository(db_session).get_recommendations(
        q="hedgehog", seed=1, last_hash=None, limit=50
    )

    image_ids = {r.id for r in image_rows}
    rec_ids = {r.id for r in rec_rows}
    assert image_ids == {matching.id}
    assert rec_ids == {matching.id}
    assert image_ids == rec_ids


@pytest.mark.asyncio(loop_scope="session")
async def test_multi_word_and_query_returns_same_ids_on_both_endpoints(db_session):
    both = Image(filename=f"{uuid.uuid4()}.jpg")
    only_one = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([both, only_one])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=both.id, lemma="grumpy"),
        OCRLemma(image_id=both.id, lemma="cat"),
        OCRLemma(image_id=only_one.id, lemma="grumpy"),
    ])
    await db_session.flush()

    image_rows, _ = await ImageRepository(db_session).search(
        q="grumpy cat", tags={}, cursor_created_at=None, cursor_id=None, limit=50
    )
    rec_rows = await RecommendationsRepository(db_session).get_recommendations(
        q="grumpy cat", seed=1, last_hash=None, limit=50
    )

    image_ids = {r.id for r in image_rows}
    rec_ids = {r.id for r in rec_rows}
    assert image_ids == {both.id}
    assert rec_ids == {both.id}
    assert image_ids == rec_ids
