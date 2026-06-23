"""Spot-check тема:мем losses in general profile."""
import asyncio
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from rules.concept_tagger import ConceptTagger
from rules.engine import RulesEngine
from rules.normalize import normalize, make_morph

DATA_DIR = Path(__file__).parent.parent / "data" / "tagging"
morph = make_morph()
engine_new = ConceptTagger.load(DATA_DIR, "general")
engine_old = RulesEngine("batch/data/rules.general.json", lemmatize=False)

TAG = "тема:мем"


async def main():
    async with AsyncSessionLocal() as session:
        repo = ImagesRepository(session)
        rows = await repo.get_images_and_ocr_texts()

    corpus = [(fn, iid, text) for fn, iid, text, conf in rows if conf >= 0.4]
    print(f"Corpus: {len(corpus)}", flush=True)

    lost = []
    for fn, iid, text in corpus:
        old_tags = {f"{n}:{v}" for n, v in engine_old.get_tags_for_ocr_text(text)}
        new_tags = {f"{k}:{v}" for k, v in engine_new.tag(text).tags}
        if TAG in old_tags and TAG not in new_tags:
            lost.append((fn, text))

    print(f"Lost for {TAG}: {len(lost)}", flush=True)

    token_counts: Counter = Counter()
    for fn, text in lost:
        tl = text.lower()
        for token in re.findall(r"[^\W_]+", tl, re.UNICODE):
            if "мем" in token or "meme" in token:
                token_counts[token] += 1

    print("Top triggering token shapes (what made old engine match):")
    for tok, cnt in token_counts.most_common(30):
        print(f"  {tok!r}: {cnt}")

    # Sample 5 lost images
    print("\nFirst 5 lost images:")
    for fn, text in lost[:5]:
        lemmas = normalize(text, morph)
        mem_lemmas = [l for l in lemmas if "мем" in l or "meme" in l]
        print(f"  {fn}")
        print(f"    mem-lemmas={mem_lemmas}")
        print(f"    text={text[:120]!r}")


asyncio.run(main())