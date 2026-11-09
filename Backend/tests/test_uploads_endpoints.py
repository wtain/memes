"""
Tests for the uploads endpoint (POST /api/uploads), which writes into the ingestion inbox
(PATH_INGESTION_SOURCE). Design: docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-b1-inbox-upload-design.md

Real file I/O goes to a tmp_path inbox; the library duplicate lookup is a fake repository.
"""
import hashlib
import logging
import os
import subprocess
import threading
import time
import unicodedata
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from starlette.datastructures import UploadFile as StarletteUploadFile

from Backend.app.api import uploads as uploads_module
from Backend.app.api.uploads import get_upload_image_repository, router as uploads_router
from Backend.app.services import inbox_upload_service as service_module
from batch.ingest_hash_dedup import hash_incoming_files
from batch.utils.file_hash import sha256_file

app = FastAPI()
app.include_router(uploads_router, prefix="/api")

MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp", "BMP": "image/bmp",
        "TIFF": "image/tiff"}


def _image(fmt="PNG", color=(255, 0, 0), size=(4, 4)):
    """Real, tiny, valid image bytes (the upload gate opens them with Pillow)."""
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


PNG_MAGIC = _image("PNG", (255, 0, 0))
PNG_OTHER = _image("PNG", (0, 0, 255))
JPEG_MAGIC = _image("JPEG", (0, 255, 0))
# BMP is uncompressed, so two colours of the same size give two different files of exactly the same length.
BMP_A = _image("BMP", (255, 0, 0))
BMP_B = _image("BMP", (0, 0, 255))
assert len(BMP_A) == len(BMP_B) and BMP_A != BMP_B


def _png(name="meme.png", data=PNG_MAGIC):
    return ("files", (name, data, "image/png"))


def _bmp(name, data):
    return ("files", (name, data, "image/bmp"))


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
    stub = SimpleNamespace(value=str(inbox), base=None)
    values = {"PATH_INGESTION_SOURCE": lambda: stub.value, "BASE_PATH": lambda: stub.base}
    stub.get = lambda key, default=None: values[key]() if key in values else default
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
        assert entry["index"] == 0
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
        second = client.post("/api/uploads", files=[_png(data=_image("PNG", (1, 2, 3)))]).json()

        assert first["uploaded"][0]["saved_as"] == "meme_1.png"
        assert second["uploaded"][0]["saved_as"] == "meme_2.png"
        assert (inbox / "meme.png").read_bytes() == PNG_OTHER

    def test_the_service_hash_equals_the_batch_algorithm(self, client, inbox, library):
        client.post("/api/uploads", files=[_png("x.png")])

        asked = library.existing_content_hashes.await_args.args[0]
        assert asked == {sha256_file(str(inbox / "x.png"))}


class TestDuplicates:
    def test_duplicate_of_a_file_in_the_inbox_writes_nothing(self, client, inbox):
        (inbox / "existing.png").write_bytes(PNG_MAGIC)

        data = client.post("/api/uploads", files=[_png("again.png")]).json()

        entry = data["uploaded"][0]
        assert entry["status"] == "duplicate"
        assert entry["saved_as"] == "again.png"  # never the name of an existing inbox file
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
        assert data["uploaded"][1]["saved_as"] == "b.png"
        assert _listing(inbox) == ["a.png"]

    def test_same_size_different_content_is_not_a_duplicate(self, client, inbox):
        (inbox / "other.bmp").write_bytes(BMP_B)

        data = client.post("/api/uploads", files=[_bmp("new.bmp", BMP_A)]).json()

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
        real_publish = service_module._publish_once

        def spying_publish(src, dst):
            seen["inbox_before"] = _listing(inbox)
            seen["hashed_before"] = hash_incoming_files(str(inbox))
            seen["src_parent"] = os.path.dirname(src)
            return real_publish(src, dst)

        with patch.object(service_module, "_publish_once", spying_publish):
            client.post("/api/uploads", files=[_png("a.png")])

        assert seen["inbox_before"] == []
        assert seen["hashed_before"] == {}
        assert seen["src_parent"] == str(inbox / ".uploading")
        assert _listing(inbox) == ["a.png"]
        assert hash_incoming_files(str(inbox)) == {hashlib.sha256(PNG_MAGIC).hexdigest(): ["a.png"]}

    def test_write_error_is_a_500_and_leaves_no_staged_file(self, client, inbox):
        with patch.object(service_module, "_publish_once", side_effect=OSError("disk full")):
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


