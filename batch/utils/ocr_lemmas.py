from collections import defaultdict

from rules.lang_plausibility import passes_language_filter
from rules.normalize import normalize


def group_lemmas_by_image(rows, morph, confidence_min, lang_score_min, min_word_length):
    """
    rows: iterable of (image_id, text, confidence, language, lang_score).

    Returns (lemmas_by_image, stats):
      - lemmas_by_image: dict[image_id, set[str]] — the union of lemmas
        across every surviving OCR row for that image. This union is what
        makes cross-line phrase matching work: a multi-word query matches
        as soon as each word's lemma is present anywhere in the image's
        set, regardless of which OCR line contributed it.
      - stats: {"rows_total": int, "rows_skipped": int, "rows_processed": int}
    """
    lemmas_by_image = defaultdict(set)
    stats = {"rows_total": 0, "rows_skipped": 0, "rows_processed": 0}

    for image_id, text, confidence, language, lang_score in rows:
        stats["rows_total"] += 1
        if not passes_language_filter(confidence, lang_score, confidence_min, lang_score_min):
            stats["rows_skipped"] += 1
            continue
        # Lemmatized using this row's own detected language (always a confident
        # ru/en/es per EasyOCR — never None/"unknown" in practice, confirmed
        # against real corpus data). A Cyrillic row EasyOCR confidently but
        # wrongly tagged non-ru still only gets lowercased here, while the same
        # word in a search query (repository/ocr_lemmas.py's matching_image_ids)
        # always gets real lemmatization via language=None's script-based
        # fallback. Accepted asymmetry — fixing OCR language misdetection is a
        # separate problem (see docs/superpowers/specs/2026-07-22-smart-search-phase1-hardening-design.md).
        lemmas_by_image[image_id] |= normalize(
            text, morph, min_length=min_word_length, language=language, keep_digit_tokens=True
        )
        stats["rows_processed"] += 1

    return dict(lemmas_by_image), stats
