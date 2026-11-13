"""P3-T1: the manifest of promotable image files, the name rules and the problem list.

Pure standard library; the only I/O is reading dev files (never following links) and, through a connection-like object, the
promotable rows. Design: docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p3-promote-files-design.md,
interfaces.md section a (`manifest_sha256`).
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

from sync.model import PROMOTABLE_IMAGES_SQL

READ_CHUNK = 1024 * 1024
MAX_NAME_BYTES = 255
TRIPWIRE_FRACTION = 0.005  # left-out images above 0.5 % of the promotable set stop the run (a dev data problem)
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# Reason codes are the ones of sync.report.REASON_CODES; `rule` says which check fired.
MISSING_FILE = "missing_file"
NAME_PROBLEM = "name_problem"
HASH_MISMATCH = "hash_mismatch"

PROMOTABLE_ROWS_SQL = f"SELECT images.id, images.filename, images.content_hash FROM images WHERE {PROMOTABLE_IMAGES_SQL}"

_RESERVED_BASE = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
_RESERVED_BASE |= {f"COM{c}" for c in "¹²³"} | {f"LPT{c}" for c in "¹²³"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class FileChanged(Exception):
    """A dev file changed (size, mtime or kind) while it was being read."""


class NotRegular(Exception):
    """The path is not a plain regular file (link, directory, reparse point, device)."""


# ---------------------------------------------------------------------------------------------------------------------------------
# name rules

def check_name(name: Any) -> str | None:
    """Return the rule that rejects `name` as a promotable file name, or None when it is acceptable.

    Rules: valid text, no control characters (tab, newline, NUL, DEL), no path separators or ':' (NTFS stream), no leading dot,
    no trailing dot or space, no Windows device name, at most 255 UTF-8 bytes. Stricter than the gate's final regex on purpose
    (the dev disk is NTFS, prod is ext4).
    """
    if not isinstance(name, str) or name == "":
        return "empty"
    try:
        raw = name.encode("utf-8")
    except UnicodeEncodeError:
        return "invalid_unicode"  # lone surrogates
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return "control_character"
    if "/" in name or "\\" in name or ":" in name:
        return "path_separator"
    if name.startswith("."):
        return "leading_dot"
    if name.endswith(".") or name.endswith(" "):
        return "trailing_dot_or_space"
    stem = name.split(".", 1)[0].rstrip(" ").upper()
    if stem in _RESERVED_BASE:
        return "reserved_name"
    if len(raw) > MAX_NAME_BYTES:
        return "name_too_long"
    return None


def collision_key(name: str) -> str:
    """Names with the same key collide on a case-insensitive or normalising file system (NTFS dev versus ext4 prod)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


# ---------------------------------------------------------------------------------------------------------------------------------
# model

@dataclass(frozen=True)
class Row:
    id: str
    filename: str
    content_hash: str | None


@dataclass(frozen=True)
class ManifestEntry:
    id: str
    filename: str
    size: int
    sha256: str  # the content hash the file must have (the column, or computed when the column was NULL)
    verified: bool = False  # the file was read and hashed on this run
    path: str = ""  # dev path to read (the on-disk spelling; may differ in case from `filename`)


@dataclass(frozen=True)
class LeftOut:
    id: str
    filename: str
    reason: str  # a sync.report reason code
    rule: str  # which check fired (check_name rule, `not_regular_file`, `changed_during_read`, ...)

    @property
    def label(self) -> str:
        """The value of `left_out[].reason` in files-promoted.json: name_<rule> for name problems, else the reason code."""
        return f"name_{self.rule}" if self.reason == NAME_PROBLEM else self.reason


@dataclass
class Manifest:
    entries: dict = field(default_factory=dict)  # filename -> ManifestEntry
    left_out: list = field(default_factory=list)  # LeftOut
    warnings: list = field(default_factory=list)  # (id, rule): case_differs_on_disk, hash_computed
    promotable_count: int = 0
    manifest_sha256: str = ""  # over the promotable set as read, left-out images included

    @property
    def tripwire_tripped(self) -> bool:
        return self.promotable_count > 0 and len(self.left_out) > TRIPWIRE_FRACTION * self.promotable_count

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries.values())

    def ids_by_reason(self) -> dict:
        out: dict = {}
        for lo in self.left_out:
            out.setdefault(lo.reason, []).append(lo.id)
        return out


