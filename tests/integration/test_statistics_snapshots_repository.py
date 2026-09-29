"""
Integration tests for repository/statistics_snapshots.py.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timezone

import pytest

from repository.statistics_snapshots import StatisticsSnapshotsRepository


def _unique_name() -> str:
    return f"test-{uuid.uuid4()}"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_returns_none_when_no_snapshot_exists(db_session):
    repo = StatisticsSnapshotsRepository(db_session)

    assert await repo.get(_unique_name()) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_then_get_round_trips_all_fields(db_session):
    repo = StatisticsSnapshotsRepository(db_session)
    name = _unique_name()
    payload = {"memes": {"total": 5}, "content": {"tags": 9}, "trends": {"runs": 1}}
    computed_at = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)

    await repo.upsert(name, payload, computed_at, duration_ms=1234)
    row = await repo.get(name)

    assert row is not None
    assert row.name == name
    assert row.payload == payload
    assert row.computed_at == computed_at
    assert row.duration_ms == 1234


@pytest.mark.asyncio(loop_scope="session")
async def test_second_upsert_overwrites_the_single_row(db_session):
    repo = StatisticsSnapshotsRepository(db_session)
    name = _unique_name()
    first_at = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
    second_at = datetime(2026, 9, 29, 13, 0, 0, tzinfo=timezone.utc)

    await repo.upsert(name, {"v": 1}, first_at, duration_ms=10)
    await repo.get(name)  # load into the session's identity map: a stale-read regression must show up below
    await repo.upsert(name, {"v": 2}, second_at, duration_ms=20)
    row = await repo.get(name)

    assert row.payload == {"v": 2}
    assert row.computed_at == second_at
    assert row.duration_ms == 20
