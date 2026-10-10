"""Unit tests for batch/import_judgments.py: tracking contract and _process against a tmp gold directory."""
import json
from unittest.mock import AsyncMock, patch

import pytest

import batch.import_judgments as module
from evaluation.gold import (Corpus, CorpusImage, ExpectedProfile, GoldError, GoldQuery, Judgment, save_corpus,
                             save_judgments, save_queries)

IMG = "11111111-1111-1111-1111-111111111111"
IMG2 = "22222222-2222-2222-2222-222222222222"
EXPECTED = ExpectedProfile("s", ("joy",), ("agree",), ("work",), ("wholesome",))


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


def _gold(tmp_path, judgments=()):
    gold = tmp_path / "gold"
    gold.mkdir()
    save_corpus(Corpus("general", 7, "2026-10-04",
                       (CorpusImage(IMG, "h", "ocr only"), CorpusImage(IMG2, "h2", "ocr only"))), gold / "corpus.json")
    save_queries([GoldQuery("q001", "en", "boss moved the deadline", "short", "easy", EXPECTED, True)],
                 gold / "queries.json")
    save_judgments(list(judgments), gold / "judgments.json")
    return gold


def _export_file(path, *judgments, round_number=3):
    path.write_text(json.dumps({"rater": "rating-ui", "date": "2026-10-11", "round": round_number,
                                "judgments": [{"queryId": q, "imageId": i, "grade": g} for q, i, g in judgments]}),
                    encoding="utf-8")
    return path


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_records_the_stats(self):
        process_mock = AsyncMock(return_value={"added": 1})
        record_mock = AsyncMock()
        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_mock, \
             patch.object(module, "_process", process_mock), patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual")
        tracked_mock.assert_called_once_with(kind="import_judgments", trigger="manual")
        record_mock.assert_awaited_once_with("run-1", {"added": 1})

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_records_on_the_given_run(self):
        record_mock = AsyncMock()
        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", AsyncMock(return_value={"added": 0})), \
             patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual", run_id="existing-run-1")
        finish_mock.assert_called_once_with("existing-run-1")
        record_mock.assert_awaited_once_with("existing-run-1", {"added": 0})


@pytest.mark.asyncio
async def test_process_merges_the_current_export(tmp_path, monkeypatch):
    gold = _gold(tmp_path)
    export = tmp_path / "export"
    export.mkdir()
    _export_file(export / "judgments-en.json", ("q001", IMG, 2))
    monkeypatch.setenv("APP_ENV", "general")
    stats = await module._process(gold=gold, export_dir=export)
    assert (stats["added"], stats["total"], stats["files"]) == (1, 1, 1)
    assert "uncommitted" in stats["note"] and stats["changed_file"].endswith("judgments.json")


@pytest.mark.asyncio
async def test_process_without_an_export_adds_nothing(tmp_path, monkeypatch):
    gold = _gold(tmp_path)
    monkeypatch.setenv("APP_ENV", "general")
    stats = await module._process(gold=gold, export_dir=tmp_path / "missing")
    assert stats["added"] == 0 and stats["files"] == 0


@pytest.mark.asyncio
async def test_process_skips_other_environments(tmp_path, monkeypatch):
    gold = _gold(tmp_path)
    export = tmp_path / "export"
    export.mkdir()
    _export_file(export / "judgments-en.json", ("q001", IMG, 2))
    before = (gold / "judgments.json").read_bytes()
    monkeypatch.setenv("APP_ENV", "metal")
    stats = await module._process(gold=gold, export_dir=export)
    assert stats["skipped"] is True
    assert (gold / "judgments.json").read_bytes() == before


@pytest.mark.asyncio
async def test_process_fails_rather_than_overwrite(tmp_path, monkeypatch):
    gold = _gold(tmp_path, [Judgment("q001", IMG, 1, "owner", "2026-10-01", 1)])
    export = tmp_path / "export"
    export.mkdir()
    _export_file(export / "judgments-en.json", ("q001", IMG, 2))
    monkeypatch.setenv("APP_ENV", "general")
    before = (gold / "judgments.json").read_bytes()
    with pytest.raises(GoldError):
        await module._process(gold=gold, export_dir=export)
    assert (gold / "judgments.json").read_bytes() == before
