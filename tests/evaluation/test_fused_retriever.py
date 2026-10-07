import uuid

import pytest

from evaluation.gold import ExpectedProfile, GoldQuery
from evaluation.retrievers.fused import FusedRetriever
from retrieval.facets import RankedItem
from retrieval.model import ScoredCandidate
from retrieval.pipeline import SearchResult
from tests.retrieval.helpers import make_candidate, make_config

IMG_A, IMG_B = str(uuid.uuid4()), str(uuid.uuid4())


class FakePipeline:
    def __init__(self):
        self.requests = []
        self._config = make_config()

    async def search(self, request, repository):
        self.requests.append((request, repository))
        items = [RankedItem(ScoredCandidate(make_candidate(IMG_A), 0.9, ()), None),
                 RankedItem(ScoredCandidate(make_candidate(IMG_B), 0.5, ()), None)]
        return SearchResult(items[:request.limit], {}, None, False, {}, False, "none", {})


@pytest.mark.asyncio
async def test_retrieve_runs_the_pipeline_restricted_to_the_corpus_and_returns_scored_ids():
    pipeline = FakePipeline()
    retriever = FusedRetriever(object(), pipeline, repository_factory=lambda session: "repo")
    query = GoldQuery("q1", "ru", "привет", "short", "easy", ExpectedProfile("s", (), (), (), ()), True)

    result = await retriever.retrieve(query, [IMG_A, IMG_B], 1)

    assert result == [(IMG_A, 0.9)]
    request, repository = pipeline.requests[0]
    assert request.text == "привет" and request.language_hint == "ru" and request.limit == 1
    assert request.restrict_ids == frozenset({uuid.UUID(IMG_A), uuid.UUID(IMG_B)}) and repository == "repo"
    assert retriever.name == "fused" and "weights" in retriever.config
