from unittest.mock import MagicMock

import Backend.app.services.meme_from_text_pipeline as factory
from retrieval.pipeline import MemeFromTextPipeline


def setup_function():
    factory._build_error = False
    factory.get_pipeline.cache_clear()


def teardown_function():
    factory._build_error = False
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


def test_the_embedding_client_is_built_with_the_profile_embedding_dimension(monkeypatch):
    parser = MagicMock()
    parser.model = "m"
    monkeypatch.setattr(factory, "get_query_parser", lambda: parser)
    seen = {}
    real = factory.OllamaEmbedClient

    def spy(url, expected_dim=None):
        seen["expected_dim"] = expected_dim
        return real(url, expected_dim)

    monkeypatch.setattr(factory, "OllamaEmbedClient", spy)

    assert factory.get_pipeline() is not None
    assert seen["expected_dim"] == factory.PROFILE_EMBEDDING_DIM == 1024


def test_a_build_failure_is_logged_once_remembered_and_answered_as_no_pipeline(monkeypatch, caplog):
    calls = []

    def broken():
        calls.append(1)
        raise ValueError("secret-config-detail")

    monkeypatch.setattr(factory, "get_query_parser", broken)

    with caplog.at_level("DEBUG"):
        assert factory.get_pipeline() is None
        assert factory.get_pipeline() is None
        assert factory.get_pipeline() is None

    assert len(calls) == 1
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "ValueError" in warnings[0].getMessage()
    assert "secret-config-detail" not in caplog.text
