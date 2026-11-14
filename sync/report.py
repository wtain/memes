"""The dry-run / apply report of the dev/prod sync (epic environment-data-sync, decision 26; contract: interfaces.md section k).

Standard library only, no database, no network. Tools build `Section` objects (one per tool, with an `Entry` per table or file set),
wrap them in a `Document`, and call `write_report`, which writes `report.json`, `report.md` (and the `dry` marker of a dry run).

Redaction (interfaces.md k): everything that goes in is validated at construction. Ids, names, reason codes, group names, flags
and run ids must have a fixed shape or construction raises `ReportError`; free-text details (handshake, preconditions, checks) are
reduced to code-like tokens or replaced by `[redacted]`; only a left-behind file without an id may carry a file name.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA_VERSION = 1

GROUPS = ("core", "ocr", "clip", "desc", "tags", "profile", "note")
REASON_CODES = (
    "missing_file", "name_problem", "hash_mismatch", "missing_required_table", "files_gate_miss",
    "deferred_changed_since_plan", "non_promotable_reference", "waiting_for_files", "capped_by_limit",
    "over_member_cap",  # P3: a dev file larger than the gate's per-member cap (max_member_mb), left out up front
    "digest_mismatch",  # P2 apply: the rows copied to prod did not hash like dev's snapshot, the batch was rolled back
    "skipped_unknown_image",  # P4: a prod rating whose image is absent on dev (dev deleted it first)
    "ratings_mode_skip",  # P4: the ratings table is not touched on dev (sync.pull.ratings_mode skip: prod's ratings routes are off)
)
ENTRY_KINDS = ("images", "rows", "files", "pulled")
LEFT_BEHIND_TITLE = "left behind on prod (removals deferred)"
DEFAULT_TOP_N = 50
DEFAULT_MAX_IDS = 1000
UNCHANGED_IDS_LIMIT = 1000  # ids of `unchanged` are in report.json only for a section with fewer than this many
REDACTED = "[redacted]"

DEPENDENCY_NOTE = (
    "Later sections depend on earlier ones being applied. In a dry run the data section cannot see files that are not yet on prod: "
    "images whose file the files section would add or replace are shown under waiting_for_files, counted in the data section's "
    "expected added/overwritten totals and part of the plan hash. At apply time the data step plans for real after the files step, "
    "and an image whose file did not arrive moves to skipped_with_reason files_gate_miss in the result block. The verify section of "
    "a dry run describes the state before the apply."
)

TOOL_COMMANDS = {
    "sync.run": "python -m sync.run",
    "sync.promote": "python -m sync.promote",
    "sync.files promote": "python -m sync.files promote",
    "sync.pull": "python -m sync.pull",
}
ENVS = ("metal", "general", "it")

_RUN_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")  # table / file-set / section / guard / check names, flag values
_ID_STR = re.compile(r"^[0-9A-Fa-f-]{1,64}$")  # uuid / hex hash ids (strings); integer ids are ints
_FLAG = re.compile(r"^--[a-z][a-z-]*$")
_DETAIL = re.compile(r"^[a-z0-9_.:=,-]{0,80}$")  # code-like detail: lower-case tokens, no spaces, no quotes
_HEX = re.compile(r"^[0-9a-f]{8,128}$")


class ReportError(ValueError):
    """Something that must not appear in a report (or a malformed report) was supplied."""


# ---------------------------------------------------------------------------------------------------------------------------------
# validation (the redaction guard)

def check_id(value: Any) -> int | str:
    if isinstance(value, bool):
        raise ReportError("an id must be an int or a hex/uuid string")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _ID_STR.match(value):
        return value
    raise ReportError("an id must be an int or a hex/uuid string")


def check_token(value: Any, what: str = "name") -> str:
    if isinstance(value, str) and _TOKEN.match(value):
        return value
    raise ReportError(f"{what} is not a plain identifier")


def check_member(value: Any, allowed: Iterable[str], what: str) -> str:
    if value in tuple(allowed):
        return value
    raise ReportError(f"{what} is not one of the known codes")


def check_count(value: Any, what: str = "count") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReportError(f"{what} must be a non-negative int")
    return value


def check_number(value: Any, what: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReportError(f"{what} must be a number")
    return value


def scrub_detail(value: Any) -> str:
    """Free text is reduced to a code-like token; anything else becomes `[redacted]` rather than being passed through."""
    if isinstance(value, str) and _DETAIL.match(value):
        return value
    return REDACTED


def scrub_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str) and _TOKEN.match(value):
        return value
    return REDACTED


def check_file_name(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 255
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ReportError("a file name must be printable text up to 255 characters")
    return value


def _sort_key(v: Any):
    return (0, v, "") if isinstance(v, int) else (1, 0, v)


def sorted_ids(ids: Iterable[Any]) -> list:
    return sorted((check_id(i) for i in ids), key=_sort_key)


def check_run_id(value: Any) -> str:
    if isinstance(value, str) and _RUN_ID.match(value):
        return value
    raise ReportError("run id must look like YYYYMMDDTHHMMSSZ-<8 hex>")


# ---------------------------------------------------------------------------------------------------------------------------------
# model

@dataclass
class IdSet:
    """A set of ids with bytes. `count` overrides the id count when the ids are not listed (unchanged rows)."""

    ids: list = field(default_factory=list)
    bytes: int = 0
    count: int | None = None

    def __post_init__(self):
        self.ids = sorted_ids(self.ids)
        self.bytes = check_count(self.bytes, "bytes")
        if self.count is not None:
            check_count(self.count)

    @property
    def n(self) -> int:
        return self.count if self.count is not None else len(self.ids)


@dataclass
class Overwritten(IdSet):
    """Overwritten ids. Images: `groups` maps id -> changed digest groups. Files: `size_changed` / `hash_changed` id lists."""

    groups: dict = field(default_factory=dict)
    size_changed: list = field(default_factory=list)
    hash_changed: list = field(default_factory=list)

    def __post_init__(self):
        norm = {}
        for i, gs in self.groups.items():
            norm[check_id(i)] = sorted({check_member(g, GROUPS, "digest group") for g in gs}, key=GROUPS.index)
        self.groups = norm
        self.ids = list(self.ids) + [i for i in norm if i not in set(self.ids)]
        super().__post_init__()
        self.size_changed = sorted_ids(self.size_changed)
        self.hash_changed = sorted_ids(self.hash_changed)

    @property
    def group_counts(self) -> dict:
        return {g: sum(1 for gs in self.groups.values() if g in gs) for g in GROUPS if any(g in gs for gs in self.groups.values())}


@dataclass
class LeftBehind(IdSet):
    """On prod, not promotable on dev. A file without an id is listed by `names` (files entries only)."""

    names: list = field(default_factory=list)

    def __post_init__(self):
        super().__post_init__()
        self.names = sorted(check_file_name(n) for n in self.names)

    @property
    def n(self) -> int:
        return (self.count if self.count is not None else len(self.ids)) + len(self.names)


@dataclass
class Entry:
    """One table or file set of a section."""

    name: str
    kind: str = "rows"
    added: IdSet = field(default_factory=IdSet)
    overwritten: Overwritten = field(default_factory=Overwritten)
    unchanged: IdSet = field(default_factory=IdSet)
    skipped: dict = field(default_factory=dict)  # reason code -> IdSet
    left_behind: LeftBehind = field(default_factory=LeftBehind)
    waiting_for_files: dict = field(default_factory=dict)  # "added" / "overwritten" -> IdSet (combined dry run only)
    pulled: bool = False  # large pulled table: report.json cuts its id lists at max_ids
    counts: dict = field(default_factory=dict)  # P4: named row counts of a pulled table (prod, dev, dev_only_discarded, ...); measurements, not hashed

    def __post_init__(self):
        check_token(self.name, "table or file-set name")
        self.counts = {check_token(k, "count name"): check_count(v, "count") for k, v in sorted(self.counts.items())}
        check_member(self.kind, ENTRY_KINDS, "entry kind")
        self.skipped = {check_member(r, REASON_CODES, "reason code"): s for r, s in self.skipped.items()}
        self.waiting_for_files = {check_member(k, ("added", "overwritten"), "waiting kind"): s
                                  for k, s in self.waiting_for_files.items()}
        if self.left_behind.names and self.kind != "files":
            raise ReportError("file names are allowed only for left-behind files")

    @property
    def waiting_total(self) -> int:
        return sum(s.n for s in self.waiting_for_files.values())


@dataclass
class Guard:
    id: str
    tripped: bool
    value: Any = None
    limit: Any = None
    lifted_by: str | None = None

    def __post_init__(self):
        check_token(self.id, "guard id")
        self.tripped = bool(self.tripped)
        self.value = check_number(self.value, "guard value")
        self.limit = check_number(self.limit, "guard limit")
        if self.lifted_by is not None and not _FLAG.match(self.lifted_by):
            raise ReportError("lifted_by must be a command line flag")


@dataclass
class Section:
    """One tool's part of the report."""

    name: str  # pull, files, data, verify (or the tool name when stand-alone)
    tool: str
    entries: list = field(default_factory=list)
    guards: list = field(default_factory=list)
    flags: dict = field(default_factory=dict)  # flags that change the plan -> part of the plan hash
    manifest_hash: str | None = None
    estimate: dict = field(default_factory=dict)  # {duration_s, wire_bytes}: measurements, not part of the hash
    lists: dict = field(default_factory=dict)  # pull: to_fetch, to_delete_on_prod_after_verified_pull -> ids
    checks: list = field(default_factory=list)  # verify: {id, status, severity, detail}
    table_rows: dict = field(default_factory=dict)  # data: table -> row and byte counts of the image-scoped tables (measurements, not hashed)
    structural: bool = False  # the plan hash covers only the structure (tables in scope, flags, guard states), not ids (sync.pull, owner decision A)

    def __post_init__(self):
        check_token(self.name, "section name")
        self.table_rows = {check_token(t, "table name"): {check_token(k, "table row key"): check_count(v, "table row count")
                                                          for k, v in sorted(counts.items())}
                           for t, counts in sorted(self.table_rows.items())}
        check_token(self.tool.replace(" ", "_"), "tool name")
        self.flags = {check_token(k, "flag name"): scrub_scalar(v) for k, v in self.flags.items()}
        if self.manifest_hash is not None and not _HEX.match(self.manifest_hash):
            raise ReportError("manifest hash must be hex")
        self.estimate = {k: check_number(self.estimate.get(k), k) for k in ("duration_s", "wire_bytes") if k in self.estimate}
        self.lists = {check_member(k, ("to_fetch", "to_delete_on_prod_after_verified_pull"), "list name"): sorted_ids(v)
                      for k, v in self.lists.items()}
        self.checks = [{"id": check_token(c["id"], "check id"), "status": check_token(c.get("status", "ok"), "status"),
                        "severity": check_token(c.get("severity", "info"), "severity"),
                        "detail": scrub_detail(c.get("detail", ""))} for c in self.checks]

    def totals(self) -> dict:
        def tot(cat):
            items = [getattr(e, cat) for e in self.entries]
            return {"count": sum(i.n for i in items), "bytes": sum(i.bytes for i in items)}

        skipped_n = sum(s.n for e in self.entries for s in e.skipped.values())
        skipped_b = sum(s.bytes for e in self.entries for s in e.skipped.values())
        waiting_added = sum(e.waiting_for_files["added"].n for e in self.entries if "added" in e.waiting_for_files)
        waiting_over = sum(e.waiting_for_files["overwritten"].n for e in self.entries if "overwritten" in e.waiting_for_files)
        t = {"added": tot("added"), "overwritten": tot("overwritten"), "unchanged": tot("unchanged"),
             "skipped": {"count": skipped_n, "bytes": skipped_b}, "left_behind": tot("left_behind")}
        if any(e.waiting_for_files for e in self.entries):
            t["waiting_for_files"] = {"count": waiting_added + waiting_over}
            t["expected_added"] = {"count": t["added"]["count"] + waiting_added}
            t["expected_overwritten"] = {"count": t["overwritten"]["count"] + waiting_over}
        return t

    def plan_hash(self) -> str:
        return section_plan_hash(self)


