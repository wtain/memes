"""
Integration tests for batch/ingest_validate_formats.py (ingestion Stage 1.5: format
validation/fix for a batch's pending images).

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
Filesystem operations use pytest's tmp_path, standing in for BASE_PATH.
"""
import os

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from batch.ingest_validate_formats import run, should_advance_stage
from repository.batch_runs import BatchRunRepository
from repository.image_extras import ImageExtrasRepository
from Storage.models import Image, ImageExtras


def _save(base_path, filename: str, pillow_format: str) -> None:
    PILImage.new("RGB", (4, 4), (255, 0, 0)).save(os.path.join(str(base_path), filename), pillow_format)


def test_should_advance_stage_only_from_hash_dedup():
    """main() must not rewind a later stage back to format_validation when an operator
    re-runs ingest_hash_dedup.py mid-review and then re-runs this script per the runbook --
    the frontend's tierForStage() would drop the review queue until
    ingest_find_duplicates re-runs. No DB needed; this is a pure predicate."""
    assert should_advance_stage("hash_dedup") is True
    assert should_advance_stage("tier_a_review") is False
    assert should_advance_stage("tier_b_review") is False
    assert should_advance_stage("format_validation") is False
    assert should_advance_stage(None) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_renames_mislabeled_pending_image_and_updates_filename(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    image = Image(filename="a.jpg", status="pending", content_hash="orig", ingestion_batch_id=batch_id)
    db_session.add(image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "PNG")

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {"renamed": 1}
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.png"
    assert refreshed.content_hash == "orig"  # unchanged -- bytes weren't touched
    assert (tmp_path / "a.png").exists()


@pytest.mark.asyncio(loop_scope="session")
async def test_converts_webp_pending_image_and_updates_hash(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    image = Image(filename="a.jpg", status="pending", content_hash="orig", ingestion_batch_id=batch_id)
    db_session.add(image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "WEBP")

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {"converted": 1}
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.jpg"
    assert refreshed.content_hash != "orig"
    with PILImage.open(tmp_path / "a.jpg") as img:
        assert img.format == "JPEG"


@pytest.mark.asyncio(loop_scope="session")
async def test_flags_unreadable_pending_image_and_leaves_it_alone(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    image = Image(filename="broken.jpg", status="pending", ingestion_batch_id=batch_id)
    db_session.add(image)
    await db_session.flush()
    (tmp_path / "broken.jpg").write_bytes(b"not an image")

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {"unreadable": 1}
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "broken.jpg"  # unchanged
    extras = (await db_session.execute(
        select(ImageExtras).where(ImageExtras.image_id == image.id)
    )).scalar_one()
    assert extras.flagged is True
    assert extras.remarks == "unreadable during format validation"


@pytest.mark.asyncio(loop_scope="session")
async def test_noop_image_is_counted_and_untouched(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    image = Image(filename="a.jpg", status="pending", content_hash="orig", ingestion_batch_id=batch_id)
    db_session.add(image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "JPEG")

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {"no_op": 1}
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.jpg"
    assert refreshed.content_hash == "orig"


@pytest.mark.asyncio(loop_scope="session")
async def test_ignores_pending_images_from_a_different_batch(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    # ix_batch_runs_one_active_per_kind allows only one 'started' run per kind, so the
    # other batch must be completed before a second active "ingestion" run can be
    # created -- same pattern as test_ingest_find_duplicates.py's
    # test_excludes_other_batches_pending_images.
    other_batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    await runs_repo.commit(other_batch_id)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    other_image = Image(filename="a.jpg", status="pending", ingestion_batch_id=other_batch_id)
    db_session.add(other_image)
    await db_session.flush()

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_ignores_active_images(tmp_path, db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    active_image = Image(filename="a.jpg", status="active")
    db_session.add(active_image)
    await db_session.flush()

    metrics = await run(db_session, str(tmp_path), batch_id)

    assert metrics.counters_dict() == {}
