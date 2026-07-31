import argparse
import asyncio
import os
import shutil
import uuid

from sqlalchemy import select

from batch import unregister_deleted_images
from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchAlreadyRunningError, BatchRunRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageExtras


async def run(session, base_path) -> SimpleMetricsListener:
    metrics = SimpleMetricsListener()

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
        try:
            print(f"Moving {filename} from {path_from} to {path_to}")
            shutil.move(path_from, path_to)
            metrics.increment("moved")
        except FileNotFoundError as e:
            print(f"Skipping {filename}: not found ({e})")
            metrics.increment("error.file_not_found")
        except Exception as e:
            print(f"Skipping {filename}: move failed ({e})")
            metrics.increment("error.move_failed")

    return metrics


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, chain: bool = True) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                metrics = await run(session, base_path)
                await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
                await session.commit()
    else:
        async with tracked_run(kind="move_flagged", trigger=trigger) as run_id:
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                metrics = await run(session, base_path)
                await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
                await session.commit()

    metrics.print()

    if chain:
        try:
            await unregister_deleted_images.main(trigger=trigger)
        except BatchAlreadyRunningError as e:
            print(f"Skipping chained unregister_deleted_images: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument(
        "--no-chain",
        action="store_true",
        help="Skip the automatic unregister_deleted_images reconcile after moving flagged files.",
    )
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(chain=not args.no_chain))  # trigger defaults to "manual" -- unchanged direct-CLI behavior