@dataclass
class ResultEntry:
    """What an apply actually committed for one entry: counts, plus skipped ids per reason code."""

    added: int = 0
    overwritten: int = 0
    skipped: dict = field(default_factory=dict)  # reason code -> ids
    failed: list = field(default_factory=list)  # ids of the batch that was rolled back (P2 apply: the run stopped there)
    failure: str | None = None  # code-like reason of that failure (`db_error:23505`, `interrupted`, ...)
    checked: int | None = None  # verify entries: how many ids or rows were compared with what the apply promised
    differences: dict = field(default_factory=dict)  # verify entries: difference code -> ids (any entry makes the run exit 4)
    info: dict = field(default_factory=dict)  # verify entries: informational counts (drift, parity), never a failure

    def __post_init__(self):
        check_count(self.added)
        check_count(self.overwritten)
        if self.checked is not None:
            check_count(self.checked)
        self.differences = {check_token(c, "difference code"): sorted_ids(v) for c, v in sorted(self.differences.items()) if v}
        self.info = {check_token(k, "info key"): check_count(v, "info count") for k, v in sorted(self.info.items())}
        self.skipped = {check_member(r, REASON_CODES, "reason code"): sorted_ids(v) for r, v in self.skipped.items()}
        self.failed = sorted_ids(self.failed)
        if self.failure is not None:
            self.failure = scrub_detail(self.failure)


