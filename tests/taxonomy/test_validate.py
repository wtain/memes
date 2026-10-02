from pathlib import Path

import pytest

from taxonomy.loader import load
from taxonomy.validate import validate_profile

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


@pytest.fixture(scope="module")
def tax():
    return load(FIXTURE)


def test_valid_profile_is_normalised(tax):
    result = validate_profile({
        "situation": "  plans cancelled  ",
        "situationConfidence": 0.8,
        "emotion": [{"key": "anger", "confidence": 0.4}, {"key": "joy", "confidence": 0.9, "source": "human"}],
        "stance": [{"key": "agree"}],
    }, tax)
    assert result.ok, result.errors
    assert result.profile["situation"] == "plans cancelled"
    assert result.profile["situationConfidence"] == 0.8
    assert result.profile["emotion"] == [
        {"key": "joy", "confidence": 0.9, "source": "human"},
        {"key": "anger", "confidence": 0.4, "source": "model"},
    ]
    assert result.profile["stance"] == [{"key": "agree", "confidence": 1.0, "source": "model"}]


def test_empty_profile_is_valid_and_has_every_axis(tax):
    result = validate_profile({}, tax)
    assert result.ok
    assert result.profile == {"situation": None, "situationConfidence": None, "emotion": [], "stance": []}


def test_blank_situation_becomes_none(tax):
    assert validate_profile({"situation": "   "}, tax).profile["situation"] is None


def test_unknown_key_is_an_error(tax):
    result = validate_profile({"emotion": [{"key": "euphoria"}]}, tax)
    assert not result.ok
    assert result.errors == ["emotion: unknown key 'euphoria'"]
    assert result.profile["emotion"] == []


def test_key_from_another_axis_is_unknown(tax):
    assert not validate_profile({"emotion": [{"key": "agree"}]}, tax).ok


def test_too_many_values_is_an_error(tax):
    result = validate_profile({"stance": [{"key": "agree"}, {"key": "refuse"}]}, tax)
    assert result.errors == ["stance: 2 values exceed the limit of 1"]


def test_duplicate_keys_merge_keeping_the_highest_confidence(tax):
    result = validate_profile({"emotion": [{"key": "joy", "confidence": 0.2}, {"key": "joy", "confidence": 0.7}]}, tax)
    assert result.ok
    assert result.profile["emotion"] == [{"key": "joy", "confidence": 0.7, "source": "model"}]


@pytest.mark.parametrize("confidence", [-0.1, 1.5, "high", True, None])
def test_bad_confidence_is_an_error(tax, confidence):
    result = validate_profile({"emotion": [{"key": "joy", "confidence": confidence}]}, tax)
    assert not result.ok


def test_bad_source_is_an_error(tax):
    assert not validate_profile({"emotion": [{"key": "joy", "source": "robot"}]}, tax).ok


def test_overlong_situation_is_an_error(tax):
    result = validate_profile({"situation": "x" * 41}, tax)
    assert result.errors == ["situation: longer than 40 characters"]


def test_non_string_situation_is_an_error(tax):
    assert not validate_profile({"situation": 5}, tax).ok


def test_situation_confidence_out_of_range_is_an_error(tax):
    assert not validate_profile({"situationConfidence": 2}, tax).ok


def test_non_list_axis_is_an_error(tax):
    assert not validate_profile({"emotion": "joy"}, tax).ok


def test_non_object_value_is_an_error(tax):
    assert not validate_profile({"emotion": ["joy"]}, tax).ok
