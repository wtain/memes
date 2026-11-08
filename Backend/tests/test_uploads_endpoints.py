"""
Tests for the uploads endpoint (POST /api/uploads), which writes into the ingestion inbox
(PATH_INGESTION_SOURCE). Design: docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-b1-inbox-upload-design.md

Real file I/O goes to a tmp_path inbox; the library duplicate lookup is a fake repository.
"""
import hashlib
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api import uploads as uploads_module
from Backend.app.api.uploads import get_upload_image_repository, router as uploads_router
from Backend.app.services import inbox_upload_service as service_module
from batch.ingest_hash_dedup import hash_incoming_files
from batch.utils.file_hash import sha256_file

app = FastAPI()
app.include_router(uploads_router, prefix="/api")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PNG_OTHER = b"\x89PNG\r\n\x1a\n" + b"\x01" * 16
JPEG_MAGIC = b"\xff\xd8\xff" + b"\x00" * 16


def _png(name="meme.png", data=PNG_MAGIC):
    return ("files", (name, data, "image/png"))


def _listing(inbox):
    return sorted(p.name for p in inbox.iterdir() if p.is_file())


@pytest.fixture
def inbox(tmp_path):
    path = tmp_path / "inbox"
    path.mkdir()
    return path


@pytest.fixture
def setting(inbox):
    """Mutable stand-in for config.settings: tests change setting.value."""
    stub = SimpleNamespace(value=str(inbox))
    stub.get = lambda key, default=None: stub.value if key == "PATH_INGESTION_SOURCE" else default
    return stub


@pytest.fixture
def library():
    """Fake repository: library.hashes is what 'the database' already holds."""
    repo = SimpleNamespace(hashes=set())

    async def existing(hashes):
        return {h for h in hashes if h in repo.hashes}

    repo.existing_content_hashes = AsyncMock(side_effect=existing)
    return repo


@pytest.fixture
def client(setting, library):
    app.dependency_overrides[get_upload_image_repository] = lambda: library
    with patch.object(uploads_module, "upload_limiter") as mock_limiter, \
            patch.object(service_module, "settings", setting):
        mock_limiter.is_allowed = AsyncMock(return_value=True)
        with TestClient(app) as test_client:
            test_client.mock_limiter = mock_limiter
            yield test_client
    app.dependency_overrides.clear()


class TestUploadSuccess:
    def test_single_image_is_written_to_the_inbox_under_its_original_name(self, client, inbox):
        response = client.post("/api/uploads", files=[_png("20241108_123456.png")])

        assert response.status_code == 200
        data = response.json()
        assert data["total_accepted"] == 1
        assert data["total_failed"] == 0
        entry = data["uploaded"][0]
        assert entry["original_filename"] == "20241108_123456.png"
        assert entry["saved_as"] == "20241108_123456.png"
        assert entry["status"] == "accepted"
        assert entry["content_type"] == "image/png"
        assert entry["size_bytes"] == len(PNG_MAGIC)
        assert data["failed"] == []
        assert (inbox / "20241108_123456.png").read_bytes() == PNG_MAGIC

    def test_multiple_images(self, client, inbox):
        response = client.post("/api/uploads", files=[
            _png("a.png"), ("files", ("b.jpg", JPEG_MAGIC, "image/jpeg"))])

        data = response.json()
        assert data["total_accepted"] == 2
        assert _listing(inbox) == ["a.png", "b.jpg"]

    def test_hostile_and_cyrillic_names_are_sanitised(self, client, inbox):
        response = client.post("/api/uploads", files=[
            _png("../../evil.png", PNG_MAGIC),
            _png("Мем:кот?.png", PNG_OTHER),
        ])

        saved = [u["saved_as"] for u in response.json()["uploaded"]]
        assert saved == ["evil.png", "Мем_кот_.png"]
        assert _listing(inbox) == ["evil.png", "Мем_кот_.png"]
        assert not (inbox.parent / "evil.png").exists()

    def test_name_collision_with_different_content_gets_a_numeric_suffix(self, client, inbox):
        (inbox / "meme.png").write_bytes(PNG_OTHER)

        first = client.post("/api/uploads", files=[_png()]).json()
        second = client.post("/api/uploads", files=[_png(data=PNG_MAGIC + b"x")]).json()

        assert first["uploaded"][0]["saved_as"] == "meme_1.png"
        assert second["uploaded"][0]["saved_as"] == "meme_2.png"
        assert (inbox / "meme.png").read_bytes() == PNG_OTHER

    def test_hash_matches_the_batch_algorithm(self, inbox):
        path = inbox / "x.png"
        path.write_bytes(PNG_MAGIC)
        assert sha256_file(str(path)) == hashlib.sha256(PNG_MAGIC).hexdigest()


