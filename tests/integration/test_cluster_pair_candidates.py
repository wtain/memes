"""Integration tests for ImageRepository.get_cluster_pair_candidates (duplicates review details panel).

Requires a live PostgreSQL instance with pgvector, see tests/integration/conftest.py.
"""
import uuid

import pytest

from Backend.app.repositories.image_repository import ImageRepository
from Storage.models import Image, TmpDuplicates


def _image() -> Image:
    return Image(filename=f"{uuid.uuid4()}.jpg")


@pytest.mark.asyncio(loop_scope="session")
async def test_returns_row_with_distance_and_source(db_session):
    a, b, c = _image(), _image(), _image()
    db_session.add_all([a, b, c])
    await db_session.flush()
    low, high = sorted([a.id, b.id])
    db_session.add(TmpDuplicates(image_id1=low, image_id2=high, distance=0.08, distance_source="ocr_text"))
    await db_session.flush()

    result = await ImageRepository(db_session).get_cluster_pair_candidates([a.id, b.id, c.id])

    assert result == {(low, high): [(0.08, "ocr_text")]}


@pytest.mark.asyncio(loop_scope="session")
async def test_ignores_pairs_outside_the_member_list(db_session):
    a, b, outsider = _image(), _image(), _image()
    db_session.add_all([a, b, outsider])
    await db_session.flush()
    low, high = sorted([a.id, outsider.id])
    db_session.add(TmpDuplicates(image_id1=low, image_id2=high, distance=0.03))
    await db_session.flush()

    assert await ImageRepository(db_session).get_cluster_pair_candidates([a.id, b.id]) == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_normalises_the_key_order(db_session):
    a, b = _image(), _image()
    db_session.add_all([a, b])
    await db_session.flush()
    low, high = sorted([a.id, b.id])
    db_session.add(TmpDuplicates(image_id1=low, image_id2=high, distance=0.02))
    await db_session.flush()

    result = await ImageRepository(db_session).get_cluster_pair_candidates([high, low])

    assert list(result) == [(low, high)]


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_and_single_member(db_session):
    repo = ImageRepository(db_session)
    assert await repo.get_cluster_pair_candidates([]) == {}
    assert await repo.get_cluster_pair_candidates([uuid.uuid4()]) == {}
