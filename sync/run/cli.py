"""P7-T1: `python -m sync.run` (design P6/P7 part B; interfaces.md a, e, i, j, k). The driver of the dev/prod sync.

Steps, in order, each an in-process call of the tool's own `run_*` entry point (no subprocess, so exit codes and plans are structured):

    0 handshake (tool R: H1 to H10 once, up front)   1 pull (sync.pull)   2 files (sync.files promote)   3 data (sync.promote)   4 verify

Dry run (default): every step plans (its own dry run, in this run's `dry` folder), the sections are merged into ONE combined report
(`report.combined_document`: one section per step, the dependency note, `waiting_for_files` for the images whose file the files section
would add or replace, the combined plan hash and the exact apply command) and written to `runs/<runid>/report.json|md` plus `run.json`.
Exit 0, also when a guard would trip.

`--apply [--plan-hash H]`: re-plans (the children in dry mode, in a scratch dry folder), refuses (exit 3) when the combined hash differs or a
guard is tripped and no lift flag removes it, shows the banner and the combined report, asks for `<env> <prod database read back from the
server>` ONCE (`--yes` skips only that), then runs the steps for real under ONE run id and ONE state-directory lock (the children get
`lock=False`) with `--yes` and no `--plan-hash` of their own. The run stops at the first failing step with that step's exit code and names
the completed steps; every step is resumable, so the owner runs the same command again.

Tunnel: one ssh tunnel for the whole run. The driver opens it once and hands the children a session opener that reuses it
(`connect.open_session(..., open_tunnel_fn=<no-op>)`); the children still open their own database sessions and run their own handshake
steps (cheap, and each tool's own contract), but not another tunnel (a second `ssh -L` on the same local port would fail).

Verify (P6) is not built: the verify step is a one-function swap point (`verify_step`). While `sync.verify` does not exist it reports
`skipped_with_reason: not_built` in the plan and, in an apply that ran the data step, accepts the verify phase `sync.promote` runs at its end.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import functools
import io
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from sync import connect, registry, report
from sync.config import (ENVIRONMENTS, EXIT_FAILURE, EXIT_GUARD, EXIT_OK, EXIT_VERIFY, PreconditionError, SyncError, SyncSettings, atomic_write,
                         exclusive_lock, load_settings, new_run_id, run_dir)
from sync.files import promote as files_cli
from sync.model import Klass
from sync.promote import cli as promote_cli
from sync.promote import plan as promote_plan
from sync.pull import cli as pull_cli
from sync.pull import plan as pull_plan

TOOL = "sync.run"
STEPS = ("handshake", "pull", "files", "data", "verify")
CHILD_STEPS = ("pull", "files", "data")
RUN_FILE = "run.json"
NOT_BUILT = "not_built"


class UsageProblem(PreconditionError):
    """A bad flag value (exit 2)."""


class Refusal(SyncError):
    """A plan hash that differs, a guard that no flag lifts or a wrong confirmation, before any write (exit 3)."""

    exit_code = EXIT_GUARD


@dataclass
class StepResult:
    """What a step runner returns: the exit code and the document the child emitted last (None when it emitted none)."""

    code: int
    doc: report.Document | None = None


@dataclass
class Ctx:
    """Everything a step runner needs; the keyword arguments of `run_driver` are the injection points of the tests."""

    env: str
    settings: SyncSettings
    args: argparse.Namespace
    out: Any
    err: Any
    opener: Callable
    capabilities: Callable | None = None
    transport: Any = None
    transport_factory: Callable | None = None
    git_sha: str | None = None
    now: _dt.datetime | None = None
    base_dir: str | None = None
    ask: Callable[[str], str] = input
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------------------------------------------
# command line

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m sync.run", description="Driver of the dev/prod sync: handshake, pull, files, data, verify. "
                                "Dry run with one combined report unless --apply.")
    p.add_argument("--env", required=True, choices=ENVIRONMENTS)
    p.add_argument("--apply", action="store_true", help="perform the sync (otherwise a dry run that writes only the report folder)")
    p.add_argument("--yes", action="store_true", help="with --apply: skip only the typed confirmation '<env> <prod database>'")
    p.add_argument("--plan-hash", default=None, help="with --apply: refuse (exit 3) if the re-planned combined hash differs from the dry run's")
    p.add_argument("--first-seed", action="store_true", help="prod is in state 'seeding' (memes.state); resumable by running again")
    p.add_argument("--stop-after", default=None, choices=STEPS, metavar="STEP", help="stop after this step (" + ", ".join(STEPS) + "); for rehearsal")
    p.add_argument("--top", type=int, default=None, help="ids listed per category in report.md and the summary (default sync.report.top_n)")
    p.add_argument("--run-id", default=None, help="run id (default: a new one)")
    p.add_argument("--json", dest="json_path", default=None, metavar="PATH", help="also write the combined report.json to PATH ('-' for stdout)")
    p.add_argument("--allow-mass-change", type=int, default=None, metavar="N", help="data: lift the mass-change guard; N must equal the planned count")
    p.add_argument("--allow-shrink", action="append", default=[], metavar="TABLE",
                   help="lift the shrink guard of one table: a class D table goes to the data step, a class P table to the pull (repeatable)")
    p.add_argument("--allow-model-change", action="store_true", help="data: lift the model-change guard")
    p.add_argument("--allow-empty-source", action="store_true", help="pull: lift the guard 'prod has no rows in any class P table while dev has some'")
    p.add_argument("--allow-growth", action="store_true", help="pull: lift the growth and row-ceiling guards")
    p.add_argument("--ignore-running-batches", action="store_true", help="data: lift the dev running-batch precondition")
    p.add_argument("--skip-files", action="store_true", help="pull: do not pull bug reports (the files step still promotes the image files)")
    return p


def split_shrink(tables: list) -> tuple:
    """`--allow-shrink` tables by owner: `(class D for the data step, class P for the pull)`; any other name is a usage problem."""
    d, p = set(registry.names(Klass.D)), set(registry.names(Klass.P))
    bad = [t for t in tables if t not in d and t not in p]
    if bad:
        raise UsageProblem(f"--allow-shrink names a table that is neither class D nor class P: {', '.join(sorted(bad))}")
    return [t for t in tables if t in d], [t for t in tables if t in p]


def validate_args(args: argparse.Namespace) -> None:
    """Flag checks that need no connection; every failure is exit 2."""
    if args.plan_hash is not None and not args.apply:
        raise UsageProblem("--plan-hash needs --apply")
    if args.plan_hash is not None and not report._HEX.match(args.plan_hash):
        raise UsageProblem("--plan-hash must be the hex hash printed by the dry run")
    if args.yes and not args.apply:
        raise UsageProblem("--yes needs --apply")
    if args.top is not None and args.top < 1:
        raise UsageProblem("--top must be at least 1")
    if args.allow_mass_change is not None and args.allow_mass_change < 0:
        raise UsageProblem("--allow-mass-change must be the planned changed count (not negative)")
    split_shrink(args.allow_shrink)
    if args.run_id is not None:
        try:
            report.check_run_id(args.run_id)
        except report.ReportError:
            raise UsageProblem("--run-id must look like YYYYMMDDTHHMMSSZ-<8 hex>") from None


def selected_steps(args: argparse.Namespace) -> list:
    """The steps of this run: all of them, or up to and including `--stop-after`."""
    if args.stop_after is None:
        return list(STEPS)
    return list(STEPS[:STEPS.index(args.stop_after) + 1])


def apply_flags(args: argparse.Namespace) -> list:
    """The tokens an apply needs besides `--env --apply --plan-hash`: the plan-changing flags and the lifts the dry run was given."""
    flags: list = []
    if args.first_seed:
        flags.append("--first-seed")
    if args.stop_after is not None:
        flags += ["--stop-after", args.stop_after]
    if args.skip_files:
        flags.append("--skip-files")
    if args.allow_mass_change is not None:
        flags += ["--allow-mass-change", str(args.allow_mass_change)]
    for t in args.allow_shrink:
        flags += ["--allow-shrink", t]
    if args.allow_model_change:
        flags.append("--allow-model-change")
    if args.allow_empty_source:
        flags.append("--allow-empty-source")
    if args.allow_growth:
        flags.append("--allow-growth")
    if args.ignore_running_batches:
        flags.append("--ignore-running-batches")
    return flags


def child_argv(step: str, args: argparse.Namespace, run_id: str, apply: bool) -> list:
    """The command line of one child (parsed by the child's own parser, so its own validation applies). Lifting flags go to the step that
    owns them; an apply passes `--yes` (the driver asked once) and never a `--plan-hash` (the driver bound the combined hash)."""
    d_shrink, p_shrink = split_shrink(args.allow_shrink)
    tail = ["--run-id", run_id] + (["--apply", "--yes"] if apply else [])
    if step == "pull":
        argv = ["--env", args.env]
        if args.skip_files:
            argv.append("--skip-files")
        if args.allow_empty_source:
            argv.append("--allow-empty-source")
        for t in p_shrink:
            argv += ["--allow-shrink", t]
        if args.allow_growth:
            argv.append("--allow-growth")
        return argv + tail
    if step == "files":
        return ["promote", "--env", args.env] + tail
    if step == "data":
        argv = ["--env", args.env]
        if args.first_seed:
            argv.append("--first-seed")
        if args.allow_mass_change is not None:
            argv += ["--allow-mass-change", str(args.allow_mass_change)]
        for t in d_shrink:
            argv += ["--allow-shrink", t]
        if args.allow_model_change:
            argv.append("--allow-model-change")
        if args.ignore_running_batches:
            argv.append("--ignore-running-batches")
        return argv + tail
    raise ValueError(f"no child for step {step!r}")


# ---------------------------------------------------------------------------------------------------------------------------------
# step runners (the default ones call the tools' entry points in process)

class _Prefixed:
    """Forwards a child's stdout lines with a `[step] ` prefix."""

    def __init__(self, out, prefix: str):
        self.out, self.prefix, self._bol = out, prefix, True

    def write(self, text: str) -> int:
        for part in text.splitlines(keepends=True):
            if self._bol:
                self.out.write(self.prefix)
            self.out.write(part)
            self._bol = part.endswith("\n")
        return len(text)

    def flush(self) -> None:
        flush = getattr(self.out, "flush", None)
        if flush:
            flush()


def _refuse_prompt(prompt: str) -> str:
    return ""  # a child never asks (the driver passes --yes); an unexpected prompt fails the confirmation


def _child_io(ctx: Ctx, step: str, apply: bool):
    return (_Prefixed(ctx.out, f"[{step}] ") if apply else io.StringIO()), ctx.err


def run_pull_step(ctx: Ctx, run_id: str, apply: bool) -> StepResult:
    docs: list = []
    args = pull_cli.build_parser().parse_args(child_argv("pull", ctx.args, run_id, apply))
    args.document_sink = docs.append
    out, err = _child_io(ctx, "pull", apply)
    transport = None if ctx.args.skip_files else ctx.transport
    code = pull_cli.run_pull(args, settings=ctx.settings, session_opener=ctx.opener, capabilities=ctx.capabilities, transport=transport,
                             git_sha=ctx.git_sha, now=ctx.now, lock=False, out=out, err=err, ask=_refuse_prompt)
    return StepResult(code, docs[-1] if docs else None)


def run_files_step(ctx: Ctx, run_id: str, apply: bool) -> StepResult:
    docs: list = []
    args = files_cli.build_parser().parse_args(child_argv("files", ctx.args, run_id, apply))
    args.document_sink = docs.append
    out, err = _child_io(ctx, "files", apply)
    code = files_cli.run_promote(args, settings=ctx.settings, session_opener=ctx.opener, transport_factory=ctx.transport_factory,
                                 base_dir=ctx.base_dir, lock=False, out=out, err=err, ask=_refuse_prompt)
    return StepResult(code, docs[-1] if docs else None)


def run_data_step(ctx: Ctx, run_id: str, apply: bool) -> StepResult:
    docs: list = []
    args = promote_cli.build_parser().parse_args(child_argv("data", ctx.args, run_id, apply))
    args.document_sink = docs.append
    out, err = _child_io(ctx, "data", apply)
    code = promote_cli.run_promote(args, settings=ctx.settings, session_opener=ctx.opener, capabilities=ctx.capabilities,
                                   git_sha=ctx.git_sha, now=ctx.now, lock=False, out=out, err=err, ask=_refuse_prompt)
    return StepResult(code, docs[-1] if docs else None)


DEFAULT_RUNNERS = {"pull": run_pull_step, "files": run_files_step, "data": run_data_step}


# ---------------------------------------------------------------------------------------------------------------------------------
# verify (P6 plugs in here)

def verify_section() -> report.Section:
    """The `verify` section of the combined report. `sync.verify` (P6) is not built: one informational check says so, and it is part of
    the plan hash (a plan made after P6 lands has a different one). P6 replaces this function with the drift of its own report."""
    return report.Section("verify", "sync.verify", checks=[{"id": "verify", "status": "skipped_with_reason", "severity": "info",
                                                              "detail": NOT_BUILT}])


def verify_step(data_ran_ok: bool, data_exit: int | None) -> dict:
    """The apply-time verify step: `{status, exit_code, reason?}` for `run.json`. While P6 is not built, the only verification is the one
    `sync.promote` runs at the end of its own apply (it is in the data step's result block under `verify`); exit 4 from the data step
    is a verification failure. P6 replaces this function with a call of `sync.verify` (library entry point, same run id and lock)."""
    if data_exit == EXIT_VERIFY:
        return {"status": "failed", "exit_code": data_exit, "reason": "promote_verify_phase"}
    if data_ran_ok:
        return {"status": "ok", "exit_code": EXIT_OK, "reason": "promote_verify_phase"}
    return {"status": "skipped_with_reason", "exit_code": None, "reason": NOT_BUILT}


# ---------------------------------------------------------------------------------------------------------------------------------
# planning the combined report

@dataclass
class Plan:
    sections: list = field(default_factory=list)
    handshake: list = field(default_factory=list)
    preconditions: list = field(default_factory=list)
    docs: dict = field(default_factory=dict)  # step -> last Document of the child
    failed: tuple | None = None  # (step, exit code)


def _prefixed_pre(step: str, doc: report.Document | None) -> list:
    return [{"id": f"{step}.{p['id']}", "ok": p["ok"], "detail": p["detail"]} for p in (doc.preconditions if doc else [])]


def plan_steps(ctx: Ctx, steps: list, run_id: str, runners: dict) -> Plan:
    """Every selected child in dry mode under `run_id`, then the verify section and the `waiting_for_files` marking. Stops at the first
    child that fails (handshake or precondition), keeping what was planned so far and the failing child's own handshake and preconditions."""
    plan = Plan()
    for step in CHILD_STEPS:
        if step not in steps:
            continue
        result = runners[step](ctx, run_id, False)
        plan.docs[step] = result.doc
        plan.preconditions += _prefixed_pre(step, result.doc)
        if result.code != EXIT_OK:
            if result.doc is not None:
                plan.handshake += [{"step": f"{step}.{h['step']}", "ok": h["ok"], "detail": h["detail"]}
                                   for h in result.doc.handshake if not h["ok"]]
            plan.failed = (step, result.code)
            return plan
        if result.doc is not None:
            plan.sections += result.doc.sections
    if "verify" in steps:
        plan.sections.append(verify_section())
    mark_dependencies(plan.sections)
    return plan


def mark_dependencies(sections: list) -> None:
    """The dependency rule of interfaces.md k: images whose file the files section would add or replace wait for it in the data section."""
    by_name = {s.name: s for s in sections}
    files, data = by_name.get("files"), by_name.get("data")
    if files is None or data is None or not files.entries or not data.entries:
        return
    fe = files.entries[0]
    target = next((e for e in data.entries if e.name == promote_plan.ENTRY), None)
    if target is not None:
        report.mark_waiting_for_files(target, list(fe.added.ids) + list(fe.overwritten.ids))


def blocking_guards(sections: list, args: argparse.Namespace) -> list:
    """`step:guard` for every tripped guard that no lift flag removes, decided by each tool's own rule."""
    d_shrink, p_shrink = split_shrink(args.allow_shrink)
    blocking = []
    for sec in sections:
        holder = SimpleNamespace(section=sec)
        if sec.name == "pull":
            ids = pull_plan.effective_blocking(holder, args.allow_empty_source, p_shrink, args.allow_growth)
        elif sec.name == "data":
            ids = promote_plan.effective_blocking(holder, args.allow_mass_change, d_shrink, args.allow_model_change)
        else:  # files: every tripped guard stops the apply (the tool has no lift flag)
            ids = [g.id for g in sec.guards if g.tripped] if sec.name == "files" else []
        blocking += [f"{sec.name}:{i}" for i in ids]
    return blocking


# ---------------------------------------------------------------------------------------------------------------------------------
# run.json

def _now_s() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RunFile:
    """`runs/<runid>/run.json` (interfaces.md a): run id, env, dry, flags, steps, the combined plan hash. Rewritten atomically after each step."""

    def __init__(self, folder: Path, env: str, run_id: str, dry: bool, args: argparse.Namespace, steps: list):
        self.path = folder / RUN_FILE
        self.doc = {"run_id": run_id, "env": env, "dry": dry, "flags": self._flags(args), "plan_hash": None,
                    "steps": [{"name": n, "status": "pending", "started_at": None, "finished_at": None, "exit_code": None} for n in STEPS]}
        for s in self.doc["steps"]:
            if s["name"] not in steps:
                s.update(status="skipped_with_reason", reason="stop_after")

    @staticmethod
    def _flags(args: argparse.Namespace) -> dict:
        return {"apply": bool(args.apply), "yes": bool(args.yes), "first_seed": bool(args.first_seed), "stop_after": args.stop_after,
                "skip_files": bool(args.skip_files), "allow_mass_change": args.allow_mass_change, "allow_shrink": list(args.allow_shrink),
                "allow_model_change": bool(args.allow_model_change), "allow_empty_source": bool(args.allow_empty_source),
                "allow_growth": bool(args.allow_growth), "ignore_running_batches": bool(args.ignore_running_batches)}

    def step(self, name: str) -> dict:
        return next(s for s in self.doc["steps"] if s["name"] == name)

    def update(self, name: str, **fields) -> None:
        self.step(name).update(fields)
        self.write()

    def write(self) -> None:
        atomic_write(self.path, json.dumps(self.doc, indent=2, ensure_ascii=True) + "\n")


# ---------------------------------------------------------------------------------------------------------------------------------
# reports

def _emit(doc: report.Document, folder: Path, args: argparse.Namespace, settings: SyncSettings, out, echo: bool = True) -> None:
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


def _failed_report(settings, args, run_id, folder, handshake=(), preconditions=(), out=None, sections=(), dry=None) -> None:
    doc = report.Document(env=settings.env, run_id=run_id, tool=TOOL, dry=(not args.apply) if dry is None else dry, handshake=list(handshake),
                          preconditions=list(preconditions), sections=list(sections))
    _emit(doc, folder, args, settings, out or sys.stdout)


def _merge_results(docs: dict) -> dict:
    merged: dict = {}
    for step in CHILD_STEPS:
        doc = docs.get(step)
        if doc is not None and doc.result:
            merged.update(doc.result)
    return merged


# ---------------------------------------------------------------------------------------------------------------------------------
# step 0

def default_handshake(ctx: Ctx, mode: str) -> tuple:
    """Step 0: tool R, H1 to H10 once, over one session (writer and reader). Returns `(results, prod database read back from the server)`;
    a failing step raises `connect.HandshakeError` (exit 2, `.results` holds the steps so far)."""
    env, settings = ctx.env, ctx.settings
    with ctx.opener(env, "R", mode, settings=settings) as conns:
        dev, prod, reader = conns["dev"], conns["prod"], conns.get("prod_reader_conn")
        ctx.out.write(connect.banner(env, settings, str(settings.get("dev.database") or "?")) + "\n")
        results = connect.handshake(env, "R", mode, settings=settings, dev=dev, prod=prod, prod_reader_conn=reader,
                                    capabilities=ctx.capabilities, transport=ctx.transport, git_sha=ctx.git_sha, fail_fast=True)
        database = str(prod.scalar("select current_database()"))
    return results, database


# ---------------------------------------------------------------------------------------------------------------------------------
# the command

def run_driver(args: argparse.Namespace, *, settings: SyncSettings | None = None, session_opener: Callable | None = None,
               capabilities: Callable | None = None, transport: Any = None, transport_factory: Callable | None = None,
               git_sha: str | None = None, now: _dt.datetime | None = None, base_dir: str | None = None, lock: bool = True,
               runners: dict | None = None, handshake_fn: Callable | None = None, out=None, err=None,
               ask: Callable[[str], str] | None = None) -> int:
    """The whole command; the keyword arguments are the injection points of the tests, the command line passes none."""
    out, err = out or sys.stdout, err or sys.stderr
    try:
        validate_args(args)
        settings = settings or load_settings(args.env)
        run_id = args.run_id or new_run_id()
        folder = run_dir(settings, run_id)
        with contextlib.ExitStack() as stack:
            if lock:
                try:
                    stack.enter_context(exclusive_lock(settings))
                except SyncError as exc:
                    _failed_report(settings, args, run_id, folder, preconditions=[{"id": "lock", "ok": False, "detail": "held"}], out=out)
                    raise exc
            opener = session_opener
            if opener is None:  # the real command: one tunnel for the whole run, shared with the children
                from sync.files.transport import SshTransport
                from sync.tunnel import open_tunnel

                try:
                    stack.enter_context(open_tunnel(settings))
                except SyncError as exc:
                    _failed_report(settings, args, run_id, folder, preconditions=[{"id": "tunnel", "ok": False, "detail": "not_up"}], out=out)
                    raise exc
                opener = functools.partial(connect.open_session, open_tunnel_fn=lambda s: contextlib.nullcontext())
                if transport is None:
                    transport = SshTransport.from_settings(settings)
                if transport_factory is None:
                    transport_factory = SshTransport.from_settings
            ctx = Ctx(env=args.env, settings=settings, args=args, out=out, err=err, opener=opener, capabilities=capabilities,
                      transport=transport, transport_factory=transport_factory, git_sha=git_sha, now=now, base_dir=base_dir,
                      ask=ask or input)
            return _locked(ctx, run_id, folder, {**DEFAULT_RUNNERS, **(runners or {})}, handshake_fn or default_handshake)
    except SyncError as exc:
        print(f"sync: {exc}", file=err)
        return exc.exit_code


def _locked(ctx: Ctx, run_id: str, folder: Path, runners: dict, handshake_fn: Callable) -> int:
    args, settings, env, out = ctx.args, ctx.settings, ctx.env, ctx.out
    steps = selected_steps(args)
    mode = "first-seed" if args.first_seed else "normal"
    runfile = RunFile(folder, env, run_id, not args.apply, args, steps)
    runfile.update("handshake", status="running", started_at=_now_s())
    try:
        handshake, database = handshake_fn(ctx, mode)
    except connect.HandshakeError as exc:
        runfile.update("handshake", status="failed", finished_at=_now_s(), exit_code=exc.exit_code)
        _failed_report(settings, args, run_id, folder, handshake=exc.results, out=out)
        raise
    runfile.update("handshake", status="ok" if args.apply else "planned", finished_at=_now_s(), exit_code=EXIT_OK)
    if args.stop_after == "handshake":
        out.write("stopped after the handshake (--stop-after handshake)\n")
        return EXIT_OK

    flags = apply_flags(args)
    if not args.apply:
        return _dry_run(ctx, run_id, folder, runners, steps, handshake, runfile, flags)
    return _apply(ctx, run_id, folder, runners, steps, handshake, database, runfile, flags)


class _StepRefused(SyncError):
    """A child could not plan (its handshake or a precondition failed); carries the child's exit code. Nothing was written."""

    def __init__(self, step: str, code: int):
        super().__init__(f"step {step} could not plan (exit {code}); nothing was written")
        self.step, self.exit_code = step, code


def _plan_or_fail(ctx: Ctx, plan_run_id: str, report_run_id: str, folder: Path, runners: dict, steps: list, handshake: list,
                  runfile: RunFile, dry: bool) -> Plan:
    """Plan the selected children in dry mode under `plan_run_id`; a failing child ends the command with its exit code after a combined
    failure report (the handshake and precondition results and the sections planned so far) was written into `folder`."""
    plan = plan_steps(ctx, steps, plan_run_id, runners)
    if plan.failed is not None:
        step, code = plan.failed
        for s in CHILD_STEPS:
            if s in steps:
                runfile.update(s, status="failed" if s == step else "pending", finished_at=_now_s() if s == step else None,
                               exit_code=code if s == step else None)
        _failed_report(ctx.settings, ctx.args, report_run_id, folder, handshake=handshake + plan.handshake,
                       preconditions=plan.preconditions, sections=plan.sections, out=ctx.out, dry=dry)
        raise _StepRefused(step, code)
    return plan


def _dry_run(ctx: Ctx, run_id, folder, runners, steps, handshake, runfile: RunFile, flags: list) -> int:
    plan = _plan_or_fail(ctx, run_id, run_id, folder, runners, steps, handshake, runfile, dry=True)
    doc = report.combined_document(ctx.env, run_id, plan.sections, dry=True, handshake=handshake, preconditions=plan.preconditions, flags=flags)
    runfile.doc["plan_hash"] = doc.plan_hash
    for s in steps:
        if s == "verify":
            runfile.update(s, status="skipped_with_reason", reason=NOT_BUILT, exit_code=None)
        elif s != "handshake":
            runfile.update(s, status="planned", exit_code=EXIT_OK)
    _emit(doc, folder, ctx.args, ctx.settings, ctx.out)
    blocking = blocking_guards(plan.sections, ctx.args)
    if blocking:
        ctx.out.write("an apply would refuse (exit 3): " + ", ".join(blocking) + "\n")
    return EXIT_OK


def _apply(ctx: Ctx, run_id, folder, runners, steps, handshake, database, runfile: RunFile, flags: list) -> int:
    args, settings, env, out = ctx.args, ctx.settings, ctx.env, ctx.out
    scratch_id = new_run_id()  # the re-plan runs every child in dry mode: its own folder, which carries the `dry` marker and is removed
    try:
        plan = _plan_or_fail(ctx, scratch_id, run_id, folder, runners, steps, handshake, runfile, dry=False)
    finally:
        shutil.rmtree(run_dir(settings, scratch_id, create=False), ignore_errors=True)
    doc = report.combined_document(env, run_id, plan.sections, dry=False, handshake=handshake, preconditions=plan.preconditions)
    plan_hash = doc.plan_hash
    runfile.doc["plan_hash"] = plan_hash
    refusal = None
    if args.plan_hash is not None and args.plan_hash.lower() != plan_hash:
        refusal = f"the plan changed since the dry run (planned hash {plan_hash}, expected {args.plan_hash.lower()})"
    else:
        blocking = blocking_guards(plan.sections, args)
        if blocking:
            refusal = "guard tripped before any write: " + ", ".join(blocking)
    if refusal:
        _emit(doc, folder, args, settings, out)
        runfile.write()
        raise Refusal(refusal)

    _emit(doc, folder, args, settings, out)  # the plan as read: the combined summary, on disk before the first write
    if not args.yes:
        expected = f"{env} {database}"
        try:
            typed = ctx.ask(f"Type '{expected}' to apply the sync (pull, files, data) to prod: ").strip()
        except EOFError:
            typed = ""
        if typed != expected:
            runfile.write()
            raise Refusal("typed confirmation did not match; nothing was written")
    return _run_steps(ctx, run_id, folder, runners, steps, handshake, plan, runfile, flags)


def _run_steps(ctx: Ctx, run_id, folder, runners, steps, handshake, plan: Plan, runfile: RunFile, flags: list) -> int:
    args, settings, env, out = ctx.args, ctx.settings, ctx.env, ctx.out
    docs: dict = {}
    completed = ["handshake"]
    failure: tuple | None = None
    data_code: int | None = None
    for step in steps:
        if step == "handshake":
            continue
        if failure is not None and not (step == "verify" and data_code == EXIT_VERIFY):
            runfile.update(step, status="not_run")
            continue
        if step == "verify":
            v = verify_step(data_code == EXIT_OK and "data" in docs, data_code)
            runfile.update(step, started_at=_now_s(), finished_at=_now_s(), **v)
            if v["status"] == "failed" and failure is None:
                failure = (step, v["exit_code"])
            elif v["status"] == "ok":
                completed.append(step)
            continue
        runfile.update(step, status="running", started_at=_now_s())
        try:
            result = runners[step](ctx, run_id, True)
        except Exception as exc:  # a bug in a child: the type only, never its message (it could quote a row)
            print(f"sync: step {step} failed unexpectedly ({type(exc).__name__})", file=ctx.err)
            result = StepResult(EXIT_FAILURE, None)
        except BaseException:
            runfile.update(step, status="interrupted", finished_at=_now_s(), exit_code=EXIT_FAILURE)
            raise
        docs[step] = result.doc
        if step == "data":
            data_code = result.code
        runfile.update(step, status="ok" if result.code == EXIT_OK else "failed", finished_at=_now_s(), exit_code=result.code)
        if result.code == EXIT_OK:
            completed.append(step)
        else:
            failure = (step, result.code)

    final = report.combined_document(env, run_id, plan.sections, dry=False, handshake=handshake, preconditions=plan.preconditions,
                                     result=_merge_results(docs) or None)
    _emit(final, folder, args, settings, out)
    runfile.write()
    if failure is None:
        out.write(f"run {run_id} complete: " + ", ".join(completed) + "\n")
        return EXIT_OK
    step, code = failure
    not_run = [s for s in steps if s not in completed and s != step]
    resume = " ".join(["python -m sync.run", "--env", env, *flags, "--apply"])
    out.write(f"run {run_id} stopped at step {step} (exit {code}); completed: {', '.join(completed)}"
              + (f"; not run: {', '.join(not_run)}" if not_run else "") + "\n"
              "every step is resumable: run the same command again (without --plan-hash: the completed steps changed the plan; "
              "a dry run shows the new one):\n  " + resume + "\n")
    return code


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_driver(args)
