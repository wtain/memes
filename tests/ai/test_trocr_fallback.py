from batch.trocr_fallback import TrOCRFallback


def test_plausible_english_passes():
    assert TrOCRFallback._is_plausibly_english("hello world") is True


def test_garbled_cyrillic_misread_is_rejected():
    # Real EasyOCR "en" reader output on a Cyrillic-only meme -- the "en"
    # reader forced Cyrillic strokes into Latin lookalike glyphs, none of
    # which are real English words.
    assert TrOCRFallback._is_plausibly_english("EcTb OTJHYHBIi CIOCO6 y3HaTB") is False
    assert TrOCRFallback._is_plausibly_english("3eMJII epex APY3BAMH:") is False


def test_gate_is_not_perfect_on_short_mixed_strings():
    # Documented limitation, not a regression: lang_plausibility.score() can
    # coincidentally match short common English words (here "HA" and "TEBa")
    # inside an otherwise-garbled string, pushing the ratio above threshold.
    # The gate is a plausibility signal, not a perfect language ID -- see
    # docs/superpowers/specs/2026-07-02-ocr-language-plausibility-filtering.md.
    assert TrOCRFallback._is_plausibly_english("AMOXET HA TEBa, CVKAPH") is True


def test_short_text_passes_through_as_unscored():
    # Fewer than 2 alphabetic tokens -> lang_plausibility.score() returns None,
    # which this gate treats as "can't judge, allow it" rather than "reject".
    assert TrOCRFallback._is_plausibly_english("lol") is True


def test_empty_text_passes_through_as_unscored():
    assert TrOCRFallback._is_plausibly_english("") is True
