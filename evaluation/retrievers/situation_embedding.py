import asyncio
import uuid
from typing import Sequence

from sqlalchemy import select

from evaluation.gold import GoldQuery
from Storage.models import MemeProfileSituationEmbedding as Embedding


class SituationEmbeddingRetriever:
    """The embedding-only path: embed the raw message and rank corpus profiles by cosine similarity to their
    stored situation vectors (no translation; the cross-language probe for the P1 embedding decision)."""

    name = "situation_embedding"

    def __init__(self, session, embedder, model_name: str):
        self.session = session
        self.embedder = embedder
        self.model_name = model_name
        self.config = {"embeddingModel": model_name}
        self._vectors: dict[str, list[float]] = {}

    async def prepare(self, queries: Sequence[GoldQuery]) -> None:
        texts = [query.text for query in queries]
        if not texts:
            return
        vectors = await asyncio.to_thread(self.embedder.embed, texts)
        self._vectors = {query.text: vector.tolist() for query, vector in zip(queries, vectors)}

    async def _vector(self, query: GoldQuery) -> list[float]:
        if query.text not in self._vectors:
            vectors = await asyncio.to_thread(self.embedder.embed, [query.text])
            self._vectors[query.text] = vectors[0].tolist()
        return self._vectors[query.text]

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        vector = await self._vector(query)
        distance = Embedding.embedding.cosine_distance(vector)
        rows = await self.session.execute(
            select(Embedding.image_id, distance)
            .where(Embedding.image_id.in_([uuid.UUID(image_id) for image_id in corpus_ids]),
                   Embedding.model_name == self.model_name)
            .order_by(distance, Embedding.image_id)
            .limit(k)
        )
        return [(str(image_id), 1.0 - float(value)) for image_id, value in rows.all()]
