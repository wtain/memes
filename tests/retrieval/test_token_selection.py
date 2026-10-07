from repository.ocr_lemmas import select_tokens


def test_no_cap_keeps_every_distinct_token_in_first_occurrence_order():
    assert select_tokens("Pizza tonight, pizza! ok", None) == ["pizza", "tonight", "ok"]
    assert select_tokens("", 3) == [] and select_tokens(None, 3) == []


def test_the_cap_keeps_the_longest_tokens_in_their_original_order():
    assert select_tokens("a bbbb cc dddddd eee", 2) == ["bbbb", "dddddd"]


def test_ties_prefer_the_first_occurrence_and_the_choice_is_deterministic():
    assert select_tokens("aa bb cc dd", 2) == ["aa", "bb"]
    assert select_tokens("aa bb cc dd", 2) == select_tokens("aa bb cc dd", 2)
    assert select_tokens("a bbb cc ddd", 2) == ["bbb", "ddd"]


def test_a_cap_above_the_token_count_changes_nothing():
    assert select_tokens("a bb ccc", 10) == ["a", "bb", "ccc"]
