"""Batch-side (numpy) vocabulary similarity for soft matching on controlled axes (P1 spec, decision 8).

Not a core module: the backend only reads the generated similarity.json.
"""
from statistics import median

import numpy as np

from taxonomy.model import Taxonomy


def compute_neighbours(keys: list[str], vectors: np.ndarray, threshold: float) -> dict[str, list[dict]]:
    """Within-axis cosine neighbours at or above `threshold` (vectors must be L2-normalised)."""
    similarities = vectors @ vectors.T
    neighbours: dict[str, list[dict]] = {}
    for i, key in enumerate(keys):
        row = [
            {"key": keys[j], "score": round(float(similarities[i, j]), 4)}
            for j in range(len(keys))
            if j != i and similarities[i, j] >= threshold
        ]
        row.sort(key=lambda item: (-item["score"], item["key"]))
        neighbours[key] = row
    return neighbours


def apply_overrides(neighbours: dict[str, list[dict]], overrides: dict, valid_keys: set[str]) -> dict[str, list[dict]]:
    """Apply a symmetric `add: [[a, b, score]]` / `remove: [[a, b]]` override to an axis's neighbours."""
    for entry in [*(overrides.get("add") or []), *(overrides.get("remove") or [])]:
        for key in entry[:2]:
            if key not in valid_keys:
                raise ValueError(f"similarity override names unknown key {key!r}")

    result = {key: list(row) for key, row in neighbours.items()}
    for a, b in (pair[:2] for pair in overrides.get("remove") or []):
        result[a] = [n for n in result[a] if n["key"] != b]
        result[b] = [n for n in result[b] if n["key"] != a]
    for a, b, score in overrides.get("add") or []:
        for source, target in ((a, b), (b, a)):
            result[source] = [n for n in result[source] if n["key"] != target]
            result[source].append({"key": target, "score": round(float(score), 4)})
    for row in result.values():
        row.sort(key=lambda item: (-item["score"], item["key"]))
    return result


def build_artifact(tax: Taxonomy, embedder, *, threshold: float, model_name: str, model_digest: str) -> dict:
    axes = {}
    for axis in tax.controlled_axes():
        keys = list(tax.values[axis])
        vectors = embedder.embed([tax.values[axis][key].definition for key in keys])
        neighbours = compute_neighbours(keys, vectors, threshold)
        overrides = tax.similarity_overrides.get(axis) or {}
        axes[axis] = apply_overrides(neighbours, overrides, set(keys)) if overrides else neighbours
    return {
        "taxonomy_version": tax.version,
        "content_hash": tax.content_hash,
        "overrides_hash": tax.overrides_hash,
        "model_name": model_name,
        "model_digest": model_digest,
        "threshold": threshold,
        "axes": axes,
    }


def check_artifact(artifact: dict, tax: Taxonomy) -> list[str]:
    """Problems that make a stored artifact unusable for the current taxonomy; empty means consistent."""
    problems = []
    if artifact.get("taxonomy_version") != tax.version:
        problems.append(f"taxonomy_version {artifact.get('taxonomy_version')} != {tax.version}")
    if artifact.get("content_hash") != tax.content_hash:
        problems.append("content_hash does not match the vocabulary files")
    if artifact.get("overrides_hash") != tax.overrides_hash:
        problems.append("overrides_hash does not match similarity_overrides.yaml")
    stored_axes = artifact.get("axes", {})
    for axis in tax.controlled_axes():
        stored = stored_axes.get(axis)
        if stored is None:
            problems.append(f"axis {axis!r} missing")
            continue
        valid = set(tax.values[axis])
        for key in sorted(valid - set(stored)):
            problems.append(f"{axis}.{key}: missing from artifact")
        for key, row in stored.items():
            if key not in valid:
                problems.append(f"{axis}.{key}: unknown key")
            for neighbour in row:
                if neighbour["key"] not in valid:
                    problems.append(f"{axis}.{key}: unknown neighbour {neighbour['key']!r}")
    return problems


def neighbour_stats(artifact: dict) -> dict:
    counts, isolated = [], []
    for axis, rows in artifact["axes"].items():
        for key, row in rows.items():
            counts.append(len(row))
            if not row:
                isolated.append(f"{axis}.{key}")
    return {"median": median(counts) if counts else 0, "max": max(counts, default=0), "isolated": isolated}
