"""
Integration tests for batch/clusterize.py -- requires a live PostgreSQL instance.
Same DB-fixture pattern as tests/integration/test_rebuild_duplicates.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.clusterize import cluster_active_library
from Storage.models import DuplicateDecision, Embedding, Image, TmpDuplicates, TmpImageClusters


async def _insert_image(session, status: str = "active") -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image.id


def _normalize(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if a < b else (b, a)


async def _insert_pair(session, a: uuid.UUID, b: uuid.UUID, distance: float) -> None:
    id1, id2 = _normalize(a, b)
    session.add(TmpDuplicates(image_id1=id1, image_id2=id2, distance=distance))
    await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_decided_pair_is_excluded_from_clustering(db_session):
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.02)
    id1, id2 = _normalize(a, b)
    db_session.add(DuplicateDecision(image_id1=id1, image_id2=id2))
    await db_session.flush()

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters))).scalars().all()
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_undecided_pair_still_clusters(db_session):
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.02)

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters.image_id))).scalars().all()
    assert set(rows) == {a, b}


@pytest.mark.asyncio(loop_scope="session")
async def test_decision_only_excludes_the_decided_pair_not_the_whole_cluster(db_session):
    # Chain a-b-c: a-b decided not-duplicate, b-c still undecided. b-c should still cluster;
    # a should end up alone (dropped -- no surviving edge at all involves a).
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    c = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.02)
    await _insert_pair(db_session, b, c, 0.02)
    id1, id2 = _normalize(a, b)
    db_session.add(DuplicateDecision(image_id1=id1, image_id2=id2))
    await db_session.flush()

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters.image_id))).scalars().all()
    assert set(rows) == {b, c}
