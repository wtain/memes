from evaluation.pooling import Pool, build_pool


def _pool(ranked, random_candidates=(), **kwargs):
    return build_pool("q001", ranked, list(random_candidates), **kwargs)


def test_ranked_sources_contribute_round_robin_from_rank_one():
    ranked = {"a": ["1", "2", "3", "4", "5"], "b": ["2", "6", "7"], "c": []}

    pool = _pool(ranked, ranked_target=5, random_target=0)

    # pass 1: a->1, b->2 ; pass 2: a->3 (2 already taken), b->6 ; pass 3: a->4
    assert sorted(pool.candidates) == ["1", "2", "3", "4", "6"]
    assert pool.sources["2"] == ("a", "b")
    assert pool.sources["1"] == ("a",)


def test_an_empty_source_is_filled_by_the_others():
    ranked = {"lexical": [], "embedding": [str(i) for i in range(20)], "axis": [str(i) for i in range(100, 120)]}

    pool = _pool(ranked, ranked_target=9, random_target=0)

    assert len(pool.candidates) == 9
    assert all("lexical" not in sources for sources in pool.sources.values())


def test_random_members_are_added_last_and_skip_images_already_taken():
    ranked = {"a": ["1", "2", "3"]}

    pool = _pool(ranked, random_candidates=["2", "x", "y", "z", "w"], ranked_target=3, random_target=3)

    assert len(pool.candidates) == 6
    assert set(pool.candidates) == {"1", "2", "3", "x", "y", "z"}
    assert pool.sources["x"] == ("random",)
    assert "random" not in pool.sources["2"]


def test_excluded_images_are_never_asked_again():
    ranked = {"a": ["1", "2", "3", "4"]}

    pool = _pool(ranked, random_candidates=["1", "r"], exclude={"1", "2"}, ranked_target=2, random_target=1)

    assert "1" not in pool.candidates and "2" not in pool.candidates
    assert set(pool.candidates) == {"3", "4", "r"}


def test_pool_is_smaller_when_candidates_run_out():
    pool = _pool({"a": ["1"]}, random_candidates=["2"], ranked_target=9, random_target=3)
    assert sorted(pool.candidates) == ["1", "2"]


def test_public_order_is_deterministic_per_query_and_seed_and_differs_between_queries():
    ranked = {"a": [str(i) for i in range(30)]}

    first = build_pool("q001", ranked, [], seed=5)
    again = build_pool("q001", ranked, [], seed=5)
    other_query = build_pool("q002", ranked, [], seed=5)
    other_seed = build_pool("q001", ranked, [], seed=6)

    assert first.candidates == again.candidates
    assert first.candidates != other_query.candidates
    assert first.candidates != other_seed.candidates
    assert sorted(first.candidates) == sorted(other_query.candidates)


def test_public_candidates_are_plain_image_ids_without_source_names():
    pool = _pool({"lexical_or": ["a1", "a2"]}, random_candidates=["r1"], ranked_target=2, random_target=1)

    assert isinstance(pool, Pool)
    assert all(isinstance(candidate, str) and "lexical" not in candidate for candidate in pool.candidates)
    assert pool.query_id == "q001"
