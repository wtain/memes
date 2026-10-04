import math

import pytest

from evaluation.metrics import QueryMetrics, aggregate, language_ratio, mean, query_metrics


def test_query_metrics_hand_computed_example():
    # ranked a, b, c; grades a=2 b=0 c=1 and d=2 was never retrieved
    result = query_metrics(["a", "b", "c"], {"a": 2, "b": 0, "c": 1, "d": 2}, k=10)

    dcg = 2 / math.log2(2) + 0 + 1 / math.log2(4)                       # 2.5
    ideal = 2 / math.log2(2) + 2 / math.log2(3) + 1 / math.log2(4)      # grades 2, 2, 1
    assert result.ndcg == pytest.approx(dcg / ideal)
    assert result.recall == pytest.approx(2 / 3)                        # positives a, c, d; found a, c
    assert result.hit == 1.0
    assert result.judged_share == 1.0
    assert result.has_positive is True


def test_perfect_ranking_has_ndcg_one():
    result = query_metrics(["a", "c", "b"], {"a": 2, "b": 0, "c": 1}, k=10)
    assert result.ndcg == pytest.approx(1.0)


def test_unjudged_results_count_as_grade_zero_and_lower_judged_share():
    result = query_metrics(["x", "y", "a"], {"a": 2}, k=10)

    assert result.ndcg == pytest.approx((2 / math.log2(4)) / 2)
    assert result.judged_share == pytest.approx(1 / 3)
    assert result.hit == 1.0


def test_only_the_top_k_counts():
    ranked = ["n1", "n2", "a"]
    assert query_metrics(ranked, {"a": 2}, k=2).hit == 0.0
    assert query_metrics(ranked, {"a": 2}, k=3).hit == 1.0


def test_query_without_a_judged_positive_has_undefined_ranking_metrics():
    result = query_metrics(["a", "b"], {"a": 0, "b": 0}, k=10)

    assert (result.ndcg, result.recall, result.hit) == (None, None, None)
    assert result.has_positive is False
    assert result.judged_share == 1.0


def test_empty_result_list_scores_zero_with_undefined_judged_share():
    result = query_metrics([], {"a": 2}, k=10)

    assert result.ndcg == 0.0 and result.recall == 0.0 and result.hit == 0.0
    assert result.judged_share is None


def test_query_with_no_judgments_at_all_is_excluded_not_zero():
    result = query_metrics(["a"], {}, k=10)
    assert result.has_positive is False and result.ndcg is None


def test_mean_ignores_none_and_is_none_for_nothing():
    assert mean([1.0, None, 3.0]) == 2.0
    assert mean([None, None]) is None
    assert mean([]) is None


def test_aggregate_means_over_defined_values_and_counts_no_positive_queries():
    items = [
        QueryMetrics(1.0, 1.0, 1.0, 1.0, True),
        QueryMetrics(0.0, 0.0, 0.0, 0.5, True),
        QueryMetrics(None, None, None, 1.0, False),
    ]

    summary = aggregate(items)

    assert summary == {"queries": 3, "noPositive": 1, "ndcg": 0.5, "recall": 0.5, "hit": 0.5,
                       "judged": pytest.approx(2.5 / 3)}


def test_language_ratio_is_relative_to_english():
    by_language = {"en": {"ndcg": 0.5}, "ru": {"ndcg": 0.25}, "es": {"ndcg": None}}

    assert language_ratio(by_language) == {"en": 1.0, "ru": 0.5, "es": None}


def test_language_ratio_is_none_everywhere_without_an_english_baseline():
    assert language_ratio({"ru": {"ndcg": 0.5}}) == {"ru": None}
    assert language_ratio({"en": {"ndcg": 0.0}, "ru": {"ndcg": 0.5}}) == {"en": None, "ru": None}
