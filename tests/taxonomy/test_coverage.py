import json
from pathlib import Path

from taxonomy.coverage import assign_from_response, format_report, summarize
from taxonomy.loader import load

FIXTURE = Path(__file__).parent / "fixtures" / "mini"
TAX = load(FIXTURE)


def test_assign_splits_valid_keys_from_unknown_ones():
    raw = json.dumps({"emotion": ["joy", "euphoria"], "stance": ["agree"],
                      "suggested": {"emotion": ["bliss"], "stance": []}})
    result = assign_from_response(raw, TAX)
    assert result["assigned"] == {"emotion": ["joy"], "stance": ["agree"]}
    assert result["suggested"] == {"emotion": ["euphoria", "bliss"], "stance": []}


def test_assign_accepts_a_bare_string_and_deduplicates():
    result = assign_from_response(json.dumps({"emotion": "joy", "stance": ["agree", "agree"]}), TAX)
    assert result["assigned"] == {"emotion": ["joy"], "stance": ["agree"]}


def test_assign_returns_none_for_invalid_json_or_non_object():
    assert assign_from_response("not json", TAX) is None
    assert assign_from_response("[1, 2]", TAX) is None
    assert assign_from_response(None, TAX) is None


def _result(emotion=(), stance=(), suggested_emotion=()):
    return {"assigned": {"emotion": list(emotion), "stance": list(stance)},
            "suggested": {"emotion": list(suggested_emotion), "stance": []}}


def test_summarize_counts_coverage_unused_overused_and_suggestions():
    results = [
        _result(["joy"], ["agree"], ["bliss"]),
        _result(["joy"], [], ["bliss"]),
        _result([], ["agree"], ["glee"]),
    ]
    summary = summarize(results, TAX)
    emotion = summary["axes"]["emotion"]
    assert summary["images"] == 3
    assert emotion["covered_share"] == 2 / 3
    assert emotion["counts"] == {"joy": 2, "anger": 0}
    assert emotion["unused"] == ["anger"]
    assert emotion["overused"] == ["joy"]  # 2/3 > 0.4
    assert emotion["suggestions"] == [("bliss", 2), ("glee", 1)]
    assert summary["axes"]["stance"]["overused"] == ["agree"]


def test_summarize_empty_input_does_not_divide_by_zero():
    summary = summarize([], TAX)
    assert summary["axes"]["emotion"]["covered_share"] == 0.0
    assert summary["axes"]["emotion"]["overused"] == []


def test_format_report_is_markdown_with_a_section_per_axis():
    report = format_report(summarize([_result(["joy"], ["agree"])], TAX))
    assert report.startswith("# Vocabulary coverage")
    assert "## emotion" in report and "## stance" in report
    assert "Unused values: anger" in report
