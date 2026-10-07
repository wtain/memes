"""Duplicate-cluster collapse and facet counts (P4 spec, decision 5). Pure."""
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from retrieval.config import AXES
from retrieval.model import ScoredCandidate


@dataclass(frozen=True)
class RankedItem:
    scored: ScoredCandidate
    cluster_size: int | None     # candidates of its duplicate cluster collapsed into it (itself included)


def collapse_clusters(ranked: Sequence[ScoredCandidate]) -> list[RankedItem]:
    """`ranked` is already ordered best first; each cluster keeps its first (best) member."""
    sizes = Counter(item.candidate.cluster_id for item in ranked if item.candidate.cluster_id is not None)
    seen: set[int] = set()
    items: list[RankedItem] = []
    for item in ranked:
        cluster_id = item.candidate.cluster_id
        if cluster_id is None:
            items.append(RankedItem(item, None))
        elif cluster_id not in seen:
            seen.add(cluster_id)
            items.append(RankedItem(item, sizes[cluster_id]))
    return items


def facet_counts(items: Sequence[RankedItem]) -> dict[str, dict[str, int]]:
    """Per axis, how many collapsed results carry each value (an item counts once per value)."""
    counts: dict[str, Counter] = {axis: Counter() for axis in AXES}
    for item in items:
        for axis in AXES:
            for key in {key for key, _confidence in item.scored.candidate.values.get(axis, ())}:
                counts[axis][key] += 1
    return {axis: dict(counter) for axis, counter in counts.items()}
