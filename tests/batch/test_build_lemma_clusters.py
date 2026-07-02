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


import pytest

from batch.build_lemma_clusters import main, run


class _FakeEmbedder:
    def __init__(self, vectors: dict):
        self.vectors = vectors

    def embed_text(self, text: str) -> np.ndarray:
        return np.array(self.vectors[text], dtype=float)


class _FakeNamer:
    def __init__(self, name: str = "meme"):
        self.name = name

    def name_cluster(self, language, word_freqs):
        return self.name


async def _async_return(value):
    return value


@pytest.mark.asyncio
async def test_run_end_to_end_writes_expected_yaml(tmp_path, monkeypatch):
    # Real HDBSCAN (min_samples defaults to min_cluster_size == 2, per commit
    # 5bd77cc) does not treat a lone trio + a lone outlier as dense enough to
    # form a stable cluster -- verified empirically it always collapses to
    # all-noise. Mirroring tests/batch/test_clustering.py's fix, this uses two
    # tight trios (each on an orthogonal axis) plus one clear outlier: the
    # мем trio reliably forms cluster 0 (highest total_frequency), the
    # рофл trio forms an incidental second cluster (not asserted on), and
    # стонкс remains the sole singleton.
    input_file = tmp_path / "unmatched.json"
    input_file.write_text(
        '{"ru": {"мем": 232, "рофл": 80, "мемасик": 45, "рофлить": 40, '
        '"мемчик": 20, "рофлан": 20, "стонкс": 12}}',
        encoding="utf-8",
    )
    output_file = tmp_path / "clusters.yaml"

    vectors = {
        "мем": [1.0, 0.0], "мемасик": [0.99, 0.05], "мемчик": [0.98, -0.05],
        "рофл": [0.0, 1.0], "рофлить": [0.05, 0.99], "рофлан": [-0.05, 0.98],
        "стонкс": [-1.0, 0.0],
    }
    monkeypatch.setattr("batch.build_lemma_clusters._get_embedder", lambda name: _FakeEmbedder(vectors))
    monkeypatch.setattr("batch.build_lemma_clusters._get_namer", lambda model: _FakeNamer("meme"))

    await run(
        input_file=str(input_file), output_file=str(output_file),
        min_cluster_size=2, ollama_enabled=True,
    )

    result = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    ru = result["languages"]["ru"]
    assert ru["clusters"][0]["ollama_concept"] == "meme"
    assert ru["clusters"][0]["members"] == {"мем": 232, "мемасик": 45, "мемчик": 20}
    assert ru["singletons"] == [{"lemma": "стонкс", "frequency": 12}]


@pytest.mark.asyncio
async def test_run_ollama_disabled_leaves_concept_null(tmp_path, monkeypatch):
    # Same trio-of-3 treatment as the ru end-to-end test above: a lone pair
    # never forms a stable HDBSCAN cluster, so lol/lmao is expanded to a trio
    # (highest total_frequency -> clusters[0]) alongside a second incidental
    # trio that only exists to give HDBSCAN enough structure to detect any
    # cluster at all.
    input_file = tmp_path / "unmatched.json"
    input_file.write_text(
        '{"en": {"lol": 10, "wtf": 6, "lmao": 8, "omg": 4, "lolz": 5, "huh": 2}}',
        encoding="utf-8",
    )
    output_file = tmp_path / "out.yaml"

    vectors = {
        "lol": [1.0, 0.0], "lmao": [0.99, 0.05], "lolz": [0.98, -0.05],
        "wtf": [0.0, 1.0], "omg": [0.05, 0.99], "huh": [-0.05, 0.98],
    }
    monkeypatch.setattr("batch.build_lemma_clusters._get_embedder", lambda name: _FakeEmbedder(vectors))

    await run(
        input_file=str(input_file), output_file=str(output_file),
        min_cluster_size=2, ollama_enabled=False,
    )

    result = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    en = result["languages"]["en"]
    assert en["clusters"][0]["members"] == {"lol": 10, "lmao": 8, "lolz": 5}
    assert en["clusters"][0]["ollama_concept"] is None


@pytest.mark.asyncio
async def test_run_skips_clustering_for_fewer_than_two_lemmas(tmp_path, monkeypatch, capsys):
    input_file = tmp_path / "unmatched.json"
    input_file.write_text('{"es": {"gato": 3}}', encoding="utf-8")
    output_file = tmp_path / "out.yaml"

    monkeypatch.setattr("batch.build_lemma_clusters._get_embedder", lambda name: _FakeEmbedder({}))

    await run(input_file=str(input_file), output_file=str(output_file), ollama_enabled=False)

    result = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    assert result["languages"]["es"]["clusters"] == []
    assert result["languages"]["es"]["singletons"] == [{"lemma": "gato", "frequency": 3}]
    assert "fewer than 2 lemmas" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_run_lookup_concepts_with_sbert_fails_fast(tmp_path):
    with pytest.raises(ValueError, match="TEXT_EMBED_MODEL=clip"):
        await run(
            input_file=str(tmp_path / "missing.json"), output_file=str(tmp_path / "out.yaml"),
            embed_model="sbert", lookup_concepts=True,
        )


