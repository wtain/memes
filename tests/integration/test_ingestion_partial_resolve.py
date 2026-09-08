"""
Integration test for the partial-cluster review workflow: a reviewer submits keep/reject for
only *some* members of a candidate cluster and leaves the rest undecided.

Unit tests cover the pieces in isolation (IngestionService.resolve loops decisions
independently; mark_reviewed settles every pair touching an image; get_tier_candidate_rows
drops reviewed/rejected pairs; get_blocked_pending_ids gates promotion). This composes them
end to end against a real schema: resolve(subset) -> list_clusters -> the cluster comes back
reshaped, the untouched member survives in the queue for its other pair, and stays blocked
from promotion.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid
from unittest.mock import patch

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, TmpDuplicates


async def _make_run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(
        kind="ingestion", trigger="manual", stage="tier_a_review"
    )


async def _make_image(session, status: str, batch_id) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    return image.id


async def _make_pair(session, id1, id2, distance: float) -> None:
    session.add(TmpDuplicates(
        image_id1=min(id1, id2), image_id2=max(id1, id2),
        distance=distance, match_source="in_batch",
    ))
    await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_partial_cluster_resolve_reshapes_queue_and_still_blocks_untouched_member(db_session):
    batch_id = await _make_run(db_session)
    # Candidate chain a-b-c-d, all pending in this batch, all inside the Tier A band (< 0.05).
    a = await _make_image(db_session, "pending", batch_id)
    b = await _make_image(db_session, "pending", batch_id)
    c = await _make_image(db_session, "pending", batch_id)
    d = await _make_image(db_session, "pending", batch_id)
    await _make_pair(db_session, a, b, distance=0.02)
    await _make_pair(db_session, b, c, distance=0.02)
    await _make_pair(db_session, c, d, distance=0.02)

    service = IngestionService(IngestionRepository(db_session))

    # Reviewer decides only a (reject) and b (keep); c and d are left untouched.
    with patch("Backend.app.services.ingestion_service.image_store.move_to_rejected"):
        result = await service.resolve("tier_a", [
            {"image_id": a, "decision": "reject"},
            {"image_id": b, "decision": "keep"},
        ])

    assert result["rejected"] == [str(a)]
    assert result["kept"] == [str(b)]
    assert result["failed"] == []
    assert result["move_failed"] == []

    # a is rejected; c and d are untouched -- still pending.
    assert (await db_session.get(Image, a)).status == "rejected"
    assert (await db_session.get(Image, c)).status == "pending"
    assert (await db_session.get(Image, d)).status == "pending"

    # The queue is reshaped: a-b is gone (a rejected), b-c is gone (keeping b settled *every*
    # pair touching b, including the one to the undecided c), only the untouched c-d pair
    # survives -> exactly one cluster, {c, d}.
    page = await service.list_clusters("tier_a", batch_id=batch_id)
    assert page["has_next"] is False
    assert len(page["items"]) == 1
    member_ids = {m["image_id"] for m in page["items"][0]["members"]}
    assert member_ids == {str(c), str(d)}

    # c still has an unresolved pair -> it (and d) stay blocked from promotion; a is rejected
    # so it's out, b's pairs are all reviewed so b is clear.
    blocked = await service.repo.get_blocked_pending_ids(batch_id, tier_a_high=0.05, tier_b_high=0.3)
    assert c in blocked
    assert d in blocked
    assert b not in blocked


@pytest.mark.asyncio(loop_scope="session")
async def test_partial_resolve_that_clears_every_pair_removes_the_member_from_the_queue(db_session):
    batch_id = await _make_run(db_session)
    # a-b-c chain: deciding a and b settles *all* of c's pairs (c only touches b).
    a = await _make_image(db_session, "pending", batch_id)
    b = await _make_image(db_session, "pending", batch_id)
    c = await _make_image(db_session, "pending", batch_id)
    await _make_pair(db_session, a, b, distance=0.02)
    await _make_pair(db_session, b, c, distance=0.02)

    service = IngestionService(IngestionRepository(db_session))
    with patch("Backend.app.services.ingestion_service.image_store.move_to_rejected"):
        await service.resolve("tier_a", [
            {"image_id": a, "decision": "reject"},
            {"image_id": b, "decision": "keep"},
        ])

    # c was never decided, but every pair it sat on is now settled -> it drops out of the
    # queue entirely and is no longer blocked (promotable as a non-duplicate).
    page = await service.list_clusters("tier_a", batch_id=batch_id)
    assert page["items"] == []
    assert (await db_session.get(Image, c)).status == "pending"
    blocked = await service.repo.get_blocked_pending_ids(batch_id, tier_a_high=0.05, tier_b_high=0.3)
    assert c not in blocked
