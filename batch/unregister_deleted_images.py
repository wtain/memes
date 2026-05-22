import asyncio
import os

from Storage.db import AsyncSessionLocal
from batch.tasks.SourceTasks import UnregisterNonExisting


async def main():

    async with AsyncSessionLocal() as session:

        BASE_PATH = os.getenv('BASE_PATH')
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)

        task = UnregisterNonExisting(session, base_path)

        await task.run()


if __name__ == "__main__":
    asyncio.run(main())