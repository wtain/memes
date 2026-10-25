"""Screenshot recut, crop detection (R2; docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-24-r2-crop-detection-design.md).
Finds active images with a flat margin around the meme and stores one `detected` proposal per image in
image_cuts. Creates no files, changes no image status and records no metric event (R3 does). Checked images
are remembered in image_processing_status under a pipeline name that carries the detector version, so a re-run
only sees new images and a new detector version sees everything again. Safe to re-run."""
import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

from PIL import Image as PILImage

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.crop_detection import (DETECTOR, DETECTOR_VERSION, DetectParams, clips_ocr,
                                        detect_margin_box)
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from repository.image_cuts import ImageCutsRepository
from repository.image_procesing_status import ImageProcessingStatusRepository
from Storage.db import AsyncSessionLocal

KIND = "detect_recut_crops"
# Failures that mean "this file cannot be read"; anything else is a code bug and fails the run.
FILE_ERRORS = (OSError, ValueError, SyntaxError, PILImage.DecompressionBombError)
# This many unreadable files in a row means the drive or BASE_PATH is the problem, not the files: stop the run.
MAX_CONSECUTIVE_FAILURES = 20
PIPELINE = f"recut_detect_v{DETECTOR_VERSION}"
CORPUS_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "gold" / "corpus.json"


def load_corpus_ids(path: Path = CORPUS_PATH) -> set[uuid.UUID]:
    """Image ids of the frozen evaluation corpus (FR-17: never proposed). The ids are for `general`; on the
    other environments they match nothing. A missing file is an error, not an empty set, so the exclusion can
    never silently switch off."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {uuid.UUID(item["imageId"]) for item in data["images"]}


def params_from_settings() -> DetectParams:
    r = settings.RECUT
    return DetectParams(min_removed_share=r.MIN_REMOVED_SHARE, max_removed_share=r.MAX_REMOVED_SHARE,
                        flat_tolerance=r.FLAT_TOLERANCE, ocr_overlap_max=r.OCR_OVERLAP_MAX)


async def run(session, base_path: str, params: DetectParams, ocr_confidence_min: float,
              run_id: uuid.UUID | None, corpus_ids, commit_interval: int,
              limit: int | None = None) -> SimpleMetricsListener:
    if not os.path.isdir(base_path):
        raise FileNotFoundError(f"BASE_PATH is not a directory: {base_path}")
    cuts = ImageCutsRepository(session)
    processed = ImageProcessingStatusRepository(session, PIPELINE)
    total = await cuts.count_candidates(PIPELINE, DETECTOR_VERSION, corpus_ids)
    rows = await cuts.select_candidates(PIPELINE, DETECTOR_VERSION, corpus_ids, limit=limit)
    print(f"Images to check: {len(rows)} of {total}")

    metrics = SimpleMetricsListener()
    metrics.add("images_selected", len(rows))
    metrics.add("images_remaining", total - len(rows))

    streak = 0
    for i, row in enumerate(rows, start=1):
        try:
            proposal = detect_margin_box(os.path.join(base_path, row.filename), params)
        except FILE_ERRORS as e:  # one bad file never fails the run; it is recorded and counted
            await processed.record_failure(row.id, f"{type(e).__name__}: {e}"[:300])
            metrics.increment("unreadable")
            streak += 1
            if streak >= MAX_CONSECUTIVE_FAILURES:
                await session.commit()  # keep the recorded failures so a re-run does not hit the same files first
                raise RuntimeError(f"{streak} unreadable files in a row; check BASE_PATH and the drive")
        else:
            streak = 0
            if proposal is None:
                await processed.mark_done_by_id(row.id)
                metrics.increment("no_cut")
            elif clips_ocr(proposal.box, await cuts.ocr_regions(row.id, ocr_confidence_min), params.ocr_overlap_max):
                await processed.mark_done_by_id(row.id)
                metrics.increment("clipped_text")
            else:
                await cuts.insert_detected(
                    source_image_id=row.id, source_content_hash=row.content_hash, box=proposal.box,
                    detector=DETECTOR, detector_version=DETECTOR_VERSION, confidence=proposal.confidence,
                    detect_run_id=run_id)
                await processed.mark_done_by_id(row.id)
                metrics.increment("proposed")
        if i % commit_interval == 0:
            await session.commit()
    return metrics


async def _execute(run_id: uuid.UUID, limit: int | None) -> SimpleMetricsListener:
    corpus_ids = load_corpus_ids()
    async with AsyncSessionLocal() as session:
        metrics = await run(session, settings.BASE_PATH, params_from_settings(), settings.OCR.CONFIDENCE_MIN,
                            run_id, corpus_ids, settings.RECUT.COMMIT_INTERVAL, limit=limit)
        await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
        await session.commit()
    return metrics


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, limit: int | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            metrics = await _execute(run_id, limit)
    else:
        async with tracked_run(kind=KIND, trigger=trigger) as new_run_id:
            metrics = await _execute(new_run_id, limit)
    print("Crop detection results:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--limit", type=int, default=None, help="check at most this many images this run")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(limit=args.limit))
