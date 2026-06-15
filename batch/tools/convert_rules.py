"""
Convert rules.<profile>.json → concepts.<profile>.yaml + tags.<profile>.yaml

Usage:
    python -m batch.tools.convert_rules \\
        --rules batch/data/rules.general.json \\
        --out batch/data/tagging \\
        --profile general

The converter seeds the new YAML from the legacy JSON format.  All alias chains
are collapsed into a single concept's ``words`` list; every vote gets weight 1.0.
With default thresholds at 1.0 the new engine reproduces the old binary behaviour
exactly — a clean baseline for the diff tool.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import yaml


def _resolve_chain(key: str, rules: dict) -> tuple[str, dict]:
    """Follow string aliases until we reach a dict. Returns (resolved_key, tag_dict)."""
    visited: set[str] = set()
    while isinstance(rules.get(key), str):
        if key in visited:
            raise ValueError(f"Cycle in alias chain at '{key}'")
        visited.add(key)
        key = rules[key]
    target = rules.get(key)
    if not isinstance(target, dict):
        raise ValueError(f"Alias chain ends at '{key}' which has no concept definition")
    return key, target


def _build_votes(tag_dict: dict) -> dict[str, float]:
    votes: dict[str, float] = {}
    for tag_key, tag_value in tag_dict.items():
        values = tag_value if isinstance(tag_value, list) else [tag_value]
        for v in values:
            votes[f"{tag_key}:{v}"] = 1.0
    return votes


def convert(rules_path: Path, out_dir: Path, profile: str) -> None:
    with open(rules_path, encoding="utf-8") as f:
        rules: dict = json.load(f)
    rules.pop("_thresholds", None)

    base_concepts = {k: v for k, v in rules.items() if isinstance(v, dict)}
    alias_entries = {k: v for k, v in rules.items() if isinstance(v, str)}

    # Map each concept key to the list of aliases that resolve to it
    aliases_for: dict[str, list[str]] = defaultdict(list)
    for alias in alias_entries:
        try:
            resolved_key, _ = _resolve_chain(alias, rules)
        except ValueError as exc:
            print(f"  WARNING: skipping alias '{alias}': {exc}")
            continue
        aliases_for[resolved_key].append(alias)

    concepts_data: dict = {}
    all_vote_tags: set[str] = set()

    for concept_key, tag_dict in base_concepts.items():
        words = [concept_key] + sorted(aliases_for.get(concept_key, []))
        votes = _build_votes(tag_dict)
        all_vote_tags.update(votes.keys())
        concepts_data[concept_key] = {"words": words, "votes": votes}

    tags_data = {
        "defaults": {"threshold": 1.0},
        "tags": {tag: {} for tag in sorted(all_vote_tags)},
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    concepts_path = out_dir / f"concepts.{profile}.yaml"
    tags_path = out_dir / f"tags.{profile}.yaml"

    with open(concepts_path, "w", encoding="utf-8") as f:
        yaml.dump(concepts_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    with open(tags_path, "w", encoding="utf-8") as f:
        yaml.dump(tags_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"Wrote {concepts_path} ({len(concepts_data)} concepts)")
    print(f"Wrote {tags_path} ({len(all_vote_tags)} tags)")

    big_groups = sorted(
        ((k, v) for k, v in aliases_for.items() if len(v) >= 5),
        key=lambda x: -len(x[1]),
    )
    if big_groups:
        print("\nConcepts with 5+ aliases:")
        for k, aliases in big_groups:
            print(f"  {k}: {len(aliases)} aliases")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", required=True, help="Path to rules.<profile>.json")
    parser.add_argument("--out", required=True, help="Output directory (will be created)")
    parser.add_argument("--profile", default="general")
    args = parser.parse_args()
    convert(Path(args.rules), Path(args.out), args.profile)


if __name__ == "__main__":
    main()