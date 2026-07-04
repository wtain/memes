"""
Evaluate rules/lang_plausibility.py's garbage-filtering quality against a
hand-labeled golden set (batch/data/tagging/golden_ocr_language.yaml).

Usage:
    python -m batch.eval_ocr_language_filter \
        --golden batch/data/tagging/golden_ocr_language.yaml \
        [--threshold 0.3]

Prints precision/recall/F1 and false-suppression rate at the given threshold
(false-suppression rate first and separately — see the Metric section of
docs/superpowers/specs/2026-07-02-ocr-language-plausibility-filtering.md for
why it's the headline number), plus a sweep across candidate thresholds to
support choosing OCR_LANG_SCORE_MIN deliberately.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from rules.lang_plausibility import score

_SWEEP_THRESHOLDS = [round(0.1 + 0.05 * i, 2) for i in range(11)]  # 0.10 .. 0.60


def _load_golden(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _score_golden_items(items: list[dict]) -> list[tuple[float | None, bool]]:
    return [(score(item["text"], item["language"]), item["is_garbage"]) for item in items]


def _classify(row_score: float | None, threshold: float) -> bool:
    """True = flagged as garbage. A None score (too short to judge) is never flagged."""
    if row_score is None:
        return False
    return row_score < threshold


def evaluate_at_threshold(scored_items: list[tuple[float | None, bool]], threshold: float) -> dict:
    tp = fp = fn = tn = 0
    for row_score, actual_garbage in scored_items:
        predicted_garbage = _classify(row_score, threshold)
        if predicted_garbage and actual_garbage:
            tp += 1
        elif predicted_garbage and not actual_garbage:
            fp += 1
        elif not predicted_garbage and actual_garbage:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    genuine_total = tn + fp
    false_suppression_rate = fp / genuine_total if genuine_total else 0.0

    return {
        "threshold": threshold,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_suppression_rate": false_suppression_rate,
    }


def threshold_sweep(scored_items: list[tuple[float | None, bool]], thresholds: list[float]) -> list[dict]:
    return [evaluate_at_threshold(scored_items, t) for t in thresholds]


def _print_report(scored_items: list[tuple[float | None, bool]], threshold: float) -> None:
    result = evaluate_at_threshold(scored_items, threshold)

    print(f"Items: {len(scored_items)}")
    print(f"Threshold: {threshold}")
    print()
    genuine_total = result["fp"] + result["tn"]
    print(
        f"False-suppression rate: {result['false_suppression_rate']:.3f}  "
        f"({result['fp']} genuine rows wrongly flagged / {genuine_total} genuine total)"
    )
    print()
    print(f"Precision={result['precision']:.3f}  Recall={result['recall']:.3f}  F1={result['f1']:.3f}")
    print(f"TP={result['tp']} FP={result['fp']} FN={result['fn']} TN={result['tn']}")
    print()
    print("Threshold sweep:")
    header = f"{'Threshold':>10}  {'Prec':>7}  {'Rec':>7}  {'F1':>7}  {'FalseSupp':>10}"
    print(header)
    print("-" * len(header))
    for row in threshold_sweep(scored_items, _SWEEP_THRESHOLDS):
        print(
            f"{row['threshold']:>10.2f}  {row['precision']:>7.3f}  {row['recall']:>7.3f}  "
            f"{row['f1']:>7.3f}  {row['false_suppression_rate']:>10.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", default="batch/data/tagging/golden_ocr_language.yaml")
    parser.add_argument("--threshold", type=float, default=0.3)
    args = parser.parse_args()

    items = _load_golden(Path(args.golden))
    if not items:
        print("Golden set is empty.")
        return

    scored_items = _score_golden_items(items)
    _print_report(scored_items, args.threshold)


if __name__ == "__main__":
    main()
