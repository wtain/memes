"""P2-T4/T5: `python -m sync.promote` (design P2; interfaces.md a, e, i, k).

Dry run by default: handshake for tool P (H1 to H5, H7 to H10; no gate step), the phase 0 preconditions, the plan from per-image
digests, the guards, and the report of interfaces.md k (`report.json`, `report.md`, stdout summary, `dry` marker, the plan hash and
the exact apply command). A dry run writes nothing to prod or dev and no file any other tool accepts; it exits 0 also when a guard
would trip.

`--apply` re-plans, then refuses (exit 2: a precondition including the files gate; exit 3: a different plan hash or a tripped guard
that no lift flag removes) before any write. The copy itself (batches, files gate per image, `committed.json`, global sets, `setval`)
is task T6 and is not built: after the refusals and the typed confirmation the command stops with exit 1 and writes nothing to prod.
`--verify-only` (task T8) stops the same way. Every flag of the design is accepted now.

Exit codes (interfaces.md i): 0 clean or dry run, 1 other failure, 2 handshake or precondition (including a held lock or a bad flag),
3 guard before any write, 4 verification differences (T8).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Any, Callable

from sync import connect, registry, report
from sync.config import (ENVIRONMENTS, EXIT_FAILURE, EXIT_GUARD, EXIT_OK, PreconditionError, SyncError, SyncSettings, exclusive_lock,
                         load_settings, mark_dry, new_run_id, run_dir, state_dir)
from sync.files.manifest import manifest_sha256
from sync.model import Klass
from sync.promote import digest as dg
from sync.promote import files_gate, plan as plan_mod, preconditions as pre_mod

TOOL = plan_mod.TOOL
DEFAULT_MAX_REPORT_AGE = "2h"
_AGE = re.compile(r"^(\d+(?:\.\d+)?)([smhd]?)$")
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
NOT_BUILT_APPLY = "--apply is not built yet (task T6); nothing was written to prod"
NOT_BUILT_VERIFY = "--verify-only is not built yet (task T8)"


class UsageProblem(PreconditionError):
    """A bad flag value (exit 2)."""


class Refusal(SyncError):
    """A plan hash that differs or a tripped guard, before any write (exit 3)."""

    exit_code = EXIT_GUARD


class NotBuilt(SyncError):
    exit_code = EXIT_FAILURE


class PreconditionsFailed(PreconditionError):
    def __init__(self, message: str, results: list):
        super().__init__(message)
        self.results = results


# ---------------------------------------------------------------------------------------------------------------------------------
# command line

def parse_age(text: str) -> float:
    """`2h`, `90m`, `7200s`, `1d` or a bare number of seconds; returns seconds."""
    m = _AGE.match(str(text).strip().lower())
    if not m or float(m.group(1)) <= 0:
        raise UsageProblem("--max-report-age must look like 2h, 90m, 7200s or 1d")
    return float(m.group(1)) * _UNITS[m.group(2)]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m sync.promote", description="Promote the class D data of the promotable images to prod "
                                "(additions and overwrites only). Dry run with a report unless --apply.")
    p.add_argument("--env", required=True, choices=ENVIRONMENTS)
    p.add_argument("--apply", action="store_true", help="perform the promote (otherwise a dry run that writes only the report folder)")
    p.add_argument("--yes", action="store_true", help="with --apply: skip only the typed confirmation '<env> <prod database>'")
    p.add_argument("--plan-hash", default=None, help="with --apply: refuse (exit 3) if the re-planned hash differs from the dry run's")
    p.add_argument("--first-seed", action="store_true", help="prod is in state 'seeding' (memes.state); resumable by running again")
    p.add_argument("--verify-only", action="store_true", help="only the verify phase against the newest committed.json (not built yet)")
    p.add_argument("--only", action="append", default=None, metavar="TABLE[,..]",
                   help="restrict to class D tables (parents are added)")
    p.add_argument("--limit-images", type=int, default=None, metavar="N",
                   help="cap new and changed images per run (the global sets are then skipped)")
    p.add_argument("--batch-size", type=int, default=None, metavar="N", help="images per prod transaction (default sync.promote.batch_size, 500)")
    p.add_argument("--max-report-age", default=DEFAULT_MAX_REPORT_AGE, metavar="AGE",
                   help="age limit of files-promoted.json (2h, 90m, 7200s, 1d; default 2h)")
    p.add_argument("--top", type=int, default=None, help="ids listed per category in report.md and the summary (default sync.report.top_n)")
    p.add_argument("--run-id", default=None, help="run id (sync.run passes it to its children)")
    p.add_argument("--json", dest="json_path", default=None, metavar="PATH", help="also write report.json to PATH ('-' for stdout)")
    p.add_argument("--allow-mass-change", type=int, default=None, metavar="N",
                   help="lift the mass-change guard; N must equal the planned changed count exactly")
    p.add_argument("--allow-shrink", action="append", default=[], metavar="TABLE",
                   help="lift the row-count and coverage regression guards of one class D table (repeatable)")
    p.add_argument("--allow-model-change", action="store_true",
                   help="lift the model-change guard: the embedding model pair or taxonomy version of dev differs from prod's "
                        "(the affected rows are overwritten)")
    p.add_argument("--ignore-running-batches", action="store_true", help="lift the dev running-batch precondition")
    return p


def only_tables(args: argparse.Namespace) -> list | None:
    if args.only is None:
        return None
    return [t for chunk in args.only for t in (s.strip() for s in chunk.split(",")) if t]


def validate_args(args: argparse.Namespace) -> None:
    """Flag checks that need no connection; every failure is exit 2."""
    if args.plan_hash is not None and not args.apply:
        raise UsageProblem("--plan-hash needs --apply")
    if args.plan_hash is not None and not re.match(r"^[0-9a-fA-F]{8,128}$", args.plan_hash):
        raise UsageProblem("--plan-hash must be the hex hash printed by the dry run")
    if args.yes and not args.apply:
        raise UsageProblem("--yes needs --apply")
    if args.verify_only and args.apply:
        raise UsageProblem("--verify-only is read only and cannot be combined with --apply")
    if args.first_seed and args.verify_only:
        raise UsageProblem("--first-seed does not apply to --verify-only")
    for name in ("top", "limit_images", "batch_size"):
        v = getattr(args, name)
        if v is not None and v < 1:
            raise UsageProblem(f"--{name.replace('_', '-')} must be at least 1")
    if args.allow_mass_change is not None and args.allow_mass_change < 0:
        raise UsageProblem("--allow-mass-change must be the planned changed count (not negative)")
    shipped = set(registry.names(Klass.D))
    for t in args.allow_shrink:
        if t not in shipped:
            raise UsageProblem(f"--allow-shrink names a table that is not class D: {t}")
    only = only_tables(args)
    if only is not None:
        if not only:
            raise UsageProblem("--only needs at least one table")
        bad = [t for t in only if t not in shipped]
        if bad:
            raise UsageProblem(f"--only names tables that are not class D: {', '.join(sorted(bad))}")
    if args.run_id is not None:
        try:
            report.check_run_id(args.run_id)
        except report.ReportError:
            raise UsageProblem("--run-id must look like YYYYMMDDTHHMMSSZ-<8 hex>") from None
    parse_age(args.max_report_age)


def apply_flags(args: argparse.Namespace) -> list:
    """The tokens an apply needs besides `--env --apply --plan-hash` (plan-changing flags and the lifts the dry run was given)."""
    flags: list = []
    if args.first_seed:
        flags.append("--first-seed")
    only = only_tables(args)
    if only:
        flags += ["--only", ",".join(only)]
    if args.limit_images is not None:
        flags += ["--limit-images", str(args.limit_images)]
    if args.batch_size is not None:
        flags += ["--batch-size", str(args.batch_size)]
    if args.max_report_age != DEFAULT_MAX_REPORT_AGE:
        flags += ["--max-report-age", args.max_report_age]
    if args.allow_mass_change is not None:
        flags += ["--allow-mass-change", str(args.allow_mass_change)]
    for t in args.allow_shrink:
        flags += ["--allow-shrink", t]
    if args.allow_model_change:
        flags.append("--allow-model-change")
    if args.ignore_running_batches:
        flags.append("--ignore-running-batches")
    return flags


# ---------------------------------------------------------------------------------------------------------------------------------
# planning with live connections

@contextlib.contextmanager
def snapshot(conn: Any):
    """One consistent read-only snapshot on a connection (`REPEATABLE READ READ ONLY`); the connection is in autocommit mode."""
    conn.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    try:
        yield
    finally:
        conn.query("COMMIT")


def _required(settings: SyncSettings) -> list:
    value = settings.get("promote.required_tables")
    return list(value) if value else list(plan_mod.DEFAULT_REQUIRED)


def _batch_size(settings: SyncSettings, args: argparse.Namespace) -> int:
    return int(args.batch_size or settings.get("promote.batch_size") or plan_mod.DEFAULT_BATCH_SIZE)


def gather(settings: SyncSettings, args: argparse.Namespace, dev: Any, prod: Any, folder: Path, now: _dt.datetime,
           pre: list) -> plan_mod.PlanInput:
    """Read dev and prod (read only, one snapshot each) into a `PlanInput`; appends the files-gate precondition to `pre`."""
    required = _required(settings)
    only = only_tables(args)
    image_tables, global_tables = plan_mod.resolve_only(only)
    fetch = dg.table_order(set(image_tables) | (set(required) if image_tables else set()))
    with snapshot(dev), snapshot(prod):
        dev_images = dg.fetch_dev_images(dev)
        manifest_hash = manifest_sha256([(i.id, i.filename, i.content_hash) for i in dev_images]) if dev_images else None
        prod_ids = dg.fetch_prod_image_ids(prod) if image_tables else set()
        dev_digests = dg.fetch_image_digests(dev, fetch, "promotable") if image_tables else {}
        prod_digests = dg.fetch_image_digests(prod, image_tables, "all") if image_tables else {}
        dev_globals: dict = {}
        prod_globals: dict = {}
        if args.limit_images is None:
            for t in global_tables:
                dev_globals[t] = dg.fetch_global_hashes(dev, t)
                prod_globals[t] = dg.fetch_global_hashes(prod, t)

    files = None
    if image_tables:
        runs_dir = state_dir(settings) / "runs"
        loaded, status = files_gate.load_newest(runs_dir, folder)
        status = files_gate.check(loaded, settings.env, now, parse_age(args.max_report_age), manifest_hash or "", status)
        if status == files_gate.VALID:
            files = loaded
        # a dry run only reports the state of the files report (nothing is classified without a valid one); an apply needs it
        pre.append(pre_mod.result("files_report", status == files_gate.VALID or not args.apply, status))
    return plan_mod.PlanInput(
        env=settings.env, dev_images=dev_images, prod_ids=prod_ids, dev_digests=dev_digests, prod_digests=prod_digests,
        tables=image_tables, required=required, manifest_hash=manifest_hash, first_seed=args.first_seed, files=files,
        limit_images=args.limit_images, global_tables=global_tables if args.limit_images is None else [], dev_globals=dev_globals,
        prod_globals=prod_globals, only=only, batch_size=_batch_size(settings, args), model_change=pre_mod.model_changed(pre))


def run_preconditions(settings: SyncSettings, args: argparse.Namespace, dev: Any, prod: Any) -> list:
    """Phase 0 on both connections; returns every result (the caller fails when any is not ok)."""
    required = _required(settings)
    pre = [pre_mod.required_tables_config(required)]
    pre.append(pre_mod.session_settings(dev, prod))
    if not all(p["ok"] for p in pre):  # the digests of the next checks would not be comparable
        return pre
    pre.append(pre_mod.schema_parity(dev, prod))
    pre.append(pre_mod.column_parity(dev, prod))
    pre.append(pre_mod.model_parity(dev, prod))
    pre.append(pre_mod.model_pair_change(dev, prod))
    pre.append(pre_mod.running_batches(dev, args.ignore_running_batches))
    pre.append(pre_mod.try_prod_lock(prod))
    return pre


# ---------------------------------------------------------------------------------------------------------------------------------
# the command

def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _emit(doc: report.Document, folder: Path, args: argparse.Namespace, settings: SyncSettings, out) -> None:
    top = args.top if args.top is not None else int(settings.get("report.top_n", report.DEFAULT_TOP_N))
    max_ids = int(settings.get("report.max_ids", report.DEFAULT_MAX_IDS))
    json_copy = args.json_path if args.json_path not in (None, "-") else None
    written = report.write_report(doc, folder, top_n=top, max_ids=max_ids, json_copy=json_copy)
    if args.json_path == "-":
        out.write(report.render_json(doc, max_ids))
    else:
        out.write(written["summary"])


def _failed_report(settings, args, run_id, folder, handshake=(), preconditions=(), out=None) -> None:
    doc = report.Document(env=settings.env, run_id=run_id, tool=TOOL, dry=not args.apply, handshake=list(handshake),
                          preconditions=list(preconditions))
    _emit(doc, folder, args, settings, out or sys.stdout)


def run_promote(args: argparse.Namespace, *, settings: SyncSettings | None = None, session_opener: Callable | None = None,
                capabilities: Callable | None = None, git_sha: str | None = None, now: _dt.datetime | None = None, lock: bool = True,
                out=None, err=None, ask: Callable[[str], str] | None = None) -> int:
    """The whole command; the keyword arguments are the injection points of the tests, the command line passes none."""
    out, err = out or sys.stdout, err or sys.stderr
    try:
        validate_args(args)
        settings = settings or load_settings(args.env)
        if args.verify_only:
            raise NotBuilt(NOT_BUILT_VERIFY)
        run_id = args.run_id or new_run_id()
        folder = run_dir(settings, run_id)
        with contextlib.ExitStack() as stack:
            if lock:
                try:
                    stack.enter_context(exclusive_lock(settings))
                except SyncError as exc:
                    _failed_report(settings, args, run_id, folder, preconditions=[pre_mod.result("lock", False, "held")], out=out)
                    raise exc
            return _locked(args, settings, run_id, folder, session_opener or connect.open_session, capabilities, git_sha,
                           now or _now(), out, ask or input)
    except SyncError as exc:
        print(f"sync: {exc}", file=err)
        return exc.exit_code


def _locked(args, settings: SyncSettings, run_id: str, folder: Path, opener, capabilities, git_sha, now, out, ask) -> int:
    env = settings.env
    mode = "first-seed" if args.first_seed else "normal"
    with opener(env, "P", mode, settings=settings) as conns:
        dev, prod = conns["dev"], conns["prod"]
        out.write(connect.banner(env, settings, str(settings.get("dev.database") or "?")) + "\n")
        try:
            if not args.apply:
                prod.query("SET default_transaction_read_only = on")  # a dry run cannot write even by mistake
            hs = connect.handshake(env, "P", mode, settings=settings, dev=dev, prod=prod, capabilities=capabilities,
                                   git_sha=git_sha, fail_fast=True)
        except connect.HandshakeError as exc:
            _failed_report(settings, args, run_id, folder, handshake=exc.results, out=out)
            raise
        locked = False
        try:
            pre = run_preconditions(settings, args, dev, prod)
            locked = any(p["id"] == "prod_lock" and p["ok"] for p in pre)
            if all(p["ok"] for p in pre):
                inp = gather(settings, args, dev, prod, folder, now, pre)
            else:
                inp = None
            if inp is None or not all(p["ok"] for p in pre):
                _failed_report(settings, args, run_id, folder, handshake=hs, preconditions=pre, out=out)
                bad = [p["id"] for p in pre if not p["ok"]]
                raise PreconditionsFailed("precondition failed: " + ", ".join(bad), pre)
            return _after_plan(args, settings, run_id, folder, inp, hs, pre, prod, out, ask)
        finally:
            if locked:
                pre_mod.release_prod_lock(prod)


def _after_plan(args, settings, run_id, folder, inp: plan_mod.PlanInput, hs, pre, prod, out, ask) -> int:
    env = settings.env
    p = plan_mod.build_plan(inp)
    if p.advice:
        pre = pre + [pre_mod.result("advice_index_rebuild", True, "tables=" + ",".join(p.advice))]
    if not args.apply:
        doc = report.Document(env=env, run_id=run_id, tool=TOOL, dry=True, sections=[p.section], handshake=hs, preconditions=pre)
        doc.apply_command = report.apply_command(TOOL, env, doc.plan_hash, apply_flags(args))
        mark_dry(folder)
        _emit(doc, folder, args, settings, out)
        _print_notes(p, args, out)
        return EXIT_OK

    doc = report.Document(env=env, run_id=run_id, tool=TOOL, dry=False, sections=[p.section], handshake=hs, preconditions=pre)
    plan_hash = p.section.plan_hash()
    refusal = None
    if args.plan_hash is not None and args.plan_hash.lower() != plan_hash:
        refusal = f"the plan changed since the dry run (planned hash {plan_hash}, expected {args.plan_hash.lower()})"
    else:
        blocking = plan_mod.effective_blocking(p, args.allow_mass_change, args.allow_shrink, args.allow_model_change)
        if blocking:
            refusal = "guard tripped before any write: " + ", ".join(blocking)
    if refusal:
        _emit(doc, folder, args, settings, out)
        raise Refusal(refusal)

    if not args.yes:
        database = prod.scalar("select current_database()")
        expected = f"{env} {database}"
        try:
            typed = ask(f"Type '{expected}' to promote the data to prod: ").strip()
        except EOFError:
            typed = ""
        if typed != expected:
            raise Refusal("typed confirmation did not match; nothing was written")
    _emit(doc, folder, args, settings, out)
    raise NotBuilt(NOT_BUILT_APPLY)  # T6: the batches, the files gate per image, committed.json, the global sets and setval go here


def _print_notes(p: plan_mod.Plan, args: argparse.Namespace, out) -> None:
    blocking = plan_mod.effective_blocking(p, args.allow_mass_change, args.allow_shrink, args.allow_model_change)
    problems = plan_mod.lift_problems(p, args.allow_mass_change, args.allow_shrink, args.allow_model_change)
    if blocking:
        out.write("an apply would refuse (exit 3): " + ", ".join(blocking) + "\n")
    if problems:
        out.write("lift flags with no effect: " + ", ".join(problems) + "\n")
    if p.advice:
        out.write("advice: more than 25 % of the rows of " + ", ".join(p.advice)
                  + " change; rebuild their indexes after the apply (owner step)\n")


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_promote(args)
