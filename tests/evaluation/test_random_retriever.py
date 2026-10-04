import pytest

from evaluation.gold import ExpectedProfile, GoldQuery
from evaluation.retrievers.random_retriever import RandomRetriever

EXPECTED = ExpectedProfile("s", (), (), (), ())


def _query(query_id):
    return GoldQuery(query_id, "en", "text", "short", "easy", EXPECTED, True)


@pytest.mark.asyncio
async def test_random_retriever_is_deterministic_per_query_and_seed_and_restricted_to_the_corpus():
    corpus = [f"img-{i:03d}" for i in range(50)]
    retriever = RandomRetriever(seed=3)

    first = await retriever.retrieve(_query("q1"), corpus, 10)
    again = await retriever.retrieve(_query("q1"), corpus, 10)
    other_query = await retriever.retrieve(_query("q2"), corpus, 10)
    other_seed = await RandomRetriever(seed=4).retrieve(_query("q1"), corpus, 10)

    assert first == again and len(first) == 10
    assert first != other_query and first != other_seed
    assert {image_id for image_id, _ in first} <= set(corpus)
    scores = [score for _, score in first]
    assert scores == sorted(scores, reverse=True)
    assert RandomRetriever.name == "random" and RandomRetriever(seed=3).config == {"seed": 3}


@pytest.mark.asyncio
async def test_random_retriever_returns_everything_when_the_corpus_is_smaller_than_k():
    result = await RandomRetriever().retrieve(_query("q1"), ["a", "b"], 10)
    assert sorted(image_id for image_id, _ in result) == ["a", "b"]
