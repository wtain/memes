"""Writes uploaded images into the ingestion inbox (PATH_INGESTION_SOURCE).

Design: docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-b1-inbox-upload-design.md
"""
import asyncio
import hashlib
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from batch.utils.file_hash import sha256_file
from config.settings import settings

logger = logging.getLogger(__name__)

STAGING_DIR_NAME = ".uploading"
STALE_STAGING_SECONDS = 3600
MAX_INBOX_ENTRIES_SCANNED = 5000
MAX_COLLISION_SUFFIX = 999

# One backend process per inbox: the name choice, the inbox duplicate check and the final os.replace of
# concurrent requests are serialised here (a second worker process would not share it).
_publish_lock = threading.Lock()

ACCEPTED = "accepted"
DUPLICATE = "duplicate"


class InboxUnavailableError(Exception):
    """PATH_INGESTION_SOURCE is unset, missing or not writable (maps to 503)."""


class DuplicateCheckUnavailableError(Exception):
    """The library duplicate lookup failed (maps to 503)."""


class InboxWriteError(Exception):
    """Writing a file into the inbox failed (maps to 500)."""


@dataclass(frozen=True)
class UploadCandidate:
    original_filename: str
    ext: str  # validated, lowercase, with the dot
    data: bytes


@dataclass(frozen=True)
class StoredFile:
    candidate: UploadCandidate
    saved_as: str
    status: str  # ACCEPTED | DUPLICATE

_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_STEM_BYTES = 200


def _cap_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def sanitize_filename(raw: str | None, ext: str) -> str:
    """Safe inbox file name for a client-supplied name. `ext` is the already validated, lowercase
    extension (with the dot). Keeps Unicode; never returns a path, a hidden or a reserved name."""
    name = unicodedata.normalize("NFC", raw or "")
    name = re.split(r"[/\\]", name)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch) != "Cc")
    name = _FORBIDDEN_CHARS.sub("_", name)
    stem = name[: -len(ext)] if name.lower().endswith(ext) else name.rsplit(".", 1)[0] if "." in name else name
    stem = stem.lstrip(". ").rstrip(". ")
    if not stem:
        return f"upload-{uuid.uuid4().hex[:8]}{ext}"
    if stem.split(".")[0].strip().upper() in _RESERVED_NAMES:
        stem = "_" + stem
    stem = _cap_utf8(stem, MAX_STEM_BYTES).rstrip(". ")
    return f"{stem}{ext}"


def _prepare_inbox() -> tuple[Path, Path]:
    raw = settings.get("PATH_INGESTION_SOURCE")
    if not raw:
        raise InboxUnavailableError("PATH_INGESTION_SOURCE is not configured on this server")
    inbox = Path(raw)
    if not inbox.is_dir():
        raise InboxUnavailableError("The ingestion inbox directory does not exist")
    staging = inbox / STAGING_DIR_NAME
    try:
        staging.mkdir(exist_ok=True)
    except OSError as exc:
        raise InboxUnavailableError("The ingestion inbox is not writable") from exc
    if not os.access(staging, os.W_OK):
        raise InboxUnavailableError("The ingestion inbox is not writable")
    return inbox, staging


def _remove_stale_staging(staging: Path) -> None:
    cutoff = time.time() - STALE_STAGING_SECONDS
    try:
        for entry in os.scandir(staging):
            try:
                if entry.is_file(follow_symlinks=False) and entry.stat().st_mtime < cutoff:
                    os.unlink(entry.path)
            except OSError:
                continue
    except OSError:
        logger.warning("Could not clean the inbox staging directory")


def _find_in_inbox(inbox: Path, digest: str, size: int) -> str | None:
    """Name of a regular inbox file with this content, or None. Only files of the same size are hashed."""
    scanned = 0
    with os.scandir(inbox) as entries:
        for entry in entries:
            scanned += 1
            if scanned > MAX_INBOX_ENTRIES_SCANNED:
                logger.warning("Inbox scan stopped after %d entries", MAX_INBOX_ENTRIES_SCANNED)
                return None
            try:
                if not entry.is_file(follow_symlinks=False) or entry.stat(follow_symlinks=False).st_size != size:
                    continue
                if sha256_file(entry.path) == digest:
                    return entry.name
            except OSError:
                continue
    return None


def _free_name(inbox: Path, name: str) -> str:
    if not (inbox / name).exists():
        return name
    stem, ext = os.path.splitext(name)
    for n in range(1, MAX_COLLISION_SUFFIX + 1):
        candidate = f"{stem}_{n}{ext}"
        if not (inbox / candidate).exists():
            return candidate
    return f"upload-{uuid.uuid4().hex[:8]}{ext}"


def _write_all(inbox: Path, staging: Path, candidates: list[UploadCandidate],
               digests: list[str], library_hashes: set[str]) -> list[StoredFile]:
    stored: list[StoredFile] = []
    for candidate, digest in zip(candidates, digests):
        name = sanitize_filename(candidate.original_filename, candidate.ext)
        if digest in library_hashes:
            stored.append(StoredFile(candidate, name, DUPLICATE))
            continue
        staged = staging / uuid.uuid4().hex
        try:
            staged.write_bytes(candidate.data)
            with _publish_lock:
                existing = _find_in_inbox(inbox, digest, len(candidate.data))
                if existing is not None:
                    staged.unlink()
                    stored.append(StoredFile(candidate, existing, DUPLICATE))
                    continue
                final = _free_name(inbox, name)
                os.replace(staged, inbox / final)
            stored.append(StoredFile(candidate, final, ACCEPTED))
        except OSError as exc:
            staged.unlink(missing_ok=True)
            raise InboxWriteError("Could not write the file into the ingestion inbox") from exc
    return stored


class InboxUploadService:
    """Stores validated uploads in the ingestion inbox. `repo` needs `existing_content_hashes`."""

    def __init__(self, repo):
        self._repo = repo

    async def store(self, candidates: list[UploadCandidate]) -> list[StoredFile]:
        if not candidates:
            return []
        inbox, staging = _prepare_inbox()
        _remove_stale_staging(staging)
        digests = await asyncio.to_thread(lambda: [hashlib.sha256(c.data).hexdigest() for c in candidates])
        try:
            library_hashes = await self._repo.existing_content_hashes(set(digests))
        except Exception as exc:
            logger.exception("Upload duplicate lookup failed")
            raise DuplicateCheckUnavailableError("Duplicate check is unavailable") from exc
        return await asyncio.to_thread(_write_all, inbox, staging, candidates, digests, library_hashes)
