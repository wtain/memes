"""
OCR: reads each image in BASE_PATH through a pipeline of EasyOCR (with preprocessing variants and
Tesseract/TrOCR fallbacks) and stores the text in ocr_texts, committing in batches. Files not yet registered are
auto-registered only in the default active mode. --status {pending,active,all} selects the images (ingestion runs it
with pending). Run-tracked (kind extract_text_from_memes); admin-triggerable, manual only. For a full re-run use
batch.reset_ocr_status first; never clear ocr_texts. --refresh-stale re-runs OCR on recut cuts (cut_proposed) that
still carry the source's stale OCR rows, reading them from the database instead of walking the directory, and also
clears the cut's text-heavy classification and OCR-text embedding (computed from the old text).
"""
import argparse
import os
import sys
import asyncio

# Windows consoles default to a codepage (e.g. cp1252) that can't encode Cyrillic/etc. OCR
# results -- reconfigure stdout to UTF-8 so a print() of non-Latin OCR text doesn't crash the
# whole run partway through. No-op on platforms where stdout is already UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from easyocr import easyocr

import time
import uuid

from batch.ocr_preprocess import generate_variants, merge_results
from batch.trocr_fallback import TrOCRFallback
from batch.tesseract_reader import TesseractReader, is_available as tesseract_available
from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from batch.utils.batch_commit import BatchCommitter
from batch.utils.ocr_refresh import refresh_producer, select_refresh_images
from batch.utils.progress import ProgressTracker
from batch.utils.refresh_stale import refresh_stats
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import ImagesRepository

PIPELINE = "easyocr:en"


async def io_producer(path, io_queue, pipeline, metrics_listener, tracker: ProgressTracker, target_status: str = "active"):
    async with AsyncSessionLocal() as session:
        status_repo = ImageProcessingStatusRepository(session, pipeline)
        image_repo = ImagesRepository(session)
        for file in os.listdir(path):
            # Hidden files (e.g. a leftover .comics-build-* temp file) are never memes.
            if file.startswith("."):
                metrics_listener.increment("skipped.hidden")
                continue
            fullFilePath = os.path.join(path, file)
            if os.path.isdir(fullFilePath):
                metrics_listener.increment("skipped.directory")
                continue
            if file.lower().endswith(".mp4"):
                metrics_listener.increment("skipped.file")
                continue

            image = await image_repo.find_image_by_filename(file)

            if image is None:
                # Auto-registering unregistered files only makes sense for the default
                # "active" scope (a file dropped directly into BASE_PATH outside the
                # ingestion pipeline). Ingestion's own --status pending pre-pass runs
                # against already-registered pending images only -- see
                # docs/superpowers/specs/2026-07-25-image-visibility-status-design.md.
                if target_status not in ("active", "all"):
                    metrics_listener.increment("skipped.not_registered")
                    continue
                image = await image_repo.register_image(file)
                metrics_listener.increment("new.registered")
            elif target_status != "all" and image.status != target_status:
                metrics_listener.increment("skipped.status_mismatch")
                continue

            if not await status_repo.should_process(image.id):
                metrics_listener.increment("skipped.existing")
                continue

            await status_repo.mark_started(image)

            full_path = os.path.join(path, file)

            t0 = time.perf_counter()
            try:
                with open(full_path, "rb") as f:
                    data = f.read()
            except Exception as e:
                print(f"Error: {e}")
                metrics_listener.increment("error.reading")
                tracker.skip()
                continue
            t_read = time.perf_counter() - t0

            await io_queue.put((file, data, t_read, image))

        await io_queue.put(None)


