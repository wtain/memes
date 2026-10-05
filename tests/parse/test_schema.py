from parse.schema import CONFIDENCE_LEVELS, LANGUAGES, build_response_schema
from taxonomy import load

TAX = load()


def test_schema_has_language_situation_and_every_controlled_axis():
    schema = build_response_schema(TAX)

    assert schema["type"] == "object"
    assert list(schema["properties"]) == ["language", "situation", *TAX.controlled_axes()]
    assert schema["required"] == list(schema["properties"])
    assert schema["properties"]["language"]["enum"] == list(LANGUAGES) == ["en", "ru", "es", "other"]
    assert schema["properties"]["situation"] == {"type": "string"}


def test_axis_enums_and_limits_come_from_the_taxonomy():
    schema = build_response_schema(TAX)
    for axis in TAX.controlled_axes():
        spec = schema["properties"][axis]
        assert spec["type"] == "array"
        assert spec["maxItems"] == TAX.axes[axis].max_values
        assert spec["items"]["properties"]["key"]["enum"] == list(TAX.values[axis])
        assert spec["items"]["properties"]["confidence"]["enum"] == ["low", "medium", "high"]
        assert spec["items"]["required"] == ["key", "confidence"]


def test_situation_has_no_max_length_because_decoding_does_not_honour_it():
    assert "maxLength" not in build_response_schema(TAX)["properties"]["situation"]


def test_confidence_levels_match_p2():
    assert CONFIDENCE_LEVELS == {"low": 0.3, "medium": 0.6, "high": 0.9}
