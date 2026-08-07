"""
Ingestion abort: abandon the currently active ingestion run.

Undoes every pending/rejected image the run registered -- moves each file back to
PATH_INGESTION_SOURCE and deletes its Image row (every images.id FK in Storage/models.py
is ondelete='CASCADE', so embeddings/ocr_texts/tmp_duplicates/tmp_clusters clean up
automatically). Marks the batch_runs row 'aborted', freeing the one-active-run-per-kind
lock so a new ingestion run can start. Already-active (promoted) images in the batch are
never touched -- undoing a promotion is out of scope, see
docs/superpowers/specs/2026-08-07-ingestion-abort-design.md.

Known limitation: file moves are real, immediate OS operations, but the DB delete is
staged inside main()'s single final commit. If the process dies after some files have
already moved back but before that commit, those images' rows still exist pointing at
files no longer in BASE_PATH -- this self-heals on the next unregister_deleted_images run
(which already deletes rows for images whose files don't exist), matching
ingest_hash_dedup.py's own accepted crash-safety posture. Not addressed further here.
"""
import argparse
import asyncio
import os

from sqlalchemy import delete

from batch.utils.safe_move import move_without_overwrite
from Backend.app.repositories.ingestion_repository import IngestionRepository
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image


async def run(session, source_path: str, base_path: str, batch_id) -> SimpleMetricsListener:
    """Undo every pending/rejected image in this batch: move its file back to
    source_path, then delete its row. Returns metrics; does not commit or touch the
    batch_runs row -- the caller owns both, in one transaction. Does not use
    ImagesRepository.delete_images() -- that method commits internally, which would
    break atomicity with the caller's abort() call (see this module's docstring)."""
    metrics = SimpleMetricsListener()
    repo = IngestionRepository(session)
    rows = await repo.list_abortable_images(batch_id)

    to_delete = []
    for image_id, filename, status in rows:
        src_dir = os.path.join(base_path, "rejected") if status == "rejected" else base_path
        src_path = os.path.join(src_dir, filename)
        try:
            move_without_overwrite(src_path, source_path)
            metrics.increment("moved_back")
        except Exception as e:
            print(f"Can't move {src_path} back to inbox: {e}")
            metrics.increment("error.move_failed")
        to_delete.append(image_id)

    if to_delete:
        await session.execute(delete(Image).where(Image.id.in_(to_delete)))
    metrics.add("unregistered", len(to_delete))
    return metrics


async def main(env: str | None) -> None:
    load_env(env)
    source_path = settings.get("PATH_INGESTION_SOURCE")
    if not source_path:
        raise RuntimeError("PATH_INGESTION_SOURCE is required but not set")
    base_path = settings.BASE_PATH

    async with AsyncSessionLocal() as session:
        runs_repo = BatchRunRepository(session)
        active_run = await runs_repo.get_active_run(kind="ingestion")
        if active_run is None:
            raise RuntimeError("No ingestion run is currently in progress -- nothing to abort.")

        metrics = await run(session, source_path, base_path, active_run.run_id)
        await runs_repo.abort(active_run.run_id, note="Aborted by user via ingest_abort.py")
        await session.commit()

    print(f"Aborted ingestion run {active_run.run_id}:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    asyncio.run(main(args.env))
