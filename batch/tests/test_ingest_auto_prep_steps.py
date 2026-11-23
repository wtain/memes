"""Step reporting of batch/ingest_auto_prep.py: report_step(i, n, name) right before each of the eight steps."""
from unittest.mock import AsyncMock

import pytest

import batch.ingest_auto_prep as module
from repository.batch_runs import BatchAlreadyRunningError

NAMES = ["ingest_hash_dedup", "ingest_validate_formats", "build_image_embeddings", "extract_text_from_memes",
         "build_ocr_lemmas", "classify_text_heavy", "build_ocr_text_embeddings", "ingest_find_duplicates"]


@pytest.fixture
def events(monkeypatch):
    events = []
    monkeypatch.setattr(module, "report_step", lambda i, n, label: events.append(("report", i, n, label)))
    for name in NAMES:
        mod = getattr(module, name)
        monkeypatch.setattr(mod, "main", AsyncMock(side_effect=lambda *a, _n=name, **kw: events.append(("run", _n))))
    return events


@pytest.mark.asyncio
async def test_reports_each_step_before_running_it(events):
    await module._run_prep_chain()

    expected = []
    for i, name in enumerate(NAMES, 1):
        expected += [("report", i, 8, name), ("run", name)]
    assert events == expected


@pytest.mark.asyncio
async def test_step_numbers_cover_eight_steps_in_documented_order(events):
    await module._run_prep_chain()

    assert [e[1:] for e in events if e[0] == "report"] == [(i, 8, n) for i, n in enumerate(NAMES, 1)]


@pytest.mark.asyncio
async def test_stopping_early_reports_only_reached_steps(events, monkeypatch):
    monkeypatch.setattr(module.build_ocr_lemmas, "main", AsyncMock(side_effect=BatchAlreadyRunningError("busy")))
    await module._run_prep_chain()

    assert [e[3] for e in events if e[0] == "report"] == NAMES[:5]
