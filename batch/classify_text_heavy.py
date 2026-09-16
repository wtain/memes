"""Computes the text-heavy classifier (see
docs/superpowers/specs/2026-09-15-text-heavy-classifier.md) for images with OCR text and real
dimensions, storing both the raw signals and the final result in image_classifications. Safe to
re-run: only images without a row for CLASSIFIER_NAME are processed."""
import argparse
import asyncio
import uuid
from itertools import groupby

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from batch.utils.text_heavy_classifier import CLASSIFIER_NAME, classify
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.image_classifications import ImageClassificationsRepository
from Storage.db import AsyncSessionLocal

COMMIT_INTERVAL = 500


async def run(session, base_path: str, confidence_min: float, status: str) -> SimpleMetricsListener:
    repo = ImageClassificationsRepository(session)
    rows = await repo.get_candidate_regions(CLASSIFIER_NAME, confidence_min, status=status)

    images = [(image_id, list(group)) for image_id, group in groupby(rows, key=lambda r: r[0])]
    print(f"Images to classify: {len(images)}")

    metrics = SimpleMetricsListener()
    tracker = ProgressTracker(len(images), report_every=100, report_interval_secs=10)

    for i, (image_id, group) in enumerate(images, start=1):
        _, filename, width, height, _, _ = group[0]
        bboxes = [row.bbox for row in group]
        outcome = classify(base_path, filename, width, height, bboxes)
        if outcome is None:
            metrics.increment("unreadable")
            tracker.skip()
            continue
        result, details = outcome
        await repo.set_result(image_id, CLASSIFIER_NAME, result, details)
        metrics.increment(result)
        tracker.mark_done()

        if i % COMMIT_INTERVAL == 0:
            await session.commit()

    tracker.summary()
    return metrics


async def main(status: str = "active", trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    base_path = settings.BASE_PATH
    confidence_min = settings.OCR.CONFIDENCE_MIN

    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, base_path, confidence_min, status)
                await session.commit()
    else:
        async with tracked_run(kind="classify_text_heavy", trigger=trigger):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, base_path, confidence_min, status)
                await session.commit()

    print("Classification results:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--status", choices=["active", "pending"], default="active")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(status=args.status))
