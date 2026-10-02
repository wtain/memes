import numpy as np

from batch.tools import probe_embedding_model as probe


class _FakeEmbedder:
    """Maps every text to a one-hot direction chosen by a lookup, so cosines are exact."""

    def __init__(self, mapping):
        self.mapping = mapping

    def embed(self, texts):
        vectors = np.zeros((len(texts), 4), dtype=np.float32)
        for i, text in enumerate(texts):
            vectors[i, self.mapping.get(text, 3)] = 1.0
        return vectors


def test_evaluate_passes_when_pairs_are_closer_than_unrelated(monkeypatch):
    monkeypatch.setattr(probe, "SIMILAR_PAIRS", [("a", "b")])
    monkeypatch.setattr(probe, "UNRELATED", ["u"])
    rows = probe.evaluate(_FakeEmbedder({"a": 0, "b": 0, "u": 1}))
    assert rows == [("a", 1.0, 0.0, True)]


def test_evaluate_fails_when_unrelated_is_as_close(monkeypatch):
    monkeypatch.setattr(probe, "SIMILAR_PAIRS", [("a", "b")])
    monkeypatch.setattr(probe, "UNRELATED", ["u"])
    rows = probe.evaluate(_FakeEmbedder({"a": 0, "b": 0, "u": 0}))
    assert rows[0][3] is False
