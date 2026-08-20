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


@pytest.mark.asyncio(loop_scope="session")
async def test_bridge_node_transitively_reunites_a_decided_pair(db_session):
    """Documents a known, accepted limitation -- NOT a bug fix target.

    duplicate_decisions only excludes the *specific* decided edge from union-find, not
    "these two images may never share a cluster." If a later image arrives that's a near-
    duplicate of both sides of an already-decided pair, the pair gets transitively reunited
    into one cluster via that bridge node, silently undoing the original decision. Truly
    preventing this would require propagating a must-not-link constraint through the whole
    clustering pass (constrained clustering), which is materially more than the plain
    per-edge filter this feature ships -- see
    docs/superpowers/specs/2026-08-19-duplicate-dismissal-decisions-design.md. This test
    exists to make the behavior visible and pin it down, not to assert it's desired.
    """
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.02)
    id1, id2 = _normalize(a, b)
    db_session.add(DuplicateDecision(image_id1=id1, image_id2=id2))
    await db_session.flush()

    await cluster_active_library(db_session)
    rows = (await db_session.execute(select(TmpImageClusters))).scalars().all()
    assert rows == []  # a-b correctly stays apart, as in test_decided_pair_is_excluded_from_clustering

    # A new image c arrives, a near-duplicate of BOTH a and b (both pairs undecided).
    c = await _insert_image(db_session)
    await _insert_pair(db_session, a, c, 0.02)
    await _insert_pair(db_session, b, c, 0.02)

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters.image_id))).scalars().all()
    assert set(rows) == {a, b, c}  # a and b are back in one cluster, despite the decision
