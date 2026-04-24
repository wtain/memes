import asyncio
import os

from batch.models.external import AsyncSessionLocal
from batch.tasks.SourceTasks import UnregisterNonExisting


async def main():

    async with AsyncSessionLocal() as session:

        base_path = os.path.abspath("c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\")

        task = UnregisterNonExisting(session, base_path)

        await task.run()


if __name__ == "__main__":
    asyncio.run(main())