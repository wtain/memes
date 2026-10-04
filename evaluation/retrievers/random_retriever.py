import random
from typing import Sequence

from evaluation.gold import GoldQuery


class RandomRetriever:
    """The floor: a seeded shuffle of the corpus, different per query."""

    name = "random"

    def __init__(self, seed: int = 0):
        self.seed = seed
        self.config = {"seed": seed}

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        order = sorted(corpus_ids)
        random.Random(f"{self.seed}:{query.id}").shuffle(order)
        top = order[:k]
        return [(image_id, 1.0 - index / max(len(top), 1)) for index, image_id in enumerate(top)]
