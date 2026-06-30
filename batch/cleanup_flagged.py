import asyncio
import os

from Storage.db import AsyncSessionLocal
from batch.move_flagged import run as move_flagged
from batch.unregister_deleted_images import run as unregister_deleted_images


async def main():
    async with AsyncSessionLocal() as session:
        BASE_PATH = os.getenv('BASE_PATH')
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)

        print("=== Moving flagged images ===")
        await move_flagged(session, base_path)

        print("=== Unregistering deleted images ===")
        await unregister_deleted_images(session, base_path)


if __name__ == "__main__":
    asyncio.run(main())