class TestDuplicates:
    def test_duplicate_of_a_file_in_the_inbox_writes_nothing(self, client, inbox):
        (inbox / "existing.png").write_bytes(PNG_MAGIC)

        data = client.post("/api/uploads", files=[_png("again.png")]).json()

        entry = data["uploaded"][0]
        assert entry["status"] == "duplicate"
        assert entry["saved_as"] == "existing.png"
        assert data["total_accepted"] == 1
        assert _listing(inbox) == ["existing.png"]
        assert list((inbox / ".uploading").iterdir()) == []

    def test_duplicate_of_a_library_image_writes_nothing(self, client, inbox, library):
        library.hashes.add(hashlib.sha256(PNG_MAGIC).hexdigest())

        data = client.post("/api/uploads", files=[_png("20240101_000000.png")]).json()

        entry = data["uploaded"][0]
        assert entry["status"] == "duplicate"
        assert entry["saved_as"] == "20240101_000000.png"
        assert _listing(inbox) == []
        assert list((inbox / ".uploading").iterdir()) == []
        library.existing_content_hashes.assert_awaited_once()

    def test_two_identical_files_in_one_request(self, client, inbox):
        data = client.post("/api/uploads", files=[_png("a.png"), _png("b.png")]).json()

        assert [u["status"] for u in data["uploaded"]] == ["accepted", "duplicate"]
        assert data["uploaded"][1]["saved_as"] == "a.png"
        assert _listing(inbox) == ["a.png"]

    def test_same_size_different_content_is_not_a_duplicate(self, client, inbox):
        (inbox / "other.png").write_bytes(PNG_OTHER)

        data = client.post("/api/uploads", files=[_png()]).json()

        assert data["uploaded"][0]["status"] == "accepted"

    def test_directories_and_unreadable_files_in_the_inbox_are_skipped(self, client, inbox):
        (inbox / "sub").mkdir()
        (inbox / "bad.png").write_bytes(PNG_MAGIC)
        real_sha = service_module.sha256_file

        def flaky(path):
            if path.endswith("bad.png"):
                raise PermissionError("locked")
            return real_sha(path)

        with patch.object(service_module, "sha256_file", flaky):
            data = client.post("/api/uploads", files=[_png("new.png")]).json()

        assert data["uploaded"][0]["status"] == "accepted"
        assert data["uploaded"][0]["saved_as"] == "new.png"

    def test_database_failure_is_a_503_and_writes_nothing(self, client, inbox, library):
        library.existing_content_hashes = AsyncMock(side_effect=ConnectionRefusedError("db down"))

        response = client.post("/api/uploads", files=[_png()])

        assert response.status_code == 503
        assert _listing(inbox) == []
        assert list((inbox / ".uploading").iterdir()) == []


class TestAtomicity:
    def test_file_is_staged_outside_the_inbox_listing_until_replaced(self, client, inbox):
        seen = {}
        real_replace = os.replace

        def spying_replace(src, dst):
            seen["inbox_before"] = _listing(inbox)
            seen["hashed_before"] = hash_incoming_files(str(inbox))
            seen["src_parent"] = os.path.dirname(src)
            return real_replace(src, dst)

        with patch.object(service_module.os, "replace", spying_replace):
            client.post("/api/uploads", files=[_png("a.png")])

        assert seen["inbox_before"] == []
        assert seen["hashed_before"] == {}
        assert seen["src_parent"] == str(inbox / ".uploading")
        assert _listing(inbox) == ["a.png"]
        assert hash_incoming_files(str(inbox)) == {hashlib.sha256(PNG_MAGIC).hexdigest(): ["a.png"]}

    def test_write_error_is_a_500_and_leaves_no_staged_file(self, client, inbox):
        with patch.object(service_module.os, "replace", side_effect=OSError("disk full")):
            response = client.post("/api/uploads", files=[_png()])

        assert response.status_code == 500
        assert _listing(inbox) == []
        assert list((inbox / ".uploading").iterdir()) == []

    def test_stale_staging_files_are_removed_and_fresh_ones_kept(self, client, inbox):
        staging = inbox / ".uploading"
        staging.mkdir()
        stale, fresh = staging / "stale", staging / "fresh"
        stale.write_bytes(b"x")
        fresh.write_bytes(b"x")
        old = time.time() - 2 * 3600
        os.utime(stale, (old, old))

        client.post("/api/uploads", files=[_png()])

        assert not stale.exists()
        assert fresh.exists()

    def test_concurrent_requests_with_the_same_name_never_overwrite(self, client, inbox):
        results = []

        def post(data):
            results.append(client.post("/api/uploads", files=[_png("same.png", data)]).json())

        payloads = [PNG_MAGIC + bytes([i]) for i in range(6)]
        threads = [threading.Thread(target=post, args=(p,)) for p in payloads]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        names = sorted(r["uploaded"][0]["saved_as"] for r in results)
        assert len(set(names)) == 6
        assert sorted((inbox / n).read_bytes() for n in names) == sorted(payloads)


