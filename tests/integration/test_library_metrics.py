"""library_metrics: the library duplicate review's business metrics (M2b). Requires a live PostgreSQL instance."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from Backend.app.repositories.duplicate_decisions_repository import DuplicateDecisionsRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService
from Backend.app.services.library_metrics import (
    open_keeper, record_confirmed, record_dismissed, retract_confirmed, retract_dismissed,
)
from repository.batch_runs import BatchRunRepository
from repository.popularity import PopularityRepository
from Storage.models import BatchRun, Image, ProcessEvent
from tests.integration.test_popularity_repository import make_image


async def _events(session, subject):
    rows = (await session.execute(select(ProcessEvent.event).where(
        ProcessEvent.process == "library_duplicates", ProcessEvent.subject_id == subject))).scalars().all()
    return set(rows)


async def _run_of(session, subject, event):
    return (await session.execute(select(ProcessEvent.batch_run_id).where(
        ProcessEvent.process == "library_duplicates", ProcessEvent.subject_id == subject,
        ProcessEvent.event == event))).scalar_one()


async def _clusterize_run(session, status, year, day):
    # year 2032: the newest runs of this kind in the shared test database
    run = BatchRun(kind="clusterize", trigger="manual", status=status, created_at=datetime(year, 1, day, tzinfo=timezone.utc))
    session.add(run)
    await session.flush()
    return run


@pytest.mark.asyncio(loop_scope="session")
async def test_confirmed_and_dismissed_are_recorded_and_idempotent(db_session):
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    await record_confirmed(db_session, [a.id, b.id, a.id])          # a duplicate id in the list is fine
    await record_confirmed(db_session, [a.id])
    await record_dismissed(db_session, [a.id])

    assert await _events(db_session, a.id) == {"confirmed", "dismissed"}
    assert await _events(db_session, b.id) == {"confirmed"}


@pytest.mark.asyncio(loop_scope="session")
async def test_events_go_to_the_newest_completed_clusterize_run(db_session):
    await _clusterize_run(db_session, "completed", 2032, 1)
    newest = await _clusterize_run(db_session, "completed", 2032, 2)
    await _clusterize_run(db_session, "started", 2032, 3)           # still running: not the clusters on screen
    await _clusterize_run(db_session, "failed", 2032, 4)
    a = await make_image(db_session, "a.jpg")
    await record_confirmed(db_session, [a.id])
    assert await _run_of(db_session, a.id, "confirmed") == newest.run_id


@pytest.mark.asyncio(loop_scope="session")
async def test_events_are_unattributed_without_a_completed_clusterize_run(db_session, monkeypatch):
    monkeypatch.setattr(BatchRunRepository, "get_latest_completed_run", AsyncMock(return_value=None))
    a = await make_image(db_session, "a.jpg")

    await record_dismissed(db_session, [a.id])

    assert await _events(db_session, a.id) == {"dismissed"}
    assert await _run_of(db_session, a.id, "dismissed") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_retract_confirmed_keeps_the_keeper_while_another_open_flag_points_at_it(db_session):
    keeper = await make_image(db_session, "keeper.jpg")
    l1 = await make_image(db_session, "l1.jpg")
    l2 = await make_image(db_session, "l2.jpg")
    pop = PopularityRepository(db_session)
    await pop.absorb(l1.id, keeper.id, "cluster_review")
    await pop.absorb(l2.id, keeper.id, "cluster_review")
    await record_confirmed(db_session, [l1.id, l2.id, keeper.id])

    await pop.reverse_open_edge(l1.id, "unflag")
    await retract_confirmed(db_session, l1.id, keeper.id)
    assert await _events(db_session, l1.id) == set()
    assert await _events(db_session, keeper.id) == {"confirmed"}      # l2 still points at it

    await pop.reverse_open_edge(l2.id, "unflag")
    await retract_confirmed(db_session, l2.id, keeper.id)
    assert await _events(db_session, l2.id) == set()
    assert await _events(db_session, keeper.id) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_retract_confirmed_without_a_keeper_only_touches_the_loser(db_session):
    keeper = await make_image(db_session, "keeper.jpg")
    loser = await make_image(db_session, "loser.jpg")
    await record_confirmed(db_session, [loser.id, keeper.id])

    await retract_confirmed(db_session, loser.id, None)

    assert await _events(db_session, loser.id) == set()
    assert await _events(db_session, keeper.id) == {"confirmed"}


@pytest.mark.asyncio(loop_scope="session")
async def test_open_keeper_returns_the_cluster_review_winner_only(db_session):
    w = await make_image(db_session, "w.jpg")
    page = await make_image(db_session, "page.jpg")
    agent = await make_image(db_session, "agent.jpg")
    free = await make_image(db_session, "free.jpg")
    pop = PopularityRepository(db_session)
    await pop.absorb(page.id, w.id, "cluster_review")
    await pop.absorb(agent.id, w.id, "agent_review")

    assert await open_keeper(db_session, page.id) == w.id
    assert await open_keeper(db_session, agent.id) is None        # an agent flag is not the page's
    assert await open_keeper(db_session, free.id) is None         # no edge at all


@pytest.mark.asyncio(loop_scope="session")
async def test_retract_dismissed_skips_images_that_still_have_a_decision_pair(db_session):
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    c = await make_image(db_session, "c.jpg")
    await DuplicateDecisionsRepository(db_session).record_decisions_bulk([(a.id, b.id), (b.id, c.id)])
    await record_dismissed(db_session, [a.id, b.id, c.id])
    await DuplicateDecisionsRepository(db_session).delete_decisions([(a.id, b.id)])

    await retract_dismissed(db_session, [a.id, b.id])

    assert await _events(db_session, a.id) == set()                   # no pair left
    assert await _events(db_session, b.id) == {"dismissed"}           # still in (b, c)
    assert await _events(db_session, c.id) == {"dismissed"}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failing_write_is_swallowed_and_the_session_stays_usable(db_session, monkeypatch):
    async def boom(self, *a, **k):
        raise RuntimeError("metrics down")
    monkeypatch.setattr(BusinessMetricsService, "record", boom)
    a = await make_image(db_session, "a.jpg")
    await record_confirmed(db_session, [a.id])                         # must not raise
    await record_dismissed(db_session, [a.id])
    assert await _events(db_session, a.id) == set()
    assert (await db_session.execute(select(Image.id).where(Image.id == a.id))).scalar_one() == a.id
