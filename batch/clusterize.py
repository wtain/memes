import asyncio
import uuid
from collections import defaultdict

from sqlalchemy import and_, or_, select, delete

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import settings
from graph.uf import UnionFind
from Storage.db import AsyncSessionLocal
from Storage.models import DuplicateDecision, Image, TmpDuplicates, TmpImageClusters

PROXIMITY_THRESHOLD = 0.05

# Matches TEXT_EMBEDDING_TIGHT_THRESHOLD in batch/rebuild_duplicates.py. A separately-named
# constant, not a shared import, even though the two are numerically equal today -- see
# docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md: never assume the two
# scales stay coupled, so a future change to either doesn't silently move the other. Safe to trust
# again as of docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md -- every
# ocr_text-sourced row in tmp_duplicates has already passed that spec's insertion-time lemma-
# overlap gate by the time this function reads it.
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
    Returns a list of finalized member-id lists -- each either within max_size, or still
    oversized because splitting hit `floor` or a tightening step found no surviving
    sub-structure at all (a "total wipe" -- both are treated as "give up, accept as-is").
    Clusters of size < 2 are dropped entirely; a >= 2-member group is never dropped.
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

    roots = sub_uf.list_clusters()
    if not roots:
        # Tightening to next_threshold severed every remaining edge among these members at
        # once -- recursing into an empty sub_uf would return [] and silently drop the whole
        # group (up to hundreds of members for a real dense CLIP "hub" -- see
        # docs/superpowers/specs/2026-09-20-clusterize-oversized-cluster-data-loss.md). Treat
        # a total wipe the same as hitting `floor`: give up and accept the group oversized as
        # one group rather than destroying it. Does NOT change the *other* outcome --
        # partial success, where some members drop as true singletons but at least one
        # sub-component of size >= 2 survives -- that recursion path is unchanged below.
        return [members]

    results: list[list[int]] = []
    for root in roots:
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
    pairs = await get_duplicate_pairs(session, img_id_to_int_id, PROXIMITY_THRESHOLD, PROXIMITY_THRESHOLD_OCR_TEXT)
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


async def get_duplicate_pairs(session, mapping, clip_threshold, ocr_text_threshold) -> list[tuple[int, int, float]]:
    """Active-library auto-clustering trusts both clip- and ocr_text-sourced tmp_duplicates rows,
    each gated by its own threshold. ocr_text-sourced rows are safe to trust here specifically
    because docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md's lexical-overlap
    corroboration check already ran at INSERTION time (batch/rebuild_duplicates.py's and
    batch/ingest_find_duplicates.py's OCR-text probes) -- this function doesn't need to know
    anything about ocr_lemmas itself, only that distance_source='ocr_text' already implies the
    check passed. See that spec's Design §4 for the full reasoning, and
    docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md's "Known limitation"
    section for why this trust was temporarily withdrawn before that spec existed."""
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
            or_(
                and_(TmpDuplicates.distance_source == "clip", TmpDuplicates.distance < clip_threshold),
                and_(TmpDuplicates.distance_source == "ocr_text", TmpDuplicates.distance < ocr_text_threshold),
            ),
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
