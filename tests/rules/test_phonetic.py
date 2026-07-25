from rules.phonetic import is_cyrillic_word, russian_metaphone


class TestRussianMetaphoneErrativesCollapseToCanonicalForm:
    def test_preved_matches_privet(self):
        assert russian_metaphone("превед") == russian_metaphone("привет")

    def test_afftar_matches_avtor(self):
        assert russian_metaphone("аффтар") == russian_metaphone("автор")

    def test_krosavcheg_matches_krasavchik(self):
        assert russian_metaphone("кросавчег") == russian_metaphone("красавчик")

    def test_zhzhot_matches_zhzhet(self):
        assert russian_metaphone("жжот") == russian_metaphone("жжёт")


class TestRussianMetaphoneKnownFalsePositives:
    """These pairs are genuinely distinct dictionary words that still
    collapse to the same phonetic code -- documented, expected behavior of
    the algorithm itself. Callers gate on pymorphy3's is_known flag
    (repository/ocr_lemmas.py::_is_known_word) to avoid surfacing these as
    search results. This suite pins the exact codes so a future change to
    the algorithm can't silently drift without a test failing."""

    def test_kot_kod_collide(self):
        assert russian_metaphone("кот") == russian_metaphone("код") == "КАТ"

    def test_dom_dym_collide(self):
        assert russian_metaphone("дом") == russian_metaphone("дым") == "ДАМ"

    def test_stol_stal_collide(self):
        assert russian_metaphone("стол") == russian_metaphone("стал") == "СТАЛ"

    def test_parta_porta_collide(self):
        assert russian_metaphone("парта") == russian_metaphone("порта") == "ПАРТА"


class TestRussianMetaphoneExactCodes:
    """Pins exact output for words exercising each pipeline stage
    (devoicing, j-insertion, repeated-letter collapse), verified against
    fonetika's reference RussianMetaphone().transform() output during
    design."""

    def test_devoicing_word_final_and_before_vowel(self):
        assert russian_metaphone("любовь") == "ЛУБАФ"

    def test_j_insertion_after_vowel(self):
        assert russian_metaphone("объявление") == "АПJАВЛИНИJИ"

    def test_repeated_letter_collapse(self):
        assert russian_metaphone("жжот") == "ЖАТ"


class TestIsCyrillicWord:
    def test_pure_cyrillic_lowercase_true(self):
        assert is_cyrillic_word("превед") is True

    def test_latin_word_false(self):
        assert is_cyrillic_word("hello") is False

    def test_mixed_script_false(self):
        assert is_cyrillic_word("превedт") is False

    def test_uppercase_false(self):
        # matching_image_ids only ever passes already-lowercased lemmas
        # (see rules/normalize.py::lemmatize_word); this pins that
        # is_cyrillic_word does not itself lowercase.
        assert is_cyrillic_word("ПРЕВЕД") is False

    def test_empty_string_false(self):
        assert is_cyrillic_word("") is False
