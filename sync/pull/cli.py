"""P4-T2/T5: `python -m sync.pull` (design P4; interfaces.md a, e, i, k). The database part: reader activity from prod to dev.

Dry run by default: handshake for tool L (H1 to H5; H6, the gate, only once the bug-report step supplies a transport), the preconditions,
the plan from the prod snapshot (one `REPEATABLE READ READ ONLY` transaction as `sync_reader`) against the dev tables, the guards, and the
report of interfaces.md k (`report.json`, `report.md`, stdout summary, `dry` marker, the plan hash and the exact apply command). A dry run
writes nothing to prod or dev and no `pull_state.json`; it exits 0 also when a guard would trip.

`--apply` re-plans, then refuses (exit 3: a different plan hash or a tripped guard that no lift flag removes) before any write. After the
typed confirmation `<env> <dev database>` (what is written is DEV, so the dev database name is what the owner types) the six tables are
mirrored in one dev transaction (`sync.pull.apply`), `pull_state.json` is written right after the commit and again after the bug-report
step (a hook that reports `skipped` until T6). Prod is never written: the connection is the read-only `sync_reader` session.

Exit codes (interfaces.md i): 0 clean or dry run, 1 other failure (a rolled-back apply, a failed bug-report step), 2 handshake or
precondition (including a held lock or a bad flag), 3 guard or plan hash before any write.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Callable

from sync import connect, registry, report
from sync.config import (ENVIRONMENTS, EXIT_GUARD, EXIT_OK, ConfigError, PreconditionError, SyncError, SyncSettings, exclusive_lock,
                         load_settings, mark_dry, new_run_id, run_dir, state_dir)
from sync.model import Klass
from sync.pull import apply as apply_mod
from sync.pull import bugreports, state
from sync.pull import plan as plan_mod
from sync.pull import snapshot as snap

TOOL = plan_mod.TOOL
BASE_STEPS = ("H1", "H2", "H3", "H4", "H5")


class UsageProblem(PreconditionError):
    """A bad flag value (exit 2)."""


class Refusal(SyncError):
    """A plan hash that differs, a tripped guard or a wrong confirmation, before any write (exit 3)."""

    exit_code = EXIT_GUARD


class ApplyFailed(SyncError):
    """The dev transaction was rolled back, or the bug-report step failed after the tables were committed (exit 1)."""


class PreconditionsFailed(PreconditionError):
    def __init__(self, message: str, results: list):
        super().__init__(message)
        self.results = results


# ---------------------------------------------------------------------------------------------------------------------------------
# command line

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m sync.pull", description="Pull the readers' activity (the six class P tables) from prod to dev. "
                                "Dry run with a report unless --apply.")
    p.add_argument("--env", required=True, choices=ENVIRONMENTS)
    p.add_argument("--apply", action="store_true", help="perform the pull (otherwise a dry run that writes only the report folder)")
    p.add_argument("--yes", action="store_true", help="with --apply: skip only the typed confirmation '<env> <dev database>'")
    p.add_argument("--plan-hash", default=None, help="with --apply: refuse (exit 3) if the re-planned hash differs from the dry run's")
    p.add_argument("--allow-empty-source", action="store_true", help="lift the guard: prod has no rows in any class P table while dev has some")
    p.add_argument("--allow-shrink", action="append", default=[], metavar="TABLE", help="lift the shrink guard of one class P table (repeatable)")
    p.add_argument("--allow-growth", action="store_true", help="lift the growth and row-ceiling guards")
    p.add_argument("--skip-files", action="store_true", help="do not pull bug reports")
    p.add_argument("--top", type=int, default=None, help="ids listed per category in report.md and the summary (default sync.report.top_n)")
    p.add_argument("--run-id", default=None, help="run id (sync.run passes it to its children)")
    p.add_argument("--json", dest="json_path", default=None, metavar="PATH", help="also write report.json to PATH ('-' for stdout)")
    return p


def validate_args(args: argparse.Namespace) -> None:
    """Flag checks that need no connection; every failure is exit 2."""
    if args.plan_hash is not None and not args.apply:
        raise UsageProblem("--plan-hash needs --apply")
    if args.plan_hash is not None:
        import re

        if not re.match(r"^[0-9a-fA-F]{8,128}$", args.plan_hash):
            raise UsageProblem("--plan-hash must be the hex hash printed by the dry run")
    if args.yes and not args.apply:
        raise UsageProblem("--yes needs --apply")
    if args.top is not None and args.top < 1:
        raise UsageProblem("--top must be at least 1")
    for t in args.allow_shrink:
        if t not in registry.names(Klass.P):
            raise UsageProblem(f"--allow-shrink names a table that is not class P: {t}")
    if args.run_id is not None:
        try:
            report.check_run_id(args.run_id)
        except report.ReportError:
            raise UsageProblem("--run-id must look like YYYYMMDDTHHMMSSZ-<8 hex>") from None


def apply_flags(args: argparse.Namespace) -> list:
    """The tokens an apply needs besides `--env --apply --plan-hash`: the plan-changing flag and the lifts the dry run was given."""
    flags: list = []
    if args.skip_files:
        flags.append("--skip-files")
    if args.allow_empty_source:
        flags.append("--allow-empty-source")
    for t in args.allow_shrink:
        flags += ["--allow-shrink", t]
    if args.allow_growth:
        flags.append("--allow-growth")
    return flags


def limits_from(settings: SyncSettings) -> plan_mod.Limits:
    def num(key: str, default):
        value = settings.get(f"pull.{key}", default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ConfigError(f"tracked setting sync.pull.{key} must be a non-negative number")
        return value

    return plan_mod.Limits(num("max_growth_factor", plan_mod.DEFAULT_GROWTH_FACTOR), int(num("min_growth_rows", plan_mod.DEFAULT_MIN_GROWTH_ROWS)),
                           int(num("max_rows_per_pull", plan_mod.DEFAULT_MAX_ROWS)),
                           int(num("truncate_threshold", plan_mod.DEFAULT_TRUNCATE_THRESHOLD)), int(num("text_cap", plan_mod.DEFAULT_TEXT_CAP)))


def mode_from(settings: SyncSettings) -> str:
    mode = settings.get("pull.ratings_mode", "skip")
    if mode not in plan_mod.MODES:
        raise ConfigError("tracked setting sync.pull.ratings_mode must be skip or mirror")
    return mode


def handshake_steps(transport: Any) -> tuple:
    return BASE_STEPS + (("H6",) if transport is not None else ())


# ---------------------------------------------------------------------------------------------------------------------------------
# preconditions

def result(check_id: str, ok: bool, detail: str = "") -> dict:
    return {"id": check_id, "ok": bool(ok), "detail": detail[:80]}


def registry_check() -> dict:
    problems = plan_mod.check_registry()
    return result("registry_tables", not problems, ",".join(problems) or "ok")


def session_settings(dev: Any, prod: Any) -> dict:
    from sync.promote import digest as dg

    problems = [f"{side}_{name}" for side, conn in (("dev", dev), ("prod", prod)) for name in dg.pin_session(conn)]
    return result("session_settings", not problems, ",".join(problems) or "ok")


def column_compat(dev: Any, prod: Any, model: dict | None = None) -> dict:
    """Column names and types of the six pulled tables (and `images.id`) are equal on both sides and the names are the model's."""
    sigs = [snap.column_signature(c) for c in (dev, prod)]
    problems = []
    differing = sorted({r[0] for r in set(sigs[0]) ^ set(sigs[1])})
    if differing:
        problems.append("dev_prod_differ=" + ",".join(differing))
    if model is None:
        model = {t: snap.columns(t) for t in plan_mod.EXPECTED_TABLES}
    for side, rows in (("dev", sigs[0]), ("prod", sigs[1])):
        have: dict = {}
        for table, column, *_ in rows:
            have.setdefault(table, []).append(column)
        wrong = [t for t in plan_mod.EXPECTED_TABLES if sorted(have.get(t, [])) != sorted(model[t])]
        if wrong:
            problems.append(f"{side}_not_model=" + ",".join(wrong))
        if "id" not in have.get("images", []):
            problems.append(f"{side}_images_id_unreadable")
    return result("column_compat", not problems, ",".join(problems) or "ok")


