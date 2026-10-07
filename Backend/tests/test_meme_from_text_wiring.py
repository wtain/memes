from unittest.mock import MagicMock

import Backend.app.services.meme_from_text_pipeline as factory
from retrieval.pipeline import MemeFromTextPipeline


def setup_function():
    factory.get_pipeline.cache_clear()


def teardown_function():
    factory.get_pipeline.cache_clear()


def test_no_parser_means_no_pipeline(monkeypatch):
    monkeypatch.setattr(factory, "get_query_parser", lambda: None)
    assert factory.get_pipeline() is None


def test_the_pipeline_is_built_once_from_the_settings(monkeypatch):
    parser = MagicMock()
    parser.model = "m"
    monkeypatch.setattr(factory, "get_query_parser", lambda: parser)

    pipeline = factory.get_pipeline()

    assert isinstance(pipeline, MemeFromTextPipeline)
    assert factory.get_pipeline() is pipeline
