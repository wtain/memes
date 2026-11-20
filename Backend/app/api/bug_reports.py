import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import File, HTTPException, Request, UploadFile
from Backend.app.stage_policy import make_router
from pydantic import BaseModel

from Backend.app.services.abuse import bug_report_dir_caps, enforce
from Backend.app.services.image_store import bug_report_dir_has_room, save_bug_report

logger = logging.getLogger(__name__)
router = make_router(prefix="/bug-reports", tags=["bug-reports"])

ALLOWED_EXTENSIONS = {".txt", ".log"}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
READ_CHUNK = 64 * 1024


class BugReportResponse(BaseModel):
    original_filename: str
    saved_as: str
    size_bytes: int


def _receipt(outcome: str, size: int) -> None:
    """One line per upload: outcome and size only (the original file name is client text and the content is private)."""
    logger.info("bug report received outcome=%s size_bytes=%d", outcome, size)


@router.post("", response_model=BugReportResponse)
async def upload_bug_report(request: Request, file: UploadFile = File(...)):
    # abuse.bug_reports_per_min: BodyLimitMiddleware already charged and refused (429) before the body was read; this call only
    # charges when the endpoint is mounted without that middleware, and never a second token.
    enforce("bug_reports", request)
    max_files, max_total_bytes = bug_report_dir_caps()
    if not bug_report_dir_has_room(0, max_files, max_total_bytes):
        _receipt("storage_full", 0)
        raise HTTPException(status_code=507, detail="Bug report storage is full")

    original_filename = file.filename or "unknown"

    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        _receipt("bad_extension", 0)
        raise HTTPException(status_code=422, detail=f"Unsupported file extension: {ext}")

    # Read in chunks and stop one byte past the cap: an oversize upload is never held whole in memory.
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(READ_CHUNK):
        size += len(chunk)
        if size > MAX_FILE_SIZE:
            _receipt("too_large", size)
            raise HTTPException(status_code=422, detail=f"File too large (max {MAX_FILE_SIZE} bytes)")
        chunks.append(chunk)
    data = b"".join(chunks)

    if len(data) == 0:
        _receipt("empty", 0)
        raise HTTPException(status_code=422, detail="Empty file")

    # No await between this final cap check and the write below, so within one worker's event loop the check and the write cannot
    # interleave with another request's: the caps do not overshoot per process. Across workers they can by a few files (accepted: the
    # caps are a disk-fill bound, not a quota; effective limit workers x the configured values, as for the rate limits).
    if not bug_report_dir_has_room(len(data), max_files, max_total_bytes):
        _receipt("storage_full", len(data))
        raise HTTPException(status_code=507, detail="Bug report storage is full")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    saved_as = f"{timestamp}_{uuid.uuid4()}{ext}"
    save_bug_report(saved_as, data)
    _receipt("saved", len(data))

    return BugReportResponse(
        original_filename=original_filename,
        saved_as=saved_as,
        size_bytes=len(data),
    )