async def cpu_worker(io_queue, cpu_queue, executor, metrics_listener):
    loop = asyncio.get_running_loop()

    while True:
        item = await io_queue.get()
        if item is None:
            await cpu_queue.put(None)
            break

        file, data, read_t, image = item

        def decode_and_resize():
            t0 = time.perf_counter()

            try:
                arr = np.frombuffer(data, np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

            except cv2.error as e:
                print(e)
                img = None
                metrics_listener.increment("error.processing")

            return img, time.perf_counter() - t0

        img, prep_t = await loop.run_in_executor(executor, decode_and_resize)

        if img is not None:
            await cpu_queue.put((file, img, read_t, prep_t, image))


async def gpu_consumer(
    queue,
    pipeline,
    metrics_listener,
    committer: BatchCommitter,
    tracker: ProgressTracker,
    refresh: bool = False,
):
    en_reader = easyocr.Reader(['en'], gpu=True)
    es_reader = easyocr.Reader(['es'], gpu=True)

    if tesseract_available():
        print("Tesseract available — using detect(EasyOCR ru+en) + recognize(Tesseract) for Russian.")
        ru_easyocr = easyocr.Reader(['ru'], gpu=True)
        ru_reader = TesseractReader(lang="rus", ru_detector=ru_easyocr, en_detector=en_reader)
    else:
        print("Tesseract not found — falling back to EasyOCR ru (poor on Impact Cyrillic).")
        print("Install: winget install --id UB-Mannheim.TesseractOCR --override \"/S /LANG=Russian\"")
        ru_reader = easyocr.Reader(['ru'], gpu=True)

    readers = {
        "ru": ru_reader,
        "en": en_reader,
        "es": es_reader,
    }

    trocr: TrOCRFallback | None = None
    try:
        trocr = TrOCRFallback(device="cuda")
        print("TrOCR fallback loaded.")
    except Exception as e:
        print(f"TrOCR unavailable ({e}), skipping fallback for stylized fonts.")

    while True:
        item = await queue.get()
        if item is None:
            break

        file, img, read_t, prep_t, image = item
        variants = generate_variants(img)

        total_ocr_t = 0.0
        for language, reader in readers.items():

            t0 = time.perf_counter()

            if isinstance(reader, TesseractReader):
                merged = reader.readtext(img)
            else:
                variant_results = []
                for _, variant_img in variants:
                    result = reader.readtext(variant_img)
                    variant_results.append(result)
                merged = merge_results(variant_results)

            if language == "en" and trocr is not None:
                try:
                    merged = trocr.rerecognize(img, merged)
                except Exception as e:
                    print(f"TrOCR rerecognize failed for {file}: {e}")

            t_ocr = time.perf_counter() - t0
            total_ocr_t += t_ocr

            print(
                f"{file} [{language}]: "
                f"read={read_t:.3f}s "
                f"prep={prep_t:.3f}s "
                f"ocr={t_ocr:.3f}s "
                f"total={read_t + prep_t + t_ocr:.3f}s"
            )

            print(f"\n=== {file} [{language}] ===")
            for _, text, confidence in merged:
                print(f"  {text!r} ({confidence:.2f})")

            metrics_listener.increment("saved")
            await committer.add_language_result(image, language, merged)

        timings = {
            "read_time_ms": read_t,
            "preprocess_time_ms": prep_t,
            "ocr_time_ms": total_ocr_t,
            "total_time_ms": read_t + prep_t + total_ocr_t,
        }
        if refresh:
            await committer.on_refresh_done(image, timings)
            metrics_listener.increment("refreshed")
        else:
            await committer.on_image_done(image, timings)
        tracker.mark_done()


async def run(path: str, batch_size: int = 100, progress_every: int = 10, target_status: str = "active",
              refresh_stale: bool = False) -> dict:
    # Seed the tracker with the number of images that actually need OCR for this run's
    # --status scope, not a raw file count of BASE_PATH (which spans every status and
    # every already-processed image -- see the progress denominator being ~4x the real
    # work in --status pending runs).
    refresh_images = []
    async with AsyncSessionLocal() as session:
        if refresh_stale:
            refresh_images = await select_refresh_images(session)
            total = len(refresh_images)
        else:
            status_repo = ImageProcessingStatusRepository(session, PIPELINE)
            total = await status_repo.count_unprocessed(
                None if target_status == "all" else target_status
            )
    if refresh_stale and not refresh_images:
        # Nothing stale: do not pay for loading the OCR models (EasyOCR x3, Tesseract, TrOCR on CUDA).
        print("No cut has stale OCR; nothing to refresh")
        return refresh_stats(selected=0, refreshed=0)
    tracker = ProgressTracker(total=total, report_every=progress_every)

    io_queue = asyncio.Queue(maxsize=200)
    cpu_queue = asyncio.Queue(maxsize=20)
    cpu_executor = ThreadPoolExecutor(max_workers=16)
    metrics_listener = SimpleMetricsListener()

    stopped: list[Exception] = []

    async def guarded_refresh_producer():
        # refresh_producer ends the queue before it raises, so the worker and consumer run to their end; the error is
        # raised only after they finished and the images refreshed so far were committed (gather would otherwise
        # re-raise at once and leave them running against a rolled-back session).
        try:
            await refresh_producer(path, refresh_images, io_queue, metrics_listener, tracker)
        except RuntimeError as error:
            stopped.append(error)

    async with AsyncSessionLocal() as session:
        committer = BatchCommitter(session, batch_size=batch_size, pipeline=PIPELINE)
        try:
            producer = (
                guarded_refresh_producer() if refresh_stale
                else io_producer(path, io_queue, PIPELINE, metrics_listener, tracker, target_status)
            )
            await asyncio.gather(
                producer,
                cpu_worker(io_queue, cpu_queue, cpu_executor, metrics_listener),
                gpu_consumer(cpu_queue, PIPELINE, metrics_listener, committer, tracker, refresh=refresh_stale),
            )
            await committer.close()
        except Exception:
            await session.rollback()
            raise
    if stopped:
        raise stopped[0]

    tracker.summary()
    metrics_listener.print()
    if not refresh_stale:
        return {}
    refreshed = metrics_listener.counters_dict().get("refreshed", 0)
    return {**refresh_stats(selected=len(refresh_images), refreshed=refreshed,
                            failed=len(refresh_images) - refreshed)}


async def _process(path: str, target_status: str, refresh_stale: bool = False) -> dict:
    batch_size = settings.GENERAL.BATCH_SIZE
    progress_every = settings.GENERAL.PROGRESS_EVERY
    return await run(path, batch_size=batch_size, progress_every=progress_every, target_status=target_status,
                     refresh_stale=refresh_stale)


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None,
               path: str | None = None, target_status: str = "active", refresh_stale: bool = False) -> None:
    # path defaults to settings.BASE_PATH so the wrapper (scheduler/admin), which passes no
    # arguments beyond trigger/run_id, scans the same directory the CLI does.
    resolved_path = path if path is not None else settings.BASE_PATH
    if run_id is not None:
        async with finish_existing_run(run_id):
            stats = await _process(resolved_path, target_status, refresh_stale)
            if refresh_stale and stats:
                await record_stats(run_id, stats)
    else:
        async with tracked_run(kind="extract_text_from_memes", trigger=trigger) as new_run_id:
            stats = await _process(resolved_path, target_status, refresh_stale)
            if refresh_stale and stats:
                await record_stats(new_run_id, stats)


"""
1. part of text for identification of the possibility, and rest - for the confirmation
2. image lists
3. tags (k=v)
"""

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--status", choices=["pending", "active", "all"], default="active",
                        help="Only OCR images with this registration status (default: active). "
                             "'pending' is for the ingestion pipeline's pre-review OCR pass; "
                             "unregistered files are only auto-registered in the default 'active' mode.")
    parser.add_argument("--refresh-stale", action="store_true",
                        help="Re-run OCR on the cut_proposed images (recut) that still carry the stale OCR copied "
                             "from their source; ignores --status")
    args = parser.parse_args()
    load_env(args.env)
    source_path = settings.BASE_PATH
    print(f"Base path: {source_path}")
    asyncio.run(main(path=source_path, target_status=args.status, refresh_stale=args.refresh_stale))
