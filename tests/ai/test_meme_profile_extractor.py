import json
from pathlib import Path

import ollama
import pytest

from ai.meme_profile_extractor import (
    MAX_USE_WHEN_CHARS, ExtractionResult, OllamaProfileExtractor, ProfileExtractionError,
)
from taxonomy.loader import load

TAX = load(Path(__file__).resolve().parents[1] / "taxonomy" / "fixtures" / "mini")


def _answer(**overrides):
    data = {
        "situation": "plans cancelled",
        "useWhen": "Use when plans fall through.",
        "emotion": [{"key": "anger", "confidence": "high"}],
        "stance": [{"key": "agree", "confidence": "low"}],
    }
    data.update(overrides)
    return json.dumps(data)


def _patch_chat(monkeypatch, *contents):
    calls = []
    queue = list(contents)

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return {"message": {"content": queue.pop(0)}}

    monkeypatch.setattr(ollama, "chat", fake_chat)
    return calls


def _extract(**overrides):
    extractor = OllamaProfileExtractor(TAX, seed=42, num_ctx=4096)
    kwargs = dict(model="text-model", note=None, descriptions=[("general_description", "a cat")], ocr="HELLO")
    kwargs.update(overrides)
    return extractor.extract(**kwargs)


def test_valid_answer_becomes_a_validated_profile(monkeypatch):
    _patch_chat(monkeypatch, _answer())

    result = _extract()

    assert isinstance(result, ExtractionResult)
    assert result.use_when == "Use when plans fall through."
    assert result.profile["situation"] == "plans cancelled"
    assert result.profile["situationConfidence"] is None
    assert result.profile["emotion"] == [{"key": "anger", "confidence": 0.9, "source": "model"}]
    assert result.profile["stance"] == [{"key": "agree", "confidence": 0.3, "source": "model"}]


def test_call_uses_the_schema_temperature_zero_seed_and_context(monkeypatch):
    calls = _patch_chat(monkeypatch, _answer())

    _extract()

    call = calls[0]
    assert call["model"] == "text-model"
    assert call["format"]["required"] == ["situation", "useWhen", "emotion", "stance"]
    assert call["options"] == {"temperature": 0, "seed": 42, "num_ctx": 4096}
    assert "images" not in call["messages"][-1]


def test_vision_path_attaches_the_image_to_the_user_message(monkeypatch):
    calls = _patch_chat(monkeypatch, _answer())

    _extract(model="vision-model", descriptions=[], ocr=None, image_path="/tmp/meme.jpg")

    assert calls[0]["messages"][-1]["images"] == ["/tmp/meme.jpg"]
    assert "image is attached" in calls[0]["messages"][-1]["content"]


def test_invalid_answer_is_retried_once_with_the_reason(monkeypatch):
    calls = _patch_chat(monkeypatch, "not json at all", _answer())

    result = _extract()

    assert result.profile["situation"] == "plans cancelled"
    assert len(calls) == 2
    retry_messages = calls[1]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": "not json at all"}
    assert "invalid" in retry_messages[-1]["content"]


def test_two_invalid_answers_raise_profile_extraction_error(monkeypatch):
    calls = _patch_chat(monkeypatch, "nope", "still nope")

    with pytest.raises(ProfileExtractionError):
        _extract()

    assert len(calls) == 2


@pytest.mark.parametrize("bad,expected_fragment", [
    ({"emotion": [{"key": "euphoria", "confidence": "high"}]}, "unknown"),                           # key outside the vocabulary
    ({"stance": [{"key": "agree", "confidence": "low"}, {"key": "refuse", "confidence": "low"}]}, "exceed"),  # over max_values
    ({"emotion": [{"key": "joy", "confidence": "certain"}]}, "confidence must be one of"),          # confidence level not allowed
    ({"situation": "x" * 41}, "longer than"),                                                       # over max_chars
    ({"useWhen": "y" * (MAX_USE_WHEN_CHARS + 1)}, "longer than"),                                  # over the use-when limit
    ({"emotion": "joy"}, "must be a list"),                                                         # not a list
])
def test_answers_that_break_the_contract_are_rejected(monkeypatch, bad, expected_fragment):
    calls = _patch_chat(monkeypatch, _answer(**bad), _answer(**bad))

    with pytest.raises(ProfileExtractionError):
        _extract()

    # Verify the error reason is included in the retry message on the second call
    retry_message = calls[1]["messages"][-1]["content"]
    assert expected_fragment in retry_message


def test_empty_situation_and_use_when_become_none(monkeypatch):
    _patch_chat(monkeypatch, _answer(situation="", useWhen=""))

    result = _extract()

    assert result.profile["situation"] is None
    assert result.use_when is None


def test_a_non_object_answer_is_invalid(monkeypatch):
    _patch_chat(monkeypatch, "[1, 2]", "[1, 2]")

    with pytest.raises(ProfileExtractionError):
        _extract()


def test_all_empty_answer_twice_raises_error(monkeypatch):
    """Test that an answer with no situation, no use-when, and no values is treated as failed."""
    calls = _patch_chat(monkeypatch, _answer(situation="", useWhen="", emotion=[], stance=[]),
                                     _answer(situation="", useWhen="", emotion=[], stance=[]))

    with pytest.raises(ProfileExtractionError) as exc_info:
        _extract()

    assert "empty" in str(exc_info.value)
    assert len(calls) == 2


def test_empty_answer_followed_by_valid_succeeds(monkeypatch):
    """Test that an empty answer is retried with the reason in the message."""
    calls = _patch_chat(monkeypatch, _answer(situation="", useWhen="", emotion=[], stance=[]),
                                     _answer())

    result = _extract()

    assert result.profile["situation"] == "plans cancelled"
    assert result.use_when == "Use when plans fall through."
    assert len(calls) == 2
    retry_message = calls[1]["messages"][-1]["content"]
    assert "empty" in retry_message


def test_answer_with_only_situation_is_valid(monkeypatch):
    """Test that an answer with only a situation (no use-when, no values) is valid."""
    _patch_chat(monkeypatch, _answer(useWhen="", emotion=[], stance=[]))

    result = _extract()

    assert result.profile["situation"] == "plans cancelled"
    assert result.use_when is None
    assert result.profile["emotion"] == []
    assert result.profile["stance"] == []


def test_answer_with_only_axis_values_is_valid(monkeypatch):
    """Test that an answer with only axis values (no situation, no use-when) is valid."""
    _patch_chat(monkeypatch, _answer(situation="", useWhen=""))

    result = _extract()

    assert result.profile["situation"] is None
    assert result.use_when is None
    assert result.profile["emotion"] == [{"key": "anger", "confidence": 0.9, "source": "model"}]
    assert result.profile["stance"] == [{"key": "agree", "confidence": 0.3, "source": "model"}]


def test_answer_with_only_use_when_is_valid(monkeypatch):
    """Test that an answer with only a use-when is valid."""
    _patch_chat(monkeypatch, _answer(situation="", emotion=[], stance=[]))

    result = _extract()

    assert result.profile["situation"] is None
    assert result.use_when == "Use when plans fall through."
    assert result.profile["emotion"] == []
    assert result.profile["stance"] == []
