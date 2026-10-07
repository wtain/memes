import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import Backend.app.main as main


def _patch(monkeypatch, parser_builder):
    calls = []

    def fake_get_query_parser():
        calls.append(1)
        return parser_builder()

    async def fake_start():
        return []

    async def fake_stop(_tasks):
        return None

    monkeypatch.setattr(main, "_configure_logging", lambda: None)      # keeps global logging state untouched
    monkeypatch.setattr(main, "get_query_parser", fake_get_query_parser)
    monkeypatch.setattr(main, "get_pipeline", lambda: None)
    monkeypatch.setattr(main, "start_scheduler", fake_start)
    monkeypatch.setattr(main, "stop_scheduler", fake_stop)
    return calls


@pytest.mark.asyncio
async def test_startup_builds_the_query_parser_once(monkeypatch):
    calls = _patch(monkeypatch, lambda: None)

    async with main.lifespan(main.app):
        assert calls == [1]


@pytest.mark.asyncio
async def test_a_failure_building_the_query_parser_does_not_stop_startup(monkeypatch):
    def boom():
        raise RuntimeError("no taxonomy")

    calls = _patch(monkeypatch, boom)
    entered = False

    async with main.lifespan(main.app):
        entered = True

    assert calls == [1] and entered


@pytest.mark.asyncio
async def test_startup_builds_the_pipeline_and_starts_the_warm_up_task(monkeypatch):
    parser = MagicMock()
    parser.warm_up = AsyncMock()
    pipeline = MagicMock()
    pipeline.warm_up = AsyncMock()
    pipeline_calls = []

    def fake_get_pipeline():
        pipeline_calls.append(1)
        return pipeline

    _patch(monkeypatch, lambda: parser)
    monkeypatch.setattr(main, "get_pipeline", fake_get_pipeline)

    real_sleep = asyncio.sleep

    async def cancel_sleep(_seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(main.asyncio, "sleep", cancel_sleep)   # the loop's own sleep ends the task after one pass

    async with main.lifespan(main.app):
        assert pipeline_calls == [1]
        for _ in range(5):
            await real_sleep(0)

    parser.warm_up.assert_awaited()
    pipeline.warm_up.assert_awaited()


@pytest.mark.asyncio
async def test_a_failure_building_the_pipeline_does_not_stop_startup(monkeypatch):
    _patch(monkeypatch, lambda: None)

    def boom():
        raise RuntimeError("no settings")

    monkeypatch.setattr(main, "get_pipeline", boom)
    entered = False

    async with main.lifespan(main.app):
        entered = True

    assert entered
