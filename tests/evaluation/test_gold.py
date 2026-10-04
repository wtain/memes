import json
from pathlib import Path

import pytest

from evaluation import gold
from evaluation.gold import (
    Corpus, CorpusImage, ExpectedProfile, GoldError, GoldQuery, Judgment, check_queries, file_sha256,
    judgments_by_query, lint_query_set, merge_judgments, parse_corpus, parse_export, parse_judgments,
    parse_queries,
)
from taxonomy import load as load_taxonomy

TAX = load_taxonomy()
UUID_A = "11111111-1111-1111-1111-111111111111"
UUID_B = "22222222-2222-2222-2222-222222222222"


def _expected(**overrides):
    values = dict(situation="someone cancels plans", emotion=("annoyance",), stance=("mock",),
                  topic=("friends",), tone=("sarcastic",))
    values.update(overrides)
    return ExpectedProfile(**values)


def _query(index=1, language="en", length="medium", difficulty="easy", text=None, expected=None, pair_id=None,
           reviewed=True):
    words = {"short": 4, "medium": 12, "long": 25}[length]
    return GoldQuery(id=f"q{index:03d}", language=language, text=text or " ".join(["word"] * words),
                     length=length, difficulty=difficulty, expected=expected or _expected(), reviewed=reviewed,
                     pair_id=pair_id)


class TestCorpus:
    def test_round_trip_preserves_everything(self, tmp_path):
        corpus = Corpus(environment="general", seed=7, created="2026-10-04",
                        images=(CorpusImage(UUID_A, "hash", "ocr only"), CorpusImage(UUID_B, None, "no usable text")))
        path = tmp_path / "corpus.json"

        gold.save_corpus(corpus, path)

        assert gold.load_corpus(path) == corpus
        assert corpus.image_ids() == (UUID_A, UUID_B)
        assert corpus.strata() == {UUID_A: "ocr only", UUID_B: "no usable text"}

    def test_saved_file_is_deterministic_lf_utf8_with_trailing_newline(self, tmp_path):
        corpus = Corpus("general", 7, "2026-10-04", (CorpusImage(UUID_A, "h", "ocr only"),))
        path = tmp_path / "corpus.json"

        gold.save_corpus(corpus, path)
        first = path.read_bytes()
        gold.save_corpus(corpus, path)

        assert path.read_bytes() == first
        assert b"\r" not in first and first.endswith(b"}\n")
        assert list(json.loads(first)) == ["version", "environment", "seed", "created", "images"]

    def test_unknown_stratum_is_rejected(self):
        data = {"version": 1, "environment": "general", "seed": 1, "created": "d",
                "images": [{"imageId": UUID_A, "contentHash": None, "stratum": "weird"}]}
        with pytest.raises(GoldError, match="stratum"):
            parse_corpus(data)

    def test_duplicate_image_ids_are_rejected(self):
        image = {"imageId": UUID_A, "contentHash": None, "stratum": "ocr only"}
        data = {"version": 1, "environment": "general", "seed": 1, "created": "d", "images": [image, image]}
        with pytest.raises(GoldError, match="duplicate"):
            parse_corpus(data)

    def test_wrong_version_is_rejected(self):
        with pytest.raises(GoldError, match="version"):
            parse_corpus({"version": 2, "environment": "general", "seed": 1, "created": "d", "images": []})


class TestQueries:
    def test_round_trip(self, tmp_path):
        queries = [_query(1, pair_id="p1"), _query(2, language="ru", reviewed=False)]
        path = tmp_path / "queries.json"

        gold.save_queries(queries, path)

        assert gold.load_queries(path) == queries

    def test_check_accepts_a_valid_query(self):
        assert check_queries([_query()], TAX) == []

    def test_check_reports_each_structural_problem(self):
        bad = [
            _query(1, text="  "),
            _query(2, expected=_expected(situation="")),
            _query(3, expected=_expected(emotion=("not_a_key",))),
            _query(4, expected=_expected(stance=("mock", "agree", "refuse"))),          # over max_values 2
            GoldQuery("q005", "fr", "hello", "short", "easy", _expected()),
            GoldQuery("q006", "en", "hello", "huge", "easy", _expected()),
            GoldQuery("q007", "en", "hello", "short", "impossible", _expected()),
            _query(1),                                                                 # duplicate id q001
        ]

        problems = " | ".join(check_queries(bad, TAX))

        for fragment in ("q001: text is empty", "q002: situation is empty", "q003: emotion: unknown key",
                         "q004: stance:", "q005: language", "q006: length", "q007: difficulty",
                         "duplicate query id q001"):
            assert fragment in problems

    def test_situation_longer_than_the_taxonomy_limit_is_reported(self):
        long_situation = "x" * (TAX.axes["situation"].max_chars + 1)
        problems = check_queries([_query(expected=_expected(situation=long_situation))], TAX)
        assert any("situation" in p and "longer" in p for p in problems)

    def test_parse_rejects_a_query_without_an_expected_profile(self):
        with pytest.raises(GoldError, match="expectedProfile"):
            parse_queries({"version": 1, "queries": [{"id": "q1", "language": "en", "text": "t",
                                                      "tags": {"length": "short", "difficulty": "easy"}}]})


