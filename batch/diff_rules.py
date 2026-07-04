"""
Compare two rule versions over the full OCR corpus and report tag changes.

Two modes:

  Two ConceptTagger profiles:
    python -m batch.diff_rules \\
        --data-dir batch/data/tagging \\
        --profile-a general \\
        --profile-b general-v2

  Legacy RulesEngine JSON vs ConceptTagger (migration validation):
    python -m batch.diff_rules \\
        --old-json batch/data/rules.general.json \\
        --data-dir batch/data/tagging \\
        --profile-b general

Options:
  --limit N   Sample filenames to show per changed tag (default 5)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rules.concept_tagger import ConceptTagger
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository


class _Tagger(Protocol):
    def tag_set(self, text: str) -> set[str]: ...


class _ConceptTaggerAdapter:
    def __init__(self, engine: ConceptTagger):
        self._engine = engine

    def tag_set(self, text: str) -> set[str]:
        result = self._engine.tag(text)
        return {f"{k}:{v}" for k, v in result.tags}


class _LegacyEngineAdapter:
    def __init__(self, json_path: str):
        from rules.engine import RulesEngine
        # Use the same defaults that build_tags_from_ocr.py used in production:
        # lemmatize=False (substring matching), no fuzzy
        self._engine = RulesEngine(json_path, lemmatize=False)

    def tag_set(self, text: str) -> set[str]:
        return {f"{name}:{value}" for name, value in self._engine.get_tags_for_ocr_text(text)}


async def _load_corpus(confidence_min: float = 0.4) -> list[tuple[str, int, str]]:
    async with AsyncSessionLocal() as session:
        repo = ImagesRepository(session)
        rows = await repo.get_images_and_ocr_texts()
    return [(fn, iid, text) for fn, iid, text, conf, _ in rows if conf >= confidence_min]


def _run_diff(
    tagger_a: _Tagger,
    label_a: str,
    tagger_b: _Tagger,
    label_b: str,
    corpus: list[tuple[str, int, str]],
    sample_limit: int,
) -> None:
    gained: dict[str, list[str]] = defaultdict(list)
    lost: dict[str, list[str]] = defaultdict(list)
    counts_a: dict[str, int] = defaultdict(int)
    counts_b: dict[str, int] = defaultdict(int)

    print(f"Running over {len(corpus)} images ...")
    for filename, _image_id, text in corpus:
        tags_a = tagger_a.tag_set(text)
        tags_b = tagger_b.tag_set(text)
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
        print(f"\nNo tag changes between '{label_a}' and '{label_b}'.")
        return

    print(f"\n{label_a!r} -> {label_b!r}  |  corpus: {len(corpus)} images\n")
    for tag in changed_tags:
        g, l_ = gained[tag], lost[tag]
        net = len(g) - len(l_)
        sign = "+" if net >= 0 else ""
        print(f"  {tag}: {sign}{net}  (gained {len(g)}, lost {len(l_)})  "
              f"[A={counts_a[tag]}, B={counts_b[tag]}]")
        if g:
            samples = g[:sample_limit]
            suffix = f" ... +{len(g) - sample_limit} more" if len(g) > sample_limit else ""
            print(f"    gained: {', '.join(samples)}{suffix}")
        if l_:
            samples = l_[:sample_limit]
            suffix = f" ... +{len(l_) - sample_limit} more" if len(l_) > sample_limit else ""
            print(f"    lost:   {', '.join(samples)}{suffix}")


async def main_async(args: argparse.Namespace) -> None:
    corpus = await _load_corpus()

    if args.old_json:
        print(f"Loading legacy engine from {args.old_json} ...")
        tagger_a: _Tagger = _LegacyEngineAdapter(args.old_json)
        label_a = f"legacy:{Path(args.old_json).name}"
    else:
        print(f"Loading profile '{args.profile_a}' ...")
        tagger_a = _ConceptTaggerAdapter(ConceptTagger.load(Path(args.data_dir), args.profile_a))
        label_a = args.profile_a

    print(f"Loading profile '{args.profile_b}' ...")
    tagger_b: _Tagger = _ConceptTaggerAdapter(ConceptTagger.load(Path(args.data_dir), args.profile_b))
    label_b = args.profile_b

    _run_diff(tagger_a, label_a, tagger_b, label_b, corpus, args.limit)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--profile-a", default=None, help="ConceptTagger profile for side A")
    parser.add_argument("--profile-b", required=True, help="ConceptTagger profile for side B")
    parser.add_argument("--old-json", default=None, help="Legacy rules JSON for side A")
    parser.add_argument("--limit", type=int, default=5, help="Sample filenames per changed tag")
    args = parser.parse_args()

    if not args.old_json and not args.profile_a:
        parser.error("provide either --profile-a or --old-json for side A")

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()