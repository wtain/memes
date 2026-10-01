"""
Unit tests for batch/description_pipeline.py -- the description/tagging pipeline driver. No
real DB; every child job's main() is mocked, matching batch/tests/test_ingest_auto_prep.py.
See docs/superpowers/specs/2026-10-01-description-tagging-pipeline-driver-design.md.
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

import batch.description_pipeline as module
from batch.description_pipeline import FAILED, OK, SKIPPED_BUSY, SKIPPED_DEPENDENCY, Step, run_steps
from batch.registry import BatchRegistry
from repository.batch_runs import BatchAlreadyRunningError


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


def _step(name, calls, needs=(), raises=None):
    async def run(trigger):
        calls.append((name, trigger))
        if raises is not None:
            raise raises

    return Step(name, run, needs)


class TestRunSteps:
    @pytest.mark.asyncio
    async def test_runs_steps_in_order_passing_the_trigger(self):
        calls = []

        results = await run_steps([_step("a", calls), _step("b", calls)], trigger="manual")

        assert calls == [("a", "manual"), ("b", "manual")]
        assert results == {"a": {"status": OK}, "b": {"status": OK}}

    @pytest.mark.asyncio
    async def test_failure_skips_dependents_but_not_independent_steps(self):
        calls = []
        steps = [
            _step("independent_first", calls),
            _step("parent", calls, raises=RuntimeError("boom")),
            _step("child", calls, needs=("parent",)),
            _step("independent_last", calls),
        ]

        results = await run_steps(steps, trigger="manual")

        assert [name for name, _ in calls] == ["independent_first", "parent", "independent_last"]
        assert results["parent"] == {"status": FAILED, "error": "boom"}
        assert results["child"]["status"] == SKIPPED_DEPENDENCY
        assert results["independent_last"] == {"status": OK}

    @pytest.mark.asyncio
    async def test_skip_propagates_through_a_chain_of_dependents(self):
        calls = []
        steps = [
            _step("a", calls, raises=RuntimeError("boom")),
            _step("b", calls, needs=("a",)),
            _step("c", calls, needs=("b",)),
        ]

        results = await run_steps(steps, trigger="manual")

        assert [name for name, _ in calls] == ["a"]
        assert results["c"]["status"] == SKIPPED_DEPENDENCY

    @pytest.mark.asyncio
    async def test_already_running_step_is_busy_and_does_not_block_dependents(self):
        calls = []
        steps = [
            _step("parent", calls, raises=BatchAlreadyRunningError("build_image_descriptions")),
            _step("child", calls, needs=("parent",)),
        ]

        results = await run_steps(steps, trigger="manual")

        assert results["parent"]["status"] == SKIPPED_BUSY
        assert results["child"] == {"status": OK}


class TestDefaultSteps:
    def test_order_and_dependencies_match_the_design(self):
        assert [(s.name, s.needs) for s in module.STEPS] == [
            ("ocr_tags", ()),
            ("note_lemmas", ()),
            ("note_embeddings", ()),
            ("note_tags", ()),
            ("describe", ()),
            ("description_embeddings", ("describe",)),
            ("description_lemmas", ("describe",)),
            ("description_tags", ("describe",)),
        ]

    def test_every_dependency_is_declared_before_its_dependent(self):
        seen = set()
        for step in module.STEPS:
            assert set(step.needs) <= seen
            seen.add(step.name)

    @pytest.mark.asyncio
    async def test_steps_call_the_expected_child_jobs(self):
        mocks = {
            "build_tags_from_ocr": AsyncMock(),
            "build_description_note_lemmas": AsyncMock(),
            "build_description_note_embeddings": AsyncMock(),
            "build_tags_from_notes": AsyncMock(),
            "build_description_lemmas": AsyncMock(),
            "build_image_descriptions": AsyncMock(),
            "build_image_description_embeddings": AsyncMock(),
            "build_tags_from_descriptions": AsyncMock(),
        }
        with ExitStack() as stack:
            for name, mock in mocks.items():
                stack.enter_context(patch.object(getattr(module, name), "main", mock))
            stack.enter_context(patch.object(module.settings, "get", return_value=40))
            await run_steps(module.STEPS, trigger="manual")

        mocks["build_tags_from_ocr"].assert_awaited_once_with(trigger="manual", incremental=True)
        mocks["build_description_note_lemmas"].assert_awaited_once_with(trigger="manual")
        mocks["build_description_note_embeddings"].assert_awaited_once_with(trigger="manual")
        mocks["build_tags_from_notes"].assert_awaited_once_with(trigger="manual", incremental=True)
        mocks["build_description_lemmas"].assert_awaited_once_with(trigger="manual")
        mocks["build_image_descriptions"].assert_awaited_once_with(trigger="manual", limit=40)
        mocks["build_image_description_embeddings"].assert_awaited_once_with(trigger="manual")
        mocks["build_tags_from_descriptions"].assert_awaited_once_with(trigger="manual", incremental=True)

    @pytest.mark.asyncio
    async def test_describe_failure_skips_lemmas_but_not_note_tags(self):
        names = ["build_tags_from_ocr", "build_description_note_lemmas", "build_description_note_embeddings",
                 "build_tags_from_notes", "build_description_lemmas", "build_image_descriptions",
                 "build_image_description_embeddings", "build_tags_from_descriptions"]
        mocks = {n: AsyncMock() for n in names}
        mocks["build_image_descriptions"].side_effect = RuntimeError("ollama down")
        with ExitStack() as stack:
            for name, mock in mocks.items():
                stack.enter_context(patch.object(getattr(module, name), "main", mock))
            stack.enter_context(patch.object(module.settings, "get", return_value=None))
            results = await run_steps(module.STEPS, trigger="manual")

        assert results["note_tags"] == {"status": OK}
        assert results["description_lemmas"]["status"] == SKIPPED_DEPENDENCY
        mocks["build_description_lemmas"].assert_not_awaited()
        mocks["build_tags_from_notes"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_describe_is_unlimited_when_max_per_run_is_not_configured(self):
        describe = AsyncMock()

        with patch.object(module.build_image_descriptions, "main", describe),              patch.object(module.settings, "get", return_value=None) as get_mock:
            step = next(s for s in module.STEPS if s.name == "describe")
            await step.run("manual")

        get_mock.assert_called_once_with("image_descriptions.max_per_run")
        describe.assert_awaited_once_with(trigger="manual", limit=None)


class TestMain:
    @pytest.mark.asyncio
    async def test_all_ok_records_stats_and_completes(self):
        results = {"a": {"status": OK}}
        record_mock = AsyncMock()

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "run_steps", AsyncMock(return_value=results)), \
             patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual")

        tracked_run_mock.assert_called_once_with(kind="description_pipeline", trigger="manual")
        record_mock.assert_awaited_once_with("run-1", {"steps": results})

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_uses_the_given_run_id(self):
        results = {"a": {"status": OK}, "b": {"status": SKIPPED_BUSY}}
        record_mock = AsyncMock()

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "run_steps", AsyncMock(return_value=results)), \
             patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        record_mock.assert_awaited_once_with("existing-run-1", {"steps": results})

    @pytest.mark.asyncio
    async def test_any_failed_step_fails_the_run_after_recording_stats(self):
        results = {
            "ok_step": {"status": OK},
            "bad_step": {"status": FAILED, "error": "boom"},
            "skipped_step": {"status": SKIPPED_DEPENDENCY},
        }
        record_mock = AsyncMock()

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "run_steps", AsyncMock(return_value=results)), \
             patch.object(module, "record_stats", record_mock):
            with pytest.raises(RuntimeError, match="bad_step"):
                await module.main(trigger="manual")

        record_mock.assert_awaited_once_with("run-1", {"steps": results})


def test_registered_in_batch_registry():
    entry = BatchRegistry().get("description_pipeline")

    assert entry == {"module": "batch.description_pipeline", "kind": "description_pipeline"}
