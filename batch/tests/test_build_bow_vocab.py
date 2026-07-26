"""
Unit tests for batch/build_bow.py's vocabulary-loading functions
(_load_ignore_lemmas, _build_json_rules_lemma_set,
_build_concepts_lemma_set). No DB, no I/O beyond reading fixture files
this test itself writes to tmp_path.
"""
import json

import yaml

from batch.build_bow import (
    _build_concepts_lemma_set,
    _build_json_rules_lemma_set,
    _load_ignore_lemmas,
)
from rules.normalize import make_morph


class TestLoadIgnoreLemmas:
    def test_english_word_is_stemmed(self, tmp_path):
        morph = make_morph()
        path = tmp_path / "ignore.json"
        path.write_text(json.dumps(["cats"]), encoding="utf-8")

        result = _load_ignore_lemmas(morph, path)

        assert result == {"cat"}

    def test_russian_word_is_lemmatized(self, tmp_path):
        morph = make_morph()
        path = tmp_path / "ignore.json"
        path.write_text(json.dumps(["кошки"]), encoding="utf-8")

        result = _load_ignore_lemmas(morph, path)

        assert result == {"кошка"}


class TestBuildJsonRulesLemmaSet:
    def test_english_word_in_rule_key_is_stemmed(self, tmp_path):
        morph = make_morph()
        path = tmp_path / "rules.json"
        path.write_text(json.dumps({"cats": {"topic": "cat"}}), encoding="utf-8")

        result = _build_json_rules_lemma_set(morph, path)

        assert result == {"cat"}


class TestBuildConceptsLemmaSet:
    def test_english_word_entry_is_stemmed(self, tmp_path):
        morph = make_morph()
        path = tmp_path / "concepts.yaml"
        path.write_text(
            yaml.dump({"felines": {"words": ["cats"], "votes": {"topic:cat": 1.0}}}),
            encoding="utf-8",
        )

        result = _build_concepts_lemma_set(morph, path)

        assert result == {"cat"}

    def test_english_fuzzy_entry_is_stemmed(self, tmp_path):
        morph = make_morph()
        path = tmp_path / "concepts.yaml"
        path.write_text(
            yaml.dump({
                "felines": {
                    "words": [],
                    "fuzzy": [{"word": "cats", "threshold": 85}],
                    "votes": {"topic:cat": 1.0},
                }
            }),
            encoding="utf-8",
        )

        result = _build_concepts_lemma_set(morph, path)

        assert result == {"cat"}
