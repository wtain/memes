"""
Unit tests for batch/build_tags_from_descriptions.py's main() self-tracking contract. No
real DB -- mirrors batch/tests/test_build_tags_from_ocr.py's style.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_tags_from_descriptions import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_forces_incremental_true_by_default(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_descriptions as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="build_tags_from_descriptions", trigger="scheduled")
        process_mock.assert_awaited_once_with(incremental=True)

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_forces_incremental_true_by_default(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_descriptions as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with(incremental=True)
