"""
Tests for the uploads endpoint.
Endpoint tested:
- upload_images (POST /api/uploads)

Regression coverage: this endpoint previously referenced an undefined name
(INCOMING_DIR) that would raise NameError on every real request -- flake8
was silently broken in CI (see backend-tests.yml) so nothing caught it, and
there was no test for this endpoint at all. test_upload_single_image_success
and test_upload_multiple_images_success exercise the full success path that
would have caught it immediately.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.uploads import router as uploads_router

app = FastAPI()
app.include_router(uploads_router, prefix="/api")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_MAGIC = b"\xff\xd8\xff" + b"\x00" * 16


@pytest.fixture
def client():
    """Test client with the rate limiter patched to always allow, and
    save_incoming patched so no real disk I/O happens."""
    with patch("Backend.app.api.uploads.upload_limiter") as mock_limiter, \
         patch("Backend.app.api.uploads.save_incoming") as mock_save:
        mock_limiter.is_allowed = AsyncMock(return_value=True)
        with TestClient(app) as test_client:
            test_client.mock_limiter = mock_limiter
            test_client.mock_save = mock_save
            yield test_client


class TestUploadSuccess:
    """Success path -- also the regression test for the undefined
    INCOMING_DIR name (would raise NameError before any response is built)."""

    def test_upload_single_image_success(self, client):
        response = client.post(
            "/api/uploads",
            files=[("files", ("meme.png", PNG_MAGIC, "image/png"))],
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_accepted"] == 1
        assert data["total_failed"] == 0
        assert len(data["uploaded"]) == 1
        assert data["uploaded"][0]["original_filename"] == "meme.png"
        assert data["uploaded"][0]["content_type"] == "image/png"
        assert data["uploaded"][0]["size_bytes"] == len(PNG_MAGIC)
        assert data["uploaded"][0]["saved_as"].endswith(".png")
        assert data["failed"] == []

        client.mock_save.assert_called_once()
        saved_filename, saved_data = client.mock_save.call_args.args
        assert saved_filename == data["uploaded"][0]["saved_as"]
        assert saved_data == PNG_MAGIC

    def test_upload_multiple_images_success(self, client):
        response = client.post(
            "/api/uploads",
            files=[
                ("files", ("a.png", PNG_MAGIC, "image/png")),
                ("files", ("b.jpg", JPEG_MAGIC, "image/jpeg")),
            ],
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_accepted"] == 2
        assert data["total_failed"] == 0
        assert client.mock_save.call_count == 2


class TestUploadValidationFailures:
    """Per-file validation failures land in `failed`, not an HTTP error --
    the request as a whole still succeeds (200) with a mixed result."""

    def test_unsupported_mime_type(self, client):
        response = client.post(
            "/api/uploads",
            files=[("files", ("doc.pdf", b"%PDF-1.4", "application/pdf"))],
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_accepted"] == 0
        assert data["total_failed"] == 1
        assert "Unsupported file type" in data["failed"][0]["reason"]
        client.mock_save.assert_not_called()

    def test_unsupported_extension(self, client):
        response = client.post(
            "/api/uploads",
            files=[("files", ("meme.txt", PNG_MAGIC, "image/png"))],
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_accepted"] == 0
        assert data["total_failed"] == 1
        assert "Unsupported file extension" in data["failed"][0]["reason"]

    def test_file_too_large(self, client):
        with patch("Backend.app.api.uploads.MAX_FILE_SIZE", 10):
            response = client.post(
                "/api/uploads",
                files=[("files", ("meme.png", PNG_MAGIC, "image/png"))],
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_accepted"] == 0
        assert data["total_failed"] == 1
        assert "File too large" in data["failed"][0]["reason"]

    def test_invalid_magic_bytes(self, client):
        response = client.post(
            "/api/uploads",
            files=[("files", ("meme.png", b"not a real png" + b"\x00" * 16, "image/png"))],
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_accepted"] == 0
        assert data["total_failed"] == 1
        assert "does not match a supported image format" in data["failed"][0]["reason"]

    def test_mixed_batch_partial_success(self, client):
        response = client.post(
            "/api/uploads",
            files=[
                ("files", ("good.png", PNG_MAGIC, "image/png")),
                ("files", ("bad.png", b"not a real png" + b"\x00" * 16, "image/png")),
            ],
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_accepted"] == 1
        assert data["total_failed"] == 1
        assert data["uploaded"][0]["original_filename"] == "good.png"
        assert data["failed"][0]["original_filename"] == "bad.png"


class TestUploadLimits:
    def test_too_many_files_rejected(self, client):
        files = [("files", (f"{i}.png", PNG_MAGIC, "image/png")) for i in range(51)]

        response = client.post("/api/uploads", files=files)

        assert response.status_code == 422
        assert "Too many files" in response.json()["detail"]
        client.mock_save.assert_not_called()

    def test_rate_limited(self, client):
        client.mock_limiter.is_allowed = AsyncMock(return_value=False)

        response = client.post(
            "/api/uploads",
            files=[("files", ("meme.png", PNG_MAGIC, "image/png"))],
        )

        assert response.status_code == 429
        client.mock_save.assert_not_called()
