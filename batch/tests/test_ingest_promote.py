"""
Unit tests for batch/ingest_promote.py's main() self-tracking contract. _process() itself
is mocked -- its own promote/maybe_complete_run logic is covered by
tests/integration/test_ingest_promote.py. Mirrors batch/tests/test_build_concept_embeddings_main.py's
style for a non-chaining self-tracked script.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.ingest_promote import main


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
        import batch.ingest_promote as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="ingest_promote", trigger="scheduled")
        process_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.ingest_promote as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_no_active_ingestion_run_propagates_as_failure(self):
        """A manual click with no active ingestion run must surface as a failed run, not be
        swallowed -- unlike ingest_auto_prep's scheduler-tick 'nothing to do' handling, this
        is an explicit human action and the error should be visible in admin history."""
        process_mock = AsyncMock(
            side_effect=RuntimeError("No ingestion run is currently in progress -- nothing to promote.")
        )
        import batch.ingest_promote as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "_process", process_mock):
            with pytest.raises(RuntimeError, match="No ingestion run is currently in progress"):
                await main(trigger="manual")
