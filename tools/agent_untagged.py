"""
Fetch a batch of untagged images with OCR text and descriptions for agent reasoning.

Usage:
    python tools/agent_untagged.py --env metal [--limit 50]

Output: JSON array to stdout. Stateless — always returns the next N untagged images
as of the current DB state.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _load_env(env: str):
    root = Path(__file__).parent.parent
    env_file = root / "environments" / f".env.{env}"
    if not env_file.exists():
        sys.exit(f"Env file not found: {env_file}")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


async def main(env: str, limit: int):
    _load_env(env)

    from sqlalchemy import select, func, exists
    from sqlalchemy.orm import aliased
    from Storage.db import AsyncSessionLocal
    from Storage.models import Image, OCRText, ImageDescription, ImageTag, ImageExtras

    img = aliased(Image)
    extras = aliased(ImageExtras)

    async with AsyncSessionLocal() as session:
        # Images with no tags
        untagged_q = (
            select(img.id, img.filename)
            .outerjoin(extras, extras.image_id == img.id)
            .where(~exists(select(ImageTag.id).where(ImageTag.image_id == img.id)))
            .order_by(img.created_at.desc())
            .limit(limit)
        )
        image_rows = (await session.execute(untagged_q)).all()
        if not image_rows:
            print(json.dumps([]))
            return

        image_ids = [r.id for r in image_rows]

        # OCR text
        ocr_rows = await session.execute(
            select(OCRText.image_id, func.string_agg(OCRText.text, " ").label("ocr"))
            .where(OCRText.image_id.in_(image_ids))
            .group_by(OCRText.image_id)
        )
        ocr_map = {str(r.image_id): r.ocr for r in ocr_rows}

        # Descriptions
        desc_rows = await session.execute(
            select(ImageDescription.image_id, ImageDescription.text)
            .where(ImageDescription.image_id.in_(image_ids))
        )
        desc_map = {str(r.image_id): r.text for r in desc_rows}

    result = []
    for r in image_rows:
        mid = str(r.id)
        result.append({
            "id": mid,
            "filename": r.filename,
            "ocr_text": ocr_map.get(mid, ""),
            "description": desc_map.get(mid, ""),
        })

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="metal", choices=["metal", "general", "it"])
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    asyncio.run(main(args.env, args.limit))