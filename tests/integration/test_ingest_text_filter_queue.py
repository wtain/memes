"""An auto-kept pair leaves the Tier B queue, the progress counts and the promote check (Task 4).

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService
from batch.clusterize import PROXIMITY_THRESHOLD as TIER_A_THRESHOLD
from batch.ingest_promote import get_promotable_ids
from batch.ingest_text_filter import apply_plan, build_plan
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, OCRLemma, TmpDuplicates

THRESHOLD = 0.12


async def _image(session, status, batch=None, prefix="w") -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch)
    session.add(image)
    await session.flush()
    session.add_all([OCRLemma(image_id=image.id, lemma=f"{prefix}{i}") for i in range(4)])
    await session.flush()
    return image.id


async def _pair(session, a, b, distance=0.08) -> uuid.UUID:
    row = TmpDuplicates(image_id1=min(a, b), image_id2=max(a, b), distance=distance, match_source="cross_corpus")
    session.add(row)
    await session.flush()
    return row.id


async def _setup(session, other_prefixes):
    """One pending subject paired with one active image per prefix (a different prefix = different text)."""
    batch = await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_b_review")
    subject = await _image(session, "pending", batch, "s")
    for prefix in other_prefixes:
        await _pair(session, subject, await _image(session, "active", None, prefix))
    return batch, subject


async def _page(session, batch):
    return await IngestionService(IngestionRepository(session)).list_tier_b_review(batch_id=batch, limit=10)


@pytest.mark.asyncio(loop_scope="session")
async def test_auto_kept_pair_disappears_from_tier_b_page_and_progress(db_session):
    batch, subject = await _setup(db_session, ["x"])
    repo = IngestionRepository(db_session)
    before = await _page(db_session, batch)
    remaining_before, _ = await repo.get_review_progress(batch, "tier_b", 0.0, TIER_A_THRESHOLD, TIER_A_THRESHOLD, THRESHOLD)
    assert len(before["items"]) == 1 and remaining_before == 1

    plan = await build_plan(db_session)
    await apply_plan(db_session, uuid.uuid4(), plan.hash)

    assert (await _page(db_session, batch))["items"] == []
    remaining_after, _ = await repo.get_review_progress(batch, "tier_b", 0.0, TIER_A_THRESHOLD, TIER_A_THRESHOLD, THRESHOLD)
    assert remaining_after == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_only_auto_kept_pairs_becomes_promotable(db_session):
    batch, subject = await _setup(db_session, ["x", "y"])
    repo = IngestionRepository(db_session)
    assert await get_promotable_ids(repo, batch, THRESHOLD) == []

    plan = await build_plan(db_session)
    await apply_plan(db_session, uuid.uuid4(), plan.hash)

    assert await get_promotable_ids(repo, batch, THRESHOLD) == [subject]


@pytest.mark.asyncio(loop_scope="session")
async def test_image_with_one_kept_and_one_open_pair_is_not_promotable(db_session):
    batch, subject = await _setup(db_session, ["x"])
    similar = await _image(db_session, "active", None, "s")   # same words as the subject: stays for review
    await _pair(db_session, subject, similar)
    repo = IngestionRepository(db_session)

    plan = await build_plan(db_session)
    assert await apply_plan(db_session, uuid.uuid4(), plan.hash) == 1

    assert await get_promotable_ids(repo, batch, THRESHOLD) == []
    page = await _page(db_session, batch)
    assert len(page["items"]) == 1 and len(page["items"][0]["candidates"]) == 1
