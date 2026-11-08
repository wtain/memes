"""Suggested comics (epic multi-image-comics, C9): pure timestamp parsing and grouping, no I/O.
Two images are linked when their filename timestamps are at most gap_seconds apart and their CLIP cosine distance
lies in [min_distance, max_distance]; sizes are never compared. Linked images form connected components (A-B and B-C
make one group even when A-C is far apart). The distance lookup is injected, so the database stays in the job."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterator

DATETIME = "datetime"
EPOCH = "epoch"

# YYYYMMDD_HHMMSS anywhere in the name; digits on either side are allowed (IMG_20200923_110706_156.jpg).
_DATETIME_RE = re.compile(r"(20\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})")
# A run of exactly 13 digits, not adjacent to other digits (epoch milliseconds, FB_IMG_1746890432471.jpg).
_EPOCH_RE = re.compile(r"(?<!\d)(\d{13})(?!\d)")
_EPOCH_MIN_MS = int(datetime(2005, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
_EPOCH_MAX_MS = int(datetime(2035, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def parse_timestamp(filename: str) -> tuple[str, float] | None:
    """(shape, seconds) from a file name, or None. A datetime is read as wall-clock time without a zone: only
    differences between images of the same shape are ever used."""
    for match in _DATETIME_RE.finditer(filename):
        try:
            moment = datetime(*(int(g) for g in match.groups()), tzinfo=timezone.utc)
        except ValueError:
            continue
        return DATETIME, moment.timestamp()
    for match in _EPOCH_RE.finditer(filename):
        millis = int(match.group(1))
        if _EPOCH_MIN_MS <= millis < _EPOCH_MAX_MS:
            return EPOCH, millis / 1000.0
    return None


@dataclass(frozen=True)
class Group:
    image_ids: list  # ordered by timestamp, then file name
    max_gap: float  # the largest gap among the group's linked edges (seconds)
    max_distance: float  # the largest CLIP distance among the group's linked edges


@dataclass(frozen=True)
class GroupingResult:
    groups: list[Group]
    pairs_linked: int
    dropped_oversized: int


def window_pairs(items, gap_seconds: float) -> Iterator[tuple]:
    """Every pair of items of the same shape whose timestamps are at most gap_seconds apart, as (a, b) with a not
    later than b. Only these pairs ever need a distance, so the job never compares all images with each other."""
    by_shape: dict[str, list] = {}
    for item in items:
        by_shape.setdefault(item[2], []).append(item)
    for shaped in by_shape.values():
        shaped.sort(key=lambda it: (it[3], it[1], str(it[0])))
        for i, a in enumerate(shaped):
            for b in shaped[i + 1:]:
                if b[3] - a[3] > gap_seconds:
                    break
                yield a, b


def group_candidates(items, distance_fn: Callable[[object, object], float | None], gap_seconds: float,
                     min_distance: float, max_distance: float, max_group: int) -> GroupingResult:
    """items: (image_id, filename, shape, ts). distance_fn(a_id, b_id) -> cosine distance or None (no embedding).
    A component bigger than max_group is a batch save: it is dropped and counted, never truncated."""
    parent: dict = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    order = {item[0]: (item[3], item[1], str(item[0])) for item in items}
    edges = []  # (a_id, gap, distance)
    for a, b in window_pairs(items, gap_seconds):
        distance = distance_fn(a[0], b[0])
        if distance is None or not (min_distance <= distance <= max_distance):
            continue
        edges.append((a[0], b[3] - a[3], distance))
        for node in (a[0], b[0]):
            parent.setdefault(node, node)
        parent[find(a[0])] = find(b[0])

    members: dict = {}
    for node in parent:
        members.setdefault(find(node), []).append(node)
    stats: dict = {}  # root -> [max_gap, max_distance]
    for a_id, gap, distance in edges:
        s = stats.setdefault(find(a_id), [0.0, 0.0])
        s[0] = max(s[0], gap)
        s[1] = max(s[1], distance)

    groups, dropped = [], 0
    for root, nodes in members.items():
        if len(nodes) > max_group:
            dropped += 1
            continue
        nodes.sort(key=order.__getitem__)
        groups.append(Group(nodes, stats[root][0], stats[root][1]))
    groups.sort(key=lambda g: (g.max_distance, str(g.image_ids[0])))
    return GroupingResult(groups, len(edges), dropped)
