import pytest

from retrieval.model import Match
from retrieval.scoring import axis_score, situation_score
from tests.retrieval.helpers import make_similarity

SIM = make_similarity()


def test_situation_score_rescales_between_floor_and_ceiling():
    assert situation_score(0.65, 0.45, 0.85, 0.3) == (pytest.approx(0.5), False)
    assert situation_score(0.30, 0.45, 0.85, 0.3) == (0.0, False)
    assert situation_score(0.95, 0.45, 0.85, 0.3) == (1.0, False)


def test_situation_score_is_neutral_without_an_embedding():
    assert situation_score(None, 0.45, 0.85, 0.3) == (0.3, True)


def test_exact_key_scores_the_meme_confidence():
    score, neutral, matched = axis_score("emotion", (("anger", 0.9),), (("anger", 0.6),), SIM, 0.3, True)

    assert score == pytest.approx(0.6) and neutral is False
    assert matched == (Match("anger", "anger", 1.0, 0.6),)


def test_a_curated_neighbour_scores_similarity_times_confidence():
    score, neutral, matched = axis_score("emotion", (("anger", 0.9),), (("annoyance", 0.6),), SIM, 0.3, True)

    assert score == pytest.approx(0.8 * 0.6) and neutral is False
    assert matched == (Match("anger", "annoyance", 0.8, 0.6),)


def test_neighbours_can_be_switched_off():
    score, _neutral, matched = axis_score("emotion", (("anger", 0.9),), (("annoyance", 0.6),), SIM, 0.3, False)
    assert score == 0.0 and matched == ()


def test_query_values_are_weighted_by_their_confidence():
    score, _n, matched = axis_score("emotion", (("anger", 0.9), ("joy", 0.3)), (("anger", 0.6),), SIM, 0.3, True)

    assert score == pytest.approx((0.9 * 0.6 + 0.3 * 0.0) / 1.2)
    assert [m.query_key for m in matched] == ["anger"]


def test_the_best_meme_value_wins_per_query_value():
    score, _n, matched = axis_score(
        "emotion", (("anger", 0.9),), (("annoyance", 0.9), ("anger", 0.3)), SIM, 0.3, True)

    assert score == pytest.approx(max(0.8 * 0.9, 1.0 * 0.3)) and matched[0].meme_key == "annoyance"


def test_a_meme_without_values_on_the_axis_is_neutral_not_zero():
    assert axis_score("emotion", (("anger", 0.9),), (), SIM, 0.3, True) == (0.3, True, ())


def test_zero_total_query_confidence_falls_back_to_equal_weights():
    score, _n, _m = axis_score("emotion", (("anger", 0.0),), (("anger", 0.6),), SIM, 0.3, True)
    assert score == pytest.approx(0.6)
