"""
Integration tests for Backend/app/repositories/recommendations_repository.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.

RecommendationsRepository.get_recommendations previously referenced a
column, ImageExtras.exclude, that didn't exist after the excluded -> flagged
rename (commit 4b9bca5 missed this file), breaking every
/api/recommendations request with an AttributeError. Router-level tests
mock the service and never execute the real query, so nothing caught it —
these tests exercise the query against a real schema.
"""
import hashlib
import uuid

import pytest

from Backend.app.repositories.recommendations_repository import RecommendationsRepository
from Storage.models import Image, ImageExtras, ImageTag, OCRLemma

_BBOX = [[0, 0], [10, 0], [10, 10], [0, 10]]


def _md5_hash(image_id, seed: int) -> str:
    return hashlib.md5(f"{image_id}{seed}".encode("utf-8")).hexdigest()


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_flagged_images(db_session):
    kept = Image(filename=f"{uuid.uuid4()}.jpg")
    flagged = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([kept, flagged])
    await db_session.flush()
    db_session.add(ImageExtras(image_id=flagged.id, flagged=True))
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q=None, seed=1, last_hash=None, limit=50)

    ids = {r.id for r in rows}
    assert kept.id in ids
    assert flagged.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_includes_image_with_no_extras_row(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q=None, seed=1, last_hash=None, limit=50)

    matches = [r for r in rows if r.id == image.id]
    assert len(matches) == 1
    assert matches[0].flagged is None


@pytest.mark.asyncio(loop_scope="session")
async def test_includes_explicitly_unflagged_image(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageExtras(image_id=image.id, flagged=False))
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q=None, seed=1, last_hash=None, limit=50)

    matches = [r for r in rows if r.id == image.id]
    assert len(matches) == 1
    assert matches[0].flagged is False


@pytest.mark.asyncio(loop_scope="session")
async def test_query_filter_matches_ocr_lemma_index(db_session):
    matching = Image(filename=f"{uuid.uuid4()}.jpg")
    other = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([matching, other])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=matching.id, lemma="cat"),
        OCRLemma(image_id=other.id, lemma="unrelated"),
    ])
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q="cat", seed=1, last_hash=None, limit=50)

    ids = {r.id for r in rows}
    assert matching.id in ids
    assert other.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_query_filter_matches_tag(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageTag(image_id=image.id, key="animal", value="cat", source="rules"))
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q="cat", seed=1, last_hash=None, limit=50)

    assert image.id in {r.id for r in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_query_filter_requires_all_words(db_session):
    both = Image(filename=f"{uuid.uuid4()}.jpg")
    only_cat = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([both, only_cat])
    await db_session.flush()
    db_session.add_all([
        ImageTag(image_id=both.id, key="a", value="cat", source="rules"),
        ImageTag(image_id=both.id, key="b", value="dog", source="rules"),
        ImageTag(image_id=only_cat.id, key="a", value="cat", source="rules"),
    ])
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q="cat dog", seed=1, last_hash=None, limit=50)

    ids = {r.id for r in rows}
    assert both.id in ids
    assert only_cat.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_no_matches_returns_empty_list(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q="nonexistentword", seed=1, last_hash=None, limit=50)

    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_last_hash_pagination_excludes_seen_and_earlier(db_session):
    seed = 777
    images = [Image(filename=f"{uuid.uuid4()}.jpg") for _ in range(4)]
    db_session.add_all(images)
    await db_session.flush()

    ordered = sorted(images, key=lambda img: _md5_hash(img.id, seed))

    repo = RecommendationsRepository(db_session)
    cutoff = _md5_hash(ordered[1].id, seed)
    rows = await repo.get_recommendations(q=None, seed=seed, last_hash=cutoff, limit=50)

    returned_ids = [r.id for r in rows if r.id in {img.id for img in images}]
    expected_ids = [img.id for img in ordered[2:]]
    assert returned_ids == expected_ids


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_returns_at_most_limit_plus_one(db_session):
    images = [Image(filename=f"{uuid.uuid4()}.jpg") for _ in range(5)]
    db_session.add_all(images)
    await db_session.flush()

    repo = RecommendationsRepository(db_session)
    rows = await repo.get_recommendations(q=None, seed=3, last_hash=None, limit=2)

    assert len(rows) <= 3
