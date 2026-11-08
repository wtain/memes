"""
B2: files written by the upload endpoint (POST /api/uploads, B1) into the ingestion inbox are picked up by
ingestion Stage 1 (batch/ingest_hash_dedup.py) and Stage 1.5 (batch/ingest_validate_formats.py).

Design: docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-b2-ingestion-pickup-verification-design.md

The REAL upload router and service run in process (httpx ASGITransport, so everything stays on the test's event loop)
with the REAL ImageRepository on the test database session; the REAL ingest_hash_dedup.run() then processes the inbox.
Layout mirrors production: tmp_path/base is BASE_PATH and tmp_path/base/inbox is PATH_INGESTION_SOURCE.
Needs the dedicated test database (tests/integration/conftest.py). Nothing here touches a real library.
"""
import asyncio
import hashlib
import os
import threading
import unicodedata
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from PIL import Image as PILImage
from sqlalchemy import func, select

from Backend.app.api import uploads as uploads_module
from Backend.app.api.uploads import get_upload_image_repository, router as uploads_router
from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services import inbox_upload_service as service_module
from batch import ingest_validate_formats
from batch.ingest_hash_dedup import (
    dedupe_cross_corpus, dedupe_in_batch, hash_incoming_files, register_and_move_to_base_path, run,
)
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, PopularityAbsorption

MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "BMP": "image/bmp"}


def _image(fmt="PNG", color=(255, 0, 0), size=(4, 4)) -> bytes:
    buffer = BytesIO()
    PILImage.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _part(name: str, data: bytes, fmt: str = "PNG"):
    return ("files", (name, data, MIME[fmt]))


@pytest.fixture
def base(tmp_path):
    path = tmp_path / "base"
    path.mkdir()
    return path


@pytest.fixture
def inbox(base):
    path = base / "inbox"
    path.mkdir()
    return path


@pytest_asyncio.fixture(loop_scope="session")
async def api(inbox, base, db_session):
    """POST helper against the real upload router backed by the real ImageRepository on the test session."""
    stub = SimpleNamespace()
    values = {"PATH_INGESTION_SOURCE": str(inbox), "BASE_PATH": str(base)}
    stub.get = lambda key, default=None: values.get(key, default)

    app = FastAPI()
    app.include_router(uploads_router, prefix="/api")
    # Production gives every request its own session; the test shares one, so only the lookup is serialised
    # (the file writes of concurrent requests still race in worker threads).
    db_lock = asyncio.Lock()
    repo = ImageRepository(db_session)
    real_lookup = repo.existing_content_hashes

    async def locked_lookup(hashes):
        async with db_lock:
            return await real_lookup(hashes)

    repo.existing_content_hashes = locked_lookup
    app.dependency_overrides[get_upload_image_repository] = lambda: repo
    with patch.object(uploads_module, "upload_limiter") as limiter, patch.object(service_module, "settings", stub):
        limiter.is_allowed = AsyncMock(return_value=True)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async def post(*parts):
                response = await client.post("/api/uploads", files=list(parts))
                assert response.status_code == 200, response.text
                return response.json()
            yield post


async def _start_run(db_session):
    return await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")


async def _images_of(db_session, batch_id):
    result = await db_session.execute(select(Image).where(Image.ingestion_batch_id == batch_id))
    return {image.filename: image for image in result.scalars()}


async def _absorption_count(db_session) -> int:
    return (await db_session.execute(select(func.count()).select_from(PopularityAbsorption))).scalar_one()


def _files(path):
    return sorted(p.name for p in path.iterdir() if p.is_file())


# --------------------------------------------------------------------------
# (a) pickup: names, hashes, registration, collision with the library
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_uploaded_files_are_registered_pending_and_moved_to_base_path(api, inbox, base, db_session):
    cyr = _image("PNG", (1, 2, 3))
    spaced = _image("JPEG", (4, 5, 6))
    plain = _image("PNG", (7, 8, 9))
    nfd_name = unicodedata.normalize("NFD", "ёж.png")
    nfd = _image("PNG", (10, 11, 12))
    body = await api(_part("Мем про кота.png", cyr), _part("my holiday  photo.jpg", spaced, "JPEG"),
                     _part("20241108_123456.png", plain), _part(nfd_name, nfd))
    assert [u["status"] for u in body["uploaded"]] == ["accepted"] * 4

    # A name that collides with a DIFFERENT file already in BASE_PATH: ingestion (not upload) renames it.
    (base / "20241108_123456.png").write_bytes(b"older library file, different bytes")

    batch_id = await _start_run(db_session)
    stats = await run(db_session, str(inbox), str(base), batch_id)

    assert stats["intake"] == 4 and stats["registered"] == 4
    assert stats["hash_duplicates_in_batch"] == 0 and stats["hash_duplicates_cross_corpus"] == 0
    images = await _images_of(db_session, batch_id)
    assert set(images) == {"Мем про кота.png", "my holiday  photo.jpg", "20241108_123456_1.png",
                           unicodedata.normalize("NFC", "ёж.png")}
    expected = {"Мем про кота.png": cyr, "my holiday  photo.jpg": spaced, "20241108_123456_1.png": plain,
                unicodedata.normalize("NFC", "ёж.png"): nfd}
    for filename, data in expected.items():
        image = images[filename]
        assert image.status == "pending"
        assert image.content_hash == _sha(data)
        assert image.ingestion_batch_id == batch_id
        assert (base / filename).read_bytes() == data  # moved, complete
    assert (base / "20241108_123456.png").read_bytes() == b"older library file, different bytes"  # untouched
    assert _files(inbox) == []


