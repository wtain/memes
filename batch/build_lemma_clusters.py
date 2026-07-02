import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml


def load_lemma_source(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_language_blocks(data: dict, language: str) -> dict[str, dict[str, int]]:
    """
    Normalize raw BOW JSON into {lang: {lemma: freq}}.

    Flat dicts (descriptions-sourced BOW files have no per-language keys) are
    treated as a single implicit "en" block. `language="all"` returns every
    block found; a specific language returns only that block (empty dict +
    warning if the language is absent from the input).
    """
    is_flat = not data or not isinstance(next(iter(data.values())), dict)
    normalized = {"en": data} if is_flat else data

    if language == "all":
        return normalized

    if language not in normalized:
        print(f"WARNING: language {language!r} not found in input file, skipping")
        return {}

    return {language: normalized[language]}


def build_cluster_records(
    groups: dict[int, list[str]],
    frequencies: dict[str, int],
) -> tuple[list[dict], list[dict]]:
    """
    Turn HDBSCAN's {label: [lemma, ...]} into (clusters, singletons).
    Clusters are sorted by total_frequency descending and numbered from 1.
    Singleton (label -1) lemmas and cluster members keep their relative
    order from `frequencies` (build_bow writes frequency-descending order).
    """
    order = {lemma: i for i, lemma in enumerate(frequencies)}

    cluster_groups = [members for label, members in groups.items() if label != -1]
    cluster_groups.sort(key=lambda members: sum(frequencies[m] for m in members), reverse=True)

    clusters = []
    for idx, members in enumerate(cluster_groups, start=1):
        ordered_members = sorted(members, key=lambda m: order[m])
        member_freqs = {m: frequencies[m] for m in ordered_members}
        clusters.append({
            "id": idx,
            "ollama_concept": None,
            "total_frequency": sum(member_freqs.values()),
            "size": len(member_freqs),
            "members": member_freqs,
        })

    singleton_lemmas = sorted(groups.get(-1, []), key=lambda m: order[m])
    singletons = [{"lemma": lemma, "frequency": frequencies[lemma]} for lemma in singleton_lemmas]

    return clusters, singletons


def write_yaml_output(output_file: str, parameters: dict, languages: dict) -> None:
    os.makedirs(Path(output_file).parent, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "parameters": parameters,
        "languages": languages,
    }
    with open(output_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def nearest_concept(centroid: np.ndarray, concept_rows: list[tuple[int, str, np.ndarray]]) -> dict | None:
    if not concept_rows:
        return None

    def distance(row):
        _, _, embedding = row
        return 1.0 - float(np.dot(centroid, np.asarray(embedding)))

    best = min(concept_rows, key=distance)
    concept_id, name, _ = best
    return {"name": name, "concept_id": concept_id, "cosine_distance": round(distance(best), 4)}
