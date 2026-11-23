"""
Unit tests for batch/recut_pipeline.py -- the recut pipeline driver. No real DB; every child job's main() is mocked,
matching batch/tests/test_refresh_recut_cuts.py.
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

import batch.recut_pipeline as module
from batch.description_pipeline import FAILED, OK, SKIPPED_BUSY, run_steps
from batch.registry import BatchRegistry
from repository.batch_runs import BatchAlreadyRunningError

CHILDREN = ["create_recut_cuts", "build_image_embeddings", "refresh_recut_cuts"]


def _patched(mocks, settings_value=None):
    stack = ExitStack()
    for name, mock in mocks.items():
        stack.enter_context(patch.object(getattr(module, name), "main", mock))
    stack.enter_context(patch.object(module.settings, "get", return_value=settings_value))
    return stack


def test_order_and_independence_match_the_design():
    assert [(s.name, s.needs) for s in module.STEPS] == [("cuts", ()), ("cut_embeddings", ()), ("refresh", ())]


@pytest.mark.asyncio
async def test_steps_call_the_children_with_the_documented_arguments():
    mocks = {name: AsyncMock() for name in CHILDREN}
    with _patched(mocks, settings_value=250):
        results = await run_steps(module.STEPS, trigger="manual")

    mocks["create_recut_cuts"].assert_awaited_once_with(trigger="manual", limit=250)
    mocks["build_image_embeddings"].assert_awaited_once_with(
        trigger="manual", incremental=True, target_status="cut_proposed")
    mocks["refresh_recut_cuts"].assert_awaited_once_with(trigger="manual")
    assert {name: r["status"] for name, r in results.items()} == {"cuts": OK, "cut_embeddings": OK, "refresh": OK}


@pytest.mark.asyncio
async def test_an_unset_cut_cap_means_no_limit():
    mocks = {name: AsyncMock() for name in CHILDREN}
    with _patched(mocks, settings_value=None):
        await run_steps(module.STEPS, trigger="manual")

    mocks["create_recut_cuts"].assert_awaited_once_with(trigger="manual", limit=None)


@pytest.mark.asyncio
async def test_a_failed_step_does_not_stop_the_others():
    mocks = {name: AsyncMock() for name in CHILDREN}
    mocks["create_recut_cuts"].side_effect = RuntimeError("20 failures in a row")
    with _patched(mocks):
        results = await run_steps(module.STEPS, trigger="manual")

    assert results["cuts"]["status"] == FAILED
    assert results["cut_embeddings"]["status"] == OK and results["refresh"]["status"] == OK
    mocks["build_image_embeddings"].assert_awaited_once()
    mocks["refresh_recut_cuts"].assert_awaited_once()


@pytest.mark.asyncio
async def test_a_child_that_is_already_running_is_skipped_not_failed():
    mocks = {name: AsyncMock() for name in CHILDREN}
    mocks["build_image_embeddings"].side_effect = BatchAlreadyRunningError("build_image_embeddings")
    with _patched(mocks):
        results = await run_steps(module.STEPS, trigger="manual")

    assert results["cut_embeddings"]["status"] == SKIPPED_BUSY


@pytest.mark.asyncio
async def test_the_driver_fails_when_a_step_failed_and_records_the_step_results():
    recorded = {}

    async def record_stats(run_id, stats):
        recorded.update(stats)

    mocks = {name: AsyncMock() for name in CHILDREN}
    mocks["refresh_recut_cuts"].side_effect = RuntimeError("boom")
    with _patched(mocks), patch.object(module, "record_stats", record_stats):
        with pytest.raises(RuntimeError, match="steps failed: refresh"):
            await module._run("run-id", "manual")

    assert recorded["steps"]["refresh"]["status"] == FAILED and recorded["steps"]["cuts"]["status"] == OK


def test_the_job_is_registered_as_admin_triggerable():
    entry = BatchRegistry().get("recut_pipeline")
    assert entry == {"module": "batch.recut_pipeline", "kind": "recut_pipeline"}


@pytest.mark.asyncio
async def test_reports_each_step_before_running_it(monkeypatch):
    import batch.description_pipeline as dp
    events = []
    monkeypatch.setattr(dp, "report_step", lambda i, n, label: events.append(("report", i, n, label)))
    mocks = {name: AsyncMock(side_effect=lambda **kw: events.append(("run",))) for name in CHILDREN}
    with _patched(mocks):
        await run_steps(module.STEPS, trigger="manual")

    assert events == [("report", 1, 3, "cuts"), ("run",), ("report", 2, 3, "cut_embeddings"), ("run",),
                      ("report", 3, 3, "refresh"), ("run",)]


@pytest.mark.asyncio
async def test_skipped_step_still_reports(monkeypatch):
    import batch.description_pipeline as dp
    reports = []
    monkeypatch.setattr(dp, "report_step", lambda i, n, label: reports.append((i, n, label)))
    mocks = {"create_recut_cuts": AsyncMock(), "build_image_embeddings": AsyncMock(
        side_effect=BatchAlreadyRunningError("busy")), "refresh_recut_cuts": AsyncMock()}
    with _patched(mocks):
        results = await run_steps(module.STEPS, trigger="manual")

    assert results["cut_embeddings"]["status"] == SKIPPED_BUSY
    assert reports == [(1, 3, "cuts"), (2, 3, "cut_embeddings"), (3, 3, "refresh")]
