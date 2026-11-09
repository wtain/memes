"""Writes uploaded images into the ingestion inbox (PATH_INGESTION_SOURCE).

Design: docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-b1-inbox-upload-design.md
(including its "Review outcome" section).
"""
import asyncio
import hashlib
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

from batch.utils.file_hash import sha256_file
from config.settings import settings

logger = logging.getLogger(__name__)

STAGING_DIR_NAME = ".uploading"
STALE_STAGING_SECONDS = 3600
# The duplicate scan lists the inbox once per request. A bigger inbox fails the request (fail closed): treating the
# file as "not a duplicate" would let a re-sent file reach ingestion and inflate popularity.
MAX_INBOX_ENTRIES = 50_000
MAX_COLLISION_SUFFIX = 999
# Refuse a request when the inbox volume would be left with less free space than this (after the request's bytes).
MIN_FREE_BYTES = 1024 ** 3
# Decoded pixel cap (width * height) of one upload: a decompression-bomb guard for Pillow and the ML stack downstream.
MAX_IMAGE_PIXELS = 100_000_000
# How many recently published (inbox, digest) pairs this process remembers, to catch concurrent identical uploads.
RECENT_PUBLISHED_LIMIT = 2000

# One backend process per inbox. The lock guards only the "recently published" registry and the publish step of
# concurrent requests; correctness against other writers (a person, ingest_abort, another process) comes from the
# no-clobber publish primitive, not from this lock.
_publish_lock = threading.Lock()
_recent_published: "OrderedDict[tuple[str, str], str]" = OrderedDict()

ACCEPTED = "accepted"
DUPLICATE = "duplicate"

_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # NTFS junction

# Pillow `Image.format` expected for each accepted extension (a mismatch is rejected, not corrected).
_FORMAT_BY_EXT = {
    ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".gif": "GIF",
    ".webp": "WEBP", ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF",
}


class InboxUnavailableError(Exception):
    """PATH_INGESTION_SOURCE is unset, missing, not writable or full of entries (maps to 503)."""


class InboxOutOfSpaceError(Exception):
    """The inbox volume has too little free space (maps to 507)."""


class DuplicateCheckUnavailableError(Exception):
    """The library duplicate lookup failed (maps to 503)."""


class InboxWriteError(Exception):
    """Writing a file into the inbox failed (maps to 500)."""


@dataclass(frozen=True)
class UploadCandidate:
    original_filename: str
    ext: str  # validated, lowercase, with the dot
    data: bytes
    index: int = 0  # position of the file in the request's `files` list


@dataclass(frozen=True)
class StoredFile:
    candidate: UploadCandidate
    saved_as: str  # the name in the inbox for ACCEPTED; the sanitised original name for DUPLICATE
    status: str  # ACCEPTED | DUPLICATE


_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
# Control, format (RTL override, zero width), surrogate, private use, line and paragraph separators.
_DROPPED_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"}
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
    name = "".join(ch for ch in name if unicodedata.category(ch) not in _DROPPED_CATEGORIES)
    name = _FORBIDDEN_CHARS.sub("_", name)
    if name.lower().endswith(ext):
        stem = name[: -len(ext)]
    elif "." in name:
        stem = name.rsplit(".", 1)[0]
    else:
        stem = name
    stem = stem.lstrip(". ").rstrip(". ")
    if not stem:
        return f"upload-{uuid.uuid4().hex[:8]}{ext}"
    if stem.split(".")[0].strip().upper() in _RESERVED_NAMES:
        stem = "_" + stem
    stem = _cap_utf8(stem, MAX_STEM_BYTES).rstrip(". ")
    return f"{stem}{ext}"


def validate_image_content(data: bytes, ext: str) -> str | None:
    """Reason the bytes are not an acceptable image for this extension, or None. Runs Pillow, so call it in a
    worker thread. A format that does not match the extension is rejected (never corrected)."""
    expected = _FORMAT_BY_EXT.get(ext)
    try:
        with Image.open(BytesIO(data)) as img:
            actual = img.format
            width, height = img.size
            if actual != expected:
                return f"File content is {actual}, which does not match the {ext} extension"
            if width * height > MAX_IMAGE_PIXELS:
                return f"Image is too large: {width}x{height} pixels (max {MAX_IMAGE_PIXELS} pixels)"
            img.verify()
    except Image.DecompressionBombError:
        return "Image is too large (decompression bomb guard)"
    except Exception:  # Pillow raises many types for broken input; none of them is the caller's business
        return "File content is not a valid image"
    return None


