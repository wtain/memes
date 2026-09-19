import asyncio
import uuid
from collections import defaultdict

from sqlalchemy import select, delete

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import settings
from graph.uf import UnionFind
from Storage.db import AsyncSessionLocal
from Storage.models import DuplicateDecision, Image, TmpDuplicates, TmpImageClusters

PROXIMITY_THRESHOLD = 0.05

# Currently UNUSED by get_duplicate_pairs() -- ocr_text-sourced pairs are deliberately excluded
# from active-library auto-clustering (see get_duplicate_pairs()'s own comment below for why).
# Kept defined, not deleted, so a future recalibrated design doesn't have to re-derive this value
# from scratch -- see docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md's
# "Known limitation" section for the full incident writeup and what a fix needs to address.
PROXIMITY_THRESHOLD_OCR_TEXT = 0.05


def resolve_cluster(
    members: list[int],
    pairs_by_member: dict[int, list[tuple[int, float]]],
    threshold: float,
    decrement: float,
    floor: float,
    max_size: int,
) -> list[list[int]]:
    """Recursively split an oversized cluster by progressively tightening the distance
    threshold, dropping any member left with no surviving edge (an implicit singleton)
    along the way. Pure / DB-free so it's independently unit-testable.

    members: the int ids in this cluster.
    pairs_by_member: id -> list of (neighbor id, distance) below the *original*
        PROXIMITY_THRESHOLD, symmetric (each pair present from both endpoints).
    Returns a list of finalized member-id lists -- each either within max_size, or
    still oversized because splitting hit `floor` without shrinking it further.
    Clusters of size < 2 are dropped entirely.
    """
    members = list(members)
    if len(members) <= 1:
        return []
    if len(members) <= max_size:
        return [members]

    next_threshold = threshold - decrement
    if next_threshold < floor:
        return [members]  # give up -- accept the cluster oversized as-is

    member_set = set(members)
    sub_uf = UnionFind()
    for member in members:
        for neighbor, distance in pairs_by_member.get(member, ()):
            if neighbor in member_set and distance < next_threshold:
                sub_uf.connect(member, neighbor)

    results: list[list[int]] = []
    for root in sub_uf.list_clusters():
        sub_members = sub_uf.get_cluster(root)
        results.extend(
            resolve_cluster(sub_members, pairs_by_member, next_threshold, decrement, floor, max_size)
        )
    return results


async def cluster_active_library(session) -> None:

    print("Cleaning up clusters...")
    query = (
        delete(TmpImageClusters)
    )
    await session.execute(query)

    print("Reading images...")
    # Select all images and build image_id -> int id dictionary (and reverse)
    img_id_to_int_id, mapping_reverse = await get_images_ids(session)
    print(f"Total images: {len(img_id_to_int_id)}")

    print("Reading duplicates...")
    # Select all duplicate pairs with distance < PROXIMITY_THRESHOLD, int-id mapped
    pairs = await get_duplicate_pairs(session, img_id_to_int_id, PROXIMITY_THRESHOLD)
    print(f"Total connections: {len(pairs)}")

    uf = UnionFind()
    pairs_by_member: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for id1, id2, distance in pairs:
        uf.connect(id1, id2)
        pairs_by_member[id1].append((id2, distance))
        pairs_by_member[id2].append((id1, distance))

    splitting = settings.CLUSTERING.SPLITTING

    print("Building graph...")
    # Traverse UnionFind, splitting oversized clusters and dropping singletons, and
    # mark the resulting clusters
    for root in uf.list_clusters():
        members = uf.get_cluster(root)
        if splitting.ENABLED:
            groups = resolve_cluster(
                members,
                pairs_by_member,
                PROXIMITY_THRESHOLD,
                splitting.DECREMENT,
                splitting.FLOOR,
                splitting.MAX_CLUSTER_SIZE,
            )
        else:
            groups = [members]

        for group in groups:
            # min() is unique across the whole run -- finalized groups always
            # partition disjoint member sets, so no two groups can share it.
            cluster_id = min(group)
            for member in group:
                img_id = mapping_reverse[member]
                session.add(TmpImageClusters(cluster_id=cluster_id, image_id=img_id))

    print("Saving results...")


async def _process() -> None:
    async with AsyncSessionLocal() as session:
        await cluster_active_library(session)
        await session.commit()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="clusterize", trigger=trigger):
            await _process()


async def get_images_ids(session):
    # active only -- matches rebuild_duplicates.py's active-library scoping
    # (_ACTIVE_PROBE_INCREMENTAL/_ACTIVE_CORPUS_FILTER_CLIP) and the review API's own
    # status == 'active' filter (Backend/app/repositories/image_repository.py's
    # get_duplicates_clustered). Without this, a pending/rejected image left over
    # from an ingestion probe still gets clustered here, and since the review API
    # then filters it back out, a real 2+-member cluster can collapse to what looks
    # like a singleton in the UI even though tmp_clusters itself has >1 row.
    query = (
        select(
            Image.id
        ).where(
            Image.status == "active",
        )
    )
    images = await session.execute(query)
    result = {}
    result_reverse = {}
    int_id = 1
    for (id, ) in images:
        result[id] = int_id
        result_reverse[int_id] = id
        int_id += 1

    return result, result_reverse


async def get_duplicate_pairs(session, mapping, clip_threshold) -> list[tuple[int, int, float]]:
    """Active-library auto-clustering is CLIP-only, deliberately -- ocr_text-sourced pairs are
    excluded here, not just at a loose threshold. Live-rollout investigation on 2026-09-19 found
    the OCR-text embedding signal produces a genuine "hub" false-positive pattern (informal/
    profanity-heavy meme captions cluster by register, not actual joke content -- the same
    "same template != same content" failure this whole feature exists to fix for CLIP, recurring
    in the text signal) at a rate too high to auto-confirm without human review: ~41% of
    general's ocr_text candidate pairs had CLIP distance >= 0.35 (almost certainly visually
    unrelated), and a corroborating-CLIP-distance threshold alone could not cleanly separate the
    remaining ambiguous middle third from genuine reposts (a confirmed true positive and a
    confirmed false positive were both found in the same 0.25-0.35 CLIP-distance band). See
    docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md's "Known limitation"
    section for the full writeup and what a recalibrated design needs to address before this can
    be safely re-enabled.

    ocr_text-sourced candidate pairs are still inserted into tmp_duplicates as before (Tasks 2/3
    of that spec are unaffected) and still surfaced for Tier A/B ingestion review, where a human
    reviewer -- not an unsupervised auto-cluster -- makes the final call; only this function's
    own active-library auto-clustering path is scoped back to CLIP-only."""
    decided_pair_exists = (
        select(DuplicateDecision.id)
        .where(
            DuplicateDecision.image_id1 == TmpDuplicates.image_id1,
            DuplicateDecision.image_id2 == TmpDuplicates.image_id2,
        )
        .exists()
    )
    query = (
        select(
            TmpDuplicates.image_id1,
            TmpDuplicates.image_id2,
            TmpDuplicates.distance,
        ).where(
            TmpDuplicates.distance_source == "clip",
            TmpDuplicates.distance < clip_threshold,
            TmpDuplicates.image_id1 != TmpDuplicates.image_id2,
            ~decided_pair_exists,
        )
    )
    duplicates = await session.execute(query)
    # mapping is active-only (see get_images_ids) -- drop any pair touching a
    # pending/rejected image rather than KeyError on it.
    return [
        (mapping[id1], mapping[id2], distance)
        for id1, id2, distance in duplicates
        if id1 in mapping and id2 in mapping
    ]


if __name__ == "__main__":
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
