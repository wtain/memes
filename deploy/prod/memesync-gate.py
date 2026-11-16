#!/usr/bin/env python3
"""memesync-gate: the forced command of the verb key of one prod environment (P3 design, interfaces.md section g).

    command="/usr/bin/python3 /opt/memes/memesync-gate.py <env>"      (authorized_keys; the key fixes <env>)

The verb is SSH_ORIGINAL_COMMAND (split on whitespace, never given to a shell); names arrive NUL-separated on stdin.
Standard library only, Python 3.9 or newer, no subprocess, no tar/find/sha256sum, no shell.

Every filesystem call uses a validated final name relative to a directory fd (dir_fd, O_NOFOLLOW, O_EXCL).
Platforms without dir_fd support (Windows) use a path based fallback that is NOT production safe (it cannot close the
check/use race on a swapped symlink); it only runs when MEMESYNC_GATE_ALLOW_NO_DIRFD=1 is set, which the tests do.

Config: /etc/memes/gate.conf (root-owned, one section per environment); the path is overridable with MEMESYNC_GATE_CONF
for tests (sshd does not pass client environment variables to a forced command: AcceptEnv must stay at its default).

Exit codes: 0 ok; 1 the verb failed (strict extractor violation, verify mismatch, bad batch, missing file);
2 usage, config or lock problem (unknown verb, bad names on stdin, lock busy); 3 precondition or guard before any
change (device mismatch, free space below the floor, replace cap, missing facts file). The reason is one line on stderr
(`gate: <reason>: <detail>`), with hostile names escaped to printable ASCII.

Verbs not built on purpose (decision 27): retire, restore, purge.
"""
from __future__ import annotations

import configparser
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import time
import unicodedata
import uuid
from datetime import datetime, timezone

GATE_VERSION = "1"

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_PRECONDITION = 0, 1, 2, 3

