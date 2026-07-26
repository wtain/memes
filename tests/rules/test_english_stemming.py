from rules.english_stemming import is_latin_word, stem_english_word


class TestStemEnglishWordUnifiesInflections:
    def test_plural_noun(self):
        assert stem_english_word("cats") == stem_english_word("cat")

    def test_verb_conjugation(self):
        assert stem_english_word("running") == stem_english_word("run")

    def test_plural_with_spelling_change(self):
        assert stem_english_word("batteries") == stem_english_word("battery")

    def test_plural_compound_noun(self):
        assert stem_english_word("metalheads") == stem_english_word("metalhead")


class TestStemEnglishWordExactStems:
    """Pins exact stem output, verified against the real snowballstemmer
    package during design, so a future stemmer version bump can't
    silently drift without a test failing."""

    def test_cats(self):
        assert stem_english_word("cats") == "cat"

    def test_running(self):
        assert stem_english_word("running") == "run"

    def test_batteries(self):
        assert stem_english_word("batteries") == "batteri"

    def test_proper_noun_unchanged(self):
        assert stem_english_word("toronto") == "toronto"

    def test_proper_noun_unchanged_2(self):
        assert stem_english_word("hanneman") == "hanneman"


class TestStemEnglishWordKnownLimitation:
    """Documents, rather than hides, the accepted tradeoff of a stemmer
    over a full lemmatizer: irregular forms don't unify."""

    def test_irregular_adjective_does_not_unify(self):
        assert stem_english_word("better") != stem_english_word("good")


class TestIsLatinWord:
    def test_plain_lowercase_true(self):
        assert is_latin_word("cats") is True

    def test_cyrillic_false(self):
        assert is_latin_word("превед") is False

    def test_mixed_script_false(self):
        assert is_latin_word("catд") is False

    def test_hyphenated_false(self):
        # Deliberately narrow scope for this pass -- see the design doc's
        # disclosed limitations and the leftovers backlog's follow-up note.
        assert is_latin_word("well-known") is False

    def test_contraction_false(self):
        assert is_latin_word("don't") is False

    def test_digits_false(self):
        assert is_latin_word("covid19") is False

    def test_uppercase_false(self):
        # matching_image_ids only ever passes already-lowercased lemmas;
        # this pins that is_latin_word does not itself lowercase.
        assert is_latin_word("CATS") is False

    def test_empty_string_false(self):
        assert is_latin_word("") is False