class TestNoClobberPublish:
    def test_a_file_created_between_the_name_check_and_the_publish_is_never_overwritten(self, client, inbox):
        """Another process (a person, ingest_abort) takes the chosen name after the exists() check. With a plain
        os.replace the foreign file would be lost; the no-clobber publish retries the next suffix."""
        real_publish = service_module._publish_once
        calls = []

        def racing_publish(staged, final):
            if not calls:
                (inbox / "meme.png").write_bytes(b"someone else's file")
            calls.append(final.name)
            return real_publish(staged, final)

        with patch.object(service_module, "_publish_once", racing_publish):
            data = client.post("/api/uploads", files=[_png("meme.png")]).json()

        assert calls == ["meme.png", "meme_1.png"]
        assert data["uploaded"][0]["saved_as"] == "meme_1.png"
        assert (inbox / "meme.png").read_bytes() == b"someone else's file"
        assert (inbox / "meme_1.png").read_bytes() == PNG_MAGIC
        assert list((inbox / ".uploading").iterdir()) == []

    def test_concurrent_requests_with_the_same_name_never_overwrite_even_with_a_wide_race_window(self, client, inbox):
        real_publish = service_module._publish_once

        def slow_publish(staged, final):
            time.sleep(0.02)  # widens the window between the name check and the publish
            return real_publish(staged, final)

        results = []
        payloads = [_image("PNG", (i, 0, 0)) for i in range(6)]
        threads = [threading.Thread(target=lambda p=p: results.append(
            client.post("/api/uploads", files=[_png("same.png", p)]).json())) for p in payloads]
        with patch.object(service_module, "_publish_once", slow_publish):
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        names = sorted(r["uploaded"][0]["saved_as"] for r in results)
        assert len(set(names)) == 6
        assert sorted((inbox / n).read_bytes() for n in names) == sorted(payloads)

    def test_the_same_new_content_in_two_concurrent_requests_is_written_once(self, client, inbox):
        real_publish = service_module._publish_once

        def slow_publish(staged, final):
            time.sleep(0.05)
            return real_publish(staged, final)

        results = []
        threads = [threading.Thread(target=lambda n=n: results.append(
            client.post("/api/uploads", files=[_png(n)]).json())) for n in ("a.png", "b.png")]
        with patch.object(service_module, "_publish_once", slow_publish):
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert sorted(r["uploaded"][0]["status"] for r in results) == ["accepted", "duplicate"]
        assert len(_listing(inbox)) == 1

    def test_case_insensitive_names_collide_on_a_case_insensitive_volume(self, client, inbox):
        (inbox / "Probe.tmp").write_bytes(b"x")
        if not (inbox / "probe.tmp").exists():
            pytest.skip("case-sensitive filesystem")
        (inbox / "A.jpg").write_bytes(b"other")

        data = client.post("/api/uploads", files=[("files", ("a.JPG", JPEG_MAGIC, "image/jpeg"))]).json()

        assert data["uploaded"][0]["saved_as"] == "a_1.jpg"
        assert (inbox / "A.jpg").read_bytes() == b"other"

    def test_nfd_and_nfc_names_are_the_same_name(self, client, inbox):
        nfc, nfd = unicodedata.normalize("NFC", "й.png"), unicodedata.normalize("NFD", "й.png")
        assert nfc != nfd

        first = client.post("/api/uploads", files=[_png(nfc, PNG_MAGIC)]).json()
        second = client.post("/api/uploads", files=[_png(nfd, PNG_OTHER)]).json()

        assert first["uploaded"][0]["saved_as"] == nfc
        assert second["uploaded"][0]["saved_as"] == unicodedata.normalize("NFC", "й_1.png")
        assert len(_listing(inbox)) == 2


