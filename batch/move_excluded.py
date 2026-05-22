import asyncio
import os
import shutil

from sqlalchemy import select

from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageExtras


async def main():

    async with AsyncSessionLocal() as session:
        query = (
            select(
                Image.filename,
            )
            .join(ImageExtras, ImageExtras.image_id == Image.id)
            .where(ImageExtras.exclude == True)
        )
        images = await session.execute(query)

        BASE_PATH = os.getenv('BASE_PATH')
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)
        excluded_path = os.path.join(base_path, "excluded")
        os.makedirs(excluded_path, exist_ok=True)

        for (filename, ) in images:
            path_from = os.path.join(base_path, filename)
            path_to = os.path.join(excluded_path, filename)
            print(f"Moving {filename} from {path_from} to {path_to}")
            shutil.move(path_from, path_to)


if __name__ == "__main__":
    asyncio.run(main())