def alembic_note(dev: Any, prod: Any) -> dict:
    """A differing alembic_version is a warning only (dev is usually one migration ahead of prod during a release)."""
    try:
        versions = [tuple(sorted(r[0] for r in c.query("select version_num from alembic_version"))) for c in (dev, prod)]
    except connect.DbError:
        return result("alembic_version", True, "warn_unreadable")
    return result("alembic_version", True, "equal" if versions[0] == versions[1] else "warn_differs")


def running_batches(dev: Any) -> dict:
    """A running dev batch only warns: the pull writes class P tables, which no batch job writes."""
    try:
        n = int(dev.scalar("select count(*) from batch_runs where status = 'started'"))
    except connect.DbError:
        return result("running_batches", True, "warn_unreadable")
    return result("running_batches", True, f"warn_running={n}" if n else "none")


def ratings_consistency(payload: dict | None, mode: str, prod_count: int) -> dict:
    """Mode against the capabilities `features.ratings` and the snapshot: only warnings."""
    routes = None
    if isinstance(payload, dict) and isinstance(payload.get("features"), dict):
        routes = payload["features"].get("ratings")
    warns = []
    if routes is None:
        warns.append("warn_routes_unknown")
    elif routes is False and prod_count > 0:
        warns.append("warn_rows_but_routes_off")
    elif routes is False and mode == "mirror":
        warns.append("warn_mirror_but_routes_off")
    elif routes is True and mode == "skip":
        warns.append("warn_routes_on_mode_skip")
    return result("ratings_consistency", True, ",".join(warns) or f"ok_{mode}")


