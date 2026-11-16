"""The off-VPS copy of the prod backup (epic environment-data-sync, P5d, dev side).

    python -m sync.fetch_backup --env metal            # fetch the newest class P dump unless dev already holds it
    python -m sync.fetch_backup --env metal --list     # what dev holds (nothing is fetched)
    python -m sync.fetch_backup --env metal --force    # fetch again even when the newest dump is already here

Run it on the dev workstation after a pull (a dump on the same disk as the data is not a backup). It asks the gate for the newest
dump with the read-only verb `fetch-backup`, checks the answer, and keeps it under `<state dir>/backups/` (default
`Storage/sync-state/<env>/backups/`) together with its manifest. It changes nothing on prod and nothing in either database.

What is checked before a file is kept (a dump that fails any of these is deleted, never kept as "the latest"):
  * the name is `memes-<env>-classp-<UTC stamp>.dump` (the remote name is untrusted; it never becomes a path in any other form);
  * the size equals the header's `size` and the manifest's `dump.size`;
  * the sha256 equals the manifest's `dump.sha256`, and the manifest says `pg_restore_list == "ok"` and names this environment.

Retention: the newest `sync.fetch_backup.keep` (default 7) dumps are kept, older ones of this tool's own naming are removed.
Exit codes: 0 fetched or already held, 1 failure, 2 precondition (no gate, no backup on prod, lock held), 4 the fetched file failed verification.
Standard library plus sync.config and sync.files.transport.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from sync.config import (EXIT_FAILURE, EXIT_OK, PreconditionError, SyncError, SyncSettings, VerificationError, atomic_write,
                         exclusive_lock, load_settings, replace_with_retry, state_dir)
from sync.files.transport import SshTransport, Transport

TOOL = "sync.fetch_backup"
HEADER_CAP = 1 << 20  # the header line of the verb is a few hundred bytes; more than this is not a header
DEFAULT_KEEP = 7
ENVIRONMENTS = ("metal", "general", "it")
STAMP = r"\d{8}T\d{6}Z"


class FetchFailed(SyncError):
    exit_code = EXIT_FAILURE


class FetchVerificationFailed(VerificationError):
    pass


def name_re(env: str) -> "re.Pattern[str]":
    return re.compile(rf"memes-{re.escape(env)}-classp-({STAMP})\.dump")


def backups_dir(settings: SyncSettings, create: bool = False) -> Path:
    path = state_dir(settings) / "backups"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_of(path: Path, start: int = 0) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        handle.seek(start)
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def held(directory: Path, env: str) -> list:
    """`(name, size)` of the dumps dev holds, newest first (the UTC stamp in the name orders them)."""
    rx = name_re(env)
    out = []
    if directory.is_dir():
        for entry in directory.iterdir():
            if rx.fullmatch(entry.name) and entry.is_file() and not entry.is_symlink():
                out.append((entry.name, entry.stat().st_size))
    return sorted(out, reverse=True)


def split_header(raw: Path) -> tuple:
    """`(header dict, offset of the dump bytes)` from the raw output of the verb: one JSON line, then the dump."""
    with open(raw, "rb") as handle:
        line = handle.readline(HEADER_CAP + 1)
    if not line.endswith(b"\n") or len(line) > HEADER_CAP:
        raise FetchFailed("the gate's answer has no header line")
    try:
        header = json.loads(line.decode("utf-8"))
    except ValueError:
        raise FetchFailed("the gate's header line is not JSON") from None
    if not isinstance(header, dict):
        raise FetchFailed("the gate's header line is not an object")
    return header, len(line)


def check(env: str, header: dict, raw_size: int, offset: int) -> tuple:
    """Validate header and manifest against the received size. Returns `(name, size, expected sha256 or None, manifest)`.
    Raises FetchVerificationFailed for a dump that must not be kept."""
    name, size, manifest = header.get("name"), header.get("size"), header.get("meta")
    match = name_re(env).fullmatch(name) if isinstance(name, str) else None
    if not match:
        raise FetchVerificationFailed(f"the dump name {str(name)[:80]!r} is not memes-{env}-classp-<stamp>.dump")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise FetchVerificationFailed("the header has no usable size")
    if raw_size - offset != size:
        raise FetchVerificationFailed(f"received {raw_size - offset} dump bytes, the header says {size}")
    if size == 0:
        raise FetchVerificationFailed("the dump is empty")
    if not isinstance(manifest, dict):
        raise FetchVerificationFailed("prod has no manifest for this dump (backup.sh writes it last); not keeping an unverifiable copy")
    if manifest.get("pg_restore_list") != "ok":
        raise FetchVerificationFailed("the manifest does not say pg_restore_list ok")
    if manifest.get("environment") not in (None, env):
        raise FetchVerificationFailed(f"the manifest belongs to environment {str(manifest.get('environment'))[:20]!r}")
    dump = manifest.get("dump")
    if not isinstance(dump, dict) or dump.get("name") != name or dump.get("size") != size:
        raise FetchVerificationFailed("the manifest does not describe this dump (name or size differ)")
    sha = dump.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise FetchVerificationFailed("the manifest has no sha256 for the dump")
    return name, size, sha, manifest


def prune(directory: Path, env: str, keep: int) -> list:
    """Remove the dumps (and their manifests) beyond the newest `keep`; only files of this tool's own naming."""
    removed = []
    for name, _size in held(directory, env)[max(keep, 1):]:
        for victim in (directory / name, directory / (name[:-5] + ".meta.json")):
            try:
                victim.unlink()
            except FileNotFoundError:
                pass
        removed.append(name)
    return removed


