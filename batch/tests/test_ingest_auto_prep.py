"""
Unit tests for batch/ingest_auto_prep.py -- the ingestion prep chain driver. No real DB; all
5 chained steps' main() functions are mocked, matching batch/tests/test_move_flagged.py's
chaining-test style.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.ingest_auto_prep import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


def _patched_steps(module, **overrides):
    """Returns a dict of the 5 step mocks, pre-wired as no-op AsyncMocks unless overridden."""
    steps = {
        "ingest_hash_dedup": AsyncMock(),
        "ingest_validate_formats": AsyncMock(),
        "build_image_embeddings": AsyncMock(),
        "extract_text_from_memes": AsyncMock(),
        "ingest_find_duplicates": AsyncMock(),
    }
    steps.update(overrides)
    for name, mock in steps.items():
        getattr(module, name).main = mock
    return steps


class TestRunPrepChain:
    @pytest.mark.asyncio
    async def test_calls_all_five_steps_in_order_with_expected_args(self):
        import batch.ingest_auto_prep as module

        call_order = []

        steps = _patched_steps(module)
        for name, mock in steps.items():
            mock.side_effect = lambda *a, name=name, **kw: call_order.append(name)

        with patch.object(module.settings, "BASE_PATH", "/fake/base"):
            await module._run_prep_chain()

        assert call_order == [
            "ingest_hash_dedup", "ingest_validate_formats", "build_image_embeddings",
            "extract_text_from_memes", "ingest_find_duplicates",
        ]
        steps["ingest_hash_dedup"].assert_awaited_once_with(env=None)
        steps["ingest_validate_formats"].assert_awaited_once_with(env=None)
        steps["build_image_embeddings"].assert_awaited_once_with(incremental=True, target_status="pending")
        steps["extract_text_from_memes"].assert_awaited_once_with("/fake/base", target_status="pending")
        steps["ingest_find_duplicates"].assert_awaited_once_with(env=None, tier="tier_a", k=None)

    @pytest.mark.asyncio
    async def test_runtime_error_from_a_later_step_is_swallowed(self):
        """The common case: the inbox is empty and no ingestion run is active, so steps 2-5
        raise 'No ingestion run is currently in progress' -- that must not fail the tick."""
        import batch.ingest_auto_prep as module

        steps = _patched_steps(
            module,
            ingest_validate_formats=AsyncMock(side_effect=RuntimeError("No ingestion run is currently in progress")),
        )

        with patch.object(module.settings, "BASE_PATH", "/fake/base"):
            await module._run_prep_chain()  # must not raise

        steps["ingest_hash_dedup"].assert_awaited_once()
        steps["build_image_embeddings"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runtime_error_from_hash_dedup_propagates(self):
        """Step 1 failing (e.g. PATH_INGESTION_SOURCE misconfigured) must fail the whole tick,
        not be swallowed like steps 2-5's expected 'nothing to do' error."""
        import batch.ingest_auto_prep as module

        steps = _patched_steps(
            module,
            ingest_hash_dedup=AsyncMock(side_effect=RuntimeError("PATH_INGESTION_SOURCE is required but not set")),
        )

        with patch.object(module.settings, "BASE_PATH", "/fake/base"):
            with pytest.raises(RuntimeError, match="PATH_INGESTION_SOURCE"):
                await module._run_prep_chain()

        steps["ingest_validate_formats"].assert_not_awaited()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path(self):
        import batch.ingest_auto_prep as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_run_prep_chain", AsyncMock()) as chain_mock:
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="ingestion_auto_prep", trigger="scheduled")
        chain_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        import batch.ingest_auto_prep as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_run_prep_chain", AsyncMock()) as chain_mock:
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        chain_mock.assert_awaited_once_with()
