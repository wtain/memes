"""
Usage: python batch/tools/spot_check_band.py <band_name>
Shows OCR rows for the first few images where old engine matched but new doesn't.
"""
import asyncio
import re
import sys

sys.path.insert(0, ".")

from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from rules.concept_tagger import ConceptTagger
from rules.engine import RulesEngine
from rules.normalize import normalize, make_morph
from pathlib import Path

BAND = sys.argv[1] if len(sys.argv) > 1 else "slayer"
DATA_DIR = Path(__file__).parent.parent / "data" / "tagging"
morph = make_morph()
engine_new = ConceptTagger.load(DATA_DIR, "metal")
engine_old = RulesEngine("batch/data/rules.json", lemmatize=False)

TAG = f"band:{BAND}"


async def main():
    async with AsyncSessionLocal() as session:
        repo = ImagesRepository(session)
        rows = await repo.get_images_and_ocr_texts()

    corpus = [(fn, iid, text) for fn, iid, text, conf in rows if conf >= 0.4]

    lost_images: dict[str, list[str]] = {}
    for fn, iid, text in corpus:
        old_tags = {f"{n}:{v}" for n, v in engine_old.get_tags_for_ocr_text(text)}
        new_tags = {f"{k}:{v}" for k, v in engine_new.tag(text).tags}
        if TAG in old_tags and TAG not in new_tags:
            lost_images.setdefault(fn, []).append(text)

    print(f"Lost images for {TAG}: {len(lost_images)}")
    for fn, texts in list(lost_images.items())[:5]:
        print(f"\n=== {fn} ===")
        for text in texts[:4]:
            tokens = re.findall(r"\w+", text)
            lower_tokens = [t.lower() for t in tokens]
            sub = BAND in text.lower()
            lemmas = normalize(text, morph)
            print(f"  old_match={sub}  tokens={lower_tokens[:10]}  lemmas={sorted(lemmas)[:8]}")


asyncio.run(main())