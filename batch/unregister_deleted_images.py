import argparse
import asyncio
import os
import uuid

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env
from Storage.db import AsyncSessionLocal
from batch.tasks.SourceTasks import UnregisterNonExisting


async def run(session, base_path):
    task = UnregisterNonExisting(session, base_path)
    await task.run()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(os.getenv('BASE_PATH'))
                await run(session, base_path)
    else:
        async with tracked_run(kind="unregister_deleted_images", trigger=trigger):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(os.getenv('BASE_PATH'))
                await run(session, base_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