def run_preconditions(settings: SyncSettings, dev: Any, prod: Any, directory: Path) -> tuple:
    """Phase 0 on both connections; returns `(results, previous state or None)` (the caller fails when any result is not ok)."""
    pre = [registry_check(), session_settings(dev, prod)]
    previous = None
    if all(p["ok"] for p in pre):
        pre.append(column_compat(dev, prod))
        pre.append(alembic_note(dev, prod))
        pre.append(running_batches(dev))
        previous, status = state.load_state(directory, settings.env)
        pre.append(result("previous_state", True, status))
    return pre, previous


# ---------------------------------------------------------------------------------------------------------------------------------
# planning with live connections

def gather(mode: str, limits: plan_mod.Limits, dev: Any, prod: Any, previous: dict | None, ledger: frozenset, skip_files: bool) -> tuple:
    """Read prod (inside the caller's open snapshot) and dev (own read-only snapshot) into a `PlanInput`; returns it with the ratings
    fingerprint and the prod image count. Nothing here writes."""
    cap, ceiling = limits.text_cap, limits.max_rows_per_pull
    prod_reads = {t: snap.read_table(prod, t, cap, ceiling, with_truncated=True) for t in plan_mod.mirror_tables()}
    fingerprint = snap.ratings_fingerprint(prod, mode)
    prod_images = snap.read_image_ids(prod)
    prod_ratings: dict = {}
    if mode == "mirror":
        prod_ratings = snap.read_ratings(prod, cap)
    ratings_trunc_sql = snap.truncated_sql(plan_mod.RATINGS, cap)
    prod_reads[plan_mod.RATINGS] = plan_mod.TableRead(fingerprint["prod_count"], {}, True, int(prod.scalar(ratings_trunc_sql)))
    with snap.snapshot(dev):
        dev_reads = {t: snap.read_table(dev, t, cap, ceiling) for t in plan_mod.mirror_tables()}
        dev_ratings: dict = {}
        dev_images: frozenset = frozenset()
        if mode == "mirror":
            dev_ratings = snap.read_ratings(dev, cap)
            dev_images = snap.read_image_ids(dev)
            dev_reads[plan_mod.RATINGS] = plan_mod.TableRead(len(dev_ratings))
        else:
            dev_reads[plan_mod.RATINGS] = plan_mod.TableRead(int(dev.scalar(snap.count_sql(plan_mod.RATINGS))))
    inp = plan_mod.PlanInput(env="", mode=mode, prod=prod_reads, dev=dev_reads, prod_ratings=prod_ratings, dev_ratings=dev_ratings,
                             ledger=ledger, prod_images=prod_images, dev_images=dev_images, previous=previous, limits=limits,
                             skip_files=skip_files)
    return inp, fingerprint, len(prod_images)


