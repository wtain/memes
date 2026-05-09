import asyncio

import numpy as np
from sqlalchemy import select

from batch.models.external import Concept, AsyncSessionLocal


async def main():
    async with AsyncSessionLocal() as session:
        results = await session.execute(
            select(Concept.name, Concept.embedding)
        )
        results = results.all()
        for (name1, embedding1, ) in results:
            print(f"{name1}: {max(np.dot(embedding1, embedding2) for (name2, embedding2, ) in results if name1 != name2)}")
            for (name2, embedding2, ) in results:
                if name1 == name2:
                    continue
                similarity = np.dot(embedding1, embedding2)
                if similarity > 0.8:
                    print(f"-- {name2} ({similarity})")




if __name__ == "__main__":
    asyncio.run(main())