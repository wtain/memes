from retrieval.cursor import decode_cursor, encode_cursor, fingerprint, paginate
from retrieval.facets import RankedItem
from retrieval.model import ScoredCandidate
from tests.retrieval.helpers import make_candidate


def _items(*pairs):
    return [RankedItem(ScoredCandidate(make_candidate(i), s, ()), None) for i, s in pairs]


def test_fingerprint_is_stable_and_order_independent():
    assert fingerprint({"a": 1, "b": [2, 3]}) == fingerprint({"b": [2, 3], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def test_cursor_round_trip_and_fingerprint_check():
    cursor = encode_cursor(0.912345678, "img-1", "fp1")

    assert decode_cursor(cursor, "fp1") == (0.912346, "img-1")
    assert decode_cursor(cursor, "other") is None
    assert decode_cursor(None, "fp1") is None
    assert decode_cursor("not a cursor", "fp1") is None


def test_pages_continue_after_the_last_returned_item():
    items = _items(("a", 0.9), ("b", 0.8), ("c", 0.8), ("d", 0.5), ("e", 0.1))

    page1, next1, more1 = paginate(items, None, "fp", 2)
    page2, next2, more2 = paginate(items, next1, "fp", 2)
    page3, next3, more3 = paginate(items, next2, "fp", 2)

    assert [i.scored.candidate.image_id for i in page1] == ["a", "b"] and more1 is True
    assert [i.scored.candidate.image_id for i in page2] == ["c", "d"] and more2 is True
    assert [i.scored.candidate.image_id for i in page3] == ["e"] and more3 is False and next3 is None


def test_a_cursor_for_another_request_restarts_at_the_first_page():
    items = _items(("a", 0.9), ("b", 0.8))
    _page, cursor, _more = paginate(items, None, "fp", 1)

    page, _next, _more = paginate(items, cursor, "different", 1)

    assert [i.scored.candidate.image_id for i in page] == ["a"]


def test_an_empty_list_has_no_next_page():
    assert paginate([], None, "fp", 5) == ([], None, False)
