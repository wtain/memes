from rules.lang_plausibility import passes_language_filter, score


def test_score_none_for_text_with_fewer_than_two_alpha_tokens():
    assert score("42", "en") is None
    assert score("lol", "en") is None


def test_score_high_for_genuine_english_sentence():
    result = score("when your friends finally get the joke", "en")
    assert result is not None
    assert result > 0.8


def test_score_high_for_genuine_russian_sentence():
    result = score("когда друзья наконец поняли шутку", "ru")
    assert result is not None
    assert result > 0.8


def test_score_high_for_genuine_spanish_sentence():
    result = score("cuando tus amigos por fin entienden el chiste", "es")
    assert result is not None
    assert result > 0.8


def test_score_low_for_garbled_latin_misread_of_cyrillic():
    # Simulates an `en` EasyOCR reader's best-effort Latin-glyph guess at a
    # Cyrillic-only image: valid Latin script, but not real English words.
    result = score("ctapt 3gect xdbl qwzk", "en")
    assert result is not None
    assert result < 0.3


def test_score_is_case_insensitive():
    assert score("WHEN YOUR FRIENDS FINALLY GET THE JOKE", "en") == score(
        "when your friends finally get the joke", "en"
    )


def test_passes_language_filter_rejects_low_confidence():
    assert passes_language_filter(0.2, 0.9, confidence_min=0.4, lang_score_min=0.3) is False


def test_passes_language_filter_rejects_low_lang_score():
    assert passes_language_filter(0.9, 0.1, confidence_min=0.4, lang_score_min=0.3) is False


def test_passes_language_filter_none_lang_score_passes_through():
    assert passes_language_filter(0.9, None, confidence_min=0.4, lang_score_min=0.3) is True


def test_passes_language_filter_none_confidence_passes_through():
    assert passes_language_filter(None, 0.9, confidence_min=0.4, lang_score_min=0.3) is True


def test_passes_language_filter_both_ok():
    assert passes_language_filter(0.9, 0.9, confidence_min=0.4, lang_score_min=0.3) is True