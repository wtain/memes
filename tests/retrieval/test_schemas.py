import json
from pathlib import Path

import pytest

SCHEMAS = Path(__file__).resolve().parents[2] / "shared" / "schemas"
NEW = ("memefromtextrequest", "memefromtextresult", "memefromtextresponse", "axisexplanation")


def _load(name):
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", NEW)
def test_each_new_schema_exists_is_valid_json_and_is_referenced_from_all(name):
    schema = _load(name)
    assert schema["$id"] == f"{name}.schema.json" and schema["type"] == "object"
    all_text = (SCHEMAS / "all.schema.json").read_text(encoding="utf-8")
    assert f"{name}.schema.json" in all_text


def test_the_request_schema_requires_text_and_bounds_the_limit():
    schema = _load("memefromtextrequest")
    assert schema["required"] == ["text"]
    assert schema["properties"]["limit"]["maximum"] == 50 and schema["properties"]["limit"]["minimum"] == 1
    assert set(schema["properties"]["filters"]["properties"]) == {"emotion", "stance", "topic", "tone", "tags"}


def test_the_request_schema_bounds_every_user_supplied_size():
    props = _load("memefromtextrequest")["properties"]
    assert props["text"]["maxLength"] == 8000 and props["cursor"]["maxLength"] == 512
    for axis in ("emotion", "stance", "topic", "tone"):
        assert props["filters"]["properties"][axis]["maxItems"] == 64
        assert props["filters"]["properties"][axis]["items"]["maxLength"] == 128
    tags = props["filters"]["properties"]["tags"]
    assert tags["maxItems"] == 64
    assert tags["items"]["properties"]["key"]["maxLength"] == 128
    assert tags["items"]["properties"]["value"]["maxLength"] == 128


def test_the_response_schema_has_the_documented_fields():
    schema = _load("memefromtextresponse")
    assert set(schema["required"]) == {"items", "hasNext", "profile", "degraded", "fallback", "meta"}
    assert set(schema["properties"]["meta"]["properties"]) == {
        "available", "parseMs", "embedMs", "retrievalMs", "candidates", "cacheHit", "parserVersion", "model"}


def test_the_result_schema_carries_meme_score_explanation_and_cluster_info():
    schema = _load("memefromtextresult")
    assert set(schema["required"]) == {"meme", "score", "axisPath", "explanation"}
    assert {"clusterId", "clusterSize"} <= set(schema["properties"])
    assert set(_load("axisexplanation")["required"]) == {
        "axis", "score", "weight", "contribution", "neutral", "queryValues", "matched"}


def test_the_generated_python_models_exist_and_accept_a_minimal_response():
    from Backend.app.types.generated.memefromtextresponse import Schema

    payload = {"items": [], "hasNext": False, "degraded": False, "fallback": "none",
               "profile": {"taxonomyVersion": 2, "language": "en", "parserVersion": "p3-x", "degraded": False,
                           "emotion": [], "stance": [], "topic": [], "tone": []},
               "meta": {"available": True, "parseMs": 1, "embedMs": 1, "retrievalMs": 1, "candidates": 0,
                        "cacheHit": False, "parserVersion": "p3-x", "model": "m"}}
    model = Schema.model_validate(payload)
    assert model.hasNext is False and model.meta.available is True
