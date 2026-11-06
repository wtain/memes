"""
Unit tests for batch/comics_pipeline.py -- the after-build driver for a built comics. No real DB; every child job's main()
is mocked, matching batch/tests/test_recut_pipeline.py.
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

import batch.comics_pipeline as module
from batch.description_pipeline import FAILED, OK, SKIPPED_BUSY, SKIPPED_DEPENDENCY, SKIPPED_NOT_CONFIGURED, run_steps
from batch.registry import BatchRegistry
from repository.batch_runs import BatchAlreadyRunningError

CHILDREN = ["extract_text_from_memes", "build_image_embeddings", "build_ocr_lemmas", "build_tags_from_ocr",
            "classify_text_heavy", "build_ocr_text_embeddings"]


def _patched(mocks, tagging_configured=True):
    stack = ExitStack()
    for name, mock in mocks.items():
        stack.enter_context(patch.object(getattr(module, name), "main", mock))
    stack.enter_context(patch.object(module, "tagging_configured", return_value=tagging_configured))
    return stack


def test_steps_run_in_order():
    assert [s.name for s in module.STEPS] == [
        "ocr", "embeddings", "ocr_lemmas", "ocr_tags", "classify", "ocr_text_embeddings"]


def test_dependencies_match_the_design():
    needs = {s.name: s.needs for s in module.STEPS}
    assert needs == {
        "ocr": (), "embeddings": (), "ocr_lemmas": ("ocr",), "ocr_tags": ("ocr",),
        "classify": ("ocr",), "ocr_text_embeddings": ("classify",)}


@pytest.mark.asyncio
async def test_steps_call_the_children_with_the_documented_arguments():
    mocks = {name: AsyncMock() for name in CHILDREN}
    with _patched(mocks):
        results = await run_steps(module.STEPS, trigger="manual")

    mocks["extract_text_from_memes"].assert_awaited_once_with(trigger="manual")
    mocks["build_image_embeddings"].assert_awaited_once_with(trigger="manual", incremental=True)
    mocks["build_ocr_lemmas"].assert_awaited_once_with(trigger="manual", incremental=True)
    mocks["build_tags_from_ocr"].assert_awaited_once_with(trigger="manual", incremental=True)
    mocks["classify_text_heavy"].assert_awaited_once_with(trigger="manual")
    mocks["build_ocr_text_embeddings"].assert_awaited_once_with(trigger="manual")
    assert all(r["status"] == OK for r in results.values())


@pytest.mark.asyncio
async def test_failed_ocr_skips_dependents():
    mocks = {name: AsyncMock() for name in CHILDREN}
    mocks["extract_text_from_memes"].side_effect = RuntimeError("boom")
    with _patched(mocks):
        results = await run_steps(module.STEPS, trigger="manual")

    assert results["ocr"]["status"] == FAILED
    for name in ("ocr_lemmas", "ocr_tags", "classify", "ocr_text_embeddings"):
        assert results[name]["status"] == SKIPPED_DEPENDENCY
    assert results["embeddings"]["status"] == OK
    mocks["build_image_embeddings"].assert_awaited_once()
    mocks["classify_text_heavy"].assert_not_awaited()


@pytest.mark.asyncio
async def test_busy_child_is_skipped_not_failed():
    mocks = {name: AsyncMock() for name in CHILDREN}
    mocks["build_image_embeddings"].side_effect = BatchAlreadyRunningError("build_image_embeddings")
    with _patched(mocks):
        results = await run_steps(module.STEPS, trigger="manual")

    assert results["embeddings"]["status"] == SKIPPED_BUSY


@pytest.mark.asyncio
async def test_unconfigured_tag_step_is_skipped_up_front():
    recorded = {}

    async def record_stats(run_id, stats):
        recorded.update(stats)

    mocks = {name: AsyncMock() for name in CHILDREN}
    with _patched(mocks, tagging_configured=False), patch.object(module, "record_stats", record_stats):
        await module._run("run-id", "manual")  # must not raise

    mocks["build_tags_from_ocr"].assert_not_awaited()
    assert recorded["steps"]["ocr_tags"]["status"] == SKIPPED_NOT_CONFIGURED
    assert recorded["steps"]["classify"]["status"] == OK


@pytest.mark.asyncio
async def test_driver_fails_when_a_step_failed():
    recorded = {}

    async def record_stats(run_id, stats):
        recorded.update(stats)

    mocks = {name: AsyncMock() for name in CHILDREN}
    mocks["classify_text_heavy"].side_effect = RuntimeError("boom")
    with _patched(mocks), patch.object(module, "record_stats", record_stats):
        with pytest.raises(RuntimeError, match="steps failed: classify"):
            await module._run("run-id", "manual")

    assert recorded["steps"]["classify"]["status"] == FAILED
    assert recorded["steps"]["ocr_text_embeddings"]["status"] == SKIPPED_DEPENDENCY


def test_tagging_configured_follows_the_profile_files(tmp_path):
    (tmp_path / "tags.x.yaml").write_text("", encoding="utf-8")
    with patch.object(module.settings, "get", side_effect=lambda k, d=None: {
            "RULES.TAGGING_DATA_DIR": str(tmp_path), "GENERAL.TAGGING_PROFILE": "x"}.get(k, d)):
        assert module.tagging_configured() is False  # concepts file missing
        (tmp_path / "concepts.x.yaml").write_text("", encoding="utf-8")
        assert module.tagging_configured() is True


def test_the_job_is_registered_as_admin_triggerable():
    entry = BatchRegistry().get("comics_pipeline")
    assert entry == {"module": "batch.comics_pipeline", "kind": "comics_pipeline"}
