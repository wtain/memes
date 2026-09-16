"""Unit tests for IngestionRepository.get_ocr_texts — mocked session, no DB."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository


def _rows(*tuples):
    """Each tuple: (image_id, text, confidence, lang_score). Mimics session.execute(...).all()."""
    result = SimpleNamespace(all=lambda: list(tuples))
    return result


@pytest.fixture
def repo():
    session = AsyncMock()
    return IngestionRepository(session), session


class TestGetOcrTexts:
    async def test_empty_ids_returns_empty_without_query(self, repo):
        r, session = repo
        assert await r.get_ocr_texts([], 0.4, 0.3) == {}
        session.execute.assert_not_awaited()

    async def test_drops_low_confidence_and_low_lang_score_blocks(self, repo):
        r, session = repo
        img = uuid.uuid4()
        session.execute.return_value = _rows(
            (img, "Не смешно", 0.85, 1.0),        # keep
            (img, "Haka3aha 40 cpepbl", 0.55, 0.2),  # drop: lang_score < 0.3
            (img, "blur", 0.2, 0.9),               # drop: confidence < 0.4
        )
        out = await r.get_ocr_texts([img], 0.4, 0.3)
        assert out == {img: "Не смешно"}

    async def test_orders_by_lang_score_desc_then_confidence_desc(self, repo):
        r, session = repo
        img = uuid.uuid4()
        # Deliberately supplied out of desired order; method must not reorder in Python if the
        # query already ORDER BYs — but the test asserts the *output* order regardless.
        session.execute.return_value = _rows(
            (img, "second", 0.90, 0.40),
            (img, "first", 0.70, 0.95),
            (img, "third", 0.99, 0.35),
        )
        out = await r.get_ocr_texts([img], 0.4, 0.3)
        assert out[img] == "first second third"

    async def test_dedupes_identical_block_text(self, repo):
        r, session = repo
        img = uuid.uuid4()
        session.execute.return_value = _rows(
            (img, "ВЕРНУТЬ МОНАРХИЮ", 0.85, 1.0),
            (img, "ВЕРНУТЬ МОНАРХИЮ", 0.85, 1.0),
            (img, "ага", 0.85, 1.0),
        )
        out = await r.get_ocr_texts([img], 0.4, 0.3)
        assert out[img] == "ВЕРНУТЬ МОНАРХИЮ ага"

    async def test_none_lang_score_is_kept(self, repo):
        r, session = repo
        img = uuid.uuid4()
        session.execute.return_value = _rows((img, "short", 0.85, None))
        out = await r.get_ocr_texts([img], 0.4, 0.3)
        assert out == {img: "short"}


class TestGetTextHeavyIds:
    async def test_empty_ids_returns_empty_without_query(self, repo):
        r, session = repo
        assert await r.get_text_heavy_ids([]) == set()
        session.execute.assert_not_awaited()

    async def test_returns_only_ids_present_in_query_result(self, repo):
        r, session = repo
        img1, img2 = uuid.uuid4(), uuid.uuid4()
        session.execute.return_value = _rows((img1,))
        out = await r.get_text_heavy_ids([img1, img2])
        assert out == {img1}