def fetch(settings: SyncSettings, transport: Transport, *, force: bool = False, out=None) -> dict:
    """Fetch the newest dump when dev does not hold it. Returns `{status: fetched|current, name, size, path, pruned}`."""
    out = out or sys.stdout
    env = settings.env
    directory = backups_dir(settings, create=True)
    head = transport.fetch_backup_meta()
    if not head.ok:
        raise PreconditionError(f"prod has no backup to fetch ({head.reason or 'gate exit ' + str(head.code)})")
    header = head.json()
    name, size = header.get("name"), header.get("size")
    if not (isinstance(name, str) and name_re(env).fullmatch(name)):
        raise FetchVerificationFailed(f"the dump name {str(name)[:80]!r} is not memes-{env}-classp-<stamp>.dump")
    target = directory / name
    sidecar = directory / (name[:-5] + ".meta.json")
    if not force and target.is_file() and not target.is_symlink() and target.stat().st_size == size and sidecar.is_file():
        print(f"{TOOL}: already held: {name} ({size} bytes)", file=out)
        return {"status": "current", "name": name, "size": size, "path": str(target), "pruned": []}

    raw = directory / f"{name}.raw.part"
    final_part = directory / f"{name}.part"
    try:
        reply = transport.fetch_backup(raw)
        if not reply.ok:
            raise PreconditionError(f"the gate refused the backup: {reply.reason or reply.code}")
        got_header, offset = split_header(raw)
        got_name, got_size, sha, manifest = check(env, got_header, raw.stat().st_size, offset)
        if got_name != name:  # a newer dump appeared between the two calls: keep what was actually received and checked
            target, sidecar = directory / got_name, directory / (got_name[:-5] + ".meta.json")
            final_part = directory / f"{got_name}.part"
        if sha256_of(raw, offset) != sha:
            raise FetchVerificationFailed("the sha256 of the received dump differs from the manifest")
        with open(raw, "rb") as src, open(final_part, "wb") as dst:
            src.seek(offset)
            for chunk in iter(lambda: src.read(1 << 20), b""):
                dst.write(chunk)
        replace_with_retry(final_part, target)
        atomic_write(sidecar, json.dumps(manifest, sort_keys=True, indent=1) + "\n")
    finally:
        for leftover in (raw, final_part):
            try:
                leftover.unlink()
            except FileNotFoundError:
                pass
    pruned = prune(directory, env, int(settings.get("fetch_backup.keep", DEFAULT_KEEP) or DEFAULT_KEEP))
    print(f"{TOOL}: fetched {target.name} ({got_size} bytes, sha256 {sha[:12]}...)", file=out)
    if pruned:
        print(f"{TOOL}: removed {len(pruned)} older dump(s)", file=out)
    return {"status": "fetched", "name": target.name, "size": got_size, "path": str(target), "pruned": pruned}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m sync.fetch_backup", description="Fetch the newest prod class P dump to dev (off-VPS copy).")
    p.add_argument("--env", required=True, choices=ENVIRONMENTS)
    p.add_argument("--list", action="store_true", help="list the dumps dev holds and exit (no connection to prod)")
    p.add_argument("--force", action="store_true", help="fetch again even when the newest dump is already held")
    return p


def main(argv: list | None = None, *, settings: SyncSettings | None = None, transport_factory: Callable[[SyncSettings], Any] | None = None,
         out=None) -> int:
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    try:
        settings = settings or load_settings(args.env)
        if args.list:
            rows = held(backups_dir(settings), settings.env)
            for name, size in rows:
                print(f"{name}\t{size}", file=out)
            if not rows:
                print(f"{TOOL}: no dump held for {settings.env} yet", file=out)
            return EXIT_OK
        with exclusive_lock(settings):
            transport = (transport_factory or SshTransport.from_settings)(settings)
            fetch(settings, transport, force=args.force, out=out)
        return EXIT_OK
    except SyncError as exc:
        print(f"sync: {exc}", file=sys.stderr)
        return exc.exit_code
    except OSError as exc:
        print(f"sync: {TOOL}: {type(exc).__name__}: {exc.strerror or exc}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
