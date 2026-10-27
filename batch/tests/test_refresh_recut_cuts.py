"""
Unit tests for batch/refresh_recut_cuts.py -- the recut refresh driver. No real DB; every child job's main() is mocked,
matching batch/tests/test_description_pipeline.py.
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

import batch.refresh_recut_cuts as module
from batch.description_pipeline import FAILED, OK, SKIPPED_BUSY, SKIPPED_DEPENDENCY, run_steps
from batch.registry import BatchRegistry

CHILDREN = ["extract_text_from_memes", "classify_text_heavy", "build_ocr_text_embeddings", "build_ocr_lemmas",
            "build_tags_from_ocr", "build_image_descriptions", "build_image_description_embeddings",
            "build_description_lemmas", "build_tags_from_descriptions", "build_meme_profiles",
            "build_meme_profile_embeddings"]


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


def _patched(mocks, settings_value=None):
    stack = ExitStack()
    for name, mock in mocks.items():
        stack.enter_context(patch.object(getattr(module, name), "main", mock))
    stack.enter_context(patch.object(module.settings, "get", return_value=settings_value))
    return stack


def test_order_and_dependencies_match_the_design():
    assert [(s.name, s.needs) for s in module.STEPS] == [
        ("ocr", ()),
        ("classify", ("ocr",)),
        ("ocr_text_embeddings", ("classify",)),
        ("ocr_lemmas", ("ocr",)),
        ("ocr_tags", ("ocr",)),
        ("describe", ()),
        ("description_embeddings", ("describe",)),
        ("description_lemmas", ("describe",)),
        ("description_tags", ("describe",)),
        ("meme_profiles", ("ocr", "describe")),
        ("meme_profile_embeddings", ("meme_profiles",)),
    ]


@pytest.mark.asyncio
async def test_steps_call_the_refresh_modes_of_the_child_jobs():
    mocks = {name: AsyncMock() for name in CHILDREN}
    with _patched(mocks, settings_value=40):
        await run_steps(module.STEPS, trigger="manual")

    mocks["extract_text_from_memes"].assert_awaited_once_with(trigger="manual", refresh_stale=True)
    mocks["classify_text_heavy"].assert_awaited_once_with(status="cut_proposed", trigger="manual")
    mocks["build_ocr_text_embeddings"].assert_awaited_once_with(status="cut_proposed", trigger="manual")
    mocks["build_ocr_lemmas"].assert_awaited_once_with(trigger="manual", refresh_stale=True)
    mocks["build_tags_from_ocr"].assert_awaited_once_with(trigger="manual", refresh_stale=True)
    mocks["build_image_descriptions"].assert_awaited_once_with(trigger="manual", refresh_stale=True, limit=40)
    mocks["build_image_description_embeddings"].assert_awaited_once_with(trigger="manual")
    mocks["build_description_lemmas"].assert_awaited_once_with(trigger="manual")
    mocks["build_tags_from_descriptions"].assert_awaited_once_with(trigger="manual", refresh_stale=True)
    mocks["build_meme_profiles"].assert_awaited_once_with(trigger="manual", status="cut_proposed", limit=40)
    mocks["build_meme_profile_embeddings"].assert_awaited_once_with(trigger="manual")


@pytest.mark.asyncio
async def test_a_failed_ocr_step_skips_its_dependants_but_not_the_description_chain():
    mocks = {name: AsyncMock() for name in CHILDREN}
    mocks["extract_text_from_memes"].side_effect = RuntimeError("gpu gone")
    with _patched(mocks):
        results = await run_steps(module.STEPS, trigger="manual")

    assert results["ocr"]["status"] == FAILED
    for dependant in ("classify", "ocr_text_embeddings", "ocr_lemmas", "ocr_tags", "meme_profiles",
                      "meme_profile_embeddings"):
        assert results[dependant]["status"] == SKIPPED_DEPENDENCY, dependant
    assert results["describe"] == {"status": OK}
    assert results["description_tags"] == {"status": OK}
    mocks["classify_text_heavy"].assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unlimited_describe_when_max_per_run_is_not_configured():
    describe = AsyncMock()
    with patch.object(module.build_image_descriptions, "main", describe), \
         patch.object(module.settings, "get", return_value=None):
        await next(s for s in module.STEPS if s.name == "describe").run("manual")

    describe.assert_awaited_once_with(trigger="manual", refresh_stale=True, limit=None)


class TestMain:
    @pytest.mark.asyncio
    async def test_records_the_steps_and_the_stale_counts_and_completes(self):
        results = {"ocr": {"status": OK}}
        record_mock = AsyncMock()

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "run_steps", AsyncMock(return_value=results)), \
             patch.object(module, "_stale_rows_total", AsyncMock(return_value={"ocr_texts": 2})), \
             patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual")

        tracked_run_mock.assert_called_once_with(kind="refresh_recut_cuts", trigger="manual")
        record_mock.assert_awaited_once_with("run-1", {"steps": results, "stale_rows_total": {"ocr_texts": 2}})

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_uses_the_given_run_id(self):
        results = {"ocr": {"status": OK}, "describe": {"status": SKIPPED_BUSY}}
        record_mock = AsyncMock()

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "run_steps", AsyncMock(return_value=results)), \
             patch.object(module, "_stale_rows_total", AsyncMock(return_value={})), \
             patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        record_mock.assert_awaited_once_with("existing-run-1", {"steps": results, "stale_rows_total": {}})

    @pytest.mark.asyncio
    async def test_any_failed_step_fails_the_run_after_recording_stats(self):
        results = {"ocr": {"status": FAILED, "error": "boom"}, "classify": {"status": SKIPPED_DEPENDENCY}}
        record_mock = AsyncMock()

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "run_steps", AsyncMock(return_value=results)), \
             patch.object(module, "_stale_rows_total", AsyncMock(return_value={})), \
             patch.object(module, "record_stats", record_mock):
            with pytest.raises(RuntimeError, match="ocr"):
                await module.main(trigger="manual")

        record_mock.assert_awaited_once()


def test_registered_in_batch_registry():
    assert BatchRegistry().get("refresh_recut_cuts") == {
        "module": "batch.refresh_recut_cuts", "kind": "refresh_recut_cuts"}
