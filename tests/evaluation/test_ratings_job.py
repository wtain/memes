import json
import uuid

import pytest

from evaluation import ratings_job
from evaluation.gold import (Corpus, CorpusImage, ExpectedProfile, GoldError, GoldQuery, Judgment, load_judgments,
                             save_corpus, save_judgments, save_queries)
from repository.meme_text_ratings import RatingRow
from retrieval.query_hash import normalised_query, query_hash

IMG = "11111111-1111-1111-1111-111111111111"
IMG2 = "22222222-2222-2222-2222-222222222222"
EXPECTED = ExpectedProfile("s", ("joy",), ("agree",), ("work",), ("wholesome",))
TEXT = "boss moved the deadline"


def _gold(tmp_path, judgments=()):
    gold = tmp_path / "gold"
    gold.mkdir()
    save_corpus(Corpus("general", 7, "2026-10-04", (CorpusImage(IMG, "h", "ocr only"), CorpusImage(IMG2, "h2", "ocr only"))),
                gold / "corpus.json")
    save_queries([GoldQuery("q001", "en", TEXT, "short", "easy", EXPECTED, True)], gold / "queries.json")
    save_judgments(list(judgments), gold / "judgments.json")
    return gold


def _export_file(path, *judgments, round_number=3):
    path.write_text(json.dumps({"rater": "rating-ui", "date": "2026-10-11", "round": round_number,
                                "judgments": [{"queryId": q, "imageId": i, "grade": g} for q, i, g in judgments]}),
                    encoding="utf-8")
    return path


def test_check_environment_matches_the_old_behaviour():
    assert ratings_job.check_environment(None, "general") is None
    assert ratings_job.check_environment("general", "general") is None
    assert "general" in ratings_job.check_environment("metal", "general")


def test_next_round_is_one_after_the_highest_round():
    assert ratings_job.next_round([]) == 1
    assert ratings_job.next_round([Judgment("q001", IMG, 2, "a", "d", 2), Judgment("q001", IMG2, 1, "a", "d", 5)]) == 6


def test_prepare_output_dir_refuses_a_populated_directory_without_force(tmp_path):
    (tmp_path / "judgments-en.json").write_text("{}")
    assert "already contains" in ratings_job.prepare_output_dir(tmp_path, force=False)
    assert ratings_job.prepare_output_dir(tmp_path, force=True) is None
    assert not (tmp_path / "judgments-en.json").exists()


def test_import_adds_new_judgments_and_reports_counts(tmp_path):
    gold = _gold(tmp_path)
    file = _export_file(tmp_path / "judgments-en.json", ("q001", IMG, 2))
    stats = ratings_job.import_judgment_files([file], gold, allow_overwrite=False)
    assert stats == {"added": 1, "overwritten": 0, "unchanged": 0, "total": 1}
    assert [(j.query_id, j.image_id, j.grade) for j in load_judgments(gold / "judgments.json")] == [("q001", IMG, 2)]
    assert not (gold / "judgments.json.tmp").exists()


def test_import_is_idempotent(tmp_path):
    gold = _gold(tmp_path)
    file = _export_file(tmp_path / "judgments-en.json", ("q001", IMG, 2))
    ratings_job.import_judgment_files([file], gold, allow_overwrite=False)
    again = ratings_job.import_judgment_files([file], gold, allow_overwrite=False)
    assert again == {"added": 0, "overwritten": 0, "unchanged": 1, "total": 1}


def test_import_refuses_to_overwrite_unless_allowed_and_leaves_the_file_untouched(tmp_path):
    gold = _gold(tmp_path, [Judgment("q001", IMG, 1, "owner", "2026-10-01", 1)])
    before = (gold / "judgments.json").read_bytes()
    file = _export_file(tmp_path / "judgments-en.json", ("q001", IMG, 2))
    with pytest.raises(GoldError, match="overwrite"):
        ratings_job.import_judgment_files([file], gold, allow_overwrite=False)
    assert (gold / "judgments.json").read_bytes() == before
    stats = ratings_job.import_judgment_files([file], gold, allow_overwrite=True)
    assert stats["overwritten"] == 1
    assert load_judgments(gold / "judgments.json")[0].grade == 2


def test_import_rejects_an_unknown_image(tmp_path):
    gold = _gold(tmp_path)
    file = _export_file(tmp_path / "judgments-en.json", ("q001", "99999999-9999-9999-9999-999999999999", 2))
    with pytest.raises(GoldError):
        ratings_job.import_judgment_files([file], gold, allow_overwrite=False)


@pytest.mark.asyncio
async def test_export_ratings_writes_files_and_reports_counts(tmp_path, monkeypatch):
    gold = _gold(tmp_path)
    row = RatingRow(query_hash(normalised_query(TEXT, 600)), TEXT, uuid.UUID(IMG), 2, 1, "parsed", "p3-x", 2, False,
                    "none", "en", "2026-10-09T10:00:00")

    class FakeRepository:
        def __init__(self, session):
            pass

        async def all_ratings(self):
            return [row]

    monkeypatch.setattr(ratings_job, "MemeTextRatingRepository", FakeRepository)
    out = tmp_path / "out"
    stats = await ratings_job.export_ratings(object(), gold, out, round_number=4, max_chars=600, today="2026-10-11")
    assert set(stats) == {"ratings", "importable", "files", "already_judged", "outside_corpus", "new_messages", "round"}
    assert stats["round"] == 4 and stats["importable"] == 1 and stats["files"] == 1
    assert (out / "judgments-en.json").exists() and (out / "summary.json").exists()
    assert (out / "candidate-queries.json").exists()
