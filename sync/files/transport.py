"""P3-T4: the transport to the prod gate (interfaces.md c, g; design P3 "Gate verbs", "Connection, identity, accounts").

`Transport` is the protocol the promote tool and `sync.connect.handshake` (step H6) use. Two implementations:

* `SshTransport`: every gate verb is one `ssh` process (full path `sync.ssh_bin`, alias `sync.prod.gate_alias`, the verb as the
  remote command, which the forced command of the verb key reads as SSH_ORIGINAL_COMMAND; stdin and stdout are piped). A tar
  batch is uploaded with `sftp -b -` (full path `sync.sftp_bin`, alias `sync.prod.upload_alias`, the chrooted upload account)
  as `/incoming/<id>.tar.part`, its size is read back with `ls -l`, and only then renamed to `/incoming/<id>.tar`.
* `LocalTransport`: the real `deploy/prod/memesync-gate.py` run in-process (or as a subprocess) against a temporary directory
  tree; the upload is a file copy into the staging incoming directory. For tests.

Standard library only. A failure that is not a normal gate answer (ssh not startable, timeout, ssh exit 255, unparsable
handshake) raises `GateFailure`, a PreconditionError (exit 2) whose `.code` is a lower-case code; gate answers with a non-zero
exit status are returned as `GateReply` for the caller to interpret (`receive` -> `batch_exists` means resume).
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from sync.config import REPO_ROOT, PreconditionError, SyncSettings

BATCH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_REASON_RE = re.compile(r"^gate: ([a-z0-9_]+): ?(.*)$")
GATE_PATH = REPO_ROOT / "deploy" / "prod" / "memesync-gate.py"
GATE_VERBS = ("handshake", "list", "hash", "receive", "verify", "commit", "sweep",  # what the files tool uses
              "pull-list", "pull-tar", "delete-verified",  # what the pull tool uses (bug reports, P4)
              "fetch-backup", "disk", "containment")  # what verify uses (P6): only `fetch-backup --meta` is ever sent, never the dump
SSH_ERROR_EXIT = 255

SHORT_TIMEOUT_S = 120.0
LONG_TIMEOUT_S = 1800.0  # receive (copy + extract), verify and commit of a batch, a full `hash`


class GateFailure(PreconditionError):
    """The gate could not be reached or answered something unusable (exit 2). `.code` is a lower-case code for reports."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or f"gate failure: {code}")
        self.code = code


@dataclass(frozen=True)
class GateReply:
    """The answer of one gate verb: exit status, stdout bytes and the one-line reason from stderr."""

    code: int
    out: bytes = b""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def reason(self) -> str:
        """`<reason>` of the gate's `gate: <reason>: <detail>` line, `` when there is none."""
        for line in self.err.splitlines():
            m = _REASON_RE.match(line.strip())
            if m:
                return m.group(1)
        return ""

    @property
    def detail(self) -> str:
        for line in self.err.splitlines():
            m = _REASON_RE.match(line.strip())
            if m:
                return m.group(2)
        return ""

    def json(self) -> dict:
        for line in self.out.decode("utf-8", "replace").splitlines():
            if line.strip():
                try:
                    value = json.loads(line)
                except ValueError:
                    break
                if isinstance(value, dict):
                    return value
                break
        raise GateFailure("reply_not_json")


class Transport(Protocol):
    """What `sync.connect.handshake` needs is `handshake()`; the promote tool uses the rest."""

    def handshake(self) -> dict: ...
    def list(self) -> bytes: ...
    def hash_all(self) -> GateReply: ...
    def upload(self, batch_id: str, local_path: str | os.PathLike) -> None: ...
    def receive(self, batch_id: str) -> GateReply: ...
    def verify(self, batch_id: str, sums: bytes) -> GateReply: ...
    def commit(self, batch_id: str) -> GateReply: ...
    def sweep(self) -> GateReply: ...
    def pull_list(self) -> GateReply: ...
    def pull_tar(self, names: list, dest: str | os.PathLike) -> GateReply: ...
    def delete_verified(self, pairs: list) -> GateReply: ...
    def disk(self) -> GateReply: ...
    def containment(self) -> GateReply: ...
    def fetch_backup_meta(self) -> GateReply: ...


def check_batch_id(batch_id: str) -> str:
    if not isinstance(batch_id, str) or not BATCH_ID_RE.match(batch_id):
        raise GateFailure("bad_batch_id")
    return batch_id


def clean_code(text: str, fallback: str = "failed") -> str:
    """A report-safe code: lower case, `[a-z0-9_.:=,-]`, at most 60 characters."""
    out = re.sub(r"[^a-z0-9_.:=,-]", "_", str(text).lower())[:60].strip("_")
    return out or fallback