# ---------------------------------------------------------------------------------------------------------------------------------
# manifest_sha256 (interfaces.md section a)

def manifest_sha256(rows: Iterable[Any]) -> str:
    """sha256 over the sorted `id TAB filename TAB content_hash` lines of the promotable set, each line ended by LF.

    `rows` are Row objects, (id, filename, content_hash) tuples or dicts with those keys. A NULL content_hash is an empty field.
    Lines are sorted by their UTF-8 bytes (surrogates pass through, so hostile rows still hash). Left-out images are included.
    """
    lines = []
    for r in rows:
        i, f, h = _row_fields(r)
        lines.append(f"{i}\t{f}\t{h or ''}\n".encode("utf-8", "surrogatepass"))
    lines.sort()
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line)
    return digest.hexdigest()


def _row_fields(r: Any) -> tuple:
    if isinstance(r, Row):
        return str(r.id), r.filename, r.content_hash
    if isinstance(r, dict):
        return str(r["id"]), r["filename"], r.get("content_hash")
    i, f, h = r
    return str(i), f, h


def to_rows(raw: Iterable[Any]) -> list:
    return [Row(*_row_fields(r)) for r in raw]


def load_promotable_rows(conn: Any) -> list:
    """Read the promotable rows through a DB-API connection-like object (cursor() / execute() / fetchall())."""
    cur = conn.cursor()
    try:
        cur.execute(PROMOTABLE_ROWS_SQL)
        return to_rows(cur.fetchall())
    finally:
        close = getattr(cur, "close", None)
        if close:
            close()


# ---------------------------------------------------------------------------------------------------------------------------------
# safe file access on dev

