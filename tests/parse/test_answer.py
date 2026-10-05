import json

import pytest

from parse.answer import UnusableAnswer, interpret_answer
from taxonomy import load

TAX = load()


def _answer(**overrides):
    answer = {
        "language": "en", "situation": "A friend got promoted.",
        "emotion": [{"key": "joy", "confidence": "high"}, {"key": "excitement", "confidence": "medium"}],
        "stance": [{"key": "congratulate", "confidence": "high"}],
        "topic": [{"key": "work", "confidence": "medium"}],
        "tone": [{"key": "wholesome", "confidence": "low"}],
    }
    answer.update(overrides)
    return json.dumps(answer)


def test_valid_answer_becomes_a_queryprofile_shaped_dict():
    profile = interpret_answer(_answer(), TAX, "p3-test")

    assert profile["taxonomyVersion"] == TAX.version
    assert profile["language"] == "en" and profile["parserVersion"] == "p3-test"
    assert profile["degraded"] is False
    assert profile["situation"] == "A friend got promoted." and profile["situationConfidence"] is None
    assert profile["emotion"] == [{"key": "joy", "confidence": 0.9, "source": "model"},
                                  {"key": "excitement", "confidence": 0.6, "source": "model"}]
    assert profile["tone"] == [{"key": "wholesome", "confidence": 0.3, "source": "model"}]
    assert set(profile) == {"taxonomyVersion", "language", "parserVersion", "degraded", "situation",
                            "situationConfidence", *TAX.controlled_axes()}


@pytest.mark.parametrize("raw, fragment", [
    ("not json", "not valid JSON"),
    ("[1, 2]", "JSON object"),
    (None, "not valid JSON"),
    (_answer(language="fr"), "language"),
    (_answer(language=None), "language"),
    (_answer(emotion="joy"), "emotion"),
    (_answer(emotion=["joy"]), "emotion"),
    (_answer(emotion=[{"key": "joy", "confidence": "certain"}]), "confidence"),
    (_answer(emotion=[{"key": "joy", "confidence": ["high"]}]), "confidence"),
    (_answer(emotion=[{"key": "not_a_key", "confidence": "high"}]), "unknown key"),
    (_answer(stance=[{"key": "mock", "confidence": "high"}, {"key": "agree", "confidence": "high"},
                     {"key": "refuse", "confidence": "high"}]), "stance"),
    (_answer(situation="x" * (TAX.axes["situation"].max_chars + 1)), "situation"),
    (_answer(situation=5), "situation"),
])
def test_unusable_answers_raise_with_a_reason(raw, fragment):
    with pytest.raises(UnusableAnswer, match=fragment):
        interpret_answer(raw, TAX, "p3-test")


def test_an_answer_with_nothing_in_it_is_unusable():
    empty = _answer(situation="", emotion=[], stance=[], topic=[], tone=[])
    with pytest.raises(UnusableAnswer, match="no situation and no values"):
        interpret_answer(empty, TAX, "p3-test")


def test_an_answer_with_only_a_situation_or_only_values_is_usable():
    only_situation = _answer(emotion=[], stance=[], topic=[], tone=[])
    only_values = _answer(situation="")
    assert interpret_answer(only_situation, TAX, "v")["situation"] == "A friend got promoted."
    assert interpret_answer(only_values, TAX, "v")["situation"] is None
    assert interpret_answer(only_values, TAX, "v")["stance"][0]["key"] == "congratulate"


def test_missing_axis_lists_default_to_empty():
    raw = json.dumps({"language": "ru", "situation": "A neighbour is noisy."})
    profile = interpret_answer(raw, TAX, "v")
    assert profile["language"] == "ru" and profile["emotion"] == []
