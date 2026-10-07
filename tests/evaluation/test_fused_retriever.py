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
        self.config = make_config()
        self.warmed = 0

    async def warm_up_models(self):
        self.warmed += 1

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


class MetaPipeline(FakePipeline):
    async def search(self, request, repository):
        result = await super().search(request, repository)
        meta = {"parseMs": 12, "embedMs": 3, "retrievalMs": 40, "candidates": 2}
        return SearchResult(result.items, {}, None, False, {}, True, "timeout", meta)


@pytest.mark.asyncio
async def test_retrieve_keeps_per_query_metadata_and_prepare_resets_it():
    retriever = FusedRetriever(object(), MetaPipeline(), repository_factory=lambda session: "repo")
    query = GoldQuery("q1", "ru", "привет", "short", "easy", ExpectedProfile("s", (), (), (), ()), True)

    await retriever.retrieve(query, [IMG_A], 1)

    assert retriever.query_meta == [{"queryId": "q1", "parseMs": 12, "embedMs": 3, "retrievalMs": 40,
                                     "fallback": "timeout", "degraded": True, "candidates": 2}]
    await retriever.prepare([query])
    assert retriever.query_meta == []


@pytest.mark.asyncio
async def test_prepare_warms_the_models_once_per_retriever():
    pipeline = FakePipeline()
    retriever = FusedRetriever(object(), pipeline, repository_factory=lambda session: "repo")
    await retriever.prepare([])
    await retriever.prepare([])
    assert pipeline.warmed == 1