def _is_reparse(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _open_regular(path: str) -> tuple:
    """Open a plain regular file for reading without following a final link; returns (fd, fstat)."""
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise NotRegular(str(exc)) from exc
    if not stat.S_ISREG(before.st_mode) or _is_reparse(before):
        raise NotRegular("not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (before.st_ino and st.st_ino and (before.st_ino, before.st_dev) != (st.st_ino, st.st_dev)):
            raise NotRegular("not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd, st


def _same(a: os.stat_result, b: os.stat_result) -> bool:
    return a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns


def hash_file(path: str) -> tuple:
    """(sha256 hex, size) of a regular file, read once. Raises NotRegular, FileChanged (size or mtime moved during the read), OSError."""
    fd, st = _open_regular(path)
    try:
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, READ_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if total != st.st_size or not _same(st, after):
        raise FileChanged("file changed while being read")
    return digest.hexdigest(), total


# ---------------------------------------------------------------------------------------------------------------------------------
# build

def _scan(base_dir: str) -> tuple:
    """Directory listing: exact name -> DirEntry-like info, and casefold map for case differences."""
    exact: dict = {}
    folded: dict = {}
    with os.scandir(base_dir) as it:
        for e in it:
            exact[e.name] = e
            folded.setdefault(collision_key(e.name), []).append(e.name)
    return exact, folded


def _regular_entry(e: Any) -> bool:
    try:
        if e.is_symlink() or not e.is_file(follow_symlinks=False):
            return False
        return not _is_reparse(e.stat(follow_symlinks=False))
    except OSError:
        return False


def build_manifest(rows: Iterable[Any], base_dir: str) -> Manifest:
    """Promotable rows -> manifest with sizes (from the file system) and the hashes of the content_hash column.

    The tar entry name is the database filename. Rows with a problem are left out and listed (never fatal here; the caller applies
    the 0.5 % tripwire through `Manifest.tripwire_tripped`). A NULL content_hash is hashed now; other hashes are trusted until
    `verify_hashes` (run for the files that are packed, or all with --deep).
    """
    rows = to_rows(rows)
    result = Manifest(promotable_count=len(rows), manifest_sha256=manifest_sha256(rows))
    exact, folded = _scan(base_dir)

    # Collisions first: every member of a group is left out (no arbitrary winner; deterministic, the owner fixes the data).
    groups: dict = {}
    for r in rows:
        if check_name(r.filename) is None:
            groups.setdefault(collision_key(r.filename), []).append(r)
    colliding = {k for k, g in groups.items() if len(g) > 1}

    for r in sorted(rows, key=lambda r: (r.filename.encode("utf-8", "surrogatepass"), r.id)):
        rule = check_name(r.filename)
        if rule:
            result.left_out.append(LeftOut(r.id, r.filename, NAME_PROBLEM, rule))
            continue
        if collision_key(r.filename) in colliding:
            same = len({x.filename for x in groups[collision_key(r.filename)]}) == 1
            result.left_out.append(LeftOut(r.id, r.filename, NAME_PROBLEM, "duplicate_name" if same else "normalisation_collision"))
            continue

        disk_name = r.filename if r.filename in exact else None
        if disk_name is None:
            cands = folded.get(collision_key(r.filename), [])
            if len(cands) == 1:
                disk_name = cands[0]
                result.warnings.append((r.id, "case_differs_on_disk"))
        if disk_name is None:
            result.left_out.append(LeftOut(r.id, r.filename, MISSING_FILE, "no_such_file"))
            continue
        if not _regular_entry(exact[disk_name]):
            result.left_out.append(LeftOut(r.id, r.filename, MISSING_FILE, "not_regular_file"))
            continue
        path = os.path.join(base_dir, disk_name)

        declared = (r.content_hash or "").strip().lower() or None
        if declared is not None and not _HEX64.match(declared):
            result.left_out.append(LeftOut(r.id, r.filename, HASH_MISMATCH, "malformed_hash"))
            continue
        try:
            if declared is None:
                digest, size = hash_file(path)
                result.warnings.append((r.id, "hash_computed"))
                result.entries[r.filename] = ManifestEntry(r.id, r.filename, size, digest, True, path)
            else:
                size = os.lstat(path).st_size
                result.entries[r.filename] = ManifestEntry(r.id, r.filename, size, declared, False, path)
        except NotRegular:
            result.left_out.append(LeftOut(r.id, r.filename, MISSING_FILE, "not_regular_file"))
        except FileChanged:
            result.left_out.append(LeftOut(r.id, r.filename, HASH_MISMATCH, "changed_during_read"))
        except OSError:
            result.left_out.append(LeftOut(r.id, r.filename, MISSING_FILE, "unreadable"))
    return result


def verify_hashes(manifest: Manifest, names: Iterable[str] | None = None) -> Manifest:
    """Read the named files (all when None) and compare with the manifest hash; failures move to `left_out`.

    Returns the same Manifest object, updated. Called for every file that is packed this run (and for all with --deep).
    """
    wanted = list(manifest.entries) if names is None else [n for n in names if n in manifest.entries]
    for name in sorted(wanted, key=lambda n: n.encode("utf-8", "surrogatepass")):
        e = manifest.entries[name]
        if e.verified:
            continue
        try:
            digest, size = hash_file(e.path)
        except NotRegular:
            _drop(manifest, e, MISSING_FILE, "not_regular_file")
        except FileChanged:
            _drop(manifest, e, HASH_MISMATCH, "changed_during_read")
        except OSError:
            _drop(manifest, e, MISSING_FILE, "unreadable")
        else:
            if digest != e.sha256:
                _drop(manifest, e, HASH_MISMATCH, "content_hash_differs")
            else:
                manifest.entries[name] = ManifestEntry(e.id, e.filename, size, e.sha256, True, e.path)
    return manifest


def _drop(manifest: Manifest, e: ManifestEntry, reason: str, rule: str) -> None:
    del manifest.entries[e.filename]
    manifest.left_out.append(LeftOut(e.id, e.filename, reason, rule))