def _is_link_like(path: Path) -> bool:
    """Symlink or NTFS junction (a OneDrive placeholder reparse point is deliberately not matched)."""
    try:
        if path.is_symlink():
            return True
        return getattr(os.lstat(path), "st_reparse_tag", 0) == _IO_REPARSE_TAG_MOUNT_POINT
    except OSError:
        return False


def _resolve_inbox() -> Path:
    raw = settings.get("PATH_INGESTION_SOURCE")
    if not raw:
        raise InboxUnavailableError("PATH_INGESTION_SOURCE is not configured on this server")
    inbox = Path(raw)
    if not inbox.is_absolute():
        logger.error("PATH_INGESTION_SOURCE is not an absolute path: %s", raw)
        raise InboxUnavailableError("PATH_INGESTION_SOURCE must be an absolute path")
    if not inbox.is_dir():
        logger.warning("The ingestion inbox directory does not exist: %s", raw)
        raise InboxUnavailableError("The ingestion inbox directory does not exist")
    base_raw = settings.get("BASE_PATH")
    if base_raw:
        resolved_inbox, resolved_base = inbox.resolve(), Path(base_raw).resolve()
        # BASE_PATH/inbox (a subdirectory) is the supported convention; the library itself is not an inbox.
        if resolved_inbox == resolved_base or resolved_inbox in resolved_base.parents:
            logger.error("PATH_INGESTION_SOURCE (%s) must not be BASE_PATH or contain it", raw)
            raise InboxUnavailableError("PATH_INGESTION_SOURCE must not be the image library directory")
    return inbox


def prepare_inbox(total_bytes: int = 0) -> tuple[Path, Path]:
    """Resolve and check the inbox and its staging directory, remove stale staging files. Blocking I/O."""
    inbox = _resolve_inbox()
    staging = inbox / STAGING_DIR_NAME
    if _is_link_like(staging) or (staging.exists() and not staging.is_dir()):
        logger.error("The inbox staging path is not a plain directory: %s", staging)
        raise InboxUnavailableError("The ingestion inbox is not writable")
    try:
        staging.mkdir(exist_ok=True)
    except OSError as exc:
        logger.exception("Could not create the inbox staging directory %s", staging)
        raise InboxUnavailableError("The ingestion inbox is not writable") from exc
    if not os.access(staging, os.W_OK):
        logger.error("The inbox staging directory is not writable: %s", staging)
        raise InboxUnavailableError("The ingestion inbox is not writable")
    try:
        free = shutil.disk_usage(inbox).free
    except OSError:
        logger.warning("Could not read the free space of the inbox volume", exc_info=True)
        free = None
    if free is not None and free - total_bytes < MIN_FREE_BYTES:
        logger.error("The inbox volume has too little free space: %d bytes free, %d requested", free, total_bytes)
        raise InboxOutOfSpaceError("The ingestion inbox volume is out of space")
    _remove_stale_staging(staging)
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
        logger.warning("Could not clean the inbox staging directory", exc_info=True)


class _InboxIndex:
    """One listing of the inbox per request: size -> regular file paths. Digests are computed lazily, only for files
    of the same size as an upload, and cached for the request."""

    def __init__(self, inbox: Path):
        self._by_size: dict[int, list[str]] = {}
        self._digests: dict[str, str] = {}
        count = 0
        with os.scandir(inbox) as entries:
            for entry in entries:
                count += 1
                if count > MAX_INBOX_ENTRIES:
                    logger.error("The ingestion inbox holds more than %d entries", MAX_INBOX_ENTRIES)
                    raise InboxUnavailableError("The ingestion inbox is full, run ingestion")
                try:
                    if entry.is_file(follow_symlinks=False):
                        size = entry.stat(follow_symlinks=False).st_size
                        self._by_size.setdefault(size, []).append(entry.path)
                except OSError:
                    continue

    def contains(self, digest: str, size: int) -> bool:
        for path in self._by_size.get(size, ()):
            if path not in self._digests:
                try:
                    self._digests[path] = sha256_file(path)
                except OSError:
                    self._digests[path] = ""  # unreadable: never a match
            if self._digests[path] == digest:
                return True
        return False

    def add(self, path: str, size: int, digest: str) -> None:
        self._by_size.setdefault(size, []).append(path)
        self._digests[path] = digest


def _exclusive_copy(staged: Path, final: Path) -> None:
    """No-clobber publish for volumes without hard links: create the target exclusively, then copy. Unlike a link
    the target is briefly visible while it is written, so this is only a fallback."""
    with open(final, "xb") as target:
        try:
            with open(staged, "rb") as source:
                shutil.copyfileobj(source, target)
        except BaseException:
            target.close()
            final.unlink(missing_ok=True)
            raise
    staged.unlink()


