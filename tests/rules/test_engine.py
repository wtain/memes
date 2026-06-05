import json
import pytest
from pathlib import Path

from rules.engine import RulesEngine

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def engine():
    return RulesEngine(str(FIXTURES_DIR / "rules.json"))


def _load_cases(method: str):
    with open(FIXTURES_DIR / "test_cases.json", encoding="utf-8") as f:
        data = json.load(f)
    return [
        pytest.param(case["input"], case["expected"], case["unexpected"], id=case["id"])
        for case in data[method]
    ]


def _assert_tags(actual: list, expected: list, unexpected: list) -> None:
    tag_set = {(key, val) for key, val in actual}
    for key, val in expected:
        assert (key, val) in tag_set, (
            f"Expected tag ({key!r}: {val!r}) not found. Got: {sorted(tag_set)}"
        )
    for key, val in unexpected:
        assert (key, val) not in tag_set, (
            f"Unexpected tag ({key!r}: {val!r}) was found. Got: {sorted(tag_set)}"
        )


class TestGetTagsForText:
    """
    Tests for RulesEngine.get_tags_for_text.

    Uses word-boundary regex (\\b…\\b) with re.IGNORECASE, so a token must
    appear as a whole word.  A token embedded inside a longer word does NOT
    match (unlike get_tags_for_ocr_text).
    """

    @pytest.mark.parametrize(
        "input_text,expected,unexpected",
        _load_cases("get_tags_for_text"),
    )
    def test_tags(self, engine, input_text, expected, unexpected):
        actual = list(engine.get_tags_for_text(input_text))
        _assert_tags(actual, expected, unexpected)


class TestGetTagsForOCRText:
    """
    Tests for RulesEngine.get_tags_for_ocr_text.

    Uses case-insensitive substring matching (rule_name in text.lower()), so
    a token matches even when it appears as part of a larger word.
    """

    @pytest.mark.parametrize(
        "input_text,expected,unexpected",
        _load_cases("get_tags_for_ocr_text"),
    )
    def test_tags(self, engine, input_text, expected, unexpected):
        actual = list(engine.get_tags_for_ocr_text(input_text))
        _assert_tags(actual, expected, unexpected)