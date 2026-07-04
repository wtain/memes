"""
Spot-check token shapes causing band losses in the metal profile.
"""
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

DATA_DIR = Path("batch/data/tagging")
engine_new = ConceptTagger.load(DATA_DIR, "metal")
engine_old = RulesEngine("batch/data/rules.json", lemmatize=False)

TAGS_TO_CHECK = {
    "band:burzum":   "burzum",
    "band:gojira":   "gojira",
    "band:gwar":     "gwar",
    "band:korn":     "korn",
    "band:deicide":  "deicide",
    "band:opeth":    "opeth",
    "band:ac/dc":    "ac",
    "animal:cat":    "cat",
    "animal:dog":    "dog",
    "animal:bear":   "bear",
}


async def main():
    async with AsyncSessionLocal() as session:
        repo = ImagesRepository(session)
        rows = await repo.get_images_and_ocr_texts()

    corpus = [(fn, iid, text) for fn, iid, text, conf, _ in rows if conf >= 0.4]
    print(f"Corpus: {len(corpus)}", flush=True)

    for tag, root in TAGS_TO_CHECK.items():
        lost = []
        for fn, iid, text in corpus:
            old_tags = {f"{n}:{v}" for n, v in engine_old.get_tags_for_ocr_text(text)}
            new_tags = {f"{k}:{v}" for k, v in engine_new.tag(text).tags}
            if tag in old_tags and tag not in new_tags:
                lost.append((fn, text))

        token_counts: Counter = Counter()
        for fn, text in lost:
            for token in tokenize(text):
                if root.lower() in token.lower():
                    token_counts[token.lower()] += 1

        print(f"\n--- {tag}: {len(lost)} lost ---")
        for tok, cnt in token_counts.most_common(12):
            print(f"    {tok!r}: {cnt}")


asyncio.run(main())