"""
main() contract and registry entries for build_image_embeddings and extract_text_from_memes, which
became run-tracked so the scheduler/admin wrapper can start them. No real DB, no models.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import batch.build_image_embeddings as embeddings_module
import batch.extract_text_from_memes as ocr_module
from batch.registry import BatchRegistry


def _ctx(value=None):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=value)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestBuildImageEmbeddingsMain:
    @pytest.mark.asyncio
    async def test_wrapper_call_is_incremental_and_active(self):
        run_id = uuid.uuid4()
        with patch.object(embeddings_module, "finish_existing_run", return_value=_ctx()) as finish, \
             patch.object(embeddings_module, "_process", AsyncMock()) as process:
            await embeddings_module.main(trigger="manual", run_id=run_id)

        finish.assert_called_once_with(run_id)
        process.assert_awaited_once_with(True, "active")

    @pytest.mark.asyncio
    async def test_self_tracked_under_its_own_kind(self):
        with patch.object(embeddings_module, "tracked_run", return_value=_ctx()) as tracked, \
             patch.object(embeddings_module, "_process", AsyncMock()) as process:
            await embeddings_module.main(trigger="scheduled", incremental=False, target_status="pending")

        tracked.assert_called_once_with(kind="build_image_embeddings", trigger="scheduled")
        process.assert_awaited_once_with(False, "pending")

    def test_registered_in_batch_registry(self):
        assert BatchRegistry().get("build_image_embeddings") == {
            "module": "batch.build_image_embeddings", "kind": "build_image_embeddings"}


class TestExtractTextFromMemesMain:
    @pytest.mark.asyncio
    async def test_wrapper_call_scans_base_path_with_active_status(self):
        run_id = uuid.uuid4()
        with patch.object(ocr_module, "finish_existing_run", return_value=_ctx()) as finish, \
             patch.object(ocr_module, "_process", AsyncMock()) as process, \
             patch.object(ocr_module.settings, "BASE_PATH", "/fake/base"):
            await ocr_module.main(trigger="manual", run_id=run_id)

        finish.assert_called_once_with(run_id)
        process.assert_awaited_once_with("/fake/base", "active", False)

    @pytest.mark.asyncio
    async def test_self_tracked_under_its_own_kind(self):
        with patch.object(ocr_module, "tracked_run", return_value=_ctx()) as tracked, \
             patch.object(ocr_module, "_process", AsyncMock()) as process:
            await ocr_module.main(trigger="manual", path="/some/dir", target_status="pending")

        tracked.assert_called_once_with(kind="extract_text_from_memes", trigger="manual")
        process.assert_awaited_once_with("/some/dir", "pending", False)

    def test_registered_in_batch_registry(self):
        assert BatchRegistry().get("extract_text_from_memes") == {
            "module": "batch.extract_text_from_memes", "kind": "extract_text_from_memes"}