@dataclass
class Document:
    env: str
    run_id: str
    tool: str
    dry: bool = True
    sections: list = field(default_factory=list)
    handshake: list = field(default_factory=list)  # {step, ok, detail}
    preconditions: list = field(default_factory=list)  # {id, ok, detail}
    apply_command: str | None = None
    result: dict | None = None  # section name -> entry name -> ResultEntry (applies only)
    generated_at: str | None = None

    def __post_init__(self):
        check_member(self.env, ENVS, "environment")
        check_run_id(self.run_id)
        self.handshake = [{"step": check_token(h["step"], "handshake step"), "ok": bool(h["ok"]),
                           "detail": scrub_detail(h.get("detail", ""))} for h in self.handshake]
        self.preconditions = [{"id": check_token(p["id"], "precondition id"), "ok": bool(p["ok"]),
                               "detail": scrub_detail(p.get("detail", ""))} for p in self.preconditions]
        if self.generated_at is None:
            self.generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if self.result is not None:
            for sec, entries in self.result.items():
                check_token(sec, "section name")
                for name, r in entries.items():
                    check_token(name, "entry name")
                    if not isinstance(r, ResultEntry):
                        raise ReportError("result entries must be ResultEntry")

    @property
    def plan_hash(self) -> str | None:
        return combined_plan_hash(self.sections)

    @property
    def banner(self) -> str:
        if self.dry:
            return "DRY RUN: nothing was written to prod or dev; read this report, then run the apply command"
        return "APPLY: this report describes a run that wrote to the target"

    @property
    def has_dependency_note(self) -> bool:
        return len(self.sections) > 1

    def totals(self) -> dict:
        out: dict = {}
        for s in self.sections:
            for cat, v in s.totals().items():
                acc = out.setdefault(cat, {"count": 0, **({"bytes": 0} if "bytes" in v else {})})
                for k, n in v.items():
                    acc[k] = acc.get(k, 0) + n
        return out


