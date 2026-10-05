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
