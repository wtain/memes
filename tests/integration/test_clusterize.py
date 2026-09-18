"""
Integration tests for batch/clusterize.py -- requires a live PostgreSQL instance.
Same DB-fixture pattern as tests/integration/test_rebuild_duplicates.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.clusterize import cluster_active_library, get_duplicate_pairs, get_images_ids
from Storage.models import DuplicateDecision, Embedding, Image, TmpDuplicates, TmpImageClusters


async def _insert_image(session, status: str = "active") -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image.id


def _normalize(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if a < b else (b, a)


async def _insert_pair(session, a: uuid.UUID, b: uuid.UUID, distance: float, distance_source: str = "clip") -> None:
    id1, id2 = _normalize(a, b)
    session.add(TmpDuplicates(image_id1=id1, image_id2=id2, distance=distance, distance_source=distance_source))
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


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_sourced_pair_clusters_under_its_own_threshold(db_session):
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.03, distance_source="ocr_text")  # < PROXIMITY_THRESHOLD_OCR_TEXT (0.05)

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters.image_id))).scalars().all()
    assert set(rows) == {a, b}


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_sourced_pair_past_its_own_threshold_does_not_cluster(db_session):
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    # 0.08 is past PROXIMITY_THRESHOLD_OCR_TEXT (0.05) but well within clip's own 0.05 too --
    # this must NOT cluster despite the distance being numerically close to what a clip-sourced
    # pair at the same value would need. Proves the two thresholds are independently enforced,
    # not OR'd loosely against a single shared cutoff.
    await _insert_pair(db_session, a, b, 0.08, distance_source="ocr_text")

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters))).scalars().all()
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_clip_and_ocr_text_thresholds_enforced_independently(db_session):
    """The core regression test for this task: two pairs at distances that would swap outcomes
    if the two distance_source thresholds were ever accidentally conflated into one shared
    comparison."""
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    c = await _insert_image(db_session)
    d = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.045, distance_source="clip")      # < 0.05 (PROXIMITY_THRESHOLD) -> clusters
    await _insert_pair(db_session, c, d, 0.045, distance_source="ocr_text")  # < 0.05 (PROXIMITY_THRESHOLD_OCR_TEXT) -> clusters

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters.image_id))).scalars().all()
    assert set(rows) == {a, b, c, d}


@pytest.mark.asyncio(loop_scope="session")
async def test_get_duplicate_pairs_enforces_each_threshold_independently(db_session):
    """Direct call to get_duplicate_pairs() with deliberately DIFFERENT clip/ocr_text thresholds --
    genuinely discriminates per-source gating from a naive OR'd single-threshold bug. The tests
    above (driven through cluster_active_library()) can't discriminate this, since
    PROXIMITY_THRESHOLD and PROXIMITY_THRESHOLD_OCR_TEXT happen to both equal 0.05 today -- a bug
    that silently dropped the per-branch distance_source guard would still pass every test above
    unnoticed, collapsing to a single distance < 0.05 check regardless of source."""
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    c = await _insert_image(db_session)
    d = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.03, distance_source="clip")      # < clip_threshold (0.05) -> included
    await _insert_pair(db_session, c, d, 0.03, distance_source="ocr_text")  # NOT < ocr_text_threshold (0.02) -> excluded

    mapping, _ = await get_images_ids(db_session)
    pairs = await get_duplicate_pairs(db_session, mapping, clip_threshold=0.05, ocr_text_threshold=0.02)

    pair_id_sets = [{p[0], p[1]} for p in pairs]
    assert {mapping[a], mapping[b]} in pair_id_sets
    assert {mapping[c], mapping[d]} not in pair_id_sets
