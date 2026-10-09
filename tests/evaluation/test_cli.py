import json
from pathlib import Path

import pytest

from evaluation import __main__ as cli
from evaluation.gold import (
    Corpus, CorpusImage, ExpectedProfile, GoldQuery, Judgment, load_judgments, save_corpus, save_judgments,
    save_queries,
)
from evaluation.metrics import QueryMetrics
from evaluation.report import load_baseline, make_baseline, save_baseline
from evaluation.runner import QueryResult, summarize

IMG = "11111111-1111-1111-1111-111111111111"
EXPECTED = ExpectedProfile("s", ("joy",), ("agree",), ("work",), ("wholesome",))


def _gold_dir(tmp_path):
    gold = tmp_path / "gold"
    (gold / "baselines").mkdir(parents=True)
    save_corpus(Corpus("general", 7, "2026-10-04", (CorpusImage(IMG, "h", "ocr only"),)), gold / "corpus.json")
    save_queries([GoldQuery("q001", "en", "boss moved the deadline", "short", "easy", EXPECTED, True)],
                 gold / "queries.json")
    save_judgments([], gold / "judgments.json")
    return gold


def _baseline(label, ndcg):
    results = [QueryResult("q001", "en", "short", "easy", (IMG,), QueryMetrics(ndcg, ndcg, 1.0, 1.0, True))]
    summary = summarize(results, {IMG: "ocr only"}, {"q001": {IMG: 2}})
    return make_baseline(label=label, retriever_name="fake", retriever_config={}, date="2026-10-04",
                         git_commit="abc", gold_hashes={"corpusSha": "c", "queriesSha": "q", "judgmentsSha": "j"},
                         profile_state={}, results=results, summary=summary)


def test_require_readonly_refuses_a_write_capable_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://ocr:secret@localhost:5432/ocrdb")
    with pytest.raises(SystemExit, match="read-only"):
        cli.require_readonly()
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(SystemExit, match="read-only"):
        cli.require_readonly()


