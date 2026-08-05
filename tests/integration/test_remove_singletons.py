"""
Integration tests for batch/remove_singletons.py.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.remove_singletons import run
from Storage.models import Image, TmpImageClusters


@pytest.mark.asyncio(loop_scope="session")
async def test_removes_singleton_cluster(db_session):
    image = Image(filename=f"singleton-{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(TmpImageClusters(cluster_id=1, image_id=image.id))
    await db_session.flush()

    metrics = await run(db_session)

    remaining = (await db_session.execute(
        select(TmpImageClusters).where(TmpImageClusters.cluster_id == 1)
    )).scalars().all()
    assert remaining == []
    assert metrics.counters_dict() == {"removed": 1}


@pytest.mark.asyncio(loop_scope="session")
async def test_leaves_multi_member_cluster_untouched(db_session):
    image_a = Image(filename=f"a-{uuid.uuid4()}.jpg")
    image_b = Image(filename=f"b-{uuid.uuid4()}.jpg")
    db_session.add_all([image_a, image_b])
    await db_session.flush()
    db_session.add_all([
        TmpImageClusters(cluster_id=2, image_id=image_a.id),
        TmpImageClusters(cluster_id=2, image_id=image_b.id),
    ])
    await db_session.flush()

    metrics = await run(db_session)

    remaining = (await db_session.execute(
        select(TmpImageClusters).where(TmpImageClusters.cluster_id == 2)
    )).scalars().all()
    assert len(remaining) == 2
    assert metrics.counters_dict() == {"removed": 0}


@pytest.mark.asyncio(loop_scope="session")
async def test_mixed_clusters_only_singleton_removed(db_session):
    image_a = Image(filename=f"a-{uuid.uuid4()}.jpg")
    image_b = Image(filename=f"b-{uuid.uuid4()}.jpg")
    image_c = Image(filename=f"c-{uuid.uuid4()}.jpg")
    db_session.add_all([image_a, image_b, image_c])
    await db_session.flush()
    db_session.add_all([
        TmpImageClusters(cluster_id=3, image_id=image_a.id),  # singleton
        TmpImageClusters(cluster_id=4, image_id=image_b.id),  # pair
        TmpImageClusters(cluster_id=4, image_id=image_c.id),  # pair
    ])
    await db_session.flush()

    metrics = await run(db_session)

    remaining_ids = {
        row.cluster_id for row in (await db_session.execute(select(TmpImageClusters))).scalars().all()
    }
    assert remaining_ids == {4}
    assert metrics.counters_dict() == {"removed": 1}
