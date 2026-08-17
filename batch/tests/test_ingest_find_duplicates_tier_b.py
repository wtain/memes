"""
Unit tests for batch/ingest_find_duplicates_tier_b.py -- the admin-triggerable Tier B
driver. ingest_find_duplicates.main() itself is mocked; its own tier-specific logic is
covered by tests/integration/test_ingest_find_duplicates.py. Mirrors
batch/tests/test_ingest_auto_prep.py's TestMain style for a driver script.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.ingest_find_duplicates_tier_b import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_calls_tier_b(self):
        import batch.ingest_find_duplicates_tier_b as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "ingest_find_duplicates") as mock_ifd:
            mock_ifd.main = AsyncMock()
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="ingest_find_duplicates_tier_b", trigger="scheduled")
        mock_ifd.main.assert_awaited_once_with(env=None, tier="tier_b", k=None)

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_calls_tier_b(self):
        import batch.ingest_find_duplicates_tier_b as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "ingest_find_duplicates") as mock_ifd:
            mock_ifd.main = AsyncMock()
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        mock_ifd.main.assert_awaited_once_with(env=None, tier="tier_b", k=None)

    @pytest.mark.asyncio
    async def test_no_active_ingestion_run_propagates_as_failure(self):
        import batch.ingest_find_duplicates_tier_b as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "ingest_find_duplicates") as mock_ifd:
            mock_ifd.main = AsyncMock(
                side_effect=RuntimeError("No ingestion run is currently in progress -- run ingest_hash_dedup.py first.")
            )
            with pytest.raises(RuntimeError, match="No ingestion run is currently in progress"):
                await main(trigger="manual")
