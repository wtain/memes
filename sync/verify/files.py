"""P6 file checks F1 to F4 and the bug-report checks P2 and A2 (design P6 "Checks").

Reads prod only through the gate verbs `list`, `hash` (deep) and `pull-list`, and dev only through the read-only session and its
BASE_PATH. `gather` does the reading, the `check_*` functions judge plain facts. File names appear only as anomaly paths (needed to
act, design) and as the left-behind files of the drift entry (they have no id).

Note on `--deep-files`: the gate verb `hash` recomputes the sha256 of every file and rewrites the gate's own `work/record.tsv` (its
cache). That is the only thing a verify ever writes on prod and it is the gate's bookkeeping, never data or an image.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any

from sync.files import diff as diff_mod
from sync.files import manifest as manifest_mod
from sync.files.transport import GateFailure
from sync.pull import bugreports
from sync.verify import model
from sync.verify.model import Check, fail, ok

FIX_FILES = "python -m sync.run --env <env>"
FIX_DEEP = "python -m sync.files promote --env <env> --deep"
FIX_ANOMALY = "owner: isolate the entry on the VPS, never follow it"
FIX_DRAIN = "python -m sync.pull --env <env> --apply"
DEFAULT_MODES = ("644",)
ANOMALY_LIMIT = 200
_STAMP = re.compile(r"^(\d{8}T\d{6})")


@dataclass
class FileFacts:
    manifest: Any
    diff: Any
    remote: list
    deep: bool = False
    deep_missing: int = 0  # prod entries `hash` found missing or not regular
    dev_hash_mismatch: int = 0  # dev files whose content differs from images.content_hash (deep)
    expected_uid: int | None = None
    anomalies: list = field(default_factory=list)  # (path as safe text, reason code)


def parse_hash_reply(out: bytes) -> dict:
    """`name -> (status, sha256)` of the gate `hash` verb: `status TAB size TAB sha TAB name NUL`."""
    result = {}
    for raw in out.split(b"\x00"):
        if not raw:
            continue
        parts = raw.decode("utf-8", "surrogateescape").split("\t", 3)
        if len(parts) == 4:
            result[parts[3]] = (parts[0], parts[2].strip().lower())
    return result


def classify_remote(remote: list, modes: tuple | list | None, expected_uid: int | None) -> list:
    """Anomalies of the entries directly in the images directory: `[(path, reason)]`. Reasons: invalid_name, not_regular, hardlinked,
    mode, owner. Regular files with one link are fine apart from mode and owner; `modes` is the set of accepted octal modes (empty or
    None: not checked), `expected_uid` None: not checked."""
    out = []
    for r in remote:
        shown = diff_mod.report_name(r.name, r.invalid)
        if r.invalid:
            out.append((shown, "invalid_name" if r.type != "?" else "malformed_record"))
        elif r.type != "f":
            out.append((shown, "not_regular"))
        elif r.nlink != 1:
            out.append((shown, "hardlinked"))
        elif modes and r.mode not in set(modes):
            out.append((shown, "mode"))
        elif expected_uid is not None and r.uid != expected_uid:
            out.append((shown, "owner"))
    return sorted(out)


def gather(dev: Any, transport: Any, base_dir: str, *, deep: bool = False, gate: dict | None = None,
           modes: tuple | list | None = DEFAULT_MODES) -> FileFacts:
    """The dev manifest against the gate's `list` (and, with `deep`, the gate's `hash` and a re-read of every dev file)."""
    rows = manifest_mod.load_promotable_rows(dev.raw)
    mf = manifest_mod.build_manifest(rows, base_dir)
    remote = diff_mod.parse_list(transport.list())
    deep_missing = dev_bad = 0
    if deep:
        reply = transport.hash_all()
        fresh = parse_hash_reply(reply.out) if reply.ok else {}
        if not reply.ok:
            raise GateFailure("hash_failed")
        refreshed = []
        for r in remote:
            if r.type == "f" and not r.invalid and r.nlink == 1:
                status, sha = fresh.get(r.name, ("missing", ""))
                if status != "ok":
                    deep_missing += 1
                    continue  # an entry the gate could not hash is not a usable file: it counts as missing
                r = diff_mod.RemoteEntry(r.type, r.size, sha, r.nlink, r.uid, r.mode, r.name, r.invalid)
            refreshed.append(r)
        remote = refreshed
        before = len(mf.entries)
        manifest_mod.verify_hashes(mf)
        dev_bad = before - len(mf.entries)
    d = diff_mod.diff_manifest(mf, remote)
    uid = ((gate or {}).get("probe") or {}).get("uid")
    expected_uid = uid if isinstance(uid, int) and not isinstance(uid, bool) else None
    return FileFacts(manifest=mf, diff=d, remote=remote, deep=deep, deep_missing=deep_missing, dev_hash_mismatch=dev_bad,
                     expected_uid=expected_uid, anomalies=classify_remote(remote, modes, expected_uid))


# ---------------------------------------------------------------------------------------------------------------------------------
# judging (pure)

def check_f1(d, mf) -> Check:
    missing, size, hashed, unverified = len(d.added), len(d.size_changed), len(d.hash_changed), len(d.unverified)
    left_out = len(mf.left_out)
    detail = f"missing={missing},size={size},hash={hashed},unverified={unverified},left_out={left_out}"
    if missing or size or hashed:
        return fail("F1", "error", detail, FIX_FILES)
    if unverified:
        return model.Check("F1", model.WARN, "error", detail, FIX_DEEP)
    return ok("F1", "error", detail)


def check_f2(d) -> Check:
    return ok("F2", "info", f"left_behind={len(d.left_behind)}")


def check_f3(facts: FileFacts) -> Check:
    if not facts.deep:
        return model.skipped("F3", "error", "deep_files_not_requested")
    bad_prod = len(facts.diff.hash_changed)
    problems = bad_prod + facts.deep_missing + facts.dev_hash_mismatch
    detail = f"prod_hash={bad_prod},prod_missing={facts.deep_missing},dev_hash={facts.dev_hash_mismatch},files={len(facts.manifest.entries)}"
    return fail("F3", "error", detail, FIX_DEEP) if problems else ok("F3", "error", detail)


def check_f4(anomalies: list) -> Check:
    if not anomalies:
        return ok("F4", "error", "anomalies=0")
    by_reason: dict = {}
    for _, reason in anomalies:
        by_reason[reason] = by_reason.get(reason, 0) + 1
    detail = f"anomalies={len(anomalies)}," + ",".join(f"{r}={n}" for r, n in sorted(by_reason.items()))
    return fail("F4", "error", detail, FIX_ANOMALY)


def files_checks(facts: FileFacts) -> list:
    return [check_f1(facts.diff, facts.manifest), check_f2(facts.diff), check_f3(facts), check_f4(facts.anomalies)]


# ---------------------------------------------------------------------------------------------------------------------------------
# bug reports (P2, A2)

def _age_hours(name: str, now: _dt.datetime) -> float | None:
    m = _STAMP.match(name)
    if not m:
        return None
    try:
        at = _dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None
    return max((now - at).total_seconds() / 3600.0, 0.0)


def bug_facts(pull_list_out: bytes, now: _dt.datetime) -> dict:
    """`{files, bytes, anomalies, oldest_age_h}` of a `pull-list` answer (names only for their timestamp; nothing else is kept)."""
    files, anomalies = bugreports.parse_listing(pull_list_out)
    ages = [a for a in (_age_hours(f.name, now) for f in files) if a is not None]
    return {"files": len(files), "bytes": sum(f.size for f in files), "anomalies": anomalies,
            "oldest_age_h": round(max(ages), 1) if ages else None}


def check_p2(facts: dict, stale_days: float = 7) -> Check:
    age = facts["oldest_age_h"]
    detail = f"waiting={facts['files']},bytes={facts['bytes']},anomalies={facts['anomalies']}" + (f",oldest_h={age:g}" if age is not None else "")
    if facts["anomalies"] or (age is not None and age > stale_days * 24):
        return fail("P2", "warn", detail, FIX_DRAIN)
    return ok("P2", "info", detail)


def check_a2(facts: dict, max_files: int, max_bytes: int, alarm_pct: float = 80.0) -> Check:
    """File count and bytes against the cap (the alarm fires at `alarm_pct` percent of it; at the cap it is an error)."""
    pct = max(100.0 * facts["files"] / max_files if max_files else 0.0, 100.0 * facts["bytes"] / max_bytes if max_bytes else 0.0)
    detail = f"files={facts['files']},bytes={facts['bytes']},cap_pct={pct:.0f}"
    if pct >= 100:
        return fail("A2", "error", detail, FIX_DRAIN)
    if pct >= alarm_pct:
        return fail("A2", "warn", detail, FIX_DRAIN)
    return ok("A2", "warn", detail)
