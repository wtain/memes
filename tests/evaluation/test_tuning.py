import pytest

from evaluation.tuning import (
    GRID, PreparedQuery, coordinate_ascent, cross_validate, evaluate_weights, stratified_folds, tune,
)
from retrieval.model import QueryView
from tests.retrieval.helpers import WEIGHTS, make_candidate, make_config, make_similarity

SIM = make_similarity()
CONFIG = make_config()


def _query(index, language="en"):
    """Situation similarity prefers the WRONG meme, the emotion axis prefers the RIGHT one."""
    view = QueryView(situation="x", axes={"emotion": (("anger", 0.9),)}, language=language, degraded=False)
    right = make_candidate(f"r{index}", cosine=0.55, values={"emotion": (("anger", 0.9),)})
    wrong = make_candidate(f"w{index}", cosine=0.85, values={"emotion": (("joy", 0.9),)})
    return PreparedQuery(f"q{index}", language, view, (wrong, right), False, {f"r{index}": 2, f"w{index}": 0})


def _prepared(count=10):
    languages = ["en", "ru", "es"]
    return [_query(i, languages[i % 3]) for i in range(count)]


def test_evaluate_weights_scores_the_ranking_with_the_given_weights():
    prepared = _prepared(3)
    situation_heavy = {**WEIGHTS, "situation": 1.0, "emotion": 0.0}
    emotion_heavy = {**WEIGHTS, "situation": 0.0, "emotion": 1.0}

    assert evaluate_weights(prepared, CONFIG, SIM, emotion_heavy) == pytest.approx(1.0)
    assert evaluate_weights(prepared, CONFIG, SIM, situation_heavy) < 1.0


def test_queries_without_a_judged_positive_are_ignored():
    view = QueryView("x", {}, "en", False)
    empty = PreparedQuery("q", "en", view, (make_candidate("a"),), False, {"a": 0})
    assert evaluate_weights([empty], CONFIG, SIM, dict(WEIGHTS)) is None


def test_coordinate_ascent_improves_a_bad_start_and_is_deterministic():
    prepared = _prepared(6)
    bad = {**WEIGHTS, "situation": 0.6, "emotion": 0.05}

    first = coordinate_ascent(prepared, CONFIG, SIM, bad)
    again = coordinate_ascent(prepared, CONFIG, SIM, bad)

    assert first == again
    assert first[1] > evaluate_weights(prepared, CONFIG, SIM, bad)
    assert first[1] == pytest.approx(1.0)
    assert set(first[0]) == set(WEIGHTS) and sum(first[0].values()) > 0 and all(w in GRID for w in first[0].values())


def test_stratified_folds_partition_every_item_once_and_balance_languages():
    items = _prepared(15)

    folds = stratified_folds(items, 5, seed=7)

    assert len(folds) == 5 and sorted(i.query_id for f in folds for i in f) == sorted(i.query_id for i in items)
    for fold in folds:
        assert {i.language for i in fold} == {"en", "ru", "es"}
    assert [[i.query_id for i in f] for f in stratified_folds(items, 5, seed=7)] == [[i.query_id for i in f] for f in folds]
    assert [[i.query_id for i in f] for f in stratified_folds(items, 5, seed=8)] != [[i.query_id for i in f] for f in folds]


def test_cross_validation_reports_held_out_scores_per_fold():
    report = cross_validate(_prepared(10), CONFIG, SIM, folds=5, seed=7)

    assert len(report["foldScores"]) == 5 and report["cvScore"] == pytest.approx(sum(report["foldScores"]) / 5)
    assert report["foldSpread"] == pytest.approx(max(report["foldScores"]) - min(report["foldScores"]))


def test_tune_returns_recommended_weights_with_in_sample_and_cross_validated_scores():
    report = tune(_prepared(10), CONFIG, SIM, folds=5, seed=7)

    assert set(report) == {"defaultWeights", "defaultScore", "recommendedWeights", "inSampleScore", "cvScore",
                           "foldScores", "foldSpread"}
    assert report["inSampleScore"] >= report["defaultScore"] and report["cvScore"] > 0.5
    assert report["defaultWeights"] == WEIGHTS


def _unscorable_view_query():
    view = QueryView(situation=None, axes={}, language="en", degraded=False)
    return PreparedQuery("qe", "en", view, (make_candidate("e1"),), False, {"e1": 2})


def test_a_query_with_no_present_axis_gets_an_empty_ranking_like_production():
    empty = _unscorable_view_query()
    perfect = _query(0)
    emotion_heavy = {**WEIGHTS, "situation": 0.0, "emotion": 1.0}

    assert evaluate_weights([empty], CONFIG, SIM, emotion_heavy) == pytest.approx(0.0)
    assert evaluate_weights([perfect], CONFIG, SIM, emotion_heavy) == pytest.approx(1.0)
    assert evaluate_weights([empty, perfect], CONFIG, SIM, emotion_heavy) == pytest.approx(0.5)


def test_cross_validation_skips_held_out_folds_without_a_judged_positive():
    judged = _prepared(2)
    unjudged = [PreparedQuery(f"u{i}", "en", judged[0].view, judged[0].candidates, False, {}) for i in range(4)]

    report = cross_validate(judged + unjudged, CONFIG, SIM, folds=5, seed=7)

    assert len(report["foldScores"]) == 2
    assert report["cvScore"] == pytest.approx(sum(report["foldScores"]) / 2)
    none_judged = cross_validate(unjudged, CONFIG, SIM, folds=5, seed=7)
    assert none_judged == {"foldScores": [], "cvScore": 0.0, "foldSpread": 0.0}


def test_fewer_than_two_folds_is_rejected():
    with pytest.raises(ValueError):
        stratified_folds(_prepared(4), 1, seed=7)
    with pytest.raises(ValueError):
        cross_validate(_prepared(4), CONFIG, SIM, folds=1)
