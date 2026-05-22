import asyncio
import os
import shutil

from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository


async def run():
    source_path = os.getenv('BASE_PATH')

    moved_path = os.path.join(source_path, "moved")
    os.makedirs(moved_path, exist_ok=True)

    async with AsyncSessionLocal() as session:
        image_repo = ImagesRepository(session)
        for file in os.listdir(source_path):
            image = await image_repo.find_image_by_filename(file)
            if image:
                continue
            print(f"Not found: {file}")
            path_from = os.path.join(source_path, file)
            if os.path.isdir(path_from):
                print(f"Directory: {path_from}")
                continue
            path_to = os.path.join(moved_path, file)
            print(f"Moving {file} from {path_from} to {path_to}")
            shutil.move(path_from, path_to)


async def main():
    await run()

if __name__ == "__main__":
    asyncio.run(main())