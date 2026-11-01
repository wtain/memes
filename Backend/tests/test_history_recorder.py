from unittest.mock import AsyncMock, MagicMock, patch

from Backend.app.services import history_recorder


def _session_factory(repository_add):
    session = MagicMock()
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value = begin
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=context)


async def test_the_fields_reach_the_repository():
    add = AsyncMock()
    with patch.object(history_recorder, "AsyncSessionLocal", _session_factory(add)), \
         patch.object(history_recorder, "HistoryRepository") as repository_class:
        repository_class.return_value.add = add
        await history_recorder.record_history(query="q", client="web", result_count=2, tags=[("topic", "work")],
                                              kind="meme_text")
    add.assert_awaited_once_with(query="q", client="web", result_count=2, tags=[("topic", "work")], kind="meme_text")


async def test_a_failing_write_is_swallowed():
    with patch.object(history_recorder, "AsyncSessionLocal", side_effect=RuntimeError("db down")):
        await history_recorder.record_history(query="q", client="web", result_count=0, tags=[])


async def test_a_failing_write_logs_the_error_type_but_never_the_fields(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger=history_recorder.logger.name),          patch.object(history_recorder, "AsyncSessionLocal", side_effect=RuntimeError("SECRET-QUERY db down")):
        await history_recorder.record_history(query="SECRET-QUERY", client="web", result_count=0, tags=[])
    assert "history write failed: RuntimeError" in caplog.text
    assert "SECRET-QUERY" not in caplog.text


async def test_the_id_of_the_stored_row_is_returned():
    import uuid
    new_id = uuid.uuid4()
    add = AsyncMock(return_value=new_id)
    with patch.object(history_recorder, "AsyncSessionLocal", _session_factory(add)),          patch.object(history_recorder, "HistoryRepository") as repository_class:
        repository_class.return_value.add = add
        result = await history_recorder.record_history(query="q", client="web", result_count=0, tags=[])
    assert result == new_id


async def test_a_failing_write_returns_none():
    with patch.object(history_recorder, "AsyncSessionLocal", side_effect=RuntimeError("db down")):
        assert await history_recorder.record_history(query="q", client="web", result_count=0, tags=[]) is None