# ---------------------------------------------------------------------------------------------------------------------------------
# plan hash

def _canon_set(s: IdSet) -> dict:
    return {"count": s.n, "ids": list(s.ids)}


def _canon_entry(e: Entry) -> dict:
    o = e.overwritten
    return {
        "name": e.name,
        "kind": e.kind,
        "added": list(e.added.ids),
        "overwritten": [[i, o.groups.get(i, [])] for i in o.ids],
        "size_changed": list(o.size_changed),
        "hash_changed": list(o.hash_changed),
        "unchanged": e.unchanged.n,
        "skipped": {r: list(s.ids) for r, s in sorted(e.skipped.items())},
        "left_behind": {"ids": list(e.left_behind.ids), "names": list(e.left_behind.names), "count": e.left_behind.n},
        "waiting": {k: list(s.ids) for k, s in sorted(e.waiting_for_files.items())},
    }


def canonical_section(sec: Section) -> dict:
    """Everything that belongs to the plan; no generated_at, run id, estimate or measured bytes.

    A `structural` section (sync.pull) is hashed by its structure only: the tables in scope, the flags and each guard's id, tripped state and
    lifting flag. No ids, row digests, lists, counts or guard values: reader activity on prod changes every minute."""
    if sec.structural:
        return {
            "section": sec.name,
            "tool": sec.tool,
            "structural": True,
            "tables": sorted(e.name for e in sec.entries),
            "guards": [{"id": g.id, "tripped": g.tripped, "lifted_by": g.lifted_by} for g in sorted(sec.guards, key=lambda g: g.id)],
            "flags": {k: sec.flags[k] for k in sorted(sec.flags)},
        }
    return {
        "section": sec.name,
        "tool": sec.tool,
        "entries": [_canon_entry(e) for e in sorted(sec.entries, key=lambda e: e.name)],
        "guards": [{"id": g.id, "tripped": g.tripped, "value": g.value, "limit": g.limit, "lifted_by": g.lifted_by}
                   for g in sorted(sec.guards, key=lambda g: g.id)],
        "flags": {k: sec.flags[k] for k in sorted(sec.flags)},
        "manifest_hash": sec.manifest_hash,
        "lists": {k: list(v) for k, v in sorted(sec.lists.items())},
        "checks": [{"id": c["id"], "status": c["status"]} for c in sorted(sec.checks, key=lambda c: c["id"])],
    }


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def section_plan_hash(sec: Section) -> str:
    return _sha(canonical_section(sec))


