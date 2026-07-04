import asyncio
import re
import sys

sys.path.insert(0, ".")

from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from rules.concept_tagger import ConceptTagger
from pathlib import Path

TARGET = "Cluster_FB_IMG_1732195804942_FB_IMG_1732195804942.jpg"
DATA_DIR = Path(__file__).parent.parent / "data" / "tagging"

engine = ConceptTagger.load(DATA_DIR, "metal")


async def main():
    async with AsyncSessionLocal() as session:
        repo = ImagesRepository(session)
        rows = await repo.get_images_and_ocr_texts()
    matches = [(fn, iid, text, conf) for fn, iid, text, conf, _ in rows if fn == TARGET]
    for fn, iid, text, conf in matches:
        tokens = re.findall(r"\w+", text)
        lower_tokens = [t.lower() for t in tokens]
        metallica_sub = "metallica" in text.lower()
        result = engine.tag(text)
        new_tags = {f"{k}:{v}" for k, v in result.tags}
        print(f"conf={conf:.2f}  old_match={metallica_sub}  new_metallica={'band:metallica' in new_tags}")
        print(f"  tokens: {lower_tokens[:20]}")
        print()


asyncio.run(main())