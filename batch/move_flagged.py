"""
Moves every image flagged in ImageExtras to BASE_PATH/excluded (renaming instead of overwriting; missing
files are counted and skipped). An image flagged `comics_source` that still has a comics panel goes to
BASE_PATH/comics-sources/<comics name>/ instead (created on demand; counter moved_to_comics_sources besides
moved); a comics_source image with no panel or comics row falls back to excluded. Run-tracked (kind move_flagged); unless chain=False (--no-chain) it then runs
unregister_deleted_images, which in turn runs remove_singletons. Admin-triggerable, manual only.
"""
import argparse
import asyncio
import os
import uuid

from sqlalchemy import select

from batch import unregister_deleted_images
from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.safe_move import move_without_overwrite
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchAlreadyRunningError, BatchRunRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Comics, ComicsPanel, Image, ImageExtras

COMICS_SOURCE_REASON = "comics_source"


async def run(session, base_path) -> SimpleMetricsListener:
    metrics = SimpleMetricsListener()

    query = (
        select(
            Image.id,
            Image.filename,
            ImageExtras.flagged_reason,
            Comics.name,
        )
        .join(ImageExtras, ImageExtras.image_id == Image.id)
        .outerjoin(ComicsPanel, ComicsPanel.image_id == Image.id)
        .outerjoin(Comics, Comics.id == ComicsPanel.comics_id)
        .where(ImageExtras.flagged == True)
    )
    images = await session.execute(query)

    flagged_path = os.path.join(base_path, "excluded")
    os.makedirs(flagged_path, exist_ok=True)

    for (_image_id, filename, reason, comics_name) in images:
        path_from = os.path.join(base_path, filename)
        to_comics = reason == COMICS_SOURCE_REASON and comics_name is not None
        dest = os.path.join(base_path, "comics-sources", comics_name) if to_comics else flagged_path
        try:
            if to_comics:
                os.makedirs(dest, exist_ok=True)
            print(f"Moving {filename} from {path_from} to {dest}")
            final_filename = move_without_overwrite(path_from, dest)
            if final_filename != filename:
                print(f"Renamed to avoid overwrite: {filename} -> {final_filename}")
                metrics.increment("renamed_to_avoid_overwrite")
            metrics.increment("moved")
            if to_comics:
                metrics.increment("moved_to_comics_sources")
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
