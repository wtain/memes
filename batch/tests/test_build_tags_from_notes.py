"""Unit tests for batch/build_tags_from_notes.py. No real DB -- mocks like
test_build_tags_from_descriptions.py."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_tags_from_notes import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class _StubEngine:
    def __init__(self, tags_by_text):
        self.tags_by_text = tags_by_text
        self.calls = []

    def tag(self, text, language=None):
        from rules.concept_tagger import TagResult
        self.calls.append((text, language))
        return TagResult(tags=self.tags_by_text.get(text, []), trace=[])


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _RecordingTagsSaver:
    instances = []

    def __init__(self, session):
        self.added = []
        _RecordingTagsSaver.instances.append(self)

    def add_tag(self, image_id, tag_name, value, source):
        self.added.append((image_id, tag_name, value, source))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _patches(module, rows, engine):
    _RecordingTagsSaver.instances = []
    tags_repo = AsyncMock()
    images_repo = AsyncMock()
    images_repo.get_total_images.return_value = 0
    images_repo.get_notes_needing_tags.return_value = rows
    images_repo.get_all_notes.return_value = rows

    stack = ExitStack()
    for p in (
        patch.object(module, "AsyncSessionLocal", return_value=_FakeSession()),
        patch.object(module, "TagsRepository", return_value=tags_repo),
        patch.object(module, "ImagesRepository", return_value=images_repo),
        patch.object(module, "TagsSaver", _RecordingTagsSaver),
        patch.object(module.ConceptTagger, "load", return_value=engine),
    ):
        stack.enter_context(p)
    return tags_repo, images_repo, stack


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_defaults_to_incremental(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_notes as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual")

        tracked_run_mock.assert_called_once_with(kind="build_tags_from_notes", trigger="manual")
        process_mock.assert_awaited_once_with(incremental=True)

    @pytest.mark.asyncio
    async def test_finish_existing_run_path(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_notes as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with(incremental=True)


class TestProcess:
    @pytest.mark.asyncio
    async def test_incremental_tags_notes_with_no_language_and_rewrites_selected_images(self):
        import batch.build_tags_from_notes as module

        engine = _StubEngine({"a cat on a sofa": [("animal", "cat")]})
        rows = [("img-1", "a cat on a sofa")]
        tags_repo, _images_repo, stack = _patches(module, rows, engine)

        with stack:
            await module._process(incremental=True)

        assert engine.calls == [("a cat on a sofa", None)]
        tags_repo.delete_tags_for_images.assert_awaited_once()
        source, image_ids = tags_repo.delete_tags_for_images.await_args.args
        assert source == "Note" and set(image_ids) == {"img-1"}
        assert _RecordingTagsSaver.instances[0].added == [("img-1", "animal", "cat", "Note")]

    @pytest.mark.asyncio
    async def test_full_mode_deletes_all_note_tags_and_reads_every_note(self):
        import batch.build_tags_from_notes as module

        engine = _StubEngine({})
        tags_repo, images_repo, stack = _patches(module, [("img-1", "x")], engine)

        with stack:
            await module._process(incremental=False)

        tags_repo.delete_tags.assert_awaited_once_with("Note")
        tags_repo.delete_tags_for_images.assert_not_awaited()
        images_repo.get_all_notes.assert_awaited_once()
        images_repo.get_notes_needing_tags.assert_not_awaited()
