import textwrap

import pytest
import yaml

from batch.draft_concepts_from_clusters import (
    append_to_file,
    collect_declared_tags,
    collect_existing_keys,
    collect_existing_words,
    format_concept_block,
    format_tag_declaration,
    main,
    resolve_key,
    run,
    select_top_clusters,
    slugify,
    top_lemma,
)


def test_slugify_quoted_multiword_name():
    assert slugify('"Local Dialect Words"') == "local_dialect_words"


def test_slugify_single_word():
    assert slugify("Memes") == "memes"


def test_slugify_cyrillic_name():
    assert slugify("Сон") == "сон"


def test_slugify_none_returns_empty():
    assert slugify(None) == ""


def test_slugify_empty_string_returns_empty():
    assert slugify("") == ""


def test_slugify_collapses_punctuation():
    assert slugify("Purchase / Activities!!") == "purchase_activities"


def test_top_lemma_returns_first_member():
    cluster = {"members": {"спать": 640, "сон": 100}}
    assert top_lemma(cluster) == "спать"


def test_collect_existing_words_includes_words_and_fuzzy():
    concepts_data = {
        "sleep": {"words": ["спать", "сон"]},
        "salary": {"words": ["зарплата"], "fuzzy": [{"word": "зорплата", "threshold": 85}]},
    }
    result = collect_existing_words(concepts_data)
    assert result == {"спать", "сон", "зарплата", "зорплата"}


def test_collect_existing_words_handles_missing_words_key():
    assert collect_existing_words({"empty": {}}) == set()


def test_collect_existing_words_handles_none():
    assert collect_existing_words(None) == set()


def test_collect_existing_keys():
    assert collect_existing_keys({"sleep": {}, "family": {}}) == {"sleep", "family"}


def test_collect_existing_keys_handles_none():
    assert collect_existing_keys(None) == set()


def test_collect_declared_tags():
    tags_data = {"defaults": {"threshold": 1.0}, "tags": {"тема:сон": {}, "тема:семья": {}}}
    assert collect_declared_tags(tags_data) == {"тема:сон", "тема:семья"}


def test_resolve_key_uses_slugified_ollama_name():
    assert resolve_key("Sleeping", "спать", existing_keys=set()) == "sleeping"


def test_resolve_key_falls_back_to_lemma_when_no_ollama_name():
    assert resolve_key(None, "спать", existing_keys=set()) == "спать"


def test_resolve_key_disambiguates_collision():
    assert resolve_key("Sleeping", "спать", existing_keys={"sleeping"}) == "sleeping_2"


def test_resolve_key_disambiguates_multiple_collisions():
    key = resolve_key("Sleeping", "спать", existing_keys={"sleeping", "sleeping_2"})
    assert key == "sleeping_3"


def _cluster(word: str, freq: int = 10) -> dict:
    return {
        "members": {word: freq},
        "total_frequency": freq,
        "size": 1,
        "ollama_concept": word.title(),
    }


def test_select_top_clusters_accepts_up_to_top_n():
    clusters = [_cluster("a"), _cluster("b"), _cluster("c")]

    accepted, skipped = select_top_clusters(clusters, existing_words=set(), top=2)

    assert [top_lemma(c) for c in accepted] == ["a", "b"]
    assert skipped == []


def test_select_top_clusters_skips_already_covered_and_backfills():
    clusters = [_cluster("a"), _cluster("b"), _cluster("c")]

    accepted, skipped = select_top_clusters(clusters, existing_words={"a"}, top=2)

    assert [top_lemma(c) for c in accepted] == ["b", "c"]
    assert [top_lemma(c) for c in skipped] == ["a"]


def test_select_top_clusters_reports_shortfall_when_not_enough_available():
    clusters = [_cluster("a"), _cluster("b")]

    accepted, skipped = select_top_clusters(clusters, existing_words={"a"}, top=5)

    assert [top_lemma(c) for c in accepted] == ["b"]
    assert [top_lemma(c) for c in skipped] == ["a"]


def test_select_top_clusters_zero_top_adds_nothing():
    clusters = [_cluster("a")]

    accepted, skipped = select_top_clusters(clusters, existing_words=set(), top=0)

    assert accepted == []
    assert skipped == []


def test_select_top_clusters_never_visits_clusters_beyond_top():
    poison = {"members": {}, "total_frequency": 0, "size": 0, "ollama_concept": None}
    clusters = [_cluster("a"), _cluster("b"), poison]

    accepted, skipped = select_top_clusters(clusters, existing_words=set(), top=2)

    assert [top_lemma(c) for c in accepted] == ["a", "b"]
    assert skipped == []


def test_format_concept_block_with_ollama_name():
    cluster = {
        "ollama_concept": "Sleeping",
        "total_frequency": 640,
        "size": 3,
        "members": {"спать": 500, "сон": 100, "уснуть": 40},
    }

    text = format_concept_block("sleeping", cluster, "спать")

    assert text == (
        '# Ollama suggested: "Sleeping" (freq=640, size=3, from build_lemma_clusters)\n'
        "sleeping:\n"
        "  words:\n"
        "  - спать\n"
        "  - сон\n"
        "  - уснуть\n"
        "  votes:\n"
        "    тема:спать: 1.0\n"
    )


def test_format_concept_block_without_ollama_name():
    cluster = {
        "ollama_concept": None,
        "total_frequency": 10,
        "size": 2,
        "members": {"a": 5, "b": 5},
    }

    text = format_concept_block("a", cluster, "a")

    assert text == (
        "# from build_lemma_clusters (no Ollama name)\n"
        "a:\n"
        "  words:\n"
        "  - a\n"
        "  - b\n"
        "  votes:\n"
        "    тема:a: 1.0\n"
    )


