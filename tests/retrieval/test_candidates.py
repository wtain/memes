from retrieval.candidates import merge_ranked


def test_sources_are_merged_round_robin_by_rank_without_duplicates():
    assert merge_ranked([["a", "b", "c"], ["b", "d"], ["e"]], 10) == ["a", "b", "e", "d", "c"]


def test_the_cap_applies_after_deduplication():
    assert merge_ranked([["a", "b", "c"], ["d", "e"]], 3) == ["a", "d", "b"]


def test_empty_sources_and_zero_cap():
    assert merge_ranked([], 5) == [] and merge_ranked([[], []], 5) == [] and merge_ranked([["a"]], 0) == []