class TestContentGate:
    def test_bmp_magic_with_a_script_body_is_rejected(self, client, inbox):
        data = client.post("/api/uploads", files=[_bmp("p.bmp", b"BM<script>alert(1)</script>")]).json()

        assert data["total_failed"] == 1
        assert "not a valid image" in data["failed"][0]["reason"]
        assert _listing(inbox) == []

    def test_gif_bytes_named_jpg_are_rejected_not_renamed(self, client, inbox):
        gif = _image("GIF")

        data = client.post("/api/uploads", files=[("files", ("gif.jpg", gif, "image/jpeg"))]).json()

        assert data["total_failed"] == 1
        assert "GIF" in data["failed"][0]["reason"] and ".jpg" in data["failed"][0]["reason"]
        assert _listing(inbox) == []

    @pytest.mark.parametrize("fmt, ext", [("JPEG", ".jpg"), ("JPEG", ".jpeg"), ("PNG", ".png"), ("GIF", ".gif"),
                                          ("WEBP", ".webp"), ("BMP", ".bmp"), ("TIFF", ".tif"), ("TIFF", ".tiff")])
    def test_a_real_image_of_each_format_is_accepted(self, client, inbox, fmt, ext):
        data = client.post("/api/uploads", files=[("files", (f"x{ext}", _image(fmt), MIME[fmt]))]).json()

        assert data["total_accepted"] == 1, data
        assert _listing(inbox) == [f"x{ext}"]

    def test_pixel_cap_rejects_a_big_image(self, client, inbox):
        with patch.object(service_module, "MAX_IMAGE_PIXELS", 10):
            data = client.post("/api/uploads", files=[_png()]).json()

        assert data["total_failed"] == 1
        assert "too large" in data["failed"][0]["reason"]
        assert _listing(inbox) == []

    def test_a_real_decompression_bomb_is_rejected(self, client, inbox):
        buffer = BytesIO()
        Image.new("1", (20000, 20000)).save(buffer, "PNG")  # 400 million pixels, a few KB on the wire
        assert len(buffer.getvalue()) < 1_000_000

        data = client.post("/api/uploads", files=[_png("bomb.png", buffer.getvalue())]).json()

        assert data["total_failed"] == 1
        assert "too large" in data["failed"][0]["reason"]

    def test_gate_does_not_run_on_the_event_loop(self, client, inbox):
        threads = []
        real = uploads_module.validate_image_content

        def spy(data, ext):
            threads.append(threading.current_thread())
            return real(data, ext)

        with patch.object(uploads_module, "validate_image_content", spy):
            client.post("/api/uploads", files=[_png()])

        assert threads and all(t is not threading.main_thread() for t in threads)


class TestResourceLimits:
    def test_an_upload_is_read_in_bounded_chunks_and_never_past_the_limit_plus_one(self, client, inbox):
        returned = []
        real_read = StarletteUploadFile.read

        async def spy(self, size=-1):
            chunk = await real_read(self, size)
            returned.append(len(chunk))
            return chunk

        with patch.object(uploads_module, "MAX_FILE_SIZE", 10), patch.object(uploads_module, "READ_CHUNK_SIZE", 4), \
                patch.object(StarletteUploadFile, "read", spy):
            data = client.post("/api/uploads", files=[_png()]).json()

        assert "File too large" in data["failed"][0]["reason"]
        assert sum(returned) == 11 and max(returned) <= 4

    def test_a_file_of_exactly_the_limit_is_read_fully(self, client, inbox):
        with patch.object(uploads_module, "MAX_FILE_SIZE", len(PNG_MAGIC)), \
                patch.object(uploads_module, "READ_CHUNK_SIZE", 7):
            data = client.post("/api/uploads", files=[_png()]).json()

        assert data["total_accepted"] == 1

    def test_low_free_space_is_a_507_and_writes_nothing(self, client, inbox):
        usage = SimpleNamespace(total=10, used=10, free=service_module.MIN_FREE_BYTES + len(PNG_MAGIC) - 1)

        with patch.object(service_module.shutil, "disk_usage", return_value=usage):
            response = client.post("/api/uploads", files=[_png()])

        assert response.status_code == 507
        assert str(inbox) not in response.text
        assert _listing(inbox) == []

    def test_enough_free_space_passes(self, client, inbox):
        usage = SimpleNamespace(total=10, used=10, free=service_module.MIN_FREE_BYTES + len(PNG_MAGIC))

        with patch.object(service_module.shutil, "disk_usage", return_value=usage):
            assert client.post("/api/uploads", files=[_png()]).status_code == 200


