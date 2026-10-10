"""
Integration test for batch/export_ratings.py against the test database (ratings read through the real repository).
Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest

import batch.export_ratings as module
from evaluation.gold import (Corpus, CorpusImage, ExpectedProfile, GoldQuery, save_corpus, save_judgments,
                             save_queries)
from repository.meme_text_ratings import MemeTextRatingRepository
from retrieval.query_hash import query_hash
from Storage.models import Image

TEXT = "boss moved the deadline"
EXPECTED = ExpectedProfile("s", ("joy",), ("agree",), ("work",), ("wholesome",))


class _SessionFactory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False


async def _image(session) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_process_writes_the_review_files_for_stored_ratings(db_session, tmp_path, monkeypatch):
    first, second = await _image(db_session), await _image(db_session)
    repo = MemeTextRatingRepository(db_session)
    for rank, (image, grade) in enumerate(((first, 2), (second, 1)), start=1):
        await repo.upsert(query_hash=query_hash(TEXT), query_text=TEXT, image_id=image.id, grade=grade, rank=rank,
                          reading="parsed", parser_version="p1", taxonomy_version=1, degraded=False,
                          fallback="none", language="en")
    await db_session.flush()

    gold = tmp_path / "gold"
    gold.mkdir()
    save_corpus(Corpus("general", 7, "2026-10-04", (CorpusImage(str(first.id), "h1", "ocr only"),
                                                      CorpusImage(str(second.id), "h2", "ocr only"))),
                gold / "corpus.json")
    save_queries([GoldQuery("q001", "en", TEXT, "short", "easy", EXPECTED, True)], gold / "queries.json")
    save_judgments([], gold / "judgments.json")
    export = tmp_path / "export"

    monkeypatch.setenv("APP_ENV", "general")
    monkeypatch.setattr(module, "AsyncSessionLocal", _SessionFactory(db_session))
    stats = await module._process(gold=gold, export_dir=export)

    assert stats["ratings"] == 2 and stats["round"] == 1
    assert (export / "summary.json").is_file() and (export / "candidate-queries.json").is_file()
    assert (export / "judgments-en.json").is_file()
