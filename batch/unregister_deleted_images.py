"""
Deletes the database rows of images whose files no longer exist in BASE_PATH (cascades clean up
dependent rows). Run-tracked (kind unregister_deleted_images); unless chain=False (--no-chain) it then runs
remove_singletons (no run row of its own). Admin-triggerable, manual only; run automatically after move_flagged.
"""
import argparse
import asyncio
import os
import uuid

from batch import remove_singletons
from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env
from Storage.db import AsyncSessionLocal
from batch.tasks.SourceTasks import UnregisterNonExisting


async def run(session, base_path):
    task = UnregisterNonExisting(session, base_path)
    await task.run()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, chain: bool = True) -> None:
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

    if chain:
        async with AsyncSessionLocal() as session:
            metrics = await remove_singletons.run(session)
            await session.commit()
        metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--no-chain", action="store_true",
                        help="Skip the automatic remove_singletons cleanup after unregistering deleted images.")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(chain=not args.no_chain))
