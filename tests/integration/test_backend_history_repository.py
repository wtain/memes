"""
Integration tests for Backend/app/repositories/history_repository.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.

add() previously read record.id right after construction, before any
flush. SearchHistory.id uses a client-side Python default
(default=uuid.uuid4), which SQLAlchemy only evaluates at flush time - so
record.id was still None, and every SearchHistoryTag got search_id=None,
violating the NOT NULL constraint on any call with a non-empty tags list.
Fixed by building tags through the ORM relationship instead of the raw FK.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from Backend.app.repositories.history_repository import HistoryRepository
from Storage.models import SearchHistory


@pytest.mark.asyncio(loop_scope="session")
async def test_add_with_tags_persists_record_and_tags(db_session):
    repo = HistoryRepository(db_session)
    await repo.add(query="grumpy cat", client="web", result_count=3, tags=[("animal", "cat"), ("mood", "grumpy")])
    await db_session.flush()

    rows = await repo.list(limit=10, cursor_searched_at=None, cursor_id=None, client=None)

    matches = [r for r in rows if r.query == "grumpy cat"]
    assert len(matches) == 1
    tag_pairs = {(t.category, t.value) for t in matches[0].tags}
    assert tag_pairs == {("animal", "cat"), ("mood", "grumpy")}
    assert matches[0].client == "web"
    assert matches[0].result_count == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_add_without_tags(db_session):
    repo = HistoryRepository(db_session)
    await repo.add(query=None, client="android", result_count=0, tags=[])
    await db_session.flush()

    rows = await repo.list(limit=10, cursor_searched_at=None, cursor_id=None, client="android")

    assert len(rows) == 1
    assert rows[0].tags == []


@pytest.mark.asyncio(loop_scope="session")
async def test_list_filters_by_client(db_session):
    repo = HistoryRepository(db_session)
    await repo.add(query="a", client="web", result_count=1, tags=[])
    await repo.add(query="b", client="android", result_count=1, tags=[])
    await db_session.flush()

    rows = await repo.list(limit=10, cursor_searched_at=None, cursor_id=None, client="web")

    assert {r.query for r in rows} == {"a"}


@pytest.mark.asyncio(loop_scope="session")
async def test_list_orders_newest_first(db_session):
    # Postgres now() is transaction start time, so two repo.add() calls in the
    # same test transaction would tie on searched_at — insert explicit,
    # distinct timestamps directly to make the ordering unambiguous.
    marker = str(uuid.uuid4())
    base = datetime.now(timezone.utc)
    db_session.add_all([
        SearchHistory(query=f"{marker}-first", client="web", result_count=1, searched_at=base),
        SearchHistory(query=f"{marker}-second", client="web", result_count=1, searched_at=base + timedelta(seconds=1)),
    ])
    await db_session.flush()

    repo = HistoryRepository(db_session)
    rows = await repo.list(limit=10, cursor_searched_at=None, cursor_id=None, client=None)
    matches = [r for r in rows if marker in (r.query or "")]

    assert [r.query for r in matches] == [f"{marker}-second", f"{marker}-first"]


@pytest.mark.asyncio(loop_scope="session")
async def test_list_cursor_pagination_excludes_current_and_later(db_session):
    repo = HistoryRepository(db_session)
    marker = str(uuid.uuid4())
    for i in range(3):
        await repo.add(query=f"{marker}-{i}", client="web", result_count=1, tags=[])
        await db_session.flush()

    all_rows = await repo.list(limit=10, cursor_searched_at=None, cursor_id=None, client=None)
    all_rows = [r for r in all_rows if marker in (r.query or "")]
    cursor_row = all_rows[0]

    page = await repo.list(
        limit=10, cursor_searched_at=cursor_row.searched_at, cursor_id=cursor_row.id, client=None
    )
    page = [r for r in page if marker in (r.query or "")]

    assert cursor_row.id not in {r.id for r in page}
    assert len(page) == 2
