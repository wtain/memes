"""
Unit tests for batch/build_tags_from_descriptions.py's main() self-tracking contract. No
real DB -- mirrors batch/tests/test_build_tags_from_ocr.py's style.
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from batch.build_tags_from_descriptions import main


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_forces_incremental_true_by_default(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_descriptions as module

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="build_tags_from_descriptions", trigger="scheduled")
        process_mock.assert_awaited_once_with(incremental=True)

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_forces_incremental_true_by_default(self):
        process_mock = AsyncMock()
        import batch.build_tags_from_descriptions as module

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "_process", process_mock):
            await main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        process_mock.assert_awaited_once_with(incremental=True)


class _StubEngine:
    """Stands in for ConceptTagger: tags are looked up by exact text."""

    def __init__(self, tags_by_text):
        self.tags_by_text = tags_by_text
        self.calls = []

    def tag(self, text, language=None):
        from rules.concept_tagger import TagResult
        self.calls.append((text, language))
        return TagResult(tags=self.tags_by_text.get(text, []), trace=[])


class _FakeSession:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _RecordingTagsSaver:
    instances = []

    def __init__(self, session):
        self.added = []
        self.events = session.events
        _RecordingTagsSaver.instances.append(self)

    def add_tag(self, image_id, tag_name, value, source):
        self.added.append((image_id, tag_name, value, source))
        self.events.append("add_tag")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _process_patches(module, rows, engine, events):
    _RecordingTagsSaver.instances = []

    tags_repo = AsyncMock()
    tags_repo.delete_tags_for_images.side_effect = lambda *a, **k: events.append("delete_tags_for_images")
    tags_repo.delete_tags.side_effect = lambda *a, **k: events.append("delete_tags")
    images_repo = AsyncMock()
    images_repo.get_total_images.return_value = 0
    images_repo.get_images_and_descriptions_needing_tags.return_value = rows
    images_repo.get_images_and_descriptions.return_value = rows

    stack = ExitStack()
    for p in (
        patch.object(module, "AsyncSessionLocal", return_value=_FakeSession(events)),
        patch.object(module, "TagsRepository", return_value=tags_repo),
        patch.object(module, "ImagesRepository", return_value=images_repo),
        patch.object(module, "TagsSaver", _RecordingTagsSaver),
        patch.object(module.ConceptTagger, "load", return_value=engine),
    ):
        stack.enter_context(p)
    return tags_repo, images_repo, stack


class TestProcess:
    @pytest.mark.asyncio
    async def test_tags_each_description_separately_as_english_and_unions_per_image(self):
        import batch.build_tags_from_descriptions as module

        engine = _StubEngine({
            "a cat on a sofa": [("animal", "cat")],
            "a sofa and a dog": [("animal", "dog")],
            "a plain wall": [],
        })
        rows = [
            ("f1.jpg", "img-1", "a cat on a sofa"),
            ("f1.jpg", "img-1", "a sofa and a dog"),
            ("f2.jpg", "img-2", "a plain wall"),
        ]
        _tags_repo, _images_repo, stack = _process_patches(module, rows, engine, [])

        with stack:
            await module._process(incremental=True)

        assert engine.calls == [
            ("a cat on a sofa", "en"), ("a sofa and a dog", "en"), ("a plain wall", "en"),
        ]
        assert sorted(_RecordingTagsSaver.instances[0].added) == [
            ("img-1", "animal", "cat", "Ollama"),
            ("img-1", "animal", "dog", "Ollama"),
        ]

    @pytest.mark.asyncio
    async def test_incremental_clears_selected_images_tags_before_writing_new_ones(self):
        import batch.build_tags_from_descriptions as module

        engine = _StubEngine({"t": [("k", "v")]})
        rows = [("f1.jpg", "img-1", "t"), ("f2.jpg", "img-2", "t")]
        events = []
        tags_repo, _images_repo, stack = _process_patches(module, rows, engine, events)

        with stack:
            await module._process(incremental=True)

        tags_repo.delete_tags_for_images.assert_awaited_once()
        source, image_ids = tags_repo.delete_tags_for_images.await_args.args
        assert source == "Ollama"
        assert sorted(image_ids) == ["img-1", "img-2"]
        tags_repo.delete_tags.assert_not_awaited()
        assert events.index("delete_tags_for_images") < events.index("add_tag")

    @pytest.mark.asyncio
    async def test_full_mode_deletes_all_ollama_tags_and_reads_every_description(self):
        import batch.build_tags_from_descriptions as module

        engine = _StubEngine({"t": [("k", "v")]})
        rows = [("f1.jpg", "img-1", "t")]
        tags_repo, images_repo, stack = _process_patches(module, rows, engine, [])

        with stack:
            await module._process(incremental=False)

        tags_repo.delete_tags.assert_awaited_once_with("Ollama")
        tags_repo.delete_tags_for_images.assert_not_awaited()
        images_repo.get_images_and_descriptions.assert_awaited_once()
        images_repo.get_images_and_descriptions_needing_tags.assert_not_awaited()
