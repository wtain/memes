import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from Backend.app.services.image_store import INCOMING_DIR
from Backend.app.services.rate_limit import upload_limiter

router = APIRouter(prefix="/uploads", tags=["uploads"])

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
}

ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"
}

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_FILES = 50

# (magic_bytes, byte_offset) pairs covering all types in ALLOWED_MIME_TYPES
_MAGIC_SIGNATURES: list[tuple[bytes, int]] = [
    (b"\xff\xd8\xff", 0),        # JPEG
    (b"\x89PNG\r\n\x1a\n", 0),  # PNG
    (b"GIF87a", 0),               # GIF87
    (b"GIF89a", 0),               # GIF89
    (b"RIFF", 0),                 # WebP (verified below with offset-8 check)
    (b"BM", 0),                   # BMP
    (b"II\x2a\x00", 0),          # TIFF little-endian
    (b"MM\x00\x2a", 0),          # TIFF big-endian
]


def _has_valid_image_magic(data: bytes) -> bool:
    for magic, offset in _MAGIC_SIGNATURES:
        if data[offset: offset + len(magic)] == magic:
            if magic == b"RIFF":
                return data[8:12] == b"WEBP"
            return True
    return False


class UploadedFile(BaseModel):
    original_filename: str
    saved_as: str
    size_bytes: int
    content_type: str
    status: str = "ok"


class FailedFile(BaseModel):
    original_filename: str
    reason: str


class UploadResponse(BaseModel):
    uploaded: list[UploadedFile]
    failed: list[FailedFile]
    total_accepted: int
    total_failed: int


@router.post("", response_model=UploadResponse)
async def upload_images(request: Request, files: list[UploadFile] = File(...)):
    client_ip = request.client.host if request.client else "unknown"
    if not await upload_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Too many upload requests — try again in a minute")

    if len(files) > MAX_FILES:
        raise HTTPException(status_code=422, detail=f"Too many files: max {MAX_FILES} per request")

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    uploaded: list[UploadedFile] = []
    failed: list[FailedFile] = []

    for file in files:
        original_filename = file.filename or "unknown"
        content_type = file.content_type or ""

        if content_type not in ALLOWED_MIME_TYPES:
            failed.append(FailedFile(
                original_filename=original_filename,
                reason=f"Unsupported file type: {content_type}",
            ))
            continue

        ext = Path(original_filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            failed.append(FailedFile(
                original_filename=original_filename,
                reason=f"Unsupported file extension: {ext}",
            ))
            continue

        data = await file.read()

        if len(data) > MAX_FILE_SIZE:
            failed.append(FailedFile(
                original_filename=original_filename,
                reason=f"File too large: {len(data)} bytes (max {MAX_FILE_SIZE})",
            ))
            continue

        if not _has_valid_image_magic(data):
            failed.append(FailedFile(
                original_filename=original_filename,
                reason="File content does not match a supported image format",
            ))
            continue

        saved_as = f"{uuid.uuid4()}{ext}"
        (INCOMING_DIR / saved_as).write_bytes(data)

        uploaded.append(UploadedFile(
            original_filename=original_filename,
            saved_as=saved_as,
            size_bytes=len(data),
            content_type=content_type,
        ))

    return UploadResponse(
        uploaded=uploaded,
        failed=failed,
        total_accepted=len(uploaded),
        total_failed=len(failed),
    )