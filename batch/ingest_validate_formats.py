"""
Ingestion Stage 1.5: validate/fix image format vs extension mismatches (and convert WebP
to JPEG) for a batch's pending images, before embeddings/OCR run on them. See
docs/superpowers/specs/2026-08-11-ingestion-image-format-validation-design.md.

Runs after ingest_hash_dedup.py, before build_image_embeddings.py --status pending. Joins
the same active ingestion run Stage 1 used -- this is an additional stage of that run, not
an independent batch -- exactly like ingest_find_duplicates.py does for Tier A/B.

Safe to re-run at any point: an already-fixed image is a no-op (its extension already
matches its real, non-webp format) on a later pass. Stats accumulate across invocations
the same way Stage 1's do, via the same accumulate_stats helper.
"""
import argparse
import asyncio

from sqlalchemy import select

from batch.ingest_hash_dedup import accumulate_stats
from batch.utils.image_format_apply import apply_format_fix
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from repository.image_extras import ImageExtrasRepository
from repository.images import ImagesRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image

STAGE = "format_validation"


async def get_pending_batch_images(session, batch_id) -> list:
    """Returns [(image_id, filename), ...] for this batch's still-pending images."""
    result = await session.execute(
        select(Image.id, Image.filename).where(
            Image.status == "pending", Image.ingestion_batch_id == batch_id,
        )
    )
    return result.all()


async def run(session, base_path: str, batch_id) -> SimpleMetricsListener:
    metrics = SimpleMetricsListener()
    images_repo = ImagesRepository(session)
    extras_repo = ImageExtrasRepository(session)

    for image_id, filename in await get_pending_batch_images(session, batch_id):
        await apply_format_fix(images_repo, extras_repo, metrics, base_path, image_id, filename)

    return metrics


async def main(env: str | None) -> None:
    load_env(env)
    base_path = settings.BASE_PATH

    async with AsyncSessionLocal() as session:
        runs_repo = BatchRunRepository(session)
        active_run = await runs_repo.get_active_run(kind="ingestion")
        if active_run is None:
            raise RuntimeError(
                "No ingestion run is currently in progress -- run ingest_hash_dedup.py first."
            )

        metrics = await run(session, base_path, active_run.run_id)
        existing_stats = active_run.stats or {}
        await runs_repo.update_stats(active_run.run_id, **accumulate_stats(existing_stats, metrics.counters_dict()))
        await runs_repo.set_stage(active_run.run_id, STAGE)
        await session.commit()

    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    asyncio.run(main(args.env))
