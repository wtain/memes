"""P4-T4/T5: the local files of the pull (interfaces.md a): `pull_state.json`, `ratings_ledger.json`, `ratings_archive.jsonl`.

All writes are atomic (`sync.config.atomic_write`) except the archive, which is append-only and fsynced. None of these files holds a
credential. Only the archive holds rating text; it is created with restricted permissions (mode 0600; on Windows the ACL is reduced to
the current user, best effort).
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import subprocess
from pathlib import Path
from typing import Iterable

from sync.config import atomic_write

STATE_FILE = "pull_state.json"
LEDGER_FILE = "ratings_ledger.json"
ARCHIVE_FILE = "ratings_archive.jsonl"
BUG_LEDGER_FILE = "bugreports_ledger.json"
STATE_FIELDS = ("env", "run_id", "finished_at", "snapshot_at", "snapshot", "applied", "ratings", "prod_image_ids", "tables", "bug_reports")
RATINGS_FIELDS = ("mode", "prod_count", "max_updated_at", "keys_sha256")
BUG_FIELDS = ("status", "pulled", "deleted_on_prod", "failed")
BUG_STATUSES = ("done", "partial", "skipped")

FIRST_PULL, FOUND, UNREADABLE, FOREIGN = "first_pull", "found", "unreadable", "state_env_mismatch"


def utc_text(now: _dt.datetime) -> str:
    return now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def skipped_bug_reports() -> dict:
    return {"status": "skipped", "pulled": 0, "deleted_on_prod": 0, "failed": 0}


def build_state(*, env: str, run_id: str, now: _dt.datetime, snapshot_at: str, snapshot: str, ratings: dict, prod_image_ids: int,
                tables: dict, bug_reports: dict | None = None) -> dict:
    """The document of interfaces.md a, exactly those fields, in that order."""
    return {"env": env, "run_id": run_id, "finished_at": utc_text(now), "snapshot_at": snapshot_at, "snapshot": snapshot, "applied": True,
            "ratings": {k: ratings[k] for k in RATINGS_FIELDS}, "prod_image_ids": int(prod_image_ids),
            "tables": {t: {"prod": int(v["prod"]), "replaced": int(v["replaced"])} for t, v in sorted(tables.items())},
            "bug_reports": {k: (bug_reports or skipped_bug_reports())[k] for k in BUG_FIELDS}}


def state_problems(doc: object) -> list:
    """Schema check of a state document (codes only)."""
    if not isinstance(doc, dict):
        return ["not_an_object"]
    problems = [f"missing_{k}" for k in STATE_FIELDS if k not in doc]
    if problems:
        return problems
    if doc["applied"] is not True:
        problems.append("applied_not_true")
    if not isinstance(doc["tables"], dict) or not all(isinstance(v, dict) and "prod" in v and "replaced" in v for v in doc["tables"].values()):
        problems.append("tables_shape")
    if not isinstance(doc["ratings"], dict) or any(k not in doc["ratings"] for k in RATINGS_FIELDS):
        problems.append("ratings_shape")
    if not isinstance(doc["bug_reports"], dict) or doc["bug_reports"].get("status") not in BUG_STATUSES:
        problems.append("bug_reports_shape")
    return problems


def load_state(directory: Path, env: str) -> tuple:
    """`(document or None, status)`: a missing, unreadable or foreign file counts as a first pull (the safe direction: no baseline)."""
    path = Path(directory) / STATE_FILE
    if not path.is_file():
        return None, FIRST_PULL
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, UNREADABLE
    if state_problems(doc):
        return None, UNREADABLE
    if doc["env"] != env:
        return None, FOREIGN
    return doc, FOUND


def write_state(directory: Path, doc: dict) -> Path:
    path = Path(directory) / STATE_FILE
    atomic_write(path, json.dumps(doc, indent=2, ensure_ascii=True) + "\n")
    return path


# ---------------------------------------------------------------------------------------------------------------------------------
# ratings ledger: sha256 of `image_id|query_hash` of every key received from prod (no text)

def load_ledger(directory: Path) -> frozenset:
    path = Path(directory) / LEDGER_FILE
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        keys = doc["keys"]
        if isinstance(keys, list) and all(isinstance(k, str) for k in keys):
            return frozenset(keys)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return frozenset()  # lost or unreadable: every dev rating counts as dev-made and is never deleted (the safe direction)


def write_ledger(directory: Path, keys: Iterable[str], env: str, now: _dt.datetime) -> Path:
    path = Path(directory) / LEDGER_FILE
    atomic_write(path, json.dumps({"env": env, "updated_at": utc_text(now), "keys": sorted(set(keys))}, indent=2) + "\n")
    return path


# ---------------------------------------------------------------------------------------------------------------------------------
# ratings archive: one JSON line per dev rating row before it is deleted or overwritten

def restrict_permissions(path: Path) -> None:
    """Mode 0600; on Windows reduce the ACL to the current user. Best effort: never fails the pull."""
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    if os.name == "nt":
        user = os.environ.get("USERNAME")
        if user:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R,W)"], capture_output=True, timeout=30)


def append_archive(directory: Path, rows: Iterable[dict], run_id: str, reason_by_id: dict, now: _dt.datetime) -> int:
    """Append one line per dev rating row (`rows` are full rows as dicts, the only place rating text is written), fsynced before the
    caller deletes or overwrites anything. Returns the number of lines written."""
    path = Path(directory) / ARCHIVE_FILE
    Path(directory).mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    lines = [json.dumps({"archived_at": utc_text(now), "run_id": run_id, "reason": reason_by_id.get(str(r.get("id")), "overwritten"), "row": r},
                        ensure_ascii=True, sort_keys=True, default=str) for r in rows]
    if not lines:
        return 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, ("\n".join(lines) + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    if not existed:
        restrict_permissions(path)
    return len(lines)
