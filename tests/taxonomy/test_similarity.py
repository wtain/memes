from pathlib import Path

import numpy as np
import pytest

from taxonomy.loader import load
from taxonomy.similarity import (apply_overrides, build_artifact, check_artifact, compute_neighbours,
                                 neighbour_stats)

FIXTURE = Path(__file__).parent / "fixtures" / "mini"
KEYS = ["a", "b", "c"]
VECTORS = np.array([[1.0, 0.0], [0.8, 0.6], [0.0, 1.0]], dtype=np.float32)  # a.b=0.8, b.c=0.6, a.c=0.0


def test_compute_neighbours_filters_by_threshold_sorts_and_excludes_self():
    result = compute_neighbours(KEYS, VECTORS, threshold=0.55)
    assert result["a"] == [{"key": "b", "score": 0.8}]
    assert result["b"] == [{"key": "a", "score": 0.8}, {"key": "c", "score": 0.6}]
    assert result["c"] == [{"key": "b", "score": 0.6}]


def test_compute_neighbours_keeps_every_key_even_when_isolated():
    assert compute_neighbours(KEYS, VECTORS, threshold=0.99) == {"a": [], "b": [], "c": []}


def test_apply_overrides_adds_and_removes_symmetrically():
    base = compute_neighbours(KEYS, VECTORS, threshold=0.55)
    result = apply_overrides(base, {"add": [["a", "c", 0.5]], "remove": [["a", "b"]]}, set(KEYS))
    assert result["a"] == [{"key": "c", "score": 0.5}]
    assert result["c"] == [{"key": "b", "score": 0.6}, {"key": "a", "score": 0.5}]
    assert result["b"] == [{"key": "c", "score": 0.6}]


def test_apply_overrides_rejects_unknown_keys():
    with pytest.raises(ValueError, match="zzz"):
        apply_overrides({"a": []}, {"add": [["a", "zzz", 0.5]]}, {"a"})


class _FakeEmbedder:
    def embed(self, texts):
        vectors = np.zeros((len(texts), 2), dtype=np.float32)
        for i, text in enumerate(texts):
            vectors[i] = [1.0, 0.0] if text in {"Plain happiness.", "Confirms what was said."} else [0.0, 1.0]
        return vectors


def test_build_artifact_embeds_definitions_and_applies_taxonomy_overrides():
    tax = load(FIXTURE)
    artifact = build_artifact(tax, _FakeEmbedder(), threshold=0.9, model_name="fake", model_digest="d1")
    assert artifact["taxonomy_version"] == 7
    assert artifact["content_hash"] == tax.content_hash
    assert artifact["overrides_hash"] == tax.overrides_hash != ""
    assert artifact["model_name"] == "fake" and artifact["model_digest"] == "d1"
    assert artifact["threshold"] == 0.9
    # joy and anger are orthogonal under the fake embedder; the fixture's override adds joy<->anger at 0.5
    assert artifact["axes"]["emotion"]["joy"] == [{"key": "anger", "score": 0.5}]
    assert artifact["axes"]["emotion"]["anger"] == [{"key": "joy", "score": 0.5}]
    assert artifact["axes"]["stance"] == {"agree": [], "refuse": []}


def test_check_artifact_accepts_a_fresh_artifact_and_flags_drift():
    tax = load(FIXTURE)
    artifact = build_artifact(tax, _FakeEmbedder(), threshold=0.9, model_name="fake", model_digest="d1")
    assert check_artifact(artifact, tax) == []

    stale = {**artifact, "taxonomy_version": 6}
    assert any("version" in p for p in check_artifact(stale, tax))
    changed = {**artifact, "content_hash": "other"}
    assert any("content_hash" in p for p in check_artifact(changed, tax))
    drifted = {**artifact, "overrides_hash": "other"}
    assert any("overrides_hash" in p for p in check_artifact(drifted, tax))
    unknown = {**artifact, "axes": {**artifact["axes"], "emotion": {"joy": [{"key": "zzz", "score": 0.9}],
                                                                       "anger": []}}}
    assert any("zzz" in p for p in check_artifact(unknown, tax))
    missing = {**artifact, "axes": {**artifact["axes"], "emotion": {"joy": []}}}
    assert any("anger" in p for p in check_artifact(missing, tax))


def test_neighbour_stats_reports_median_max_and_isolated_keys():
    artifact = {"axes": {"emotion": {"a": [{"key": "b", "score": 0.9}], "b": [{"key": "a", "score": 0.9}], "c": []}}}
    stats = neighbour_stats(artifact)
    assert stats["median"] == 1
    assert stats["max"] == 1
    assert stats["isolated"] == ["emotion.c"]
