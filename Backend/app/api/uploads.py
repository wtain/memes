import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from Backend.app.services.image_store import INCOMING_DIR

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
async def upload_images(files: list[UploadFile] = File(...)):
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