def test_format_tag_declaration():
    assert format_tag_declaration("спать") == "  тема:спать: {}\n"


def test_append_to_file_adds_leading_newline_when_missing(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_text("existing: true", encoding="utf-8")

    append_to_file(str(path), "new: 1\n")

    assert path.read_text(encoding="utf-8") == "existing: true\nnew: 1\n"


def test_append_to_file_no_extra_newline_when_already_present(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_text("existing: true\n", encoding="utf-8")

    append_to_file(str(path), "new: 1\n")

    assert path.read_text(encoding="utf-8") == "existing: true\nnew: 1\n"


def _write(path, content):
    path.write_text(content, encoding="utf-8")


def test_run_end_to_end_appends_new_concept_and_tag(tmp_path):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    concepts_path = data_dir / "concepts.general.yaml"
    tags_path = data_dir / "tags.general.yaml"
    _write(concepts_path, "existing_concept:\n  words:\n  - existing_word\n  votes:\n    тема:existing: 1.0")
    _write(tags_path, "defaults:\n  threshold: 1.0\ntags:\n  тема:existing: {}")

    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, textwrap.dedent("""\
        languages:
          ru:
            clusters:
              - id: 1
                ollama_concept: "Sleeping"
                total_frequency: 640
                size: 2
                members:
                  спать: 500
                  сон: 140
            singletons: []
        """))

    run(str(cluster_file), env="general", language="ru", top=1, data_dir=str(data_dir))

    concepts_data = yaml.safe_load(concepts_path.read_text(encoding="utf-8"))
    assert concepts_data["sleeping"]["words"] == ["спать", "сон"]
    assert concepts_data["sleeping"]["votes"] == {"тема:спать": 1.0}
    assert concepts_data["existing_concept"]["words"] == ["existing_word"]

    tags_data = yaml.safe_load(tags_path.read_text(encoding="utf-8"))
    assert "тема:спать" in tags_data["tags"]
    assert "тема:existing" in tags_data["tags"]


def test_run_skips_cluster_already_covered_and_backfills(tmp_path):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    concepts_path = data_dir / "concepts.general.yaml"
    tags_path = data_dir / "tags.general.yaml"
    _write(concepts_path, "sleep:\n  words:\n  - спать\n  votes:\n    тема:спать: 1.0")
    _write(tags_path, "defaults:\n  threshold: 1.0\ntags:\n  тема:спать: {}")

    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, textwrap.dedent("""\
        languages:
          ru:
            clusters:
              - id: 1
                ollama_concept: "Sleeping"
                total_frequency: 640
                size: 2
                members:
                  спать: 500
                  сон: 140
              - id: 2
                ollama_concept: "Purchases"
                total_frequency: 300
                size: 2
                members:
                  купить: 200
                  покупать: 100
            singletons: []
        """))

    run(str(cluster_file), env="general", language="ru", top=1, data_dir=str(data_dir))

    concepts_data = yaml.safe_load(concepts_path.read_text(encoding="utf-8"))
    assert "purchases" in concepts_data
    assert "sleeping" not in concepts_data


def test_run_missing_language_raises(tmp_path):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    _write(data_dir / "concepts.general.yaml", "{}")
    _write(data_dir / "tags.general.yaml", "tags: {}")
    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, "languages:\n  ru:\n    clusters: []\n    singletons: []\n")

    with pytest.raises(ValueError, match="es"):
        run(str(cluster_file), env="general", language="es", top=1, data_dir=str(data_dir))


def test_run_missing_concepts_file_raises(tmp_path):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    _write(data_dir / "tags.general.yaml", "tags: {}")
    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, "languages:\n  ru:\n    clusters: []\n    singletons: []\n")

    with pytest.raises(FileNotFoundError):
        run(str(cluster_file), env="general", language="ru", top=1, data_dir=str(data_dir))


def test_run_missing_tags_file_raises(tmp_path):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    _write(data_dir / "concepts.general.yaml", "{}")
    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, "languages:\n  ru:\n    clusters: []\n    singletons: []\n")

    with pytest.raises(FileNotFoundError):
        run(str(cluster_file), env="general", language="ru", top=1, data_dir=str(data_dir))


def test_run_prints_shortfall_warning_when_not_enough_clusters(tmp_path, capsys):
    data_dir = tmp_path / "tagging"
    data_dir.mkdir()
    _write(data_dir / "concepts.general.yaml", "{}")
    _write(data_dir / "tags.general.yaml", "tags: {}")

    cluster_file = tmp_path / "clusters.yaml"
    _write(cluster_file, textwrap.dedent("""\
        languages:
          ru:
            clusters:
              - id: 1
                ollama_concept: "Sleeping"
                total_frequency: 640
                size: 2
                members:
                  спать: 500
                  сон: 140
            singletons: []
        """))

    run(str(cluster_file), env="general", language="ru", top=5, data_dir=str(data_dir))

    out = capsys.readouterr().out
    assert "Added 1 concept" in out
    assert "only 1" in out.lower()


def test_main_parses_args_and_calls_run(monkeypatch):
    captured = {}

    def fake_run(cluster_file, env, language, top, data_dir):
        captured.update(cluster_file=cluster_file, env=env, language=language, top=top, data_dir=data_dir)

    monkeypatch.setattr("batch.draft_concepts_from_clusters.run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        ["draft_concepts_from_clusters", "--cluster-file", "c.yaml", "--env", "general", "--language", "ru", "--top", "5"],
    )

    main()

    assert captured == {
        "cluster_file": "c.yaml",
        "env": "general",
        "language": "ru",
        "top": 5,
        "data_dir": "batch/data/tagging",
    }