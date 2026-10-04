"""
Unit tests for batch/build_meme_profiles.py's main() contract and clean exits. No real DB --
mirrors batch/tests/test_build_image_descriptions_main.py.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import batch.build_meme_profiles as module
from batch.registry import BatchRegistry
from batch.utils.meme_profile_config import NOT_CONFIGURED_MESSAGE


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_passes_options_and_records_stats(self):
        process_mock = AsyncMock(return_value={"images_selected": 2, "images_remaining": 5})
        record_mock = AsyncMock()

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual", limit=7, retry_failed=True)

        tracked_run_mock.assert_called_once_with(kind="build_meme_profiles", trigger="manual")
        process_mock.assert_awaited_once_with(reset=False, limit=7, retry_failed=True, only_ids=None)
        record_mock.assert_awaited_once_with("run-1", {"images_selected": 2, "images_remaining": 5})

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_records_stats_on_the_given_run(self):
        process_mock = AsyncMock(return_value={"images_selected": 0, "images_remaining": 0})
        record_mock = AsyncMock()

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "record_stats", record_mock):
            await module.main(trigger="manual", run_id="existing-run-1", reset=True)

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with(reset=True, limit=None, retry_failed=False, only_ids=None)
        record_mock.assert_awaited_once_with("existing-run-1", {"images_selected": 0, "images_remaining": 0})

    @pytest.mark.asyncio
    async def test_only_ids_is_passed_through_to_process(self):
        process_mock = AsyncMock(return_value={"images_selected": 0, "images_remaining": 0})
        ids = frozenset({"id-1"})

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")), \
             patch.object(module, "_process", process_mock), \
             patch.object(module, "record_stats", AsyncMock()):
            await module.main(trigger="manual", only_ids=ids)

        process_mock.assert_awaited_once_with(reset=False, limit=None, retry_failed=False, only_ids=ids)


@pytest.mark.asyncio
async def test_process_exits_cleanly_when_the_environment_is_not_configured(capsys):
    session_factory = MagicMock(side_effect=AssertionError("must not open a session when not configured"))

    with patch.object(module, "load_config", return_value=None), \
         patch.object(module, "AsyncSessionLocal", session_factory):
        stats = await module._process(reset=False, limit=None, retry_failed=False)

    assert stats == {"not_configured": 1, "images_selected": 0, "images_remaining": 0}
    assert NOT_CONFIGURED_MESSAGE in capsys.readouterr().out
    session_factory.assert_not_called()


def test_registered_in_batch_registry():
    entry = BatchRegistry().get("build_meme_profiles")

    assert entry == {"module": "batch.build_meme_profiles", "kind": "build_meme_profiles"}


def test_is_run_level_error_classifies_backend_unavailability():
    import httpx
    import ollama

    assert module.is_run_level_error(httpx.ConnectError("refused"))
    assert module.is_run_level_error(ConnectionRefusedError("refused"))
    assert module.is_run_level_error(ollama.ResponseError("model not found", 404))
    assert not module.is_run_level_error(ValueError("bad"))
    assert not module.is_run_level_error(ollama.ResponseError("Failed to load image", 400))
