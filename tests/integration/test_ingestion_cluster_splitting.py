"""
Integration tests for IngestionService.list_clusters cluster splitting against the real
schema. Splitting is presentational -- these prove the queue is reshaped without changing
decision semantics or promote-blocking.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService
from config.settings import settings
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, TmpDuplicates


async def _make_run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(
        kind="ingestion", trigger="manual", stage="tier_a_review"
    )


async def _make_image(session, status, batch_id) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    return image.id


async def _make_pair(session, id1, id2, distance) -> None:
    session.add(TmpDuplicates(
        image_id1=min(id1, id2), image_id2=max(id1, id2), distance=distance, match_source="in_batch",
    ))
    await session.flush()


async def _chain(session, batch_id, n, loose_at, tight=0.02, loose=0.045):
    """n pending images in a chain; links are `tight` except indices in `loose_at` (0-based
    edge index) which are `loose` -> one union-find blob, splits at the loose links.
    Defaults are a Tier A band; for a Tier B chain pass tight=0.08 and derive `loose` from
    the live settings.DUPLICATES.THRESHOLD (e.g. settings.DUPLICATES.THRESHOLD - 0.01) rather
    than hardcoding a value -- the Tier B query band is [0.05, settings.DUPLICATES.THRESHOLD)."""
    ids = [await _make_image(session, "pending", batch_id) for _ in range(n)]
    for i in range(n - 1):
        await _make_pair(session, ids[i], ids[i + 1], loose if i in loose_at else tight)
    return ids


@pytest.fixture
def force_split(monkeypatch):
    """Force a small max_size so a modest fixture splits, without touching real config."""
    monkeypatch.setattr(
        "Backend.app.services.ingestion_service._split_params",
        lambda tier: {"start": 0.05, "decrement": 0.01, "floor": 0.01, "max_size": 3},
    )


@pytest.fixture
def force_split_tier_b(monkeypatch):
    """Tier B ladder with a small max_size so a modest Tier B chain splits."""
    monkeypatch.setattr(
        "Backend.app.services.ingestion_service._split_params",
        lambda tier: {"start": 0.30, "decrement": 0.05, "floor": 0.05, "max_size": 3},
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_oversized_blob_splits_into_a_partition_of_subgroups(db_session, force_split):
    batch_id = await _make_run(db_session)
    ids = await _chain(db_session, batch_id, n=8, loose_at={2, 5})  # splits {0,1,2} {3,4,5} {6,7}
    service = IngestionService(IngestionRepository(db_session))

    page = await service.list_clusters("tier_a", batch_id=batch_id)

    assert page["has_next"] is False
    assert len(page["items"]) >= 2
    returned = [str(m["image_id"]) for c in page["items"] for m in c["members"]]
    assert sorted(returned) == sorted(str(i) for i in ids)   # partition: nothing dropped/dupd
    assert len(returned) == len(set(returned))
    assert all(len(c["members"]) <= 3 for c in page["items"])


@pytest.mark.asyncio(loop_scope="session")
async def test_splitting_disabled_returns_one_cluster_per_blob(db_session, monkeypatch):
    monkeypatch.setattr(
        "Backend.app.services.ingestion_service._split_params", lambda tier: None,
    )
    batch_id = await _make_run(db_session)
    ids = await _chain(db_session, batch_id, n=8, loose_at={2, 5})
    service = IngestionService(IngestionRepository(db_session))

    page = await service.list_clusters("tier_a", batch_id=batch_id)

    assert len(page["items"]) == 1
    assert len(page["items"][0]["members"]) == 8


@pytest.mark.asyncio(loop_scope="session")
async def test_decision_on_a_split_member_still_settles_its_cross_subgroup_pair(db_session, force_split):
    batch_id = await _make_run(db_session)
    ids = await _chain(db_session, batch_id, n=8, loose_at={2, 5})
    service = IngestionService(IngestionRepository(db_session))

    # ids[2] and ids[3] are in different subgroups but share the loose 0.045 pair.
    from unittest.mock import patch
    with patch("Backend.app.services.ingestion_service.image_store.move_to_rejected"):
        await service.resolve("tier_a", [{"image_id": ids[2], "decision": "reject"}])

    page = await service.list_clusters("tier_a", batch_id=batch_id)
    remaining_pairs = [
        (e["image_id1"], e["image_id2"]) for c in page["items"] for e in c["edges"]
    ]
    assert (str(min(ids[2], ids[3])), str(max(ids[2], ids[3]))) not in remaining_pairs
    blocked = await service.repo.get_blocked_pending_ids(batch_id, tier_a_high=0.05, tier_b_high=0.3)
    assert ids[2] not in blocked


@pytest.mark.asyncio(loop_scope="session")
async def test_tier_b_blob_splits_with_the_tier_b_ladder(db_session, force_split_tier_b):
    batch_id = await _make_run(db_session)
    # Tier B band: tight links 0.08, loose links 0.22 -> one union-find blob, splits at the
    # loose links into {0,1,2} {3,4,5} {6,7}.
    ids = await _chain(
        db_session, batch_id, n=8, loose_at={2, 5},
        tight=0.08, loose=settings.DUPLICATES.THRESHOLD - 0.01,
    )
    service = IngestionService(IngestionRepository(db_session))

    page = await service.list_clusters("tier_b", batch_id=batch_id)

    assert page["has_next"] is False
    assert len(page["items"]) >= 2
    returned = [str(m["image_id"]) for c in page["items"] for m in c["members"]]
    assert sorted(returned) == sorted(str(i) for i in ids)   # partition: nothing dropped/dupd
    assert len(returned) == len(set(returned))
    assert all(len(c["members"]) <= 3 for c in page["items"])


@pytest.mark.asyncio(loop_scope="session")
async def test_tier_b_splitting_disabled_returns_one_cluster_per_blob(db_session, monkeypatch):
    monkeypatch.setattr(
        "Backend.app.services.ingestion_service._split_params", lambda tier: None,
    )
    batch_id = await _make_run(db_session)
    ids = await _chain(
        db_session, batch_id, n=8, loose_at={2, 5},
        tight=0.08, loose=settings.DUPLICATES.THRESHOLD - 0.01,
    )
    service = IngestionService(IngestionRepository(db_session))

    page = await service.list_clusters("tier_b", batch_id=batch_id)

    assert len(page["items"]) == 1
    assert len(page["items"][0]["members"]) == 8
