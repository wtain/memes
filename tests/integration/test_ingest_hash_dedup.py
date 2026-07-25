"""
Integration tests for batch/ingest_hash_dedup.py (ingestion Stage 1: hash dedup).

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
Filesystem operations use pytest's tmp_path, standing in for PATH_INGESTION_SOURCE/BASE_PATH.
"""
import hashlib
import os

import pytest

from batch.ingest_hash_dedup import (
    dedupe_cross_corpus,
    dedupe_in_batch,
    hash_incoming_files,
    register_and_move_to_base_path,
    run,
)
from repository.batch_runs import BatchRunRepository
from Storage.models import Image


def _write(path, name: str, content: bytes) -> None:
    with open(os.path.join(path, name), "wb") as f:
        f.write(content)


# --------------------------------------------------------------------------
# hash_incoming_files / dedupe_in_batch
# --------------------------------------------------------------------------

def test_hash_incoming_files_groups_by_content(tmp_path):
    _write(tmp_path, "a.jpg", b"same-bytes")
    _write(tmp_path, "b.jpg", b"same-bytes")
    _write(tmp_path, "c.jpg", b"different-bytes")

    grouped = hash_incoming_files(str(tmp_path))

    sizes = sorted(len(v) for v in grouped.values())
    assert sizes == [1, 2]


def test_hash_incoming_files_ignores_subdirectories(tmp_path):
    _write(tmp_path, "a.jpg", b"bytes")
    (tmp_path / "subdir").mkdir()

    grouped = hash_incoming_files(str(tmp_path))

    assert sum(len(v) for v in grouped.values()) == 1


def test_dedupe_in_batch_keeps_lexicographic_first_and_moves_rest(tmp_path):
    _write(tmp_path, "b.jpg", b"same-bytes")
    _write(tmp_path, "a.jpg", b"same-bytes")
    duplicates_dir = str(tmp_path / "duplicates")

    survivors = dedupe_in_batch(str(tmp_path), hash_incoming_files(str(tmp_path)), duplicates_dir)

    assert list(survivors.keys()) == ["a.jpg"]
    assert os.path.exists(os.path.join(str(tmp_path), "a.jpg"))
    assert os.path.exists(os.path.join(duplicates_dir, "b.jpg"))
    assert not os.path.exists(os.path.join(str(tmp_path), "b.jpg"))


def test_dedupe_in_batch_leaves_unique_files_untouched(tmp_path):
    _write(tmp_path, "a.jpg", b"one")
    _write(tmp_path, "b.jpg", b"two")
    duplicates_dir = str(tmp_path / "duplicates")

    survivors = dedupe_in_batch(str(tmp_path), hash_incoming_files(str(tmp_path)), duplicates_dir)

    assert set(survivors.keys()) == {"a.jpg", "b.jpg"}
    assert not os.path.exists(duplicates_dir)  # never created -- nothing to move


# --------------------------------------------------------------------------
# dedupe_cross_corpus
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_dedupe_cross_corpus_moves_matches_against_active_images(tmp_path, db_session):
    db_session.add(Image(filename="existing.jpg", content_hash="abc123"))
    await db_session.flush()

    _write(tmp_path, "repost.jpg", b"whatever")  # content irrelevant -- hash injected below
    survivors = {"repost.jpg": "abc123", "new.jpg": "def456"}
    duplicates_dir = str(tmp_path / "duplicates")

    remaining = await dedupe_cross_corpus(db_session, str(tmp_path), survivors, duplicates_dir)

    assert remaining == {"new.jpg": "def456"}
    assert os.path.exists(os.path.join(duplicates_dir, "repost.jpg"))


@pytest.mark.asyncio(loop_scope="session")
async def test_dedupe_cross_corpus_ignores_pending_images(tmp_path, db_session):
    """Only the active corpus counts -- a matching pending image (e.g. from a concurrent
    or prior interrupted ingestion run) must not short-circuit this batch's own review."""
    db_session.add(Image(filename="pending.jpg", status="pending", content_hash="abc123"))
    await db_session.flush()

    _write(tmp_path, "new.jpg", b"whatever")
    survivors = {"new.jpg": "abc123"}
    duplicates_dir = str(tmp_path / "duplicates")

    remaining = await dedupe_cross_corpus(db_session, str(tmp_path), survivors, duplicates_dir)

    assert remaining == {"new.jpg": "abc123"}
    assert os.path.exists(os.path.join(str(tmp_path), "new.jpg"))


# --------------------------------------------------------------------------
# register_and_move_to_base_path
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_register_and_move_creates_pending_images_in_base_path(tmp_path, db_session):
    source = tmp_path / "source"
    base = tmp_path / "base"
    source.mkdir()
    _write(source, "new.jpg", b"bytes")

    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", stage="hash_dedup")

    ids = await register_and_move_to_base_path(
        db_session, str(source), str(base), {"new.jpg": "abc123"}, batch_id
    )

    assert len(ids) == 1
    image = await db_session.get(Image, ids[0])
    assert image.status == "pending"
    assert image.content_hash == "abc123"
    assert image.ingestion_batch_id == batch_id
    assert os.path.exists(base / "new.jpg")
    assert not os.path.exists(source / "new.jpg")


# --------------------------------------------------------------------------
# run() end to end
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_run_end_to_end_stats_and_final_state(tmp_path, db_session):
    db_session.add(Image(filename="existing.jpg", content_hash="cross-corpus-hash"))
    await db_session.flush()

    source = tmp_path / "source"
    base = tmp_path / "base"
    source.mkdir()
    _write(source, "dup_a.jpg", b"in-batch-dup")
    _write(source, "dup_b.jpg", b"in-batch-dup")
    _write(source, "repost.jpg", b"cross-corpus-content")  # hash won't actually match -- see below
    _write(source, "unique.jpg", b"unique-content")

    # dedupe_cross_corpus compares real sha256 hashes, so fabricate a real collision by
    # reusing the same bytes the "existing" active image would have been hashed from.
    real_hash = hashlib.sha256(b"cross-corpus-content").hexdigest()
    db_session.add(Image(filename="existing2.jpg", content_hash=real_hash))
    await db_session.flush()

    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", stage="hash_dedup")

    stats = await run(db_session, str(source), str(base), batch_id)

    assert stats["intake"] == 4
    assert stats["hash_duplicates_in_batch"] == 1
    assert stats["hash_duplicates_cross_corpus"] == 1
    assert stats["registered"] == 2

    assert set(os.listdir(base)) == {"dup_a.jpg", "unique.jpg"}
    assert set(os.listdir(source / "duplicates")) == {"dup_b.jpg", "repost.jpg"}
    assert os.listdir(source) == ["duplicates"]  # nothing left in the source dir but the subfolder


# --------------------------------------------------------------------------
# concurrent-run guard (BatchRunRepository primitive, used by main())
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_active_run_finds_started_ingestion_run(db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", stage="hash_dedup")

    active = await runs_repo.get_active_run(kind="ingestion")

    assert active is not None
    assert active.run_id == batch_id


@pytest.mark.asyncio(loop_scope="session")
async def test_get_active_run_none_once_completed(db_session):
    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", stage="hash_dedup")
    await runs_repo.commit(batch_id)

    active = await runs_repo.get_active_run(kind="ingestion")

    assert active is None
