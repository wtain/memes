"""The library /duplicates review records its decisions as business metrics (M2b), through the real ImageService.
Requires a live PostgreSQL instance."""
import random
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from Backend.app.repositories.duplicate_decisions_repository import DuplicateDecisionsRepository
from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService
from Backend.app.services.image_service import ImageService
from Storage.models import BatchRun, ImageExtras, ProcessEvent, RunStatus, TmpImageClusters
from tests.integration.test_popularity_repository import make_image


def _service(session):
    return ImageService(ImageRepository(session), DuplicateDecisionsRepository(session), MagicMock())


async def _events(session, subject):
    rows = (await session.execute(select(ProcessEvent.event).where(
        ProcessEvent.process == "library_duplicates", ProcessEvent.subject_id == subject))).scalars().all()
    return set(rows)


async def _cluster(session, *images):
    cluster_id = random.randint(10**8, 10**9)
    for image in images:
        session.add(TmpImageClusters(cluster_id=cluster_id, image_id=image.id))
    await session.flush()
    return cluster_id


@pytest.mark.asyncio(loop_scope="session")
async def test_a_page_flag_confirms_the_loser_and_the_keeper(db_session):
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    await _service(db_session).mark_flagged(str(l.id), "duplicate_review", str(w.id))
    assert await _events(db_session, l.id) == {"confirmed"}
    assert await _events(db_session, w.id) == {"confirmed"}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_flag_without_a_keeper_confirms_only_the_loser(db_session):
    l = await make_image(db_session, "l.jpg")
    await _service(db_session).mark_flagged(str(l.id), "duplicate_review")
    assert await _events(db_session, l.id) == {"confirmed"}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("reason", ["agent_review", None, "other"])
async def test_other_flags_record_nothing(db_session, reason):
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    service = _service(db_session)

    await service.mark_flagged(str(l.id), reason)
    if reason == "agent_review":
        await service.mark_flagged(str(l.id), reason, str(w.id))      # an agent flag with a keeper

    assert await _events(db_session, l.id) == set()
    assert await _events(db_session, w.id) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_flag_with_a_bad_duplicate_of_raises_before_anything_is_recorded(db_session):
    l = await make_image(db_session, "l.jpg")
    for bad in ("not-a-uuid", str(l.id)):
        with pytest.raises(HTTPException) as exc:
            await _service(db_session).mark_flagged(str(l.id), "duplicate_review", bad)
        assert exc.value.status_code == 422
    assert await _events(db_session, l.id) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_unflag_retracts_the_loser_and_the_keeper_once_nothing_points_at_it(db_session):
    w = await make_image(db_session, "w.jpg")
    l1 = await make_image(db_session, "l1.jpg")
    l2 = await make_image(db_session, "l2.jpg")
    service = _service(db_session)
    await service.mark_flagged(str(l1.id), "duplicate_review", str(w.id))
    await service.mark_flagged(str(l2.id), "duplicate_review", str(w.id))

    await service.unmark_flagged(str(l1.id))
    assert await _events(db_session, l1.id) == set()
    assert await _events(db_session, w.id) == {"confirmed"}          # l2's flag still points at it
    await service.unmark_flagged(str(l2.id))
    assert await _events(db_session, l2.id) == set()
    assert await _events(db_session, w.id) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_flag_unflag_flag_again_confirms_again(db_session):
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    service = _service(db_session)
    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))
    await service.unmark_flagged(str(l.id))
    assert await _events(db_session, l.id) == set()

    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))

    assert await _events(db_session, l.id) == {"confirmed"}
    assert await _events(db_session, w.id) == {"confirmed"}


