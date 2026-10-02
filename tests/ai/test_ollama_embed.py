import numpy as np
import ollama
import pytest

from ai.ollama_embed import OllamaEmbedder


def test_embed_returns_l2_normalised_float32(monkeypatch):
    def fake_embed(model, input):
        assert model == "fake-embed"
        assert input == ["a", "b"]
        return {"embeddings": [[3.0, 4.0], [0.0, 5.0]]}

    monkeypatch.setattr(ollama, "embed", fake_embed)
    result = OllamaEmbedder("fake-embed").embed(["a", "b"])

    assert result.dtype == np.float32
    assert result.shape == (2, 2)
    np.testing.assert_allclose(result, [[0.6, 0.8], [0.0, 1.0]], atol=1e-6)


def test_embed_empty_input_does_not_call_ollama(monkeypatch):
    def boom(**kwargs):
        raise AssertionError("must not be called")

    monkeypatch.setattr(ollama, "embed", boom)
    assert OllamaEmbedder("fake-embed").embed([]).shape == (0, 0)


def test_embed_zero_vector_stays_zero(monkeypatch):
    monkeypatch.setattr(ollama, "embed", lambda model, input: {"embeddings": [[0.0, 0.0]]})
    np.testing.assert_array_equal(OllamaEmbedder("m").embed(["x"]), [[0.0, 0.0]])


def test_model_digest_matches_with_or_without_latest_tag(monkeypatch):
    listing = {"models": [{"model": "other:latest", "digest": "zzz"},
                          {"model": "bge-large:latest", "digest": "abc123"}]}
    monkeypatch.setattr(ollama, "list", lambda: listing)
    assert OllamaEmbedder("bge-large").model_digest() == "abc123"
    assert OllamaEmbedder("bge-large:latest").model_digest() == "abc123"


def test_model_digest_missing_model_names_the_pull_command(monkeypatch):
    monkeypatch.setattr(ollama, "list", lambda: {"models": []})
    with pytest.raises(RuntimeError, match="ollama pull bge-large"):
        OllamaEmbedder("bge-large").model_digest()
