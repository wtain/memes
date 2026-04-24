import os
import asyncio
from collections import defaultdict
from datetime import datetime

import cv2
import numpy
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from easyocr import easyocr

import time

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

# split models in exports into models and db
# or create a module and import it?
from batch.models.external import AsyncSessionLocal
from batch.models.external import Image, OCRText, ImageMetrics, ImageProcessingStatus


PIPELINE = "easyocr:en"


class ImageProcessingStatusRepository:

    def __init__(self, session, pipeline):
        self.session = session
        self.pipeline = pipeline

    async def mark_started(self, image):
        status = await self.get_image_status(image.id)
        if status is None:
            status = ImageProcessingStatus(image=image, pipeline=self.pipeline, status="processing",
                                           started_at=datetime.utcnow())
        self.session.add(
            status
        )
        await self.session.commit()

    async def get_image_status(self, image_id):
        existing = await self.session.get(
            ImageProcessingStatus,
            {"image_id": image_id, "pipeline": self.pipeline}
        )
        return existing

    async def mark_done(self, image):
        status = await self.session.get(
            ImageProcessingStatus,
            {"image_id": image.id, "pipeline": self.pipeline}
        )
        if status is None:
            status = ImageProcessingStatus(image=image, pipeline=self.pipeline)
        status.status = "done"
        status.finished_at = datetime.utcnow()
        await self.session.commit()

    async def mark_failed(self, image, error):
        status = await self.session.get(
            ImageProcessingStatus,
            {"image_id": image.id, "pipeline": self.pipeline}
        )
        if status is None:
            status = ImageProcessingStatus(image=image, pipeline=self.pipeline)
        status.status = "failed"
        status.error_message = str(error)
        status.finished_at = datetime.utcnow()
        await self.session.commit()

    async def try_claim_image(self, image_id: str) -> bool:
        existing = await self.get_image_status(image_id)

        if existing:
            if existing.status == "done":
                return False  # already processed
            if existing.status == "processing":
                return False  # in-progress elsewhere
        return True


class ImageMetricsRepository:

    def __init__(self, session):
        self.session = session

    async def overwrite_metrics(self, image, metrics):
        await self.session.execute(
            delete(ImageMetrics).where(
                ImageMetrics.image_id == image.id
            )
        )

        self.session.add(
            ImageMetrics(
                image_id=image.id,
                **metrics
            )
        )


class OCRTextRepository:

    def __init__(self, session):
        self.session = session

    async def overwrite_texts(self, image, ocr_result, language):
        await self.session.execute(
            delete(ImageMetrics).where(
                OCRText.image_id == image.id
            )
        )

        for bbox, text, confidence in ocr_result:
            # todo: threshold confidence
            # todo: create session once
            self.session.add(
                OCRText(
                    image_id=image.id,
                    text=text,
                    confidence=float(confidence),
                    bbox=[[v.item() if isinstance(v, numpy.int32) else v for v in p] for p in bbox],
                    language=language,
                )
            )

class ImageRepository:

    def __init__(self, session):
        self.session = session

    async def find_image_by_filename(
        self,
        filename: str,
    ) -> Image | None:
        result = await self.session.execute(
            select(Image).where(Image.filename == filename)
        )
        return result.scalar_one_or_none()

    async def register_image(self, file):
        image = Image(
            filename=file
        )
        self.session.add(image)
        await self.session.flush()  # image.id available
        return image


async def io_producer(path, io_queue, pipeline):
    async with AsyncSessionLocal() as session:
        status_repo = ImageProcessingStatusRepository(session, pipeline)
        image_repo = ImageRepository(session)
        for file in os.listdir(path):
            if file.lower().endswith(".mp4"):
                # todo: metric: skipped
                continue

            image = await image_repo.find_image_by_filename(file)
            if image and await status_repo.try_claim_image(image.id):
                continue

            if image is None:
                image = await image_repo.register_image(file)

            await status_repo.mark_started(image)

            full_path = os.path.join(path, file)

            t0 = time.perf_counter()
            try:
                with open(full_path, "rb") as f:
                    data = f.read()
            except Exception as e:
                print(f"Error: {e}")
                continue
            t_read = time.perf_counter() - t0

            await io_queue.put((file, data, t_read, image))

        await io_queue.put(None)


async def cpu_worker(io_queue, cpu_queue, executor):
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

                h, w = img.shape[:2]
                # if max(h, w) > 1600:
                #     scale = 1600 / max(h, w)
                #     img = cv2.resize(
                #         img,
                #         (int(w * scale), int(h * scale)),
                #         interpolation=cv2.INTER_AREA
                #     )
            except cv2.error as e:
                print(e)
                img = None
                # todo: mark as error

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


async def gpu_consumer(queue, metrics, pipeline):
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

            metrics["read_time_ms"].append(read_t)
            metrics["preprocess_time_ms"].append(prep_t)
            metrics["ocr_time_ms"].append(t_ocr)
            metrics["total_time_ms"].append(t_total)
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

            h, w = img.shape[:2]
            await persist_ocr_result(result, {
                "read_time_ms": read_t,
                "preprocess_time_ms": prep_t,
                "ocr_time_ms": t_ocr,
                "total_time_ms": t_total
            }, image, pipeline, language)


async def main(path: str):
    io_queue = asyncio.Queue(maxsize=50)
    cpu_queue = asyncio.Queue(maxsize=20)

    cpu_executor = ThreadPoolExecutor(max_workers=4)

    metrics = defaultdict(list)

    await asyncio.gather(
        io_producer(path, io_queue, PIPELINE),
        cpu_worker(io_queue, cpu_queue, cpu_executor),
        gpu_consumer(cpu_queue, metrics, PIPELINE)
    )

"""
1. part of text for identification of the possibility, and rest - for the confirmation
2. image lists
3. tags (k=v)
"""

if __name__ == "__main__":
    source_path = "c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes"
    asyncio.run(main(source_path))
