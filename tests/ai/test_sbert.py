import numpy as np

from ai.sbert import SbertModel


class _FakeSentenceTransformer:
    def __init__(self, model_name):
        self.model_name = model_name

    def encode(self, text, normalize_embeddings=False):
        assert normalize_embeddings is True
        return np.array([1.0, 2.0, 3.0])


def test_embed_text_returns_numpy_array(monkeypatch):
    monkeypatch.setattr("ai.sbert.SentenceTransformer", _FakeSentenceTransformer)
    model = SbertModel(model_name="fake-model")

    result = model.embed_text("hello")

    assert isinstance(result, np.ndarray)
    np.testing.assert_array_equal(result, np.array([1.0, 2.0, 3.0]))


def test_default_model_name_is_multilingual_minilm(monkeypatch):
    captured = {}

    class _Capturing(_FakeSentenceTransformer):
        def __init__(self, model_name):
            captured["model_name"] = model_name
            super().__init__(model_name)

    monkeypatch.setattr("ai.sbert.SentenceTransformer", _Capturing)
    SbertModel()

    assert captured["model_name"] == "paraphrase-multilingual-MiniLM-L12-v2"