def _publish_once(staged: Path, final: Path) -> None:
    """Publish the staged file under `final` without ever replacing an existing file: raises FileExistsError if the
    name is taken (case-insensitively on NTFS). The staged file is consumed on success."""
    try:
        os.link(staged, final)
    except FileExistsError:
        raise
    except OSError:
        logger.warning("Hard links are not supported here, falling back", exc_info=True)
        if os.name == "nt":
            os.rename(staged, final)  # atomic; unlike POSIX, fails when the target exists
        else:
            _exclusive_copy(staged, final)
        return
    staged.unlink()


def _candidate_names(name: str):
    yield name
    stem, ext = os.path.splitext(name)
    for n in range(1, MAX_COLLISION_SUFFIX + 1):
        yield f"{stem}_{n}{ext}"
    while True:
        yield f"upload-{uuid.uuid4().hex[:8]}{ext}"


def _publish(inbox: Path, staged: Path, name: str) -> str:
    """Move the staged file into the inbox under `name`, or the next free numeric suffix. The cheap exists() test
    only skips names that are visibly taken; the no-clobber primitive decides."""
    for candidate in _candidate_names(name):
        if (inbox / candidate).exists():
            continue
        try:
            _publish_once(staged, inbox / candidate)
        except FileExistsError:
            continue
        return candidate
    raise AssertionError("unreachable")  # pragma: no cover


def _recently_published(inbox: Path, digest: str, size: int) -> bool:
    """True when this process published this content into this inbox a moment ago and it is still there."""
    name = _recent_published.get((str(inbox), digest))
    if name is None:
        return False
    try:
        return (inbox / name).stat().st_size == size
    except OSError:
        return False


def _remember_published(inbox: Path, digest: str, name: str) -> None:
    _recent_published[(str(inbox), digest)] = name
    while len(_recent_published) > RECENT_PUBLISHED_LIMIT:
        _recent_published.popitem(last=False)


def _write_one(inbox: Path, staging: Path, candidate: UploadCandidate, digest: str,
               name: str) -> str | None:
    """Stage and publish one file. Returns the final inbox name, or None when a concurrent request of this process
    published the same content first."""
    staged = staging / uuid.uuid4().hex
    try:
        staged.write_bytes(candidate.data)
        with _publish_lock:
            if _recently_published(inbox, digest, len(candidate.data)):
                staged.unlink()
                return None
            final = _publish(inbox, staged, name)
            _remember_published(inbox, digest, final)
        return final
    except OSError as exc:
        staged.unlink(missing_ok=True)
        logger.exception("Could not write the upload %r into the ingestion inbox", name)
        raise InboxWriteError("Could not write the file into the ingestion inbox") from exc


def _write_all(inbox: Path, staging: Path, candidates: list[UploadCandidate],
               digests: list[str], library_hashes: set[str]) -> list[StoredFile]:
    stored: list[StoredFile] = []
    index: _InboxIndex | None = None
    for candidate, digest in zip(candidates, digests):
        name = sanitize_filename(candidate.original_filename, candidate.ext)
        size = len(candidate.data)
        if digest in library_hashes:
            stored.append(StoredFile(candidate, name, DUPLICATE))
            continue
        if index is None:
            index = _InboxIndex(inbox)
        if index.contains(digest, size):
            stored.append(StoredFile(candidate, name, DUPLICATE))
            continue
        final = _write_one(inbox, staging, candidate, digest, name)
        if final is None:
            stored.append(StoredFile(candidate, name, DUPLICATE))
            continue
        index.add(str(inbox / final), size, digest)
        stored.append(StoredFile(candidate, final, ACCEPTED))
    return stored


class InboxUploadService:
    """Stores validated uploads in the ingestion inbox. `repo` needs `existing_content_hashes`."""

    def __init__(self, repo):
        self._repo = repo

    async def store(self, candidates: list[UploadCandidate]) -> list[StoredFile]:
        if not candidates:
            return []
        total_bytes = sum(len(c.data) for c in candidates)
        inbox, staging = await asyncio.to_thread(prepare_inbox, total_bytes)
        digests = await asyncio.to_thread(lambda: [hashlib.sha256(c.data).hexdigest() for c in candidates])
        try:
            library_hashes = await self._repo.existing_content_hashes(set(digests))
        except Exception as exc:
            logger.exception("Upload duplicate lookup failed")
            raise DuplicateCheckUnavailableError("Duplicate check is unavailable") from exc
        try:
            return await asyncio.to_thread(_write_all, inbox, staging, candidates, digests, library_hashes)
        except OSError as exc:  # the inbox listing itself failed
            logger.exception("Could not list the ingestion inbox")
            raise InboxWriteError("Could not read the ingestion inbox") from exc
