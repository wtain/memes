"""Unit tests for batch/export_ratings.py: tracking contract and environment guard. No real DB."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import batch.export_ratings as module
from evaluation.gold import (Corpus, CorpusImage, ExpectedProfile, GoldQuery, Judgment, save_corpus, save_judgments,
                             save_queries)

IMG = "11111111-1111-1111-1111-111111111111"
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
    save_corpus(Corpus("general", 7, "2026-10-04", (CorpusImage(IMG, "h", "ocr only"),)), gold / "corpus.json")
    save_queries([GoldQuery("q001", "en", "boss moved the deadline", "short", "easy", EXPECTED, True)],
                 gold / "queries.json")
    save_judgments(list(judgments), gold / "judgments.json")
    return gold


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_records_the_stats(self):
        record_mock = AsyncMock()
        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_mock, \
             patch.object(module, "_process", AsyncMock(return_value={"ratings": 2})), \
             patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual")
        tracked_mock.assert_called_once_with(kind="export_ratings", trigger="manual")
        record_mock.assert_awaited_once_with("run-1", {"ratings": 2})

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_records_on_the_given_run(self):
        record_mock = AsyncMock()
        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", AsyncMock(return_value={"ratings": 0})), \
             patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual", run_id="existing-run-1")
        finish_mock.assert_called_once_with("existing-run-1")
        record_mock.assert_awaited_once_with("existing-run-1", {"ratings": 0})


@pytest.mark.asyncio
async def test_process_skips_other_environments_without_opening_a_session(tmp_path, monkeypatch):
    gold = _gold(tmp_path)
    export = tmp_path / "export"
    session_factory = MagicMock()
    monkeypatch.setenv("APP_ENV", "metal")
    with patch.object(module, "AsyncSessionLocal", session_factory):
        stats = await module._process(gold=gold, export_dir=export)
    assert stats["skipped"] is True
    session_factory.assert_not_called()
    assert not export.exists()


@pytest.mark.asyncio
async def test_process_exports_with_the_next_round(tmp_path, monkeypatch):
    gold = _gold(tmp_path, [Judgment("q001", IMG, 1, "owner", "2026-10-01", 4)])
    export = tmp_path / "export"
    monkeypatch.setenv("APP_ENV", "general")
    export_mock = AsyncMock(return_value={"ratings": 0, "importable": 0, "files": 0, "round": 5})
    session = MagicMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    with patch.object(module, "AsyncSessionLocal", factory), \
         patch.object(module.ratings_job, "export_ratings", export_mock):
        stats = await module._process(gold=gold, export_dir=export)
    assert stats == export_mock.return_value
    args, kwargs = export_mock.call_args
    assert args == (session, gold, export) and kwargs["round_number"] == 5
