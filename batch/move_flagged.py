import asyncio
import os
import shutil

from sqlalchemy import select

from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageExtras


async def run(session, base_path):
    query = (
        select(
            Image.filename,
        )
        .join(ImageExtras, ImageExtras.image_id == Image.id)
        .where(ImageExtras.flagged == True)
    )
    images = await session.execute(query)

    flagged_path = os.path.join(base_path, "excluded")
    os.makedirs(flagged_path, exist_ok=True)

    for (filename, ) in images:
        path_from = os.path.join(base_path, filename)
        path_to = os.path.join(flagged_path, filename)
        print(f"Moving {filename} from {path_from} to {path_to}")
        shutil.move(path_from, path_to)


async def main():
    async with AsyncSessionLocal() as session:
        BASE_PATH = os.getenv('BASE_PATH')
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)
        await run(session, base_path)


if __name__ == "__main__":
    asyncio.run(main())