def _valid_set():
    """A 60-query set that satisfies every drafting rule, built from the real taxonomy."""
    topics = list(TAX.values["topic"])
    stances = list(TAX.values["stance"])
    emotions = list(TAX.values["emotion"])
    tones = list(TAX.values["tone"])
    lengths = ["short"] * 5 + ["medium"] * 10 + ["long"] * 5
    difficulties = ["easy"] * 7 + ["hard"] * 8 + ["ambiguous"] * 5
    queries = []
    counter = 0
    for language in ("en", "ru", "es"):
        for i in range(20):
            counter += 1
            queries.append(_query(
                counter, language=language, length=lengths[i], difficulty=difficulties[i],
                expected=_expected(topic=(topics[counter % len(topics)],), stance=(stances[counter % len(stances)],),
                                   emotion=(emotions[counter % len(emotions)],), tone=(tones[counter % len(tones)],)),
                pair_id=f"p{i}" if i < 6 else None))
    return queries


class TestLintQuerySet:
    def test_a_valid_set_passes(self):
        assert lint_query_set(_valid_set(), TAX) == []

    def test_wrong_language_counts_are_reported(self):
        queries = [q for q in _valid_set() if not (q.language == "es" and q.id == "q041")]
        assert any("es" in p and "20" in p for p in lint_query_set(queries, TAX))

    def test_length_tag_must_match_the_word_count(self):
        queries = _valid_set()
        queries[0] = _query(1, language="en", length="short", text="this one has far too many words to be short")
        assert any("q001" in p and "short" in p for p in lint_query_set(queries, TAX))

    def test_missing_topic_coverage_is_reported(self):
        queries = [q for q in _valid_set()]
        only_work = ExpectedProfile("s", ("annoyance",), ("mock",), ("work",), ("sarcastic",))
        queries = [GoldQuery(q.id, q.language, q.text, q.length, q.difficulty, only_work, True, q.pair_id)
                   for q in queries]
        assert any("topic" in p for p in lint_query_set(queries, TAX))

    def test_too_few_distinct_stances_are_reported(self):
        same = ExpectedProfile("s", tuple(TAX.values["emotion"])[:1], ("mock",), tuple(TAX.values["topic"])[:1],
                               tuple(TAX.values["tone"])[:1])
        queries = [GoldQuery(q.id, q.language, q.text, q.length, q.difficulty, same, True, q.pair_id)
                   for q in _valid_set()]
        problems = " | ".join(lint_query_set(queries, TAX))
        assert "stance" in problems and "emotion" in problems and "tone" in problems

    def test_pair_ids_must_cover_all_three_languages(self):
        queries = [GoldQuery(q.id, q.language, q.text, q.length, q.difficulty, q.expected, True,
                             None if q.language == "es" else q.pair_id) for q in _valid_set()]
        assert any("pair" in p for p in lint_query_set(queries, TAX))


