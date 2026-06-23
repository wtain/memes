import asyncio
import re
import sys

sys.path.insert(0, ".")

from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from rules.concept_tagger import ConceptTagger
from rules.normalize import normalize, make_morph
from pathlib import Path

TARGETS = [
    "Cluster_7a3a8a97664ff1699e13a894e5fffca8_7a3a8a97664ff1699e13a894e5fffca8.jpg",
    "Cluster_FB_IMG_1734009928801_FB_IMG_1734009928801.jpg",
    "3159d4004ec29c5d85f9ed3ebe199f5e.jpg",
]

DATA_DIR = Path(__file__).parent.parent / "data" / "tagging"
morph = make_morph()
engine = ConceptTagger.load(DATA_DIR, "metal")


async def main():
    async with AsyncSessionLocal() as session:
        repo = ImagesRepository(session)
        rows = await repo.get_images_and_ocr_texts()
    for target in TARGETS:
        matches = [(fn, iid, text, conf) for fn, iid, text, conf in rows if fn == target]
        print(f"=== {target} ({len(matches)} rows) ===")
        for fn, iid, text, conf in matches[:5]:
            tokens = re.findall(r"\w+", text)
            lower_tokens = [t.lower() for t in tokens]
            slayer_sub = "slayer" in text.lower()
            lemmas = normalize(text, morph)
            new_tags = {f"{k}:{v}" for k, v in engine.tag(text).tags}
            print(f"  conf={conf:.2f}  old_slayer={slayer_sub}  new_slayer={'band:slayer' in new_tags}")
            print(f"  tokens: {lower_tokens[:15]}")
            print(f"  lemmas: {sorted(lemmas)[:15]}")
        print()


asyncio.run(main())