def combined_plan_hash(sections: list) -> str | None:
    """No sections: no plan (a report of a failed precondition). One section: its own hash (a stand-alone tool's `--plan-hash`).
    Several: the hash of the ordered section hashes (`sync.run`)."""
    if not sections:
        return None
    hashes = [section_plan_hash(s) for s in sections]
    if len(hashes) == 1:
        return hashes[0]
    return _sha(hashes)


# ---------------------------------------------------------------------------------------------------------------------------------
# apply command

def apply_command(tool: str, env: str, plan_hash: str | None, flags: Iterable[str] = ()) -> str | None:
    """The exact command that applies this plan, e.g. `python -m sync.run --env general --apply --plan-hash 3fa9...`.
    `flags` are the plan-changing tokens of the dry run (`--first-seed`, `--only`, `a,b`, `--allow-mass-change`, `120`...)."""
    if plan_hash is None or tool not in TOOL_COMMANDS:
        return None
    check_member(env, ENVS, "environment")
    if not _HEX.match(plan_hash):
        raise ReportError("plan hash must be hex")
    parts = [TOOL_COMMANDS[tool], "--env", env]
    for f in flags:
        if not (_FLAG.match(f) or re.match(r"^[A-Za-z0-9_,.]+$", f)):
            raise ReportError("apply command flags must be flags or plain values")
        parts.append(f)
    parts += ["--apply", "--plan-hash", plan_hash]
    return " ".join(parts)


# ---------------------------------------------------------------------------------------------------------------------------------
# combined sync.run document helpers

def mark_waiting_for_files(data_entry: Entry, files_ids: Iterable[Any]) -> None:
    """In a combined dry run: ids of the data entry that the files section would add or replace move to `waiting_for_files`
    (they stay in the expected totals and in the plan hash)."""
    fid = set(sorted_ids(files_ids))
    for kind in ("added", "overwritten"):
        cur = getattr(data_entry, kind)
        moved = [i for i in cur.ids if i in fid]
        if not moved:
            continue
        keep = [i for i in cur.ids if i not in fid]
        if kind == "overwritten":
            for i in moved:
                cur.groups.pop(i, None)
        cur.ids = keep
        cur.bytes = 0 if not keep else cur.bytes
        prev = data_entry.waiting_for_files.get(kind)
        data_entry.waiting_for_files[kind] = IdSet(ids=(prev.ids if prev else []) + moved)


def combined_document(env: str, run_id: str, sections: list, *, dry: bool = True, handshake=(), preconditions=(),
                      flags: Iterable[str] = (), result: dict | None = None, generated_at: str | None = None) -> Document:
    """The one document of `sync.run`: one section per step (pull, files, data, verify)."""
    doc = Document(env=env, run_id=run_id, tool="sync.run", dry=dry, sections=list(sections), handshake=list(handshake),
                   preconditions=list(preconditions), result=result, generated_at=generated_at)
    doc.apply_command = apply_command("sync.run", env, doc.plan_hash, flags) if dry else None
    return doc


def plan_vs_result(section: Section, result: dict) -> list:
    """Differences between a section's plan and the apply's result for it (entry name -> ResultEntry): a count that is lower is
    accepted only when the difference is listed under skipped. Returns human-free codes `<entry>:<category>`."""
    diffs = []
    for e in section.entries:
        r = result.get(e.name)
        if r is None:
            diffs.append(f"{e.name}:missing")
            continue
        skipped = sum(len(v) for v in r.skipped.values())
        expect = e.added.n + (e.waiting_for_files["added"].n if "added" in e.waiting_for_files else 0)
        expect_o = e.overwritten.n + (e.waiting_for_files["overwritten"].n if "overwritten" in e.waiting_for_files else 0)
        if r.added + r.overwritten + skipped != expect + expect_o:
            diffs.append(f"{e.name}:count")
        elif r.added > expect or r.overwritten > expect_o:
            diffs.append(f"{e.name}:category")
    return diffs


# ---------------------------------------------------------------------------------------------------------------------------------
# JSON