# --------------------------------------------------------------------------
# (b) staging directory and duplicates/ are ignored and survive
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_staging_and_duplicates_directories_are_ignored_and_survive(api, inbox, base, db_session):
    await api(_part("a.png", _image("PNG", (1, 1, 1))))
    staging = inbox / ".uploading"
    assert staging.is_dir()
    leftover = staging / "deadbeef"  # a crashed upload's staged file: must never be hashed or registered
    leftover.write_bytes(b"half written")
    (inbox / "duplicates").mkdir()
    (inbox / "duplicates" / "old.png").write_bytes(b"previously dropped copy")

    assert hash_incoming_files(str(inbox)) == {_sha(_image("PNG", (1, 1, 1))): ["a.png"]}

    batch_id = await _start_run(db_session)
    stats = await run(db_session, str(inbox), str(base), batch_id)

    assert stats["intake"] == 1 and stats["registered"] == 1
    assert staging.is_dir() and leftover.read_bytes() == b"half written"
    assert (inbox / "duplicates" / "old.png").read_bytes() == b"previously dropped copy"
    assert set(await _images_of(db_session, batch_id)) == {"a.png"}
    # the upload endpoint still works after ingestion moved everything out
    body = await api(_part("b.png", _image("PNG", (2, 2, 2))))
    assert body["uploaded"][0]["status"] == "accepted"


# --------------------------------------------------------------------------
# (c) duplicates inside the inbox never reach ingestion twice
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_same_content_uploaded_twice_or_under_two_names_leaves_one_inbox_file(api, inbox, base, db_session):
    data = _image("PNG", (9, 9, 9))
    first = await api(_part("one.png", data))
    again = await api(_part("one.png", data))
    renamed = await api(_part("two.png", data))
    in_one_request = await api(_part("three.png", data), _part("four.png", data))

    assert first["uploaded"][0]["status"] == "accepted"
    assert again["uploaded"][0]["status"] == "duplicate"
    assert renamed["uploaded"][0]["status"] == "duplicate"
    assert [u["status"] for u in in_one_request["uploaded"]] == ["duplicate", "duplicate"]
    assert _files(inbox) == ["one.png"]  # never two inbox files with one content

    batch_id = await _start_run(db_session)
    stats = await run(db_session, str(inbox), str(base), batch_id)
    assert stats["intake"] == 1 and stats["hash_duplicates_in_batch"] == 0 and stats["registered"] == 1
    assert stats["popularity_copies_recorded"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_same_content_twice_in_one_fresh_request_is_accepted_once(api, inbox):
    data = _image("PNG", (8, 8, 8))
    body = await api(_part("x.png", data), _part("y.png", data))
    assert [u["status"] for u in body["uploaded"]] == ["accepted", "duplicate"]
    assert _files(inbox) == ["x.png"]


# --------------------------------------------------------------------------
# (d) library duplicates are refused at upload, so ingestion adds no popularity
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("status", ["active", "pending", "rejected", "cut_proposed"])
async def test_library_duplicate_is_refused_at_upload_and_creates_no_absorption(api, inbox, base, db_session, status):
    data = _image("PNG", (50, 60, 70))
    db_session.add(Image(filename=f"lib_{status}.png", status=status, content_hash=_sha(data)))
    await db_session.flush()
    before = await _absorption_count(db_session)

    body = await api(_part("phone copy.png", data))

    assert body["uploaded"][0]["status"] == "duplicate"
    assert _files(inbox) == []  # nothing written
    batch_id = await _start_run(db_session)
    stats = await run(db_session, str(inbox), str(base), batch_id)
    assert stats["intake"] == 0 and stats["registered"] == 0
    assert stats["popularity_copies_recorded"] == 0 and stats["popularity_skipped"] == 0
    assert await _absorption_count(db_session) == before


@pytest.mark.asyncio(loop_scope="session")
async def test_contrast_a_hand_dropped_library_copy_does_add_popularity(inbox, base, db_session):
    """Guards test (d): the same flow WITHOUT the upload gate (a person drops the file) records an absorption,
    so a zero above is the upload endpoint's doing and the counter really can move."""
    data = _image("PNG", (51, 61, 71))
    db_session.add(Image(filename="lib.png", status="active", content_hash=_sha(data)))
    await db_session.flush()
    (inbox / "dropped.png").write_bytes(data)
    before = await _absorption_count(db_session)

    stats = await run(db_session, str(inbox), str(base), await _start_run(db_session))

    assert stats["hash_duplicates_cross_corpus"] == 1 and stats["popularity_copies_recorded"] == 1
    assert await _absorption_count(db_session) == before + 1


# --------------------------------------------------------------------------
# (e) no half-written file is ever visible to the scan
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_scan_during_a_slow_upload_never_sees_a_partial_file(api, inbox, base):
    # Uncompressed BMP, a few MB, so a copy-based publish would take measurable time.
    data = _image("BMP", (3, 4, 5), size=(1200, 1000))
    digest = _sha(data)
    real_publish = service_module._publish_once

    def slow_publish(staged, final):
        threading.Event().wait(0.4)  # the staged file is complete but not yet published
        return real_publish(staged, final)

    observed: list[dict] = []
    stop = threading.Event()

    def scanner():
        while not stop.is_set():
            observed.append(hash_incoming_files(str(inbox)))

    thread = threading.Thread(target=scanner)
    with patch.object(service_module, "_publish_once", slow_publish):
        thread.start()
        try:
            body = await api(_part("big.bmp", data, "BMP"))
        finally:
            stop.set()
            thread.join()
    assert body["uploaded"][0]["status"] == "accepted"
    assert len(observed) > 5
    # every scan saw either nothing, or the complete file; never a partial one
    for snapshot in observed:
        assert snapshot == {} or snapshot == {digest: ["big.bmp"]}
    assert any(snapshot == {} for snapshot in observed)  # the slow window really was observed
    assert hash_incoming_files(str(inbox)) == {digest: ["big.bmp"]}


# --------------------------------------------------------------------------
# (f) names and formats the later stages assume
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [".hidden.png", "..png", " .png", "...x.png", "./.a.png", "..\\..\\.b.png", ".png", ""])
def test_sanitiser_never_produces_a_hidden_name(raw):
    saved = service_module.sanitize_filename(raw, ".png")
    assert not saved.startswith(".")  # extract_text_from_memes skips names starting with a dot
    assert os.sep not in saved and "/" not in saved and "\\" not in saved
    assert saved.endswith(".png") and len(saved) > len(".png")


