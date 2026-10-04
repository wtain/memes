import json

import pytest

from evaluation.metrics import QueryMetrics
from evaluation.report import (
    CompareError, compare_baselines, format_report, load_baseline, make_baseline, pooling_bias, save_baseline,
)
from evaluation.runner import QueryResult, summarize

HASHES = {"corpusSha": "c1", "queriesSha": "q1", "judgmentsSha": "j1"}
STATE = {"profiled": 10, "taxonomyVersions": {"2": 10}}


def _result(query_id, language, ndcg):
    return QueryResult(query_id, language, "short", "easy", (f"img-{query_id}",),
                       QueryMetrics(ndcg, ndcg, 1.0 if ndcg else 0.0, 1.0, True))


def _baseline(label="b1", ndcgs=None, hashes=None):
    ndcgs = ndcgs or {"q1": 1.0, "q2": 0.5}
    results = [_result("q1", "en", ndcgs["q1"]), _result("q2", "ru", ndcgs["q2"])]
    summary = summarize(results, {"img-q1": "ocr only", "img-q2": "ocr only"}, {"q1": {"img-q1": 2}, "q2": {"img-q2": 1}})
    return make_baseline(label=label, retriever_name="fake", retriever_config={"x": 1}, date="2026-10-04",
                         git_commit="abc123", gold_hashes=hashes or HASHES, profile_state=STATE,
                         results=results, summary=summary)


def test_baseline_contains_identity_hashes_state_summary_and_per_query_metrics_but_no_file_names():
    baseline = _baseline()

    assert baseline["label"] == "b1" and baseline["retriever"] == {"name": "fake", "config": {"x": 1}}
    assert baseline["gitCommit"] == "abc123" and baseline["gold"] == HASHES and baseline["profileState"] == STATE
    assert baseline["summary"]["overall"]["queries"] == 2
    assert baseline["queries"]["q1"] == {"language": "en", "ndcg": 1.0, "recall": 1.0, "hit": 1.0, "judged": 1.0}
    assert "filename" not in json.dumps(baseline).lower()


def test_save_and_load_round_trip_is_deterministic(tmp_path):
    path = tmp_path / "b1.json"
    save_baseline(path, _baseline())
    first = path.read_bytes()

    assert load_baseline(path) == _baseline()
    save_baseline(path, _baseline())
    assert path.read_bytes() == first and first.endswith(b"}\n") and b"\r" not in first


def test_format_report_is_markdown_with_the_main_sections():
    report = format_report(_baseline(), bias={"lexical_or": 3, "random": 1, "unpooled": 2})

    assert report.startswith("# Evaluation: b1")
    for section in ("## Overall", "## By language", "## By difficulty", "## By length", "## By input stratum",
                    "## Pooling bias"):
        assert section in report
    assert "nDCG@10" in report and "recall@10" in report and "hit@10" in report and "judged@10" in report
    assert "unpooled" in report


def test_pooling_bias_counts_which_source_proposed_each_top_result():
    results = [_result("q1", "en", 1.0)]          # ranked = ("img-q1",)
    pool_sources = {"q1": {"img-q1": ["lexical_or", "axis_match"]}, "q2": {}}

    bias = pooling_bias(results, pool_sources)

    assert bias == {"lexical_or": 1, "axis_match": 1, "unpooled": 0, "topResults": 1}
    assert pooling_bias(results, {"q1": {}}) == {"unpooled": 1, "topResults": 1}


def test_compare_prints_deltas_and_the_queries_that_got_worse():
    before = _baseline("before", {"q1": 1.0, "q2": 0.5})
    after = _baseline("after", {"q1": 0.6, "q2": 0.5})

    text = compare_baselines(before, after, query_texts={"q1": "boss moved the deadline", "q2": "other"})

    assert "before -> after" in text
    assert "-0.200" in text                                   # overall nDCG mean 0.75 -> 0.55
    assert "q1" in text and "boss moved the deadline" in text and "-0.400" in text
    assert "q2" not in text.split("got worse")[1]


def test_compare_refuses_different_gold_data_unless_forced():
    before = _baseline("before")
    after = _baseline("after", hashes={**HASHES, "judgmentsSha": "j2"})

    with pytest.raises(CompareError, match="judgmentsSha"):
        compare_baselines(before, after)
    assert "before -> after" in compare_baselines(before, after, force=True)


def test_compare_with_no_regression_says_so():
    text = compare_baselines(_baseline("a"), _baseline("b"))
    assert "No query got worse" in text


def test_compare_threshold_is_not_defeated_by_float_noise():
    worse = compare_baselines(_baseline("a", {"q1": 0.6, "q2": 0.5}), _baseline("b", {"q1": 0.5, "q2": 0.5}))
    assert "- q1" in worse.split("got worse")[1]
    fine = compare_baselines(_baseline("a", {"q1": 0.6, "q2": 0.5}), _baseline("b", {"q1": 0.51, "q2": 0.5}))
    assert "No query got worse" in fine
