from rules.normalize import tokenize


class TestTokenizeHyphenPreservation:
    def test_hyphenated_compound_stays_joined(self):
        assert tokenize("Санкт-Петербурга") == ["Санкт-Петербурга"]

    def test_double_hyphen_splits(self):
        assert tokenize("Санкт--Петербурга") == ["Санкт", "Петербурга"]

    def test_leading_hyphen_not_consumed(self):
        assert tokenize("-leading") == ["leading"]

    def test_trailing_hyphen_not_consumed(self):
        assert tokenize("trailing-") == ["trailing"]

    def test_digit_containing_compound_joins_too(self):
        assert tokenize("covid-19") == ["covid-19"]


class TestTokenizeApostrophePreservation:
    def test_contraction_stays_joined(self):
        assert tokenize("don't") == ["don't"]

    def test_name_with_apostrophe_stays_joined(self):
        assert tokenize("O'Brien") == ["O'Brien"]

    def test_trailing_quote_not_joined(self):
        assert tokenize("YOLO'") == ["YOLO"]

    def test_leading_quote_not_joined(self):
        assert tokenize("'sup") == ["sup"]

    def test_closing_quote_role_resolved_correctly(self):
        assert tokenize("it's a 'quote' test") == ["it's", "a", "quote", "test"]


class TestTokenizeJoinerNormalization:
    def test_em_dash_normalized_to_hyphen(self):
        assert tokenize("well—known") == ["well-known"]

    def test_en_dash_normalized_to_hyphen(self):
        assert tokenize("well–known") == ["well-known"]

    def test_curly_apostrophe_normalized(self):
        assert tokenize("don’t") == ["don't"]


class TestTokenizeUnderscoreStillSplits:
    def test_underscore_handle_splits(self):
        assert tokenize("varg_vikernes") == ["varg", "vikernes"]


from unittest.mock import Mock

from rules.normalize import lemmatize_word, lemmatize_word_autodetect, make_morph, normalize


class TestLemmatizeWordLanguageGating:
    def test_language_none_lemmatizes_russian_word_as_before(self):
        morph = make_morph()
        assert lemmatize_word("работе", morph) == "работа"

    def test_language_none_lowercases_latin_word_as_before(self):
        morph = make_morph()
        assert lemmatize_word("RUNNING", morph) == "running"

    def test_language_ru_lemmatizes_normally(self):
        morph = make_morph()
        assert lemmatize_word("Путина", morph, language="ru") == "путин"

    def test_language_en_skips_pymorphy3_entirely(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = lemmatize_word("RUNNING", wrapped, language="en")
        assert result == "run"
        wrapped.parse.assert_not_called()

    def test_language_es_skips_pymorphy3_entirely(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = lemmatize_word("CANCIÓN", wrapped, language="es")
        assert result == "canción"
        wrapped.parse.assert_not_called()

    def test_language_unknown_skips_pymorphy3_entirely(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = lemmatize_word("MYSTERY", wrapped, language="unknown")
        assert result == "mystery"
        wrapped.parse.assert_not_called()


class TestLemmatizeWordStemmable:
    def test_language_en_stems_instead_of_lowercasing(self):
        morph = make_morph()
        assert lemmatize_word("cats", morph, language="en") == "cat"

    def test_language_en_does_not_call_pymorphy3(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = lemmatize_word("cats", wrapped, language="en")
        assert result == "cat"
        wrapped.parse.assert_not_called()


class TestLemmatizeWordUnknownWordsStayAsTyped:
    """pymorphy3 runs its own unknown-word-guessing heuristics even for
    words it doesn't recognize, and that guess can invent letters that
    were never in the original word (e.g. "превед" -> "преведа"). For
    genuinely unrecognized words -- typos, internet-slang erratives,
    foreign fragments -- the word as typed is a more stable, predictable
    lemma than an unreliable guess with no real dictionary entry backing
    it. See docs/superpowers/specs/2026-07-25-smart-search-phonetic-erratives-design.md."""

    def test_preved_stays_unchanged(self):
        morph = make_morph()
        assert lemmatize_word("превед", morph) == "превед"

    def test_afftar_stays_unchanged(self):
        morph = make_morph()
        assert lemmatize_word("аффтар", morph) == "аффтар"

    def test_known_word_still_lemmatizes_normally(self):
        """Regression guard: the fix must only change is_known=False
        behavior -- known words still get their real normal_form."""
        morph = make_morph()
        assert lemmatize_word("работе", morph) == "работа"


class TestLemmatizeWordAutodetect:
    def test_cyrillic_word_gets_real_lemmatization(self):
        morph = make_morph()
        assert lemmatize_word_autodetect("кошки", morph) == "кошка"

    def test_latin_word_gets_stemmed(self):
        morph = make_morph()
        assert lemmatize_word_autodetect("cats", morph) == "cat"

    def test_digit_only_word_falls_through_to_plain_lowercase(self):
        morph = make_morph()
        assert lemmatize_word_autodetect("2020", morph) == "2020"

    def test_mixed_script_word_falls_through_to_plain_lowercase(self):
        morph = make_morph()
        assert lemmatize_word_autodetect("METALLICAкринж", morph) == "metallicaкринж"

    def test_uppercase_latin_word_is_detected_and_stemmed(self):
        # is_cyrillic_word/is_latin_word require already-lowercased input;
        # this pins that lemmatize_word_autodetect lowercases before checking.
        morph = make_morph()
        assert lemmatize_word_autodetect("CATS", morph) == "cat"


class TestNormalizeLanguageGating:
    def test_language_none_reproduces_default_behavior(self):
        morph = make_morph()
        assert normalize("работе сегодня", morph) == {"работа", "сегодня"}

    def test_language_en_lowercases_without_pymorphy3(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = normalize("RUNNING FAST", wrapped, language="en")
        assert result == {"run", "fast"}
        wrapped.parse.assert_not_called()


from rules.normalize import lemmatize_phrase


class TestLemmatizePhrase:
    def test_single_word(self):
        morph = make_morph()
        assert lemmatize_phrase("Путина", morph) == "путин"

    def test_multi_word_phrase_normalizes_each_word(self):
        morph = make_morph()
        assert lemmatize_phrase("Владимира Путина", morph) == "владимир путин"

    def test_hyphenated_compound_stays_joined_and_lemmatizes_as_one(self):
        morph = make_morph()
        assert lemmatize_phrase("Санкт-Петербурга", morph) == "санкт-петербург"

    def test_already_nominative_input_is_idempotent(self):
        morph = make_morph()
        assert lemmatize_phrase("Владимир Путин", morph) == "владимир путин"

    def test_preserves_word_order(self):
        morph = make_morph()
        result = lemmatize_phrase("Владимира Путина", morph)
        assert result.split() == ["владимир", "путин"]


class TestNormalizeKeepDigitTokens:
    def test_digit_token_dropped_by_default(self):
        morph = make_morph()
        assert normalize("year 2020 report", morph) == {"year", "report"}

    def test_digit_token_kept_when_requested(self):
        morph = make_morph()
        assert normalize("year 2020 report", morph, keep_digit_tokens=True) == {"year", "2020", "report"}

    def test_short_digit_token_still_dropped_when_kept(self):
        morph = make_morph()
        assert normalize("a 12 report", morph, min_length=3, keep_digit_tokens=True) == {"report"}

    def test_kept_digit_token_is_not_lemmatized(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = normalize("2020", wrapped, keep_digit_tokens=True)
        assert result == {"2020"}
        wrapped.parse.assert_not_called()
