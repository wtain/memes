from batch.trocr_fallback import TrOCRFallback


def test_plausible_english_passes():
    assert TrOCRFallback._is_plausibly_english("hello world") is True


def test_garbled_cyrillic_misread_is_rejected():
    # Real EasyOCR "en" reader output on Cyrillic-only memes -- the "en"
    # reader forced Cyrillic strokes into Latin lookalike glyphs, none of
    # which are real English words. TROCR_MIN_LANG_SCORE=0.6 (stricter than
    # settings.OCR.LANG_SCORE_MIN) catches these; the 0.3 default would not
    # have (their scores are 0.0-0.5, all below 0.6 but some above 0.3).
    assert TrOCRFallback._is_plausibly_english("EcTb OTJHYHBIi CIOCO6 y3HaTB") is False
    assert TrOCRFallback._is_plausibly_english("3eMJII epex APY3BAMH:") is False
    assert TrOCRFallback._is_plausibly_english("AMOXET HA TEBa, CVKAPH") is False


def test_gate_is_not_perfect_on_short_mixed_strings():
    # Documented limitation, not a regression: lang_plausibility.score() can
    # coincidentally match multiple short common English words (here "Ana",
    # "MeHa", "OHa", "Tak") inside an otherwise-garbled Cyrillic-misread
    # string, pushing the ratio (0.8) above even the stricter 0.6 threshold.
    # The gate is a plausibility signal, not a perfect language ID -- see
    # docs/superpowers/specs/2026-07-02-ocr-language-plausibility-filtering.md.
    assert TrOCRFallback._is_plausibly_english("Ana MeHa OHa BbIrnAMMT Tak:") is True


def test_short_text_passes_through_as_unscored():
    # Fewer than 2 alphabetic tokens -> lang_plausibility.score() returns None,
    # which this gate treats as "can't judge, allow it" rather than "reject".
    assert TrOCRFallback._is_plausibly_english("lol") is True


def test_empty_text_passes_through_as_unscored():
    assert TrOCRFallback._is_plausibly_english("") is True
