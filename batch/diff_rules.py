"""
Compare two ConceptTagger profiles over the full OCR corpus and report tag changes.

Usage:
    python -m batch.diff_rules \\
        --data-dir batch/data/tagging \\
        --profile-a general \\
        --profile-b general-v2 \\
        [--limit 5]          # sample images to show per tag

Outputs per-tag gain/loss counts and example image filenames so you can review
the blast radius of a rule change before committing it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rules.concept_tagger import ConceptTagger
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository


async def _load_corpus(confidence_min: float = 0.4) -> list[tuple[str, int, str]]:
    """Return [(filename, image_id, ocr_text), ...] filtered by confidence."""
    async with AsyncSessionLocal() as session:
        repo = ImagesRepository(session)
        rows = await repo.get_images_and_ocr_texts()
    return [(filename, image_id, text) for filename, image_id, text, conf in rows if conf >= confidence_min]


def _tag_set(engine: ConceptTagger, text: str) -> set[str]:
    result = engine.tag(text)
    return {f"{k}:{v}" for k, v in result.tags}


def diff(
    data_dir: Path,
    profile_a: str,
    profile_b: str,
    sample_limit: int,
    corpus: list[tuple[str, int, str]],
) -> None:
    print(f"Loading profile '{profile_a}' …")
    engine_a = ConceptTagger.load(data_dir, profile_a)
    print(f"Loading profile '{profile_b}' …")
    engine_b = ConceptTagger.load(data_dir, profile_b)

    gained: dict[str, list[str]] = defaultdict(list)   # tag → [filename, ...]
    lost: dict[str, list[str]] = defaultdict(list)
    counts_a: dict[str, int] = defaultdict(int)
    counts_b: dict[str, int] = defaultdict(int)

    print(f"Running over {len(corpus)} images …")
    for filename, _image_id, text in corpus:
        tags_a = _tag_set(engine_a, text)
        tags_b = _tag_set(engine_b, text)

        for t in tags_a:
            counts_a[t] += 1
        for t in tags_b:
            counts_b[t] += 1

        for t in tags_b - tags_a:
            gained[t].append(filename)
        for t in tags_a - tags_b:
            lost[t].append(filename)

    changed_tags = sorted(set(gained) | set(lost))
    if not changed_tags:
        print("\nNo tag changes detected.")
        return

    print(f"\nProfile {profile_a!r} → {profile_b!r}  |  corpus: {len(corpus)} images\n")
    for tag in changed_tags:
        g, l_ = gained[tag], lost[tag]
        net = len(g) - len(l_)
        sign = "+" if net >= 0 else ""
        print(f"  {tag}: {sign}{net}  (gained {len(g)}, lost {len(l_)})  "
              f"[A={counts_a[tag]}, B={counts_b[tag]}]")
        if g:
            samples = g[:sample_limit]
            suffix = f" … +{len(g) - sample_limit} more" if len(g) > sample_limit else ""
            print(f"    gained: {', '.join(samples)}{suffix}")
        if l_:
            samples = l_[:sample_limit]
            suffix = f" … +{len(l_) - sample_limit} more" if len(l_) > sample_limit else ""
            print(f"    lost:   {', '.join(samples)}{suffix}")


async def main_async(args: argparse.Namespace) -> None:
    corpus = await _load_corpus()
    diff(
        Path(args.data_dir),
        args.profile_a,
        args.profile_b,
        args.limit,
        corpus,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--profile-a", required=True)
    parser.add_argument("--profile-b", required=True)
    parser.add_argument("--limit", type=int, default=5, help="Sample images per tag")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()