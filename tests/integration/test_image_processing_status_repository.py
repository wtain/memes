"""
Integration tests for repository/image_procesing_status.py's failure-tracking methods.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.images import OCR_LEMMAS_PIPELINE
from Storage.models import Image, ImageProcessingStatus


async def _insert_image(session, status: str = "active") -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_record_failure_writes_without_committing(db_session):
    image = await _insert_image(db_session)
    repo = ImageProcessingStatusRepository(db_session, "image_description:general_description")

    await repo.record_failure(image.id, "context size exceeded")
    await db_session.flush()

    result = await db_session.execute(
        select(ImageProcessingStatus).where(
            ImageProcessingStatus.image_id == image.id,
            ImageProcessingStatus.pipeline == "image_description:general_description",
        )
    )
    row = result.scalar_one()
    assert row.status == "failed"
    assert row.error_message == "context size exceeded"


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_done_by_id_writes_without_committing(db_session):
    image = await _insert_image(db_session)
    repo = ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE)

    await repo.mark_done_by_id(image.id)
    await db_session.flush()

    result = await db_session.execute(
        select(ImageProcessingStatus).where(
            ImageProcessingStatus.image_id == image.id,
            ImageProcessingStatus.pipeline == OCR_LEMMAS_PIPELINE,
        )
    )
    row = result.scalar_one()
    assert row.status == "done"
    assert row.finished_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_done_by_id_is_idempotent(db_session):
    image = await _insert_image(db_session)
    repo = ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE)

    await repo.mark_done_by_id(image.id)
    await repo.mark_done_by_id(image.id)
    await db_session.flush()

    result = await db_session.execute(
        select(ImageProcessingStatus).where(
            ImageProcessingStatus.image_id == image.id,
            ImageProcessingStatus.pipeline == OCR_LEMMAS_PIPELINE,
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_done_by_id_transitions_existing_row_to_done(db_session):
    """Proves the upsert is a real UPDATE on conflict, not a no-op --
    a row that already exists in some other status must transition to
    done, not be silently skipped."""
    image = await _insert_image(db_session)
    db_session.add(ImageProcessingStatus(image_id=image.id, pipeline=OCR_LEMMAS_PIPELINE, status="processing"))
    await db_session.flush()

    repo = ImageProcessingStatusRepository(db_session, OCR_LEMMAS_PIPELINE)
    await repo.mark_done_by_id(image.id)
    await db_session.flush()

    result = await db_session.execute(
        select(ImageProcessingStatus).where(
            ImageProcessingStatus.image_id == image.id,
            ImageProcessingStatus.pipeline == OCR_LEMMAS_PIPELINE,
        )
    )
    row = result.scalar_one()
    assert row.status == "done"
    assert row.finished_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_image_ids_with_status_filters_by_pipeline_and_status(db_session):
    image_a = await _insert_image(db_session)
    image_b = await _insert_image(db_session)

    repo_a = ImageProcessingStatusRepository(db_session, "image_description:general_description")
    repo_b = ImageProcessingStatusRepository(db_session, "image_description:humor_explanation")

    await repo_a.record_failure(image_a.id, "boom")
    await repo_b.record_failure(image_b.id, "boom")
    await db_session.flush()

    failed_for_a = await repo_a.get_image_ids_with_status("failed")
    assert failed_for_a == {image_a.id}

    failed_for_other_status = await repo_a.get_image_ids_with_status("done")
    assert failed_for_other_status == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_count_unprocessed_excludes_done_and_filters_by_registration_status(db_session):
    pipeline = "easyocr:en"
    repo = ImageProcessingStatusRepository(db_session, pipeline)

    pending_todo = await _insert_image(db_session, status="pending")
    pending_done = await _insert_image(db_session, status="pending")
    active_todo = await _insert_image(db_session, status="active")

    # A done row for this pipeline takes pending_done out of the count...
    await repo.mark_done_by_id(pending_done.id)
    # ...but a non-done row (interrupted run) still counts as work to do.
    db_session.add(ImageProcessingStatus(image_id=pending_todo.id, pipeline=pipeline, status="processing"))
    # A done row for a *different* pipeline must not exempt active_todo.
    db_session.add(ImageProcessingStatus(image_id=active_todo.id, pipeline="other", status="done"))
    await db_session.flush()

    assert await repo.count_unprocessed("pending") == 1
    assert await repo.count_unprocessed("active") == 1
    assert await repo.count_unprocessed(None) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_all_only_clears_its_own_pipeline(db_session):
    image_a = await _insert_image(db_session)
    image_b = await _insert_image(db_session)

    repo_a = ImageProcessingStatusRepository(db_session, "image_description:general_description")
    repo_b = ImageProcessingStatusRepository(db_session, "image_description:humor_explanation")

    await repo_a.record_failure(image_a.id, "boom")
    await repo_b.record_failure(image_b.id, "boom")
    await db_session.flush()

    await repo_a.delete_all()
    await db_session.flush()

    assert await repo_a.get_image_ids_with_status("failed") == set()
    assert await repo_b.get_image_ids_with_status("failed") == {image_b.id}
