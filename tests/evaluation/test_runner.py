import pytest

from evaluation.gold import ExpectedProfile, GoldQuery
from evaluation.runner import QueryResult, run_retriever, summarize

EXPECTED = ExpectedProfile("s", ("joy",), ("agree",), ("work",), ("wholesome",))


def _query(query_id, language="en", reviewed=True, length="short", difficulty="easy"):
    return GoldQuery(query_id, language, "text", length, difficulty, EXPECTED, reviewed)


class FakeRetriever:
    name = "fake"
    config = {"k": 1}

    def __init__(self, rankings, with_prepare=False):
        self.rankings = rankings
        self.prepared = None
        if with_prepare:
            self.prepare = self._prepare

    async def _prepare(self, queries):
        self.prepared = [q.id for q in queries]

    async def retrieve(self, query, corpus_ids, k):
        return [(image_id, 1.0) for image_id in self.rankings.get(query.id, [])][:k]


@pytest.mark.asyncio
async def test_only_reviewed_queries_run_and_metrics_use_the_judgments():
    queries = [_query("q1"), _query("q2", reviewed=False), _query("q3", language="ru")]
    judgments = {"q1": {"a": 2, "b": 0}, "q3": {"c": 1}}
    retriever = FakeRetriever({"q1": ["a", "b"], "q2": ["a"], "q3": ["x"]})

    results = await run_retriever(retriever, queries, ["a", "b", "c", "x"], judgments, k=10)

    assert [r.query_id for r in results] == ["q1", "q3"]
    assert results[0].metrics.hit == 1.0 and results[0].ranked == ("a", "b")
    assert results[1].metrics.hit == 0.0
    assert (results[1].language, results[1].length, results[1].difficulty) == ("ru", "short", "easy")


@pytest.mark.asyncio
async def test_prepare_is_called_once_with_the_reviewed_queries_when_the_retriever_has_it():
    retriever = FakeRetriever({}, with_prepare=True)

    await run_retriever(retriever, [_query("q1"), _query("q2", reviewed=False)], ["a"], {}, k=10)

    assert retriever.prepared == ["q1"]


@pytest.mark.asyncio
async def test_results_are_truncated_to_k():
    retriever = FakeRetriever({"q1": ["a", "b", "c"]})

    results = await run_retriever(retriever, [_query("q1")], ["a", "b", "c"], {"q1": {"c": 2}}, k=2)

    assert results[0].ranked == ("a", "b") and results[0].metrics.hit == 0.0


def test_summarize_groups_by_language_difficulty_length_and_stratum():
    from evaluation.metrics import QueryMetrics

    def result(query_id, language, difficulty, ranked, ndcg):
        return QueryResult(query_id, language, "short", difficulty, tuple(ranked),
                           QueryMetrics(ndcg, ndcg, ndcg, 1.0, True))

    results = [result("q1", "en", "easy", ["a", "b"], 1.0), result("q2", "ru", "hard", ["c"], 0.5)]
    strata = {"a": "ocr only", "b": "ocr+description", "c": "ocr only"}
    judgments = {"q1": {"a": 2, "b": 0}, "q2": {"c": 1}}

    summary = summarize(results, strata, judgments, k=10)

    assert summary["overall"]["queries"] == 2 and summary["overall"]["ndcg"] == 0.75
    assert summary["byLanguage"]["en"]["ndcg"] == 1.0 and summary["byLanguage"]["ru"]["ndcg"] == 0.5
    assert summary["byDifficulty"]["hard"]["queries"] == 1
    assert summary["byLength"]["short"]["queries"] == 2
    assert summary["languageRatio"] == {"en": 1.0, "ru": 0.5}
    ocr_only = summary["byStratum"]["ocr only"]
    assert ocr_only["retrievedShare"] == pytest.approx((0.5 + 1.0) / 2)      # q1: 1 of 2, q2: 1 of 1
    assert ocr_only["meanGradeJudged"] == pytest.approx((2 + 1) / 2)
    assert ocr_only["judgedCount"] == 2
    assert summary["byStratum"]["ocr+description"]["meanGradeJudged"] == 0.0
