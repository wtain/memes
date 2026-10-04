import json

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
