import asyncio
import os

from sqlalchemy import select, delete

from batch.models.external import AsyncSessionLocal
from batch.models.external import Image


async def main():

    async with AsyncSessionLocal() as session:
        query = (
            select(
                Image.filename,
                Image.id,
            )
        )
        images = await session.execute(query)

        base_path = os.path.abspath("c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\")

        non_existent = set()
        for (filename, id, ) in images:
            path = os.path.join(base_path, filename)
            if not os.path.exists(path):
                print(f"Doesn't exist: {path}")
                non_existent.add(id)

        print(f"Non existing: {len(non_existent)}")

        delete_query = (
            delete(
                Image
            )
            .where(
                Image.id.in_(non_existent)
            )
        )

        print("Deleting...")
        await session.execute(delete_query)
        print("Committing...")
        await session.commit()
        print("DONE")


if __name__ == "__main__":
    asyncio.run(main())