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
        assert tokenize("don't") == ["don't"]


class TestTokenizeUnderscoreStillSplits:
    def test_underscore_handle_splits(self):
        assert tokenize("varg_vikernes") == ["varg", "vikernes"]
