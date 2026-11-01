"""
Integration tests for HistoryRepository (search_history kind column, delete by query hash).
Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import pytest
from sqlalchemy import func, select

from Backend.app.repositories.history_repository import HistoryRepository
from Storage.models import SearchHistoryTag

pytestmark = pytest.mark.asyncio(loop_scope="session")

HASH = "a" * 64
OTHER = "b" * 64


async def _add(repository, **overrides):
    values = {"query": "q", "client": "web", "result_count": 1, "tags": []}
    values.update(overrides)
    await repository.add(**values)


async def _list(repository, **overrides):
    values = {"limit": 50, "cursor_searched_at": None, "cursor_id": None, "client": None}
    values.update(overrides)
    return await repository.list(**values)


async def test_a_plain_search_row_defaults_to_kind_search(db_session):
    repository = HistoryRepository(db_session)
    await _add(repository, query="cats")
    await db_session.flush()
    rows = await _list(repository)
    assert [(row.query, row.kind) for row in rows] == [("cats", "search")]
    assert rows[0].query_hash is None and rows[0].degraded is None


async def test_a_meme_text_row_keeps_its_parse_and_outcome(db_session):
    repository = HistoryRepository(db_session)
    await _add(repository, query="boss moved the deadline", kind="meme_text", language="en", query_hash=HASH,
               situation="a deadline moved", degraded=False, fallback=None, parse_ms=700, retrieval_ms=120,
               result_count=20, tags=[("emotion", "stress"), ("topic", "work")])
    await db_session.flush()
    rows = await _list(repository, kind="meme_text")
    assert len(rows) == 1
    row = rows[0]
    assert (row.kind, row.language, row.query_hash, row.situation) == ("meme_text", "en", HASH, "a deadline moved")
    assert (row.degraded, row.fallback, row.parse_ms, row.retrieval_ms, row.result_count) == (False, None, 700, 120, 20)
    assert sorted((t.category, t.value) for t in row.tags) == [("emotion", "stress"), ("topic", "work")]


async def test_list_filters_by_kind_and_defaults_to_search(db_session):
    repository = HistoryRepository(db_session)
    await _add(repository, query="search one")
    await _add(repository, query="message one", kind="meme_text", query_hash=HASH)
    await db_session.flush()
    assert [r.query for r in await _list(repository)] == ["search one"]
    assert [r.query for r in await _list(repository, kind="meme_text")] == ["message one"]
    assert sorted(r.query for r in await _list(repository, kind="all")) == ["message one", "search one"]


async def test_delete_by_query_hash_removes_only_matching_meme_text_rows(db_session):
    repository = HistoryRepository(db_session)
    await _add(repository, query="mine", kind="meme_text", query_hash=HASH, tags=[("topic", "work")])
    await _add(repository, query="mine again", kind="meme_text", query_hash=HASH)
    await _add(repository, query="someone else", kind="meme_text", query_hash=OTHER)
    await _add(repository, query="search with same hash column", kind="search", query_hash=HASH)
    await db_session.flush()
    assert await repository.delete_by_query_hash(HASH) == 2
    await db_session.flush()
    remaining = sorted(r.query for r in await _list(repository, kind="all"))
    assert remaining == ["search with same hash column", "someone else"]
    assert (await db_session.execute(select(func.count()).select_from(SearchHistoryTag))).scalar_one() == 0


async def test_add_returns_the_id_of_the_stored_row(db_session):
    repository = HistoryRepository(db_session)
    new_id = await repository.add(query="cats", client="web", result_count=1, tags=[])
    await db_session.flush()
    assert [row.id for row in await _list(repository)] == [new_id]
