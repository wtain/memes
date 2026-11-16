"""P4-T6: the bug-report file step of `sync.pull` (design "Bug reports (file part)", interfaces.md a, g, k).

SECURITY: bug reports are anonymous, UNTRUSTED bytes (a stranger wrote them on prod). This module never opens, decodes, parses, previews or
executes their content: it moves bytes, hashes them and nothing else. Consumers of the dev bug-reports directory MUST sanitise: treat every file
as hostile (no `eval`/`open`-and-render of `.json`, `.log`, `.txt`, `.zip` or images without escaping, size and format checks; never serve a file
back with a content type the file itself chose; never extract a `.zip`; never follow names to anywhere outside the directory).

Flow (stage 2 of `pull_state.json`, after the dev commit):
1. `pull-list` through the gate (flat, regular, non-hidden files settled for 60 s: size, sha256, name). The client applies its own caps
   (`sync.pull.bug_reports.*`: allowed extensions, max_file_mb, max_files, max_total_mb) and re-validates every name; anything else is counted,
   never fetched, never deleted on prod.
2. `pull-tar` of the files not yet received (ledger `bugreports_ledger.json`, `{name: sha256}`) into a temp file. The tar is read member by member,
   never `extract`ed: regular members only, name equal to a listed name, size equal to the listed size, no duplicates. Each body is streamed into
   a hidden temp file in the dev directory, its sha256 compared with the listed one, fsynced, and published without clobbering (hard link, which
   fails when the name exists). A same-named dev file with the same hash counts as received; a different one is never touched (the report is stored
   as `<stem>_<n>.<ext>`, design step 3).
3. The ledger is rewritten atomically after each published file, THEN `delete-verified` sends the `(sha256, name)` pairs of files verified on dev;
   prod deletes a file only when its hash still equals. Nothing is deleted for a file that did not verify. A crash anywhere leaves either an
   unlisted dev file (re-downloaded, same hash: no-op) or a ledger entry (prod delete without a second download); nothing is lost or duplicated.

The dry run (`plan_lists` / `plan_report`) only lists: it writes nothing on dev, prod or in the state directory, and what it reports is the uuid
part of each file name (hex and dashes), counts and codes, never names, bodies or free text.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import stat
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sync.config import PreconditionError, atomic_write
from sync.pull import state

NAME_RE = re.compile(r"[0-9TZ_-]+_[0-9a-f-]{36}\.[a-z0-9]{1,8}")  # used with fullmatch (no `$`, which also matches before a newline)
UUID_RE = re.compile(r"_([0-9a-f-]{36})\.[a-z0-9]{1,8}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
MIB = 1024 * 1024
DEFAULT_EXTENSIONS = (".txt", ".json", ".png", ".jpg", ".log", ".zip")
BATCH_FILES = 100  # files per pull-tar call
BATCH_BYTES = 64 * MIB  # bytes per pull-tar call (the gate's own pull cap is tracked separately)
DELETE_CHUNK = 200
MAX_SUFFIX = 99
CHECK_ID = "bug_reports_listing"


@dataclass(frozen=True)
class Listed:
    name: str
    size: int
    sha: str

    @property
    def uuid(self) -> str:
        m = UUID_RE.search(self.name)
        return m.group(1) if m else ""


@dataclass
class Caps:
    extensions: tuple
    max_file: int
    max_files: int
    max_total: int


@dataclass
class BugPlan:
    fetch: list = field(default_factory=list)  # Listed not yet received (within the caps)
    resume: list = field(default_factory=list)  # Listed already in the ledger with this hash: only the prod delete is left
    anomalies: int = 0  # gate anomalies and unparsable or invalid records
    not_allowed: int = 0  # extension outside the allow-list
    too_big: int = 0  # over max_file_mb
    deferred: int = 0  # valid, but over max_files / max_total_mb for this run (the next run takes them)

    @property
    def to_delete(self) -> list:
        return self.resume + self.fetch


def caps_from(settings: Any) -> Caps:
    def get(key, default):
        value = settings.get("pull.bug_reports." + key) if settings is not None else None
        return default if value is None else value

    exts = tuple(str(e).lower() for e in (get("allowed_extensions", DEFAULT_EXTENSIONS) or ()))
    return Caps(exts, int(float(get("max_file_mb", 10)) * MIB), int(get("max_files", 200)), int(float(get("max_total_mb", 200)) * MIB))


def dev_dir(settings: Any) -> Path:
    """Where received files land: tracked `sync.pull.bug_reports.dev_dir` when set (tests, odd layouts), else the backend's own rule
    (`BUG_REPORTS_PATH`, default `BASE_PATH/bug_reports`, Backend/app/services/image_store.py)."""
    raw = settings.get("pull.bug_reports.dev_dir") if settings is not None else None
    raw = raw or os.environ.get("BUG_REPORTS_PATH")
    if raw:
        return Path(str(raw))
    base = os.environ.get("BASE_PATH")
    if base:
        return Path(base) / "bug_reports"
    raise PreconditionError("the dev bug-reports directory is unknown: set BUG_REPORTS_PATH or BASE_PATH, or sync.pull.bug_reports.dev_dir")


# ---------------------------------------------------------------------------------------------------------------------------------
# ledger {name: sha256}

def load_ledger(directory: Path) -> dict:
    try:
        doc = json.loads((Path(directory) / state.BUG_LEDGER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # lost or unreadable: costs a re-download (the no-clobber publish of the same hash is a no-op)
    if not isinstance(doc, dict):
        return {}
    return {k: v for k, v in doc.items() if isinstance(k, str) and NAME_RE.fullmatch(k) and isinstance(v, str) and SHA_RE.fullmatch(v)}


def write_ledger(directory: Path, ledger: dict) -> None:
    atomic_write(Path(directory) / state.BUG_LEDGER_FILE, json.dumps(dict(sorted(ledger.items())), indent=2) + "\n")


# ---------------------------------------------------------------------------------------------------------------------------------
# planning

def parse_listing(raw: bytes) -> tuple:
    """`(files, anomalies)` of a `pull-list` answer: records are NUL-separated `f<TAB>size<TAB>sha<TAB>name`; everything else (anomaly rows,
    undecodable or malformed rows, names outside the pattern, duplicate names) counts as an anomaly and is never fetched."""
    files, seen, anomalies = [], set(), 0
    parts = raw.split(b"\x00")
    if parts and parts[-1] == b"":
        parts.pop()
    for part in parts:
        try:
            fields = part.decode("utf-8").split("\t")
        except UnicodeDecodeError:
            anomalies += 1
            continue
        if len(fields) != 4 or fields[0] != "f" or not fields[1].isdigit() or not SHA_RE.fullmatch(fields[2]) \
                or not NAME_RE.fullmatch(fields[3]) or fields[3] in seen:
            anomalies += 1
            continue
        seen.add(fields[3])
        files.append(Listed(fields[3], int(fields[1]), fields[2]))
    return sorted(files, key=lambda f: f.name), anomalies


def select(files: list, anomalies: int, ledger: dict, caps: Caps) -> BugPlan:
    plan = BugPlan(anomalies=anomalies)
    total = 0
    for f in files:
        ext = "." + f.name.rsplit(".", 1)[1]
        if ext not in caps.extensions:
            plan.not_allowed += 1
        elif f.size > caps.max_file:
            plan.too_big += 1
        elif ledger.get(f.name) == f.sha:
            plan.resume.append(f)
        elif len(plan.fetch) >= caps.max_files or total + f.size > caps.max_total:
            plan.deferred += 1
        else:
            plan.fetch.append(f)
            total += f.size
    return plan


def make_plan(settings: Any, transport: Any, directory: Path) -> BugPlan:
    reply = transport.pull_list()
    if not reply.ok:
        raise PreconditionError(f"bug-report listing failed: {reply.reason or reply.code}")
    files, anomalies = parse_listing(reply.out)
    return select(files, anomalies, load_ledger(directory), caps_from(settings))


def _state_dir(settings: Any) -> Path:
    return Path(settings.state_dir)


def plan_report(settings: Any, transport: Any = None) -> tuple:
    """`(lists, checks)` for the report section of a dry run (ids and codes only). Nothing is written anywhere."""
    if transport is None:
        return {}, []
    plan = make_plan(settings, transport, _state_dir(settings))
    if plan.fetch:
        dev_dir(settings)  # fail before any write (exit 2) when the destination cannot be named
    lists = {"to_fetch": sorted({f.uuid for f in plan.fetch}), "to_delete_on_prod_after_verified_pull": sorted({f.uuid for f in plan.to_delete})}
    left = {"anomalies": plan.anomalies, "not_allowed": plan.not_allowed, "too_big": plan.too_big, "deferred": plan.deferred}
    detail = ",".join(f"{k}={v}" for k, v in left.items() if v)
    checks = [{"id": CHECK_ID, "status": "attention" if detail else "ok", "severity": "info", "detail": detail}]
    return lists, checks


def plan_lists(settings: Any, transport: Any = None) -> dict:
    """Report lists of the dry run (see `plan_report`)."""
    return plan_report(settings, transport)[0]


# ---------------------------------------------------------------------------------------------------------------------------------
# apply

class _Hostile(Exception):
    pass


def _regular_sha(path: Path) -> str | None:
    """sha256 of a regular, non-link file; None for anything else (a link or directory is never read)."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(MIB), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _no_clobber(tmp: Path, final: Path) -> None:
    """Publish `tmp` as `final` only when `final` does not exist (FileExistsError otherwise); the temp name is removed by the caller."""
    try:
        os.link(tmp, final)
    except FileExistsError:
        raise
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
        os.rename(tmp, final)  # Windows: refuses to replace an existing file


