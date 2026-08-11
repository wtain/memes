"""
Retroactive maintenance batch: applies the same format validation/fix logic as
batch/ingest_validate_formats.py (ingestion Stage 1.5) to images that were already
ingested before that check existed. See
docs/superpowers/specs/2026-08-11-ingestion-image-format-validation-design.md.

Safe to re-run at any time -- already-fixed images are no-ops on a subsequent pass.
Defaults to --status active (the existing corpus); pass --status pending to also cover an
old in-flight ingestion batch that predates this feature and never went through Stage 1.5.
"""
import argparse
import asyncio
import uuid

from sqlalchemy import select

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.image_format_apply import apply_format_fix
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from repository.image_extras import ImageExtrasRepository
from repository.images import ImagesRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image


async def get_images_by_status(session, status: str) -> list:
    result = await session.execute(
        select(Image.id, Image.filename).where(Image.status == status)
    )
    return result.all()


async def run(session, base_path: str, status: str) -> SimpleMetricsListener:
    metrics = SimpleMetricsListener()
    images_repo = ImagesRepository(session)
    extras_repo = ImageExtrasRepository(session)

    for image_id, filename in await get_images_by_status(session, status):
        await apply_format_fix(images_repo, extras_repo, metrics, base_path, image_id, filename)

    return metrics


async def main(status: str = "active", trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    base_path = settings.BASE_PATH

    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, base_path, status)
                await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
                await session.commit()
    else:
        async with tracked_run(kind="fix_image_formats", trigger=trigger) as run_id:
            async with AsyncSessionLocal() as session:
                metrics = await run(session, base_path, status)
                await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
                await session.commit()

    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--status", choices=["active", "pending"], default="active")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(status=args.status))
