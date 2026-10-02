import shutil
from pathlib import Path

import pytest

from taxonomy.loader import TaxonomyError, load
from taxonomy.model import FREE_TEXT, VOCABULARY

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


def _copy(tmp_path) -> Path:
    target = tmp_path / "tax"
    shutil.copytree(FIXTURE, target)
    return target


def test_load_reads_version_axes_and_values():
    tax = load(FIXTURE)
    assert tax.version == 7
    assert tax.axes["situation"].type == FREE_TEXT
    assert tax.axes["situation"].max_chars == 40
    assert tax.axes["emotion"].type == VOCABULARY
    assert tax.axes["emotion"].max_values == 2
    assert list(tax.values["emotion"]) == ["joy", "anger"]  # file order preserved
    assert tax.values["emotion"]["joy"].definition == "Plain happiness."
    assert tax.values["emotion"]["joy"].labels["ru"] == "Радость"


def test_free_text_axis_has_no_values_and_controlled_axes_listed():
    tax = load(FIXTURE)
    assert "situation" not in tax.values
    assert tax.controlled_axes() == ("emotion", "stance")


def test_has_key():
    tax = load(FIXTURE)
    assert tax.has_key("emotion", "joy")
    assert not tax.has_key("emotion", "agree")
    assert not tax.has_key("nope", "joy")


def test_similarity_overrides_are_parsed():
    tax = load(FIXTURE)
    assert tax.similarity_overrides == {"emotion": {"add": [["joy", "anger", 0.5]], "remove": []}}


def test_editing_overrides_changes_overrides_hash_but_not_content_hash(tmp_path):
    target = _copy(tmp_path)
    before = load(target)
    overrides = target / "similarity_overrides.yaml"
    overrides.write_text(overrides.read_text(encoding="utf-8").replace("0.5", "0.6"), encoding="utf-8")
    after = load(target)
    assert after.overrides_hash != before.overrides_hash
    assert after.content_hash == before.content_hash


def test_missing_overrides_file_gives_empty_overrides_and_hash(tmp_path):
    target = _copy(tmp_path)
    (target / "similarity_overrides.yaml").unlink()
    tax = load(target)
    assert tax.similarity_overrides == {}
    assert tax.overrides_hash == ""


def test_similarity_overrides_in_taxonomy_yaml_is_rejected(tmp_path):
    target = _copy(tmp_path)
    taxonomy = target / "taxonomy.yaml"
    taxonomy.write_text(taxonomy.read_text(encoding="utf-8") + "similarity_overrides: {}\n", encoding="utf-8")
    with pytest.raises(TaxonomyError, match="moved"):
        load(target)


def test_overrides_hash_is_stable_across_crlf(tmp_path):
    original = load(FIXTURE).overrides_hash
    target = _copy(tmp_path)
    overrides = target / "similarity_overrides.yaml"
    overrides.write_bytes(overrides.read_bytes().replace(b"\n", b"\r\n"))
    assert load(target).overrides_hash == original


def test_content_hash_is_stable_across_crlf(tmp_path):
    original = load(FIXTURE).content_hash
    target = _copy(tmp_path)
    for path in target.glob("*.yaml"):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert load(target).content_hash == original


def test_content_hash_changes_when_a_definition_changes(tmp_path):
    target = _copy(tmp_path)
    before = load(target).content_hash
    emotion = target / "emotion.yaml"
    emotion.write_text(emotion.read_text(encoding="utf-8").replace("Plain happiness.", "Pure delight."),
                       encoding="utf-8")
    assert load(target).content_hash != before


def test_duplicate_key_is_rejected(tmp_path):
    target = _copy(tmp_path)
    (target / "emotion.yaml").write_text(
        "values:\n  joy:\n    definition: a\n    labels: {en: a, ru: a, es: a}\n"
        "  joy:\n    definition: b\n    labels: {en: b, ru: b, es: b}\n", encoding="utf-8")
    with pytest.raises(TaxonomyError, match="duplicate key"):
        load(target)


def test_missing_axis_file_is_rejected(tmp_path):
    target = _copy(tmp_path)
    (target / "stance.yaml").unlink()
    with pytest.raises(TaxonomyError, match="missing file: stance.yaml"):
        load(target)


def test_unknown_axis_type_is_rejected(tmp_path):
    target = _copy(tmp_path)
    (target / "taxonomy.yaml").write_text(
        "version: 1\naxes:\n  emotion: {type: weird, max_values: 1}\n", encoding="utf-8")
    with pytest.raises(TaxonomyError, match="unknown type"):
        load(target)


def test_vocabulary_axis_without_max_values_is_rejected(tmp_path):
    target = _copy(tmp_path)
    (target / "taxonomy.yaml").write_text(
        "version: 1\naxes:\n  emotion: {type: vocabulary}\n", encoding="utf-8")
    with pytest.raises(TaxonomyError, match="max_values"):
        load(target)


def test_non_integer_version_is_rejected(tmp_path):
    target = _copy(tmp_path)
    (target / "taxonomy.yaml").write_text(
        "version: one\naxes:\n  emotion: {type: vocabulary, max_values: 1}\n", encoding="utf-8")
    with pytest.raises(TaxonomyError, match="version"):
        load(target)
