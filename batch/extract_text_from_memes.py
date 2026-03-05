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


async def find_image_by_filename(
    session: AsyncSession,
    filename: str,
) -> Image | None:
    result = await session.execute(
        select(Image).where(Image.filename == filename)
    )
    return result.scalar_one_or_none()


async def try_claim_image(session, image_id: str, pipeline) -> bool:
    existing = await get_image_status(image_id, session, pipeline)

    if existing:
        if existing.status == "done":
            return False  # already processed
        if existing.status == "processing":
            return False  # in-progress elsewhere
    return True


async def mark_started(image, pipeline, session):
    status = await get_image_status(image.id, session, pipeline)
    if status is None:
        status = ImageProcessingStatus(image=image, pipeline=pipeline, status="processing",
                                       started_at=datetime.utcnow())
    session.add(
        status
    )
    await session.commit()


async def get_image_status(image_id, session, pipeline):
    existing = await session.get(
        ImageProcessingStatus,
        {"image_id": image_id, "pipeline": pipeline}
    )
    return existing


async def mark_done(session, image, pipeline):
    status = await session.get(
        ImageProcessingStatus,
        {"image_id": image.id, "pipeline": pipeline}
    )
    if status is None:
        status = ImageProcessingStatus(image=image, pipeline=pipeline)
    status.status = "done"
    status.finished_at = datetime.utcnow()
    await session.commit()


async def mark_failed(session, image, error, pipeline):
    status = await session.get(
        ImageProcessingStatus,
        {"image_id": image.id, "pipeline": pipeline}
    )
    if status is None:
        status = ImageProcessingStatus(image=image, pipeline=pipeline)
    status.status = "failed"
    status.error_message = str(error)
    status.finished_at = datetime.utcnow()
    await session.commit()



def load_and_decode_image(
    path: str,
    max_side: int = 1600
):
    t0 = time.perf_counter()

    with open(path, "rb") as f:
        data = f.read()

    t_read = time.perf_counter()

    arr = np.frombuffer(data, np.uint8)
    # todo: decode on CPU in thread pool?
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    if img is None:
        raise ValueError("Failed to decode image")

    h, w = img.shape[:2]
    max_dim = max(h, w)

    if max_dim > max_side:
        scale = max_side / max_dim
        new_w = int(w * scale)
        new_h = int(h * scale)

        img = cv2.resize(
            img,
            (new_w, new_h),
            interpolation=cv2.INTER_AREA
        )

    t_done = time.perf_counter()

    return img, t_read - t0, t_done - t_read


async def io_producer(path, io_queue, pipeline):
    async with AsyncSessionLocal() as session:
        for file in os.listdir(path):
            if file.lower().endswith(".mp4"):
                # todo: metric: skipped
                continue

            image = await find_image_by_filename(session, file)
            if image and await try_claim_image(session, image.id, pipeline):
                continue

            if image is None:
                image = Image(
                    filename=file
                )
                session.add(image)
                await session.flush()  # image.id available

            await mark_started(image, pipeline, session)

            full_path = os.path.join(path, file)

            t0 = time.perf_counter()
            with open(full_path, "rb") as f:
                data = f.read()
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
                if max(h, w) > 1600:
                    scale = 1600 / max(h, w)
                    img = cv2.resize(
                        img,
                        (int(w * scale), int(h * scale)),
                        interpolation=cv2.INTER_AREA
                    )
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
    filename: str,
    image_size: tuple[int, int],
    ocr_result: list,
    metrics: dict,
    image: Image,
    pipeline):
    async with AsyncSessionLocal() as session:
        for bbox, text, confidence in ocr_result:
            # todo: threshold confidence
            # todo: create session once
            session.add(
                OCRText(
                    image_id=image.id,
                    text=text,
                    confidence=float(confidence),
                    bbox=[[v.item() if isinstance(v, numpy.int32) else v for v in p] for p in bbox]
                )
            )

        await mark_done(session, image, pipeline)

        # todo: also delete texts
        await session.execute(
            delete(ImageMetrics).where(
                ImageMetrics.image_id == image.id
            )
        )

        session.add(
            ImageMetrics(
                image_id=image.id,
                **metrics
            )
        )

        # todo: batch database queries
        await session.commit()


async def gpu_consumer(queue, metrics, pipeline):
    # reader = easyocr.Reader(['en'], gpu=True)
    # reader = easyocr.Reader(['en', 'ru'], gpu=True)
    reader_ru = easyocr.Reader(['ru'], gpu=True)
    reader = easyocr.Reader(['en'], gpu=True)

    while True:
        item = await queue.get()
        if item is None:
            break

        file, img, read_t, prep_t, image = item

        t0 = time.perf_counter()
        result_en = reader.readtext(img)
        result_ru = reader_ru.readtext(img)
        t_ocr = time.perf_counter() - t0
        t_total = read_t + prep_t + t_ocr

        result = result_en + result_ru

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
        await persist_ocr_result(file, (w, h), result, {
            "read_time_ms": read_t,
            "preprocess_time_ms": prep_t,
            "ocr_time_ms": t_ocr,
            "total_time_ms": t_total
        }, image, pipeline)


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
