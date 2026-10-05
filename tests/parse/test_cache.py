from parse.cache import ParseCache
from parse.parser import ParseMeta, ParseResult


def _result(tag="a"):
    return ParseResult({"situation": tag, "emotion": [{"key": "joy"}]}, ParseMeta("m", "v", 1, False, 1, "none"))


def test_miss_then_hit_updates_the_counters():
    cache = ParseCache(2)
    assert cache.get("k") is None
    cache.put("k", _result())

    assert cache.get("k").profile["situation"] == "a"
    assert (cache.hits, cache.misses, len(cache)) == (1, 1, 1)


def test_least_recently_used_entry_is_evicted():
    cache = ParseCache(2)
    cache.put("a", _result("a"))
    cache.put("b", _result("b"))
    cache.get("a")                      # a becomes most recent
    cache.put("c", _result("c"))        # evicts b

    assert cache.get("b") is None
    assert cache.get("a") is not None and cache.get("c") is not None


def test_stored_and_returned_results_are_isolated_from_callers():
    cache = ParseCache(2)
    original = _result()
    cache.put("k", original)
    original.profile["emotion"].clear()

    first = cache.get("k")
    first.profile["emotion"].clear()

    assert cache.get("k").profile["emotion"] == [{"key": "joy"}]


def test_a_non_positive_size_still_holds_one_entry():
    cache = ParseCache(0)
    cache.put("a", _result("a"))
    cache.put("b", _result("b"))
    assert len(cache) == 1 and cache.get("b") is not None