def _nul_join(items) -> bytes:
    return b"".join(str(i).encode("utf-8") + b"\x00" for i in items)


class _VerbTransport:
    """The verbs, written once on top of `_run(command line, stdin, timeout)` and `upload`."""

    def _run(self, line: str, stdin: bytes, timeout: float) -> GateReply:  # pragma: no cover - abstract
        raise NotImplementedError

    def _run_to_file(self, line: str, stdin: bytes, dest: str | os.PathLike, timeout: float) -> GateReply:  # pragma: no cover - abstract
        """Like `_run`, but the verb's stdout (binary) is written to the file `dest`; the reply's `out` is empty."""
        raise NotImplementedError

    def upload(self, batch_id: str, local_path: str | os.PathLike) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def handshake(self) -> dict:
        reply = self._run("handshake", b"", SHORT_TIMEOUT_S)
        if not reply.ok:
            raise GateFailure(clean_code(reply.reason, f"gate_exit_{reply.code}"), f"gate handshake failed: {reply.reason or reply.code}")
        try:
            data = reply.json()
        except GateFailure:
            raise GateFailure("handshake_not_json") from None
        return data

    def list(self) -> bytes:
        reply = self._run("list", b"", LONG_TIMEOUT_S)
        if not reply.ok:
            raise GateFailure(clean_code(reply.reason, f"gate_exit_{reply.code}"), f"gate list failed: {reply.reason or reply.code}")
        return reply.out

    def hash_all(self) -> GateReply:
        return self._run("hash", b"", LONG_TIMEOUT_S)

    def receive(self, batch_id: str) -> GateReply:
        return self._run(f"receive {check_batch_id(batch_id)}", b"", LONG_TIMEOUT_S)

    def verify(self, batch_id: str, sums: bytes) -> GateReply:
        return self._run(f"verify {check_batch_id(batch_id)}", sums, LONG_TIMEOUT_S)

    def commit(self, batch_id: str) -> GateReply:
        return self._run(f"commit {check_batch_id(batch_id)}", b"", LONG_TIMEOUT_S)

    def sweep(self) -> GateReply:
        return self._run("sweep", b"", SHORT_TIMEOUT_S)

    # P4 bug-report verbs: names and hashes travel NUL-separated on stdin, as the gate reads them

    def pull_list(self) -> GateReply:
        return self._run("pull-list", b"", LONG_TIMEOUT_S)

    def pull_tar(self, names: list, dest: str | os.PathLike) -> GateReply:
        """The named bug reports as a tar written to the file `dest` (the stream is never held in memory)."""
        return self._run_to_file("pull-tar", _nul_join(names), dest, LONG_TIMEOUT_S)

    def delete_verified(self, pairs: list) -> GateReply:
        """`pairs` are `(sha256, name)`; the gate deletes a file only when its current hash equals the one sent."""
        return self._run("delete-verified", _nul_join([x for pair in pairs for x in pair]), LONG_TIMEOUT_S)

    # P6 (sync.verify) verbs: read-only facts. `fetch_backup_meta` asks for the header only (`--meta`); the dump itself is never fetched here.

    def disk(self) -> GateReply:
        """The root timer's `disk.json` (free space per mount of this environment); the JSON is in `reply.json()`."""
        return self._run("disk", b"", SHORT_TIMEOUT_S)

    def containment(self) -> GateReply:
        """The root timer's `containment.json` (the backend container's configuration)."""
        return self._run("containment", b"", SHORT_TIMEOUT_S)

    def fetch_backup_meta(self) -> GateReply:
        """Header line of the newest class P dump (`name`, `size`, `mtime`, `meta`), no dump bytes."""
        return self._run("fetch-backup --meta", b"", SHORT_TIMEOUT_S)


# ---------------------------------------------------------------------------------------------------------------------------------
# ssh

Runner = Callable[[list, bytes, float], tuple]  # (command, stdin, timeout) -> (returncode, stdout bytes, stderr bytes)
FileRunner = Callable[[list, bytes, str, float], tuple]  # (command, stdin, stdout file path, timeout) -> (returncode, stderr bytes)


def subprocess_runner(command: list, stdin: bytes, timeout: float) -> tuple:
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        proc = subprocess.run(command, input=stdin or b"", capture_output=True, timeout=timeout, **extra)
    except subprocess.TimeoutExpired:
        raise GateFailure("timeout", f"timed out after {timeout:g}s") from None
    except OSError as exc:
        raise GateFailure("cannot_start_ssh", f"cannot start {Path(command[0]).name}: {type(exc).__name__}") from None
    return proc.returncode, proc.stdout, proc.stderr


