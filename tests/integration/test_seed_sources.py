"""
Integration test for batch/trends/seed_sources.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import pytest

from batch.trends.seed_sources import MEDUZA_SOURCE, seed_meduza_source
from repository.trends import TrendSourceRepository


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
