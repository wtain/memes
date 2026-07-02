# tests/batch/test_build_lemma_clusters.py
import json

import numpy as np
import pytest
import yaml

from batch.build_lemma_clusters import (
    build_cluster_records,
    load_lemma_source,
    nearest_concept,
    resolve_language_blocks,
    write_yaml_output,
)


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


def test_build_cluster_records_groups_and_sorts_by_total_frequency():
    frequencies = {
        "мем": 232, "мемасик": 45, "мемный": 18,
        "металхед": 89, "metalhead": 45, "стонкс": 12,
    }
    groups = {
        0: ["мем", "мемасик", "мемный"],
        1: ["металхед", "metalhead"],
        -1: ["стонкс"],
    }

    clusters, singletons = build_cluster_records(groups, frequencies)

    assert [c["id"] for c in clusters] == [1, 2]
    assert clusters[0]["total_frequency"] == 295
    assert clusters[0]["size"] == 3
    assert clusters[0]["members"] == {"мем": 232, "мемасик": 45, "мемный": 18}
    assert clusters[0]["ollama_concept"] is None
    assert clusters[1]["total_frequency"] == 134
    assert singletons == [{"lemma": "стонкс", "frequency": 12}]


def test_build_cluster_records_no_singletons():
    frequencies = {"a": 5, "b": 3}
    groups = {0: ["a", "b"]}

    clusters, singletons = build_cluster_records(groups, frequencies)

    assert len(clusters) == 1
    assert singletons == []


def test_build_cluster_records_all_noise():
    frequencies = {"a": 5, "b": 3}
    groups = {-1: ["a", "b"]}

    clusters, singletons = build_cluster_records(groups, frequencies)

    assert clusters == []
    assert singletons == [{"lemma": "a", "frequency": 5}, {"lemma": "b", "frequency": 3}]


def test_write_yaml_output_writes_expected_structure(tmp_path):
    output_file = tmp_path / "out" / "clusters.yaml"
    parameters = {"min_cluster_size": 2, "embed_model": "sbert"}
    languages = {
        "ru": {
            "clusters": [{
                "id": 1, "ollama_concept": "meme", "total_frequency": 295, "size": 3,
                "members": {"мем": 232, "мемасик": 45, "мемный": 18},
            }],
            "singletons": [{"lemma": "стонкс", "frequency": 12}],
        }
    }

    write_yaml_output(str(output_file), parameters, languages)

    loaded = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    assert loaded["parameters"] == parameters
    assert loaded["languages"] == languages
    assert "generated_at" in loaded


def test_nearest_concept_picks_closest_by_cosine_distance():
    centroid = np.array([1.0, 0.0])
    rows = [
        (1, "Metal", np.array([0.0, 1.0])),
        (2, "Memes", np.array([0.99, 0.14])),
    ]

    result = nearest_concept(centroid, rows)

    assert result["concept_id"] == 2
    assert result["name"] == "Memes"
    assert result["cosine_distance"] < 0.02


def test_nearest_concept_no_rows_returns_none():
    assert nearest_concept(np.array([1.0, 0.0]), []) is None
