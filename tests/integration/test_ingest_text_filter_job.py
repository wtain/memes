"""Integration tests for batch/ingest_text_filter.py (ingestion Tier B text filter, Task 3).

The core functions take a session (like batch/ingest_promote.py's `promote`), so they run inside
the rolled-back db_session fixture. Requires a live PostgreSQL instance with pgvector.
"""
import json
import uuid
from datetime import datetime, timezone

import pytest

from batch import ingest_text_filter as job
from batch.ingest_text_filter import PlanMismatch, apply_plan, build_plan, plan_hash, undo, write_dry_run
from repository.batch_runs import BatchRunRepository
from repository.ingestion_text_filter import FilterParams
from Storage.models import Image, OCRLemma, OCRText, TmpDuplicates


async def _run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_b_review")


async def _image(session, status="pending", batch=None, prefix="w", n=4, text=None) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch)
    session.add(image)
    await session.flush()
    for i in range(n):
        session.add(OCRLemma(image_id=image.id, lemma=f"{prefix}{i}"))
    if text:
        session.add(OCRText(image_id=image.id, text=text, confidence=0.99))
    await session.flush()
    return image.id


async def _pair(session, a, b, distance=0.08, **extra) -> uuid.UUID:
    row = TmpDuplicates(image_id1=min(a, b), image_id2=max(a, b), distance=distance,
                        match_source="cross_corpus", **extra)
    session.add(row)
    await session.flush()
    return row.id


async def _scenario(session):
    """One clearly different pair (eligible) and one pair sharing most words (stays)."""
    batch = await _run(session)
    subject = await _image(session, "pending", batch, "a", text="SECRET-OCR-ALPHA")
    different = await _image(session, "active", None, "b", text="SECRET-OCR-BETA")
    similar = await _image(session, "active", None, "a")   # same lemmas as subject
    eligible = await _pair(session, subject, different)
    stays = await _pair(session, subject, similar)
    return batch, eligible, stays


def test_plan_hash_depends_on_ids_and_params_but_not_order():
    a, b = uuid.uuid4(), uuid.uuid4()
    p = FilterParams(3, 0.2, 0.10)
    assert plan_hash([a, b], p) == plan_hash([b, a], p)
    assert plan_hash([a, b], p) != plan_hash([a], p)
    assert plan_hash([a, b], p) != plan_hash([a, b], FilterParams(3, 0.3, 0.10))
    far = FilterParams(3, 0.2, 0.10, far_min_distance=0.10, far_max_overlap=0.5)
    assert plan_hash([a, b], p) != plan_hash([a, b], far)
    assert plan_hash([a, b], far) != plan_hash([a, b], FilterParams(3, 0.2, 0.10, far_min_distance=0.10, far_max_overlap=0.6))


@pytest.mark.asyncio(loop_scope="session")
async def test_dry_run_changes_nothing_and_writes_report(db_session, tmp_path):
    batch, eligible, stays = await _scenario(db_session)
    plan = await build_plan(db_session)

    stats = await write_dry_run(db_session, uuid.uuid4(), plan, tmp_path)

    assert (await db_session.get(TmpDuplicates, eligible)).tier_b_reviewed_at is None
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["plan_hash"] == plan.hash
    assert report["counts"]["eligible"] == 1
    assert report["counts"]["overlap_high"] == 1
    sample = (tmp_path / "sample.md").read_text(encoding="utf-8")
    assert "SECRET-OCR-ALPHA" in sample and "SECRET-OCR-BETA" in sample   # the report may hold OCR text
    assert stats == {"pairs_open": 2, "pairs_eligible": 1, "pairs_vetoed": 0, "pairs_no_text": 0,
                     "pairs_far_weak_text": 0, "pairs_applied": 0}
    assert "SECRET" not in json.dumps(stats)   # run stats never carry text


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_with_matching_hash_settles_only_eligible_pairs(db_session):
    batch, eligible, stays = await _scenario(db_session)
    plan = await build_plan(db_session)
    run = uuid.uuid4()

    applied = await apply_plan(db_session, run, plan.hash)

    assert applied == 1
    row = await db_session.get(TmpDuplicates, eligible)
    assert row.tier_b_reviewed_at is not None and row.tier_b_auto_kept_run == run
    assert (await db_session.get(TmpDuplicates, stays)).tier_b_reviewed_at is None


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_with_wrong_hash_changes_nothing(db_session):
    batch, eligible, stays = await _scenario(db_session)

    with pytest.raises(PlanMismatch):
        await apply_plan(db_session, uuid.uuid4(), "0" * 64)

    assert (await db_session.get(TmpDuplicates, eligible)).tier_b_reviewed_at is None


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_refuses_when_pair_set_changed_since_dry_run(db_session):
    batch, eligible, stays = await _scenario(db_session)
    plan = await build_plan(db_session)
    (await db_session.get(TmpDuplicates, eligible)).tier_b_reviewed_at = datetime.now(timezone.utc)  # a reviewer
    await db_session.flush()

    with pytest.raises(PlanMismatch):
        await apply_plan(db_session, uuid.uuid4(), plan.hash)


@pytest.mark.asyncio(loop_scope="session")
async def test_second_apply_of_same_plan_is_refused(db_session):
    batch, eligible, stays = await _scenario(db_session)
    plan = await build_plan(db_session)
    await apply_plan(db_session, uuid.uuid4(), plan.hash)

    with pytest.raises(PlanMismatch):
        await apply_plan(db_session, uuid.uuid4(), plan.hash)


@pytest.mark.asyncio(loop_scope="session")
async def test_undo_restores_the_queue(db_session):
    batch, eligible, stays = await _scenario(db_session)
    plan = await build_plan(db_session)
    run = uuid.uuid4()
    await apply_plan(db_session, run, plan.hash)

    assert await undo(db_session, run) == 1

    row = await db_session.get(TmpDuplicates, eligible)
    await db_session.refresh(row)
    assert row.tier_b_reviewed_at is None and row.tier_b_auto_kept_run is None
    assert (await build_plan(db_session)).hash == plan.hash


@pytest.mark.asyncio(loop_scope="session")
async def test_build_plan_fails_visibly_without_active_ingestion_run(db_session):
    with pytest.raises(RuntimeError, match="No ingestion run"):
        await build_plan(db_session)


def test_report_root_is_the_git_ignored_storage_directory():
    assert job.REPORT_ROOT.parts[-2:] == ("Storage", "ingestion-text-filter")


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_also_settles_far_pairs_without_strong_text_and_reports_them(db_session, tmp_path):
    batch = await _run(db_session)
    subject = await _image(db_session, "pending", batch, "a")
    notext = await _image(db_session, "active", None, "b", n=1)           # 1 lemma: no usable text
    far = await _pair(db_session, subject, notext, distance=0.11)
    near = await _image(db_session, "active", None, "c", n=1)
    near_pair = await _pair(db_session, subject, near, distance=0.07)     # same lack of text, but not far
    plan = await build_plan(db_session)

    stats = await write_dry_run(db_session, uuid.uuid4(), plan, tmp_path)
    applied = await apply_plan(db_session, uuid.uuid4(), plan.hash)

    assert stats["pairs_far_weak_text"] == 1 and plan.counts()["far_weak_text"] == 1
    assert applied == 1
    assert (await db_session.get(TmpDuplicates, far)).tier_b_reviewed_at is not None
    assert (await db_session.get(TmpDuplicates, near_pair)).tier_b_reviewed_at is None
    assert "far band" in (tmp_path / "sample.md").read_text(encoding="utf-8").lower()
