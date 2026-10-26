"""
extract_text_from_memes.run() in --refresh-stale mode: the wiring around the producer/worker/consumer tasks. The OCR
stages are replaced by fakes (no GPU, no database); what is under test is the run's control flow.
"""
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import batch.extract_text_from_memes as module
from batch.utils.ocr_refresh import MAX_CONSECUTIVE_UNREADABLE


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


def _images(count):
    return [types.SimpleNamespace(filename=f"missing{i}.jpg") for i in range(count)]


@pytest.mark.asyncio
async def test_nothing_to_refresh_does_not_start_the_ocr_models():
    session = MagicMock()
    gpu_consumer = AsyncMock(side_effect=AssertionError("the OCR models must not load for an empty selection"))

    with patch.object(module, "AsyncSessionLocal", MagicMock(return_value=_ctx(session))), \
         patch.object(module, "select_refresh_images", AsyncMock(return_value=[])), \
         patch.object(module, "gpu_consumer", gpu_consumer):
        stats = await module.run("/nowhere", refresh_stale=True)

    assert stats["images_selected"] == 0 and stats["refreshed"] == 0
    gpu_consumer.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stopped_producer_lets_the_other_tasks_finish_before_the_error_surfaces(tmp_path):
    session = MagicMock(rollback=AsyncMock())
    finished = {}
    committer = MagicMock(close=AsyncMock())

    async def fake_cpu_worker(io_queue, cpu_queue, executor, metrics):
        while (item := await io_queue.get()) is not None:
            await cpu_queue.put(item)
        await cpu_queue.put(None)

    async def fake_gpu_consumer(queue, pipeline, metrics, committer_, tracker, refresh=False):
        while await queue.get() is not None:
            pass
        finished["consumer"] = True

    with patch.object(module, "AsyncSessionLocal", MagicMock(return_value=_ctx(session))), \
         patch.object(module, "select_refresh_images", AsyncMock(return_value=_images(MAX_CONSECUTIVE_UNREADABLE + 3))), \
         patch.object(module, "BatchCommitter", MagicMock(return_value=committer)), \
         patch.object(module, "cpu_worker", fake_cpu_worker), \
         patch.object(module, "gpu_consumer", fake_gpu_consumer):
        with pytest.raises(RuntimeError, match="unreadable"):
            await module.run(str(tmp_path), refresh_stale=True)

    assert finished.get("consumer") is True            # the consumer ran to its end instead of being orphaned
    committer.close.assert_awaited_once()              # what was refreshed before the stop is committed, not rolled back
    session.rollback.assert_not_awaited()
