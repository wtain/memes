import pytest

from retrieval.fusion import explanation_dict, fuse, rank_candidates
from retrieval.model import QueryView
from tests.retrieval.helpers import make_candidate, make_config, make_similarity

SIM = make_similarity()
CONFIG = make_config()


def _view(situation="x", **axes):
    return QueryView(situation=situation, axes={a: tuple(v) for a, v in axes.items()}, language="en", degraded=False)


def test_fusion_renormalises_the_weights_over_the_present_axes():
    candidate = make_candidate("a", cosine=0.85, values={"emotion": (("anger", 0.9),)})

    scored = fuse(candidate, _view(emotion=[("anger", 0.9)]), CONFIG, SIM, lexical_active=False)

    assert scored.score == pytest.approx((0.4 * 1.0 + 0.15 * 0.9) / 0.55, abs=1e-5)
    by_axis = {a.axis: a for a in scored.axes}
    assert set(by_axis) == {"situation", "emotion"}
    assert by_axis["situation"].weight == pytest.approx(0.4 / 0.55, abs=1e-5)
    assert by_axis["emotion"].contribution == pytest.approx(0.15 / 0.55 * 0.9, abs=1e-5)
    assert sum(a.contribution for a in scored.axes) == pytest.approx(scored.score, abs=1e-5)


def test_an_axis_absent_from_the_query_contributes_nothing():
    candidate = make_candidate("a", cosine=0.85, values={"tone": (("sarcastic", 0.9),)})

    scored = fuse(candidate, _view(emotion=[("anger", 0.9)]), CONFIG, SIM, lexical_active=False)

    assert "tone" not in {a.axis for a in scored.axes}


def test_a_meme_with_no_value_on_a_queried_axis_scores_neutral():
    candidate = make_candidate("a", cosine=0.85, values={})

    scored = fuse(candidate, _view(stance=[("mock", 0.9)]), CONFIG, SIM, lexical_active=False)

    stance = next(a for a in scored.axes if a.axis == "stance")
    assert stance.neutral is True and stance.score == pytest.approx(0.3) and stance.matched == ()


def test_a_meme_without_a_profile_is_neutral_on_every_axis_but_keeps_its_lexical_score():
    candidate = make_candidate("a", cosine=None, values={}, lexical=0.8, axis_path=False)

    scored = fuse(candidate, _view(emotion=[("anger", 0.9)]), CONFIG, SIM, lexical_active=True)

    by_axis = {a.axis: a for a in scored.axes}
    assert by_axis["situation"].neutral and by_axis["emotion"].neutral
    assert by_axis["lexical"].score == pytest.approx(0.8) and by_axis["lexical"].neutral is False


def test_lexical_is_an_axis_only_when_active():
    candidate = make_candidate("a", lexical=0.5)
    without = fuse(candidate, _view(), CONFIG, SIM, lexical_active=False)
    with_lexical = fuse(candidate, _view(), CONFIG, SIM, lexical_active=True)

    assert {a.axis for a in without.axes} == {"situation"}
    assert {a.axis for a in with_lexical.axes} == {"situation", "lexical"}


def test_a_query_with_nothing_to_score_gives_zero():
    scored = fuse(make_candidate("a"), _view(situation=None), CONFIG, SIM, lexical_active=False)
    assert scored.score == 0.0 and scored.axes == ()


def test_ranking_is_by_score_then_image_id():
    view = _view()
    scored = [fuse(make_candidate(i, cosine=c), view, CONFIG, SIM, lexical_active=False)
              for i, c in (("b", 0.85), ("a", 0.85), ("c", 0.65))]

    assert [s.candidate.image_id for s in rank_candidates(scored)] == ["a", "b", "c"]


def test_explanation_dict_has_the_public_shape():
    candidate = make_candidate("a", cosine=0.85, values={"emotion": (("annoyance", 0.6),)},
                               situation_text="Boss moved the deadline.", use_when="Use when plans change.")
    scored = fuse(candidate, _view(emotion=[("anger", 0.9)]), CONFIG, SIM, lexical_active=False)

    explanation = explanation_dict(scored)

    assert explanation["situationText"] == "Boss moved the deadline." and explanation["useWhen"] == "Use when plans change."
    emotion = next(a for a in explanation["axes"] if a["axis"] == "emotion")
    assert set(emotion) == {"axis", "score", "weight", "contribution", "neutral", "queryValues", "matched"}
    assert emotion["queryValues"] == ["anger"]
    assert emotion["matched"] == [{"queryKey": "anger", "memeKey": "annoyance", "similarity": 0.8, "memeConfidence": 0.6}]


def test_query_view_from_a_queryprofile_dict():
    profile = {"situation": " A friend got promoted. ", "language": "ru", "degraded": False,
               "emotion": [{"key": "joy", "confidence": 0.9, "source": "model"}], "stance": [], "topic": [], "tone": []}

    view = QueryView.from_profile(profile)

    assert view.situation == "A friend got promoted." and view.language == "ru"
    assert view.axes == {"emotion": (("joy", 0.9),)}
    assert view.present_axes(lexical_active=True) == ("situation", "emotion", "lexical")
    assert QueryView.from_profile({"situation": None, "emotion": []}).present_axes(lexical_active=False) == ()
