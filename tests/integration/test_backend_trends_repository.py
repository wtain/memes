"""
Integration tests for Backend/app/repositories/trends_repository.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from Backend.app.repositories.trends_repository import TrendsRepository
from repository.batch_runs import BatchRunRepository
from Storage.models import TrendSource, BatchRun, TrendsRunResult


async def _make_source(db_session) -> TrendSource:
    source = TrendSource(
        name="test-source",
        connector_type="rss",
        config={"url": "https://example.com", "selector": ".item"},
    )
    db_session.add(source)
    await db_session.flush()
    return source


async def _make_run(db_session, created_at=None) -> BatchRun:
    run = BatchRun(kind="trends", trigger="manual", created_at=created_at) if created_at else BatchRun(kind="trends", trigger="manual")
    db_session.add(run)
    await db_session.flush()
    return run


@pytest.mark.asyncio(loop_scope="session")
async def test_get_available_dates_includes_run_date(db_session):
    run = await _make_run(db_session)
    await BatchRunRepository(db_session).commit(run.run_id)
    run_date = run.created_at.date()

    repo = TrendsRepository(db_session)
    dates = await repo.get_available_dates()

    assert run_date in dates


@pytest.mark.asyncio(loop_scope="session")
async def test_get_available_dates_excludes_non_completed_run(db_session):
    # A run that started but never finished (still running, failed, or aborted) has no
    # usable trend data -- its date must not shadow the actual last successful run's date.
    run = await _make_run(db_session)
    run_date = run.created_at.date()

    repo = TrendsRepository(db_session)
    dates = await repo.get_available_dates()

    assert run_date not in dates


@pytest.mark.asyncio(loop_scope="session")
async def test_get_available_dates_filters_by_label(db_session):
    source = await _make_source(db_session)
    run = await _make_run(db_session)
    await BatchRunRepository(db_session).commit(run.run_id)
    db_session.add(TrendsRunResult(run_id=run.run_id, source_id=source.id, label="tech", name="ai", value=10))
    await db_session.flush()

    repo = TrendsRepository(db_session)
    matching = await repo.get_available_dates(label="tech")
    nonmatching = await repo.get_available_dates(label="nonexistent-label")

    assert run.created_at.date() in matching
    assert nonmatching == []


@pytest.mark.asyncio(loop_scope="session")
async def test_get_runs_for_date_returns_runs_on_that_date(db_session):
    run = await _make_run(db_session)
    run_date = run.created_at.date()

    repo = TrendsRepository(db_session)
    runs = await repo.get_runs_for_date(run_date)
    other_runs = await repo.get_runs_for_date(run_date - timedelta(days=30))

    assert run.run_id in {r.run_id for r in runs}
    assert other_runs == []


@pytest.mark.asyncio(loop_scope="session")
async def test_get_latest_run_for_date_returns_most_recent(db_session):
    base = datetime.now(timezone.utc)
    earlier = await _make_run(db_session, created_at=base)
    # Complete earlier run before creating another to satisfy the unique index constraint
    await BatchRunRepository(db_session).commit(earlier.run_id)
    later = await _make_run(db_session, created_at=base + timedelta(minutes=5))
    await BatchRunRepository(db_session).commit(later.run_id)

    repo = TrendsRepository(db_session)
    latest = await repo.get_latest_run_for_date(base.date())

    assert latest.run_id == later.run_id
    assert latest.run_id != earlier.run_id


@pytest.mark.asyncio(loop_scope="session")
async def test_get_latest_run_for_date_returns_none_when_no_runs(db_session):
    repo = TrendsRepository(db_session)
    result = await repo.get_latest_run_for_date(datetime(2000, 1, 1).date())
    assert result is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_latest_run_for_date_ignores_a_more_recent_non_completed_run(db_session):
    # Reproduces the real-world bug: the most recent run of the day is still running (or
    # failed), but an earlier run that day did complete successfully -- the completed one
    # must win, not the merely-most-recent one.
    base = datetime.now(timezone.utc)
    completed = await _make_run(db_session, created_at=base)
    await BatchRunRepository(db_session).commit(completed.run_id)
    still_running = await _make_run(db_session, created_at=base + timedelta(minutes=5))

    repo = TrendsRepository(db_session)
    latest = await repo.get_latest_run_for_date(base.date())

    assert latest is not None
    assert latest.run_id == completed.run_id
    assert latest.run_id != still_running.run_id


@pytest.mark.asyncio(loop_scope="session")
async def test_get_latest_run_for_date_returns_none_when_only_non_completed_runs(db_session):
    run = await _make_run(db_session)
    run_date = run.created_at.date()

    repo = TrendsRepository(db_session)
    result = await repo.get_latest_run_for_date(run_date)

    assert result is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_entries_for_run_aggregates_and_filters_by_min_value(db_session):
    source = await _make_source(db_session)
    run = await _make_run(db_session)
    db_session.add_all([
        TrendsRunResult(run_id=run.run_id, source_id=source.id, label="tech", name="ai", value=6),
        TrendsRunResult(run_id=run.run_id, source_id=source.id, label="tech", name="ai", value=6),
        TrendsRunResult(run_id=run.run_id, source_id=source.id, label="tech", name="crypto", value=2),
    ])
    await db_session.flush()

    repo = TrendsRepository(db_session)
    rows = await repo.get_entries_for_run(run.run_id, min_value=10)

    entries = {(r.label, r.name): r.value for r in rows}
    assert entries == {("tech", "ai"): 12}  # crypto's sum (2) is below min_value


@pytest.mark.asyncio(loop_scope="session")
async def test_get_entries_for_run_filters_by_label_and_name(db_session):
    source = await _make_source(db_session)
    run = await _make_run(db_session)
    db_session.add_all([
        TrendsRunResult(run_id=run.run_id, source_id=source.id, label="tech", name="ai", value=20),
        TrendsRunResult(run_id=run.run_id, source_id=source.id, label="sports", name="football", value=20),
    ])
    await db_session.flush()

    repo = TrendsRepository(db_session)
    rows = await repo.get_entries_for_run(run.run_id, label="tech", name="ai", min_value=0)

    assert [(r.label, r.name) for r in rows] == [("tech", "ai")]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_history_aggregates_across_run(db_session):
    source = await _make_source(db_session)
    run = await _make_run(db_session)
    db_session.add_all([
        TrendsRunResult(run_id=run.run_id, source_id=source.id, label="tech", name="ai", value=15),
        TrendsRunResult(run_id=run.run_id, source_id=source.id, label="tech", name="ai", value=15),
    ])
    await db_session.flush()

    repo = TrendsRepository(db_session)
    rows = await repo.get_history(min_value=10)

    matches = [r for r in rows if r.run_id == run.run_id]
    assert len(matches) == 1
    assert matches[0].label == "tech"
    assert matches[0].name == "ai"
    assert matches[0].value == 30
    assert matches[0].date == run.created_at.date()


@pytest.mark.asyncio(loop_scope="session")
async def test_trend_source_config_and_extraction_round_trip_as_json(db_session):
    source = TrendSource(
        name="round-trip-source",
        connector_type="api",
        config={"base_url": "https://meduza.io/api/w5/new_search", "num_pages": 5},
        extraction={"labels": ["person", "organization"], "model": "urchade/gliner_multi-v2.1"},
    )
    db_session.add(source)
    await db_session.flush()
    source_id = source.id  # captured before expire() — see note below
    db_session.expire(source)

    # Note: `TrendSource.id == source.id` would re-trigger a lazy load of the
    # just-expired `id` attribute while building the WHERE clause, which is a
    # synchronous attribute access outside any greenlet and raises
    # sqlalchemy.exc.MissingGreenlet under AsyncSession. Using the
    # pre-captured `source_id` avoids that and still forces a real DB
    # round-trip for `config`/`extraction` via the expired identity map entry.
    reloaded = (await db_session.execute(
        select(TrendSource).where(TrendSource.id == source_id)
    )).scalar_one()

    assert reloaded.config == {"base_url": "https://meduza.io/api/w5/new_search", "num_pages": 5}
    assert reloaded.extraction == {"labels": ["person", "organization"], "model": "urchade/gliner_multi-v2.1"}
