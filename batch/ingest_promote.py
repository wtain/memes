"""
Ingestion Stage 4 (final): promote pending images that have cleared both Tier A and Tier B
review to `active`. See docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md.

A pure status flip -- no file move, since Stage 1 already moved the file into BASE_PATH.
"Clear" means no remaining tmp_duplicates row (in either tier's distance band) where the
image is one side, the other side isn't already rejected, and that tier's reviewed_at is
still null -- i.e. exactly the same "still actionable" definition the review queues use
(Backend/app/repositories/ingestion_repository.py's get_tier_candidate_rows), so an image
never gets promoted out from under an unresolved review item.

The ingestion run itself is only marked complete (batch_runs.status -> completed) once
every pending image in the batch has been resolved one way or another (promoted or
rejected) -- running this script again later, after more images clear review, is safe and
expected; it's a no-op once there's nothing left to promote.

Once active, an image needs no further ingestion-specific handling: the rest of the
pipeline (build_tags_from_ocr, build_ocr_lemmas, build_image_descriptions, ...) already
defaults to status="active" everywhere (see
docs/superpowers/specs/2026-07-25-image-visibility-status-design.md), so a newly-promoted
image is picked up automatically the next time those scripts run. extract_text_from_memes
is the one exception worth calling out -- it's *not* re-run here, because Tier B's OCR
pre-pass already ran it with --status pending before this image ever reached this stage.
"""
import argparse
import asyncio
import uuid

from sqlalchemy import func, select

from batch.clusterize import PROXIMITY_THRESHOLD as TIER_A_THRESHOLD
from batch.run_tracking import finish_existing_run, tracked_run
from Backend.app.repositories.ingestion_repository import IngestionRepository
from config.settings import load_env, settings
from repository.batch_runs import BatchRunRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image


async def get_promotable_ids(repo: IngestionRepository, batch_id, tier_b_threshold: float) -> list:
    blocked = await repo.get_blocked_pending_ids(batch_id, TIER_A_THRESHOLD, tier_b_threshold)
    pending_rows = await repo.list_pending_images(batch_id)
    return [image_id for image_id, _, _ in pending_rows if image_id not in blocked]


async def promote(session, batch_id, tier_b_threshold: float) -> list:
    repo = IngestionRepository(session)
    promotable = await get_promotable_ids(repo, batch_id, tier_b_threshold)
    await repo.promote_images(promotable)
    return promotable


async def maybe_complete_run(session, runs_repo: BatchRunRepository, batch_id) -> int:
    """If no pending images remain in this batch, mark the run's stage `promoted` and the
    run itself `completed`. Returns the remaining pending count either way, so a caller
    running this after a partial promotion (some images still blocked) can report it."""
    remaining = (await session.execute(
        select(func.count()).select_from(Image)
        .where(Image.ingestion_batch_id == batch_id, Image.status == "pending")
    )).scalar_one()
    if remaining == 0:
        await runs_repo.set_stage(batch_id, "promoted")
        await runs_repo.commit(batch_id)
    return remaining


async def _process() -> None:
    tier_b_threshold = settings.DUPLICATES.THRESHOLD

    async with AsyncSessionLocal() as session:
        runs_repo = BatchRunRepository(session)
        active_run = await runs_repo.get_active_run(kind="ingestion")
        if active_run is None:
            raise RuntimeError("No ingestion run is currently in progress -- nothing to promote.")

        promoted = await promote(session, active_run.run_id, tier_b_threshold)
        remaining = await maybe_complete_run(session, runs_repo, active_run.run_id)
        await session.commit()

    print(f"Promoted {len(promoted)} image(s); {remaining} still pending review.")


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="ingest_promote", trigger=trigger):
            await _process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
