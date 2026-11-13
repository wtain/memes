"""P2-T8: the verify phase of `sync.promote` (design P2 "3. Verify") and `--verify-only`.

Prod is re-read in a read-only transaction (`REPEATABLE READ READ ONLY`; the sessions are pinned like the plan's, so the digests are
comparable) and compared with what the run PROMISED, not with dev now:

- images: for every image of every batch of `committed.json` the recorded digest group hashes (the dev snapshot that was copied) against
  prod's current digests over the same tables (`committed.json["tables"]`, which is the `--only` selection when one was used). A
  missing image or a group whose hash differs is a difference (exit 4). When the group differs only because a later promote overwrote the
  image (prod now equals dev's current rows) it is counted as `superseded`, information only. For a group that differs while dev still
  hashes as recorded, the row counts of its tables on both sides say whether child rows are missing or extra (`rows_missing_<table>`,
  `rows_extra_<table>`, `content_changed_<table>`);
- global sets: for every row of `committed.json["global_rows"]` (key and the row hash of the dev snapshot) the row must exist on prod with
  that hash; a missing row or a different hash is a difference (same `superseded` rule);
- parity (information only): the plan of dev against prod over the whole promotable set, as counts: what a promote would add or
  overwrite now (`parity_*`, images that dev changed since the copy are among them) and what is left behind on prod.

Everything is read; the statements are SELECT, BEGIN, COMMIT, ROLLBACK and the SET and SHOW statements of the session pinning. Results
are ids, codes and counts; no row value is read into Python except the digests (hashes) and the primary keys of global rows.
"""
from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sync import connect, report
from sync.config import EXIT_FAILURE, PreconditionError, SyncError, is_dry
from sync.promote import digest as dg
from sync.promote import plan as plan_mod

COMMITTED = "committed.json"
SNAPSHOT = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
ID_CHUNK = 2000
SECTION = "verify"
IMAGES = "images"


class VerifyError(SyncError):
    """Verification could not run (exit 1). The message is a code, never a value."""

    exit_code = EXIT_FAILURE


class NothingToVerify(PreconditionError):
    """No usable committed.json of this environment (exit 2)."""


@dataclass
class VerifyResult:
    run_id: str = ""
    checked_images: int = 0
    differences: dict = field(default_factory=dict)  # image difference code -> ids (`image_missing`, `group_changed`)
    changed_groups: dict = field(default_factory=dict)  # image id -> the groups that differ
    info: dict = field(default_factory=dict)  # image-level informational counts (superseded, drift, parity)
    global_checked: dict = field(default_factory=dict)  # table -> rows compared
    global_differences: dict = field(default_factory=dict)  # table -> {code: keys} (`rows_missing`, `rows_changed`)
    global_info: dict = field(default_factory=dict)  # table -> informational counts
    parity: Any = None  # plan.Plan of dev against prod (information), or None
    error: str | None = None

    @property
    def difference_count(self) -> int:
        return sum(len(v) for v in self.differences.values()) + sum(len(v) for d in self.global_differences.values() for v in d.values())

    @property
    def ok(self) -> bool:
        return self.error is None and self.difference_count == 0


# ---------------------------------------------------------------------------------------------------------------------------------
# committed.json

def has_committed(doc: dict) -> bool:
    return bool(any(b.get("ids") for b in doc.get("batches", [])) or doc.get("global_rows"))


def parse_committed(data: Any) -> dict | None:
    """The decoded committed.json when it has the shape this tool writes, else None."""
    try:
        if not isinstance(data, dict) or not isinstance(data["run_id"], str) or not isinstance(data["env"], str):
            return None
        if not isinstance(data["batches"], list):
            return None
        for b in data["batches"]:
            if not isinstance(b["ids"], list) or not isinstance(b["digests"], dict):
                return None
        if not isinstance(data.get("tables", []), list) or not isinstance(data.get("global_rows", {}), dict):
            return None
        known = set(dg.global_tables())
        if any(t not in known for t in data.get("global_rows", {})) or any(t not in known for t in data.get("global_tables", [])):
            return None
        if any(t not in set(dg.image_scoped_tables()) for t in data.get("tables", [])):
            return None
        return data
    except (KeyError, TypeError):
        return None


