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
            )
        )
        images = await session.execute(query)

        base_path = os.path.abspath("c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\")

        existing_filenames = set(filename for (filename, ) in images)
        print(f"Total existing: {len(existing_filenames)}")

        # for file in os.listdir(base_path):

        print("Committing...")
        await session.commit()
        print("DONE")


if __name__ == "__main__":
    asyncio.run(main())