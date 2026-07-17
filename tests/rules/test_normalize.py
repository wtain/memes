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

from rules.normalize import lemmatize_word, make_morph, normalize


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
        assert result == "running"
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


class TestNormalizeLanguageGating:
    def test_language_none_reproduces_default_behavior(self):
        morph = make_morph()
        assert normalize("работе сегодня", morph) == {"работа", "сегодня"}

    def test_language_en_lowercases_without_pymorphy3(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = normalize("RUNNING FAST", wrapped, language="en")
        assert result == {"running", "fast"}
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
