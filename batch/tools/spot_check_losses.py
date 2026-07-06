"""
Spot-check token shapes causing losses for a set of tags.
Usage: python batch/tools/spot_check_losses.py
"""
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from rules.concept_tagger import ConceptTagger
from rules.engine import RulesEngine
from rules.normalize import tokenize

load_env()

TAGS_TO_CHECK = [
    "тема:мать",
    "тема:кринж",
    "тема:цирк",
    "тема:пацан",
    "тема:волки",
]

# Pre-existing inconsistency, not fixed by this migration: PROFILE differs from
# TAGGING_PROFILE used everywhere else; no environment sets PROFILE, so this
# always resolves to "general" regardless of APP_ENV (see ADR 2026-07-05, decision 7).
PROFILE = settings.get("PROFILE", "general")
JSON_PATH = f"batch/data/rules.{PROFILE}.json"
DATA_DIR = Path("batch/data/tagging")

engine_new = ConceptTagger.load(DATA_DIR, PROFILE)
engine_old = RulesEngine(JSON_PATH, lemmatize=False)


def find_trigger_tokens(text: str, substring: str) -> list[str]:
    """Find tokens in text that contain substring (case-insensitive)."""
    return [t for t in tokenize(text) if substring.lower() in t.lower()]


async def main():
    async with AsyncSessionLocal() as session:
        repo = ImagesRepository(session)
        rows = await repo.get_images_and_ocr_texts()

    corpus = [(fn, iid, text) for fn, iid, text, conf, _ in rows if conf >= 0.4]
    print(f"Corpus: {len(corpus)}", flush=True)

    # Extract root word for substring search from tag name
    tag_roots = {
        "тема:мать": "мат",
        "тема:кринж": "кринж",
        "тема:цирк": "цирк",
        "тема:пацан": "пацан",
        "тема:волки": "волк",
    }

    for tag in TAGS_TO_CHECK:
        root = tag_roots[tag]
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
        print("  Top triggering tokens:")
        for tok, cnt in token_counts.most_common(15):
            print(f"    {tok!r}: {cnt}")


asyncio.run(main())