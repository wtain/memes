"""list_clusters caps an oversized cluster's members at CLUSTER_MEMBER_CAP while leaving the
cursor / ordering identical to the uncapped run. Requires a live PostgreSQL (see conftest)."""
import uuid

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService, CLUSTER_MEMBER_CAP
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, TmpDuplicates


async def _make_run(session):
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_b_review")


async def _make_image(session, status, batch_id):
    img = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(img); await session.flush(); return img.id


async def _make_pair(session, a, b, dist):
    session.add(TmpDuplicates(image_id1=min(a, b), image_id2=max(a, b), distance=dist, match_source="in_batch"))
    await session.flush()


@pytest.fixture
def no_split(monkeypatch):
    # one blob per component -> the star below stays a single oversized group
    monkeypatch.setattr("Backend.app.services.ingestion_service._split_params", lambda tier: None)


@pytest.mark.asyncio(loop_scope="session")
async def test_oversized_cluster_is_capped_and_reports_total(db_session, no_split, monkeypatch):
    batch_id = await _make_run(db_session)
    hub = await _make_image(db_session, "pending", batch_id)
    spokes = [await _make_image(db_session, "pending", batch_id) for _ in range(CLUSTER_MEMBER_CAP + 20)]
    for i, s in enumerate(spokes):
        await _make_pair(db_session, hub, s, 0.10 + i * 0.0001)  # all in Tier B band, distinct

    # a second, disjoint small cluster -- used to prove ordering/cursor are cap-independent
    x = await _make_image(db_session, "pending", batch_id)
    y = await _make_image(db_session, "pending", batch_id)
    await _make_pair(db_session, x, y, 0.20)

    service = IngestionService(IngestionRepository(db_session))
    page = await service.list_clusters("tier_b", batch_id=batch_id)

    assert len(page["items"]) == 2
    c = next(item for item in page["items"] if item["total_members"] > 2)
    assert c["total_members"] == CLUSTER_MEMBER_CAP + 21
    assert len(c["members"]) == CLUSTER_MEMBER_CAP
    kept = {m["image_id"] for m in c["members"]}
    assert all(e["image_id1"] in kept and e["image_id2"] in kept for e in c["edges"])
    # the hub sits on every edge (tightest) -> always kept
    assert str(hub) in kept

    # ordering and cursor are computed from the uncapped group, so an uncapped run must
    # return the clusters in the same order with the same paging boundary. limit=1 forces a
    # real (non-None) cursor so the comparison is not vacuous.
    capped_p1 = await service.list_clusters("tier_b", batch_id=batch_id, limit=1)
    monkeypatch.setattr("Backend.app.services.ingestion_service.CLUSTER_MEMBER_CAP", 10_000)
    uncapped_p1 = await service.list_clusters("tier_b", batch_id=batch_id, limit=1)

    assert [it["total_members"] for it in capped_p1["items"]] == [it["total_members"] for it in uncapped_p1["items"]]
    assert capped_p1["next_cursor"] is not None
    assert capped_p1["next_cursor"] == uncapped_p1["next_cursor"]


@pytest.mark.asyncio(loop_scope="session")
async def test_small_clusters_untouched(db_session, no_split):
    batch_id = await _make_run(db_session)
    a = await _make_image(db_session, "pending", batch_id)
    b = await _make_image(db_session, "pending", batch_id)
    await _make_pair(db_session, a, b, 0.12)

    service = IngestionService(IngestionRepository(db_session))
    page = await service.list_clusters("tier_b", batch_id=batch_id)

    c = page["items"][0]
    assert c["total_members"] == 2 == len(c["members"])
