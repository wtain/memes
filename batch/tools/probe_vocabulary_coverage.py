"""Throwaway corpus probe for the P1 vocabulary check (spec section 9). NOT the P2 extractor.

Reads a stratified sample of active images with a READ-ONLY database role, asks a local vision model to
pick vocabulary values, and writes one JSON line per image. Run by the controller session only:

    DATABASE_URL=<DATABASE_URL_READONLY of the environment> PYTHONIOENCODING=utf-8 \
        python -m batch.tools.probe_vocabulary_coverage --base-path <BASE_PATH> --out results.jsonl

then:  python -m taxonomy.coverage results.jsonl
"""
import argparse
import asyncio
import json
import os
import sys

import ollama
from sqlalchemy import exists, func, select

from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageDescription, OCRText
from taxonomy.coverage import assign_from_response
from taxonomy.loader import load
from taxonomy.prompt import vocabulary_prompt

STRATA = [  # (name, has_ocr, has_description, share of --n)
    ("ocr+description", True, True, 0.4),
    ("ocr only", True, False, 0.2),
    ("description only", False, True, 0.2),
    ("neither", False, False, 0.2),
]
OCR_CHAR_LIMIT = 600


def build_prompt(tax, ocr_text: str, description: str) -> str:
    sections = [
        "You label memes for a search engine. Look at the image and the extra text below, then say how this meme",
        "would be used as a REPLY to a message. Pick values ONLY from these lists (use the keys exactly).",
        "",
    ]
    for axis in tax.controlled_axes():
        sections += [f"{axis} (up to {tax.axes[axis].max_values}):", vocabulary_prompt(axis, tax), ""]
    sections += [
        f"Text in the image (OCR): {ocr_text[:OCR_CHAR_LIMIT] or '(none)'}",
        f"Existing description: {description or '(none)'}",
        "",
        'Answer with JSON only: {"emotion": [...], "stance": [...], "topic": [...], "tone": [...], '
        '"suggested": {"emotion": [], "stance": [], "topic": [], "tone": []}}. '
        'Put a short free-text word in "suggested" for an axis only when no listed value fits.',
    ]
    return "\n".join(sections)


async def sample(session, has_ocr: bool, has_description: bool, limit: int):
    ocr = exists().where(OCRText.image_id == Image.id)
    desc = exists().where(ImageDescription.image_id == Image.id)
    stmt = (
        select(Image.id, Image.filename)
        .where(Image.status == "active", ocr if has_ocr else ~ocr, desc if has_description else ~desc)
        .order_by(func.random())
        .limit(limit)
    )
    return (await session.execute(stmt)).all()


async def texts_for(session, image_id):
    ocr = (await session.execute(
        select(OCRText.text).where(OCRText.image_id == image_id).order_by(OCRText.id))).scalars().all()
    desc = (await session.execute(
        select(ImageDescription.text).where(ImageDescription.image_id == image_id).limit(1))).scalars().first()
    return " ".join(ocr), desc or ""


async def run(args) -> None:
    tax = load()
    with open(args.out, "w", encoding="utf-8") as out:
        async with AsyncSessionLocal() as session:
            for name, has_ocr, has_description, share in STRATA:
                rows = await sample(session, has_ocr, has_description, round(args.n * share))
                for image_id, filename in rows:
                    ocr_text, description = await texts_for(session, image_id)
                    path = os.path.join(args.base_path, filename)
                    entry = {"image_id": str(image_id), "stratum": name}
                    try:
                        response = ollama.chat(
                            model=args.model, format="json",
                            messages=[{"role": "user", "images": [path],
                                       "content": build_prompt(tax, ocr_text, description)}],
                            options={"temperature": 0, "num_ctx": 8192},
                        )
                        parsed = assign_from_response(response["message"]["content"], tax)
                        entry.update(parsed if parsed else {"error": "unparseable answer"})
                    except Exception as exc:  # one bad image (e.g. WebP) must not stop the probe
                        entry["error"] = str(exc)[:200]
                    out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    out.flush()
                    print(f"{name}: {filename} {'ok' if 'assigned' in entry else 'FAILED'}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-path", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--model", default="qwen2.5vl:7b")
    args = parser.parse_args(argv)
    if "ocr_readonly" not in os.environ.get("DATABASE_URL", ""):
        print("Refusing to run: DATABASE_URL must be the read-only URL (role ocr_readonly).", file=sys.stderr)
        return 2
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