def _candidates(name: str):
    yield name
    stem, ext = name.rsplit(".", 1)
    for n in range(1, MAX_SUFFIX + 1):
        yield f"{stem}_{n}.{ext}"


def publish(directory: Path, tmp: Path, name: str, sha: str) -> str:
    """Put the verified temp file into the dev directory. Returns `new` (published), `same` (a file with this hash is already there: counts as
    received). A different existing file is never replaced; the report takes the next free `_n` name. Raises OSError / _Hostile on failure."""
    for cand in _candidates(name):
        final = directory / cand
        if os.path.lexists(final):
            if _regular_sha(final) == sha:
                return "same"
            continue
        try:
            _no_clobber(tmp, final)
            return "new"
        except FileExistsError:
            if _regular_sha(final) == sha:
                return "same"
    raise _Hostile("no_free_name")


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError, AttributeError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _store_member(tar: tarfile.TarFile, member: tarfile.TarInfo, listed: Listed, directory: Path) -> str:
    """Stream one member into a hidden temp file, verify its hash, publish it. Returns `new` or `same`; raises _Hostile / OSError."""
    tmp = directory / f".bugpull-{secrets.token_hex(6)}.tmp"
    try:
        src = tar.extractfile(member)
        if src is None:
            raise _Hostile("no_body")
        h, left = hashlib.sha256(), listed.size
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
        with os.fdopen(fd, "wb") as out:
            while left > 0:
                chunk = src.read(min(MIB, left))
                if not chunk:
                    raise _Hostile("short_member")
                h.update(chunk)
                out.write(chunk)
                left -= len(chunk)
            out.flush()
            os.fsync(out.fileno())
        if h.hexdigest() != listed.sha:
            raise _Hostile("sha_mismatch")
        result = publish(directory, tmp, listed.name, listed.sha)
        _fsync_dir(directory)
        return result
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def _batches(files: list):
    batch, size = [], 0
    for f in files:
        if batch and (len(batch) >= BATCH_FILES or size + f.size > BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(f)
        size += f.size
    if batch:
        yield batch


def _fetch_batch(transport: Any, batch: list, directory: Path, state_path: Path, ledger: dict, run_id: str, counter: int) -> tuple:
    """Returns `(verified Listed list, failed count)`; the ledger (dict, in place and on disk) gains every file that is on dev."""
    expected = {f.name: f for f in batch}
    tmp_tar = state_path / f".bugpull-{run_id}-{counter}.tar"
    verified, seen, failed = [], set(), 0
    try:
        reply = transport.pull_tar([f.name for f in batch], tmp_tar)
        if not reply.ok:
            return [], len(batch)
        try:
            tar = tarfile.open(tmp_tar, mode="r:")
        except (tarfile.TarError, OSError):
            return [], len(batch)
        with tar:
            extra = 0
            while True:
                try:
                    member = tar.next()
                except (tarfile.TarError, OSError, ValueError):
                    break  # a damaged stream: whatever was not stored counts as failed below
                if member is None:
                    break
                listed = expected.get(member.name)
                if (listed is None or member.name in seen or not member.isreg() or member.size != listed.size):
                    extra += 1
                    if extra > len(batch) + 10:
                        break
                    if listed is not None:
                        seen.add(member.name)  # a listed name delivered wrongly: the file fails, the rest continue
                    continue
                seen.add(member.name)
                try:
                    _store_member(tar, member, listed, directory)
                except (_Hostile, OSError, tarfile.TarError, ValueError):
                    continue
                ledger[listed.name] = listed.sha
                try:
                    write_ledger(state_path, ledger)  # the dev file exists: record it before anything is deleted on prod
                except OSError:
                    ledger.pop(listed.name, None)  # not recorded: not deleted on prod, picked up again by the next run
                    continue
                verified.append(listed)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_tar)
    failed = len(batch) - len(verified)
    return verified, failed


