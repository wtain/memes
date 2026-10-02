import json
from pathlib import Path

import pytest
from pydantic import ValidationError

# The generator emits each schema's model as `Schema` in its own module.
from Backend.app.types.generated.memeprofile import Schema as MemeProfile
from Backend.app.types.generated.queryprofile import Schema as QueryProfile
from taxonomy.loader import load

SCHEMAS = Path(__file__).resolve().parents[2] / "shared" / "schemas"
VALUE = {"key": "joy", "confidence": 0.9, "source": "model"}


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["profilevalue", "memeprofile", "queryprofile"])
def test_schema_id_matches_filename(name):
    assert _schema(f"{name}.schema.json")["$id"] == f"{name}.schema.json"


def test_all_schema_registers_the_profile_schemas():
    definitions = _schema("all.schema.json")["definitions"]
    assert definitions["ProfileValue"] == {"$ref": "profilevalue.schema.json"}
    assert definitions["MemeProfile"] == {"$ref": "memeprofile.schema.json"}
    assert definitions["QueryProfile"] == {"$ref": "queryprofile.schema.json"}


@pytest.mark.parametrize("name", ["memeprofile", "queryprofile"])
def test_schema_properties_cover_every_taxonomy_axis(name):
    properties = _schema(f"{name}.schema.json")["properties"]
    for axis in load().axes:
        assert axis in properties, f"{name} schema lacks axis {axis!r}"


def test_meme_profile_accepts_a_valid_example():
    profile = MemeProfile.model_validate({
        "taxonomyVersion": 1, "situation": "plans cancelled", "situationConfidence": 0.7,
        "emotion": [VALUE], "stance": [], "topic": [], "tone": [],
        "useWhen": "Use when plans fall through.", "humanVerified": False, "stale": False,
    })
    assert profile.emotion[0].key == "joy"


def test_meme_profile_allows_null_situation_and_use_when():
    MemeProfile.model_validate({
        "taxonomyVersion": 1, "situation": None, "emotion": [], "stance": [], "topic": [], "tone": [],
        "useWhen": None, "humanVerified": True, "stale": True,
    })


def test_meme_profile_requires_its_required_fields():
    with pytest.raises(ValidationError):
        MemeProfile.model_validate({"taxonomyVersion": 1})


def test_profile_value_rejects_unknown_source_and_out_of_range_confidence():
    base = {"taxonomyVersion": 1, "emotion": [], "stance": [], "topic": [], "tone": [],
            "humanVerified": False, "stale": False}
    with pytest.raises(ValidationError):
        MemeProfile.model_validate({**base, "emotion": [{**VALUE, "source": "robot"}]})
    with pytest.raises(ValidationError):
        MemeProfile.model_validate({**base, "emotion": [{**VALUE, "confidence": 1.5}]})


def test_query_profile_accepts_a_valid_example_and_rejects_bad_language():
    base = {"taxonomyVersion": 1, "language": "ru", "parserVersion": "p1", "degraded": False,
            "emotion": [], "stance": [VALUE], "topic": [], "tone": []}
    assert QueryProfile.model_validate(base).language.value == "ru"
    with pytest.raises(ValidationError):
        QueryProfile.model_validate({**base, "language": "fr"})
