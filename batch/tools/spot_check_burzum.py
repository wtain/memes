"""Spot-check burzum losses — look for varg/vikernes tokens."""
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from rules.concept_tagger import ConceptTagger
from rules.engine import RulesEngine
from rules.normalize import tokenize

engine_new = ConceptTagger.load(Path("batch/data/tagging"), "metal")
engine_old = RulesEngine("batch/data/rules.json", lemmatize=False)


async def main():
    async with AsyncSessionLocal() as session:
        repo = ImagesRepository(session)
        rows = await repo.get_images_and_ocr_texts()

    corpus = [(fn, iid, text) for fn, iid, text, conf in rows if conf >= 0.4]

    lost = []
    for fn, iid, text in corpus:
        old_tags = {f"{n}:{v}" for n, v in engine_old.get_tags_for_ocr_text(text)}
        new_tags = {f"{k}:{v}" for k, v in engine_new.tag(text).tags}
        if "band:burzum" in old_tags and "band:burzum" not in new_tags:
            lost.append((fn, text))

    tc: Counter = Counter()
    for fn, text in lost:
        for token in tokenize(text):
            tl = token.lower()
            if "varg" in tl or "burzum" in tl or "vikernes" in tl:
                tc[tl] += 1

    print(f"Lost: {len(lost)}", flush=True)
    print("Triggering tokens:")
    for tok, cnt in tc.most_common(20):
        print(f"  {tok!r}: {cnt}")

    # Show first 5 lost images text snippet
    print("\nFirst 5 lost:")
    for fn, text in lost[:5]:
        print(f"  {fn}: {text[:100]!r}")


asyncio.run(main())