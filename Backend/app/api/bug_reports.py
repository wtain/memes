import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import File, HTTPException, Request, UploadFile
from Backend.app.stage_policy import make_router
from pydantic import BaseModel

from Backend.app.services.abuse import bug_report_dir_caps, enforce
from Backend.app.services.image_store import bug_report_dir_has_room, save_bug_report

router = make_router(prefix="/bug-reports", tags=["bug-reports"])

ALLOWED_EXTENSIONS = {".txt", ".log"}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
READ_CHUNK = 64 * 1024


class BugReportResponse(BaseModel):
    original_filename: str
    saved_as: str
    size_bytes: int


@router.post("", response_model=BugReportResponse)
async def upload_bug_report(request: Request, file: UploadFile = File(...)):
    enforce("bug_reports", request)   # abuse.bug_reports_per_min: 429 over the limit
    max_files, max_total_bytes = bug_report_dir_caps()
    if not bug_report_dir_has_room(0, max_files, max_total_bytes):
        raise HTTPException(status_code=507, detail="Bug report storage is full")

    original_filename = file.filename or "unknown"

    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"Unsupported file extension: {ext}")

    # Read in chunks and stop one byte past the cap: an oversize upload is never held whole in memory.
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(READ_CHUNK):
        size += len(chunk)
        if size > MAX_FILE_SIZE:
            raise HTTPException(status_code=422, detail=f"File too large (max {MAX_FILE_SIZE} bytes)")
        chunks.append(chunk)
    data = b"".join(chunks)

    if len(data) == 0:
        raise HTTPException(status_code=422, detail="Empty file")

    if not bug_report_dir_has_room(len(data), max_files, max_total_bytes):
        raise HTTPException(status_code=507, detail="Bug report storage is full")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    saved_as = f"{timestamp}_{uuid.uuid4()}{ext}"
    save_bug_report(saved_as, data)

    return BugReportResponse(
        original_filename=original_filename,
        saved_as=saved_as,
        size_bytes=len(data),
    )