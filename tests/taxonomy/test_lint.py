from taxonomy.lint import lint
from taxonomy.model import FREE_TEXT, VOCABULARY, AxisSpec, Taxonomy, VocabValue


def _value(key="joy", definition="Plain happiness.", labels=None):
    return VocabValue(key, definition, labels or {"en": "Joy", "ru": "Радость", "es": "Alegría"})


def _tax(emotion_values, axes=None):
    axes = axes or {
        "situation": AxisSpec("situation", FREE_TEXT, max_chars=160),
        "emotion": AxisSpec("emotion", VOCABULARY, max_values=3),
        "stance": AxisSpec("stance", VOCABULARY, max_values=2),
        "topic": AxisSpec("topic", VOCABULARY, max_values=3),
        "tone": AxisSpec("tone", VOCABULARY, max_values=2),
    }
    values = {"emotion": emotion_values, "stance": {"agree": _value("agree")},
              "topic": {"work": _value("work")}, "tone": {"wholesome": _value("wholesome")}}
    return Taxonomy(1, axes, values, "hash")


def test_clean_taxonomy_has_no_problems_when_sizes_are_not_checked():
    assert lint(_tax({"joy": _value()}), check_sizes=False) == []


def test_bad_key_formats_are_reported():
    for bad in ("Joy", "joy-ful", "1joy", "joy joy", "j" * 49):
        problems = lint(_tax({bad: _value(bad)}), check_sizes=False)
        assert any("key" in p and "emotion" in p for p in problems), bad


def test_empty_and_overlong_definitions_are_reported():
    assert any("definition" in p for p in lint(_tax({"joy": _value(definition="  ")}), check_sizes=False))
    assert any("200" in p for p in lint(_tax({"joy": _value(definition="x" * 201)}), check_sizes=False))


def test_missing_label_is_reported():
    problems = lint(_tax({"joy": _value(labels={"en": "Joy", "ru": "Радость"})}), check_sizes=False)
    assert "emotion.joy: missing es label" in problems


def test_size_ranges_are_enforced_when_requested():
    problems = lint(_tax({"joy": _value()}))
    assert any(p.startswith("emotion: 1 values outside 20-30") for p in problems)


def test_required_axes_must_exist():
    axes = {"emotion": AxisSpec("emotion", VOCABULARY, max_values=3)}
    tax = Taxonomy(1, axes, {"emotion": {"joy": _value()}}, "hash")
    assert "missing required axis 'situation'" in lint(tax, check_sizes=False)


def test_situation_must_be_free_text():
    axes = {
        "situation": AxisSpec("situation", VOCABULARY, max_values=1),
        "emotion": AxisSpec("emotion", VOCABULARY, max_values=3),
        "stance": AxisSpec("stance", VOCABULARY, max_values=2),
        "topic": AxisSpec("topic", VOCABULARY, max_values=3),
        "tone": AxisSpec("tone", VOCABULARY, max_values=2),
    }
    tax = _tax({"joy": _value()}, axes=axes)
    assert "situation: must be a free_text axis" in lint(tax, check_sizes=False)


def test_axis_name_length_is_limited():
    axes = {"a_very_long_axis_name": AxisSpec("a_very_long_axis_name", FREE_TEXT, max_chars=10)}
    tax = Taxonomy(1, axes, {}, "hash")
    assert any("longer than 16" in p for p in lint(tax, check_sizes=False))
