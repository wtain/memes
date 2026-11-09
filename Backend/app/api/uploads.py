import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.inbox_upload_service import (
    DuplicateCheckUnavailableError, InboxOutOfSpaceError, InboxUnavailableError, InboxUploadService, InboxWriteError,
    UploadCandidate, validate_image_content)
from Backend.app.services.rate_limit import upload_limiter
from Storage.db import get_async_db

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
READ_CHUNK_SIZE = 1024 * 1024  # an upload is read in chunks of this size and never past MAX_FILE_SIZE + 1

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


async def _read_bounded(file: UploadFile, limit: int) -> bytes | None:
    """The whole upload, or None as soon as it is known to be longer than `limit` (nothing past limit + 1 is read)."""
    buffer = bytearray()
    while True:
        chunk = await file.read(min(READ_CHUNK_SIZE, limit + 1 - len(buffer)))
        if not chunk:
            return bytes(buffer)
        buffer += chunk
        if len(buffer) > limit:
            return None


class UploadedFile(BaseModel):
    index: int = Field(description="0-based position of the file in the request's `files` parts.")
    original_filename: str
    saved_as: str
    size_bytes: int
    content_type: str
    status: str = Field(
        description='"accepted": written to the ingestion inbox. "duplicate": the same content is already in the '
                    'library (any status) or the inbox, nothing was written.',
    )


class FailedFile(BaseModel):
    index: int = Field(description="0-based position of the file in the request's `files` parts.")
    original_filename: str
    reason: str


class UploadResponse(BaseModel):
    uploaded: list[UploadedFile]
    failed: list[FailedFile]
    total_accepted: int
    total_failed: int


async def get_upload_image_repository(db: AsyncSession = Depends(get_async_db)):
    yield ImageRepository(db)


async def get_inbox_upload_service(repo: ImageRepository = Depends(get_upload_image_repository)):
    return InboxUploadService(repo)


@router.post("", response_model=UploadResponse)
async def upload_images(
    request: Request,
    files: list[UploadFile] = File(...),
    service: InboxUploadService = Depends(get_inbox_upload_service),
):
    client_ip = request.client.host if request.client else "unknown"
    if not await upload_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Too many upload requests — try again in a minute")

    if len(files) > MAX_FILES:
        raise HTTPException(status_code=422, detail=f"Too many files: max {MAX_FILES} per request")

    failed: list[FailedFile] = []
    candidates: list[UploadCandidate] = []
    content_types: list[str] = []

    for index, file in enumerate(files):
        original_filename = file.filename or "unknown"
        content_type = file.content_type or ""

        def reject(reason: str) -> None:
            failed.append(FailedFile(index=index, original_filename=original_filename, reason=reason))

        if content_type not in ALLOWED_MIME_TYPES:
            reject(f"Unsupported file type: {content_type}")
            continue

        ext = Path(original_filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            reject(f"Unsupported file extension: {ext}")
            continue

        data = await _read_bounded(file, MAX_FILE_SIZE)
        if data is None:
            reject(f"File too large: more than {MAX_FILE_SIZE} bytes (max {MAX_FILE_SIZE})")
            continue

        if not _has_valid_image_magic(data):
            reject("File content does not match a supported image format")
            continue

        content_problem = await asyncio.to_thread(validate_image_content, data, ext)
        if content_problem is not None:
            reject(content_problem)
            continue

        candidates.append(UploadCandidate(original_filename=original_filename, ext=ext, data=data, index=index))
        content_types.append(content_type)

    try:
        stored = await service.store(candidates)
    except InboxOutOfSpaceError as error:
        raise HTTPException(status_code=507, detail=str(error)) from None
    except (InboxUnavailableError, DuplicateCheckUnavailableError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except InboxWriteError as error:
        raise HTTPException(status_code=500, detail=str(error)) from None

    uploaded = [
        UploadedFile(
            index=item.candidate.index,
            original_filename=item.candidate.original_filename,
            saved_as=item.saved_as,
            size_bytes=len(item.candidate.data),
            content_type=content_type,
            status=item.status,
        )
        for item, content_type in zip(stored, content_types)
    ]

    return UploadResponse(
        uploaded=uploaded,
        failed=failed,
        total_accepted=len(uploaded),
        total_failed=len(failed),
    )