def _ids_json(ids: list, cut_at: int | None) -> dict:
    if cut_at is not None and len(ids) > cut_at:
        return {"ids": None, "ids_range": {"count": len(ids), "min": ids[0], "max": ids[-1]}}
    return {"ids": list(ids)}


def _set_json(s: IdSet, cut_at: int | None, with_ids: bool = True) -> dict:
    out = {"count": s.n, "bytes": s.bytes}
    if with_ids:
        out.update(_ids_json(s.ids, cut_at))
    return out


def entry_json(e: Entry, cut_at: int | None, unchanged_ids: bool) -> dict:
    cut = cut_at if e.pulled else None
    o = e.overwritten
    ow = _set_json(o, cut)
    if o.groups:
        ow["group_counts"] = o.group_counts
        if cut is None or len(o.ids) <= cut:
            ow["groups"] = [{"id": i, "groups": o.groups[i]} for i in o.ids if i in o.groups]
    if o.size_changed or o.hash_changed:
        ow["size_changed"] = {"count": len(o.size_changed), "ids": o.size_changed}
        ow["hash_changed"] = {"count": len(o.hash_changed), "ids": o.hash_changed}
    lb = _set_json(e.left_behind, cut)
    lb["count"] = e.left_behind.n
    if e.left_behind.names:
        lb["names"] = list(e.left_behind.names)
    out = {
        "name": e.name,
        "kind": e.kind,
        "added": _set_json(e.added, cut),
        "overwritten": ow,
        "unchanged": _set_json(e.unchanged, cut, with_ids=unchanged_ids and bool(e.unchanged.ids)),
        "skipped_with_reason": {r: {"count": s.n, "ids": list(s.ids)} for r, s in sorted(e.skipped.items())},
        "left_behind": lb,
    }
    if e.counts:
        out["counts"] = dict(e.counts)
    if e.waiting_for_files:
        out["waiting_for_files"] = {k: {"count": s.n, "ids": list(s.ids)} for k, s in sorted(e.waiting_for_files.items())}
        out["skipped_with_reason"]["waiting_for_files"] = {
            "count": e.waiting_total, "ids": sorted_ids(i for s in e.waiting_for_files.values() for i in s.ids)}
    return out


def section_json(sec: Section, cut_at: int | None = None) -> dict:
    unchanged_ids = sum(e.unchanged.n for e in sec.entries) < UNCHANGED_IDS_LIMIT
    out = {
        "name": sec.name,
        "tool": sec.tool,
        "entries": [entry_json(e, cut_at, unchanged_ids) for e in sec.entries],
        "totals": sec.totals(),
        "guards": [{"id": g.id, "tripped": g.tripped, "value": g.value, "limit": g.limit, "lifted_by": g.lifted_by}
                   for g in sec.guards],
        "estimate": dict(sec.estimate),
        "plan_hash": sec.plan_hash(),
        "flags": dict(sec.flags),
    }
    if sec.manifest_hash:
        out["manifest_hash"] = sec.manifest_hash
    if sec.lists:
        out["lists"] = {k: {"count": len(v), **_ids_json(v, cut_at)} for k, v in sec.lists.items()}
    if sec.checks:
        out["checks"] = list(sec.checks)
    if sec.table_rows:
        out["table_rows"] = {t: dict(c) for t, c in sec.table_rows.items()}
    return out


def _result_entry_json(r: ResultEntry) -> dict:
    out = {"added": r.added, "overwritten": r.overwritten,
           "skipped_with_reason": {c: {"count": len(v), "ids": v} for c, v in sorted(r.skipped.items())}}
    if r.failed or r.failure:
        out["failed"] = {"count": len(r.failed), "ids": list(r.failed), "failure": r.failure}
    if r.checked is not None:
        out["verify"] = {"checked": r.checked, "differences": {c: {"count": len(v), "ids": list(v)} for c, v in r.differences.items()},
                         "info": dict(r.info)}
    return out


def result_json(result: dict) -> dict:
    return {sec: {name: _result_entry_json(r) for name, r in entries.items()} for sec, entries in result.items()}


