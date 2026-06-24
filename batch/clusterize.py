import asyncio

from sqlalchemy import select, delete

from graph.uf import UnionFind
from Storage.db import AsyncSessionLocal
from Storage.models import Image, TmpDuplicates, TmpImageClusters

PROXIMITY_THRESHOLD = 0.05


async def main():

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
        # Select all duplicates with distance < THRESHOLD and convert img1 to int id
        uf = await get_duplicates(session, img_id_to_int_id, PROXIMITY_THRESHOLD)

        print("Building graph...")
        # Traverse UnionFind and mark clusters
        for cluster_id in uf.list_clusters():
            for id in uf.get_cluster(cluster_id):
                img_id = mapping_reverse[id]
                session.add(TmpImageClusters(cluster_id=cluster_id, image_id=img_id))

        print("Saving results...")
        # Save changes to the database
        await session.commit()



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


async def get_duplicates(session, mapping, threshold):
    query = (
        select(
            TmpDuplicates.image_id1,
            TmpDuplicates.image_id2,
        ).where(
            TmpDuplicates.distance < threshold
        )
    )
    duplicates = await session.execute(query)
    uf = UnionFind()
    cnt = 0
    for (id1, id2, ) in duplicates:
        i1 = mapping[id1]
        i2 = mapping[id2]
        uf.connect(i1, i2)
        cnt += 1
    print(f"Total connections: {cnt}")

    return uf


if __name__ == "__main__":
    asyncio.run(main())