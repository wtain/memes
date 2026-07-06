import argparse
import os
import asyncio

import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from easyocr import easyocr

import time

from batch.ocr_preprocess import generate_variants, merge_results
from batch.trocr_fallback import TrOCRFallback
from batch.tesseract_reader import TesseractReader, is_available as tesseract_available
from batch.utils.batch_commit import BatchCommitter
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import ImagesRepository

PIPELINE = "easyocr:en"


async def io_producer(path, io_queue, pipeline, metrics_listener, tracker: ProgressTracker):
    async with AsyncSessionLocal() as session:
        status_repo = ImageProcessingStatusRepository(session, pipeline)
        image_repo = ImagesRepository(session)
        for file in os.listdir(path):
            fullFilePath = os.path.join(path, file)
            if os.path.isdir(fullFilePath):
                metrics_listener.increment("skipped.directory")
                tracker.skip()
                continue
            if file.lower().endswith(".mp4"):
                metrics_listener.increment("skipped.file")
                tracker.skip()
                continue

            image = await image_repo.find_image_by_filename(file)
            if image and not await status_repo.should_process(image.id):
                metrics_listener.increment("skipped.existing")
                tracker.skip()
                continue

            if image is None:
                image = await image_repo.register_image(file)
                metrics_listener.increment("new.registered")

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

        await committer.on_image_done(image, {
            "read_time_ms": read_t,
            "preprocess_time_ms": prep_t,
            "ocr_time_ms": total_ocr_t,
            "total_time_ms": read_t + prep_t + total_ocr_t,
        })
        tracker.mark_done()


async def run(path: str, batch_size: int = 100, progress_every: int = 10) -> None:
    total = len([f for f in os.listdir(path) if not os.path.isdir(os.path.join(path, f))])
    tracker = ProgressTracker(total=total, report_every=progress_every)

    io_queue = asyncio.Queue(maxsize=200)
    cpu_queue = asyncio.Queue(maxsize=20)
    cpu_executor = ThreadPoolExecutor(max_workers=16)
    metrics_listener = SimpleMetricsListener()

    async with AsyncSessionLocal() as session:
        committer = BatchCommitter(session, batch_size=batch_size, pipeline=PIPELINE)
        try:
            await asyncio.gather(
                io_producer(path, io_queue, PIPELINE, metrics_listener, tracker),
                cpu_worker(io_queue, cpu_queue, cpu_executor, metrics_listener),
                gpu_consumer(cpu_queue, PIPELINE, metrics_listener, committer, tracker),
            )
            await committer.close()
        except Exception:
            await session.rollback()
            raise

    tracker.summary()
    metrics_listener.print()


async def main(path: str) -> None:
    batch_size = settings.BATCH_SIZE
    progress_every = settings.PROGRESS_EVERY
    await run(path, batch_size=batch_size, progress_every=progress_every)


"""
1. part of text for identification of the possibility, and rest - for the confirmation
2. image lists
3. tags (k=v)
"""

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    source_path = settings.BASE_PATH
    print(f"Base path: {source_path}")
    asyncio.run(main(source_path))
