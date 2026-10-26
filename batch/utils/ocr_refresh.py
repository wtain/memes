"""Pieces of extract_text_from_memes' --refresh-stale mode (screenshot recut R3c). Kept free of the OCR stack's heavy
imports so they can be tested without a GPU."""
import os
import time

from sqlalchemy import select

from Storage.models import Image, ImageStatus, OCRText
from Storage.stale import images_with_stale_rows

MAX_CONSECUTIVE_UNREADABLE = 20


async def select_refresh_images(session) -> list[Image]:
    """cut_proposed images whose OCR rows are still the stale copies from the source."""
    ids = await images_with_stale_rows(session, OCRText, ImageStatus.CUT_PROPOSED)
    if not ids:
        return []
    result = await session.execute(select(Image).where(Image.id.in_(ids)).order_by(Image.created_at, Image.id))
    return list(result.scalars().all())


async def refresh_producer(path, images, io_queue, metrics_listener, tracker) -> None:
    """Feeds the cut files to the OCR pipeline. An unreadable file leaves its image stale; 20 in a row stop the run."""
    consecutive = 0
    for image in images:
        started = time.perf_counter()
        try:
            with open(os.path.join(path, image.filename), "rb") as handle:
                data = handle.read()
        except OSError as error:
            print(f"Error: {error}")
            metrics_listener.increment("error.reading")
            tracker.skip()
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_UNREADABLE:
                await io_queue.put(None)
                raise RuntimeError(f"{consecutive} unreadable cut files in a row; stopping the OCR refresh")
            continue
        consecutive = 0
        await io_queue.put((image.filename, data, time.perf_counter() - started, image))
    await io_queue.put(None)