class TestJudgments:
    def test_round_trip_and_grade_validation(self, tmp_path):
        judgments = [Judgment("q001", UUID_A, 2, "ramiz", "2026-10-04", 1)]
        path = tmp_path / "judgments.json"

        gold.save_judgments(judgments, path)

        assert gold.load_judgments(path) == judgments
        with pytest.raises(GoldError, match="grade"):
            parse_judgments({"version": 1, "judgments": [
                {"queryId": "q001", "imageId": UUID_A, "grade": 3, "rater": "r", "date": "d", "round": 1}]})

    def test_merge_adds_new_pairs_and_reports_overwrites_and_unchanged(self):
        existing = [Judgment("q1", "a", 1, "r", "d", 1), Judgment("q1", "b", 2, "r", "d", 1)]
        incoming = [Judgment("q1", "a", 2, "r", "d2", 2),        # overwrite
                    Judgment("q1", "b", 2, "r", "d2", 2),        # same grade: unchanged
                    Judgment("q2", "c", 0, "r", "d2", 2)]        # new

        result = merge_judgments(existing, incoming)

        assert (result.added, result.overwritten, result.unchanged) == (1, 1, 1)
        by_pair = {(j.query_id, j.image_id): j for j in result.merged}
        assert by_pair[("q1", "a")].grade == 2 and by_pair[("q1", "a")].round == 2
        assert set(by_pair) == {("q1", "a"), ("q1", "b"), ("q2", "c")}

    def test_judgments_by_query_groups_grades(self):
        judgments = [Judgment("q1", "a", 2, "r", "d", 1), Judgment("q1", "b", 0, "r", "d", 1),
                     Judgment("q2", "a", 1, "r", "d", 1)]
        assert judgments_by_query(judgments) == {"q1": {"a": 2, "b": 0}, "q2": {"a": 1}}


class TestExportImport:
    def _export(self, **overrides):
        data = {"language": "en", "rater": "ramiz", "date": "2026-10-04", "round": 1,
                "judgments": [{"queryId": "q001", "imageId": UUID_A, "grade": 2}]}
        data.update(overrides)
        return data

    def test_valid_export_becomes_judgments(self):
        result = parse_export(self._export(), valid_query_ids={"q001"}, valid_image_ids={UUID_A})

        assert result == [Judgment("q001", UUID_A, 2, "ramiz", "2026-10-04", 1)]

    def test_unknown_query_unknown_image_bad_grade_and_duplicates_are_refused(self):
        valid = dict(valid_query_ids={"q001"}, valid_image_ids={UUID_A})
        with pytest.raises(GoldError, match="unknown query"):
            parse_export(self._export(judgments=[{"queryId": "q999", "imageId": UUID_A, "grade": 1}]), **valid)
        with pytest.raises(GoldError, match="unknown image"):
            parse_export(self._export(judgments=[{"queryId": "q001", "imageId": UUID_B, "grade": 1}]), **valid)
        with pytest.raises(GoldError, match="grade"):
            parse_export(self._export(judgments=[{"queryId": "q001", "imageId": UUID_A, "grade": 5}]), **valid)
        pair = {"queryId": "q001", "imageId": UUID_A, "grade": 1}
        with pytest.raises(GoldError, match="duplicate"):
            parse_export(self._export(judgments=[pair, pair]), **valid)

    def test_missing_rater_is_refused(self):
        data = self._export()
        del data["rater"]
        with pytest.raises(GoldError, match="rater"):
            parse_export(data, valid_query_ids={"q001"}, valid_image_ids={UUID_A})


def test_file_sha256_ignores_crlf(tmp_path):
    lf = tmp_path / "a.json"
    crlf = tmp_path / "b.json"
    lf.write_bytes(b'{"a": 1}\n')
    crlf.write_bytes(b'{"a": 1}\r\n')

    assert file_sha256(lf) == file_sha256(crlf)
    assert len(file_sha256(lf)) == 64


def test_malformed_entries_raise_gold_error_naming_the_entry():
    entry = {"id": "q001", "text": "t", "expectedProfile": {"situation": "s"}}      # no language
    with pytest.raises(GoldError, match="q001.*language"):
        gold.parse_queries({"version": 1, "queries": [entry]})

    with pytest.raises(GoldError, match="environment"):
        gold.parse_corpus({"version": 1, "seed": 1, "created": "d", "images": []})

    with pytest.raises(GoldError, match="queryId|#0"):
        gold.parse_judgments({"version": 1, "judgments": [{"imageId": "i", "grade": 1, "rater": "r",
                                                           "date": "d", "round": 1}]})
    with pytest.raises(GoldError, match="not an object"):
        gold.parse_export({"rater": "r", "date": "d", "round": 1, "judgments": ["x"]},
                          valid_query_ids=set(), valid_image_ids=set())
