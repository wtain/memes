"""The retriever interface every evaluated system implements (P6 spec, decision 7). Typing only."""
from typing import Protocol, Sequence

from evaluation.gold import GoldQuery


class Retriever(Protocol):
    name: str
    config: dict

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        """Ranked (image id, score) pairs, best first, restricted to `corpus_ids`, at most `k`.
        Deterministic: ties are broken by image id. A retriever may also define
        `async def prepare(self, queries)` to batch work (for example one embedding call) before the run."""
        ...
