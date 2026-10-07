import uuid
from typing import Sequence

from evaluation.gold import GoldQuery
from repository.ocr_lemmas import lexical_or_scores, scored_image_matches


class LexicalRetriever:
    """The production keyword search (mode "and": every query lemma must match, so a long message is usually
    empty) or its any-token variant (mode "or": per-token scores summed, same source and tier weights)."""

    def __init__(self, session, mode: str):
        if mode not in ("and", "or"):
            raise ValueError("mode must be 'and' or 'or'")
        self.session = session
        self.mode = mode
        self.name = f"lexical_{mode}"
        self.config = {"mode": mode}

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        corpus = {uuid.UUID(image_id) for image_id in corpus_ids}
        if self.mode == "and":
            scores = await scored_image_matches(self.session, query.text) or {}
        else:
            scores = await lexical_or_scores(self.session, query.text, corpus)
        ranked = sorted(((str(image_id), score) for image_id, score in scores.items() if image_id in corpus),
                        key=lambda pair: (-pair[1], pair[0]))
        return ranked[:k]
