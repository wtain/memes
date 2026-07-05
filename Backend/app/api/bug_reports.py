import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from Backend.app.services.image_store import save_bug_report
from Backend.app.services.rate_limit import bug_report_limiter

router = APIRouter(prefix="/bug-reports", tags=["bug-reports"])

ALLOWED_EXTENSIONS = {".txt", ".log"}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


class BugReportResponse(BaseModel):
    original_filename: str
    saved_as: str
    size_bytes: int


@router.post("", response_model=BugReportResponse)
async def upload_bug_report(request: Request, file: UploadFile = File(...)):
    client_ip = request.client.host if request.client else "unknown"
    if not await bug_report_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Too many bug report requests — try again in a minute")

    original_filename = file.filename or "unknown"

    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"Unsupported file extension: {ext}")

    data = await file.read()

    if len(data) == 0:
        raise HTTPException(status_code=422, detail="Empty file")

    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=422, detail=f"File too large: {len(data)} bytes (max {MAX_FILE_SIZE})")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    saved_as = f"{timestamp}_{uuid.uuid4()}{ext}"
    save_bug_report(saved_as, data)

    return BugReportResponse(
        original_filename=original_filename,
        saved_as=saved_as,
        size_bytes=len(data),
    )