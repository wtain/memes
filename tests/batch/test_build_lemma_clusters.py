# tests/batch/test_build_lemma_clusters.py
import json

import pytest

from batch.build_lemma_clusters import load_lemma_source, resolve_language_blocks


def test_load_lemma_source_reads_json(tmp_path):
    path = tmp_path / "unmatched.json"
    path.write_text(json.dumps({"ru": {"мем": 5}}), encoding="utf-8")

    data = load_lemma_source(str(path))

    assert data == {"ru": {"мем": 5}}


def test_load_lemma_source_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_lemma_source("does_not_exist.json")


def test_resolve_language_blocks_all_returns_every_block():
    data = {"ru": {"мем": 5}, "en": {"lol": 10}}

    result = resolve_language_blocks(data, "all")

    assert result == data


def test_resolve_language_blocks_specific_language():
    data = {"ru": {"мем": 5}, "en": {"lol": 10}}

    result = resolve_language_blocks(data, "en")

    assert result == {"en": {"lol": 10}}


def test_resolve_language_blocks_missing_language_warns_and_skips(capsys):
    data = {"ru": {"мем": 5}}

    result = resolve_language_blocks(data, "fr")

    assert result == {}
    assert "fr" in capsys.readouterr().out


def test_resolve_language_blocks_flat_dict_treated_as_en():
    flat_data = {"lol": 120, "lmao": 45}

    result = resolve_language_blocks(flat_data, "all")

    assert result == {"en": {"lol": 120, "lmao": 45}}


def test_resolve_language_blocks_empty_dict_treated_as_empty_en():
    result = resolve_language_blocks({}, "all")

    assert result == {"en": {}}
