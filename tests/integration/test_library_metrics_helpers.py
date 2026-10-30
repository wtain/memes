from datetime import datetime, timezone

import pytest

from Backend.app.repositories.duplicate_decisions_repository import DuplicateDecisionsRepository
from repository.batch_runs import BatchRunRepository
from repository.popularity import PopularityRepository
from Storage.models import BatchRun, RunStatus
from tests.integration.test_popularity_repository import make_image


def _run(kind, status, day):
    # a year no other test uses, so these rows are the newest of their kind in the shared test database
    return BatchRun(kind=kind, trigger="manual", status=str(status), created_at=datetime(2031, 1, day, tzinfo=timezone.utc))


@pytest.mark.asyncio(loop_scope="session")
async def test_open_winner_is_the_keeper_of_an_open_edge_of_a_listed_source(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    repo = PopularityRepository(db_session)
    await repo.absorb(l.id, w.id, "cluster_review")

    assert await repo.open_winner(l.id, {"cluster_review"}) == w.id
    assert await repo.open_winner(l.id, {"agent_review"}) is None            # another source
    assert await repo.open_winner(w.id, {"cluster_review"}) is None          # no edge as a loser


@pytest.mark.asyncio(loop_scope="session")
async def test_open_winner_is_none_once_the_edge_is_reversed(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    repo = PopularityRepository(db_session)
    await repo.absorb(l.id, w.id, "cluster_review")
    await repo.reverse_open_edge(l.id, "unflag")

    assert await repo.open_winner(l.id, {"cluster_review"}) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_has_open_edge_to_counts_only_open_edges_of_the_listed_sources(db_session):
    w = await make_image(db_session, "w.jpg")
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    c = await make_image(db_session, "c.jpg")
    repo = PopularityRepository(db_session)
    await repo.absorb(a.id, w.id, "cluster_review")
    await repo.absorb(b.id, w.id, "agent_review")

    assert await repo.has_open_edge_to(w.id, {"cluster_review"}) is True
    assert await repo.has_open_edge_to(w.id, {"file_duplicates"}) is False
    await repo.reverse_open_edge(a.id, "unflag")
    assert await repo.has_open_edge_to(w.id, {"cluster_review"}) is False    # reversed
    assert await repo.has_open_edge_to(w.id, {"cluster_review", "agent_review"}) is True
    assert await repo.has_open_edge_to(c.id, {"cluster_review"}) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_images_with_decisions_finds_either_side_of_a_pair(db_session):
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    c = await make_image(db_session, "c.jpg")
    lone = await make_image(db_session, "lone.jpg")
    repo = DuplicateDecisionsRepository(db_session)
    await repo.record_decisions_bulk([(a.id, b.id), (c.id, a.id)])

    assert await repo.images_with_decisions([a.id, b.id, c.id, lone.id]) == {a.id, b.id, c.id}
    assert await repo.images_with_decisions([lone.id]) == set()
    assert await repo.images_with_decisions([]) == set()
    await repo.delete_decisions([(a.id, b.id)])
    assert await repo.images_with_decisions([b.id]) == set()
    assert await repo.images_with_decisions([a.id]) == {a.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_get_latest_completed_run_ignores_started_and_failed_runs_and_other_kinds(db_session):
    older = _run("clusterize", RunStatus.completed, day=1)
    newer = _run("clusterize", RunStatus.completed, day=2)
    started = _run("clusterize", RunStatus.started, day=3)
    failed = _run("clusterize", RunStatus.failed, day=4)
    other = _run("rebuild_duplicates", RunStatus.completed, day=5)
    db_session.add_all([older, newer, started, failed, other])
    await db_session.flush()
    repo = BatchRunRepository(db_session)

    assert (await repo.get_latest_completed_run("clusterize")).run_id == newer.run_id
    assert (await repo.get_latest_completed_run("no_such_kind_xyz")) is None
