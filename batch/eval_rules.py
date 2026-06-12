"""
Evaluate ConceptTagger against the golden set.

Usage:
    python -m batch.eval_rules \\
        --data-dir batch/data/tagging \\
        --profile general \\
        [--golden batch/data/tagging/golden.general.yaml]

Prints per-tag TP/FP/FN/precision/recall/F1 and overall coverage.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rules.concept_tagger import ConceptTagger


def _load_golden(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def evaluate(data_dir: Path, profile: str, golden_path: Path) -> None:
    engine = ConceptTagger.load(data_dir, profile)
    items = _load_golden(golden_path)

    if not items:
        print("Golden set is empty.")
        return

    # Per-tag accumulators
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    tagged_items = 0

    for item in items:
        text = item.get("text", "")
        expected: set[str] = set(item.get("expected") or [])
        forbidden: set[str] = set(item.get("forbidden") or [])

        result = engine.tag(text)
        output: set[str] = {f"{k}:{v}" for k, v in result.tags}

        if output:
            tagged_items += 1

        for tag in expected:
            if tag in output:
                tp[tag] += 1
            else:
                fn[tag] += 1

        for tag in forbidden:
            if tag in output:
                fp[tag] += 1

    all_tags = sorted(set(tp) | set(fp) | set(fn))

    if not all_tags:
        print("No expected/forbidden tags found in golden set.")
        return

    col_w = max(len(t) for t in all_tags) + 2
    header = f"{'Tag':<{col_w}} {'TP':>4} {'FP':>4} {'FN':>4}  {'Prec':>7}  {'Rec':>7}  {'F1':>7}"
    print(header)
    print("-" * len(header))

    sum_tp = sum_fp = sum_fn = 0
    for tag in all_tags:
        t, f_, n = tp[tag], fp[tag], fn[tag]
        prec = t / (t + f_) if (t + f_) else 1.0
        rec = t / (t + n) if (t + n) else 1.0
        f1 = _f1(prec, rec)
        print(f"{tag:<{col_w}} {t:>4} {f_:>4} {n:>4}  {prec:>7.3f}  {rec:>7.3f}  {f1:>7.3f}")
        sum_tp += t
        sum_fp += f_
        sum_fn += n

    micro_prec = sum_tp / (sum_tp + sum_fp) if (sum_tp + sum_fp) else 1.0
    micro_rec = sum_tp / (sum_tp + sum_fn) if (sum_tp + sum_fn) else 1.0
    micro_f1 = _f1(micro_prec, micro_rec)

    print()
    print(f"Micro precision={micro_prec:.3f}  recall={micro_rec:.3f}  F1={micro_f1:.3f}")
    print(f"Items: {len(items)}  |  tagged: {tagged_items}/{len(items)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--profile", default="general")
    parser.add_argument("--golden", default=None)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    golden_path = Path(args.golden) if args.golden else data_dir / f"golden.{args.profile}.yaml"
    evaluate(data_dir, args.profile, golden_path)


if __name__ == "__main__":
    main()