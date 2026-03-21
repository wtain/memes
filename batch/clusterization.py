import asyncio
import os
import hdbscan
import numpy as np
from sqlalchemy import select

from batch.models.external import AsyncSessionLocal
from batch.models.external import ImageTag, OCRText, Embedding

from batch.models.external import Image as Img

async def main():

    async with AsyncSessionLocal() as session:

        query = (
            select(Embedding.embedding)
        )

        results = await session.execute(query)
        results = results.all()

        image_embeddings = [np.array(embedding) for (embedding, ) in results]
        image_embeddings = np.vstack(image_embeddings)

        clusterer = hdbscan.HDBSCAN(min_cluster_size=5, metric='euclidean')
        # clusterer = hdbscan.HDBSCAN(min_cluster_size=5, metric='cosine') # ⚠️ Slower, but sometimes more stable. // doesn't work
        labels = clusterer.fit_predict(image_embeddings)
        print("Unique labels:", np.unique(labels))
        print("Noise ratio:", np.mean(labels == -1))


if __name__ == "__main__":
    asyncio.run(main())