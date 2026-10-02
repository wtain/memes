import hashlib
import json

from batch.utils.meme_profile_bundle import (
    PATH_TEXT, PATH_VISION, Bundle, BundleLimits, build_bundle, choose_path, fingerprint, truncate_at_word,
)
from repository.meme_profile_inputs import OcrRow

LIMITS = BundleLimits(note_chars=50, description_chars=50, ocr_chars=50)


def _row(text, confidence=0.9, lang_score=0.9, bbox=None, row_id="a"):
    return OcrRow(text=text, confidence=confidence, lang_score=lang_score, bbox=bbox, row_id=row_id)


def _build(note=None, descriptions=(), ocr_rows=(), **kwargs):
    params = dict(ocr_confidence_min=0.4, ocr_lang_score_min=0.3, limits=LIMITS)
    params.update(kwargs)
    return build_bundle(note, list(descriptions), list(ocr_rows), **params)


class TestTruncateAtWord:
    def test_short_text_is_unchanged(self):
        assert truncate_at_word("hello world", 50) == "hello world"

    def test_cuts_at_a_word_boundary(self):
        assert truncate_at_word("hello wonderful world", 14) == "hello"

    def test_cuts_exactly_when_the_limit_lands_on_a_boundary(self):
        assert truncate_at_word("hello world", 5) == "hello"

    def test_a_single_long_word_is_cut_hard(self):
        assert truncate_at_word("abcdefghij", 4) == "abcd"


class TestBuildBundle:
    def test_note_is_stripped_and_blank_becomes_none(self):
        assert _build(note="  my note  ").note == "my note"
        assert _build(note="   ").note is None
        assert _build(note=None).note is None

    def test_descriptions_are_sorted_by_prompt_key_stripped_and_truncated(self):
        bundle = _build(descriptions=[("b_key", " second "), ("a_key", "first " + "x" * 80), ("c_key", "  ")])
        assert [key for key, _ in bundle.descriptions] == ["a_key", "b_key"]
        assert bundle.descriptions[1][1] == "second"
        assert len(bundle.descriptions[0][1]) <= 50

    def test_ocr_rows_below_the_filters_are_dropped(self):
        rows = [_row("keep", row_id="1"), _row("low confidence", confidence=0.1, row_id="2"),
                _row("wrong language", lang_score=0.1, row_id="3")]
        assert _build(ocr_rows=rows).ocr == "keep"

    def test_ocr_rows_are_joined_in_reading_order_top_to_bottom_then_left_to_right(self):
        rows = [
            _row("bottom", bbox=[[0, 100], [10, 100], [10, 110], [0, 110]], row_id="1"),
            _row("top right", bbox=[[50, 0], [60, 0], [60, 10], [50, 10]], row_id="2"),
            _row("top left", bbox=[[0, 0], [10, 0], [10, 10], [0, 10]], row_id="3"),
        ]
        assert _build(ocr_rows=rows).ocr == "top left top right bottom"

    def test_rows_without_a_usable_bbox_come_after_positioned_rows_in_id_order(self):
        rows = [_row("no box b", bbox=None, row_id="b"), _row("positioned", bbox=[[0, 5], [1, 5], [1, 6], [0, 6]], row_id="z"),
                _row("no box a", bbox="garbage", row_id="a")]
        assert _build(ocr_rows=rows).ocr == "positioned no box a no box b"

    def test_ocr_is_truncated_and_empty_ocr_is_none(self):
        assert len(_build(ocr_rows=[_row("word " * 40)]).ocr) <= 50
        assert _build(ocr_rows=[_row("  ")]).ocr is None
        assert _build(ocr_rows=[]).ocr is None

    def test_none_filter_values_pass_the_filter(self):
        assert _build(ocr_rows=[_row("text", confidence=None, lang_score=None)]).ocr == "text"


class TestBundleHelpers:
    def test_total_chars_sums_every_source(self):
        bundle = Bundle(note="abc", descriptions=(("k", "defg"),), ocr="hi")
        assert bundle.total_chars() == 9
        assert Bundle(None, (), None).total_chars() == 0

    def test_canonical_json_is_stable_and_key_sorted(self):
        bundle = Bundle(note="n", descriptions=(("k", "d"),), ocr="o")
        assert bundle.canonical_json() == '{"descriptions":[["k","d"]],"note":"n","ocr":"o"}'


class TestFingerprint:
    def test_is_the_sha256_of_the_canonical_json_plus_the_content_hash(self):
        bundle = Bundle(note="n", descriptions=(), ocr=None)
        expected = hashlib.sha256((bundle.canonical_json() + "\x1f" + "abc").encode("utf-8")).hexdigest()
        assert fingerprint(bundle, "abc") == expected

    def test_changes_when_any_source_or_the_content_hash_changes(self):
        base = Bundle(note=None, descriptions=(("k", "d"),), ocr="o")
        assert fingerprint(base, "h") == fingerprint(Bundle(None, (("k", "d"),), "o"), "h")
        assert fingerprint(base, "h") != fingerprint(Bundle("n", (("k", "d"),), "o"), "h")
        assert fingerprint(base, "h") != fingerprint(Bundle(None, (("k", "d2"),), "o"), "h")
        assert fingerprint(base, "h") != fingerprint(Bundle(None, (("k", "d"),), "o2"), "h")
        assert fingerprint(base, "h") != fingerprint(base, "other")

    def test_a_missing_content_hash_is_treated_as_empty(self):
        bundle = Bundle(None, (), "o")
        assert fingerprint(bundle, None) == fingerprint(bundle, "")


class TestChoosePath:
    def test_enough_text_takes_the_text_path(self):
        assert choose_path(Bundle(None, (("k", "x" * 40),), None), min_text_chars=40) == PATH_TEXT

    def test_too_little_text_takes_the_vision_path(self):
        assert choose_path(Bundle(None, (), "x" * 39), min_text_chars=40) == PATH_VISION
        assert choose_path(Bundle(None, (), None), min_text_chars=40) == PATH_VISION

    def test_the_total_over_all_sources_counts(self):
        assert choose_path(Bundle("a" * 20, (("k", "b" * 10),), "c" * 10), min_text_chars=40) == PATH_TEXT
