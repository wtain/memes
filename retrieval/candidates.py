"""Union of the candidate sources (P4 spec, decision 3). Pure."""
from typing import Hashable, Sequence


def merge_ranked(sources: Sequence[Sequence[Hashable]], cap: int) -> list:
    """Round-robin by rank over the ranked source lists, without duplicates, up to `cap` items."""
    if cap <= 0:
        return []
    merged: list = []
    seen: set = set()
    depth = max((len(source) for source in sources), default=0)
    for rank in range(depth):
        for source in sources:
            if rank < len(source) and source[rank] not in seen:
                seen.add(source[rank])
                merged.append(source[rank])
                if len(merged) >= cap:
                    return merged
    return merged
