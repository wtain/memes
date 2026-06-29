import asyncio
import os
import shutil
from pathlib import Path

from sqlalchemy import select, and_
from sqlalchemy.orm import aliased

from graph.uf import UnionFind
from Storage.db import AsyncSessionLocal
from Storage.models import Image, Embedding


async def main():

    async with AsyncSessionLocal() as session:
        img1 = aliased(Image)
        embed1 = aliased(Embedding)
        img2 = aliased(Image)
        embed2 = aliased(Embedding)
        query = (
            select(
                img1.id,
                img1.filename,
                img2.id,
                img2.filename,
                embed1.embedding.cosine_distance(embed2.embedding).label("distance")
            )
            .select_from(img1, img2)
            .join(
                embed1, embed1.image_id == img1.id
            )
            .join(
                embed2, embed2.image_id == img2.id
            )
            .where(
                and_(
                    embed1.embedding.cosine_distance(embed2.embedding) < 0.05,
                    img1.id != img2.id
                )
            )
            .order_by(
                img1.id,
                img2.id
            )
        )
        images = await session.execute(query)

        base_path = os.path.abspath("c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\")

        uf = UnionFind()

        for (id1, filename1, id2, filename2, distance,) in images:
            uf.connect(filename1, filename2)

        for filename1 in uf.list_clusters():
            print(f"Cluster {filename1}")
            cluster_name = Path(filename1).stem
            for filename2 in uf.get_cluster(filename1):
                print(f">> {filename2}")
                path_from = os.path.join(base_path, filename2)
                path_to = os.path.join(base_path, f"Cluster_{cluster_name}_{filename2}")
                print(f"Moving {filename2} from {path_from} to {path_to}")
                shutil.move(path_from, path_to)



if __name__ == "__main__":
    asyncio.run(main())