import argparse
import asyncio
import os

from config.settings import load_env
from Storage.db import AsyncSessionLocal
from batch.tasks.SourceTasks import UnregisterNonExisting


async def run(session, base_path):
    task = UnregisterNonExisting(session, base_path)
    await task.run()


async def main():
    async with AsyncSessionLocal() as session:
        BASE_PATH = os.getenv('BASE_PATH')
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)
        await run(session, base_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
