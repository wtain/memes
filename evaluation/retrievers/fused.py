import uuid
from typing import Callable, Sequence

from evaluation.gold import GoldQuery


def _default_repository(session):
    from repository.meme_retrieval import MemeRetrievalRepository
    return MemeRetrievalRepository(session)


class FusedRetriever:
    """The P4 pipeline (parse, embed, candidates, fusion) restricted to the gold corpus: the system under test."""

    name = "fused"

    def __init__(self, session, pipeline, repository_factory: Callable = _default_repository):
        self._session = session
        self._pipeline = pipeline
        self._repository_factory = repository_factory
        config = getattr(pipeline, "config", None)
        self.config = {"weights": dict(config.weights) if config is not None else {},
                       "embeddingModel": getattr(config, "embedding_model", "")}

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        from retrieval.pipeline import SearchRequest
        request = SearchRequest(text=query.text, language_hint=query.language, limit=k,
                                restrict_ids=frozenset(uuid.UUID(image_id) for image_id in corpus_ids))
        result = await self._pipeline.search(request, self._repository_factory(self._session))
        return [(item.scored.candidate.image_id, item.scored.score) for item in result.items][:k]