# ---------------------------------------------------------------------------------------------------------------------------------
# the command

def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _emit(doc: report.Document, folder: Path, args: argparse.Namespace, settings: SyncSettings, out, echo: bool = True) -> None:
    sink = getattr(args, "document_sink", None)  # sync.run collects each child's document for the combined report
    if sink is not None:
        sink(doc)
    top = args.top if args.top is not None else int(settings.get("report.top_n", report.DEFAULT_TOP_N))
    max_ids = int(settings.get("report.max_ids", report.DEFAULT_MAX_IDS))
    json_copy = args.json_path if args.json_path not in (None, "-") else None
    written = report.write_report(doc, folder, top_n=top, max_ids=max_ids, json_copy=json_copy)
    if not echo:
        return
    if args.json_path == "-":
        out.write(report.render_json(doc, max_ids))
    else:
        out.write(written["summary"])


def _failed_report(settings, args, run_id, folder, handshake=(), preconditions=(), out=None) -> None:
    doc = report.Document(env=settings.env, run_id=run_id, tool=TOOL, dry=not args.apply, handshake=list(handshake),
                          preconditions=list(preconditions))
    _emit(doc, folder, args, settings, out or sys.stdout)


def run_pull(args: argparse.Namespace, *, settings: SyncSettings | None = None, session_opener: Callable | None = None,
             capabilities: Callable | None = None, transport: Any = None, git_sha: str | None = None, now: _dt.datetime | None = None,
             lock: bool = True, out=None, err=None, ask: Callable[[str], str] | None = None) -> int:
    """The whole command; the keyword arguments are the injection points of the tests, the command line passes none."""
    out, err = out or sys.stdout, err or sys.stderr
    try:
        validate_args(args)
        settings = settings or load_settings(args.env)
        mode, limits = mode_from(settings), limits_from(settings)
        run_id = args.run_id or new_run_id()
        folder = run_dir(settings, run_id)
        with contextlib.ExitStack() as stack:
            if lock:
                try:
                    stack.enter_context(exclusive_lock(settings))
                except SyncError as exc:
                    _failed_report(settings, args, run_id, folder, preconditions=[result("lock", False, "held")], out=out)
                    raise exc
            return _locked(args, settings, mode, limits, run_id, folder, session_opener or connect.open_session, capabilities, transport,
                           git_sha, now, out, ask or input)
    except SyncError as exc:
        print(f"sync: {exc}", file=err)
        return exc.exit_code