class TestInboxDuplicateScan:
    def test_the_inbox_is_listed_once_per_request_not_once_per_file(self, client, inbox):
        real_scandir = os.scandir
        listed = []

        def counting(path):
            listed.append(str(path))
            return real_scandir(path)

        files = [_png(f"{i}.png", _image("PNG", (i, 0, 0))) for i in range(5)]
        with patch.object(service_module.os, "scandir", counting):
            client.post("/api/uploads", files=files)

        assert listed.count(str(inbox)) == 1

    def test_only_same_size_files_are_hashed_and_each_only_once_per_request(self, client, inbox):
        for i in range(3):
            (inbox / f"old{i}.bmp").write_bytes(_image("BMP", (0, 0, i + 1)))
        (inbox / "different_size.bmp").write_bytes(_image("BMP", (0, 0, 9), size=(8, 8)))
        hashed = []
        real = service_module.sha256_file

        def counting(path):
            hashed.append(os.path.basename(path))
            return real(path)

        uploads = [_bmp(f"new{i}.bmp", _image("BMP", (i + 10, 0, 0))) for i in range(3)]
        with patch.object(service_module, "sha256_file", counting):
            data = client.post("/api/uploads", files=uploads).json()

        assert data["total_accepted"] == 3
        assert sorted(hashed) == ["old0.bmp", "old1.bmp", "old2.bmp"]

    def test_hashing_happens_outside_the_global_lock(self, client, inbox):
        (inbox / "old.bmp").write_bytes(BMP_B)
        lock_states = []
        real = service_module.sha256_file

        def spy(path):
            lock_states.append(service_module._publish_lock.locked())
            return real(path)

        with patch.object(service_module, "sha256_file", spy):
            client.post("/api/uploads", files=[_bmp("new.bmp", BMP_A)])

        assert lock_states == [False]

    def test_a_too_big_inbox_fails_closed_with_a_503(self, client, inbox):
        for i in range(4):
            (inbox / f"f{i}.png").write_bytes(b"x")

        with patch.object(service_module, "MAX_INBOX_ENTRIES", 3):
            response = client.post("/api/uploads", files=[_png()])

        assert response.status_code == 503
        assert "full" in response.json()["detail"]
        assert _listing(inbox) == [f"f{i}.png" for i in range(4)]

    def test_a_library_duplicate_does_not_need_the_inbox_scan(self, client, inbox, library):
        library.hashes.add(hashlib.sha256(PNG_MAGIC).hexdigest())
        for i in range(4):
            (inbox / f"f{i}.png").write_bytes(b"x")

        with patch.object(service_module, "MAX_INBOX_ENTRIES", 3):
            data = client.post("/api/uploads", files=[_png()]).json()

        assert data["uploaded"][0]["status"] == "duplicate"