@pytest.mark.asyncio(loop_scope="session")
async def test_validate_formats_leaves_uploaded_jpeg_and_png_alone_and_converts_webp(api, inbox, base, db_session):
    jpeg = _image("JPEG", (12, 34, 56))
    png = _image("PNG", (65, 43, 21))
    webp = _image("WEBP", (99, 88, 77))
    await api(_part("a.jpeg", jpeg, "JPEG"), _part("b.png", png), _part("c.webp", webp, "WEBP"))
    batch_id = await _start_run(db_session)
    await run(db_session, str(inbox), str(base), batch_id)

    metrics = await ingest_validate_formats.run(db_session, str(base), batch_id)

    counters = metrics.counters_dict()
    assert counters.get("no_op") == 2 and counters.get("converted") == 1
    assert "error.fix_failed" not in counters and "unreadable" not in counters
    images = await _images_of(db_session, batch_id)
    assert set(images) == {"a.jpeg", "b.png", "c.jpg"}  # .jpeg is an accepted JPEG extension, kept
    assert (base / "a.jpeg").read_bytes() == jpeg and (base / "b.png").read_bytes() == png
    assert (base / "converted_originals" / "c.webp").read_bytes() == webp
    assert images["c.jpg"].content_hash == _sha((base / "c.jpg").read_bytes())
    assert all(not name.startswith(".") for name in images)


@pytest.mark.asyncio(loop_scope="session")
async def test_pending_status_scan_and_later_stages_select_uploaded_images_by_batch(api, inbox, base, db_session):
    await api(_part("keep.png", _image("PNG", (3, 3, 3))))
    batch_id = await _start_run(db_session)
    await run(db_session, str(inbox), str(base), batch_id)

    pending = await ingest_validate_formats.get_pending_batch_images(db_session, batch_id)
    assert [filename for _, filename in pending] == ["keep.png"]


# --------------------------------------------------------------------------
# stage building blocks used directly, with the upload result as input
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_stage_functions_in_order_match_run(api, inbox, base, db_session):
    data = _image("PNG", (70, 80, 90))
    await api(_part("s.png", data))
    batch_id = await _start_run(db_session)
    hashed = hash_incoming_files(str(inbox))
    survivors = dedupe_in_batch(str(inbox), hashed, str(inbox / "duplicates"))
    survivors = await dedupe_cross_corpus(db_session, str(inbox), survivors, str(inbox / "duplicates"))
    ids = await register_and_move_to_base_path(db_session, str(inbox), str(base), survivors, batch_id)
    assert len(ids) == 1
    assert (await db_session.get(Image, ids[0])).content_hash == _sha(data)
    assert not (inbox / "duplicates").exists()


@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_identical_uploads_yield_one_file_ingestion_sees_once(api, inbox, base, db_session):
    data = _image("PNG", (21, 22, 23))
    results = await asyncio.gather(*[api(_part(f"c{i}.png", data)) for i in range(4)])
    statuses = sorted(r["uploaded"][0]["status"] for r in results)
    assert statuses == ["accepted", "duplicate", "duplicate", "duplicate"]
    stats = await run(db_session, str(inbox), str(base), await _start_run(db_session))
    assert stats["intake"] == 1 and stats["registered"] == 1
