import asyncio
import hdbscan
import numpy as np
from sqlalchemy import select

from Storage.db import AsyncSessionLocal
from Storage.models import Embedding

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
        labels = clusterer.fit_predict(image_embeddings)
        print("Unique labels:", np.unique(labels))
        print("Noise ratio:", np.mean(labels == -1))


if __name__ == "__main__":
    asyncio.run(main())