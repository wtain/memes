from evaluation.state import embedding_digest_warnings


def test_no_warning_when_the_stored_digest_matches():
    assert embedding_digest_warnings({"bge abc": 10}, "bge", "abc") == []


def test_warns_for_each_stored_digest_of_the_model_that_differs():
    warnings = embedding_digest_warnings({"bge old": 4, "bge abc": 6, "other zzz": 9}, "bge", "abc")

    assert len(warnings) == 1 and "4" in warnings[0] and "old" in warnings[0] and "abc" in warnings[0]


def test_other_models_and_empty_state_give_no_warning():
    assert embedding_digest_warnings({"other zzz": 9}, "bge", "abc") == []
    assert embedding_digest_warnings({}, "bge", "abc") == []
