"""Blinded candidate pools for human judging (P6 spec, decision 5). Pure: standard library only."""
import random
from dataclasses import dataclass
from typing import Collection, Mapping, Sequence


@dataclass(frozen=True)
class Pool:
    query_id: str
    candidates: tuple[str, ...]              # the order the judge sees (shuffled)
    sources: dict[str, tuple[str, ...]]      # PRIVATE: image id -> the sources that proposed it


def build_pool(query_id: str, ranked: Mapping[str, Sequence[str]], random_candidates: Sequence[str], *,
               exclude: Collection[str] = (), ranked_target: int = 9, random_target: int = 3,
               seed: int | str = 0) -> Pool:
    """Round-robin over the ranked sources from rank 1 (skipping images already taken or excluded) until
    `ranked_target` distinct images are chosen; an exhausted or empty source is filled by the others. Random
    images are added last. The public order is a shuffle seeded by (seed, query id)."""
    names = list(ranked)
    seen = set(exclude)
    cursors = {name: 0 for name in names}
    taken: list[str] = []

    progressed = True
    while len(taken) < ranked_target and progressed:
        progressed = False
        for name in names:
            if len(taken) >= ranked_target:
                break
            ranking = ranked[name]
            while cursors[name] < len(ranking) and ranking[cursors[name]] in seen:
                cursors[name] += 1
            if cursors[name] < len(ranking):
                image_id = ranking[cursors[name]]
                cursors[name] += 1
                seen.add(image_id)
                taken.append(image_id)
                progressed = True

    random_taken: list[str] = []
    for image_id in random_candidates:
        if len(random_taken) >= random_target:
            break
        if image_id not in seen:
            seen.add(image_id)
            random_taken.append(image_id)

    candidates = taken + random_taken
    membership = {name: set(ranked[name]) for name in names}
    sources: dict[str, tuple[str, ...]] = {}
    for image_id in candidates:
        found = [name for name in names if image_id in membership[name]]
        if image_id in random_taken:
            found.append("random")
        sources[image_id] = tuple(sorted(found))

    order = list(candidates)
    random.Random(f"{seed}:{query_id}").shuffle(order)
    return Pool(query_id=query_id, candidates=tuple(order), sources=sources)