def _locked(args, settings: SyncSettings, mode: str, limits, run_id: str, folder: Path, opener, capabilities, transport, git_sha, now,
            out, ask) -> int:
    env = settings.env
    cache: dict = {}

    def caps(url: str) -> dict:  # the handshake's H5 call, kept for the ratings consistency note
        payload = (capabilities or connect.fetch_capabilities)(url)
        cache["payload"] = payload
        return payload

    with opener(env, "L", "normal", settings=settings) as conns:
        dev, prod = conns["dev"], conns["prod"]
        out.write(connect.banner(env, settings, str(settings.get("dev.database") or "?"), str(settings.get("prod.reader_role")),
                                 read_only=False) + "\n")
        try:
            hs = connect.handshake(env, "L", "normal", settings=settings, dev=dev, prod=prod, capabilities=caps, transport=transport,
                                   git_sha=git_sha, fail_fast=True, steps=handshake_steps(transport))
        except connect.HandshakeError as exc:
            _failed_report(settings, args, run_id, folder, handshake=exc.results, out=out)
            raise
        directory = state_dir(settings, create=True)
        pre, previous = run_preconditions(settings, dev, prod, directory)
        if not all(p["ok"] for p in pre):
            _failed_report(settings, args, run_id, folder, handshake=hs, preconditions=pre, out=out)
            raise PreconditionsFailed("precondition failed: " + ", ".join(p["id"] for p in pre if not p["ok"]), pre)
        ledger = state.load_ledger(directory) if mode == "mirror" else frozenset()
        with snap.snapshot(prod):
            snapshot_text, snapshot_at = snap.snapshot_facts(prod)
            inp, fingerprint, image_count = gather(mode, limits, dev, prod, previous, ledger, args.skip_files)
            inp.env = env
            pre = pre + [ratings_consistency(cache.get("payload"), mode, fingerprint["prod_count"])]
            p = plan_mod.build_plan(inp)
            facts = {"snapshot": snapshot_text, "snapshot_at": snapshot_at, "ratings": fingerprint, "prod_image_ids": image_count}
            return _after_plan(args, settings, run_id, folder, inp, p, hs, pre, dev, prod, directory, facts, transport, now, out, ask)


def _after_plan(args, settings, run_id, folder, inp, p: plan_mod.Plan, hs, pre, dev, prod, directory, facts, transport, now, out, ask) -> int:
    env = settings.env
    lists, checks = bugreports.plan_report(settings, None if args.skip_files else transport)
    p.section.lists = lists
    p.section.checks = list(p.section.checks) + checks
    p.section.flags["bug_reports"] = "on" if (transport is not None and not args.skip_files) else "off"  # part of the structural plan hash
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
        blocking = plan_mod.effective_blocking(p, args.allow_empty_source, args.allow_shrink, args.allow_growth)
        if blocking:
            refusal = "guard tripped before any write: " + ", ".join(blocking)
    if refusal:
        _emit(doc, folder, args, settings, out)
        raise Refusal(refusal)

    if not args.yes:
        expected = f"{env} {dev.scalar('select current_database()')}"
        try:
            typed = ask(f"Type '{expected}' to replace the reader-activity tables of the dev database with prod's: ").strip()
        except EOFError:
            typed = ""
        if typed != expected:
            raise Refusal("typed confirmation did not match; nothing was written")
    _emit(doc, folder, args, settings, out, echo=False)  # the plan as read, on disk before the first write
    outcome = apply_mod.run_apply(inp=inp, dev=dev, prod=prod, directory=directory, run_id=run_id, now=(now or _now()), out=out)
    if not outcome.ok:
        failed = report.Document(env=env, run_id=run_id, tool=TOOL, dry=False, sections=[p.section], handshake=hs, preconditions=pre,
                                 result={plan_mod.SECTION: {e.name: report.ResultEntry(failure=outcome.failure) for e in p.section.entries}})
        _emit(failed, folder, args, settings, out)
        raise ApplyFailed(f"the pull was rolled back, dev is unchanged ({outcome.failure}); run it again")

    finished = now or _now()
    tables = {t: {"prod": inp.prod[t].count, "replaced": outcome.replaced.get(t, 0)} for t in plan_mod.EXPECTED_TABLES}
    doc_state = state.build_state(env=env, run_id=run_id, now=finished, snapshot_at=facts["snapshot_at"], snapshot=facts["snapshot"],
                                  ratings=facts["ratings"], prod_image_ids=facts["prod_image_ids"], tables=tables)
    state.write_state(directory, doc_state)  # stage 1: right after the dev commit
    if outcome.ledger is not None:
        state.write_ledger(directory, outcome.ledger, env, finished)
    bug = state.skipped_bug_reports() if args.skip_files else bugreports.run(settings, transport, directory, run_id)
    doc_state["bug_reports"] = {k: bug[k] for k in state.BUG_FIELDS}
    state.write_state(directory, doc_state)  # stage 2: after the bug-report step

    done = report.Document(env=env, run_id=run_id, tool=TOOL, dry=False, sections=[p.section], handshake=hs, preconditions=pre,
                           result=result_of(p, outcome, inp))
    _emit(done, folder, args, settings, out)
    _print_result(p, outcome, out)
    if bug.get("failed"):
        raise ApplyFailed(f"{bug['failed']} bug report(s) failed; the tables are committed (state: {bug['status']})")
    return EXIT_OK