def newest_committed(runs_dir: Path, env: str, skip: Path | None = None) -> tuple | None:
    """`(path, document)` of the newest run folder (run ids sort by time) of `env` that holds a committed.json with something in it;
    dry folders and `skip` (this run's own folder) are ignored. An unreadable file is a `VerifyError`, not silently passed over."""
    if not Path(runs_dir).is_dir():
        return None
    for folder in sorted((p for p in Path(runs_dir).iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        if (skip is not None and folder.resolve() == Path(skip).resolve()) or is_dry(folder):
            continue
        path = folder / COMMITTED
        if not path.is_file():
            continue
        try:
            doc = parse_committed(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            doc = None
        if doc is None:
            raise VerifyError("committed_json_unreadable")
        if doc["env"] == env and has_committed(doc):
            return path, doc
    return None


# ---------------------------------------------------------------------------------------------------------------------------------
# the checks

def _chunks(items: list, size: int = ID_CHUNK) -> list:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _group_order(groups: list) -> list:
    return sorted(groups, key=report.GROUPS.index)


def check_images(dev: Any, prod: Any, doc: dict, res: VerifyResult) -> None:
    tables = dg.table_order(doc.get("tables") or [])
    recorded: dict = {}
    for batch in doc.get("batches", []):
        for i in batch["ids"]:
            recorded[str(i)] = batch["digests"].get(i, {})
    ids = list(recorded)
    res.checked_images = len(ids)
    if not ids or not tables:
        return
    group_tables = dg.groups_for(tables)
    present: set = set()
    prod_digests: dict = {}
    for chunk in _chunks(ids):
        present.update(str(r[0]) for r in prod.query("SELECT id::text FROM images WHERE id = ANY(%s::uuid[])", (chunk,)))
        prod_digests.update(dg.fetch_image_digests(prod, tables, "ids", chunk))
    missing = [i for i in ids if i not in present]
    suspects: dict = {}  # id -> groups whose prod hash differs from the recorded one
    prod_hashes: dict = {}
    for i in ids:
        if i in present:
            got = dg.image_group_hashes(prod_digests.get(i, {}), group_tables)
            prod_hashes[i] = got
            bad = [g for g, h in recorded[i].items() if g in got and got[g] != h]
            if bad:
                suspects[i] = bad
    dev_digests: dict = {}
    dev_hashes: dict = {}
    if suspects:
        for chunk in _chunks(list(suspects)):
            dev_digests.update(dg.fetch_image_digests(dev, tables, "ids", chunk))
        dev_hashes = {i: dg.image_group_hashes(dev_digests.get(i, {}), group_tables) for i in suspects}
    changed: list = []
    superseded = 0
    drift: dict = {}
    for i, bad in suspects.items():
        real = [g for g in bad if prod_hashes[i][g] != dev_hashes[i].get(g)]  # prod equals dev now: a later promote replaced it
        superseded += len(bad) - len(real)
        if not real:
            continue
        changed.append(i)
        res.changed_groups[i] = _group_order(real)
        for g in real:
            if dev_hashes[i].get(g) != recorded[i][g]:
                continue  # dev changed too: no telling which side's rows are missing
            for t in group_tables[g]:
                p, d = prod_digests.get(i, {}).get(t, dg.EMPTY), dev_digests.get(i, {}).get(t, dg.EMPTY)
                if p.count < d.count:
                    drift[f"rows_missing_{t}"] = drift.get(f"rows_missing_{t}", 0) + d.count - p.count
                elif p.count > d.count:
                    drift[f"rows_extra_{t}"] = drift.get(f"rows_extra_{t}", 0) + p.count - d.count
                elif p.token != d.token:
                    drift[f"content_changed_{t}"] = drift.get(f"content_changed_{t}", 0) + 1
    if missing:
        res.differences["image_missing"] = missing
    if changed:
        res.differences["group_changed"] = changed
        for gs in res.changed_groups.values():
            for g in gs:
                res.info[f"changed_group_{g}"] = res.info.get(f"changed_group_{g}", 0) + 1
    if superseded:
        res.info["superseded_groups"] = superseded
    res.info.update(drift)


def _typed_keys(table: str, keys: list) -> list:
    return [int(k) for k in keys] if dg.key_sql_type(table) == "int4" else [str(k) for k in keys]


def check_globals(dev: Any, prod: Any, doc: dict, res: VerifyResult) -> None:
    for table, rows in doc.get("global_rows", {}).items():
        keys = _typed_keys(table, list(rows))
        got = dg.fetch_global_hashes(prod, table, keys)
        missing = [k for k in keys if k not in got]
        differing = [k for k in keys if k in got and got[k][0] != rows[str(k)]]
        superseded = 0
        if differing:
            now = dg.fetch_global_hashes(dev, table, differing)
            real = [k for k in differing if k not in now or now[k][0] != got[k][0]]
            superseded = len(differing) - len(real)
            differing = real
        res.global_checked[table] = len(keys)
        diffs = {c: v for c, v in (("rows_missing", missing), ("rows_changed", differing)) if v}
        if diffs:
            res.global_differences[table] = diffs
        if superseded:
            res.global_info[table] = {"superseded_rows": superseded}


def parity_plan(dev: Any, prod: Any, env: str, tables: list, global_tables: list) -> Any:
    """The promote plan of dev against prod over the whole promotable set (information: nothing is gated, capped or required)."""
    tables = dg.table_order(tables)
    dev_images = dg.fetch_dev_images(dev) if tables else []
    inp = plan_mod.PlanInput(
        env=env, dev_images=dev_images, prod_ids=dg.fetch_prod_image_ids(prod) if tables else set(),
        dev_digests=dg.fetch_image_digests(dev, tables, "promotable") if tables else {},
        prod_digests=dg.fetch_image_digests(prod, tables, "all") if tables else {}, tables=tables, required=[],
        global_tables=list(global_tables), dev_globals={t: dg.fetch_global_hashes(dev, t) for t in global_tables},
        prod_globals={t: dg.fetch_global_hashes(prod, t) for t in global_tables})
    return plan_mod.build_plan(inp)


def _parity_info(plan: Any, res: VerifyResult, tables: list) -> None:
    if tables:
        res.info.update({"parity_added": len(plan.new), "parity_overwritten": len(plan.changed),
                         "parity_unchanged": len(plan.unchanged), "parity_left_behind": len(plan.left_behind)})
    for t, g in plan.globals.items():
        res.global_info.setdefault(t, {}).update({"parity_added": len(g["added"]), "parity_overwritten": len(g["overwritten"]),
                                                  "parity_unchanged": g["unchanged"], "parity_left_behind": len(g["left_behind"])})


def run_verify(dev: Any, prod: Any, doc: dict, *, parity: bool = True) -> VerifyResult:
    """Verify `doc` (a parsed committed.json) against prod. Read only; raises `connect.DbError` or `VerifyError`."""
    res = VerifyResult(run_id=str(doc.get("run_id", "")))
    for side in (dev, prod):
        if dg.pin_session(side):
            raise VerifyError("session_not_pinned")
    dev.query(SNAPSHOT)
    try:
        prod.query(SNAPSHOT)
        try:
            check_images(dev, prod, doc, res)
            check_globals(dev, prod, doc, res)
            if parity:
                tables = list(doc.get("tables") or [])
                globals_ = list(dict.fromkeys(list(doc.get("global_tables") or []) + list(doc.get("global_rows") or {})))
                res.parity = parity_plan(dev, prod, str(doc.get("env")), tables, dg.table_order(globals_))
                _parity_info(res.parity, res, tables)
        finally:
            with contextlib.suppress(Exception):
                prod.query("COMMIT")
    finally:
        with contextlib.suppress(Exception):
            dev.query("COMMIT")
    return res


def verify_after_apply(dev: Any, prod: Any, doc: dict) -> VerifyResult:
    """The verify at the end of an apply: a failure to verify is reported in the result, it does not hide what the apply did."""
    try:
        return run_verify(dev, prod, doc)
    except connect.DbError as exc:
        return VerifyResult(run_id=str(doc.get("run_id", "")), error=exc.code)
    except VerifyError as exc:
        return VerifyResult(run_id=str(doc.get("run_id", "")), error=str(exc)[:60])
    except Exception as exc:  # anything else: its type only
        return VerifyResult(run_id=str(doc.get("run_id", "")), error=f"unexpected:{type(exc).__name__.lower()}"[:60])


# ---------------------------------------------------------------------------------------------------------------------------------
# report and console

def result_entries(res: VerifyResult) -> dict:
    """The `verify` section of the result block: one entry for the images and one per global table checked."""
    out: dict = {}
    if res.checked_images or res.differences or res.info:
        out[IMAGES] = report.ResultEntry(checked=res.checked_images, differences=dict(res.differences), info=dict(res.info))
    for table in dict.fromkeys(list(res.global_checked) + list(res.global_info)):
        out[table] = report.ResultEntry(checked=res.global_checked.get(table, 0), differences=dict(res.global_differences.get(table, {})),
                                        info=dict(res.global_info.get(table, {})))
    return out


def parity_section(res: VerifyResult) -> report.Section | None:
    """The parity plan as a `verify` report section (categories only; its guards are not part of a verify)."""
    if res.parity is None or not res.parity.section.entries:
        return None
    return report.Section(name=SECTION, tool=plan_mod.TOOL, entries=list(res.parity.section.entries), flags=dict(res.parity.section.flags),
                          table_rows=dict(res.parity.section.table_rows))


def summary(res: VerifyResult) -> list:
    if res.error:
        return [f"verify: could not run ({res.error}); run `python -m sync.promote --env <env> --verify-only`"]
    lines = [f"verify: {res.checked_images} image(s) and {sum(res.global_checked.values())} global row(s) compared with run {res.run_id}"]
    if res.ok:
        lines[0] += ", no differences"
    for code, ids in res.differences.items():
        lines.append(f"verify: DIFFERENCE {code}: {len(ids)} image(s)")
    for table, diffs in res.global_differences.items():
        for code, keys in diffs.items():
            lines.append(f"verify: DIFFERENCE {table} {code}: {len(keys)} row(s)")
    informational = ", ".join(f"{k} {v}" for k, v in sorted(res.info.items()) if k.startswith(("superseded", "parity_")))
    if informational:
        lines.append("verify (information): " + informational)
    return lines