def document_json(doc: Document, max_ids: int | None = DEFAULT_MAX_IDS) -> dict:
    out = {
        "schema": SCHEMA_VERSION,
        "env": doc.env,
        "run_id": doc.run_id,
        "tool": doc.tool,
        "dry": doc.dry,
        "generated_at": doc.generated_at,
        "banner": doc.banner,
        "handshake": doc.handshake,
        "preconditions": doc.preconditions,
        "plan_hash": doc.plan_hash,
        "apply_command": doc.apply_command,
        "totals": doc.totals(),
        "sections": [section_json(s, max_ids) for s in doc.sections],
    }
    if doc.has_dependency_note:
        out["dependency_note"] = DEPENDENCY_NOTE
    if doc.result is not None:
        out["result"] = result_json(doc.result)
    return out


def render_json(doc: Document, max_ids: int | None = DEFAULT_MAX_IDS) -> str:
    return json.dumps(document_json(doc, max_ids), indent=2, sort_keys=False, ensure_ascii=True) + "\n"


# ---------------------------------------------------------------------------------------------------------------------------------
# Markdown and stdout summary

def _capped(ids: list, top_n: int) -> str:
    shown = ", ".join(str(i) for i in ids[:top_n])
    rest = len(ids) - top_n
    return shown + (f" ... ({rest} more in report.json)" if rest > 0 else "")


def _md_bt(text: str) -> str:
    return "`" + text.replace("`", "'") + "`"


def render_markdown(doc: Document, top_n: int = DEFAULT_TOP_N) -> str:
    L = [f"# Sync report: {doc.tool} ({doc.env})", "", f"**{doc.banner}**", "",
         f"- run id: {doc.run_id}", f"- generated at: {doc.generated_at}", f"- dry run: {str(doc.dry).lower()}",
         f"- plan hash: {doc.plan_hash}"]
    if doc.apply_command:
        L += ["", "Apply command:", "", "```", doc.apply_command, "```"]
    if doc.handshake:
        L += ["", "## Handshake", ""] + [f"- {h['step']}: {'ok' if h['ok'] else 'FAILED'} {h['detail']}".rstrip() for h in doc.handshake]
    if doc.preconditions:
        L += ["", "## Preconditions", ""] + [f"- {p['id']}: {'ok' if p['ok'] else 'FAILED'} {p['detail']}".rstrip()
                                             for p in doc.preconditions]
    if doc.has_dependency_note:
        L += ["", "> " + DEPENDENCY_NOTE]
    for sec in doc.sections:
        L += ["", f"## Section {sec.name}", ""]
        t = sec.totals()
        L.append("totals: " + ", ".join(f"{k} {v['count']}" + (f" ({v['bytes']} bytes)" if v.get("bytes") else "") for k, v in t.items()))
        for g in sec.guards:
            lift = f", lifted by {g.lifted_by}" if g.lifted_by else ""
            L.append(f"- guard {g.id}: {'TRIPPED' if g.tripped else 'ok'} (value {g.value}, limit {g.limit}{lift})")
        for e in sec.entries:
            L += ["", f"### {e.name} ({e.kind})", ""]
            L.append(f"**added**: {e.added.n} ({e.added.bytes} bytes)")
            if e.added.ids:
                L.append(_capped(e.added.ids, top_n))
            L.append(f"**overwritten**: {e.overwritten.n} ({e.overwritten.bytes} bytes)")
            o = e.overwritten
            if o.groups:
                L.append("changed digest groups: " + ", ".join(f"{g} {n}" for g, n in o.group_counts.items()))
                shown = [i for i in o.ids if i in o.groups][:top_n]
                L.append("; ".join(f"{i}: {'/'.join(o.groups[i])}" for i in shown)
                         + (f" ... ({len(o.groups) - top_n} more in report.json)" if len(o.groups) > top_n else ""))
            elif o.ids:
                L.append(_capped(o.ids, top_n))
            if o.size_changed or o.hash_changed:
                L.append(f"size_changed {len(o.size_changed)}, hash_changed {len(o.hash_changed)}")
            L.append(f"**unchanged**: {e.unchanged.n}")
            if e.counts:
                L.append("counts: " + ", ".join(f"{k} {v}" for k, v in e.counts.items()))
            for r, s in sorted(e.skipped.items()):
                L.append(f"**skipped ({r})**: {s.n}")
                if s.ids:
                    L.append(_capped(s.ids, top_n))
            for k, s in sorted(e.waiting_for_files.items()):
                L.append(f"**waiting_for_files ({k})**: {s.n}")
                if s.ids:
                    L.append(_capped(s.ids, top_n))
            L.append(f"**{LEFT_BEHIND_TITLE}**: {e.left_behind.n}")
            if e.left_behind.ids:
                L.append(_capped(e.left_behind.ids, top_n))
            if e.left_behind.names:
                names = e.left_behind.names
                L.append(", ".join(_md_bt(n) for n in names[:top_n])
                         + (f" ... ({len(names) - top_n} more in report.json)" if len(names) > top_n else ""))
        for k, v in sec.lists.items():
            L += ["", f"**{k}**: {len(v)}", _capped(v, top_n)]
        if sec.table_rows:
            L += ["", "rows per table (rows written to prod; replaced = prod rows deleted first):"]
            for t, c in sec.table_rows.items():
                L.append(f"- {t}: " + ", ".join(f"{k} {v}" for k, v in c.items()))
        for c in sec.checks:
            L.append(f"- check {c['id']}: {c['status']} ({c['severity']}) {c['detail']}".rstrip())
        if sec.estimate:
            L.append(f"estimate: {sec.estimate}")
        L.append(f"section plan hash: {sec.plan_hash()}")
    if doc.result is not None:
        L += ["", "## Result", ""]
        for sec, entries in doc.result.items():
            for name, r in entries.items():
                skipped = ", ".join(f"{c} {len(v)}" for c, v in sorted(r.skipped.items()))
                L.append(f"- {sec}/{name}: added {r.added}, overwritten {r.overwritten}" + (f", skipped: {skipped}" if skipped else "")
                         + (f", FAILED batch of {len(r.failed)} images rolled back ({r.failure})" if r.failed or r.failure else ""))
                if r.failed:
                    L.append(f"  failed ids: {_capped(r.failed, top_n)}")
                if r.checked is not None:
                    diffs = ", ".join(f"{c} {len(v)}" for c, v in r.differences.items())
                    L.append(f"  verified {r.checked}: " + (f"DIFFERENCES: {diffs}" if diffs else "no differences")
                             + (" (" + ", ".join(f"{k} {v}" for k, v in r.info.items()) + ")" if r.info else ""))
                    for c, v in r.differences.items():
                        L.append(f"  {c} ids: {_capped(v, top_n)}")
    return "\n".join(L) + "\n"