def result_of(p: plan_mod.Plan, outcome: apply_mod.ApplyOutcome, inp: plan_mod.PlanInput) -> dict:
    """The `result` block (interfaces.md k): per table what the apply wrote to dev."""
    data: dict = {}
    for e in p.section.entries:
        if e.name == plan_mod.RATINGS:
            part = outcome.partition
            if part is None:
                data[e.name] = report.ResultEntry()
            else:
                pr = inp.prod_ratings
                data[e.name] = report.ResultEntry(
                    added=len(part.insert), overwritten=len(part.update) + len(part.delete),
                    skipped={"skipped_unknown_image": [pr[k].id for k in part.skipped_unknown]} if part.skipped_unknown else {})
        else:
            data[e.name] = report.ResultEntry(added=e.added.n, overwritten=e.overwritten.n)
    return {plan_mod.SECTION: data}


def _print_result(p: plan_mod.Plan, outcome: apply_mod.ApplyOutcome, out) -> None:
    rows = sum(outcome.replaced.values())
    out.write(f"applied: {rows} rows written to dev in {len(outcome.replaced)} tables"
              + (f"; truncated: {', '.join(outcome.truncated)}" if outcome.truncated else "") + "\n")
    part = outcome.partition
    if part is not None:
        out.write(f"ratings: {len(part.insert)} inserted, {len(part.update)} updated, {len(part.delete)} deleted, "
                  f"{len(part.kept_retired)} kept (image retired on prod), {len(part.kept_dev_made)} kept (made on dev), "
                  f"{len(part.skipped_unknown)} skipped (image unknown on dev), {outcome.archived} archived\n")
    else:
        out.write("ratings: skipped (mode skip: dev ratings untouched)\n")


def _print_notes(p: plan_mod.Plan, args: argparse.Namespace, out) -> None:
    blocking = plan_mod.effective_blocking(p, args.allow_empty_source, args.allow_shrink, args.allow_growth)
    problems = plan_mod.lift_problems(p, args.allow_empty_source, args.allow_shrink, args.allow_growth)
    if blocking:
        out.write("an apply would refuse (exit 3): " + ", ".join(blocking) + "\n")
    if problems:
        out.write("lift flags with no effect: " + ", ".join(problems) + "\n")
    if p.first_pull:
        out.write("first pull: no previous pull_state.json, the shrink and growth guards have no baseline\n")
    if p.truncated_values:
        out.write(f"text cap: {p.truncated_values} value(s) would be cut\n")
    if p.mode == "skip":
        out.write("ratings: skipped (mode skip); the fingerprint is still recorded\n")


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    transport = None
    if not args.skip_files:  # the real command: bug reports go through the gate (H6 joins the handshake)
        try:
            from sync.files.transport import SshTransport

            settings = load_settings(args.env)
            transport = SshTransport.from_settings(settings)
        except SyncError as exc:
            print(f"sync: {exc}", file=sys.stderr)
            return exc.exit_code
        return run_pull(args, settings=settings, transport=transport)
    return run_pull(args)
