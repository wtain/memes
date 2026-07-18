"""
Integration test for batch/trends/seed_sources.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import pytest

from batch.trends.seed_sources import MEDUZA_SOURCE, seed_meduza_source
from repository.trends import TrendSourceRepository
from Storage.models import TrendSource


@pytest.mark.asyncio(loop_scope="session")
async def test_seed_meduza_source_adds_row_once(db_session):
    repo = TrendSourceRepository(db_session)

    added_first = await seed_meduza_source(db_session)
    added_second = await seed_meduza_source(db_session)

    assert added_first is True
    assert added_second is False

    sources = await repo.get_all()
    matching = [s for s in sources if s.name == MEDUZA_SOURCE["name"]]
    assert len(matching) == 1
    assert matching[0].connector_type == "api"
    assert matching[0].config["base_url"] == MEDUZA_SOURCE["config"]["base_url"]
    assert matching[0].extraction == {"language": "ru"}


@pytest.mark.asyncio(loop_scope="session")
async def test_seed_meduza_source_updates_stale_extraction_on_existing_row(db_session):
    """A row seeded before `extraction` existed on MEDUZA_SOURCE (extraction=None,
    matching real environments seeded prior to this field being added) must have
    its extraction synced to the current MEDUZA_SOURCE value on the next seed run,
    not silently skipped."""
    repo = TrendSourceRepository(db_session)
    db_session.add(TrendSource(
        name=MEDUZA_SOURCE["name"],
        connector_type=MEDUZA_SOURCE["connector_type"],
        extraction=None,
        config=MEDUZA_SOURCE["config"],
    ))
    await db_session.flush()

    added = await seed_meduza_source(db_session)

    assert added is False
    sources = await repo.get_all()
    matching = [s for s in sources if s.name == MEDUZA_SOURCE["name"]]
    assert len(matching) == 1
    assert matching[0].extraction == {"language": "ru"}
