"""
Unit tests for batch/build_image_description_embeddings.py's main() self-tracking contract.
No real DB -- mirrors batch/tests/test_build_tags_from_descriptions.py's style.
"""
from unittest.mock import AsyncMock, patch

import pytest

import batch.build_image_description_embeddings as module
from batch.registry import BatchRegistry


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_records_stats(self):
        process_mock = AsyncMock(return_value={"descriptions_embedded": 3})
        record_mock = AsyncMock()

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual")

        tracked_run_mock.assert_called_once_with(kind="build_image_description_embeddings", trigger="manual")
        process_mock.assert_awaited_once_with(reset=False)
        record_mock.assert_awaited_once_with("run-1", {"descriptions_embedded": 3})

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_records_stats_on_the_given_run(self):
        process_mock = AsyncMock(return_value={"descriptions_embedded": 0})
        record_mock = AsyncMock()

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual", run_id="existing-run-1", reset=True)

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with(reset=True)
        record_mock.assert_awaited_once_with("existing-run-1", {"descriptions_embedded": 0})


def test_registered_in_batch_registry():
    entry = BatchRegistry().get("build_image_description_embeddings")

    assert entry == {
        "module": "batch.build_image_description_embeddings",
        "kind": "build_image_description_embeddings",
    }