@pytest.mark.asyncio
async def test_run_lookup_concepts_attaches_nearest_concept(tmp_path, monkeypatch):
    # Same trio treatment (see test_run_ollama_disabled_leaves_concept_null):
    # lol/lmao expanded to a trio so a real cluster forms, plus an incidental
    # second trio for HDBSCAN structure. Only one concept row exists, so
    # nearest_concept()'s pick is unaffected by the trio expansion.
    input_file = tmp_path / "unmatched.json"
    input_file.write_text(
        '{"en": {"lol": 10, "wtf": 6, "lmao": 8, "omg": 4, "lolz": 5, "huh": 2}}',
        encoding="utf-8",
    )
    output_file = tmp_path / "out.yaml"

    vectors = {
        "lol": [1.0, 0.0], "lmao": [0.99, 0.05], "lolz": [0.98, -0.05],
        "wtf": [0.0, 1.0], "omg": [0.05, 0.99], "huh": [-0.05, 0.98],
    }
    monkeypatch.setattr("batch.build_lemma_clusters._get_embedder", lambda name: _FakeEmbedder(vectors))
    monkeypatch.setattr(
        "batch.build_lemma_clusters._load_concept_rows",
        lambda: _async_return([(1, "Internet Memes", np.array([1.0, 0.0]))]),
    )

    await run(
        input_file=str(input_file), output_file=str(output_file), min_cluster_size=2,
        embed_model="clip", ollama_enabled=False, lookup_concepts=True,
    )

    result = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    cluster = result["languages"]["en"]["clusters"][0]
    assert cluster["members"] == {"lol": 10, "lmao": 8, "lolz": 5}
    nearest = cluster["nearest_concept"]
    assert nearest["concept_id"] == 1
    assert nearest["name"] == "Internet Memes"


@pytest.mark.asyncio
async def test_run_lookup_concepts_no_concepts_in_db_sets_null(tmp_path, monkeypatch, capsys):
    # Same trio treatment as the other lookup_concepts tests above.
    input_file = tmp_path / "unmatched.json"
    input_file.write_text(
        '{"en": {"lol": 10, "wtf": 6, "lmao": 8, "omg": 4, "lolz": 5, "huh": 2}}',
        encoding="utf-8",
    )
    output_file = tmp_path / "out.yaml"

    vectors = {
        "lol": [1.0, 0.0], "lmao": [0.99, 0.05], "lolz": [0.98, -0.05],
        "wtf": [0.0, 1.0], "omg": [0.05, 0.99], "huh": [-0.05, 0.98],
    }
    monkeypatch.setattr("batch.build_lemma_clusters._get_embedder", lambda name: _FakeEmbedder(vectors))
    monkeypatch.setattr("batch.build_lemma_clusters._load_concept_rows", lambda: _async_return([]))

    await run(
        input_file=str(input_file), output_file=str(output_file), min_cluster_size=2,
        embed_model="clip", ollama_enabled=False, lookup_concepts=True,
    )

    result = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    cluster = result["languages"]["en"]["clusters"][0]
    assert cluster["members"] == {"lol": 10, "lmao": 8, "lolz": 5}
    assert cluster["nearest_concept"] is None
    assert "no concept embeddings" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_main_reads_env_vars_and_calls_run(monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("batch.build_lemma_clusters.run", fake_run)
    monkeypatch.setenv("TEXT_SCOPE", "all")
    monkeypatch.setenv("BOW_OUTPUT_FILE", "bow.json")
    monkeypatch.setenv("BOW_UNMATCHED_FILE", "unmatched.json")
    monkeypatch.setenv("CLUSTER_OUTPUT_FILE", "out.yaml")
    monkeypatch.setenv("LANGUAGE", "ru")
    monkeypatch.setenv("MIN_CLUSTER_SIZE", "3")
    monkeypatch.setenv("MIN_SAMPLES", "2")
    monkeypatch.setenv("CLUSTER_SELECTION_EPSILON", "0.15")
    monkeypatch.setenv("TEXT_EMBED_MODEL", "clip")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3")
    monkeypatch.setenv("OLLAMA_ENABLED", "false")
    monkeypatch.setenv("LOOKUP_CONCEPTS", "true")

    await main()

    assert captured["input_file"] == "bow.json"
    assert captured["output_file"] == "out.yaml"
    assert captured["language"] == "ru"
    assert captured["min_cluster_size"] == 3
    assert captured["min_samples"] == 2
    assert captured["cluster_selection_epsilon"] == 0.15
    assert captured["embed_model"] == "clip"
    assert captured["ollama_model"] == "llama3"
    assert captured["ollama_enabled"] is False
    assert captured["lookup_concepts"] is True


@pytest.mark.asyncio
async def test_main_default_text_scope_uses_unmatched_file(monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("batch.build_lemma_clusters.run", fake_run)
    monkeypatch.delenv("TEXT_SCOPE", raising=False)
    monkeypatch.setenv("BOW_UNMATCHED_FILE", "unmatched.json")
    monkeypatch.setenv("CLUSTER_OUTPUT_FILE", "out.yaml")

    await main()

    assert captured["input_file"] == "unmatched.json"