def subprocess_file_runner(command: list, stdin: bytes, dest: str, timeout: float) -> tuple:
    """Like `subprocess_runner`, with stdout going straight into the file `dest` (binary, no text layer, no memory copy)."""
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        with open(dest, "wb") as handle:
            proc = subprocess.run(command, input=stdin or b"", stdout=handle, stderr=subprocess.PIPE, timeout=timeout, **extra)
    except subprocess.TimeoutExpired:
        raise GateFailure("timeout", f"timed out after {timeout:g}s") from None
    except OSError as exc:
        raise GateFailure("cannot_start_ssh", f"cannot start {Path(command[0]).name}: {type(exc).__name__}") from None
    return proc.returncode, proc.stderr


def _sftp_quote(path: str) -> str:
    return '"' + path.replace("\\", "/").replace('"', '\\"') + '"'


def parse_ls_size(output: str, name: str) -> int | None:
    """Size from an `sftp ls -l` line for `name`: `perms links user group size month day time name`."""
    for line in output.splitlines():
        if line.startswith("sftp>"):
            continue
        fields = line.split()
        if len(fields) >= 9 and fields[-1].endswith(name) and fields[4].isdigit():
            return int(fields[4])
    return None


class SshTransport(_VerbTransport):
    def __init__(self, ssh_bin: str, sftp_bin: str, gate_alias: str, upload_alias: str, *, runner: Runner = subprocess_runner,
                 file_runner: FileRunner = subprocess_file_runner, connect_timeout_s: int = 20, keepalive_interval_s: int = 15, keepalive_count_max: int = 3):
        if not gate_alias or not upload_alias:
            raise GateFailure("alias_missing", "the gate and upload ssh aliases must be set")
        self.ssh_bin, self.sftp_bin = ssh_bin, sftp_bin
        self.gate_alias, self.upload_alias = gate_alias, upload_alias
        self.runner, self.file_runner = runner, file_runner
        self._opts = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", f"ConnectTimeout={connect_timeout_s}",
                      "-o", f"ServerAliveInterval={keepalive_interval_s}", "-o", f"ServerAliveCountMax={keepalive_count_max}",
                      "-o", "ClearAllForwardings=yes"]

    @classmethod
    def from_settings(cls, settings: SyncSettings, *, runner: Runner = subprocess_runner,
                      file_runner: FileRunner = subprocess_file_runner) -> "SshTransport":
        return cls(str(settings.require("ssh_bin")), str(settings.require("sftp_bin")), str(settings.require("prod.gate_alias")),
                   str(settings.require("prod.upload_alias")), runner=runner, file_runner=file_runner,
                   connect_timeout_s=int(settings.get("tunnel.connect_timeout_s", 20)),
                   keepalive_interval_s=int(settings.get("tunnel.keepalive_interval_s", 15)),
                   keepalive_count_max=int(settings.get("tunnel.keepalive_count_max", 3)))

    # -- command construction (no process is started here)

    def verb_command(self, line: str) -> list:
        verb = line.split(" ", 1)[0]
        if verb not in GATE_VERBS:
            raise GateFailure("unknown_verb")
        return [self.ssh_bin, "-T", *self._opts, self.gate_alias, line]

    def sftp_command(self) -> list:
        return [self.sftp_bin, "-q", *self._opts, "-b", "-", self.upload_alias]

    @staticmethod
    def part_path(batch_id: str) -> str:
        return f"/incoming/{check_batch_id(batch_id)}.tar.part"

    @staticmethod
    def final_path(batch_id: str) -> str:
        return f"/incoming/{check_batch_id(batch_id)}.tar"

    def upload_scripts(self, batch_id: str, local_path: str | os.PathLike) -> tuple:
        """(first batch file: put + ls -l of the .part, second: remove a stale final name and rename)."""
        part, final = self.part_path(batch_id), self.final_path(batch_id)
        first = f"put {_sftp_quote(Path(local_path).as_posix())} {part}\nls -l {part}\n"
        second = f"-rm {final}\nrename {part} {final}\n"
        return first, second

    # -- verbs

    def _run(self, line: str, stdin: bytes, timeout: float) -> GateReply:
        code, out, err = self.runner(self.verb_command(line), stdin, timeout)
        text = err.decode("utf-8", "replace")
        if code == SSH_ERROR_EXIT:
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            raise GateFailure("ssh_failed", f"ssh to the gate alias failed: {first[:200]}")
        return GateReply(code, out, text)

    def _run_to_file(self, line: str, stdin: bytes, dest: str | os.PathLike, timeout: float) -> GateReply:
        code, err = self.file_runner(self.verb_command(line), stdin, str(dest), timeout)
        text = err.decode("utf-8", "replace")
        if code == SSH_ERROR_EXIT:
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            raise GateFailure("ssh_failed", f"ssh to the gate alias failed: {first[:200]}")
        return GateReply(code, b"", text)

    def upload(self, batch_id: str, local_path: str | os.PathLike) -> None:
        size = os.path.getsize(local_path)
        first, second = self.upload_scripts(batch_id, local_path)
        timeout = 300.0 + size / (512 * 1024)
        code, out, err = self.runner(self.sftp_command(), first.encode("utf-8"), timeout)
        if code != 0:
            raise GateFailure("upload_failed", f"sftp upload failed with exit {code}: {err.decode('utf-8', 'replace').strip()[:200]}")
        remote = parse_ls_size(out.decode("utf-8", "replace"), f"{batch_id}.tar.part")
        if remote != size:
            raise GateFailure("upload_size_mismatch", f"uploaded size {remote} differs from {size}")
        code, out, err = self.runner(self.sftp_command(), second.encode("utf-8"), SHORT_TIMEOUT_S)
        if code != 0:
            raise GateFailure("upload_rename_failed", f"sftp rename failed with exit {code}: {err.decode('utf-8', 'replace').strip()[:200]}")