def render_summary(doc: Document) -> str:
    """Stdout summary: headline counts and bytes per section, guards that would trip, plan hash, apply command."""
    L = [doc.banner, f"run {doc.run_id}  env {doc.env}  tool {doc.tool}"]
    failed = [h["step"] for h in doc.handshake if not h["ok"]] + [p["id"] for p in doc.preconditions if not p["ok"]]
    if failed:
        L.append("FAILED: " + ", ".join(failed))
    for sec in doc.sections:
        parts = []
        for k, v in sec.totals().items():
            parts.append(f"{k} {v['count']}" + (f" ({v['bytes']} B)" if v.get("bytes") else ""))
        L.append(f"[{sec.name}] " + ", ".join(parts))
        for g in sec.guards:
            if g.tripped:
                L.append(f"  guard {g.id} would trip (value {g.value}, limit {g.limit}"
                         + (f"; lift with {g.lifted_by}" if g.lifted_by else "") + ")")
    if doc.has_dependency_note:
        L.append("note: later sections depend on earlier ones being applied (see report.md)")
    if doc.plan_hash:
        L.append(f"plan hash: {doc.plan_hash}")
    if doc.apply_command:
        L.append(f"apply: {doc.apply_command}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------------------------------------------
# writing

def atomic_write_text(path: Path | str, text: str) -> None:
    """Temp file in the same directory, fsync, os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_report(doc: Document, run_dir: Path | str, *, top_n: int = DEFAULT_TOP_N, max_ids: int | None = DEFAULT_MAX_IDS,
                 json_copy: Path | str | None = None, write: Callable[[Path, str], None] = atomic_write_text) -> dict:
    """Write `dry` (dry runs only), `report.json` and `report.md` into the run folder. A folder with the marker never takes an
    apply report and vice versa. `json_copy` is the `--json PATH` copy. Returns the paths and the text."""
    run_dir = Path(run_dir)
    marker = run_dir / "dry"
    if doc.dry:
        write(marker, "")
    elif marker.exists():
        raise ReportError("a run folder with the dry marker never holds an apply report")
    js = render_json(doc, max_ids)
    write(run_dir / "report.json", js)
    write(run_dir / "report.md", render_markdown(doc, top_n))
    if json_copy is not None:
        write(Path(json_copy), js)
    return {"json": run_dir / "report.json", "md": run_dir / "report.md", "summary": render_summary(doc)}