@pytest.mark.asyncio(loop_scope="session")
async def test_unflag_of_an_agent_flagged_image_touches_nothing(db_session):
    w2 = await make_image(db_session, "w2.jpg")
    l2 = await make_image(db_session, "l2.jpg")
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    service = _service(db_session)
    await service.mark_flagged(str(l2.id), "duplicate_review", str(w2.id))    # the page's own flag
    await service.mark_flagged(str(l.id), "agent_review", str(w.id))          # an agent flag

    await service.unmark_flagged(str(l.id))

    assert await _events(db_session, l2.id) == {"confirmed"}
    assert await _events(db_session, w2.id) == {"confirmed"}
    assert await _events(db_session, l.id) == set()
    assert await _events(db_session, w.id) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_dismissal_records_every_member_and_an_undo_retracts_only_the_images_without_a_pair_left(db_session):
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    c = await make_image(db_session, "c.jpg")
    cid = await _cluster(db_session, a, b, c)
    service = _service(db_session)
    pairs = await service.dismiss_cluster(cid)
    for image in (a, b, c):
        assert await _events(db_session, image.id) == {"dismissed"}

    await service.undo_dismiss([pairs[0]])                           # one pair of the three goes
    for image in (a, b, c):
        assert await _events(db_session, image.id) == {"dismissed"}  # each is still in another pair

    await service.undo_dismiss(pairs[1:])
    for image in (a, b, c):
        assert await _events(db_session, image.id) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_dismissing_a_subset_records_only_that_subset(db_session):
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    c = await make_image(db_session, "c.jpg")
    cid = await _cluster(db_session, a, b, c)

    await _service(db_session).dismiss_cluster(cid, [a.id, b.id])

    assert await _events(db_session, a.id) == {"dismissed"}
    assert await _events(db_session, b.id) == {"dismissed"}
    assert await _events(db_session, c.id) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_flag_then_a_dismissal_of_one_image_leaves_both_events(db_session):
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    other = await make_image(db_session, "other.jpg")
    cid = await _cluster(db_session, l, other)
    service = _service(db_session)
    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))

    await service.dismiss_cluster(cid)

    assert await _events(db_session, l.id) == {"confirmed", "dismissed"}


@pytest.mark.asyncio(loop_scope="session")
async def test_the_events_are_attributed_to_the_newest_completed_clusterize_run(db_session):
    run = BatchRun(kind="clusterize", trigger="manual", status=str(RunStatus.completed),
                   created_at=datetime(2033, 6, 1, tzinfo=timezone.utc))
    db_session.add(run)
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    await db_session.flush()

    await _service(db_session).mark_flagged(str(l.id), "duplicate_review", str(w.id))

    rows = (await db_session.execute(select(ProcessEvent.batch_run_id).where(
        ProcessEvent.process == "library_duplicates", ProcessEvent.subject_id == l.id))).scalars().all()
    assert rows == [run.run_id]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_metrics_failure_leaves_the_flag_and_the_dismissal_applied(db_session, monkeypatch):
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    other = await make_image(db_session, "other.jpg")
    cid = await _cluster(db_session, l, other)

    async def boom(self, *args, **kwargs):
        raise RuntimeError("metrics down")
    monkeypatch.setattr(BusinessMetricsService, "record", boom)
    service = _service(db_session)

    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))
    pairs = await service.dismiss_cluster(cid)

    flagged = (await db_session.execute(select(ImageExtras.flagged).where(ImageExtras.image_id == l.id))).scalar_one()
    assert flagged is True
    assert len(pairs) == 1 and await DuplicateDecisionsRepository(db_session).images_with_decisions([l.id]) == {l.id}
    assert await _events(db_session, l.id) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_keeper_without_an_image_row_is_not_confirmed(db_session):
    l = await make_image(db_session, "l.jpg")
    ghost = uuid.uuid4()
    await _service(db_session).mark_flagged(str(l.id), "duplicate_review", str(ghost))
    assert await _events(db_session, l.id) == {"confirmed"}
    assert await _events(db_session, ghost) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_reflag_to_another_keeper_confirms_only_the_keeper_of_the_edge(db_session):
    k1 = await make_image(db_session, "k1.jpg")
    k2 = await make_image(db_session, "k2.jpg")
    l = await make_image(db_session, "l.jpg")
    service = _service(db_session)
    await service.mark_flagged(str(l.id), "duplicate_review", str(k1.id))
    await service.mark_flagged(str(l.id), "duplicate_review", str(k2.id))
    assert await _events(db_session, k1.id) == {"confirmed"}
    assert await _events(db_session, k2.id) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_unflagging_a_keeper_that_is_itself_flagged_keeps_its_own_confirmation(db_session):
    k = await make_image(db_session, "k.jpg")
    k2 = await make_image(db_session, "k2.jpg")
    l1 = await make_image(db_session, "l1.jpg")
    service = _service(db_session)
    await service.mark_flagged(str(l1.id), "duplicate_review", str(k.id))
    await service.mark_flagged(str(k.id), "duplicate_review", str(k2.id))

    await service.unmark_flagged(str(k.id))

    assert await _events(db_session, k.id) == {"confirmed"}      # l1's flag still points at it
    assert await _events(db_session, k2.id) == set()