# ---------------------------------------------------------------------------------------------------------------------------------
# local (tests)

_GATE_MODULE: Any = None


def load_gate(path: Path = GATE_PATH):
    """The real gate file as a module (cached for the default path)."""
    global _GATE_MODULE
    if path == GATE_PATH and _GATE_MODULE is not None:
        return _GATE_MODULE
    spec = importlib.util.spec_from_file_location("memesync_gate_local_transport", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if path == GATE_PATH:
        _GATE_MODULE = module
    return module


class LocalTransport(_VerbTransport):
    """The real gate against a temporary tree. `conf_path` is the gate.conf (section name = `env`), `incoming_dir` its staging
    incoming directory. `before_verb(line)` may raise to simulate a kill before a verb runs."""

    def __init__(self, env: str, conf_path: str | os.PathLike, incoming_dir: str | os.PathLike, *, subprocess_mode: bool = False,
                 before_verb: Callable[[str], None] | None = None):
        self.env, self.conf_path, self.incoming_dir = env, str(conf_path), Path(incoming_dir)
        self.subprocess_mode = subprocess_mode
        self.before_verb = before_verb
        self.calls: list = []  # every verb line, in order (tests assert on it)

    def _environ(self, line: str) -> dict:
        environ = {"SSH_ORIGINAL_COMMAND": line, "MEMESYNC_GATE_CONF": self.conf_path}
        if os.name == "nt" or not load_gate().HAVE_DIRFD:
            environ["MEMESYNC_GATE_ALLOW_NO_DIRFD"] = "1"
        return environ

    def _run(self, line: str, stdin: bytes, timeout: float) -> GateReply:
        self.calls.append(line)
        if self.before_verb is not None:
            self.before_verb(line)
        if self.subprocess_mode:
            env = dict(os.environ)
            env.update(self._environ(line))
            proc = subprocess.run([sys.executable, str(GATE_PATH), self.env], input=stdin, capture_output=True, env=env, timeout=timeout)
            return GateReply(proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace"))
        out, err = io.BytesIO(), io.StringIO()
        code = load_gate().main(["memesync-gate.py", self.env], self._environ(line), io.BytesIO(stdin), out, err)
        return GateReply(code, out.getvalue(), err.getvalue())

    def _run_to_file(self, line: str, stdin: bytes, dest: str | os.PathLike, timeout: float) -> GateReply:
        self.calls.append(line)
        if self.before_verb is not None:
            self.before_verb(line)
        with open(dest, "wb") as handle:
            if self.subprocess_mode:
                env = dict(os.environ)
                env.update(self._environ(line))
                proc = subprocess.run([sys.executable, str(GATE_PATH), self.env], input=stdin, stdout=handle, stderr=subprocess.PIPE, env=env,
                                      timeout=timeout)
                return GateReply(proc.returncode, b"", proc.stderr.decode("utf-8", "replace"))
            err = io.StringIO()
            code = load_gate().main(["memesync-gate.py", self.env], self._environ(line), io.BytesIO(stdin), handle, err)
        return GateReply(code, b"", err.getvalue())

    def upload(self, batch_id: str, local_path: str | os.PathLike) -> None:
        self.calls.append(f"upload {batch_id}")
        part = self.incoming_dir / f"{check_batch_id(batch_id)}.tar.part"
        final = self.incoming_dir / f"{batch_id}.tar"
        shutil.copyfile(local_path, part)
        if part.stat().st_size != os.path.getsize(local_path):
            raise GateFailure("upload_size_mismatch")
        os.replace(part, final)
