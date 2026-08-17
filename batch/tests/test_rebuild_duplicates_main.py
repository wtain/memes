"""
Unit tests for batch/rebuild_duplicates.py's main() self-tracking contract, default k/threshold
resolution, and chained clusterize call. _process() itself (and rebuild_active_library/
find_duplicates) is mocked here -- their query logic is covered by
tests/integration/test_rebuild_duplicates.py. Mirrors batch/tests/test_build_concept_embeddings_main.py's
chaining-test style.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.rebuild_duplicates import main
from repository.batch_runs import BatchAlreadyRunningError


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_resolves_defaults_and_chains_clusterize(self):
        process_mock = AsyncMock()
        import batch.rebuild_duplicates as module

        with patch.object(module, "settings") as mock_settings, \
             patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "clusterize") as mock_clusterize:
            mock_settings.DUPLICATES.K = 20
            mock_settings.DUPLICATES.THRESHOLD = 0.3
            mock_clusterize.main = AsyncMock()
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="rebuild_duplicates", trigger="scheduled")
        process_mock.assert_awaited_once_with(20, 0.3, False)
        mock_clusterize.main.assert_awaited_once_with(trigger="scheduled")

    @pytest.mark.asyncio
    async def test_explicit_k_threshold_full_override_settings_defaults(self):
        process_mock = AsyncMock()
        import batch.rebuild_duplicates as module

        with patch.object(module, "settings") as mock_settings, \
             patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "clusterize") as mock_clusterize:
            mock_settings.DUPLICATES.K = 20
            mock_settings.DUPLICATES.THRESHOLD = 0.3
            mock_clusterize.main = AsyncMock()
            await main(trigger="manual", k=5, threshold=0.1, full=True)

        process_mock.assert_awaited_once_with(5, 0.1, True)

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.rebuild_duplicates as module

        with patch.object(module, "settings") as mock_settings, \
             patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "clusterize") as mock_clusterize:
            mock_settings.DUPLICATES.K = 20
            mock_settings.DUPLICATES.THRESHOLD = 0.3
            mock_clusterize.main = AsyncMock()
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        mock_clusterize.main.assert_awaited_once_with(trigger="manual")

    @pytest.mark.asyncio
    async def test_no_chain_skips_clusterize(self):
        process_mock = AsyncMock()
        import batch.rebuild_duplicates as module

        with patch.object(module, "settings") as mock_settings, \
             patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "clusterize") as mock_clusterize:
            mock_settings.DUPLICATES.K = 20
            mock_settings.DUPLICATES.THRESHOLD = 0.3
            mock_clusterize.main = AsyncMock()
            await main(trigger="scheduled", chain=False)

        mock_clusterize.main.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_chained_batch_already_running_error_is_swallowed(self):
        process_mock = AsyncMock()
        import batch.rebuild_duplicates as module

        with patch.object(module, "settings") as mock_settings, \
             patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "clusterize") as mock_clusterize:
            mock_settings.DUPLICATES.K = 20
            mock_settings.DUPLICATES.THRESHOLD = 0.3
            mock_clusterize.main = AsyncMock(side_effect=BatchAlreadyRunningError("clusterize"))
            await main(trigger="scheduled")  # must not raise

        mock_clusterize.main.assert_awaited_once_with(trigger="scheduled")