def test_require_readonly_accepts_the_read_only_role(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://ocr_readonly:x@localhost:5432/ocrdb")
    cli.require_readonly()


def test_import_judgments_merges_an_export_and_reports_counts(tmp_path, capsys):
    gold = _gold_dir(tmp_path)
    export = tmp_path / "judgments-en.json"
    export.write_text(json.dumps({"language": "en", "rater": "ramiz", "date": "2026-10-04", "round": 1,
                                  "judgments": [{"queryId": "q001", "imageId": IMG, "grade": 2}]}), encoding="utf-8")

    code = cli.main(["--gold-dir", str(gold), "import-judgments", str(export)])

    assert code == 0
    assert load_judgments(gold / "judgments.json") == [Judgment("q001", IMG, 2, "ramiz", "2026-10-04", 1)]
    assert "added 1" in capsys.readouterr().out


def test_import_judgments_refuses_an_export_with_unknown_ids_and_changes_nothing(tmp_path, capsys):
    gold = _gold_dir(tmp_path)
    export = tmp_path / "bad.json"
    export.write_text(json.dumps({"language": "en", "rater": "r", "date": "d", "round": 1,
                                  "judgments": [{"queryId": "q999", "imageId": IMG, "grade": 1}]}), encoding="utf-8")

    code = cli.main(["--gold-dir", str(gold), "import-judgments", str(export)])

    assert code == 1 and "unknown query" in capsys.readouterr().err
    assert load_judgments(gold / "judgments.json") == []


def test_compare_and_list_baselines_work_on_stored_files(tmp_path, capsys):
    gold = _gold_dir(tmp_path)
    save_baseline(gold / "baselines" / "before.json", _baseline("before", 1.0))
    save_baseline(gold / "baselines" / "after.json", _baseline("after", 0.5))

    assert cli.main(["--gold-dir", str(gold), "list-baselines"]) == 0
    listing = capsys.readouterr().out
    assert "before" in listing and "after" in listing and "fake" in listing

    assert cli.main(["--gold-dir", str(gold), "compare", "before", "after"]) == 0
    assert "before -> after" in capsys.readouterr().out


def test_compare_refuses_mismatched_gold_hashes_without_force(tmp_path, capsys):
    gold = _gold_dir(tmp_path)
    other = _baseline("after", 0.5)
    other["gold"]["judgmentsSha"] = "different"
    save_baseline(gold / "baselines" / "before.json", _baseline("before", 1.0))
    save_baseline(gold / "baselines" / "after.json", other)

    assert cli.main(["--gold-dir", str(gold), "compare", "before", "after"]) == 1
    assert "judgmentsSha" in capsys.readouterr().err
    assert cli.main(["--gold-dir", str(gold), "compare", "before", "after", "--force"]) == 0


def test_a_missing_baseline_is_a_clear_error(tmp_path, capsys):
    gold = _gold_dir(tmp_path)

    assert cli.main(["--gold-dir", str(gold), "compare", "nope", "nada"]) == 1
    assert "nope" in capsys.readouterr().err


def test_check_environment_refuses_only_a_differing_app_env():
    assert cli.check_environment(None, "general") is None
    assert cli.check_environment("", "general") is None
    assert cli.check_environment("general", "general") is None
    assert "metal" in cli.check_environment("metal", "general") and "meaningless" in cli.check_environment("metal", "general")


def test_corpus_paths_follow_the_gold_dir_unless_given(tmp_path):
    assert cli.resolve_corpus_paths(tmp_path, None, None) == (tmp_path / "corpus.json", cli.DEFAULT_WORK / "corpus.ids")
    assert cli.resolve_corpus_paths(tmp_path, "x.json", "y.ids") == (Path("x.json"), Path("y.ids"))


def test_build_pools_refuses_to_overwrite_a_round_file_without_force(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://ocr_readonly:x@localhost:5432/none")
    pools_dir = tmp_path / "work"
    pools_dir.mkdir()
    existing = pools_dir / "pools-r1.json"
    existing.write_text("old", encoding="utf-8")

    code = cli.main(["--gold-dir", str(_gold_dir(tmp_path)), "build-pools", "--round", "1", "--pools-dir", str(pools_dir)])

    assert code == 1 and "already exists" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8") == "old"


@pytest.mark.parametrize("command", [["run", "--retriever", "random", "--label", "x"], ["build-corpus"], ["build-pools", "--round", "1"]])
def test_database_commands_refuse_without_the_read_only_url(tmp_path, monkeypatch, command):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SystemExit, match="read-only"):
        cli.main(["--gold-dir", str(_gold_dir(tmp_path)), *command])


def test_run_parsed_axis_match_requires_a_parse_model(tmp_path, capsys):
    gold = _gold_dir(tmp_path)

    code = cli.main(["--gold-dir", str(gold), "run", "--retriever", "parsed_axis_match", "--label", "x"])

    assert code == 1 and "--parse-model" in capsys.readouterr().err


def test_parsed_axis_match_is_a_retriever_choice_and_parse_model_is_an_option():
    args = cli.build_parser().parse_args(
        ["run", "--retriever", "parsed_axis_match", "--label", "l", "--parse-model", "m"])
    assert args.retriever == "parsed_axis_match" and args.parse_model == "m"


def test_parse_eval_refuses_to_overwrite_a_stored_report_without_force(tmp_path, capsys):
    gold = _gold_dir(tmp_path)
    (gold / "parse").mkdir()
    (gold / "parse" / "m1.json").write_text("{}", encoding="utf-8")

    code = cli.main(["--gold-dir", str(gold), "parse-eval", "--model", "qwen", "--label", "m1"])

    assert code == 1 and "already exists" in capsys.readouterr().err
    assert (gold / "parse" / "m1.json").read_text(encoding="utf-8") == "{}"


def test_parse_eval_options():
    args = cli.build_parser().parse_args(["parse-eval", "--model", "m", "--label", "l", "--max-queries", "5",
                                          "--timeout-seconds", "9", "--force"])
    assert (args.model, args.label, args.max_queries, args.timeout_seconds, args.force) == ("m", "l", 5, 9.0, True)


def _pool_args(**overrides):
    base = ["build-pools", "--round", "2"]
    for key, value in overrides.items():
        base += [f"--{key.replace('_', '-')}", str(value)]
    return cli.build_parser().parse_args(base)


def test_build_pools_defaults_keep_the_round_one_behaviour():
    args = _pool_args()
    assert args.sources == "lexical_or,situation_embedding,axis_match"
    assert (args.random, args.depth, args.per_query, args.parse_model) == (3, 30, 12, None)
    assert cli.validate_pool_args(args) is None
    assert cli.pool_source_names(args.sources) == ["lexical_or", "situation_embedding", "axis_match"]


def test_a_parser_only_pool_of_exactly_the_top_ten_is_valid():
    args = _pool_args(sources="parsed_axis_match", parse_model="m", random=0, depth=10, per_query=10)
    assert cli.validate_pool_args(args) is None
    assert cli.pool_source_names("parsed_axis_match, axis_match ") == ["parsed_axis_match", "axis_match"]


@pytest.mark.parametrize("overrides, fragment", [
    ({"sources": "parsed_axis_match"}, "--parse-model"),
    ({"sources": "nope"}, "unknown pool source"),
    ({"sources": ","}, "at least one source"),
    ({"random": -1}, "--random"),
    ({"per_query": 3, "random": 3}, "--per-query"),
    ({"depth": 0}, "--depth"),
])
def test_validate_pool_args_rejects_bad_options(overrides, fragment):
    assert fragment in cli.validate_pool_args(_pool_args(**overrides))


def test_build_pools_validates_its_options_before_requiring_the_read_only_url(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    gold = _gold_dir(tmp_path)

    code = cli.main(["--gold-dir", str(gold), "build-pools", "--round", "2", "--sources", "parsed_axis_match"])

    assert code == 1 and "--parse-model" in capsys.readouterr().err


def test_fused_is_a_retriever_and_a_pool_source_that_need_a_parse_model(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    gold = _gold_dir(tmp_path)

    assert cli.main(["--gold-dir", str(gold), "run", "--retriever", "fused", "--label", "x"]) == 1
    assert "--parse-model" in capsys.readouterr().err
    args = cli.build_parser().parse_args(["build-pools", "--round", "3", "--sources", "fused", "--parse-model", "m"])
    assert cli.validate_pool_args(args) is None
    args = cli.build_parser().parse_args(["build-pools", "--round", "3", "--sources", "fused"])
    assert "--parse-model" in cli.validate_pool_args(args)


def test_tune_weights_options_and_refusal_to_overwrite(tmp_path, capsys):
    args = cli.build_parser().parse_args(["tune-weights", "--parse-model", "m", "--label", "l", "--folds", "3",
                                          "--seed", "9", "--force"])
    assert (args.parse_model, args.label, args.folds, args.seed, args.force) == ("m", "l", 3, 9, True)

    gold = _gold_dir(tmp_path)
    (gold / "tuning").mkdir()
    (gold / "tuning" / "l.json").write_text("{}", encoding="utf-8")
    code = cli.main(["--gold-dir", str(gold), "tune-weights", "--parse-model", "m", "--label", "l"])
    assert code == 1 and "already exists" in capsys.readouterr().err

def test_tune_weights_rejects_fewer_than_two_folds_before_touching_the_database(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    code = cli.main(["--gold-dir", str(_gold_dir(tmp_path)), "tune-weights", "--parse-model", "m", "--label", "l",
                     "--folds", "1"])
    assert code == 1 and "--folds must be at least 2" in capsys.readouterr().err


def test_tune_weights_allow_degraded_flag_defaults_to_off():
    parser = cli.build_parser()
    base = ["tune-weights", "--parse-model", "m", "--label", "l"]
    assert parser.parse_args(base).allow_degraded is False
    assert parser.parse_args(base + ["--allow-degraded"]).allow_degraded is True


def test_export_ratings_options_and_refusal_without_the_read_only_url(tmp_path, monkeypatch):
    args = cli.build_parser().parse_args(["export-ratings", "--round", "4", "--out", str(tmp_path / "x"), "--force"])
    assert (args.round, args.out, args.force) == (4, str(tmp_path / "x"), True)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://ocr:ocr@localhost/ocrdb")
    with pytest.raises(SystemExit):
        cli.main(["--gold-dir", str(_gold_dir(tmp_path)), "export-ratings", "--round", "4"])


def test_export_ratings_refuses_to_overwrite_an_existing_export(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://ocr_readonly:x@localhost/ocrdb")
    out = tmp_path / "out"
    out.mkdir()
    (out / "summary.json").write_text("{}", encoding="utf-8")
    code = cli.main(["--gold-dir", str(_gold_dir(tmp_path)), "export-ratings", "--round", "4", "--out", str(out)])
    assert code == 1 and "already contains an export" in capsys.readouterr().err


def test_export_ratings_default_out_is_anchored_at_the_repository_root():
    default = Path(cli.build_parser().parse_args(["export-ratings", "--round", "1"]).out)
    assert default.is_absolute() and default.as_posix().endswith("evaluation/work/ratings-export")
    assert (default.parents[2] / "evaluation" / "__main__.py").exists()


def test_prepare_output_dir_refuses_without_force_and_cleans_only_its_own_files_with_force(tmp_path):
    out = tmp_path / "out"
    for name in ("judgments-ru.json", "candidate-queries.json", "summary.json", "keep.txt"):
        out.mkdir(exist_ok=True)
        (out / name).write_text("x", encoding="utf-8")
    assert "already contains an export" in cli.prepare_output_dir(out, force=False)
    assert cli.prepare_output_dir(out, force=True) is None
    assert sorted(p.name for p in out.iterdir()) == ["keep.txt"]
    assert cli.prepare_output_dir(tmp_path / "fresh", force=False) is None
