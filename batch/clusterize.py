import asyncio
import uuid
from collections import defaultdict

from sqlalchemy import select, delete

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import settings
from graph.uf import UnionFind
from Storage.db import AsyncSessionLocal
from Storage.models import Image, TmpDuplicates, TmpImageClusters

PROXIMITY_THRESHOLD = 0.05


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


async def _process() -> None:

    async with AsyncSessionLocal() as session:

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
        # Save changes to the database
        await session.commit()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="clusterize", trigger=trigger):
            await _process()


async def get_images_ids(session):
    query = (
        select(
            Image.id
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


async def get_duplicate_pairs(session, mapping, threshold) -> list[tuple[int, int, float]]:
    query = (
        select(
            TmpDuplicates.image_id1,
            TmpDuplicates.image_id2,
            TmpDuplicates.distance,
        ).where(
            TmpDuplicates.distance < threshold,
            TmpDuplicates.image_id1 != TmpDuplicates.image_id2,
        )
    )
    duplicates = await session.execute(query)
    return [(mapping[id1], mapping[id2], distance) for id1, id2, distance in duplicates]


if __name__ == "__main__":
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
