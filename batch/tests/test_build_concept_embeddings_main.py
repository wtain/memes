"""
Unit tests for batch/build_concept_embeddings.py's main() self-tracking contract and its
chained tag_images_from_concepts call. No real DB/model loading -- _process is mocked
entirely. Chaining tests mirror batch/tests/test_move_flagged.py's TestMain style (patching
the chained module and setting .main on the mock).
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_concept_embeddings import main
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
    async def test_tracked_run_path(self):
        process_mock = AsyncMock()
        import batch.build_concept_embeddings as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "tag_images_from_concepts") as mock_tag_images:
            mock_tag_images.main = AsyncMock()
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="build_concept_embeddings", trigger="scheduled")
        process_mock.assert_awaited_once_with()
        mock_tag_images.main.assert_awaited_once_with(trigger="scheduled")

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.build_concept_embeddings as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "tag_images_from_concepts") as mock_tag_images:
            mock_tag_images.main = AsyncMock()
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with()
        mock_tag_images.main.assert_awaited_once_with(trigger="manual")

    @pytest.mark.asyncio
    async def test_no_chain_skips_tag_images_from_concepts(self):
        process_mock = AsyncMock()
        import batch.build_concept_embeddings as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "tag_images_from_concepts") as mock_tag_images:
            mock_tag_images.main = AsyncMock()
            await main(trigger="scheduled", chain=False)

        mock_tag_images.main.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_chained_batch_already_running_error_is_swallowed(self):
        process_mock = AsyncMock()
        import batch.build_concept_embeddings as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "tag_images_from_concepts") as mock_tag_images:
            mock_tag_images.main = AsyncMock(
                side_effect=BatchAlreadyRunningError("tag_images_from_concepts")
            )
            await main(trigger="scheduled")  # must not raise

        mock_tag_images.main.assert_awaited_once_with(trigger="scheduled")
