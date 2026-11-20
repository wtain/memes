import logging
import os
import shutil
from pathlib import Path, PureWindowsPath
from typing import List

from Backend.app.observability.logfiles import log_sampled
from config.settings import settings

logger = logging.getLogger(__name__)

_images_dir = settings.get('BASE_PATH')
if not _images_dir:
    raise RuntimeError("BASE_PATH environment variable is required but not set")
_IMAGES_DIR = Path(_images_dir)

_bug_reports_raw = settings.get('BUG_REPORTS_PATH')
_BUG_REPORTS_DIR = Path(_bug_reports_raw) if _bug_reports_raw else _IMAGES_DIR / "bug_reports"

# Ingestion review rejects -- single flat directory, deliberately distinct from
# PATH_INGESTION_SOURCE/duplicates/ (stage 1's hash-dedup rejects, which never became a DB
# row). See docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md Decision #6.
_REJECTED_DIR = _IMAGES_DIR / "rejected"


class UnsafeImagePath(ValueError):
    """A file name that does not name a plain file directly inside the directory it is looked up in."""


def _contained(directory: Path, filename: str) -> Path:
    """`directory / filename`, after proving that it is a direct child of `directory`.

    Containment (P5a-6, decision 19): the file names come from the database, which on prod is filled from dev, and
    the images directory is filled by file transfer, so neither is trusted. A name is refused when it is empty,
    holds a NUL or a path separator, is absolute or drive-qualified, is `.` or `..`, resolves (symlinks followed)
    outside the directory or to anything but a direct child, is itself a symlink (links are refused even when they
    point inside: regular files only) or is a directory. Raises UnsafeImagePath.
    """
    if not isinstance(filename, str) or not filename or filename in (".", ".."):
        raise UnsafeImagePath("empty or relative-only file name")
    if "\x00" in filename or "/" in filename or "\\" in filename:
        raise UnsafeImagePath("file name holds a NUL or a path separator")
    if os.path.isabs(filename) or (os.name == "nt" and PureWindowsPath(filename).drive):
        raise UnsafeImagePath("absolute file name")
    candidate = directory / filename
    try:
        if candidate.is_symlink():
            raise UnsafeImagePath("file name is a symbolic link")
        resolved = candidate.resolve(strict=False)
        base = directory.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as error:
        raise UnsafeImagePath("file name cannot be resolved") from error
    if resolved.parent != base:
        raise UnsafeImagePath("file name resolves outside its directory")
    if candidate.is_dir():
        raise UnsafeImagePath("file name is a directory")
    return candidate


def get_image_path(filename: str) -> Path:
    """Path of an image file; raises UnsafeImagePath for a name that is not a plain file inside the images directory."""
    return _contained(_IMAGES_DIR, filename)


def list_images() -> List[str]:
    if not _IMAGES_DIR.exists():
        return []
    return [p.name for p in _IMAGES_DIR.iterdir() if p.is_file()]


def image_exists(filename: str) -> bool:
    try:
        return _contained(_IMAGES_DIR, filename).is_file()
    except UnsafeImagePath as refusal:
        log_sampled(logger, "image_name_refused", "image file name refused (%s)", type(refusal).__name__)
        return False


def bug_report_usage() -> tuple[int, int]:
    """(number of regular files, their total size in bytes) in the bug-reports directory; (0, 0) when it does not exist yet."""
    if not _BUG_REPORTS_DIR.is_dir():
        return 0, 0
    count = total = 0
    with os.scandir(_BUG_REPORTS_DIR) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                count += 1
                total += entry.stat(follow_symlinks=False).st_size
    return count, total


def bug_report_dir_has_room(incoming_bytes: int, max_files: int, max_total_bytes: int) -> bool:
    """False when storing one more file of `incoming_bytes` would pass `max_files` or `max_total_bytes` (0 = no cap)."""
    if not max_files and not max_total_bytes:
        return True
    count, total = bug_report_usage()
    if max_files and count + 1 > max_files:
        return False
    if max_total_bytes and total + incoming_bytes > max_total_bytes:
        return False
    return True


def save_bug_report(filename: str, data: bytes) -> None:
    target = _contained(_BUG_REPORTS_DIR, filename)
    _BUG_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def move_to_rejected(filename: str) -> None:
    src = _contained(_IMAGES_DIR, filename)
    dest = _contained(_REJECTED_DIR, filename)
    _REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), str(dest))


def move_from_rejected(filename: str) -> None:
    src = _contained(_REJECTED_DIR, filename)
    dest = _contained(_IMAGES_DIR, filename)
    if src.exists():
        shutil.move(str(src), str(dest))
