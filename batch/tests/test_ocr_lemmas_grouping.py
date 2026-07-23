from rules.normalize import make_morph
from batch.utils.ocr_lemmas import group_lemmas_by_image

_MORPH = make_morph()


def test_unions_lemmas_across_multiple_rows_for_same_image():
    rows = [
        ("img-1", "звоню в", 0.9, "ru", 1.0),
        ("img-1", "полицию", 0.9, "ru", 1.0),
    ]
    lemmas_by_image, all_image_ids, _ = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "полиция" in lemmas_by_image["img-1"]
    assert "звонить" in lemmas_by_image["img-1"]
    assert all_image_ids == {"img-1"}


def test_separate_images_kept_separate():
    rows = [
        ("img-1", "cat picture", 0.9, "en", 1.0),
        ("img-2", "dog picture", 0.9, "en", 1.0),
    ]
    lemmas_by_image, all_image_ids, _ = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "cat" in lemmas_by_image["img-1"]
    assert "dog" not in lemmas_by_image["img-1"]
    assert "dog" in lemmas_by_image["img-2"]
    assert all_image_ids == {"img-1", "img-2"}


def test_low_confidence_row_skipped():
    rows = [("img-1", "cat picture", 0.1, "en", 1.0)]
    lemmas_by_image, all_image_ids, stats = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "img-1" not in lemmas_by_image
    assert stats == {"rows_total": 1, "rows_skipped": 1, "rows_processed": 0}
    assert all_image_ids == {"img-1"}


def test_low_lang_score_row_skipped():
    rows = [("img-1", "cat picture", 0.9, "en", 0.0)]
    lemmas_by_image, all_image_ids, stats = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "img-1" not in lemmas_by_image
    assert stats["rows_skipped"] == 1
    assert all_image_ids == {"img-1"}


def test_digit_tokens_kept_as_lemmas():
    rows = [("img-1", "made in 2020", 0.9, "en", 1.0)]
    lemmas_by_image, all_image_ids, _ = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "2020" in lemmas_by_image["img-1"]
    assert all_image_ids == {"img-1"}


def test_stats_counts_total_and_processed():
    rows = [
        ("img-1", "cat", 0.9, "en", 1.0),
        ("img-1", "dog", 0.1, "en", 1.0),
    ]
    _, _, stats = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert stats == {"rows_total": 2, "rows_skipped": 1, "rows_processed": 1}


def test_no_rows_returns_empty():
    lemmas_by_image, all_image_ids, stats = group_lemmas_by_image([], _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert lemmas_by_image == {}
    assert all_image_ids == set()
    assert stats == {"rows_total": 0, "rows_skipped": 0, "rows_processed": 0}


def test_image_with_all_rows_filtered_out_still_appears_in_all_image_ids():
    """Regression test: an image whose every OCR row fails the
    confidence/lang-score filter must still be tracked as seen, so the
    caller can mark it done and stop reprocessing it forever."""
    rows = [
        ("img-1", "cat picture", 0.1, "en", 1.0),
        ("img-1", "more text", 0.1, "en", 1.0),
    ]
    lemmas_by_image, all_image_ids, stats = group_lemmas_by_image(rows, _MORPH, confidence_min=0.4, lang_score_min=0.3, min_word_length=3)
    assert "img-1" not in lemmas_by_image
    assert all_image_ids == {"img-1"}
    assert stats == {"rows_total": 2, "rows_skipped": 2, "rows_processed": 0}
