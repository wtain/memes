"""
Unit tests for batch/build_concept_embeddings.py's main() self-tracking contract. No real
DB/model loading -- _process is mocked entirely.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_concept_embeddings import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path(self):
        process_mock = AsyncMock()
        import batch.build_concept_embeddings as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="build_concept_embeddings", trigger="scheduled")
        process_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.build_concept_embeddings as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with()
