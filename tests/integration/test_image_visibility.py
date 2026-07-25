"""
Visibility contract test for images.status.

Seeds one `active` and one `pending` image (with an embedding, OCR text, a tag,
and cluster/flag rows so every audited query path has something to return) and
asserts the pending image never appears in a default-parameter call, but does
appear when status="pending" is requested explicitly.

This is a regression safety net, not the guarantee itself -- see
docs/superpowers/specs/2026-07-25-image-visibility-status-design.md's "Full
audit" section for the authoritative list of call sites. Any PR adding a new
select(Image...) should add a row here too.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
import pytest_asyncio

from Backend.app.repositories.diagnostics_repository import DiagnosticsRepository
from Backend.app.repositories.image_repository import ImageRepository
from repository.images import ImagesRepository
from Storage.models import (
    Embedding,
    Image,
    ImageExtras,
    ImageTag,
    OCRText,
    TmpImageClusters,
)

_DIM = 512


def _unit_vector(index: int) -> list[float]:
    vec = [0.0] * _DIM
    vec[index] = 1.0
    return vec


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_images(db_session):
    active = Image(filename=f"{uuid.uuid4()}.jpg", content_hash="hash-active")
    pending = Image(filename=f"{uuid.uuid4()}.jpg", status="pending", content_hash="hash-pending")
    db_session.add_all([active, pending])
    await db_session.flush()

    db_session.add_all([
        Embedding(image_id=active.id, embedding=_unit_vector(0)),
        Embedding(image_id=pending.id, embedding=_unit_vector(0)),  # identical -> would rank #1 if leaked
        OCRText(image_id=active.id, text="hello", confidence=0.9),
        OCRText(image_id=pending.id, text="hello pending", confidence=0.9),
    ])
    db_session.add_all([
        TmpImageClusters(cluster_id=1, image_id=active.id),
        TmpImageClusters(cluster_id=1, image_id=pending.id),
    ])
    db_session.add_all([
        ImageExtras(image_id=active.id, flagged=True),
        ImageExtras(image_id=pending.id, flagged=True),
    ])
    await db_session.flush()
    return active, pending


# --------------------------------------------------------------------------
# repository/images.py (global repo, used by batch jobs)
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_images_excludes_pending_by_default(db_session, seeded_images):
    active, pending = seeded_images
    repo = ImagesRepository(db_session)

    default_ids = {i for (_, i) in await repo.get_all_images()}
    pending_ids = {i for (_, i) in await repo.get_all_images(status="pending")}

    assert active.id in default_ids
    assert pending.id not in default_ids
    assert pending.id in pending_ids
    assert active.id not in pending_ids


@pytest.mark.asyncio(loop_scope="session")
async def test_iterate_images_excludes_pending_by_default(db_session, seeded_images):
    active, pending = seeded_images
    repo = ImagesRepository(db_session)

    ids = {i async for (_, i) in repo.iterate_images()}

    assert active.id in ids
    assert pending.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_excludes_pending_by_default(db_session, seeded_images):
    active, pending = seeded_images
    repo = ImagesRepository(db_session)

    rows = await repo.get_images_and_ocr_texts()
    ids = {r[1] for r in rows}

    assert active.id in ids
    assert pending.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_get_all_images_with_hash_excludes_pending_by_default(db_session, seeded_images):
    active, pending = seeded_images
    repo = ImagesRepository(db_session)

    rows = await repo.get_all_images_with_hash()
    ids = {r[0] for r in rows}

    assert active.id in ids
    assert pending.id not in ids


# --------------------------------------------------------------------------
# Backend/app/repositories/image_repository.py
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_search_excludes_pending(db_session, seeded_images):
    active, pending = seeded_images
    repo = ImageRepository(db_session)

    rows, _facets = await repo.search(q=None, tags={}, cursor_created_at=None, cursor_id=None, limit=50)
    ids = {r.id for r in rows}

    assert active.id in ids
    assert pending.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_get_similar_excludes_pending_even_at_zero_distance(db_session, seeded_images):
    active, pending = seeded_images
    repo = ImageRepository(db_session)

    # Query with a third, unrelated embedding so both active/pending are "candidates" —
    # pending has the identical vector to active, so if the status filter were missing it
    # would rank first (distance 0) rather than being absent entirely.
    rows = await repo.get_similar(uuid.uuid4(), _unit_vector(0), limit=10)
    ids = {r.image_id for r in rows}

    assert active.id in ids
    assert pending.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_get_untagged_excludes_pending(db_session, seeded_images):
    active, pending = seeded_images
    repo = ImageRepository(db_session)

    rows = await repo.get_untagged(cursor_created_at=None, cursor_id=None, limit=50)
    ids = {r.id for r in rows}

    assert active.id in ids  # neither image has a tag
    assert pending.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_get_no_ocr_excludes_pending(db_session, seeded_images):
    active, pending = seeded_images
    repo = ImageRepository(db_session)
    other = Image(filename=f"{uuid.uuid4()}.jpg")  # active, genuinely no OCR
    db_session.add(other)
    await db_session.flush()

    rows = await repo.get_no_ocr(cursor_created_at=None, cursor_id=None, limit=50)
    ids = {r.id for r in rows}

    assert other.id in ids
    assert active.id not in ids  # has OCR — excluded on its own merits
    assert pending.id not in ids  # would also have no OCR-exclusion reason if status leaked


@pytest.mark.asyncio(loop_scope="session")
async def test_get_duplicates_clustered_excludes_pending(db_session, seeded_images):
    active, pending = seeded_images
    repo = ImageRepository(db_session)

    rows = await repo.get_duplicates_clustered(after_cluster_id=None, limit=50)
    ids = {r[0] for r in rows}

    assert active.id in ids
    assert pending.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_get_flagged_excludes_pending(db_session, seeded_images):
    active, pending = seeded_images
    repo = ImageRepository(db_session)

    rows = await repo.get_flagged(cursor_created_at=None, cursor_id=None, limit=50)
    ids = {r.id for r in rows}

    assert active.id in ids
    assert pending.id not in ids  # flagged=True too, but pending status must still win


# --------------------------------------------------------------------------
# Backend/app/repositories/diagnostics_repository.py
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_statistics_counts_exclude_pending(db_session, seeded_images):
    active, pending = seeded_images
    repo = DiagnosticsRepository(db_session)

    stats = await repo.get_statistics()

    # Both images have an embedding/OCR row and are in the same cluster; if the
    # pending one leaked in, these counts would be 2 instead of 1.
    assert stats.total_memes == 1
    assert stats.with_embeddings == 1
    assert stats.with_ocr == 1