class TestInboxConfiguration:
    def test_relative_inbox_path_is_a_503(self, client, setting):
        setting.value = "inbox"

        response = client.post("/api/uploads", files=[_png()])

        assert response.status_code == 503
        assert "absolute" in response.json()["detail"]

    def test_inbox_equal_to_the_library_is_a_503(self, client, setting, inbox):
        setting.base = str(inbox)

        assert client.post("/api/uploads", files=[_png()]).status_code == 503
        assert _listing(inbox) == []

    def test_inbox_containing_the_library_is_a_503(self, client, setting, inbox):
        (inbox / "library").mkdir()
        setting.base = str(inbox / "library")

        assert client.post("/api/uploads", files=[_png()]).status_code == 503

    def test_inbox_as_a_subdirectory_of_the_library_is_valid(self, client, setting, inbox):
        setting.base = str(inbox.parent)

        assert client.post("/api/uploads", files=[_png()]).status_code == 200

    def test_staging_directory_that_is_a_symlink_is_a_503(self, client, inbox, tmp_path):
        target = tmp_path / "elsewhere"
        target.mkdir()
        try:
            os.symlink(target, inbox / ".uploading", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not permitted here")

        assert client.post("/api/uploads", files=[_png()]).status_code == 503
        assert list(target.iterdir()) == []

    @pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
    def test_staging_directory_that_is_a_junction_is_a_503(self, client, inbox, tmp_path):
        target = tmp_path / "elsewhere"
        target.mkdir()
        subprocess.run(["cmd", "/c", "mklink", "/J", str(inbox / ".uploading"), str(target)],
                       check=True, capture_output=True)
        try:
            assert client.post("/api/uploads", files=[_png()]).status_code == 503
            assert list(target.iterdir()) == []
        finally:
            os.rmdir(inbox / ".uploading")


class TestResponseIndexAndOrder:
    def test_index_is_the_position_in_the_request_for_uploaded_and_failed(self, client, inbox):
        files = [
            _png("same.png", PNG_MAGIC),                                     # 0 accepted
            ("files", ("same.png", b"junk", "image/png")),                    # 1 failed (same name as 0 and 2)
            _png("same.png", PNG_OTHER),                                     # 2 accepted
            ("files", ("doc.pdf", b"%PDF", "application/pdf")),               # 3 failed
            _png("again.png", PNG_MAGIC),                                    # 4 duplicate of 0
        ]

        data = client.post("/api/uploads", files=files).json()

        assert [(u["index"], u["status"]) for u in data["uploaded"]] == [
            (0, "accepted"), (2, "accepted"), (4, "duplicate")]
        assert [f["index"] for f in data["failed"]] == [1, 3]
        assert data["uploaded"][0]["saved_as"] == "same.png" and data["uploaded"][1]["saved_as"] == "same_1.png"
        assert data["uploaded"][2]["saved_as"] == "again.png"

    def test_original_filename_is_echoed_exactly_as_received(self, client, inbox):
        data = client.post("/api/uploads", files=[_png("  We ird '.png")]).json()

        assert data["uploaded"][0]["original_filename"] == "  We ird '.png"


class TestFailuresAreLogged:
    def test_a_write_error_is_logged_with_the_sanitised_name_and_leaves_earlier_files(self, client, inbox, caplog):
        real_publish = service_module._publish_once
        count = []

        def failing_second(staged, final):
            count.append(final.name)
            if len(count) == 2:
                raise OSError(28, "No space left on device")
            return real_publish(staged, final)

        files = [_png("first.png", PNG_MAGIC), _png("../second.png", PNG_OTHER), _png("third.png", JPEG_MAGIC)]
        with patch.object(service_module, "_publish_once", failing_second), caplog.at_level(logging.WARNING):
            response = client.post("/api/uploads", files=files)

        assert response.status_code == 500
        assert "second.png" in caplog.text and "No space left" in caplog.text
        assert str(inbox) not in response.text
        assert _listing(inbox) == ["first.png"]
        assert list((inbox / ".uploading").iterdir()) == []

    def test_unavailable_inbox_is_logged(self, client, setting, inbox, caplog):
        setting.value = str(inbox / "nope")

        with caplog.at_level(logging.WARNING):
            response = client.post("/api/uploads", files=[_png()])

        assert response.status_code == 503
        assert "nope" in caplog.text
        assert "nope" not in response.text
