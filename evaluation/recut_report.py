"""Precision of the shipped recut detector on the labelled sample (screenshot recut R7, FR-16). Pure: reads the gold files
under evaluation/gold/recut/ and counts; no database, no images.

The sample was judged on the R2 spike's detector A. The shipped detector (`margin_trim` v1) is A's margin part, so only
margin proposals count: round 1 items proposed by A or C (all at confidence 1.0), round 2 items of a margin band. Round 2
inner-rectangle items are excluded (the shipped detector does not propose them). A different detector version needs a new
labelled round. Grades: 2 use as is, 1 slightly off, 0 wrong, s skipped.

BAND_LABELS mirrors the bands of GET /api/recut/stats (Backend/app/repositories/recut_repository.py), so gold and live
numbers compare band for band."""
import json
from math import sqrt
from pathlib import Path

BAND_LABELS = ("0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0", "1.0")
_FILES = ("spike-round1-judgments.json", "spike-round1-key.json", "spike-round2-judgments.json", "spike-round2-key.json")


def band_of(confidence: float) -> str:
    """Lower edge inclusive, upper exclusive; 1.0 is its own band; anything under 0.7 is the first band (the detector's
    floor is 0.6)."""
    if confidence >= 1.0:
        return "1.0"
    if confidence >= 0.9:
        return "0.9-1.0"
    if confidence >= 0.8:
        return "0.8-0.9"
    if confidence >= 0.7:
        return "0.7-0.8"
    return "0.6-0.7"


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (centre - margin, centre + margin)


def _empty() -> dict:
    return {"graded": 0, "as_is": 0, "slightly_off": 0, "wrong": 0}


def _count(counts: dict, grade: str) -> None:
    counts["graded"] += 1
    counts[{"2": "as_is", "1": "slightly_off", "0": "wrong"}[grade]] += 1


def build_report(round1_judgments: dict, round1_key: dict, round2_judgments: dict, round2_key: dict) -> dict:
    bands = {label: _empty() for label in BAND_LABELS}
    excluded = {"inner": 0, "skipped": 0, "ungraded": 0}
    recall = {"measurable": False, "judged_no_proposal": 0, "needed_a_cut": 0, "skipped_no_proposal": 0}

    def take(grade, label):
        if grade is None:
            excluded["ungraded"] += 1
        elif grade == "s":
            excluded["skipped"] += 1
        else:
            _count(bands[label], grade)

    for item, entry in round1_key.items():
        by = entry.get("by", [])
        grade = round1_judgments.get(item)
        if "A" in by or "C" in by:
            take(grade, "1.0")
        elif "none" in by:
            if grade in ("y", "n"):
                recall["judged_no_proposal"] += 1
                recall["needed_a_cut"] += grade == "y"
            elif grade == "s":
                recall["skipped_no_proposal"] += 1
    for item, entry in round2_key.items():
        if not str(entry.get("band", "")).startswith("margin"):
            excluded["inner"] += 1
            continue
        take(round2_judgments.get(item), band_of(entry["confidence"]))

    total = _empty()
    for counts in bands.values():
        for field in total:
            total[field] += counts[field]
    return {"bands": bands, "total": total, "excluded": excluded, "recall": recall}


def load_report(directory: Path) -> dict:
    directory = Path(directory)
    missing = [name for name in _FILES if not (directory / name).exists()]
    if missing:
        raise FileNotFoundError(f"missing gold files in {directory}: {', '.join(missing)}")
    r1j, r1k, r2j, r2k = (json.loads((directory / name).read_text(encoding="utf-8")) for name in _FILES)
    return build_report(r1j, r1k, r2j, r2k)


def _row(label: str, counts: dict) -> str:
    graded = counts["graded"]
    if graded == 0:
        return f"{label:<10}{0:>8}{'-':>26}{'-':>34}{0:>7}"
    as_is = f"{counts['as_is'] / graded:.1%} ({counts['as_is']}/{graded})"
    ok = counts["as_is"] + counts["slightly_off"]
    low, high = wilson(ok, graded)
    either = f"{ok / graded:.1%} ({ok}/{graded}) [{low:.1%}-{high:.1%}]"
    return f"{label:<10}{graded:>8}{as_is:>26}{either:>34}{counts['wrong']:>7}"


def format_report(report: dict) -> str:
    lines = [
        "Recut detection precision on the labelled sample",
        "Sample: the R2 spike's detector A, whose margin part is the shipped margin_trim v1. A different detector version",
        "needs a new labelled round.",
        "",
        f"{'band':<10}{'graded':>8}{'as is':>26}{'as is or slightly off [95% CI]':>34}{'wrong':>7}",
    ]
    lines += [_row(label, report["bands"][label]) for label in BAND_LABELS]
    lines.append(_row("all", report["total"]))
    excluded = report["excluded"]
    lines += [
        "",
        f"Excluded: {excluded['inner']} inner-rectangle items (not proposed by the shipped detector), "
        f"{excluded['skipped']} skipped, {excluded['ungraded']} without a judgment.",
        "",
        "Recall: not measurable. "
        f"{report['recall']['judged_no_proposal']} no-proposal items were judged ({report['recall']['needed_a_cut']} needed a "
        f"cut) and {report['recall']['skipped_no_proposal']} were skipped; too few to say anything.",
    ]
    return "\n".join(lines)
