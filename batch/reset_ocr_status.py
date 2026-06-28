"""
Reset OCR processing status so images get re-processed on the next pipeline run.

Usage:
    # Reset specific image IDs
    python -m batch.reset_ocr_status <uuid1> <uuid2> ...

    # Reset ALL images (full re-run)
    python -m batch.reset_ocr_status --all
"""
import asyncio
import sys

from sqlalchemy import delete, select

from Storage.db import AsyncSessionLocal
from Storage.models import ImageProcessingStatus

PIPELINE = "easyocr:en"


async def reset(image_ids: list[str] | None):
    async with AsyncSessionLocal() as session:
        if image_ids is None:
            stmt = delete(ImageProcessingStatus).where(
                ImageProcessingStatus.pipeline == PIPELINE
            )
            result = await session.execute(stmt)
            await session.commit()
            print(f"Reset {result.rowcount} entries (all images, pipeline={PIPELINE})")
        else:
            stmt = delete(ImageProcessingStatus).where(
                ImageProcessingStatus.pipeline == PIPELINE,
                ImageProcessingStatus.image_id.in_(image_ids),
            )
            result = await session.execute(stmt)
            await session.commit()
            print(f"Reset {result.rowcount} entries for {len(image_ids)} image(s)")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if args == ["--all"]:
        asyncio.run(reset(None))
    else:
        asyncio.run(reset(args))