"""
Integration tests for batch/ingest_abort.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
Uses tmp_path for base_path/source_path, matching batch/tests/test_move_flagged.py's
established real-file pattern -- this script's correctness depends on real FK cascade
behavior (embeddings/ocr_texts/tmp_duplicates all cascade-delete on images.id), worth
exercising against the actual schema rather than mocks.
"""
import uuid

import pytest

from batch.ingest_abort import run
from repository.batch_runs import BatchRunRepository
from Storage.models import Embedding, Image, OCRText, TmpDuplicates


async def _make_run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_a_review")


async def _make_image(session, status: str, batch_id, filename: str) -> uuid.UUID:
    image = Image(filename=filename, status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    return image.id


@pytest.mark.asyncio(loop_scope="session")
async def test_pending_image_moved_back_and_row_and_related_rows_deleted(db_session, tmp_path):
    base_path = tmp_path / "base"
    base_path.mkdir()
    source_path = tmp_path / "inbox"
    source_path.mkdir()
    (base_path / "pending.jpg").write_bytes(b"x")

    batch_id = await _make_run(db_session)
    image_id = await _make_image(db_session, "pending", batch_id, "pending.jpg")
    db_session.add(Embedding(image_id=image_id, embedding=[0.0] * 512))
    db_session.add(OCRText(image_id=image_id, text="hello", confidence=0.9, language="en"))
    await db_session.flush()
    other_id = await _make_image(db_session, "active", None, "other.jpg")
    db_session.add(TmpDuplicates(
        image_id1=min(image_id, other_id), image_id2=max(image_id, other_id),
        distance=0.1, match_source="cross_corpus",
    ))
    await db_session.flush()

    metrics = await run(db_session, str(source_path), str(base_path), batch_id)

    assert metrics.counters_dict() == {"moved_back": 1, "unregistered": 1}
    assert (source_path / "pending.jpg").exists()
    assert not (base_path / "pending.jpg").exists()
    assert (await db_session.get(Image, image_id)) is None
    embeddings_left = (await db_session.execute(
        Embedding.__table__.select().where(Embedding.image_id == image_id)
    )).all()
    assert embeddings_left == []
    ocr_left = (await db_session.execute(
        OCRText.__table__.select().where(OCRText.image_id == image_id)
    )).all()
    assert ocr_left == []
    pairs_left = (await db_session.execute(
        TmpDuplicates.__table__.select().where(
            (TmpDuplicates.image_id1 == image_id) | (TmpDuplicates.image_id2 == image_id)
        )
    )).all()
    assert pairs_left == []


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_image_sourced_from_rejected_subdir(db_session, tmp_path):
    base_path = tmp_path / "base"
    (base_path / "rejected").mkdir(parents=True)
    source_path = tmp_path / "inbox"
    source_path.mkdir()
    (base_path / "rejected" / "rej.jpg").write_bytes(b"x")

    batch_id = await _make_run(db_session)
    await _make_image(db_session, "rejected", batch_id, "rej.jpg")

    metrics = await run(db_session, str(source_path), str(base_path), batch_id)

    assert metrics.counters_dict() == {"moved_back": 1, "unregistered": 1}
    assert (source_path / "rej.jpg").exists()
    assert not (base_path / "rejected" / "rej.jpg").exists()


@pytest.mark.asyncio(loop_scope="session")
async def test_active_image_in_same_batch_untouched(db_session, tmp_path):
    base_path = tmp_path / "base"
    base_path.mkdir()
    source_path = tmp_path / "inbox"
    source_path.mkdir()
    (base_path / "promoted.jpg").write_bytes(b"x")

    batch_id = await _make_run(db_session)
    image_id = await _make_image(db_session, "active", batch_id, "promoted.jpg")

    metrics = await run(db_session, str(source_path), str(base_path), batch_id)

    assert metrics.counters_dict() == {"unregistered": 0}
    assert (base_path / "promoted.jpg").exists()
    assert not (source_path / "promoted.jpg").exists()
    assert (await db_session.get(Image, image_id)) is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_file_does_not_abort_remaining_and_row_still_deleted(db_session, tmp_path):
    base_path = tmp_path / "base"
    base_path.mkdir()
    source_path = tmp_path / "inbox"
    source_path.mkdir()
    # no file written for this image -- simulates a file already missing on disk
    (base_path / "good.jpg").write_bytes(b"x")

    batch_id = await _make_run(db_session)
    image_id = await _make_image(db_session, "pending", batch_id, "missing.jpg")
    good_image_id = await _make_image(db_session, "pending", batch_id, "good.jpg")

    metrics = await run(db_session, str(source_path), str(base_path), batch_id)

    assert metrics.counters_dict() == {"error.move_failed": 1, "moved_back": 1, "unregistered": 2}
    assert (await db_session.get(Image, image_id)) is None
    assert (await db_session.get(Image, good_image_id)) is None
    assert (source_path / "good.jpg").exists()
    assert not (base_path / "good.jpg").exists()
