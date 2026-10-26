"""Screenshot recut, cut and copy (R3a; docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-25-r3a-cut-and-copy-design.md).
Turns each `detected` image_cuts proposal into a cut file (same format as the source), a `cut_proposed` image, copied
derived data (groups 1 and 3), a `proposed` state and the `proposed` metric event, one transaction per cut. Highest
confidence first; --limit caps a run. Safe to re-run: a proposal is cut once."""
import argparse
import asyncio
import contextlib
import os
import uuid

from sqlalchemy.exc import SQLAlchemyError

from Backend.app.services.business_metrics_service import BusinessMetricsService
from batch.detect_recut_crops import load_corpus_ids
from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.recut_cut import WRITE_ERROR, CutError, crop_to_file
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from repository.cut_copier import CutCopier
from repository.image_cuts import ImageCutsRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageStatus

KIND = "create_recut_cuts"
# This many failed proposals in a row means the drive or BASE_PATH is the problem, not the proposals: stop the run.
MAX_CONSECUTIVE_FAILURES = 20


def _remove(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


async def _cut_one(session, cuts: ImageCutsRepository, base_path: str, row, dst_name: str, jpeg_quality: int) -> None:
    crop = (row.crop_x, row.crop_y, row.crop_w, row.crop_h)
    cut_file = crop_to_file(os.path.join(base_path, row.filename), os.path.join(base_path, dst_name), crop,
                            row.source_content_hash, jpeg_quality)
    try:
        async with session.begin_nested():
            image = Image(id=uuid.uuid4(), filename=dst_name, content_hash=cut_file.content_hash,
                          width=cut_file.width, height=cut_file.height, status=ImageStatus.CUT_PROPOSED,
                          created_at=row.created_at)
            session.add(image)
            await session.flush()
            await CutCopier(session).copy(row.source_image_id, image.id, crop)
            await cuts.mark_proposed(row.cut_id, image.id)
            await BusinessMetricsService(session).record("recut", "proposed", row.detect_run_id, row.cut_id)
    except SQLAlchemyError as e:
        raise CutError(WRITE_ERROR, f"{type(e).__name__}: {e}"[:200]) from e


async def run(session, base_path: str, corpus_ids, jpeg_quality: int, limit: int | None = None,
              retry_failed: bool = False) -> SimpleMetricsListener:
    if not os.path.isdir(base_path):
        raise FileNotFoundError(f"BASE_PATH is not a directory: {base_path}")
    cuts = ImageCutsRepository(session)
    if retry_failed:
        print(f"Re-queued {await cuts.requeue_failed()} failed proposals")
    skipped = await cuts.count_ineligible_detected(corpus_ids)
    total = await cuts.count_cuttable(corpus_ids)
    rows = await cuts.select_cuttable(corpus_ids, limit=limit)
    print(f"Proposals to cut: {len(rows)} of {total} ({skipped} skipped: source not active, flagged or in the corpus)")

    metrics = SimpleMetricsListener()
    metrics.add("proposals_selected", len(rows))
    metrics.add("proposals_remaining", total - len(rows))
    metrics.add("skipped", skipped)
    metrics.add("cut", 0)
    metrics.add("failed", 0)

    streak = 0
    for row in rows:
        dst_name = f"{uuid.uuid4()}{os.path.splitext(row.filename)[1]}"
        dst = os.path.join(base_path, dst_name)
        print(f"cut {row.cut_id}: {row.filename} -> {dst_name}")
        try:
            await _cut_one(session, cuts, base_path, row, dst_name, jpeg_quality)
        except CutError as e:
            _remove(dst)
            await cuts.mark_failed(row.cut_id, e.reason)
            await session.commit()
            metrics.increment("failed")
            streak += 1
            if streak >= MAX_CONSECUTIVE_FAILURES:
                raise RuntimeError(f"{streak} proposals failed in a row (last: {e}); check BASE_PATH and the drive")
        except BaseException:
            _remove(dst)
            raise
        else:
            await session.commit()
            metrics.increment("cut")
            streak = 0
    return metrics


async def _execute(run_id: uuid.UUID, limit: int | None, retry_failed: bool = False) -> SimpleMetricsListener:
    corpus_ids = load_corpus_ids()
    async with AsyncSessionLocal() as session:
        metrics = await run(session, settings.BASE_PATH, corpus_ids, settings.RECUT.JPEG_QUALITY, limit=limit,
                            retry_failed=retry_failed)
        await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
        await session.commit()
    return metrics


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, limit: int | None = None,
               retry_failed: bool = False) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            metrics = await _execute(run_id, limit, retry_failed)
    else:
        async with tracked_run(kind=KIND, trigger=trigger) as new_run_id:
            metrics = await _execute(new_run_id, limit, retry_failed)
    print("Recut cut results:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--limit", type=int, default=None, help="cut at most this many proposals this run")
    parser.add_argument("--retry-failed", action="store_true",
                        help="first put failed proposals back to detected (after a drive or database outage)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(limit=args.limit, retry_failed=args.retry_failed))
