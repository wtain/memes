"""Vocabulary similarity for soft matching on the controlled axes (P1 spec, decision 8). Pure.

Reads the generated batch/data/taxonomy/similarity.json (25 curated near-synonym pairs)."""
import json
from pathlib import Path
from typing import Mapping

from taxonomy.loader import DEFAULT_DIR


class SimilarityIndex:
    def __init__(self, axes: Mapping[str, Mapping[str, Mapping[str, float]]]):
        self._axes = axes

    def neighbours(self, axis: str, key: str) -> Mapping[str, float]:
        return self._axes.get(axis, {}).get(key, {})

    def similarity(self, axis: str, a: str, b: str) -> float:
        if a == b:
            return 1.0
        return float(self.neighbours(axis, a).get(b, 0.0))

    def neighbours_for(self, query_axes: Mapping[str, tuple]) -> dict[str, dict[str, tuple[tuple[str, float], ...]]]:
        """For the repository's axis source: axis -> query key -> ((neighbour key, score), ...), best first."""
        result: dict[str, dict[str, tuple[tuple[str, float], ...]]] = {}
        for axis, values in query_axes.items():
            result[axis] = {
                key: tuple(sorted(self.neighbours(axis, key).items(), key=lambda pair: (-pair[1], pair[0])))
                for key, _confidence in values
            }
        return result


def load_similarity(path: Path | None = None) -> SimilarityIndex:
    target = Path(path) if path is not None else DEFAULT_DIR / "similarity.json"
    with open(target, encoding="utf-8") as handle:
        data = json.load(handle)
    axes = {
        axis: {key: {item["key"]: float(item["score"]) for item in neighbours} for key, neighbours in keys.items()}
        for axis, keys in data.get("axes", {}).items()
    }
    return SimilarityIndex(axes)
