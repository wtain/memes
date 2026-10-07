import pytest

from retrieval.similarity import SimilarityIndex, load_similarity


def test_the_shipped_artifact_exposes_the_curated_pairs():
    index = load_similarity()

    assert index.neighbours("emotion", "anger")["annoyance"] == pytest.approx(0.8)
    assert index.similarity("stance", "celebrate", "congratulate") == pytest.approx(0.8)
    assert index.similarity("emotion", "joy", "joy") == 1.0
    assert index.similarity("emotion", "joy", "anger") == 0.0
    assert index.neighbours("emotion", "unknown_key") == {}
    assert index.neighbours("not_an_axis", "joy") == {}


def test_neighbours_for_returns_sorted_pairs_for_the_given_query_axes():
    index = SimilarityIndex({"emotion": {"anger": {"annoyance": 0.8, "disgust": 0.7}}})

    result = index.neighbours_for({"emotion": (("anger", 0.9),), "tone": (("sarcastic", 0.6),)})

    assert result == {"emotion": {"anger": (("annoyance", 0.8), ("disgust", 0.7))}, "tone": {"sarcastic": ()}}


def test_a_missing_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_similarity(tmp_path / "nope.json")


def test_a_custom_file_is_read(tmp_path):
    path = tmp_path / "similarity.json"
    path.write_text('{"axes": {"tone": {"dark": [{"key": "crude", "score": 0.6}]}}}', encoding="utf-8")
    assert load_similarity(path).similarity("tone", "dark", "crude") == pytest.approx(0.6)
