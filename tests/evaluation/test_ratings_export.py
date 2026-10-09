import uuid

from evaluation.gold import ExpectedProfile, GoldQuery, Judgment
from evaluation.ratings_export import build_export
from repository.meme_text_ratings import RatingRow
from retrieval.query_hash import normalised_query, query_hash

IMG_A, IMG_B, IMG_C = ("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222",
                     "33333333-3333-3333-3333-333333333333")   # fixed: the export sorts by image id


def _query(query_id, text, language="en"):
    return GoldQuery(query_id, language, text, "short", "easy", ExpectedProfile("s", (), (), (), ()), True)


def _h(text):
    return query_hash(normalised_query(text, 600))


def _rating(text, image, grade, *, rank=1, language="en", reading="parsed", degraded=False, text_kept=True, parser="p3-x"):
    return RatingRow(_h(text), text if text_kept else None, uuid.UUID(image), grade, rank, reading, parser, 2, degraded,
                     "none", language, "2026-10-09T10:00:00")


def _build(ratings, queries=(), judgments=(), corpus=(IMG_A, IMG_B, IMG_C)):
    return build_export(ratings, list(queries), list(judgments), set(corpus), max_chars=600, round_number=3,
                        today="2026-10-09")


def test_ratings_of_a_known_gold_query_become_import_ready_judgments_per_language():
    result = _build([_rating("Guess who got the promotion!", IMG_A, 2), _rating("Mi jefe", IMG_B, 1, language="es")],
                    queries=[_query("q001", "Guess who got the promotion!"), _query("q021", "Mi jefe", "es")])

    en = result.judgments_by_language["en"]
    assert (en["rater"], en["date"], en["round"], en["language"]) == ("rating-ui", "2026-10-09", 3, "en")
    assert en["judgments"] == [{"queryId": "q001", "imageId": IMG_A, "grade": 2}]
    assert result.judgments_by_language["es"]["judgments"] == [{"queryId": "q021", "imageId": IMG_B, "grade": 1}]
    assert result.candidates["candidates"] == []


def test_matching_ignores_whitespace_and_case_of_normalisation_only():
    result = _build([_rating("  Guess   who got the promotion! ", IMG_A, 2)], queries=[_query("q001", "Guess who got the promotion!")])
    assert result.judgments_by_language["en"]["judgments"][0]["queryId"] == "q001"
    result = _build([_rating("guess who got the promotion!", IMG_A, 2)], queries=[_query("q001", "Guess who got the promotion!")])
    assert result.judgments_by_language == {} and len(result.candidates["candidates"]) == 1


def test_an_already_judged_pair_is_excluded_and_reported_with_both_grades():
    existing = [Judgment("q001", IMG_A, 1, "ramiz", "2026-10-04", 1)]
    result = _build([_rating("Guess who got the promotion!", IMG_A, 2), _rating("Guess who got the promotion!", IMG_B, 0)],
                    queries=[_query("q001", "Guess who got the promotion!")], judgments=existing)

    assert result.judgments_by_language["en"]["judgments"] == [{"queryId": "q001", "imageId": IMG_B, "grade": 0}]
    assert result.report["alreadyJudged"] == [
        {"queryId": "q001", "imageId": IMG_A, "existingGrade": 1, "ratingGrade": 2, "agree": False}]


def test_an_image_outside_the_frozen_corpus_is_excluded_and_listed():
    outside = str(uuid.uuid4())
    result = _build([_rating("Guess who got the promotion!", outside, 2)], queries=[_query("q001", "Guess who got the promotion!")])
    assert result.judgments_by_language == {}
    assert result.report["outsideCorpus"] == [{"queryId": "q001", "imageId": outside, "grade": 2}]


def test_new_messages_go_to_the_candidates_file_grouped_by_message():
    result = _build([
        _rating("A brand new message", IMG_A, 2, rank=1), _rating("A brand new message", IMG_B, 0, rank=4, reading="edited"),
        _rating("Another one", IMG_C, 1, text_kept=False),
    ])
    candidates = {c["queryHash"]: c for c in result.candidates["candidates"]}
    first = candidates[_h("A brand new message")]
    assert first["text"] == "A brand new message" and first["language"] == "en"
    assert [(r["imageId"], r["grade"], r["rank"], r["reading"]) for r in first["ratings"]] == [
        (IMG_A, 2, 1, "parsed"), (IMG_B, 0, 4, "edited")]
    assert candidates[_h("Another one")]["text"] is None            # the text was forgotten


def test_the_summary_counts_every_rating():
    result = _build([
        _rating("m1", IMG_A, 2), _rating("m1", IMG_B, 0, degraded=True, reading="edited"),
        _rating("m2", IMG_C, 2, language="ru", parser="p3-y"),
    ])
    summary = result.report["summary"]
    assert summary["total"] == 3 and summary["byGrade"] == {"0": 1, "1": 0, "2": 2}
    assert summary["byLanguage"] == {"en": 2, "ru": 1} and summary["byReading"] == {"edited": 1, "parsed": 2}
    assert summary["degraded"] == 1 and summary["byParserVersion"] == {"p3-x": 2, "p3-y": 1}


def test_the_output_is_deterministic_and_sorted():
    ratings = [_rating("Guess who got the promotion!", IMG_B, 1), _rating("Guess who got the promotion!", IMG_A, 2)]
    queries = [_query("q001", "Guess who got the promotion!")]
    first = _build(ratings, queries=queries)
    second = _build(list(reversed(ratings)), queries=queries)
    assert first.judgments_by_language == second.judgments_by_language
    ids = [j["imageId"] for j in first.judgments_by_language["en"]["judgments"]]
    assert ids == sorted(ids)


def test_nothing_to_export_gives_empty_results():
    result = _build([])
    assert result.judgments_by_language == {} and result.candidates["candidates"] == []
    assert result.report["summary"]["total"] == 0
