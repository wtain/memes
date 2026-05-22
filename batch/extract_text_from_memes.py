import os
import asyncio

import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from easyocr import easyocr

import time

from metrics.listener import SimpleMetricsListener
# split models in exports into models and db
# or create a module and import it?
from Storage.db import AsyncSessionLocal
from Storage.models import Image
from repository.image_metrics import ImageMetricsRepository
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import ImagesRepository
from repository.ocr_text import OCRTextRepository

PIPELINE = "easyocr:en"


async def io_producer(path, io_queue, pipeline, metrics_listener):
    async with AsyncSessionLocal() as session:
        status_repo = ImageProcessingStatusRepository(session, pipeline)
        image_repo = ImagesRepository(session)
        for file in os.listdir(path):
            if os.path.isdir(file):
                metrics_listener.increment("skipped.directory")
                continue
            if file.lower().endswith(".mp4"):
                metrics_listener.increment("skipped.file")
                continue

            image = await image_repo.find_image_by_filename(file)
            if image and not await status_repo.should_process(image.id):
                metrics_listener.increment("skipped.existing")
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
        else:
            # await mark_failed(session)
            pass



async def persist_ocr_result(
        ocr_result: list,
        metrics: dict,
        image: Image,
        pipeline,
        language: str):
    async with AsyncSessionLocal() as session:
        status_repo = ImageProcessingStatusRepository(session, pipeline)
        metrics_repo = ImageMetricsRepository(session)
        ocr_repo = OCRTextRepository(session)

        await ocr_repo.overwrite_texts(image, ocr_result, language)
        await status_repo.mark_done(image)
        await metrics_repo.overwrite_metrics(image, metrics)

        # todo: batch database queries
        await session.commit()


async def gpu_consumer(queue, pipeline, metrics_listener):
    readers = {
        "ru": easyocr.Reader(['ru'], gpu=True),
        "en": easyocr.Reader(['en'], gpu=True),
        "es": easyocr.Reader(['es'], gpu=True)
    }

    while True:
        item = await queue.get()
        if item is None:
            break

        file, img, read_t, prep_t, image = item

        for language, reader in readers.items():

            t0 = time.perf_counter()
            result = reader.readtext(img)
            t_ocr = time.perf_counter() - t0
            t_total = read_t + prep_t + t_ocr
            # todo: error metrics
            # todo: store to database

            print(
                f"{file}: "
                f"read={read_t:.3f}s "
                f"prep={prep_t:.3f}s "
                f"ocr={t_ocr:.3f}s "
                f"total={t_total :.3f}s"
            )

            print(f"\n=== {file} ===")
            for bbox, text, confidence in result:
                print(f"{text} ({confidence})")

            metrics_listener.increment("saved")
            await persist_ocr_result(result, {
                "read_time_ms": read_t,
                "preprocess_time_ms": prep_t,
                "ocr_time_ms": t_ocr,
                "total_time_ms": t_total
            }, image, pipeline, language)


async def main(path: str):
    io_queue = asyncio.Queue(maxsize=200)
    cpu_queue = asyncio.Queue(maxsize=20)

    cpu_executor = ThreadPoolExecutor(max_workers=16)
    metrics_listener = SimpleMetricsListener()

    await asyncio.gather(
        io_producer(path, io_queue, PIPELINE, metrics_listener),
        cpu_worker(io_queue, cpu_queue, cpu_executor, metrics_listener),
        gpu_consumer(cpu_queue, PIPELINE, metrics_listener)
    )

    metrics_listener.print()

"""
1. part of text for identification of the possibility, and rest - for the confirmation
2. image lists
3. tags (k=v)
"""

if __name__ == "__main__":
    source_path = os.getenv('BASE_PATH')
    print(f"Base path: {source_path}")
    asyncio.run(main(source_path))