_O_DIR = getattr(os, "O_DIRECTORY", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BIN = getattr(os, "O_BINARY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
CHUNK = 1 << 20
MIB = 1 << 20


def _dirfd_supported() -> bool:
    return bool(
        _NOFOLLOW and _O_DIR
        and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd and os.unlink in os.supports_dir_fd
        # os.replace takes src_dir_fd/dst_dir_fd wherever os.rename does (both are renameat) but CPython lists only os.rename in
        # supports_dir_fd; testing os.replace made the gate refuse every verb on Linux ("unsafe_platform") -- found in the P5d-T5 sshd test.
        and os.rename in os.supports_dir_fd and os.mkdir in os.supports_dir_fd and os.rmdir in os.supports_dir_fd
        and os.listdir in os.supports_fd
    )


HAVE_DIRFD = _dirfd_supported()


class GateError(Exception):
    def __init__(self, code: int, reason: str, detail: str = ""):
        super().__init__(reason)
        self.code, self.reason, self.detail = code, reason, detail


def printable(text: object, limit: int = 80) -> str:
    """Escape anything but printable ASCII so a hostile name cannot forge a log line."""
    out = []
    for ch in str(text):
        o = ord(ch)
        if 0x20 <= o < 0x7F and ch != "\\":
            out.append(ch)
        else:
            out.append(ch.encode("unicode_escape").decode("ascii"))
    s = "".join(out)
    return s if len(s) <= limit else s[: limit - 3] + "..."


# ---------------------------------------------------------------- names

NAME_RE = re.compile(r"[^/\\\x00-\x1f.][^/\\\x00-\x1f]{0,254}")
BATCH_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
BUG_NAME_RE = re.compile(r"[0-9TZ_-]+_[0-9a-f-]{36}\.[a-z0-9]{1,8}")
BACKUP_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.dump")
SHA_RE = re.compile(r"[0-9a-f]{64}")
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}


def name_problem(name: str):
    """Return a reason code when `name` may not be a final file name in the images directory, else None."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return "name_pattern"
    try:
        if len(name.encode("utf-8")) > 255:
            return "name_too_long"
    except UnicodeEncodeError:
        return "name_not_unicode"
    if "\x7f" in name:
        return "name_control"
    if name.endswith(" ") or name.endswith("."):
        return "name_trailing"
    if name.split(".")[0].rstrip(" ").upper() in RESERVED:
        return "name_reserved"
    return None


def fold_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def check_component(name: str) -> str:
    """Defence in depth under every Dir call: one plain path component."""
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise GateError(EXIT_USAGE, "bad_component", printable(name))
    return name


# ---------------------------------------------------------------- directory handle


class Dir:
    """A directory opened once; every call afterwards is relative to it. dir_fd path (production) or path fallback."""

    def __init__(self, path: str, fd):
        self.path, self.fd = path, fd

    @classmethod
    def open_root(cls, path: str) -> "Dir":
        if HAVE_DIRFD:
            return cls(path, os.open(path, os.O_RDONLY | _O_DIR | _CLOEXEC))
        if not os.path.isdir(path):
            raise FileNotFoundError(errno.ENOENT, "not a directory", path)
        return cls(path, None)

    def _p(self, name: str) -> str:
        return os.path.join(self.path, check_component(name))

    def sub(self, name: str) -> "Dir":
        check_component(name)
        if self.fd is not None:
            return Dir(self._p(name), os.open(name, os.O_RDONLY | _O_DIR | _NOFOLLOW | _CLOEXEC, dir_fd=self.fd))
        st = os.lstat(self._p(name))
        if not stat.S_ISDIR(st.st_mode):
            raise NotADirectoryError(errno.ENOTDIR, "not a directory", name)
        return Dir(self._p(name), None)

    def open(self, name: str, flags: int, mode: int = 0o644) -> int:
        check_component(name)
        flags |= _BIN | _CLOEXEC
        if self.fd is not None:
            return os.open(name, flags | _NOFOLLOW, mode, dir_fd=self.fd)
        try:  # fallback: refuse a symlink by hand (racy, test platforms only)
            if stat.S_ISLNK(os.lstat(self._p(name)).st_mode):
                raise OSError(errno.ELOOP, "symlink", name)
        except FileNotFoundError:
            pass
        return os.open(self._p(name), flags, mode)

    def lstat(self, name: str):
        check_component(name)
        if self.fd is not None:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        return os.lstat(self._p(name))

    def unlink(self, name: str) -> None:
        check_component(name)
        if self.fd is not None:
            os.unlink(name, dir_fd=self.fd)
        else:
            os.unlink(self._p(name))

    def mkdir(self, name: str, mode: int = 0o700) -> None:
        check_component(name)
        if self.fd is not None:
            os.mkdir(name, mode, dir_fd=self.fd)
        else:
            os.mkdir(self._p(name), mode)

    def rmdir(self, name: str) -> None:
        check_component(name)
        if self.fd is not None:
            os.rmdir(name, dir_fd=self.fd)
        else:
            os.rmdir(self._p(name))

    def listdir(self):
        return os.listdir(self.fd) if self.fd is not None else os.listdir(self.path)

    def replace_to(self, name: str, other: "Dir", new_name: str) -> None:
        """os.replace of our `name` onto other's `new_name` (atomic on one filesystem)."""
        check_component(name), check_component(new_name)
        if self.fd is not None and other.fd is not None:
            os.replace(name, new_name, src_dir_fd=self.fd, dst_dir_fd=other.fd)
        else:
            os.replace(self._p(name), other._p(new_name))

    def fsync(self) -> None:
        if self.fd is not None:
            try:
                os.fsync(self.fd)
            except OSError:
                pass

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def remove_tree(parent: Dir, name: str) -> None:
    """Delete `name` under `parent` without following any symlink; a missing name is fine."""
    try:
        st = parent.lstat(name)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        child = parent.sub(name)
        try:
            for entry in child.listdir():
                remove_tree(child, entry)
        finally:
            child.close()
        parent.rmdir(name)
    else:
        parent.unlink(name)


def hash_fd(fd: int):
    h, size = hashlib.sha256(), 0
    while True:
        chunk = os.read(fd, CHUNK)
        if not chunk:
            return size, h.hexdigest()
        h.update(chunk)
        size += len(chunk)


def hash_file(d: Dir, name: str):
    fd = d.open(name, os.O_RDONLY)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise GateError(EXIT_FAIL, "not_regular", printable(name))
        return hash_fd(fd)
    finally:
        os.close(fd)


def write_all(fd: int, data) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def write_atomic(d: Dir, name: str, data: bytes) -> None:
    """Temp file in the same directory, fsync, os.replace, fsync of the directory: readers see the old or the new file."""
    tmp = "%s.tmp-%d-%s" % (name, os.getpid(), uuid.uuid4().hex[:8])
    fd = d.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        try:
            write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        d.replace_to(tmp, d, name)
    except BaseException:
        try:
            d.unlink(tmp)
        except OSError:
            pass
        raise
    d.fsync()


# ---------------------------------------------------------------- lock


class Lock:
    """flock of work/gate.lock (fcntl on POSIX; msvcrt byte lock is the fallback used by the tests on Windows)."""

    def __init__(self, work: Dir, timeout: float):
        self.work, self.timeout, self.fd = work, timeout, None

    def __enter__(self):
        self.fd = self.work.open("gate.lock", os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._try()
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(self.fd)
                    self.fd = None
                    raise GateError(EXIT_USAGE, "lock_busy", "another gate command holds work/gate.lock")
                time.sleep(0.05)

    def _try(self):
        try:
            import fcntl
        except ImportError:
            import msvcrt
            os.lseek(self.fd, 0, os.SEEK_SET)
            msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            return
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(self, *exc):
        try:
            try:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except ImportError:
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(self.fd)
            self.fd = None


# ---------------------------------------------------------------- config

DEFAULTS = {
    "max_member_mb": "200", "max_members": "5000", "max_batch_mb": "600", "free_floor_pct": "10",
    "replace_day_min": "200", "replace_day_pct": "5", "sweep_age_hours": "6", "lock_timeout_s": "60",
    "pull_min_age_s": "60", "bug_max_file_mb": "10", "bug_max_total_mb": "100", "probe_require_other_read": "true",
}


class Context:
    def __init__(self, env: str, section, stdin, stdout):
        self.env, self.stdin, self.stdout = env, stdin, stdout
        root = "/srv/memes/%s" % env
        self.paths = {
            "base": section.get("base_path", root + "/images"),
            "work": section.get("work_dir", root + "/work"),
            "incoming": section.get("incoming_dir", root + "/staging/incoming"),
            "bug": section.get("bug_reports_dir", root + "/bug-reports"),
            "backups": section.get("backups_dir", root + "/backups"),
            "facts": section.get("facts_dir", "/var/lib/memes/%s" % env),
            "postgres": section.get("postgres_path", ""),
        }
        try:
            self.max_member = int(float(section.get("max_member_mb", DEFAULTS["max_member_mb"])) * MIB)
            self.max_members = int(section.get("max_members", DEFAULTS["max_members"]))
            self.max_batch = int(float(section.get("max_batch_mb", DEFAULTS["max_batch_mb"])) * MIB)
            self.floor_pct = float(section.get("free_floor_pct", DEFAULTS["free_floor_pct"]))
            self.replace_min = int(section.get("replace_day_min", DEFAULTS["replace_day_min"]))
            self.replace_pct = float(section.get("replace_day_pct", DEFAULTS["replace_day_pct"]))
            self.sweep_age = float(section.get("sweep_age_hours", DEFAULTS["sweep_age_hours"])) * 3600
            self.lock_timeout = float(section.get("lock_timeout_s", DEFAULTS["lock_timeout_s"]))
            self.pull_min_age = float(section.get("pull_min_age_s", DEFAULTS["pull_min_age_s"]))
            self.bug_max_file = int(float(section.get("bug_max_file_mb", DEFAULTS["bug_max_file_mb"])) * MIB)
            self.bug_max_total = int(float(section.get("bug_max_total_mb", DEFAULTS["bug_max_total_mb"])) * MIB)
            self.probe_other_read = section.get("probe_require_other_read", DEFAULTS["probe_require_other_read"]).strip().lower() in ("1", "true", "yes", "on")
        except ValueError as exc:
            raise GateError(EXIT_USAGE, "config_value", str(exc))
        self._open = []

    def dir(self, key: str) -> Dir:
        try:
            d = Dir.open_root(self.paths[key])
        except OSError as exc:
            raise GateError(EXIT_PRECONDITION, "dir_missing", "%s: %s" % (key, errno.errorcode.get(exc.errno, exc.errno)))
        self._open.append(d)
        return d

    def close(self):
        for d in self._open:
            d.close()

    def caps(self) -> dict:
        return {
            "max_member_mb": self.max_member / MIB, "max_members": self.max_members, "max_batch_mb": self.max_batch / MIB,
            "free_floor_pct": self.floor_pct, "replace_day_min": self.replace_min, "replace_day_pct": self.replace_pct,
        }

    def free(self, key: str):
        """(free bytes, total bytes, free percent) of the filesystem holding a configured path."""
        du = shutil.disk_usage(self.paths[key])
        return du.free, du.total, (100.0 * du.free / du.total if du.total else 0.0)

    def emit(self, obj) -> None:
        self.stdout.write((json.dumps(obj, sort_keys=True) + "\n").encode("utf-8"))


def read_names(ctx: Context, limit: int = 64 * MIB):
    """NUL-separated names from stdin; every one is decoded as strict UTF-8 and validated by the caller."""
    data = ctx.stdin.read(limit + 1)
    if len(data) > limit:
        raise GateError(EXIT_USAGE, "stdin_too_large")
    if not data:
        return []
    parts = data.split(b"\x00")
    if parts[-1] == b"":
        parts.pop()
    out = []
    for raw in parts:
        try:
            out.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            raise GateError(EXIT_USAGE, "stdin_not_utf8")
    return out


def read_fields(ctx: Context, width: int):
    flat = read_names(ctx)
    if len(flat) % width:
        raise GateError(EXIT_USAGE, "stdin_record_shape")
    return [tuple(flat[i:i + width]) for i in range(0, len(flat), width)]


def valid_names(names, *, regex=None):
    seen = set()
    for n in names:
        problem = name_problem(n) if regex is None else (None if regex.fullmatch(n) else "name_pattern")
        if problem:
            raise GateError(EXIT_USAGE, problem, printable(n))
        if n in seen:
            raise GateError(EXIT_USAGE, "name_duplicate", printable(n))
        seen.add(n)
    return names


# ---------------------------------------------------------------- the record (work/record.tsv)
# One line per file: name TAB sha256 TAB size TAB mtime_ns. Appended by commit, rewritten atomically by hash.
# The last line of a name wins; a torn last line (kill mid-write) is ignored.


def load_record(work: Dir) -> dict:
    try:
        fd = work.open("record.tsv", os.O_RDONLY)
    except FileNotFoundError:
        return {}
    try:
        chunks = []
        while True:
            c = os.read(fd, CHUNK)
            if not c:
                break
            chunks.append(c)
    finally:
        os.close(fd)
    out = {}
    for line in b"".join(chunks).decode("utf-8", "replace").split("\n"):
        f = line.split("\t")
        if len(f) == 4 and SHA_RE.fullmatch(f[1]) and _ascii_digits(f[2]) and _ascii_digits(f[3]):
            out[f[0]] = (f[1], int(f[2]), int(f[3]))
    return out


def _record_lines(entries: dict) -> bytes:
    return "".join("%s\t%s\t%d\t%d\n" % (n, e[0], e[1], e[2]) for n, e in sorted(entries.items())).encode("utf-8")


def rewrite_record(work: Dir, entries: dict) -> None:
    write_atomic(work, "record.tsv", _record_lines(entries))


def append_record(work: Dir, entries: dict) -> None:
    if not entries:
        return
    fd = work.open("record.tsv", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        write_all(fd, _record_lines(entries))
        os.fsync(fd)
    finally:
        os.close(fd)


def trusted_hash(rec: dict, name: str, st):
    """The recorded sha256 of `name`, only if the file still has the size and mtime recorded with it."""
    e = rec.get(name)
    if e and e[1] == st.st_size and e[2] == st.st_mtime_ns:
        return e[0]
    return None


# ---------------------------------------------------------------- verbs: handshake, list, hash


def entry_type(name: str, mode: int) -> str:
    try:
        name.encode("utf-8")
        listable = not any(ord(c) < 0x20 or ord(c) == 0x7F for c in name)
    except UnicodeEncodeError:
        listable = False
    if not listable:
        return "invalid"
    if name.startswith("."):
        return "h"
    for test, letter in ((stat.S_ISREG, "f"), (stat.S_ISLNK, "l"), (stat.S_ISDIR, "d"), (stat.S_ISFIFO, "p"),
                         (stat.S_ISSOCK, "s"), (stat.S_ISCHR, "c"), (stat.S_ISBLK, "b")):
        if test(mode):
            return letter
    return "o"


def device_of(d: Dir) -> int:
    return os.fstat(d.fd).st_dev if d.fd is not None else os.stat(d.path).st_dev


def verb_handshake(ctx: Context, args):
    base, work = ctx.dir("base"), ctx.dir("work")
    incoming = ctx.dir("incoming")
    devs = {"base": device_of(base), "work": device_of(work)}
    if devs["base"] != devs["work"]:
        raise GateError(EXIT_PRECONDITION, "device_mismatch", "work dir and images are on different filesystems")
    devs["incoming"] = os.stat(incoming.path).st_dev
    free, total, pct = ctx.free("base")
    if pct < ctx.floor_pct:
        raise GateError(EXIT_PRECONDITION, "free_space_below_floor", "%.1f%% free, floor %.1f%%" % (pct, ctx.floor_pct))
    pg = None
    if ctx.paths["postgres"]:
        try:
            pf, pt, pp = ctx.free("postgres")
            pg = {"free_bytes": pf, "total_bytes": pt, "free_pct": round(pp, 2)}
        except OSError:
            raise GateError(EXIT_PRECONDITION, "postgres_path_unreadable")
    probe = {"umask": "0o022"}
    if hasattr(os, "getuid"):
        mode = stat.S_IMODE(os.stat(base.path).st_mode)
        probe.update(uid=os.getuid(), gid=os.getgid(), base_mode=oct(mode), other_readable=(mode & 0o005) == 0o005)
        if ctx.probe_other_read and not probe["other_readable"]:
            raise GateError(EXIT_PRECONDITION, "probe_unreadable", "the images directory is not readable by 'other' (backend uid)")
    ctx.emit({"environment": ctx.env, "version": GATE_VERSION, "devices": devs,
              "free": {"files": {"free_bytes": free, "total_bytes": total, "free_pct": round(pct, 2)}, "postgres": pg},
              "caps": ctx.caps(), "probe": probe, "dirfd": HAVE_DIRFD})


def verb_list(ctx: Context, args):
    base, work = ctx.dir("base"), ctx.dir("work")
    rec = load_record(work)
    out = bytearray()
    for name in sorted(base.listdir()):
        try:
            st = base.lstat(name)
        except FileNotFoundError:
            continue
        typ = entry_type(name, st.st_mode)
        shown = printable(name, 1000) if typ == "invalid" else name
        sha = (trusted_hash(rec, name, st) or "") if typ == "f" else ""
        out += ("%s\t%d\t%s\t%d\t%d\t%s\t%s" % (typ, st.st_size, sha, st.st_nlink, st.st_uid, oct(stat.S_IMODE(st.st_mode))[2:], shown)).encode("utf-8") + b"\x00"
    ctx.stdout.write(bytes(out))


def _ascii_digits(text: str) -> bool:
    """Only 0-9: str.isdigit() accepts characters such as superscript two that int() rejects."""
    return bool(text) and text.isascii() and text.isdecimal()


def verb_hash(ctx: Context, args):
    base, work = ctx.dir("base"), ctx.dir("work")
    named = "--names" in args
    names = valid_names(read_names(ctx)) if named else None
    rec = load_record(work)
    out = bytearray()
    new = {} if names is None else dict(rec)
    targets = names if names is not None else sorted(n for n in base.listdir() if not name_problem(n))
    for name in targets:
        try:
            st = base.lstat(name)
            if not stat.S_ISREG(st.st_mode):
                status = "notregular"
            else:
                size, sha = hash_file(base, name)
                st = base.lstat(name)
                new[name] = (sha, size, st.st_mtime_ns)
                out += ("ok\t%d\t%s\t%s" % (size, sha, name)).encode("utf-8") + b"\x00"
                continue
        except FileNotFoundError:
            status = "missing"
            new.pop(name, None)
        out += ("%s\t0\t\t%s" % (status, name)).encode("utf-8") + b"\x00"
    if names is None:  # a full hash drops record entries of files that are gone
        wanted = set(targets)  # built once
        new = {n: e for n, e in new.items() if n in wanted}
    rewrite_record(work, new)
    ctx.stdout.write(bytes(out))


# ---------------------------------------------------------------- the strict extractor

ALLOWED_PAX = {"path", "size", "mtime", "atime", "ctime", "uid", "gid", "uname", "gname", "charset", "hdrcharset", "comment"}


class Violation(GateError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(EXIT_FAIL, reason, detail)


def extract_strict(src, dest: Dir, *, max_member: int, max_members: int, max_batch: int):
    """Stream `src` (a tar) into `dest` by plain file names. Returns [(name, size, sha256)]. Raises Violation.

    Accepts a member only if it is a regular file (type 0 / NUL: no link, device, FIFO, directory, sparse, contiguous),
    the final name (after any PAX/GNU long name override) passes name_problem, no other name folds to the same NFC
    casefold key, sizes and counts are within the caps. Written with O_CREAT|O_EXCL|O_NOFOLLOW by name only.
    """
    try:
        tar = tarfile.open(fileobj=src, mode="r|", errorlevel=1)
    except tarfile.TarError as exc:
        raise Violation("bad_tar", type(exc).__name__)
    entries, folded, total = [], set(), 0
    try:
        for member in tar:
            if len(entries) + 1 > max_members:
                raise Violation("too_many_members", str(max_members))
            if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.issparse():
                raise Violation("member_not_regular", "%s type=%r" % (printable(member.name), member.type))
            for key in member.pax_headers:
                if key not in ALLOWED_PAX:
                    raise Violation("pax_header", printable(key))
            name = member.name
            problem = name_problem(name)
            if problem:
                raise Violation(problem, printable(name))
            key = fold_key(name)
            if key in folded:
                raise Violation("name_duplicate", printable(name))
            folded.add(key)
            if member.size < 0 or member.size > max_member:
                raise Violation("member_too_large", "%s %d" % (printable(name), member.size))
            total += member.size
            if total > max_batch:
                raise Violation("batch_too_large", str(total))
            reader = tar.extractfile(member)
            fd = dest.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                h, written = hashlib.sha256(), 0
                while True:
                    chunk = reader.read(CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > member.size:
                        raise Violation("member_size_mismatch", printable(name))
                    h.update(chunk)
                    write_all(fd, chunk)
                if written != member.size:
                    raise Violation("member_size_mismatch", printable(name))
                os.fsync(fd)
            finally:
                os.close(fd)
            entries.append((name, written, h.hexdigest()))
    except tarfile.TarError as exc:
        raise Violation("bad_tar", type(exc).__name__)
    except EOFError:
        raise Violation("bad_tar", "truncated")
    except FileExistsError:
        raise Violation("name_duplicate", "exists")
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EEXIST):
            raise Violation("name_duplicate", "exists")
        raise
    finally:
        tar.close()
    return entries


# ---------------------------------------------------------------- verbs: receive, verify, commit, sweep


def _batch_id(args) -> str:
    if len(args) != 1 or not BATCH_ID_RE.fullmatch(args[0]):
        raise GateError(EXIT_USAGE, "bad_batch_id", printable(args[0] if args else ""))
    return args[0]


def remove_batch(work: Dir, bid: str) -> None:
    for name in (bid, bid + ".tar", bid + ".list", bid + ".verified"):
        try:
            remove_tree(work, name)
        except FileNotFoundError:
            pass


def load_list(work: Dir, bid: str):
    try:
        fd = work.open(bid + ".list", os.O_RDONLY)
    except FileNotFoundError:
        raise GateError(EXIT_FAIL, "no_such_batch", bid)
    try:
        chunks = []
        while True:
            c = os.read(fd, CHUNK)
            if not c:
                break
            chunks.append(c)
    finally:
        os.close(fd)
    out = []
    for line in b"".join(chunks).decode("utf-8").splitlines():
        name, size, sha = line.split("\t")
        out.append((name, int(size), sha))
    return out


def verb_receive(ctx: Context, args):
    bid = _batch_id(args)
    work, incoming = ctx.dir("work"), ctx.dir("incoming")
    for leftover in (bid, bid + ".tar", bid + ".list", bid + ".verified"):
        try:
            work.lstat(leftover)
        except FileNotFoundError:
            continue
        raise GateError(EXIT_FAIL, "batch_exists", bid)
    try:
        sfd = incoming.open(bid + ".tar", os.O_RDONLY)
    except FileNotFoundError:
        raise GateError(EXIT_FAIL, "no_such_upload", bid)
    except OSError as exc:
        raise GateError(EXIT_FAIL, "upload_unreadable", errno.errorcode.get(exc.errno, str(exc.errno)))
    try:
        st = os.fstat(sfd)
        if not stat.S_ISREG(st.st_mode):
            raise GateError(EXIT_FAIL, "upload_not_regular", bid)
        tar_cap = ctx.max_batch + ctx.max_members * 2048 + MIB  # payload cap plus headers
        if st.st_size > tar_cap:
            raise GateError(EXIT_FAIL, "upload_too_large", str(st.st_size))
        free, total, _ = ctx.free("work")
        need = 2 * st.st_size  # the copy and the extracted files
        if total and 100.0 * (free - need) / total < ctx.floor_pct:
            raise GateError(EXIT_PRECONDITION, "free_space_below_floor", "batch does not fit")
        created = False
        try:
            dfd = work.open(bid + ".tar", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created = True
            try:  # copy out of the jail: from here on the upload account cannot change what is read
                copied = 0
                while True:
                    chunk = os.read(sfd, CHUNK)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > tar_cap:
                        raise GateError(EXIT_FAIL, "upload_too_large", str(copied))
                    write_all(dfd, chunk)
                os.fsync(dfd)
            finally:
                os.close(dfd)
            work.mkdir(bid, 0o700)
            dest = work.sub(bid)
            try:
                rfd = work.open(bid + ".tar", os.O_RDONLY)
                try:
                    with os.fdopen(rfd, "rb", closefd=False) as fobj:
                        entries = extract_strict(fobj, dest, max_member=ctx.max_member, max_members=ctx.max_members, max_batch=ctx.max_batch)
                finally:
                    os.close(rfd)
            finally:
                dest.close()
            listing = "".join("%s\t%d\t%s\n" % e for e in sorted(entries)).encode("utf-8")
            lfd = work.open(bid + ".list", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                write_all(lfd, listing)
                os.fsync(lfd)
            finally:
                os.close(lfd)
            work.unlink(bid + ".tar")
        except BaseException:
            if created:
                remove_batch(work, bid)
            raise
    except GateError as exc:
        if exc.code == EXIT_FAIL and exc.reason not in ("batch_exists", "no_such_upload"):
            os.close(sfd)  # (Windows cannot unlink an open file)
            sfd = None
            try:
                incoming.unlink(bid + ".tar")  # a rejected archive is not kept
            except OSError:
                pass
        raise
    finally:
        if sfd is not None:
            os.close(sfd)
    try:
        incoming.unlink(bid + ".tar")
    except OSError:
        pass
    ctx.emit({"id": bid, "members": len(entries), "bytes": sum(e[1] for e in entries)})


def verb_verify(ctx: Context, args):
    bid = _batch_id(args)
    work = ctx.dir("work")
    listing = load_list(work, bid)
    sums = {}
    for sha, size, name in read_fields(ctx, 3):
        if name in sums or not SHA_RE.fullmatch(sha) or not _ascii_digits(size) or name_problem(name):
            raise GateError(EXIT_USAGE, "bad_sums", printable(name))
        sums[name] = (sha, int(size))
    problems = []
    expected = {n: (sha, size) for n, size, sha in listing}
    if set(sums) != set(expected):
        problems.append("name_set")
    try:
        batch = work.sub(bid)
    except OSError:
        raise GateError(EXIT_FAIL, "no_such_batch", bid)
    try:
        if set(batch.listdir()) != set(expected):
            problems.append("directory_content")
        for name, (sha, size) in expected.items():
            try:
                actual_size, actual_sha = hash_file(batch, name)
            except (OSError, GateError):
                problems.append("unreadable")
                continue
            if (actual_sha, actual_size) != (sha, size):
                problems.append("changed_since_extraction")
            if name in sums and sums[name] != (actual_sha, actual_size):
                problems.append("sum_mismatch")
    finally:
        batch.close()
    if problems:
        remove_batch(work, bid)
        raise GateError(EXIT_FAIL, "verify_failed", ",".join(sorted(set(problems))))
    fd = work.open(bid + ".verified", os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    os.close(fd)
    ctx.emit({"id": bid, "verified": len(expected)})


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_counter(work: Dir) -> int:
    try:
        fd = work.open("counters.json", os.O_RDONLY)
    except FileNotFoundError:
        return 0
    try:
        raw = b""
        while True:
            c = os.read(fd, 65536)
            if not c:
                break
            raw += c
    finally:
        os.close(fd)
    try:
        data = json.loads(raw.decode("utf-8"))
        return int(data["replaced"]) if data.get("day") == _utc_day() else 0
    except (ValueError, KeyError, TypeError):
        return 0


def verb_commit(ctx: Context, args):
    bid = _batch_id(args)
    base, work = ctx.dir("base"), ctx.dir("work")
    listing = load_list(work, bid)
    try:
        work.lstat(bid + ".verified")
    except FileNotFoundError:
        raise GateError(EXIT_FAIL, "not_verified", bid)
    batch = work.sub(bid)
    try:
        present = set(batch.listdir())
        names = {n for n, _, _ in listing}
        if not present <= names:
            remove_batch(work, bid)
            raise GateError(EXIT_FAIL, "batch_content_changed", bid)
        rec = load_record(work)
        publish, identical, replaced = [], [], []
        for name, size, sha in sorted(listing):
            if name_problem(name):  # the list is ours, but nothing reaches os.replace unvalidated
                raise GateError(EXIT_FAIL, "bad_name_in_list", printable(name))
            if name not in present:  # published by an earlier, interrupted commit of this batch: it must be there unchanged
                try:
                    ok = hash_file(base, name) == (size, sha)
                except (OSError, GateError):
                    ok = False
                if not ok:
                    raise GateError(EXIT_FAIL, "batch_content_changed", printable(name))
                identical.append((name, size, sha))
                continue
            if hash_file(batch, name) != (size, sha):
                remove_batch(work, bid)
                raise GateError(EXIT_FAIL, "changed_since_verify", printable(name))
            try:
                st = base.lstat(name)
            except FileNotFoundError:
                publish.append((name, size, sha))
                continue
            if not stat.S_ISREG(st.st_mode):
                raise GateError(EXIT_FAIL, "destination_not_regular", printable(name))
            have = trusted_hash(rec, name, st)
            if have is None:
                have = hash_file(base, name)[1]
            if have == sha:
                identical.append((name, size, sha))
            else:
                publish.append((name, size, sha))
                replaced.append(name)
        total_files = sum(1 for n in base.listdir() if stat.S_ISREG(base.lstat(n).st_mode)) if replaced else 0
        limit = max(ctx.replace_min, int(-(-ctx.replace_pct * total_files // 100)))
        today = _load_counter(work)
        if today + len(replaced) > limit:
            raise GateError(EXIT_PRECONDITION, "replace_cap", "%d replaced today + %d planned > %d" % (today, len(replaced), limit))
        if replaced:
            write_atomic(work, "counters.json", json.dumps({"day": _utc_day(), "replaced": today + len(replaced)}).encode("utf-8"))
        done = {}
        try:
            for name, size, sha in publish:
                batch.replace_to(name, base, name)
                st = base.lstat(name)
                done[name] = (sha, st.st_size, st.st_mtime_ns)
        finally:
            for name, size, sha in identical:
                st = base.lstat(name)
                done[name] = (sha, st.st_size, st.st_mtime_ns)
            append_record(work, done)
        base.fsync()
    finally:
        batch.close()
    remove_batch(work, bid)
    if len(rec) + len(done) > 2 * (len(rec) + 1000):
        rewrite_record(work, {**rec, **done})
    ctx.emit({"id": bid, "committed": len(publish), "replaced": len(replaced), "identical": len(identical)})


BATCH_DEBRIS_RE = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?:\.tar|\.list|\.verified|\.tar\.part)?|(?:record\.tsv|counters\.json)\.tmp-.*)")


def verb_sweep(ctx: Context, args):
    work, incoming = ctx.dir("work"), ctx.dir("incoming")
    now = time.time()
    removed = {"work": 0, "incoming": 0}
    for key, d, pattern in (("work", work, BATCH_DEBRIS_RE), ("incoming", incoming, None)):
        for name in d.listdir():
            if key == "work" and (name in ("record.tsv", "counters.json", "gate.lock") or not pattern.fullmatch(name)):
                continue
            try:
                st = d.lstat(name)
                if now - st.st_mtime > ctx.sweep_age:
                    remove_tree(d, name)
                    removed[key] += 1
            except FileNotFoundError:
                continue
    ctx.emit({"removed": removed})


# ---------------------------------------------------------------- P4 verbs: bug reports


def _bug_candidates(ctx: Context):
    bug = ctx.dir("bug")
    now = time.time()
    good, anomalies = [], []
    for name in sorted(bug.listdir()):
        try:
            st = bug.lstat(name)
        except FileNotFoundError:
            continue
        if now - st.st_mtime < ctx.pull_min_age:
            continue  # settling (or being written): neither listed nor reported
        typ = entry_type(name, st.st_mode)
        if typ != "f":
            anomalies.append(("anomaly:" + ("hidden" if typ == "h" else "invalid_name" if typ == "invalid" else "not_regular:" + typ), name, st.st_size))
        elif not BUG_NAME_RE.fullmatch(name):
            anomalies.append(("anomaly:name", name, st.st_size))
        elif st.st_nlink != 1:
            anomalies.append(("anomaly:nlink", name, st.st_size))
        elif st.st_size > ctx.bug_max_file:
            anomalies.append(("anomaly:too_big", name, st.st_size))
        else:
            good.append((name, st.st_size))
    return bug, good, anomalies


def verb_pull_list(ctx: Context, args):
    bug, good, anomalies = _bug_candidates(ctx)
    out = bytearray()
    for name, _ in good:
        try:
            size, sha = hash_file(bug, name)
        except (OSError, GateError):
            anomalies.append(("anomaly:unreadable", name, 0))
            continue
        out += ("f\t%d\t%s\t%s" % (size, sha, name)).encode("utf-8") + b"\x00"
    for kind, name, size in anomalies:
        out += ("%s\t%d\t\t%s" % (kind, size, printable(name, 300))).encode("utf-8") + b"\x00"
    ctx.stdout.write(bytes(out))


def verb_pull_tar(ctx: Context, args):
    names = valid_names(read_names(ctx), regex=BUG_NAME_RE)
    if len(names) > ctx.max_members:
        raise GateError(EXIT_PRECONDITION, "too_many_names")
    bug = ctx.dir("bug")
    total = 0
    for name in names:  # check everything before the first byte goes out
        try:
            st = bug.lstat(name)
        except FileNotFoundError:
            raise GateError(EXIT_FAIL, "no_such_file", name)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise GateError(EXIT_FAIL, "not_regular", name)
        if st.st_size > ctx.bug_max_file:
            raise GateError(EXIT_PRECONDITION, "file_too_big", name)
        total += st.st_size
    if total > ctx.bug_max_total:
        raise GateError(EXIT_PRECONDITION, "pull_too_big", str(total))
    tar = tarfile.open(fileobj=ctx.stdout, mode="w|", format=tarfile.PAX_FORMAT)
    for name in names:
        fd = bug.open(name, os.O_RDONLY)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise GateError(EXIT_FAIL, "not_regular", name)
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = st.st_size, int(st.st_mtime), 0o644
            with os.fdopen(os.dup(fd), "rb") as fobj:
                tar.addfile(info, fobj)
        except (OSError, tarfile.TarError, ValueError) as exc:  # file shrank or vanished: the client's sha256 check also catches it
            raise GateError(EXIT_FAIL, "read_failed", type(exc).__name__)
        finally:
            os.close(fd)
    tar.close()


def verb_delete_verified(ctx: Context, args):
    pairs = read_fields(ctx, 2)
    seen = set()
    for sha, name in pairs:
        if not SHA_RE.fullmatch(sha) or not BUG_NAME_RE.fullmatch(name) or name in seen:
            raise GateError(EXIT_USAGE, "bad_pair", printable(name))
        seen.add(name)
    bug = ctx.dir("bug")
    out, bad = bytearray(), False
    for sha, name in pairs:
        try:
            before = bug.lstat(name)
            if not stat.S_ISREG(before.st_mode):
                status = "not_regular"
            else:
                size, actual = hash_file(bug, name)
                after = bug.lstat(name)
                if actual != sha:
                    status = "mismatch"
                elif (after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns):
                    status = "mismatch"  # changed while it was hashed: keep it
                else:
                    bug.unlink(name)
                    status = "deleted"
        except FileNotFoundError:
            status = "missing"
        if status not in ("deleted", "missing"):
            bad = True
        out += ("%s\t%s" % (status, name)).encode("utf-8") + b"\x00"
    ctx.stdout.write(bytes(out))
    if bad:
        raise GateError(EXIT_FAIL, "delete_refused", "see stdout")


# ---------------------------------------------------------------- P5 verbs: fetch-backup, disk, containment


def _read_small(d: Dir, name: str, cap: int) -> bytes:
    fd = d.open(name, os.O_RDONLY)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > cap:
            raise GateError(EXIT_PRECONDITION, "facts_unusable", name)
        raw = b""
        while True:
            c = os.read(fd, 65536)
            if not c:
                return raw
            raw += c
            if len(raw) > cap:
                raise GateError(EXIT_PRECONDITION, "facts_unusable", name)
    finally:
        os.close(fd)


def verb_fetch_backup(ctx: Context, args):
    """Header line (JSON) then, unless --meta, exactly `size` bytes of the newest `*.dump` of this environment.

    Metadata comes from the sidecar `<dump>.meta.json` that backup.sh writes (size, mtime, pg_restore --list result)."""
    if any(a not in ("--meta",) for a in args):
        raise GateError(EXIT_USAGE, "bad_args")
    backups = ctx.dir("backups")
    best = None
    for name in backups.listdir():
        if not BACKUP_NAME_RE.fullmatch(name):
            continue
        try:
            st = backups.lstat(name)
        except FileNotFoundError:
            continue
        if stat.S_ISREG(st.st_mode) and (best is None or (st.st_mtime_ns, name) > (best[1].st_mtime_ns, best[0])):
            best = (name, st)
    if best is None:
        raise GateError(EXIT_PRECONDITION, "no_backup")
    name, st = best
    meta = None
    try:
        meta = json.loads(_read_small(backups, name[:-5] + ".meta.json", 65536).decode("utf-8"))
    except (FileNotFoundError, GateError, ValueError):
        meta = None
    fd = backups.open(name, os.O_RDONLY)
    try:
        st = os.fstat(fd)
        ctx.emit({"name": name, "size": st.st_size, "mtime": int(st.st_mtime), "meta": meta})
        if "--meta" in args:
            return
        left = st.st_size
        while left:
            chunk = os.read(fd, min(CHUNK, left))
            if not chunk:
                raise GateError(EXIT_FAIL, "backup_shrank")
            ctx.stdout.write(chunk)
            left -= len(chunk)
    finally:
        os.close(fd)


def _facts_verb(filename: str):
    def verb(ctx: Context, args):
        facts = ctx.dir("facts")
        try:
            raw = _read_small(facts, filename, MIB)
        except FileNotFoundError:
            raise GateError(EXIT_PRECONDITION, "facts_missing", filename)
        ctx.stdout.write(raw if raw.endswith(b"\n") else raw + b"\n")
    return verb


# ---------------------------------------------------------------- dispatch

# verb -> (function, takes the work lock, allowed first-argument shape)
VERBS = {
    "handshake": (verb_handshake, False),
    "list": (verb_list, False),
    "hash": (verb_hash, True),
    "receive": (verb_receive, True),
    "verify": (verb_verify, False),
    "commit": (verb_commit, True),
    "sweep": (verb_sweep, True),
    "pull-list": (verb_pull_list, False),
    "pull-tar": (verb_pull_tar, False),
    "delete-verified": (verb_delete_verified, True),
    "fetch-backup": (verb_fetch_backup, False),
    "disk": (_facts_verb("disk.json"), False),
    "containment": (_facts_verb("containment.json"), False),
}


def load_config(environ) -> configparser.ConfigParser:
    path = environ.get("MEMESYNC_GATE_CONF") or "/etc/memes/gate.conf"
    try:
        st = os.stat(path)
    except OSError:
        raise GateError(EXIT_USAGE, "config_missing", printable(path))
    if hasattr(os, "getuid") and st.st_mode & 0o022:
        raise GateError(EXIT_USAGE, "config_writable", "gate.conf must not be group or world writable")
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#",))
    try:
        with open(path, encoding="utf-8") as fh:
            cp.read_file(fh)
    except (OSError, configparser.Error) as exc:
        raise GateError(EXIT_USAGE, "config_unreadable", type(exc).__name__)
    return cp


def main(argv, environ=None, stdin=None, stdout=None, stderr=None) -> int:
    environ = os.environ if environ is None else environ
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout.buffer if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    ctx = None
    try:
        os.umask(0o022)
        if len(argv) != 2 or not re.fullmatch(r"[a-z][a-z0-9_-]{0,15}", argv[1]):
            raise GateError(EXIT_USAGE, "usage", "memesync-gate.py <env>")
        if not HAVE_DIRFD and environ.get("MEMESYNC_GATE_ALLOW_NO_DIRFD") != "1":
            raise GateError(EXIT_USAGE, "unsafe_platform", "no dir_fd support; not for production")
        command = environ.get("SSH_ORIGINAL_COMMAND", "")
        if not command or not all(0x20 <= ord(c) < 0x7F for c in command):
            raise GateError(EXIT_USAGE, "no_command")
        verb, *args = command.split()
        if verb not in VERBS:
            raise GateError(EXIT_USAGE, "unknown_verb", printable(verb))
        flags = {"hash": {"--names"}, "fetch-backup": {"--meta"}}.get(verb, set())
        if verb in ("receive", "verify", "commit"):
            _batch_id(args)
        elif len(set(args)) != len(args) or not set(args) <= flags:
            raise GateError(EXIT_USAGE, "bad_args", printable(" ".join(args)))
        cp = load_config(environ)
        if not cp.has_section(argv[1]):
            raise GateError(EXIT_USAGE, "unknown_environment", argv[1])
        ctx = Context(argv[1], cp[argv[1]], stdin, stdout)
        func, locked = VERBS[verb]
        if locked:
            with Lock(ctx.dir("work"), ctx.lock_timeout):
                func(ctx, args)
        else:
            func(ctx, args)
        stdout.flush()
        return EXIT_OK
    except GateError as exc:
        try:
            stdout.flush()
        except (OSError, ValueError):
            pass
        stderr.write("gate: %s: %s\n" % (exc.reason, exc.detail))
        return exc.code
    except tarfile.TarError as exc:
        stderr.write("gate: bad_tar: %s\n" % type(exc).__name__)
        return EXIT_FAIL
    except OSError as exc:
        stderr.write("gate: os_error: %s\n" % errno.errorcode.get(exc.errno, str(exc.errno)))
        return EXIT_FAIL
    except Exception as exc:  # last resort: one line, the exception type only (no traceback, no path, no value)
        try:
            stderr.write("gate: internal_error: %s\n" % type(exc).__name__)
        except Exception:
            pass
        return EXIT_FAIL
    finally:
        if ctx is not None:
            ctx.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
