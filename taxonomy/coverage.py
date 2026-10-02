"""Batch-side helpers for the vocabulary corpus check (P1 spec, section 9). Not imported by the backend."""
import json
import sys
from collections import Counter

from taxonomy.loader import load
from taxonomy.model import Taxonomy

OVERUSE_SHARE = 0.4


def assign_from_response(raw, tax: Taxonomy) -> dict | None:
    """Parse a probe model's JSON answer into valid keys and free-text suggestions.

    Returns None when the answer is not a JSON object. Keys not in the vocabulary become suggestions.
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    assigned = {axis: [] for axis in tax.controlled_axes()}
    suggested = {axis: [] for axis in tax.controlled_axes()}
    for axis in tax.controlled_axes():
        values = data.get(axis) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            value = value.strip()
            if not value:
                continue
            if tax.has_key(axis, value):
                if value not in assigned[axis]:
                    assigned[axis].append(value)
            else:
                suggested[axis].append(value)
    extra = data.get("suggested")
    if isinstance(extra, dict):
        for axis in tax.controlled_axes():
            items = extra.get(axis) or []
            if isinstance(items, list):
                suggested[axis].extend(s.strip() for s in items if isinstance(s, str) and s.strip())
    return {"assigned": assigned, "suggested": suggested}


def summarize(results: list[dict], tax: Taxonomy) -> dict:
    total = len(results)
    summary = {"images": total, "axes": {}}
    for axis in tax.controlled_axes():
        counts = Counter({key: 0 for key in tax.values[axis]})
        suggestions: Counter = Counter()
        covered = 0
        for result in results:
            keys = result["assigned"].get(axis, [])
            if keys:
                covered += 1
            counts.update(keys)
            suggestions.update(result["suggested"].get(axis, []))
        summary["axes"][axis] = {
            "covered_share": covered / total if total else 0.0,
            "counts": dict(counts),
            "unused": sorted(key for key, count in counts.items() if count == 0),
            "overused": sorted(key for key, count in counts.items() if total and count / total > OVERUSE_SHARE),
            "suggestions": suggestions.most_common(10),
        }
    return summary


def format_report(summary: dict) -> str:
    lines = ["# Vocabulary coverage", "", f"Images probed: {summary['images']}", ""]
    for axis, data in summary["axes"].items():
        lines += [
            f"## {axis}",
            "",
            f"- Images with at least one value: {data['covered_share']:.0%}",
            f"- Unused values: {', '.join(data['unused']) or 'none'}",
            f"- Overused values (over {OVERUSE_SHARE:.0%} of images): {', '.join(data['overused']) or 'none'}",
            "- Suggested values the model wanted but the vocabulary lacks: "
            + (", ".join(f"{text} ({count})" for text, count in data["suggestions"]) or "none"),
            "",
            "| Value | Images |",
            "|---|---|",
        ]
        lines += [f"| {key} | {count} |" for key, count in sorted(data["counts"].items(), key=lambda kv: -kv[1])]
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    results = []
    failures = 0
    with open(argv[0], encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            if "assigned" in entry:
                results.append(entry)
            else:
                failures += 1
    print(format_report(summarize(results, load())))
    print(f"Probe failures (excluded above): {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
