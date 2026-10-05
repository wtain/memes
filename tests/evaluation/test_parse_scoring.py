import pytest

from evaluation.gold import ExpectedProfile
from evaluation.parse_scoring import (
    AxisScore, aggregate_parse, build_parse_report, format_parse_report, load_parse_report,
    percentile, save_parse_report, score_axis, score_query, summarize_parse,
)


def test_score_axis_set_precision_recall_f1_and_top1():
    score = score_axis(["a", "b"], ["a"])
    assert (score.precision, score.recall, score.top1) == (1.0, 0.5, True)
    assert score.f1 == pytest.approx(2 * 1.0 * 0.5 / 1.5)

    score = score_axis(["a"], ["b", "a"])
    assert (score.precision, score.recall, score.top1) == (0.5, 1.0, False)
    assert score.f1 == pytest.approx(2 * 0.5 * 1.0 / 1.5)

    assert score_axis(["a"], ["b"]) == AxisScore(0.0, 0.0, 0.0, False)


def test_score_axis_with_empty_sets():
    assert score_axis([], []) == AxisScore(1.0, 1.0, 1.0, None)
    assert score_axis([], ["a"]) == AxisScore(0.0, 0.0, 0.0, None)
    assert score_axis(["a"], []) == AxisScore(0.0, 0.0, 0.0, False)


def test_percentile_is_nearest_rank():
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([1, 2, 3, 4, 5], 95) == 5
    assert percentile([5, 1, 3], 50) == 3
    assert percentile([7], 95) == 7
    assert percentile([], 50) is None


EXPECTED = ExpectedProfile("s", ("joy",), ("congratulate",), ("work",), ("wholesome",))


def _profile(**overrides):
    base = {"language": "en", "degraded": False,
            "emotion": [{"key": "joy", "confidence": 0.9, "source": "model"}],
            "stance": [{"key": "mock", "confidence": 0.9, "source": "model"}],
            "topic": [{"key": "work", "confidence": 0.6, "source": "model"}],
            "tone": []}
    base.update(overrides)
    return base


def _score(query_id="q1", language="en", profile=None, latency=1000, similarity=0.5, fallback="none"):
    return score_query(query_id, language, EXPECTED, profile or _profile(),
                       {"fallback": fallback, "latencyMs": latency}, similarity)


def test_score_query_extracts_keys_per_axis_and_copies_meta():
    score = _score()

    assert score.axes["emotion"].f1 == 1.0 and score.axes["stance"].f1 == 0.0
    assert score.axes["topic"].f1 == 1.0 and score.axes["tone"].f1 == 0.0
    assert (score.language, score.detected_language, score.degraded) == ("en", "en", False)
    assert (score.fallback, score.latency_ms, score.situation_similarity) == ("none", 1000, 0.5)


def test_aggregate_parse_means_rates_and_latency_percentiles():
    scores = [
        _score("q1", latency=1000, similarity=0.4),
        _score("q2", profile=_profile(language="ru", degraded=True), latency=3000, similarity=None, fallback="timeout"),
    ]

    summary = aggregate_parse(scores)

    assert summary["queries"] == 2
    assert summary["languageAccuracy"] == 0.5                  # the second query was detected as ru, expected en
    assert summary["degradedRate"] == 0.5
    assert summary["axes"]["emotion"]["f1"] == 1.0
    assert summary["axes"]["stance"]["top1"] == 0.0
    assert summary["macroF1"] == pytest.approx((1.0 + 0.0 + 1.0 + 0.0) / 4)
    assert summary["situationSimilarity"] == 0.4
    assert (summary["latencyP50"], summary["latencyP95"]) == (1000, 3000)


def test_aggregate_parse_of_nothing_is_all_none():
    summary = aggregate_parse([])
    assert summary["queries"] == 0 and summary["macroF1"] is None and summary["latencyP50"] is None


def test_summarize_parse_groups_by_language():
    scores = [_score("q1", "en"), _score("q2", "ru", profile=_profile(language="ru"))]

    summary = summarize_parse(scores)

    assert summary["overall"]["queries"] == 2
    assert set(summary["byLanguage"]) == {"en", "ru"} and summary["byLanguage"]["ru"]["languageAccuracy"] == 1.0


def test_report_round_trip_format_and_no_file_names(tmp_path):
    scores = [_score("q1", "en"), _score("q2", "ru", profile=_profile(language="ru"))]
    parsed = {"q1": {"situation": "A friend got promoted.", "emotion": ["joy"], "stance": ["mock"],
                     "topic": ["work"], "tone": []},
              "q2": {"situation": None, "emotion": [], "stance": [], "topic": [], "tone": []}}

    report = build_parse_report(label="m1", date="2026-10-06", model="qwen2.5:7b-instruct", parser_version="p3-x",
                                taxonomy_version=2, queries_sha="abc", scores=scores, parsed=parsed)
    path = tmp_path / "m1.json"
    save_parse_report(path, report)

    assert load_parse_report(path) == report
    assert path.read_bytes().endswith(b"}\n") and b"\r" not in path.read_bytes()
    assert report["model"] == "qwen2.5:7b-instruct" and report["queriesSha"] == "abc"
    assert report["queries"]["q1"]["parsed"]["emotion"] == ["joy"]
    assert report["queries"]["q1"]["f1"]["stance"] == 0.0
    text = format_parse_report(report)
    assert text.startswith("# Parse evaluation: m1")
    for fragment in ("languageAccuracy", "degradedRate", "macroF1", "latency", "## By language"):
        assert fragment in text