def _delete(transport: Any, files: list) -> tuple:
    """`(deleted, failed)`; a file prod keeps (hash changed since, not regular) counts as failed, one already gone does not."""
    deleted = failed = 0
    for i in range(0, len(files), DELETE_CHUNK):
        chunk = files[i:i + DELETE_CHUNK]
        try:
            reply = transport.delete_verified([(f.sha, f.name) for f in chunk])
        except PreconditionError:
            failed += len(chunk)
            continue
        statuses = {}
        for part in reply.out.split(b"\x00"):
            status, _, name = part.decode("utf-8", "replace").partition("\t")
            if name:
                statuses[name] = status
        for f in chunk:
            status = statuses.get(f.name)
            if status == "deleted":
                deleted += 1
            elif status != "missing":
                failed += 1
    return deleted, failed


def run(settings: Any, transport: Any, state_dir: Any, run_id: str) -> dict:
    """The bug-report step of an apply: `{status: done|partial|skipped, pulled, deleted_on_prod, failed}` (state `bug_reports`)."""
    if transport is None:
        return state.skipped_bug_reports()
    directory = Path(state_dir)
    try:
        plan = make_plan(settings, transport, directory)
        target = dev_dir(settings) if (plan.fetch or plan.resume) else None
    except PreconditionError:
        return {"status": "partial", "pulled": 0, "deleted_on_prod": 0, "failed": 1}
    ledger = load_ledger(directory)
    pulled = failed = 0
    # review finding L4: a ledger entry alone never authorises a delete on prod; the dev file must still exist with the listed sha
    done = []
    for f in plan.resume:
        if _regular_sha(target / f.name) == f.sha:
            done.append(f)
        else:
            ledger.pop(f.name, None)  # the dev copy is gone or changed: pull it again, delete nothing on prod for it yet
            plan.fetch.append(f)
    if len(done) != len(plan.resume):
        try:
            write_ledger(directory, ledger)
        except OSError:
            return {"status": "partial", "pulled": 0, "deleted_on_prod": 0, "failed": len(plan.fetch)}
    if plan.fetch:
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            return {"status": "partial", "pulled": 0, "deleted_on_prod": 0, "failed": len(plan.fetch)}
        for n, batch in enumerate(_batches(plan.fetch)):
            try:
                verified, bad = _fetch_batch(transport, batch, target, directory, ledger, run_id, n)
            except PreconditionError:
                verified, bad = [], len(batch)
            pulled += len(verified)
            failed += bad
            done += verified
    deleted, delete_failed = _delete(transport, done)
    failed += delete_failed
    return {"status": "partial" if failed else "done", "pulled": pulled, "deleted_on_prod": deleted, "failed": failed}
