"""P3-T2: batch planner, deterministic batch ids, streaming tar writer and the sums list for the gate's `verify` verb.

Pure standard library. Design: P3 "promote flow" step 6 and the strict extractor (the tar written here must pass it: regular files
only, plain names, PAX format with UTF-8 names).
"""
from __future__ import annotations

import hashlib
import os
import tarfile
from dataclasses import dataclass
from typing import BinaryIO

from sync.files.manifest import FileChanged, ManifestEntry, NotRegular, _open_regular, _same, check_name

MIN_BATCH_MB = 150
MAX_BATCH_MB = 500
DEFAULT_MAX_MEMBERS = 5000  # gate cap `max_members`
DEFAULT_MAX_MEMBER_MB = 200  # gate cap `max_member_mb`
ID_VERSION = b"memes-p3-batch-v1"
READ_CHUNK = 1024 * 1024
MB = 1024 * 1024


class PlanError(ValueError):
    """The entries cannot be planned within the gate's caps or name rules."""


class PackError(Exception):
    """A dev file was not what the manifest says while it was packed. The partial tar must be discarded."""

    def __init__(self, name: str, rule: str):
        super().__init__(f"{rule}")
        self.name = name
        self.rule = rule  # not_regular_file, unreadable, changed_during_read, content_hash_differs


@dataclass(frozen=True)
class Batch:
    id: str
    entries: tuple  # ManifestEntry, name-sorted
    total_bytes: int

    @property
    def names(self) -> list:
        return [e.filename for e in self.entries]


def _name_key(e: ManifestEntry) -> bytes:
    return e.filename.encode("utf-8", "surrogatepass")


def batch_id(env: str, entries) -> str:
    """32 hex characters: a hash of the environment and the slice contents (name, sha256, size of each member, name order).

    A changed manifest yields different ids; an unchanged one reproduces them (resume, review L4)."""
    h = hashlib.sha256()
    h.update(ID_VERSION + b"\0" + env.encode("utf-8") + b"\0")
    for e in sorted(entries, key=_name_key):
        h.update(e.filename.encode("utf-8", "surrogatepass") + b"\0" + e.sha256.encode("ascii") + b"\0" + str(e.size).encode("ascii") + b"\n")
    return h.hexdigest()[:32]


def plan_batches(env: str, entries, batch_mb: float = 300, *, max_members: int = DEFAULT_MAX_MEMBERS,
                 max_member_mb: float = DEFAULT_MAX_MEMBER_MB, target_bytes: int | None = None) -> list:
    """Split entries (name-sorted) into batches of about `batch_mb` (150 to 500), at most `max_members` members each.

    A batch closes before the member that would push it over the target; a member never has to fit alone beyond the member cap.
    `target_bytes` overrides the target (tests); it skips the 150 to 500 range check.
    """
    if target_bytes is None:
        if not (MIN_BATCH_MB <= batch_mb <= MAX_BATCH_MB):
            raise PlanError(f"batch size must be between {MIN_BATCH_MB} and {MAX_BATCH_MB} MB")
        target_bytes = int(batch_mb * MB)
    if max_members < 1:
        raise PlanError("max_members must be at least 1")
    ordered = sorted(entries, key=_name_key)
    seen = set()
    for e in ordered:
        if check_name(e.filename) is not None:
            raise PlanError("an entry has a name the gate would refuse")
        if e.filename in seen:
            raise PlanError("duplicate name in the entries")
        seen.add(e.filename)
        if e.size > max_member_mb * MB:
            raise PlanError("an entry is larger than the member cap")
        if e.size < 0:
            raise PlanError("negative size")
    batches, cur, cur_bytes = [], [], 0

    def close():
        nonlocal cur, cur_bytes
        if cur:
            batches.append(Batch(batch_id(env, cur), tuple(cur), cur_bytes))
        cur, cur_bytes = [], 0

    for e in ordered:
        if cur and (cur_bytes + e.size > target_bytes or len(cur) >= max_members):
            close()
        cur.append(e)
        cur_bytes += e.size
    close()
    return batches


# ---------------------------------------------------------------------------------------------------------------------------------
# tar

class _HashingSource:
    """File-like reading exactly the manifest size from an open fd; hashes what it reads. tarfile asks for `size` bytes."""

    def __init__(self, fd: int, size: int):
        self.fd, self.size, self.read_bytes = fd, size, 0
        self.digest = hashlib.sha256()

    def read(self, n: int = -1) -> bytes:
        remaining = self.size - self.read_bytes
        if remaining <= 0:
            return b""
        n = remaining if n is None or n < 0 else min(n, remaining, READ_CHUNK)
        data = os.read(self.fd, n)
        self.read_bytes += len(data)
        self.digest.update(data)
        return data


def write_tar(batch: Batch, out: BinaryIO) -> int:
    """Write the batch as a PAX tar in streaming mode (no seeking) and return the bytes written.

    Members are regular files only, in name order, with fixed metadata (uid/gid 0, no user names, mode 0644, mtime 0), so the same
    batch always yields the same bytes. Every source is opened without following links, must still be a regular file with the
    manifest size, must end exactly where the manifest says and must hash to the manifest value; otherwise PackError (the caller
    discards the partial output). Memory use is one read chunk.
    """
    counter = _Counter(out)
    with tarfile.open(fileobj=counter, mode="w|", format=tarfile.PAX_FORMAT, encoding="utf-8", errors="strict") as tar:
        for e in batch.entries:
            try:
                fd, st = _open_regular(e.path)
            except NotRegular as exc:
                raise PackError(e.filename, "not_regular_file") from exc
            except OSError as exc:
                raise PackError(e.filename, "unreadable") from exc
            try:
                if st.st_size != e.size:
                    raise PackError(e.filename, "changed_during_read")
                src = _HashingSource(fd, e.size)
                info = tarfile.TarInfo(e.filename)
                info.size, info.mode, info.mtime = e.size, 0o644, 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.type = tarfile.REGTYPE
                info.pax_headers = {}
                try:
                    tar.addfile(info, src)
                except OSError as exc:  # short read: the file shrank
                    if src.read_bytes != e.size:
                        raise PackError(e.filename, "changed_during_read") from exc
                    raise
                if src.read_bytes != e.size or os.read(fd, 1) or not _same(st, os.fstat(fd)):
                    raise PackError(e.filename, "changed_during_read")
                if src.digest.hexdigest() != e.sha256:
                    raise PackError(e.filename, "content_hash_differs")
            finally:
                os.close(fd)
    return counter.n


class _Counter:
    def __init__(self, out: BinaryIO):
        self.out, self.n = out, 0

    def write(self, data: bytes) -> int:
        self.out.write(data)
        self.n += len(data)
        return len(data)


def sums_payload(batch: Batch) -> bytes:
    """Stdin of the gate's `verify <id>`: for each member three NUL-terminated fields `sha256 NUL size NUL name NUL`, name order
    (the gate reads flat NUL fields in groups of three). Names cannot contain NUL (name rules), so no escaping is needed. The sums
    are the manifest's, not re-read."""
    return b"".join(f"{e.sha256}\0{e.size}\0{e.filename}\0".encode("utf-8") for e in batch.entries)