class TestInboxUnavailable:
    def test_unset_inbox_is_a_503(self, client, setting):
        setting.value = None

        response = client.post("/api/uploads", files=[_png()])

        assert response.status_code == 503
        assert "PATH_INGESTION_SOURCE" in response.json()["detail"]

    def test_missing_inbox_directory_is_a_503(self, client, setting, inbox):
        setting.value = str(inbox / "nope")

        assert client.post("/api/uploads", files=[_png()]).status_code == 503
        assert not (inbox / "nope").exists()

    def test_unwritable_inbox_is_a_503(self, client, inbox):
        (inbox / ".uploading").write_bytes(b"i am a file, not a directory")

        assert client.post("/api/uploads", files=[_png()]).status_code == 503

    def test_does_not_fall_back_to_incoming(self, client, setting, inbox):
        setting.value = None

        client.post("/api/uploads", files=[_png()])

        assert not (inbox.parent / "incoming").exists()


class TestUploadValidationFailures:
    """Per-file validation failures land in `failed`, not an HTTP error."""

    def test_unsupported_mime_type(self, client, inbox):
        response = client.post("/api/uploads", files=[("files", ("doc.pdf", b"%PDF-1.4", "application/pdf"))])

        data = response.json()
        assert response.status_code == 200
        assert data["total_accepted"] == 0
        assert data["total_failed"] == 1
        assert "Unsupported file type" in data["failed"][0]["reason"]
        assert _listing(inbox) == []

    def test_unsupported_extension(self, client):
        data = client.post("/api/uploads", files=[("files", ("meme.txt", PNG_MAGIC, "image/png"))]).json()

        assert data["total_failed"] == 1
        assert "Unsupported file extension" in data["failed"][0]["reason"]

    def test_file_too_large(self, client, inbox):
        with patch.object(uploads_module, "MAX_FILE_SIZE", 10):
            data = client.post("/api/uploads", files=[_png()]).json()

        assert data["total_failed"] == 1
        assert "File too large" in data["failed"][0]["reason"]
        assert _listing(inbox) == []

    def test_invalid_magic_bytes(self, client):
        data = client.post("/api/uploads", files=[_png(data=b"not a real png" + b"\x00" * 16)]).json()

        assert data["total_failed"] == 1
        assert "does not match a supported image format" in data["failed"][0]["reason"]

    def test_mixed_batch_partial_success(self, client, inbox):
        data = client.post("/api/uploads", files=[
            _png("good.png"), _png("bad.png", b"not a real png" + b"\x00" * 16)]).json()

        assert data["total_accepted"] == 1
        assert data["total_failed"] == 1
        assert data["uploaded"][0]["original_filename"] == "good.png"
        assert data["failed"][0]["original_filename"] == "bad.png"
        assert _listing(inbox) == ["good.png"]

    def test_all_rejected_does_not_need_the_inbox(self, client, setting):
        setting.value = None

        response = client.post("/api/uploads", files=[("files", ("doc.pdf", b"%PDF", "application/pdf"))])

        assert response.status_code == 200


class TestUploadLimits:
    def test_too_many_files_rejected(self, client, inbox):
        files = [_png(f"{i}.png") for i in range(51)]

        response = client.post("/api/uploads", files=files)

        assert response.status_code == 422
        assert "Too many files" in response.json()["detail"]
        assert _listing(inbox) == []

    def test_rate_limited(self, client, inbox):
        client.mock_limiter.is_allowed = AsyncMock(return_value=False)

        response = client.post("/api/uploads", files=[_png()])

        assert response.status_code == 429
        assert _listing(inbox) == []
